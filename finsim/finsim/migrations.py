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


# v2 -> v3: the fictional universe became real companies and institutions. Old saves keep their stored prices and
# history; only identifiers and names are rewritten so the save still opens under the new universe.
RENAME_IDS = [("MRDN-29", "JPM-29"), ("PTRX-32", "F-32"), ("NGSL-30", "AAL-28"), ("ATLS-P", "BAC-PL"), ("SPXE", "SPY"), ("SPXI", "SPX"), ("SHRT", "SHV"),
              ("NVRA", "NVDA"), ("QNTM", "AMD"), ("CLDX", "ORCL"), ("MRDN", "JPM"), ("ATLS", "GS"), ("HLXB", "LLY"), ("VTRA", "UNH"), ("PTRX", "F"), ("NGSL", "AAL"),
              ("ORCH", "WMT"), ("BRWN", "PG"), ("LUXA", "NKE"), ("TITN", "CAT"), ("AERO", "BA"), ("GRDP", "NEE"), ("URBN", "PLD"), ("CPRX", "FCX"), ("TLNK", "VZ"),
              ("ZYNQ", "BYND"), ("PLSR", "RIVN"), ("KOBE", "TM"), ("NORDBANK", "BARCLAYS"), ("MERIDIAN-CUST", "BNYM-CUST"), ("MERIDIAN", "JPMORGAN"),
              ("HARBOR", "MORGAN_STANLEY"), ("KESTREL", "CITI"), ("VANTAGE", "DEUTSCHE"), ("ATLAS", "GOLDMAN")]
RENAME_NAMES = [("Meridian Custody Services", "BNY Mellon Asset Servicing"), ("Harbor Prime Brokerage", "Goldman Sachs Prime Brokerage"), ("Harbor Securities FX", "Citi FX"),
                ("Harbor Options Exchange", "Cboe Options Exchange"), ("Harbor Commodity Options", "CME (NYMEX/COMEX options)"), ("Harbor Securities", "Goldman Sachs"),
                ("Atlas Capital Markets", "Goldman Sachs"), ("Meridian Bank Derivatives", "J.P. Morgan"), ("Harbor Securities Swaps", "Morgan Stanley"),
                ("Kestrel Global Markets", "Citigroup"), ("Nordbank International", "Barclays"), ("Vantage Commodities & Credit", "Deutsche Bank"),
                ("Meridian Bancorp", "JPMorgan Chase"), ("Petrox Energy", "Ford Motor Credit"), ("Northgate Solar", "American Airlines"),
                ("Novara Systems", "NVIDIA Corporation"), ("Broad Market Equity ETF", "SPDR S&P 500 ETF Trust"), ("Short-Term Treasury ETF", "iShares Short Treasury Bond ETF")]


def migrate_2_to_3(events: List[Event]) -> Tuple[List[Event], List[str]]:
    import json as _json
    import re as _re
    # a ticker stands alone (NVRA), prefixes an option id (NVRA260220C300) or a bond/preferred id (handled by the longer keys first)
    pats = [(_re.compile(r"(?<![A-Za-z0-9_])" + _re.escape(a) + r"(?=\d{6}[CP]\d|(?![A-Za-z0-9_]))"), b) for a, b in RENAME_IDS]
    out, n = [], 0
    for ev in events:
        txt = _json.dumps(ev.payload, separators=(",", ":"), default=str)
        new = txt
        for a, b in RENAME_NAMES:
            new = new.replace(a, b)
        for pat, b in pats:
            new = pat.sub(b, new)
        if ev.type == E.WORLD_CREATED:
            p = _json.loads(new)
            p["save_version"] = 3
            out.append(_rebuild(ev, p))
            n += 1
        elif new != txt:
            out.append(_rebuild(ev, _json.loads(new)))
            n += 1
        else:
            out.append(ev)
    return out, [f"universe renamed to real companies and institutions on {n} events (prices and history unchanged)"]


MIGRATIONS: Dict[int, Callable[[List[Event]], Tuple[List[Event], List[str]]]] = {1: migrate_1_to_2, 2: migrate_2_to_3}


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
