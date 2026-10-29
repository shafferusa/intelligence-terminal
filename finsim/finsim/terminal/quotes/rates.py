"""Curve, rate and reference-FX stores + the light commands that persist them (TERMINAL_DESIGN.md §8.6, §12.2, §13.11).

Conventions
- ``CurveStore`` holds the latest Treasury par curve (``ParCurve``: tenor years → par yield in PERCENT) and derives:
  ``par_yield(t)`` linear in maturity between the two surrounding tenors, flat beyond the ends;
  ``intraday_shifts(cache)`` = Yahoo ``^IRX`` (3M) / ``^FVX`` (5Y) / ``^TNX`` (10Y) / ``^TYX`` (30Y) ``last − prev_close``
  in percentage points (each with its freshness, carried as an input quality);
  ``discount_factors()`` BOOTSTRAPPED from the par yields, semi-annual, linear in maturity (the input of §13.11):
  for t = 0.5, 1.0, …: ``df(t) = (1 − c(t)/2 × Σ_{s<t} df(s)) / (1 + c(t)/2)`` with ``c(t)`` the decimal par yield
  at t — a flat 4.00 % curve gives 1/1.02, 1/1.02², … exactly (§19.15).  ``set_fixed_dfs`` (the §19.15 FixedCurve
  fixture) bypasses the bootstrap with discount factors given directly.
- ``RateStore`` holds FRED observations per series (``SOFR``, the policy indices of ``tables.POLICY_INDEX``, the ICE BofA
  OAS series - the HY index ``BAMLH0A0HYM2`` and, since Build 2 P07 (OA-06), every rating bucket of ``OAS_SERIES`` AAA ..
  CCC - ``VIXCLS``, and the OECD monthly long-term government yields of ``OECD_10Y`` for the gilt / OAT / BTP benchmarks):
  ``latest(series)`` → ``(date, Decimal percent) | None``; ``as_of_le(series, d)`` is the RATE_LAG lookup (latest
  published observation on or before d).  A missing rate is ``None`` — nothing is defaulted to a number.
- SOURCES (ORC-8): ``put(series, observations, source)`` labels the series with ``observations.source`` when the
  provider's ``Observations`` carry one (SOFR from the NY Fed and the ECB deposit rate from the ECB data API while FRED is
  in maintenance), else with ``source``; ``source(series)`` and ``view()`` show it next to the value.  A failed fetch never
  erases anything: the store keeps the last good observations with their TRUE observation dates (and ``load_persisted``
  brings them back after a restart), so during a FRED outage every rate shows its real as-of date, never today's.
  ``route`` (set at bind time) is the provider's ``FredRoute``: ``view()["fred"]`` says "FRED unavailable (maintenance)"
  while it lasts.
- RFR AND TERM CURVES (FX_CCS_PLAN rev 2 K2 / D12, 1.0.6.1): ``RateStore`` also holds the overnight risk-free rates of
  ``tables.RFR`` - ``ESTR`` (ECB, FRED fallback), ``IUDSOIA`` (SONIA: the Bank of England database first, FRED the fallback), ``SOFR`` -
  and their compounded INDICES ``ESTR_INDEX``, ``SOFR_INDEX`` and ``SONIA_INDEX`` (index LEVELS, not percents: see UNITS), each
  observation with its own source (``observation(series, d)``; ``sources_seen(series)`` keeps the latest date each source gave, so
  "both as-of dates are recorded" for SONIA).  It also holds the dated term-curve GRIDS of ``TERM_SERIES``: ``BOE_OIS_SPOT`` (the Bank
  of England UK OIS spot curve, tenors 1M..60M), ``ECB_AAA_SPOT`` (the ECB euro-area AAA government spot rates, 3M..10Y) - both in
  PERCENT, continuously compounded, kept to 6 decimals of a percent (``CURVE_NODE_Q``: the value a curve node uses, stored as is) - and
  ``ECB_AAA_SV`` (the six Svensson parameters, for the self-check only).  ``put_grid`` / ``grid`` / ``grid_on_or_before`` /
  ``latest_grid``; persisted in ``curve_points`` (series, as_of, tenor label, value) through ``rates.record`` like the par curve;
  ``load_persisted`` brings the last ``GRID_KEEP_DATES`` dates per series back and ``grid_on_or_before`` reads an older date from the
  database when asked.  ``fxcurves`` builds the zero curves from them.
- ``FxStore`` holds the Frankfurter ECB reference rates (units of currency per USD) used only as the FX fallback.
- UNITS — the one place the rate units of the terminal are defined (display-units fix, 2026-10-21):
  * Every observation this module stores or returns — FRED series (SOFR, the ``tables.POLICY_INDEX`` indices, the OAS
    series, VIXCLS), ESTR, the term-curve grids and the Treasury par curve — is a PERCENT exactly as published:
    ``Decimal("3.87")`` means 3.87 %.  EXCEPTIONS: the compounded indices ``ESTR_INDEX`` (110.09953057), ``SOFR_INDEX``
    (1.26467626) and ``SONIA_INDEX`` (121.79978731) are index LEVELS - never a percent, never divided by 100 (a coupon uses the
    RATIO of two levels: ``fxcurves.index_factor``) - and the Svensson parameters of ``ECB_AAA_SV`` are model parameters.
    ``latest`` / ``as_of_le`` / ``series`` / ``policy_rate`` / ``oas`` / ``vix`` / ``sofr`` and ``CurveStore.par_yield``
    all return percents.
  * Spreads, fees and every accrual rate are FRACTIONS: ``tables.RATES`` spreads (−0.0050 = −0.50 %), ``LOAN_SPREAD``,
    the borrow tiers ``HTB``, every PB schedule field named ``*_spread`` / ``*_rate`` / ``htb_add``.
  * ``pct_to_fraction()`` is THE percent → fraction conversion (÷ 100, exactly once).  An accrual rate is
    ``pct_to_fraction(index) + spread`` (a fraction) and a night accrues ``balance × rate / basis``: 1,000,000.00 at
    SOFR 3.87 % − 0.50 % for one night ACT/360 is 1,000,000 × 0.0337 / 360 = 93.61.  ``*_fraction`` accessors
    (``sofr_fraction``, ``treasury_cash_rate``) divide here so a caller never divides again.  Audited on 2026-10-21,
    each accrual divides its index percent by 100 exactly once: ``lifecycle_jobs`` (Treasury cash interest, PB
    financing, PB credit), ``prime`` (financing / credit rates), ``lending_accruals.rebate_rate``, ``credit.loan_rate``,
    ``products.private_credit.coupon_rate``, ``products.swaps`` fixings, ``marks`` (bond yields, OAS, CIP forwards,
    the ^IRX rate).  ``performance.sofr_input`` passes ``sofr()`` (a percent) where ``statistics()`` needs a fraction
    (it raises "SOFR must be a decimal fraction"): it must read ``sofr_fraction()`` instead.
  * Over JSON: ``/api/rates`` values are percents and say so (``unit: "percent"``, ``view()``); ``/api/curve`` par
    ``yield`` values are percents; an observation echoed elsewhere (``index.value`` of a loan, ``sofr`` of a fixing) is
    a percent; every other rate field (``rate``, ``*_rate``, ``spread``, ``all_in``, ``segment_rate``) is a fraction, and a
    percent elsewhere is named ``*_pct``.  The UI formats a fraction with ``T.fmt.rate`` and a percent with
    ``T.fmt.ratePct`` (``static/core.js``).
- DATED CURVES (C12, FINSIM1_PLAN P4): ``CurveStore.for_date(d)`` is the curve OF a past date - the UST_PAR curve dated d, else
  the last business date before it within ``FOR_DATE_LOOKBACK_BD`` (3) business days (an NYSE business day without a Treasury
  curve, such as Veterans Day, values on the previous one) - as a read-only view with the same ``discount_factors`` /
  ``par_rate`` / ``annuity`` method as the live store (``DatedCurve``); None when there is none.  It reads ``curve_points``
  (cached per date; ``load_persisted`` clears the cache), never only the in-memory latest curve, so a curve an importer records
  after the runtime was built is visible.  A store with fixed discount factors (the test fixture) serves them for every date on
  or after their own date.
- Persistence: every observation goes to ``curve_points`` (series, as_of, tenor, value) through the light command
  ``rates.record`` (idempotent request ids derived from the payload hash); ``marks.record`` writes ``marks`` rows
  (CLOSE / SETTLE / CURVE) for the lifecycle jobs.  Stores work without a database (pure, for tests).
"""
from __future__ import annotations

import hashlib
import threading
from datetime import date, timedelta
from decimal import Decimal, getcontext
from typing import Dict, Iterable, List, Optional, Tuple

from .. import tables
from ..commands import Command
from ..db import canonical_json
from ..units import qprice
from .providers import FxRates, ParCurve, TENOR_Q, TENOR_YEARS

getcontext().prec = 34
CURVE_SERIES = "UST_PAR"
FX_SERIES = "FRANKFURTER_USD"
FOR_DATE_LOOKBACK_BD = 3              # C12: a dated curve looks back at most 3 business days (Veterans Day has no Treasury curve)
OBS_TENOR = "OBS"
HALF = Decimal("0.5")
DF_Q = Decimal("0.0000000001")
SHIFT_SYMBOLS: Dict[str, Decimal] = {"^IRX": Decimal("0.25"), "^FVX": Decimal(5), "^TNX": Decimal(10), "^TYX": Decimal(30)}
OAS_SERIES = {"AAA": "BAMLC0A1CAAA", "AA": "BAMLC0A2CAA", "A": "BAMLC0A3CA", "BBB": "BAMLC0A4CBBB", "BB": "BAMLH0A1HYBB", "B": "BAMLH0A2HYB",
              "CCC": "BAMLH0A3HYC", "HY": "BAMLH0A0HYM2"}
#: OECD main economic indicators, long-term government bond yields (10-year), MONTHLY averages on FRED: the gilt / OAT / BTP
#: benchmarks of the Debt / Foreign Markets pages are LIMITED to these (the Bank of England database refuses scripts)
OECD_10Y = {"GB": "IRLTLT01GBM156N", "FR": "IRLTLT01FRM156N", "IT": "IRLTLT01ITM156N"}
#: FX_CCS_PLAN K2 (1.0.6.1): SONIA (IUDSOIA) is the worker's ``rfr`` job's now - the Bank of England database first, FRED only when the
#: Bank fails - so the 6-hourly FRED slot no longer fetches it
RFR_OWNED = ("IUDSOIA",)
FRED_SERIES = tuple(s for s in dict.fromkeys(("SOFR",) + tuple(tables.POLICY_INDEX.values()) + ("BAMLH0A0HYM2", "BAMLC0A0CM", "VIXCLS")
                                             + tuple(v for k, v in OAS_SERIES.items() if k != "HY") + tuple(OECD_10Y.values()))
                    if s not in RFR_OWNED)   # each series once
#: FX_CCS_PLAN rev 2 K2: the RFR series and their compounded indices (index LEVELS, not percents), and the dated term-curve grids
RFR_SERIES = ("ESTR", "IUDSOIA", "SOFR")
INDEX_SERIES = ("ESTR_INDEX", "SOFR_INDEX", "SONIA_INDEX")
BOE_SERIES = "BOE_OIS_SPOT"
ECB_SPOT_SERIES = "ECB_AAA_SPOT"
ECB_SV_SERIES = "ECB_AAA_SV"
TERM_SERIES = (BOE_SERIES, ECB_SPOT_SERIES, ECB_SV_SERIES)
CURVE_NODE_Q = Decimal("0.000001")         # a term-curve value is kept to 6 decimals of a percent (0.0001 bp): the node value used
GRID_KEEP_DATES = 40                       # dated grids per series kept in memory (older dates are read from curve_points on demand)
PERCENT = Decimal(100)
UNIT_PERCENT = "percent"
UNIT_FRACTION = "fraction"
UNITS_NOTE = ("FRED and par-curve values are percents as published (3.87 = 3.87 %); spreads, fees and accrual rates are fractions "
              "(0.0337 = 3.37 %); a percent outside /api/rates and /api/curve is named *_pct; the compounded indices ESTR_INDEX, SOFR_INDEX and "
              "SONIA_INDEX are index levels, not percents")


def _rid(prefix: str, payload: dict) -> str:
    sha = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{sha}"


def pct_to_fraction(pct) -> Decimal:
    """THE percent → fraction conversion (module docstring UNITS): ``3.87`` (FRED quoting) → ``0.0387``.  Divides by 100
    exactly once; the result is canonical (``qprice``: at most 8 decimals).  Floats are refused (``TypeError``)."""
    if isinstance(pct, float):
        raise TypeError("a rate must be a Decimal, str or int, not a float")
    return qprice(Decimal(str(pct)) / PERCENT)


def fraction_to_pct(fraction) -> Decimal:
    """A fraction shown as a percent (display only): ``0.0337`` → ``3.37``.  Never feed the result to an accrual."""
    if isinstance(fraction, float):
        raise TypeError("a rate must be a Decimal, str or int, not a float")
    return qprice(Decimal(str(fraction)) * PERCENT)


def treasury_cash_rate(sofr_pct) -> Decimal:
    """The Treasury cash-interest accrual rate as a FRACTION: ``max(floor, SOFR / 100 + spread)`` with
    ``tables.RATES["treasury_cash"]`` (spread −0.0050, floor 0; sentence CASH_INT).  SOFR 3.87 → 0.0387 − 0.0050 =
    0.0337; one night on 1,000,000.00 ACT/360 = 1,000,000 × 0.0337 / 360 = 93.61.  The same arithmetic as the nightly
    accrual (``lifecycle_jobs`` cash interest), so what the Treasury page shows is what accrues."""
    spec = tables.RATES["treasury_cash"]
    return qprice(max(Decimal(str(spec["floor"])), pct_to_fraction(sofr_pct) + Decimal(str(spec["spread"]))))


def _interp(points: List[Tuple[Decimal, Decimal]], t: Decimal) -> Optional[Decimal]:
    """Linear interpolation in the first coordinate, flat beyond the ends; None on an empty list."""
    if not points:
        return None
    pts = sorted(points)
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return v0
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def bootstrap_dfs(par_at, max_years: Decimal, step: Decimal = HALF) -> Dict[Decimal, Decimal]:
    """Semi-annual discount factors from a par-yield function (percent) — see the module docstring."""
    dfs: Dict[Decimal, Decimal] = {}
    cum = Decimal(0)
    t = step
    while t <= max_years:
        c = Decimal(par_at(t)) / Decimal(100)
        alpha = step
        df = (Decimal(1) - c * alpha * cum) / (Decimal(1) + c * alpha)
        df = df.quantize(DF_Q)
        dfs[t.quantize(Decimal("0.0001"))] = df
        cum += df
        t += step
    return dfs


class CurveStore:
    def __init__(self, db=None, bus=None, clock=None, log=None):
        self.db, self.bus, self.clock, self.log = db, bus, clock, log
        self._lock = threading.RLock()
        self._curve: Optional[ParCurve] = None
        self._fixed: Optional[Tuple[Dict[Decimal, Decimal], date]] = None
        self._by_date: Dict[date, ParCurve] = {}          # C12: the curves of past dates seen in memory or read from curve_points

    # ---- state ---------------------------------------------------------------------------------------------------
    def latest(self) -> Optional[ParCurve]:
        with self._lock:
            return self._curve

    def as_of(self) -> Optional[date]:
        with self._lock:
            if self._curve is not None:
                return self._curve.as_of
            return self._fixed[1] if self._fixed is not None else None

    def put(self, curve: ParCurve, persist: bool = True) -> None:
        with self._lock:
            if curve is not None and curve.points:
                self._by_date[curve.as_of] = curve          # C12: every dated curve stays reachable through for_date
            if self._curve is not None and curve.as_of < self._curve.as_of:
                return
            self._curve = curve
        if persist:
            self._persist(curve)

    def set_fixed_dfs(self, dfs: Dict, as_of) -> None:
        """Discount factors given directly (the §19.15 FixedCurve); ``discount_factors()`` then returns them."""
        d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
        with self._lock:
            self._fixed = ({Decimal(str(t)).quantize(Decimal("0.0001")): Decimal(str(v)) for t, v in dfs.items()}, d)

    def clear_fixed_dfs(self) -> None:
        with self._lock:
            self._fixed = None

    # ---- derived -----------------------------------------------------------------------------------------------------
    def par_yield(self, t_years, shifts: Optional[Dict[Decimal, Decimal]] = None) -> Optional[Decimal]:
        """Par yield in percent at ``t_years`` (linear in maturity; flat beyond the ends) plus the interpolated
        intraday shift when given."""
        curve = self.latest()
        if curve is None or not curve.points:
            return None
        t = Decimal(str(t_years))
        y = _interp(list(curve.points.items()), t)
        if y is None:
            return None
        if shifts:
            s = _interp(list(shifts.items()), t)
            if s is not None:
                y += s
        return y.quantize(Decimal("0.000001"))

    def intraday_shifts(self, cache) -> Tuple[Dict[Decimal, Decimal], dict]:
        """``({tenor_years: shift_pct}, inputs)`` from the Yahoo yield records in ``cache`` (``last − prev_close``);
        records without both values are skipped; ``inputs`` carries each record's quality and time."""
        shifts: Dict[Decimal, Decimal] = {}
        inputs: dict = {}
        if cache is None:
            return shifts, inputs
        for sym, t in SHIFT_SYMBOLS.items():
            rec = cache.get(sym)
            if rec is None or rec.last is None or rec.prev_close is None:
                continue
            shifts[t] = qprice(rec.last - rec.prev_close)
            inputs[sym] = {"shift": str(shifts[t]), "quality": rec.quality, "quote_time": rec.quote_time}
        return shifts, inputs

    def discount_factors(self, max_years=None, shifts: Optional[Dict[Decimal, Decimal]] = None) -> Optional[Dict[Decimal, Decimal]]:
        """Semi-annual discount factors (bootstrapped; or the fixed set when one was given).  ``max_years`` defaults
        to the curve's last tenor (30) or the fixed set's horizon."""
        with self._lock:
            fixed = self._fixed
            curve = self._curve
        if fixed is not None:
            dfs = dict(fixed[0])
            if max_years is not None:
                m = Decimal(str(max_years))
                dfs = {t: v for t, v in dfs.items() if t <= m}
            return dfs
        if curve is None or not curve.points:
            return None
        horizon = Decimal(str(max_years)) if max_years is not None else max(curve.points)
        return bootstrap_dfs(lambda t: self.par_yield(t, shifts), horizon)

    def annuity(self, years, shifts=None) -> Optional[Decimal]:
        """``Σ α × df`` for the semi-annual schedule to ``years`` (α = 0.5)."""
        dfs = self.discount_factors(years, shifts)
        if not dfs:
            return None
        return (sum(dfs.values(), Decimal(0)) * HALF).quantize(DF_Q)

    def par_rate(self, years, shifts=None) -> Optional[Decimal]:
        """The semi-annual par swap rate implied by the discount factors: ``2 × (1 − df(T)) / Σ df`` (decimal)."""
        dfs = self.discount_factors(years, shifts)
        if not dfs:
            return None
        last = dfs[max(dfs)]
        return ((Decimal(1) - last) * 2 / sum(dfs.values(), Decimal(0))).quantize(DF_Q)

    # ---- dated curves (C12) -------------------------------------------------------------------------------------------
    def fixed_as_of(self) -> Optional[date]:
        with self._lock:
            return None if self._fixed is None else self._fixed[1]

    def _curve_rows(self, d: date) -> Optional[ParCurve]:
        """The UST_PAR curve dated exactly ``d`` from ``curve_points`` (None without a database or a row)."""
        if self.db is None:
            return None
        try:
            with self.db.reader() as conn:
                rows = conn.execute("SELECT tenor, value, source, fetched_ts FROM curve_points WHERE series=? AND as_of=?",
                                    (CURVE_SERIES, d.isoformat())).fetchall()
        except Exception:   # noqa: BLE001 - a database without curve_points (a pure store) has no dated curve
            return None
        pts: Dict[Decimal, Decimal] = {}
        src, fts = "treasury.gov daily par yield curve", 0
        for r in rows:
            years = TENOR_YEARS.get(r["tenor"])
            if years is None:
                try:
                    years = Decimal(r["tenor"]).quantize(TENOR_Q)
                except ArithmeticError:
                    continue
            try:
                pts[years] = Decimal(r["value"])
            except ArithmeticError:
                continue
            src, fts = r["source"] or src, int(r["fetched_ts"] or 0)
        return ParCurve(d, pts, source=src, fetched_at=fts) if pts else None

    def curve_on(self, d) -> Optional[ParCurve]:
        """The par curve dated exactly ``d``: the latest in memory when it is of that date, else the per-date cache, else
        ``curve_points`` (cached once found; a miss is not cached, so a curve recorded later is seen)."""
        d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        with self._lock:
            latest = self._curve
            if latest is not None and latest.as_of == d and latest.points:
                return latest
            got = self._by_date.get(d)
        if got is not None:
            return got
        got = self._curve_rows(d)
        if got is not None:
            with self._lock:
                self._by_date[d] = got
        return got

    def for_date(self, d, cal=None, lookback_bd: int = FOR_DATE_LOOKBACK_BD) -> Optional["DatedCurve"]:
        """C12: the curve view OF date ``d`` - the UST_PAR curve of ``d``, else of the last business date before it within
        ``lookback_bd`` business days (``cal``: the trading calendar; without one, Monday to Friday) - with the live store's
        ``discount_factors`` / ``par_rate`` / ``annuity``.  None when absent.  Fixed discount factors (the test fixture) are served
        for every date on or after their own date."""
        d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        with self._lock:
            fixed = self._fixed
        if fixed is not None:
            if fixed[1] > d:
                return None
            view = DatedCurve(d)
            view.set_fixed_dfs(dict(fixed[0]), fixed[1])
            return view

        def business(x: date) -> bool:
            if cal is not None and hasattr(cal, "is_business_day"):
                return bool(cal.is_business_day(x))
            return x.weekday() < 5
        x, seen = d, 0
        for _ in range(int(lookback_bd) * 3 + 7):         # calendar days: enough for 3 business days across a long weekend
            if x == d or business(x):
                got = self.curve_on(x)
                if got is not None:
                    view = DatedCurve(d)
                    view.put(got, persist=False)
                    return view
                if x != d:
                    seen += 1
                    if seen >= int(lookback_bd):
                        break
            x = x - timedelta(days=1)
        return None

    # ---- persistence ---------------------------------------------------------------------------------------------------
    def _persist(self, curve: ParCurve) -> None:
        if self.bus is None and self.db is None:
            return
        payload = {"series": CURVE_SERIES, "as_of": curve.as_of.isoformat(), "points": {label: str(v) for label, v in curve.labels().items()},
                   "source": curve.source, "fetched_ts": int(curve.fetched_at or 0)}
        _record(self.bus, self.db, payload)

    def load_persisted(self) -> bool:
        if self.db is None:
            return False
        with self._lock:
            self._by_date = {}                               # C12: the per-date cache is re-read from curve_points
        with self.db.reader() as conn:
            row = conn.execute("SELECT MAX(as_of) AS d FROM curve_points WHERE series=?", (CURVE_SERIES,)).fetchone()
            if row is None or row["d"] is None:
                return False
            rows = conn.execute("SELECT tenor, value, source, fetched_ts FROM curve_points WHERE series=? AND as_of=?", (CURVE_SERIES, row["d"])).fetchall()
        pts: Dict[Decimal, Decimal] = {}
        src, fts = "treasury.gov daily par yield curve", 0
        for r in rows:
            years = TENOR_YEARS.get(r["tenor"])
            if years is None:
                try:
                    years = Decimal(r["tenor"]).quantize(TENOR_Q)
                except ArithmeticError:
                    continue
            pts[years] = Decimal(r["value"])
            src, fts = r["source"] or src, int(r["fetched_ts"] or 0)
        if not pts:
            return False
        self.put(ParCurve(date.fromisoformat(row["d"]), pts, source=src, fetched_at=fts), persist=False)
        return True


class DatedCurve(CurveStore):
    """C12: a read-only view of ONE dated curve (``CurveStore.for_date``): the same ``par_yield`` / ``discount_factors`` /
    ``annuity`` / ``par_rate`` as the live store; ``requested`` is the date asked for, ``as_of()`` the curve's own date (it may be up
    to 3 business days earlier).  Nothing is persisted."""

    def __init__(self, requested: date):
        super().__init__(None, None, None, None)
        self.requested = requested

    def put(self, curve: ParCurve, persist: bool = False) -> None:
        super().put(curve, persist=False)

    def for_date(self, d, cal=None, lookback_bd: int = FOR_DATE_LOOKBACK_BD):
        return self


class Grid:
    """One dated term-curve grid (``RateStore.put_grid``): ``series``, ``as_of``, ``points`` {tenor label: Decimal} (percent,
    continuously compounded, 6 dp for the spot curves; model parameters for ``ECB_AAA_SV``), ``source``, ``fetched_ts``."""

    __slots__ = ("series", "as_of", "points", "source", "fetched_ts")

    def __init__(self, series: str, as_of: date, points: Dict[str, Decimal], source: str = "", fetched_ts: int = 0):
        self.series, self.as_of, self.points, self.source, self.fetched_ts = series, as_of, dict(points), source, int(fetched_ts or 0)

    def __repr__(self) -> str:
        return f"<Grid {self.series} {self.as_of.isoformat()} {len(self.points)} points>"

    def to_dict(self) -> dict:
        return {"series": self.series, "as_of": self.as_of.isoformat(), "points": {k: str(v) for k, v in self.points.items()}, "source": self.source,
                "fetched_ts": self.fetched_ts}


def _grid_value(series: str, v) -> Decimal:
    """A grid value as stored: a spot rate to 6 decimals of a percent (``CURVE_NODE_Q``), a Svensson parameter to 8 dp."""
    d = Decimal(str(v))
    return d.quantize(CURVE_NODE_Q) if series in (BOE_SERIES, ECB_SPOT_SERIES) else qprice(d)


class RateStore:
    def __init__(self, db=None, bus=None, log=None):
        self.db, self.bus, self.log = db, bus, log
        self._lock = threading.RLock()
        self._obs: Dict[str, List[Tuple[date, Decimal]]] = {}
        self._source: Dict[str, str] = {}
        self._obs_src: Dict[str, Dict[date, str]] = {}          # 1.0.6.1: the source of each observation (curve_points.source)
        self._seen: Dict[str, Dict[str, date]] = {}             # 1.0.6.1: per series, the latest observation date each source gave
        self._grids: Dict[str, Dict[date, Grid]] = {}           # 1.0.6.1: the dated term-curve grids of TERM_SERIES
        self.fetch_status: Dict[str, dict] = {}                 # 1.0.6.1: the worker's last fetch of each RFR series / grid ({ok, at_ts, text, ...})
        self.route = None                    # the provider's FredRoute (module docstring SOURCES), set at bind time

    def put(self, series_id: str, observations: Iterable[Tuple[date, Decimal]], source: str = "FRED", persist: bool = True) -> int:
        """Merge ``observations`` (new dates added, a date's value replaced; nothing ever removed).  The label is the
        observations' own ``source`` when they carry one (a fallback provider), else ``source``."""
        own = getattr(observations, "source", None)
        if isinstance(own, str) and own:
            source = own
        rows = [(d if isinstance(d, date) else date.fromisoformat(str(d)), qprice(v)) for d, v in observations]
        if not rows:
            return 0
        with self._lock:
            merged = dict(self._obs.get(series_id, []))
            merged.update(rows)
            self._obs[series_id] = sorted(merged.items())
            self._source[series_id] = source
            srcs = self._obs_src.setdefault(series_id, {})
            for d, _v in rows:
                srcs[d] = source
            seen = self._seen.setdefault(series_id, {})
            last = max(d for d, _v in rows)
            if seen.get(source) is None or seen[source] < last:
                seen[source] = last
        if persist:
            _record(self.bus, self.db, {"series": series_id, "observations": [[d.isoformat(), str(v)] for d, v in rows], "source": source})
        return len(rows)

    def latest(self, series_id: str) -> Optional[Tuple[date, Decimal]]:
        with self._lock:
            obs = self._obs.get(series_id)
            return obs[-1] if obs else None

    def as_of_le(self, series_id: str, d: date) -> Optional[Tuple[date, Decimal]]:
        """RATE_LAG: the latest observation on or before ``d``."""
        with self._lock:
            obs = self._obs.get(series_id) or []
        best = None
        for od, v in obs:
            if od <= d:
                best = (od, v)
            else:
                break
        return best

    def series(self, series_id: str) -> List[Tuple[date, Decimal]]:
        with self._lock:
            return list(self._obs.get(series_id) or [])

    def observation(self, series_id: str, d, exact: bool = False) -> Optional[dict]:
        """1.0.6.1: the observation of ``series_id`` dated ``d`` (``exact``) or the latest on or before ``d`` (RATE_LAG) with its own
        source: ``{series, obs_date, value, source}`` (``value`` a Decimal; a percent, or an index level for ``*_INDEX``); None when absent."""
        d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        got = None
        with self._lock:
            for od, v in self._obs.get(series_id) or []:
                if od < d:
                    got = None if exact else (od, v)
                elif od == d:
                    got = (od, v)
                    break
                else:
                    break
            if got is None:
                return None
            src = (self._obs_src.get(series_id) or {}).get(got[0]) or self._source.get(series_id) or ""
        return {"series": series_id, "obs_date": got[0], "value": got[1], "source": src}

    def sources_seen(self, series_id: str) -> Dict[str, str]:
        """1.0.6.1: ``{source: latest observation date (ISO)}`` of a series - for SONIA both the Bank of England's and FRED's as-of dates."""
        with self._lock:
            return {k: v.isoformat() for k, v in (self._seen.get(series_id) or {}).items()}

    # ---- the term-curve grids (1.0.6.1, module docstring RFR AND TERM CURVES) ----------------------------------------------
    def put_grid(self, series: str, as_of, points: Dict[str, object], source: str = "", fetched_ts: int = 0, persist: bool = True) -> Grid:
        """Store one dated grid (a date's grid is replaced as a whole; nothing else is ever removed) and persist it through
        ``rates.record`` (``curve_points``: series, as_of, tenor label, value)."""
        if series not in TERM_SERIES:
            raise ValueError(f"put_grid: {series!r} is not a term-curve series ({', '.join(TERM_SERIES)})")
        d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
        pts = {str(k): _grid_value(series, v) for k, v in dict(points).items()}
        if not pts:
            raise ValueError(f"put_grid: {series} {d.isoformat()} has no points")
        g = Grid(series, d, pts, source, fetched_ts)
        with self._lock:
            by = self._grids.setdefault(series, {})
            by[d] = g
            for old in sorted(by)[:-GRID_KEEP_DATES]:
                by.pop(old, None)
        if persist:
            _record(self.bus, self.db, {"series": series, "as_of": d.isoformat(), "points": {k: str(v) for k, v in pts.items()}, "source": source,
                                        "fetched_ts": int(fetched_ts or 0)})
        return g

    def _grid_rows(self, series: str, d: date, on_or_before: bool) -> Optional[Grid]:
        if self.db is None:
            return None
        try:
            with self.db.reader() as conn:
                if on_or_before:
                    row = conn.execute("SELECT MAX(as_of) AS d FROM curve_points WHERE series=? AND as_of<=?", (series, d.isoformat())).fetchone()
                    if row is None or row["d"] is None:
                        return None
                    day = row["d"]
                else:
                    day = d.isoformat()
                rows = conn.execute("SELECT tenor, value, source, fetched_ts FROM curve_points WHERE series=? AND as_of=?", (series, day)).fetchall()
        except Exception:   # noqa: BLE001 - a database without curve_points (a pure store) has no grid
            return None
        pts: Dict[str, Decimal] = {}
        src, fts = "", 0
        for r in rows:
            try:
                pts[str(r["tenor"])] = _grid_value(series, r["value"])
            except ArithmeticError:
                continue
            src, fts = r["source"] or src, int(r["fetched_ts"] or 0)
        if not pts:
            return None
        return Grid(series, date.fromisoformat(day), pts, src, fts)     # not cached: memory holds only the latest dates, without gaps

    def grid(self, series: str, as_of) -> Optional[Grid]:
        """The grid of ``series`` dated exactly ``as_of`` (memory, else ``curve_points``)."""
        d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
        with self._lock:
            got = (self._grids.get(series) or {}).get(d)
        return got if got is not None else self._grid_rows(series, d, False)

    def grid_on_or_before(self, series: str, d) -> Optional[Grid]:
        """The latest grid of ``series`` dated on or before ``d``.  Memory holds the latest ``GRID_KEEP_DATES`` dates without gaps (every
        grid this process stores goes to memory first), so a date inside that window is answered from memory; an older date, or a store
        with nothing in memory, reads ``curve_points``."""
        d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        with self._lock:
            by = self._grids.get(series) or {}
            dates = [x for x in by if x <= d]
            got = by[max(dates)] if dates else None
        if got is not None:
            return got
        return self._grid_rows(series, d, True) if self.db is not None else None

    def latest_grid(self, series: str) -> Optional[Grid]:
        """The latest grid of ``series`` (memory or ``curve_points``)."""
        with self._lock:
            by = self._grids.get(series) or {}
            got = by[max(by)] if by else None
        if self.db is not None:
            db_got = self._grid_rows(series, date(9999, 12, 31), True)
            if db_got is not None and (got is None or db_got.as_of > got.as_of):
                return db_got
        return got

    def grid_dates(self, series: str) -> List[date]:
        """The dates of ``series`` held in memory."""
        with self._lock:
            return sorted((self._grids.get(series) or {}))

    def source(self, series_id: str) -> Optional[str]:
        with self._lock:
            return self._source.get(series_id)

    def policy_rate(self, ccy: str) -> Optional[Tuple[date, Decimal]]:
        sid = tables.POLICY_INDEX.get(ccy.upper())
        return self.latest(sid) if sid else None

    def policy_series(self, ccy: str) -> Optional[str]:
        return tables.POLICY_INDEX.get(ccy.upper())

    def sofr(self) -> Optional[Tuple[date, Decimal]]:
        """``(observation date, PERCENT)`` — the latest SOFR print as published (3.87 = 3.87 %)."""
        return self.latest("SOFR")

    def sofr_fraction(self) -> Optional[Tuple[date, Decimal]]:
        """``(observation date, FRACTION)`` — ``sofr()`` divided by 100 here, exactly once (0.0387 = 3.87 %): the input
        the performance statistics need (§6.9 rf = SOFR / 360)."""
        got = self.sofr()
        return None if got is None else (got[0], pct_to_fraction(got[1]))

    def oas(self, bucket: str) -> Optional[Tuple[date, Decimal]]:
        sid = OAS_SERIES.get(bucket.upper())
        return self.latest(sid) if sid else None

    def vix(self) -> Optional[Tuple[date, Decimal]]:
        return self.latest("VIXCLS")

    def fred_status(self) -> dict:
        """The FRED route's state for the views: ``{maintenance, since_ts, note}`` (module docstring SOURCES)."""
        route = self.route
        st = route.status() if route is not None and hasattr(route, "status") else {}
        out = {"maintenance": bool(st.get("maintenance")), "since_ts": st.get("since_ts"), "paused_until_ts": st.get("paused_until_ts"),
               "key_route": st.get("key_route"), "fallbacks": st.get("fallbacks") or {}}
        out["note"] = ("FRED unavailable (maintenance): every FRED series keeps its last observation with its true date; SOFR and the ECB deposit "
                       "rate come from their free fallbacks (labelled)") if out["maintenance"] else ""
        return out

    def view(self) -> dict:
        """``/api/rates`` shape: ``{SOFR: {value, as_of, unit, source}, policy: {ccy: {...}}, oas: {...}, long_term: {...},
        fred: {maintenance, note}, units}``; every ``value`` is a PERCENT as published (``unit: "percent"``, module docstring
        UNITS); ``source`` says where it came from (FRED, or a labelled fallback)."""
        def one(sid):
            got = self.latest(sid) if sid else None
            return {"series": sid, "value": None if got is None else str(got[1]), "as_of": None if got is None else got[0].isoformat(),
                    "unit": UNIT_PERCENT, "monthly_proxy": sid in tables.POLICY_INDEX_MONTHLY or sid in OECD_10Y.values(),
                    "source": None if got is None else (self.source(sid) or "FRED")}
        return {"SOFR": one("SOFR"), "policy": {ccy: one(sid) for ccy, sid in tables.POLICY_INDEX.items()},
                "oas": {b: one(sid) for b, sid in OAS_SERIES.items()}, "long_term": {c: one(sid) for c, sid in OECD_10Y.items()}, "VIXCLS": one("VIXCLS"),
                # OTC-R1-05: the two index OAS series the CDX desk uses as its labelled proxy spreads (IG = US Corporate, HY = US High Yield)
                "cdx_proxy": {"IG": one("BAMLC0A0CM"), "HY": one("BAMLH0A0HYM2")},
                # FX_CCS_PLAN K2 (1.0.6.1): the overnight RFRs with their compounded indices and the term-curve grids (dates and sources)
                "rfr": self.rfr_view(), "term_curves": self.term_curves_view(),
                "fred": self.fred_status(), "units": UNITS_NOTE}

    def rfr_view(self) -> dict:
        """``{ccy: {series, value, as_of, unit, source, sources_seen, fetch, index: {series, value, as_of, unit "index level", source, fetch}}}``
        for USD / EUR / GBP (``tables.RFR``); values are what is stored (percent; index levels), never a default."""
        out = {}
        for ccy, spec in tables.RFR.items():
            row = {}
            for key, sid, unit in (("rate", spec["series"], UNIT_PERCENT), ("index", spec["index"], "index level")):
                got = self.latest(sid)
                obs = self.observation(sid, got[0], exact=True) if got is not None else None
                row[key] = {"series": sid, "value": None if got is None else str(got[1]), "as_of": None if got is None else got[0].isoformat(), "unit": unit,
                            "source": None if obs is None else obs["source"], "sources_seen": self.sources_seen(sid), "fetch": self.fetch_status.get(sid)}
            out[ccy] = dict(row["rate"], name=spec["name"], basis=spec["basis"], index=row["index"])
        return out

    def term_curves_view(self) -> dict:
        """``{series: {as_of, points, source, fetch}}`` of the latest stored grid of each term-curve series (no grid: as_of None)."""
        out = {}
        for sid in TERM_SERIES:
            g = self.latest_grid(sid)
            out[sid] = {"as_of": None if g is None else g.as_of.isoformat(), "points": 0 if g is None else len(g.points), "source": None if g is None else g.source,
                        "fetch": self.fetch_status.get(sid), "unit": "percent, continuously compounded" if sid != ECB_SV_SERIES else "Svensson parameters"}
        return out

    def load_persisted(self) -> int:
        if self.db is None:
            return 0
        n = 0
        with self.db.reader() as conn:
            rows = conn.execute("SELECT series, as_of, value, source FROM curve_points WHERE tenor=? ORDER BY series, as_of", (OBS_TENOR,)).fetchall()
            grid_rows = []
            for sid in TERM_SERIES:      # the last GRID_KEEP_DATES dates of each term-curve series (older ones are read on demand)
                ds = [r[0] for r in conn.execute("SELECT DISTINCT as_of FROM curve_points WHERE series=? ORDER BY as_of DESC LIMIT ?",
                                                 (sid, GRID_KEEP_DATES)).fetchall()]
                if ds:
                    marks = ",".join("?" * len(ds))
                    grid_rows += conn.execute(f"SELECT series, as_of, tenor, value, source, fetched_ts FROM curve_points WHERE series=? AND as_of IN ({marks})",
                                              (sid, *ds)).fetchall()
        by: Dict[str, List[Tuple[date, Decimal, str]]] = {}
        for r in rows:
            try:
                by.setdefault(r["series"], []).append((date.fromisoformat(r["as_of"]), Decimal(r["value"]), r["source"] or "FRED"))
                n += 1
            except (ValueError, ArithmeticError):
                continue
        for sid, obs in by.items():
            # each observation keeps its own source (a fallback's rows stay labelled as the fallback); the series label is the latest's
            for src in dict.fromkeys(o[2] for o in obs):
                self.put(sid, [(d, v) for d, v, s_ in obs if s_ == src], src, persist=False)
            with self._lock:
                self._source[sid] = obs[-1][2]
        grids: Dict[Tuple[str, str], dict] = {}
        for r in grid_rows:
            g = grids.setdefault((r["series"], r["as_of"]), {"points": {}, "source": "", "fetched_ts": 0})
            try:
                g["points"][str(r["tenor"])] = Decimal(r["value"])
            except ArithmeticError:
                continue
            g["source"], g["fetched_ts"] = r["source"] or g["source"], int(r["fetched_ts"] or 0)
        for (sid, d), g in sorted(grids.items()):
            if g["points"]:
                self.put_grid(sid, d, g["points"], g["source"], g["fetched_ts"], persist=False)
                n += 1
        return n


class FxStore:
    """The Frankfurter fallback: ECB reference rates, units of currency per USD, quality ``close``."""

    def __init__(self, db=None, bus=None):
        self.db, self.bus = db, bus
        self._lock = threading.RLock()
        self._rates: Optional[FxRates] = None

    def put(self, rates: FxRates, persist: bool = True) -> None:
        with self._lock:
            if self._rates is not None and rates.as_of < self._rates.as_of:
                return
            self._rates = rates
        if persist:
            _record(self.bus, self.db, {"series": FX_SERIES, "as_of": rates.as_of.isoformat(), "points": {k: str(v) for k, v in rates.per_usd.items()},
                                        "source": rates.source, "fetched_ts": int(rates.fetched_at or 0)})

    def latest(self) -> Optional[FxRates]:
        with self._lock:
            return self._rates

    def usd_per(self, ccy: str) -> Optional[Tuple[Decimal, date, str]]:
        """``(USD per 1 ccy, as_of, source)`` or None."""
        with self._lock:
            r = self._rates
        if r is None:
            return None
        per_usd = r.per_usd.get(ccy.upper())
        if per_usd is None or per_usd <= 0:
            return None
        return qprice(Decimal(1) / per_usd), r.as_of, r.source

    def load_persisted(self) -> bool:
        if self.db is None:
            return False
        with self.db.reader() as conn:
            row = conn.execute("SELECT MAX(as_of) AS d FROM curve_points WHERE series=?", (FX_SERIES,)).fetchone()
            if row is None or row["d"] is None:
                return False
            rows = conn.execute("SELECT tenor, value, source, fetched_ts FROM curve_points WHERE series=? AND as_of=?", (FX_SERIES, row["d"])).fetchall()
        per = {}
        src, fts = "Frankfurter (ECB reference rates)", 0
        for r in rows:
            try:
                per[r["tenor"]] = Decimal(r["value"])
            except ArithmeticError:
                continue
            src, fts = r["source"] or src, int(r["fetched_ts"] or 0)
        if not per:
            return False
        self.put(FxRates(date.fromisoformat(row["d"]), per, source=src, fetched_at=fts), persist=False)
        return True


# ---- persistence through the bus (light commands) ------------------------------------------------------------------------------
def _record(bus, db, payload: dict) -> None:
    if bus is not None:
        res = bus.execute(Command("rates.record", payload, _rid("rates:" + str(payload.get("series")), payload), "worker"))
        if not res.ok:
            raise RuntimeError(f"rates.record failed: {res.code} {res.message}")
        return
    if db is not None:
        with db.writer() as tx:
            _write_points(tx.conn, payload, tx.ts)


def _write_points(conn, payload: dict, ts: int) -> int:
    series = str(payload.get("series") or "")
    if not series:
        raise ValueError("series is required")
    source = str(payload.get("source") or "")
    fts = int(payload.get("fetched_ts") or ts)
    n = 0
    if payload.get("points"):
        as_of = date.fromisoformat(str(payload["as_of"])).isoformat()
        for tenor, value in dict(payload["points"]).items():
            conn.execute("INSERT OR REPLACE INTO curve_points(series, as_of, tenor, value, fetched_ts, source) VALUES (?,?,?,?,?,?)",
                         (series, as_of, str(tenor), str(qprice(value)), fts, source))
            n += 1
    for d, v in payload.get("observations") or []:
        conn.execute("INSERT OR REPLACE INTO curve_points(series, as_of, tenor, value, fetched_ts, source) VALUES (?,?,?,?,?,?)",
                     (series, date.fromisoformat(str(d)).isoformat(), OBS_TENOR, str(qprice(v)), fts, source))
        n += 1
    return n


def h_rates_record(ctx, payload: dict) -> dict:
    """Light: ``{series, as_of, points: {tenor: value}, source}`` (curve / FX) or ``{series, observations: [[date, value]], source}``."""
    return {"rows": _write_points(ctx.conn, payload, ctx.ts), "series": payload.get("series")}


def h_marks_record(ctx, payload: dict) -> dict:
    """Light: ``{rows: [{instrument_id, mark_date, kind: CLOSE|SETTLE|CURVE, price, currency, quality, source, method, quote_time,
    fetched_at, note}]}`` → ``marks`` (INSERT OR REPLACE on the primary key)."""
    n = 0
    for r in payload.get("rows") or []:
        kind = str(r.get("kind") or "CLOSE")
        if kind not in ("CLOSE", "SETTLE", "CURVE"):
            raise ValueError(f"bad mark kind {kind!r}")
        ctx.conn.execute("INSERT OR REPLACE INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (str(r["instrument_id"]), date.fromisoformat(str(r["mark_date"])).isoformat(), kind, str(qprice(r["price"])), str(r.get("currency") or "USD"),
                          str(r.get("quality") or "close"), str(r.get("source") or ""), str(r.get("method") or "direct"),
                          None if r.get("quote_time") is None else int(r["quote_time"]), int(r.get("fetched_at") or ctx.ts), r.get("note")))
        n += 1
    return {"rows": n}


def record_marks(bus, rows: List[dict]) -> None:
    payload = {"rows": rows}
    res = bus.execute(Command("marks.record", payload, _rid("marks", payload), "worker"))
    if not res.ok:
        raise RuntimeError(f"marks.record failed: {res.code} {res.message}")


def register(bus) -> None:
    bus.register("rates.record", h_rates_record, light=True)
    bus.register("marks.record", h_marks_record, light=True)


__all__ = ["CurveStore", "DatedCurve", "FOR_DATE_LOOKBACK_BD", "RateStore", "FxStore", "Grid", "RFR_OWNED", "RFR_SERIES", "INDEX_SERIES", "TERM_SERIES",
           "BOE_SERIES", "ECB_SPOT_SERIES", "ECB_SV_SERIES", "CURVE_NODE_Q", "GRID_KEEP_DATES", "bootstrap_dfs", "register", "record_marks", "FRED_SERIES", "OAS_SERIES", "OECD_10Y", "SHIFT_SYMBOLS",
           "CURVE_SERIES", "FX_SERIES", "OBS_TENOR", "h_rates_record", "h_marks_record", "pct_to_fraction", "fraction_to_pct", "treasury_cash_rate",
           "PERCENT", "UNIT_PERCENT", "UNIT_FRACTION", "UNITS_NOTE"]
