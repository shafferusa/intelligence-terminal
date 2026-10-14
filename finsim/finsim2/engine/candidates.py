"""Candidate signal families for the Shaffer Score: new, economically different information (carry, the yield curve,
term structure, inflation, currencies, commodities, option pricing).

They are computed point in time like every feature (macro series by publication date, dividends by ex-date, prices
up to t) and run through the same evidence machinery as the production signals, but in SHADOW: they do not enter the
production Shaffer Score until the admission test in the audit (`finsim2.engine.audit`, section "Candidate
families") shows, out of sample, that a family adds incremental information at a horizon and does not worsen the
score's calibration. Admission is recorded in `shaffer_score.ADMITTED`.

They are deliberately kept out of `features.FEATURES`, so the ML models' feature set is unchanged.

Research phase 2 added Earnings Surprise (SUE from first-reported SEC filings) and Breadth (the cross-section of the
store's equities).

What could not be built from the data FinSim2 can reach (and is therefore not here): option skew (no chains),
futures curves beyond the front month (term structure is proxied by fund-versus-front-month roll yield), foreign
inflation and growth (FX differentials use policy rates only), commodity inventories, CFTC positioning (the CFTC is
not an allowed source), short interest (FINRA's daily short-sale volume files start in 2019 — too late for the
pre-2018 admission test), consensus estimates, revisions and forward valuation (no point-in-time estimate history;
the Alpha Vantage free tier allows 25 requests a day), fund flows.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
from typing import Dict, List, Optional

Series = List[Optional[float]]

# name -> (family, label, description)
CANDIDATE_FEATURES: Dict[str, tuple] = {
    # Carry: what the position earns if prices do not move
    "carry": ("Carry", "Carry over cash", "equities and funds: trailing 12-month distributions ÷ price − 3-month bill; "
              "constant-maturity bonds: yield at the duration − bill; currency pairs: base-currency short rate − quote-currency rate"),
    "div_yield": ("Carry", "Dividend yield", "trailing 12-month dividends (by ex-date) ÷ price; indices use their tracking fund"),
    # Yield curve: shape beyond the 10Y − 3M slope already in Rates
    "curvature": ("Yield Curve", "Curve curvature (2·5Y − 2Y − 10Y)", "Treasury butterfly: positive = a humped belly"),
    "d_curvature_3m": ("Yield Curve", "Curvature change, 3 months", "change in 2·5Y − 2Y − 10Y over 63 sessions"),
    "slope_5s30s": ("Yield Curve", "Long-end slope (30Y − 5Y)", "FRED DGS30 − DGS5"),
    "d_slope_2s10s_1m": ("Yield Curve", "2s10s change, 1 month", "change in 10Y − 2Y over 21 sessions"),
    "roll_down": ("Yield Curve", "Roll-down", "bonds: (yield at D − yield at D − 1 year) × D on today's curve, the price gain from ageing one year"),
    # Term structure
    "vix_term": ("Term Structure", "VIX term structure (VIX ÷ VIX3M − 1)", "above 0 = backwardation (near-term stress priced above 3-month)"),
    "roll_yield": ("Term Structure", "Futures roll yield", "commodity fund's 1-year return − the front-month futures' price change − bill: "
                   "negative in contango, positive in backwardation (WTI/USO, NATGAS/UNG, COPPER/CPER)"),
    "d_roll_yield_3m": ("Term Structure", "Roll-yield change, 3 months", "change in the roll yield over 63 sessions"),
    # Inflation as its own family
    "infl_accel": ("Inflation", "Inflation acceleration", "CPI 3-month annualised − 12-month (first releases, publication dates)"),
    "d_breakeven_3m": ("Inflation", "Breakeven change, 3 months", "change in the 5-year breakeven (T5YIE) over 63 sessions"),
    "d_real_y10_3m": ("Inflation", "Real-yield change, 3 months", "change in the 10-year TIPS yield (DFII10) over 63 sessions"),
    # Currency-specific
    "d_rate_diff_3m": ("FX", "Rate-differential change, 3 months", "change in (foreign − US) short rate for the asset's currency exposure"),
    "d_rate_diff_12m": ("FX", "Rate-differential change, 12 months", "change in (foreign − US) short rate over 252 sessions"),
    # Commodity-specific
    "real_rate_transmit": ("Commodity", "Real-rate transmission", "1-year beta of daily returns to daily real-yield changes × the 3-month real-yield change"),
    "usd_transmit": ("Commodity", "Dollar transmission", "1-year dollar beta × the dollar's 3-month move"),
    "cmd_breadth_3m": ("Commodity", "Commodity breadth, 3 months", "share of the 11 commodity futures with a positive 3-month return, minus ½"),
    # Optionality / volatility pricing (own implied volatility where a Cboe index exists)
    "vrp": ("Optionality", "Volatility risk premium", "own 30-day implied volatility − 20-day realised volatility"),
    "iv_pctile": ("Optionality", "Implied-volatility percentile", "own implied volatility's percentile over 3 years"),
    "d_iv_1m": ("Optionality", "Implied-volatility change, 1 month", "change in own implied volatility over 21 sessions"),
    # Earnings surprise (research phase 2): company filings, not prices
    "sue_eps": ("Earnings Surprise", "Standardised EPS surprise (SUE)", "latest quarter's EPS − the same quarter a year earlier, less the "
                "average of that change over the previous 8 quarters, ÷ its standard deviation (seasonal random walk with drift); "
                "first-reported SEC figures, usable from the filing date for 63 sessions"),
    "sue_rev": ("Earnings Surprise", "Standardised revenue surprise", "the same construction on quarterly revenue"),
    # Breadth (research phase 2): the cross-section of OTHER stocks' prices, not the asset's own history
    "breadth_200d": ("Breadth", "Stock breadth (share above 200-day average)", "share of the research store's equities trading above "
                     "their 200-day average, minus ½ (dates with at least 20 names)"),
    "d_breadth_3m": ("Breadth", "Breadth change, 3 months", "change in that share over 63 sessions"),
}

CMT = [(1 / 12, "DGS1MO"), (0.25, "DGS3MO"), (0.5, "DGS6MO"), (1.0, "DGS1"), (2.0, "DGS2"), (5.0, "DGS5"), (7.0, "DGS7"),
       (10.0, "DGS10"), (20.0, "DGS20"), (30.0, "DGS30")]
SHORT_RATE = {"USD": "DGS3MO", "EUR": "ECBDFR", "GBP": "IUDSOIA", "JPY": "IRSTCI01JPM156N", "CAD": "IRSTCI01CAM156N",
              "CHF": "IRSTCI01CHM156N", "AUD": "IRSTCI01AUM156N"}
DIVIDEND_PROXY = {"SPX": "SPY", "NDX": "QQQ", "RUT": "IWM", "DJI": "DIA"}
COUNTRY_FX = {"EWJ": "JPY", "EWG": "EUR", "EWU": "GBP", "FXE": "EUR", "FXY": "JPY", "FXB": "GBP", "FEZ": "EUR", "VGK": "EUR"}
ROLL_PAIRS = {"WTI": ("WTI", "USO"), "USO": ("WTI", "USO"), "NATGAS": ("NATGAS", "UNG"), "UNG": ("NATGAS", "UNG"),
              "COPPER": ("COPPER", "CPER"), "CPER": ("COPPER", "CPER")}
OWN_IV = {"SPX": "VIXCLS", "SPY": "VIXCLS", "VTI": "VIXCLS", "IVV": "VIXCLS", "VOO": "VIXCLS", "NDX": "VXNCLS", "QQQ": "VXNCLS",
          "RUT": "RVXCLS", "IWM": "RVXCLS", "DJI": "VXDCLS", "DIA": "VXDCLS", "GLD": "GVZCLS", "GOLD": "GVZCLS", "USO": "OVXCLS",
          "WTI": "OVXCLS", "EEM": "VXEEMCLS", "EWZ": "VXEWZCLS", "AAPL": "VXAPLCLS", "AMZN": "VXAZNCLS", "GOOGL": "VXGOGCLS",
          "GS": "VXGSCLS", "IBM": "VXIBMCLS"}
COMMODITIES = ["GOLD", "SILVER", "WTI", "BRENT", "NATGAS", "COPPER", "PLATINUM", "CORN", "WHEAT", "SOYBEANS", "COFFEE"]


def ann3m_log(obs):
    """3-month annualised log change of a monthly series, on its own observation dates."""
    from .align import _iso_add
    out = []
    dates = [d for d, _ in obs]
    for d, v in obs:
        j = bisect.bisect_right(dates, _iso_add(d, -92)) - 1
        if j >= 0 and v > 0 and obs[j][1] > 0 and (_dt.date.fromisoformat(d) - _dt.date.fromisoformat(dates[j])).days <= 120:
            out.append((d, 4.0 * math.log(v / obs[j][1])))
    return out


def _pct(s):
    return [v / 100.0 if v is not None else None for v in s]


def _diff(x: Series, k: int) -> Series:
    out: Series = [None] * len(x)
    for i in range(k, len(x)):
        if x[i] is not None and x[i - k] is not None:
            out[i] = x[i] - x[i - k]
    return out


def _combine(a: Series, b: Series, fn) -> Series:
    return [fn(x, y) if x is not None and y is not None else None for x, y in zip(a, b)]


_SHARED: Dict[int, Dict[str, Series]] = {}


def shared(panel) -> Dict[str, Series]:
    """Series common to every asset (the Treasury curve, VIX term structure, inflation, commodity breadth)."""
    key = id(panel)
    if key in _SHARED:
        return _SHARED[key]
    from .align import yoy_log
    n = len(panel.calendar())
    curve = {t: _pct(panel.macro(sid)) for t, sid in CMT}
    y2, y5, y10, y30 = curve[2.0], curve[5.0], curve[10.0], curve[30.0]
    curv = [(2 * b - a - c) if a is not None and b is not None and c is not None else None for a, b, c in zip(y2, y5, y10)]
    s2s10 = _combine(y10, y2, lambda a, b: a - b)
    vix, vxv = panel.macro("VIXCLS"), panel.macro("VXVCLS")
    cpi_y = panel.macro("CPIAUCSL", transform=yoy_log)
    cpi_3 = panel.macro("CPIAUCSL", transform=ann3m_log)
    brk, real = _pct(panel.macro("T5YIE")), _pct(panel.macro("DFII10"))
    # commodity breadth: share of commodity futures up over 3 months (only dates where at least 6 have data)
    ups = [0] * n
    cnt = [0] * n
    for c in COMMODITIES:
        try:
            p = panel.series(c)
        except Exception:
            continue
        for i in range(63, n):
            a, b = p[i - 63], p[i]
            if a is not None and b is not None and a > 0 and b > 0:
                cnt[i] += 1
                ups[i] += b > a
    # stock breadth: share of the store's equities above their 200-day average
    above, have = [0] * n, [0] * n
    try:
        eqs = [a["id"] for a in panel.store.assets("EQUITY")]
    except Exception:
        eqs = []
    for a in eqs:
        try:
            p = panel.series(a)
        except Exception:
            continue
        run, cnt_ = 0.0, 0
        for i in range(n):
            v = p[i]
            if v is not None:
                run += v; cnt_ += 1
            if i >= 200 and p[i - 200] is not None:
                run -= p[i - 200]; cnt_ -= 1
            if v is not None and cnt_ >= 150:
                have[i] += 1
                above[i] += v > run / cnt_
    breadth = [(above[i] / have[i] - 0.5) if have[i] >= 20 else None for i in range(n)]
    out = {
        "breadth_200d": breadth, "d_breadth_3m": _diff(breadth, 63),
        "_curve": curve, "curvature": curv, "d_curvature_3m": _diff(curv, 63), "slope_5s30s": _combine(y30, y5, lambda a, b: a - b),
        "d_slope_2s10s_1m": _diff(s2s10, 21),
        "vix_term": _combine(vix, vxv, lambda a, b: a / b - 1.0 if b else None),
        "infl_accel": _combine(cpi_3, cpi_y, lambda a, b: a - b),
        "d_breakeven_3m": _diff(brk, 63), "d_real_y10_3m": _diff(real, 63), "_real": real,
        "cmd_breadth_3m": [(ups[i] / cnt[i] - 0.5) if cnt[i] >= 6 else None for i in range(n)],
    }
    _SHARED.clear()
    _SHARED[key] = out
    return out


def curve_yield(curve: Dict[float, Series], i: int, T: float) -> Optional[float]:
    """Treasury yield at maturity T on date i, linear in maturity between the tenors available that day."""
    pts = [(t, s[i]) for t, s in sorted(curve.items()) if s[i] is not None]
    if len(pts) < 2:
        return None
    if T <= pts[0][0]:
        return pts[0][1]
    if T >= pts[-1][0]:
        return pts[-1][1]
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if t0 <= T <= t1:
            return a + (b - a) * (T - t0) / (t1 - t0)
    return None


def trailing_dividend_yield(panel, asset_id: str) -> Optional[Series]:
    """Trailing 12-month dividends by ex-date ÷ close, or None when the store has no dividend record for the asset
    (a missing record cannot be told apart from a non-payer)."""
    src = DIVIDEND_PROXY.get(asset_id, asset_id)
    try:
        divs = sorted((d["date"], float(d["value"])) for d in panel.store.actions(src, "DIVIDEND") if d.get("value"))
    except Exception:
        return None
    if not divs:
        return None
    cal = panel.calendar()
    px = panel.series(src, "close")
    out: Series = [None] * len(cal)
    first = divs[0][0]
    lo = hi = 0
    run = 0.0
    for i, d in enumerate(cal):
        while hi < len(divs) and divs[hi][0] <= d:
            run += divs[hi][1]
            hi += 1
        cut = (_dt.date.fromisoformat(d) - _dt.timedelta(days=365)).isoformat()
        while lo < hi and divs[lo][0] <= cut:
            run -= divs[lo][1]
            lo += 1
        if d >= first and px[i] and px[i] > 0 and (_dt.date.fromisoformat(d) - _dt.date.fromisoformat(first)).days >= 365:
            out[i] = max(0.0, run) / px[i]
    return out


def quarterly_values(rows: List[dict], concept: str) -> List[tuple]:
    """(period_end, available_from, value) per fiscal quarter, as FIRST reported: Q1–Q3 from the 10-Qs, Q4 = FY − Q1 − Q2 − Q3
    available from the annual filing. A later restatement never replaces the first report (that would be hindsight)."""
    first: Dict[tuple, tuple] = {}
    for r in rows:
        if r.get("concept") != concept or r.get("value") is None or r.get("fp") not in ("Q1", "Q2", "Q3", "FY"):
            continue
        k = (str(r["period_end"])[:10], r["fp"])
        filed = str(r["filed"])[:10]
        if k not in first or filed < first[k][0]:
            first[k] = (filed, float(r["value"]))
    qs = [(end, filed, v) for (end, fp), (filed, v) in first.items() if fp != "FY"]
    out = list(qs)
    for (end, fp), (filed, v) in first.items():
        if fp != "FY":
            continue
        e = _dt.date.fromisoformat(end)
        lo = (e - _dt.timedelta(days=360)).isoformat()
        inside = [q for q in qs if lo < q[0] < end and q[1] <= filed]
        if len(inside) == 3:
            out.append((end, filed, v - sum(q[2] for q in inside)))
    return sorted(out)


def sue_series(quarters: List[tuple], cal: List[str], hold: int = 63) -> Series:
    """Standardised unexpected value per session: from each quarter's availability date for `hold` sessions."""
    n = len(cal)
    events = []
    diffs: List[tuple] = []                          # (end, seasonal difference)
    by_end = {q[0]: q for q in quarters}
    for end, avail, v in quarters:
        e = _dt.date.fromisoformat(end)
        prev = [q for k, q in by_end.items() if 350 <= (e - _dt.date.fromisoformat(k)).days <= 380]
        if not prev:
            continue
        d = v - prev[0][2]
        hist = [x for k, x in diffs if k < end][-8:]
        diffs.append((end, d))
        if len(hist) < 4:
            continue
        m = sum(hist) / len(hist)
        sd = math.sqrt(sum((x - m) ** 2 for x in hist) / (len(hist) - 1))
        if sd <= 0:
            continue
        events.append((max(avail, prev[0][1]), max(-5.0, min(5.0, (d - m) / sd))))
    out: Series = [None] * n
    events.sort()
    for avail, val in events:
        i = bisect.bisect_left(cal, avail)
        for j in range(i, min(n, i + hold)):
            out[j] = val
    return out


def earnings_surprise(panel, asset_id: str) -> Dict[str, Series]:
    from .features import split_adjust
    cal = panel.calendar()
    try:
        rows = panel.store.fundamentals(asset_id)
    except Exception:
        rows = []
    if not rows:
        return {}
    rows = split_adjust(rows, panel.store.actions(asset_id, "SPLIT"))
    return {"sue_eps": sue_series(quarterly_values(rows, "eps"), cal), "sue_rev": sue_series(quarterly_values(rows, "revenue"), cal)}


def candidate_features(panel, asset_id: str, f: Dict[str, Series], mac: Dict[str, Series]) -> Dict[str, Series]:
    """Every candidate signal for one asset (None where it does not apply or the data are missing)."""
    from .features import roll_pctile, roll_regress
    n = len(panel.calendar())
    none: Series = [None] * n
    asset = panel.store.asset(asset_id) or {}
    cls = asset.get("asset_class") or "EQUITY"
    sh = shared(panel)
    rf = mac.get("_rf") or none
    out: Dict[str, Series] = {k: none for k in CANDIDATE_FEATURES}
    for k in ("curvature", "d_curvature_3m", "slope_5s30s", "d_slope_2s10s_1m", "vix_term", "infl_accel", "d_breakeven_3m", "d_real_y10_3m"):
        out[k] = sh[k]
    if cls in ("EQUITY", "ETF", "INDEX"):
        out["breadth_200d"], out["d_breadth_3m"] = sh["breadth_200d"], sh["d_breadth_3m"]
    if cls == "EQUITY":
        out.update(earnings_surprise(panel, asset_id))
    # ---- carry and dividend yield
    dy = trailing_dividend_yield(panel, asset_id) if cls in ("EQUITY", "ETF", "INDEX") else None
    if dy is not None:
        out["div_yield"] = dy
    dur = asset.get("duration")
    carry = none
    if cls == "FX" and len(asset_id) == 6:
        base, quote = asset_id[:3], asset_id[3:]
        if base in SHORT_RATE and quote in SHORT_RATE:
            rb, rq = _pct(panel.macro(SHORT_RATE[base])), _pct(panel.macro(SHORT_RATE[quote]))
            carry = _combine(rb, rq, lambda a, b: a - b)
    elif cls == "CORP_BOND" and asset_id == "CORP_BAA":
        carry = _combine(_pct(panel.macro("DBAA")), rf, lambda a, b: a - b)
    elif cls == "TREASURY" and dur:
        carry = [(curve_yield(sh["_curve"], i, dur) - rf[i]) if rf[i] is not None and curve_yield(sh["_curve"], i, dur) is not None else None for i in range(n)]
    elif dy is not None:
        carry = _combine(dy, rf, lambda a, b: a - b)
    out["carry"] = carry
    # ---- roll-down for bonds (constant-maturity indices and bond funds with a stated duration)
    if dur and dur >= 1.0 and cls in ("TREASURY", "CORP_BOND", "ETF"):
        rd: Series = [None] * n
        for i in range(n):
            a, b = curve_yield(sh["_curve"], i, dur), curve_yield(sh["_curve"], i, dur - 1.0)
            if a is not None and b is not None:
                rd[i] = (a - b) * dur
        out["roll_down"] = rd
    # ---- futures roll yield (fund vs front month)
    if asset_id in ROLL_PAIRS:
        fut, fund = ROLL_PAIRS[asset_id]
        try:
            pf, pu = panel.series(fut, "close"), panel.series(fund, "adj_close")
            ry: Series = [None] * n
            for i in range(252, n):
                a, b, c, d = pf[i - 252], pf[i], pu[i - 252], pu[i]
                if all(x is not None and x > 0 for x in (a, b, c, d)) and rf[i] is not None:
                    ry[i] = math.log(d / c) - math.log(b / a) - rf[i]
            out["roll_yield"] = ry
            out["d_roll_yield_3m"] = _diff(ry, 63)
        except Exception:
            pass
    # ---- currency: change in the short-rate differential of the asset's currency exposure
    ccy_long = None
    if cls == "FX" and len(asset_id) == 6:
        base, quote = asset_id[:3], asset_id[3:]
        if base in SHORT_RATE and quote in SHORT_RATE:
            ccy_long = (base, quote)
    elif asset_id in COUNTRY_FX and COUNTRY_FX[asset_id] in SHORT_RATE:
        ccy_long = (COUNTRY_FX[asset_id], "USD")
    if ccy_long:
        a, b = _pct(panel.macro(SHORT_RATE[ccy_long[0]])), _pct(panel.macro(SHORT_RATE[ccy_long[1]]))
        dif = _combine(a, b, lambda x, y: x - y)
        out["d_rate_diff_3m"], out["d_rate_diff_12m"] = _diff(dif, 63), _diff(dif, 252)
    # ---- commodity transmission and breadth
    r = f.get("ret_1d") or none
    dreal = _diff(sh["_real"], 1)
    beta_real = roll_regress(r, dreal, 252)["slope"]
    out["real_rate_transmit"] = _combine(beta_real, sh["d_real_y10_3m"], lambda a, b: a * b)
    out["usd_transmit"] = _combine(f.get("dollar_beta_252") or none, f.get("dollar_mom_3m") or none, lambda a, b: a * b)
    if cls == "COMMODITY" or asset_id in ("GLD", "SLV", "USO", "UNG", "CPER", "DBA", "DBC"):
        out["cmd_breadth_3m"] = sh["cmd_breadth_3m"]
    # ---- own implied volatility
    if asset_id in OWN_IV:
        iv = _pct(panel.macro(OWN_IV[asset_id]))
        if any(v is not None for v in iv):
            rv = f.get("vol_20") or none
            out["vrp"] = _combine(iv, rv, lambda a, b: a - b)
            out["iv_pctile"] = roll_pctile(iv, 756, minp=126)
            out["d_iv_1m"] = _diff(iv, 21)
    # only where the asset has a price (an asset that has not started trading gets no signal)
    p = panel.series(asset_id)
    return {k: [v if p[i] is not None else None for i, v in enumerate(s)] for k, s in out.items()}
