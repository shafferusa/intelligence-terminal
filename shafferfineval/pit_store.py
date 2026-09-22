"""Point-in-time historical store for ShafferFinEval research.

This module is the RESEARCH half of the production/research split. It never
writes to the live tables in `storage.py` and the live app never writes here.

    storage.py            -> live production Shaffer Score (score_history)
    pit_store.py (here)   -> historical point-in-time replay (pit_score)

The two are different model versions computed from different sources and are
never pooled. `equity_shaffer_v1` is the promoted human production model;
`equity_shaffer_v1_pit` is its historical research twin. They share the same
human-authored financial equation and will not produce identical numbers,
because the twin uses XBRL-assembled EBITDA, point-in-time facts, point-in-time
peers and historical identity. That gap is research evidence, not a defect.

Three invariants this module exists to enforce:

1. ENTITY IDENTITY IS PRIMARY. Nothing here is keyed on a ticker. An issuer is
   an `entity_id` (CIK-backed); a tradeable line is a `listing_id` disambiguated
   by its first trade date. Tickers are reused, reassigned and renamed; the live
   `assets` table is keyed UNIQUE(symbol, asset_class) and holds only today's
   universe, so it can never decide whether a historical entity may exist.

2. RAW OBSERVATIONS ARE APPEND-ONLY. A restatement is a NEW observation with its
   own accession and availability date, never an update of the original. This is
   enforced by SQLite triggers, not by convention -- a trigger survives a
   contributor reaching for INSERT OR REPLACE.

3. NOTHING IS KNOWABLE BEFORE IT WAS AVAILABLE. Every feature row carries
   `feature_available_date` and a CHECK constraint refuses a row where that
   post-dates the as-of date. The constraint guards the column; the future-data
   canary in test_pit_store.py guards the selector, which is where leaks live.

The store is a SEPARATE SQLite file. The DERA source archive is ~5.6 GB across
70 quarters and the fact table is the largest object in the project by an order
of magnitude; keeping it out of the transactional app database means a research
ingest can never slow or corrupt the terminal.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Optional, Sequence

DEFAULT_PIT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "shafferfineval_pit.db")

#: Where raw option chains land. One gzipped JSONL file per snapshot; SQLite
#: holds only the manifest. Option chains are the one source that grows without
#: bound, and they must stay replayable byte-for-byte.
DEFAULT_ARCHIVE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pit_archive")

# --------------------------------------------------------------------------
# Versioned policies. Every historical row records which ones produced it, so
# a change of policy creates new rows beside the old instead of reinterpreting
# them. None of these may be edited in place -- add v2 and leave v1 alone.
# --------------------------------------------------------------------------

#: The historical research twin of the promoted `equity_model_v1`.
EQUITY_PIT_MODEL_VERSION = "equity_shaffer_v1_pit"
SECTOR_PIT_MODEL_VERSION = "sector_shaffer_v1_pit"

#: When newly filed information becomes usable by the strategy. See pit_policy.
LATENCY_POLICY_VERSION = "information_latency_policy_v1"

#: Which XBRL tag ladder resolved each financial concept. See pit_policy.
LADDER_VERSION = "concept_ladder_v1"

#: The second ladder set. v1 is NOT retired and NOT edited: it stays the
#: DEFAULT, so every row already stamped `concept_ladder_v1` stays reproducible
#: by re-running the same code. v2 exists because v1's `total_debt` traps its
#: best-covered tag inside a three-way sum -- measured 2026-09-21, v1 resolves
#: total_debt for 15.9% / 17.8% / 14.5% of the base universe at 2015-06-30 /
#: 2019-06-28 / 2024-06-28 while the union of the tags that same ladder names
#: covers 42.8% / 46.7% / 42.7%. A feature row records which version resolved
#: it, so the two can be compared rather than conflated. See pit_policy.
LADDER_VERSION_V2 = "concept_ladder_v2"

#: How peer cohorts are built (SIC rung ladder and minimum sizes).
PEER_SET_VERSION = "peer_set_v1_sic"

#: The production return calibration, stored beside the score rather than baked
#: into it, so research can ask whether 0.20 was ever the right slope.
RETURN_CALIBRATION_VERSION = "return_calibration_v1_0.20"

#: Replay cadence. Phase 1 is month-end only: the unit of historical work is a
#: DATE (the whole cross-section must be rebuilt for peer percentiles), and a
#: daily grid multiplies cost by ~21 for labels that are already overlapping.
GRID_MONTH_END = "month_end"
GRID_WEEKLY = "weekly"
GRID_DAILY = "daily"

#: Feature availability states. `tagged_zero` and `company_had_none` are
#: different facts from `never_tagged`: Apple's first long-term-debt observation
#: is 2013-07-24 because Apple genuinely had no debt, not because nobody tagged it.
AVAIL_COMPLETE = "complete"
AVAIL_PARTIAL = "partial"
AVAIL_UNAVAILABLE = "unavailable"

REASON_NEVER_TAGGED = "never_tagged"
REASON_TAGGED_ZERO = "tagged_zero"
REASON_COMPANY_HAD_NONE = "company_had_none"
REASON_NOT_YET_FILED = "not_yet_filed"
REASON_LADDER_EXHAUSTED = "ladder_exhausted"
REASON_STALE = "stale_beyond_max_age"
REASON_NO_PEERS = "insufficient_peers"

#: A VALUATION factor had no ADMISSIBLE peer set. Produced by the two-test gate
#: in `pit_factor_spec` (SUFFICIENT N and ECONOMIC COHERENCE) and consumed by
#: `pit_score_signature` as the reason the valuation pillar is absent, which
#: under the owner's decision 7 makes the row a PARTIAL score that may not be
#: pooled with full ones.
#:
#: Deliberately its OWN outcome and not a second spelling of `insufficient_peers`:
#: the SEC Office rung clears 12 members 94.5% of the time on cohorts of
#: 542-1,310 members, so "enough peers to compute a percentile" and "peers worth
#: comparing a company against" are different questions, and one reason string
#: could not say which of them failed.
REASON_VALUATION_PEER_SET_INSUFFICIENT = "VALUATION_PEER_SET_INSUFFICIENT"

#: How a forward-return label was computed. Stamped on every `pit_label` row,
#: because that table's natural key is (listing_id, as_of_date, horizon) and
#: carried no version at all until 2026-09-21: a second labelling policy would
#: have upserted straight over the first with nothing recording that it had.
#:
#: THE SAMPLE SCOPE IS IN THE NAME ON PURPOSE. `pit_label_set` is where the
#: policy is described, but a consumer who joins `pit_label` and never opens
#: the registry still cannot read a row without reading `survivor_only`. The
#: price sample behind these labels is 2,574 listings every one of which is
#: alive in 2026; a return measured on it answers "among survivors", never
#: "in the market".
LABEL_POLICY_VERSION = "label_v1_adjclose_sessions_survivor_only"

#: Sample-scope vocabulary for a label set. A set marked SURVIVOR_ONLY_DIAGNOSTIC
#: may be used to diagnose and may NOT be used to promote a model.
SAMPLE_SURVIVOR_ONLY = "SURVIVOR_ONLY_DIAGNOSTIC"
SAMPLE_FULL_UNIVERSE = "FULL_UNIVERSE"

#: Benchmark kinds, explicit because "the benchmark" names three different
#: questions and an excess return measured against the wrong one is not a small
#: error -- it is a different result with the same column name.
BENCHMARK_BROAD_EQUITY = "broad_equity_market"
BENCHMARK_SECTOR = "sector"
BENCHMARK_ASSET_CLASS = "asset_class"

#: What a `pit_listing` row IS. Benchmarks share the table with companies
#: because `pit_price_bar.listing_id` is a foreign key into it; they are kept
#: apart by this column rather than by a second bar table with a second set of
#: rules about stale prints, ingest dedup and the disk floor.
INSTRUMENT_COMPANY = "company"
INSTRUMENT_BENCHMARK = "benchmark"

#: Label outcome states. OUTCOME_UNKNOWN is mandatory and is never silently
#: converted to -100%, to zero, or to a carried-forward last price.
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
OUTCOME_BANKRUPTCY = "bankruptcy"
OUTCOME_MERGER_CASH = "merger_cash"
OUTCOME_MERGER_STOCK = "merger_stock"

#: Research lifecycle. Nothing reaches production without an explicit human
#: promotion recorded in pit_promotion.
RESEARCH = "RESEARCH"
CHALLENGER = "CHALLENGER"
VALIDATED_CHALLENGER = "VALIDATED_CHALLENGER"
PRODUCTION = "PRODUCTION"
RETIRED = "RETIRED"


SCHEMA = """
-- =====================================================================
-- IDENTITY. Primary, and never the ticker.
-- =====================================================================

CREATE TABLE IF NOT EXISTS pit_entity (
    entity_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cik                   TEXT NOT NULL UNIQUE,
    first_filing_date     TEXT,
    last_filing_date      TEXT,
    entity_kind           TEXT,          -- operating | trust | shell | fund
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pit_entity_name (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    name                  TEXT NOT NULL,
    valid_from            TEXT,
    valid_to              TEXT,
    source                TEXT NOT NULL,
    UNIQUE (entity_id, name, valid_from)
);

-- SIC as assigned AT FILING TIME, read from the filing's own header. The
-- submissions API's `sic` is a current-only field and is provably wrong for
-- 13.6% of historical rows, so it is never read into anything a replay touches.
CREATE TABLE IF NOT EXISTS pit_entity_sic (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    sic                   TEXT NOT NULL,
    filed                 TEXT NOT NULL,
    source_accession      TEXT NOT NULL,
    source                TEXT NOT NULL,
    UNIQUE (entity_id, source_accession)
);

-- Exit dating takes the EARLIEST DEFENSIBLE of three sources, because each one
-- alone is wrong: Form 25-NSE is filed per security (Berkshire has six and is
-- not delisted) and lags the last trade by months; Form 15 is deregistration,
-- not delisting, and can post years later; and some issuers filed neither.
CREATE TABLE IF NOT EXISTS pit_entity_exit (
    entity_id             INTEGER PRIMARY KEY REFERENCES pit_entity(entity_id),
    exit_date             TEXT,
    exit_type             TEXT,          -- merger | exchange_delisting | issuer_withdrawal
    exit_date_source      TEXT,          -- 8k_item_301 | last_volume_bar | form_25nse
    exit_source_accession  TEXT,
    dereg_date            TEXT,          -- Form 15. NEVER used as exit_date.
    no_exit_record        INTEGER NOT NULL DEFAULT 0,
    notes                 TEXT
);

-- A tradeable line. first_trade_date is the only field that catches ticker
-- reuse: a reused symbol returns HTTP 200 with a matching name and exchange.
CREATE TABLE IF NOT EXISTS pit_listing (
    listing_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER REFERENCES pit_entity(entity_id),
    symbol                TEXT NOT NULL,
    exchange              TEXT,
    instrument_type       TEXT,
    currency              TEXT,
    first_trade_date      TEXT,
    valid_from            TEXT NOT NULL,
    valid_to              TEXT,
    confidence            TEXT NOT NULL,  -- proved | inferred | unverified
    source                TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    -- company | benchmark. A benchmark ETF is a listing with a NULL entity_id;
    -- this is what keeps it out of every query that means "the issuers".
    instrument_kind       TEXT NOT NULL DEFAULT 'company',
    UNIQUE (symbol, first_trade_date)
);

-- Convenience link to the LIVE universe, for the terminal UI only. The replay
-- never reads this table; it exists so a live page can find a PIT history.
CREATE TABLE IF NOT EXISTS pit_entity_asset (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    asset_id              INTEGER NOT NULL,
    valid_from            TEXT,
    valid_to              TEXT,
    confidence            TEXT NOT NULL,
    UNIQUE (entity_id, asset_id, valid_from)
);

-- =====================================================================
-- RAW POINT-IN-TIME SOURCES. Append-only, trigger-enforced.
-- =====================================================================

-- One immutable row per observation AS PUBLISHED. A restatement is a new row.
-- `frame` and `prevrpt` are deliberately absent: frame always carries the
-- LATEST restated value and has no filed date, and prevrpt is computed from the
-- future ("was later amended"). Neither may ever be a PIT selector.
CREATE TABLE IF NOT EXISTS pit_fact (
    fact_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    taxonomy              TEXT NOT NULL,   -- us-gaap | dei | ifrs-full
    tag                   TEXT NOT NULL,
    unit                  TEXT NOT NULL,
    period_start          TEXT,            -- NULL for instantaneous facts
    period_end            TEXT NOT NULL,
    qtrs                  INTEGER NOT NULL,
    segments              TEXT NOT NULL DEFAULT '',
    coreg                 TEXT NOT NULL DEFAULT '',
    val                   REAL NOT NULL,
    accn                  TEXT NOT NULL,
    form                  TEXT NOT NULL,
    filed                 TEXT NOT NULL,
    -- The acceptance timestamp is kept in THREE forms so a reader can always
    -- reconstruct why a filing became available on a given session:
    --   accepted_raw      exactly as the source published it, never parsed
    --   accepted_eastern  the derived Eastern wall clock (naive)
    --   available_date    the session the latency policy assigned
    -- SEC timestamp semantics differ across sources; keeping the raw string
    -- means a later correction can be re-derived without a re-ingest.
    accepted_raw          TEXT,
    accepted_eastern      TEXT,
    available_date        TEXT NOT NULL,
    latency_policy_version TEXT NOT NULL,
    source                TEXT NOT NULL,   -- dera:2021q2 | companyfacts
    UNIQUE (entity_id, taxonomy, tag, unit, period_end, qtrs, segments, coreg, accn),
    CHECK (qtrs IN (0, 1, 2, 3, 4)),
    CHECK (available_date >= filed)
);

CREATE INDEX IF NOT EXISTS idx_pit_fact_select
    ON pit_fact (entity_id, tag, period_end, available_date);
CREATE INDEX IF NOT EXISTS idx_pit_fact_period
    ON pit_fact (entity_id, qtrs, period_end);

CREATE TRIGGER IF NOT EXISTS trg_pit_fact_no_update
BEFORE UPDATE ON pit_fact
BEGIN
    SELECT RAISE(ABORT, 'pit_fact is append-only: a restatement is a new row');
END;

CREATE TRIGGER IF NOT EXISTS trg_pit_fact_no_delete
BEFORE DELETE ON pit_fact
BEGIN
    SELECT RAISE(ABORT, 'pit_fact is append-only: published facts are never deleted');
END;

-- adjclose is retroactively restated by every later split and dividend, so the
-- pull it came from is part of its identity. close is split-adjusted only and
-- must have future splits un-applied before it is used as a price level.
CREATE TABLE IF NOT EXISTS pit_price_bar (
    listing_id            INTEGER NOT NULL REFERENCES pit_listing(listing_id),
    bar_date              TEXT NOT NULL,
    open                  REAL,
    high                  REAL,
    low                   REAL,
    close                 REAL,
    adjclose              REAL,
    volume                REAL,
    source_symbol         TEXT NOT NULL,
    ingest_id             INTEGER NOT NULL,
    UNIQUE (listing_id, bar_date, ingest_id)
);

CREATE INDEX IF NOT EXISTS idx_pit_bar_lookup ON pit_price_bar (listing_id, bar_date);

-- applies_to_price / applies_to_shares separate the two meanings a Yahoo split
-- event can carry: AT&T's "1324:1000" on 2022-04-11 is a spin-off, valid as a
-- price adjustment and wrong by 32.4% as a share-count divisor.
CREATE TABLE IF NOT EXISTS pit_corporate_action (
    listing_id            INTEGER NOT NULL REFERENCES pit_listing(listing_id),
    event_date            TEXT NOT NULL,
    event_type            TEXT NOT NULL,
    ratio_num             REAL,
    ratio_den             REAL,
    cash_amount           REAL,
    applies_to_price      INTEGER NOT NULL DEFAULT 1,
    applies_to_shares     INTEGER NOT NULL DEFAULT 1,
    acquirer_entity_id    INTEGER REFERENCES pit_entity(entity_id),
    exchange_ratio        REAL,
    terminal_value_per_share REAL,
    confidence            TEXT NOT NULL DEFAULT 'inferred',
    source                TEXT NOT NULL,
    source_accession      TEXT,
    UNIQUE (listing_id, event_date, event_type)
);

-- Macro is stored per VINTAGE. vintage_date is what the provider published on
-- that date; available_date is when the observation could first be read.
-- Selection needs BOTH (see macro_value_as_of): a row whose available_date is
-- in the past can still carry a vintage from the future.
CREATE TABLE IF NOT EXISTS pit_macro_obs (
    series_id             TEXT NOT NULL,
    obs_date              TEXT NOT NULL,
    vintage_date          TEXT NOT NULL,
    value                 REAL,
    available_date        TEXT NOT NULL,
    revision_class        TEXT NOT NULL,   -- NOT_REVISED | REVISED
    base_tag              TEXT,            -- index base, e.g. '2017=100'
    source                TEXT NOT NULL,
    UNIQUE (series_id, obs_date, vintage_date),
    CHECK (vintage_date >= obs_date)
);

CREATE INDEX IF NOT EXISTS idx_pit_macro_select
    ON pit_macro_obs (series_id, obs_date, vintage_date);

CREATE TABLE IF NOT EXISTS pit_macro_manifest (
    series_id             TEXT PRIMARY KEY,
    label                 TEXT,
    frequency             TEXT,
    revision_class        TEXT NOT NULL,
    vintage_coverage_start TEXT,
    series_first_obs      TEXT,
    publication_lag_days  INTEGER,
    feature_role          TEXT NOT NULL,   -- production | research_candidate
    units_note            TEXT,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pit_curve_obs (
    curve_id              TEXT NOT NULL,   -- ust_par | ust_real | ust_bill
    obs_date              TEXT NOT NULL,
    tenor                 TEXT NOT NULL,
    value                 REAL,
    available_date        TEXT NOT NULL,
    source                TEXT NOT NULL,
    UNIQUE (curve_id, obs_date, tenor)
);

-- One canonical session calendar. Horizons are trading days, never calendar
-- offsets: unscheduled closures are real and session counts run 250-253.
CREATE TABLE IF NOT EXISTS pit_calendar (
    market                TEXT NOT NULL,
    session_date          TEXT NOT NULL,
    session_index         INTEGER NOT NULL,
    is_month_end          INTEGER NOT NULL DEFAULT 0,
    source                TEXT NOT NULL,
    UNIQUE (market, session_date)
);

CREATE INDEX IF NOT EXISTS idx_pit_calendar_index ON pit_calendar (market, session_index);

-- defined_on_date is what stops a hindsight era label becoming a feature: a
-- crash window drawn in 2026 cannot carry an availability date in 2008.
CREATE TABLE IF NOT EXISTS pit_regime (
    regime_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    label                 TEXT NOT NULL,
    start_date            TEXT,
    end_date              TEXT,
    method                TEXT NOT NULL,
    defined_on_date       TEXT NOT NULL,
    usage                 TEXT NOT NULL,   -- evaluation_only | feature
    fold_set_id           TEXT,
    fold_index            INTEGER,
    source                TEXT NOT NULL
);

-- =====================================================================
-- FEATURE STORE
-- =====================================================================

CREATE TABLE IF NOT EXISTS pit_feature_definition (
    feature_key           TEXT NOT NULL,
    model_version         TEXT NOT NULL,
    description           TEXT NOT NULL,
    feature_role          TEXT NOT NULL,   -- production | research_candidate
    tag_ladder_json       TEXT,
    ladder_version        TEXT,
    transform             TEXT,
    winsorisation         TEXT,
    norm_method           TEXT,
    max_age_days          INTEGER,
    defined_on            TEXT NOT NULL,
    UNIQUE (feature_key, model_version)
);

CREATE TABLE IF NOT EXISTS pit_peer_set (
    peer_set_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_date            TEXT NOT NULL,
    rung                  TEXT NOT NULL,   -- sic4 | sic3 | sic2 | office
    key                   TEXT NOT NULL,
    n_members             INTEGER NOT NULL,
    n_price_resolvable    INTEGER NOT NULL,
    peer_set_version      TEXT NOT NULL,
    crosswalk_hash        TEXT NOT NULL,
    model_version         TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (as_of_date, rung, key, model_version, peer_set_version)
);

CREATE TABLE IF NOT EXISTS pit_peer_member (
    peer_set_id           INTEGER NOT NULL REFERENCES pit_peer_set(peer_set_id),
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    UNIQUE (peer_set_id, entity_id)
);

-- sources_json is an ARRAY, not a string: EBITDA is assembled from operating
-- income plus a D&A tag, frequently filed on different dates, so one accession
-- could never answer "what exactly did the system know".
CREATE TABLE IF NOT EXISTS pit_feature (
    feature_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    listing_id            INTEGER REFERENCES pit_listing(listing_id),
    as_of_date            TEXT NOT NULL,
    feature_key           TEXT NOT NULL,
    feature_role          TEXT NOT NULL DEFAULT 'production',
    sources_json          TEXT NOT NULL,
    source_tag            TEXT,
    ladder_version        TEXT,
    source_tag_changed    INTEGER NOT NULL DEFAULT 0,
    fact_max_age_days     INTEGER,
    feature_available_date TEXT NOT NULL,
    latency_policy_version TEXT NOT NULL,
    raw_value             REAL,
    normalized_value      REAL,
    norm_method           TEXT,
    peer_set_id           INTEGER REFERENCES pit_peer_set(peer_set_id),
    availability          TEXT NOT NULL,
    available_flag        INTEGER NOT NULL DEFAULT 1,
    unavailable_reason    TEXT,
    model_version         TEXT NOT NULL,
    replay_run_id         INTEGER NOT NULL REFERENCES pit_replay_run(run_id),
    created_at            TEXT NOT NULL,
    UNIQUE (entity_id, as_of_date, feature_key, model_version, replay_run_id),
    CHECK (feature_available_date <= as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_pit_feature_asof
    ON pit_feature (as_of_date, model_version, replay_run_id);
CREATE INDEX IF NOT EXISTS idx_pit_feature_entity
    ON pit_feature (entity_id, feature_key, as_of_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_feature_no_update
BEFORE UPDATE ON pit_feature
BEGIN
    SELECT RAISE(ABORT, 'pit_feature is immutable: re-run the replay instead');
END;

-- =====================================================================
-- REPLAY OUTPUT
-- =====================================================================

CREATE TABLE IF NOT EXISTS pit_replay_run (
    run_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    as_of_from            TEXT,
    as_of_to              TEXT,
    as_of_grid            TEXT NOT NULL,
    model_version         TEXT NOT NULL,
    latency_policy_version TEXT NOT NULL,
    ladder_version        TEXT NOT NULL,
    peer_set_version      TEXT NOT NULL,
    universe_def_json     TEXT,
    source_versions_json  TEXT,
    crosswalk_hash        TEXT,
    code_revision         TEXT,
    seed                  INTEGER,
    status                TEXT NOT NULL DEFAULT 'running',
    superseded_by         INTEGER,
    invalidated_reason    TEXT,
    n_entities            INTEGER,
    n_dates               INTEGER,
    summary_json          TEXT
);

-- effective_weights_json is mandatory, not decorative. A score computed with
-- valuation unavailable is a DIFFERENT MODEL STATE (0.4167/0.3333/0.25 over
-- 0.60), and the ML lab must be able to tell the two apart.
CREATE TABLE IF NOT EXISTS pit_score (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    listing_id            INTEGER REFERENCES pit_listing(listing_id),
    as_of_date            TEXT NOT NULL,
    model_version         TEXT NOT NULL,
    replay_run_id         INTEGER NOT NULL REFERENCES pit_replay_run(run_id),
    latency_policy_version TEXT NOT NULL,
    peer_set_version      TEXT NOT NULL,
    ladder_version        TEXT NOT NULL,
    company_score         REAL,
    sector_overlay        REAL,
    political_overlay     REAL,
    final_score           REAL,
    classification        TEXT,
    benchmark_level       TEXT,
    valuation_benchmark_level TEXT,
    n_industry_peers      INTEGER,
    n_valid_peers         INTEGER,
    n_ebitda_cohort       INTEGER,
    effective_weights_json TEXT NOT NULL,
    factor_scores_json    TEXT,
    raw_inputs_json       TEXT,
    missing_factors_json  TEXT,
    notes_json            TEXT,
    provenance_json       TEXT,
    coverage              REAL,
    confidence            TEXT,
    created_at            TEXT NOT NULL,
    UNIQUE (entity_id, as_of_date, model_version, replay_run_id)
);

CREATE INDEX IF NOT EXISTS idx_pit_score_asof
    ON pit_score (as_of_date, model_version, replay_run_id);

CREATE TRIGGER IF NOT EXISTS trg_pit_score_no_update
BEFORE UPDATE ON pit_score
BEGIN
    SELECT RAISE(ABORT, 'pit_score is immutable: re-run the replay instead');
END;

-- The production calibration is stored SEPARATELY from the score, carrying its
-- own version, so research can ask whether 0.20 was ever the right slope
-- without the answer being baked into the historical record.
CREATE TABLE IF NOT EXISTS pit_score_prediction (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    as_of_date            TEXT NOT NULL,
    model_version         TEXT NOT NULL,
    replay_run_id         INTEGER NOT NULL,
    calibration_version   TEXT NOT NULL,
    predicted_return_pct  REAL,
    horizon               TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (entity_id, as_of_date, model_version, replay_run_id, calibration_version, horizon)
);

CREATE TABLE IF NOT EXISTS pit_sector_score (
    as_of_date            TEXT NOT NULL,
    rung                  TEXT NOT NULL,
    key                   TEXT NOT NULL,
    raw_score             REAL,
    overlay               REAL,
    growth_score          REAL,
    roe_score             REAL,
    roa_score             REAL,
    debt_score            REAL,
    effective_weights_json TEXT,
    n_eligible            INTEGER,
    confidence            TEXT,
    crosswalk_hash        TEXT,
    model_version         TEXT NOT NULL,
    replay_run_id         INTEGER NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (as_of_date, rung, key, model_version, replay_run_id)
);

-- Labels are refinable as more price history arrives, so they are upsertable
-- rather than trigger-frozen. terminal_reason OUTCOME_UNKNOWN is a first-class
-- result: an unresolved exit is never silently converted to -100% or to zero.
CREATE TABLE IF NOT EXISTS pit_label (
    listing_id            INTEGER NOT NULL REFERENCES pit_listing(listing_id),
    entity_id             INTEGER REFERENCES pit_entity(entity_id),
    as_of_date            TEXT NOT NULL,
    horizon               TEXT NOT NULL,
    anchor_date           TEXT,
    anchor_price          REAL,
    anchor_roll_days      INTEGER NOT NULL DEFAULT 0,
    end_date              TEXT,
    end_price             REAL,
    terminal_roll_days    INTEGER NOT NULL DEFAULT 0,
    forward_return        REAL,
    benchmark_forward_return REAL,
    excess_return         REAL,
    net_forward_return    REAL,
    cost_assumption_set_id INTEGER,
    censored              INTEGER NOT NULL DEFAULT 0,
    terminal_reason       TEXT,
    price_source          TEXT NOT NULL,
    price_ingest_id       INTEGER,
    computed_at           TEXT NOT NULL,
    -- Declared here for a new store and listed in MIGRATIONS for an existing
    -- one, in the SAME position, so a fixture database and the 2.3M-row store
    -- have identical column order. See MIGRATIONS for why there is no DEFAULT.
    label_policy_version  TEXT,
    label_run_id          INTEGER,
    benchmark_listing_id  INTEGER,
    UNIQUE (listing_id, as_of_date, horizon)
);

CREATE INDEX IF NOT EXISTS idx_pit_label_lookup ON pit_label (as_of_date, horizon);

-- A label row must say which policy computed it, and no upsert may change that
-- answer. THE VERSION IS NOT IN THE UNIQUE KEY and could not be put there: the
-- key is an inline UNIQUE, SQLite cannot drop the implicit index behind one,
-- and the table rebuild that would replace it cannot be afforded on this
-- volume (`pit_labelset.label_key_rebuild_cost` measures it: a second copy of
-- the table and its three indexes held at once, and no VACUUM afterwards,
-- because VACUUM needs a second copy of the whole 8.1 GiB file against 10.7
-- GiB free). So the version is enforced by these two triggers instead, and the
-- consequence is stated plainly rather than hidden: two label policies STILL
-- cannot coexist in this table -- but the second one is now REFUSED instead of
-- silently overwriting the first, which is the failure versioning is for.
CREATE TRIGGER IF NOT EXISTS trg_pit_label_policy_required
BEFORE INSERT ON pit_label
WHEN NEW.label_policy_version IS NULL OR NEW.label_policy_version = ''
BEGIN
    SELECT RAISE(ABORT, 'pit_label: label_policy_version is required -- an unversioned label cannot be told apart from another policy''s');
END;

CREATE TRIGGER IF NOT EXISTS trg_pit_label_policy_immutable
BEFORE UPDATE OF label_policy_version ON pit_label
WHEN OLD.label_policy_version IS NOT NULL
 AND NEW.label_policy_version IS NOT NULL
 AND NEW.label_policy_version <> OLD.label_policy_version
BEGIN
    SELECT RAISE(ABORT, 'pit_label: this row belongs to a different label policy; a second policy needs its own table, not this row');
END;

-- The versioned, IMMUTABLE description of one labelling policy. `pit_label`
-- carries only the version string; everything that string means lives here, so
-- the per-row cost of full provenance is 41 bytes rather than 300.
--
-- Immutable is enforced, not requested: the triggers below refuse every UPDATE
-- and every DELETE. A policy that changes is a NEW row with a new version and
-- `supersedes` pointing back at this one -- never an edit, because an edited
-- policy row silently re-describes every label already stamped with it.
CREATE TABLE IF NOT EXISTS pit_label_set (
    label_policy_version  TEXT PRIMARY KEY,
    label_set_name        TEXT NOT NULL,
    price_source          TEXT NOT NULL,   -- e.g. yahoo:chart:adjclose
    price_source_version  TEXT NOT NULL,   -- the gate/ingest version behind it
    price_basis           TEXT NOT NULL,   -- adjclose | close | raw
    corporate_action_policy TEXT NOT NULL,
    anchor_rule           TEXT NOT NULL,
    terminal_rule         TEXT NOT NULL,
    censoring_rule        TEXT NOT NULL,
    outcome_unknown_rule  TEXT NOT NULL,
    horizon_unit          TEXT NOT NULL,   -- trading_sessions | calendar_days
    horizons_json         TEXT NOT NULL,
    calendar_market       TEXT NOT NULL,
    sample_scope          TEXT NOT NULL,   -- SURVIVOR_ONLY_DIAGNOSTIC | FULL_UNIVERSE
    sample_scope_note     TEXT NOT NULL,
    may_promote_a_model   INTEGER NOT NULL DEFAULT 0,
    code_module           TEXT NOT NULL,
    params_json           TEXT,
    supersedes            TEXT,
    created_at            TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_pit_label_set_no_update
BEFORE UPDATE ON pit_label_set
BEGIN
    SELECT RAISE(ABORT, 'pit_label_set is immutable: register a new label_policy_version instead of editing one');
END;

CREATE TRIGGER IF NOT EXISTS trg_pit_label_set_no_delete
BEFORE DELETE ON pit_label_set
BEGIN
    SELECT RAISE(ABORT, 'pit_label_set is immutable: a policy must outlive the labels stamped with it');
END;

-- One row per BUILD of a label set. `pit_replay_run` is the score replay's
-- ledger and is deliberately not reused: a label build is not a replay, and
-- pointing labels at a replay run would make the two indistinguishable in
-- exactly the table where they must not be.
CREATE TABLE IF NOT EXISTS pit_label_run (
    label_run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label_policy_version  TEXT NOT NULL REFERENCES pit_label_set(label_policy_version),
    run_kind              TEXT NOT NULL,   -- build | backfill_policy | backfill_benchmark | reconstructed
    started_at            TEXT,
    finished_at           TEXT,
    code_module           TEXT,
    code_revision         TEXT,
    n_listings            INTEGER,
    n_dates               INTEGER,
    n_rows_written        INTEGER,
    benchmark_listing_id  INTEGER REFERENCES pit_listing(listing_id),
    cost_assumption_set_id INTEGER REFERENCES pit_cost_assumption(assumption_set_id),
    status                TEXT NOT NULL DEFAULT 'running',
    evidence_json         TEXT,
    note                  TEXT
);

-- Benchmark instruments, seeded EXPLICITLY and never through company identity.
-- A CIK-driven listing gate cannot produce one: the gate is fed by issuers
-- filing `dei:TradingSymbol`, and an index ETF trust does not file that way --
-- which is exactly why `benchmark_forward_return` was NULL on every label row.
--
-- The instrument itself lives in `pit_listing` with a NULL entity_id and
-- `instrument_kind = 'benchmark'`, because `pit_price_bar.listing_id` is a
-- foreign key into `pit_listing` and a second bar table would mean a second
-- copy of the dedup-by-ingest rule, the stale-print rule and the disk floor.
-- This table is the registry that says WHICH benchmark a listing is and what
-- kind of question it answers.
CREATE TABLE IF NOT EXISTS pit_benchmark (
    benchmark_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_key         TEXT NOT NULL UNIQUE,
    benchmark_kind        TEXT NOT NULL,   -- broad_equity_market | sector | asset_class
    symbol                TEXT NOT NULL,
    listing_id            INTEGER REFERENCES pit_listing(listing_id),
    instrument_name       TEXT,
    tracks                TEXT,            -- the index, in words
    asset_class           TEXT,
    sector_key            TEXT,            -- NULL unless benchmark_kind = 'sector'
    currency              TEXT,
    inception_date        TEXT,
    return_basis          TEXT NOT NULL,   -- adjclose = total return, dividends in
    is_default_for_kind   INTEGER NOT NULL DEFAULT 0,
    price_source          TEXT NOT NULL,
    survivorship_note     TEXT NOT NULL,
    selection_note        TEXT NOT NULL,   -- WHY this instrument and not another
    created_at            TEXT NOT NULL,
    UNIQUE (benchmark_kind, symbol)
);

CREATE TABLE IF NOT EXISTS pit_cost_assumption (
    assumption_set_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    label                 TEXT NOT NULL,
    effective_from        TEXT,
    asset_class           TEXT,
    commission_bps        REAL,
    spread_bps            REAL,
    borrow_bps            REAL,
    financing_spread_bps  REAL,
    adv_participation_json TEXT,
    roll_cost_json        TEXT,
    option_premium_source TEXT,
    is_assumption         INTEGER NOT NULL DEFAULT 1,
    source                TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    -- The bps columns above carry no per-side/round-trip convention, and a
    -- cost number whose convention is unstated is off by a factor of two half
    -- the time. This says which, what is included, and what is NOT priced.
    cost_convention_json  TEXT,
    note                  TEXT
);

-- Idempotency for the seeder, as an INDEX rather than an inline UNIQUE: this
-- table already exists in the 8.1 GiB store without one, and an index can be
-- added to an existing table where a constraint cannot.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pit_cost_assumption_label
    ON pit_cost_assumption (label);

-- =====================================================================
-- OPTION CHAIN ARCHIVE. Manifest only; raw chains are gzipped files.
-- =====================================================================

CREATE TABLE IF NOT EXISTS pit_option_snapshot (
    snapshot_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying_symbol     TEXT NOT NULL,
    entity_id             INTEGER REFERENCES pit_entity(entity_id),
    listing_id            INTEGER REFERENCES pit_listing(listing_id),
    captured_at           TEXT NOT NULL,
    session_date          TEXT NOT NULL,
    underlying_spot       REAL,
    n_contracts           INTEGER,
    n_expiries            INTEGER,
    greeks_source         TEXT,            -- source | none
    file_path             TEXT,
    file_bytes            INTEGER,
    sha256                TEXT,
    source                TEXT NOT NULL,
    status                TEXT NOT NULL,   -- ok | empty | error
    error                 TEXT,
    UNIQUE (underlying_symbol, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_pit_option_session
    ON pit_option_snapshot (session_date, underlying_symbol);

-- =====================================================================
-- RESEARCH: folds, predictions, proposals, lineage
-- =====================================================================

CREATE TABLE IF NOT EXISTS ml_fold (
    fold_set_id           TEXT NOT NULL,
    fold_index            INTEGER NOT NULL,
    horizon               TEXT NOT NULL,
    mode                  TEXT NOT NULL,   -- walk_forward | purged_kfold
    train_start           TEXT,
    train_end             TEXT,
    validate_start        TEXT,
    validate_end          TEXT,
    test_start            TEXT,
    test_end              TEXT,
    purge_days            INTEGER NOT NULL,
    embargo_days          INTEGER NOT NULL,
    embargo_rule          TEXT NOT NULL,
    n_train_raw           INTEGER,
    n_train               INTEGER,
    n_test                INTEGER,
    n_purged              INTEGER,
    n_embargoed           INTEGER,
    n_effective           INTEGER,
    created_at            TEXT NOT NULL,
    UNIQUE (fold_set_id, fold_index)
);

CREATE TABLE IF NOT EXISTS ml_fold_metric (
    fold_set_id           TEXT NOT NULL,
    fold_index            INTEGER NOT NULL,
    model_ref             TEXT NOT NULL,
    metric                TEXT NOT NULL,
    value                 REAL,
    n_effective           INTEGER,
    UNIQUE (fold_set_id, fold_index, model_ref, metric)
);

CREATE TABLE IF NOT EXISTS pit_prediction (
    model_ref             TEXT NOT NULL,
    fold_set_id           TEXT,
    fold_index            INTEGER,
    entity_id             INTEGER NOT NULL,
    as_of_date            TEXT NOT NULL,
    horizon               TEXT NOT NULL,
    point_estimate        REAL,
    interval_lo           REAL,
    interval_hi           REAL,
    interval_method       TEXT,
    interval_nominal_coverage REAL,
    created_at            TEXT NOT NULL,
    UNIQUE (model_ref, entity_id, as_of_date, horizon)
);

CREATE TABLE IF NOT EXISTS pit_proposal (
    proposal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at            TEXT NOT NULL,
    title                 TEXT NOT NULL,
    target_model          TEXT NOT NULL,
    claim                 TEXT NOT NULL,
    rationale             TEXT NOT NULL,
    current_spec_json     TEXT,
    proposed_spec_json    TEXT,
    replay_run_id         INTEGER,
    fold_set_id           TEXT,
    model_ref             TEXT,
    dataset_spec_json     TEXT,
    metrics_json          TEXT,
    baseline_metrics_json TEXT,
    effect_size           REAL,
    n_effective           INTEGER,
    folds_improved        INTEGER,
    folds_total           INTEGER,
    hypotheses_tested     INTEGER,
    known_weaknesses      TEXT,
    status                TEXT NOT NULL DEFAULT 'OPEN',
    decided_by            TEXT,
    decided_at            TEXT,
    decision_note         TEXT
);

-- Permanent lineage of the human-designed Shaffer framework. Every production
-- version is recorded here with its authored specification; a successor never
-- rewrites its predecessor's row.
CREATE TABLE IF NOT EXISTS pit_model_lineage (
    model_version         TEXT PRIMARY KEY,
    model_name            TEXT NOT NULL,
    asset_class           TEXT NOT NULL,
    authorship            TEXT NOT NULL,   -- human | ml_challenger | hybrid
    created_date          TEXT NOT NULL,
    predecessor_version   TEXT,
    factor_definitions_json TEXT,
    weights_json          TEXT,
    transformations_json  TEXT,
    missing_data_rule     TEXT,
    normalization         TEXT,
    overlays_json         TEXT,
    calibration_version   TEXT,
    hedge_rules_json      TEXT,
    status                TEXT NOT NULL,
    notes                 TEXT
);

CREATE TABLE IF NOT EXISTS pit_promotion (
    promotion_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version         TEXT NOT NULL,
    from_status           TEXT NOT NULL,
    to_status             TEXT NOT NULL,
    proposal_id           INTEGER,
    evidence_json         TEXT,
    promoted_by           TEXT NOT NULL,
    promoted_at           TEXT NOT NULL,
    note                  TEXT
);

-- =====================================================================
-- INGEST PROVENANCE
-- =====================================================================

CREATE TABLE IF NOT EXISTS pit_ingest_run (
    ingest_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source                TEXT NOT NULL,
    scope                 TEXT,
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    rows_read             INTEGER DEFAULT 0,
    rows_kept             INTEGER DEFAULT 0,
    rows_rejected         INTEGER DEFAULT 0,
    bytes_downloaded      INTEGER DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'running',
    summary_json          TEXT
);
"""

#: ADD-COLUMN-only upgrades, matching storage.py's convention. A column is
#: added here and NEVER by recreating a table: `pit_label` holds 2.3 million
#: rows and `pit_fact` fourteen million, and on this volume a table rebuild
#: cannot be followed by a VACUUM, so its old pages would be leaked for good.
#:
#: `pit_label.label_policy_version` is deliberately added WITHOUT a DEFAULT.
#: A constant default would have backfilled 2.3M rows for free -- and would
#: also have stamped v1 on a row written by a future policy whose author
#: forgot the column, which is the exact silent mislabelling this work exists
#: to stop. NULL means "written before the column existed", the backfill in
#: `pit_labelset` turns it into a positive statement, and the INSERT trigger
#: refuses NULL from then on.
MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "pit_listing": [
        # Every row that predates this column is an issuer-gated company
        # listing, so the constant default is a true statement about all of
        # them and costs no rewrite. Benchmarks are written with it explicit.
        ("instrument_kind", "TEXT NOT NULL DEFAULT 'company'"),
    ],
    "pit_cost_assumption": [
        ("cost_convention_json", "TEXT"),
        ("note", "TEXT"),
    ],
    "pit_label": [
        ("label_policy_version", "TEXT"),
        ("label_run_id", "INTEGER"),
        # WHICH benchmark produced benchmark_forward_return. A row whose
        # benchmark is unknown is worse than one with a NULL return: the NULL
        # says "not measured", the unknown benchmark says nothing at all.
        ("benchmark_listing_id", "INTEGER"),
    ],
}


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Add every missing MIGRATIONS column. Idempotent. Returns what it added.

    Split out of `init_db` so a module that connects through `pit_prices`
    (which tunes the connection for a shared file and does not run the schema)
    can still bring an existing store up to date without a second, drifting
    copy of the ALTER loop.
    """
    added: list[str] = []
    for table, columns in MIGRATIONS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            continue
        existing = {r["name"] if isinstance(r, sqlite3.Row) else r[1] for r in rows}
        for name, sql_type in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                added.append(f"{table}.{name}")
    if added:
        conn.commit()
    return added


# --------------------------------------------------------------------------
# Connection and transactions
# --------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _dumps(payload: Any) -> Optional[str]:
    if payload is None:
        return None
    return json.dumps(payload, default=str, sort_keys=True)


def _loads(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


dumps = _dumps
loads = _loads


def connect(db_path: str = DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """A connection with row access by name, foreign keys and WAL on."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str = DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """Create the PIT schema if absent and return a connection. Idempotent."""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    apply_migrations(conn)
    conn.commit()
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


@contextmanager
def bulk_load(conn: sqlite3.Connection):
    """Fast path for a multi-million-row ingest.

    Ordinary per-row helpers cannot move a DERA quarter in reasonable time.
    Inside this context, synchronous writes are relaxed and foreign keys are
    deferred; the caller is expected to use executemany and commit per batch.
    Restores the connection's normal settings on the way out, including after
    an exception, so a failed ingest cannot leave the store in a loose mode.
    """
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA cache_size = -64000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def upsert_entity(conn: sqlite3.Connection, cik: str, **fields) -> int:
    """Register an issuer by CIK and return its entity_id. Idempotent."""
    cik = str(cik).lstrip("0").zfill(10)
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO pit_entity (cik, first_filing_date, last_filing_date,
                                    entity_kind, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cik) DO UPDATE SET
                first_filing_date = COALESCE(
                    MIN(excluded.first_filing_date, pit_entity.first_filing_date),
                    excluded.first_filing_date, pit_entity.first_filing_date),
                last_filing_date = COALESCE(
                    MAX(excluded.last_filing_date, pit_entity.last_filing_date),
                    excluded.last_filing_date, pit_entity.last_filing_date),
                entity_kind = COALESCE(excluded.entity_kind, pit_entity.entity_kind)
            """,
            (cik, fields.get("first_filing_date"), fields.get("last_filing_date"),
             fields.get("entity_kind"), _now()),
        )
    row = conn.execute("SELECT entity_id FROM pit_entity WHERE cik = ?", (cik,)).fetchone()
    return int(row["entity_id"])


def entity_id_for_cik(conn: sqlite3.Connection, cik: str) -> Optional[int]:
    cik = str(cik).lstrip("0").zfill(10)
    row = conn.execute("SELECT entity_id FROM pit_entity WHERE cik = ?", (cik,)).fetchone()
    return int(row["entity_id"]) if row else None


def add_entity_name(conn: sqlite3.Connection, entity_id: int, name: str,
                    valid_from: Optional[str], valid_to: Optional[str],
                    source: str) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_entity_name (entity_id, name, valid_from, valid_to, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (entity_id, name, valid_from) DO NOTHING""",
            (entity_id, name, valid_from, valid_to, source),
        )


def add_entity_sic(conn: sqlite3.Connection, entity_id: int, sic: str, filed: str,
                   source_accession: str, source: str) -> None:
    """Record the SIC as assigned at the time of one specific filing."""
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_entity_sic (entity_id, sic, filed, source_accession, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (entity_id, source_accession) DO NOTHING""",
            (entity_id, str(sic), filed, source_accession, source),
        )


def sic_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str) -> Optional[str]:
    """The industry classification this issuer carried on `as_of`.

    Resolved from the latest filing available by then -- never from the current
    company record, which is a mutable field and is wrong for 13.6% of rows.
    """
    row = conn.execute(
        """SELECT sic FROM pit_entity_sic
            WHERE entity_id = ? AND filed <= ?
            ORDER BY filed DESC, source_accession DESC LIMIT 1""",
        (entity_id, as_of),
    ).fetchone()
    return row["sic"] if row else None


def set_entity_exit(conn: sqlite3.Connection, entity_id: int, **fields) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_entity_exit (entity_id, exit_date, exit_type,
                    exit_date_source, exit_source_accession, dereg_date,
                    no_exit_record, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE SET
                    exit_date = excluded.exit_date,
                    exit_type = excluded.exit_type,
                    exit_date_source = excluded.exit_date_source,
                    exit_source_accession = excluded.exit_source_accession,
                    dereg_date = COALESCE(excluded.dereg_date, pit_entity_exit.dereg_date),
                    no_exit_record = excluded.no_exit_record,
                    notes = excluded.notes""",
            (entity_id, fields.get("exit_date"), fields.get("exit_type"),
             fields.get("exit_date_source"), fields.get("exit_source_accession"),
             fields.get("dereg_date"), int(fields.get("no_exit_record", 0)),
             fields.get("notes")),
        )


def upsert_listing(conn: sqlite3.Connection, symbol: str, first_trade_date: Optional[str],
                   **fields) -> int:
    """Register a tradeable line and return its listing_id.

    (symbol, first_trade_date) is the natural key precisely because a reused
    ticker is a DIFFERENT listing: BBBY today returns a matching name and
    exchange with a first trade date in 2026, and only that date separates it
    from the retailer that went bankrupt in 2023.
    """
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_listing (entity_id, symbol, exchange, instrument_type,
                    currency, first_trade_date, valid_from, valid_to, confidence,
                    source, created_at, instrument_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (symbol, first_trade_date) DO UPDATE SET
                    entity_id = COALESCE(excluded.entity_id, pit_listing.entity_id),
                    exchange = COALESCE(excluded.exchange, pit_listing.exchange),
                    valid_to = COALESCE(excluded.valid_to, pit_listing.valid_to),
                    confidence = excluded.confidence,
                    instrument_kind = excluded.instrument_kind""",
            (fields.get("entity_id"), symbol, fields.get("exchange"),
             fields.get("instrument_type"), fields.get("currency"), first_trade_date,
             fields.get("valid_from") or first_trade_date or "1900-01-01",
             fields.get("valid_to"), fields.get("confidence", "unverified"),
             fields.get("source", "unknown"), _now(),
             fields.get("instrument_kind", INSTRUMENT_COMPANY)),
        )
    row = conn.execute(
        "SELECT listing_id FROM pit_listing WHERE symbol = ? AND first_trade_date IS ?",
        (symbol, first_trade_date),
    ).fetchone()
    return int(row["listing_id"])


# --------------------------------------------------------------------------
# Facts -- the point-in-time selector
# --------------------------------------------------------------------------

FACT_COLUMNS = (
    "entity_id", "taxonomy", "tag", "unit", "period_start", "period_end", "qtrs",
    "segments", "coreg", "val", "accn", "form", "filed", "accepted_raw",
    "accepted_eastern",
    "available_date", "latency_policy_version", "source",
)

_FACT_INSERT = (
    "INSERT OR IGNORE INTO pit_fact (" + ", ".join(FACT_COLUMNS) + ") VALUES ("
    + ", ".join("?" * len(FACT_COLUMNS)) + ")"
)


def insert_facts(conn: sqlite3.Connection, rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert fact tuples in FACT_COLUMNS order. Returns rows inserted.

    INSERT OR IGNORE is safe here and ONLY here: the unique key includes `accn`,
    so a conflict means the identical published observation is already stored.
    It can never overwrite a value -- the immutability trigger forbids UPDATE.
    """
    cursor = conn.executemany(_FACT_INSERT, rows)
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def fact_as_of(conn: sqlite3.Connection, entity_id: int, tag: str, unit: str,
               period_end: str, qtrs: int, as_of: str) -> Optional[sqlite3.Row]:
    """THE point-in-time selector. The only sanctioned way to read a fact.

    Returns the latest vintage of (tag, period) that a reader could have seen on
    `as_of` -- which is the latest restatement KNOWN then, not today's value and
    not the original. Verified against a known restatement: Kraft Heinz FY2016
    net income resolves to the original at 2019-06-06 and to the restated value
    at 2019-06-07.

    Never select by period alone, and never order by `filed` without the accn
    tie-break: Apple filed a 10-K/A and a 10-Q on the same day, 2010-01-25.

    Consolidated facts only. Segment and co-registrant rows are stored but are
    never returned here -- 47% of DERA rows are dimensional, and a segment's
    revenue substituted for the company's corrupts the score with no error.
    """
    return conn.execute(
        """SELECT * FROM pit_fact
            WHERE entity_id = ? AND tag = ? AND unit = ?
              AND period_end = ? AND qtrs = ?
              AND segments = '' AND coreg = ''
              AND available_date <= ?
            ORDER BY available_date DESC, filed DESC, accn DESC
            LIMIT 1""",
        (entity_id, tag, unit, period_end, qtrs, as_of),
    ).fetchone()


def latest_period_as_of(conn: sqlite3.Connection, entity_id: int, tag: str, unit: str,
                        qtrs: int, as_of: str,
                        max_age_days: Optional[int] = None) -> Optional[sqlite3.Row]:
    """The most recent reporting period for `tag` knowable on `as_of`.

    `max_age_days` bounds how stale the period may be. Without it a perfectly
    PIT-selected fact can still be economically dead: SVB's NetIncomeLoss stops
    being filed in 2012 while the company reported through 2023, so an unbounded
    lookup returns the FY2011 value at a 2019 as-of -- a real number, correctly
    selected, eight years stale.
    """
    row = conn.execute(
        """SELECT * FROM pit_fact
            WHERE entity_id = ? AND tag = ? AND unit = ? AND qtrs = ?
              AND segments = '' AND coreg = ''
              AND available_date <= ?
            ORDER BY period_end DESC, available_date DESC, accn DESC
            LIMIT 1""",
        (entity_id, tag, unit, qtrs, as_of),
    ).fetchone()
    if row is None or max_age_days is None:
        return row
    if _days_between(row["period_end"], as_of) > max_age_days:
        return None
    return row


def _days_between(start: str, end: str) -> int:
    try:
        a = _dt.date.fromisoformat(start[:10])
        b = _dt.date.fromisoformat(end[:10])
    except (ValueError, TypeError):
        return 10 ** 6
    return (b - a).days


# --------------------------------------------------------------------------
# Macro -- vintage selection
# --------------------------------------------------------------------------

def insert_macro_obs(conn: sqlite3.Connection, rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert (series_id, obs_date, vintage_date, value, available_date,
    revision_class, base_tag, source)."""
    cursor = conn.executemany(
        """INSERT OR IGNORE INTO pit_macro_obs
               (series_id, obs_date, vintage_date, value, available_date,
                revision_class, base_tag, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def macro_value_as_of(conn: sqlite3.Connection, series_id: str, obs_date: str,
                      as_of: str) -> Optional[sqlite3.Row]:
    """The value of one macro observation AS PUBLISHED on `as_of`.

    Both bounds are required and neither implies the other. `available_date`
    says the observation had been released; `vintage_date` says which release.
    Filtering on availability alone returns today's revised value for a 2013
    observation, which is the exact leak the vintage archive exists to prevent:
    a first-print core PCE of +0.294% reads +0.176% today.
    """
    return conn.execute(
        """SELECT * FROM pit_macro_obs
            WHERE series_id = ? AND obs_date = ?
              AND vintage_date <= ? AND available_date <= ?
            ORDER BY vintage_date DESC
            LIMIT 1""",
        (series_id, obs_date, as_of, as_of),
    ).fetchone()


def macro_series_as_of(conn: sqlite3.Connection, series_id: str, as_of: str,
                       start: Optional[str] = None) -> list[sqlite3.Row]:
    """The whole series as it stood on `as_of`, one row per observation date.

    Every returned row comes from the SAME vintage where one exists, because a
    transform computed across two vintages manufactures a shock that never
    happened -- INDPRO rebased 2012=100 to 2017=100 inside the replay window.
    """
    rows = conn.execute(
        """SELECT obs_date, MAX(vintage_date) AS vintage_date
             FROM pit_macro_obs
            WHERE series_id = ? AND vintage_date <= ? AND available_date <= ?
              AND (? IS NULL OR obs_date >= ?)
            GROUP BY obs_date
            ORDER BY obs_date""",
        (series_id, as_of, as_of, start, start),
    ).fetchall()
    if not rows:
        return []
    newest = max(r["vintage_date"] for r in rows)
    return conn.execute(
        """SELECT * FROM pit_macro_obs
            WHERE series_id = ? AND vintage_date = ?
              AND (? IS NULL OR obs_date >= ?)
            ORDER BY obs_date""",
        (series_id, newest, start, start),
    ).fetchall()


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

def insert_calendar(conn: sqlite3.Connection, market: str,
                    sessions: Sequence[str], source: str) -> int:
    """Store a canonical session calendar, indexed so horizons are session counts."""
    ordered = sorted(set(sessions))
    month_ends = set()
    for index, day in enumerate(ordered):
        is_last = index + 1 == len(ordered) or ordered[index + 1][:7] != day[:7]
        if is_last:
            month_ends.add(day)
    rows = [(market, day, index, 1 if day in month_ends else 0, source)
            for index, day in enumerate(ordered)]
    with transaction(conn):
        cursor = conn.executemany(
            """INSERT OR IGNORE INTO pit_calendar
                   (market, session_date, session_index, is_month_end, source)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def session_index(conn: sqlite3.Connection, session_date: str,
                  market: str = "XNYS") -> Optional[int]:
    row = conn.execute(
        "SELECT session_index FROM pit_calendar WHERE market = ? AND session_date = ?",
        (market, session_date),
    ).fetchone()
    return int(row["session_index"]) if row else None


def session_offset(conn: sqlite3.Connection, session_date: str, sessions: int,
                   market: str = "XNYS") -> Optional[str]:
    """The session `sessions` trading days after `session_date`.

    Returns None past the end of the calendar, which is how a label learns it is
    censored rather than silently truncating to the last available bar.
    """
    base = session_index(conn, session_date, market)
    if base is None:
        return None
    row = conn.execute(
        "SELECT session_date FROM pit_calendar WHERE market = ? AND session_index = ?",
        (market, base + sessions),
    ).fetchone()
    return row["session_date"] if row else None


def month_end_sessions(conn: sqlite3.Connection, start: str, end: str,
                       market: str = "XNYS") -> list[str]:
    """The month-end trading sessions in a range -- the Phase 1 replay grid."""
    rows = conn.execute(
        """SELECT session_date FROM pit_calendar
            WHERE market = ? AND is_month_end = 1
              AND session_date >= ? AND session_date <= ?
            ORDER BY session_date""",
        (market, start, end),
    ).fetchall()
    return [r["session_date"] for r in rows]


# --------------------------------------------------------------------------
# Replay runs, features and scores
# --------------------------------------------------------------------------

def start_replay_run(conn: sqlite3.Connection, model_version: str, as_of_grid: str,
                     **fields) -> int:
    with transaction(conn):
        cursor = conn.execute(
            """INSERT INTO pit_replay_run
                   (started_at, as_of_from, as_of_to, as_of_grid, model_version,
                    latency_policy_version, ladder_version, peer_set_version,
                    universe_def_json, source_versions_json, crosswalk_hash,
                    code_revision, seed, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
            (_now(), fields.get("as_of_from"), fields.get("as_of_to"), as_of_grid,
             model_version,
             fields.get("latency_policy_version", LATENCY_POLICY_VERSION),
             fields.get("ladder_version", LADDER_VERSION),
             fields.get("peer_set_version", PEER_SET_VERSION),
             _dumps(fields.get("universe_def")), _dumps(fields.get("source_versions")),
             fields.get("crosswalk_hash"), fields.get("code_revision"),
             fields.get("seed")),
        )
    return int(cursor.lastrowid)


def finish_replay_run(conn: sqlite3.Connection, run_id: int, status: str = "complete",
                      **fields) -> None:
    with transaction(conn):
        conn.execute(
            """UPDATE pit_replay_run
                  SET finished_at = ?, status = ?, n_entities = ?, n_dates = ?,
                      summary_json = ?
                WHERE run_id = ?""",
            (_now(), status, fields.get("n_entities"), fields.get("n_dates"),
             _dumps(fields.get("summary")), run_id),
        )


FEATURE_COLUMNS = (
    "entity_id", "listing_id", "as_of_date", "feature_key", "feature_role",
    "sources_json", "source_tag", "ladder_version", "source_tag_changed",
    "fact_max_age_days", "feature_available_date", "latency_policy_version",
    "raw_value", "normalized_value", "norm_method", "peer_set_id",
    "availability", "available_flag", "unavailable_reason", "model_version",
    "replay_run_id", "created_at",
)

_FEATURE_INSERT = (
    "INSERT OR IGNORE INTO pit_feature (" + ", ".join(FEATURE_COLUMNS) + ") VALUES ("
    + ", ".join("?" * len(FEATURE_COLUMNS)) + ")"
)


def insert_features(conn: sqlite3.Connection, rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert feature tuples in FEATURE_COLUMNS order.

    The CHECK constraint on the table rejects any row whose availability date
    post-dates its as-of date, so a leak of this shape fails loudly at write
    time rather than becoming a quietly optimistic backtest.
    """
    cursor = conn.executemany(_FEATURE_INSERT, rows)
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def save_score(conn: sqlite3.Connection, entity_id: int, as_of_date: str,
               model_version: str, replay_run_id: int, **fields) -> str:
    """Write one historical score row. Returns 'written' or 'exists'.

    Immutable in the same sense as the live close snapshot: a row that already
    exists for this (entity, date, model, run) is never rewritten. Re-scoring
    under a corrected replay means a NEW run_id, so both stay side by side and
    can be diffed.
    """
    existing = conn.execute(
        """SELECT 1 FROM pit_score
            WHERE entity_id = ? AND as_of_date = ? AND model_version = ?
              AND replay_run_id = ?""",
        (entity_id, as_of_date, model_version, replay_run_id),
    ).fetchone()
    if existing:
        return "exists"
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_score
                   (entity_id, listing_id, as_of_date, model_version, replay_run_id,
                    latency_policy_version, peer_set_version, ladder_version,
                    company_score, sector_overlay, political_overlay, final_score,
                    classification, benchmark_level, valuation_benchmark_level,
                    n_industry_peers, n_valid_peers, n_ebitda_cohort,
                    effective_weights_json, factor_scores_json, raw_inputs_json,
                    missing_factors_json, notes_json, provenance_json,
                    coverage, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_id, fields.get("listing_id"), as_of_date, model_version,
             replay_run_id,
             fields.get("latency_policy_version", LATENCY_POLICY_VERSION),
             fields.get("peer_set_version", PEER_SET_VERSION),
             fields.get("ladder_version", LADDER_VERSION),
             fields.get("company_score"), fields.get("sector_overlay"),
             fields.get("political_overlay"), fields.get("final_score"),
             fields.get("classification"), fields.get("benchmark_level"),
             fields.get("valuation_benchmark_level"),
             fields.get("n_industry_peers"), fields.get("n_valid_peers"),
             fields.get("n_ebitda_cohort"),
             _dumps(fields.get("effective_weights") or {}),
             _dumps(fields.get("factor_scores")), _dumps(fields.get("raw_inputs")),
             _dumps(fields.get("missing_factors")), _dumps(fields.get("notes")),
             _dumps(fields.get("provenance")),
             fields.get("coverage"), fields.get("confidence"), _now()),
        )
    return "written"


def save_label(conn: sqlite3.Connection, listing_id: int, as_of_date: str,
               horizon: str, **fields) -> None:
    """Upsert one forward-return label.

    Deliberately mutable, unlike features and scores: a label is an observation
    of the future and is refined as more price history arrives. The point-in-time
    rule binds the FEATURES, not the outcome.

    `label_policy_version` defaults to the module's CURRENT policy rather than
    to a frozen v1 string, so a v2 labeller that bumps the constant is stamped
    correctly without editing every caller -- and the upsert sets the column, so
    writing a v2 row over a v1 row raises instead of silently succeeding.
    """
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_label
                   (listing_id, entity_id, as_of_date, horizon, anchor_date,
                    anchor_price, anchor_roll_days, end_date, end_price,
                    terminal_roll_days, forward_return, benchmark_forward_return,
                    excess_return, net_forward_return, cost_assumption_set_id,
                    censored, terminal_reason, price_source, price_ingest_id,
                    computed_at, label_policy_version, label_run_id,
                    benchmark_listing_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?)
               ON CONFLICT (listing_id, as_of_date, horizon) DO UPDATE SET
                    anchor_date = excluded.anchor_date,
                    anchor_price = excluded.anchor_price,
                    anchor_roll_days = excluded.anchor_roll_days,
                    end_date = excluded.end_date,
                    end_price = excluded.end_price,
                    terminal_roll_days = excluded.terminal_roll_days,
                    forward_return = excluded.forward_return,
                    benchmark_forward_return = excluded.benchmark_forward_return,
                    excess_return = excluded.excess_return,
                    net_forward_return = excluded.net_forward_return,
                    cost_assumption_set_id = excluded.cost_assumption_set_id,
                    censored = excluded.censored,
                    terminal_reason = excluded.terminal_reason,
                    computed_at = excluded.computed_at,
                    label_policy_version = excluded.label_policy_version,
                    label_run_id = excluded.label_run_id,
                    benchmark_listing_id = excluded.benchmark_listing_id""",
            (listing_id, fields.get("entity_id"), as_of_date, horizon,
             fields.get("anchor_date"), fields.get("anchor_price"),
             int(fields.get("anchor_roll_days", 0)), fields.get("end_date"),
             fields.get("end_price"), int(fields.get("terminal_roll_days", 0)),
             fields.get("forward_return"), fields.get("benchmark_forward_return"),
             fields.get("excess_return"), fields.get("net_forward_return"),
             fields.get("cost_assumption_set_id"), int(fields.get("censored", 0)),
             fields.get("terminal_reason"), fields.get("price_source", "unknown"),
             fields.get("price_ingest_id"), _now(),
             fields.get("label_policy_version", LABEL_POLICY_VERSION),
             fields.get("label_run_id"), fields.get("benchmark_listing_id")),
        )


# --------------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------------

def register_model_lineage(conn: sqlite3.Connection, model_version: str,
                           model_name: str, asset_class: str, authorship: str,
                           **fields) -> None:
    """Record a model version's authored specification, permanently.

    A successor never rewrites its predecessor: `equity_shaffer_v1` keeps its
    row and stays reproducible forever even after a v2 is promoted.
    """
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_model_lineage
                   (model_version, model_name, asset_class, authorship, created_date,
                    predecessor_version, factor_definitions_json, weights_json,
                    transformations_json, missing_data_rule, normalization,
                    overlays_json, calibration_version, hedge_rules_json, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(model_version) DO UPDATE SET
                    status = excluded.status,
                    notes  = excluded.notes""",
            (model_version, model_name, asset_class, authorship,
             fields.get("created_date") or _now()[:10],
             fields.get("predecessor_version"),
             _dumps(fields.get("factor_definitions")), _dumps(fields.get("weights")),
             _dumps(fields.get("transformations")), fields.get("missing_data_rule"),
             fields.get("normalization"), _dumps(fields.get("overlays")),
             fields.get("calibration_version"), _dumps(fields.get("hedge_rules")),
             fields.get("status", RESEARCH), fields.get("notes")),
        )


def record_promotion(conn: sqlite3.Connection, model_version: str, from_status: str,
                     to_status: str, promoted_by: str, **fields) -> int:
    """The only way a model changes status. Never automatic."""
    with transaction(conn):
        cursor = conn.execute(
            """INSERT INTO pit_promotion
                   (model_version, from_status, to_status, proposal_id,
                    evidence_json, promoted_by, promoted_at, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (model_version, from_status, to_status, fields.get("proposal_id"),
             _dumps(fields.get("evidence")), promoted_by, _now(), fields.get("note")),
        )
        conn.execute("UPDATE pit_model_lineage SET status = ? WHERE model_version = ?",
                     (to_status, model_version))
    return int(cursor.lastrowid)


# --------------------------------------------------------------------------
# Ingest provenance
# --------------------------------------------------------------------------

def start_ingest(conn: sqlite3.Connection, source: str, scope: str = "") -> int:
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO pit_ingest_run (source, scope, started_at) VALUES (?, ?, ?)",
            (source, scope, _now()),
        )
    return int(cursor.lastrowid)


def finish_ingest(conn: sqlite3.Connection, ingest_id: int, status: str = "complete",
                  **fields) -> None:
    with transaction(conn):
        conn.execute(
            """UPDATE pit_ingest_run
                  SET finished_at = ?, status = ?, rows_read = ?, rows_kept = ?,
                      rows_rejected = ?, bytes_downloaded = ?, summary_json = ?
                WHERE ingest_id = ?""",
            (_now(), status, fields.get("rows_read", 0), fields.get("rows_kept", 0),
             fields.get("rows_rejected", 0), fields.get("bytes_downloaded", 0),
             _dumps(fields.get("summary")), ingest_id),
        )


def store_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts per table, for the coverage reporting the ML lab needs."""
    tables = [
        "pit_entity", "pit_entity_name", "pit_entity_sic", "pit_entity_exit",
        "pit_listing", "pit_fact", "pit_price_bar", "pit_corporate_action",
        "pit_macro_obs", "pit_curve_obs", "pit_calendar", "pit_peer_set",
        "pit_feature", "pit_score", "pit_label", "pit_option_snapshot",
        "pit_replay_run", "pit_model_lineage", "pit_proposal",
    ]
    out: dict[str, int] = {}
    for table in tables:
        try:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            out[table] = int(row["n"])
        except sqlite3.Error:
            out[table] = -1
    return out
