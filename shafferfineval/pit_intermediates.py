"""Intermediate-state persistence, and the attribution engine that needs it.

WHY THIS EXISTS
---------------
The replay computes a great deal and keeps almost none of it. `pit_score` holds
a final number and three JSON blobs; `pit_feature` holds a raw value and a
normalized one. Everything between -- the pillar scores, the effective weights,
the cohort distribution, the primitive VALUES that were actually selected -- is
built in memory and dropped. So a score that moves

    Shaffer Score  +58 -> +64

cannot be decomposed. Not "is hard to decompose": cannot. The numbers that would
explain it were never written down, and re-deriving them means re-running the
point-in-time selector for a historical date.

THE ONE SYSTEM
--------------
Historical replay and the live daily job are the SAME SYSTEM asking the same
question at two ends of one timeline:

    historical   what was knowable THEN?
    live         what just became knowable NOW?

Both are answered by `available_date`, which is the shared event clock: a fact
enters the model on the session the latency policy assigned it, and that rule
does not know or care whether the as-of date is 2019 or today. The same
dependency graph (`pit_derive`), the same latency policy (`pit_policy`), the
same normalization (`pit_normalization`). Therefore ONE set of tables and ONE
attribution engine serve both, and `explain_change` below is called identically
by a replay writing 2019-06-28 and by tonight's job writing today. There is no
research engine and no separate production engine to drift apart.
`worked_example()` runs both and prints them side by side to prove it.

THE HARD PART
-------------
A naive delta does not reconcile, and the way it fails is the way that matters.
The score is a weighted blend over AVAILABLE pillars with renormalisation, so
between two dates BOTH the pillar values and the WEIGHTS move. Attributing the
whole move to pillar values silently reports an availability change as an
economic one -- exactly the failure the UI rules forbid, where VGPD -> GPD
renders as "Shaffer Score fell 11 points" when nothing economic happened at all.

So the decomposition separates the effects and reconciles EXACTLY:

    dScore = SUM_i [ w_i,t-1 * dF_i ]   FACTOR       what actually changed
           + SUM_i [ F_i,t-1 * dw_i ]   WEIGHT       renormalisation
           + SUM_i [ dw_i * dF_i ]      INTERACTION  the cross term
           + SUM_entered [ w_i,t * F_i,t ]           ENTRY
           - SUM_exited  [ w_i,t-1 * F_i,t-1 ]       EXIT

over i present at BOTH dates for the first three sums. That is an algebraic
identity, not an approximation: for any i in both,

    w_t F_t - w_s F_s == w_s (F_t - F_s) + F_s (w_t - w_s) + (w_t - w_s)(F_t - F_s)

so the terms sum to the observed change to floating-point tolerance, and
`test_pit_intermediates.py` asserts it on generated cases rather than trusting
this paragraph. A pillar entering or leaving is reported as its OWN term,
separately from both the factor and the weight effect, because it is neither.

THE INVARIANT THIS MODULE HOLDS
-------------------------------
    Every point of an observed score change is assigned to exactly one named
    term, and no term may be rendered as an economic move unless it is one.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not write. There is no `sqlite3.connect` in this file, no `import
sqlite3`, and every SQL string it emits is either a SELECT (executed) or an
INSERT/DDL (returned as text and parameters for another process to run). Two
workflows are mid-flight on the store with seven live processes; `ensure_schema`
is written for someone to run LATER, when nothing is mid-write, and nothing in
this module calls it. `pit_feature` and `pit_score` stay at 0 rows.

It fits no models. Every statistic quoted here was measured elsewhere and is
cited with its measurement.

Stdlib only.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

import pit_derive
import pit_invariants

__all__ = [
    # vocabulary
    "INTERMEDIATE_SCHEMA_VERSION", "PILLARS", "PILLAR_LETTER", "PILLAR_LABEL",
    "MAJOR_WEIGHTS_DECLARED", "COMPANY_SCALE", "SUBFACTORS", "SUBFACTOR_LETTER",
    "AVAIL_RESOLVED", "AVAIL_MISSING",
    # states
    "STATE_ECONOMIC_CHANGE", "STATE_COMPARABILITY_CHANGED", "STATE_UNCHANGED",
    "STATE_NO_PRIOR", "STATE_NO_SCORE", "STATE_RECONCILIATION_FAILED",
    # causes
    "CAUSE_ECONOMIC", "CAUSE_PEER_SET_CHANGED", "CAUSE_BECAME_AVAILABLE",
    "CAUSE_BECAME_UNAVAILABLE", "CAUSE_RENORMALISATION",
    "CAUSE_DECLARED_WEIGHTS_CHANGED", "CAUSE_CLAMP", "CAUSE_SECTOR_OVERLAY",
    # schema -- specified, NOT applied
    "PIT_INTERMEDIATE_DDL_V2", "INTERMEDIATE_MIGRATIONS", "INSERT_SQL",
    "ensure_schema", "grain_arithmetic", "NOT_STORED",
    # snapshot model
    "Part", "ScoreSnapshot", "HedgeSnapshot", "CohortStat",
    "signature_of", "pillar_mask_of", "subfactor_mask_of",
    "original_weight_coverage", "effective_weights", "blend_value",
    "resolved_inputs", "RESOLVED_INPUT_STOPS",
    # row builders (build; do not execute)
    "cohort_stat_row", "pillar_score_rows", "score_additions",
    "hedge_snapshot_row", "pending_writes",
    # reading (SELECT only)
    "load_snapshot", "load_cohort_stat", "read_only_sql",
    # attribution
    "Term", "BlendDecomposition", "Attribution",
    "decompose_blend", "explain_change", "render_change",
    # the three UI rules
    "comparability_message", "horizon_forecast", "MEASURED_EVIDENCE",
    "supported_horizons", "hedge_research_status", "OPTION_ARCHIVE_START",
    # demonstration
    "FIXTURE", "worked_example",
]


# ==========================================================================
# (0) VOCABULARY
# ==========================================================================

INTERMEDIATE_SCHEMA_VERSION = "pit_intermediate_v1"

#: The four major pillars, IN DECLARED-WEIGHT ORDER. The order is part of the
#: contract: it is the order of the letters in a score signature and of the
#: digits in a pillar mask, and a signature whose letter order drifted between
#: two runs would silently compare two different things.
PILLARS: tuple[str, ...] = ("valuation", "growth", "profitability", "debt")

PILLAR_LETTER: dict[str, str] = {
    "valuation": "V", "growth": "G", "profitability": "P", "debt": "D",
}

PILLAR_LABEL: dict[str, str] = {
    "valuation": "Valuation",
    "growth": "Growth",
    "profitability": "Profitability",
    "debt": "Debt/quality",
}

#: The declared major weights, RESTATED rather than imported. `pit_derive` set
#: the precedent and the reason holds here: this module must not import the
#: frozen production core, because a research module that imports it can be
#: changed into one that edits it. `test_pit_intermediates.py` asserts these
#: equal `company_scoring.MAJOR_WEIGHTS`, so the copy cannot drift in silence.
MAJOR_WEIGHTS_DECLARED: dict[str, float] = {
    "valuation": 0.40,
    "growth": 0.25,
    "profitability": 0.20,
    "debt": 0.15,
}

#: CompanyScore = COMPANY_SCALE * CompanyRawScore, clamped. Restated, as above.
COMPANY_SCALE = 0.75
COMPANY_SCORE_MIN, COMPANY_SCORE_MAX = -75.0, 75.0
FINAL_MIN, FINAL_MAX = -100.0, 100.0

#: The sub-factors inside each pillar, in the production core's own order.
#: The detailed mask is built from this and answers a different question from
#: the headline signature: the signature says WHICH ECONOMIC PILLARS survived,
#: the mask says WHAT ACTUALLY PRODUCED THE NUMBER. Two companies both scored
#: 'VGPD' whose growth rested on three components and on one are not the same
#: measurement, and P(Return | Score, Signature) is only answerable later if
#: the distinction was written down at the time.
#:
#: Valuation carries two members because `pit_invariants` declares two: the
#: EV/EBITDA cohort route and P/E, "the broad valuation backbone precisely
#: because it routes around the binding leaf". The mask records whichever the
#: replay actually resolved; it does not assert that both are always used.
SUBFACTORS: dict[str, tuple[str, ...]] = {
    "valuation": ("pe_ratio", "ev_ebitda"),
    "growth": ("revenue_growth", "revenue_acceleration", "ebitda_growth"),
    "profitability": ("ebitda_margin", "roa"),
    "debt": ("net_debt_ebitda", "debt_market_cap"),
}

SUBFACTOR_LETTER: dict[str, str] = {
    "pe_ratio": "PE", "ev_ebitda": "EVEBITDA",
    "revenue_growth": "RG", "revenue_acceleration": "ACC", "ebitda_growth": "EG",
    "ebitda_margin": "EM", "roa": "ROA",
    "net_debt_ebitda": "ND", "debt_market_cap": "DM",
}

#: The graph node that computes each pillar, used to decide price dependence
#: from the derivation graph rather than from a hand-maintained list.
PILLAR_NODE: dict[str, str] = {
    "valuation": "valuation_score",
    "growth": "growth_score",
    "profitability": "profitability_score",
    "debt": "debt_score",
}

AVAIL_RESOLVED = "resolved"
AVAIL_MISSING = "unavailable"

#: Sample-scope vocabulary, restated from `pit_store` for the same reason as
#: the weights. Anything whose value depends on a price carries it.
SAMPLE_SURVIVOR_ONLY = "SURVIVOR_ONLY_DIAGNOSTIC"
SAMPLE_FULL_UNIVERSE = "FULL_UNIVERSE"

#: The first day of the option archive. Before it, an option quote is in
#: `pit_derive.UNAVAILABLE_REGISTER` as genuinely unavailable -- a chain is a
#: snapshot of a moment and nobody sells the moment back afterwards. So a hedge
#: recommendation can be MADE today and cannot be BACKTESTED before this date.
OPTION_ARCHIVE_START = "2026-09-20"

# -- the output states -----------------------------------------------------

#: The pillars available at both dates are the same set: the move is economic
#: and a point change is a fair thing to render.
STATE_ECONOMIC_CHANGE = "ECONOMIC_CHANGE"

#: The score signature changed. THE SCORES ARE NOT COMPARABLE and this state
#: exists so that fact is carried in the data rather than left to a front-end
#: to remember. `Attribution.headline_delta` is None in this state, so a
#: VGPD -> GPD transition is structurally unable to render as a downgrade.
STATE_COMPARABILITY_CHANGED = "COMPARABILITY_CHANGED"

STATE_UNCHANGED = "UNCHANGED"
STATE_NO_PRIOR = "NO_PRIOR_SNAPSHOT"
STATE_NO_SCORE = "NO_SCORE"

#: The terms do not sum to the observed change and no clamp explains the gap.
#: Reported, never smoothed: a residual with no cause is a bug in the engine or
#: a snapshot written by a different model, and both must be visible.
STATE_RECONCILIATION_FAILED = "RECONCILIATION_FAILED"

# -- the cause labels ------------------------------------------------------

#: The pillar value moved while the ruler stayed still. A real move.
CAUSE_ECONOMIC = "economic_move"

#: The pillar value moved because the PEER SET moved. Every pillar but
#: valuation is a percentile rank, so a company can rise without doing
#: anything: its peers fell, or the rung fell back from sic4 to sic3. This is
#: not the company changing and must not be reported as though it were.
CAUSE_PEER_SET_CHANGED = "peer_set_changed"

CAUSE_BECAME_AVAILABLE = "factor_became_available"
CAUSE_BECAME_UNAVAILABLE = "factor_became_unavailable"

#: A surviving pillar's weight moved because ANOTHER pillar entered or left and
#: the remaining weights were renormalised. The detail names which.
CAUSE_RENORMALISATION = "renormalisation_over_available_factors"

#: A surviving pillar's weight moved with no entry and no exit, which can only
#: mean the DECLARED weights differ -- i.e. the two snapshots are two models.
CAUSE_DECLARED_WEIGHTS_CHANGED = "declared_weights_changed"

CAUSE_CLAMP = "clamp_bound"
CAUSE_SECTOR_OVERLAY = "sector_overlay_move"

# -- term kinds ------------------------------------------------------------

TERM_FACTOR = "factor"
TERM_WEIGHT = "weight"
TERM_INTERACTION = "interaction"
TERM_ENTRY = "entry"
TERM_EXIT = "exit"
TERM_OVERLAY = "overlay"
TERM_CLAMP = "clamp"

#: Terms that may be presented to a reader as "what changed economically".
#: Everything else is availability, renormalisation or arithmetic, and the
#: renderer keeps them in a separate block for that reason.
ECONOMIC_TERM_KINDS = (TERM_FACTOR, TERM_OVERLAY)

_TOL = 1e-9


# ==========================================================================
# (1) THE SCHEMA -- specified, NOT applied
# ==========================================================================
#
# GRAIN IS THE WHOLE DESIGN. Each level of the derivation chain is stored at
# the granularity it actually belongs to, and the arithmetic that decides it is
# in `grain_arithmetic()` rather than in a paragraph of prose:
#
#   pit_cohort_stat          COHORT level     ~39,038 rows/run
#   pit_pillar_score         PILLAR level     ~4,960,000 rows/run (4 x 1.24M)
#   pit_company_intermediate COMPANY level    ~1,240,000 rows/run
#   pit_hedge_snapshot       POSITION level   held positions only, not 1.24M
#
# `pit_cohort_stat` and `pit_company_intermediate` are ALREADY DECLARED, in
# `pit_derive.PIT_INTERMEDIATE_DDL`. This module does NOT redeclare them, and
# the reason is a trap worth naming: every statement in this store is
# CREATE TABLE IF NOT EXISTS, so a second, differing declaration of the same
# table is not an error -- it is a SILENT NO-OP, and the store ends up with
# whichever module ran first while both files claim to describe it. One
# declaration, extended by ALTER. That is why the cohort additions below are
# MIGRATIONS and not a new CREATE.

#: ADD-COLUMN-only upgrades, in `pit_store.MIGRATIONS` form and for the same
#: reason: `pit_feature` and `pit_score` are empty TODAY, so a rebuild would be
#: free today and ruinous in a month. Written as ALTERs from the start so the
#: procedure is the same either way.
#:
#: No column takes a DEFAULT. A constant default would backfill a positive
#: claim onto rows that never made it -- the exact silent mislabelling
#: `pit_store` refused for `label_policy_version`. NULL means "written before
#: this column existed", which is a true statement and a checkable one.
INTERMEDIATE_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "pit_cohort_stat": [
        # Per-FACTOR eligibility. Different factors have different eligibility
        # now: the growth pillar needs peers with a usable revenue history, the
        # debt pillar needs peers with debt AND a market cap, and a single
        # n_members answers neither question. Stored as JSON because the factor
        # set is model-versioned and a column per factor would need a migration
        # every time a factor is added.
        ("n_eligible_json", "TEXT"),
        # ...and the two scalars that make the JSON queryable without parsing
        # it. The binding factor and its count are what a "why is this cohort
        # thin" query filters on, and a JSON blob cannot be indexed. This pair
        # is the deliberate, bounded denormalisation: two numbers, derived from
        # the blob, that carry the question anyone actually asks.
        ("n_eligible_min", "INTEGER"),
        ("binding_factor", "TEXT"),
        # WHICH rule decided eligibility, and its version. A cohort statistic
        # whose eligibility rule is unrecorded cannot be compared with one
        # computed under a different rule, and the two look identical in a row.
        ("eligibility_rule", "TEXT"),
        ("eligibility_rule_version", "TEXT"),
    ],
    "pit_feature": [
        # THE PRIMITIVE VALUES ACTUALLY USED. `sources_json` already records
        # the accessions, which answers "which filings" and not "which
        # numbers". Without the values, "why did EBITDA change" means re-running
        # the point-in-time selector for a historical date -- which is both slow
        # and, if any policy has moved since, a different answer.
        #
        # Compact by construction: the payload is the node's DE-DUPLICATED leaf
        # set from `pit_derive.leaves()`, stopping at EBITDA. EBITDA has two
        # inputs; EV/EBITDA has five (price, shares, debt, cash, EBITDA), which
        # is the same five `pit_invariants` names in its ev_ebitda claim.
        # Recording EBITDA's own two again underneath EV/EBITDA would be the
        # double-charging that module exists to forbid.
        ("resolved_inputs_json", "TEXT"),
    ],
    "pit_score": [
        # The headline: which economic pillars survived, e.g. 'VGPD' or 'GPD'.
        ("score_signature", "TEXT"),
        # The same thing positionally, e.g. '1111' or '0111'. TEXT and NOT
        # INTEGER, deliberately: as an integer '0111' is 111, and the leading
        # zero -- which is precisely the valuation-missing case -- is destroyed
        # by the storage type.
        ("pillar_mask", "TEXT"),
        # The DECLARED weight the surviving pillars carried before
        # renormalisation: 1.00 for VGPD, 0.60 when valuation's 0.40 is absent.
        # This is the one number that says how much of the model produced the
        # score, and it is what a research query filters on to avoid pooling
        # two model states.
        ("original_weight_coverage", "REAL"),
        # The detailed mask, e.g. 'V[PE=1,EVEBITDA=0] G[RG=1,ACC=0,EG=1]'.
        ("subfactor_mask", "TEXT"),
    ],
}

#: Tables this module declares itself. Cohort and company intermediates are
#: NOT here -- see the note above.
PIT_INTERMEDIATE_DDL_V2 = """
-- =====================================================================
-- PILLAR-LEVEL INTERMEDIATES  (4 rows per entity-date, ~4.96M per run)
--
-- A pillar gets a ROW and not a key in a JSON blob, and that is the entire
-- point of this table. The product question is "what changed?", which is a
-- DIFFERENCE between two dates; a difference is a join; and a JSON blob
-- inside pit_score cannot be joined, indexed, or aggregated across a
-- cross-section. Storing the pillar scores as JSON would preserve the
-- numbers and lose the only operation anyone wants to perform on them.
--
-- effective_weight AND original_weight are both stored. Their ratio is the
-- renormalisation, and the whole weight-effect half of the attribution is
-- unrecoverable without the pair.
-- =====================================================================
CREATE TABLE IF NOT EXISTS pit_pillar_score (
    entity_id               INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    as_of_date              TEXT NOT NULL,
    pillar                  TEXT NOT NULL,   -- valuation | growth | profitability | debt
    model_version           TEXT NOT NULL,
    replay_run_id           INTEGER NOT NULL REFERENCES pit_replay_run(run_id),

    -- the number, and how much of the score it was allowed to move
    pillar_score            REAL,            -- NULL iff availability = 'unavailable'
    original_weight         REAL NOT NULL,   -- as declared: 0.40 / 0.25 / 0.20 / 0.15
    effective_weight        REAL NOT NULL,   -- after renormalising over what resolved

    -- availability, and WHY when it is absent
    availability            TEXT NOT NULL,   -- resolved | unavailable
    unavailable_reason      TEXT,            -- pit_store REASON_* vocabulary

    -- which sub-factors produced it. The count is queryable; the detail is
    -- JSON because it is a per-model list, not a fixed set of columns.
    n_subfactors_declared   INTEGER NOT NULL,
    n_subfactors_resolved   INTEGER NOT NULL,
    subfactors_json         TEXT,            -- {key: {normalized, inner_weight, availability}}
    subfactor_mask          TEXT,            -- e.g. 'RG=1,ACC=0,EG=1'

    -- the ruler this pillar was measured against, so a rank move caused by the
    -- peer set can be told apart from one caused by the company
    peer_set_id             INTEGER REFERENCES pit_peer_set(peer_set_id),
    benchmark_level         TEXT,            -- Industry | Sector Fallback
    n_peers                 INTEGER,

    -- SURVIVOR_ONLY_DIAGNOSTIC where the pillar's value depends on a price.
    -- Valuation and debt both do (EV/EBITDA and debt/market-cap reach a quoted
    -- price through market cap), and the price sample is 2,574 listings every
    -- one of which is alive in 2026.
    sample_scope            TEXT NOT NULL,

    model_state_version     TEXT NOT NULL,   -- INTERMEDIATE_SCHEMA_VERSION
    created_at              TEXT NOT NULL,

    UNIQUE (entity_id, as_of_date, pillar, model_version, replay_run_id),
    CHECK (availability IN ('resolved', 'unavailable')),
    CHECK (availability = 'unavailable' OR pillar_score IS NOT NULL),
    CHECK (availability = 'resolved' OR effective_weight = 0.0),
    CHECK (effective_weight >= 0.0 AND effective_weight <= 1.0),
    CHECK (original_weight  >= 0.0 AND original_weight  <= 1.0),
    CHECK (n_subfactors_resolved <= n_subfactors_declared),
    CHECK (sample_scope IN ('SURVIVOR_ONLY_DIAGNOSTIC', 'FULL_UNIVERSE'))
);

CREATE INDEX IF NOT EXISTS idx_pit_pillar_score_asof
    ON pit_pillar_score (as_of_date, pillar, model_version, replay_run_id);
CREATE INDEX IF NOT EXISTS idx_pit_pillar_score_entity
    ON pit_pillar_score (entity_id, pillar, as_of_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_pillar_score_no_update
BEFORE UPDATE ON pit_pillar_score
BEGIN
    SELECT RAISE(ABORT, 'pit_pillar_score is immutable: re-run the replay instead');
END;

-- =====================================================================
-- HEDGE SNAPSHOT  (one row per held position per as-of, per direction)
--
-- The INPUTS and the RECOMMENDATION together, frozen at the snapshot, so a
-- hedge change decomposes the same way a score change does: the ratio is
-- clamp((AdverseScore - floor) / span, 0, 1), and the floor and span are
-- stored beside it because a rule change would otherwise be indistinguishable
-- from a score change in the history.
--
-- research_status is NOT NULL and has no default. A hedge recommendation is
-- available LIVE today and cannot be validated historically before the option
-- archive begins (2026-09-20), because an option quote from before that date
-- does not exist at any price of effort. Writing that status on the row means
-- a consumer cannot join to this table and forget it.
-- =====================================================================
CREATE TABLE IF NOT EXISTS pit_hedge_snapshot (
    entity_id               INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    listing_id              INTEGER REFERENCES pit_listing(listing_id),
    as_of_date              TEXT NOT NULL,
    model_version           TEXT NOT NULL,
    replay_run_id           INTEGER NOT NULL REFERENCES pit_replay_run(run_id),
    direction               TEXT NOT NULL,   -- long | short

    -- THE INPUTS, frozen
    equity_score            REAL,            -- the final Shaffer Score that drove it
    score_signature         TEXT,            -- so a hedge move caused by a signature
                                             -- change is separable from a real one
    adverse_score           REAL,            -- max(0, -direction * score)
    hedge_floor             REAL NOT NULL,   -- the rule's constants AT THE SNAPSHOT
    hedge_span              REAL NOT NULL,
    position_shares         REAL,
    spot                    REAL,            -- SURVIVOR_ONLY_DIAGNOSTIC
    position_market_value   REAL,            -- SURVIVOR_ONLY_DIAGNOSTIC

    -- THE RECOMMENDATION
    hedge_ratio             REAL,
    hedge_required          INTEGER NOT NULL DEFAULT 0,
    hedged_shares           REAL,
    hedge_notional          REAL,
    preferred_strategy      STRATEGY_TEXT_PLACEHOLDER,
    strategy_inputs_json    TEXT,            -- strike, expiry, delta, premium basis
    excluded_json           TEXT,            -- strategy -> why it was refused

    -- provenance of the option data, by REFERENCE. The chains themselves are
    -- gzipped JSONL under pit_archive with a manifest row in
    -- pit_option_snapshot; copying quotes in here would duplicate the one
    -- source in this project that grows without bound.
    option_snapshot_id      INTEGER REFERENCES pit_option_snapshot(snapshot_id),

    sample_scope            TEXT NOT NULL,
    research_status         TEXT NOT NULL,   -- LIVE_RECOMMENDATION_ONLY | HISTORICALLY_VALIDATED
    validation_available_from TEXT NOT NULL, -- '2026-09-20', the archive start
    created_at              TEXT NOT NULL,

    UNIQUE (entity_id, as_of_date, direction, model_version, replay_run_id),
    CHECK (direction IN ('long', 'short')),
    CHECK (hedge_ratio IS NULL OR (hedge_ratio >= 0.0 AND hedge_ratio <= 1.0)),
    CHECK (hedge_required IN (0, 1)),
    CHECK (sample_scope IN ('SURVIVOR_ONLY_DIAGNOSTIC', 'FULL_UNIVERSE')),
    CHECK (research_status IN ('LIVE_RECOMMENDATION_ONLY', 'HISTORICALLY_VALIDATED')),
    CHECK (research_status = 'LIVE_RECOMMENDATION_ONLY'
           OR as_of_date >= validation_available_from)
);

CREATE INDEX IF NOT EXISTS idx_pit_hedge_snapshot_asof
    ON pit_hedge_snapshot (as_of_date, model_version, replay_run_id);
CREATE INDEX IF NOT EXISTS idx_pit_hedge_snapshot_entity
    ON pit_hedge_snapshot (entity_id, as_of_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_hedge_snapshot_no_update
BEFORE UPDATE ON pit_hedge_snapshot
BEGIN
    SELECT RAISE(ABORT, 'pit_hedge_snapshot is immutable: re-run the replay instead');
END;
""".replace("STRATEGY_TEXT_PLACEHOLDER", "TEXT")


def ensure_schema(conn: Any) -> dict[str, Any]:
    """Create the intermediate tables and add the columns. RUN BY SOMEONE ELSE.

    NOTHING IN THIS MODULE CALLS THIS and nothing in this module opens a
    connection to pass it. Two workflows are mid-flight on the store with seven
    live processes; a writer arriving now contends for the write lock, and a
    reader that lingers blocks the WAL checkpointer -- which is how this project
    once put a 9.3 GB WAL on a volume with 13.4 GiB free.

    ORDER MATTERS AND IS THE INVARIANT HERE. `pit_derive.PIT_INTERMEDIATE_DDL`
    owns the CREATE for `pit_cohort_stat`; this function runs it FIRST, then its
    own CREATEs, then the ALTERs -- because an ALTER against a table that does
    not exist yet is an error, and a second CREATE with different columns is
    worse than an error: under IF NOT EXISTS it is a silent no-op.

    Idempotent. Every CREATE is IF NOT EXISTS and every ALTER is guarded by a
    PRAGMA table_info check, so a second run adds nothing and raises nothing.
    Returns a status dict naming exactly what it added.
    """
    added: list[str] = []
    conn.executescript(pit_derive.PIT_INTERMEDIATE_DDL)
    conn.executescript(PIT_INTERMEDIATE_DDL_V2)
    for table, columns in INTERMEDIATE_MIGRATIONS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            continue
        existing = {r[1] if not hasattr(r, "keys") else r["name"] for r in rows}
        for name, sql_type in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                added.append(f"{table}.{name}")
    conn.commit()
    return {"status": "ok", "schema_version": INTERMEDIATE_SCHEMA_VERSION,
            "columns_added": added}


#: Parametrised INSERTs, as TEXT. This module builds rows and returns them with
#: their statement; it does not execute one. `pending_writes()` pairs them.
INSERT_SQL: dict[str, str] = {
    "pit_cohort_stat": (
        "INSERT INTO pit_cohort_stat ("
        "as_of_date, rung, key, model_version, peer_set_version, replay_run_id, "
        "peer_set_id, ladder_version, n_sector_peers, n_ebitda_resolved, "
        "n_cohort_members, n_ev_ebitda_used, ebitda_p50, ebitda_p75, "
        "cohort_mean_ebitda, cohort_mean_margin, cohort_mean_ev_ebitda, "
        "benchmark_level, binding_leaf, source_coverage, confidence, created_at, "
        "n_eligible_json, n_eligible_min, binding_factor, eligibility_rule, "
        "eligibility_rule_version) VALUES ("
        ":as_of_date, :rung, :key, :model_version, :peer_set_version, :replay_run_id, "
        ":peer_set_id, :ladder_version, :n_sector_peers, :n_ebitda_resolved, "
        ":n_cohort_members, :n_ev_ebitda_used, :ebitda_p50, :ebitda_p75, "
        ":cohort_mean_ebitda, :cohort_mean_margin, :cohort_mean_ev_ebitda, "
        ":benchmark_level, :binding_leaf, :source_coverage, :confidence, :created_at, "
        ":n_eligible_json, :n_eligible_min, :binding_factor, :eligibility_rule, "
        ":eligibility_rule_version)"
    ),
    "pit_pillar_score": (
        "INSERT INTO pit_pillar_score ("
        "entity_id, as_of_date, pillar, model_version, replay_run_id, "
        "pillar_score, original_weight, effective_weight, availability, "
        "unavailable_reason, n_subfactors_declared, n_subfactors_resolved, "
        "subfactors_json, subfactor_mask, peer_set_id, benchmark_level, n_peers, "
        "sample_scope, model_state_version, created_at) VALUES ("
        ":entity_id, :as_of_date, :pillar, :model_version, :replay_run_id, "
        ":pillar_score, :original_weight, :effective_weight, :availability, "
        ":unavailable_reason, :n_subfactors_declared, :n_subfactors_resolved, "
        ":subfactors_json, :subfactor_mask, :peer_set_id, :benchmark_level, :n_peers, "
        ":sample_scope, :model_state_version, :created_at)"
    ),
    "pit_hedge_snapshot": (
        "INSERT INTO pit_hedge_snapshot ("
        "entity_id, listing_id, as_of_date, model_version, replay_run_id, direction, "
        "equity_score, score_signature, adverse_score, hedge_floor, hedge_span, "
        "position_shares, spot, position_market_value, hedge_ratio, hedge_required, "
        "hedged_shares, hedge_notional, preferred_strategy, strategy_inputs_json, "
        "excluded_json, option_snapshot_id, sample_scope, research_status, "
        "validation_available_from, created_at) VALUES ("
        ":entity_id, :listing_id, :as_of_date, :model_version, :replay_run_id, :direction, "
        ":equity_score, :score_signature, :adverse_score, :hedge_floor, :hedge_span, "
        ":position_shares, :spot, :position_market_value, :hedge_ratio, :hedge_required, "
        ":hedged_shares, :hedge_notional, :preferred_strategy, :strategy_inputs_json, "
        ":excluded_json, :option_snapshot_id, :sample_scope, :research_status, "
        ":validation_available_from, :created_at)"
    ),
}


# -- the grain arithmetic --------------------------------------------------

#: Measured, not assumed. Same figures `pit_derive.storage_recommendation()`
#: rests on, restated here so this module's arithmetic is checkable in place.
PEER_SETS_TOTAL = 39_038
AS_OF_DATES_TOTAL = 165
ENTITY_DATES_TOTAL = 1_240_000

#: What was deliberately NOT stored, and the reason. A storage design is only
#: honest if it says what it threw away; a list of tables says what survived.
NOT_STORED: tuple[dict[str, str], ...] = (
    {"thing": "sector_ebitda_vector (the ~32 EBITDA values behind each cohort)",
     "cost": f"{PEER_SETS_TOTAL:,} x 31.8 = {PEER_SETS_TOTAL * 32:,} numbers per run",
     "why": ("it is a second copy of pit_feature. Every member's EBITDA is "
             "already a pit_feature row under the same (as_of, model_version, "
             "replay_run_id), and membership is already in pit_peer_member, so "
             "the vector is a join and not a fact. Only its COUNT and its "
             "percentiles are stored -- which is what pit_derive's own node "
             "note says the vector's value IS in a trace.")},
    {"thing": "per-peer contribution to each percentile rank",
     "cost": f"{ENTITY_DATES_TOTAL:,} x 31.8 = {int(ENTITY_DATES_TOTAL * 31.76):,} rows",
     "why": ("'which peers was I ranked against' is answered exactly by "
             "pit_peer_member joined to pit_feature. Materialising the "
             "cross-product would be 39 million rows to store a join.")},
    {"thing": "the option chain behind a hedge recommendation",
     "cost": "unbounded -- chains are the one source in this project with no ceiling",
     "why": ("already archived as gzipped JSONL with a manifest row in "
             "pit_option_snapshot. pit_hedge_snapshot stores option_snapshot_id "
             "and the few quote fields the ticket actually used.")},
    {"thing": "the rendered explanation text",
     "cost": f"~200 bytes x {ENTITY_DATES_TOTAL:,} = ~248 MB per run",
     "why": ("every sentence this module produces is derived from the stored "
             "numbers. Storing the sentence freezes a rendering bug into the "
             "historical record, where it can never be corrected without an "
             "UPDATE -- and these tables refuse UPDATE by trigger.")},
    {"thing": "a forecast for every horizon",
     "cost": "4 horizons x 1.24M = 4.96M rows, most of them meaningless",
     "why": ("pit_score_prediction and pit_prediction already hold predictions, "
             "and a horizon whose evidence is INSUFFICIENT must not have a "
             "number written anywhere -- writing it is how it later gets "
             "quoted. See horizon_forecast().")},
)


def grain_arithmetic() -> dict[str, Any]:
    """Row counts per grain, and the alternative each grain choice refuses.

    Declarative: computes the comparison, applies nothing. ROW counts are exact
    arithmetic on measured totals; BYTE figures are labelled estimates.

    The headline number is the repetition factor. 1,240,000 entity-dates over
    39,038 peer sets is 31.76 members per set, so a cohort statistic stored per
    COMPANY asserts 1,240,000 facts where 39,038 exist. Two copies that
    disagree are then indistinguishable from a real difference, which is a
    worse failure than the 322 MB.
    """
    repetition = ENTITY_DATES_TOTAL / PEER_SETS_TOTAL
    cohort_fields = 11
    return {
        "measured_inputs": {
            "peer_sets": PEER_SETS_TOTAL,
            "as_of_dates": AS_OF_DATES_TOTAL,
            "entity_dates": ENTITY_DATES_TOTAL,
            "mean_peer_set_size": round(repetition, 2),
        },
        "grains": {
            "pit_cohort_stat": {
                "grain": "(as_of_date, rung, key, model_version, peer_set_version)",
                "rows_per_run": PEER_SETS_TOTAL,
                "bytes_estimate": PEER_SETS_TOTAL * 140,
                "refuses": {
                    "alternative": "the same fields on every company row",
                    "rows_per_run": ENTITY_DATES_TOTAL,
                    "bytes_estimate": ENTITY_DATES_TOTAL * (140 + 120),
                    "repetition_factor": round(repetition, 2),
                    "objection": (f"asserts {ENTITY_DATES_TOTAL:,} facts where "
                                  f"{PEER_SETS_TOTAL:,} exist; ~322 MB against "
                                  f"~5.5 MB; and a corrected P50 rewrites "
                                  f"{ENTITY_DATES_TOTAL:,} rows, not "
                                  f"{PEER_SETS_TOTAL:,}"),
                },
                "also_refuses": {
                    "alternative": "cohort statistics as extra pit_feature rows",
                    "rows_per_run": cohort_fields * ENTITY_DATES_TOTAL,
                    "objection": ("pit_feature's UNIQUE key is per ENTITY, so a "
                                  "cohort constant has no natural home in it; "
                                  "the table would be dominated by restatements "
                                  "of 39,038 numbers"),
                },
            },
            "pit_pillar_score": {
                "grain": "(entity_id, as_of_date, pillar, model_version, replay_run_id)",
                "rows_per_run": len(PILLARS) * ENTITY_DATES_TOTAL,
                "bytes_estimate": len(PILLARS) * ENTITY_DATES_TOTAL * 110,
                "refuses": {
                    "alternative": "a JSON blob of pillar scores inside pit_score",
                    "rows_per_run": ENTITY_DATES_TOTAL,
                    "objection": ("cheaper and useless. The product question is "
                                  "a DIFFERENCE between two dates, a difference "
                                  "is a join, and JSON cannot be joined, "
                                  "indexed or aggregated across a cross-section. "
                                  "It would preserve the numbers and lose the "
                                  "only operation anyone performs on them."),
                },
            },
            "pit_company_intermediate": {
                "grain": "(entity_id, as_of_date, model_version, replay_run_id)",
                "rows_per_run": ENTITY_DATES_TOTAL,
                "bytes_estimate": ENTITY_DATES_TOTAL * 120,
                "note": "declared in pit_derive.PIT_INTERMEDIATE_DDL, not here",
            },
            "pit_hedge_snapshot": {
                "grain": "(entity_id, as_of_date, direction, model_version, replay_run_id)",
                "rows_per_run": "held positions only -- NOT the 1.24M universe",
                "why": ("a hedge is a property of a POSITION, not of a company. "
                        "There is no recommendation to store for a name nobody "
                        "holds, and writing one would be inventing a position."),
            },
        },
        "not_stored": list(NOT_STORED),
        "not_applied": ("ensure_schema() is written for another process to run "
                        "later. This module executes no DDL and opens no "
                        "connection."),
    }


# ==========================================================================
# (2) THE SNAPSHOT MODEL
# ==========================================================================

@dataclass(frozen=True)
class Part:
    """One weighted member of a renormalising blend, at one date.

    A pillar inside the major blend, or a sub-factor inside a pillar -- the
    attribution engine does not care which, which is why there is one type and
    one `decompose_blend`.

    THE INVARIANT: `available` is the single source of truth about membership.
    `value` is None exactly when `available` is False. A part that is
    unavailable carries no value and no effective weight, and a part that is
    available carries both; anything else is a snapshot that cannot be
    decomposed, and `ScoreSnapshot.problems()` says so rather than letting the
    arithmetic quietly produce a number.
    """

    key: str
    value: Optional[float] = None
    weight: float = 0.0              # the DECLARED weight, before renormalisation
    available: bool = True
    reason: str = ""                 # unavailable_reason, when absent
    label: str = ""
    n_subfactors_declared: int = 0
    n_subfactors_resolved: int = 0
    subfactors: tuple[str, ...] = () # which sub-factors resolved
    price_dependent: bool = False

    @property
    def display(self) -> str:
        return self.label or PILLAR_LABEL.get(self.key, self.key)


def _price_dependent_pillars() -> dict[str, bool]:
    """Which pillars reach a quoted price, ASKED OF THE GRAPH not hardcoded.

    Anything touching a price is SURVIVOR_ONLY_DIAGNOSTIC: the price sample is
    2,574 listings, every one of which is alive in 2026. A hand-maintained list
    would drift the first time a factor changed; the graph cannot.
    """
    out: dict[str, bool] = {}
    for pillar, node_key in PILLAR_NODE.items():
        touched: set[str] = set()
        for ref in pit_derive.leaves(node_key):
            if ref.per_member or ref.key not in pit_derive.NODES:
                touched.add(ref.key)
                continue
            try:
                touched.update(r.key for r in pit_derive.leaves(ref.key))
            except ValueError:
                touched.add(ref.key)
            touched.add(ref.key)
        out[pillar] = bool(touched & pit_invariants.PRICE_DEPENDENT_LEAVES)
    return out


PRICE_DEPENDENT_PILLAR: dict[str, bool] = _price_dependent_pillars()


@dataclass(frozen=True)
class ScoreSnapshot:
    """Everything needed to explain one score, frozen at one as_of.

    This is the row set `pit_score` + `pit_pillar_score` hold, in memory. The
    replay builds one for a past `as_of`; the daily job builds one for today.
    They are the same object because `available_date` decides membership in
    both cases -- the shared event clock.

    THE INVARIANT: `final_score` is reproducible from `parts`, `overlay` and
    the two clamps. `recompute()` does it, and `problems()` reports any gap
    rather than letting a stored number and its own decomposition disagree.
    """

    entity_id: int
    as_of_date: str
    parts: tuple[Part, ...]
    overlay: Optional[float] = None
    final_score: Optional[float] = None
    peer_set_id: Optional[int] = None
    benchmark_level: str = ""
    model_version: str = ""
    replay_run_id: int = 0
    scale: float = COMPANY_SCALE

    # -- membership --------------------------------------------------------

    @property
    def available(self) -> tuple[Part, ...]:
        return tuple(p for p in self.parts if p.available)

    @property
    def by_key(self) -> dict[str, Part]:
        return {p.key: p for p in self.parts}

    @property
    def signature(self) -> str:
        return signature_of(self.parts)

    @property
    def pillar_mask(self) -> str:
        return pillar_mask_of(self.parts)

    @property
    def subfactor_mask(self) -> str:
        return subfactor_mask_of(self.parts)

    @property
    def original_weight_coverage(self) -> float:
        return original_weight_coverage(self.parts)

    @property
    def effective_weights(self) -> dict[str, float]:
        return effective_weights(self.parts)

    @property
    def price_dependent(self) -> bool:
        return any(p.price_dependent for p in self.available)

    @property
    def sample_scope(self) -> str:
        return SAMPLE_SURVIVOR_ONLY if self.price_dependent else SAMPLE_FULL_UNIVERSE

    # -- arithmetic --------------------------------------------------------

    def raw_score(self) -> Optional[float]:
        return blend_value(self.parts)

    def recompute(self) -> Optional[float]:
        """The final score this snapshot's own parts imply.

        CompanyScore = clamp(scale * raw, -75, +75); Final = clamp(CompanyScore
        + overlay, -100, +100). A missing overlay contributes zero rather than
        blocking the score, exactly as the production core does it.
        """
        raw = self.raw_score()
        if raw is None:
            return None
        company = _clamp(self.scale * raw, COMPANY_SCORE_MIN, COMPANY_SCORE_MAX)
        overlay = float(self.overlay) if self.overlay is not None else 0.0
        return _clamp(company + overlay, FINAL_MIN, FINAL_MAX)

    def clamp_binding(self) -> str:
        """Which clamp, if any, bound. '' when the blend passed through clean.

        Named rather than inferred: when a clamp binds, the decomposition of
        the blend no longer equals the change in the final score, and the gap
        must be attributed to the clamp BY NAME instead of dropped into a
        residual that absorbs anything.
        """
        raw = self.raw_score()
        if raw is None:
            return ""
        scaled = self.scale * raw
        company = _clamp(scaled, COMPANY_SCORE_MIN, COMPANY_SCORE_MAX)
        marks: list[str] = []
        if abs(company - scaled) > _TOL:
            marks.append("company_score")
        overlay = float(self.overlay) if self.overlay is not None else 0.0
        total = company + overlay
        if abs(_clamp(total, FINAL_MIN, FINAL_MAX) - total) > _TOL:
            marks.append("final_score")
        return "+".join(marks)

    def problems(self) -> list[str]:
        """Every internal inconsistency, as plain sentences. Empty is healthy."""
        out: list[str] = []
        for part in self.parts:
            if part.available and part.value is None:
                out.append(f"{part.key}: available but carries no value")
            if not part.available and part.value is not None:
                out.append(f"{part.key}: unavailable but carries a value")
            if not part.available and not part.reason:
                out.append(f"{part.key}: unavailable with no reason recorded")
            if part.n_subfactors_resolved > part.n_subfactors_declared:
                out.append(f"{part.key}: more sub-factors resolved than declared")
        keys = [p.key for p in self.parts]
        if len(set(keys)) != len(keys):
            out.append("a part appears twice")
        mine = self.recompute()
        if self.final_score is not None and mine is not None:
            if abs(mine - self.final_score) > 1e-6:
                out.append(f"stored final_score {self.final_score:.6f} but the "
                           f"parts imply {mine:.6f}")
        return out


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def signature_of(parts: Sequence[Part]) -> str:
    """The headline signature, e.g. 'VGPD' or 'GPD'. Order is PILLARS order.

    WHICH ECONOMIC PILLARS SURVIVED -- and nothing about how well they did.
    Two scores with different signatures are not comparable, and this string is
    what makes that checkable in one column instead of by reasoning about
    weights.
    """
    present = {p.key for p in parts if p.available}
    return "".join(PILLAR_LETTER[k] for k in PILLARS
                   if k in present and k in PILLAR_LETTER)


def pillar_mask_of(parts: Sequence[Part]) -> str:
    """The same membership positionally, e.g. '1111' or '0111'.

    TEXT, always. As an INTEGER '0111' is 111 and the leading zero -- the
    valuation-missing case, which is the one that matters most -- is destroyed
    by the storage type.
    """
    present = {p.key for p in parts if p.available}
    return "".join("1" if k in present else "0" for k in PILLARS)


def subfactor_mask_of(parts: Sequence[Part]) -> str:
    """The detailed mask, e.g. 'V[PE=1,EVEBITDA=0] G[RG=1,ACC=0,EG=1]'.

    The headline signature says which pillars survived; this says WHAT ACTUALLY
    PRODUCED THE NUMBER. A growth pillar resting on one component and one
    resting on three are different measurements that share a letter, and
    P(Return | Score, Signature) is only answerable later if the difference was
    written down at the time.

    Only AVAILABLE pillars appear: an absent pillar's sub-factor detail is
    already implied by its absence from the signature, and printing 'V[PE=0,
    EVEBITDA=0]' would invite a reader to treat it as a weak valuation rather
    than no valuation.
    """
    chunks: list[str] = []
    for key in PILLARS:
        part = next((p for p in parts if p.key == key), None)
        if part is None or not part.available:
            continue
        declared = SUBFACTORS.get(key, ())
        resolved = set(part.subfactors)
        inner = ",".join(f"{SUBFACTOR_LETTER.get(s, s)}={1 if s in resolved else 0}"
                         for s in declared)
        chunks.append(f"{PILLAR_LETTER[key]}[{inner}]")
    return " ".join(chunks)


def original_weight_coverage(parts: Sequence[Part]) -> float:
    """The DECLARED weight the surviving pillars carried, before renormalising.

    1.00 for VGPD; 0.60 when valuation's 0.40 is absent. This one number says
    how much of the model produced the score, and it is what a research query
    filters on so that two model states are never pooled.
    """
    return sum(p.weight for p in parts if p.available)


def effective_weights(parts: Sequence[Part]) -> dict[str, float]:
    """Declared weights renormalised over the parts that resolved.

    A missing part never contributes a silent zero -- it is dropped and the
    rest are rescaled, exactly as `company_scoring._blend` does it. Returns {}
    when nothing resolved, which is how "no score" propagates.
    """
    live = [p for p in parts if p.available]
    total = sum(p.weight for p in live)
    if not live or total <= 0:
        return {}
    return {p.key: p.weight / total for p in live}


def blend_value(parts: Sequence[Part]) -> Optional[float]:
    """The renormalised weighted blend, or None when nothing resolved."""
    weights = effective_weights(parts)
    if not weights:
        return None
    return sum(weights[p.key] * float(p.value)
               for p in parts if p.available and p.value is not None)


# -- resolved primitive inputs --------------------------------------------

#: Flattening stops at EBITDA. That is what makes EV/EBITDA five inputs
#: (price, shares, debt, cash, EBITDA) rather than six, and five is exactly
#: what `pit_invariants`' ev_ebitda claim names. EBITDA's own two inputs are
#: recorded on EBITDA's own feature row; recording them again underneath
#: EV/EBITDA would be the double-charging that module exists to forbid.
RESOLVED_INPUT_STOPS: tuple[str, ...] = ("ebitda",)


def resolved_inputs(feature_key: str, values: Mapping[str, Any],
                    *, stop_at: Sequence[str] = RESOLVED_INPUT_STOPS
                    ) -> dict[str, Any]:
    """The primitive VALUES a feature actually used, compact, for `_json`.

    `sources_json` already records the accessions, which answers "which
    filings" and not "which numbers". Without the numbers, "why did this
    change" means re-running the point-in-time selector for a historical date
    -- slow, and a different answer if any policy has moved since.

    The key set is the graph's own DE-DUPLICATED leaf set, so what is stored is
    exactly what `pit_invariants` checks the factor is allowed to depend on. A
    leaf the caller could not resolve is recorded as None and NOT omitted:
    absent-from-the-dict and resolved-to-nothing are different facts, and
    conflating them is how a coverage number gets flattered.
    """
    refs = pit_derive.leaves(feature_key, stop_at=tuple(stop_at))
    used: dict[str, Any] = {}
    for ref in refs:
        if ref.per_member:
            used[ref.leaf_id] = "(one per cohort member; see pit_cohort_stat)"
            continue
        value = values.get(ref.leaf_id, values.get(ref.key))
        used[ref.leaf_id] = value
    return {
        "n": len(refs),
        "stop_at": list(stop_at),
        "values": used,
        "unresolved": sorted(k for k, v in used.items() if v is None),
    }


# ==========================================================================
# (3) ROW BUILDERS -- build the rows; do NOT execute them
# ==========================================================================

@dataclass(frozen=True)
class CohortStat:
    """One cohort's statistics, at the grain the cohort actually has.

    ~39,038 of these per run against 1,240,000 if stored per company. See
    `grain_arithmetic()` for the arithmetic; the reason is not the 322 MB, it
    is that repeating a cohort statistic 31.8 times asserts 1,240,000 facts
    where 39,038 exist.
    """

    as_of_date: str
    rung: str
    key: str
    n_members: int
    n_ebitda_resolved: int = 0
    cohort_n: int = 0
    ebitda_p50: Optional[float] = None
    ebitda_p75: Optional[float] = None
    cohort_mean_ebitda: Optional[float] = None
    cohort_mean_margin: Optional[float] = None
    cohort_mean_ev_ebitda: Optional[float] = None
    n_ev_ebitda_used: Optional[int] = None
    #: per-FACTOR eligibility: different factors have different eligibility now
    n_eligible: Mapping[str, int] = field(default_factory=dict)
    eligibility_rule: str = ""
    eligibility_rule_version: str = ""
    benchmark_level: str = ""
    binding_leaf: str = ""
    source_coverage: Optional[float] = None
    confidence: str = ""
    peer_set_id: Optional[int] = None


def cohort_stat_row(stat: CohortStat, *, model_version: str,
                    peer_set_version: str, replay_run_id: int,
                    ladder_version: str, created_at: str) -> dict[str, Any]:
    """One `pit_cohort_stat` row, as parameters. Executes nothing.

    Derives `n_eligible_min` and `binding_factor` from the per-factor map so
    the two scalars can never disagree with the JSON they summarise -- the
    bounded denormalisation is computed here, once, rather than written twice
    by a caller.
    """
    eligible = {str(k): int(v) for k, v in dict(stat.n_eligible).items()}
    if eligible:
        binding = min(eligible, key=lambda k: (eligible[k], k))
        n_min: Optional[int] = eligible[binding]
    else:
        binding, n_min = "", None
    return {
        "as_of_date": stat.as_of_date,
        "rung": stat.rung,
        "key": stat.key,
        "model_version": model_version,
        "peer_set_version": peer_set_version,
        "replay_run_id": int(replay_run_id),
        "peer_set_id": stat.peer_set_id,
        "ladder_version": ladder_version,
        "n_sector_peers": int(stat.n_members),
        "n_ebitda_resolved": int(stat.n_ebitda_resolved),
        "n_cohort_members": int(stat.cohort_n),
        "n_ev_ebitda_used": stat.n_ev_ebitda_used,
        "ebitda_p50": stat.ebitda_p50,
        "ebitda_p75": stat.ebitda_p75,
        "cohort_mean_ebitda": stat.cohort_mean_ebitda,
        "cohort_mean_margin": stat.cohort_mean_margin,
        "cohort_mean_ev_ebitda": stat.cohort_mean_ev_ebitda,
        "benchmark_level": stat.benchmark_level or None,
        "binding_leaf": stat.binding_leaf or None,
        "source_coverage": stat.source_coverage,
        "confidence": stat.confidence or None,
        "created_at": created_at,
        "n_eligible_json": json.dumps(eligible, sort_keys=True) if eligible else None,
        "n_eligible_min": n_min,
        "binding_factor": binding or None,
        "eligibility_rule": stat.eligibility_rule or None,
        "eligibility_rule_version": stat.eligibility_rule_version or None,
    }


def pillar_score_rows(snapshot: ScoreSnapshot, *, created_at: str,
                      n_peers: Optional[int] = None) -> list[dict[str, Any]]:
    """Four `pit_pillar_score` rows, as parameters. Executes nothing.

    One row per DECLARED pillar, including the unavailable ones. An absent
    pillar is a fact about the score and must be stored as a row: leaving it
    out makes "valuation was unavailable" indistinguishable from "this run
    never wrote valuation", and the attribution engine needs to tell those
    apart to call an exit an exit.
    """
    weights = snapshot.effective_weights
    rows: list[dict[str, Any]] = []
    for part in snapshot.parts:
        declared = SUBFACTORS.get(part.key, ())
        resolved = set(part.subfactors)
        inner = ",".join(f"{SUBFACTOR_LETTER.get(s, s)}={1 if s in resolved else 0}"
                         for s in declared)
        detail = {s: {"resolved": s in resolved} for s in declared}
        rows.append({
            "entity_id": int(snapshot.entity_id),
            "as_of_date": snapshot.as_of_date,
            "pillar": part.key,
            "model_version": snapshot.model_version,
            "replay_run_id": int(snapshot.replay_run_id),
            "pillar_score": part.value if part.available else None,
            "original_weight": float(part.weight),
            "effective_weight": float(weights.get(part.key, 0.0)),
            "availability": AVAIL_RESOLVED if part.available else AVAIL_MISSING,
            "unavailable_reason": None if part.available else (part.reason or None),
            "n_subfactors_declared": part.n_subfactors_declared or len(declared),
            "n_subfactors_resolved": part.n_subfactors_resolved or len(resolved),
            "subfactors_json": json.dumps(detail, sort_keys=True) if detail else None,
            "subfactor_mask": inner or None,
            "peer_set_id": snapshot.peer_set_id,
            "benchmark_level": snapshot.benchmark_level or None,
            "n_peers": n_peers,
            "sample_scope": (SAMPLE_SURVIVOR_ONLY if part.price_dependent
                             else SAMPLE_FULL_UNIVERSE),
            "model_state_version": INTERMEDIATE_SCHEMA_VERSION,
            "created_at": created_at,
        })
    return rows


def score_additions(snapshot: ScoreSnapshot) -> dict[str, Any]:
    """The four new `pit_score` columns for this snapshot. Executes nothing."""
    return {
        "score_signature": snapshot.signature,
        "pillar_mask": snapshot.pillar_mask,
        "original_weight_coverage": snapshot.original_weight_coverage,
        "subfactor_mask": snapshot.subfactor_mask,
    }


@dataclass(frozen=True)
class HedgeSnapshot:
    """The hedge inputs AND the recommendation, frozen at one as_of.

    Frozen together on purpose. HedgeRatio = clamp((AdverseScore - floor) /
    span, 0, 1), so a ratio stored without its floor and span cannot later be
    told apart from a ratio produced by a changed rule -- and a rule change
    that looks like a score change is the hedge equivalent of a signature
    change rendering as a downgrade.

    SURVIVOR_ONLY_DIAGNOSTIC: `spot`, `position_market_value` and
    `hedge_notional` all depend on a price, and the price sample behind this
    store is 2,574 listings every one of which is alive in 2026.
    """

    entity_id: int
    as_of_date: str
    direction: str = "long"
    equity_score: Optional[float] = None
    score_signature: str = ""
    adverse_score: float = 0.0
    hedge_floor: float = 15.0
    hedge_span: float = 85.0
    hedge_ratio: float = 0.0
    hedge_required: bool = False
    position_shares: Optional[float] = None
    hedged_shares: Optional[float] = None
    spot: Optional[float] = None
    position_market_value: Optional[float] = None
    hedge_notional: Optional[float] = None
    preferred_strategy: str = ""
    strategy_inputs: Mapping[str, Any] = field(default_factory=dict)
    excluded: Mapping[str, str] = field(default_factory=dict)
    option_snapshot_id: Optional[int] = None
    listing_id: Optional[int] = None


def hedge_snapshot_row(snap: HedgeSnapshot, *, model_version: str,
                       replay_run_id: int, created_at: str) -> dict[str, Any]:
    """One `pit_hedge_snapshot` row, as parameters. Executes nothing.

    `research_status` is derived from the as_of against the option-archive
    start rather than accepted from the caller: a row that claims historical
    validation for a date before 2026-09-20 is claiming to have priced an
    option quote that was never recorded.
    """
    status = hedge_research_status(snap.as_of_date)
    return {
        "entity_id": int(snap.entity_id),
        "listing_id": snap.listing_id,
        "as_of_date": snap.as_of_date,
        "model_version": model_version,
        "replay_run_id": int(replay_run_id),
        "direction": snap.direction,
        "equity_score": snap.equity_score,
        "score_signature": snap.score_signature or None,
        "adverse_score": float(snap.adverse_score),
        "hedge_floor": float(snap.hedge_floor),
        "hedge_span": float(snap.hedge_span),
        "position_shares": snap.position_shares,
        "spot": snap.spot,
        "position_market_value": snap.position_market_value,
        "hedge_ratio": float(snap.hedge_ratio),
        "hedge_required": 1 if snap.hedge_required else 0,
        "hedged_shares": snap.hedged_shares,
        "hedge_notional": snap.hedge_notional,
        "preferred_strategy": snap.preferred_strategy or None,
        "strategy_inputs_json": (json.dumps(dict(snap.strategy_inputs), sort_keys=True)
                                 if snap.strategy_inputs else None),
        "excluded_json": (json.dumps(dict(snap.excluded), sort_keys=True)
                          if snap.excluded else None),
        "option_snapshot_id": snap.option_snapshot_id,
        "sample_scope": SAMPLE_SURVIVOR_ONLY,
        "research_status": status["research_status"],
        "validation_available_from": OPTION_ARCHIVE_START,
        "created_at": created_at,
    }


def pending_writes(rows: Mapping[str, Sequence[Mapping[str, Any]]]
                   ) -> list[tuple[str, Mapping[str, Any]]]:
    """Pair each built row with its INSERT statement. STILL EXECUTES NOTHING.

    This is the handover: a replay (or tonight's live job) calls the row
    builders, passes the result here, and gets a list of (sql, params) ready
    for `executemany` -- by a process that is allowed to write, at a moment
    when nothing else is mid-write on the store. Returning the pairs instead of
    running them is what keeps `pit_feature` and `pit_score` at 0 rows today.
    """
    out: list[tuple[str, Mapping[str, Any]]] = []
    for table, batch in rows.items():
        sql = INSERT_SQL.get(table)
        if sql is None:
            raise ValueError(f"no INSERT registered for {table!r}; "
                             f"known: {sorted(INSERT_SQL)}")
        for params in batch:
            out.append((sql, params))
    return out


# ==========================================================================
# (4) READING -- SELECT only, and enforced rather than promised
# ==========================================================================

def read_only_sql(sql: str) -> str:
    """Refuse any statement that is not a single SELECT. Returns it unchanged.

    Every read in this module goes through here. The docstring at the top says
    this module does not write; this function is what makes that a property of
    the code instead of a claim about it, and `test_pit_intermediates.py` feeds
    it an INSERT to check that it bites.
    """
    head = sql.lstrip().lstrip("(").lstrip()
    if not head[:6].upper() == "SELECT":
        raise ValueError(
            f"pit_intermediates issues SELECT only; refused: {sql.strip()[:60]!r}")
    if ";" in sql.strip()[:-1]:
        raise ValueError("pit_intermediates issues ONE statement at a time; "
                         "a second statement after a semicolon is refused")
    return sql


def _select(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[Any]:
    return list(conn.execute(read_only_sql(sql), tuple(params)).fetchall())


def _cell(row: Any, name: str, index: int) -> Any:
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return row[index]


def load_snapshot(conn: Any, entity_id: int, as_of_date: str, *,
                  model_version: str, replay_run_id: int) -> Optional[ScoreSnapshot]:
    """One `ScoreSnapshot` from `pit_score` + `pit_pillar_score`. SELECT only.

    Returns None when the snapshot is absent, which is a first-class answer:
    `explain_change` turns it into NO_PRIOR_SNAPSHOT rather than treating a
    missing prior as a zero, because a score that "rose from zero" because
    nothing was stored is the most confident lie this system could tell.

    TODAY IT RETURNS None FOR EVERYTHING. pit_score holds 0 rows and the
    official replay has not run. That is correct and expected; the function is
    the landing site, and `worked_example()` exercises the same attribution on
    constructed snapshots so the shape is inspectable before a single row
    exists.
    """
    score_rows = _select(conn, (
        "SELECT final_score, sector_overlay FROM pit_score "
        "WHERE entity_id = ? AND as_of_date = ? AND model_version = ? "
        "  AND replay_run_id = ?"
    ), (entity_id, as_of_date, model_version, replay_run_id))
    if not score_rows:
        return None
    final_score = _cell(score_rows[0], "final_score", 0)
    overlay = _cell(score_rows[0], "sector_overlay", 1)

    pillar_rows = _select(conn, (
        "SELECT pillar, pillar_score, original_weight, effective_weight, "
        "availability, unavailable_reason, n_subfactors_declared, "
        "n_subfactors_resolved, subfactor_mask, peer_set_id, benchmark_level "
        "FROM pit_pillar_score "
        "WHERE entity_id = ? AND as_of_date = ? AND model_version = ? "
        "  AND replay_run_id = ? ORDER BY pillar"
    ), (entity_id, as_of_date, model_version, replay_run_id))
    if not pillar_rows:
        return None

    parts: list[Part] = []
    peer_set_id: Optional[int] = None
    benchmark_level = ""
    for row in pillar_rows:
        key = _cell(row, "pillar", 0)
        available = _cell(row, "availability", 4) == AVAIL_RESOLVED
        mask = _cell(row, "subfactor_mask", 8) or ""
        resolved = tuple(
            name for name in SUBFACTORS.get(key, ())
            if f"{SUBFACTOR_LETTER.get(name, name)}=1" in mask)
        parts.append(Part(
            key=key,
            value=_cell(row, "pillar_score", 1) if available else None,
            weight=float(_cell(row, "original_weight", 2) or 0.0),
            available=available,
            reason=_cell(row, "unavailable_reason", 5) or "",
            n_subfactors_declared=int(_cell(row, "n_subfactors_declared", 6) or 0),
            n_subfactors_resolved=int(_cell(row, "n_subfactors_resolved", 7) or 0),
            subfactors=resolved,
            price_dependent=PRICE_DEPENDENT_PILLAR.get(key, False),
        ))
        peer_set_id = peer_set_id if peer_set_id is not None else _cell(row, "peer_set_id", 9)
        benchmark_level = benchmark_level or (_cell(row, "benchmark_level", 10) or "")

    order = {k: i for i, k in enumerate(PILLARS)}
    parts.sort(key=lambda p: order.get(p.key, len(PILLARS)))
    return ScoreSnapshot(
        entity_id=entity_id, as_of_date=as_of_date, parts=tuple(parts),
        overlay=overlay, final_score=final_score, peer_set_id=peer_set_id,
        benchmark_level=benchmark_level, model_version=model_version,
        replay_run_id=replay_run_id)


def load_cohort_stat(conn: Any, as_of_date: str, rung: str, key: str, *,
                     model_version: str, peer_set_version: str,
                     replay_run_id: int) -> Optional[dict[str, Any]]:
    """One `pit_cohort_stat` row as a dict, or None. SELECT only."""
    rows = _select(conn, (
        "SELECT n_sector_peers, n_ebitda_resolved, n_cohort_members, ebitda_p50, "
        "ebitda_p75, cohort_mean_ebitda, cohort_mean_margin, cohort_mean_ev_ebitda, "
        "n_eligible_json, n_eligible_min, binding_factor, eligibility_rule "
        "FROM pit_cohort_stat WHERE as_of_date = ? AND rung = ? AND key = ? "
        "  AND model_version = ? AND peer_set_version = ? AND replay_run_id = ?"
    ), (as_of_date, rung, key, model_version, peer_set_version, replay_run_id))
    if not rows:
        return None
    names = ("n_sector_peers", "n_ebitda_resolved", "n_cohort_members", "ebitda_p50",
             "ebitda_p75", "cohort_mean_ebitda", "cohort_mean_margin",
             "cohort_mean_ev_ebitda", "n_eligible_json", "n_eligible_min",
             "binding_factor", "eligibility_rule")
    out = {name: _cell(rows[0], name, i) for i, name in enumerate(names)}
    raw = out.get("n_eligible_json")
    out["n_eligible"] = json.loads(raw) if raw else {}
    return out


# ==========================================================================
# (5) THE ATTRIBUTION ENGINE
# ==========================================================================

@dataclass(frozen=True)
class Term:
    """One named piece of an observed score change.

    `points` is signed and is expressed in FINAL SCORE POINTS, not in blend
    units, because the reader's question is "why did my score move six points"
    and a decomposition in another unit answers a different question.

    `kind` decides whether a term may be presented as an economic move.
    `cause` says WHY, and is never empty on a weight, entry or exit term: an
    availability change with no named cause is exactly the thing that gets
    mis-read as a downgrade.
    """

    kind: str
    key: str
    label: str
    points: float
    cause: str = ""
    detail: str = ""
    price_dependent: bool = False

    @property
    def economic(self) -> bool:
        return self.kind in ECONOMIC_TERM_KINDS and self.cause != CAUSE_PEER_SET_CHANGED


@dataclass(frozen=True)
class BlendDecomposition:
    """The exact decomposition of one renormalising blend between two dates.

    THE INVARIANT, and the reason this type exists separately from
    `Attribution`: `residual` is zero to floating-point tolerance, ALWAYS. The
    five effects are an algebraic identity over the blend, not an estimate, so
    a non-zero residual here is a bug in this function and nothing else --
    which is what makes the reconciliation test meaningful. Clamps and overlays
    are handled one level up precisely so they cannot hide inside this number.
    """

    terms: tuple[Term, ...]
    observed: float
    factor_effect: float
    weight_effect: float
    interaction_effect: float
    entry_effect: float
    exit_effect: float
    residual: float
    entered: tuple[str, ...]
    exited: tuple[str, ...]

    @property
    def reconciles(self) -> bool:
        return abs(self.residual) <= 1e-7

    @property
    def total(self) -> float:
        return (self.factor_effect + self.weight_effect + self.interaction_effect
                + self.entry_effect + self.exit_effect)


def decompose_blend(before: Sequence[Part], after: Sequence[Part], *,
                    scale: float = 1.0, peer_set_changed: bool = False,
                    labels: Optional[Mapping[str, str]] = None
                    ) -> BlendDecomposition:
    """Split the change in a renormalising blend into named, exact terms.

    THE ARITHMETIC, for i available at BOTH dates:

        w_t F_t - w_s F_s == w_s dF + F_s dw + dw dF

    which is an identity, so summing it over the shared members and adding the
    entry and exit terms reproduces the observed change exactly. Nothing here
    is an approximation and nothing is left over; `residual` exists to be
    asserted at zero, not to absorb.

    WHY THE SPLIT IS THE POINT. Both the values and the weights move between
    two dates, because the weights are renormalised over whatever resolved. A
    decomposition that charged everything to values would report an
    availability change as an economic one -- the failure that makes a
    VGPD -> GPD transition look like an eleven-point downgrade. Here the weight
    effect is its own line, every weight line carries a CAUSE, and a member
    that entered or left is a third kind of term again, because it is neither a
    value move nor a weight move.

    `scale` multiplies every term, so a blend that is later multiplied by 0.75
    can be reported in the units the reader sees.

    `peer_set_changed` is not cosmetic. Every pillar but valuation is a
    percentile rank, so a company's value can move because its PEERS moved.
    That is not the company changing and the cause label says so.

    Used for the major blend and for the blend inside a pillar. One engine.
    """
    labels = dict(labels or {})
    b = {p.key: p for p in before}
    a = {p.key: p for p in after}
    wb = effective_weights(before)
    wa = effective_weights(after)

    shared = [k for k in PILLARS if k in wb and k in wa]
    shared += sorted(k for k in set(wb) & set(wa) if k not in PILLARS)
    entered = tuple([k for k in PILLARS if k in wa and k not in wb]
                    + sorted(k for k in set(wa) - set(wb) if k not in PILLARS))
    exited = tuple([k for k in PILLARS if k in wb and k not in wa]
                   + sorted(k for k in set(wb) - set(wa) if k not in PILLARS))

    def name(key: str) -> str:
        return labels.get(key) or (b.get(key) or a.get(key)).display

    # WHY a surviving member's weight moved. Entry and exit are the ordinary
    # reason; declared-weight drift is the only other possibility and means the
    # two snapshots came from two models, which must be said out loud.
    if entered or exited:
        moved = []
        if entered:
            moved.append("entered: " + ", ".join(name(k) for k in entered))
        if exited:
            moved.append("left: " + ", ".join(name(k) for k in exited))
        weight_cause = CAUSE_RENORMALISATION
        weight_detail = "; ".join(moved)
        if peer_set_changed:
            weight_detail += " (peer set also changed)"
    else:
        weight_cause = CAUSE_DECLARED_WEIGHTS_CHANGED
        weight_detail = ("no member entered or left, so a weight move can only "
                         "mean the DECLARED weights differ -- these are two models")

    terms: list[Term] = []
    factor_effect = weight_effect = interaction_effect = 0.0

    for key in shared:
        fb, fa = float(b[key].value), float(a[key].value)
        dw, df = wa[key] - wb[key], fa - fb
        price = bool(b[key].price_dependent or a[key].price_dependent)

        # The factor term is emitted for EVERY shared member, including the
        # ones that did not move. "Debt/quality: 0.0" is a statement -- we
        # looked and it contributed nothing -- while omitting the line says
        # nothing at all, and a reader cannot tell a zero from an oversight.
        points = scale * wb[key] * df
        factor_effect += points
        terms.append(Term(
            TERM_FACTOR, key, name(key), points,
            cause=CAUSE_PEER_SET_CHANGED if peer_set_changed else CAUSE_ECONOMIC,
            detail=(f"{fb:+.3f} -> {fa:+.3f} at the prior weight "
                    f"{wb[key]:.4f}"),
            price_dependent=price))

        points = scale * fb * dw
        weight_effect += points
        if abs(dw) > _TOL:
            terms.append(Term(
                TERM_WEIGHT, key, name(key), points, cause=weight_cause,
                detail=(f"weight {wb[key]:.4f} -> {wa[key]:.4f} at the prior "
                        f"value {fb:+.3f}; {weight_detail}"),
                price_dependent=price))

        points = scale * dw * df
        interaction_effect += points
        if abs(points) > _TOL:
            terms.append(Term(
                TERM_INTERACTION, key, name(key), points, cause=weight_cause,
                detail=(f"the cross term: weight moved {dw:+.4f} while the value "
                        f"moved {df:+.3f}"),
                price_dependent=price))

    entry_effect = 0.0
    for key in entered:
        points = scale * wa[key] * float(a[key].value)
        entry_effect += points
        terms.append(Term(
            TERM_ENTRY, key, name(key), points, cause=CAUSE_BECAME_AVAILABLE,
            detail=(f"absent at the earlier date, present at {wa[key]:.4f} x "
                    f"{float(a[key].value):+.3f}"
                    + (" -- the peer set also changed" if peer_set_changed else "")),
            price_dependent=bool(a[key].price_dependent)))

    exit_effect = 0.0
    for key in exited:
        points = -scale * wb[key] * float(b[key].value)
        exit_effect += points
        why = (a.get(key).reason if a.get(key) is not None else "") or ""
        terms.append(Term(
            TERM_EXIT, key, name(key), points, cause=CAUSE_BECAME_UNAVAILABLE,
            detail=(f"present at {wb[key]:.4f} x {float(b[key].value):+.3f}, "
                    f"absent at the later date"
                    + (f" ({why})" if why else "")
                    + (" -- the peer set also changed" if peer_set_changed else "")),
            price_dependent=bool(b[key].price_dependent)))

    vb, va = blend_value(before), blend_value(after)
    observed = scale * ((va if va is not None else 0.0)
                        - (vb if vb is not None else 0.0))
    total = (factor_effect + weight_effect + interaction_effect
             + entry_effect + exit_effect)
    return BlendDecomposition(
        terms=tuple(terms), observed=observed, factor_effect=factor_effect,
        weight_effect=weight_effect, interaction_effect=interaction_effect,
        entry_effect=entry_effect, exit_effect=exit_effect,
        residual=observed - total, entered=entered, exited=exited)


@dataclass(frozen=True)
class Attribution:
    """The full answer to "why did this score change?", at final-score scale.

    THE INVARIANT: `sum(t.points for t in terms) == observed_delta` to
    tolerance, and `state` is RECONCILIATION_FAILED when it does not hold. A
    clamp is the ONLY term permitted to be inferred from a residual, and it is
    only emitted when a clamp is independently confirmed to have bound -- so
    the clamp line can never become a bin for unexplained points.
    """

    state: str
    entity_id: int
    as_of_from: str
    as_of_to: str
    score_from: Optional[float]
    score_to: Optional[float]
    observed_delta: Optional[float]
    terms: tuple[Term, ...]
    blend: Optional[BlendDecomposition]
    signature_from: str
    signature_to: str
    coverage_from: float
    coverage_to: float
    residual: float
    message: str
    peer_set_changed: bool = False
    price_dependent: bool = False
    problems: tuple[str, ...] = ()

    @property
    def reconciles(self) -> bool:
        return self.state != STATE_RECONCILIATION_FAILED and abs(self.residual) <= 1e-7

    @property
    def comparable(self) -> bool:
        return self.signature_from == self.signature_to

    @property
    def headline_delta(self) -> Optional[float]:
        """The net point move -- or None when the scores are NOT COMPARABLE.

        This property is the first UI rule, encoded. When the signature changed
        there is no honest point move to show, so there is no number to take:
        a front-end that renders `headline_delta` cannot accidentally render a
        VGPD -> GPD transition as a downgrade, because there is nothing there.
        """
        if self.state == STATE_COMPARABILITY_CHANGED:
            return None
        return self.observed_delta

    def by_kind(self, kind: str) -> tuple[Term, ...]:
        return tuple(t for t in self.terms if t.kind == kind)

    def effect(self, kind: str) -> float:
        return sum(t.points for t in self.by_kind(kind))

    @property
    def economic_terms(self) -> tuple[Term, ...]:
        return tuple(t for t in self.terms if t.economic)

    @property
    def availability_terms(self) -> tuple[Term, ...]:
        return tuple(t for t in self.terms
                     if t.kind in (TERM_WEIGHT, TERM_ENTRY, TERM_EXIT,
                                   TERM_INTERACTION))


def explain_change(conn: Any, entity_id: int, as_of_from: str, as_of_to: str, *,
                   model_version: str = "", replay_run_id: int = 0,
                   snapshot_from: Optional[ScoreSnapshot] = None,
                   snapshot_to: Optional[ScoreSnapshot] = None,
                   ) -> Attribution:
    """Decompose one entity's score change between two as-of dates.

    ONE FUNCTION FOR BOTH HALVES OF THE SYSTEM. `available_date` is the shared
    event clock: a fact enters the model on the session the latency policy
    assigned it, and that rule is indifferent to whether `as_of_to` is
    2019-06-28 or today. So the historical replay calls this with two past
    dates and the live daily job calls it with yesterday and today, on the same
    rows written by the same builders. There is no second engine to drift.

    `conn` comes first, house style, and is used ONLY to SELECT the two
    snapshots. Pass `snapshot_from` / `snapshot_to` and `conn` is never touched
    -- which is how the live job explains a score it is holding in memory, and
    how the tests run without a database.

    The returned `Attribution` carries every term, and its `state` encodes the
    first UI rule: a signature change is COMPARABILITY_CHANGED with no headline
    point move, never a downgrade.
    """
    # `conn is None` means SNAPSHOT MODE: the caller is holding the rows and a
    # None snapshot is a positive statement -- "there is no score at that
    # date" -- rather than "please go and load it". The distinction is not
    # pedantry: the live job's first-ever run for a name legitimately has no
    # prior, and the difference between "absent" and "unfetched" is the
    # difference between NO_PRIOR_SNAPSHOT and a crash.
    if conn is not None:
        if snapshot_from is None:
            snapshot_from = load_snapshot(conn, entity_id, as_of_from,
                                          model_version=model_version,
                                          replay_run_id=replay_run_id)
        if snapshot_to is None:
            snapshot_to = load_snapshot(conn, entity_id, as_of_to,
                                        model_version=model_version,
                                        replay_run_id=replay_run_id)
    elif snapshot_from is None and snapshot_to is None:
        raise ValueError(
            "explain_change needs a connection, or at least one snapshot; "
            "two Nones and no connection says nothing at all")

    if snapshot_to is None or snapshot_from is None:
        missing = as_of_from if snapshot_from is None else as_of_to
        return Attribution(
            state=STATE_NO_PRIOR, entity_id=entity_id, as_of_from=as_of_from,
            as_of_to=as_of_to,
            score_from=snapshot_from.final_score if snapshot_from else None,
            score_to=snapshot_to.final_score if snapshot_to else None,
            observed_delta=None, terms=(), blend=None,
            signature_from=snapshot_from.signature if snapshot_from else "",
            signature_to=snapshot_to.signature if snapshot_to else "",
            coverage_from=snapshot_from.original_weight_coverage if snapshot_from else 0.0,
            coverage_to=snapshot_to.original_weight_coverage if snapshot_to else 0.0,
            residual=0.0,
            message=(f"No score snapshot at {missing}. A missing prior is NOT a "
                     f"zero: a score that 'rose from nothing' because nothing "
                     f"was stored would be the most confident lie this system "
                     f"could tell."))

    problems = tuple(snapshot_from.problems() + snapshot_to.problems())
    before, after = snapshot_from.parts, snapshot_to.parts
    peer_set_changed = (snapshot_from.peer_set_id is not None
                        and snapshot_to.peer_set_id is not None
                        and snapshot_from.peer_set_id != snapshot_to.peer_set_id)

    raw_from, raw_to = snapshot_from.raw_score(), snapshot_to.raw_score()
    score_from = snapshot_from.final_score
    score_to = snapshot_to.final_score
    if score_from is None:
        score_from = snapshot_from.recompute()
    if score_to is None:
        score_to = snapshot_to.recompute()

    if raw_from is None or raw_to is None or score_from is None or score_to is None:
        return Attribution(
            state=STATE_NO_SCORE, entity_id=entity_id, as_of_from=as_of_from,
            as_of_to=as_of_to, score_from=score_from, score_to=score_to,
            observed_delta=None, terms=(), blend=None,
            signature_from=snapshot_from.signature,
            signature_to=snapshot_to.signature,
            coverage_from=snapshot_from.original_weight_coverage,
            coverage_to=snapshot_to.original_weight_coverage,
            residual=0.0, problems=problems,
            message=("No major factor resolved at one of the two dates, so "
                     "there is no score and nothing to decompose."))

    scale = snapshot_to.scale
    blend = decompose_blend(before, after, scale=scale,
                            peer_set_changed=peer_set_changed)
    terms: list[Term] = list(blend.terms)

    overlay_from = float(snapshot_from.overlay or 0.0)
    overlay_to = float(snapshot_to.overlay or 0.0)
    d_overlay = overlay_to - overlay_from
    if abs(d_overlay) > _TOL:
        terms.append(Term(
            TERM_OVERLAY, "sector", "Sector overlay", d_overlay,
            cause=CAUSE_SECTOR_OVERLAY,
            detail=(f"{overlay_from:+.3f} -> {overlay_to:+.3f}; the overlay is "
                    f"ADDITIVE and outside the renormalised blend, so it never "
                    f"moves a pillar weight")))

    observed = score_to - score_from
    residual = observed - sum(t.points for t in terms)

    # A clamp is the only term permitted to be inferred from a residual, and it
    # is only emitted when a clamp is independently confirmed to have bound.
    # Otherwise the clamp line would be a bin for unexplained points, which is
    # the failure mode this whole module exists to prevent.
    state = STATE_ECONOMIC_CHANGE
    clamp_from, clamp_to = snapshot_from.clamp_binding(), snapshot_to.clamp_binding()
    if abs(residual) > 1e-7:
        if clamp_from or clamp_to:
            bound = " and ".join(x for x in (
                f"at {as_of_from}: {clamp_from}" if clamp_from else "",
                f"at {as_of_to}: {clamp_to}" if clamp_to else "") if x)
            terms.append(Term(
                TERM_CLAMP, "clamp", "Clamp", residual, cause=CAUSE_CLAMP,
                detail=(f"the score hit a bound ({bound}); the blend moved more "
                        f"than the reported score could")))
            residual = observed - sum(t.points for t in terms)
        else:
            state = STATE_RECONCILIATION_FAILED

    signature_from, signature_to = snapshot_from.signature, snapshot_to.signature
    message = ""
    if state == STATE_RECONCILIATION_FAILED:
        message = (f"The terms do not sum to the observed change "
                   f"({observed:+.6f}); {residual:+.6f} is unexplained and no "
                   f"clamp bound. These snapshots are inconsistent or came from "
                   f"different models -- the gap is reported, not smoothed.")
    elif signature_from != signature_to:
        state = STATE_COMPARABILITY_CHANGED
        message = comparability_message(signature_from, signature_to)
    elif abs(observed) <= _TOL:
        state = STATE_UNCHANGED
        message = "No change."
    else:
        message = (f"Shaffer Score {score_from:+.1f} -> {score_to:+.1f} "
                   f"({observed:+.1f}), signature {signature_to}.")

    return Attribution(
        state=state, entity_id=entity_id, as_of_from=as_of_from,
        as_of_to=as_of_to, score_from=score_from, score_to=score_to,
        observed_delta=observed, terms=tuple(terms), blend=blend,
        signature_from=signature_from, signature_to=signature_to,
        coverage_from=snapshot_from.original_weight_coverage,
        coverage_to=snapshot_to.original_weight_coverage,
        residual=residual, message=message, peer_set_changed=peer_set_changed,
        price_dependent=snapshot_from.price_dependent or snapshot_to.price_dependent,
        problems=problems)


# ==========================================================================
# (6) THE RENDERER
# ==========================================================================

def _points(value: float, width: int = 6) -> str:
    """One signed contribution, rendered for a reader checking the arithmetic.

    A term that rounds to nothing prints '0.0', not '+0.0' or '-0.0'. A signed
    zero reads as a small move that got rounded, which is the opposite of what
    it means; unsigned says "nothing, to one decimal place". The exact value is
    still on `Term.points` for anyone who needs it.
    """
    if abs(value) < 0.05:
        return "0.0".rjust(width)
    return f"{value:+.1f}".rjust(width)


def render_change(att: Attribution, *, width: int = 66,
                  show_causes: bool = True) -> str:
    """The owner's output shape: factor name, signed contribution, net.

    Two rules are enforced here rather than left to a front-end:

      1. In COMPARABILITY_CHANGED there is NO net line and no point move. The
         reader is told the comparison broke and which input went; the
         comparable part is still itemised, clearly labelled as partial.
      2. Availability terms never sit in the same block as economic ones. A
         renormalisation is not a company doing anything, and putting the two
         in one list is how one gets read as the other.
    """
    out: list[str] = []
    rule = "=" * width
    out.append(rule)
    out.append(f"WHY IT CHANGED  --  entity {att.entity_id}   "
               f"{att.as_of_from} -> {att.as_of_to}")
    out.append(rule)

    if att.state == STATE_COMPARABILITY_CHANGED:
        out.append(att.message)
        out.append("")
        out.append(f"  signature   {att.signature_from} -> {att.signature_to}")
        out.append(f"  model weight covered   {att.coverage_from:.2f} -> "
                   f"{att.coverage_to:.2f}")
        out.append("")
        out.append("  The two scores are NOT COMPARABLE, so no point move is")
        out.append("  reported. What follows is the comparable part only.")
    elif att.state == STATE_NO_PRIOR or att.state == STATE_NO_SCORE:
        out.append(att.message)
        return "\n".join(out)
    elif att.state == STATE_RECONCILIATION_FAILED:
        out.append("RECONCILIATION FAILED")
        out.append(att.message)
    else:
        out.append(f"  Shaffer Score   {att.score_from:+.1f} -> {att.score_to:+.1f}"
                   f"      signature {att.signature_to}")
    out.append("")

    economic = att.economic_terms
    if economic:
        out.append("  WHAT CHANGED")
        for term in economic:
            mark = "  *" if term.price_dependent else "   "
            out.append(f"    {term.label + ':':<22}{_points(term.points)}{mark}")

    peer_terms = tuple(t for t in att.terms
                       if t.kind == TERM_FACTOR and t.cause == CAUSE_PEER_SET_CHANGED)
    if peer_terms:
        out.append("")
        out.append("  THE RULER MOVED (peer set changed -- not the company)")
        for term in peer_terms:
            out.append(f"    {term.label + ':':<22}{_points(term.points)}")

    availability = att.availability_terms
    if availability:
        out.append("")
        out.append("  AVAILABILITY / RENORMALISATION (not an economic move)")
        for term in availability:
            out.append(f"    {term.label + ' [' + term.kind + ']:':<22}"
                       f"{_points(term.points)}")
            if show_causes:
                out.append(f"      cause: {term.cause}")
                out.append(f"      {term.detail}")

    clamps = att.by_kind(TERM_CLAMP)
    if clamps:
        out.append("")
        out.append("  CLAMP")
        for term in clamps:
            out.append(f"    {term.label + ':':<22}{_points(term.points)}")
            out.append(f"      {term.detail}")

    out.append("")
    if att.state == STATE_COMPARABILITY_CHANGED:
        out.append("    " + "-" * 34)
        out.append("    Net:                   not comparable")
    else:
        out.append("    " + "-" * 28)
        out.append(f"    {'Net:':<22}{_points(att.observed_delta)}")
        out.append("")
        out.append(f"    reconciles: {att.reconciles}   "
                   f"residual {att.residual:+.2e}")

    if att.price_dependent:
        out.append("")
        out.append("  * SURVIVOR_ONLY_DIAGNOSTIC -- this line depends on a price,")
        out.append("    and the price sample is 2,574 listings, all alive in 2026.")
    if att.problems:
        out.append("")
        out.append("  SNAPSHOT PROBLEMS")
        for problem in att.problems:
            out.append(f"    - {problem}")
    return "\n".join(line.rstrip() for line in out)


# ==========================================================================
# (7) THE THREE UI RULES, AS OUTPUT STATES
# ==========================================================================

_PILLAR_NOUN = {"V": "valuation", "G": "growth", "P": "profitability",
                "D": "debt"}


def comparability_message(signature_from: str, signature_to: str) -> str:
    """The sentence a signature change must render as. RULE 1.

    For the canonical case -- VGPD -> GPD -- this returns exactly

        Score comparability changed — valuation input unavailable

    and NOT "Shaffer Score fell 11 points", which is the reading it exists to
    make impossible. A signature change is a change in WHAT WAS MEASURED. The
    company may have done nothing at all; usually it has not.
    """
    lost = [c for c in signature_from if c not in signature_to]
    gained = [c for c in signature_to if c not in signature_from]
    if not lost and not gained:
        return "Score comparability unchanged."
    parts: list[str] = []
    if lost:
        nouns = [_PILLAR_NOUN.get(c, c) for c in lost]
        parts.append(f"{_join(nouns)} input{'s' if len(nouns) > 1 else ''} "
                     f"unavailable")
    if gained:
        nouns = [_PILLAR_NOUN.get(c, c) for c in gained]
        parts.append(f"{_join(nouns)} input{'s' if len(nouns) > 1 else ''} "
                     f"now available")
    return "Score comparability changed — " + "; ".join(parts)


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


#: Effective observations per horizon. MEASURED where it says measured; the 1M
#: and 3M figures are PROJECTIONS and are labelled, because a projected n_eff
#: quoted as a measured one is the same error one level up.
#:
#: These are effective observations, never rows. 355,727 twelve-month label
#: rows are worth 160.7 independent observations once overlap and
#: cross-sectional correlation are taken out.
MEASURED_EVIDENCE: dict[str, dict[str, Any]] = {
    "1M": {"n_effective": 1450.0, "n_folds": 5, "basis": "projected"},
    "3M": {"n_effective": 480.0, "n_folds": 5, "basis": "projected"},
    "12M": {"n_effective": 160.7, "n_folds": 5, "basis": "measured"},
    "monthly_panel": {"n_effective": 2035.6, "n_folds": 5, "basis": "measured"},
}


def horizon_forecast(horizon: str, point_estimate: Optional[float] = None, *,
                     n_effective: Optional[float] = None,
                     n_folds: Optional[int] = None,
                     target_effect: float = 0.05) -> dict[str, Any]:
    """A forecast, or INSUFFICIENT_EVIDENCE. Never a number the data can't make.

    RULE 2. A horizon whose evidence cannot distinguish the effect of interest
    from zero does not get a number -- in either direction. `point_estimate` is
    dropped rather than shown, because a number that reaches a screen gets
    quoted, and a 12M rank IC of 0.04 from this store is noise with a decimal
    point on it: 160.7 independent observations detect nothing below rho 0.155.

    Delegates the verdict to `pit_invariants.evidence_status` rather than
    re-implementing the arithmetic, so there is one definition of sufficiency
    in the project and the UI cannot hold a softer one than the research layer.
    """
    known = MEASURED_EVIDENCE.get(horizon, {})
    n_eff = n_effective if n_effective is not None else known.get("n_effective")
    folds = n_folds if n_folds is not None else known.get("n_folds")
    if n_eff is None or folds is None:
        return {"horizon": horizon, "status": pit_invariants.STATUS_INSUFFICIENT,
                "supported": False, "point_estimate": None,
                "display": "Insufficient evidence",
                "reason": (f"no effective-observation count is recorded for "
                           f"{horizon!r}; a horizon with no measured n_eff is "
                           f"unsupported until someone measures it"),
                "basis": "unmeasured"}
    verdict = pit_invariants.evidence_status(float(n_eff), int(folds),
                                             target_effect=target_effect)
    supported = verdict.sufficient
    return {
        "horizon": horizon,
        "status": verdict.status,
        "supported": supported,
        "n_effective": float(n_eff),
        "n_folds": int(folds),
        "detectable_rho": verdict.detectable,
        "target_effect": target_effect,
        "basis": known.get("basis", "caller_supplied"),
        "point_estimate": point_estimate if supported else None,
        "display": (f"{point_estimate:+.2f}%" if supported and point_estimate is not None
                    else ("Supported" if supported else "Insufficient evidence")),
        "reason": verdict.reason,
    }


def supported_horizons(horizons: Sequence[str] = ("1M", "3M", "12M"),
                       ) -> dict[str, Any]:
    """Which horizons the store can currently support, and which it cannot.

    Today the honest answer is uncomfortable and is reported as it is: only the
    MONTHLY PANEL clears the bar, at n_eff 2,035.6 detecting rho 0.043. 12M
    fails badly (160.7, detects 0.155), 3M fails (480, detects 0.090) and even
    1M fails narrowly (1,450, detects 0.0515 against a target of 0.05) -- and
    the near miss is said out loud rather than rounded into a pass.
    """
    rows = {h: horizon_forecast(h) for h in horizons}
    panel = horizon_forecast("monthly_panel")
    return {
        "supported": sorted(h for h, r in rows.items() if r["supported"]),
        "unsupported": sorted(h for h, r in rows.items() if not r["supported"]),
        "detail": rows,
        "monthly_panel": panel,
        "note": ("effective observations, never rows. The only cell that clears "
                 "the bar today is the monthly panel; 1M misses it narrowly at "
                 "0.0515 against 0.05, and a narrow miss is still a miss."),
    }


HEDGE_LIVE_ONLY = "LIVE_RECOMMENDATION_ONLY"
HEDGE_VALIDATED = "HISTORICALLY_VALIDATED"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _pretty_date(iso: str) -> str:
    try:
        year, month, day = iso.split("-")
        return f"{_MONTHS[int(month) - 1]} {int(day)}, {year}"
    except (ValueError, IndexError):
        return iso


def hedge_research_status(as_of_date: str) -> dict[str, Any]:
    """What a hedge recommendation may claim on this date. RULE 3.

    The recommendation itself is available NOW: the rule is arithmetic on a
    score and needs no history. What is NOT available is historical validation
    before 2026-09-20, because an option chain is a snapshot of a moment and
    `pit_derive.UNAVAILABLE_REGISTER` classes a pre-archive quote as genuinely
    unavailable -- nobody sells the moment back afterwards.

    Saying both plainly is the whole point. "Live recommendation / Historical
    validation available from Sep 20, 2026 onward" is transparent without
    making the product read as unfinished, and it is the difference between a
    known limit and a hidden one.
    """
    validated = as_of_date >= OPTION_ARCHIVE_START
    pretty = _pretty_date(OPTION_ARCHIVE_START)
    if validated:
        display = ("Live recommendation\nHistorical validation available from "
                   f"{pretty} onward")
    else:
        # A replay CAN compute the ratio for 2019 -- it is arithmetic on a
        # score -- and CANNOT price the ticket, because the chain that day was
        # never recorded. Saying "validation available from Sep 20, 2026" here
        # would read as a promise about this row; it is the opposite.
        display = ("Recommendation reproducible from the score\n"
                   f"NOT historically validated: no option data before {pretty}")
    return {
        "as_of_date": as_of_date,
        "recommendation_available": True,
        "historical_validation_available": validated,
        "validation_available_from": OPTION_ARCHIVE_START,
        "research_status": HEDGE_VALIDATED if validated else HEDGE_LIVE_ONLY,
        "display": display,
        "why": ("the option archive began " + OPTION_ARCHIVE_START +
                " and chains are not retro-sold"),
    }


# ==========================================================================
# (8) THE WORKED EXAMPLE -- one engine, two clocks
# ==========================================================================
#
# Fixture data, in the spirit of pit_derive.FIXTURE_VALUES: realistic,
# internally consistent, and NOT read from the store. No database is opened to
# produce any of this. The historical pair and the live pair go through the
# identical call, which is the demonstration.

def _part(key: str, value: Optional[float], *, available: bool = True,
          reason: str = "", subfactors: Sequence[str] = ()) -> Part:
    declared = SUBFACTORS.get(key, ())
    subs = tuple(subfactors) if subfactors else (declared if available else ())
    return Part(key=key, value=value, weight=MAJOR_WEIGHTS_DECLARED[key],
                available=available, reason=reason,
                n_subfactors_declared=len(declared),
                n_subfactors_resolved=len(subs), subfactors=subs,
                price_dependent=PRICE_DEPENDENT_PILLAR.get(key, False))


def _snapshot(as_of: str, values: Mapping[str, Optional[float]], overlay: float,
              *, peer_set_id: int = 4101, missing: Mapping[str, str] = {},
              subfactors: Mapping[str, Sequence[str]] = {}) -> ScoreSnapshot:
    parts = tuple(
        _part(k, values.get(k), available=k not in missing,
              reason=missing.get(k, ""), subfactors=subfactors.get(k, ()))
        for k in PILLARS)
    snap = ScoreSnapshot(entity_id=8812, as_of_date=as_of, parts=parts,
                         overlay=overlay, peer_set_id=peer_set_id,
                         benchmark_level="Industry",
                         model_version="equity_shaffer_v1_pit", replay_run_id=1)
    return ScoreSnapshot(
        entity_id=snap.entity_id, as_of_date=snap.as_of_date, parts=snap.parts,
        overlay=snap.overlay, final_score=snap.recompute(),
        peer_set_id=snap.peer_set_id, benchmark_level=snap.benchmark_level,
        model_version=snap.model_version, replay_run_id=snap.replay_run_id)


#: Pillar values chosen so the headline reads +58.0 -> +64.0 and the terms come
#: out at the owner's own magnitudes. Arithmetic, not decoration:
#:   raw = 0.40V + 0.25G + 0.20P + 0.15D; company = 0.75 x raw; final = + overlay
FIXTURE: dict[str, Any] = {
    "entity_id": 8812,
    "note": ("A mid-cap industrial, SIC 3559, the same fixture company as "
             "pit_derive's trace. FIXTURE values, not store reads: no database "
             "was opened to produce any number here."),
    "hist_from": {"as_of": "2019-05-31",
                  "values": {"valuation": 75.41666666666667, "growth": 65.0,
                             "profitability": 60.0, "debt": 55.0},
                  "overlay": 8.0},
    "hist_to": {"as_of": "2019-06-28",
                "values": {"valuation": 72.41666666666667, "growth": 74.6,
                           "profitability": 80.66666666666666, "debt": 55.0},
                "overlay": 10.0},
    "live_from": {"as_of": "2026-08-31",
                  "values": {"valuation": 40.0, "growth": 30.0,
                             "profitability": 25.0, "debt": 20.0},
                  "overlay": 4.0},
    "live_to": {"as_of": "2026-09-21",
                "values": {"valuation": 40.0, "growth": 44.0,
                           "profitability": 25.0, "debt": 20.0},
                "overlay": 4.0},
}


def worked_example(*, width: int = 66) -> str:
    """Render the whole thing: historical, live, and the three UI rules.

    The historical pair and the live pair are decomposed by the SAME call with
    the SAME arguments shape. That is the unity claim, executed rather than
    asserted: `available_date` is the shared event clock, so "what was knowable
    then" and "what just became knowable now" are one question asked twice.
    """
    out: list[str] = []
    hist_from = _snapshot(FIXTURE["hist_from"]["as_of"],
                          FIXTURE["hist_from"]["values"],
                          FIXTURE["hist_from"]["overlay"])
    hist_to = _snapshot(FIXTURE["hist_to"]["as_of"],
                        FIXTURE["hist_to"]["values"],
                        FIXTURE["hist_to"]["overlay"])
    live_from = _snapshot(FIXTURE["live_from"]["as_of"],
                          FIXTURE["live_from"]["values"],
                          FIXTURE["live_from"]["overlay"])
    live_to = _snapshot(FIXTURE["live_to"]["as_of"],
                        FIXTURE["live_to"]["values"],
                        FIXTURE["live_to"]["overlay"])

    out.append("#" * width)
    out.append("# HISTORICAL REPLAY  --  'what was knowable then?'")
    out.append("#" * width)
    out.append(render_change(explain_change(
        None, FIXTURE["entity_id"], hist_from.as_of_date, hist_to.as_of_date,
        snapshot_from=hist_from, snapshot_to=hist_to), width=width))

    out.append("")
    out.append("#" * width)
    out.append("# LIVE DAILY JOB  --  'what just became knowable now?'")
    out.append("#  SAME function, SAME arguments, different clock position.")
    out.append("#" * width)
    out.append(render_change(explain_change(
        None, FIXTURE["entity_id"], live_from.as_of_date, live_to.as_of_date,
        snapshot_from=live_from, snapshot_to=live_to), width=width))

    # RULE 1: the signature change. Valuation leaves; the company does nothing.
    gpd = _snapshot("2026-09-21",
                    {"valuation": None, "growth": 44.0, "profitability": 25.0,
                     "debt": 20.0}, 4.0,
                    missing={"valuation": "insufficient_peers"})
    out.append("")
    out.append("#" * width)
    out.append("# UI RULE 1 -- a signature change is NOT a downgrade")
    out.append("#" * width)
    signature_change = explain_change(
        None, FIXTURE["entity_id"], live_from.as_of_date, "2026-09-21",
        snapshot_from=live_from, snapshot_to=gpd)
    out.append(render_change(signature_change, width=width))
    out.append("")
    out.append(f"  headline_delta -> {signature_change.headline_delta!r}   "
               f"(None: there is no honest point move to render)")

    out.append("")
    out.append("#" * width)
    out.append("# UI RULE 2 -- an unsupported horizon shows no number")
    out.append("#" * width)
    support = supported_horizons()
    for horizon in ("1M", "3M", "12M"):
        row = support["detail"][horizon]
        out.append(f"  {horizon:>4}: {row['display']:<22} "
                   f"n_eff {row['n_effective']:>8.1f} ({row['basis']})")
        out.append(f"        {row['reason']}")
    panel = support["monthly_panel"]
    out.append(f"  monthly panel: {panel['status']}  n_eff "
               f"{panel['n_effective']:.1f}, detects rho "
               f"{panel['detectable_rho']:.3f}")

    out.append("")
    out.append("#" * width)
    out.append("# UI RULE 3 -- the hedge carries its research status")
    out.append("#" * width)
    for day in ("2019-06-28", "2026-09-21"):
        status = hedge_research_status(day)
        out.append(f"  as_of {day}: {status['research_status']}")
        for line in status["display"].splitlines():
            out.append(f"      {line}")

    out.append("")
    out.append("#" * width)
    out.append("# GRAIN -- what is stored where, and what was thrown away")
    out.append("#" * width)
    arithmetic = grain_arithmetic()
    for table, info in arithmetic["grains"].items():
        rows = info["rows_per_run"]
        shown = f"{rows:,}" if isinstance(rows, int) else rows
        out.append(f"  {table:<26} {shown:>12}  {info['grain']}")
    out.append("")
    out.append("  NOT stored:")
    for item in arithmetic["not_stored"]:
        out.append(f"    - {item['thing']}")
        out.append(f"        cost avoided: {item['cost']}")
    return "\n".join(out)


def _demo() -> None:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(worked_example())


if __name__ == "__main__":
    _demo()
