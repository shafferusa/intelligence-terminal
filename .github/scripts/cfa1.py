#!/usr/bin/env python3
"""CFA Morning Lesson (slot cfa1): the bookkeeping the 4:50 AM routine runs as
plain commands, so the model spends its effort on teaching, not on JSON.

    python3 .github/scripts/cfa1.py today --out FILE [--date YYYY-MM-DD]
        Today's context: the plan day, yesterday's assignment and lesson, spaced-
        review candidates, open misconceptions, backlog, unread CFA replies,
        the previous report link, briefs on file. Writes JSON to FILE.
    python3 .github/scripts/cfa1.py feedback --apply FILE
        Apply the routine's reading of Logan's replies (progress, sessions,
        misconceptions, pause/resume) and advance the inbox cursor. Progress
        changes must cite the update_ids of the replies they came from.
    python3 .github/scripts/cfa1.py snapshot --reason-file FILE
        Save the current plan to state/cfa1/plan-history/vN.json and open
        version N+1 (call BEFORE editing plan.json; then run check-plan).
    python3 .github/scripts/cfa1.py check-plan
        Every module still scheduled, first pass by first_pass_by, the review
        window kept free of new reading, lesson numbers in order.
    python3 .github/scripts/cfa1.py finish --page PAGE --record FILE [--date YYYY-MM-DD]
        After the page passes check_report: index entry, lesson record,
        last-run (idempotency) and the run-log line. Prints the live URL.
    python3 .github/scripts/cfa1.py selftest --out FILE [--log]
        Pre-start check (before the first lesson): files, plan, queued page;
        --log also appends the run-log line.

Progress is recorded only from Logan's replies or explicit records -- never
from a lesson having been generated or a date having passed.
Stdlib only. Run from the repository root.
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

ST = "state/cfa1"
PLAN = ST + "/plan.json"
HIST = ST + "/plan-history"
PROGRESS = ST + "/progress.json"
LESSONS = ST + "/lessons.json"
MISC = ST + "/misconceptions.json"
CONTROL = ST + "/control.json"
INBOX = ST + "/inbox.jsonl"
CURSOR = ST + "/inbox-cursor.json"
BLOCKERS = ST + "/blockers.json"
MODULES = "curriculum/cfa1/modules.json"
BRIEFS = "curriculum/cfa1/briefs"
INDEX = "site/reports/index.json"
LAST_RUN = "state/last-run.json"
RUN_LOG = "state/run-log.jsonl"
BASE = "https://shafferusa.github.io/intelligence-terminal/"
SLOT = "cfa1"
SPACING = (2, 6, 13, 27, 55)          # days back, beyond yesterday: ~3, 7, 14, 28, 56-day reviews
STUDY_KINDS = {"study"}
REVIEW_KINDS = {"review", "mock", "mock_review", "buffer", "checkpoint", "psm", "exam"}


# ------------------------------------------------------------------ helpers

def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def et_now():
    """Current time in America/New_York (zoneinfo, or the US DST rule if tzdata is missing)."""
    utc = dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        y = utc.year
        mar = dt.datetime(y, 3, 8, 7, tzinfo=dt.timezone.utc)      # 2 AM EST on the 2nd Sunday of March
        mar += dt.timedelta(days=(6 - mar.weekday()) % 7)
        nov = dt.datetime(y, 11, 1, 6, tzinfo=dt.timezone.utc)     # 2 AM EDT on the 1st Sunday of November
        nov += dt.timedelta(days=(6 - nov.weekday()) % 7)
        off = -4 if mar <= utc < nov else -5
        return utc.astimezone(dt.timezone(dt.timedelta(hours=off)))


def today_et():
    return et_now().date().isoformat()


def d(s):
    return dt.date.fromisoformat(s)


def inbox_lines():
    out = []
    try:
        with open(INBOX, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return out


def plan_day(plan, date):
    for day in plan["days"]:
        if day["date"] == date:
            return day
    return None


def rel_link(from_path, to_path):
    """Relative href between two report paths (both like reports/YYYY/MM/file.html)."""
    rel = os.path.relpath(to_path, os.path.dirname(from_path)).replace(os.sep, "/")
    return rel if rel.startswith("../") else "./" + rel


def modules_index():
    m = load(MODULES, {"modules": []})
    return {x["key"]: x for x in m.get("modules", [])}


# ------------------------------------------------------------------ today

def cmd_today(a):
    date = a.date or today_et()
    plan = load(PLAN)
    if plan is None:
        sys.exit("no plan at " + PLAN)
    key = "%s-%s" % (date, SLOT)
    last = load(LAST_RUN, {}) or {}
    done = ((last.get("runs") or {}).get(key) or {}).get("status") == "success"

    day = plan_day(plan, date)
    days = plan["days"]
    idx = days.index(day) if day else None
    prev_day = days[idx - 1] if idx else None

    lessons = (load(LESSONS, {"lessons": []}) or {}).get("lessons", [])
    lessons = sorted(lessons, key=lambda x: x["date"])
    before = [x for x in lessons if x["date"] < date]
    last_lesson = before[-1] if before else None
    spaced = []
    for back in SPACING:
        target = (d(date) - dt.timedelta(days=back)).isoformat()
        for x in before:
            if x["date"] == target and x.get("recall"):
                spaced.append({"days_ago": back, "lesson": x.get("lesson"), "date": x["date"],
                               "modules": x.get("modules"), "recall": x.get("recall")})

    progress = load(PROGRESS, {"modules": {}, "sessions": []}) or {}
    mods = progress.get("modules", {})
    backlog = []
    for pd in days:
        if pd["date"] >= date or pd["kind"] not in STUDY_KINDS:
            continue
        for r in pd.get("reading", []):
            st = (mods.get(r["module"]) or {}).get("status", "not_started")
            if st != "done":
                backlog.append({"date": pd["date"], "module": r["module"], "title": r["title"], "status": st})

    misc = (load(MISC, {"items": []}) or {}).get("items", [])
    open_misc = [x for x in misc if x.get("status") == "open"]

    cursor = (load(CURSOR, {"last_update_id": 0}) or {}).get("last_update_id", 0) or 0
    new_msgs = [x for x in inbox_lines() if int(x.get("update_id", 0)) > int(cursor)]

    control = load(CONTROL, {"paused": False}) or {"paused": False}

    page_path = "reports/%s/%s/%s-%s.html" % (date[:4], date[5:7], date, SLOT)
    index = load(INDEX, []) or []
    prev_entry = None
    for e in index:
        if (e.get("date"), e.get("slot")) == (date, SLOT):
            continue
        if e.get("date", "") <= date:
            prev_entry = e
            break
    already_in_index = any((e.get("date"), e.get("slot")) == (date, SLOT) for e in index)

    briefs = {}
    for r in (day or {}).get("reading", []):
        p = "%s/%s.md" % (BRIEFS, r["module"])
        briefs[r["module"]] = p if os.path.exists(p) else None

    queued = (day or {}).get("queued")
    exam = plan["exam"]["date"]
    out = {
        "date": date,
        "dow": d(date).strftime("%a"),
        "run_key": key,
        "already_done": done,
        "already_in_index": already_in_index,
        "before_start": date < plan["start"],
        "after_exam": date > exam,
        "days_to_exam": (d(exam) - d(date)).days,
        "plan_version": plan.get("version"),
        "paused": bool(control.get("paused")),
        "control": control,
        "today": day,
        "yesterday_assignment": prev_day,
        "last_lesson": last_lesson,
        "spaced_review": spaced,
        "open_misconceptions": open_misc,
        "backlog": backlog,
        "progress_modules": mods,
        "recent_sessions": (progress.get("sessions") or [])[-10:],
        "inbox_cursor": cursor,
        "new_messages": new_msgs,
        "briefs": briefs,
        "queued_page": queued if queued and os.path.exists(queued) else None,
        "page": {"file": "site/" + page_path, "path": page_path, "url": BASE + page_path},
        "previous_report": ({"date": prev_entry.get("date"), "slot": prev_entry.get("slot"),
                             "href": rel_link(page_path, prev_entry["path"])} if prev_entry else None),
        "blockers": [b for b in (load(BLOCKERS, {"items": []}) or {}).get("items", [])
                     if b.get("status") == "open"],
    }
    save(a.out, out)
    t = day or {}
    print("date %s (%s) run_key %s already_done=%s paused=%s plan v%s" % (
        date, out["dow"], key, done, out["paused"], out["plan_version"]))
    print("today: lesson %s, kind %s, %s" % (t.get("lesson"), t.get("kind"), t.get("assignment")))
    print("new replies: %d, backlog items: %d, open misconceptions: %d, spaced-review lessons: %d" % (
        len(new_msgs), len(backlog), len(open_misc), len(spaced)))
    print("briefs: %s" % json.dumps(briefs))
    print("queued page: %s" % out["queued_page"])
    print("context written to %s" % a.out)
    return 0


# ------------------------------------------------------------------ feedback

VALID_STATUS = {"not_started", "partial", "done"}
VALID_UNDERSTANDING = {"unknown", "shaky", "solid"}


def cmd_feedback(a):
    fb = load(a.apply)
    if fb is None:
        sys.exit("no feedback file " + a.apply)
    inbox = {int(x["update_id"]): x for x in inbox_lines()}
    mods = modules_index()
    problems = []

    def cited(ids, what):
        ids = [int(i) for i in (ids or [])]
        if not ids:
            problems.append("%s: no source_update_ids -- progress changes only from Logan's replies" % what)
            return []
        missing = [i for i in ids if i not in inbox]
        if missing:
            problems.append("%s: update_ids %s are not in %s" % (what, missing, INBOX))
        return ids

    progress = load(PROGRESS, {"modules": {}, "sessions": []}) or {"modules": {}, "sessions": []}
    progress.setdefault("modules", {})
    progress.setdefault("sessions", [])
    now = et_now().isoformat(timespec="seconds")

    for s in fb.get("sessions", []):
        ids = cited(s.get("source_update_ids"), "session %s" % s.get("module"))
        if s.get("module") and s["module"] not in mods:
            problems.append("session: unknown module %s" % s["module"])
        if s.get("status") and s["status"] not in VALID_STATUS:
            problems.append("session: bad status %s" % s["status"])
        s = dict(s)
        s["source_update_ids"] = ids
        s["recorded"] = now
        progress["sessions"].append(s)

    for m in fb.get("module_status", []):
        ids = cited(m.get("source_update_ids"), "module_status %s" % m.get("module"))
        key = m.get("module")
        if key not in mods:
            problems.append("module_status: unknown module %s" % key)
            continue
        if m.get("status") not in VALID_STATUS:
            problems.append("module_status %s: bad status %s" % (key, m.get("status")))
            continue
        und = m.get("understanding", "unknown")
        if und not in VALID_UNDERSTANDING:
            problems.append("module_status %s: bad understanding %s" % (key, und))
            continue
        rec = progress["modules"].get(key, {})
        rec.update({"status": m["status"], "understanding": und, "evidence": m.get("evidence", ""),
                    "source": "telegram update_ids %s" % ids, "updated": now})
        if m.get("stopped_at"):
            rec["stopped_at"] = m["stopped_at"]
        progress["modules"][key] = rec

    misc = load(MISC, {"items": []}) or {"items": []}
    items = misc.setdefault("items", [])
    next_id = max([x.get("id", 0) for x in items] + [0]) + 1
    for x in fb.get("misconceptions_add", []):
        ids = cited(x.get("source_update_ids"), "misconception")
        items.append({"id": next_id, "opened": now[:10], "module": x.get("module"), "item": x.get("item"),
                      "correction": x.get("correction"), "source_update_ids": ids, "status": "open",
                      "taught": [], "resolved": None})
        next_id += 1
    for x in fb.get("misconceptions_resolve", []):
        hit = [m for m in items if m.get("id") == x.get("id")]
        if not hit:
            problems.append("misconceptions_resolve: no id %s" % x.get("id"))
            continue
        cited(x.get("source_update_ids"), "resolve %s" % x.get("id"))
        hit[0]["status"] = "resolved"
        hit[0]["resolved"] = {"date": now[:10], "evidence": x.get("evidence", "")}
    for x in fb.get("misconceptions_taught", []):
        for m in items:
            if m.get("id") == x:
                m.setdefault("taught", []).append(now[:10])

    control = load(CONTROL, {"paused": False}) or {"paused": False}
    if fb.get("control") is not None:
        c = fb["control"]
        cited(c.get("source_update_ids"), "control")
        control.update({"paused": bool(c.get("paused")), "note": c.get("note", ""), "updated": now})

    if problems:
        print("NOT APPLIED -- fix the feedback file:")
        for p in problems:
            print("  - " + p)
        return 1

    through = fb.get("processed_through")
    if through is not None:
        cur = (load(CURSOR, {"last_update_id": 0}) or {}).get("last_update_id", 0) or 0
        save(CURSOR, {"last_update_id": max(int(cur), int(through)), "updated": now})
    progress["updated"] = now
    progress["note"] = ("Recorded only from Logan's replies (state/cfa1/inbox.jsonl) or explicit records; "
                        "a lesson being sent is never evidence of study.")
    save(PROGRESS, progress)
    save(MISC, misc)
    save(CONTROL, control)
    print("applied: %d sessions, %d module updates, %d new misconceptions, %d resolved; cursor -> %s" % (
        len(fb.get("sessions", [])), len(fb.get("module_status", [])), len(fb.get("misconceptions_add", [])),
        len(fb.get("misconceptions_resolve", [])), through))
    return 0


# ------------------------------------------------------------------ plan

def cmd_snapshot(a):
    plan = load(PLAN)
    reason = open(a.reason_file, encoding="utf-8").read().strip()
    if not reason:
        sys.exit("empty reason")
    v = int(plan.get("version", 1))
    os.makedirs(HIST, exist_ok=True)
    snap = "%s/v%d.json" % (HIST, v)
    if not os.path.exists(snap):
        shutil.copyfile(PLAN, snap)
    plan["version"] = v + 1
    plan["revised"] = today_et()
    plan.setdefault("revisions", []).append({"version": v + 1, "date": today_et(), "summary": reason,
                                             "snapshot": snap})
    save(PLAN, plan)
    print("plan v%d saved to %s; now editing v%d. Run check-plan after your edits." % (v, snap, v + 1))
    return 0


def plan_problems(plan):
    out = []
    mods = modules_index()
    days = plan["days"]
    dates = [x["date"] for x in days]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        out.append("dates are not unique and in order")
    for i in range(1, len(days)):
        if (d(days[i]["date"]) - d(days[i - 1]["date"])).days != 1:
            out.append("gap in dates between %s and %s" % (days[i - 1]["date"], days[i]["date"]))
    n = 0
    for x in days:
        if x["date"] >= plan["start"]:
            n += 1
            if x.get("lesson") != n:
                out.append("%s: lesson number %s, expected %d" % (x["date"], x.get("lesson"), n))
    scheduled = {}
    for x in days:
        for r in x.get("reading", []):
            scheduled.setdefault(r["module"], []).append(x["date"])
            if r["module"] not in mods:
                out.append("%s: unknown module %s" % (x["date"], r["module"]))
    # Module 0 of each book is its glossary: reference material, used online, never a reading day.
    missing = sorted(k for k, v in mods.items() if k not in scheduled and v.get("module_num") != 0)
    if missing:
        out.append("modules no longer scheduled anywhere: %s" % ", ".join(missing))
    fp = plan["first_pass_by"]
    for x in days:
        if x["kind"] == "study" and x["date"] > fp:
            out.append("%s: first-pass study after %s" % (x["date"], fp))
    rw = plan["review_window"]
    for x in days:
        if rw["from"] <= x["date"] <= rw["to"] and x["kind"] == "study":
            out.append("%s: new reading inside the protected review window" % x["date"])
    if days[-1]["date"] != plan["exam"]["date"] or days[-1]["kind"] != "exam":
        out.append("the last day must be the exam day")
    for r in plan.get("revisions", []):
        if r.get("snapshot") and not os.path.exists(r["snapshot"]):
            out.append("revision v%s: snapshot %s missing" % (r.get("version"), r["snapshot"]))
    return out


def cmd_check_plan(a):
    plan = load(PLAN)
    p = plan_problems(plan)
    if p:
        print("PLAN FAIL")
        for line in p:
            print("  - " + line)
        return 1
    study = sum(1 for x in plan["days"] if x["kind"] == "study")
    print("PLAN OK v%s: %d days, %d study days, every module scheduled, first pass by %s, review window clear"
          % (plan.get("version"), len(plan["days"]), study, plan["first_pass_by"]))
    return 0


# ------------------------------------------------------------------ finish

def page_meta(page):
    raw = open(page, encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="report-meta">(.*?)</script>', raw, re.S)
    if not m:
        sys.exit("no report-meta in " + page)
    return json.loads(m.group(1))


def cmd_finish(a):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import check_report  # noqa: E402
    probs = check_report.problems(a.page)
    if probs:
        print("PAGE FAIL -- nothing recorded:")
        for p in probs:
            print("  - " + p)
        return 1
    meta = page_meta(a.page)
    date = a.date or meta["date"]
    if meta.get("slot") != SLOT or meta.get("date") != date:
        sys.exit("report-meta slot/date %s %s do not match %s %s" % (meta.get("slot"), meta.get("date"), SLOT, date))
    rec = load(a.record)
    if rec is None or rec.get("date") != date:
        sys.exit("lesson record missing or dated wrongly: " + a.record)
    for k in ("lesson", "modules", "concepts", "recall"):
        if k not in rec:
            sys.exit("lesson record lacks %r" % k)

    entry = {k: meta[k] for k in ("date", "slot", "title", "path", "summary", "headlines", "reading_minutes")}
    index = load(INDEX, []) or []
    hit = [i for i, e in enumerate(index) if (e.get("date"), e.get("slot")) == (date, SLOT)]
    if hit:
        index[hit[0]] = entry
    else:
        index.insert(0, entry)
    save(INDEX, index)

    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lessons = load(LESSONS, {"lessons": []}) or {"lessons": []}
    ls = [x for x in lessons.get("lessons", []) if x.get("date") != date]
    rec = dict(rec)
    rec["path"] = meta["path"]
    rec["published"] = now_utc
    ls.append(rec)
    lessons["lessons"] = sorted(ls, key=lambda x: x["date"])
    save(LESSONS, lessons)

    key = "%s-%s" % (date, SLOT)
    last = load(LAST_RUN, {"last_success": None, "runs": {}}) or {"last_success": None, "runs": {}}
    runs = last.setdefault("runs", {})
    runs[key] = {"status": "success", "ts": now_utc}
    cutoff = (d(date) - dt.timedelta(days=30)).isoformat()
    for k in list(runs):
        if k[:10] < cutoff:
            del runs[k]
    last["last_success"] = key
    save(LAST_RUN, last)

    line = {"ts": now_utc, "slot": SLOT, "ok": True, "telegram_ok": "delegated", "pages_ok": None,
            "sources_failed": [], "note": str(rec.get("run_note", ""))[:600]}
    with open(RUN_LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    print("recorded lesson %s for %s; index entry %s; last-run %s success" % (
        rec.get("lesson"), date, "replaced" if hit else "prepended", key))
    print("live URL: %s%s" % (BASE, meta["path"]))
    return 0


# ------------------------------------------------------------------ selftest

def cmd_selftest(a):
    results = []

    def check(name, ok, detail=""):
        results.append({"check": name, "ok": bool(ok), "detail": detail})

    plan = load(PLAN)
    check("plan.json loads", plan is not None)
    if plan:
        p = plan_problems(plan)
        check("plan passes check-plan", not p, "; ".join(p[:5]))
        start = plan["start"]
        first = plan_day(plan, start)
        q = (first or {}).get("queued")
        check("first lesson queued", bool(q and os.path.exists(q)), str(q))
        if q and os.path.exists(q):
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import check_report  # noqa: E402
            import tempfile
            tmp = os.path.join(tempfile.mkdtemp(prefix="cfa1-"), "%s-%s.html" % (start, SLOT))
            shutil.copyfile(q, tmp)
            probs = check_report.problems(tmp)
            check("queued lesson passes check_report", not probs, "; ".join(probs))
        week = [x for x in plan["days"] if start <= x["date"] <= (d(start) + dt.timedelta(days=6)).isoformat()]
        need = sorted({r["module"] for x in week for r in x.get("reading", [])})
        missing = [m for m in need if not os.path.exists("%s/%s.md" % (BRIEFS, m))]
        check("briefs on file for the first week", not missing, "missing: %s" % missing if missing else
              "%d modules" % len(need))
    check("modules.json loads", load(MODULES) is not None)
    for f in (PROGRESS, LESSONS, MISC, BLOCKERS, CURSOR, CONTROL):
        check("%s loads" % f, load(f) is not None)
    check("inbox file exists", os.path.exists(INBOX))
    check("routine instructions exist", os.path.exists("prompts/cfa-morning.md"))
    out = {"ran_at_et": et_now().isoformat(timespec="seconds"), "all_ok": all(r["ok"] for r in results),
           "results": results}
    save(a.out, out)
    if a.log:
        failed = [r["check"] for r in results if not r["ok"]]
        line = {"ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "slot": SLOT,
                "ok": out["all_ok"], "telegram_ok": "delegated", "pages_ok": None, "sources_failed": [],
                "note": "pre-start self-test: " + ("ALL OK" if not failed else "FAILED: " + "; ".join(failed))}
        with open(RUN_LOG, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    for r in results:
        print("%s  %s%s" % ("OK  " if r["ok"] else "FAIL", r["check"], (" -- " + r["detail"]) if r["detail"] else ""))
    print("ALL OK" if out["all_ok"] else "SELFTEST FAILED")
    return 0 if out["all_ok"] else 1


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("today")
    p.add_argument("--out", required=True)
    p.add_argument("--date")
    p = sub.add_parser("feedback")
    p.add_argument("--apply", required=True)
    p = sub.add_parser("snapshot")
    p.add_argument("--reason-file", required=True)
    sub.add_parser("check-plan")
    p = sub.add_parser("finish")
    p.add_argument("--page", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--date")
    p = sub.add_parser("selftest")
    p.add_argument("--out", required=True)
    p.add_argument("--log", action="store_true", help="also append the run-log line")
    a = ap.parse_args()
    return {"today": cmd_today, "feedback": cmd_feedback, "snapshot": cmd_snapshot,
            "check-plan": cmd_check_plan, "finish": cmd_finish, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
