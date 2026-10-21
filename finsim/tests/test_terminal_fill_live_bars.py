"""Regression: market orders must fill on REAL Yahoo intraday payloads (found by the 2026-10-21 market-hours check).

Yahoo's 1-minute chart appends one trailing point that is not a 1-minute bar: it is stamped with the last trade time (not on a
minute boundary), O = H = L = C = the last price, volume 0.  The parser used to keep it as the "latest completed bar", so the
participation cap read volume 0 (NO_VOLUME_THIS_BAR) and a MARKET BUY 10 SPY stayed WORKING for minutes in an open session.
The payload below is the real SPY tail observed at 11:57 ET on 2026-10-21 (timestamps, closes and volumes copied from the
quote cache of that run).  No network.
"""
from __future__ import annotations

import os
import sys
import unittest
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finsim.terminal.orders_rules import OrderRow, fill_cap  # noqa: E402
from finsim.terminal.quotes.parsers import parse_intraday  # noqa: E402

TS = [1792597980, 1792598040, 1792598100, 1792598160, 1792598232]          # the last one is Yahoo's placeholder (232 = 160 + 72)
CLOSE = [772.66998291, 772.59002686, 772.60998535, 772.65997314, 772.70501709]
VOL = [23791, 24656, 20219, 37557, 0]
FETCHED_AT = 1792598364


def spy_result():
    return {
        "meta": {"symbol": "SPY", "currency": "USD", "regularMarketPrice": 772.705, "regularMarketTime": 1792598232,
                 "previousClose": 769.05, "exchangeTimezoneName": "America/New_York",
                 "currentTradingPeriod": {"regular": {"start": 1792589400, "end": 1792612800}}},
        "timestamp": list(TS),
        "indicators": {"quote": [{"open": list(CLOSE), "high": list(CLOSE), "low": list(CLOSE), "close": list(CLOSE), "volume": list(VOL)}]},
    }


def market_buy(qty=10, last_bar_ts=None):
    return OrderRow(order_id="O-T", account_id="T1:US_EXCHANGE", instrument_id="SPY", side="BUY", qty=qty, filled_qty=0,
                    order_type="MARKET", limit_px=None, stop_px=None, trail_amt=None, trail_pct=None, trail_ref_px=None,
                    fill_mode="SEQUENTIAL", tif="DAY", status="WORKING", triggered=False, first_eval_done=False,
                    last_bar_ts=last_bar_ts, last_quote_time=None, session_group="US_EQUITY", expires_ts=1792612800,
                    participation=Decimal("0.10"))


class FillOnRealYahooBars(unittest.TestCase):
    def test_the_placeholder_point_is_not_a_bar(self):
        y = parse_intraday(spy_result(), "SPY", FETCHED_AT)
        # minute-aligned points: 980, 040, 100, 160 (all % 60 == 0); 232 % 60 = 52 -> dropped.
        # completed = t <= 1792598364 - 60 = 1792598304 -> all four real bars; the latest is 1792598160 with 37,557 shares.
        self.assertEqual(1792598160, y.bar.t)
        self.assertEqual(37557, y.bar.v)
        self.assertEqual([1792597980, 1792598040, 1792598100, 1792598160], [b.t for b in y.bars_tail])
        # the last price still comes from the meta, i.e. the placeholder's price is not lost
        self.assertEqual(Decimal("772.705"), y.last)
        self.assertEqual(1792598232, y.quote_time)

    def test_a_market_order_gets_a_fill_size_on_that_payload(self):
        y = parse_intraday(spy_result(), "SPY", FETCHED_AT)
        rec = SimpleNamespace(bar=y.bar, quote_time=y.quote_time, ask_size=None, bid_size=None)
        cap, note = fill_cap(market_buy(10), rec, None, Decimal("0.10"))
        # 10 % x 37,557 = 3,755.7 -> floor to whole shares 3,755; min(remaining 10, 3,755) = 10: the order fills in full
        self.assertEqual(10, cap, note)
        self.assertIn("37,557", note)
        self.assertIn("3,755", note)

    def test_the_old_placeholder_would_have_blocked_the_fill(self):
        # what the pre-fix parser selected: the placeholder itself (volume 0) -> 0 shares fillable
        placeholder = SimpleNamespace(t=1792598232, v=0)
        cap, note = fill_cap(market_buy(10), SimpleNamespace(bar=placeholder, quote_time=1792598232, ask_size=None, bid_size=None), None, Decimal("0.10"))
        self.assertEqual(0, cap)
        self.assertIn("NO_VOLUME_THIS_BAR", note)

    def test_large_order_fills_over_several_bars_not_beyond_the_cap(self):
        y = parse_intraday(spy_result(), "SPY", FETCHED_AT)
        rec = SimpleNamespace(bar=y.bar, quote_time=y.quote_time, ask_size=None, bid_size=None)
        cap, _ = fill_cap(market_buy(10_000), rec, None, Decimal("0.10"))
        self.assertEqual(3755, cap)                                       # floor(0.10 x 37,557) = 3,755 this pass
        cap2, note2 = fill_cap(market_buy(10_000, last_bar_ts=1792598160), rec, None, Decimal("0.10"))
        self.assertEqual(0, cap2)                                         # the same bar is never used twice
        self.assertIn("NO_NEW_BAR", note2)


def es_bar(t, v):
    return SimpleNamespace(t=t, v=v)


class FillOnFuturesWithUnfinalisedVolume(unittest.TestCase):
    """The ESZ26 record of the 12:05 ET run: Yahoo's newest completed minute (t=1792598280) still shows volume 0 (not yet
    finalised) while the minutes before it carry 1,233 - 2,205 contracts.  A market BUY 1 stayed WORKING for minutes."""

    def rec(self):
        tail = [es_bar(1792598040, 2205), es_bar(1792598100, 2086), es_bar(1792598160, 1233), es_bar(1792598220, 1561), es_bar(1792598280, 0)]
        return SimpleNamespace(bar=tail[-1], bars_tail=tail, quote_time=1792598327, ask_size=None, bid_size=None)

    def order(self, qty=1, last_bar_ts=None):
        o = market_buy(qty, last_bar_ts)
        o.kind = "FUTURE"
        o.instrument_id = "ESZ26"
        return o

    def test_reads_the_latest_bar_with_reported_volume(self):
        from finsim.terminal.orders_rules import volume_bar
        self.assertEqual(1792598220, volume_bar(self.order(), self.rec()).t)          # 1,561 contracts, not the unfinalised 0
        cap, note = fill_cap(self.order(1), self.rec(), None, Decimal("0.10"))
        # 10 % x 1,561 = 156.1 -> 156 contracts this pass; min(remaining 1, 156) = 1: the 1-lot fills
        self.assertEqual(1, cap, note)
        self.assertIn("1,561", note)

    def test_never_reuses_a_bar_and_waits_while_only_unfinalised_bars_are_newer(self):
        cap, note = fill_cap(self.order(500, last_bar_ts=1792598220), self.rec(), None, Decimal("0.10"))
        self.assertEqual(0, cap)                                                        # only the 0-volume minute is newer
        self.assertIn("NO_VOLUME_THIS_BAR", note)
        finalised = self.rec()
        finalised.bars_tail[-1] = es_bar(1792598280, 1800)                              # Yahoo later finalises that minute
        finalised.bar = finalised.bars_tail[-1]
        cap, note = fill_cap(self.order(500, last_bar_ts=1792598220), finalised, None, Decimal("0.10"))
        self.assertEqual(180, cap, note)                                               # 10 % x 1,800 = 180 contracts

    def test_nothing_newer_than_the_last_used_bar_is_no_new_bar(self):
        cap, note = fill_cap(self.order(500, last_bar_ts=1792598280), self.rec(), None, Decimal("0.10"))
        self.assertEqual(0, cap)
        self.assertIn("NO_NEW_BAR", note)


if __name__ == "__main__":
    unittest.main()
