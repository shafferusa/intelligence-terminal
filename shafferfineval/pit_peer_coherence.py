"""IS THIS A PEER GROUP? -- the second test, measured. Read-only, stdlib only.

`pit_factor_spec` now gates peer selection on TWO INDEPENDENT tests: SUFFICIENT
N (enough members to compute a percentile) and ECONOMIC COHERENCE (the members
are actually comparable). This module is the evidence for the second one, and
the price tag on it.

THE FINDING THAT FORCES A SECOND TEST. Over the 39,038 peer sets in this store,
the SEC Office rung clears twelve members 94.5% of the time -- and does it with
cohorts of 542 to 1,310 issuers whose only shared property is the SEC
Corporation Finance office that reviews their filings. At sic4, 57.4% of all
peer sets, twelve members is cleared 2.4% of the time. FEASIBILITY AND
MEANINGFULNESS MOVE IN OPPOSITE DIRECTIONS ALONG THE SIC LADDER, so a single
threshold on N can only trade one for the other silently.

WHAT IS MEASURED HERE

  (1) COHERENCE AS A NUMBER, per rung, so "coherence" stops being the name of a
      rung. Two statistics, chosen because neither is a restatement of N:

        effective_sic4_count   1 / SUM(share^2) over the distinct SIC4 codes in
                               the cohort -- the effective number of DIFFERENT
                               four-digit industries it is made of. Scale-free:
                               doubling a cohort with more of the same industry
                               does not move it. Degenerate at sic4 (1.00 by
                               construction) and reported as such.
        margin_iqr             p75 - p25 of the EBITDA margin across the
                               members whose EBITDA and revenue resolve at a
                               MATCHED period. An interquartile spread is a
                               CONSISTENT estimator of the population spread --
                               it does not grow with the member count -- which
                               is exactly what makes it evidence about the
                               population and not about the cohort's size.

      A third candidate -- cohort size relative to the rung above it -- was
      REJECTED: it is a function of member counts alone, so it could only
      restate the thing the second test exists to be independent of.

  (2) THE SIZE-MATCHED CONTROL, which is the part that makes (1) admissible.
      Office cohorts run 542-1,310 members and sic2 cohorts run up to 1,227, so
      the two ranges OVERLAP and the measures can be compared at equal size. If
      a statistic separates office from sic2 among cohorts of the same member
      count, it is measuring composition and not N.

  (3) THE COST OF THE BAN, by year and by SIC division: how many entity-dates
      lose a valuation peer set entirely because the office rung is no longer
      admissible. That number is the price of the rule and the owner sees it
      before the rule is locked.

WHAT IT FOUND (run 2026-09-21: 39,038 peer sets, 165 month-end dates
2013-01-31..2026-09-18, 1,238,663 entity-dates, 116.7s, peak working set
298.4 MiB, nothing written).

  COHERENCE, median by rung          sic4    sic3    sic2  office   verdict
    effective_sic4_count             1.00    1.88    3.37   18.01   ADOPTED
    top_sic4_share                   1.00    0.67    0.44    0.16   ADOPTED
    margin_iqr                       0.35    0.21    0.16    0.34   REJECTED
    log10_ebitda_span                2.07    2.29    2.25    2.47   REJECTED

  THE TWO REJECTIONS ARE THE POINT. Both dispersion candidates separate the
  rungs over all peer sets and STOP separating them at equal member count: at
  542+ members the office margin IQR is 0.342 against sic2's 1.427 -- the
  office cohort is TIGHTER. Pooling twenty industries pulls the quartiles
  toward the aggregate centre, so a dispersion statistic measures a
  population's variance rather than whether it is one population. Composition
  survives (18.01 against 3.285 at equal size), because industry mix cannot be
  made homogeneous by adding members.

  THE COST: 57,278 of 1,238,663 entity-dates (4.62%) lose a valuation peer
  set -- 10,156 because no narrower rung forms one at all, 47,122 because the
  narrower rungs that do exist are too thin where the office rung was not. By
  division the burden is wildly uneven: Agriculture 66.3%, Public
  Administration 26.0%, Construction 21.1%, Retail 20.4%, Mining 17.8%,
  Manufacturing 2.7%, and Finance and Wholesale exactly 0.00%.

SAMPLE SCOPE. Everything here is FULL_REPORTING_UNIVERSE. The peer sets, the
SIC classifications and the EBITDA and revenue values all come from `pit_fact`
and `pit_entity_sic`, which are CIK-keyed and include issuers that are long
dead. NOTHING in this module touches `pit_listing`, a price or a share count,
so no table it prints needs the SURVIVOR_ONLY_DIAGNOSTIC label -- and the one
place a priced quantity would have been needed is stated as an upper bound
rather than silently substituted (see `cost_of_the_ban`).

DISK. The store is 8.35 GiB on a volume that has been filled once already by a
9.3 GB WAL. This module opens the database `mode=ro`, writes nothing, checks
`shutil.disk_usage` before it starts and refuses to run below the floor, and
samples free space and WAL size throughout so the run can report what it
actually cost. An unmeasured quantity is reported as UNKNOWN, never as 0.

    python pit_peer_coherence.py [--out DIR] [--limit-dates N] [--no-validate]
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
import time
from typing import Any, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_factor_spec as FS
import pit_policy
import pit_store
import statlib

UNKNOWN = "UNKNOWN"

#: Refuse to start below this much free space. Nothing here writes to the
#: store, but the report, the JSON and any SQLite temp file do land on the same
#: volume, and a run that begins on a full disk fails in the least useful place.
MIN_FREE_BYTES = 2 * 1024 ** 3

#: Other agents write to this store concurrently; a lock is expected, not an error.
BUSY_TIMEOUT_MS = 60_000

#: The ladder version every selector here resolves under, matching
#: `pit_factor_spec.LADDER`: v2, because v1's total_debt ladder hides its
#: best-covered tag inside a composite rung.
LADDER = FS.LADDER

#: Duration facts at ANNUAL cadence, as everywhere else in this lineage.
ANNUAL_QTRS = FS.ANNUAL_QTRS

#: The minimum members carrying a margin before a cohort's dispersion is
#: reported. Below this the IQR is an artefact of four numbers, and a cohort
#: with no revenue coverage is a COVERAGE fact, not an incoherent peer group.
MIN_MARGIN_MEMBERS = 12

#: The N a valuation cohort must reach. `ev_ebitda`'s `min_peer_count`, read
#: off the spec rather than restated, so a changed threshold changes this too.
VALUATION_MIN_N = FS.spec("ev_ebitda").min_peer_count

PERCENTILE_POINTS = (10, 25, 50, 75, 90)

#: SIC divisions by two-digit major group -- the SEC's own list, restated from
#: `pit_cohort_measure` so this module imports no census code to be read as a
#: specification. The two are asserted equal by `test_pit_peer_coherence`.
SIC_DIVISIONS: tuple[tuple[int, int, str], ...] = (
    (1, 9, "A Agriculture, Forestry & Fishing"),
    (10, 14, "B Mining"),
    (15, 17, "C Construction"),
    (20, 39, "D Manufacturing"),
    (40, 49, "E Transport, Comms & Utilities"),
    (50, 51, "F Wholesale Trade"),
    (52, 59, "G Retail Trade"),
    (60, 67, "H Finance, Insurance & Real Estate"),
    (70, 89, "I Services"),
    (91, 99, "J Public Administration"),
)


def division_of_sic(sic: Optional[str]) -> str:
    """The SIC division of one issuer's code. 'unclassified' when it has none."""
    if not sic:
        return "unclassified"
    text = str(sic).strip()
    if not text.isdigit():
        return "unclassified"
    major = int(text[:2]) if len(text) >= 2 else int(text)
    for low, high, name in SIC_DIVISIONS:
        if low <= major <= high:
            return name
    return f"unmapped ({text[:2]})"


# ==========================================================================
# Disk, WAL and the read-only connection
# ==========================================================================

def wal_bytes(db_path: str) -> int:
    """Size of the live WAL beside the database, or 0 when there is none."""
    try:
        return os.path.getsize(db_path + "-wal")
    except OSError:
        return 0


def disk_status(db_path: str) -> dict[str, Any]:
    """Free/total bytes for the volume holding the store, plus the WAL size."""
    usage = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path)) or ".")
    return {"free_bytes": usage.free, "total_bytes": usage.total,
            "used_pct": round(100.0 * (usage.total - usage.free) / usage.total, 1),
            "wal_bytes": wal_bytes(db_path)}


class ResourceWatch:
    """Free space and WAL size, sampled as the run goes. Never estimated.

    Peak WAL is the MAXIMUM observed, and the minimum free space is the WORST
    observed. Both are sampled rather than derived, and a quantity nobody
    sampled is reported as UNKNOWN.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        start = disk_status(db_path)
        self.free_start = start["free_bytes"]
        self.free_min = start["free_bytes"]
        self.wal_start = start["wal_bytes"]
        self.wal_peak = start["wal_bytes"]
        self.samples = 1
        self.started = time.time()

    def sample(self) -> None:
        status = disk_status(self.db_path)
        self.free_min = min(self.free_min, status["free_bytes"])
        self.wal_peak = max(self.wal_peak, status["wal_bytes"])
        self.samples += 1

    def report(self) -> dict[str, Any]:
        self.sample()
        end = disk_status(self.db_path)
        # The peak working set comes from `pit_cohort_resources`, which already
        # owns the Windows call. A second implementation of it here would be a
        # second answer to "what did this cost".
        try:
            import pit_cohort_resources
            peak_rss = pit_cohort_resources.peak_working_set()
        except Exception:
            peak_rss = UNKNOWN
        return {
            "samples": self.samples,
            "seconds": round(time.time() - self.started, 1),
            "free_bytes_start": self.free_start,
            "free_bytes_end": end["free_bytes"],
            "free_bytes_min_observed": self.free_min,
            "free_gib_min_observed": round(self.free_min / 1024 ** 3, 2),
            "free_delta_bytes": end["free_bytes"] - self.free_start,
            "wal_bytes_start": self.wal_start,
            "wal_bytes_peak_observed": self.wal_peak,
            "wal_bytes_end": end["wal_bytes"],
            "wal_grew_during_run": self.wal_peak > self.wal_start,
            "wal_growth_note": (
                "This module holds a mode=ro connection and CANNOT write. A "
                "WAL that grows during the run is ANOTHER process writing to "
                "the same store -- expected on this machine, and reported "
                "rather than attributed to this reader."),
            "peak_working_set_bytes": peak_rss,
            "peak_working_set_mib": (round(peak_rss / 1024 ** 2, 1)
                                     if peak_rss != UNKNOWN else UNKNOWN),
        }


def connect_ro(db_path: str) -> sqlite3.Connection:
    """The store, read-only, with a busy timeout and no long snapshot."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


# ==========================================================================
# Point-in-time indexes
# ==========================================================================

def _ord(day: Any) -> int:
    return _dt.date.fromisoformat(str(day)[:10]).toordinal()


def _deadline_ord(period_end_ord: int, qtrs: int, concept: str) -> int:
    """`period_end + max_age_months`: the last as-of date `is_stale` allows."""
    end = _dt.date.fromordinal(period_end_ord)
    total = end.month - 1 + pit_policy.max_age_months(qtrs, concept)
    year, month = end.year + total // 12, total % 12 + 1
    last = 31 if month == 12 else (
        _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(end.day, last)).toordinal()


def _ranked_tags(concept: str) -> dict[str, tuple[int, Optional[str]]]:
    """tag -> (ladder rank, min_filed_date gate) for one concept.

    Rank is LADDER ORDER, which is what decides the winner: the first rung with
    a vintage available on the date wins, exactly as `pit_policy.resolve` walks
    them. The gate travels with the tag because the two ASC 606 revenue tags
    are INVISIBLE before 2018 -- consulting them at a 2016 as-of reads a
    restatement filed three years later.
    """
    out: dict[str, tuple[int, Optional[str]]] = {}
    for rank, rung in enumerate(pit_policy.ladder_for(concept, LADDER).rungs):
        for tag in rung.tags:
            out.setdefault(tag, (rank, rung.min_filed_date))
    return out


def matched_value_index(conn: sqlite3.Connection, grid: Sequence[str],
                        progress: bool = True) -> dict[str, Any]:
    """Point-in-time EBITDA levels and MATCHED-period EBITDA margins per date.

    Two quantities, from one pass over `pit_fact`:

      ebitda[day][entity]  the latest NON-STALE period whose operating income
                           and D&A had both landed by `day`, at the latest
                           vintage knowable then. Identical by construction to
                           `pit_factor_spec.ebitda_value_index`, and
                           `--validate` proves it against that function rather
                           than asserting it.
      margin[day][entity]  EBITDA / revenue with ALL THREE components at ONE
                           (period_end, qtrs). A margin is a ratio of one
                           period's numbers -- `pit_policy`'s EBITDA assembly
                           rule -- and the matched period is why this cannot be
                           read off two independent selections.

    unit='USD' is enforced: entity 2544 is a JPY-reporting 20-F filer with a
    complete D&A history, and a JPY margin is fine but a JPY EBITDA inside a
    USD percentile is a 150x error with clean provenance.
    """
    started = time.time()
    oi_rank = _ranked_tags("operating_income")
    da_rank = _ranked_tags("depreciation_amortisation")
    rev_rank = _ranked_tags("revenue")
    tags = list(dict.fromkeys(list(oi_rank) + list(da_rank) + list(rev_rank)))
    marks = ", ".join("?" * len(tags))

    first_ord, last_ord = _ord(grid[0]), _ord(grid[-1])
    keyed: dict[tuple[int, int], dict[str, list]] = {}
    n_rows = 0
    for entity_id, tag, period_end, available, val in conn.execute(
            f"""SELECT entity_id, tag, period_end, available_date, val
                  FROM pit_fact
                 WHERE segments = '' AND coreg = '' AND unit = 'USD'
                   AND qtrs = ? AND tag IN ({marks})""",
            [ANNUAL_QTRS] + tags):
        n_rows += 1
        try:
            avail_o, pe_o = _ord(available), _ord(period_end)
        except (ValueError, TypeError):
            continue
        if avail_o > last_ord:
            continue
        key = (int(entity_id), pe_o)
        slot = keyed.get(key)
        if slot is None:
            slot = keyed[key] = {"oi": [], "da": [], "rev": []}
        if tag in oi_rank:
            rank, gate = oi_rank[tag]
            slot["oi"].append((rank, avail_o, float(val), gate))
        if tag in da_rank:
            rank, gate = da_rank[tag]
            slot["da"].append((rank, avail_o, float(val), gate))
        if tag in rev_rank:
            rank, gate = rev_rank[tag]
            slot["rev"].append((rank, avail_o, float(val), gate))
        if progress and n_rows % 2_000_000 == 0:
            print(f"    ... {n_rows:,} rows  {time.time() - started:.0f}s",
                  flush=True)

    per_entity: dict[int, list[tuple[int, int, list, list, list]]] = {}
    n_matched = 0
    for (entity_id, pe_o), slot in keyed.items():
        if not slot["oi"] or not slot["da"]:
            continue                       # no EBITDA at this period, ever
        deadline = _deadline_ord(pe_o, ANNUAL_QTRS, "operating_income")
        if deadline < first_ord:
            continue                       # retired before the grid begins
        n_matched += 1
        per_entity.setdefault(entity_id, []).append(
            (pe_o, deadline, sorted(slot["oi"]), sorted(slot["da"]),
             sorted(slot["rev"])))
    for rows in per_entity.values():
        rows.sort(key=lambda r: -r[0])
    keyed.clear()

    def pick(entries: Sequence[tuple[int, int, float, Optional[str]]],
             day_ord: int, day: str) -> Optional[float]:
        """The ladder's winner on this date: lowest rung with a landed vintage,
        latest vintage within that rung, date-gated rungs invisible."""
        best_rank: Optional[int] = None
        value: Optional[float] = None
        for rank, avail_o, val, gate in entries:
            if avail_o > day_ord:
                continue
            if gate is not None and gate > day:
                continue
            if best_rank is None or rank < best_rank:
                best_rank, value = rank, val
            elif rank == best_rank:
                value = val                # a later vintage of the same rung
        return value

    ebitda: dict[str, dict[int, float]] = {}
    margin: dict[str, dict[int, float]] = {}
    margin_periods: dict[str, dict[int, int]] = {}
    for position, day in enumerate(grid):
        day_ord = _ord(day)
        levels: dict[int, float] = {}
        margins: dict[int, float] = {}
        periods: dict[int, int] = {}
        for entity_id, rows in per_entity.items():
            level: Optional[float] = None
            for pe_o, deadline, oi, da, rev in rows:
                if pe_o > day_ord or deadline < day_ord:
                    continue               # not yet, or stale on this date
                oi_val = pick(oi, day_ord, day)
                da_val = pick(da, day_ord, day)
                if oi_val is None or da_val is None:
                    continue
                if level is None:
                    level = oi_val + da_val
                rev_val = pick(rev, day_ord, day)
                if rev_val is not None and rev_val > 0:
                    margins[entity_id] = (oi_val + da_val) / rev_val
                    periods[entity_id] = pe_o
                    break                  # newest matched triple wins
            if level is not None:
                levels[entity_id] = level
        ebitda[day] = levels
        margin[day] = margins
        margin_periods[day] = periods
        if progress and (position % 40 == 0 or position == len(grid) - 1):
            print(f"    {day}: {len(levels):,} with EBITDA, {len(margins):,} "
                  f"with a matched margin  {time.time() - started:.0f}s",
                  flush=True)

    return {"ebitda": ebitda, "margin": margin, "margin_periods": margin_periods,
            "n_rows_scanned": n_rows, "n_matched_periods": n_matched,
            "n_entities": len(per_entity),
            "seconds": round(time.time() - started, 1)}


def sic_index(conn: sqlite3.Connection) -> dict[int, list[tuple[str, str, str]]]:
    """entity -> [(filed, source_accession, sic)], sorted.

    The same order `pit_store.sic_as_of` selects on -- `ORDER BY filed DESC,
    source_accession DESC LIMIT 1` -- so a lookup here and a lookup there
    cannot disagree about which classification an issuer carried on a date. A
    cohort a company is ranked in must match the cohort its features came from.
    """
    index: dict[int, list[tuple[str, str, str]]] = {}
    for row in conn.execute(
            "SELECT entity_id, filed, source_accession, sic FROM pit_entity_sic"):
        index.setdefault(int(row["entity_id"]), []).append(
            (str(row["filed"])[:10], str(row["source_accession"]), str(row["sic"])))
    for rows in index.values():
        rows.sort()
    return index


def sic_as_of(index: Mapping[int, Sequence[tuple[str, str, str]]],
              entity_id: int, day: str) -> Optional[str]:
    """The SIC this issuer carried on `day`, from a prebuilt index."""
    rows = index.get(int(entity_id))
    if not rows:
        return None
    chosen: Optional[str] = None
    for filed, _accession, sic in rows:
        if filed <= day:
            chosen = sic
        else:
            break
    return chosen


# ==========================================================================
# Distributions
# ==========================================================================

def distribution(values: Sequence[float], places: int = 3) -> dict[str, Any]:
    """Percentiles of a sample. Empty in, empty out -- never a fabricated 0."""
    usable = sorted(float(v) for v in values
                    if v is not None and v == v and abs(v) != float("inf"))
    if not usable:
        return {"n": 0}
    out: dict[str, Any] = {"n": len(usable),
                           "min": round(usable[0], places),
                           "max": round(usable[-1], places)}
    for point in PERCENTILE_POINTS:
        value = statlib.percentile(usable, point / 100.0)
        out[f"p{point}"] = round(value, places) if value is not None else None
    out["median"] = out["p50"]
    out["mean"] = round(sum(usable) / len(usable), places)
    return out


# ==========================================================================
# THE MEASUREMENT
# ==========================================================================

def measure(conn: sqlite3.Connection, db_path: str, *, limit_dates: int = 0,
            progress: bool = True) -> dict[str, Any]:
    """Coherence per rung, the size-matched control, and the cost of the ban."""
    watch = ResourceWatch(db_path)
    grid = [row["as_of_date"] for row in conn.execute(
        """SELECT DISTINCT as_of_date FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
            ORDER BY as_of_date""",
        (pit_store.EQUITY_PIT_MODEL_VERSION, pit_store.PEER_SET_VERSION))]
    if limit_dates:
        grid = grid[-limit_dates:]
    if not grid:
        raise RuntimeError("no peer sets in this store: nothing to measure")

    if progress:
        print(f"  grid: {len(grid)} as-of dates "
              f"{grid[0]} .. {grid[-1]}", flush=True)
        print("  building the point-in-time value index (one pass over "
              "pit_fact)...", flush=True)
    index = matched_value_index(conn, grid, progress=progress)
    watch.sample()
    sics = sic_index(conn)
    watch.sample()

    sets = conn.execute(
        """SELECT peer_set_id, as_of_date, rung, key, n_members
             FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
            ORDER BY as_of_date, peer_set_id""",
        (pit_store.EQUITY_PIT_MODEL_VERSION, pit_store.PEER_SET_VERSION)).fetchall()
    wanted_dates = set(grid)
    by_date: dict[str, list[Any]] = {}
    for row in sets:
        if row["as_of_date"] in wanted_dates:
            by_date.setdefault(row["as_of_date"], []).append(row)

    #: per rung -> lists of the coherence statistics
    per_rung: dict[str, dict[str, list[float]]] = {}
    #: (rung, size bucket) -> the same, for the size-matched control
    matched: dict[tuple[str, str], dict[str, list[float]]] = {}
    cohort_rows: list[dict[str, Any]] = []

    #: the cost of the ban, accumulated per entity-date
    cost: dict[str, Any] = {
        "n_entity_dates": 0,
        "assigned_office": 0,
        "lost_no_admissible_peer_set": 0,
        "lost_admissible_set_too_thin": 0,
        "already_unavailable_everywhere": 0,
        "kept": 0,
        "office_would_have_served": 0,
        "by_year": {},
        "by_division": {},
    }

    def bucket_of(n: int) -> str:
        for low, high in ((12, 24), (25, 49), (50, 99), (100, 199), (200, 399),
                          (400, 541)):
            if low <= n <= high:
                return f"{low}-{high}"
        return "542+"           # the office range: 542-1,310 members

    def note(target: dict[str, list[float]], coherence: FS.CohortCoherence,
             n_members: int) -> None:
        target.setdefault("n_members", []).append(float(n_members))
        if coherence.effective_sic4_count is not None:
            target.setdefault("effective_sic4_count", []).append(
                coherence.effective_sic4_count)
        if coherence.top_sic4_share is not None:
            target.setdefault("top_sic4_share", []).append(coherence.top_sic4_share)
        if coherence.margin_iqr is not None and coherence.n_margin >= MIN_MARGIN_MEMBERS:
            target.setdefault("margin_iqr", []).append(coherence.margin_iqr)
            if coherence.margin_p10_p90 is not None:
                target.setdefault("margin_p10_p90", []).append(
                    coherence.margin_p10_p90)
        if (coherence.log10_ebitda_span is not None
                and coherence.n_level >= MIN_MARGIN_MEMBERS):
            target.setdefault("log10_ebitda_span", []).append(
                coherence.log10_ebitda_span)

    for position, day in enumerate(grid):
        rows = by_date.get(day, [])
        if not rows:
            continue
        ids = [int(r["peer_set_id"]) for r in rows]
        low, high = min(ids), max(ids)
        wanted = set(ids)
        members: dict[int, list[int]] = {i: [] for i in ids}
        for peer_set_id, entity_id in conn.execute(
                "SELECT peer_set_id, entity_id FROM pit_peer_member "
                "WHERE peer_set_id BETWEEN ? AND ?", (low, high)):
            if peer_set_id in wanted:
                members[int(peer_set_id)].append(int(entity_id))

        day_ebitda = index["ebitda"].get(day, {})
        day_margin = index["margin"].get(day, {})

        # ---- per cohort: coherence, and the EBITDA-carrying count ----------
        n_ebitda_by_set: dict[int, int] = {}
        rung_by_set: dict[int, str] = {}
        for row in rows:
            peer_set_id = int(row["peer_set_id"])
            cohort = members[peer_set_id]
            rung = str(row["rung"])
            rung_by_set[peer_set_id] = rung
            sic_by_member = {e: sic_as_of(sics, e, day) for e in cohort}
            margins = {e: day_margin[e] for e in cohort if e in day_margin}
            levels = {e: day_ebitda[e] for e in cohort if e in day_ebitda}
            coherence = FS.cohort_coherence(rung, sic_by_member, margins,
                                            levels)
            n_ebitda = sum(1 for e in cohort if e in day_ebitda)
            n_ebitda_by_set[peer_set_id] = n_ebitda
            note(per_rung.setdefault(rung, {}), coherence, len(cohort))
            note(matched.setdefault((rung, bucket_of(len(cohort))), {}),
                 coherence, len(cohort))
            cohort_rows.append({
                "peer_set_id": peer_set_id, "as_of": day, "rung": rung,
                "key": str(row["key"]), "n_members": len(cohort),
                "n_ebitda": n_ebitda, **coherence.as_dict()})

        # ---- per entity-date: what the ban costs ---------------------------
        best_permitted: dict[int, int] = {}
        office_n: dict[int, int] = {}
        narrowest: dict[int, int] = {}
        for peer_set_id, cohort in members.items():
            rung = rung_by_set[peer_set_id]
            rank = FS.RUNG_RANK.get(rung, len(FS.RUNG_RANK))
            n_ebitda = n_ebitda_by_set[peer_set_id]
            permitted = rung != FS.RUNG_OFFICE
            for entity_id in cohort:
                if rank < narrowest.get(entity_id, len(FS.RUNG_RANK)):
                    narrowest[entity_id] = rank
                if permitted:
                    if n_ebitda > best_permitted.get(entity_id, -1):
                        best_permitted[entity_id] = n_ebitda
                else:
                    office_n[entity_id] = max(office_n.get(entity_id, 0), n_ebitda)

        year = day[:4]
        year_row = cost["by_year"].setdefault(year, _empty_cost_row())
        for entity_id, rank in narrowest.items():
            division = division_of_sic(sic_as_of(sics, entity_id, day))
            division_row = cost["by_division"].setdefault(division, _empty_cost_row())
            permitted_n = best_permitted.get(entity_id, 0)
            office = office_n.get(entity_id, 0)
            assigned_office = rank == FS.RUNG_RANK[FS.RUNG_OFFICE]
            has_permitted_set = entity_id in best_permitted
            permitted_ok = permitted_n >= VALUATION_MIN_N
            office_ok = office >= VALUATION_MIN_N

            for target in (cost, year_row, division_row):
                target["n_entity_dates"] += 1
                if assigned_office:
                    target["assigned_office"] += 1
                if office_ok:
                    target["office_would_have_served"] += 1
                if permitted_ok:
                    target["kept"] += 1
                elif office_ok and not has_permitted_set:
                    target["lost_no_admissible_peer_set"] += 1
                elif office_ok:
                    target["lost_admissible_set_too_thin"] += 1
                else:
                    target["already_unavailable_everywhere"] += 1

        if progress and (position % 20 == 0 or position == len(grid) - 1):
            print(f"    {day}: {len(rows)} peer sets, "
                  f"{len(narrowest):,} entity-dates", flush=True)
        if position % 10 == 0:
            watch.sample()

    for target in [cost] + list(cost["by_year"].values()) + list(
            cost["by_division"].values()):
        _finish_cost_row(target)

    coherence_by_rung = {
        rung: {name: distribution(values) for name, values in sorted(stats.items())}
        for rung, stats in sorted(per_rung.items(),
                                  key=lambda kv: FS.RUNG_RANK.get(kv[0], 9))}
    size_matched = {
        f"{rung}|{bucket}": {name: distribution(values)
                             for name, values in sorted(stats.items())}
        for (rung, bucket), stats in sorted(
            matched.items(), key=lambda kv: (kv[0][1], FS.RUNG_RANK.get(kv[0][0], 9)))}

    payload: dict[str, Any] = {
        "measured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "db": os.path.abspath(db_path),
        "full_grid": not limit_dates,
        "sample_scope": FS.FULL_UNIVERSE,
        "spec_version": FS.FACTOR_SPEC_VERSION_V2,
        "ladder_version": LADDER,
        "model_version": pit_store.EQUITY_PIT_MODEL_VERSION,
        "peer_set_version": pit_store.PEER_SET_VERSION,
        "grid": {"n_dates": len(grid), "first": grid[0], "last": grid[-1]},
        "n_peer_sets": len(cohort_rows),
        "valuation_min_n": VALUATION_MIN_N,
        "min_margin_members": MIN_MARGIN_MEMBERS,
        "value_index": {k: v for k, v in index.items()
                        if k not in ("ebitda", "margin", "margin_periods")},
        "coherence_measures": FS.COHERENCE_MEASURES,
        "coherence_by_rung": coherence_by_rung,
        "size_matched_control": size_matched,
        "cost_of_the_ban": cost,
        "resources": watch.report(),
    }
    # The verdicts travel INSIDE the payload, so a JSON file read six months
    # from now says which statistics were quoted as coherence and which were
    # measured and thrown away -- without anyone having to re-run this.
    payload["candidate_verdicts"] = evaluate_candidates(payload)
    payload["coherence_adopted"] = sorted(
        k for k, v in payload["candidate_verdicts"].items()
        if v["verdict"] == "ADOPTED")
    payload["coherence_adopted_declared"] = list(FS.COHERENCE_ADOPTED)
    return payload


def _empty_cost_row() -> dict[str, Any]:
    return {"n_entity_dates": 0, "assigned_office": 0,
            "lost_no_admissible_peer_set": 0,
            "lost_admissible_set_too_thin": 0,
            "already_unavailable_everywhere": 0, "kept": 0,
            "office_would_have_served": 0}


def _finish_cost_row(row: dict[str, Any]) -> None:
    """Totals and shares. The lost count is the PRICE OF THE RULE."""
    lost = (row["lost_no_admissible_peer_set"]
            + row["lost_admissible_set_too_thin"])
    total = max(1, row["n_entity_dates"])
    row["lost_total"] = lost
    row["lost_pct_of_entity_dates"] = round(100.0 * lost / total, 2)
    served = max(1, row["office_would_have_served"])
    row["lost_pct_of_office_served"] = round(
        100.0 * lost / served, 2) if row["office_would_have_served"] else 0.0


# ==========================================================================
# VALIDATION -- the index is proved against the module that owns the question
# ==========================================================================

def validate_index(conn: sqlite3.Connection, grid: Sequence[str],
                   mine: Mapping[str, Mapping[int, float]],
                   progress: bool = True) -> dict[str, Any]:
    """This module's EBITDA levels against `pit_factor_spec.ebitda_value_index`.

    Two implementations of one selection rule is exactly the duplication this
    project refuses, so the local index exists only because the MARGIN needs a
    matched three-concept period that the EBITDA-only selector cannot express.
    The half that overlaps must agree EXACTLY, entity for entity and value for
    value, and this is what proves it instead of a comment claiming it.
    """
    started = time.time()
    theirs = FS.ebitda_value_index(conn, list(grid), progress=progress)["values"]
    disagreements: list[dict[str, Any]] = []
    compared = 0
    for day in grid:
        a = mine.get(day, {})
        b = theirs.get(day, {})
        if set(a) != set(b):
            only_mine = sorted(set(a) - set(b))[:5]
            only_theirs = sorted(set(b) - set(a))[:5]
            disagreements.append({"as_of": day, "kind": "membership",
                                  "only_here": only_mine,
                                  "only_in_factor_spec": only_theirs,
                                  "n_here": len(a), "n_there": len(b)})
            continue
        for entity_id, value in a.items():
            compared += 1
            other = b[entity_id]
            if abs(value - other) > max(1e-6, abs(other) * 1e-12):
                disagreements.append({"as_of": day, "kind": "value",
                                      "entity_id": entity_id,
                                      "here": value, "there": other})
                if len(disagreements) > 20:
                    break
    return {"dates": list(grid), "n_compared": compared,
            "n_disagreements": len(disagreements),
            "disagreements": disagreements[:20],
            "seconds": round(time.time() - started, 1)}


# ==========================================================================
# RENDERING
# ==========================================================================

def _row(name: str, dist: Mapping[str, Any], places: int = 2) -> str:
    if not dist or not dist.get("n"):
        return f"  {name:<24s}{'(no observations)':>56s}"
    def fmt(key: str) -> str:
        value = dist.get(key)
        return "-" if value is None else f"{value:.{places}f}"
    return (f"  {name:<24s}{dist['n']:>7d}" + "".join(
        f"{fmt(k):>9s}" for k in ("p10", "p25", "median", "p75", "p90", "mean")))


_HEAD = (f"  {'statistic':<24s}{'n':>7s}" + "".join(
    f"{k:>9s}" for k in ("p10", "p25", "median", "p75", "p90", "mean")))


def render(payload: Mapping[str, Any], width: int = 78) -> str:
    """The measurement as tables a person reads and argues with."""
    out: list[str] = []
    out.append("PEER-SET COHERENCE: sufficient N is not enough")
    out.append("=" * width)
    grid = payload["grid"]
    out.append(f"scope {payload['sample_scope']}   spec {payload['spec_version']}"
               f"   ladder {payload['ladder_version']}")
    out.append(f"{payload['n_peer_sets']:,} peer sets over {grid['n_dates']} "
               f"as-of dates {grid['first']} .. {grid['last']}")
    out.append("")

    out.append("(1) COHERENCE BY RUNG -- a number, not a rung name")
    out.append("-" * width)
    for name, measure_spec in payload["coherence_measures"].items():
        out.append(f"  {name}: {measure_spec['definition']}")
        out.append(f"      better = {measure_spec['better']}; degenerate at "
                   f"{measure_spec['degenerate_at']}")
    out.append("")
    for rung, stats in payload["coherence_by_rung"].items():
        out.append(f"  rung {rung}")
        out.append(_HEAD)
        for name in ("n_members", "effective_sic4_count", "top_sic4_share",
                     "margin_iqr", "margin_p10_p90", "log10_ebitda_span"):
            if name in stats:
                out.append(_row(name, stats[name]))
        out.append("")

    out.append("(2) THE SIZE-MATCHED CONTROL -- is it composition, or is it N?")
    out.append("-" * width)
    out.append("  Office cohorts hold 542-1,310 members and sic2 cohorts reach")
    out.append("  1,227, so the ranges OVERLAP and the two can be compared at")
    out.append("  equal size. A measure that still separates them there is")
    out.append("  measuring composition; one that does not is restating N.")
    out.append("")
    buckets: dict[str, list[str]] = {}
    for key in payload["size_matched_control"]:
        rung, bucket = key.split("|", 1)
        buckets.setdefault(bucket, []).append(rung)
    for bucket in sorted(buckets, key=lambda b: (b == "542+", b)):
        rungs = buckets[bucket]
        if len(rungs) < 2:
            continue
        out.append(f"  members {bucket}")
        out.append(f"  {'rung':<10s}{'sets':>7s}{'members med':>14s}"
                   f"{'eff SIC4 med':>15s}{'margin IQR med':>16s}")
        for rung in sorted(rungs, key=lambda r: FS.RUNG_RANK.get(r, 9)):
            stats = payload["size_matched_control"][f"{rung}|{bucket}"]
            members = stats.get("n_members", {})
            effective = stats.get("effective_sic4_count", {})
            iqr = stats.get("margin_iqr", {})
            out.append(
                f"  {rung:<10s}{members.get('n', 0):>7d}"
                f"{_num(members.get('median')):>14s}"
                f"{_num(effective.get('median')):>15s}"
                f"{_num(iqr.get('median'), 3):>16s}")
        out.append("")

    out.append("(2b) THE VERDICT -- which candidates may be quoted as coherence")
    out.append("-" * width)
    out.append(f"  {'statistic':<24s}{'better':>8s}{'sic4':>9s}{'sic3':>9s}"
               f"{'sic2':>9s}{'office':>9s}   verdict")
    for name, verdict in payload["candidate_verdicts"].items():
        by_rung = verdict["median_by_rung"]
        out.append(
            f"  {name:<24s}{verdict['better']:>8s}"
            + "".join(f"{_num(by_rung.get(r), 3):>9s}"
                      for r in ("sic4", "sic3", "sic2", "office"))
            + f"   {verdict['verdict']}")
    out.append("")
    for name, verdict in payload["candidate_verdicts"].items():
        out.append(f"  {name}: {verdict['verdict']} -- {verdict['why']}")
        for row in verdict["size_matched_rows"]:
            out.append(f"      at {row['bucket']:>8s} members: office "
                       f"{_num(row['office'], 3)} vs sic2 {_num(row['sic2'], 3)}"
                       f"   office worse: {row['office_worse']}")
    out.append("")
    out.append(f"  ADOPTED: {payload['coherence_adopted'] or 'NONE'}")
    out.append("")

    cost = payload["cost_of_the_ban"]
    out.append("(3) THE COST OF THE BAN -- the price of the rule, before it locks")
    out.append("-" * width)
    out.append(f"  entity-dates in the peer universe        "
               f"{cost['n_entity_dates']:>12,}")
    out.append(f"  assigned the office rung (no narrower set){cost['assigned_office']:>11,}")
    out.append(f"  valuation cohort kept at sic4/sic3/sic2  {cost['kept']:>12,}")
    out.append(f"  LOST: no admissible peer set at all      "
               f"{cost['lost_no_admissible_peer_set']:>12,}")
    out.append(f"  LOST: admissible set too thin, office had enough "
               f"{cost['lost_admissible_set_too_thin']:>4,}")
    out.append(f"  LOST TOTAL                               {cost['lost_total']:>12,}"
               f"   ({cost['lost_pct_of_entity_dates']}% of entity-dates)")
    out.append(f"  already unavailable everywhere           "
               f"{cost['already_unavailable_everywhere']:>12,}")
    out.append("")
    out.append(f"  {'year':<10s}{'entity-dates':>14s}{'lost':>10s}{'lost %':>10s}")
    for year in sorted(cost["by_year"]):
        row = cost["by_year"][year]
        out.append(f"  {year:<10s}{row['n_entity_dates']:>14,}"
                   f"{row['lost_total']:>10,}"
                   f"{row['lost_pct_of_entity_dates']:>9.2f}%")
    out.append("")
    out.append(f"  {'SIC division':<38s}{'entity-dates':>14s}{'lost':>10s}{'lost %':>10s}")
    for division in sorted(cost["by_division"]):
        row = cost["by_division"][division]
        out.append(f"  {division:<38s}{row['n_entity_dates']:>14,}"
                   f"{row['lost_total']:>10,}"
                   f"{row['lost_pct_of_entity_dates']:>9.2f}%")
    out.append("")

    resources = payload["resources"]
    out.append("(4) WHAT THE RUN COST -- measured, never estimated")
    out.append("-" * width)
    out.append(f"  seconds                  {resources['seconds']}")
    out.append(f"  free space, minimum seen {resources['free_gib_min_observed']} GiB "
               f"({resources['free_bytes_min_observed']:,} bytes)")
    out.append(f"  free space delta         {resources['free_delta_bytes']:,} bytes")
    out.append(f"  WAL peak observed        {resources['wal_bytes_peak_observed']:,} bytes"
               + ("   (another process is writing; this reader is mode=ro)"
                  if resources.get("wal_grew_during_run") else ""))
    out.append(f"  peak working set         {resources['peak_working_set_mib']} MiB "
               f"({resources['peak_working_set_bytes']})")
    return "\n".join(out)


def _num(value: Any, places: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return str(value)


# ==========================================================================
# CHECKS -- the claims this module makes, tested against what it measured
# ==========================================================================

def _worse(statistic: str, a: Optional[float], b: Optional[float]) -> Optional[bool]:
    """Is `a` WORSE than `b` on this statistic? None when either is missing.

    "Worse" is read off the statistic's own declared direction in
    `pit_factor_spec.COHERENCE_MEASURES`, never hard-coded here: a measure
    whose direction lived in two places would eventually disagree with itself.
    """
    if a is None or b is None:
        return None
    better = FS.COHERENCE_MEASURES.get(statistic, {}).get("better", "lower")
    return a > b if better == "lower" else a < b


def evaluate_candidates(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Which candidate statistics may be QUOTED as coherence, and which may not.

    Three tests, and a candidate has to pass all three:

      LADDER      the statistic gets monotonically worse from sic4 to office.
      SEPARATES   office is worse than sic2 over all peer sets.
      CONTROLLED  office is STILL worse than sic2 inside every member-count
                  bucket that holds both. This is the one that matters: a
                  statistic that separates the rungs only because office
                  cohorts are bigger is measuring N, and the second test would
                  then be a disguised copy of the first.

    A rejected candidate keeps its measured numbers. "We tried it and it does
    not separate" is a finding; leaving it out would look like it was never
    considered.
    """
    rungs = payload["coherence_by_rung"]
    control = payload["size_matched_control"]
    ladder = [r for r in ("sic4", "sic3", "sic2", "office") if r in rungs]

    def median(rung: str, statistic: str) -> Optional[float]:
        return rungs.get(rung, {}).get(statistic, {}).get("median")

    verdicts: dict[str, Any] = {}
    for statistic in FS.COHERENCE_MEASURES:
        by_rung = {rung: median(rung, statistic) for rung in ladder}
        series = [by_rung[r] for r in ladder]
        monotone: Optional[bool] = None
        if all(v is not None for v in series) and len(series) > 1:
            monotone = all(_worse(statistic, b, a) is not False
                           for a, b in zip(series, series[1:]))
        separates = _worse(statistic, by_rung.get("office"), by_rung.get("sic2"))

        buckets: dict[str, Any] = {}
        for key in control:
            rung, bucket = key.split("|", 1)
            if rung not in ("office", "sic2"):
                continue
            buckets.setdefault(bucket, {})[rung] = (
                control[key].get(statistic, {}).get("median"))
        matched_rows = []
        controlled: Optional[bool] = None
        for bucket, pair in sorted(buckets.items()):
            if "office" not in pair or "sic2" not in pair:
                continue
            answer = _worse(statistic, pair["office"], pair["sic2"])
            matched_rows.append({"bucket": bucket, "office": pair["office"],
                                 "sic2": pair["sic2"], "office_worse": answer})
            if answer is None:
                continue
            controlled = answer if controlled is None else (controlled and answer)

        adopted = bool(monotone) and bool(separates) and bool(controlled)
        if adopted:
            verdict = "ADOPTED"
            why = ("separates office from sic2 and KEEPS separating them at "
                   "equal member count")
        elif controlled is None:
            verdict = "REJECTED"
            why = ("no member-count bucket holds both office and sic2 cohorts, "
                   "so the size-matched control could not be run -- an "
                   "uncontrolled separation may be a restatement of N")
        elif not controlled:
            verdict = "REJECTED"
            why = ("fails the size-matched control: inside at least one "
                   "member-count bucket the office cohorts are NOT worse than "
                   "the sic2 cohorts, so what looked like coherence was the "
                   "member count")
        elif not separates:
            verdict = "REJECTED"
            why = "office is not worse than sic2 over all peer sets"
        else:
            verdict = "REJECTED"
            why = "the statistic is not monotone along the SIC ladder"

        verdicts[statistic] = {
            "verdict": verdict, "why": why,
            "better": FS.COHERENCE_MEASURES[statistic]["better"],
            "median_by_rung": by_rung,
            "monotone_along_ladder": monotone,
            "office_worse_than_sic2": separates,
            "survives_size_matched_control": controlled,
            "size_matched_rows": matched_rows,
        }
    return verdicts


def check_findings(payload: Mapping[str, Any]) -> list[str]:
    """Every claim this module makes, re-tested against the numbers it measured.

    A measurement that cannot fail is a decoration. If a future store makes the
    office rung look coherent, this fails and the owner's ruling gets
    re-examined on evidence instead of being quietly kept.
    """
    problems: list[str] = []
    rungs = payload["coherence_by_rung"]
    for rung in ("sic4", "sic2", "office"):
        if rung not in rungs:
            problems.append(f"no peer sets at rung {rung}: nothing was measured")
    if problems:
        return problems

    verdicts = payload["candidate_verdicts"]
    adopted = sorted(k for k, v in verdicts.items() if v["verdict"] == "ADOPTED")

    #: (a) the second test needs at least one number behind it.
    if not adopted:
        problems.append(
            "NO candidate statistic survived the size-matched control, so "
            "'coherence' has no measured number behind it and the rung ceiling "
            "would be resting on the rung name alone")

    #: (b) an adopted statistic must be one the spec declares, and the spec's
    #: declaration must not name a statistic the data rejected.
    declared = set(FS.COHERENCE_ADOPTED)
    if declared and payload.get("full_grid"):
        if declared != set(adopted):
            problems.append(
                f"pit_factor_spec.COHERENCE_ADOPTED says {sorted(declared)} "
                f"while the measurement adopts {adopted} -- the declaration "
                "has drifted from the evidence")
    for name in declared:
        if name not in FS.COHERENCE_MEASURES:
            problems.append(f"COHERENCE_ADOPTED names {name!r}, which is not a "
                            "declared candidate")

    #: (c) the composition measure is degenerate at sic4 BY CONSTRUCTION, and
    #: saying so is part of reading it honestly.
    if (rungs.get("sic4", {}).get("effective_sic4_count", {}).get("median")
            != 1.0):
        problems.append("effective_sic4_count at sic4 must be exactly 1.00 -- "
                        "every member shares the code by construction")

    #: (d) the cost of the ban must be internally consistent.
    cost = payload["cost_of_the_ban"]
    parts = (cost["kept"] + cost["lost_no_admissible_peer_set"]
             + cost["lost_admissible_set_too_thin"]
             + cost["already_unavailable_everywhere"])
    if parts != cost["n_entity_dates"]:
        problems.append(f"the cost rows do not sum to the entity-date count: "
                        f"{parts} vs {cost['n_entity_dates']}")
    if cost["n_entity_dates"] <= 0:
        problems.append("no entity-dates were measured")

    #: (e) the volume must never have gone below the floor. The WAL is NOT
    #: checked here: this reader cannot write, and another agent writing to the
    #: same store is a fact about the machine, not a failure of this run. It is
    #: reported in `resources.wal_grew_during_run` instead.
    resources = payload["resources"]
    if resources["free_bytes_min_observed"] < MIN_FREE_BYTES:
        problems.append(
            f"free space fell to "
            f"{resources['free_gib_min_observed']} GiB during the run, below "
            f"the {MIN_FREE_BYTES / 1024 ** 3:.0f} GiB floor")
    return problems


# ==========================================================================
# CLI
# ==========================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = ""
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    limit_dates = 0
    do_validate = True
    i = 0
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--limit-dates" and i + 1 < len(argv):
            limit_dates = int(argv[i + 1]); i += 2
        elif argv[i] == "--no-validate":
            do_validate = False; i += 1
        else:
            i += 1

    if not os.path.exists(db_path):
        print(f"store not found: {db_path}", file=sys.stderr)
        return 2
    status = disk_status(db_path)
    print(f"disk: {status['free_bytes'] / 1024 ** 3:.2f} GiB free of "
          f"{status['total_bytes'] / 1024 ** 3:.0f} GiB ({status['used_pct']}% "
          f"used); WAL {status['wal_bytes']:,} bytes")
    if status["free_bytes"] < MIN_FREE_BYTES:
        print(f"ABORT: {status['free_bytes'] / 1024 ** 3:.2f} GiB free is below "
              f"the {MIN_FREE_BYTES / 1024 ** 3:.0f} GiB floor", file=sys.stderr)
        return 2

    conn = connect_ro(db_path)
    try:
        payload = measure(conn, db_path, limit_dates=limit_dates)
        if do_validate:
            print("  validating the value index against "
                  "pit_factor_spec.ebitda_value_index...", flush=True)
            sample = [payload["grid"]["first"], payload["grid"]["last"]]
            index = matched_value_index(conn, sample, progress=False)
            payload["index_validation"] = validate_index(
                conn, sample, index["ebitda"], progress=False)
    finally:
        conn.close()

    problems = check_findings(payload)
    validation = payload.get("index_validation")
    if validation and validation["n_disagreements"]:
        problems.append(
            f"the local value index disagrees with "
            f"pit_factor_spec.ebitda_value_index on "
            f"{validation['n_disagreements']} observation(s)")
    payload["problems"] = problems

    text = render(payload)
    print()
    print(text)
    if validation:
        print()
        print(f"index validation: {validation['n_compared']:,} EBITDA values "
              f"compared against pit_factor_spec on {len(validation['dates'])} "
              f"dates, {validation['n_disagreements']} disagreements")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        json_path = os.path.join(out_dir, "peer_coherence.json")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        report_path = os.path.join(out_dir, "peer_coherence_report.txt")
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"\nwrote {json_path}\nwrote {report_path}")

    print()
    if problems:
        for problem in problems:
            print(f"  FAIL  {problem}")
    print("PASS" if not problems else "FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
