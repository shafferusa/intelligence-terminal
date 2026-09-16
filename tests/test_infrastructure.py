"""Phase 11 — infrastructure: the real-time scheduler under a fake clock, save versioning and migration,
resilient replay with integrity reporting, logging and the health endpoint."""
import json
import logging
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from helpers import make_world
from finsim.api.server import Router, Scheduler
from finsim.api.service import Service
from finsim.domain.events import E, Event
from finsim.log import get_logger
from finsim.migrations import migrate, save_version_of
from finsim.store import EventStore
from finsim.version import SAVE_VERSION, ENGINE_VERSION
from finsim.world import World, ReplayError

NY = ZoneInfo("America/New_York")


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


class FakeClockSchedulerTest(unittest.TestCase):
    def test_scheduler_processes_exactly_the_due_days_under_a_fake_clock(self):
        s = Service(EventStore(":memory:"), strict_replay=True)
        router = Router(s)
        now = [at(2026, 1, 9, 10, 0)]                 # Friday 10:00 ET, after the 09:00 update
        sched = Scheduler(router, interval=60, clock=lambda: now[0], sleep=lambda _: None)
        r = s.create_world("career", 42, job="PORTFOLIO_MANAGER", clock_mode="REAL_TIME", timezone="America/New_York", update_time="09:00", at=now[0])
        w = s.world(r["world_id"], at=now[0])
        self.assertEqual(w.current_date, date(2026, 1, 9))
        self.assertEqual(sched.tick(), {}, "nothing due on the day the save was created")
        now[0] = at(2026, 1, 10, 12, 0)               # Saturday: no business day
        self.assertEqual(sched.tick(), {})
        now[0] = at(2026, 1, 12, 8, 59)               # Monday before the update time
        self.assertEqual(sched.tick(), {})
        now[0] = at(2026, 1, 12, 9, 0)                # Monday at the update time
        self.assertEqual(sched.tick(), {w.id: ["2026-01-12"]})
        self.assertEqual(sched.tick(), {}, "idempotent: a second tick at the same time does nothing")
        now[0] = at(2026, 1, 20, 9, 30)               # a week later; 2026-01-19 (MLK day) is a holiday
        self.assertEqual(sched.tick(), {w.id: ["2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16", "2026-01-20"]})
        self.assertEqual(w.current_date, date(2026, 1, 20))
        self.assertEqual(s.scheduler_state["ticks"], 6)
        self.assertEqual(s.scheduler_state["last_result"], {w.id: ["2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16", "2026-01-20"]})
        nu = w.next_update_at(now[0])
        self.assertEqual((nu.date(), nu.hour, nu.minute), (date(2026, 1, 21), 9, 0))
        self.assertEqual(len(w.run_log), 6)
        self.assertTrue(all(x["events"] > 0 and x["ms"] >= 0 for x in w.run_log))
        # a career world cannot be advanced by hand; a sandbox world ignores the scheduler
        from finsim.world import CommandError
        with self.assertRaises(CommandError):
            w.advance(1)
        r2 = s.create_world("sandbox", 7, "2026-01-05", job="SANDBOX", clock_mode="SANDBOX")
        now[0] = at(2026, 3, 1, 12, 0)
        done = sched.tick()
        self.assertNotIn(r2["world_id"], done)
        # the loop uses tick with the injected clock and sleep; run it once
        calls = []
        sched.sleep = lambda n: (calls.append(n), sched.stop.set())
        sched.loop()
        self.assertEqual(calls, [60])

    def test_next_update_and_target_date_across_timezones(self):
        from finsim.clock import ClockConfig, target_sim_date, next_update
        from finsim.calendar import BusinessCalendar
        cal = BusinessCalendar()
        cfg = ClockConfig("REAL_TIME", "Asia/Tokyo", "07:30")
        t = datetime(2026, 1, 12, 7, 29, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.assertEqual(target_sim_date(cfg, cal, t), date(2026, 1, 9), "before the update time the previous business day is current")
        self.assertEqual(target_sim_date(cfg, cal, t + timedelta(minutes=1)), date(2026, 1, 12))
        self.assertEqual(next_update(cfg, cal, t).isoformat(), datetime(2026, 1, 12, 7, 30, tzinfo=ZoneInfo("Asia/Tokyo")).isoformat())
        self.assertEqual(next_update(cfg, cal, t + timedelta(minutes=1)).date(), date(2026, 1, 13))


class SaveVersioningTest(unittest.TestCase):
    def _old_save(self, store, world_id):
        """Rewrite a stored save as version 1: strip the keys phases 7–9 added."""
        events = store.load_events(world_id)
        for ev in events:
            if ev.type == E.WORLD_CREATED:
                p = {k: v for k, v in ev.payload.items() if k not in ("scenario", "save_version", "engine_version")}
                store.replace_event(world_id, ev.seq, Event(ev.seq, ev.id, ev.type, ev.sim_date, p, ev.cause_id, ev.portfolio_id))
            elif ev.type == E.MARKET_CLOSE:
                p = {k: v for k, v in ev.payload.items() if k not in ("macro", "corporate_events")}
                store.replace_event(world_id, ev.seq, Event(ev.seq, ev.id, ev.type, ev.sim_date, p, ev.cause_id, ev.portfolio_id))
        store.set_save_version(world_id, 1)

    def test_new_saves_carry_the_version_and_old_saves_are_migrated_on_load(self):
        store = EventStore(":memory:")
        w, pf, _ = make_world(store=store, capital=5_000_000)
        w.place_order(pf.id, "SPXE", "BUY", 100)
        w.advance(2)
        nav = w.ledgers[pf.id].nav()
        self.assertEqual(w.save_version, SAVE_VERSION)
        self.assertEqual(save_version_of(store.load_events("t")), SAVE_VERSION)
        self.assertEqual(store.list_worlds()[0]["save_version"], SAVE_VERSION)
        self._old_save(store, "t")
        self.assertEqual(save_version_of(store.load_events("t")), 1)
        evs, rep = migrate(store.load_events("t"))
        self.assertEqual((rep["from"], rep["to"], rep["migrated"]), (1, SAVE_VERSION, True))
        self.assertTrue(any("scenario=NONE" in n for n in rep["notes"]))
        w2 = World.load(store, "t")
        self.assertEqual(w2.save_version, SAVE_VERSION)
        self.assertTrue(w2.migration["migrated"])
        self.assertEqual(w2.scenario, "NONE")
        self.assertEqual(w2.ledgers[pf.id].nav(), nav, "migration changes no economics")
        self.assertTrue(w2.integrity["ok"])
        self.assertEqual(w2.replay_errors, [])
        # the service records the upgrade in the store and reports it
        s = Service(store, strict_replay=True)
        info = s.world_info("t")
        self.assertEqual(info["save_version"], SAVE_VERSION)
        self.assertEqual(info["engine_version"], ENGINE_VERSION)
        self.assertTrue(info["migration"]["migrated"])
        self.assertEqual(store.list_worlds()[0]["save_version"], SAVE_VERSION)
        # a save from the future is refused
        evs = store.load_events("t")
        wc = next(e for e in evs if e.type == E.WORLD_CREATED)
        store.replace_event("t", wc.seq, Event(wc.seq, wc.id, wc.type, wc.sim_date, {**wc.payload, "save_version": SAVE_VERSION + 5}, wc.cause_id, wc.portfolio_id))
        with self.assertRaises(RuntimeError):
            World.load(store, "t")

    def test_store_schema_upgrade_adds_the_version_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            import sqlite3
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_utc TEXT NOT NULL)")
            conn.execute("INSERT INTO worlds VALUES ('old', 'Old', '2026-01-01T00:00:00+00:00')")
            conn.commit()
            conn.close()
            st = EventStore(path)
            self.assertEqual(st.list_worlds()[0]["save_version"], 1, "pre-versioning databases are version 1")
            st.close()


class ResilientReplayTest(unittest.TestCase):
    def _damage(self, store, world_id):
        """Corrupt one trade event so its handler fails: drop the security id."""
        evs = store.load_events(world_id)
        tr = next(e for e in evs if e.type == E.TRADE_EXECUTED)
        bad = Event(tr.seq, tr.id, tr.type, tr.sim_date, {k: v for k, v in tr.payload.items() if k != "security_id"}, tr.cause_id, tr.portfolio_id)
        store.replace_event(world_id, tr.seq, bad)
        return tr.seq

    def test_strict_load_raises_and_resilient_load_reports(self):
        store = EventStore(":memory:")
        w, pf, _ = make_world(store=store, capital=5_000_000)
        w.place_order(pf.id, "SPXE", "BUY", 100)
        w.advance(3)
        last = w.current_date
        seq = self._damage(store, "t")
        with self.assertRaises(ReplayError) as cm:
            World.load(store, "t", strict=True)
        self.assertEqual(cm.exception.seq, seq)
        self.assertEqual(cm.exception.etype, E.TRADE_EXECUTED)
        w2 = World.load(store, "t", strict=False)
        self.assertEqual(w2.current_date, last, "replay continued past the damaged event")
        self.assertEqual(len(w2.events), len(w.events))
        self.assertTrue(w2.replay_errors and w2.replay_errors[0]["seq"] == seq, "the damaged event is reported first")
        self.assertTrue(all(e["seq"] >= seq for e in w2.replay_errors), "events before the damage applied cleanly; dependants of the lost trade cascade")
        self.assertFalse(w2.integrity["ok"])
        self.assertEqual(w2.integrity["replay_errors"], len(w2.replay_errors))
        # the service surfaces it (non-strict by default) and health degrades
        s = Service(store)
        self.assertFalse(s.strict_replay)
        info = s.world_info("t")
        self.assertGreaterEqual(len(info["replay_errors"]), 1)
        self.assertFalse(info["integrity"]["ok"])
        h = s.health()
        self.assertEqual(h["status"], "degraded")
        self.assertEqual(h["worlds"][0]["replay_errors"], len(info["replay_errors"]))
        s_strict = Service(store, strict_replay=True)
        with self.assertRaises(ReplayError):
            s_strict.world("t")

    def test_integrity_of_a_healthy_save(self):
        store = EventStore(":memory:")
        w, pf, _ = make_world(store=store, capital=5_000_000)
        w.place_order(pf.id, "UST-10Y", "BUY", 1_000_000)
        w.advance(2)
        w2 = World.load(store, "t")
        self.assertTrue(w2.integrity["ok"])
        self.assertEqual(w2.integrity["problems"], [])
        self.assertEqual(w2.integrity["nav"][pf.id], str(w.ledgers[pf.id].nav()))


class LoggingAndHealthTest(unittest.TestCase):
    def test_logger_writes_to_the_configured_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "finsim.log")
            log = get_logger("finsim", path=path, level="INFO")
            log.info("hello from the test")
            for h in log.handlers:
                h.flush()
            with open(path) as f:
                txt = f.read()
            self.assertIn("hello from the test", txt)
            child = get_logger("world")
            self.assertEqual(child.name, "finsim.world")
            self.assertIs(child.parent, log)
            get_logger("finsim", path="", level="WARNING")   # restore: stderr only

    def test_health_endpoint_and_run_log(self):
        s = Service(EventStore(":memory:"), strict_replay=True)
        router = Router(s)
        h = router.dispatch("GET", "/api/health", {}, {})
        self.assertEqual(h["status"], "ok")
        self.assertEqual(h["engine_version"], ENGINE_VERSION)
        self.assertEqual(h["save_version"], SAVE_VERSION)
        self.assertEqual(h["worlds_loaded"], 0)
        r = router.dispatch("POST", "/api/worlds", {}, {"name": "x", "seed": 1, "start_date": "2026-01-05", "job": "SANDBOX", "clock_mode": "SANDBOX"})
        router.dispatch("POST", f"/api/worlds/{r['world_id']}/advance", {}, {"days": 2})
        h = router.dispatch("GET", "/api/health", {}, {})
        self.assertEqual(h["worlds_loaded"], 1)
        self.assertEqual(h["worlds"][0]["last_run"]["date"], "2026-01-07")
        self.assertTrue(h["worlds"][0]["integrity_ok"])
        json.dumps(h)


if __name__ == "__main__":
    unittest.main()
