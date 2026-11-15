#!/usr/bin/env python3
"""CFA Morning Lesson (slot cfa1): the bookkeeping the 4:50 AM routine runs as plain commands,
so the model spends its effort on teaching.

    python3 .github/scripts/cfa1.py today --out FILE [--date YYYY-MM-DD] [--key-file FILE]
        Today's context: the plan day and its modules (course, Module M of T, sections with
        printed and PDF pages, exhibits), the previous report, the queued page, and what Logan's
        study workbook confirms. The workbook arrives encrypted (state/cfa1/workbook.enc, opened
        with CFA1_WORKBOOK_KEY); FILE gets only statuses, counts and flags. His notes go to the
        private extract /tmp/cfa1/workbook.json, which is never committed.
    python3 .github/scripts/cfa1.py start-page [--date D] [--record-out F] [--page-out F] [--fresh]
        Create the page: the queued lesson if one is waiting (and its record), else the template.
    python3 .github/scripts/cfa1.py snapshot --reason-file FILE
        Save the plan to state/cfa1/plan-history/vN.json and open version N+1 (before editing).
    python3 .github/scripts/cfa1.py check-plan
        Every module scheduled, first pass by first_pass_by, review window free, lessons in order.
    python3 .github/scripts/cfa1.py finish --page PAGE --record FILE [--date D] [--long-ok WHY] [--dry-run]
        Stamps the page (dateline, title, reading time, generated_at, Previous link), checks it
        (check_report; no template text; the refresher, today's reading and the references; every
        page number inside the modules' ranges; every exhibit real; no run of words from his notes;
        the length), and only then records the index entry, the lesson record, progress copied from
        the workbook's status fields, last-run and the run-log line. --dry-run checks only.
    python3 .github/scripts/cfa1.py selftest --out FILE [--log]
        Pre-start check: plan, queued first lesson, the guards, the workbook key and extract.

Progress comes only from the workbook's status fields (what Logan marked), never from a lesson
having been sent or a date having passed. Stdlib only. Run from the repository root.
"""

import argparse
import collections
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
CONTROL = ST + "/control.json"
INBOX = ST + "/inbox.jsonl"
CURSOR = ST + "/inbox-cursor.json"
BLOCKERS = ST + "/blockers.json"
WORKBOOK_ENC = ST + "/workbook.enc"
MODULES = "curriculum/cfa1/modules.json"
BRIEFS = "curriculum/cfa1/briefs"
EXHIBITS = "curriculum/cfa1/exhibits.json"
INDEX = "site/reports/index.json"
LAST_RUN = "state/last-run.json"
RUN_LOG = "state/run-log.jsonl"
BASE = "https://shafferusa.github.io/intelligence-terminal/"
SLOT = "cfa1"
PRIVATE_DIR = os.environ.get("CFA1_PRIVATE_DIR", "/tmp/cfa1")   # never inside the repository
STUDY_KINDS = {"study"}
# Columns he may write in. Text pre-filled down a column (the workbook's own instructions) and the
# drop-down marks below are not his writing.
PRIVATE_RE = re.compile(r"notes|instructions|teach back", re.I)
MARKS = {"Not checked", "Can explain", "Needs work", "Not started", "In progress", "Done", "Needs review"}
CALC_RE = re.compile(r"<!--CALC:START-->.*?<!--CALC:END-->", re.S)     # Lesson 1's one-time calculator setup
PART1_RE = re.compile(r"(<!--PART1:START-->)(.*?)(<!--PART1:END-->)", re.S)


# ------------------------------------------------------------------ helpers

def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save(path, obj):
    d_ = os.path.dirname(path)
    if d_:
        os.makedirs(d_, exist_ok=True)
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


def first_int(s):
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else None


def module_pages(m):
    """((printed first, printed last), (pdf first, pdf last)) for a module of modules.json."""
    a, z = m["pdf"]
    off = None
    for s in m.get("sections", []):
        p, q = first_int(s.get("printed")), (s.get("pdf") or [None])[0]
        if p is not None and q is not None:
            off = q - p
            break
    return ((a - off, z - off) if off is not None else None), (a, z)


# modules.json keeps the workbook's course labels; lessons use the books' own wording.
COURSE_DISPLAY = {"Economics prerequisite": "Prerequisite Economics",
                  "FSA prerequisite": "Prerequisite Financial Statement Analysis"}


def module_info(mods, key):
    """What a lesson needs to name a module and cite it."""
    m = mods.get(key)
    if not m:
        return None
    total = sum(1 for x in mods.values() if x.get("course") == m.get("course") and x.get("module_num") != 0)
    printed, pdf = module_pages(m)
    return {"course": COURSE_DISPLAY.get(m.get("course"), m.get("course")), "module_num": m.get("module_num"),
            "of": total, "title": m.get("title"), "book_title": m.get("book_title"),
            "printed": list(printed) if printed else None, "pdf": list(pdf),
            "brief": "%s/%s.md" % (BRIEFS, key) if os.path.exists("%s/%s.md" % (BRIEFS, key)) else None}


def exhibits_for(key):
    return ((load(EXHIBITS, {}) or {}).get("modules") or {}).get(key, [])


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


# ------------------------------------------------------------------ the workbook

def open_workbook(key_file=None):
    """(extract, None), or (None, why it is unavailable). The extract holds his notes: keep it
    out of the repository and off every page."""
    if not os.path.exists(WORKBOOK_ENC):
        return None, "no synced workbook in the repository yet (%s)" % WORKBOOK_ENC
    key = key_text(key_file)
    if not key:
        return None, "CFA1_WORKBOOK_KEY is not set in this environment"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfa1_box  # noqa: E402
    try:
        with open(WORKBOOK_ENC, "rb") as fh:
            raw = cfa1_box.open_sealed(cfa1_box.load_key(key), fh.read())
        return json.loads(raw.decode("utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, "the synced workbook could not be opened (%s)" % exc


def unit_key(by_course, course, unit):
    m = re.match(r"(?:Learning\s+)?Module\s+(\d+)\b", unit or "")
    if m:
        hit = [k for k, x in by_course.get(course, {}).items() if x.get("module_num") == int(m.group(1))]
        if len(hit) == 1:
            return hit[0]
    return "%s | %s" % (course, unit)     # PSM units keep the workbook's own name


def workbook_facts(wb, plan, date, mindex):
    """Statuses and counts from the extract, with the flags the reading must mention. No note text."""
    by_course = {}
    for k, x in mindex.items():
        by_course.setdefault(x.get("course"), {})[k] = x
    lc = (wb.get("sheets") or {}).get("Lesson Checklist") or {}
    modules = {}
    for name, m in (lc.get("by_module") or {}).items():
        course, unit = name.split(" | ", 1) if " | " in name else ("", name)
        st = m.get("status") or {}
        items = int(m.get("items") or 0)
        done, review, prog = int(st.get("Done", 0)), int(st.get("Needs review", 0)), int(st.get("In progress", 0))
        state = ("done" if items and done + review == items else
                 "in_progress" if done or review or prog else "not_started")
        modules[unit_key(by_course, course, unit)] = {
            "workbook_name": name, "state": state, "items": items, "done": done, "needs_review": review,
            "in_progress": prog, "understanding": m.get("understanding") or {}, "completed": m.get("completed") or []}
    by_date = {x["date"]: x for x in plan["days"]}
    sessions = []
    daily_rows = ((wb.get("sheets") or {}).get("Daily Plan") or {}).get("rows", [])
    boiler = boilerplate(daily_rows)
    for r in daily_rows:
        day = r.get("Date")
        if not isinstance(day, str) or day > date:
            continue
        written = sorted(his_writing(r, boiler))
        sessions.append({"date": day, "status": r.get("Session status") or "", "assignment": r.get("Assignment"),
                         "plan_modules": [x["module"] for x in (by_date.get(day) or {}).get("reading", [])],
                         "actual_hours": r.get("Actual hours"), "attempted": r.get("Attempted"),
                         "correct": r.get("Correct"), "accuracy": r.get("Accuracy"),
                         "written_in": written})
    sessions.sort(key=lambda s: s["date"])
    past = [s for s in sessions if s["date"] < date]
    done_sessions = [s for s in past if s["status"] == "Done"]
    last_done = done_sessions[-1] if done_sessions else None
    flags, behind = [], []
    for s in past:
        if s["written_in"] and s["status"] in ("", "Not started"):
            flags.append("%s: something is written (%s) but the session status is %s; read it before "
                         "deciding what was finished" % (s["date"], ", ".join(s["written_in"]),
                                                           s["status"] or "empty"))
        if isinstance(s.get("accuracy"), str) and s["accuracy"] in ("Check inputs", "Add correct"):
            flags.append("%s: the Accuracy cell says %r (Attempted and Correct do not fit)" % (s["date"], s["accuracy"]))
        for k in s["plan_modules"]:
            m = modules.get(k)
            if m is None:
                continue
            if s["status"] == "Done" and m["state"] != "done":
                flags.append("%s: the session is marked Done, but the Lesson Checklist has %d of %d items done "
                             "for %s" % (s["date"], m["done"] + m["needs_review"], m["items"], k))
            elif s["status"] != "Done" and m["state"] == "done":
                flags.append("%s: the Lesson Checklist has %s complete, but the session status is %s"
                             % (s["date"], k, s["status"] or "empty"))
            if s["status"] != "Done" and m["state"] != "done" and (d(date) - d(s["date"])).days <= 21:
                behind.append({"date": s["date"], "module": k, "session": s["status"] or "empty",
                               "checklist": "%d of %d done" % (m["done"] + m["needs_review"], m["items"])})
    refresher = None
    if last_done:
        refresher = {"basis": "session marked Done on %s" % last_done["date"], "date": last_done["date"],
                     "modules": last_done["plan_modules"]}
    else:
        done_mods = sorted(((m["completed"] or [""])[-1], k) for k, m in modules.items() if m["state"] == "done")
        if done_mods:
            refresher = {"basis": "Lesson Checklist complete (%s)" % (done_mods[-1][0] or "no completed date"),
                         "date": done_mods[-1][0] or None, "modules": [done_mods[-1][1]]}
    seen = (load(PROGRESS, {}) or {}).get("workbook_seen") or {}
    return {
        "saved_at": wb.get("saved_at"), "extracted_at": wb.get("extracted_at"),
        "changed_since_last_lesson": (wb.get("saved_at") != seen.get("saved_at")) if seen else None,
        "last_seen_saved_at": seen.get("saved_at"),
        "sessions_recent": [s for s in sessions if (d(date) - d(s["date"])).days <= 10],
        "last_done_session": last_done, "refresher": refresher, "behind": behind, "flags": flags,
        "modules_started": {k: m for k, m in modules.items() if m["state"] != "not_started"},
        "dates_with_writing": [s["date"] for s in sessions if s["written_in"]][-14:],
        "_modules_all": modules, "_sessions_all": sessions,
    }


def boilerplate(rows):
    """(column, text) pairs pre-filled down a column (the same text in 3 or more rows)."""
    c = collections.Counter((k, v) for r in rows for k, v in r.items() if isinstance(v, str))
    return {kv for kv, n in c.items() if n >= 3}


def his_writing(row, boiler):
    """{column: text} that he wrote himself in this row: notes, teach-back or instruction columns,
    minus pre-filled text and drop-down marks."""
    return {k: v for k, v in row.items()
            if isinstance(v, str) and v.strip() and PRIVATE_RE.search(k)
            and (k, v) not in boiler and v.strip() not in MARKS}


def private_texts(wb):
    """Everything he wrote in the workbook, for the leak check."""
    out = []
    for sheet in (wb.get("sheets") or {}).values():
        rows = sheet.get("rows", [])
        boiler = boilerplate(rows)
        for r in rows:
            out += [v for v in his_writing(r, boiler).values() if len(v.split()) >= 8]
    return out


def private_notes(wb, date, days=14):
    """His recent writing and marks, for the run to read and reason from. Private: it stays in
    PRIVATE_DIR and is never quoted on a page, in a record or in a commit."""
    sheets = wb.get("sheets") or {}
    lo = (d(date) - dt.timedelta(days=days)).isoformat()
    out = {"note": ("Logan's own words. Reason from his exact wording; mentioning a topic is not mastery and "
                    "confusion is not proof it is unfinished. Never quote any of this publicly."),
           "saved_at": wb.get("saved_at"), "extracted_at": wb.get("extracted_at"),
           "daily_plan": [], "lesson_checklist": [], "progress": []}
    rows = (sheets.get("Daily Plan") or {}).get("rows", [])
    boiler = boilerplate(rows)
    for r in rows:
        day = r.get("Date")
        if isinstance(day, str) and lo <= day <= date:
            w = his_writing(r, boiler)
            marks = {k: r.get(k) for k in ("Session status", "Actual hours", "Attempted", "Correct", "Accuracy")
                     if r.get(k) not in (None, "", "Not started")}
            if w or marks:
                out["daily_plan"].append({"date": day, "assignment": r.get("Assignment"), "marks": marks,
                                          "written": w})
    rows = (sheets.get("Lesson Checklist") or {}).get("rows", [])
    boiler = boilerplate(rows)
    for r in rows:
        w = his_writing(r, boiler)
        recent = isinstance(r.get("Completed date"), str) and r["Completed date"] >= lo
        if w or recent or r.get("Status") in ("In progress", "Needs review") or \
                r.get("Understanding") not in (None, "", "Not checked"):
            item = {k: r.get(k) for k in ("Course", "Module or unit", "Exact linked item", "Status",
                                           "Completed date", "Understanding")}
            if w:
                item["written"] = w
            out["lesson_checklist"].append(item)
    rows = (sheets.get("Progress") or {}).get("rows", [])
    boiler = boilerplate(rows)
    for r in rows:
        w = his_writing(r, boiler)
        if w or r.get("Teach back") not in (None, "", "Not checked"):
            out["progress"].append({"course": r.get("Course"), "teach_back": r.get("Teach back"), "written": w})
    return out


def words_of(s):
    return re.findall(r"[a-z0-9]+", str(s).lower())


def grams(words, n):
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


_PUBLIC = {}


def public_grams(n=8):
    """Word runs that are public anyway: module, section and exhibit titles and plan assignments.
    A note that names a module is not a leak when the lesson names the same module."""
    if n not in _PUBLIC:
        texts = []
        for m in (load(MODULES, {}) or {}).get("modules", []):
            t = m.get("title", "")
            texts += ["Learning Module %s %s" % (m.get("module_num"), t), "Module %s %s" % (m.get("module_num"), t),
                      m.get("book_title", "")] + [s.get("title", "") for s in m.get("sections", [])]
        texts += [x.get("assignment") or "" for x in (load(PLAN, {}) or {}).get("days", [])]
        for mod in ((load(EXHIBITS, {}) or {}).get("modules") or {}).values():
            texts += [x.get("title", "") for x in mod]
        _PUBLIC[n] = set().union(*(grams(words_of(t), n) for t in texts)) if texts else set()
    return _PUBLIC[n]


def shared_runs(text, privates, n=8):
    """Runs of n consecutive words found both in text and in one of his notes."""
    have = grams(words_of(text), n) - public_grams(n)
    hits = []
    for p in privates:
        common = grams(words_of(p), n) & have
        if common:
            hits.append(" ".join(sorted(common)[0]))
    return hits


def key_text(key_file=None):
    """The workbook key: --key-file, else CFA1_WORKBOOK_KEY, else PRIVATE_DIR/workbook.key (where
    the routine writes it when its prompt carries the key). Never print it."""
    fallback = os.path.join(PRIVATE_DIR, "workbook.key")
    if not key_file and not os.environ.get("CFA1_WORKBOOK_KEY", "").strip() and os.path.exists(fallback):
        key_file = fallback
    if key_file:
        with open(key_file, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get("CFA1_WORKBOOK_KEY", "").strip()


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
    mindex = modules_index()

    lessons = sorted((load(LESSONS, {"lessons": []}) or {}).get("lessons", []), key=lambda x: x["date"])
    before = [x for x in lessons if x["date"] < date]

    os.makedirs(PRIVATE_DIR, exist_ok=True)
    wb, why = open_workbook(a.key_file)
    facts = None
    notes_file = os.path.join(PRIVATE_DIR, "notes.json")
    if wb is not None:
        save(os.path.join(PRIVATE_DIR, "workbook.json"), wb)
        save(notes_file, private_notes(wb, date))
        facts = workbook_facts(wb, plan, date, mindex)
    else:
        for stale in (notes_file, os.path.join(PRIVATE_DIR, "workbook.json")):
            if os.path.exists(stale):
                os.remove(stale)
    workbook = {"available": wb is not None, "why_unavailable": why,
                "private_notes": notes_file if wb is not None else None,
                "private_extract": os.path.join(PRIVATE_DIR, "workbook.json") if wb is not None else None}
    if facts:
        workbook.update({k: v for k, v in facts.items() if not k.startswith("_")})
    else:
        workbook["last_confirmed"] = load(PROGRESS, {})   # statuses copied at the last lesson

    wanted = [r["module"] for r in (day or {}).get("reading", [])]
    ref_mods = ((facts or {}).get("refresher") or {}).get("modules") or []
    wanted += ref_mods + [b["module"] for b in (facts or {}).get("behind", [])]
    minfo = {k: module_info(mindex, k) for k in dict.fromkeys(wanted) if k in mindex}

    cursor = (load(CURSOR, {"last_update_id": 0}) or {}).get("last_update_id", 0) or 0
    new_msgs = [x for x in inbox_lines() if int(x.get("update_id", 0)) > int(cursor)]
    control = load(CONTROL, {"paused": False}) or {"paused": False}
    page_path, page_file = page_paths(date)
    index = load(INDEX, []) or []
    prev_entry = previous_entry(index, date)
    queued = (day or {}).get("queued")
    qrec = queued_record_path(date)
    alternates = sorted(os.path.join(QUEUE, f).replace("\\", "/") for f in os.listdir(QUEUE)
                        if f.startswith(date + "-refresher-") and f.endswith(".html")) if os.path.isdir(QUEUE) else []
    exam = plan["exam"]["date"]
    out = {
        "date": date,
        "now_et": et_now().isoformat(timespec="seconds"),
        "dow": d(date).strftime("%a"),
        "run_key": key,
        "already_done": done,
        "already_in_index": any((e.get("date"), e.get("slot")) == (date, SLOT) for e in index),
        "before_start": date < plan["start"],
        "after_exam": date > exam,
        "days_to_exam": (d(exam) - d(date)).days,
        "plan_version": plan.get("version"),
        "paused": bool(control.get("paused")),
        "today": day,
        "yesterday_plan": prev_day,
        "last_lesson": before[-1] if before else None,
        "workbook": workbook,
        "modules_info": minfo,
        "exhibits": {r["module"]: exhibits_for(r["module"]) for r in (day or {}).get("reading", [])},
        "new_telegram_messages": new_msgs,
        "inbox_cursor": cursor,
        "queued_page": queued if queued and os.path.exists(queued) else None,
        "queued_record": qrec if os.path.exists(qrec) else None,
        "queued_refresher_alternates": alternates,
        "page": {"file": page_file, "path": page_path, "url": BASE + page_path, "exists": os.path.exists(page_file)},
        "previous_report": ({"date": prev_entry.get("date"), "slot": prev_entry.get("slot"),
                             "href": rel_link(page_path, prev_entry["path"])} if prev_entry else None),
        "blockers": [b for b in (load(BLOCKERS, {"items": []}) or {}).get("items", []) if b.get("status") == "open"],
    }
    save(a.out, out)
    t = day or {}
    print("date %s (%s) run_key %s already_done=%s plan v%s" % (date, out["dow"], key, done, out["plan_version"]))
    print("today: lesson %s, kind %s, %s" % (t.get("lesson"), t.get("kind"), t.get("assignment")))
    if facts:
        print("workbook: saved %s, synced %s, changed since last lesson: %s" % (
            facts["saved_at"], facts["extracted_at"], facts["changed_since_last_lesson"]))
        print("  last session marked Done: %s; refresher candidate: %s" % (
            (facts["last_done_session"] or {}).get("date"), json.dumps(facts["refresher"])))
        print("  flags: %d, behind: %d, dates with writing (last 14): %s" % (
            len(facts["flags"]), len(facts["behind"]), facts["dates_with_writing"]))
        print("  his recent notes and marks (private, never quote or commit): %s" % notes_file)
    else:
        print("workbook UNAVAILABLE: %s -- use workbook.last_confirmed and say so in one line" % why)
    print("telegram messages (optional input): %d" % len(new_msgs))
    print("queued page: %s, record: %s, refresher alternates: %s" % (out["queued_page"], out["queued_record"],
                                                                      alternates))
    print("previous report: %s" % ((out["previous_report"] or {}).get("href")))
    print("context written to %s" % a.out)
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
    page_file = a.page_out or page_paths(date)[1]
    queued = day.get("queued")
    if os.path.exists(page_file) and not a.fresh:
        print("page exists, left as is: %s (--fresh starts it over)" % page_file)
    else:
        src = queued if queued and os.path.exists(queued) else TEMPLATE
        os.makedirs(os.path.dirname(page_file) or ".", exist_ok=True)
        shutil.copyfile(src, page_file)
        print("%s -> %s (%s)" % (src, page_file, "queued lesson: change only what the procedure lists"
                                 if src != TEMPLATE else "template: replace every placeholder"))
    qrec = queued_record_path(date)
    if os.path.exists(qrec):
        if os.path.exists(a.record_out) and not a.fresh:
            print("record exists, left as is: %s" % a.record_out)
        else:
            os.makedirs(os.path.dirname(a.record_out) or ".", exist_ok=True)
            shutil.copyfile(qrec, a.record_out)
            print("%s -> %s (the queued lesson's record: update workbook, run_note)" % (qrec, a.record_out))
    else:
        print("no queued record: write %s yourself" % a.record_out)
    return 0


def use_refresher(page, record, date, module):
    """Put a pre-written alternate Part 1 (queue/DATE-refresher-MODULE.html) into a queued lesson,
    and point the record and the "Refresher:" headline at it. Returns a one-line note."""
    frag = "%s/%s-refresher-%s.html" % (QUEUE, date, module)
    if not os.path.exists(frag):
        sys.exit("no alternate refresher %s" % frag)
    part = open(frag, encoding="utf-8").read().strip()
    raw = open(page, encoding="utf-8").read()
    m = PART1_RE.search(raw)
    if not m:
        sys.exit("%s has no PART1:START / PART1:END markers" % page)
    raw = raw[:m.start(2)] + "\n" + part + "\n      " + raw[m.end(2):]
    h3 = re.search(r"<h3\b[^>]*>(.*?)</h3>", part, re.S)
    topic = re.sub(r"^1 · ", "", visible_text(h3.group(1))) if h3 else ""
    mm = re.search(META_RE, masked(raw), re.S)
    meta = json.loads(raw[mm.start(2):mm.end(2)])
    meta["headlines"] = [topic if str(h).startswith("Refresher:") and topic else h for h in meta.get("headlines", [])]
    block = "\n  " + json.dumps(meta, ensure_ascii=False, indent=2).replace("\n", "\n  ") + "\n  "
    raw = raw[:mm.start(2)] + block + raw[mm.end(2):]
    with open(page, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(raw)
    rec = load(record) or {}
    rec["refresher_modules"] = [module]
    side = load(frag[:-len(".html")] + ".json")
    if side and side.get("refresher_topics"):
        rec["refresher_topics"] = side["refresher_topics"]
    save(record, rec)
    return "Part 1 is now %s (%s); record refresher_modules = [%s]" % (frag, topic, module)


def cmd_use_refresher(a):
    print(use_refresher(a.page, a.record, a.date or today_et(), a.module))
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


# ------------------------------------------------------------------ page checks

META_RE = r'(<script type="application/json" id="report-meta">)(.*?)(</script>)'

# Text from lesson-template.html that must never reach a published lesson. Matched against the
# rendered text (comments removed, tags stripped) and report-meta; "<...>" entries against the HTML.
PLACEHOLDERS = [
    r"\bHEADLINE\b", r"\bWEEKDAY\b", r"\bMONTH D\b", r"\bMON D\b", r"\bYYYY\b", r"\bLesson N\b",
    r"\bModule M\b", r"\bD days to the exam\b", r"\bN min read\b", r"\bONE SENTENCE\b", r"\bCOURSE\b",
    r"\bMODULE TITLE\b", r"\bBOOK TITLE\b", r"\bpp\.[A-H]–[A-H]\b", r"\bPDF [C-H]–[C-H]\b", r"\bPDF Y\b",
    r"\bp\.X\b", r"\bWORKBOOK FACTS\b", r"\bREFRESHER TOPIC\b", r"\bREFRESHER PRINCIPLE\b",
    r"\bSHORT ORIGINAL DEFINITION\b", r"\bFORMULA OR FACT\b", r"\bONLY WHEN YOUR NOTES\b",
    r"Delete this block otherwise", r"\bTODAY['’]S TOPIC\b", r"\bTHE QUESTION THE MODULE\b",
    r"\bPRINCIPLE\.", r"\bTHE IDEA, WHY\b", r"\bA BRIEF EVERYDAY\b", r"\bTHE KEY FORMULA\b",
    r"\bMEANING AND UNITS\b", r"\bASSUMPTION OR EXCEPTION\b", r"\bExhibit N\b", r"\bCAPTION\b",
    r"\bWHY IT IS WORTH\b", r"\bPASSAGE\b", r"\bTHE ONE IDEA\b", r"\bLABELLED AS ONE\b",
    r"\bPLAN CHANGE, IF ANY\b", r"Delete if none", r"Delete this block if none helps",
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


def article(raw, calc=False):
    """The lesson body with comments blanked, without Lesson 1's one-time calculator setup unless
    calc is set (the setup cites TI's guidebook, not the curriculum)."""
    if not calc:
        raw = CALC_RE.sub(" ", raw)
    m = re.search(r'<article class="lesson-body[^"]*"[^>]*>(.*?)</article>', masked(raw), re.S)
    return m.group(1) if m else ""


def reading_words(raw):
    """Words of the morning reading: Where you are, the refresher and today's teaching. The
    References section and Lesson 1's calculator setup are extra. Counted the way reading_minutes.py
    counts."""
    body = article(raw)
    at = re.search(r"<h3\b[^>]*>\s*References\b", body)
    if at:
        nxt = re.search(r"<h3\b", body[at.end():])
        body = body[:at.start()] + (body[at.end() + nxt.start():] if nxt else "")
    body = re.sub(r'<p class="expr-spoken[^"]*">.*?</p>', "", body, flags=re.S)
    body = re.sub(r"</?su[bp]\b[^>]*>", "", body)
    return len(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())


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
    """What check_report cannot know: template text left in, the two parts and the references,
    no quiz, h1 = report-meta title, lesson number, Module M of T, days to the exam."""
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
    h3 = [visible_text(x) for x in re.findall(r"<h3\b[^>]*>(.*?)</h3>", body, flags=re.S)]
    if (day or {}).get("kind") != "exam":
        if "Where you are" not in text:
            out.append('the "Where you are" box is missing (what the workbook confirms, or that it is unavailable '
                       'or unchanged)')
        if not any(re.match(r"1 · Refresher\b", x) for x in h3):
            out.append('part 1 missing: an h3 starting "1 · Refresher" (yesterday\'s or the latest completed study)')
        if not any(re.match(r"2 · Today\b", x) for x in h3):
            out.append('part 2 missing: an h3 starting "2 · Today" (the teaching of today\'s material)')
        if not any(x.startswith("References") for x in h3):
            out.append('references missing: an h3 "References" with the book, module, sections and pages')
    if re.search(r'<details class="check"', body) or re.search(r"Recall first|Tonight’s checklist", text):
        out.append("no quizzes, answer keys or homework checklists: the reading explains, it does not test")
    if day and day.get("lesson") is not None:
        n = day["lesson"]
        tp = re.search(r'<span class="track-progress">(.*?)</span>', body, re.S)
        track = visible_text(tp.group(1)) if tp else ""
        for where, s in (("track line", track), ("first headline", (meta.get("headlines") or [""])[0])):
            got = re.search(r"\bLesson (\d+)\b", s)
            if got and int(got.group(1)) != n:
                out.append("%s says Lesson %s; the plan says %d" % (where, got.group(1), n))
        left_days = (d(plan["exam"]["date"]) - d(date)).days
        got = re.search(r"\b(\d+) days? to the exam\b", track)
        if got and int(got.group(1)) != left_days:
            out.append("track line says %s days to the exam; it is %d" % (got.group(1), left_days))
        got = re.search(r"\bModule (\d+) of (\d+)\b", track)
        mindex = modules_index()
        infos = [i for i in (module_info(mindex, r["module"]) for r in day.get("reading", [])) if i]
        if got and infos and not any((i["module_num"], i["of"]) == (int(got.group(1)), int(got.group(2)))
                                     for i in infos):
            out.append("track line says Module %s of %s; today is %s" % (got.group(1), got.group(2), ", ".join(
                "Module %s of %s" % (i["module_num"], i["of"]) for i in infos)))
    return out


# Printed pages: "p.81", "pp.79–81", "pages 67–136", never the "p." of "PDF p.87". PDF pages: "PDF 89".
PRINTED_RE = re.compile(r"(?<!PDF )(?<!PDF\xa0)\b(?:pp?\.|pages?\s)\s?(\d{1,4})(?:\s?[–-]\s?(\d{1,4}))?")
PDF_RE = re.compile(r"\bPDF\s(?:pp?\.\s?|pages?\s)?(\d{1,4})(?:\s?[–-]\s?(\d{1,4}))?")
CELL_RE = re.compile(r"<td>\s*(\d{1,4})(?:\s*(?:&ndash;|–|-)\s*(\d{1,4}))?\s*</td>")
EXHIBIT_RE = re.compile(r"\bExhibits?\s+(\d{1,3}(?:\s*(?:,|and|–|-|&)\s*\d{1,3})*)")


def reference_problems(page, keys):
    """Every page number in the reading lies inside a module the record names, every exhibit it
    names exists in those modules, and the References table's page cells are inside them too."""
    out = []
    mindex = modules_index()
    keys = [k for k in dict.fromkeys(keys) if k in mindex]
    raw = open(page, encoding="utf-8").read()
    body = article(raw)
    text = visible_text(body)
    at = re.search(r"<h3\b[^>]*>\s*References\b", body)
    refs = body[at.start():] if at else ""
    found = [m for r_ in (PRINTED_RE, PDF_RE) for m in r_.finditer(text)] + list(CELL_RE.finditer(refs))
    exhibits = list(EXHIBIT_RE.finditer(text))
    if not keys:
        return (["the page cites pages or exhibits, but the record names no known module to check them "
                 "against (modules, refresher_modules, cited_modules)"] if found or exhibits else [])
    printed, pdf = [], []
    for k in keys:
        pr, pd_ = module_pages(mindex[k])
        if pr:
            printed.append(pr)
        pdf.append(pd_)
    inside = lambda n, rr: any(a <= n <= z for a, z in rr)  # noqa: E731
    bad = set()
    for m in PRINTED_RE.finditer(text):
        bad |= {"p.%s" % g for g in m.groups() if g and not inside(int(g), printed)}
    for m in PDF_RE.finditer(text):
        bad |= {"PDF %s" % g for g in m.groups() if g and not inside(int(g), pdf)}
    for m in CELL_RE.finditer(refs):
        bad |= {"table cell %s" % g for g in m.groups() if g and not inside(int(g), printed + pdf)}
    if bad:
        out.append("page numbers outside %s (printed %s, PDF %s): %s" % (
            ", ".join(keys), printed, pdf, ", ".join(sorted(bad))))
    real = {x["n"] for k in keys for x in exhibits_for(k)}
    for m in exhibits:
        for n in re.findall(r"\d+", m.group(1)):
            if int(n) not in real:
                out.append("Exhibit %s does not exist in %s (curriculum/cfa1/exhibits.json)" % (n, ", ".join(keys)))
    return out


def cited_keys(rec):
    """Modules whose pages a lesson may cite: today's, the refresher's, and any it names in passing."""
    rec = rec or {}
    return list(dict.fromkeys((rec.get("modules") or []) + (rec.get("refresher_modules") or [])
                              + (rec.get("cited_modules") or [])))


def record_problems(rec, date, day):
    out = []
    if rec is None:
        return ["lesson record missing"]
    if rec.get("date") != date:
        out.append("lesson record date %r, should be %s" % (rec.get("date"), date))
    for k in ("lesson", "kind", "modules", "refresher_modules", "workbook", "topics", "references", "run_note"):
        if k not in rec:
            out.append("lesson record lacks %r" % k)
    if day and rec.get("lesson") != day.get("lesson"):
        out.append("lesson record says lesson %r; the plan says %r" % (rec.get("lesson"), day.get("lesson")))
    if rec.get("workbook") not in ("updated", "unchanged", "unavailable"):
        out.append('record "workbook" must be "updated", "unchanged" or "unavailable"')
    mindex = modules_index()
    for k in cited_keys(rec):
        if k not in mindex:
            out.append("record names unknown module %r" % k)
    planned = [r["module"] for r in (day or {}).get("reading", [])]
    if day and day.get("kind") in STUDY_KINDS and sorted(rec.get("modules") or []) != sorted(planned) \
            and not rec.get("plan_changes"):
        out.append("record modules %s differ from the plan's %s; a change needs plan_changes and a new plan "
                   "version" % (rec.get("modules"), planned))
    return out


# Words in the reading (Lesson 1's calculator setup not counted): (target low, target high,
# refused above). --long-ok lifts the refusal to LONG_CAP.
LENGTH = {"study": (500, 800, 1000), "exam": (0, 300, 450)}
OTHER_LENGTH = (300, 600, 800)
LONG_CAP = 1300


def record_progress(facts, wb, date):
    now = et_now().isoformat(timespec="seconds")
    progress = {
        "note": ("Statuses only, copied from Logan's study workbook when a lesson is recorded. His notes stay "
                 "private (they travel encrypted in workbook.enc and are never written here)."),
        "updated": now,
        "workbook_seen": {"saved_at": wb.get("saved_at"), "extracted_at": wb.get("extracted_at"), "lesson_date": date},
        "modules": {k: {"state": m["state"], "items": m["items"], "done": m["done"] + m["needs_review"]}
                    for k, m in sorted(facts["_modules_all"].items()) if m["state"] != "not_started"},
        "sessions": [{"date": s["date"], "status": s["status"]} for s in facts["_sessions_all"] if s["status"]],
    }
    save(PROGRESS, progress)


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
    rec = load(a.record)
    probs = check_report.problems(a.page) + lesson_problems(a.page, date, plan) + record_problems(rec, date, day)
    if rec:
        probs += reference_problems(a.page, cited_keys(rec))
    raw = open(a.page, encoding="utf-8").read()
    everything = html.unescape(re.sub(r"<[^>]+>", " ", raw))      # report-meta and comments included
    shown = visible_text(masked(raw))
    rec_text = json.dumps(rec or {}, ensure_ascii=False)
    key = key_text(a.key_file)
    if key and (key in raw or key in rec_text):
        probs.append("the workbook key is written in the page or the record: remove it")
    wb, why = open_workbook(a.key_file)
    facts = workbook_facts(wb, plan, date, modules_index()) if wb is not None else None
    if wb is not None:
        privates = private_texts(wb)
        leaked = shared_runs(everything, privates) + shared_runs(rec_text, privates)
        if leaked:
            probs.append("words copied from his private notes (make it a learning point in your own words; never "
                         "quote his notes): " + "; ".join(sorted(set(leaked))[:5]))
        expect = "unchanged" if facts["changed_since_last_lesson"] is False else "updated"
        if rec and rec.get("workbook") != expect:
            probs.append('record "workbook" is %r, but the synced workbook is %s (saved %s; the last lesson saw %s)'
                         % (rec.get("workbook"), expect, facts["saved_at"], facts["last_seen_saved_at"]))
    elif rec and rec.get("workbook") != "unavailable":
        probs.append('the workbook is unavailable here (%s), so the record must say "workbook": "unavailable"' % why)
    state = (rec or {}).get("workbook")
    if state == "unavailable" and not re.search(r"unavailable|not available|could not (?:be )?(?:read|opened)",
                                                 shown, re.I):
        probs.append('the workbook was unavailable: say so in one line in "Where you are"')
    if state == "unchanged" and not re.search(r"unchanged|no (?:new )?(?:update|change|entr|note)|not (?:been )?"
                                               r"(?:changed|updated|saved)|hasn.t (?:changed|been)", shown, re.I):
        probs.append('the workbook has not changed since the last lesson: say so in one line in "Where you are"')
    words = reading_words(raw)
    kind = (day or {}).get("kind")
    lo, hi, cap = LENGTH.get(kind, OTHER_LENGTH)
    long_ok = (a.long_ok or "").strip()
    limit = LONG_CAP if long_ok else cap
    if words > limit:
        probs.append("%d words in the reading: %s days aim for %d-%d. Cut repetition, never an essential "
                     "explanation; if one truly needs the room, re-run with --long-ok \"why\" (up to %d words)" % (
                         words, kind, lo, hi, LONG_CAP))
    if probs:
        print("PAGE FAIL -- nothing recorded (fix, then run finish again):")
        for p in probs:
            print("  - " + p)
        return 1
    meta = page_meta(a.page)
    if meta.get("slot") != SLOT or meta.get("date") != date:
        sys.exit("report-meta slot/date %s %s do not match %s %s" % (meta.get("slot"), meta.get("date"), SLOT, date))
    calc_words = len(visible_text(" ".join(CALC_RE.findall(raw))).split())
    if not lo <= words <= hi:
        print("note: %d words in the reading; %s days aim for %d-%d" % (words, kind, lo, hi))
    if a.dry_run:
        print("DRY RUN OK: every check passed (%d words in the reading%s); nothing recorded" % (
            words, ", plus %d in the one-time calculator setup" % calc_words if calc_words else ""))
        return 0
    rec = dict(rec)
    if long_ok and words > cap:
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
    rec["path"] = meta["path"]
    rec["published"] = now_utc
    ls.append(rec)
    lessons["lessons"] = sorted(ls, key=lambda x: x["date"])
    save(LESSONS, lessons)

    if wb is not None:
        record_progress(facts, wb, date)
    if rec.get("inbox_through"):
        cur = int((load(CURSOR, {"last_update_id": 0}) or {}).get("last_update_id", 0) or 0)
        save(CURSOR, {"last_update_id": max(cur, int(rec["inbox_through"])), "updated": now_utc})

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
    print("recorded lesson %s for %s; index entry %s; progress %s; last-run %s success" % (
        rec.get("lesson"), date, "replaced" if hit else "prepended",
        "copied from the workbook" if wb is not None else "unchanged (workbook unavailable)", key))
    print("live URL: %s%s" % (BASE, meta["path"]))
    return 0


# ------------------------------------------------------------------ selftest

def fill_runtime_slot(page):
    """What the routine does to a queued page at run time, for the self-test: the "Where you are"
    box is written from that morning's workbook facts."""
    raw = open(page, encoding="utf-8").read()
    raw = re.sub(r"WORKBOOK FACTS:[^<]*", "Self-test stand-in for the workbook facts the routine writes.", raw)
    with open(page, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(raw)


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
        rec = load(queued_record_path(start))
        if q and os.path.exists(q):
            tmp = os.path.join(tmpdir, "%s-%s.html" % (start, SLOT))
            shutil.copyfile(q, tmp)
            caught = lesson_problems(tmp, start, plan)
            check("the queued lesson's run-time slot is guarded", any("WORKBOOK FACTS" in x for x in caught),
                  "finish refuses it until the routine writes the workbook facts")
            fill_runtime_slot(tmp)
            stamp_page(tmp, start)
            probs = check_report.problems(tmp) + lesson_problems(tmp, start, plan)
            if rec:
                probs += reference_problems(tmp, cited_keys(rec))
            words = reading_words(open(tmp, encoding="utf-8").read())
            lo, hi, cap = LENGTH["study"]
            if words > cap:
                probs.append("%d words in the reading (limit %d)" % (words, cap))
            check("queued lesson passes check_report, the lesson checks and the page/exhibit checks", not probs,
                  "; ".join(probs) if probs else "%d words in the reading" % words)
            bad = os.path.join(tmpdir, "bad", "%s-%s.html" % (start, SLOT))
            os.makedirs(os.path.dirname(bad))
            raw = open(tmp, encoding="utf-8").read()
            at = raw.index("<h3>2 &middot; Today")
            raw = raw[:at] + "<p>See printed p.999 (PDF 998) and Exhibit 99.</p>\n      " + raw[at:]
            with open(bad, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(raw)
            caught = " ".join(reference_problems(bad, cited_keys(rec)))
            check("finish refuses invented pages and exhibits", all(s in caught for s in ("p.999", "PDF 998",
                                                                                         "Exhibit 99")), caught[:160])
            for alt in sorted(f for f in os.listdir(QUEUE) if f.startswith(start + "-refresher-") and
                              f.endswith(".html")):
                module = alt[len(start + "-refresher-"):-len(".html")]
                page2 = os.path.join(tmpdir, "alt", "%s-%s.html" % (start, SLOT))
                os.makedirs(os.path.dirname(page2), exist_ok=True)
                rec2 = os.path.join(tmpdir, "alt", "record.json")
                shutil.copyfile(tmp, page2)
                save(rec2, rec or {})
                note = use_refresher(page2, rec2, start, module)
                r2 = load(rec2)
                probs = check_report.problems(page2) + lesson_problems(page2, start, plan) + \
                    reference_problems(page2, cited_keys(r2))
                words = reading_words(open(page2, encoding="utf-8").read())
                if words > cap:
                    probs.append("%d words in the reading (limit %d)" % (words, cap))
                check("alternate refresher %s fits and passes" % module, not probs,
                      "; ".join(probs) if probs else "%s; %d words" % (note.split(";")[0], words))
        rp = record_problems(rec, start, first)
        check("first lesson's record queued", not rp, "; ".join(rp) if rp else "ok")
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
        ex = load(EXHIBITS)
        check("exhibit index loads", bool(ex and ex.get("modules")),
              "%d modules" % len((ex or {}).get("modules") or {}))
        wb, why = open_workbook(a.key_file)
        if wb is None:
            check("study workbook readable (key and synced extract)", False, why)
        else:
            facts = workbook_facts(wb, plan, start, modules_index())
            check("study workbook readable (key and synced extract)", True,
                  "saved %s, synced %s; %d sessions with a status, writing on %d dates, %d modules started"
                  % (wb.get("saved_at"), wb.get("extracted_at"),
                     sum(1 for s in facts["_sessions_all"] if s["status"]), len(facts["dates_with_writing"]),
                     len(facts["modules_started"])))
        planted = "this sentence stands in for a private nightly note written in the workbook"
        check("leak check catches copied note text", bool(shared_runs("Lesson text: " + planted, [planted])))
    check("modules.json loads", load(MODULES) is not None)
    for f in (PROGRESS, LESSONS, BLOCKERS, CURSOR, CONTROL):
        check("%s loads" % f, load(f) is not None)
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
    p.add_argument("--key-file", help="read the workbook key from a file (local tests) instead of the environment")
    p = sub.add_parser("start-page")
    p.add_argument("--date")
    p.add_argument("--record-out", default="/tmp/cfa1/record.json")
    p.add_argument("--page-out", help="write the page here instead of site/reports (self-test rehearsal)")
    p.add_argument("--fresh", action="store_true", help="overwrite an existing page and record")
    p = sub.add_parser("use-refresher")
    p.add_argument("--page", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--module", required=True)
    p.add_argument("--date")
    p = sub.add_parser("snapshot")
    p.add_argument("--reason-file", required=True)
    sub.add_parser("check-plan")
    p = sub.add_parser("finish")
    p.add_argument("--page", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--date")
    p.add_argument("--long-ok", help="why a page over the length limit cannot be cut (recorded in the run note)")
    p.add_argument("--dry-run", action="store_true", help="run every check, record nothing")
    p.add_argument("--key-file", help="read the workbook key from a file (local tests)")
    p = sub.add_parser("selftest")
    p.add_argument("--out", required=True)
    p.add_argument("--log", action="store_true", help="also append the run-log line")
    p.add_argument("--key-file", help="read the workbook key from a file (local tests)")
    a = ap.parse_args()
    return {"today": cmd_today, "start-page": cmd_start_page, "use-refresher": cmd_use_refresher,
            "snapshot": cmd_snapshot,
            "check-plan": cmd_check_plan, "finish": cmd_finish, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
