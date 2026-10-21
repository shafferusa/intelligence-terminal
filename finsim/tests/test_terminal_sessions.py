"""WP-00 session table and calendar tests (TERMINAL_DESIGN.md §9, §19.9 session cases)."""
from __future__ import annotations

import unittest
from datetime import date, time

from finsim.terminal import config
from finsim.terminal.calendar import TradingCalendar
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.sessions import GROUPS, SessionTable


def at(d: str, hhmm: str) -> int:
    return epoch_of(date.fromisoformat(d), hhmm)


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cal = TradingCalendar(config.holidays_path())
        cls.clock = FakeClock("2026-10-20T14:00:00-04:00")
        cls.st = SessionTable(cls.cal, cls.clock)

    def test_saturday_us_equity_rests_until_monday(self):
        # Sat 2026-10-24 12:00 ET: WEEKEND; next open Mon 2026-10-26 09:30; a DAY order placed now expires Mon 16:00
        s = self.st.state("US_EQUITY", at("2026-10-24", "12:00"))
        self.assertEqual(("WEEKEND", False, None, None), (s.phase, s.open, s.opened_at, s.closes_at))
        self.assertEqual(at("2026-10-26", "09:30"), s.next_open)
        self.assertEqual("2026-10-26", s.trade_date)
        self.assertEqual(at("2026-10-26", "16:00"), self.st.day_order_expiry("US_EQUITY", at("2026-10-24", "12:00")))

    def test_open_session_and_day_expiry_after_close(self):
        # Tue 2026-10-20 14:00: OPEN 09:30-16:00; at 17:30 CLOSED and a DAY order expires Wed 16:00 (not Tue)
        s = self.st.state("US_EQUITY", at("2026-10-20", "14:00"))
        self.assertEqual(("OPEN", True, at("2026-10-20", "09:30"), at("2026-10-20", "16:00"), "2026-10-20", False), (s.phase, s.open, s.opened_at, s.closes_at, s.trade_date, s.early_close))
        self.assertEqual(at("2026-10-20", "16:00"), self.st.day_order_expiry("US_EQUITY", at("2026-10-20", "14:00")))
        s2 = self.st.state("US_EQUITY", at("2026-10-20", "17:30"))
        self.assertEqual(("CLOSED", False), (s2.phase, s2.open))
        self.assertEqual(at("2026-10-21", "09:30"), s2.next_open)
        self.assertEqual(at("2026-10-21", "16:00"), self.st.day_order_expiry("US_EQUITY", at("2026-10-20", "17:30")))
        # the default clock (Tue 14:00) is used when `at` is omitted
        self.assertTrue(self.st.state("US_EQUITY").open)

    def test_early_close_day_after_thanksgiving(self):
        # 2026-11-27: US equities close 13:00, index options 13:15, bonds 14:00; a DAY order expires at the early close
        s = self.st.state("US_EQUITY", at("2026-11-27", "10:00"))
        self.assertEqual((True, at("2026-11-27", "13:00"), True), (s.open, s.closes_at, s.early_close))
        self.assertEqual(at("2026-11-27", "13:00"), self.st.day_order_expiry("US_EQUITY", at("2026-11-27", "10:00")))
        self.assertEqual(at("2026-11-27", "13:15"), self.st.state("US_INDEX_OPTIONS", at("2026-11-27", "10:00")).closes_at)
        self.assertEqual(at("2026-11-27", "14:00"), self.st.state("US_BONDS", at("2026-11-27", "10:00")).closes_at)
        self.assertEqual(at("2026-11-27", "13:00"), self.st.state("CME_EQUITY_INDEX", at("2026-11-27", "10:00")).closes_at)
        self.assertEqual(at("2026-11-27", "13:05"), self.st.state("CBOT_GRAINS", at("2026-11-27", "10:00")).closes_at)
        # at 13:30 the equity session is over: CLOSED, next open Mon 2026-11-30 09:30
        s2 = self.st.state("US_EQUITY", at("2026-11-27", "13:30"))
        self.assertEqual(("CLOSED", at("2026-11-30", "09:30")), (s2.phase, s2.next_open))

    def test_thanksgiving_closed(self):
        s = self.st.state("US_EQUITY", at("2026-11-26", "12:00"))
        self.assertEqual(("HOLIDAY", False, at("2026-11-27", "09:30")), (s.phase, s.open, s.next_open))
        self.assertFalse(self.st.is_trading_day("US_EQUITY", date(2026, 11, 26)))
        # CME on Thanksgiving: the Wed 18:00 session closes early at 13:00 (approximation) and reopens 18:00 for Friday
        s = self.st.state("CME_EQUITY_INDEX", at("2026-11-26", "12:00"))
        self.assertEqual((True, at("2026-11-25", "18:00"), at("2026-11-26", "13:00"), True, "2026-11-26"), (s.open, s.opened_at, s.closes_at, s.early_close, s.trade_date))
        s = self.st.state("CME_EQUITY_INDEX", at("2026-11-26", "15:00"))
        self.assertEqual(("HOLIDAY", at("2026-11-26", "18:00")), (s.phase, s.next_open))

    def test_cme_sunday_open_monday_break_friday_close(self):
        # CME_EQUITY_INDEX: Sun 2026-10-25 19:00 OPEN (session Sun 18:00 -> Mon 17:00, trade date 10-26)
        s = self.st.state("CME_EQUITY_INDEX", at("2026-10-25", "19:00"))
        self.assertEqual(("OPEN", at("2026-10-25", "18:00"), at("2026-10-26", "17:00"), "2026-10-26"), (s.phase, s.opened_at, s.closes_at, s.trade_date))
        self.assertEqual(at("2026-10-26", "18:00"), s.next_open)
        # Mon 17:30: BREAK until 18:00 (trade date 10-27)
        s = self.st.state("CME_EQUITY_INDEX", at("2026-10-26", "17:30"))
        self.assertEqual(("BREAK", False, at("2026-10-26", "18:00"), "2026-10-27"), (s.phase, s.open, s.next_open, s.trade_date))
        self.assertEqual(at("2026-10-27", "17:00"), self.st.day_order_expiry("CME_EQUITY_INDEX", at("2026-10-26", "17:30")))
        # Fri 17:30: CLOSED for the weekend; Sat: WEEKEND; next open Sun 18:00
        s = self.st.state("CME_EQUITY_INDEX", at("2026-10-23", "17:30"))
        self.assertEqual(("CLOSED", at("2026-10-25", "18:00")), (s.phase, s.next_open))
        self.assertEqual("WEEKEND", self.st.state("CME_EQUITY_INDEX", at("2026-10-24", "12:00")).phase)
        # Sunday itself has no trade date (no session attributed to 10-25)
        self.assertFalse(self.st.is_trading_day("CME_EQUITY_INDEX", date(2026, 10, 25)))
        self.assertTrue(self.st.is_trading_day("CME_EQUITY_INDEX", date(2026, 10, 26)))

    def test_good_friday_full_closure(self):
        # 2026-04-03: US equities and the CME groups are closed all day; the Thursday evening has no session either
        self.assertEqual("HOLIDAY", self.st.state("US_EQUITY", at("2026-04-03", "12:00")).phase)
        self.assertEqual("HOLIDAY", self.st.state("CME_EQUITY_INDEX", at("2026-04-03", "12:00")).phase)
        self.assertFalse(self.st.state("CME_EQUITY_INDEX", at("2026-04-02", "19:00")).open)
        self.assertFalse(self.st.is_trading_day("CME_RATES", date(2026, 4, 3)))
        self.assertTrue(self.cal.full_closure(date(2026, 4, 3)))
        self.assertFalse(self.cal.full_closure(date(2026, 11, 26)))
        # next open after Good Friday: Sun 2026-04-05 18:00
        self.assertEqual(at("2026-04-05", "18:00"), self.st.state("CME_EQUITY_INDEX", at("2026-04-03", "12:00")).next_open)

    def test_settlement_dates(self):
        # T+1 over Thanksgiving: 2026-11-25 (Wed) + 1 business day = Fri 2026-11-27 (Thu 26 is a holiday); Fri 10-23 + 1 = Mon 10-26; T+2 FX from Tue 10-20 = Thu 10-22
        self.assertEqual(date(2026, 11, 27), self.cal.settlement_date(date(2026, 11, 25), 1))
        self.assertEqual(date(2026, 10, 26), self.cal.settlement_date(date(2026, 10, 23), 1))
        self.assertEqual(date(2026, 10, 22), self.cal.settlement_date(date(2026, 10, 20), 2))
        self.assertEqual(date(2026, 10, 20), self.cal.settlement_date(date(2026, 10, 20), 0))
        self.assertEqual(date(2026, 10, 19), self.cal.add_business_days(date(2026, 10, 16), 1))
        self.assertEqual(date(2026, 10, 16), self.cal.add_business_days(date(2026, 10, 19), -1))
        # ex + 15 business days from 2026-11-09 (§19.19): 11-10..13 (4), 11-16..20 (9), 11-23..25 (12), 11-27 (13), 11-30 (14), 12-01 (15)
        self.assertEqual(date(2026, 12, 1), self.cal.add_business_days(date(2026, 11, 9), 15))
        self.assertEqual(date(2026, 10, 30), self.cal.month_end(date(2026, 10, 20)))
        self.assertEqual(date(2026, 11, 30), self.cal.month_end(date(2026, 11, 5)))
        self.assertEqual(3, self.cal.business_days_between(date(2026, 10, 20), date(2026, 10, 23)))

    def test_session_overrides(self):
        st = SessionTable(self.cal, self.clock, {"2026-12-24": {"CME_EQUITY_INDEX": "13:00"}, "2026-12-28": {"US_EQUITY": "CLOSED"}})
        s = st.state("CME_EQUITY_INDEX", at("2026-12-24", "10:00"))
        self.assertEqual((True, at("2026-12-24", "13:00"), True), (s.open, s.closes_at, s.early_close))
        self.assertIn("override", s.note)
        self.assertFalse(st.is_trading_day("US_EQUITY", date(2026, 12, 28)))
        self.assertTrue(self.st.is_trading_day("US_EQUITY", date(2026, 12, 28)))
        # the NYSE file's 24 Dec early close applies without an override
        self.assertEqual(at("2026-12-24", "13:00"), self.st.state("US_EQUITY", at("2026-12-24", "10:00")).closes_at)
        self.assertEqual({"2026-12-24": "13:00"}, st.rules()["CME_EQUITY_INDEX"]["overrides"])

    def test_settle_time(self):
        self.assertEqual(at("2026-10-26", "16:00"), self.st.settle_time("CME_EQUITY_INDEX", date(2026, 10, 26)))
        self.assertEqual(at("2026-10-26", "14:30"), self.st.settle_time("NYMEX_ENERGY", date(2026, 10, 26)))
        self.assertEqual(at("2026-10-26", "14:20"), self.st.settle_time("CBOT_GRAINS", date(2026, 10, 26)))
        self.assertIsNone(self.st.settle_time("US_EQUITY", date(2026, 10, 26)))
        self.assertIsNone(self.st.settle_time("CME_EQUITY_INDEX", date(2026, 10, 25)))
        # early close caps the settle time: 2026-11-27 closes 13:00 < 16:00
        self.assertEqual(at("2026-11-27", "13:00"), self.st.settle_time("CME_EQUITY_INDEX", date(2026, 11, 27)))

    def test_grains_and_fx(self):
        # CBOT grains Tue 10-20: night session Mon 20:00 -> Tue 08:45, break, day session 09:30 -> 14:20
        s = self.st.state("CBOT_GRAINS", at("2026-10-20", "09:00"))
        self.assertEqual(("BREAK", at("2026-10-20", "09:30")), (s.phase, s.next_open))
        s = self.st.state("CBOT_GRAINS", at("2026-10-20", "10:00"))
        self.assertEqual((True, at("2026-10-20", "09:30"), at("2026-10-20", "14:20")), (s.open, s.opened_at, s.closes_at))
        s = self.st.state("CBOT_GRAINS", at("2026-10-19", "21:00"))
        self.assertEqual((True, at("2026-10-19", "20:00"), at("2026-10-20", "08:45"), "2026-10-20"), (s.open, s.opened_at, s.closes_at, s.trade_date))
        self.assertEqual("BREAK", self.st.state("CBOT_GRAINS", at("2026-10-20", "17:30")).phase)
        # FX spot: Sat WEEKEND; Sun 17:30 OPEN (trade date Mon); Fri 17:30 CLOSED; Christmas closed
        self.assertEqual("WEEKEND", self.st.state("FX_SPOT", at("2026-10-24", "12:00")).phase)
        s = self.st.state("FX_SPOT", at("2026-10-25", "17:30"))
        self.assertEqual((True, at("2026-10-25", "17:00"), at("2026-10-26", "17:00"), "2026-10-26"), (s.open, s.opened_at, s.closes_at, s.trade_date))
        self.assertEqual("CLOSED", self.st.state("FX_SPOT", at("2026-10-23", "17:30")).phase)
        self.assertFalse(self.st.is_trading_day("FX_SPOT", date(2026, 12, 25)))
        self.assertTrue(self.st.is_trading_day("FX_SPOT", date(2026, 11, 26)))      # a US holiday does not close FX
        self.assertEqual("HOLIDAY", self.st.state("FX_SPOT", at("2026-12-25", "12:00")).phase)

    def test_states_cover_every_group(self):
        states = self.st.states(at("2026-10-20", "14:00"))
        self.assertEqual(set(GROUPS), set(states))
        self.assertTrue(states["US_EQUITY"].open)
        self.assertTrue(states["CME_EQUITY_INDEX"].open)
        self.assertTrue(states["FX_SPOT"].open)
        self.assertFalse(states["FOREIGN"].open)
        # a foreign session from Yahoo's currentTradingPeriod
        f = self.st.state("FOREIGN:LSE", at("2026-10-20", "10:00"), {"open_ts": at("2026-10-20", "03:00"), "close_ts": at("2026-10-20", "11:30"), "trade_date": "2026-10-20"})
        self.assertTrue(f.open)
        self.assertEqual(at("2026-10-20", "11:30"), f.closes_at)

    def test_calendar_fallback_years_and_early_closes(self):
        cal = TradingCalendar(config.holidays_path())
        self.assertTrue(cal.is_holiday(date(2030, 7, 4)))                 # Thursday 4 July 2030, from the rule
        self.assertIn(2030, cal.source()["fallback_years"])
        self.assertNotIn(2026, cal.source()["fallback_years"])
        # Thanksgiving 2030 = 4th Thursday of November = 28 Nov; the Friday after is an early close
        self.assertEqual(time(13, 0), cal.early_close(date(2030, 11, 29)))
        self.assertEqual(time(13, 0), cal.early_close(date(2026, 11, 27)))
        self.assertIsNone(cal.early_close(date(2026, 10, 20)))
        self.assertEqual([date(2026, 11, 27), date(2026, 12, 24)], sorted(cal.early_closes(2026)))
        self.assertEqual(10, len(cal.holidays(2026)))
        # a calendar without a file falls back entirely to the rule
        rule_only = TradingCalendar(None)
        self.assertTrue(rule_only.is_holiday(date(2026, 11, 26)))
        self.assertTrue(rule_only.full_closure(date(2026, 12, 25)))
        self.assertIn(2026, rule_only.source()["fallback_years"])


if __name__ == "__main__":
    unittest.main()
