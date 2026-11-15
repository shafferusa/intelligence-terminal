#!/usr/bin/env python3
"""Capture Logan's SIE quiz answers from Telegram and score them instantly.

The SIE Program (prompts/sie.md) publishes a quiz at noon every day. Logan
answers by messaging the bot, e.g.  "SIE 5: BDACA CBDAB"  or  "SIE 5: 1B 2D 3A".
Two problems this script exists to solve:

1. Telegram keeps undelivered bot updates for only 24 hours. A reply sent at
   11 a.m. would be gone before the next noon run could read it. So this runs
   every ~15 minutes from GitHub Actions and appends each SIE message to
   state/sie/inbox.jsonl, which the noon routine reads (and never writes).

2. Waiting a day for a score is a poor feedback loop. The routine writes each
   quiz's answer key to state/sie/quizzes/day-NN.json at publish time, so this
   script can reply within minutes with the score, the section split and the
   one-line rule for every miss. The full walk-through -- every distractor --
   still comes in the next noon edition.

Two phases, so an answer is never acknowledged unless it was saved:

  collect  -> read updates, append new SIE messages to the inbox, write the
              replies to $SIE_PENDING (outside the repo). Does NOT confirm.
  (workflow commits + pushes the inbox)
  confirm  -> send the replies, then confirm the updates with getUpdates
              offset=max+1 so Telegram stops re-delivering them.

If the push fails, confirm never runs; the next poll sees the same updates
again, and because the inbox on main does not contain them they are simply
collected again. If confirm fails after a successful push, the next poll
finds those update_ids already in the inbox and neither stores nor replies
twice.

Only messages from TG_CHAT are considered, and only SIE and CFA messages are
stored (the repo is public; nothing else Logan sends the bot is written
anywhere).

CFA Morning Lesson (2026-11-15). Messages that start with "CFA" are Logan's
study reports for the morning-lesson routine (prompts/cfa-morning.md), e.g.
"CFA: M3 done, 2h10, 6/8, missed Q7 Q21". They go to state/cfa1/inbox.jsonl
the same way, with a short acknowledgement echoing what was understood. The
4:50 AM routine reads that inbox (and never writes it) and records progress
only from these messages.

Actions is the only Telegram sender in this system (shared-rules section 14).
The routines read the inboxes; they never send.

Env: TG_TOKEN, TG_CHAT, SIE_PENDING (path for the collect->confirm handoff).
Stdlib only.
"""

import glob
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

INBOX = "state/sie/inbox.jsonl"
CFA_INBOX = "state/cfa1/inbox.jsonl"
STATE = "state/sie.json"
KEYS = "state/sie/quizzes"
LIMIT = 4096
BASE = "https://shafferusa.github.io/intelligence-terminal/"

SECTION_NAMES = {
    "1": "Capital markets",
    "2": "Products & risks",
    "3": "Trading, accounts & prohibited acts",
    "4": "Regulatory framework",
}

HELP = (
    "<b>SIE Program — how to reply</b>\n"
    "Answers: <code>SIE 5: BDACA CBDAB</code> (one letter per question, in order; "
    "<code>-</code> to skip) or <code>SIE 5: 1B 2D 3A</code>. Notes after the letters are kept "
    "(\"Q4 guessed\").\n"
    "Exam date: <code>SIE exam 2026-10-24</code>\n"
    "<code>SIE pause</code> · <code>SIE resume</code> · <code>SIE stop</code> · "
    "<code>SIE note …</code>\n"
    "You get your score here within ~15 minutes; the full walk-through is in the next noon edition."
)


# ---------------------------------------------------------------- telegram

def api(token, method, params=None, timeout=30):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = None
    headers = {}
    if params is not None:
        data = json.dumps(params).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# ---------------------------------------------------------------- parsing

ANS_CH = "ABCDabcd?_-"


def parse(text):
    """Return a parsed dict, or None if the message is not SIE-related."""
    t = (text or "").strip()
    m = re.match(r"^/?sie\b\s*[:#\-]?\s*", t, re.I)
    prefixed = bool(m)
    body = t[m.end():] if m else t

    if prefixed:
        if re.match(r"help\b", body, re.I) or not body:
            return {"kind": "command", "command": "help"}
        c = re.match(r"(pause|resume|stop)\b", body, re.I)
        if c:
            return {"kind": "command", "command": c.group(1).lower()}
        c = re.match(r"note\b[:\s]*(.*)$", body, re.I | re.S)
        if c:
            return {"kind": "command", "command": "note", "text": c.group(1).strip()}
        c = re.match(r"exam(?:\s+date)?\s*[:\s]\s*(\d{4}-\d{2}-\d{2})\b", body, re.I)
        if c:
            return {"kind": "command", "command": "exam_date", "date": c.group(1)}

    day = None
    rest = body
    # "5: …", "5 …", "day 5 …", "exam 26: …" -- but not "1B 2C …" (numbered answers).
    d = re.match(r"(?:day|exam|d)?\s*#?\s*(\d{1,3})\s*(?::|\s)\s*", body, re.I)
    if d:
        day = int(d.group(1))
        rest = body[d.end():]

    answers = ""
    notes = ""
    pairs = re.findall(r"(?<!\d)(\d{1,3})\s*[.):=\-]?\s*([A-Da-d?_\-])(?![A-Za-z])", rest)
    if len(pairs) >= 2:
        got = {}
        for n, letter in pairs:
            n = int(n)
            if 1 <= n <= 150:
                got[n] = letter
        if got:
            answers = "".join(got.get(i, "-") for i in range(1, max(got) + 1))
        tail = re.split(r"(?<!\d)\d{1,3}\s*[.):=\-]?\s*[A-Da-d?_\-](?![A-Za-z])", rest)
        notes = " ".join(s.strip(" ,;|/") for s in tail if s.strip(" ,;|/")).strip()
    else:
        tokens = rest.split()
        used = 0
        for tok in tokens:
            if tok and all(ch in ANS_CH + ",|/" for ch in tok):
                answers += "".join(ch for ch in tok if ch in ANS_CH)
                used += 1
            else:
                break
        notes = " ".join(tokens[used:]).strip()

    answers = answers.upper().replace("?", "-").replace("_", "-")

    if not answers:
        if prefixed:
            return {"kind": "unknown", "day": day}
        return None
    # An unprefixed message only counts if it is unmistakably an answer string.
    if not prefixed and (len(answers) < 5 or notes and len(notes) > 200):
        return None
    return {"kind": "answers", "day": day, "answers": answers, "notes": notes}


# ---------------------------------------------------------------- keys & grading

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


def key_path(day):
    return os.path.join(KEYS, "day-%02d.json" % day)


def latest_open_day():
    state = load_json(STATE, {})
    open_days = [q.get("day") for q in state.get("quizzes", []) if q.get("status") == "open"]
    open_days = [d for d in open_days if isinstance(d, int)]
    if open_days:
        return max(open_days)
    days = []
    for p in glob.glob(os.path.join(KEYS, "day-*.json")):
        m = re.search(r"day-(\d+)\.json$", p)
        if m:
            days.append(int(m.group(1)))
    return max(days) if days else None


def grade(day, answers):
    key = load_json(key_path(day), None)
    if not key or not key.get("questions"):
        return None
    qs = sorted(key["questions"], key=lambda q: q.get("n", 0))
    correct = 0
    by_sec = {}
    misses = []
    for i, q in enumerate(qs):
        given = answers[i] if i < len(answers) else "-"
        right = str(q.get("answer", "")).upper()
        sec = str(q.get("section", "?"))
        c, t = by_sec.get(sec, (0, 0))
        ok = given == right
        by_sec[sec] = (c + (1 if ok else 0), t + 1)
        if ok:
            correct += 1
        else:
            misses.append((q.get("n", i + 1), given, right, q.get("rule", "")))
    return {
        "kind": key.get("kind", "quiz"),
        "total": len(qs),
        "correct": correct,
        "by_section": by_sec,
        "misses": misses,
        "extra": max(0, len(answers) - len(qs)),
        "short": max(0, len(qs) - len(answers)),
    }


def score_message(day, g):
    pct = round(100 * g["correct"] / g["total"]) if g["total"] else 0
    label = "practice exam" if g["kind"] == "exam" else "quiz"
    lines = ["<b>SIE Day %d %s — %d/%d (%d%%)</b>" % (day, label, g["correct"], g["total"], pct)]
    if g["kind"] == "exam":
        lines.append("Passing line on the real SIE is 70%%. You are %s it." %
                     ("above" if pct >= 70 else "below"))
    secs = []
    for s in sorted(g["by_section"]):
        c, t = g["by_section"][s]
        secs.append("%s %d/%d" % (SECTION_NAMES.get(s, "Section " + s), c, t))
    if secs:
        lines.append(" · ".join(secs))
    if g["short"]:
        lines.append("Note: %d answer(s) missing at the end — counted as unanswered." % g["short"])
    if g["extra"]:
        lines.append("Note: %d extra letter(s) ignored." % g["extra"])
    if g["misses"]:
        lines.append("")
        lines.append("<b>Missed</b>")
        for n, given, right, rule in g["misses"]:
            lines.append("• Q%s — you %s, answer %s. %s" % (
                n, html.escape(given if given != "-" else "skipped"), html.escape(right),
                html.escape(rule)))
    else:
        lines.append("")
        lines.append("Clean sheet.")
    lines.append("")
    lines.append("Every distractor explained in the next noon edition.")
    text = "\n".join(lines)
    if len(text) > LIMIT:
        text = text[: LIMIT - 80].rsplit("\n", 1)[0] + "\n…\nFull list in the next noon edition."
    return text


def reply_for(parsed):
    kind = parsed["kind"]
    if kind == "command":
        cmd = parsed["command"]
        if cmd == "help":
            return HELP
        if cmd == "exam_date":
            return "Exam date noted: <b>%s</b>. The program will pace to it from the next noon edition." % (
                html.escape(parsed["date"]))
        if cmd == "pause":
            return "SIE Program will pause from the next noon run. Send <code>SIE resume</code> to continue."
        if cmd == "resume":
            return "SIE Program resumes at the next noon run."
        if cmd == "stop":
            return "SIE Program will stop at the next noon run. Send <code>SIE resume</code> if that was a mistake."
        if cmd == "note":
            return "Note saved for the next edition."
    if kind == "answers":
        day = parsed.get("day") or latest_open_day()
        if not day:
            return "Answers saved. No open quiz found to score against yet — the next noon edition will sort it out."
        parsed["day_resolved"] = day
        g = grade(day, parsed["answers"])
        if g is None:
            return ("Answers saved for Day %d (%d letters). No answer key on file for that day — "
                    "they will be graded in the next noon edition." % (day, len(parsed["answers"])))
        return score_message(day, g)
    return "Saved. I could not read answers in that message; the next noon edition will interpret it. Send <code>SIE help</code> for the format."


# ---------------------------------------------------------------- CFA morning lessons

CFA_HELP = (
    "<b>CFA Morning Lesson — how to reply</b>\n"
    "After each night's study, one message starting with <code>CFA</code>:\n"
    "<code>CFA: M3 done, 2h10, 6/8, missed Q7 Q21, deflator confused me</code>\n"
    "<code>CFA: M3 partial, stopped at p.110, 1h55</code>\n"
    "<code>CFA: skipped tonight</code>\n"
    "Anything in plain words works; the 4:50 AM lesson reads it, records your progress and "
    "corrects mistakes. Progress is only ever recorded from these messages.\n"
    "<code>CFA pause</code> · <code>CFA resume</code> · <code>CFA note …</code>"
)


def parse_cfa(text):
    """Return a parsed dict for a CFA message, or None if it is not one.

    The routine interprets the raw text; this only pulls out what the
    acknowledgement echoes back, so Logan can see at once what was understood."""
    t = (text or "").strip()
    m = re.match(r"^/?cfa\b\s*[:#\-]?\s*", t, re.I)
    if not m:
        return None
    body = t[m.end():].strip()
    if not body or re.match(r"help\b", body, re.I):
        return {"kind": "command", "command": "help"}
    c = re.match(r"(pause|resume)\b", body, re.I)
    if c:
        return {"kind": "command", "command": c.group(1).lower()}
    c = re.match(r"note\b[:\s]*(.*)$", body, re.I | re.S)
    if c:
        return {"kind": "command", "command": "note", "text": c.group(1).strip()}
    out = {"kind": "report"}
    mod = re.search(r"\b(?:m|lm|module)\s*\.?\s*(\d{1,2})\b", body, re.I)
    if mod:
        out["module"] = int(mod.group(1))
    st = re.search(r"\b(done|finished|complete|completed|partial|half|started|skipped|missed|not done|didn'?t)\b",
                   body, re.I)
    if st:
        w = st.group(1).lower()
        out["status"] = ("done" if w in ("done", "finished", "complete", "completed")
                         else "partial" if w in ("partial", "half", "started") else "not done")
    hm = re.search(r"\b(\d{1,2})\s*h(?:ours?|rs?)?\s*(\d{1,2})?\s*(?:m(?:in)?s?)?\b", body, re.I)
    mins = re.search(r"\b(\d{2,3})\s*(?:m|min|mins|minutes)\b", body, re.I)
    if hm:
        out["minutes"] = int(hm.group(1)) * 60 + int(hm.group(2) or 0)
    elif mins:
        out["minutes"] = int(mins.group(1))
    sc = re.search(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b", body)
    if sc and int(sc.group(2)) > 0 and int(sc.group(1)) <= int(sc.group(2)):
        out["score"] = "%s/%s" % (sc.group(1), sc.group(2))
    missed = re.findall(r"\bq\s*(\d{1,3})\b", body, re.I)
    if missed:
        out["questions"] = sorted({int(x) for x in missed})
    return out


def reply_cfa(parsed):
    kind = parsed["kind"]
    if kind == "command":
        cmd = parsed["command"]
        if cmd == "help":
            return CFA_HELP
        if cmd == "pause":
            return ("Morning lessons pause from the next 4:50 AM run. Send <code>CFA resume</code> to continue; "
                    "the plan will be re-dated from that day, with nothing dropped.")
        if cmd == "resume":
            return "Morning lessons resume at the next 4:50 AM run, with the plan re-dated from there."
        if cmd == "note":
            return "Note saved for the next morning lesson."
    bits = []
    if parsed.get("module"):
        bits.append("M%d" % parsed["module"])
    if parsed.get("status"):
        bits.append(parsed["status"])
    if parsed.get("minutes"):
        bits.append("%dh%02d" % divmod(parsed["minutes"], 60))
    if parsed.get("score"):
        bits.append(parsed["score"])
    if parsed.get("questions"):
        bits.append("Q" + ", Q".join(str(q) for q in parsed["questions"]))
    seen = (" — read as: " + html.escape(" · ".join(bits))) if bits else ""
    return ("Saved for the 4:50 AM lesson%s. It will record your progress and work on what you missed. "
            "Send <code>CFA help</code> for examples." % seen)


# ---------------------------------------------------------------- phases

def known_ids():
    ids = set()
    for path in (INBOX, CFA_INBOX):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            ids.add(int(json.loads(line)["update_id"]))
                        except Exception:  # noqa: BLE001
                            pass
        except FileNotFoundError:
            pass
    return ids


def collect(token, chat, pending_path):
    resp = api(token, "getUpdates", {"timeout": 0, "allowed_updates": ["message", "edited_message"]})
    updates = resp.get("result") or []
    seen = known_ids()
    new_lines = []
    cfa_lines = []
    replies = []
    max_id = None
    for u in updates:
        uid = int(u.get("update_id"))
        max_id = uid if max_id is None else max(max_id, uid)
        msg = u.get("message") or u.get("edited_message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat):
            continue
        if uid in seen:
            continue
        text = msg.get("text") or ""
        stamp = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cfa = parse_cfa(text)
        if cfa is not None:
            cfa_lines.append({"update_id": uid, "date": stamp, "text": text[:4000], "parsed": cfa})
            replies.append(reply_cfa(cfa))
            continue
        parsed = parse(text)
        if parsed is None:
            continue
        reply = reply_for(parsed)
        new_lines.append({"update_id": uid, "date": stamp, "text": text[:4000], "parsed": parsed})
        replies.append(reply)

    for path, lines in ((INBOX, new_lines), (CFA_INBOX, cfa_lines)):
        if lines:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    with open(pending_path, "w", encoding="utf-8") as fh:
        json.dump({"max_update_id": max_id, "replies": replies}, fh)
    print("updates: %d, new SIE messages: %d, new CFA messages: %d" % (len(updates), len(new_lines), len(cfa_lines)))
    return 0


def confirm(token, chat, pending_path):
    pending = load_json(pending_path, None)
    if not pending:
        print("nothing pending")
        return 0
    failures = 0
    for text in pending.get("replies", []):
        try:
            api(token, "sendMessage", {
                "chat_id": chat, "text": text, "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            })
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("reply failed:", exc)
    max_id = pending.get("max_update_id")
    if max_id is not None:
        # Confirms every update up to max_id so Telegram stops re-sending them.
        api(token, "getUpdates", {"offset": int(max_id) + 1, "timeout": 0})
        print("confirmed through update", max_id)
    return 1 if failures else 0


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    token = os.environ.get("TG_TOKEN")
    chat = os.environ.get("TG_CHAT")
    pending = os.environ.get("SIE_PENDING", "/tmp/sie-pending.json")
    if not token or not chat:
        print("Telegram credentials not configured -- nothing to do.")
        return 0
    if phase == "collect":
        return collect(token, chat, pending)
    if phase == "confirm":
        return confirm(token, chat, pending)
    print("usage: sie_inbox.py collect|confirm")
    return 2


if __name__ == "__main__":
    sys.exit(main())
