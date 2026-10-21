"""Foreign universe listings in the instrument master (``finsim/terminal/instruments.py``; TERMINAL_DESIGN.md §7.1, §7.3,
§7.4, §13.9).

The defect (owner's terminal, 2026-10-21): the first-run seed stored all 798 universe equities as USD / US_EQUITY /
US_EXCHANGE+PB with the ticker as the Yahoo symbol, so the 38 foreign listings (0700-HK, SHEL-L, 7203-T, ...) sat at the
top of the US Exchange account (digits sort first) with quotes unavailable forever (Yahoo knows ``0700.HK``, not
``0700-HK``).

Pinned here:
- the fresh seed: 0700-HK = HKD / FOREIGN / FOREIGN_FX+PB / Yahoo 0700.HK / T+2 / no Cboe symbol / UNSUPPORTED (quotes and
  marks only in this build), AAPL unchanged, the 12 London rows in GBP with price_scale 0.01 (Yahoo quotes pence), all 38
  rows by currency, the other 760 rows still US listings;
- ``upsert_from_yahoo_meta`` writes the same classification for a foreign venue, and refuses a Yahoo symbol another row
  owns (naming that row) instead of a silent no-op insert;
- ``repair_foreign_listings`` fixes deliberately mis-seeded rows in place, is a no-op the second time, leaves a row that an
  open order or a position references (ONE WARN attention each, resolved by the run that repairs it), takes the Yahoo
  symbol back from a duplicate a pre-fix resolve created, and leaves a holder that something references alone;
- the markets endpoint's function (``api_products.markets_view``): the US Exchange equities / options lists exclude the
  foreign rows (before the repair they led the list — the owner's screen); the account search puts them in Foreign
  Markets & FX; Yahoo prints reach them through the corrected symbol.

No network: ``make_runtime`` / ``server.build_runtime(offline=True)`` with the FakeProvider; temp homes only; the FakeClock
stands at Tue 2026-10-20 14:00 ET.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from collections import Counter
from decimal import Decimal

from tests.terminal_helpers import FOREIGN_FX, US_EXCHANGE, _Log, buy, make_runtime
from finsim.terminal import config, server as _server
from finsim.terminal.api import Request
from finsim.terminal.api_products import markets_view
from finsim.terminal.clock import FakeClock
from finsim.terminal.commands import RejectError
from finsim.terminal.instruments import (EQUITY_KINDS, FOREIGN_ACCOUNT_KINDS, FOREIGN_GROUP, REPAIR_CODE, UNIVERSE_SPEC_KEYS,
                                         foreign_listing_fields, universe_is_foreign)
from finsim.terminal.quotes.providers import FakeProvider

AT = "2026-10-20T14:00:00-04:00"


def _universe_rows():
    with open(config.universe_path(), "r", encoding="utf-8") as fh:
        return [e for e in json.load(fh)["equities"] if e.get("ticker") and e.get("asset_class") in EQUITY_KINDS]


UNIVERSE = {e["ticker"]: e for e in _universe_rows()}
FOREIGN_ROWS = [e for e in UNIVERSE.values() if universe_is_foreign(e)]
FOREIGN_IDS = {e["ticker"] for e in FOREIGN_ROWS}


# ---- helpers ------------------------------------------------------------------------------------------------------------
def row_of(rt, iid: str) -> dict:
    with rt.db.reader() as conn:
        r = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
    return None if r is None else dict(r)


def mis_seed(rt, iid: str) -> None:
    """Rewrite ``iid`` exactly as the pre-2026-10-21 seed stored every universe equity (the owner's live terminal.db):
    USD, US_EQUITY, settle 1, US_EXCHANGE + PB, Yahoo = the ticker, Cboe = the ticker with '-' -> '.', FULL, the 8 universe
    spec keys only."""
    e = UNIVERSE[iid]
    spec = {k: e.get(k) for k in UNIVERSE_SPEC_KEYS}
    with rt.db.writer() as tx:
        tx.conn.execute("UPDATE instruments SET currency='USD', price_scale='1', session_group='US_EQUITY', settle_days=1, account_kinds=?, yahoo_symbol=?,"
                        " cboe_symbol=?, support='FULL', spec_json=? WHERE instrument_id=?",
                        (json.dumps(["US_EXCHANGE", "PB"]), iid, iid.replace("-", "."), json.dumps(spec), iid))
    rt.instruments.load()


def insert_order(rt, order_id: str, iid: str, status: str, account: str = US_EXCHANGE) -> None:
    """A bare orders row (no fills) on ``iid`` — what a ticket click on a mis-seeded row could have left behind."""
    now = rt.clock.epoch()
    with rt.db.writer() as tx:
        tx.conn.execute("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, tif, status, session_group, created_ts,"
                        " updated_ts, expires_ts, preview_json) VALUES (?,?,?,?,'BUY',1,'MARKET','DAY',?,'US_EQUITY',?,?,?,'{}')",
                        (order_id, f"test:{order_id}", account, iid, status, now, now, now + 3600))


def repair(rt) -> dict:
    with rt.db.writer() as tx:
        out = rt.instruments.repair_foreign_listings(tx)
    rt.instruments.load()
    return out


def repair_log_lines(rt) -> list:
    return [msg for level, msg in rt.log.records if level == "INFO" and msg.startswith("instrument repair (foreign universe listings)")]


def open_repair_attention(rt) -> list:
    with rt.db.reader() as conn:
        return [tuple(r) for r in conn.execute("SELECT ref_id, severity, source FROM attention WHERE code=? AND resolved_ts IS NULL ORDER BY ref_id",
                                               (REPAIR_CODE,)).fetchall()]


def classification(inst) -> tuple:
    """Every column the foreign classification decides (what seed and upsert must agree on)."""
    return (inst.currency, inst.price_scale, inst.session_group, inst.settle_days, inst.account_kinds, inst.cboe_symbol, inst.support,
            inst.spec.get("foreign"), inst.spec.get("quote_unit"), inst.spec.get("note"))


# ===== the fresh seed ==================================================================================================================
class FreshSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fst-foreign-seed-")
        cls.rt = make_runtime(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_hong_kong_listing_is_foreign_and_aapl_is_unchanged(self):
        m = self.rt.instruments
        hk = m.get("0700-HK")
        self.assertEqual(("EQUITY", "Tencent Holdings Ltd.", "HKD", FOREIGN_GROUP, ("FOREIGN_FX", "PB"), "0700.HK", 2, None, "UNSUPPORTED", Decimal("1")),
                         (hk.kind, hk.name, hk.currency, hk.session_group, hk.account_kinds, hk.yahoo_symbol, hk.settle_days, hk.cboe_symbol, hk.support,
                          hk.price_scale))
        self.assertEqual("FOREIGN", FOREIGN_GROUP)                               # the group upsert_from_yahoo_meta has always used
        self.assertEqual(("HK", True, "HK"), (hk.spec["venue_suffix"], hk.spec["foreign"], hk.spec["country"]))   # universe spec keys kept
        self.assertNotIn("quote_unit", hk.spec)                                  # HKD is quoted in dollars: no scale
        self.assertIn("orders are refused", hk.spec["note"])
        # tradeability (§7.2): Foreign Markets & FX and prime brokers; never the US Exchange account, Debt or Treasury
        self.assertEqual([True, True, False, False, False], [m.tradeable_in(hk, k) for k in ("FOREIGN_FX", "PB", "US_EXCHANGE", "DEBT", "TREASURY")])
        aapl = m.get("AAPL")
        self.assertEqual(("EQUITY", "USD", "US_EQUITY", ("US_EXCHANGE", "PB"), "AAPL", 1, "AAPL", "FULL", Decimal("1")),
                         (aapl.kind, aapl.currency, aapl.session_group, aapl.account_kinds, aapl.yahoo_symbol, aapl.settle_days, aapl.cboe_symbol,
                          aapl.support, aapl.price_scale))
        self.assertNotIn("foreign", aapl.spec)
        brk = m.get("BRK-B")                                                     # US class shares: Yahoo hyphen form, Cboe dot form (unchanged)
        self.assertEqual(("BRK-B", "BRK.B", "US_EQUITY"), (brk.yahoo_symbol, brk.cboe_symbol, brk.session_group))

    def test_all_38_foreign_universe_rows_and_the_760_us_rows(self):
        m = self.rt.instruments
        # 4 HK + 3 KS + 1 TW + 2 SR + 4 PA + 3 DE + 1 AS + 2 SW + 3 T + 1 NS + 2 AX + 12 L = 38 foreign listings
        self.assertEqual(38, len(FOREIGN_ROWS))
        self.assertEqual({"GBP": 12, "EUR": 8, "HKD": 4, "KRW": 3, "JPY": 3, "SAR": 2, "CHF": 2, "AUD": 2, "TWD": 1, "INR": 1},
                         dict(Counter(e["currency"] for e in FOREIGN_ROWS)))    # 12 + 8 + 4 + 3 + 3 + 2 + 2 + 2 + 1 + 1 = 38
        for e in FOREIGN_ROWS:
            inst = m.get(e["ticker"])
            self.assertEqual((e["currency"], e["yahoo"], FOREIGN_GROUP, FOREIGN_ACCOUNT_KINDS, None, "UNSUPPORTED"),
                             (inst.currency, inst.yahoo_symbol, inst.session_group, inst.account_kinds, inst.cboe_symbol, inst.support), e["ticker"])
            # T+2 everywhere except NSE India (RELIANCE.NS), which settles T+1 since 2023-01-27
            self.assertEqual(1 if e["yahoo"].endswith(".NS") else 2, inst.settle_days, e["ticker"])
        foreign_in_master = sorted(i.instrument_id for i in m.all() if i.kind in EQUITY_KINDS and i.session_group == FOREIGN_GROUP)
        self.assertEqual(sorted(FOREIGN_IDS), foreign_in_master)
        # 798 equity-kind universe rows - 38 foreign = 760 US listings, each still USD / US_EQUITY / US_EXCHANGE+PB / FULL, Yahoo = ticker
        us = [i for i in m.all() if i.kind in EQUITY_KINDS and i.session_group != FOREIGN_GROUP]
        self.assertEqual(760, len(us))
        self.assertEqual({("USD", "US_EQUITY", ("US_EXCHANGE", "PB"), "FULL", 1, Decimal("1"))},
                         {(i.currency, i.session_group, i.account_kinds, i.support, i.settle_days, i.price_scale) for i in us})
        self.assertTrue(all(i.yahoo_symbol == i.instrument_id for i in us))

    def test_london_is_quoted_in_pence_with_price_scale_001(self):
        m = self.rt.instruments
        shel = m.get("SHEL-L")
        self.assertEqual(("GBP", Decimal("0.01"), "SHEL.L", "GBp"), (shel.currency, shel.price_scale, shel.yahoo_symbol, shel.spec["quote_unit"]))
        # 100 shares at a Yahoo print of 2,712.50 (pence): 100 x 2,712.50 x multiplier 1 x price_scale 0.01 = GBP 2,712.50 = 271,250 cents
        self.assertEqual(271250, shel.contract_value_c(Decimal("2712.50"), 100))
        london = sorted(i.instrument_id for i in m.all() if (i.yahoo_symbol or "").endswith(".L"))
        self.assertEqual(12, len(london))
        self.assertEqual({("GBP", Decimal("0.01"), "GBp")}, {(m.get(i).currency, m.get(i).price_scale, m.get(i).spec.get("quote_unit")) for i in london})
        scaled = sorted(i.instrument_id for i in m.all() if i.kind in EQUITY_KINDS and i.price_scale != 1)
        self.assertEqual(london, scaled)                                         # no other equity row carries a quote scale
        # a Hong Kong print is in dollars: 100 x 612.50 x 1 x 1 = HKD 61,250.00 = 6,125,000 cents
        self.assertEqual(6125000, m.get("0700-HK").contract_value_c(Decimal("612.50"), 100))

    def test_the_shared_classification_reads_yahoo_minor_units(self):
        gbp = foreign_listing_fields("VOD.L", "GBp")                             # Yahoo meta says GBp: pence -> GBP x 0.01
        self.assertEqual(("GBP", "0.01", "GBp"), (gbp["currency"], gbp["price_scale"], gbp["spec"]["quote_unit"]))
        seed = foreign_listing_fields("SHEL.L", "GBP", 0.01)                     # the universe says GBP + yahoo_scale 0.01: the same row
        self.assertEqual({k: v for k, v in gbp.items() if k not in ("yahoo_symbol",)}, {k: v for k, v in seed.items() if k not in ("yahoo_symbol",)})
        self.assertEqual(("ZAR", "0.01", "ZAc"), (lambda f: (f["currency"], f["price_scale"], f["spec"]["quote_unit"]))(foreign_listing_fields("NPN.JO", "ZAc")))
        self.assertEqual((1, "INR"), (lambda f: (f["settle_days"], f["currency"]))(foreign_listing_fields("RELIANCE.NS", "INR")))
        for bad in (("X.L", "pounds", None), ("X.L", "GBP", "0"), ("X.L", "GBP", "2"), ("X.L", "GBP", "NaN"), ("X.L", "GBP", "abc")):
            with self.assertRaises(ValueError, msg=str(bad)):
                foreign_listing_fields(*bad)


# ===== the Yahoo resolve path agrees with the seed ======================================================================================
class UpsertAgreementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-foreign-upsert-")
        self.rt = make_runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upsert_writes_the_seed_classification(self):
        m = self.rt.instruments
        with self.rt.db.writer() as tx:
            vod = m.upsert_from_yahoo_meta(tx, "VOD.L", {"symbol": "VOD.L", "currency": "GBp", "exchangeName": "LSE", "fullExchangeName": "LSE",
                                                         "instrumentType": "EQUITY", "longName": "Vodafone Group Public Limited Company",
                                                         "exchangeTimezoneName": "Europe/London"})
            bas = m.upsert_from_yahoo_meta(tx, "BAS.DE", {"symbol": "BAS.DE", "currency": "EUR", "exchangeName": "GER", "instrumentType": "EQUITY",
                                                          "longName": "BASF SE", "exchangeTimezoneName": "Europe/Berlin"})
            zz = m.upsert_from_yahoo_meta(tx, "ZZTEST", {"symbol": "ZZTEST", "currency": "USD", "exchangeName": "NMS", "instrumentType": "EQUITY",
                                                         "longName": "ZZ Test Corp"})
        self.assertEqual(classification(m.get("SHEL-L")), classification(vod))  # GBP x 0.01, FOREIGN, T+2, FOREIGN_FX+PB, no Cboe, UNSUPPORTED
        self.assertEqual(classification(m.get("ALV-DE")), classification(bas))
        self.assertEqual(("VOD.L", "LSE", "Europe/London", "L"), (vod.yahoo_symbol, vod.spec["exchange"], vod.spec["tz"], vod.spec["venue_suffix"]))
        self.assertEqual(("FULL", "US_EQUITY", ("US_EXCHANGE", "PB"), 1, "ZZTEST", "USD"),
                         (zz.support, zz.session_group, zz.account_kinds, zz.settle_days, zz.cboe_symbol, zz.currency))   # US path unchanged

    def test_a_yahoo_symbol_another_row_owns_is_refused_with_its_id(self):
        m = self.rt.instruments
        for sym, owner in (("0700.HK", "0700-HK"), ("JPY=X", "USDJPY"), ("^GSPC", "SPX")):
            with self.assertRaises(RejectError) as cm:
                with self.rt.db.writer() as tx:
                    m.upsert_from_yahoo_meta(tx, sym, {"symbol": sym, "currency": "HKD", "exchangeName": "HKG", "instrumentType": "EQUITY"})
            self.assertEqual("ALREADY_IN_MASTER", cm.exception.code, sym)
            self.assertEqual(owner, cm.exception.details["instrument_id"], sym)
            self.assertIn(owner, cm.exception.message)
            self.assertIsNone(m.get(sym))                                        # nothing inserted
        self.assertEqual("0700-HK", m.by_yahoo_symbol("0700.HK").instrument_id)
        self.assertIsNone(m.by_yahoo_symbol("0700-HK"))                          # the old (wrong) symbol is nobody's now
        self.assertEqual("0700-HK", m.search("0700.HK")[0].instrument_id)       # typing the Yahoo code finds the listing
        self.assertEqual("USDJPY", m.search("JPY=X")[0].instrument_id)


# ===== the startup repair for databases seeded before the fix ===========================================================================
class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-foreign-repair-")
        self.rt = make_runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repair_fixes_mis_seeded_rows_once(self):
        rt = self.rt
        self.assertEqual({"repaired": [], "skipped": {}, "moved": {}}, repair(rt))           # a fresh seed has nothing to repair
        want = {iid: row_of(rt, iid) for iid in ("0700-HK", "SHEL-L")}          # the rows a fresh seed writes
        for iid in want:
            mis_seed(rt, iid)
        old = row_of(rt, "0700-HK")
        self.assertEqual(("USD", "US_EQUITY", '["US_EXCHANGE", "PB"]', "0700-HK", "0700.HK", "FULL", 1),
                         (old["currency"], old["session_group"], old["account_kinds"], old["yahoo_symbol"], old["cboe_symbol"], old["support"],
                          old["settle_days"]))
        rt.clock.advance(60)
        out = repair(rt)
        self.assertEqual(["0700-HK", "SHEL-L"], sorted(out["repaired"]))
        self.assertEqual({}, out["skipped"])
        ts = rt.clock.epoch()
        for iid, fresh in want.items():
            got = row_of(rt, iid)
            self.assertEqual(ts, got["updated_ts"], iid)
            self.assertEqual({k: v for k, v in fresh.items() if k != "updated_ts"}, {k: v for k, v in got.items() if k != "updated_ts"}, iid)
        hk = rt.instruments.get("0700-HK")                                       # the master's cache follows the repair
        self.assertEqual(("HKD", "0700.HK", FOREIGN_GROUP), (hk.currency, hk.yahoo_symbol, hk.session_group))
        self.assertEqual(Decimal("0.01"), rt.instruments.get("SHEL-L").price_scale)
        lines = repair_log_lines(rt)
        self.assertEqual(2, len(lines))                                         # one INFO line per run (the fresh-seed run, then this one)
        self.assertEqual("instrument repair (foreign universe listings): 2 corrected [0700-HK, SHEL-L]; 0 left as recorded [none]", lines[1])
        # the second run is a no-op: nothing corrected, rows byte-identical (updated_ts included)
        before = {iid: row_of(rt, iid) for iid in want}
        rt.clock.advance(60)
        self.assertEqual({"repaired": [], "skipped": {}, "moved": {}}, repair(rt))
        self.assertEqual(before, {iid: row_of(rt, iid) for iid in want})
        self.assertEqual("instrument repair (foreign universe listings): 0 corrected [none]; 0 left as recorded [none]", repair_log_lines(rt)[-1])
        self.assertEqual([], open_repair_attention(rt))

    def test_rows_referenced_by_an_open_order_or_a_position_are_left_and_reported(self):
        rt = self.rt
        for iid in ("7203-T", "6758-T", "8306-T", "9988-HK"):
            mis_seed(rt, iid)
        res = buy(rt, US_EXCHANGE, "6758-T", 10, "20.00")                       # a fill + LONG lot booked in USD on the mis-seeded row
        self.assertTrue(res.ok, res.body)
        insert_order(rt, "O-900001", "7203-T", "WORKING")                        # live: must block
        insert_order(rt, "O-900002", "8306-T", "EXPIRED")                        # history without fills: does not block
        insert_order(rt, "O-900003", "9988-HK", "CANCELLED")
        before = {iid: row_of(rt, iid) for iid in ("7203-T", "6758-T")}
        out = repair(rt)
        self.assertEqual(["8306-T", "9988-HK"], sorted(out["repaired"]))
        self.assertEqual({"7203-T": "open order O-900001 (WORKING) references it", "6758-T": "a position lot references it"}, out["skipped"])
        self.assertEqual(before, {iid: row_of(rt, iid) for iid in before})      # left exactly as recorded
        self.assertEqual(("JPY", "8306.T", FOREIGN_GROUP), (lambda i: (i.currency, i.yahoo_symbol, i.session_group))(rt.instruments.get("8306-T")))
        self.assertEqual([("6758-T", "WARN", "instruments"), ("7203-T", "WARN", "instruments")], open_repair_attention(rt))
        with rt.db.reader() as conn:
            msg = conn.execute("SELECT message FROM attention WHERE code=? AND ref_id='7203-T'", (REPAIR_CODE,)).fetchone()[0]
        self.assertIn("open order O-900001 (WORKING)", msg)
        self.assertIn("cancel that order", msg)
        self.assertEqual("instrument repair (foreign universe listings): 2 corrected [8306-T, 9988-HK]; 2 left as recorded"
                         " [6758-T: a position lot references it; 7203-T: open order O-900001 (WORKING) references it]", repair_log_lines(rt)[-1])
        # a restart with nothing changed: same answer, still ONE attention item per row
        out = repair(rt)
        self.assertEqual(([], ["6758-T", "7203-T"]), (out["repaired"], sorted(out["skipped"])))
        self.assertEqual(2, len(open_repair_attention(rt)))
        # the owner cancels the order: the next start corrects 7203-T and resolves its attention item; 6758-T stays (booked history)
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE orders SET status='CANCELLED' WHERE order_id='O-900001'")
        out = repair(rt)
        self.assertEqual((["7203-T"], ["6758-T"]), (out["repaired"], sorted(out["skipped"])))
        self.assertEqual(("JPY", "7203.T"), (lambda i: (i.currency, i.yahoo_symbol))(rt.instruments.get("7203-T")))
        self.assertEqual([("6758-T", "WARN", "instruments")], open_repair_attention(rt))

    @staticmethod
    def resolved_duplicate(rt, yahoo: str, name: str) -> None:
        """What the pre-fix resolve recorded when the owner typed the real Yahoo symbol of a mis-seeded listing: an UNSUPPORTED
        FOREIGN equity row with no account kinds, keyed and quoted by that symbol."""
        with rt.db.writer() as tx:
            rt.instruments._insert(tx.conn, tx.ts, instrument_id=yahoo, kind="EQUITY", symbol=yahoo, name=name, currency="HKD", session_group="FOREIGN",
                                   settle_days=2, account_kinds=[], yahoo_symbol=yahoo, spec={"exchange": "HKG"}, support="UNSUPPORTED")

    def test_a_resolved_duplicate_releases_the_yahoo_symbol(self):
        rt = self.rt
        m = rt.instruments
        mis_seed(rt, "0941-HK")                                                  # frees Yahoo 0941.HK ...
        self.resolved_duplicate(rt, "0941.HK", "CHINA MOBILE")                   # ... which a pre-fix resolve then took
        out = repair(rt)
        self.assertEqual({"repaired": ["0941-HK"], "skipped": {}, "moved": {"0941.HK": "0941-HK"}}, out)
        r = m.get("0941-HK")
        self.assertEqual(("HKD", FOREIGN_GROUP, FOREIGN_ACCOUNT_KINDS, "0941.HK", None, "UNSUPPORTED"),
                         (r.currency, r.session_group, r.account_kinds, r.yahoo_symbol, r.cboe_symbol, r.support))
        self.assertNotIn("yahoo_symbol_held_by", r.spec)
        dup = m.get("0941.HK")                                                   # kept (nothing is deleted), without a quote source
        self.assertEqual((None, "0941-HK", "UNSUPPORTED"), (dup.yahoo_symbol, dup.spec["yahoo_symbol_moved_to"], dup.support))
        self.assertEqual("instrument repair (foreign universe listings): 1 corrected [0941-HK]; 0 left as recorded [none]; Yahoo symbol of the"
                         " resolved duplicate 0941.HK moved to 0941-HK", repair_log_lines(rt)[-1])
        self.assertEqual({"repaired": [], "skipped": {}, "moved": {}}, repair(rt))

    def test_a_referenced_holder_keeps_the_yahoo_symbol(self):
        rt = self.rt
        m = rt.instruments
        mis_seed(rt, "1299-HK")
        self.resolved_duplicate(rt, "1299.HK", "AIA GROUP")
        insert_order(rt, "O-900010", "1299.HK", "WORKING", account=FOREIGN_FX)    # a holder something references is never changed
        holder = row_of(rt, "1299.HK")
        out = repair(rt)
        self.assertEqual({"repaired": ["1299-HK"], "skipped": {}, "moved": {}}, out)
        r = m.get("1299-HK")
        self.assertEqual(("HKD", FOREIGN_GROUP, None, "1299.HK"), (r.currency, r.session_group, r.yahoo_symbol, r.spec["yahoo_symbol_held_by"]))
        self.assertEqual(holder, row_of(rt, "1299.HK"))
        self.assertEqual({"repaired": [], "skipped": {}, "moved": {}}, repair(rt))


# ===== the markets endpoint and the quote path (server.build_runtime, offline) ===========================================================
def build(tmp: str):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                 books=[config.TEST_BOOK], fetch_threads=0, news_enabled=False)


def markets(rt, aid: str, tab: str) -> dict:
    """GET /api/accounts/{aid}/markets?tab= through the function the route calls (api_views.account_markets -> markets_view)."""
    return markets_view(Request(rt, "GET", f"/api/accounts/{aid}/markets", (aid,), {"tab": tab}, None, "127.0.0.1", {}), aid, tab)


class MarketsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-foreign-markets-")
        self.rt = build(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_us_exchange_lists_exclude_foreign_rows_and_the_repair_removes_them(self):
        rt = self.rt
        eq = markets(rt, US_EXCHANGE, "equities")
        ids = [r["instrument_id"] for r in eq["rows"]]
        # every ACTIVE FULL US equity / ETF of the master (no 60-row cap since 2026-10-21: the owner wants all of them)
        us_full = {i.instrument_id for i in rt.instruments.all() if i.kind in ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")
                   and i.status == "ACTIVE" and i.support == "FULL" and "US_EXCHANGE" in i.account_kinds}
        self.assertEqual(us_full, set(ids))
        self.assertEqual(["A", "AA", "AAL", "AAPL"], ids[:4])                    # sorted by symbol; no foreign row ahead of the US names
        self.assertEqual(set(), FOREIGN_IDS & set(ids))
        self.assertEqual({("USD", "US_EQUITY")}, {(r["currency"], r["session_group"]) for r in eq["rows"]})
        self.assertEqual(set(), FOREIGN_IDS & {r["instrument_id"] for r in markets(rt, US_EXCHANGE, "options")["rows"]})
        # the owner's screen: mis-seeded rows sort first (digits) and lead both lists, the old Cboe symbol made them "optionable"
        for iid in ("0700-HK", "000660-KS"):
            mis_seed(rt, iid)
        self.assertEqual(["000660-KS", "0700-HK"], [r["instrument_id"] for r in markets(rt, US_EXCHANGE, "equities")["rows"]][:2])
        self.assertEqual({"000660-KS", "0700-HK"}, FOREIGN_IDS & {r["instrument_id"] for r in markets(rt, US_EXCHANGE, "options")["rows"]})
        self.assertEqual(["000660-KS", "0700-HK"], sorted(repair(rt)["repaired"]))
        self.assertEqual(set(), FOREIGN_IDS & {r["instrument_id"] for r in markets(rt, US_EXCHANGE, "equities")["rows"]})
        self.assertEqual(set(), FOREIGN_IDS & {r["instrument_id"] for r in markets(rt, US_EXCHANGE, "options")["rows"]})

    def test_account_search_puts_foreign_rows_in_foreign_markets(self):
        rt = self.rt
        status, hits, _ = rt.router.handle("GET", f"/api/instruments?q=0700&account={FOREIGN_FX}")
        self.assertEqual(200, status)
        self.assertEqual(["0700-HK"], [h["instrument_id"] for h in hits])
        self.assertEqual(("HKD", "UNSUPPORTED", FOREIGN_GROUP), (hits[0]["currency"], hits[0]["support"], hits[0]["session_group"]))
        status, hits, _ = rt.router.handle("GET", f"/api/instruments?q=0700&account={US_EXCHANGE}")
        self.assertEqual((200, []), (status, hits))
        status, hits, _ = rt.router.handle("GET", f"/api/instruments?q=SHEL&account={FOREIGN_FX}")
        self.assertIn("SHEL-L", [h["instrument_id"] for h in hits])

    def test_yahoo_prints_reach_the_foreign_rows_through_the_corrected_symbol(self):
        rt = self.rt
        mis_seed(rt, "6758-T")                                                    # still on the old Yahoo symbol "6758-T"
        now = rt.clock.epoch()
        rt.fp.set("0700.HK", last="612.50", quote_time=now - 300, currency="HKD", exchange_tz="Asia/Hong_Kong")
        rt.fp.set("SHEL.L", last="2712.50", quote_time=now - 300, currency="GBp", exchange_tz="Europe/London")
        rt.fp.set("6758.T", last="3301.00", quote_time=now - 300, currency="JPY", exchange_tz="Asia/Tokyo")
        rt.cache.subscribe(["0700-HK", "SHEL-L", "6758-T"], 300, "PAGE")
        rt.worker.tick()
        hk, shel, sony = rt.cache.get("0700-HK"), rt.cache.get("SHEL-L"), rt.cache.get("6758-T")
        self.assertEqual((Decimal("612.50"), "HKD", FOREIGN_GROUP, None), (hk.last, hk.currency, hk.session_group, hk.error))
        self.assertEqual((Decimal("2712.50"), "GBP"), (shel.last, shel.currency))   # the mark stays as quoted (pence)
        # accounting applies the scale: 100 shares = 100 x 2,712.50 x 1 x 0.01 = GBP 2,712.50 = 271,250 cents
        self.assertEqual(271250, rt.instruments.get("SHEL-L").contract_value_c(shel.last, 100))
        self.assertIsNone(sony.last)                                             # the owner's "N/A forever": Yahoo has no "6758-T"
        self.assertEqual("6758-T: not found", sony.error)                       # the 404 for the ticker used as a Yahoo symbol
        self.assertEqual(["6758-T"], repair(rt)["repaired"])
        rt.clock.advance(3601)                                                    # past the cache's hourly retry of a 404 symbol
        rt.cache.subscribe(["6758-T"], 300, "PAGE")
        rt.worker.tick()
        sony = rt.cache.get("6758-T")
        self.assertEqual((Decimal("3301.00"), "JPY", None), (sony.last, sony.currency, sony.error))

    def test_resolving_the_yahoo_symbol_of_a_seeded_listing_names_the_row(self):
        rt = self.rt
        rt.fp.set_meta("0700.HK", {"symbol": "0700.HK", "currency": "HKD", "exchangeName": "HKG", "instrumentType": "EQUITY", "longName": "TENCENT"})
        rt.worker.resolve("0700.HK")
        rt.worker.tick()
        st = rt.worker.resolve_status("0700.HK")
        self.assertEqual(("error", 400), (st["status"], st["http_status"]))      # a refusal (RejectError), not a 500 crash
        self.assertIn("0700-HK", st["message"])
        self.assertIsNone(rt.instruments.get("0700.HK"))
        with rt.db.reader() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM attention WHERE code='COMMAND_FAILED'").fetchone()[0])

    def test_foreign_markets_listings_tab_lists_the_foreign_rows(self):
        """The FOREIGN_FX 'listings' tab fills from the master (``"FOREIGN_FX" in account_kinds``) after the held / working /
        watched rows, so all 38 seeded foreign listings are listed (orchestrator applied the interface request 2026-10-21)."""
        out = markets(self.rt, FOREIGN_FX, "listings")
        self.assertEqual("listings", out["tab"])
        ids = {r["instrument_id"] for r in out["rows"]}
        self.assertEqual(set(), FOREIGN_IDS - ids)


if __name__ == "__main__":
    unittest.main()
