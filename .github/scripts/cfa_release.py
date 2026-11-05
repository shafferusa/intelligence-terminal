#!/usr/bin/env python3
"""Release the CFA Math course's lesson for today (SPEC Sec.0e).

The 90 lessons are written and fact-checked in advance and wait in
curriculum/cfa-l1/day-NN.html. This puts the one due today on the site, so a
lesson goes out every morning with no Claude run at all -- the course keeps
arriving even in a week when Claude usage runs out (which is what stopped the
Learning Brief on Oct 22-27, 2026).

Day N is due on start_date + (N - 1) days, America/New_York, every day of the
week. Releasing is idempotent and catches up: it releases every day from the
one after state's last_day through today's, in order, so a skipped morning
just means two lessons the next day, never a gap or a double.

Per lesson it:
  - fills the date placeholders (__DATE__, __DATELINE__, __GENERATED_AT__),
  - writes site/reports/cfa/l1/day-NN.html and checks it with check_report.py,
  - turns the previous lesson's disabled "Next" into a link,
  - prepends the index entry (from the page's report-meta; newest on top,
    which is what the Telegram push announces).
Then it records state/cfa.json and one state/run-log.jsonl line.

Usage:  cfa_release.py [--today YYYY-MM-DD] [--dry-run]
Prints `released=<days>` for the workflow; exits 1 if a due lesson is
missing or fails its check (after releasing everything before it).
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
STATE = "state/cfa.json"
INDEX = "site/reports/index.json"
SRC = "curriculum/cfa-l1/day-%02d.html"
DEST_DIR = "site/reports/cfa/l1"
DEST = DEST_DIR + "/day-%02d.html"
PATH = "reports/cfa/l1/day-%02d.html"
CHECK = ".github/scripts/check_report.py"
NEXT_OFF = '<a class="is-disabled" aria-disabled="true">Next report &rarr;</a>'


def stamp(html, day_date, now):
    gen = now.replace(microsecond=0).isoformat()
    return (html.replace("__DATE__", day_date.isoformat())
                .replace("__DATELINE__", "%s, %s %d, %d" % (
                    day_date.strftime("%A"), day_date.strftime("%B"), day_date.day, day_date.year))
                .replace("__GENERATED_AT__", gen))


def meta_of(html):
    m = re.search(r'<script type="application/json" id="report-meta">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def check(page, day_date):
    """check_report.py wants a dated filename; check a copy named like one."""
    with tempfile.TemporaryDirectory() as t:
        cp = os.path.join(t, "%s-cfa.html" % day_date.isoformat())
        shutil.copy(page, cp)
        r = subprocess.run([sys.executable, CHECK, cp], capture_output=True, text=True)
        return r.returncode == 0, r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    ap.add_argument("--dry-run", action="store_true", help="report what is due, write nothing")
    a = ap.parse_args()

    now = dt.datetime.now(ET)
    today = dt.date.fromisoformat(a.today) if a.today else now.date()
    st = json.load(open(STATE, encoding="utf-8"))
    if st.get("paused"):
        print("course paused in %s -- nothing released" % STATE)
        print("released=")
        return 0
    start = dt.date.fromisoformat(st["start_date"])
    total = int(st.get("total_days", 90))
    last = int(st.get("last_day", 0))
    due = min(total, (today - start).days + 1)
    if due <= last:
        print("nothing due: last released day %d, due day %d (%s)" % (last, due, today))
        print("released=")
        return 0

    index = json.load(open(INDEX, encoding="utf-8"))
    released, failure = [], None
    for n in range(last + 1, due + 1):
        src = SRC % n
        if not os.path.exists(src):
            failure = "day %d is due but %s does not exist" % (n, src)
            break
        html = stamp(open(src, encoding="utf-8").read(), today, now)
        meta = meta_of(html)
        if not meta or meta.get("path") != PATH % n or meta.get("slot") != "cfa":
            failure = "day %d: report-meta missing or path/slot wrong" % n
            break
        if "__DATE" in html or "__GENERATED_AT__" in html:
            failure = "day %d: a date placeholder survived stamping" % n
            break
        if a.dry_run:
            released.append(n)
            continue
        os.makedirs(DEST_DIR, exist_ok=True)
        open(DEST % n, "w", encoding="utf-8").write(html)
        ok, out = check(DEST % n, today)
        if not ok:
            os.remove(DEST % n)
            failure = "day %d failed check_report:\n%s" % (n, out)
            break
        # The previous lesson was published with "Next" disabled; link it now.
        prev = DEST % (n - 1)
        if n > 1 and os.path.exists(prev):
            p = open(prev, encoding="utf-8").read()
            if NEXT_OFF in p:
                p = p.replace(NEXT_OFF, '<a href="./day-%02d.html">Next report &rarr;</a>' % n, 1)
                open(prev, "w", encoding="utf-8").write(p)
        entry = {k: meta[k] for k in ("date", "slot", "title", "path", "summary", "headlines", "reading_minutes")}
        index = [e for e in index if e.get("path") != entry["path"]]
        index.insert(0, entry)
        released.append(n)

    if released and not a.dry_run:
        open(INDEX, "w", encoding="utf-8").write(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
        st["last_day"] = released[-1]
        st["last_released"] = today.isoformat()
        st.setdefault("released", []).extend({"day": n, "date": today.isoformat()} for n in released)
        open(STATE, "w", encoding="utf-8").write(json.dumps(st, indent=2, ensure_ascii=False) + "\n")
        with open("state/run-log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "slot": "cfa", "ok": failure is None, "telegram_ok": "delegated", "pages_ok": None,
                "note": "CFA Math: released day %s by the cfa-release Action%s." % (
                    ", ".join(map(str, released)), "; " + failure if failure else "")}) + "\n")

    print(("would release" if a.dry_run else "released") + " day(s): %s" % (released or "none"))
    if failure:
        print("PROBLEM: " + failure)
    print("released=" + ",".join(map(str, released)))
    return 1 if failure else 0


if __name__ == "__main__":
    sys.exit(main())
