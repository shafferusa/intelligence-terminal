"""FX_CCS_PLAN rev 2 (release 1.0.6.1, package P-D): the owner's OTC booking program ``otcplan`` - D-T5 (``run`` against a small stateful
stdlib fake of the terminal's OTC desk, written here: FX-C-17), D-T6 (``check`` passes on a fixture database and fails each of 13 tamper
cases with its evidence; it passes, listing them as other activity, when an owner fill and a UI command run between the snapshot and
the check) and D-T8 (``reprice`` reproduces the numbers of sections 3.2 and 3.3 to the cent and to the 1e-10 / 6 dp quantisations, and
``otcplan`` imports no pricing code).

The fixture records are the worked example's (FX_CCS_PLAN 3.2 / 3.3: T = 2026-10-29, curves of 2026-10-27, EURUSD 1.1158, GBPUSD 1.3323,
EURGBP print 0.83725), written in the shape P-A's ``fxcurves`` and P-B's / P-C's products store them.  Every expected number is the plan's
hand-worked one, restated in the comments:
- forward (3.2): S = 1.1158 / 1.3323 = 0.837499 (6 dp); DF*_EUR = exp(-0.03015723 x 2.019178 + 0.02477619 x 0.016438) = 0.9413073853;
  DF*_GBP = exp(-0.04729075 x 2.019178 + 0.03760957 x 0.016438) = 0.9094909816; F = 0.837499 x 0.9413073853 / 0.9094909816 = 0.866797;
  points (0.866797 - 0.837499) / 0.0001 = 292.98; hw_px = 0.866797 x 2.0 / 10,000 = 0.000173; K = 0.866797 - 0.000173 = 0.866624;
  GBP 2,000,000 x 0.866624 = 1,733,248.00; USD notional 2,000,000 x 1.1158 = 2,231,600.00; IM 6 % = 133,896.00; mark0 = -2,000,000 x
  0.000173 x 0.9094909816 x 1.3323 = -419.2533 -> -419.25; PV deltas EUR -2,000,000 x 0.9413073853 = -1,882,614.77, GBP +1,733,248 x
  0.9094909816 = +1,576,373.42.
- swap (3.3): N_USD = 2,000,000 x 1.1158 = 2,231,600.00; A* = 1.9646274388; PV(0) = -403.98 (-370.75 final-exchange timing, -33.23
  coupon delays); s_par = -403.98... / (1.1158 x 2,000,000 x 1.9646274388) = -0.9214 bp; dealt = ceil(-0.9214 + 1.0, 0.1) = +0.1 bp;
  mark0 = -447.82 (the half-width 438.43 + the rounding 9.39); IM 2 % = 44,632.00; period-1 indicative EUR 12,945.83, USD 23,571.46;
  basis01 +437.98; rate01 USD -2.25, EUR +2.32; the conversion 1.1158 - 0.00002 = 1.11578, USD 2,000,000 x 1.11578 = 2,231,560.00,
  spread 40.00; before the start the clean mark's EURUSD delta is +6.92 USD per 1 % = +620.04 EUR (6.92 / (1.1158 x 1 %)).
"""
from __future__ import annotations

import ast
import copy
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from finsim.terminal import otcplan

D = Decimal
CTX = Context(prec=34, rounding=ROUND_HALF_EVEN)
HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_PATH = os.path.join(os.path.dirname(HERE), "docs", "plans", "OWNER_FX_TRADES_2026-10-28.plan.json")
QT = 1793282370                       # 2026-10-29 09:59:30 ET (13:59:30 UTC): the prints the quotes use
QUOTED_TS = 1793282400                # 2026-10-29 10:00:00 ET
BOE = {1: "3.899390", 2: "3.955686", 3: "4.009726", 4: "4.066759", 5: "4.126083", 6: "4.185446", 7: "4.242870", 8: "4.297217", 9: "4.347877",
       10: "4.394790", 11: "4.438072", 12: "4.477821", 13: "4.514130", 14: "4.547098", 15: "4.576851", 16: "4.603534", 17: "4.627310",
       18: "4.648351", 19: "4.666836", 20: "4.682952", 21: "4.696883", 22: "4.708817", 23: "4.718942", 24: "4.727447", 25: "4.734519"}
ECB = (("3M", "0.25", "2.542643"), ("6M", "0.5", "2.693553"), ("9M", "0.75", "2.801826"), ("1Y", "1", "2.878760"), ("2Y", "2", "3.015032"),
       ("3Y", "3", "3.051054"))
#: the plan's pinned semi-annual Treasury bootstrap (P-A task 3): DF 0.5 .. 2.5
USD_DF = (("0.5", "0.9787608887"), ("1.0", "0.9564575844"), ("1.5", "0.9340077958"), ("2.0", "0.9111609015"), ("2.5", "0.8890996871"))
SCHEDULE = (("2026-11-02", "2027-02-02", "2027-02-04"), ("2027-02-02", "2027-05-03", "2027-05-05"), ("2027-05-03", "2027-08-02", "2027-08-04"),
            ("2027-08-02", "2027-11-02", "2027-11-04"), ("2027-11-02", "2028-02-02", "2028-02-04"), ("2028-02-02", "2028-05-02", "2028-05-04"),
            ("2028-05-02", "2028-08-02", "2028-08-04"), ("2028-08-02", "2028-11-02", "2028-11-06"))
SRC = "test fixture of the 10-27 rows"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---- the worked example's records (the shape fxcurves / otc_options / swaps_trs store) ---------------------------------------------------------
def _on(series, obs, value, basis):
    with localcontext(CTX):
        t = D(1) / D(365)
        z = otcplan.on_node_z(value, basis) * 100
    return {"tenor": "ON", "series": series, "obs_date": obs, "value_pct": value, "basis": basis, "t": str(t), "z": str(z), "source": SRC}


def curve_block(ccy, kind, series, first, grid):
    nodes = [{"tenor": "ON", "t": first["t"], "z": first["z"]}] + [{"tenor": ten, "t": t, "z": z} for ten, t, z in grid]
    return {"ccy": ccy, "kind": kind, "source": SRC, "as_of": "2026-10-27", "series": series, "first_node": first, "nodes": nodes, "spread_bp": "0.0",
            "level_bp": "0"}


def gbp_curve():
    with localcontext(CTX):
        grid = [(f"{m}M", str(D(m) / D(12)), z) for m, z in BOE.items()]
    return curve_block("GBP", "observed", "BOE_OIS_SPOT", _on("IUDSOIA", "2026-10-26", "3.7328", 365), grid)


def eur_curve():
    return curve_block("EUR", "PROXY", "ECB_AAA_SPOT", _on("ESTR", "2026-10-27", "2.440", 360), list(ECB))


def usd_curve():
    with localcontext(CTX):
        grid = [(f"{t}Y", t, str(-(D(df).ln()) / D(t) * 100)) for t, df in USD_DF]
    return curve_block("USD", "PROXY", "UST_PAR", _on("SOFR", "2026-10-27", "3.95", 360), grid)


def legs():
    return [{"pair": "EURUSD", "mid": "1.1158", "quote_time": QT, "source": "Yahoo EURUSD=X (delayed)", "quality": "delayed"},
            {"pair": "GBPUSD", "mid": "1.3323", "quote_time": QT, "source": "Yahoo GBPUSD=X (delayed)", "quality": "delayed"}]


LIMITS = {"text": "notional limits: none configured in the Goldman Sachs schedule; compliance has no OTC limit"}
FUNDING = {"sfi": "EUR bought with USD two business days before each obligation"}


def fwd_record():
    return {"kind": "FX_FORWARD", "pair": "EURGBP", "base": "EUR", "quote": "GBP", "trade_date": "2026-10-29", "spot_date": "2026-11-02",
            "value_date": "2028-11-02", "days": 731, "tenor": "2Y", "side": "SELL", "notional_base_c": 200000000,
            "spot": {"pair": "EURGBP", "mid": "0.837499", "basis": "derived from the USD pairs", "legs": legs(), "legs_gap_s": 0,
                     "print": {"mid": "0.83725", "quote_time": QT, "source": "Yahoo EURGBP=X (delayed)"}, "diff_pips": "-2.49", "tolerance_pips": "5.0",
                     "executable": True},
            "curves": {"EUR": eur_curve(), "GBP": gbp_curve()}, "dfs": {"base": "0.9413073853", "quote": "0.9094909816"},
            "forward": {"outright": "0.866797", "points": "292.98", "pip": "0.0001"},
            "basis": {"pair": "EURGBP", "eur_usd_level_bp": "0.0", "gbp_usd_level_bp": "0.0", "cross_bp": "0.0"},
            "dealer": {"forward_hw_bp": "2.0", "otc_spread_mult": "1.0", "hw_px": "0.000173", "K": "0.866624", "side": "SELL"},
            "im": {"pct": "0.06", "usd_notional_c": 223160000, "im_c": 13389600}, "usd_rates": legs(),
            "settlement": {"value_date": "2028-11-02", "deliver": {"ccy": "EUR", "amount_c": 200000000}, "receive": {"ccy": "GBP", "amount_c": 173324800}},
            "labels": ["MODEL", "observed: Bank of England UK OIS curve of 2026-10-27 (continuously compounded)",
                       "PROXY: ECB euro-area AAA government curve of 2026-10-27 + 0.0 bp (Goldman Sachs schedule, ASSUMED): no free EUR OIS curve",
                       "ASSUMED: the basis levels, the dealer half-width and IM are Goldman Sachs schedule terms"],
            "limits": LIMITS, "funding": FUNDING}


def ccs_record():
    return {"kind": "XCCY", "variant": "FLOAT_FLOAT", "trade_date": "2026-10-29", "spot_date": "2026-11-02", "start_date": "2026-11-02",
            "termination_date": "2028-11-02", "maturity_date": "2028-11-06",
            "deal_fx": {"pair": "EURUSD", "mid": "1.1158", "source": "Yahoo EURUSD=X (delayed)", "quote_time": QT, "quality": "delayed"},
            "curves": {"USD": usd_curve(), "EUR": eur_curve()},
            "legs": {"receive": {"currency": "USD", "index": "SOFR", "notional_c": 223160000, "spread_bp": "0"},
                     "pay": {"currency": "EUR", "index": "ESTR", "notional_c": 200000000, "spread_bp": "0.1"}},
            "basis": {"pair": "EURUSD", "level_bp": "0.0", "par_bp": "-0.9214259921902200202986522582131354", "hw_bp": "1.0", "otc_spread_mult": "1.0",
                      "dealt_bp": "0.1", "rounding_bp": "0.0214", "on_leg": "EUR", "eur_side": "pay"},
            "schedule": [{"s": s, "e": e, "p": p} for s, e, p in SCHEDULE],
            "exchanges": {"initial": {"date": "2026-11-02", "rate": "1.1158", "we_pay": {"currency": "USD", "amount_c": 223160000},
                                      "we_receive": {"currency": "EUR", "amount_c": 200000000}},
                          "final": {"date": "2028-11-06", "rate": "1.1158", "we_pay": {"currency": "EUR", "amount_c": 200000000},
                                    "we_receive": {"currency": "USD", "amount_c": 223160000}}},
            "conversion": {"on": True, "pair": "EURUSD", "mid": "1.1158", "rate": "1.11578", "sell": {"currency": "EUR", "amount_c": 200000000},
                           "buy": {"currency": "USD", "amount_c": 223156000}, "spread_cost_c": 4000, "value_date": "2026-11-02"},
            "notionals": {"usd_c": 223160000, "eur_c": 200000000},
            "results": {"pv_zero_c": -40398, "final_exchange_timing_c": -37075, "coupon_delays_c": -3323, "annuity_star": "1.9646274388", "mark0_c": -44782,
                        "basis01_c": 43798, "rate01_c": {"USD": -225, "EUR": 232}, "im": {"pct": "0.02", "im_c": 4463200}},
            "labels": ["MODEL", "PROXY: Treasury par curve of 2026-10-27, the IRS desk's stand-in for the SOFR curve",
                       "PROXY: ECB euro-area AAA government curve of 2026-10-27 + 0.0 bp (Goldman Sachs schedule, ASSUMED): no free EUR OIS curve",
                       "ASSUMED: the EUR/USD basis level, the dealer half-width and IM are Goldman Sachs schedule terms", "indicative model quote, simulated dealer"],
            "limits": LIMITS, "funding": FUNDING}


def served(x):
    """The terminal's JSON codec (api.encode) on a record: an int ``*_c`` key becomes the name without ``_c`` as a 2-dp decimal string."""
    if isinstance(x, list):
        return [served(v) for v in x]
    if not isinstance(x, dict):
        return x
    out = {}
    for k, v in x.items():
        if k.endswith("_c") and len(k) > 2 and k[:-2] not in x and isinstance(v, int) and not isinstance(v, bool):
            out[k[:-2]] = str((D(v) / 100).quantize(D("0.01")))
        elif k.endswith("_c") and isinstance(v, dict) and v and all(isinstance(t, int) for t in v.values()) and k[:-2] not in x:
            out[k[:-2]] = {kk: str((D(t) / 100).quantize(D("0.01"))) for kk, t in v.items()}
        else:
            out[k] = served(v)
    return out


# ---- D-T8: the independent re-pricer ---------------------------------------------------------------------------------------------------------
class RepriceTests(unittest.TestCase):
    def test_dt8_forward_reproduces_3_2(self):
        m = otcplan.reprice(fwd_record())
        self.assertEqual(("0.837499", "2026-11-02", "2028-11-02", 731), (m["S"], m["spot_date"], m["value_date"], m["days"]))
        # z_GBP(t_S) 3.760957, z_GBP(t_V) 4.729075, z_EUR(t_S) 2.477619, z_EUR(t_V) 3.015723 (% cc; t_S 0.016438, t_V 2.019178)
        self.assertEqual(({"t_S": "3.760957", "t_V": "4.729075"}, {"t_S": "2.477619", "t_V": "3.015723"}, "0.016438", "2.019178"),
                         (m["z"]["GBP"], m["z"]["EUR"], m["t_S"], m["t_V"]))
        self.assertEqual(("0.9413073853", "0.9094909816", "0.866797", "292.98", "0.000173", "0.866624"),
                         (m["df_base"], m["df_quote"], m["F"], m["points"], m["hw_px"], m["K"]))
        self.assertEqual((200000000, 173324800, 223160000, 13389600, -41925), (m["base_amount_c"], m["quote_amount_c"], m["usd_notional_c"], m["im_c"], m["mark0_c"]))
        self.assertEqual({"EUR": -188261477, "GBP": 157637342}, m["pv_delta_c"])
        self.assertEqual([], otcplan.compare_forward(fwd_record(), m))
        # served over HTTP (the codec's renamed cents) the same record gives the same numbers
        self.assertEqual(m, otcplan.reprice(served(fwd_record())))

    def test_dt8_swap_reproduces_3_3(self):
        m = otcplan.reprice(ccs_record())
        self.assertEqual(("1.1158", 200000000, 223160000, "2026-11-02", "2028-11-02", "2028-11-06"),
                         (m["X0"], m["n_eur_c"], m["n_usd_c"], m["start_date"], m["termination_date"], m["maturity_date"]))
        self.assertEqual(("1.9646274388", -40398, -37075, -3323), (m["annuity_star"], m["pv_zero_c"], m["final_exchange_timing_c"], m["coupon_delays_c"]))
        self.assertEqual(("-0.9214", "0.1", "0.0214", -44782), (m["par_bp"], m["dealt_bp"], m["rounding_bp"], m["mark0_c"]))
        self.assertEqual((4463200, 43798, {"USD": -225, "EUR": 232}, {"EUR": 1294583, "USD": 2357146}), (m["im_c"], m["basis01_c"], m["rate01_c"], m["coupons1"]))
        self.assertEqual({"rate": "1.11578", "usd_c": 223156000, "spread_cost_c": 4000}, m["conversion"])
        self.assertEqual({"EUR": 62004}, m["pv_delta_c"])
        self.assertEqual([], otcplan.compare_ccs(ccs_record(), m))
        self.assertEqual(m, otcplan.reprice(served(ccs_record())))

    def test_dt8_quantisation_and_a_changed_input_are_caught(self):
        rec = fwd_record()
        rec["dealer"]["K"] = "0.866625"                        # K one step of 1e-6 off: refused
        m = otcplan.reprice(rec)
        self.assertEqual(["K: the quote says 0.866625, re-derived 0.866624"], otcplan.compare_forward(rec, m))
        rec = ccs_record()
        rec["results"]["annuity_star"] = "1.9646274389"        # A* off by 1e-10
        self.assertEqual(["A*: the quote says 1.9646274389, re-derived 1.9646274388"], otcplan.compare_ccs(rec, otcplan.reprice(rec)))

    def test_dt8_dates_are_re_derived_independently(self):
        T = date(2026, 10, 29)
        self.assertEqual((date(2026, 11, 2), date(2026, 11, 2), date(2028, 11, 2), date(2028, 11, 3)),
                         (otcplan.spot_date("EURGBP", T), otcplan.spot_date("EURUSD", T), otcplan.tenor_date("EURGBP", "2Y", T),
                          otcplan.tenor_date("EURGBP", "2Y", date(2026, 10, 30))))
        # the USD T+1 rule (Veterans Day 11-11) and the cross rule (Thanksgiving 11-26)
        self.assertEqual((date(2026, 11, 12), date(2026, 11, 27)), (otcplan.spot_date("EURUSD", date(2026, 11, 10)), otcplan.spot_date("EURGBP", date(2026, 11, 24))))
        self.assertEqual([tuple(d.isoformat() for d in p) for p in otcplan.ccs_schedule(date(2026, 11, 2), 24)], list(SCHEDULE))

    def test_dt8_otcplan_imports_no_pricing_code(self):
        """FX-C-05: the re-pricer must not share code with what it checks - otcplan imports the standard library only."""
        path = os.path.join(os.path.dirname(os.path.dirname(HERE)), "finsim", "finsim", "terminal", "otcplan.py")
        if not os.path.exists(path):
            path = otcplan.__file__
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add(("." * node.level) + (node.module or ""))
        banned = [m for m in mods if m.startswith(".") or m.startswith("finsim") or any(x in m for x in ("fxcurves", "ccycal", "products", "swaps", "otc_options"))
                  or m in ("fx",)]
        self.assertEqual([], banned)
        import sys
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib:
            self.assertEqual([], sorted(m for m in mods if m.split(".")[0] not in stdlib and m != "__future__"))

    def test_dt8_cross_check_against_the_products_records(self):
        """The records P-B's and P-C's previews build on the fixture world are re-derived exactly (skipped while their tests' runtimes
        cannot be imported: this test reads their helpers, never their pricing code)."""
        import tempfile as _tf
        from finsim.terminal.products import swaps
        for label, modname, kind, terms in (("forward", "test_terminal_otc_fx_forward", "FX_FORWARD", "B_TERMS"),
                                            ("swap", "test_terminal_xccy_float", "XCCY", "TERMS")):
            try:
                mod = __import__(f"tests.{modname}", fromlist=["runtime"])
                rt_fn, pb, t = mod.runtime, mod.PB, getattr(mod, terms)
            except Exception as e:   # noqa: BLE001 - their test helpers are not importable in this tree
                self.skipTest(f"{modname} helpers unavailable: {e}")
            tmp = _tf.mkdtemp(prefix="fst-otcplan-x-")
            rt = rt_fn(tmp)
            try:
                pv = swaps.preview(rt, pb, kind, dict(t))
                rec = pv.get("pricing_record")
                self.assertIsNotNone(rec, f"{label}: no pricing record ({pv.get('blockers')})")
                mine = otcplan.reprice(dict(rec, kind=kind), kind)
                cmp = otcplan.compare_forward(rec, mine) if kind == "FX_FORWARD" else otcplan.compare_ccs(rec, mine)
                self.assertEqual([], cmp, label)
                self.assertEqual([], otcplan.input_checks(rec, date(2026, 10, 29)), label)
            finally:
                rt.close()
                shutil.rmtree(tmp, ignore_errors=True)


# ---- D-T5: run against a stateful fake terminal (FX-C-17) ------------------------------------------------------------------------------------------
MODULES = {m: "bound" for m in ("swaps", "swaps_trs", "otc_options", "fx", "ccyfunding")}


class FakeTerminal:
    """A small stateful stand-in of the terminal's OTC desk: health, contracts, preview, RFQ request / read / accept / decline, with
    the command bus's replay of a committed request id.  Scenario knobs (per kind): ``curve_missing`` (the preview answers
    CURVE_UNAVAILABLE), ``refuse`` (the next N requests answer 409 QUOTE_STALE, nothing stored), ``expire`` (the dealer does not quote the
    next N requests), ``move`` (the next N accepts answer QUOTE_MOVED), ``drop`` (the next N accepts commit, then drop the connection)."""

    def __init__(self, et="2026-10-29 10:00:00"):
        self.et = et
        self.contracts, self.rfqs, self.commands, self.seen = [], {}, {}, []
        self.curve_missing, self.refuse, self.expire, self.move, self.drop = set(), {}, {}, {}, {}
        self.next_id = {"XCCY": 7, "FX_FORWARD": 1, "RFQ": 1, "FX": 1}
        self.lock = threading.Lock()

    def health(self):
        return {"version": "1.0.6.1", "ready": True, "clock": {"ny": f"{self.et} ET"}, "modules": dict(MODULES), "integrity": {"ok": True, "hold": False},
                "workers": {"lifecycle": {"alive": True, "stalled": False}}, "providers": {p: {"ok": True} for p in ("boe", "ecb", "nyfed", "fred")}}

    @staticmethod
    def _take(knob, kind):
        if knob.get(kind, 0) > 0:
            knob[kind] -= 1
            return True
        return False

    def handle(self, method, path, query, body):
        with self.lock:
            rid = (body or {}).get("request_id")
            self.seen.append((method, path, rid))
            if method == "GET" and path == "/api/health":
                return 200, self.health()
            if method == "GET" and path == "/api/otc/contracts":
                return 200, [served(c) for c in self.contracts]
            if method == "POST" and path == "/api/otc/preview":
                if body.get("kind") in self.curve_missing:
                    return 200, {"refused": True, "blockers": [{"code": "CURVE_UNAVAILABLE", "message": "EURGBP 24M: the Bank of England OIS curve is unavailable"
                                                                " (HTTP 403 at 09:12 ET); the forward is not priced from any other curve"}]}
                return 200, {"bookable": True, "blockers": []}
            if method == "GET" and path == "/api/otc/rfqs":
                return 200, [self.view(r) for r in self.rfqs.values() if r["status"] in ("REQUESTED", "QUOTED")]
            if method == "POST" and path == "/api/otc/rfqs":
                return self.request(body)
            parts = path.strip("/").split("/")
            if len(parts) >= 4 and parts[:3] == ["api", "otc", "rfqs"]:
                r = self.rfqs.get(parts[3])
                if r is None:
                    return 404, {"error": {"code": "NOT_FOUND", "message": parts[3]}}
                if method == "GET" and len(parts) == 4:
                    if r["status"] == "REQUESTED":
                        self.dealer(r)
                    return 200, self.view(r, full=True)
                if method == "POST" and parts[4:] == ["decline"]:
                    if r["status"] in ("REQUESTED", "QUOTED"):
                        r["status"] = "DECLINED"
                        return 200, {"rfq_id": r["rfq_id"], "status": "DECLINED"}
                    return 409, {"error": {"code": "RFQ_CLOSED", "message": "closed"}}
                if method == "POST" and parts[4:] == ["accept"]:
                    return self.accept(r, rid)
            return 404, {"error": {"code": "NOT_FOUND", "message": path}}

    def request(self, body):
        rid, kind = body.get("request_id"), body["kind"]
        if rid in self.commands:
            return self.commands[rid]
        ref = (body.get("terms") or {}).get("client_ref")
        if any(c["client_ref"] == ref for c in self.contracts) or any((r["terms"].get("client_ref") == ref and r["status"] in ("REQUESTED", "QUOTED"))
                                                                       for r in self.rfqs.values()):
            return 409, {"error": {"code": "DUPLICATE_CLIENT_REF", "message": f"the reference {ref} is taken"}}
        if self._take(self.refuse, kind):
            return 409, {"error": {"code": "QUOTE_STALE", "message": "EURGBP: stale - the deal needs a fresh quote in the FX session"}}
        rfq_id = f"RFQ-{self.next_id['RFQ']:06d}"
        self.next_id["RFQ"] += 1
        self.rfqs[rfq_id] = {"rfq_id": rfq_id, "kind": kind, "terms": dict(body["terms"]), "status": "REQUESTED", "contract_id": None, "reason": None}
        out = (200, {"rfq_id": rfq_id, "status": "REQUESTED"})
        self.commands[rid] = out
        return out

    def dealer(self, r):
        if self._take(self.expire, r["kind"]):
            r.update(status="EXPIRED", reason="the simulated dealer could not quote: EURGBP: stale")
            return
        rec = ccs_record() if r["kind"] == "XCCY" else fwd_record()
        r.update(status="QUOTED", quote={"quoted_ts": QUOTED_TS, "expires_ts": QUOTED_TS + 120},
                 quote_preview={"pricing_record": rec, "trade_date": "2026-10-29", "deal_rate": "1.1158" if r["kind"] == "XCCY" else None,
                                "variant": "FLOAT_FLOAT" if r["kind"] == "XCCY" else None})

    def view(self, r, full=False):
        v = {"rfq_id": r["rfq_id"], "kind": r["kind"], "terms": r["terms"], "status": r["status"], "reason": r["reason"], "contract_id": r["contract_id"],
             "quote": r.get("quote"), "quoted_ts": (r.get("quote") or {}).get("quoted_ts")}
        if full:
            v["quote_preview"] = served(r.get("quote_preview"))
        return v

    def accept(self, r, rid):
        if rid in self.commands:
            return self.commands[rid]
        if r["status"] != "QUOTED":
            return 409, {"error": {"code": "RFQ_NOT_QUOTED", "message": r["status"]}}
        if self._take(self.move, r["kind"]):
            return 409, {"error": {"code": "QUOTE_MOVED", "message": "the dealer's price moved since the quote (deal_rate quoted 1.1158, now 1.1161)"}}
        kind = r["kind"]
        cid = f"SWP-{self.next_id['XCCY']:06d}" if kind == "XCCY" else f"OTC-{self.next_id['FX_FORWARD']:06d}"
        self.next_id[kind] += 1
        self.contracts.append({"contract_id": cid, "client_ref": r["terms"]["client_ref"], "kind": kind, "status": "OPEN", "terms": dict(r["terms"])})
        r.update(status="ACCEPTED", contract_id=cid)
        body = {"contract_id": cid, "contract": {"contract_id": cid, "kind": kind}, "rfq_id": r["rfq_id"], "rfq": {"closed_ts": QUOTED_TS + 20}, "entries": [1, 2]}
        if kind == "XCCY":
            body["conversion"] = {"deal_id": f"FX-{self.next_id['FX']:06d}"}
            self.next_id["FX"] += 1
        out = (200, body)
        self.commands[rid] = out
        if self._take(self.drop, kind):
            return "DROP"
        return out


def serve_fake(fake):
    """The fake on a free port of this package's range (9530-9539), else any free port on 127.0.0.1."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return None

        def _go(self, method):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            res = fake.handle(method, u.path, parse_qs(u.query), body)
            if res == "DROP":
                self.close_connection = True
                return
            st, out = res
            data = json.dumps(out).encode("utf-8")
            self.send_response(st)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

    srv = None
    for port in list(range(9530, 9540)) + [0]:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), H)
            break
        except OSError:
            continue
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, th


class RunTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTerminal()
        self.srv, self.th = serve_fake(self.fake)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.tmp = tempfile.mkdtemp(prefix="fst-otcplan-run-")
        self.plan, self.sha = otcplan.load_plan(PLAN_PATH)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.th.join(5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_plan(self, window=None):
        r = otcplan.Runner(self.plan, self.sha, otcplan.Http(self.url, timeout=10), self.tmp, window, sleep=lambda s: None, poll_s=0.0)
        return r.run()

    def ids(self, path_end):
        return [rid for m, p, rid in self.fake.seen if m == "POST" and p.endswith(path_end) and rid]

    def test_dt5_both_booked_through_the_rfq_path_and_a_rerun_books_nothing(self):
        rc, rep = self.run_plan()
        self.assertEqual(0, rc, rep)
        self.assertEqual([("A", "EXECUTED", "SWP-000007", "FX-000001"), ("B", "EXECUTED", "OTC-000001", None)],
                         [(t["key"], t["status"], t.get("contract_id"), t.get("conversion_deal_id")) for t in rep["trades"]])
        pfx = "OWNER-FX-2026-10-28:20261029T100000"
        self.assertEqual([f"{pfx}:A:1:rfq", f"{pfx}:B:1:rfq"], self.ids("/api/otc/rfqs"))
        self.assertEqual([f"{pfx}:A:1:acc", f"{pfx}:B:1:acc"], self.ids("/accept"))
        self.assertEqual("passed", rep["trades"][0]["attempts"][0]["validation"])
        # a re-run five minutes later: both found by their references, nothing is sent
        self.fake.et = "2026-10-29 10:05:00"
        n_post = len([x for x in self.fake.seen if x[0] == "POST"])
        rc, rep = self.run_plan()
        self.assertEqual((0, ["ALREADY_BOOKED", "ALREADY_BOOKED"]), (rc, [t["status"] for t in rep["trades"]]))
        self.assertEqual(n_post, len([x for x in self.fake.seen if x[0] == "POST"]))
        self.assertEqual(2, len(self.fake.contracts))

    def test_dt5_gbp_curve_missing_books_a_only(self):
        self.fake.curve_missing = {"FX_FORWARD"}
        rc, rep = self.run_plan()
        self.assertEqual(1, rc)
        a, b = rep["trades"]
        self.assertEqual(("EXECUTED", "UNEXECUTED"), (a["status"], b["status"]))
        self.assertEqual("CURVE_UNAVAILABLE: EURGBP 24M: the Bank of England OIS curve is unavailable (HTTP 403 at 09:12 ET); the forward is not priced from"
                         " any other curve", b["reason"])
        self.assertEqual(3, len(b["attempts"]))                         # max_attempts_per_run 3, then UNEXECUTED with the reason
        self.assertEqual([], [x for x in self.ids("/api/otc/rfqs") if ":B:" in x])

    def test_dt5_refused_attempt_reuses_its_request_id(self):
        self.fake.refuse = {"FX_FORWARD": 1}                            # 409 QUOTE_STALE: no row, so the same id is used again
        rc, rep = self.run_plan()
        self.assertEqual(0, rc, rep)
        pfx = "OWNER-FX-2026-10-28:20261029T100000:B"
        self.assertEqual([f"{pfx}:1:rfq", f"{pfx}:1:rfq"], [x for x in self.ids("/api/otc/rfqs") if ":B:" in x])
        self.assertEqual([f"{pfx}:1:acc"], [x for x in self.ids("/accept") if ":B:" in x])

    def test_dt5_quote_moved_is_declined_then_the_next_attempt(self):
        self.fake.move = {"XCCY": 1}
        rc, rep = self.run_plan()
        self.assertEqual(0, rc, rep)
        pfx = "OWNER-FX-2026-10-28:20261029T100000:A"
        self.assertEqual([f"{pfx}:1:rfq", f"{pfx}:2:rfq"], [x for x in self.ids("/api/otc/rfqs") if ":A:" in x])
        self.assertEqual([f"{pfx}:1:dec"], [x for x in self.ids("/decline") if ":A:" in x])
        self.assertEqual("DECLINED", self.fake.rfqs["RFQ-000001"]["status"])
        self.assertEqual(("EXECUTED", "SWP-000007"), (rep["trades"][0]["status"], rep["trades"][0]["contract_id"]))

    def test_dt5_lost_accept_response_is_replayed(self):
        self.fake.drop = {"XCCY": 1}                                    # the accept commits, then the connection drops
        rc, rep = self.run_plan()
        self.assertEqual(0, rc, rep)
        a = rep["trades"][0]
        self.assertEqual(("EXECUTED", "SWP-000007"), (a["status"], a["contract_id"]))
        self.assertIn("first response was lost", a["note"])
        pfx = "OWNER-FX-2026-10-28:20261029T100000:A:1:acc"
        self.assertEqual([pfx, pfx], [x for x in self.ids("/accept") if ":A:" in x])   # the same id re-POSTed; the bus replays
        self.assertEqual(1, len([c for c in self.fake.contracts if c["kind"] == "XCCY"]))

    def test_dt5_attempts_are_counted_per_run(self):
        self.fake.expire = {"FX_FORWARD": 3}                            # day 1: the dealer does not quote three times
        rc, rep = self.run_plan()
        self.assertEqual((1, "UNEXECUTED"), (rc, rep["trades"][1]["status"]))
        d1 = "OWNER-FX-2026-10-28:20261029T100000:B"
        self.assertEqual([f"{d1}:1:rfq", f"{d1}:2:rfq", f"{d1}:3:rfq"], [x for x in self.ids("/api/otc/rfqs") if ":B:" in x])
        self.fake.et = "2026-10-30 09:00:00"                             # day 2: a new run id, the attempts count again from 1
        rc, rep = self.run_plan()
        self.assertEqual((0, "ALREADY_BOOKED", "EXECUTED"), (rc, rep["trades"][0]["status"], rep["trades"][1]["status"]))
        self.assertIn("OWNER-FX-2026-10-28:20261030T090000:B:1:rfq", self.ids("/api/otc/rfqs"))

    def test_server_and_window_checks(self):
        self.fake.et = "2026-10-29 17:30:00"                             # after 16:30 ET: exit 3, nothing sent
        rc, rep = self.run_plan()
        self.assertEqual((3, []), (rc, [x for x in self.fake.seen if x[0] == "POST"]))
        rc, rep = self.run_plan(window="any")                            # a dry run may book outside the window; the report says so
        self.assertEqual((0, "any"), (rc, rep["window_flag"]))
        fails, _ = otcplan.server_checks(dict(self.fake.health(), version="1.0.6", modules=dict(MODULES, ccyfunding="missing")))
        self.assertIn("version 1.0.6: 1.0.6.1 or later is required", fails)
        fails, notes = otcplan.server_checks(dict(self.fake.health(), version="1.0.6"))
        self.assertEqual([], fails)
        self.assertIn("VERSION 1.0.6 is not stamped 1.0.6.1 yet", notes[0])
        fails, _ = otcplan.server_checks(dict(self.fake.health(), providers={"boe": {"ok": False, "last_error": "HTTP 403"}, "ecb": {"ok": True}, "nyfed": {"ok": True}}))
        self.assertEqual(["provider boe: HTTP 403"], fails)

    def test_a_quote_that_fails_a_check_is_declined_never_accepted(self):
        orig = self.fake.dealer

        def bad_dealer(r):
            orig(r)
            if r["kind"] == "FX_FORWARD" and r["status"] == "QUOTED":
                r["quote_preview"]["pricing_record"]["dealer"]["K"] = "0.866625"     # K off by 0.000001
        self.fake.dealer = bad_dealer
        rc, rep = self.run_plan()
        self.assertEqual(1, rc)
        b = rep["trades"][1]
        self.assertEqual("UNEXECUTED", b["status"])
        self.assertIn("K: the quote says 0.866625, re-derived 0.866624", b["reason"])
        self.assertEqual([], [x for x in self.ids("/accept") if ":B:" in x])
        self.assertEqual(["OWNER-FX-2026-10-28:20261029T100000:B:1:dec"], [x for x in self.ids("/decline") if ":B:" in x])


class QuoteFreshnessTests(unittest.TestCase):
    """V1061-01 (1.0.6.1 fix round 1): the quote's freshness on real data.  Yahoo's EURUSD=X / GBPUSD=X - the terminal's only FX source - stamp
    their prints on a 60 s grid and serve them 168-228 s after their own time (sampled 2026-10-29 04:46-04:52 ET: 24 fetches, 167.7-200.8 s;
    acceptance V saw 159-220 s), so 10.2's "the quote within 120 s of the spot" declined every real quote and both owner trades ended
    UNEXECUTED with no further attempt.  The rule now: each print at most ``PRINT_MAX_AGE_S`` = 300 s older than the quote (the feed's lag +
    the 15 s ORDER-priority fetch + the dealer job; 300 s is also the app's own fetch / staleness grace) and executable when quoted (quality
    delayed / live); the two USD legs still within 120 s of each other.  A failure of the data's timing declines the quote and asks again
    (the next attempt, as 10.2 step 2 does for QUOTE_STALE); a pricing failure still ends the trade UNEXECUTED at once.

    The fixture prints are at QT = 09:59:30 ET; a quote at QT + 195 s is 10:02:45 ET (the dry run's 195 s), at QT + 300 s 10:04:30 ET."""

    setUp, tearDown, run_plan, ids = RunTests.setUp, RunTests.tearDown, RunTests.run_plan, RunTests.ids

    def quote_at(self, ages, quality=None):
        """The dealer quotes the n-th request of each kind ``ages[n]`` seconds after the prints (the last age repeats); ``quality`` replaces
        the prints' quality on the first quote of each kind only."""
        orig = self.fake.dealer
        seen = {}

        def dealer(r):
            orig(r)
            if r["status"] != "QUOTED":
                return
            n = seen.get(r["kind"], 0)
            seen[r["kind"]] = n + 1
            age = ages[min(n, len(ages) - 1)]
            r["quote"] = {"quoted_ts": QT + age, "expires_ts": QT + age + 120}
            rec = r["quote_preview"]["pricing_record"]
            if quality and n == 0:
                for leg in (rec.get("spot") or {}).get("legs") or []:
                    leg["quality"] = quality
                if rec.get("deal_fx"):
                    rec["deal_fx"]["quality"] = quality
        self.fake.dealer = dealer

    def test_v1061_01_yahoo_lag_of_195_s_books_both(self):
        self.quote_at([195])
        rc, rep = self.run_plan()
        self.assertEqual(0, rc, rep)
        self.assertEqual([("A", "EXECUTED", "passed"), ("B", "EXECUTED", "passed")],
                         [(t["key"], t["status"], t["attempts"][0]["validation"]) for t in rep["trades"]])

    def test_v1061_01_the_300_s_boundary(self):
        # 300 s: accepted (QT 09:59:30 -> quote 10:04:30); 301 s: declined with the age said in seconds
        rfq = {"quote": {"quoted_ts": QT + 300}, "quote_preview": {"pricing_record": fwd_record(), "trade_date": "2026-10-29"}}
        t = self.plan["trades"][1]
        fails, _rec, _mine = otcplan.validate_quote(t, rfq, self.plan)
        self.assertEqual([], fails)
        rfq["quote"]["quoted_ts"] = QT + 301
        fails, _rec, _mine = otcplan.validate_quote(t, rfq, self.plan)
        self.assertEqual(["the EURUSD print is 301 s older than the quote (at most 300)", "the GBPUSD print is 301 s older than the quote (at most 300)"],
                         fails)
        self.assertTrue(otcplan.retryable(fails))

    def test_v1061_01_a_stale_quote_is_declined_and_asked_again(self):
        self.quote_at([301, 195])                                       # the first quote of each trade is 301 s after its prints, the next 195 s
        rc, rep = self.run_plan()
        self.assertEqual(0, rc, rep)
        for t, key in zip(rep["trades"], ("A", "B")):
            self.assertEqual(("EXECUTED", 2), (t["status"], len(t["attempts"])), t)
            self.assertIn("the EURUSD print is 301 s older than the quote (at most 300)", t["attempts"][0]["validation"])
            self.assertIn("declined; asked again", t["attempts"][0]["outcome"])
            self.assertEqual("passed", t["attempts"][1]["validation"])
            pfx = f"OWNER-FX-2026-10-28:20261029T100000:{key}"
            self.assertEqual([f"{pfx}:1:rfq", f"{pfx}:2:rfq"], [x for x in self.ids("/api/otc/rfqs") if f":{key}:" in x])
            self.assertEqual([f"{pfx}:1:dec"], [x for x in self.ids("/decline") if f":{key}:" in x])
            self.assertEqual([f"{pfx}:2:acc"], [x for x in self.ids("/accept") if f":{key}:" in x])

    def test_v1061_01_stale_on_every_attempt_ends_unexecuted_with_the_reason(self):
        self.quote_at([400])
        rc, rep = self.run_plan()
        self.assertEqual(1, rc)
        for t in rep["trades"]:
            self.assertEqual(("UNEXECUTED", 3), (t["status"], len(t["attempts"])), t)
            self.assertIn("quote check failed: the EURUSD print is 400 s older than the quote (at most 300)", t["reason"])
        self.assertEqual([], self.ids("/accept"))
        self.assertEqual(6, len(self.ids("/decline")))                   # every quote declined, never accepted

    def test_v1061_01_a_print_not_executable_when_quoted_is_declined(self):
        self.quote_at([30], quality="close")                            # a session close is a valuation price, never a dealing one
        rc, rep = self.run_plan()
        self.assertEqual(0, rc, rep)
        for t in rep["trades"]:
            self.assertIn("the EURUSD print was not executable when quoted (quality close)", t["attempts"][0]["validation"])
            self.assertEqual(("EXECUTED", "passed"), (t["status"], t["attempts"][1]["validation"]))

    def test_v1061_01_a_pricing_failure_is_still_final(self):
        # the timing rule's retry never covers a price that does not re-derive: K off by 0.000001 -> declined, UNEXECUTED after one attempt
        orig = self.fake.dealer

        def bad(r):
            orig(r)
            if r["kind"] == "FX_FORWARD" and r["status"] == "QUOTED":
                r["quote"] = {"quoted_ts": QT + 195, "expires_ts": QT + 315}
                r["quote_preview"]["pricing_record"]["dealer"]["K"] = "0.866625"
        self.fake.dealer = bad
        rc, rep = self.run_plan()
        b = rep["trades"][1]
        self.assertEqual((1, "UNEXECUTED", 1), (rc, b["status"], len(b["attempts"])))
        self.assertFalse(otcplan.retryable(b["attempts"][0]["validation"]))


# ---- D-T6: the checker on a fixture database ---------------------------------------------------------------------------------------------------------
PLAN_ID, RUN_ID = "OWNER-FX-2026-10-28", "20261029T100000"
PFX = f"{PLAN_ID}:{RUN_ID}"


def _book_fixture(ctx, payload):
    """What the run's accept wrote (class 1): the contract with its reference and record, its IM, the RFQ accepted, the swap's conversion."""
    from finsim.terminal import fx
    from finsim.terminal.ledger import Entry, cr, dr
    from finsim.terminal.db import dumps
    conn, aid, cid, kind = ctx.conn, "F1:PB_GS", payload["contract_id"], payload["kind"]
    terms, im_c = payload["terms"], int(payload["im_c"])
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                 " margin_mode, im_c, status, mark_c, mark_ts, vm_balance_c, client_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,0,?)",
                 (cid, ctx.command_id, aid, "GS", kind, dumps(terms), 223160000, "USD", payload["start"], payload["maturity"], "CTM", im_c, payload["mark0_c"], ctx.ts,
                  terms["client_ref"]))
    ctx.post(Entry("OTC_IM", f"OTC IM {cid}", [dr(aid, "1330", "USD", im_c, instrument_id=cid), cr(aid, "1010", "USD", im_c)], f"im:{cid}:open", ref_type="otc", ref_id=cid))
    conn.execute("UPDATE rfqs SET status='ACCEPTED', contract_id=?, accept_command_id=?, closed_ts=? WHERE rfq_id=?", (cid, ctx.command_id, ctx.ts, payload["rfq_id"]))
    out = {"contract_id": cid}
    if kind == "XCCY":
        res = fx.book_linked_spot(ctx, aid, "USD", "EUR", 200000000, "EUR", "2026-11-02", "1.1158", {"type": "xccy_start", "id": cid, "text": f"conversion of {cid}"},
                                  ("xccy_start", cid))
        out["conversion"] = res["deal"]["deal_id"]
    ctx.event("OTC_BOOKED", account_id=aid, ref_type="otc", ref_id=cid, kind=kind)
    return out


def _rfq_fixture(ctx, payload):
    from finsim.terminal.db import dumps
    ctx.conn.execute("INSERT INTO rfqs(rfq_id, command_id, account_id, broker, kind, terms_json, status, requested_ts, updated_ts) VALUES (?,?,?,?,?,?,'REQUESTED',?,?)",
                     (payload["rfq_id"], ctx.command_id, "F1:PB_GS", "GS", payload["kind"], dumps(payload["terms"]), ctx.ts, ctx.ts))
    return {"rfq_id": payload["rfq_id"]}


def _quote_fixture(ctx, payload):
    ctx.conn.execute("UPDATE rfqs SET status='QUOTED', quoted_ts=?, expires_ts=? WHERE rfq_id=?", (ctx.ts, ctx.ts + 120, payload["rfq_id"]))
    return {"rfq_id": payload["rfq_id"]}


def _pre_contract(ctx, payload):
    """A pre-existing contract of the owner (SWP-000001, a TRS-like row), booked before the snapshot."""
    ctx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                     " margin_mode, im_c, status, mark_c, vm_balance_c) VALUES ('SWP-000001',?,'F1:PB_GS','GS','TRS','{}',100000000,'USD','2026-10-20','2027-10-20','CTM',0,'OPEN',0,0)",
                     (ctx.command_id,))
    return {}


def _vm_fixture(ctx, payload):
    """SWP-000001's own VM job (class 3): mark and VM balance move, one otc_vm row with its entry (the Fund posts 100.00)."""
    from finsim.terminal.db import dumps
    from finsim.terminal.ledger import Entry, cr, dr
    eid = ctx.post(Entry("SWAP_VM", "VM SWP-000001", [dr("F1:PB_GS", "1320", "USD", 10000, instrument_id="SWP-000001"), cr("F1:PB_GS", "1010", "USD", 10000)],
                         "vm:SWP-000001:2026-10-29", ref_type="otc", ref_id="SWP-000001"))
    ctx.conn.execute("UPDATE otc_contracts SET mark_c=-10000, mark_ts=?, vm_balance_c=-10000 WHERE contract_id='SWP-000001'", (ctx.ts,))
    ctx.conn.execute("INSERT INTO otc_vm(contract_id, vm_date, mark_c, delta_c, mode, entry_id, inputs_json) VALUES ('SWP-000001','2026-10-29',-10000,-10000,'CTM',?,'{}')",
                     (eid,))
    return {"entry_id": eid}


class CheckTests(unittest.TestCase):
    """The checker on one fixture database (built once), and the 13 tamper cases, each on its own sqlite3 backup of it."""

    @classmethod
    def setUpClass(cls):
        from tests.terminal_helpers import buy, execute, make_runtime
        from finsim.terminal import fx, prime
        from finsim.terminal.products import swaps
        cls.tmp = tempfile.mkdtemp(prefix="fst-otcplan-chk-")
        rt = make_runtime(os.path.join(cls.tmp, "home"), at="2026-10-29T09:30:00-04:00", books="real",
                          marks={"EURUSD": ("1.1158", "delayed"), "GBPUSD": ("1.3323", "delayed")})
        prime.register(rt.bus)
        prime.bind(rt)
        fx.register(rt.bus)
        fx.bind(rt)
        rt.fx = fx
        swaps.ensure_schema(rt.db)
        for name, fn in (("test.pre_contract", _pre_contract), ("test.vm_fixture", _vm_fixture), ("test.book_fixture", _book_fixture),
                         ("test.rfq_fixture", _rfq_fixture), ("test.quote_fixture", _quote_fixture)):
            rt.bus.register(name, fn)
        assert execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"}).ok
        assert execute(rt, "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": "F1:PB_GS", "currency": "USD", "amount_c": 50000000}).ok
        assert execute(rt, "test.pre_contract", {}, "ui:pre-contract-1").ok
        with rt.db.writer() as tx:                                 # the stored inputs of the records' as-of dates (C4)
            rows = [("IUDSOIA", "2026-10-26", "ON", "3.7328"), ("ESTR", "2026-10-27", "ON", "2.440"), ("SOFR", "2026-10-27", "ON", "3.95"),
                    ("UST_PAR", "2026-10-27", "6M", "4.34")] + [("BOE_OIS_SPOT", "2026-10-27", f"{m}M", z) for m, z in BOE.items()] + \
                   [("ECB_AAA_SPOT", "2026-10-27", ten, z) for ten, _t, z in ECB]
            tx.conn.executemany("INSERT INTO curve_points(series, as_of, tenor, value, fetched_ts, source) VALUES (?,?,?,?,0,?)", [r + (SRC,) for r in rows])
        cls.db_path = rt.db_path
        cls.before = otcplan.snapshot(cls.db_path)
        rt.clock.set("2026-10-29T10:00:00-04:00")
        a_terms = dict(load_json(PLAN_PATH)["trades"][0]["terms"])
        b_terms = dict(load_json(PLAN_PATH)["trades"][1]["terms"])
        for key, kind, terms, rfq in (("A", "XCCY", a_terms, "RFQ-000001"), ("B", "FX_FORWARD", b_terms, "RFQ-000002")):
            assert execute(rt, "test.rfq_fixture", {"rfq_id": rfq, "kind": kind, "terms": terms}, f"{PFX}:{key}:1:rfq").ok
            assert execute(rt, "test.quote_fixture", {"rfq_id": rfq}, f"job:RFQ_DEALER:{rfq}:QUOTE", actor="lifecycle").ok
        a = execute(rt, "test.book_fixture", {"contract_id": "SWP-000007", "kind": "XCCY", "rfq_id": "RFQ-000001", "im_c": 4463200, "mark0_c": -44782,
                                              "start": "2026-11-02", "maturity": "2028-11-06",
                                              "terms": dict(a_terms, variant="FLOAT_FLOAT", deal_rate="1.1158", pay_spread_bp="0.1",
                                                            legs={"pay": {"currency": "EUR", "notional_c": 200000000},
                                                                  "receive": {"currency": "USD", "notional_c": 223160000}},
                                                            start_date="2026-11-02", pricing_record=ccs_record())}, f"{PFX}:A:1:acc")
        assert a.ok, a.body
        b = execute(rt, "test.book_fixture", {"contract_id": "OTC-000001", "kind": "FX_FORWARD", "rfq_id": "RFQ-000002", "im_c": 13389600, "mark0_c": -41925,
                                              "start": "2026-10-29", "maturity": "2028-11-02",
                                              "terms": dict(b_terms, buy_ccy="GBP", sell_ccy="EUR", buy_c=173324800, sell_c=200000000, strike="0.866624",
                                                            value_date="2028-11-02", pricing_record=fwd_record())}, f"{PFX}:B:1:acc")
        assert b.ok, b.body
        cls.conversion = a.body["conversion"]
        # SWP-000001's own VM job (class 3) and the owner's activity (class 4): a UI transfer and a fill of his order
        assert execute(rt, "test.vm_fixture", {}, "job:SWAP_VM:SWP-000001:2026-10-29", actor="lifecycle").ok
        assert execute(rt, "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": "F1:US_EXCHANGE", "currency": "USD", "amount_c": 100000}).ok
        assert buy(rt, "F1:US_EXCHANGE", "AAPL", 10, "339.50").ok
        rt.close()
        plan = load_json(PLAN_PATH)
        # the Risk FX block the run captured: EUR nominal -4,000,000.00 (the conversion's 2350 payable + the forward); PV delta = the ledger's
        # -2,000,000.00 + the swap's +620.04 + the forward's -1,882,614.77 = -3,881,994.73; GBP +1,733,248.00 nominal, +1,576,373.42 PV delta
        cls.run_report = {"plan_id": PLAN_ID, "run_id": RUN_ID, "plan": plan,
                   "trades": [{"key": "A", "client_ref": "OWNER-FX-2026-10-28-A", "status": "EXECUTED", "contract_id": "SWP-000007"},
                              {"key": "B", "client_ref": "OWNER-FX-2026-10-28-B", "status": "EXECUTED", "contract_id": "OTC-000001"}],
                   "views": {"risk": {"fx_sensitivities": {"by_currency": [{"ccy": "EUR", "nominal": "-4000000.00", "pv_delta": "-3881994.73"},
                                                                          {"ccy": "GBP", "nominal": "1733248.00", "pv_delta": "1576373.42"}]}}}}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy_db(self, tamper=None):
        dst = os.path.join(self.tmp, f"copy-{self._testMethodName}.db")
        src = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
            if tamper:
                tamper(out)
                out.commit()
        finally:
            out.close()
            src.close()
        return dst

    def check(self, tamper=None, run=None, second=None):
        path = self.copy_db(tamper)
        c = otcplan.Checker(path, self.before, run or self.run_report, second)
        try:
            return c.result()
        finally:
            c.close()

    def failing(self, res):
        return sorted(k for k, v in res["items"].items() if not v["ok"])

    @staticmethod
    def edit_terms(conn, cid, fn):
        t = json.loads(conn.execute("SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()[0])
        fn(t)
        conn.execute("UPDATE otc_contracts SET terms_json=? WHERE contract_id=?", (json.dumps(t), cid))

    def test_dt6_passes_and_lists_other_activity(self):
        res = self.check()
        self.assertTrue(res["ok"], json.dumps(res, indent=1, default=str)[:3000])
        c1 = res["items"]["C1"]["evidence"]
        self.assertTrue(any("treasury.transfer" in x or "ui:" in x for x in c1["other_activity"]), c1["other_activity"])
        self.assertTrue(any("SWP-000001" in x for x in c1["notes"]), c1)
        self.assertEqual("1", "1" if c1["classes"]["3"] >= 1 else "0")
        self.assertEqual(["OWNER-FX-2026-10-28-A -> SWP-000007", "OWNER-FX-2026-10-28-B -> OTC-000001"], sorted(x for x in res["items"]["C2"]["evidence"]["found"] if "->" in x))

    def test_dt6_tamper_01_pre_existing_contract_edited(self):
        res = self.check(lambda c: c.execute("UPDATE otc_contracts SET notional_c=notional_c+1 WHERE contract_id='SWP-000001'"))
        self.assertEqual(["C1"], self.failing(res))
        self.assertIn("SWP-000001: columns notional_c changed", " ".join(res["items"]["C1"]["evidence"]["problems"]))

    def test_dt6_tamper_02_old_entry_altered(self):
        res = self.check(lambda c: c.execute("UPDATE journal_lines SET memo='altered' WHERE line_id=(SELECT MIN(line_id) FROM journal_lines)"))
        self.assertEqual(["C1"], self.failing(res))
        self.assertIn("journal entries altered after the snapshot", " ".join(res["items"]["C1"]["evidence"]["problems"]))

    def test_dt6_tamper_03_negative_cash_in_a_scoped_account(self):
        res = self.check(lambda c: c.execute("UPDATE gl_balances SET balance_c=-1 WHERE account_id='F1:TREASURY' AND gl='1010' AND currency='USD'"))
        self.assertIn("C5", self.failing(res))
        self.assertIn("F1:TREASURY settled USD is -0.01", res["items"]["C5"]["evidence"]["problems"])

    def test_dt6_tamper_04_strike_changed_by_one_millionth(self):
        res = self.check(lambda c: self.edit_terms(c, "OTC-000001", lambda t: t["pricing_record"]["dealer"].update(K="0.866625")))
        self.assertEqual(["C3"], self.failing(res))
        self.assertIn("OTC-000001: K: the quote says 0.866625, re-derived 0.866624", res["items"]["C3"]["evidence"]["problems"])

    def test_dt6_tamper_05_curve_node_changed(self):
        def bump(t):
            n = next(x for x in t["pricing_record"]["curves"]["GBP"]["nodes"] if x["tenor"] == "24M")
            n["z"] = "4.727448"
        res = self.check(lambda c: self.edit_terms(c, "OTC-000001", bump))
        # C3 (the record's own DFs no longer follow), C4 (the node differs from the stored row) and C7 (the re-derived PV delta moves)
        self.assertEqual(["C3", "C4", "C7"], self.failing(res))
        self.assertIn("curve GBP BOE_OIS_SPOT 24M: stored 4.727447, the record 4.727448", " ".join(res["items"]["C4"]["evidence"]["problems"]))

    def test_dt6_tamper_06_conversion_missing(self):
        def drop(c):
            c.execute("DELETE FROM settlements WHERE fx_deal_id=?", (self.conversion,))
            c.execute("DELETE FROM fx_deals WHERE deal_id=?", (self.conversion,))
        res = self.check(drop)
        self.assertIn("C2", self.failing(res))
        self.assertIn("SWP-000007: 0 inception conversions linked (exactly one expected)", res["items"]["C2"]["evidence"]["problems"])

    def test_dt6_tamper_07_second_contract_with_the_same_ref(self):
        def dup(c):
            c.execute("DROP INDEX IF EXISTS ux_otc_client_ref")
            cols = [r[1] for r in c.execute("PRAGMA table_info(otc_contracts)").fetchall() if r[1] != "contract_id"]
            c.execute(f"INSERT INTO otc_contracts(contract_id, {', '.join(cols)}) SELECT 'OTC-000099', {', '.join(cols)} FROM otc_contracts WHERE contract_id='OTC-000001'")
        res = self.check(dup)
        self.assertIn("C2", self.failing(res))
        self.assertIn("OWNER-FX-2026-10-28-B: 2 contracts carry it (exactly one expected)", res["items"]["C2"]["evidence"]["problems"])

    def test_dt6_tamper_08_missing_label(self):
        res = self.check(lambda c: self.edit_terms(c, "SWP-000007", lambda t: t["pricing_record"].update(labels=[])))
        self.assertEqual(["C4"], self.failing(res))
        self.assertIn("SWP-000007: no labels", res["items"]["C4"]["evidence"]["problems"])

    def test_dt6_tamper_09_missing_source_time(self):
        res = self.check(lambda c: self.edit_terms(c, "OTC-000001", lambda t: t["pricing_record"]["spot"]["legs"][0].update(quote_time=None)))
        self.assertEqual(["C4"], self.failing(res))
        self.assertIn("OTC-000001: spot leg EURUSD: no quote time", res["items"]["C4"]["evidence"]["problems"])

    def test_dt6_tamper_10_unbalanced_trial_balance(self):
        res = self.check(lambda c: c.execute("UPDATE journal_lines SET amount_c=amount_c+1 WHERE line_id=(SELECT MAX(line_id) FROM journal_lines)"))
        self.assertIn("C6", self.failing(res))
        self.assertTrue(any("does not balance" in p for p in res["items"]["C6"]["evidence"]["problems"]))

    def test_dt6_tamper_11_im_differs_from_the_schedule(self):
        res = self.check(lambda c: self.edit_terms(c, "OTC-000001", lambda t: t["pricing_record"]["im"].update(im_c=13389601)))
        self.assertEqual(["C3"], self.failing(res))
        self.assertIn("OTC-000001: IM: the quote says 13389601, re-derived 13389600 (cents)", res["items"]["C3"]["evidence"]["problems"])

    def test_dt6_tamper_11b_record_terms_differ_from_the_gs_schedule(self):
        """C4: the record says forward IM 5 % while the Goldman Sachs schedule in force says 6 % (the amounts re-derive from the record,
        so only the comparison with the stored schedule catches it)."""
        def five(t):
            t["pricing_record"]["im"].update(pct="0.05", im_c=11158000)          # 2,231,600.00 x 5 % = 111,580.00
        res = self.check(lambda c: self.edit_terms(c, "OTC-000001", five))
        self.assertEqual(["C4"], self.failing(res))
        self.assertIn("OTC-000001: the forward IM percentage 0.05 differs from the Goldman Sachs schedule's forward_im_pct 0.06", res["items"]["C4"]["evidence"]["problems"])

    def test_dt6_tamper_12_pv_delta_off_by_two_cents(self):
        run = copy.deepcopy(self.run_report)
        run["views"]["risk"]["fx_sensitivities"]["by_currency"][0]["pv_delta"] = "-3881994.75"
        res = self.check(run=run)
        self.assertEqual(["C7"], self.failing(res))
        self.assertIn("EUR PV delta -388199475 (cents) differs from the ledger -200000000 + the re-derived off-ledger deltas -188199473 = -388199473",
                      " ".join(res["items"]["C7"]["evidence"]["problems"]))

    def test_dt6_tamper_13_run_command_edits_the_gs_schedule(self):
        def sched(c):
            cmd = c.execute("SELECT id FROM commands WHERE request_id=?", (f"{PFX}:B:1:acc",)).fetchone()[0]
            c.execute("INSERT INTO events(command_id, ts, type, account_id, ref_type, ref_id, payload_json) VALUES (?,?,?,?,?,?,?)",
                      (cmd, QUOTED_TS, "SETTINGS_CHANGED", "F1:PB_GS", "pb_schedule", "F1:PB_GS", json.dumps({"before": {"forward_hw_bp": 2.0}, "after": {"forward_hw_bp": 0.5}})))
        res = self.check(sched)
        self.assertEqual(["C11", "C2"], self.failing(res))
        self.assertIn("the run changed settings", " ".join(res["items"]["C11"]["evidence"]["problems"]))

    def test_v1061_02_c7_is_not_applicable_when_the_run_booked_nothing(self):
        """V1061-02: a run whose trades all ended UNEXECUTED leaves no new FX contract, and the Risk block legitimately has no currency rows
        (``by_currency`` []): C7 passes as not applicable, saying so - it failed with "the run report carries no Risk FX block"."""
        run = copy.deepcopy(self.run_report)
        for t in run["trades"]:
            t.update(status="UNEXECUTED", contract_id=None, reason="quote check failed: the EURUSD print is 400 s older than the quote (at most 300)")
        run["views"]["risk"] = {"fx_sensitivities": {"by_currency": []}}
        # the database of such a run: no contract carries a plan reference (the fixture's two bookings unreferenced)
        res = self.check(lambda c: c.execute("UPDATE otc_contracts SET client_ref=NULL WHERE client_ref LIKE 'OWNER-FX-%'"), run=run)
        c7 = res["items"]["C7"]
        self.assertTrue(c7["ok"], c7)
        self.assertEqual("not applicable: the run booked no FX contract (no open contract carries a plan reference)", c7["evidence"]["note"])
        # a Risk view the run could not read changes nothing when nothing was booked
        run["views"]["risk"] = {"unavailable": "HTTP 503: BUSY"}
        res = self.check(lambda c: c.execute("UPDATE otc_contracts SET client_ref=NULL WHERE client_ref LIKE 'OWNER-FX-%'"), run=run)
        self.assertTrue(res["items"]["C7"]["ok"], res["items"]["C7"])

    def test_v1061_02_c7_still_fails_when_a_booked_contract_has_no_risk_block(self):
        run = copy.deepcopy(self.run_report)
        run["views"]["risk"] = {"fx_sensitivities": {"by_currency": []}}
        res = self.check(run=run)
        self.assertEqual(["C7"], self.failing(res))
        self.assertEqual(["the run report carries no Risk FX block (fx_sensitivities.by_currency) although the run booked OTC-000001, SWP-000007"],
                         res["items"]["C7"]["evidence"]["problems"])

    def test_v1061_02_a_missing_input_file_is_one_line_never_a_traceback(self):
        """V1061-02: ``check`` (and ``run``, ``snapshot``, ``reprice``) answer a missing or unreadable input file with one line and exit 2."""
        import contextlib
        import io
        before = os.path.join(self.tmp, "v1061-before.json")
        runp = os.path.join(self.tmp, "v1061-run.json")
        for path, body in ((before, self.before), (runp, self.run_report)):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(body, f, default=str)
        missing = os.path.join(self.tmp, "no-such-views.json")
        cases = [(["check", "--db", self.db_path, "--before", before, "--run", runp, "--after-restart", missing], "--after-restart"),
                 (["check", "--db", self.db_path, "--before", missing, "--run", runp], "--before"),
                 (["check", "--db", os.path.join(self.tmp, "no-such.db"), "--before", before, "--run", runp], "--db"),
                 (["snapshot", "--db", os.path.join(self.tmp, "no-such.db"), "--out", os.path.join(self.tmp, "snap.json")], "--db"),
                 (["run", missing, "--base-url", "http://127.0.0.1:9", "--report", os.path.join(self.tmp, "rep")], "plan"),
                 (["reprice", missing], "record")]
        for argv, what in cases:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = otcplan.main(argv)
            text = out.getvalue() + err.getvalue()
            self.assertEqual(2, rc, (argv, text))
            self.assertNotIn("Traceback", text)
            self.assertEqual(1, len(text.strip().splitlines()), text)
            self.assertIn(what, text)
        with open(os.path.join(self.tmp, "not-json.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = otcplan.main(["check", "--db", self.db_path, "--before", before, "--run", os.path.join(self.tmp, "not-json.json")])
        self.assertEqual((2, 1), (rc, len(out.getvalue().strip().splitlines())), out.getvalue())
        self.assertIn("--run", out.getvalue())

    def test_c8_second_run_books_nothing(self):
        second = {"plan_id": PLAN_ID, "run_id": "20261029T100500", "trades": [{"key": "A", "status": "ALREADY_BOOKED"}, {"key": "B", "status": "ALREADY_BOOKED"}]}
        res = self.check(second=second)
        self.assertTrue(res["items"]["C8"]["ok"], res["items"]["C8"])
        res = self.check(second=dict(second, trades=[{"key": "A", "status": "EXECUTED"}, {"key": "B", "status": "ALREADY_BOOKED"}]))
        self.assertEqual(["C8"], self.failing(res))
        res = self.check(second=dict(second, trades=[], exit_code=2))           # a second run that never reached the trades proves nothing
        self.assertEqual(["C8"], self.failing(res))


# ---- D-T9: the forward-check harness on the fixture world ---------------------------------------------------------------------------------------
class ForwardCheckTests(unittest.TestCase):
    """The harness (tests/terminal_fx_forward_check.py) drives a control copy (a pre-existing IRS, and a pre-existing short ORCL TRS in the
    input tests) and a run copy (the same + both trades booked by the real ``otc.book``) from the booking (Thu 2026-10-29 10:01 ET) to S + 1
    08:00 ET (Tue 2026-11-03), inputs held flat: the START PvP and the conversion settle on Mon 11-02 at 00:45, both legs accrue night 11-02 on
    11-03 at 00:30, the first VM posts at 17:05 on 10-29, and the pre-existing contracts' job results and rows are the same on both copies (an
    empty control diff).  Fixer round 2: one set of inputs for both copies (V1061R1-01), the held curves re-dated on business days only
    (V1061R1-03), and the START's net per currency, funding and cash shown and checked (V1061R1-04)."""

    def _world(self, tmp, trs=False, book=True, run_edit=None, orcl_run=None):
        """The fixture world: ``c0`` (the control: the pre-existing IRS [and TRS]) and ``c1`` (the run: both trades booked at 10:00 ET when
        ``book``; ``run_edit(ex)`` runs extra commands on it; ``orcl_run`` re-prices ORCL in the run copy's quote cache)."""
        from finsim.terminal import config, fxcurves, server
        from finsim.terminal.clock import FakeClock
        from finsim.terminal.commands import Command
        from finsim.terminal.quotes import QuoteRecord
        from finsim.terminal.quotes.providers import FakeProvider
        from tests import terminal_fx_forward_check as H
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        db = os.path.join(home, "terminal.db")
        os.environ[config.HOME_ENV], os.environ[config.DB_ENV] = home, db
        clock = FakeClock("2026-10-29T09:30:00-04:00")
        rt = server.build_runtime(home, db, clock=clock, offline=True, fake_provider=FakeProvider(clock), log=H._Log(), books=[config.BOOKS[1]], fetch_threads=0)
        try:
            fxcurves.fixture_curves(rt, persist=True)

            def prints(orcl="144.54"):
                now = int(clock.epoch())
                for iid, px, grp in (("EURUSD", "1.1158", "FX_SPOT"), ("GBPUSD", "1.3323", "FX_SPOT"), ("ORCL", orcl, "US_EQUITY")):
                    rt.cache.put(QuoteRecord(iid, "yahoo", "test print", "direct", last=D(px), quote_time=now, fetched_at=now, session_group=grp), at=now)
                rt.cache.persist()                     # what the live quote worker does: the copies reload these from quote_cache

            def ex(kind, payload, rid):
                r = rt.bus.execute(Command(kind, payload, rid, "ui"))
                self.assertTrue(r.ok, r.body)
                return r.body
            prints()
            ex("account.open_pb", {"lifetime_id": "F1", "broker": "GS"}, "ui:fxfwd-open-pb")
            irs = {"notional": "10000000.00", "currency": "USD", "pay_fixed": True, "tenor_months": 24, "fixed_rate": None, "fixed_basis": "30/360",
                   "fixed_months": 6, "float_index": "SOFR", "float_basis": "ACT/360", "float_months": 6, "fixing": "TERM"}
            ex("otc.book", {"account_id": "F1:PB_GS", "kind": "IRS", "terms": irs}, "ui:fxfwd-pre-irs")
            if trs:
                ex("otc.book", {"account_id": "F1:PB_GS", "kind": "TRS", "terms": {"underlying_id": "ORCL", "units": 1000, "receiver": False, "reset_months": 1,
                                                                                 "tenor_months": 12}}, "ui:fxfwd-pre-trs")
            c0 = os.path.join(tmp, "c0.db")
            H.backup(db, c0)
            clock.set("2026-10-29T10:00:00-04:00")
            prints(orcl_run or "144.54")
            if book:
                plan = load_json(PLAN_PATH)
                for t in plan["trades"]:
                    ex("otc.book", {"account_id": "F1:PB_GS", "kind": t["kind"], "terms": dict(t["terms"])}, f"{PFX}:{t['key']}:1:acc")
            if run_edit is not None:
                run_edit(ex)
            c1 = os.path.join(tmp, "c1.db")
            H.backup(db, c1)
        finally:
            rt.close()
        return c0, c1

    def test_dt9_runs_to_s_plus_1_and_the_control_diff_is_empty(self):
        from finsim.terminal.clock import epoch_of
        from tests import terminal_fx_forward_check as H
        tmp = tempfile.mkdtemp(prefix="fst-fxfwd-")
        try:
            c0, c1 = self._world(tmp)
            rep = H.run_forward_check(c1, c0, work=os.path.join(tmp, "work"), until=epoch_of(date(2026, 11, 3), "08:00"), out=lambda *a: None)
            txt = H.report_text(rep)
            self.assertEqual({}, rep["control_diff"], txt)
            self.assertEqual({"control": [], "run": []}, rep["job_failures"], txt)
            self.assertTrue(all(v["ok"] for v in rep["checks"].values()), txt)
            self.assertTrue(rep["ok"], txt)
            swap = next(c for c in rep["new_contracts"] if c.startswith("SWP"))
            fwd = next(c for c in rep["new_contracts"] if c.startswith("OTC"))
            self.assertIn(("2026-11-02", "NOTIONAL_START", 223160000), [tuple(x) for x in rep["events"][swap]["resets"]])
            # V1061R1-04 (FX_CCS_PLAN 9 item 5, "net USD -40.00 class"): the START's settled cash in F1:PB_GS, the funding sweeps apart -
            #   USD: + 2,231,560.00 (the conversion SELL EUR 2,000,000.00 at 1.1158 - 0.00002 = 1.11578) - 2,231,600.00 (lent: 2,000,000 x 1.1158)
            #        = -40.00 = the quote's net start flow (2,000,000 x 0.00002 = 40.00, the desk spread);
            #   EUR: + 2,000,000.00 (borrowed in the exchange) - 2,000,000.00 (delivered to the conversion) = 0.00
            st = rep["events"][swap]["start"]
            self.assertEqual({"USD": {"in_c": 223156000, "out_c": -223160000, "net_c": -4000}, "EUR": {"in_c": 200000000, "out_c": -200000000, "net_c": 0}},
                             st["pvp"], txt)
            self.assertEqual({"USD": -4000, "EUR": 0}, st["net_quoted"], txt)
            self.assertEqual({"USD": -4000, "EUR": 0}, st["net_from_rows"], txt)
            self.assertEqual(f"job:XCCY_EVENTS:{swap}:2026-11-02:START", st["command"])
            self.assertEqual(223160000, st["funding"]["need_c"])                  # the USD lent; the conversion's USD settled into 1010 first
            self.assertIn(st["funding"]["source"], ("own", "treasury_sweep", "sweep"), txt)
            swept = sum(x["amount_c"] for x in st["sweeps"] if x["kind"] == "SWEEP_IN")
            self.assertEqual(int(st["funding"]["sweep_c"] or 0), swept, txt)
            self.assertGreaterEqual(st["cash_after"].get("USD", 0), 0, txt)
            self.assertEqual(0, st["cash_after"].get("EUR", 0), txt)
            self.assertTrue(all(v >= 0 for v in st["treasury_after"].values()), txt)
            for name in (f"{swap} START net per currency (the quoted net start flows)", f"{swap} START funding source",
                         f"{swap} cash at or above zero after the START and at the end"):
                self.assertTrue(rep["checks"][name]["ok"], txt)
            self.assertIn("= net -40.00 (quoted net start flow -40.00", txt)
            self.assertIn("= net 0.00 (quoted net start flow 0.00", txt)
            # V1061R1-03: the held prints / grids / par curve are re-dated on business days only, at their first tick from 08:00 ET (the booking
            # tick 10:01 on Thu 10-29, then Fri 10-30, Mon 11-02, Tue 11-03): nothing on Sat 10-31 or Sun 11-01, so no weekend VM posts
            self.assertEqual([("2026-10-29 10:01 ET", "2026-10-28"), ("2026-10-30 08:00 ET", "2026-10-29"), ("2026-11-02 08:00 ET", "2026-10-30"),
                              ("2026-11-03 08:00 ET", "2026-11-02")], [(h["on"], h["dated"]) for h in rep["held_inputs"]], txt)
            for cid in (swap, fwd):
                self.assertEqual([], [v for v in rep["events"][cid]["vm"] if v[0] in ("2026-10-31", "2026-11-01")], txt)
            self.assertEqual(["2026-10-29", "2026-10-30", "2026-11-02"], [v[0] for v in rep["events"][fwd]["vm"]], txt)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_v1061r1_01_input_differences_alone_never_fail_the_check(self):
        """V's sensitivity case: nothing booked, the run copy's ORCL quote 145.54 against the control's 144.54 (a short TRS of 1,000 ORCL is
        pre-existing).  Driven from their OWN inputs the copies part at the 10-29 close: the run copy's TRS posts VM -1,000 x (145.54 - 144.54)
        = -1,000.00 that the control does not - a DIFF that is not an effect of any booking.  From ONE set of inputs (the run copy's) both
        post the same VM: the control diff is empty and the report lists the quote that was aligned."""
        from finsim.terminal.clock import epoch_of
        from tests import terminal_fx_forward_check as H
        tmp = tempfile.mkdtemp(prefix="fst-fxfwd-in-")
        try:
            c0, c1 = self._world(tmp, trs=True, book=False, orcl_run="145.54")
            kw = dict(start=epoch_of(date(2026, 10, 29), "10:01"), until=epoch_of(date(2026, 10, 30), "08:00"), out=lambda *a: None)
            own = H.run_forward_check(c1, c0, work=os.path.join(tmp, "w0"), align_inputs=False, **kw)
            self.assertFalse(own["ok"])
            trs = next(cid for cid, d in own["control_diff"].items())
            self.assertIn("vm", own["control_diff"][trs]["differs_in"], H.report_text(own))
            # the run copy's VM of 10-29: -1,000 units x (145.54 - 144.54) = -1,000.00 against the control's NO_CHANGE
            self.assertEqual([("2026-10-29", -100000)], [(v[0], v[2]) for v in own["control_diff"][trs]["run"]["vm"] if v[0] == "2026-10-29"])
            rep = H.run_forward_check(c1, c0, work=os.path.join(tmp, "w1"), **kw)
            txt = H.report_text(rep)
            self.assertEqual({}, rep["control_diff"], txt)
            self.assertTrue(rep["ok"], txt)
            aligned = rep["inputs_before_alignment"]["quote_cache"]
            self.assertIn({"key": ["ORCL"], "control": "144.54", "run": "145.54"}, aligned["differ_examples"], txt)
            self.assertIn("Inputs quote_cache aligned to the run copy", txt)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_v1061r1_01_a_change_to_a_pre_existing_contract_is_still_caught(self):
        """The alignment copies inputs only: a run that touches a pre-existing contract (here: it terminates the pre-existing IRS) still fails."""
        from finsim.terminal.clock import epoch_of
        from tests import terminal_fx_forward_check as H
        tmp = tempfile.mkdtemp(prefix="fst-fxfwd-hit-")
        try:
            c0, c1 = self._world(tmp, book=False, run_edit=lambda ex: ex("otc.terminate", {"contract_id": "SWP-000001"}, "ui:fxfwd-terminate-irs"))
            rep = H.run_forward_check(c1, c0, work=os.path.join(tmp, "w"), start=epoch_of(date(2026, 10, 29), "10:01"), until=epoch_of(date(2026, 10, 30), "08:00"),
                                      out=lambda *a: None)
            self.assertFalse(rep["ok"])
            self.assertIn("SWP-000001", rep["control_diff"], H.report_text(rep))
            self.assertIn("row", rep["control_diff"]["SWP-000001"]["differs_in"])
            self.assertEqual(("OPEN", "TERMINATED"), (rep["control_diff"]["SWP-000001"]["control"]["row"]["status"],
                                                     rep["control_diff"]["SWP-000001"]["run"]["row"]["status"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_v1061r1_04_the_start_checks_on_a_swept_start_and_their_failures(self):
        """Pure (``checks_of``): a START funded by a Treasury sweep of the 40.00 shortfall passes; each check fails on its own defect - a net
        that is not the quoted one, a sweep that does not match the funding record, cash below zero after the START; a START without an
        inception conversion (the legacy exchange) is checked against its rows only."""
        import copy as _copy
        from tests import terminal_fx_forward_check as H
        by = "job:XCCY_EVENTS:SWP-000007:2026-11-02:START"
        start = {"command": by, "finished_ts": 1793598300, "account": "F1:PB_GS", "treasury": "F1:TREASURY", "conversion_deal": "D-000001", "conversion_status": "SETTLED",
                 # USD: + 2,231,560.00 (the conversion) - 2,231,600.00 (lent) = -40.00; EUR: + 2,000,000.00 - 2,000,000.00 = 0.00
                 "pvp": {"USD": {"in_c": 223156000, "out_c": -223160000, "net_c": -4000}, "EUR": {"in_c": 200000000, "out_c": -200000000, "net_c": 0}},
                 "net_from_rows": {"USD": -4000, "EUR": 0}, "net_quoted": {"USD": -4000, "EUR": 0},
                 # no own cash beyond the conversion's 2,231,560.00: the shortfall 2,231,600.00 - 2,231,560.00 = 40.00 is swept from Treasury
                 "funding": {"source": "sweep", "need_c": 223160000, "own_c": 223156000, "shortfall_c": 4000, "sweep_c": 4000, "pb_draw_c": 0, "funded": True, "unfunded_c": 0},
                 "sweeps": [{"from": "F1:TREASURY", "to": "F1:PB_GS", "currency": "USD", "amount_c": 4000, "kind": "SWEEP_IN"}],
                 "cash_after": {"EUR": 0, "USD": 0}, "treasury_after": {"USD": 192538248}}
        ev = {"SWP-000007": {"kind": "XCCY", "status": "OPEN", "account": "F1:PB_GS", "resets": [("2026-11-02", "NOTIONAL_START", 223160000)],
                             "vm": [("2026-10-29", -44782, -44782)], "accruals": [("ACCRUAL", "2026-11-03", "receive"), ("ACCRUAL", "2026-11-03", "pay")], "attention": [],
                             "deals": [{"deal_id": "D-000001", "kind": "SPOT", "status": "SETTLED", "link": "xccy_start",
                                        "settlements": [{"leg": "BUY_CCY", "settled_by": by}, {"leg": "SELL_CCY", "settled_by": by}]}],
                             "start": start, "cash_end": {"account": {"EUR": 0, "USD": 0}, "treasury": {"USD": 192538248}}}}
        names = ("SWP-000007 START net per currency (the quoted net start flows)", "SWP-000007 START funding source",
                 "SWP-000007 cash at or above zero after the START and at the end")

        def run(mut):
            e = _copy.deepcopy(ev)
            mut(e["SWP-000007"])
            got = H.checks_of(e)
            return tuple(got[n]["ok"] for n in names)
        self.assertEqual((True, True, True), run(lambda e: None))
        self.assertTrue(all(v["ok"] for v in H.checks_of(_copy.deepcopy(ev)).values()))

        def net_off(e):                                   # one cent more than the quote promised
            e["start"]["pvp"]["USD"]["net_c"] = -4001
        self.assertEqual((False, True, True), run(net_off))

        def sweep_off(e):                                  # the transfer does not match the funding record
            e["start"]["sweeps"][0]["amount_c"] = 3999
        self.assertEqual((True, False, True), run(sweep_off))

        def overdrawn(e):                                  # the PB overdrawn by a cent after the START
            e["start"]["cash_after"]["USD"] = -1
        self.assertEqual((True, True, False), run(overdrawn))

        def treasury_negative_at_end(e):
            e["cash_end"]["treasury"]["USD"] = -5
        self.assertEqual((True, True, False), run(treasury_negative_at_end))

        def legacy(e):                                     # no inception conversion: the exchange alone, checked against its rows
            st = e["start"]
            st.update(conversion_deal=None, net_quoted={}, pvp={"USD": {"in_c": 0, "out_c": -223160000, "net_c": -223160000},
                                                                 "EUR": {"in_c": 200000000, "out_c": 0, "net_c": 200000000}},
                      net_from_rows={"USD": -223160000, "EUR": 200000000})
        self.assertEqual((True, True, True), run(legacy))

        def conversion_lost(e):                            # a conversion is linked but its legs never settled: the START nets a notional
            e["start"]["pvp"] = {"USD": {"in_c": 0, "out_c": -223160000, "net_c": -223160000}, "EUR": {"in_c": 200000000, "out_c": 0, "net_c": 200000000}}
        self.assertEqual((False, True, True), run(conversion_lost))

    def test_v1061r1_03_the_held_inputs_follow_the_business_calendar(self):
        """Pure: on Thu 2026-11-26 (Thanksgiving, an NYSE holiday), Sat 10-31 and Sun 11-01 nothing is re-dated; Mon 11-02 from 08:00 re-dates
        Friday 10-30; Fri 11-27 re-dates Wed 11-25 (the holiday skipped); before 08:00 nothing runs."""
        from finsim.terminal.clock import epoch_of
        from tests import terminal_fx_forward_check as H

        class _Rates:
            """SOFR a daily print (last 10-29); the OECD CHF rate a monthly one (2024-03-01: never re-dated, it is not published daily)."""
            def __init__(self):
                self.last = {"SOFR": date(2026, 10, 29), "IRSTCI01CHM156N": date(2024, 3, 1)}
                self._obs, self._grids, self.put_calls = dict.fromkeys(self.last), {}, []

            def latest(self, series):
                return (self.last[series], D("3.95"))

            def put(self, series, obs, source):
                self.put_calls.append((series, obs[0][0]))
                self.last[series] = obs[0][0]
        from finsim.terminal.calendar import TradingCalendar
        from finsim.terminal import config
        cp = H.Copy.__new__(H.Copy)
        cp.rt = type("RT", (), {"cal": TradingCalendar(config.holidays_path()), "rates": _Rates(), "curve": None})()
        cp.inputs_day, cp.held = None, []
        for d, hm in ((date(2026, 10, 31), "09:00"), (date(2026, 11, 1), "09:00"), (date(2026, 11, 2), "07:00"), (date(2026, 11, 26), "09:00")):
            self.assertIsNone(cp.hold_rates(epoch_of(d, hm)), (d, hm))
        got = cp.hold_rates(epoch_of(date(2026, 11, 2), "08:00"))
        self.assertEqual(("2026-11-02 08:00 ET", "2026-10-30", ["SOFR"]), (got["on"], got["dated"], got["series"]))
        self.assertIsNone(cp.hold_rates(epoch_of(date(2026, 11, 2), "17:05")))          # once a day
        cp.rt.rates.last["SOFR"] = date(2026, 11, 24)                                    # the daily holds of 11-03 .. 11-25 (not replayed here)
        self.assertEqual("2026-11-25", cp.hold_rates(epoch_of(date(2026, 11, 27), "08:00"))["dated"])
        self.assertEqual([("SOFR", date(2026, 10, 30)), ("SOFR", date(2026, 11, 25))], cp.rt.rates.put_calls)


class ReportViewsOnTheRealRouterTests(unittest.TestCase):
    """V1061R2-02: the run report's cash and collateral views (10.2 step 6; 10.7 "Available Treasury cash and Goldman collateral after
    booking") and the restart check C9, read through the REAL terminal API - ``Router.handle`` of a ``build_runtime`` world, every module
    bound as ``serve`` binds it - not ``FakeTerminal``, which answers neither route and so hid that the program asked for ``/api/cash`` and
    ``/api/collateral``, two routes the terminal never had (404: the 1.0.6.1 dry runs' reports said "unavailable" and C9 compared two
    equal "unavailable" answers).

    The world: Fund Book F1 (Treasury USD 25,000,000.00), the Goldman Sachs PB opened, Thu 2026-10-29 10:00 ET, the worked example's curves
    and prints; trade B of the plan booked (``otc.book``).  Hand-worked: IM 6 % x the USD notional 2,000,000.00 x 1.1158 = 2,231,600.00 x
    0.06 = 133,896.00, one Treasury sweep -> Treasury USD 25,000,000.00 - 133,896.00 = 24,866,104.00 settled and available; Goldman Sachs
    IM posted 133,896.00 = required (the open contracts' IM); no VM yet (0.00 posted, 0.00 received).  Then the runtime is stopped and
    started again on the same database (``build_runtime`` re-opens it, as ``serve`` does) and the views are read again."""

    @classmethod
    def setUpClass(cls):
        from tests.test_terminal_api import build as api_build
        from tests.test_terminal_fx1061_integration import RouterHttp
        from finsim.terminal import config, fxcurves
        from finsim.terminal.commands import Command
        cls.tmp = tempfile.mkdtemp(prefix="fst-otcplan-views-")
        cls.plan, cls.sha = otcplan.load_plan(PLAN_PATH)
        rt = api_build(cls.tmp, at="2026-10-29T10:00:00-04:00", books=[config.BOOKS[1]])
        try:
            fxcurves.fixture_curves(rt, persist=True)
            cls.put_prints(rt)
            r = rt.bus.execute(Command("account.open_pb", {"lifetime_id": "F1", "broker": "GS"}, "ui:otcplan-views-open-pb", "ui"))
            assert r.ok, r.body
            r = rt.bus.execute(Command("otc.book", {"account_id": "F1:PB_GS", "kind": "FX_FORWARD", "terms": dict(cls.plan["trades"][1]["terms"])},
                                       "ui:otcplan-views-b-0001", "ui"))
            assert r.ok, r.body
            cls.http = RouterHttp(rt)
            cls.before = cls.views(cls.http)
            cls.tre_c = cls.ledger(rt, "F1:TREASURY", "1010")
            cls.im_c = cls.ledger(rt, "F1:PB_GS", "1330")
            cls.old = {p: cls.http.get(p, {"scope": "F1:ALL"})[0] for p in ("/api/cash", "/api/collateral")}
        finally:
            rt.close()
        cls.rt = api_build(cls.tmp, at="2026-10-29T10:05:00-04:00", books=[config.BOOKS[1]])      # the restart: the same database re-opened
        cls.put_prints(cls.rt)
        cls.after = cls.views(RouterHttp(cls.rt))
        cls.snap = otcplan.snapshot(cls.rt.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def put_prints(rt):
        from finsim.terminal.quotes import QuoteRecord
        now = rt.clock.epoch()
        for iid, px in (("EURUSD", "1.1158"), ("GBPUSD", "1.3323"), ("EURGBP", "0.83725")):
            rt.cache.put(QuoteRecord(iid, "yahoo", "test print", "direct", last=D(px), quote_time=now, fetched_at=now, session_group="FX_SPOT"), at=now)

    @classmethod
    def views(cls, http):
        """``Runner.views`` over the real router; the JSON round trip of the run report."""
        r = otcplan.Runner(cls.plan, cls.sha, http, cls.tmp, sleep=lambda s: None, out=lambda s: None)
        return json.loads(json.dumps(r.views(), default=str))

    @staticmethod
    def ledger(rt, account_id, gl):
        from tests.terminal_helpers import bal
        return bal(rt, account_id, gl, "USD")

    def c9(self, before, after):
        chk = otcplan.Checker(self.rt.db_path, self.snap, {"plan_id": self.plan["plan_id"], "run_id": "20261029T100000"},
                              after_restart={"before": before, "after": after})
        try:
            chk.c9()
        finally:
            chk.close()
        return chk.items["C9"]

    def test_v1061r2_02_every_view_the_report_needs_is_served(self):
        # the routes the report reads answer 200 on the real router; the two the program used to ask for do not exist there
        self.assertEqual({"/api/cash": 404, "/api/collateral": 404}, self.old)
        self.assertEqual(("/api/accounting/cash", "/api/collateral/summary"), (otcplan.CASH_ROUTE, otcplan.COLLATERAL_ROUTE))
        for side, views in (("before", self.before), ("after", self.after)):
            self.assertEqual({}, {k: v["unavailable"] for k, v in views.items() if isinstance(v, dict) and "unavailable" in v}, side)
        got = [p for m, p in self.http.calls if m == "GET"]
        self.assertIn("/api/accounting/cash", got)
        self.assertIn("/api/collateral/summary", got)

    def test_v1061r2_02_treasury_cash_and_goldman_collateral_after_booking(self):
        cash, col = self.before["cash"], self.before["collateral"]
        # Treasury: USD 25,000,000.00 - the IM sweep 133,896.00 = 24,866,104.00, settled and available (the ledger's 1010)
        tre = [t for t in cash["treasury"] if t["account_id"] == "F1:TREASURY"]
        self.assertEqual([({"USD": "24866104.00"}, "24866104.00")], [(t["settled_by_ccy"], t["available"]) for t in tre])
        self.assertEqual(2486610400, self.tre_c)
        # the PB account: no USD of its own, its OTC margin = the IM posted
        pb = [(r["currency"], r["settled"], r["otc_margin"]) for r in cash["cash"] if r["account_id"] == "F1:PB_GS"]
        self.assertEqual([("USD", "0.00", "133896.00")], pb)
        # Goldman Sachs under its CSA: IM posted 133,896.00 (= 1330) against the IM required 133,896.00; VM 0.00 posted, 0.00 received
        gs = [g for g in col["by_counterparty"] if g["type"] == "CSA"]
        self.assertEqual([("Goldman Sachs", "F1:PB_GS", "133896.00", "133896.00", "0.00", "0.00")],
                         [(g["counterparty"], g["account_id"], g["im_posted"], g["im_required"], g["vm_posted"], g["vm_received"]) for g in gs])
        self.assertEqual(13389600, self.im_c)

    def test_v1061r2_02_the_report_text_states_them(self):
        text = otcplan.report_text({"plan_id": self.plan["plan_id"], "account_id": "F1:PB_GS", "trades": [], "checks": {}, "views": self.before,
                                    "exit_code": 0})
        self.assertIn("Treasury F1:TREASURY after the run: settled USD 24,866,104.00; available USD 24,866,104.00.", text)
        self.assertIn("Goldman Sachs collateral after the run (F1:PB_GS): IM posted 133,896.00 (required 133,896.00); VM posted 0.00; VM received 0.00.",
                      text)
        # a view that could not be read is said so, never left out
        gone = dict(self.before, cash={"unavailable": "HTTP 503: ('BUSY', 'try again')"}, collateral={"unavailable": "HTTP 404: ('NOT_FOUND', 'x')"})
        lines = otcplan.cash_collateral_lines(gone, "F1:PB_GS")
        self.assertEqual(["Treasury cash after the run: unavailable (HTTP 503: ('BUSY', 'try again')).",
                          "Prime-broker collateral after the run: unavailable (HTTP 404: ('NOT_FOUND', 'x'))."], lines)

    def test_v1061r2_02_c9_compares_cash_and_collateral_across_the_restart(self):
        # the real stop and start keeps every stored fact: C9 passes, having compared the cash and the collateral too
        item = self.c9(self.before, self.after)
        self.assertTrue(item["ok"], item)
        self.assertEqual(["contracts", "risk", "risk_pb", "cash", "collateral"], item["evidence"]["views"])
        self.assertEqual(["funding_plan", "state"], item["evidence"]["not_compared"])
        sv = otcplan.stable_views(self.after)
        self.assertIn({"part": "treasury", "account_id": "F1:TREASURY", "settled_by_ccy": {"USD": 2486610400}}, sv["cash"])
        self.assertEqual([{"counterparty": "Goldman Sachs", "account_id": "F1:PB_GS", "im_posted": 13389600, "im_required": 13389600, "vm_posted": 0,
                           "vm_received": 0}], sv["collateral"])

    def test_v1061r2_02_c9_fails_when_cash_or_collateral_changed(self):
        # one cent less in Treasury after the "restart": C9 fails naming the view, the field and the row
        after = copy.deepcopy(self.after)
        for t in after["cash"]["treasury"]:
            t["settled_by_ccy"]["USD"] = "24866103.99"
        item = self.c9(self.before, after)
        self.assertFalse(item["ok"])
        self.assertEqual(["cash differs after the restart in settled_by_ccy (treasury F1:TREASURY)"], item["evidence"]["problems"])
        # Goldman Sachs' IM posted lost after the "restart": C9 fails on the collateral
        after = copy.deepcopy(self.after)
        for g in after["collateral"]["by_counterparty"]:
            g["im_posted"] = "0.00"
        item = self.c9(self.before, after)
        self.assertFalse(item["ok"])
        self.assertEqual(["collateral differs after the restart in im_posted (Goldman Sachs F1:PB_GS)"], item["evidence"]["problems"])
        # a live figure (the available cash, re-computed from working orders and today's prices) is not a stored fact: not compared
        after = copy.deepcopy(self.after)
        for r in after["cash"]["cash"]:
            r["available"] = "1.00"
        self.assertTrue(self.c9(self.before, after)["ok"])

    def test_v1061r2_02_c9_never_passes_on_views_it_could_not_read(self):
        # the 1.0.6.1 dry runs' views: cash and collateral 404 before and after - C9 compared them vacuously; now each is a problem
        gone = {"cash": {"unavailable": "HTTP 404: ('NOT_FOUND', 'no route GET /api/cash')"},
                "collateral": {"unavailable": "HTTP 404: ('NOT_FOUND', 'no route GET /api/collateral')"}}
        item = self.c9(dict(self.before, **gone), dict(self.after, **gone))
        self.assertFalse(item["ok"])
        self.assertEqual(["cash could not be read before the restart (HTTP 404: ('NOT_FOUND', 'no route GET /api/cash')) and after the restart"
                          " (HTTP 404: ('NOT_FOUND', 'no route GET /api/cash')): not compared",
                          "collateral could not be read before the restart (HTTP 404: ('NOT_FOUND', 'no route GET /api/collateral')) and after the"
                          " restart (HTTP 404: ('NOT_FOUND', 'no route GET /api/collateral')): not compared"], item["evidence"]["problems"])


if __name__ == "__main__":
    unittest.main()
