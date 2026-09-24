"""Signal backtests on one asset.

The position is decided at the close of day t from information known at t (the signal and the regime on t) and
earns the return of day t+1. Entry when the signal (in the direction its history points, or inverted by the user)
reaches `entry`; exit when it falls back through `exit` after at least `min_hold` sessions. Optional long/short,
a regime filter (trade only while a regime holds), a date range and trading costs in basis points per side.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

ANN = 252


def _stats(daily: List[float]) -> Dict[str, Optional[float]]:
    n = len(daily)
    if n < 2:
        return {"cagr": None, "vol": None, "sharpe": None, "sortino": None, "max_drawdown": None, "total_return": None}
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in daily:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    mean = sum(daily) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in daily) / (n - 1))
    dn = math.sqrt(sum(min(r, 0) ** 2 for r in daily) / n)
    years = n / ANN
    cagr = eq ** (1 / years) - 1 if years > 0 and eq > 0 else None
    return {"total_return": eq - 1, "cagr": cagr, "vol": sd * math.sqrt(ANN), "sharpe": (mean * ANN) / (sd * math.sqrt(ANN)) if sd > 0 else None,
            "sortino": (mean * ANN) / (dn * math.sqrt(ANN)) if dn > 0 else None, "max_drawdown": mdd}


def run(dates: List[str], price: List[Optional[float]], signal: List[Optional[float]], spec: dict,
        regimes: Optional[Dict[str, List[Optional[str]]]] = None, benchmark: Optional[List[Optional[float]]] = None) -> dict:
    entry = float(spec.get("entry", 1.0))
    exit_ = float(spec.get("exit", 0.0))
    sign = -1.0 if spec.get("invert") else 1.0
    sign *= float(spec.get("direction_sign", 1.0))            # the signal's historical direction (from the horizon engine)
    long_short = spec.get("mode", "long") == "long_short"
    min_hold = int(spec.get("min_hold", 1))
    cost = float(spec.get("cost_bps", 5.0)) / 1e4
    start = spec.get("start") or dates[0]
    end = spec.get("end") or dates[-1]
    rf_dim, rf_state = (spec.get("regime") or (None, None)) if isinstance(spec.get("regime"), (list, tuple)) else (None, None)
    n = len(dates)
    pos = 0
    held = 0
    daily, bench, mkt, posn = [], [], [], []
    trades: List[dict] = []
    cur = None
    eq_dates = []
    for t in range(n - 1):
        if dates[t] < start or dates[t + 1] > end:
            continue
        p0, p1 = price[t], price[t + 1]
        if p0 is None or p1 is None or p0 <= 0:
            continue
        s = signal[t]
        allowed = True
        if rf_dim and regimes is not None:
            allowed = regimes.get(rf_dim, [None] * n)[t] == rf_state
        new = pos
        if s is not None:
            v = s * sign
            if pos == 0 and allowed:
                if v >= entry:
                    new = 1
                elif long_short and v <= -entry:
                    new = -1
            elif pos == 1 and held >= min_hold and (v <= exit_ or not allowed):
                new = -1 if (long_short and allowed and v <= -entry) else 0
            elif pos == -1 and held >= min_hold and (v >= -exit_ or not allowed):
                new = 1 if (allowed and v >= entry) else 0
        tc = abs(new - pos) * cost
        if new != pos:
            if cur is not None:
                cur["exit"] = dates[t]
                cur["return"] = cur["_eq"] - 1
                trades.append({k: v for k, v in cur.items() if not k.startswith("_")})
                cur = None
            if new != 0:
                cur = {"entry": dates[t], "side": "long" if new > 0 else "short", "_eq": 1.0}
            held = 0
        pos = new
        held += 1
        r = p1 / p0 - 1
        d = pos * r - tc
        if cur is not None:
            cur["_eq"] *= (1 + pos * r)
        daily.append(d)
        bench.append(r)
        posn.append(pos)
        eq_dates.append(dates[t + 1])
        if benchmark is not None and benchmark[t] and benchmark[t + 1]:
            mkt.append(benchmark[t + 1] / benchmark[t] - 1)
        else:
            mkt.append(None)
    if cur is not None:
        cur["exit"] = None
        cur["return"] = cur["_eq"] - 1
        trades.append({k: v for k, v in cur.items() if not k.startswith("_")})
    st = _stats(daily)
    bst = _stats(bench)
    wins = [t["return"] for t in trades if t["return"] > 0]
    losses = [t["return"] for t in trades if t["return"] <= 0]
    changes = sum(1 for i in range(1, len(posn)) if posn[i] != posn[i - 1])
    years = len(daily) / ANN if daily else 0
    # alpha and beta against the benchmark (SPY)
    alpha = beta = None
    pairs = [(d, m) for d, m in zip(daily, mkt) if m is not None]
    if len(pairs) > 60:
        mx = sum(m for _, m in pairs) / len(pairs)
        my = sum(d for d, _ in pairs) / len(pairs)
        vx = sum((m - mx) ** 2 for _, m in pairs)
        if vx > 0:
            beta = sum((m - mx) * (d - my) for d, m in pairs) / vx
            alpha = (my - beta * mx) * ANN
    eq, curve, bcurve = 1.0, [], []
    be = 1.0
    for d, b in zip(daily, bench):
        eq *= 1 + d
        be *= 1 + b
        curve.append(eq)
        bcurve.append(be)
    step = max(1, len(curve) // 400)
    return {"spec": spec, "metrics": {**st, "win_rate": (len(wins) / len(trades)) if trades else None,
                                      "avg_gain": (sum(wins) / len(wins)) if wins else None, "avg_loss": (sum(losses) / len(losses)) if losses else None,
                                      "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
                                      "turnover": (changes / years) if years else None, "trades": len(trades),
                                      "exposure": (sum(1 for x in posn if x != 0) / len(posn)) if posn else None,
                                      "benchmark_return": bst["total_return"], "benchmark_cagr": bst["cagr"], "benchmark_sharpe": bst["sharpe"],
                                      "alpha": alpha, "beta": beta, "days": len(daily)},
            "curve": {"dates": eq_dates[::step], "strategy": curve[::step], "buy_hold": bcurve[::step]},
            "trades": trades[-200:]}
