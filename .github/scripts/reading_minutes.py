#!/usr/bin/env python3
"""Print a lesson page's reading time and word count.

Usage:  python3 .github/scripts/reading_minutes.py PAGE.html

Counts the words inside <article class="lesson-body"> (tags stripped; the
masthead, track head and colophon are not counted; sub- and superscripts join
their word), skipping spoken-formula paragraphs, and prints "<minutes> <words>":
minutes = words / 220, rounded up
(shared-rules section 12.7). A script file, so routines can run it as one plain
command under the shell rule instead of an inline `python3 -c`.
"""

import html
import math
import re
import sys


def count(path):
    page = open(path, encoding="utf-8").read()
    m = re.search(r'<article class="lesson-body[^"]*"[^>]*>(.*?)</article>', page, re.S)
    if not m:
        raise SystemExit("no <article class=\"lesson-body\"> in %s" % path)
    body = re.sub(r'<p class="expr-spoken[^"]*">.*?</p>', "", m.group(1), flags=re.S)
    # Sub- and superscripts belong to the word they mark: q<sub>0</sub> is one word, not two.
    body = re.sub(r"</?su[bp]\b[^>]*>", "", body)
    words = len(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())
    return math.ceil(words / 220), words


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[2].strip())
    minutes, words = count(sys.argv[1])
    print(minutes, words)
