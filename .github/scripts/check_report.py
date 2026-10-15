#!/usr/bin/env python3
"""Check a published report page before it is committed.

Usage:  python3 .github/scripts/check_report.py site/reports/YYYY/MM/YYYY-MM-DD-slot.html [...]
        python3 .github/scripts/check_report.py --all     # archive audit
Prints "OK <file>" or the problems found; exits 1 if any page fails.

A named file gets every check (rendering + current-template structure): that is
what a routine runs on the page it just wrote. --all runs only the rendering
checks over every dated page, because pages from before the v2 template
(July-mid August) and the Learning Brief use other layouts on purpose.

Why this exists (2026-10-15): eight reports went out as plain white, unstyled
pages. Each run had set the page title with a find-and-replace that matched the
word "<title>" inside the template's instruction comment instead of the real
title element. That deleted the comment's closing "-->" together with <html>,
<head>, the charset and the viewport meta, so the browser read everything up to
the next "-->" -- including the stylesheet link -- as one long comment. Nothing
failed loudly; the page simply had no styling and rendered at desktop width on a
phone. This checks the page the way a browser sees it: comments stripped.
"""

import glob
import json
import os
import re
import sys

SLOTS = ("am", "pm", "sat", "sun", "learn", "sie")
NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(%s)\.html$" % "|".join(SLOTS))
DAY = r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
MON = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
D = r"[1-9]\d?"
# The fixed edition title per newspaper slot (shared-rules section 11).
EDITION_TITLE = {
    "am": re.compile(r"^Morning Brief — %s, %s %s, \d{4}$" % (DAY, MON, D)),
    "pm": re.compile(r"^Closing Brief — %s, %s %s, \d{4}$" % (DAY, MON, D)),
    "sat": re.compile(r"^Weekly Review — %s %s – %s %s, \d{4}$" % (MON, D, MON, D)),
    "sun": re.compile(r"^Week Ahead — Week of %s %s, \d{4}$" % (MON, D)),
}
BARE = {"morning brief", "closing brief", "weekly review", "week ahead", "week-ahead outlook"}
META_KEYS = {"date", "slot", "title", "path", "summary", "headlines",
             "reading_minutes", "generated_at", "timezone"}


def problems(path, strict=True):
    raw = open(path, encoding="utf-8").read()
    out = []

    m = NAME.search(os.path.basename(path))
    if not m:
        return ["filename is not YYYY-MM-DD-<slot>.html with a known slot"]
    date, slot = "-".join(m.group(1, 2, 3)), m.group(4)

    # --- rendering: a failure here means the reader sees a broken page ---

    # A comment left open swallows the page. Every "<!--" needs its "-->".
    if raw.count("<!--") != raw.count("-->"):
        out.append("unbalanced HTML comment (%d opened, %d closed) -- part of the page is "
                   "commented out; usually a find-and-replace damaged the template comment"
                   % (raw.count("<!--"), raw.count("-->")))

    seen = re.sub(r"<!--.*?-->", "", raw, flags=re.S)   # what the browser renders

    need = [
        (r"<html\s+lang=", "<html lang> element missing"),
        (r"<meta\s+charset=", "charset meta missing"),
        (r'<meta\s+name="viewport"', "viewport meta missing (page renders at desktop width on a phone)"),
        (r'<link\s+rel="stylesheet"\s+href="\.\./\.\./\.\./assets/style\.css"',
         "stylesheet link ../../../assets/style.css missing or commented out (page renders unstyled)"),
        (r'<script\s+src="\.\./\.\./\.\./assets/report\.js"', "report.js script tag missing"),
    ]
    for pattern, msg in need:
        if not re.search(pattern, seen):
            out.append(msg)

    titles = re.findall(r"<title>(.*?)</title>", seen, flags=re.S)
    if len(titles) != 1:
        out.append("expected exactly one <title> element, found %d" % len(titles))
    elif not titles[0].strip() or titles[0].lstrip().startswith("Sample"):
        out.append("title is empty or still the template's sample title")

    if not strict:
        return out

    # --- structure: the current report template, checked on new pages ---
    mains = re.findall(r'<main\b[^>]*class="[^"]*\bpaper\b[^"]*"[^>]*>', seen)
    if len(mains) != 1:
        out.append("expected exactly one main.paper element, found %d" % len(mains))
    else:
        ds = re.search(r'data-slot="([^"]*)"', mains[0])
        if not ds or ds.group(1) != slot:
            out.append('main.paper data-slot is %r, filename says "%s"' % (ds and ds.group(1), slot))

    if "<!--REPORT:START-->" not in raw or "<!--REPORT:END-->" not in raw:
        out.append("REPORT:START / REPORT:END marker missing")

    mm = re.search(r'<script type="application/json" id="report-meta">(.*?)</script>', seen, flags=re.S)
    if not mm:
        out.append("report-meta JSON block missing")
    else:
        try:
            meta = json.loads(mm.group(1))
        except ValueError as exc:
            out.append("report-meta JSON does not parse: %s" % exc)
        else:
            missing = META_KEYS - set(meta)
            if missing:
                out.append("report-meta missing keys: %s" % ", ".join(sorted(missing)))
            if meta.get("slot") != slot or meta.get("date") != date:
                out.append("report-meta date/slot (%s %s) do not match the filename (%s %s)"
                           % (meta.get("date"), meta.get("slot"), date, slot))
            out += title_problems(slot, meta.get("title", ""), titles, seen)
    return out


def title_problems(slot, meta_title, titles, seen):
    """Newspaper editions: fixed edition title everywhere, a real headline in the h1."""
    if slot not in EDITION_TITLE:
        return []                                   # Learning Brief / SIE: own procedures
    out = []
    if not EDITION_TITLE[slot].match(meta_title):
        out.append("report-meta title %r is not the fixed edition title for %s "
                   "(shared-rules section 11, e.g. %r)" % (meta_title, slot, {
                       "am": "Morning Brief — Wed, Oct 14, 2026",
                       "pm": "Closing Brief — Wed, Oct 14, 2026",
                       "sat": "Weekly Review — Oct 5 – Oct 9, 2026",
                       "sun": "Week Ahead — Week of Oct 12, 2026"}[slot]))
    elif len(titles) == 1 and titles[0].strip().replace("'", "’") != meta_title + " · Logan’s Daily Newspaper":
        out.append("title element should be %r" % (meta_title + " · Logan’s Daily Newspaper"))
    h1 = re.findall(r"<h1\b[^>]*>(.*?)</h1>", seen, flags=re.S)
    text = re.sub(r"<[^>]+>", "", h1[0]).strip() if len(h1) == 1 else ""
    if len(h1) != 1:
        out.append("expected exactly one h1 (the lead headline), found %d" % len(h1))
    elif not text or text.lower() in BARE or text.startswith("Sample") or EDITION_TITLE[slot].match(text):
        out.append("h1 must be the lead news headline, not %r (shared-rules section 11)" % text)
    return out


def main(argv):
    audit = argv == ["--all"]
    files = ([f for f in sorted(glob.glob("site/reports/20*/*/*.html"))
              if NAME.search(os.path.basename(f))] if audit else argv)
    if not files:
        print(__doc__.strip().splitlines()[2])
        return 2
    failed = 0
    for f in files:
        p = problems(f, strict=not audit)
        if p:
            failed += 1
            print("FAIL %s" % f)
            for line in p:
                print("  - " + line)
        else:
            print("OK   %s" % f)
    if len(files) > 1:
        print("%d checked, %d failed" % (len(files), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
