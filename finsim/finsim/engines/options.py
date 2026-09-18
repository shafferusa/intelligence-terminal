"""Listed options: contracts, chains, quotes, margin, exercise, assignment,
expiration, strategies and analytics.

Contracts are Securities (asset_class OPTION) with a deliverable abstraction:
equity/ETF options deliver 100 shares (American, physical); index options on
the SPX level (10x the SPY ETF) are European and cash-settled. Chains are
listed deterministically (monthly third Fridays plus two quarterlies, strikes
around spot) and re-listed as spot moves and months expire.

Quotes are theoretical values from the underlying's vol surface, wrapped in a
spread that widens with distance from the money, maturity, thin underlyings
and stress. Volume and open interest are seeded functions of the same.

Clearing is modelled as: exchange -> clearinghouse -> clearing member -> this
book. Premium settles T+1 through the ordinary settlement engine; short
positions carry a transparent margin requirement (long: none; covered call:
none; verticals: width; naked: premium + 20% of underlying less OTM, floor
10%) that is swept to the clearing account with futures initial margin and is
financeable by the prime broker. Exercise and assignment create ordinary
trades in the underlying at the strike that settle through custody and cash.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..domain.events import E, Event
from ..domain.models import Bar, Portfolio, Security, Strategy
from ..engines.ledger import dr, cr
from ..engines.options_pricing import bounds_ok, greeks as calc_greeks, price as calc_price
from ..engines.pricing import interp_rate
from ..money import D, money, price as qprice, qty as qqty, ZERO

MULTIPLIER = 100
INDEX_ID = "SPX"
INDEX_SOURCE = "SPY"
INDEX_FACTOR = 10.0
from .vol import INDICES, is_index, index_source, index_factor  # noqa: E402
CRYPTO_MULTIPLIER = 1
STRIKE_RANGE = 6
OPTION_COMMISSION = D("0.65")
REGIME_MARGIN_MULT = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.0, "RATE_HIKING": 1.1, "RECESSION": 1.25, "LIQUIDITY_STRESS": 1.5}
SPREAD_BY_TIER = {"LARGE": 0.03, "MID": 0.06, "SMALL": 0.12}
STRATEGY_TEMPLATES = {
    # name: (legs as (type, side, strike_index, expiry_index, ratio), requires_shares)
    "COVERED_CALL": ([("C", "SELL", 0, 0, 1)], True),
    "PROTECTIVE_PUT": ([("P", "BUY", 0, 0, 1)], False),
    "BULL_CALL_SPREAD": ([("C", "BUY", 0, 0, 1), ("C", "SELL", 1, 0, 1)], False),
    "BEAR_PUT_SPREAD": ([("P", "BUY", 1, 0, 1), ("P", "SELL", 0, 0, 1)], False),
    "BULL_PUT_SPREAD": ([("P", "SELL", 1, 0, 1), ("P", "BUY", 0, 0, 1)], False),
    "BEAR_CALL_SPREAD": ([("C", "SELL", 0, 0, 1), ("C", "BUY", 1, 0, 1)], False),
    "LONG_STRADDLE": ([("C", "BUY", 0, 0, 1), ("P", "BUY", 0, 0, 1)], False),
    "SHORT_STRADDLE": ([("C", "SELL", 0, 0, 1), ("P", "SELL", 0, 0, 1)], False),
    "LONG_STRANGLE": ([("P", "BUY", 0, 0, 1), ("C", "BUY", 1, 0, 1)], False),
    "SHORT_STRANGLE": ([("P", "SELL", 0, 0, 1), ("C", "SELL", 1, 0, 1)], False),
    "CALENDAR_SPREAD": ([("C", "SELL", 0, 0, 1), ("C", "BUY", 0, 1, 1)], False),
    "BUTTERFLY": ([("C", "BUY", 0, 0, 1), ("C", "SELL", 1, 0, 2), ("C", "BUY", 2, 0, 1)], False),
    "IRON_CONDOR": ([("P", "BUY", 0, 0, 1), ("P", "SELL", 1, 0, 1), ("C", "SELL", 2, 0, 1), ("C", "BUY", 3, 0, 1)], False),
    "COLLAR": ([("P", "BUY", 0, 0, 1), ("C", "SELL", 1, 0, 1)], True),
    "SYNTHETIC_LONG": ([("C", "BUY", 0, 0, 1), ("P", "SELL", 0, 0, 1)], False),
    "SYNTHETIC_SHORT": ([("C", "SELL", 0, 0, 1), ("P", "BUY", 0, 0, 1)], False),
}


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def strike_step(level: float) -> float:
    if level < 25:
        return 1.0
    if level < 100:
        return 2.5
    if level < 200:
        return 5.0
    if level < 500:
        return 10.0
    if level < 1000:
        return 25.0
    if level < 5000:
        return 50.0
    return 100.0


def strike_step_future(level: float) -> float:
    """Strike spacing for options on futures (finer than equity options: commodity vol is quoted per unit)."""
    if level < 5:
        return 0.05
    if level < 25:
        return 0.25
    if level < 150:
        return 1.0
    if level < 500:
        return 5.0
    if level < 2000:
        return 10.0
    return 25.0


def cm(sec) -> int:
    """Contract multiplier: 100 shares for equity options, the futures contract size for options on futures."""
    return int(sec.multiplier)


def is_fut_opt(sec) -> bool:
    return bool(sec.is_option and (sec.deliverable or {}).get("future"))


def contract_id(under: str, expiry: date, otype: str, strike: float) -> str:
    k = f"{strike:g}".replace(".", "_")
    return f"{under}{expiry.strftime('%y%m%d')}{otype}{k}"


class OptionsEngine:
    def __init__(self, world):
        self.w = world
        self._bar_cache: Dict[Tuple[str, str], Bar] = {}
        self._quote_cache: Dict[Tuple[str, str, str], Dict] = {}
        self._seq = 50000

    def register(self) -> None:
        w = self.w
        w.on(E.OPTION_EXERCISED, lambda world, ev: self._h_exercised(ev))
        w.on(E.OPTION_ASSIGNED, lambda world, ev: self._h_assigned(ev))
        w.on(E.OPTION_EXPIRED, lambda world, ev: self._h_expired(ev))
        w.on(E.OPTION_CASH_SETTLED, lambda world, ev: None)
        w.on(E.OPTIONS_MARGIN_COMPUTED, lambda world, ev: self._h_margin(ev))
        w.on(E.STRATEGY_ENTERED, lambda world, ev: self._h_strategy(ev))
        w.on(E.STRATEGY_STATUS_CHANGED, lambda world, ev: self._h_strategy_status(ev))
        w.on(E.STOCK_SPLIT, lambda world, ev: self._h_split(ev))
        w.on(E.CONTRACT_ADJUSTED, lambda world, ev: None)

    # ------------------------------------------------------------------ underlyings & listings
    def optionable(self) -> List[str]:
        out = [s.id for s in self.w.securities.values() if s.asset_class in ("EQUITY", "ETF", "REIT", "ADR", "CRYPTO") and s.liquidity_tier == "LARGE"
               and s.shares_outstanding and (s.asset_class != "ETF" or s.adv >= 500_000) and (s.asset_class != "CRYPTO" or s.adv * float(self.w.market.last_bar(s.id).close if self.w.market.history.get(s.id) else 0) >= 1e9)]
        return sorted(out) + [i for i in INDICES if INDICES[i][0] in self.w.securities] + self.optionable_futures()

    def optionable_futures(self) -> List[str]:
        """Options on futures: the front two unexpired contracts of each physical commodity with at least 20 sessions left."""
        w = self.w
        d = w.current_date
        by_code: Dict[str, List] = {}
        for s in w.securities.values():
            if s.is_future and (s.underlying_class or "").startswith("COMMODITY") and not s.expired and s.expiry and w.market.history.get(s.id):
                if w.calendar.business_days_between(d, date.fromisoformat(s.expiry)) >= 20:
                    by_code.setdefault(s.underlying, []).append(s)
        out = []
        for code in sorted(by_code):
            for s in sorted(by_code[code], key=lambda x: x.expiry)[:2]:
                out.append(s.id)
        return out

    def is_future_underlying(self, under: str) -> bool:
        sec = self.w.securities.get(under)
        return bool(sec is not None and sec.is_future)

    def underlying_level(self, under: str, when: str = "close") -> float:
        m = self.w.market
        if is_index(under):
            b = m.last_bar(index_source(under))
            return float(getattr(b, when)) * index_factor(under)
        return float(getattr(m.last_bar(under), when))

    def underlying_yield(self, under: str) -> float:
        if self.is_future_underlying(under):
            return 0.0
        src = index_source(under)
        return self.w.securities[src].dividend_yield

    def structural_vol(self, under: str) -> float:
        if self.is_future_underlying(under):
            return self.w.securities[under].sigma_annual
        src = index_source(under)
        sec = self.w.securities[src]
        return math.sqrt((sec.beta * 0.16) ** 2 + sec.sigma_annual ** 2) if not is_index(under) else {"SPX": 0.16, "NDX": 0.21, "RUT": 0.22}.get(under, 0.16)

    def future_option_expiry(self, under: str) -> date:
        """Options on a futures contract expire three sessions before the contract's last trade date."""
        return self.w.calendar.add_business_days(date.fromisoformat(self.w.securities[under].expiry), -3)

    def expiries(self, d: date) -> List[date]:
        out = []
        y, m = d.year, d.month
        while len(out) < 4:
            e = third_friday(y, m)
            if e > d:
                out.append(self.w.calendar.roll_back(e))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        # two quarterlies beyond
        qm = [3, 6, 9, 12]
        added = 0
        while added < 2:
            if m in qm:
                e = self.w.calendar.roll_back(third_friday(y, m))
                if e not in out:
                    out.append(e)
                    added += 1
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    def ensure_listings(self, d: date) -> int:
        w = self.w
        added = 0
        for under in self.optionable():
            if not is_index(under) and not w.market.history.get(under):
                continue
            level = self.underlying_level(under)
            fut = self.is_future_underlying(under)
            step = strike_step_future(level) if fut else strike_step(level)
            atm = round(level / step) * step
            if fut:
                fsec = w.securities[under]
                exps = [self.future_option_expiry(under)]
                if exps[0] <= d:
                    continue
                if under not in w.market.vol.state:
                    w.market.vol.init_underlying(under, fsec.sigma_annual)
            else:
                exps = self.expiries(d)
            for exp in exps:
                for i in range(-STRIKE_RANGE, STRIKE_RANGE + 1):
                    k = atm + i * step
                    if k <= 0:
                        continue
                    for ot in ("C", "P"):
                        cid = contract_id(under, exp, ot, k)
                        if cid in w.securities:
                            continue
                        self._seq += 1
                        european = is_index(under)
                        crypto = (not fut and not european and w.securities[under].asset_class == "CRYPTO")
                        if fut:
                            deliverable = {"security_id": under, "quantity": 1, "future": True}
                            multiplier = fsec.multiplier
                        else:
                            deliverable = ({"cash": True, "index": under, "source": index_source(under), "factor": index_factor(under)} if european
                                           else {"security_id": under, "quantity": CRYPTO_MULTIPLIER if crypto else MULTIPLIER})
                            multiplier = CRYPTO_MULTIPLIER if crypto else MULTIPLIER
                        w.securities[cid] = Security(
                            id=cid, name=f"{under} {exp.isoformat()} {k:g} {'Call' if ot == 'C' else 'Put'}", asset_class="OPTION", market="US_OPTIONS",
                            currency="USD", country="US", sector="Options", isin=f"XO{self._seq:09d}O", cusip=f"O{self._seq:08d}", adv=self._listing_volume(under, k, level, exp, d),
                            spread_bps=0.0, liquidity_tier=w.securities[index_source(under)].liquidity_tier, underlying=under,
                            underlying_class="OPTION", multiplier=multiplier, tick_size=(fsec.tick_size if fut else 0.01), expiry=exp.isoformat(), option_type=ot, strike=float(k),
                            exercise_style="EUROPEAN" if european else "AMERICAN", settlement_style="CASH" if european else "PHYSICAL",
                            deliverable=deliverable,
                            exchange="CME (NYMEX/COMEX options)" if fut else ("Deribit-style crypto options" if crypto else "Cboe Options Exchange"), listed=d.isoformat(),
                            index_level_source=index_source(under) if european else None,
                            index_factor=index_factor(under) if european else 1.0, lot_size=1, beta=0.0, sigma_annual=0.0)
                        added += 1
        for sec in w.securities.values():
            if sec.is_option and not sec.expired and sec.expiry and date.fromisoformat(sec.expiry) < d:
                sec.expired = True
        self._bar_cache.clear()
        self._quote_cache.clear()
        return added

    def _listing_volume(self, under: str, k: float, level: float, exp: date, d: date) -> int:
        rng = random.Random(f"{self.w.seed}|ovol|{under}|{exp}|{k}")
        src = self.w.securities[index_source(under)]
        base = {"LARGE": 4000, "MID": 900, "SMALL": 150}.get(src.liquidity_tier, 500)
        if is_index(under):
            base = 15000 if under == INDEX_ID else 3000
        if src.is_future:
            base = max(100, int(src.adv * 0.15))
        dist = abs(math.log(k / level)) if level > 0 else 0
        near = max(0.25, 1.0 - (exp - d).days / 400)
        return max(5, int(base * math.exp(-8 * dist) * near * rng.uniform(0.6, 1.4)))

    # ------------------------------------------------------------------ quotes
    def curve_rate(self, T: float) -> float:
        return interp_rate(self.w.market.curve(), max(0.02, T))

    def model_inputs(self, sec: Security, S: Optional[float] = None) -> Tuple[float, float, float, float, float]:
        w = self.w
        S = S if S is not None else self.underlying_level(sec.underlying)
        T = max(0.0, (date.fromisoformat(sec.expiry) - w.current_date).days / 365.0)
        r = self.curve_rate(T)
        q = r if is_fut_opt(sec) else self.underlying_yield(sec.underlying)
        F = S * math.exp((r - q) * T)
        sigma = w.market.vol.iv(sec.underlying, sec.strike, F, T) if T > 0 else w.market.vol.state[sec.underlying].atm
        return S, T, r, q, sigma

    def theo(self, sec: Security, S: Optional[float] = None) -> Dict:
        S, T, r, q, sigma = self.model_inputs(sec, S)
        g = calc_greeks(sec.option_type, sec.exercise_style, S, sec.strike, T, r, q, sigma)
        g["underlying"] = S
        g["T"] = T
        g["r"] = r
        g["q"] = q
        return g

    def spread_pct(self, sec: Security, S: float, T: float) -> float:
        base = SPREAD_BY_TIER.get(sec.liquidity_tier, 0.08) if not is_index(sec.underlying) else 0.02
        dist = abs(math.log(sec.strike / S)) if S > 0 else 0
        stress = self.w.market.regime().spread_mult
        return min(0.6, base * (1 + 6 * dist) * (1 + 0.5 * min(1.0, T)) * stress)

    def quote(self, sec: Security, S: Optional[float] = None, session: str = "close") -> Dict:
        key = (sec.id, self.w.current_date.isoformat(), f"{session}:{S}")
        if key in self._quote_cache:
            return self._quote_cache[key]
        g = self.theo(sec, S)
        mid = g["price"]
        spr = self.spread_pct(sec, g["underlying"], g["T"])
        half = max(sec.tick_size / 2, mid * spr / 2)
        bid = max(0.0, round((mid - half) / sec.tick_size) * sec.tick_size)
        ask = round((mid + half) / sec.tick_size) * sec.tick_size
        if ask <= bid:
            ask = bid + sec.tick_size
        rng = random.Random(f"{self.w.seed}|oq|{sec.id}|{self.w.current_date.isoformat()}")
        oi = int(sec.adv * rng.uniform(4, 12))
        vol = int(sec.adv * rng.lognormvariate(0, 0.5))
        out = {**g, "bid": bid, "ask": ask, "mid": mid, "spread_pct": spr, "volume": vol, "open_interest": oi,
               "bounds_ok": bounds_ok(sec.option_type, sec.exercise_style, g["underlying"], sec.strike, g["T"], g["r"], g["q"], mid)}
        self._quote_cache[key] = out
        return out

    def option_bar(self, sec: Security) -> Bar:
        """A synthetic session bar for execution: open from the underlying's open, close from its close."""
        key = (sec.id, self.w.current_date.isoformat())
        if key in self._bar_cache:
            return self._bar_cache[key]
        w = self.w
        src = index_source(sec.underlying)
        ub = w.market.last_bar(src)
        f = index_factor(sec.underlying)
        qo = self.quote(sec, float(ub.open) * f, "open")
        qc = self.quote(sec, float(ub.close) * f, "close")
        qh = self.theo(sec, float(ub.high) * f)["price"]
        ql = self.theo(sec, float(ub.low) * f)["price"]
        hi, lo = max(qo["mid"], qc["mid"], qh, ql), min(qo["mid"], qc["mid"], qh, ql)
        bar = Bar(w.current_date.isoformat(), qprice(qo["mid"]), qprice(hi), qprice(lo), qprice(qc["mid"]), qc["volume"], qprice(qc["bid"]), qprice(qc["ask"]))
        self._bar_cache[key] = bar
        return bar

    # ------------------------------------------------------------------ chains & analytics
    def chain(self, under: str, expiry: Optional[str] = None) -> Dict:
        w = self.w
        contracts = [s for s in w.securities.values() if s.is_option and s.underlying == under and not s.expired]
        exps = sorted({s.expiry for s in contracts})
        exp = expiry or (exps[0] if exps else None)
        if exp and exps and exp not in exps:
            # a date that is not listed (the player's preferred tenor, or an expiry from another underlying) snaps to
            # the nearest listed expiry, so a preference like "three months out" always lands on a real contract
            want = date.fromisoformat(exp)
            exp = min(exps, key=lambda e: (abs((date.fromisoformat(e) - want).days), e))
        rows: Dict[float, Dict] = {}
        for s in contracts:
            if s.expiry != exp:
                continue
            q = self.quote(s)
            pos = self._pos_qty(under, s.id)
            rows.setdefault(s.strike, {"strike": s.strike})[s.option_type] = {"id": s.id, "bid": q["bid"], "ask": q["ask"], "last": round(q["mid"], 2), "iv": q["iv"],
                                                                                "delta": q["delta"], "gamma": q["gamma"], "vega": q["vega"], "theta": q["theta"],
                                                                                "volume": q["volume"], "open_interest": q["open_interest"], "intrinsic": q["intrinsic"],
                                                                                "extrinsic": q["extrinsic"], "position": pos}
        level = self.underlying_level(under)
        return {"underlying": under, "level": level, "expiries": exps, "expiry": exp, "rows": [rows[k] for k in sorted(rows)],
                "days_to_expiry": (date.fromisoformat(exp) - w.current_date).days if exp else None, "style": "EUROPEAN/CASH" if is_index(under) else "AMERICAN/PHYSICAL"}

    def _pos_qty(self, under: str, cid: str) -> Dict[str, Decimal]:
        return {pf.id: pf.positions[cid].quantity for pf in self.w.portfolios.values() if cid in pf.positions and pf.positions[cid].quantity}

    def surface(self, under: str) -> Dict:
        w = self.w
        vs = w.market.vol
        st = vs.state[under]
        S = self.underlying_level(under)
        exps = self.expiries(w.current_date)
        term = []
        for e in exps:
            T = max(1 / 365, (e - w.current_date).days / 365)
            term.append({"expiry": e.isoformat(), "T": T, "atm_iv": vs.atm_for_tenor(under, T)})
        grid = []
        for e in exps[:4]:
            T = max(1 / 365, (e - w.current_date).days / 365)
            r = self.curve_rate(T)
            F = S * math.exp((r - self.underlying_yield(under)) * T)
            row = {"expiry": e.isoformat(), "points": []}
            for mny in (0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2):
                row["points"].append({"moneyness": mny, "strike": S * mny, "iv": vs.iv(under, S * mny, F, T)})
            grid.append(row)
        src = index_source(under)
        realized = w.market.realized_vol(src)
        hist = [{"date": h[0], "atm": h[1], "skew": h[2], "term": h[3]} for h in vs.history.get(under, [])[-260:]]
        return {"underlying": under, "level": S, "atm_30d": st.atm, "skew": st.skew, "curv": st.curv, "term": st.term, "realized_20d": realized,
                "iv_minus_realized": st.atm - realized, "rank": vs.iv_rank(under), "term_structure": term, "skew_grid": grid, "history": hist,
                "structural_vol": self.structural_vol(under), "regime": w.market.regime().name}

    # ------------------------------------------------------------------ positions / greeks / margin
    def position_rows(self, pf: Portfolio) -> List[Dict]:
        rows = []
        for pos in pf.positions.values():
            if not pos.is_option or pos.quantity == 0:
                continue
            sec = self.w.securities[pos.security_id]
            q = self.quote(sec)
            n = float(pos.quantity)
            M = cm(sec)
            rows.append({"security_id": sec.id, "underlying": sec.underlying, "type": sec.option_type, "strike": sec.strike, "expiry": sec.expiry,
                         "style": sec.exercise_style, "contracts": pos.quantity, "average_cost": (pos.cost_basis / (pos.quantity * M)).quantize(D("0.0001")) if pos.quantity else ZERO,
                         "mark": pos.mark, "market_value": pos.market_value, "on_future": is_fut_opt(sec), "multiplier": M,
                         "unrealized": pos.unrealized_pnl, "realized": pos.realized_pnl, "iv": q["iv"], "delta": q["delta"] * n * M,
                         "gamma": q["gamma"] * n * M, "vega": q["vega"] * n * M, "theta": q["theta"] * n * M, "rho": q["rho"] * n * M,
                         "delta_shares": q["delta"] * n * M, "dollar_delta": q["delta"] * n * M * q["underlying"],
                         "days_to_expiry": (date.fromisoformat(sec.expiry) - self.w.current_date).days, "intrinsic": q["intrinsic"], "extrinsic": q["extrinsic"],
                         "margin": pos.margin_requirement, "covered": pos.covered_by_shares})
        return rows

    def aggregate_greeks(self, pf: Portfolio) -> Dict:
        rows = self.position_rows(pf)
        by_under: Dict[str, Dict[str, float]] = {}
        tot = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0, "dollar_delta": 0.0}
        for r in rows:
            u = by_under.setdefault(r["underlying"], {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0, "dollar_delta": 0.0})
            for k in u:
                u[k] += float(r[k])
                tot[k] += float(r[k])
        return {"total": tot, "by_underlying": by_under}

    def covered_shares_needed(self, pf: Portfolio, under: str) -> Decimal:
        """Shares of `under` reserved to cover short calls (so they cannot be sold or pledged while the calls are open)."""
        return sum((pos.covered_by_shares * cm(self.w.securities[pos.security_id]) for pos in pf.positions.values() if pos.is_option and pos.covered_by_shares
                    and self.w.securities[pos.security_id].underlying == under), ZERO)

    def margin_requirement(self, pf: Portfolio, extra: Optional[List[Tuple[Security, Decimal]]] = None) -> Tuple[Decimal, List[Dict]]:
        """Transparent listed-options margin. Returns (total, detail rows). `extra` simulates additional signed contracts."""
        w = self.w
        mult = REGIME_MARGIN_MULT.get(w.market.state.regime, 1.0)
        qty: Dict[str, Decimal] = {p.security_id: p.quantity for p in pf.positions.values() if p.is_option and p.quantity}
        for sec, n in (extra or []):
            qty[sec.id] = qty.get(sec.id, ZERO) + n
        by_under: Dict[str, List[Tuple[Security, Decimal]]] = {}
        for cid, n in qty.items():
            if n == 0:
                continue
            sec = w.securities[cid]
            by_under.setdefault(sec.underlying, []).append((sec, n))
        total = ZERO
        detail: List[Dict] = []
        for under, items in by_under.items():
            S = D(repr(self.underlying_level(under)))
            if self.is_future_underlying(under):
                total += self._futures_option_margin(pf, under, items, S, mult, detail)
                continue
            shares = ZERO
            if not is_index(under) and under in pf.positions:
                shares = max(ZERO, pf.positions[under].settled_quantity + pf.positions[under].pending_receive - pf.positions[under].pending_deliver
                             - pf.pledged_quantity(under))
            short_calls = sorted([(s, -n) for s, n in items if s.option_type == "C" and n < 0], key=lambda x: x[0].strike)
            long_calls = sorted([(s, n) for s, n in items if s.option_type == "C" and n > 0], key=lambda x: x[0].strike)
            short_puts = sorted([(s, -n) for s, n in items if s.option_type == "P" and n < 0], key=lambda x: -x[0].strike)
            long_puts = sorted([(s, n) for s, n in items if s.option_type == "P" and n > 0], key=lambda x: -x[0].strike)
            # 1. covered calls
            remaining_shares = shares
            for sec, n in short_calls:
                cov = min(n, remaining_shares // cm(sec))
                if cov > 0:
                    detail.append({"underlying": under, "kind": "COVERED_CALL", "contract": sec.id, "contracts": cov, "requirement": ZERO, "note": f"covered by {cov * cm(sec):,} shares"})
                    remaining_shares -= cov * cm(sec)
                uncovered = n - cov
                items_left = uncovered
                # 2. call spreads: pair with long calls (same expiry)
                for j, (lc, ln) in enumerate(long_calls):
                    if items_left <= 0 or ln <= 0 or lc.expiry != sec.expiry:
                        continue
                    pair = min(items_left, ln)
                    width = max(D(0), D(repr(lc.strike - sec.strike)))
                    req = money(width * pair * cm(sec) * D(str(mult)))
                    detail.append({"underlying": under, "kind": "CALL_SPREAD", "contract": sec.id, "contracts": pair, "requirement": req, "note": f"vs long {lc.id}: width {width}"})
                    total += req
                    long_calls[j] = (lc, ln - pair)
                    items_left -= pair
                if items_left > 0:
                    q = self.quote(sec)
                    prem = D(repr(q["mid"]))
                    otm = max(ZERO, D(repr(sec.strike)) - S)
                    req_unit = max(prem + S * D("0.20") - otm, prem + S * D("0.10"))
                    req = money(req_unit * items_left * cm(sec) * D(str(mult)))
                    detail.append({"underlying": under, "kind": "NAKED_CALL", "contract": sec.id, "contracts": items_left, "requirement": req,
                                   "note": f"premium + 20% underlying − OTM (floor 10%), × regime {mult}"})
                    total += req
            for sec, n in short_puts:
                items_left = n
                for j, (lp, ln) in enumerate(long_puts):
                    if items_left <= 0 or ln <= 0 or lp.expiry != sec.expiry:
                        continue
                    pair = min(items_left, ln)
                    width = max(D(0), D(repr(sec.strike - lp.strike)))
                    req = money(width * pair * cm(sec) * D(str(mult)))
                    detail.append({"underlying": under, "kind": "PUT_SPREAD", "contract": sec.id, "contracts": pair, "requirement": req, "note": f"vs long {lp.id}: width {width}"})
                    total += req
                    long_puts[j] = (lp, ln - pair)
                    items_left -= pair
                if items_left > 0:
                    q = self.quote(sec)
                    prem = D(repr(q["mid"]))
                    K = D(repr(sec.strike))
                    otm = max(ZERO, S - K)
                    req_unit = max(prem + S * D("0.20") - otm, prem + K * D("0.10"))
                    req = money(req_unit * items_left * cm(sec) * D(str(mult)))
                    detail.append({"underlying": under, "kind": "NAKED_PUT", "contract": sec.id, "contracts": items_left, "requirement": req,
                                   "note": f"premium + 20% underlying − OTM (floor 10% of strike), × regime {mult}; cash-secured if cash ≥ strike × 100"})
                    total += req
        return total, detail

    def _futures_option_margin(self, pf: Portfolio, under: str, items, S: Decimal, mult: float, detail: List[Dict]) -> Decimal:
        """Options on futures: a short call is covered by a long future (and a short put by a short future) one for one;
        vertical spreads need the width; naked shorts need the premium plus the future's initial margin."""
        w = self.w
        fpos = pf.positions.get(under)
        fq = fpos.quantity if fpos else ZERO
        im = w.futures.initial_margin_per_contract(w.securities[under])
        total = ZERO
        long_f = max(ZERO, fq)
        short_f = max(ZERO, -fq)
        for otype, cover in (("C", long_f), ("P", short_f)):
            shorts = sorted([(s_, -n) for s_, n in items if s_.option_type == otype and n < 0], key=lambda x: x[0].strike if otype == "C" else -x[0].strike)
            longs = sorted([(s_, n) for s_, n in items if s_.option_type == otype and n > 0], key=lambda x: x[0].strike if otype == "C" else -x[0].strike)
            remaining_cover = cover
            for sec, n in shorts:
                M = cm(sec)
                cov = min(n, remaining_cover)
                if cov > 0:
                    detail.append({"underlying": under, "kind": "COVERED_BY_FUTURE", "contract": sec.id, "contracts": cov, "requirement": ZERO,
                                   "note": f"covered by {cov} {'long' if otype == 'C' else 'short'} {under}"})
                    remaining_cover -= cov
                left = n - cov
                for j, (lo, ln) in enumerate(longs):
                    if left <= 0 or ln <= 0 or lo.expiry != sec.expiry:
                        continue
                    pair = min(left, ln)
                    width = max(D(0), D(repr((lo.strike - sec.strike) if otype == "C" else (sec.strike - lo.strike))))
                    req = money(width * pair * M * D(str(mult)))
                    detail.append({"underlying": under, "kind": f"{'CALL' if otype == 'C' else 'PUT'}_SPREAD", "contract": sec.id, "contracts": pair, "requirement": req,
                                   "note": f"vs long {lo.id}: width {width}"})
                    total += req
                    longs[j] = (lo, ln - pair)
                    left -= pair
                if left > 0:
                    prem = D(repr(self.quote(sec)["mid"]))
                    req = money((prem * M + im) * left * D(str(mult)))
                    detail.append({"underlying": under, "kind": f"NAKED_{'CALL' if otype == 'C' else 'PUT'}", "contract": sec.id, "contracts": left, "requirement": req,
                                   "note": f"premium + futures initial margin {im:,.0f} per contract, × regime {mult}"})
                    total += req
        return total

    def incremental_margin(self, pf: Portfolio, sec: Security, signed: Decimal) -> Decimal:
        base, _ = self.margin_requirement(pf)
        after, _ = self.margin_requirement(pf, extra=[(sec, signed)])
        return max(ZERO, after - base)

    def incremental_margin_release(self, pf: Portfolio, sec: Security, closing: Decimal) -> Decimal:
        """Margin freed by buying `closing` contracts of a short position back."""
        base, _ = self.margin_requirement(pf)
        after, _ = self.margin_requirement(pf, extra=[(sec, closing)])
        return max(ZERO, base - after)

    def recompute_margin(self, pf: Portfolio, cause: Event) -> None:
        """Emit the portfolio's options margin requirement (and per-contract detail) when it changed."""
        w = self.w
        total, detail = self.margin_requirement(pf)
        cur = [{**d, "requirement": str(d["requirement"]), "contracts": str(d["contracts"])} for d in detail]
        prev = [{**d, "requirement": str(d["requirement"]), "contracts": str(d["contracts"])} for d in pf.options_margin_detail]
        if total != pf.options_margin or cur != prev:
            w.emit(E.OPTIONS_MARGIN_COMPUTED, {"portfolio_id": pf.id, "total": total, "detail": detail,
                                               "regime_multiplier": REGIME_MARGIN_MULT.get(w.market.state.regime, 1.0)}, cause_id=cause.id, portfolio_id=pf.id)

    def link_leg(self, pf: Portfolio, order) -> None:
        """Attach an entered order to the first unlinked strategy leg it matches (replay-safe: driven by ORDER_ENTERED)."""
        st = pf.strategies.get(order.strategy_tag)
        if st is None:
            return
        for leg in st.legs:
            if leg.get("order_id") is None and leg["security_id"] == order.security_id and leg["side"] == order.side:
                leg["order_id"] = order.id
                return

    # ------------------------------------------------------------------ commands
    def exercise(self, pf: Portfolio, contract_id_: str, quantity=None) -> Dict:
        from ..world import CommandError
        w = self.w
        sec = w.securities.get(contract_id_)
        if sec is None or not sec.is_option:
            raise CommandError(f"unknown option {contract_id_}")
        pos = pf.positions.get(sec.id)
        if pos is None or pos.quantity <= 0:
            raise CommandError("no long position to exercise")
        n = qqty(quantity) if quantity is not None else pos.quantity
        if n <= 0 or n > pos.quantity:
            raise CommandError(f"exercise quantity must be between 1 and {pos.quantity}")
        if sec.exercise_style != "AMERICAN":
            raise CommandError("European options can only be exercised at expiration (automatic)")
        if sec.expired or sec.expiry < w.current_date.isoformat():
            raise CommandError("contract has expired")
        q = self.quote(sec)
        ev = w.emit(E.OPTION_EXERCISED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": n, "strike": sec.strike, "underlying": sec.underlying,
                                         "option_type": sec.option_type, "extrinsic_forgone": money(D(repr(q["extrinsic"])) * n * cm(sec)),
                                         "note": "exercised by holder"}, portfolio_id=pf.id)
        return {"event_id": ev.id, "quantity": n}

    def _deliver(self, pf: Portfolio, sec: Security, n: Decimal, direction: str, ev: Event, reason: str) -> None:
        """Physical delivery at the strike: direction BUY = we buy the underlying (call exercise / put assignment)."""
        w = self.w
        under = w.securities[sec.deliverable["security_id"]]
        shares = n * D(sec.deliverable["quantity"])
        # for an option on a future the "delivery" is a futures position opened at the strike; it is marked to settlement tonight
        w.trading.system_order(pf, under, direction, shares, ev, reason, forced=False, fixed_price=D(repr(sec.strike)), allow_short=True)

    def _close_option(self, pf: Portfolio, sec: Security, n: Decimal, price_per_share: Decimal, ev: Event, reason: str) -> None:
        pos = pf.positions[sec.id]
        side = "SELL" if pos.quantity > 0 else "BUY"
        self.w.trading.system_order(pf, sec, side, n, ev, reason, forced=False, fixed_price=price_per_share, allow_short=True, commission_free=True)

    # ------------------------------------------------------------------ daily processing
    def process_day(self, cause: Event) -> None:
        w = self.w
        today = w.current_date.isoformat()
        for pf in w.portfolios.values():
            # expirations
            for pos in list(pf.positions.values()):
                if not pos.is_option or pos.quantity == 0:
                    continue
                sec = w.securities[pos.security_id]
                if sec.expiry == today:
                    self._expire(pf, sec, pos, cause)
            # assignments on short American positions
            for pos in list(pf.positions.values()):
                if not pos.is_option or pos.quantity >= 0:
                    continue
                sec = w.securities[pos.security_id]
                if sec.exercise_style != "AMERICAN" or sec.expiry <= today:
                    continue
                self._maybe_assign(pf, sec, pos, cause)
            # margin
            self.recompute_margin(pf, cause)
            # strategies status
            for st in pf.strategies.values():
                if st.status == "WORKING":
                    legs = [pf.orders[l["order_id"]] for l in st.legs if l.get("order_id") in pf.orders]
                    if legs and all(o.status == "FILLED" for o in legs):
                        w.emit(E.STRATEGY_STATUS_CHANGED, {"portfolio_id": pf.id, "strategy_id": st.id, "status": "FILLED", "net_premium": self._net_premium(pf, st)},
                               cause_id=cause.id, portfolio_id=pf.id)
                    elif legs and all(o.status in ("EXPIRED", "CANCELLED") for o in legs):
                        w.emit(E.STRATEGY_STATUS_CHANGED, {"portfolio_id": pf.id, "strategy_id": st.id, "status": "EXPIRED", "net_premium": ZERO}, cause_id=cause.id, portfolio_id=pf.id)

    def _expire(self, pf: Portfolio, sec: Security, pos, cause: Event) -> None:
        w = self.w
        S = self.underlying_level(sec.underlying)
        intrinsic = max(0.0, S - sec.strike) if sec.option_type == "C" else max(0.0, sec.strike - S)
        n = abs(pos.quantity)
        long = pos.quantity > 0
        if intrinsic < 0.01:
            ev = w.emit(E.OPTION_EXPIRED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity, "underlying_level": S, "intrinsic": 0.0,
                                           "outcome": "WORTHLESS"}, cause_id=cause.id, portfolio_id=pf.id)
            self._close_option(pf, sec, n, ZERO, ev, f"expired worthless ({sec.id})")
            return
        if sec.settlement_style == "CASH":
            amount = money(D(repr(intrinsic)) * n * cm(sec))
            ev = w.emit(E.OPTION_EXPIRED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity, "underlying_level": S, "intrinsic": intrinsic,
                                           "outcome": "CASH_SETTLED", "amount": amount if long else -amount}, cause_id=cause.id, portfolio_id=pf.id)
            self._close_option(pf, sec, n, D(repr(intrinsic)), ev, f"cash settlement at expiry ({sec.id}, level {S:,.2f})")
            return
        outcome = "AUTO_EXERCISED" if long else "ASSIGNED_AT_EXPIRY"
        ev = w.emit(E.OPTION_EXPIRED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity, "underlying_level": S, "intrinsic": intrinsic,
                                       "outcome": outcome}, cause_id=cause.id, portfolio_id=pf.id)
        if long:
            self._deliver(pf, sec, n, "BUY" if sec.option_type == "C" else "SELL", ev, f"auto-exercise at expiry of {sec.id}")
        else:
            self._deliver(pf, sec, n, "SELL" if sec.option_type == "C" else "BUY", ev, f"assignment at expiry of {sec.id}")
        self._close_option(pf, sec, n, ZERO, ev, f"{outcome.lower().replace('_', ' ')} ({sec.id})")

    @staticmethod
    def _scale_open_settlements(pf: Portfolio, sid: str, ratio: Decimal) -> None:
        """A whole-ratio split multiplies the units still in transit (the cash owed does not change), so the custody
        box (settled + pending receive − pending deliver) keeps matching the position after the instructions settle."""
        for si in pf.settlements.values():
            if si.security_id == sid and si.status in ("PENDING", "MATCHED", "FAILED"):
                si.quantity = qqty(si.quantity * ratio)

    def _maybe_assign(self, pf: Portfolio, sec: Security, pos, cause: Event) -> None:
        w = self.w
        q = self.quote(sec)
        S = q["underlying"]
        intrinsic = q["intrinsic"]
        if intrinsic <= 0:
            return
        extrinsic = q["extrinsic"]
        depth = intrinsic / S
        if sec.option_type == "P":
            # deep in-the-money puts are exercised early rationally (interest on the strike beats the time value)
            p = 0.01 + 0.15 * min(1.0, depth * 5) * (1.0 if extrinsic < 0.02 * S else 0.2)
        else:
            # calls are rarely exercised early except to capture a dividend (below); a little irrational exercise remains
            p = 0.02 if extrinsic < 0.005 * S else 0.005
        # dividend capture: deep ITM call before an ex-date where the dividend exceeds the remaining extrinsic value
        ex_tomorrow = False
        if sec.option_type == "C" and not is_index(sec.underlying) and not is_fut_opt(sec):
            nxt = w.calendar.next_business_day(w.current_date).isoformat()
            for ca in w.corporate_actions.values():
                if ca.security_id == sec.underlying and ca.ex_date == nxt and float(ca.amount_per_unit) > extrinsic:
                    ex_tomorrow = True
        if ex_tomorrow:
            p = 0.85
        rng = random.Random(f"{w.seed}|assign|{sec.id}|{w.current_date.isoformat()}")
        if rng.random() >= p:
            return
        n = abs(pos.quantity) if (ex_tomorrow or depth > 0.15) else max(D(1), qqty(abs(pos.quantity) * D(str(rng.choice([0.25, 0.5, 1.0])))))
        ev = w.emit(E.OPTION_ASSIGNED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": n, "underlying_level": S, "intrinsic": intrinsic, "extrinsic": extrinsic,
                                        "reason": "dividend capture: ex-date tomorrow" if ex_tomorrow else "early assignment (deep in the money)"}, cause_id=cause.id, portfolio_id=pf.id)
        if sec.settlement_style == "CASH":
            self._close_option(pf, sec, n, D(repr(intrinsic)), ev, f"assignment cash settlement ({sec.id})")
        else:
            self._deliver(pf, sec, n, "SELL" if sec.option_type == "C" else "BUY", ev, f"assignment on {sec.id}")
            self._close_option(pf, sec, n, ZERO, ev, f"assigned ({sec.id})")

    # ------------------------------------------------------------------ strategies
    def place_strategy(self, pf: Portfolio, strategy_type: str, underlying: str, expiry: str, strikes: List[float], quantity, net_limit=None,
                       expiry2: Optional[str] = None, time_in_force: str = "DAY") -> Strategy:
        from ..world import CommandError
        w = self.w
        st = strategy_type.upper()
        if st not in STRATEGY_TEMPLATES:
            raise CommandError(f"unknown strategy {st}; choose from {', '.join(STRATEGY_TEMPLATES)}")
        template, needs_shares = STRATEGY_TEMPLATES[st]
        n = qqty(quantity)
        if n <= 0:
            raise CommandError("quantity must be positive")
        exps = [expiry, expiry2 or expiry]
        legs = []
        for (ot, side, ki, ei, ratio) in template:
            if ki >= len(strikes):
                raise CommandError(f"{st} needs {max(k for _, _, k, _, _ in template) + 1} strike(s)")
            cid = contract_id(underlying, date.fromisoformat(exps[ei]), ot, float(strikes[ki]))
            if cid not in w.securities:
                raise CommandError(f"contract {cid} is not listed; pick strikes and expiries from the chain")
            legs.append({"security_id": cid, "side": side, "ratio": ratio, "quantity": n * ratio})
        if needs_shares:
            have = pf.positions[underlying].quantity if underlying in pf.positions else ZERO
            unit = 1 if self.is_future_underlying(underlying) else MULTIPLIER
            if have < n * unit:
                raise CommandError(f"{st} needs {n * unit:,} {'contracts' if unit == 1 else 'shares'} of {underlying} (have {have:,})")
        # margin/affordability check on the whole package
        extra = [(w.securities[l["security_id"]], (l["quantity"] if l["side"] == "BUY" else -l["quantity"])) for l in legs]
        base, _ = self.margin_requirement(pf)
        after, _ = self.margin_requirement(pf, extra=extra)
        debit = ZERO
        for l in legs:
            q = self.quote(w.securities[l["security_id"]])
            px = D(repr(q["ask"] if l["side"] == "BUY" else q["bid"]))
            debit += px * l["quantity"] * cm(w.securities[l["security_id"]]) * (1 if l["side"] == "BUY" else -1)
        need = max(ZERO, after - base) + max(ZERO, debit)
        why = w.prime.affordable(pf, money(need), None) if need > 0 else None
        if why:
            raise CommandError(f"strategy not financeable: {why}")
        sid = w.new_id("STR")
        w.emit(E.STRATEGY_ENTERED, {"portfolio_id": pf.id, "strategy_id": sid, "strategy_type": st, "underlying": underlying, "legs": legs, "quantity": n,
                                    "net_limit": money(net_limit) if net_limit is not None else None, "time_in_force": time_in_force.upper()}, portfolio_id=pf.id)
        strategy = pf.strategies[sid]
        for leg in list(strategy.legs):
            w.trading.enter_order(pf.id, leg["security_id"], leg["side"], leg["quantity"], "MARKET", None, None, time_in_force, sid)   # linked by ORDER_ENTERED
        return strategy

    def _net_premium(self, pf: Portfolio, st: Strategy) -> Decimal:
        total = ZERO
        for leg in st.legs:
            o = pf.orders.get(leg.get("order_id"))
            if o and o.filled_quantity:
                total += o.avg_fill_price * o.filled_quantity * cm(self.w.securities[o.security_id]) * (1 if o.side == "BUY" else -1)
        return money(total / st.quantity) if st.quantity else ZERO

    def strategy_net_at(self, pf: Portfolio, st: Strategy, session: str) -> Decimal:
        """Net debit (+) / credit (-) per unit if all legs were filled now at the session's quotes."""
        total = ZERO
        for leg in st.legs:
            sec = self.w.securities[leg["security_id"]]
            bar = self.option_bar(sec) if sec.is_option else self.w.market.last_bar(sec.id)     # futures legs (calendar spread tickets)
            px = bar.open if session == "OPEN" else bar.close
            half = (bar.ask - bar.bid) / 2
            fill = px + half if leg["side"] == "BUY" else px - half
            total += fill * leg["ratio"] * cm(sec) * (1 if leg["side"] == "BUY" else -1)
        return money(total)

    def strategy_analytics(self, pf: Portfolio, st: Strategy) -> Dict:
        w = self.w
        if st.strategy_type == "FUTURES_CALENDAR":
            return w.cdesk.spread_analytics(pf, st)
        secs = [w.securities[l["security_id"]] for l in st.legs]
        S = self.underlying_level(st.underlying)
        same_expiry = len({s.expiry for s in secs}) == 1
        net = st.net_premium if st.status == "FILLED" else self.strategy_net_at(pf, st, "CLOSE")
        greeks = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
        for l, sec in zip(st.legs, secs):
            q = self.quote(sec)
            sign = 1 if l["side"] == "BUY" else -1
            for k in greeks:
                greeks[k] += q[k] * sign * float(l["ratio"]) * cm(sec) * float(st.quantity)
        out = {"net_premium_per_unit": net, "units": st.quantity, "greeks": greeks, "defined": same_expiry,
               "note": "payoff, max gain and max loss are for the whole ticket at expiry (per-unit net x units); breakevens are underlying levels"}
        if same_expiry:
            lo, hi = S * 0.4, S * 1.6
            pts = 401
            grid = [lo + (hi - lo) * i / (pts - 1) for i in range(pts)]
            payoff = []
            units = float(st.quantity)
            for s_ in grid:
                v = -float(net)
                for l, sec in zip(st.legs, secs):
                    intrinsic = max(0.0, s_ - sec.strike) if sec.option_type == "C" else max(0.0, sec.strike - s_)
                    v += intrinsic * (1 if l["side"] == "BUY" else -1) * float(l["ratio"]) * cm(sec)
                payoff.append(v * units)      # whole ticket: per-unit payoff x units
            slope_lo = payoff[1] - payoff[0]
            slope_hi = payoff[-1] - payoff[-2]
            mx = max(payoff)
            mn = min(payoff)
            out["max_gain"] = mx if abs(slope_hi) < 1e-9 and abs(slope_lo) < 1e-9 or (slope_hi <= 1e-9 and slope_lo >= -1e-9) else None
            out["max_loss"] = mn if (slope_hi >= -1e-9 and slope_lo <= 1e-9) else None
            bes = []
            for i in range(1, pts):
                if (payoff[i - 1] < 0 <= payoff[i]) or (payoff[i - 1] >= 0 > payoff[i]):
                    x0, x1, y0, y1 = grid[i - 1], grid[i], payoff[i - 1], payoff[i]
                    bes.append(x0 + (0 - y0) * (x1 - x0) / (y1 - y0) if y1 != y0 else x0)
            out["breakevens"] = bes
            out["payoff"] = [(round(g, 2), round(p, 2)) for g, p in zip(grid[::8], payoff[::8])]
        margin, detail = self.margin_requirement(pf)
        out["margin_detail"] = [d for d in detail if any(d["contract"] == l["security_id"] for l in st.legs)]
        return out

    # ------------------------------------------------------------------ corporate action: cash-out (merger)
    def settle_underlying_at(self, under: str, price: float, cause: Event, note: str) -> None:
        """All contracts on `under` settle in cash at intrinsic value against `price` (cash merger) and are delisted."""
        w = self.w
        for pf in w.portfolios.values():
            for pos in list(pf.positions.values()):
                if not pos.is_option or pos.quantity == 0:
                    continue
                sec = w.securities[pos.security_id]
                if sec.underlying != under:
                    continue
                intrinsic = max(0.0, price - sec.strike) if sec.option_type == "C" else max(0.0, sec.strike - price)
                n = abs(pos.quantity)
                amount = money(D(repr(intrinsic)) * n * D(str(sec.multiplier)))
                ev = w.emit(E.OPTION_EXPIRED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity, "underlying_level": price, "intrinsic": intrinsic,
                                               "outcome": "CASH_SETTLED_CORPORATE", "amount": amount if pos.quantity > 0 else -amount, "note": note}, cause_id=cause.id, portfolio_id=pf.id)
                self._close_option(pf, sec, n, D(repr(intrinsic)), ev, f"{note}: contract settled at intrinsic {intrinsic:.2f}")
        for c in w.securities.values():
            if c.is_option and c.underlying == under:
                c.expired = True

    # ------------------------------------------------------------------ corporate action: split
    def apply_split(self, sec_id: str, ratio: float, cause: Optional[Event]) -> Event:
        return self.w.emit(E.STOCK_SPLIT, {"security_id": sec_id, "ratio": ratio, "name": self.w.securities[sec_id].name}, cause_id=cause.id if cause else None)

    # ------------------------------------------------------------------ handlers
    def _h_exercised(self, ev: Event) -> None:
        w = self.w
        if w.replaying:
            return      # the delivery trade and the option close-out are in the log as ordinary orders/trades caused by this event
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        sec = w.securities[p["security_id"]]
        n = D(p["quantity"])
        if sec.settlement_style == "CASH":
            q = self.quote(sec)
            self._close_option(pf, sec, n, D(repr(q["intrinsic"])), ev, f"exercise cash settlement ({sec.id})")
            return
        self._deliver(pf, sec, n, "BUY" if sec.option_type == "C" else "SELL", ev, f"exercise of {sec.id}: {'buy' if sec.option_type == 'C' else 'sell'} at strike {sec.strike:g}")
        self._close_option(pf, sec, n, ZERO, ev, f"exercised ({sec.id})")

    def _h_assigned(self, ev: Event) -> None:
        pass   # delivery and close are derived trades emitted by the command phase (not re-run on replay)

    def _h_expired(self, ev: Event) -> None:
        pass

    def _h_margin(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.options_margin = D(p["total"])
        pf.options_margin_detail = [{**d, "requirement": D(str(d["requirement"])), "contracts": D(str(d["contracts"]))} for d in p["detail"]]
        for pos in pf.positions.values():
            if pos.is_option:
                pos.margin_requirement = sum((d["requirement"] for d in pf.options_margin_detail if d["contract"] == pos.security_id), ZERO)
                cov = [d for d in pf.options_margin_detail if d["contract"] == pos.security_id and d["kind"] == "COVERED_CALL"]
                pos.covered_by_shares = cov[0]["contracts"] if cov else ZERO

    def _h_strategy(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        legs = [{"security_id": l["security_id"], "side": l["side"], "ratio": int(l["ratio"]), "quantity": D(l["quantity"]), "order_id": None} for l in p["legs"]]
        pf.strategies[p["strategy_id"]] = Strategy(id=p["strategy_id"], portfolio_id=pf.id, strategy_type=p["strategy_type"], underlying=p["underlying"], legs=legs,
                                                   quantity=D(p["quantity"]), net_limit=D(p["net_limit"]) if p.get("net_limit") is not None else None, status="WORKING",
                                                   entered_date=ev.sim_date)

    def _h_strategy_status(self, ev: Event) -> None:
        p = ev.payload
        st = self.w.portfolios[p["portfolio_id"]].strategies[p["strategy_id"]]
        st.status = p["status"]
        st.net_premium = D(p.get("net_premium", 0))
        st.notes.append({"date": ev.sim_date, "note": p.get("note", p["status"])})

    def _h_split(self, ev: Event) -> None:
        """Stock split: positions, lots and marks scale; listed contracts are adjusted (strike / ratio, contracts × ratio)."""
        w = self.w
        p = ev.payload
        sid = p["security_id"]
        ratio = D(repr(float(p["ratio"])))
        # market data
        m = w.market
        for b in m.history.get(sid, []):
            b.open, b.high, b.low, b.close = qprice(b.open / ratio), qprice(b.high / ratio), qprice(b.low / ratio), qprice(b.close / ratio)
            b.bid, b.ask = qprice(b.bid / ratio), qprice(b.ask / ratio)
        if sid in m._prev_close:
            m._prev_close[sid] = qprice(m._prev_close[sid] / ratio)
        sec = w.securities[sid]
        if sec.shares_outstanding:
            sec.shares_outstanding = int(sec.shares_outstanding * ratio)
        sec.dividend_per_share = money(sec.dividend_per_share / ratio)
        sec.adv = int(sec.adv * ratio)
        # positions
        for pf in w.portfolios.values():
            pos = pf.positions.get(sid)
            if pos:
                pos.quantity = qqty(pos.quantity * ratio)
                pos.settled_quantity = qqty(pos.settled_quantity * ratio)
                pos.pending_receive = qqty(pos.pending_receive * ratio)
                pos.pending_deliver = qqty(pos.pending_deliver * ratio)
                pos.borrowed_quantity = qqty(pos.borrowed_quantity * ratio)
                pos.mark = qprice(pos.mark / ratio)
                for lot in pos.lots:
                    lot.quantity = qqty(lot.quantity * ratio)
                    lot.original_quantity = qqty(lot.original_quantity * ratio)
                    lot.cost_per_unit = qprice(lot.cost_per_unit / ratio)
            self._scale_open_settlements(pf, sid, ratio)
            for l in pf.loans.values():
                if l.security_id == sid and l.status == "OPEN":
                    l.quantity = qqty(l.quantity * ratio)
                    l.original_quantity = qqty(l.original_quantity * ratio)
            for pl in pf.pledges:
                if pl.security_id == sid:
                    pl.quantity = qqty(pl.quantity * ratio)
            for ld in pf.lends.values():             # inventory lent out (phase 8) splits with the shares
                if ld["security_id"] == sid and ld["status"] == "OPEN":
                    ld["quantity"] = qqty(D(ld["quantity"]) * ratio)
                    ld["original_quantity"] = qqty(D(ld["original_quantity"]) * ratio)
                    if ld.get("recall_quantity"):
                        ld["recall_quantity"] = qqty(D(ld["recall_quantity"]) * ratio)
        # option contracts
        whole = float(ratio) == int(float(ratio))
        for c in list(w.securities.values()):
            if c.is_option and c.underlying == sid and not c.expired:
                old_strike = c.strike
                c.strike = float(D(repr(c.strike)) / ratio)
                if whole:
                    for pf in w.portfolios.values():
                        pos = pf.positions.get(c.id)
                        if pos:
                            pos.quantity = qqty(pos.quantity * ratio)
                            pos.settled_quantity = qqty(pos.settled_quantity * ratio)
                            pos.pending_receive = qqty(pos.pending_receive * ratio)
                            pos.pending_deliver = qqty(pos.pending_deliver * ratio)
                            pos.mark = qprice(pos.mark / ratio)
                            for lot in pos.lots:
                                lot.quantity = qqty(lot.quantity * ratio)
                                lot.original_quantity = qqty(lot.original_quantity * ratio)
                                lot.cost_per_unit = qprice(lot.cost_per_unit / ratio)
                        self._scale_open_settlements(pf, c.id, ratio)
                else:
                    c.deliverable = {**c.deliverable, "quantity": int(MULTIPLIER * float(ratio))}
                    c.multiplier = float(MULTIPLIER * float(ratio))
                w.derive(E.CONTRACT_ADJUSTED, {"security_id": c.id, "underlying": sid, "ratio": float(ratio), "old_strike": old_strike, "new_strike": c.strike,
                                               "deliverable": c.deliverable, "note": "contracts multiplied" if whole else "deliverable adjusted"}, ev)
        self._bar_cache.clear()
        self._quote_cache.clear()
