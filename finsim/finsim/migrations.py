"""Save migrations: pure functions over the event list, applied in order on load.

Each migration takes the events of a save at version N and returns them at version N+1. Migrations never
change economics: they add the keys newer handlers expect (with the values the old engine implied) and stamp the
new version on WORLD_CREATED. `migrate` returns the (possibly rewritten) events and a report.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from .domain.events import E, Event
from .version import SAVE_VERSION


def _rebuild(ev: Event, payload: Dict) -> Event:
    return Event(seq=ev.seq, id=ev.id, type=ev.type, sim_date=ev.sim_date, payload=payload, cause_id=ev.cause_id, portfolio_id=ev.portfolio_id)


def migrate_1_to_2(events: List[Event]) -> Tuple[List[Event], List[str]]:
    """v1 saves predate scenarios, the macro world and corporate events: stamp the defaults the v1 engine implied."""
    out, notes = [], []
    n_close = 0
    for ev in events:
        if ev.type == E.WORLD_CREATED:
            p = dict(ev.payload)
            p.setdefault("scenario", "NONE")
            p["save_version"] = 2
            out.append(_rebuild(ev, p))
            notes.append("WORLD_CREATED: scenario=NONE, save_version=2")
        elif ev.type == E.MARKET_CLOSE and ("macro" not in ev.payload or "corporate_events" not in ev.payload):
            p = dict(ev.payload)
            p.setdefault("macro", {})
            p.setdefault("corporate_events", [])
            out.append(_rebuild(ev, p))
            n_close += 1
        else:
            out.append(ev)
    if n_close:
        notes.append(f"MARKET_CLOSE: empty macro/corporate_events on {n_close} closes")
    return out, notes


MIGRATIONS: Dict[int, Callable[[List[Event]], Tuple[List[Event], List[str]]]] = {1: migrate_1_to_2}


def save_version_of(events: List[Event]) -> int:
    for ev in events:
        if ev.type == E.WORLD_CREATED:
            return int(ev.payload.get("save_version", 1))
    return SAVE_VERSION


def migrate(events: List[Event]) -> Tuple[List[Event], Dict]:
    v0 = save_version_of(events)
    v = v0
    notes: List[str] = []
    while v < SAVE_VERSION:
        fn = MIGRATIONS.get(v)
        if fn is None:
            raise RuntimeError(f"no migration from save version {v}")
        events, n = fn(events)
        notes.extend(n)
        v += 1
    if v > SAVE_VERSION:
        raise RuntimeError(f"save version {v} is newer than this engine's {SAVE_VERSION}")
    return events, {"from": v0, "to": v, "notes": notes, "migrated": v0 != v}
