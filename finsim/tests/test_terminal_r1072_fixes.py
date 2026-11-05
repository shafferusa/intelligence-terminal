"""1.0.7.2 Phase 0 fixes (CHANGE_PLAN 1.2, 1.3, 1.5): CHAIN-LEGS-1, PREVIEW-SHORT-NOQUOTE and the 1.0.6.1 open items 2, 6 and 9.

- CHAIN-LEGS-1: a chain snapshot is the same whatever order Cboe's file lists its contracts in (sorted by expiry, strike, right, then the
  standard root first); an exact duplicate OCC keeps the entry with a usable NBBO; every skipped entry is counted by reason (``drops``);
  the chain view's cell takes the first (preferred) contract and never lets a second root overwrite a leg (it goes to ``alt``); strikes that
  list one side only are named (``one_sided`` and the note); ``ChainSnapshot.row`` and the hedge panel's ``_row`` agree.
- PREVIEW-SHORT-NOQUOTE: a short sale of a name never quoted is refused NO_BORROW (no mark to size the 102 % collateral) and now also asks
  for the name's quote (PAGE), so the next preview locates on the fetched mark.
- 1.0.6.1 items: a USD rate reads "USD per GBP" (never "USD/GBP"); an FX forward's model words follow its pricing method (the curves or the
  legacy method, also for a contract booked before 1.0.6.1 and for a stale mark); the IM source of the legacy OTC kinds says ASSUMED.

Fictional ACME / WIDG with invented prices; the OTC tests reuse the forwards tests' runtime (F1's prime-broker account, the FX pairs of the
test universe).  No network, no port, no ``~/.finsim``.
"""
from __future__ import annotations

import inspect
import json
import os
import random
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from tests import test_terminal_otc_forwards as ofw
from tests.terminal_helpers import _Log, execute, make_runtime
from finsim.terminal import config, fx, hedges, orders_preview, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.commands import ConflictError
from finsim.terminal.products import options as _options, otc_options, swaps
from finsim.terminal.quotes.parsers import CHAIN_DROP_REASONS, parse_chain
from finsim.terminal.quotes.providers import FakeProvider

D = Decimal
AT = "2026-10-20T14:00:00-04:00"            # Tuesday 14:00 ET: the option and equity sessions are open
TODAY = date(2026, 10, 20)
EXPIRY = "2026-12-18"
STRIKES = (100, 105, 110, 115, 120, 125)
SPOT = 112.5
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(otc_options.__file__))), "static", "components")


def occ(right: str, k: int, root: str = "ACME", yymmdd: str = "261218") -> str:
    return f"{root}{yymmdd}{right}{k * 1000:08d}"


def leg(right: str, k: int, root: str = "ACME", quoted: bool = True) -> dict:
    """One Cboe-shaped contract with invented prices: intrinsic value + 2.00, a 0.20-wide market (an unquoted one shows 0 / 0)."""
    intrinsic = max(SPOT - k, 0) if right == "C" else max(k - SPOT, 0)
    bid = round(intrinsic + 2.0, 2) if quoted else 0.0
    ask = round(bid + 0.2, 2) if quoted else 0.0
    return {"option": occ(right, k, root), "bid": bid, "bid_size": 20.0 if quoted else 0.0, "ask": ask, "ask_size": 25.0 if quoted else 0.0, "iv": 0.30,
            "open_interest": float(100 + k), "volume": 10.0, "delta": 0.5, "gamma": 0.02, "vega": 0.15, "theta": -0.05, "last_trade_price": bid,
            "last_trade_time": "2026-10-20T13:55:00"}


def base_options() -> list:
    return [leg(r, k) for k in STRIKES for r in ("C", "P")]


def payload(options: list) -> dict:
    return {"timestamp": "2026-10-20 14:00:00", "symbol": "ACME",
            "data": {"symbol": "ACME", "current_price": SPOT, "bid": 112.45, "ask": 112.55, "bid_size": 3, "ask_size": 4, "prev_day_close": 111.8,
                     "iv30": 31.2, "last_trade_time": "2026-10-20T13:59:30", "options": options}}


class ChainLegsTests(unittest.TestCase):
    """CHAIN-LEGS-1 on a Cboe-shaped ACME file (expiry 2026-12-18, strikes 100 .. 125, both rights quoted)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-r1072-legs-")
        self.rt = make_runtime(self.tmp, at=AT)
        with self.rt.db.writer() as tx:
            self.rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME Test Co (fictional)", currency="USD",
                                       session_group="US_EQUITY", account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME")
        self.rt.instruments.load()
        self.now = int(self.rt.clock.epoch())

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self, options):
        return parse_chain(payload(options), "ACME", "ACME", TODAY, self.now)

    def view(self, snap) -> dict:
        return _options.chain_view(self.rt, "ACME", EXPIRY, chain=snap, now=self.now)

    def cell(self, view: dict, strike: str) -> dict:
        return {r["strike"]: r for r in view["rows"]}[strike]

    def test_every_contract_of_the_file_is_in_the_snapshot_once(self):
        snap = self.snap(base_options())
        keys = [(c.expiry, c.right, c.strike) for c in snap.contracts]
        self.assertEqual(sorted((EXPIRY, r, D(k)) for k in STRIKES for r in ("C", "P")), sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(({}, 12, 12), (snap.drops, snap.n_raw, snap.n_kept()))
        # sorted by (expiry, strike, right): 100 C, 100 P, 105 C ...
        self.assertEqual([(D(k), r) for k in STRIKES for r in ("C", "P")], [(c.strike, c.right) for c in snap.contracts])
        v = self.view(snap)
        self.assertEqual(([str(k) for k in STRIKES], [], {}), ([r["strike"] for r in v["rows"]], v["one_sided"], v["drops"]))
        self.assertTrue(all(r["call"] and r["put"] and "alt" not in r for r in v["rows"]))
        self.assertIsNone(v["note"])

    def test_an_unquoted_duplicate_never_replaces_the_quoted_leg(self):
        """Cboe lists the 110 call twice (the same OCC), once unquoted: placed after the quoted entry or before it, the snapshot and the chain
        view keep the quoted one and count one duplicate (1.0.7.1 kept whichever came last in the view: 'no bid / no ask')."""
        dup = leg("C", 110, quoted=False)
        for name, options in (("after", base_options() + [dup]), ("before", [dup] + base_options())):
            snap = self.snap(options)
            self.assertEqual({"duplicate": 1}, snap.drops, name)
            self.assertEqual(12, snap.n_kept(), name)
            row = snap.row(EXPIRY, "C", 110)
            self.assertEqual((occ("C", 110), D("4.5"), D("4.7"), True), (row.occ, row.bid, row.ask, row.quoted), name)  # 112.5 - 110 + 2.00
            c = self.cell(self.view(snap), "110")["call"]
            self.assertEqual((occ("C", 110), "4.5", "4.7", "delayed"), (c["instrument_id"], c["bid"], c["ask"], c["quality"]), name)
            self.assertEqual({"duplicate": 1}, self.view(snap)["drops"], name)
        # two unquoted copies: the first is kept, still one row
        snap = self.snap([leg("C", 110, quoted=False), leg("C", 110, quoted=False)])
        self.assertEqual(({"duplicate": 1}, 1, False), (snap.drops, snap.n_kept(), snap.row(EXPIRY, "C", 110).quoted))

    def test_a_second_root_goes_to_alt_and_never_into_the_cell(self):
        """A second root at the same expiry, right and strike (an adjusted series ACME1): the standard root ACME keeps the cell wherever the
        file lists it; the other contract is in row["alt"]["call"]; every first-match consumer takes the standard one."""
        other = leg("C", 110, root="ACME1", quoted=False)
        for name, options in (("after", base_options() + [other]), ("before", [other] + base_options())):
            snap = self.snap(options)
            self.assertEqual(({}, 13), (snap.drops, snap.n_kept()), name)
            self.assertEqual([occ("C", 110), occ("C", 110, "ACME1")], [c.occ for c in snap.rows_for(EXPIRY) if c.right == "C" and c.strike == 110], name)
            row = self.cell(self.view(snap), "110")
            self.assertEqual(occ("C", 110), row["call"]["instrument_id"], name)
            self.assertEqual({"call": [occ("C", 110, "ACME1")]}, {k: [d["instrument_id"] for d in v] for k, v in row["alt"].items()}, name)
            self.assertEqual("unavailable", row["alt"]["call"][0]["quality"], name)
            self.assertIs(snap.row(EXPIRY, "C", 110), snap.by_occ(occ("C", 110)), name)
        # an index-style pair: the root without the Cboe symbol's "_" is the standard one (SPX beside SPXW, here _ACME beside ACMEW)
        idx = parse_chain(payload([leg("C", 110, root="ACMEW"), leg("C", 110)]), "_ACME", "ACME", TODAY, self.now)
        self.assertEqual([occ("C", 110), occ("C", 110, "ACMEW")], [c.occ for c in idx.contracts])

    def test_a_quoted_second_root_is_never_hidden_by_an_unquoted_standard_root(self):
        """1.0.7.2 review: when the standard root of a cell shows no market and another root of the same expiry, right and strike does, the
        quoted contract takes the chain cell (its root named on it, so chain.js can say it) and the unquoted one goes to row["alt"]; the
        snapshot's own row() keeps the standard root for the hedge panel and the strategies."""
        snap = self.snap([o for o in base_options() if o["option"] != occ("C", 110)] + [leg("C", 110, quoted=False), leg("C", 110, root="ACMEW")])
        row = self.cell(self.view(snap), "110")
        self.assertEqual((occ("C", 110, "ACMEW"), "ACMEW"), (row["call"]["instrument_id"], row["call"].get("root")))
        self.assertNotEqual("unavailable", row["call"]["quality"])
        self.assertEqual([(occ("C", 110), "unavailable")], [(d["instrument_id"], d["quality"]) for d in row["alt"]["call"]])
        self.assertEqual(occ("C", 110), snap.row(EXPIRY, "C", 110).occ)
        with open(os.path.join(os.path.dirname(os.path.abspath(_options.__file__)), "..", "static", "components", "chain.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("r.alt", js)                                                 # the page lists the other roots of a cell
        self.assertIn("leg.root", js)

    def test_a_strike_listing_one_side_is_named(self):
        snap = self.snap([o for o in base_options() if o["option"] != occ("C", 115)])
        self.assertEqual([{"strike": "115", "has": "put"}], snap.one_sided(EXPIRY))
        v = self.view(snap)
        row = self.cell(v, "115")
        self.assertIsNone(row["call"])
        self.assertEqual(occ("P", 115), row["put"]["instrument_id"])
        self.assertEqual([{"strike": "115", "has": "put"}], v["one_sided"])
        self.assertEqual("1 strike of this expiry lists only one side in this Cboe snapshot", v["note"])
        snap = self.snap([o for o in base_options() if o["option"] not in (occ("C", 115), occ("P", 125))])
        self.assertEqual([{"strike": "115", "has": "put"}, {"strike": "125", "has": "call"}], snap.one_sided(date(2026, 12, 18)))
        self.assertEqual("2 strikes of this expiry list only one side in this Cboe snapshot", self.view(snap)["note"])
        self.assertEqual([], snap.one_sided("2027-01-15"))                   # an expiry the snapshot does not list

    def test_two_file_orders_give_byte_identical_chain_views(self):
        options = base_options() + [leg("C", 110, quoted=False), leg("P", 105, root="ACME1"), "not a contract", {"option": "NOPE"}]
        shuffled = list(options)
        random.Random(1072).shuffle(shuffled)
        a, b = self.snap(options), self.snap(list(reversed(options)))
        c = self.snap(shuffled)
        self.assertEqual([x.occ for x in a.contracts], [x.occ for x in b.contracts])
        self.assertEqual([x.occ for x in a.contracts], [x.occ for x in c.contracts])
        ja, jb, jc = (json.dumps(self.view(s), default=str) for s in (a, b, c))
        self.assertEqual(ja, jb)
        self.assertEqual(ja, jc)
        self.assertEqual({"not_a_dict": 1, "bad_symbol": 1, "duplicate": 1}, a.drops)

    def test_every_skipped_entry_is_counted_by_reason(self):
        options = base_options() + ["junk", {"option": "BADSYMBOL"}, {"option": occ("C", 110, yymmdd="261318")},        # month 13
                                    {"option": occ("C", 110, yymmdd="251218")},                                         # expired
                                    {"option": occ("C", 110, yymmdd="281218")},                                         # 790 days out
                                    {"option": occ("C", 300)}, leg("P", 100)]                                           # out of band; a duplicate
        snap = self.snap(options)
        self.assertEqual(dict.fromkeys(CHAIN_DROP_REASONS, 1), snap.drops)
        self.assertEqual(list(CHAIN_DROP_REASONS), list(snap.drops))         # always in the reasons' order (deterministic JSON)
        self.assertEqual((12, 18), (snap.n_kept(), snap.n_raw))               # n_raw counts the dict entries, as before
        self.assertEqual(len(options), snap.n_kept() + sum(snap.drops.values()))

    def test_the_hedge_panels_row_and_the_snapshots_row_agree(self):
        options = base_options() + [leg("C", 110, root="ACME1"), leg("P", 120, quoted=False)]
        random.Random(7).shuffle(options)
        snap = self.snap(options)
        for k in STRIKES:
            for r in ("C", "P"):
                got = snap.row(EXPIRY, r, D(k))
                self.assertIs(hedges._row(snap, EXPIRY, r, D(k)), got, (k, r))
                self.assertEqual(occ(r, k), got.occ)
                self.assertIs(got, snap.row(EXPIRY, {"C": "call", "P": "put"}[r], str(k)))          # words and text also read
        self.assertIsNone(snap.row(EXPIRY, "C", D(130)))
        self.assertIsNone(snap.row(EXPIRY, "X", D(110)))
        self.assertIsNone(snap.row(EXPIRY, "C", "not a strike"))


# ===== PREVIEW-SHORT-NOQUOTE ===============================================================================================================
class PreviewShortTests(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        self.tmp = tempfile.mkdtemp(prefix="fst-r1072-short-")
        os.environ[config.HOME_ENV] = self.tmp
        os.environ[config.DB_ENV] = os.path.join(self.tmp, "terminal.db")
        clock = FakeClock(AT)
        self.rt = _server.build_runtime(self.tmp, os.path.join(self.tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock),
                                        log=_Log(), books=[config.TEST_BOOK], fetch_threads=0, news_enabled=False)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_a_ticket_on_a_held_option_asks_for_its_chain_ahead_of_the_pinned_refreshes(self):
        """CHAIN-FETCH-1 (1.0.7.2 review): the ticket's preview asks for an option's chain itself at REQUEST - for an option the owner
        holds, the best quote pin is HELD, which maps to a REFRESH, so the ticket's chain used to wait behind every pinned refresh."""
        rt = self.rt
        with rt.db.writer() as tx:
            for u in ("WIDG1", "WIDG2"):
                rt.instruments.upsert(tx, instrument_id=u, kind="EQUITY", symbol=u, name=f"{u} Test Co (fictional)", currency="USD",
                                      session_group="US_EQUITY", account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=u, cboe_symbol=u)
        rt.instruments.load()
        o = occ("C", 100, root="WIDG2")
        with rt.db.writer() as tx:
            rt.instruments.ensure_option(tx, o, rt.instruments.get("WIDG2", tx.conn), {"option": o})
        rt.instruments.load()
        rt.chains.set_pins(["WIDG1", "WIDG2"])
        self.assertEqual(["WIDG1", "WIDG2"], rt.chains.due(int(rt.clock.epoch())))
        rt.cache.pin([o], "HELD")
        orders_preview._ask_for_quote(rt, o)                                        # what the ticket's preview does without a quote
        self.assertEqual(["HELD", "PAGE"], rt.cache.priorities(o))
        self.assertEqual("REQUEST", rt.chains.stats()["pending_priority"]["WIDG2"])
        self.assertEqual(["WIDG2", "WIDG1"], rt.chains.pending())                   # the ticket's chain first

    def test_a_short_of_a_never_quoted_name_asks_for_its_quote(self):
        rt = self.rt
        with rt.db.writer() as tx:
            rt.instruments.upsert(tx, instrument_id="WIDG", kind="EQUITY", symbol="WIDG", name="WIDG Test Co (fictional)", currency="USD",
                                  session_group="US_EQUITY", account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="WIDG", spec={"adv": 500000})
        rt.instruments.load()
        req = {"account_id": "T1:US_EXCHANGE", "instrument_id": "WIDG", "side": "SELL_SHORT", "qty": 100, "order_type": "LIMIT", "limit_px": "20.00",
               "tif": "DAY"}
        self.assertEqual([], rt.cache.priorities("WIDG"))
        pv = orders_preview.preview(rt, dict(req))
        self.assertEqual(["NO_BORROW"], [b["code"] for b in pv["blockers"]])
        self.assertIn("no mark to size the 102 % collateral", pv["blockers"][0]["message"])
        self.assertIn("PAGE", rt.cache.priorities("WIDG"))                    # 1.0.7.1 never asked: the short could never be previewed
        # the worker's next tick fetches the name (a never-observed PAGE name goes first), then the ticket's next preview locates on its mark
        now = int(rt.clock.epoch())
        qt = now - 120
        rt.fp.set("WIDG", last="20.05", quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
        self.assertIn("WIDG", rt.worker.tick()["fetched"])
        pv = orders_preview.preview(rt, dict(req))
        self.assertEqual([], [b for b in pv["blockers"] if b["code"] == "NO_BORROW"], pv["blockers"])
        b = pv["borrow"]
        # ADV 500,000 and no FINRA row: tier T3, 10 % of ADV available; collateral 102 % x 100 x 20.05 = 2,045.10
        self.assertEqual((True, True, "T3", 100, "20.05"), (b["needed"], b["available"], b["tier"], b["qty_available"], str(b["mark"])))
        self.assertEqual(204510, b["collateral_c"])


# ===== 1.0.6.1 open items 2, 6, 9 (display text) ===========================================================================================
class OtcDisplayTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-r1072-otc-")
        self.rt = ofw.runtime(self.tmp)
        ofw.curves(self.rt)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def terms(self, cid: str) -> dict:
        with self.rt.db.reader() as conn:
            return json.loads(conn.execute("SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()[0])

    def book(self, terms: dict, rid: str) -> str:
        r = execute(self.rt, "otc.book", {"account_id": ofw.PB, "kind": "FX_FORWARD", "terms": dict(terms)}, rid)
        self.assertTrue(r.ok, r.body)
        return r.body["contract_id"]

    def test_item6_the_forward_model_words_follow_its_pricing_method(self):
        """The drawer of a forward on the curves showed the legacy 'F = CIP outright ... (fx.forward_rate)' words: now each contract reads the
        model of its own stored method - the curves, or the legacy method with its LIMITED label - without restating anything."""
        rt = self.rt
        legacy_words = otc_options.MODELS["FX_FORWARD"] + " - " + otc_options.LEGACY_LABEL
        cur = self.book(ofw.FWD, "ui:r1072-fwd-curves")                         # EURUSD 3M: on the term curves
        old = self.book(dict(ofw.FWD, pair="USDJPY"), "ui:r1072-fwd-legacy")    # USDJPY: no JPY curve, the legacy method
        self.assertEqual(("CURVES", otc_options.CURVES_MODEL), (self.terms(cur)["pricing_method"], self.terms(cur)["model"]))
        self.assertEqual(("LEGACY", legacy_words), (self.terms(old)["pricing_method"], self.terms(old)["model"]))
        self.assertEqual((otc_options.CURVES_MODEL, legacy_words), (otc_options.contract_detail(rt, cur)["model"], otc_options.contract_detail(rt, old)["model"]))
        _value, inputs = otc_options.mark(rt, old)                              # the legacy forward's live mark
        self.assertEqual(legacy_words, inputs["model"])
        with mock.patch.object(otc_options, "_curve_fwd_value", side_effect=ConflictError("NO_QUOTE", "EURUSD: no observed rate (test)")):
            mv = otc_options.mark(rt, cur)                                      # an input missing now: the stored mark, labelled stale ...
        self.assertEqual(("stale", otc_options.CURVES_MODEL), (mv.quality, mv.inputs["model"]))   # ... still with its own model words
        # a forward booked before 1.0.6.1 stored no method: it reads the legacy method's words (its terms are not rewritten)
        t = self.terms(old)
        t.pop("pricing_method")
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE otc_contracts SET terms_json=? WHERE contract_id=?", (json.dumps(t), old))
        self.assertEqual(legacy_words, otc_options.contract_detail(rt, old)["model"])
        self.assertNotIn("pricing_method", self.terms(old))
        # the other kinds keep their own words
        self.assertEqual(otc_options.MODELS["EQ_OPTION"], otc_options._model_text("EQ_OPTION", "CURVES"))
        self.assertEqual(otc_options.CURVES_MODEL, otc_options._model_text("FX_FORWARD", " curves "))

    def test_item2_a_usd_rate_reads_usd_per_the_currency(self):
        rt = self.rt
        old = self.book(dict(ofw.FWD, pair="USDJPY"), "ui:r1072-fwd-usd-per")
        _value, inputs = otc_options.mark(rt, old)
        self.assertRegex(inputs["formula"], r" x [0-9.]+ USD per JPY$")
        # an FX option's settlement at the observed close: EUR 1,000,000.00 x max(0, 0.8700 - 0.8500) = 0.0200 GBP per unit x 1.3300 USD per GBP
        # = 26,600.00 (invented terms)
        cents, words = otc_options._payoff_c("FX_OPTION", {"side": "BUY", "strike": "0.8500", "right": "C", "pair": "EURGBP", "notional_c": 100000000},
                                             D("0.8700"), {"usd_per_quote": "1.3300"})
        self.assertEqual((2660000, "+EUR 1,000,000.00 x max(0, S - K) = 0.0200 GBP per unit x 1.3300 USD per GBP"), (cents, words))
        for mod in (otc_options, fx):                                           # every formula of the two modules
            src = inspect.getsource(mod)
            self.assertNotIn("USD/{", src, mod.__name__)
        self.assertEqual(4, inspect.getsource(otc_options).count(" USD per {"))
        self.assertIn("USD per {quote_ccy}", inspect.getsource(fx))

    def test_item9_the_legacy_otc_kinds_say_their_im_source_is_assumed(self):
        rt = self.rt
        with rt.db.writer() as tx:
            rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME Test Co (fictional)", currency="USD",
                                  session_group="US_EQUITY", account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", spec={"dividend_yield": 0.0})
        rt.instruments.load()
        rt.marks.set("ACME", "50.00", "delayed", rt.clock.epoch())
        eq = swaps.preview(rt, ofw.PB, "EQ_FORWARD", {"underlying_id": "ACME", "units": 100, "side": "BUY", "tenor": "3M"})
        fwd = swaps.preview(rt, ofw.PB, "FX_FORWARD", dict(ofw.FWD, pair="USDJPY"))
        cur = swaps.preview(rt, ofw.PB, "FX_FORWARD", dict(ofw.FWD))            # the curves forward said it since 1.0.6.1
        for p in (eq, fwd, cur):
            self.assertTrue(p["im_rule"].endswith(otc_options.ASSUMED_SUFFIX + ")"), p["im_rule"])
            self.assertEqual(1, p["im_rule"].count("ASSUMED"), p["im_rule"])
        src = inspect.getsource(otc_options)
        self.assertNotIn('"im_source": imp.get("source")', src)                # no deal builder passes the source on unlabelled
        self.assertEqual(5, src.count('assumed_source(imp.get("source"))'))    # EQ option, FX option, EQ forward, legacy and curves FX forward


# ===== the two components (static) ===========================================================================================================
class ComponentTextTests(unittest.TestCase):
    def read(self, name: str) -> str:
        with open(os.path.join(STATIC, name), "rb") as fh:
            return fh.read().decode("ascii")                                    # raises on a non-ASCII byte

    def test_the_chain_head_names_one_sided_strikes(self):
        js = self.read("chain.js")
        self.assertIn("var os = data.one_sided || [];", js)
        self.assertIn("(os.length === 1 ? ' strike lists' : ' strikes list') + ' one side only (Cboe file)'", js)
        self.assertIn("return x.strike + ' ' + x.has + ' only';", js)

    def test_the_cds_risk_panel_says_unavailable_and_the_bucket_of_the_mid(self):
        js = self.read("cdsnames.js")
        self.assertIn("label: 'bucket (mid)'", js)
        self.assertIn("title: function (r) { return r.model_bucket ? 'model bucket ' + s(r.model_bucket) + '-like' : ''; }", js)
        self.assertIn("var tot = function (key, signed) { return hasMoney(block, key) ? (signed ? signedMoney(block, key) : money(block, key)) : 'unavailable'; };",
                      js)
        for key in ("tot('cs01', true)", "tot('jtd_gain')", "tot('jtd_loss')", "tot('net_jtd', true)", "(block.partial ? ' (the totals exclude them)' : '')"):
            self.assertIn(key, js)


if __name__ == "__main__":
    unittest.main()
