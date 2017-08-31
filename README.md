# Ebook: Essays on Reducing Suffering, by Brian Tomasik

This simple Python script generates the source files to create an ebook containing all of Brian Tomasik's essays (found at reducing-suffering.org)

## Prerequisites

* Python 2
* [Calibre](https://calibre-ebook.com)

## Running the script

The first time you run this script, simply call

```
python run.py
```

This creates a folder `html-raw` with all raw html files and another folder `html-clean` with the html files after processing. It also creates the file `toc.html` with the table of contents. You can then set the flag `download` to zero to avoid downloading the source files in future runs:

```
python run.py --download=0
```

## Creating the ebook with Calibre

I'm not an expert using Calibre, so the following steps are just me having no idea what I'm doing (as indicated by the question marks below). Any improvements and suggestions are welcome. I'm using Calibre 3.7.0.

- Click on `Add books`and import the file `toc.html`.
- Rename the file in Calibre to whatever you want the ebook to be called. Add Brian Tomasik as author.
- Select the file and click on `Convert books`. 
  - I chose `EPUB` as output format (see upper right corner).
  - In `Look & feel -> Layout` check `Remove spacing between paragraphs`
  - In `Heuristic processing` check `Enable heuristic processing` (?)
  - In `Structure detection` leave field `Insert page breaks before (XPath expression)` blank (?)
  - Click `OK`
- Once the conversion is finished, select the ebook and click on the menu button `Edit book`
  - Go to `File -> Import files into book`
  - Select all html files in the folder `html-clean`
  - Click on `Tools -> Fix HTML - all files`
  - (Also try `File -> Save copy` and save a separate copy somewhere)
  - Save the changes and close the window
- Select the ebook and click on `Convert books` again. Select the same options as previously (?). You can also add a cover for the ebook. 
- Click `OK`. It'll take a few minutes for the ebook to be created. (In my case it takes about 15 minutes.)
- If the resulting ebook consists of only the TOC, import the additional copy created in one of the steps above and convert that copy. No idea why this works.
