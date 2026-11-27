"""Signal backtests on one asset.

Timing. The signal and the regime are observed at the close of day t. With an execution lag of L sessions (spec key
`lag`, default 1) the position they call for is first held from the close of t+L, so a day-t signal first earns the
return from close t+1 to close t+2. `lag = 0` trades at the very close whose data produced the signal (it earns
close t -> close t+1): that is optimistic, since the signal is only known once that close has printed, and it is
kept only for comparison.

Rules. Entry when the signal (times `direction_sign`, and inverted if the user asks) reaches `entry`; exit when it
falls back through `exit` after at least `min_hold` sessions. Optional long/short, a regime filter (trade only while
a regime holds), a date range and trading costs in basis points per side. A caller that wants the signal pointed the
way its history says passes a point-in-time direction series (horizons.expanding_direction) multiplied into the
signal, never a full-sample sign.

Metrics. Daily and per-trade returns are net of costs. `buy_hold_*` is the same asset bought and held over the same
window (`benchmark_*` are the same numbers under their old names); alpha and beta are measured against SPY.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

ANN = 252
OPTIMISTIC_NOTE = "lag 0: trades at the same close that produced the signal (optimistic, for comparison only)"


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


def calmar(cagr: Optional[float], max_drawdown: Optional[float]) -> Optional[float]:
    """CAGR / |max drawdown| (None when there was no drawdown)."""
    if cagr is None or max_drawdown is None or max_drawdown >= 0:
        return None
    return cagr / abs(max_drawdown)


def simulate(dates: List[str], price: List[Optional[float]], signal: List[Optional[float]], spec: dict,
             regimes: Optional[Dict[str, List[Optional[str]]]] = None, benchmark: Optional[List[Optional[float]]] = None) -> dict:
    """The day-by-day simulation: net daily returns, the asset's own returns, SPY's, the position held over each
    interval close t -> close t+1 (dated t+1) and the trades (returns net of costs)."""
    entry = float(spec.get("entry", 1.0))
    exit_ = float(spec.get("exit", 0.0))
    sign = -1.0 if spec.get("invert") else 1.0
    sign *= float(spec.get("direction_sign", 1.0))            # a constant sign; prefer a point-in-time direction series
    long_short = spec.get("mode", "long") == "long_short"
    min_hold = int(spec.get("min_hold", 1))
    cost = float(spec.get("cost_bps", 5.0)) / 1e4
    lag = max(0, int(spec.get("lag", 1)))
    start = spec.get("start") or dates[0]
    end = spec.get("end") or dates[-1]
    rf_dim, rf_state = (spec.get("regime") or (None, None)) if isinstance(spec.get("regime"), (list, tuple)) else (None, None)
    n = len(dates)
    reg_states = regimes.get(rf_dim, [None] * n) if (rf_dim and regimes is not None) else None
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
        # decided on what was known at the close of t - lag, executed at the close of t, earns close t -> t+1
        k = t - lag
        s = signal[k] if k >= 0 else None
        allowed = True
        if reg_states is not None:
            allowed = k >= 0 and reg_states[k] == rf_state
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
        r = p1 / p0 - 1
        if new != pos:
            if cur is not None:
                cur["_eq"] *= 1 - abs(pos) * cost          # the exit leg's cost belongs to the closing trade
                cur["exit"] = dates[t]
                cur["return"] = cur["_eq"] - 1
                trades.append({a: b for a, b in cur.items() if not a.startswith("_")})
                cur = None
            if new != 0:                                   # ... and the entry leg's to the opening one
                cur = {"entry": dates[t], "signal_date": dates[k] if k >= 0 else None, "side": "long" if new > 0 else "short",
                       "_eq": 1.0 - abs(new) * cost}
            held = 0
        pos = new
        held += 1
        if cur is not None:
            cur["_eq"] *= 1 + pos * r
        daily.append(pos * r - tc)
        bench.append(r)
        posn.append(pos)
        eq_dates.append(dates[t + 1])
        if benchmark is not None and benchmark[t] and benchmark[t + 1]:
            mkt.append(benchmark[t + 1] / benchmark[t] - 1)
        else:
            mkt.append(None)
    if cur is not None:
        cur["exit"] = None                                 # still open: marked to the last close, no exit cost yet
        cur["return"] = cur["_eq"] - 1
        trades.append({a: b for a, b in cur.items() if not a.startswith("_")})
    return {"daily": daily, "bench": bench, "mkt": mkt, "positions": posn, "dates": eq_dates, "trades": trades, "lag": lag}


def run(dates: List[str], price: List[Optional[float]], signal: List[Optional[float]], spec: dict,
        regimes: Optional[Dict[str, List[Optional[str]]]] = None, benchmark: Optional[List[Optional[float]]] = None) -> dict:
    sim = simulate(dates, price, signal, spec, regimes, benchmark)
    daily, bench, mkt, posn, trades = sim["daily"], sim["bench"], sim["mkt"], sim["positions"], sim["trades"]
    st = _stats(daily)
    bst = _stats(bench)
    wins = [t["return"] for t in trades if t["return"] > 0]
    losses = [t["return"] for t in trades if t["return"] <= 0]
    changes = sum(1 for i in range(1, len(posn)) if posn[i] != posn[i - 1])
    years = len(daily) / ANN if daily else 0
    # alpha and beta against the market (SPY)
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
    lag = sim["lag"]
    execution = OPTIMISTIC_NOTE if lag == 0 else f"lag {lag}: a signal seen at the close of day t is first traded at the close of day t+{lag}"
    return {"spec": spec, "execution": execution,
            "metrics": {**st, "calmar": calmar(st["cagr"], st["max_drawdown"]), "lag": lag,
                        "win_rate": (len(wins) / len(trades)) if trades else None,
                        "avg_gain": (sum(wins) / len(wins)) if wins else None, "avg_loss": (sum(losses) / len(losses)) if losses else None,
                        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
                        "turnover": (changes / years) if years else None, "trades": len(trades),
                        "exposure": (sum(1 for x in posn if x != 0) / len(posn)) if posn else None,
                        "buy_hold_return": bst["total_return"], "buy_hold_cagr": bst["cagr"], "buy_hold_sharpe": bst["sharpe"],
                        "buy_hold_max_drawdown": bst["max_drawdown"],
                        # the same asset's buy & hold under the old names (saved backtests and older callers read these)
                        "benchmark_return": bst["total_return"], "benchmark_cagr": bst["cagr"], "benchmark_sharpe": bst["sharpe"],
                        "alpha": alpha, "beta": beta, "days": len(daily)},
            "curve": {"dates": sim["dates"][::step], "strategy": curve[::step], "buy_hold": bcurve[::step]},
            "trades": trades[-200:]}
