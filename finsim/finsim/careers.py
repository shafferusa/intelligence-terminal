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

ALL_CLASSES = {"EQUITY", "GOVT_BOND", "CORP_BOND", "COMMODITY_ENERGY", "COMMODITY_METAL", "COMMODITY_AG", "COMMODITY_LIVESTOCK", "EQUITY_INDEX", "RATES", "OPTION", "OTC", "PHYSICAL",
               "CRYPTO", "FX_INDEX", "VOLATILITY"}


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
    "SANDBOX": Job("SANDBOX", "Sandbox", "Unlimited experimentation. Any instrument, no limits, no reviews.", D(1_000_000_000), "SPY",
                   frozenset(ALL_CLASSES), 99.0, 9.0, 1.0, ("Sandbox",)),
    "PORTFOLIO_MANAGER": Job("PORTFOLIO_MANAGER", "Portfolio Manager", "Run a $100MM multi-asset fund against an equity benchmark. Equities, bonds, ETFs and "
                             "listed futures. Judged on alpha, Sharpe and drawdown.", D(100_000_000), "SPY", frozenset(ALL_CLASSES), 1.5, 0.15, 0.15,
                             ("Analyst", "Associate PM", "Portfolio Manager", "Senior PM", "CIO")),
    "GLOBAL_MACRO": Job("GLOBAL_MACRO", "Global Macro Trader", "Rates, equity indices and commodities via futures and government bonds. Absolute return; "
                        "leverage allowed but drawdowns are watched closely.", D(250_000_000), None,
                        frozenset({"GOVT_BOND", "EQUITY_INDEX", "RATES", "COMMODITY_ENERGY", "COMMODITY_METAL", "COMMODITY_AG", "COMMODITY_LIVESTOCK", "OPTION", "OTC", "FX_INDEX", "VOLATILITY", "CRYPTO"}),
                        3.0, 0.30, 0.12, ("Junior Trader", "Trader", "Senior Trader", "Desk Head", "CIO")),
    "COMMODITY_TRADER": Job("COMMODITY_TRADER", "Commodity Trader", "Energy, metals, agriculture and livestock futures. Read inventories, curves and weather; "
                            "trade outrights, calendar and cross-commodity spreads.", D(100_000_000), None,
                            frozenset({"COMMODITY_ENERGY", "COMMODITY_METAL", "COMMODITY_AG", "COMMODITY_LIVESTOCK", "PHYSICAL", "OPTION"}), 4.0, 0.35, 0.15,
                            ("Junior Trader", "Trader", "Senior Trader", "Head of Commodities", "CIO")),
    "FIXED_INCOME_PM": Job("FIXED_INCOME_PM", "Fixed-Income Portfolio Manager", "Treasuries, corporates and Treasury futures against a 5Y Treasury benchmark. "
                           "Manage duration, DV01, credit and curve exposure.", D(250_000_000), "UST-5Y", frozenset({"GOVT_BOND", "CORP_BOND", "RATES", "OTC_RATES"}),
                           2.0, 0.40, 0.08, ("Analyst", "Associate PM", "Portfolio Manager", "Senior PM", "CIO")),
    "HEDGE_FUND": Job("HEDGE_FUND", "Hedge Fund Manager", "Run a $500MM multi-strategy fund for outside investors: shorts, leverage, repo, borrow, options and OTC. "
                      "Investors subscribe and redeem on your numbers; management and performance fees.", D(500_000_000), None, frozenset(ALL_CLASSES), 5.0, 0.30, 0.20,
                      ("Analyst", "Portfolio Manager", "Senior PM", "Partner", "Founder"), capital_step=0.3),
    "BANK_TRADER": Job("BANK_TRADER", "Bank Rates & Credit Trader", "Make prices for clients in swaps, bonds, CDS and blocks; run the resulting inventory, hedge it and fund it. "
                       "Judged on client flow won, spread captured and hedged risk.", D(50_000_000), None,
                       frozenset({"GOVT_BOND", "CORP_BOND", "RATES", "EQUITY_INDEX", "EQUITY", "OPTION", "OTC"}), 12.0, 1.0, 0.20,
                       ("Associate", "Trader", "Senior Trader", "Desk Head", "Global Head"), capital_step=0.4),
    "DERIVATIVES_TRADER": Job("DERIVATIVES_TRADER", "Derivatives Trader", "Listed options and OTC swaps: quote client derivative requests, run a Greek-limited book, hedge with futures "
                              "and manage collateral.", D(50_000_000), None, frozenset({"OPTION", "OTC", "EQUITY", "EQUITY_INDEX", "RATES", "GOVT_BOND"}), 12.0, 1.0, 0.20,
                              ("Associate", "Trader", "Senior Trader", "Head of Derivatives", "Global Head"), capital_step=0.4),
    "SEC_LENDING": Job("SEC_LENDING", "Securities Lending Trader", "Build an inventory of stocks and Treasuries and lend it: earn fees on specials, reinvest cash collateral, "
                       "pay rebates, manage recalls and returns.", D(100_000_000), None, frozenset({"EQUITY", "GOVT_BOND", "CORP_BOND"}), 2.0, 0.25, 0.10,
                       ("Associate", "Trader", "Senior Trader", "Head of Lending", "Global Head")),
    "REPO_TRADER": Job("REPO_TRADER", "Repo / Funding Trader", "Run a matched book: finance Treasuries and corporates in repo, lend cash in reverse repo, manage haircuts, "
                       "term structure and collateral calls through the cycle.", D(100_000_000), None, frozenset({"GOVT_BOND", "CORP_BOND", "RATES"}), 15.0, 3.0, 0.10,
                       ("Associate", "Trader", "Senior Trader", "Head of Funding", "Treasurer")),
    "TREASURY_MANAGER": Job("TREASURY_MANAGER", "Corporate Treasurer", "A company's treasury: operating cash in four currencies, a debt stack with coupons and maturities, minimum "
                            "liquidity, FX receipts to hedge with forwards or cross-currency swaps, floating debt to swap.", D(120_000_000), None,
                            frozenset({"GOVT_BOND", "OTC", "OTC_RATES"}), 2.0, 1.0, 0.15, ("Analyst", "Treasury Manager", "Assistant Treasurer", "Treasurer", "CFO")),
    "RISK_MANAGER": Job("RISK_MANAGER", "Risk Manager", "Oversee four AI desks (momentum, carry, vol selling, commodity trend): set their limits, approve or reject "
                        "their large orders, force reductions. Judged on the firm's breaches and losses, not on trading.", D(0), None, frozenset(), 99.0, 9.0, 1.0,
                        ("Risk Analyst", "Risk Manager", "Senior Risk Manager", "Head of Risk", "CRO")),
    # AI desks (not selectable): created for the risk manager to oversee
    "AI_MOMENTUM": Job("AI_MOMENTUM", "AI desk — Equity momentum", "Rule-based equity momentum desk.", D(100_000_000), "SPY", frozenset({"EQUITY"}), 2.0, 0.25, 0.20, ("Desk",), status="AI"),
    "AI_CARRY": Job("AI_CARRY", "AI desk — Rates carry", "Rule-based Treasury and credit carry desk.", D(200_000_000), "UST-5Y", frozenset({"GOVT_BOND", "CORP_BOND"}), 3.0, 0.5, 0.10, ("Desk",), status="AI"),
    "AI_VOL": Job("AI_VOL", "AI desk — Volatility", "Rule-based index vol seller with delta hedging.", D(100_000_000), None, frozenset({"OPTION", "EQUITY_INDEX"}), 4.0, 0.5, 0.25, ("Desk",), status="AI"),
    "AI_COMMODITY": Job("AI_COMMODITY", "AI desk — Commodity trend", "Rule-based commodity trend follower.", D(100_000_000), None,
                        frozenset({"COMMODITY_ENERGY", "COMMODITY_METAL", "COMMODITY_AG", "COMMODITY_LIVESTOCK"}), 4.0, 0.35, 0.20, ("Desk",), status="AI"),
}

# ---------------------------------------------------------------- missions
# Each mission: id, title, description, horizon (business days), check(w, pf) -> (progress 0..1, done). Bonus capital on completion.
def _positions_sectors(w, pf):
    secs = [w.securities[p.security_id] for p in pf.positions.values() if p.quantity and not p.is_option]
    return len(secs), len({s.sector for s in secs})


def _rates_dv01(w, pf):
    f = w.risk.factor_summary(pf)
    return abs(f["dv01"])


MISSIONS: Dict[str, list] = {
    "PORTFOLIO_MANAGER": [
        {"id": "PM_DIVERSIFY", "title": "Build a diversified book", "description": "Hold at least 8 positions across 4 sectors.", "horizon": 25,
         "check": lambda w, pf: (min(1.0, _positions_sectors(w, pf)[0] / 8 * 0.5 + _positions_sectors(w, pf)[1] / 4 * 0.5), _positions_sectors(w, pf)[0] >= 8 and _positions_sectors(w, pf)[1] >= 4)},
        {"id": "PM_VAR", "title": "Respect the risk budget", "description": "No VaR limit breach for 40 consecutive sessions.", "horizon": 60,
         "check": lambda w, pf: _streak(w, pf, "VAR", 40)},
    ],
    "GLOBAL_MACRO": [
        {"id": "GM_CURVE", "title": "Express a rates view", "description": "Carry at least 25,000 of DV01 (bonds, note futures or swaps) for 10 sessions.", "horizon": 40,
         "check": lambda w, pf: _streak_metric(w, pf, "GM_CURVE", lambda: _rates_dv01(w, pf) >= 25_000, 10)},
        {"id": "GM_CMDTY", "title": "Trade the cycle", "description": "Hold commodity futures with gross notional above 20% of NAV for 10 sessions.", "horizon": 40,
         "check": lambda w, pf: _streak_metric(w, pf, "GM_CMDTY", lambda: w.risk.factor_summary(pf)["gross_commodity"] >= 0.2 * float(w.pnl.compute_summary(pf)["nav"]), 10)},
    ],
    "COMMODITY_TRADER": [
        {"id": "CT_SPREAD", "title": "Run a calendar spread", "description": "Be long one contract month and short another of the same commodity at a close.", "horizon": 30,
         "check": lambda w, pf: _calendar_spread(w, pf)},
        {"id": "CT_TREND", "title": "Follow the tape", "description": "Positive commodity P&L over 30 sessions.", "horizon": 45, "check": lambda w, pf: _bucket_pnl(w, pf, "commodities", 30)},
    ],
    "FIXED_INCOME_PM": [
        {"id": "FI_CREDIT", "title": "Take credit risk", "description": "Hold at least 25% of NAV in corporate bonds.", "horizon": 25,
         "check": lambda w, pf: _weight(w, pf, lambda s: s.asset_class == "CORP_BOND", 0.25)},
        {"id": "FI_DURATION", "title": "Manage duration", "description": "Keep DV01 between 50% and 150% of the benchmark-equivalent for 15 sessions.", "horizon": 40,
         "check": lambda w, pf: _streak_metric(w, pf, "FI_DURATION", lambda: _duration_band(w, pf), 15)},
    ],
    "HEDGE_FUND": [
        {"id": "HF_AUM", "title": "Grow the fund", "description": "Net subscriptions of 5% of starting capital.", "horizon": 130, "check": lambda w, pf: _net_subs(w, pf, 0.05)},
        {"id": "HF_SHARPE", "title": "Earn the fees", "description": "Sharpe above 1.0 at a quarterly review.", "horizon": 130, "check": lambda w, pf: _review_metric(pf, "sharpe", 1.0)},
    ],
    "BANK_TRADER": [
        {"id": "BT_FLOW", "title": "Win the flow", "description": "Win 10 client trades.", "horizon": 60, "check": lambda w, pf: (min(1.0, pf.client_stats.get("won", 0) / 10), pf.client_stats.get("won", 0) >= 10)},
        {"id": "BT_HEDGE", "title": "Hedge the book", "description": "Keep desk DV01 under 15,000 at 15 consecutive closes while carrying client trades.", "horizon": 60,
         "check": lambda w, pf: _streak_metric(w, pf, "BT_HEDGE", lambda: _rates_dv01(w, pf) < 15_000 and any(t.status == "OPEN" for t in pf.otc_trades.values()), 15)},
    ],
    "DERIVATIVES_TRADER": [
        {"id": "DT_NEUTRAL", "title": "Delta neutral", "description": "Run at least 4 option positions with dollar delta under 3% of NAV at 10 closes.", "horizon": 45,
         "check": lambda w, pf: _streak_metric(w, pf, "DT_NEUTRAL", lambda: _delta_neutral(w, pf), 10)},
        {"id": "DT_FLOW", "title": "Serve the clients", "description": "Win 6 client derivative requests.", "horizon": 60, "check": lambda w, pf: (min(1.0, pf.client_stats.get("won", 0) / 6), pf.client_stats.get("won", 0) >= 6)},
    ],
    "SEC_LENDING": [
        {"id": "SL_UTIL", "title": "Put inventory to work", "description": "Lend out at least 40% of inventory.", "horizon": 30, "check": lambda w, pf: _lend_util(w, pf, 0.4)},
        {"id": "SL_FEES", "title": "Earn the spread", "description": "Collect 150,000 of lending fees.", "horizon": 90, "check": lambda w, pf: (min(1.0, float(pf.lend_fees_earned) / 150_000), pf.lend_fees_earned >= 150_000)},
    ],
    "REPO_TRADER": [
        {"id": "RP_MATCHED", "title": "Run a matched book", "description": "Reverse repo at least 60% of repo borrowing with 100MM on each side.", "horizon": 30, "check": lambda w, pf: _matched_book(w, pf)},
        {"id": "RP_TERM", "title": "Term out the funding", "description": "Fund at least 100MM through term repo of a week or more.", "horizon": 30, "check": lambda w, pf: _term_funding(w, pf, 100_000_000)},
    ],
    "TREASURY_MANAGER": [
        {"id": "TM_FX", "title": "Hedge the receipts", "description": "Hedge at least 50% of next year's EUR receipts with forwards or a cross-currency swap.", "horizon": 40,
         "check": lambda w, pf: _fx_hedge(w, pf, "EUR", 0.5)},
        {"id": "TM_RATES", "title": "Fix the floating debt", "description": "Bring the floating share of debt to 20% or less with pay-fixed swaps.", "horizon": 40,
         "check": lambda w, pf: _floating_share(w, pf, 0.20)},
        {"id": "TM_LIQ", "title": "Keep the lights on", "description": "Never breach minimum liquidity over 60 sessions.", "horizon": 60,
         "check": lambda w, pf: _streak_metric(w, pf, "TM_LIQ", lambda: pf.cash_account(pf.base_currency).balance >= D(pf.treasury.get("min_liquidity", 0)) if pf.treasury else True, 60)},
    ],
    "RISK_MANAGER": [
        {"id": "RM_CLEAN", "title": "Keep the desks in line", "description": "No hard limit breach on any desk for 20 sessions.", "horizon": 60,
         "check": lambda w, pf: _streak_metric(w, pf, "RM_CLEAN", lambda: not any(b["date"] == w.current_date.isoformat() and b.get("severity") == "HARD" for d in w.institutions.desks() for b in d.breaches), 20)},
        {"id": "RM_DECIDE", "title": "Decide in time", "description": "Approve or reject 8 desk requests before they lapse.", "horizon": 90,
         "check": lambda w, pf: (min(1.0, _decided(w) / 8), _decided(w) >= 8)},
    ],
}


def _streak(w, pf, kind, n):
    days = pf.nav_history[-n:]
    if len(pf.nav_history) < 2:
        return 0.0, False
    bad = {b["date"] for b in pf.breaches if b["kind"] == kind}
    clean = 0
    for s in reversed(pf.nav_history):
        if s.date in bad:
            break
        clean += 1
    return min(1.0, clean / n), clean >= n


def _streak_metric(w, pf, key, cond, n):
    st = pf.mission_state.setdefault(key, 0)
    ok = False
    try:
        ok = bool(cond())
    except Exception:
        ok = False
    st = st + 1 if ok else 0
    pf.mission_state[key] = st
    return min(1.0, st / n), st >= n


def _calendar_spread(w, pf):
    by_code = {}
    for p in pf.positions.values():
        if p.is_future and p.quantity:
            by_code.setdefault(w.securities[p.security_id].underlying, []).append(p.quantity)
    ok = any(any(q > 0 for q in qs) and any(q < 0 for q in qs) for qs in by_code.values())
    return (1.0 if ok else 0.3 if by_code else 0.0), ok


def _bucket_pnl(w, pf, bucket, n):
    snaps = pf.nav_history[-n:]
    if len(pf.nav_history) < n:
        return len(pf.nav_history) / n * 0.5, False
    tot = sum((s.explain.get(bucket, D(0)) for s in snaps), D(0))
    return (1.0 if tot > 0 else 0.5), tot > 0


def _weight(w, pf, pred, target):
    nav = w.pnl.compute_summary(pf)["nav"]
    mv = sum((p.market_value for p in pf.positions.values() if p.quantity > 0 and pred(w.securities[p.security_id])), D(0))
    wgt = float(mv / nav) if nav else 0.0
    return min(1.0, wgt / target), wgt >= target


def _duration_band(w, pf):
    nav = float(w.pnl.compute_summary(pf)["nav"])
    if not nav:
        return False
    from .engines.pricing import BondPricer, Instrument
    bench = w.securities.get(pf.benchmark)
    if bench is None or not w.market.history.get(bench.id):
        return False
    rm = BondPricer.risk_metrics(bench, w.current_date, float(w.market.last_bar(bench.id).close))
    bench_dv01 = rm["dv01_per_100"] * nav / 100
    dv01 = _rates_dv01(w, pf)
    return 0.5 * bench_dv01 <= dv01 <= 1.5 * bench_dv01


def _net_subs(w, pf, frac):
    inv = pf.investors
    subs = sum((D(1) for f in inv.get("flows", []) if f["kind"] == "SUBSCRIPTION"), D(0))
    holders = sum((D(v) for v in inv.get("holders", {}).values()), D(0))
    start = D(inv.get("start_capital", pf.contributed_capital)) if inv else pf.contributed_capital
    growth = float((holders - start) / start) if start else 0.0
    return min(1.0, max(0.0, growth / frac)), growth >= frac


def _review_metric(pf, key, target):
    revs = [r for r in pf.reviews if r["period"] in ("QUARTER", "YEAR")]
    if not revs:
        return 0.0, False
    best = max(r.get(key, 0.0) for r in revs)
    return min(1.0, max(0.0, best / target)), best >= target


def _delta_neutral(w, pf):
    n = sum(1 for p in pf.positions.values() if p.is_option and p.quantity)
    if n < 4:
        return False
    g = w.options.aggregate_greeks(pf)["total"]
    nav = float(w.pnl.compute_summary(pf)["nav"])
    return abs(g["dollar_delta"]) < 0.03 * nav


def _lend_util(w, pf, target):
    b = w.lenddesk.book(pf)
    u = b["utilisation_of_inventory"]
    return min(1.0, u / target), u >= target


def _matched_book(w, pf):
    repo = sum((r.principal for r in pf.repos.values() if r.status == "OPEN" and r.side == "REPO"), D(0))
    rev = sum((r.principal for r in pf.repos.values() if r.status == "OPEN" and r.side == "REVERSE"), D(0))
    ok = repo >= 100_000_000 and rev >= D("0.6") * repo
    return min(1.0, float(min(repo, D(100_000_000)) / 100_000_000) * 0.5 + (0.5 if repo and rev >= D("0.6") * repo else 0.0)), ok


def _term_funding(w, pf, amount):
    term = sum((r.principal for r in pf.repos.values() if r.status == "OPEN" and r.side == "REPO" and r.term_type == "TERM"
                and r.maturity and (__import__("datetime").date.fromisoformat(r.maturity) - __import__("datetime").date.fromisoformat(r.start_date)).days >= 7), D(0))
    return min(1.0, float(term / amount)), term >= amount


def _fx_hedge(w, pf, ccy, target):
    d = w.treasury.dashboard(pf)
    r = d.get("fx_exposure", {}).get(ccy, {}).get("hedge_ratio", 0.0) if d else 0.0
    return min(1.0, r / target), r >= target


def _floating_share(w, pf, target):
    d = w.treasury.dashboard(pf)
    if not d or not d["total_debt"]:
        return 0.0, False
    share = d["floating_share_after_swaps"]
    base = float(d["floating_debt"] / d["total_debt"])
    return min(1.0, max(0.0, (base - share) / max(1e-9, base - target))), share <= target


def _decided(w):
    return sum(1 for d in w.institutions.desks() for r in d.desk_requests.values() if r["status"] in ("APPROVED", "REJECTED"))


class ME:
    MISSION_STARTED = "MISSION_STARTED"
    MISSION_UPDATED = "MISSION_UPDATED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    MISSION_FAILED = "MISSION_FAILED"


# ---------------------------------------------------------------- crisis mode script (sandbox or career): processed-day index -> forced events
# The save's creation day is index 0 (the player sees a calm first briefing); the script starts on the first advance.
CRISIS_SCRIPT = [
    {"day": 1, "regime": "LIQUIDITY_STRESS", "rates_bp": 35, "note": "Funding markets seize: haircuts double, spreads gap, dealers widen, Treasuries sell off in a dash for cash."},
    {"day": 5, "issuer_default": "AAL-28", "recovery": 0.25, "note": "The weakest high-yield issuer fails."},
    {"day": 10, "dealer_default": "DEUTSCHE", "note": "A specialist dealer collapses; its netting sets are closed out."},
    {"day": 31, "regime": "RECESSION", "note": "The acute phase passes into recession."},
    {"day": 96, "regime": "RATE_CUTTING", "note": "The central bank eases; recovery begins."},
]


class CareerEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        self.w.on(E.RISK_BREACH, lambda world, ev: self._h_breach(ev))
        self.w.on(E.PERFORMANCE_REVIEW, lambda world, ev: self._h_review(ev))
        self.w.on(E.CAREER_EVENT, lambda world, ev: self._h_career(ev))
        for et in (ME.MISSION_STARTED, ME.MISSION_UPDATED, ME.MISSION_COMPLETED, ME.MISSION_FAILED):
            self.w.on(et, lambda world, ev: self._h_mission(ev))

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
            if not job or job.key in ("SANDBOX", "RISK_MANAGER"):
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

    # ------------------------------------------------------------------ missions
    def start_missions(self, pf, cause: Optional[Event]) -> None:
        w = self.w
        for m in MISSIONS.get(pf.job, []):
            if any(x["id"] == m["id"] for x in pf.missions):
                continue
            w.emit(ME.MISSION_STARTED, {"portfolio_id": pf.id, "mission_id": m["id"], "title": m["title"], "description": m["description"],
                                        "deadline": w.calendar.add_business_days(w.current_date, m["horizon"]).isoformat()}, cause_id=cause.id if cause else None, portfolio_id=pf.id)

    def process_missions(self, cause: Event) -> None:
        w = self.w
        today = w.current_date.isoformat()
        for pf in w.portfolios.values():
            defs = {m["id"]: m for m in MISSIONS.get(pf.job, [])}
            for ms in pf.missions:
                if ms["status"] != "ACTIVE" or ms["id"] not in defs:
                    continue
                try:
                    progress, done = defs[ms["id"]]["check"](w, pf)
                except Exception as e:
                    progress, done = ms.get("progress", 0.0), False
                if done:
                    job = self.job_for(pf)
                    bonus = money(job.capital * D("0.05")) if job and job.capital > 0 else ZERO
                    w.emit(ME.MISSION_COMPLETED, {"portfolio_id": pf.id, "mission_id": ms["id"], "progress": 1.0, "bonus": bonus}, cause_id=cause.id, portfolio_id=pf.id)
                    if bonus > 0:
                        w.emit(E.CAPITAL_CONTRIBUTED, {"portfolio_id": pf.id, "currency": pf.base_currency, "amount": bonus, "reason": f"mission bonus: {ms['title']}"},
                               cause_id=cause.id, portfolio_id=pf.id)
                elif ms["deadline"] < today:
                    w.emit(ME.MISSION_FAILED, {"portfolio_id": pf.id, "mission_id": ms["id"], "progress": float(progress)}, cause_id=cause.id, portfolio_id=pf.id)
                elif abs(float(progress) - float(ms.get("progress", 0.0))) >= 0.05:
                    w.emit(ME.MISSION_UPDATED, {"portfolio_id": pf.id, "mission_id": ms["id"], "progress": float(progress)}, cause_id=cause.id, portfolio_id=pf.id)

    def _h_mission(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        if ev.type == ME.MISSION_STARTED:
            pf.missions.append({"id": p["mission_id"], "title": p["title"], "description": p["description"], "started": ev.sim_date, "deadline": p["deadline"],
                                "status": "ACTIVE", "progress": 0.0})
            return
        ms = next(x for x in pf.missions if x["id"] == p["mission_id"])
        ms["progress"] = float(p.get("progress", ms["progress"]))
        if ev.type == ME.MISSION_COMPLETED:
            ms["status"], ms["completed"], ms["bonus"] = "COMPLETED", ev.sim_date, D(p.get("bonus", 0))
            pf.career_log.append({"date": ev.sim_date, "kind": "MISSION", "text": f"Mission complete: {ms['title']}" + (f" — bonus allocation {D(p['bonus']):,.0f}" if D(p.get("bonus", 0)) else "")})
        elif ev.type == ME.MISSION_FAILED:
            ms["status"], ms["failed"] = "FAILED", ev.sim_date
            pf.career_log.append({"date": ev.sim_date, "kind": "MISSION", "text": f"Mission failed: {ms['title']} ({ms['progress']:.0%} progress)"})

    # ------------------------------------------------------------------ reviews
    def maybe_review(self, cause: Event) -> None:
        w = self.w
        d = w.current_date
        if not w.calendar.is_month_end(d):
            return
        for pf in w.portfolios.values():
            job = self.job_for(pf)
            if not job or job.key == "SANDBOX" or job.status == "AI" or len(pf.nav_history) < 5:
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
        mean = sum(rets) / len(rets) if rets else 0.0
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)) if rets else 0.0
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
        self.w.portfolios[p["portfolio_id"]].breaches.append({"date": ev.sim_date, "kind": p["kind"], "text": p["text"], "value": p["value"], "event_id": ev.id,
                                                              "severity": p.get("severity", "HARD")})

    def _h_review(self, ev: Event) -> None:
        p = dict(ev.payload)
        p["event_id"] = ev.id
        self.w.portfolios[p["portfolio_id"]].reviews.append(p)

    def _h_career(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.level = int(p["level"])
        pf.career_log.append({"date": ev.sim_date, "kind": p["kind"], "title": p["title"], "text": p["text"], "event_id": ev.id})
