#!/usr/bin/env python3
"""Build an EPUB of Brian Tomasik's "Essays on Reducing Suffering".

The script
  1. reads the table of contents on https://reducing-suffering.org/ (plus the
     "Additional Essays" page),
  2. downloads every essay (caching everything under .cache/ so that re-runs
     are fast and polite to the server),
  3. extracts the article text, makes all ids unique across the book, turns
     links between essays into links inside the book and bundles the images,
  4. writes one big HTML file and hands it to pandoc, which produces the EPUB.

Usage:
    python3 build.py                 # build dist/essays-on-reducing-suffering.epub
    python3 build.py --refresh       # re-download pages instead of using the cache
    python3 build.py --limit 10      # only the first 10 essays (for testing)
    python3 build.py --no-images     # leave images out (smaller file)
"""

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, quote

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
ASSETS = ROOT / "assets"

HOME = "https://reducing-suffering.org/"
ADDITIONAL = "https://reducing-suffering.org/additional-essays/"
CLR_INDEX = "https://longtermrisk.org/research/"

# The site's firewall rejects the default python-requests user agent.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120 Safari/537.36 (ebook builder)")
REQUEST_DELAY = 0.5  # seconds between network requests

RS_HOSTS = {"reducing-suffering.org", "www.reducing-suffering.org"}
FRI_HOSTS = {"foundational-research.org", "www.foundational-research.org"}
CLR_HOSTS = {"longtermrisk.org", "www.longtermrisk.org"}
# Old domains whose links redirect to reducing-suffering.org.
REDIRECT_HOSTS = {"utilitarian-essays.com", "www.utilitarian-essays.com",
                  "utilitarian-essays.org", "www.utilitarian-essays.org"}

# Homepage entries that are not essays (translations, the homepage itself, ...).
SKIP_PATHS = {"/", "/wiekszosc-cierpienia-doswiadczanego-przez-zwierzeta-ma-miejsce-w-naturze/"}

# Foundational Research Institute articles that were later renamed.
FRI_ALIASES = {
    "robots-ai-intelligence-explosion": "artificial-intelligence-and-its-implications-for-future-suffering",
    "thoughts-on-robots-ai-and-intelligence-explosion":
        "artificial-intelligence-and-its-implications-for-future-suffering",
}

MAX_IMAGE_WIDTH = 800

# Elements whose ids pandoc keeps. Ids on anything else are moved onto an empty <span>.
ID_KEEPERS = {"h1", "h2", "h3", "h4", "h5", "h6", "div", "span", "a", "section", "img",
              "table", "figure", "code", "pre"}
INLINE_HOSTS = {"p", "li", "td", "th", "dt", "dd", "em", "strong", "b", "i", "u", "s", "sup",
                "sub", "small", "cite", "q", "caption", "figcaption", "label", "abbr", "mark"}

new_tag = BeautifulSoup("", "lxml").new_tag


# --------------------------------------------------------------------------
# Downloading with a cache
# --------------------------------------------------------------------------

class Fetcher:
    """Downloads URLs, remembering the final URL after redirects."""

    def __init__(self, refresh=False):
        self.refresh = refresh
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        (CACHE / "files").mkdir(parents=True, exist_ok=True)
        self.index_path = CACHE / "index.json"
        self.index = json.loads(self.index_path.read_text()) if self.index_path.exists() else {}
        self.refreshed = set()
        self.last_request = 0.0

    def save(self):
        self.index_path.write_text(json.dumps(self.index, indent=1, sort_keys=True))

    def _wait(self):
        delay = REQUEST_DELAY - (time.time() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.time()

    def get(self, url):
        """Return (final_url, status, content_bytes or None)."""
        entry = self.index.get(url)
        use_cache = entry and (not self.refresh or url in self.refreshed)
        if use_cache and (entry["status"] != 200 or (CACHE / "files" / entry["file"]).exists()):
            content = None
            if entry["status"] == 200:
                content = (CACHE / "files" / entry["file"]).read_bytes()
            return entry["final"], entry["status"], content

        for attempt in range(3):
            self._wait()
            try:
                r = self.session.get(url, timeout=60)
                break
            except requests.RequestException as e:
                if attempt == 2:
                    print(f"    ! failed to download {url}: {e}")
                    return url, 0, None
                time.sleep(2 * (attempt + 1))
        fname = hashlib.sha1(url.encode()).hexdigest()
        if r.status_code == 200:
            (CACHE / "files" / fname).write_bytes(r.content)
        self.index[url] = {"final": r.url, "status": r.status_code, "file": fname,
                           "type": r.headers.get("content-type", "")}
        self.refreshed.add(url)
        if len(self.index) % 20 == 0:
            self.save()
        return r.url, r.status_code, r.content if r.status_code == 200 else None

    def resolve(self, url):
        """Return the URL that `url` redirects to (or None if it's dead)."""
        final, status = self.resolve_any(url)
        return final if status == 200 else None

    def resolve_any(self, url):
        """Return (final URL after redirects, HTTP status)."""
        entry = self.index.get("HEAD " + url)
        if entry is None:
            self._wait()
            try:
                r = self.session.head(url, allow_redirects=True, timeout=30)
                entry = {"final": r.url, "status": r.status_code}
            except requests.RequestException:
                entry = {"final": url, "status": 0}
            self.index["HEAD " + url] = entry
        return entry["final"], entry["status"]

    def soup(self, url):
        final, status, content = self.get(url)
        if status != 200:
            return final, None
        return final, BeautifulSoup(content.decode("utf-8", errors="replace"), "lxml")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Entry:
    title: str
    url: str
    kind: str = ""            # "rs", "clr" or "external"
    source_url: str = ""      # where the text was actually downloaded from
    eid: str = ""             # id of the essay inside the book
    html: str = ""
    duplicate_of: "Entry" = None


@dataclass
class Topic:
    title: str
    entries: list = field(default_factory=list)


@dataclass
class Part:
    title: str
    topics: list = field(default_factory=list)


def clean_text(s):
    return re.sub(r"\s+", " ", s).strip()


def norm_title(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("’", "'")).strip()


def link_key(url):
    """A canonical key identifying which page a URL points to (ignores fragment, scheme, www)."""
    p = urlparse(url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path or "/"
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return host + path.lower()


# --------------------------------------------------------------------------
# Table of contents
# --------------------------------------------------------------------------

def parse_toc(soup, base_url, top_tag="h2", sub_tag="h3"):
    """Turn a page made of headings and bulleted lists of links into Parts/Topics/Entries."""
    container = soup.find("article") or soup.body
    parts = []
    for el in container.find_all([top_tag, sub_tag, "li"]):
        if el.name == top_tag:
            title = clean_text(el.get_text())
            if title.lower() == "contents":
                continue
            parts.append(Part(title))
        elif el.name == sub_tag and parts:
            parts[-1].topics.append(Topic(clean_text(el.get_text())))
        elif el.name == "li" and parts:
            # Skip the page's own table of contents and nested lists.
            if el.find_parent(id="table_of_contents") or el.find_parent("li"):
                continue
            a = el.find("a", href=True)
            if a is None or not clean_text(a.get_text()):
                continue
            if not parts[-1].topics:
                parts[-1].topics.append(Topic(""))
            parts[-1].topics[-1].entries.append(
                Entry(clean_text(a.get_text()), urljoin(base_url, a["href"])))
    return parts


def classify(entry):
    p = urlparse(entry.url)
    host = p.netloc.lower()
    if host in RS_HOSTS and not p.path.startswith("/wp-content/") and p.path not in SKIP_PATHS:
        entry.kind = "rs"
    elif host in FRI_HOSTS | CLR_HOSTS and p.path.strip("/"):
        entry.kind = "clr"
    else:
        entry.kind = "external"


class ClrResolver:
    """Finds the current location of old foundational-research.org articles on longtermrisk.org."""

    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.by_title = None

    def _load_index(self):
        _, soup = self.fetcher.soup(CLR_INDEX)
        self.by_title = {}
        if soup is None:
            return
        for a in soup.find_all("a", href=True):
            href = urljoin(CLR_INDEX, a["href"])
            if urlparse(href).path.startswith("/research/") and "?" not in href and "#" not in href:
                self.by_title.setdefault(norm_title(a.get_text()), href)

    def resolve(self, url, title=None):
        slug = [s for s in urlparse(url).path.split("/") if s]
        if not slug:
            return None
        name = FRI_ALIASES.get(slug[-1], slug[-1])
        candidate = f"https://longtermrisk.org/research/{name}/"
        if self.fetcher.resolve(candidate):
            return candidate
        if self.by_title is None:
            self._load_index()
        if title:
            t = norm_title(title)
            for k, href in self.by_title.items():
                if k == t or k.startswith(t) or t.startswith(k):
                    return href
        # Slugs were sometimes shortened (e.g. "eliminativist-approach-consciousness" for
        # "the-eliminativist-approach-to-consciousness"): match on shared words.
        words = set(slug[-1].split("-"))
        best, best_score = None, 0.0
        for href in set(self.by_title.values()):
            other = set([s for s in urlparse(href).path.split("/") if s][-1].split("-"))
            score = len(words & other) / len(words | other)
            if score > best_score:
                best, best_score = href, score
        return best if best_score >= 0.6 else None


# --------------------------------------------------------------------------
# Article extraction
# --------------------------------------------------------------------------

def extract_rs(soup):
    """Return (title, byline_html, body_tag) for a reducing-suffering.org page."""
    header = soup.find("header")
    h1 = (header.find("h1") if header else None) or soup.find("h1")
    title = clean_text(h1.get_text()) if h1 else clean_text(soup.title.get_text())
    body = soup.find("article")
    byline = []
    bd = body.find(class_="byline_and_dates") if body else None
    if bd:
        for p in bd.find_all("p"):
            byline.append(p.decode_contents().strip())
        bd.decompose()
    elif body:
        # Older pages put "By X<br>First written: ..." in the first paragraph.
        first = body.find(True, recursive=False)
        if first is not None and first.name == "p":
            for c in first.find_all(string=lambda x: isinstance(x, Comment)):
                c.extract()
            text = clean_text(first.get_text())
            if len(text) < 300 and re.search(
                    r"(?i)^(by |an interview|.{0,80}interview(ed)? by)|first (written|published)", text):
                parts = re.split(r"<br\s*/?>", first.decode_contents())
                byline = [b.strip() for b in parts if b.strip()]
                first.decompose()
    return title, byline, body


def extract_clr(soup):
    """Return (title, byline_html, body_tag) for a longtermrisk.org page."""
    header = soup.select_one("header.doc-header")
    title = clean_text(header.select_one(".doc-title").get_text())
    byline = []
    authors = header.select_one(".doc-facts-authors")
    if authors:
        byline.append("By " + html.escape(clean_text(authors.get_text())))
    dates = [clean_text(x.get_text()) for x in header.select(".doc-facts-line, .doc-revision")]
    dates = [d for d in dates if d]
    if dates:
        byline.append(html.escape(". ".join(dates)) + ".")
    body = soup.select_one(".doc-body")
    # Screen-reader-only duplicate heading inside the footnotes section.
    for h in body.select(".sr-only"):
        h.decompose()
    return title, byline, body


# --------------------------------------------------------------------------
# Cleaning and link rewriting
# --------------------------------------------------------------------------

REMOVE_TAGS = ["script", "style", "noscript", "form", "input", "button", "nav", "link", "meta"]
UNWRAP_TAGS = ["font", "center", "o:p", "big"]
KEEP_ATTRS = {"href", "src", "alt", "title", "id", "colspan", "rowspan", "start", "type",
              "datetime", "class", "lang"}


def encode_image(img):
    """Shrink and re-encode an image; returns (bytes, extension), whichever format is smaller."""
    from PIL import Image
    fmt = img.format
    img.load()
    if img.width > MAX_IMAGE_WIDTH:
        img = img.resize((MAX_IMAGE_WIDTH, round(img.height * MAX_IMAGE_WIDTH / img.width)),
                         Image.LANCZOS)
    if img.mode in ("P", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        if img.getextrema()[3][0] == 255:  # fully opaque
            img = img.convert("RGB")
        else:  # flatten onto white; e-reader pages are (almost always) white
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    jpeg = BytesIO()
    img.save(jpeg, "JPEG", quality=75, optimize=True, progressive=True)
    if fmt == "JPEG":
        return jpeg.getvalue(), "jpg"
    png = BytesIO()
    if img.mode == "RGB" and img.getcolors(256) is None:
        # Charts and screenshots look the same with a 256-colour palette at a fraction of the size.
        img.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
            png, "PNG", optimize=True)
    else:
        img.save(png, "PNG", optimize=True)
    # Charts and diagrams compress well as PNG and look bad as JPEG; photos the opposite.
    if len(jpeg.getvalue()) < 0.6 * len(png.getvalue()):
        return jpeg.getvalue(), "jpg"
    return png.getvalue(), "png"


class Book:
    def __init__(self, fetcher, with_images=True):
        self.fetcher = fetcher
        self.with_images = with_images
        self.entries_by_key = {}   # link_key -> Entry
        self.ids_by_eid = {}       # eid -> set of original ids within that essay
        self.used_eids = set()
        self.image_dir = BUILD / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.images = {}           # url -> local path (or None if failed)
        self.stats = {"internal_links": 0, "images": 0, "images_failed": 0}

    def make_eid(self, url):
        slug = [s for s in urlparse(url).path.split("/") if s]
        base = re.sub(r"[^a-z0-9-]+", "-", (slug[-1] if slug else "essay").lower()).strip("-")
        eid, n = "e-" + base, 2
        while eid in self.used_eids:
            eid, n = f"e-{base}-{n}", n + 1
        self.used_eids.add(eid)
        return eid

    # -- links ------------------------------------------------------------

    def canonical_target(self, url):
        """Map a link to an in-book essay key when possible; otherwise return a fixed-up URL."""
        p = urlparse(url)
        host = p.netloc.lower()
        if host in REDIRECT_HOSTS:
            plain = urlunparse(p._replace(fragment=""))
            final, status = self.fetcher.resolve_any(plain)
            if status == 200 or urlparse(final).netloc.lower() in FRI_HOSTS | CLR_HOSTS:
                url = urlunparse(urlparse(final)._replace(fragment=p.fragment))
                p = urlparse(url)
                host = p.netloc.lower()
        if host in FRI_HOSTS | CLR_HOSTS:
            plain = urlunparse(p._replace(fragment=""))
            if link_key(plain) in self.entries_by_key:
                return url
            if host in CLR_HOSTS and self.fetcher.resolve(plain):
                return url
            if not p.path.strip("/"):
                return "https://longtermrisk.org/"
            new = self.clr.resolve(plain)
            if new:
                return new + ("#" + p.fragment if p.fragment else "")
            # Dead link: point to the Internet Archive's copy instead.
            original = url.replace("longtermrisk.org", "foundational-research.org")
            return "https://web.archive.org/web/2020/" + original
        if host in RS_HOSTS and p.scheme == "http":
            return urlunparse(p._replace(scheme="https"))
        return url

    def rewrite_href(self, href, page_url, eid):
        href = href.strip()
        if href.startswith(("mailto:", "javascript:", "tel:", "data:")):
            return href
        if href.startswith("#"):
            return self.local_anchor(eid, href[1:])
        absolute = urljoin(page_url, href)
        if not absolute.startswith("http"):
            return absolute
        target = self.canonical_target(absolute)
        key = link_key(target)
        entry = self.entries_by_key.get(key)
        if entry is None and key.startswith("reducing-suffering.org/"):
            # Old WordPress permalinks sometimes redirect to the new slug.
            final = self.fetcher.resolve(urlunparse(urlparse(target)._replace(fragment="")))
            if final:
                entry = self.entries_by_key.get(link_key(final))
        if entry is not None:
            while entry.duplicate_of is not None:
                entry = entry.duplicate_of
            if entry.eid == eid and not urlparse(target).fragment:
                return "#" + eid
            self.stats["internal_links"] += 1
            return self.local_anchor(entry.eid, urlparse(target).fragment)
        return target

    def local_anchor(self, eid, fragment):
        fragment = fragment.strip()
        if fragment and fragment in self.ids_by_eid.get(eid, set()):
            return f"#{eid}--{fragment}"
        return f"#{eid}"

    # -- images -----------------------------------------------------------

    def local_image(self, url):
        if url in self.images:
            return self.images[url]
        from PIL import Image
        path = None
        final, status, content = self.fetcher.get(url)
        if status == 200 and content:
            name = hashlib.sha1(url.encode()).hexdigest()[:16]
            try:
                if url.lower().endswith(".svg") or content.lstrip()[:5] in (b"<?xml", b"<svg "):
                    path = self.image_dir / f"{name}.svg"
                    path.write_bytes(content)
                else:
                    img = Image.open(BytesIO(content))
                    fmt = img.format
                    if getattr(img, "is_animated", False) and fmt == "GIF":
                        path = self.image_dir / f"{name}.gif"
                        path.write_bytes(content)
                    else:
                        data, ext = encode_image(img)
                        path = self.image_dir / f"{name}.{ext}"
                        path.write_bytes(data)
            except Exception as e:  # unreadable image
                print(f"    ! could not process image {url}: {e}")
                path = None
        if path is None:
            self.stats["images_failed"] += 1
        else:
            self.stats["images"] += 1
        self.images[url] = path
        return path

    # -- cleaning ---------------------------------------------------------

    def clean(self, body, page_url, eid, essay_level):
        for c in body.find_all(string=lambda s: isinstance(s, Comment)):
            c.extract()
        for t in body.find_all(REMOVE_TAGS):
            t.decompose()
        # The site's per-article contents box: e-readers have their own navigation,
        # and we generate a fresh one below.
        for t in body.select("#table_of_contents, #toc_container, .doc-toc"):
            t.decompose()

        # Embedded videos become plain links.
        for f in body.find_all(["iframe", "embed", "object", "video", "audio"]):
            src = f.get("src") or f.get("data") or ""
            if src:
                src = urljoin(page_url, src)
                m = re.search(r"youtube(?:-nocookie)?\.com/embed/([\w-]+)", src)
                if m:
                    src = f"https://www.youtube.com/watch?v={m.group(1)}"
                p = new_tag("p")
                a = new_tag("a", href=src)
                a.string = "Video: " + src if "youtube" in src else src
                p.append(a)
                f.replace_with(p)
            else:
                f.decompose()

        # MathML -> TeX that pandoc understands (it turns it back into proper MathML).
        for m in body.find_all("math"):
            ann = m.find("annotation")
            tex = ann.get_text().strip() if ann else clean_text(m.get_text())
            display = m.get("display") == "block"
            span = new_tag("span")
            span["class"] = "math display" if display else "math inline"
            span.string = (r"\[" + tex + r"\]") if display else (r"\(" + tex + r"\)")
            m.replace_with(span)

        for name in UNWRAP_TAGS:
            for t in body.find_all(name):
                t.unwrap()

        # <a name="x"> anchors -> ids; heading <span id="x"> -> heading id.
        for a in body.find_all("a", attrs={"name": True}):
            if not a.get("id"):
                a["id"] = a["name"]
            del a["name"]
        for h in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            spans = [s for s in h.find_all(["span", "a"]) if s.get("id") and not s.get("href")]
            if spans and not h.get("id"):
                h["id"] = spans[0]["id"]
                del spans[0]["id"]

        # Make ids unique across the whole book.
        original_ids = self.ids_by_eid.setdefault(eid, set())
        seen = set()
        for t in body.find_all(attrs={"id": True}):
            old = t["id"].strip()
            if not old or old in seen:
                del t["id"]
                continue
            seen.add(old)
            original_ids.add(old)
            t["id"] = f"{eid}--{old}"

        # Headings: shift so that the essay's own sections sit below the essay title.
        # Find the shallowest heading level used in the body.
        heads = body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        if heads:
            top = min(int(h.name[1]) for h in heads)
            for h in heads:
                # Never above h4: levels 1-3 are reserved for parts, topics and essays
                # (pandoc splits the book into files at those levels).
                level = int(h.name[1]) - top + max(essay_level, 3) + 1
                if level > 6:
                    h.name = "p"
                    strong = new_tag("strong")
                    for child in list(h.children):
                        strong.append(child.extract())
                    h.append(strong)
                else:
                    h.name = f"h{level}"
                    # Section headings are reached via the essay's own contents list.
                    h["class"] = "unnumbered unlisted"

        # pandoc drops ids on paragraphs, list items, etc.: carry them on an empty span instead.
        for t in body.find_all(attrs={"id": True}):
            if t.name in ID_KEEPERS:
                continue
            anchor = new_tag("span", id=t["id"])
            del t["id"]
            if t.name in INLINE_HOSTS:
                t.insert(0, anchor)
            else:
                wrapper = new_tag("div", id=anchor["id"])
                t.wrap(wrapper)

        # Drop attributes pandoc/EPUB don't need.
        for t in body.find_all(True):
            for attr in list(t.attrs):
                if attr not in KEEP_ATTRS:
                    del t[attr]
            if "class" in t.attrs and t.name not in ("h3", "h4", "h5", "h6", "p", "div", "span"):
                del t["class"]
        return body

    def finish_links_and_images(self, body, page_url, eid):
        for a in body.find_all("a", href=True):
            a["href"] = self.rewrite_href(a["href"], page_url, eid)
            if a.get("title") and clean_text(a["title"]) == clean_text(a.get_text()):
                del a["title"]
        # Anchors without href are just id carriers; turn them into spans.
        for a in body.find_all("a"):
            if not a.get("href"):
                a.name = "span"
        for img in body.find_all("img"):
            src = img.get("src")
            local = None
            if self.with_images and src and not src.startswith("data:"):
                local = self.local_image(urljoin(page_url, src))
            if local is None:
                alt = img.get("alt")
                if alt:
                    img.replace_with(NavigableString(f"[Image: {alt}]"))
                else:
                    img.decompose()
                continue
            img["src"] = str(local.relative_to(BUILD))
            if not img.get("alt"):
                img["alt"] = ""
            # Image credits on the site live in the title attribute.
            if img.get("title"):
                credit = img["title"]
                del img["title"]
                wrapper = img.find_parent("p") or img
                note = new_tag("div")
                note["class"] = "image-credit"
                inner = new_tag("p")
                inner.string = credit
                note.append(inner)
                wrapper.insert_after(note)

    def essay_contents(self, body, eid):
        """A small contents list for long essays (pandoc's own TOC stops at the essay level)."""
        heads = [h for h in body.find_all(re.compile(r"^h[1-6]$")) if h.get("id")]
        if len(heads) < 4:
            return ""
        top = min(int(h.name[1]) for h in heads)
        items = "".join(
            f'<li><a href="#{h["id"]}">{html.escape(clean_text(h.get_text()))}</a></li>'
            for h in heads if int(h.name[1]) == top)
        return (f'<div class="essay-contents"><div class="essay-contents-title"><p>Contents</p></div>'
                f"<ul>{items}</ul></div>")


# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------

def gather_toc(fetcher):
    _, home = fetcher.soup(HOME)
    if home is None:
        sys.exit("Could not download the homepage.")
    parts = parse_toc(home, HOME)

    # Expand the "Additional Essays" index page into its own part.
    _, extra = fetcher.soup(ADDITIONAL)
    for part in parts:
        for topic in part.topics:
            topic.entries = [e for e in topic.entries if link_key(e.url) != link_key(ADDITIONAL)]
    if extra is not None:
        extra_part = Part("Additional Essays")
        for p in parse_toc(extra, ADDITIONAL, top_tag="h2", sub_tag="h3"):
            extra_part.topics.append(Topic(p.title, [e for t in p.topics for e in t.entries]))
        parts.append(extra_part)
    return parts


def build(args):
    fetcher = Fetcher(refresh=args.refresh)
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    book = Book(fetcher, with_images=not args.no_images)
    book.clr = ClrResolver(fetcher)

    print("Reading the table of contents...")
    parts = gather_toc(fetcher)
    entries = [e for p in parts for t in p.topics for e in t.entries]
    for e in entries:
        classify(e)
    if args.limit:
        keep = set(id(e) for e in [e for e in entries if e.kind != "external"][:args.limit])
        for p in parts:
            for t in p.topics:
                t.entries = [e for e in t.entries if id(e) in keep or e.kind == "external"]
        entries = [e for p in parts for t in p.topics for e in t.entries]

    # Pass 1: download and extract every essay.
    essays = [e for e in entries if e.kind != "external"]
    extracted = {}
    print(f"Downloading {len(essays)} essays...")
    for i, e in enumerate(essays, 1):
        url = e.url
        if e.kind == "clr":
            url = book.clr.resolve(e.url, e.title)
            if url is None:
                print(f"  [{i}] ! could not find {e.title} ({e.url}); listing it as a link")
                e.kind = "external"
                continue
        final, soup = fetcher.soup(url)
        if soup is None:
            print(f"  [{i}] ! could not download {e.title} ({url}); listing it as a link")
            e.kind = "external"
            continue
        if urlparse(final).netloc.lower() not in RS_HOSTS | CLR_HOSTS:
            e.kind = "external"
            continue
        e.source_url = final
        try:
            if urlparse(final).netloc.lower() in CLR_HOSTS:
                e.kind = "clr"
                title, byline, body = extract_clr(soup)
            else:
                title, byline, body = extract_rs(soup)
        except Exception as ex:
            print(f"  [{i}] ! could not parse {e.title} ({final}): {ex}")
            e.kind = "external"
            continue
        if body is None:
            print(f"  [{i}] ! no article body in {final}")
            e.kind = "external"
            continue

        keys = {link_key(e.url), link_key(final), link_key(url)}
        dup = next((book.entries_by_key[k] for k in keys if k in book.entries_by_key), None)
        if dup is not None:
            e.duplicate_of = dup
            e.title = dup.title
            continue
        e.eid = book.make_eid(final)
        for k in keys:
            book.entries_by_key[k] = e
        e.title = title or e.title
        extracted[e.eid] = (byline, body, final)
        print(f"  [{i}/{len(essays)}] {e.title}")
    fetcher.save()

    # Pass 2: clean, now that we know every essay's id (needed for cross-links).
    print("Cleaning essays and resolving links...")
    for p in parts:
        has_topics = any(t.title for t in p.topics)
        for t in p.topics:
            for e in t.entries:
                if not e.eid or e.eid not in extracted:
                    continue
                byline, body, final = extracted[e.eid]
                level = 3 if has_topics else 2
                book.clean(body, final, e.eid, essay_level=level)
    for p in parts:
        for t in p.topics:
            for e in t.entries:
                if not e.eid or e.eid not in extracted:
                    continue
                byline, body, final = extracted[e.eid]
                book.finish_links_and_images(body, final, e.eid)
                bl = '<div class="byline">' + "".join(f"<p>{b}</p>" for b in byline) + "</div>"
                source = (f'<div class="source"><p>Source: <a href="{html.escape(final)}">'
                          f"{html.escape(final)}</a></p></div>")
                e.html = bl + source + book.essay_contents(body, e.eid) + body.decode_contents()
    fetcher.save()

    write_book(parts, book, args)


def write_book(parts, book, args):
    today = date.today()
    out = []
    out.append('<h1 id="about-this-edition" class="unnumbered">About this edition</h1>')
    out.append(
        "<p>This ebook collects the essays from Brian Tomasik's website "
        f'<a href="{HOME}">Essays on Reducing Suffering</a>, in the order of the site\'s '
        "table of contents, together with the articles hosted by the Center on Long-Term Risk "
        "(formerly the Foundational Research Institute) that the site links to. A few pieces "
        "are by other authors, as noted under their titles.</p>"
        f"<p>The text was downloaded on {today:%B} {today.day}, {today.year}. Many essays are "
        "updated regularly, so the website may have newer versions. Each essay begins with the "
        "link it was taken from.</p>"
        "<p>Links between essays point to the essay inside this book; all other links go to "
        "the web. Items on the website's table of contents that are not essays (videos, PDFs, "
        "podcasts and articles on other sites) are listed at the end of the book under "
        '<a href="#further-resources">Further resources</a>.</p>')

    external = []
    seen_external = set()
    n_essays = 0
    for p in parts:
        has_topics = any(t.title for t in p.topics)
        part_essays = [e for t in p.topics for e in t.entries if e.html or e.duplicate_of]
        if not part_essays:
            continue
        out.append(f'<h1 class="unnumbered part">{html.escape(p.title)}</h1>')
        for t in p.topics:
            topic_entries = [e for e in t.entries if e.html or e.duplicate_of]
            if t.title and topic_entries:
                out.append(f'<h2 class="unnumbered topic">{html.escape(t.title)}</h2>')
            level = 3 if has_topics else 2
            for e in t.entries:
                if e.kind == "external" or not (e.html or e.duplicate_of):
                    if e.kind == "external" and e.url not in seen_external:
                        seen_external.add(e.url)
                        external.append((p.title, t.title, e))
                    continue
                if e.duplicate_of is not None:
                    # Listed twice on the website: keep one copy, point to it from here.
                    target = e.duplicate_of
                    out.append(f'<h{level} class="unnumbered">{html.escape(target.title)}</h{level}>'
                               f'<div class="see-also"><p>This essay appears in another section: '
                               f'<a href="#{target.eid}">{html.escape(target.title)}</a>.</p></div>')
                    continue
                n_essays += 1
                out.append(f'<h{level} id="{e.eid}" class="unnumbered">{html.escape(e.title)}</h{level}>')
                out.append(e.html)

    if external:
        out.append('<h1 id="further-resources" class="unnumbered part">Further resources</h1>')
        out.append("<p>These items from the website's table of contents are not essays on "
                   "reducing-suffering.org (videos, PDFs, podcasts, or pieces hosted elsewhere), "
                   "so they are not included in the book. They are available online:</p>")
        current = None
        for part_title, topic_title, e in external:
            heading = part_title + (" — " + topic_title if topic_title else "")
            if heading != current:
                if current is not None:
                    out.append("</ul>")
                out.append(f'<div class="resource-group"><p>{html.escape(heading)}</p></div><ul>')
                current = heading
            out.append(f'<li><a href="{html.escape(e.url)}">{html.escape(e.title)}</a></li>')
        out.append("</ul>")

    doc = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
           "<title>Essays on Reducing Suffering</title></head><body>"
           + "\n".join(out) + "</body></html>")
    (BUILD / "book.html").write_text(doc, encoding="utf-8")
    print(f"{n_essays} essays, {book.stats['internal_links']} links between essays, "
          f"{book.stats['images']} images ({book.stats['images_failed']} failed), "
          f"{len(external)} external resources.")

    meta = {
        "title": "Essays on Reducing Suffering",
        "creator": [{"role": "author", "text": "Brian Tomasik"}],
        "lang": "en-US",
        "date": today.isoformat(),
        "description": "Brian Tomasik's essays from reducing-suffering.org, collected as an ebook.",
        "subject": ["Ethics", "Effective altruism", "Animal welfare", "Wild-animal suffering"],
        "identifier": [{"scheme": "URI", "text": HOME}],
    }
    (BUILD / "metadata.json").write_text(json.dumps(meta, indent=1))
    make_cover(BUILD / "cover.png", today)

    DIST.mkdir(exist_ok=True)
    output = DIST / ("essays-on-reducing-suffering.epub" if not args.limit
                     else "essays-on-reducing-suffering-sample.epub")
    cmd = ["pandoc", "book.html", "--from", "html", "--to", "epub3",
           "--output", str(output),
           "--metadata-file", "metadata.json",
           "--css", str(ASSETS / "epub.css"),
           "--epub-cover-image", "cover.png",
           "--split-level", "3", "--toc-depth", "3",
           "--resource-path", str(BUILD)]
    print("Running pandoc...")
    subprocess.run(cmd, check=True, cwd=BUILD)
    print(f"Wrote {output.relative_to(ROOT)} ({output.stat().st_size / 1e6:.1f} MB)")


def make_cover(path, today):
    """Draw a simple typographic cover so the book looks decent in library views."""
    from PIL import Image, ImageDraw, ImageFont

    def font(names, size):
        dirs = [Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"),
                Path("/Library/Fonts"), Path.home() / "Library/Fonts",
                Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/TTF")]
        for n in names:
            for d in dirs:
                if (d / n).exists():
                    return ImageFont.truetype(str(d / n), size)
        return ImageFont.load_default(size)

    W, H = 1600, 2400
    img = Image.new("RGB", (W, H), "#1f2a33")
    d = ImageDraw.Draw(img)
    serif = ["Georgia.ttf", "Times New Roman.ttf", "DejaVuSerif.ttf"]
    serif_bold = ["Georgia Bold.ttf", "Times New Roman Bold.ttf", "DejaVuSerif-Bold.ttf"]
    title_font = font(serif_bold, 150)
    author_font = font(serif, 90)
    small_font = font(serif, 50)
    d.rectangle([110, 110, W - 110, H - 110], outline="#c9a96e", width=6)
    y = 700
    for line in ["Essays on", "Reducing", "Suffering"]:
        w = d.textlength(line, font=title_font)
        d.text(((W - w) / 2, y), line, font=title_font, fill="#f4efe6")
        y += 190
    d.line([(W / 2 - 160, y + 60), (W / 2 + 160, y + 60)], fill="#c9a96e", width=5)
    w = d.textlength("Brian Tomasik", font=author_font)
    d.text(((W - w) / 2, y + 140), "Brian Tomasik", font=author_font, fill="#f4efe6")
    line = f"reducing-suffering.org · {today.year}"
    w = d.textlength(line, font=small_font)
    d.text(((W - w) / 2, H - 330), line, font=small_font, fill="#c9a96e")
    img.save(path, "PNG", optimize=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-download pages instead of using the cache")
    ap.add_argument("--limit", type=int, default=0, help="only include the first N essays (for testing)")
    ap.add_argument("--no-images", action="store_true", help="leave images out of the book")
    args = ap.parse_args()
    if shutil.which("pandoc") is None:
        sys.exit("pandoc is required: https://pandoc.org/installing.html")
    build(args)


if __name__ == "__main__":
    main()
