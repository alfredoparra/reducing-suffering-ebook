# Ebook: Essays on Reducing Suffering, by Brian Tomasik

A script that turns all of Brian Tomasik's essays on
[reducing-suffering.org](https://reducing-suffering.org/) into a single EPUB.

The book follows the site's table of contents (Introduction, Altruism, Animals,
Consciousness, Ethics, Science, The future, Other) and adds:

- the essays from the site's [Additional Essays](https://reducing-suffering.org/additional-essays/) page;
- Brian's articles hosted by the Center on Long-Term Risk (formerly the
  Foundational Research Institute) that the table of contents links to.

Links between essays become links inside the book. Footnotes, tables and images
are kept, each essay starts with its byline, dates and source URL, and longer
essays get a small contents list. Items on the table of contents that aren't
essays (videos, PDFs, podcasts, pages on other sites) are listed at the end
under "Further resources".

## Requirements

- Python 3.9+ with `requests`, `beautifulsoup4`, `lxml` and `Pillow`
  (`pip install -r requirements.txt`)
- [pandoc](https://pandoc.org/installing.html) 3.x (`brew install pandoc` on macOS)

## Building

```
python3 build.py
```

This writes `dist/essays-on-reducing-suffering.epub`. The first run downloads
all ~250 pages and their images (about 5–10 minutes; it waits half a second
between requests to be gentle on the server). Everything is cached in `.cache/`,
so later runs take under a minute. Options:

```
python3 build.py --refresh     # re-download pages (to pick up updated essays)
python3 build.py --limit 10    # quick test build with only 10 essays
python3 build.py --no-images   # leave out images
```

To check the result:

```
python3 check_epub.py dist/essays-on-reducing-suffering.epub
```

This checks that the XHTML is well formed and that every internal link, footnote
and image resolves. For a full standards check, also run
[epubcheck](https://www.w3.org/publishing/epubcheck/) (`brew install epubcheck`).

## Reading on a Kindle

Kindles read EPUB files sent through [Send to Kindle](https://www.amazon.com/sendtokindle).
To get a `.mobi`/`.azw3` file instead, convert the EPUB with Calibre's `ebook-convert`.

## How it works

`build.py` reads the headings and link lists on the homepage, downloads every
linked essay, and keeps only the article (title, byline and body). It then:

1. prefixes every `id` with the essay's id, so anchors are unique across the book;
2. rewrites links (including old `utilitarian-essays.com` and
   `foundational-research.org` URLs) that point to essays in the book so they
   become in-book links; dead `foundational-research.org` links go to the
   Internet Archive;
3. downloads images, shrinking large ones;
4. writes `build/book.html` and runs pandoc to produce the EPUB, with one file
   per essay and a three-level table of contents (part, topic, essay).

All essays are © Brian Tomasik. This project only repackages them for offline reading.
