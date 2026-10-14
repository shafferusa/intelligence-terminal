"""finsim/tzfallback.py: the built-in zones agree with the tz database, and FinSim runs without one (stock Windows)."""
import builtins
import importlib
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from finsim import tzfallback as T


class BuiltinZones(unittest.TestCase):
    def test_agree_with_the_tz_database_every_six_hours_1996_2030(self):
        for name in ("America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Tokyo", "Asia/Kolkata"):
            real, mine = ZoneInfo(name), T.builtin_zone(name)
            t = datetime(1996, 1, 1, tzinfo=timezone.utc)
            while t < datetime(2031, 1, 1, tzinfo=timezone.utc):
                a, b = t.astimezone(real), t.astimezone(mine)
                self.assertEqual(a.replace(tzinfo=None), b.replace(tzinfo=None), (name, t))
                t += timedelta(hours=6)

    def test_switch_moments_new_york(self):
        ny = T.builtin_zone("America/New_York")
        # 2026-03-08 02:00 EST -> 03:00 EDT (07:00 UTC); 2026-11-01 02:00 EDT -> 01:00 EST (06:00 UTC)
        self.assertEqual(datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc).astimezone(ny).hour, 1)
        self.assertEqual(datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc).astimezone(ny).hour, 3)
        self.assertEqual(datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc).astimezone(ny).hour, 1)
        self.assertEqual(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc).astimezone(ny).hour, 1)
        self.assertEqual(datetime(2026, 7, 1, 20, 0, tzinfo=ny).astimezone(timezone.utc).hour, 0)   # 20:00 EDT = 00:00 UTC

    def test_unknown_zone_raises(self):
        with self.assertRaises(KeyError):
            T.get_zone("Mars/Olympus_Mons")


class WithoutTzDatabase(unittest.TestCase):
    """A stock Windows Python: ZoneInfo cannot find any key."""

    def test_clock_imports_and_works(self):
        import zoneinfo
        real = zoneinfo.ZoneInfo

        def broken(key):
            raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")
        zoneinfo.ZoneInfo = broken
        try:
            from finsim import clock
            importlib.reload(clock)
            self.assertIsInstance(clock.MARKET_TZ, T.RuleZone)
            ny = clock.market_now(datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc))
            self.assertEqual((ny.hour, ny.minute), (9, 30))
        finally:
            zoneinfo.ZoneInfo = real
            from finsim import clock
            importlib.reload(clock)


if __name__ == "__main__":
    unittest.main()
