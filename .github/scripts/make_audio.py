#!/usr/bin/env python3
"""Generate the spoken version of the newest report.

Web Speech (site/assets/report.js) reads a report aloud in the browser, but
iOS stops it the moment the screen locks -- useless on a commute, which is
exactly when a 27-minute lesson wants listening to. This produces a real MP3
so it plays on the lock screen, in the background and over CarPlay.

Hosting: GitHub Releases, NOT the repo. Three reports a day at ~5 MB each is
~5 GB a year, which has no business in git history. Release assets are free,
outside history, and the URL is fully predictable:

  https://github.com/<repo>/releases/download/audio-<date>-<slot>/<date>-<slot>.mp3

Predictable matters -- it means the report page can reference its own audio
with no commit-back step and no race against the page build. The player just
tries the URL and falls back to Web Speech if it 404s.

edge-tts is Microsoft's read-aloud service. Free, no key, genuinely good
neural voices. It is an unofficial client, so it can break -- hence the
fallback, and hence this job never failing the build.
"""

import asyncio
import html.parser
import json
import os
import re
import sys

VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewMultilingualNeural")
RATE = os.environ.get("TTS_RATE", "+8%")

# Mirrors the skip rules in site/assets/report.js: a spoken read-out of a
# 25-row market table is noise, and nobody wants the colophon read to them.
SKIP_CLASSES = {
    "board", "colophon", "report-nav", "top-bar", "audio-bar", "data-table",
    "health-footer", "meta-grid", "story-tags", "story-sourceline",
    "sources-list", "paper-dateline", "table-wrap", "range-cell",
}
SKIP_TAGS = {"script", "style", "table", "nav", "figcaption"}
READ_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "blockquote"}


class Extractor(html.parser.HTMLParser):
    """Pull the readable text out of a report page, in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth_skip = 0
        self.stack = []
        self.capture = 0
        self.buf = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        skip = tag in SKIP_TAGS or bool(classes & SKIP_CLASSES)
        # A collapsed <details> is content the reader chose not to open.
        if tag == "details" and "open" not in attrs:
            skip = True
        self.stack.append(skip)
        if skip:
            self.depth_skip += 1
        if tag in READ_TAGS and not self.depth_skip:
            self.capture += 1
            self.buf = []

    def handle_endtag(self, tag):
        if tag in READ_TAGS and self.capture:
            text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if len(text) > 1:
                self.blocks.append((tag, text))
            self.capture = 0
            self.buf = []
        if self.stack:
            skip = self.stack.pop()
            if skip:
                self.depth_skip -= 1

    def handle_data(self, data):
        if self.capture and not self.depth_skip:
            self.buf.append(data)


def build_script(html_text, title):
    parser = Extractor()
    parser.feed(html_text)

    lines = []
    seen_h1 = False
    for tag, text in parser.blocks:
        if tag == "h1":
            if seen_h1:
                continue
            seen_h1 = True
        # A pause between sections reads far better than a wall of speech.
        if tag in ("h2", "h3"):
            lines.append("")
        lines.append(text)

    body = "\n".join(lines).strip()
    if not body:
        return None

    intro = "%s. From Logan's Daily Newspaper." % title
    return intro + "\n\n" + body


async def synth(text, path):
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE, rate=RATE)
    await comm.save(path)


def main():
    try:
        with open("site/reports/index.json", encoding="utf-8") as fh:
            index = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print("cannot read index.json:", exc)
        return 0
    if not index:
        print("index empty")
        return 0

    entry = index[0]
    date, slot = entry.get("date"), entry.get("slot")
    path = entry.get("path")
    if not (date and slot and path):
        print("newest entry is missing date/slot/path")
        return 0

    page = os.path.join("site", path)
    if not os.path.exists(page):
        print("report page not found:", page)
        return 0

    with open(page, encoding="utf-8") as fh:
        html_text = fh.read()

    script = build_script(html_text, entry.get("title", "Report"))
    if not script:
        print("no readable text extracted")
        return 0

    words = len(script.split())
    print("extracted %d words (~%d min spoken)" % (words, round(words / 165)))

    out = "%s-%s.mp3" % (date, slot)
    asyncio.run(synth(script, out))

    size = os.path.getsize(out)
    print("wrote %s (%.1f MB)" % (out, size / 1_048_576))
    if size < 20_000:
        print("suspiciously small -- treating as a failure")
        os.remove(out)
        return 1

    # Hand the tag and filename to the workflow.
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write("mp3=%s\n" % out)
            fh.write("tag=audio-%s-%s\n" % (date, slot))
            fh.write("title=%s\n" % entry.get("title", "Report").replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
