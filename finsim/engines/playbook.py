"""The playbook: named multi-leg strategies (stock, listed options, futures, total return swaps) that preview as one
package and execute as one click.

Each strategy is a list of legs with rules rather than numbers: a strike as a percentage from spot ("7% below"),
an expiry as a tenor, a size relative to the base position. `preview` resolves the rules against today's chain
and quotes every leg (bid for sells, ask for buys), adds up cash, premium, delta and incremental margin, and
computes the payoff at expiry on a price grid so the page can draw it and state max gain, max loss and
breakevens. `execute` places every leg: stock and futures orders, option orders tagged with the strategy, a
locate-and-borrow before a short sale, an RFQ dealt with the best dealer for a swap. Legs are independent
instructions (a short sale that cannot be borrowed fails alone), so the result reports each leg's outcome.
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..domain.models import Portfolio
from ..money import D, money, ZERO

# leg: kind (STOCK | OPTION | FUTURE | TRS), side, and rules
# option rules: type C/P, strike "ATM" | "pct:<n>" (n% from spot, negative below) | "zero_cost" (call strike whose bid pays for the put)
# size: multiple of the base units (stock/TRS shares; options: contracts = units × size / 100; futures: notional-matched contracts × size)
GROUPS = [("long_hedge", "Long stock + hedge"), ("short_hedge", "Short stock + hedge"), ("income", "Income"), ("bull", "Bullish spreads"),
          ("bear", "Bearish spreads"), ("vol", "Volatility"), ("trs", "Total return swaps"), ("futures", "Futures"), ("rv", "Relative value"), ("adv", "Advanced")]

S = lambda side, size=1.0: {"kind": "STOCK", "side": side, "size": size}
O = lambda side, typ, strike, size=1.0: {"kind": "OPTION", "side": side, "type": typ, "strike": strike, "size": size}
F = lambda side, size=1.0: {"kind": "FUTURE", "side": side, "size": size}
T = lambda side, size=1.0: {"kind": "TRS", "side": side, "size": size}       # BUY = receive total return

STRATEGIES: List[Dict] = [
    # ---- long stock + hedge
    {"key": "PROTECTIVE_PUT", "group": "long_hedge", "title": "Protective put", "thesis": "Bullish, but you want a floor. Own the stock and buy a put below the market; the put pays if the stock falls through the strike.",
     "legs": [S("BUY"), O("BUY", "P", "pct:-7")], "notes": "Closer strikes protect more and cost more: 1–2% below is strong, 7% moderate, 15% cheap crash insurance."},
    {"key": "PROTECTIVE_PUT_ATM", "group": "long_hedge", "title": "Protective put, at the money", "thesis": "Own the stock and buy a put right at the market: much stronger protection, much more expensive.",
     "legs": [S("BUY"), O("BUY", "P", "ATM")]},
    {"key": "TAIL_HEDGE", "group": "long_hedge", "title": "Tail-risk hedge", "thesis": "Own the stock and buy a put far below the market: cheap insurance against a catastrophe that does little for a normal 5–10% decline.",
     "legs": [S("BUY"), O("BUY", "P", "pct:-17")]},
    {"key": "COLLAR", "group": "long_hedge", "title": "Collar", "thesis": "Bullish but willing to give up extreme upside to make protection cheaper: own the stock, buy a put below, sell a call above.",
     "legs": [S("BUY"), O("BUY", "P", "pct:-7"), O("SELL", "C", "pct:+10")]},
    {"key": "ZERO_COST_COLLAR", "group": "long_hedge", "title": "Zero-cost collar", "thesis": "A collar whose sold call pays for the bought put: the call strike is searched until its premium matches the put's.",
     "legs": [S("BUY"), O("BUY", "P", "pct:-7"), O("SELL", "C", "zero_cost")]},
    {"key": "PUT_SPREAD_HEDGE", "group": "long_hedge", "title": "Put-spread hedge", "thesis": "Crash protection without paying for unlimited insurance: buy a put below the market and sell one farther below. Protection works between the two strikes and stops increasing beyond the lower one.",
     "legs": [S("BUY"), O("BUY", "P", "pct:-7"), O("SELL", "P", "pct:-20")]},
    {"key": "LONG_PLUS_CALL", "group": "adv", "title": "Long stock + call (extra upside)", "thesis": "Own the stock and buy a call above the market. The call is not a hedge: it is additional bullish exposure.",
     "legs": [S("BUY"), O("BUY", "C", "pct:+7")]},
    {"key": "LONG_FUTURES_HEDGE", "group": "futures", "title": "Long stock + temporary futures hedge", "thesis": "Long-term bullish but worried about the next month: keep the stock and short index futures for a while instead of selling.",
     "legs": [S("BUY"), F("SELL")]},
    # ---- short stock + hedge
    {"key": "PROTECTED_SHORT", "group": "short_hedge", "title": "Protected short", "thesis": "Short the stock and buy a call above the market: the call protects you if the stock explodes upward. Higher strike = cheaper but more pain first.",
     "legs": [S("SELL"), O("BUY", "C", "pct:+10")]},
    {"key": "PROTECTED_SHORT_ATM", "group": "short_hedge", "title": "Strongly protected short", "thesis": "Short the stock and buy a call near the market: much tighter protection.",
     "legs": [S("SELL"), O("BUY", "C", "pct:+2")]},
    {"key": "SHORT_TAIL_HEDGE", "group": "short_hedge", "title": "Short catastrophe hedge", "thesis": "Short the stock and buy a call far above the market: very cheap, only protects against a monster rally.",
     "legs": [S("SELL"), O("BUY", "C", "pct:+20")]},
    {"key": "SHORT_COLLAR", "group": "short_hedge", "title": "Short collar", "thesis": "Protect the short from a rally and finance the hedge by giving up some downside profit: short the stock, buy a call above, sell a put below.",
     "legs": [S("SELL"), O("BUY", "C", "pct:+10"), O("SELL", "P", "pct:-10")]},
    {"key": "CALL_SPREAD_HEDGE", "group": "short_hedge", "title": "Call-spread hedge for a short", "thesis": "Cheaper than a bought call: buy a call above the market and sell one farther above. Protection works between the strikes and stops beyond the higher one.",
     "legs": [S("SELL"), O("BUY", "C", "pct:+7"), O("SELL", "C", "pct:+20")]},
    {"key": "SHORT_PLUS_PUT", "group": "adv", "title": "Short stock + put (extra downside)", "thesis": "Short the stock and buy a put below the market. The put does not hedge the short: it makes you even more bearish.",
     "legs": [S("SELL"), O("BUY", "P", "pct:-7")]},
    # ---- income
    {"key": "COVERED_CALL", "group": "income", "title": "Covered call", "thesis": "You own the stock and expect only a moderate rise: sell a call above the market and collect the premium. Above the strike you give up further upside. Not a downside hedge.",
     "legs": [S("BUY"), O("SELL", "C", "pct:+10")]},
    {"key": "PARTIAL_COVERED_CALL", "group": "income", "title": "Partial covered call", "thesis": "Own 1,000 shares but write calls on only half of them: cap some upside, keep the rest.",
     "legs": [S("BUY"), O("SELL", "C", "pct:+10", 0.5)]},
    {"key": "CASH_SECURED_PUT", "group": "income", "title": "Cash-secured put", "thesis": "You would own the stock, but only if it falls: sell a put below the market. Stays above → keep the premium; falls below → you end up buying near the strike, less the premium.",
     "legs": [O("SELL", "P", "pct:-7")]},
    # ---- bullish spreads
    {"key": "BULL_CALL_SPREAD", "group": "bull", "title": "Bull call spread", "thesis": "Bullish without needing unlimited upside: buy a call near the market and sell one farther above. Cheaper than the call alone; upside capped at the higher strike.",
     "legs": [O("BUY", "C", "pct:+3"), O("SELL", "C", "pct:+13")]},
    {"key": "BULL_PUT_SPREAD", "group": "bull", "title": "Bull put spread", "thesis": "Bullish or neutral and you want premium: sell a put below the market and buy one farther below. Money if the stock stays strong; the bought put caps the damage.",
     "legs": [O("SELL", "P", "pct:-7"), O("BUY", "P", "pct:-13")]},
    # ---- bearish spreads
    {"key": "BEAR_PUT_SPREAD", "group": "bear", "title": "Bear put spread", "thesis": "Bearish without needing unlimited downside profit: buy a put near the market and sell one farther below. Gains stop once the stock is below the lower strike.",
     "legs": [O("BUY", "P", "pct:-3"), O("SELL", "P", "pct:-13")]},
    {"key": "BEAR_CALL_SPREAD", "group": "bear", "title": "Bear call spread", "thesis": "Bearish or neutral and you want premium: sell a call above the market and buy one farther above. Ideal if the stock stays below the sold strike; the bought call limits a squeeze.",
     "legs": [O("SELL", "C", "pct:+7"), O("BUY", "C", "pct:+13")]},
    # ---- volatility
    {"key": "LONG_STRADDLE", "group": "vol", "title": "Long straddle", "thesis": "A huge move is coming but you do not know which way: buy a call and a put at the money. A big rally → the call wins; a crash → the put wins; nothing → both decay.",
     "legs": [O("BUY", "C", "ATM"), O("BUY", "P", "ATM")]},
    {"key": "LONG_STRANGLE", "group": "vol", "title": "Long strangle", "thesis": "Same thesis, cheaper: buy a call above and a put below the market. Needs a larger move than the straddle.",
     "legs": [O("BUY", "C", "pct:+7"), O("BUY", "P", "pct:-7")]},
    {"key": "SHORT_STRADDLE", "group": "vol", "title": "Short straddle", "thesis": "You expect the stock to barely move: sell a call and a put at the money. Very large risk if it moves hard either way; a learning exercise you would normally define with wings.",
     "legs": [O("SELL", "C", "ATM"), O("SELL", "P", "ATM")]},
    {"key": "IRON_CONDOR", "group": "vol", "title": "Iron condor", "thesis": "The stock stays in a range and you want defined risk: sell a put and a call around the market, buy a put and a call farther out. Premium from the two sold options; the two bought ones cap the loss.",
     "legs": [O("BUY", "P", "pct:-13"), O("SELL", "P", "pct:-7"), O("SELL", "C", "pct:+7"), O("BUY", "C", "pct:+13")]},
    # ---- TRS
    {"key": "SYNTHETIC_LONG_TRS", "group": "trs", "title": "Synthetic long (receive TRS)", "thesis": "Receive the total return on the stock through a swap: economically long without owning the shares, for a financing spread.",
     "legs": [T("BUY")]},
    {"key": "SYNTHETIC_SHORT_TRS", "group": "trs", "title": "Synthetic short (pay TRS)", "thesis": "Pay the total return: economically short without borrowing the shares yourself.",
     "legs": [T("SELL")]},
    {"key": "PROTECTED_SYNTHETIC_LONG", "group": "trs", "title": "Protected synthetic long", "thesis": "Receive the TRS and buy a put below the market: the same logic as stock plus put, without the shares.",
     "legs": [T("BUY"), O("BUY", "P", "pct:-7")]},
    {"key": "PROTECTED_SYNTHETIC_SHORT", "group": "trs", "title": "Protected synthetic short", "thesis": "Pay the TRS and buy a call above the market: short stock plus call, without the borrow.",
     "legs": [T("SELL"), O("BUY", "C", "pct:+10")]},
    {"key": "CORE_PLUS_OVERLAY", "group": "trs", "title": "Long stock, protected, plus TRS overlay", "thesis": "Own the stock, protect it with a put, and add unprotected bullish exposure through a received TRS on top.",
     "legs": [S("BUY"), O("BUY", "P", "pct:-7"), T("BUY")]},
    {"key": "DELTA_NEUTRAL_TRS", "group": "trs", "title": "Delta-neutral TRS (financing basis)", "thesis": "Receive the TRS and short the same amount of stock: direction largely cancels; what is left is the financing and basis economics.",
     "legs": [T("BUY"), S("SELL")]},
    # ---- futures
    {"key": "LONG_FUTURES", "group": "futures", "title": "Bullish through futures", "thesis": "Buy index futures: linear exposure, no strike, margin instead of cash.", "legs": [F("BUY")]},
    {"key": "SHORT_FUTURES", "group": "futures", "title": "Bearish through futures", "thesis": "Sell index futures: linear short exposure.", "legs": [F("SELL")]},
    {"key": "LONG_FUTURES_PROTECTED", "group": "futures", "title": "Long futures with crash protection", "thesis": "Buy index futures and buy a put on the index below the level you can tolerate.",
     "legs": [F("BUY"), O("BUY", "P", "pct:-7")]},
    {"key": "SHORT_FUTURES_PROTECTED", "group": "futures", "title": "Short futures with rally protection", "thesis": "Sell index futures and buy a call above the market: short stock plus call, in futures form.",
     "legs": [F("SELL"), O("BUY", "C", "pct:+10")]},
    # ---- relative value (a second name against the underlying)
    {"key": "RV_LONG_OTHER_SHORT_UNDER", "group": "rv", "title": "Other name outperforms (long other, short this)", "thesis": "Long the second name, short the underlying: you profit from the second name's return minus the underlying's, not from the market going up. E.g. QQQ vs SPY, MSFT vs SPY.",
     "legs": [{"kind": "STOCK", "side": "BUY", "size": 1.0, "other": True}, S("SELL")]},
    {"key": "RV_LONG_UNDER_SHORT_OTHER", "group": "rv", "title": "Other name underperforms (long this, short other)", "thesis": "The opposite relative-value view: long the underlying, short the second name.",
     "legs": [S("BUY"), {"kind": "STOCK", "side": "SELL", "size": 1.0, "other": True}]},
    {"key": "RV_PROTECTED", "group": "rv", "title": "Protected single-stock relative value", "thesis": "Long the second name with a put on it for company-specific risk, short the underlying for market beta.",
     "legs": [{"kind": "STOCK", "side": "BUY", "size": 1.0, "other": True}, {"kind": "OPTION", "side": "BUY", "type": "P", "strike": "pct:-7", "size": 1.0, "other": True}, S("SELL")]},
]
BY_KEY = {s["key"]: s for s in STRATEGIES}


def _f(x) -> float:
    return float(x) if x is not None else 0.0


class Playbook:
    def __init__(self, world):
        self.w = world

    # ------------------------------------------------------------------ catalogue
    def catalogue(self) -> Dict:
        return {"groups": GROUPS, "strategies": [{k: v for k, v in s.items()} for s in STRATEGIES]}

    # ------------------------------------------------------------------ resolution
    def _chain(self, under: str, tenor_months: int) -> Dict:
        from ..engines import otc_pricing as px
        w = self.w
        want = w.calendar.roll(px._add_months(w.current_date, tenor_months)).isoformat()
        return w.options.chain(under, want)

    def _strike_for(self, rule: str, S: float, strikes: List[float]) -> float:
        if not strikes:
            raise ValueError("no strikes listed")
        if rule == "ATM":
            target = S
        elif rule.startswith("pct:"):
            target = S * (1 + float(rule[4:]) / 100.0)
        else:
            target = S
        return min(strikes, key=lambda k: (abs(k - target), k))

    def _zero_cost_call(self, chain: Dict, put_premium: float, S: float) -> Optional[float]:
        """The call strike above spot whose bid comes closest to the put's ask."""
        best = None
        for r in chain["rows"]:
            if r["strike"] <= S or not r.get("C"):
                continue
            d = abs(_f(r["C"]["bid"]) - put_premium)
            if best is None or d < best[0]:
                best = (d, r["strike"])
        return best[1] if best else None

    def preview(self, pf: Portfolio, key: str, params: Dict) -> Dict:
        from ..world import CommandError
        from ..engines.options import contract_id as cid_of
        w = self.w
        strat = BY_KEY.get(str(key).upper())
        if strat is None:
            raise CommandError(f"unknown strategy {key}")
        under = str(params.get("underlying", "SPY")).upper()
        other = str(params.get("other", "QQQ")).upper()
        units = int(_f(params.get("units", 1000)))
        if units <= 0:
            raise CommandError("units must be positive")
        tenor = max(1, int(_f(params.get("tenor_months", 3))))
        overrides = params.get("strikes") or {}            # leg index -> pct or absolute strike
        legs_out: List[Dict] = []
        needs_option = any(l["kind"] == "OPTION" for l in strat["legs"])
        chains: Dict[str, Dict] = {}
        levels: Dict[str, float] = {}

        def level(sym: str) -> float:
            if sym not in levels:
                levels[sym] = w.options.underlying_level(sym) if (sym == "SPX" or sym in w.securities) else _f(w.market.last_bar(sym).close)
            return levels[sym]

        def chain(sym: str) -> Dict:
            if sym not in chains:
                chains[sym] = self._chain(sym, tenor)
            return chains[sym]

        def bar(sym: str):
            return w.market.last_bar(sym)

        fut = w.market.front_contract("ES")
        put_premium_for_zero_cost = 0.0
        expiry = None
        for i, leg in enumerate(strat["legs"]):
            sym = other if leg.get("other") else under
            row: Dict = {"index": i, "kind": leg["kind"], "side": leg["side"], "symbol": sym}
            if leg["kind"] == "STOCK":
                b = bar(sym)
                qty = int(round(units * leg.get("size", 1.0)))
                px_ = _f(b.ask if leg["side"] == "BUY" else b.bid)
                row.update({"security_id": sym, "quantity": qty, "price": px_, "cash": -qty * px_ if leg["side"] == "BUY" else qty * px_,
                            "delta_shares": qty if leg["side"] == "BUY" else -qty, "what": f"{'buy' if leg['side'] == 'BUY' else 'short'} {qty:,} {sym} at {px_:,.2f}"})
                if leg["side"] == "SELL":
                    row["what"] += " (locate and borrow first)"
            elif leg["kind"] == "TRS":
                px_ = level(sym)
                qty = int(round(units * leg.get("size", 1.0)))
                fair = w.otc.fair("TRS", {"security_id": sym, "units": qty, "tenor_years": max(1, round(tenor / 12)), "receiver": leg["side"] == "BUY"})
                row.update({"security_id": sym, "quantity": qty, "price": px_, "cash": 0.0, "notional": qty * px_, "spread_bps": fair["mid"],
                            "delta_shares": qty if leg["side"] == "BUY" else -qty,
                            "what": f"{'receive' if leg['side'] == 'BUY' else 'pay'} total return on {qty:,} {sym} (notional {qty * px_:,.0f}) vs policy {'+' if leg['side'] == 'BUY' else '−'} ~{fair['mid']:.0f}bp; dealers quote on execute"})
            elif leg["kind"] == "FUTURE":
                if fut is None:
                    raise CommandError("no index future listed")
                fb = bar(fut.id)
                spy = level("SPY")
                n = max(1, int(round(units * leg.get("size", 1.0) * level(under) / (_f(fb.close) * fut.multiplier))))
                row.update({"security_id": fut.id, "quantity": n, "price": _f(fb.close), "cash": 0.0, "margin": _f(w.securities[fut.id].initial_margin if hasattr(w.securities[fut.id], 'initial_margin') else 0) * n,
                            "notional": n * _f(fb.close) * fut.multiplier, "delta_shares": (n * fut.multiplier * _f(fb.close) / spy) * (1 if leg["side"] == "BUY" else -1),
                            "what": f"{'buy' if leg['side'] == 'BUY' else 'sell'} {n} {fut.id} ({fut.multiplier} × {_f(fb.close):,.2f} = {n * fut.multiplier * _f(fb.close):,.0f} notional, margin instead of cash)"})
            elif leg["kind"] == "OPTION":
                ch = chain(sym)
                S_ = _f(ch["level"])
                strikes = [r["strike"] for r in ch["rows"] if r.get(leg["type"])]
                rule = leg["strike"]
                ov = overrides.get(str(i))
                if ov not in (None, ""):
                    rule = f"pct:{float(ov)}" if abs(float(ov)) < 60 else None
                    K = self._strike_for(rule, S_, strikes) if rule else min(strikes, key=lambda k: abs(k - float(ov)))
                elif rule == "zero_cost":
                    K = self._zero_cost_call(ch, put_premium_for_zero_cost, S_) or self._strike_for("pct:+10", S_, strikes)
                else:
                    K = self._strike_for(rule, S_, strikes)
                cid = cid_of(sym, date.fromisoformat(ch["expiry"]), leg["type"], K)
                if cid not in w.securities:
                    raise CommandError(f"contract {cid} is not listed")
                q = w.options.quote(w.securities[cid])
                contracts = max(1, int(round(units * leg.get("size", 1.0) / 100)))
                px_ = _f(q["ask"] if leg["side"] == "BUY" else q["bid"])
                mult = 100
                cash = -px_ * mult * contracts if leg["side"] == "BUY" else px_ * mult * contracts
                if leg["side"] == "BUY" and leg["type"] == "P":
                    put_premium_for_zero_cost = px_
                sign = 1 if leg["side"] == "BUY" else -1
                row.update({"security_id": cid, "quantity": contracts, "price": px_, "cash": cash, "strike": K, "expiry": ch["expiry"], "type": leg["type"],
                            "pct_from_spot": (K / S_ - 1) * 100, "iv": q["iv"], "delta": q["delta"], "delta_shares": sign * contracts * mult * _f(q["delta"]),
                            "vega": sign * contracts * mult * _f(q["vega"]), "theta": sign * contracts * mult * _f(q["theta"]),
                            "what": f"{'buy' if leg['side'] == 'BUY' else 'sell'} {contracts} {sym} {ch['expiry']} {K:g} {'call' if leg['type'] == 'C' else 'put'} at {px_:.2f} ({(K / S_ - 1) * 100:+.1f}% from spot)"})
                expiry = ch["expiry"]
            legs_out.append(row)
        # option margin for the package
        extra = [(w.securities[l["security_id"]], D(l["quantity"] if l["side"] == "BUY" else -l["quantity"])) for l in legs_out if l["kind"] == "OPTION"]
        inc_margin = ZERO
        if extra:
            base, _ = w.options.margin_requirement(pf)
            after, _ = w.options.margin_requirement(pf, extra=extra)
            inc_margin = max(ZERO, after - base)
        cash = sum(l["cash"] for l in legs_out)
        premium = sum(l["cash"] for l in legs_out if l["kind"] == "OPTION")
        S0 = level(under)
        payoff = self._payoff(legs_out, S0, level, other)
        totals = {"net_cash_today": cash, "net_option_premium": premium, "delta_shares": sum(l.get("delta_shares", 0.0) for l in legs_out),
                  "dollar_delta": sum(l.get("delta_shares", 0.0) for l in legs_out) * S0, "vega": sum(l.get("vega", 0.0) for l in legs_out),
                  "theta": sum(l.get("theta", 0.0) for l in legs_out), "incremental_option_margin": inc_margin,
                  "futures_margin": sum(l.get("margin", 0.0) for l in legs_out if l["kind"] == "FUTURE"), "spot": S0, "expiry": expiry, "underlying": under, "units": units}
        return {"strategy": {k: v for k, v in strat.items() if k != "legs"}, "params": {"underlying": under, "other": other, "units": units, "tenor_months": tenor},
                "legs": legs_out, "totals": totals, **payoff}

    def _payoff(self, legs: List[Dict], S0: float, level, other: str) -> Dict:
        """P&L at expiry versus the underlying's price, on a ±35% grid; the second name is assumed to move with it."""
        grid = [S0 * (0.65 + 0.7 * i / 70) for i in range(71)]
        pts = []
        for ST in grid:
            pnl = 0.0
            for l in legs:
                sign = 1 if l["side"] == "BUY" else -1
                if l["kind"] in ("STOCK", "TRS"):
                    base = level(l["symbol"])
                    pnl += sign * l["quantity"] * (ST / S0 * base - base)
                elif l["kind"] == "FUTURE":
                    pnl += sign * l["quantity"] * 50 * (ST / S0 * l["price"] - l["price"])
                elif l["kind"] == "OPTION":
                    base = level(l["symbol"])
                    ST_sym = ST / S0 * base
                    intrinsic = max(0.0, ST_sym - l["strike"]) if l["type"] == "C" else max(0.0, l["strike"] - ST_sym)
                    pnl += sign * l["quantity"] * 100 * intrinsic + l["cash"]
            pts.append([round(ST, 2), round(pnl, 2)])
        vals = [p[1] for p in pts]
        unlimited_up = legs and any(l["kind"] in ("STOCK", "TRS", "FUTURE") and l["side"] == "BUY" for l in legs) and not any(l["kind"] == "OPTION" and l["type"] == "C" and l["side"] == "SELL" for l in legs)
        unlimited_down = legs and any(l["kind"] in ("STOCK", "TRS", "FUTURE") and l["side"] == "SELL" for l in legs) and not any(l["kind"] == "OPTION" and l["type"] == "C" and l["side"] == "BUY" for l in legs)
        # naked short options are unlimited too
        if any(l["kind"] == "OPTION" and l["side"] == "SELL" and l["type"] == "C" for l in legs) and not any((l["kind"] == "OPTION" and l["type"] == "C" and l["side"] == "BUY") or (l["kind"] in ("STOCK", "TRS", "FUTURE") and l["side"] == "BUY") for l in legs):
            unlimited_down = True
        breakevens = []
        for (a, pa), (b, pb) in zip(pts, pts[1:]):
            if (pa < 0 <= pb) or (pa >= 0 > pb):
                breakevens.append(round(a + (b - a) * (0 - pa) / (pb - pa) if pb != pa else a, 2))
        return {"payoff": pts, "max_gain": None if unlimited_up else max(vals), "max_loss": None if unlimited_down else min(vals), "breakevens": breakevens,
                "grid_note": "P&L at expiry versus the underlying's price (±35%); swaps and futures treated as linear, a second name assumed to move with the underlying"}

    # ------------------------------------------------------------------ execution
    def execute(self, pf: Portfolio, key: str, params: Dict) -> Dict:
        from ..world import CommandError
        w = self.w
        pv = self.preview(pf, key, params)
        results = []
        tag = pv["strategy"]["key"]
        for l in pv["legs"]:
            try:
                if l["kind"] == "STOCK":
                    if l["side"] == "SELL" and (l["symbol"] not in pf.positions or pf.positions[l["symbol"]].quantity < l["quantity"]):
                        need = l["quantity"] - int(max(0, pf.positions[l["symbol"]].quantity) if l["symbol"] in pf.positions else 0)
                        loc = w.request_locate(pf.id, l["symbol"], need)
                        got = int(min(need, _f(loc.available)))
                        if got <= 0:
                            raise CommandError(f"no {l['symbol']} available to borrow")
                        loan = w.borrow_securities(pf.id, loc.id, got)
                        qty = got + (l["quantity"] - need)
                        o = w.place_order(pf.id, l["symbol"], "SELL", qty, "MARKET", None, None, "GTC", tag)
                        results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [loc.id, loan.id, o.id], "note": f"borrowed {got:,}, sold {qty:,}" + ("" if got == need else f" (only {got:,} of {need:,} could be borrowed)")})
                    else:
                        o = w.place_order(pf.id, l["symbol"], l["side"], l["quantity"], "MARKET", None, None, "GTC", tag)
                        results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [o.id], "note": o.status})
                elif l["kind"] == "FUTURE":
                    o = w.place_order(pf.id, l["security_id"], l["side"], l["quantity"], "MARKET", None, None, "GTC", tag)
                    results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [o.id], "note": o.status})
                elif l["kind"] == "OPTION":
                    o = w.place_order(pf.id, l["security_id"], l["side"], l["quantity"], "MARKET", None, None, "GTC", tag)
                    results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [o.id], "note": o.status})
                elif l["kind"] == "TRS":
                    r = w.request_quote(pf.id, "TRS", {"security_id": l["symbol"], "units": l["quantity"], "tenor_years": max(1, round(pv["params"]["tenor_months"] / 12)), "receiver": l["side"] == "BUY"})
                    live = [q for q in r.quotes if not q.get("declined")]
                    if not live:
                        raise CommandError("no dealer quoted the swap")
                    best = min(live, key=lambda q: q["cost_vs_mid"])
                    t = w.execute_rfq(pf.id, r.id, best["dealer"])
                    results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [r.id, t.id], "note": f"dealt with {best['dealer']} at {best['level']:.1f}bp (cost vs mid {best['cost_vs_mid']:,.0f})"})
            except CommandError as e:
                results.append({"leg": l["index"], "ok": False, "what": l["what"], "ids": [], "note": str(e)})
        w.flush()
        return {"strategy": pv["strategy"], "results": results, "all_ok": all(r["ok"] for r in results), "preview": pv}
