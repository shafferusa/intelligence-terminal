"""The user's portfolio: a ledger of deposits, withdrawals, buys and sells, valued on the research calendar.

Everything is reported in US dollars. A position in another currency is converted at that day's rate (EUR, GBP,
AUD and NZD via their USD pairs, JPY, CAD, CHF, CNY, MXN and INR via the inverse of theirs, HKD at the 7.8 peg).
Cost basis uses average cost; realised P&L is booked on sales. Risk is measured on the current holdings over
their recent history (a historical simulation of today's weights), and on the actual NAV once it has enough days.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from .align import Panel
from .features import log_returns

ANN = 252
FX_DIRECT = {"EUR": "EURUSD", "GBP": "GBPUSD", "AUD": "AUDUSD", "NZD": "NZDUSD"}
FX_INVERSE = {"JPY": "USDJPY", "CAD": "USDCAD", "CHF": "USDCHF", "CNY": "USDCNY", "MXN": "USDMXN", "INR": "USDINR"}
PEGS = {"HKD": 1 / 7.8, "USD": 1.0}
FACTORS = {                       # factor -> (long leg, short leg or None)
    "Market": ("SPY", None), "Size (small − large)": ("IWM", "SPY"), "Value (value − growth)": ("IWD", "IWF"),
    "Momentum": ("MTUM", "SPY"), "Quality": ("QUAL", "SPY"), "Low volatility": ("USMV", "SPY"),
    "Technology / Nasdaq": ("QQQ", "SPY"), "Rates (Treasuries)": ("IEF", None), "Credit (HY − Treasuries)": ("HYG", "IEF"),
    "Dollar": ("DXY", None), "Oil": ("WTI", None), "Gold": ("GOLD", None),
}


def fx_series(panel: Panel, ccy: str) -> List[Optional[float]]:
    """USD per unit of `ccy` on the calendar."""
    n = len(panel.calendar())
    if ccy in PEGS:
        return [PEGS[ccy]] * n
    if ccy in FX_DIRECT:
        return panel.series(FX_DIRECT[ccy], "close", max_fill=10)
    if ccy in FX_INVERSE:
        return [(1.0 / v) if v else None for v in panel.series(FX_INVERSE[ccy], "close", max_fill=10)]
    return [None] * n


def _ffill(xs):
    out, last = [], None
    for v in xs:
        last = v if v is not None else last
        out.append(last)
    return out


class Ledger:
    def __init__(self, store, panel: Panel, portfolio_id: str = "main"):
        self.store = store
        self.panel = panel
        self.pid = portfolio_id
        if not any(p["id"] == portfolio_id for p in store.portfolios()):
            store.create_portfolio(portfolio_id, "My Portfolio", "USD")

    # ------------------------------------------------------------------ commands
    def deposit(self, amount: float, date: Optional[str] = None, note: str = "") -> int:
        if not amount or amount <= 0:
            raise ValueError("the amount must be positive")
        return self.store.add_transaction({"portfolio_id": self.pid, "date": date or self.panel.calendar()[-1], "kind": "DEPOSIT",
                                           "asset_id": None, "quantity": None, "price": float(amount), "fee": 0.0, "currency": "USD", "note": note})

    def withdraw(self, amount: float, date: Optional[str] = None, note: str = "") -> int:
        if not amount or amount <= 0:
            raise ValueError("the amount must be positive")
        cash = min(self.holdings(date)["cash"], self.holdings()["cash"])     # later purchases may already rely on that cash
        if amount > cash + 1e-6:
            raise ValueError(f"only {cash:,.2f} USD of cash is available to withdraw on that date")
        return self.store.add_transaction({"portfolio_id": self.pid, "date": date or self.panel.calendar()[-1], "kind": "WITHDRAW",
                                           "asset_id": None, "quantity": None, "price": float(amount), "fee": 0.0, "currency": "USD", "note": note})

    def trade(self, asset_id: str, quantity: float, side: str = "BUY", price: Optional[float] = None, date: Optional[str] = None,
              fee: float = 0.0, note: str = "") -> int:
        """Record a buy or sell. Without a price, the asset's close on that date is used."""
        a = self.store.asset(asset_id)
        if a is None:
            raise ValueError(f"unknown asset {asset_id}")
        if not quantity or quantity <= 0:
            raise ValueError("the quantity must be positive")
        cal = self.panel.calendar()
        d = date or cal[-1]
        if price is None:
            i = self.panel.index_of(d)
            px = self.panel.series(asset_id, "close")[i]
            if px is None:
                raise ValueError(f"no {asset_id} price on {d}")
            price = px
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        if side == "SELL":
            held = self.holdings(d)["positions"].get(asset_id, {}).get("quantity", 0.0)
            if quantity > held + 1e-9 and not a.get("allow_short"):
                raise ValueError(f"you hold {held:g} {asset_id}; selling more would open a short (not supported)")
        return self.store.add_transaction({"portfolio_id": self.pid, "date": d, "kind": side, "asset_id": asset_id, "quantity": float(quantity),
                                           "price": float(price), "fee": float(fee or 0.0), "currency": a.get("currency") or "USD", "note": note})

    # ------------------------------------------------------------------ state
    def _fx_at(self, ccy: str, i: int) -> float:
        s = fx_series(self.panel, ccy or "USD")
        v = s[i] if 0 <= i < len(s) else None
        if v is None:
            prior = [x for x in s[:i + 1] if x is not None]
            v = prior[-1] if prior else None
        if v is None:
            raise ValueError(f"no {ccy} exchange rate")
        return v

    def holdings(self, as_of: Optional[str] = None) -> dict:
        cal = self.panel.calendar()
        as_of = as_of or cal[-1]
        cash = 0.0
        pos: Dict[str, dict] = {}
        realized = 0.0
        contributed = 0.0
        for t in sorted(self.store.transactions(self.pid), key=lambda x: (x["date"], x["id"])):
            if t["date"] > as_of:
                continue
            k = t["kind"]
            if k == "DEPOSIT":
                cash += t["price"]; contributed += t["price"]
            elif k == "WITHDRAW":
                cash -= t["price"]; contributed -= t["price"]
            else:
                i = self.panel.index_of(t["date"])
                fx = self._fx_at(t["currency"], i)
                usd = t["quantity"] * t["price"] * fx
                p = pos.setdefault(t["asset_id"], {"quantity": 0.0, "cost": 0.0, "realized": 0.0})
                if k == "BUY":
                    cash -= usd + (t["fee"] or 0)
                    p["quantity"] += t["quantity"]
                    p["cost"] += usd + (t["fee"] or 0)
                else:
                    avg = p["cost"] / p["quantity"] if p["quantity"] else 0.0
                    q = min(t["quantity"], p["quantity"])
                    pnl = usd - (t["fee"] or 0) - avg * q
                    p["realized"] += pnl
                    realized += pnl
                    p["cost"] -= avg * q
                    p["quantity"] -= q
                    cash += usd - (t["fee"] or 0)
        return {"as_of": as_of, "cash": cash, "positions": pos, "realized": realized, "contributed": contributed}

    def nav_history(self) -> dict:
        """Daily NAV from the first transaction to today (positions at each day's close and rate)."""
        txs = sorted(self.store.transactions(self.pid), key=lambda x: (x["date"], x["id"]))
        cal = self.panel.calendar()
        if not txs:
            return {"dates": [], "nav": [], "flows": []}
        i0 = self.panel.index_of(txs[0]["date"])
        assets = sorted({t["asset_id"] for t in txs if t["asset_id"]})
        px = {a: _ffill(self.panel.series(a, "close", max_fill=10)) for a in assets}
        fx = {a: _ffill(fx_series(self.panel, (self.store.asset(a) or {}).get("currency") or "USD")) for a in assets}
        qty = {a: 0.0 for a in assets}
        cash = 0.0
        j = 0
        dates, navs, flows = [], [], []
        for i in range(i0, len(cal)):
            d = cal[i]
            flow = 0.0
            while j < len(txs) and txs[j]["date"] <= d:
                t = txs[j]
                if t["kind"] == "DEPOSIT":
                    cash += t["price"]; flow += t["price"]
                elif t["kind"] == "WITHDRAW":
                    cash -= t["price"]; flow -= t["price"]
                else:
                    rate = self._fx_at(t["currency"], self.panel.index_of(t["date"]))
                    usd = t["quantity"] * t["price"] * rate
                    if t["kind"] == "BUY":
                        qty[t["asset_id"]] += t["quantity"]; cash -= usd + (t["fee"] or 0)
                    else:
                        qty[t["asset_id"]] -= t["quantity"]; cash += usd - (t["fee"] or 0)
                j += 1
            v = cash
            for a in assets:
                if qty[a]:
                    p, r = px[a][i], fx[a][i]
                    if p is not None and r is not None:
                        v += qty[a] * p * r
            dates.append(d); navs.append(v); flows.append(flow)
        return {"dates": dates, "nav": navs, "flows": flows}


# ------------------------------------------------------------------ analytics
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def aligned_returns(panel: Panel, assets: List[str], days: int = 756) -> Dict[str, List[Optional[float]]]:
    """Daily simple returns in USD (price × rate) of each asset over the last `days` sessions."""
    out = {}
    for a in assets:
        p = panel.series(a, "adj_close")
        ccy = (panel.store.asset(a) or {}).get("currency") or "USD"
        fx = fx_series(panel, ccy) if ccy != "USD" else None
        usd = [(p[i] * fx[i]) if (fx is not None and p[i] is not None and fx[i] is not None) else (p[i] if fx is None else None) for i in range(len(p))]
        r = [None] + [(usd[i] / usd[i - 1] - 1) if usd[i] is not None and usd[i - 1] else None for i in range(1, len(usd))]
        out[a] = r[-days:]
    return out


def covariance(rets: Dict[str, list], assets: List[str]) -> List[List[float]]:
    n = len(assets)
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            pairs = [(x, y) for x, y in zip(rets[assets[i]], rets[assets[j]]) if x is not None and y is not None]
            if len(pairs) < 20:
                c = 0.0
            else:
                mx = sum(x for x, _ in pairs) / len(pairs)
                my = sum(y for _, y in pairs) / len(pairs)
                c = sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) - 1) * ANN
            cov[i][j] = cov[j][i] = c
    return cov


def correlation_from_cov(cov):
    n = len(cov)
    return [[(cov[i][j] / math.sqrt(cov[i][i] * cov[j][j])) if cov[i][i] > 0 and cov[j][j] > 0 else None for j in range(n)] for i in range(n)]


def portfolio_series(rets: Dict[str, list], weights: Dict[str, float]) -> List[Optional[float]]:
    n = len(next(iter(rets.values()))) if rets else 0
    out = []
    for t in range(n):
        s = 0.0
        ok = False
        for a, w in weights.items():
            v = rets[a][t]
            if v is not None:
                s += w * v
                ok = True
        out.append(s if ok else None)
    return out


def risk_stats(r: List[Optional[float]], rf: float = 0.0) -> dict:
    xs = [x for x in r if x is not None]
    if len(xs) < 30:
        return {}
    m, sd = _mean(xs), _std(xs)
    dn = math.sqrt(sum(min(x, 0) ** 2 for x in xs) / len(xs))
    eq, peak, mdd = 1.0, 1.0, 0.0
    for x in xs:
        eq *= 1 + x; peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    srt = sorted(xs)
    q95, q99 = srt[int(0.05 * len(srt))], srt[int(0.01 * len(srt))]
    es95 = -_mean(srt[:max(1, int(0.05 * len(srt)))])
    es99 = -_mean(srt[:max(1, int(0.01 * len(srt)))])
    ann_ret = m * ANN
    return {"ann_return": ann_ret, "vol": sd * math.sqrt(ANN), "sharpe": (ann_ret - rf) / (sd * math.sqrt(ANN)) if sd else None,
            "sortino": (ann_ret - rf) / (dn * math.sqrt(ANN)) if dn else None, "max_drawdown": mdd,
            "var95": -q95, "var99": -q99, "es95": es95, "es99": es99, "days": len(xs)}


def regress(y: List[Optional[float]], xs: Dict[str, List[Optional[float]]]) -> dict:
    """Multivariate OLS with intercept (rows with any missing value dropped): betas, t-stats, R², and each factor's
    share of the explained variance (Euler: β_k Cov(f_k, ŷ) / Var(ŷ))."""
    from finsim.quant.linalg import solve
    names = list(xs)
    rows = [(y[t], [xs[k][t] for k in names]) for t in range(len(y)) if y[t] is not None and all(xs[k][t] is not None for k in names)]
    p = len(names) + 1
    if len(rows) < p + 10:
        return {}
    X = [[1.0] + r for _, r in rows]
    Y = [v for v, _ in rows]
    XtX = [[sum(X[t][a] * X[t][b] for t in range(len(X))) for b in range(p)] for a in range(p)]
    for a in range(p):
        XtX[a][a] += 1e-12
    XtY = [sum(X[t][a] * Y[t] for t in range(len(X))) for a in range(p)]
    try:
        beta = solve(XtX, XtY)
    except Exception:
        return {}
    fit = [sum(beta[a] * X[t][a] for a in range(p)) for t in range(len(X))]
    res = [Y[t] - fit[t] for t in range(len(X))]
    my = sum(Y) / len(Y)
    sst = sum((v - my) ** 2 for v in Y)
    sse = sum(e * e for e in res)
    s2 = sse / max(1, len(Y) - p)
    try:
        from finsim.quant.linalg import inverse
        inv = inverse(XtX)
        se = [math.sqrt(max(0.0, s2 * inv[a][a])) for a in range(p)]
    except Exception:
        se = [None] * p
    mf = sum(fit) / len(fit)
    vf = sum((f - mf) ** 2 for f in fit) or 1e-18
    shares = {}
    for k, name in enumerate(names, start=1):
        col = [X[t][k] for t in range(len(X))]
        mc = sum(col) / len(col)
        cov = sum((col[t] - mc) * (fit[t] - mf) for t in range(len(X)))
        shares[name] = beta[k] * cov / vf
    return {"alpha": beta[0], "betas": {n: beta[i + 1] for i, n in enumerate(names)},
            "t": {n: (beta[i + 1] / se[i + 1]) if se[i + 1] else None for i, n in enumerate(names)},
            "r2": 1 - sse / sst if sst > 0 else None, "shares": shares, "n": len(Y)}


def factor_returns(panel: Panel, days: int = 756, weekly: bool = True) -> Dict[str, List[Optional[float]]]:
    ids = sorted({x for a, b in FACTORS.values() for x in (a, b) if x})
    rets = aligned_returns(panel, [i for i in ids if panel.store.asset(i)], days)
    out = {}
    for name, (a, b) in FACTORS.items():
        if a not in rets or (b and b not in rets):
            continue
        out[name] = [(x - (rets[b][t] if b else 0.0)) if x is not None and (not b or rets[b][t] is not None) else None for t, x in enumerate(rets[a])]
    return _weekly(out) if weekly else out


def _weekly(series: Dict[str, list], k: int = 5) -> Dict[str, list]:
    out = {}
    for name, s in series.items():
        agg = []
        for i in range(0, len(s) - k + 1, k):
            chunk = s[i:i + k]
            if any(v is None for v in chunk):
                agg.append(None)
            else:
                g = 1.0
                for v in chunk:
                    g *= 1 + v
                agg.append(g - 1)
        out[name] = agg
    return out


def analytics(store, panel: Panel, ledger: Ledger, scores: Optional[Dict[str, dict]] = None) -> dict:
    """Everything on the Portfolio page. `scores`: {asset_id: {quant, ml, expected, confidence, horizon}} if known."""
    cal = panel.calendar()
    i = len(cal) - 1
    h = ledger.holdings()
    rows = []
    nav = h["cash"]
    for a, p in h["positions"].items():
        if abs(p["quantity"]) < 1e-12 and not p["realized"]:
            continue
        meta = store.asset(a) or {"id": a, "name": a, "asset_class": "?", "currency": "USD"}
        px = next((v for v in reversed(panel.series(a, "close", max_fill=10)[:i + 1]) if v is not None), None)
        fx = ledger._fx_at(meta.get("currency") or "USD", i)
        mv = (p["quantity"] * px * fx) if px is not None else 0.0
        nav += mv
        rows.append({"asset_id": a, "name": meta.get("name"), "asset_class": meta.get("asset_class"), "sector": meta.get("sector"),
                     "country": meta.get("country"), "currency": meta.get("currency"), "duration": meta.get("duration"),
                     "quantity": p["quantity"], "price": px, "market_value": mv, "cost_basis": p["cost"],
                     "unrealized": mv - p["cost"] if p["quantity"] else 0.0, "realized": p["realized"]})
    held = [r for r in rows if abs(r["quantity"]) > 1e-12]
    for r in rows:
        r["weight"] = r["market_value"] / nav if nav else None
    weights = {r["asset_id"]: r["market_value"] / nav for r in held} if nav else {}
    rets = aligned_returns(panel, list(weights), 756) if weights else {}
    ps = portfolio_series(rets, weights) if weights else []
    rf = next((v for v in reversed(panel.macro("DGS3MO")) if v is not None), 0.0) / 100.0
    risk = risk_stats(ps, rf) if ps else {}
    assets = list(weights)
    cov = covariance(rets, assets) if assets else []
    w = [weights[a] for a in assets]
    port_var = sum(w[i] * cov[i][j] * w[j] for i in range(len(w)) for j in range(len(w))) if w else 0.0
    sig = math.sqrt(port_var) if port_var > 0 else None
    mrc = [sum(cov[i][j] * w[j] for j in range(len(w))) / sig if sig else None for i in range(len(w))]
    for k, a in enumerate(assets):
        r = next(x for x in held if x["asset_id"] == a)
        r["risk_contribution"] = (w[k] * mrc[k] / sig) if sig and mrc[k] is not None else None
        r["marginal_risk"] = mrc[k]
        vol = _std(rets[a])
        r["volatility"] = vol * math.sqrt(ANN) if vol else None
        st = risk_stats(rets[a], rf)
        r["sharpe"] = st.get("sharpe")
    mkt = aligned_returns(panel, ["SPY"], 756)["SPY"] if store.asset("SPY") else []
    for r in held:
        pairs = [(x, m) for x, m in zip(rets.get(r["asset_id"], []), mkt) if x is not None and m is not None]
        if len(pairs) > 60:
            mx = sum(m for _, m in pairs) / len(pairs); my = sum(x for x, _ in pairs) / len(pairs)
            vx = sum((m - mx) ** 2 for _, m in pairs)
            r["beta"] = sum((m - mx) * (x - my) for x, m in pairs) / vx if vx else None
        else:
            r["beta"] = None
        sc = (scores or {}).get(r["asset_id"]) or {}
        r.update({"quant_score": sc.get("quant"), "ml_score": sc.get("ml"), "expected_return": sc.get("expected"),
                  "signal_confidence": sc.get("confidence"), "primary_horizon": sc.get("horizon")})
    long_ = sum(r["market_value"] for r in held if r["market_value"] > 0)
    short_ = -sum(r["market_value"] for r in held if r["market_value"] < 0)

    def group(key):
        g: Dict[str, float] = {}
        for r in held:
            g[r.get(key) or "Other"] = g.get(r.get(key) or "Other", 0.0) + r["market_value"]
        if key == "asset_class" and h["cash"]:
            g["Cash"] = g.get("Cash", 0.0) + h["cash"]
        if key == "currency" and h["cash"]:
            g["USD"] = g.get("USD", 0.0) + h["cash"]
        return dict(sorted(g.items(), key=lambda kv: -abs(kv[1])))
    dur_w = [(r["duration"], r["market_value"]) for r in held if r.get("duration")]
    duration = (sum(d * v for d, v in dur_w) / nav) if nav and dur_w else 0.0
    beta_p = sum((r["beta"] or 0.0) * r["market_value"] for r in held) / nav if nav else None
    corr = correlation_from_cov(cov) if cov else []
    factors = {}
    if ps:
        fr = factor_returns(panel, 756, weekly=True)
        pw = _weekly({"p": ps})["p"]
        m = min(len(pw), *(len(v) for v in fr.values())) if fr else 0
        if m > 30:
            factors = regress(pw[-m:], {k: v[-m:] for k, v in fr.items()})
    hist = ledger.nav_history()
    nav_r = []
    for k in range(1, len(hist["nav"])):
        a, b = hist["nav"][k - 1], hist["nav"][k]
        nav_r.append(((b - hist["flows"][k]) / a - 1) if a else None)
    actual = risk_stats(nav_r, rf) if len(nav_r) >= 30 else {}
    return {"as_of": cal[-1], "nav": nav, "cash": h["cash"], "contributed": h["contributed"], "realized": h["realized"],
            "unrealized": sum(r["unrealized"] for r in held), "pnl": nav - h["contributed"],
            "gross": long_ + short_, "net": long_ - short_, "long": long_, "short": short_,
            "allocation": group("asset_class"), "sector": group("sector"), "country": group("country"), "currency": group("currency"),
            "duration": duration, "beta": beta_p, "risk": risk, "risk_actual": actual, "volatility": sig,
            "positions": sorted(rows, key=lambda r: -abs(r["market_value"])),
            "correlation": {"assets": assets, "matrix": corr}, "factors": factors, "rf": rf,
            "history": {"dates": hist["dates"][-2520:], "nav": hist["nav"][-2520:]}}
