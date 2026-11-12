"""Option chains and the implied-volatility surface — ready for when a chain source is available.

A chain snapshot (store.option_quotes: bid, ask, last, IV, Greeks, open interest, volume per contract and date) gives:
  * the market quote of a contract: mid price, real bid/ask, volume and open interest;
  * an implied-volatility surface for the underlying: out-of-the-money options (puts below the forward, calls above),
    IV linear in log-moneyness k = ln(K/F) within each expiry (flat beyond the listed strikes) and total variance σ²T
    linear in T across expiries (flat vol beyond them) — so skew and term structure are used, not a single number;
  * skew and term-structure summaries (90%/110% moneyness skew, ATM IV by expiry, put/call richness).
Pricing order in products.Priced: an exact quote (MARKET QUOTE) → the chain's surface at (K, T) (MODEL-PRICED —
CHAIN-IMPLIED SURFACE) → the flat Cboe index volatility (MODEL-PRICED — FLAT VOLATILITY ASSUMPTION).

Import: `python -m finsim2 import-chain FILE.csv [--source NAME]` or POST /fs2/hedge/chain {"rows": [...]}; columns
asof, underlying, expiry, strike, right (C/P), bid, ask, last, iv, delta, gamma, vega, theta, rho, open_interest, volume."""
from __future__ import annotations

import csv
import datetime as _dt
import math
from typing import Dict, List, Optional

from . import pricing as px

COLUMNS = ["asof", "underlying", "expiry", "strike", "right", "bid", "ask", "last", "iv", "delta", "gamma", "vega", "theta", "rho",
           "open_interest", "volume"]


def validate_rows(rows: List[dict], source: str = "import") -> List[dict]:
    """Normalise and check chain rows; raises ValueError naming the first bad row."""
    out = []
    for n, r in enumerate(rows, 1):
        try:
            asof = _dt.date.fromisoformat(str(r["asof"])[:10]).isoformat()
            exp = _dt.date.fromisoformat(str(r["expiry"])[:10]).isoformat()
            right = str(r["right"]).strip().upper()[:1]
            if right not in ("C", "P"):
                raise ValueError("right must be C or P")
            if exp < asof:
                raise ValueError("expiry before the quote date")
            num = lambda k: (float(r[k]) if r.get(k) not in (None, "") else None)
            row = {"asof": asof, "underlying": str(r["underlying"]).strip().upper(), "expiry": exp, "strike": float(r["strike"]), "right": right,
                   **{k: num(k) for k in ("bid", "ask", "last", "iv", "delta", "gamma", "vega", "theta", "rho", "open_interest", "volume")},
                   "source": r.get("source") or source}
            if row["strike"] <= 0:
                raise ValueError("strike must be positive")
            if row["bid"] is not None and row["ask"] is not None and (row["bid"] < 0 or row["ask"] < row["bid"]):
                raise ValueError("need 0 ≤ bid ≤ ask")
            if row["iv"] is not None and not 0 < row["iv"] < 5:
                raise ValueError("iv must be a decimal between 0 and 5")
            out.append(row)
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError(f"chain row {n}: {e}") from None
    return out


def read_csv(path: str, source: str = "import") -> List[dict]:
    with open(path, newline="") as fh:
        return validate_rows(list(csv.DictReader(fh)), source)


def mid(row: dict) -> Optional[float]:
    b, a = row.get("bid"), row.get("ask")
    if b is not None and a is not None and a > 0 and a >= b:
        return 0.5 * (a + b)
    return row.get("last")


def implied_vol(right: str, S: float, K: float, T: float, r: float, q: float, price: float, style: str = "EUROPEAN") -> Optional[float]:
    """σ that reproduces `price` (bisection on the model price, which rises with σ)."""
    if price is None or price <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if right == "C" else (K - S))
    if price < intrinsic * math.exp(-r * T) - 1e-9:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(80):
        m = 0.5 * (lo + hi)
        if px.option_price(right, S, K, T, r, q, m, style) > price:
            hi = m
        else:
            lo = m
    return 0.5 * (lo + hi)


class VolSurface:
    """IV(K, T) from one chain snapshot: OTM options only, linear in log-moneyness per expiry, total variance in T."""

    def __init__(self, rows: List[dict], S: float, r: float, q: float):
        self.S, self.r, self.q = S, r, q
        self.asof = rows[0]["asof"] if rows else None
        self.source = rows[0].get("source") if rows else None
        by: Dict[str, List[tuple]] = {}
        for row in rows:
            T = max(1 / 365, px.year_frac(row["asof"], row["expiry"]))
            F = S * math.exp((r - q) * T)
            K = row["strike"]
            if (row["right"] == "P") != (K < F):          # keep out-of-the-money options only
                continue
            iv = row.get("iv")
            if iv is None:
                iv = implied_vol(row["right"], S, K, T, r, q, mid(row))
            if iv is None or not 0 < iv < 5:
                continue
            by.setdefault(row["expiry"], []).append((math.log(K / F), iv, T))
        self.smiles = {e: sorted(v) for e, v in sorted(by.items()) if v}

    def __bool__(self):
        return bool(self.smiles)

    @staticmethod
    def _interp(pts: List[tuple], k: float) -> float:
        if k <= pts[0][0]:
            return pts[0][1]
        if k >= pts[-1][0]:
            return pts[-1][1]
        for (k0, v0, _), (k1, v1, _) in zip(pts, pts[1:]):
            if k0 <= k <= k1:
                return v0 + (v1 - v0) * (k - k0) / (k1 - k0) if k1 > k0 else v0
        return pts[-1][1]

    def iv(self, K: float, T: float) -> Optional[float]:
        if not self.smiles:
            return None
        F = self.S * math.exp((self.r - self.q) * T)
        k = math.log(K / F)
        nodes = [(pts[0][2], self._interp(pts, k)) for pts in self.smiles.values()]       # (T_e, σ_e(k))
        if T <= nodes[0][0]:
            return nodes[0][1]
        if T >= nodes[-1][0]:
            return nodes[-1][1]
        for (t0, s0), (t1, s1) in zip(nodes, nodes[1:]):
            if t0 <= T <= t1:
                w = s0 * s0 * t0 + (s1 * s1 * t1 - s0 * s0 * t0) * (T - t0) / (t1 - t0)
                return math.sqrt(max(w, 1e-12) / T)
        return nodes[-1][1]

    def summary(self) -> dict:
        """ATM IV by expiry (term structure) and the 90%/110% moneyness skew of the expiry nearest 30 days."""
        term = []
        for e, pts in self.smiles.items():
            term.append({"expiry": e, "T": pts[0][2], "atm_iv": self._interp(pts, 0.0), "points": len(pts)})
        near = min(term, key=lambda t: abs(t["T"] - 30 / 365)) if term else None
        skew = None
        if near:
            pts = self.smiles[near["expiry"]]
            skew = self._interp(pts, math.log(0.9)) - self._interp(pts, math.log(1.1))
        return {"asof": self.asof, "source": self.source, "term": term, "skew_90_110": skew}
