import os
import re
import sys
import time
import jinja2
import shutil
import pickle
import hashlib
import logging
import requests
import tempfile
import itertools
import subprocess

from xdg.BaseDirectory import save_cache_path


re_widont_html = re.compile(
    r"([^<>\s])\s+([^<>\s]+\s*)(</?(?:address|blockquote|br|dd|div|dt|fieldset|form|h[1-6]|li|noscript|p|td|th)[^>]*>|$)",
    re.IGNORECASE,
)


def widont(txt):
    def cb_widont(m):
        return "{}&nbsp;{}{}".format(*m.groups())

    return re_widont_html.sub(cb_widont, txt)


class BaseToMobi:
    def __init__(self):
        self.epoch = time.time() - (60 * 60)
        self.session = requests.Session()

        self.context = {"articles": [], "title": self.TITLE, "images": []}

    def main(self):
        self.setup_logging()

        self.handle_base()

        for idx, x in enumerate(self.context["articles"]):
            x["idx"] = idx

            x["body"] = self.handle_body(x["body"])

            def image_cb(m):
                url = self.handle_image(m.group(1))
                return f'<img src="{url}" width="50%">'

            x["body"] = re.sub(r"<iframe[^>]*>", "", x["body"])
            x["body"] = re.sub(r'<img [^>]*src="([^"]+)"([^>]+)>', image_cb, x["body"])
            x["body"] = widont(x["body"])

        t = tempfile.mkdtemp(prefix=f"{self.PREFIX}-")

        try:
            self.generate_mobi(t)
        finally:
            if self.keep_html:
                self.log.info("Keeping HTML in %s/index.html", t)
                self.log.debug("Opening %s/index.html with xdg-open", t)
                try:
                    subprocess.call(("xdg-open", f"{t}/index.html"))
                except FileNotFoundError:
                    pass
            else:
                shutil.rmtree(t, ignore_errors=True)

        return 0

    def generate_mobi(self, tempdir):
        self.log.info("Generating magazine in %s", tempdir)

        assert self.context[
            "articles"
        ], f"No articles downloaded; please check {self.BASE_URL}"

        template_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates"
        )
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))

        self.context["tempdir"] = tempdir
        self.context["grouped"] = [
            (x, list(y))
            for x, y in itertools.groupby(
                self.context["articles"], lambda x: x["subsection"]
            )
        ]

        self.save_image_to(self.context["cover"], os.path.join(tempdir, "cover.jpg"))

        # Download images
        for idx, x in enumerate(self.context["images"]):
            self.save_image_to(x, os.path.join(tempdir, f"{idx}.jpg"))

        for x in ("index.html", "toc.html", "style.css", "toc.ncx", "book.opf"):
            val = env.get_template(x).render(**self.context)
            with open(os.path.join(tempdir, x), "w") as f:
                f.write(val)

        # Hide kindlegen output by default
        stdout, stderr = subprocess.PIPE, subprocess.PIPE
        if self.verbosity >= 2:
            stdout, stderr = None, None

        subprocess.call(
            ("kindlegen/kindlegen", "-verbose", os.path.join(tempdir, "book.opf"),),
            stdout=stdout,
            stderr=stderr,
        )

        if not self.filename:
            self.filename = "{}_{}.mobi".format(
                self.PREFIX, self.context["date"].replace(" ", "_")
            )

        self.log.info("Saving output to %s", self.filename)

        shutil.move(os.path.join(tempdir, "book.mobi"), self.filename)

    def handle_image(self, url):
        self.context["images"].append(url)

        return "{}.jpg".format(len(self.context["images"]) - 1)

    def save_image_to(self, url, target):
        self.log.debug("Downloading %s to %s", url, target)

        with open(target, "wb") as f:
            for x in self.get(url).iter_content(chunk_size=128):
                f.write(x)

        self.log.debug("Resizing and resampling %s", target)
        subprocess.check_call(
            (
                "convert",
                target,
                "-resize",
                "800x",
                "-set",
                "colorspace",
                "Gray",
                "-separate",
                "-average",
                "-quality",
                "60%",
                target,
            )
        )

    def setup_logging(self):
        self.log = logging.getLogger()
        self.log.setLevel(
            {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}[self.verbosity]
        )

        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime).19s %(levelname).1s %(message)s")
        )
        self.log.addHandler(handler)

    def get(self, url, **kwargs):
        self.log.info("Downloading %s", url)

        h = hashlib.sha1()
        h.update(url.encode("utf-8"))
        for k, v in kwargs.items():
            h.update(k.encode("utf-8"))
            h.update(v.encode("utf-8"))

        filename = os.path.join(save_cache_path(self.NAME), h.hexdigest())

        if os.path.exists(filename) and os.path.getmtime(filename) > self.epoch:
            with open(filename, "rb") as f:
                return pickle.load(f)

        response = self.session.get(
            url, headers={"User-agent": "Mozilla/5.0"}, params=kwargs
        )
        response.raise_for_status()

        with open(filename, "wb") as f:
            pickle.dump(response, f)

        return response

    def handle_body(self, body):
        return body
