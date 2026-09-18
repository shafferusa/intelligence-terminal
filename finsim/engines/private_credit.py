"""Private credit: direct lending to sponsor-backed private companies.

A pipeline of deals refreshes every month (deterministic from the seed): each is a senior or junior loan to a
private company owned by a real private-equity sponsor, described by sector rather than by an invented name.
The player commits an amount (min $1m) while the deal is open; the loan funds at par less the OID, pays a
floating coupon (policy rate + spread) quarterly, accretes its discount, and is marked daily off a loan-market
spread that follows the high-yield index and the borrower's own drift. Ratings migrate monthly; defaults pay
recovery after a workout; covenant breaches are amended for a fee and a wider spread; loans can be sold in the
secondary market at a bid below the mark. Everything is an event, so replay rebuilds the book exactly.

Accounts: 1170 loans (par), 1175 valuation adjustment, 1225 accrued interest; 4320 interest, 4330 fees and
accretion, 4340 mark-to-market and realised sale gains/losses, 5350 credit losses.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import Portfolio
from ..engines.ledger import dr, cr
from ..money import D, money, ZERO

SPONSORS = ["KKR", "Blackstone", "Apollo", "Carlyle", "TPG", "Bain Capital", "Thoma Bravo", "Vista Equity", "Warburg Pincus", "Silver Lake", "Advent", "Hellman & Friedman"]
BORROWERS = {
    "Health Care": ["dental services platform", "physician practice group", "medical device distributor", "behavioural health provider", "specialty pharmacy"],
    "Information Technology": ["vertical SaaS provider", "managed IT services firm", "payments software company", "cybersecurity vendor", "data-centre operator"],
    "Industrials": ["aerospace components maker", "industrial distributor", "waste services company", "logistics provider", "building products manufacturer"],
    "Consumer Discretionary": ["restaurant franchisor", "fitness chain", "e-commerce retailer", "auto aftermarket parts supplier", "consumer brands roll-up"],
    "Consumer Staples": ["pet food producer", "snacks manufacturer", "beverage bottler", "personal-care brand"],
    "Communication Services": ["fibre broadband operator", "outdoor advertising company", "digital media group"],
    "Financials": ["insurance brokerage roll-up", "wealth management aggregator", "specialty finance lender"],
    "Energy": ["midstream services company", "oilfield services provider"],
    "Materials": ["specialty chemicals maker", "packaging manufacturer"],
}
FACILITIES = {   # base spread (bp) over policy, OID (pts), recovery on default, leverage range, typical rating
    "FIRST_LIEN": (475, 1.5, 0.65, (4.0, 5.5), ["B+", "B", "B"]),
    "UNITRANCHE": (575, 2.0, 0.60, (5.0, 6.5), ["B", "B", "B-"]),
    "SECOND_LIEN": (825, 2.5, 0.35, (6.0, 7.5), ["B-", "B-", "CCC+"]),
    "MEZZANINE": (1050, 3.0, 0.20, (6.5, 8.0), ["CCC+", "B-", "CCC+"]),
}
RATINGS = ["BB-", "B+", "B", "B-", "CCC+", "CCC", "D"]
RATING_SPREAD_MULT = {"BB-": 0.8, "B+": 0.9, "B": 1.0, "B-": 1.15, "CCC+": 1.45, "CCC": 1.9}
REGIME_SPREAD = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 0.95, "RATE_HIKING": 1.1, "RECESSION": 1.5, "LIQUIDITY_STRESS": 2.0}
MIN_COMMIT = 1_000_000
WORKOUT_BD = 60          # business days from default to recovery cash


@dataclass
class PrivateLoan:
    id: str
    portfolio_id: str
    deal_id: str
    borrower: str
    sponsor: str
    sector: str
    facility: str
    par: Decimal
    original_par: Decimal
    spread_bps: float
    oid_pct: float
    rating: str
    leverage: float
    start_date: str
    maturity: str
    recovery: float
    price: float = 1.0                  # fraction of par, the mark
    accrued_interest: Decimal = ZERO
    interest_paid: Decimal = ZERO
    fees_received: Decimal = ZERO
    market_spread_bps: float = 0.0
    idio: float = 0.0                   # borrower-specific spread drift (log)
    status: str = "OPEN"                # OPEN | DEFAULTED | RECOVERED | SOLD | MATURED
    default_date: Optional[str] = None
    recovery_due: Optional[str] = None
    amendments: int = 0
    history: List[Dict] = field(default_factory=list)
    next_coupon: str = ""


class PrivateCreditEngine:
    def __init__(self, world):
        self.w = world
        self.committed: Dict[str, Decimal] = {}          # deal id -> amount already taken (all books)

    def register(self) -> None:
        w = self.w
        w.on(E.PC_COMMITTED, lambda world, ev: self._h_committed(ev))
        w.on(E.PC_INTEREST_ACCRUED, lambda world, ev: self._h_accrued(ev))
        w.on(E.PC_INTEREST_PAID, lambda world, ev: self._h_paid(ev))
        w.on(E.PC_MARKED, lambda world, ev: self._h_marked(ev))
        w.on(E.PC_RATING_CHANGED, lambda world, ev: self._h_rating(ev))
        w.on(E.PC_AMENDED, lambda world, ev: self._h_amended(ev))
        w.on(E.PC_DEFAULTED, lambda world, ev: self._h_defaulted(ev))
        w.on(E.PC_RECOVERED, lambda world, ev: self._h_recovered(ev))
        w.on(E.PC_SOLD, lambda world, ev: self._h_sold(ev))
        w.on(E.PC_MATURED, lambda world, ev: self._h_matured(ev))

    # ------------------------------------------------------------------ market inputs
    def policy_rate(self) -> float:
        return float(self.w.market.curves[-1].policy_rate) if self.w.market.curves else 0.04

    def hy_scale(self) -> float:
        curves = self.w.market.curves
        if not curves:
            return 1.0
        return float(curves[-1].hy_spread_bps) / 360.0

    def regime(self) -> str:
        return self.w.market.state.regime if self.w.market.state else "NORMAL_GROWTH"

    # ------------------------------------------------------------------ the pipeline
    def pipeline(self, d: Optional[date] = None) -> List[Dict]:
        """This month's deals. Deterministic: same seed, same month, same deals; the amounts already taken are replayed."""
        w = self.w
        d = d or w.current_date
        rng = random.Random(f"{w.seed}|pcredit|{d.year}-{d.month:02d}")
        n = 6 + rng.randint(0, 3)
        out = []
        sectors = list(BORROWERS)
        month_start = date(d.year, d.month, 1)
        close = w.calendar.roll(date(d.year + (d.month // 12), d.month % 12 + 1, 1))   # closes on the first business day of next month
        scale = self._month_scale(d)
        for i in range(n):
            fac = rng.choices(list(FACILITIES), weights=[4, 4, 2, 1])[0]
            base, oid, rec, lev_rng, ratings = FACILITIES[fac]
            sector = rng.choice(sectors)
            sponsor = rng.choice(SPONSORS)
            rating = rng.choice(ratings)
            spread = round(base * RATING_SPREAD_MULT[rating] * max(0.7, scale) * rng.uniform(0.92, 1.08) / 25) * 25
            size = rng.choice([50, 75, 100, 150, 200, 250, 300, 400, 500, 750]) * 1_000_000
            term = rng.choice([4, 5, 5, 6, 7])
            lev = round(rng.uniform(*lev_rng), 1)
            did = f"PCD-{d.year % 100:02d}{d.month:02d}-{i + 1:02d}"
            taken = self.committed.get(did, ZERO)
            out.append({"id": did, "borrower": f"{sponsor}-backed {rng.choice(BORROWERS[sector])}", "sponsor": sponsor, "sector": sector, "facility": fac,
                        "size": size, "available": max(0, size - float(taken)), "spread_bps": spread, "oid_pct": oid, "term_years": term, "rating": rating,
                        "leverage": lev, "recovery": rec, "opened": month_start.isoformat(), "closes": close.isoformat(), "all_in_yield": self.policy_rate() + spread / 1e4 + oid / 100 / term,
                        "covenants": f"net leverage ≤ {lev + 1.0:.1f}x, interest cover ≥ {1.5 if fac in ('FIRST_LIEN', 'UNITRANCHE') else 1.2:.1f}x, tested quarterly",
                        "min_commitment": MIN_COMMIT, "status": "OPEN" if d < close else "CLOSED"})
        return out

    def _month_scale(self, d: date) -> float:
        """Spread scale fixed at the month's first close, so a deal's terms do not drift while it is open."""
        curves = self.w.market.curves
        first = next((c for c in curves if c.date[:7] == d.isoformat()[:7]), curves[-1] if curves else None)
        hy = float(first.hy_spread_bps) / 360.0 if first else 1.0
        return hy * REGIME_SPREAD.get(self.regime(), 1.0)

    def deal(self, deal_id: str, d: Optional[date] = None) -> Optional[Dict]:
        return next((x for x in self.pipeline(d) if x["id"] == deal_id), None)

    # ------------------------------------------------------------------ commands
    def commit(self, pf: Portfolio, deal_id: str, amount) -> PrivateLoan:
        from ..world import CommandError
        w = self.w
        job = w.careers.job_for(pf)
        if job and job.allowed_classes and "PRIVATE_CREDIT" not in job.allowed_classes and "OTC" not in job.allowed_classes and "CORP_BOND" not in job.allowed_classes:
            raise CommandError("private credit is outside this job's mandate")
        deal = self.deal(deal_id)
        if deal is None:
            raise CommandError(f"unknown deal {deal_id}: the pipeline refreshes monthly")
        if deal["status"] != "OPEN":
            raise CommandError(f"{deal_id} has closed")
        amt = money(amount)
        if amt < MIN_COMMIT:
            raise CommandError(f"minimum commitment is {MIN_COMMIT:,.0f}")
        if amt % 100_000 != 0:
            raise CommandError("commit in multiples of 100,000")
        if float(amt) > deal["available"] + 1e-6:
            raise CommandError(f"only {deal['available']:,.0f} of {deal_id} is still available")
        funded = money(amt * D(str(1 - deal["oid_pct"] / 100)))
        reason = w.prime.affordable(pf, funded, None)
        if reason:
            raise CommandError(f"cannot fund {funded:,.0f}: {reason}")
        lid = w.new_id("PCL")
        maturity = w.calendar.roll(date(w.current_date.year + deal["term_years"], w.current_date.month, min(28, w.current_date.day))).isoformat()
        w.emit(E.PC_COMMITTED, {"portfolio_id": pf.id, "loan_id": lid, "deal": deal, "amount": amt, "funded": funded, "maturity": maturity,
                                "currency": pf.base_currency, "policy_rate": self.policy_rate()}, portfolio_id=pf.id)
        return pf.private_loans[lid]

    def bid(self, loan: PrivateLoan) -> float:
        """Secondary-market bid: a point or two under the mark, wider in stress, nothing for a loan in workout."""
        haircut = {"NORMAL_GROWTH": 0.01, "RATE_CUTTING": 0.01, "RATE_HIKING": 0.015, "RECESSION": 0.025, "LIQUIDITY_STRESS": 0.05}.get(self.regime(), 0.015)
        if loan.status == "DEFAULTED":
            haircut += 0.05
        return max(0.05, loan.price - haircut)

    def sell(self, pf: Portfolio, loan_id: str, amount) -> PrivateLoan:
        from ..world import CommandError
        w = self.w
        loan = pf.private_loans.get(loan_id)
        if loan is None or loan.status not in ("OPEN", "DEFAULTED"):
            raise CommandError("no such open loan")
        amt = money(amount)
        if amt <= 0 or amt > loan.par:
            raise CommandError(f"sell between 0 and the loan's par {loan.par:,.0f}")
        if amt % 100_000 != 0 and amt != loan.par:
            raise CommandError("sell in multiples of 100,000 (or the whole loan)")
        bid = self.bid(loan)
        w.emit(E.PC_SOLD, {"portfolio_id": pf.id, "loan_id": loan.id, "amount": amt, "bid": bid, "proceeds": money(amt * D(repr(bid))), "currency": pf.base_currency},
               portfolio_id=pf.id)
        return loan

    # ------------------------------------------------------------------ daily process
    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        today = w.current_date
        days = max(1, (today - prev_date).days)
        new_month = today.month != prev_date.month
        for pf in w.portfolios.values():
            for loan in list(pf.private_loans.values()):
                if loan.status == "RECOVERED" or loan.status == "SOLD" or loan.status == "MATURED":
                    continue
                if loan.status == "DEFAULTED":
                    if loan.recovery_due and today.isoformat() >= loan.recovery_due:
                        w.emit(E.PC_RECOVERED, {"portfolio_id": pf.id, "loan_id": loan.id, "recovery": loan.recovery, "cash": money(loan.par * D(repr(loan.recovery))),
                                                "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
                    continue
                # coupon accrual and quarterly payment
                rate = self.policy_rate() + loan.spread_bps / 1e4
                interest = money(loan.par * D(repr(rate)) * days / 360)
                if interest:
                    w.emit(E.PC_INTEREST_ACCRUED, {"portfolio_id": pf.id, "loan_id": loan.id, "interest": interest, "rate": rate, "days": days}, cause_id=cause.id, portfolio_id=pf.id)
                if loan.next_coupon and today.isoformat() >= loan.next_coupon:
                    nxt = self._add_months(date.fromisoformat(loan.next_coupon), 3)
                    w.emit(E.PC_INTEREST_PAID, {"portfolio_id": pf.id, "loan_id": loan.id, "amount": loan.accrued_interest, "next_coupon": w.calendar.roll(nxt).isoformat(),
                                                "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
                # monthly review: migration, covenants, default
                if new_month:
                    self._review(pf, loan, cause)
                    if loan.status == "DEFAULTED":
                        continue
                # maturity
                if today.isoformat() >= loan.maturity:
                    w.emit(E.PC_MATURED, {"portfolio_id": pf.id, "loan_id": loan.id, "par": loan.par, "accrued": loan.accrued_interest, "currency": pf.base_currency},
                           cause_id=cause.id, portfolio_id=pf.id)
                    continue
                # mark
                self._mark(pf, loan, cause)

    def _review(self, pf: Portfolio, loan: PrivateLoan, cause: Event) -> None:
        w = self.w
        today = w.current_date
        rng = random.Random(f"{w.seed}|pcreview|{loan.id}|{today.year}-{today.month:02d}")
        stress = loan.market_spread_bps / max(1.0, loan.spread_bps) - 1.0
        idx = RATINGS.index(loan.rating) if loan.rating in RATINGS else 2
        # default hazard: from the loan's own market spread, worse in bad regimes
        hazard = (loan.market_spread_bps / 1e4) / max(0.1, 1 - loan.recovery) * {"LIQUIDITY_STRESS": 1.6, "RECESSION": 1.1}.get(self.regime(), 0.35)
        p_default = hazard / 12.0
        if rng.random() < p_default:
            due = w.calendar.add_business_days(today, WORKOUT_BD).isoformat()
            w.emit(E.PC_DEFAULTED, {"portfolio_id": pf.id, "loan_id": loan.id, "recovery": loan.recovery, "recovery_due": due, "accrued_written_off": loan.accrued_interest,
                                    "reason": rng.choice(["missed interest payment", "filed for Chapter 11", "liquidity shortfall after a lost contract", "sponsor declined to inject equity"])},
                   cause_id=cause.id, portfolio_id=pf.id)
            return
        if stress > 0.35 and idx < len(RATINGS) - 2 and rng.random() < min(0.6, 0.2 + stress * 0.4):
            w.emit(E.PC_RATING_CHANGED, {"portfolio_id": pf.id, "loan_id": loan.id, "from": loan.rating, "to": RATINGS[idx + 1], "reason": "spreads wide of the rating's norm"},
                   cause_id=cause.id, portfolio_id=pf.id)
        elif stress < -0.2 and idx > 0 and rng.random() < 0.12:
            w.emit(E.PC_RATING_CHANGED, {"portfolio_id": pf.id, "loan_id": loan.id, "from": loan.rating, "to": RATINGS[idx - 1], "reason": "deleveraging ahead of plan"},
                   cause_id=cause.id, portfolio_id=pf.id)
        # covenant test: a breach is cured by an amendment (fee to the lender, wider spread)
        if rng.random() < (0.10 if stress > 0.25 else 0.03):
            fee = money(loan.par * D("0.005"))
            w.emit(E.PC_AMENDED, {"portfolio_id": pf.id, "loan_id": loan.id, "fee": fee, "spread_add_bps": 75.0, "currency": pf.base_currency,
                                  "reason": rng.choice(["leverage covenant breached: amend-and-extend", "interest cover below the test: waiver for a fee"])},
                   cause_id=cause.id, portfolio_id=pf.id)

    def _mark(self, pf: Portfolio, loan: PrivateLoan, cause: Event) -> None:
        w = self.w
        today = w.current_date
        rng = random.Random(f"{w.seed}|pcmark|{loan.id}|{today.isoformat()}")
        # borrower-specific drift on the market spread, mean-reverting
        loan.idio = loan.idio * 0.98 + rng.gauss(0, 0.012)
        mkt = loan.spread_bps * RATING_SPREAD_MULT.get(loan.rating, 1.3) / RATING_SPREAD_MULT.get(loan.history[0].get("rating", loan.rating), 1.0) if loan.history else loan.spread_bps
        mkt = mkt * max(0.6, self.hy_scale() * REGIME_SPREAD.get(self.regime(), 1.0)) * math.exp(loan.idio)
        yrs = max(0.1, (date.fromisoformat(loan.maturity) - today).days / 365.0)
        dur = min(yrs, 3.5) * 0.85
        total = max(1, (date.fromisoformat(loan.maturity) - date.fromisoformat(loan.start_date)).days)
        remaining = max(0, (date.fromisoformat(loan.maturity) - today).days) / total
        pull_to_par = 1 - loan.oid_pct / 100 * remaining
        price = pull_to_par * (1 - dur * (mkt - loan.spread_bps) / 1e4)
        price = max(0.3, min(1.015, price))
        if abs(price - loan.price) >= 0.00005 or abs(mkt - loan.market_spread_bps) >= 0.5:
            w.emit(E.PC_MARKED, {"portfolio_id": pf.id, "loan_id": loan.id, "price": round(price, 6), "market_spread_bps": round(mkt, 2), "idio": round(loan.idio, 6)},
                   cause_id=cause.id, portfolio_id=pf.id)

    @staticmethod
    def _add_months(d: date, n: int) -> date:
        m = d.month - 1 + n
        y = d.year + m // 12
        m = m % 12 + 1
        return date(y, m, min(d.day, 28))

    # ------------------------------------------------------------------ views
    def book(self, pf: Portfolio) -> Dict:
        loans = []
        for l in pf.private_loans.values():
            row = asdict(l)
            row["market_value"] = money(l.par * D(repr(l.price)))
            row["carrying"] = row["market_value"] + l.accrued_interest
            row["bid"] = self.bid(l) if l.status in ("OPEN", "DEFAULTED") else None
            row["coupon_rate"] = self.policy_rate() + l.spread_bps / 1e4
            row["unrealized"] = money(l.par * D(repr(l.price - 1))) + money(l.original_par * D(repr(l.oid_pct / 100))) * (l.par / l.original_par if l.original_par else 1)
            loans.append(row)
        open_par = sum((l.par for l in pf.private_loans.values() if l.status in ("OPEN", "DEFAULTED")), ZERO)
        return {"pipeline": self.pipeline(), "loans": loans, "market": {"policy_rate": self.policy_rate(), "hy_scale": self.hy_scale(), "regime": self.regime()},
                "totals": {"open_par": open_par, "market_value": sum((money(l.par * D(repr(l.price))) for l in pf.private_loans.values() if l.status in ("OPEN", "DEFAULTED")), ZERO),
                           "accrued": sum((l.accrued_interest for l in pf.private_loans.values()), ZERO), "interest_received": sum((l.interest_paid for l in pf.private_loans.values()), ZERO),
                           "fees": sum((l.fees_received for l in pf.private_loans.values()), ZERO),
                           "weighted_spread_bps": (float(sum((l.par * D(repr(l.spread_bps)) for l in pf.private_loans.values() if l.status in ("OPEN", "DEFAULTED")), ZERO)) / float(open_par)) if open_par else 0.0}}

    # ------------------------------------------------------------------ handlers
    def _h_committed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        deal = p["deal"]
        amt, funded = D(p["amount"]), D(p["funded"])
        loan = PrivateLoan(id=p["loan_id"], portfolio_id=pf.id, deal_id=deal["id"], borrower=deal["borrower"], sponsor=deal["sponsor"], sector=deal["sector"],
                           facility=deal["facility"], par=amt, original_par=amt, spread_bps=float(deal["spread_bps"]), oid_pct=float(deal["oid_pct"]), rating=deal["rating"],
                           leverage=float(deal["leverage"]), start_date=ev.sim_date, maturity=p["maturity"], recovery=float(deal["recovery"]),
                           price=1 - float(deal["oid_pct"]) / 100, market_spread_bps=float(deal["spread_bps"]))
        loan.next_coupon = w.calendar.roll(self._add_months(date.fromisoformat(ev.sim_date), 3)).isoformat()
        loan.history.append({"date": ev.sim_date, "note": f"committed {amt:,.0f} at {deal['spread_bps']:.0f}bp over policy, OID {deal['oid_pct']:.1f}pt, {deal['rating']}", "rating": deal["rating"]})
        pf.private_loans[loan.id] = loan
        self.committed[deal["id"]] = self.committed.get(deal["id"], ZERO) + amt
        ccy = p["currency"]
        ca = pf.cash_account(ccy)
        ca.balance -= funded
        ca.base_value -= funded
        discount = amt - funded
        w.record_cash_movement(pf, ccy, -funded, "PRIVATE_CREDIT", f"{loan.id}: funded {deal['borrower']} ({deal['facility'].lower().replace('_', ' ')})", ev)
        w.post(pf.id, f"Private credit {loan.id}: fund {amt:,.0f} par at {100 - deal['oid_pct']:.1f}",
               [dr("1170", amt, loan.id, "loan at par"), cr(f"1010:{ccy}", funded, loan.id, "cash funded"), cr("1175", discount, loan.id, "original issue discount")], ev,
               {"loan_id": loan.id, "kind": "PRIVATE_CREDIT"})

    def _h_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        i = D(p["interest"])
        loan.accrued_interest += i
        w.post(pf.id, f"Private credit interest accrual {loan.id}: {i:,.2f}", [dr("1225", i, loan.id, "accrued interest"), cr("4320", i, loan.id, f"coupon {p['rate']:.3%}")], ev,
               {"loan_id": loan.id, "kind": "PRIVATE_CREDIT_INTEREST"})

    def _h_paid(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        amt = D(p["amount"])
        loan.next_coupon = p["next_coupon"]
        if amt <= 0:
            return
        ccy = p["currency"]
        ca = pf.cash_account(ccy)
        ca.balance += amt
        ca.base_value += amt
        loan.accrued_interest -= amt
        loan.interest_paid += amt
        w.record_cash_movement(pf, ccy, amt, "PRIVATE_CREDIT_INTEREST", f"{loan.id}: quarterly coupon", ev)
        w.post(pf.id, f"Private credit coupon {loan.id}: {amt:,.2f}", [dr(f"1010:{ccy}", amt, loan.id, "coupon received"), cr("1225", amt, loan.id, "accrued interest settled")], ev,
               {"loan_id": loan.id, "kind": "PRIVATE_CREDIT_INTEREST"})

    def _h_marked(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        new_price = float(p["price"])
        loan.market_spread_bps = float(p["market_spread_bps"])
        loan.idio = float(p.get("idio", loan.idio))
        delta = money(loan.par * D(repr(new_price - loan.price)))
        loan.price = new_price
        if delta:
            lines = [dr("1175", delta, loan.id, "mark"), cr("4340", delta, loan.id, "mark-to-market")] if delta > 0 else [dr("4340", -delta, loan.id, "mark-to-market"), cr("1175", -delta, loan.id, "mark")]
            w.post(pf.id, f"Private credit mark {loan.id}: {new_price * 100:.2f}", lines, ev, {"loan_id": loan.id, "kind": "PRIVATE_CREDIT_MTM"})

    def _h_rating(self, ev: Event) -> None:
        p = ev.payload
        loan = self.w.portfolios[p["portfolio_id"]].private_loans[p["loan_id"]]
        loan.rating = p["to"]
        loan.history.append({"date": ev.sim_date, "note": f"{p['from']} → {p['to']}: {p['reason']}", "rating": loan.history[0].get("rating", loan.rating) if loan.history else loan.rating})

    def _h_amended(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        fee = D(p["fee"])
        loan.spread_bps += float(p["spread_add_bps"])
        loan.amendments += 1
        loan.fees_received += fee
        loan.history.append({"date": ev.sim_date, "note": f"amended: {p['reason']}; +{p['spread_add_bps']:.0f}bp, fee {fee:,.0f}", "rating": loan.history[0].get("rating", loan.rating) if loan.history else loan.rating})
        ccy = p["currency"]
        ca = pf.cash_account(ccy)
        ca.balance += fee
        ca.base_value += fee
        w.record_cash_movement(pf, ccy, fee, "PRIVATE_CREDIT_FEE", f"{loan.id}: amendment fee", ev)
        w.post(pf.id, f"Private credit amendment fee {loan.id}: {fee:,.2f}", [dr(f"1010:{ccy}", fee, loan.id, "fee received"), cr("4330", fee, loan.id, "amendment fee")], ev,
               {"loan_id": loan.id, "kind": "PRIVATE_CREDIT_FEE"})

    def _h_defaulted(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        loan.status = "DEFAULTED"
        loan.rating = "D"
        loan.default_date = ev.sim_date
        loan.recovery_due = p["recovery_due"]
        loan.history.append({"date": ev.sim_date, "note": f"DEFAULT: {p['reason']}; recovery {loan.recovery:.0%} expected {p['recovery_due']}", "rating": loan.history[0].get("rating", loan.rating) if loan.history else loan.rating})
        lines = []
        wo = D(p["accrued_written_off"])
        if wo:
            lines += [dr("5350", wo, loan.id, "accrued interest written off"), cr("1225", wo, loan.id, "accrued interest written off")]
            loan.accrued_interest -= wo
        target = float(loan.recovery)
        delta = money(loan.par * D(repr(target - loan.price)))
        loan.price = target
        if delta < 0:
            lines += [dr("5350", -delta, loan.id, "credit loss to recovery"), cr("1175", -delta, loan.id, "mark to recovery")]
        elif delta > 0:
            lines += [dr("1175", delta, loan.id, "mark to recovery"), cr("4340", delta, loan.id, "mark")]
        w.post(pf.id, f"Private credit default {loan.id}: marked to {target:.0%} recovery", lines, ev, {"loan_id": loan.id, "kind": "PRIVATE_CREDIT_DEFAULT"})

    def _close_out(self, pf: Portfolio, loan: PrivateLoan, par_out: Decimal, cash: Decimal, ev: Event, ccy: str, memo: str, kind: str, gain_account: str = "4340") -> None:
        """Remove `par_out` of par at the loan's carrying value against `cash`; the difference is realised."""
        w = self.w
        share = (par_out / loan.par) if loan.par else D(1)
        va_out = money(loan.par * D(repr(loan.price - 1)) * share)          # valuation adjustment attached to the par leaving
        ca = pf.cash_account(ccy)
        ca.balance += cash
        ca.base_value += cash
        w.record_cash_movement(pf, ccy, cash, kind, f"{loan.id}: {memo}", ev)
        lines = [dr(f"1010:{ccy}", cash, loan.id, "cash"), cr("1170", par_out, loan.id, "par removed")]
        if va_out < 0:
            lines.append(dr("1175", -va_out, loan.id, "valuation adjustment released"))
        elif va_out > 0:
            lines.append(cr("1175", va_out, loan.id, "valuation adjustment released"))
        realised = cash - (par_out + va_out)
        if realised > 0:
            lines.append(cr(gain_account, realised, loan.id, "realised gain"))
        elif realised < 0:
            lines.append(dr("5350" if gain_account == "5350" else gain_account, -realised, loan.id, "realised loss"))
        w.post(pf.id, f"Private credit {loan.id}: {memo}", lines, ev, {"loan_id": loan.id, "kind": kind})
        loan.par -= par_out

    def _h_recovered(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        cash = D(p["cash"])
        self._close_out(pf, loan, loan.par, cash, ev, p["currency"], f"workout recovery {float(p['recovery']):.0%} of par", "PRIVATE_CREDIT_RECOVERY", gain_account="5350")
        loan.status = "RECOVERED"
        loan.history.append({"date": ev.sim_date, "note": f"recovered {cash:,.0f}", "rating": loan.history[0].get("rating", loan.rating) if loan.history else loan.rating})

    def _h_sold(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        amt, proceeds = D(p["amount"]), D(p["proceeds"])
        # the buyer takes the accrued interest on the par sold
        accrued_share = money(loan.accrued_interest * (amt / loan.par)) if loan.par else ZERO
        self._close_out(pf, loan, amt, proceeds, ev, p["currency"], f"sold {amt:,.0f} par at {float(p['bid']) * 100:.2f}", "PRIVATE_CREDIT_SALE")
        if accrued_share:
            ccy = p["currency"]
            ca = pf.cash_account(ccy)
            ca.balance += accrued_share
            ca.base_value += accrued_share
            loan.accrued_interest -= accrued_share
            w.record_cash_movement(pf, ccy, accrued_share, "PRIVATE_CREDIT_INTEREST", f"{loan.id}: accrued interest on the par sold", ev)
            w.post(pf.id, f"Private credit {loan.id}: accrued interest on sale", [dr(f"1010:{ccy}", accrued_share, loan.id, "accrued received"), cr("1225", accrued_share, loan.id, "accrued settled")], ev,
                   {"loan_id": loan.id, "kind": "PRIVATE_CREDIT_INTEREST"})
        loan.history.append({"date": ev.sim_date, "note": f"sold {amt:,.0f} at {float(p['bid']) * 100:.2f}", "rating": loan.history[0].get("rating", loan.rating) if loan.history else loan.rating})
        if loan.par <= 0:
            loan.status = "SOLD"

    def _h_matured(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.private_loans[p["loan_id"]]
        ccy = p["currency"]
        accrued = D(p["accrued"])
        if accrued:
            ca = pf.cash_account(ccy)
            ca.balance += accrued
            ca.base_value += accrued
            loan.accrued_interest -= accrued
            loan.interest_paid += accrued
            w.record_cash_movement(pf, ccy, accrued, "PRIVATE_CREDIT_INTEREST", f"{loan.id}: final coupon", ev)
            w.post(pf.id, f"Private credit final coupon {loan.id}: {accrued:,.2f}", [dr(f"1010:{ccy}", accrued, loan.id, "final coupon"), cr("1225", accrued, loan.id, "accrued settled")], ev,
                   {"loan_id": loan.id, "kind": "PRIVATE_CREDIT_INTEREST"})
        self._close_out(pf, loan, loan.par, D(p["par"]), ev, ccy, "repaid at par at maturity", "PRIVATE_CREDIT_REPAYMENT")
        loan.status = "MATURED"
        loan.history.append({"date": ev.sim_date, "note": "repaid at maturity", "rating": loan.history[0].get("rating", loan.rating) if loan.history else loan.rating})
