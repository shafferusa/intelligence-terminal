"""WP-09 risk and compliance tests (TERMINAL_DESIGN.md §14, §12.5, §19.24 D10 / D11).  Every expected number is worked by hand in a
comment from the inputs; nothing here is pasted from the code's output.  No network, no ~/.finsim (make_runtime points FINSIM_HOME at a
temp folder).  Risk and compliance only inform: no call here can block anything, and the preview hook is exercised through
``orders.preview`` with the modules bound as ``services.risk`` / ``services.compliance``.

Positions are seeded through a test-only ``test.open_position`` command (registered on the bus by this module) that posts the §5.4
lines for equities, options, futures, private shares and equity shorts with their fill, lot, settlement, borrow and margin rows, so
``assert_integrity`` holds after every step (L7–L10, L12–L16, L24).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from decimal import Decimal

from tests.terminal_helpers import (T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, execute, fund, make_runtime, post_entry, sell, set_mark)
from finsim.terminal import books, compliance, config, orders, risk
from finsim.terminal.db import next_id
from finsim.terminal.ledger import Entry, cr, dr, open_lot
from finsim.terminal.quotes import MarkView, QuoteRecord
from finsim.terminal.quotes.parsers import ChainRow, ChainSnapshot, ChainUnderlying
from finsim.terminal.units import to_c

D = Decimal
YAHOO = "Yahoo Finance (up to 15 min delayed)"
GL_LONG = {"EQUITY": "1100", "ETF": "1100", "ADR": "1100", "REIT": "1100", "PREFERRED": "1100", "PRIVATE": "1120", "OPTION": "1700"}
GL_SHORT = {"EQUITY": "2600", "ETF": "2600", "ADR": "2600", "REIT": "2600", "PREFERRED": "2600", "OPTION": "2900"}


# ---- test-only seeding ---------------------------------------------------------------------------------------------------------
def _h_open_position(ctx, payload):
    """``{"account_id", "instrument_id", "side": LONG|SHORT, "qty", "price", "fee_c", "lock_until"?, "margin_c"?}`` -> a fill, its entry, a lot and
    (cash products) a settlement row; futures post fee + IM only (lot cost 0); equity shorts add a borrow with 102 % collateral; short
    options add margin_state + 1310.  Every row matches the integrity L-set."""
    aid, iid, side = payload["account_id"], payload["instrument_id"], payload["side"]
    qty, px, fee = int(payload["qty"]), D(str(payload["price"])), int(payload.get("fee_c") or 0)
    inst = ctx.services.instruments.get(iid, ctx.conn)
    gross = inst.contract_value_c(px, qty)
    fill_id = next_id(ctx.conn, "fill")
    trade_date = ctx.clock.today()
    ccy = inst.currency
    lines, settle, settle_amt, settle_gl, lot_cost = [], None, 0, None, gross
    if inst.kind == "FUTURE":
        im = int((inst.spec or {}).get("im_c") or 0) * qty
        if fee:
            lines.append(dr(aid, "5000", ccy, fee, instrument_id=iid))
        lines += [dr(aid, "1300", ccy, im, instrument_id=iid), cr(aid, "1010", ccy, im + fee)]
        lot_cost = 0
    elif side == "LONG":
        settle = ctx.cal.settlement_date(trade_date, inst.settle_days)
        lines.append(dr(aid, GL_LONG[inst.kind], ccy, gross, instrument_id=iid))
        if fee:
            lines.append(dr(aid, "5000", ccy, fee, instrument_id=iid))
        lines.append(cr(aid, "2100", ccy, gross + fee))
        settle_amt, settle_gl = -(gross + fee), "2100"
    else:
        settle = ctx.cal.settlement_date(trade_date, inst.settle_days)
        lines.append(dr(aid, "1200", ccy, gross - fee))
        if fee:
            lines.append(dr(aid, "5000", ccy, fee, instrument_id=iid))
        lines.append(cr(aid, GL_SHORT[inst.kind], ccy, gross, instrument_id=iid))
        settle_amt, settle_gl = gross - fee, "1200"
        if inst.kind == "OPTION":
            margin = int(payload.get("margin_c") or 0)
            if margin:
                lines += [dr(aid, "1310", ccy, margin), cr(aid, "1010", ccy, margin)]
                ctx.conn.execute("INSERT OR REPLACE INTO margin_state(account_id, as_of_ts, options_required_c, options_detail_json, note) VALUES (?,?,?,?,?)",
                                 (aid, ctx.ts, margin, "{}", "test"))
        else:
            coll = to_c(D(gross) / D(100) * config.COLLATERAL_PCT)
            lines += [dr(aid, "1400", ccy, coll, instrument_id=iid), cr(aid, "1010", ccy, coll)]
            bid = next_id(ctx.conn, "borrow")
            ctx.conn.execute("INSERT INTO borrows(borrow_id, command_id, account_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, lender, collateral_c, currency,"
                             " opened_ts, opened_date, status) VALUES (?,?,?,?,?,0,'0.003','0.03','GC','test',?,?,?,?,'OPEN')",
                             (bid, ctx.command_id, aid, iid, qty, coll, ccy, ctx.ts, trade_date.isoformat()))
    entry_id = ctx.post(Entry("FILL", f"{side} {qty:,} {iid} @ {px} (test seed)", lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
    origin = "NEGOTIATED" if inst.kind == "PRIVATE" else "ORDER"
    ctx.conn.execute("INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date, origin,"
                     " price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fill_id, None, aid, iid, "BUY" if side == "LONG" else "SELL", qty, str(px), gross, fee, ccy, "1", ctx.ts, trade_date.isoformat(),
                      settle.isoformat() if settle else None, origin, "test", "{}", None, None, None, entry_id, f"test:{fill_id}"))
    lot_id = open_lot(ctx.tx, aid, iid, side, fill_id, qty, px, lot_cost, ccy, lock_until=payload.get("lock_until"))
    if settle is not None:
        sid = next_id(ctx.conn, "settlement")
        ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                         (sid, fill_id, None, aid, "CASH", ccy, settle_amt, settle_gl, settle.isoformat()))
    ctx.touched_instruments.add((aid, iid))
    ctx.event("ORDER_FILLED", account_id=aid, instrument_id=iid, ref_type="fill", ref_id=fill_id, qty=qty if side == "LONG" else -qty, price=str(px))
    return {"fill_id": fill_id, "lot_id": lot_id, "gross_c": gross, "fee_c": fee}


def open_position(rt, account, iid, side, qty, price, fee_c=0, **extra):
    r = execute(rt, "test.open_position", dict({"account_id": account, "instrument_id": iid, "side": side, "qty": int(qty), "price": str(price), "fee_c": int(fee_c)}, **extra))
    assert r.ok, r.body
    return r.body


def runtime(tmp, **kw):
    rt = make_runtime(tmp, **kw)
    rt.bus.register("test.open_position", _h_open_position)
    return rt


def add_account(rt, lid, role, name, kind, sort, broker=None, financing=False):
    with rt.db.writer() as tx:
        aid = books._insert_account(tx.conn, tx.ts, lid[0], lid, role, name, kind, sort, broker=broker)
        if financing:
            tx.x("UPDATE accounts SET pb_financing=1 WHERE account_id=?", (aid,))
    return aid


def add_instrument(rt, iid, kind, name, group, kinds, settle_days=1, **cols):
    with rt.db.writer() as tx:
        tx.x("INSERT INTO instruments(instrument_id, kind, symbol, name, currency, multiplier, tick, lot, price_scale, session_group, settle_days, account_kinds, yahoo_symbol,"
             " yahoo_invert, cboe_symbol, underlying_id, expiry, strike, right, style, exercise, spec_json, support, status, created_ts, updated_ts)"
             " VALUES (?,?,?,?,'USD','1','0.01',1,'1',?,?,?,NULL,0,NULL,NULL,?,NULL,NULL,NULL,NULL,'{}','COMPUTED','ACTIVE',?,?)",
             (iid, kind, iid, name, group, settle_days, json.dumps(kinds), cols.get("expiry"), tx.ts, tx.ts))
    return rt.instruments.refresh(iid)


def ensure_option(rt, occ, underlying):
    with rt.db.writer() as tx:
        u = rt.instruments.get(underlying, tx.conn)
        rt.instruments.ensure_option(tx, occ, u, {"option": occ})
    return rt.instruments.refresh(occ)


def book_swap(rt, aid, cid, notional_c, mark_c, vm_balance_c, im_c, pay_fixed=True):
    """An OPEN CTM IRS row with its instrument and ONE entry Dr 1330 IM / Dr 1320 |VM| / Cr 1010 (L22 ties 1320 = max(0, -vm), 1330 = im)."""
    add_instrument(rt, cid, "SWAP", "IRS 10y pay fixed", "US_BONDS", ["PB"], 0, expiry="2036-10-20")
    with rt.db.writer() as tx:
        cmd = tx.one("SELECT MAX(id) FROM commands")[0]
        tx.x("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c, status,"
             " last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,NULL,?,?,?,NULL,NULL)",
             (cid, cmd, aid, "GS", "IRS", json.dumps({"pay_fixed": pay_fixed, "tenor_months": 120, "fixed_rate": "0.0410"}), notional_c, "USD", "2026-10-20", "2036-10-20", "CTM", im_c,
              "2026-10-20", mark_c, tx.ts, vm_balance_c))
    lines = [[aid, "1330", "USD", im_c, cid], [aid, "1320", "USD", -vm_balance_c, cid], [aid, "1010", "USD", -(im_c - vm_balance_c)]]
    r = post_entry(rt, lines, f"otc:margin:{cid}", kind="OTC_MARGIN", memo="IM and VM posted (test seed)")
    assert r.ok, r.body


def book_trs(rt, aid, cid, underlying, units, notional_c, im_c, receiver=True):
    """An OPEN CTM TRS row (mark 0, VM 0) on ``underlying`` with its SWAP instrument (spec otc_kind TRS, the underlying) and ONE entry
    Dr 1330 IM / Cr 1010 (L22: 1330 = im, no VM yet)."""
    with rt.db.writer() as tx:
        tx.x("INSERT INTO instruments(instrument_id, kind, symbol, name, currency, multiplier, tick, lot, price_scale, session_group, settle_days, account_kinds, yahoo_symbol,"
             " yahoo_invert, cboe_symbol, underlying_id, expiry, strike, right, style, exercise, spec_json, support, status, created_ts, updated_ts)"
             " VALUES (?,?,?,?,'USD','1','0.01',1,'1','US_BONDS',0,?,NULL,0,NULL,?,'2027-10-20',NULL,NULL,NULL,NULL,?,'COMPUTED','ACTIVE',?,?)",
             (cid, "SWAP", cid, f"TRS receive {units:,} {underlying}", json.dumps(["PB"]), underlying,
              json.dumps({"otc_kind": "TRS", "underlying_id": underlying, "receiver": receiver, "units": units}), tx.ts, tx.ts))
        cmd = tx.one("SELECT MAX(id) FROM commands")[0]
        tx.x("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c, status,"
             " last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,'TRS',?,?,'USD','2026-10-20','2027-10-20','CTM',?,'OPEN',"
             "'2026-10-20','339.50',0,?,0,NULL,NULL)", (cid, cmd, aid, "GS", json.dumps({"receiver": receiver, "units": units, "underlying_id": underlying}), notional_c, im_c, tx.ts))
    rt.instruments.refresh(cid)
    r = post_entry(rt, [[aid, "1330", "USD", im_c, cid], [aid, "1010", "USD", -im_c]], f"otc:margin:{cid}", kind="OTC_MARGIN", memo="TRS IM (test seed)")
    assert r.ok, r.body


class ModelMarks:
    """Wraps the FixedMarks provider: the marks of ``inputs_by_id`` come back as option-model marks (method ``model``) carrying the
    §8.6 inputs, so the risk scenarios can re-price them through ``marks.price_option``; everything else is delegated."""

    def __init__(self, fixed, inputs_by_id):
        self._fixed, self._inputs = fixed, inputs_by_id

    def __getattr__(self, name):
        return getattr(self._fixed, name)

    def snapshot(self, ids=None):
        return _ModelSnap(self._fixed.snapshot(ids), self._inputs)


class _ModelSnap:
    def __init__(self, snap, inputs_by_id):
        self._snap, self._inputs = snap, inputs_by_id

    def mark(self, iid):
        v = self._snap.mark(iid)
        inp = self._inputs.get(iid)
        if inp is None or v.mark is None:
            return v
        return MarkView(iid, v.mark, "computed", "option model", "model", v.quote_time, v.fetched_at, "model", dict(inp), False, "test model mark")

    def fx(self, ccy):
        return self._snap.fx(ccy)

    def quote(self, iid):
        return self._snap.quote(iid)

    def as_of(self):
        return self._snap.as_of()


def quote(iid, last, prev_close, day_low, now, group="US_EQUITY"):
    return QuoteRecord(instrument_id=iid, provider="yahoo", source=YAHOO, method="direct", last=D(str(last)), prev_close=D(str(prev_close)), day_low=D(str(day_low)),
                       quote_time=now - 60, fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group=group, quality="delayed")


def by_name(rows, name):
    return next(r for r in rows if r["name"] == name)


def notices(ns, code):
    return [n for n in ns if n.code == code]


# ---- risk ------------------------------------------------------------------------------------------------------------------------
class RiskReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-risk-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exposures_tie_to_positions(self):
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        # 1,000 AAPL @ 339.50 (fee 1,000 x 0.005 = 5.00) -> MV 339,500.00; 500 SPY @ 769.32 (fee 2.50) -> MV 500 x 769.32 = 384,660.00
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)
        self.assertTrue(buy(rt, US_EXCHANGE, "SPY", 500, "769.32").ok)
        v = risk.report(rt, "ALL")
        # NAV = 10,000,000 - 5.00 - 2.50 = 9,999,992.50; long = 339,500 + 384,660 = 724,160.00; no shorts
        self.assertEqual((999999250, 72416000, 0, 72416000, 72416000), (v.nav_c, v.long_c, v.short_c, v.gross_c, v.net_c))
        self.assertEqual((0, 0, False, []), (v.dv01_c, v.dv01_signed_c, v.partial, v.missing))
        pos = rt.accounting.positions("ALL")
        self.assertEqual(sum(p.mv_base_c for p in pos), v.net_c)                      # every figure ties to accounting.positions
        self.assertEqual(sum(r.exposure_c for r in v.positions), v.net_c)
        self.assertEqual({"AAPL", "SPY"}, {r.instrument_id for r in v.positions})
        # one bucket: 724,160 / 9,999,992.50 = 0.0724160... -> 0.0724
        self.assertEqual([("US equities", 72416000, 0, 72416000, 72416000, D("0.0724"))], [(e["bucket"], e["long_c"], e["short_c"], e["net_c"], e["gross_c"], e["pct_nav"]) for e in v.exposures])
        # by class with its % of NAV: 724,160 / 9,999,992.50 = 0.0724
        self.assertEqual({"securities": {"long_c": 72416000, "short_c": 0, "net_c": 72416000, "gross_c": 72416000, "n": 2, "pct_nav": D("0.0724")}}, v.exposure_by_class)
        # per account: the US Exchange account's NAV is 1,000,000 - 7.50 = 999,992.50; its % of NAV is against the lifetime's NAV (0.0724)
        a = v.exposure_by_account[US_EXCHANGE]
        self.assertEqual((72416000, 0, 72416000, 99999250, D("0.0724")), (a["gross_c"], a["short_c"], a["net_c"], a["nav_c"], a["pct_nav"]))
        self.assertEqual(D("0.0724"), v.leverage)
        # concentration: SPY 384,660 / 9,999,992.50 = 0.038466 -> 0.0385; AAPL 339,500 / 9,999,992.50 = 0.033950 -> 0.0340
        self.assertEqual([("SPY", 38466000, D("0.0385")), ("AAPL", 33950000, D("0.0340"))], [(c["symbol"], c["mv_c"], c["pct_nav"]) for c in v.concentration])
        # scenarios: equities -10 % = -72,416.00; +10 % = +72,416.00; -20 % = -144,832.00; +5 % = +36,208.00; rates / vol / FX untouched
        self.assertEqual((-7241600, 7241600, -14483200, 3620800), (by_name(v.scenarios, "equities -10 %")["pnl_c"], by_name(v.scenarios, "equities +10 %")["pnl_c"],
                                                                 by_name(v.scenarios, "equities -20 %")["pnl_c"], by_name(v.scenarios, "equities +5 %")["pnl_c"]))
        self.assertEqual(([], [], {"securities": -7241600}), (by_name(v.scenarios, "equities -10 %")["unpriced"], by_name(v.scenarios, "equities -10 %")["unchanged"],
                                                            by_name(v.scenarios, "equities -10 %")["detail"]["by_class"]))
        self.assertEqual((0, 0, 0, 0), (by_name(v.scenarios, "rates +100 bp")["pnl_c"], by_name(v.scenarios, "vol +5 pts")["pnl_c"],
                                        by_name(v.scenarios, "USD +5 % vs foreign currencies")["pnl_c"], by_name(v.scenarios, "USD -5 % vs foreign currencies")["pnl_c"]))
        self.assertEqual(16, len(v.scenarios))
        # liquidity: available(US_EXCHANGE) = 1,000,000 - 339,505 - 384,662.50 = 275,832.50; Treasury 9,000,000 -> excess 9,275,832.50; nothing encumbered
        self.assertEqual((927583250, 0, 999999250), (v.excess_liquidity_c, v.encumbrance_c, v.financing_liquidity["margin_excess_c"]))
        self.assertEqual(([], [], [], []), (v.margin, v.otc, v.warnings, v.fx_exposure))
        self.assertEqual((0, D("0.0000"), [], []), (v.private["mv_c"], v.private["pct_nav"], v.private["positions"], v.private["lockups"]))
        self.assertEqual((0, D(0), 0, 0, 0, []), (v.greeks["delta_c"], v.greeks["delta_shares"], v.greeks["gamma_c"], v.greeks["vega_c"], v.greeks["theta_c"], v.greeks["unavailable"]))
        d = json.loads(json.dumps(v.to_dict()))                                   # the API shape: decimal strings, no floats
        self.assertEqual(("724160.00", "724160.00", "0.00", "724160.00", "0.00", "9275832.50", "0.00"), (d["gross"], d["long"], d["short"], d["net"], d["dv01"], d["excess_liquidity"], d["encumbrance"]))
        self.assertEqual(("US equities", "724160.00", "0.0724"), (d["exposures"][0]["bucket"], d["exposures"][0]["long"], d["exposures"][0]["pct_nav"]))
        self.assertEqual(("-72416.00", "384660.00", "0.0385", "0.00"), (by_name(d["scenarios"], "equities -10 %")["pnl"], d["concentration"][0]["mv"], d["concentration"][0]["pct_nav"], d["greeks"]["delta"]))
        self.assertEqual(risk.NOTE, d["note"])
        # the preview hook: 1,000 more AAPL (gross 339,500) -> 679,000 / 9,999,992.50 = 0.0679 (INFO); 8,000 more (gross 2,716,000) -> 3,055,500 / 9,999,992.50 = 0.3056 (WARN)
        aapl = rt.instruments.get("AAPL")
        w = risk.pretrade(rt, US_EXCHANGE, aapl, "BUY", 1000, {"economics": {"gross": "339500.00"}})
        self.assertEqual([("CONCENTRATION", "INFO", "0.0679")], [(x["code"], x["severity"], x["value"]) for x in w])
        w = risk.pretrade(rt, US_EXCHANGE, aapl, "BUY", 8000, {"economics": {"gross": "2716000.00"}})
        self.assertEqual([("CONCENTRATION", "WARN", "0.3056", "0.25")], [(x["code"], x["severity"], x["value"], x["threshold"]) for x in w])
        self.assertIs(risk.warnings_for_preview, risk.pretrade)
        assert_integrity(self, rt, "full")

    def test_concentration_leverage_and_mark_warnings(self):
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 300000000).ok)
        # 8,000 AAPL @ 339.50 = 2,716,000.00 (fee 40.00); 100 MSFT @ 500 (fee 1.00) has no mark -> unpriced (NAV keeps its cost, partial)
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 8000, "339.50").ok)
        self.assertTrue(buy(rt, US_EXCHANGE, "MSFT", 100, "500.00").ok)
        # 60 ESZ26 @ 7,700: IM 17,500 x 60 = 1,050,000.00 (static table), fee 2.25 x 60 = 135.00; notional 60 x 50 x 7,700 = 23,100,000.00
        set_mark(rt, "ESZ26", "7700")
        open_position(rt, US_EXCHANGE, "ESZ26", "LONG", 60, "7700", fee_c=13500)
        self.assertEqual(105000000, bal(rt, US_EXCHANGE, "1300", inst="ESZ26"))
        set_mark(rt, "AAPL", "339.50", "stale")
        v = risk.report(rt, "ALL")
        # NAV = 10,000,000 - 40 - 1 - 135 = 9,999,824.00 (MSFT at cost, partial); gross = 2,716,000 + 23,100,000 = 25,816,000 (MSFT excluded: no mark)
        self.assertEqual((999982400, True, ["MSFT"], 2581600000), (v.nav_c, v.partial, v.missing, v.gross_c))
        # leverage 25,816,000 / 9,999,824 = 2.58164... -> 2.5816 (> 2x); AAPL 2,716,000 / 9,999,824 = 0.27160 -> 0.2716; ESZ26 23,100,000 / 9,999,824 = 2.31004 -> 2.3100
        self.assertEqual(D("2.5816"), v.leverage)
        codes = sorted((w["code"], w["instrument_id"], w["value"]) for w in v.warnings)
        self.assertEqual([("CONCENTRATION", "AAPL", "0.2716"), ("CONCENTRATION", "ESZ26", "2.3100"), ("LEVERAGE", None, "2.5816"), ("STALE_MARK", "AAPL", "stale"), ("UNPRICED", "MSFT", "unavailable")], codes)
        self.assertTrue(all(w["severity"] == "WARN" for w in v.warnings))
        self.assertEqual([("US equities", 271600000), ("Index futures (notional)", 2310000000)], [(e["bucket"], e["long_c"]) for e in v.exposures])
        self.assertEqual(0, v.dv01_c)                                              # ES is not a rates future
        # equities -10 %: -(271,600 + 2,310,000) = -2,581,600.00 with MSFT unpriced
        s = by_name(v.scenarios, "equities -10 %")
        self.assertEqual((-258160000, ["MSFT"], {"securities": -27160000, "futures": -231000000}), (s["pnl_c"], s["unpriced"], s["detail"]["by_class"]))
        # margin row: required = posted = IM 1,050,000.00; MM 15,900 x 60 = 954,000 -> margin excess 9,999,824 - 954,000 = 9,045,824.00; no maintenance shortfall
        self.assertEqual([(US_EXCHANGE, 105000000, 105000000)], [(m["account_id"], m["required_c"], m["posted_c"]) for m in v.margin])
        self.assertEqual((904582400, 95400000, 0, 105000000), (v.financing_liquidity["margin_excess_c"], v.financing_liquidity["maintenance_required_c"],
                                                               v.financing_liquidity["maintenance_shortfall_c"], v.financing_liquidity["futures_im_posted_c"]))
        self.assertEqual(105000000, v.encumbrance_c)
        # a short-option margin above 50 % of the account's NAV warns; an account scope reports that account only
        self.assertTrue(fund(rt, "T1:DEBT", 100000000).ok)
        with rt.db.writer() as tx:
            tx.x("INSERT INTO margin_state(account_id, as_of_ts, options_required_c, options_detail_json, note) VALUES (?,?,?,?,?)", ("T1:DEBT", tx.ts, 60000000, "{}", "test"))
        r = post_entry(rt, [["T1:DEBT", "1310", "USD", 60000000], ["T1:DEBT", "1010", "USD", -60000000]], "test:optmargin:1", kind="MARGIN")
        self.assertTrue(r.ok, r.body)
        # DEBT NAV 1,000,000 (cash 400,000 + margin deposit 600,000); 600,000 / 1,000,000 = 0.6000 > 0.50
        va = risk.report(rt, "T1:DEBT")
        self.assertEqual([("OPTION_MARGIN", "T1:DEBT", "0.6000")], [(w["code"], w["account_id"], w["value"]) for w in va.warnings])
        self.assertEqual((100000000, 0, 0, []), (va.nav_c, va.gross_c, len(va.positions), va.exposures))
        self.assertEqual([("T1:DEBT", 60000000, 60000000)], [(m["account_id"], m["required_c"], m["posted_c"]) for m in va.margin])
        set_mark(rt, "MSFT", "500.00")                                               # price MSFT so the full integrity run has no partial-NAV WARN (M1)
        self.assertEqual([], risk.report(rt, "ALL").missing)
        assert_integrity(self, rt, "full")

    def test_greeks_and_option_scenarios(self):
        rt = self.rt
        now = rt.clock.epoch()
        self.assertTrue(fund(rt, US_EXCHANGE, 10000000).ok)
        set_mark(rt, "SPX", "500")
        p1 = ensure_option(rt, "SPX261120P00700000", "SPX").instrument_id        # SPX -> EUROPEAN, multiplier 100
        p2 = ensure_option(rt, "SPX261218P00700000", "SPX").instrument_id
        self.assertEqual(("EUROPEAN", D(100)), (rt.instruments.get(p1).style, rt.instruments.get(p1).multiplier))
        # deep in the money, sigma 0.0001, r = q = 0, T = 31/365: BSM put = K - S exactly (N(-d1) = N(-d2) = 1): 200 at S 500, 250 at 450, 150 at 550, 300 at 400, 100 at 600,
        # 225 at 475, 175 at 525; sigma + 5 pts (0.0501) still gives 200.0000 (N(23.0) = 1), sigma - 5 pts floors at 0.0001 -> 200.0000
        inputs = {"S": "500", "K": "700", "T_days": 31, "r": "0", "q": "0", "sigma": "0.0001", "model": "Black-Scholes-Merton (European)", "style": "EUROPEAN", "right": "P"}
        set_mark(rt, p1, "200.00", "computed")
        set_mark(rt, p2, "200.00", "computed")
        wrapper = ModelMarks(rt.marks, {p1: inputs, p2: inputs})
        rt.marks = wrapper
        rt.accounting.marks = wrapper
        # Cboe chain row for p1 only: delta -0.20, gamma 0.004, theta -0.05 / day, vega 0.30 / vol point
        row = ChainRow(p1, "2026-11-20", "P", D(700), D("199.50"), D("200.50"), 10, 10, None, None, 0.25, -0.20, 0.004, -0.05, 0.30, 100, 10)
        chain = ChainSnapshot("SPX", "_SPX", ChainUnderlying("SPX", D(500), None, None, None, None, None, None, None), [row], now, now, 1)
        rt.chains = types.SimpleNamespace(peek=lambda uid: chain if uid == "SPX" else None)
        # BTO 10 p1 @ 2.05 (premium 10 x 100 x 2.05 = 2,050.00, fee 6.50); BTO 5 p2 @ 2.05 (1,025.00, fee 3.25)
        open_position(rt, US_EXCHANGE, p1, "LONG", 10, "2.05", fee_c=650)
        open_position(rt, US_EXCHANGE, p2, "LONG", 5, "2.05", fee_c=325)
        v = risk.report(rt, "ALL")
        rows = {r.instrument_id: r for r in v.positions}
        self.assertEqual(("Cboe", "model"), (rows[p1].greeks_source, rows[p2].greeks_source))
        # p1 (Cboe): n = 10 x 100 = 1,000; delta -200 shares -> -200 x 500 = -100,000.00 USD; gamma USD = 1,000 x 0.004 x 500 x 500 / 100 = 10,000.00; vega 300.00; theta -50.00
        # p2 (model): delta -1.0 -> -500 shares -> -250,000.00; gamma / vega / theta 0
        self.assertEqual((-10000000, "delta USD (Cboe)"), (rows[p1].exposure_c, rows[p1].exposure_basis))
        self.assertEqual((-25000000, "delta USD (model)"), (rows[p2].exposure_c, rows[p2].exposure_basis))
        g = v.greeks
        self.assertEqual((D("-700.0000"), -35000000, 1000000, 30000, -5000, []), (g["delta_shares"], g["delta_c"], g["gamma_c"], g["vega_c"], g["theta_c"], g["unavailable"]))
        self.assertEqual(1, len(v.greeks_by_underlying))
        u = v.greeks_by_underlying[0]
        self.assertEqual(("SPX", 15, D("-700.0000"), -35000000, 1000000, 30000, -5000, ["Cboe", "model"]),
                         (u["underlying"], u["contracts"], u["delta_shares"], u["delta_c"], u["gamma_c"], u["vega_c"], u["theta_c"], u["sources"]))
        self.assertEqual([("Listed options (delta USD)", 0, 35000000, -35000000, 35000000)], [(e["bucket"], e["long_c"], e["short_c"], e["net_c"], e["gross_c"]) for e in v.exposures])
        self.assertEqual((0, 35000000, -35000000), (v.long_c, v.short_c, v.net_c))
        # scenarios through the same model: -10 % -> (250 - 200) x 1,000 + (250 - 200) x 500 = +75,000.00; +10 % -> -75,000.00; -20 % -> +150,000.00; +5 % -> -37,500.00
        self.assertEqual((7500000, -7500000, 15000000, -3750000), (by_name(v.scenarios, "equities -10 %")["pnl_c"], by_name(v.scenarios, "equities +10 %")["pnl_c"],
                                                                 by_name(v.scenarios, "equities -20 %")["pnl_c"], by_name(v.scenarios, "equities +5 %")["pnl_c"]))
        self.assertEqual({"listed options": 7500000}, by_name(v.scenarios, "equities -10 %")["detail"]["by_class"])
        self.assertEqual((0, 0, [], 0), (by_name(v.scenarios, "vol +5 pts")["pnl_c"], by_name(v.scenarios, "vol -5 pts")["pnl_c"], by_name(v.scenarios, "vol +5 pts")["unpriced"],
                                         by_name(v.scenarios, "rates +100 bp")["pnl_c"]))
        self.assertEqual([p2, p1], [c["instrument_id"] for c in v.concentration])     # |-250,000| before |-100,000|
        self.assertEqual("delta USD (model)", v.concentration[0]["note"])
        assert_integrity(self, rt, "full")

    def test_dv01_rates_scenario_and_private_unchanged(self):
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 10000000).ok)
        # 10 ZNZ26 @ 110.00, mark 110.50: IM 2,600 x 10 = 26,000.00; fee 22.50; notional 10 x 1,000 x 110.50 = 1,105,000.00; OTE 10 x 0.50 x 1,000 = 5,000.00
        set_mark(rt, "ZNZ26", "110.50")
        open_position(rt, US_EXCHANGE, "ZNZ26", "LONG", 10, "110.00", fee_c=2250)
        # private: 70,000 Testco @ 50.00 (fee 0, negotiated, lock-up 2027-04-20) in T1:PRIVATE, last-round mark 50.00 -> MV 3,500,000.00
        add_instrument(rt, "PRV-TESTCO", "PRIVATE", "Testco Inc.", "PRIVATE_MARKETS", ["PRIVATE"], 30)
        priv = add_account(rt, T1, "PRIVATE", "Private Markets", "TRADING", 50)
        self.assertTrue(fund(rt, priv, 400000000).ok)
        set_mark(rt, "PRV-TESTCO", "50.00", "private")
        open_position(rt, priv, "PRV-TESTCO", "LONG", 70000, "50.00", lock_until="2027-04-20")
        v = risk.report(rt, "ALL")
        # NAV = 10,000,000 - 22.50 + 5,000 (OTE) = 10,004,977.50
        self.assertEqual(1000497750, v.nav_c)
        # DV01 = 10 x 6.5 x 110.50 x 1,000 / 10,000 = 718.25 per bp; long -> -718.25 for +1 bp
        self.assertEqual((71825, -71825), (v.dv01_c, v.dv01_signed_c))
        self.assertEqual([("ZNZ26", -71825, 10)], [(r["instrument_id"], r["dv01_c"], r["qty"]) for r in v.dv01_rows])
        self.assertIn("duration 6.5", v.dv01_rows[0]["basis"])
        # rates +100 bp = -71,825.00; -25 bp = +17,956.25; +50 bp = -35,912.50
        self.assertEqual((-7182500, 1795625, -3591250), (by_name(v.scenarios, "rates +100 bp")["pnl_c"], by_name(v.scenarios, "rates -25 bp")["pnl_c"], by_name(v.scenarios, "rates +50 bp")["pnl_c"]))
        self.assertEqual({"futures": -7182500}, by_name(v.scenarios, "rates +100 bp")["detail"]["by_class"])
        # equities -10 %: nothing moves (a rates future and a private mark are unchanged, listed as such)
        s = by_name(v.scenarios, "equities -10 %")
        self.assertEqual((0, [], ["ZNZ26", "PRV-TESTCO"]), (s["pnl_c"], s["unpriced"], sorted(s["unchanged"], reverse=True)))
        self.assertIn("private marks and private loans are not shocked", s["note"])
        self.assertEqual([("Rates futures (notional)", 110500000), ("Private / illiquid", 350000000)], [(e["bucket"], e["long_c"]) for e in v.exposures])
        # private bucket 3,500,000 / 10,004,977.50 = 0.34983 -> 0.3498 (> 30 %); the lock-up calendar lists the lot
        self.assertEqual((350000000, D("0.3498"), ["Testco Inc."]), (v.private["mv_c"], v.private["pct_nav"], v.private["top_names"]))
        self.assertEqual([("PRV-TESTCO", "Testco Inc.", priv, 70000, 350000000, D("0.3498"), "2027-04-20", "private")],
                         [(p["instrument_id"], p["name"], p["account_id"], p["qty"], p["mv_c"], p["pct_nav"], p["lock_until"], p["quality"]) for p in v.private["positions"]])
        self.assertEqual([("PRV-TESTCO", 70000, "2027-04-20")], [(l["instrument_id"], l["qty"], l["lock_until"]) for l in v.private["lockups"]])
        self.assertEqual(sorted([("CONCENTRATION", "PRV-TESTCO", "0.3498"), ("PRIVATE_ILLIQUID", None, "0.3498")]), sorted((w["code"], w["instrument_id"], w["value"]) for w in v.warnings))
        d = v.to_dict()
        self.assertEqual(("718.25", "-718.25", "-71825.00", "3500000.00", "0.3498"), (d["dv01"], d["dv01_signed"], by_name(d["scenarios"], "rates +100 bp")["pnl"], d["private"]["mv"], d["private"]["pct_nav"]))
        # the private buy hook: another 100 shares at the last round would keep the bucket above 30 %
        w = risk.pretrade(rt, priv, rt.instruments.get("PRV-TESTCO"), "BUY", 100, {"economics": {"gross": "5000.00"}})
        self.assertEqual(["CONCENTRATION", "PRIVATE_ILLIQUID"], [x["code"] for x in w])
        assert_integrity(self, rt, "full")

    def test_fx_exposure_scenarios(self):
        rt = self.rt
        # EUR cash in FOREIGN_FX: a test entry Dr 1010 EUR 100,000 / Cr 3900 EUR 100,000 ... keeps 3900 non-zero in base; instead swap USD cash into EUR at 1.1238 via
        # a balanced two-currency entry: Dr 1010 EUR 100,000 (fx 1.1238 -> 112,380.00) / Cr 1010 USD 112,380.00 inside the same account (an FX conversion at the mark)
        self.assertTrue(fund(rt, "T1:FOREIGN_FX", 20000000).ok)
        r = post_entry(rt, [["T1:FOREIGN_FX", "1010", "EUR", 10000000, None, "1.1238"], ["T1:FOREIGN_FX", "3900", "EUR", -10000000, None, "1.1238"],
                            ["T1:FOREIGN_FX", "3900", "USD", 11238000], ["T1:FOREIGN_FX", "1010", "USD", -11238000]], "test:fx:1", kind="FX")
        self.assertTrue(r.ok, r.body)
        v = risk.report(rt, "ALL")
        # EUR 100,000 x 1.1238 = 112,380.00 USD; 112,380 / NAV 10,000,000 = 0.0112
        self.assertEqual([("EUR", 10000000, 11238000, D("1.1238"), D("0.0112"))], [(f["currency"], f["native_c"], f["usd_c"], f["rate"], f["pct_nav"]) for f in v.fx_exposure])
        self.assertEqual([("FX (EUR)", 11238000, 0)], [(e["bucket"], e["long_c"], e["short_c"]) for e in v.exposures])
        self.assertEqual(0, v.gross_c)                                               # currency exposure is its own axis, not a position
        # USD +5 % vs foreign: -5 % x 112,380 = -5,619.00; USD -5 %: +5,619.00
        self.assertEqual((-561900, 561900), (by_name(v.scenarios, "USD +5 % vs foreign currencies")["pnl_c"], by_name(v.scenarios, "USD -5 % vs foreign currencies")["pnl_c"]))
        self.assertEqual({"EUR": -561900}, by_name(v.scenarios, "USD +5 % vs foreign currencies")["detail"]["by_currency"])
        assert_integrity(self, rt, "full")

    def test_exposure_per_pb(self):
        rt = self.rt
        gs = add_account(rt, T1, "PB_GS", "Goldman Sachs", "PB", 60, broker="GS", financing=True)
        self.assertTrue(fund(rt, gs, 150000000).ok)
        # 3,000 AAPL @ 339.50 in the GS account: gross 1,018,500.00, GS fee 3,000 x 0.0030 = 9.00
        self.assertTrue(buy(rt, gs, "AAPL", 3000, "339.50", fee_c=900).ok)
        # one IRS, pay fixed, notional 10,000,000, stored clean PV -123.45, VM balance -160,000.00 (we posted 160,000 to 1320), IM 50,000.00 in 1330
        book_swap(rt, gs, "SWP-000001", 1000000000, -12345, -16000000, 5000000)
        rt.swaps = types.SimpleNamespace(dv01_c=lambda services, row: 75000)        # +750.00 per +1 bp (pay fixed gains when rates rise)
        self.assertEqual((16000000, 5000000, 129000000), (bal(rt, gs, "1320", inst="SWP-000001"), bal(rt, gs, "1330", inst="SWP-000001"), bal(rt, gs, "1010")))
        v = risk.report(rt, "ALL")
        # headline exposure = the AAPL long only (the IRS is in its own bucket, not in gross / net)
        self.assertEqual((101850000, 0, 101850000), (v.long_c, v.short_c, v.net_c))
        self.assertEqual({gs: 101850000}, {k: b["long_c"] for k, b in v.exposure_by_account.items()})
        self.assertEqual([("US equities", 101850000, 0, True), ("OTC swaps (notional)", 0, 1000000000, False)], [(e["bucket"], e["long_c"], e["short_c"], e["headline"]) for e in v.exposures])
        # DV01 from swaps.dv01_c: +750.00; rates +100 bp = +75,000.00
        self.assertEqual((75000, 75000, 7500000), (v.dv01_c, v.dv01_signed_c, by_name(v.scenarios, "rates +100 bp")["pnl_c"]))
        # PB headroom = 50 % x 1,018,500 - financing 0 - OTC IM 50,000 - OTC VM 160,000 = 299,250.00
        self.assertEqual(1, len(v.financing_liquidity["pb"]))
        pb = v.financing_liquidity["pb"][0]
        self.assertEqual((gs, "GS", True, 29925000, 0, 101850000, 5000000, 16000000, 0, 1), (pb["account_id"], pb["broker"], pb["pb_financing"], pb["headroom_c"], pb["financed_c"], pb["long_mv_c"],
                                                                                            pb["otc_im_c"], pb["otc_vm_posted_c"], pb["otc_vm_received_c"], pb["contracts"]))
        # PB NAV = cash 1,290,000 + VM 160,000 + IM 50,000 + AAPL 1,018,500 - payable 1,018,509 - swap 123.45 = 1,499,867.55
        self.assertEqual(149986755, pb["nav_c"])
        self.assertEqual([(gs, 5000000, 21000000, 29925000)], [(m["account_id"], m["required_c"], m["posted_c"], m["headroom_c"]) for m in v.margin])
        self.assertIn("PB financing on", v.margin[0]["note"])
        # the OTC block per PB: 1 contract, notional 10,000,000, DV01 750.00, mark -123.45, VM balance -160,000.00, IM 50,000.00
        self.assertEqual(1, len(v.otc))
        o = v.otc[0]
        self.assertEqual((gs, "GS", 1, 1000000000, 75000, False, -12345, -16000000, 5000000, 16000000, 0, ["2036-10-20"], None),
                         (o["account_id"], o["broker"], o["contracts"], o["notional_c"], o["dv01_c"], o["dv01_partial"], o["mark_c"], o["vm_balance_c"], o["im_c"], o["vm_posted_c"],
                          o["vm_received_c"], o["maturities"], o["next_events"]))
        self.assertEqual("SWP-000001", o["contracts_detail"][0]["contract_id"])
        # VM posted 160,000 / PB NAV 1,499,867.55 = 0.10668 -> 0.1067 > 10 % -> the OTC_VM warning; nothing else (AAPL is 10.2 % of the lifetime's NAV)
        self.assertEqual([("OTC_VM", gs, "0.1067", "0.10")], [(w["code"], w["account_id"], w["value"], w["threshold"]) for w in v.warnings])
        # encumbrance = 1320 + 1330 = 210,000.00; excess liquidity = Treasury 8,500,000 + GS available (1,290,000 - 1,018,509 = 271,491.00) = 8,771,491.00
        self.assertEqual((21000000, 877149100, 5000000, 16000000), (v.encumbrance_c, v.excess_liquidity_c, v.financing_liquidity["otc_im_c"], v.financing_liquidity["otc_vm_posted_c"]))
        d = v.to_dict()
        self.assertEqual(("299250.00", "750.00", "160000.00"), (d["financing_liquidity"]["pb"][0]["headroom"], d["otc"][0]["dv01"], d["otc"][0]["vm_posted"]))
        # a report on the PB account scope alone shows the same PB figures
        va = risk.report(rt, gs)
        self.assertEqual((101850000, 29925000, 1), (va.long_c, va.financing_liquidity["pb"][0]["headroom_c"], len(va.otc)))
        assert_integrity(self, rt, "full")


    def test_leverage_gross_notional_per_account_pb_and_book(self):
        """Owner 2026-10-21 18:29: "calculate gross notional against nav too ... prob going to leverage w TRS" - the multiples by hand."""
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)                        # 1,000,000.00 Treasury -> US Exchange
        # US Exchange: 1,000 AAPL @ 339.50 (fee 1,000 x 0.005 = 5.00) -> MV 339,500.00; 2 ESZ26 @ 7,700 (fee 2 x 2.25 = 4.50) -> notional
        # 2 x 50 x 7,700 = 770,000.00 (IM 2 x 17,500 = 35,000.00 posted); short 100 SPY @ 769.32 (fee 0) -> -76,932.00 (collateral 102 %)
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)
        set_mark(rt, "ESZ26", "7700")
        open_position(rt, US_EXCHANGE, "ESZ26", "LONG", 2, "7700", fee_c=450)
        open_position(rt, US_EXCHANGE, "SPY", "SHORT", 100, "769.32")
        # Goldman PB (financing on): 3,000 AAPL @ 339.50 (fee 9.00) -> 1,018,500.00; a TRS receiving AAPL on 10,000 shares at 339.50 = notional
        # 3,395,000.00 (IM 10 % = 339,500.00, mark 0); an IRS pay fixed 10,000,000.00 (mark -123.45, VM posted 160,000.00, IM 50,000.00)
        gs = add_account(rt, T1, "PB_GS", "Goldman Sachs", "PB", 60, broker="GS", financing=True)
        self.assertTrue(fund(rt, gs, 150000000).ok)                                 # 1,500,000.00
        self.assertTrue(buy(rt, gs, "AAPL", 3000, "339.50", fee_c=900).ok)
        book_trs(rt, gs, "SWP-000002", "AAPL", 10000, 339500000, 33950000)
        book_swap(rt, gs, "SWP-000001", 1000000000, -12345, -16000000, 5000000)
        rt.swaps = types.SimpleNamespace(dv01_c=lambda services, row: 75000 if row["kind"] == "IRS" else None)
        v = risk.report(rt, "ALL")
        # NAV = 10,000,000 - 5.00 - 4.50 - 9.00 - 123.45 (the IRS overlay) = 9,999,858.05 (IM / VM / collateral are assets: NAV-neutral)
        self.assertEqual(999985805, v.nav_c)
        # headline: long = 339,500 + 1,018,500 + 770,000 + 3,395,000 (TRS) = 5,523,000.00; short = 76,932.00 (SPY);
        # gross = 5,599,932.00; net = 5,446,068.00 (the IRS is not in the headline)
        self.assertEqual((552300000, 7693200, 559993200, 544606800), (v.long_c, v.short_c, v.gross_c, v.net_c))
        # gross NOTIONAL = 339,500 + 1,018,500 + 76,932 (|MV|) + 770,000 (ES) + 3,395,000 (TRS) + 10,000,000 (IRS) = 15,599,932.00
        self.assertEqual((1559993200, 339500000, []), (v.gross_notional_c, v.trs_notional_c, v.notional_missing))
        # / 9,999,858.05: gross 5,599,932 -> 0.56000115 -> 0.5600; notional 15,599,932 -> 1.56001534 -> 1.5600; long 5,523,000 -> 0.55230784 -> 0.5523;
        # short 76,932 -> 0.00769331 -> 0.0077; net 5,446,068 -> 0.54461453 -> 0.5446; TRS 3,395,000 -> 0.33950482 -> 0.3395
        self.assertEqual((D("0.5600"), D("1.5600"), D("0.5523"), D("0.0077"), D("0.5446"), D("0.3395")),
                         (v.gross_leverage, v.gross_notional_leverage, v.long_pct_nav, v.short_pct_nav, v.net_pct_nav, v.trs_pct_nav))
        self.assertEqual(v.leverage, v.gross_leverage)
        lev = {r["account_id"]: r for r in v.leverage_by_account}
        # US Exchange (NAV 1,000,000 - 5.00 - 4.50 = 999,990.50): long 339,500 + 770,000 = 1,109,500; short 76,932; gross 1,186,432; net 1,032,568;
        # notional 1,186,432 -> 1,186,432 / 999,990.50 = 1.18644327 -> 1.1864; long 1.10951054 -> 1.1095; short 0.07693273 -> 0.0769; net 1.03257781 -> 1.0326
        u = lev[US_EXCHANGE]
        self.assertEqual((99999050, 110950000, 7693200, 118643200, 103256800, 118643200), (u["nav_c"], u["long_c"], u["short_c"], u["gross_c"], u["net_c"], u["gross_notional_c"]))
        self.assertEqual((D("1.1864"), D("1.1864"), D("1.1095"), D("0.0769"), D("1.0326")),
                         (u["gross_leverage"], u["gross_notional_leverage"], u["long_pct_nav"], u["short_pct_nav"], u["net_pct_nav"]))
        # Goldman (NAV 1,500,000 - 9.00 - 123.45 = 1,499,867.55): gross 1,018,500 + 3,395,000 = 4,413,500 -> 2.94259316 -> 2.9426;
        # notional 4,413,500 + 10,000,000 = 14,413,500 -> 9.60984855 -> 9.6098; TRS 3,395,000 -> 2.26353320 -> 2.2635
        g = lev[gs]
        self.assertEqual((149986755, 441350000, 1441350000, D("2.9426"), D("9.6098"), D("2.2635"), D("0.0000")),
                         (g["nav_c"], g["gross_c"], g["gross_notional_c"], g["gross_leverage"], g["gross_notional_leverage"], g["trs_pct_nav"], g["short_pct_nav"]))
        pb = v.financing_liquidity["pb"][0]                                        # the PB row carries the same multiples
        self.assertEqual((gs, D("2.9426"), D("9.6098"), D("2.2635"), 339500000), (pb["account_id"], pb["gross_leverage"], pb["gross_notional_leverage"], pb["trs_pct_nav"], pb["trs_notional_c"]))
        self.assertEqual(D("2.9426"), v.exposure_by_account[gs]["gross_leverage"])
        # per book: one open lifetime -> its NAV = the scope's NAV and the same multiples
        self.assertEqual([(T1, 999985805, D("0.5600"), D("1.5600"))], [(b["lifetime_id"], b["nav_c"], b["gross_leverage"], b["gross_notional_leverage"]) for b in v.by_book])
        # BM-27: the equity delta = stocks 339,500 + 1,018,500 - 76,932 = 1,281,068 + index futures 770,000 + TRS 3,395,000 = 5,446,068.00
        self.assertEqual((128106800, 77000000, 339500000, 0, 544606800), (v.delta["equities_c"], v.delta["index_futures_c"], v.delta["trs_c"], v.delta["options_c"], v.delta["total_c"]))
        by_u = {r["underlying"]: r for r in v.delta_by_underlying}
        self.assertEqual((135800000, 339500000, 475300000), (by_u["AAPL"]["stock_c"], by_u["AAPL"]["trs_c"], by_u["AAPL"]["total_c"]))
        # PO-18: readable contract labels; signed DV01 of the IRS (+750.00); the TRS has no DV01 row
        labels = {c["contract_id"]: c["label"] for c in v.otc[0]["contracts_detail"]}
        self.assertEqual("TRS AAPL (receive)", labels["SWP-000002"])
        self.assertEqual([("SWP-000001", 75000)], [(r["instrument_id"], r["dv01_c"]) for r in v.dv01_rows])
        # warnings stay warnings: TRS AAPL is 33.95 % of NAV (> 25 %) and GS's posted VM is 10.67 % of its NAV; nothing about leverage (0.56x < 2x)
        self.assertEqual(["CONCENTRATION", "OTC_VM"], sorted(w["code"] for w in v.warnings))
        self.assertIn("TRS AAPL", next(w["message"] for w in v.warnings if w["code"] == "CONCENTRATION"))
        d = v.to_dict()
        self.assertEqual(("0.5600", "1.5600", "15599932.00", "0.3395"), (d["gross_leverage"], d["gross_notional_leverage"], d["gross_notional"], d["trs_pct_nav"]))
        assert_integrity(self, rt, "full")

    def test_unpriced_private_at_cost_and_futures_maintenance_after_the_trade(self):
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        set_mark(rt, "ESZ26", "7700")
        open_position(rt, US_EXCHANGE, "ESZ26", "LONG", 2, "7700", fee_c=450)          # fee 4.50
        # SR-03: 70,000 shares of a private name WITHOUT a price per share, bought at 50.00 -> cost 3,500,000.00; NAV carries it at cost
        add_instrument(rt, "PRV-NOMARK", "PRIVATE", "Nomark Inc.", "PRIVATE_MARKETS", ["PRIVATE"], 30)
        priv = add_account(rt, T1, "PRIVATE", "Private Markets", "TRADING", 50)
        self.assertTrue(fund(rt, priv, 400000000).ok)
        open_position(rt, priv, "PRV-NOMARK", "LONG", 70000, "50.00", lock_until="2027-04-20")
        v = risk.report(rt, "ALL")
        # NAV = 10,000,000 - 4.50 = 9,999,995.50 (the private position at its cost, partial)
        self.assertEqual((999999550, True, ["PRV-NOMARK"]), (v.nav_c, v.partial, v.missing))
        # the bucket at carrying value: 3,500,000 / 9,999,995.50 = 0.35000016 -> 0.3500 (was 0.00 %: "NAV excludes it")
        self.assertEqual((350000000, D("0.3500"), 350000000, ["PRV-NOMARK"]), (v.private["mv_c"], v.private["pct_nav"], v.private["at_cost_c"], v.private["unpriced"]))
        p = v.private["positions"][0]
        self.assertEqual((350000000, D("0.3500"), "at cost (no mark)", True), (p["mv_c"], p["pct_nav"], p["basis"], p["at_cost"]))
        self.assertEqual([("PRV-NOMARK", 350000000, D("0.3500"), "at cost (no mark)")], [(c["instrument_id"], c["mv_c"], c["pct_nav"], c["note"]) for c in v.concentration if c["instrument_id"] == "PRV-NOMARK"])
        w = {x["code"]: x for x in v.warnings}
        self.assertEqual(("0.3500", "0.3500", "INFO"), (w["CONCENTRATION"]["value"], w["PRIVATE_ILLIQUID"]["value"], w["UNPRICED"]["severity"]))
        self.assertEqual("PRV-NOMARK has no valid mark; NAV carries it at cost (3,500,000.00)", w["UNPRICED"]["message"])
        # the private pre-trade line: 10,000 more at 50.00 = 500,000.00 -> (3,500,000 + 500,000) / 9,999,995.50 = 0.40000018 -> 0.4000
        lines = risk.pretrade(rt, priv, rt.instruments.get("PRV-NOMARK"), "BUY", 10000, {"economics": {"gross": "500000.00"}})
        self.assertEqual([("CONCENTRATION", "WARN", "0.4000"), ("PRIVATE_ILLIQUID", "WARN", "0.4000")], [(x["code"], x["severity"], x["value"]) for x in lines])
        # ORC-1: maintenance after the trade = 15,900.00 per contract x |held + signed order|: SELL 2 closes (0.00), BUY 1 -> 3 x 15,900 = 47,700.00,
        # SELL 3 -> short 1 -> 15,900.00
        es = rt.instruments.get("ESZ26")

        def maint(side, qty):
            m = [x for x in risk.pretrade(rt, US_EXCHANGE, es, side, qty, {}) if x["code"] == "FUTURES_MAINTENANCE"][0]
            return m["value"], m["severity"], m["message"]
        val, sev, msg = maint("SELL", 2)
        self.assertEqual(("0.00", "INFO"), (val, sev))
        self.assertEqual("maintenance margin after the trade 0.00: flat after the trade (+2 held, 2 sold)", msg)
        self.assertEqual("47700.00", maint("BUY", 1)[0])
        val, _sev, msg = maint("SELL", 3)
        self.assertEqual("15900.00", val)
        self.assertIn("1 contract short after the trade (+2 held, 3 sold) x 15,900.00 per contract", msg)
        assert_integrity(self, rt, "ledger")


# ---- compliance ---------------------------------------------------------------------------------------------------------------------
class FakeLending:
    """WP-06 stand-in for the preview: a full locate."""

    def locate(self, services, account_id, inst, qty):
        return types.SimpleNamespace(instrument_id=inst.instrument_id, qty=qty, available=True, qty_available=qty, fee_rate=D("0.003"), rebate_rate=D("0.03"), tier="GC",
                                     lender="test lender", collateral_c=0, mark=D("310"), mark_quality="delayed", valid_until=0, basis="test locate")


class ComplianceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-comp-")
        self.rt = runtime(self.tmp, books="real")               # P1 INDIVIDUAL 1,000,000; F1 FUND 25,000,000; I1 INSTITUTION 1,000,000,000
        self.rt.accounting.selected_book = "F"
        with self.rt.db.writer() as tx:                           # KO: 1,000,000 shares outstanding (as of 2026-07-01) so 5 % = 50,000 shares
            tx.x("UPDATE instruments SET spec_json=? WHERE instrument_id='KO'", (json.dumps({"shares_outstanding": 1000000, "shares_asof": "2026-07-01", "adv": 500000}),))
        self.rt.instruments.refresh("KO")
        set_mark(self.rt, "KO", "10.00")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_d11_compliance_notices(self):
        rt = self.rt
        now = rt.clock.epoch()
        F, I, P = "F1:US_EXCHANGE", "I1:US_EXCHANGE", "P1:US_EXCHANGE"
        aapl, ko = rt.instruments.get("AAPL"), rt.instruments.get("KO")
        # --- Rule 201: prev close 340.00, trigger 0.90 x 340 = 306.00; day low 305.00 <= 306.00 -> notice; 306.00 -> notice (at the level); 306.01 -> ok
        rt.marks.set_quote(quote("AAPL", 310, 340, 305, now))
        ns = compliance.pretrade(rt, F, aapl, "SELL_SHORT", 100, {})
        n = notices(ns, "RULE_201")[0]
        self.assertEqual(("Rule 201 short-sale price test", "notice", "low 305 vs 306.0000", "verify the current rule text"), (n.rule, n.status, n.value, n.verify))
        self.assertEqual({"day_low": "305", "prev_close": "340", "trigger_level": "306.0000"}, {k: n.data_used[k] for k in ("day_low", "prev_close", "trigger_level")})
        rt.marks.set_quote(quote("AAPL", 310, 340, 306, now))
        self.assertEqual("notice", notices(compliance.pretrade(rt, F, aapl, "SELL_SHORT", 100, {}), "RULE_201")[0].status)
        rt.marks.set_quote(quote("AAPL", 310, 340, "306.01", now))
        self.assertEqual("ok", notices(compliance.pretrade(rt, F, aapl, "SELL_SHORT", 100, {}), "RULE_201")[0].status)
        self.assertEqual("n/a", notices(compliance.pretrade(rt, F, aapl, "BUY", 100, {}), "RULE_201")[0].status)
        rt.marks.set_quote(quote("AAPL", 310, 340, 305, now))
        # --- 13D/G and Section 16 on KO (1,000,000 shares outstanding): held 40,000 = 4.00 %; +10,000 -> 5.00 % notice; +60,000 -> 10.00 % (both); +5,000 -> 4.50 % ok
        self.assertTrue(fund(rt, F, 200000000).ok)
        self.assertTrue(buy(rt, F, "KO", 40000, "10.00").ok)                        # fee 40,000 x 0.005 = 200.00
        n = notices(compliance.pretrade(rt, F, ko, "BUY", 10000, {}), "13DG")[0]
        self.assertEqual(("notice", "5.00 %", 50000, 1000000, "2026-07-01"), (n.status, n.value, n.data_used["qty"], n.data_used["shares_outstanding"], n.data_used["shares_asof"]))
        self.assertIn("as of 2026-07-01", n.message)
        ns = compliance.pretrade(rt, F, ko, "BUY", 60000, {})
        self.assertEqual([("SECTION_16", "notice", "10.00 %"), ("13DG", "notice", "10.00 %")], [(x.code, x.status, x.value) for x in ns if x.code in ("13DG", "SECTION_16")])
        self.assertEqual(("ok", "4.50 %"), ((lambda x: (x.status, x.value))(notices(compliance.pretrade(rt, F, ko, "BUY", 5000, {}), "13DG")[0])))
        self.assertEqual([("ok", "4.00 %")], [(x.status, x.value) for x in notices(compliance.holdings(rt, "F1:ALL"), "13DG")])
        self.assertEqual([], notices(compliance.holdings(rt, "F1:ALL"), "SECTION_16"))
        self.assertTrue(buy(rt, F, "KO", 10000, "10.00").ok)                        # now 50,000 = 5.00 %
        self.assertEqual([("notice", "5.00 %", "KO")], [(x.status, x.value, x.instrument_id) for x in notices(compliance.holdings(rt, "F1:ALL"), "13DG")])
        # --- self-trading: a WORKING SELL of KO in the Institutional book, then a fill in another own account within 60 s
        with rt.db.writer() as tx:
            tx.x("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, tif, status, session_group, created_ts, updated_ts, expires_ts, preview_json)"
                 " VALUES ('O-000900','ui:selftrade-1',?,?,'SELL',100,'MARKET','DAY','WORKING','US_EQUITY',?,?,?,'{}')", (I, "KO", now, now, now + 7200))
        n = notices(compliance.pretrade(rt, F, ko, "BUY", 100, {}), "SELF_TRADING")[0]
        self.assertEqual(("notice", "1 order(s), 0 fill(s)"), (n.status, n.value))
        self.assertIn("O-000900", n.message)
        self.assertIn(I, n.message)
        self.assertEqual("ok", notices(compliance.pretrade(rt, F, ko, "SELL", 100, {}), "SELF_TRADING")[0].status)     # same side: not a cross
        self.assertTrue(fund(rt, I, 1000000).ok)
        self.assertTrue(buy(rt, I, "KO", 100, "10.00").ok)                           # a BUY fill now in I1
        n = notices(compliance.pretrade(rt, F, ko, "SELL", 100, {}), "SELF_TRADING")[0]
        self.assertEqual(("notice", "0 order(s), 1 fill(s)"), (n.status, n.value))
        self.assertIn("filled 0 s ago in I1:US_EXCHANGE", n.message)
        rt.clock.advance(61)
        self.assertEqual("ok", notices(compliance.pretrade(rt, F, ko, "SELL", 100, {}), "SELF_TRADING")[0].status)     # the fill is 61 s old
        self.assertEqual("notice", notices(compliance.pretrade(rt, F, ko, "BUY", 100, {}), "SELF_TRADING")[0].status)  # the working SELL still crosses a BUY
        hn = notices(compliance.holdings(rt, "ALL_BOOKS"), "SELF_TRADING")
        self.assertEqual({"ok"}, {x.status for x in hn})                                                                 # no opposite-side pair of working orders
        # --- wash sale: sell 10,000 KO @ 9.00 against lots at 10.00 -> realised 10,000 x (9 - 10) = -10,000.00 (fee 50.00 separate)
        r = sell(rt, F, "KO", 10000, "9.00")
        self.assertEqual(-1000000, r.body["realised_c"])
        n = notices(compliance.pretrade(rt, F, ko, "BUY", 100, {}), "WASH_SALE")[0]
        self.assertEqual(("notice", "-10,000.00"), (n.status, n.value))
        self.assertEqual(-1000000, n.data_used["loss_sales"][0]["realised_c"])
        # a further loss sale: 9.00 < avg cost 10.00 with a purchase in the window -> notice; 10.50 -> ok (not a loss)
        n = notices(compliance.pretrade(rt, F, ko, "SELL", 100, {"execution": {"expected_price": "9.00"}}), "WASH_SALE")[0]
        self.assertEqual(("notice", "9.00 vs 10"), (n.status, n.value))
        self.assertEqual("ok", notices(compliance.pretrade(rt, F, ko, "SELL", 100, {"execution": {"expected_price": "10.50"}}), "WASH_SALE")[0].status)
        hn = notices(compliance.holdings(rt, "F1:ALL"), "WASH_SALE")
        self.assertEqual([("notice", "-10,000.00", "KO")], [(x.status, x.value, x.instrument_id) for x in hn])          # the second lot's buy is a purchase within 30 days
        # --- none of this blocks: the preview hook runs both modules; a SELL_SHORT with the Rule 201 trigger previews ok with the notices attached
        rt.compliance, rt.risk, rt.lending = compliance, risk, FakeLending()
        pv = orders.preview(rt, {"account_id": F, "instrument_id": "AAPL", "side": "SELL_SHORT", "qty": 100, "order_type": "LIMIT", "limit_px": "300.00", "tif": "DAY"})
        self.assertEqual((True, []), (pv["ok"], pv["blockers"]))
        rules = {c["rule"]: c["status"] for c in pv["compliance"]}
        self.assertEqual("notice", rules["Rule 201 short-sale price test"])
        self.assertEqual("ok", rules["Reg SHO 203(b) locate"])
        self.assertTrue(all(c["verify"] == "verify the current rule text" for c in pv["compliance"]))
        self.assertEqual(["CONCENTRATION"], [w["code"] for w in pv["risk"]])
        self.assertEqual("INFO", pv["risk"][0]["severity"])
        self.assertNotIn("compliance notices unavailable", " ".join(pv["warnings"]))
        self.assertNotIn("risk notices unavailable", " ".join(pv["warnings"]))
        # the FUND profile never runs the Personal-book rules, and the Personal book never runs the fund rules
        f_codes = {x.code for x in compliance.holdings(rt, "F1:ALL")}
        self.assertTrue({"13F", "13H1", "FORM_SHO", "13DG", "WASH_SALE", "SELF_TRADING", "REG_SHO"} <= f_codes, f_codes)
        self.assertFalse({"PDT", "REG_T", "CASH_ACCOUNT_GOOD_FAITH"} & f_codes)
        self.assertEqual("ok", notices(compliance.holdings(rt, "F1:ALL"), "13F")[0].status)                            # 40,000 KO x 10 = 400,000 < 100 M
        self.assertEqual("400000.00", notices(compliance.holdings(rt, "F1:ALL"), "13F")[0].value)
        assert_integrity(self, rt, "full")

    def test_profiles_pdt_13f_from_start_and_position_limits(self):
        rt = self.rt
        P, I = "P1:US_EXCHANGE", "I1:US_EXCHANGE"
        # --- PDT only for the Personal book: withdraw 980,000 -> NAV 20,000 < 25,000; four same-day round trips of 100 KO
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": "P1", "amount_c": 98000000, "currency": "USD"}).ok)
        self.assertTrue(fund(rt, P, 1000000).ok)
        for _ in range(4):
            self.assertTrue(buy(rt, P, "KO", 100, "10.00").ok)
            self.assertTrue(sell(rt, P, "KO", 100, "10.00").ok)
        # NAV = 20,000 - 8 x 1.00 fees = 19,992.00; day trades = min(4 buys, 4 sells) = 4
        n = notices(compliance.holdings(rt, "P1:ALL"), "PDT")[0]
        self.assertEqual(("notice", "4 day trades; NAV 19,992.00", 4), (n.status, n.value, n.data_used["day_trades_5_sessions"]))
        self.assertEqual([], notices(compliance.holdings(rt, "F1:ALL"), "PDT"))
        self.assertEqual([], notices(compliance.holdings(rt, "I1:ALL"), "PDT"))
        # a fifth round trip's closing sale counts as a day trade in the preview
        self.assertTrue(buy(rt, P, "KO", 100, "10.00").ok)
        n = notices(compliance.pretrade(rt, P, rt.instruments.get("KO"), "SELL", 100, {}), "PDT")[0]
        self.assertEqual(("notice", True, "5 day trades; NAV 19,991.00"), (n.status, n.data_used["this_order_is_a_day_trade"], n.value))
        # Reg T and good-faith notices exist only for the Personal book; the good-faith notice names the unsettled purchase
        p_codes = {x.code for x in compliance.pretrade(rt, P, rt.instruments.get("KO"), "SELL", 100, {})}
        self.assertTrue({"REG_T", "CASH_ACCOUNT_GOOD_FAITH", "PDT", "13DG", "WASH_SALE", "SELF_TRADING", "REG_SHO", "RULE_201"} <= p_codes, p_codes)
        self.assertFalse({"13F", "13H1", "OPTION_LIMITS", "FUTURES_LIMITS", "FORM_SHO"} & p_codes)
        n = notices(compliance.pretrade(rt, P, rt.instruments.get("KO"), "SELL", 100, {}), "CASH_ACCOUNT_GOOD_FAITH")[0]
        self.assertEqual(("notice", "2026-10-21"), (n.status, n.data_used["settles"]))
        # --- 13F "applies from the start" only for the Institutional book
        self.assertTrue(fund(rt, I, 50000000000).ok)
        n = notices(compliance.holdings(rt, "I1:ALL"), "13F")[0]
        self.assertEqual(("info", "applies from the start (institutional investment manager)"), (n.status, n.threshold))
        self.assertIn("13F applies: file quarterly", n.message)
        self.assertEqual("ok", notices(compliance.holdings(rt, "F1:ALL"), "13F")[0].status)
        self.assertEqual([], notices(compliance.holdings(rt, "P1:ALL"), "13F"))
        # --- futures spot-month limit (ES 20,000): 16,000 long = 80 % -> notice; +4,000 in the preview -> breach; options default tier 250,000: 200,000 calls -> notice, +50,000 -> breach
        set_mark(rt, "ESZ26", "7700")
        open_position(rt, I, "ESZ26", "LONG", 16000, "7700", fee_c=3600000)        # IM 17,500 x 16,000 = 280,000,000.00; fee 2.25 x 16,000 = 36,000.00
        es = rt.instruments.get("ESZ26")
        n = notices(compliance.holdings(rt, "I1:ALL"), "FUTURES_LIMITS")[0]
        self.assertEqual(("notice", "net +16,000", {"root": "ES", "net": 16000, "limit": 20000}), (n.status, n.value, n.data_used))
        self.assertEqual("breach", notices(compliance.pretrade(rt, I, es, "BUY", 4000, {}), "FUTURES_LIMITS")[0].status)
        self.assertEqual("ok", notices(compliance.pretrade(rt, I, es, "SELL", 1000, {}), "FUTURES_LIMITS")[0].status)   # net 15,000 = 75 %
        c800 = ensure_option(rt, "SPY261120C00800000", "SPY")
        set_mark(rt, c800.instrument_id, "1.00")
        open_position(rt, I, c800.instrument_id, "LONG", 200000, "1.00", fee_c=13000000)   # premium 200,000 x 100 x 1.00 = 20,000,000.00; fee 0.65 x 200,000 = 130,000.00
        n = notices(compliance.holdings(rt, "I1:ALL"), "OPTION_LIMITS")[0]
        self.assertEqual(("notice", "bullish 200,000 / bearish 0", "SPY"), (n.status, n.value, n.data_used["underlying"]))
        self.assertEqual("breach", notices(compliance.pretrade(rt, I, c800, "BTO", 50000, {}), "OPTION_LIMITS")[0].status)
        n = notices(compliance.pretrade(rt, I, c800, "STO", 50000, {}), "OPTION_LIMITS")[0]        # short calls are bearish: 200,000 / 50,000 -> still the 80 % notice
        self.assertEqual(("notice", "bullish 200,000 / bearish 50,000"), (n.status, n.value))
        # 13h-1 for the day: 200,000 contracts x 100 = 20,000,000 shares (>= 2 M) and 20,000,000.00 (>= 20 M) -> notice; the futures fill does not count
        n = notices(compliance.holdings(rt, "I1:ALL"), "13H1")[0]
        self.assertEqual(("notice", 20000000, 2000000000), (n.status, n.data_used["day_shares"], n.data_used["day_usd_c"]))
        # 13F from the start carries the value: SPY calls at 1.00 x 100 x 200,000 = 20,000,000.00; Form SHO: nothing short -> ok 0.00
        self.assertEqual(("info", "20000000.00"), ((lambda x: (x.status, x.value))(notices(compliance.holdings(rt, "I1:ALL"), "13F")[0])))
        self.assertEqual(("ok", "0.00"), ((lambda x: (x.status, x.value))(notices(compliance.holdings(rt, "I1:ALL"), "FORM_SHO")[0])))
        # every notice serialises to the API shape with the verify wording and never raises
        for n in compliance.holdings(rt, "ALL_BOOKS"):
            d = json.loads(json.dumps(n.to_dict()))
            self.assertEqual("verify the current rule text", d["verify"])
            self.assertIn(d["status"], ("ok", "notice", "breach", "info", "n/a"))
        assert_integrity(self, rt, "full")

    def test_reg_sho_and_private_notices(self):
        rt = self.rt
        I = "I1:US_EXCHANGE"
        now = rt.clock.epoch()
        # a short of 1,000 AAPL @ 310 backed by a borrow (collateral 1.02 x 310,000 = 316,200.00); Rule 201 on the holding uses the cached quote
        self.assertTrue(fund(rt, I, 100000000).ok)
        rt.marks.set_quote(quote("AAPL", 310, 340, 305, now))
        set_mark(rt, "AAPL", "310.00")
        open_position(rt, I, "AAPL", "SHORT", 1000, "310.00", fee_c=500)
        self.assertEqual(31620000, bal(rt, I, "1400", inst="AAPL"))
        hn = compliance.holdings(rt, "I1:ALL")
        n = notices(hn, "REG_SHO")[0]
        self.assertEqual(("ok", "1 short(s)"), (n.status, n.value))
        self.assertIn("B-000001", n.message)
        self.assertEqual([("notice", "AAPL", I)], [(x.status, x.instrument_id, x.account_id) for x in notices(hn, "RULE_201")])
        # Form SHO: gross short 1,000 x 310 = 310,000.00 < 10 M -> ok
        self.assertEqual(("ok", "310000.00"), ((lambda x: (x.status, x.value))(notices(hn, "FORM_SHO")[0])))
        # the preview with a locate -> ok; without -> notice (the borrow leg decides, never this notice)
        aapl = rt.instruments.get("AAPL")
        b = {"needed": True, "available": True, "qty_available": 500, "tier": "GC", "lender": "test lender"}
        self.assertEqual("ok", notices(compliance.pretrade(rt, I, aapl, "SELL_SHORT", 500, {"borrow": b}), "REG_SHO")[0].status)
        self.assertEqual("notice", notices(compliance.pretrade(rt, I, aapl, "SELL_SHORT", 500, {}), "REG_SHO")[0].status)
        # private markets (Institutional profile): QP status info, Rule 144 lock-up notice on the locked lot, Form D info, "13F does not cover private shares"
        add_instrument(rt, "PRV-TESTCO", "PRIVATE", "Testco Inc.", "PRIVATE_MARKETS", ["PRIVATE"], 30)
        priv = "I1:PRIVATE"                                                            # created with the lifetime for INSTITUTION (QP threshold 0)
        self.assertIn("USD", rt.accounting.cash(priv))                               # ScopeError if the account did not exist
        self.assertTrue(fund(rt, priv, 10000000).ok)
        set_mark(rt, "PRV-TESTCO", "50.00", "private")
        open_position(rt, priv, "PRV-TESTCO", "LONG", 1000, "50.00", lock_until="2027-04-20")
        hn = compliance.holdings(rt, "I1:ALL")
        self.assertEqual(("info", "institutional profile: Private Markets open from the start"), ((lambda x: (x.status, x.message.split(" (")[0]))(notices(hn, "PRIVATE_QP")[0])))
        n = notices(hn, "RULE_144")[0]
        self.assertEqual(("notice", "2027-04-20", 1000, "PRV-TESTCO"), (n.status, n.value, n.data_used["qty"], n.instrument_id))
        self.assertEqual("info", notices(hn, "FORM_D")[0].status)
        self.assertEqual("13F does not cover private shares", notices(hn, "13F_PRIVATE")[0].message)
        self.assertEqual([], notices(compliance.holdings(rt, "F1:ALL"), "PRIVATE_QP"))
        # selling inside the lock-up: the Rule 144 notice rides on the preview; a buy carries the Form D note
        pn = compliance.pretrade(rt, priv, rt.instruments.get("PRV-TESTCO"), "SELL", 100, {})
        self.assertEqual(["notice"], [x.status for x in notices(pn, "RULE_144")])
        self.assertEqual(["info"], [x.status for x in notices(compliance.pretrade(rt, priv, rt.instruments.get("PRV-TESTCO"), "BUY", 100, {}), "FORM_D")])
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
