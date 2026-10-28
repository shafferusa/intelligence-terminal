"""The owner's import checker for IMP-F1-20261027-A (FINSIM1_PLAN section 15.6 item 3, P8-lite; critic S-02).

    py -3.12 tests\\terminal_import_check.py --batch <batch .jsonl> --source <the database the dry run copied>
        --dryrun <the dry run's work folder> [--second <a second dry run's work folder>] --summary <file>

What it is
- A deliverable, not throwaway evidence: P8-lite runs it on the build-day dry run and after every fix round's re-run, and the
  orchestrator runs it on the apply day (15.8 step 8) with ``--source`` the stopped live database and ``--dryrun`` the apply-day dry
  run's work folder.  The apply runs only when it prints PASS for all five conditions.
- Read-only.  Every database is opened through a sqlite3 URI with ``mode=ro`` (and ``immutable=1`` when its -wal file is absent or
  empty, so SQLite touches no -shm / -wal file either).  The only file it writes is ``--summary``.
- Its own figures.  It uses the standard library only and never imports the application: the row hashes, the business-date cash
  table, the trial balance and the arithmetic of the restated split are computed here from the databases; the terms are compared with
  both the batch's payloads (lines 24-33 and the package record) and the plan's table (below).  It reads the integrity-full result and
  the restatement residuals from the result database, as the plan says.
- Not collected by the suite (its name does not match ``test_terminal_*.py``); the gate commits it through the ``terminal_*.py``
  pathspec.

The checks (the first five are the owner's import conditions; the exit code is 0 only when all five pass)
1. PRESERVED   lines 1-21 report present_differs and 22-23 present_match (the dry run's report of the source, its result and the
               result database's import_lines agree); each preserved line's recorded fills still carry the batch's quantity and
               VWAP; every F1 order, fill, lot, borrow, settlement, futures VM, transfer and nav_snapshots row - and every F1 journal
               entry and line, order event, lot relief, hedge package, stored mark and curve point - that existed in the source is
               identical in the result over the source's columns, and no column a release added was filled in on such a row (the
               import only adds rows); the source is the database the dry run copied (same sha256, state_version, max command id).
2. TERMS       the five OTC contracts and the five option executions are booked once each, at exactly the batch's payloads and the
               plan's terms table, with the as-of booking, the dates and the times below; nothing else was booked; every label is the
               D1 as-of label and none says modeled, modelled, hypothetical or estimated.
3. CASH        every F1 account's business-date settled cash (GL 1010) closes every date from the window start through the day
               before the run at or above zero, and every current balance is >= 0.
4. INTEGRITY   integrity full ok (the result database's import record) and the trial balance balanced (computed here from the
               journal: every entry balances, the stored balances equal the journal, assets = liabilities + equity), and
               PRAGMA integrity_check ok.
5. RESIDUAL    the restatement residual is 0 for every account and F1:ALL on every date; every version row's split adds up; every
               original snapshot has its restated version; F1:ALL equals the sum of its accounts.
6. IDEMPOTENT  (only with --second) the second dry run on the result posted 0 entries and added no row, its lines 24-33 report
               already_imported, and both -wal files are empty.

The plan's terms table (15.6 item 3; critic S-06: the OTC contracts and the options carry different dates)
  TRS ORCL / META   short (pay total return)  reset 129.13 / 697.52 on 38,700 / 7,200 units
  CDX IG / HY       buy protection            64 / 352 bp on 3,000,000 / 4,000,000
  IRS               pay fixed                 4.720 % on 7,500,000
                    -> trade, value and start date 2026-10-21; entered as executed 19:45 ET; Goldman Sachs (F1:PB_GS)
  Options           BTO ORCL 140C, META 660C, IBIT 46C and QQQ 700P; STO QQQ 675P (all expiring 2026-11-20); premiums 3.55 /
                    23.59 / 2.10 / 17.64 / 10.25 on 387 / 72 / 105 / 150 / 150 -> trade date 2026-10-22 at 16:00 ET; settlement
                    2026-10-23; US Exchange
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

BATCH_ID = "IMP-F1-20261027-A"
#: the plan's pinned sha256 of batch revision 3 (FINSIM1_PLAN section 7 and 15.2 step 1)
PINNED_SHA256 = "75690c19327713c6648d20b710495f5ef0b5a924ed263748b3f74aa51bd11d2f"
WORKBOOK_NAV_C = 2577070086          # the owner's audited reconstruction of 2026-10-23 (25,770,700.86): never forced
WORKBOOK_DATE = "2026-10-23"
BANNED_WORDS = re.compile(r"\b(modell?ed|hypothetical|estimated)\b", re.I)

# ---- the plan's terms table (independent of the batch file) ------------------------------------------------------------------
OTC_ACCOUNT, OTC_BROKER = "F1:PB_GS", "GS"
OTC_DATE = "2026-10-21"
OTC_EXEC_ET = (2026, 10, 21, 19, 45)
OTC_TABLE = {
    24: {"kind": "TRS", "what": "TRS ORCL short (pay total return)", "underlying": "ORCL", "units": 38700, "reset_px": "129.13"},
    25: {"kind": "TRS", "what": "TRS META short (pay total return)", "underlying": "META", "units": 7200, "reset_px": "697.52"},
    26: {"kind": "CDX", "what": "CDX.NA.IG 5Y buy protection", "index": "IG", "notional": "3000000", "spread_bp": "64"},
    27: {"kind": "CDX", "what": "CDX.NA.HY 5Y buy protection", "index": "HY", "notional": "4000000", "spread_bp": "352"},
    28: {"kind": "IRS", "what": "USD SOFR IRS pay fixed", "notional": "7500000", "fixed_rate": "0.04720", "tenor_months": 36},
}
OPT_ACCOUNT = "F1:US_EXCHANGE"
OPT_TRADE_DATE, OPT_SETTLE_DATE, OPT_EXPIRY = "2026-10-22", "2026-10-23", "2026-11-20"
OPT_EXEC_ET = (2026, 10, 22, 16, 0)
OPT_TABLE = {   # line: (OCC symbol, side, contracts, premium, underlying, right, strike)
    29: ("ORCL261120C00140000", "BTO", 387, "3.55", "ORCL", "C", "140"),
    30: ("META261120C00660000", "BTO", 72, "23.59", "META", "C", "660"),
    31: ("IBIT261120C00046000", "BTO", 105, "2.10", "IBIT", "C", "46"),
    32: ("QQQ261120P00700000", "BTO", 150, "17.64", "QQQ", "P", "700"),
    33: ("QQQ261120P00675000", "STO", 150, "10.25", "QQQ", "P", "675"),
}
PRESERVE_STATUS = {**{n: "present_differs" for n in range(1, 22)}, 22: "present_match", 23: "present_match"}
OTC_LABEL_RX = re.compile(r"^As-of booking - simulated OTC trade with Goldman Sachs, value date 2026-10-21, entered (.+) by the operator at the"
                          r" owner's instruction \(batch IMP-F1-20261027-A\)$")
OPT_LABEL_RX = re.compile(r"^As-of booking - simulated fill at the 2026-10-22 closing print, entered (.+) by the operator at the owner's"
                          r" instruction \(batch IMP-F1-20261027-A\)$")

# ---- the plan's estimates (section 7, 15.6 and P5 acceptance 2; figures through 10-26, variant A) ----------------------------
PLAN_ESTIMATES = [
    # (key, what, cents, note)
    ("im_total", "OTC initial margin, all five contracts", 140561550, "TRS 499,733.10 + 502,214.40, CDX 60,000.00 + 240,000.00, IRS 103,668.00"),
    ("opt_paid", "option premiums paid", 59388300, ""),
    ("opt_received", "option premium received (the QQQ 675 put)", 15375000, ""),
    ("opt_fees", "option commissions", 56160, ""),
    ("opt_net", "net option cash", -44069460, "settling 2026-10-23"),
    ("cdx_initial", "CDX initial payments received (upfronts + rebates)", 30992095, "settling 2026-10-22"),
    ("vm_trs", "variation margin through 10-26: TRS", 60092100, ""),
    ("vm_cdx", "variation margin through 10-26: CDX", -28648716, "the plan's IG part rests on implied proxies (an estimate)"),
    ("vm_irs", "variation margin through 10-26: IRS", -2284470, ""),
    ("vm_net", "variation margin through 10-26: net (Goldman's collateral)", 29158914, ""),
    ("restricted_1026", "restricted cash at the close of 10-26 (net CSA collateral received)", 29158914, ""),
    ("tsy_1022_before", "Treasury at the close of 10-22 before the as-of sweep", -23433770, "the overdraft the as-of sweep resolves"),
    ("tsy_1022_after", "Treasury at the close of 10-22 after the as-of sweep", 36188625, ""),
    ("pb_sweep", "the as-of sweep from Goldman to Treasury dated 10-22", 59622395, ""),
    ("settle_sweeps", "Treasury sweeps for the option settlement on 10-23", 43683522, ""),
    ("int_tsy", "interest restated through night 10-26: Treasury", -84807, ""),
    ("int_pb", "interest restated through night 10-26: Goldman PB credit", 438, ""),
    ("nav_1023_ex_dating", "restated NAV of 10-23 before the dating line", 2568240637, "the estimate had no dating line"),
    ("nav_1026", "restated NAV of 10-26 (window past 10-26: options at Yahoo last trades)", 2600611100, "the plan says about 26,006,111"),
]
#: plan figures given only to the unit ("about"): a difference below this many cents is the plan's rounding, not a difference
PLAN_TOLERANCE_C = {"nav_1026": 100}


# ---- small helpers ---------------------------------------------------------------------------------------------------------------
def money(c: Optional[int], sign: bool = False) -> str:
    if c is None:
        return "unavailable"
    c = int(c)
    s = f"{abs(c) // 100:,}.{abs(c) % 100:02d}"
    if c < 0:
        return "-" + s
    return ("+" + s) if sign else s


def dec(x) -> Optional[Decimal]:
    if x is None:
        return None
    try:
        return Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def cents_of_text(x) -> Optional[int]:
    """'-101,731.44' / '+638.16' / '25,770,700.86' -> cents; None when the text does not start with a number."""
    m = re.match(r"^\s*([+-]?[\d,]+(?:\.\d+)?)", str(x or ""))
    if not m:
        return None
    d = dec(m.group(1))
    return None if d is None else int((d * 100).to_integral_value())


def sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sundays(y: int, m: int) -> List[int]:
    return [d for d in range(1, 32) if _valid(y, m, d) and date(y, m, d).weekday() == 6]


def _valid(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def et_offset(utc: datetime) -> int:
    """US Eastern offset in hours (the 2007 rule: EDT from the second Sunday of March 02:00 to the first Sunday of November 02:00)."""
    y = utc.year
    start = datetime(y, 3, _sundays(y, 3)[1], 7, 0, tzinfo=timezone.utc)
    end = datetime(y, 11, _sundays(y, 11)[0], 6, 0, tzinfo=timezone.utc)
    return -4 if start <= utc < end else -5


def et_text(epoch: Optional[int]) -> str:
    if epoch is None:
        return "unavailable"
    utc = datetime.fromtimestamp(int(epoch), timezone.utc)
    return (utc + timedelta(hours=et_offset(utc))).strftime("%Y-%m-%d %H:%M:%S ET")


def et_epoch(y: int, mo: int, d: int, h: int, mi: int) -> int:
    for off in (-4, -5):
        utc = datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=off)))
        if et_offset(utc.astimezone(timezone.utc)) == off:
            return int(utc.timestamp())
    raise ValueError("no ET time")


def days(a: str, b: str) -> List[str]:
    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
    out = []
    while d0 <= d1:
        out.append(d0.isoformat())
        d0 += timedelta(days=1)
    return out


def file_size(path: str) -> Optional[int]:
    return os.path.getsize(path) if os.path.exists(path) else None


def open_ro(path: str) -> Tuple[sqlite3.Connection, dict]:
    """A read-only connection: mode=ro, plus immutable=1 when the -wal file is absent or empty (no -shm / -wal is touched)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    wal = file_size(path + "-wal")
    uri = "file:" + quote(os.path.abspath(path).replace("\\", "/"), safe="/:") + "?mode=ro"
    immutable = not wal
    if immutable:
        uri += "&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn, {"path": os.path.abspath(path), "wal_bytes": wal, "shm_bytes": file_size(path + "-shm"), "immutable": immutable}


def table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def table_info(conn, name: str) -> List[dict]:
    return [dict(r) for r in conn.execute(f'PRAGMA table_info("{name}")').fetchall()]


def jload(x, default=None):
    try:
        return json.loads(x) if isinstance(x, str) else (x if x is not None else default)
    except ValueError:
        return default


def num_text(x, places: Optional[int] = None) -> str:
    """A decimal as plain text without trailing zeros ('243.740000' -> '243.74', '100' stays '100')."""
    d = dec(x)
    if d is None:
        return str(x)
    if places is not None:
        d = d.quantize(Decimal(1).scaleb(-places))
    t = format(d, "f")
    return t.rstrip("0").rstrip(".") if "." in t else t


def px_text(x) -> str:
    """A price with 2 to 4 decimals ('363' -> '363.00', '242.6' -> '242.60', '168.710867' -> '168.7109')."""
    d = dec(x)
    if d is None:
        return str(x)
    t = format(d.quantize(Decimal("0.0001")), "f")
    while t.endswith("0") and len(t.split(".")[1]) > 2:
        t = t[:-1]
    return t


def without_chain_rows(payload: Any) -> Any:
    """A booking payload with every instrument_spec.chain_row removed (the batch holds a placeholder; the command row the real row)."""
    if isinstance(payload, dict):
        return {k: (without_chain_rows(v) if k != "chain_row" else None) for k, v in payload.items() if k != "chain_row"}
    if isinstance(payload, list):
        return [without_chain_rows(v) for v in payload]
    return payload


class Check:
    def __init__(self, cid: str, title: str):
        self.cid, self.title = cid, title
        self.evidence: List[str] = []
        self.problems: List[str] = []
        self.ran = True

    @property
    def ok(self) -> bool:
        return self.ran and not self.problems

    def ev(self, s: str) -> None:
        self.evidence.append(s)

    def fail(self, s: str) -> None:
        self.problems.append(s)

    def lines(self) -> List[str]:
        head = f"{'PASS' if self.ok else ('NOT RUN' if not self.ran else 'FAIL')}  {self.cid}  {self.title}"
        out = [head]
        out += ["      FAIL: " + p for p in self.problems]
        out += ["      " + e for e in self.evidence]
        return out


# ---- inputs ------------------------------------------------------------------------------------------------------------------------
class Batch:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        with open(path, "rb") as f:
            raw = f.read()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.content_sha256 = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        self.bytes = len(raw)
        recs = [json.loads(ln) for ln in raw.decode("utf-8").splitlines() if ln.strip()]
        self.records = recs
        self.header = recs[0] if recs and recs[0].get("record") == "batch" else {}
        self.batch_id = self.header.get("batch_id") or BATCH_ID
        self.lifetime = self.header.get("lifetime") or "F1"
        self.lines: Dict[int, dict] = {int(r["line_no"]): r for r in recs if r.get("record") == "line"}
        self.packages = [r for r in recs if r.get("record") == "package"]
        self.summary = next((r for r in recs if r.get("record") == "summary"), {})
        self.window_from = ((self.header.get("window") or {}).get("from")) or "2026-10-21"

    def payload_of(self, n: int) -> Tuple[Optional[str], Optional[dict]]:
        """(command kind, the line's own payload or its package leg + package fields) of an import line."""
        ln = self.lines.get(n) or {}
        cmd = ln.get("command") or {}
        if cmd.get("payload"):
            return cmd.get("kind"), cmd["payload"]
        key = ln.get("package_key")
        for p in self.packages:
            if p.get("package_key") == key:
                pc = p.get("command") or {}
                pl = pc.get("payload") or {}
                for leg in pl.get("legs") or []:
                    if leg.get("line_key") == ln.get("idem_key"):
                        merged = dict(leg)
                        for k in ("account_id", "order_type", "tif", "fee_rule", "as_of", "package"):
                            merged[k] = pl.get(k)
                        return pc.get("kind"), merged
        return None, None


def load_report(folder: str) -> dict:
    p = os.path.join(folder, "report.json")
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---- 1. PRESERVED ------------------------------------------------------------------------------------------------------------------
def _row_tables(lid: str) -> List[Tuple[str, Optional[str]]]:
    like = f"'{lid}:%'"
    return [
        ("orders", f"account_id LIKE {like}"),
        ("fills", f"account_id LIKE {like}"),
        ("lots", f"account_id LIKE {like}"),
        ("borrows", f"account_id LIKE {like}"),
        ("settlements", f"account_id LIKE {like}"),
        ("futures_settle", f"account_id LIKE {like}"),
        ("transfers", f"from_account LIKE {like} OR to_account LIKE {like}"),
        ("nav_snapshots", f"account_id LIKE {like}"),
        ("journal_entries", f"entry_id IN (SELECT entry_id FROM journal_lines WHERE account_id LIKE {like})"),
        ("journal_lines", f"account_id LIKE {like}"),
        ("order_events", f"order_id IN (SELECT order_id FROM orders WHERE account_id LIKE {like})"),
        ("lot_relief", f"lot_id IN (SELECT lot_id FROM lots WHERE account_id LIKE {like})"),
        ("hedge_packages", f"account_id LIKE {like}"),
        ("otc_contracts", f"account_id LIKE {like}"),
        ("otc_vm", f"contract_id IN (SELECT contract_id FROM otc_contracts WHERE account_id LIKE {like})"),
        ("marks", None),
        ("curve_points", None),
    ]


def _default_text(d) -> Optional[str]:
    if d is None:
        return None
    s = str(d).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    return s


def compare_rows(src, res, table: str, where: Optional[str]) -> dict:
    """Every source row of ``table`` (filtered) against the result over the source's columns; added columns on old rows."""
    out = {"table": table, "source_rows": 0, "result_rows": 0, "identical": 0, "changed": [], "missing": [], "added_rows": 0,
           "filled_new_columns": [], "skipped": None, "digest_source": None, "digest_result": None}
    if not table_exists(src, table):
        out["skipped"] = "not in the source"
        return out
    if not table_exists(res, table):
        out["skipped"] = "MISSING in the result"
        return out
    sinfo, rinfo = table_info(src, table), table_info(res, table)
    scols = [c["name"] for c in sinfo]
    pk = [c["name"] for c in sorted((c for c in sinfo if c["pk"]), key=lambda c: c["pk"])] or ["rowid"]
    new_cols = [c for c in rinfo if c["name"] not in scols]
    w = f" WHERE {where}" if where else ""
    sel = ", ".join(f'"{c}"' for c in scols)
    pk_sel = ", ".join(f'"{c}"' if c != "rowid" else "rowid" for c in pk)

    def keyed(conn, cols_sel):
        rows = {}
        for r in conn.execute(f'SELECT {pk_sel}, {cols_sel} FROM "{table}"{w}').fetchall():
            t = tuple(r)
            rows[t[:len(pk)]] = t[len(pk):]
        return rows

    srows = keyed(src, sel)
    rrows = keyed(res, sel)
    out["source_rows"], out["result_rows"] = len(srows), len(rrows)
    for k, v in srows.items():
        rv = rrows.get(k)
        if rv is None:
            out["missing"].append(list(k))
        elif rv == v:
            out["identical"] += 1
        else:
            diff = [f"{c}: {a!r} -> {b!r}" for c, a, b in zip(scols, v, rv) if a != b]
            out["changed"].append({"key": list(k), "columns": diff[:6]})
    out["added_rows"] = len(set(rrows) - set(srows))
    # the row hashes: sha256 over the canonical JSON of every preserved row (source columns, by primary key) in each database
    hs, hr = hashlib.sha256(), hashlib.sha256()
    for k in sorted(srows, key=lambda x: json.dumps(x, default=str)):
        hs.update(json.dumps([list(k), list(srows[k])], default=str, separators=(",", ":")).encode("utf-8"))
        hr.update(json.dumps([list(k), list(rrows.get(k) or [])], default=str, separators=(",", ":")).encode("utf-8"))
    out["digest_source"], out["digest_result"] = hs.hexdigest(), hr.hexdigest()
    if new_cols and srows:
        nsel = ", ".join(f'"{c["name"]}"' for c in new_cols)
        nrows = keyed(res, nsel)
        for k in srows:
            vals = nrows.get(k)
            if vals is None:
                continue
            for c, v in zip(new_cols, vals):
                if v is None:
                    continue
                if _default_text(c.get("dflt_value")) is not None and str(v) == _default_text(c.get("dflt_value")):
                    continue
                out["filled_new_columns"].append({"key": list(k), "column": c["name"], "value": v})
    return out


def check_preserved(batch: Batch, src, res, rep: dict, src_path: str, rows_out: dict) -> Check:
    ck = Check("1", "PRESERVED: lines 1-21 present_differs, 22-23 present_match; every existing F1 record identical (the import only adds rows)")
    lid = batch.lifetime
    # the source is the database this dry run copied
    sha_now = sha256_file(src_path)
    if not rep:
        ck.fail("the dry run's report.json was not found")
    else:
        if rep.get("source_sha256_before") and rep.get("source_sha256_before") != sha_now:
            ck.fail(f"the source's sha256 {str(sha_now)[:16]}... differs from the one the dry run copied ({str(rep.get('source_sha256_before'))[:16]}...):"
                    " it is not the same database, or it changed since; re-run the dry run")
        if not rep.get("source_unchanged", False):
            ck.fail("the dry run reports that its source changed while it ran")
        sm = rep.get("source_meta") or {}
        sv = src.execute("SELECT value FROM meta WHERE key='state_version'").fetchone()
        mc = src.execute("SELECT MAX(id) FROM commands").fetchone()[0]
        if sm and (str(sm.get("state_version")) != str(sv[0] if sv else None) or int(sm.get("max_command_id") or -1) != int(mc or -2)):
            ck.fail(f"the source's state_version / max command id {sv[0] if sv else None} / {mc} differ from the dry run's {sm.get('state_version')} /"
                    f" {sm.get('max_command_id')}")
        ck.ev(f"source = the database the dry run copied: sha256 {str(sha_now)[:16]}... = report {str(rep.get('source_sha256_before'))[:16]}...;"
              f" state_version {sv[0] if sv else None}, max command id {mc}")
    # statuses: the report of the source (report_lines), the run's lines, the result database's import_lines
    rl = {int(r["line_no"]): r for r in rep.get("report_lines") or []}
    ll = {int(r["line_no"]): r for r in rep.get("lines") or []}
    il = {}
    if table_exists(res, "import_lines"):
        il = {int(r["line_no"]): dict(r) for r in res.execute("SELECT * FROM import_lines WHERE batch_id=?", (batch.batch_id,)).fetchall()}
    wrong = []
    for n, want in PRESERVE_STATUS.items():
        ln = batch.lines.get(n) or {}
        got = [(rl.get(n) or {}).get("status"), (ll.get(n) or {}).get("status"), (il.get(n) or {}).get("status")]
        if ln.get("decision") != "preserve" or any(g != want for g in got):
            wrong.append(f"line {n}: decision {ln.get('decision')}, statuses report/run/import_lines {got}, expected {want}")
        act = (il.get(n) or {}).get("action") or (ll.get(n) or {}).get("action")
        if act and "preserved" not in str(act):
            wrong.append(f"line {n}: action {act!r}")
    for w in wrong:
        ck.fail(w)
    if not wrong:
        ck.ev("lines 1-21 present_differs and 22-23 present_match, decision preserve, in the report of the source, the run and import_lines")
    # each preserved line's recorded execution still has the batch's quantity, side, date and VWAP (in the source and the result)
    bad = []
    for n in range(1, 24):
        ln = batch.lines.get(n) or {}
        m = ln.get("match") or {}
        fids = m.get("fill_ids") or []
        if not fids:
            bad.append(f"line {n}: the batch records no fill ids")
            continue
        for label, conn in (("source", src), ("result", res)):
            q = ",".join("?" for _ in fids)
            rows = conn.execute(f"SELECT fill_id, account_id, instrument_id, side, qty, price, trade_date FROM fills WHERE fill_id IN ({q})", fids).fetchall()
            if len(rows) != len(fids):
                bad.append(f"line {n}: {len(rows)} of the batch's {len(fids)} fills found in the {label}")
                continue
            qty = sum(int(r["qty"]) for r in rows)
            vwap = sum(Decimal(r["qty"]) * Decimal(r["price"]) for r in rows) / Decimal(qty)
            want_side = "BUY" if str(ln.get("side")) in ("BUY", "BTO", "BUY_TO_COVER") else "SELL"
            key_date = ln.get("exchange_trade_date") or ln.get("intended_trade_date") or ln.get("trade_date")
            probs = []
            if qty != int(ln.get("qty")):
                probs.append(f"quantity {qty} vs line {ln.get('qty')}")
            if any(r["side"] != want_side for r in rows):
                probs.append("side")
            if any(r["account_id"] != ln.get("account_id") or r["instrument_id"] != ln.get("instrument_id") for r in rows):
                probs.append("account / instrument")
            if any(r["trade_date"] != key_date for r in rows):
                probs.append(f"trade date vs {key_date}")
            mv = dec(m.get("vwap"))
            if mv is not None and round(vwap, 4) != round(mv, 4):
                probs.append(f"VWAP {round(vwap, 4)} vs the batch's {mv}")
            matches = round(vwap, 4) == round(dec(ln.get("intended_price")), 4) and not probs
            if (PRESERVE_STATUS[n] == "present_match") != matches and not probs:
                probs.append(f"VWAP {round(vwap, 4)} vs the workbook's {ln.get('intended_price')} gives {'present_match' if matches else 'present_differs'}")
            if probs:
                bad.append(f"line {n} ({label}): " + "; ".join(probs))
    for b in bad:
        ck.fail(b)
    if not bad:
        ck.ev("lines 1-23: every recorded fill exists in the source and the result with the batch's quantity, side, account, key trade date and"
              " VWAP; the VWAP vs the workbook price gives exactly the reported status")
    # row hashes
    total_src = total_ident = total_added = 0
    for table, where in _row_tables(lid):
        r = compare_rows(src, res, table, where)
        rows_out[table] = r
        if r["skipped"]:
            if "MISSING" in r["skipped"]:
                ck.fail(f"{table}: {r['skipped']}")
            continue
        total_src += r["source_rows"]
        total_ident += r["identical"]
        total_added += r["added_rows"]
        if r["missing"]:
            ck.fail(f"{table}: {len(r['missing'])} existing row(s) missing in the result, e.g. {r['missing'][:3]}")
        if r["changed"]:
            ck.fail(f"{table}: {len(r['changed'])} existing row(s) changed, e.g. " + "; ".join(f"{c['key']}: {', '.join(c['columns'])}" for c in r["changed"][:3]))
        if r["filled_new_columns"]:
            ck.fail(f"{table}: a column added by this release was filled in on {len(r['filled_new_columns'])} existing row(s), e.g. "
                    + "; ".join(f"{x['key']} {x['column']}={x['value']!r}" for x in r["filled_new_columns"][:3]))
    ck.ev(f"row hashes: {total_ident:,} of {total_src:,} existing rows identical over the source's columns ("
          + ", ".join(f"{t} {rows_out[t]['identical']}/{rows_out[t]['source_rows']}" for t, _ in _row_tables(lid) if not rows_out[t]["skipped"])
          + f"); {total_added:,} rows added")
    ck.ev("rows added per table: " + ", ".join(f"{t} +{rows_out[t]['added_rows']}" for t, _ in _row_tables(lid)
                                               if not rows_out[t]["skipped"] and rows_out[t]["added_rows"]))
    agg_s, agg_r = hashlib.sha256(), hashlib.sha256()
    for t, _ in _row_tables(lid):
        if not rows_out[t]["skipped"]:
            agg_s.update(rows_out[t]["digest_source"].encode())
            agg_r.update(rows_out[t]["digest_result"].encode())
    ck.ev(f"preserved-row digest (sha256 over every table's rows): source {agg_s.hexdigest()[:16]}... result {agg_r.hexdigest()[:16]}..."
          + (" - equal" if agg_s.hexdigest() == agg_r.hexdigest() else " - DIFFERENT"))
    if agg_s.hexdigest() != agg_r.hexdigest() and ck.ok:
        ck.fail("the preserved-row digests differ")
    return ck


# ---- 2. TERMS ----------------------------------------------------------------------------------------------------------------------
def _eq_dec(a, b) -> bool:
    da, db = dec(a), dec(b)
    return da is not None and db is not None and da == db


def check_terms(batch: Batch, src, res, booked: dict) -> Check:
    ck = Check("2", "TERMS: the five OTC contracts and five options at his exact terms, dates and times; nothing else booked; honest labels")
    lid = batch.lifetime
    otc_exec = et_epoch(*OTC_EXEC_ET)
    opt_exec = et_epoch(*OPT_EXEC_ET)
    il = {}
    if table_exists(res, "import_lines"):
        il = {int(r["line_no"]): dict(r) for r in res.execute("SELECT * FROM import_lines WHERE batch_id=?", (batch.batch_id,)).fetchall()}
    # --- OTC
    for n, t in OTC_TABLE.items():
        ln = batch.lines.get(n) or {}
        key = ln.get("idem_key")
        kind, pl = batch.payload_of(n)
        rows = [dict(r) for r in res.execute("SELECT * FROM otc_contracts WHERE import_key=?", (key,)).fetchall()] if key else []
        if ln.get("decision") != "import" or kind != "otc.book_as_of" or not pl:
            ck.fail(f"line {n}: the batch does not carry an otc.book_as_of import (decision {ln.get('decision')}, command {kind})")
            continue
        if len(rows) != 1:
            ck.fail(f"line {n}: {len(rows)} OTC contracts carry its import key {key} (exactly 1 expected)")
            continue
        c = rows[0]
        terms = jload(c.get("terms_json"), {}) or {}
        pt = pl.get("terms") or {}
        aso = pl.get("as_of") or {}
        p = []
        if c["account_id"] != OTC_ACCOUNT or pl.get("account_id") != OTC_ACCOUNT:
            p.append(f"account {c['account_id']} (payload {pl.get('account_id')})")
        if c["broker"] != OTC_BROKER:
            p.append(f"broker {c['broker']}")
        if c["kind"] != t["kind"] or pl.get("kind") != t["kind"]:
            p.append(f"kind {c['kind']} (payload {pl.get('kind')})")
        if c.get("booking") != "AS_OF":
            p.append(f"booking {c.get('booking')}")
        for f_ in ("trade_date", "value_date", "start_date"):
            if c.get(f_) != OTC_DATE:
                p.append(f"{f_} {c.get(f_)}")
        if aso.get("trade_date") != OTC_DATE or aso.get("value_date") != OTC_DATE:
            p.append("payload dates")
        exec_payload = int(datetime.fromisoformat(str(aso.get("exec_ts"))).timestamp()) if aso.get("exec_ts") else None
        if terms.get("dealt_ts") != otc_exec or exec_payload != otc_exec:
            p.append(f"executed {et_text(terms.get('dealt_ts'))} (payload {et_text(exec_payload)}; the plan 2026-10-21 19:45:00 ET)")
        if aso.get("historical_model"):
            p.append("payload historical_model true")
        if c.get("confirm_status") != "CONFIRMED":
            p.append(f"confirm_status {c.get('confirm_status')}")
        if t["kind"] == "TRS":
            if terms.get("underlying_id") != t["underlying"] or pt.get("underlying_id") != t["underlying"]:
                p.append(f"underlying {terms.get('underlying_id')}")
            if int(terms.get("units") or 0) != t["units"] or int(pt.get("units") or 0) != t["units"]:
                p.append(f"units {terms.get('units')} (payload {pt.get('units')})")
            if terms.get("receiver") is not False or pt.get("receiver") is not False:
                p.append(f"side: receiver {terms.get('receiver')} (short = pay total return = receiver false)")
            if not (_eq_dec(c.get("reset_px"), t["reset_px"]) and _eq_dec(terms.get("reset_px_dealt", c.get("reset_px")), t["reset_px"])
                    and _eq_dec(pt.get("reset_px"), t["reset_px"])):
                p.append(f"reset price {c.get('reset_px')} / {terms.get('reset_px_dealt')} (payload {pt.get('reset_px')})")
            want_notional = int(Decimal(t["units"]) * Decimal(t["reset_px"]) * 100)
            if int(c["notional_c"]) != want_notional:
                p.append(f"notional {money(c['notional_c'])} vs {money(want_notional)}")
            for f_ in ("tenor_months", "reset_months"):
                if f_ in pt and terms.get(f_) != pt.get(f_):
                    p.append(f"{f_} {terms.get(f_)} (payload {pt.get(f_)})")
            desc = f"{t['underlying']} short {terms.get('units'):,} units at {c.get('reset_px')}, notional {money(c['notional_c'])}"
        elif t["kind"] == "CDX":
            if terms.get("index") != t["index"] or pt.get("index") != t["index"]:
                p.append(f"index {terms.get('index')}")
            if terms.get("side") != "BUY_PROTECTION" or pt.get("side") != "BUY_PROTECTION":
                p.append(f"side {terms.get('side')}")
            if not (_eq_dec(terms.get("deal_spread_bp"), t["spread_bp"]) and _eq_dec(pt.get("dealt_spread_bp"), t["spread_bp"])):
                p.append(f"dealt spread {terms.get('deal_spread_bp')} bp (payload {pt.get('dealt_spread_bp')})")
            want_n = int(Decimal(t["notional"]) * 100)
            if int(c["notional_c"]) != want_n or not _eq_dec(pt.get("notional"), t["notional"]):
                p.append(f"notional {money(c['notional_c'])} (payload {pt.get('notional')})")
            for f_ in ("series", "tenor"):
                if f_ in pt and terms.get(f_) != pt.get(f_):
                    p.append(f"{f_} {terms.get(f_)} (payload {pt.get(f_)})")
            desc = f"CDX.NA.{t['index']} buy protection {money(c['notional_c'])} at {num_text(terms.get('deal_spread_bp'))} bp"
        else:
            if terms.get("pay_fixed") is not True or pt.get("pay_fixed") is not True:
                p.append(f"side: pay_fixed {terms.get('pay_fixed')}")
            if not (_eq_dec(terms.get("fixed_rate"), t["fixed_rate"]) and _eq_dec(pt.get("fixed_rate"), t["fixed_rate"])):
                p.append(f"fixed rate {terms.get('fixed_rate')} (payload {pt.get('fixed_rate')})")
            want_n = int(Decimal(t["notional"]) * 100)
            if int(c["notional_c"]) != want_n or not _eq_dec(pt.get("notional"), t["notional"]):
                p.append(f"notional {money(c['notional_c'])} (payload {pt.get('notional')})")
            if int(terms.get("tenor_months") or 0) != t["tenor_months"]:
                p.append(f"tenor {terms.get('tenor_months')} months")
            for f_ in ("fixed_months", "fixed_basis", "float_index", "float_basis", "float_months", "fixing", "calibrate_curve_spread_to_rate"):
                if f_ in pt and terms.get(f_) != pt.get(f_):
                    p.append(f"{f_} {terms.get(f_)} (payload {pt.get(f_)})")
            desc = f"pay fixed {Decimal(terms.get('fixed_rate')) * 100:.3f} % on {money(c['notional_c'])}, {terms.get('tenor_months')} months"
        # the command that booked it is exactly the batch's
        cmd = res.execute("SELECT kind, actor, payload_json FROM commands WHERE id=?", (c["command_id"],)).fetchone()
        if cmd is None or cmd["kind"] != "otc.book_as_of" or jload(cmd["payload_json"], {}) != pl:
            p.append("the booking command's payload differs from the batch's (or the command is missing)")
        lab = (il.get(n) or {}).get("source_label") or ""
        if not OTC_LABEL_RX.match(lab):
            p.append(f"label {lab!r}")
        if p:
            ck.fail(f"line {n} {c['contract_id']}: " + "; ".join(p))
        else:
            ck.ev(f"line {n} {c['contract_id']} {desc}; trade / value / start 2026-10-21, executed {et_text(terms.get('dealt_ts'))}, entered"
                  f" {et_text(c.get('entered_ts'))}; booking AS_OF, CONFIRMED; = the batch's payload and the plan's table")
        booked[n] = {"contract_id": c["contract_id"], "row": c, "terms": terms}
    # --- options (lines 29-31 their own command; 32-33 the package)
    pkg_ids = set()
    for n, (occ, side, qty, px, und, right, strike) in OPT_TABLE.items():
        ln = batch.lines.get(n) or {}
        key = ln.get("idem_key")
        kind, pl = batch.payload_of(n)
        want_kind = "trade.book_as_of_package" if n in (32, 33) else "trade.book_as_of"
        if ln.get("decision") != "import" or kind != want_kind or not pl:
            ck.fail(f"line {n}: the batch does not carry a {want_kind} import (decision {ln.get('decision')}, command {kind})")
            continue
        orders = [dict(r) for r in res.execute("SELECT * FROM orders WHERE import_key=?", (key,)).fetchall()]
        fills = [dict(r) for r in res.execute("SELECT * FROM fills WHERE import_key=?", (key,)).fetchall()]
        if len(orders) != 1 or len(fills) != 1:
            ck.fail(f"line {n}: {len(orders)} order(s) and {len(fills)} fill(s) carry its import key {key} (1 and 1 expected)")
            continue
        o, f = orders[0], fills[0]
        p = []
        if pl.get("instrument_id") != occ or o["instrument_id"] != occ or f["instrument_id"] != occ:
            p.append(f"instrument {o['instrument_id']} (payload {pl.get('instrument_id')})")
        if o["account_id"] != OPT_ACCOUNT or f["account_id"] != OPT_ACCOUNT or pl.get("account_id") != OPT_ACCOUNT:
            p.append(f"account {o['account_id']}")
        if o["side"] != side or pl.get("side") != side or f["side"] != ("BUY" if side == "BTO" else "SELL"):
            p.append(f"side {o['side']} / fill {f['side']} (payload {pl.get('side')})")
        if int(o["qty"]) != qty or int(o["filled_qty"]) != qty or int(f["qty"]) != qty or int(pl.get("qty") or 0) != qty:
            p.append(f"quantity {o['qty']} / filled {o['filled_qty']} / fill {f['qty']} (payload {pl.get('qty')})")
        if not (_eq_dec(f["price"], px) and _eq_dec(o.get("avg_px"), px) and _eq_dec(o.get("limit_px"), px) and _eq_dec(pl.get("price"), px)):
            p.append(f"price {f['price']} / avg {o.get('avg_px')} / limit {o.get('limit_px')} (payload {pl.get('price')})")
        if o["order_type"] != "LIMIT" or o["tif"] != "DAY" or o["status"] != "FILLED":
            p.append(f"order {o['order_type']} {o['tif']} {o['status']}")
        if o.get("booking") != "AS_OF" or f.get("booking") != "AS_OF":
            p.append(f"booking {o.get('booking')} / {f.get('booking')}")
        if f["trade_date"] != OPT_TRADE_DATE or (pl.get("as_of") or {}).get("trade_date") != OPT_TRADE_DATE:
            p.append(f"trade date {f['trade_date']}")
        if int(f["ts"]) != opt_exec or int(o["created_ts"]) != opt_exec:
            p.append(f"executed {et_text(f['ts'])} (order created {et_text(o['created_ts'])}; the plan 2026-10-22 16:00:00 ET)")
        if f.get("settle_date") != OPT_SETTLE_DATE:
            p.append(f"settle date {f.get('settle_date')}")
        if (pl.get("as_of") or {}).get("historical_model"):
            p.append("payload historical_model true")
        inst = res.execute("SELECT * FROM instruments WHERE instrument_id=?", (occ,)).fetchone()
        spec = pl.get("instrument_spec") or {}
        if inst is None:
            p.append("no instrument row")
        else:
            inst = dict(inst)
            if (inst["kind"] != "OPTION" or inst["underlying_id"] != und or inst["expiry"] != OPT_EXPIRY or inst["right"] != right
                    or not _eq_dec(inst["strike"], strike) or not _eq_dec(inst["multiplier"], 100)):
                p.append(f"instrument {inst['kind']} {inst['underlying_id']} {inst['expiry']} {inst['right']} {inst['strike']} x{inst['multiplier']}")
            if (spec.get("underlying_id") != und or spec.get("expiry") != OPT_EXPIRY or spec.get("right") != right
                    or not _eq_dec(spec.get("strike"), strike) or int(spec.get("multiplier") or 0) != 100):
                p.append("the batch's instrument_spec differs from the plan's table")
        st = res.execute("SELECT due_date, status, amount_c, fail_state FROM settlements WHERE fill_id=?", (f["fill_id"],)).fetchall() \
            if "fail_state" in [c["name"] for c in table_info(res, "settlements")] else \
            res.execute("SELECT due_date, status, amount_c, NULL AS fail_state FROM settlements WHERE fill_id=?", (f["fill_id"],)).fetchall()
        if not st or any(s["due_date"] != OPT_SETTLE_DATE for s in st):
            p.append(f"settlement {[dict(s) for s in st]}")
        lab = (il.get(n) or {}).get("source_label") or ""
        if not OPT_LABEL_RX.match(lab):
            p.append(f"label {lab!r}")
        # the command that booked it carries exactly the batch's payload (the Cboe chain row aside: the batch holds a placeholder)
        rid = ((batch.lines.get(n) or {}).get("command") or {}).get("request_id") if n not in (32, 33) else \
            ((batch.packages[0] if batch.packages else {}).get("command") or {}).get("request_id")
        bpl = ((batch.lines.get(n) or {}).get("command") or {}).get("payload") if n not in (32, 33) else \
            (((batch.packages[0] if batch.packages else {}).get("command") or {}).get("payload"))
        cmd = res.execute("SELECT kind, payload_json FROM commands WHERE request_id=?", (rid,)).fetchone() if rid else None
        if cmd is None or cmd["kind"] != want_kind or without_chain_rows(jload(cmd["payload_json"], {})) != without_chain_rows(bpl):
            p.append(f"the booking command {rid} is missing or its payload differs from the batch's")
        if n in (32, 33):
            pkg_ids.add(o.get("package_id"))
        if p:
            ck.fail(f"line {n} {o['order_id']} / {f['fill_id']}: " + "; ".join(p))
        else:
            ck.ev(f"line {n} {o['order_id']} / {f['fill_id']} {side} {qty} {occ} at {px}: traded {et_text(f['ts'])} (trade date {f['trade_date']}),"
                  f" settles {f.get('settle_date')} ({st[0]['status']}); premium {money(f['gross_c'])}, fee {money(f['fee_c'])}; booking AS_OF;"
                  f" entered {et_text(o.get('entered_ts'))}")
        booked[n] = {"order": o, "fill": f, "settle": [dict(s) for s in st]}
    # the package: one hedge_packages row holding both QQQ legs
    if len(pkg_ids) != 1 or None in pkg_ids:
        ck.fail(f"lines 32-33: the two QQQ legs are not grouped as one package ({sorted(str(x) for x in pkg_ids)})")
    else:
        pid = next(iter(pkg_ids))
        hp = res.execute("SELECT * FROM hedge_packages WHERE package_id=?", (pid,)).fetchone()
        pkg = batch.packages[0] if batch.packages else {}
        want_key = ((pkg.get("command") or {}).get("payload") or {}).get("package", {}).get("strategy_key")
        if hp is None or hp["strategy_key"] != want_key:
            ck.fail(f"lines 32-33: package {pid} is {dict(hp) if hp else None}, expected strategy {want_key}")
        else:
            ck.ev(f"lines 32-33 grouped as one package {pid} ({hp['strategy_key']}, {hp['status']}); the long 700 put booked before the short 675")
    # nothing else was booked: the new orders / fills / contracts are exactly these
    new = {}
    for table, idc in (("orders", "order_id"), ("fills", "fill_id"), ("otc_contracts", "contract_id")):
        s_ids = {r[0] for r in src.execute(f"SELECT {idc} FROM {table} WHERE account_id LIKE ?", (lid + ":%",)).fetchall()}
        r_ids = {r[0] for r in res.execute(f"SELECT {idc} FROM {table} WHERE account_id LIKE ?", (lid + ":%",)).fetchall()}
        new[table] = sorted(r_ids - s_ids)
    exp_orders = sorted(b["order"]["order_id"] for b in booked.values() if "order" in b)
    exp_fills = sorted(b["fill"]["fill_id"] for b in booked.values() if "fill" in b)
    exp_otc = sorted(b["contract_id"] for b in booked.values() if "contract_id" in b)
    if new["orders"] != exp_orders or new["fills"] != exp_fills or new["otc_contracts"] != exp_otc:
        ck.fail(f"rows booked beyond the ten lines: new orders {new['orders']}, fills {new['fills']}, contracts {new['otc_contracts']}")
    else:
        ck.ev(f"nothing else booked: new F1 orders {len(exp_orders)}, fills {len(exp_fills)}, OTC contracts {len(exp_otc)} = the ten lines")
    # lines 34 / 35 never booked; every label honest
    for n in (34, 35):
        ln = batch.lines.get(n) or {}
        row = il.get(n) or {}
        if ln.get("decision") not in ("declined", "unresolved") or (row and row.get("action") not in ("declined (never booked)", "unresolved (not a transaction)")):
            ck.fail(f"line {n}: decision {ln.get('decision')}, action {row.get('action')}")
    hm = [n for n, r in il.items() if r.get("historical_model")]
    if hm:
        ck.fail(f"lines {hm} carry historical_model (this batch has none)")
    banned = [(n, r.get("source_label")) for n, r in il.items() if BANNED_WORDS.search(str(r.get("source_label") or ""))]
    if banned:
        ck.fail("labels with banned words: " + "; ".join(f"line {n}: {lab!r}" for n, lab in banned))
    else:
        ck.ev(f"labels: lines 24-28 the D1 OTC as-of label, 29-33 the D1 option as-of label; {len(il)} import_lines, none historical_model, none"
              " says modeled / modelled / hypothetical / estimated; lines 34 declined and 35 unresolved never booked")
    return ck


# ---- 3. CASH -----------------------------------------------------------------------------------------------------------------------
def cash_table(conn, lid: str, d0: str, d1: str) -> Dict[str, Dict[str, Dict[str, int]]]:
    """{account: {currency: {date: business-date settled cash (GL 1010, natural = storage sign for an asset)}}}."""
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    accts = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (lid,)).fetchall()]
    rows = conn.execute("SELECT l.account_id, l.currency, e.business_date, SUM(l.amount_c) FROM journal_lines l JOIN journal_entries e"
                        " ON e.entry_id = l.entry_id WHERE l.gl='1010' AND l.account_id LIKE ? GROUP BY l.account_id, l.currency, e.business_date",
                        (lid + ":%",)).fetchall()
    by: Dict[Tuple[str, str], List[Tuple[str, int]]] = {}
    for a, ccy, bd, amt in rows:
        by.setdefault((a, ccy), []).append((bd, int(amt)))
    for a in accts:
        ccys = sorted({c for (aa, c) in by if aa == a}) or ["USD"]
        for ccy in ccys:
            pts = sorted(by.get((a, ccy), []))
            series, run, i = {}, 0, 0
            for d in days(d0, d1):
                while i < len(pts) and pts[i][0] <= d:
                    run += pts[i][1]
                    i += 1
                series[d] = run
            out.setdefault(a, {})[ccy] = series
    return out


def current_cash(conn, lid: str) -> Dict[Tuple[str, str], Tuple[int, int]]:
    j = {(r[0], r[1]): int(r[2]) for r in conn.execute("SELECT account_id, currency, SUM(amount_c) FROM journal_lines WHERE gl='1010' AND"
                                                       " account_id LIKE ? GROUP BY account_id, currency", (lid + ":%",)).fetchall()}
    g = {(r[0], r[1]): int(r[2]) for r in conn.execute("SELECT account_id, currency, SUM(balance_c) FROM gl_balances WHERE gl='1010' AND"
                                                       " account_id LIKE ? GROUP BY account_id, currency", (lid + ":%",)).fetchall()}
    return {k: (j.get(k, 0), g.get(k, 0)) for k in set(j) | set(g)}


def check_cash(batch: Batch, src, res, rep: dict, tables: dict) -> Check:
    ck = Check("3", "CASH: every F1 account >= 0 at the close of every date through the day before the run, and every current balance >= 0")
    lid = batch.lifetime
    run_day = str(rep.get("started_et") or "")[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}$", run_day):
        ck.fail("the dry run's start time is unavailable, so the date range cannot be set")
        return ck
    through = (date.fromisoformat(run_day) - timedelta(days=1)).isoformat()
    d0 = batch.window_from
    after = cash_table(res, lid, d0, through)
    before = cash_table(src, lid, d0, through)
    tables.update({"before": before, "after": after, "from": d0, "through": through, "run_day": run_day})
    neg = [(a, ccy, d, v) for a, by in after.items() for ccy, s in by.items() for d, v in s.items() if v < 0]
    for a, ccy, d, v in neg[:20]:
        ck.fail(f"{a} {ccy} closes {d} at {money(v)}")
    cur = current_cash(res, lid)
    for (a, ccy), (jv, gv) in sorted(cur.items()):
        if jv < 0:
            ck.fail(f"{a} {ccy} current settled cash {money(jv)}")
        if jv != gv:
            ck.fail(f"{a} {ccy} current settled cash: journal {money(jv)} vs stored balance {money(gv)}")
    n_accts = len(after)
    if not neg:
        ck.ev(f"{n_accts} F1 accounts x {len(days(d0, through))} dates ({d0} .. {through}): every close >= 0 (computed here from GL 1010 by"
              " business date)")
    ck.ev("current settled cash: " + ", ".join(f"{a} {money(jv)}" for (a, ccy), (jv, gv) in sorted(cur.items()) if jv) + " (all others 0.00)")
    # agreement with the dry run's own table (information: the checker's figures are its own)
    rt = (rep.get("cash_table") or {})
    diffs = []
    for which, mine in (("after", after), ("before", before)):
        theirs = rt.get(which) or {}
        for a, by in mine.items():
            for d, v in (by.get("USD") or {}).items():
                t = (theirs.get(a) or {}).get(d)
                if t is not None and int(t) != v:
                    diffs.append(f"{which} {a} {d}: here {money(v)}, dry run {money(int(t))}")
    if diffs:
        ck.ev("NOTE the dry run's own table differs: " + "; ".join(diffs[:5]))
    elif rt:
        ck.ev("the dry run's own business-date table (before and after) equals this one to the cent")
    return ck


# ---- 4. INTEGRITY ------------------------------------------------------------------------------------------------------------------
def check_integrity(batch: Batch, res, rep: dict, tb_out: dict) -> Check:
    ck = Check("4", "INTEGRITY: integrity full ok and the trial balance balanced")
    lid = batch.lifetime
    ic = res.execute("PRAGMA integrity_check").fetchone()[0]
    if ic != "ok":
        ck.fail(f"PRAGMA integrity_check: {ic}")
    fk = res.execute("PRAGMA foreign_key_check").fetchall()
    # the integrity-full result the dry run recorded in the result database
    row = res.execute("SELECT status, report_json FROM import_batches WHERE batch_id=?", (batch.batch_id,)).fetchone() \
        if table_exists(res, "import_batches") else None
    rj = jload(row["report_json"], {}) if row else {}
    c6 = (rj or {}).get("C6") or {}
    integ = c6.get("integrity") or {}
    if not row:
        ck.fail("the result database has no import_batches row for the batch")
    elif row["status"] != "DONE":
        ck.fail(f"the batch's recorded status is {row['status']} (DONE expected)")
    if integ.get("ok") is not True or integ.get("failing"):
        ck.fail(f"integrity full (recorded): ok {integ.get('ok')}, failing {integ.get('failing')}")
    rep_integ = ((rep.get("C6") or {}).get("integrity") or {})
    if rep and (rep_integ.get("ok") is not True or rep.get("status") != "DONE"):
        ck.fail(f"the dry run's report: status {rep.get('status')}, integrity ok {rep_integ.get('ok')}")
    warns = [f"{w.get('id')} {w.get('scope')}: {w.get('detail')}" for w in integ.get("warnings") or []]
    # the trial balance and the ledger, computed here
    accts = [r[0] for r in res.execute("SELECT account_id FROM accounts WHERE lifetime_id=?", (lid,)).fetchall()]
    q = ",".join("?" for _ in accts)
    by_ccy = {r[0]: (int(r[1]), int(r[2])) for r in res.execute(f"SELECT currency, SUM(amount_c), SUM(base_c) FROM journal_lines WHERE account_id IN ({q})"
                                                               " GROUP BY currency", accts).fetchall()}
    base_total = sum(b for _a, b in by_ccy.values())
    balanced = base_total == 0 and all(a == 0 for a, _b in by_ccy.values())
    tb_out.update({"by_currency": {k: {"amount_c": a, "base_c": b} for k, (a, b) in by_ccy.items()}, "balanced": balanced})
    if not balanced:
        ck.fail(f"trial balance not balanced: {tb_out['by_currency']}")
    unbal = res.execute(f"SELECT entry_id, SUM(base_c) FROM journal_lines WHERE entry_id IN (SELECT entry_id FROM journal_lines WHERE account_id IN ({q}))"
                        " GROUP BY entry_id HAVING SUM(base_c) <> 0", accts).fetchall()
    if unbal:
        ck.fail(f"{len(unbal)} journal entr(y/ies) do not balance, e.g. {[tuple(r) for r in unbal[:3]]}")
    jb = {(r[0], r[1], r[2], r[3] or ""): (int(r[4]), int(r[5])) for r in res.execute(
        f"SELECT account_id, gl, currency, COALESCE(instrument_id, ''), SUM(amount_c), SUM(base_c) FROM journal_lines WHERE account_id IN ({q})"
        " GROUP BY account_id, gl, currency, COALESCE(instrument_id, '')", accts).fetchall()}
    gb = {(r[0], r[1], r[2], r[3] or ""): (int(r[4]), int(r[5])) for r in res.execute(
        f"SELECT account_id, gl, currency, instrument_id, balance_c, base_c FROM gl_balances WHERE account_id IN ({q})", accts).fetchall()}
    mism = [k for k in set(jb) | set(gb) if jb.get(k, (0, 0)) != gb.get(k, (0, 0))]
    if mism:
        # gl_balances may hold one row per (account, gl, currency) where the journal splits by instrument: compare without the instrument
        j2: Dict[tuple, Tuple[int, int]] = {}
        g2: Dict[tuple, Tuple[int, int]] = {}
        for src_, dst in ((jb, j2), (gb, g2)):
            for (a, g, c, _i), (x, y) in src_.items():
                o = dst.get((a, g, c), (0, 0))
                dst[(a, g, c)] = (o[0] + x, o[1] + y)
        mism2 = [k for k in set(j2) | set(g2) if j2.get(k, (0, 0)) != g2.get(k, (0, 0))]
        if mism2:
            ck.fail(f"stored balances differ from the journal for {len(mism2)} (account, GL, currency), e.g. {sorted(mism2)[:3]}")
    # assets = liabilities + equity (+ income - expense) is the balanced journal; the chart must know every GL used
    unknown = res.execute(f"SELECT DISTINCT gl FROM journal_lines WHERE account_id IN ({q}) AND gl NOT IN (SELECT gl FROM chart)", accts).fetchall()
    if unknown:
        ck.fail(f"journal lines on GLs the chart does not hold: {[r[0] for r in unknown]}")
    if ck.ok:
        n_e = res.execute(f"SELECT COUNT(DISTINCT entry_id), COUNT(*) FROM journal_lines WHERE account_id IN ({q})", accts).fetchone()
        ck.ev(f"integrity full ok (recorded by the dry run: failing none); PRAGMA integrity_check ok; foreign key problems {len(fk)}")
        ck.ev(f"trial balance computed here: {', '.join(f'{k} {money(a)} (base {money(b)})' for k, (a, b) in by_ccy.items())} - balanced;"
              f" {n_e[0]:,} F1 entries / {n_e[1]:,} lines each balance; stored GL balances = the journal")
    if warns:
        ck.ev("integrity warnings (not failures): " + "; ".join(warns[:6]))
    return ck


# ---- 5. RESIDUAL -------------------------------------------------------------------------------------------------------------------
def check_residual(batch: Batch, src, res, rows_out: list) -> Check:
    ck = Check("5", "RESIDUAL: the restatement residual is 0 for every account and F1:ALL on every date")
    lid = batch.lifetime
    if not table_exists(res, "nav_snapshot_versions"):
        ck.fail("the result has no nav_snapshot_versions table")
        return ck
    rows = [dict(r) for r in res.execute("SELECT * FROM nav_snapshot_versions WHERE batch_id=? AND account_id LIKE ? ORDER BY snap_date, account_id, version",
                                         (batch.batch_id, lid + ":%")).fetchall()]
    if not rows:
        ck.fail("no restated version rows for the batch")
        return ck
    latest: Dict[Tuple[str, str], dict] = {}
    for r in rows:
        k = (r["account_id"], r["snap_date"])
        if k not in latest or r["version"] > latest[k]["version"]:
            latest[k] = r
    orig = {(r["account_id"], r["snap_date"]): int(r["nav_c"]) for r in src.execute("SELECT account_id, snap_date, nav_c FROM nav_snapshots WHERE account_id LIKE ?",
                                                                                     (lid + ":%",)).fetchall()}
    bad = []
    for r in rows:
        imp = jload(r.get("import_effect_json"), []) or []
        mp = jload(r.get("mark_policy_json"), []) or []
        dt = jload(r.get("dating_json"), []) or []
        imp_sum = sum(int(x.get("amount_c") or 0) for x in imp)
        total = int(r["original_nav_c"]) + imp_sum + int(r["interest_effect_c"]) + int(r["mark_policy_c"]) + int(r["dating_c"]) + int(r["residual_c"])
        tag = f"{r['account_id']} {r['snap_date']} v{r['version']}"
        if int(r["residual_c"]) != 0:
            bad.append(f"{tag}: residual {money(r['residual_c'])}")
        if total != int(r["nav_c"]):
            bad.append(f"{tag}: original + import + interest + mark policy + dating + residual = {money(total)} but restated {money(r['nav_c'])}")
        if mp and sum(int(x.get("amount_c") or 0) for x in mp) != int(r["mark_policy_c"]):
            bad.append(f"{tag}: mark policy by position does not add up to {money(r['mark_policy_c'])}")
        o = orig.get((r["account_id"], r["snap_date"]))
        if o is None and not (int(r["original_nav_c"]) == 0 and r.get("added")):
            bad.append(f"{tag}: no original snapshot, but original {money(r['original_nav_c'])} / added {r.get('added')}")
        if o is not None and int(r["original_nav_c"]) != o:
            bad.append(f"{tag}: original {money(r['original_nav_c'])} vs the source snapshot {money(o)}")
        if r["account_id"] == f"{lid}:ALL":
            fund = [x for x in imp if x.get("key") == "funding"]
            if fund and int(fund[0].get("amount_c") or 0) != 0:
                bad.append(f"{tag}: the funding group nets to {money(fund[0].get('amount_c'))} at the lifetime level (0 expected)")
        if int(r["dating_c"]) != 0 and not dt:
            bad.append(f"{tag}: a dating effect of {money(r['dating_c'])} without its listed entries")
    missing = sorted(k for k in orig if k not in latest)
    if missing:
        bad.append(f"{len(missing)} original snapshot(s) without a restated version, e.g. {missing[:4]}")
    dates = sorted({d for _a, d in latest})
    for d in dates:
        allr = latest.get((f"{lid}:ALL", d))
        parts = [v for (a, dd), v in latest.items() if dd == d and a != f"{lid}:ALL"]
        if allr is None:
            bad.append(f"{d}: no {lid}:ALL version")
        elif sum(int(p["nav_c"]) for p in parts) != int(allr["nav_c"]):
            bad.append(f"{d}: {lid}:ALL {money(allr['nav_c'])} vs the sum of its accounts {money(sum(int(p['nav_c']) for p in parts))}")
    for b in bad[:25]:
        ck.fail(b)
    if len(bad) > 25:
        ck.fail(f"... and {len(bad) - 25} more")
    added = sorted(f"{a} {d}" for (a, d), v in latest.items() if v.get("added"))
    if not bad:
        ck.ev(f"{len(rows)} version rows ({len({a for a, _d in latest})} accounts incl. {lid}:ALL x {len(dates)} dates {dates[0]} .. {dates[-1]}):"
              " every residual 0.00; each split adds up (original + import by line + interest + mark policy + dating = restated); every"
              f" original snapshot has its version; {lid}:ALL = the sum of its accounts on every date; the funding group nets to 0 at {lid}:ALL")
        if added:
            ck.ev("version rows added where no original existed (original 0): " + ", ".join(added))
    rows_out.extend(rows)
    return ck


# ---- 6. IDEMPOTENT -----------------------------------------------------------------------------------------------------------------
COUNT_TABLES = ("journal_entries", "journal_lines", "orders", "fills", "lots", "otc_contracts", "otc_vm", "transfers", "settlements",
                "nav_snapshot_versions", "nav_snapshots", "borrows", "hedge_packages", "marks")


def check_idempotent(batch: Batch, res, first_dir: str, second_dir: Optional[str], first_wal: Optional[int]) -> Check:
    ck = Check("6", "IDEMPOTENT: a second dry run on the result posts 0 entries, and the -wal file is empty")
    if not second_dir:
        ck.ran = False
        ck.ev("not run: no --second folder was given (the apply day relies on the build-day and fix-round runs, 15.6 item 3)")
        return ck
    rep2 = load_report(second_dir)
    db2 = os.path.join(second_dir, "terminal.db")
    wal2 = file_size(db2 + "-wal")
    if first_wal:
        ck.fail(f"the first dry run's -wal file holds {first_wal:,} bytes")
    if wal2:
        ck.fail(f"the second dry run's -wal file holds {wal2:,} bytes")
    if not rep2:
        ck.fail("the second dry run's report.json was not found")
        return ck
    if rep2.get("status") != "DONE":
        ck.fail(f"the second dry run's status is {rep2.get('status')}: {json.dumps(rep2.get('refused'))[:300]}")
    if int(rep2.get("entries_posted", -1)) != 0:
        ck.fail(f"the second dry run posted {rep2.get('entries_posted')} entries")
    src2 = os.path.normcase(os.path.abspath(str(rep2.get("source_db") or "")))
    first_db = os.path.normcase(os.path.abspath(os.path.join(first_dir, "terminal.db")))
    if src2 != first_db:
        ck.fail(f"the second dry run did not run on the first one's result (its source is {rep2.get('source_db')})")
    elif rep2.get("source_sha256_before") != sha256_file(os.path.join(first_dir, "terminal.db")):
        ck.fail("the first dry run's result changed since the second dry run copied it")
    sts = {int(r["line_no"]): r.get("status") for r in rep2.get("lines") or []}
    nb = [n for n in range(24, 34) if sts.get(n) != "already_imported"]
    if nb:
        ck.fail(f"second run: lines {nb} are not already_imported ({[sts.get(n) for n in nb]})")
    if rep2.get("funding_moves"):
        ck.fail(f"second run: {len(rep2['funding_moves'])} new funding move(s)")
    try:
        c2, _info = open_ro(db2)
    except Exception as e:   # noqa: BLE001 - reported
        ck.fail(f"cannot open the second dry run's database: {e}")
        return ck
    try:
        diffs = []
        for t in COUNT_TABLES:
            if table_exists(res, t) and table_exists(c2, t):
                a = res.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                b = c2.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if a != b:
                    diffs.append(f"{t} {a} -> {b}")
        me1 = res.execute("SELECT COALESCE(MAX(entry_id), 0) FROM journal_entries").fetchone()[0]
        me2 = c2.execute("SELECT COALESCE(MAX(entry_id), 0) FROM journal_entries").fetchone()[0]
        if me1 != me2:
            diffs.append(f"max entry id {me1} -> {me2}")
    finally:
        c2.close()
    for d in diffs:
        ck.fail(f"the second dry run added rows: {d}")
    if ck.ok:
        ck.ev(f"second dry run {rep2.get('started_et')}: status DONE, entries posted 0, lines 24-33 already_imported, no funding move; row counts"
              f" equal in {len(COUNT_TABLES)} tables (journal max entry id {me1}); -wal files {first_wal or 0} and {wal2 or 0} bytes")
    return ck


# ---- the owner's summary ----------------------------------------------------------------------------------------------------------
STATUS_WORDS = {"present_differs": "status: already in your book, price differs from the workbook",
                "present_match": "status: already in your book, matches the workbook",
                "missing_import": "status: was missing", "already_imported": "status: already imported",
                "declined": "status: declined", "unresolved": "status: unresolved"}


def status_words(s: Optional[str]) -> str:
    return STATUS_WORDS.get(str(s), f"status: {s or 'unknown'}")


def _side_words(ln: dict) -> str:
    s = str(ln.get("side") or "")
    return {"BUY": "long", "SELL": "short", "SELL_SHORT": "short", "BTO": "buy", "STO": "sell"}.get(s, s.lower())


def figures(batch: Batch, res, rep: dict, tables: dict, booked: dict) -> Dict[str, Optional[int]]:
    """The dry run's figures that the plan estimated (section 7 / 15.6), computed from the result database."""
    f: Dict[str, Optional[int]] = {k: None for k, *_ in PLAN_ESTIMATES}
    otc = {n: b for n, b in booked.items() if "contract_id" in b}
    opt = {n: b for n, b in booked.items() if "fill" in b}
    if otc:
        f["im_total"] = sum(int(b["row"]["im_c"]) for b in otc.values())
    if opt:
        f["opt_paid"] = sum(int(b["fill"]["gross_c"]) for b in opt.values() if b["fill"]["side"] == "BUY")
        f["opt_received"] = sum(int(b["fill"]["gross_c"]) for b in opt.values() if b["fill"]["side"] == "SELL")
        f["opt_fees"] = sum(int(b["fill"]["fee_c"]) for b in opt.values())
        f["opt_net"] = f["opt_received"] - f["opt_paid"] - f["opt_fees"]
    cdx_ids = [b["contract_id"] for b in otc.values() if b["row"]["kind"] == "CDX"]
    if cdx_ids:
        q = ",".join("?" for _ in cdx_ids)
        f["cdx_initial"] = int(res.execute(f"SELECT COALESCE(SUM(amount_c), 0) FROM settlements WHERE ref_type='otc' AND ref_id IN ({q})", cdx_ids).fetchone()[0])
    vm = {"TRS": 0, "CDX": 0, "IRS": 0}
    for b in otc.values():
        v = res.execute("SELECT COALESCE(SUM(delta_c), 0) FROM otc_vm WHERE contract_id=? AND vm_date<='2026-10-26'", (b["contract_id"],)).fetchone()[0]
        vm[b["row"]["kind"]] = vm.get(b["row"]["kind"], 0) + int(v)
    if otc:
        f["vm_trs"], f["vm_cdx"], f["vm_irs"] = vm["TRS"], vm["CDX"], vm["IRS"]
        f["vm_net"] = vm["TRS"] + vm["CDX"] + vm["IRS"]

    def bal_at(acct: str, gl: str, d: str) -> int:
        return int(res.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE"
                               " l.account_id=? AND l.gl=? AND e.business_date<=?", (acct, gl, d)).fetchone()[0])
    pb = f"{batch.lifetime}:PB_GS"
    # natural signs: 2330 / 2520 liabilities (credit = negative storage), 1320 an asset
    f["restricted_1026"] = max(0, -bal_at(pb, "2330", "2026-10-26") - bal_at(pb, "1320", "2026-10-26")) - bal_at(pb, "2520", "2026-10-26")
    tsy = f"{batch.lifetime}:TREASURY"
    mid = ((rep.get("cash_table") or {}).get("before_sweeps") or {}).get(tsy) or {}
    f["tsy_1022_before"] = int(mid["2026-10-22"]) if "2026-10-22" in mid else None
    aft = ((tables.get("after") or {}).get(tsy) or {}).get("USD") or {}
    f["tsy_1022_after"] = aft.get("2026-10-22")
    f["pb_sweep"] = int(res.execute("SELECT COALESCE(SUM(amount_c), 0) FROM transfers WHERE linked_type='asof_sweep' AND from_account=? AND"
                                    " business_date='2026-10-22'", (pb,)).fetchone()[0])
    fills = [b["fill"]["fill_id"] for b in opt.values()]
    sids: List[str] = []
    if fills:
        q = ",".join("?" for _ in fills)
        sids = [r[0] for r in res.execute(f"SELECT settle_id FROM settlements WHERE fill_id IN ({q})", fills).fetchall()]
    if sids:
        q = ",".join("?" for _ in sids)
        f["settle_sweeps"] = int(res.execute(f"SELECT COALESCE(SUM(amount_c), 0) FROM transfers WHERE linked_type='settlement' AND linked_id IN ({q})",
                                             sids).fetchone()[0])
    pl_gls = {r[0] for r in res.execute("SELECT gl FROM chart WHERE kind IN ('INCOME', 'EXPENSE')").fetchall()}

    def reaccrued(acct: str, kind: str) -> int:
        tot = 0
        for eid, key in res.execute("SELECT entry_id, idem_key FROM journal_entries WHERE idem_key LIKE ?", (f"reaccrue:{batch.batch_id}:{acct}:{kind}:%",)):
            night = key.rsplit(":", 1)[-1]
            if night <= "2026-10-26":
                tot -= sum(int(a) for g, a in res.execute("SELECT gl, amount_c FROM journal_lines WHERE entry_id=?", (eid,)).fetchall() if g in pl_gls)
        return tot
    f["int_tsy"] = reaccrued(tsy, "cashint")
    f["int_pb"] = reaccrued(pb, "pbcredit") + reaccrued(pb, "pb")
    v = latest_versions(batch, res)
    a23 = v.get((f"{batch.lifetime}:ALL", "2026-10-23"))
    if a23:
        f["nav_1023_ex_dating"] = int(a23["nav_c"]) - int(a23["dating_c"])
    a26 = v.get((f"{batch.lifetime}:ALL", "2026-10-26"))
    if a26:
        f["nav_1026"] = int(a26["nav_c"])
    return f


def latest_versions(batch: Batch, res) -> Dict[Tuple[str, str], dict]:
    out: Dict[Tuple[str, str], dict] = {}
    if not table_exists(res, "nav_snapshot_versions"):
        return out
    for r in res.execute("SELECT * FROM nav_snapshot_versions WHERE batch_id=? ORDER BY version", (batch.batch_id,)).fetchall():
        out[(r["account_id"], r["snap_date"])] = dict(r)
    return out


def write_summary(path: str, batch: Batch, args, checks: List[Check], src, res, rep: dict, tables: dict, booked: dict, src_info: dict,
                  verdict: bool, rep2: dict) -> None:
    lid = batch.lifetime
    L: List[str] = []
    w = L.append
    started = str(rep.get("started_et") or "unavailable")
    w(f"DRY RUN OF YOUR IMPORT {batch.batch_id}: {'PASSED' if verdict else 'NOT PASSED'} (run {started[:16]} ET on a copy; your live database was not changed)")
    w("")
    w("This report was written by the import checker (tests\\terminal_import_check.py). It read the databases without changing them and")
    w("computed its own figures from them; the apply runs only when all five of your conditions below say PASS.")
    w("")
    w("YOUR FIVE CONDITIONS")
    words = {"1": "Your existing trades and records are untouched",
             "2": "The new trades are booked at exactly your terms",
             "3": "No account goes below zero on any day",
             "4": "The books are intact (integrity check and trial balance)",
             "5": "Every restated NAV row ties to the cent (residual 0)",
             "6": "Running it a second time changes nothing"}
    for c in checks:
        state = "PASS" if c.ok else ("not run" if not c.ran else "FAIL")
        w(f"  {c.cid}. {words.get(c.cid, c.title)}: {state}")
        for p in c.problems[:4]:
            w(f"       problem: {p}")
    w("")
    # what was run
    w("WHAT WAS RUN")
    srcmeta = rep.get("source_meta") or {}
    w(f"- A fresh copy of your FinSim database: {rep.get('source_db')}")
    w(f"  (sha256 {str(rep.get('source_sha256_before'))[:16]}..., state version {srcmeta.get('state_version')}, last command {srcmeta.get('max_command_id')}).")
    w(f"  The dry run opened it read-only and worked on its own copy in {rep.get('work_dir')}.")
    pin = "the pinned revision 3" if PINNED_SHA256 in (batch.sha256, batch.content_sha256) else "NOT the pinned revision 3"
    w(f"- Your batch file, unchanged: {batch.path} (sha256 {batch.content_sha256[:16]}..., {pin}).")
    pk = rep.get("pack") or {}
    win = pk.get("window") or {}
    w(f"- Market data for {win.get('from')} to {win.get('through')}, fetched {pk.get('fetched_et')} (pack sha256 {str(pk.get('sha256'))[:16]}...).")
    w(f"- The dry run ran {rep.get('started_et')} .. {rep.get('finished_et')} and finished {rep.get('status')}.")
    L_day = (rep.get("preflight") or {}).get("L") or win.get("through")
    est = [r["instrument_id"] for r in res.execute("SELECT instrument_id, note FROM marks WHERE mark_date=? AND kind='CLOSE' AND note LIKE '%an estimate%'",
                                                    (L_day,)).fetchall()] if L_day else []
    if est:
        w(f"- The option marks of {L_day} are that day's last trades, labelled estimates (the official closes were not yet tied to {L_day} when")
        w("  the data was fetched); the import day's own run fetches after 09:45 ET and uses the official closes.")
    for n_ in (rep.get("preflight") or {}).get("notes") or []:
        w(f"- Note: {n_}.")
    w("")
    # 1. every line
    w("1. EVERY LINE")
    rl = {int(r["line_no"]): r for r in rep.get("lines") or []}
    w("PRESERVED untouched (decision preserve): your existing executions. The differences from the workbook are reported, never changed.")
    for n in range(1, 24):
        ln = batch.lines.get(n) or {}
        r = rl.get(n) or {}
        m = ln.get("match") or {}
        vw_t = px_text(m.get("vwap")) if dec(m.get("vwap")) is not None else "?"
        extra = ""
        for d in ln.get("diffs") or []:
            if d.get("field") == "trade_date":
                extra = f"; trade date {d.get('app')} (CME evening fill of {d.get('workbook')}; reported, not changed)"
        w(f"  {n:>2} {ln.get('instrument_id'):<6} {_side_words(ln):<5} {int(ln.get('qty')):>7,} at {vw_t} vs workbook {px_text(ln.get('intended_price'))}"
          f" ({status_words(r.get('status'))}){extra}")
    w("BOOKED as of their dates (decision import; as-of bookings, labelled simulated, entered by the operator at your instruction):")
    for n in range(24, 34):
        ln = batch.lines.get(n) or {}
        b = booked.get(n) or {}
        r = rl.get(n) or {}
        if "contract_id" in b:
            row, t = b["row"], b["terms"]
            vmb = res.execute("SELECT COALESCE(SUM(delta_c), 0) FROM otc_vm WHERE contract_id=?", (b["contract_id"],)).fetchone()[0]
            w(f"  {n:>2} {b['contract_id']} {OTC_TABLE[n]['what']} with Goldman Sachs, value date {row.get('value_date')}: notional {money(row['notional_c'])},"
              f" initial margin {money(row['im_c'])}, variation margin to date {money(int(vmb), True)}"
              + (f", initial payment {money(t.get('initial_payment_c'))} received {t.get('settle_date')}" if row["kind"] == "CDX" else "")
              + (f", contract curve spread {t.get('curve_spread_bp')} bp" if row["kind"] == "IRS" else "")
              + f" ({status_words(r.get('status'))}, now booked)")
        elif "fill" in b:
            fl, o = b["fill"], b["order"]
            occ, side, qty, px = OPT_TABLE[n][:4]
            w(f"  {n:>2} {o['order_id']} / {fl['fill_id']} {side} {qty} {occ} at {px}: premium {money(fl['gross_c'])}"
              f" {'paid' if fl['side'] == 'BUY' else 'received'}, commission {money(fl['fee_c'])}; traded {OPT_TRADE_DATE} 16:00 ET, settled {fl.get('settle_date')}")
        else:
            w(f"  {n:>2} {ln.get('instrument_id')}: NOT BOOKED ({r.get('status') or '?'} / {r.get('action') or '?'})")
    if booked.get(32) and booked.get(33):
        w("     Lines 32 and 33 are one bear put spread (one package).")
    for n in (34, 35):
        ln = batch.lines.get(n) or {}
        w(f"{'DECLINED' if ln.get('decision') == 'declined' else 'UNRESOLVED'}: {n} {ln.get('product_label') or ln.get('instrument_id')} -"
          f" {str(ln.get('decision_reason') or ln.get('note') or '')[:160]}")
    w("")
    # 2. cash
    w("2. CASH BY BUSINESS DATE (settled cash at each day's close; computed by the checker from the ledger)")
    before, after = tables.get("before") or {}, tables.get("after") or {}
    ds = days(tables["from"], tables["through"]) if tables.get("from") else []
    mid = (rep.get("cash_table") or {}).get("before_sweeps") or {}
    w("  " + f"{'account / date':<22}" + "".join(f"{d[5:]:>15}" for d in ds))
    zero = []
    for a in after:
        s_b = (before.get(a) or {}).get("USD") or {}
        s_a = (after.get(a) or {}).get("USD") or {}
        if not any(s_b.get(d) for d in ds) and not any(s_a.get(d) for d in ds) and not any(int((mid.get(a) or {}).get(d) or 0) for d in ds):
            zero.append(a)
            continue
        nm = a.split(":", 1)[1]
        w("  " + f"{nm + ' before':<22}" + "".join(f"{money(s_b.get(d, 0)):>15}" for d in ds))
        if mid.get(a) and any(int(mid[a].get(d) or 0) != s_a.get(d, 0) for d in ds):
            w("  " + f"{nm + ' mid':<22}" + "".join(f"{money(int(mid[a].get(d) or 0)):>15}" for d in ds))
        w("  " + f"{nm + ' after':<22}" + "".join(f"{money(s_a.get(d, 0)):>15}" for d in ds))
    if zero:
        w("  " + ", ".join(z.split(':', 1)[1] for z in zero) + " hold 0.00 throughout.")
    w("  (before = before the import; mid = after the contracts' bookings, before the as-of sweep; after = at the end of the run)")
    w("FUNDING MOVES (every transfer the run created, with its business date)")
    s_ids = {r[0] for r in src.execute("SELECT transfer_id FROM transfers").fetchall()}
    moves = [dict(r) for r in res.execute("SELECT transfer_id, kind, from_account, to_account, amount_c, business_date, linked_type, linked_id FROM transfers"
                                          " ORDER BY business_date, transfer_id").fetchall() if r["transfer_id"] not in s_ids]
    for m in moves:
        why = {"otc": "initial margin", "swapvm": "day-0 variation margin", "asof_sweep": "as-of sweep of the account's import cash",
               "settlement": "option settlement", "asof_funding": "as-of funding transfer"}.get(m["linked_type"], m["linked_type"] or "")
        w(f"  {m['business_date']} {m['transfer_id']} {m['kind']:<9} {m['from_account']} -> {m['to_account']} {money(m['amount_c']):>14}  {why}"
          f" ({m['linked_id']})")
    if not any(m["linked_type"] == "asof_funding" for m in moves):
        w("  No internal as-of funding transfer was needed.")
    w("")
    # 3. restated NAV
    w("3. RESTATED NAV OF THE FUND (original + import + interest + mark policy + dating = restated; residual 0 every day)")
    v = latest_versions(batch, res)
    for d in sorted({d for _a, d in v}):
        r = v.get((f"{lid}:ALL", d))
        if not r:
            continue
        imp = sum(int(x.get("amount_c") or 0) for x in jload(r["import_effect_json"], []) or [])
        w(f"  {d}: {money(r['original_nav_c'])} {money(imp, True)} {money(r['interest_effect_c'], True)} {money(r['mark_policy_c'], True)}"
          f" {money(r['dating_c'], True)} = {money(r['nav_c'])} (residual {money(r['residual_c'])})")
    r23 = v.get((f"{lid}:ALL", WORKBOOK_DATE))
    if r23:
        parts = []
        for x in jload(r23["import_effect_json"], []) or []:
            if not int(x.get("amount_c") or 0):
                continue
            mm = re.match(r"line (\d+)", str(x.get("label") or ""))
            parts.append(f"{'line ' + mm.group(1) if mm else str(x.get('key'))} {money(x.get('amount_c'), True)}")
        w(f"  Import effect by line on {WORKBOOK_DATE}: " + "; ".join(parts))
    accts = sorted({a for a, _d in v})
    dd = sorted({d for _a, d in v})
    w("  By account (orig = your original snapshot, 0 where none existed; new = the restated version):")
    w("  " + f"{'account':<22}" + "".join(f"{d[5:]:>15}" for d in dd))
    for a in accts:
        if all(int((v.get((a, d)) or {}).get("nav_c") or 0) == 0 and int((v.get((a, d)) or {}).get("original_nav_c") or 0) == 0 for d in dd):
            continue
        nm = a.split(":", 1)[1]
        w("  " + f"{nm + ' orig':<22}" + "".join(f"{money(int((v.get((a, d)) or {}).get('original_nav_c') or 0)):>15}" for d in dd))
        w("  " + f"{nm + ' new':<22}" + "".join(f"{money(int((v.get((a, d)) or {}).get('nav_c') or 0)):>15}" for d in dd))
    w("  Your original snapshots are kept; the restated rows are new versions stored beside them.")
    w("")
    # 4. bridge
    w(f"4. BRIDGE TO THE WORKBOOK'S {money(WORKBOOK_NAV_C)} ({WORKBOOK_DATE}; never forced, no balancing entry)")
    if r23:
        gap = batch.summary.get("gap_explained_10_23_A_before_interest_and_mark_policy") or {}
        run = WORKBOOK_NAV_C
        w(f"  {'the workbook (reconstructed on assumed prices)':<72}{money(WORKBOOK_NAV_C):>16}")
        for k, val in gap.items():
            if k == "total":
                continue
            c = cents_of_text(val)
            if c is None:
                continue
            run += c
            w(f"  {k:<72}{money(c, True):>16}")
        for k, c in (("interest restated on business-date balances (this run)", int(r23["interest_effect_c"])),
                     ("mark policy: preserved positions at the day's official closes (this run)", int(r23["mark_policy_c"])),
                     ("dating: entries the original snapshot counted on another date (this run)", int(r23["dating_c"]))):
            run += c
            w(f"  {k:<72}{money(c, True):>16}")
        w(f"  {'left unexplained (rounding of the reconciliation estimates)':<72}{money(int(r23['nav_c']) - run, True):>16}")
        w(f"  {'= restated NAV of ' + WORKBOOK_DATE:<72}{money(r23['nav_c']):>16} ({money(int(r23['nav_c']) - WORKBOOK_NAV_C, True)} vs the workbook)")
    else:
        w("  unavailable: no restated row for the workbook's date")
    w("")
    # 5. vs the plan
    w("5. VERSUS THE PLAN'S ESTIMATES (figures through 10-26)")
    fig = figures(batch, res, rep, tables, booked)
    same, diff = [], []
    for key, what, plan_c, note in PLAN_ESTIMATES:
        got = fig.get(key)
        if got is None:
            diff.append(f"  - {what}: plan {money(plan_c, True)}, this run unavailable")
        elif got == plan_c:
            same.append(f"  - {what}: {money(got, True)}")
        elif abs(got - plan_c) < PLAN_TOLERANCE_C.get(key, 0):
            same.append(f"  - {what}: {money(got, True)} (the plan gives it to the unit: {note})")
        else:
            diff.append(f"  - {what}: plan {money(plan_c, True)}, this run {money(got, True)} (difference {money(got - plan_c, True)})"
                        + (f"; {note}" if note else ""))
    if same:
        w(f"  Equal to the plan's figure ({len(same)} of {len(PLAN_ESTIMATES)}; to the cent unless the plan gives it to the unit):")
        for s in same:
            w(s)
    if diff:
        w(f"  Different ({len(diff)} of {len(PLAN_ESTIMATES)}):")
        for s in diff:
            w(s)
    if r23:
        w(f"  The plan's restated 10-23 (25,682,406.37) had no dating line; this run's dating line on 10-23 is {money(r23['dating_c'], True)}.")
    vs = rep.get("vs_expected") or []
    if vs:
        w(f"  The batch's own booking checks: {sum(1 for x in vs if x.get('equal'))} of {len(vs)} equal to the cent"
          + ("" if all(x.get("equal") for x in vs) else " (different: " + "; ".join(f"line {x.get('line_no')} {x.get('what')}" for x in vs if not x.get('equal')) + ")"))
    w("")
    w("6. NOTE")
    w("  The batch file's header was written before the two-phase plan and your standing pre-approval, so it still names 'packages P1a /")
    w("  P1b - P7' and 'the owner's typed go'. It is unchanged on purpose: its sha256 is pinned, and any edit would change it.")
    if rep2:
        w(f"  A second dry run on the result ({rep2.get('started_et')}) posted {rep2.get('entries_posted')} entries.")
    w("")
    w("THE CHECKER'S EVIDENCE")
    for c in checks:
        for x in c.lines():
            w("  " + x)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


# ---- main ----------------------------------------------------------------------------------------------------------------------------
def run(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only checker of a dry run of IMP-F1-20261027-A (the owner's five import conditions).")
    ap.add_argument("--batch", required=True, help="the batch file (JSONL)")
    ap.add_argument("--source", required=True, help="the database the dry run copied (its --home terminal.db; on the apply day the stopped live one)")
    ap.add_argument("--dryrun", required=True, help="the dry run's work folder (terminal.db, report.json)")
    ap.add_argument("--second", help="a second dry run's work folder, run on the first one's result (idempotency)")
    ap.add_argument("--summary", required=True, help="where to write the owner's plain-English report")
    args = ap.parse_args(argv)
    t0 = datetime.now(timezone.utc)
    batch = Batch(args.batch)
    res_path = os.path.join(args.dryrun, "terminal.db")
    first_wal = file_size(res_path + "-wal")
    rep = load_report(args.dryrun)
    rep2 = load_report(args.second) if args.second else {}
    out: List[str] = []
    out.append(f"terminal_import_check {et_text(int(t0.timestamp()))}: batch {batch.batch_id} ({batch.path})")
    pin_ok = PINNED_SHA256 in (batch.sha256, batch.content_sha256)
    out.append(f"  batch sha256 {batch.sha256} (content {batch.content_sha256}): {'= the pinned revision 3' if pin_ok else 'NOT the pinned revision 3'}")
    missing = [p for p in (args.source, res_path) if not os.path.isfile(p)]
    if missing:
        why = (rep.get("refused") or {}) if rep else {}
        msg = (f"the dry run produced no result to check ({', '.join(missing)} missing); the dry run's report says: status {rep.get('status')}"
               f" {json.dumps(why)[:400]}" if rep else f"no dry run found: {', '.join(missing)} missing and no report.json in {args.dryrun}")
        out.append("FAIL  -  " + msg)
        out.append("RESULT: FAIL - the owner's five import conditions cannot be checked")
        with open(args.summary, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"DRY RUN OF YOUR IMPORT {batch.batch_id}: NOT PASSED\n\n{msg}\n\nNothing was applied; the dry run must be run again (before 16:00 ET).\n")
        print("\n".join(out))
        return 1
    src, sinfo = open_ro(args.source)
    res, rinfo = open_ro(res_path)
    out.append(f"  source {sinfo['path']} (opened read-only{', immutable' if sinfo['immutable'] else ''}; -wal {sinfo['wal_bytes']})")
    out.append(f"  result {rinfo['path']} (opened read-only{', immutable' if rinfo['immutable'] else ''}; -wal {rinfo['wal_bytes']})")
    out.append(f"  dry run {rep.get('started_et')} .. {rep.get('finished_et')} status {rep.get('status')}; pack {str((rep.get('pack') or {}).get('sha256'))[:16]}...")
    checks: List[Check] = []
    rows_out: dict = {}
    booked: dict = {}
    tables: dict = {}
    tb: dict = {}
    vrows: list = []
    try:
        c1 = check_preserved(batch, src, res, rep, args.source, rows_out)
        if not pin_ok:
            c1.ev("NOTE the batch is not the pinned revision 3")
        checks.append(c1)
        c2 = check_terms(batch, src, res, booked)
        if not pin_ok:
            c2.fail(f"the batch file is not the pinned revision 3 (sha256 {batch.content_sha256})")
        checks.append(c2)
        checks.append(check_cash(batch, src, res, rep, tables))
        checks.append(check_integrity(batch, res, rep, tb))
        checks.append(check_residual(batch, src, res, vrows))
        checks.append(check_idempotent(batch, res, args.dryrun, args.second, first_wal))
        verdict = all(c.ok for c in checks[:5])
        write_summary(args.summary, batch, args, checks, src, res, rep, tables, booked, sinfo, verdict, rep2)
    finally:
        src.close()
        res.close()
    for c in checks:
        out += c.lines()
    out.append(f"RESULT: {'PASS' if verdict else 'FAIL'} - the owner's five import conditions {'all pass' if verdict else 'do NOT all pass'}"
               + ("" if not args.second else f"; idempotency {'PASS' if checks[5].ok else 'FAIL'}"))
    out.append(f"summary written to {os.path.abspath(args.summary)}")
    print("\n".join(out))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(run())
