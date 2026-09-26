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

Those two stay exclusive per edition: a GITHUB_TOKEN merge fires no push
event, and build-site.yml only notifies when the merged PR changed
site/reports/index.json (see the guard there).

No audio wait any more: report audio was retired 2026-09-25 (Logan: "no audio
is needed"), so the push goes out as soon as the page is published -- which
means as soon as Pages is SERVING it, not the moment the report commit lands.
notify-telegram.yml starts ~5 s after the push while the Pages deploy takes
~1.5 min (2026-09-25 learn: push 10:18:45, live 10:20:13), so a push sent at
once links to a 404. wait_for_page() holds it until the page answers 200, up
to PAGE_WAIT_SECONDS, then sends regardless: late, never lost. On the PR path
(build-site.yml) the deploy is already done and the first check passes.

Reads: site/reports/index.json (newest entry first).
Env:   TG_TOKEN, TG_CHAT, PAGE_WAIT_SECONDS (default 420 -- the notify jobs time out at 10 min; 0 disables).
"""

import html
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = "shafferusa/intelligence-terminal"
BASE = "https://shafferusa.github.io/intelligence-terminal/"
LIMIT = 4096  # Telegram's message length cap
PAGE_WAIT = int(os.environ.get("PAGE_WAIT_SECONDS", "420"))  # under the 10-min job timeout
PAGE_POLL = 15

EDITION = {
    "am": "Morning Brief",
    "pm": "Closing Brief",
    "sat": "Weekly Review",
    "sun": "Week-Ahead Outlook",
    "learn": "Learning Brief",
    "sie": "SIE Program",
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


def wait_for_page(entry):
    """Hold the push until Pages serves this edition's page. True if it did."""
    path = entry.get("path")
    if not path or PAGE_WAIT <= 0:
        return False
    url = BASE + str(path)
    deadline = time.monotonic() + PAGE_WAIT
    attempt = 0
    while True:
        attempt += 1
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    print("page live after %d check(s): %s" % (attempt, url))
                    return True
        except Exception:  # noqa: BLE001 - 404 until the deploy lands
            pass
        if time.monotonic() >= deadline:
            print("page still not live after %ds -- sending anyway" % PAGE_WAIT)
            return False
        time.sleep(PAGE_POLL)


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

    entry = index[0]
    text = build_message(entry)

    # Waiting is an optimisation; sending is the job. Nothing in the wait may
    # turn "the push is a minute late" into "there is no push".
    try:
        wait_for_page(entry)
    except Exception as exc:  # noqa: BLE001
        print("page wait failed (%s) -- sending now" % exc)

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
