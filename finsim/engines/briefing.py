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
                       "orders_summary": self._orders_summary(pf)}
            w.emit(E.DAILY_BRIEFING, payload, cause_id=cause.id, portfolio_id=pf.id)

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

    def _attention(self, pf, snap) -> List[Dict]:
        w = self.w
        today = w.current_date
        items: List[Dict] = []
        add = lambda sev, text, link=None: items.append({"severity": sev, "text": text, "link": link})
        for m in pf.margin_calls:
            if m.status == "OPEN":
                add("HIGH", f"Margin call: {m.amount:,.0f} {pf.base_currency} due at the clearing member ({m.days_open} day(s) open; forced liquidation after 3)", "#/treasury")
            elif m.status == "FORCED" and m.resolved_date == today.isoformat():
                add("HIGH", f"Clearing member force-liquidated futures positions today for margin call {m.id}", "#/trading")
        cash = pf.cash_account(pf.base_currency).balance
        if cash < 0:
            add("HIGH", f"Cash overdrawn: {cash:,.0f} {pf.base_currency}; overdraft interest accruing at policy + 150bp", "#/treasury")
        failed = [si for si in pf.settlements.values() if si.status == "FAILED"]
        if failed:
            add("HIGH", f"{len(failed)} settlement(s) failed: " + "; ".join(f"{si.id} {si.security_id} — {si.fail_reason}" for si in failed[:3]), "#/settlements")
        for b in pf.breaches:
            if b["date"] == today.isoformat():
                add("MEDIUM", f"Risk limit breach ({b['kind']}): {b['text']}", "#/career")
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
            if ent and not ent.get("paid") and w.calendar.business_days_between(today, date.fromisoformat(ca.pay_date)) <= 3:
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
