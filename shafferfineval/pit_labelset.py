"""Versioned label sets -- what policy produced a `pit_label` row, and its cost.

`pit_label` was the only derived table in this store with no version column.
`pit_feature`, `pit_score` and `pit_peer_set` each carry a model or policy
version in their natural key, so a second policy lands beside the first; a
label's key was (listing_id, as_of_date, horizon) and nothing else, which means
a second labelling policy would have UPSERTED STRAIGHT OVER the first with no
record that it had. 2,334,978 rows would have changed meaning in place and
every number downstream of them would still have looked correct.

WHAT A LABEL NOW IDENTIFIES, and where each part lives:

    label policy version   pit_label.label_policy_version   (per row)
    price source           pit_label.price_source           (per row)
    price pull             pit_label.price_ingest_id        (per row)
    horizon                pit_label.horizon                (per row)
    creation run           pit_label.label_run_id           (per row)
    benchmark instrument   pit_label.benchmark_listing_id   (per row)
    cost assumption        pit_label.cost_assumption_set_id (per row)
    price-source version   pit_label_set.price_source_version
    corporate-action rule  pit_label_set.corporate_action_policy
    anchor rule            pit_label_set.anchor_rule
    terminal rule          pit_label_set.terminal_rule
    censoring rule         pit_label_set.censoring_rule
    sample scope           pit_label_set.sample_scope  AND the version string

Six fields sit in the registry rather than on the row for one reason, measured:
a row is ~135 bytes and the six strings are ~300, so stamping them per row
would have tripled `pit_label` on a volume with 10.7 GiB free. The version
string on the row is the join key to all of them.

WHY THE VERSION IS NOT IN THE UNIQUE KEY, which is the decision this module
had to make. It belongs there in principle -- a version in the key is exactly
what lets two policies coexist. It could not be put there in practice:

  * `pit_label`'s key is an INLINE UNIQUE, so its index is `sqlite_autoindex_
    pit_label_1`, and SQLite cannot drop an automatic index. Widening the key
    means rebuilding the table.
  * A rebuild of a 2.3M-row table holds the old and new copies at once and,
    on this volume, can never be tidied up afterwards: VACUUM needs a second
    copy of the whole 8.14 GiB file and there are 10.70 GiB free, so the old
    pages stay leaked inside the file for good. `label_key_rebuild_cost`
    measures all of it rather than asserting it.
  * The work order for this change forbade recreating the table.

So the version is enforced by TRIGGERS instead (declared in `pit_store`):
an insert without a policy version is refused, and an upsert that would change
a row's policy version is refused. The honest statement of what that buys:
two policies still cannot coexist in this table, but the second one now stops
the run instead of quietly replacing the first. Coexistence is a rebuild away
and the rebuild has a price tag attached.

SURVIVOR_ONLY_DIAGNOSTIC IS PART OF THE VERSION STRING, not only a registry
column. The price sample is 2,574 listings, every one alive in 2026, zero
series ending before 2020 and zero bankruptcy terminal reasons. A consumer who
joins `pit_label` and never opens `pit_label_set` still cannot read a row
without reading `survivor_only` in the version it is stamped with.

COSTS. `pit_cost_assumption` was empty, so `net_forward_return` had no defined
meaning. Two sets are seeded here and both are ASSUMPTIONS, flagged as such:
a zero set (the explicit null hypothesis) and one flat US-equity round-trip
scenario. Nothing in this store measures a spread -- there are no quotes, no
ADV and no borrow -- so a cost number here is a scenario that is exactly
reversible from the gross return, never a measurement.

Tables written: `pit_label_set`, `pit_label_run`, `pit_cost_assumption`, and
the three version columns of `pit_label`. Nothing else, and the frozen
production core is not imported.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
import time
from typing import Any, Mapping, Optional, Sequence

import pit_labels
import pit_path
import pit_prices
import pit_store
from pit_prices import checkpoint_wal, disk_free_gib, require_disk, wal_bytes
from pit_prices import _with_lock_retry
from pit_store import transaction

# --------------------------------------------------------------------------
# Versions and vocabulary
# --------------------------------------------------------------------------

#: The policy that produced the 2,334,978 rows already in the store, described
#: exactly as they were actually computed -- not as anyone would like them to
#: have been. Every string here is checked against `pit_labels`' constants by
#: `describe_policy_v1`, so the description cannot drift from the code.
LABEL_POLICY_V1 = pit_store.LABEL_POLICY_VERSION

#: Run kinds. `reconstructed` is a first-class value and is used for the
#: original build: it recorded no run id, so its run row is assembled after the
#: fact from what the rows themselves prove (computed_at, price_ingest_id) and
#: is labelled as reconstructed rather than presented as an observed run.
RUN_BUILD = "build"
RUN_BACKFILL_POLICY = "backfill_policy"
RUN_BACKFILL_BENCHMARK = "backfill_benchmark"
RUN_RECONSTRUCTED = "reconstructed"

#: Cost assumption set labels. Both are assumptions; the names say so.
COST_ZERO = "cost_v0_zero_frictionless"
COST_FLAT_10BPS = "cost_v1_us_equity_flat_10bps_per_side"

#: Which set `net_forward_return` is computed from unless a caller says otherwise.
DEFAULT_COST_SET = COST_FLAT_10BPS

#: Rows per backfill batch, expressed in LISTINGS rather than rows, because
#: that is how `pit_label` is physically ordered. The build wrote every date
#: for one listing before moving to the next, so a listing's ~990 rows are
#: contiguous by rowid and a listing-ranged UPDATE dirties contiguous pages.
#: The same work batched by as_of_date would touch 14,151 rows scattered across
#: every page of the table and rewrite the whole 316 MB table 165 times.
DEFAULT_BATCH_LISTINGS = 100

#: Batches between WAL checkpoints. Small: a WAL that is only looked at every
#: tenth batch is a WAL nobody is measuring.
DEFAULT_CHECKPOINT_EVERY = 4

#: Free-space floor in GiB. Identical to `pit_prices` and `pit_labels`, for the
#: same reason they are identical to each other: two floors is one of them
#: quietly overriding the other.
DEFAULT_MIN_FREE_GIB = pit_prices.DEFAULT_MIN_FREE_GIB

#: Deliberately empty, and the reason is worth more than an index would be.
#: The obvious one is `pit_label (label_policy_version)`, for the backfill's
#: resume test and the coverage GROUP BY. It was measured and rejected: the
#: column holds ONE value across 2,334,978 rows, so it selects nothing the
#: planner would use it for, it costs ~117 MB on a volume with 2.7 GiB above
#: its floor, and every row the backfill stamps rewrites its entry -- doubling
#: the write amplification of the exact operation it was meant to help. The two
#: queries that wanted it run twice per run and are happy to scan. The backfill
#: itself is driven by `listing_id BETWEEN ?`, which uses the index that is
#: already there.
SCHEMA = ""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _schema_objects(conn: sqlite3.Connection) -> set[str]:
    return {f"{r[1]}:{r[0]}" for r in conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index', 'trigger')")}


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create this module's tables, columns and index if absent. Idempotent.

    Runs `pit_store`'s ADD-COLUMN migrations too, because a store opened through
    `pit_prices.connect` never passes through `init_db` and would otherwise be
    missing `label_policy_version` entirely -- at which point the trigger that
    guards it cannot even be created.
    """
    before = _schema_objects(conn)
    added = pit_store.apply_migrations(conn)
    conn.executescript(pit_store.SCHEMA)
    conn.executescript(SCHEMA)
    conn.commit()
    created = sorted(_schema_objects(conn) - before)
    parts = []
    if added:
        parts.append(f"columns {added}")
    if created:
        parts.append(f"objects {created}")
    return "; ".join(parts) if parts else "already present"


def connect(db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """A store connection tuned for a file other processes may also hold."""
    return pit_prices.connect(db_path)


# --------------------------------------------------------------------------
# 1. The policy description -- read out of the code, never retyped
# --------------------------------------------------------------------------

def describe_policy_v1() -> dict[str, Any]:
    """The v1 labelling policy, assembled FROM `pit_labels`' live constants.

    Every number in the prose below is interpolated from the constant that
    actually governs it, so a future edit to `MAX_TERMINAL_ROLL_SESSIONS` makes
    this description change with it instead of becoming a comfortable lie. A
    description that has to be kept in sync by hand is a description that is
    wrong within a month.

    The one thing NOT derived: this describes the policy as it was when the
    2,334,978 existing rows were written. `assert_policy_matches_code` is what
    catches the case where the code has moved on and the stored description has
    not -- it compares the registered row against this function's output and
    refuses to backfill through a mismatch.
    """
    return {
        "label_policy_version": LABEL_POLICY_V1,
        "label_set_name": "Forward returns on adjusted closes, session-counted horizons",
        "price_source": pit_labels.PRICE_SOURCE,
        "price_source_version": pit_prices.PRICE_SOURCE_VERSION,
        "price_basis": "adjclose",
        "corporate_action_policy": (
            "Returns are ratios of adjusted closes, so a split or dividend inside "
            "the window rescales both endpoints by one common factor and cancels "
            "(verified window-invariant to 2.5e-7). Corporate actions are read "
            "only to RESOLVE A TERMINAL: pit_corporate_action rows of type "
            + ", ".join(sorted(pit_labels.TERMINAL_EVENT_TYPES)) +
            ". A cash terminal is converted to the adjusted basis by "
            "adjclose/close at the last real bar; a stock merger is "
            "exchange_ratio x the acquirer's own as-traded close, and is "
            + pit_store.OUTCOME_UNKNOWN +
            " when either the ratio or the acquirer's price is missing."
        ),
        "anchor_rule": (
            "The first REAL trade (adjclose > 0 and volume > 0) at or after the "
            "as-of date, rolled forward at most %d sessions; beyond that the "
            "as-of date is abandoned and counted, never stretched. anchor_price "
            "is an adjusted close as of the pull, never an as-traded price."
            % pit_labels.MAX_ANCHOR_ROLL_SESSIONS
        ),
        "terminal_rule": (
            "The first REAL trade at or after as-of + N sessions, rolled forward "
            "at most %d sessions -- larger than the anchor roll because the event "
            "that stops trading is the event worth measuring. Past that roll "
            "there is no observable exit price and the row is %s."
            % (pit_labels.MAX_TERMINAL_ROLL_SESSIONS, pit_store.OUTCOME_UNKNOWN)
        ),
        "censoring_rule": (
            "forward_return IS NULL if and only if censored = 1. A window whose "
            "terminal session is past the data horizon is censored, never "
            "truncated to the last available bar. A series whose last bar is "
            "within %d sessions of the data horizon is ALIVE and its unfinished "
            "windows are censored; one that stopped earlier TERMINATED and needs "
            "an outcome." % pit_labels.ALIVE_TAIL_SESSIONS
        ),
        "outcome_unknown_rule": (
            "A stopped series with no resolving corporate action is %s: censored "
            "= 1, forward_return NULL, the row present and counted. It is NEVER "
            "-100%%, never zero, never a carried-forward last price, and never "
            "dropped -- the names that disappear are not a random sample."
            % pit_store.OUTCOME_UNKNOWN
        ),
        "horizon_unit": "trading_sessions",
        "horizons_json": json.dumps(
            {h: pit_labels.HORIZON_SESSIONS[h] for h in pit_labels.HORIZON_ORDER},
            sort_keys=True),
        "calendar_market": pit_labels.MARKET,
        "sample_scope": pit_store.SAMPLE_SURVIVOR_ONLY,
        "sample_scope_note": (
            "The price sample behind these labels is survivor-only: 2,574 "
            "listings, every one alive in 2026, zero series ending before 2020, "
            "zero bankruptcy terminal reasons. The listing gate is fed by symbols "
            "a live vendor still answers for, so a company that died is one the "
            "gate could not accept. ANY return computed from these rows answers "
            "'among survivors', never 'in the market', and MAY NOT BE USED TO "
            "PROMOTE A MODEL. Excess return is worse than gross, not better: the "
            "stock leg excludes failures and the benchmark leg (a total-market "
            "ETF whose NAV absorbed every constituent failure) does not, so the "
            "difference is biased UPWARD by the whole of the survivorship gap."
        ),
        "may_promote_a_model": 0,
        "code_module": "pit_labels.py",
        "params_json": json.dumps({
            "label_source_version": pit_labels.LABEL_SOURCE_VERSION,
            "max_anchor_roll_sessions": pit_labels.MAX_ANCHOR_ROLL_SESSIONS,
            "max_terminal_roll_sessions": pit_labels.MAX_TERMINAL_ROLL_SESSIONS,
            "alive_tail_sessions": pit_labels.ALIVE_TAIL_SESSIONS,
            "terminal_event_types": sorted(pit_labels.TERMINAL_EVENT_TYPES),
            "path_model_version": pit_path.PATH_MODEL_VERSION,
            "derived_columns": {
                "benchmark_forward_return": "per-row benchmark_listing_id; NULL until seeded",
                "excess_return": "forward_return - benchmark_forward_return; NULL if either leg is NULL",
                "net_forward_return": "(1 + forward_return) * (1 - round_trip) - 1, per cost_assumption_set_id",
            },
        }, sort_keys=True),
        "supersedes": None,
    }


LABEL_SET_COLUMNS = (
    "label_policy_version", "label_set_name", "price_source",
    "price_source_version", "price_basis", "corporate_action_policy",
    "anchor_rule", "terminal_rule", "censoring_rule", "outcome_unknown_rule",
    "horizon_unit", "horizons_json", "calendar_market", "sample_scope",
    "sample_scope_note", "may_promote_a_model", "code_module", "params_json",
    "supersedes",
)


def register_label_set(conn: sqlite3.Connection,
                       policy: Optional[Mapping[str, Any]] = None) -> str:
    """Register one immutable label policy. Returns a status string.

    'registered' | 'already_registered' | 'conflict:<column>'.

    The table refuses UPDATE outright, so this cannot repair a row that differs
    -- and must not: a stored policy that disagrees with the code is a fact
    about the store worth stopping for, not a row to overwrite. `conflict` is
    returned rather than raised so a caller can decide; `assert_policy_matches_
    code` is the one that stops a build.
    """
    spec = dict(policy or describe_policy_v1())
    version = spec["label_policy_version"]
    existing = label_set(conn, version)
    if existing is not None:
        for column in LABEL_SET_COLUMNS:
            if str(existing[column]) != str(spec.get(column)):
                return f"conflict:{column}"
        return "already_registered"

    def _write() -> None:
        with transaction(conn):
            conn.execute(
                "INSERT INTO pit_label_set ({}, created_at) VALUES ({})".format(
                    ", ".join(LABEL_SET_COLUMNS),
                    ", ".join("?" * (len(LABEL_SET_COLUMNS) + 1))),
                tuple(spec.get(c) for c in LABEL_SET_COLUMNS) + (_now(),))

    _with_lock_retry(_write)
    return "registered"


def label_set(conn: sqlite3.Connection, version: str) -> Optional[sqlite3.Row]:
    """One registered policy, or None."""
    return conn.execute(
        "SELECT * FROM pit_label_set WHERE label_policy_version = ?",
        (version,)).fetchone()


def assert_policy_matches_code(conn: sqlite3.Connection,
                               version: str = LABEL_POLICY_V1) -> dict[str, Any]:
    """Raise unless `version` is registered AND still describes the live code.

    The label equivalent of `pit_peer_meta.assert_peer_sets_fresh`, and it
    exists for the same reason: a stored description of a policy is only useful
    if something checks it against the policy. A build should call this before
    its first write.
    """
    row = label_set(conn, version)
    if row is None:
        raise RuntimeError(
            f"label policy {version!r} is not registered in pit_label_set. Run "
            f"`python pit_labelset.py --register` before writing labels; an "
            f"unregistered version names nothing a reader can look up.")
    if version != LABEL_POLICY_V1:
        return {"version": version, "checked": False,
                "note": "no code description exists for this version"}
    spec = describe_policy_v1()
    drift = [c for c in LABEL_SET_COLUMNS if str(row[c]) != str(spec.get(c))]
    if drift:
        raise RuntimeError(
            f"label policy {version!r} as registered no longer matches the code "
            f"that claims to implement it; columns differ: {drift}. The stored "
            f"row is immutable by design -- register a NEW version and label "
            f"with that, rather than editing either side into agreement.")
    return {"version": version, "checked": True, "drift": []}


# --------------------------------------------------------------------------
# 2. Cost assumptions
# --------------------------------------------------------------------------

#: The two seeded sets. PER SIDE is the convention and it is stated in the row
#: itself, because a bps number with no side convention is off by two half the
#: time. borrow and financing are zero and that is a STATEMENT: these labels
#: describe an unlevered long-only cash position, so there is nothing to borrow
#: and nothing to finance. A short book would need its own set.
COST_SETS: tuple[dict[str, Any], ...] = (
    {
        "label": COST_ZERO,
        "asset_class": "equity",
        "commission_bps": 0.0,
        "spread_bps": 0.0,
        "borrow_bps": 0.0,
        "financing_spread_bps": 0.0,
        "is_assumption": 1,
        "source": "pit_labelset.COST_SETS",
        "note": (
            "The explicit null hypothesis. net_forward_return under this set is "
            "exactly forward_return. It exists so that 'no costs were applied' "
            "can be a recorded decision with an id rather than a NULL that might "
            "mean anything."
        ),
    },
    {
        "label": COST_FLAT_10BPS,
        "asset_class": "equity",
        "commission_bps": 0.0,
        "spread_bps": 10.0,
        "borrow_bps": 0.0,
        "financing_spread_bps": 0.0,
        "is_assumption": 1,
        "source": "pit_labelset.COST_SETS",
        "note": (
            "A SCENARIO, not a measurement. This store holds no quotes, no ADV "
            "and no borrow rates, so nothing here can measure a spread; 10 bps "
            "per side is a flat mid-range figure for US listed equity over "
            "2013-2026 and its error is structured, not random. It is far too "
            "low for a small illiquid name and several times too high for a "
            "mega-cap, so it FLATTERS small-cap strategies and penalises "
            "large-cap ones. Commission is 0 because the round trip is priced "
            "at the modern zero-commission retail schedule, which is wrong for "
            "the early part of the window in the same direction for every name. "
            "The transform is exactly reversible: forward_return is kept beside "
            "net_forward_return on every row."
        ),
    },
)


def cost_convention(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The convention document stored with a cost set. One place, one meaning."""
    per_side = float(spec.get("commission_bps") or 0.0) + float(spec.get("spread_bps") or 0.0)
    return {
        "bps_are": "per_side",
        "round_trip_sides": 2,
        "round_trip_fraction": round(2.0 * per_side / 10_000.0, 10),
        "applied_as": "net = (1 + forward_return) * (1 - round_trip_fraction) - 1",
        "applies_to": "one open and one close of an unlevered long cash position",
        "priced": ["commission", "half_spread_each_side"],
        "not_priced": ["market_impact", "adv_participation", "borrow",
                       "financing", "taxes", "fees_on_the_benchmark_etf"],
        "per_window_not_per_annum": (
            "The cost is what ONE window costs to trade. It is not scaled by "
            "horizon: at 1D it is 20 bps against a one-session return and swamps "
            "it, at 12M it is 20 bps against a year. Do not annualise it."
        ),
    }


def round_trip_fraction(row: Mapping[str, Any]) -> float:
    """Round-trip cost as a fraction of notional, from a cost-assumption row."""
    per_side = float(row["commission_bps"] or 0.0) + float(row["spread_bps"] or 0.0)
    return 2.0 * per_side / 10_000.0


def seed_cost_assumptions(conn: sqlite3.Connection) -> dict[str, str]:
    """Insert the documented cost sets. Idempotent. Returns label -> status."""
    out: dict[str, str] = {}
    for spec in COST_SETS:
        existing = conn.execute(
            "SELECT assumption_set_id FROM pit_cost_assumption WHERE label = ?",
            (spec["label"],)).fetchone()
        if existing is not None:
            out[spec["label"]] = "already_seeded"
            continue

        def _write(spec=spec) -> None:
            with transaction(conn):
                conn.execute(
                    """INSERT INTO pit_cost_assumption
                           (label, effective_from, asset_class, commission_bps,
                            spread_bps, borrow_bps, financing_spread_bps,
                            is_assumption, source, created_at,
                            cost_convention_json, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (spec["label"], None, spec["asset_class"],
                     spec["commission_bps"], spec["spread_bps"],
                     spec["borrow_bps"], spec["financing_spread_bps"],
                     spec["is_assumption"], spec["source"], _now(),
                     json.dumps(cost_convention(spec), sort_keys=True),
                     spec["note"]))

        _with_lock_retry(_write)
        out[spec["label"]] = "seeded"
    return out


def cost_set(conn: sqlite3.Connection, label: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pit_cost_assumption WHERE label = ?", (label,)).fetchone()


# --------------------------------------------------------------------------
# 3. Label runs
# --------------------------------------------------------------------------

def start_label_run(conn: sqlite3.Connection, run_kind: str, *,
                    label_policy_version: str = LABEL_POLICY_V1,
                    code_module: Optional[str] = None,
                    started_at: Optional[str] = None,
                    note: Optional[str] = None,
                    **fields) -> int:
    """Open a `pit_label_run` row and return its id."""
    def _write() -> int:
        with transaction(conn):
            cursor = conn.execute(
                """INSERT INTO pit_label_run
                       (label_policy_version, run_kind, started_at, code_module,
                        code_revision, n_listings, n_dates, n_rows_written,
                        benchmark_listing_id, cost_assumption_set_id, status,
                        evidence_json, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)""",
                (label_policy_version, run_kind, started_at or _now(),
                 code_module, fields.get("code_revision"),
                 fields.get("n_listings"), fields.get("n_dates"),
                 fields.get("n_rows_written"), fields.get("benchmark_listing_id"),
                 fields.get("cost_assumption_set_id"),
                 pit_store.dumps(fields.get("evidence")), note))
            return int(cursor.lastrowid)
    return _with_lock_retry(_write)


def finish_label_run(conn: sqlite3.Connection, label_run_id: int,
                     status: str = "complete", **fields) -> None:
    """Close a run, recording what it actually wrote."""
    def _write() -> None:
        with transaction(conn):
            conn.execute(
                """UPDATE pit_label_run
                      SET finished_at = ?, status = ?,
                          n_listings = COALESCE(?, n_listings),
                          n_dates = COALESCE(?, n_dates),
                          n_rows_written = COALESCE(?, n_rows_written),
                          benchmark_listing_id = COALESCE(?, benchmark_listing_id),
                          cost_assumption_set_id = COALESCE(?, cost_assumption_set_id),
                          evidence_json = COALESCE(?, evidence_json),
                          note = COALESCE(?, note)
                    WHERE label_run_id = ?""",
                (_now(), status, fields.get("n_listings"), fields.get("n_dates"),
                 fields.get("n_rows_written"), fields.get("benchmark_listing_id"),
                 fields.get("cost_assumption_set_id"),
                 pit_store.dumps(fields.get("evidence")), fields.get("note"),
                 label_run_id))
    _with_lock_retry(_write)


def reconstruct_original_run(conn: sqlite3.Connection,
                             label_policy_version: str = LABEL_POLICY_V1) -> dict[str, Any]:
    """Register the run that wrote the unstamped rows, marked `reconstructed`.

    The original build recorded no run id -- the column did not exist -- so the
    only honest thing to attach those rows to is a run assembled from what the
    rows themselves prove: the window of their `computed_at` values, the price
    ingests they cite, and their listing and date counts. It is stamped
    `reconstructed` and says so in its note, because a reconstruction presented
    as an observation is worse than no row at all.

    Returns {'status', 'label_run_id', 'evidence'}. Idempotent: an existing
    reconstructed run for this policy is reused, never duplicated.
    """
    existing = conn.execute(
        """SELECT label_run_id FROM pit_label_run
            WHERE label_policy_version = ? AND run_kind = ?
            ORDER BY label_run_id LIMIT 1""",
        (label_policy_version, RUN_RECONSTRUCTED)).fetchone()
    row = conn.execute(
        """SELECT COUNT(*) AS n_rows, COUNT(DISTINCT listing_id) AS n_listings,
                  COUNT(DISTINCT as_of_date) AS n_dates,
                  MIN(computed_at) AS first_computed,
                  MAX(computed_at) AS last_computed
             FROM pit_label""").fetchone()
    ingests = [int(r[0]) for r in conn.execute(
        """SELECT DISTINCT price_ingest_id FROM pit_label
            WHERE price_ingest_id IS NOT NULL ORDER BY price_ingest_id""")]
    evidence = {
        "n_rows_at_reconstruction": int(row["n_rows"] or 0),
        "n_listings": int(row["n_listings"] or 0),
        "n_dates": int(row["n_dates"] or 0),
        "computed_at_first": row["first_computed"],
        "computed_at_last": row["last_computed"],
        "price_ingest_ids_cited": ingests,
        "derivation": ("assembled from pit_label itself; the original build "
                       "predates pit_label_run and recorded nothing"),
    }
    if existing is not None:
        return {"status": "already_reconstructed",
                "label_run_id": int(existing[0]), "evidence": evidence}

    run_id = start_label_run(
        conn, RUN_RECONSTRUCTED, label_policy_version=label_policy_version,
        code_module="pit_labels.py", started_at=row["first_computed"],
        n_listings=evidence["n_listings"], n_dates=evidence["n_dates"],
        n_rows_written=evidence["n_rows_at_reconstruction"], evidence=evidence,
        note=("RECONSTRUCTED, not observed. This row stands for the build that "
              "wrote the store's first 2.3M labels before pit_label_run "
              "existed. Its times are the rows' own computed_at window, not a "
              "recorded start and finish."))
    finish_label_run(conn, run_id, status="reconstructed",
                     n_rows_written=evidence["n_rows_at_reconstruction"])
    return {"status": "reconstructed", "label_run_id": run_id, "evidence": evidence}


# --------------------------------------------------------------------------
# 4. The backfill
# --------------------------------------------------------------------------

def unstamped_rows(conn: sqlite3.Connection) -> int:
    """How many label rows still carry no policy version."""
    return int(conn.execute(
        "SELECT COUNT(*) FROM pit_label WHERE label_policy_version IS NULL"
    ).fetchone()[0])


def _listing_ranges(conn: sqlite3.Connection, batch: int) -> list[tuple[int, int]]:
    """Contiguous listing-id windows covering every labelled listing.

    Ranged rather than enumerated because `pit_label` is physically ordered by
    listing: the build wrote all 990 of one listing's rows before moving on, so
    a range UPDATE walks contiguous pages. Batching the same work by as_of_date
    would scatter 14,151 rows across the whole table per batch.
    """
    ids = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT listing_id FROM pit_label ORDER BY listing_id")]
    return [(ids[i], ids[min(i + batch, len(ids)) - 1])
            for i in range(0, len(ids), batch)]


def backfill_label_policy(conn: sqlite3.Connection, *,
                          db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                          label_policy_version: str = LABEL_POLICY_V1,
                          label_run_id: Optional[int] = None,
                          batch_listings: int = DEFAULT_BATCH_LISTINGS,
                          checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
                          min_free_gib: float = DEFAULT_MIN_FREE_GIB,
                          progress: bool = True) -> dict[str, Any]:
    """Stamp every unversioned label row with the policy that produced it.

    ONLY rows where `label_policy_version IS NULL` are touched, so this is
    resumable and can never relabel a row that already claims a policy -- the
    trigger would refuse that anyway, and a backfill that depends on a trigger
    to stop it is a backfill that has already decided to be wrong.

    Disk is re-checked before every batch and every checkpoint's BUSY FLAG is
    read: a busy checkpoint does nothing at all while returning success-shaped
    output, which is how a WAL reached 9.3 GB on this volume unnoticed.
    """
    if label_run_id is None:
        raise ValueError("label_run_id is required: a stamped row must name the "
                         "run that stamped it")
    assert_policy_matches_code(conn, label_policy_version)

    started = time.time()
    ranges = _listing_ranges(conn, batch_listings)
    total_before = unstamped_rows(conn)
    file_before = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    summary: dict[str, Any] = {
        "rows_unstamped_before": total_before,
        "n_batches": len(ranges),
        "rows_stamped": 0,
        "checkpoints": 0,
        "checkpoints_busy": 0,
        "wal_peak_bytes": wal_bytes(db_path),
        "free_gib_min": disk_free_gib(db_path),
        "db_bytes_before": file_before,
        "stopped": "completed",
    }

    for i, (low, high) in enumerate(ranges, start=1):
        try:
            free = require_disk(db_path, min_free_gib)
        except RuntimeError as exc:
            summary["stopped"] = f"disk_floor: {exc}"
            break
        summary["free_gib_min"] = min(summary["free_gib_min"], free)

        def _write(low=low, high=high) -> int:
            with transaction(conn):
                cursor = conn.execute(
                    """UPDATE pit_label
                          SET label_policy_version = ?, label_run_id = ?
                        WHERE listing_id BETWEEN ? AND ?
                          AND label_policy_version IS NULL""",
                    (label_policy_version, label_run_id, low, high))
                return int(cursor.rowcount or 0)

        summary["rows_stamped"] += _with_lock_retry(_write)
        summary["wal_peak_bytes"] = max(summary["wal_peak_bytes"], wal_bytes(db_path))

        if i % checkpoint_every == 0 or i == len(ranges):
            state = checkpoint_wal(conn, db_path)
            summary["checkpoints"] += 1
            summary["checkpoints_busy"] += int(state["busy"])
            if progress:
                print(f"  batch {i}/{len(ranges)} listings {low}-{high}: "
                      f"{summary['rows_stamped']:,} stamped, "
                      f"wal {state['wal_after'] / 2 ** 20:.1f} MiB "
                      f"(busy={state['busy']}), free {free:.2f} GiB")

    summary["rows_unstamped_after"] = unstamped_rows(conn)
    summary["db_bytes_after"] = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    summary["db_growth_mib"] = round(
        (summary["db_bytes_after"] - file_before) / 2 ** 20, 1)
    summary["wal_peak_mib"] = round(summary["wal_peak_bytes"] / 2 ** 20, 1)
    summary["elapsed_seconds"] = round(time.time() - started, 1)
    return summary


# --------------------------------------------------------------------------
# 5. What the key change would have cost
# --------------------------------------------------------------------------

def label_key_rebuild_cost(conn: sqlite3.Connection,
                           db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                           sample: int = 20_000) -> dict[str, Any]:
    """Price the rebuild that would put the policy version IN the UNIQUE key.

    Measured, not asserted, because "it is expensive" is not a decision and a
    number is. The method: serialise a real sample of `pit_label` rows and their
    three index entries, extrapolate to the full table, and compare the total
    against the volume's actual free space and against what VACUUM would need.

    Why a rebuild is the only route: the key is an INLINE UNIQUE, so its index
    is `sqlite_autoindex_pit_label_1`, and SQLite cannot drop an automatic
    index. ALTER TABLE cannot add or widen a constraint. Adding a second, wider
    UNIQUE INDEX changes nothing -- the narrow autoindex still refuses the
    second policy's row.
    """
    n_rows = int(conn.execute("SELECT COUNT(*) FROM pit_label").fetchone()[0])
    payload = conn.execute(
        """SELECT AVG(LENGTH(CAST(listing_id AS TEXT)) + LENGTH(as_of_date)
                    + LENGTH(horizon) + COALESCE(LENGTH(anchor_date), 0)
                    + COALESCE(LENGTH(end_date), 0) + LENGTH(price_source)
                    + LENGTH(computed_at) + COALESCE(LENGTH(terminal_reason), 0)
                    + COALESCE(LENGTH(label_policy_version), 0)) AS text_bytes
             FROM (SELECT * FROM pit_label LIMIT ?)""", (sample,)).fetchone()
    text_bytes = float(payload["text_bytes"] or 0.0)
    # 6 REAL columns at 8 bytes, 6 small integers, and a ~24-byte record header.
    row_bytes = text_bytes + 6 * 8 + 6 * 2 + 24
    # Three indexes: the autoindex (listing_id, as_of_date, horizon),
    # idx_pit_label_lookup (as_of_date, horizon), idx_pit_label_listing
    # (listing_id, horizon), each plus a rowid and a cell header.
    index_bytes = (10 + 10 + 3 + 8) + (10 + 3 + 8) + (10 + 3 + 8) + 3 * 6
    table_mib = n_rows * row_bytes / 2 ** 20
    index_mib = n_rows * index_bytes / 2 ** 20
    free_gib = disk_free_gib(db_path)
    db_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    return {
        "n_rows": n_rows,
        "sampled_rows": min(sample, n_rows),
        "bytes_per_row_estimated": round(row_bytes, 1),
        "table_copy_mib": round(table_mib, 1),
        "index_copy_mib": round(index_mib, 1),
        "peak_extra_mib": round(table_mib + index_mib, 1),
        # Bytes as well as MiB: a fixture of 42 rows rounds to 0.0 MiB, and a
        # cost that reads as zero is a cost nothing can be asserted about.
        "peak_extra_bytes": int(n_rows * (row_bytes + index_bytes)),
        "wal_exposure_mib": round(table_mib + index_mib, 1),
        "wal_exposure_bytes": int(n_rows * (row_bytes + index_bytes)),
        "wal_note": ("a table rebuild is one transaction: every page of the new "
                     "table and all three indexes sits in the WAL until it "
                     "commits, and no intermediate checkpoint can truncate it"),
        "free_gib_now": round(free_gib, 2),
        "db_gib": round(db_bytes / 2 ** 30, 2),
        "vacuum_needs_gib": round(2 * db_bytes / 2 ** 30, 2),
        "vacuum_possible": (2 * db_bytes / 2 ** 30) < free_gib,
        "old_pages_leaked_mib": round(table_mib + index_mib, 1),
        "leak_note": ("the dropped table's pages return to the freelist, not to "
                      "the volume; only VACUUM shrinks the file, and VACUUM "
                      "needs a second copy of the whole store"),
        "verdict": (
            "affordable" if (table_mib + index_mib) / 1024.0 < (free_gib - DEFAULT_MIN_FREE_GIB)
            else "refused_by_disk_floor"),
    }


# --------------------------------------------------------------------------
# 6. Reporting
# --------------------------------------------------------------------------

def policy_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Who claims what, across `pit_label`. The audit this module exists for."""
    by_policy = [
        {"label_policy_version": r["label_policy_version"], "n_rows": int(r["n"]),
         "n_listings": int(r["n_listings"]), "n_runs": int(r["n_runs"])}
        for r in conn.execute(
            """SELECT label_policy_version, COUNT(*) AS n,
                      COUNT(DISTINCT listing_id) AS n_listings,
                      COUNT(DISTINCT label_run_id) AS n_runs
                 FROM pit_label GROUP BY label_policy_version ORDER BY n DESC""")]
    sets = [dict(r) for r in conn.execute(
        """SELECT label_policy_version, sample_scope, may_promote_a_model,
                  price_source, created_at FROM pit_label_set
            ORDER BY created_at""")]
    runs = [dict(r) for r in conn.execute(
        """SELECT label_run_id, run_kind, status, n_rows_written,
                  benchmark_listing_id, cost_assumption_set_id, started_at
             FROM pit_label_run ORDER BY label_run_id""")]
    costs = [dict(r) for r in conn.execute(
        """SELECT assumption_set_id, label, commission_bps, spread_bps,
                  is_assumption FROM pit_cost_assumption ORDER BY assumption_set_id""")]
    unregistered = [p["label_policy_version"] for p in by_policy
                    if p["label_policy_version"] is not None
                    and p["label_policy_version"] not in {s["label_policy_version"] for s in sets}]
    return {
        "by_policy": by_policy,
        "rows_unstamped": unstamped_rows(conn),
        "policies_registered": sets,
        "policies_used_but_unregistered": unregistered,
        "runs": runs,
        "cost_assumption_sets": costs,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Register the policy, seed costs, stamp the rows, then report."""
    args = list(sys.argv[1:] if argv is None else argv)

    def opt(name: str, default: Any) -> Any:
        return args[args.index(name) + 1] if name in args else default

    db_path = str(opt("--db", pit_store.DEFAULT_PIT_DB_PATH))
    batch = int(opt("--batch-listings", DEFAULT_BATCH_LISTINGS))
    min_free = float(opt("--min-free-gib", DEFAULT_MIN_FREE_GIB))
    do_register = "--register" in args
    do_backfill = "--backfill" in args

    conn = connect(db_path)
    print(ensure_schema(conn))
    print(f"disk: {disk_free_gib(db_path):.2f} GiB free, floor {min_free:.2f} GiB, "
          f"wal {wal_bytes(db_path) / 2 ** 20:.1f} MiB")

    if do_register or do_backfill:
        print("register:", register_label_set(conn))
        print("costs:", json.dumps(seed_cost_assumptions(conn), sort_keys=True))

    if do_backfill:
        recon = reconstruct_original_run(conn)
        print("original run:", recon["status"], "id", recon["label_run_id"])
        run_id = start_label_run(
            conn, RUN_BACKFILL_POLICY, code_module="pit_labelset.py",
            note="stamping rows written before pit_label carried a policy version")
        summary = backfill_label_policy(
            conn, db_path=db_path, label_run_id=recon["label_run_id"],
            batch_listings=batch, min_free_gib=min_free)
        finish_label_run(conn, run_id, n_rows_written=summary["rows_stamped"],
                         evidence=summary)
        print(json.dumps(summary, indent=2, sort_keys=True))

    print("\nkey-rebuild cost (measured, NOT performed):")
    print(json.dumps(label_key_rebuild_cost(conn, db_path), indent=2, sort_keys=True))
    print("\ncoverage:")
    print(json.dumps(policy_coverage(conn), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
