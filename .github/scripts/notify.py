#!/usr/bin/env python3
"""Send the Telegram push for the newest published report.

GitHub Actions is the ONLY sender. The routine used to send its own message
and let a workflow guard suppress the duplicate, but that guard read
state/run-log.jsonl at the report commit -- and the routine writes its
run-log line in a *later* commit, so the guard was always reading the
previous run and never fired. Result: two pushes for every edition the
routine notified itself (mornings, Saturdays, Sundays).

Fixed by removing the second sender rather than fixing the guard: the
routine no longer sends at all (shared-rules section 14), so there is
nothing to guard against. Both publish paths converge here:

  direct push to main  -> notify-telegram.yml -> this script
  claude/* PR path     -> build-site.yml      -> this script (after deploy)

Those two triggers are mutually exclusive: a GITHUB_TOKEN merge does not
fire push events.

Reads: site/reports/index.json (newest entry first).
Env:   TG_TOKEN, TG_CHAT.
"""

import html
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://shafferusa.github.io/intelligence-terminal/"
LIMIT = 4096

EDITION = {
    "am": "Morning Brief",
    "pm": "Closing Brief",
    "sat": "Weekly Review",
    "sun": "Week-Ahead Outlook",
    "learn": "Learning Brief",
}


def build_message(entry):
    """Title, summary, up to three headline bullets, then the link."""
    title = html.escape(str(entry.get("title", "New report")))
    summary = html.escape(str(entry.get("summary", "")))

    lines = ["<b>%s</b>" % title]
    if summary:
        lines.append("")
        lines.append(summary)

    headlines = entry.get("headlines") or []
    if isinstance(headlines, list) and headlines:
        lines.append("")
        for h in headlines[:3]:
            lines.append("• " + html.escape(str(h)))

    url = BASE + str(entry.get("path", ""))
    mins = entry.get("reading_minutes")
    tail = '<a href="%s">Open the full report</a>' % html.escape(url, quote=True)
    if isinstance(mins, int) and mins > 0:
        tail += " · %d min read" % mins
    lines.append("")
    lines.append(tail)

    text = "\n".join(lines)
    if len(text) <= LIMIT:
        return text
    # Trim bullets before touching the link; the link is the point of the push.
    keep = text[: LIMIT - len(tail) - 2].rsplit("\n", 1)[0]
    return keep + "\n\n" + tail


def send(token, chat, text):
    body = json.dumps(
        {
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main():
    token = os.environ.get("TG_TOKEN")
    chat = os.environ.get("TG_CHAT")
    if not token or not chat:
        print("Telegram credentials not configured -- nothing sent.")
        return 0

    try:
        with open("site/reports/index.json", encoding="utf-8") as fh:
            index = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - never fail the deploy over this
        print("could not read index.json:", exc)
        return 0

    if not index:
        print("index.json empty -- nothing to announce.")
        return 0

    text = build_message(index[0])

    try:
        resp = send(token, chat, text)
    except urllib.error.HTTPError as exc:
        print("telegram HTTP error:", exc.code, exc.read()[:400])
        return 1
    except Exception as exc:  # noqa: BLE001
        print("telegram send failed:", exc)
        return 1

    print("telegram ok:", resp.get("ok"))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
