"""Repo desk: repo (we borrow cash against securities we keep economic exposure
to) and reverse repo (we lend cash against collateral we receive). Both legs are
explicit: the pledge (encumbrance), the cash, the rate, the haircut, daily
interest, marks against haircut-adjusted requirements, calls with a one-cycle
grace, maturities and overnight rolls."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import Portfolio, RepoTrade, Security
from ..engines.collateral import asset_key
from ..engines.ledger import dr, cr
from ..money import D, money, qty as qqty, ZERO

COUNTERPARTIES = ["Goldman Sachs Repo Desk", "J.P. Morgan Treasury Financing", "Citigroup Global Markets Funding"]
REPO_SPREAD = {"GOVT_BOND": 0.0005, "MBS": 0.0015, "CORP_BOND_IG": 0.0045, "CORP_BOND_HY": 0.012, "ETF": 0.010, "EQUITY_LARGE": 0.010, "EQUITY_MID": 0.015}
STRESS_SPREAD = {"NORMAL_GROWTH": 0.0, "RATE_CUTTING": 0.0, "RATE_HIKING": 0.0005, "RECESSION": 0.002, "LIQUIDITY_STRESS": 0.008}
CALL_THRESHOLD_PCT = 0.005


class RepoEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.REPO_OPENED, lambda world, ev: self._h_opened(ev))
        w.on(E.REPO_INTEREST_ACCRUED, lambda world, ev: self._h_accrued(ev))
        w.on(E.REPO_ROLLED, lambda world, ev: self._h_rolled(ev))
        w.on(E.REPO_MARKED, lambda world, ev: self._h_marked(ev))
        w.on(E.REPO_ADJUSTED, lambda world, ev: self._h_adjusted(ev))
        w.on(E.REPO_CLOSED, lambda world, ev: self._h_closed(ev))

    # ------------------------------------------------------------------ quotes
    def quote(self, side: str, sec: Security, quantity, term_type: str = "OVERNIGHT", term_days: int = 1) -> Dict:
        from ..world import CommandError
        w = self.w
        q = qqty(quantity)
        h = w.collateral.haircut(sec, "REPO")
        if h is None:
            raise CommandError(f"{sec.id} is not eligible repo collateral")
        policy = w.market.curve().policy_rate
        key = asset_key(sec)
        stress = STRESS_SPREAD.get(w.market.state.regime, 0.0)
        term_prem = 0.0002 * min(12, term_days / 30) if term_type == "TERM" else 0.0
        if side == "REPO":
            rate = policy + REPO_SPREAD.get(key, 0.015) + stress + term_prem
        else:
            rate = policy - 0.0005 + (REPO_SPREAD.get(key, 0.015) * 0.5) + stress * 0.5 + term_prem
        mv = w.collateral.market_value(sec, q)
        principal = money(mv * D(str(1 - h)))
        rng_cp = COUNTERPARTIES[abs(hash((w.seed, sec.id, w.current_date.isoformat()))) % len(COUNTERPARTIES)]
        return {"side": side, "security_id": sec.id, "quantity": q, "market_value": mv, "haircut": h, "principal": principal, "rate": round(rate, 5),
                "counterparty": rng_cp, "term_type": term_type, "term_days": term_days, "policy_rate": policy, "stress_spread": stress}

    # ------------------------------------------------------------------ commands
    def open(self, pf: Portfolio, side: str, sec: Security, quantity, term_type: str = "OVERNIGHT", term_days: int = 1, auto_roll: bool = True) -> RepoTrade:
        from ..world import CommandError
        w = self.w
        side = side.upper()
        term_type = term_type.upper()
        if side not in ("REPO", "REVERSE"):
            raise CommandError("side must be REPO or REVERSE")
        if term_type not in ("OVERNIGHT", "TERM", "OPEN"):
            raise CommandError("term_type must be OVERNIGHT, TERM or OPEN")
        if term_type == "TERM" and term_days < 2:
            raise CommandError("term repo needs at least 2 days; use OVERNIGHT for one day")
        qt = self.quote(side, sec, quantity, term_type, term_days)
        q = qt["quantity"]
        if sec.lot_size > 1 and q % sec.lot_size != 0:
            raise CommandError(f"{sec.id} trades in multiples of {sec.lot_size:,} face")
        if side == "REPO":
            avail = w.collateral.available_quantity(pf, sec.id)
            if avail < q:
                raise CommandError(f"only {avail:,} {sec.id} unencumbered in custody; cannot repo securities that are pledged elsewhere or pending delivery")
        else:
            if w.trading.projected_cash(pf, pf.base_currency) < qt["principal"]:
                raise CommandError(f"reverse repo needs {qt['principal']:,.2f} of projected cash")
        maturity = None
        if term_type == "OVERNIGHT":
            maturity = w.calendar.next_business_day(w.current_date).isoformat()
        elif term_type == "TERM":
            maturity = w.calendar.add_business_days(w.current_date, int(term_days)).isoformat()
        rid = w.new_id("RP")
        w.emit(E.REPO_OPENED, {"portfolio_id": pf.id, "repo_id": rid, "side": side, "counterparty": qt["counterparty"], "security_id": sec.id, "quantity": q,
                               "maturity": maturity, "term_type": term_type, "rate": qt["rate"], "haircut": qt["haircut"], "principal": qt["principal"],
                               "market_value": qt["market_value"], "auto_roll": bool(auto_roll), "currency": pf.base_currency}, portfolio_id=pf.id)
        return pf.repos[rid]

    def close(self, pf: Portfolio, repo_id: str, cause: Optional[Event] = None, note: str = "closed by player") -> RepoTrade:
        from ..world import CommandError
        w = self.w
        r = pf.repos.get(repo_id)
        if r is None or r.status != "OPEN":
            raise CommandError(f"repo {repo_id} is not open")
        w.emit(E.REPO_CLOSED, {"portfolio_id": pf.id, "repo_id": repo_id, "principal": r.principal, "interest": r.accrued_interest, "note": note,
                               "currency": pf.base_currency}, cause_id=cause.id if cause else None, portfolio_id=pf.id)
        return r

    def post_collateral(self, pf: Portfolio, repo_id: str, sec: Security, quantity) -> RepoTrade:
        from ..world import CommandError
        w = self.w
        r = pf.repos.get(repo_id)
        if r is None or r.status != "OPEN" or r.side != "REPO":
            raise CommandError(f"{repo_id} is not an open repo")
        q = qqty(quantity)
        if not w.collateral.eligible(sec, "REPO"):
            raise CommandError(f"{sec.id} is not eligible repo collateral")
        if w.collateral.available_quantity(pf, sec.id) < q:
            raise CommandError(f"only {w.collateral.available_quantity(pf, sec.id):,} {sec.id} unencumbered")
        w.emit(E.REPO_ADJUSTED, {"portfolio_id": pf.id, "repo_id": repo_id, "kind": "ADD_COLLATERAL", "security_id": sec.id, "quantity": q, "note": "additional collateral posted"}, portfolio_id=pf.id)
        return r

    def post_cash(self, pf: Portfolio, repo_id: str, amount) -> RepoTrade:
        from ..world import CommandError
        w = self.w
        r = pf.repos.get(repo_id)
        if r is None or r.status != "OPEN" or r.side != "REPO":
            raise CommandError(f"{repo_id} is not an open repo")
        amt = money(amount)
        if amt <= 0:
            raise CommandError("amount must be positive")
        w.emit(E.REPO_ADJUSTED, {"portfolio_id": pf.id, "repo_id": repo_id, "kind": "CASH_MARGIN", "amount": amt, "note": "cash margin posted"}, portfolio_id=pf.id)
        return r

    def reduce(self, pf: Portfolio, repo_id: str, amount, cause: Optional[Event] = None, note: str = "principal reduced by player") -> RepoTrade:
        from ..world import CommandError
        w = self.w
        r = pf.repos.get(repo_id)
        if r is None or r.status != "OPEN":
            raise CommandError(f"{repo_id} is not open")
        amt = money(amount)
        if amt <= 0 or amt > r.principal:
            raise CommandError(f"amount must be between 0 and the principal {r.principal:,.2f}")
        if amt == r.principal:
            return self.close(pf, repo_id, cause, note)
        w.emit(E.REPO_ADJUSTED, {"portfolio_id": pf.id, "repo_id": repo_id, "kind": "REDUCE", "amount": amt, "note": note, "currency": pf.base_currency},
               cause_id=cause.id if cause else None, portfolio_id=pf.id)
        return r

    def substitute(self, pf: Portfolio, repo_id: str, old_security_id: str, old_quantity, new_sec: Security, new_quantity) -> RepoTrade:
        from ..world import CommandError
        w = self.w
        r = pf.repos.get(repo_id)
        if r is None or r.status != "OPEN" or r.side != "REPO":
            raise CommandError(f"{repo_id} is not an open repo")
        oq, nq = qqty(old_quantity), qqty(new_quantity)
        held = sum((p.quantity for p in pf.pledges if p.reference == repo_id and p.security_id == old_security_id), ZERO)
        if oq > held:
            raise CommandError(f"only {held:,} {old_security_id} pledged to {repo_id}")
        if not w.collateral.eligible(new_sec, "REPO"):
            raise CommandError(f"{new_sec.id} is not eligible repo collateral")
        if w.collateral.available_quantity(pf, new_sec.id) < nq:
            raise CommandError(f"only {w.collateral.available_quantity(pf, new_sec.id):,} {new_sec.id} unencumbered")
        old_val = w.collateral.collateral_value(w.securities[old_security_id], oq, "REPO")
        new_val = w.collateral.collateral_value(new_sec, nq, "REPO")
        if new_val < old_val:
            raise CommandError(f"replacement collateral value {new_val:,.0f} is below the {old_val:,.0f} being released; the old asset is released only once the requirement is met")
        w.emit(E.REPO_ADJUSTED, {"portfolio_id": pf.id, "repo_id": repo_id, "kind": "SUBSTITUTE", "old_security_id": old_security_id, "old_quantity": oq,
                                 "security_id": new_sec.id, "quantity": nq, "note": f"substituted {oq:,} {old_security_id} with {nq:,} {new_sec.id}"}, portfolio_id=pf.id)
        return r

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        today = w.current_date
        days = max(1, (today - prev_date).days)
        for pf in w.portfolios.values():
            for r in list(pf.repos.values()):
                if r.status != "OPEN":
                    continue
                interest = money(r.principal * D(str(r.rate)) * days / 360)
                if interest:
                    w.emit(E.REPO_INTEREST_ACCRUED, {"portfolio_id": pf.id, "repo_id": r.id, "interest": interest, "days": days, "rate": r.rate, "side": r.side},
                           cause_id=cause.id, portfolio_id=pf.id)
                if r.maturity and r.maturity <= today.isoformat():
                    if r.term_type == "OVERNIGHT" and r.auto_roll:
                        sec = w.securities[r.security_id]
                        qt = self.quote(r.side, sec, r.quantity, "OVERNIGHT", 1)
                        w.emit(E.REPO_ROLLED, {"portfolio_id": pf.id, "repo_id": r.id, "interest": r.accrued_interest, "new_rate": qt["rate"],
                                               "new_maturity": w.calendar.next_business_day(today).isoformat(), "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
                    else:
                        self.close(pf, r.id, cause, note="matured")
                        continue
                self._mark(pf, r, cause)
            # unresolved calls -> counterparty reduces the repo
            for c in list(pf.collateral_calls.values()):
                if c.source == "REPO" and c.status == "OPEN" and c.cycles_open >= 1:
                    r = pf.repos.get(c.reference)
                    if r and r.status == "OPEN":
                        amt = min(r.principal, c.amount)
                        if amt >= r.principal:
                            self.close(pf, r.id, cause, note=f"counterparty closed out for unmet call {c.id}")
                        else:
                            self.reduce(pf, r.id, amt, cause, note=f"counterparty reduced principal for unmet call {c.id}")
                        w.collateral.resolve_call(pf, c, "FORCED", "counterparty unwound the shortfall", cause)

    def _mark(self, pf: Portfolio, r: RepoTrade, cause: Event) -> None:
        w = self.w
        sec = w.securities[r.security_id]
        h = w.collateral.haircut(sec, "REPO") or r.haircut
        if r.side == "REPO":
            mv = sum((w.collateral.market_value(w.securities[p.security_id], p.quantity) for p in pf.pledges if p.reference == r.id), ZERO)
            cv = sum((w.collateral.collateral_value(w.securities[p.security_id], p.quantity, "REPO") for p in pf.pledges if p.reference == r.id), ZERO)
            required_cv = r.principal
            shortfall = required_cv - cv - r.cash_margin
            w.emit(E.REPO_MARKED, {"portfolio_id": pf.id, "repo_id": r.id, "collateral_mv": mv, "collateral_value": cv, "haircut": h,
                                   "required_collateral_mv": money(r.principal / D(str(1 - h))), "shortfall": shortfall}, cause_id=cause.id, portfolio_id=pf.id)
            open_call = w.collateral.open_call(pf, "REPO", r.id)
            if shortfall > max(D(10_000), money(r.principal * D(str(CALL_THRESHOLD_PCT)))):
                w.collateral.issue_or_update_call(pf, "REPO", r.id, shortfall, f"collateral shortfall on {r.id}: haircut {h:.1%}, collateral value {cv:,.0f} + cash margin {r.cash_margin:,.0f} vs principal {r.principal:,.0f}", cause)
            elif open_call:
                w.collateral.resolve_call(pf, open_call, "MET", "shortfall covered", cause)
                # release cash margin no longer needed
            if r.cash_margin > 0 and shortfall < 0:
                back = min(r.cash_margin, -shortfall)
                if back >= D(1000):
                    w.emit(E.REPO_ADJUSTED, {"portfolio_id": pf.id, "repo_id": r.id, "kind": "CASH_MARGIN", "amount": -back, "note": "excess cash margin returned"},
                           cause_id=cause.id, portfolio_id=pf.id)
        else:
            mv = w.collateral.market_value(sec, r.quantity)
            required_mv = money(r.principal / D(str(1 - h)))
            # counterparty tops up / we return excess automatically (bilateral daily margining)
            w.emit(E.REPO_MARKED, {"portfolio_id": pf.id, "repo_id": r.id, "collateral_mv": mv, "collateral_value": money(mv * D(str(1 - h))), "haircut": h,
                                   "required_collateral_mv": required_mv, "shortfall": max(ZERO, required_mv - mv)}, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ views
    def book(self, pf: Portfolio) -> Dict:
        w = self.w
        rows = []
        for r in pf.repos.values():
            pledges = [{"security_id": p.security_id, "quantity": p.quantity} for p in pf.pledges if p.reference == r.id]
            rows.append({**{k: getattr(r, k) for k in ("id", "side", "counterparty", "security_id", "quantity", "start_date", "maturity", "term_type", "rate", "haircut",
                                                       "principal", "accrued_interest", "interest_paid", "collateral_mv", "required_collateral_mv", "cash_margin", "status", "rolls", "auto_roll")},
                         "pledges": pledges, "call": w.collateral.open_call(pf, "REPO", r.id).id if w.collateral.open_call(pf, "REPO", r.id) else None,
                         "received": pf.collateral_received.get(r.id)})
        open_repo = [r for r in pf.repos.values() if r.status == "OPEN" and r.side == "REPO"]
        open_rev = [r for r in pf.repos.values() if r.status == "OPEN" and r.side == "REVERSE"]
        bal = sum((r.principal for r in open_repo), ZERO)
        avg = (sum((r.principal * D(str(r.rate)) for r in open_repo), ZERO) / bal) if bal else ZERO
        return {"rows": rows, "repo_balance": bal, "avg_repo_rate": float(avg), "reverse_balance": sum((r.principal for r in open_rev), ZERO),
                "accrued_repo_interest": sum((r.accrued_interest for r in open_repo), ZERO)}

    # ------------------------------------------------------------------ handlers
    def _h_opened(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        r = RepoTrade(id=p["repo_id"], portfolio_id=pf.id, side=p["side"], counterparty=p["counterparty"], security_id=p["security_id"], quantity=D(p["quantity"]),
                      start_date=ev.sim_date, maturity=p.get("maturity"), term_type=p["term_type"], rate=float(p["rate"]), haircut=float(p["haircut"]),
                      principal=D(p["principal"]), collateral_mv=D(p["market_value"]), auto_roll=bool(p.get("auto_roll", True)))
        r.required_collateral_mv = money(r.principal / D(str(1 - r.haircut)))
        r.history.append({"date": ev.sim_date, "note": f"opened {r.term_type} at {r.rate:.3%}, haircut {r.haircut:.1%}"})
        pf.repos[r.id] = r
        ccy = p["currency"]
        ca = pf.cash_account(ccy)
        sec = w.securities[r.security_id]
        if r.side == "REPO":
            w.collateral.pledge(pf, sec, r.quantity, "REPO", r.id, ev)
            ca.balance += r.principal
            ca.base_value += r.principal
            w.record_cash_movement(pf, ccy, r.principal, "REPO", f"Repo {r.id}: cash borrowed against {r.quantity:,} {sec.id} from {r.counterparty}", ev)
            w.post(pf.id, f"Repo {r.id}: borrow {r.principal:,.2f} vs {r.quantity:,} {sec.id} (haircut {r.haircut:.1%}, {r.rate:.3%})",
                   [dr(f"1010:{ccy}", r.principal, sec.id, "cash received"), cr("2500", r.principal, sec.id, f"repo borrowing {r.counterparty}")], ev,
                   {"repo_id": r.id, "security_id": sec.id, "kind": "REPO"})
        else:
            ca.balance -= r.principal
            ca.base_value -= r.principal
            pf.collateral_received[r.id] = {"security_id": sec.id, "quantity": r.quantity, "value": D(p["market_value"])}
            w.record_cash_movement(pf, ccy, -r.principal, "REVERSE_REPO", f"Reverse repo {r.id}: cash lent to {r.counterparty} vs {r.quantity:,} {sec.id}", ev)
            w.post(pf.id, f"Reverse repo {r.id}: lend {r.principal:,.2f} vs {r.quantity:,} {sec.id} received ({r.rate:.3%})",
                   [dr("1500", r.principal, sec.id, f"reverse repo receivable {r.counterparty}"), cr(f"1010:{ccy}", r.principal, sec.id, "cash lent")], ev,
                   {"repo_id": r.id, "security_id": sec.id, "kind": "REVERSE_REPO"})

    def _h_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        r = pf.repos[p["repo_id"]]
        i = D(p["interest"])
        r.accrued_interest += i
        if r.side == "REPO":
            lines = [dr("5500", i, r.security_id, f"repo interest {p['rate']*100:.3f}%"), cr("2330", i, r.security_id, "accrued repo interest")]
        else:
            lines = [dr("1240", i, r.security_id, "accrued reverse repo interest"), cr("4500", i, r.security_id, f"reverse repo income {p['rate']*100:.3f}%")]
        w.post(pf.id, f"Repo interest accrual {r.id}: {i:,.2f} ({p['days']}d)", lines, ev, {"repo_id": r.id, "security_id": r.security_id, "kind": "REPO_INTEREST"})

    def _pay_interest(self, pf: Portfolio, r: RepoTrade, ev: Event, ccy: str) -> None:
        w = self.w
        i = r.accrued_interest
        if i == 0:
            return
        ca = pf.cash_account(ccy)
        if r.side == "REPO":
            ca.balance -= i
            ca.base_value -= i
            w.record_cash_movement(pf, ccy, -i, "REPO_INTEREST", f"Repo interest paid {r.id}", ev)
            lines = [dr("2330", i, r.security_id, "accrued interest paid"), cr(f"1010:{ccy}", i, r.security_id, "cash")]
        else:
            ca.balance += i
            ca.base_value += i
            w.record_cash_movement(pf, ccy, i, "REPO_INTEREST", f"Reverse repo interest received {r.id}", ev)
            lines = [dr(f"1010:{ccy}", i, r.security_id, "cash"), cr("1240", i, r.security_id, "accrued interest received")]
        r.interest_paid += i
        r.accrued_interest = ZERO
        w.post(pf.id, f"Repo interest settled {r.id}: {i:,.2f}", lines, ev, {"repo_id": r.id, "security_id": r.security_id})

    def _h_rolled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        r = pf.repos[p["repo_id"]]
        self._pay_interest(pf, r, ev, p["currency"])
        r.rate = float(p["new_rate"])
        r.maturity = p["new_maturity"]
        r.rolls += 1
        r.history.append({"date": ev.sim_date, "note": f"rolled overnight at {r.rate:.3%}"})

    def _h_marked(self, ev: Event) -> None:
        p = ev.payload
        r = self.w.portfolios[p["portfolio_id"]].repos[p["repo_id"]]
        r.collateral_mv = D(p["collateral_mv"])
        r.required_collateral_mv = D(p["required_collateral_mv"])
        r.haircut = float(p["haircut"])
        if r.side == "REVERSE":
            rec = self.w.portfolios[p["portfolio_id"]].collateral_received.get(r.id)
            if rec is not None:
                rec["value"] = max(D(p["collateral_mv"]), D(p["required_collateral_mv"]))

    def _h_adjusted(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        r = pf.repos[p["repo_id"]]
        kind = p["kind"]
        if kind == "ADD_COLLATERAL":
            w.collateral.pledge(pf, w.securities[p["security_id"]], D(p["quantity"]), "REPO", r.id, ev)
        elif kind == "CASH_MARGIN":
            amt = D(p["amount"])
            w.collateral.move_cash(pf, amt, r.id, "REPO", ev, note=p.get("note", ""))
            r.cash_margin += amt
        elif kind == "REDUCE":
            amt = D(p["amount"])
            ccy = p["currency"]
            ca = pf.cash_account(ccy)
            ca.balance -= amt
            ca.base_value -= amt
            r.principal -= amt
            r.required_collateral_mv = money(r.principal / D(str(1 - r.haircut)))
            w.record_cash_movement(pf, ccy, -amt, "REPO", f"Repo {r.id} principal reduced: {p.get('note', '')}", ev)
            w.post(pf.id, f"Repo {r.id} principal reduced by {amt:,.2f}", [dr("2500", amt, r.security_id, "repo borrowing repaid"), cr(f"1010:{ccy}", amt, r.security_id, "cash")],
                   ev, {"repo_id": r.id, "security_id": r.security_id})
        elif kind == "SUBSTITUTE":
            w.collateral.pledge(pf, w.securities[p["security_id"]], D(p["quantity"]), "REPO", r.id, ev)
            w.collateral.release(pf, p["old_security_id"], D(p["old_quantity"]), r.id, ev, note="substituted")
        r.history.append({"date": ev.sim_date, "note": p.get("note", kind)})

    def _h_closed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        r = pf.repos[p["repo_id"]]
        ccy = p["currency"]
        self._pay_interest(pf, r, ev, ccy)
        ca = pf.cash_account(ccy)
        if r.side == "REPO":
            ca.balance -= r.principal
            ca.base_value -= r.principal
            w.record_cash_movement(pf, ccy, -r.principal, "REPO", f"Repo {r.id} repaid ({p.get('note', '')})", ev)
            w.post(pf.id, f"Repo {r.id} closed: repay {r.principal:,.2f}", [dr("2500", r.principal, r.security_id, "repo borrowing repaid"), cr(f"1010:{ccy}", r.principal, r.security_id, "cash")],
                   ev, {"repo_id": r.id, "security_id": r.security_id})
            for pl in [x for x in pf.pledges if x.reference == r.id]:
                w.collateral.release(pf, pl.security_id, pl.quantity, r.id, ev, note="repo closed")
            cm = pf.cash_collateral.get(r.id, ZERO)
            if cm:
                w.collateral.move_cash(pf, -cm, r.id, "REPO", ev, note="cash margin returned on close")
            r.cash_margin = ZERO
        else:
            ca.balance += r.principal
            ca.base_value += r.principal
            pf.collateral_received.pop(r.id, None)
            w.record_cash_movement(pf, ccy, r.principal, "REVERSE_REPO", f"Reverse repo {r.id} principal returned ({p.get('note', '')})", ev)
            w.post(pf.id, f"Reverse repo {r.id} closed: principal {r.principal:,.2f} returned", [dr(f"1010:{ccy}", r.principal, r.security_id, "cash"), cr("1500", r.principal, r.security_id, "receivable extinguished")],
                   ev, {"repo_id": r.id, "security_id": r.security_id})
        r.status = "CLOSED"
        r.history.append({"date": ev.sim_date, "note": p.get("note", "closed")})
