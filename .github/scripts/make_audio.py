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
RATE = os.environ.get("TTS_RATE", "+0%")

# Mirrors the skip rules in site/assets/report.js: a spoken read-out of a
# 25-row market table is noise, and nobody wants the colophon read to them.
SKIP_CLASSES = {
    "board", "colophon", "report-nav", "top-bar", "audio-bar", "data-table",
    "health-footer", "meta-grid", "story-tags", "story-sourceline",
    "sources-list", "paper-dateline", "table-wrap", "range-cell",
}
SKIP_TAGS = {"script", "style", "table", "nav", "figcaption"}
READ_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "blockquote", "dt", "dd"}

# Notation read literally is noise, or worse: "(1 + y)<sup>−n</sup>" flattens
# to "(1 + y)−n" and the voice says "minus n" where the lesson means "to the
# power of minus n". So a raw .expr line is translated symbol by symbol, and
# a lesson that supplies its own spoken form (<p class="expr-spoken">, which
# prompts/learning.md asks for) wins over the translation.
SPOKEN = [
    ("≈", " approximately equals "), ("≠", " is not equal to "),
    ("≤", " is less than or equal to "), ("≥", " is greater than or equal to "),
    ("→", " goes to "), ("∞", " infinity "), ("√", " the square root of "),
    ("∂", " partial "), ("Δ", " delta "), ("Σ", " the sum of "), ("∫", " the integral of "),
    ("π", " pi "), ("·", " times "), ("×", " times "), ("−", " minus "),
    ("²", " squared "), ("³", " cubed "), ("′", " prime "),
    ("^", " to the power of "), ("=", " equals "), ("+", " plus "), ("/", " over "),
]


def speak_math(text):
    for sym, words in SPOKEN:
        text = text.replace(sym, words)
    return re.sub(r"\s+", " ", text).strip()


class Extractor(html.parser.HTMLParser):
    """Pull the readable text out of a report page, in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth_skip = 0
        self.stack = []
        self.capture = 0
        self.buf = []
        self.blocks = []       # (tag, text, kind, plate)
        self.kind = None       # "expr" / "spoken" / None for the block being captured
        self.plate = 0         # formula-plate counter, so .expr and .expr-spoken pair up

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
        if "formula" in classes:
            self.plate += 1
        if tag in READ_TAGS and not self.depth_skip:
            self.capture += 1
            self.buf = []
            self.kind = "spoken" if "expr-spoken" in classes else ("expr" if "expr" in classes else None)
        elif self.capture and not self.depth_skip:
            # Exponents and subscripts inside a captured block.
            if tag == "sup":
                self.buf.append(" to the power of ")
            elif tag == "sub":
                self.buf.append(" sub ")

    def handle_endtag(self, tag):
        if tag in READ_TAGS and self.capture:
            text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if len(text) > 1:
                self.blocks.append((tag, text, self.kind, self.plate))
            self.capture = 0
            self.buf = []
            self.kind = None
        elif tag in ("sup", "sub") and self.capture:
            self.buf.append(" ")
        if self.stack:
            skip = self.stack.pop()
            if skip:
                self.depth_skip -= 1

    def handle_data(self, data):
        if self.capture and not self.depth_skip:
            self.buf.append(data)


def polish(text):
    """Prepare one block for speech.

    Most of a report is headings and list items -- "The Brief", "Markets.
    Equity futures are little changed" -- and they carry no terminal
    punctuation. Without a full stop the synthesiser runs one block straight
    into the next or clips its tail, which is heard as stop-start choppiness.
    It sounds like the voice is rushing; it is actually missing the cue to
    finish a sentence. Rate changes do not fix it, and Logan picked this
    alongside the slower rate after hearing both.

    "&" is spelled out for the same reason: read literally it is a stumble in
    the middle of things like S&P.
    """
    text = text.replace("&", " and ")
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[-1] not in ".!?:;,":
        text += "."
    return text


def build_script(html_text, title):
    parser = Extractor()
    parser.feed(html_text)

    # Plates that carry their own spoken form: drop the raw notation there.
    spoken_plates = {plate for (_t, _x, kind, plate) in parser.blocks if kind == "spoken"}

    lines = []
    seen_h1 = False
    pending_dt = None
    for tag, text, kind, plate in parser.blocks:
        if tag == "h1":
            if seen_h1:
                continue
            seen_h1 = True
        if kind == "expr":
            if plate in spoken_plates:
                continue
            text = speak_math(text)
        # Symbol lists: "P, the price of the bond" rather than two clipped blocks.
        if tag == "dt":
            pending_dt = text
            continue
        if tag == "dd":
            text = (pending_dt + ", " + text) if pending_dt else text
            pending_dt = None
        # A pause between sections reads far better than a wall of speech.
        if tag in ("h2", "h3"):
            lines.append("")
        lines.append(polish(text))

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

    # Hand the tag and filename to the workflow. The title is interpolated into
    # a shell command by the workflow, and report titles legitimately contain
    # apostrophes, quotes and dashes -- strip anything that could break out.
    title = entry.get("title", "Report")
    title = re.sub(r'[\r\n"`$\\]', " ", title)
    title = re.sub(r"\s+", " ", title).strip()[:90] or "Report"

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write("mp3=%s\n" % out)
            fh.write("tag=audio-%s-%s\n" % (date, slot))
            fh.write("title=%s\n" % title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
