"""Valuation, NAV and P&L explain.

Marking a position posts the change in (market value - cost) to the valuation
adjustment account, so ledger NAV always equals the economic NAV. The daily
NAV snapshot explains the change in NAV since the previous snapshot entirely
from ledger account deltas — the components sum exactly to the NAV change
less capital flows. No number is invented.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict

from ..domain.events import E, Event
from ..domain.models import NAVSnapshot, Portfolio
from ..engines.ledger import dr, cr, adj_account
from ..engines.pricing import Instrument
from ..money import D, money, ZERO

PNL_ACCOUNTS = ["4000", "4100", "4200", "4300", "4350", "4400", "4500", "4600", "4650", "4700", "4800", "4900", "4910", "5000", "5100", "5300", "5400", "5500", "5600", "5700", "5800",
                "4360", "4980", "5310", "5900", "5910", "5950", "5320"]


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
        if sec.is_future:
            return
        greeks = None
        if sec.is_option:
            if pos.quantity == 0 and pos.cost_basis == 0 and pos.valuation_adjustment == 0 and pos.market_value == 0:
                return
            if sec.expired or not w.market.history.get(sec.index_level_source or sec.underlying):
                return
            q = w.options.quote(sec)
            mark = D(repr(q["mid"])).quantize(D("0.0001"))
            mv = money(pos.quantity * mark * D(str(sec.multiplier))) if pos.quantity else ZERO
            greeks = {"delta": q["delta"], "gamma": q["gamma"], "vega": q["vega"], "theta": q["theta"], "rho": q["rho"], "iv": q["iv"],
                      "underlying": q["underlying"], "T": q["T"], "date": w.current_date.isoformat(), "intrinsic": q["intrinsic"], "extrinsic": q["extrinsic"]}
        else:
            if not w.market.history.get(security_id):
                return
            mark = w.market.last_bar(security_id).close
            inst = Instrument(sec, mark, w.current_date, w.market.curve())
            mv = inst.market_value(pos.quantity) if pos.quantity else ZERO
        target = mv - pos.cost_basis
        delta = target - pos.valuation_adjustment
        if delta == 0 and pos.mark == mark and pos.market_value == mv and (greeks is None or pos.greeks.get("date") == greeks["date"]):
            return
        payload = {"portfolio_id": pf.id, "security_id": security_id, "mark": mark, "quantity": pos.quantity, "market_value": mv,
                   "cost_basis": pos.cost_basis, "unrealized": target, "adjustment": delta}
        if greeks is not None:
            payload["greeks"] = greeks
        w.derive(E.VALUATION_MARKED, payload, cause, portfolio_id=pf.id)

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
        sec = w.securities[sid]
        pos.mark = D(p["mark"])
        pos.market_value = D(p["market_value"])
        delta = D(p["adjustment"])
        pos.valuation_adjustment += delta
        if p.get("greeks") is not None:
            pos.is_option = True
            g = {k: (v if k == "date" else float(v)) for k, v in p["greeks"].items()}
            if pos.greeks and pos.greeks.get("date") != g["date"]:
                pos.prev_greeks = pos.greeks
            pos.greeks = g
        # the valuation adjustment lives in a long account (1150 / 1750) or a short account (2610 / 2910); reclassify on a flip
        short = pos.quantity < 0
        la, sa = adj_account(sec, False), adj_account(sec, True)
        lines = []
        long_bal = led.security_balance(sid, la)          # debit-natural
        short_bal = led.security_balance(sid, sa)         # credit-natural (positive = liability)
        if short and long_bal != 0:
            lines += [cr(la, long_bal, sid, "reclassify to short adjustment"), dr(sa, long_bal, sid, "reclassify from long adjustment")]
        if not short and short_bal != 0:
            lines += [cr(sa, -short_bal, sid, "reclassify to long adjustment"), dr(la, -short_bal, sid, "reclassify from short adjustment")]
        if delta != 0:
            if short:
                lines += [dr(sa, delta, sid, "short valuation adjustment (liability falls on gains)"), cr("4100", delta, sid, "unrealized gain/loss")]
            else:
                lines += [dr(la, delta, sid, "valuation adjustment"), cr("4100", delta, sid, "unrealized gain/loss")]
        if lines:
            w.post(pf.id, f"Mark {sid} @ {pos.mark}: unrealized {D(p['unrealized']):,.2f} (change {delta:,.2f})", lines, ev, {"security_id": sid, "kind": "MTM"})

    @staticmethod
    def bucket_of(sec) -> str:
        if sec.is_option:
            return "commodities" if (sec.deliverable or {}).get("future") else "options"
        if sec.is_future:
            uc = sec.underlying_class or ""
            if uc.startswith("COMMODITY"):
                return "commodities"
            if uc == "RATES":
                return "rates"
            return "equities"
        if sec.asset_class == "PHYSICAL":
            return "commodities"
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
        recv = led.balance("1200") + led.balance("1210") + led.balance("1220") + led.balance("1230") + led.balance("1240") + led.balance("1250") + led.balance("1410") + led.balance("1430")
        margin = led.balance("1300")
        collateral_posted = led.balance("1400")
        reverse_repo = led.balance("1500")
        fx_forwards = led.balance("1600")
        pay = (led.balance("2100") + led.balance("2300") + led.balance("2310") + led.balance("2320") + led.balance("2330") + led.balance("2340") + led.balance("2350")
               + led.balance("2360") + led.balance("2370") + led.balance("2380") + led.balance("2460"))
        repo = led.balance("2500")
        margin_loan = led.balance("2700")
        otc_assets = led.balance("1800")
        otc_liabilities = led.balance("2800")
        operating_assets = led.balance("1900")
        vm_received = led.balance("2450")
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
        nav = (cash_base + mv + recv + margin + collateral_posted + reverse_repo + fx_forwards + otc_assets + operating_assets
               - otc_liabilities - vm_received - pay - repo - margin_loan)
        unreal = sum((p.unrealized_pnl for p in pf.positions.values() if not p.is_future), ZERO)
        gross = long_exp + short_exp + fut_long + fut_short
        return {"nav": nav, "ledger_nav": led.nav(), "cash": cash, "cash_base": cash_base, "market_value": mv, "receivables": recv, "payables": pay,
                "margin_deposits": margin, "collateral_posted": collateral_posted, "reverse_repo": reverse_repo, "fx_forwards": fx_forwards,
                "repo_borrowing": repo, "margin_loan": margin_loan, "short_market_value": -short_exp, "otc_assets": otc_assets,
                "otc_liabilities": otc_liabilities, "vm_received": vm_received, "operating_assets": operating_assets,
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
            buckets = {"equities": ZERO, "commodities": ZERO, "rates": ZERO, "credit": ZERO, "options": ZERO, "fx": ZERO}
            bond_int = ZERO
            for sid in set(list(led.security_balances) + list(prev_sec)):
                cur = {a: led.security_balance(sid, a) for a in PNL_ACCOUNTS}
                pb = prev_sec.get(sid, {})
                dd = {a: cur[a] - D(pb.get("_" + a, 0)) for a in PNL_ACCOUNTS}
                price_pnl = dd["4000"] + dd["4100"] + dd["4400"] + dd["4800"] + dd["4900"] + dd["4910"]
                sec = w.securities.get(sid)
                bucket = self.bucket_of(sec) if sec is not None else w.otc.bucket_of(sid)
                buckets[bucket] += price_pnl
                if sec is not None and sec.is_bond:
                    bond_int += dd["4300"]
                pos = pf.positions.get(sid)
                fin_sid = dd["4350"] - dd["5300"] - dd["5700"] - dd["5400"] + dd["4500"] - dd["5500"]
                by_pos[sid] = {"price_pnl": price_pnl, "realized": dd["4000"] + dd["4400"], "unrealized_change": dd["4100"], "dividends": dd["4200"],
                               "interest": dd["4300"], "commissions": -dd["5000"] - dd["5800"], "financing": fin_sid,
                               "total": price_pnl + dd["4200"] + dd["4300"] - dd["5000"] - dd["5800"] + fin_sid,
                               "quantity": pos.quantity if pos else ZERO, "market_value": pos.market_value if pos else ZERO,
                               "mark": pos.mark if pos else ZERO, "bucket": bucket, **{"_" + a: cur[a] for a in PNL_ACCOUNTS}}
            cash_int = d["4300"] - bond_int - d["5100"]
            fx = d["4600"] + d["4650"] + d["4700"] + buckets["fx"]      # cash translation, spot realisation, forwards, cross-currency swaps
            borrow = d["4350"] - d["5300"] - d["5700"]
            repo_fin = d["4500"] - d["5500"]
            explain = {"equities": buckets["equities"], "commodities": buckets["commodities"], "rates": buckets["rates"], "credit": buckets["credit"],
                       "options": buckets["options"], "fx": fx,
                       "dividends": d["4200"], "manufactured_dividends": -d["5400"], "bond_interest": bond_int, "cash_interest": cash_int,
                       "borrow_fees": borrow, "repo_financing": repo_fin, "margin_financing": -d["5600"], "fees": -d["5000"] - d["5800"],
                       "lending_income": d["4360"] - d["5310"], "fund_fees": -d["5900"] - d["5910"], "operating": d["4980"] - d["5950"], "storage": -d["5320"], "_balances": balances}
            day_pnl = sum((v for k, v in explain.items() if not k.startswith("_")), ZERO)
            greek_attr = self.greek_attribution(pf, buckets["options"])
            prev_nav = prev.nav if prev else ZERO
            payload = {"portfolio_id": pf.id, "date": w.current_date.isoformat(), "nav": s["nav"], "ledger_nav": s["ledger_nav"], "cash": s["cash"],
                       "market_value": s["market_value"], "receivables": s["receivables"], "payables": s["payables"],
                       "accrued": led.balance("1220") + led.balance("1230") - led.balance("2300"), "day_pnl": day_pnl, "explain": explain,
                       "by_position": by_pos, "capital_flows": pf.day_capital_flows, "cumulative_realized": s["realized"], "unrealized": s["unrealized"],
                       "gross_exposure": s["gross_exposure"], "net_exposure": s["net_exposure"], "long_exposure": s["long_exposure"],
                       "short_exposure": s["short_exposure"], "leverage": money(s["leverage"] * 10000) / 10000 if s["nav"] else ZERO,
                       "prev_nav": prev_nav, "reconciles": (s["nav"] - prev_nav - pf.day_capital_flows) == day_pnl,
                       "margin_deposits": s["margin_deposits"], "greek_attribution": greek_attr}
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
                           short_exposure=D(p["short_exposure"]), leverage=D(p["leverage"]), ledger_nav=D(p["ledger_nav"]), event_id=ev.id,
                           greek_attribution=_ga(p.get("greek_attribution")))
        pf.nav_history.append(snap)
        pf.day_capital_flows = ZERO      # the snapshot consumed today's flows: the next one explains NAV from this one

    # ------------------------------------------------------------------ approximate option attribution
    def greek_attribution(self, pf: Portfolio, actual: Decimal) -> Dict:
        """Approximate: yesterday's Greeks x today's moves in the underlying, implied vol and time. The residual is the
        difference from the exact ledger P&L of the options bucket (which includes trades, spreads, higher-order terms)."""
        w = self.w
        rows = []
        tot = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
        for pos in pf.positions.values():
            if not pos.is_option or not pos.prev_greeks or not pos.greeks or pos.greeks.get("date") != w.current_date.isoformat():
                continue
            g0, g1 = pos.prev_greeks, pos.greeks
            if g0.get("date") == g1.get("date"):
                continue
            sec = w.securities[pos.security_id]
            n = float(pos.quantity) * float(sec.multiplier)
            dS = g1["underlying"] - g0["underlying"]
            dIV = (g1["iv"] - g0["iv"]) * 100.0
            days = max(1, (date.fromisoformat(g1["date"]) - date.fromisoformat(g0["date"])).days)
            r = {"security_id": sec.id, "contracts": pos.quantity, "dS": dS, "dIV_pts": dIV, "days": days,
                 "delta": g0["delta"] * dS * n, "gamma": 0.5 * g0["gamma"] * dS * dS * n, "vega": g0["vega"] * dIV * n, "theta": g0["theta"] * days * n}
            rows.append(r)
            for k in tot:
                tot[k] += r[k]
        if not rows:
            return {}
        explained = sum(tot.values())
        return {"method": "first/second-order Taylor from previous close Greeks (delta, 1/2 gamma dS^2, vega x dIV, theta x days); "
                          "residual = exact ledger options P&L - explained (trades, spreads, cross terms)",
                "rows": rows, **{k: round(v, 2) for k, v in tot.items()}, "explained": round(explained, 2), "actual": actual,
                "residual": round(float(actual) - explained, 2)}


def _ga(ga):
    if not ga:
        return {}
    out = dict(ga)
    if "actual" in out and out["actual"] is not None:
        out["actual"] = D(out["actual"])
    return out
