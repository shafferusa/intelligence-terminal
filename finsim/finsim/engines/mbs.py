"""Agency mortgage-backed securities: TBAs and the pools they deliver into.

The market
  * Four programs: Fannie Mae 30-year and Freddie Mac 30-year (both UMBS), Ginnie Mae II 30-year, Fannie Mae 15-year,
    each at a ladder of coupons. A TBA ("to be announced") is a forward on a generic pool of that program and coupon
    delivering on the SIFMA settlement day of its month (class A on the 12th for 30-year UMBS, class B on the 16th for
    15-year, class C on the 19th for Ginnie). Two settlement months are listed at a time, so the front/back spread is the
    dollar roll.
  * A TBA id names the program, coupon and delivery month: FNCL-5.5-2026-10. Buying it is a forward purchase: the trade
    settles, and cash moves, on the delivery day. On that day the same security becomes the delivered pool: from then on
    it accrues and pays a monthly coupon and returns principal every month (scheduled amortisation plus prepayments), so
    its face shrinks with the pool factor. Older delivered pools stay tradeable as specified pools until they are a year
    past delivery with nobody holding them.

The model
  * Prepayments: a CPR (annual conditional prepayment rate) from a refinancing S-curve on the incentive WAC − current
    mortgage rate, plus housing turnover with a seasoning ramp and burnout with age. The current mortgage rate is the
    10-year Treasury plus the primary-secondary spread, so it moves with the curve every session, in simulated saves and
    in saves pinned to the real curve alike.
  * Price: the projected monthly cash flows of a level-pay pool (interest at the net coupon, scheduled principal at the
    WAC, prepayments at the CPR) discounted on the Treasury curve plus an option-adjusted spread, per 100 of current
    face, as of the delivery day (a TBA is a forward). Effective duration and convexity come from ±50bp parallel shifts
    of the curve, which move the mortgage rate and therefore the prepayment speed: premium coupons show the negative
    convexity a mortgage desk lives with.

Determinism: the pool factors are a pure function of the curve history, recomputed identically on generation and on
replay; the paydown that hits a book is an event.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..calendar import BusinessCalendar
from ..domain.models import Security, YieldCurve
from ..money import D, money, price as qprice, ZERO
from ..domain.events import E
from .pricing import interp_rate

PRIMARY_SPREAD = 0.0170        # the 30-year mortgage rate over the 10-year Treasury: the primary-secondary spread plus the note-to-MBS basis
MBS_CLASSES = ("MBS_TBA",)
MARKET = "US_MBS"


@dataclass(frozen=True)
class ProgramSpec:
    code: str
    name: str
    agency: str
    term_months: int
    settle_class: str          # SIFMA class: A (30-year UMBS), B (15-year), C (Ginnie)
    settle_day: int            # calendar day of the month (rolled forward to a business day)
    coupons: Tuple[float, ...]
    servicing: float           # WAC − net coupon (servicing plus guarantee fee)
    turnover: float            # housing-turnover CPR once seasoned
    refi_mult: float           # how eagerly the borrowers refinance (Ginnie's FHA/VA borrowers a little more)
    oas_bps: float             # option-adjusted spread over Treasuries
    adv: int                   # daily volume, face
    spread_bps: float          # dealing spread


PROGRAMS: List[ProgramSpec] = [
    ProgramSpec("FNCL", "Fannie Mae 30-year (UMBS)", "Fannie Mae", 360, "A", 12, (4.0, 4.5, 5.0, 5.5, 6.0, 6.5), 0.0055, 0.055, 1.00, 35.0, 2_000_000_000, 4.0),
    ProgramSpec("FGLMC", "Freddie Mac 30-year (UMBS)", "Freddie Mac", 360, "A", 12, (4.0, 4.5, 5.0, 5.5, 6.0, 6.5), 0.0055, 0.055, 1.00, 36.0, 1_200_000_000, 4.5),
    ProgramSpec("G2SF", "Ginnie Mae II 30-year", "Ginnie Mae", 360, "C", 19, (4.0, 4.5, 5.0, 5.5, 6.0, 6.5), 0.0050, 0.065, 1.15, 20.0, 800_000_000, 5.0),
    ProgramSpec("FNCI", "Fannie Mae 15-year (UMBS)", "Fannie Mae", 180, "B", 16, (4.0, 4.5, 5.0, 5.5), 0.0045, 0.060, 0.90, 25.0, 500_000_000, 5.0),
]
PROGRAM_BY_CODE = {p.code: p for p in PROGRAMS}


def tba_id(code: str, coupon: float, y: int, m: int) -> str:
    return f"{code}-{coupon:.1f}-{y}-{m:02d}"


def parse_id(sid: str) -> Optional[Tuple[str, float, int, int]]:
    parts = sid.split("-")
    if len(parts) != 4 or parts[0] not in PROGRAM_BY_CODE:
        return None
    try:
        return parts[0], float(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None


def settlement_day(cal: BusinessCalendar, spec: ProgramSpec, y: int, m: int) -> date:
    d = date(y, m, spec.settle_day)
    return d if cal.is_business_day(d) else cal.next_business_day(d)


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, 28))


def listed_months(cal: BusinessCalendar, spec: ProgramSpec, d: date, n: int = 2) -> List[Tuple[int, int]]:
    """The delivery months on offer: the current month while its settlement day is still ahead, then the next."""
    out = []
    y, m = d.year, d.month
    while len(out) < n:
        if settlement_day(cal, spec, y, m) > d:
            out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def make_tba(cal: BusinessCalendar, spec: ProgramSpec, coupon: float, y: int, m: int, seq: int, listed: str) -> Security:
    settle = settlement_day(cal, spec, y, m)
    mat = _add_months(settle, spec.term_months)
    return Security(
        id=tba_id(spec.code, coupon, y, m), name=f"{spec.name} {coupon:.1f}% TBA {date(y, m, 1).strftime('%b %Y')}", asset_class="MBS_TBA", market=MARKET,
        currency="USD", country="US", sector="Agency MBS", isin=f"US{seq:09d}M", cusip=f"{seq * 7919:08d}M"[-9:],
        coupon=coupon / 100, maturity=mat.isoformat(), issue_date=settle.isoformat(), freq=12, rating="AAA", issuer=spec.agency,
        spread_bps_credit=spec.oas_bps, adv=spec.adv, spread_bps=spec.spread_bps, liquidity_tier="LARGE", lot_size=1000,
        beta=0.0, sigma_annual=0.0, underlying=spec.code, underlying_class="MBS", contract_month=f"{y}-{m:02d}", unit="face", listed=listed,
    )


# ----------------------------------------------------------------------------- the prepayment and pricing model

def mortgage_rate(curve: YieldCurve, shift: float = 0.0) -> float:
    return interp_rate(curve, 10.0) + shift + PRIMARY_SPREAD


def cpr(spec: ProgramSpec, wac: float, mrate: float, age_months: int) -> float:
    """Annual prepayment rate: turnover with a seasoning ramp, refinancing on an S-curve of the rate incentive, burnout with age."""
    incentive = wac - mrate
    ramp = 0.5 + 0.5 * min(1.0, age_months / 30.0)          # a new pool turns over at half speed and seasons over 30 months
    turnover = spec.turnover * ramp
    refi = 0.50 * spec.refi_mult / (1.0 + math.exp(-(incentive - 0.0125) / 0.0060))
    burnout = max(0.35, 1.0 - age_months / 240.0)
    return min(0.85, turnover + refi * burnout)


def cash_flows(coupon: float, wac: float, months: int, cpr_annual: float) -> List[Tuple[int, float, float]]:
    """(month, interest, principal) per 100 of current face for a level-pay pool at a constant CPR."""
    smm = 1.0 - (1.0 - cpr_annual) ** (1.0 / 12.0)
    r = wac / 12.0
    f = 100.0
    out = []
    for m in range(1, months + 1):
        rem = months - m + 1
        sched = f * (r / (1.0 - (1.0 + r) ** (-rem)) - r) if r > 0 else f / rem
        prepay = max(0.0, f - sched) * smm
        interest = f * coupon / 12.0
        principal = min(f, sched + prepay)
        out.append((m, interest, principal))
        f -= principal
        if f < 1e-9:
            break
    return out


@dataclass
class PoolState:
    factor: float = 1.0
    age_months: int = 0        # months since delivery
    last_cpr: float = 0.0


class MBSModel:
    """Pool factors and pricing for every TBA / pool in the universe. Owned by the market engine."""

    def __init__(self, cal: BusinessCalendar):
        self.cal = cal
        self.state: Dict[str, PoolState] = {}
        self.paydowns: Dict[str, float] = {}      # today's principal paid as a fraction of the face outstanding, by security
        self.mortgage_rate_history: List[Tuple[str, float]] = []

    # ---- helpers
    @staticmethod
    def spec_of(sec: Security) -> ProgramSpec:
        return PROGRAM_BY_CODE[sec.underlying]

    @staticmethod
    def wac(sec: Security) -> float:
        return float(sec.coupon) + PROGRAM_BY_CODE[sec.underlying].servicing

    def pool(self, sec: Security) -> PoolState:
        return self.state.setdefault(sec.id, PoolState())

    @staticmethod
    def delivered(sec: Security, d: date) -> bool:
        return date.fromisoformat(sec.issue_date) <= d

    def months_left(self, sec: Security, d: date) -> int:
        spec = self.spec_of(sec)
        return max(1, spec.term_months - self.pool(sec).age_months)

    # ---- the daily step: factors move on the pool's payment day
    def step(self, d: date, curve: YieldCurve, securities: Dict[str, Security]) -> Dict[str, float]:
        """Advance every delivered pool whose monthly payment falls today; returns {id: fraction of face paid down}."""
        from .pricing import BondPricer
        self.paydowns = {}
        mrate = mortgage_rate(curve)
        self.mortgage_rate_history.append((d.isoformat(), mrate))
        for sec in securities.values():
            if sec.asset_class != "MBS_TBA" or not self.delivered(sec, d) or d.isoformat() == sec.issue_date:
                continue
            st = self.pool(sec)
            if d.isoformat() not in (c.isoformat() for c in BondPricer.coupon_dates(sec)):
                continue
            spec = self.spec_of(sec)
            wac = self.wac(sec)
            speed = cpr(spec, wac, mrate, st.age_months)
            rem = max(1, spec.term_months - st.age_months)
            r = wac / 12.0
            sched = (r / (1.0 - (1.0 + r) ** (-rem)) - r)
            smm = 1.0 - (1.0 - speed) ** (1.0 / 12.0)
            frac = min(1.0, sched + (1.0 - sched) * smm)
            st.factor *= (1.0 - frac)
            st.age_months += 1
            st.last_cpr = speed
            if frac > 0:
                self.paydowns[sec.id] = frac
        return self.paydowns

    # ---- pricing
    def _flows(self, sec: Security, curve: YieldCurve, shift: float, as_of_delivery: bool = True) -> Tuple[List[Tuple[int, float, float]], float]:
        spec = self.spec_of(sec)
        st = self.pool(sec)
        wac = self.wac(sec)
        speed = cpr(spec, wac, mortgage_rate(curve, shift), st.age_months)
        return cash_flows(float(sec.coupon), wac, max(1, spec.term_months - st.age_months), speed), speed

    def dirty_price(self, sec: Security, curve: YieldCurve, d: date, shift: float = 0.0, oas: Optional[float] = None) -> float:
        """Per 100 of current face. A delivered pool: the flows discounted to today. A TBA: the same flows, which start a month
        after delivery, discounted to today and financed to the delivery day at the short rate, so a later delivery month is
        cheaper by the coupon carry less financing: the dollar-roll drop."""
        flows, _ = self._flows(sec, curve, shift)
        spread = (sec.spread_bps_credit if oas is None else oas) / 1e4
        settle = date.fromisoformat(sec.issue_date)
        t_settle = max(0.0, (settle - d).days / 365.25)
        total = 0.0
        for m, interest, principal in flows:
            t = t_settle + m / 12.0
            z = interp_rate(curve, t) + shift + spread
            total += (interest + principal) / (1.0 + z / 12.0) ** (12.0 * t)
        if t_settle > 0:
            total *= 1.0 + (interp_rate(curve, 0.25) + shift + 0.0015) * t_settle
        return total

    def clean_price(self, sec: Security, curve: YieldCurve, d: date, shift: float = 0.0) -> Decimal:
        from .pricing import BondPricer
        dirty = self.dirty_price(sec, curve, d, shift)
        accrued = float(BondPricer.accrued_per_100(sec, d)) if self.delivered(sec, d) else 0.0
        return qprice(D(repr(max(0.01, dirty - accrued))))

    def metrics(self, sec: Security, curve: YieldCurve, d: date, clean: Optional[float] = None) -> Dict[str, float]:
        """Yield, effective duration/convexity (±50bp, the mortgage rate moving with the curve), WAL, CPR, factor, OAS."""
        from .pricing import BondPricer
        spec = self.spec_of(sec)
        st = self.pool(sec)
        flows, speed = self._flows(sec, curve, 0.0)
        p0 = self.dirty_price(sec, curve, d)
        accrued = float(BondPricer.accrued_per_100(sec, d)) if self.delivered(sec, d) else 0.0
        dirty = (clean + accrued) if clean is not None else p0
        bump = 0.005
        up, dn = self.dirty_price(sec, curve, d, bump), self.dirty_price(sec, curve, d, -bump)
        eff_dur = (dn - up) / (2.0 * p0 * bump)
        convexity = (up + dn - 2.0 * p0) / (p0 * bump * bump)
        # yield: the monthly IRR of the projected flows against the dirty price, bond-equivalent
        y = 0.05
        for _ in range(60):
            pv = sum((i + p) / (1.0 + y / 12.0) ** m for m, i, p in flows)
            dpv = sum(-m / 12.0 * (i + p) / (1.0 + y / 12.0) ** (m + 1) for m, i, p in flows)
            if abs(dpv) < 1e-12:
                break
            step = (pv - dirty) / dpv
            y -= step
            if abs(step) < 1e-10:
                break
        bey = ((1.0 + y / 12.0) ** 6 - 1.0) * 2.0
        tot_p = sum(p for _, _, p in flows) or 1.0
        wal = sum(m / 12.0 * p for m, _, p in flows) / tot_p
        bench = interp_rate(curve, max(0.5, wal))
        return {"price_model": round(p0 - accrued, 4), "ytm": bey, "effective_duration": eff_dur, "modified_duration": eff_dur, "convexity": convexity,
                "dv01_per_100": eff_dur * p0 / 1e4, "wal": wal, "cpr": speed, "wac": self.wac(sec), "wam": max(1, spec.term_months - st.age_months),
                "factor": st.factor, "age_months": st.age_months, "oas_bps": sec.spread_bps_credit, "mortgage_rate": mortgage_rate(curve),
                "incentive_bps": (self.wac(sec) - mortgage_rate(curve)) * 1e4, "benchmark_yield": bench, "spread_to_curve_bps": (bey - bench) * 1e4,
                "accrued": accrued, "dirty_price": dirty, "years_to_maturity": max(1, spec.term_months - st.age_months) / 12.0,
                "delivered": self.delivered(sec, d), "settlement": sec.issue_date, "credit_spread_bps": sec.spread_bps_credit, "default_probability_1y": 0.0,
                "macaulay_duration": eff_dur}

    def projected_flows(self, sec: Security, curve: YieldCurve, d: date, n: int = 12) -> List[Tuple[str, float]]:
        flows, _ = self._flows(sec, curve, 0.0)
        start = max(d, date.fromisoformat(sec.issue_date))
        return [(_add_months(start, m).isoformat(), round(i + p, 6)) for m, i, p in flows[:n]]

    def ingest(self, payload: Dict) -> None:
        for sid, s in (payload or {}).items():
            self.state[sid] = PoolState(float(s["factor"]), int(s["age"]), float(s.get("cpr", 0.0)))

    def payload(self) -> Dict:
        return {sid: {"factor": round(s.factor, 8), "age": s.age_months, "cpr": round(s.last_cpr, 6)} for sid, s in self.state.items() if s.age_months}


class MBSDesk:
    """The book side: monthly principal paydowns on delivered pools, as events."""

    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        self.w.on(E.MBS_PAID_DOWN, lambda world, ev: self._h_paid_down(ev))

    def process_day(self, cause) -> None:
        w = self.w
        pays = w.market.mbs.paydowns
        if not pays:
            return
        for pf in w.portfolios.values():
            for pos in list(pf.positions.values()):
                frac = pays.get(pos.security_id)
                if not frac or pos.quantity == 0:
                    continue
                fD = D(repr(frac))
                face_paid = (pos.quantity * fD).quantize(Decimal("0.01"))
                if face_paid == 0:
                    continue
                settled_paid = (pos.settled_quantity * fD).quantize(Decimal("0.01"))
                cost_removed = money(pos.cost_basis * fD)
                principal = money(face_paid)                       # paid at par
                w.emit(E.MBS_PAID_DOWN, {"portfolio_id": pf.id, "security_id": pos.security_id, "face_paid": face_paid, "settled_paid": settled_paid,
                                         "principal": principal, "cost_removed": cost_removed, "realized": principal - cost_removed,
                                         "factor": w.market.mbs.pool(w.securities[pos.security_id]).factor, "fraction": frac, "currency": "USD"},
                       cause_id=cause.id, portfolio_id=pf.id)

    def _h_paid_down(self, ev) -> None:
        from .ledger import dr, cr, cost_account
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        sec = w.securities[p["security_id"]]
        pos = pf.position(sec.id)
        face_paid, settled_paid = D(p["face_paid"]), D(p["settled_paid"])
        principal, cost_removed, realized = D(p["principal"]), D(p["cost_removed"]), D(p["realized"])
        fD = D(repr(float(p["fraction"])))
        pos.quantity -= face_paid
        pos.settled_quantity -= settled_paid
        pos.cost_basis -= cost_removed
        pos.realized_pnl += realized
        for lot in pos.lots:
            dq = (lot.quantity * fD).quantize(Decimal("0.01"))
            lot.quantity -= dq
            lot.cost_total -= money(lot.cost_total * fD)
        pos.lots = [l for l in pos.lots if l.quantity != 0]
        short = face_paid < 0
        ca = pf.cash_account(p["currency"])
        ca.balance += principal
        w.record_cash_movement(pf, p["currency"], principal, "PAYDOWN", f"Principal paydown {sec.id} (factor {float(p['factor']):.6f})", ev)
        w.record_custody_movement(pf, sec.id, -settled_paid, "PAYDOWN", f"principal returned, factor {float(p['factor']):.6f}", ev)
        lines = [dr(f"1010:{p['currency']}", principal, sec.id, "principal received" if not short else "principal paid on the short"),
                 cr(cost_account(sec, short), cost_removed, sec.id, "cost basis paid down" if not short else "short proceeds paid down"),
                 cr("4000", realized, sec.id, "realized on paydown at par")]
        w.post(pf.id, f"Paydown {sec.id}: {abs(face_paid):,.2f} face at par ({float(p['fraction']) * 100:.3f}% of the pool), realized {realized:+,.2f}", lines, ev,
               {"security_id": sec.id, "kind": "MBS_PAYDOWN"})
