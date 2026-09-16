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
        # A/B/C — markets
        bars, curve, regime_change = w.market.generate_day(d)
        st = w.market.state_dict()
        payload = {"date": d.isoformat(), "day_index": w.day_count + 1,
                   "bars": {t: [b.open, b.high, b.low, b.close, b.volume, b.bid, b.ask] for t, b in bars.items()},
                   "curve": {"tenors": curve.tenors, "rates": curve.rates, "ig": curve.ig_spread_bps, "hy": curve.hy_spread_bps, "policy": curve.policy_rate},
                   "state": st, "regime_change": regime_change, "commodities": w.market.commodity_payload()}
        mkt = w.emit(E.MARKET_CLOSE, payload, cause_id=start.id, sim_date=d.isoformat())
        if regime_change:
            r = REGIMES[regime_change]
            rc = w.emit(E.REGIME_CHANGED, {"regime": regime_change, "label": r.label}, cause_id=mkt.id)
            w.emit(E.NEWS_PUBLISHED, {"headline": f"Market regime shifts: {r.label}", "body": r.description + " Expect changes in volatility, "
                                      "liquidity, the stock/rates correlation, credit spreads and commodity demand.", "category": "MACRO", "refs": []}, cause_id=rc.id)
        for n in w.market.day_news:
            w.emit(E.NEWS_PUBLISHED, {"headline": n["headline"], "body": n["body"], "category": n["category"], "refs": [n["code"]]}, cause_id=mkt.id)
        w.corporate.declare_upcoming(mkt)
        # D — overnight instructions meet the session
        w.trading.work_orders(mkt)
        # E/H — post-trade
        closing = w.emit(E.DAY_CLOSING, {"date": d.isoformat()}, cause_id=start.id)
        w.settlement.process_lifecycle(closing)
        w.settlement.process_due(closing)
        # I — corporate actions
        w.corporate.process_day(closing)
        # F/G — futures and accruals
        w.futures.expire_contracts(closing)
        w.futures.daily_settlement(closing)
        w.accruals.process_day(closing, prev)
        w.pnl.mark_all(closing)
        w.futures.sweep_margin(closing)
        # J — results
        w.careers.check_limits(closing)
        w.pnl.snapshot(closing)
        w.trading.expire_day_orders(closing)
        w.careers.maybe_review(closing)
        # K — briefing
        w.briefing.build(closing)
        w.emit(E.DAY_CLOSED, {"date": d.isoformat()}, cause_id=closing.id)
        return d.isoformat()

    def advance_one_day(self) -> str:
        nxt = self.w.calendar.next_business_day(self.w.current_date)
        return self.run_daily_process(nxt)
