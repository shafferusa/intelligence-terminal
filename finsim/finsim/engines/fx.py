"""FX: multi-currency cash, spot trades, forwards, translation.

Each currency has its own settled cash balance in local units and a carrying
value in the base currency (the ledger account 1010:CCY). Spot trades settle
T+2 through explicit receivable/payable accounts; selling a foreign balance
realises the difference between the sale value and the carrying value; the
rest of the book is retranslated every day so unrealised FX shows separately.
Forwards price by covered interest parity off the simulated curves, are
marked daily, and settle physically at maturity into the cash ledgers.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, Optional

from ..domain.events import E, Event
from ..domain.models import FXForward, FXTrade, Portfolio
from ..engines.fx_market import CURRENCIES, SPECS
from ..engines.ledger import dr, cr
from ..money import D, money, ZERO

FX_DEALER = "Citi FX"


class FXEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.FX_TRADE_EXECUTED, lambda world, ev: self._h_executed(ev))
        w.on(E.FX_TRADE_SETTLED, lambda world, ev: self._h_settled(ev))
        w.on(E.FX_TRANSLATED, lambda world, ev: self._h_translated(ev))
        w.on(E.FX_FORWARD_OPENED, lambda world, ev: self._h_fwd_opened(ev))
        w.on(E.FX_FORWARD_MARKED, lambda world, ev: self._h_fwd_marked(ev))
        w.on(E.FX_FORWARD_SETTLED, lambda world, ev: self._h_fwd_settled(ev))

    # ------------------------------------------------------------------ helpers
    def usd(self, ccy: str) -> Decimal:
        return D(repr(self.w.market.fx.spot[ccy]))

    def to_base(self, ccy: str, amount: Decimal) -> Decimal:
        return money(amount * self.usd(ccy))

    def k(self, ccy: str) -> Decimal:
        """Base-currency units per unit of `ccy` today (1 for the base currency): the factor a foreign amount is booked at."""
        return D(1) if ccy == "USD" else self.usd(ccy)

    def quote(self, buy: str, sell: str, amount: Decimal, amount_ccy: str = "BUY", mid: Optional[float] = None) -> Dict:
        m = self.w.market.fx
        mid = m.cross(buy, sell) if mid is None else float(mid)
        spread = m.spread_bps(buy, sell) / 1e4
        rate = mid * (1 + spread / 2)          # we pay the offer for the currency we buy
        if amount_ccy == "BUY":
            buy_amount = money(amount)
            sell_amount = money(buy_amount * D(repr(rate)))
        else:
            sell_amount = money(amount)
            buy_amount = money(sell_amount / D(repr(rate)))
        return {"buy_ccy": buy, "sell_ccy": sell, "buy_amount": buy_amount, "sell_amount": sell_amount, "rate": rate, "mid": mid, "spread_bps": m.spread_bps(buy, sell)}

    # ------------------------------------------------------------------ commands
    def spot(self, pf: Portfolio, buy: str, sell: str, amount, amount_ccy: str = "BUY", execution: str = "NEXT_UPDATE", tag: Optional[str] = None) -> FXTrade:
        """A spot deal at the day's rate; with execution LIVE (a save that tracks the market) at the pair's latest quote."""
        from ..world import CommandError
        w = self.w
        buy, sell = buy.upper(), sell.upper()
        if buy not in CURRENCIES or sell not in CURRENCIES or buy == sell:
            raise CommandError(f"currencies must be two of {', '.join(CURRENCIES)}")
        if D(str(amount)) <= 0:
            raise CommandError("amount must be positive")
        live = None
        if str(execution).upper() == "LIVE":
            live = w.live.fx_mid(buy, sell)
            if live is None:
                raise CommandError(f"no live quote for {buy}/{sell} right now; deal at the day's rate instead")
        q = self.quote(buy, sell, D(str(amount)), amount_ccy.upper(), mid=live["mid"] if live else None)
        if live:
            q = {**q, "execution": "LIVE", "quote_time": live["time"], "quote_time_ny": live["time_ny"], "quote_source": live["source"]}
        avail = pf.cash_account(sell).balance
        pending_out = sum((t.sell_amount for t in pf.fx_trades.values() if t.status == "PENDING" and t.sell_ccy == sell), ZERO)
        if sell == pf.base_currency:
            avail = w.trading.projected_cash(pf, sell)
        if avail - pending_out < q["sell_amount"]:
            raise CommandError(f"insufficient {sell}: need {q['sell_amount']:,.2f}, available {avail - pending_out:,.2f}")
        sd = w.calendar.add_business_days(w.current_date, w.settlement_config.cycle_for("FX_SPOT")).isoformat()
        tid = w.new_id("FX")
        w.emit(E.FX_TRADE_EXECUTED, {"portfolio_id": pf.id, "fx_id": tid, **q, "settlement_date": sd, "usd_value": self.to_base(buy, q["buy_amount"]),
                                     "counterparty": FX_DEALER, "tag": (str(tag).strip().lstrip("#") or None) if tag else None}, portfolio_id=pf.id)
        return pf.fx_trades[tid]

    def forward(self, pf: Portfolio, buy: str, sell: str, buy_amount, maturity: str, tag: Optional[str] = None) -> FXForward:
        from ..world import CommandError
        w = self.w
        buy, sell = buy.upper(), sell.upper()
        if buy not in CURRENCIES or sell not in CURRENCIES or buy == sell:
            raise CommandError(f"currencies must be two of {', '.join(CURRENCIES)}")
        mat = date.fromisoformat(maturity)
        if mat <= w.calendar.add_business_days(w.current_date, 2):
            raise CommandError("forward maturity must be beyond spot (T+2)")
        mat = w.calendar.roll(mat)
        amt = money(buy_amount)
        if amt <= 0:
            raise CommandError("amount must be positive")
        m = w.market.fx
        fwd = m.forward(buy, sell, w.current_date, mat) * (1 + m.spread_bps(buy, sell) / 1e4)
        fid = w.new_id("FWD")
        w.emit(E.FX_FORWARD_OPENED, {"portfolio_id": pf.id, "forward_id": fid, "buy_ccy": buy, "sell_ccy": sell, "buy_amount": amt,
                                     "sell_amount": money(amt * D(repr(fwd))), "forward_rate": fwd, "maturity": mat.isoformat(), "spot": m.cross(buy, sell),
                                     "counterparty": "Citi FX Derivatives", "tag": (str(tag).strip().lstrip("#") or None) if tag else None}, portfolio_id=pf.id)
        return pf.fx_forwards[fid]

    # ------------------------------------------------------------------ valuation
    def forward_value(self, f: FXForward, asof: date) -> Decimal:
        m = self.w.market.fx
        T = max(0.0, (date.fromisoformat(f.maturity) - asof).days / 365.0)
        pv_buy = f.buy_amount * self.usd(f.buy_ccy) / D(repr(1 + m.rate[f.buy_ccy] * T))
        pv_sell = f.sell_amount * self.usd(f.sell_ccy) / D(repr(1 + m.rate[f.sell_ccy] * T))
        return money(pv_buy - pv_sell)

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event) -> None:
        w = self.w
        today = w.current_date.isoformat()
        for pf in w.portfolios.values():
            for t in list(pf.fx_trades.values()):
                if t.status == "PENDING" and t.settlement_date <= today:
                    w.emit(E.FX_TRADE_SETTLED, {"portfolio_id": pf.id, "fx_id": t.id}, cause_id=cause.id, portfolio_id=pf.id)
            for f in list(pf.fx_forwards.values()):
                if f.status != "OPEN":
                    continue
                if f.maturity <= today:
                    w.emit(E.FX_FORWARD_SETTLED, {"portfolio_id": pf.id, "forward_id": f.id, "spot_buy": self.usd(f.buy_ccy), "spot_sell": self.usd(f.sell_ccy)},
                           cause_id=cause.id, portfolio_id=pf.id)
                else:
                    v = self.forward_value(f, w.current_date)
                    if v != f.mtm:
                        w.emit(E.FX_FORWARD_MARKED, {"portfolio_id": pf.id, "forward_id": f.id, "mtm": v, "delta": v - f.mtm,
                                                     "forward_now": w.market.fx.forward(f.buy_ccy, f.sell_ccy, w.current_date, date.fromisoformat(f.maturity))},
                               cause_id=cause.id, portfolio_id=pf.id)
            # retranslate foreign balances
            for ccy, ca in pf.cash.items():
                if ccy == pf.base_currency:
                    continue
                target = self.to_base(ccy, ca.balance)
                if target != ca.base_value:
                    w.emit(E.FX_TRANSLATED, {"portfolio_id": pf.id, "currency": ccy, "balance": ca.balance, "spot": self.usd(ccy), "base_value": target,
                                             "delta": target - ca.base_value}, cause_id=cause.id, portfolio_id=pf.id)

    def exposures(self, pf: Portfolio) -> Dict:
        """Net exposure per currency in base terms: cash + pending spot legs + forward legs."""
        out: Dict[str, Decimal] = {}
        for ccy, ca in pf.cash.items():
            out[ccy] = out.get(ccy, ZERO) + ca.balance
        for t in pf.fx_trades.values():
            if t.status == "PENDING":
                out[t.buy_ccy] = out.get(t.buy_ccy, ZERO) + t.buy_amount
                out[t.sell_ccy] = out.get(t.sell_ccy, ZERO) - t.sell_amount
        for f in pf.fx_forwards.values():
            if f.status == "OPEN":
                out[f.buy_ccy] = out.get(f.buy_ccy, ZERO) + f.buy_amount
                out[f.sell_ccy] = out.get(f.sell_ccy, ZERO) - f.sell_amount
        return {ccy: {"local": amt, "base": self.to_base(ccy, amt), "spot": self.usd(ccy)} for ccy, amt in out.items()}

    # ------------------------------------------------------------------ handlers
    def _adjust_cash(self, pf: Portfolio, ccy: str, local_delta: Decimal, ev: Event, kind: str, ref: str, base_value_hint: Optional[Decimal] = None) -> Decimal:
        """Move local cash and return the base-currency carrying value moved (positive = inflow)."""
        ca = pf.cash_account(ccy)
        if local_delta >= 0:
            base = base_value_hint if base_value_hint is not None else self.to_base(ccy, local_delta)
        else:
            base = -(money(ca.base_value * (-local_delta) / ca.balance) if ca.balance and abs(local_delta) <= abs(ca.balance) else self.to_base(ccy, -local_delta))
            if ccy == pf.base_currency:
                base = local_delta
        ca.balance += local_delta
        ca.base_value += base
        self.w.record_cash_movement(pf, ccy, local_delta, kind, ref, ev)
        return base

    def _h_executed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = FXTrade(id=p["fx_id"], portfolio_id=pf.id, buy_ccy=p["buy_ccy"], sell_ccy=p["sell_ccy"], buy_amount=D(p["buy_amount"]), sell_amount=D(p["sell_amount"]),
                    rate=float(p["rate"]), trade_date=ev.sim_date, settlement_date=p["settlement_date"], status="PENDING", counterparty=p["counterparty"], usd_value=D(p["usd_value"]), tag=p.get("tag"))
        pf.fx_trades[t.id] = t
        w.post(pf.id, f"FX spot {t.id}: buy {t.buy_ccy} {t.buy_amount:,.2f} / sell {t.sell_ccy} {t.sell_amount:,.2f} @ {t.rate:.5f}, settles {t.settlement_date}",
               [dr("1250", t.usd_value, None, f"{t.buy_ccy} receivable"), cr("2350", t.usd_value, None, f"{t.sell_ccy} payable")], ev, {"fx_id": t.id, "kind": "FX"})

    def _h_settled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = pf.fx_trades[p["fx_id"]]
        usd_val = t.usd_value
        # receive the bought currency at the trade value
        self._adjust_cash(pf, t.buy_ccy, t.buy_amount, ev, "FX_SETTLEMENT", f"{t.id} receive {t.buy_ccy}", base_value_hint=usd_val)
        # deliver the sold currency at carrying value; the difference to the trade value is realised FX
        carrying = -self._adjust_cash(pf, t.sell_ccy, -t.sell_amount, ev, "FX_SETTLEMENT", f"{t.id} deliver {t.sell_ccy}")
        realized = usd_val - carrying
        t.realized_fx = realized
        t.status = "SETTLED"
        lines = [dr(f"1010:{t.buy_ccy}", usd_val, None, f"{t.buy_ccy} received"), cr("1250", usd_val, None, "receivable settled"),
                 dr("2350", usd_val, None, "payable settled"), cr(f"1010:{t.sell_ccy}", carrying, None, f"{t.sell_ccy} delivered at carrying value"),
                 cr("4650", realized, None, "realised FX on currency delivered")]
        w.post(pf.id, f"FX settlement {t.id}: {t.buy_ccy} {t.buy_amount:,.2f} in, {t.sell_ccy} {t.sell_amount:,.2f} out (realised FX {realized:+,.2f})", lines, ev, {"fx_id": t.id, "kind": "FX"})

    def _h_translated(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        ca = pf.cash_account(p["currency"])
        delta = D(p["delta"])
        ca.base_value = D(p["base_value"])
        w.post(pf.id, f"FX translation {p['currency']} {D(p['balance']):,.2f} @ {D(p['spot']):.5f}: {delta:+,.2f}",
               [dr(f"1010:{p['currency']}", delta, None, "translation"), cr("4600", delta, None, "unrealised FX")], ev, {"kind": "FX_TRANSLATION", "currency": p["currency"]})

    def _h_fwd_opened(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        f = FXForward(id=p["forward_id"], portfolio_id=pf.id, counterparty=p["counterparty"], buy_ccy=p["buy_ccy"], sell_ccy=p["sell_ccy"], buy_amount=D(p["buy_amount"]),
                      sell_amount=D(p["sell_amount"]), forward_rate=float(p["forward_rate"]), trade_date=ev.sim_date, maturity=p["maturity"], spot_at_trade=float(p["spot"]), tag=p.get("tag"))
        f.history.append({"date": ev.sim_date, "note": f"opened at forward {f.forward_rate:.5f} (spot {f.spot_at_trade:.5f})"})
        pf.fx_forwards[f.id] = f

    def _h_fwd_marked(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        f = pf.fx_forwards[p["forward_id"]]
        delta = D(p["delta"])
        f.mtm = D(p["mtm"])
        w.post(pf.id, f"FX forward mark {f.id}: MTM {f.mtm:,.2f} (change {delta:+,.2f})", [dr("1600", delta, None, "forward MTM"), cr("4700", delta, None, "unrealised forward P&L")],
               ev, {"forward_id": f.id, "kind": "FX_FORWARD"})

    def _h_fwd_settled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        f = pf.fx_forwards[p["forward_id"]]
        spot_buy, spot_sell = D(p["spot_buy"]), D(p["spot_sell"])
        buy_base = money(f.buy_amount * spot_buy)
        self._adjust_cash(pf, f.buy_ccy, f.buy_amount, ev, "FX_FORWARD", f"{f.id} receive {f.buy_ccy}", base_value_hint=buy_base)
        carrying = -self._adjust_cash(pf, f.sell_ccy, -f.sell_amount, ev, "FX_FORWARD", f"{f.id} deliver {f.sell_ccy}")
        realized = buy_base - carrying - f.mtm
        lines = [dr(f"1010:{f.buy_ccy}", buy_base, None, f"{f.buy_ccy} received"), cr(f"1010:{f.sell_ccy}", carrying, None, f"{f.sell_ccy} delivered"),
                 cr("1600", f.mtm, None, "forward MTM extinguished"), cr("4650", realized, None, "realised FX on settlement"),
                 dr("4700", f.mtm, None, "unrealised forward P&L reclassified"), cr("4650", f.mtm, None, "realised forward P&L")]
        f.status = "SETTLED"
        f.history.append({"date": ev.sim_date, "note": f"settled: {f.buy_ccy} {f.buy_amount:,.2f} received, {f.sell_ccy} {f.sell_amount:,.2f} delivered"})
        w.post(pf.id, f"FX forward settlement {f.id}", lines, ev, {"forward_id": f.id, "kind": "FX_FORWARD"})
        f.mtm = ZERO
