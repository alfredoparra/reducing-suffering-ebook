# Ebook: Essays on Reducing Suffering, by Brian Tomasik

This simple Python script generates the source files to create an ebook containing all of Brian Tomasik's essays (found at reducing-suffering.org)

## Prerequisites

Python 2
[Calibre](calibre-ebook.com)

## Running the script

The first time you run this script, simply call

```
python run.py
```

This creates a folder _html-raw_ with all raw html files and another folder _html-clean_ with the html files after processing. You can then set the flag _download_ to zero to avoid downloading the source files again:

```
python run.py --download=0
```
