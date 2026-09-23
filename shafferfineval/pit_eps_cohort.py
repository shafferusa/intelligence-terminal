"""THE DELIVERABLE: can P/E actually be the broad valuation backbone?

READ-ONLY. Stdlib only. Writes JSON to the caller's --out directory and nothing
to the store.

THE QUESTION, stated precisely. EV/EBITDA was measured over all 39,038 peer sets
and the answer was a MEDIAN COHORT OF ONE: P(N>=3) = 34.2% and P(N>=12) = 7.0%,
where 3 is `company_scoring.MIN_EBITDA_COHORT` and 12 is what a 50-75 percentile
BAND needs -- the band is a quarter of the vector by construction, so three
members inside it requires twelve in it. This module computes THE SAME two
numbers for P/E over THE SAME peer sets, so the comparison is like for like and
the owner can see whether decision 2 -- P/E as the primary broad valuation input
in candidate v2 -- is supported by the data or merely hoped for.

WHAT A USABLE P/E COSTS, leaf by leaf:

    EV/EBITDA   price AND a defensible point-in-time share count AND total_debt
                AND cash AND a matched-period EBITDA          -- five leaves
    P/E         price AND a point-in-time TTM EPS that is POSITIVE
                                                              -- two leaves

The share count is the BINDING leaf (leave-one-out: 34.2% -> 61.6% when it is
dropped), and P/E does not use it. That is the whole thesis, and this file is
where it is confirmed or refuted.

THREE COHORTS ARE REPORTED AND THEY ARE NOT THE SAME COHORT:

    N_PE            price + TTM EPS strictly above one cent. The multiple a
                    percentile can rank.
    N_PE_incl_extreme  the above PLUS the near-zero-positive cases. Reported
                    separately because a $30 stock at $0.01 of earnings prints
                    a P/E of 3,000 that halves on the next cent; putting it in
                    a percentile vector is a decision, not a default.
    N_EPS_any_sign  price + a usable TTM EPS OF ANY SIGN. This is NOT a P/E
                    cohort -- it is the SIGNED EARNINGS YIELD cohort, the ML
                    research leg, and it is reported so the gap between "we
                    have earnings" and "we have a multiple" is a measured
                    number rather than an inference.

SAMPLE SCOPE. Every number that touches a price is SURVIVOR_ONLY_DIAGNOSTIC:
`pit_listing` holds company lines that are all alive in 2026 with no series
ending before 2020, so a count of price-resolvable peers is a count among
survivors. The label travels with the number in the output file. The EPS
availability counts that do NOT involve a price are labelled separately.

    python pit_eps_cohort.py [--out DIR] [--priced-cache FILE]
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import sqlite3
import sys
import time
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_scan
import pit_eps
import pit_identity
import pit_policy
import pit_store

SAMPLE_SCOPE_PRICED = pit_store.SAMPLE_SURVIVOR_ONLY
SAMPLE_SCOPE_FUNDAMENTAL = "FULL_REPORTING_UNIVERSE"

#: 3 is `company_scoring.MIN_EBITDA_COHORT`; 12 is the 50-75 band's requirement.
#: The same two the EV/EBITDA measurement reported, so the rows line up.
THRESHOLDS = (3, 5, 8, 12)
PERCENTILE_POINTS = (10, 25, 50, 75, 90)

CENSUS_DATES = ("2015-06-30", "2019-06-28", "2024-06-28")

#: The measured EV/EBITDA result this file exists to be compared against. NOT
#: re-derived here -- it is quoted, with its provenance, so the deliverable is
#: one table rather than two documents.
EV_EBITDA_MEASURED = {
    "peer_sets": 39038,
    "median_cohort": 1,
    "pct_ge_3": 34.2,
    "pct_ge_12": 7.0,
    "sic4_pct_ge_12": 2.4,
    "sic4_share_of_peer_sets": 57.4,
    "leaves": ["price", "defensible_share_count", "total_debt", "cash",
               "ebitda_matched_period"],
    "binding_leaf": "defensible_share_count",
    "leave_one_out_pct_ge_3": {"all_five": 34.2, "no_share_guard": 61.6,
                               "no_total_debt": 51.2, "no_ebitda": 46.8,
                               "no_cash": 35.0},
    "source": ("measured before this module was written; quoted, not "
               "re-derived"),
}


def percentiles(values: Sequence[int]) -> dict[str, Any]:
    """Nearest-rank percentiles over MEMBER COUNTS, matching pit_cohort_measure.

    Nearest rank and not interpolation: a median of 23.5 peers is a cohort no
    percentile was ever taken over.
    """
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    out: dict[str, Any] = {"n_sets": n, "min": ordered[0], "max": ordered[-1]}
    for point in PERCENTILE_POINTS:
        rank = max(1, min(n, math.ceil(point / 100.0 * n)))
        out[f"p{point}"] = ordered[rank - 1]
    out["median"] = out["p50"]
    out["mean"] = round(sum(ordered) / n, 2)
    for threshold in THRESHOLDS:
        out[f"pct_ge_{threshold}"] = round(
            100.0 * sum(1 for v in ordered if v >= threshold) / n, 2)
    return out


# ==========================================================================
# (1) THE IN-MEMORY LADDER EVALUATOR
# ==========================================================================
#
# `pit_eps.ttm_eps_as_of_detail` is the authority, and it is one indexed SELECT
# per (entity, concept, date). 2,533 entities x 165 grid dates x up to 3 rungs
# is over a million random reads against an 8.35 GiB file on a box whose free
# RAM is measured in hundreds of megabytes. So the rows are loaded ONCE per
# entity and the ladder is walked in memory -- and `validate_against_selector`
# proves the two agree on a sample rather than asserting it here.

class EntityTtm:
    """One issuer's TTM rows, arranged so the ladder can be walked per date."""

    __slots__ = ("by_concept",)

    def __init__(self, rows: Sequence[tuple[Any, ...]]) -> None:
        # concept -> period_end -> list[(available_date, ttm_eps, pe_leg, sign,
        #                                 method, sign_crossing)]
        # v6 (owner ruling R21, 2026-09-23): method and sign_crossing ride
        # along so a replay row can say WHICH TTM assembly and whether the
        # earnings crossed zero, without a second read per entity-date.
        table: dict[str, dict[str, list[tuple[Any, ...]]]] = {}
        for concept, period_end, ttm, avail, leg, sign, method, crossing in rows:
            table.setdefault(concept, {}).setdefault(period_end, []).append(
                (avail, float(ttm), leg, sign, method, int(crossing or 0)))
        self.by_concept: dict[str, list[tuple[str, list[tuple[Any, ...]]]]] = {}
        for concept, periods in table.items():
            ordered = sorted(periods.items(), reverse=True)     # newest first
            for _period, vintages in ordered:
                vintages.sort()                                  # by available_date
            self.by_concept[concept] = ordered

    def reason(self, as_of: str,
               ladder: Sequence[str] = pit_eps.EPS_LADDER) -> str:
        """Why select() returned None: stale, not yet filed, or never filed.

        Mirrors pit_eps.ttm_eps_as_of_detail's precedence (stale_seen before
        future_seen) so the replay row's 'er' code reads like the selector's.
        """
        stale_seen = future_seen = False
        for concept in ladder:
            for period_end, vintages in self.by_concept.get(concept) or ():
                if period_end > as_of:
                    future_seen = True
                    continue
                if any(v[0] <= as_of for v in vintages):
                    stale_seen = True          # knowable, so only staleness refused it
                else:
                    future_seen = True
        if stale_seen:
            return pit_eps.REASON_STALE
        if future_seen:
            return pit_eps.REASON_NOT_YET_FILED
        return pit_eps.REASON_NEVER_FILED

    def select(self, as_of: str,
               ladder: Sequence[str] = pit_eps.EPS_LADDER) -> Optional[dict[str, Any]]:
        """The ladder-selected TTM at `as_of`, or None. Mirrors the SQL exactly.

        The SQL takes the newest (period_end, available_date) satisfying BOTH
        bounds and THEN tests staleness -- if the winner is stale the whole rung
        fails and the ladder moves on; it does NOT retreat to an older period.
        This does the same, and must, because a measurement that is 'nearly' the
        selector is a different measurement.
        """
        for concept in ladder:
            periods = self.by_concept.get(concept)
            if not periods:
                continue
            for period_end, vintages in periods:
                if period_end > as_of:
                    continue
                newest = None
                for vintage in vintages:
                    if vintage[0] <= as_of:
                        newest = vintage
                    else:
                        break
                if newest is None:
                    continue
                if pit_policy.is_stale(period_end, as_of, pit_eps.STALENESS_QTRS,
                                       pit_eps.STALENESS_CONCEPT):
                    break                       # rung fails; next concept
                return {"concept_key": concept, "period_end": period_end,
                        "available_date": newest[0], "ttm_eps": newest[1],
                        "pe_leg": newest[2], "sign_case": newest[3],
                        "method": newest[4], "sign_crossing": newest[5]}
        return None


def load_ttm(conn: sqlite3.Connection) -> dict[int, EntityTtm]:
    """Every TTM row, grouped per entity. ONE sequential pass, no random reads.

    Every string is INTERNED through one dict. This box has under 1 GiB of free
    RAM and the columns loaded here are drawn from small alphabets -- three
    concept keys, four pe_legs, a few thousand distinct period ends and filing
    dates across hundreds of thousands of rows -- so sharing one object per
    distinct value is the difference between a measurement that runs and one
    that swaps.
    """
    pool: dict[str, str] = {}

    def intern(value: str) -> str:
        got = pool.get(value)
        if got is None:
            pool[value] = value
            return value
        return got

    buckets: dict[int, list[tuple[Any, ...]]] = {}
    for (entity_id, concept, period_end, ttm, avail, leg, sign, method,
         crossing) in conn.execute(
            "SELECT entity_id, concept_key, period_end, ttm_eps, available_date, "
            "pe_leg, sign_case, method, sign_crossing FROM pit_eps_ttm"):
        buckets.setdefault(int(entity_id), []).append(
            (intern(concept), intern(period_end), ttm, intern(avail),
             intern(leg), intern(sign), intern(str(method)), int(crossing or 0)))
    return {e: EntityTtm(rows) for e, rows in buckets.items()}


def validate_against_selector(conn: sqlite3.Connection,
                              table: dict[int, EntityTtm],
                              dates: Sequence[str], sample: int = 400,
                              seed: int = 20260921) -> dict[str, Any]:
    """Prove the in-memory ladder equals `pit_eps.ttm_eps_as_of_detail`.

    A vectorised selector that has not been checked against the real one is an
    assertion, not a measurement. This runs both on a seeded random sample of
    (entity, date) pairs and reports disagreements rather than raising, so a
    mismatch is visible in the output file even if nobody reads the console.
    """
    import random
    rng = random.Random(seed)
    entities = sorted(table)
    if not entities:
        return {"checked": 0, "agreed": 0, "disagreements": [],
                "note": "no TTM rows to validate"}
    checked = agreed = 0
    disagreements: list[dict[str, Any]] = []
    for _ in range(sample):
        entity_id = rng.choice(entities)
        day = rng.choice(list(dates))
        mine = table[entity_id].select(day)
        theirs = pit_eps.ttm_eps_as_of_detail(conn, entity_id, day)
        checked += 1
        same = ((mine is None and not theirs["available"])
                or (mine is not None and theirs["available"]
                    and mine["concept_key"] == theirs["concept_key"]
                    and mine["period_end"] == theirs["period_end"]
                    and abs(mine["ttm_eps"] - theirs["ttm_eps"]) < 1e-9
                    and str(mine["method"]) == str(theirs["method"])
                    and int(mine["sign_crossing"]) == int(theirs["sign_crossing"])))
        if same:
            agreed += 1
        elif len(disagreements) < 20:
            disagreements.append({"entity_id": entity_id, "as_of": day,
                                  "in_memory": mine,
                                  "selector_available": theirs["available"],
                                  "selector_period": theirs["period_end"],
                                  "selector_ttm": theirs["ttm_eps"]})
    return {"checked": checked, "agreed": agreed,
            "pct_agreed": round(100.0 * agreed / max(checked, 1), 2),
            "disagreements": disagreements}


# ==========================================================================
# (2) PRICED UNIVERSE PER DATE
# ==========================================================================

def priced_per_date(conn: sqlite3.Connection, dates: Sequence[str],
                    progress: bool = True) -> dict[str, list[int]]:
    """`scored_universe_as_of` at every grid date. SURVIVOR_ONLY_DIAGNOSTIC.

    The same price test the peer sets were built with. Deliberately NOT the
    share-count half of `pit_cohort_price.measure_date`: a P/E needs a price and
    no share count, and re-running the defensible-share ladder here would import
    the very leaf P/E exists to avoid.
    """
    out: dict[str, list[int]] = {}
    started = time.time()
    for index, day in enumerate(dates):
        out[day] = sorted({int(r["entity_id"])
                           for r in pit_identity.scored_universe_as_of(conn, day)})
        if progress and (index % 25 == 0 or index == len(dates) - 1):
            print(f"  priced {day}: {len(out[day]):5d}  "
                  f"{time.time() - started:.0f}s", flush=True)
    return out


# ==========================================================================
# (3) THE THREE MEASUREMENTS
# ==========================================================================

def eps_coverage(conn: sqlite3.Connection, table: dict[int, EntityTtm],
                 priced: dict[str, list[int]],
                 dates: Sequence[str] = CENSUS_DATES) -> dict[str, Any]:
    """EPS coverage as a share of the PRICED universe, at the census dates.

    The denominator is the priced universe at that date and not the entities
    that happen to have EPS -- dividing by the survivors is how a coverage
    number becomes survivorship bias with a decimal point.
    """
    out: dict[str, Any] = {}
    for day in dates:
        universe = priced.get(day) or []
        n = len(universe)
        counts = {"eps_any_sign": 0, "pe_available": 0, "pe_extreme": 0,
                  "eps_zero": 0, "eps_negative": 0, "no_eps": 0,
                  "crossing_flagged": 0}
        by_concept: dict[str, int] = {}
        by_method_unknown = 0
        for entity_id in universe:
            entity = table.get(entity_id)
            picked = entity.select(day) if entity else None
            if picked is None:
                counts["no_eps"] += 1
                continue
            counts["eps_any_sign"] += 1
            by_concept[picked["concept_key"]] = by_concept.get(
                picked["concept_key"], 0) + 1
            leg = picked["pe_leg"]
            if leg == pit_eps.PE_AVAILABLE:
                counts["pe_available"] += 1
            elif leg == pit_eps.PE_AVAILABLE_EXTREME:
                counts["pe_extreme"] += 1
            elif leg == pit_eps.PE_UNAVAILABLE_ZERO:
                counts["eps_zero"] += 1
            else:
                counts["eps_negative"] += 1
        out[day] = {
            "priced_universe": n,
            "sample_scope": SAMPLE_SCOPE_PRICED,
            **counts,
            "pct_eps_any_sign": round(100.0 * counts["eps_any_sign"] / max(n, 1), 2),
            "pct_pe_available": round(100.0 * counts["pe_available"] / max(n, 1), 2),
            "pct_pe_incl_extreme": round(
                100.0 * (counts["pe_available"] + counts["pe_extreme"]) / max(n, 1), 2),
            "pct_of_eps_that_is_a_multiple": round(
                100.0 * counts["pe_available"] / max(counts["eps_any_sign"], 1), 2),
            "concept_wins": dict(sorted(by_concept.items())),
            "unknown_method": by_method_unknown,
        }
    return out


def sign_census(conn: sqlite3.Connection) -> dict[str, Any]:
    """The sign-case census over the stored rows -- primitives AND derived TTM.

    Two populations, reported separately because they answer different
    questions: the primitives are every EPS figure ever filed by these issuers
    (quarterly and annual, every vintage), the TTM rows are the derived
    annual-length quantity a multiple would actually use.
    """
    primitives = {r[0]: int(r[1]) for r in conn.execute(
        "SELECT sign_case, COUNT(*) FROM pit_eps_obs GROUP BY sign_case")}
    ttm = {r[0]: int(r[1]) for r in conn.execute(
        "SELECT sign_case, COUNT(*) FROM pit_eps_ttm GROUP BY sign_case")}
    crossings = int(conn.execute(
        "SELECT COUNT(*) FROM pit_eps_ttm WHERE sign_crossing = 1").fetchone()[0])
    ttm_total = sum(ttm.values()) or 1
    entities_crossing = int(conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM pit_eps_ttm "
        "WHERE sign_crossing = 1").fetchone()[0])
    entities_any = int(conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM pit_eps_ttm").fetchone()[0]) or 1
    methods = {r[0]: int(r[1]) for r in conn.execute(
        "SELECT method, COUNT(*) FROM pit_eps_ttm GROUP BY method")}
    concepts = {r[0]: int(r[1]) for r in conn.execute(
        "SELECT concept_key, COUNT(*) FROM pit_eps_obs GROUP BY concept_key")}
    return {
        "sample_scope": SAMPLE_SCOPE_FUNDAMENTAL,
        "primitive_rows": {"by_sign": dict(sorted(primitives.items())),
                           "total": sum(primitives.values()),
                           "by_concept": dict(sorted(concepts.items()))},
        "ttm_rows": {
            "by_sign": dict(sorted(ttm.items())),
            "total": sum(ttm.values()),
            "pct_by_sign": {k: round(100.0 * v / ttm_total, 2)
                            for k, v in sorted(ttm.items())},
            "by_method": dict(sorted(methods.items())),
        },
        "sign_crossing": {
            "ttm_rows_flagged": crossings,
            "pct_of_ttm_rows": round(100.0 * crossings / ttm_total, 2),
            "entities_with_a_crossing": entities_crossing,
            "entities_with_any_ttm": entities_any,
            "pct_of_entities": round(100.0 * entities_crossing / entities_any, 2),
            "what_it_means": ("a TTM EPS that crossed zero between adjacent "
                              "periods. Every change-based earnings factor must "
                              "DROP that step: a ratio across a sign change is "
                              "not a growth rate."),
        },
        "why_no_abs": pit_eps.eps_ladder_spec()["never_abs"],
    }


def scoped_entities(conn: sqlite3.Connection) -> set[int]:
    """Entities the EPS ingest actually ASKED about -- 404s included.

    `pit_eps_ingest` records the negative results, which is what makes
    "we never asked" separable from "we asked and the issuer never filed".
    Without that separation a coverage gap caused by the owner's scoping
    decision would be indistinguishable from one caused by the source.
    """
    return {int(r[0]) for r in conn.execute(
        "SELECT DISTINCT entity_id FROM pit_eps_ingest")}


def peer_cohorts(conn: sqlite3.Connection, table: dict[int, EntityTtm],
                 priced: dict[str, list[int]], scoped: set[int],
                 model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                 peer_set_version: str = pit_store.PEER_SET_VERSION,
                 progress: bool = True) -> dict[str, Any]:
    """Per-peer-set P/E cohort sizes over every peer set. THE DELIVERABLE.

    Same peer sets, same thresholds and the same nearest-rank percentiles the
    EV/EBITDA measurement used, so the two rows of the comparison table are
    produced the same way.
    """
    started = time.time()
    sets = conn.execute(
        """SELECT peer_set_id, as_of_date, rung, key, n_members
             FROM pit_peer_set WHERE model_version = ? AND peer_set_version = ?
            ORDER BY as_of_date, peer_set_id""",
        (model_version, peer_set_version)).fetchall()
    by_date: dict[str, list[sqlite3.Row]] = {}
    for row in sets:
        by_date.setdefault(row["as_of_date"], []).append(row)

    records: list[dict[str, Any]] = []
    member_rows = 0
    priced_outside_scope = 0
    priced_in_scope_no_ttm = 0
    for day, rows in sorted(by_date.items()):
        priced_today = set(priced.get(day) or ())
        ids = [int(r["peer_set_id"]) for r in rows]
        lo, hi = min(ids), max(ids)
        wanted = set(ids)
        members: dict[int, list[int]] = {i: [] for i in ids}
        for peer_set_id, entity_id in conn.execute(
                "SELECT peer_set_id, entity_id FROM pit_peer_member "
                "WHERE peer_set_id BETWEEN ? AND ?", (lo, hi)):
            if peer_set_id in wanted:
                members[peer_set_id].append(int(entity_id))
                member_rows += 1

        cache: dict[int, Optional[dict[str, Any]]] = {}

        def picked_for(entity_id: int) -> Optional[dict[str, Any]]:
            if entity_id not in cache:
                entity = table.get(entity_id)
                cache[entity_id] = entity.select(day) if entity else None
            return cache[entity_id]

        for row in rows:
            cohort = members[int(row["peer_set_id"])]
            n_priced = n_eps = n_pe = n_pe_ext = n_neg = 0
            for entity_id in cohort:
                if entity_id not in priced_today:
                    continue
                n_priced += 1
                picked = picked_for(entity_id)
                if picked is None:
                    # Two different absences, and they must not be averaged:
                    # one is the owner's scoping decision, the other is the
                    # source.
                    if entity_id not in scoped:
                        priced_outside_scope += 1
                    else:
                        priced_in_scope_no_ttm += 1
                    continue
                n_eps += 1
                if picked["pe_leg"] == pit_eps.PE_AVAILABLE:
                    n_pe += 1
                    n_pe_ext += 1
                elif picked["pe_leg"] == pit_eps.PE_AVAILABLE_EXTREME:
                    n_pe_ext += 1
                elif picked["pe_leg"] == pit_eps.PE_UNAVAILABLE_LOSS:
                    n_neg += 1
            records.append({
                "peer_set_id": int(row["peer_set_id"]), "as_of": day,
                "year": day[:4], "rung": row["rung"], "key": row["key"],
                "n_members": len(cohort), "N_Priced": n_priced,
                "N_EPS_any_sign": n_eps, "N_PE": n_pe,
                "N_PE_incl_extreme": n_pe_ext, "N_EPS_negative": n_neg,
            })
        if progress and len(records) % 5000 < len(rows):
            print(f"  {day}: {len(records):,} sets, {time.time() - started:.0f}s",
                  flush=True)

    return {"records": records, "n_peer_sets": len(records),
            "n_member_rows": member_rows,
            "priced_members_outside_eps_scope": priced_outside_scope,
            "priced_members_in_scope_with_no_ttm": priced_in_scope_no_ttm,
            "elapsed_seconds": round(time.time() - started, 1)}


METRICS = ("n_members", "N_Priced", "N_EPS_any_sign", "N_PE",
           "N_PE_incl_extreme", "N_EPS_negative")


def group(records: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(str(record[field]), []).append(record)
    return {name: {metric: percentiles([r[metric] for r in rows])
                   for metric in METRICS}
            for name, rows in sorted(buckets.items())}


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    out_dir = os.getcwd()
    priced_cache = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--priced-cache" and i + 1 < len(argv):
            priced_cache = argv[i + 1]; i += 2
        else:
            i += 1

    started = time.time()
    wal_before = pit_cohort_scan.wal_bytes(db_path)
    conn = pit_cohort_scan.connect_ro(db_path)
    dates = pit_cohort_scan.grid_dates(conn)
    print(f"grid: {len(dates)} as-of dates {dates[0]} .. {dates[-1]}", flush=True)

    print("loading TTM rows ...", flush=True)
    table = load_ttm(conn)
    print(f"  {len(table):,} entities with a derived TTM", flush=True)

    if priced_cache and os.path.exists(priced_cache):
        with open(priced_cache, encoding="utf-8") as handle:
            priced = json.load(handle)
        print(f"priced universe: {len(priced)} dates from cache", flush=True)
    else:
        print("resolving the priced universe at every grid date ...", flush=True)
        priced = priced_per_date(conn, dates)
        if priced_cache:
            with open(priced_cache, "w", encoding="utf-8") as handle:
                json.dump(priced, handle)

    print("validating the in-memory ladder against the real selector ...", flush=True)
    validation = validate_against_selector(conn, table, dates)
    print(f"  {validation['agreed']}/{validation['checked']} agreed "
          f"({validation['pct_agreed']}%)", flush=True)

    print("measuring EPS coverage at the census dates ...", flush=True)
    coverage = eps_coverage(conn, table, priced)
    print("counting the sign census ...", flush=True)
    census = sign_census(conn)
    print("counting P/E cohorts over every peer set ...", flush=True)
    scoped = scoped_entities(conn)
    print(f"  {len(scoped):,} entities are inside the EPS ingest scope", flush=True)
    cohorts = peer_cohorts(conn, table, priced, scoped)
    conn.close()

    records = cohorts["records"]
    overall = {metric: percentiles([r[metric] for r in records])
               for metric in METRICS}
    comparison = {
        "peer_sets_measured": cohorts["n_peer_sets"],
        "ev_ebitda_measured": EV_EBITDA_MEASURED,
        "pe_measured": {
            "median_cohort": overall["N_PE"].get("median"),
            "pct_ge_3": overall["N_PE"].get("pct_ge_3"),
            "pct_ge_12": overall["N_PE"].get("pct_ge_12"),
            "leaves": ["price", "positive_point_in_time_ttm_eps"],
        },
        "pe_incl_extreme": {
            "median_cohort": overall["N_PE_incl_extreme"].get("median"),
            "pct_ge_3": overall["N_PE_incl_extreme"].get("pct_ge_3"),
            "pct_ge_12": overall["N_PE_incl_extreme"].get("pct_ge_12"),
        },
        "eps_any_sign": {
            "median_cohort": overall["N_EPS_any_sign"].get("median"),
            "pct_ge_3": overall["N_EPS_any_sign"].get("pct_ge_3"),
            "pct_ge_12": overall["N_EPS_any_sign"].get("pct_ge_12"),
            "what_it_is": ("the SIGNED EARNINGS YIELD cohort, not a P/E cohort. "
                           "Reported so the cost of the sign rule is a measured "
                           "number."),
        },
        "sample_scope": SAMPLE_SCOPE_PRICED,
        "scope_caveat": (
            "EPS was ingested for the PRICED universe only (2,533 entities), so "
            "a peer with no price has no EPS here. That costs the P/E COHORT "
            "nothing -- a member with no price could never contribute a P/E "
            "under any ingest -- but `priced_members_outside_eps_scope` counts "
            "the (member, date) pairs that WERE priced at that date and still "
            "fell outside the 6-date union the scope was enumerated over. That "
            "number is the only part of the scoping decision that costs "
            "coverage, and it is reported rather than assumed to be zero. "
            "`priced_members_in_scope_with_no_ttm` is the SOURCE's absence -- "
            "asked and not filed, or filed too long ago to be fresh -- and the "
            "two are counted apart because one is a decision and the other is "
            "a fact."),
        "priced_members_outside_eps_scope": cohorts["priced_members_outside_eps_scope"],
        "priced_members_in_scope_with_no_ttm":
            cohorts["priced_members_in_scope_with_no_ttm"],
        "entities_in_eps_scope": len(scoped),
    }

    payload: dict[str, Any] = {
        "db": os.path.abspath(db_path),
        "dates": dates,
        "spec": pit_eps.eps_ladder_spec(),
        "validation": validation,
        "eps_coverage_of_priced_universe": coverage,
        "sign_census": census,
        "peer_cohort_overall": overall,
        "by_rung": group(records, "rung"),
        "by_year": group(records, "year"),
        "comparison": comparison,
        "n_member_rows": cohorts["n_member_rows"],
        "wal_bytes_before": wal_before,
        "wal_bytes_after": pit_cohort_scan.wal_bytes(db_path),
        "elapsed_seconds": round(time.time() - started, 1),
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "eps_cohort.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, default=str)

    print("\nEPS COVERAGE OF THE PRICED UNIVERSE  (SURVIVOR_ONLY_DIAGNOSTIC)")
    for day, row in coverage.items():
        print(f"  {day}  priced={row['priced_universe']:5d}  "
              f"eps={row['pct_eps_any_sign']:6.2f}%  "
              f"P/E={row['pct_pe_available']:6.2f}%  "
              f"(+extreme {row['pct_pe_incl_extreme']:6.2f}%)  "
              f"neg={row['eps_negative']:5d}  zero={row['eps_zero']:4d}")
    print("\nSIGN CENSUS (derived TTM rows)")
    for case, n in census["ttm_rows"]["by_sign"].items():
        print(f"  {case:22s} {n:9,d}  "
              f"{census['ttm_rows']['pct_by_sign'][case]:6.2f}%")
    print(f"  {'sign_crossing (flag)':22s} "
          f"{census['sign_crossing']['ttm_rows_flagged']:9,d}  "
          f"{census['sign_crossing']['pct_of_ttm_rows']:6.2f}%")
    print(f"\nPEER COHORTS over {cohorts['n_peer_sets']:,} peer sets")
    print(f"  {'metric':22s} {'median':>7s} {'P(N>=3)':>9s} {'P(N>=12)':>9s}")
    print(f"  {'EV/EBITDA (quoted)':22s} "
          f"{EV_EBITDA_MEASURED['median_cohort']:7d} "
          f"{EV_EBITDA_MEASURED['pct_ge_3']:8.1f}% {EV_EBITDA_MEASURED['pct_ge_12']:8.1f}%")
    for label, metric in (("P/E", "N_PE"), ("P/E incl extreme", "N_PE_incl_extreme"),
                          ("EPS any sign", "N_EPS_any_sign"),
                          ("priced members", "N_Priced"),
                          ("all members", "n_members")):
        stats = overall[metric]
        print(f"  {label:22s} {stats.get('median', 0):7d} "
              f"{stats.get('pct_ge_3', 0):8.2f}% {stats.get('pct_ge_12', 0):8.2f}%")
    print(f"\nwrote {path} ({os.path.getsize(path):,} bytes) in "
          f"{payload['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
