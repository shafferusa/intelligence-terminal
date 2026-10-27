"""WP-03 hedge-candidate tests (TERMINAL_DESIGN.md §10.9, §19.9 D7).  Every expected number is worked by hand in a comment.

The chain is a fake Cboe snapshot of SPY (spot 769.32) with strikes 640…900 step 5 on three expiries (2026-11-20, 2027-01-15,
2027-02-19) whose prices follow a formula so every pin is hand-checkable: put bid = max(0.10, (K − 640) × 0.08), call bid =
max(0.10, (900 − K) × 0.08), ask = bid + 0.10; call delta = clip(0.5 + (769.32 − K) × 0.01, 0.02, 0.98), put delta = call − 1.
No network, no ~/.finsim.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests import test_terminal_orders as sc
from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, execute, fund, pb
from finsim.terminal import books, hedges
from finsim.terminal.commands import RejectError
from finsim.terminal.instruments import parse_occ
from finsim.terminal.products import options
from finsim.terminal.quotes.cache import ChainCache
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying

C760 = "SPY261120C00760000"
P740 = "SPY261120P00740000"
P715_FEB = "SPY270219P00715000"
STRIKES = list(range(640, 905, 5))
EXPIRIES = ("2026-11-20", "2027-01-15", "2027-02-19")        # 31, 87 and 122 days after 2026-10-20
SPOT = Decimal("769.32")


def D(x) -> Decimal:
    return Decimal(str(x))


def occ(expiry: str, right: str, k: int) -> str:
    y, m, d = expiry.split("-")
    return f"SPY{y[2:]}{m}{d}{right}{k * 1000:08d}"


def price(right: str, k: int):
    bid = max(0.10, (k - 640) * 0.08) if right == "P" else max(0.10, (900 - k) * 0.08)
    return D(f"{bid:.2f}"), D(f"{bid + 0.10:.2f}")


def delta_of(right: str, k: int) -> float:
    c = min(0.98, max(0.02, round(0.5 + (float(SPOT) - k) * 0.01, 4)))
    return round(c if right == "C" else c - 1.0, 4)


def chain(now: int, fetched_at=None, expiries=EXPIRIES, sizes=(50, 50)) -> ChainSnapshot:
    rows = []
    for e in expiries:
        for k in STRIKES:
            for right in ("C", "P"):
                bid, ask = price(right, k)
                rows.append(ChainRow(occ(e, right, k), e, right, D(k), bid, ask, sizes[0], sizes[1], None, None, 0.2, delta_of(right, k), 0.01, -0.05, 0.8, 100, 10))
    under = ChainUnderlying("SPY", SPOT, D("769.30"), D("769.34"), 10, 10, now - 100, D("765.00"), 0.18)
    return ChainSnapshot("SPY", "SPY", under, rows, now - 30 if fetched_at is None else fetched_at, now - 100, len(rows))


def set_beta(rt, iid: str, beta) -> None:
    with rt.db.writer() as tx:
        row = tx.conn.execute("SELECT spec_json FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
        spec = json.loads(row["spec_json"] or "{}")
        if beta is None:
            spec.pop("beta", None)
        else:
            spec["beta"] = beta
        tx.conn.execute("UPDATE instruments SET spec_json=? WHERE instrument_id=?", (json.dumps(spec), iid))
    rt.instruments.refresh(iid)


def by_id(cands):
    return {c.id: c for c in cands}


class HedgeCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-hdg-")
        self.rt = sc.runtime(self.tmp)
        options.register(self.rt.bus)
        self.rt.options = options
        self.rt.hedges = hedges
        self.now = self.rt.clock.epoch()
        self.spy = self.rt.instruments.get("SPY")
        set_beta(self.rt, "SPY", 1.0)
        self.rt.marks.set("ESZ26", "6250.00")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cands(self, side, qty, inst=None, chain_=None, account=US_EXCHANGE, edits=None):
        return hedges.candidates(self.rt, account, inst or self.spy, side, qty, chain=chain_ if chain_ is not None else chain(self.now), edits=edits)

    def test_long_equity_put_collar_spread(self):
        c = by_id(self.cands("BUY", 1000))
        self.assertEqual(["COLLAR_7", "INDEX_OVERLAY", "PUT_7PCT", "PUT_SPREAD"], sorted(c))
        # expiry: nearest listed >= 90 days -> 2027-02-19 (2027-01-15 is 87 days out); strikes snap: 0.93 x 769.32 = 715.4676 -> 715,
        # 1.07 x 769.32 = 823.1724 -> 825 (820 is 3.17 away, 825 is 1.83), 0.85 x 769.32 = 653.922 -> 655; n = floor(1,000 / 100) = 10
        p = c["PUT_7PCT"]
        self.assertEqual(("2027-02-19", True, "delayed", self.now - 100), (p.expiry, p.available, p.chain_quality, p.as_of))
        self.assertEqual([(P715_FEB, "BTO", 10, "ask", D("6.10"))], [(l.instrument_id, l.side, l.qty, l.price_basis, l.est_price) for l in p.legs])
        # cost = ask 6.10 x 100 x 10 = 6,100.00; fees 10 x 0.65 = 6.50; net cash -6,106.50; protection 715 / 769.32 - 1 = -0.0706
        self.assertEqual((D("6100.00"), D("6.50"), D("-6106.50"), D("-0.0706")), (p.cost, p.fees, p.net_cash, p.protection_level))
        self.assertEqual({"strike": True, "expiry": True, "ratio": True}, p.editable)
        self.assertEqual(("6.00", "6.10", 50, 50, self.now - 100), (lambda q: (q["bid"], q["ask"], q["bid_size"], q["ask_size"], q["quote_time"]))(p.legs[0].quote))
        self.assertFalse(p.legs[0].listed)                      # materialised only at submit
        # COLLAR_7: + sell 10 C825 at the bid 6.00 x 1,000 = 6,000.00 -> cost 100.00; fees 13.00; net -113.00
        col = c["COLLAR_7"]
        self.assertEqual([(occ("2027-02-19", "P", 715), "BTO", 10, D("6.10")), (occ("2027-02-19", "C", 825), "STO", 10, D("6.00"))],
                         [(l.instrument_id, l.side, l.qty, l.est_price) for l in col.legs])
        self.assertEqual((D("100.00"), D("13.00"), D("-113.00"), D("-0.0706")), (col.cost, col.fees, col.net_cash, col.protection_level))
        # PUT_SPREAD: buy P715 6.10, sell P655 bid (655 - 640) x 0.08 = 1.20 -> cost 6,100 - 1,200 = 4,900.00; net -4,913.00
        ps = c["PUT_SPREAD"]
        self.assertEqual([("BTO", 715), ("STO", 655)], [(l.side, int(self.rt.instruments.get(l.instrument_id).strike) if self.rt.instruments.get(l.instrument_id) else int(l.instrument_id[-8:]) // 1000) for l in ps.legs])
        self.assertEqual((D("4900.00"), D("-4913.00")), (ps.cost, ps.net_cash))
        # the dict form the API serialises
        d = p.to_dict()
        self.assertEqual(("6100.00", "-0.0706", "PUT_7PCT", "order"), (d["cost"], d["protection_level"], d["id"], d["submit_via"]))
        json.dumps(d)

    def test_candidate_dict_matches_panel_contract(self):
        """The keys components/hedge.js (WP-13) reads: per-leg symbol / strike / expiry / fee / quote provenance, candidate ratio,
        fill_mode, protection text, rule and not_in_this_build; everything JSON-serialisable."""
        c = by_id(self.cands("BUY", 1000))
        d = c["PUT_7PCT"].to_dict()
        # ratio = 10 contracts / 1,000 shares = 0.01; protection floor 715 = 715 / 769.32 - 1 = -7.06 %; one leg fee 10 x 0.65 = 6.50
        self.assertEqual(("0.01", "SEQUENTIAL", "floor 715 (-7.06 %)", False, "6.50", "-6106.50"), (d["ratio"], d["fill_mode"], d["protection"], d["not_in_this_build"], d["fees"], d["net_cash"]))
        self.assertIn("K = 0.93 x 769.32 = 715.4676 -> nearest listed 715", d["rule"])
        self.assertIn("n = floor(1,000 / 100) = 10", d["rule"])
        leg = d["legs"][0]
        # est_price is the canonical price string (6.10 -> "6.1"); the quote keeps the chain's own "6.00" / "6.10"
        self.assertEqual((P715_FEB, P715_FEB, "715", "2027-02-19", "6.50", "ask", "6.1"), (leg["instrument_id"], leg["symbol"], leg["strike"], leg["expiry"], leg["fee"], leg["price_basis"], leg["est_price"]))
        q = leg["quote"]
        self.assertEqual(("put", "6.00", "6.10", self.now - 100, self.now - 30, 100, "chain", "cboe", "delayed", "Cboe delayed quotes"),
                         (q["kind"], q["bid"], q["ask"], q["quote_time"], q["fetched_at"], q["age_s"], q["method"], q["provider"], q["quality"], q["source"]))
        # option greeks per the fake chain: call delta = clip(0.5 + (769.32 - 715) x 0.01, 0.02, 0.98) = clip(1.0432) = 0.98 -> put delta 0.98 - 1 = -0.02;
        # delta_shares = +1 (a buy) x -0.02 x 100 x 10 = -20.00
        self.assertEqual("-20.00", d["greeks"]["delta_shares"])
        self.assertEqual(sorted(d["greeks"]), ["delta", "delta_shares", "gamma", "note", "theta", "vega"])
        json.dumps(d)
        # INDEX_OVERLAY: 2 ES -> ratio 2 / 1,000 = 0.002; leg IM 2 x 17,500.00 = 35,000.00; leg fee 2 x 2.25 = 4.50; ESZ26 last trades Fri 2026-12-18
        o = c["INDEX_OVERLAY"].to_dict()
        self.assertEqual(("0.002", "35000.00", "17500.00", "ESZ26", "2026-12-18", "4.50"), (o["ratio"], o["legs"][0]["margin"]["initial"], o["legs"][0]["margin"]["per_contract"],
                                                                                          o["legs"][0]["symbol"], o["legs"][0]["expiry"], o["legs"][0]["fee"]))
        self.assertIn("notional hedged 769,320.00 (769,320.00 x beta 1.0) with 2 ESZ26 = 625,000.00", o["protection"])     # 2 x 50 x 6,250 = 625,000.00
        self.assertEqual(("6250", "6250", "delayed"), (o["legs"][0]["quote"]["last"], o["legs"][0]["quote"]["value"], o["legs"][0]["quote"]["quality"]))
        json.dumps(o)

    def test_edits_override_strike_and_expiry(self):
        """The ticket's hedge.edits: the protective strike snaps to the listed grid (ties lower), the expiry must be listed."""
        c = by_id(self.cands("BUY", 1000, edits={"strike": "717.5"}))
        self.assertEqual((P715_FEB, True), (c["PUT_7PCT"].legs[0].instrument_id, "K = 717.5 (ticket) -> nearest listed 715" in c["PUT_7PCT"].rule))
        # strike 700 at the January expiry: P700 Jan bid (700 - 640) x 0.08 = 4.80, ask 4.90 -> cost 4.90 x 100 x 10 = 4,900.00; protection 700 / 769.32 - 1 = -0.0901
        c = by_id(self.cands("BUY", 1000, edits={"strike": "700", "expiry": "2027-01-15"}))
        p = c["PUT_7PCT"]
        self.assertEqual((occ("2027-01-15", "P", 700), "2027-01-15", D("4.90"), D("4900.00"), D("-0.0901")), (p.legs[0].instrument_id, p.expiry, p.legs[0].est_price, p.cost, p.protection_level))
        # the collar's call keeps its 1.07 rule at the edited expiry: C825 Jan bid (900 - 825) x 0.08 = 6.00 -> cost 4,900 - 6,000 = -1,100.00 (a net credit)
        col = c["COLLAR_7"]
        self.assertEqual(([(occ("2027-01-15", "P", 700), "BTO"), (occ("2027-01-15", "C", 825), "STO")], D("-1100.00")), ([(l.instrument_id, l.side) for l in col.legs], col.cost))
        # an unlisted expiry makes the option candidates unavailable with the reason; the overlay is untouched
        c = by_id(self.cands("BUY", 1000, edits={"expiry": "2027-03-19"}))
        self.assertEqual((False, True, True), (c["PUT_7PCT"].available, "not listed" in c["PUT_7PCT"].reason, c["INDEX_OVERLAY"].available))
        with self.assertRaises(RejectError) as cm:
            self.cands("BUY", 1000, edits={"strike": "abc"})
        self.assertEqual("EDIT_INVALID", cm.exception.code)

    def test_sizing_floor_and_small_positions(self):
        # 150 shares -> floor(150 / 100) = 1 contract; 99 shares -> no option candidate (disclosed), the overlay is unavailable too
        self.assertEqual(1, by_id(self.cands("BUY", 150))["PUT_7PCT"].legs[0].qty)
        c = by_id(self.cands("BUY", 99))
        self.assertEqual((False, True), (c["PUT_7PCT"].available, "fewer than 100 shares" in c["PUT_7PCT"].reason))
        self.assertFalse(c["INDEX_OVERLAY"].available)          # 99 x 769.32 / 312,500 = 0.24 -> 0 contracts

    def test_expiry_rule_90_days(self):
        today = date(2026, 10, 20)
        self.assertEqual(("2027-02-19", "nearest listed expiry at least 90 days out"), hedges.pick_expiry(chain(self.now), today))
        # only 31- and 87-day expiries listed: the farthest is used and disclosed
        short = chain(self.now, expiries=("2026-11-20", "2027-01-15"))
        e, note = hedges.pick_expiry(short, today)
        self.assertEqual(("2027-01-15", True), (e, "farthest listed" in note))
        self.assertEqual("2027-01-15", by_id(self.cands("BUY", 1000, chain_=short))["PUT_7PCT"].expiry)
        # 2027-01-22 listed (94 days) -> it wins over 2027-02-19
        self.assertEqual("2027-01-22", hedges.pick_expiry(chain(self.now, expiries=("2026-11-20", "2027-01-22", "2027-02-19")), today)[0])
        # strike snapping ties go to the lower strike: target 717.5 between 715 and 720 -> 715
        rows = chain(self.now).rows_for("2027-02-19")
        self.assertEqual((D(715), D(715), D(720)), (hedges.snap_strike(rows, D("717.5")), hedges.snap_strike(rows, D("715.4676")), hedges.snap_strike(rows, D("718"))))

    def test_index_overlay_rounding(self):
        # n = round(1,000 x 769.32 x beta / (6,250 x 50)): beta 1.0 -> 769,320 / 312,500 = 2.4618 -> 2 (SELL for a long primary)
        o = by_id(self.cands("BUY", 1000))["INDEX_OVERLAY"]
        self.assertEqual((True, [("ESZ26", "SELL", 2, D("6250"), "FUTURE")]), (o.available, [(l.instrument_id, l.side, l.qty, l.est_price, l.kind) for l in o.legs]))
        self.assertEqual((D("4.50"), D("-4.50")), (o.fees, o.net_cash))                # 2 x 2.25 futures commission, no premium
        self.assertIn("beta 1.0 (static, from the master)", o.note)
        # beta 1.2 -> 923,184 / 312,500 = 2.954 -> 3; a short primary buys the overlay
        set_beta(self.rt, "SPY", 1.2)
        self.spy = self.rt.instruments.get("SPY")
        o = by_id(self.cands("SELL_SHORT", 1000))["INDEX_OVERLAY"]
        self.assertEqual([("ESZ26", "BUY", 3)], [(l.instrument_id, l.side, l.qty) for l in o.legs])
        # no beta in the master -> 1.0 assumed and labelled
        set_beta(self.rt, "SPY", None)
        self.spy = self.rt.instruments.get("SPY")
        o = by_id(self.cands("BUY", 1000))["INDEX_OVERLAY"]
        self.assertEqual((2, True), (o.legs[0].qty, "1.0 assumed" in o.note))
        # no ES mark -> unavailable, nothing invented
        self.rt.marks.unset("ESZ26")
        self.assertEqual((False, "ESZ26 mark unavailable"), (lambda c: (c.available, c.reason))(by_id(self.cands("BUY", 1000))["INDEX_OVERLAY"]))

    def test_short_equity_calls(self):
        c = by_id(self.cands("SELL_SHORT", 1000))
        self.assertEqual(["CALL_7PCT", "CALL_SPREAD", "COLLAR_SHORT", "INDEX_OVERLAY"], sorted(c))
        # CALL_7PCT: buy 10 C825 at the ask 6.10 -> 6,100.00; protection 825 / 769.32 - 1 = +0.0724
        self.assertEqual(([(occ("2027-02-19", "C", 825), "BTO", 10, D("6.10"))], D("6100.00"), D("0.0724")),
                         ([(l.instrument_id, l.side, l.qty, l.est_price) for l in c["CALL_7PCT"].legs], c["CALL_7PCT"].cost, c["CALL_7PCT"].protection_level))
        # CALL_SPREAD: sell 10 C885 at the bid (900 - 885) x 0.08 = 1.20 -> cost 6,100 - 1,200 = 4,900.00
        self.assertEqual(([("BTO", 825), ("STO", 885)], D("4900.00")), ([(l.side, int(l.instrument_id[-8:]) // 1000) for l in c["CALL_SPREAD"].legs], c["CALL_SPREAD"].cost))
        # COLLAR_SHORT: buy C825 6.10, sell P715 at the bid 6.00 -> cost 100.00
        self.assertEqual(([("BTO", "C", 825), ("STO", "P", 715)], D("100.00")),
                         ([(l.side, parse_occ(l.instrument_id)[2], int(parse_occ(l.instrument_id)[3])) for l in c["COLLAR_SHORT"].legs], c["COLLAR_SHORT"].cost))

    def test_closing_sides_and_unquoted_legs(self):
        self.assertEqual([], self.cands("SELL", 1000))
        self.assertEqual([], self.cands("BUY_TO_COVER", 1000))
        # a leg without an NBBO (bid 0) makes the candidate unavailable with the reason; no model price fills in
        snap = chain(self.now)
        row = snap.by_occ(P715_FEB)
        row.bid, row.ask = D(0), D(0)
        c = by_id(self.cands("BUY", 1000, chain_=snap))
        self.assertEqual((False, True, None), (c["PUT_7PCT"].available, "no usable NBBO" in c["PUT_7PCT"].reason, c["PUT_7PCT"].legs[0].est_price))
        self.assertTrue(c["PUT_SPREAD"].available is False)      # shares the P715 leg

    def test_delta_hedge_rounding(self):
        c760 = sc.ensure_option(self.rt, C760)
        p740 = sc.ensure_option(self.rt, P740)
        # BTO 10 C760: delta 0.5 + (769.32 - 760) x 0.01 = 0.5932; shares = -(0.5932 x 10 x 100) = -593.2 -> -600 (nearest 100, half up)
        d = by_id(self.cands("BTO", 10, inst=c760))["DELTA_HEDGE"]
        self.assertEqual([("SPY", "SELL_SHORT", 600, True)], [(l.instrument_id, l.side, l.qty, l.needs_borrow) for l in d.legs])
        # cost = -600 x 769.32 = -461,592.00 (a sale); fee 600 x 0.005 = 3.00; net cash +461,589.00; ratio 600 / 10 = 60 shares per contract
        self.assertEqual((D("-461592.00"), D("3.00"), D("461589.00"), "-600", "-600", D(60)), (d.cost, d.fees, d.net_cash, d.greeks["delta"], d.greeks["delta_shares"], d.ratio))
        # with 1,000 shares held the hedge sells from the position (no borrow)
        buy(self.rt, US_EXCHANGE, "SPY", 1000, "769.32")
        d = by_id(self.cands("BTO", 10, inst=c760))["DELTA_HEDGE"]
        self.assertEqual(("SELL", False), (d.legs[0].side, d.legs[0].needs_borrow))
        # STO 10 P740: delta 0.7932 - 1 = -0.2068; position delta = -(-0.2068) x 10 x 100 = +206.8 -> hedge -206.8 -> -200 shares -> SELL 200
        d = by_id(self.cands("STO", 10, inst=p740))["DELTA_HEDGE"]
        self.assertEqual([("SPY", "SELL", 200)], [(l.instrument_id, l.side, l.qty) for l in d.legs])
        # a chain row without delta -> unavailable; closing option sides -> nothing
        snap = chain(self.now)
        snap.by_occ(C760).delta = None
        self.assertFalse(by_id(self.cands("BTO", 10, inst=c760, chain_=snap))["DELTA_HEDGE"].available)
        self.assertEqual([], self.cands("STC", 10, inst=c760))
        # rounding helper pins: 550 -> 600, -593.2 -> -600, 206.8 -> 200, 49 -> 0
        self.assertEqual((600, -600, 200, 0), tuple(hedges.round_to_100(D(x)) for x in ("550", "-593.2", "206.8", "49")))

    def test_futures_fx_bond_rows(self):
        es = self.rt.instruments.get("ESZ26")
        self.rt.marks.set("ESH27", "6260.00")
        c = by_id(self.cands("BUY", 2, inst=es))
        # CALENDAR: the opposite side in the next listed month ESH27 (Mar 2027), priced from its mark; options on futures NOT IN THIS BUILD
        self.assertEqual(([("ESH27", "SELL", 2, D("6260"))], True, D("-4.50")), ([(l.instrument_id, l.side, l.qty, l.est_price) for l in c["CALENDAR"].legs], c["CALENDAR"].available, c["CALENDAR"].net_cash))
        # the futures leg carries its static IM: 2 x 17,500.00 = 35,000.00 (FUT_MARGIN); options on futures are flagged not_in_this_build
        self.assertEqual(({"initial": "35000.00", "per_contract": "17500.00"}, D(1)), ({k: c["CALENDAR"].legs[0].margin[k] for k in ("initial", "per_contract")}, c["CALENDAR"].ratio))
        self.assertEqual((False, "NOT IN THIS BUILD", True), (c["OPTIONS_ON_FUTURES"].available, c["OPTIONS_ON_FUTURES"].reason, c["OPTIONS_ON_FUTURES"].not_in_this_build))
        # FX forward (Build 2, OA-09): without the FX desk bound the candidate says so - never a guessed forward
        eurusd = self.rt.instruments.get("EURUSD")
        fx = by_id(self.cands("BUY", 100000, inst=eurusd))
        self.assertEqual((False, "the FX desk is not loaded in this runtime: no forward price"), (fx["FX_FORWARD"].available, fx["FX_FORWARD"].reason))
        # with the desk (a stand-in for services.fx whose CIP outright is F = 1.1290 for the 3M value date 2027-01-22): a long EUR 100,000.00
        # is hedged by SELLING EUR 100,000.00 for USD 100,000 x 1.1290 = 112,900.00; a forward has no premium (cost 0, nothing moves today)
        vd = date(2027, 1, 22)

        def desk(F):
            return types.SimpleNamespace(fx_trade_date=lambda s, now: date(2026, 10, 20), tenor_date=lambda s, pair, tenor, td: vd,
                                         forward_rate=lambda s, pair, v: (D(F), {"spot": {"mid": "1.1238", "quote_time": self.now - 60}, "days": 90}))
        self.rt.fx = desk("1.1290")
        f = by_id(self.cands("BUY", 100000, inst=eurusd))["FX_FORWARD"]
        self.assertEqual((True, "fx", "SELL", 100000, D("1.129"), "2027-01-22", D(0), D(0)),
                         (f.available, f.submit_via, f.legs[0].side, f.legs[0].qty, f.legs[0].est_price, f.expiry, f.cost, f.net_cash))
        self.assertIn("sell EUR 100,000.00 / buy USD 112,900.00 at 1.129, value 2027-01-22", f.legs[0].label)
        self.assertEqual({"kind": "FX", "mode": "FORWARD", "tenor": "3M", "buy_ccy": "USD", "sell_ccy": "EUR", "amount": "100000.00", "amount_ccy": "EUR", "instrument_id": "EURUSD"},
                         f.to_dict()["prefill"])
        # a London listing's currency exposure: 100 SHEL-L at 2,700 GBp x 0.01 = GBP 2,700.00; at GBPUSD 1.2650 the forward sells it for
        # USD 2,700 x 1.2650 = 3,415.50; it is dealt on the desk of the lifetime's Foreign Markets & FX account
        self.rt.marks.set("SHEL-L", "2700")
        self.rt.fx = desk("1.2650")
        g = by_id(self.cands("BUY", 100, inst=self.rt.instruments.get("SHEL-L")))
        self.assertEqual(["FX_FORWARD"], sorted(g))
        g = g["FX_FORWARD"]
        self.assertEqual((2700, "2700.00", "GBP", "USD", f"{T1}:FOREIGN_FX"), (g.legs[0].qty, g.to_dict()["prefill"]["amount"], g.to_dict()["prefill"]["sell_ccy"],
                                                                                g.to_dict()["prefill"]["buy_ccy"], g.to_dict()["trade_account"]))
        self.assertIn("buy USD 3,415.50 at 1.265", g.legs[0].label)
        del self.rt.fx
        # DV01 futures (Build 2, OA-05): without the bond engine the candidate says so
        bond = next(i for i in self.rt.instruments.all() if i.kind == "BOND")
        b = by_id(self.cands("BUY", 1000, inst=bond))
        self.assertEqual((False, "the bond engine is not loaded in this runtime: no DV01"), (b["DV01_FUTURES"].available, b["DV01_FUTURES"].reason))
        # with a stand-in engine: DV01 of the face held -850.00 USD per bp (a long loses when yields rise); maturity 2036-04-20 = 3,470 days
        # / 365.25 = 9.50 years -> ZN (<= 15 y); contract DV01 = 6.5 x 112.50 x 1,000 / 10,000 = 73.125 -> 73.12 (cents, half even);
        # n = round(850.00 / 73.12) = round(11.6247) = 12 -> SELL 12 ZNZ26 (the front ZN), fees 12 x 2.25 = 27.00, no premium; in US Exchange
        self.rt.bonds = types.SimpleNamespace(dv01_c=lambda s, inst, face: (-85000 if face > 0 else 85000, {"basis": "full reprice"}),
                                              bond_terms=lambda inst: types.SimpleNamespace(maturity=date(2036, 4, 20)))
        self.rt.marks.set("ZNZ26", "112.50")
        d = by_id(self.cands("BUY", 1000000, inst=bond))["DV01_FUTURES"]
        self.assertEqual((True, [("ZNZ26", "SELL", 12)], D("27.00"), D("-27.00"), "73.12", f"{T1}:US_EXCHANGE"),
                         (d.available, [(l.instrument_id, l.side, l.qty) for l in d.legs], d.fees, d.net_cash, d.greeks["dv01_contract"], d.to_dict()["trade_account"]))
        self.assertIn("round(11.6247) = 12", d.rule)
        del self.rt.bonds

    def test_chain_quality_fetching_and_stale(self):
        # a chain fetched 1,000 s ago is stale (> 900 s) and still priced, labelled
        c = by_id(self.cands("BUY", 1000, chain_=chain(self.now, fetched_at=self.now - 1000)))["PUT_7PCT"]
        self.assertEqual(("stale", "stale"), (c.chain_quality, c.legs[0].quote["quality"]))
        # no chain argument: the cache answers fetching (202) and queues the fetch; a stale cached chain is used with a refresh queued
        self.rt.chains = ChainCache(clock=self.rt.clock)
        r = hedges.candidates(self.rt, US_EXCHANGE, self.spy, "BUY", 1000)
        self.assertEqual(("fetching", [], ["SPY"]), (r["status"], r["candidates"], self.rt.chains.pending()))
        self.rt.chains.put(chain(self.now, fetched_at=self.now - 1000))
        self.assertEqual("stale", by_id(hedges.candidates(self.rt, US_EXCHANGE, self.spy, "BUY", 1000))["PUT_7PCT"].chain_quality)
        self.rt.chains.put(chain(self.now))
        self.assertEqual("delayed", by_id(hedges.candidates(self.rt, US_EXCHANGE, self.spy, "BUY", 1000))["PUT_7PCT"].chain_quality)
        # without a chain source at all
        del self.rt.chains
        self.assertEqual("unavailable", hedges.candidates(self.rt, US_EXCHANGE, self.spy, "BUY", 1000)["status"])
        # the preview block of WP-02 carries the candidates with status ready
        self.rt.chains = ChainCache(clock=self.rt.clock)
        self.rt.chains.put(chain(self.now))
        fund(self.rt, US_EXCHANGE, 100000000)
        self.rt.marks.set_quote(sc.rec("SPY", "769.32", self.now - 60, self.now))
        pv = sc.orders.preview(self.rt, {"account_id": US_EXCHANGE, "instrument_id": "SPY", "side": "BUY", "qty": 1000, "order_type": "MARKET"})
        self.assertEqual(("ready", ["PUT_7PCT", "COLLAR_7", "PUT_SPREAD", "INDEX_OVERLAY"]), (pv["hedges"]["status"], [c["id"] for c in pv["hedges"]["candidates"]]))

    def test_trs_overlay_only_in_eligible_pb_account(self):
        rt = self.rt
        with rt.db.writer() as tx:
            books._insert_account(tx.conn, rt.clock.epoch(), "T", T1, "PB_GS", "Goldman Sachs PB", "PB", 60, broker="GS")
        rt.swaps = types.SimpleNamespace(preview=lambda *a, **k: {})
        rt.swaps_trs = types.SimpleNamespace(preview=lambda *a, **k: {})                 # WP-26: swaps.preview(.., "TRS", ..) delegates there
        # a PB account whose lifetime has no relationships and NAV 10,000,000 < the 25,000,000 threshold -> not eligible -> no row
        self.assertNotIn("TRS_OVERLAY", by_id(self.cands("BUY", 1000, account=pb("GS"))))
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO agreements(lifetime_id, broker, kind, signed_date, command_id, note) VALUES (?,?,?,?,1,NULL)", (T1, "GS", "PBA", "2026-10-20"))
        c = by_id(self.cands("BUY", 1000, account=pb("GS")))
        self.assertIn("TRS_OVERLAY", c)
        t = c["TRS_OVERLAY"]
        self.assertEqual(("otc", [("PAY", 1000, "TRS", SPOT)], True), (t.submit_via, [(l.side, l.qty, l.kind, l.est_price) for l in t.legs], t.available))
        self.assertEqual("RECEIVE", by_id(self.cands("SELL_SHORT", 1000, account=pb("GS")))["TRS_OVERLAY"].legs[0].side)
        # never in the exchange account, never without the swaps modules (WP-17's gate, WP-26's TRS)
        self.assertNotIn("TRS_OVERLAY", by_id(self.cands("BUY", 1000)))
        saved_trs = rt.swaps_trs
        rt.swaps_trs = None
        self.assertNotIn("TRS_OVERLAY", by_id(self.cands("BUY", 1000, account=pb("GS"))))
        rt.swaps_trs = saved_trs
        del rt.swaps
        self.assertNotIn("TRS_OVERLAY", by_id(self.cands("BUY", 1000, account=pb("GS"))))
        # choosing it on the ticket is refused: it is booked on the OTC desk
        rt.swaps = types.SimpleNamespace(preview=lambda *a, **k: {})
        rt.chains = ChainCache(clock=rt.clock)
        rt.chains.put(chain(self.now))
        fund(rt, pb("GS"), 100000000)
        rt.marks.set_quote(sc.rec("SPY", "769.32", self.now - 60, self.now))
        r = sc.submit(rt, account_id=pb("GS"), instrument_id="SPY", side="BUY", qty=1000, order_type="LIMIT", limit_px="700.00", hedge={"candidate_id": "TRS_OVERLAY"})
        self.assertEqual((400, "NOT_AVAILABLE"), (r.http, r.code))
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM orders")["n"])


class PositionHedgeTests(unittest.TestCase):
    """USX-12 / ORC-4: the hedges of a position ALREADY HELD (no primary order): sized to the quantity held."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-hpos-")
        self.rt = sc.runtime(self.tmp)
        options.register(self.rt.bus)
        self.rt.options = options
        self.rt.hedges = hedges
        self.now = self.rt.clock.epoch()
        self.spy = self.rt.instruments.get("SPY")
        set_beta(self.rt, "SPY", 1.0)
        self.rt.marks.set("ESZ26", "6250.00")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_position_mode_sizes_to_the_holding(self):
        rt = self.rt
        # flat: nothing to hedge, said
        held, res = hedges.position_candidates(rt, US_EXCHANGE, self.spy, chain=chain(self.now))
        self.assertEqual((0, "flat"), (held, res["status"]))
        # 1,250 SPY held -> n = floor(1,250 / 100) = 12 contracts: the protective put is its own order (one leg), the collar one AON package;
        # the put leg P715 Feb at the ask 6.10: 12 x 6.10 x 100 = 7,320.00 + fees 12 x 0.65 = 7.80 -> net cash -7,327.80
        buy(rt, US_EXCHANGE, "SPY", 1250, "769.32")
        held, res = hedges.position_candidates(rt, US_EXCHANGE, self.spy, chain=chain(self.now))
        c = by_id(res)
        self.assertEqual(1250, held)
        p = c["PUT_7PCT"]
        self.assertEqual(("position", "order", None, [(P715_FEB, "BTO", 12)], D("7320.00"), D("7.80"), D("-7327.80")),
                         (p.mode, p.submit_via, p.fill_mode, [(l.instrument_id, l.side, l.qty) for l in p.legs], p.cost, p.fees, p.net_cash))
        self.assertEqual(("package", "AON"), (c["COLLAR_7"].submit_via, c["COLLAR_7"].fill_mode))
        # an option hedge of shares is always sized 1 contract per 100 shares (floor): 12 for 1,250 held
        self.assertEqual("1 contract per 100 shares held", p.ratio_text)
        self.assertEqual("1 contract per 100 shares", hedges.ratio_text(D("0.01"), "OPTION", self.spy))
        # futures per share: 2 / 1,000 = 0.002 -> 1 per 500; 3 / 1,250 = 0.0024 -> 1 per 416.67 -> 'about 417'
        self.assertEqual("1 contract per 500 shares", hedges.ratio_text(D("0.002"), "FUTURE", self.spy))
        self.assertEqual("1 contract per about 417 shares held", hedges.ratio_text(D("0.0024"), "FUTURE", self.spy, held=True))
        self.assertEqual("60 shares per contract", hedges.ratio_text(D(60), "EQUITY", rt.instruments.get("ESZ26")))
        # INDEX_OVERLAY: round(1,250 x 769.32 / 312,500) = round(3.0773) = 3 ES sold: its own order
        self.assertEqual(("order", [("ESZ26", "SELL", 3)]), (c["INDEX_OVERLAY"].submit_via, [(l.instrument_id, l.side, l.qty) for l in c["INDEX_OVERLAY"].legs]))
        # 99 shares: the option candidates and the overlay size to nothing (zero_size: the panel hides them and says why)
        rt2 = self.rt
        payload = [x.to_dict() for x in hedges.candidates(rt2, US_EXCHANGE, self.spy, "BUY", 99, chain=chain(self.now))]
        self.assertEqual({"PUT_7PCT": True, "COLLAR_7": True, "PUT_SPREAD": True, "INDEX_OVERLAY": True}, {x["id"]: x["zero_size"] for x in payload})
        json.dumps(payload)


class HedgePackageTests(unittest.TestCase):
    """§10.9 / §10.7 step 8 with WP-02: the real ``hedges.package`` creates the children and they resize on partials."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-hpk-")
        self.rt = sc.runtime(self.tmp)
        options.register(self.rt.bus)
        self.rt.options = options
        self.rt.hedges = hedges
        self.rt.chains = ChainCache(clock=self.rt.clock)
        self.now = self.rt.clock.epoch()
        self.rt.chains.put(chain(self.now))
        fund(self.rt, US_EXCHANGE, 100000000)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_package_resizes_on_60pct_partial(self):
        rt = self.rt
        t1, t2 = self.now - 180, self.now - 120
        rt.marks.set_quote(sc.rec("SPY", "769.32", self.now - 90, self.now, bar=(t1, 6000)))       # cap 10 % x 6,000 = 600 of 1,000 (60 %)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=1000, order_type="MARKET", hedge={"candidate_id": "PUT_7PCT"})
        self.assertTrue(r.ok, r.body)
        oid, pid = r.body["order"]["order_id"], r.body["hedge_package"]
        self.assertEqual(("PARTIAL", 600), (r.body["order"]["status"], r.body["order"]["filled_qty"]))
        child = sc.one(rt, "SELECT * FROM orders WHERE parent_order_id=?", (oid,))
        # the candidate's leg is 10 P715 (Feb 2027), materialised from the chain; ratio 10 / 1,000 = 0.01; after the 60 % partial: floor(600 x 0.01) = 6 WORKING
        self.assertEqual((P715_FEB, "BTO", "0.01", 6, "WORKING", pid, "MARKET"), (child["instrument_id"], child["side"], child["ratio"], child["qty"], child["status"], child["package_id"], child["order_type"]))
        inst = rt.instruments.get(P715_FEB)
        self.assertEqual(("OPTION", D(715), "P", "2027-02-19", "SPY"), (inst.kind, inst.strike, inst.right, inst.expiry, inst.underlying_id))
        self.assertEqual(("SEQUENTIAL", oid, "PUT_7PCT"), (lambda p: (p["fill_mode"], p["primary_order_id"], p["candidate_id"]))(sc.one(rt, "SELECT * FROM hedge_packages WHERE package_id=?", (pid,))))
        # the rest fills on the next bar (v 4,000 -> cap 400): the child becomes floor(1,000 x 0.01) = 10
        cache = sc.FakeCache({"SPY": sc.rec("SPY", "769.40", self.now - 20, self.now, bar=(t2, 4000))})
        self.assertEqual(1, sc.matcher_for(rt, cache).on_quotes(["SPY"]))
        self.assertEqual("FILLED", sc.order_row(rt, oid)["status"])
        self.assertEqual((10, "WORKING"), (lambda c: (c["qty"], c["status"]))(sc.one(rt, "SELECT * FROM orders WHERE parent_order_id=?", (oid,))))
        # the child then fills on its own chain observation at the ask 6.10: 10 x 6.10 x 100 = 6,100.00, fee 6.50
        cache[P715_FEB] = sc.chain_rec(P715_FEB, "6.00", "6.10", 50, 50, self.now - 10, self.now)
        self.assertEqual(1, sc.matcher_for(rt, cache).on_quotes([P715_FEB]))
        f = sc.one(rt, "SELECT * FROM fills WHERE instrument_id=?", (P715_FEB,))
        self.assertEqual((10, "6.1", 610000, 650), (f["qty"], f["price"], f["gross_c"], f["fee_c"]))
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1310"))                                              # a long put needs no margin
        rt.marks.set(P715_FEB, "6.05")                     # the mark provider prices a held option at the Cboe mid; the full check needs every position priced
        assert_integrity(self, rt, "full")

    def test_ratio_override_and_explicit_legs(self):
        rt = self.rt
        rt.marks.set_quote(sc.rec("SPY", "769.32", self.now - 90, self.now))
        # ratio 0.02 -> each leg floor(1,000 x 0.02) = 20 contracts (the candidate's own sizing of 10 is overridden)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=1000, order_type="LIMIT", limit_px="700.00", hedge={"candidate_id": "COLLAR_7", "ratio": "0.02"})
        self.assertTrue(r.ok, r.body)
        kids = sc.q(rt, "SELECT instrument_id, side, qty, ratio, status FROM orders WHERE parent_order_id=? ORDER BY order_id", (r.body["order"]["order_id"],))
        self.assertEqual([(P715_FEB, "BTO", 20, "0.02", "WAITING_PARENT"), (occ("2027-02-19", "C", 825), "STO", 20, "0.02", "WAITING_PARENT")],
                         [(k["instrument_id"], k["side"], k["qty"], k["ratio"], k["status"]) for k in kids])
        # explicit legs (the owner edited the strike): materialised from the chain and attached as given
        legs = [{"instrument_id": occ("2027-02-19", "P", 700), "side": "BTO", "qty": 5}]
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=500, order_type="LIMIT", limit_px="700.00", hedge={"candidate_id": "PUT_7PCT", "legs": legs})
        self.assertTrue(r.ok, r.body)
        k = sc.one(rt, "SELECT instrument_id, qty, ratio FROM orders WHERE parent_order_id=?", (r.body["order"]["order_id"],))
        self.assertEqual((occ("2027-02-19", "P", 700), 5, "0.01"), (k["instrument_id"], k["qty"], k["ratio"]))
        # an unknown candidate and an unlisted contract are refused, nothing invented
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=500, order_type="LIMIT", limit_px="700.00", hedge={"candidate_id": "NOPE"})
        self.assertEqual((400, "NOT_AVAILABLE"), (r.http, r.code))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=500, order_type="LIMIT", limit_px="700.00",
                      hedge={"candidate_id": "PUT_7PCT", "legs": [{"instrument_id": "SPY270219P00999000", "side": "BTO", "qty": 5}]})
        self.assertEqual((400, "NOT_LISTED"), (r.http, r.code))
        self.assertEqual(2, sc.one(rt, "SELECT COUNT(*) n FROM orders WHERE parent_order_id IS NULL")["n"])
        assert_integrity(self, rt, "full")

    def test_ticket_edits_move_the_protective_leg(self):
        """components/hedge.js submits ``hedge: {candidate_id, ratio, edits: {strike, expiry}}``; package() reads the edits
        from the command payload (WP-02's _attach_hedge forwards only candidate_id / legs / ratio)."""
        rt = self.rt
        rt.marks.set_quote(sc.rec("SPY", "769.32", self.now - 90, self.now))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=1000, order_type="LIMIT", limit_px="700.00",
                      hedge={"candidate_id": "PUT_7PCT", "ratio": None, "edits": {"strike": "700", "expiry": "2027-01-15"}})
        self.assertTrue(r.ok, r.body)
        # the protective leg is P700 Jan-2027 (materialised from the chain), 10 contracts, ratio 10 / 1,000 = 0.01, waiting for the resting primary
        k = sc.one(rt, "SELECT instrument_id, side, qty, ratio, status FROM orders WHERE parent_order_id=?", (r.body["order"]["order_id"],))
        self.assertEqual((occ("2027-01-15", "P", 700), "BTO", 10, "0.01", "WAITING_PARENT"), (k["instrument_id"], k["side"], k["qty"], k["ratio"], k["status"]))
        inst = rt.instruments.get(occ("2027-01-15", "P", 700))
        self.assertEqual((D(700), "P", "2027-01-15"), (inst.strike, inst.right, inst.expiry))
        # an unlisted expiry in the edits refuses the package (nothing is invented) and rolls the primary back with it
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=1000, order_type="LIMIT", limit_px="700.00",
                      hedge={"candidate_id": "PUT_7PCT", "edits": {"expiry": "2027-03-19"}})
        self.assertEqual((400, "NOT_AVAILABLE"), (r.http, r.code))
        self.assertEqual(1, sc.one(rt, "SELECT COUNT(*) n FROM orders WHERE parent_order_id IS NULL")["n"])
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
