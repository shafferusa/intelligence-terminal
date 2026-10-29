"""FX term curves, the derived cross spot, covered-interest-parity forwards and the compounded RFR indices (FX_CCS_PLAN rev 2 D2, 3.1,
K3; release 1.0.6.1, package P-A).  Reads the stores; never fetches, never writes (``fixture_curves`` aside, which tests call).

One zero curve per currency (D2), continuously compounded, t = ACT/365 days from the curve's own date d0:
- GBP: the first node is SONIA (``IUDSOIA``: the Bank of England database first, FRED the fallback), then the Bank of England UK OIS
  spot curve ``BOE_OIS_SPOT``, 1M..60M at t = m / 12 - OBSERVED.
- EUR: the first node is ESTR, then the ECB euro-area AAA government spot rates ``ECB_AAA_SPOT`` (3M, 6M, 9M, 1Y, 2Y, 3Y, 5Y, 7Y,
  10Y at t = 0.25 .. 10) plus the PB's ``eur_ois_spread_bp`` on every GRID node (not on the ESTR node) - a PROXY plus an ASSUMED
  spread (no free EUR OIS curve).
- USD: the first node is SOFR, then the Treasury par curve bootstrapped semi-annually exactly like the IRS desk
  (``CurveStore.for_date(..).discount_factors()``), z_i = -ln(DF_i) / t_i at t_i = 0.5, 1.0, ... - a PROXY for the SOFR curve.
- The first node sits at t = 1 / 365: z = 365 x ln(1 + r / B) with r the latest print on or before the valuation date (a fraction) and
  B = 360 (ESTR, SOFR) or 365 (SONIA); a print more than ``RFR_MAX_AGE_DAYS`` (10) calendar days old is 409 RATE_UNAVAILABLE.
- A grid more than ``CURVE_MAX_AGE_BD`` (3) business days of its own calendar older than the valuation date (London for the BoE
  curve, TARGET2 for the ECB curve, the SIFMA calendar for the Treasury curve - the IRS desk's dated-curve rule) is 409
  CURVE_UNAVAILABLE naming the source and its last fetch.  Grid nodes must be a gap-free prefix of the published grid (a missing node
  blocks: nothing falls back to a flat rate, to another curve or to a policy rate).
- Interpolation is linear in z (percent) between nodes, flat before the first node and NEVER beyond the last one (a date past it is
  409 CURVE_SHORT).  The exact arithmetic (Decimal, precision 34, ROUND_HALF_EVEN; an independent re-pricer must use the same order):
      z(t)   = z0 + (z1 - z0) * (t - t0) / (t1 - t0)                       (percent; t0 <= t <= t1)
      zr(t)  = z(t) / 100 + bump,  bump = (shift_bp + level_bp) * 0.0001   (a fraction)
      DF(a, b) = exp(-(zr(t_b) * t_b) + zr(t_a) * t_a),  t_x = (x - d0).days / 365
  ``discount(curve, level_bp)`` is the curve with a cross-currency basis LEVEL (D5) on every node (discounting under the USD CSA; a
  projection curve never carries it); ``shifted(curve, bp)`` a parallel bump of every node for a sensitivity; ``with_spread(curve,
  bp)`` the EUR curve rebuilt with another EUR OIS spread.  A grid node keeps the stored value (6 decimals of a percent).
- ``df`` is unquantised; a quote or a record shows a discount factor quantised to 1e-10 HALF_EVEN (``q10``).

The spot (D3, G20): a USD pair is its executable print; a cross is DERIVED from the two USD pairs (base-USD / quote-USD, quantised to
6 dp: never 8 decimals, OTC-20), executable only when both USD prints are executable and their quote times are at most
``LEG_TIME_TOL_S`` (120 s) apart; the cross's own print, when it is executable, is stored with the difference in pips and a difference
beyond ``CROSS_TOL_PIPS`` (5.0) is SPOT_INCONSISTENT.  ``forward``: F = S x DF*_base(S, V) / DF*_quote(S, V) (DFs to 1e-10, F to 6 dp,
4 dp for a JPY-quoted pair), points = (F - S) / pip to 2 dp.

Indices (D4): ``index_factor(services, ccy, s, e)`` = I(e) / I(s) from the stored compounded index (``ESTR_INDEX``, ``SOFR_INDEX``,
``SONIA_INDEX``); EUR falls back to compounding the daily ESTR prints over the TARGET2 days in [s, e) with n_i / 360 weights (every
print required); USD has no fallback (the SOFR Index only).  Otherwise 409 INDEX_PENDING - a coupon is never estimated.
``compounded_rate(factor, days)`` = (factor - 1) x 360 / days, quantised to 1e-7 (7 decimals of a fraction = 0.00001 %), HALF_EVEN.

Every number this module makes is a MODEL value; the labels say which inputs are observed, PROXY or ASSUMED (D16).

The two age limits are module constants read at call time (``RFR_MAX_AGE_DAYS``, ``CURVE_MAX_AGE_BD``; the Treasury curve's look-back
uses the same one).  A TEST that reproduces the plan's after-start numbers - the 2026-10-27 curves valued on 2026-11-02, 4 business days
later - raises ``CURVE_MAX_AGE_BD`` for its own duration (and restores it); the terminal never does.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Dict, List, Optional, Tuple

from . import ccycal, tables
from .commands import ConflictError, RejectError

D = Decimal
RFR_MAX_AGE_DAYS = 10
CURVE_MAX_AGE_BD = 3
CROSS_TOL_PIPS = D("5.0")
LEG_TIME_TOL_S = 120
MODEL_TEXT = "MODEL"
PROXY_TEXT = "PROXY"
OBSERVED_TEXT = "observed"
ASSUMED_TEXT = "ASSUMED"
MODEL_VERSION = "fxcurves-2"
PREC = 34
DF_Q = D("0.0000000001")
SPOT_Q = D("0.000001")
JPY_Q = D("0.0001")
POINTS_Q = D("0.01")
RATE7_Q = D("0.0000001")
Z6_Q = D("0.000001")
BP = D("0.0001")
HUNDRED = D(100)
T_ON = D(1) / D(365)
ZERO = D(0)
CURVE_CCYS = ("USD", "EUR", "GBP")
INTERP_TEXT = "linear in the zero rate between nodes; flat before the first node; never beyond the last node"
CC_TEXT = "continuously compounded, ACT/365 from the curve's date"
_EUR_GRID_T = {"3M": D("0.25"), "6M": D("0.5"), "9M": D("0.75"), "1Y": D(1), "2Y": D(2), "3Y": D(3), "5Y": D(5), "7Y": D(7), "10Y": D(10)}
#: the 2026-10-27 rows of the worked example (FX_CCS_PLAN 1.3; the Bank of England's spot short end at 6 dp, the ECB AAA spot rates at
#: 6 dp, the Treasury par row of 10-27 as treasury.gov published it) - ``fixture_curves`` installs them for every package's tests
FIXTURE_BOE_20261027 = (
    "3.899390", "3.955686", "4.009726", "4.066759", "4.126083", "4.185446", "4.242870", "4.297217", "4.347877", "4.394790",
    "4.438072", "4.477821", "4.514130", "4.547098", "4.576851", "4.603534", "4.627310", "4.648351", "4.666836", "4.682952",
    "4.696883", "4.708817", "4.718942", "4.727447", "4.734519", "4.740334", "4.745047", "4.748792", "4.751687", "4.753837",
    "4.755335", "4.756266", "4.756707", "4.756725", "4.756386", "4.755747", "4.754862", "4.753780", "4.752544", "4.751192",
    "4.749757", "4.748268", "4.746750", "4.745226", "4.743714", "4.742231", "4.740793", "4.739410", "4.738095", "4.736856",
    "4.735700", "4.734635", "4.733666", "4.732798", "4.732034", "4.731377", "4.730830", "4.730393", "4.730069", "4.729857")
FIXTURE_ECB_20261027 = {"3M": "2.542643", "6M": "2.693553", "9M": "2.801826", "1Y": "2.878760", "2Y": "3.015032", "3Y": "3.051054", "5Y": "3.113230"}
FIXTURE_UST_20261027 = {"1M": "4.10", "2M": "4.18", "3M": "4.22", "4M": "4.31", "6M": "4.34", "1Y": "4.50", "2Y": "4.70", "3Y": "4.80", "5Y": "4.82",
                        "7Y": "4.91", "10Y": "4.99", "20Y": "5.34", "30Y": "5.30"}
FIXTURE_RATES = {"IUDSOIA": "3.7328", "ESTR": "2.440", "SOFR": "3.95"}      # SONIA of the previous London day, ESTR and SOFR of d0
FIXTURE_SOURCE = "test fixture (the 2026-10-27 rows of FX_CCS_PLAN 1.3)"


def _ctx(c):
    c.prec = PREC
    c.rounding = ROUND_HALF_EVEN
    return c


def q10(x: Decimal) -> Decimal:
    """A discount factor as a quote or a record shows it: 1e-10, HALF_EVEN."""
    return D(x).quantize(DF_Q, rounding=ROUND_HALF_EVEN)


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _s(x: Optional[Decimal]) -> Optional[str]:
    return None if x is None else str(x)


def _pct6(x: Decimal) -> str:
    return str(D(x).quantize(Z6_Q, rounding=ROUND_HALF_EVEN))


def pip_of(pair: str) -> Decimal:
    return D("0.01") if "JPY" in str(pair or "").upper()[3:] else D("0.0001")


def price_q(pair: str) -> Decimal:
    """The pair's outright precision: 6 dp, 4 dp for a JPY-quoted pair."""
    return JPY_Q if str(pair or "").upper()[3:] == "JPY" else SPOT_Q


# ---- the curve ---------------------------------------------------------------------------------------------------------------------
class Curve:
    """One currency's zero curve (module docstring).  ``nodes`` [(tenor, t, z %)] with the first node first; ``kind`` observed or
    PROXY; ``level_bp`` (a discount curve's basis level) and ``shift_bp`` (a sensitivity bump) are added after interpolation."""

    def __init__(self, ccy: str, d0: date, kind: str, nodes: List[Tuple[str, Decimal, Decimal]], source: str, labels: List[str], first_node: dict,
                 grid: dict, spread_bp: Decimal = ZERO, spread: Optional[dict] = None, calendar: str = ""):
        self.ccy, self.d0, self.kind, self.source = ccy, d0, kind, source
        self.nodes = list(nodes)
        self.labels = list(labels)
        self.first_node = dict(first_node)
        self.grid = dict(grid)                   # {series, as_of, source, fetched_ts, raw: [(tenor, t, value %)]} - the stored values
        self.spread_bp = D(spread_bp)
        self.spread = dict(spread or {})         # the EUR OIS spread's schedule source and time
        self.level_bp = ZERO
        self.level = {}                          # the basis level's pair, source and time (discount curves)
        self.shift_bp = ZERO
        self.calendar = calendar

    def __repr__(self) -> str:
        return f"<Curve {self.ccy} {self.d0.isoformat()} {self.kind} level {self.level_bp} shift {self.shift_bp}>"

    @property
    def last_t(self) -> Decimal:
        return self.nodes[-1][1]

    def t_of(self, d) -> Decimal:
        with localcontext() as c:
            _ctx(c)
            return D((_d(d) - self.d0).days) / D(365)

    def z_at(self, t: Decimal) -> Decimal:
        """The interpolated zero rate in PERCENT at ``t`` (without level / shift); 409 CURVE_SHORT beyond the last node."""
        pts = self.nodes
        with localcontext() as c:
            _ctx(c)
            if t > pts[-1][1]:
                raise ConflictError("CURVE_SHORT", f"the {self.ccy} curve of {self.d0.isoformat()} ends at {pts[-1][0]} ({self._last_date_text()}): "
                                    "a date beyond its last node is never extrapolated", {"ccy": self.ccy, "last_tenor": pts[-1][0], "as_of": self.d0.isoformat()})
            if t <= pts[0][1]:
                return pts[0][2]
            if t >= pts[-1][1]:
                return pts[-1][2]
            for (_a, t0, v0), (_b, t1, v1) in zip(pts, pts[1:]):
                if t0 <= t <= t1:
                    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
            return pts[-1][2]

    def _last_date_text(self) -> str:
        days = int((self.last_t * 365).to_integral_value())
        return f"about {(self.d0 + timedelta(days=days)).isoformat()}"

    def bump(self) -> Decimal:
        with localcontext() as c:
            _ctx(c)
            return (self.shift_bp + self.level_bp) * BP

    def zr(self, t: Decimal) -> Decimal:
        """The zero rate as a FRACTION incl. the level and the shift."""
        with localcontext() as c:
            _ctx(c)
            return self.z_at(t) / HUNDRED + self.bump()

    def record(self, until=None) -> dict:
        """What a pricing record stores of this curve: kind, source, as-of, the first node, the nodes up to the first node beyond ``until``
        (every t and z as an exact string: enough to recompute every number), the EUR OIS spread and the basis level with their sources."""
        nodes = []
        tu = self.t_of(until) if until is not None else None
        for tenor, t, z in self.nodes:
            nodes.append({"tenor": tenor, "t": str(t), "z": str(z), "z_pct6": _pct6(z)})
            if tu is not None and t > tu:
                break
        g = {k: v for k, v in self.grid.items() if k != "raw"}
        if isinstance(g.get("dfs"), dict):                      # the USD bootstrap's DFs of the nodes carried (not the whole 30 years)
            kept = {n["t"] for n in nodes}
            g["dfs"] = {k: v for k, v in g["dfs"].items() if str(D(k)) in kept or any(D(k) == D(t) for t in kept)}
        return {"ccy": self.ccy, "kind": self.kind, "source": self.source, "as_of": self.d0.isoformat(), "series": self.grid.get("series"),
                "grid": g, "first_node": dict(self.first_node), "nodes": nodes, "nodes_total": len(self.nodes), "spread_bp": str(self.spread_bp),
                "spread": dict(self.spread), "level_bp": str(self.level_bp), "level": dict(self.level), "shift_bp": str(self.shift_bp),
                "compounding": CC_TEXT, "interpolation": INTERP_TEXT, "calendar": self.calendar, "labels": list(self.labels)}

    def copy(self) -> "Curve":
        return copy.copy(self)


def _first_node(services, ccy: str, on: date) -> dict:
    spec = tables.RFR[ccy]
    series, basis = spec["series"], D(spec["basis"])
    rates = getattr(services, "rates", None)
    obs = rates.observation(series, on) if rates is not None and hasattr(rates, "observation") else None
    if obs is None and rates is not None and hasattr(rates, "as_of_le"):
        got = rates.as_of_le(series, on)
        obs = None if got is None else {"series": series, "obs_date": got[0], "value": got[1], "source": rates.source(series) or ""}
    tried = spec["source"]
    if obs is None:
        raise ConflictError("RATE_UNAVAILABLE", f"no {spec['name']} print on or before {on.isoformat()} ({tried}): the {ccy} curve is not built without it",
                            {"ccy": ccy, "series": series, "sources": tried})
    age = (on - obs["obs_date"]).days
    if age > RFR_MAX_AGE_DAYS:
        raise ConflictError("RATE_UNAVAILABLE", f"{spec['name']} of {obs['obs_date'].isoformat()} is {age} days old on {on.isoformat()} (at most "
                            f"{RFR_MAX_AGE_DAYS}; {tried}): no older print is used", {"ccy": ccy, "series": series, "obs_date": obs["obs_date"].isoformat(),
                                                                                       "age_days": age, "max_age_days": RFR_MAX_AGE_DAYS})
    with localcontext() as c:
        _ctx(c)
        r = D(obs["value"])
        z = ((1 + r / HUNDRED / basis).ln() * 365) * HUNDRED
    return {"tenor": "ON", "series": series, "name": spec["name"], "obs_date": obs["obs_date"].isoformat(), "value_pct": str(r), "basis": int(basis),
            "z": str(z), "z_pct6": _pct6(z), "t": str(T_ON), "source": obs.get("source") or "", "age_days": age,
            "formula": f"z = 365 x ln(1 + {r} % / {int(basis)})"}


def age_bd(cal, as_of: date, on: date) -> int:
    """Business days of ``cal`` in [as_of, on): 0 for a grid of ``on`` itself, 3 for Monday's grid on Thursday."""
    n, d = 0, as_of
    while d < on:
        if cal.is_business_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def _fetch_note(services, series: str) -> str:
    rates = getattr(services, "rates", None)
    st = getattr(rates, "fetch_status", None)
    got = st.get(series) if isinstance(st, dict) else None
    if not got:
        return "no fetch recorded yet"
    return str(got.get("text") or got)


def _grid_curve(services, ccy: str, on: date, spread_bp: Decimal) -> Tuple[List[Tuple[str, Decimal, Decimal]], dict]:
    spec = tables.CURVES[ccy]
    series = spec["series"]
    cal = ccycal.CALENDARS[spec["calendar"]]
    rates = getattr(services, "rates", None)
    grid = rates.grid_on_or_before(series, on) if rates is not None and hasattr(rates, "grid_on_or_before") else None
    what = "Bank of England UK OIS curve" if ccy == "GBP" else "ECB euro-area AAA government curve"
    if grid is None:
        raise ConflictError("CURVE_UNAVAILABLE", f"no {what} on or before {on.isoformat()} (source tried: {spec['source']}; last fetch: "
                            f"{_fetch_note(services, series)}): the {ccy} curve is never replaced by a flat rate or another curve",
                            {"ccy": ccy, "series": series})
    age = age_bd(cal, grid.as_of, on)
    if age > CURVE_MAX_AGE_BD:
        raise ConflictError("CURVE_UNAVAILABLE", f"the {what} of {grid.as_of.isoformat()} is {age} {cal.short} business days old on {on.isoformat()} "
                            f"(at most {CURVE_MAX_AGE_BD}; last fetch: {_fetch_note(services, series)})",
                            {"ccy": ccy, "series": series, "as_of": grid.as_of.isoformat(), "age_bd": age, "max_age_bd": CURVE_MAX_AGE_BD})
    nodes, raw = [], []
    tenors = spec["grid"]
    with localcontext() as c:
        _ctx(c)
        for tenor in tenors:
            v = grid.points.get(tenor)
            if v is None:
                break
            t = (D(int(tenor[:-1])) / 12) if ccy == "GBP" else _EUR_GRID_T[tenor]
            raw.append((tenor, t, D(v)))
            z = D(v) + spread_bp * D("0.01") if ccy == "EUR" else D(v)
            nodes.append((tenor, t, z))
        later = [t for t in tenors[len(nodes):] if t in grid.points]
    if not nodes or later:
        gap = tenors[len(nodes)] if len(nodes) < len(tenors) else tenors[0]
        raise ConflictError("CURVE_UNAVAILABLE", f"the {what} of {grid.as_of.isoformat()} has no {gap} node: a missing node blocks (nothing is "
                            "interpolated over a hole or replaced by another rate)", {"ccy": ccy, "series": series, "missing": gap})
    return nodes, {"series": series, "as_of": grid.as_of.isoformat(), "source": grid.source or spec["source"], "fetched_ts": grid.fetched_ts,
                   "age_bd": age, "raw": raw, "points": len(nodes)}


def _usd_grid(services, on: date) -> Tuple[List[Tuple[str, Decimal, Decimal]], dict]:
    store = getattr(services, "curve", None)
    spec = tables.CURVES["USD"]
    dated = store.for_date(on, cal=ccycal.USGS, lookback_bd=CURVE_MAX_AGE_BD) if store is not None and hasattr(store, "for_date") else None
    dfs = dated.discount_factors() if dated is not None else None
    if not dfs:
        raise ConflictError("CURVE_UNAVAILABLE", f"no Treasury par curve within {CURVE_MAX_AGE_BD} US government-securities business days of "
                            f"{on.isoformat()} (source tried: {spec['source']}; last fetch: {_fetch_note(services, 'UST_PAR')})",
                            {"ccy": "USD", "series": "UST_PAR"})
    as_of = dated.as_of() or on
    nodes = []
    with localcontext() as c:
        _ctx(c)
        for t in sorted(dfs):
            df = D(dfs[t])
            nodes.append((f"{t.normalize():f}Y", D(t), (-(df.ln()) / D(t)) * HUNDRED))
    latest = dated.latest() if hasattr(dated, "latest") else None
    src = getattr(latest, "source", None) or spec["source"]
    fixed = getattr(dated, "fixed_as_of", lambda: None)()
    return nodes, {"series": "UST_PAR", "as_of": as_of.isoformat(), "source": src if fixed is None else "fixed discount factors (test FixedCurve)",
                   "fetched_ts": int(getattr(latest, "fetched_at", 0) or 0), "age_bd": age_bd(ccycal.USGS, as_of, on), "points": len(nodes),
                   "bootstrap": "semi-annual from the par yields (the IRS desk's CurveStore.discount_factors); z_i = -ln(DF_i) / t_i",
                   "dfs": {str(t): str(dfs[t]) for t in sorted(dfs)}}


def _schedule_terms(services, pb) -> dict:
    from . import prime
    return prime.xccy_terms(services, pb)


def _label(ccy: str, as_of: str, spread_bp: Decimal, sched: dict) -> str:
    """D2's label of a curve (``tables.CURVES[ccy]["label"]`` filled in)."""
    who = f"{sched['name']} schedule" if sched.get("name") else "no prime-broker schedule"
    return tables.CURVES[ccy]["label"].format(date=as_of, spread=spread_bp, schedule=who)


def curve(services, ccy: str, on, pb: Optional[str] = None) -> Curve:
    """The zero curve of USD, EUR or GBP for valuation date ``on`` (module docstring); ``pb`` the prime-broker account whose
    ``eur_ois_spread_bp`` the EUR grid carries (0.0 with no PB).  409 RATE_UNAVAILABLE / CURVE_UNAVAILABLE / CURVE_SHORT."""
    ccy = str(ccy or "").upper()
    on = _d(on)
    if ccy not in CURVE_CCYS:
        raise ConflictError("CURVE_UNAVAILABLE", f"no term curve for {ccy} in this terminal (USD, EUR and GBP only): {ccy} pairs keep the legacy "
                            "method (one overnight / policy rate, up to 1 year)", {"ccy": ccy})
    sched = _schedule_terms(services, pb)
    spread = D(sched["eur_ois_spread_bp"]) if ccy == "EUR" else ZERO
    first = _first_node(services, ccy, on)
    if ccy == "USD":
        grid_nodes, grid = _usd_grid(services, on)
    else:
        grid_nodes, grid = _grid_curve(services, ccy, on, spread)
    spec = tables.CURVES[ccy]
    nodes = [("ON", T_ON, D(first["z"]))] + grid_nodes
    d0 = _d(grid["as_of"])
    spread_info = ({"field": "eur_ois_spread_bp", "bp": str(spread), "source": sched["source"], "updated_ts": sched.get("updated_ts"),
                    "applies_to": "every ECB AAA grid node (not the ESTR node), projection and discounting"} if ccy == "EUR" else {})
    labels = [_label(ccy, grid["as_of"], spread, sched)]
    if ccy == "EUR":
        labels.append(f"ASSUMED: EUR OIS spread {spread} bp over the ECB AAA curve ({sched['source']})")
    return Curve(ccy, d0, spec["kind"], nodes, spec["source"], labels, first, grid, spread, spread_info, spec["calendar"])


def discount(crv: Curve, level_bp, pair: Optional[str] = None, source: Optional[str] = None, updated_ts=None) -> Curve:
    """The discount curve under the USD CSA: ``crv`` with the cross-currency basis level ``level_bp`` on every node (added after the
    interpolation: ``zr = z / 100 + (shift + level) x 0.0001``).  USD carries none."""
    out = crv.copy()
    out.labels = list(crv.labels)
    out.level_bp = D(str(level_bp))
    out.level = {"pair": pair or (f"{crv.ccy}USD" if crv.ccy != "USD" else None), "bp": str(out.level_bp), "source": source, "updated_ts": updated_ts,
                 "on": "the discount curve only (never projection)"}
    if crv.ccy != "USD":
        out.labels.append(f"ASSUMED: {crv.ccy}/USD cross-currency basis level {out.level_bp} bp on the {crv.ccy} discount curve"
                          + (f" ({source})" if source else ""))
    return out


def shifted(crv: Curve, bp) -> Curve:
    """A parallel bump of every node (the first node included) by ``bp`` basis points: a rate01 re-pricing."""
    out = crv.copy()
    out.shift_bp = crv.shift_bp + D(str(bp))
    return out


def with_spread(crv: Curve, spread_bp) -> Curve:
    """The EUR curve rebuilt with another EUR OIS spread (every grid node = stored value + spread; the ESTR node unchanged)."""
    if crv.ccy != "EUR":
        raise RejectError("CURVE", "only the EUR curve carries the EUR OIS spread")
    out = crv.copy()
    sp = D(str(spread_bp))
    with localcontext() as c:
        _ctx(c)
        out.nodes = [crv.nodes[0]] + [(tenor, t, v + sp * D("0.01")) for tenor, t, v in crv.grid["raw"]]
    out.spread_bp = sp
    out.spread = dict(crv.spread, bp=str(sp), note="re-priced with this EUR OIS spread")
    return out


def df(crv: Curve, a, b, quantize: bool = False) -> Decimal:
    """DF(a, b) = exp(-(zr(t_b) x t_b) + zr(t_a) x t_a) on ``crv`` (unquantised; ``quantize`` -> 1e-10 HALF_EVEN)."""
    ta, tb = crv.t_of(a), crv.t_of(b)
    with localcontext() as c:
        _ctx(c)
        za, zb = crv.zr(ta), crv.zr(tb)
        x = (-(zb * tb) + za * ta).exp()
    return q10(x) if quantize else x


def level_of(sched: dict, ccy: str) -> Decimal:
    """The schedule's basis level for ``ccy`` (EUR / GBP; USD and others 0)."""
    return D(sched["xccy_basis_eur_bp"]) if ccy == "EUR" else (D(sched["xccy_basis_gbp_bp"]) if ccy == "GBP" else ZERO)


def discount_curve(services, ccy: str, on, pb: Optional[str] = None, sched: Optional[dict] = None) -> Curve:
    """``curve`` plus the PB's basis level for ``ccy`` (D5): the discount curve under the USD CSA."""
    sched = sched or _schedule_terms(services, pb)
    crv = curve(services, ccy, on, pb)
    if ccy.upper() == "USD":
        return crv
    return discount(crv, level_of(sched, ccy.upper()), f"{ccy.upper()}USD", sched["source"], sched.get("updated_ts"))


# ---- the spot (D3, G20) ------------------------------------------------------------------------------------------------------------
def _now(services, snapshot, now) -> int:
    if now is not None:
        return int(now)
    clock = getattr(services, "clock", None)
    if clock is not None and hasattr(clock, "epoch"):
        return int(clock.epoch())
    return int(snapshot.as_of()) if snapshot is not None and hasattr(snapshot, "as_of") else 0


_LEG_CODES = {"STALE": "QUOTE_STALE", "QUOTE_FROM_PREVIOUS_SESSION": "QUOTE_STALE", "PROVIDER_SESSION_ENDED": "QUOTE_STALE", "NO_QUOTE": "NO_QUOTE",
              "SESSION_CLOSED": "SESSION_CLOSED", "COMPUTED_NOT_EXECUTABLE": "QUOTE_STALE", "NO_NBBO": "QUOTE_STALE"}


def spot(services, pair: str, snapshot, now=None) -> dict:
    """The spot of ``pair`` for the PB forward (module docstring): ``{pair, mid, basis, legs, legs_gap_s, print, diff_pips, tolerance_pips,
    executable, reason, code, quote_time}``.  ``mid`` a Decimal (None when no price at all); ``code`` None when executable, else
    NO_QUOTE / QUOTE_STALE / SESSION_CLOSED / SPOT_INCONSISTENT.  Never raises for a market reason (``require_spot`` does)."""
    from . import fx
    from .orders_preview import quote_of
    pair = str(pair or "").upper()
    base, quote = pair[:3], pair[3:]
    now = _now(services, snapshot, now)
    try:
        session = services.sessions.state(fx.SESSION_GROUP, now)
    except Exception:   # noqa: BLE001 - a stand-in without sessions: no session, nothing executable
        session = None

    def leg(ccy):
        got = fx.usd_leg(services, snapshot, ccy, session, now)
        return {"pair": got.get("pair"), "mid": got.get("quoted"), "usd_per": got.get("rate"), "quote_time": got.get("quote_time"),
                "source": got.get("source"), "quality": got.get("quality"), "executable": bool(got.get("executable")), "reason": got.get("reason")}
    out = {"pair": pair, "mid": None, "basis": None, "legs": [], "legs_gap_s": None, "print": None, "diff_pips": None, "tolerance_pips": str(CROSS_TOL_PIPS),
           "executable": False, "reason": "", "code": "NO_QUOTE", "quote_time": None, "leg_time_tol_s": LEG_TIME_TOL_S}
    if "USD" in (base, quote):
        lg = leg(quote if base == "USD" else base)
        out.update(legs=[lg], basis="pair print", legs_gap_s=0, quote_time=lg["quote_time"])
        if lg["mid"] is None:
            out["reason"] = f"no {pair} print"
            return out
        out["mid"] = D(lg["mid"])
        if not lg["executable"]:
            out.update(code=_LEG_CODES.get(lg["reason"], "QUOTE_STALE"), reason=f"the {pair} print is not executable ({lg['reason']})")
            return out
        out.update(executable=True, code=None, reason="OK")
        return out
    lb, lq = leg(base), leg(quote)
    out.update(legs=[lb, lq], basis="derived from the USD pairs")
    if lb["usd_per"] is None or lq["usd_per"] is None:
        out["reason"] = f"no USD rate for {base if lb['usd_per'] is None else quote}: the {pair} cross is derived from the two USD pairs"
        return out
    with localcontext() as c:
        _ctx(c)
        bu = D(lb["mid"]) if lb["pair"] == f"{base}USD" else D(1) / D(lb["mid"])
        qu = D(lq["mid"]) if lq["pair"] == f"{quote}USD" else D(1) / D(lq["mid"])
        exact = bu / qu
    mid = exact.quantize(SPOT_Q, rounding=ROUND_HALF_EVEN)
    qts = [t for t in (lb["quote_time"], lq["quote_time"]) if t is not None]
    gap = abs(int(lb["quote_time"]) - int(lq["quote_time"])) if len(qts) == 2 else None
    out.update(mid=mid, exact=str(exact), quote_time=min(qts) if qts else None, legs_gap_s=gap,
               arithmetic=f"{lb['pair']} {lb['mid']} / {lq['pair']} {lq['mid']} = {exact} -> {mid}")
    # the cross's own print: stored with its difference; compared only when it is itself executable (a stale print is no evidence)
    view = snapshot.mark(pair) if snapshot is not None and hasattr(snapshot, "mark") else None
    if view is not None and view.mark is not None and view.quality != "unavailable":
        rec = quote_of(services, pair, snapshot)
        ok, why = _print_executable(rec, view, session, now)
        pm = D(view.mark)
        with localcontext() as c:
            _ctx(c)
            diff = (pm - exact) / pip_of(pair)
        out["print"] = {"mid": pm, "quote_time": view.quote_time, "source": view.source, "quality": view.quality, "executable": ok, "compared": ok,
                        "note": "" if ok else f"the {pair} print is not executable ({why}): stored, not compared"}
        out["diff_pips"] = diff.quantize(POINTS_Q, rounding=ROUND_HALF_EVEN)
        out["diff_pips_exact"] = str(diff)
    if not lb["executable"] or not lq["executable"]:
        bad = lb if not lb["executable"] else lq
        out.update(code=_LEG_CODES.get(bad["reason"], "QUOTE_STALE"), reason=f"the {bad['pair']} print is not executable ({bad['reason']})")
        return out
    if gap is None or gap > LEG_TIME_TOL_S:
        out.update(code="QUOTE_STALE", reason=(f"the {lb['pair']} and {lq['pair']} prints are {gap} s apart (at most {LEG_TIME_TOL_S} s): no "
                                               f"consistent {pair} cross" if gap is not None else "a USD leg has no quote time"))
        return out
    p = out.get("print")
    if p is not None and p["compared"] and abs(D(out["diff_pips_exact"])) > CROSS_TOL_PIPS:
        out.update(code="SPOT_INCONSISTENT", reason=(f"{pair}: the cross print {p['mid']} differs from the USD pairs' cross {mid} by "
                                                     f"{out['diff_pips']} pips (limit {CROSS_TOL_PIPS}); no quote"))
        return out
    out.update(executable=True, code=None, reason="OK")
    return out


def _print_executable(rec, view, session, now: int):
    """The executability of the cross's own print (the FX desk's rule, with only public helpers): the quote record's session /
    staleness rule when there is a record, else the mark's quality and the session."""
    from .quotes import EXECUTABLE_QUALITIES, is_executable
    if rec is not None:
        return is_executable(rec, session, now)
    if session is None or not getattr(session, "open", False):
        return False, "SESSION_CLOSED"
    if view.quality not in EXECUTABLE_QUALITIES or not view.executable:
        return False, ("STALE" if view.quality == "stale" else "COMPUTED_NOT_EXECUTABLE")
    return True, "OK"


def require_spot(services, pair: str, snapshot, now=None) -> dict:
    """``spot`` or 409 with its code (NO_QUOTE, QUOTE_STALE, SESSION_CLOSED, SPOT_INCONSISTENT) and the reason."""
    sp = spot(services, pair, snapshot, now)
    if not sp["executable"]:
        raise ConflictError(sp["code"] or "NO_QUOTE", sp["reason"] or f"no executable {pair} spot", {"spot": spot_view(sp)})
    return sp


def spot_view(sp: dict) -> dict:
    """``spot`` as JSON-safe values (Decimals as strings)."""
    def j(x):
        if isinstance(x, Decimal):
            return str(x)
        if isinstance(x, dict):
            return {k: j(v) for k, v in x.items()}
        if isinstance(x, list):
            return [j(v) for v in x]
        return x
    return j(sp)


# ---- the forward (D3, 3.2) ---------------------------------------------------------------------------------------------------------
def _fx_trade_date(services, now: int) -> date:
    from . import fx
    return fx.fx_trade_date(services, now)


def _on_date(now: int) -> date:
    from .clock import ny_of
    return ny_of(int(now)).date()


def forward(services, pair: str, value_date, snapshot=None, pb: Optional[str] = None, *, now=None, on=None, trade_date=None, spot_rec=None,
            curves: Optional[Dict[str, Curve]] = None, exact: bool = False) -> dict:
    """The CIP outright of ``pair`` for ``value_date`` on the discount curves (module docstring) with its record: ``{header..., spot,
    curves, dfs, forward, basis, usd_rates, labels, F, S, df_base, df_quote}``.  ``spot_rec`` (a ``spot`` dict or a Decimal mid) replaces
    the snapshot's spot (a VM at the 17:00 closes); ``curves`` {ccy: discount Curve} replaces the stored curves (a re-pricing);
    ``exact`` skips the 1e-10 / 6 dp quantisations (sensitivities).  The spot date is that of ``trade_date`` (default the FX trade
    date of ``now``); ``on`` (default the New York date of ``now``) is the valuation date the curves and prints are aged against.
    400 VALUE_DATE for a value date that is not a business day of the pair's value calendar or not after the spot date; 409
    RATE_UNAVAILABLE / CURVE_UNAVAILABLE / CURVE_SHORT / NO_QUOTE (no spot price at all).  The spot need NOT be executable here (a
    preview or a mark is still computed and shows ``spot.executable`` / ``spot.reason``): a dealer quote checks it or uses
    ``require_spot``."""
    pair = str(pair or "").upper()
    base, quote = pair[:3], pair[3:]
    if not ccycal.has_calendars(pair):
        missing = [c for c in (base, quote) if c not in CURVE_CCYS]
        raise ConflictError("CURVE_UNAVAILABLE", f"{pair}: no term curve for {', '.join(missing)} in this terminal - the legacy method applies "
                            "(LEGACY METHOD - LIMITED: one overnight / policy rate per currency, no term structure, no basis)", {"pair": pair})
    now = _now(services, snapshot, now)
    td = _d(trade_date) if trade_date is not None else _fx_trade_date(services, now)
    on = _d(on) if on is not None else _on_date(now)
    sd = ccycal.spot_date(services, pair, td)
    vd = ccycal.require_value_date(services, pair, value_date)
    if vd <= sd:
        raise RejectError("VALUE_DATE", f"{pair} value date {vd.isoformat()} is not after the spot date {sd.isoformat()}: a forward settles after spot",
                          {"field": "value_date", "spot_date": sd.isoformat()})
    sched = _schedule_terms(services, pb)
    if spot_rec is None:
        sp = spot(services, pair, snapshot, now)
    elif isinstance(spot_rec, dict):
        sp = spot_rec
    else:
        sp = {"pair": pair, "mid": D(str(spot_rec)), "basis": "given", "legs": [], "executable": True, "code": None, "reason": "given"}
    if sp.get("mid") is None:
        raise ConflictError(sp.get("code") or "NO_QUOTE", sp.get("reason") or f"no {pair} spot", {"spot": spot_view(sp)})
    S = D(sp["mid"])
    cb = (curves or {}).get(base) or discount_curve(services, base, on, pb, sched)
    cq = (curves or {}).get(quote) or discount_curve(services, quote, on, pb, sched)
    dfb, dfq = df(cb, sd, vd, quantize=not exact), df(cq, sd, vd, quantize=not exact)
    pip = pip_of(pair)
    with localcontext() as c:
        _ctx(c)
        raw = S * dfb / dfq
        F = raw if exact else raw.quantize(price_q(pair), rounding=ROUND_HALF_EVEN)
        points = (F - S) / pip
    days = (vd - sd).days

    def crv_block(cv: Curve) -> dict:
        tS, tV = cv.t_of(sd), cv.t_of(vd)
        blk = cv.record(vd)
        blk.update(t_S=str(tS), t_V=str(tV), z_S=_pct6(cv.z_at(tS)), z_V=_pct6(cv.z_at(tV)), z_S_exact=str(cv.z_at(tS)), z_V_exact=str(cv.z_at(tV)))
        return blk
    lv_b, lv_q = cb.level_bp, cq.level_bp
    labels = [MODEL_TEXT] + list(dict.fromkeys(cb.labels + cq.labels))
    labels.append("ASSUMED: the basis levels are " + (f"{sched['name']} schedule terms" if sched.get("name") else "0.0 bp (no prime-broker schedule)"))
    rec = {"method": method_text(pair), "model_version": MODEL_VERSION, "valuation_ts": now, "on": on.isoformat(), "pair": pair, "base": base,
           "quote": quote, "trade_date": td.isoformat(), "spot_date": sd.isoformat(), "value_date": vd.isoformat(), "days": days,
           "spot": spot_view(sp),
           "curves": {base: crv_block(cb), quote: crv_block(cq)},
           "dfs": {"base": str(dfb), "quote": str(dfq), "quantised": not exact,
                   "arithmetic": f"DF*_{base}({sd.isoformat()}, {vd.isoformat()}) = {dfb}; DF*_{quote}({sd.isoformat()}, {vd.isoformat()}) = {dfq}"},
           "forward": {"outright": str(F), "points": str(points if exact else points.quantize(POINTS_Q, rounding=ROUND_HALF_EVEN)), "pip": str(pip),
                       "arithmetic": f"F = {S} x {dfb} / {dfq} = {F}"},
           "basis": basis_block(pair, sched, lv_b if base != "USD" else None, lv_q if quote != "USD" else None),
           "usd_rates": [{"pair": lg.get("pair"), "mid": _s(lg.get("mid")) if isinstance(lg.get("mid"), Decimal) else lg.get("mid"),
                          "quote_time": lg.get("quote_time"), "source": lg.get("source"), "quality": lg.get("quality")} for lg in sp.get("legs") or []],
           "labels": labels, "value_calendar": ccycal.describe(services, pair),
           "F": F, "S": S, "df_base": dfb, "df_quote": dfq}
    return rec


def method_text(pair: str) -> str:
    """The record header's method (3.2): the curves named observed first, then the proxies (GBP, EUR, USD order)."""
    ccys = {pair[:3], pair[3:]}
    kinds = (("GBP", "GBP observed (Bank of England OIS)"), ("EUR", "EUR PROXY (ECB AAA + spread)"), ("USD", "USD PROXY (Treasury par curve)"))
    return ("covered interest parity on the discount curves under the USD CSA: " + ", ".join(t for c, t in kinds if c in ccys) + "; MODEL")


def basis_block(pair: str, sched: dict, level_base: Optional[Decimal], level_quote: Optional[Decimal]) -> dict:
    """The record's basis block (FX-C-11): both USD levels, the cross level (base minus quote), where they sit and their source."""
    base, quote = pair[:3], pair[3:]
    out = {"pair": pair, "eur_usd_level_bp": str(sched["xccy_basis_eur_bp"]), "gbp_usd_level_bp": str(sched["xccy_basis_gbp_bp"]),
           "on": "both discount curves" if "USD" not in (base, quote) else f"the {base if quote == 'USD' else quote} discount curve",
           "source": (f"{sched['name']} schedule ({'seed / as edited'}), ASSUMED: no free cross-currency basis source" if sched.get("name")
                      else "no prime-broker schedule: 0.0 bp, ASSUMED"),
           "schedule_source": sched["source"], "updated_ts": sched.get("updated_ts")}
    lb = level_base if level_base is not None else ZERO
    lq = level_quote if level_quote is not None else ZERO
    out["cross_bp"] = str(lb - lq)
    out["base_level_bp"], out["quote_level_bp"] = str(lb), str(lq)
    return out


def cross_checks(services, pair: str, value_date, snapshot=None, fwd: Optional[dict] = None, pb: Optional[str] = None, now=None) -> dict:
    """Shown and stored, never priced: the flat-overnight outright (revision 1's method: each currency's overnight print compounded
    daily over the days from spot, DFs to 1e-10), the outright on the cross print instead of the derived cross, and the FX desk's
    outright for the same value date when the FX desk quotes it (up to 1 year), each with its difference to F in pips."""
    pair = str(pair or "").upper()
    base, quote = pair[:3], pair[3:]
    fwd = fwd or forward(services, pair, value_date, snapshot, pb, now=now)
    F, S = D(fwd["F"]), D(fwd["S"])
    pip = pip_of(pair)
    days = int(fwd["days"])
    out = {"note": "cross-checks are shown and stored, never priced"}
    with localcontext() as c:
        _ctx(c)
        fn = {}
        for ccy in (base, quote):
            fnode = fwd["curves"][ccy]["first_node"]
            r = D(fnode["value_pct"]) / HUNDRED
            B = D(fnode["basis"])
            fn[ccy] = (q10((1 + r / B) ** (-days)), fnode)
        Ff = (S * fn[base][0] / fn[quote][0]).quantize(price_q(pair), rounding=ROUND_HALF_EVEN)
        out["flat_overnight"] = {"outright": str(Ff), "diff_pips": str(((Ff - F) / pip).quantize(POINTS_Q)),
                                 "method": "each currency's overnight print held flat, compounded daily over the days from spot (revision 1's method)",
                                 "dfs": {base: str(fn[base][0]), quote: str(fn[quote][0])},
                                 "inputs": {ccy: f"{fn[ccy][1]['name']} {fn[ccy][1]['value_pct']} % ({fn[ccy][1]['obs_date']}), ACT/{fn[ccy][1]['basis']}"
                                            for ccy in (base, quote)}}
        p = (fwd.get("spot") or {}).get("print")
        if p and p.get("mid") is not None:
            pm = D(str(p["mid"]))
            Fp = (pm * D(fwd["df_base"]) / D(fwd["df_quote"])).quantize(price_q(pair), rounding=ROUND_HALF_EVEN)
            out["on_print"] = {"outright": str(Fp), "diff_pips": str(((Fp - F) / pip).quantize(POINTS_Q)), "print": str(pm),
                               "method": "the same discount factors on the cross print instead of the derived cross"}
        else:
            out["on_print"] = {"outright": None, "note": f"no {pair} print"}
    out["fx_desk"] = _fx_desk_check(services, pair, _d(fwd["value_date"]), _d(fwd["trade_date"]), snapshot, F, pip)
    return out


def _fx_desk_check(services, pair: str, vd: date, td: date, snapshot, F: Decimal, pip: Decimal) -> dict:
    try:
        last = ccycal.tenor_date(services, pair, "1Y", td)
    except Exception:   # noqa: BLE001
        last = None
    if last is not None and vd > last:
        return {"outright": None, "note": "none beyond 1Y: the FX desk quotes forwards up to 1 year"}
    try:
        from . import fx
        got = fx.forward_rate(services, pair, vd, snapshot)
        fdesk, info = (got[0], got[1]) if isinstance(got, tuple) else (got, {})
    except Exception as e:   # noqa: BLE001 - a cross-check never blocks the quote
        return {"outright": None, "note": f"FX desk outright unavailable: {getattr(e, 'message', None) or e}"}
    if fdesk is None:
        return {"outright": None, "note": "FX desk outright unavailable" + (f": {info.get('note')}" if isinstance(info, dict) and info.get("note") else "")}
    fdesk = D(str(fdesk))
    return {"outright": str(fdesk), "diff_pips": str(((F - fdesk) / pip).quantize(D("0.1"))),
            "method": (info.get("method") if isinstance(info, dict) and info.get("method") else
                       "the FX desk's simple-interest CIP on one overnight / policy rate per currency (moves to these curves in 1.0.6.2)")}


# ---- indices (D4) ------------------------------------------------------------------------------------------------------------------
def index_detail(services, ccy: str, s, e) -> dict:
    """``{factor, method, start {date, value, source}, end {...}, nights[]}``: the compounding factor of [s, e) from the published index,
    or (EUR) the daily ESTR prints over the TARGET2 days; 409 INDEX_PENDING otherwise (never estimated)."""
    ccy = str(ccy or "").upper()
    s, e = _d(s), _d(e)
    if ccy not in tables.RFR:
        raise ConflictError("INDEX_PENDING", f"no compounded index for {ccy} in this terminal", {"ccy": ccy})
    if e <= s:
        raise RejectError("INDEX_PERIOD", f"the period end {e.isoformat()} is not after its start {s.isoformat()}")
    spec = tables.RFR[ccy]
    rates = services.rates
    a = rates.observation(spec["index"], s, exact=True)
    b = rates.observation(spec["index"], e, exact=True)
    if a is not None and b is not None:
        with localcontext() as c:
            _ctx(c)
            f = D(b["value"]) / D(a["value"])
        return {"factor": f, "method": f"the {spec['name']} compounded index: I({e.isoformat()}) / I({s.isoformat()})",
                "start": {"date": s.isoformat(), "value": str(a["value"]), "source": a["source"]},
                "end": {"date": e.isoformat(), "value": str(b["value"]), "source": b["source"]}, "ccy": ccy, "series": spec["index"]}
    missing = [d.isoformat() for d, o in ((s, a), (e, b)) if o is None]
    if ccy == "EUR":
        cal = ccycal.TARGET
        f, nights, gaps = D(1), [], []
        with localcontext() as c:
            _ctx(c)
            d = s if cal.is_business_day(s) else cal.next_business_day(s)
            while d < e:
                nxt = cal.next_business_day(d)
                n = (min(nxt, e) - d).days
                o = rates.observation(spec["series"], d, exact=True)
                if o is None:
                    gaps.append(d.isoformat())
                else:
                    f = f * (1 + D(o["value"]) / HUNDRED * n / D(360))
                    nights.append({"date": d.isoformat(), "rate_pct": str(o["value"]), "days": n, "source": o["source"]})
                d = nxt
        if not gaps:
            return {"factor": f, "method": (f"ESTR compounded over the TARGET2 days of [{s.isoformat()}, {e.isoformat()}) with n_i / 360 weights "
                                            f"(the ECB index of {', '.join(missing)} is not stored)"), "nights": nights, "ccy": ccy, "series": spec["series"],
                    "start": {"date": s.isoformat()}, "end": {"date": e.isoformat()}}
        raise ConflictError("INDEX_PENDING", f"EUR {s.isoformat()}..{e.isoformat()}: the ECB compounded ESTR index of {', '.join(missing)} is not "
                            f"published yet and ESTR is missing for {', '.join(gaps[:5])}{'...' if len(gaps) > 5 else ''}: the payment waits",
                            {"ccy": ccy, "missing_index": missing, "missing_prints": gaps})
    raise ConflictError("INDEX_PENDING", f"{ccy} {s.isoformat()}..{e.isoformat()}: the {spec['name']} index of {', '.join(missing)} is not published "
                        "yet: the payment waits (never estimated)", {"ccy": ccy, "missing_index": missing})


def index_factor(services, ccy: str, s, e) -> Decimal:
    """I(e) / I(s) (``index_detail``'s ``factor``)."""
    return index_detail(services, ccy, s, e)["factor"]


def compounded_rate(factor, days: int, basis: int = 360) -> Decimal:
    """((factor - 1) x basis / days), quantised to 1e-7 of a fraction (0.00001 %), HALF_EVEN."""
    if int(days) <= 0:
        raise RejectError("INDEX_PERIOD", "a compounding period needs at least one day")
    with localcontext() as c:
        _ctx(c)
        r = (D(factor) - 1) * D(int(basis)) / D(int(days))
    return r.quantize(RATE7_Q, rounding=ROUND_HALF_EVEN)


# ---- the test fixture ----------------------------------------------------------------------------------------------------------
def fixture_curves(services, d0=date(2026, 10, 27), persist: bool = True) -> dict:
    """Install the worked example's rows (FX_CCS_PLAN 3.1) on ``services.rates`` / ``services.curve``: the Bank of England 1M..60M row,
    the seven ECB AAA spot rates and the Treasury par row, all dated ``d0``; SONIA 3.7328 % of the previous London business day, ESTR
    2.440 % and SOFR 3.95 % of ``d0``.  A FixedCurve on ``services.curve`` is cleared (the USD curve is the example's par row).
    ``persist`` also writes ``curve_points`` through the services' bus (when the stores have one).  Returns what was installed."""
    from .quotes.parsers import ParCurve, TENOR_YEARS
    d0 = _d(d0)
    rates, store = services.rates, services.curve
    sonia_d = ccycal.GBLO.prev_business_day(d0)
    rates.put("IUDSOIA", [(sonia_d, D(FIXTURE_RATES["IUDSOIA"]))], f"Bank of England database (IUDSOIA) - {FIXTURE_SOURCE}", persist=persist)
    rates.put("ESTR", [(d0, D(FIXTURE_RATES["ESTR"]))], f"ECB data portal (ESTR) - {FIXTURE_SOURCE}", persist=persist)
    rates.put("SOFR", [(d0, D(FIXTURE_RATES["SOFR"]))], f"NY Fed (SOFR) - {FIXTURE_SOURCE}", persist=persist)
    rates.put_grid("BOE_OIS_SPOT", d0, {f"{m}M": v for m, v in enumerate(FIXTURE_BOE_20261027, start=1)},
                   f"{tables.CURVES['GBP']['source']} - {FIXTURE_SOURCE}", 0, persist=persist)
    rates.put_grid("ECB_AAA_SPOT", d0, dict(FIXTURE_ECB_20261027), f"{tables.CURVES['EUR']['source']} - {FIXTURE_SOURCE}", 0, persist=persist)
    if hasattr(store, "clear_fixed_dfs"):
        store.clear_fixed_dfs()
    store.put(ParCurve(d0, {TENOR_YEARS[k]: D(v) for k, v in FIXTURE_UST_20261027.items()}, source=f"treasury.gov daily par yield curve - {FIXTURE_SOURCE}"),
              persist=persist)
    return {"d0": d0.isoformat(), "sonia_date": sonia_d.isoformat(), "rates": dict(FIXTURE_RATES), "boe_points": len(FIXTURE_BOE_20261027),
            "ecb_points": len(FIXTURE_ECB_20261027), "ust_points": len(FIXTURE_UST_20261027)}


__all__ = ["Curve", "curve", "discount", "discount_curve", "shifted", "with_spread", "df", "q10", "spot", "require_spot", "spot_view", "forward", "cross_checks",
           "index_detail", "index_factor", "compounded_rate", "fixture_curves", "age_bd", "basis_block", "method_text", "level_of", "pip_of", "price_q",
           "RFR_MAX_AGE_DAYS", "CURVE_MAX_AGE_BD", "CROSS_TOL_PIPS", "LEG_TIME_TOL_S", "MODEL_TEXT", "PROXY_TEXT", "OBSERVED_TEXT", "ASSUMED_TEXT",
           "MODEL_VERSION", "T_ON", "CURVE_CCYS", "FIXTURE_BOE_20261027", "FIXTURE_ECB_20261027", "FIXTURE_UST_20261027", "FIXTURE_RATES"]
