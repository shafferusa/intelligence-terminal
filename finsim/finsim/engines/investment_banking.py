"""Investment banking: winning mandates, executing deals, earning fees and carrying underwriting risk.

The bank pitches for mandates (a beauty parade against rival banks): sell-side and buy-side M&A advisory, IPOs,
block trades (bought deals), bond issues and leveraged-loan underwriting. A pitch names a fee and, where it matters,
what the bank promises — the valuation it will get a seller, the range it will list a company at, the discount it
will buy a block at, the concession it will price a bond with. The client answers in three sessions; competitors
undercut on fee and over-promise on valuation, and a bank's reputation (0-100) and league-table standing tilt the
choice.

A won mandate is an engagement with a timeline: a sale process brings bids after twenty-five sessions and the
adviser recommends accepting or holding out; a buy-side mandate lands or loses on the premium offered; an IPO
builds a book for fifteen sessions (demand depends on the price against fair value, the market and the bank's
standing), the bank prices it and the first day of trading follows; a block is bought from the seller at the
discount and resold into the market over the next sessions at the simulation's own prices — underwriting risk is
real; a bond book fills or is pulled on the concession; a leveraged loan syndicates unless the market seizes up and
the commitment hangs on the bank's balance sheet at a discount. Fees are cash on closing (advisory success fees,
gross spreads, underwriting fees); losses hit the ledger; every deal moves the reputation and the league table,
and announcements go on the wire (invented companies, invented deals — this desk exists only in simulated saves).

Accounts: 4390 fees, 4395 underwriting gains, 5370 underwriting losses and broken-deal costs.
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
from .private_equity import SECTORS, REGIME_MULT

RIVALS = ["Goldman Sachs", "Morgan Stanley", "J.P. Morgan", "Bank of America", "Citi", "Barclays", "UBS", "Jefferies", "Evercore", "Lazard"]
FIRST = ["Northwind", "Halcyon", "Meridian", "Tessera", "Brightline", "Copperfield", "Ardent", "Silverpine", "Kestrel", "Oakmont", "Lumen", "Harborview",
         "Atlas", "Verity", "Summitcrest", "Crescent", "Pinnacle", "Redwood", "Beacon", "Foundry", "Cobalt", "Ironbridge", "Larkspur", "Granite Peak"]
SUFFIX = {"Software": "Systems", "Health Care": "Health", "Business Services": "Partners", "Industrials": "Industries", "Consumer": "Brands",
          "Communication & Media": "Media", "Energy & Infrastructure": "Energy"}
KINDS = {
    "SELL_SIDE": {"label": "Sell-side M&A", "fee": (0.010, 0.020), "sessions": 25, "promise": "valuation (EV/EBITDA the bank says it can get)",
                  "note": "run a sale process for the owner; the success fee is a share of the enterprise value; promise too much and the bids disappoint"},
    "BUY_SIDE":  {"label": "Buy-side M&A", "fee": (0.006, 0.012), "sessions": 20, "promise": "premium the acquirer should offer (%)",
                  "note": "advise an acquirer: the offer premium decides whether the target's board accepts, and whether the client overpays"},
    "IPO":       {"label": "IPO", "fee": (0.035, 0.070), "sessions": 15, "promise": "valuation for the range (EV/EBITDA)",
                  "note": "lead-left on a listing: build the book, price the deal, own the first day — a broken IPO costs reputation for a year"},
    "FOLLOW_ON": {"label": "Block trade (bought deal)", "fee": (0.0, 0.0), "sessions": 0, "promise": "discount to the last close the bank buys at (%)",
                  "note": "buy a holder's block at a discount and resell it into the market: the discount is the fee, the resale is the risk"},
    "BOND":      {"label": "Bond issue", "fee": (0.004, 0.015), "sessions": 10, "promise": "new-issue concession over the issuer's curve (bp)",
                  "note": "lead a corporate bond: a fair concession fills the book, a tight one gets the deal pulled"},
    "LEV_LOAN":  {"label": "Leveraged loan underwriting", "fee": (0.020, 0.030), "sessions": 15, "promise": "spread the bank commits at (bp over SOFR)",
                  "note": "commit to a sponsor's financing and syndicate it; if the market seizes up the loan hangs on the balance sheet at a discount"},
}
PITCH_DAYS = 3
DECISION_DAYS = 5
REGIME_ACTIVITY = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.15, "RATE_HIKING": 0.9, "RECESSION": 0.6, "LIQUIDITY_STRESS": 0.4}
REP_START = 50.0


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@dataclass
class Engagement:
    id: str
    portfolio_id: str
    mandate_id: str
    kind: str
    client: str
    counterparty: str
    sector: str
    size: float                          # deal value / proceeds / block value / issue size
    fee_pct: float
    promise: float
    won_date: str
    status: str = "ACTIVE"               # ACTIVE | DECISION | CLOSED | LOST | PULLED
    phase: str = ""
    next_step: str = ""                  # date of the next scheduled step
    pending: Optional[Dict] = None       # a decision the banker owes: {type, options, deadline, default, detail}
    detail: Dict = field(default_factory=dict)
    fee_earned: Decimal = ZERO
    pnl: Decimal = ZERO                  # underwriting gains/losses
    outcome: str = ""
    history: List[Dict] = field(default_factory=list)


class InvestmentBankingEngine:
    def __init__(self, world):
        self.w = world
        self.pitches: Dict[str, Dict[str, Dict]] = {}     # mandate id -> portfolio id -> pitch
        self.taken: set = set()

    def register(self) -> None:
        w = self.w
        w.on(E.IB_PITCHED, lambda world, ev: self._h_pitched(ev))
        w.on(E.IB_MANDATE_RESOLVED, lambda world, ev: self._h_resolved(ev))
        w.on(E.IB_UPDATE, lambda world, ev: self._h_update(ev))
        w.on(E.IB_DECIDED, lambda world, ev: self._h_decided(ev))
        w.on(E.IB_CLOSED, lambda world, ev: self._h_closed(ev))

    # ------------------------------------------------------------------ market
    def regime(self) -> str:
        return self.w.market.state.regime if self.w.market.state else "NORMAL_GROWTH"

    def policy_rate(self) -> float:
        return float(self.w.market.curves[-1].policy_rate) if self.w.market.curves else 0.04

    def hy_scale(self) -> float:
        curves = self.w.market.curves
        return float(curves[-1].hy_spread_bps) / 360.0 if curves else 1.0

    def public_multiple(self, sector: str) -> float:
        return round(SECTORS[sector]["mult"] * REGIME_MULT.get(self.regime(), 1.0), 2)

    def stats(self, pf: Portfolio) -> Dict:
        st = pf.ib_stats
        st.setdefault("reputation", REP_START); st.setdefault("fees_ytd", ZERO); st.setdefault("volume_ytd", 0.0); st.setdefault("deals", 0); st.setdefault("broken", 0)
        st.setdefault("year", self.w.current_date.year)
        return st

    def market(self) -> Dict:
        r = self.regime()
        return {"regime": r, "activity": REGIME_ACTIVITY.get(r, 1.0), "ipo_window": r in ("NORMAL_GROWTH", "RATE_CUTTING", "RATE_HIKING"), "policy_rate": self.policy_rate(),
                "hy_scale": round(self.hy_scale(), 3), "fee_norms": {k: v["fee"] for k, v in KINDS.items()}, "kinds": {k: {"label": v["label"], "promise": v["promise"], "note": v["note"]} for k, v in KINDS.items()}}

    # ------------------------------------------------------------------ mandates
    def _company(self, rng: random.Random, sector: str) -> str:
        return f"{rng.choice(FIRST)} {SUFFIX.get(sector, 'Group')}"

    def pipeline(self, d: Optional[date] = None, portfolio_id: Optional[str] = None) -> List[Dict]:
        """This month's mandates up for pitch. Deterministic from the seed; invented companies throughout."""
        w = self.w
        d = d or w.current_date
        rng = random.Random(f"{w.seed}|ibank|{d.year}-{d.month:02d}")
        act = REGIME_ACTIVITY.get(self.regime(), 1.0)
        n = max(3, int(round((5 + rng.randint(0, 3)) * act)))
        close = w.calendar.roll(date(d.year + (d.month // 12), d.month % 12 + 1, 1))
        listed = [s for s in w.securities.values() if s.asset_class == "EQUITY" and w.market.history.get(s.id) and s.liquidity_tier in ("LARGE", "MID")]
        out = []
        order = list(KINDS)
        rng.shuffle(order)                                                   # the first mandates cover every kind; the rest are drawn
        for i in range(n):
            kind = order[i] if i < len(order) else rng.choices(list(KINDS), weights=[4, 3, 3, 3, 3, 2])[0]
            sector = rng.choice(list(SECTORS))
            sp = SECTORS[sector]
            mid = f"IBM-{d.year % 100:02d}{d.month:02d}-{i + 1:02d}"
            ebitda = rng.choice([25, 40, 60, 90, 150, 250, 400, 700]) * 1_000_000 * rng.uniform(0.85, 1.15)
            pub = self.public_multiple(sector)
            fee_lo, fee_hi = KINDS[kind]["fee"]
            row = {"id": mid, "kind": kind, "label": KINDS[kind]["label"], "sector": sector, "opened": date(d.year, d.month, 1).isoformat(), "closes": close.isoformat(),
                   "rivals": rng.sample(RIVALS, rng.randint(2, 4)), "fee_norm": (fee_lo, fee_hi), "promise_label": KINDS[kind]["promise"],
                   "status": "TAKEN" if mid in self.taken else ("OPEN" if d < close else "CLOSED")}
            if kind == "SELL_SIDE":
                row.update({"client": self._company(rng, sector), "counterparty": "buyers to be found", "ebitda": round(ebitda, -5), "public_multiple": pub,
                            "size": round(pub * ebitda, -6), "ask": f"the owner wants at least {pub * 0.95:.1f}x", "_fair": pub * rng.uniform(0.92, 1.08)})
            elif kind == "BUY_SIDE":
                tgt = self._company(rng, sector)
                row.update({"client": self._company(rng, sector), "counterparty": tgt, "ebitda": round(ebitda, -5), "public_multiple": pub, "size": round(pub * ebitda, -6),
                            "ask": f"{tgt} trades at {pub:.1f}x; the board will want a premium", "_fair_premium": rng.uniform(0.18, 0.35)})
            elif kind == "IPO":
                eb = max(ebitda, 40_000_000)
                row.update({"client": self._company(rng, sector), "counterparty": "public investors", "ebitda": round(eb, -5), "public_multiple": pub, "size": round(pub * eb * 0.2, -6),
                            "ask": f"listing 20% of the company; comps at {pub:.1f}x", "_fair": pub * rng.uniform(0.9, 1.05), "_quality": rng.uniform(0.85, 1.15)})
            elif kind == "FOLLOW_ON":
                sec = rng.choice(listed) if listed else None
                if sec is None:
                    continue
                px = float(w.market.last_bar(sec.id).close)
                shares = int(max(200_000, min(sec.adv * rng.uniform(1.5, 4.0), 50_000_000, 400_000_000 / max(1.0, px))))   # blocks a mid-sized bank can carry
                row.update({"client": f"a holder of {sec.name}", "counterparty": sec.id, "security_id": sec.id, "shares": shares, "last": px, "size": round(shares * px, -5),
                            "ask": f"{shares:,} shares ({shares / max(1, sec.adv):.1f} days' volume) to be sold overnight", "sector": sec.sector})
            elif kind == "BOND":
                issuers = [s for s in w.securities.values() if s.is_bond and s.issuer and not s.id.startswith(("UST", "STRIP", "SOV", "MUNI", "AGY")) and w.market.history.get(s.id)]
                ref = rng.choice(issuers) if issuers else None
                size = rng.choice([300, 500, 750, 1000, 1500, 2000, 3000]) * 1_000_000
                row.update({"client": (ref.issuer if ref else self._company(rng, sector)), "counterparty": "bond investors", "size": size, "reference_bond": ref.id if ref else None,
                            "sector": (ref.sector if ref and getattr(ref, "sector", None) else sector),
                            "tenor_years": rng.choice([3, 5, 7, 10]), "rating": (ref.rating if ref else "BBB"), "ask": f"{size / 1e6:,.0f}m {rng.choice([3, 5, 7, 10])}-year senior notes",
                            "_fair_concession": rng.uniform(8, 25) * max(0.8, self.hy_scale())})
            else:
                size = rng.choice([200, 300, 500, 750, 1000, 1500, 2000]) * 1_000_000
                row.update({"client": f"{rng.choice(['KKR', 'Blackstone', 'Apollo', 'Carlyle', 'TPG', 'Thoma Bravo'])} (sponsor)", "counterparty": self._company(rng, sector), "size": size,
                            "ask": f"underwrite a {size / 1e6:,.0f}m term loan for a buyout", "_fair_spread": 375 * max(0.8, self.hy_scale()) * rng.uniform(0.9, 1.15)})
            if portfolio_id:
                row["my_pitch"] = self.pitches.get(mid, {}).get(portfolio_id)
            out.append(row)
        return out

    def mandate(self, mid: str, portfolio_id: Optional[str] = None) -> Optional[Dict]:
        return next((m for m in self.pipeline(None, portfolio_id) if m["id"] == mid), None)

    def _mandate_ok(self, pf: Portfolio) -> None:
        from ..world import CommandError
        if getattr(self.w, "market_source", "SIMULATED") == "REAL":
            raise CommandError("this save tracks the real market: investment banking mandates are invented and stay switched off here — play the Investment Banker career")
        job = self.w.careers.job_for(pf)
        if job and job.allowed_classes and "INVESTMENT_BANKING" not in job.allowed_classes:
            raise CommandError("investment banking is outside this job's mandate")

    # ------------------------------------------------------------------ commands
    def pitch(self, pf: Portfolio, mid: str, fee_pct: float, promise: float) -> Dict:
        from ..world import CommandError
        w = self.w
        self._mandate_ok(pf)
        m = self.mandate(mid, pf.id)
        if m is None:
            raise CommandError(f"unknown mandate {mid}: the pipeline refreshes monthly")
        if m["status"] != "OPEN":
            raise CommandError(f"{mid} is {m['status'].lower()}")
        fee_pct, promise = float(fee_pct), float(promise)
        lo, hi = m["fee_norm"]
        if m["kind"] != "FOLLOW_ON" and not (lo * 0.4 <= fee_pct <= hi * 1.6):
            raise CommandError(f"a fee between {lo * 0.4:.2%} and {hi * 1.6:.2%} is credible on this mandate")
        if m["kind"] == "FOLLOW_ON":
            if not (0.5 <= promise <= 12):
                raise CommandError("bid a discount to the last close between 0.5% and 12%")
            need = money(D(str(m["size"])) * D(str(1 - promise / 100)))
            reason = w.prime.affordable(pf, need, None)
            if reason:
                raise CommandError(f"the bank cannot carry a {need:,.0f} block: {reason}")
        elif m["kind"] in ("SELL_SIDE", "IPO"):
            if not (3 <= promise <= 40):
                raise CommandError("promise an EV/EBITDA multiple between 3x and 40x")
        elif m["kind"] == "BUY_SIDE":
            if not (0 <= promise <= 80):
                raise CommandError("the premium is a percentage between 0 and 80")
        elif m["kind"] == "BOND":
            if not (0 <= promise <= 100):
                raise CommandError("the concession is 0 to 100 basis points")
        else:
            if not (150 <= promise <= 900):
                raise CommandError("the loan spread is 150 to 900 basis points over SOFR")
        decides = w.calendar.add_business_days(w.current_date, PITCH_DAYS).isoformat()
        w.emit(E.IB_PITCHED, {"portfolio_id": pf.id, "mandate_id": mid, "fee_pct": fee_pct, "promise": promise, "decides": decides,
                              "mandate": {k: v for k, v in m.items() if not k.startswith("_") and k != "my_pitch"}, "hidden": {k: v for k, v in m.items() if k.startswith("_")}},
               portfolio_id=pf.id)
        return self.pitches[mid][pf.id]

    def decide(self, pf: Portfolio, eng_id: str, choice: str, value: Optional[float] = None) -> Engagement:
        from ..world import CommandError
        e = pf.ib_engagements.get(eng_id)
        if e is None or e.status != "DECISION" or not e.pending:
            raise CommandError("no decision is owed on that engagement")
        choice = str(choice).upper()
        opts = e.pending["options"]
        if choice not in opts:
            raise CommandError(f"choose one of {', '.join(opts)}")
        if e.pending["type"] == "PRICE":
            lo, hi = e.pending["range"]
            if value is None:
                raise CommandError("name the price")
            value = float(value)
            if not (lo * 0.85 <= value <= hi * 1.15):
                raise CommandError(f"price within 15% of the range {lo:.2f}-{hi:.2f}")
        self.w.emit(E.IB_DECIDED, {"portfolio_id": pf.id, "engagement_id": e.id, "choice": choice, "value": value}, portfolio_id=pf.id)
        return e

    # ------------------------------------------------------------------ daily process
    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        iso = w.current_date.isoformat()
        for mid, by_pf in list(self.pitches.items()):
            for pid, p in list(by_pf.items()):
                if iso >= p["decides"]:
                    self._resolve_pitch(w.portfolios[pid], mid, p, cause)
        for pf in w.portfolios.values():
            st = self.stats(pf)
            if st["year"] != w.current_date.year:                      # league tables reset with the calendar year
                st["year"], st["fees_ytd"], st["volume_ytd"] = w.current_date.year, ZERO, 0.0
            for e in list(pf.ib_engagements.values()):
                if e.status == "DECISION" and e.pending and iso >= e.pending["deadline"]:
                    w.emit(E.IB_DECIDED, {"portfolio_id": pf.id, "engagement_id": e.id, "choice": e.pending["default"], "value": e.pending.get("default_value"), "defaulted": True},
                           cause_id=cause.id, portfolio_id=pf.id)
                if e.status == "ACTIVE" and e.next_step and iso >= e.next_step:
                    self._step(pf, e, cause)

    def _resolve_pitch(self, pf: Portfolio, mid: str, p: Dict, cause: Event) -> None:
        w = self.w
        rng = random.Random(f"{w.seed}|ibpitch|{mid}|{pf.id}")
        m, hidden = p["mandate"], p["hidden"]
        st = self.stats(pf)
        lo, hi = m["fee_norm"]
        norm = (lo + hi) / 2 if hi else 0.0
        fee_edge = ((norm - p["fee_pct"]) / norm) if norm else 0.0
        promise_edge = 0.0
        if m["kind"] == "SELL_SIDE":
            promise_edge = (p["promise"] - m["public_multiple"]) / m["public_multiple"] * 1.5      # owners like a big number
        elif m["kind"] == "IPO":
            promise_edge = (p["promise"] - m["public_multiple"]) / m["public_multiple"] * 1.2
        elif m["kind"] == "FOLLOW_ON":
            promise_edge = (4.0 - p["promise"]) / 4.0                                                # the seller wants a small discount
        elif m["kind"] == "BOND":
            promise_edge = (hidden["_fair_concession"] - p["promise"]) / 20.0                       # the issuer wants a tight concession
        elif m["kind"] == "LEV_LOAN":
            promise_edge = (hidden["_fair_spread"] - p["promise"]) / 150.0
        elif m["kind"] == "BUY_SIDE":
            promise_edge = 0.0
        rep_edge = (st["reputation"] - REP_START) / 25.0
        x = 0.2 + 1.5 * fee_edge + 1.0 * promise_edge + 0.8 * rep_edge - 0.35 * len(m["rivals"])
        won = mid not in self.taken and rng.random() < _sig(x)
        rival = rng.choice(m["rivals"])
        payload = {"portfolio_id": pf.id, "mandate_id": mid, "won": won, "pitch": p, "currency": pf.base_currency,
                   "note": (f"mandate won against {', '.join(m['rivals'])}" if won else f"lost to {rival}: {rng.choice(['a lower fee', 'a bigger promised valuation', 'a longer relationship', 'a stronger league-table position'])}")}
        if won:
            payload["engagement_id"] = w.new_id("IBE")
            kind = m["kind"]
            sessions = KINDS[kind]["sessions"]
            payload["next_step"] = w.calendar.add_business_days(w.current_date, sessions).isoformat() if sessions else w.current_date.isoformat()
            payload["phase"] = {"SELL_SIDE": "running the process", "BUY_SIDE": "approaching the target", "IPO": "building the book", "FOLLOW_ON": "block bought at the close",
                                "BOND": "bookbuilding", "LEV_LOAN": "syndicating"}[kind]
            if kind == "FOLLOW_ON":
                px = float(w.market.last_bar(m["security_id"]).close)
                buy = px * (1 - p["promise"] / 100)
                payload["block"] = {"security_id": m["security_id"], "shares": m["shares"], "last": px, "price": round(buy, 4), "cost": money(D(str(buy * m["shares"])))}
                payload["next_step"] = w.calendar.add_business_days(w.current_date, 1).isoformat()
        w.emit(E.IB_MANDATE_RESOLVED, payload, cause_id=cause.id, portfolio_id=pf.id)

    def _step(self, pf: Portfolio, e: Engagement, cause: Event) -> None:
        """The scheduled step of an engagement: bids in, a book to price, a resale, a bond outcome, a loan syndicated."""
        w = self.w
        today = w.current_date
        rng = random.Random(f"{w.seed}|ibstep|{e.id}|{today.isoformat()}")
        regime = self.regime()
        st = self.stats(pf)
        d = e.detail
        if e.kind == "SELL_SIDE":
            fair = d["fair"]
            n_bids = max(0, int(round(rng.gauss(3, 1) * REGIME_ACTIVITY.get(regime, 1.0))))
            bids = sorted([round(fair * rng.uniform(0.85, 1.12) * (1.0 - 0.02 * d.get("rounds", 0)), 2) for _ in range(n_bids)], reverse=True)
            if not bids:
                self._close(pf, e, cause, "PULLED", f"no bids at an acceptable level: the process is pulled", fee=ZERO, pnl=ZERO, rep=-3.0)
                return
            best = bids[0]
            deadline = w.calendar.add_business_days(today, DECISION_DAYS).isoformat()
            w.emit(E.IB_UPDATE, {"portfolio_id": pf.id, "engagement_id": e.id, "phase": "bids received", "status": "DECISION",
                                 "pending": {"type": "BIDS", "options": ["ACCEPT", "HOLD"], "deadline": deadline, "default": "ACCEPT", "bids": bids, "best": best,
                                             "detail": f"best bid {best:.1f}x EBITDA ({best * d['ebitda']:,.0f} EV) against the {e.promise:.1f}x promised; HOLD reruns the process (a better bid one time in three, buyers walk one time in four)"},
                                 "note": f"{len(bids)} bid(s): {', '.join(f'{b:.1f}x' for b in bids)}"}, cause_id=cause.id, portfolio_id=pf.id)
        elif e.kind == "BUY_SIDE":
            prem = e.promise / 100.0
            fairp = d["fair_premium"]
            p_ok = _sig((prem - fairp) / 0.06 + 0.3) * REGIME_ACTIVITY.get(regime, 1.0)
            if rng.random() < p_ok:
                value = d["ebitda"] * d["public_multiple"] * (1 + prem)
                overpay = max(0.0, prem - fairp - 0.05)
                rep = 2.0 - overpay * 20
                fee = money(D(str(value)) * D(str(e.fee_pct)))
                self._close(pf, e, cause, "CLOSED", f"{e.client} agrees to acquire {e.counterparty} at a {prem:.0%} premium ({value:,.0f})", fee=fee, pnl=ZERO, rep=rep, value=value,
                            news=(f"{e.client} to acquire {e.counterparty} for {value / 1e9:,.2f} billion", f"The {e.sector.lower()} deal, at a {prem:.0%} premium to where the target traded, was advised by the bank; it closes in about ninety days."))
            else:
                self._close(pf, e, cause, "LOST", f"the target's board rejected the {prem:.0%} premium; {rng.choice(['a rival bidder', 'the board', 'the largest shareholder'])} walked away", fee=money(D(str(e.size)) * D("0.0005")), pnl=ZERO, rep=-1.0)
        elif e.kind == "IPO":
            fair = d["fair"]
            mid_mult = e.promise
            lo, hi = round(mid_mult * 0.92, 2), round(mid_mult * 1.08, 2)
            shares = d["shares_offered"]
            px_lo, px_hi = lo * d["ebitda"] / d["shares_total"], hi * d["ebitda"] / d["shares_total"]
            cover = max(0.2, (fair / mid_mult) ** 3 * REGIME_ACTIVITY.get(regime, 1.0) * (0.8 + 0.4 * (st["reputation"] / 100)) * d["quality"] * rng.uniform(0.8, 1.2))
            deadline = w.calendar.add_business_days(today, 2).isoformat()
            w.emit(E.IB_UPDATE, {"portfolio_id": pf.id, "engagement_id": e.id, "phase": "book covered, pricing", "status": "DECISION",
                                 "pending": {"type": "PRICE", "options": ["PRICE", "POSTPONE"], "deadline": deadline, "default": "PRICE", "default_value": round((px_lo + px_hi) / 2, 2),
                                             "range": [round(px_lo, 2), round(px_hi, 2)], "coverage": round(cover, 2), "shares": shares,
                                             "detail": f"range {px_lo:.2f}-{px_hi:.2f} a share ({lo:.1f}-{hi:.1f}x); the book is {cover:.1f}x covered; price above the range only with a deep book, below it if it is thin; POSTPONE waits for a better window (costs the client a quarter)"},
                                 "note": f"book {cover:.1f}x covered"}, cause_id=cause.id, portfolio_id=pf.id)
        elif e.kind == "FOLLOW_ON":
            b = d["block"]
            sec = w.securities[b["security_id"]]
            bar = w.market.last_bar(sec.id)
            sold = min(b["remaining"], max(1, int(sec.adv * 0.6)))
            px = float(bar.close) * (1 - 0.004)
            proceeds = money(D(str(px * sold)))
            remaining = b["remaining"] - sold
            w.emit(E.IB_UPDATE, {"portfolio_id": pf.id, "engagement_id": e.id, "phase": "reselling the block", "status": "ACTIVE" if remaining > 0 else "ACTIVE",
                                 "resale": {"shares": sold, "price": round(px, 4), "proceeds": proceeds, "remaining": remaining}, "currency": pf.base_currency,
                                 "next_step": w.calendar.add_business_days(today, 1).isoformat() if remaining > 0 else "",
                                 "note": f"resold {sold:,} at {px:.2f} ({proceeds:,.0f}); {remaining:,} left"}, cause_id=cause.id, portfolio_id=pf.id)
            if remaining <= 0:
                e2 = pf.ib_engagements[e.id]
                pnl = e2.detail["block"]["proceeds_total"] - e2.detail["block"]["cost"]
                rep = 1.5 if pnl > 0 else -1.5
                self._close(pf, e2, cause, "CLOSED", f"block placed: {'gain' if pnl >= 0 else 'loss'} of {abs(pnl):,.0f} against the purchase", fee=ZERO, pnl=money(pnl), rep=rep, value=e.size,
                            news=(f"{sec.name}: a holder sells a {e.size / 1e6:,.0f} million block through the bank", f"{b['shares']:,} shares were bought overnight at {b['price']:.2f}, a {(1 - b['price'] / b['last']):.1%} discount to the last close, and placed with institutions."))
        elif e.kind == "BOND":
            conc = e.promise
            fair = d["fair_concession"]
            cover = max(0.1, _sig((conc - fair) / 6.0 + 0.6) * 2.4 * REGIME_ACTIVITY.get(regime, 1.0) * rng.uniform(0.85, 1.15))
            if cover < 1.0:
                if rng.random() < 0.5:
                    self._close(pf, e, cause, "PULLED", f"the book reached {cover:.1f}x at +{conc:.0f}bp: the deal is pulled", fee=ZERO, pnl=money(D(str(e.size)) * D("0.0005")) * -1, rep=-3.0)
                else:
                    self._close(pf, e, cause, "CLOSED", f"repriced {fair + 8:.0f}bp wide to fill the book; the issuer is unhappy", fee=money(D(str(e.size)) * D(str(e.fee_pct))), pnl=ZERO, rep=-1.5, value=e.size)
            else:
                rep = 1.0 if cover < 4 else 0.0
                self._close(pf, e, cause, "CLOSED", f"{e.size / 1e6:,.0f}m priced at +{conc:.0f}bp, book {cover:.1f}x covered", fee=money(D(str(e.size)) * D(str(e.fee_pct))), pnl=ZERO, rep=rep, value=e.size,
                            news=(f"{e.client} prices {e.size / 1e6:,.0f} million of {d.get('tenor_years', 5)}-year notes", f"The bonds priced {conc:.0f}bp over the issuer's curve with the book {cover:.1f} times covered; the bank led the deal."))
        elif e.kind == "LEV_LOAN":
            spread = e.promise
            fair = d["fair_spread"]
            hang_p = {"NORMAL_GROWTH": 0.05, "RATE_CUTTING": 0.03, "RATE_HIKING": 0.10, "RECESSION": 0.45, "LIQUIDITY_STRESS": 0.8}.get(regime, 0.1) + max(0.0, (fair - spread) / 300.0)
            if rng.random() < hang_p:
                disc = rng.uniform(0.03, 0.09)
                loss = money(D(str(e.size * disc)))
                self._close(pf, e, cause, "CLOSED", f"the loan hung: syndicated at {disc:.1%} below par, {loss:,.0f} lost against the fee", fee=money(D(str(e.size)) * D(str(e.fee_pct))), pnl=-loss, rep=-2.5, value=e.size)
            else:
                self._close(pf, e, cause, "CLOSED", f"{e.size / 1e6:,.0f}m term loan syndicated at S+{spread:.0f}bp", fee=money(D(str(e.size)) * D(str(e.fee_pct))), pnl=ZERO, rep=1.0, value=e.size,
                            news=(f"{e.counterparty}'s {e.size / 1e6:,.0f} million buyout loan syndicates at S+{spread:.0f}bp", f"The bank underwrote the financing for {e.client}; the paper was placed with CLOs and loan funds."))

    def _close(self, pf: Portfolio, e: Engagement, cause: Event, status: str, outcome: str, fee: Decimal, pnl: Decimal, rep: float, value: float = 0.0, news=None) -> None:
        w = self.w
        payload = {"portfolio_id": pf.id, "engagement_id": e.id, "status": status, "outcome": outcome, "fee": money(fee), "pnl": money(pnl), "reputation_delta": rep,
                   "value": float(value), "currency": pf.base_currency}
        if news:
            payload["news"] = {"headline": news[0], "body": news[1]}
        w.emit(E.IB_CLOSED, payload, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ views
    def league(self, pf: Portfolio) -> List[Dict]:
        """Year-to-date fees and deal value: rivals' books are drawn from the seed month by month, the bank's are its own."""
        w = self.w
        d = w.current_date
        rows = {r: {"bank": r, "fees": 0.0, "volume": 0.0, "deals": 0} for r in RIVALS}
        for m in range(1, d.month + 1):
            rng = random.Random(f"{w.seed}|ibleague|{d.year}-{m:02d}")
            act = REGIME_ACTIVITY.get(self.regime(), 1.0)
            for i, r in enumerate(RIVALS):
                base = [1.0, 0.95, 1.0, 0.7, 0.6, 0.45, 0.4, 0.35, 0.3, 0.25][i]
                vol = base * rng.uniform(1.5, 3.5) * 1e9 * act
                rows[r]["volume"] += vol
                rows[r]["fees"] += vol * rng.uniform(0.012, 0.02)
                rows[r]["deals"] += rng.randint(1, 4)
        st = self.stats(pf)
        rows["You"] = {"bank": "You", "fees": float(st["fees_ytd"]), "volume": float(st["volume_ytd"]), "deals": int(st["deals"])}
        out = sorted(rows.values(), key=lambda r: -r["fees"])
        for i, r in enumerate(out):
            r["rank"] = i + 1
        return out

    def book(self, pf: Portfolio) -> Dict:
        st = self.stats(pf)
        engs = [asdict(e) for e in pf.ib_engagements.values()]
        for row in engs:
            row["kind_label"] = KINDS[row["kind"]]["label"]
        active = [r for r in engs if r["status"] in ("ACTIVE", "DECISION")]
        closed = [r for r in engs if r["status"] not in ("ACTIVE", "DECISION")]
        pending = [{"mandate_id": mid, **p} for mid, by in self.pitches.items() for pid, p in by.items() if pid == pf.id]
        return {"pipeline": self.pipeline(None, pf.id), "engagements": active, "closed": closed, "pending_pitches": pending, "league": self.league(pf), "market": self.market(),
                "stats": {"reputation": round(st["reputation"], 1), "fees_ytd": st["fees_ytd"], "volume_ytd": st["volume_ytd"], "deals": st["deals"], "broken": st["broken"],
                          "fees_total": sum((e.fee_earned for e in pf.ib_engagements.values()), ZERO), "pnl_total": sum((e.pnl for e in pf.ib_engagements.values()), ZERO)},
                "log": pf.ib_log[-30:]}

    # ------------------------------------------------------------------ ledger helpers
    def _cash(self, pf: Portfolio, ccy: str, amount: Decimal, kind: str, memo: str, ev: Event) -> None:
        ca = pf.cash_account(ccy)
        ca.balance += amount
        ca.base_value += amount
        self.w.record_cash_movement(pf, ccy, amount, kind, memo, ev)

    # ------------------------------------------------------------------ handlers
    def _h_pitched(self, ev: Event) -> None:
        p = ev.payload
        self.pitches.setdefault(p["mandate_id"], {})[p["portfolio_id"]] = {"fee_pct": p["fee_pct"], "promise": p["promise"], "decides": p["decides"], "mandate": p["mandate"],
                                                                           "hidden": p["hidden"], "date": ev.sim_date, "status": "PENDING"}

    def _h_resolved(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        pitch = p["pitch"]
        m, hidden = pitch["mandate"], pitch["hidden"]
        self.pitches.get(p["mandate_id"], {}).pop(pf.id, None)
        if not p["won"]:
            pf.ib_log.append({"date": ev.sim_date, "mandate_id": p["mandate_id"], "note": f"{m['label']} for {m['client']}: {p['note']}"})
            st = self.stats(pf)
            st["reputation"] = max(0.0, st["reputation"] - 0.3)
            return
        self.taken.add(p["mandate_id"])
        e = Engagement(id=p["engagement_id"], portfolio_id=pf.id, mandate_id=p["mandate_id"], kind=m["kind"], client=m["client"], counterparty=m["counterparty"], sector=m["sector"],
                       size=float(m["size"]), fee_pct=float(pitch["fee_pct"]), promise=float(pitch["promise"]), won_date=ev.sim_date, phase=p["phase"], next_step=p["next_step"])
        d = e.detail
        if m["kind"] == "SELL_SIDE":
            d.update({"ebitda": m["ebitda"], "public_multiple": m["public_multiple"], "fair": hidden["_fair"], "rounds": 0})
        elif m["kind"] == "BUY_SIDE":
            d.update({"ebitda": m["ebitda"], "public_multiple": m["public_multiple"], "fair_premium": hidden["_fair_premium"]})
        elif m["kind"] == "IPO":
            d.update({"ebitda": m["ebitda"], "public_multiple": m["public_multiple"], "fair": hidden["_fair"], "quality": hidden["_quality"], "shares_total": 100_000_000, "shares_offered": 20_000_000})
        elif m["kind"] == "FOLLOW_ON":
            b = p["block"]
            d["block"] = {**b, "cost": D(b["cost"]), "remaining": b["shares"], "proceeds_total": ZERO}
            ccy = p["currency"]
            self._cash(pf, ccy, -D(b["cost"]), "INVESTMENT_BANKING", f"{e.id}: block of {b['shares']:,} {b['security_id']} bought at {b['price']}", ev)
            w.post(pf.id, f"Investment banking {e.id}: block bought", [dr("1190", D(b["cost"]), e.id, "underwriting position"), cr(f"1010:{ccy}", D(b["cost"]), e.id, "paid to the seller")], ev,
                   {"engagement_id": e.id, "kind": "INVESTMENT_BANKING"})
        elif m["kind"] == "BOND":
            d.update({"fair_concession": hidden["_fair_concession"], "tenor_years": m.get("tenor_years", 5), "rating": m.get("rating")})
        else:
            d.update({"fair_spread": hidden["_fair_spread"]})
        e.history.append({"date": ev.sim_date, "note": f"{p['note']}; fee {e.fee_pct:.2%}, {KINDS[e.kind]['promise']}: {e.promise:g}"})
        pf.ib_engagements[e.id] = e
        pf.ib_log.append({"date": ev.sim_date, "mandate_id": p["mandate_id"], "note": f"{m['label']} for {m['client']}: mandate won"})

    def _h_update(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        e = pf.ib_engagements[p["engagement_id"]]
        e.phase = p.get("phase", e.phase)
        if p.get("pending"):
            e.pending = p["pending"]
            e.status = "DECISION"
            e.next_step = ""
        if "next_step" in p:
            e.next_step = p["next_step"]
        if p.get("resale"):
            r = p["resale"]
            b = e.detail["block"]
            proceeds = D(r["proceeds"])
            b["remaining"] = int(r["remaining"])
            b["proceeds_total"] = D(b.get("proceeds_total", ZERO)) + proceeds
            ccy = p["currency"]
            self._cash(pf, ccy, proceeds, "INVESTMENT_BANKING", f"{e.id}: resold {r['shares']:,} at {r['price']}", ev)
            cost_out = money(D(str(b["cost"])) * D(str(r["shares"])) / D(str(b["shares"])))
            lines = [dr(f"1010:{ccy}", proceeds, e.id, "resale proceeds"), cr("1190", cost_out, e.id, "underwriting position released")]
            diff = proceeds - cost_out
            if diff > 0:
                lines.append(cr("4395", diff, e.id, "underwriting gain"))
            elif diff < 0:
                lines.append(dr("5370", -diff, e.id, "underwriting loss"))
            w.post(pf.id, f"Investment banking {e.id}: block resale", lines, ev, {"engagement_id": e.id, "kind": "INVESTMENT_BANKING"})
        if p.get("note"):
            e.history.append({"date": ev.sim_date, "note": p["note"]})

    def _h_decided(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        e = pf.ib_engagements[p["engagement_id"]]
        pend = e.pending or {}
        choice = p["choice"]
        e.pending = None
        e.status = "ACTIVE"
        tag = " (deadline passed: the default applied)" if p.get("defaulted") else ""
        if pend.get("type") == "BIDS":
            if choice == "ACCEPT":
                best = float(pend["best"])
                value = best * e.detail["ebitda"]
                fee = money(D(str(value)) * D(str(e.fee_pct)))
                rep = 2.0 + (1.0 if best >= e.promise else -1.5)
                e.history.append({"date": ev.sim_date, "note": f"best bid of {best:.1f}x accepted{tag}"})
                self._apply_close(pf, e, ev, "CLOSED", f"{e.client} sold at {best:.1f}x EBITDA ({value:,.0f})", fee, ZERO, rep, value,
                                  news=(f"{e.client} agrees to a {value / 1e9:,.2f} billion sale", f"The {e.sector.lower()} company was sold at {best:.1f}x EBITDA after a process run by the bank."))
            else:
                e.detail["rounds"] = e.detail.get("rounds", 0) + 1
                e.phase = "rerunning the process"
                e.next_step = w.calendar.add_business_days(date.fromisoformat(ev.sim_date), 10).isoformat()
                e.history.append({"date": ev.sim_date, "note": f"held out for more: the process reruns{tag}"})
        elif pend.get("type") == "PRICE":
            if choice == "POSTPONE":
                e.phase = "postponed: waiting for a window"
                e.next_step = w.calendar.add_business_days(date.fromisoformat(ev.sim_date), 40).isoformat()
                e.history.append({"date": ev.sim_date, "note": f"IPO postponed{tag}"})
                st = self.stats(pf)
                st["reputation"] = max(0.0, st["reputation"] - 1.0)
            else:
                price = float(p.get("value") or pend.get("default_value"))
                lo, hi = pend["range"]
                cover = float(pend["coverage"])
                shares = int(pend["shares"])
                rng = random.Random(f"{w.seed}|ipo|{e.id}")
                rel = (price - (lo + hi) / 2) / ((hi - lo) / 2 or 1.0)                 # −1 bottom, +1 top of the range
                pop = 0.12 * (cover - 1.5) / 2.0 - 0.10 * rel + rng.gauss(0, 0.06)
                if cover < 1.0:
                    pop -= 0.15
                proceeds = price * shares
                fee = money(D(str(proceeds)) * D(str(e.fee_pct)))
                first = price * (1 + pop)
                rep = 2.5 if 0.03 <= pop <= 0.30 else (-4.0 if pop < -0.05 else (-1.5 if pop > 0.45 else 0.5))
                e.history.append({"date": ev.sim_date, "note": f"priced at {price:.2f} ({'top' if rel > 0.5 else 'bottom' if rel < -0.5 else 'middle'} of {lo:.2f}-{hi:.2f}); first day {first:.2f} ({pop:+.1%}){tag}"})
                st = self.stats(pf)
                if pop < -0.05:
                    st["broken"] = st.get("broken", 0) + 1
                self._apply_close(pf, e, ev, "CLOSED", f"listed at {price:.2f}, first day {pop:+.1%}, {proceeds:,.0f} raised", fee, ZERO, rep, proceeds,
                                  news=(f"{e.client} IPO prices at {price:.2f}, {'jumps' if pop > 0.05 else 'slips' if pop < -0.02 else 'steady'} {pop:+.1%} on debut",
                                        f"{shares:,} shares were sold at {price:.2f} ({'above' if price > hi else 'below' if price < lo else 'within'} the {lo:.2f}-{hi:.2f} range) with the book {cover:.1f}x covered; the bank was lead-left."))

    def _h_closed(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        e = pf.ib_engagements[p["engagement_id"]]
        self._apply_close(pf, e, ev, p["status"], p["outcome"], D(p["fee"]), D(p["pnl"]), float(p["reputation_delta"]), float(p.get("value", 0.0)),
                          news=((p["news"]["headline"], p["news"]["body"]) if p.get("news") else None), emit_news=True)

    def _apply_close(self, pf: Portfolio, e: Engagement, ev: Event, status: str, outcome: str, fee: Decimal, pnl: Decimal, rep: float, value: float, news=None, emit_news: bool = True) -> None:
        w = self.w
        ccy = pf.base_currency
        e.status = status
        e.outcome = outcome
        e.pending = None
        e.next_step = ""
        e.fee_earned += money(fee)
        e.pnl += money(pnl)
        e.history.append({"date": ev.sim_date, "note": outcome})
        st = self.stats(pf)
        st["reputation"] = max(0.0, min(100.0, st["reputation"] + rep))
        if status == "CLOSED":
            st["deals"] = st.get("deals", 0) + 1
            st["fees_ytd"] = D(st.get("fees_ytd", ZERO)) + money(fee)
            st["volume_ytd"] = float(st.get("volume_ytd", 0.0)) + float(value)
        lines = []
        if fee:
            self._cash(pf, ccy, money(fee), "INVESTMENT_BANKING_FEE", f"{e.id}: {KINDS[e.kind]['label'].lower()} fee", ev)
            lines += [dr(f"1010:{ccy}", money(fee), e.id, "fee received"), cr("4390", money(fee), e.id, "investment banking fee")]
        if pnl and e.kind != "FOLLOW_ON":                                   # block P&L was booked resale by resale
            if pnl < 0:
                self._cash(pf, ccy, money(pnl), "INVESTMENT_BANKING_LOSS", f"{e.id}: underwriting loss", ev)
                lines += [dr("5370", -money(pnl), e.id, "underwriting loss"), cr(f"1010:{ccy}", -money(pnl), e.id, "paid")]
            else:
                self._cash(pf, ccy, money(pnl), "INVESTMENT_BANKING", f"{e.id}: underwriting gain", ev)
                lines += [dr(f"1010:{ccy}", money(pnl), e.id, "received"), cr("4395", money(pnl), e.id, "underwriting gain")]
        if lines:
            w.post(pf.id, f"Investment banking {e.id}: {status.lower()}", lines, ev, {"engagement_id": e.id, "kind": "INVESTMENT_BANKING"})
        pf.ib_log.append({"date": ev.sim_date, "mandate_id": e.mandate_id, "note": f"{KINDS[e.kind]['label']} for {e.client}: {outcome}"})
        if news and emit_news and not w.replaying:
            w.emit(E.NEWS_PUBLISHED, {"headline": news[0], "body": news[1], "category": "DEALS", "refs": [e.detail["block"]["security_id"]] if e.kind == "FOLLOW_ON" else []},
                   cause_id=ev.id, portfolio_id=None)
