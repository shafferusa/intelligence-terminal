"""Simulation engine: the daily cycle.

advance_one_day():
  1. DAY_CLOSING for the current date:
       trade lifecycle (capture/match/affirm/clear) -> settlements due ->
       corporate actions (ex/pay dates, coupons, maturities) -> accruals ->
       mark-to-market -> NAV snapshot & P&L explain -> expire DAY orders
  2. DAY_CLOSED
  3. Next business day opens: MARKET_CLOSE publishes that day's prices,
     working orders execute at the open, new dividends are declared,
     regime changes are announced as news.

The player then trades "in" the new day at its quotes until advancing again.
"""
from __future__ import annotations

from datetime import date
from typing import Dict

from ..domain.events import E, Event
from ..engines.market import REGIMES


class SimulationEngine:
    def __init__(self, world):
        self.w = world

    def open_day(self, d: date, first: bool = False) -> Event:
        w = self.w
        bars, curve, regime_change = w.market.generate_day(d)
        st = w.market.state_dict()
        payload = {"date": d.isoformat(), "day_index": (0 if first else w.day_count + 1),
                   "bars": {t: [b.open, b.high, b.low, b.close, b.volume, b.bid, b.ask] for t, b in bars.items()},
                   "curve": {"tenors": curve.tenors, "rates": curve.rates, "ig": curve.ig_spread_bps, "hy": curve.hy_spread_bps, "policy": curve.policy_rate},
                   "state": st, "regime_change": regime_change}
        ev = w.emit(E.MARKET_CLOSE, payload, sim_date=d.isoformat())
        if regime_change:
            r = REGIMES[regime_change]
            rc = w.emit(E.REGIME_CHANGED, {"regime": regime_change, "label": r.label}, cause_id=ev.id)
            w.emit(E.NEWS_PUBLISHED, {"headline": f"Market regime shifts: {r.label}", "body": r.description + " Expect changes in volatility, "
                                      "liquidity, the stock/rates correlation and credit spreads.", "category": "MACRO", "refs": []}, cause_id=rc.id)
        if not first:
            # Positions are re-marked as soon as the new day's prices arrive, so NAV,
            # unrealized P&L and the ledger move with the market before any trading.
            w.pnl.mark_all(ev)
            w.trading.work_open_orders(ev)
            w.corporate.declare_upcoming(ev)
        return ev

    def advance_one_day(self) -> str:
        w = self.w
        today = w.current_date
        prev = w.calendar.prev_business_day(today)
        closing = w.emit(E.DAY_CLOSING, {"date": today.isoformat()})
        w.settlement.process_lifecycle(closing)
        w.settlement.process_due(closing)
        w.corporate.process_day(closing)
        w.accruals.process_day(closing, prev)
        w.pnl.mark_all(closing)
        w.pnl.snapshot(closing)
        w.trading.expire_day_orders(closing)
        w.emit(E.DAY_CLOSED, {"date": today.isoformat()}, cause_id=closing.id)
        nxt = w.calendar.next_business_day(today)
        self.open_day(nxt)
        return today.isoformat()
