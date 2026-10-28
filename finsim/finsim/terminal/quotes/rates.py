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
- ``FxStore`` holds the Frankfurter ECB reference rates (units of currency per USD) used only as the FX fallback.
- UNITS — the one place the rate units of the terminal are defined (display-units fix, 2026-10-21):
  * Every observation this module stores or returns — FRED series (SOFR, the ``tables.POLICY_INDEX`` indices, the OAS
    series, VIXCLS) and the Treasury par curve — is a PERCENT exactly as published: ``Decimal("3.87")`` means 3.87 %.
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
FRED_SERIES = tuple(dict.fromkeys(("SOFR",) + tuple(tables.POLICY_INDEX.values()) + ("BAMLH0A0HYM2", "BAMLC0A0CM", "VIXCLS")
                                   + tuple(v for k, v in OAS_SERIES.items() if k != "HY") + tuple(OECD_10Y.values())))   # each series once
PERCENT = Decimal(100)
UNIT_PERCENT = "percent"
UNIT_FRACTION = "fraction"
UNITS_NOTE = ("FRED and par-curve values are percents as published (3.87 = 3.87 %); spreads, fees and accrual rates are fractions "
              "(0.0337 = 3.37 %); a percent outside /api/rates and /api/curve is named *_pct")


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


class RateStore:
    def __init__(self, db=None, bus=None, log=None):
        self.db, self.bus, self.log = db, bus, log
        self._lock = threading.RLock()
        self._obs: Dict[str, List[Tuple[date, Decimal]]] = {}
        self._source: Dict[str, str] = {}
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
                "fred": self.fred_status(), "units": UNITS_NOTE}

    def load_persisted(self) -> int:
        if self.db is None:
            return 0
        n = 0
        with self.db.reader() as conn:
            rows = conn.execute("SELECT series, as_of, value, source FROM curve_points WHERE tenor=? ORDER BY series, as_of", (OBS_TENOR,)).fetchall()
        by: Dict[str, List[Tuple[date, Decimal]]] = {}
        src: Dict[str, str] = {}
        for r in rows:
            try:
                by.setdefault(r["series"], []).append((date.fromisoformat(r["as_of"]), Decimal(r["value"])))
                src[r["series"]] = r["source"] or "FRED"
                n += 1
            except (ValueError, ArithmeticError):
                continue
        for sid, obs in by.items():
            self.put(sid, obs, src.get(sid, "FRED"), persist=False)
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


__all__ = ["CurveStore", "DatedCurve", "FOR_DATE_LOOKBACK_BD", "RateStore", "FxStore", "bootstrap_dfs", "register", "record_marks", "FRED_SERIES", "OAS_SERIES", "OECD_10Y", "SHIFT_SYMBOLS",
           "CURVE_SERIES", "FX_SERIES", "OBS_TENOR", "h_rates_record", "h_marks_record", "pct_to_fraction", "fraction_to_pct", "treasury_cash_rate",
           "PERCENT", "UNIT_PERCENT", "UNIT_FRACTION", "UNITS_NOTE"]
