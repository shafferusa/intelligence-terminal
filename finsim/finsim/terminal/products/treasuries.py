"""Debt Instruments marketplace data (Build 2 P07; owner 2026-10-21: "no bonds in debt instruments available??? thought this
was suppose to be a market place"): the REAL US Treasury master, the generic corporates, the generic foreign government
benchmarks, the ``DebtStore`` of observed prices and curves, the light commands that persist them and the daily refresher.

Conventions (every number shown comes from a named free source with its date; nothing is invented)
- TREASURY MASTER: every outstanding marketable US Treasury from fiscaldata ``v1/debt/mspd/mspd_table_3_market`` (the
  latest monthly record: CUSIP, coupon, original issue date, maturity, coupon months, amount outstanding) plus the
  securities issued since that record date from ``v1/accounting/od/auctions_query`` (coupon known once auctioned) plus any
  CUSIP TreasuryDirect FedInvest prices that neither lists yet (its type, rate and maturity; issue date unknown).  Only
  securities issued on or before today and maturing after today are listed.  Id ``UST-<CUSIP>``, symbol the CUSIP, kind
  BOND, USD, multiplier 0.01 (qty = face, price per 100), lot 1,000, US_BONDS, T+1, account kinds DEBT + PB.  Names:
  "US Treasury 4.125 % 15-Aug-2035 (91282CNT4)", "US Treasury bill 21-Jan-2027 (912797TM9)", "US Treasury TIPS 1.875 %
  15-Jul-2035 (...)", "US Treasury FRN 31-Jan-2028 (...)".  Bills, notes and bonds are tradable (support FULL); TIPS and
  FRNs are LIMITED with the reason on the row (``spec.limited_reason``): their principal / coupon depends on the CPI index
  ratio / the weekly 13-week bill reset, which this ledger does not book.
- GENERIC CORPORATES (OA-07): there is no free source of real corporate bond terms and prices, so the Corporates tab
  lists one GENERIC bond per rating bucket x tenor (AAA AA A BBB BB B CCC x 2 / 5 / 10 / 30Y), never a real issuer.  Each
  is listed for the month (id ``CORP-<bucket>-<n>Y-<yyyymm>``, issue = the listing date, maturity = the 15th of the
  listing month n years on), coupon = its model par yield at listing (par curve yield + the bucket's ICE BofA OAS from
  FRED) rounded to the nearest 1/8 %, named "Generic A-rated USD corporate 5Y 4.375 % 15-Oct-2031 (generic: curve + ICE
  BofA A OAS)".  A bucket without an OAS print is NOT listed: the Corporates tab shows it as unavailable with the reason
  (FRED in maintenance, series not fetched yet) - never the old static spread.
- FOREIGN GOVERNMENT BENCHMARKS (OA-13, C19): generic 2 / 5 / 10 / 30Y benchmarks in the local currency, listed for the
  month like the corporates (id ``GOV-<CC>-<n>Y-<yyyymm>``): DE from the ECB euro-area AAA government PAR yield curve
  (daily; a proxy for Bunds, said so), JP from the MOF JGB yields by maturity (daily); GB / FR / IT have only FRED's OECD
  monthly 10-year average (LIMITED: shown with its month, not tradable; the Bank of England database refuses scripts and
  is never fetched).  Coupon = the source yield at listing rounded to 1/8 % (JPY: 0.1 %); DE / FR annual coupons, JP / GB
  / IT semi-annual; account kind FOREIGN_FX; T+2 in the currency (funded through the FX conversion of the Foreign Markets
  account); session FX_SPOT (OTC government bonds trade weekdays around the clock across centres: the FX session is the
  stated approximation).  Every row says "generic benchmark priced from <series> as of <date>".
- LEGACY ROWS: the old monthly model benchmarks ``UST-{tenor}-{yyyymm}`` and the universe's curated corporates /
  sovereigns / munis (invented maturities) are DELISTED by ``debt.sync`` unless an open lot or a working order references
  them; generic rows of earlier months likewise (they stay ACTIVE while held).  ``finsim/data/universe.json`` is never
  edited (the old simulation and FinSim2 read it).
- STORE: ``DebtStore`` (``services.debt_store``) keeps the latest FedInvest prices per CUSIP (persisted in ``debt_prices``,
  created through ``Database.ensure_schema_ext``; the last 10 price dates are kept) and the real / EUR AAA / JGB curves
  (persisted in ``curve_points`` through ``rates.record``), each with its source, as-of date and fetch time; a failed
  fetch keeps the last good data and records the error per part (``status()``).
- REFRESHER (``refresh(services, now)``, registered with the quote worker through ``quotes.worker.register_refresher``
  every ``REFRESH_S``; runs on the worker's fetch thread, never on a request thread, never inside the writer): the master
  once a day (and at the first run), FedInvest until today's prices are in and once more for the END OF DAY column, the
  real and foreign curves when older than the previous business day (at most every 2 h), the FRED OAS / OECD series a
  bond needs when the 6-hourly FRED slot has none yet, then ``debt.sync`` for this month's generic rows; the Yahoo yield
  records (^IRX ^FVX ^TNX ^TYX) are kept subscribed while a bond position is held so the intraday shift is fresh.
- LIGHT COMMANDS: ``debt.sync {rows, delist}`` (instrument upserts + delistings), ``debt.prices {date, rows, source}``.
"""
from __future__ import annotations

import re
import threading
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, Iterable, List, Optional, Tuple

from ..clock import ny_of
from ..commands import Command
from ..db import canonical_json

SOURCE = "treasuries"
UST_PREFIX = "UST-"
CORP_PREFIX = "CORP-"
GOV_PREFIX = "GOV-"
US_ACCOUNT_KINDS = ["DEBT", "PB"]
FOREIGN_ACCOUNT_KINDS = ["FOREIGN_FX"]
LOT = 1000
REFRESH_S = 1800
CURVE_RETRY_S = 7200
EOD_TRIES = 3
PRICE_KEEP_DAYS = 10
EIGHTH = Decimal("0.125")
TENTH = Decimal("0.1")
TYPE_WORD = {"BILL": "bill", "NOTE": "note", "BOND": "bond", "TIPS": "TIPS", "FRN": "FRN"}
LIMITED_REASON = {
    "TIPS": "inflation-indexed: shown at its real price per 100 of indexed principal; booking needs the daily CPI index ratio for principal, "
            "accrued and coupons, which this ledger does not book yet - quotes only",
    "FRN": "floating-rate note: its coupon resets weekly off the 13-week bill auction; the ledger books fixed coupons only - quotes only",
}
RATING_BUCKETS = ("AAA", "AA", "A", "BBB", "BB", "B", "CCC")
CORP_TENORS = (2, 5, 10, 30)
IG_BUCKETS = ("AAA", "AA", "A", "BBB")
#: country -> the generic benchmark's terms and its source (module docstring FOREIGN GOVERNMENT BENCHMARKS)
FOREIGN = {
    "DE": {"ccy": "EUR", "label": "German government (Bund proxy)", "curve": "EUR_AAA", "tenors": (2, 5, 10, 30), "freq": 1, "step": EIGHTH,
           "series": "the ECB euro-area AAA government par yield curve (a Bund proxy: every AAA euro-area issuer)", "lot": 1000},
    "JP": {"ccy": "JPY", "label": "Japanese government (JGB)", "curve": "JGB", "tenors": (2, 5, 10, 30), "freq": 2, "step": TENTH,
           "series": "the MOF JGB yields by maturity", "lot": 100000},
    "GB": {"ccy": "GBP", "label": "UK gilt", "fred": "IRLTLT01GBM156N", "tenors": (10,), "freq": 2, "step": EIGHTH,
           "series": "the OECD 10-year UK government yield (FRED IRLTLT01GBM156N, monthly average)", "lot": 1000,
           "limited": "monthly OECD average only (the Bank of England database refuses scripts): shown with its month, not tradable"},
    "FR": {"ccy": "EUR", "label": "French government (OAT)", "fred": "IRLTLT01FRM156N", "tenors": (10,), "freq": 1, "step": EIGHTH,
           "series": "the OECD 10-year French government yield (FRED IRLTLT01FRM156N, monthly average)", "lot": 1000,
           "limited": "monthly OECD average only: shown with its month, not tradable"},
    "IT": {"ccy": "EUR", "label": "Italian government (BTP)", "fred": "IRLTLT01ITM156N", "tenors": (10,), "freq": 2, "step": EIGHTH,
           "series": "the OECD 10-year Italian government yield (FRED IRLTLT01ITM156N, monthly average)", "lot": 1000,
           "limited": "monthly OECD average only: shown with its month, not tradable"},
}
CURVE_SERIES = {"REAL": "UST_REAL_PAR", "EUR_AAA": "EUR_AAA_PAR", "JGB": "JGB_MOF"}
CURVE_SOURCE = {"REAL": "treasury.gov daily real yield curve", "EUR_AAA": "ECB euro-area AAA government par yield curve (data-api.ecb.europa.eu)",
                "JGB": "MOF JGB yields by maturity (www.mof.go.jp jgbcme.csv)"}
FEDINVEST_LABEL = "TreasuryDirect FedInvest"
PRICES_DDL = ("CREATE TABLE IF NOT EXISTS debt_prices (cusip TEXT NOT NULL, price_date TEXT NOT NULL, sec_type TEXT, rate TEXT, maturity TEXT, buy TEXT,"
              " sell TEXT, eod TEXT, fetched_ts INTEGER NOT NULL, source TEXT NOT NULL, PRIMARY KEY (cusip, price_date))")
_LEGACY_UST = re.compile(r"^UST-(1M|3M|6M|1Y|2Y|3Y|5Y|7Y|10Y|20Y|30Y)-\d{6}$")
_GENERIC = re.compile(r"^(CORP|GOV)-[A-Z]{1,3}-\d{1,2}Y-(\d{6})$")
OPEN_ORDER_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")


# ---- small pure helpers -------------------------------------------------------------------------------------------------
def _d(x) -> Optional[date]:
    if x is None or isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _years(label) -> Optional[Decimal]:
    m = re.match(r"^(\d{1,2})Y$", str(label))
    if m:
        return Decimal(int(m.group(1)))
    try:
        return Decimal(str(label))
    except ArithmeticError:
        return None


def round_step(y_pct, step: Decimal) -> Decimal:
    """The nearest ``step`` (1/8 % or 0.1 %), ties to even, 3 dp; never negative (a coupon floor of 0)."""
    v = ((Decimal(str(y_pct)) / step).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * step).quantize(Decimal("0.001"))
    return max(v, Decimal("0.000"))


def interp(points: Dict[Decimal, Decimal], t) -> Optional[Decimal]:
    """Linear in maturity between the two surrounding tenors, flat beyond the ends (the CurveStore rule)."""
    if not points:
        return None
    pts = sorted((Decimal(k), Decimal(v)) for k, v in points.items())
    t = Decimal(str(t))
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def is_month_end(d: date) -> bool:
    return (d + timedelta(days=1)).month != d.month


def ust_id(cusip: str) -> str:
    return UST_PREFIX + cusip


def ust_name(kind: str, coupon_pct: Optional[Decimal], maturity: date, cusip: str) -> str:
    mat = maturity.strftime("%d-%b-%Y")
    if kind == "BILL":
        return f"US Treasury bill {mat} ({cusip})"
    if kind == "FRN":
        return f"US Treasury FRN {mat} ({cusip})"
    c = Decimal(coupon_pct or 0).quantize(Decimal("0.001"))
    if kind == "TIPS":
        return f"US Treasury TIPS {c} % {mat} ({cusip})"
    return f"US Treasury {c} % {mat} ({cusip})"


def _row(instrument_id: str, symbol: str, name: str, currency: str, maturity: date, spec: dict, account_kinds: List[str], support: str = "FULL",
         session_group: str = "US_BONDS", settle_days: int = 1, lot: int = LOT) -> dict:
    return {"instrument_id": instrument_id, "kind": "BOND", "symbol": symbol, "name": name, "currency": currency, "multiplier": "0.01", "tick": "0.000001",
            "lot": int(lot), "price_scale": "1", "session_group": session_group, "settle_days": int(settle_days), "account_kinds": list(account_kinds),
            "expiry": maturity.isoformat(), "spec": spec, "support": support}


# ---- the Treasury master (module docstring TREASURY MASTER) ---------------------------------------------------------------
def treasury_rows(record_date: Optional[date], securities: Iterable[dict], auctions: Iterable[dict], fedinvest: Optional[Dict[str, dict]],
                  fedinvest_date: Optional[date], today: date) -> List[dict]:
    """Instrument rows for every outstanding marketable Treasury (pure; the inputs are the parsed sources)."""
    by: Dict[str, dict] = {}
    for s in securities or []:
        mat, issue = _d(s.get("maturity")), _d(s.get("issue"))
        if mat is None or mat <= today or (issue is not None and issue > today):
            continue
        by[s["cusip"]] = {"cusip": s["cusip"], "type": s["type"], "coupon_pct": s.get("coupon_pct"), "issue": issue, "maturity": mat,
                          "pay_dates": list(s.get("pay_dates") or []), "outstanding_musd": s.get("outstanding_musd"),
                          "source": f"fiscaldata MSPD (record date {record_date.isoformat() if record_date else 'n/a'})"}
    for a in sorted(auctions or [], key=lambda x: (x.get("issue") or date.min)):
        mat, issue = _d(a.get("maturity")), _d(a.get("issue"))
        if mat is None or issue is None or mat <= today or issue > today:
            continue
        cur = by.get(a["cusip"])
        if cur is None:
            if a["type"] != "BILL" and a.get("coupon_pct") is None:
                continue                       # a coupon security is listed once its auction has set the coupon
            by[a["cusip"]] = {"cusip": a["cusip"], "type": a["type"], "coupon_pct": a.get("coupon_pct"), "issue": issue, "maturity": mat, "pay_dates": [],
                              "outstanding_musd": None, "term": a.get("term"),
                              "source": f"fiscaldata auctions (auctioned {a['auction_date'].isoformat() if a.get('auction_date') else 'n/a'}, issued {issue.isoformat()})"}
        elif a.get("term") and not cur.get("term"):
            cur["term"] = a.get("term")
    for cusip, f in (fedinvest or {}).items():
        mat = _d(f.get("maturity"))
        if cusip in by or mat is None or mat <= today or f.get("type") not in TYPE_WORD:
            continue
        rate = f.get("rate_pct")
        by[cusip] = {"cusip": cusip, "type": f["type"], "coupon_pct": None if f["type"] in ("BILL", "FRN") else rate, "issue": None, "maturity": mat,
                     "pay_dates": [], "outstanding_musd": None,
                     "source": f"{FEDINVEST_LABEL} {fedinvest_date.isoformat() if fedinvest_date else ''} (issue date not in the fiscaldata record yet)".strip()}
    out = []
    for cusip in sorted(by):
        s = by[cusip]
        kind = s["type"]
        zero = kind == "BILL"
        coupon = Decimal("0") if zero else Decimal(str(s["coupon_pct"] if s["coupon_pct"] is not None else 0))
        if kind == "FRN":
            coupon = Decimal("0")
        mat = s["maturity"]
        spec = {"cusip": cusip, "type": kind, "issuer": "United States Treasury", "coupon_pct": str(coupon.quantize(Decimal("0.001"))),
                "coupon": str((coupon / 100).quantize(Decimal("0.00001"))), "maturity": mat.isoformat(),
                "issue": s["issue"].isoformat() if s.get("issue") else None, "freq": 0 if zero else 2, "daycount": "ACT/ACT",
                "zero_coupon": zero or kind == "FRN", "eom": (not zero) and is_month_end(mat), "pay_dates": s.get("pay_dates") or [],
                "outstanding_musd": None if s.get("outstanding_musd") is None else str(s["outstanding_musd"]), "term": s.get("term"),
                "source": s["source"], "rating": None, "group": "Treasuries"}
        support = "FULL"
        if kind in LIMITED_REASON:
            support = "LIMITED"
            spec["limited_reason"] = LIMITED_REASON[kind]
            if kind == "FRN":
                spec["frn_note"] = "price per 100 from FedInvest only; no model (the coupon floats)"
        out.append(_row(ust_id(cusip), cusip, ust_name(kind, coupon, mat, cusip), "USD", mat, spec, US_ACCOUNT_KINDS, support))
    return out


# ---- generic corporates and foreign benchmarks -----------------------------------------------------------------------------
def month_key(d: date) -> str:
    return d.strftime("%Y%m")


def generic_maturity(listing: date, years: int) -> date:
    return date(listing.year + int(years), listing.month, 15)


def corp_id(bucket: str, years: int, listing: date) -> str:
    return f"{CORP_PREFIX}{bucket}-{int(years)}Y-{month_key(listing)}"


def gov_id(cc: str, years: int, listing: date) -> str:
    return f"{GOV_PREFIX}{cc}-{int(years)}Y-{month_key(listing)}"


def corporate_rows(curve, rates, listing: date) -> Tuple[List[dict], List[dict]]:
    """``(rows, missing)``: one generic per bucket x tenor whose inputs exist (module docstring GENERIC CORPORATES);
    ``missing`` = ``[{bucket, tenor, reason}]`` for the buckets that cannot be listed honestly."""
    from ..quotes.rates import OAS_SERIES
    rows, missing = [], []
    pc = curve.latest() if curve is not None else None
    for bucket in RATING_BUCKETS:
        sid = OAS_SERIES[bucket]
        got = rates.latest(sid) if rates is not None else None
        for years in CORP_TENORS:
            if pc is None or not pc.points:
                missing.append({"bucket": bucket, "tenor": f"{years}Y", "series": sid, "reason": "no Treasury par curve fetched yet"})
                continue
            if got is None:
                missing.append({"bucket": bucket, "tenor": f"{years}Y", "series": sid, "reason": f"no ICE BofA {bucket} OAS ({sid}) observation yet"})
                continue
            mat = generic_maturity(listing, years)
            t = Decimal((mat - listing).days) / Decimal(365)
            par = curve.par_yield(t)
            y = (par + got[1]).quantize(Decimal("0.000001"))
            coupon = round_step(y, EIGHTH)
            ig = bucket in IG_BUCKETS
            spec = {"generic": True, "type": "CORP", "bucket": bucket, "rating": bucket, "grade": "investment grade" if ig else "high yield", "tenor": f"{years}Y",
                    "coupon_pct": str(coupon), "coupon": str((coupon / 100).quantize(Decimal("0.00001"))), "maturity": mat.isoformat(), "issue": listing.isoformat(),
                    "freq": 2, "daycount": "ACT/ACT (generic simplification: US corporates settle 30/360)", "zero_coupon": False, "eom": False,
                    "oas_series": sid, "listing": {"par_yield_pct": str(par.quantize(Decimal("0.000001"))), "oas_pct": str(got[1]), "oas_as_of": got[0].isoformat(),
                                                   "curve_as_of": pc.as_of.isoformat()},
                    "source": f"generic: Treasury par curve + ICE BofA {bucket} OAS ({sid}); coupon = model par yield at listing rounded to 1/8 %",
                    "issuer": f"generic {bucket}-rated USD corporate (no real issuer)", "group": "Corporates"}
            name = f"Generic {bucket}-rated USD corporate {years}Y {coupon} % {mat.strftime('%d-%b-%Y')} (generic: curve + ICE BofA {bucket} OAS)"
            rows.append(_row(corp_id(bucket, years, listing), f"{bucket} {years}Y", name, "USD", mat, spec, US_ACCOUNT_KINDS))
    return rows, missing


def foreign_rows(store, rates, listing: date) -> Tuple[List[dict], List[dict]]:
    """``(rows, missing)`` for the generic foreign government benchmarks (module docstring FOREIGN GOVERNMENT BENCHMARKS)."""
    rows, missing = [], []
    for cc, f in FOREIGN.items():
        if f.get("curve"):
            got = store.curve(f["curve"]) if store is not None else None
            if got is None:
                for years in f["tenors"]:
                    missing.append({"country": cc, "tenor": f"{years}Y", "reason": f"{CURVE_SOURCE[f['curve']]} not fetched yet"})
                continue
            as_of, points, source = got["as_of"], got["points"], got["source"]
            limited = None
        else:
            obs = rates.latest(f["fred"]) if rates is not None else None
            if obs is None:
                for years in f["tenors"]:
                    missing.append({"country": cc, "tenor": f"{years}Y", "reason": f"{f['fred']} (OECD monthly) not available yet: FRED has served no observation"})
                continue
            as_of, points, source = obs[0], {Decimal(10): obs[1]}, f"FRED {f['fred']} (OECD monthly average)"
            limited = f["limited"]
        for years in f["tenors"]:
            mat = generic_maturity(listing, years)
            t = Decimal((mat - listing).days) / Decimal(365)
            y = interp(points, t)
            if y is None:
                missing.append({"country": cc, "tenor": f"{years}Y", "reason": "no yield at this maturity"})
                continue
            coupon = round_step(y, f["step"])
            spec = {"generic": True, "type": "GOV", "country": cc, "tenor": f"{years}Y", "coupon_pct": str(coupon), "coupon": str((coupon / 100).quantize(Decimal("0.00001"))),
                    "maturity": mat.isoformat(), "issue": listing.isoformat(), "freq": f["freq"], "daycount": "ACT/ACT (ICMA)", "zero_coupon": False, "eom": False,
                    "curve": f.get("curve"), "fred_series": f.get("fred"), "listing": {"yield_pct": str(Decimal(y).quantize(Decimal("0.000001"))), "as_of": as_of.isoformat()},
                    "source": f"generic benchmark priced from {f['series']}", "issuer": f"generic {f['label']} benchmark (no specific bond)", "rating": None,
                    "group": "Foreign government", "currency_note": f"settles T+2 in {f['ccy']}"}
            support = "FULL"
            if limited:
                support = "LIMITED"
                spec["limited_reason"] = limited
            name = f"Generic {f['label']} {years}Y {coupon} % {mat.strftime('%d-%b-%Y')} ({f['ccy']})"
            rows.append(_row(gov_id(cc, years, listing), f"{cc} {years}Y", name, f["ccy"], mat, spec, FOREIGN_ACCOUNT_KINDS, support, session_group="FX_SPOT",
                             settle_days=2, lot=f["lot"]))
    return rows, missing


def is_legacy(inst) -> bool:
    """A Build 1 bond row: the monthly model benchmarks and the universe's curated rows (module docstring LEGACY ROWS)."""
    iid = inst.instrument_id
    if _LEGACY_UST.match(iid):
        return True
    return inst.kind == "BOND" and not (iid.startswith(UST_PREFIX) or iid.startswith(CORP_PREFIX) or iid.startswith(GOV_PREFIX))


def stale_generic(iid: str, listing: date) -> bool:
    m = _GENERIC.match(iid)
    return bool(m) and m.group(2) != month_key(listing)


# ---- the store ------------------------------------------------------------------------------------------------------------------
class DebtStore:
    """Observed debt data in memory (module docstring STORE); ``persist`` writes through the bus (light commands)."""

    def __init__(self, db=None, bus=None, clock=None, log=None):
        self.db, self.bus, self.clock, self.log = db, bus, clock, log
        self._lock = threading.RLock()
        self._fi_date: Optional[date] = None
        self._fi: Dict[str, dict] = {}
        self._fi_fetched = 0
        self._curves: Dict[str, dict] = {}
        self._status: Dict[str, dict] = {}
        self.master: dict = {"record_date": None, "synced_ts": None, "synced_day": None, "count": 0, "sources": None}
        self.missing: Dict[str, List[dict]] = {"corporates": [], "foreign": []}
        self.last_attempt: Dict[str, int] = {}

    # ---- FedInvest ------------------------------------------------------------------------------------------------
    def put_fedinvest(self, price_date: date, rows: Dict[str, dict], fetched_ts: int, persist: bool = True) -> int:
        if price_date is None or not rows:
            return 0
        with self._lock:
            if self._fi_date is not None and price_date < self._fi_date:
                return 0
            self._fi_date, self._fi, self._fi_fetched = price_date, {k: dict(v) for k, v in rows.items()}, int(fetched_ts or 0)
        if persist and self.bus is not None:
            payload = {"date": price_date.isoformat(), "source": f"{FEDINVEST_LABEL} {price_date.isoformat()}", "fetched_ts": int(fetched_ts or 0),
                       "rows": [[c, r.get("type"), _s(r.get("rate_pct")), _s(r.get("maturity")), _s(r.get("buy")), _s(r.get("sell")), _s(r.get("eod"))]
                                for c, r in sorted(rows.items())]}
            res = self.bus.execute(Command("debt.prices", payload, _rid("debt:prices:" + price_date.isoformat(), payload), "worker"))
            if not res.ok:
                raise RuntimeError(f"debt.prices failed: {res.code} {res.message}")
        return len(rows)

    def fedinvest_date(self) -> Optional[date]:
        with self._lock:
            return self._fi_date

    def fedinvest(self, cusip: str) -> Optional[dict]:
        """``{date, buy, sell, eod, price, basis, type, rate_pct, fetched_ts}`` or None.  ``price`` = END OF DAY when published,
        else the BUY / SELL mid, else the one side there is (module docstring of parsers DEBT SOURCES)."""
        with self._lock:
            r = self._fi.get(cusip)
            d, fetched = self._fi_date, self._fi_fetched
        if r is None:
            return None
        buy, sell, eod = _dec(r.get("buy")), _dec(r.get("sell")), _dec(r.get("eod"))
        if eod is not None:
            price, basis = eod, "END OF DAY valuation price"
        elif buy is not None and sell is not None:
            price, basis = ((buy + sell) / 2).quantize(Decimal("0.000001")), "mid of BUY (invest) and SELL (redeem)"
        elif sell is not None:
            price, basis = sell, "SELL (redemption, bid side) - no BUY price offered"
        elif buy is not None:
            price, basis = buy, "BUY (investment side) - no SELL price"
        else:
            return None
        return {"date": d, "buy": buy, "sell": sell, "eod": eod, "price": price, "basis": basis, "type": r.get("type"), "rate_pct": r.get("rate_pct"),
                "fetched_ts": fetched}

    def fedinvest_rows(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._fi)

    # ---- curves ---------------------------------------------------------------------------------------------------
    def put_curve(self, key: str, as_of: date, points: Dict, source: str, fetched_ts: int, persist: bool = True) -> bool:
        pts = {Decimal(str(k)).quantize(Decimal("0.00000001")): Decimal(str(v)) for k, v in (points or {}).items()}
        if as_of is None or not pts:
            return False
        with self._lock:
            cur = self._curves.get(key)
            if cur is not None and as_of < cur["as_of"]:
                return False
            self._curves[key] = {"as_of": as_of, "points": pts, "source": source, "fetched_ts": int(fetched_ts or 0)}
        if persist and self.bus is not None:
            from ..quotes.rates import _record
            labels = {f"{int(k)}Y" if k == k.to_integral_value() else str(k): str(v) for k, v in sorted(pts.items())}
            _record(self.bus, None, {"series": CURVE_SERIES[key], "as_of": as_of.isoformat(), "points": labels, "source": source, "fetched_ts": int(fetched_ts or 0)})
        return True

    def curve(self, key: str) -> Optional[dict]:
        with self._lock:
            c = self._curves.get(key)
            return None if c is None else {"as_of": c["as_of"], "points": dict(c["points"]), "source": c["source"], "fetched_ts": c["fetched_ts"]}

    def yield_at(self, key: str, years) -> Optional[Tuple[Decimal, dict]]:
        c = self.curve(key)
        if c is None:
            return None
        y = interp(c["points"], years)
        return None if y is None else (y, c)

    # ---- status ---------------------------------------------------------------------------------------------------
    def note(self, part: str, ok: bool, error: Optional[str] = None, as_of=None, ts: Optional[int] = None, **extra) -> None:
        with self._lock:
            row = self._status.setdefault(part, {"ok": None, "error": None, "as_of": None, "last_ok_ts": None, "last_err_ts": None})
            row["ok"] = bool(ok)
            if ok:
                row.update(error=None, last_ok_ts=ts)
                if as_of is not None:
                    row["as_of"] = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
            else:
                row.update(error=(error or "failed")[:200], last_err_ts=ts)
            row.update(extra)

    def status(self) -> dict:
        with self._lock:
            out = {k: dict(v) for k, v in self._status.items()}
            out["fedinvest_date"] = self._fi_date.isoformat() if self._fi_date else None
            out["fedinvest_rows"] = len(self._fi)
            out["curves"] = {k: {"as_of": c["as_of"].isoformat(), "source": c["source"], "points": len(c["points"])} for k, c in self._curves.items()}
            out["master"] = dict(self.master)
            out["missing"] = {k: list(v) for k, v in self.missing.items()}
            return out

    # ---- persistence ------------------------------------------------------------------------------------------------
    def load_persisted(self) -> dict:
        """The last FedInvest date and the last curve of each key back into memory (a restart keeps the true as-of)."""
        out = {"fedinvest": 0, "curves": []}
        if self.db is None:
            return out
        with self.db.reader() as conn:
            try:
                row = conn.execute("SELECT MAX(price_date) d FROM debt_prices").fetchone()
                rows = conn.execute("SELECT * FROM debt_prices WHERE price_date=?", (row["d"],)).fetchall() if row is not None and row["d"] else []
            except Exception:   # noqa: BLE001 - no table yet (a database bound before P07): nothing to load
                rows = []
            fi, fts, fdate = {}, 0, None
            for r in rows:
                fdate = _d(r["price_date"])
                fts = max(fts, int(r["fetched_ts"] or 0))
                fi[r["cusip"]] = {"type": r["sec_type"], "rate_pct": _dec(r["rate"]), "maturity": _d(r["maturity"]), "buy": _dec(r["buy"]), "sell": _dec(r["sell"]),
                                  "eod": _dec(r["eod"])}
            curves = {}
            for key, series in CURVE_SERIES.items():
                r = conn.execute("SELECT MAX(as_of) d FROM curve_points WHERE series=?", (series,)).fetchone()
                if r is None or not r["d"]:
                    continue
                pts = conn.execute("SELECT tenor, value, source, fetched_ts FROM curve_points WHERE series=? AND as_of=?", (series, r["d"])).fetchall()
                curves[key] = (_d(r["d"]), {_years(p["tenor"]): Decimal(p["value"]) for p in pts if _years(p["tenor"]) is not None},
                               (pts[0]["source"] if pts else CURVE_SOURCE[key]), max((int(p["fetched_ts"] or 0) for p in pts), default=0))
        if fi and fdate is not None:
            self.put_fedinvest(fdate, fi, fts, persist=False)
            out["fedinvest"] = len(fi)
        for key, (as_of, pts, src, fts2) in curves.items():
            if as_of is not None and pts:
                self.put_curve(key, as_of, pts, src, fts2, persist=False)
                out["curves"].append(key)
        return out


def _s(v) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, date) else str(v)


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    try:
        d = Decimal(str(v))
    except ArithmeticError:
        return None
    return d if d.is_finite() else None


def _rid(prefix: str, payload) -> str:
    import hashlib
    return f"{prefix}:{hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()[:12]}"


def store_of(services) -> Optional[DebtStore]:
    return getattr(services, "debt_store", None) if services is not None else None


# ---- light commands ------------------------------------------------------------------------------------------------------------
def _referenced(conn, iid: str) -> bool:
    q = ",".join("?" for _ in OPEN_ORDER_STATUSES)
    if conn.execute(f"SELECT 1 FROM orders WHERE instrument_id=? AND status IN ({q}) LIMIT 1", (iid, *OPEN_ORDER_STATUSES)).fetchone() is not None:
        return True
    return conn.execute("SELECT 1 FROM lots WHERE instrument_id=? AND qty_remaining <> 0 LIMIT 1", (iid,)).fetchone() is not None


def h_sync(ctx, payload: dict) -> dict:
    """``debt.sync {rows: [instrument row], delist: [instrument_id]}`` (light): upsert the rows (INSERT, or UPDATE of the
    descriptive fields when the row exists; a DELISTED row comes back ACTIVE), then DELIST every id of ``delist`` that no
    open lot or working order references (those stay ACTIVE and are reported as ``kept``)."""
    import json
    conn = ctx.conn
    ts = int(ctx.ts)
    new = upd = 0
    for r in payload.get("rows") or []:
        iid = str(r["instrument_id"])
        if not (iid.startswith(UST_PREFIX) or iid.startswith(CORP_PREFIX) or iid.startswith(GOV_PREFIX)):
            raise ValueError(f"debt.sync lists Treasuries, generic corporates and generic government benchmarks only, not {iid}")
        vals = (str(r["symbol"]), str(r["name"]), str(r["currency"]), str(r["multiplier"]), str(r["tick"]), int(r["lot"]), str(r.get("price_scale") or "1"),
                str(r["session_group"]), int(r["settle_days"]), json.dumps(list(r["account_kinds"])), r.get("expiry"), json.dumps(r.get("spec") or {}),
                str(r.get("support") or "FULL"))
        old = conn.execute("SELECT spec_json, name, support, status, account_kinds FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
        if old is None:
            conn.execute("INSERT INTO instruments(instrument_id, kind, symbol, name, currency, multiplier, tick, lot, price_scale, session_group, settle_days,"
                         " account_kinds, expiry, spec_json, support, status, created_ts, updated_ts) VALUES (?,'BOND',?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?)",
                         (iid,) + vals + (ts, ts))
            new += 1
            continue
        if (old["spec_json"], old["name"], old["support"], old["status"], old["account_kinds"]) == (vals[11], vals[1], vals[12], "ACTIVE", vals[9]):
            continue
        conn.execute("UPDATE instruments SET symbol=?, name=?, currency=?, multiplier=?, tick=?, lot=?, price_scale=?, session_group=?, settle_days=?, account_kinds=?,"
                     " expiry=?, spec_json=?, support=?, status='ACTIVE', updated_ts=? WHERE instrument_id=? AND kind='BOND'", vals + (ts, iid))
        upd += 1
    delisted, kept = [], []
    for iid in payload.get("delist") or []:
        row = conn.execute("SELECT status, kind FROM instruments WHERE instrument_id=?", (str(iid),)).fetchone()
        if row is None or row["kind"] != "BOND" or row["status"] != "ACTIVE":
            continue
        if _referenced(conn, str(iid)):
            kept.append(str(iid))
            continue
        conn.execute("UPDATE instruments SET status='DELISTED', updated_ts=? WHERE instrument_id=? AND status='ACTIVE'", (ts, str(iid)))
        delisted.append(str(iid))
    if new or upd or delisted:
        ctx.event("DEBT_MASTER_SYNC", ref_type="housekeeping", ref_id=str(payload.get("label") or "debt"), new=new, updated=upd, delisted=len(delisted), kept=kept)
    return {"new": new, "updated": upd, "delisted": delisted, "kept": kept, "reload_master": bool(new or upd or delisted), "posted": False}


def h_prices(ctx, payload: dict) -> dict:
    """``debt.prices {date, source, fetched_ts, rows: [[cusip, type, rate, maturity, buy, sell, eod]]}`` (light): one row per
    CUSIP and price date in ``debt_prices``; dates older than the last ``PRICE_KEEP_DAYS`` dates are dropped."""
    conn = ctx.conn
    conn.execute(PRICES_DDL)
    d = date.fromisoformat(str(payload["date"])).isoformat()
    src = str(payload.get("source") or FEDINVEST_LABEL)[:120]
    fts = int(payload.get("fetched_ts") or ctx.ts)
    n = 0
    for row in payload.get("rows") or []:
        cusip, kind, rate, mat, buy, sell, eod = (list(row) + [None] * 7)[:7]
        conn.execute("INSERT OR REPLACE INTO debt_prices(cusip, price_date, sec_type, rate, maturity, buy, sell, eod, fetched_ts, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (str(cusip), d, kind, rate, mat, buy, sell, eod, fts, src))
        n += 1
    dates = [r[0] for r in conn.execute("SELECT DISTINCT price_date FROM debt_prices ORDER BY price_date DESC").fetchall()]
    dropped = 0
    if len(dates) > PRICE_KEEP_DAYS:
        cur = conn.execute("DELETE FROM debt_prices WHERE price_date < ?", (dates[PRICE_KEEP_DAYS - 1],))
        dropped = cur.rowcount or 0
    return {"rows": n, "date": d, "dropped": dropped, "posted": False}


def register(bus, services=None) -> None:
    bus.register("debt.sync", h_sync, light=True)
    bus.register("debt.prices", h_prices, light=True)


# ---- the refresher (module docstring REFRESHER) -------------------------------------------------------------------------------
def _sync(services, rows: List[dict], delist: List[str], label: str) -> dict:
    bus = getattr(services, "bus", None)
    if bus is None or (not rows and not delist):
        return {"new": 0, "updated": 0, "delisted": [], "kept": []}
    payload = {"rows": rows, "delist": sorted(set(delist)), "label": label}
    res = bus.execute(Command("debt.sync", payload, _rid(f"debt:sync:{label}", payload), "worker"))
    if not res.ok:
        raise RuntimeError(f"debt.sync failed: {res.code} {res.message}")
    body = res.body or {}
    if body.get("reload_master") and getattr(services, "instruments", None) is not None:
        services.instruments.load()
    return body


def _changed(master, rows: List[dict]) -> List[dict]:
    """The rows whose stored version differs (or that are new / not ACTIVE): ``debt.sync`` carries only those."""
    import json
    out = []
    for r in rows:
        inst = master.get(r["instrument_id"]) if master is not None else None
        if inst is None or inst.status != "ACTIVE" or inst.name != r["name"] or inst.support != r["support"] or json.dumps(inst.spec or {}, sort_keys=True) != json.dumps(
                r["spec"], sort_keys=True) or list(inst.account_kinds) != list(r["account_kinds"]):
            out.append(r)
    return out


def _held_bonds(services) -> bool:
    db = getattr(services, "db", None)
    if db is None:
        return False
    try:
        with db.reader() as conn:
            return conn.execute("SELECT 1 FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id WHERE i.kind='BOND' AND v.qty <> 0 LIMIT 1"
                                ).fetchone() is not None
    except Exception:   # noqa: BLE001 - a busy pool: no pin this round
        return False


def refresh(services, now: Optional[int] = None) -> dict:
    """One refresher pass (module docstring REFRESHER).  Returns a summary; raises only when every part failed."""
    from ..quotes.http import FetchError
    from ..quotes.rates import OAS_SERIES, OECD_10Y, SHIFT_SYMBOLS
    now = int(now if now is not None else services.clock.epoch())
    today = ny_of(now).date()
    ny = ny_of(now)
    store = store_of(services)
    if store is None:
        return {"skipped": "no debt store bound"}
    providers = getattr(services, "providers", None)
    debt = getattr(providers, "debt", None) if providers is not None else None
    cal = getattr(services, "cal", None)
    prev_bd = cal.prev_business_day(today) if cal is not None else today - timedelta(days=1)
    out: dict = {"errors": [], "ran": []}

    def attempt(part: str, every: int) -> bool:
        last = store.last_attempt.get(part)
        if last is not None and now - last < every:
            return False
        store.last_attempt[part] = now
        return True

    # 1. the master (+ today's FedInvest list) once a day
    fi_date = store.fedinvest_date()
    need_master = store.master.get("synced_day") != today.isoformat()
    business = cal is None or cal.is_business_day(today)
    # FedInvest posts the day's prices during business days: ask from 09:00 ET until today's are in (every REFRESH_S), then at most
    # EOD_TRIES times after 18:00 ET for the END OF DAY column; never overnight or on a weekend (one fetch at start-up when empty)
    need_prices = fi_date is None or (fi_date < today and business and ny.hour >= 9)
    if store.master.get("eod_day") != today.isoformat():
        store.master.update(eod_day=today.isoformat(), eod_tries=0, eod_seen=False)
    eod_due = fi_date == today and business and ny.hour >= 18 and not store.master.get("eod_seen") and int(store.master.get("eod_tries") or 0) < EOD_TRIES
    if eod_due:
        store.master["eod_tries"] = int(store.master.get("eod_tries") or 0) + 1
    if debt is not None and (need_master or need_prices or eod_due) and attempt("fedinvest", REFRESH_S - 60):
        try:
            fdate, frows = debt.fedinvest()
            if fdate is not None and frows:
                store.put_fedinvest(fdate, frows, now)
                store.note("fedinvest", True, as_of=fdate, ts=now, rows=len(frows))
                if fdate == today and any(r.get("eod") is not None for r in frows.values()):
                    store.master["eod_seen"] = True
            out["ran"].append("fedinvest")
        except (FetchError, ValueError, RuntimeError) as e:
            store.note("fedinvest", False, f"{e}", ts=now)
            out["errors"].append(f"fedinvest: {e}")
    if debt is not None and need_master and attempt("master", REFRESH_S - 60):
        try:
            record, secs = debt.mspd()
            since = (record or today - timedelta(days=45)) - timedelta(days=7)
            try:
                auctions = debt.auctions(since)
            except FetchError as e:
                auctions = []
                store.note("auctions", False, f"{e}", ts=now)
                out["errors"].append(f"auctions: {e}")
            rows = treasury_rows(record, secs, auctions, store.fedinvest_rows(), store.fedinvest_date(), today)
            master = getattr(services, "instruments", None)
            legacy = [i.instrument_id for i in (master.all() if master is not None else []) if i.kind == "BOND" and i.status == "ACTIVE" and is_legacy(i)]
            listed = {r["instrument_id"] for r in rows}
            gone = [i.instrument_id for i in (master.all() if master is not None else []) if i.kind == "BOND" and i.status == "ACTIVE"
                    and i.instrument_id.startswith(UST_PREFIX) and not _LEGACY_UST.match(i.instrument_id) and i.instrument_id not in listed]
            res = _sync(services, _changed(master, rows), legacy + gone, f"treasuries:{today.isoformat()}")
            store.master.update(record_date=record.isoformat() if record else None, synced_ts=now, synced_day=today.isoformat(), count=len(rows),
                                sources={"mspd": record.isoformat() if record else None, "auctions_since": since.isoformat(), "fedinvest": _s(store.fedinvest_date())})
            store.note("master", True, as_of=record, ts=now, rows=len(rows), new=res.get("new"), updated=res.get("updated"), delisted=len(res.get("delisted") or []))
            out["ran"].append("master")
        except (FetchError, ValueError, RuntimeError) as e:
            store.note("master", False, f"{e}", ts=now)
            out["errors"].append(f"master: {e}")
    # 2. the curves (real, EUR AAA, JGB): when older than the previous business day, at most every CURVE_RETRY_S
    for key, fn in (("REAL", "real_curve"), ("EUR_AAA", "ecb_aaa_curve"), ("JGB", "jgb_curve")):
        cur = store.curve(key)
        if debt is None or (cur is not None and cur["as_of"] >= prev_bd) or not attempt(f"curve:{key}", CURVE_RETRY_S):
            continue
        try:
            got = getattr(debt, fn)()
            if got is None:
                raise FetchError(f"{CURVE_SOURCE[key]}: nothing published for this month or the previous one", retryable=False)
            if hasattr(got, "points"):
                as_of, pts, src = got.as_of, got.points, got.source
            else:
                as_of, pts, src = got[0], got[1], CURVE_SOURCE[key]
            store.put_curve(key, as_of, pts, src, now)
            store.note(f"curve:{key}", True, as_of=as_of, ts=now)
            out["ran"].append(f"curve:{key}")
        except (FetchError, ValueError, RuntimeError) as e:
            store.note(f"curve:{key}", False, f"{e}", ts=now)
            out["errors"].append(f"curve {key}: {e}")
    # 3. the FRED series the bonds need (OAS buckets, OECD 10-year) when the 6-hourly slot has none yet
    rates, fred = getattr(services, "rates", None), getattr(providers, "fred", None) if providers is not None else None
    if rates is not None and fred is not None:
        wanted = [v for k, v in OAS_SERIES.items() if k != "HY"] + list(OECD_10Y.values())
        missing = [s for s in wanted if rates.latest(s) is None]
        if missing and attempt("fred_debt", REFRESH_S - 60):
            for sid in missing:
                try:
                    obs = fred.latest(sid, 60)
                    if obs:
                        rates.put(sid, obs, "FRED")
                except FetchError as e:
                    store.note("fred", False, f"{e}", ts=now)
                    if getattr(e, "kind", None) == "maintenance":
                        break                      # FRED paused: the route answers the rest at once anyway
            out["ran"].append("fred_debt")
    # 4. this month's generic rows (and the delisting of last month's that nobody holds)
    master = getattr(services, "instruments", None)
    if master is not None:
        listing = today
        try:
            corp, corp_missing = corporate_rows(getattr(services, "curve", None), rates, listing)
            gov, gov_missing = foreign_rows(store, rates, listing)
            store.missing = {"corporates": corp_missing, "foreign": gov_missing}
            existing = {i.instrument_id: i for i in master.all() if i.kind == "BOND"}
            fresh = [r for r in corp + gov if r["instrument_id"] not in existing or existing[r["instrument_id"]].status != "ACTIVE"]
            old = [iid for iid, i in existing.items() if i.status == "ACTIVE" and stale_generic(iid, listing)]
            if fresh or old:
                res = _sync(services, fresh, old, f"generics:{month_key(listing)}")
                store.note("generics", True, as_of=listing, ts=now, new=res.get("new"), delisted=len(res.get("delisted") or []))
            out["ran"].append("generics")
        except (ValueError, RuntimeError) as e:
            store.note("generics", False, f"{e}", ts=now)
            out["errors"].append(f"generics: {e}")
    # 5. keep the Yahoo yield records fresh while bonds are held (the intraday shift of the marks)
    cache = getattr(services, "cache", None)
    if cache is not None and _held_bonds(services):
        try:
            cache.subscribe(list(SHIFT_SYMBOLS), REFRESH_S + 120, "WATCH")
        except Exception as e:   # noqa: BLE001 - a pin is a convenience; the mark says when the shift is missing
            out["errors"].append(f"shift pin: {e}")
    if out["errors"] and not out["ran"]:
        raise RuntimeError("; ".join(out["errors"])[:300])
    return out


def bind(rt) -> None:
    """``services.debt_store`` (persisted state loaded), the ``debt_prices`` table (C4), the FRED route on the rate store,
    and the refresher on the quote worker (C14) when the worker exposes ``register_refresher``."""
    db = getattr(rt, "db", None)
    if db is not None and hasattr(db, "ensure_schema_ext"):
        db.ensure_schema_ext("treasuries", [PRICES_DDL])
    store = getattr(rt, "debt_store", None)
    if store is None:
        store = DebtStore(db, getattr(rt, "bus", None), getattr(rt, "clock", None), getattr(rt, "log", None))
        rt.debt_store = store
        try:
            store.load_persisted()
        except Exception as e:   # noqa: BLE001 - a restart without the last prices shows them unavailable, never invented
            if getattr(rt, "log", None):
                rt.log.warning("debt store load_persisted failed: %s", e)
    rates = getattr(rt, "rates", None)
    fred = getattr(getattr(rt, "providers", None), "fred", None)
    if rates is not None and fred is not None:
        rates.route = getattr(fred, "fred_route", None) or (fred if hasattr(fred, "status") else None)
    try:
        from ..quotes.worker import register_refresher
    except ImportError:
        register_refresher = None
    if register_refresher is not None:
        register_refresher("debt", REFRESH_S, refresh, label="Debt market data (Treasury fiscal data, TreasuryDirect, ECB, MOF)")
    elif getattr(rt, "log", None):
        rt.log.info("debt refresher: the quote worker has no register_refresher hook yet; the Debt master refreshes when it lands")


# ---- the Debt page views (GET /api/debt/*; module docstring of pages/markets_debt.js) -------------------------------------------
TABS = [{"key": "treasuries", "label": "Treasuries"}, {"key": "corporates", "label": "Corporates"}, {"key": "curve", "label": "Curve"}]
TYPE_ORDER = {"BILL": 0, "NOTE": 1, "BOND": 2, "TIPS": 3, "FRN": 4, "CORP": 5, "GOV": 6, "LEGACY": 7}
BENCH_TENORS = [("1M", 1 / 12, "BILL"), ("3M", 0.25, "BILL"), ("6M", 0.5, "BILL"), ("1Y", 1.0, "BILL"), ("2Y", 2.0, "NOTE"), ("3Y", 3.0, "NOTE"), ("5Y", 5.0, "NOTE"),
                ("7Y", 7.0, "NOTE"), ("10Y", 10.0, "NOTE"), ("20Y", 20.0, "BOND"), ("30Y", 30.0, "BOND")]
NOTE_TEXT = ("Real US Treasuries from fiscaldata (MSPD + auctions), priced from TreasuryDirect FedInvest (that day's price, moved by today's Yahoo "
             "yield change when it is an earlier day's) or the Treasury par curve; generic corporates are curve + ICE BofA OAS (no real issuer). "
             "Dealt at the price +/- a disclosed assumed half-spread, fee USD 0.02 per 100 face (min 5.00), T+1, 08:00-17:00 ET.")


def _compact_mark(view) -> Optional[dict]:
    if view is None:
        return None
    inp = view.inputs or {}
    return {"value": None if view.mark is None else str(view.mark), "mark": None if view.mark is None else str(view.mark), "quality": view.quality,
            "source": view.source, "method": view.method, "as_of": view.quote_time, "quote_time": view.quote_time, "fetched_at": view.fetched_at,
            "delay_label": view.delay_label, "note": view.note, "executable": False,
            "inputs": {k: inp.get(k) for k in ("as_of_date", "yield_pct", "shift_pct", "label") if inp.get(k) is not None} or None}


def bond_row(services, inst, snapshot, today: date, positions: Dict[str, int], par_at=None) -> dict:
    """One Debt grid row: coupon, maturity, price (with its source and as-of), yield, spread, rating, accrued, DV01 per
    1,000,000 face, the account's position, and whether / why it trades here."""
    from . import bonds as B
    spec = inst.spec or {}
    view = snapshot.mark(inst.instrument_id)
    row = {"instrument_id": inst.instrument_id, "symbol": inst.symbol, "name": inst.name, "kind": "BOND", "type": B._type_of(inst), "currency": inst.currency,
           "support": inst.support, "status": inst.status, "limited_reason": spec.get("limited_reason"), "coupon_pct": spec.get("coupon_pct"),
           "maturity": spec.get("maturity") or inst.expiry, "issue": spec.get("issue"), "rating": spec.get("rating") or ("UST" if inst.instrument_id.startswith(UST_PREFIX) else None),
           "cusip": spec.get("cusip"), "term": spec.get("term"), "outstanding_musd": spec.get("outstanding_musd"), "group": spec.get("group"), "lot": inst.lot,
           "position": positions.get(inst.instrument_id), "mark": _compact_mark(view), "price": None, "yield_pct": None, "spread_bp": None, "accrued": None,
           "dv01_per_1m": None, "years": None, "tradable": inst.support == "FULL"}
    try:
        terms = B.bond_terms(inst)
    except Exception as e:   # noqa: BLE001 - a row without terms says so
        row["note"] = f"{e}"
        return row
    if today >= terms.maturity:
        row["note"] = "matured"
        return row
    row["years"] = round((terms.maturity - today).days / 365.0, 2)
    row["accrued"] = str(B.accrued_per_100(terms, today))
    if view is None or view.mark is None:
        row["note"] = (view.note if view is not None else "") or "no price"
        return row
    clean = Decimal(str(view.mark))
    row["price"] = str(clean)
    if row["type"] != "FRN":
        y = B.yield_from_clean(terms, today, clean)
        row["yield_pct"] = None if y is None else str(y)
        if y is not None:
            dv = B.dv01_per_100(terms, today, y)
            row["dv01_per_1m"] = None if dv is None else str((dv * Decimal(10000)).quantize(Decimal("0.01")))
            if row["type"] == "CORP" and spec.get("oas_series"):
                rates = getattr(services, "rates", None)
                got = rates.latest(spec["oas_series"]) if rates is not None else None
                row["spread_bp"] = None if got is None else str((got[1] * 100).quantize(Decimal("0.1")))
                row["spread_basis"] = "ICE BofA OAS of the bucket (bp)"
            elif row["type"] in ("BILL", "NOTE", "BOND") and par_at is not None:
                par = par_at(Decimal(repr((terms.maturity - today).days / 365.0)))
                if par is not None:
                    row["spread_bp"] = str(((y - par) * 100).quantize(Decimal("0.1")))
                    row["spread_basis"] = "yield minus the Treasury par curve at the same maturity (bp; rich < 0 < cheap)"
        if row["type"] == "BILL":
            dr = B.discount_rate(terms, today, clean)
            row["discount_rate_pct"] = None if dr is None else str(dr)
    ok, why = B.executable_mark(view, today)
    if not ok and inst.support == "FULL":
        row["why_not"] = why
    return row


def _positions(services, account_id: Optional[str]) -> Dict[str, int]:
    if not account_id:
        return {}
    with services.db.reader() as conn:
        return {r[0]: int(r[1]) for r in conn.execute("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (account_id,)).fetchall()}


def _bonds(services, pos: Dict[str, int]) -> list:
    """Every ACTIVE bond (plus the held ones) of the DATABASE, each through ``master.get`` - not ``master.all()``: a command that
    rolls back evicts its instrument from the master's memory (TX-2) and ``all()`` then misses it until something re-reads it;
    ``get`` re-reads a missing row, so the Debt lists never drop a bond after a refused trade."""
    master = services.instruments
    with services.db.reader() as conn:
        ids = [r[0] for r in conn.execute("SELECT instrument_id FROM instruments WHERE kind='BOND' AND status='ACTIVE'").fetchall()]
        ids += [i for i in pos if i not in set(ids)]
        out = []
        for iid in ids:
            inst = master.get(iid, conn)
            if inst is not None and inst.kind == "BOND":
                out.append(inst)
    return out


def _par_at(services):
    curve = getattr(services, "curve", None)
    if curve is None or curve.latest() is None:
        return None
    return lambda t: curve.par_yield(t)


def _subscribe_shifts(services) -> None:
    """The Debt page shows prices that move with today's Yahoo yields: keep those four records fresh while it is open."""
    from ..quotes.rates import SHIFT_SYMBOLS
    cache = getattr(services, "cache", None)
    if cache is not None and hasattr(cache, "subscribe"):
        try:
            cache.subscribe(list(SHIFT_SYMBOLS), 120, "PAGE")
        except Exception:   # noqa: BLE001 - a pin is a convenience; each mark says when no shift is used
            pass


def debt_markets(services, account_id: Optional[str], tab: Optional[str], now: int) -> dict:
    """GET /api/debt/markets: the Treasuries / Corporates / Curve tabs of the Debt Instruments account."""
    today = ny_of(now).date()
    tab = tab or "treasuries"
    if tab not in ("treasuries", "corporates", "curve"):
        tab = "treasuries"
    master = services.instruments
    pos = _positions(services, account_id)
    store = store_of(services)
    bonds_all = _bonds(services, pos)
    counts = {"treasuries": sum(1 for i in bonds_all if i.instrument_id.startswith(UST_PREFIX)),
              "corporates": sum(1 for i in bonds_all if i.instrument_id.startswith(CORP_PREFIX))}
    out: dict = {"account_id": account_id, "tab": tab, "tabs": [dict(t, count=counts.get(t["key"])) for t in TABS], "server_time": now, "rows": [],
                 "note": NOTE_TEXT, "status": store.status() if store is not None else {"master": {}, "note": "the debt store is not bound"},
                 "fred": services.rates.fred_status() if getattr(services, "rates", None) is not None else None,
                 "sessions": {"US_BONDS": services.sessions.state("US_BONDS", now)}}
    _subscribe_shifts(services)
    if tab == "curve":
        out["curve"] = debt_curve(services, now)
        return out
    if tab == "treasuries":
        ids = [i for i in bonds_all if i.instrument_id.startswith(UST_PREFIX) and not _LEGACY_UST.match(i.instrument_id)]
        ids += [i for i in bonds_all if is_legacy(i) and i.instrument_id in pos]          # a held Build 1 row stays visible
    else:
        ids = [i for i in bonds_all if i.instrument_id.startswith(CORP_PREFIX)]
        out["missing"] = list((store.missing.get("corporates") if store is not None else []) or [])
        out["oas"] = services.rates.view().get("oas") if getattr(services, "rates", None) is not None else None
    ids.sort(key=lambda i: (TYPE_ORDER.get(str((i.spec or {}).get("type") or "LEGACY").upper(), 9), i.expiry or "", i.symbol))
    snap = services.marks.snapshot([i.instrument_id for i in ids])
    par_at = _par_at(services)
    out["rows"] = [bond_row(services, i, snap, today, pos, par_at) for i in ids]
    types: Dict[str, int] = {}
    for r in out["rows"]:
        types[r["type"]] = types.get(r["type"], 0) + 1
    out["types"] = types
    if tab == "treasuries" and not out["rows"]:
        st = (out["status"] or {}).get("master") or {}
        out["empty"] = ("the Treasury list has not been fetched yet (fiscaldata MSPD + auctions, TreasuryDirect FedInvest): the debt refresher loads it in the "
                        "background" + (f"; last error: {((out['status'] or {}).get('master') or {}).get('error')}" if isinstance(st, dict) and st.get("error") else ""))
    return out


def benchmark_rows(services, today: date) -> List[dict]:
    """For each curve tenor the most recently issued Treasury of that maturity bucket (bills <= 1Y, notes, bonds), with its
    FedInvest / computed price and yield against the par curve at the same maturity."""
    from . import bonds as B
    master = services.instruments
    usts = [i for i in _bonds(services, {}) if i.instrument_id.startswith(UST_PREFIX) and not _LEGACY_UST.match(i.instrument_id)
            and (i.spec or {}).get("type") in ("BILL", "NOTE", "BOND")]
    picks = []
    for label, years, kind in BENCH_TENORS:
        lo, hi = (years * 0.6, years * 1.25) if years < 1.5 else (years * 0.85, years * 1.02)
        cands = []
        for i in usts:
            mat = _d((i.spec or {}).get("maturity") or i.expiry)
            if mat is None:
                continue
            yrs = (mat - today).days / 365.0
            if lo <= yrs <= hi and ((kind == "BILL") == ((i.spec or {}).get("type") == "BILL")):
                cands.append(((i.spec or {}).get("issue") or "", -abs(yrs - years), i))
        if cands:
            cands.sort(key=lambda c: (c[0], c[1]))
            picks.append((label, years, cands[-1][2]))
    if not picks:
        return []
    snap = services.marks.snapshot([p[2].instrument_id for p in picks])
    par_at = _par_at(services)
    out = []
    for label, years, inst in picks:
        r = bond_row(services, inst, snap, today, {}, par_at)
        r["tenor"] = label
        r["tenor_years"] = years
        out.append(r)
    return out


def _curve_block(points: Dict, as_of, source, labels=True) -> dict:
    pts = []
    for k, v in sorted((Decimal(str(k)), Decimal(str(v))) for k, v in (points or {}).items()):
        lab = (f"{int(round(k * 12))}M" if k < 1 else f"{int(round(k))}Y") if labels else str(k)
        pts.append({"tenor": lab, "years": str(k.normalize()), "yield": str(v)})
    return {"as_of": _s(as_of), "source": source, "points": pts}


def debt_curve(services, now: int) -> dict:
    """GET /api/debt/curve: the nominal par curve (+ today's Yahoo shifts), the real curve, the EUR AAA and JGB curves, the
    OECD monthly 10-year yields and the benchmark Treasuries."""
    today = ny_of(now).date()
    curve = getattr(services, "curve", None)
    pc = curve.latest() if curve is not None else None
    store = store_of(services)
    out: dict = {"as_of_date": today.isoformat()}
    if pc is not None:
        shifts, inputs = curve.intraday_shifts(getattr(services, "cache", None))
        cal = getattr(services, "cal", None)
        fresh = cal is None or pc.as_of >= cal.prev_business_day(today)
        from ..quotes.rates import SHIFT_SYMBOLS
        shift_rows = [dict(inputs.get(sym) or {}, symbol=sym, years=str(k.normalize()), shift_pct=str(shifts[k])) for sym, k in SHIFT_SYMBOLS.items() if k in shifts]
        out["par"] = dict(_curve_block(pc.points, pc.as_of, pc.source), quality="close" if fresh else "stale", shifts=shift_rows,
                          shift_note="today's change of Yahoo's ^IRX ^FVX ^TNX ^TYX (last - previous close), applied to bonds priced off an earlier day")
    else:
        out["par"] = {"as_of": None, "points": [], "quality": "unavailable", "source": None,
                      "note": "no Treasury par curve has been fetched yet (Treasury.gov daily par yield curve, after 18:00 ET)"}
    for key, name in (("REAL", "real"), ("EUR_AAA", "eur_aaa"), ("JGB", "jgb")):
        c = store.curve(key) if store is not None else None
        out[name] = _curve_block(c["points"], c["as_of"], c["source"]) if c is not None else {"as_of": None, "points": [], "source": CURVE_SOURCE[key],
                                                                                             "note": "not fetched yet"}
    rates = getattr(services, "rates", None)
    out["oecd_10y"] = rates.view().get("long_term") if rates is not None else None
    out["fred"] = rates.fred_status() if rates is not None else None
    out["sofr"] = rates.view().get("SOFR") if rates is not None else None
    out["benchmarks"] = benchmark_rows(services, today)
    return out


def debt_bonds(services, account_id: Optional[str], scope: str, now: int) -> dict:
    """GET /api/debt/bonds?scope=FOREIGN: the generic foreign government benchmarks (C18 bondlist, C19)."""
    today = ny_of(now).date()
    pos = _positions(services, account_id)
    store = store_of(services)
    rows = [i for i in _bonds(services, pos) if (i.instrument_id.startswith(GOV_PREFIX) if scope.upper() == "FOREIGN" else i.instrument_id.startswith(UST_PREFIX))]
    rows.sort(key=lambda i: ((i.spec or {}).get("country") or "", i.expiry or ""))
    snap = services.marks.snapshot([i.instrument_id for i in rows])
    curves = {}
    for key in ("EUR_AAA", "JGB"):
        c = store.curve(key) if store is not None else None
        curves[key] = None if c is None else {"as_of": c["as_of"].isoformat(), "source": c["source"]}
    rates = getattr(services, "rates", None)
    return {"account_id": account_id, "scope": scope.upper(), "rows": [bond_row(services, i, snap, today, pos, None) for i in rows],
            "missing": list((store.missing.get("foreign") if store is not None else []) or []), "curves": curves,
            "oecd_10y": rates.view().get("long_term") if rates is not None else None, "fred": rates.fred_status() if rates is not None else None,
            "note": ("Generic government benchmarks in their own currency: DE from the ECB euro-area AAA par curve (a Bund proxy), JP from the MOF JGB "
                     "yields; gilts / OATs / BTPs only from the OECD monthly 10-year average (LIMITED). Settle T+2 in the currency; dealt at the curve "
                     "price +/- an assumed half-spread."), "server_time": now, "sessions": {"FX_SPOT": services.sessions.state("FX_SPOT", now)}}


def bond_bars(services, iid: str, now: int) -> dict:
    """GET /api/instruments/<bond>/bars: bonds have no intraday prints (FedInvest is daily, the rest is computed), so the chart
    gets no bars and says where the price comes from - never a 404 for a listed bond, never an invented series."""
    from ..api import NotFound
    inst = services.instruments.get(str(iid or "").strip().upper())
    if inst is None or inst.kind != "BOND":
        raise NotFound(f"instrument {iid} is not a listed bond")
    snap = services.marks.snapshot([inst.instrument_id])
    v = snap.mark(inst.instrument_id)
    return {"instrument_id": inst.instrument_id, "range": "1d", "bars": [], "bar": None, "prev_close": None,
            "session": services.sessions.state(inst.session_group or "US_BONDS", now), "quote_time": v.quote_time if v is not None else None,
            "fetched_at": v.fetched_at if v is not None else None, "source": v.source if v is not None else None, "quality": v.quality if v is not None else "unavailable",
            "delay_label": "daily price / model", "note": ("no intraday bars for a bond: its price is the TreasuryDirect FedInvest price of the day or a curve model "
                                                             "(see the price's source and as-of)")}


def register_routes(r) -> None:
    """C1: GET /api/debt/markets, /api/debt/curve, /api/debt/bonds and the bars of a bond (UST- / CORP- / GOV- ids only)."""
    def markets(req):
        return debt_markets(req.rt, req.q("account_id"), req.q("tab"), req.now)

    def curve(req):
        return debt_curve(req.rt, req.now)

    def bondlist(req):
        return debt_bonds(req.rt, req.q("account_id"), req.q("scope") or "FOREIGN", req.now)

    def bars(req):
        return bond_bars(req.rt, req.params[0], req.now)

    r.add("GET", r"/api/instruments/((?:UST|CORP|GOV)-[A-Z0-9-]+)/bars", bars)
    r.add("GET", r"/api/debt/markets", markets)
    r.add("GET", r"/api/debt/curve", curve)
    r.add("GET", r"/api/debt/bonds", bondlist)


__all__ = ["DebtStore", "treasury_rows", "corporate_rows", "foreign_rows", "refresh", "bind", "register", "h_sync", "h_prices", "store_of", "ust_id", "ust_name",
           "corp_id", "gov_id", "round_step", "interp", "is_legacy", "stale_generic", "generic_maturity", "FOREIGN", "RATING_BUCKETS", "CORP_TENORS", "LIMITED_REASON",
           "REFRESH_S", "FEDINVEST_LABEL", "CURVE_SOURCE", "PRICES_DDL", "bond_row", "debt_markets", "debt_curve", "debt_bonds", "benchmark_rows",
           "register_routes", "bond_bars"]
