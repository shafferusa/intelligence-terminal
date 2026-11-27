"""Daily model prices of derivative instruments on the research calendar, for the ledger's valuation.

The same pricing functions as the live quote (`products.Priced`) are used with the inputs as known on each date
(Panel.macro with the FRED lag, closes carried at most 5 sessions, trailing-year dividends), so a trade at the
quoted price starts with zero P&L and the daily marks are consistent with it. Values after expiry are None; the
ledger settles a position on its expiry date (options at intrinsic value, futures and forwards at the final mark)."""
from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from . import pricing as px
from .market import CMT, DIVIDEND_PROXY, SHORT_RATE, VOL_INDEX


def _ffill(xs, limit=5):
    out, last, age = [], None, 0
    for v in xs:
        if v is not None:
            last, age = v, 0
        else:
            age += 1
        out.append(last if last is not None and age <= limit else None)
    return out


def _div_yield(panel, store, asset_id: str) -> List[float]:
    a = DIVIDEND_PROXY.get(asset_id, asset_id)
    key = ("inst:dy", a)
    if key in panel._cache:
        return panel._cache[key]
    cal = panel.calendar()
    close = _ffill(panel.series(a, "close", max_fill=10), 10)
    evs = [(e["date"], e["value"]) for e in store.actions(a, "DIVIDEND")]
    out, j0, j1, run = [], 0, 0, 0.0
    for k, d in enumerate(cal):
        while j1 < len(evs) and evs[j1][0] <= d:
            run += evs[j1][1]; j1 += 1
        lo = (_dt.date.fromisoformat(d) - _dt.timedelta(days=365)).isoformat()
        while j0 < j1 and evs[j0][0] <= lo:
            run -= evs[j0][1]; j0 += 1
        out.append(run / close[k] if close[k] else 0.0)
    panel._cache[key] = out
    return out


def _rate(panel, sid: str) -> List[Optional[float]]:
    from ..data.universe import FRED_SERIES
    monthly = (FRED_SERIES.get(sid) or {}).get("freq") == "monthly"
    s = panel.macro(sid, max_age_days=100 if monthly else 10)
    return [(v / 100.0) if v is not None else None for v in s]


def price_series(panel, store, inst) -> List[Optional[float]]:
    """Model price per unit (futures/forwards: the quoted price; options: premium per share) on every calendar date
    up to the expiry; None where an input is missing or after expiry."""
    key = ("inst:px", inst.id)
    if key in panel._cache:
        return panel._cache[key]
    cal = panel.calendar()
    n = len(cal)
    out: List[Optional[float]] = [None] * n
    exp = inst.expiry
    r = _rate(panel, "DGS3MO")
    if inst.type == "FUTURE":
        kind = inst.spec["kind"]
        if kind == "equity_index":
            S = _ffill(panel.series(inst.underlying, "close", max_fill=0))
            q = _div_yield(panel, store, inst.spec["proxy"])
            for k, d in enumerate(cal):
                if d > exp or S[k] is None or r[k] is None:
                    continue
                out[k] = px.equity_future(S[k], r[k], q[k], px.year_frac(d, exp))
        elif kind == "treasury":
            ser = [(t, _rate(panel, sid)) for t, sid in CMT]
            for k, d in enumerate(cal):
                if d > exp:
                    continue
                pts = {t: s[k] for t, s in ser if s[k] is not None}
                if len(pts) < 3:
                    continue
                out[k] = px.treasury_future(px.Curve(pts), inst.spec["ctd_years"], px.year_frac(d, exp), inst.spec["face"])["price"]
        elif kind == "fx":
            from ..engine.portfolio import fx_series
            S = _ffill(fx_series(panel, inst.spec["ccy"]))
            rf = _rate(panel, SHORT_RATE.get(inst.spec["ccy"], ""))
            for k, d in enumerate(cal):
                if d > exp or S[k] is None or r[k] is None or rf[k] is None:
                    continue
                out[k] = px.fx_forward(S[k], r[k], rf[k], px.year_frac(d, exp))
    elif inst.type == "FORWARD":
        S = _ffill(panel.series(inst.underlying, "close", max_fill=0))
        rb, rq = _rate(panel, SHORT_RATE.get(inst.spec["base"], "")), _rate(panel, SHORT_RATE.get(inst.spec["quote"], ""))
        for k, d in enumerate(cal):
            if d > exp or S[k] is None or rb[k] is None or rq[k] is None:
                continue
            out[k] = px.fx_forward(S[k], rq[k], rb[k], px.year_frac(d, exp))
    elif inst.type == "OPTION":
        S = _ffill(panel.series(inst.underlying, "close", max_fill=0))
        q = _div_yield(panel, store, inst.underlying)
        spec = VOL_INDEX.get(inst.underlying) or []
        vs = [(mat, _rate(panel, sid)) for mat, sid in spec]
        for k, d in enumerate(cal):
            if d > exp or S[k] is None or r[k] is None:
                continue
            T = px.year_frac(d, exp)
            pts = [(mat, s[k]) for mat, s in vs if s[k] is not None]
            if not pts:
                continue
            vol = px.interp_var_time(max(T, 1 / 365), pts)
            out[k] = px.option_price(inst.right, S[k], inst.strike, T, r[k], q[k], vol, inst.style)
    panel._cache[key] = out
    return out


def settlement(panel, store, inst) -> Optional[float]:
    """Final settlement per unit on the expiry date: an option's intrinsic value at the underlying's close; a future's
    or forward's last mark (its fair value converges to spot)."""
    cal = panel.calendar()
    k = panel.index_of(inst.expiry)
    if inst.type == "OPTION":
        S = _ffill(panel.series(inst.underlying, "close", max_fill=0))
        s = S[k]
        if s is None:
            return None
        return max(0.0, s - inst.strike) if inst.right == "C" else max(0.0, inst.strike - s)
    ser = price_series(panel, store, inst)
    for j in range(k, max(-1, k - 6), -1):
        if ser[j] is not None:
            return ser[j]
    return None


def unit_size(inst) -> float:
    """Dollars (quote currency) per unit of price for one contract: multiplier, face/100 for Treasury futures,
    contract size for currency futures, 1 per unit of base currency for forwards and 100 per option."""
    if inst.type == "FUTURE":
        k = inst.spec["kind"]
        if k == "treasury":
            return inst.spec["face"] / 100.0
        if k == "fx":
            return float(inst.spec["size"])
        return float(inst.multiplier)
    if inst.type == "OPTION":
        return float(inst.multiplier)
    return 1.0


MARGIN = {"equity_index": 0.06, "treasury": 0.025, "fx": 0.04, "forward": 0.05}


def margin_rate(inst) -> float:
    """Initial-margin / collateral estimate as a share of notional (not the exchange's figure): equity-index futures
    6%, Treasury futures 2.5%, currency futures 4%, FX forwards 5%. Margin is collateral, never exposure."""
    if inst.type == "FORWARD":
        return MARGIN["forward"]
    if inst.type == "FUTURE":
        return MARGIN.get(inst.spec["kind"], 0.10)
    return 0.0
