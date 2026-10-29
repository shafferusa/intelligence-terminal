"""FX_CCS_PLAN rev 2 D10 / K1 (release 1.0.6.1, package P-A): the currency settlement calendars, FX value dates and swap schedules of
``finsim.terminal.ccycal``.  Pure: no network, no database (a ``services`` stand-in carries the NYSE ``TradingCalendar`` for the legacy
pairs).  The holiday tables are the plan's (P-A task 1), checked by hand; ``calendar_publication_days.json`` holds every weekday on
which FRED has no print of SOFR (USGS), ESTR (TARGET2), SONIA (London) or EFFR (New York) - the calendars must reproduce them all."""
from __future__ import annotations

import json
import os
import types
import unittest
from datetime import date, timedelta

from finsim.terminal import ccycal, config
from finsim.terminal.calendar import TradingCalendar
from finsim.terminal.commands import RejectError

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "rfr")
SVC = types.SimpleNamespace(cal=TradingCalendar(config.holidays_path()))


def D_(s: str) -> date:
    return date.fromisoformat(s)


def mmdd(cal, year):
    return [d.strftime("%m-%d") for d in cal.holidays(year)]


class HolidayTablesTests(unittest.TestCase):
    """P-A task 1: the plan's tables, hand-checked."""

    def test_target2_2026_2028(self):
        # New Year's Day, Good Friday, Easter Monday, 1 May, 25 and 26 December - fixed days, listed even on a weekend (2026-12-26 is a Saturday)
        self.assertEqual(["01-01", "04-03", "04-06", "05-01", "12-25", "12-26"], mmdd(ccycal.TARGET, 2026))
        self.assertEqual(["01-01", "03-26", "03-29", "05-01", "12-25", "12-26"], mmdd(ccycal.TARGET, 2027))
        self.assertEqual(["01-01", "04-14", "04-17", "05-01", "12-25", "12-26"], mmdd(ccycal.TARGET, 2028))

    def test_new_york_fed_2026_2028(self):
        # 2026: Independence Day is a Saturday -> not observed (no 07-03); Juneteenth Friday 06-19 observed
        self.assertEqual(["01-01", "01-19", "02-16", "05-25", "06-19", "09-07", "10-12", "11-11", "11-26", "12-25"], mmdd(ccycal.USNY, 2026))
        # 2027: Independence Day Sunday -> Monday 07-05; Juneteenth (06-19) and Christmas (12-25) are Saturdays -> not observed
        self.assertEqual(["01-01", "01-18", "02-15", "05-31", "07-05", "09-06", "10-11", "11-11", "11-25"], mmdd(ccycal.USNY, 2027))
        # 2028: New Year's Day and Veterans Day are Saturdays -> not observed
        self.assertEqual(["01-17", "02-21", "05-29", "06-19", "07-04", "09-04", "10-09", "11-23", "12-25"], mmdd(ccycal.USNY, 2028))

    def test_london_2026_2028_substitute_days(self):
        # 2026: Christmas Friday -> Boxing Day (Saturday) moves to Monday 12-28
        self.assertEqual(["01-01", "04-03", "04-06", "05-04", "05-25", "08-31", "12-25", "12-28"], mmdd(ccycal.GBLO, 2026))
        # 2027: Christmas Saturday -> Monday 27 and Tuesday 28
        self.assertEqual(["01-01", "03-26", "03-29", "05-03", "05-31", "08-30", "12-27", "12-28"], mmdd(ccycal.GBLO, 2027))
        # 2028: New Year's Day Saturday -> Monday 01-03
        self.assertEqual(["01-03", "04-14", "04-17", "05-01", "05-29", "08-28", "12-25", "12-26"], mmdd(ccycal.GBLO, 2028))

    def test_usgs_adds_good_friday_and_the_sifma_friday_closes(self):
        # USGS = New York + Good Friday (2026-04-03, 2027-03-26, 2028-04-14: no early-close-only year in the hand-maintained list) + the Friday
        # before a Saturday Independence Day / Juneteenth / Christmas (2026-07-03 seen in the SOFR record; 2027-06-18 and 2027-12-24 by the rule)
        for y, extra in ((2026, {"04-03", "07-03"}), (2027, {"03-26", "06-18", "12-24"}), (2028, {"04-14"})):
            self.assertEqual(sorted(set(mmdd(ccycal.USNY, y)) | extra), mmdd(ccycal.USGS, y), y)
        self.assertEqual(frozenset(), ccycal.USGS_GOOD_FRIDAY_OPEN)
        self.assertIn("hand-maintained", ccycal.USGS.note)
        self.assertIn("none is known for 2026-2030", ccycal.GBLO.note)

    def test_one_off_days(self):
        # England and Wales: the 2022 Platinum Jubilee moved the spring bank holiday to Thursday 06-02 and added Friday 06-03; the state
        # funeral of 2022-09-19; the coronation 2023-05-08; the moved VE-day holiday 2020-05-08 (not 05-04)
        self.assertTrue(all(ccycal.GBLO.is_holiday(D_(d)) for d in ("2022-06-02", "2022-06-03", "2022-09-19", "2023-05-08", "2020-05-08")))
        self.assertFalse(ccycal.GBLO.is_holiday(D_("2022-05-30")))
        self.assertFalse(ccycal.GBLO.is_holiday(D_("2020-05-04")))
        self.assertTrue(ccycal.TARGET.is_holiday(D_("2001-12-31")))
        self.assertTrue(ccycal.USGS.is_holiday(D_("2018-12-05")))              # President Bush's day of mourning: no SOFR
        self.assertFalse(ccycal.USGS.is_holiday(D_("2025-01-09")))             # President Carter's: an early close only (SOFR published)

    def test_calendars_reproduce_every_publication_day(self):
        """Every weekday without a SOFR / ESTR / SONIA / EFFR print on FRED is a holiday of USGS / TARGET2 / London / New York, and no other
        weekday in the range is (2000-2026 for London: 26 years of substitute days and one-offs)."""
        with open(os.path.join(FIX, "calendar_publication_days.json"), encoding="utf-8") as fh:
            pubs = json.load(fh)
        for code in ("USGS", "TARGET", "GBLO", "USNY"):
            cal = ccycal.CALENDARS[code]
            spec = pubs[code]
            d, end, closed = D_(spec["from"]), D_(spec["to"]), []
            while d <= end:
                if d.weekday() < 5 and not cal.is_business_day(d):
                    closed.append(d.isoformat())
                d += timedelta(days=1)
            self.assertEqual(spec["weekdays_without_a_print"], closed, f"{code} against {spec['series']}")
            self.assertGreater(len(closed), 10, code)


class SpotAndTenorTests(unittest.TestCase):
    def test_spot_dates(self):
        # T+2: Thursday 10-29 -> Friday 10-30 (T+1) -> Monday 11-02 for both a USD pair and the cross
        self.assertEqual(D_("2026-11-02"), ccycal.spot_date(SVC, "EURUSD", D_("2026-10-29")))
        self.assertEqual(D_("2026-11-02"), ccycal.spot_date(SVC, "EURGBP", D_("2026-10-29")))
        # Thursday 2027-03-25: Good Friday and Easter Monday close TARGET2 -> T+1 = Tuesday 03-30 -> spot Wednesday 03-31
        self.assertEqual(D_("2027-03-31"), ccycal.spot_date(SVC, "EURUSD", D_("2027-03-25")))

    def test_usd_pair_t_plus_1_rule_and_the_cross_rule(self):
        # EURUSD traded Tuesday 2026-11-10: T+1 need only be a TARGET2 day (Veterans Day 11-11 does not delay it); spot = Thursday 11-12, not 11-13
        self.assertEqual(D_("2026-11-12"), ccycal.spot_date(SVC, "EURUSD", D_("2026-11-10")))
        # EURGBP traded Tuesday 2026-11-24: T+1 = 11-25; the spot date must also be a New York day: 11-26 is Thanksgiving -> Friday 11-27
        self.assertEqual(D_("2026-11-27"), ccycal.spot_date(SVC, "EURGBP", D_("2026-11-24")))

    def test_legacy_pair_keeps_the_nyse_dates(self):
        for pair, td in (("USDJPY", "2026-10-29"), ("USDCAD", "2026-11-10"), ("AUDUSD", "2027-03-25"), ("EURJPY", "2026-11-24")):
            from finsim.terminal.instruments import fx_spot_days
            self.assertEqual(SVC.cal.settlement_date(D_(td), fx_spot_days(pair)), ccycal.spot_date(SVC, pair, D_(td)), pair)
        vc = ccycal.value_calendar(SVC, "USDJPY")
        self.assertTrue(vc.limited)
        self.assertIn("local holidays not modelled", vc.note)
        self.assertIsNone(ccycal.for_ccy("JPY"))
        self.assertFalse(ccycal.has_calendars("USDJPY"))
        self.assertTrue(ccycal.has_calendars("EURGBP"))

    def test_tenor_dates(self):
        # 2W EURGBP from 10-29: spot 11-02 + 14 days = Monday 11-16
        self.assertEqual(D_("2026-11-16"), ccycal.tenor_date(SVC, "EURGBP", "2W", D_("2026-10-29")))
        # 24M EURGBP from 10-29: spot 2026-11-02 + 24 months = Thursday 2028-11-02 (a business day of EUR, GBP and New York); 731 days
        v = ccycal.tenor_date(SVC, "EURGBP", "24M", D_("2026-10-29"))
        self.assertEqual(D_("2028-11-02"), v)
        self.assertEqual(731, (v - D_("2026-11-02")).days)
        self.assertEqual(v, ccycal.tenor_date(SVC, "EURGBP", "2Y", D_("2026-10-29")))
        # from 10-30: spot 2026-11-03 -> 2028-11-03
        self.assertEqual(D_("2028-11-03"), ccycal.tenor_date(SVC, "EURGBP", "2Y", D_("2026-10-30")))
        # 18M and ON
        self.assertEqual(D_("2028-05-02"), ccycal.tenor_date(SVC, "EURGBP", "18M", D_("2026-10-29")))
        self.assertEqual(D_("2026-11-03"), ccycal.tenor_date(SVC, "EURGBP", "ON", D_("2026-10-29")))
        self.assertEqual(("1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "18M", "2Y"), ccycal.OTC_TENORS)
        with self.assertRaises(RejectError) as cm:
            ccycal.tenor_date(SVC, "EURGBP", "30M", D_("2026-10-29"))
        self.assertEqual("TENOR", cm.exception.code)

    def test_end_of_month_rule(self):
        # EURGBP traded Thursday 2026-11-26 settles Monday 11-30 (the last business day of November) -> + 24M = Thursday 2028-11-30
        self.assertEqual(D_("2026-11-30"), ccycal.spot_date(SVC, "EURGBP", D_("2026-11-26")))
        self.assertEqual(D_("2028-11-30"), ccycal.tenor_date(SVC, "EURGBP", "24M", D_("2026-11-26")))
        # where the rule bites: EURUSD traded 2027-02-24 settles Friday 02-26 (February's last business day); + 1M = 03-26 Good Friday:
        # modified following alone would give Tuesday 03-30 (Easter Monday 03-29 is closed too), the end-of-month rule gives Wednesday 03-31
        self.assertEqual(D_("2027-02-26"), ccycal.spot_date(SVC, "EURUSD", D_("2027-02-24")))
        self.assertEqual(D_("2027-03-31"), ccycal.tenor_date(SVC, "EURUSD", "1M", D_("2027-02-24")))
        self.assertEqual(D_("2027-03-30"), ccycal.joint(ccycal.TARGET, ccycal.USNY).modified_following(D_("2027-03-26")))

    def test_value_date_refusals_name_the_calendar(self):
        # B-T3's texts: Easter Monday 2027 is a TARGET2 (and London) holiday -> the first currency's calendar is named
        self.assertEqual("not a business day in EUR (TARGET2)", ccycal.value_date_problem(SVC, "EURGBP", D_("2027-03-29")))
        self.assertEqual("not a business day in USD (New York)", ccycal.value_date_problem(SVC, "EURUSD", D_("2026-11-11")))
        self.assertEqual("not a business day in USD (New York): the EUR is bought against USD for the same value date",
                         ccycal.value_date_problem(SVC, "EURGBP", D_("2026-11-26")))
        self.assertEqual("2026-11-28 is a Saturday: not a value date", ccycal.value_date_problem(SVC, "EURGBP", D_("2026-11-28")))
        self.assertIsNone(ccycal.value_date_problem(SVC, "EURGBP", D_("2028-11-02")))
        with self.assertRaises(RejectError) as cm:
            ccycal.require_value_date(SVC, "EURGBP", D_("2026-11-26"))
        self.assertEqual(("VALUE_DATE", "value_date"), (cm.exception.code, cm.exception.details["field"]))
        self.assertEqual("TARGET2 + London (England and Wales bank holidays) + New York (Federal Reserve holidays)", cm.exception.details["calendar"])


class ScheduleTests(unittest.TestCase):
    CAL = ccycal.joint(ccycal.TARGET, ccycal.USNY)

    def test_ccs_schedule_from_10_29(self):
        # FX_CCS_PLAN 3.3: start 2026-11-02; e_k = MF(start + 3k months) on TARGET2 + New York; p_k = e_k + 2 business days
        sch = ccycal.schedule(self.CAL, D_("2026-11-02"), 24, 3, 2, sofr=True)
        self.assertEqual(["2027-02-02", "2027-05-03", "2027-08-02", "2027-11-02", "2028-02-02", "2028-05-02", "2028-08-02", "2028-11-02"],
                         [e.isoformat() for _s, e, _p in sch])
        self.assertEqual(["2027-02-04", "2027-05-05", "2027-08-04", "2027-11-04", "2028-02-04", "2028-05-04", "2028-08-04", "2028-11-06"],
                         [p.isoformat() for _s, _e, p in sch])
        self.assertEqual([D_("2026-11-02")] + [e for _s, e, _p in sch[:-1]], [s for s, _e, _p in sch])     # s_k = e_(k-1)

    def test_ccs_schedule_from_10_30(self):
        sch = ccycal.schedule(self.CAL, ccycal.spot_date(SVC, "EURUSD", D_("2026-10-30")), 24, 3, 2, sofr=True)
        self.assertEqual(D_("2026-11-03"), sch[0][0])
        self.assertEqual(["2027-02-03", "2027-05-03", "2027-08-03", "2027-11-03", "2028-02-03", "2028-05-03", "2028-08-03", "2028-11-03"],
                         [e.isoformat() for _s, e, _p in sch])
        self.assertEqual(["2027-02-05", "2027-05-05", "2027-08-05", "2027-11-05", "2028-02-07", "2028-05-05", "2028-08-07", "2028-11-07"],
                         [p.isoformat() for _s, _e, p in sch])

    def test_sofr_period_on_a_sifma_close_is_refused(self):
        # a synthetic New-York-only schedule ending on Good Friday 2027-03-26: a New York day, but no SOFR -> 400 SCHEDULE
        with self.assertRaises(RejectError) as cm:
            ccycal.schedule(ccycal.USNY, D_("2027-02-26"), 1, 1, 0, sofr=True)
        self.assertEqual("SCHEDULE", cm.exception.code)
        self.assertIn("SOFR is not published on 2027-03-26 (SIFMA close)", cm.exception.message)
        self.assertEqual(1, len(ccycal.schedule(ccycal.USNY, D_("2027-02-26"), 1, 1, 0, sofr=False)))      # without the SOFR guard it is a date
        with self.assertRaises(RejectError) as cm2:
            ccycal.schedule(self.CAL, D_("2026-11-02"), 10, 3)
        self.assertEqual("SCHEDULE", cm2.exception.code)

    def test_calendar_arithmetic(self):
        c = self.CAL
        self.assertEqual(D_("2026-11-27"), c.add_business_days(D_("2026-11-25"), 1))           # 11-26 Thanksgiving skipped
        self.assertEqual(D_("2026-11-25"), c.add_business_days(D_("2026-11-27"), -1))
        self.assertEqual(D_("2027-04-30"), c.month_end(D_("2027-04-02")))
        self.assertEqual(D_("2027-05-31"), ccycal.TARGET.month_end(D_("2027-05-03")))
        self.assertEqual(D_("2027-05-28"), c.month_end(D_("2027-05-03")))                        # Memorial Day 05-31 in New York
        self.assertEqual(3, c.business_days_between(D_("2026-10-29"), D_("2026-11-03")))         # 10-30, 11-02, 11-03
        self.assertEqual(("TARGET", "USNY"), tuple(x.code for x in c.parts()))
        self.assertFalse(c.limited)
        self.assertEqual(["USD", "EUR", "GBP"], [k for k in ccycal.CCY_CALENDAR])


if __name__ == "__main__":
    unittest.main()
