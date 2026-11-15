#!/usr/bin/env python3
"""CFA Morning Lesson (slot cfa1): the bookkeeping the 4:50 AM routine runs as
plain commands, so the model spends its effort on teaching, not on JSON.

    python3 .github/scripts/cfa1.py today --out FILE [--date YYYY-MM-DD]
        Today's context: the plan day, yesterday's assignment and lesson, spaced-
        review candidates, open misconceptions, backlog, unread CFA replies,
        the previous report link, briefs on file, the time now (ET). Writes
        JSON to FILE. Run it again after `feedback`: progress has changed.
    python3 .github/scripts/cfa1.py feedback --apply FILE
        Apply the routine's reading of Logan's replies (progress, sessions,
        misconceptions, pause/resume) and advance the inbox cursor. Progress
        changes must cite the update_ids of the replies they came from.
    python3 .github/scripts/cfa1.py start-page [--date YYYY-MM-DD] [--record-out FILE] [--fresh]
        Create today's page: the queued lesson if one is waiting (and its
        lesson record, copied to --record-out), otherwise the template.
        Leaves an existing page alone unless --fresh.
    python3 .github/scripts/cfa1.py snapshot --reason-file FILE
        Save the current plan to state/cfa1/plan-history/vN.json and open
        version N+1 (call BEFORE editing plan.json; then run check-plan).
    python3 .github/scripts/cfa1.py check-plan
        Every module still scheduled, first pass by first_pass_by, the review
        window kept free of new reading, lesson numbers in order.
    python3 .github/scripts/cfa1.py finish --page PAGE --record FILE [--date YYYY-MM-DD]
        Stamps the page (dateline date and reading time, title element,
        reading_minutes, generated_at, Previous link), then checks it
        (check_report plus the lesson checks: no template text left, h1 =
        report-meta title, lesson number, days to the exam) and only then
        records: index entry, lesson record, last-run (idempotency) and the
        run-log line. Prints the live URL.
    python3 .github/scripts/cfa1.py selftest --out FILE [--log]
        Pre-start check (before the first lesson): files, plan, queued page
        and record, the page guards; --log also appends the run-log line.

Progress is recorded only from Logan's replies or explicit records -- never
from a lesson having been generated or a date having passed.
Stdlib only. Run from the repository root.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sys

ST = "state/cfa1"
QUEUE = ST + "/queue"
TEMPLATE = "curriculum/cfa1/lesson-template.html"
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


# modules.json keeps the Master Study Plan's course labels; lessons use the books' own wording.
COURSE_DISPLAY = {"Economics prerequisite": "Prerequisite Economics",
                  "FSA prerequisite": "Prerequisite Financial Statement Analysis"}


def brief_questions(key):
    """Question numbers in a module brief's question list ("- Q7 — topic"), or None without a brief."""
    try:
        with open("%s/%s.md" % (BRIEFS, key), encoding="utf-8") as fh:
            txt = fh.read()
    except (FileNotFoundError, TypeError):
        return None
    return sorted({int(n) for n in re.findall(r"(?m)^- Q(\d+) —", txt)})


def module_info(mods, key):
    """What a lesson needs to name a module: display course, 'Module M of T', question count."""
    m = mods.get(key)
    if not m:
        return None
    total = sum(1 for x in mods.values() if x.get("course") == m.get("course") and x.get("module_num") != 0)
    qs = brief_questions(key)
    return {"course": COURSE_DISPLAY.get(m.get("course"), m.get("course")), "module_num": m.get("module_num"),
            "of": total, "title": m.get("title"), "book_title": m.get("book_title"),
            "questions_in_brief": len(qs) if qs else None}


def page_paths(date):
    path = "reports/%s/%s/%s-%s.html" % (date[:4], date[5:7], date, SLOT)
    return path, "site/" + path


def queued_record_path(date):
    return "%s/%s-%s.record.json" % (QUEUE, date, SLOT)


def previous_entry(index, date):
    """The report the Previous link points to: the newest earlier CFA lesson,
    else (before Lesson 2) the newest earlier report of any slot."""
    earlier = [e for e in index if e.get("path") and e.get("date", "") <= date
               and (e.get("date"), e.get("slot")) != (date, SLOT)]
    for pool in ([e for e in earlier if e.get("slot") == SLOT and e["date"] < date], earlier):
        if pool:
            return sorted(pool, key=lambda e: e["date"], reverse=True)[0]   # stable: index order breaks ties
    return None


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
    older_misc = [x for x in open_misc if (x.get("opened") or "") < date]   # today's belong to the Correction block
    shaky = [{"module": k, "evidence": v.get("evidence"), "updated": v.get("updated"),
              "brief": "%s/%s.md" % (BRIEFS, k) if os.path.exists("%s/%s.md" % (BRIEFS, k)) else None}
             for k, v in sorted(mods.items()) if v.get("understanding") == "shaky"]
    mindex = modules_index()
    wanted = [r["module"] for x in (day, prev_day) if x for r in x.get("reading", [])]
    wanted += [b["module"] for b in backlog] + [s["module"] for s in shaky]
    minfo = {k: module_info(mindex, k) for k in dict.fromkeys(wanted)}

    cursor = (load(CURSOR, {"last_update_id": 0}) or {}).get("last_update_id", 0) or 0
    new_msgs = [x for x in inbox_lines() if int(x.get("update_id", 0)) > int(cursor)]

    control = load(CONTROL, {"paused": False}) or {"paused": False}

    page_path, page_file = page_paths(date)
    index = load(INDEX, []) or []
    prev_entry = previous_entry(index, date)
    already_in_index = any((e.get("date"), e.get("slot")) == (date, SLOT) for e in index)

    briefs = {}
    for r in (day or {}).get("reading", []):
        p = "%s/%s.md" % (BRIEFS, r["module"])
        briefs[r["module"]] = p if os.path.exists(p) else None

    queued = (day or {}).get("queued")
    qrec = queued_record_path(date)
    exam = plan["exam"]["date"]
    out = {
        "date": date,
        "now_et": et_now().isoformat(timespec="seconds"),
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
        "older_misconceptions": older_misc,
        "shaky_modules": shaky,
        "modules_info": minfo,
        "backlog": backlog,
        "progress_modules": mods,
        "recent_sessions": (progress.get("sessions") or [])[-10:],
        "inbox_cursor": cursor,
        "new_messages": new_msgs,
        "briefs": briefs,
        "queued_page": queued if queued and os.path.exists(queued) else None,
        "queued_record": qrec if os.path.exists(qrec) else None,
        "page": {"file": page_file, "path": page_path, "url": BASE + page_path,
                 "exists": os.path.exists(page_file)},
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
    print("new replies: %d, backlog items: %d, open misconceptions: %d (%d older), shaky modules: %d, "
          "spaced-review lessons: %d" % (len(new_msgs), len(backlog), len(open_misc), len(older_misc),
                                         len(shaky), len(spaced)))
    print("briefs: %s" % json.dumps(briefs))
    print("queued page: %s, queued record: %s" % (out["queued_page"], out["queued_record"]))
    print("previous report: %s" % ((out["previous_report"] or {}).get("href")))
    print("now (ET): %s" % out["now_et"])
    print("context written to %s" % a.out)
    return 0


# ------------------------------------------------------------------ feedback

VALID_STATUS = {"not_started", "partial", "done"}
VALID_UNDERSTANDING = {"unknown", "shaky", "solid"}


def question_problems(module, qs, what):
    """Missed questions must be written Q7 and exist in the module brief's question list."""
    out = []
    listed = brief_questions(module) if module else None
    for q in qs:
        n = re.match(r"Q(\d+)$", str(q).strip())
        if not n:
            out.append("%s: missed question %r should be written like Q7" % (what, q))
        elif listed and int(n.group(1)) not in listed:
            out.append("%s: %s is not in the %s brief's question list (Q%d-Q%d)" % (
                what, q, module, listed[0], listed[-1]))
    return out


def cmd_feedback(a):
    fb = load(a.apply)
    if fb is None:
        sys.exit("no feedback file " + a.apply)
    inbox = {int(x["update_id"]): x for x in inbox_lines()}
    mods = modules_index()
    problems = []
    accounted = set()
    day = a.date or today_et()          # the lesson date: opened / taught / resolved

    def cited(ids, what):
        ids = [int(i) for i in (ids or [])]
        accounted.update(ids)
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
        if s.get("date_studied") and not re.match(r"\d{4}-\d{2}-\d{2}$", str(s["date_studied"])):
            problems.append("session %s: date_studied %r is not YYYY-MM-DD" % (s.get("module"), s["date_studied"]))
        problems.extend(question_problems(s.get("module"), s.get("missed") or [], "session %s" % s.get("module")))
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
        if und == "solid":
            for s in fb.get("sessions", []):
                sc = re.match(r"\s*(\d+)\s*/\s*(\d+)\s*$", str(s.get("score") or ""))
                if s.get("module") == key and sc and int(sc.group(2)) and int(sc.group(1)) < 0.75 * int(sc.group(2)):
                    problems.append("module_status %s: \"solid\" needs a score of at least 75%%; this file reports %s"
                                    % (key, s["score"]))
        rec = progress["modules"].get(key, {})
        rec.update({"status": m["status"], "understanding": und, "evidence": m.get("evidence", ""),
                    "source": "telegram update_ids %s" % ids, "updated": now})
        if m.get("stopped_at"):
            rec["stopped_at"] = m["stopped_at"]
        progress["modules"][key] = rec

    misc = load(MISC, {"items": []}) or {"items": []}
    items = misc.setdefault("items", [])
    next_id = max([x.get("id", 0) for x in items] + [0]) + 1
    new_ids = []
    for x in fb.get("misconceptions_add", []):
        ids = cited(x.get("source_update_ids"), "misconception")
        if not x.get("item"):
            problems.append("misconceptions_add: empty item")
        if x.get("module") and x["module"] not in mods:
            problems.append("misconceptions_add: unknown module %s" % x["module"])
        problems.extend(question_problems(x.get("module"), re.findall(r"\bQ\d+\b", x.get("item") or ""),
                                          "misconceptions_add"))
        # "taught": true when today's lesson teaches the correction (the usual case).
        items.append({"id": next_id, "opened": day, "module": x.get("module"), "item": x.get("item"),
                      "correction": x.get("correction"), "source_update_ids": ids, "status": "open",
                      "taught": [day] if x.get("taught") is True else [], "resolved": None})
        new_ids.append(next_id)
        next_id += 1
    for x in fb.get("misconceptions_resolve", []):
        hit = [m for m in items if m.get("id") == x.get("id")]
        if not hit:
            problems.append("misconceptions_resolve: no id %s" % x.get("id"))
            continue
        cited(x.get("source_update_ids"), "resolve %s" % x.get("id"))
        hit[0]["status"] = "resolved"
        hit[0]["resolved"] = {"date": day, "evidence": x.get("evidence", "")}
    for x in fb.get("misconceptions_taught", []):
        hit = [m for m in items if m.get("id") == x and m.get("id") not in new_ids]
        if not hit:
            problems.append("misconceptions_taught: no earlier misconception with id %s "
                            "(for one added in this file, set \"taught\": true on it instead)" % x)
            continue
        if day not in hit[0].setdefault("taught", []):
            hit[0]["taught"].append(day)

    control = load(CONTROL, {"paused": False}) or {"paused": False}
    if fb.get("control") is not None:
        c = fb["control"]
        cited(c.get("source_update_ids"), "control")
        control.update({"paused": bool(c.get("paused")), "note": c.get("note", ""), "updated": now})

    # Every reply up to processed_through must be accounted for: cited by a change above, or listed
    # in "noted" with the reason it changes nothing (a CFA note, a duplicate, a pasted sample...).
    noted = []
    for n in fb.get("noted", []):
        uid = int(n.get("update_id", 0))
        if uid not in inbox:
            problems.append("noted: update_id %s is not in %s" % (uid, INBOX))
        if not str(n.get("why", "")).strip():
            problems.append("noted %s: say why it needs no change" % uid)
        accounted.add(uid)
        noted.append({"update_id": uid, "why": str(n.get("why", ""))[:300], "recorded": now})
    cur = int((load(CURSOR, {"last_update_id": 0}) or {}).get("last_update_id", 0) or 0)
    through = fb.get("processed_through")
    if through is not None:
        skipped = sorted(u for u in inbox if cur < u <= int(through) and u not in accounted)
        if skipped:
            problems.append("replies %s are neither cited by a change nor listed in \"noted\" with a reason "
                            "(every reply up to processed_through must be accounted for)" % skipped)

    if problems:
        print("NOT APPLIED -- fix the feedback file:")
        for p in problems:
            print("  - " + p)
        return 1

    if through is not None:
        save(CURSOR, {"last_update_id": max(cur, int(through)), "updated": now})
    if noted:
        progress.setdefault("noted", []).extend(noted)
    progress["updated"] = now
    progress["note"] = ("Recorded only from Logan's replies (state/cfa1/inbox.jsonl) or explicit records; "
                        "a lesson being sent is never evidence of study.")
    save(PROGRESS, progress)
    save(MISC, misc)
    save(CONTROL, control)
    print("applied: %d sessions, %d module updates, %d new misconceptions (ids %s), %d resolved; cursor -> %s" % (
        len(fb.get("sessions", [])), len(fb.get("module_status", [])), len(new_ids), new_ids,
        len(fb.get("misconceptions_resolve", [])), through))
    print("now re-run `today`: progress, backlog and misconceptions have changed")
    return 0


# ------------------------------------------------------------------ start-page

def cmd_start_page(a):
    date = a.date or today_et()
    plan = load(PLAN)
    if plan is None:
        sys.exit("no plan at " + PLAN)
    day = plan_day(plan, date)
    if day is None:
        sys.exit("no plan day for %s" % date)
    page_path, page_file = page_paths(date)
    queued = day.get("queued")
    if os.path.exists(page_file) and not a.fresh:
        print("page exists, left as is: %s (--fresh starts it over)" % page_file)
    else:
        src = queued if queued and os.path.exists(queued) else TEMPLATE
        os.makedirs(os.path.dirname(page_file), exist_ok=True)
        shutil.copyfile(src, page_file)
        print("%s -> %s (%s)" % (src, page_file, "queued lesson: change only what Step 4 lists"
                                 if src != TEMPLATE else "template: replace every placeholder"))
    qrec = queued_record_path(date)
    if os.path.exists(qrec):
        if os.path.exists(a.record_out) and not a.fresh:
            print("record exists, left as is: %s" % a.record_out)
        else:
            d_ = os.path.dirname(a.record_out)
            if d_:
                os.makedirs(d_, exist_ok=True)
            shutil.copyfile(qrec, a.record_out)
            print("%s -> %s (the queued lesson's record: update run_note)" % (qrec, a.record_out))
    else:
        print("no queued record: write %s yourself (Step 6)" % a.record_out)
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

META_RE = r'(<script type="application/json" id="report-meta">)(.*?)(</script>)'

# Text from lesson-template.html that must never reach a published lesson. Matched against the
# rendered text (comments removed, tags stripped) and report-meta; "<...>" entries against the HTML.
PLACEHOLDERS = [
    r"\bHEADLINE\b", r"\bWEEKDAY\b", r"\bMONTH D\b", r"\bMON D\b", r"\bYYYY\b", r"\bLesson [NK]\b",
    r"\bModule M\b", r"\bD days to the exam\b", r"\bN min read\b", r"\bAbout N hours\b",
    r"\bONE SENTENCE\b", r"\bONE LINE\b", r"\bCOURSE\b", r"\bMODULE TITLE\b", r"\bBOOK TITLE\b",
    r"\bpp\.[A-H]–[A-H]\b", r"\bPDF [C-H]–[C-H]\b", r"\(PDF \.\.\.\)", r"\bONLY WHEN A REPLY\b",
    r"Delete this block otherwise", r"\bYESTERDAY['’]S\b", r"\bSPACED REVIEW: FROM\b", r"\bANSWER\b",
    r"\bPLAIN-ENGLISH TITLE\b", r"\bTHE QUESTION THE MODULE\b", r"\bPRINCIPLE ONE\b",
    r"\bTHE REASONING BEFORE\b", r"\bEACH STEP ON ITS OWN\b", r"\bTHE KEY FORMULA\b",
    r"\bMEANING, UNITS\b", r"\bSHORT ORIGINAL DEFINITION\b", r"\bFORMULA OR FACT\b",
    r"\bA MISTAKE THE EXAM\b", r"\bREASON\b", r"\bQ\.\.", r"\bCFA: MM\b", r"\bp\.X\b",
    r"\bTHE ONE IDEA OR CORRECTION\b",
    r"<dt>SYMBOL</dt>", r"<dt>TERM</dt>", r'<td class="txt">SECTION</td>', r"<td>[A-H]&ndash;[A-H]</td>",
]


def masked(raw):
    """The page with every comment blanked to spaces: same offsets, so a match found here can be
    spliced into the real file without ever touching a comment (check_report.py says why)."""
    return re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), raw, flags=re.S)


def splice(raw, pattern, repl, flags=re.S):
    """Replace the first match of pattern outside comments; repl gets the match. None if no match."""
    m = re.search(pattern, masked(raw), flags)
    if not m:
        return None
    return raw[:m.start()] + repl(m) + raw[m.end():]


def page_meta(page):
    raw = open(page, encoding="utf-8").read()
    m = re.search(META_RE, masked(raw), re.S)
    if not m:
        sys.exit("no report-meta in " + page)
    return json.loads(raw[m.start(2):m.end(2)])


def long_date(date):
    x = d(date)
    return "%s, %s %d, %d" % (x.strftime("%A"), x.strftime("%B"), x.day, x.year)


def short_date(date):
    x = d(date)
    return "%s, %s %d, %d" % (x.strftime("%a"), x.strftime("%b"), x.day, x.year)


def visible_text(fragment):
    t = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    t = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", t, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())


def stamp_page(page, date):
    """Set what a script can know exactly: the dateline (date and reading time), the title element
    from report-meta's title, reading_minutes, generated_at and the Previous link. Returns notes."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import reading_minutes  # noqa: E402
    notes = []
    raw = open(page, encoding="utf-8").read()
    try:
        minutes, words = reading_minutes.count(page)
    except SystemExit as exc:
        return ["reading time not set: %s" % exc]

    m = re.search(META_RE, masked(raw), re.S)
    meta = None
    if m:
        try:
            meta = json.loads(raw[m.start(2):m.end(2)])
        except ValueError:
            meta = None
    if meta is None:
        notes.append("report-meta missing or not valid JSON: not stamped")
    else:
        meta["reading_minutes"] = minutes
        meta["generated_at"] = et_now().isoformat(timespec="seconds")
        block = "\n  " + json.dumps(meta, ensure_ascii=False, indent=2).replace("\n", "\n  ") + "\n  "
        raw = raw[:m.start(2)] + block + raw[m.end(2):]
        if isinstance(meta.get("title"), str) and meta["title"].strip():
            title = "%s — CFA Morning Lesson, %s · Logan’s Daily Newspaper" % (
                html.escape(meta["title"].strip(), quote=False), short_date(date))
            new = splice(raw, r"<title>.*?</title>", lambda _m: "<title>%s</title>" % title)
            if new is None:
                notes.append("no title element to stamp")
            else:
                raw = new

    new = splice(raw, r'(<p class="paper-dateline">)(.*?)(</p>)', lambda x: (
        x.group(1) + '\n        %s <span class="sep">&middot;</span> %d min read\n      ' % (long_date(date), minutes)
        + x.group(3)))
    if new is None:
        notes.append("no paper-dateline paragraph to stamp")
    else:
        raw = new

    index = load(INDEX, []) or []
    prev = previous_entry(index, date)
    path, _file = page_paths(date)
    link = ('<a href="%s">&larr; Previous report</a>' % rel_link(path, prev["path"]) if prev else
            '<a class="is-disabled" aria-disabled="true">&larr; Previous report</a>')
    new = splice(raw, r"<a\b[^>]*>\s*&larr; Previous report\s*</a>", lambda _m: link)
    if new is None:
        notes.append("no Previous-report link to stamp")
    else:
        raw = new

    with open(page, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(raw)
    notes.append("stamped: %s, %d min read (%d words), Previous -> %s" % (
        long_date(date), minutes, words, prev["path"] if prev else "none"))
    return notes


def lesson_problems(page, date, plan):
    """What check_report cannot know about a CFA lesson: template text left in, a headline that
    differs between report-meta and the h1, the wrong lesson number or days to the exam."""
    out = []
    raw = open(page, encoding="utf-8").read()
    seen = masked(raw)
    m = re.search(META_RE, seen, re.S)
    try:
        meta = json.loads(raw[m.start(2):m.end(2)]) if m else {}
    except ValueError:
        meta = {}
    body = re.sub(META_RE, " ", seen, flags=re.S)
    text = visible_text(body)
    meta_text = " ".join(str(meta.get(k, "")) for k in ("title", "summary", "path", "date")) + " " + \
        " ".join(str(h) for h in (meta.get("headlines") or []))
    left = []
    for pat in PLACEHOLDERS:
        where = body if pat.startswith("<") else text + " " + meta_text
        for hit in re.findall(pat, where):
            left.append(hit if isinstance(hit, str) else pat)
    if left:
        out.append("template text left in the page: %s" % ", ".join(sorted(set(left))[:12]))

    path, _file = page_paths(date)
    if meta and meta.get("path") != path:
        out.append("report-meta path is %r, should be %r" % (meta.get("path"), path))
    if meta and meta.get("timezone") != "America/New_York":
        out.append("report-meta timezone should be America/New_York")
    if meta and not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$", str(meta.get("generated_at", ""))):
        out.append("report-meta generated_at %r is not an ET timestamp" % meta.get("generated_at"))
    norm = lambda s: " ".join(str(s).replace("'", "’").split())  # noqa: E731
    h1 = re.findall(r"<h1\b[^>]*>(.*?)</h1>", body, flags=re.S)
    if len(h1) != 1:
        out.append("expected exactly one h1, found %d" % len(h1))
    elif meta and norm(visible_text(h1[0])) != norm(meta.get("title", "")):
        out.append("h1 %r differs from report-meta title %r" % (visible_text(h1[0]), meta.get("title")))

    day = plan_day(plan, date) if plan else None
    if day and day.get("lesson") is not None:
        n = day["lesson"]
        tp = re.search(r'<span class="track-progress">(.*?)</span>', body, re.S)
        track = visible_text(tp.group(1)) if tp else ""
        for where, s in (("track line", track), ("first headline", (meta.get("headlines") or [""])[0])):
            got = re.search(r"\bLesson (\d+)\b", s)
            if got and int(got.group(1)) != n:
                out.append("%s says Lesson %s; the plan says %d" % (where, got.group(1), n))
        exam = plan["exam"]["date"]
        left_days = (d(exam) - d(date)).days
        got = re.search(r"\b(\d+) days? to the exam\b", track)
        if got and int(got.group(1)) != left_days:
            out.append("track line says %s days to the exam; it is %d" % (got.group(1), left_days))
        got = re.search(r"\bModule (\d+) of (\d+)\b", track)
        mindex = modules_index()
        infos = [module_info(mindex, r["module"]) for r in day.get("reading", [])]
        infos = [i for i in infos if i]
        if got and infos and not any((i["module_num"], i["of"]) == (int(got.group(1)), int(got.group(2)))
                                     for i in infos):
            out.append("track line says Module %s of %s; tonight is %s" % (got.group(1), got.group(2), ", ".join(
                "Module %s of %s" % (i["module_num"], i["of"]) for i in infos)))

    # A miss logged today and marked taught must actually be taught on this page.
    misc = (load(MISC, {"items": []}) or {}).get("items", [])
    fresh = [x for x in misc if x.get("opened") == date and date in (x.get("taught") or [])]
    if fresh and "Correction from your reply" not in text:
        out.append("misconceptions %s were logged today as taught, but the page has no "
                   "\"Correction from your reply\" block" % [x.get("id") for x in fresh])
    for x in fresh:
        for q in re.findall(r"\bQ\d+\b", x.get("item") or ""):
            if not re.search(r"\b%s\b" % q, text):
                out.append("misconception %s (%s) is marked taught, but %s is not on the page" % (
                    x.get("id"), x.get("item"), q))
    return out


def record_problems(rec, date, day):
    out = []
    if rec is None:
        return ["lesson record missing"]
    if rec.get("date") != date:
        out.append("lesson record date %r, should be %s" % (rec.get("date"), date))
    for k in ("lesson", "modules", "concepts", "recall"):
        if k not in rec:
            out.append("lesson record lacks %r" % k)
    if day and rec.get("lesson") != day.get("lesson"):
        out.append("lesson record says lesson %r; the plan says %r" % (rec.get("lesson"), day.get("lesson")))
    recall = rec.get("recall")
    if not isinstance(recall, list) or any(not (isinstance(x, dict) and x.get("q") and x.get("a")) for x in recall):
        out.append("recall must be a list of {\"q\", \"a\", \"ref\"} items")
    elif day and day.get("kind") in STUDY_KINDS and len(recall) < 3:
        out.append("a study day's record needs 3-5 recall items on tonight's material (later lessons draw on them)")
    elif day and day.get("kind") in STUDY_KINDS and any(not str(x.get("ref") or "").strip() for x in recall):
        out.append("every recall item needs a \"ref\" (the printed page its answer comes from)")
    return out


# (target low, target high, refused above). Lesson 1 is exempt: the one-time calculator setup.
LENGTH = {"study": (800, 1100, 1250), "exam": (0, 300, 450)}
OTHER_LENGTH = (300, 600, 900)


def cmd_finish(a):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import check_report  # noqa: E402
    import reading_minutes  # noqa: E402
    m = re.search(r"(\d{4}-\d{2}-\d{2})-%s\.html$" % SLOT, a.page.replace("\\", "/"))
    if not m:
        sys.exit("page name is not YYYY-MM-DD-%s.html: %s" % (SLOT, a.page))
    date = a.date or m.group(1)
    if m.group(1) != date:
        sys.exit("page %s is not dated %s" % (a.page, date))
    plan = load(PLAN)
    day = plan_day(plan, date) if plan else None
    for note in stamp_page(a.page, date):
        print(note)
    probs = check_report.problems(a.page) + lesson_problems(a.page, date, plan)
    rec = load(a.record)
    probs += record_problems(rec, date, day)
    words = reading_minutes.count(a.page)[1]
    kind = (day or {}).get("kind")
    lo, hi, cap = LENGTH.get(kind, OTHER_LENGTH)
    long_ok = (a.long_ok or "").strip()
    if words > cap and (day or {}).get("lesson") != 1 and not long_ok:
        probs.append("%d words: %s days aim for %d-%d. Cut repetition (never an examinable outcome); or, only if "
                     "cutting would drop one, re-run with --long-ok \"why\"" % (words, kind, lo, hi))
    if probs:
        print("PAGE FAIL -- nothing recorded (fix, then run finish again):")
        for p in probs:
            print("  - " + p)
        return 1
    meta = page_meta(a.page)
    if meta.get("slot") != SLOT or meta.get("date") != date:
        sys.exit("report-meta slot/date %s %s do not match %s %s" % (meta.get("slot"), meta.get("date"), SLOT, date))
    if not lo <= words <= hi and (day or {}).get("lesson") != 1:
        print("note: %d words; %s days aim for %d-%d (recorded)" % (words, kind, lo, hi))
    if long_ok:
        rec = dict(rec)
        rec["run_note"] = ("%s (long page, %d words: %s)" % (rec.get("run_note", ""), words, long_ok)).strip()

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
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import check_report  # noqa: E402
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="cfa1-")
        if q and os.path.exists(q):
            tmp = os.path.join(tmpdir, "%s-%s.html" % (start, SLOT))
            shutil.copyfile(q, tmp)
            stamp_page(tmp, start)
            probs = check_report.problems(tmp) + lesson_problems(tmp, start, plan)
            check("queued lesson passes check_report and the lesson checks", not probs, "; ".join(probs))
        rec = load(queued_record_path(start))
        rp = record_problems(rec, start, first)
        check("first lesson's record queued", not rp, "; ".join(rp) if rp else
              "%d recall items" % len(rec.get("recall", [])))
        # The guard itself: a bare template copy must be refused, so a half-written page never goes out.
        second = (d(start) + dt.timedelta(days=1)).isoformat()
        tmpl = os.path.join(tmpdir, "%s-%s.html" % (second, SLOT))
        shutil.copyfile(TEMPLATE, tmpl)
        caught = lesson_problems(tmpl, second, plan)
        check("finish refuses an unfilled template", any(p.startswith("template text left") for p in caught),
              (caught[0][:120] if caught else "nothing caught"))
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
    p.add_argument("--date", help="the lesson date (default: today in New York); for tests")
    p = sub.add_parser("start-page")
    p.add_argument("--date")
    p.add_argument("--record-out", default="/tmp/cfa1/record.json")
    p.add_argument("--fresh", action="store_true", help="overwrite an existing page and record")
    p = sub.add_parser("snapshot")
    p.add_argument("--reason-file", required=True)
    sub.add_parser("check-plan")
    p = sub.add_parser("finish")
    p.add_argument("--page", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--date")
    p.add_argument("--long-ok", help="why a page over the length limit cannot be cut (recorded in the run note)")
    p = sub.add_parser("selftest")
    p.add_argument("--out", required=True)
    p.add_argument("--log", action="store_true", help="also append the run-log line")
    a = ap.parse_args()
    return {"today": cmd_today, "feedback": cmd_feedback, "start-page": cmd_start_page,
            "snapshot": cmd_snapshot, "check-plan": cmd_check_plan, "finish": cmd_finish,
            "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
