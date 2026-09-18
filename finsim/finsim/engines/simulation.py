"""Simulation engine: the once-per-day cycle.

One call to `run_daily_process(d)` is one simulated business day, executed at
the world's update time (career mode) or on demand (sandbox):

   A/B  macro + regime update, commodity fundamentals, news
   C    reprice everything (equities, bonds, curve, spreads, commodity curves,
        futures), list new contract months
   D    execute the instructions the player left overnight against the session
   E/H  post-trade lifecycle and settlements due today (fails retry)
   I    corporate actions: ex/pay dates, coupons, maturities
   F/G  futures: expiries auto-close, variation margin, initial-margin sweep,
        margin calls / forced liquidation; interest accruals
   J    mark positions, risk-limit checks, NAV snapshot and P&L explain,
        expire good-for-day orders, performance reviews at period ends
   K    daily briefing

The player then reviews the briefing and enters instructions for the next day.
"""
from __future__ import annotations

from datetime import date

from ..domain.events import E, Event
from ..engines.market import REGIMES


class SimulationEngine:
    def __init__(self, world):
        self.w = world

    def run_daily_process(self, d: date) -> str:
        w = self.w
        prev = w.current_date if w.current_date and w.current_date < d else w.calendar.prev_business_day(d)
        start = w.emit(E.DAY_STARTED, {"date": d.isoformat(), "previous": prev.isoformat()}, sim_date=d.isoformat())
        w.apply_scenario(start)
        # A/B/C — markets (the lending market sees what the player has on loan)
        w.market.player_on_loan = w.seclending.player_on_loan()
        bars, curve, regime_change = w.market.generate_day(d)
        st = w.market.state_dict()
        payload = {"date": d.isoformat(), "day_index": w.day_count + 1,
                   "bars": {t: [b.open, b.high, b.low, b.close, b.volume, b.bid, b.ask] for t, b in bars.items()},
                   "curve": {"tenors": curve.tenors, "rates": curve.rates, "ig": curve.ig_spread_bps, "hy": curve.hy_spread_bps, "policy": curve.policy_rate},
                   "state": st, "regime_change": regime_change, "commodities": w.market.commodity_payload(),
                   "lending": w.market.lending_payload(), "fx": w.market.fx_payload(), "vol": w.market.vol_payload(),
                   "dealers": w.market.dealer_payload(), "macro": w.market.macro_payload(), "corporate_events": w.market.corporate_events_payload()}
        mkt = w.emit(E.MARKET_CLOSE, payload, cause_id=start.id, sim_date=d.isoformat())
        if regime_change:
            r = REGIMES[regime_change]
            rc = w.emit(E.REGIME_CHANGED, {"regime": regime_change, "label": r.label}, cause_id=mkt.id)
            w.emit(E.NEWS_PUBLISHED, {"headline": f"Market regime shifts: {r.label}", "body": r.description + " Expect changes in volatility, "
                                      "liquidity, the stock/rates correlation, credit spreads and commodity demand.", "category": "MACRO", "refs": []}, cause_id=rc.id)
        for n in w.market.day_news:
            w.emit(E.NEWS_PUBLISHED, {"headline": n["headline"], "body": n["body"], "category": n["category"], "refs": [n["code"]]}, cause_id=mkt.id)
        # macro world and corporate events become auditable events; defaults ripple into bonds, CDS and dealers
        mp = w.market.macro_payload()
        for r in mp.get("releases", []):
            w.emit(E.ECONOMIC_RELEASE, r, cause_id=mkt.id)
        for e in mp.get("earnings", []):
            w.emit(E.EARNINGS_REPORTED, {k: v for k, v in e.items() if k != "fundamentals"}, cause_id=mkt.id)
        for r in mp.get("ratings", []):
            w.emit(E.RATING_CHANGED, r, cause_id=mkt.id)
        for dflt in mp.get("defaults", []):
            w.issuer_default(dflt["reference"], float(dflt["recovery"]), mkt)
        for k, dp in w.market.dealer_payload().items():
            if dp.get("default_now") and not w.market.dealers.state[k].defaulted:
                w.otc.default_counterparty(k, mkt)
        w.cevents.announce(mkt)
        w.corporate.declare_upcoming(mkt)
        # C2 — the franchise: client requests arrive / resolve, AI desks decide (their orders join the session)
        w.clients.process_day(mkt)
        w.institutions.process_day(mkt)
        # D — overnight instructions meet the session
        w.trading.work_orders(mkt)
        # E/H — post-trade
        closing = w.emit(E.DAY_CLOSING, {"date": d.isoformat()}, cause_id=start.id)
        w.settlement.process_lifecycle(closing)
        w.settlement.process_due(closing)
        w.cevents.process_fails(closing)
        # I — corporate actions
        w.corporate.process_day(closing)
        w.cevents.process_day(closing)
        # F/G — futures, financing desks, accruals
        w.futures.expire_contracts(closing)
        w.futures.daily_settlement(closing)
        w.seclending.process_day(closing, prev)
        w.lenddesk.process_day(closing, prev)
        w.repo.process_day(closing, prev)
        w.pcredit.process_day(closing, prev)
        w.fx.process_day(closing)
        w.otc.process_day(closing, prev)
        w.options.process_day(closing)
        w.pnl.mark_all(closing)
        w.futures.sweep_margin(closing)
        w.prime.process_day(closing, prev)
        w.accruals.process_day(closing, prev)
        w.cdesk.process_day(closing, prev)
        w.treasury.process_day(closing, prev)
        w.investors.process_day(closing, prev)
        w.pnl.mark_all(closing)
        # J — results
        w.careers.check_limits(closing)
        w.pnl.snapshot(closing)
        w.risk.process_day(closing)
        w.trading.expire_day_orders(closing)
        w.careers.process_missions(closing)
        w.careers.maybe_review(closing)
        # K — briefing
        w.briefing.build(closing)
        w.emit(E.DAY_CLOSED, {"date": d.isoformat()}, cause_id=closing.id)
        return d.isoformat()

    def advance_one_day(self) -> str:
        nxt = self.w.calendar.next_business_day(self.w.current_date)
        return self.run_daily_process(nxt)
