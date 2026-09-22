"""Peer-set metadata: the priced-coverage count, and the guard that keeps it honest.

WHY THIS MODULE EXISTS. `pit_peer_set` carries two counts per cohort. `n_members`
is the cohort that shapes the percentile -- every issuer that legitimately
existed on that date, ticker or no ticker, survivor or not. `n_price_resolvable`
is how much of that same cohort could also be priced, scored, labelled and held.
The gap between them is the single most important honesty number in the store:
it is the distance between "Shaffer ranked this company against its industry"
and "Shaffer could have taken a position in that industry".

That second count was written WRONG, and not by a modelling mistake. The 165
cross-sections were built between 13:07:15 and 13:14:52 on 2026-09-21 while a
different process was still filling `pit_listing`, which it finished at 13:22:04.
Every cohort therefore recorded whatever fraction of the listing table happened
to exist at the instant its date was written: 2 price-resolvable member slots
across all 276 cohorts at 2013-01-31, rising monotonically with the CLOCK rather
than with the market, to 3,359 at 2026-09-18. Read naively, the stored numbers
say equity coverage grew 1,600-fold over thirteen years. It did not. The build
was racing an ingest.

`n_members` is untouched by that race -- membership needs only fundamentals --
and it is verifiably intact: SUM(n_members) equals COUNT(*) in `pit_peer_member`
exactly, 3,069,260 both ways. Only the priced COUNT is wrong, and only the count:
not one entity was dropped from a cohort for lack of a price, which is the
property that makes this repairable in place at all.

THE TEST. `n_price_resolvable` is recomputed with `pit_identity.scored_universe_as_of`
-- the same function `pit_peers.build_peer_sets` called, and, more importantly,
the gate that decides who receives a score, a forward label and a position in the
replay. Any other test (say, "has a listing row") would make this column a number
no downstream step consumes. Note precisely what the gate includes, because the
column's name understates it: a listing valid on the date with `first_trade_date`
at or before it, a real bar with volume > 0 within 10 days, and NO quarantine.
So n_price_resolvable is priced AND investable. Two entities in this store carry
`no_exit_record = 1`; they are peers and are not price-resolvable, by design.

AND THE COUNT IS A FLOOR, NOT A MEASUREMENT. `pit_listing` holds 2,574 verified
listings and every one of them is alive in 2026: no series ends before 2020 and
no terminal reason is a bankruptcy. A peer that genuinely traded on a 2013 date
and was delisted in 2016 therefore has no listing row, cannot pass the gate, and
is counted as not price-resolvable. So every number this module produces from
prices is SURVIVOR_ONLY_DIAGNOSTIC and is a LOWER BOUND on the priced coverage
the cohort actually had. The label is written into `price_test_params_json` on
every build row rather than left in a report, because the column will outlive
the report. It may not be used to promote a model.

WHY IN PLACE AND NOT A NEW peer_set_version. `peer_set_version` names the COHORT
CONSTRUCTION POLICY -- the SIC rung ladder, the crosswalk, MIN_COHORT_N. None of
those changed. Bumping it would assert a policy change that did not happen and
would fork `pit_feature`, `pit_score` and `pit_replay_run` into two lineages
identical in every policy they name, at the cost of duplicating 3.07M membership
rows (~90 MiB measured) that are already correct. The one real argument for a new
version -- that rewriting destroys the record of what was there -- is answered
directly instead: every prior (n_members, n_price_resolvable) pair is copied into
`pit_peer_set_meta_history` before it is overwritten, 39,038 rows for about
2.5 MiB. The stale value is preserved as evidence; it is simply no longer served
as if it were a measurement.

THE GUARD, so this cannot recur. A cohort built before prices existed was
previously undetectable: `created_at` told you WHEN a cohort was written but
nothing about what the store contained at that moment. `pit_peer_set_build` now
records, per date, the `pit_listing` count at the time the priced count was
computed, alongside the count of listings that actually had a bar.
`peer_set_staleness()` compares those to the live counts and reports any date
whose metadata predates listings that exist now; `assert_peer_sets_fresh()`
raises on one, and the feature build is expected to call it before its first
write. A date with no build row at all is reported as `unrecorded`, which is
exactly the state the original 165 dates were in -- absence of provenance is
itself the detection, not a silent pass.

DISK. This module rewrites two integers on 39,038 existing rows and appends about
39,200 small rows. Measured cost: the whole pass grows the database by roughly
6 MiB retained, with a WAL peak bounded by the checkpoint cadence (default every
20 dates, ~2 MiB). It reads `PRAGMA wal_checkpoint(TRUNCATE)`'s return value on
every checkpoint and reports `busy`, because a checkpoint that silently did
nothing is how a 9.3 GB WAL once filled this volume.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import pit_identity
import pit_peers
import pit_store
import statlib

# --------------------------------------------------------------------------
# Versions and defaults
# --------------------------------------------------------------------------

#: Names the price-resolvability test, so a stored count says which gate produced
#: it. Bump it (never edit it) if the gate ever changes; a build row carrying the
#: old name then reads as a different measurement rather than a comparable one.
PRICE_TEST_VERSION = "price_test_scored_universe_v1"

#: The reason stamped on the 2026-09-21 repair. Kept as a constant so the history
#: rows written by that repair can be selected exactly.
REASON_LISTING_RACE = "built_while_pit_listing_was_still_filling"

#: A metadata rewrite that is not the race repair (a re-measure after more price
#: history arrives, for instance).
REASON_REMEASURE = "remeasured_against_current_listings"

#: Stamped on every count drawn from `pit_price_bar`. The price sample is
#: survivor-only -- 2,574 listings, all alive in 2026 -- so a priced count is a
#: floor under the coverage a cohort actually had, not a measurement of it.
SURVIVOR_ONLY_DIAGNOSTIC = "SURVIVOR_ONLY_DIAGNOSTIC"

#: Checkpoint cadence, in dates. 20 dates is ~4,700 updated rows, which bounds
#: the WAL at roughly 2 MiB between truncations.
CHECKPOINT_EVERY_DATES = 20

STATUS_REBUILT = "rebuilt"
STATUS_UNCHANGED = "unchanged"
STATUS_REFUSED = "refused"
STATUS_DRY_RUN = "dry_run"

#: Staleness verdicts. `unrecorded` is not a benign default: it is the state a
#: cohort built before this module existed is in, and it must not pass a gate.
FRESH_OK = "ok"
FRESH_STALE = "stale"
FRESH_UNRECORDED = "unrecorded"


SCHEMA = """
-- One row per (date, model, peer-set policy) recording WHAT THE STORE CONTAINED
-- when that date's priced count was computed. `created_at` on pit_peer_set says
-- when a cohort was written; only this says whether prices existed yet.
CREATE TABLE IF NOT EXISTS pit_peer_set_build (
    as_of_date             TEXT NOT NULL,
    model_version          TEXT NOT NULL,
    peer_set_version       TEXT NOT NULL,
    cohorts_built_at       TEXT,            -- MIN(created_at) of the date's cohorts
    metadata_built_at      TEXT NOT NULL,   -- when n_price_resolvable was computed
    n_listings_at_build    INTEGER NOT NULL,-- COUNT(*) pit_listing at that moment
    n_priced_listings_at_build INTEGER,     -- listings holding at least one bar
    n_cohorts              INTEGER NOT NULL,
    n_member_slots         INTEGER NOT NULL,
    n_priced_entities      INTEGER NOT NULL,-- distinct entities passing the gate
    price_test             TEXT NOT NULL,
    price_test_params_json TEXT NOT NULL,
    crosswalk_hash         TEXT,
    UNIQUE (as_of_date, model_version, peer_set_version)
);

-- The record an in-place rewrite would otherwise destroy. Append-only by use:
-- one row per cohort per rewrite, keyed on the rewrite's timestamp.
CREATE TABLE IF NOT EXISTS pit_peer_set_meta_history (
    peer_set_id            INTEGER NOT NULL REFERENCES pit_peer_set(peer_set_id),
    rewritten_at           TEXT NOT NULL,
    n_members_before       INTEGER NOT NULL,
    n_price_resolvable_before INTEGER NOT NULL,
    n_members_after        INTEGER NOT NULL,
    n_price_resolvable_after  INTEGER NOT NULL,
    reason                 TEXT NOT NULL,
    price_test             TEXT NOT NULL,
    UNIQUE (peer_set_id, rewritten_at)
);

CREATE INDEX IF NOT EXISTS idx_pit_peer_set_meta_hist_when
    ON pit_peer_set_meta_history (rewritten_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the provenance tables if absent. Idempotent, and cheap to call."""
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------
# The price-resolvability test
# --------------------------------------------------------------------------

def price_test_params(max_filing_age_days: int = pit_identity.DEFAULT_PEER_MAX_FILING_AGE_DAYS,
                      max_price_age_days: int = pit_identity.DEFAULT_PRICE_MAX_AGE_DAYS,
                      allowed_confidence: Sequence[str] = (pit_identity.CONFIDENCE_PROVED,
                                                           pit_identity.CONFIDENCE_INFERRED),
                      ) -> dict[str, Any]:
    """The gate's parameters, serialised onto every build row.

    Stored rather than assumed because all three move the count: widening
    `max_price_age_days` prices halted lines, and admitting `unverified`
    listings prices ticker matches that were never proved.
    """
    return {
        "price_test": PRICE_TEST_VERSION,
        "function": "pit_identity.scored_universe_as_of",
        "max_filing_age_days": int(max_filing_age_days),
        "max_price_age_days": int(max_price_age_days),
        "allowed_confidence": list(allowed_confidence),
        "excludes_quarantined": True,
        "counts": "distinct entity_id",
        # The price sample this count is drawn from is SURVIVOR-ONLY, and the
        # count inherits that. Stamped on every stored build row rather than
        # left in a report, because a reader of the column will not otherwise
        # know its bound.
        "price_sample": SURVIVOR_ONLY_DIAGNOSTIC,
        "price_sample_note": (
            "pit_listing holds 2,574 verified listings, ALL alive in 2026, with no "
            "series ending before 2020 and no bankruptcy terminal reason. A peer "
            "that genuinely traded on the as-of date and has since been delisted "
            "has no listing row here, so n_price_resolvable is a LOWER BOUND on "
            "the true historical priced coverage of its cohort, never an estimate "
            "of it, and it may not be used to promote a model."),
    }


def price_resolvable_entities(conn: sqlite3.Connection, as_of: str, **params) -> set[int]:
    """The entities that were priced AND investable on `as_of`.

    Deliberately delegates to `pit_identity.scored_universe_as_of` rather than
    re-expressing the gate: a cohort's priced count and the replay's scored
    universe must be the same population or the reported coverage describes a
    set nothing downstream uses. Returns entity_ids, de-duplicated -- that
    function yields one row per LISTING, and a dual-class issuer is one peer.
    """
    rows = pit_identity.scored_universe_as_of(
        conn, as_of,
        max_filing_age_days=int(params.get(
            "max_filing_age_days", pit_identity.DEFAULT_PEER_MAX_FILING_AGE_DAYS)),
        max_price_age_days=int(params.get(
            "max_price_age_days", pit_identity.DEFAULT_PRICE_MAX_AGE_DAYS)),
        allowed_confidence=tuple(params.get(
            "allowed_confidence", (pit_identity.CONFIDENCE_PROVED,
                                   pit_identity.CONFIDENCE_INFERRED))),
    )
    return {int(r["entity_id"]) for r in rows}


# --------------------------------------------------------------------------
# Build provenance -- the guard
# --------------------------------------------------------------------------

def _listing_census(conn: sqlite3.Connection) -> tuple[int, Optional[int]]:
    """(listings, listings holding at least one price bar) as of right now.

    The second number matters on its own: a listing row with no bars prices
    nothing, so a build that saw 2,574 listings and 0 bars is as stale as a
    build that saw no listings at all.
    """
    n_listings = int(conn.execute("SELECT COUNT(*) FROM pit_listing").fetchone()[0])
    row = conn.execute(
        "SELECT COUNT(DISTINCT listing_id) FROM pit_price_bar").fetchone()
    return n_listings, (int(row[0]) if row else None)


def record_peer_set_build(conn: sqlite3.Connection, as_of: str,
                          model_version: str, peer_set_version: str,
                          n_priced_entities: int,
                          *,
                          params: Optional[Mapping[str, Any]] = None,
                          n_listings: Optional[int] = None,
                          n_priced_listings: Optional[int] = None,
                          crosswalk_hash: Optional[str] = None) -> dict[str, Any]:
    """Stamp one date's metadata with the store census that produced it.

    Called both by the metadata rebuild and by `pit_peers.build_peer_sets`, so a
    freshly built date is provenanced at birth and never enters the `unrecorded`
    state the original 165 dates are in. Upserts: re-measuring a date replaces
    its census, because the census describes the CURRENT count on the row.
    """
    day = str(as_of)[:10]
    spec = dict(params or price_test_params())
    if n_listings is None or n_priced_listings is None:
        census = _listing_census(conn)
        n_listings = census[0] if n_listings is None else n_listings
        n_priced_listings = census[1] if n_priced_listings is None else n_priced_listings
    summary = conn.execute(
        """SELECT COUNT(*) AS n_cohorts,
                  COALESCE(SUM(n_members), 0) AS n_member_slots,
                  MIN(created_at) AS built_at,
                  MIN(crosswalk_hash) AS hash
             FROM pit_peer_set
            WHERE as_of_date = ? AND model_version = ? AND peer_set_version = ?""",
        (day, model_version, peer_set_version),
    ).fetchone()
    with pit_store.transaction(conn):
        conn.execute(
            """INSERT INTO pit_peer_set_build
                   (as_of_date, model_version, peer_set_version, cohorts_built_at,
                    metadata_built_at, n_listings_at_build, n_priced_listings_at_build,
                    n_cohorts, n_member_slots, n_priced_entities, price_test,
                    price_test_params_json, crosswalk_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (as_of_date, model_version, peer_set_version) DO UPDATE SET
                    cohorts_built_at = COALESCE(pit_peer_set_build.cohorts_built_at,
                                                excluded.cohorts_built_at),
                    metadata_built_at = excluded.metadata_built_at,
                    n_listings_at_build = excluded.n_listings_at_build,
                    n_priced_listings_at_build = excluded.n_priced_listings_at_build,
                    n_cohorts = excluded.n_cohorts,
                    n_member_slots = excluded.n_member_slots,
                    n_priced_entities = excluded.n_priced_entities,
                    price_test = excluded.price_test,
                    price_test_params_json = excluded.price_test_params_json,
                    crosswalk_hash = COALESCE(excluded.crosswalk_hash,
                                              pit_peer_set_build.crosswalk_hash)""",
            (day, model_version, peer_set_version,
             summary["built_at"] if summary else None,
             pit_store._now(), int(n_listings),
             None if n_priced_listings is None else int(n_priced_listings),
             int(summary["n_cohorts"]) if summary else 0,
             int(summary["n_member_slots"]) if summary else 0,
             int(n_priced_entities), spec.get("price_test", PRICE_TEST_VERSION),
             pit_store.dumps(spec),
             crosswalk_hash or (summary["hash"] if summary else None)),
        )
    return {"as_of": day, "status": "recorded", "n_listings_at_build": int(n_listings),
            "n_priced_entities": int(n_priced_entities)}


def peer_set_staleness(conn: sqlite3.Connection,
                       *,
                       model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                       peer_set_version: str = pit_peers.PEER_SET_VERSION,
                       dates: Optional[Sequence[str]] = None) -> dict[str, Any]:
    """Which dates' priced counts were computed against a smaller listing table.

    The comparison is deliberately one-sided. A build that saw FEWER listings
    than exist now undercounts and is `stale`; a build that saw MORE has had
    listings retracted since, which is also a mismatch and is reported, but as
    `stale` for the same reason -- the count no longer describes the store.
    A date with no build row is `unrecorded`, never `ok`: the 165 dates this
    module was written for had no provenance at all, and treating missing
    evidence as a pass would reproduce the exact failure.
    """
    n_listings_now, n_priced_listings_now = _listing_census(conn)
    built = {r["as_of_date"]: r for r in conn.execute(
        """SELECT * FROM pit_peer_set_build
            WHERE model_version = ? AND peer_set_version = ?""",
        (model_version, peer_set_version))}
    if dates is None:
        dates = [r[0] for r in conn.execute(
            """SELECT DISTINCT as_of_date FROM pit_peer_set
                WHERE model_version = ? AND peer_set_version = ?
                ORDER BY as_of_date""", (model_version, peer_set_version))]
    verdicts: dict[str, list[str]] = {FRESH_OK: [], FRESH_STALE: [],
                                      FRESH_UNRECORDED: []}
    detail: list[dict[str, Any]] = []
    for day in dates:
        day = str(day)[:10]
        row = built.get(day)
        if row is None:
            verdicts[FRESH_UNRECORDED].append(day)
            detail.append({"as_of": day, "verdict": FRESH_UNRECORDED,
                           "n_listings_at_build": None,
                           "n_listings_now": n_listings_now})
            continue
        at_build = int(row["n_listings_at_build"])
        priced_at_build = row["n_priced_listings_at_build"]
        drifted = (at_build != n_listings_now
                   or (priced_at_build is not None
                       and int(priced_at_build) != (n_priced_listings_now or 0)))
        verdict = FRESH_STALE if drifted else FRESH_OK
        verdicts[verdict].append(day)
        if verdict == FRESH_STALE:
            detail.append({"as_of": day, "verdict": verdict,
                           "n_listings_at_build": at_build,
                           "n_listings_now": n_listings_now,
                           "n_priced_listings_at_build": priced_at_build,
                           "n_priced_listings_now": n_priced_listings_now})
    return {
        "n_dates": len(dates),
        "n_ok": len(verdicts[FRESH_OK]),
        "n_stale": len(verdicts[FRESH_STALE]),
        "n_unrecorded": len(verdicts[FRESH_UNRECORDED]),
        "stale_dates": verdicts[FRESH_STALE],
        "unrecorded_dates": verdicts[FRESH_UNRECORDED],
        "n_listings_now": n_listings_now,
        "n_priced_listings_now": n_priced_listings_now,
        "detail": detail[:50],
        "fresh": not verdicts[FRESH_STALE] and not verdicts[FRESH_UNRECORDED],
    }


def assert_peer_sets_fresh(conn: sqlite3.Connection,
                           dates: Optional[Sequence[str]] = None,
                           *,
                           model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                           peer_set_version: str = pit_peers.PEER_SET_VERSION,
                           ) -> dict[str, Any]:
    """Raise unless every date's priced count matches the store as it stands.

    The feature build is expected to call this before its first write. It exists
    because `delete_peer_sets` refuses once one feature row points at a cohort:
    the window in which stale metadata can be corrected closes permanently at
    that moment, so the check has to happen on the near side of it.
    """
    report = peer_set_staleness(conn, model_version=model_version,
                                peer_set_version=peer_set_version, dates=dates)
    if not report["fresh"]:
        raise RuntimeError(
            "peer-set metadata is not current: %d stale, %d unrecorded of %d dates "
            "(pit_listing now holds %s rows). Run "
            "`python pit_peer_meta.py --rebuild` before building features; once a "
            "pit_feature row references a cohort, delete_peer_sets refuses and the "
            "counts can no longer be corrected. First offenders: %s"
            % (report["n_stale"], report["n_unrecorded"], report["n_dates"],
               report["n_listings_now"],
               (report["stale_dates"] + report["unrecorded_dates"])[:5]))
    return report


# --------------------------------------------------------------------------
# The rebuild
# --------------------------------------------------------------------------

def _features_referencing(conn: sqlite3.Connection, model_version: str,
                          peer_set_version: str) -> int:
    return int(conn.execute(
        """SELECT COUNT(*) FROM pit_feature
            WHERE peer_set_id IN (SELECT peer_set_id FROM pit_peer_set
                                   WHERE model_version = ? AND peer_set_version = ?)""",
        (model_version, peer_set_version)).fetchone()[0])


def _membership_counts(conn: sqlite3.Connection, as_of: str, model_version: str,
                       peer_set_version: str, priced: set[int]
                       ) -> dict[int, tuple[int, int]]:
    """Recount one date's cohorts straight from `pit_peer_member`.

    Both numbers come from the membership table itself, never from the stored
    `n_members`: a recount that trusted the value it is auditing could not
    detect a cohort whose stored size had drifted from its rows.
    """
    counts: dict[int, list[int]] = {}
    for peer_set_id, entity_id in conn.execute(
        """SELECT m.peer_set_id, m.entity_id
             FROM pit_peer_set ps JOIN pit_peer_member m
                  ON m.peer_set_id = ps.peer_set_id
            WHERE ps.as_of_date = ? AND ps.model_version = ?
              AND ps.peer_set_version = ?""",
        (str(as_of)[:10], model_version, peer_set_version),
    ):
        slot = counts.get(peer_set_id)
        if slot is None:
            slot = counts[peer_set_id] = [0, 0]
        slot[0] += 1
        if entity_id in priced:
            slot[1] += 1
    return {k: (v[0], v[1]) for k, v in counts.items()}


def rebuild_peer_set_metadata(conn: sqlite3.Connection,
                              *,
                              dates: Optional[Sequence[str]] = None,
                              model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                              peer_set_version: str = pit_peers.PEER_SET_VERSION,
                              reason: str = REASON_LISTING_RACE,
                              checkpoint_every: int = CHECKPOINT_EVERY_DATES,
                              dry_run: bool = False,
                              params: Optional[Mapping[str, Any]] = None,
                              progress: Optional[Callable[[Mapping[str, Any]], None]] = None,
                              ) -> dict[str, Any]:
    """Recompute `n_members` and `n_price_resolvable` on every cohort. In place.

    MEMBERSHIP IS NOT TOUCHED. Not one row of `pit_peer_member` is inserted or
    deleted; the only statements this issues against cohort data are UPDATEs of
    two integers. That is the whole reason an in-place repair is honest here --
    the cohorts were right, the count of how many of them could be priced was a
    photograph of a half-filled listing table.

    Every prior pair is copied into `pit_peer_set_meta_history` before it is
    overwritten, so the rewrite is auditable without duplicating 3.07M
    membership rows into a second peer_set_version that would differ from the
    first in no policy it names.

    Refuses outright while any `pit_feature` row references these cohorts: a
    percentile computed against a denominator whose reported size then changes
    underneath it is worse than a stale count. Transactions are one date long
    and the WAL is checkpointed on a cadence with `busy` READ, not assumed.
    """
    started = time.time()
    spec = dict(params or price_test_params())
    db_path = pit_peers._db_path_of(conn)
    ensure_schema(conn)

    referencing = _features_referencing(conn, model_version, peer_set_version)
    if referencing:
        return {"status": STATUS_REFUSED,
                "reason": "features_reference_these_peer_sets",
                "n_features": referencing,
                "note": ("pit_feature rows point at these cohorts; their reported "
                         "denominator may not change underneath them. Re-run the "
                         "replay instead.")}

    if dates is None:
        dates = [r[0] for r in conn.execute(
            """SELECT DISTINCT as_of_date FROM pit_peer_set
                WHERE model_version = ? AND peer_set_version = ?
                ORDER BY as_of_date""", (model_version, peer_set_version))]
    dates = [str(d)[:10] for d in dates]

    if not dry_run and db_path:
        pit_peers.require_free_space(db_path)

    rewritten_at = pit_store._now()
    n_listings, n_priced_listings = _listing_census(conn)

    totals = {
        "n_dates": len(dates), "n_cohorts": 0, "n_changed": 0,
        "n_member_slots_before": 0, "n_member_slots_after": 0,
        "n_priced_before": 0, "n_priced_after": 0,
        "n_members_disagreed": 0, "n_below_min_cohort": 0,
    }
    member_drift: list[dict[str, Any]] = []
    below_min: list[dict[str, Any]] = []
    per_date: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []

    for index, day in enumerate(dates, start=1):
        t0 = time.time()
        priced = price_resolvable_entities(conn, day, **spec)
        stored = conn.execute(
            """SELECT peer_set_id, rung, key, n_members, n_price_resolvable
                 FROM pit_peer_set
                WHERE as_of_date = ? AND model_version = ? AND peer_set_version = ?""",
            (day, model_version, peer_set_version)).fetchall()
        recount = _membership_counts(conn, day, model_version, peer_set_version, priced)

        updates: list[tuple[int, int, int]] = []
        history: list[tuple] = []
        date_totals = {"n_cohorts": len(stored), "slots_before": 0, "slots_after": 0,
                       "priced_before": 0, "priced_after": 0, "changed": 0}
        for row in stored:
            pid = int(row["peer_set_id"])
            n_before, priced_before = int(row["n_members"]), int(row["n_price_resolvable"])
            n_after, priced_after = recount.get(pid, (0, 0))
            date_totals["slots_before"] += n_before
            date_totals["slots_after"] += n_after
            date_totals["priced_before"] += priced_before
            date_totals["priced_after"] += priced_after
            if n_after != n_before:
                totals["n_members_disagreed"] += 1
                member_drift.append({"peer_set_id": pid, "as_of": day,
                                     "rung": row["rung"], "key": row["key"],
                                     "stored": n_before, "recounted": n_after})
            if n_after < pit_peers.MIN_COHORT_N:
                totals["n_below_min_cohort"] += 1
                below_min.append({"peer_set_id": pid, "as_of": day,
                                  "rung": row["rung"], "key": row["key"],
                                  "n_members": n_after})
            if n_after != n_before or priced_after != priced_before:
                date_totals["changed"] += 1
                updates.append((n_after, priced_after, pid))
                history.append((pid, rewritten_at, n_before, priced_before,
                                n_after, priced_after, reason,
                                spec.get("price_test", PRICE_TEST_VERSION)))

        if not dry_run and updates:
            def _write(updates=updates, history=history) -> None:
                with pit_store.transaction(conn):
                    conn.executemany(
                        """INSERT INTO pit_peer_set_meta_history
                               (peer_set_id, rewritten_at, n_members_before,
                                n_price_resolvable_before, n_members_after,
                                n_price_resolvable_after, reason, price_test)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT (peer_set_id, rewritten_at) DO NOTHING""",
                        history)
                    conn.executemany(
                        """UPDATE pit_peer_set
                              SET n_members = ?, n_price_resolvable = ?
                            WHERE peer_set_id = ?""", updates)
            pit_peers._retry_on_lock(_write)

        if not dry_run:
            record_peer_set_build(conn, day, model_version, peer_set_version,
                                  len(priced), params=spec, n_listings=n_listings,
                                  n_priced_listings=n_priced_listings)

        totals["n_cohorts"] += date_totals["n_cohorts"]
        totals["n_changed"] += date_totals["changed"]
        totals["n_member_slots_before"] += date_totals["slots_before"]
        totals["n_member_slots_after"] += date_totals["slots_after"]
        totals["n_priced_before"] += date_totals["priced_before"]
        totals["n_priced_after"] += date_totals["priced_after"]
        entry = {"as_of": day, "n_cohorts": date_totals["n_cohorts"],
                 "n_priced_entities": len(priced),
                 "priced_slots_before": date_totals["priced_before"],
                 "priced_slots_after": date_totals["priced_after"],
                 "n_changed": date_totals["changed"],
                 "elapsed_s": round(time.time() - t0, 3)}
        per_date.append(entry)
        if progress is not None:
            progress(entry)

        if not dry_run and checkpoint_every and index % int(checkpoint_every) == 0:
            result = pit_peers.checkpoint_wal(conn, db_path)
            result["after_date"] = day
            checkpoints.append(result)
            if progress is not None:
                progress({"checkpoint": result})

    if not dry_run:
        final = pit_peers.checkpoint_wal(conn, db_path)
        final["after_date"] = "final"
        checkpoints.append(final)

    report = {
        "status": STATUS_DRY_RUN if dry_run else (
            STATUS_REBUILT if totals["n_changed"] else STATUS_UNCHANGED),
        "rewritten_at": rewritten_at,
        "reason": reason,
        "price_test": spec,
        "n_listings_at_build": n_listings,
        "n_priced_listings_at_build": n_priced_listings,
        "checkpoints": checkpoints,
        "n_checkpoints_busy": sum(1 for c in checkpoints if c.get("busy")),
        "member_drift": member_drift[:20],
        "below_min_cohort": below_min[:20],
        "per_date": per_date,
        "elapsed_s": round(time.time() - started, 3),
    }
    report.update(totals)
    if db_path:
        report["disk_after"] = pit_peers.disk_status(db_path)
    return report


# --------------------------------------------------------------------------
# Assertions the rebuild must survive
# --------------------------------------------------------------------------

def verify_metadata(conn: sqlite3.Connection,
                    *,
                    model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                    peer_set_version: str = pit_peers.PEER_SET_VERSION,
                    expected_member_rows: Optional[int] = None,
                    expected_member_entities: Optional[int] = None,
                    ) -> dict[str, Any]:
    """The four invariants a metadata-only rewrite may not break.

    1. SUM(n_members) equals COUNT(*) in pit_peer_member EXACTLY. Not `close`:
       the column is a cached row count and any gap means a membership row moved.
    2. No cohort below MIN_COHORT_N. A cohort that fell under 12 would mean the
       rebuild had silently filtered members, which is the survivorship bias
       this whole store exists to avoid.
    3. n_price_resolvable <= n_members on every row, and never negative. The
       priced set is a SUBSET of the cohort; a count that exceeded it would mean
       entities outside the cohort were counted.
    4. The membership table is byte-for-byte the size it was: same row count and
       same distinct-entity count. This is what proves no entity was dropped for
       lack of a price.
    """
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, **detail) -> None:
        checks.append({"check": name, "ok": bool(ok), **detail})

    sum_members = int(conn.execute(
        """SELECT COALESCE(SUM(n_members), 0) FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?""",
        (model_version, peer_set_version)).fetchone()[0])
    member_rows = int(conn.execute(
        """SELECT COUNT(*) FROM pit_peer_member
            WHERE peer_set_id IN (SELECT peer_set_id FROM pit_peer_set
                                   WHERE model_version = ? AND peer_set_version = ?)""",
        (model_version, peer_set_version)).fetchone()[0])
    record("sum_n_members_equals_member_rows", sum_members == member_rows,
           sum_n_members=sum_members, member_rows=member_rows)

    below = int(conn.execute(
        """SELECT COUNT(*) FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ? AND n_members < ?""",
        (model_version, peer_set_version, pit_peers.MIN_COHORT_N)).fetchone()[0])
    record("no_cohort_below_min_cohort_n", below == 0,
           min_cohort_n=pit_peers.MIN_COHORT_N, n_below=below)

    bad_priced = int(conn.execute(
        """SELECT COUNT(*) FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
              AND (n_price_resolvable > n_members OR n_price_resolvable < 0)""",
        (model_version, peer_set_version)).fetchone()[0])
    record("priced_is_a_subset_of_members", bad_priced == 0, n_violations=bad_priced)

    distinct_entities = int(conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM pit_peer_member").fetchone()[0])
    total_member_rows = int(conn.execute(
        "SELECT COUNT(*) FROM pit_peer_member").fetchone()[0])
    if expected_member_rows is not None:
        record("member_rows_unchanged", total_member_rows == int(expected_member_rows),
               before=int(expected_member_rows), after=total_member_rows)
    if expected_member_entities is not None:
        record("member_entities_unchanged",
               distinct_entities == int(expected_member_entities),
               before=int(expected_member_entities), after=distinct_entities)

    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "sum_n_members": sum_members, "member_rows": total_member_rows,
            "distinct_member_entities": distinct_entities}


# --------------------------------------------------------------------------
# Priced coverage -- the number nobody has seen yet
# --------------------------------------------------------------------------

def _ratio_stats(pairs: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """Slot-weighted and cohort-median coverage for a group of cohorts.

    Both are reported because they answer different questions. The slot-weighted
    ratio is the coverage of the AVERAGE PERCENTILE TAKEN (big cohorts are used
    more); the median cohort ratio is the coverage of the TYPICAL INDUSTRY. They
    diverge exactly when priced names cluster in large cohorts, which they do.
    """
    members = sum(p[0] for p in pairs)
    priced = sum(p[1] for p in pairs)
    ratios = sorted(p[1] / p[0] for p in pairs if p[0] > 0)
    return {
        "n_cohorts": len(pairs),
        "n_member_slots": members,
        "n_priced_slots": priced,
        "slot_weighted_pct": round(100.0 * priced / members, 2) if members else None,
        "cohort_median_pct": (round(100.0 * statlib.median(ratios), 2)
                              if ratios else None),
        "cohort_p25_pct": (round(100.0 * statlib.percentile(ratios, 0.25), 2)
                           if ratios else None),
        "cohort_p75_pct": (round(100.0 * statlib.percentile(ratios, 0.75), 2)
                           if ratios else None),
        "n_cohorts_zero_priced": sum(1 for p in pairs if p[1] == 0),
        "pct_cohorts_zero_priced": (round(100.0 * sum(1 for p in pairs if p[1] == 0)
                                          / len(pairs), 2) if pairs else None),
        "n_cohorts_priced_ge_min": sum(1 for p in pairs
                                       if p[1] >= pit_peers.MIN_COHORT_N),
        "pct_cohorts_priced_ge_min": (round(100.0 * sum(1 for p in pairs if p[1]
                                                        >= pit_peers.MIN_COHORT_N)
                                            / len(pairs), 2) if pairs else None),
    }


def coverage_report(conn: sqlite3.Connection,
                    *,
                    model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
                    peer_set_version: str = pit_peers.PEER_SET_VERSION,
                    ) -> dict[str, Any]:
    """Priced coverage of every cohort, by rung and by year.

    This is the ratio the stale column was hiding: what fraction of the cohort a
    percentile is taken against could itself have been bought. A cohort with 300
    members and 8 priced names is a legitimate denominator for the RANK and a
    near-empty opportunity set for the PORTFOLIO, and the two facts have to be
    reported separately or a backtest silently conflates them.
    """
    rows = conn.execute(
        """SELECT as_of_date, rung, n_members, n_price_resolvable
             FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?""",
        (model_version, peer_set_version)).fetchall()
    by_rung: dict[str, list[tuple[int, int]]] = {}
    by_year: dict[str, list[tuple[int, int]]] = {}
    overall: list[tuple[int, int]] = []
    for row in rows:
        pair = (int(row["n_members"]), int(row["n_price_resolvable"]))
        overall.append(pair)
        by_rung.setdefault(row["rung"], []).append(pair)
        by_year.setdefault(str(row["as_of_date"])[:4], []).append(pair)
    return {
        "overall": _ratio_stats(overall),
        "by_rung": {rung: _ratio_stats(pairs)
                    for rung, pairs in sorted(
                        by_rung.items(),
                        key=lambda kv: pit_peers.RUNG_LADDER.index(kv[0])
                        if kv[0] in pit_peers.RUNG_LADDER else 99)},
        "by_year": {year: _ratio_stats(pairs)
                    for year, pairs in sorted(by_year.items())},
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_table(title: str, rows: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
    print(f"\n{title}")
    print("  %-8s %8s %12s %12s %9s %9s %9s %9s %9s"
          % ("group", "cohorts", "slots", "priced", "slot%", "med%", "p25%", "p75%", "zero%"))
    for name, stats in rows:
        print("  %-8s %8d %12d %12d %9s %9s %9s %9s %9s"
              % (name, stats["n_cohorts"], stats["n_member_slots"],
                 stats["n_priced_slots"], stats["slot_weighted_pct"],
                 stats["cohort_median_pct"], stats["cohort_p25_pct"],
                 stats["cohort_p75_pct"], stats["pct_cohorts_zero_priced"]))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Rebuild, check or report peer-set metadata.

        python pit_peer_meta.py --check          # staleness verdict only
        python pit_peer_meta.py --dry-run        # measure, write nothing
        python pit_peer_meta.py --rebuild        # repair in place
        python pit_peer_meta.py --coverage       # the by-rung / by-year table
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    mode = None
    limit: Optional[int] = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--db" and index + 1 < len(argv):
            db_path = argv[index + 1]; index += 2
        elif token == "--limit" and index + 1 < len(argv):
            limit = int(argv[index + 1]); index += 2
        elif token in ("--check", "--dry-run", "--rebuild", "--coverage", "--verify"):
            mode = token; index += 1
        else:
            print(f"unknown argument: {token}"); return 2
    if mode is None:
        print(main.__doc__)
        return 2

    conn = pit_store.connect(db_path)
    pit_peers.prepare_connection(conn)
    ensure_schema(conn)
    try:
        if mode == "--check":
            report = peer_set_staleness(conn)
            print(json.dumps({k: v for k, v in report.items() if k != "detail"},
                             indent=2, default=str)[:4000])
            return 0 if report["fresh"] else 1
        if mode == "--verify":
            print(json.dumps(verify_metadata(conn), indent=2, default=str))
            return 0
        if mode == "--coverage":
            report = coverage_report(conn)
            _print_table("BY RUNG", list(report["by_rung"].items()))
            _print_table("BY YEAR", list(report["by_year"].items()))
            _print_table("OVERALL", [("all", report["overall"])])
            return 0

        dates = None
        if limit:
            dates = [r[0] for r in conn.execute(
                """SELECT DISTINCT as_of_date FROM pit_peer_set
                    ORDER BY as_of_date LIMIT ?""", (limit,))]
        report = rebuild_peer_set_metadata(
            conn, dates=dates, dry_run=(mode == "--dry-run"),
            progress=lambda e: print("  " + json.dumps(e, default=str))
            if "checkpoint" in e else None)
        report.pop("per_date", None)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["status"] != STATUS_REFUSED else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
