"""Single-name CDS spread MODEL - CDS_SINGLE_NAME_PLAN rev 2, package B3 (release 1.0.7; the owner 2026-10-28: "CDS needs added to prime
brokers, i see CDX, but I want to CDS on everything you can").  Bound as ``services.cds_model`` (server.OPTIONAL_MODULES, after
``credit_entities`` and before ``swaps_cds_names``); this module edits no existing file and is importable alone.  Every spread here is a
MODEL with its inputs, dates and sources - never a dealer quote, never a traded level; DTCC data is never used.

THE MODEL (plan 3.1; the CreditGrades Technical Document, RiskMetrics Group, ed. C. Finger, May 2002, sections 2.1-2.2)
- Inputs: S the share price, sigma_S the 1000-day realised volatility, D the debt per share (SEC XBRL debt / split-adjusted shares),
  L-bar 0.5 and lambda 0.3 (the document's global debt recovery and its uncertainty, from about 300 defaulted US non-financial firms),
  R 0.40 (the CDS recovery), r = -ln DF(T) / T on the swaps' Treasury-bootstrapped curve (``swaps.dfs_for``, log-linear
  ``swaps_cds.df_function``), T = (maturity - valuation date) days / 365.
- LD = L-bar x D; sigma = sigma_S x S / (S + LD); d = (S + LD) / LD x e^(lambda^2); A_t = sqrt(sigma^2 t + lambda^2);
  P(t) = Phi(-A_t/2 + ln d / A_t) - d x Phi(-A_t/2 - ln d / A_t) (survival); xi = lambda^2 / sigma^2; z = sqrt(1/4 + 2 r / sigma^2);
  G(u) = d^(z+1/2) Phi(-ln d / (sigma sqrt u) - z sigma sqrt u) + d^(-z+1/2) Phi(-ln d / (sigma sqrt u) + z sigma sqrt u);
  H = e^(r xi) (G(T + xi) - G(xi)); c* = r (1 - R)(1 - P(0) + H) / (P(0) - P(T) e^(-rT) - H) (the par spread, continuous premium).
  Float64 with ``math.erfc`` (Phi(x) = 0.5 erfc(-x / sqrt 2)); raw_bp = 10^4 c*, quantized to 0.01 bp (ROUND_HALF_EVEN, from
  ``Decimal(repr(value))``).
- THE QUADRATURE PATH: when P(0) < 0.95, when r <= 0, or when the closed form raises (Python's ``math.exp`` and float ``**`` raise
  OverflowError instead of returning infinity), gives a non-finite value or a denominator <= 0, the same par spread is computed in its
  integral form c* = (1 - R)(1 - P(T) e^(-rT) - r I) / I, I = the integral of e^(-rs) P(s) over [0, T] by the composite Simpson rule
  with 2,000 equal steps (the closed form's denominator is exactly r I: both agree to 12 significant digits where both work).  Near the
  barrier the closed form cancels (S 0.05, sigma 1.5, D 40 gives 5,024.92 bp against the exact 5,036.85): the quadrature is used.
- GUARDS: S, sigma_S or D not above 0 is no model (NO_PRICE / NO_VOL / NOT_COVERED); ln d / A_T >= 8 gives raw 0.00 bp exactly (the barrier
  is over 8 standard deviations away; it also avoids the overflow of d^(z+1/2)); a quadrature that fails too is MODEL_DOMAIN.  No
  exception ever leaves ``cg_spread``.  DISTRESSED = P(0) < 0.95 or raw > 2,000 bp: computed and labelled; a new deal needs the owner's
  negotiated mid (the engine's rule).
- CALIBRATION (plan 3.3): model spread = the ICE BofA AAA US Corporate OAS of the valuation date (FRED ``BAMLC0A1CAAA``, percent x 100)
  + raw.  RATING-LIKE BUCKET: the FRED bucket (AAA ... CCC, the same date) whose OAS is nearest in |ln(spread / OAS)| (Decimal ln, 12
  digits; a tie goes to the wider bucket), shown as "BBB-like (model spread bucket - not a credit rating)".  NOT VALIDATED against
  traded levels (plan 3.4): on the 10-28 inputs ORCL is about 113-116 bp while public news put its 5-year CDS near 295-315 bp.

THE INPUTS (plan 3.2, 4.4)
- S: live = the caller's mark snapshot (or ``services.marks``): live / delayed in session, the last close out of session (a print more
  than 15 minutes before the last session close is PRICE_STALE); a VM date d = the recorded CLOSE of R, the last trading day of the
  equity's session group on or before d (``swaps_trs._vm_close`` / ``close_on``: CLOSE_PENDING, or CAUGHT_UP when a later session's price
  exists).
- sigma_S: Yahoo daily closes (split-adjusted), the last 1,001 closes through the session R = 1,000 log returns, sample standard
  deviation (n - 1) x sqrt(252), quantized to 0.0001; at least 250 returns (SHORT_HISTORY otherwise; a shorter window is said); stored
  per (ticker, R) in ``cds_vol`` by the worker, so a VM of a past date uses that date's volatility.  Live: the latest row, up to 3
  business days older than the last complete session (labelled when older than it; NO_VOL beyond).
- Splits: every fetch's split events go to ``cds_splits`` in the same ``cds.vol_record`` as its volatility rows; each vol row records
  ``events_through`` (the last session its fetch saw).  A VM of d uses the splits dated after the share count's date and on or before R;
  a vol row of R exists only after a fetch that saw R, so a VM never runs on a split list it does not have (CLOSE_PENDING and a fetch
  queued otherwise).  Live: the split list must come from a fetch of this process that saw the price's session (the name's first preview
  of the session, or the 09:45 ET pass over the open contracts' equities): a split is known on its ex-date.
- D: ``services.credit_entities`` (C-ENT): ``fundamentals_on(conn, cik, R)`` for a VM, ``fundamentals_now`` live; shares multiplied by
  numerator / denominator of every split after the share count's date.  A balance sheet 183-465 days old is labelled; older than 465
  days (or a share count older than 465 days) is not dealable (marks and VM go on, labelled).
- The floor and the ladder: ``services.rates`` (the FRED slot already fetches every bucket of ``rates.OAS_SERIES``): live = the latest
  date of the AAA series (the latest date on which every bucket is observed when that is within 3 business days), at most 3 business days
  old (FLOOR_STALE beyond, FLOOR_UNAVAILABLE without one); a VM = the observation OF R exactly (CLOSE_PENDING until FRED publishes it the
  next morning, CAUGHT_UP when FRED skipped R and a later value exists - the CDX proxy rule).
- r: the curve OF R for a VM (``swaps.dfs_for(on=R)``: CAUGHT_UP when a later curve exists, CLOSE_PENDING otherwise), the latest live.
- Nothing here does network I/O on the calling thread: a missing live input queues a fetch (``request_inputs``, an in-memory queue)
  and answers INPUTS_PENDING with ``retry_ms``; a VM answers CLOSE_PENDING and queues it.
- COVERAGE: this module answers NOT_COVERED only when the model cannot be computed (no debt, no share count); an entity whose status is
  not offered (below the debt minimum, a foreign filer, a stale balance sheet ...) is still valued - an open contract keeps its marks
  and its variation margin - with ``dealable`` False and the reason in words (the engine refuses new deals on it).

THE QUEUE AND THE REFRESHERS (plan 4.3; B3 task 5)
- ``request_inputs(services, cik)``: an ordered set per runtime guarded by a lock (request threads add, the worker drains); each entry is
  queued / in flight / ready / error (an error is kept 10 minutes, then the next request re-queues it); a ready entry is not fetched
  again for 10 minutes unless an earlier session is wanted.
- ``cds_inputs`` (every 5 s): drains up to 5 queued names a run, in order - ``credit_entities.refresh_entity`` when the entity was not
  checked for 7 days, ``credit_entities.instrument_for`` (a resolve), then ONE Yahoo request: ``providers.yahoo._chart(symbol,
  "range=5y&interval=1d&events=div%2Csplits")`` through the provider's limiter, breaker and health (``daily`` / ``events`` when the
  providers are offline or have no ``_chart``); computes the ``cds_vol`` row of every complete session (now >= its close + 20 minutes)
  not yet stored, from the requested session (a contract's trade date, a VM date) or the last 2 sessions, and writes them with the new
  or changed split events in one light ``cds.vol_record`` (at most 500 rows a command, request id ``cds:vol:<ticker>:<sha256[:12]>``: a
  replay is a no-op).  Once per session after 09:45 ET it queues every open CDS contract's equity (its split events).  It returns at
  once when the queue is empty and when the providers are offline (``--offline``, the tests).
- ``cds_eod`` (from 16:45 ET, then hourly that evening): queues every reference equity of an OPEN CDS contract from its trade date
  (the back-fill of any missing session); the drain fetches only when a row is missing.
- The live model results of the desk list (``last_models``), the queue and the per-session fetch records live in memory (process
  lifetime) and are never persisted; the commands table keeps every payload, so no cache lives there.

ROUTE: ``GET /api/otc/cds/model?cik=|ticker=&tenor_years=5`` - 200 the model dict (a refusal: 200 ``{refused: true, code, message}``,
words first), 202 ``{status: "fetching", retry_ms, message}`` while an input is fetched, 404 ENTITY_UNKNOWN.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from datetime import date, time as dtime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .. import marks as _marks
from ..clock import epoch_of, ny_of, ny_str
from ..commands import Command, RejectError
from ..db import canonical_json
from ..quotes.rates import OAS_SERIES
from ..units import fmt_money, qprice

# ---- labels and constants ------------------------------------------------------------------------------------------------------------
LABEL = "MODEL"
MODEL_TEXT = ("equity-implied CreditGrades (RiskMetrics 2002) on the share price, 1000-day realised volatility and SEC-reported debt per share"
              " (L-bar 0.5, lambda 0.3, recovery 40 %), plus the ICE BofA AAA OAS floor (FRED) - a MODEL spread, not a dealer quote")
MODEL_VERSION = "CG2002-AAA-FLOOR-1"
VALIDATION_NOTE = ("not validated against traded single-name CDS levels: design-time checks missed traded names by about 2 times in both"
                   " directions")
FLOOR_SERIES = "BAMLC0A1CAAA"
LADDER = ("AAA", "AA", "A", "BBB", "BB", "B", "CCC")
LADDER_SERIES = {b: OAS_SERIES[b] for b in LADDER}
LBAR, LAMBDA, RECOVERY = 0.5, 0.3, 0.40
VOL_WINDOW, VOL_MIN, TRADING_DAYS = 1000, 250, 252
SIMPSON_STEPS = 2000
FAR_SD = 8.0                          # ln d / A_T at or above this: the barrier is over 8 standard deviations away -> raw 0.00 bp
DISTRESS_P0 = 0.95
DISTRESS_BP = Decimal(2000)
FLOOR_MAX_AGE_BD = 3                  # FRED posts a business day's OAS the next morning; older than 3 business days = stale
VOL_MAX_AGE_BD = 3
STALE_DAYS, AGE_WARN_DAYS = 465, 183  # balance sheet: labelled from 183 days, not dealable beyond 465 (plan 4.4, as C-ENT)
SHARES_STALE_DAYS = 465
COMPLETE_AFTER_S = 1200               # a session is complete 20 minutes after its close
PRICE_CLOSE_SLACK_S = 900             # out of session the price must be a print of the last session's close (15 minutes of slack)
PRICE_WAIT_S = 60                     # a first quote is waited for this long (INPUTS_PENDING), then NO_PRICE
SUBSCRIBE_EVERY_S = 30
RETRY_MS = 1500
ERROR_KEEP_S = 600
REFETCH_MIN_S = 600
ENTITY_CHECK_S = 7 * 86400
DRAIN_PER_RUN = 5
RESOLVE_TRIES = 24
INPUTS_INTERVAL_S = 5
EOD_ET = "16:45"
EOD_INTERVAL_S = 3600
EVENTS_PASS_ET = dtime(9, 45)
MAX_ROWS = 500
MODELS_KEEP = 4000
CHART_QUERY = "range=5y&interval=1d&events=div%2Csplits"
REFRESHER_LABEL = "Single-name CDS inputs (SEC EDGAR, Yahoo daily closes)"
TENORS = (1, 2, 3, 4, 5, 7, 10)
DEFAULT_TENOR = 5
DEFAULT_GROUP = "US_EQUITY"
BP_Q = Decimal("0.01")
SIGMA_Q = Decimal("0.0001")
PCT = Decimal(100)
METHOD_CLOSED = "closed form"
METHOD_QUAD = "quadrature, Simpson 2,000 steps"
METHOD_FAR = "far from the barrier (ln d / A_T >= 8): raw 0.00 bp exactly"
LN_CTX = Context(prec=12)
NOT_COVERED_REASONS = ("BELOW_MIN_DEBT", "NO_DEBT", "NO_SHARES", "STALE_FUNDAMENTALS", "FOREIGN_FILER", "DEBT_GT_LIABILITIES", "NOT_LISTED",
                       "NO_LISTED_COMMON")
DISTRESSED_LABEL = "distressed - outside the model's calibrated range"
FINANCIAL_NOTE = ("financial firm: CreditGrades was calibrated for industrial firms (its 2002 document excludes banks and finance firms); deposits,"
                  " repos and policy liabilities are left out of debt - treat this spread with care")
REIT_NOTE = "REIT: real-estate investment trust (debt recipes for REITs allowed)"
REASON_WORDS = {
    "NO_DEBT": "not offered: no debt reported in the latest 10-Q / 10-K XBRL (or it uses the company's own tags)",
    "NO_SHARES": "share count not reported (multi-class issuer?)",
    "FOREIGN_FILER": "not offered: foreign private issuer (ADR ratios and IFRS reports are not modelled in this release)",
    "BELOW_MIN_DEBT": "not offered: reported debt is below the USD 100,000,000 minimum (assumed)",
    "STALE_FUNDAMENTALS": "not offered: the latest balance sheet is more than 15 months old",
    "DEBT_GT_LIABILITIES": "not offered: the reported debt is larger than the total liabilities of the same balance sheet (a tagging problem)",
    "NOT_LISTED": "not offered: no longer listed on NYSE, Nasdaq, Cboe or the Texas Stock Exchange in the SEC's exchange file",
    "NO_LISTED_COMMON": "not offered: no common stock listed - only preferred shares, warrants, units or rights of this company are listed; the model"
                        " needs the share price of listed common stock",
}

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS cds_vol (ticker TEXT NOT NULL, vol_date TEXT NOT NULL, sigma TEXT NOT NULL, n_returns INTEGER NOT NULL,"
    " first_date TEXT NOT NULL, last_close TEXT NOT NULL, events_through TEXT NOT NULL, source TEXT NOT NULL, fetched_ts INTEGER NOT NULL,"
    " PRIMARY KEY (ticker, vol_date))",
    "CREATE TABLE IF NOT EXISTS cds_splits (ticker TEXT NOT NULL, split_date TEXT NOT NULL, numerator TEXT NOT NULL, denominator TEXT NOT NULL,"
    " source TEXT NOT NULL, fetched_ts INTEGER NOT NULL, PRIMARY KEY (ticker, split_date))",
]


class ShortHistory(ValueError):
    """Fewer returns than the minimum (``realised_vol``)."""

    def __init__(self, n: int, minimum: int):
        super().__init__(f"{n} daily returns, fewer than the {minimum} needed")
        self.n, self.minimum = int(n), int(minimum)


# ---- small helpers -------------------------------------------------------------------------------------------------------------------
def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _g(x) -> Optional[str]:
    """A float intermediate for the inputs panel: 12 significant digits."""
    if x is None:
        return None
    try:
        return format(float(x), ".12g")
    except (TypeError, ValueError):
        return None


def _bp(value: float) -> Decimal:
    """10^4 c* in bp to 0.01 bp, ROUND_HALF_EVEN, from ``Decimal(repr(value))`` (never "-0.00")."""
    q = Decimal(repr(float(value))).quantize(BP_Q, rounding=ROUND_HALF_EVEN)
    return q if q != 0 else Decimal("0.00")


def _finite(*xs) -> bool:
    return all(isinstance(x, float) and math.isfinite(x) for x in xs)


_SQRT2 = math.sqrt(2.0)


def _phi(x: float) -> float:
    """The standard normal CDF, 0.5 x erfc(-x / sqrt 2) (accurate in both tails)."""
    return 0.5 * math.erfc(-x / _SQRT2)


def _survival(t: float, sig: float, lam: float, d: float, ln_d: float) -> Tuple[float, float]:
    """``(P(t), A_t)``: the CreditGrades survival probability at t years."""
    a = math.sqrt(sig * sig * t + lam * lam)
    return _phi(-a / 2.0 + ln_d / a) - d * _phi(-a / 2.0 - ln_d / a), a


def _simpson(f: Callable[[float], float], a: float, b: float, n: int = SIMPSON_STEPS) -> float:
    """The composite Simpson rule with ``n`` (even) equal steps."""
    h = (b - a) / n
    s = f(a) + f(b)
    for i in range(1, n):
        s += (4.0 if i % 2 else 2.0) * f(a + i * h)
    return s * h / 3.0


# ---- the model (pure) ------------------------------------------------------------------------------------------------------------------
def _closed_form(sig: float, lam: float, d: float, ln_d: float, P0: float, PT: float, T: float, r: float, R: float) -> dict:
    """Steps 6-9 of plan 3.1: ``{xi, z, G_xi, G_T_xi, H, den, c}``; raises OverflowError / ZeroDivisionError / ValueError as Python does."""
    xi = lam * lam / (sig * sig)
    z = math.sqrt(0.25 + 2.0 * r / (sig * sig))

    def G(u: float) -> float:
        su = sig * math.sqrt(u)
        a = -ln_d / su
        return d ** (z + 0.5) * _phi(a - z * su) + d ** (0.5 - z) * _phi(a + z * su)
    g_xi = G(xi)
    g_t_xi = G(T + xi)
    H = math.exp(r * xi) * (g_t_xi - g_xi)
    den = P0 - PT * math.exp(-r * T) - H
    c = r * (1.0 - R) * (1.0 - P0 + H) / den
    return {"xi": xi, "z": z, "G_xi": g_xi, "G_T_xi": g_t_xi, "H": H, "den": den, "c": c}


def _quadrature(sig: float, lam: float, d: float, ln_d: float, PT: float, T: float, r: float, R: float) -> dict:
    """The integral form of the same par spread: ``{I, c}`` with I = the Simpson integral of e^(-rs) P(s) over [0, T]."""
    I = _simpson(lambda s: math.exp(-r * s) * _survival(s, sig, lam, d, ln_d)[0], 0.0, T)
    c = (1.0 - R) * (1.0 - PT * math.exp(-r * T) - r * I) / I
    return {"I": I, "c": c}


def cg_spread(S, sigma_s, dps, T, r, R=RECOVERY, Lbar=LBAR, lam=LAMBDA) -> dict:
    """The CreditGrades par spread (plan 3.1), pure and in float64.  ``ok`` True: ``{method, LD, sigma_A, d, A_T, P0, PT, xi, z, G_xi,
    G_T_xi, H (closed form) | I (quadrature), c, raw_bp, distressed, ...}`` (floats; ``raw_bp`` a Decimal to 0.01 bp).  ``ok`` False:
    ``{code, reason}`` (NO_PRICE, NO_VOL, NOT_COVERED, MODEL_DOMAIN) with the intermediates computed so far.  Never raises."""
    try:
        S, sigma_s, D, T, r, R, Lbar, lam = (float(x) for x in (S, sigma_s, dps, T, r, R, Lbar, lam))
    except (TypeError, ValueError, ArithmeticError) as e:
        return {"ok": False, "code": "MODEL_DOMAIN", "reason": f"the model inputs are not numbers ({type(e).__name__})"}
    base = {"S": S, "sigma_S": sigma_s, "dps": D, "T": T, "r": r, "R": R, "Lbar": Lbar, "lambda": lam}
    if not _finite(S, sigma_s, D, T, r, R, Lbar, lam):
        return dict(base, ok=False, code="MODEL_DOMAIN", reason="a model input is not a finite number")
    if S <= 0:
        return dict(base, ok=False, code="NO_PRICE", reason="no positive share price")
    if sigma_s <= 0:
        return dict(base, ok=False, code="NO_VOL", reason="no positive equity volatility")
    if D <= 0:
        return dict(base, ok=False, code="NOT_COVERED", reason=REASON_WORDS["NO_DEBT"], status_reason="NO_DEBT")
    if T <= 0:
        return dict(base, ok=False, code="MODEL_DOMAIN", reason="the maturity is not after the valuation date")
    if not (0.0 <= R < 1.0) or Lbar <= 0 or lam <= 0:
        return dict(base, ok=False, code="MODEL_DOMAIN", reason="recovery, L-bar or lambda outside the model's domain")
    out = dict(base, ok=True)
    try:
        LD = Lbar * D
        sig = sigma_s * S / (S + LD)
        d = (S + LD) / LD * math.exp(lam * lam)
        ln_d = math.log(d)
        P0, _a0 = _survival(0.0, sig, lam, d, ln_d)
        PT, A_T = _survival(T, sig, lam, d, ln_d)
    except (OverflowError, ZeroDivisionError, ValueError) as e:
        return dict(base, ok=False, code="MODEL_DOMAIN", reason=f"the model cannot be evaluated on these inputs ({type(e).__name__})")
    out.update(LD=LD, sigma_A=sig, d=d, A_T=A_T, P0=P0, PT=PT)
    if not _finite(sig, d, P0, PT, A_T) or A_T <= 0:
        return dict(out, ok=False, code="MODEL_DOMAIN", reason="the survival probabilities are not finite on these inputs")
    out["ln_d_over_A_T"] = ln_d / A_T
    if ln_d / A_T >= FAR_SD:
        out.update(method=METHOD_FAR, c=0.0, raw_bp=Decimal("0.00"), distressed=False)
        return out
    fallback = None
    if P0 < DISTRESS_P0:
        fallback = "survival now P(0) below 0.95 (distressed)"
    elif r <= 0:
        fallback = "rate r not above 0: the closed form's denominator r x I is not positive"
    else:
        try:
            cf = _closed_form(sig, lam, d, ln_d, P0, PT, T, r, R)
            if not _finite(cf["xi"], cf["z"], cf["G_xi"], cf["G_T_xi"], cf["H"], cf["den"], cf["c"]):
                fallback = "the closed form gave a non-finite value"
            elif cf["den"] <= 0:
                fallback = "the closed form's denominator is not positive"
            else:
                out.update(method=METHOD_CLOSED, xi=cf["xi"], z=cf["z"], G_xi=cf["G_xi"], G_T_xi=cf["G_T_xi"], H=cf["H"], c=cf["c"])
        except (OverflowError, ZeroDivisionError, ValueError) as e:
            fallback = f"the closed form raised {type(e).__name__}"
    if fallback is not None:
        try:
            q = _quadrature(sig, lam, d, ln_d, PT, T, r, R)
            ok = _finite(q["I"], q["c"]) and q["I"] > 0
        except (OverflowError, ZeroDivisionError, ValueError):
            ok, q = False, {}
        if not ok:
            return dict(out, ok=False, code="MODEL_DOMAIN", reason=f"the model cannot be evaluated on these inputs ({fallback}; the quadrature failed too)")
        out.update(method=METHOD_QUAD, I=q["I"], c=q["c"], fallback=fallback)
    c = out["c"]
    if c < 0:
        if c > -1e-12:
            out["c"] = c = 0.0
        else:
            return dict(out, ok=False, code="MODEL_DOMAIN", reason="the model gives a negative spread on these inputs")
    raw = _bp(1e4 * c)
    out.update(raw_bp=raw, distressed=bool(P0 < DISTRESS_P0 or raw > DISTRESS_BP))
    return out


def _vol_of_returns(rets: Sequence[Decimal]) -> Decimal:
    n = len(rets)
    mean = sum(rets, Decimal(0)) / n
    var = sum(((x - mean) * (x - mean) for x in rets), Decimal(0)) / (n - 1)
    return (var.sqrt() * Decimal(TRADING_DAYS).sqrt()).quantize(SIGMA_Q, rounding=ROUND_HALF_EVEN)


def realised_vol(closes: Sequence[Decimal], window: int = VOL_WINDOW, minimum: int = VOL_MIN) -> Tuple[Decimal, int]:
    """``(sigma, n_returns)``: the last ``window`` + 1 closes -> ``window`` log returns r_i = ln(c_i / c_(i-1)); the sample standard
    deviation (n - 1) x sqrt(252), quantized to 0.0001 (Decimal, 28 digits).  Raises ``ShortHistory`` below ``minimum`` returns and
    ValueError on a close that is not a positive number."""
    cs = []
    for c in closes:
        x = Decimal(str(c))
        if not x.is_finite() or x <= 0:
            raise ValueError(f"a close must be a positive number, got {c!r}")
        cs.append(x)
    cs = cs[-(int(window) + 1):]
    n = len(cs) - 1
    if n < max(2, int(minimum)):
        raise ShortHistory(max(0, n), max(2, int(minimum)))
    rets = [(cs[i] / cs[i - 1]).ln() for i in range(1, len(cs))]
    return _vol_of_returns(rets), n


def bucket_of(spread_bp, ladder_bp: Dict[str, Decimal]) -> str:
    """The rating-like bucket of a spread: the ladder bucket whose OAS is nearest in |ln(spread / OAS)| (Decimal ln to 12 digits); a tie
    goes to the wider bucket (the larger OAS, then the lower rating).  A spread not above 0 takes the tightest bucket.  Raises
    ValueError when the ladder has no positive OAS."""
    rows = []
    for i, b in enumerate(LADDER):
        v = (ladder_bp or {}).get(b)
        if v is None:
            continue
        try:
            oas = Decimal(str(v))
        except (InvalidOperation, ValueError):
            continue
        if oas.is_finite() and oas > 0:
            rows.append((i, b, oas))
    if not rows:
        raise ValueError("the ladder has no positive OAS")
    s = Decimal(str(spread_bp))
    if not s.is_finite() or s <= 0:
        return min(rows, key=lambda x: (x[2], x[0]))[1]
    best = min(rows, key=lambda x: (abs(LN_CTX.ln(s / x[2])), -x[2], -x[0]))
    return best[1]


def bucket_label(bucket: Optional[str]) -> str:
    return f"{bucket}-like (model spread bucket - not a credit rating)" if bucket else "no bucket (ladder unavailable)"


def spread_at(model: dict, s_mult) -> Optional[Decimal]:
    """Plan 5.2: the model spread (floor + raw, bp) re-run from ``model["inputs"]`` with the share price x ``s_mult`` - the volatility, the
    debt per share, the floor, the curve and T held.  None when the model is not ok or the re-run fails (never 0)."""
    try:
        if not isinstance(model, dict) or not model.get("ok"):
            return None
        inp = model.get("inputs") or {}
        S = Decimal(str(inp["S"])) * Decimal(str(s_mult))
        cg = cg_spread(float(S), float(Decimal(str(inp["sigma"]))), float(inp["dps"]), float(model["T"]), float(inp["r"]),
                       float(inp.get("R", RECOVERY)), float(inp.get("Lbar", LBAR)), float(inp.get("lambda", LAMBDA)))
        if not cg.get("ok"):
            return None
        return (Decimal(str(model["floor_bp"])) + cg["raw_bp"]).quantize(BP_Q)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None


def maturity_for(cal, trade_date, tenor_years: int) -> date:
    """Plan 2.1: roll = the on-the-run roll date (20 March / 20 September, rolled following); maturity = 20 December (a September roll)
    or 20 June (a March roll) ``tenor_years`` later."""
    from . import swaps_cds
    roll = swaps_cds.on_the_run(cal, _date(trade_date))["roll_date"]
    return date(roll.year + int(tenor_years), 12 if roll.month == 9 else 6, 20)


# ---- the in-memory state of a runtime: the fetch queue, this process's fetches, the quote waits ----------------------------------------
class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entries: "OrderedDict[int, dict]" = OrderedDict()   # cik -> {status, ts, since, force, name, tries, not_before, ...}
        self.fetched: Dict[str, dict] = {}         # ticker -> this process's last fetch {ts, events_through, first_bar, last_bar, since_done}
        self.no_bar: Dict[str, set] = {}           # ticker -> sessions (ISO) the fetched daily history has no close of
        self.price_wait: Dict[str, int] = {}       # instrument id -> when its first quote started to be waited for
        self.subscribed: Dict[str, int] = {}       # instrument id -> the last WATCH subscription
        self.events_day: Optional[str] = None      # the NY date of the last 09:45 ET pass


_STATE_ATTR = "_cds_model_state"
_STATE_LOCK = threading.Lock()
_FALLBACK = _State()
_MODELS: "OrderedDict[int, dict]" = OrderedDict()              # cik -> the last live 5Y model of this process (the desk list)
_MODELS_LOCK = threading.Lock()


def _state(services) -> _State:
    """The runtime's own state (an attribute of the services object, so two runtimes in one process never share a queue)."""
    st = getattr(services, _STATE_ATTR, None)
    if isinstance(st, _State):
        return st
    with _STATE_LOCK:
        st = getattr(services, _STATE_ATTR, None)
        if not isinstance(st, _State):
            st = _State()
            try:
                setattr(services, _STATE_ATTR, st)
            except (AttributeError, TypeError):
                return _FALLBACK
        return st


def _now(services, now=None) -> int:
    return int(now if now is not None else services.clock.epoch())


def _id_safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))[:24]


def _sha12(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:12]


def _log(services, level: str, msg: str, *args) -> None:
    log = getattr(services, "log", None)
    fn = getattr(log, level, None) if log is not None else None
    if callable(fn):
        try:
            fn(msg, *args)
        except Exception:   # noqa: BLE001 - logging never breaks the work it reports on
            pass


# ---- sessions --------------------------------------------------------------------------------------------------------------------------
def _session_close(services, group: str, d: date) -> Optional[int]:
    sessions = getattr(services, "sessions", None)
    if sessions is None:
        return None
    try:
        c = sessions.session_close(group, d)
    except KeyError:
        return None
    return None if c is None else int(c)


def _last_session(services, group: str, d: date) -> Optional[Tuple[date, int]]:
    """``(the last trading day of the group on or before d, its close epoch)`` within 10 days."""
    for i in range(11):
        day = d - timedelta(days=i)
        c = _session_close(services, group, day)
        if c is not None:
            return day, c
    return None


def last_complete_session(services, group: str, now: int) -> Optional[date]:
    """The last session of the group that is complete at ``now`` (now >= its close + 20 minutes)."""
    d = ny_of(int(now)).date()
    for _ in range(8):
        got = _last_session(services, group, d)
        if got is None:
            return None
        if int(now) >= got[1] + COMPLETE_AFTER_S:
            return got[0]
        d = got[0] - timedelta(days=1)
    return None


def _last_closed_session(services, group: str, now: int) -> Optional[Tuple[date, int]]:
    d = ny_of(int(now)).date()
    for _ in range(8):
        got = _last_session(services, group, d)
        if got is None:
            return None
        if int(now) >= got[1]:
            return got
        d = got[0] - timedelta(days=1)
    return None


def _sessions_between(services, group: str, start: date, end: date) -> List[date]:
    out, d = [], start
    while d <= end:
        if _session_close(services, group, d) is not None:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bd(services, a: date, b: date) -> int:
    cal = getattr(services, "cal", None)
    if cal is not None and hasattr(cal, "business_days_between"):
        return int(cal.business_days_between(a, b))
    return max(0, (b - a).days)


# ---- the fetch queue (request threads add, the worker drains) ---------------------------------------------------------------------------
def _answer(e: dict) -> dict:
    who = e.get("name") or f"CIK {e['cik']}"
    if e["status"] in ("queued", "in flight"):
        return {"status": "queued", "retry_ms": RETRY_MS,
                "message": e.get("message") or f"fetching {who}'s SEC filings and 1,000 daily closes (first time today only)"}
    if e["status"] == "error":
        return {"status": "error", "retry_ms": None, "code": e.get("code") or "NO_VOL", "message": e.get("message") or "the inputs could not be fetched",
                "at": e.get("ts")}
    return {"status": "ready", "retry_ms": None, "message": f"{who}: inputs fetched {ny_str(int(e['ts']))}", "at": e.get("ts"),
            "events_through": e.get("events_through"), "since_done": e.get("since_done")}


def request_inputs(services, cik, since=None, force: bool = True, name: Optional[str] = None, now: Optional[int] = None,
                   events_through=None) -> dict:
    """Queue a fetch of one name's inputs for the worker: ``{status: queued|ready|error, retry_ms, message}``.  Never blocks, no I/O.
    ``since``: the earliest session wanted (a contract's trade date, a VM date; default the last 2 sessions); ``force``: fetch even when
    every wanted session is stored (the split events of the session); ``events_through``: the session the caller's split list must have
    seen (a ready entry whose fetch saw less is queued again, at most once a minute).  An error is kept 10 minutes; a ready entry is not
    fetched again for 10 minutes unless an earlier session or a later split check is wanted."""
    try:
        cik = int(cik)
    except (TypeError, ValueError):
        return {"status": "error", "retry_ms": None, "code": "ENTITY_UNKNOWN", "message": f"{cik!r} is not a CIK"}
    ts = _now(services, now)
    since_iso = None if since in (None, "") else _date(since).isoformat()
    ev_iso = None if events_through in (None, "") else _date(events_through).isoformat()
    st = _state(services)
    with st.lock:
        e = st.entries.get(cik)
        if e is not None:
            if name:
                e["name"] = name
            if e["status"] in ("queued", "in flight"):
                if since_iso and (e.get("since") is None or since_iso < e["since"]):
                    e["since"] = since_iso
                e["force"] = bool(e.get("force")) or bool(force)
                return _answer(e)
            age = ts - int(e.get("ts") or 0)
            if e["status"] == "error" and age < ERROR_KEEP_S:
                return _answer(e)
            if e["status"] == "ready":
                earlier = since_iso is not None and (e.get("since_done") is None or since_iso < e["since_done"])
                later_events = ev_iso is not None and (e.get("events_through") or "") < ev_iso and age >= 60
                if not earlier and not later_events and age < REFETCH_MIN_S:
                    return _answer(e)
        ne = {"cik": cik, "status": "queued", "ts": ts, "queued_ts": ts, "since": since_iso, "force": bool(force), "tries": 0, "not_before": 0,
              "name": name or (e or {}).get("name"), "code": None, "message": None}
        st.entries[cik] = ne
        st.entries.move_to_end(cik)
        if len(st.entries) > MODELS_KEEP:                          # old finished entries go first; queued ones are kept
            for k in [k for k, v in st.entries.items() if v["status"] in ("ready", "error")][:len(st.entries) - MODELS_KEEP]:
                st.entries.pop(k, None)
        return _answer(ne)


def queue_status(services) -> dict:
    """The queue for the health views: ``{queued, in_flight, ready, error, entries: [...]}`` (in memory)."""
    st = _state(services)
    with st.lock:
        rows = [dict(e) for e in st.entries.values()]
    out = {"queued": 0, "in_flight": 0, "ready": 0, "error": 0}
    for e in rows:
        out[{"queued": "queued", "in flight": "in_flight", "ready": "ready", "error": "error"}[e["status"]]] += 1
    out["entries"] = rows[-50:]
    return out


def _take(st: _State, n: int, now: int) -> List[dict]:
    with st.lock:
        out = []
        for e in st.entries.values():
            if e["status"] == "queued" and int(e.get("not_before") or 0) <= now:
                e["status"] = "in flight"
                out.append(dict(e))
                if len(out) >= n:
                    break
        return out


def _finish(st: _State, cik: int, status: str, now: int, **kw) -> None:
    with st.lock:
        e = st.entries.get(cik)
        if e is None:
            return
        e.update(kw)
        e["status"] = status
        e["ts"] = int(now)


# ---- the ladder and the floor (FRED, plan 3.2 / 3.3) -------------------------------------------------------------------------------------
def _fred_maintenance(rates) -> bool:
    try:
        return bool((rates.fred_status() or {}).get("maintenance")) if rates is not None and hasattr(rates, "fred_status") else False
    except Exception:   # noqa: BLE001 - informational
        return False


def ladder(services, on: Optional[date] = None, today: Optional[date] = None) -> dict:
    """The FRED rating ladder of one date and the AAA floor: ``{ok, code, reason, as_of, age_bd, rows {bucket: {series_id, value_pct,
    spread_bp, as_of, age_bd, source}}, floor {...}, missing}``.  Live (``on`` None): the latest AAA date (the latest date within 3
    business days on which every bucket is observed, when there is one), FLOOR_UNAVAILABLE / FLOOR_STALE otherwise; ``on`` (a VM): the
    AAA observation OF that date exactly - CLOSE_PENDING until FRED publishes it, CAUGHT_UP when FRED skipped it and a later one exists."""
    rates = getattr(services, "rates", None)
    today = _date(today) if today is not None else ny_of(_now(services)).date()
    on = None if on is None else _date(on)
    maint = _fred_maintenance(rates)
    base = {"label": "ICE BofA US corporate option-adjusted spreads by rating (FRED); the AAA series is the floor", "floor_series": FLOOR_SERIES,
            "series": dict(LADDER_SERIES), "fred_maintenance": maint, "rows": {}, "floor": None, "missing": list(LADDER)}
    if rates is None or not hasattr(rates, "as_of_le") or not hasattr(rates, "latest"):
        return dict(base, ok=False, code="FLOOR_UNAVAILABLE", reason="no single-name CDS price: no rate store is bound, so the ICE BofA AAA OAS floor is unknown")

    def exact(sid: str, d: date):
        got = rates.as_of_le(sid, d)
        return got if got is not None and got[0] == d else None
    if on is not None:
        if exact(FLOOR_SERIES, on) is None:
            latest = rates.latest(FLOOR_SERIES)
            later = latest is not None and latest[0] > on
            reason = (f"the ICE BofA AAA OAS (the floor, FRED {FLOOR_SERIES}) has no value for {on.isoformat()} (FRED skipped that date) and a later one"
                      f" ({latest[0].isoformat()}) exists" if later else
                      f"the ICE BofA AAA OAS (the floor, FRED {FLOOR_SERIES}) of {on.isoformat()} is not published yet (FRED posts a business day's value"
                      " the next morning)")
            return dict(base, ok=False, code="CAUGHT_UP" if later else "CLOSE_PENDING", close_date=on.isoformat(),
                        latest_as_of=None if latest is None else latest[0].isoformat(), reason=reason)
        day = on
    else:
        latest = rates.latest(FLOOR_SERIES)
        if latest is None:
            why = "FRED unavailable (maintenance) and no observation kept" if maint else "no observation received from FRED yet"
            return dict(base, ok=False, code="FLOOR_UNAVAILABLE", reason=f"no single-name CDS price: the ICE BofA AAA OAS floor (FRED {FLOOR_SERIES}): {why}")
        age = _bd(services, latest[0], today)
        if age > FLOOR_MAX_AGE_BD:
            return dict(base, ok=False, code="FLOOR_STALE", as_of=latest[0].isoformat(), age_bd=age,
                        reason=(f"no single-name CDS price: the latest ICE BofA AAA OAS (FRED {FLOOR_SERIES}) is dated {latest[0].isoformat()}, {age} business"
                                f" days old (more than {FLOOR_MAX_AGE_BD}; FRED publishes a business day's value the next morning)"
                                + (" - FRED unavailable (maintenance)" if maint else "")))
        day = latest[0]
        series = rates.series(FLOOR_SERIES) if hasattr(rates, "series") else [latest]
        for od, _v in reversed(series):
            if _bd(services, od, today) > FLOOR_MAX_AGE_BD:
                break
            if all(exact(sid, od) is not None for sid in LADDER_SERIES.values()):
                day = od
                break
    rows, missing = {}, []
    for b in LADDER:
        sid = LADDER_SERIES[b]
        got = exact(sid, day)
        if got is None:
            missing.append(b)
            continue
        pct = Decimal(str(got[1]))
        rows[b] = {"bucket": b, "series_id": sid, "value_pct": str(qprice(pct)), "spread_bp": (pct * PCT).quantize(BP_Q), "as_of": day.isoformat(),
                   "age_bd": _bd(services, day, today), "source": (rates.source(sid) if hasattr(rates, "source") else None) or "FRED"}
    floor = dict(rows["AAA"], label="the floor: ICE BofA AAA US Corporate OAS (FRED)")
    note = (f"no {', '.join(missing)} OAS observed on {day.isoformat()}: the bucket is the nearest of the others" if missing else "")
    return dict(base, ok=True, code=None, reason=None, as_of=day.isoformat(), age_bd=_bd(services, day, today), rows=rows, floor=floor,
                missing=missing, note=note)


# ---- the inputs of one name ------------------------------------------------------------------------------------------------------------
def _fail(code: str, reason: str, **extra) -> dict:
    out = {"ok": False, "label": LABEL, "code": code, "reason": reason, "message": reason}
    out.update(extra)
    return out


def _pending(reason: str, retry_ms: Optional[int] = None, **extra) -> dict:
    return _fail("INPUTS_PENDING", reason, retry_ms=int(retry_ms or RETRY_MS), **extra)


def _coverage(ent: dict) -> Tuple[bool, Optional[str], Optional[str]]:
    """``(offered, reason code, words)`` from C-ENT's entity view: ``offered`` / ``status`` OFFERED | NOT_OFFERED with ``status_reason``
    (NO_DEBT, BELOW_MIN_DEBT, FOREIGN_FILER, NOT_LISTED ...) and ``status_words`` (derived by C-ENT as of today)."""
    status = str(ent.get("status") or "").strip().upper()
    reason = str(ent.get("status_reason") or "").strip()
    code = reason.upper() if reason.upper() in NOT_COVERED_REASONS else (status if status in NOT_COVERED_REASONS else None)
    offered = ent.get("offered")
    if isinstance(offered, bool):
        if offered:
            return True, None, None
    elif code is None and not status.startswith("NOT") and status not in ("EXCLUDED", "REFUSED", "UNAVAILABLE"):
        return True, None, None
    words = None
    for k in ("status_words", "status_text", "reason_text", "words"):
        v = ent.get(k)
        if isinstance(v, str) and v.strip() and v.strip().lower() != "offered":
            words = v.strip()
            break
    if words is None and " " in reason:
        words = reason
    if words is None:
        words = REASON_WORDS.get(code or "", "not offered for single-name CDS")
    return False, code, words


def _flag(ent: dict) -> Tuple[str, Optional[str]]:
    """``(FINANCIAL | REIT | "", words)``: C-ENT's flag with its source (filing tags until the SIC is checked) and the CreditGrades caveat."""
    flag = str(ent.get("flag") or "").strip().upper()
    src = str(ent.get("flag_source") or "").strip().lower()
    tags = src in ("tags", "tag", "filing tags", "frames")
    own = str(ent.get("flag_note") or "").strip()
    if flag.startswith("FIN") or flag in ("BANK", "INSURER", "INSURANCE"):
        head = own or ("financial (from filing tags; SIC not checked yet)" if tags else "financial")
        caveat = str(ent.get("limited_reason") or FINANCIAL_NOTE).strip()
        return "FINANCIAL", f"{head} - {caveat}" if caveat and caveat not in head else head
    if flag == "REIT":
        return "REIT", own or (REIT_NOTE + (" (from filing tags; SIC not checked yet)" if tags else ""))
    return "", None


def _int(x) -> Optional[int]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return int(Decimal(str(x)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _structural(f: Optional[dict], who: dict) -> Optional[dict]:
    """NOT_COVERED when the model cannot be computed from the fundamentals: none, no debt above 0, no share count."""
    if not isinstance(f, dict):
        return _fail("NOT_COVERED", REASON_WORDS["NO_DEBT"], status_reason="NO_DEBT", **who)
    debt = _int(f.get("debt_c"))
    if debt is None or debt <= 0:
        return _fail("NOT_COVERED", REASON_WORDS["NO_DEBT"], status_reason="NO_DEBT", **who)
    shares = _int(f.get("shares"))
    if shares is None or shares <= 0:
        return _fail("NOT_COVERED", REASON_WORDS["NO_SHARES"], status_reason="NO_SHARES", **who)
    return None


def _splits(conn, ticker: str, after: date, through: date) -> List[dict]:
    try:
        rows = conn.execute("SELECT split_date, numerator, denominator, source FROM cds_splits WHERE ticker=? AND split_date>? AND split_date<=?"
                            " ORDER BY split_date", (ticker, after.isoformat(), through.isoformat())).fetchall()
    except Exception:   # noqa: BLE001 - a runtime without the table (not bound) has no split rows
        return []
    return [{"split_date": r["split_date"], "numerator": str(r["numerator"]), "denominator": str(r["denominator"]), "source": r["source"]} for r in rows]


def _vol_row(conn, ticker: str, vol_date: Optional[date] = None, upto: Optional[date] = None) -> Optional[dict]:
    try:
        if vol_date is not None:
            r = conn.execute("SELECT * FROM cds_vol WHERE ticker=? AND vol_date=?", (ticker, vol_date.isoformat())).fetchone()
        else:
            r = conn.execute("SELECT * FROM cds_vol WHERE ticker=? AND vol_date<=? ORDER BY vol_date DESC LIMIT 1", (ticker, (upto or date.max).isoformat())).fetchone()
    except Exception:   # noqa: BLE001 - not bound: no stored volatility
        return None
    return None if r is None else {k: r[k] for k in r.keys()}


def _has_later_vol(conn, ticker: str, d: date) -> bool:
    try:
        return conn.execute("SELECT 1 FROM cds_vol WHERE ticker=? AND vol_date>? LIMIT 1", (ticker, d.isoformat())).fetchone() is not None
    except Exception:   # noqa: BLE001
        return False


def _subscribe(services, st: _State, iid: str, ts: int) -> None:
    """Keep the reference equity's quote coming while the model is asked for (WATCH, 90 s; at most once per 30 s per name)."""
    worker = getattr(services, "worker", None)
    fn = getattr(worker, "subscribe", None) if worker is not None else None
    if not callable(fn):
        return
    with st.lock:
        last = st.subscribed.get(iid)
        if last is not None and ts - last < SUBSCRIBE_EVERY_S:
            return
        st.subscribed[iid] = ts
    try:
        fn([iid], 90, "WATCH")
    except Exception as e:   # noqa: BLE001 - a subscription is a convenience; the price check below says what is missing
        _log(services, "warning", "cds_model: subscribe %s failed: %s", iid, e)


def _frozen_marks(snap, iid: str):
    """B4-R1 (integration): the caller's mark snapshot as an object with ``mark()``.  ``services.marks`` is the live ``marks.MarkProvider``,
    which has ``snapshot()`` and ``mark_for()`` but no ``mark()``: it is frozen for this one name (one ``FrozenMarks``, as the TRS does);
    a ``FrozenMarks`` (or a test's object with ``mark()``) is used as given; None when neither applies (an unknown print, never a crash)."""
    if snap is None or callable(getattr(snap, "mark", None)):
        return snap
    fn = getattr(snap, "snapshot", None)
    if callable(fn):
        fz = fn([iid])
        return fz if callable(getattr(fz, "mark", None)) else None
    return None


def _live_price(services, st: _State, snap, iid: str, group: str, ts: int, who: dict) -> dict:
    """The live share price: ``{ok, S, info, session}`` or a refusal (NO_PRICE / PRICE_STALE, INPUTS_PENDING while a first quote is
    awaited for up to a minute)."""
    mv = None
    try:
        snap = _frozen_marks(snap, iid)     # B4-R1: services.marks (the live MarkProvider) has no mark(): one FrozenMarks of this name
        mv = snap.mark(iid) if snap is not None else None
    except Exception:   # noqa: BLE001 - a failing mark is an unavailable price with the reason
        mv = None
    px = getattr(mv, "mark", None) if mv is not None else None
    q = str(getattr(mv, "quality", "unavailable") or "unavailable") if mv is not None else "unavailable"
    qt = getattr(mv, "quote_time", None) if mv is not None else None
    bad = None
    if px is None or q == "unavailable":
        bad = ("NO_PRICE", f"no share price for {iid}" + (f" ({getattr(mv, 'note', '')})" if mv is not None and getattr(mv, "note", "") else ""))
    else:
        try:
            S = qprice(px)
        except (InvalidOperation, ValueError, TypeError):
            S = Decimal(0)
        if S <= 0:
            bad = ("NO_PRICE", f"no positive share price for {iid}")
        elif q not in ("live", "delayed", "close"):
            bad = ("PRICE_STALE", f"the share price of {iid} is {q} ({ny_str(int(qt)) if qt else 'no time'})")
        elif q == "close":
            last = _last_closed_session(services, group, ts)
            if last is not None and (qt is None or int(qt) < last[1] - PRICE_CLOSE_SLACK_S):
                when = ny_str(int(qt)) if qt else "no time"
                bad = ("PRICE_STALE", f"the last price of {iid} ({when}) is older than its session's last close ({ny_str(last[1])})")
    if bad is not None:
        has_worker = callable(getattr(getattr(services, "worker", None), "subscribe", None))
        with st.lock:
            first = st.price_wait.setdefault(iid, ts)
        if has_worker and ts - first < PRICE_WAIT_S:
            return _pending(f"fetching the share price of {iid}", **who)
        return _fail(bad[0], bad[1], **who)
    with st.lock:
        st.price_wait.pop(iid, None)
    session = ny_of(int(qt)).date() if qt else ny_of(ts).date()
    info = {"price": str(S), "quality": q, "source": getattr(mv, "source", None), "quote_time": int(qt) if qt else None,
            "delay_label": getattr(mv, "delay_label", None), "basis": "the live mark" if q in ("live", "delayed") else "the last close"}
    return {"ok": True, "S": S, "info": info, "session": session}


def _curve(services, maturity: date, v: date, on: Optional[date]) -> Tuple[Optional[dict], Optional[dict]]:
    """``(curve, None)`` with ``{r, df_T, dfs, inputs, T_days}``, or ``(None, refusal)``: NO_CURVE live; for a VM CAUGHT_UP when a curve
    dated after the VM's session exists, else CLOSE_PENDING."""
    from ..commands import ConflictError
    from . import swaps, swaps_cds
    days = (maturity - v).days
    if days <= 0:
        return None, {"code": "MODEL_DOMAIN", "reason": f"the maturity {maturity.isoformat()} is not after the valuation date {v.isoformat()}"}
    n = max(2, int((Decimal(days) / Decimal(365) * 2).to_integral_value(rounding="ROUND_CEILING")) + 1)
    try:
        dfs, cin = swaps.dfs_for(services, n, on=on)
    except ConflictError as e:
        if on is None:
            return None, {"code": "NO_CURVE", "reason": f"no single-name CDS price: {e.message}"}
        later = None
        try:
            later = swaps.curve_as_of(services)
        except Exception:   # noqa: BLE001
            later = None
        if later is not None and later > on:
            return None, {"code": "CAUGHT_UP", "reason": f"no Treasury par curve of {on.isoformat()} (or of the 3 business days before it) is recorded and a later"
                                                         f" one ({later.isoformat()}) exists"}
        return None, {"code": "CLOSE_PENDING", "reason": f"the Treasury par curve of {on.isoformat()} is not recorded yet"}
    df = swaps_cds.df_function(dfs)
    t = Decimal(days) / Decimal(365)
    df_t = df(t)
    r = float(-df_t.ln() / t)
    return {"r": r, "df_T": df_t, "inputs": cin, "T_days": days}, None


def _months(days: int) -> int:
    return int(round(days / 30.4375))


def _assemble(services, ent: dict, f: dict, maturity: date, v: date, S: Decimal, s_info: dict, vol: dict, splits: List[dict],
              lad: dict, cur: dict, ts: int, vm: bool, underlying: dict, notes: List[str], offered: Tuple[bool, Optional[str], Optional[str]]) -> dict:
    """The model dict of C-MODEL (plan 6.4) from the gathered inputs, or the model's own refusal."""
    cik, ticker, name = int(ent["cik"]), str(ent.get("ticker") or ""), ent.get("name") or ent.get("ticker")
    who = {"cik": cik, "ticker": ticker, "name": name}
    debt_c = _int(f.get("debt_c"))
    shares = _int(f.get("shares"))
    end = _date(f.get("end_date")) if f.get("end_date") else None
    shares_end = _date(f.get("shares_end")) if f.get("shares_end") else end
    factor, adj = Decimal(1), []
    for sp in splits:
        num, den = Decimal(sp["numerator"]), Decimal(sp["denominator"])
        if num > 0 and den > 0:
            factor *= num / den
            adj.append(f"{sp['split_date']} {sp['numerator']}:{sp['denominator']}")
    shares_adj = Decimal(shares) * factor
    dps = float(Decimal(debt_c) / PCT / shares_adj)
    T = cur["T_days"] / 365.0
    sigma = Decimal(str(vol["sigma"]))
    cg = cg_spread(float(S), float(sigma), dps, T, cur["r"])
    if not cg.get("ok"):
        return _fail(cg["code"], cg["reason"], **who)
    floor_bp = Decimal(str(lad["floor"]["spread_bp"]))
    spread = (floor_bp + cg["raw_bp"]).quantize(BP_Q)
    ladder_bp = {b: r["spread_bp"] for b, r in (lad.get("rows") or {}).items()}
    bucket = bucket_of(spread, ladder_bp)
    flag, flag_note = _flag(ent)
    quality = "distressed" if cg["distressed"] else "computed"
    dealable, why = offered[0], (None if offered[0] else offered[2])
    bs_age = (v - end).days if end is not None else None
    sh_age = (v - shares_end).days if shares_end is not None else None
    if bs_age is not None and bs_age > STALE_DAYS:
        dealable, why = False, f"not offered: balance sheet {end.isoformat()} is {_months(bs_age)} months old"
    elif sh_age is not None and sh_age > SHARES_STALE_DAYS:
        dealable, why = False, f"not offered: the share count of {shares_end.isoformat()} is {_months(sh_age)} months old"
    if bs_age is not None and AGE_WARN_DAYS <= bs_age <= STALE_DAYS:
        notes.append(f"balance sheet {end.isoformat()} is {_months(bs_age)} months old")
    if flag_note:
        notes.append(flag_note)
    if quality == "distressed":
        notes.append(DISTRESSED_LABEL + " (a new deal needs your negotiated mid)")
    if int(vol["n_returns"]) < VOL_WINDOW:
        notes.append(f"volatility from {vol['n_returns']} daily returns (fewer than 1000: listed since {vol.get('first_date')})")
    notes.append(VALIDATION_NOTE)
    parts = f.get("debt_parts") if isinstance(f.get("debt_parts"), dict) else {}
    debt_text = f.get("debt_text") if isinstance(f.get("debt_text"), str) and f.get("debt_text") else None
    if debt_text is None:
        debt_text = " + ".join(f"{k} {fmt_money(int(_int(c) or 0))[:-3]}" for k, c in parts.items()) if parts else f"debt {fmt_money(debt_c)[:-3]}"
        debt_text += f" ({f.get('form') or 'filing'}, balance sheet {end.isoformat() if end else 'unknown'})"
    shares_note = ("" if not adj else f"{shares:,} shares x the splits after {shares_end.isoformat()} ({', '.join(adj)}) = {shares_adj.normalize():,f} shares")
    inputs = {"S": str(S), "S_source": s_info.get("source"), "S_quality": s_info.get("quality"), "S_basis": s_info.get("basis"),
              "S_time": s_info.get("quote_time"), "S_close_date": s_info.get("close_date"),
              "sigma": str(sigma), "sigma_window": int(vol["n_returns"]), "sigma_through": vol["vol_date"], "sigma_first_date": vol.get("first_date"),
              "sigma_source": vol.get("source"), "sigma_events_through": vol.get("events_through"), "sigma_age_bd": vol.get("age_bd", 0),
              "debt_c": debt_c, "debt_text": debt_text, "debt_parts": parts, "debt_note": f.get("debt_note"), "debt_recipe": f.get("debt_recipe"),
              "debt_end": end.isoformat() if end else None, "debt_known_from": f.get("known_from"), "debt_form": f.get("form"), "debt_filed": f.get("filed"),
              "debt_source": f.get("source"), "balance_sheet_age_days": bs_age,
              "shares": int(shares_adj) if shares_adj == shares_adj.to_integral_value() else str(shares_adj.normalize()), "shares_reported": shares,
              "shares_end": shares_end.isoformat() if shares_end else None, "shares_tag": f.get("shares_tag"), "shares_adj_note": shares_note,
              "splits": splits, "dps": repr(dps), "flag": flag, "flag_source": ent.get("flag_source"), "flag_note": flag_note,
              "r": repr(cur["r"]), "r_rule": "-ln DF(T) / T on the swaps' Treasury-bootstrapped curve (log-linear)", "curve_as_of": cur["inputs"].get("curve_as_of"),
              "curve_source": cur["inputs"].get("curve_source"), "R": "0.40", "Lbar": "0.5", "lambda": "0.3", "T_days": cur["T_days"]}
    inter = {"LD": _g(cg["LD"]), "sigma_A": _g(cg["sigma_A"]), "d": _g(cg["d"]), "A_T": _g(cg["A_T"]), "P0": _g(cg["P0"]), "PT": _g(cg["PT"]),
             "c": _g(cg["c"]), "ln_d_over_A_T": _g(cg.get("ln_d_over_A_T"))}
    if cg["method"] == METHOD_CLOSED:
        inter.update(xi=_g(cg["xi"]), z=_g(cg["z"]), G_xi=_g(cg["G_xi"]), G_T_xi=_g(cg["G_T_xi"]), H=_g(cg["H"]))
    elif cg["method"] == METHOD_QUAD:
        inter.update(I=_g(cg["I"]), fallback=cg.get("fallback"))
    out = {"ok": True, "label": LABEL, "model": MODEL_TEXT, "model_version": MODEL_VERSION, "validation": VALIDATION_NOTE, **who,
           "valuation_date": v.isoformat(), "maturity_date": maturity.isoformat(), "T": repr(T), "T_days": cur["T_days"],
           "raw_bp": cg["raw_bp"], "floor_bp": floor_bp, "spread_bp": spread, "bucket": bucket, "bucket_label": bucket_label(bucket),
           "quality": quality, "quality_note": DISTRESSED_LABEL if quality == "distressed" else None, "dealable": bool(dealable), "dealable_reason": why,
           "status_reason": offered[1], "floor": dict(lad["floor"], source=lad["floor"].get("source") or "FRED"), "ladder": lad, "inputs": inputs,
           "method": cg["method"], "intermediate": inter, "underlying": underlying, "proxy": {"as_of": lad["floor"]["as_of"], "series_id": FLOOR_SERIES},
           "curve_as_of": cur["inputs"].get("curve_as_of"), "notes": notes, "as_of": int(ts), "vm": bool(vm),
           "spread_rule": (f"model {spread} bp = ICE BofA AAA OAS floor {floor_bp} bp (FRED {FLOOR_SERIES} as of {lad['floor']['as_of']}) + CreditGrades"
                           f" {cg['raw_bp']} bp ({cg['method']})")}
    return out


def _remember(m: dict, ts: int) -> None:
    keep = {k: m.get(k) for k in ("ok", "label", "cik", "ticker", "name", "spread_bp", "raw_bp", "floor_bp", "bucket", "bucket_label", "quality",
                                  "dealable", "dealable_reason", "valuation_date", "maturity_date", "code", "reason", "method")}
    keep["computed_ts"] = int(ts)
    with _MODELS_LOCK:
        _MODELS[int(m["cik"])] = keep
        _MODELS.move_to_end(int(m["cik"]))
        while len(_MODELS) > MODELS_KEEP:
            _MODELS.popitem(last=False)


def last_models(ciks=None) -> Dict[int, dict]:
    """The live 5Y results of this process for the desk list (``{cik: {ok, spread_bp, bucket, bucket_label, quality, ...}}``), in memory
    only; ``ciks`` None returns every kept result."""
    with _MODELS_LOCK:
        if ciks is None:
            return {k: dict(v) for k, v in _MODELS.items()}
        out = {}
        for c in ciks:
            try:
                k = int(c)
            except (TypeError, ValueError):
                continue
            if k in _MODELS:
                out[k] = dict(_MODELS[k])
        return out


def _universe(ce, conn) -> Tuple[str, Optional[str]]:
    """``(state, words)`` of the reference-entity list (C-ENT ``universe_status``: ready | refreshing | loading | empty | offline | error)."""
    fn = getattr(ce, "universe_status", None)
    if not callable(fn):
        return "ready", None
    try:
        u = fn(conn) or {}
    except Exception:   # noqa: BLE001
        return "ready", None
    state = str(u.get("state") or "").lower()
    if not state:
        state = "empty" if (not u.get("refreshed_ts") and not u.get("listed")) else "ready"
    return state, (u.get("message") if isinstance(u.get("message"), str) else None)


def _universe_loading(ce, conn) -> bool:
    """True while the reference-entity list is loading or not loaded yet (C-ENT ``universe_status`` state loading / empty)."""
    return _universe(ce, conn)[0] in ("loading", "empty")


def _unknown_words(ce, conn, key) -> str:
    """ENTITY_UNKNOWN in words: the list's own state when it is not loaded (offline, an error), else the name is not in it."""
    state, words = _universe(ce, conn)
    if state in ("offline", "error") and words:
        return words
    return f"no SEC registrant {key} is in the reference-entity list"


def model_spread(services, cik: int, maturity: date, valuation_date: Optional[date] = None, conn=None, now: Optional[int] = None,
                 marks=None) -> dict:
    """C-MODEL (plan 6.4): the MODEL spread of a reference entity for a maturity - live (``valuation_date`` None: the live mark, the latest
    volatility, today's ladder and curve) or as of a VM date (every input that of R, the equity's last trading day on or before it;
    nothing fetched).  ``ok`` True: the model dict (``spread_bp`` = ``floor_bp`` + ``raw_bp``, ``bucket``, ``quality``, ``dealable``,
    ``inputs``, ``intermediate``, ``underlying`` / ``proxy`` / ``curve_as_of`` for ``swaps._input_stamp``).  ``ok`` False: ``{code,
    reason}`` (plan 3.6), INPUTS_PENDING with ``retry_ms`` (a fetch is queued), or for a VM ``vm_outcome`` CLOSE_PENDING / CAUGHT_UP with
    ``close_date``, ``quiet``, ``quiet_until`` and ``note``.  ``marks``: the caller's snapshot for S (else ``services.marks``).  Never
    network I/O; never raises on missing data."""
    ts = _now(services, now)
    today = ny_of(ts).date()
    try:
        cik = int(cik)
    except (TypeError, ValueError):
        return _fail("ENTITY_UNKNOWN", f"{cik!r} is not an SEC CIK")
    try:
        maturity = _date(maturity)
    except (TypeError, ValueError):
        return _fail("MODEL_DOMAIN", f"the maturity {maturity!r} is not a date", cik=cik)
    ce = getattr(services, "credit_entities", None)
    if ce is None or not callable(getattr(ce, "entity", None)):
        return _fail("ENTITY_UNKNOWN", "the reference-entity list (SEC EDGAR) is not available in this version: no single-name CDS model", cik=cik)
    st = _state(services)
    snap = marks if marks is not None else getattr(services, "marks", None)
    with _marks.shared_reader(services.db, conn) as c:
        ent = ce.entity(c, cik)
        if ent is None:
            if valuation_date is None and _universe_loading(ce, c):
                return _pending("loading the reference-entity list from SEC EDGAR (first run, about 2-3 minutes)", cik=cik)
            return _fail("ENTITY_UNKNOWN", _unknown_words(ce, c, f"with CIK {cik}"), cik=cik)
        ent = dict(ent)
        ent.setdefault("cik", cik)
        if valuation_date is not None:
            return _model_vm(services, st, c, ce, ent, maturity, _date(valuation_date), ts, today, snap)
        out = _model_live(services, st, c, ce, ent, maturity, ts, today, snap)
    try:
        if out.get("code") != "INPUTS_PENDING" and maturity == maturity_for(services.cal, today, DEFAULT_TENOR):
            _remember(dict(out, cik=cik), ts)
    except Exception:   # noqa: BLE001 - the desk list's memory is a convenience
        pass
    return out


def _model_live(services, st: _State, c, ce, ent: dict, maturity: date, ts: int, today: date, snap) -> dict:
    cik, ticker = int(ent["cik"]), str(ent.get("ticker") or "")
    name = ent.get("name") or ticker
    who = {"cik": cik, "ticker": ticker, "name": name}
    offered = _coverage(ent)
    f = ent.get("fundamentals") if isinstance(ent.get("fundamentals"), dict) else None
    if f is None and callable(getattr(ce, "fundamentals_now", None)):
        f = ce.fundamentals_now(c, cik)
    hard = _structural(f, who)
    if hard is not None:
        if offered[1] in ("NO_DEBT", "NO_SHARES", "FOREIGN_FILER") and offered[2]:
            hard.update(reason=offered[2], message=offered[2], status_reason=offered[1])
        return hard
    lad = ladder(services, today=today)
    if not lad["ok"]:
        return _fail(lad["code"], lad["reason"], ladder=lad, **who)
    checked = ent.get("checked_ts")
    stale_check = checked in (None, "") or ts - int(checked) >= ENTITY_CHECK_S
    iid = ent.get("instrument_id")
    if not iid:
        r = request_inputs(services, cik, name=name, now=ts)
        if r["status"] == "error":
            return _fail(r.get("code") if r.get("code") in ("NO_PRICE", "SHORT_HISTORY", "NO_VOL") else "NO_PRICE", f"{ticker}: {r['message']}", **who)
        return _pending(f"looking up {ticker} on the quote provider and fetching {name}'s SEC filings and 1,000 daily closes (first time today only)", **who)
    inst = None
    master = getattr(services, "instruments", None)
    if master is not None:
        try:
            inst = master.get(iid, c)
        except Exception:   # noqa: BLE001
            inst = None
    group = getattr(inst, "session_group", None) or DEFAULT_GROUP
    _subscribe(services, st, iid, ts)
    price = _live_price(services, st, snap, iid, group, ts, who)
    if not price.get("ok"):
        return price
    s_session = price["session"]
    last_complete = last_complete_session(services, group, ts) or s_session
    row = _vol_row(c, ticker, upto=last_complete)
    with st.lock:
        fetched = dict(st.fetched.get(ticker) or {})
    events_ok = (fetched.get("events_through") or "") >= s_session.isoformat()
    if row is None or not events_ok:
        r = request_inputs(services, cik, name=name, now=ts, events_through=s_session)
        if r["status"] == "queued":
            return _pending(r["message"], **who)
        if r["status"] == "error":
            code = r.get("code") if r.get("code") in ("SHORT_HISTORY", "NO_VOL", "NO_PRICE") else "NO_VOL"
            return _fail(code, f"{name} ({ticker}): {r['message']}", **who)
        if row is None:
            if fetched.get("short") is not None:
                return _fail("SHORT_HISTORY", f"{name} ({ticker}): fewer than 250 daily closes (listed since {fetched.get('first_bar')})", **who)
            return _fail("NO_VOL", f"{name} ({ticker}): no 1000-day volatility from the daily closes fetched {ny_str(int(fetched.get('ts') or ts))}", **who)
        return _pending(f"waiting for the {s_session.isoformat()} daily bar of {ticker} (the split check of the session)", **who)
    age = _bd(services, _date(row["vol_date"]), last_complete)
    if age > VOL_MAX_AGE_BD:
        r = request_inputs(services, cik, force=False, name=name, now=ts)
        if r["status"] == "queued":
            return _pending(r["message"], **who)
        return _fail("NO_VOL", f"{name} ({ticker}): the latest 1000-day volatility is of {row['vol_date']}, {age} business days before the last complete"
                     f" session {last_complete.isoformat()} (a new fetch is queued)", **who)
    notes: List[str] = []
    if age > 0:
        notes.append(f"volatility as of {row['vol_date']}, {age} business day{'s' if age != 1 else ''} before the last complete session")
        request_inputs(services, cik, force=False, name=name, now=ts)
    elif stale_check:
        request_inputs(services, cik, force=False, name=name, now=ts)
    row["age_bd"] = age
    shares_end = _date(f.get("shares_end") or f.get("end_date"))
    splits = _splits(c, ticker, shares_end, s_session)
    cur, bad = _curve(services, maturity, today, None)
    if bad is not None:
        return _fail(bad["code"], bad["reason"], **who)
    underlying = {"instrument_id": iid, "quote_time": price["info"].get("quote_time"), "price": price["info"]["price"], "quality": price["info"]["quality"]}
    return _assemble(services, ent, f, maturity, today, price["S"], price["info"], row, splits, lad, cur, ts, False, underlying, notes, offered)


def _vm_wait(services, code: str, reason: str, d: date, R: date, **extra) -> dict:
    cal = getattr(services, "cal", None)
    quiet = R != d or (cal is not None and not cal.is_business_day(d))
    nxt = cal.next_business_day(R) if cal is not None else R + timedelta(days=1)
    return _fail(code, reason, vm_outcome=code, close_date=R.isoformat(), note=reason, quiet=bool(quiet), quiet_until=epoch_of(nxt, "12:00"), **extra)


def _model_vm(services, st: _State, c, ce, ent: dict, maturity: date, d: date, ts: int, today: date, snap) -> dict:
    cik, ticker = int(ent["cik"]), str(ent.get("ticker") or "")
    name = ent.get("name") or ticker
    who = {"cik": cik, "ticker": ticker, "name": name}
    iid = ent.get("instrument_id")
    master = getattr(services, "instruments", None)
    inst = None
    if iid and master is not None:
        try:
            inst = master.get(iid, c)
        except Exception:   # noqa: BLE001
            inst = None
    if not iid or inst is None:
        request_inputs(services, cik, name=name, now=ts, force=False)
        return _vm_wait(services, "CLOSE_PENDING", f"{ticker} is not in the instrument master yet (being looked up): its close is unknown", d, d, **who)
    from . import swaps_trs
    try:
        snap = _frozen_marks(snap, iid)     # B4-R1: close_on and the CAUGHT_UP check read mark(); the live MarkProvider has none
    except Exception:   # noqa: BLE001 - a failing snapshot: the recorded CLOSE row still values the VM, else CLOSE_PENDING
        snap = None
    view = None
    try:
        view = snap.mark(iid) if snap is not None else None
    except Exception:   # noqa: BLE001
        view = None
    px, pinfo = swaps_trs._vm_close(services, iid, inst.session_group, d, lambda r: swaps_trs.close_on(services, c, snap, inst, r), view)
    if px is None:
        out = _fail(str(pinfo.get("vm_outcome") or "CLOSE_PENDING"), str(pinfo.get("note") or "the close is not known yet"), **who)
        out.update({k: pinfo[k] for k in ("vm_outcome", "close_date", "quiet", "quiet_until") if k in pinfo})
        out.setdefault("vm_outcome", out["code"])
        out["note"] = out["reason"]
        return out
    R = _date(pinfo.get("close_date") or d)
    lad = ladder(services, on=R, today=today)
    if not lad["ok"]:
        if lad["code"] in ("CLOSE_PENDING", "CAUGHT_UP"):
            return _vm_wait(services, lad["code"], lad["reason"], d, R, **who)
        return _fail(lad["code"], lad["reason"], **who)
    row = _vol_row(c, ticker, vol_date=R)
    if row is None:
        request_inputs(services, cik, since=R, force=False, name=name, now=ts)
        with st.lock:
            no_bar = R.isoformat() in st.no_bar.get(ticker, set())
        if no_bar and _has_later_vol(c, ticker, R):
            return _vm_wait(services, "CAUGHT_UP", f"the Yahoo daily history of {ticker} has no close of {R.isoformat()} and a later session's volatility exists",
                            d, R, **who)
        return _vm_wait(services, "CLOSE_PENDING", f"the 1000-day volatility of {ticker} as of {R.isoformat()} is not recorded yet (its daily closes are being"
                        " fetched)", d, R, **who)
    row["age_bd"] = 0
    f = ce.fundamentals_on(c, cik, R) if callable(getattr(ce, "fundamentals_on", None)) else None
    hard = _structural(f, who)
    if hard is not None:
        hard["reason"] = hard["message"] = f"{hard['reason']} (as known on {R.isoformat()})"
        return hard
    shares_end = _date(f.get("shares_end") or f.get("end_date"))
    splits = _splits(c, ticker, shares_end, R)
    cur, bad = _curve(services, maturity, R, R)
    if bad is not None:
        if bad["code"] in ("CLOSE_PENDING", "CAUGHT_UP"):
            return _vm_wait(services, bad["code"], bad["reason"], d, R, **who)
        return _fail(bad["code"], bad["reason"], **who)
    s_info = {"price": str(qprice(px)), "quality": "close", "source": pinfo.get("source"), "quote_time": pinfo.get("quote_time"), "close_date": R.isoformat(),
              "basis": pinfo.get("basis") or "the recorded close"}
    underlying = {"instrument_id": iid, "close_date": R.isoformat(), "price": s_info["price"], "basis": s_info["basis"]}
    return _assemble(services, ent, f, maturity, R, qprice(px), s_info, row, splits, lad, cur, ts, True, underlying, [], _coverage(ent))


# ---- the fetch (worker threads only: the network) ----------------------------------------------------------------------------------------
def _record(services, ticker: str, rows: List[dict], splits: List[dict]) -> int:
    """Write the new volatility rows and split events through the light ``cds.vol_record`` (splits first; at most 500 rows a command)."""
    bus = getattr(services, "bus", None)
    if bus is None or (not rows and not splits):
        return 0
    items = [("splits", s) for s in splits] + [("vol", r) for r in rows]
    n = 0
    for k in range(0, len(items), MAX_ROWS):
        chunk = items[k:k + MAX_ROWS]
        payload = {"ticker": ticker, "splits": [x for kind, x in chunk if kind == "splits"], "vol": [x for kind, x in chunk if kind == "vol"]}
        res = bus.execute(Command("cds.vol_record", payload, f"cds:vol:{_id_safe(ticker)}:{_sha12(payload)}", "worker"))
        if not res.ok:
            err = (res.body or {}).get("error") or {}
            raise RuntimeError(f"cds.vol_record failed: {err.get('code')} {err.get('message')}")
        n += int((res.body or {}).get("vol_rows") or 0) + int((res.body or {}).get("split_rows") or 0)
    return n


def fetch_vol(services, ticker: str, symbol: Optional[str] = None, since=None, now: Optional[int] = None, group: str = DEFAULT_GROUP,
              force: bool = True) -> dict:
    """NETWORK - the worker's threads only.  One Yahoo request for the 5-year daily closes and split events of ``symbol`` (the provider's
    ``_chart`` live; ``daily`` / ``events`` when the providers are offline or have no ``_chart``); the ``cds_vol`` row of every complete
    session from ``since`` (default: the last 2 sessions) not yet stored, and the new or changed split events, in one ``cds.vol_record``.
    Skips the request when every wanted session is stored and ``force`` is False.  ``{status: ok|skipped|error, code?, message, rows,
    splits, events_through, since_done}``; a provider failure raises its FetchError."""
    ts = _now(services, now)
    st = _state(services)
    ticker = str(ticker or "").strip().upper()
    symbol = str(symbol or ticker).strip()
    last = last_complete_session(services, group, ts)
    if last is None:
        return {"status": "error", "code": "NO_VOL", "message": f"no complete {group} session in the last 10 days"}
    start = _date(since) if since else (_last_session(services, group, last - timedelta(days=1)) or (last, 0))[0]
    start = min(start, last)
    sessions = _sessions_between(services, group, start, last)
    with _marks.shared_reader(services.db) as c:
        stored = {r[0] for r in c.execute("SELECT vol_date FROM cds_vol WHERE ticker=? AND vol_date>=? AND vol_date<=?",
                                          (ticker, start.isoformat(), last.isoformat())).fetchall()}
        old_splits = {r[0]: (str(r[1]), str(r[2])) for r in c.execute("SELECT split_date, numerator, denominator FROM cds_splits WHERE ticker=?",
                                                                      (ticker,)).fetchall()}
    targets = [x for x in sessions if x.isoformat() not in stored]
    if not targets and not force:
        return {"status": "skipped", "message": "every wanted session is already stored", "rows": 0, "splits": 0, "since_done": start.isoformat()}
    providers = getattr(services, "providers", None)
    yahoo = getattr(providers, "yahoo", None) if providers is not None else None
    if yahoo is None:
        return {"status": "error", "code": "NO_VOL", "message": "no quote provider is bound: the daily closes cannot be fetched"}
    if getattr(providers, "offline", None) is not True and hasattr(yahoo, "_chart"):
        from ..quotes.parsers import parse_daily, parse_events
        res = yahoo._chart(symbol, CHART_QUERY)
        bars, events = parse_daily(res), parse_events(res)
        source = f"Yahoo daily closes ({symbol}, 5 years, split-adjusted)"
    else:
        bars = list(yahoo.daily(symbol, days=VOL_WINDOW + 1 + len(sessions)))
        events = yahoo.events(symbol)
        source = f"daily closes ({symbol}) of the offline provider"
    closes: Dict[date, Decimal] = {}
    for b in bars:
        try:
            v = Decimal(str(b.c))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if v.is_finite() and v > 0:
            closes[b.date] = v
    if len(closes) < 2:
        return {"status": "error", "code": "NO_VOL", "message": f"no daily closes for {symbol} from the quote provider"}
    dates = sorted(closes)
    vals = [closes[x] for x in dates]
    idx = {x: i for i, x in enumerate(dates)}
    first_bar, last_bar = dates[0], dates[-1]
    rets: Optional[List[Decimal]] = None
    rows: List[dict] = []
    short = None
    missing: set = set()
    for R in targets:
        i = idx.get(R)
        if i is None:
            missing.add(R.isoformat())
            continue
        lo = max(0, i - VOL_WINDOW)
        n = i - lo
        if n < VOL_MIN:
            short = n
            continue
        if rets is None:
            rets = [(vals[k] / vals[k - 1]).ln() for k in range(1, len(vals))]
        rows.append({"vol_date": R.isoformat(), "sigma": str(_vol_of_returns(rets[lo:i])), "n_returns": n, "first_date": dates[lo].isoformat(),
                     "last_close": str(qprice(vals[i])), "events_through": last_bar.isoformat(), "source": source})
    splits = []
    for sp in getattr(events, "splits", None) or []:
        key, val = sp.date.isoformat(), (str(int(sp.numerator)), str(int(sp.denominator)))
        if old_splits.get(key) != val:
            splits.append({"split_date": key, "numerator": val[0], "denominator": val[1], "source": f"Yahoo split events ({symbol})"})
    _record(services, ticker, rows, splits)
    with st.lock:
        st.no_bar.setdefault(ticker, set()).update(missing)
        st.fetched[ticker] = {"ts": ts, "events_through": last_bar.isoformat(), "first_bar": first_bar.isoformat(), "last_bar": last_bar.isoformat(),
                              "since_done": start.isoformat(), "source": source, "short": short, "rows": len(rows)}
    if short is not None and not rows and not stored:
        return {"status": "error", "code": "SHORT_HISTORY", "message": f"fewer than 250 daily closes (listed since {first_bar.isoformat()})",
                "rows": 0, "splits": len(splits), "events_through": last_bar.isoformat(), "since_done": start.isoformat()}
    return {"status": "ok", "message": f"{len(rows)} volatility row(s), {len(splits)} split event(s) from {source}", "rows": len(rows), "splits": len(splits),
            "events_through": last_bar.isoformat(), "since_done": start.isoformat(), "no_close": sorted(missing)}


def _drain_one(services, st: _State, e: dict, ts: int) -> str:
    """One queued name: the SEC check when due, the ticker's instrument (a resolve), then the volatility fetch."""
    cik = int(e["cik"])
    ce = getattr(services, "credit_entities", None)
    if ce is None or not callable(getattr(ce, "entity", None)):
        _finish(st, cik, "error", ts, code="ENTITY_UNKNOWN", message="the reference-entity list (SEC EDGAR) is not available in this version")
        return f"{cik}: no entity list"
    with _marks.shared_reader(services.db) as c:
        ent = ce.entity(c, cik)
    if ent is None:
        _finish(st, cik, "error", ts, code="ENTITY_UNKNOWN", message=f"no SEC registrant with CIK {cik} is in the reference-entity list")
        return f"{cik}: unknown"
    ent = dict(ent)
    ticker, name = str(ent.get("ticker") or ""), ent.get("name") or ent.get("ticker")
    notes = []
    checked = ent.get("checked_ts")
    if (checked in (None, "") or ts - int(checked) >= ENTITY_CHECK_S) and callable(getattr(ce, "refresh_entity", None)):
        try:
            ce.refresh_entity(services, cik)
            with _marks.shared_reader(services.db) as c:
                ent = dict(ce.entity(c, cik) or ent)
        except Exception as ex:   # noqa: BLE001 - the SEC check is noted; the daily closes are fetched anyway
            notes.append(f"the SEC filings check failed ({type(ex).__name__}: {str(ex)[:120]})")
    iid, how = ent.get("instrument_id"), "ok"
    fn = getattr(ce, "instrument_for", None)
    if callable(fn):
        try:
            iid, how = fn(services, ent)
        except Exception as ex:   # noqa: BLE001
            iid, how = None, f"error: {type(ex).__name__}: {ex}"
    if not iid:
        if str(how) == "resolving" and int(e.get("tries") or 0) < RESOLVE_TRIES:
            with st.lock:
                cur = st.entries.get(cik)
                if cur is not None:
                    cur.update(status="queued", not_before=ts + INPUTS_INTERVAL_S, tries=int(e.get("tries") or 0) + 1,
                               message=f"looking up {ticker} on the quote provider (first time only)")
            return f"{ticker}: resolving"
        _finish(st, cik, "error", ts, code="NO_PRICE", message=f"{ticker} could not be found on the quote provider ({how}): no share price")
        return f"{ticker}: not found"
    inst = None
    master = getattr(services, "instruments", None)
    if master is not None:
        try:
            inst = master.get(iid)
        except Exception:   # noqa: BLE001
            inst = None
    symbol = getattr(inst, "yahoo_symbol", None) or getattr(inst, "symbol", None) or iid
    group = getattr(inst, "session_group", None) or DEFAULT_GROUP
    try:
        res = fetch_vol(services, ticker, symbol, since=e.get("since"), now=ts, group=group, force=bool(e.get("force")))
    except Exception as ex:   # noqa: BLE001 - a FetchError (its status kept in the words) or a write failure: the entry carries it 10 minutes
        status = getattr(ex, "status", None)
        msg = f"the daily closes of {symbol} could not be fetched ({str(ex)[:160]}{'' if status is None else ''})"
        _finish(st, cik, "error", ts, code="NO_VOL", message=msg)
        _log(services, "warning", "cds_inputs %s: %s", ticker, msg)
        return f"{ticker}: error"
    if res["status"] == "error":
        _finish(st, cik, "error", ts, code=res.get("code") or "NO_VOL", message=res.get("message"))
        return f"{ticker}: {res.get('code')}"
    _finish(st, cik, "ready", ts, since_done=res.get("since_done"), events_through=res.get("events_through") or (st.fetched.get(ticker) or {}).get("events_through"),
            message=None, code=None, notes=notes, last_result=res.get("message"))
    return f"{ticker}: {res['status']}"


def _open_ciks(services) -> Dict[int, Tuple[str, Optional[str]]]:
    """``{cik: (earliest trade date, name)}`` of the OPEN single-name CDS contracts."""
    out: Dict[int, Tuple[str, Optional[str]]] = {}
    db = getattr(services, "db", None)
    if db is None:
        return out
    try:
        with _marks.shared_reader(db) as c:
            rows = c.execute("SELECT terms_json, start_date FROM otc_contracts WHERE kind='CDS' AND status='OPEN'").fetchall()
    except Exception:   # noqa: BLE001 - a database without the table / kind has no CDS contracts
        return out
    for r in rows:
        try:
            t = json.loads(r["terms_json"] or "{}")
            k = int(t.get("cik"))
        except (ValueError, TypeError):
            continue
        td = str(t.get("trade_date") or r["start_date"] or "")[:10]
        prev = out.get(k)
        out[k] = (min(prev[0], td) if prev and prev[0] else td, t.get("entity_name") or t.get("ticker"))
    return out


def _events_pass(services, st: _State, ts: int) -> int:
    """Once per session after 09:45 ET: every open contract's equity is queued for its split events."""
    ny = ny_of(ts)
    today = ny.date()
    if st.events_day == today.isoformat():
        return 0
    cal = getattr(services, "cal", None)
    if cal is None or not cal.is_business_day(today) or ny.time() < EVENTS_PASS_ET:
        return 0
    st.events_day = today.isoformat()
    n = 0
    for k, (_td, name) in _open_ciks(services).items():
        request_inputs(services, k, force=True, name=name, now=ts, events_through=today)
        n += 1
    return n


def refresh_inputs(services, now: Optional[int] = None):
    """The ``cds_inputs`` refresher (every 5 s on the worker): the 09:45 ET split pass, then up to 5 queued names.  Returns at once
    (no network) when the queue is empty or the providers are offline."""
    providers = getattr(services, "providers", None)
    if providers is None or getattr(providers, "offline", False) is True:
        return None
    ts = _now(services, now)
    st = _state(services)
    try:
        _events_pass(services, st, ts)
    except Exception as ex:   # noqa: BLE001 - the pass is retried next session; the queue still drains
        _log(services, "warning", "cds_inputs: the 09:45 split pass failed: %s", ex)
    taken = _take(st, DRAIN_PER_RUN, ts)
    if not taken:
        return None
    done = []
    for e in taken:
        try:
            done.append(_drain_one(services, st, e, ts))
        except Exception as ex:   # noqa: BLE001 - one name's failure never blocks the others
            _finish(st, int(e["cik"]), "error", ts, code="NO_VOL", message=f"the inputs could not be fetched ({type(ex).__name__}: {str(ex)[:160]})")
            _log(services, "warning", "cds_inputs CIK %s failed: %s", e.get("cik"), ex)
            done.append(f"{e.get('cik')}: error")
    return "; ".join(done)[:200]


def refresh_eod(services, now: Optional[int] = None):
    """The ``cds_eod`` refresher (from 16:45 ET): every reference equity of an OPEN CDS contract is queued from its trade date (the
    back-fill); ``cds_inputs`` fetches only the sessions not yet stored."""
    providers = getattr(services, "providers", None)
    if providers is None or getattr(providers, "offline", False) is True:
        return None
    ts = _now(services, now)
    n = 0
    for k, (td, name) in _open_ciks(services).items():
        request_inputs(services, k, since=td or None, force=False, name=name, now=ts)
        n += 1
    return f"{n} reference equities queued" if n else None


# B3-R2 (integration): an idle run (empty queue, nothing open, offline) made no request: the worker does not count it in Data Status'
# "requests today" (cds_inputs runs every 5 s: about 17,000 idle runs a day would read as requests)
refresh_inputs.count_idle_runs = False
refresh_eod.count_idle_runs = False


# ---- the light command ------------------------------------------------------------------------------------------------------------------
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")


def _iso(x, field: str) -> str:
    try:
        return date.fromisoformat(str(x)[:10]).isoformat()
    except (TypeError, ValueError) as e:
        raise RejectError("TERMS", f"{field} must be an ISO date, got {x!r}", {"field": field}) from e


def _pos(x, field: str, allow_zero: bool = False) -> Decimal:
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise RejectError("TERMS", f"{field} must be a decimal string, got {x!r}", {"field": field}) from e
    if not d.is_finite() or d < 0 or (d == 0 and not allow_zero):
        raise RejectError("TERMS", f"{field} must be a positive number, got {x!r}", {"field": field})
    return d


def h_vol_record(ctx, payload: dict) -> dict:
    """``cds.vol_record {ticker, vol: [{vol_date, sigma, n_returns, first_date, last_close, events_through, source}], splits: [{split_date,
    numerator, denominator, source}]}`` (light; the worker's writes): a volatility row is written once (a stored row of a session is never
    changed, so a VM of a past date is reproducible); a split event is inserted or replaced when its ratio changed.  At most 500 rows."""
    ticker = str(payload.get("ticker") or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise RejectError("TERMS", f"ticker must be a listed symbol, got {payload.get('ticker')!r}", {"field": "ticker"})
    vol, splits = payload.get("vol") or [], payload.get("splits") or []
    if not isinstance(vol, list) or not isinstance(splits, list):
        raise RejectError("TERMS", "vol and splits must be lists", {"field": "vol"})
    if len(vol) + len(splits) > MAX_ROWS:
        raise RejectError("TOO_MANY_ROWS", f"at most {MAX_ROWS} rows a command, got {len(vol) + len(splits)}")
    nv = ns = 0
    for r in vol:
        if not isinstance(r, dict):
            raise RejectError("TERMS", "a volatility row must be an object", {"field": "vol"})
        vd, fd, et = _iso(r.get("vol_date"), "vol_date"), _iso(r.get("first_date"), "first_date"), _iso(r.get("events_through"), "events_through")
        sig, lc = _pos(r.get("sigma"), "sigma", allow_zero=True), _pos(r.get("last_close"), "last_close")
        n = _int(r.get("n_returns"))
        if n is None or n < 2:
            raise RejectError("TERMS", f"n_returns must be at least 2, got {r.get('n_returns')!r}", {"field": "n_returns"})
        if et < vd or fd > vd:
            raise RejectError("TERMS", f"the volatility row of {vd} must have first_date <= vol_date <= events_through", {"field": "events_through"})
        cur = ctx.conn.execute("INSERT OR IGNORE INTO cds_vol(ticker, vol_date, sigma, n_returns, first_date, last_close, events_through, source, fetched_ts)"
                               " VALUES (?,?,?,?,?,?,?,?,?)", (ticker, vd, str(sig), n, fd, str(lc), et, str(r.get("source") or "")[:200], int(ctx.ts)))
        nv += max(0, cur.rowcount)
    for s in splits:
        if not isinstance(s, dict):
            raise RejectError("TERMS", "a split event must be an object", {"field": "splits"})
        sd = _iso(s.get("split_date"), "split_date")
        num, den = _int(s.get("numerator")), _int(s.get("denominator"))
        if num is None or den is None or num <= 0 or den <= 0:
            raise RejectError("TERMS", f"the split of {sd} needs a positive numerator and denominator", {"field": "splits"})
        cur = ctx.conn.execute("INSERT INTO cds_splits(ticker, split_date, numerator, denominator, source, fetched_ts) VALUES (?,?,?,?,?,?)"
                               " ON CONFLICT(ticker, split_date) DO UPDATE SET numerator=excluded.numerator, denominator=excluded.denominator,"
                               " source=excluded.source, fetched_ts=excluded.fetched_ts"
                               " WHERE cds_splits.numerator<>excluded.numerator OR cds_splits.denominator<>excluded.denominator",
                               (ticker, sd, str(num), str(den), str(s.get("source") or "")[:200], int(ctx.ts)))
        ns += max(0, cur.rowcount)
    return {"ticker": ticker, "vol_rows": nv, "split_rows": ns}


# ---- route, registry, binding ---------------------------------------------------------------------------------------------------------
def register_routes(router) -> None:
    """``GET /api/otc/cds/model?cik=|ticker=&tenor_years=5`` (plan 6.5): 200 the model dict; a refusal 200 ``{refused: true, code, message,
    blockers}`` (words first); 202 ``{status: "fetching", retry_ms, message}`` while an input is fetched; 404 ENTITY_UNKNOWN."""
    from ..api import ApiError

    def cds_model(req):
        rt = req.rt
        key = str(req.q("cik") or req.q("ticker") or "").strip()
        if not key:
            raise ApiError(400, "BAD_REQUEST", "a company is required: cik or ticker")
        raw = str(req.q("tenor_years") or DEFAULT_TENOR).strip().upper().rstrip("Y")
        try:
            n = int(raw)
        except ValueError:
            n = -1
        if n not in TENORS:
            raise ApiError(400, "TERMS", f"the tenor must be 1, 2, 3, 4, 5, 7 or 10 years, got {req.q('tenor_years')!r}", {"field": "tenor_years"})
        ce = getattr(rt, "credit_entities", None)
        if ce is None or not callable(getattr(ce, "entity", None)):
            return {"ok": False, "refused": True, "bookable": False, "code": "ENTITY_UNKNOWN", "label": LABEL,
                    "message": "the reference-entity list (SEC EDGAR) is not available in this version",
                    "blockers": [{"code": "ENTITY_UNKNOWN", "message": "the reference-entity list (SEC EDGAR) is not available in this version"}]}
        with _marks.shared_reader(rt.db) as conn:
            ent = ce.entity(conn, int(key) if key.isdigit() else key.upper())
            loading = ent is None and _universe_loading(ce, conn)
            unknown = _unknown_words(ce, conn, key) if ent is None and not loading else None
        if ent is None:
            if loading:
                return 202, {"status": "fetching", "retry_ms": RETRY_MS, "code": "INPUTS_PENDING",
                             "message": "loading the reference-entity list from SEC EDGAR (first run, about 2-3 minutes)"}
            raise ApiError(404, "ENTITY_UNKNOWN", unknown, {"key": key})
        today = ny_of(req.now).date()
        maturity = maturity_for(rt.cal, today, n)
        m = model_spread(rt, int(ent["cik"]), maturity, now=req.now)
        if m.get("ok"):
            return dict(m, tenor_years=n)
        if m.get("code") == "INPUTS_PENDING":
            return 202, {"status": "fetching", "retry_ms": int(m.get("retry_ms") or RETRY_MS), "code": "INPUTS_PENDING", "message": m.get("reason"),
                         "cik": m.get("cik"), "ticker": m.get("ticker"), "name": m.get("name")}
        if m.get("code") == "ENTITY_UNKNOWN":
            raise ApiError(404, "ENTITY_UNKNOWN", str(m.get("reason")), {"key": key})
        words = str(m.get("reason") or m.get("code"))
        return {"ok": False, "refused": True, "bookable": False, "label": LABEL, "code": m.get("code"), "message": words,
                "details": {k: v for k, v in m.items() if k not in ("ok", "code", "reason", "message", "label")},
                "blockers": [{"code": m.get("code"), "message": words}], "tenor_years": n, "maturity_date": maturity.isoformat(),
                "cik": m.get("cik"), "ticker": m.get("ticker"), "name": m.get("name")}

    router.add("GET", r"/api/otc/cds/model", cds_model)


def register(bus, services=None) -> None:
    bus.register("cds.vol_record", h_vol_record, light=True)


def bind(rt) -> None:
    """The ``cds_vol`` / ``cds_splits`` tables (``ensure_schema_ext``, additive) and the two refreshers on the quote worker (C14)."""
    db = getattr(rt, "db", None)
    if db is not None and hasattr(db, "ensure_schema_ext"):
        db.ensure_schema_ext("cds_model", SCHEMA)
    try:
        from ..quotes.worker import register_refresher
    except ImportError:
        register_refresher = None
    if register_refresher is not None:
        register_refresher("cds_inputs", INPUTS_INTERVAL_S, refresh_inputs, label=REFRESHER_LABEL)
        register_refresher("cds_eod", EOD_INTERVAL_S, refresh_eod, run_after_et=EOD_ET, label=REFRESHER_LABEL)


__all__ = ["LABEL", "MODEL_TEXT", "MODEL_VERSION", "VALIDATION_NOTE", "FLOOR_SERIES", "LADDER", "LADDER_SERIES", "SCHEMA", "ShortHistory", "cg_spread",
           "realised_vol", "bucket_of", "bucket_label", "spread_at", "maturity_for", "ladder", "model_spread", "request_inputs", "queue_status", "last_models",
           "last_complete_session", "fetch_vol", "refresh_inputs", "refresh_eod", "h_vol_record", "register_routes", "register", "bind"]
