"""Careers: jobs, mandates, risk limits, levels, performance reviews.

A save is a job. The job fixes starting capital, which instrument classes are
inside the mandate, the risk limits the employer monitors, the benchmark, and
the promotion ladder. Reviews are generated at month, quarter and year ends
from the portfolio's own NAV history and event log — nothing is invented.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from .domain.events import E, Event
from .money import D, money, ZERO

ALL_CLASSES = {"EQUITY", "GOVT_BOND", "CORP_BOND", "COMMODITY_ENERGY", "COMMODITY_METAL", "COMMODITY_AG", "COMMODITY_LIVESTOCK", "EQUITY_INDEX", "RATES", "OPTION"}


@dataclass(frozen=True)
class Job:
    key: str
    title: str
    description: str
    capital: Decimal
    benchmark: Optional[str]            # security id; None = absolute return vs cash
    allowed_classes: frozenset
    max_gross_leverage: float           # gross exposure / NAV
    max_position_pct: float             # single position notional / NAV
    max_drawdown: float                 # from peak NAV
    ladder: tuple
    status: str = "PLAYABLE"            # or PLANNED (phase)
    capital_step: float = 0.5           # capital added per promotion, as fraction of starting capital


JOBS: Dict[str, Job] = {
    "SANDBOX": Job("SANDBOX", "Sandbox", "Unlimited experimentation. Any instrument, no limits, no reviews.", D(1_000_000_000), "SPXE",
                   frozenset(ALL_CLASSES), 99.0, 9.0, 1.0, ("Sandbox",)),
    "PORTFOLIO_MANAGER": Job("PORTFOLIO_MANAGER", "Portfolio Manager", "Run a $100MM multi-asset fund against an equity benchmark. Equities, bonds, ETFs and "
                             "listed futures. Judged on alpha, Sharpe and drawdown.", D(100_000_000), "SPXE", frozenset(ALL_CLASSES), 1.5, 0.15, 0.15,
                             ("Analyst", "Associate PM", "Portfolio Manager", "Senior PM", "CIO")),
    "GLOBAL_MACRO": Job("GLOBAL_MACRO", "Global Macro Trader", "Rates, equity indices and commodities via futures and government bonds. Absolute return; "
                        "leverage allowed but drawdowns are watched closely.", D(250_000_000), None,
                        frozenset({"GOVT_BOND", "EQUITY_INDEX", "RATES", "COMMODITY_ENERGY", "COMMODITY_METAL", "COMMODITY_AG", "COMMODITY_LIVESTOCK", "OPTION"}),
                        3.0, 0.30, 0.12, ("Junior Trader", "Trader", "Senior Trader", "Desk Head", "CIO")),
    "COMMODITY_TRADER": Job("COMMODITY_TRADER", "Commodity Trader", "Energy, metals, agriculture and livestock futures. Read inventories, curves and weather; "
                            "trade outrights, calendar and cross-commodity spreads.", D(100_000_000), None,
                            frozenset({"COMMODITY_ENERGY", "COMMODITY_METAL", "COMMODITY_AG", "COMMODITY_LIVESTOCK"}), 4.0, 0.35, 0.15,
                            ("Junior Trader", "Trader", "Senior Trader", "Head of Commodities", "CIO")),
    "FIXED_INCOME_PM": Job("FIXED_INCOME_PM", "Fixed-Income Portfolio Manager", "Treasuries, corporates and Treasury futures against a 5Y Treasury benchmark. "
                           "Manage duration, DV01, credit and curve exposure.", D(250_000_000), "UST-5Y", frozenset({"GOVT_BOND", "CORP_BOND", "RATES"}),
                           2.0, 0.40, 0.08, ("Analyst", "Associate PM", "Portfolio Manager", "Senior PM", "CIO")),
    # planned — listed honestly, not playable until their modules exist
    "HEDGE_FUND": Job("HEDGE_FUND", "Hedge Fund Manager", "Shorts, leverage, repo, borrow, investors and redemptions.", D(500_000_000), None, frozenset(ALL_CLASSES), 5.0, 0.3, 0.2,
                      ("Analyst", "PM", "Partner"), status="PLANNED (needs securities lending, prime brokerage — Phase 2)"),
    "BANK_TRADER": Job("BANK_TRADER", "Bank Rates Trader", "Client RFQs, dealer inventory, hedging and financing.", D(25_000_000), None, frozenset(), 10, 1, 0.2, ("Trader",),
                       status="PLANNED (needs RFQ flow and repo — Phase 2/4)"),
    "DERIVATIVES_TRADER": Job("DERIVATIVES_TRADER", "Derivatives Trader", "Options, swaps, Greeks and collateral.", D(50_000_000), None, frozenset(), 10, 1, 0.2, ("Trader",),
                              status="PLANNED (needs options and OTC — Phase 3/4)"),
    "SEC_LENDING": Job("SEC_LENDING", "Securities Lending Trader", "Lend inventory, price specials, manage recalls.", D(100_000_000), None, frozenset(), 10, 1, 0.2, ("Trader",),
                       status="PLANNED (Phase 2)"),
    "REPO_TRADER": Job("REPO_TRADER", "Repo / Funding Trader", "Secured financing, haircuts, liquidity.", D(100_000_000), None, frozenset(), 10, 1, 0.2, ("Trader",),
                       status="PLANNED (Phase 2)"),
    "TREASURY_MANAGER": Job("TREASURY_MANAGER", "Treasury Manager", "Cash, debt, liquidity, FX and rate exposure.", D(100_000_000), None, frozenset(), 10, 1, 0.2, ("Treasurer",),
                            status="PLANNED (needs FX and money markets — Phase 2)"),
    "RISK_MANAGER": Job("RISK_MANAGER", "Risk Manager", "Oversee simulated traders; approve, reject, cut limits.", D(0), None, frozenset(), 10, 1, 0.2, ("Risk Manager",),
                        status="PLANNED (needs AI traders and the risk module — Phase 5/8)"),
}


class CareerEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        self.w.on(E.RISK_BREACH, lambda world, ev: self._h_breach(ev))
        self.w.on(E.PERFORMANCE_REVIEW, lambda world, ev: self._h_review(ev))
        self.w.on(E.CAREER_EVENT, lambda world, ev: self._h_career(ev))

    def job_for(self, pf) -> Optional[Job]:
        return JOBS.get(pf.job)

    def level_title(self, pf) -> str:
        job = self.job_for(pf)
        if not job:
            return ""
        return job.ladder[min(pf.level, len(job.ladder) - 1)]

    # ------------------------------------------------------------------ daily limits
    def check_limits(self, cause: Event) -> None:
        w = self.w
        for pf in w.portfolios.values():
            job = self.job_for(pf)
            if not job or job.key == "SANDBOX":
                continue
            s = w.pnl.compute_summary(pf)
            nav = s["nav"]
            if nav <= 0:
                continue
            pf.peak_nav = max(pf.peak_nav, nav)
            breaches = []
            lev = float(s["gross_exposure"] / nav)
            if lev > job.max_gross_leverage:
                breaches.append(("GROSS_LEVERAGE", f"gross leverage {lev:.2f}x exceeds limit {job.max_gross_leverage:.2f}x", lev))
            for pos in pf.positions.values():
                expo = abs(pos.notional if pos.is_future else pos.market_value)
                wgt = float(expo / nav)
                if wgt > job.max_position_pct:
                    breaches.append(("CONCENTRATION", f"{pos.security_id} is {wgt:.1%} of NAV, limit {job.max_position_pct:.0%}", wgt))
            dd = float((pf.peak_nav - nav) / pf.peak_nav) if pf.peak_nav else 0.0
            if dd > job.max_drawdown:
                breaches.append(("DRAWDOWN", f"drawdown {dd:.1%} from peak exceeds limit {job.max_drawdown:.0%}", dd))
            for kind, text, value in breaches:
                w.emit(E.RISK_BREACH, {"portfolio_id": pf.id, "kind": kind, "text": text, "value": value, "nav": nav}, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ reviews
    def maybe_review(self, cause: Event) -> None:
        w = self.w
        d = w.current_date
        if not w.calendar.is_month_end(d):
            return
        for pf in w.portfolios.values():
            job = self.job_for(pf)
            if not job or job.key == "SANDBOX" or len(pf.nav_history) < 5:
                continue
            period = "YEAR" if d.month == 12 else "QUARTER" if d.month in (3, 6, 9) else "MONTH"
            start = date(d.year, 1, 1) if period == "YEAR" else date(d.year, d.month - 2, 1) if period == "QUARTER" else date(d.year, d.month, 1)
            review = self.build_review(pf, start.isoformat(), d.isoformat(), period)
            if review is None:
                continue
            w.emit(E.PERFORMANCE_REVIEW, review, cause_id=cause.id, portfolio_id=pf.id)
            if period in ("QUARTER", "YEAR"):
                self._career_decision(pf, review, cause)

    def build_review(self, pf, start: str, end: str, period: str) -> Optional[Dict]:
        w = self.w
        job = self.job_for(pf)
        snaps = [s for s in pf.nav_history if start <= s.date <= end]
        if len(snaps) < 2:
            return None
        before = [s for s in pf.nav_history if s.date < start]
        nav0 = before[-1].nav if before else (snaps[0].nav - snaps[0].day_pnl - snaps[0].capital_flows)
        flows = sum((s.capital_flows for s in snaps), ZERO)
        nav1 = snaps[-1].nav
        ret = float((nav1 - flows - nav0) / nav0) if nav0 else 0.0
        bench = self._benchmark_return(pf, start, end)
        # daily returns for Sharpe
        rets = []
        prev = nav0
        for s in snaps:
            if prev:
                rets.append(float(s.day_pnl / prev))
            prev = s.nav
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1))
        rf_daily = w.market.curve().policy_rate / 252
        sharpe = ((mean - rf_daily) / sd * math.sqrt(252)) if sd > 0 else 0.0
        peak = nav0
        max_dd = 0.0
        for s in snaps:
            peak = max(peak, s.nav)
            max_dd = max(max_dd, float((peak - s.nav) / peak) if peak else 0.0)
        # contributions
        contrib: Dict[str, Decimal] = {}
        buckets: Dict[str, Decimal] = {}
        for s in snaps:
            for sid, row in s.by_position.items():
                contrib[sid] = contrib.get(sid, ZERO) + row.get("total", ZERO)
            for k, v in s.explain.items():
                if not k.startswith("_"):
                    buckets[k] = buckets.get(k, ZERO) + v
        best = max(contrib.items(), key=lambda kv: kv[1], default=(None, ZERO))
        worst = min(contrib.items(), key=lambda kv: kv[1], default=(None, ZERO))
        breaches = [b for b in pf.breaches if start <= b["date"] <= end]
        fails = sum(1 for si in pf.settlements.values() if any(h["status"] == "FAILED" and start <= h["date"] <= end for h in si.history))
        calls = [m for m in pf.margin_calls if start <= m.date <= end]
        forced = sum(1 for m in calls if m.status == "FORCED")
        alpha = ret - bench if bench is not None else ret - (w.market.curve().policy_rate * len(snaps) / 252)
        rating, text = self._evaluate(job, ret, bench, alpha, max_dd, sharpe, len(breaches), fails, forced, period)
        return {"portfolio_id": pf.id, "period": period, "start": start, "end": end, "return": ret, "benchmark_return": bench, "alpha": alpha,
                "max_drawdown": max_dd, "sharpe": sharpe, "volatility": sd * math.sqrt(252), "pnl": nav1 - flows - nav0, "nav_start": nav0, "nav_end": nav1,
                "largest_contribution": {"security_id": best[0], "pnl": best[1]}, "largest_loss": {"security_id": worst[0], "pnl": worst[1]},
                "buckets": buckets, "risk_breaches": len(breaches), "settlement_failures": fails, "margin_calls": len(calls), "margin_calls_missed": forced,
                "rating": rating, "evaluation": text, "title": self.level_title(pf), "level": pf.level, "days": len(snaps)}

    def _benchmark_return(self, pf, start: str, end: str) -> Optional[float]:
        w = self.w
        if not pf.benchmark or pf.benchmark not in w.market.history:
            return None
        h = [b for b in w.market.history[pf.benchmark] if b.date <= end]
        before = [b for b in h if b.date < start]
        if not before or not h:
            return None
        p0, p1 = float(before[-1].close), float(h[-1].close)
        sec = w.securities[pf.benchmark]
        days = (date.fromisoformat(end) - date.fromisoformat(before[-1].date)).days
        carry = (sec.coupon or sec.dividend_yield or 0.0) * days / 365.0    # approximate total return
        return p1 / p0 - 1 + carry

    def _evaluate(self, job: Job, ret, bench, alpha, max_dd, sharpe, breaches, fails, forced, period):
        score = 0
        if alpha > 0.01:
            score += 2
        elif alpha > -0.01:
            score += 1
        if max_dd <= job.max_drawdown * 0.5:
            score += 1
        elif max_dd > job.max_drawdown:
            score -= 2
        if sharpe > 1.0:
            score += 1
        score -= min(2, breaches // 3)
        score -= forced * 2
        score -= min(1, fails // 3)
        if score >= 4:
            rating = "EXCEEDS"
        elif score >= 2:
            rating = "MEETS"
        elif score >= 0:
            rating = "BELOW"
        else:
            rating = "UNACCEPTABLE"
        b = f"vs benchmark {bench:+.2%}" if bench is not None else "absolute return mandate"
        text = (f"{period.title()} return {ret:+.2%} ({b}; alpha {alpha:+.2%}). Max drawdown {max_dd:.1%} against a {job.max_drawdown:.0%} limit; "
                f"Sharpe {sharpe:.2f}. {breaches} risk-limit breach(es), {fails} settlement failure(s), {forced} forced liquidation(s). ")
        text += {"EXCEEDS": "Strong, disciplined performance. Additional capital and a promotion are on the table.",
                 "MEETS": "Within expectations. Keep breaches down and drawdowns controlled.",
                 "BELOW": "Below expectations. The desk expects better risk control or better returns next period.",
                 "UNACCEPTABLE": "Unacceptable. Limits will be cut and continued breaches will end the mandate."}[rating]
        return rating, text

    def _career_decision(self, pf, review: Dict, cause: Event) -> None:
        w = self.w
        job = self.job_for(pf)
        recent = [r for r in pf.reviews if r["period"] in ("QUARTER", "YEAR")][-2:]
        ratings = [r["rating"] for r in recent] + [review["rating"]]
        if review["rating"] == "EXCEEDS" and pf.level < len(job.ladder) - 1:
            new_cap = money(job.capital * D(str(job.capital_step)))
            w.emit(E.CAREER_EVENT, {"portfolio_id": pf.id, "kind": "PROMOTION", "level": pf.level + 1, "title": job.ladder[pf.level + 1],
                                    "capital_added": new_cap, "text": f"Promoted to {job.ladder[pf.level + 1]}; the firm allocates an additional {new_cap:,.0f}."},
                   cause_id=cause.id, portfolio_id=pf.id)
            w.emit(E.CAPITAL_CONTRIBUTED, {"portfolio_id": pf.id, "currency": pf.base_currency, "amount": new_cap, "reason": "capital allocation on promotion"},
                   cause_id=cause.id, portfolio_id=pf.id)
        elif ratings[-2:] == ["UNACCEPTABLE", "UNACCEPTABLE"] and pf.level > 0:
            w.emit(E.CAREER_EVENT, {"portfolio_id": pf.id, "kind": "DEMOTION", "level": pf.level - 1, "title": job.ladder[pf.level - 1],
                                    "text": "Two consecutive unacceptable reviews: demoted and placed on probation."}, cause_id=cause.id, portfolio_id=pf.id)
        elif review["rating"] in ("BELOW", "UNACCEPTABLE"):
            w.emit(E.CAREER_EVENT, {"portfolio_id": pf.id, "kind": "WARNING", "level": pf.level, "title": self.level_title(pf),
                                    "text": "Formal warning: performance or risk control below the desk's standard."}, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ handlers
    def _h_breach(self, ev: Event) -> None:
        p = ev.payload
        self.w.portfolios[p["portfolio_id"]].breaches.append({"date": ev.sim_date, "kind": p["kind"], "text": p["text"], "value": p["value"], "event_id": ev.id})

    def _h_review(self, ev: Event) -> None:
        p = dict(ev.payload)
        p["event_id"] = ev.id
        self.w.portfolios[p["portfolio_id"]].reviews.append(p)

    def _h_career(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.level = int(p["level"])
        pf.career_log.append({"date": ev.sim_date, "kind": p["kind"], "title": p["title"], "text": p["text"], "event_id": ev.id})
