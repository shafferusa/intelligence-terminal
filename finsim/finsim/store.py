"""Event store (sqlite3, standard library).

Only immutable events are persisted. State is rebuilt by replay. The schema is
deliberately trivial so it can be pointed at PostgreSQL later: one append-only
table of (world_id, seq, event_json).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List

from .domain.events import Event


class EventStore:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_utc TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS events (world_id TEXT NOT NULL, seq INTEGER NOT NULL, event_id TEXT NOT NULL, "
                          "type TEXT NOT NULL, sim_date TEXT NOT NULL, cause_id TEXT, portfolio_id TEXT, body TEXT NOT NULL, "
                          "PRIMARY KEY (world_id, seq))")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_events_type ON events(world_id, type)")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(worlds)").fetchall()}
        if "save_version" not in cols:       # schema upgrade for databases created before save versioning
            self.conn.execute("ALTER TABLE worlds ADD COLUMN save_version INTEGER NOT NULL DEFAULT 1")
        self.conn.commit()

    def create_world(self, world_id: str, name: str, save_version: int = None) -> None:
        from .version import SAVE_VERSION
        self.conn.execute("INSERT INTO worlds (id, name, created_utc, save_version) VALUES (?, ?, ?, ?)",
                          (world_id, name, datetime.now(timezone.utc).isoformat(), int(save_version or SAVE_VERSION)))
        self.conn.commit()

    def set_save_version(self, world_id: str, save_version: int) -> None:
        self.conn.execute("UPDATE worlds SET save_version = ? WHERE id = ?", (int(save_version), world_id))
        self.conn.commit()

    def list_worlds(self) -> List[Dict]:
        rows = self.conn.execute("SELECT w.id, w.name, w.created_utc, (SELECT COUNT(*) FROM events e WHERE e.world_id = w.id), w.save_version "
                                 "FROM worlds w ORDER BY w.created_utc DESC").fetchall()
        return [{"id": r[0], "name": r[1], "created_utc": r[2], "events": r[3], "save_version": r[4]} for r in rows]

    def replace_event(self, world_id: str, seq: int, event: Event) -> None:
        """Test/maintenance hook: overwrite one stored event body (used to simulate old or damaged saves)."""
        self.conn.execute("UPDATE events SET type = ?, body = ? WHERE world_id = ? AND seq = ?", (event.type, event.to_json(), world_id, seq))
        self.conn.commit()

    def append_events(self, world_id: str, events: List[Event]) -> None:
        self.conn.executemany(
            "INSERT INTO events (world_id, seq, event_id, type, sim_date, cause_id, portfolio_id, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(world_id, e.seq, e.id, e.type, e.sim_date, e.cause_id, e.portfolio_id, e.to_json()) for e in events])
        self.conn.commit()

    def load_events(self, world_id: str) -> List[Event]:
        rows = self.conn.execute("SELECT body FROM events WHERE world_id = ? ORDER BY seq", (world_id,)).fetchall()
        return [Event.from_json(r[0]) for r in rows]

    def delete_world(self, world_id: str) -> None:
        self.conn.execute("DELETE FROM events WHERE world_id = ?", (world_id,))
        self.conn.execute("DELETE FROM worlds WHERE id = ?", (world_id,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
