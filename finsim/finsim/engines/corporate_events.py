"""Corporate events beyond cash dividends: stock dividends, special dividends,
cash tender offers, rights issues, cash mergers (delisting), spin-offs and
bond calls, plus settlement fail charges and buy-ins.

Two halves:
  * `CorporateEventModel` (market side): seeded announcement of events with
    future effective dates; the day's announcements and price reactions are
    part of the market close so replay ingests them.
  * `CorporateEventsEngine` (world side): turns announcements into corporate
    actions with entitlements, takes the player's elections (tender, subscribe)
    and processes effective dates through ordinary trades, cash and custody.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..domain.events import E, Event
from ..domain.models import CorporateAction, Portfolio, Security
from ..engines.ledger import dr, cr
from ..money import D, money, price as qprice, qty as qqty, ZERO

EVENT_TYPES = ("STOCK_DIVIDEND", "SPECIAL_DIVIDEND", "TENDER_OFFER", "RIGHTS_ISSUE", "CASH_MERGER", "SPIN_OFF", "BOND_CALL")
ANNUAL_PROB = {"STOCK_DIVIDEND": 0.06, "SPECIAL_DIVIDEND": 0.08, "TENDER_OFFER": 0.05, "RIGHTS_ISSUE": 0.05, "CASH_MERGER": 0.03, "SPIN_OFF": 0.03}
FAIL_CHARGE_RATE = 0.03            # annual, on the cash amount of a failed delivery (TMPG-style)
BUY_IN_AFTER_FAILS = 5             # failed business days before the receiving party buys in
BUY_IN_PENALTY = 0.02
CALLABLE = {"AAL-28": {"call_price": 102.0, "first_call_years": 2.0}, "F-32": {"call_price": 101.0, "first_call_years": 3.0}}


class CorporateEventModel:
    """Announces events (pure function of seed and date)."""

    def __init__(self, seed: int, cal):
        self.seed = seed
        self.cal = cal
        self.announced: List[Dict] = []
        self._pending_by_sid: Dict[str, str] = {}      # sid -> effective date of an outstanding event (one at a time per name)
        self._seq = 0

    def step(self, d: date, securities: Dict[str, Security], regime: str, prices: Dict[str, float], curve_level: float) -> Tuple[List[Dict], List[Dict], Dict[str, float]]:
        """Returns (announcements, news, price shocks) for the day."""
        out: List[Dict] = []
        news: List[Dict] = []
        shocks: Dict[str, float] = {}
        # clear expired pendings
        for sid in list(self._pending_by_sid):
            if self._pending_by_sid[sid] <= d.isoformat():
                del self._pending_by_sid[sid]
        for sid, sec in securities.items():
            if sec.asset_class not in ("EQUITY", "ADR", "REIT") or sec.expired or getattr(sec, "delisted", False) or sid in self._pending_by_sid:
                continue
            rng = random.Random(f"{self.seed}|cevent|{sid}|{d.isoformat()}")
            u = rng.random()
            acc = 0.0
            kind = None
            for k, p in ANNUAL_PROB.items():
                acc += p / 252.0 * (1.5 if regime in ("RECESSION", "LIQUIDITY_STRESS") and k in ("RIGHTS_ISSUE", "CASH_MERGER") else 1.0)
                if u < acc:
                    kind = k
                    break
            if kind is None:
                continue
            px = prices.get(sid)
            if not px:
                continue
            self._seq += 1
            eff = self.cal.add_business_days(d, rng.randint(15, 30))
            ev = {"id": f"CE-{sid}-{d.isoformat()}", "kind": kind, "security_id": sid, "announced": d.isoformat(), "effective": eff.isoformat(), "terms": {}}
            if kind == "STOCK_DIVIDEND":
                ratio = rng.choice([0.05, 0.10, 0.20])
                ev["terms"] = {"ratio": ratio}
                shocks[sid] = 0.0
                news.append({"code": sid, "headline": f"{sec.name} declares a {ratio:.0%} stock dividend", "category": "CORPORATE_ACTION",
                             "body": f"Holders receive {ratio:.0%} additional shares on {eff.isoformat()}; the price and listed option contracts adjust by the same ratio."})
            elif kind == "SPECIAL_DIVIDEND":
                amt = round(px * rng.uniform(0.03, 0.08), 2)
                ev["terms"] = {"amount": amt}
                shocks[sid] = 0.01
                news.append({"code": sid, "headline": f"{sec.name} declares a special dividend of ${amt:.2f}", "category": "CORPORATE_ACTION",
                             "body": f"A one-off cash dividend of ${amt:.2f} per share goes ex on {eff.isoformat()} and pays ten business days later."})
            elif kind == "TENDER_OFFER":
                prem = rng.uniform(0.15, 0.35)
                offer = round(px * (1 + prem), 2)
                ev["terms"] = {"offer_price": offer, "max_pct": rng.choice([0.15, 0.25, 0.35]), "deadline": self.cal.add_business_days(eff, -2).isoformat(), "bidder": rng.choice(["KKR", "Blackstone", "Apollo Global Management", "a strategic acquirer"])}
                shocks[sid] = prem * 0.7
                news.append({"code": sid, "headline": f"{ev['terms']['bidder']} launches a cash tender for up to {ev['terms']['max_pct']:.0%} of {sec.name} at ${offer:.2f}", "category": "CORPORATE_ACTION",
                             "body": f"Holders may tender shares until {ev['terms']['deadline']}; tendered shares are bought at ${offer:.2f} on {eff.isoformat()}. Shares jump toward the offer."})
            elif kind == "RIGHTS_ISSUE":
                disc = rng.uniform(0.15, 0.30)
                sub = round(px * (1 - disc), 2)
                ev["terms"] = {"subscription_price": sub, "ratio": rng.choice([0.10, 0.20, 0.25]), "deadline": self.cal.add_business_days(eff, -2).isoformat()}
                shocks[sid] = -0.02 - disc * ev["terms"]["ratio"] * 0.5
                news.append({"code": sid, "headline": f"{sec.name} announces a rights issue: 1 new share per {1 / ev['terms']['ratio']:.0f} held at ${sub:.2f}", "category": "CORPORATE_ACTION",
                             "body": f"Holders of record on {d.isoformat()} may subscribe until {ev['terms']['deadline']}; new shares are issued on {eff.isoformat()}. Unexercised rights lapse. The shares fell on dilution."})
            elif kind == "CASH_MERGER":
                prem = rng.uniform(0.20, 0.45)
                deal = round(px * (1 + prem), 2)
                ev["terms"] = {"deal_price": deal, "acquirer": rng.choice(["KKR", "Blackstone", "Berkshire Hathaway", "a private consortium"])}
                shocks[sid] = prem * 0.85
                news.append({"code": sid, "headline": f"{sec.name} agrees to be acquired for ${deal:.2f} a share in cash", "category": "CORPORATE_ACTION",
                             "body": f"{ev['terms']['acquirer']} will pay ${deal:.2f} per share; the deal closes on {eff.isoformat()}, when all shares are exchanged for cash and the stock is delisted. "
                                     "Listed options settle at intrinsic value against the deal price."})
            elif kind == "SPIN_OFF":
                frac = rng.uniform(0.10, 0.25)
                ratio = rng.choice([0.25, 0.5, 1.0])
                ev["terms"] = {"fraction": frac, "ratio": ratio, "new_id": f"{sid[:3]}S", "new_name": f"{sec.name.split()[0]} Spinco", "sector": sec.sector}
                shocks[sid] = 0.015
                news.append({"code": sid, "headline": f"{sec.name} to spin off {frac:.0%} of its business as {ev['terms']['new_name']}", "category": "CORPORATE_ACTION",
                             "body": f"Holders receive {ratio:g} share(s) of {ev['terms']['new_id']} per share held on {eff.isoformat()}; the parent's price drops by the value spun out."})
            out.append(ev)
            self._pending_by_sid[sid] = ev["effective"]
        # bond calls: issuer calls when it can refinance cheaper (spread tightened, rates low) after the first call date
        for bid, spec in CALLABLE.items():
            sec = securities.get(bid)
            if sec is None or sec.expired or getattr(sec, "called", None) or bid in self._pending_by_sid:
                continue
            issue = date.fromisoformat(sec.issue_date)
            if (d - issue).days / 365.0 < spec["first_call_years"]:
                continue
            rng = random.Random(f"{self.seed}|call|{bid}|{d.isoformat()}")
            px = prices.get(bid, 100.0)
            if px > spec["call_price"] and rng.random() < 0.25 / 252 * 12:
                eff = self.cal.add_business_days(d, 30)
                ev = {"id": f"CE-{bid}-{d.isoformat()}", "kind": "BOND_CALL", "security_id": bid, "announced": d.isoformat(), "effective": eff.isoformat(),
                      "terms": {"call_price": spec["call_price"]}}
                out.append(ev)
                self._pending_by_sid[bid] = eff.isoformat()
                news.append({"code": bid, "headline": f"{sec.issuer} calls its {sec.coupon:.2%} notes at {spec['call_price']:.1f}", "category": "CORPORATE_ACTION",
                             "body": f"The {sec.id} bonds will be redeemed at {spec['call_price']:.1f} plus accrued interest on {eff.isoformat()}; the issuer refinances at lower yields."})
        for ev in out:
            self.announced.append(ev)
        return out, news, shocks

    def ingest(self, announcements: List[Dict]) -> None:
        for ev in announcements:
            self.announced.append(ev)
            self._pending_by_sid[ev["security_id"]] = ev["effective"]


class CorporateEventsEngine:
    """World side: elections, effective-date processing, fail charges and buy-ins."""

    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.CORPORATE_EVENT_ANNOUNCED, lambda world, ev: self._h_announced(ev))
        w.on(E.CORPORATE_ELECTION, lambda world, ev: self._h_election(ev))
        w.on(E.CORPORATE_EVENT_PROCESSED, lambda world, ev: self._h_processed(ev))
        w.on(E.SECURITY_LISTED, lambda world, ev: self._h_listed(ev))
        w.on(E.SECURITY_DELISTED, lambda world, ev: self._h_delisted(ev))
        w.on(E.FAIL_CHARGE, lambda world, ev: self._h_fail_charge(ev))
        w.on(E.BUY_IN, lambda world, ev: None)

    # ------------------------------------------------------------------ announcements (from the market close)
    def announce(self, cause: Event) -> None:
        w = self.w
        for ev in w.market.corporate_announcements():
            if ev["id"] in w.corporate_actions:
                continue
            w.emit(E.CORPORATE_EVENT_ANNOUNCED, ev, cause_id=cause.id)

    def _h_announced(self, ev: Event) -> None:
        p = ev.payload
        w = self.w
        t = p["terms"]
        amount = D(str(t.get("amount", t.get("ratio", t.get("offer_price", t.get("deal_price", t.get("call_price", t.get("subscription_price", 0))))))))
        ca = CorporateAction(id=p["id"], security_id=p["security_id"], action_type=p["kind"], declared_date=p["announced"], ex_date=p["effective"],
                             record_date=p["announced"], pay_date=p["effective"], amount_per_unit=amount, currency="USD", status="DECLARED")
        ca.entitlements["_terms"] = dict(t)
        w.corporate_actions[ca.id] = ca

    # ------------------------------------------------------------------ elections
    def elect(self, pf: Portfolio, ca_id: str, quantity) -> Dict:
        """Tender shares into an offer or subscribe to a rights issue (quantity = shares tendered / new shares subscribed)."""
        from ..world import CommandError
        w = self.w
        ca = w.corporate_actions.get(ca_id)
        if ca is None or ca.action_type not in ("TENDER_OFFER", "RIGHTS_ISSUE"):
            raise CommandError("elections apply to tender offers and rights issues")
        if ca.status != "DECLARED":
            raise CommandError(f"{ca_id} is {ca.status}")
        terms = ca.entitlements["_terms"]
        if w.current_date.isoformat() > terms["deadline"]:
            raise CommandError(f"the election deadline {terms['deadline']} has passed")
        q = qqty(quantity)
        pos = pf.positions.get(ca.security_id)
        held = pos.quantity if pos else ZERO
        if q <= 0:
            raise CommandError("quantity must be positive")
        if ca.action_type == "TENDER_OFFER":
            cap = qqty(held * D(str(terms["max_pct"])))
            if q > held:
                raise CommandError(f"cannot tender more than the {held:,} shares held")
            if q > cap:
                raise CommandError(f"the offer accepts at most {terms['max_pct']:.0%} of holdings ({cap:,} shares)")
        else:
            cap = qqty(held * D(str(terms["ratio"])))
            if q > cap:
                raise CommandError(f"rights allow at most {cap:,} new shares (1 per {1 / terms['ratio']:.0f} held)")
            cost = money(q * D(str(terms["subscription_price"])))
            why = w.prime.affordable(pf, cost, w.collateral.haircut(w.securities[ca.security_id], "PRIME"))
            if why:
                raise CommandError(f"subscription of {cost:,.0f} not financeable: {why}")
        ev = w.emit(E.CORPORATE_ELECTION, {"portfolio_id": pf.id, "ca_id": ca.id, "quantity": q, "kind": ca.action_type}, portfolio_id=pf.id)
        return {"event_id": ev.id, "quantity": q}

    def _h_election(self, ev: Event) -> None:
        p = ev.payload
        ca = self.w.corporate_actions[p["ca_id"]]
        ca.entitlements[p["portfolio_id"]] = {"elected": D(p["quantity"]), "paid": False}

    # ------------------------------------------------------------------ effective dates
    def process_day(self, cause: Event) -> None:
        w = self.w
        today = w.current_date.isoformat()
        for ca in list(w.corporate_actions.values()):
            if ca.action_type not in EVENT_TYPES or ca.status != "DECLARED" or ca.ex_date != today:
                continue
            terms = ca.entitlements.get("_terms", {})
            sec = w.securities[ca.security_id]
            ev = w.emit(E.CORPORATE_EVENT_PROCESSED, {"ca_id": ca.id, "kind": ca.action_type, "security_id": ca.security_id, "terms": terms}, cause_id=cause.id)
            if ca.action_type == "STOCK_DIVIDEND":
                w.options.apply_split(sec.id, 1.0 + float(terms["ratio"]), ev)
            elif ca.action_type == "SPECIAL_DIVIDEND":
                pay = w.calendar.add_business_days(w.current_date, 10).isoformat()
                did = f"DIV-{sec.id}-{today}-SPECIAL"
                if did not in w.corporate_actions:
                    w.emit(E.DIVIDEND_DECLARED, {"ca_id": did, "security_id": sec.id, "amount": money(D(str(terms["amount"]))), "currency": "USD", "declared_date": ca.declared_date,
                                                 "ex_date": today, "record_date": today, "pay_date": pay}, cause_id=ev.id)
                    w.corporate.entitle_today(w.corporate_actions[did], ev)
            elif ca.action_type == "TENDER_OFFER":
                for pf in w.portfolios.values():
                    ent = ca.entitlements.get(pf.id)
                    if ent and ent.get("elected"):
                        q = min(D(ent["elected"]), pf.positions[sec.id].quantity if sec.id in pf.positions else ZERO)
                        if q > 0:
                            w.trading.system_order(pf, sec, "SELL", q, ev, f"tender offer {ca.id}: {q:,} shares accepted at {terms['offer_price']}", forced=False,
                                                   fixed_price=D(str(terms["offer_price"])), commission_free=True)
                        ent["paid"] = True
            elif ca.action_type == "RIGHTS_ISSUE":
                for pf in w.portfolios.values():
                    ent = ca.entitlements.get(pf.id)
                    if ent and ent.get("elected"):
                        q = D(ent["elected"])
                        w.trading.system_order(pf, sec, "BUY", q, ev, f"rights issue {ca.id}: {q:,} new shares subscribed at {terms['subscription_price']}", forced=False,
                                               fixed_price=D(str(terms["subscription_price"])), commission_free=True)
                        ent["paid"] = True
            elif ca.action_type == "CASH_MERGER":
                deal = D(str(terms["deal_price"]))
                # listed options settle at intrinsic against the deal price, then every holder is cashed out and the stock delists
                w.options.settle_underlying_at(sec.id, float(deal), ev, f"cash merger of {sec.id} at {deal}")
                for pf in w.portfolios.values():
                    pos = pf.positions.get(sec.id)
                    if pos and pos.quantity != 0:
                        side = "SELL" if pos.quantity > 0 else "BUY"
                        w.trading.system_order(pf, sec, side, abs(pos.quantity), ev, f"cash merger {ca.id}: {abs(pos.quantity):,} shares exchanged for cash at {deal}", forced=False,
                                               fixed_price=deal, commission_free=True)
                for pf in w.portfolios.values():
                    for t in list(pf.otc_trades.values()):
                        if t.status == "OPEN" and t.product == "TRS" and t.terms.get("security_id") == sec.id:
                            w.otc.terminate(pf, t.id, ev, f"reference {sec.id} acquired for cash: swap terminated at the deal price", cost_mult=0.0)
                w.emit(E.SECURITY_DELISTED, {"security_id": sec.id, "reason": f"acquired for cash at {deal}", "last_price": deal}, cause_id=ev.id)
            elif ca.action_type == "SPIN_OFF":
                self._spin_off(sec, terms, ev)
            elif ca.action_type == "BOND_CALL":
                call_px = D(str(terms["call_price"]))
                for pf in w.portfolios.values():
                    pos = pf.positions.get(sec.id)
                    if pos and pos.quantity > 0:
                        w.emit(E.BOND_MATURED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity, "principal": money(pos.quantity * call_px / 100),
                                                "currency": sec.currency, "called": True, "call_price": float(call_px)}, cause_id=ev.id, portfolio_id=pf.id)
                w.emit(E.SECURITY_DELISTED, {"security_id": sec.id, "reason": f"called at {call_px}", "last_price": call_px}, cause_id=ev.id)

    def _spin_off(self, parent: Security, terms: Dict, ev: Event) -> None:
        w = self.w
        new_id = terms["new_id"]
        px = float(w.market.last_bar(parent.id).close)
        frac, ratio = float(terms["fraction"]), float(terms["ratio"])
        new_px = round(px * frac / ratio, 2)
        if new_id not in w.securities:
            w.emit(E.SECURITY_LISTED, {"security_id": new_id, "name": terms["new_name"], "asset_class": "EQUITY", "sector": terms["sector"], "price": new_px,
                                       "parent": parent.id, "beta": parent.beta * 1.1, "sigma_annual": parent.sigma_annual * 1.3, "adv": int(parent.adv * 0.4),
                                       "shares_outstanding": int((parent.shares_outstanding or 1_000_000) * ratio), "liquidity_tier": "MID" if parent.liquidity_tier == "LARGE" else "SMALL",
                                       "spread_bps": parent.spread_bps * 2}, cause_id=ev.id)
        for pf in w.portfolios.values():
            pos = pf.positions.get(parent.id)
            if pos and pos.quantity > 0:
                q = qqty(pos.quantity * D(repr(ratio)))
                if q > 0:
                    w.emit(E.CORPORATE_EVENT_PROCESSED, {"ca_id": None, "kind": "SPIN_OFF_DISTRIBUTION", "security_id": new_id, "parent": parent.id, "portfolio_id": pf.id,
                                                         "quantity": q, "price": new_px, "cost_fraction": frac}, cause_id=ev.id, portfolio_id=pf.id)

    def _h_processed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        if p.get("ca_id") and p["ca_id"] in w.corporate_actions:
            w.corporate_actions[p["ca_id"]].status = "PAID"
        if p["kind"] == "SPIN_OFF_DISTRIBUTION":
            # the parent's cost basis is split pro rata; the new shares arrive in custody (no cash)
            from ..domain.models import Lot
            pf = w.portfolios[p["portfolio_id"]]
            parent = pf.position(p["parent"])
            child = pf.position(p["security_id"])
            q = D(p["quantity"])
            frac = D(repr(float(p["cost_fraction"])))
            moved = money(parent.cost_basis * frac)
            for lot in parent.lots:
                lot.cost_total = money(lot.cost_total * (1 - frac))
                lot.cost_per_unit = qprice(lot.cost_total / lot.quantity) if lot.quantity else lot.cost_per_unit
            parent.cost_basis -= moved
            child.lots.append(Lot(id=f"LOT-{ev.seq:06d}", trade_id=ev.id, open_date=ev.sim_date, quantity=q, original_quantity=q, cost_per_unit=qprice(moved / q), cost_total=moved))
            child.quantity += q
            child.settled_quantity += q
            child.cost_basis += moved
            w.record_custody_movement(pf, p["security_id"], q, "SPIN_OFF", f"received from {p['parent']}", ev)
            w.post(pf.id, f"Spin-off: {q:,} {p['security_id']} received from {p['parent']} (cost {moved:,.2f} carved out)",
                   [dr("1100", moved, p["security_id"], "cost basis allocated to spin-off"), cr("1100", moved, p["parent"], "cost basis carved out")], ev,
                   {"security_id": p["security_id"], "kind": "SPIN_OFF"})
            w.pnl.mark_position(pf, p["parent"], ev)
            w.pnl.mark_position(pf, p["security_id"], ev)

    def _h_listed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        if p["security_id"] in w.securities:
            return
        sec = Security(id=p["security_id"], name=p["name"], asset_class=p["asset_class"], market="US_EQUITY", currency="USD", country="US", sector=p["sector"],
                       isin=f"XS{ev.seq:010d}", cusip=f"S{ev.seq:08d}", shares_outstanding=int(p["shares_outstanding"]), dividend_yield=0.0, beta=float(p["beta"]),
                       sigma_annual=float(p["sigma_annual"]), adv=int(p["adv"]), spread_bps=float(p["spread_bps"]), liquidity_tier=p["liquidity_tier"], fundamentals={})
        w.securities[sec.id] = sec
        w.market.list_security(sec, D(str(p["price"])), ev.sim_date)

    def _h_delisted(self, ev: Event) -> None:
        w = self.w
        sec = w.securities[ev.payload["security_id"]]
        sec.expired = True
        sec.delisted = True
        for c in w.securities.values():
            if c.is_option and c.underlying == sec.id:
                c.expired = True

    # ------------------------------------------------------------------ settlement fails: charges and buy-ins
    def process_fails(self, cause: Event) -> None:
        w = self.w
        for pf in w.portfolios.values():
            for si in list(pf.settlements.values()):
                if si.status != "FAILED":
                    continue
                sec = w.securities[si.security_id]
                if sec.is_option:
                    continue
                charge = money(si.cash_amount * D(str(FAIL_CHARGE_RATE)) / 360)
                if charge > 0:
                    w.emit(E.FAIL_CHARGE, {"portfolio_id": pf.id, "si_id": si.id, "security_id": si.security_id, "amount": charge, "currency": si.currency,
                                           "reason": f"{'delivery' if si.instruction_type == 'DVP' else 'payment'} failed ({si.fail_count} day(s))"}, cause_id=cause.id, portfolio_id=pf.id)
                if si.instruction_type == "DVP" and si.fail_count >= BUY_IN_AFTER_FAILS and not sec.is_bond:
                    pos = pf.position(si.security_id)
                    short = si.quantity - max(ZERO, pos.settled_quantity)
                    if short > 0:
                        ev = w.emit(E.BUY_IN, {"portfolio_id": pf.id, "si_id": si.id, "security_id": si.security_id, "quantity": short,
                                               "note": f"receiving party bought in {short:,} {si.security_id} after {si.fail_count} failed days; penalty {BUY_IN_PENALTY:.0%}"},
                                    cause_id=cause.id, portfolio_id=pf.id)
                        w.trading.system_order(pf, sec, "BUY", short, ev, f"buy-in for failed delivery {si.id}", forced=True)
                        penalty = money(short * w.market.last_bar(sec.id).close * D(str(BUY_IN_PENALTY)))
                        w.emit(E.FAIL_CHARGE, {"portfolio_id": pf.id, "si_id": si.id, "security_id": si.security_id, "amount": penalty, "currency": si.currency,
                                               "reason": "buy-in penalty"}, cause_id=ev.id, portfolio_id=pf.id)

    def _h_fail_charge(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ca = pf.cash_account(p["currency"])
        ca.balance -= amt
        if p["currency"] == pf.base_currency:
            ca.base_value -= amt
        else:
            ca.base_value -= w.fx.to_base(p["currency"], amt)
        pf.fail_charges += amt
        w.record_cash_movement(pf, p["currency"], -amt, "FAIL_CHARGE", f"{p['reason']} on {p['si_id']} {p['security_id']}", ev)
        w.post(pf.id, f"Fail charge {p['si_id']} {p['security_id']}: {amt:,.2f} ({p['reason']})",
               [dr("5800", amt, p["security_id"], "settlement fail charge"), cr(f"1010:{p['currency']}", amt, p["security_id"], "charge paid")], ev,
               {"si_id": p["si_id"], "kind": "FAIL_CHARGE"})
