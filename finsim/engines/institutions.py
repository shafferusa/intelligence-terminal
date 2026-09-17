"""AI institutions: rule-based desks that trade through the same order engine,
settlement, margin and risk as the player. They exist so a risk manager has
something to oversee and so the world has other books to compare against.

Strategies (bounded, deterministic):
  MOMENTUM        long the strongest 20-day equities (long-only: AI desks do not run borrow), rebalanced weekly
  CARRY           long Treasuries and investment-grade corporates against the policy rate, DV01-capped
  VOL_SELLER      writes index strangles each month, hedges delta with index futures weekly
  COMMODITY_TREND long/short front futures by 50-day trend, sized by ADV

Every desk is an ordinary portfolio with a job, limits and reviews. Orders
above a request threshold are queued for the risk manager's approval instead of
being placed; unapproved requests lapse at the next update.
"""
from __future__ import annotations

import math
import random
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import Event
from ..money import D, money, qty as qqty, ZERO

AI_JOBS = {"AI_MOMENTUM": "MOMENTUM", "AI_CARRY": "CARRY", "AI_VOL": "VOL_SELLER", "AI_COMMODITY": "COMMODITY_TREND"}
REQUEST_THRESHOLD_PCT = 0.08       # orders above this share of NAV need the risk manager's approval
REQUEST_TTL = 5                    # sessions a request waits for the risk manager before it lapses


class AE:
    DESK_REQUEST = "DESK_REQUEST"
    DESK_REQUEST_DECIDED = "DESK_REQUEST_DECIDED"
    DESK_LIMIT_SET = "DESK_LIMIT_SET"
    DESK_CLIP_WORKED = "DESK_CLIP_WORKED"


class InstitutionEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(AE.DESK_REQUEST, lambda world, ev: self._h_request(ev))
        w.on(AE.DESK_REQUEST_DECIDED, lambda world, ev: self._h_decided(ev))
        w.on(AE.DESK_LIMIT_SET, lambda world, ev: self._h_limit(ev))
        w.on(AE.DESK_CLIP_WORKED, lambda world, ev: self._h_clip(ev))

    def desks(self) -> List:
        return [pf for pf in self.w.portfolios.values() if pf.job in AI_JOBS]

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event) -> None:
        w = self.w
        for pf in self.desks():
            # undecided requests lapse after REQUEST_TTL sessions; a lapsed request is then worked in clips at the
            # approval threshold, one clip per session, so an absent risk manager slows a desk down but never freezes it
            nav = w.pnl.compute_summary(pf)["nav"]
            for r in list(pf.desk_requests.values()):
                if r["status"] == "PENDING" and w.calendar.business_days_between(date.fromisoformat(r["date"]), w.current_date) >= REQUEST_TTL:
                    w.emit(AE.DESK_REQUEST_DECIDED, {"portfolio_id": pf.id, "request_id": r["id"], "decision": "LAPSED", "by": "system"}, cause_id=cause.id, portfolio_id=pf.id)
                if r["status"] == "LAPSED" and D(r.get("remaining", 0)) > 0:
                    sec = w.securities[r["security_id"]]
                    px = w.market.last_bar(sec.id).close
                    unit = px * D(str(sec.multiplier)) if sec.is_future else px * (D(1) / 100 if sec.is_bond else D(1))
                    clip_q = qqty(nav * D(str(REQUEST_THRESHOLD_PCT * pf.desk_limits.get("request_mult", 1.0))) / unit) if unit else ZERO
                    clip = min(D(r["remaining"]), max(clip_q, D(1)))
                    if sec.is_bond:
                        clip = clip - (clip % 1000) if clip >= 1000 else clip
                    w.emit(AE.DESK_CLIP_WORKED, {"portfolio_id": pf.id, "request_id": r["id"], "quantity": clip}, cause_id=cause.id, portfolio_id=pf.id)
                    self._place(pf, sec.id, r["side"], clip, f"lapsed {r['id']} worked in clips")
            strat = AI_JOBS[pf.job]
            try:
                getattr(self, "_" + strat.lower())(pf, cause)
            except Exception as e:      # a desk's bad day must never stop the world; it is logged as a desk note
                pf.desk_notes.append({"date": w.current_date.isoformat(), "note": f"strategy error: {e}"})

    def _target(self, pf, sid: str, target_qty: Decimal, cause: Event, note: str) -> None:
        """Move the position toward target through the normal order flow, subject to the request threshold."""
        w = self.w
        pos = pf.positions.get(sid)
        cur = pos.quantity if pos else ZERO
        working = sum(((o.quantity - o.filled_quantity) * (1 if o.side == "BUY" else -1) for o in pf.orders.values()
                       if o.security_id == sid and o.status in ("WORKING", "PARTIALLY_FILLED")), ZERO)
        delta = target_qty - cur - working
        if delta == 0 or any((r["status"] == "PENDING" or (r["status"] == "LAPSED" and D(r.get("remaining", 0)) > 0)) and r["security_id"] == sid
                             for r in pf.desk_requests.values()):
            return
        side = "BUY" if delta > 0 else "SELL"
        q = abs(delta)
        sec = w.securities[sid]
        nav = w.pnl.compute_summary(pf)["nav"]
        px = w.market.last_bar(sid).close
        notional = q * px * D(str(sec.multiplier)) if sec.is_future else q * px * (D(1) / 100 if sec.is_bond else D(1))
        if nav and notional > nav * D(str(REQUEST_THRESHOLD_PCT * pf.desk_limits.get("request_mult", 1.0))) and any(p.job == "RISK_MANAGER" for p in w.portfolios.values()):
            rid = w.new_id("REQ")
            w.emit(AE.DESK_REQUEST, {"portfolio_id": pf.id, "request_id": rid, "security_id": sid, "side": side, "quantity": q, "notional": money(notional), "note": note},
                   cause_id=cause.id, portfolio_id=pf.id)
            return
        self._place(pf, sid, side, q, note)

    def _place(self, pf, sid: str, side: str, q: Decimal, note: str) -> None:
        from ..world import CommandError
        try:
            self.w.trading.enter_order(pf.id, sid, side, q, "MARKET", None, None, "DAY", f"AI:{note}")
        except CommandError as e:
            pf.desk_notes.append({"date": self.w.current_date.isoformat(), "note": f"{side} {q:,} {sid} rejected: {e}"})

    # ------------------------------------------------------------------ strategies
    def _momentum(self, pf, cause: Event) -> None:
        w = self.w
        if w.current_date.weekday() != 0 and (pf.positions or pf.desk_requests):
            return
        nav = w.pnl.compute_summary(pf)["nav"]
        mult = D(str(pf.desk_limits.get("gross_mult", 1.0)))
        scores = []
        for s in w.securities.values():
            if s.asset_class != "EQUITY" or s.liquidity_tier == "SMALL" or s.delisted:
                continue
            h = w.market.history.get(s.id) or []
            if len(h) < 25:
                continue
            scores.append((float(h[-1].close / h[-21].close - 1), s.id))
        scores.sort()
        longs = [sid for _, sid in scores[-4:]]
        weight = nav * D("0.15") * mult
        targets = {sid: qqty(weight / w.market.last_bar(sid).close) for sid in longs}
        for sid in list(pf.positions):
            if sid not in targets and w.securities[sid].asset_class == "EQUITY" and pf.positions[sid].quantity > 0:
                targets[sid] = ZERO
        for sid, tq in targets.items():
            self._target(pf, sid, tq, cause, "momentum rebalance")

    def _carry(self, pf, cause: Event) -> None:
        w = self.w
        if pf.positions and w.current_date.day not in (1, 2, 3):
            return
        nav = w.pnl.compute_summary(pf)["nav"]
        mult = D(str(pf.desk_limits.get("gross_mult", 1.0)))
        book = {"UST-5Y": D("0.35"), "UST-10Y": D("0.35"), "JPM-29": D("0.15"), "F-32": D("0.15")}
        for sid, wgt in book.items():
            sec = w.securities[sid]
            if sec.defaulted or sec.delisted:
                continue
            px = w.market.last_bar(sid).close
            face = qqty(nav * wgt * mult * 100 / px)
            face = face - (face % 1000)
            self._target(pf, sid, face, cause, "carry book")

    def _vol_seller(self, pf, cause: Event) -> None:
        w = self.w
        today = w.current_date
        # write a strangle on the index in the first week of each month, one expiry out
        opts = [p for p in pf.positions.values() if p.is_option and p.quantity < 0]
        if not opts and today.day <= 7:
            ch = w.options.chain("SPX")
            if len(ch["expiries"]) >= 2:
                ch = w.options.chain("SPX", ch["expiries"][1])
                rows = ch["rows"]
                i = min(range(len(rows)), key=lambda j: abs(rows[j]["strike"] - ch["level"]))
                nav = w.pnl.compute_summary(pf)["nav"]
                n = max(1, int(float(nav) * 0.5 * pf.desk_limits.get("gross_mult", 1.0) / (ch["level"] * 100)))
                if 0 <= i - 3 and i + 3 < len(rows):
                    self._place(pf, rows[i - 3]["P"]["id"], "SELL", D(n), "vol: short strangle put")
                    self._place(pf, rows[i + 3]["C"]["id"], "SELL", D(n), "vol: short strangle call")
        # weekly delta hedge with index futures
        if today.weekday() == 2:
            g = w.options.aggregate_greeks(pf)["total"]
            es = w.market.front_contract("ES")
            if es and g["delta"]:
                per_contract = float(w.market.last_bar(es.id).close) * es.multiplier
                shares_delta = g["delta"] * float(w.market.last_bar("SPY").close)
                target = -int(round(shares_delta / per_contract))
                self._target(pf, es.id, D(target), cause, "delta hedge")

    def _commodity_trend(self, pf, cause: Event) -> None:
        w = self.w
        if w.current_date.weekday() != 1:
            return
        nav = w.pnl.compute_summary(pf)["nav"]
        mult = pf.desk_limits.get("gross_mult", 1.0)
        for code in ("CL", "GC", "NG", "ZC", "HG"):
            h = w.market.commodities.spot_history.get(code) or []
            if len(h) < 55:
                continue
            trend = h[-1][1] / h[-51][1] - 1
            fut = w.market.front_contract(code)
            if fut is None or not w.market.history.get(fut.id):
                continue
            px = float(w.market.last_bar(fut.id).close) * fut.multiplier
            n = int(float(nav) * 0.12 * mult / px)
            target = n if trend > 0.03 else -n if trend < -0.03 else 0
            # roll: flatten expiring contracts
            for pos in list(pf.positions.values()):
                if pos.is_future and pos.quantity and w.securities[pos.security_id].underlying == code and pos.security_id != fut.id:
                    self._target(pf, pos.security_id, ZERO, cause, f"roll {code}")
            self._target(pf, fut.id, D(target), cause, f"trend {code} {trend:+.1%}")

    # ------------------------------------------------------------------ risk manager commands
    def decide(self, rm_pf, desk_pf, request_id: str, approve: bool, note: str = "") -> Dict:
        from ..world import CommandError
        w = self.w
        r = desk_pf.desk_requests.get(request_id)
        if r is None or r["status"] != "PENDING":
            raise CommandError("no pending request with that id")
        ev = w.emit(AE.DESK_REQUEST_DECIDED, {"portfolio_id": desk_pf.id, "request_id": request_id, "decision": "APPROVED" if approve else "REJECTED", "by": rm_pf.id, "note": note},
                    portfolio_id=desk_pf.id)
        if approve:
            self._place(desk_pf, r["security_id"], r["side"], D(r["quantity"]), f"approved {request_id}")
        return desk_pf.desk_requests[request_id]

    def set_limit(self, rm_pf, desk_pf, key: str, value: float) -> Dict:
        from ..world import CommandError
        if key not in ("gross_mult", "request_mult", "var_mult"):
            raise CommandError("limit keys: gross_mult (position sizing), request_mult (approval threshold), var_mult (VaR limit scaling)")
        v = float(value)
        if not (0.0 <= v <= 3.0):
            raise CommandError("limit multipliers must be between 0 and 3")
        self.w.emit(AE.DESK_LIMIT_SET, {"portfolio_id": desk_pf.id, "key": key, "value": v, "by": rm_pf.id}, portfolio_id=desk_pf.id)
        return desk_pf.desk_limits

    def force_reduce(self, rm_pf, desk_pf, security_id: str, fraction: float, cause: Optional[Event] = None) -> Dict:
        from ..world import CommandError
        w = self.w
        pos = desk_pf.positions.get(security_id)
        if pos is None or pos.quantity == 0:
            raise CommandError("no position to reduce")
        f = max(0.0, min(1.0, float(fraction)))
        q = qqty(abs(pos.quantity) * D(repr(f)))
        if q <= 0:
            raise CommandError("nothing to reduce")
        side = "SELL" if pos.quantity > 0 else "BUY"
        o = w.trading.enter_order(desk_pf.id, security_id, side, q, "MARKET", None, None, "DAY", f"RISK:{rm_pf.id} forced reduction")
        desk_pf.desk_notes.append({"date": w.current_date.isoformat(), "note": f"risk manager ordered {side} {q:,} {security_id} ({f:.0%})"})
        return {"order_id": o.id, "side": side, "quantity": q}

    def oversight(self, rm_pf) -> Dict:
        w = self.w
        rows = []
        for pf in self.desks():
            s = w.pnl.compute_summary(pf)
            snap = pf.nav_history[-1] if pf.nav_history else None
            rk = pf.risk_history[-1] if pf.risk_history else {}
            rows.append({"portfolio_id": pf.id, "name": pf.name, "job": pf.job, "strategy": AI_JOBS[pf.job], "nav": s["nav"], "day_pnl": snap.day_pnl if snap else ZERO,
                         "gross": s["gross_exposure"], "leverage": s["leverage"], "var99": rk.get("var99"), "limits": rk.get("limits", []), "desk_limits": pf.desk_limits,
                         "breaches_today": [b for b in pf.breaches if b["date"] == w.current_date.isoformat()],
                         "pending_requests": [r for r in pf.desk_requests.values() if r["status"] == "PENDING"], "notes": pf.desk_notes[-5:],
                         "positions": [{"security_id": p.security_id, "quantity": p.quantity, "market_value": p.market_value if not p.is_future else p.notional} for p in pf.positions.values() if p.quantity]})
        return {"desks": rows, "firm_nav": sum((r["nav"] for r in rows), ZERO), "firm_day_pnl": sum((r["day_pnl"] for r in rows), ZERO)}

    # ------------------------------------------------------------------ handlers
    def _h_request(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.desk_requests[p["request_id"]] = {"id": p["request_id"], "date": ev.sim_date, "security_id": p["security_id"], "side": p["side"], "quantity": D(p["quantity"]),
                                             "notional": D(p["notional"]), "note": p["note"], "status": "PENDING", "decision": None}

    def _h_decided(self, ev: Event) -> None:
        p = ev.payload
        r = self.w.portfolios[p["portfolio_id"]].desk_requests[p["request_id"]]
        r["status"] = p["decision"]
        r["decision"] = {"by": p.get("by"), "date": ev.sim_date, "note": p.get("note", "")}
        if p["decision"] == "LAPSED":
            r["remaining"] = D(r["quantity"])

    def _h_clip(self, ev: Event) -> None:
        p = ev.payload
        r = self.w.portfolios[p["portfolio_id"]].desk_requests[p["request_id"]]
        r["remaining"] = max(ZERO, D(r.get("remaining", 0)) - D(p["quantity"]))
        r["clips"] = int(r.get("clips", 0)) + 1

    def _h_limit(self, ev: Event) -> None:
        p = ev.payload
        self.w.portfolios[p["portfolio_id"]].desk_limits[p["key"]] = float(p["value"])
