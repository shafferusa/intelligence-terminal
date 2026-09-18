"""Borrowing in other currencies at their own rates.

A currency loan draws cash in any currency the FX model carries (euros, yen, francs, lira, rupiah ...) at that
currency's policy rate plus a funding spread that depends on the currency's tier and the regime. In a save that tracks
the real market the policy rates are the real ones (FRED: the ECB deposit rate, the OECD immediate rates for the
others) pinned into the FX model every session, so a yen loan costs what a yen loan costs. Interest accrues daily on an
ACT/360 basis in the loan's currency; the book carries the principal, the accrued interest and the interest expense in
the base currency, retranslating the principal at spot every session (a stronger yen makes a yen loan dearer to repay:
unrealised FX until it is repaid, realised when it is). A term loan repays itself at maturity from the currency's cash
account when the balance covers it and rolls at the then-current rate when it does not; an open loan runs until repaid
and its rate resets to the policy rate every day. Lending a currency is simply holding it: every cash balance earns its
policy rate daily.

Events carry every amount, so replay needs no market data.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import CcyLoan, Portfolio
from ..money import D, money, ZERO
from .ledger import dr, cr

TIER_SPREAD = {"G10": 0.0050, "ASIA": 0.0060, "EM_IG": 0.0100, "EM_HY": 0.0150, "STRESSED": 0.0300}
TIER = {"USD": "G10", "EUR": "G10", "GBP": "G10", "JPY": "G10", "CHF": "G10", "CAD": "G10", "AUD": "G10", "NZD": "G10", "SEK": "G10", "NOK": "G10",
        "HKD": "ASIA", "SGD": "ASIA", "SAR": "ASIA", "TWD": "ASIA",
        "CNH": "EM_IG", "KRW": "EM_IG", "THB": "EM_IG", "CZK": "EM_IG", "PLN": "EM_IG", "HUF": "EM_IG",
        "MXN": "EM_HY", "BRL": "EM_HY", "INR": "EM_HY", "IDR": "EM_HY", "ZAR": "EM_HY",
        "TRY": "STRESSED"}
REGIME_ADD = {"NORMAL_GROWTH": 0.0, "RATE_CUTTING": 0.0, "RATE_HIKING": 0.0010, "RECESSION": 0.0050, "LIQUIDITY_STRESS": 0.0150}
CAPACITY_OF_NAV = D("2.0")        # total currency borrowing, in base, up to twice the book's NAV
ACCT_PRINCIPAL, ACCT_ACCRUED, ACCT_EXPENSE = "2750", "2755", "5150"


class CcyFundingEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.CCY_LOAN_OPENED, lambda world, ev: self._h_opened(ev))
        w.on(E.CCY_LOAN_ACCRUED, lambda world, ev: self._h_accrued(ev))
        w.on(E.CCY_LOAN_TRANSLATED, lambda world, ev: self._h_translated(ev))
        w.on(E.CCY_LOAN_REPAID, lambda world, ev: self._h_repaid(ev))
        w.on(E.CCY_LOAN_ROLLED, lambda world, ev: self._h_rolled(ev))

    # ------------------------------------------------------------------ quotes
    def policy_rate(self, ccy: str) -> float:
        return float(self.w.market.fx.rate.get(ccy, self.w.market.curve().policy_rate))

    def spread(self, ccy: str) -> float:
        regime = self.w.market.state.regime if self.w.market.state else "NORMAL_GROWTH"
        return TIER_SPREAD[TIER.get(ccy, "EM_HY")] + REGIME_ADD.get(regime, 0.0)

    def borrow_rate(self, ccy: str) -> float:
        return round(max(0.0, self.policy_rate(ccy) + self.spread(ccy)), 6)

    def rates(self) -> List[Dict]:
        fxm = self.w.market.fx
        src = getattr(fxm, "rate_source", {})
        out = []
        for c in fxm.rate:
            out.append({"currency": c, "policy_rate": self.policy_rate(c), "spread": self.spread(c), "borrow_rate": self.borrow_rate(c), "tier": TIER.get(c, "EM_HY"),
                        "source": src.get(c) or ("model"), "deposit_rate": self.policy_rate(c)})
        return sorted(out, key=lambda r: r["currency"])

    def outstanding_base(self, pf: Portfolio) -> Decimal:
        return sum((l.base_value + l.accrued_base for l in pf.ccy_loans.values() if l.status == "OPEN"), ZERO)

    def capacity(self, pf: Portfolio) -> Decimal:
        nav = self.w.pnl.compute_summary(pf)["nav"]
        return max(ZERO, money(nav * CAPACITY_OF_NAV) - self.outstanding_base(pf))

    # ------------------------------------------------------------------ commands
    def borrow(self, pf: Portfolio, ccy: str, amount, term_days: int = 0, tag: Optional[str] = None) -> CcyLoan:
        from ..world import CommandError
        w = self.w
        ccy = (ccy or "").upper()
        if ccy not in w.market.fx.rate:
            raise CommandError(f"no funding market in {ccy}")
        amt = money(D(str(amount)))
        if amt <= 0:
            raise CommandError("amount must be positive")
        base = w.fx.to_base(ccy, amt)
        cap = self.capacity(pf)
        if base > cap:
            raise CommandError(f"{ccy} {amt:,.0f} is {base:,.0f} in {pf.base_currency}: over the funding line (room {cap:,.0f}, twice the NAV less what is drawn)")
        term = max(0, int(term_days or 0))
        maturity = w.calendar.add_business_days(w.current_date, term).isoformat() if term else None
        lid = w.new_id("CL")
        ev = w.emit(E.CCY_LOAN_OPENED, {"portfolio_id": pf.id, "loan_id": lid, "currency": ccy, "principal": amt, "base_value": base, "rate": self.borrow_rate(ccy),
                                        "policy_rate": self.policy_rate(ccy), "spread": self.spread(ccy), "term_days": term, "maturity": maturity, "tag": tag,
                                        "lender": {"G10": "J.P. Morgan Treasury Services", "ASIA": "HSBC Global Banking", "EM_IG": "Citi Emerging Markets Funding",
                                                   "EM_HY": "Standard Chartered", "STRESSED": "Standard Chartered"}[TIER.get(ccy, "EM_HY")]}, portfolio_id=pf.id)
        w.flush()
        return pf.ccy_loans[lid]

    def repay(self, pf: Portfolio, loan_id: str, amount=None, cause: Optional[Event] = None, note: str = "") -> CcyLoan:
        from ..world import CommandError
        w = self.w
        loan = pf.ccy_loans.get(loan_id)
        if loan is None or loan.status != "OPEN":
            raise CommandError(f"no open currency loan {loan_id}")
        principal = loan.principal if amount is None else min(loan.principal, money(D(str(amount))))
        if principal <= 0:
            raise CommandError("amount must be positive")
        full = principal >= loan.principal
        interest = loan.accrued if full else ZERO
        cash_needed = principal + interest
        bal = pf.cash_account(loan.currency).balance
        if bal < cash_needed:
            raise CommandError(f"insufficient {loan.currency} cash to repay: need {cash_needed:,.2f}, have {bal:,.2f} (deal an FX spot into {loan.currency} first)")
        frac = principal / loan.principal
        carrying = money(loan.base_value * frac)
        payload = {"portfolio_id": pf.id, "loan_id": loan.id, "currency": loan.currency, "principal": principal, "interest": interest, "carrying": carrying,
                   "accrued_base": loan.accrued_base if full else ZERO, "full": full, "note": note}
        ev = w.emit(E.CCY_LOAN_REPAID, payload, cause_id=cause.id if cause else None, portfolio_id=pf.id)
        if cause is None:
            w.flush()
        return loan

    # ------------------------------------------------------------------ the day
    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        today = w.current_date
        days = max(1, (today - prev_date).days)
        for pf in w.portfolios.values():
            for loan in list(pf.ccy_loans.values()):
                if loan.status != "OPEN":
                    continue
                rate = loan.rate if loan.maturity else self.borrow_rate(loan.currency)      # an open loan resets to the policy rate daily
                interest = money(loan.principal * D(repr(rate)) * days / 360)
                if interest:
                    w.emit(E.CCY_LOAN_ACCRUED, {"portfolio_id": pf.id, "loan_id": loan.id, "currency": loan.currency, "interest": interest, "base": w.fx.to_base(loan.currency, interest),
                                                "days": days, "rate": rate}, cause_id=cause.id, portfolio_id=pf.id)
                target = w.fx.to_base(loan.currency, loan.principal)
                if target != loan.base_value:
                    w.emit(E.CCY_LOAN_TRANSLATED, {"portfolio_id": pf.id, "loan_id": loan.id, "currency": loan.currency, "base_value": target, "delta": target - loan.base_value,
                                                   "spot": w.fx.usd(loan.currency)}, cause_id=cause.id, portfolio_id=pf.id)
                if loan.maturity and loan.maturity <= today.isoformat():
                    if pf.cash_account(loan.currency).balance >= loan.principal + loan.accrued:
                        self.repay(pf, loan.id, None, cause, note="matured: repaid from the currency account")
                    else:
                        w.emit(E.CCY_LOAN_ROLLED, {"portfolio_id": pf.id, "loan_id": loan.id, "currency": loan.currency, "new_rate": self.borrow_rate(loan.currency),
                                                   "new_maturity": w.calendar.add_business_days(today, max(1, loan.term_days)).isoformat(),
                                                   "note": f"matured with insufficient {loan.currency} cash: rolled at the current rate"}, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ handlers (apply payloads only)
    def _h_opened(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt, base = D(p["principal"]), D(p["base_value"])
        loan = CcyLoan(id=p["loan_id"], portfolio_id=pf.id, currency=p["currency"], principal=amt, rate=float(p["rate"]), policy_rate=float(p["policy_rate"]),
                       spread=float(p["spread"]), start_date=ev.sim_date, maturity=p.get("maturity"), term_days=int(p.get("term_days", 0)), base_value=base,
                       lender=p.get("lender", ""), tag=p.get("tag"))
        loan.history.append({"date": ev.sim_date, "note": f"drawn {loan.currency} {amt:,.2f} at {loan.rate:.4%} (policy {loan.policy_rate:.4%} + {loan.spread * 1e4:.0f}bp)" + (f", due {loan.maturity}" if loan.maturity else ", open")})
        pf.ccy_loans[loan.id] = loan
        received = w.fx._adjust_cash(pf, loan.currency, amt, ev, "CCY_LOAN", f"{loan.id}: {loan.currency} loan drawn", base_value_hint=base)
        w.post(pf.id, f"Currency loan {loan.id}: borrowed {loan.currency} {amt:,.2f} at {loan.rate:.4%}",
               [dr(f"1010:{loan.currency}", received, None, "loan proceeds"), cr(ACCT_PRINCIPAL, base, None, f"{loan.currency} loan principal")], ev, {"loan_id": loan.id, "kind": "CCY_LOAN"})

    def _h_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.ccy_loans[p["loan_id"]]
        interest, base = D(p["interest"]), D(p["base"])
        loan.accrued += interest
        loan.accrued_base += base
        loan.rate = float(p["rate"])
        w.post(pf.id, f"Currency loan interest {loan.id}: {loan.currency} {interest:,.2f} ({p['days']}d at {float(p['rate']):.4%})",
               [dr(ACCT_EXPENSE, base, None, f"{loan.currency} funding cost"), cr(ACCT_ACCRUED, base, None, "accrued interest payable")], ev, {"loan_id": loan.id, "kind": "CCY_LOAN"})

    def _h_translated(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.ccy_loans[p["loan_id"]]
        delta = D(p["delta"])
        loan.base_value = D(p["base_value"])
        w.post(pf.id, f"Currency loan translation {loan.id}: {loan.currency} {loan.principal:,.2f} @ {D(p['spot']):.5f}: {delta:+,.2f}",
               [dr("4600", delta, None, "unrealised FX on the loan"), cr(ACCT_PRINCIPAL, delta, None, "retranslated")], ev, {"loan_id": loan.id, "kind": "CCY_LOAN"})

    def _h_repaid(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.ccy_loans[p["loan_id"]]
        principal, interest, carrying, accrued_base = D(p["principal"]), D(p["interest"]), D(p["carrying"]), D(p["accrued_base"])
        paid = -w.fx._adjust_cash(pf, loan.currency, -(principal + interest), ev, "CCY_LOAN_REPAID", f"{loan.id}: repaid {loan.currency} {principal:,.2f} + interest {interest:,.2f}")
        loan.principal -= principal
        loan.base_value -= carrying
        loan.interest_paid += interest
        if p.get("full"):
            loan.accrued, loan.accrued_base = ZERO, ZERO
            loan.status = "REPAID"
            loan.closed_date = ev.sim_date
        realized = (carrying + accrued_base) - paid
        loan.history.append({"date": ev.sim_date, "note": (p.get("note") or "repaid") + f": {loan.currency} {principal:,.2f}" + (f" plus interest {interest:,.2f}" if interest else "") + f" (realised FX {realized:+,.2f})"})
        w.post(pf.id, f"Currency loan repaid {loan.id}: {loan.currency} {principal:,.2f}" + (f" + interest {interest:,.2f}" if interest else ""),
               [dr(ACCT_PRINCIPAL, carrying, None, "principal repaid"), dr(ACCT_ACCRUED, accrued_base, None, "accrued interest paid"),
                cr(f"1010:{loan.currency}", paid, None, "cash paid"), cr("4650", realized, None, "realised FX on the loan")], ev, {"loan_id": loan.id, "kind": "CCY_LOAN"})

    def _h_rolled(self, ev: Event) -> None:
        p = ev.payload
        loan = self.w.portfolios[p["portfolio_id"]].ccy_loans[p["loan_id"]]
        loan.rate = float(p["new_rate"])
        loan.maturity = p["new_maturity"]
        loan.rolls += 1
        loan.history.append({"date": ev.sim_date, "note": p.get("note", "rolled") + f" ({loan.rate:.4%}, due {loan.maturity})"})

    # ------------------------------------------------------------------ views
    def book(self, pf: Portfolio) -> Dict:
        loans = []
        for l in sorted(pf.ccy_loans.values(), key=lambda x: x.start_date, reverse=True):
            row = asdict(l)
            row["spot"] = float(self.w.fx.usd(l.currency))
            row["accrued_pct"] = float(l.accrued / l.principal) if l.principal else 0.0
            loans.append(row)
        return {"rates": self.rates(), "loans": loans, "outstanding_base": self.outstanding_base(pf), "capacity": self.capacity(pf),
                "cash": {c: {"balance": a.balance, "base_value": a.base_value} for c, a in pf.cash.items()}}
