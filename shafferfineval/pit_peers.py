"""Point-in-time peer cohorts -- the denominator under every Shaffer percentile.

A Shaffer factor score is never a number about a company on its own. It is a
number about a company RELATIVE TO ITS PEERS: growth in the 80th percentile of
what, leverage in the 20th percentile of what. The cohort is not a lookup
detail, it is half the model, and getting it wrong moves every factor by exactly
the amount the cohort moved without raising an error anywhere.

Three decisions live in this module. Each is a measured correction to an obvious
implementation that is quietly wrong.

1. CLASSIFICATION IS POINT-IN-TIME. The cohort key comes from
   `pit_store.sic_as_of` -- the SIC the issuer carried in the most recent filing
   it had made by the as-of date -- and never from the current company record.
   13.62% of the entities in this store changed their 4-digit SIC over their
   life, so today's classification silently re-labels one company in seven, and
   it re-labels each one toward what it later became. That is the future leaking
   into the cross-section through the industry key.

2. THE PEER UNIVERSE IS NOT THE SCORED UNIVERSE, and the distinction is
   load-bearing. A peer needs fundamentals and nothing else: no ticker, no
   price, no listing. That is precisely what makes the percentile
   survivorship-free -- an entity that is quarantined, dark, or has no
   resolvable symbol still shaped the distribution it belonged to, and dropping
   Enron from the 2001 cross-section would itself be survivorship bias. Only the
   SCORED universe receives a score, a label and a position. Both counts are
   written on every cohort row (`n_members`, `n_price_resolvable`), so the gap
   between them is a reported number and never a silent filter. It is a large
   gap: the symbol ceiling on the scored universe is 43.61% / 63.18% / 68.10% of
   peers at 2015-06-30 / 2019-06-28 / 2024-06-28, and pre-2019 that is an
   early-adopter artefact of dei:TradingSymbol tagging rather than a property of
   the market. A cohort restricted to those names would be a 2019 cohort wearing
   a 2015 date.

3. A COHORT TOO SMALL TO RANK IS REFUSED, NOT SHRUNK. MIN_COHORT_N = 12 is the
   floor and it is derived, not chosen for roundness: the valuation leg subsets
   its peers to the 50th-75th EBITDA percentile band, which holds about a
   quarter of a cohort, and `company_scoring.MIN_EBITDA_COHORT` = 3 has to
   survive that subsetting -- 12 * 0.25 = 3. Below 12 the cohort is unusable and
   every factor normalised against it is UNAVAILABLE with reason
   'insufficient_peers'. Between 12 and LOW_CONFIDENCE_N = 25 the percentile
   computes but is marked low confidence, because a 12-member cohort resolves to
   a grid of roughly 9 percentile points and one peer moves a company two bands.

THE RUNG LADDER. A cohort widens until it is usable and stops the moment it is:

    sic4  ->  sic3  ->  sic2  ->  SEC Office of Corporation Finance

and the rung ACTUALLY USED is recorded on the peer-set row, because a percentile
against 4-digit peers and a percentile against a whole SEC office are different
measurements and must never be pooled as if they were one. A wider rung is not a
failure; falling all the way through the office rung and still finding fewer
than 12 peers IS, and such an entity is reported as unscoreable rather than
scored against a cohort that cannot support a rank.

Note what the ladder does NOT do: it does not build a cohort out of whoever is
left over. The cohort at rung sic3 for key '283' is EVERY peer whose
point-in-time SIC starts with '283', including the ones that resolved their own
percentile at sic4. An issuer with a thin 4-digit code is ranked against the
full 3-digit population, which is the population it actually competes in.

WHY THE CROSSWALK IS HASHED. The SIC-to-office mapping and the rung ladder
together decide which companies are compared with which. Changing either
re-labels history -- same company, same date, same facts, different percentile
-- so the mapping cannot be an implementation detail that drifts silently
between runs. `crosswalk_hash()` is a SHA-256 over the canonical serialisation
of the actual mapping plus the ladder and the thresholds, it is stamped on every
peer-set row, and a build that meets rows carrying a different hash refuses
rather than mixing two mappings inside one as-of date.

The office crosswalk is the SEC's own, read on 2026-09-21 from
https://www.sec.gov/corpfin/division-of-corporation-finance-standard-industrial-classification-sic-code-list
-- 444 SIC codes across 14 office labels, covering all 435 distinct SIC codes
present in this store with no gaps. Three of those labels are disjunctions the
SEC publishes verbatim ('Office of Finance or Office of Crypto Assets' among
them); they are kept exactly as published rather than resolved by guess, and
they behave as ordinary cohort keys.

DISK. This module writes millions of membership rows into a 6.3 GiB store on a
volume with ~13 GiB free. Every date checks `shutil.disk_usage` before it writes
and aborts with a clear message rather than filling the disk, and the WAL is
checkpointed on a cadence with the RETURN VALUE READ -- `wal_checkpoint` returns
busy=1 and does nothing at all while another connection is reading, which is
exactly how an earlier ingest grew a 9.3 GB WAL with nothing looking wrong.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import pit_identity
import pit_store
import statlib

# --------------------------------------------------------------------------
# Versions and thresholds
# --------------------------------------------------------------------------

#: The cohort construction policy. Bump it (never edit it) to build a second
#: set of cohorts beside the first; both stay readable and comparable.
PEER_SET_VERSION = pit_store.PEER_SET_VERSION

RUNG_SIC4 = "sic4"
RUNG_SIC3 = "sic3"
RUNG_SIC2 = "sic2"
RUNG_OFFICE = "office"

#: Widening order. Narrowest first; the first usable rung wins.
RUNG_LADDER: tuple[str, ...] = (RUNG_SIC4, RUNG_SIC3, RUNG_SIC2, RUNG_OFFICE)

#: Fewer than this and the cohort cannot support a percentile at all.
#: 12 * 0.25 = 3 = company_scoring.MIN_EBITDA_COHORT, which is what sets the
#: floor: the valuation leg ranks inside the 50th-75th EBITDA band, and that
#: band holds about a quarter of the cohort it was drawn from.
MIN_COHORT_N = 12

#: 12-24 members computes, and says so quietly: a 12-member cohort is a
#: ~9-point percentile grid, where one peer moves a company two bands.
LOW_CONFIDENCE_N = 25

CONFIDENCE_NORMAL = "normal"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "unavailable"

#: The reason a factor carries when no rung produced a usable cohort. Shared
#: with pit_store so the feature writer and this module cannot drift apart.
REASON_NO_PEERS = pit_store.REASON_NO_PEERS

#: A different refusal, and the difference matters: the cohort this entity would
#: belong to EXISTS and is usable, but the entity was not in the peer universe
#: on that date (it had stopped filing, or had exited). Collapsing this into
#: 'insufficient_peers' would report a live company's missing cohort and a dead
#: company's absence as the same fact.
REASON_NOT_A_PEER = "not_in_peer_universe"

#: A SIC with no SEC office. Measured 2026-09-21: zero of the 435 distinct SIC
#: codes in this store land here, but a later filing can carry a code the
#: published list does not, and an unmapped code must be visible rather than
#: pooled with everything else that failed to map.
OFFICE_UNMAPPED = "unmapped"

#: Refuse to start a date's writes below this. The store is 6.3 GiB on a volume
#: that has been filled once already, and a build that runs the disk to zero
#: corrupts far more than this table.
MIN_FREE_BYTES = 2 * 1024 ** 3

#: Checkpoint cadence, in as-of dates.
CHECKPOINT_EVERY_DATES = 10

#: Other agents write to this store concurrently, so a lock is an expected
#: condition and not an error.
BUSY_TIMEOUT_MS = 60_000
LOCK_RETRIES = 5
LOCK_RETRY_SLEEP_S = 2.0

#: Status strings returned by build_peer_sets.
STATUS_WRITTEN = "written"
STATUS_EXISTS = "exists"
STATUS_REBUILT = "rebuilt"
STATUS_CROSSWALK_CONFLICT = "crosswalk_conflict"
STATUS_EMPTY_UNIVERSE = "empty_universe"


# --------------------------------------------------------------------------
# The SIC -> SEC office crosswalk (the widest rung)
#
# Published by the SEC's Division of Corporation Finance as the office that
# reviews filings in each SIC code; read 2026-09-21. Stored office -> codes
# because that is how the source is organised and how a human checks it. The
# SIC -> office direction used at runtime is inverted once, at import, with a
# duplicate check, so one code can never sit under two offices unnoticed.
# --------------------------------------------------------------------------

SEC_OFFICE_SOURCE = (
    "https://www.sec.gov/corpfin/division-of-corporation-finance-standard-"
    "industrial-classification-sic-code-list"
)
SEC_OFFICE_READ_ON = "2026-09-21"

OFFICE_SIC_CODES: dict[str, tuple[str, ...]] = {
    "Industrial Applications and Services": (
        "0100", "0200", "0700", "0800", "0900", "2800", "2810", "2820",
        "2821", "2840", "2842", "2844", "2851", "2860", "2870", "2890",
        "2891", "3080", "3081", "3086", "3089", "3821", "3822", "3823",
        "3824", "3825", "3826", "3827", "3829", "3841", "3842", "3843",
        "3844", "3845", "3851", "3861", "3873", "8000", "8011", "8050",
        "8051", "8060", "8062", "8071", "8082", "8090", "8093", "8300",
        "8731", "8734",
    ),
    "Multiple Offices": (
        "6770",
    ),
    "Office of Crypto Assets": (
        "6200", "6221",
    ),
    "Office of Energy & Transportation": (
        "1000", "1040", "1090", "1220", "1221", "1311", "1381", "1382",
        "1389", "1400", "2911", "2950", "2990", "3533", "4011", "4013",
        "4100", "4210", "4213", "4220", "4231", "4400", "4412", "4512",
        "4513", "4522", "4581", "4610", "4700", "4731", "4900", "4911",
        "4922", "4923", "4924", "4931", "4932", "4941", "4950", "4953",
        "4955", "4961", "4991", "6792", "6795",
    ),
    "Office of Finance": (
        "6021", "6022", "6029", "6035", "6036", "6099", "6111", "6141",
        "6153", "6159", "6162", "6163", "6172", "6282", "6311", "6321",
        "6324", "6331", "6351", "6361", "6399", "6411",
    ),
    "Office of Finance or Office of Crypto Assets": (
        "6199", "6211",
    ),
    "Office of International Corp Fin": (
        "8880", "8888", "9721",
    ),
    "Office of Life Sciences": (
        "2833", "2834", "2835", "2836",
    ),
    "Office of Manufacturing": (
        "2000", "2011", "2013", "2015", "2020", "2024", "2030", "2033",
        "2040", "2050", "2052", "2060", "2070", "2080", "2082", "2086",
        "2090", "2092", "2100", "2111", "2200", "2211", "2221", "2250",
        "2253", "2273", "2300", "2320", "2330", "2340", "2390", "2400",
        "2421", "2430", "2451", "2452", "2510", "2511", "2520", "2522",
        "2531", "2540", "2590", "2600", "2611", "2621", "2631", "2650",
        "2670", "2673", "2711", "2721", "2731", "2732", "2741", "2750",
        "2761", "2771", "2780", "2790", "3011", "3021", "3050", "3060",
        "3100", "3140", "3211", "3220", "3221", "3231", "3241", "3250",
        "3260", "3270", "3272", "3281", "3290", "3310", "3312", "3317",
        "3320", "3330", "3334", "3341", "3350", "3357", "3360", "3390",
        "3411", "3412", "3420", "3430", "3433", "3440", "3442", "3443",
        "3444", "3448", "3451", "3452", "3460", "3470", "3480", "3490",
        "3600", "3612", "3613", "3620", "3621", "3630", "3634", "3640",
        "3651", "3652", "3661", "3663", "3669", "3670", "3672", "3674",
        "3677", "3678", "3679", "3690", "3695", "3711", "3713", "3714",
        "3715", "3716", "3720", "3721", "3724", "3728", "3730", "3743",
        "3751", "3760", "3790", "3812", "3910", "3911", "3931", "3942",
        "3944", "3949", "3950", "3960", "3990",
    ),
    "Office of Real Estate & Construction": (
        "1520", "1531", "1540", "1600", "1623", "1700", "1731", "6500",
        "6510", "6512", "6513", "6519", "6531", "6532", "6552", "6794",
        "6798", "6799", "7000", "7011", "9995",
    ),
    "Office of Structured Finance": (
        "6189",
    ),
    "Office of Technology": (
        "3510", "3523", "3524", "3530", "3531", "3532", "3537", "3540",
        "3541", "3550", "3555", "3559", "3560", "3561", "3562", "3564",
        "3567", "3569", "3570", "3571", "3572", "3575", "3576", "3577",
        "3578", "3579", "3580", "3585", "3590", "4812", "4813", "4822",
        "4832", "4833", "4841", "4899", "7370", "7371", "7372", "7373",
        "7374",
    ),
    "Office of Trade & Services": (
        "5000", "5010", "5013", "5020", "5030", "5031", "5040", "5045",
        "5047", "5050", "5051", "5063", "5064", "5065", "5070", "5072",
        "5080", "5082", "5084", "5090", "5094", "5099", "5110", "5122",
        "5130", "5140", "5141", "5150", "5160", "5171", "5172", "5180",
        "5190", "5200", "5211", "5271", "5311", "5331", "5399", "5400",
        "5411", "5412", "5500", "5531", "5600", "5621", "5651", "5661",
        "5700", "5712", "5731", "5734", "5735", "5810", "5812", "5900",
        "5912", "5940", "5944", "5945", "5960", "5961", "5990", "7200",
        "7310", "7311", "7320", "7330", "7331", "7340", "7350", "7359",
        "7361", "7363", "7377", "7380", "7381", "7384", "7385", "7500",
        "7510", "7600", "7812", "7819", "7822", "7829", "7830", "7841",
        "7900", "7948", "7990", "7997", "8111", "8200", "8351", "8600",
        "8700", "8711", "8741", "8742", "8744", "8900",
    ),
    "Office of Trade & Services or Office of Energy & Transportation": (
        "7389",
    ),
}


def _invert_offices(office_codes: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """SIC -> office, refusing a code that two offices both claim.

    A duplicate would make the office rung depend on dict ordering, which is a
    silent re-labelling of every company in that code.
    """
    out: dict[str, str] = {}
    for office, codes in office_codes.items():
        for code in codes:
            key = str(code).zfill(4)
            if key in out and out[key] != office:
                raise ValueError(
                    f"SIC {key} is claimed by both {out[key]!r} and {office!r}")
            out[key] = office
    return out


#: The runtime direction: 4-digit SIC -> SEC office label.
SIC_OFFICE: dict[str, str] = _invert_offices(OFFICE_SIC_CODES)


# --------------------------------------------------------------------------
# Cohort keys and the crosswalk hash
# --------------------------------------------------------------------------

def normalize_sic(sic: Any) -> Optional[str]:
    """A 4-character SIC string, or None when this is not a SIC at all.

    The store holds SIC as TEXT and all 410,402 of its rows are already 4
    characters, leading zeros included ('0100' for agricultural crops). This
    exists so a caller whose SIC came from somewhere else -- an int, a
    zero-stripped API field -- cannot create a second spelling of one cohort.
    """
    if sic is None:
        return None
    text = str(sic).strip()
    if not text or not text.isdigit():
        return None
    return text.zfill(4)[:4]


def office_for(sic: Any, crosswalk: Optional[Mapping[str, str]] = None) -> str:
    """The SEC office reviewing this SIC, or OFFICE_UNMAPPED."""
    key = normalize_sic(sic)
    if key is None:
        return OFFICE_UNMAPPED
    table = SIC_OFFICE if crosswalk is None else crosswalk
    return table.get(key, OFFICE_UNMAPPED)


def cohort_key(rung: str, sic: Any,
               crosswalk: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The key this rung would file this SIC under, or None if it cannot.

    Keys are deliberately different shapes per rung ('2834', '283', '28',
    'Office of Life Sciences'), so a (rung, key) pair read back out of the
    database can never be mistaken for a pair from a different rung.
    """
    key = normalize_sic(sic)
    if key is None:
        return None
    if rung == RUNG_SIC4:
        return key
    if rung == RUNG_SIC3:
        return key[:3]
    if rung == RUNG_SIC2:
        return key[:2]
    if rung == RUNG_OFFICE:
        return office_for(key, crosswalk)
    raise ValueError(f"unknown rung {rung!r}; expected one of {RUNG_LADDER}")


def crosswalk_spec(crosswalk: Optional[Mapping[str, str]] = None,
                   rungs: Sequence[str] = RUNG_LADDER,
                   min_cohort_n: int = MIN_COHORT_N,
                   low_confidence_n: int = LOW_CONFIDENCE_N) -> dict[str, Any]:
    """Everything that decides which companies are compared with which.

    The SIC-to-office table, the widening order and both size thresholds change
    cohort membership or cohort usability, so all four are part of the mapping's
    identity and all four are hashed.
    """
    table = SIC_OFFICE if crosswalk is None else crosswalk
    return {
        "peer_set_version": PEER_SET_VERSION,
        "rung_ladder": list(rungs),
        "min_cohort_n": int(min_cohort_n),
        "low_confidence_n": int(low_confidence_n),
        "sic_office": {str(k).zfill(4): str(v) for k, v in sorted(table.items())},
    }


def crosswalk_hash(crosswalk: Optional[Mapping[str, str]] = None,
                   rungs: Sequence[str] = RUNG_LADDER,
                   min_cohort_n: int = MIN_COHORT_N,
                   low_confidence_n: int = LOW_CONFIDENCE_N) -> str:
    """SHA-256 of the real mapping: 'sha256:' plus 16 hex characters.

    Not a placeholder, and not a version string somebody has to remember to
    bump: move one SIC to another office, reorder the ladder or move a
    threshold, and this changes on the next run whether or not a constant was
    edited. Stamped on every peer-set row so that two different mappings can
    never be mixed inside one as-of date without the build noticing.
    """
    payload = json.dumps(
        crosswalk_spec(crosswalk, rungs, min_cohort_n, low_confidence_n),
        sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def cohort_confidence(n_members: Optional[int],
                      min_cohort_n: int = MIN_COHORT_N,
                      low_confidence_n: int = LOW_CONFIDENCE_N) -> str:
    """Confidence derived from size alone, so it cannot disagree with the row.

    A function rather than a stored column for exactly that reason: a column
    would be a second copy of a fact `n_members` already carries.
    """
    if n_members is None or int(n_members) < min_cohort_n:
        return CONFIDENCE_NONE
    return CONFIDENCE_LOW if int(n_members) < low_confidence_n else CONFIDENCE_NORMAL


# --------------------------------------------------------------------------
# Disk, WAL and lock discipline
#
# DISK IS THE BINDING CONSTRAINT on this machine, not CPU and not the SEC rate
# limit. These four helpers are why a build stops with a message instead of
# filling a volume that is already at 95%.
# --------------------------------------------------------------------------

def disk_status(path: str) -> dict[str, Any]:
    """Free/total bytes for the volume holding `path`, plus the live WAL size."""
    target = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    usage = shutil.disk_usage(target or ".")
    return {
        "free_bytes": int(usage.free),
        "total_bytes": int(usage.total),
        "used_pct": round(100.0 * usage.used / usage.total, 2) if usage.total else None,
        "db_bytes": os.path.getsize(path) if os.path.isfile(path) else None,
        "wal_bytes": wal_bytes(path),
    }


def wal_bytes(db_path: str) -> int:
    """Size of the write-ahead log beside `db_path`, 0 when there is none.

    Measured every checkpoint because the WAL, not the database file, is what
    grows without bound when a checkpoint is blocked.
    """
    wal = db_path + "-wal"
    try:
        return os.path.getsize(wal)
    except OSError:
        return 0


def require_free_space(db_path: str, need_bytes: int = MIN_FREE_BYTES) -> dict[str, Any]:
    """Abort BEFORE a bulk write when the volume is too full to take it.

    Raises RuntimeError with the actual numbers in the message. This is the
    guard that an earlier ingest did not have; it filled the disk instead.
    """
    status = disk_status(db_path)
    if status["free_bytes"] < int(need_bytes):
        raise RuntimeError(
            "REFUSING TO WRITE: %.2f GiB free on the volume holding %s, "
            "below the %.2f GiB floor (db %.2f GiB, WAL %.2f GiB, volume %.1f%% used). "
            "Free space or lower MIN_FREE_BYTES deliberately."
            % (status["free_bytes"] / 2 ** 30, db_path, need_bytes / 2 ** 30,
               (status["db_bytes"] or 0) / 2 ** 30, status["wal_bytes"] / 2 ** 30,
               status["used_pct"] or 0.0))
    return status


def checkpoint_wal(conn: sqlite3.Connection, db_path: Optional[str] = None
                   ) -> dict[str, Any]:
    """Truncate the WAL and REPORT WHETHER IT ACTUALLY HAPPENED.

    `PRAGMA wal_checkpoint(TRUNCATE)` returns (busy, log_frames, checkpointed):
    busy = 1 means another connection held a read lock and the checkpoint did
    NOTHING. Calling it and discarding that row is how a WAL reached 9.3 GB on
    this machine while the code looked like it was checkpointing on a cadence.
    Callers are expected to log `busy` and watch `wal_bytes`.
    """
    path = db_path or _db_path_of(conn)
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    busy, log_frames, checkpointed = (tuple(row) + (None, None, None))[:3] if row \
        else (None, None, None)
    return {
        "busy": int(busy) if busy is not None else None,
        "log_frames": int(log_frames) if log_frames is not None else None,
        "checkpointed_frames": int(checkpointed) if checkpointed is not None else None,
        "wal_bytes_after": wal_bytes(path) if path else None,
        "effective": bool(busy == 0),
    }


def _db_path_of(conn: sqlite3.Connection) -> Optional[str]:
    """The file backing the 'main' schema, or None for an in-memory store."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return row[2] or None
    return None


def prepare_connection(conn: sqlite3.Connection,
                       busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Make this connection safe to share a store with other agents.

    A busy timeout is not an optimisation here: another agent's transaction is
    an expected condition, and without the timeout it surfaces as an immediate
    'database is locked' in the middle of a 165-date build.
    """
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return conn


def _retry_on_lock(thunk: Callable[[], Any], attempts: int = LOCK_RETRIES,
                   sleep_s: float = LOCK_RETRY_SLEEP_S,
                   on_retry: Optional[Callable[[int, Exception], None]] = None) -> Any:
    """Run `thunk`, retrying only the lock errors another writer can cause.

    Any other OperationalError (a missing table, a bad column) is a bug and is
    raised immediately -- retrying it would just hide it for ten seconds.
    """
    last: Optional[Exception] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return thunk()
        except sqlite3.OperationalError as exc:
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            last = exc
            if on_retry is not None:
                on_retry(attempt, exc)
            if attempt < attempts:
                time.sleep(sleep_s * attempt)
    raise RuntimeError(f"gave up after {attempts} lock retries: {last}")


# --------------------------------------------------------------------------
# Classification and cohort assignment
# --------------------------------------------------------------------------

def classification_as_of(conn: sqlite3.Connection, entity_id: int,
                         as_of: str) -> Optional[str]:
    """The cohort classification for one entity on one date.

    Delegates to `pit_store.sic_as_of` deliberately, rather than reading any
    current-company field: this module and the fact selector must resolve the
    same SIC for the same (entity, date) or the cohort a company is ranked in
    stops matching the cohort its features were built from.
    """
    return normalize_sic(pit_store.sic_as_of(conn, int(entity_id), as_of))


def group_universe(members: Sequence[tuple[int, str]],
                   rungs: Sequence[str] = RUNG_LADDER,
                   crosswalk: Optional[Mapping[str, str]] = None
                   ) -> dict[str, dict[str, list[int]]]:
    """(entity_id, sic) pairs -> {rung: {key: [entity_id, ...]}}.

    Every member is grouped at EVERY rung, not only at the rung it ends up
    using, because a wider cohort contains the companies that resolved
    themselves at a finer one. Pure and database-free so the ladder can be
    tested without a store.
    """
    groups: dict[str, dict[str, list[int]]] = {rung: {} for rung in rungs}
    for entity_id, sic in members:
        for rung in rungs:
            key = cohort_key(rung, sic, crosswalk)
            if key is None:
                continue
            groups[rung].setdefault(key, []).append(int(entity_id))
    return groups


def assign_rung(groups: Mapping[str, Mapping[str, Sequence[int]]], sic: Any,
                rungs: Sequence[str] = RUNG_LADDER,
                min_cohort_n: int = MIN_COHORT_N,
                crosswalk: Optional[Mapping[str, str]] = None
                ) -> tuple[Optional[str], Optional[str], int]:
    """Walk the ladder and stop at the first usable cohort.

    Returns (rung, key, n_members); (None, None, n_at_widest) when even the
    widest rung is below `min_cohort_n` -- which is the honest answer
    'this entity cannot be percentile-scored on this date', not an error.
    """
    widest = 0
    for rung in rungs:
        key = cohort_key(rung, sic, crosswalk)
        if key is None:
            continue
        size = len(groups.get(rung, {}).get(key, ()))
        widest = max(widest, size)
        if size >= min_cohort_n:
            return rung, key, size
    return None, None, widest


# --------------------------------------------------------------------------
# Writing cohorts
# --------------------------------------------------------------------------

def _existing_hashes(conn: sqlite3.Connection, as_of: str, model_version: str,
                     peer_set_version: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT crosswalk_hash FROM pit_peer_set
            WHERE as_of_date = ? AND model_version = ? AND peer_set_version = ?""",
        (as_of, model_version, peer_set_version),
    ).fetchall()
    return [r[0] for r in rows]


def _peer_set_id(conn: sqlite3.Connection, as_of: str, rung: str, key: str,
                 model_version: str, peer_set_version: str) -> Optional[int]:
    row = conn.execute(
        """SELECT peer_set_id FROM pit_peer_set
            WHERE as_of_date = ? AND rung = ? AND key = ?
              AND model_version = ? AND peer_set_version = ?""",
        (as_of, rung, key, model_version, peer_set_version),
    ).fetchone()
    return int(row[0]) if row else None


def delete_peer_sets(conn: sqlite3.Connection, as_of: str, model_version: str,
                     peer_set_version: str = PEER_SET_VERSION) -> dict[str, Any]:
    """Remove one date's cohorts, refusing while anything still points at them.

    A peer_set_id is referenced by `pit_feature.peer_set_id`: deleting a cohort
    a feature was normalised against would leave a percentile whose denominator
    no longer exists, which is worse than a stale cohort. Re-running a corrected
    build means a new replay run, not a rewritten history.
    """
    referenced = conn.execute(
        """SELECT COUNT(*) FROM pit_feature
            WHERE peer_set_id IN (SELECT peer_set_id FROM pit_peer_set
                                   WHERE as_of_date = ? AND model_version = ?
                                     AND peer_set_version = ?)""",
        (as_of, model_version, peer_set_version),
    ).fetchone()[0]
    if referenced:
        return {"status": "refused", "reason": "features_reference_these_peer_sets",
                "n_features": int(referenced), "n_peer_sets": 0, "n_members": 0}
    with pit_store.transaction(conn):
        members = conn.execute(
            """DELETE FROM pit_peer_member
                WHERE peer_set_id IN (SELECT peer_set_id FROM pit_peer_set
                                       WHERE as_of_date = ? AND model_version = ?
                                         AND peer_set_version = ?)""",
            (as_of, model_version, peer_set_version),
        ).rowcount
        sets = conn.execute(
            """DELETE FROM pit_peer_set
                WHERE as_of_date = ? AND model_version = ? AND peer_set_version = ?""",
            (as_of, model_version, peer_set_version),
        ).rowcount
    return {"status": "deleted", "n_peer_sets": max(0, sets),
            "n_members": max(0, members)}


def build_peer_sets(conn: sqlite3.Connection, as_of: str,
                    model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                    *,
                    peer_set_version: str = PEER_SET_VERSION,
                    rungs: Sequence[str] = RUNG_LADDER,
                    min_cohort_n: int = MIN_COHORT_N,
                    low_confidence_n: int = LOW_CONFIDENCE_N,
                    crosswalk: Optional[Mapping[str, str]] = None,
                    max_filing_age_days: int = pit_identity.DEFAULT_PEER_MAX_FILING_AGE_DAYS,
                    persist: bool = True,
                    persist_unused: bool = False,
                    rebuild: bool = False,
                    need_free_bytes: int = MIN_FREE_BYTES,
                    verify_sample: int = 25) -> dict[str, Any]:
    """Snapshot every peer cohort for one as-of date. Idempotent per date.

    Membership comes from `pit_identity.peer_universe_as_of` -- CIK-keyed,
    survivorship-free, no ticker required -- and the classification from
    `pit_store.sic_as_of`, the SIC as it stood on that date in the issuer's own
    filing. Each entity is then assigned the narrowest rung whose cohort holds
    at least `min_cohort_n` members.

    Persisted by default: only the cohorts that at least one entity was ASSIGNED
    to, with their FULL membership. A cohort nobody used is a group that exists
    in the classification but was never a denominator, and writing all four
    rungs for every date would quadruple a 3.3-million-row table to store
    denominators no percentile was ever taken against. Pass persist_unused=True
    to keep them anyway (and read the disk guard's message when it stops you).

    `n_members` and `n_price_resolvable` are recorded separately on every row:
    the first is the cohort that shapes the percentile, the second is how much
    of it could also be priced, and they are very different numbers before 2019.

    Returns a status dict; 'status' is one of written / exists / rebuilt /
    crosswalk_conflict / empty_universe. Re-running a date that is already
    built does no work and writes nothing.
    """
    started = time.time()
    day = str(as_of)[:10]
    expected_hash = crosswalk_hash(crosswalk, rungs, min_cohort_n, low_confidence_n)
    db_path = _db_path_of(conn)

    existing = _existing_hashes(conn, day, model_version, peer_set_version)
    # The skip is for WRITES. A measurement run writes nothing, so it is allowed
    # to re-measure a date that is already built -- which is how the stored
    # cohorts get audited against a fresh computation.
    if existing and not rebuild and persist:
        if any(h != expected_hash for h in existing):
            return {"as_of": day, "status": STATUS_CROSSWALK_CONFLICT,
                    "crosswalk_hash": expected_hash, "stored_hashes": existing,
                    "note": ("this date was built under a different SIC-to-cohort "
                             "mapping; rebuild it or bump peer_set_version rather "
                             "than mixing the two")}
        counts = conn.execute(
            """SELECT COUNT(*) AS n_sets, COALESCE(SUM(n_members), 0) AS n_members
                 FROM pit_peer_set
                WHERE as_of_date = ? AND model_version = ? AND peer_set_version = ?""",
            (day, model_version, peer_set_version),
        ).fetchone()
        return {"as_of": day, "status": STATUS_EXISTS,
                "crosswalk_hash": expected_hash,
                "n_cohorts": int(counts["n_sets"]),
                "n_member_slots": int(counts["n_members"]),
                "elapsed_s": round(time.time() - started, 3)}

    # ---- disk, before any work at all --------------------------------------
    # Checked here rather than beside the INSERT so a full volume costs a
    # millisecond instead of a cross-section query, and so the run stops on the
    # first date it cannot afford rather than the last one it can.
    disk_before: dict[str, Any] = {}
    if persist and db_path:
        disk_before = require_free_space(db_path, need_free_bytes)

    # ---- membership and classification -------------------------------------
    universe = pit_identity.peer_universe_as_of(conn, day, max_filing_age_days)
    members: list[tuple[int, str]] = []
    n_no_sic = 0
    for row in universe:
        entity_id = int(row["entity_id"])
        sic = classification_as_of(conn, entity_id, day)
        if sic is None:
            n_no_sic += 1
            continue
        members.append((entity_id, sic))

    if not members:
        return {"as_of": day, "status": STATUS_EMPTY_UNIVERSE,
                "n_universe": len(universe), "n_no_sic": n_no_sic,
                "crosswalk_hash": expected_hash,
                "elapsed_s": round(time.time() - started, 3)}

    # The universe query resolves its own SIC with a subquery that mirrors
    # sic_as_of; a sample is re-read through sic_as_of itself so a future edit
    # to either cannot drift the two apart unnoticed.
    sic_by_entity = dict(members)
    disagreements = 0
    step = max(1, len(universe) // max(1, verify_sample))
    for row in universe[::step]:
        mine = sic_by_entity.get(int(row["entity_id"]))
        theirs = normalize_sic(row["sic"])
        if mine is not None and theirs is not None and mine != theirs:
            disagreements += 1

    # ---- the ladder ---------------------------------------------------------
    groups = group_universe(members, rungs, crosswalk)
    assignments: dict[int, tuple[Optional[str], Optional[str]]] = {}
    assigned_by_rung: dict[str, int] = {rung: 0 for rung in rungs}
    unusable: list[int] = []
    for entity_id, sic in members:
        rung, key, _size = assign_rung(groups, sic, rungs, min_cohort_n, crosswalk)
        assignments[entity_id] = (rung, key)
        if rung is None:
            unusable.append(entity_id)
        else:
            assigned_by_rung[rung] += 1

    used_cohorts = {pair for pair in assignments.values() if pair[0] is not None}
    if persist_unused:
        used_cohorts |= {(rung, key) for rung in rungs for key in groups.get(rung, {})}

    # ---- the priced subset --------------------------------------------------
    # The scored universe is a strict subset and is reported, never filtered on.
    priced = {int(r["entity_id"]) for r in
              pit_identity.scored_universe_as_of(conn, day,
                                                 max_filing_age_days=max_filing_age_days)}

    summary = _measure(groups, assignments, assigned_by_rung, unusable, members,
                       priced, rungs, min_cohort_n, low_confidence_n)
    summary.update({
        "as_of": day, "crosswalk_hash": expected_hash,
        "model_version": model_version, "peer_set_version": peer_set_version,
        "n_universe": len(universe), "n_classified": len(members),
        "n_no_sic": n_no_sic, "n_price_resolvable_universe": len(priced),
        "sic_as_of_disagreements": disagreements,
        "n_cohorts_used": len(used_cohorts),
    })

    if not persist:
        summary["status"] = "measured"
        summary["elapsed_s"] = round(time.time() - started, 3)
        return summary

    # ---- write --------------------------------------------------------------
    if disk_before:
        summary["free_bytes_before"] = disk_before["free_bytes"]
        summary["wal_bytes_before"] = disk_before["wal_bytes"]

    rebuilt = False
    if existing and rebuild:
        removed = delete_peer_sets(conn, day, model_version, peer_set_version)
        if removed["status"] == "refused":
            summary["status"] = "refused"
            summary["note"] = removed["reason"]
            summary["n_features_referencing"] = removed["n_features"]
            return summary
        rebuilt = True

    n_sets = 0
    n_member_rows = 0

    def _write() -> tuple[int, int]:
        sets = 0
        member_rows = 0
        with pit_store.transaction(conn):
            for rung, key in sorted(used_cohorts):
                cohort = groups.get(rung, {}).get(key, [])
                conn.execute(
                    """INSERT INTO pit_peer_set
                           (as_of_date, rung, key, n_members, n_price_resolvable,
                            peer_set_version, crosswalk_hash, model_version, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (as_of_date, rung, key, model_version,
                                    peer_set_version) DO NOTHING""",
                    (day, rung, key, len(cohort),
                     sum(1 for e in cohort if e in priced),
                     peer_set_version, expected_hash, model_version, pit_store._now()),
                )
                peer_set_id = _peer_set_id(conn, day, rung, key, model_version,
                                           peer_set_version)
                if peer_set_id is None:      # pragma: no cover - insert just ran
                    raise RuntimeError(f"peer set vanished after insert: {rung}/{key}")
                sets += 1
                cursor = conn.executemany(
                    """INSERT INTO pit_peer_member (peer_set_id, entity_id)
                       VALUES (?, ?)
                       ON CONFLICT (peer_set_id, entity_id) DO NOTHING""",
                    [(peer_set_id, entity_id) for entity_id in cohort],
                )
                member_rows += max(0, cursor.rowcount or 0)
        return sets, member_rows

    n_sets, n_member_rows = _retry_on_lock(_write)

    summary["status"] = STATUS_REBUILT if rebuilt else STATUS_WRITTEN
    summary["n_cohorts_written"] = n_sets
    summary["n_member_rows_written"] = n_member_rows

    # PROVENANCE, so a cohort built before prices existed is DETECTABLE.
    # `created_at` records when a cohort was written and says nothing about what
    # the store held at that moment: the 165 cross-sections of 2026-09-21 were
    # written while pit_listing was still filling, and their priced count was a
    # photograph of a half-loaded table. The build row stamps the listing census
    # that produced `n_price_resolvable`, which is what
    # `pit_peer_meta.assert_peer_sets_fresh()` compares against before the
    # feature build. Imported locally because pit_peer_meta imports this module,
    # and optionally so a checkout without it still builds cohorts.
    try:
        import pit_peer_meta
    except ImportError:                     # pragma: no cover - optional guard
        summary["build_provenance"] = "unavailable: pit_peer_meta not importable"
    else:
        pit_peer_meta.ensure_schema(conn)
        pit_peer_meta.record_peer_set_build(
            conn, day, model_version, peer_set_version, len(priced),
            crosswalk_hash=expected_hash)
        summary["build_provenance"] = "recorded"

    if db_path:
        after = disk_status(db_path)
        summary["free_bytes_after"] = after["free_bytes"]
        summary["wal_bytes_after"] = after["wal_bytes"]
    summary["elapsed_s"] = round(time.time() - started, 3)
    return summary


def _measure(groups: Mapping[str, Mapping[str, Sequence[int]]],
             assignments: Mapping[int, tuple[Optional[str], Optional[str]]],
             assigned_by_rung: Mapping[str, int],
             unusable: Sequence[int],
             members: Sequence[tuple[int, str]],
             priced: set[int],
             rungs: Sequence[str],
             min_cohort_n: int,
             low_confidence_n: int) -> dict[str, Any]:
    """The numbers this build exists to produce, computed once per date.

    Two different questions are answered per rung and are easy to confuse:
      * usable_at_rung   -- would THIS rung alone have given the entity a
                            cohort of at least min_cohort_n?
      * assigned_at_rung -- is this the rung the ladder actually stopped at?
    The first is a property of the classification, the second of the ladder.
    """
    n = len(members)
    per_rung: dict[str, Any] = {}
    for rung in rungs:
        sizes = sorted(len(v) for v in groups.get(rung, {}).values())
        usable_members = sum(size for size in sizes if size >= min_cohort_n)
        per_rung[rung] = {
            "n_groups": len(sizes),
            "n_usable_groups": sum(1 for size in sizes if size >= min_cohort_n),
            "n_low_confidence_groups": sum(
                1 for size in sizes if min_cohort_n <= size < low_confidence_n),
            "min": sizes[0] if sizes else None,
            "p25": statlib.percentile(sizes, 0.25) if sizes else None,
            "median": statlib.percentile(sizes, 0.50) if sizes else None,
            "p75": statlib.percentile(sizes, 0.75) if sizes else None,
            "max": sizes[-1] if sizes else None,
            "usable_at_rung": usable_members,
            "usable_at_rung_pct": round(100.0 * usable_members / n, 2) if n else None,
            "assigned_at_rung": int(assigned_by_rung.get(rung, 0)),
            "assigned_at_rung_pct": (round(100.0 * assigned_by_rung.get(rung, 0) / n, 2)
                                     if n else None),
        }

    assigned_sizes = sorted(
        len(groups[rung][key]) for rung, key in assignments.values() if rung is not None)
    return {
        "per_rung": per_rung,
        "n_unusable": len(unusable),
        "unusable_pct": round(100.0 * len(unusable) / n, 2) if n else None,
        "unusable_entity_ids": sorted(unusable)[:50],
        "n_low_confidence_entities": sum(1 for size in assigned_sizes
                                         if size < low_confidence_n),
        "assigned_cohort_size_median": (statlib.percentile(assigned_sizes, 0.50)
                                        if assigned_sizes else None),
        "n_priced_in_universe": len(priced),
        "priced_pct": round(100.0 * len(priced) / n, 2) if n else None,
    }


def build_peer_sets_range(conn: sqlite3.Connection, dates: Sequence[str],
                          model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                          *,
                          checkpoint_every: int = CHECKPOINT_EVERY_DATES,
                          progress: bool = True,
                          **kwargs: Any) -> dict[str, Any]:
    """Build a whole grid, checkpointing the WAL and watching the disk.

    Peak free-space and peak WAL are MEASURED across the run and returned,
    because 'it fitted' is a claim that needs a number behind it on a volume
    that is 95% full. Every checkpoint's busy flag is counted: a run whose
    checkpoints were all busy did not actually truncate anything.
    """
    db_path = _db_path_of(conn)
    results: list[dict[str, Any]] = []
    peak_wal = wal_bytes(db_path) if db_path else 0
    min_free = disk_status(db_path)["free_bytes"] if db_path else None
    busy_checkpoints = 0
    checkpoints = 0
    started = time.time()

    for index, day in enumerate(dates, start=1):
        result = build_peer_sets(conn, day, model_version, **kwargs)
        results.append(result)
        if progress:
            print(f"  {day}  {result.get('status'):<10} "
                  f"universe={result.get('n_universe', '-'):>6} "
                  f"cohorts={result.get('n_cohorts_written', result.get('n_cohorts', 0)):>5} "
                  f"members={result.get('n_member_rows_written', 0):>7} "
                  f"unusable={result.get('n_unusable', '-'):>4} "
                  f"{result.get('elapsed_s', 0):.1f}s", flush=True)
        if db_path:
            status = disk_status(db_path)
            peak_wal = max(peak_wal, status["wal_bytes"])
            min_free = min(min_free, status["free_bytes"]) if min_free else status["free_bytes"]
        if checkpoint_every and index % checkpoint_every == 0:
            checkpoint = checkpoint_wal(conn, db_path)
            checkpoints += 1
            if not checkpoint["effective"]:
                busy_checkpoints += 1
                if progress:
                    print(f"    WAL checkpoint BUSY (did nothing): "
                          f"{checkpoint['wal_bytes_after'] / 2 ** 20:.1f} MiB WAL",
                          flush=True)

    final = checkpoint_wal(conn, db_path) if db_path else {}
    return {
        "n_dates": len(dates),
        "results": results,
        "status_counts": _count_statuses(results),
        "peak_wal_bytes": peak_wal,
        "min_free_bytes": min_free,
        "checkpoints": checkpoints,
        "busy_checkpoints": busy_checkpoints,
        "final_checkpoint": final,
        "elapsed_s": round(time.time() - started, 2),
    }


def _count_statuses(results: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for result in results:
        key = str(result.get("status"))
        out[key] = out.get(key, 0) + 1
    return out


# --------------------------------------------------------------------------
# Reading cohorts back -- what the feature writer calls
# --------------------------------------------------------------------------

def peer_set_for_entity(conn: sqlite3.Connection, entity_id: int, as_of: str,
                        model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                        *,
                        peer_set_version: str = PEER_SET_VERSION,
                        rungs: Sequence[str] = RUNG_LADDER,
                        min_cohort_n: int = MIN_COHORT_N,
                        low_confidence_n: int = LOW_CONFIDENCE_N,
                        crosswalk: Optional[Mapping[str, str]] = None
                        ) -> dict[str, Any]:
    """The cohort this entity is ranked in, or an explicit refusal.

    Walks the same ladder the build walked, from the same point-in-time SIC, and
    returns the first stored cohort that is both usable and actually contains
    the entity. On failure it returns availability 'unavailable' with a reason
    the caller writes straight onto the feature row rather than inventing a
    percentile against three companies -- 'insufficient_peers' when no rung
    reached the floor, and 'not_in_peer_universe' when a usable cohort for this
    classification exists but the entity was not a live filer on that date.
    """
    day = str(as_of)[:10]
    sic = classification_as_of(conn, int(entity_id), day)
    if sic is None:
        return {"availability": pit_store.AVAIL_UNAVAILABLE,
                "reason": REASON_NO_PEERS, "sic": None, "peer_set_id": None,
                "rung": None, "key": None, "n_members": 0,
                "confidence": CONFIDENCE_NONE,
                "note": "no SIC had been filed by this date"}
    usable_cohort_exists = False
    for rung in rungs:
        key = cohort_key(rung, sic, crosswalk)
        if key is None:
            continue
        row = conn.execute(
            """SELECT * FROM pit_peer_set
                WHERE as_of_date = ? AND rung = ? AND key = ?
                  AND model_version = ? AND peer_set_version = ?""",
            (day, rung, key, model_version, peer_set_version),
        ).fetchone()
        if row is None or int(row["n_members"]) < min_cohort_n:
            continue
        usable_cohort_exists = True
        member = conn.execute(
            "SELECT 1 FROM pit_peer_member WHERE peer_set_id = ? AND entity_id = ?",
            (int(row["peer_set_id"]), int(entity_id)),
        ).fetchone()
        if member is None:
            continue
        return {"availability": pit_store.AVAIL_COMPLETE, "reason": None,
                "sic": sic, "peer_set_id": int(row["peer_set_id"]),
                "rung": rung, "key": key,
                "n_members": int(row["n_members"]),
                "n_price_resolvable": int(row["n_price_resolvable"]),
                "crosswalk_hash": row["crosswalk_hash"],
                "confidence": cohort_confidence(int(row["n_members"]), min_cohort_n,
                                                low_confidence_n)}
    return {"availability": pit_store.AVAIL_UNAVAILABLE,
            "reason": REASON_NOT_A_PEER if usable_cohort_exists else REASON_NO_PEERS,
            "sic": sic, "peer_set_id": None, "rung": None, "key": None,
            "n_members": 0, "confidence": CONFIDENCE_NONE,
            "note": (f"a usable cohort exists for SIC {sic} on {day} but this "
                     "entity was not in the peer universe then"
                     if usable_cohort_exists
                     else f"no rung in {tuple(rungs)} reached {min_cohort_n} peers")}


def peer_members(conn: sqlite3.Connection, peer_set_id: int) -> list[int]:
    """Entity ids in one cohort, ordered, so a percentile is reproducible."""
    return [int(r[0]) for r in conn.execute(
        "SELECT entity_id FROM pit_peer_member WHERE peer_set_id = ? ORDER BY entity_id",
        (int(peer_set_id),))]


def peer_set_coverage(conn: sqlite3.Connection,
                      model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                      peer_set_version: str = PEER_SET_VERSION) -> dict[str, Any]:
    """What is in the store now: dates, cohorts, members and the rung mix."""
    row = conn.execute(
        """SELECT COUNT(*) AS n_sets, COUNT(DISTINCT as_of_date) AS n_dates,
                  MIN(as_of_date) AS first_date, MAX(as_of_date) AS last_date,
                  COUNT(DISTINCT crosswalk_hash) AS n_hashes
             FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?""",
        (model_version, peer_set_version),
    ).fetchone()
    by_rung = conn.execute(
        """SELECT rung, COUNT(*) AS n_sets, SUM(n_members) AS n_member_slots,
                  SUM(n_price_resolvable) AS n_priced
             FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
            GROUP BY rung ORDER BY rung""",
        (model_version, peer_set_version),
    ).fetchall()
    members = conn.execute("SELECT COUNT(*) FROM pit_peer_member").fetchone()[0]
    return {
        "n_peer_sets": int(row["n_sets"]), "n_dates": int(row["n_dates"]),
        "first_date": row["first_date"], "last_date": row["last_date"],
        "n_distinct_crosswalk_hashes": int(row["n_hashes"]),
        "n_member_rows": int(members),
        "by_rung": {r["rung"]: {"n_sets": int(r["n_sets"]),
                                "n_member_slots": int(r["n_member_slots"] or 0),
                                "n_priced": int(r["n_priced"] or 0)}
                    for r in by_rung},
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _aggregate(results: Sequence[Mapping[str, Any]],
               rungs: Sequence[str] = RUNG_LADDER) -> dict[str, Any]:
    """Pool per-date measurements into the headline numbers across the grid."""
    built = [r for r in results if r.get("per_rung")]
    if not built:
        return {}
    total = sum(int(r["n_classified"]) for r in built)
    out: dict[str, Any] = {"n_dates": len(built), "n_entity_dates": total}
    for rung in rungs:
        assigned = sum(int(r["per_rung"][rung]["assigned_at_rung"]) for r in built)
        usable = sum(int(r["per_rung"][rung]["usable_at_rung"]) for r in built)
        sizes = [r["per_rung"][rung]["median"] for r in built
                 if r["per_rung"][rung]["median"] is not None]
        out[rung] = {
            "assigned": assigned,
            "assigned_pct": round(100.0 * assigned / total, 2) if total else None,
            "usable_at_rung_pct": round(100.0 * usable / total, 2) if total else None,
            "median_of_median_cohort_size": (statlib.percentile(sorted(sizes), 0.50)
                                             if sizes else None),
        }
    unusable = sum(int(r["n_unusable"]) for r in built)
    low = sum(int(r["n_low_confidence_entities"]) for r in built)
    out["unusable"] = unusable
    out["unusable_pct"] = round(100.0 * unusable / total, 2) if total else None
    out["low_confidence"] = low
    out["low_confidence_pct"] = round(100.0 * low / total, 2) if total else None
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Build point-in-time peer cohorts over the month-end grid.

        python pit_peers.py --from 2013-01-01                # build and measure
        python pit_peers.py --from 2013-01-01 --dry-run      # measure only
        python pit_peers.py --coverage                       # what is stored

    Idempotent: a date already built is skipped, so an interrupted run resumes
    simply by being run again.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    start, end = "2013-01-01", "2100-01-01"
    model_version = pit_store.EQUITY_PIT_MODEL_VERSION
    limit = None
    dry_run = False
    coverage_only = False
    rebuild = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--db" and index + 1 < len(argv):
            db_path = argv[index + 1]; index += 2
        elif token == "--from" and index + 1 < len(argv):
            start = argv[index + 1]; index += 2
        elif token == "--to" and index + 1 < len(argv):
            end = argv[index + 1]; index += 2
        elif token == "--limit" and index + 1 < len(argv):
            limit = int(argv[index + 1]); index += 2
        elif token == "--model" and index + 1 < len(argv):
            model_version = argv[index + 1]; index += 2
        elif token == "--dry-run":
            dry_run = True; index += 1
        elif token == "--rebuild":
            rebuild = True; index += 1
        elif token == "--coverage":
            coverage_only = True; index += 1
        else:
            print(f"unknown argument {token!r}")
            print(main.__doc__)
            return 2

    conn = prepare_connection(pit_store.init_db(db_path))
    print(f"PIT store: {db_path}")
    print(f"crosswalk: {crosswalk_hash()}  "
          f"({len(SIC_OFFICE)} SIC codes -> {len(OFFICE_SIC_CODES)} SEC offices, "
          f"read {SEC_OFFICE_READ_ON})")
    status = disk_status(db_path)
    print(f"disk: {status['free_bytes'] / 2 ** 30:.2f} GiB free, "
          f"db {(status['db_bytes'] or 0) / 2 ** 30:.2f} GiB, "
          f"WAL {status['wal_bytes'] / 2 ** 20:.1f} MiB, "
          f"volume {status['used_pct']}% used")

    if coverage_only:
        print(json.dumps(peer_set_coverage(conn, model_version), indent=2, default=str))
        return 0

    dates = pit_store.month_end_sessions(conn, start, end)
    if limit:
        dates = dates[:limit]
    print(f"grid: {len(dates)} month-end sessions "
          f"{dates[0] if dates else '-'} .. {dates[-1] if dates else '-'}")
    if not dates:
        return 1

    report = build_peer_sets_range(conn, dates, model_version,
                                   persist=not dry_run, rebuild=rebuild)
    print()
    print("status:", report["status_counts"])
    print(f"peak WAL: {report['peak_wal_bytes'] / 2 ** 20:.1f} MiB   "
          f"min free: {(report['min_free_bytes'] or 0) / 2 ** 30:.2f} GiB   "
          f"checkpoints: {report['checkpoints']} "
          f"({report['busy_checkpoints']} busy)   "
          f"{report['elapsed_s']:.0f}s")
    print()
    print(json.dumps(_aggregate(report["results"]), indent=2, default=str))
    print()
    print(json.dumps(peer_set_coverage(conn, model_version), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
