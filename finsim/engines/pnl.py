"""Valuation, NAV and P&L explain.

Marking a position posts the change in (market value - cost) to the valuation
adjustment account, so ledger NAV always equals the economic NAV. The daily
NAV snapshot explains the change in NAV since the previous snapshot entirely
from ledger account deltas — the components sum exactly to the NAV change
less capital flows. No number is invented.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict

from ..domain.events import E, Event
from ..domain.models import NAVSnapshot, Portfolio
from ..engines.ledger import dr, cr
from ..engines.pricing import Instrument
from ..money import D, money, ZERO

PNL_ACCOUNTS = ["4000", "4100", "4200", "4300", "5000", "5100"]


class PnLEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        self.w.on(E.VALUATION_MARKED, lambda world, ev: self._h_marked(ev))
        self.w.on(E.NAV_SNAPSHOT, lambda world, ev: self._h_snapshot(ev))

    # ------------------------------------------------------------------ marks
    def mark_position(self, pf: Portfolio, security_id: str, cause: Event) -> None:
        w = self.w
        pos = pf.position(security_id)
        sec = w.securities[security_id]
        mark = w.market.last_bar(security_id).close
        inst = Instrument(sec, mark, w.current_date, w.market.curve())
        mv = inst.market_value(pos.quantity) if pos.quantity else ZERO
        target = mv - pos.cost_basis
        delta = target - pos.valuation_adjustment
        if delta == 0 and pos.mark == mark and pos.market_value == mv:
            return
        w.derive(E.VALUATION_MARKED, {"portfolio_id": pf.id, "security_id": security_id, "mark": mark, "quantity": pos.quantity, "market_value": mv,
                                      "cost_basis": pos.cost_basis, "unrealized": target, "adjustment": delta}, cause, portfolio_id=pf.id)

    def mark_all(self, cause: Event) -> None:
        for pf in self.w.portfolios.values():
            for sid in list(pf.positions):
                self.mark_position(pf, sid, cause)

    def _h_marked(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        pos = pf.position(p["security_id"])
        pos.mark = D(p["mark"])
        pos.market_value = D(p["market_value"])
        delta = D(p["adjustment"])
        pos.valuation_adjustment += delta
        if delta != 0:
            w.post(pf.id, f"Mark {p['security_id']} @ {pos.mark}: unrealized {D(p['unrealized']):,.2f} (change {delta:,.2f})",
                   [dr("1150", delta, p["security_id"], "valuation adjustment"), cr("4100", delta, p["security_id"], "unrealized gain/loss")], ev,
                   {"security_id": p["security_id"], "kind": "MTM"})

    # ------------------------------------------------------------------ NAV
    def compute_summary(self, pf: Portfolio) -> Dict:
        """Live economic summary reconstructed from state (not from stored totals)."""
        w = self.w
        led = w.ledgers[pf.id]
        cash = {c: ca.balance for c, ca in pf.cash.items()}
        mv = sum((p.market_value for p in pf.positions.values()), ZERO)
        recv = led.balance("1200") + led.balance("1210") + led.balance("1220") + led.balance("1230")
        pay = led.balance("2100") + led.balance("2300")
        long_exp = sum((p.market_value for p in pf.positions.values() if p.quantity > 0), ZERO)
        short_exp = sum((-p.market_value for p in pf.positions.values() if p.quantity < 0), ZERO)
        nav = sum(cash.values(), ZERO) + mv + recv - pay
        unreal = sum((p.unrealized_pnl for p in pf.positions.values()), ZERO)
        return {"nav": nav, "ledger_nav": led.nav(), "cash": cash, "market_value": mv, "receivables": recv, "payables": pay,
                "unrealized": unreal, "realized": led.balance("4000"), "long_exposure": long_exp, "short_exposure": short_exp,
                "gross_exposure": long_exp + short_exp, "net_exposure": long_exp - short_exp,
                "leverage": (long_exp + short_exp) / nav if nav else ZERO,
                "income": {a: led.balance(a) for a in PNL_ACCOUNTS}}

    def snapshot(self, cause: Event) -> None:
        w = self.w
        for pf in w.portfolios.values():
            led = w.ledgers[pf.id]
            s = self.compute_summary(pf)
            prev = pf.nav_history[-1] if pf.nav_history else None
            prev_bal = prev.explain.get("_balances", {}) if prev else {}
            prev_sec = prev.by_position if prev else {}
            balances = {a: led.balance(a) for a in PNL_ACCOUNTS}
            d = {a: balances[a] - D(prev_bal.get(a, 0)) for a in PNL_ACCOUNTS}
            # per-security
            by_pos: Dict[str, Dict[str, Decimal]] = {}
            eq_price = fi_price = bond_int = ZERO
            for sid in set(list(led.security_balances) + list(prev_sec)):
                cur = {a: led.security_balance(sid, a) for a in PNL_ACCOUNTS}
                pb = prev_sec.get(sid, {})
                dd = {a: cur[a] - D(pb.get("_" + a, 0)) for a in PNL_ACCOUNTS}
                price_pnl = dd["4000"] + dd["4100"]
                sec = w.securities[sid]
                if sec.is_bond:
                    fi_price += price_pnl
                    bond_int += dd["4300"]
                else:
                    eq_price += price_pnl
                pos = pf.positions.get(sid)
                by_pos[sid] = {"price_pnl": price_pnl, "realized": dd["4000"], "unrealized_change": dd["4100"], "dividends": dd["4200"],
                               "interest": dd["4300"], "commissions": -dd["5000"], "total": price_pnl + dd["4200"] + dd["4300"] - dd["5000"],
                               "quantity": pos.quantity if pos else ZERO, "market_value": pos.market_value if pos else ZERO,
                               "mark": pos.mark if pos else ZERO, **{"_" + a: cur[a] for a in PNL_ACCOUNTS}}
            cash_int = d["4300"] - bond_int - d["5100"]
            explain = {"equity_price": eq_price, "fixed_income_price": fi_price, "dividends": d["4200"], "bond_interest": bond_int,
                       "cash_interest": cash_int, "commissions": -d["5000"], "_balances": balances}
            day_pnl = eq_price + fi_price + d["4200"] + bond_int + cash_int - d["5000"]
            prev_nav = prev.nav if prev else ZERO
            payload = {"portfolio_id": pf.id, "date": w.current_date.isoformat(), "nav": s["nav"], "ledger_nav": s["ledger_nav"], "cash": s["cash"],
                       "market_value": s["market_value"], "receivables": s["receivables"], "payables": s["payables"],
                       "accrued": led.balance("1220") + led.balance("1230") - led.balance("2300"), "day_pnl": day_pnl, "explain": explain,
                       "by_position": by_pos, "capital_flows": pf.day_capital_flows, "cumulative_realized": s["realized"], "unrealized": s["unrealized"],
                       "gross_exposure": s["gross_exposure"], "net_exposure": s["net_exposure"], "long_exposure": s["long_exposure"],
                       "short_exposure": s["short_exposure"], "leverage": money(s["leverage"] * 10000) / 10000 if s["nav"] else ZERO,
                       "prev_nav": prev_nav, "reconciles": (s["nav"] - prev_nav - pf.day_capital_flows) == day_pnl}
            w.emit(E.NAV_SNAPSHOT, payload, cause_id=cause.id, portfolio_id=pf.id)

    def _h_snapshot(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        snap = NAVSnapshot(portfolio_id=pf.id, date=p["date"], nav=D(p["nav"]), cash={k: D(v) for k, v in p["cash"].items()},
                           market_value=D(p["market_value"]), receivables=D(p["receivables"]), payables=D(p["payables"]), accrued=D(p["accrued"]),
                           day_pnl=D(p["day_pnl"]), explain={k: ({kk: D(vv) for kk, vv in v.items()} if isinstance(v, dict) else D(v)) for k, v in p["explain"].items()},
                           by_position={sid: {k: D(v) for k, v in row.items()} for sid, row in p["by_position"].items()},
                           capital_flows=D(p["capital_flows"]), cumulative_realized=D(p["cumulative_realized"]), unrealized=D(p["unrealized"]),
                           gross_exposure=D(p["gross_exposure"]), net_exposure=D(p["net_exposure"]), long_exposure=D(p["long_exposure"]),
                           short_exposure=D(p["short_exposure"]), leverage=D(p["leverage"]), ledger_nav=D(p["ledger_nav"]), event_id=ev.id)
        pf.nav_history.append(snap)
