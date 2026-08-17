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

THE PUSH WAITS FOR THE AUDIO. Synthesis takes seven to nine minutes and runs
in a separate workflow, so a push sent the moment the report commits arrives
while the MP3 does not yet exist -- and tapping it lands on the Web Speech
fallback, which is a flat iOS system voice, not the chosen edge-tts one. In
practice that is what every push sounded like. So this script polls the
release asset first and only sends once the MP3 answers. If it never shows
up the push still goes out, late but never lost: a missing MP3 must delay
the edition, never cancel it.

This wait lives here, not in the workflows, because both publish paths call
this one script -- one place to be correct instead of two to keep in sync.

Reads: site/reports/index.json (newest entry first).
Env:   TG_TOKEN, TG_CHAT, AUDIO_WAIT_SECONDS (default 900, 0 disables).
"""

import calendar
import html
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "shafferusa/intelligence-terminal"
BASE = "https://shafferusa.github.io/intelligence-terminal/"
RELEASES = "https://github.com/%s/releases/download/" % REPO
API = "https://api.github.com"
LIMIT = 4096

# make_audio.py deletes anything under 20 KB as a failed synthesis; an asset
# below that floor is not audio worth waiting to link to.
MIN_MP3 = 20_000

# Synthesis alone runs 3-7 min (measured 2026-08-16), but audio.yml serialises
# on one concurrency group: when the Learning Brief and Morning Brief publish
# minutes apart -- which they do by design -- the second edition queues behind
# the first and its MP3 can land ~14 min after its commit. Twenty minutes
# clears that worst case with room, and still gives up long before a broken
# edge-tts could sit on an edition indefinitely.
AUDIO_WAIT = int(os.environ.get("AUDIO_WAIT_SECONDS", "1200"))
AUDIO_POLL = 30

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


def audio_names(entry):
    """(release tag, asset filename) for an index entry, or (None, None).

    Must stay in step with make_audio.py and report.js -- all three build this
    same pair independently, which is what lets the report page reference its
    own audio with no commit-back step.
    """
    date, slot = entry.get("date"), entry.get("slot")
    if not (date and slot):
        return None, None
    name = "%s-%s" % (date, slot)
    return "audio-%s" % name, "%s.mp3" % name


def report_commit_epoch():
    """Unix time of the commit being announced, as a staleness floor.

    Existence alone is not proof of freshness. A release tag is per date+slot,
    so a re-published edition -- a retried run, a manual audio dispatch --
    finds LAST time's MP3 already sitting at the exact URL this one will
    write. Without a floor the wait returns instantly and the push links to
    audio of a report that no longer exists. Any MP3 for this edition must
    have been written after the commit that published it.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return int(out.stdout.strip())
    except Exception as exc:  # noqa: BLE001 - no git, shallow checkout, anything
        print("cannot read commit time (%s) -- freshness check skipped" % exc)
        return None


def asset_via_api(tag, filename):
    """Release asset metadata, or None if the API cannot answer.

    Authenticated with GITHUB_TOKEN when present: Actions runners share public
    IPs, and the unauthenticated 60/hour limit is shared across every runner on
    that IP. Returning None means 'ask the CDN instead', never 'not ready'.
    """
    req = urllib.request.Request(
        "%s/repos/%s/releases/tags/%s" % (API, REPO, tag),
        headers={
            "User-Agent": "LoganTerminal/1.0 (loganshaffer87@gmail.com)",
            "Accept": "application/vnd.github+json",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}  # release not cut yet -- a real answer, not a failure
        return None
    except Exception:  # noqa: BLE001
        return None
    for asset in data.get("assets") or []:
        if asset.get("name") == filename:
            return asset
    return {}


def parse_iso(stamp):
    try:
        return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001
        return None


def pages_audio_ready(filename):
    """True once Pages is serving the MP3 the phone will actually play.

    The release asset existing is not the finish line. Releases serve
    application/octet-stream with a Content-Disposition of attachment, which
    iOS Safari refuses to play, so the build stages a copy into the site and
    the page prefers that. Waiting on the release alone would go back to
    announcing an edition the phone still cannot listen to -- only now for a
    subtler reason than before.
    """
    url = BASE + "audio/" + filename
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status not in (200, 206):
                return False
            ctype = (resp.headers.get("Content-Type") or "").lower()
            # Pages must be calling it audio. If it ever serves octet-stream
            # the iPhone is back to browser speech and the wait is pointless.
            return "audio" in ctype or "mpeg" in ctype
    except Exception:  # noqa: BLE001 - not deployed yet is the expected case
        return False


def audio_ready(tag, filename, floor):
    """True once THIS edition's MP3 is uploaded and complete.

    Prefers the API, which is the only source that can answer 'is it finished
    and is it new'. A half-uploaded asset reports state 'starting', and an
    asset left over from a previous run of the same slot reports an old
    updated_at -- neither is something to link a push to.
    """
    asset = asset_via_api(tag, filename)

    if asset is not None:
        if not asset:
            return False
        if asset.get("state") != "uploaded":
            return False                      # upload still in flight
        if (asset.get("size") or 0) < MIN_MP3:
            return False                      # matches make_audio.py's own floor
        if floor:
            written = parse_iso(asset.get("updated_at") or "")
            # 60s of slack: the runner's clock and GitHub's need not agree.
            if written and written < floor - 60:
                print("found a stale MP3 for %s -- waiting for this run's" % tag)
                return False
        return True

    # API unreachable. Fall back to asking the CDN directly: one byte, and the
    # Content-Range header carries the full size so a truncated file is still
    # caught. Freshness cannot be checked this way -- accept rather than block,
    # because a late push beats no push.
    req = urllib.request.Request(
        RELEASES + "%s/%s" % (tag, filename), headers={"Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status not in (200, 206):
                return False
            crange = resp.headers.get("Content-Range") or ""
            total = crange.rsplit("/", 1)[-1]
            return (not total.isdigit()) or int(total) >= MIN_MP3
    except Exception:  # noqa: BLE001 - 404 before upload is the expected case
        return False


def wait_for_audio(entry):
    """Hold the push until this edition's MP3 is up. True if it turned up."""
    if AUDIO_WAIT <= 0:
        print("audio wait disabled -- sending immediately")
        return False

    tag, filename = audio_names(entry)
    if not tag:
        print("entry has no date/slot -- cannot wait for audio")
        return False

    floor = report_commit_epoch()
    deadline = time.monotonic() + AUDIO_WAIT
    attempt = 0
    while True:
        attempt += 1
        # Pages first: that is the copy the phone plays. The release is only
        # accepted as a floor -- an archive page older than the staging window
        # still links it, and a late Pages deploy should not hold the push
        # past the deadline on its own.
        if pages_audio_ready(filename):
            print("audio ready on Pages after %d check(s): %s" % (attempt, filename))
            return True
        if audio_ready(tag, filename, floor) and time.monotonic() >= deadline - 120:
            print("release asset up but Pages not serving it yet -- sending")
            return True
        if time.monotonic() >= deadline:
            print(
                "audio still missing after %ds -- sending anyway; the page "
                "falls back to browser speech" % AUDIO_WAIT
            )
            return False
        time.sleep(AUDIO_POLL)


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

    # Deliberately before the send, not after: the whole point is that the
    # notification is the thing that arrives late, so that what it links to
    # is complete when it does.
    #
    # Belt and braces around the whole wait. Every probe is already guarded
    # individually, but this call sits ahead of the send -- so anything that
    # escaped would turn "the push is late" into "there is no push", which is
    # far worse than the problem the wait exists to solve. Waiting is an
    # optimisation; sending is the job.
    try:
        wait_for_audio(entry)
    except Exception as exc:  # noqa: BLE001
        print("audio wait failed (%s) -- sending now" % exc)

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
