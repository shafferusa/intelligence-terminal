"""The forward check of the owner's FX booking (FX_CCS_PLAN rev 2, section 9 item 5, FX-C-12; release 1.0.6.1, package P-D; fixer round 2:
V1061R1-01, V1061R1-03, V1061R1-04).

    py -3.12 tests\\terminal_fx_forward_check.py --run <C1.db> --control <C0.db> --report <dir> [--start <ISO ET>] [--until <ISO ET>]

C1 is the dry run's copy (the trades booked), C0 its control (the backup taken before the run).  The script never touches them: it makes
a working copy of each (sqlite3 backup, source opened mode=ro) under ``--work`` (default: a new folder next to the report) and deletes
the working copies at the end (``--keep`` keeps them).

ONE SET OF INPUTS (V1061R1-01).  C1's server kept fetching while the dry run served it (inside the equity session in the 08:00-16:30 ET
booking window), so the copies' own last quotes, closes and curves differ for reasons that are not the booking: held flat per copy, the
owner's TRS underlyings, the CDX spread inputs and the futures would move the control and the run apart and the diff would fail (or prove
nothing).  Before the first tick the control's working copy therefore receives the run copy's input tables (``INPUT_TABLES``: quote_cache,
curve_points, marks, dividends, debt_prices, finra_short) - both copies are driven from ONE set of inputs - and the report lists what
differed before the alignment.

For each copy it builds the REAL runtime (``server.build_runtime``: every module bound exactly as ``serve`` binds it) on a FakeClock,
offline, and
- holds the inputs FLAT, labelled TEST: before every tick each cached quote is re-put with the tick's time and its last price (so the
  marks are fresh, never moved); on each BUSINESS day (the New York calendar: never a Saturday, a Sunday or a holiday, when no source
  publishes - V1061R1-03) at its first tick from ``HOLD_RATES_ET`` (08:00 ET, when the day's overnight prints and curves are out), the last
  daily overnight prints (a monthly series stays as it is), the last term-curve grids and the last Treasury par curve are recorded again
  under the previous business day (the values of the last observation, source "held flat (TEST)"); every such re-dating is listed in the
  report (``held_inputs``);
- ticks the lifecycle through every scheduled job time of each day (00:15 .. 23:30 ET, the 17:05 VM, the 00:45 swap events, the
  10:00-16:30 settlement funding, 06:00 the late-event check) from ``--start`` (default: the booking instant found in C1, else now on
  the copy) to ``--until`` (default: 08:00 ET on the day after the swap's start date S, or 2 business days when the run booked no swap);
- prints the new contracts' events: the START PvP of a cross-currency swap with its cash per currency, the net it must come to (the
  quote's ``net_start_flows``: USD minus the conversion spread, the "net USD -40.00 class"; 0 in the foreign currency), its funding
  source and the settled cash of the account and of Treasury after it (V1061R1-04), the conversion's settlement, the first accruals of
  both legs, the first VM on the CSA exposure, which job settled the tagged rows;
- diffs every PRE-EXISTING contract's job results and rows between the two copies since the fork (the control's last command; a job the
  run copy's server ran before the booking is then compared with the control's catch-up of it): ``otc_contracts`` values, ``otc_vm``,
  ``otc_resets``, their journal lines, the jobs_done rows of their keys.

Exit 0 when the control diff is empty and every check passes, 1 otherwise; the report (JSON + text) is written either way.  There is
no fixture fallback: a copy that cannot be driven fails the check (FX_CCS_PLAN 9 item 5).
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
FINSIM_DIR = os.path.dirname(HERE)
if FINSIM_DIR not in sys.path:
    sys.path.insert(0, FINSIM_DIR)

TEST_LABEL = "held flat (TEST)"
#: the clock times (ET) ticked every day: housekeeping, settlement / accruals, the coupon / swap events and their late check, the
#: funding window, the closes, the VM and confirmations, the sweep, the snapshot, the evening
TICK_TIMES = ("00:15", "00:30", "00:40", "00:45", "00:50", "01:00", "06:00", "06:05", "07:00", "08:00", "09:30", "10:00", "10:05", "11:00", "12:00",
              "14:00", "16:00", "16:20", "16:25", "16:31", "17:00", "17:05", "17:10", "17:30", "18:00", "20:00", "23:30")
UNTOUCHED_DEFAULT = ("CDX-000001", "CDX-000002", "SWP-000001", "SWP-000002", "SWP-000003", "SWP-000004", "SWP-000005", "SWP-000006")
ID_RE = re.compile(r"\b(?:[A-Z]{1,4}-\d{6}|\d{9,})\b")
#: V1061R1-01: the market-input tables the control's working copy receives from the run copy's before the first tick
INPUT_TABLES = ("quote_cache", "curve_points", "marks", "dividends", "debt_prices", "finra_short")
#: V1061R1-03: the held overnight prints and curves are re-dated once per business day, at its first tick from this time (ET)
HOLD_RATES_ET = "08:00"
#: a held series counts as a daily print when its last observation is at most this many days before the date it is re-dated to
DAILY_MAX_GAP_DAYS = 10
EXAMPLES = 12


class _Log:
    def __init__(self):
        self.records = []

    def _add(self, lvl, msg, *a):
        try:
            self.records.append((lvl, msg % a if a else msg))
        except Exception:   # noqa: BLE001
            self.records.append((lvl, str(msg)))

    def debug(self, m, *a, **k):
        pass

    def info(self, m, *a, **k):
        self._add("INFO", m, *a)

    def warning(self, m, *a, **k):
        self._add("WARNING", m, *a)

    def error(self, m, *a, **k):
        self._add("ERROR", m, *a)

    def exception(self, m, *a, **k):
        self._add("EXCEPTION", m, *a)


def _uri(path: str, mode: str) -> str:
    return f"file:{os.path.abspath(path).replace(os.sep, '/')}?mode={mode}"


def backup(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    s = sqlite3.connect(_uri(src, "ro"), uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


@contextlib.contextmanager
def ro(path: str):
    """A read-only connection (mode=ro) that is CLOSED at the end of the ``with`` (a bare sqlite3 connection's ``with`` only commits)."""
    c = sqlite3.connect(_uri(path, "ro"), uri=True)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def rows(conn, sql, params=()) -> List[dict]:
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql, params).fetchall()]


def has_col(conn, table, col) -> bool:
    return col in {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def money(c) -> str:
    """Cents as a signed amount with thousands separators: 222696000 -> "2,226,960.00", -4000 -> "-40.00"."""
    c = int(c or 0)
    return ("-" if c < 0 else "") + f"{abs(c) // 100:,}.{abs(c) % 100:02d}"


# ---- one set of inputs (V1061R1-01) --------------------------------------------------------------------------------------------------------
def _cols(conn, schema: str, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall()]


def _pk(conn, schema: str, table: str) -> List[str]:
    info = conn.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall()
    return [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]


def _brief(table: str, cols: List[str], row) -> Optional[str]:
    """The value a reader compares: a cached quote's last / mid, a curve point's or a mark's value."""
    if row is None:
        return None
    r = dict(zip(cols, row))
    if table == "quote_cache":
        try:
            rec = json.loads(r.get("record_json") or "{}")
        except ValueError:
            return None
        return str(rec.get("last") if rec.get("last") is not None else rec.get("mid"))
    for k in ("value", "price", "amount", "eod", "short_volume"):
        if k in r:
            return str(r[k])
    return None


def seed_inputs(src: str, dst: str, tables=INPUT_TABLES) -> Dict[str, dict]:
    """V1061R1-01: replace the control working copy's (``dst``) market-input tables with the run working copy's (``src``), so both copies are
    driven from ONE set of inputs, and return, per table, what differed before: the rows only in the run copy, only in the control, and the
    rows of the same key with other values (counts, and up to ``EXAMPLES`` keys with the compared value on each side)."""
    out: Dict[str, dict] = {}
    d = sqlite3.connect(_uri(dst, "rw"), uri=True)
    try:
        d.execute("ATTACH DATABASE ? AS src", (_uri(src, "ro"),))
        for t in tables:
            sc, dc = _cols(d, "src", t), _cols(d, "main", t)
            if not sc or not dc:
                out[t] = {"skipped": "no such table in " + ("the run copy" if not sc else "the control")}
                continue
            cols = [c for c in dc if c in sc]
            key = [c for c in _pk(d, "main", t) if c in cols] or cols
            kix = [cols.index(k) for k in key]
            ql = ", ".join(f'"{c}"' for c in cols)

            def load(schema):
                return {tuple(r[i] for i in kix): tuple(r) for r in d.execute(f'SELECT {ql} FROM {schema}."{t}"').fetchall()}
            run_rows, ctl_rows = load("src"), load("main")
            only_run = sorted((k for k in run_rows if k not in ctl_rows), key=str)
            only_ctl = sorted((k for k in ctl_rows if k not in run_rows), key=str)
            differ = sorted((k for k in run_rows if k in ctl_rows and run_rows[k] != ctl_rows[k]), key=str)
            rep = {"rows_run": len(run_rows), "rows_control": len(ctl_rows), "only_run": len(only_run), "only_control": len(only_ctl), "differ": len(differ)}
            if only_run:
                rep["only_run_keys"] = [list(k) for k in only_run[:EXAMPLES]]
            if only_ctl:
                rep["only_control_keys"] = [list(k) for k in only_ctl[:EXAMPLES]]
            if differ:
                rep["differ_examples"] = [{"key": list(k), "control": _brief(t, cols, ctl_rows[k]), "run": _brief(t, cols, run_rows[k])} for k in differ[:EXAMPLES]]
            d.execute(f'DELETE FROM main."{t}"')
            d.execute(f'INSERT INTO main."{t}" ({ql}) SELECT {ql} FROM src."{t}"')
            out[t] = rep
        d.commit()
        d.execute("DETACH DATABASE src")
    finally:
        d.close()
    return out


def fork_ts(control_db: str) -> int:
    """The control's last command: every row either copy wrote after it is compared (both copies hold the same rows up to it)."""
    with ro(control_db) as c:
        r = c.execute("SELECT MAX(COALESCE(finished_ts, started_ts)) FROM commands").fetchone()
    return int(r[0]) if r is not None and r[0] is not None else 0


# ---- what the run booked ------------------------------------------------------------------------------------------------------------------------
def new_contracts(run_db: str, control_db: str) -> List[dict]:
    """The contracts in the run copy that the control does not have (the run's bookings)."""
    with ro(control_db) as c0:
        before = {r["contract_id"] for r in rows(c0, "SELECT contract_id FROM otc_contracts")}
    with ro(run_db) as c1:
        return [r for r in rows(c1, "SELECT * FROM otc_contracts ORDER BY contract_id") if r["contract_id"] not in before]


def booking_instant(run_db: str, contracts: List[dict]) -> Optional[int]:
    if not contracts:
        return None
    with ro(run_db) as c:
        ts = [c.execute("SELECT finished_ts FROM commands WHERE id=?", (r["command_id"],)).fetchone() for r in contracts]
    vals = [int(t[0]) for t in ts if t is not None]
    return max(vals) + 60 if vals else None


def default_until(contracts: List[dict], start: int) -> int:
    """08:00 ET of the day after the swap's start date S (the latest start of the new XCCY contracts), else start + 2 days at 08:00."""
    from finsim.terminal.clock import epoch_of, ny_of
    starts = [date.fromisoformat(str(r["start_date"])[:10]) for r in contracts if r["kind"] == "XCCY"]
    if starts:
        return epoch_of(max(starts) + timedelta(days=1), "08:00")
    return epoch_of(ny_of(start).date() + timedelta(days=2), "08:00")


def tick_instants(start: int, until: int) -> List[int]:
    from finsim.terminal.clock import epoch_of, ny_of
    out = [start]
    d = ny_of(start).date()
    while True:
        for hm in TICK_TIMES:
            t = epoch_of(d, hm)
            if start < t <= until:
                out.append(t)
        if epoch_of(d, "00:00") > until:
            break
        d += timedelta(days=1)
    if out[-1] != until:
        out.append(until)
    return sorted(set(out))


# ---- one copy ---------------------------------------------------------------------------------------------------------------------------------
class Copy:
    """A prepared working copy (``db``) driven by the real runtime on a FakeClock (module docstring)."""

    def __init__(self, label: str, db: str, start: int):
        from finsim.terminal import config, server
        from finsim.terminal.clock import FakeClock
        from finsim.terminal.quotes.providers import FakeProvider
        self.label = label
        self.db = db
        self.home = os.path.dirname(os.path.abspath(db))
        os.environ[config.HOME_ENV] = self.home
        os.environ[config.DB_ENV] = self.db
        self.clock = FakeClock(datetime.fromtimestamp(start, timezone.utc).isoformat())
        self.fp = FakeProvider(self.clock)
        self.log = _Log()
        self.rt = server.build_runtime(self.home, self.db, clock=self.clock, offline=True, fake_provider=self.fp, log=self.log, fetch_threads=0)
        self.inputs_day: Optional[date] = None
        self.ticks = 0
        self.failures: List[str] = []
        self.held: List[dict] = []

    def close(self) -> None:
        try:
            self.rt.close()
        except Exception:   # noqa: BLE001 - a runtime half-built: nothing more to close
            try:
                self.rt.db.close()
            except Exception:   # noqa: BLE001
                pass

    # -- the calendar of the held inputs (V1061R1-03) --
    def business_day(self, d: date) -> bool:
        cal = getattr(self.rt, "cal", None)
        if cal is not None and hasattr(cal, "is_business_day"):
            try:
                return bool(cal.is_business_day(d))
            except Exception:   # noqa: BLE001 - no calendar for that year: the weekday rule
                pass
        return d.weekday() < 5

    def prev_business_day(self, d: date) -> date:
        p = d - timedelta(days=1)
        while not self.business_day(p):
            p -= timedelta(days=1)
        return p

    # -- the inputs, held flat --
    def hold_quotes(self, now: int) -> int:
        cache = self.rt.cache
        recs = cache.get_many(cache.ids(), at=now)
        n = 0
        for iid, r in recs.items():
            if r is None or (r.last is None and r.mid is None):
                continue
            src = r.source if TEST_LABEL in str(r.source or "") else f"{r.source or r.provider} - {TEST_LABEL}"
            cache.put(dataclasses.replace(r, quote_time=now, fetched_at=now, restored=False, error=None, source=src, last_error_at=None), at=now)
            n += 1
        return n

    def hold_rates(self, now: int) -> Optional[dict]:
        """V1061R1-03: once per BUSINESS day, at its first tick from ``HOLD_RATES_ET`` (when the day's overnight prints and curves are out), the
        last overnight prints, the last grids and the last par curve again, dated the previous business day.  Never on a Saturday, a Sunday
        or a holiday: no source publishes then, so production's inputs do not move - re-dating them on Saturday 10-31 made the new contracts
        post a weekend VM that production would not post.  Returns what was re-dated (None when nothing ran)."""
        from finsim.terminal.clock import ny_of
        t = ny_of(now)
        today = t.date()
        if self.inputs_day == today or not self.business_day(today) or t.strftime("%H:%M") < HOLD_RATES_ET:
            return None
        self.inputs_day = today
        prev = self.prev_business_day(today)
        rec = {"on": t.strftime("%Y-%m-%d %H:%M ET"), "dated": prev.isoformat(), "series": [], "grids": [], "par_curve_of": None}
        rates = self.rt.rates
        for series in sorted(getattr(rates, "_obs", {}).keys()):
            last = rates.latest(series)
            # a DAILY print only (observed within DAILY_MAX_GAP_DAYS): a monthly series (the OECD rates) is never published daily, so it stays
            if last is not None and last[0] < prev and (prev - last[0]).days <= DAILY_MAX_GAP_DAYS:
                rates.put(series, [(prev, last[1])], f"{TEST_LABEL}: {series} {last[1]} of {last[0].isoformat()}")
                rec["series"].append(series)
        for series in sorted(getattr(rates, "_grids", {}).keys()):
            g = rates.latest_grid(series)
            if g is not None and g.as_of < prev:
                rates.put_grid(series, prev, dict(g.points), f"{TEST_LABEL}: {series} of {g.as_of.isoformat()}", int(self.clock.epoch()))
                rec["grids"].append(series)
        cur = self.rt.curve.latest() if self.rt.curve is not None else None
        if cur is not None and cur.as_of < prev:
            from finsim.terminal.quotes.parsers import ParCurve
            self.rt.curve.put(ParCurve(prev, dict(cur.points), source=f"{TEST_LABEL}: Treasury par curve of {cur.as_of.isoformat()}",
                                       fetched_at=int(self.clock.epoch())))
            rec["par_curve_of"] = cur.as_of.isoformat()
        self.held.append(rec)
        return rec

    def tick(self, at: int) -> dict:
        from finsim.terminal.clock import ny_of
        self.clock.set(datetime.fromtimestamp(at, timezone.utc).isoformat())
        self.hold_rates(at)
        self.hold_quotes(at)
        out = self.rt.lifecycle.tick()
        self.ticks += 1
        for item in out.get("ran") or []:
            if item.get("status") == "FAILED":
                self.failures.append(f"{ny_of(at).strftime('%Y-%m-%d %H:%M')} {item.get('job')} {item.get('key')}: {item.get('note')}")
        return out


# ---- the diff and the events -------------------------------------------------------------------------------------------------------------------------
def norm(x) -> str:
    return ID_RE.sub("#", str(x if x is not None else ""))


def contract_state(db: str, cid: str, since: int) -> dict:
    with ro(db) as c:
        row = c.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
        if row is None:
            return {"missing": True}
        keep = ("status", "mark_c", "vm_balance_c", "im_c", "notional_c", "maturity_date", "start_date", "terms_json", "closed_ts")
        out = {"row": {k: row[k] for k in keep if k in row.keys()}}
        out["vm"] = [(r["vm_date"], r["mark_c"], r["delta_c"], r["mode"]) for r in rows(c, "SELECT * FROM otc_vm WHERE contract_id=? ORDER BY vm_date", (cid,))]
        out["resets"] = [(r["event_date"], r["kind"], r["amount_c"]) for r in rows(c, "SELECT * FROM otc_resets WHERE contract_id=? ORDER BY event_date, kind", (cid,))]
        out["lines"] = sorted((e["kind"], e["business_date"], ln["gl"], ln["currency"], ln["amount_c"]) for e in rows(c, "SELECT * FROM journal_entries WHERE ref_type='otc'"
                              " AND ref_id=? AND ts>=?", (cid, since)) for ln in rows(c, "SELECT gl, currency, amount_c FROM journal_lines WHERE entry_id=?", (e["entry_id"],)))
        out["jobs"] = sorted((r["job"], r["key"], r["status"], norm(r["note"])) for r in rows(c, "SELECT job, key, status, note FROM jobs_done WHERE key LIKE ? AND run_ts>=?",
                                                                                                ("%" + cid + "%", since)))
    return out


def diff_states(a: dict, b: dict) -> List[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if json.dumps(a.get(k), sort_keys=True, default=str) != json.dumps(b.get(k), sort_keys=True, default=str):
            out.append(k)
    return out


def _cash_1010(c, account_id: str, upto_entry: Optional[int] = None) -> Dict[str, int]:
    """Settled cash (1010) of ``account_id`` per currency, after entry ``upto_entry`` (None: now)."""
    sql = "SELECT currency, SUM(amount_c) s FROM journal_lines WHERE account_id=? AND gl='1010'"
    params: list = [account_id]
    if upto_entry is not None:
        sql += " AND entry_id<=?"
        params.append(int(upto_entry))
    return {r["currency"]: int(r["s"] or 0) for r in rows(c, sql + " GROUP BY currency ORDER BY currency", params)}


def start_pvp(c, cid: str, account_id: str, terms: dict) -> Optional[dict]:
    """V1061R1-04 (FX_CCS_PLAN 9 item 5, D7, 4.2): the START of a cross-currency swap as one command - the PvP's settled cash per currency in
    the swap's account (the conversion's receipt, the exchange, the conversion's delivery; the funding sweeps left out), the net it must come
    to (the quote's ``net_start_flows``, and the same figure from the rows: the conversion's USD less the USD lent, the foreign currency
    received less the foreign currency delivered), its funding (source, own cash, Treasury sweep) and the settled cash of the account and of
    its Treasury after it."""
    r = c.execute("SELECT * FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_START'", (cid,)).fetchone()
    if r is None:
        return None
    try:
        det = json.loads(r["detail_json"] or "{}")
    except ValueError:
        det = {}
    e = c.execute("SELECT command_id FROM journal_entries WHERE entry_id=?", (r["entry_id"],)).fetchone() if r["entry_id"] is not None else None
    cmd_id = e["command_id"] if e is not None else None
    cmd = c.execute("SELECT request_id, finished_ts FROM commands WHERE id=?", (cmd_id,)).fetchone() if cmd_id is not None else None
    entries = rows(c, "SELECT entry_id, kind, memo FROM journal_entries WHERE command_id=? ORDER BY entry_id", (cmd_id,)) if cmd_id is not None else []
    transfers = rows(c, "SELECT * FROM transfers WHERE command_id=? ORDER BY transfer_id", (cmd_id,)) if cmd_id is not None else []
    funding_entries = {t["entry_id"] for t in transfers if t.get("entry_id") is not None}
    pvp: Dict[str, dict] = {}
    for en in entries:
        if en["entry_id"] in funding_entries:
            continue
        for ln in rows(c, "SELECT currency, amount_c FROM journal_lines WHERE entry_id=? AND account_id=? AND gl='1010'", (en["entry_id"], account_id)):
            p = pvp.setdefault(ln["currency"], {"in_c": 0, "out_c": 0, "net_c": 0})
            a = int(ln["amount_c"])
            p["in_c" if a > 0 else "out_c"] += a
            p["net_c"] += a
    # the same net from the rows: the exchange (we pay the lent currency, we receive the borrowed one) and the conversion deal's two legs
    from_rows: Dict[str, int] = {}

    def add(ccy, amt):
        if ccy:
            from_rows[ccy] = from_rows.get(ccy, 0) + int(amt or 0)
    we_pay, we_rcv = det.get("we_pay") or {}, det.get("we_receive") or {}
    add(we_pay.get("currency"), -int(we_pay.get("amount_c") or 0))
    add(we_rcv.get("currency"), int(we_rcv.get("amount_c") or 0))
    conv = det.get("conversion") or {}
    deal = c.execute("SELECT * FROM fx_deals WHERE deal_id=?", (conv.get("deal_id"),)).fetchone() if conv.get("deal_id") else None
    if deal is not None:
        add(deal["buy_ccy"], abs(int(deal["buy_c"] or 0)))
        add(deal["sell_ccy"], -abs(int(deal["sell_c"] or 0)))
    quoted = ((terms.get("pricing_record") or {}).get("results") or {}).get("net_start_flows") or {}
    quoted = {k: int(v) for k, v in quoted.items() if k != "words" and v is not None}
    upto = max((en["entry_id"] for en in entries), default=None)
    lid = account_id.split(":", 1)[0]
    funding = det.get("funding") or {}
    return {"command": cmd["request_id"] if cmd is not None else None, "finished_ts": cmd["finished_ts"] if cmd is not None else None,
            "pvp": pvp, "net_from_rows": from_rows, "net_quoted": quoted, "conversion_deal": conv.get("deal_id"),
            "conversion_status": deal["status"] if deal is not None else None,
            "funding": {k: funding.get(k) for k in ("source", "need_c", "own_c", "shortfall_c", "sweep_c", "pb_draw_c", "funded", "unfunded_c") if k in funding},
            "sweeps": [{"from": t["from_account"], "to": t["to_account"], "currency": t["currency"], "amount_c": t["amount_c"], "kind": t["kind"]} for t in transfers],
            "account": account_id, "cash_after": _cash_1010(c, account_id, upto), "treasury": f"{lid}:TREASURY",
            "treasury_after": _cash_1010(c, f"{lid}:TREASURY", upto)}


def events_of(db: str, contracts: List[dict], since: int) -> dict:
    """The new contracts' life over the window, and the checks of 9 item 5."""
    out: Dict[str, dict] = {}
    with ro(db) as c:
        for k in contracts:
            cid = k["contract_id"]
            row = c.execute("SELECT status, account_id, terms_json FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
            e: dict = {"kind": k["kind"], "status": row["status"], "account": row["account_id"]}
            e["resets"] = [(r["event_date"], r["kind"], r["amount_c"]) for r in rows(c, "SELECT * FROM otc_resets WHERE contract_id=? ORDER BY event_date, kind", (cid,))]
            e["vm"] = [(r["vm_date"], r["mark_c"], r["delta_c"]) for r in rows(c, "SELECT * FROM otc_vm WHERE contract_id=? ORDER BY vm_date", (cid,))]
            e["accruals"] = [(r["kind"], r["business_date"], r["memo"][:120]) for r in rows(c, "SELECT * FROM journal_entries WHERE kind='ACCRUAL' AND ref_id=? ORDER BY entry_id LIMIT 6",
                                                                                              (cid,))]
            e["attention"] = [(r["severity"], r["code"], r["message"][:160]) for r in rows(c, "SELECT * FROM attention WHERE (ref_id=? OR message LIKE ?) AND ts>=?",
                                                                                              (cid, "%" + cid + "%", since))]
            deals = [d for d in rows(c, "SELECT * FROM fx_deals") if cid in str(d.get("inputs_json") or "")]
            e["deals"] = []
            for d in deals:
                sl = []
                for s in rows(c, "SELECT * FROM settlements WHERE fx_deal_id=? ORDER BY leg", (d["deal_id"],)):
                    by = None
                    if s.get("entry_id"):
                        cmd = c.execute("SELECT c.request_id FROM journal_entries e JOIN commands c ON c.id=e.command_id WHERE e.entry_id=?", (s["entry_id"],)).fetchone()
                        by = cmd[0] if cmd is not None else None
                    sl.append({"leg": s["leg"], "currency": s["currency"], "amount_c": s["amount_c"], "due": s["due_date"], "status": s["status"],
                               "ref": [s.get("ref_type"), s.get("ref_id")] if has_col(c, "settlements", "ref_type") else None, "settled_by": by})
                e["deals"].append({"deal_id": d["deal_id"], "kind": d["kind"], "status": d["status"], "buy": [d["buy_ccy"], d["buy_c"]], "sell": [d["sell_ccy"], d["sell_c"]],
                                   "link": (json.loads(d["inputs_json"] or "{}").get("link") or {}).get("type"), "settlements": sl})
            if k["kind"] == "XCCY":
                try:
                    terms = json.loads(row["terms_json"] or "{}")
                except ValueError:
                    terms = {}
                e["start"] = start_pvp(c, cid, row["account_id"], terms if isinstance(terms, dict) else {})
            lid = str(row["account_id"]).split(":", 1)[0]
            e["cash_end"] = {"account": _cash_1010(c, row["account_id"]), "treasury": _cash_1010(c, f"{lid}:TREASURY")}
            out[cid] = e
    return out


def checks_of(events: dict) -> Dict[str, dict]:
    """9 item 5: the START PvP (its net per currency as quoted, its funding, cash at or above zero after it) and the conversion SETTLED, both
    legs' first accruals, the first VM, the tagged rows settled by the START."""
    chk: Dict[str, dict] = {}
    for cid, e in events.items():
        if e["kind"] != "XCCY":
            if e["kind"] == "FX_FORWARD":
                chk[f"{cid} first VM"] = {"ok": bool(e["vm"]), "evidence": e["vm"][:2]}
            continue
        started = [r for r in e["resets"] if r[1] == "NOTIONAL_START"]
        chk[f"{cid} START PvP"] = {"ok": bool(started), "evidence": started}
        st = e.get("start")
        if st is not None:
            # V1061R1-04: the PvP nets, per currency, to what the quote promised (net_start_flows) and to what the rows say; the foreign
            # currency nets to 0 and USD to minus the conversion spread (the "net USD -40.00 class": never a notional)
            nets = {k: v["net_c"] for k, v in st["pvp"].items()}
            ccys = sorted(set(nets) | set(st["net_from_rows"]) | set(st["net_quoted"]))
            usd = nets.get("USD", 0)
            foreign = [x for x in ccys if x != "USD"]
            ok_net = (all(nets.get(x, 0) == st["net_from_rows"].get(x, 0) for x in ccys)
                      and (not st["net_quoted"] or all(nets.get(x, 0) == st["net_quoted"].get(x, 0) for x in ccys))
                      # with the inception conversion (the owner's trade) the foreign currency nets to 0 and USD to minus the spread
                      and (not st["conversion_deal"] or (bool(st["net_quoted"]) and all(nets.get(x, 0) == 0 for x in foreign) and usd <= 0)))
            chk[f"{cid} START net per currency (the quoted net start flows)"] = {
                "ok": ok_net, "evidence": {"pvp": st["pvp"], "quoted": st["net_quoted"], "from_rows": st["net_from_rows"]}}
            f = st["funding"]
            swept = sum(int(s["amount_c"] or 0) for s in st["sweeps"] if s["to"] == st["account"] and s["kind"] == "SWEEP_IN")
            ok_fund = (bool(f.get("source")) and f.get("funded") is not False and int(f.get("sweep_c") or 0) == swept
                       and int(f.get("own_c") or 0) + int(f.get("sweep_c") or 0) + int(f.get("pb_draw_c") or 0) >= int(f.get("need_c") or 0))
            chk[f"{cid} START funding source"] = {"ok": ok_fund, "evidence": {"funding": f, "sweeps": st["sweeps"]}}
            neg = {k: v for k, v in list(st["cash_after"].items()) + [("TREASURY " + kk, vv) for kk, vv in st["treasury_after"].items()] if v < 0}
            neg_end = {k: v for k, v in list(e["cash_end"]["account"].items()) + [("TREASURY " + kk, vv) for kk, vv in e["cash_end"]["treasury"].items()] if v < 0}
            chk[f"{cid} cash at or above zero after the START and at the end"] = {
                "ok": not neg and not neg_end, "evidence": {"after_start": {"account": st["cash_after"], "treasury": st["treasury_after"]}, "end": e["cash_end"],
                                                            "negative": dict(neg, **{f"end {k}": v for k, v in neg_end.items()})}}
        conv = [d for d in e["deals"] if d["link"] == "xccy_start"]
        chk[f"{cid} conversion SETTLED"] = {"ok": bool(conv) and all(d["status"] == "SETTLED" for d in conv), "evidence": [(d["deal_id"], d["status"]) for d in conv]}
        by = [s["settled_by"] for d in conv for s in d["settlements"]]
        chk[f"{cid} tagged rows settled by the START, not SETTLE_DUE"] = {"ok": bool(by) and all(b and ":START" in b for b in by), "evidence": by}
        kinds = {a[0] for a in e["accruals"]}
        legs = [a for a in e["accruals"]]
        chk[f"{cid} first accruals of both legs"] = {"ok": len(legs) >= 2, "evidence": legs[:4], "kinds": sorted(kinds)}
        chk[f"{cid} first VM on the CSA exposure"] = {"ok": bool(e["vm"]), "evidence": e["vm"][:3]}
    return chk


# ---- the check -----------------------------------------------------------------------------------------------------------------------------------
def run_forward_check(run_db: str, control_db: str, *, work: Optional[str] = None, start: Optional[int] = None, until: Optional[int] = None,
                      untouched: Optional[List[str]] = None, keep: bool = False, out=print, align_inputs: bool = True) -> dict:
    """``align_inputs`` False (tests only, to show what the alignment prevents) drives each copy from its OWN last inputs, as before
    V1061R1-01; the report then says so and the check cannot pass."""
    from finsim.terminal.clock import ny_of
    contracts = new_contracts(run_db, control_db)
    if start is None:
        start = booking_instant(run_db, contracts) or int(time.time())
    if until is None:
        until = default_until(contracts, start)
    instants = tick_instants(start, until)
    with ro(control_db) as c0:
        pre = [r["contract_id"] for r in rows(c0, "SELECT contract_id FROM otc_contracts ORDER BY contract_id")]
    watch = sorted(set(pre) | set(untouched or []))
    fork = fork_ts(control_db)
    own_work = work is None
    work = work or tempfile.mkdtemp(prefix="fxfwdchk-")
    report = {"run_db": os.path.abspath(run_db), "control_db": os.path.abspath(control_db), "start_et": ny_of(start).strftime("%Y-%m-%d %H:%M ET"),
              "until_et": ny_of(until).strftime("%Y-%m-%d %H:%M ET"), "ticks": len(instants), "new_contracts": [c["contract_id"] for c in contracts],
              "pre_existing": watch, "fork_et": ny_of(fork).strftime("%Y-%m-%d %H:%M:%S ET") if fork else None,
              "inputs": (f"ONE set of inputs for both copies: the run copy's {', '.join(INPUT_TABLES)} copied into the control before the first tick;"
                         f" quotes held flat at every tick, overnight prints / grids / par curve re-dated on business days from {HOLD_RATES_ET} ET; {TEST_LABEL}")}
    states: Dict[str, Dict[str, dict]] = {}
    failures: Dict[str, List[str]] = {}
    held: Dict[str, List[dict]] = {}
    paths = {label: os.path.join(work, label, "terminal.db") for label in ("control", "run")}
    try:
        backup(control_db, paths["control"])
        backup(run_db, paths["run"])
        if align_inputs:
            report["inputs_before_alignment"] = seed_inputs(paths["run"], paths["control"])
        else:
            report["inputs"] = "each copy's OWN last inputs (NOT aligned: a test of what V1061R1-01 fixed; this check cannot pass)"
        for label in ("control", "run"):
            cp = Copy(label, paths[label], start)
            try:
                for t in instants:
                    cp.tick(t)
            finally:
                cp.close()
            failures[label] = cp.failures
            held[label] = cp.held
            states[label] = {cid: contract_state(cp.db, cid, fork) for cid in watch}
            out(f"{label}: {cp.ticks} ticks from {report['start_et']} to {report['until_et']}, {len(cp.failures)} job failures")
        diff = {cid: diff_states(states["control"][cid], states["run"][cid]) for cid in watch}
        diff = {k: v for k, v in diff.items() if v}
        report["control_diff"] = {cid: {"differs_in": parts, "control": {p: states["control"][cid].get(p) for p in parts},
                                         "run": {p: states["run"][cid].get(p) for p in parts}} for cid, parts in diff.items()}
        report["job_failures"] = failures
        report["held_inputs"] = held["run"]
        ev = events_of(paths["run"], contracts, start)
        report["events"] = ev
        chk = checks_of(ev)
        # V1061R1-01 / -03: both copies re-dated the same inputs at the same instants (one set of inputs), and only on business days
        weekend = [h for h in held["run"] + held["control"] if date.fromisoformat(h["on"][:10]).weekday() >= 5]
        chk["one set of inputs: the control re-dated exactly what the run did, on business days only"] = {
            "ok": held["control"] == held["run"] and not weekend, "evidence": {"run": held["run"], "control_equal": held["control"] == held["run"], "weekend": weekend}}
        report["checks"] = chk
        report["ok"] = align_inputs and not diff and all(v["ok"] for v in chk.values()) and not failures["run"] and not failures["control"]
    finally:
        if own_work and not keep:
            shutil.rmtree(work, ignore_errors=True)
        elif not keep:
            for label in ("control", "run"):
                shutil.rmtree(os.path.join(work, label), ignore_errors=True)
    return report


def _start_text(st: dict) -> List[str]:
    from finsim.terminal.clock import ny_of
    when = ny_of(int(st["finished_ts"])).strftime("%Y-%m-%d %H:%M ET") if st.get("finished_ts") else "?"
    out = [f"  START PvP ({st['command']}, {when}) in {st['account']}, settled cash per currency (funding sweeps apart):"]
    for ccy in sorted(set(st["pvp"]) | set(st["net_quoted"]) | set(st["net_from_rows"])):
        p = st["pvp"].get(ccy) or {"in_c": 0, "out_c": 0, "net_c": 0}
        q = st["net_quoted"].get(ccy)
        out.append(f"    {ccy} +{money(p['in_c'])} / {money(p['out_c'])} = net {money(p['net_c'])} (quoted net start flow {money(q) if q is not None else 'none'};"
                   f" from the exchange and the conversion {money(st['net_from_rows'].get(ccy, 0))})")
    f = st["funding"]
    out.append(f"    funding: source {f.get('source')}, need {money(f.get('need_c'))}, own cash available {money(f.get('own_c'))}, shortfall {money(f.get('shortfall_c'))},"
               f" Treasury sweep {money(f.get('sweep_c'))}"
               + (f", PB financing {money(f.get('pb_draw_c'))}" if f.get("pb_draw_c") else "")
               + ("; sweeps " + ", ".join(f"{s['kind']} {s['currency']} {money(s['amount_c'])} {s['from']} -> {s['to']}" for s in st["sweeps"]) if st["sweeps"] else "; no sweep"))
    out.append(f"    settled cash after it: {st['account']} " + ", ".join(f"{k} {money(v)}" for k, v in st["cash_after"].items())
               + f"; {st['treasury']} " + ", ".join(f"{k} {money(v)}" for k, v in st["treasury_after"].items())
               + f"; conversion {st['conversion_deal']} {st['conversion_status']}")
    return out


def report_text(rep: dict) -> str:
    lines = [f"Forward check {rep['start_et']} -> {rep['until_et']} ({rep['ticks']} ticks); inputs: {rep['inputs']}",
             f"New contracts: {', '.join(rep['new_contracts']) or 'none'}; pre-existing watched: {', '.join(rep['pre_existing'])}; compared since the fork"
             f" {rep.get('fork_et')}"]
    for t, d in (rep.get("inputs_before_alignment") or {}).items():
        if d.get("skipped"):
            lines.append(f"Inputs {t}: {d['skipped']}")
        elif d.get("only_run") or d.get("only_control") or d.get("differ"):
            ex = "; ".join(f"{x['key']} {x['control']} -> {x['run']}" for x in (d.get("differ_examples") or [])[:6])
            lines.append(f"Inputs {t} aligned to the run copy: {d['differ']} differed, {d['only_run']} only in the run copy, {d['only_control']} only in the control"
                         + (f" (e.g. {ex})" if ex else ""))
    for h in rep.get("held_inputs") or []:
        lines.append(f"Held inputs re-dated {h['dated']} on {h['on']}: {len(h['series'])} overnight series, grids {', '.join(h['grids']) or 'none'},"
                     f" par curve {'of ' + h['par_curve_of'] if h['par_curve_of'] else 'unchanged'}")
    for cid, e in (rep.get("events") or {}).items():
        lines.append(f"{cid} ({e['kind']}, {e['status']}): resets {e['resets']}; VM {e['vm'][:3]}; accruals {[a[:2] for a in e['accruals'][:4]]}")
        if e.get("start"):
            lines.extend(_start_text(e["start"]))
        for d in e["deals"]:
            lines.append(f"  deal {d['deal_id']} {d['kind']} {d['status']} ({d['link']}): " + "; ".join(f"{s['leg']} {s['currency']} {s['amount_c']} {s['status']} by {s['settled_by']}"
                                                                                                  for s in d["settlements"]))
        for a in e["attention"]:
            lines.append(f"  attention {a[0]} {a[1]}: {a[2]}")
    for k, v in (rep.get("checks") or {}).items():
        lines.append(f"CHECK {k}: {'PASS' if v['ok'] else 'FAIL'} {v.get('evidence')}")
    if rep.get("control_diff"):
        for cid, d in rep["control_diff"].items():
            lines.append(f"DIFF {cid}: {', '.join(d['differs_in'])}")
    else:
        lines.append("Control diff: empty (every pre-existing contract's job results and rows are the same on both copies)")
    for label, f in (rep.get("job_failures") or {}).items():
        for x in f:
            lines.append(f"JOB FAILURE ({label}): {x}")
    lines.append("RESULT: " + ("PASS" if rep.get("ok") else "FAIL"))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FX_CCS_PLAN 9 item 5: drive a dry run's copy and its control through the lifecycle and diff them")
    ap.add_argument("--run", required=True, help="the dry run's copy (C1)")
    ap.add_argument("--control", required=True, help="the control copy taken before the run (C0)")
    ap.add_argument("--report", required=True, help="folder for forward_check.json / .txt")
    ap.add_argument("--work", default=None, help="folder for the working copies (deleted at the end unless --keep)")
    ap.add_argument("--start", default=None, help="ISO time with offset; default the booking instant in the run copy")
    ap.add_argument("--until", default=None, help="ISO time with offset; default 08:00 ET on the day after the swap's start date")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)

    def ep(s):
        return None if not s else int(datetime.fromisoformat(s).timestamp())
    rep = run_forward_check(a.run, a.control, work=a.work, start=ep(a.start), until=ep(a.until), keep=a.keep)
    os.makedirs(a.report, exist_ok=True)
    with open(os.path.join(a.report, "forward_check.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, default=str)
    txt = report_text(rep)
    with open(os.path.join(a.report, "forward_check.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt, end="")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
