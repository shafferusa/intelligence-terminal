"""Shared collateral engine.

Every product that takes or gives collateral (securities lending, repo, prime
brokerage; OTC later) goes through here, so an asset can never be counted twice:

* eligibility and haircuts are a schedule by asset class and liquidity tier,
  scaled up in stressed regimes;
* encumbrance is a registry of pledges (security, quantity, purpose, reference);
  the sellable / pledgeable quantity of a security is what is settled in custody
  minus what is already pledged or pending delivery;
* cash collateral moves through an explicit asset account (posted) and is
  tracked per reference;
* collateral calls are one object regardless of source, with an issue date, a
  due date (next processing cycle) and a status.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import CollateralCall, Pledge, Portfolio, Security
from ..engines.ledger import dr, cr
from ..money import D, money, ZERO

# base haircuts by (purpose) -> asset key. Purposes: REPO (secured financing), SECLOAN (collateral to a lender),
# PRIME (financing value at the prime broker).
HAIRCUTS = {
    "GOVT_BOND": {"REPO": 0.02, "SECLOAN": 0.02, "PRIME": 0.03, "LENT": 0.0},
    "MBS": {"REPO": 0.03, "SECLOAN": 0.03, "PRIME": 0.05, "LENT": 0.0},
    "CORP_BOND_IG": {"REPO": 0.08, "SECLOAN": None, "PRIME": 0.12, "LENT": 0.0},
    "CORP_BOND_HY": {"REPO": 0.15, "SECLOAN": None, "PRIME": 0.25, "LENT": 0.0},
    "EQUITY_LARGE": {"REPO": 0.25, "SECLOAN": None, "PRIME": 0.25, "LENT": 0.0},
    "EQUITY_MID": {"REPO": 0.35, "SECLOAN": None, "PRIME": 0.35, "LENT": 0.0},
    "EQUITY_SMALL": {"REPO": None, "SECLOAN": None, "PRIME": 0.50, "LENT": 0.0},
    "PREFERRED": {"REPO": None, "SECLOAN": None, "PRIME": 0.40, "LENT": 0.0},
    "ADR": {"REPO": None, "SECLOAN": None, "PRIME": 0.35, "LENT": 0.0},
    "ETF": {"REPO": 0.20, "SECLOAN": None, "PRIME": 0.20, "LENT": 0.0},
    "FUTURE": {"REPO": None, "SECLOAN": None, "PRIME": None},
    "PHYSICAL": {"REPO": None, "SECLOAN": None, "PRIME": None, "LENT": None},
    "CRYPTO": {"REPO": None, "SECLOAN": None, "PRIME": 0.50, "LENT": 0.0},
    "INDEX": {"REPO": None, "SECLOAN": None, "PRIME": None, "LENT": None},
}
REGIME_HAIRCUT_MULT = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.0, "RATE_HIKING": 1.1, "RECESSION": 1.5, "LIQUIDITY_STRESS": 2.5}
IG_RATINGS = {"AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-"}


def asset_key(sec: Security) -> str:
    if sec.is_future:
        return "FUTURE"
    if sec.asset_class == "PHYSICAL":
        return "PHYSICAL"
    if sec.asset_class == "GOVT_BOND":
        return "GOVT_BOND"
    if sec.asset_class == "MBS_TBA":
        return "MBS"
    if sec.asset_class in ("CORP_BOND", "STRUCTURED"):
        return "CORP_BOND_IG" if (sec.rating or "") in IG_RATINGS else "CORP_BOND_HY"
    if sec.asset_class in ("PREFERRED", "ADR", "ETF", "CRYPTO", "INDEX"):
        return sec.asset_class
    return {"LARGE": "EQUITY_LARGE", "MID": "EQUITY_MID", "SMALL": "EQUITY_SMALL"}.get(sec.liquidity_tier, "EQUITY_MID")


class CollateralEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.COLLATERAL_PLEDGED, lambda world, ev: self._h_pledged(ev))
        w.on(E.COLLATERAL_RELEASED, lambda world, ev: self._h_released(ev))
        w.on(E.CASH_COLLATERAL_MOVED, lambda world, ev: self._h_cash_moved(ev))
        w.on(E.COLLATERAL_CALL, lambda world, ev: self._h_call(ev))

    # ------------------------------------------------------------------ schedule
    def regime_mult(self) -> float:
        return REGIME_HAIRCUT_MULT.get(self.w.market.state.regime if self.w.market.state else "NORMAL_GROWTH", 1.0)

    def haircut(self, sec: Security, purpose: str) -> Optional[float]:
        base = HAIRCUTS.get(asset_key(sec), {}).get(purpose)
        if base is None:
            return None
        return min(0.75, base * self.regime_mult())

    def eligible(self, sec: Security, purpose: str) -> bool:
        return self.haircut(sec, purpose) is not None

    def market_value(self, sec: Security, quantity: Decimal) -> Decimal:
        """Dirty market value of a quantity (bonds include accrued)."""
        from ..engines.pricing import Instrument
        if not self.w.market.history.get(sec.id):
            return ZERO
        inst = Instrument(sec, self.w.market.last_bar(sec.id).close, self.w.current_date, self.w.market.curve())
        return inst.market_value(quantity, include_accrued=True)

    def collateral_value(self, sec: Security, quantity: Decimal, purpose: str) -> Decimal:
        h = self.haircut(sec, purpose)
        if h is None:
            return ZERO
        return money(self.market_value(sec, quantity) * D(str(1 - h)))

    # ------------------------------------------------------------------ availability
    def available_quantity(self, pf: Portfolio, security_id: str, exclude_ref: Optional[str] = None, include_covered: bool = False) -> Decimal:
        """Settled, unencumbered quantity not committed to a pending delivery. Shares covering written calls are reserved
        (they cannot be sold, lent or pledged) unless `include_covered` (prime-broker collateral value still counts them)."""
        pos = pf.positions.get(security_id)
        if pos is None:
            return ZERO
        reserved = ZERO if include_covered else self.w.options.covered_shares_needed(pf, security_id)
        return pos.settled_quantity - pos.pending_deliver - pf.pledged_quantity(security_id, exclude_ref) - reserved

    def pledge(self, pf: Portfolio, sec: Security, quantity: Decimal, purpose: str, reference: str, cause: Event) -> None:
        from ..world import CommandError
        if quantity <= 0:
            raise CommandError("pledge quantity must be positive")
        if not self.eligible(sec, purpose):
            raise CommandError(f"{sec.id} is not eligible collateral for {purpose}")
        avail = self.available_quantity(pf, sec.id)
        if avail < quantity:
            raise CommandError(f"only {avail:,} {sec.id} unencumbered in custody (need {quantity:,}); assets pledged elsewhere or pending delivery cannot be re-used")
        # derived from its cause so replay never double-applies (the stored child event is replayed in sequence)
        self.w.derive(E.COLLATERAL_PLEDGED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": quantity, "purpose": purpose, "reference": reference,
                                             "market_value": self.market_value(sec, quantity), "haircut": self.haircut(sec, purpose),
                                             "collateral_value": self.collateral_value(sec, quantity, purpose)}, cause, portfolio_id=pf.id)

    def release(self, pf: Portfolio, security_id: str, quantity: Decimal, reference: str, cause: Event, note: str = "") -> None:
        held = sum((p.quantity for p in pf.pledges if p.reference == reference and p.security_id == security_id), ZERO)
        q = min(quantity, held)
        if q <= 0:
            return
        self.w.derive(E.COLLATERAL_RELEASED, {"portfolio_id": pf.id, "security_id": security_id, "quantity": q, "reference": reference, "note": note},
                      cause, portfolio_id=pf.id)

    def move_cash(self, pf: Portfolio, amount: Decimal, reference: str, purpose: str, cause: Event, note: str = "") -> None:
        """amount > 0 posts cash to the counterparty; < 0 returns it."""
        amount = money(amount)
        if amount == 0:
            return
        self.w.derive(E.CASH_COLLATERAL_MOVED, {"portfolio_id": pf.id, "amount": amount, "reference": reference, "purpose": purpose, "note": note,
                                                "currency": pf.base_currency}, cause, portfolio_id=pf.id)

    def pledged_to(self, pf: Portfolio, reference: str) -> List[Pledge]:
        return [p for p in pf.pledges if p.reference == reference]

    # ------------------------------------------------------------------ calls
    def issue_or_update_call(self, pf: Portfolio, source: str, reference: str, amount: Decimal, reason: str, cause: Event) -> CollateralCall:
        existing = next((c for c in pf.collateral_calls.values() if c.status == "OPEN" and c.reference == reference and c.source == source), None)
        due = self.w.calendar.next_business_day(self.w.current_date).isoformat()
        if existing is None:
            cid = f"CALL-{self.w.next_seq():06d}"
            self.w.emit(E.COLLATERAL_CALL, {"portfolio_id": pf.id, "call_id": cid, "source": source, "reference": reference, "amount": money(amount),
                                            "status": "OPEN", "due": due, "reason": reason, "cycles_open": 0}, cause_id=cause.id, portfolio_id=pf.id)
            return pf.collateral_calls[cid]
        self.w.emit(E.COLLATERAL_CALL, {"portfolio_id": pf.id, "call_id": existing.id, "source": source, "reference": reference, "amount": money(amount),
                                        "status": "OPEN", "due": existing.due, "reason": reason, "cycles_open": existing.cycles_open + 1}, cause_id=cause.id, portfolio_id=pf.id)
        return existing

    def resolve_call(self, pf: Portfolio, call: CollateralCall, status: str, reason: str, cause: Event) -> None:
        self.w.emit(E.COLLATERAL_CALL, {"portfolio_id": pf.id, "call_id": call.id, "source": call.source, "reference": call.reference, "amount": ZERO,
                                        "status": status, "due": call.due, "reason": reason, "cycles_open": call.cycles_open}, cause_id=cause.id, portfolio_id=pf.id)

    def open_call(self, pf: Portfolio, source: str, reference: str) -> Optional[CollateralCall]:
        return next((c for c in pf.collateral_calls.values() if c.status == "OPEN" and c.reference == reference and c.source == source), None)

    # ------------------------------------------------------------------ dashboard
    def dashboard(self, pf: Portfolio) -> Dict:
        w = self.w
        available, encumbered = [], []
        for pos in pf.positions.values():
            if pos.is_future or pos.quantity <= 0 and pos.settled_quantity <= 0:
                continue
            sec = w.securities[pos.security_id]
            pledged = pf.pledged_quantity(sec.id)
            avail = self.available_quantity(pf, sec.id)
            mv_avail = self.market_value(sec, avail) if avail > 0 else ZERO
            row = {"security_id": sec.id, "name": sec.name, "asset_key": asset_key(sec), "settled": pos.settled_quantity, "pledged": pledged,
                   "pending_deliver": pos.pending_deliver, "available": avail, "market_value_available": mv_avail,
                   "haircuts": {p: self.haircut(sec, p) for p in ("REPO", "SECLOAN", "PRIME")},
                   "collateral_value": {p: (self.collateral_value(sec, avail, p) if avail > 0 else ZERO) for p in ("REPO", "SECLOAN", "PRIME")}}
            available.append(row)
            for p in pf.pledges:
                if p.security_id == sec.id:
                    encumbered.append({"security_id": sec.id, "quantity": p.quantity, "purpose": p.purpose, "reference": p.reference,
                                       "market_value": self.market_value(sec, p.quantity)})
        cash = pf.cash_account(pf.base_currency).balance
        posted = {"SECLOAN": ZERO, "REPO": ZERO, "PRIME": ZERO, "OTC": ZERO}
        for ref, amt in pf.cash_collateral.items():
            src = "SECLOAN" if ref.startswith("LN-") else "REPO" if ref.startswith("RP-") else "OTC" if ref.startswith(("CSA:", "IM:")) else "PRIME"
            posted[src] += amt
        posted_sec = {"SECLOAN": ZERO, "REPO": ZERO, "PRIME": ZERO}
        for p in pf.pledges:
            if p.purpose == "LENT":
                continue
            posted_sec[p.purpose] = posted_sec.get(p.purpose, ZERO) + self.market_value(w.securities[p.security_id], p.quantity)
        received = {"SECLOAN": ZERO, "REVERSE_REPO": ZERO, "OTC_VM": sum((c.vm_received for c in pf.csas.values()), ZERO)}
        for ref, r in pf.collateral_received.items():
            received["REVERSE_REPO" if ref.startswith("RP-") else "SECLOAN"] += D(r["value"])
        calls = [c for c in pf.collateral_calls.values()]
        total_avail_cv = sum((r["collateral_value"]["REPO"] for r in available), ZERO) + max(ZERO, cash)
        tsy_total = sum((pos.settled_quantity for pos in pf.positions.values() if w.securities[pos.security_id].asset_class == "GOVT_BOND"), ZERO)
        tsy_pledged = sum((p.quantity for p in pf.pledges if w.securities[p.security_id].asset_class == "GOVT_BOND"), ZERO)
        return {"available": available, "encumbered": encumbered, "cash_unencumbered": max(ZERO, cash), "cash_collateral_posted": posted,
                "securities_collateral_posted": posted_sec, "received": received, "calls": calls, "regime_haircut_multiplier": self.regime_mult(),
                "available_collateral_value_repo": total_avail_cv,
                "treasury_encumbrance_pct": float(tsy_pledged / tsy_total) if tsy_total else 0.0}

    # ------------------------------------------------------------------ handlers
    def _h_pledged(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.pledges.append(Pledge(p["reference"], p["purpose"], p["security_id"], D(p["quantity"])))

    def _h_released(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        remaining = D(p["quantity"])
        for pl in list(pf.pledges):
            if remaining <= 0:
                break
            if pl.reference == p["reference"] and pl.security_id == p["security_id"]:
                take = min(pl.quantity, remaining)
                pl.quantity -= take
                remaining -= take
                if pl.quantity <= 0:
                    pf.pledges.remove(pl)

    def _h_cash_moved(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ccy = p["currency"]
        ca = pf.cash_account(ccy)
        ca.balance -= amt
        ca.base_value -= amt
        pf.cash_collateral[p["reference"]] = pf.cash_collateral.get(p["reference"], ZERO) + amt
        if pf.cash_collateral[p["reference"]] == 0:
            del pf.cash_collateral[p["reference"]]
        w.record_cash_movement(pf, ccy, -amt, "COLLATERAL", f"Cash collateral {'posted to' if amt > 0 else 'returned from'} {p['reference']} ({p['purpose']}) {p.get('note', '')}".strip(), ev)
        sid = p["reference"] if False else None
        lines = [dr("1400", amt, None, f"cash collateral posted {p['reference']}"), cr(f"1010:{ccy}", amt, None, "cash out")] if amt > 0 else \
                [dr(f"1010:{ccy}", -amt, None, "cash back"), cr("1400", -amt, None, f"cash collateral returned {p['reference']}")]
        w.post(pf.id, f"Cash collateral {amt:+,.2f} {p['reference']} ({p['purpose']})", lines, ev, {"reference": p["reference"], "kind": "COLLATERAL"})

    def _h_call(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        c = pf.collateral_calls.get(p["call_id"])
        if c is None:
            pf.collateral_calls[p["call_id"]] = CollateralCall(id=p["call_id"], portfolio_id=pf.id, source=p["source"], reference=p["reference"],
                                                                amount=D(p["amount"]), issued=ev.sim_date, due=p["due"], status=p["status"], reason=p["reason"],
                                                                cycles_open=int(p.get("cycles_open", 0)))
        else:
            c.amount, c.status, c.reason, c.cycles_open = D(p["amount"]), p["status"], p["reason"], int(p.get("cycles_open", c.cycles_open))
            if p["status"] != "OPEN":
                c.resolved = ev.sim_date
