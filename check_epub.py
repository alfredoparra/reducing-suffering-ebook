#!/usr/bin/env python3
"""Sanity checks for the generated EPUB (a lightweight complement to epubcheck).

    python3 check_epub.py dist/essays-on-reducing-suffering.epub

Checks that every XHTML file is well-formed XML, that every internal link points
to an existing file and id, that every image exists, that ids are unique per
file, and that every file in the package is listed in the manifest.
"""

import posixpath
import sys
import zipfile
from collections import Counter
from urllib.parse import unquote, urlparse

from lxml import etree

XHTML = "{http://www.w3.org/1999/xhtml}"
OPF = "{http://www.idpf.org/2007/opf}"


def main(path):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    problems = []

    if z.namelist()[0] != "mimetype" or z.read("mimetype") != b"application/epub+zip":
        problems.append("mimetype must be the first file and contain application/epub+zip")

    container = etree.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//{*}rootfile").get("full-path")
    opf_dir = posixpath.dirname(opf_path)
    opf = etree.fromstring(z.read(opf_path))
    manifest = {posixpath.normpath(posixpath.join(opf_dir, unquote(i.get("href"))))
                for i in opf.iter(OPF + "item")}
    for n in names:
        if n not in manifest and n not in ("mimetype", opf_path) and not n.startswith("META-INF/"):
            problems.append(f"{n} is not in the manifest")
    for m in manifest:
        if m not in names:
            problems.append(f"manifest lists missing file {m}")

    docs, ids = {}, {}
    for n in sorted(names):
        if n.endswith((".xhtml", ".html")):
            try:
                docs[n] = etree.fromstring(z.read(n))
            except etree.XMLSyntaxError as e:
                problems.append(f"{n}: not well-formed: {e}")
                continue
            file_ids = [el.get("id") for el in docs[n].iter() if el.get("id")]
            for i, c in Counter(file_ids).items():
                if c > 1:
                    problems.append(f"{n}: duplicate id {i}")
            ids[n] = set(file_ids)

    stats = Counter()
    for n, doc in docs.items():
        base = posixpath.dirname(n)
        for el in doc.iter():
            tag = el.tag if isinstance(el.tag, str) else ""
            if tag in (XHTML + "img",) or tag.endswith("}image"):
                src = el.get("src") or el.get("{http://www.w3.org/1999/xlink}href")
                if src and posixpath.normpath(posixpath.join(base, unquote(src))) not in names:
                    problems.append(f"{n}: missing image {src}")
                stats["images"] += 1
            href = el.get("href") if tag == XHTML + "a" else None
            if not href:
                continue
            p = urlparse(href)
            if p.scheme:
                stats["external links"] += 1
                continue
            target = posixpath.normpath(posixpath.join(base, unquote(p.path))) if p.path else n
            if target not in names:
                problems.append(f"{n}: link to missing file {href}")
            elif p.fragment and p.fragment not in ids.get(target, set()):
                problems.append(f"{n}: link to missing anchor {href}")
            else:
                stats["internal links"] += 1

    for k, v in sorted(stats.items()):
        print(f"{k}: {v}")
    print(f"documents: {len(docs)}")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems[:200]:
            print("  " + p)
        return 1
    print("No problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/essays-on-reducing-suffering.epub"))
