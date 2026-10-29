"""FINSIM1_PLAN P5 (TEST-01, OMS-04 orchestration, PORTFOLIO-01 run; directive section 18 #10 historical imports, #11 duplicate-import
prevention, #22 historical NAV reconstruction): the import tool ``finsim.terminal.importer`` on the owner's batch IMP-F1-20261027-A
(validation, hashes) and on the 9-line mini batch ``fixtures/terminal/import/mini_batch.jsonl`` with its fixture pack, in a temp home
seeded like the owner's book (``seed``: FakeClock, FakeProvider, no network).  Nothing here touches ``%USERPROFILE%\\.finsim``.

The seeded world (``seed``): the three real books built by ``server.build_runtime`` offline on Wed 2026-10-21 10:00 ET (the Fund book's
25,000,000.00 dated 10-21), the Goldman Sachs PB account opened 19:44 ET on 10-21, two existing simulated fills on Thu 10-22 (AAPL 100 @
339.50 and MSFT 50 @ 520.00, fee 1.00 each, settling 10-23), the FRED prints and Treasury curves the live worker had recorded (SOFR,
ICE BofA HY OAS, UST_PAR 10-21 .. 10-23 - NO IG OAS and no 10-26 curve: the pack brings them, S2b), the recorded closes of the held
names and the lifecycle ticked every evening (17:50) and night (00:50) through 10-26, as the live scheduler does.

Hand-worked figures of the mini batch (the same rules as the owner's lines; P4 / P2 work them in full), pinned by the report, pack and
end-to-end tests (``DryRunTests`` .. ``MondayTests``):
- line 3, BTO 10 ORCL Nov 20 140 C @ 3.55 on 10-22 16:00 ET: premium 10 x 3.55 x 100 = 3,550.00, fee 10 x 0.65 = 6.50 (DEFAULT schedule) ->
  the 10-22 sweep Treasury -> US Exchange 3,556.50; settling 10-23 (T+1).
- line 4, TRS ORCL short 1,000 @ 129.13: notional 129,130.00, IM 10 % = 12,913.00 (dated 10-21); VM -1,000 x (S - 129.13): 10-22 125.54 ->
  +3,590.00, 10-23 126.16 -> -620.00, 10-26 126.06 -> +100.00 (mark 3,070.00 = 1,000 x 3.07).
- line 5, CDX IG buy protection 1,000,000 @ 64 bp: IM 2 % = 20,000.00; basis derived in S4 = 64 - 0.50 - 88.00 = -24.50 bp; mark0 = 1/3 of
  the owner's 3,000,000 contract's -49,176.13 = -16,392.04 (day-0 VM, posted 10-21); cost of dealing -16,392.04 + 16,164.19 (1/3 of the
  48,492.57 upfront) = -227.85.
- line 6, IRS pay 4.720 % on 1,000,000: DV01 = 1,000,000 x 0.0001 x 2.7644767216 = 276.45, IM x 50 = 13,822.50, mark0 = -276.45 (day 0).
- the import's NAV effect at the close of 10-21 = -227.85 - 276.45 = -504.30 (the TRS is at its reset close, the options trade 10-22).
- the 10-22 as-of PB sweep: the CDX initial payment 17,025.30 (16,164.19 + rebate 861.11) + TRS VM 3,590.00 + CDX VM 397.13 - IRS VM
  4,712.26 = 16,300.17 settled; restricted max(0, 3,590.00 - 20,983.62) = 0; less the 10,000.00 buffer -> 6,300.17 swept to Treasury.
- line 9, a historical-model BUY 10 SPY @ 600.00: gross 6,000.00, fee max(1.00, 10 x 0.005) = 1.00 -> 6,001.00; labelled "Historical
  model (not an independent execution)", never the as-of label.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from finsim.terminal import config, importer as im, server
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.commands import Command
from finsim.terminal.quotes.providers import FakeProvider
from tests.terminal_helpers import _Log, _h_test_buy

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "terminal", "import")
MINI_BATCH = os.path.join(FIXTURES, "mini_batch.jsonl")
MINI_PACK = os.path.join(FIXTURES, "mini_pack.json")
REAL_BATCH = os.path.join(os.path.dirname(HERE), "finsim", "terminal", "data", "imports", "IMP-F1-20261027-A.jsonl")
DOCS_BATCH = os.path.join(os.path.dirname(HERE), "docs", "imports", "IMP-F1-20261027-A.jsonl")
APPLY_AT = "2026-10-27T10:00:00-04:00"            # Tue: L = Mon 2026-10-26
CURVES_IN_DB = ("2026-10-21", "2026-10-22", "2026-10-23")
CLOSES = {"AAPL": ("339.50", "341.20", "342.05", "338.90", "340.00", "343.10"), "MSFT": ("521.00", "522.40", "519.80", "524.10", "526.00", "525.50"),
          "SPY": ("690.10", "688.20", "689.40", "691.00", "693.50", "695.20")}
CLOSE_DATES = ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27")


# ---- the seeded world ------------------------------------------------------------------------------------------------------------------
def fixture_values() -> dict:
    with open(MINI_PACK, encoding="utf-8") as f:
        p = json.load(f)
    return {"SOFR": p["fred"]["SOFR"], "HY_OAS": p["fred"]["BAMLH0A0HYM2"], "IG_OAS": p["fred"]["BAMLC0A0CM"], "CURVES": p["curves"]}


def build(home: str, at: str):
    os.makedirs(home, exist_ok=True)
    os.environ[config.HOME_ENV] = home
    os.environ[config.DB_ENV] = os.path.join(home, "terminal.db")
    clock = FakeClock(at)
    return server.build_runtime(home, os.path.join(home, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(), books=None,
                                fetch_threads=0)


def cmd(rt, kind: str, payload: dict, rid: str, actor: str = "ui"):
    res = rt.bus.execute(Command(kind, payload, rid, actor))
    if not res.ok:
        raise AssertionError(f"{kind} {rid} -> {res.http} {res.body}")
    return res.body


#: variant -> [(New York time, command kind, payload)] entered live in the seeded history (the funding scenarios of C3)
VARIANTS = {
    # 24,990,000.00 leaves Treasury at 19:39 ET on 10-21 (business date 10-22, as the owner's transfers did) for F1:DEBT (sweep-out off), which
    # sends 24,980,000.00 back on 10-23 and receives 1,000,000.00 on 10-26: Treasury closes 10-22 below zero once the import's 10-21 needs are
    # dated, F1:DEBT is the only account with the idle cash that date and today, and it would close 10-23 below zero after lending it
    "overdraft": [("2026-10-21T19:30:00-04:00", "account.update_settings", {"account_id": "F1:DEBT", "sweep_out": False}),
                  ("2026-10-21T19:39:00-04:00", "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": "F1:DEBT", "amount": "24990000.00"}),
                  ("2026-10-23T10:00:00-04:00", "treasury.transfer", {"from_account": "F1:DEBT", "to_account": "F1:TREASURY", "amount": "24980000.00"}),
                  ("2026-10-26T10:00:00-04:00", "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": "F1:DEBT", "amount": "1000000.00"})],
    # the same 24,990,000.00 withdrawn from the book (business date 10-22) and deposited again on 10-23: on 10-22 no account holds the cash
    "no_donor": [("2026-10-21T19:39:00-04:00", "treasury.withdraw", {"lifetime_id": "F1", "amount": "24990000.00"}),
                 ("2026-10-23T10:00:00-04:00", "treasury.deposit", {"lifetime_id": "F1", "amount": "24990000.00"})],
}


def seed(home: str, through: str = "2026-10-26", ig_in_db: bool = False, curves_in_db=CURVES_IN_DB, fills: bool = True, variant: Optional[str] = None,
         ticks: bool = True) -> str:
    """Build the world in ``home`` and leave it closed (no lock held); returns the database path.  ``through`` = the last evening snapshot
    (the morning after it - the apply day - runs its 00:50 tick too)."""
    v = fixture_values()
    rt = build(home, "2026-10-21T10:00:00-04:00")
    events = sorted(VARIANTS.get(variant or "", []))
    done = []

    def due_events(upto: str):
        for at, kind, payload in events:
            if at <= upto and at not in done:
                rt.clock.set(at)
                cmd(rt, kind, payload, f"seed:{variant}:{len(done) + 1:04d}")
                done.append(at)
    try:
        rt.bus.register("test.buy", _h_test_buy)
        cmd(rt, "rates.record", {"series": "SOFR", "observations": v["SOFR"], "source": "FRED"}, "seed:sofr-0001", "worker")
        cmd(rt, "rates.record", {"series": "BAMLH0A0HYM2", "observations": v["HY_OAS"], "source": "FRED"}, "seed:hy-0001", "worker")
        if ig_in_db:
            cmd(rt, "rates.record", {"series": "BAMLC0A0CM", "observations": v["IG_OAS"], "source": "FRED"}, "seed:ig-0001", "worker")
        for d in curves_in_db:
            cmd(rt, "rates.record", {"series": "UST_PAR", "as_of": d, "points": v["CURVES"][d], "source": "treasury.gov daily par yield curve",
                                     "fetched_ts": epoch_of(date.fromisoformat(d), "18:00")}, f"seed:curve-{d}", "worker")
        rows = []
        for iid, prices in CLOSES.items():
            for d, px in zip(CLOSE_DATES, prices):
                if d <= through:
                    rows.append({"instrument_id": iid, "mark_date": d, "kind": "CLOSE", "price": px, "currency": "USD", "quality": "close",
                                 "source": "Yahoo regularMarketPrice (official close)", "method": "official_close", "quote_time": epoch_of(date.fromisoformat(d), "16:00"),
                                 "fetched_at": epoch_of(date.fromisoformat(d), "16:21"), "note": "seed"})
        cmd(rt, "marks.record", {"rows": rows}, "seed:marks-0001", "worker")
        rt.rates.load_persisted()
        rt.curve.load_persisted()
        if ticks:
            rt.lifecycle.tick(at="2026-10-21T17:50:00-04:00")      # the 10-21 snapshot is taken before the PB account opens (as the owner's)
        due_events("2026-10-21T19:43:00-04:00")
        rt.clock.set("2026-10-21T19:44:00-04:00")
        cmd(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"}, "seed:pb-gs-0001")
        if ticks:
            rt.lifecycle.tick(at="2026-10-22T00:50:00-04:00")
        if fills:
            rt.clock.set("2026-10-22T10:00:00-04:00")
            cmd(rt, "test.buy", {"account_id": "F1:US_EXCHANGE", "instrument_id": "AAPL", "qty": 100, "price": "339.50", "fee_c": 100}, "seed:buy-aapl-1")
            cmd(rt, "test.buy", {"account_id": "F1:US_EXCHANGE", "instrument_id": "MSFT", "qty": 50, "price": "520.00", "fee_c": 100}, "seed:buy-msft-1")
        d = date(2026, 10, 22)
        end = date.fromisoformat(through) if ticks else d - timedelta(days=1)
        while d <= end:
            due_events(f"{d.isoformat()}T17:49:00-04:00")
            rt.lifecycle.tick(at=f"{d.isoformat()}T17:50:00-04:00")
            nxt = d + timedelta(days=1)
            rt.lifecycle.tick(at=f"{nxt.isoformat()}T00:50:00-04:00")
            d = nxt
    finally:
        rt.close()
    return os.path.join(home, "terminal.db")


def clone(seed_home: str, home: str) -> str:
    """A fresh home holding a sqlite3-backup copy of the seeded database."""
    os.makedirs(home, exist_ok=True)
    src = sqlite3.connect(os.path.join(seed_home, "terminal.db"))
    dst = sqlite3.connect(os.path.join(home, "terminal.db"))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return os.path.join(home, "terminal.db")


def db_q(path: str, sql: str, params=()) -> list:
    c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def write_pack_for(path: str, through: Optional[str] = None, drop: Optional[dict] = None, edit=None) -> str:
    """The fixture pack trimmed to window ``through`` (L), with ``drop`` removed, written to ``path`` (with its .sha256)."""
    with open(MINI_PACK, encoding="utf-8") as f:
        p = json.load(f)
    if through:
        p["window"]["through"] = through
        for sym in p["closes"].values():
            sym["bars"] = {d: b for d, b in sym["bars"].items() if d <= through}
        for o in p["options"].values():
            o["daily"]["bars"] = {d: b for d, b in o["daily"]["bars"].items() if d <= through}
            if (o.get("official_close") or {}).get("date") != through:
                o["official_close"] = dict(o.get("official_close") or {}, date=through, tied_to_L=False, price=None)
        for sid in list(p["fred"]):
            p["fred"][sid] = [x for x in p["fred"][sid] if x[0] <= through]
        p["curves"] = {d: c for d, c in p["curves"].items() if d <= through}
    for what, keys in (drop or {}).items():
        if what == "fred":
            for sid, dates in keys.items():
                p["fred"][sid] = [x for x in p["fred"][sid] if x[0] not in dates]
        elif what == "curves":
            for d in keys:
                p["curves"].pop(d, None)
        elif what == "closes":
            for sym, dates in keys.items():
                p["closes"][sym]["bars"] = {d: b for d, b in p["closes"][sym]["bars"].items() if d not in dates}
        elif what == "chain_row":
            for occ in keys:
                p["options"][occ].pop("chain_row", None)
    if edit is not None:
        edit(p)
    return im.write_pack(path, None, p)


def finsim_home_listing() -> list:
    """What the import tool could ever write under the default home: its imports and import-backup folders (never touched by the suite)."""
    base = server.default_home()
    out = []
    for sub in ("imports", os.path.join("backups", "import")):
        p = os.path.join(base, sub)
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                out += [os.path.join(root, f) for f in files]
    return sorted(out)


_SEED: dict = {}
_HOME_BEFORE: list = []


def setUpModule():   # noqa: N802 - unittest hook
    _HOME_BEFORE[:] = finsim_home_listing()


def tearDownModule():   # noqa: N802 - unittest hook
    for p in _SEED.values():
        shutil.rmtree(os.path.dirname(p), ignore_errors=True)


def seeded(name: str = "base", **kw) -> str:
    """The seed home ``name`` (built once per module run, then cloned by each test)."""
    if name not in _SEED:
        root = tempfile.mkdtemp(prefix="fst-import-seed-")
        home = os.path.join(root, "home")
        seed(home, **kw)
        _SEED[name] = home
    return _SEED[name]


def quiet():
    return redirect_stdout(io.StringIO())


# ---- the batch -------------------------------------------------------------------------------------------------------------------
class BatchTests(unittest.TestCase):
    def test_the_owners_batch_validates_and_its_repo_copy_is_byte_identical(self):
        """P5 task 9: the repo copy equals docs/imports byte for byte, and both hash to the plan's 75690c19... (121,468 bytes); 45 records,
        decisions 23 preserve / 10 import / 1 declined / 1 unresolved; every idem key recomputed; the package key recomputed from lines 32 / 33."""
        self.assertEqual(Path(DOCS_BATCH).read_bytes(), Path(REAL_BATCH).read_bytes())
        b = im.load_batch(REAL_BATCH)
        self.assertEqual(("75690c19327713c6648d20b710495f5ef0b5a924ed263748b3f74aa51bd11d2f", 121468), (b.sha256, b.size))
        self.assertEqual(b.expected_sha256(), b.content_sha256)
        self.assertEqual(45, len(b.records))
        self.assertEqual({"preserve": 23, "import": 10, "declined": 1, "unresolved": 1},
                         {d: sum(1 for ln in b.lines.values() if ln["decision"] == d) for d in im.DECISIONS})
        self.assertEqual("asofpkg:88d48966d8ba2bafae94bd16", im.package_key_of([b.lines[32]["idem_key"], b.lines[33]["idem_key"]]))
        for n, ln in b.lines.items():
            if ln.get("idem_key"):
                self.assertEqual(im.idem_key_of(ln["idem_canonical"]), ln["idem_key"], n)
        # the canonical key of a future uses the exchange trade date (CME 10-22), not the workbook's 10-21
        self.assertEqual("F1:US_EXCHANGE|FUTURE|GCZ26|BUY|5|2026-10-22", b.lines[22]["idem_canonical"])
        self.assertEqual("2026-10-22", im.key_trade_date(b.lines[22]))
        # a CRLF checkout (git autocrlf) changes the raw sha256 but not the content one
        tmp = tempfile.mkdtemp(prefix="fst-import-crlf-")
        try:
            p = os.path.join(tmp, "b.jsonl")
            Path(p).write_bytes(Path(REAL_BATCH).read_bytes().replace(b"\n", b"\r\n"))
            c = im.load_batch(p)
            self.assertNotEqual(b.sha256, c.sha256)
            self.assertEqual(b.content_sha256, c.content_sha256)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_bridge_to_the_workbook_line_by_line(self):
        """The bridge of 2026-10-23 on the owner's batch summary, with a constructed F1:ALL version row (the first-pass rehearsal's figures):
        workbook 25,770,700.86; the reconciliation's eight lines -101,731.44 + 638.16 - 691.95 - 10,836.51 + 228.21 + 13,860.86 + 5,091.02
        + 2,197.70 = -91,243.95; this run's interest -262.73, mark policy +3,212.20 and dating +5,216.31; explained -91,243.95 - 262.73
        + 3,212.20 + 5,216.31 = -83,078.17 = 25,687,622.69 - 25,770,700.86, so nothing is left unexplained.  A row 1.00 higher leaves +1.00
        unexplained, shown, never forced."""
        b = im.load_batch(REAL_BATCH)
        row = {"account_id": "F1:ALL", "snap_date": "2026-10-23", "version": 1, "nav_c": 2568762269, "original_nav_c": 2521552808, "added": 0,
               "import_effect": [{"key": b.lines[24]["idem_key"], "label": "x", "amount_c": 46392883}, {"key": "funding", "label": "f", "amount_c": 0}],
               "interest_effect_c": -26273, "mark_policy_c": 321220, "mark_policy": [], "dating_c": 521631, "dating": [], "residual_c": 0}
        br = im.bridge(b, [row])
        tw = br["to_workbook"]
        self.assertEqual(("2026-10-23", 2577070086, 2568762269, -8307817), (tw["date"], tw["workbook_nav_c"], tw["restated_nav_c"], tw["difference_c"]))
        self.assertEqual([-10173144, 63816, -69195, -1083651, 22821, 1386086, 509102, 219770, -26273, 321220, 521631], [x["amount_c"] for x in tw["lines"]])
        self.assertEqual((-9124395, -8307817, 0), (tw["reconciliation_total_c"], tw["explained_c"], tw["unexplained_c"]))
        self.assertEqual((24, "funding"), (br["dates"][0]["import_effect"][0]["line_no"], br["dates"][0]["import_effect"][1]["key"]))
        self.assertEqual(46392883, br["dates"][0]["import_total_c"])
        row2 = dict(row, nav_c=row["nav_c"] + 100, version=2)
        self.assertEqual(100, im.bridge(b, [row, row2])["to_workbook"]["unexplained_c"])
        text = "\n".join(im.report_lines({"batch_id": b.batch_id, "mode": "dry-run", "status": "DONE", "pack": {"path": "p", "sha256": "s", "window": {}},
                                          "bridge": br, "restated": [row]}))
        self.assertIn("+ not explained by the estimates", text)
        self.assertIn("= the restated NAV (this run)", text)
        self.assertIn("25,687,622.69", text)

    def _mutated(self, fn) -> list:
        recs = [json.loads(x) for x in Path(MINI_BATCH).read_text(encoding="utf-8").splitlines() if x.strip()]
        fn(recs)
        tmp = tempfile.mkdtemp(prefix="fst-import-bad-")
        try:
            p = os.path.join(tmp, "bad.jsonl")
            Path(p).write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
            with self.assertRaises(im.BatchError) as cm:
                im.load_batch(p)
            return cm.exception.problems
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_validator_refuses_each_kind_of_bad_batch(self):
        def line(recs, n):
            return next(r for r in recs if r.get("record") == "line" and r.get("line_no") == n)

        def tamper_key(recs):
            line(recs, 3)["idem_key"] = "imp:000000000000000000000000"
        self.assertTrue(any("idem_key" in p for p in self._mutated(tamper_key)))

        def price_in_key(recs):
            ln = line(recs, 4)
            ln["idem_canonical"] += "|129.13"
            ln["idem_key"] = im.idem_key_of(ln["idem_canonical"])
            ln["command"]["request_id"] = ln["idem_key"]
            ln["command"]["payload"]["as_of"]["line_key"] = ln["idem_key"]
        self.assertTrue(any("price is part of the canonical key" in p for p in self._mutated(price_in_key)))

        def wrong_lists(recs):
            recs[0]["decisions"]["import"] = [3, 4]
        self.assertTrue(any("the header lists import" in p for p in self._mutated(wrong_lists)))

        def prose(recs):
            line(recs, 3)["command"]["payload"]["evidence"] = "the owner said so"
        self.assertTrue(any("outside the machine set" in p for p in self._mutated(prose)))

        def unknown_kind(recs):
            line(recs, 4)["command"]["kind"] = "otc.book"
        self.assertTrue(any("is not allowed" in p for p in self._mutated(unknown_kind)))

        def hm_label(recs):
            line(recs, 9)["source_label"] = "As-of booking - simulated fill"
        self.assertTrue(any("Historical model" in p for p in self._mutated(hm_label)))

        def bad_date(recs):
            ln = line(recs, 2)
            ln["idem_canonical"] = ln["idem_canonical"].replace("2026-10-22", "2026-10-21")
            ln["idem_key"] = im.idem_key_of(ln["idem_canonical"])
        self.assertTrue(any("key trade date" in p for p in self._mutated(bad_date)))

    def test_no_mode_runs_without_home(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr_quiet():
            code = im.main(["--batch", MINI_BATCH, "--mode", "report"])
        self.assertEqual(2, code)


class redirect_stderr_quiet:
    def __enter__(self):
        import sys
        self._old = sys.stderr
        sys.stderr = io.StringIO()
        return self

    def __exit__(self, *exc):
        import sys
        sys.stderr = self._old
        return False


class _Home(unittest.TestCase):
    """A fresh home holding a copy of the seeded world WITHOUT the lifecycle runs (``ticks=False``): what report mode, the light
    commands, the pack and the locks need, nothing of the as-of hooks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-import-")
        self.home = os.path.join(self.tmp, "home")
        self.db = clone(seeded("plain", ticks=False), self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---- report mode -----------------------------------------------------------------------------------------------------------------
class ReportTests(_Home):
    def test_report_statuses_beside_a_writer_leave_the_file_byte_identical(self):
        """P5 task 2: mode=ro, never Database.open; another connection holds the write lock (as a running server would) and the database
        file's bytes do not change.  Line 1's fill equals the workbook (100 AAPL @ 339.50 on 10-22: present_match); line 2's VWAP 520.00 differs
        from the workbook's 515.00 (present_differs, delta +5.0000); the import lines are missing; 7 unresolved; 8 declined."""
        before = im.sha256_file(self.db)
        writer = sqlite3.connect(self.db, isolation_level=None)
        writer.execute("BEGIN IMMEDIATE")
        try:
            with quiet():
                rep = im.report(MINI_BATCH, self.home)
        finally:
            writer.execute("ROLLBACK")
            writer.close()
        self.assertEqual(before, im.sha256_file(self.db))
        st = {r["line_no"]: r["status"] for r in rep["lines"]}
        self.assertEqual({1: "present_match", 2: "present_differs", 3: "missing_import", 4: "missing_import", 5: "missing_import", 6: "missing_import",
                          7: "unresolved", 8: "declined", 9: "missing_import"}, st)
        line2 = rep["lines"][1]
        self.assertEqual(("price", "520.0000", "515.00", "5.0000"), (line2["diffs"][0]["field"], line2["diffs"][0]["app"], line2["diffs"][0]["workbook"],
                                                                     line2["diffs"][0]["delta_per_unit"]))
        self.assertEqual((100, "339.500000", ["2026-10-22"]), (rep["lines"][0]["match"]["filled_qty"], rep["lines"][0]["match"]["vwap"],
                                                                rep["lines"][0]["match"]["trade_dates"]))
        # the declined line keeps its search result in match (no T-bill position found)
        self.assertEqual([], rep["lines"][7]["match"]["bond_positions"])
        self.assertEqual({"present_match": 1, "present_differs": 1, "missing_import": 5, "unresolved": 1, "declined": 1}, rep["counts"])

    def test_the_match_hash_is_stable_and_moves_when_a_read_row_changes(self):
        with quiet():
            h1 = im.report(MINI_BATCH, self.home)["match_hash"]
            h2 = im.report(MINI_BATCH, self.home)["match_hash"]
        self.assertEqual(h1, h2)
        c = sqlite3.connect(self.db)
        try:
            c.execute("UPDATE settlements SET status='SETTLED' WHERE status='PENDING'")
            c.commit()
        finally:
            c.close()
        with quiet():
            self.assertNotEqual(h1, im.report(MINI_BATCH, self.home)["match_hash"])

    def test_the_owners_batch_reports_on_a_book_without_his_executions(self):
        """The owner's 35 lines against the seeded F1 book (none of his executions are in it): every preserve line missing, every import line
        missing_import, 34 declined, 35 unresolved - the statuses follow the database, never the batch's recorded ones."""
        with quiet():
            rep = im.report(REAL_BATCH, self.home)
        st = [r["status"] for r in rep["lines"]]
        self.assertEqual(["missing_import"] * 33 + ["declined", "unresolved"], st)
        self.assertEqual(35, len(rep["lines"]))


# ---- the light commands and the module wiring --------------------------------------------------------------------------------------------
class CommandTests(_Home):
    def test_record_inputs_inserts_only_absent_values_and_reports_differences(self):
        rt = build(self.home, "2026-10-27T10:00:00-04:00")
        try:
            self.assertEqual("bound", rt.modules.get("importer"))
            payload = {"batch_id": "IMP-TEST-MINI-A",
                       "marks": [{"instrument_id": "ORCL", "mark_date": "2026-10-23", "kind": "CLOSE", "price": "126.16", "quality": "close",
                                  "source": "Yahoo daily bars (observed close)", "method": "daily_close", "quote_time": 1793131200, "fetched_at": 1793120400}],
                       "observations": [{"series": "BAMLC0A0CM", "as_of": "2026-10-21", "value": "0.88", "source": "FRED"},
                                        {"series": "SOFR", "as_of": "2026-10-23", "value": "3.92", "source": "FRED"}],
                       "curves": [{"as_of": "2026-10-26", "points": {"3Y": "4.79", "5Y": "4.84"}, "source": "treasury.gov", "fetched_ts": 1793120400}]}
            r = rt.bus.execute(Command("import.record_inputs", payload, "impset:IMP-TEST-MINI-A:S2", "import"))
            self.assertTrue(r.ok, r.body)
            # SOFR 10-23 3.92 was recorded by the live worker (the seed): present; the rest is new
            self.assertEqual(({"marks": 1, "observations": 1, "curve_points": 2}, {"marks": 0, "observations": 1, "curve_points": 0}, []),
                             (r.body["inserted"], r.body["present"], r.body["differences"]))
            # the same id and payload replays; a differing value never overwrites the stored one and is listed
            self.assertTrue(rt.bus.execute(Command("import.record_inputs", payload, "impset:IMP-TEST-MINI-A:S2", "import")).replayed)
            p2 = json.loads(json.dumps(payload))
            p2["marks"][0]["price"] = "126.20"
            p2["observations"][1]["value"] = "3.95"
            r2 = rt.bus.execute(Command("import.record_inputs", p2, "impset:IMP-TEST-MINI-A:S2:r2", "import"))
            self.assertEqual([("mark", "126.16", "126.20"), ("observation", "3.92", "3.95")], [(d["what"], d["stored"], d["pack"]) for d in r2.body["differences"]])
            with rt.db.reader() as conn:
                self.assertEqual("126.16", conn.execute("SELECT price FROM marks WHERE instrument_id='ORCL' AND mark_date='2026-10-23'").fetchone()[0])
            # only the import tool sends it
            r3 = rt.bus.execute(Command("import.record_inputs", payload, "ui:record-inputs-0001", "ui"))
            self.assertEqual((403, "AS_OF_FORBIDDEN"), (r3.http, r3.code))
            # the bookkeeping rows upsert
            self.assertTrue(rt.bus.execute(Command("import.batch_record", {"batch_id": "IMP-TEST-MINI-A", "status": "RUNNING", "mode_last": "dry-run"},
                                                   "impbk:test:0001", "import")).ok)
            self.assertTrue(rt.bus.execute(Command("import.batch_record", {"batch_id": "IMP-TEST-MINI-A", "status": "DONE"}, "impbk:test:0002", "import")).ok)
            self.assertTrue(rt.bus.execute(Command("import.line_record", {"batch_id": "IMP-TEST-MINI-A", "line_no": 7, "product": "CASH", "status": "unresolved",
                                                                          "action": "unresolved", "source_label": ""}, "impbk:test:0003", "import")).ok)
            with rt.db.reader() as conn:
                self.assertEqual(("DONE", "dry-run"), tuple(conn.execute("SELECT status, mode_last FROM import_batches").fetchone()))
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM import_lines").fetchone()[0])
            # the C19 routes answer read-only (no request id needed for the report)
            status, out, _h = rt.router.handle("GET", "/api/import/batches", None, None, "127.0.0.1", None)
            self.assertEqual(200, status)
            self.assertIn("IMP-F1-20261027-A", [b["batch_id"] for b in out["batches"]])
            status, out, _h = rt.router.handle("GET", "/api/import/batches/IMP-F1-20261027-A", None, None, "127.0.0.1", None)
            self.assertEqual((200, "NOT_RUN", 35), (status, out["status"], len(out["planned"])))
            status, out, _h = rt.router.handle("POST", "/api/import/report", None, {"batch_id": "IMP-F1-20261027-A"}, "127.0.0.1", None)
            self.assertEqual((200, 35), (status, len(out["lines"])))
            status, out, _h = rt.router.handle("GET", "/api/import/batches/NOPE", None, None, "127.0.0.1", None)
            self.assertEqual(404, status)
        finally:
            rt.close()


# ---- the data pack -----------------------------------------------------------------------------------------------------------------
def _chart(bars: dict, divs=()) -> bytes:
    """A Yahoo chart payload: ``bars`` {iso date: (close, volume)} stamped 09:30 ET (gmtoffset -14400), dividends [(iso date, amount)]."""
    ts = [epoch_of(date.fromisoformat(d), "09:30") for d in sorted(bars)]
    closes = [float(bars[d][0]) for d in sorted(bars)]
    vols = [int(bars[d][1]) for d in sorted(bars)]
    res = {"meta": {"gmtoffset": -14400, "exchangeTimezoneName": "America/New_York"}, "timestamp": ts,
           "indicators": {"quote": [{"open": closes, "high": closes, "low": closes, "close": closes, "volume": vols}]}}
    if divs:
        res["events"] = {"dividends": {str(epoch_of(date.fromisoformat(d), "09:30")): {"amount": float(a), "date": epoch_of(date.fromisoformat(d), "09:30")}
                                       for d, a in divs}}
    return json.dumps({"chart": {"result": [res], "error": None}}).encode("utf-8")


class FakeNet:
    """URL -> bytes for the pack fetch (no network): Yahoo charts, Cboe chains, FRED CSV, the Treasury.gov month feed."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, ua, timeout, accept):
        self.urls.append((url, ua))
        orcl = {"2026-10-20": ("131.02", 9100000), "2026-10-21": ("129.13", 12000000), "2026-10-22": ("125.54", 15000000), "2026-10-23": ("126.16", 11000000),
                "2026-10-26": ("126.06", 9800000)}
        if "/v8/finance/chart/ORCL261120C00140000" in url:
            return _chart({"2026-10-21": ("4.55", 410), "2026-10-22": ("3.55", 1250), "2026-10-23": ("3.55", 980), "2026-10-26": ("3.15", 640)})
        if "/v8/finance/chart/META261120C00660000" in url:
            return _chart({"2026-10-22": ("23.59", 300), "2026-10-23": ("20.04", 0), "2026-10-26": ("15.40", 0)})
        if "/v8/finance/chart/ORCL" in url:
            return _chart(orcl, [("2026-10-09", "0.50")] if "events=" in url else ())
        if "/v8/finance/chart/META" in url:
            return _chart({d: ("697.52", 1) for d in orcl}, [("2026-09-21", "0.525")] if "events=" in url else ())
        if "cdn.cboe.com" in url:
            u = "ORCL" if "/ORCL.json" in url else "META"
            occ = "ORCL261120C00140000" if u == "ORCL" else "META261120C00660000"
            return json.dumps({"timestamp": "2026-10-27 11:36:00", "data": {"symbol": u, "current_price": 130.07, "options": [
                {"option": occ, "bid": 4.3, "ask": 4.45, "last_trade_price": 4.375, "prev_day_close": 3.325 if u == "ORCL" else 15.875, "volume": 512,
                 "open_interest": 4120, "iv": 0.41}, {"option": occ[:-8] + "00999000", "bid": 1.0, "ask": 1.1}]}}).encode("utf-8")
        if "fred.stlouisfed.org" in url:
            sid = url.split("id=")[1].split("&")[0]
            rows = {"SOFR": [("2026-10-21", "3.88"), ("2026-10-22", "3.90"), ("2026-10-23", "3.92"), ("2026-10-26", "3.93")],
                    "BAMLC0A0CM": [("2026-10-21", "0.88"), ("2026-10-22", "0.89"), ("2026-10-23", "0.91"), ("2026-10-26", "0.90")],
                    "BAMLH0A0HYM2": [("2026-10-21", "3.27"), ("2026-10-22", "3.40"), ("2026-10-23", "3.38"), ("2026-10-26", "3.30")]}[sid]
            return ("observation_date," + sid + "\n" + "\n".join(f"{d},{v}" for d, v in rows) + "\n").encode("utf-8")
        if "home.treasury.gov" in url:
            entry = ("<entry><content><m:properties><d:NEW_DATE>2026-10-26T00:00:00</d:NEW_DATE><d:BC_1MONTH>4.11</d:BC_1MONTH><d:BC_3MONTH>4.22</d:BC_3MONTH>"
                     "<d:BC_6MONTH>4.35</d:BC_6MONTH><d:BC_1YEAR>4.47</d:BC_1YEAR><d:BC_2YEAR>4.68</d:BC_2YEAR><d:BC_3YEAR>4.79</d:BC_3YEAR>"
                     "<d:BC_5YEAR>4.84</d:BC_5YEAR><d:BC_10YEAR>5.00</d:BC_10YEAR></m:properties></content></entry>")
            return ('<feed xmlns:m="m" xmlns:d="d">' + entry + "</feed>").encode("utf-8")
        from finsim.terminal.quotes.http import FetchError
        raise FetchError(f"unexpected url in the test: {url[:60]}", status=404, retryable=False)


class PackTests(_Home):
    def _batch(self) -> str:
        """The owner's batch trimmed to lines 24 and 29-30 plus a META TRS (the pack needs only what the lines name): a valid small batch."""
        recs = [json.loads(x) for x in Path(REAL_BATCH).read_text(encoding="utf-8").splitlines() if x.strip()]
        keep = {24, 25, 29, 30}
        out = []
        for r in recs:
            if r.get("record") == "line":
                if r["line_no"] not in keep:
                    continue
                r = dict(r, line_no=sorted(keep).index(r["line_no"]) + 1)
            elif r.get("record") == "package":
                continue
            elif r.get("record") == "batch":
                r = dict(r, decisions={"preserve": [], "import": [1, 2, 3, 4], "declined": [], "unresolved": []},
                         decision_counts={"preserve": 0, "import": 4, "declined": 0, "unresolved": 0, "total_lines": 4})
            out.append(r)
        p = os.path.join(self.tmp, "small.jsonl")
        Path(p).write_text("\n".join(json.dumps(r) for r in out) + "\n", encoding="utf-8")
        im.load_batch(p)
        return p

    def test_fetch_data_pack_from_the_sources_into_the_home(self):
        """P5 task 3: the window 2026-10-21 .. L (today Tue 10-27 -> L Mon 10-26); Yahoo with the browser user agent, Cboe with the terminal's
        honest agent, Treasury.gov / FRED with the government agent; the UST_PAR curves the database lacks (10-26) from Treasury.gov; the
        chain's previous close tied to L (the chain quotes 10-27 11:36 ET, the session after L); the pack and its sha256 in <home>/imports."""
        net = FakeNet()
        with quiet():
            got = im.fetch_data_pack(self._batch(), self.home, clock=FakeClock("2026-10-27T11:40:00-04:00"), getter=net, sleep=lambda s: None)
        path = got["path"]
        self.assertTrue(path.startswith(os.path.join(self.home, "imports", "IMP-F1-20261027-A", "pack-")))
        self.assertEqual(Path(path + ".sha256").read_text().strip(), im.sha256_file(path))
        pack = im.load_pack(path)
        self.assertEqual((date(2026, 10, 21), date(2026, 10, 26)), (pack.window_from, pack.through))
        self.assertEqual("129.13", pack.closes("ORCL")["2026-10-21"]["close"])
        o = pack.option("ORCL261120C00140000")
        self.assertEqual(("ORCL261120C00140000", "3.325", True, "3.325"), (o["chain_row"]["option"], o["chain_row"]["prev_day_close"], o["official_close"]["tied_to_L"],
                                                                         o["official_close"]["price"]))
        self.assertEqual(0, pack.option("META261120C00660000")["daily"]["bars"]["2026-10-23"]["volume"])
        self.assertEqual([("2026-10-21", "0.88")], [(d.isoformat(), str(v)) for d, v in pack.fred("BAMLC0A0CM")][:1])
        self.assertEqual("database (treasury.gov daily par yield curve)", pack.data["curve_sources"]["2026-10-21"])
        self.assertEqual("treasury.gov daily par yield curve (fetched for the pack)", pack.data["curve_sources"]["2026-10-26"])
        self.assertEqual([("2026-10-09", "0.5")], pack.dividends("ORCL"))
        uas = {u.split("/")[2]: ua for u, ua in net.urls}
        self.assertEqual(config.BROWSER_UA, uas["query1.finance.yahoo.com"])
        self.assertEqual(config.TERMINAL_UA, uas["cdn.cboe.com"])
        self.assertEqual(config.GOV_UA, uas["fred.stlouisfed.org"])
        self.assertEqual(config.GOV_UA, uas["home.treasury.gov"])
        # the S2 rows from this pack: ORCL closes every NYSE day 10-21 .. 10-26; the call from its trade date, L at the Cboe official close
        from finsim.terminal.calendar import TradingCalendar
        rows, _notes = im.mark_rows(pack, im.load_batch(self._batch()), TradingCalendar(config.holidays_path()))
        by = {(r["instrument_id"], r["mark_date"]): r for r in rows}
        self.assertEqual(["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"], sorted(d for i, d in by if i == "ORCL"))
        self.assertEqual(("3.325", "official_close"), (by[("ORCL261120C00140000", "2026-10-26")]["price"], by[("ORCL261120C00140000", "2026-10-26")]["method"]))
        meta_l = by[("META261120C00660000", "2026-10-26")]
        self.assertEqual(("15.875", "close", "official_close"), (meta_l["price"], meta_l["quality"], meta_l["method"]))
        self.assertEqual(("20.04", "stale"), (by[("META261120C00660000", "2026-10-23")]["price"], by[("META261120C00660000", "2026-10-23")]["quality"]))
        self.assertEqual("3.55", by[("ORCL261120C00140000", "2026-10-22")]["price"])

    def test_option_prices_lose_the_providers_float_noise(self):
        """Yahoo's option daily bars and Cboe's chain publish 32-bit floats (the 2026-10-27 night pack): 4.52 arrives as 4.51999998, 3.325 as
        3.32500004768372, 20.04 as 20.04000092, and Cboe's closing mid (11.55 + 11.67) / 2 as 11.6100001335144.  S2 records the printed
        prices; a genuine decimal and a clean one are kept as given."""
        cases = {"4.51999998": "4.52", "3.32500004768372": "3.325", "20.04000092": "20.04", "11.6100001335144": "11.61", "1.51999998092651": "1.52",
                 "2.0999999": "2.1", "15.39999962": "15.4", "130.52999878": "130.53", "15.875": "15.875", "3.55": "3.55", "0.123456789012": "0.123456789012",
                 "1.1125": "1.1125"}
        self.assertEqual(cases, {x: im.clean_price(x) for x in cases})
        self.assertIsNone(im.clean_price(None))

        def noisy(p):
            p["options"]["ORCL261120C00140000"]["daily"]["bars"]["2026-10-23"]["close"] = "3.54999995"
        pack = im.load_pack(write_pack_for(os.path.join(self.tmp, "noisy.json"), edit=noisy))
        from finsim.terminal.calendar import TradingCalendar
        rows, _n = im.mark_rows(pack, im.load_batch(MINI_BATCH), TradingCalendar(config.holidays_path()))
        got = {(r["instrument_id"], r["mark_date"]): r["price"] for r in rows}
        self.assertEqual("3.55", got.get(("ORCL261120C00140000", "2026-10-23")), sorted(got))

    def test_a_pack_that_changed_after_it_was_written_is_refused(self):
        p = write_pack_for(os.path.join(self.tmp, "pack.json"))
        Path(p).write_bytes(Path(p).read_bytes() + b" ")
        with self.assertRaises(im.ImportRefused) as cm:
            im.load_pack(p)
        self.assertEqual("PACK_CHANGED", cm.exception.code)

    def test_the_input_rules_of_s0(self):
        """S0 tests the inputs with the jobs' rules: a missing close is a problem; a business day without a Treasury curve passes through the
        3-business-day lookback; an OAS date FRED skipped while it has a later value passes (CAUGHT_UP), L's unpublished value passes (pending),
        a date with nothing after it does not; SOFR within the 10-day lag; an ex-date in the window needs its dividend record."""
        from finsim.terminal.calendar import TradingCalendar
        cal = TradingCalendar(config.holidays_path())
        batch = im.load_batch(MINI_BATCH)
        conn = im.open_ro(self.db)
        try:
            def run(**kw):
                p = im.load_pack(write_pack_for(os.path.join(tempfile.mkdtemp(dir=self.tmp), "p.json"), **kw))
                return im.input_problems(conn, batch, p, cal)
            probs, notes = run()
            self.assertEqual([], probs)
            probs, notes = run(drop={"curves": ["2026-10-26"]})
            self.assertEqual([], probs)
            self.assertIn("2026-10-26: no Treasury curve that day; the curve of 2026-10-23 values it", " ".join(notes))
            probs, notes = run(drop={"fred": {"BAMLC0A0CM": ["2026-10-22"]}})
            self.assertEqual([], probs)
            probs, notes = run(drop={"fred": {"BAMLC0A0CM": ["2026-10-26"]}})
            self.assertEqual([], probs)
            self.assertIn("(L) is not published yet", " ".join(notes))
            probs, _n = run(drop={"fred": {"BAMLC0A0CM": ["2026-10-23", "2026-10-26"]}})
            self.assertTrue(any("BAMLC0A0CM: no value for 2026-10-23 and none after it" in x for x in probs), probs)
            probs, _n = run(drop={"closes": {"ORCL": ["2026-10-22"]}})
            self.assertTrue(any("ORCL: no close for 2026-10-22" in x for x in probs), probs)
            probs, _n = run(drop={"chain_row": ["ORCL261120C00140000"]})
            self.assertTrue(any("no Cboe chain row" in x for x in probs), probs)
            probs, _n = run(edit=lambda p: p["dividends"].update(ORCL=[["2026-10-23", "0.50"]]))
            self.assertTrue(any("ex-dividend date 2026-10-23" in x for x in probs), probs)
        finally:
            conn.close()


# ---- clock, locks, backup and restore -----------------------------------------------------------------------------------------------
class GuardTests(_Home):
    def test_clock_rules(self):
        b = im.load_batch(MINI_BATCH)
        self.assertEqual([], im.clock_problems(b, FakeClock("2026-10-27T15:59:59-04:00")))
        self.assertTrue(im.clock_problems(b, FakeClock("2026-10-27T16:00:00-04:00")))
        self.assertTrue(im.clock_problems(b, FakeClock("2026-11-20T10:00:00-05:00")))
        self.assertEqual([], im.clock_problems(b, FakeClock("2026-11-19T10:00:00-05:00")))

    def test_dry_run_and_apply_refuse_while_a_terminal_holds_the_home(self):
        pack = write_pack_for(os.path.join(self.tmp, "pack.json"))
        lk = server.InstanceLock(os.path.join(self.home, "terminal.lock"))
        self.assertTrue(lk.acquire())
        try:
            lk.write({"pid": 4242, "port": 8765})
            with self.assertRaises(im.ImportRefused) as cm:
                with quiet():
                    im.dry_run(MINI_BATCH, self.home, pack, work_dir=os.path.join(self.tmp, "dry1"), clock=FakeClock(APPLY_AT))
            self.assertEqual("TERMINAL_RUNNING", cm.exception.code)
            self.assertIn("4242", cm.exception.message)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "dry1")))
            with self.assertRaises(im.ImportRefused) as cm:
                with quiet():
                    im.apply(MINI_BATCH, self.home, pack, "go", clock=FakeClock(APPLY_AT))
            self.assertEqual("TERMINAL_RUNNING", cm.exception.code)
            with self.assertRaises(im.ImportRefused) as cm:
                with quiet():
                    im.apply(MINI_BATCH, self.home, pack, "  ", clock=FakeClock(APPLY_AT))
            self.assertEqual("OWNER_GO_REQUIRED", cm.exception.code)
        finally:
            lk.release()
        with self.assertRaises(im.ImportRefused) as cm:
            with quiet():
                im.dry_run(MINI_BATCH, self.home, pack, work_dir=os.path.join(self.tmp, "dry2"), clock=FakeClock("2026-10-27T16:00:01-04:00"))
        self.assertEqual("CLOCK", cm.exception.code)

    def test_backup_outside_the_rotation_and_restore(self):
        """Section 7: the pre-import backup in <home>/backups/import with its sha256 (written, re-read); restore with the locks held: the
        database, -wal and -shm moved aside into backups/failed-<ts>, the backup copied in, its sha256 and integrity_check verified."""
        clock = FakeClock(APPLY_AT)
        bpath, bsha = im.backup_live(self.home, "IMP-TEST-MINI-A", self.db, clock)
        self.assertEqual(os.path.join(self.home, "backups", "import"), os.path.dirname(bpath))
        self.assertTrue(os.path.basename(bpath).startswith("terminal-pre-IMP-TEST-MINI-A-20261027-100000"))
        self.assertEqual((bsha, bsha), (im.sha256_file(bpath), Path(bpath + ".sha256").read_text().strip()))
        c = sqlite3.connect(self.db)
        try:
            c.execute("UPDATE meta SET value='999' WHERE key='state_version'")
            c.commit()
        finally:
            c.close()
        self.assertNotEqual(bsha, im.sha256_file(self.db))
        lk = server.InstanceLock(os.path.join(self.home, "terminal.lock"))
        self.assertTrue(lk.acquire())
        try:
            with self.assertRaises(im.ImportRefused) as cm:
                with quiet():
                    im.restore(self.home, bpath, clock=clock)
            self.assertEqual("TERMINAL_RUNNING", cm.exception.code)
        finally:
            lk.release()
        with quiet():
            res = im.restore(self.home, bpath, clock=clock)
        self.assertEqual(("ok", bsha), (res["integrity_check"], im.sha256_file(self.db)))
        self.assertIn("terminal.db", res["moved_aside"])
        self.assertTrue(os.path.isfile(os.path.join(res["failed_folder"], "terminal.db")))
        self.assertEqual("999", db_q(os.path.join(res["failed_folder"], "terminal.db"), "SELECT value FROM meta WHERE key='state_version'")[0]["value"])
        # a backup whose recorded sha256 no longer matches is refused
        Path(bpath + ".sha256").write_text("0" * 64 + "\n")
        with self.assertRaises(im.ImportRefused) as cm:
            with quiet():
                im.restore(self.home, bpath, clock=clock)
        self.assertEqual("BACKUP_CHANGED", cm.exception.code)

    def test_cli_exit_codes(self):
        cases = [(["--home", self.home], 2), (["--home", self.home, "--mode", "dry-run", "--batch", MINI_BATCH], 2),
                 (["--home", self.home, "--mode", "report"], 2), (["--home", self.home, "--mode", "report", "--batch", MINI_BATCH], 0),
                 (["--home", self.home, "--mode", "apply", "--batch", MINI_BATCH, "--pack", MINI_PACK], 2),
                 (["--home", self.home, "--mode", "restore"], 2)]
        for argv, want in cases:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr_quiet():
                self.assertEqual(want, im.main(argv), argv)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr_quiet():
            code = im.main(["--help"])
        self.assertEqual(0, code)
        self.assertIn("--home is required", out.getvalue())
        self.assertIn("Restore:", out.getvalue())


# ---- end to end: dry run, apply, funding, the Monday case (moved from wip_import_tests.py once the Part A hooks landed, 15.2 step 5) ----------
def counts(db: str) -> dict:
    return {t: db_q(db, f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in ("journal_entries", "fills", "orders", "otc_contracts", "transfers", "settlements",
                                                                      "nav_snapshot_versions", "marks")}


class E2E(unittest.TestCase):
    seed_kw: dict = {}
    seed_name = "base"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-import-e2e-")
        self.home = os.path.join(self.tmp, "home")
        self.db = clone(seeded(self.seed_name, **self.seed_kw), self.home)
        self.pack = write_pack_for(os.path.join(self.tmp, "pack.json"))
        self.clock = FakeClock(APPLY_AT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dry(self, name="dry1", pack=None, home=None, **kw):
        with quiet():
            return im.dry_run(MINI_BATCH, home or self.home, pack or self.pack, work_dir=os.path.join(self.tmp, name), clock=kw.pop("clock", self.clock), **kw)

    def apply(self, dry="dry1", owner_go="test: the owner's go", pack=None, **kw):
        with quiet():
            return im.apply(MINI_BATCH, self.home, pack or self.pack, owner_go, dryrun_dir=os.path.join(self.tmp, dry) if dry else None,
                            clock=kw.pop("clock", self.clock), **kw)


class DryRunTests(E2E):
    def test_dry_run_books_restates_and_leaves_the_source_byte_identical(self):
        before = im.sha256_file(self.db)
        r = self.dry()
        self.assertEqual("DONE", r["status"], r.get("refused"))
        self.assertTrue(r["source_unchanged"])
        self.assertEqual(before, im.sha256_file(self.db))
        st = {ln["line_no"]: (ln["status"], ln["action"]) for ln in r["lines"]}
        self.assertEqual({1: ("present_match", "preserved (untouched)"), 2: ("present_differs", "preserved (untouched)"), 3: ("missing_import", "booked"),
                          4: ("missing_import", "booked"), 5: ("missing_import", "booked"), 6: ("missing_import", "booked"),
                          7: ("unresolved", "unresolved (not a transaction)"), 8: ("declined", "declined (never booked)"), 9: ("missing_import", "booked")}, st)
        line = {ln["line_no"]: ln for ln in r["lines"]}
        # the money of each booked line, worked by hand in the module docstring
        self.assertEqual((355000, 650, "2026-10-23"), (line[3]["money"]["gross_c"], line[3]["money"]["fee_c"], line[3]["money"]["settle_date"]))
        self.assertEqual((1291300, "129.13", 307000, "AS_OF", "CONFIRMED"), (line[4]["money"]["im_c"], line[4]["money"]["reset_px"], line[4]["money"]["vm_balance_c"],
                                                                               line[4]["money"]["booking"], line[4]["money"]["confirm_status"]))
        self.assertEqual([("2026-10-22", 359000), ("2026-10-23", -62000), ("2026-10-26", 10000)],
                         [(v["date"], v["delta_c"]) for v in line[4]["money"]["vm"] if v["delta_c"]])
        self.assertEqual(2000000, line[5]["money"]["im_c"])
        self.assertEqual(1382250, line[6]["money"]["im_c"])
        self.assertEqual((600000, 100), (line[9]["money"]["gross_c"], line[9]["money"]["fee_c"]))
        # S4: basis -24.50 bp derived from the pack's 10-21 IG OAS 0.88 % (IG is NOT in the seeded database: S2b recorded it, the stores were reloaded)
        self.assertEqual(("88.0000", "-24.5000", "set"), (r["S4"][0]["proxy_bp"], r["S4"][0]["basis_bp"], r["S4"][0]["action"]))
        self.assertEqual(6, r["S2b"]["result"]["inserted"]["observations"])
        # ... and the IG contract deals exactly 64 bp and its VM dates post (none left CLOSE_PENDING): 10-21 day 0 at mid 63.5 bp, then 10-22 / 10-23 /
        # 10-26 on the pack's proxies 0.89 / 0.91 / 0.90 % (10-24 / 10-25: unchanged, the weekend)
        self.assertEqual("64.0000", line[5]["money"]["terms"]["deal_spread_bp"])
        self.assertEqual(["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"], [v["date"] for v in line[5]["money"]["vm"] if v["delta_c"]])
        self.assertEqual(-1639204, line[5]["money"]["vm"][0]["delta_c"])
        # C2: the PB's 10-22 import cash swept to Treasury, dated 10-22 (6,300.17); US Exchange (import cash negative: premiums paid) never swept
        c2 = {(x["account_id"], x["date"]): x["sweep_c"] for x in r["C2a"] + r["C2"]}
        self.assertEqual(630017, c2[("F1:PB_GS", "2026-10-22")])
        self.assertFalse(any(a == "F1:US_EXCHANGE" for a, _d in c2))
        self.assertEqual(([], []), (r["C3a"], r["C3"]))
        # C5: every account row and F1:ALL restated on every date 10-21 .. 10-26, residual 0 each; F1:PB_GS 10-21 added with original 0
        rows = r["restated"]
        self.assertTrue(rows)
        self.assertEqual([0], sorted({v["residual_c"] for v in rows}))
        dates = sorted({v["snap_date"] for v in rows})
        self.assertEqual(["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"], dates)
        for ds in dates:
            self.assertIn("F1:ALL", {v["account_id"] for v in rows if v["snap_date"] == ds})
        pb21 = [v for v in rows if v["account_id"] == "F1:PB_GS" and v["snap_date"] == "2026-10-21"]
        self.assertEqual((0, 1), (pb21[0]["original_nav_c"], pb21[0]["added"]))
        all21 = [v for v in rows if v["account_id"] == "F1:ALL" and v["snap_date"] == "2026-10-21"][0]
        self.assertEqual(-50430, sum(x["amount_c"] for x in all21["import_effect"]))
        self.assertEqual(2500000000 - 50430, all21["nav_c"])
        # the funding group carries the as-of sweeps and nets to 0 at the lifetime level
        for v in rows:
            if v["account_id"] == "F1:ALL":
                self.assertEqual(0, sum(x["amount_c"] for x in v["import_effect"] if x["key"] == "funding"), v["snap_date"])
        # C6
        self.assertTrue(r["C6"]["integrity"]["ok"], r["C6"])
        self.assertTrue(r["C6"]["trial_balance"]["balanced"])
        self.assertEqual(0, r["wal_bytes"])
        # the copy holds the import tables with the Completed Trades labels; every business-date close >= 0
        work_db = os.path.join(self.tmp, "dry1", "terminal.db")
        lines = {x["line_no"]: x for x in db_q(work_db, "SELECT * FROM import_lines WHERE batch_id='IMP-TEST-MINI-A'")}
        self.assertTrue(lines[3]["source_label"].startswith("As-of booking - simulated fill at the 2026-10-22 closing print, entered 2026-10-27 10:00 ET"))
        self.assertIn("(batch IMP-TEST-MINI-A)", lines[3]["source_label"])
        self.assertTrue(lines[4]["source_label"].startswith("As-of booking - simulated OTC trade with Goldman Sachs, value date 2026-10-21, entered 2026-10-27 10:00 ET"))
        self.assertEqual("Historical model (not an independent execution)", lines[9]["source_label"])
        for aid, by in r["cash_table"]["after"].items():
            for ds, v in by.items():
                self.assertGreaterEqual(v, 0, (aid, ds))

    def test_completed_trades_rows_carry_the_labels(self):
        r = self.dry()
        self.assertEqual("DONE", r["status"])
        rt = build(os.path.join(self.tmp, "dry1"), APPLY_AT)
        try:
            status, body, _h = rt.router.handle("GET", "/api/blotter/executions", {"scope": "F1:ALL"}, None, "127.0.0.1", None)
            self.assertEqual(200, status, body)
            by_label = {}
            for row in body["rows"]:
                by_label.setdefault(row.get("source_label") or row.get("label") or "", []).append(row)
            labels = " | ".join(by_label)
            self.assertIn("As-of booking - simulated fill at the 2026-10-22 closing print, entered 2026-10-27 10:00 ET by the operator at the owner's instruction"
                          " (batch IMP-TEST-MINI-A)", labels)
            self.assertIn("As-of booking - simulated OTC trade with Goldman Sachs, value date 2026-10-21", labels)
            self.assertIn("Historical model (not an independent execution)", labels)
            hm = [row for row in body["rows"] if (row.get("source_label") or "").startswith("Historical model")]
            self.assertTrue(hm and all("As-of" not in (row.get("source_label") or "") for row in hm))
            # import_lines.source_label equals the label the blotter shows for the same row
            lines = {x["line_no"]: x for x in db_q(os.path.join(self.tmp, "dry1", "terminal.db"), "SELECT line_no, source_label FROM import_lines")}
            for n in (3, 4, 5, 6, 9):
                self.assertIn(lines[n]["source_label"], labels, n)
            # the C19 routes answer read-only
            status, out, _h = rt.router.handle("GET", "/api/import/batches", None, None, "127.0.0.1", None)
            self.assertEqual(200, status)
            self.assertIn("IMP-TEST-MINI-A", [b["batch_id"] for b in out["batches"]])
            status, out, _h = rt.router.handle("GET", "/api/import/batches/IMP-TEST-MINI-A", None, None, "127.0.0.1", None)
            self.assertEqual((200, "DONE", 9), (status, out["status"], len(out["lines"])))
            status, out, _h = rt.router.handle("POST", "/api/import/report", None, {"batch_id": "IMP-F1-20261027-A"}, "127.0.0.1", None)
            self.assertEqual(200, status, out)
            self.assertEqual(35, len(out["lines"]))
        finally:
            rt.close()

    def test_a_second_dry_run_on_the_result_posts_nothing(self):
        r1 = self.dry()
        self.assertEqual("DONE", r1["status"])
        home2 = os.path.join(self.tmp, "dry1")
        r2 = self.dry(name="dry2", home=home2)
        self.assertEqual("DONE", r2["status"], r2.get("refused"))
        self.assertEqual(0, r2["entries_posted"])
        self.assertEqual({3, 4, 5, 6, 9}, {ln["line_no"] for ln in r2["lines"] if ln["status"] == "already_imported"})
        # 1.0.6 (on purpose, AppliedAgainTests): a line already in the book is not sent again at all (1.0.5 replayed it under its request
        # id, which a later pack's moved chain row turned into a refusal); so no booking command is in the second run's steps
        self.assertEqual([], [s["request_id"] for s in r2["steps"] if s["kind"] in ("otc.book_as_of", "trade.book_as_of", "trade.book_as_of_package")])
        self.assertTrue(r2["verify_only"])
        self.assertTrue(all(x["action"].startswith("skipped") for x in r2["S4"]))
        self.assertEqual(counts(os.path.join(self.tmp, "dry1", "terminal.db"))["nav_snapshot_versions"],
                         counts(os.path.join(self.tmp, "dry2", "terminal.db"))["nav_snapshot_versions"])
        self.assertEqual(0, r2["wal_bytes"])

    def test_a_pack_missing_one_dates_input_is_refused_before_any_write(self):
        pack = write_pack_for(os.path.join(self.tmp, "pack-missing.json"), drop={"closes": {"ORCL": ["2026-10-23"]}})
        src_max = db_q(self.db, "SELECT MAX(id) m FROM commands")[0]["m"]
        r = self.dry(pack=pack)
        self.assertEqual(("REFUSED", "PREFLIGHT", "S0"), (r["status"], r["refused"]["code"], r["refused"]["step"]))
        self.assertTrue(any("ORCL: no close for 2026-10-23" in p for p in r["refused"]["details"]["problems"]))
        copy_db = os.path.join(self.tmp, "dry1", "terminal.db")
        self.assertEqual(src_max, db_q(copy_db, "SELECT MAX(id) m FROM commands")[0]["m"])
        self.assertEqual(0, db_q(copy_db, "SELECT COUNT(*) n FROM otc_contracts")[0]["n"])

    def test_an_unpublished_fred_value_of_L_leaves_that_cdx_vm_pending_and_a_skipped_date_is_caught_up(self):
        pack = write_pack_for(os.path.join(self.tmp, "pack-ig.json"), drop={"fred": {"BAMLC0A0CM": ["2026-10-22", "2026-10-26"]}})
        r = self.dry(pack=pack)
        self.assertEqual("DONE", r["status"], r.get("refused"))
        notes = " ".join(r["preflight"]["notes"])
        self.assertIn("BAMLC0A0CM for 2026-10-26 (L) is not published yet", notes)
        self.assertIn("BAMLC0A0CM has no value for 2026-10-22 but a later one (2026-10-23)", notes)
        skipped = {x["key"]: x["note"] for x in r["C1a"]["skipped"] + r["C1"]["skipped"]}
        cdx = [k for k in skipped if k.startswith("SWAP_VM:CDX-")]
        # the live scheduler's own outcomes: L's VM waits (CLOSE_PENDING, retried), the skipped date is CAUGHT_UP (its move posts with 10-23's VM)
        self.assertTrue(any(k.endswith(":2026-10-26") and skipped[k].startswith("CLOSE_PENDING") for k in cdx), skipped)
        self.assertTrue(any(k.endswith(":2026-10-22") and skipped[k].startswith("CAUGHT_UP") for k in cdx), skipped)
        self.assertEqual([0], sorted({v["residual_c"] for v in r["restated"]}))

    def test_record_inputs_never_overwrites_a_stored_close(self):
        # the database already holds a different ORCL close for 10-23 (126.10): S2 keeps it and lists the difference
        rt = build(self.home, "2026-10-26T20:00:00-04:00")
        try:
            rt.bus.execute(im.Command("marks.record", {"rows": [{"instrument_id": "ORCL", "mark_date": "2026-10-23", "kind": "CLOSE", "price": "126.10",
                                                                   "currency": "USD", "quality": "close", "source": "live", "method": "official_close"}]},
                                      "test:orcl-close-0001", "worker"))
        finally:
            rt.close()
        r = self.dry()
        diffs = r["S2"]["result"]["differences"]
        from decimal import Decimal as D
        self.assertEqual([("ORCL", "2026-10-23", D("126.10"), D("126.16"))], [(d["instrument_id"], d["date"], D(d["stored"]), D(d["pack"])) for d in diffs])
        stored = db_q(os.path.join(self.tmp, "dry1", "terminal.db"), "SELECT price FROM marks WHERE instrument_id='ORCL' AND mark_date='2026-10-23'")[0]["price"]
        self.assertEqual(D("126.10"), D(stored))


class ApplyTests(E2E):
    def test_apply_writes_the_backup_first_then_twice_posts_nothing(self):
        self.assertEqual("DONE", self.dry()["status"])
        r = self.apply()
        self.assertEqual("DONE", r["status"], r.get("refused"))
        bpath = r["backup_path"]
        self.assertTrue(bpath.startswith(os.path.join(self.home, "backups", "import")))
        self.assertEqual(r["backup_sha256"], Path(bpath + ".sha256").read_text().strip())
        self.assertEqual(r["backup_sha256"], im.sha256_file(bpath))
        self.assertEqual(0, r["wal_bytes"])
        rows = db_q(self.db, "SELECT status, owner_go, backup_path, applied_ts FROM import_batches")
        self.assertEqual(("DONE", "test: the owner's go", bpath), (rows[0]["status"], rows[0]["owner_go"], rows[0]["backup_path"]))
        self.assertIsNotNone(rows[0]["applied_ts"])
        before = counts(self.db)
        # a second apply needs a new dry run (the database moved) and then books nothing
        self.assertEqual("DONE", self.dry(name="dry2")["status"])
        r2 = self.apply(dry="dry2")
        self.assertEqual("DONE", r2["status"], r2.get("refused"))
        self.assertEqual(0, r2["entries_posted"])
        after = counts(self.db)
        for t in ("journal_entries", "fills", "otc_contracts", "nav_snapshot_versions", "transfers", "settlements"):
            self.assertEqual(before[t], after[t], t)
        self.assertEqual({3, 4, 5, 6, 9}, {ln["line_no"] for ln in r2["lines"] if ln["status"] == "already_imported"})

    def test_restore_brings_back_the_pre_import_file(self):
        self.assertEqual("DONE", self.dry()["status"])
        pre = im.sha256_file(self.db)
        r = self.apply()
        self.assertEqual("DONE", r["status"])
        self.assertNotEqual(pre, im.sha256_file(self.db))
        with quiet():
            res = im.restore(self.home, r["backup_path"], clock=self.clock)
        self.assertEqual(("ok", r["backup_sha256"]), (res["integrity_check"], im.sha256_file(self.db)))
        self.assertIn("terminal.db", os.listdir(res["failed_folder"]))
        self.assertEqual(0, db_q(self.db, "SELECT COUNT(*) n FROM otc_contracts")[0]["n"])

    def test_a_partial_apply_resumes(self):
        self.assertEqual("DONE", self.dry()["status"])
        real = im.Run.cmd

        def failing(run, step, kind, payload, rid, ok_codes=(), rerun_suffix=False):
            if step == "line 6":
                raise im.ImportRefused("SIMULATED_CRASH", "the test stops the apply at line 6", step=step)
            return real(run, step, kind, payload, rid, ok_codes, rerun_suffix)
        im.Run.cmd = failing
        try:
            r = self.apply()
        finally:
            im.Run.cmd = real
        self.assertEqual(("PARTIAL", "SIMULATED_CRASH"), (r["status"], r["refused"]["code"]))
        self.assertEqual("PARTIAL", db_q(self.db, "SELECT status FROM import_batches")[0]["status"])
        # the OTC contracts book first: lines 4 and 5 committed, each consistent, before the crash at line 6
        self.assertEqual((0, 2), (db_q(self.db, "SELECT COUNT(*) n FROM fills WHERE import_key IS NOT NULL")[0]["n"],
                                  db_q(self.db, "SELECT COUNT(*) n FROM otc_contracts WHERE import_key IS NOT NULL")[0]["n"]))
        # the database moved, so the apply needs a new dry run (on a fresh copy of the partial file); then it resumes
        with self.assertRaises(im.ImportRefused):
            self.apply()
        self.assertEqual("DONE", self.dry(name="dry2")["status"])
        r2 = self.apply(dry="dry2")
        self.assertEqual("DONE", r2["status"], r2.get("refused"))
        self.assertEqual({4, 5}, {ln["line_no"] for ln in r2["lines"] if ln["status"] == "already_imported"})
        self.assertEqual({3, 6, 9}, {ln["line_no"] for ln in r2["lines"] if ln["action"] == "booked"})
        # each line booked exactly once: 2 listed fills (lines 3, 9) and 3 contracts (lines 4, 5, 6) carry import keys
        self.assertEqual((2, 3), (db_q(self.db, "SELECT COUNT(*) n FROM fills WHERE import_key IS NOT NULL")[0]["n"],
                                  db_q(self.db, "SELECT COUNT(*) n FROM otc_contracts WHERE import_key IS NOT NULL")[0]["n"]))
        self.assertEqual([0], sorted({v["residual_c"] for v in r2["restated"]}))

    def test_apply_refusals_write_nothing(self):
        self.assertEqual("DONE", self.dry()["status"])
        sha = im.sha256_file(self.db)
        with self.assertRaises(im.ImportRefused) as cm:
            self.apply(owner_go="")
        self.assertEqual("OWNER_GO_REQUIRED", cm.exception.code)
        lk = server.InstanceLock(os.path.join(self.home, "terminal.lock"))
        self.assertTrue(lk.acquire())
        try:
            lk.write({"pid": 4242, "port": 8765})
            with self.assertRaises(im.ImportRefused) as cm:
                self.apply()
            self.assertEqual("TERMINAL_RUNNING", cm.exception.code)
        finally:
            lk.release()
        other = write_pack_for(os.path.join(self.tmp, "pack-other.json"), edit=lambda p: p["notes"].append("changed"))
        with self.assertRaises(im.ImportRefused) as cm:
            self.apply(pack=other)
        self.assertIn(cm.exception.code, ("PACK_CHANGED", "NO_DRY_RUN"))
        with self.assertRaises(im.ImportRefused) as cm:
            self.apply(clock=FakeClock("2026-10-27T16:00:00-04:00"))
        self.assertEqual("CLOCK", cm.exception.code)
        with self.assertRaises(im.ImportRefused) as cm:
            self.apply(clock=FakeClock("2026-11-20T10:00:00-05:00"))
        self.assertEqual("CLOCK", cm.exception.code)
        # the cutoff: a FakeClock moved across 00:30 (a new accrual night is due) -> re-run the dry run; the live file is never opened
        with self.assertRaises(im.ImportRefused) as cm:
            self.apply(clock=FakeClock("2026-10-28T00:40:00-04:00"))
        self.assertEqual("CUTOFF_CHANGED", cm.exception.code)
        self.assertEqual(sha, im.sha256_file(self.db))
        self.assertEqual(0, db_q(self.db, "SELECT COUNT(*) n FROM otc_contracts")[0]["n"])
        # a command run on the source in between (a server started) moves state_version / max command id
        rt = build(self.home, "2026-10-27T10:05:00-04:00")
        try:
            rt.bus.execute(im.Command("account.update_settings", {"account_id": "F1:DEBT", "sweep_buffer_c": 2000000}, "test:between-0001", "ui"))
        finally:
            rt.close()
        with self.assertRaises(im.ImportRefused) as cm:
            self.apply()
        self.assertIn(cm.exception.code, ("CUTOFF_CHANGED", "MATCH_CHANGED"))
        self.assertEqual(0, db_q(self.db, "SELECT COUNT(*) n FROM otc_contracts")[0]["n"])

    def test_the_cutoff_ignores_integrity_and_housekeeping_keys(self):
        from finsim.terminal.importer import PackProvider, cutoff_fields, load_pack, maintenance_runtime
        pack = load_pack(self.pack)
        out = []
        for at in ("2026-10-27T10:00:00-04:00", "2026-10-27T11:06:00-04:00"):
            clk = FakeClock(at)
            rt = maintenance_runtime(self.home, self.db, pack, clk, None)
            try:
                rt.lifecycle.tick(only="INTEGRITY_FULL")
                out.append(cutoff_fields(rt, {"state_version": 1, "max_command_id": 1}))
            finally:
                rt.close()
        self.assertEqual(out[0]["due_keys_sha256"], out[1]["due_keys_sha256"])
        self.assertIsNotNone(PackProvider)


class FundingTests(E2E):
    seed_name = "overdraft"
    seed_kw = {"variant": "overdraft"}

    def test_a_business_date_overdraft_is_resolved_by_a_dated_transfer_pair(self):
        r = self.dry()
        self.assertEqual("DONE", r["status"], r.get("refused"))
        # C3a (after the OTC contracts, C1a and C2a): Treasury would close 10-22 at 10,000.00 + 6,300.17 (the PB sweep) - 63,403.99 (the 10-21 IM and
        # day-0 VM 12,913.00 + 20,000.00 + 16,392.04 + 13,822.50 + 276.45) = -47,103.82 -> F1:DEBT (idle 24,990,000.00 that date) lends it, dated
        # 10-22; F1:DEBT would then close 10-23 at 10,000.00 - 47,103.82 = -37,103.82 -> Treasury pays it back, dated 10-23 (the pair)
        # the table before the as-of sweeps (after the contracts' bookings and C1a): Treasury 10,000.00 - 63,403.99 = -53,403.99 at the close of 10-22;
        # at the close of 10-21 it still held 25,000,000.00 - 63,403.99 = 24,936,596.01 (the 24,990,000.00 left with business date 10-22)
        mid = r["cash_table"]["before_sweeps"]["F1:TREASURY"]
        self.assertEqual((2493659601, -5340399), (mid["2026-10-21"], mid["2026-10-22"]))
        self.assertEqual(1000000, r["cash_table"]["before"]["F1:TREASURY"]["2026-10-22"])
        moves = r["C3a"] + r["C3"]
        self.assertGreaterEqual(len(moves), 2, moves)
        first, back = moves[0], moves[1]
        self.assertEqual(("F1:DEBT", "F1:TREASURY", "2026-10-22", 4710382), (first["from_account"], first["to_account"], first["business_date"], first["amount_c"]))
        self.assertEqual(("F1:TREASURY", "F1:DEBT", "2026-10-23", 3710382), (back["from_account"], back["to_account"], back["business_date"], back["amount_c"]))
        work_db = os.path.join(self.tmp, "dry1", "terminal.db")
        rows = db_q(work_db, "SELECT t.kind, t.linked_type, t.linked_id, e.kind ekind, e.memo, t.business_date FROM transfers t JOIN journal_entries e ON e.entry_id=t.entry_id"
                             " WHERE t.idem_key LIKE 'asofxfer:%' ORDER BY t.transfer_id")
        self.assertEqual(["TRANSFER"] * len(rows), [x["kind"] for x in rows])
        self.assertEqual({"asof_funding"}, {x["linked_type"] for x in rows})
        self.assertEqual({"ASOF_FUNDING"}, {x["ekind"] for x in rows})
        self.assertEqual({"as-of funding"}, {x["memo"] for x in rows})
        for aid, by in r["cash_table"]["after"].items():
            for ds, v in by.items():
                self.assertGreaterEqual(v, 0, (aid, ds))
        # F1:DEBT's pre-import idle cash (24,990,000.00 on 10-22) is never swept by C2: only the import's own cash is
        self.assertFalse(any(x["account_id"] == "F1:DEBT" for x in r["C2"]))
        self.assertEqual([0], sorted({v["residual_c"] for v in r["restated"]}))


class NoDonorTests(E2E):
    seed_name = "no_donor"
    seed_kw = {"variant": "no_donor"}

    def test_a_business_date_overdraft_with_no_donor_is_refused(self):
        r = self.dry()
        self.assertEqual(("REFUSED", "ASOF_OVERDRAFT"), (r["status"], r["refused"]["code"]), r.get("refused"))
        self.assertTrue(r["refused"]["step"].startswith("C3"), r["refused"])
        self.assertEqual("2026-10-22", r["refused"]["details"]["date"])


class MondayTests(E2E):
    seed_name = "monday"
    seed_kw = {"through": "2026-10-25"}

    def setUp(self):
        super().setUp()
        self.pack = write_pack_for(os.path.join(self.tmp, "pack-fri.json"), through="2026-10-23")
        self.clock = FakeClock("2026-10-26T10:00:00-04:00")

    def test_a_weekend_between_L_and_the_apply_day(self):
        r = self.dry()
        self.assertEqual("DONE", r["status"], r.get("refused"))
        self.assertEqual("2026-10-23", r["preflight"]["L"])
        rows = r["restated"]
        dates = sorted({v["snap_date"] for v in rows})
        self.assertEqual(["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"], dates)
        all_ = {v["snap_date"]: v for v in rows if v["account_id"] == "F1:ALL"}
        # Saturday and Sunday at Friday's marks: the positions' value does not move (only the nightly interest does)
        self.assertEqual([0], sorted({v["residual_c"] for v in rows}))
        self.assertTrue(r["C6"]["integrity"]["ok"], r["C6"])
        # the cash-interest nights of the weekend were re-accrued
        c4 = {(x["account_id"], x["kind"]): x for x in r["C4"]}
        self.assertIn(("F1:TREASURY", "cashint"), c4)
        work_db = os.path.join(self.tmp, "dry1", "terminal.db")
        nights = [x["idem_key"] for x in db_q(work_db, "SELECT idem_key FROM journal_entries WHERE idem_key LIKE 'reaccrue:IMP-TEST-MINI-A:F1:TREASURY:cashint:%'")]
        self.assertTrue(any(k.endswith("2026-10-24") for k in nights) and any(k.endswith("2026-10-25") for k in nights), nights)
        # Monday's TODAY baseline is the restated Sunday
        from finsim.terminal.accounting import _last_snapshot
        c = sqlite3.connect(work_db)
        c.row_factory = sqlite3.Row
        try:
            base = _last_snapshot(c, "F1:ALL", "2026-10-26")
        finally:
            c.close()
        base = dict(base) if base is not None else {}
        self.assertEqual(("2026-10-25", all_["2026-10-25"]["nav_c"]), (base.get("snap_date"), base.get("nav_c")))


# ---- phase 2 (1.0.6): the applied batch run again, the batch view, the C18 path, the import panel (FINSIM1_PLAN 15.5, 15.9 item 8, 15.14) --------
class AppliedAgainTests(E2E):
    """The owner's batch was applied on 2026-10-28; 1.0.6 runs report mode and, before 16:00 ET, a dry run on copies of that book.  A dry run
    with a LATER pack (the Cboe chain rows moved, a FRED value published after the apply) used to be refused at the first option line
    (REQUEST_ID_REUSED: the chain row is part of the booking's payload), and its catch-up would have posted the live scheduler's own
    pending variation margin as the import's.  An applied batch run again only checks and reports: nothing is written to the book, and
    the apply's own record (the lines it booked and when, the owner's go, the backup) is kept."""

    def test_a_dry_run_on_the_applied_book_with_a_later_pack_writes_nothing(self):
        # the apply's pack: the IG OAS of L (10-26) not published yet, so the IG contract's 10-26 VM waits (CLOSE_PENDING)
        first = write_pack_for(os.path.join(self.tmp, "pack-first.json"), drop={"fred": {"BAMLC0A0CM": ["2026-10-26"]}})
        self.assertEqual("DONE", self.dry(pack=first)["status"])
        r1 = self.apply(pack=first)
        self.assertEqual("DONE", r1["status"], r1.get("refused"))

        def later(p):                                   # the chain quotes moved since; the 10-26 IG OAS is published now
            for o in p["options"].values():
                if o.get("chain_row"):
                    o["chain_row"]["bid"] = "9.99"
                    o["chain_row"]["volume"] = "12345"
        second = write_pack_for(os.path.join(self.tmp, "pack-later.json"), edit=later)
        before = dict(counts(self.db), curve_points=db_q(self.db, "SELECT COUNT(*) n FROM curve_points")[0]["n"])
        batch1 = db_q(self.db, "SELECT applied_ts, owner_go, backup_path, backup_sha256 FROM import_batches")[0]
        lines1 = {x["line_no"]: (x["action"], x["applied_ts"]) for x in db_q(self.db, "SELECT line_no, action, applied_ts FROM import_lines")}
        r2 = self.dry(name="dry-later", pack=second)
        self.assertEqual("DONE", r2["status"], r2.get("refused"))
        self.assertTrue(r2["verify_only"])
        self.assertEqual(0, r2["entries_posted"])
        work = os.path.join(self.tmp, "dry-later", "terminal.db")
        self.assertEqual(before, dict(counts(work), curve_points=db_q(work, "SELECT COUNT(*) n FROM curve_points")[0]["n"]))
        self.assertEqual({3, 4, 5, 6, 9}, {ln["line_no"] for ln in r2["lines"] if ln["status"] == "already_imported"})
        self.assertEqual({3, 4, 5, 6, 9}, {ln["line_no"] for ln in r2["lines"] if ln["action"] == "already_imported"})
        writes = ("otc.book_as_of", "trade.book_as_of", "trade.book_as_of_package", "cdx.terms", "import.record_inputs", "cash.asof_sweep",
                  "cash.asof_transfer", "cash.reaccrue", "nav.restate")
        self.assertEqual([], [s_["kind"] for s_ in r2["steps"] if s_["kind"] in writes])
        # the newer FRED value is reported, not recorded (the live system records it and posts its own VM)
        self.assertEqual(1, r2["S2b"]["result"]["absent"]["observations"])
        self.assertIn("1 observation(s)", " ".join(r2["notes"]))
        self.assertEqual([], r2["vs_expected"])          # nothing booked: no booking figure to compare
        # the apply's record stays whole: the lines it booked and when, its report, the owner's go and the backup; the run sent no command
        self.assertEqual(lines1, {x["line_no"]: (x["action"], x["applied_ts"]) for x in db_q(work, "SELECT line_no, action, applied_ts FROM import_lines")})
        self.assertEqual("booked", lines1[3][0])
        self.assertIsNotNone(lines1[3][1])
        self.assertEqual(batch1, db_q(work, "SELECT applied_ts, owner_go, backup_path, backup_sha256 FROM import_batches")[0])
        self.assertEqual(db_q(self.db, "SELECT * FROM import_lines ORDER BY line_no"), db_q(work, "SELECT * FROM import_lines ORDER BY line_no"))
        self.assertEqual(db_q(self.db, "SELECT * FROM import_batches"), db_q(work, "SELECT * FROM import_batches"))
        src_max = db_q(self.db, "SELECT MAX(id) m FROM commands")[0]["m"]
        self.assertEqual([], db_q(work, "SELECT request_id FROM commands WHERE id > ? AND actor = 'import'", (src_max,)))

    def test_a_second_apply_writes_nothing_and_keeps_the_first_applys_record(self):
        self.assertEqual("DONE", self.dry()["status"])
        self.assertEqual("DONE", self.apply()["status"])
        batch1 = db_q(self.db, "SELECT applied_ts, owner_go, backup_path, backup_sha256 FROM import_batches")[0]
        lines1 = {x["line_no"]: (x["action"], x["applied_ts"], x["created_refs_json"]) for x in db_q(self.db, "SELECT * FROM import_lines")}
        before = counts(self.db)
        self.clock.set("2026-10-27T11:00:00-04:00")       # an hour later: a second apply would stamp a new time
        self.assertEqual("DONE", self.dry(name="dry2")["status"])
        r2 = self.apply(dry="dry2", owner_go="test: a second go")
        self.assertEqual(("DONE", True, 0), (r2["status"], r2["verify_only"], r2["entries_posted"]))
        self.assertEqual(before, counts(self.db))
        self.assertEqual(batch1, db_q(self.db, "SELECT applied_ts, owner_go, backup_path, backup_sha256 FROM import_batches")[0])
        self.assertEqual(lines1, {x["line_no"]: (x["action"], x["applied_ts"], x["created_refs_json"]) for x in db_q(self.db, "SELECT * FROM import_lines")})
        self.assertEqual(("DONE", "apply"), tuple(db_q(self.db, "SELECT status, mode_last FROM import_batches")[0].values()))
        # the second run took its own backup first (in its report), and the batch row keeps the apply's
        self.assertNotEqual(batch1["backup_path"], r2["backup_path"])
        self.assertTrue(os.path.isfile(r2["backup_path"]))
        self.assertEqual(0, r2["wal_bytes"])


class BatchViewTests(E2E):
    def test_the_batch_view_in_trader_words(self):
        """C19 ``view``: the panel's words and figures, every account by name (never a raw account id), the run's own figures."""
        self.assertEqual("DONE", self.dry()["status"])
        rt = build(os.path.join(self.tmp, "dry1"), APPLY_AT)
        try:
            status, out, _h = rt.router.handle("GET", "/api/import/batches", None, None, "127.0.0.1", None)
            self.assertEqual(200, status, out)
            rows = {b["batch_id"]: b for b in out["batches"]}
            self.assertEqual(("F", "dry run done (not applied to this book)"), (rows["IMP-TEST-MINI-A"]["book_code"], rows["IMP-TEST-MINI-A"]["state"]))
            self.assertEqual(("F", "not run in this book"), (rows["IMP-F1-20261027-A"]["book_code"], rows["IMP-F1-20261027-A"]["state"]))
            status, out, _h = rt.router.handle("GET", "/api/import/batches/IMP-TEST-MINI-A", None, None, "127.0.0.1", None)
            self.assertEqual(200, status, out)
            v = out["view"]
            summary = {p_[0]: p_[1] for p_ in v["summary"]}
            self.assertEqual(("dry run done (not applied to this book)", "ok", "balanced"), (summary["Status"], summary["Integrity"], summary["Trial balance"]))
            self.assertEqual("2026-10-21 .. 2026-10-26", summary["Window"])
            ln = {x["line_no"]: x for x in v["lines"]}
            self.assertEqual(("Option", "buy to open", "import", "missing", "booked in a dry run", "10"),
                             (ln[3]["product_short"], ln[3]["side_short"], ln[3]["decision_short"], ln[3]["status_short"], ln[3]["action_short"], ln[3]["qty_short"]))
            self.assertEqual(("TRS", "CDX", "IRS"), (ln[4]["product_short"], ln[5]["product_short"], ln[6]["product_short"]))
            self.assertRegex(ln[3]["created"], r"^order O-\d{6}; fill F-\d{6}; settlement S-\d{6}")
            self.assertTrue(ln[3]["asof"])
            self.assertTrue(ln[9]["historical_model"])
            self.assertEqual("Historical model (not an independent execution)", ln[9]["source"])
            self.assertRegex(ln[4]["instrument"], r"^TRS ORCL \(SWP-\d{6}\)$")
            self.assertEqual("Goldman Sachs (PB)", ln[4]["account"])
            self.assertEqual(("differs", "preserve"), (ln[2]["status_short"], ln[2]["decision_short"]))
            self.assertTrue(ln[2]["differences"] and all("None" not in d for d in ln[2]["differences"]))
            self.assertEqual(("declined", "unresolved"), (ln[8]["status_short"], ln[7]["status_short"]))
            # the business-date cash table: Treasury first, named accounts, every close >= 0 after the import
            accts = [r_["account"] for r_ in v["cash"]["rows"]]
            self.assertEqual("Treasury", accts[0])
            self.assertTrue(all(":" not in a for a in accts), accts)
            self.assertEqual(["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"], v["cash"]["dates"])
            self.assertFalse(any(r_["below_zero"] for r_ in v["cash"]["rows"] if r_["stage"] == "after the import"))
            # the funding moves: the PB's 10-22 as-of sweep of 6,300.17 (hand-worked in the docstring) in words
            sweep = [f for f in v["funding"] if f["business_date"] == "2026-10-22" and f["amount"] == "6300.17"]
            self.assertEqual(1, len(sweep), v["funding"])
            self.assertEqual(("sweep out (to Treasury)", "Goldman Sachs (PB)", "Treasury", "as-of sweep of the import's cash"),
                             (sweep[0]["kind"], sweep[0]["from"], sweep[0]["to"], sweep[0]["for"]))
            # the restated NAV of the whole book with its split, residual 0; the PB's 10-21 row added by the restatement
            book = {r_["date"]: r_ for r_ in v["restated"]["book"]}
            # (the API shape: the view's cents arrive as decimal strings, `amount_c` -> `amount`; hand-worked: 25,000,000.00 - 504.30)
            self.assertEqual(["0.00"], sorted({r_["residual"] for r_ in book.values()}))
            self.assertEqual(("25000000.00", "-504.30", "24999495.70"), (book["2026-10-21"]["original"], book["2026-10-21"]["import"],
                                                                         book["2026-10-21"]["restated"]))
            from decimal import Decimal as D
            self.assertEqual(D("-504.30"), sum(D(x["amount"]) for x in book["2026-10-21"]["by_line"]))
            self.assertTrue(all(not x["what"].startswith("line ") and "new contract id" not in x["what"] for x in book["2026-10-21"]["by_line"]),
                            book["2026-10-21"]["by_line"])
            pb = [a for a in v["restated"]["accounts"] if a["account"] == "Goldman Sachs (PB)"][0]
            self.assertEqual(("0.00", True), (pb["dates"]["2026-10-21"]["original"], pb["dates"]["2026-10-21"]["added"]))
            # no raw account id in what the panel shows
            shown = json.dumps({k: v[k] for k in ("lines", "cash", "funding", "restated")})
            self.assertNotIn('"F1:', shown)
            # the read-only check of the owner's batch against this book carries its words too
            status, out, _h = rt.router.handle("POST", "/api/import/report", None, {"batch_id": "IMP-F1-20261027-A"}, "127.0.0.1", None)
            self.assertEqual(200, status, out)
            self.assertTrue(all(x["status_short"] and x["status_text"] and isinstance(x["differences"], list) for x in out["lines"]))
            self.assertEqual("2026-10-27 10:00:00 ET", out["checked_et"])
        finally:
            rt.close()

    def test_difference_wording(self):
        self.assertEqual("fills: the book holds 3 where the reconciliation matched 2 (fills added or changed since; reported, never corrected)",
                         im._diff_text({"field": "drift", "batch": 2, "app": 3, "note": "the fills differ"}))
        self.assertEqual("existing contract: SWP-000001 (a contract with these key terms already exists)",
                         im._diff_text({"field": "existing_contract", "app": ["SWP-000001"], "note": "a contract with these key terms already exists"}))
        self.assertEqual("quantity: app 6000 vs workbook 4790", im._diff_text({"field": "qty", "app": "6000", "workbook": "4790"}))
        self.assertEqual("execution (the batch expects an existing execution and none was found)",
                         im._diff_text({"field": "execution", "note": "the batch expects an existing execution and none was found"}))


class C18Tests(_Home):
    def test_python_m_finsim_import_runs_the_mini_batch(self):
        """C18: ``python -m finsim import ...`` hands its arguments to the import tool: the help exits 0, report mode and a dry run of the
        mini batch run through it (the dry run with the clock pinned to the apply day: the command line takes the real clock)."""
        from finsim.terminal import app
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr_quiet():
            self.assertEqual(0, app.main(["import", "--help"]))
        self.assertIn("usage: python -m finsim import", out.getvalue())
        self.assertNotIn("FINSIM1_PLAN", out.getvalue())
        self.assertIn("1.0.5 or later", out.getvalue())                 # the restore procedure names no single release
        jpath = os.path.join(self.tmp, "report.json")
        with redirect_stdout(io.StringIO()), redirect_stderr_quiet():
            self.assertEqual(0, app.main(["import", "--batch", MINI_BATCH, "--home", self.home, "--mode", "report", "--json", jpath]))
        rep = json.loads(Path(jpath).read_text(encoding="utf-8"))
        self.assertEqual(9, len(rep["lines"]))
        e2e = os.path.join(self.tmp, "e2e")
        clone(seeded("base"), e2e)
        pack = write_pack_for(os.path.join(self.tmp, "pack.json"))
        wd = os.path.join(self.tmp, "c18-dry")
        real = im.RealClock
        im.RealClock = lambda: FakeClock(APPLY_AT)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr_quiet():
                rc = app.main(["import", "--batch", MINI_BATCH, "--home", e2e, "--mode", "dry-run", "--pack", pack, "--work-dir", wd])
        finally:
            im.RealClock = real
        self.assertEqual(0, rc)
        r = json.loads(Path(wd, "report.json").read_text(encoding="utf-8"))
        self.assertEqual(("DONE", 0), (r["status"], r["wal_bytes"]))
        self.assertEqual({3, 4, 5, 6, 9}, {ln["line_no"] for ln in r["lines"] if ln["action"] == "booked"})


class PanelStaticTests(unittest.TestCase):
    def test_the_import_panel_file(self):
        """components/imports.js: ASCII, the C19 routes only, gated on the importer module, no plan or internal codes outside its header."""
        path = os.path.join(config.package_dir(), "static", "components", "imports.js")
        raw = Path(path).read_bytes()
        self.assertTrue(all(b < 128 for b in raw))
        self.assertNotIn(b"\r", raw)
        text = raw.decode("ascii")
        self.assertIn("T.components.imports = { render: render };", text)
        self.assertIn("T.hasModule('importer')", text)
        self.assertEqual({"/api/import/batches", "/api/import/batches/", "/api/import/report"}, set(re.findall(r"'(/api/[^']*)'", text)))
        body = text.split("*/", 1)[1]
        for word in ("FINSIM1_PLAN", "C19", "idem", "impcu", "impset", "imp:", "F1:", "console.log", "http://", "https://"):
            self.assertNotIn(word, body, word)
        # the History hub mounts it with render(el, {book}) and unmounts the handle it returns
        hist = Path(config.package_dir(), "static", "pages", "history.js").read_text(encoding="utf-8")
        self.assertIn("comp.render(ip, { book: T.book.code })", hist)
        self.assertIn("return { unmount: function () { dead = true; drop(); } };", text)


class ZzDefaultHomeTests(unittest.TestCase):
    def test_nothing_under_the_default_home_changed(self):
        """No mode ran without --home, and every test used a temp home: the default home's import folders are as they were."""
        self.assertEqual(_HOME_BEFORE, finsim_home_listing())


if __name__ == "__main__":
    unittest.main()
