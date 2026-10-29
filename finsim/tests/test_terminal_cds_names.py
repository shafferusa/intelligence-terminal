"""Single-name CDS (release 1.0.7, CDS_SINGLE_NAME_PLAN rev 2, package B1): products/swaps_cds_names.py - the ``CDS`` delegate kind of the PB OTC
swaps.  Money tests worked by hand in the comments (plan section 8, B1 tests 1-19; the numbers of Appendix C are there to check against);
nothing is pasted from the code's output.  No network: FakeClock, a CurveStore with fixed discount factors, a temp database, and STUBS of
the two modules B1 codes against - ``services.credit_entities`` (contract C-ENT) and ``services.cds_model`` (contract C-MODEL) - whose
spread the tests set (the model is stubbed at a mid of 65.47 bp, bucket AA, tier A_OR_BETTER, GS).

The pure pieces (maturities, terms, seeds, the risky annuity re-derived independently, words) run everywhere.  The booked-contract tests need
the hook edits that wire the CDS kind into the platform (``hooks_b1.py``: db.OTC_KINDS, prime.OTC_LIVE, swaps.SINGLE_CDS_KIND / _module_for /
FIRM_KINDS, lifecycle_jobs.ACCRUAL_PRODUCT); until they are applied those tests SKIP with that reason.

Hand-worked inputs (Appendix C)
- Trade Thu 2026-10-29 14:00 ET.  On-the-run roll 2026-09-21 (20 Sep 2026 was a Sunday), 5Y maturity 2031-12-20, 21 coupon periods: the first
  2026-09-21 -> 2026-12-21 (paid 2026-12-21, 20 Dec is a Sunday), the last 2031-09-22 -> 2031-12-21 (the maturity included), paid 2031-12-22.
- The CDX tests' curve: DF = 1 / 1.02^k at 0.5 k years (k = 1 .. 12, rounded to 8 dp), log-linear in between.
- Buy protection USD 5,000,000.00, coupon 100 bp, recovery 40 %.  MODEL mid 65.47 bp (stub), bucket AA -> tier A_OR_BETTER: GS IM 4 %, half-spread
  2.00 bp, OTC spread multiplier 1 -> buyer deals 67.47 bp, seller 63.47 bp.
- Risky annuities (re-derived independently below, ``test_risky_annuity_by_an_independent_sum``): RPV01(67.47) = 4.5669965459, RPV01(65.47) =
  4.5707484462, RPV01(63.47) = 4.5745045516.
"""
from __future__ import annotations

import json
import math
import shutil
import tempfile
import types
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tests.terminal_helpers import T1, assert_integrity, bal, deposit, execute, fund, make_runtime, pb
from finsim.terminal import config, db as _db, lifecycle, lifecycle_jobs, prime
from finsim.terminal.calendar import TradingCalendar
from finsim.terminal.clock import epoch_of, ny_of
from finsim.terminal.products import swaps, swaps_cds as cdx
from finsim.terminal.products import swaps_cds_names as cdsn
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
PB_GS = pb("GS")
THU = date(2026, 10, 29)
THU_14 = "2026-10-29T14:00:00-04:00"
DFS = {"0.5": "0.98039216", "1.0": "0.96116878", "1.5": "0.94232233", "2.0": "0.92384543", "2.5": "0.90573081", "3.0": "0.88797138", "3.5": "0.87056018",
       "4.0": "0.85349037", "4.5": "0.83675527", "5.0": "0.82034830", "5.5": "0.80426304", "6.0": "0.78849318"}
HOOKS = ("CDS" in getattr(_db, "OTC_KINDS", ()) and "CDS" in getattr(prime, "OTC_LIVE", ()) and getattr(swaps, "SINGLE_CDS_KIND", None) == "CDS"
         and "CDS" in getattr(swaps, "FIRM_KINDS", ()) and "CDS" in lifecycle_jobs.ACCRUAL_PRODUCT.get("swappay", ("", "", "", ()))[3])
SKIP_WHY = ("the single-name CDS hook edits (hooks_b1.py: db.OTC_KINDS, prime.OTC_LIVE, swaps.SINGLE_CDS_KIND / _module_for / FIRM_KINDS,"
            " lifecycle_jobs.ACCRUAL_PRODUCT) are not applied to this tree: the booked-contract tests run on a rehearsal copy")
ORCL_CIK = 1341439
FORD_CIK = 37996
LADDER = {"AAA": "42", "AA": "63", "A": "73", "BBB": "109", "BB": "187", "B": "322", "CCC": "1321"}       # FRED ICE BofA OAS, bp (2026-10-27)
BUY = {"cik": ORCL_CIK, "ticker": "ORCL", "tenor_years": 5, "side": "BUY_PROTECTION", "notional": "5000000.00"}
SELL = dict(BUY, side="SELL_PROTECTION")


def _cal():
    return TradingCalendar(config.holidays_path())


# ---- the stubs of C-ENT and C-MODEL (plan 6.3 / 6.4 shapes) -----------------------------------------------------------------------------
ENTITIES = [
    {"cik": ORCL_CIK, "ticker": "ORCL", "name": "ORACLE CORP", "exchange": "NYSE", "tickers": [], "status": "COVERED", "status_reason": None, "flag": "",
     "flag_source": None, "sic": "7372", "sic_desc": "Services-Prepackaged Software", "latest_form": "10-Q", "latest_filed": "2026-09-10", "checked_ts": None,
     "instrument_id": "ORCL", "fundamentals": {"debt_c": 12533700000000, "debt_recipe": "R6", "end_date": "2026-08-31"}},
    {"cik": FORD_CIK, "ticker": "F", "name": "FORD MOTOR CO", "exchange": "NYSE", "tickers": [], "status": "NO_DEBT", "status_reason": "NO_DEBT", "flag": "",
     "flag_source": None, "sic": "3711", "sic_desc": None, "latest_form": "10-Q", "latest_filed": None, "checked_ts": None, "instrument_id": "F", "fundamentals": None},
]


class StubEntities:
    """C-ENT ``services.credit_entities``: ``entity(conn, key)`` by CIK or ticker, ``universe_status(conn)``."""

    def __init__(self, rows=ENTITIES):
        self.rows = {r["cik"]: dict(r) for r in rows}

    def entity(self, conn, key):
        if isinstance(key, int) or str(key).strip().isdigit():
            r = self.rows.get(int(key))
            return dict(r) if r else None
        k = str(key).strip().upper()
        for r in self.rows.values():
            if r["ticker"] == k or k in r.get("tickers", []):
                return dict(r)
        return None

    def universe_status(self, conn):
        return {"offered": 1, "listed": 2, "by_status": {"COVERED": 1, "NO_DEBT": 1}, "refreshed_ts": None, "source": "test fixture"}


class StubModel:
    """C-MODEL ``services.cds_model``: the spread of each CIK is what the test sets (``spread``), with the AAA floor 42.00 bp and the FRED ladder
    of 2026-10-27; ``vm`` answers the VM path per date (a spread, or "CLOSE_PENDING" / "CAUGHT_UP"); ``shocks`` is what ``spread_at`` returns
    per share-price multiplier; ``pending`` CIKs answer INPUTS_PENDING live."""
    LABEL = "MODEL"
    MODEL_TEXT = "test fixture: equity-implied CreditGrades plus the ICE BofA AAA OAS floor - a MODEL spread, not a dealer quote"

    def __init__(self):
        self.spread = {ORCL_CIK: D("65.47")}
        self.quality = {}
        self.vm = {}
        self.shocks = {}
        self.pending = set()
        self.undealable = {}
        self.calls = []

    @staticmethod
    def bucket_of(spread_bp, ladder_bp):
        # plan 3.3: the bucket whose OAS is nearest in |ln(spread / OAS)|; a tie goes to the wider bucket (a later one in the ladder order wins "<=")
        s, best = D(str(spread_bp)), None
        for b in ("AAA", "AA", "A", "BBB", "BB", "B", "CCC"):
            dist = abs((s / D(str(ladder_bp[b]))).ln())
            if best is None or dist <= best[0]:
                best = (dist, b)
        return best[1]

    def ladder(self, services, on=None, today=None):
        rows = {b: {"series_id": f"FRED-{b}", "value_pct": str(D(x) / 100), "spread_bp": D(x), "as_of": "2026-10-27", "age_bd": 2} for b, x in LADDER.items()}
        return {"ok": True, "code": None, "reason": None, "as_of": "2026-10-27", "rows": rows, "floor": dict(rows["AAA"], series_id="BAMLC0A1CAAA")}

    def model_spread(self, services, cik, maturity, valuation_date=None, conn=None, now=None, marks=None):
        self.calls.append((cik, maturity, valuation_date))
        if cik not in self.spread:
            return {"ok": False, "code": "ENTITY_UNKNOWN", "reason": f"no reference entity {cik}"}
        if valuation_date is not None:
            ds = valuation_date.isoformat()
            spec = self.vm.get(ds)
            if spec in ("CLOSE_PENDING", "CAUGHT_UP"):
                why = (f"the AAA OAS of {ds} is not published yet (FRED posts it the next morning)" if spec == "CLOSE_PENDING"
                       else f"FRED skipped {ds} and a later value exists")
                return {"ok": False, "code": spec, "reason": why, "vm_outcome": spec, "close_date": ds}
            sp = D(str(spec)) if spec is not None else self.spread[cik]
            v = services.cal.roll_back(valuation_date)
            underlying = {"instrument_id": "ORCL", "close_date": v.isoformat()}
        else:
            if cik in self.pending:
                return {"ok": False, "code": "INPUTS_PENDING", "reason": "fetching ORACLE CORP's SEC filings and 1,000 daily closes", "retry_ms": 1500}
            sp = self.spread[cik]
            ts = int(now if now is not None else services.clock.epoch())
            v = ny_of(ts).date()
            underlying = {"instrument_id": "ORCL", "quote_time": ts}
        lad = self.ladder(services)
        return {"ok": True, "label": "MODEL", "model": self.MODEL_TEXT, "cik": cik, "ticker": "ORCL", "name": "ORACLE CORP", "valuation_date": v.isoformat(),
                "maturity_date": maturity.isoformat(), "T": "5.1452", "raw_bp": sp - D("42.00"), "floor_bp": D("42.00"), "spread_bp": sp,
                "bucket": self.bucket_of(sp, {b: r["spread_bp"] for b, r in lad["rows"].items()}),
                "bucket_label": "model spread bucket - not a credit rating", "quality": self.quality.get(cik, "computed"),
                "dealable": cik not in self.undealable, "dealable_reason": self.undealable.get(cik), "floor": lad["floor"], "ladder": lad,
                "inputs": {"S": "144.54", "S_source": "test", "sigma": "0.4577", "debt_c": 12533700000000, "shares": 3023736000, "dps": "41.45",
                           "debt_end": "2026-08-31", "r": "0.0476", "R": "0.40", "Lbar": "0.5", "lambda": "0.3"},
                "method": "closed form", "intermediate": {"P0": "0.99"}, "underlying": underlying,
                "proxy": {"as_of": "2026-10-27", "series_id": "BAMLC0A1CAAA"}, "curve_as_of": "2026-10-28"}

    def spread_at(self, model, s_mult):
        v = self.shocks.get(str(s_mult))
        return None if v is None else D(str(v))


# ---- pure pieces (no hooks) ------------------------------------------------------------------------------------------------------------------
class CdsNamesPureTests(unittest.TestCase):

    def test_01_maturities_by_the_semi_annual_roll(self):
        cal = _cal()
        r = cdsn.roll_of(cal, THU)
        # 20 Sep 2026 is a Sunday: the roll is Mon 2026-09-21 (a September roll -> 20 December maturities, n years after 2026)
        self.assertEqual(date(2026, 9, 21), r["roll_date"])
        self.assertEqual({"1": date(2027, 12, 20), "3": date(2029, 12, 20), "5": date(2031, 12, 20), "7": date(2033, 12, 20), "10": date(2036, 12, 20)},
                         {k: r["maturities"][k] for k in ("1", "3", "5", "7", "10")})
        # 20 Mar 2027 is a Saturday: the March roll is Mon 2027-03-22 -> 20 June maturities; 5Y = 2032-06-20; the day before still rolls from 2026-09-21
        self.assertEqual(date(2032, 6, 20), cdsn.roll_of(cal, date(2027, 3, 22))["maturities"]["5"])
        self.assertEqual(date(2031, 12, 20), cdsn.roll_of(cal, date(2027, 3, 19))["maturities"]["5"])

    def test_terms_parsing_names_the_field(self):
        t = cdsn.parse_terms({"ticker": "orcl", "side": "buy", "notional": "25,000,000.00"})
        self.assertEqual((None, "ORCL", 5, "BUY_PROTECTION", 2500000000, None), (t["cik"], t["ticker"], t["tenor_years"], t["side"], t["notional_c"], t["coupon_bp"]))
        t = cdsn.parse_terms({"cik": str(ORCL_CIK), "tenor_years": "7Y", "side": "SELL_PROTECTION", "notional_c": 10000000, "coupon_bp": "500"})
        self.assertEqual((ORCL_CIK, 7, 10000000, 500), (t["cik"], t["tenor_years"], t["notional_c"], t["coupon_bp"]))
        bad = (({"side": "BUY", "notional": "1000000"}, "cik"), ({"cik": "x1", "side": "BUY", "notional": "1000000"}, "cik"),
               ({"cik": 1, "side": "LONG", "notional": "1000000"}, "side"), ({"cik": 1, "side": "BUY", "notional": "1000000.001"}, "notional"),
               ({"cik": 1, "side": "BUY", "notional": "99999.99"}, "notional"), ({"cik": 1, "side": "BUY", "notional": 1000000.0}, "notional"),
               ({"cik": 1, "side": "BUY", "notional": "1000000", "tenor_years": 6}, "tenor_years"),
               ({"cik": 1, "side": "BUY", "notional": "1000000", "coupon_bp": "250"}, "coupon_bp"))
        for payload, field in bad:
            with self.assertRaises(Exception) as cm:
                cdsn.parse_terms(payload)
            self.assertEqual(("TERMS", field), (getattr(cm.exception, "code", None), (getattr(cm.exception, "details", None) or {}).get("field")), payload)

    def test_tier_seeds_by_broker_group(self):
        # plan 5.1: GS / JPM / CITI / BOFA lowest; MS / BARC / UBS; DB / BNP; the half-spread is the same for every broker
        self.assertEqual((D("0.04"), D("2.0")), (cdsn.seed_terms("GS")["A_OR_BETTER"]["im_pct"], cdsn.seed_terms("GS")["A_OR_BETTER"]["half_spread_bp"]))
        self.assertEqual(D("0.05"), cdsn.seed_terms("BOFA")["BBB"]["im_pct"])
        self.assertEqual((D("0.06"), D("3.0")), (cdsn.seed_terms("MS")["BBB"]["im_pct"], cdsn.seed_terms("MS")["BBB"]["half_spread_bp"]))
        self.assertEqual(D("0.135"), cdsn.seed_terms("UBS")["B"]["im_pct"])
        self.assertEqual((D("0.30"), D("40.0")), (cdsn.seed_terms("BNP")["CCC"]["im_pct"], cdsn.seed_terms("BNP")["CCC"]["half_spread_bp"]))
        self.assertEqual(D("0.10"), cdsn.seed_terms("DB")["BB"]["im_pct"])
        self.assertEqual({"AAA": "A_OR_BETTER", "AA": "A_OR_BETTER", "A": "A_OR_BETTER", "BBB": "BBB", "BB": "BB", "B": "B", "CCC": "CCC"}, cdsn.TIER_OF_BUCKET)

    def test_default_coupon_follows_the_model_bucket(self):
        # Q11: 100 bp unless the MODEL bucket (before any basis) is BB-like or worse
        self.assertEqual([100, 100, 100, 100, 500, 500, 500], [cdsn.default_coupon_bp({"bucket": b}) for b in ("AAA", "AA", "A", "BBB", "BB", "B", "CCC")])

    def test_risky_annuity_by_an_independent_sum(self):
        """Appendix C's RPV01 re-derived without the module: the clean risky annuity = sum over the periods ending after the step-in date
        (10-30) of accrual (days / 360) x DF(pay) x (Q(start) + Q(end)) / 2, Q(t) = exp(-lambda t), lambda = S / (1 - 0.40), t = days / 365,
        DF log-linear in ln DF between (0, 0) and the curve's nodes."""
        cal = _cal()
        nodes = [(0.0, 0.0)] + sorted((float(t), math.log(float(v))) for t, v in DFS.items())

        def df(t):
            for (t0, l0), (t1, l1) in zip(nodes, nodes[1:]):
                if t <= t1:
                    return math.exp(l0 + (l1 - l0) * (t - t0) / (t1 - t0))
            (t0, l0), (t1, l1) = nodes[-2], nodes[-1]
            return math.exp(l1 + (l1 - l0) / (t1 - t0) * (t - t1))

        periods = cdx.coupon_periods(cal, THU, date(2031, 12, 20))
        self.assertEqual(21, len(periods))

        def rpv(spread_bp):
            lam = spread_bp / 10000.0 / 0.6
            step_in = THU + timedelta(days=1)
            total = 0.0
            for s, e, p in periods:
                if e <= step_in:
                    continue
                a = max(s, step_in)
                q = lambda d: math.exp(-lam * (d - THU).days / 365.0)            # noqa: E731
                total += (e - a).days / 360.0 * df((p - THU).days / 365.0) * (q(a) + q(e)) / 2
            return total
        self.assertAlmostEqual(4.5669965459, rpv(67.47), places=9)
        self.assertAlmostEqual(4.5707484462, rpv(65.47), places=9)
        self.assertAlmostEqual(4.5745045516, rpv(63.47), places=9)
        self.assertAlmostEqual(4.5688719707, rpv(66.47), places=9)                   # the mid + 1 bp (CS01, the VM test) and the moved model
        self.assertAlmostEqual(4.2155122306, rpv(265.47), places=9)                  # the basis-effect test's mid
        # CS01 at the mid: (66.47 - 100) bp x 5,000,000 = -16,765 x 4.5688719707 = -76,597.14; -76,597.14 - (-78,913.97) = +2,316.83
        self.assertEqual(D("-76597.14"), (D(-16765) * D("4.5688719707")).quantize(D("0.01")))
        # the value at 265.47: (265.47 - 100) bp x 5,000,000 = 82,735 x 4.2155122306 = +348,770.40
        self.assertEqual(D("348770.40"), (D(82735) * D("4.2155122306")).quantize(D("0.01")))
        # the buyer's upfront at the dealt 67.47 bp: 5,000,000 x (67.47 - 100) bp x 4.5669965459 = -16,265 x 4.5669965459 = -74,282.198... -> -74,282.20
        self.assertEqual(D("-74282.20"), (D(-16265) * D("4.5669965459")).quantize(D("0.01")))
        # the mark at mid 65.47: -17,265 x 4.5707484462 = -78,913.97; the seller's upfront at 63.47: -18,265 x 4.5745045516 = -83,553.33 (the seller pays)
        self.assertEqual(D("-78913.97"), (D(-17265) * D("4.5707484462")).quantize(D("0.01")))
        self.assertEqual(D("-83553.33"), (D(-18265) * D("4.5745045516")).quantize(D("0.01")))

    def test_words_and_display_fields(self):
        self.assertEqual("ORCL 5Y CDS (ORACLE CORP)", cdsn.entity_label("ORCL", 5, "ORACLE CORP"))
        label, side, units, n = cdsn.rfq_describe({"cik": ORCL_CIK, "ticker": "ORCL", "tenor_years": 5, "side": "BUY_PROTECTION", "notional": "5000000.00"}, None)
        self.assertEqual(("ORCL 5Y CDS", "BUY PROTECTION", None, 500000000), (label, side, units, n))
        label, side, _u, n = cdsn.rfq_describe({"cik": ORCL_CIK, "side": "SELL", "notional_c": 100000000}, {"entity_label": "ORCL 5Y CDS (ORACLE CORP)", "notional_c": 100000000})
        self.assertEqual(("ORCL 5Y CDS (ORACLE CORP)", "SELL PROTECTION", 100000000), (label, side, n))
        f = cdsn.display_fields({"notional_c": 500000000}, {"deal_spread_bp": "67.4700", "ticker": "ORCL", "upfront_c": -7428220, "side": "BUY_PROTECTION"},
                                {"mid_spread_bp": "65.4700", "proxy": {"as_of": "2026-10-27"}, "bucket": "AA"})
        self.assertEqual((5000000, "USD notional", "67.47 bp", "65.47 bp", "received", "spread now (MODEL mid)", "2026-10-27"),
                         (f["units"], f["unit_label"], f["entry"], f["current"], f["extra"]["upfront"], f["extra"]["current_label"], f["extra"]["floor_as_of"]))
        self.assertIsNone(cdsn.display_fields({"notional_c": 100}, {}, {})["current"])
        words = cdsn.level_words({"level_kind": "NEGOTIATED", "basis_bp": "18.5300", "entered_ts": epoch_of(THU, "10:14")}, D("65.47"), D("84.00"))
        self.assertEqual("mid 84.00 bp = model 65.47 bp + your negotiated basis +18.53 bp (set 2026-10-29 10:14 ET)", words)
        words = cdsn.level_words({"level_kind": "MODEL", "basis_bp": "0", "entered_ts": epoch_of(THU, "10:14")}, D("65.47"), D("65.47"))
        self.assertEqual("mid 65.47 bp = MODEL (you confirmed dealing at the model on 2026-10-29 10:14 ET)", words)

    def test_unsupported_rows(self):
        rows = cdsn.unsupported()
        self.assertEqual(7, len(rows))
        self.assertEqual(["LIMITED", "LIMITED", "NOT_OFFERED", "NOT_OFFERED", "SIMULATED", "NOT_OFFERED", "NOT_OFFERED"], [r["state"] for r in rows])
        self.assertIn("DTCC's terms prohibit it", rows[-1]["wording"])
        for r in rows:
            self.assertTrue(r["item"] and r["wording"] and r["where"], r)
            self.assertEqual(r["wording"], r["wording"].encode("ascii", "strict").decode("ascii"))

    def test_conventions_registered(self):
        from finsim.terminal import tables
        for k in ("CDS_MODEL", "CDS_DEAL", "CDS_COUPON", "CDS_CREDIT"):
            self.assertTrue(tables.CONVENTIONS.get(k), k)
        self.assertIn("not validated against traded levels", tables.CONVENTIONS["CDS_MODEL"])
        self.assertIn("three business days later", tables.CONVENTIONS["CDS_DEAL"])


# ---- the booked contracts (need the hooks) ---------------------------------------------------------------------------------------------
def runtime(tmp):
    rt = make_runtime(tmp, at=THU_14, books="test")
    prime.register(rt.bus)
    prime.bind(rt)
    rt.rates = RateStore()
    rt.rates.put("SOFR", [(date(2026, 10, 28), D("3.87"))], persist=False)
    rt.curve = CurveStore()
    rt.curve.set_fixed_dfs(DFS, date(2026, 10, 28))
    swaps.register(rt.bus, rt)
    rt.swaps = swaps
    swaps.bind(rt)
    cdsn.register(rt.bus, rt)
    cdsn.bind(rt)
    rt.swaps_cds_names = cdsn
    rt.credit_entities = StubEntities()
    rt.cds_model = StubModel()
    rt.providers = types.SimpleNamespace(yahoo=rt.fp)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    return rt


@unittest.skipUnless(HOOKS, SKIP_WHY)
class CdsNamesBookedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-cdsn-")
        rt = self.rt = runtime(self.tmp)
        self.model = rt.cds_model
        self.assertTrue(deposit(rt, 1510000000).ok)                  # T1 10,000,000 + 15,100,000 = 25,100,000.00: past the PB / OTC threshold
        r = execute(rt, "account.open_pb", {"lifetime_id": T1, "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        self.assertTrue(fund(rt, PB_GS, 100000000).ok)                # 1,000,000.00 settled in T1:PB_GS

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----------------------------------------------------------------------------------------------------------------------
    def q(self, sql, params=()):
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def lines(self, idem):
        return [(r["gl"], r["amount_c"]) for r in self.q("SELECT l.gl, l.amount_c FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.idem_key=?"
                                                           " ORDER BY l.line_no", (idem,))]

    def level(self, rid="ui:cdsn-level-model", **kw):
        payload = {"account_id": PB_GS, "cik": ORCL_CIK}
        payload.update(kw or {"deal_at_model": True})
        r = execute(self.rt, "cds.basis", payload, rid)
        self.assertTrue(r.ok, r.body)
        return r.body

    def preview(self, terms=None):
        return swaps.preview(self.rt, PB_GS, "CDS", dict(terms or BUY))

    def book(self, terms=None, rid="ui:cdsn-book-0001"):
        r = execute(self.rt, "otc.book", {"account_id": PB_GS, "kind": "CDS", "terms": dict(terms or BUY)}, rid)
        self.assertTrue(r.ok, r.body)
        return r.body

    def accrue(self, cid, first: date, last: date, kind="swappay"):
        night = first
        while night <= last:
            key = f"{kind}:{cid}:{night.isoformat()}"
            r = execute(self.rt, "lifecycle.accrue", {"key": key, "kind": kind, "ref": cid, "night": night.isoformat()}, f"job:ACCRUE_DAILY:{key}", actor="lifecycle")
            self.assertTrue(r.ok and r.body["status"] == "DONE", (night, r.body))
            night += timedelta(days=1)

    def vm(self, cid, ds, suffix=""):
        return execute(self.rt, "swap.vm", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, f"job:SWAP_VM:{cid}:{ds}{suffix}", actor="lifecycle")

    def settings_changed(self, ref_type="cds_basis"):
        return self.q("SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_type=? ORDER BY id", (ref_type,))

    def mark0(self, spread, side="BUY_PROTECTION", on=THU, notional_c=500000000):
        """The hand-derivation helper the plan names: the existing swaps_cds.value_c on the CDX tests' curve (Appendix C)."""
        periods = cdx.coupon_periods(self.rt.cal, THU, date(2031, 12, 20))
        df = cdx.df_function({D(k): D(v) for k, v in DFS.items()})
        return cdx.value_c(side, notional_c, D(str(spread)), D(100), D("0.40"), periods, on, df)[0]

    # ---- plan B1 tests 2 .. 19 --------------------------------------------------------------------------------------------------------
    def test_02_tier_and_spreads(self):
        p = self.preview()
        # MODEL 65.47 bp = AA-like (|ln(65.47 / 63)| = 0.0385 < A 0.1088) -> tier A_OR_BETTER: GS half-spread 2.00 bp x multiplier 1
        self.assertEqual(("65.4700", "AA", "A_OR_BETTER", "2.0000", "67.4700"), (p["mid_spread_bp"], p["mid_bucket"], p["tier"], p["half_spread_eff_bp"], p["deal_spread_bp"]))
        self.assertEqual("63.4700", self.preview(SELL)["deal_spread_bp"])
        r = execute(self.rt, "account.update_schedule", {"account_id": PB_GS, "otc_spread_mult": "2.0"}, "ui:cdsn-mult-2")
        self.assertTrue(r.ok, r.body)
        # 65.47 + 2.00 x 2.0 = 69.47
        self.assertEqual(("4.0000", "69.4700"), (self.preview()["half_spread_eff_bp"], self.preview()["deal_spread_bp"]))

    def test_03_buyer_booking(self):
        rt = self.rt
        nav0 = rt.accounting.nav(PB_GS).nav_c
        self.level()
        body = self.book()
        cid = body["contract_id"]
        self.assertEqual("CDS-000001", cid)
        p = body["preview"]
        # rebate: 09-21 .. 10-29 inclusive = 39 days: 5,000,000 x 1 % x 39 / 360 = 5,416.666... -> 5,416.67
        self.assertEqual((39, 541667), (p["accrued_rebate"]["days"], p["accrued_rebate"]["amount_c"]))
        # upfront at the dealt 67.47 bp: -74,282.20 (received; test_risky_annuity_by_an_independent_sum)
        self.assertEqual((-7428220, -7428220), (body["initial_payment"]["upfront_c"], p["upfront_buyer_c"]))
        # IM: 4 % x 5,000,000 = 200,000.00, Dr 1330 / Cr 1010
        self.assertEqual([("1330", 20000000), ("1010", -20000000)], self.lines(f"im:{cid}:open"))
        # the initial payment: Cr 2345 74,282.20 (the cost basis), Cr 2340 5,416.67 (the rebate), Dr 1200 74,282.20 + 5,416.67 = 79,698.87
        self.assertEqual([("2345", -7428220), ("2340", -541667), ("1200", 7969887)], self.lines(f"otc:{cid}:upfront"))
        s = self.q("SELECT account_id, amount_c, gl, due_date, status, leg, ref_type, ref_id FROM settlements WHERE leg='UPFRONT'")
        # T+3 business days from Thu 10-29: Fri 10-30, Mon 11-02, Tue 11-03
        self.assertEqual([(PB_GS, 7969887, "1200", "2026-11-03", "PENDING", "otc", cid)],
                         [(x["account_id"], x["amount_c"], x["gl"], x["due_date"], x["status"], x["ref_type"], x["ref_id"]) for x in s])
        # mark0 at the mid 65.47: -17,265 x 4.5707484462 = -78,913.97; cost of dealing = -78,913.97 - (-74,282.20) = -4,631.77
        row = self.q("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))[0]
        self.assertEqual(("CDS", "OPEN", 500000000, 20000000, "2026-10-29", "2031-12-20", -7891397),
                         (row["kind"], row["status"], row["notional_c"], row["im_c"], row["start_date"], row["maturity_date"], row["mark_c"]))
        self.assertEqual(-463177, p["cost_of_dealing_c"])
        # NAV change = +79,698.87 (1200) - 5,416.67 (2340) - 78,913.97 (the MV) + 74,282.20 - 74,282.20 (2345 against the cost basis) = -4,631.77
        self.assertEqual(-463177, rt.accounting.nav(PB_GS).nav_c - nav0)
        terms = json.loads(row["terms_json"])
        for k in ("kind", "cik", "ticker", "entity_name", "underlying_id", "seniority", "doc_clause", "tenor_years", "side", "receiver", "notional_c", "coupon_bp",
                  "recovery", "trade_date", "step_in_date", "accrual_start", "maturity_date", "roll_date", "fixed_basis", "fixed_periods", "model", "level_kind",
                  "basis_bp", "mid_spread_bp", "tier", "coupon_rule", "quoted", "half_spread_bp", "otc_spread_mult", "deal_spread_bp", "upfront_c", "upfront_buyer_c",
                  "upfront_pct", "price", "upfront_gl", "rebate_days", "rebate_c", "initial_payment_c", "settle_date", "settle_id", "im_pct", "mark0_c", "cs01_0_c",
                  "jtd_0_c", "rpv01_0", "margin_mode", "dealt_ts", "model_version"):
            self.assertIn(k, terms, k)
        self.assertEqual(("CDS", ORCL_CIK, "ORCL", "ORCL", "SENIOR_UNSECURED", "XR14", "100", "0.40", "MODEL", "A_OR_BETTER", "default from the MODEL bucket",
                          "2345", "CG2002-AAA-FLOOR-1", "2026-09-21"),
                         (terms["kind"], terms["cik"], terms["ticker"], terms["underlying_id"], terms["seniority"], terms["doc_clause"], terms["coupon_bp"],
                          terms["recovery"], terms["level_kind"], terms["tier"], terms["coupon_rule"], terms["upfront_gl"], terms["model_version"], terms["roll_date"]))
        # CS01 at the mid: +2,316.83 (Appendix C: full revaluation at 66.47 - at 65.47 = -76,597.14 + 78,913.97)
        self.assertEqual(231683, terms["cs01_0_c"])
        self.assertEqual(-7659714 + 7891397, terms["cs01_0_c"])
        # the premium leg starts at the accrual start with the rebate as its cumulative; the seller's kind is marked done through the maturity
        st = {r["kind"]: r for r in self.q("SELECT * FROM accrual_state WHERE ref_id=?", (cid,))}
        self.assertEqual(("2026-09-21", "2026-10-29", 541667, "0.01", 500000000), (st["swappay"]["segment_start"], st["swappay"]["accrued_through"],
                                                                                    st["swappay"]["cum_c"], st["swappay"]["rate"], st["swappay"]["base_c"]))
        self.assertEqual("2031-12-20", st["swaprcv"]["accrued_through"])
        inst = self.q("SELECT kind, underlying_id, spec_json, status FROM instruments WHERE instrument_id=?", (cid,))[0]
        self.assertEqual(("SWAP", "ORCL", "ACTIVE"), (inst["kind"], inst["underlying_id"], inst["status"]))
        self.assertEqual(("CDS", ORCL_CIK, 5), (json.loads(inst["spec_json"])["otc_kind"], json.loads(inst["spec_json"])["cik"], json.loads(inst["spec_json"])["tenor_years"]))
        ev = self.q("SELECT payload_json FROM events WHERE type='OTC_BOOKED' AND ref_id=?", (cid,))
        self.assertEqual("CDS", json.loads(ev[0]["payload_json"])["kind"])
        v = swaps.contract_view(rt, cid)
        self.assertEqual(("single-name CDS", "BUY_PROTECTION"), (v["kind_label"], v["side"]))
        self.assertTrue(v["side_text"].startswith("buy protection on ORCL 5Y (short credit): USD 5,000,000.00 notional; CS01 +"), v["side_text"])
        self.assertEqual(("2026-12-21", 1263889), (v["next_events"][0]["date"], v["next_events"][0]["legs"][0]["amount_c"]))
        self.assertEqual(("67.47 bp", "65.47 bp"), (v["position"]["entry"], v["position"]["current"]))
        assert_integrity(self, rt)

    def test_04_seller_booking(self):
        self.level()
        p = self.preview(SELL)
        # the seller deals at 63.47: the buyer's upfront there is -83,553.33 (received by a buyer), so the seller PAYS 83,553.33 and the rebate 5,416.67:
        # one payable 88,970.00; mark0 +78,913.97; cost of dealing 78,913.97 - 83,553.33 = -4,639.36; IM 200,000.00
        self.assertEqual((8355333, 7891397, -463936, 20000000, -8897000), (p["upfront_c"], p["mark0_c"], p["cost_of_dealing_c"], p["im_c"],
                                                                           p["initial_payment"]["amount_c"]))
        # the funding check covers the IM and the payable initial payment together: 200,000.00 + 88,970.00 = 288,970.00
        self.assertEqual((28897000, True), (p["funding"]["need_c"], p["funding"]["ok"]))
        body = self.book(SELL, "ui:cdsn-book-sell")
        cid = body["contract_id"]
        self.assertEqual([("1335", 8355333), ("1225", 541667), ("2100", -8897000)], self.lines(f"otc:{cid}:upfront"))
        st = {r["kind"]: r for r in self.q("SELECT * FROM accrual_state WHERE ref_id=?", (cid,))}
        self.assertEqual((541667, "0.01", "2031-12-20"), (st["swaprcv"]["cum_c"], st["swaprcv"]["rate"], st["swappay"]["accrued_through"]))
        terms = json.loads(self.q("SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,))[0]["terms_json"])
        self.assertEqual((True, "1335", -307891397), (terms["receiver"], terms["upfront_gl"], terms["jtd_0_c"]))
        assert_integrity(self, self.rt)

    def test_05_06_premium_nights_and_the_first_coupon(self):
        rt = self.rt
        self.level()
        body = self.book()
        cid = body["contract_id"]
        rt.clock.set("2026-11-03T00:50:00-05:00")
        self.accrue(cid, date(2026, 10, 30), date(2026, 11, 2))
        # SETTLE_DUE on Tue 11-03 (T+3): the initial payment receivable comes in - Dr 1010 / Cr 1200 79,698.87
        sid = body["initial_payment"]["settle_id"]
        rt.clock.set("2026-11-03T09:00:00-05:00")
        r = execute(rt, "lifecycle.settle", {"key": sid, "settle_id": sid}, f"job:SETTLE_DUE:{sid}", actor="lifecycle")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual([("1010", 7969887), ("1200", -7969887)], self.lines(f"settle:{sid}"))
        # cumulative to_c(5,000,000 x 1 % x days / 360) from the 39 rebate days: 40 -> 5,555.56 (+138.89), 41 -> 5,694.44 (+138.88),
        # 42 -> 5,833.33 (+138.89), 43 -> 5,972.22 (+138.89); Dr 4453 / Cr 2340
        got = [self.lines(f"accr:swappay:{cid}:{d}") for d in ("2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02")]
        self.assertEqual([[("4453", 13889), ("2340", -13889)], [("4453", 13888), ("2340", -13888)], [("4453", 13889), ("2340", -13889)],
                          [("4453", 13889), ("2340", -13889)]], got)
        self.assertEqual(597222, bal(rt, PB_GS, "2340", "USD", cid))
        # the coupon of Mon 2026-12-21 before the night of 12-20 is accrued: SKIPPED ACCRUALS_PENDING (retried)
        rt.clock.set("2026-12-21T00:50:00-05:00")
        r = execute(rt, "cds.coupon", {"contract_id": cid, "date": "2026-12-21", "key": f"{cid}:2026-12-21"}, f"job:CDS_COUPON:{cid}:2026-12-21", actor="lifecycle")
        self.assertEqual(("SKIPPED", "ACCRUALS_PENDING"), (r.body["status"], r.body["code"]), r.body)
        self.assertIn((cid, "2026-12-21"), self.coupons_due())
        self.accrue(cid, date(2026, 11, 3), date(2026, 12, 20))
        # the full first coupon 09-21 -> 12-21 = 91 days: 5,000,000 x 1 % x 91 / 360 = 12,638.888... -> 12,638.89; Dr 2340 / Cr 1010
        r = execute(rt, "cds.coupon", {"contract_id": cid, "date": "2026-12-21", "key": f"{cid}:2026-12-21"}, f"job:CDS_COUPON:{cid}:2026-12-21:r1", actor="lifecycle")
        self.assertEqual(("DONE", 1263889), (r.body["status"], r.body["amount_c"]), r.body)
        self.assertEqual([("2340", 1263889), ("1010", -1263889)], self.lines(f"cdscpn:{cid}:2026-12-21"))
        reset = self.q("SELECT amount_c, detail_json FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,))[0]
        self.assertEqual((-1263889, "CDS coupon"), (reset["amount_c"], json.loads(reset["detail_json"])["kind"]))
        self.assertEqual(0, bal(rt, PB_GS, "2340", "USD", cid))
        self.assertNotIn((cid, "2026-12-21"), self.coupons_due())
        # a credit event dated in the period whose coupon is already paid: 409 EVENT_PERIOD_SETTLED, nothing written
        r = execute(rt, "cds.credit_event", {"contract_id": cid, "event_type": "BANKRUPTCY", "recovery_pct": "40", "event_date": "2026-12-18"}, "ui:cdsn-event-settled")
        self.assertEqual((409, "EVENT_PERIOD_SETTLED"), (r.http, r.body["error"]["code"]))
        self.assertEqual("OPEN", self.q("SELECT status FROM otc_contracts WHERE contract_id=?", (cid,))[0]["status"])
        assert_integrity(self, rt)

    def coupons_due(self):
        with self.rt.db.reader() as conn:
            return [tuple(k) for k in cdsn.coupon_keys(conn, self.rt.clock.epoch())]

    def test_07_credit_event_buyer(self):
        rt = self.rt
        self.level()
        body = self.book()
        cid = body["contract_id"]
        rt.clock.set("2026-11-04T10:00:00-05:00")
        self.accrue(cid, date(2026, 10, 30), date(2026, 11, 3))       # the night of 11-03 is already posted when the owner enters the event
        self.assertEqual(611111, bal(rt, PB_GS, "2340", "USD", cid))   # 5,416.67 + 138.89 + 138.88 + 138.89 + 138.89 + 138.89 = 6,111.11
        r = execute(rt, "cds.credit_event", {"contract_id": cid, "event_type": "BANKRUPTCY", "recovery_pct": "40", "event_date": "2026-11-02"}, "ui:cdsn-event-0001")
        self.assertTrue(r.ok, r.body)
        ev = r.body
        # protection 5,000,000 x (1 - 40 %) = 3,000,000.00; accrued through 11-02 = 6,111.11 - 138.89 (the late night of 11-03) = 5,972.22;
        # net receivable 3,000,000.00 - 5,972.22 = 2,994,027.78 due T+1 business day of 11-02 = Tue 11-03
        self.assertEqual((300000000, 597222, 13889, 299402778, "2026-11-03"), (ev["protection_c"], ev["accrued_c"], ev["late_c"], ev["net_c"], ev["settle_date"]))
        # Dr 2340 6,111.11 / Cr 4453 138.89 / Cr 4450 3,000,000.00 / Dr 1200 2,994,027.78 (debits 3,000,138.89 = credits 3,000,138.89)
        self.assertEqual([("2340", 611111), ("4453", -13889), ("4450", -300000000), ("1200", 299402778)], self.lines(f"otc:{cid}:credit"))
        s = self.q("SELECT amount_c, gl, due_date, leg, ref_id FROM settlements WHERE leg='CREDIT EVENT'")
        self.assertEqual([(299402778, "1200", "2026-11-03", "CREDIT EVENT", cid)], [(x["amount_c"], x["gl"], x["due_date"], x["leg"], x["ref_id"]) for x in s])
        # then the close: the cost basis Dr 2345 / Cr 4450 74,282.20 and the IM 200,000.00 back to cash
        self.assertEqual([("2345", 7428220), ("4450", -7428220)], self.lines(f"otc:{cid}:credit-close"))
        self.assertEqual([("1010", 20000000), ("1330", -20000000)], self.lines(f"im:{cid}:release"))
        row = self.q("SELECT status, im_c, mark_c, vm_balance_c FROM otc_contracts WHERE contract_id=?", (cid,))[0]
        self.assertEqual(("TERMINATED", 0, 0, 0), (row["status"], row["im_c"], row["mark_c"], row["vm_balance_c"]))
        for gl in ("2340", "2345", "1330", "1320", "2330", "1335", "1225"):
            self.assertEqual(0, bal(rt, PB_GS, gl, "USD", cid), gl)
        # realised 4450 (INCOME, credit-normal): 3,000,000.00 + 74,282.20 = 3,074,282.20; premium 4453 (EXPENSE): 694.44 accrued - 138.89 reversed = 555.55
        self.assertEqual(307428220, bal(rt, PB_GS, "4450", "USD", cid))
        self.assertEqual(55555, bal(rt, PB_GS, "4453", "USD", cid))
        self.assertEqual([], self.q("SELECT * FROM accrual_state WHERE ref_id=?", (cid,)))
        self.assertEqual("EXPIRED", self.q("SELECT status FROM instruments WHERE instrument_id=?", (cid,))[0]["status"])
        self.assertEqual(["CDS_CREDIT_EVENT"], [e["type"] for e in self.q("SELECT type FROM events WHERE ref_id=? AND type IN ('CDS_CREDIT_EVENT','OTC_TERMINATED')", (cid,))])
        p = json.loads(self.q("SELECT payload_json FROM events WHERE type='CDS_CREDIT_EVENT'")[0]["payload_json"])
        for k in ("reference_entity", "event_date", "recovery_pct", "defaulted_c", "protection_c", "accrued_c", "net_c", "settle_date", "settle_id", "notional_before_c",
                  "notional_after_c", "label", "event_type", "event_words", "contract_closed"):
            self.assertIn(k, p, k)
        self.assertEqual(("ORACLE CORP", 500000000, 0, True, "bankruptcy"), (p["reference_entity"], p["notional_before_c"], p["notional_after_c"], p["contract_closed"],
                                                                              p["event_words"]))
        self.assertEqual([ORCL_CIK], [x["cik"] for x in cdsn.credit_events_of(rt, [cid])[cid]])
        steps = [s["step"] for s in swaps.contract_timeline(rt, cid)]
        self.assertEqual("CREDIT_EVENT", steps[-1])
        assert_integrity(self, rt)
        # a second event: the contract is TERMINATED
        r = execute(rt, "cds.credit_event", {"contract_id": cid, "event_type": "BANKRUPTCY", "recovery_pct": "40", "event_date": "2026-11-02"}, "ui:cdsn-event-0002")
        self.assertEqual((409, "NOT_OPEN"), (r.http, r.body["error"]["code"]))

    def test_08_credit_event_seller(self):
        rt = self.rt
        self.level()
        cid = self.book(SELL, "ui:cdsn-book-sell")["contract_id"]
        rt.clock.set("2026-11-04T10:00:00-05:00")
        self.accrue(cid, date(2026, 10, 30), date(2026, 11, 3), kind="swaprcv")
        r = execute(rt, "cds.credit_event", {"contract_id": cid, "event_type": "FAILURE_TO_PAY", "recovery_pct": "40", "event_date": "2026-11-02"}, "ui:cdsn-event-sell")
        self.assertTrue(r.ok, r.body)
        # the mirror: Cr 1225 6,111.11 / Dr 4453 138.89 / Dr 4450 3,000,000.00 / Cr 2100 2,994,027.78 (net payable = 5,972.22 - 3,000,000.00)
        self.assertEqual(-299402778, r.body["net_c"])
        self.assertEqual([("1225", -611111), ("4453", 13889), ("4450", 300000000), ("2100", -299402778)], self.lines(f"otc:{cid}:credit"))
        # the cost basis the seller PAID (1335, 83,553.33) released into 4450: Dr 4450 / Cr 1335
        self.assertEqual([("4450", 8355333), ("1335", -8355333)], self.lines(f"otc:{cid}:credit-close"))
        for gl in ("1225", "1335", "1330"):
            self.assertEqual(0, bal(rt, PB_GS, gl, "USD", cid), gl)
        assert_integrity(self, rt)

    def test_credit_event_refusals(self):
        rt = self.rt
        self.level()
        cid = self.book()["contract_id"]
        base = {"contract_id": cid, "event_type": "BANKRUPTCY", "recovery_pct": "40", "event_date": "2026-10-29"}
        cases = ((dict(base, event_date="2026-10-30"), 400, "TERMS"), (dict(base, event_date="2026-10-28"), 400, "TERMS"),
                 (dict(base, event_type="RESTRUCTURING"), 400, "TERMS"), (dict(base, recovery_pct="101"), 400, "TERMS"),
                 (dict(base, recovery_pct=None), 400, "TERMS"))
        for i, (payload, http, code) in enumerate(cases):
            r = execute(rt, "cds.credit_event", payload, f"ui:cdsn-bad-event-{i:02d}")
            self.assertEqual((http, code), (r.http, r.body["error"]["code"]), payload)
        r = execute(rt, "cds.credit_event", base, "ui:cdsn-event-lifecycle", actor="lifecycle")
        self.assertEqual((403, "CREDIT_EVENT_OWNER_ONLY"), (r.http, r.body["error"]["code"]))
        self.assertEqual("OPEN", self.q("SELECT status FROM otc_contracts WHERE contract_id=?", (cid,))[0]["status"])

    def test_no_as_of_booking(self):
        # plan 2.1: CDS is not an as-of kind (its trade date's model inputs are not recorded): 409 ASOF_INPUT_MISSING, nothing written
        self.level()
        a = {"trade_date": "2026-10-28", "value_date": "2026-10-28", "exec_ts": "2026-10-28T11:00:00-04:00", "batch_id": "B1-TEST", "line_key": "B1-TEST:1"}
        r = execute(self.rt, "otc.book_as_of", {"account_id": PB_GS, "kind": "CDS", "terms": dict(BUY), "as_of": a}, "imp:cdsn-asof-0001", actor="import")
        self.assertEqual((409, "ASOF_INPUT_MISSING"), (r.http, r.body["error"]["code"]))
        self.assertEqual([], self.q("SELECT * FROM otc_contracts"))

    def test_09_jump_to_default(self):
        # JTD = sign x (1 - 40 %) x N - MV: buyer 3,000,000.00 - (-78,913.97) = +3,078,913.97; the seller -3,000,000.00 - 78,913.97 = -3,078,913.97
        self.assertEqual((307891397, -307891397), (self.preview()["jtd_c"], self.preview(SELL)["jtd_c"]))

    def test_10_negotiated_basis_moves_with_the_model(self):
        b = self.level("ui:cdsn-neg-84", negotiated_mid_bp="84.00")
        # basis = 84.00 - 65.47 = +18.53
        self.assertEqual(("NEGOTIATED", "18.5300", "84.00"), (b["level"]["level_kind"], b["level"]["basis_bp"], b["level"]["negotiated_mid_bp"]))
        self.assertEqual("84.0000", self.preview()["mid_spread_bp"])
        self.model.spread[ORCL_CIK] = D("66.10")
        # the next model 66.10 + 18.53 = 84.63
        p = self.preview()
        self.assertEqual("84.6300", p["mid_spread_bp"])
        self.assertTrue(p["level"]["rule"].startswith("mid 84.63 bp = model 66.10 bp + your negotiated basis +18.53 bp (set 2026-10-29 14:00 ET)"), p["level"]["rule"])
        self.level("ui:cdsn-clear", clear=True)
        p = self.preview()
        self.assertEqual(("66.1000", False, "DEALING_LEVEL_UNCONFIRMED"), (p["mid_spread_bp"], p["bookable"], p["blockers"][0]["code"]))
        self.assertEqual(2, len(self.settings_changed()))
        r = execute(self.rt, "cds.basis", {"account_id": PB_GS, "cik": ORCL_CIK, "clear": True}, "ui:cdsn-clear-again")
        self.assertEqual((400, "NOTHING_TO_UPDATE"), (r.http, r.body["error"]["code"]))

    def test_11_distressed_needs_a_negotiated_mid(self):
        self.model.spread[ORCL_CIK] = D("2300.22")
        self.model.quality[ORCL_CIK] = "distressed"
        with self.assertRaises(Exception) as cm:
            self.preview()
        self.assertEqual("DISTRESSED_NEEDS_NEGOTIATED_MID", getattr(cm.exception, "code", None))
        self.assertIn("needs your negotiated mid", cm.exception.message)
        r = execute(self.rt, "cds.basis", {"account_id": PB_GS, "cik": ORCL_CIK, "deal_at_model": True}, "ui:cdsn-distressed-model")
        self.assertEqual((409, "DISTRESSED_NEEDS_NEGOTIATED_MID"), (r.http, r.body["error"]["code"]))
        self.level("ui:cdsn-distressed-neg", negotiated_mid_bp="2500.00")
        p = self.preview()
        self.assertEqual((True, "distressed", "2500.0000"), (p["bookable"], p["quality"], p["mid_spread_bp"]))

    def test_12_vm_close_pending_then_one_vm_then_caught_up(self):
        rt = self.rt
        self.level()
        cid = self.book()["contract_id"]
        self.model.vm["2026-10-29"] = "CLOSE_PENDING"
        rt.clock.set("2026-10-29T17:06:00-04:00")
        r = self.vm(cid, "2026-10-29")
        self.assertEqual(("SKIPPED", "CLOSE_PENDING", True), (r.body["status"], r.body["code"], r.body["quiet"]), r.body)
        self.assertEqual([], self.q("SELECT * FROM otc_vm WHERE contract_id=?", (cid,)))
        # the inputs of 10-29 arrive: the model of that date is 66.47 bp -> the buyer's value as of 10-29 on Appendix C's curve:
        # (66.47 - 100) bp x 5,000,000 = -16,765 x RPV01(66.47) 4.5688719707 = -76,597.14; the first VM moves it all (reference 0): CTM, a
        # negative mark -> VM posted 76,597.14 with business date 10-29
        self.model.vm["2026-10-29"] = "66.47"
        rt.clock.set("2026-10-30T09:00:00-04:00")
        r = self.vm(cid, "2026-10-29", ":r1")
        self.assertEqual("DONE", r.body["status"], r.body)
        self.assertEqual((-7659714, -7659714), (r.body["mark_c"], r.body["delta_c"]))
        self.assertEqual(self.mark0("66.47"), r.body["mark_c"])
        self.assertEqual([("1320", 7659714), ("1010", -7659714)], self.lines(f"swapvm:{cid}:2026-10-29"))
        self.assertEqual("2026-10-29", self.q("SELECT business_date FROM journal_entries WHERE idem_key=?", (f"swapvm:{cid}:2026-10-29",))[0]["business_date"])
        self.assertEqual(1, len(self.q("SELECT * FROM otc_vm WHERE contract_id=?", (cid,))))
        # the next date never gets its inputs (FRED skipped it): CAUGHT_UP, quiet and terminal
        self.model.vm["2026-10-30"] = "CAUGHT_UP"
        rt.clock.set("2026-10-31T09:00:00-04:00")
        r = self.vm(cid, "2026-10-30")
        self.assertEqual(("SKIPPED", "CAUGHT_UP", True, False), (r.body["status"], r.body["code"], r.body["quiet"], r.body["retry"]), r.body)
        with rt.db.reader() as conn:
            due = swaps.vm_keys(conn, rt.clock.epoch())
        self.assertNotIn((cid, "2026-10-30"), due)
        assert_integrity(self, rt)

    def test_13_termination_at_mid_minus_the_half_spread(self):
        rt = self.rt
        self.level()
        body = self.book()
        cid = body["contract_id"]
        rt.clock.set("2026-11-02T10:00:00-05:00")
        r = execute(rt, "otc.terminate", {"contract_id": cid}, "ui:cdsn-term-0001")
        self.assertTrue(r.ok, r.body)
        t = r.body
        # the nights owed 10-30 .. 11-02 are accrued first (4 nights: 138.89 + 138.88 + 138.89 + 138.89 = 555.55; 2340 = 5,416.67 + 555.55 = 5,972.22)
        self.assertEqual((4, 597222, "2026-11-02"), (len(t["caught_up"]), t["accrued_pay_c"], t["accrued_through"]))
        # the buyer sells protection back at mid 65.47 - 2.00 = 63.47, valued on 11-02 (hand-derived with swaps_cds.value_c)
        self.assertEqual("63.4700", t["unwind_spread_bp"])
        self.assertEqual(self.mark0("63.47", on=date(2026, 11, 2)), t["termination_value_c"])
        self.assertEqual(self.mark0("65.47", on=date(2026, 11, 2)), t["mid_value_c"])
        # the upfront received (2345) is released into the swap result: Dr 2345 74,282.20 / Cr 4450
        self.assertEqual(7428220, t["cost_released_c"])
        self.assertIn(("2345", 7428220), self.lines(f"otc:{cid}:terminate"))
        for gl in ("1330", "1320", "2330", "2340", "1225", "2345", "1335"):
            self.assertEqual(0, bal(rt, PB_GS, gl, "USD", cid), gl)
        self.assertEqual("TERMINATED", self.q("SELECT status FROM otc_contracts WHERE contract_id=?", (cid,))[0]["status"])
        assert_integrity(self, rt)

    def _quote(self, terms=None, rid="ui:cdsn-rfq-0001"):
        rt = self.rt
        r = execute(rt, "otc.rfq.request", {"account_id": PB_GS, "kind": "CDS", "terms": dict(terms or BUY)}, rid)
        self.assertTrue(r.ok, r.body)
        rt.lifecycle.tick(at=rt.clock.epoch() + 30, only="RFQ_DEALER")
        row = self.q("SELECT * FROM rfqs WHERE rfq_id=?", (r.body["rfq_id"],))[0]
        self.assertEqual("QUOTED", row["status"], row)
        return r.body["rfq_id"], json.loads(row["quote_json"])

    def test_14_rfq_books_at_the_quote(self):
        rt = self.rt
        self.level()
        rid, quote = self._quote()
        self.assertEqual({"dealt_spread_bp": "67.4700", "coupon_bp": "100", "maturity_date": "2031-12-20", "tier": "A_OR_BETTER"}, quote["firm"])
        self.assertTrue(quote["words"].startswith("ORCL 5Y CDS (ORACLE CORP) 67.4700 bp (MODEL mid 65.4700 bp); you receive USD"), quote["words"])
        self.model.spread[ORCL_CIK] = D("66.47")                         # the model moves +1 bp before the accept
        a = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:cdsn-rfq-accept")
        self.assertTrue(a.ok, a.body)
        cid = a.body["contract_id"]
        terms = json.loads(self.q("SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,))[0]["terms_json"])
        # booked at the quoted 67.47 (firm; QUOTE_MOVED never fires); the mark is the mid of the booking instant, 66.47: -76,597.14
        self.assertEqual(("67.4700", "66.4700", rid, -7659714), (terms["deal_spread_bp"], terms["mid_spread_bp"], terms["quoted"]["rfq_id"], terms["mark0_c"]))
        self.assertEqual("ACCEPTED", self.q("SELECT status FROM rfqs WHERE rfq_id=?", (rid,))[0]["status"])
        self.assertEqual("PENDING_CONFIRM", self.q("SELECT confirm_status FROM otc_contracts WHERE contract_id=?", (cid,))[0]["confirm_status"])
        assert_integrity(self, rt)

    def test_15_dealing_level_guard(self):
        rt = self.rt
        p = self.preview()
        # every number, not bookable, the blocker in words first
        self.assertEqual((False, "DEALING_LEVEL_UNCONFIRMED", "67.4700", -7428220, 20000000), (p["bookable"], p["blockers"][0]["code"], p["deal_spread_bp"],
                                                                                               p["upfront_c"], p["im_c"]))
        msg = p["blockers"][0]["message"]
        self.assertTrue(msg.startswith("No market level is known for ORACLE CORP (ORCL): its spread is a MODEL (65.47 bp, AA-like) and has not been validated"), msg)
        r = execute(rt, "otc.rfq.request", {"account_id": PB_GS, "kind": "CDS", "terms": dict(BUY)}, "ui:cdsn-rfq-guard")
        self.assertEqual((409, "DEALING_LEVEL_UNCONFIRMED", msg), (r.http, r.body["error"]["code"], r.body["error"]["message"]))
        r = execute(rt, "otc.book", {"account_id": PB_GS, "kind": "CDS", "terms": dict(BUY)}, "ui:cdsn-book-guard")
        self.assertEqual((409, "DEALING_LEVEL_UNCONFIRMED", msg), (r.http, r.body["error"]["code"], r.body["error"]["message"]))
        self.assertEqual(([], [], []), (self.q("SELECT * FROM otc_contracts"), self.q("SELECT * FROM rfqs"),
                                        self.q("SELECT * FROM journal_entries WHERE ref_type='otc'")))
        self.level()
        body = self.book()
        self.assertEqual(("65.4700", "67.4700"), (body["preview"]["mid_spread_bp"], body["preview"]["deal_spread_bp"]))
        self.level("ui:cdsn-neg-84", negotiated_mid_bp="84.00")
        self.assertEqual(("84.0000", "86.0000"), (self.preview()["mid_spread_bp"], self.preview()["deal_spread_bp"]))
        self.level("ui:cdsn-clear", clear=True)
        self.assertEqual("DEALING_LEVEL_UNCONFIRMED", self.preview()["blockers"][0]["code"])
        # a level cleared between the quote and the accept: the accept is refused and writes nothing
        self.level("ui:cdsn-model-2", deal_at_model=True)
        rid, _q = self._quote(rid="ui:cdsn-rfq-0002")
        self.level("ui:cdsn-clear-2", clear=True)
        a = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:cdsn-rfq-accept-cleared")
        self.assertEqual((409, "DEALING_LEVEL_UNCONFIRMED"), (a.http, a.body["error"]["code"]))
        self.assertEqual(1, len(self.q("SELECT * FROM otc_contracts")))
        # MODEL, NEGOTIATED, clear, MODEL, clear: one SETTINGS_CHANGED each
        self.assertEqual(5, len(self.settings_changed()))
        assert_integrity(self, rt)

    def test_16_firm_quote_terms(self):
        rt = self.rt
        self.level()
        rid, quote = self._quote()
        self.assertEqual(("100", "2031-12-20", "A_OR_BETTER"), (quote["firm"]["coupon_bp"], quote["firm"]["maturity_date"], quote["firm"]["tier"]))
        # the model moves to 190.00 bp (BB-like: the default coupon would be 500 and the tier BB) before the accept
        self.model.spread[ORCL_CIK] = D("190.00")
        a = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:cdsn-rfq-firm")
        self.assertTrue(a.ok, a.body)
        cid = a.body["contract_id"]
        row = self.q("SELECT terms_json, im_c, maturity_date FROM otc_contracts WHERE contract_id=?", (cid,))[0]
        terms = json.loads(row["terms_json"])
        # the booking keeps the quoted coupon 100, maturity, spread 67.47 and tier: IM 4 % x 5,000,000 = 200,000.00
        self.assertEqual(("100", "2031-12-20", "67.4700", "A_OR_BETTER", 20000000), (terms["coupon_bp"], row["maturity_date"], terms["deal_spread_bp"], terms["tier"],
                                                                                     row["im_c"]))
        assert_integrity(self, rt)

    def test_17_basis_effect_on_an_open_contract(self):
        rt = self.rt
        self.level()
        cid = self.book()["contract_id"]
        entries0 = len(self.q("SELECT entry_id FROM journal_entries"))
        eff = cdsn.basis_effect(rt, PB_GS, ORCL_CIK, negotiated_mid_bp="265.47")
        # at 65.47 the contract is worth -78,913.97; at 265.47 (basis +200.00) by full revaluation (swaps_cds.value_c) +348,770.40
        v0, v1 = self.mark0("65.47"), self.mark0("265.47")
        self.assertEqual((-7891397, 34877040), (v0, v1))
        self.assertEqual([{"contract_id": cid, "value_before_c": v0, "value_after_c": v1, "effect_c": v1 - v0}],
                         [{k: c[k] for k in ("contract_id", "value_before_c", "value_after_c", "effect_c")} for c in eff["contracts"]])
        self.assertEqual(v1 - v0, eff["effect_total_c"])
        b = self.level("ui:cdsn-neg-265", negotiated_mid_bp="265.47")
        effect = json.loads(self.settings_changed()[-1]["payload_json"])["effect"]
        self.assertEqual({"contracts": [{"contract_id": cid, "value_before_c": v0, "value_after_c": v1, "effect_c": v1 - v0}], "effect_total_c": v1 - v0}, effect)
        self.assertEqual(effect, b["effect"])
        # no posting is restated: the same journal entries, the stored mark unchanged until the next VM
        self.assertEqual(entries0, len(self.q("SELECT entry_id FROM journal_entries")))
        self.assertEqual(-7891397, self.q("SELECT mark_c FROM otc_contracts WHERE contract_id=?", (cid,))[0]["mark_c"])
        assert_integrity(self, rt)

    def test_18_wide_negotiated_mid_keeps_the_coupon_and_moves_the_tier(self):
        eff = cdsn.basis_effect(self.rt, PB_GS, ORCL_CIK, negotiated_mid_bp="315.00")
        # 315.00 is B-like (|ln(315 / 322)| = 0.022 < BB 0.522): IM 12 %, half-spread 15.0; before: A_OR_BETTER 4 %, 2.0; the default coupon stays 100
        self.assertEqual(("A_OR_BETTER", "0.04", "2.0", "B", "0.12", "15.0", 100),
                         (eff["before"]["tier"], eff["before"]["im_pct"], eff["before"]["half_spread_bp"], eff["after"]["tier"], eff["after"]["im_pct"],
                          eff["after"]["half_spread_bp"], eff["coupon_default_bp"]))
        self.level("ui:cdsn-neg-315", negotiated_mid_bp="315.00")
        p = self.preview()
        # mid 315.00 + 15.00 = 330.00 dealt; coupon 100 (the MODEL bucket AA); IM 12 % x 5,000,000 = 600,000.00
        self.assertEqual(("315.0000", "B", "330.0000", "100", "default from the MODEL bucket", 60000000),
                         (p["mid_spread_bp"], p["tier"], p["deal_spread_bp"], p["coupon_bp"], p["coupon_rule"], p["im_c"]))

    def _pos_rows(self, cids, mv=None):
        out = []
        for cid in cids:
            row = self.q("SELECT account_id, mark_c FROM otc_contracts WHERE contract_id=?", (cid,))[0]
            out.append(types.SimpleNamespace(kind="SWAP", extra={"kind": "CDS"}, instrument_id=cid, account_id=row["account_id"],
                                             mv_c=row["mark_c"] if mv is None else mv))
        return out

    def test_19_equity_scenarios_through_the_model(self):
        rt = self.rt
        self.level("ui:cdsn-neg-7547", negotiated_mid_bp="75.47")          # basis +10.00 bp to the MODEL 65.47
        cid = self.book()["contract_id"]
        # hand values of the stub for S x 0.7 / 1.3 / 1.5 (the MODEL re-run); each contract is revalued at those + the basis 10.00
        self.model.shocks = {"0.7": "160.31", "1.3": "93.85", "1.5": "83.09"}
        base = self.mark0("75.47")
        dn30, up30, up50 = (self.mark0(x) - base for x in ("170.31", "103.85", "93.09"))
        blk = cdsn.risk_block(rt, self._pos_rows([cid]), 2510000000)
        nm = blk["by_name"][0]
        self.assertEqual((dn30, up30, up50), (nm["eq_dn30_pnl_c"], nm["eq_up30_pnl_c"], nm["eq_up50_pnl_c"]))
        sc = {s["name"]: s["pnl_c"] for s in blk["scenarios"]}
        self.assertEqual(["-0.30", "0.30", "-0.30", "0.30", "0.50"], [s["equity_shock"] for s in blk["scenarios"] if "equity_shock" in s])
        self.assertEqual(dn30, sc["single-name equities -30 % (spreads through the model)"])
        self.assertEqual(up30, sc["single-name equities +30 % (spreads through the model)"])
        self.assertEqual((dn30, up30, up50), (sc["ORCL equity -30 % alone (largest jump-to-default)"], sc["ORCL equity +30 % alone (largest jump-to-default)"],
                                              sc["ORCL equity +50 % alone (largest jump-to-default)"]))
        # the spread shocks: full revaluation at the mid +25 bp
        self.assertEqual(self.mark0("100.47") - base, sc["single-name spreads +25 bp"])
        self.model.shocks = {"0.7": "160.31", "1.3": "93.85"}                 # the model cannot be re-run at x 1.5: unavailable, never 0
        blk = cdsn.risk_block(rt, self._pos_rows([cid]), 2510000000)
        self.assertIsNone(blk["by_name"][0]["eq_up50_pnl_c"])
        row = [s for s in blk["scenarios"] if s["name"] == "ORCL equity +50 % alone (largest jump-to-default)"][0]
        self.assertIsNone(row["pnl_c"])
        self.assertIn("unavailable", row["note"])

    def test_risk_block_jtd_and_the_concentration_warning(self):
        rt = self.rt
        self.level()
        b = self.book()["contract_id"]
        s = self.book(SELL, "ui:cdsn-book-sell")["contract_id"]
        blk = cdsn.risk_block(rt, self._pos_rows([b, s]), 2000000000)
        rows = {r["contract_id"]: r for r in blk["rows"]}
        # JTD buyer +3,078,913.97, seller -3,078,913.97 (the MV of each at the booking mark); net 0 for the name
        self.assertEqual((307891397, -307891397), (rows[b]["jtd_c"], rows[s]["jtd_c"]))
        self.assertEqual((0, 2, 1000000000, 0), (blk["by_name"][0]["jtd_c"], blk["by_name"][0]["contracts"], blk["by_name"][0]["gross_notional_c"],
                                                 blk["by_name"][0]["net_exposure_c"]))
        self.assertEqual([], blk["warnings"])
        blk = cdsn.risk_block(rt, self._pos_rows([s]), 2000000000)
        # the seller alone on a NAV of 20,000,000.00: a loss of 3,078,913.97 = 15.39 % of NAV, above 10 %
        self.assertEqual(["CDS_JTD_CONCENTRATION"], [w["code"] for w in blk["warnings"]])
        self.assertEqual("ORACLE CORP jump to default would lose USD 3,078,913.97 (15.39 % of NAV, above 10 %)", blk["warnings"][0]["message"])
        self.assertEqual(("0.1539", "0.10"), (blk["warnings"][0]["value"], blk["warnings"][0]["threshold"]))
        self.assertEqual(("default of ORCL (largest jump-to-default loss)", -307891397), (blk["scenarios"][-1]["name"], blk["scenarios"][-1]["pnl_c"]))

    def test_terms_edit_and_refusals_in_words(self):
        rt = self.rt
        r = execute(rt, "cds.terms", {"account_id": PB_GS, "tier": "A_OR_BETTER", "im_pct": "5 %", "half_spread_bp": "2.5"}, "ui:cdsn-terms-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual({"im_pct": "0.05", "half_spread_bp": "2.5"}, r.body["after"])
        # 65.47 + 2.50 = 67.97; IM 5 % x 5,000,000 = 250,000.00
        p = self.preview()
        self.assertEqual(("67.9700", 25000000), (p["deal_spread_bp"], p["im_c"]))
        self.assertEqual("NOTHING_TO_UPDATE", execute(rt, "cds.terms", {"account_id": PB_GS, "tier": "A_OR_BETTER", "im_pct": "0.05"}, "ui:cdsn-terms-2").body["error"]["code"])
        self.assertEqual("TERMS_VALUE", execute(rt, "cds.terms", {"account_id": PB_GS, "tier": "BBB", "half_spread_bp": "501"}, "ui:cdsn-terms-3").body["error"]["code"])
        self.assertEqual("TERMS_FIELD", execute(rt, "cds.terms", {"account_id": PB_GS, "tier": "AAA", "im_pct": "0.05"}, "ui:cdsn-terms-4").body["error"]["code"])
        self.assertEqual(1, len(self.settings_changed("cds_terms")))
        # a name the entities refuse: NOT_COVERED with the reason in words; an unknown ticker: 404
        with self.assertRaises(Exception) as cm:
            self.preview(dict(BUY, cik=FORD_CIK, ticker="F"))
        self.assertEqual(("NOT_COVERED", 409), (cm.exception.code, cm.exception.http))
        self.assertIn("FORD MOTOR CO (F): not offered: no debt reported", cm.exception.message)
        with self.assertRaises(Exception) as cm:
            self.preview({"ticker": "ZZZZ", "side": "BUY", "notional": "1000000"})
        self.assertEqual(("ENTITY_UNKNOWN", 404), (cm.exception.code, cm.exception.http))
        # the model says the name is not dealable as of its date (a balance sheet gone stale): a new deal is refused with the model's words
        self.model.undealable[ORCL_CIK] = "not offered: balance sheet 2025-06-30 is 16 months old"
        with self.assertRaises(Exception) as cm:
            self.preview()
        self.assertEqual(("NOT_COVERED", "ORACLE CORP (ORCL): not offered: balance sheet 2025-06-30 is 16 months old"), (cm.exception.code, cm.exception.message))
        self.model.undealable.clear()
        # inputs being fetched: 409 with retry_ms (the preview routes answer 202)
        self.model.pending.add(ORCL_CIK)
        with self.assertRaises(Exception) as cm:
            self.preview()
        self.assertEqual(("INPUTS_PENDING", 1500), (cm.exception.code, cm.exception.details.get("retry_ms")))
        # a dealing level needs the model mid now: 409 INPUTS_PENDING ("preview the name first"), nothing stored
        r = execute(rt, "cds.basis", {"account_id": PB_GS, "cik": ORCL_CIK, "negotiated_mid_bp": "84.00"}, "ui:cdsn-basis-pending")
        self.assertEqual((409, "INPUTS_PENDING"), (r.http, r.body["error"]["code"]))
        self.assertIn("preview the name first", r.body["error"]["message"])
        self.assertEqual([], self.q("SELECT * FROM cds_basis"))

    def test_routes_desk_preview_basis_effect(self):
        from finsim.terminal import api
        rt = self.rt
        router = api.Router(rt)
        st, body, _h = router.handle("GET", f"/api/otc/cds/desk?account_id={PB_GS}")
        self.assertEqual(200, st, body)
        # the tiers seeded from GS (plan 5.1): A_OR_BETTER 4 % / 2.0 bp ... CCC 25 % / 40.0 bp
        self.assertEqual(("GS", D("0.04"), D("2.0"), D("0.25"), "MODEL", [1, 2, 3, 4, 5, 7, 10], "2031-12-20"),
                         (body["broker"], D(body["tiers"]["A_OR_BETTER"]["im_pct"]), D(body["tiers"]["A_OR_BETTER"]["half_spread_bp"]),
                          D(body["tiers"]["CCC"]["im_pct"]), body["label"], body["tenors"], body["roll"]["maturities"]["5"]))
        self.assertEqual("seed (assumed)", body["tiers"]["BBB"]["source"])
        st, body, _h = router.handle("POST", "/api/otc/cds/preview", None, {"account_id": PB_GS, "terms": dict(BUY)})
        self.assertEqual((200, False, "DEALING_LEVEL_UNCONFIRMED"), (st, body["bookable"], body["blockers"][0]["code"]))
        st, body, _h = router.handle("POST", "/api/otc/cds/preview", None, {"account_id": PB_GS, "terms": dict(BUY, cik=FORD_CIK, ticker="F")})
        self.assertEqual((200, True, "NOT_COVERED"), (st, body["refused"], body["code"]))
        st, body, _h = router.handle("POST", "/api/otc/preview", None, {"account_id": PB_GS, "kind": "CDS", "terms": dict(BUY)})
        self.assertEqual((200, "67.4700"), (st, body["deal_spread_bp"]))
        self.model.pending.add(ORCL_CIK)
        st, body, _h = router.handle("POST", "/api/otc/cds/preview", None, {"account_id": PB_GS, "terms": dict(BUY)})
        self.assertEqual((202, 1500, "fetching"), (st, body["retry_ms"], body["status"]))
        st, body, _h = router.handle("POST", "/api/otc/preview", None, {"account_id": PB_GS, "kind": "CDS", "terms": dict(BUY)})
        self.assertEqual(202, st, body)
        self.model.pending.discard(ORCL_CIK)
        st, body, _h = router.handle("GET", f"/api/otc/cds/basis/effect?account_id={PB_GS}&cik={ORCL_CIK}&negotiated_mid_bp=300.00")
        # before: no level, mid = MODEL 65.47 (AA, A_OR_BETTER); after: 300.00 is B-like (|ln(300 / 322)| = 0.071 < BB 0.473): IM 12 %; coupon 100
        self.assertEqual((200, None, "A_OR_BETTER", "B", "B", "0.12", 100), (st, body["before"]["level_kind"], body["before"]["tier"], body["after"]["bucket"],
                                                                             body["after"]["tier"], body["after"]["im_pct"], body["coupon_default_bp"]))
        # a name opened by its ticker (no CIK in the request yet): the level resolves the ticker through the reference entities
        st, body, _h = router.handle("GET", f"/api/otc/cds/basis/effect?account_id={PB_GS}&cik=&ticker=ORCL&deal_at_model=1")
        self.assertEqual((200, ORCL_CIK, "MODEL"), (st, body["cik"], body["action"]))
        st, body, _h = router.handle("POST", "/api/otc/cds/basis", None, {"account_id": PB_GS, "cik": None, "ticker": "ORCL", "deal_at_model": True,
                                                                          "request_id": "ui:cdsn-route-level"})
        self.assertEqual((200, "MODEL", ORCL_CIK), (st, body["level"]["level_kind"], body["cik"]))
        st, body, _h = router.handle("POST", "/api/otc/cds/terms", None, {"account_id": PB_GS, "tier": "BBB", "im_pct": "0.055", "request_id": "ui:cdsn-route-terms"})
        self.assertEqual(200, st, body)

    def test_b4r2_a_refused_basis_effect_answers_200_in_words(self):
        """B4-R2 (integration; V104R2-BW-05): both level dialogs call the effect route as the owner types, so a refusal (409 or 400) answers
        200 {refused: true, ok: false, code, message, details} and no 4xx reaches the browser console; the 202 retry and a 404 are unchanged."""
        from finsim.terminal import api
        router = api.Router(self.rt)
        # a MODEL confirmation of a distressed name: 409 DISTRESSED_NEEDS_NEGOTIATED_MID before, now 200 refused with the words
        self.model.quality[ORCL_CIK] = "distressed"
        st, body, _h = router.handle("GET", f"/api/otc/cds/basis/effect?account_id={PB_GS}&cik={ORCL_CIK}&deal_at_model=1")
        self.assertEqual((200, True, False, "DISTRESSED_NEEDS_NEGOTIATED_MID"), (st, body.get("refused"), body.get("ok"), body.get("code")), body)
        self.assertIn("ORACLE CORP", body["message"])
        self.model.quality.clear()
        # a half-typed mid (400 TERMS before): 200 refused, the field named in the details
        st, body, _h = router.handle("GET", f"/api/otc/cds/basis/effect?account_id={PB_GS}&cik={ORCL_CIK}&negotiated_mid_bp=3x")
        self.assertEqual((200, True, "TERMS", "negotiated_mid_bp"), (st, body.get("refused"), body.get("code"), (body.get("details") or {}).get("field")), body)
        # the model mid cannot be computed now (inputs being fetched): still 202 with retry_ms, not a refusal
        self.model.pending.add(ORCL_CIK)
        st, body, _h = router.handle("GET", f"/api/otc/cds/basis/effect?account_id={PB_GS}&cik={ORCL_CIK}&negotiated_mid_bp=300.00")
        self.assertEqual((202, 1500), (st, body.get("retry_ms")), body)
        self.model.pending.discard(ORCL_CIK)
        # an unknown ticker stays a 404 (the dialogs open only on a listed name)
        st, body, _h = router.handle("GET", f"/api/otc/cds/basis/effect?account_id={PB_GS}&cik=&ticker=ZZZZ&deal_at_model=1")
        self.assertEqual(404, st, body)
        # nothing was written by any of these reads
        self.assertEqual([], self.q("SELECT * FROM cds_basis"))


if __name__ == "__main__":
    unittest.main()
