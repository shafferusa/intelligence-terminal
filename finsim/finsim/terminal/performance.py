"""Portfolio performance statistics per lifetime (TERMINAL_DESIGN.md §6.9, §19.20; WP-20).

``statistics()`` is pure: it takes the lifetime's daily ``nav_snapshots`` rows (as ``Snap``), the benchmark's observed
daily closes by date and SOFR, and returns a ``PerfView``.  ``perf_view(services, scope)`` is the thin loader the route
``GET /api/portfolio/performance`` (WP-11) calls: on one pooled reader it reads the ``{lifetime}:ALL`` snapshots, the
SPY ``CLOSE`` rows of ``marks`` and SOFR as a fraction (``sofr_input``: ``services.rates.sofr_fraction()``), then calls
``statistics()``.  Nothing here writes, nothing here fetches, and the page (``static/components/performance.js``) only
renders what ``PerfView.to_dict()`` says.

Conventions (the sentence PERF states them on the tab)
- Snapshots are the lifetime's ``{lifetime}:ALL`` rows in date order.  Fewer than two -> ``status "insufficient
  history"`` with ``needed 2``; nothing is computed.  Statistics are per lifetime: ``ALL_BOOKS`` is refused.
- Daily return  r_t = (nav_t - flows_day_t) / nav_{t-1} - 1  over consecutive snapshot rows (t = 1 .. n-1).  Flows are
  the day's capital deposits minus withdrawals (``flows_day_c``), so a deposit is not a gain and a withdrawal is not a
  loss.  Consecutive rows are one "day" whatever the calendar gap between them (snapshots are written every calendar
  day at 17:45; a gap is downtime, which the data window discloses).  A pair whose previous NAV is not positive has no
  return: it is skipped and counted in ``returns_skipped``.
- Benchmark  m_t = spy_t / spy_{t-1} - 1  on the same two dates, from SPY ``CLOSE`` marks (observed, never modelled).
  A pair missing a close on either date is skipped for beta / alpha, counted in ``pairs_skipped`` and listed in
  ``skipped_dates``; the return statistics still use every return.
- rf = SOFR / 360 per day (SOFR is ACT/360, sentence RATE_LAG).  ``sofr`` is a decimal fraction: ``Decimal("0.0387")``
  is 3.87 %, the unit the core's rate tables use.  The rate store keeps SOFR as the PERCENT FRED publishes (3.87;
  ``quotes.rates`` module docstring UNITS); ``sofr_input`` reads ``RateStore.sofr_fraction()``, which divides by 100
  exactly once, so SOFR 3.87 gives sofr 0.0387 and rf = 0.0387 / 360 = 0.0001075 per day.  A percent reaching
  ``statistics()`` is refused (ValueError), never read as a 387 % rate.  With no SOFR, Sharpe, Sortino and alpha are
  ``None`` ("n/a") and a note says so; nothing is assumed.
- ann_ret = mean(r) x 252;  vol = stdev(r, ddof=1) x sqrt(252) (needs >= 2 returns);
  sharpe = (mean(r) - rf) / stdev(r) x sqrt(252);
  sortino = (mean(r) - rf) / sqrt(mean(min(r - rf, 0)^2)) x sqrt(252), the downside deviation over ALL days (no day
  below rf -> ``None``);
  beta = cov(r, m, ddof=1) / var(m, ddof=1) and alpha = (mean(r) - rf - beta x (mean(m) - rf)) x 252, both over the
  paired days only (>= 2 pairs with var(m) > 0, else ``None``);
  max_dd = max_t (peak_t - idx_t) / peak_t on the flow-adjusted index idx_0 = 1, idx_t = idx_{t-1} x (1 + r_t);
  hit_rate = count(r > 0) / count(r).
- Monthly: per calendar month (nav_end - nav_start - sum of flows) / nav_start, where nav_start is the last snapshot
  of the previous month (inception: the first snapshot, whose own ``flows_day`` IS the starting NAV and is therefore
  not a flow of the month) and the flows are the ``flows_day`` of the month's snapshots dated after nav_start.
- Ratios are floats (they are not money): 4 dp in JSON, daily and monthly returns 6 dp; NAVs and flows are integer
  cents (``*_c``) as in every core view -- the API layer renders cents as decimal strings.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from finsim.quant.stats import covariance, downside_deviation, mean, sharpe_ratio, sortino_ratio, stdev, variance

from .accounting import ScopeError, restated_label, snapshot_chain
from .tables import CONVENTIONS

__all__ = ["BENCHMARK", "NEEDED", "MonthRow", "PerfView", "Snap", "STATUS_INSUFFICIENT", "STATUS_OK", "monthly_table",
           "perf_view", "statistics"]

TRADING_DAYS = 252                  # annualisation: mean x 252, stdev x sqrt(252)
SQRT_TRADING_DAYS = math.sqrt(252.0)
RATE_DAYS = 360                     # SOFR is ACT/360: rf = SOFR / 360 per day
NEEDED = 2                          # snapshots needed before anything is computed
BENCHMARK = "SPY"                   # CLOSE marks of this instrument are the regression benchmark
STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient history"
RATIO_DP = 4
RETURN_DP = 6
_LIFETIME_RE = re.compile(r"^[A-Z][0-9]+$")
_MAX_SOFR = Decimal("1")            # a SOFR at or above 100 % is a unit error (percent passed as a fraction), not a rate


@dataclass(frozen=True)
class Snap:
    """One ``nav_snapshots`` row of the lifetime's ``{lifetime}:ALL`` key: the fields the statistics need."""
    snap_date: str                  # ISO New York date the snapshot represents (17:45 of that day)
    nav_c: int                      # consolidated NAV in cents
    flows_day_c: int                # capital deposits - withdrawals of that day in cents (first snapshot: the starting NAV)
    partial: int = 0                # 1 when a mark was unavailable and the NAV is partial (disclosed, still used)
    quality: str = "close"

    @classmethod
    def from_row(cls, row) -> "Snap":
        """From a ``nav_snapshots`` row (``sqlite3.Row`` or dict) selected with at least the five fields."""
        try:
            partial = int(row["partial"] or 0)
        except (KeyError, IndexError):
            partial = 0
        try:
            quality = str(row["quality"] or "close")
        except (KeyError, IndexError):
            quality = "close"
        return cls(str(row["snap_date"]), int(row["nav_c"]), int(row["flows_day_c"]), partial, quality)


@dataclass
class MonthRow:
    """One calendar month of the NAV-to-NAV table (flows excluded)."""
    month: str                      # "YYYY-MM"
    start_date: str                 # nav_start's snapshot date (previous month's last; inception: the first snapshot)
    end_date: str                   # the month's last snapshot date
    nav_start_c: int
    nav_end_c: int
    flows_c: int                    # sum of flows_day_c of the month's snapshots dated after start_date
    ret: Optional[float]            # (nav_end - nav_start - flows) / nav_start; None when nav_start <= 0
    n_snapshots: int                # snapshots dated in the month

    def to_dict(self) -> dict:
        return {"month": self.month, "start_date": self.start_date, "end_date": self.end_date, "nav_start_c": self.nav_start_c,
                "nav_end_c": self.nav_end_c, "flows_c": self.flows_c, "ret": _r(self.ret, RETURN_DP), "n_snapshots": self.n_snapshots}


@dataclass
class PerfView:
    """The Performance tab's data.  Every ratio is a float or ``None`` ("n/a", explained in ``notes``); NAVs are cents.
    ``status`` is ``"ok"`` or ``"insufficient history"`` (then ``needed`` says how many snapshots are required and only
    the data window is filled)."""
    status: str
    lifetime_id: Optional[str] = None
    needed: int = NEEDED
    n_snapshots: int = 0
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    partial_snapshots: int = 0      # snapshots flagged partial (a mark was unavailable that day)
    n_returns: int = 0
    returns_skipped: int = 0        # pairs with a non-positive previous NAV (no return defined)
    returns: List[float] = field(default_factory=list)      # the daily flow-adjusted returns, in date order
    ann_ret: Optional[float] = None
    vol: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    beta: Optional[float] = None
    alpha: Optional[float] = None
    max_dd: Optional[float] = None
    hit_rate: Optional[float] = None
    benchmark: str = BENCHMARK
    pairs_used: int = 0             # return/benchmark pairs with both closes (the regression sample)
    pairs_skipped: int = 0          # pairs missing a benchmark close on either date
    skipped_dates: List[str] = field(default_factory=list)  # the snapshot dates lacking a benchmark close
    sofr: Optional[str] = None      # the SOFR used, as a decimal-fraction string ("0.0387" = 3.87 %); None = unavailable
    sofr_as_of: Optional[str] = None
    rf_daily: Optional[float] = None
    monthly: List[MonthRow] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    sentence: str = CONVENTIONS["PERF"]
    restated: List[dict] = field(default_factory=list)          # C9d: the snapshot dates read from a restated version, each labelled

    def to_dict(self) -> dict:
        return {"status": self.status, "lifetime_id": self.lifetime_id, "needed": self.needed, "n_snapshots": self.n_snapshots,
                "first_date": self.first_date, "last_date": self.last_date, "partial_snapshots": self.partial_snapshots,
                "n_returns": self.n_returns, "returns_skipped": self.returns_skipped, "returns": [_r(x, RETURN_DP) for x in self.returns],
                "ann_ret": _r(self.ann_ret), "vol": _r(self.vol), "sharpe": _r(self.sharpe), "sortino": _r(self.sortino),
                "beta": _r(self.beta), "alpha": _r(self.alpha), "max_dd": _r(self.max_dd), "hit_rate": _r(self.hit_rate),
                "benchmark": self.benchmark, "pairs_used": self.pairs_used, "pairs_skipped": self.pairs_skipped,
                "skipped_dates": list(self.skipped_dates), "sofr": self.sofr, "sofr_as_of": self.sofr_as_of,
                "rf_daily": None if self.rf_daily is None else _r(self.rf_daily, 8), "monthly": [m.to_dict() for m in self.monthly],
                "notes": list(self.notes), "sentence": self.sentence,
                "restated": [{"snap_date": x.get("snap_date"), "on": x.get("on"), "batch_id": x.get("batch_id"), "text": x.get("text")} for x in self.restated]}


def _r(x: Optional[float], dp: int = RATIO_DP) -> Optional[float]:
    """Round a ratio for JSON (``None`` stays ``None``; a non-finite float becomes ``None``)."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return round(x, dp)


# ---- pure statistics ----------------------------------------------------------------------------------------------------
def statistics(snapshots: Iterable[Snap], spy_closes: Mapping[str, Decimal], sofr: Optional[Decimal], *,
               lifetime_id: Optional[str] = None, sofr_as_of: Optional[str] = None, benchmark: str = BENCHMARK) -> PerfView:
    """The §6.9 statistics of one lifetime (pure; see the module docstring for every convention).

    ``snapshots``: the lifetime's ``{lifetime}:ALL`` rows (any order; sorted here by date).
    ``spy_closes``: ISO date -> observed close of the benchmark (``Decimal``); a missing date skips that pair.
    ``sofr``: the policy rate as a decimal fraction (``Decimal("0.0387")`` = 3.87 %) or ``None`` when unavailable.
    """
    snaps = sorted(snapshots, key=lambda s: s.snap_date)
    view = PerfView(STATUS_OK, lifetime_id=lifetime_id, n_snapshots=len(snaps), benchmark=benchmark,
                    partial_snapshots=sum(1 for s in snaps if s.partial))
    if snaps:
        view.first_date, view.last_date = snaps[0].snap_date, snaps[-1].snap_date
    rf = _rf(sofr)
    if rf is not None:
        view.sofr, view.sofr_as_of, view.rf_daily = str(sofr), sofr_as_of, rf
    if len(snaps) < NEEDED:
        view.status = STATUS_INSUFFICIENT
        view.notes.append(f"insufficient history ({len(snaps)} of {NEEDED} snapshots)")
        return view

    rets: List[float] = []
    pair_r: List[float] = []
    pair_m: List[float] = []
    for prev, cur in zip(snaps, snaps[1:]):
        if prev.nav_c <= 0:
            view.returns_skipped += 1
            continue
        r = (cur.nav_c - cur.flows_day_c) / prev.nav_c - 1.0
        rets.append(r)
        a, b = spy_closes.get(prev.snap_date), spy_closes.get(cur.snap_date)
        if a is None or b is None or Decimal(a) <= 0:
            view.pairs_skipped += 1
            for d in (prev.snap_date, cur.snap_date):
                if spy_closes.get(d) is None and d not in view.skipped_dates:
                    view.skipped_dates.append(d)
            continue
        pair_r.append(r)
        pair_m.append(float(Decimal(b) / Decimal(a)) - 1.0)
    view.n_returns, view.returns, view.pairs_used = len(rets), rets, len(pair_r)
    if view.returns_skipped:
        view.notes.append(f"{view.returns_skipped} pair(s) skipped: previous NAV not positive")
    if view.pairs_skipped:
        view.notes.append(f"{view.pairs_skipped} benchmark pair(s) skipped: no {benchmark} close on {', '.join(view.skipped_dates)}")
    if not rets:
        view.status = STATUS_INSUFFICIENT
        view.notes.append("no computable daily return")
        return view

    n = len(rets)
    mu = mean(rets)
    view.ann_ret = mu * TRADING_DAYS
    sd = stdev(rets) if n >= 2 else None
    if sd is not None:
        view.vol = sd * SQRT_TRADING_DAYS
    else:
        view.notes.append("volatility needs two daily returns")
    if rf is None:
        view.notes.append("SOFR unavailable: Sharpe, Sortino and alpha are n/a")
    else:
        if sd is None:
            pass                                            # the volatility note covers Sharpe
        elif sd > 0:
            view.sharpe = sharpe_ratio(rets, rf, TRADING_DAYS)
        else:
            view.notes.append("zero volatility: Sharpe is n/a")
        if downside_deviation(rets, rf) > 0:
            view.sortino = sortino_ratio(rets, rf, TRADING_DAYS)
        else:
            view.notes.append("no day below the policy rate: Sortino is n/a")
    if len(pair_r) >= 2:
        vm = variance(pair_m)
        if vm > 0:
            beta = covariance(pair_r, pair_m) / vm
            view.beta = beta
            if rf is not None:
                view.alpha = (mean(pair_r) - rf - beta * (mean(pair_m) - rf)) * TRADING_DAYS
        else:
            view.notes.append(f"{benchmark} did not move over the paired days: beta and alpha are n/a")
    else:
        view.notes.append(f"beta and alpha need two {benchmark} pairs")
    view.max_dd = max_drawdown(rets)
    view.hit_rate = sum(1 for r in rets if r > 0) / n
    view.monthly = monthly_table(snaps)
    return view


def _rf(sofr: Optional[Decimal]) -> Optional[float]:
    """Daily policy rate SOFR / 360 from a decimal fraction; ``None`` passes through; a unit error raises."""
    if sofr is None:
        return None
    s = Decimal(str(sofr))
    if abs(s) >= _MAX_SOFR:
        raise ValueError(f"SOFR must be a decimal fraction (0.0387 = 3.87 %), got {s}")
    return float(s) / RATE_DAYS


def max_drawdown(returns: Iterable[float]) -> float:
    """max_t (peak_t - idx_t) / peak_t on the index idx_0 = 1, idx_t = idx_{t-1} x (1 + r_t); 0.0 with no drawdown."""
    idx = peak = 1.0
    mdd = 0.0
    for r in returns:
        idx *= 1.0 + r
        if idx > peak:
            peak = idx
        dd = (peak - idx) / peak
        if dd > mdd:
            mdd = dd
    return mdd


def monthly_table(snaps: List[Snap]) -> List[MonthRow]:
    """The NAV-to-NAV monthly table with flows excluded (``snaps`` sorted by date; see the module docstring)."""
    groups: Dict[str, List[Snap]] = {}
    for s in snaps:
        groups.setdefault(s.snap_date[:7], []).append(s)
    out: List[MonthRow] = []
    prev_last: Optional[Snap] = None
    for month, rows in groups.items():
        start = prev_last if prev_last is not None else rows[0]
        end = rows[-1]
        flows = sum(s.flows_day_c for s in rows if s.snap_date > start.snap_date)
        ret = (end.nav_c - start.nav_c - flows) / start.nav_c if start.nav_c > 0 else None
        out.append(MonthRow(month, start.snap_date, end.snap_date, start.nav_c, end.nav_c, flows, ret, len(rows)))
        prev_last = end
    return out


# ---- loader (one pooled reader; no writes) ----------------------------------------------------------------------------
def perf_view(services, scope: Optional[str] = None) -> PerfView:
    """Statistics for the lifetime ``scope`` names: ``None`` / ``"ALL"`` = the selected book's open lifetime, ``"P1"``
    or ``"P1:ALL"`` = that lifetime (CLOSED ones included: History pages), an account id = its lifetime.  ``ALL_BOOKS``
    (several lifetimes) raises ``ScopeError`` -- statistics are per lifetime.  Reads the lifetime's snapshot chain (C9d: per date
    the latest restated version, else the original ``nav_snapshots`` row; ``restated`` lists the restated dates, labelled "restated
    on <date> (batch X)"), the benchmark's CLOSE ``marks`` inside the data window and SOFR as a fraction (``sofr_input``)."""
    key = scope or "ALL"
    if _LIFETIME_RE.match(key):
        key = f"{key}:ALL"
    acc = services.accounting
    with services.db.reader() as conn:
        sc = acc._scope(conn, key)
        if len(sc.lifetime_ids) != 1:
            raise ScopeError("performance statistics are per lifetime; choose one book")
        lid = sc.lifetime_ids[0]
        # C9d readers: per date the latest restated version, else the original (``accounting.snapshot_chain``), each labelled
        rows = snapshot_chain(conn, f"{lid}:ALL")
        snaps = [Snap.from_row(r) for r in rows]
        restated = [lab for lab in (restated_label(r) for r in rows) if lab]
        closes: Dict[str, Decimal] = {}
        if snaps:
            for r in conn.execute("SELECT mark_date, price FROM marks WHERE instrument_id=? AND kind='CLOSE' AND mark_date BETWEEN ? AND ?",
                                  (BENCHMARK, snaps[0].snap_date, snaps[-1].snap_date)).fetchall():
                closes[str(r["mark_date"])] = Decimal(str(r["price"]))
    sofr, as_of = sofr_input(services)
    view = statistics(snaps, closes, sofr, lifetime_id=lid, sofr_as_of=as_of)
    if restated:
        view.restated = restated
        batches = sorted({(x["on"], x.get("batch_id") or "") for x in restated})
        view.notes.append(f"{len(restated)} snapshot(s) " + "; ".join(f"restated on {on}" + (f" (batch {b})" if b else "") for on, b in batches)
                          + ": the returns use the restated NAV")
    return view


def sofr_input(services) -> Tuple[Optional[Decimal], Optional[str]]:
    """``(sofr, as_of)`` for ``statistics()``: ``sofr`` is a decimal FRACTION (``Decimal("0.0387")`` = 3.87 %).

    ``RateStore.sofr()`` is the PERCENT as FRED publishes it (3.87; ``quotes.rates`` module docstring UNITS), so this
    reads ``RateStore.sofr_fraction()`` -- the percent divided by 100 exactly once, in ``rates.pct_to_fraction`` -- and
    divides nothing again: SOFR 3.87 -> 0.0387 -> rf = 0.0387 / 360 = 0.0001075 per day.  A rate store without
    ``sofr_fraction`` (a test double) is read through its ``sofr()`` as given, and ``statistics()`` still refuses a
    value that is not a fraction.  ``(None, None)`` when no rate store is bound or it has no observation -- the view
    then says so instead of assuming a rate."""
    rates = getattr(services, "rates", None)
    if rates is None:
        return None, None
    as_fraction = getattr(rates, "sofr_fraction", None)
    got = as_fraction() if callable(as_fraction) else rates.sofr()
    if got is None:
        return None, None
    as_of, value = got
    as_of_s = as_of.isoformat() if hasattr(as_of, "isoformat") else (None if as_of is None else str(as_of))
    return Decimal(str(value)), as_of_s
