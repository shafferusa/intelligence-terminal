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

PNL_ACCOUNTS = ["4000", "4100", "4200", "4300", "4350", "4400", "4500", "4600", "4650", "4700", "5000", "5100", "5300", "5400", "5500", "5600", "5700"]


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
        if sec.is_future or not w.market.history.get(security_id):
            return
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
        sid = p["security_id"]
        led = w.ledgers[pf.id]
        pos.mark = D(p["mark"])
        pos.market_value = D(p["market_value"])
        delta = D(p["adjustment"])
        pos.valuation_adjustment += delta
        # the valuation adjustment lives in 1150 for longs and 2610 for shorts; reclassify on a flip
        short = pos.quantity < 0
        lines = []
        long_bal = led.security_balance(sid, "1150")          # debit-natural
        short_bal = led.security_balance(sid, "2610")         # credit-natural (positive = liability)
        if short and long_bal != 0:
            lines += [cr("1150", long_bal, sid, "reclassify to short adjustment"), dr("2610", long_bal, sid, "reclassify from long adjustment")]
        if not short and short_bal != 0:
            lines += [cr("2610", -short_bal, sid, "reclassify to long adjustment"), dr("1150", -short_bal, sid, "reclassify from short adjustment")]
        if delta != 0:
            if short:
                lines += [dr("2610", delta, sid, "short valuation adjustment (liability falls on gains)"), cr("4100", delta, sid, "unrealized gain/loss")]
            else:
                lines += [dr("1150", delta, sid, "valuation adjustment"), cr("4100", delta, sid, "unrealized gain/loss")]
        if lines:
            w.post(pf.id, f"Mark {sid} @ {pos.mark}: unrealized {D(p['unrealized']):,.2f} (change {delta:,.2f})", lines, ev, {"security_id": sid, "kind": "MTM"})

    @staticmethod
    def bucket_of(sec) -> str:
        if sec.is_future:
            uc = sec.underlying_class or ""
            if uc.startswith("COMMODITY"):
                return "commodities"
            if uc == "RATES":
                return "rates"
            return "equities"
        if sec.asset_class == "GOVT_BOND":
            return "rates"
        if sec.asset_class == "CORP_BOND":
            return "credit"
        return "equities"

    # ------------------------------------------------------------------ NAV
    def compute_summary(self, pf: Portfolio) -> Dict:
        """Live economic summary reconstructed from state (not from stored totals)."""
        w = self.w
        led = w.ledgers[pf.id]
        cash = {c: ca.balance for c, ca in pf.cash.items()}
        cash_base = sum((ca.base_value for ca in pf.cash.values()), ZERO)
        mv = sum((p.market_value for p in pf.positions.values() if not p.is_future), ZERO)
        recv = led.balance("1200") + led.balance("1210") + led.balance("1220") + led.balance("1230") + led.balance("1240") + led.balance("1250") + led.balance("1410")
        margin = led.balance("1300")
        collateral_posted = led.balance("1400")
        reverse_repo = led.balance("1500")
        fx_forwards = led.balance("1600")
        pay = (led.balance("2100") + led.balance("2300") + led.balance("2310") + led.balance("2320") + led.balance("2330") + led.balance("2340") + led.balance("2350"))
        repo = led.balance("2500")
        margin_loan = led.balance("2700")
        long_exp = sum((p.market_value for p in pf.positions.values() if p.quantity > 0 and not p.is_future), ZERO)
        short_exp = sum((-p.market_value for p in pf.positions.values() if p.quantity < 0 and not p.is_future), ZERO)
        fut_long = fut_short = ZERO
        for p in pf.positions.values():
            if p.is_future and p.quantity != 0:
                sec = w.securities[p.security_id]
                notional = money(abs(p.quantity) * w.market.last_bar(sec.id).close * D(str(sec.multiplier))) if w.market.history.get(sec.id) else p.notional
                p.notional = notional
                if p.quantity > 0:
                    fut_long += notional
                else:
                    fut_short += notional
        nav = cash_base + mv + recv + margin + collateral_posted + reverse_repo + fx_forwards - pay - repo - margin_loan
        unreal = sum((p.unrealized_pnl for p in pf.positions.values() if not p.is_future), ZERO)
        gross = long_exp + short_exp + fut_long + fut_short
        return {"nav": nav, "ledger_nav": led.nav(), "cash": cash, "cash_base": cash_base, "market_value": mv, "receivables": recv, "payables": pay,
                "margin_deposits": margin, "collateral_posted": collateral_posted, "reverse_repo": reverse_repo, "fx_forwards": fx_forwards,
                "repo_borrowing": repo, "margin_loan": margin_loan, "short_market_value": -short_exp,
                "unrealized": unreal, "realized": led.balance("4000") + led.balance("4400"), "long_exposure": long_exp + fut_long,
                "short_exposure": short_exp + fut_short, "futures_long": fut_long, "futures_short": fut_short,
                "gross_exposure": gross, "net_exposure": long_exp + fut_long - short_exp - fut_short,
                "leverage": gross / nav if nav else ZERO,
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
            buckets = {"equities": ZERO, "commodities": ZERO, "rates": ZERO, "credit": ZERO}
            bond_int = ZERO
            for sid in set(list(led.security_balances) + list(prev_sec)):
                cur = {a: led.security_balance(sid, a) for a in PNL_ACCOUNTS}
                pb = prev_sec.get(sid, {})
                dd = {a: cur[a] - D(pb.get("_" + a, 0)) for a in PNL_ACCOUNTS}
                price_pnl = dd["4000"] + dd["4100"] + dd["4400"]
                sec = w.securities[sid]
                buckets[self.bucket_of(sec)] += price_pnl
                if sec.is_bond:
                    bond_int += dd["4300"]
                pos = pf.positions.get(sid)
                fin_sid = dd["4350"] - dd["5300"] - dd["5700"] - dd["5400"] + dd["4500"] - dd["5500"]
                by_pos[sid] = {"price_pnl": price_pnl, "realized": dd["4000"] + dd["4400"], "unrealized_change": dd["4100"], "dividends": dd["4200"],
                               "interest": dd["4300"], "commissions": -dd["5000"], "financing": fin_sid,
                               "total": price_pnl + dd["4200"] + dd["4300"] - dd["5000"] + fin_sid,
                               "quantity": pos.quantity if pos else ZERO, "market_value": pos.market_value if pos else ZERO,
                               "mark": pos.mark if pos else ZERO, "bucket": self.bucket_of(sec), **{"_" + a: cur[a] for a in PNL_ACCOUNTS}}
            cash_int = d["4300"] - bond_int - d["5100"]
            fx = d["4600"] + d["4650"] + d["4700"]
            borrow = d["4350"] - d["5300"] - d["5700"]
            repo_fin = d["4500"] - d["5500"]
            explain = {"equities": buckets["equities"], "commodities": buckets["commodities"], "rates": buckets["rates"], "credit": buckets["credit"], "fx": fx,
                       "dividends": d["4200"], "manufactured_dividends": -d["5400"], "bond_interest": bond_int, "cash_interest": cash_int,
                       "borrow_fees": borrow, "repo_financing": repo_fin, "margin_financing": -d["5600"], "fees": -d["5000"], "_balances": balances}
            day_pnl = sum((v for k, v in explain.items() if not k.startswith("_")), ZERO)
            prev_nav = prev.nav if prev else ZERO
            payload = {"portfolio_id": pf.id, "date": w.current_date.isoformat(), "nav": s["nav"], "ledger_nav": s["ledger_nav"], "cash": s["cash"],
                       "market_value": s["market_value"], "receivables": s["receivables"], "payables": s["payables"],
                       "accrued": led.balance("1220") + led.balance("1230") - led.balance("2300"), "day_pnl": day_pnl, "explain": explain,
                       "by_position": by_pos, "capital_flows": pf.day_capital_flows, "cumulative_realized": s["realized"], "unrealized": s["unrealized"],
                       "gross_exposure": s["gross_exposure"], "net_exposure": s["net_exposure"], "long_exposure": s["long_exposure"],
                       "short_exposure": s["short_exposure"], "leverage": money(s["leverage"] * 10000) / 10000 if s["nav"] else ZERO,
                       "prev_nav": prev_nav, "reconciles": (s["nav"] - prev_nav - pf.day_capital_flows) == day_pnl,
                       "margin_deposits": s["margin_deposits"]}
            w.emit(E.NAV_SNAPSHOT, payload, cause_id=cause.id, portfolio_id=pf.id)
            for pos in pf.positions.values():
                pos.day_variation_margin = ZERO

    def _h_snapshot(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        snap = NAVSnapshot(portfolio_id=pf.id, date=p["date"], nav=D(p["nav"]), cash={k: D(v) for k, v in p["cash"].items()},
                           market_value=D(p["market_value"]), receivables=D(p["receivables"]), payables=D(p["payables"]), accrued=D(p["accrued"]),
                           day_pnl=D(p["day_pnl"]), explain={k: ({kk: D(vv) for kk, vv in v.items()} if isinstance(v, dict) else D(v)) for k, v in p["explain"].items()},
                           by_position={sid: {k: (v if k == "bucket" else D(v)) for k, v in row.items()} for sid, row in p["by_position"].items()},
                           capital_flows=D(p["capital_flows"]), cumulative_realized=D(p["cumulative_realized"]), unrealized=D(p["unrealized"]),
                           gross_exposure=D(p["gross_exposure"]), net_exposure=D(p["net_exposure"]), long_exposure=D(p["long_exposure"]),
                           short_exposure=D(p["short_exposure"]), leverage=D(p["leverage"]), ledger_nav=D(p["ledger_nav"]), event_id=ev.id)
        pf.nav_history.append(snap)
