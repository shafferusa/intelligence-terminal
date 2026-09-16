"""The daily briefing: what the player sees on login.

Built at the end of every daily update from the portfolio's snapshot, the
market's day, the news, and open operational items. Stored as an event so it
is part of the audit trail and identical on replay.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List

from ..domain.events import E, Event
from ..engines.pricing import interp_rate
from ..money import D, money, ZERO


class BriefingEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        self.w.on(E.DAILY_BRIEFING, lambda world, ev: self._h_briefing(ev))

    def build(self, cause: Event) -> None:
        w = self.w
        for pf in w.portfolios.values():
            snap = pf.nav_history[-1] if pf.nav_history else None
            if snap is None:
                continue
            payload = {"portfolio_id": pf.id, "date": w.current_date.isoformat(), "nav": snap.nav, "day_pnl": snap.day_pnl,
                       "day_return": float(snap.day_pnl / (snap.nav - snap.day_pnl)) if (snap.nav - snap.day_pnl) else 0.0,
                       "pnl_buckets": {k: v for k, v in snap.explain.items() if not k.startswith("_")},
                       "market": self._market_summary(), "news": self._news_today(), "attention": self._attention(pf, snap),
                       "movers": self._movers(snap), "regime": w.market.regime().label, "job_title": w.careers.level_title(pf),
                       "orders_summary": self._orders_summary(pf), "financing": self._financing(pf, snap), "collateral": self._collateral(pf),
                       "short_book": self._short_book(pf, snap), "fx": self._fx(pf), "derivatives": self._derivatives(pf, snap), "otc": self._otc(pf, snap), "risk": self._risk(pf), "macro": self._macro(), "corporate": self._corporate(pf),
                       "desk": self._desk(pf), "missions": [{"id": m["id"], "title": m["title"], "status": m["status"], "progress": m["progress"], "deadline": m["deadline"]} for m in pf.missions],
                       "scenario": [x for x in w.scenario_log if x["date"] == w.current_date.isoformat()]}
            w.emit(E.DAILY_BRIEFING, payload, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ the job's desk (phase 8/10)
    def _desk(self, pf) -> Dict:
        w = self.w
        today = w.current_date.isoformat()
        out: Dict = {"job": pf.job}
        if w.investors.applies(pf) and pf.investors:
            b = w.investors.book(pf)
            out["investors"] = {"subscribed": b["total_subscribed"], "pending": b["pending"], "mgmt_fee_accrued": b["mgmt_fee_accrued"],
                                "flows_today": [f for f in b["flows"] if f["date"] == today]}
        if w.clients.applies(pf):
            out["clients"] = {"open": [{"id": r["id"], "client": r["client_name"], "product": r["product"], "side": r["client_side"],
                                        "detail": f"{r['quantity']:,} {r['security_id']}" if r.get("security_id") else str(r.get("params")), "mid": r["mid"], "unit": r.get("unit")}
                                       for r in pf.client_rfqs.values() if r["status"] == "OPEN"],
                              "resolved_today": [{"id": r["id"], "client": r["client_name"], "product": r["product"], "outcome": r["outcome"], "note": r.get("note", "")}
                                                 for r in pf.client_rfqs.values() if r.get("resolved") == today], "stats": pf.client_stats}
        if pf.lends:
            b = w.lenddesk.book(pf)
            out["lending"] = {"on_loan_mv": b["on_loan_mv"], "collateral_held": b["collateral_held"], "accrued_fees": b["accrued_fees"], "fees_earned": b["fees_earned"],
                              "returns_today": [h for l in pf.lends.values() for h in l["history"] if h["date"] == today]}
        if w.treasury.applies(pf) and pf.treasury:
            d = w.treasury.dashboard(pf)
            out["treasury"] = {"liquidity": d["liquidity"], "min_liquidity": d["min_liquidity"], "liquidity_ok": d["liquidity_ok"], "floating_share": d["floating_share_after_swaps"],
                               "hedge_ratios": {c: x["hedge_ratio"] for c, x in d["fx_exposure"].items()}, "flows_today": [f for f in d["flows"] if f["date"] == today],
                               "next_debt_events": d["next_debt_events"][:3]}
        if pf.job == "RISK_MANAGER":
            o = w.institutions.oversight(pf)
            out["oversight"] = {"firm_nav": o["firm_nav"], "firm_day_pnl": o["firm_day_pnl"],
                                "desks": [{"name": r["name"], "portfolio_id": r["portfolio_id"], "nav": r["nav"], "day_pnl": r["day_pnl"], "leverage": r["leverage"],
                                           "pending": len(r["pending_requests"]), "breaches": len(r["breaches_today"]), "hard": [l["name"] for l in r["limits"] if l["status"] == "HARD"]}
                                          for r in o["desks"]]}
        return out

    # ------------------------------------------------------------------ pieces
    def _pct(self, sid: str) -> float:
        h = self.w.market.history.get(sid) or []
        if len(h) < 2:
            return 0.0
        return float(h[-1].close / h[-2].close - 1)

    def _front_pct(self, code: str) -> float:
        h = self.w.market.commodities.spot_history.get(code) or []
        return (h[-1][1] / h[-2][1] - 1) if len(h) >= 2 and h[-2][1] else 0.0

    def _market_summary(self) -> List[Dict]:
        w = self.w
        cur = w.market.curve()
        prev = w.market.curves[-2] if len(w.market.curves) > 1 else cur
        vi = w.market.vol_index_history
        vchg = (vi[-1][1] / vi[-2][1] - 1) if len(vi) > 1 and vi[-2][1] else 0.0
        rows = [
            {"label": "Broad equity index (SPXE)", "value": self._pct("SPXE"), "fmt": "pct"},
            {"label": "WTI crude (spot)", "value": self._front_pct("CL"), "fmt": "pct", "extra": f"${w.market.spot('CL'):.2f}"},
            {"label": "Brent crude", "value": self._front_pct("BRN"), "fmt": "pct"},
            {"label": "Natural gas", "value": self._front_pct("NG"), "fmt": "pct"},
            {"label": "Gold", "value": self._front_pct("GC"), "fmt": "pct", "extra": f"${w.market.spot('GC'):,.0f}"},
            {"label": "Copper", "value": self._front_pct("HG"), "fmt": "pct"},
            {"label": "Corn", "value": self._front_pct("ZC"), "fmt": "pct"},
            {"label": "10Y Treasury yield", "value": (cur.rates[7] - prev.rates[7]) * 1e4, "fmt": "bp", "extra": f"{cur.rates[7]*100:.2f}%"},
            {"label": "2Y Treasury yield", "value": (cur.rates[3] - prev.rates[3]) * 1e4, "fmt": "bp", "extra": f"{cur.rates[3]*100:.2f}%"},
            {"label": "IG credit spread", "value": cur.ig_spread_bps - prev.ig_spread_bps, "fmt": "bp", "extra": f"{cur.ig_spread_bps:.0f}bp"},
            {"label": "HY credit spread", "value": cur.hy_spread_bps - prev.hy_spread_bps, "fmt": "bp", "extra": f"{cur.hy_spread_bps:.0f}bp"},
            {"label": "Volatility index", "value": vchg, "fmt": "pct", "extra": f"{w.market.vol_index():.1f}"},
        ]
        return rows

    def _news_today(self) -> List[Dict]:
        today = self.w.current_date.isoformat()
        return [{"headline": n.headline, "category": n.category, "refs": n.refs, "body": n.body} for n in self.w.news if n.date == today][-10:]

    def _movers(self, snap) -> Dict:
        rows = [(sid, r.get("total", ZERO)) for sid, r in snap.by_position.items() if r.get("quantity", ZERO) != 0 or r.get("total", ZERO) != 0]
        rows.sort(key=lambda kv: kv[1])
        return {"losers": [{"security_id": s, "pnl": v} for s, v in rows[:3] if v < 0], "gainers": [{"security_id": s, "pnl": v} for s, v in reversed(rows[-3:]) if v > 0]}

    def _orders_summary(self, pf) -> Dict:
        today = self.w.current_date.isoformat()
        fills = [t for t in pf.trades.values() if t.trade_date == today]
        return {"filled_today": [{"trade_id": t.id, "side": t.side, "quantity": t.quantity, "security_id": t.security_id, "price": t.price,
                                  "forced": t.execution_detail.get("forced", False), "note": t.execution_detail.get("note", "")} for t in fills],
                "working": [{"order_id": o.id, "side": o.side, "quantity": o.quantity - o.filled_quantity, "security_id": o.security_id, "type": o.order_type,
                             "note": o.reason or ""} for o in pf.orders.values() if o.status in ("WORKING", "PARTIALLY_FILLED")],
                "expired_today": [{"order_id": o.id, "side": o.side, "security_id": o.security_id, "quantity": o.quantity - o.filled_quantity}
                                  for o in pf.orders.values() if o.status == "EXPIRED" and any(h.get("date") == today for h in o.history)]}

    def _financing(self, pf, snap) -> Dict:
        w = self.w
        book = w.repo.book(pf)
        fin = w.prime.financing(pf)
        ex = {k: v for k, v in snap.explain.items()}
        return {"repo_balance": book["repo_balance"], "avg_repo_rate": book["avg_repo_rate"], "reverse_balance": book["reverse_balance"],
                "repo_cost_today": -ex.get("repo_financing", ZERO), "borrow_expense_today": -ex.get("borrow_fees", ZERO),
                "margin_loan": fin["loan_balance"], "margin_rate": fin["rate"], "margin_cost_today": -ex.get("margin_financing", ZERO),
                "excess_liquidity": fin["excess_liquidity"], "collateral_value": fin["collateral_value"]}

    def _collateral(self, pf) -> Dict:
        d = self.w.collateral.dashboard(pf)
        posted = sum(d["cash_collateral_posted"].values(), ZERO) + sum(d["securities_collateral_posted"].values(), ZERO)
        open_calls = [c for c in d["calls"] if c.status == "OPEN"]
        return {"posted": posted, "available": d["available_collateral_value_repo"], "calls_due": sum((c.amount for c in open_calls), ZERO),
                "calls": [{"id": c.id, "source": c.source, "reference": c.reference, "amount": c.amount, "due": c.due} for c in open_calls],
                "treasury_encumbrance_pct": d["treasury_encumbrance_pct"], "received": sum(d["received"].values(), ZERO)}

    def _short_book(self, pf, snap) -> Dict:
        sb = self.w.seclending.short_book(pf)
        return {"market_value": sb["short_mv"], "borrow_cost_today": -snap.explain.get("borrow_fees", ZERO), "hard_to_borrow": sb["htb"], "recalls": sb["recalls"],
                "positions": [{"security_id": r["security_id"], "short_quantity": r["short_quantity"], "rate": r["rate"], "category": r["category"],
                               "market_value": r["market_value"], "recalled": r["recalled"]} for r in sb["rows"] if r["short_quantity"] > 0 or r["borrowed"] > 0]}

    def _option_events_today(self, pf) -> List[Dict]:
        w = self.w
        today = w.current_date.isoformat()
        out = []
        for ev in reversed(w.events):
            if ev.sim_date != today:
                break
            if ev.portfolio_id == pf.id and ev.type in (E.OPTION_EXERCISED, E.OPTION_ASSIGNED, E.OPTION_EXPIRED):
                p = ev.payload
                out.append({"kind": ev.type, "security_id": p["security_id"], "quantity": D(p["quantity"]), "event_id": ev.id,
                            "detail": p.get("outcome") or p.get("reason") or p.get("note", ""), "underlying_level": p.get("underlying_level"),
                            "amount": D(p["amount"]) if p.get("amount") is not None else None})
        out.reverse()
        return out

    def _derivatives(self, pf, snap) -> Dict:
        w = self.w
        rows = w.options.position_rows(pf)
        g = w.options.aggregate_greeks(pf)["total"]
        expiring = []
        for r in rows:
            bd = w.calendar.business_days_between(w.current_date, date.fromisoformat(r["expiry"]))
            if bd <= 5:
                expiring.append({"security_id": r["security_id"], "contracts": r["contracts"], "expiry": r["expiry"], "business_days": bd,
                                 "intrinsic": r["intrinsic"], "style": r["style"], "market_value": r["market_value"]})
        strategies = [{"id": st.id, "type": st.strategy_type, "underlying": st.underlying, "status": st.status, "quantity": st.quantity, "net_premium": st.net_premium}
                      for st in pf.strategies.values() if st.status in ("WORKING", "FILLED")]
        return {"positions": len(rows), "market_value": sum((r["market_value"] for r in rows), ZERO), "delta_shares": g["delta"], "dollar_delta": g["dollar_delta"],
                "gamma": g["gamma"], "vega": g["vega"], "theta": g["theta"], "margin": pf.options_margin, "events": self._option_events_today(pf),
                "expiring": expiring, "strategies": strategies, "options_pnl_today": snap.explain.get("options", ZERO),
                "greek_attribution": {k: v for k, v in snap.greek_attribution.items() if k != "rows"}}

    def _macro(self) -> Dict:
        w = self.w
        m = w.market.macro
        today = w.current_date.isoformat()
        return {"state": {"growth": m.state.growth, "inflation": m.state.inflation, "unemployment": m.state.unemployment, "policy_rate": m.state.policy_rate,
                          "policy_target": m.policy_target(), "next_meeting": m.next_meeting(w.current_date).isoformat(), "last_decision": m.state.last_decision},
                "releases_today": [r for r in m.releases if r["date"] == today], "earnings_today": [e for e in m.earnings if e["date"] == today],
                "upcoming": m.upcoming(w.current_date, 7)[:12], "ratings_today": [r for r in m.rating_history if r["date"] == today]}

    def _corporate(self, pf) -> Dict:
        w = self.w
        today = w.current_date.isoformat()
        rows = []
        for ca in w.corporate_actions.values():
            if ca.action_type in ("CASH_DIVIDEND", "COUPON", "MATURITY"):
                continue
            held = pf.positions[ca.security_id].quantity if ca.security_id in pf.positions else ZERO
            terms = ca.entitlements.get("_terms", {})
            ent = ca.entitlements.get(pf.id)
            if ca.status == "DECLARED" or ca.pay_date == today:
                rows.append({"id": ca.id, "kind": ca.action_type, "security_id": ca.security_id, "announced": ca.declared_date, "effective": ca.ex_date, "status": ca.status,
                             "held": held, "terms": terms, "deadline": terms.get("deadline"), "elected": ent.get("elected") if ent else None,
                             "election_open": ca.action_type in ("TENDER_OFFER", "RIGHTS_ISSUE") and ca.status == "DECLARED" and today <= str(terms.get("deadline", ""))})
        return {"events": rows, "announced_today": [r for r in rows if r["announced"] == today], "fail_charges": pf.fail_charges}

    def _risk(self, pf) -> Dict:
        h = pf.risk_history[-1] if pf.risk_history else None
        if not h:
            return {}
        stress = h.get("stress", {})
        worst = h.get("worst_stress")
        return {"var95": h.get("var95"), "var99": h.get("var99"), "es975": h.get("es975"), "nav": h.get("nav"), "worst_stress": worst,
                "worst_stress_pnl": stress.get(worst) if worst else None, "dv01": h.get("factors", {}).get("dv01"), "beta_dollar": h.get("factors", {}).get("beta_dollar"),
                "vega": h.get("factors", {}).get("vega"), "illiquid_pct_nav": h.get("illiquid_pct_nav"), "counterparty_exposure": h.get("counterparty_exposure"),
                "limits": [l for l in h.get("limits", []) if l["status"] != "OK"], "limits_all": h.get("limits", [])}

    def _otc(self, pf, snap) -> Dict:
        w = self.w
        today = w.current_date.isoformat()
        open_trades = [t for t in pf.otc_trades.values() if t.status == "OPEN"]
        pnl_today = sum((snap.by_position.get(t.id, {}).get("price_pnl", ZERO) for t in pf.otc_trades.values()), ZERO)
        flows = [{"trade_id": t.id, "product": t.product, "kind": cf["kind"], "amount": cf["base"], "note": cf["note"]}
                 for t in pf.otc_trades.values() for cf in t.cashflows if cf["date"] == today]
        ex = w.otc.exposure(pf)
        top = sorted([r for r in ex["rows"] if r["trades"]], key=lambda r: -float(r["current_exposure"]))[:4]
        closed_today = [t for t in pf.otc_trades.values() if t.closed_date == today]
        return {"open": len(open_trades), "net_mtm": sum((t.mtm for t in open_trades), ZERO), "pnl_today": pnl_today,
                "vm_posted": sum((c.vm_posted for c in pf.csas.values()), ZERO), "vm_received": sum((c.vm_received for c in pf.csas.values()), ZERO),
                "im_posted": sum((c.im_posted for c in pf.csas.values()), ZERO), "cashflows_today": flows,
                "closed_today": [{"trade_id": t.id, "product": t.product, "status": t.status, "note": t.notes[-1]["note"] if t.notes else ""} for t in closed_today],
                "upcoming": w.otc.upcoming(pf, 5), "exposure": [{"dealer": r["dealer"], "current_exposure": r["current_exposure"], "pfe": r["pfe"], "pct_nav": r["pct_nav"],
                                                                   "rating": r["rating"], "cds_bps": r["cds_bps"]} for r in top],
                "rfqs_open": sum(1 for r in pf.rfqs.values() if r.status == "OPEN"), "rates_dv01": w.otc.book(pf)["aggregate"]["rates_dv01"] if open_trades else 0.0}

    def _fx(self, pf) -> Dict:
        ex = self.w.fx.exposures(pf)
        return {"balances": {c: {"local": ca.balance, "base": ca.base_value} for c, ca in pf.cash.items()},
                "exposures": {c: v for c, v in ex.items() if c != pf.base_currency and v["local"] != 0},
                "forwards": sum(1 for f in pf.fx_forwards.values() if f.status == "OPEN")}

    def _attention(self, pf, snap) -> List[Dict]:
        w = self.w
        today = w.current_date
        items: List[Dict] = []
        add = lambda sev, text, link=None: items.append({"severity": sev, "text": text, "link": link})
        # the job's desk: investors, clients, lending, treasury, oversight (phase 8/10)
        if pf.investors:
            for p in pf.investors.get("pending", []):
                add("MEDIUM" if p["kind"] == "REDEMPTION" else "INFO", f"Investor {p['kind'].lower()}: {p['investor']} {D(p['amount']):,.0f} dealing {p['dealing']}", "#/desk")
        open_reqs = [r for r in pf.client_rfqs.values() if r["status"] == "OPEN"]
        if open_reqs:
            add("MEDIUM", f"{len(open_reqs)} client request(s) await a price today: " + "; ".join(f"{r['client_name']} {r['client_side']} {r['product']}" for r in open_reqs[:4]), "#/desk")
        for r in pf.client_rfqs.values():
            if r.get("resolved") == today.isoformat() and r["outcome"] == "WON":
                add("INFO", f"Client trade won: {r['client_name']} {r['product']} at {r['level']} — {r.get('note', '')}", "#/desk")
        for l in pf.lends.values():
            for h in l["history"]:
                if h["date"] == today.isoformat() and "returned" in h["note"]:
                    add("INFO", f"Lend {l['id']}: {h['note']}", "#/desk")
        if pf.treasury:
            d = w.treasury.dashboard(pf)
            if not d["liquidity_ok"]:
                add("HIGH", f"LIQUIDITY BREACH — cash {d['liquidity']:,.0f} is below the {d['min_liquidity']:,.0f} minimum: draw, sell or stop paying out", "#/desk")
            for e in d["next_debt_events"][:2]:
                if w.calendar.business_days_between(today, date.fromisoformat(e["date"])) <= 5:
                    add("MEDIUM", f"Debt {e['kind'].lower()} on {e['debt_id']} due {e['date']}", "#/desk")
        if pf.job == "RISK_MANAGER":
            for d in w.institutions.desks():
                pend = [r for r in d.desk_requests.values() if r["status"] == "PENDING"]
                if pend:
                    detail = ", ".join(f"{r['side']} {r['quantity']:,} {r['security_id']}" for r in pend[:3])
                    add("MEDIUM", f"{d.name}: {len(pend)} request(s) awaiting your decision ({detail})", "#/desk")
                for b in d.breaches:
                    if b["date"] == today.isoformat():
                        add("HIGH", f"{d.name}: {b['kind']} — {b['text']}", "#/desk")
                for l in (d.risk_history[-1].get("limits", []) if d.risk_history else []):
                    if l["status"] == "HARD":
                        add("HIGH", f"{d.name}: HARD limit {l['name']} at {l['value']:.1%}", "#/desk")
        for m in pf.missions:
            if m.get("completed") == today.isoformat():
                add("INFO", f"Mission complete: {m['title']}", "#/career")
            elif m.get("failed") == today.isoformat():
                add("MEDIUM", f"Mission failed: {m['title']}", "#/career")
        for x in w.scenario_log:
            if x["date"] == today.isoformat():
                add("HIGH", f"SCENARIO — {x['note']}", "#/career")
        # collateral / margin calls
        for c in pf.collateral_calls.values():
            if c.status == "OPEN":
                src = {"PRIME": "Prime-broker margin call", "REPO": f"Repo collateral call on {c.reference}", "SECLOAN": f"Collateral call on loan {c.reference}",
                       "OTC": f"CSA margin call from {c.reference}"}.get(c.source, f"{c.source} collateral call {c.reference}")
                add("HIGH", f"{src}: {c.amount:,.0f} {pf.base_currency} due {c.due} — {c.reason}", "#/otc/csa" if c.source == "OTC" else "#/collateral")
            elif c.status == "FORCED" and c.resolved == today.isoformat() and c.source != "OTC":
                add("HIGH", f"Unmet call {c.id} ({c.source}): counterparty {'liquidated positions' if c.source == 'PRIME' else 'unwound the repo'} today", "#/trading")
        # recalls and buy-ins
        for l in pf.loans.values():
            if l.status == "OPEN" and l.recall_status == "RECALLED":
                add("HIGH", f"SECURITY RECALL — return {l.recall_quantity:,} {l.security_id} to {l.lender} by {l.recall_due} (find a replacement borrow, buy to cover, or return from custody)", "#/seclending")
            if l.recall_status == "BOUGHT_IN" and any(t.trade_date == today.isoformat() and "buy-in" in t.execution_detail.get("note", "") for t in pf.trades.values()):
                add("HIGH", f"Lender executed a buy-in on {l.security_id} for the unmet recall on {l.id}; penalty charged", "#/seclending")
            if l.status == "OPEN" and len(l.rate_history) >= 2:
                prev, cur = l.rate_history[-2]["rate"], l.rate_history[-1]["rate"]
                if l.rate_history[-1]["date"] == today.isoformat() and (cur >= prev + 0.05 or (prev > 0 and cur >= 2 * prev and cur >= 0.02)):
                    add("MEDIUM", f"BORROW RATE SPIKE — {l.security_id} re-rated from {prev:.2%} to {cur:.2%} on {l.quantity:,} shares", "#/seclending")
        # repo maturities (term) and rolls in stress
        nxt = w.calendar.next_business_day(today).isoformat()
        for r in pf.repos.values():
            if r.status == "OPEN" and r.term_type == "TERM" and r.maturity == nxt:
                add("MEDIUM", f"REPO MATURITY — {r.id}: {r.principal:,.0f} {'repayable' if r.side == 'REPO' else 'returns'} tomorrow (collateral {r.quantity:,} {r.security_id})", "#/repo")
        coll = self._collateral(pf)
        if coll["treasury_encumbrance_pct"] >= 0.70:
            add("MEDIUM", f"COLLATERAL CONCENTRATION — {coll['treasury_encumbrance_pct']:.0%} of Treasury holdings are encumbered", "#/collateral")
        # cash
        for ccy, ca in pf.cash.items():
            if ca.balance < 0 and abs(ca.base_value) >= 10_000:
                add("HIGH", f"NEGATIVE CASH — {ccy} account {ca.balance:,.0f}; overdraft interest accruing", "#/treasury")
        if pf.margin_loan > 0 and any(m.kind == "MARGIN_LOAN" and m.date == today.isoformat() and m.amount > 0 for m in pf.cash_movements):
            add("MEDIUM", f"Prime-broker margin loan drawn today; balance {pf.margin_loan:,.0f} at {w.prime.rate():.2%}", "#/treasury")
        for f in pf.fx_forwards.values():
            if f.status == "OPEN" and f.maturity == nxt:
                add("INFO", f"FX forward {f.id} settles tomorrow: receive {f.buy_ccy} {f.buy_amount:,.0f}, deliver {f.sell_ccy} {f.sell_amount:,.0f}", "#/treasury")
        cash = pf.cash_account(pf.base_currency).balance
        failed = [si for si in pf.settlements.values() if si.status == "FAILED"]
        if failed:
            add("HIGH", f"{len(failed)} settlement(s) failed: " + "; ".join(f"{si.id} {si.security_id} — {si.fail_reason}" for si in failed[:3]), "#/settlements")
        for b in pf.breaches:
            if b["date"] == today.isoformat():
                add("HIGH" if b.get("severity") == "HARD" else "MEDIUM", f"Risk limit breach ({b['kind']}, {'HARD' if b.get('severity') == 'HARD' else 'soft'}): {b['text']}", "#/risk/limits")
        # expiring futures
        for pos in pf.positions.values():
            if pos.is_future and pos.quantity != 0:
                sec = w.securities[pos.security_id]
                bd = w.calendar.business_days_between(today, date.fromisoformat(sec.expiry))
                if 0 <= bd <= 3:
                    add("MEDIUM", f"{sec.id} expires in {bd} business day(s): {pos.quantity:+,} contract(s) will be auto-closed at settlement unless rolled", f"#/security/{sec.id}")
        # upcoming settlements & cash flows
        due = [si for si in pf.settlements.values() if si.status in ("PENDING", "MATCHED") and si.settlement_date == w.calendar.next_business_day(today).isoformat()]
        if due:
            net = sum(((si.cash_amount if si.instruction_type == "DVP" else -si.cash_amount) for si in due), ZERO)
            add("INFO", f"{len(due)} settlement(s) due next session, net cash {net:+,.0f}", "#/settlements")
        for ca in w.corporate_actions.values():
            ent = ca.entitlements.get(pf.id)
            if ent and ent.get("amount") is not None and not ent.get("paid") and w.calendar.business_days_between(today, date.fromisoformat(ca.pay_date)) <= 3:
                add("INFO", f"Dividend {ca.security_id} {ent['amount']:,.2f} pays {ca.pay_date}", f"#/position/{ca.security_id}")
            elif ca.status == "DECLARED" and ca.security_id in pf.positions and pf.positions[ca.security_id].quantity > 0 \
                    and w.calendar.business_days_between(today, date.fromisoformat(ca.ex_date)) <= 2:
                add("INFO", f"{ca.security_id} goes ex-dividend {ca.ex_date} (${ca.amount_per_unit}/share)", f"#/security/{ca.security_id}")
        for pos in pf.positions.values():
            sec = w.securities[pos.security_id]
            if sec.is_bond and pos.quantity > 0:
                from ..engines.pricing import BondPricer
                nxt = next((c for c in BondPricer.coupon_dates(sec) if c > today), None)
                if nxt and w.calendar.business_days_between(today, nxt) <= 3:
                    add("INFO", f"{sec.id} coupon {money(pos.quantity * D(sec.coupon) / sec.freq):,.2f} pays {nxt.isoformat()}", f"#/position/{sec.id}")
        # concentration
        nav = snap.nav
        job = w.careers.job_for(pf)
        for pos in pf.positions.values():
            expo = abs(pos.notional if pos.is_future else pos.market_value)
            if nav and float(expo / nav) > 0.25 and (not job or job.key == "SANDBOX"):
                add("INFO", f"{pos.security_id} is {float(expo / nav):.0%} of NAV", f"#/position/{pos.security_id}")
        # orders
        working = [o for o in pf.orders.values() if o.status in ("WORKING", "PARTIALLY_FILLED")]
        if working:
            add("INFO", f"{len(working)} order(s) still working for the next session", "#/trading")
        expired = [o for o in pf.orders.values() if o.status == "EXPIRED" and any(h.get("date") == today.isoformat() for h in o.history)]
        if expired:
            add("INFO", f"{len(expired)} good-for-day order(s) expired unfilled", "#/trading")
        # reviews
        for r in pf.reviews:
            if r["end"] == today.isoformat():
                add("MEDIUM" if r["rating"] in ("BELOW", "UNACCEPTABLE") else "INFO", f"{r['period'].title()} performance review: {r['rating']} — return {r['return']:+.2%}", "#/career")
        for c in pf.career_log:
            if c["date"] == today.isoformat():
                add("INFO", f"Career: {c['kind']} — {c['text']}", "#/career")
        # listed options: expirations, exercise/assignment, margin
        for r in w.options.position_rows(pf):
            sec = w.securities[r["security_id"]]
            bd = w.calendar.business_days_between(today, date.fromisoformat(sec.expiry))
            n = r["contracts"]
            if not (0 <= bd <= 3):
                continue
            shares = abs(n) * D(str(sec.multiplier))
            if r["intrinsic"] > 0:
                if sec.settlement_style == "CASH":
                    what = f"cash-settled at intrinsic value (~{r['intrinsic'] * float(shares):,.0f} {'received' if n > 0 else 'paid'})"
                elif n > 0:
                    what = (f"auto-exercised: {'buy' if sec.option_type == 'C' else 'sell'} {shares:,} {sec.underlying} at {sec.strike:g} "
                            f"({float(shares) * sec.strike:,.0f} cash {'needed' if sec.option_type == 'C' else 'received'}); sell the contract to keep the extrinsic value instead")
                else:
                    what = f"assignment: {'deliver' if sec.option_type == 'C' else 'buy'} {shares:,} {sec.underlying} at {sec.strike:g}; buy the contract back or roll to avoid it"
                add("HIGH" if bd <= 1 else "MEDIUM", f"EXPIRATION — {n:+,} {sec.id} expires in {bd} session(s), {r['intrinsic']:.2f}/share in the money: {what}", "#/options")
            else:
                add("INFO", f"EXPIRATION — {n:+,} {sec.id} expires in {bd} session(s), out of the money: expires worthless unless {sec.underlying} moves through {sec.strike:g}", "#/options")
        for e in self._option_events_today(pf):
            if e["kind"] == E.OPTION_ASSIGNED:
                add("HIGH", f"ASSIGNED — {e['quantity']:,} {e['security_id']}: {e['detail']}; the underlying trade settles through custody", "#/options")
            elif e["kind"] == E.OPTION_EXERCISED:
                add("INFO", f"EXERCISED — {e['quantity']:,} {e['security_id']}: delivery at the strike is in settlement", "#/options")
            elif e["kind"] == E.OPTION_EXPIRED:
                add("INFO", f"EXPIRED — {e['quantity']:+,} {e['security_id']}: {str(e['detail']).replace('_', ' ').lower()}", "#/options")
        margin_evs = [ev for ev in reversed(w.events) if ev.sim_date == today.isoformat() and ev.type == E.OPTIONS_MARGIN_COMPUTED and ev.portfolio_id == pf.id]
        if margin_evs:
            new = pf.options_margin
            for ev in reversed(w.events):
                if ev.sim_date < today.isoformat() and ev.type == E.OPTIONS_MARGIN_COMPUTED and ev.portfolio_id == pf.id:
                    old = D(ev.payload["total"])
                    if (old == 0 and new > 0) or (old > 0 and abs(new - old) / old >= 0.25):
                        add("MEDIUM", f"OPTIONS MARGIN — requirement moved from {old:,.0f} to {new:,.0f} (regime multiplier {margin_evs[0].payload['regime_multiplier']}x)", "#/options")
                    break
        for st in pf.strategies.values():
            if st.notes and st.notes[-1]["date"] == today.isoformat() and st.status in ("FILLED", "EXPIRED"):
                add("INFO", f"Strategy {st.id} {st.strategy_type} on {st.underlying}: {st.status.lower()}" + (f" at net {st.net_premium:+,.2f}/unit" if st.status == "FILLED" else " (legs unfilled)"), "#/options")
        # corporate events and elections on held names
        for r in self._corporate(pf)["events"]:
            if r["held"] == 0 and not r["elected"]:
                continue
            if r["election_open"]:
                add("MEDIUM" if r["deadline"] > today.isoformat() else "HIGH", f"{r['kind'].replace('_', ' ')} on {r['security_id']} — elect by {r['deadline']} "
                    f"({'tender at ' + str(r['terms'].get('offer_price')) if r['kind'] == 'TENDER_OFFER' else 'subscribe at ' + str(r['terms'].get('subscription_price'))}); "
                    f"{'elected ' + str(r['elected']) if r['elected'] else 'no election yet'}", "#/settlements")
            elif r["announced"] == today.isoformat():
                add("MEDIUM", f"{r['kind'].replace('_', ' ')} announced on {r['security_id']}: effective {r['effective']}", f"#/security/{r['security_id']}")
            elif 0 < w.calendar.business_days_between(today, date.fromisoformat(r["effective"])) <= 2:
                add("INFO", f"{r['kind'].replace('_', ' ')} on {r['security_id']} effective {r['effective']}", f"#/security/{r['security_id']}")
        for ca in w.corporate_actions.values():
            if ca.action_type not in ("CASH_DIVIDEND", "COUPON", "MATURITY") and ca.pay_date == today.isoformat() and ca.status == "PAID" and (ca.entitlements.get(pf.id) or ca.security_id in pf.positions):
                add("INFO", f"{ca.action_type.replace('_', ' ')} on {ca.security_id} processed today", f"#/position/{ca.security_id}")
        # macro: releases and earnings on held names, defaults
        mac = self._macro()
        for r in mac["releases_today"]:
            if abs(r.get("z", 0)) >= 1.5 or r["kind"] == "FOMC":
                add("INFO", f"{r['kind']}: {r.get('headline') or (str(r['actual']) + ' vs ' + str(r['consensus']) + ' expected')} — rates {r['rate_shock_bp']:+.0f}bp", "#/macro")
        for e in mac["earnings_today"]:
            if e["security_id"] in pf.positions and pf.positions[e["security_id"]].quantity:
                add("MEDIUM" if abs(e["jump"]) >= 0.05 else "INFO", f"EARNINGS — {e['security_id']} EPS {e['eps_actual']:.2f} vs {e['eps_consensus']:.2f} ({e['surprise']:+.1%}), shares {e['jump']:+.1%}", f"#/security/{e['security_id']}")
        for u in mac["upcoming"]:
            if u["kind"] == "EARNINGS" and u["security_id"] in pf.positions and pf.positions[u["security_id"]].quantity and w.calendar.business_days_between(today, date.fromisoformat(u["date"])) <= 2:
                add("INFO", f"{u['security_id']} reports earnings {u['date']} (consensus EPS {u['consensus']:.2f})", f"#/security/{u['security_id']}")
            elif u["kind"] == "FOMC" and w.calendar.business_days_between(today, date.fromisoformat(u["date"])) == 1:
                add("INFO", f"Central bank meeting tomorrow (policy {mac['state']['policy_rate']:.2%}, reaction-function target {mac['state']['policy_target']:.2%})", "#/macro")
        for r in mac["ratings_today"]:
            if r["reference"] in pf.positions and pf.positions[r["reference"]].quantity:
                add("MEDIUM", f"RATING — {r['issuer']} {r['from']} → {r['to']}", f"#/security/{r['reference']}")
        for ev_ in reversed(w.events):
            if ev_.sim_date != today.isoformat():
                break
            if ev_.type == E.ISSUER_DEFAULTED:
                ref = ev_.payload["reference"]
                held = ref in pf.positions and pf.positions[ref].quantity or any(t.status != "OPEN" and t.product == "CDS" and t.terms.get("reference") == ref and t.closed_date == today.isoformat() for t in pf.otc_trades.values())
                add("HIGH" if held else "INFO", f"DEFAULT — {ev_.payload['issuer']} ({ref}) failed: bonds mark at {float(ev_.payload['recovery']):.0%} recovery, CDS settle, equity collapses", f"#/security/{ref}")
        # OTC derivatives, CSA collateral and counterparties
        for c in pf.collateral_calls.values():
            if c.source == "OTC" and c.status == "FORCED" and c.resolved == today.isoformat():
                add("HIGH", f"CSA CLOSE-OUT — {c.reference} terminated the netting set after the unmet margin call {c.id}", "#/otc")
        for t in pf.otc_trades.values():
            if t.closed_date == today.isoformat() and t.status in ("TERMINATED", "SETTLED_DEFAULT", "EXERCISED", "EXPIRED", "MATURED"):
                sev = "HIGH" if t.status in ("SETTLED_DEFAULT", "TERMINATED") else "INFO"
                add(sev, f"OTC {t.status.replace('_', ' ').lower()} — {t.id} {t.product} with {t.counterparty}: {t.notes[-1]['note'] if t.notes else ''}", "#/otc")
        ex = w.otc.exposure(pf) if pf.otc_trades else {"rows": []}
        for r in ex["rows"]:
            if r["trades"] and r["pct_nav"] >= 0.05:
                add("MEDIUM", f"COUNTERPARTY CONCENTRATION — {r['name']}: current exposure {r['current_exposure']:,.0f} ({r['pct_nav']:.1%} of NAV), PFE {r['pfe']:,.0f}, CDS {r['cds_bps']:.0f}bp", "#/otc/counterparties")
            if r["trades"] and r["defaulted"]:
                add("HIGH", f"COUNTERPARTY DEFAULT — {r['name']}: netting set closed out", "#/otc/counterparties")
        for n in w.news:
            if n.date == today.isoformat() and n.category == "COUNTERPARTY":
                sev = "MEDIUM" if any(t.status == "OPEN" and t.counterparty in n.refs for t in pf.otc_trades.values()) else "INFO"
                add(sev, f"DEALER CREDIT — {n.headline}", "#/otc/counterparties")
        for u in w.otc.upcoming(pf, 3):
            if u["kind"] in ("EXPIRY", "NOTIONAL_EXCHANGE", "FRA_SETTLEMENT", "MATURITY"):
                add("MEDIUM" if u["kind"] in ("EXPIRY", "NOTIONAL_EXCHANGE") else "INFO", f"OTC {u['kind'].replace('_', ' ').lower()} in {u['business_days']} session(s) — {u['trade_id']} {u['product']}: {u['note']}", "#/otc")
            elif u["business_days"] == 1:
                add("INFO", f"OTC {u['kind'].replace('_', ' ').lower()} next session — {u['trade_id']} {u['product']}: {u['note']}", "#/otc")
        open_rfqs = [r for r in pf.rfqs.values() if r.status == "OPEN"]
        if open_rfqs:
            add("INFO", f"{len(open_rfqs)} open RFQ(s) expire at the next update unless executed", "#/otc")
        # regime
        if w.market.regime_history and w.market.regime_history[-1][0] == today.isoformat() and len(w.market.regime_history) > 1:
            add("MEDIUM", f"Market regime changed to {w.market.regime().label}: {w.market.regime().description}", "#/markets")
        order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
        items.sort(key=lambda i: order[i["severity"]])
        return items

    def _h_briefing(self, ev: Event) -> None:
        p = dict(ev.payload)
        p["event_id"] = ev.id
        p["nav"], p["day_pnl"] = D(p["nav"]), D(p["day_pnl"])
        p["pnl_buckets"] = {k: D(v) for k, v in p["pnl_buckets"].items()}
        p["movers"] = {k: [{"security_id": m["security_id"], "pnl": D(m["pnl"])} for m in v] for k, v in p["movers"].items()}
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.briefings.append(p)
        if len(pf.briefings) > 60:
            pf.briefings = pf.briefings[-60:]
