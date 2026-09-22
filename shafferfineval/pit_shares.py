"""Point-in-time SHARE COUNTS -- the missing half of historical market cap.

The valuation factor is 40% of the equity Shaffer Score and it needs a market
capitalisation. A market capitalisation is

    MarketCap_t = HistoricalPrice_t x SharesKnownAt_t

and the second term has, until this module, had no source. DERA's compact
`num.txt` -- the bulk path in `pit_dera` -- carries 3 rows of
`dei:EntityCommonStockSharesOutstanding` and ZERO `dei:EntityPublicFloat` in
2,833,915 rows, because the compact dataset drops most cover-page facts. The
one share concept DERA does carry at scale is `us-gaap:CommonStockSharesOutstanding`
(56,095 rows over 6,510 entities in the live store as this was written), and
that one is dated at the BALANCE SHEET, not at the cover page. So this module
is a TARGETED complement fetched per issuer from the XBRL companyconcept API:
five concepts, nothing else, streamed and filtered rather than stored whole.

TODAY'S SHARE COUNT MUST NEVER BE USED FOR A HISTORICAL DATE. That is the whole
reason this file exists, and it is not a slogan -- it is the difference between
Apple being worth $1.37tn and $343bn on 2020-01-31.

==========================================================================
THE SPLIT TRAP -- read this before using anything below
==========================================================================

Share counts are RESTATED RETROACTIVELY for splits. Measured live on
2026-09-20 from companyconcept, `us-gaap:CommonStockSharesOutstanding` for
Apple (CIK 320193) at the instant 2019-09-28:

    4,443,236,000   accn 0000320193-19-000119   10-K   filed 2019-10-31
    4,443,236,000   accn 0000320193-20-000010   10-Q   filed 2020-01-29
    4,443,236,000   accn 0000320193-20-000052   10-Q   filed 2020-05-01
    4,443,236,000   accn 0000320193-20-000062   10-Q   filed 2020-07-31
   17,772,945,000   accn 0000320193-20-000096   10-K   filed 2020-10-30

One instant, one tag, two numbers four times apart, and the only thing that
separates them is the 4:1 split of 2020-08-31. The point-in-time selector in
this module handles that BY CONSTRUCTION -- it takes the newest vintage
AVAILABLE AT THE AS-OF DATE, so a 2020-01-31 as-of returns 4,443,236,000 and
never sees the restatement. That is correct, and it has a consequence that is
easy to get catastrophically wrong:

    A POINT-IN-TIME SHARE COUNT IS IN THE SHARE BASIS OF ITS OWN ERA.
    IT MUST BE MULTIPLIED BY A RAW, AS-PRINTED, UN-ADJUSTED PRICE.

Yahoo's `adjclose` -- and its `close`, which is split-adjusted even when the
dividend adjustment is off -- are retroactively restated by every later split.
Pairing a pre-split PIT share count with today's retroactively split-adjusted
price series understates Apple's 2020-01-31 market cap by exactly the 4x split:
4.443bn x $309.51 (as printed) = $1.375tn, against 4.443bn x ~$77 (as shown
today) = $343bn. The number looks entirely plausible. That is what makes it
dangerous. `price_basis_for()` states, per observation, which price series is
admissible, and `market_cap_as_of()` REFUSES an adjusted-basis price outright.

The mirror-image error is subtler and is not fixed by using a raw price. The
count is measured on date D, the price on the as-of date A, and D < A always
(the count arrives with a filing). If a split falls between D and A, the count
is in pre-split terms and the raw price at A is in post-split terms:

    Apple, as-of 2020-09-30. Newest cover count available: 4,275,634,000 at
    2020-07-17 (filed 2020-07-31). Raw close 2020-09-30: $115.81. Naive
    product: $495bn. Truth: ~$1.98tn. Wrong by the same 4x, opposite sign.

So `market_cap_as_of()` takes a split factor for the window (D, A] and records
whether it was actually CHECKED. An unchecked window is reported as unchecked;
it is never quietly assumed to be 1.0 without saying so.

==========================================================================
THE CONCEPTS ARE NOT INTERCHANGEABLE
==========================================================================

They measure four different things and a silent substitution is a wrong number
wearing a right number's clothes. `concept_key` is stored on every row so that
no consumer ever has to guess which one it got:

  dei:EntityCommonStockSharesOutstanding  -- the COVER PAGE count, dated a few
      weeks AFTER period end (Apple: instant 2020-07-17 on a quarter ending
      2020-06-27). Best availability, and the right input for market cap.
  us-gaap:CommonStockSharesOutstanding    -- dated at the BALANCE SHEET date.
      Older by those few weeks, and the one DERA actually carries.
  us-gaap:CommonStockSharesIssued         -- ISSUED, which includes treasury
      stock. An UPPER BOUND on outstanding, not a synonym; off the default
      ladder entirely and opt-in only.
  us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding -- a PERIOD AVERAGE
      for per-share arithmetic. It is not a point-in-time count and this module
      will not return it as one, ever. When it is the only thing available the
      answer is "unavailable" and the valuation factor drops.
  dei:EntityPublicFloat                   -- an annual USD MARKET VALUE, as of
      the prior fiscal Q2, excluding affiliate holdings. Point-in-time, and a
      poor market-cap proxy: stored for research, never a share count.

==========================================================================
WHAT THE SOURCE CANNOT GIVE (measured, not assumed)
==========================================================================

* PRE-XBRL IS EMPTY. companyconcept 404s for Lehman (806085) and Enron
  (1024401) on every share concept. XBRL begins ~2009; an issuer that died
  before it has no share count here and never will. That is a hole in the
  dead cohort, and it is reported, not filled.
* MULTI-CLASS COVER PAGES VANISH. Alphabet (1652044) 404s on
  `dei:EntityCommonStockSharesOutstanding` because it tags the cover count once
  PER CLASS, and the companyconcept API returns only undimensioned facts. Its
  `us-gaap:CommonStockSharesOutstanding` survives and is the consolidated total
  (12,088,000,000 at 2025-12-31). Berkshire (1067983) loses both: 7 cover rows
  from 2009-2011 and a 404 on the balance-sheet tag.
* A ZERO IS A REAL NUMBER. CIK 40730 -- General Motors Corp, now Motors
  Liquidation Co -- reports `EntityCommonStockSharesOutstanding` = 0 from 2012
  onward. Correctly selected, genuinely zero, and a $0 market cap would rank as
  infinitely cheap in a valuation percentile. `shares_as_of` returns the zero
  and flags it; `market_cap_as_of` refuses it by default.

==========================================================================
INVARIANTS
==========================================================================

1. APPEND-ONLY, LIKE pit_fact. A restatement is a new row with its own
   accession and availability date. Enforced by triggers, not convention.
2. NOTHING IS KNOWABLE BEFORE IT WAS AVAILABLE. `available_date` comes from
   `pit_policy.available_date` and is never earlier than `filed`; every
   selector filters `available_date <= as_of`, and `period_end <= as_of` too.
3. `frame` IS NEVER STORED. It always carries the LATEST restated value and has
   no filed date. `fy`/`fp` describe the FILING, not the fact, so they are
   stored for audit and never selected on.
4. NO SUBSTITUTION WITHOUT A LABEL. Every returned count names its concept.
5. NO COUNT, NO MARKET CAP. The answer is None with a reason. The valuation
   factor is then unavailable and the FROZEN production rule applies: drop it
   and renormalise the rest over 0.60 -- growth 0.4167, profitability 0.3333,
   debt 0.25. This module records that rule; it does not implement scoring.

This module owns its own tables and does not edit `pit_store`. Stdlib plus
`requests`, reusing `pit_identity`'s throttled and cached SEC session so the
whole process stays under the SEC's 10 requests/second ceiling.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence

import pit_identity
import pit_policy
import pit_store
from pit_identity import SEC_USER_AGENT, cik10, fetch_sec_json, submission_index

__all__ = [
    "SCHEMA", "ensure_schema",
    "ShareConcept", "SHARE_CONCEPTS", "share_concepts", "concept_for",
    "concept_spec", "CONCEPT_DEI_COVER", "CONCEPT_GAAP_OUTSTANDING",
    "CONCEPT_GAAP_ISSUED", "CONCEPT_WEIGHTED_DILUTED", "CONCEPT_PUBLIC_FLOAT",
    "SHARES_LADDER_STRICT", "SHARES_LADDER_WITH_ISSUED",
    "fetch_share_concept", "filter_companyfacts", "acceptance_index",
    "ingest_entity_shares", "insert_share_obs",
    "shares_as_of", "shares_as_of_detail",
    "market_cap_as_of", "market_cap_as_of_detail",
    "price_basis_for", "split_factor_between",
    "coverage_report", "valuation_dropped_weights",
]

# --------------------------------------------------------------------------
# Endpoints. The SEC session, its UA and its throttle come from pit_identity,
# so both modules share ONE rate budget against the 10 requests/second ceiling.
# --------------------------------------------------------------------------

COMPANYCONCEPT_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/{taxonomy}/{tag}.json")
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

SOURCE_COMPANYCONCEPT = "sec:companyconcept"
SOURCE_COMPANYFACTS = "sec:companyfacts"
SOURCE_SUBMISSIONS = "sec:submissions"

# --------------------------------------------------------------------------
# What a row MEANS. Stored per observation so a consumer never has to infer it.
# --------------------------------------------------------------------------

#: A count at one instant. The only kind admissible in a market cap.
MEASURE_POINT_IN_TIME = "point_in_time_count"

#: An average over a reporting period. Per-share arithmetic only.
MEASURE_PERIOD_AVERAGE = "period_average_count"

#: A dollar amount, not a count.
MEASURE_MARKET_VALUE = "market_value_usd"

#: Which price series an observation may legally be multiplied by.
PRICE_BASIS_RAW = "raw_as_printed_unadjusted"
PRICE_BASIS_SPLIT_ADJUSTED = "split_adjusted"
PRICE_BASIS_TOTAL_RETURN = "split_and_dividend_adjusted"

#: Bases a market cap will accept. Everything else is refused, loudly.
ADMISSIBLE_PRICE_BASES = frozenset({PRICE_BASIS_RAW})

UNIT_SHARES = "shares"
UNIT_USD = "USD"

CONCEPT_DEI_COVER = "dei_cover_outstanding"
CONCEPT_GAAP_OUTSTANDING = "gaap_balance_sheet_outstanding"
CONCEPT_GAAP_ISSUED = "gaap_balance_sheet_issued"
CONCEPT_WEIGHTED_DILUTED = "gaap_weighted_average_diluted"
CONCEPT_PUBLIC_FLOAT = "dei_public_float"

#: Why a count could not be produced. These land in
#: `pit_feature.unavailable_reason` alongside pit_store's own vocabulary.
REASON_NEVER_FILED = "never_filed_a_share_count"
REASON_NOT_YET_FILED = pit_store.REASON_NOT_YET_FILED
REASON_STALE = pit_store.REASON_STALE
REASON_ONLY_PERIOD_AVERAGE = "only_period_average_available"
REASON_ONLY_PUBLIC_FLOAT = "only_public_float_available"
REASON_ZERO_SHARE_COUNT = "zero_share_count"
REASON_NO_PRICE = "no_raw_price"
REASON_PRICE_BASIS_REFUSED = "price_basis_not_raw"
REASON_SPLIT_UNRESOLVED = "split_window_unresolved"

#: How the split window between the count date and the as-of date was settled.
SPLIT_CHECKED = "checked"
SPLIT_NO_EVENTS_RECORDED = "no_events_recorded"
SPLIT_UNCHECKED = "unchecked"
SPLIT_CALLER_SUPPLIED = "caller_supplied"

#: The concept name `pit_policy`'s staleness override is keyed on. The cover
#: count is refreshed at EVERY filing, so 12 months annual / 4 months quarterly
#: -- an older one means a filing was missed, not that the number moves slowly.
STALENESS_CONCEPT = "shares_outstanding"


@dataclass(frozen=True)
class ShareConcept:
    """One XBRL concept that carries something share-shaped.

    `point_in_time` is the field that decides whether a concept may ever answer
    "how many shares existed on this date". It is a property of the concept and
    not of the caller's intent, which is why it lives here rather than in a
    comment at the call site.

    `dates_at` records WHAT the fact's `end` field means -- a cover page dated
    weeks after period end is a different measurement from a balance-sheet
    instant, even though both are instants.
    """

    key: str
    taxonomy: str
    tag: str
    unit: str
    measure_kind: str
    point_in_time: bool
    dates_at: str
    upper_bound: bool = False
    why: str = ""

    @property
    def url_taxonomy(self) -> str:
        return self.taxonomy

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "taxonomy": self.taxonomy,
            "tag": self.tag,
            "unit": self.unit,
            "measure_kind": self.measure_kind,
            "point_in_time": self.point_in_time,
            "dates_at": self.dates_at,
            "upper_bound": self.upper_bound,
            "why": self.why,
        }


SHARE_CONCEPTS: tuple[ShareConcept, ...] = (
    ShareConcept(
        key=CONCEPT_DEI_COVER,
        taxonomy=pit_policy.TAXONOMY_DEI,
        tag="EntityCommonStockSharesOutstanding",
        unit=UNIT_SHARES,
        measure_kind=MEASURE_POINT_IN_TIME,
        point_in_time=True,
        dates_at="cover_page_date",
        why=("The cover-page count, dated a few weeks AFTER period end -- Apple's "
             "quarter ending 2020-06-27 carries an instant of 2020-07-17. That "
             "makes it both the freshest count and the closest to the filing "
             "date, which is why it leads. It is also the concept DERA's compact "
             "num.txt effectively drops: 3 rows in 2,833,915."),
    ),
    ShareConcept(
        key=CONCEPT_GAAP_OUTSTANDING,
        taxonomy=pit_policy.TAXONOMY_US_GAAP,
        tag="CommonStockSharesOutstanding",
        unit=UNIT_SHARES,
        measure_kind=MEASURE_POINT_IN_TIME,
        point_in_time=True,
        dates_at="balance_sheet_date",
        why=("The balance-sheet count. Same quantity as the cover page, measured "
             "a few weeks earlier, and the one concept DERA carries at scale. "
             "Second because older is worse, not because it is less true."),
    ),
    ShareConcept(
        key=CONCEPT_GAAP_ISSUED,
        taxonomy=pit_policy.TAXONOMY_US_GAAP,
        tag="CommonStockSharesIssued",
        unit=UNIT_SHARES,
        measure_kind=MEASURE_POINT_IN_TIME,
        point_in_time=True,
        dates_at="balance_sheet_date",
        upper_bound=True,
        why=("ISSUED, which includes shares held in treasury. Equal to "
             "outstanding only for an issuer with no buyback history, and an "
             "upper bound otherwise -- so it is OFF the default ladder and must "
             "be opted into with SHARES_LADDER_WITH_ISSUED. A silent fall to "
             "this rung would overstate market cap for every serial repurchaser."),
    ),
    ShareConcept(
        key=CONCEPT_WEIGHTED_DILUTED,
        taxonomy=pit_policy.TAXONOMY_US_GAAP,
        tag="WeightedAverageNumberOfDilutedSharesOutstanding",
        unit=UNIT_SHARES,
        measure_kind=MEASURE_PERIOD_AVERAGE,
        point_in_time=False,
        dates_at="period_end_of_an_averaging_window",
        why=("A DURATION average built for earnings per share, and emphatically "
             "not a count on a date. It is ingested so that the answer to 'was "
             "anything available?' can be measured honestly, and it is excluded "
             "from every point-in-time ladder by construction. When it is the "
             "only concept an issuer filed, the count is UNAVAILABLE."),
    ),
    ShareConcept(
        key=CONCEPT_PUBLIC_FLOAT,
        taxonomy=pit_policy.TAXONOMY_DEI,
        tag="EntityPublicFloat",
        unit=UNIT_USD,
        measure_kind=MEASURE_MARKET_VALUE,
        point_in_time=True,
        dates_at="prior_fiscal_q2_last_business_day",
        why=("A USD market value, not a count: the aggregate value of shares "
             "held by non-affiliates, disclosed annually as of the last business "
             "day of the prior fiscal second quarter. Genuinely point-in-time "
             "and genuinely a poor market-cap proxy -- it excludes affiliate "
             "holdings and is up to fifteen months stale. Stored for research; "
             "never a share count and never a market cap."),
    ),
)

_BY_KEY = {c.key: c for c in SHARE_CONCEPTS}

#: The default point-in-time ladder. Outstanding only: the cover page first
#: because it is fresher, the balance sheet second because it is the same
#: quantity measured earlier.
SHARES_LADDER_STRICT: tuple[str, ...] = (CONCEPT_DEI_COVER, CONCEPT_GAAP_OUTSTANDING)

#: Opt-in ladder that will accept ISSUED as a last resort. Every result it
#: produces carries `upper_bound=True`, so a consumer can exclude them.
SHARES_LADDER_WITH_ISSUED: tuple[str, ...] = SHARES_LADDER_STRICT + (CONCEPT_GAAP_ISSUED,)

#: Concepts fetched by default: everything, including the two that can never
#: answer the question, because "only the weighted average exists" is a finding
#: that has to be measurable rather than inferred from an absence.
DEFAULT_INGEST_CONCEPTS: tuple[str, ...] = tuple(c.key for c in SHARE_CONCEPTS)

#: The FROZEN production rule when valuation drops out. Recorded here, applied
#: by `company_scoring`; this module never scores anything.
VALUATION_DROPPED_WEIGHTS: dict[str, float] = {
    "growth": 0.25 / 0.60,
    "profitability": 0.20 / 0.60,
    "debt": 0.15 / 0.60,
}


def share_concepts() -> tuple[ShareConcept, ...]:
    """Every share-shaped concept this module knows, in ladder order."""
    return SHARE_CONCEPTS


def concept_for(key: str) -> ShareConcept:
    """One concept by key. Raises on an unknown key rather than guessing."""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(
            f"unknown share concept {key!r}; known: {', '.join(sorted(_BY_KEY))}"
        ) from None


def valuation_dropped_weights() -> dict[str, Any]:
    """The production drop-and-renormalise rule, as a record.

    Stated here so a replay that lost the valuation factor can SAY so in
    `pit_score.effective_weights_json` -- a score computed without valuation is
    a different model state, not a slightly noisier version of the same one.
    Nothing in this module changes a weight; the core is frozen.
    """
    return {
        "rule": "drop the unavailable factor and renormalise over the remainder",
        "full_weights": {"valuation": 0.40, "growth": 0.25,
                         "profitability": 0.20, "debt": 0.15},
        "renormalised_over": 0.60,
        "effective_weights_without_valuation": dict(VALUATION_DROPPED_WEIGHTS),
        "rounded": {"growth": 0.4167, "profitability": 0.3333, "debt": 0.25},
        "why": ("A missing factor never contributes a silent zero. The score is "
                "still produced, and it is a DIFFERENT model state, which the "
                "effective weights record."),
    }


def concept_spec() -> dict[str, Any]:
    """The whole share policy as a JSON-serialisable record.

    What a replay run stores so that a future reader can tell which concept
    answered, which ladder was in force, and what price basis the answer
    required -- without having to re-read this file.
    """
    return {
        "module": "pit_shares",
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
        "staleness_concept": STALENESS_CONCEPT,
        "max_age_months": {
            "annual": pit_policy.max_age_months(4, STALENESS_CONCEPT),
            "quarterly": pit_policy.max_age_months(0, STALENESS_CONCEPT),
        },
        "ladder_strict": list(SHARES_LADDER_STRICT),
        "ladder_with_issued": list(SHARES_LADDER_WITH_ISSUED),
        "admissible_price_bases": sorted(ADMISSIBLE_PRICE_BASES),
        "concepts": [c.as_dict() for c in SHARE_CONCEPTS],
        "never_stored": ["frame"],
        "stored_but_never_selected_on": ["fy", "fp"],
        "split_rule": ("A PIT share count is in the share basis of its own era. "
                       "Pair it with a raw un-adjusted price, and un-apply any "
                       "split falling between the count date and the as-of date."),
        "valuation_dropped": valuation_dropped_weights(),
    }


# ==========================================================================
# SCHEMA. Owned here; `pit_store` is not edited. Consolidation is a later step.
# ==========================================================================

SCHEMA = """
-- One immutable row per published share observation. Deliberately a MIRROR of
-- pit_fact's shape (same acceptance audit trail, same append-only triggers,
-- same available_date >= filed check) so that folding it into pit_fact later is
-- a copy rather than a translation.
--
-- `concept_key` is not decoration: a cover-page count, a balance-sheet count, a
-- treasury-inclusive issued count and a period average are four different
-- measurements, and a consumer must never have to infer which one it holds.
--
-- `frame` is absent on purpose -- it always carries the LATEST restated value
-- and has no filed date, so it can never be a point-in-time selector. `fy` and
-- `fp` describe the FILING, not the fact, and are stored for audit only.
CREATE TABLE IF NOT EXISTS pit_share_obs (
    share_obs_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    concept_key           TEXT NOT NULL,
    taxonomy              TEXT NOT NULL,
    tag                   TEXT NOT NULL,
    unit                  TEXT NOT NULL,
    measure_kind          TEXT NOT NULL,
    period_start          TEXT,
    period_end            TEXT NOT NULL,
    qtrs                  INTEGER NOT NULL,
    val                   REAL NOT NULL,
    accn                  TEXT NOT NULL,
    form                  TEXT NOT NULL,
    fy                    INTEGER,
    fp                    TEXT,
    filed                 TEXT NOT NULL,
    accepted_raw          TEXT,
    accepted_eastern      TEXT,
    available_date        TEXT NOT NULL,
    availability_rule     TEXT NOT NULL,
    latency_policy_version TEXT NOT NULL,
    price_basis           TEXT NOT NULL,
    source                TEXT NOT NULL,
    ingested_at           TEXT NOT NULL,
    UNIQUE (entity_id, tag, unit, period_end, accn),
    CHECK (qtrs IN (0, 1, 2, 3, 4)),
    CHECK (val >= 0),
    CHECK (available_date >= filed)
);

CREATE INDEX IF NOT EXISTS idx_pit_share_select
    ON pit_share_obs (entity_id, concept_key, period_end, available_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_share_obs_no_update
BEFORE UPDATE ON pit_share_obs
BEGIN
    SELECT RAISE(ABORT, 'pit_share_obs is append-only: a restatement is a new row');
END;

CREATE TRIGGER IF NOT EXISTS trg_pit_share_obs_no_delete
BEFORE DELETE ON pit_share_obs
BEGIN
    SELECT RAISE(ABORT, 'pit_share_obs is append-only: published facts are never deleted');
END;

-- Per (entity, concept) ingest evidence, including the NEGATIVE results. A 404
-- on Lehman's cover count is a FACT about the source -- XBRL began in 2009 and
-- Lehman died in 2008 -- not a failed fetch, and coverage arithmetic that
-- cannot see the 404s will quietly divide by the survivors.
CREATE TABLE IF NOT EXISTS pit_share_ingest (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    cik                   TEXT NOT NULL,
    concept_key           TEXT NOT NULL,
    http_status           INTEGER NOT NULL,
    rows_seen             INTEGER NOT NULL DEFAULT 0,
    rows_kept             INTEGER NOT NULL DEFAULT 0,
    rows_rejected         INTEGER NOT NULL DEFAULT 0,
    first_period_end      TEXT,
    last_period_end       TEXT,
    acceptance_matched    INTEGER NOT NULL DEFAULT 0,
    source                TEXT NOT NULL,
    checked_at            TEXT NOT NULL,
    UNIQUE (entity_id, concept_key, source)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create this module's tables if absent. Idempotent; returns a status.

    Separate from `pit_store.init_db` on purpose: a full DERA ingest may hold
    the write lock on the shared store for half an hour, and a schema helper
    that can be pointed at a throwaway database is what lets pilot work proceed
    without touching it.
    """
    existed = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'pit_share%'")}
    conn.executescript(SCHEMA)
    conn.commit()
    return "exists" if {"pit_share_obs", "pit_share_ingest"} <= existed else "created"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _day(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None
    return text[:10]


def _days_between(start: Any, end: Any) -> int:
    """Calendar days from `start` to `end`; a huge number if either is unusable.

    The sentinel makes an unparseable date read as "impossibly stale", matching
    `pit_identity`: a bad date can never be mistaken for a fresh one.
    """
    a, b = _day(start), _day(end)
    if a is None or b is None:
        return 10 ** 6
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days


def _qtrs(start: Any, end: Any) -> int:
    """pit_fact's `qtrs` for one observation. 0 means instantaneous.

    Clamped into 0..4 because that is the CHECK constraint's domain. A duration
    longer than four quarters -- a transition period, or a comparative spanning
    a fiscal-year change -- is clamped to 4 and is visible as such: the exact
    length is recoverable from period_start and period_end, which are stored.
    """
    if start is None or _day(start) is None:
        return 0
    days = _days_between(start, end)
    quarters = int(round(days / 91.3125))
    return max(1, min(4, quarters))


def _percentiles(values: Sequence[float],
                 points: Sequence[int] = (5, 25, 50, 75, 95)) -> dict[str, float]:
    """Nearest-rank percentiles over a small sample. Stdlib, no numpy.

    Nearest-rank rather than interpolated because these are DAY COUNTS of real
    filings -- an interpolated 47.5-day lag is a number no filing ever had.
    """
    if not values:
        return {}
    ordered = sorted(values)
    out: dict[str, float] = {"n": float(len(ordered))}
    for point in points:
        rank = max(1, min(len(ordered), math.ceil(point / 100.0 * len(ordered))))
        out[f"p{point}"] = ordered[rank - 1]
    out["mean"] = round(sum(ordered) / len(ordered), 1)
    return out


# ==========================================================================
# FETCH. Targeted: five concepts, streamed and filtered, never stored whole.
# ==========================================================================

def fetch_share_concept(cik: Any, concept: ShareConcept,
                        cache_dir: Optional[str] = None) -> dict[str, Any]:
    """One companyconcept document, reduced to the rows this module keeps.

    The companyconcept endpoint IS the targeted filter: it returns one tag for
    one issuer, typically 3-35 KB, against a companyfacts payload that runs to
    megabytes for a long-lived filer. Nothing but the rows survives the call --
    the label, the description and the entity name are dropped on the floor.

    A 404 is a FACT, not a failure: it means this issuer never tagged this
    concept. Lehman and Enron 404 on every share concept because they died
    before XBRL; Alphabet 404s on the cover-page count because it tags that
    count per share class and the API returns only undimensioned facts. The
    status is returned so the caller can record the negative result.

    AN HTTP 200 CAN ALSO BE EMPTY, AND IT LIES. Coca-Cola (CIK 21344) returns
    200 with `{"units": {"shares": {}}}` -- an empty OBJECT where a list of
    observations belongs -- for the cover count, the issued count and the
    weighted average, while its companyfacts document carries 71, 144 and 233
    rows of exactly those tags. So an empty 200 is flagged (`empty_200`) rather
    than read as an absence, and `ingest_entity_shares` falls back to
    companyfacts when it sees one. Treating that 200 as "Coca-Cola never filed
    a share count" would drop a mega cap out of the valuation factor entirely.

    Returns {'status', 'rows', 'units_seen', 'unit_kept', 'empty_200'}. `rows`
    are the raw observations with `frame` already stripped -- it always carries
    the latest restated value and must never reach the store.
    """
    url = COMPANYCONCEPT_URL.format(
        cik10=cik10(cik), taxonomy=concept.taxonomy, tag=concept.tag)
    status, payload = fetch_sec_json(url, cache_dir)
    if status != 200 or not payload:
        return {"status": status, "rows": [], "units_seen": [],
                "unit_kept": None, "empty_200": False}

    units = payload.get("units") or {}
    block = units.get(concept.unit)
    # Defensive on purpose: the unit key exists with a dict value for KO. Only
    # a list is a list of observations.
    observations = block if isinstance(block, list) else []
    rows = [{k: v for k, v in row.items() if k != "frame"} for row in observations]
    return {
        "status": status,
        "rows": rows,
        "units_seen": sorted(units),
        "unit_kept": concept.unit if concept.unit in units else None,
        "empty_200": not rows,
    }


def fetch_companyfacts_shares(cik: Any, cache_dir: Optional[str] = None
                              ) -> dict[str, Any]:
    """The companyfacts route: one request, reduced to the share concepts here.

    Slower and far heavier than companyconcept -- Coca-Cola's companyfacts is
    5.0 MB against 10-35 KB per concept -- so this is a FALLBACK and not the
    default path. It is needed because companyconcept returns an empty 200 for
    some issuers, and a coverage measurement built on the cheap route alone
    would record those issuers as never having filed.

    The payload is filtered the moment it is parsed and never stored whole.
    """
    url = COMPANYFACTS_URL.format(cik10=cik10(cik))
    status, payload = fetch_sec_json(url, cache_dir)
    if status != 200 or not payload:
        return {"status": status, "by_concept": {}}
    return {"status": status, "by_concept": filter_companyfacts(payload)}


def filter_companyfacts(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Reduce a whole companyfacts document to the share concepts, and nothing else.

    A pure function, and the reason it exists is discipline: a caller that
    already holds a companyfacts payload -- because some other pass fetched it
    -- can harvest the share concepts from it WITHOUT this module ever storing a
    whole payload. Apple's companyfacts is megabytes of which this keeps five
    tags; everything else is discarded here rather than written and filtered
    later. `frame` is stripped on the way through.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for concept in SHARE_CONCEPTS:
        block = ((payload.get("facts") or {}).get(concept.taxonomy) or {}).get(concept.tag)
        if not block:
            continue
        rows = (block.get("units") or {}).get(concept.unit) or []
        out[concept.key] = [{k: v for k, v in row.items() if k != "frame"} for row in rows]
    return out


def acceptance_index(cik: Any, cache_dir: Optional[str] = None) -> dict[str, Any]:
    """accession -> EDGAR acceptanceDateTime, from the submissions index.

    companyconcept carries `filed` but no acceptance TIMESTAMP, and `filed` is a
    date. Under `information_latency_policy_v1` a fact with no timestamp is
    available only from the NEXT session, always -- the pessimistic branch,
    because 65.9% of periodic filings are accepted at or after 16:00 ET. One
    extra submissions call per issuer buys the real timestamp for most rows and
    turns that blanket next-session penalty into a measured decision.

    Returns {'status', 'accepted': {accn: accepted_raw}, 'n_filings'}.
    """
    index = submission_index(cik, cache_dir)
    accepted = {row["accn"]: row.get("accepted_at")
                for row in index["rows"] if row.get("accepted_at")}
    return {"status": index["status"], "accepted": accepted,
            "n_filings": len(index["rows"])}


# ==========================================================================
# INGEST
# ==========================================================================

SHARE_COLUMNS = (
    "entity_id", "concept_key", "taxonomy", "tag", "unit", "measure_kind",
    "period_start", "period_end", "qtrs", "val", "accn", "form", "fy", "fp",
    "filed", "accepted_raw", "accepted_eastern", "available_date",
    "availability_rule", "latency_policy_version", "price_basis", "source",
    "ingested_at",
)

_SHARE_INSERT = (
    "INSERT OR IGNORE INTO pit_share_obs (" + ", ".join(SHARE_COLUMNS) + ") VALUES ("
    + ", ".join("?" * len(SHARE_COLUMNS)) + ")"
)


def insert_share_obs(conn: sqlite3.Connection,
                     rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert share tuples in SHARE_COLUMNS order. Returns rows inserted.

    INSERT OR IGNORE is safe here for the same reason it is safe in
    `pit_store.insert_facts`: the unique key includes `accn`, so a conflict
    means the identical published observation is already stored. It can never
    overwrite a value, because the immutability trigger forbids UPDATE outright.
    """
    cursor = conn.executemany(_SHARE_INSERT, rows)
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def _store_concept(conn: sqlite3.Connection, entity_id: int, padded: str,
                   concept: ShareConcept, raw_rows: Sequence[dict[str, Any]],
                   *, status: int, source: str, stamp: str,
                   accepted_map: dict[str, Any],
                   next_session: Optional[Callable[[str], Optional[str]]],
                   units_seen: Sequence[str] = ()) -> dict[str, Any]:
    """Validate, stamp and store one concept's observations. Returns a report.

    Every row leaves here carrying the concept that produced it, the three-form
    acceptance trail and the price basis it demands, so no downstream reader has
    to infer any of the three.
    """
    rows: list[tuple[Any, ...]] = []
    rejected = 0
    matched = 0
    ends: list[str] = []

    for raw in raw_rows:
        period_end = _day(raw.get("end"))
        filed = _day(raw.get("filed"))
        accn = raw.get("accn") or ""
        value = raw.get("val")
        if period_end is None or filed is None or not accn or value is None:
            rejected += 1
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            rejected += 1
            continue
        if value < 0:
            # A negative share count or a negative float is not a partial
            # observation, it is a broken one. Rejected, and counted.
            rejected += 1
            continue

        accepted_raw = accepted_map.get(accn)
        if accepted_raw:
            matched += 1
        detail = pit_policy.available_date_detail(accepted_raw, filed, next_session)
        period_start = _day(raw.get("start"))
        ends.append(period_end)
        rows.append((
            entity_id, concept.key, concept.taxonomy, concept.tag, concept.unit,
            concept.measure_kind, period_start, period_end,
            _qtrs(period_start, period_end), value, accn,
            raw.get("form") or "", raw.get("fy"), raw.get("fp"), filed,
            accepted_raw, detail["accepted_et"], detail["available_date"],
            detail["rule"], detail["latency_policy_version"],
            PRICE_BASIS_RAW, source, stamp,
        ))

    with pit_store.transaction(conn):
        written = insert_share_obs(conn, rows) if rows else 0
        conn.execute(
            """INSERT INTO pit_share_ingest
                   (entity_id, cik, concept_key, http_status, rows_seen,
                    rows_kept, rows_rejected, first_period_end, last_period_end,
                    acceptance_matched, source, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (entity_id, concept_key, source) DO UPDATE SET
                    http_status = excluded.http_status,
                    rows_seen = excluded.rows_seen,
                    rows_kept = excluded.rows_kept,
                    rows_rejected = excluded.rows_rejected,
                    first_period_end = excluded.first_period_end,
                    last_period_end = excluded.last_period_end,
                    acceptance_matched = excluded.acceptance_matched,
                    checked_at = excluded.checked_at""",
            (entity_id, padded, concept.key, status, len(raw_rows), len(rows),
             rejected, min(ends) if ends else None, max(ends) if ends else None,
             matched, source, stamp),
        )

    return {
        "http_status": status,
        "source": source,
        "rows_seen": len(raw_rows),
        "rows_valid": len(rows),
        "rows_written": written,
        "rows_rejected": rejected,
        "acceptance_matched": matched,
        "units_seen": list(units_seen),
        "first_period_end": min(ends) if ends else None,
        "last_period_end": max(ends) if ends else None,
    }


def ingest_entity_shares(conn: sqlite3.Connection, entity_id: int, cik: Any, *,
                         cache_dir: Optional[str] = None,
                         next_session: Optional[Callable[[str], Optional[str]]] = None,
                         concepts: Sequence[str] = DEFAULT_INGEST_CONCEPTS,
                         use_acceptance: bool = True,
                         verify_with_companyfacts: bool = True) -> dict[str, Any]:
    """Fetch and store every share observation this issuer ever published.

    The cheap route first: one companyconcept call per concept, plus -- when
    `use_acceptance` is on -- one submissions call for the acceptance
    timestamps. Six small requests per issuer, throttled through
    `pit_identity`'s shared SEC session and cached.

    Then the correction. companyconcept returns an EMPTY HTTP 200 for some
    issuers: Coca-Cola's cover count, issued count and weighted average all come
    back as `{"units": {"shares": {}}}` while its companyfacts document holds
    71, 144 and 233 rows of those same tags. So when the cheap route yields no
    point-in-time count, `verify_with_companyfacts` fetches companyfacts ONCE
    and harvests the five concepts from it. That request is heavy -- 5.0 MB for
    Coca-Cola against 10-35 KB per concept -- which is exactly why it is a
    fallback and not the default, and it is only ever paid for the issuers the
    cheap route failed. Rows found that way are stamped `sec:companyfacts`, so
    provenance always says which route produced a number.

    Negative results are recorded in `pit_share_ingest` rather than dropped: a
    404 means the issuer never tagged the concept, and a coverage measurement
    that cannot see the 404s will quietly divide by the survivors.

    Returns a report dict. Never raises for a missing issuer.
    """
    padded = cik10(cik)
    stamp = _now()
    accepted_map: dict[str, Any] = {}
    submissions_status = 0
    if use_acceptance:
        index = acceptance_index(padded, cache_dir)
        accepted_map = index["accepted"]
        submissions_status = index["status"]

    report: dict[str, Any] = {
        "entity_id": entity_id,
        "cik": padded,
        "submissions_status": submissions_status,
        "concepts": {},
        "companyfacts_fallback": None,
        "rows_written": 0,
        "rows_seen": 0,
        "rows_rejected": 0,
        "acceptance_matched": 0,
        "empty_200": [],
        "notes": [],
    }

    def accumulate(key: str, result: dict[str, Any]) -> None:
        report["concepts"][key] = result
        report["rows_seen"] += result["rows_seen"]
        report["rows_written"] += result["rows_written"]
        report["rows_rejected"] += result["rows_rejected"]
        report["acceptance_matched"] += result["acceptance_matched"]

    for key in concepts:
        concept = concept_for(key)
        fetched = fetch_share_concept(padded, concept, cache_dir)
        if fetched["status"] == 200 and fetched["empty_200"]:
            report["empty_200"].append(key)
        accumulate(key, _store_concept(
            conn, entity_id, padded, concept, fetched["rows"],
            status=fetched["status"], source=SOURCE_COMPANYCONCEPT, stamp=stamp,
            accepted_map=accepted_map, next_session=next_session,
            units_seen=fetched["units_seen"]))

    def pit_rows() -> int:
        return sum(report["concepts"].get(k, {}).get("rows_valid", 0)
                   for k in SHARES_LADDER_WITH_ISSUED
                   if k in report["concepts"])

    if verify_with_companyfacts and pit_rows() == 0:
        facts = fetch_companyfacts_shares(padded, cache_dir)
        report["companyfacts_fallback"] = {"http_status": facts["status"],
                                           "concepts_found": sorted(facts["by_concept"])}
        for key in concepts:
            raw_rows = facts["by_concept"].get(key) or []
            if not raw_rows:
                continue
            result = _store_concept(
                conn, entity_id, padded, concept_for(key), raw_rows,
                status=facts["status"], source=SOURCE_COMPANYFACTS, stamp=stamp,
                accepted_map=accepted_map, next_session=next_session)
            # Keep the better of the two routes in the headline report, and say
            # which one it was. The other route's row stays in pit_share_ingest.
            if result["rows_valid"] > report["concepts"].get(key, {}).get("rows_valid", 0):
                previous = report["concepts"].get(key, {})
                report["concepts"][key] = result
                report["rows_seen"] += result["rows_seen"] - previous.get("rows_seen", 0)
                report["rows_written"] += result["rows_written"]
                report["rows_rejected"] += result["rows_rejected"]
                report["acceptance_matched"] += result["acceptance_matched"]
        if pit_rows() > 0:
            report["notes"].append("recovered_via_companyfacts")

    statuses = [report["concepts"][k]["http_status"] for k in report["concepts"]]
    if statuses and all(status == 404 for status in statuses):
        # Every concept 404s. Almost always a pre-XBRL issuer: the filer died,
        # or stopped filing, before XBRL tagging began around 2009.
        report["notes"].append("no_xbrl_share_concepts")
    cover = report["concepts"].get(CONCEPT_DEI_COVER, {}).get("rows_valid", 0)
    balance = report["concepts"].get(CONCEPT_GAAP_OUTSTANDING, {}).get("rows_valid", 0)
    average = report["concepts"].get(CONCEPT_WEIGHTED_DILUTED, {}).get("rows_valid", 0)
    if cover == 0 and balance > 0:
        report["notes"].append("cover_page_absent_balance_sheet_only")
    if average > 0 and cover == 0 and balance == 0:
        report["notes"].append("period_average_only")
    if report["empty_200"]:
        report["notes"].append("empty_200_from_companyconcept")
    return report


# ==========================================================================
# SELECTION -- the point-in-time share count
# ==========================================================================

def _select_one(conn: sqlite3.Connection, entity_id: int, concept_key: str,
                as_of: str) -> Optional[sqlite3.Row]:
    """The newest vintage of the newest measurement knowable on `as_of`.

    Two bounds, and neither implies the other:

      period_end    <= as_of   the count must be OF a date that has happened
      available_date <= as_of  the filing carrying it must have landed

    `ORDER BY period_end DESC` first, then `available_date DESC` within that
    measurement date, is what makes the split case come out right: at a
    2020-01-31 as-of Apple's newest instant with an available filing is
    2019-09-28, and the only vintage of it published by then is the pre-split
    4,443,236,000. The 17,772,945,000 restatement filed 2020-10-30 is invisible
    -- not filtered out afterwards, but never in scope.
    """
    return conn.execute(
        """SELECT * FROM pit_share_obs
            WHERE entity_id = ? AND concept_key = ?
              AND period_end <= ? AND available_date <= ?
            ORDER BY period_end DESC, available_date DESC, filed DESC, accn DESC
            LIMIT 1""",
        (entity_id, concept_key, as_of, as_of),
    ).fetchone()


def shares_as_of_detail(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                        ladder: Sequence[str] = SHARES_LADDER_STRICT,
                        enforce_staleness: bool = True) -> dict[str, Any]:
    """The share count knowable on `as_of`, WITH the reasoning. Always a record.

    Walks `ladder` in order and returns the first concept that yields a fresh
    contemporaneous observation. The ladder contains only point-in-time
    concepts; `WeightedAverageNumberOfDilutedSharesOutstanding` is not in it and
    cannot be put in it -- a rung whose concept is not `point_in_time` raises,
    because the failure this module exists to prevent is a period average
    silently standing in for a count.

    The record always carries `available`, `reason`, `concept_key`, `tag`,
    `accn`, `available_date`, `age_days` (as_of minus the measurement date --
    how old the count is AT USE TIME) and `price_basis`. When nothing resolves,
    `reason` says which of the four distinct failures it was:

      never_filed_a_share_count   the issuer never tagged any of these concepts
      not_yet_filed               it did, but not by this date
      stale_beyond_max_age        the newest count is older than the bound
      only_period_average_available  the ONLY thing filed is the weighted
                                  average, which is not a count. Substituting
                                  it would be a wrong number, so the answer is
                                  no number.

    `enforce_staleness` off is for diagnostics only. It returns counts older
    than `pit_policy`'s bound with `stale=True` set, and no scorer should use it.
    """
    as_of = str(as_of)[:10]
    record: dict[str, Any] = {
        "entity_id": entity_id,
        "as_of": as_of,
        "available": False,
        "reason": None,
        "shares": None,
        "concept_key": None,
        "tag": None,
        "measure_kind": None,
        "measured_at": None,
        "accn": None,
        "form": None,
        "filed": None,
        "available_date": None,
        "availability_rule": None,
        "age_days": None,
        "reporting_lag_days": None,
        "stale": False,
        "zero_count": False,
        "upper_bound": False,
        "price_basis": PRICE_BASIS_RAW,
        "ladder": list(ladder),
        "ladder_trace": [],
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
    }

    stale_seen = False
    future_seen = False
    for key in ladder:
        concept = concept_for(key)
        if not concept.point_in_time:
            raise ValueError(
                f"{key} is {concept.measure_kind}, not a point-in-time count; it "
                "may never appear in a shares ladder")
        row = _select_one(conn, entity_id, key, as_of)
        if row is None:
            later = conn.execute(
                """SELECT COUNT(*) AS n FROM pit_share_obs
                    WHERE entity_id = ? AND concept_key = ?""",
                (entity_id, key),
            ).fetchone()
            has_any = bool(later and later["n"])
            future_seen = future_seen or has_any
            record["ladder_trace"].append(
                {"concept_key": key, "outcome": "not_yet_filed" if has_any else "no_rows"})
            continue
        stale = pit_policy.is_stale(row["period_end"], as_of, int(row["qtrs"]),
                                    STALENESS_CONCEPT)
        if stale and enforce_staleness:
            stale_seen = True
            record["ladder_trace"].append(
                {"concept_key": key, "outcome": "stale",
                 "measured_at": row["period_end"],
                 "age_days": _days_between(row["period_end"], as_of)})
            continue

        record.update({
            "available": True,
            "reason": None,
            "shares": float(row["val"]),
            "concept_key": row["concept_key"],
            "tag": row["tag"],
            "measure_kind": row["measure_kind"],
            "measured_at": row["period_end"],
            "accn": row["accn"],
            "form": row["form"],
            "filed": row["filed"],
            "available_date": row["available_date"],
            "availability_rule": row["availability_rule"],
            "age_days": _days_between(row["period_end"], as_of),
            "reporting_lag_days": _days_between(row["period_end"], row["available_date"]),
            "stale": bool(stale),
            "zero_count": float(row["val"]) == 0.0,
            "upper_bound": concept.upper_bound,
            "price_basis": row["price_basis"],
        })
        record["ladder_trace"].append({"concept_key": key, "outcome": "selected"})
        if record["zero_count"]:
            # A real, correctly selected zero -- Motors Liquidation Co, CIK
            # 40730, reports exactly this from 2012. It is returned because it
            # is true, and flagged because a $0 market cap would read as
            # infinitely cheap to a valuation percentile.
            record["warning"] = REASON_ZERO_SHARE_COUNT
        return record

    if stale_seen:
        record["reason"] = REASON_STALE
    elif future_seen:
        record["reason"] = REASON_NOT_YET_FILED
    else:
        record["reason"] = _absence_reason(conn, entity_id)
    return record


def _absence_reason(conn: sqlite3.Connection, entity_id: int) -> str:
    """Why this issuer has no point-in-time count AT ALL.

    Distinguishes the case the brief singles out: an issuer whose only share
    concept is the weighted-average diluted count. That is not a partial
    answer to be patched with a substitution -- a period average is a different
    measurement -- so the reason has its own name and the count stays absent.
    """
    rows = conn.execute(
        """SELECT concept_key, COUNT(*) AS n FROM pit_share_obs
            WHERE entity_id = ? GROUP BY concept_key""",
        (entity_id,),
    ).fetchall()
    present = {r["concept_key"] for r in rows if r["n"]}
    if not present:
        return REASON_NEVER_FILED
    if present == {CONCEPT_WEIGHTED_DILUTED}:
        return REASON_ONLY_PERIOD_AVERAGE
    if present == {CONCEPT_PUBLIC_FLOAT}:
        return REASON_ONLY_PUBLIC_FLOAT
    if not (present & set(SHARES_LADDER_WITH_ISSUED)):
        return REASON_ONLY_PERIOD_AVERAGE
    return REASON_NEVER_FILED


def shares_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                 ladder: Sequence[str] = SHARES_LADDER_STRICT,
                 enforce_staleness: bool = True) -> Optional[dict[str, Any]]:
    """The point-in-time share count, or None. See `shares_as_of_detail`.

    None is a first-class answer and means the valuation factor is unavailable
    for this entity at this date. It is never a guess, never today's count and
    never a weighted average wearing a count's label.
    """
    record = shares_as_of_detail(conn, entity_id, as_of, ladder=ladder,
                                 enforce_staleness=enforce_staleness)
    return record if record["available"] else None


# ==========================================================================
# PRICE BASIS AND SPLITS
# ==========================================================================

def price_basis_for(observation: dict[str, Any]) -> dict[str, Any]:
    """Which price series this share observation may legally be multiplied by.

    The answer is always the RAW, AS-PRINTED, UN-ADJUSTED close, because a
    point-in-time share count is expressed in the share basis of its own era and
    a retroactively adjusted price is expressed in today's. Pairing them is the
    error that produces a correct-looking 8x -- or, for Apple's 4:1, a
    correct-looking 4x.

    Two distinct windows matter and this helper names both:

      before the count date   Splits before `measured_at` are already baked into
                              the count as published. Nothing to do.
      (count date, as-of]     A split here leaves the count in the OLD basis
                              while the raw price has already moved to the new
                              one. This window must be un-applied, and
                              `market_cap_as_of` will say whether it was checked.

    Takes a record from `shares_as_of_detail` and returns a statement, not a
    price: this module holds no price data and will not pretend to.
    """
    measured_at = observation.get("measured_at")
    return {
        "required_price_basis": PRICE_BASIS_RAW,
        "refused_price_bases": [PRICE_BASIS_SPLIT_ADJUSTED, PRICE_BASIS_TOTAL_RETURN],
        "share_basis_era": measured_at,
        "splits_already_in_the_count": f"on or before {measured_at}",
        "split_window_to_un_apply": (
            f"({measured_at}, {observation.get('as_of')}]"
            if measured_at else None),
        "why": ("Share counts are restated retroactively for splits and so are "
                "adjusted price series -- but to DIFFERENT eras. Apple's "
                "2019-09-28 count was 4,443,236,000 in four filings and "
                "17,772,945,000 in the fifth; a point-in-time selector returns "
                "the pre-split number at a pre-split as-of, which is right, and "
                "which makes today's split-adjusted price the wrong multiplier."),
        "yahoo_note": ("Yahoo's `adjclose` is restated by every later split AND "
                       "dividend; its `close` is split-adjusted. Neither is a raw "
                       "price. pit_price_bar stores both plus the ingest that "
                       "produced them, and a raw level has to be reconstructed by "
                       "un-applying the splits after the bar date."),
    }


def split_factor_between(conn: sqlite3.Connection, listing_id: Optional[int],
                         after: str, through: str) -> dict[str, Any]:
    """Cumulative share-count multiplier for splits in the window (after, through].

    Reads `pit_corporate_action` and honours `applies_to_shares`, which exists
    precisely because a Yahoo split event is not always a split: AT&T's
    "1324:1000" on 2022-04-11 is a spin-off, valid as a price adjustment and
    wrong by 32.4% as a share-count divisor. Rows flagged price-only are
    ignored here.

    Returns {'factor', 'status', 'events'}. `status` is the honest part:

      checked             the corporate-action table was consulted and it holds
                          events for this listing
      no_events_recorded  consulted, and empty for this listing -- which is
                          AMBIGUOUS between "no splits happened" and "splits were
                          never ingested", and is reported as such rather than
                          being rounded down to "no splits happened"
      unchecked           no listing_id was supplied; nothing was consulted
    """
    if listing_id is None:
        return {"factor": 1.0, "status": SPLIT_UNCHECKED, "events": []}
    try:
        rows = conn.execute(
            """SELECT event_date, event_type, ratio_num, ratio_den
                 FROM pit_corporate_action
                WHERE listing_id = ? AND applies_to_shares = 1
                  AND event_date > ? AND event_date <= ?
                ORDER BY event_date""",
            (listing_id, str(after)[:10], str(through)[:10]),
        ).fetchall()
        any_row = conn.execute(
            "SELECT COUNT(*) AS n FROM pit_corporate_action WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        return {"factor": 1.0, "status": SPLIT_UNCHECKED, "events": [],
                "error": str(exc)}

    factor = 1.0
    events: list[dict[str, Any]] = []
    for row in rows:
        num, den = row["ratio_num"], row["ratio_den"]
        if not num or not den:
            continue
        factor *= float(num) / float(den)
        events.append({"event_date": row["event_date"], "event_type": row["event_type"],
                       "ratio": f"{num}:{den}"})
    status = SPLIT_CHECKED if (any_row and any_row["n"]) else SPLIT_NO_EVENTS_RECORDED
    return {"factor": factor, "status": status, "events": events}


# ==========================================================================
# MARKET CAP
# ==========================================================================

def market_cap_as_of_detail(conn: sqlite3.Connection, entity_id: int, as_of: str,
                            raw_price: Optional[float], *,
                            listing_id: Optional[int] = None,
                            price_basis: str = PRICE_BASIS_RAW,
                            price_source: str = "caller",
                            split_factor: Optional[float] = None,
                            ladder: Sequence[str] = SHARES_LADDER_STRICT,
                            allow_zero_shares: bool = False,
                            require_split_check: bool = False) -> dict[str, Any]:
    """MarketCap = raw price x point-in-time share count, with full provenance.

    `raw_price` must be the price AS PRINTED on `as_of` -- the level a trader
    would have seen, not a retroactively adjusted one. `price_basis` is a
    required declaration and a non-raw basis is REFUSED rather than silently
    corrected: this module cannot un-adjust a price it was handed, and a market
    cap computed from an adjusted series is wrong by every split since, which is
    the exact failure the module was written to stop.

    The share count is brought into the as-of date's share basis by
    `split_factor` -- the cumulative multiplier for splits in the window
    (measured_at, as_of]. Supply it, or supply a `listing_id` and let
    `split_factor_between` read `pit_corporate_action`. Whichever happened is
    recorded in `split_check`, and `require_split_check=True` refuses to return
    a number the window was never verified for. Worked example of why the window
    matters: at a 2020-09-30 as-of the newest Apple cover count available is
    4,275,634,000 measured 2020-07-17, before the 4:1 split of 2020-08-31, while
    the raw close of $115.81 is after it. Unadjusted the product is $495bn
    against a truth near $1.98tn.

    Returns a record whose `market_cap` is None whenever anything is missing,
    with `reason` naming which. A None market cap means the valuation factor is
    unavailable and the frozen production rule applies -- drop it, renormalise
    the rest over 0.60 -- which `valuation_dropped_weights()` spells out.
    """
    as_of = str(as_of)[:10]
    record: dict[str, Any] = {
        "entity_id": entity_id,
        "as_of": as_of,
        "market_cap": None,
        "reason": None,
        "raw_price": raw_price,
        "price_basis": price_basis,
        "price_source": price_source,
        "shares_used": None,
        "shares_as_published": None,
        "split_factor": None,
        "split_check": None,
        "split_events": [],
        "shares": None,
        "valuation_factor": "unavailable",
        "effective_weights_if_dropped": dict(VALUATION_DROPPED_WEIGHTS),
    }

    if price_basis not in ADMISSIBLE_PRICE_BASES:
        record["reason"] = REASON_PRICE_BASIS_REFUSED
        record["detail"] = (
            f"price_basis {price_basis!r} is not raw. A point-in-time share count "
            "is in its own era's share basis; multiplying it by a retroactively "
            "adjusted price is wrong by every split since. Supply an as-printed "
            "close, or un-apply the adjustments first.")
        return record

    shares = shares_as_of_detail(conn, entity_id, as_of, ladder=ladder)
    record["shares"] = shares
    if not shares["available"]:
        record["reason"] = shares["reason"]
        return record

    if shares["zero_count"] and not allow_zero_shares:
        record["reason"] = REASON_ZERO_SHARE_COUNT
        record["detail"] = (
            "the share count is a genuine, correctly selected zero -- CIK 40730, "
            "Motors Liquidation Co, reports exactly this -- and a $0 market cap "
            "would rank as infinitely cheap in a valuation percentile.")
        return record

    if raw_price is None:
        record["reason"] = REASON_NO_PRICE
        return record
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        record["reason"] = REASON_NO_PRICE
        return record
    if price <= 0:
        record["reason"] = REASON_NO_PRICE
        return record

    if split_factor is not None:
        splits = {"factor": float(split_factor), "status": SPLIT_CALLER_SUPPLIED,
                  "events": []}
    else:
        splits = split_factor_between(conn, listing_id, shares["measured_at"], as_of)
    record["split_factor"] = splits["factor"]
    record["split_check"] = splits["status"]
    record["split_events"] = splits["events"]

    if require_split_check and splits["status"] in (SPLIT_UNCHECKED,
                                                    SPLIT_NO_EVENTS_RECORDED):
        record["reason"] = REASON_SPLIT_UNRESOLVED
        record["detail"] = (
            f"the split window ({shares['measured_at']}, {as_of}] was "
            f"{splits['status']}; a split inside it moves the answer by the whole "
            "split ratio.")
        return record

    adjusted = shares["shares"] * splits["factor"]
    record.update({
        "shares_as_published": shares["shares"],
        "shares_used": adjusted,
        "market_cap": adjusted * price,
        "valuation_factor": "available",
        "concept_key": shares["concept_key"],
        "tag": shares["tag"],
        "accn": shares["accn"],
        "measured_at": shares["measured_at"],
        "available_date": shares["available_date"],
        "age_days": shares["age_days"],
        "upper_bound": shares["upper_bound"],
        "price_basis_required": price_basis_for(shares),
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
    })
    return record


def market_cap_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str,
                     raw_price: Optional[float], **options: Any
                     ) -> Optional[dict[str, Any]]:
    """The market cap with its provenance, or None. See `market_cap_as_of_detail`.

    None is never a zero and never an estimate. It means the valuation factor --
    40% of the equity Shaffer Score -- is unavailable for this entity on this
    date, and that the frozen drop-and-renormalise rule applies.
    """
    record = market_cap_as_of_detail(conn, entity_id, as_of, raw_price, **options)
    return record if record["market_cap"] is not None else None


# ==========================================================================
# MEASUREMENT
# ==========================================================================

#: How recently an issuer must have published ANY share-shaped fact to count as
#: still reporting. 400 days is a fiscal year plus a filing lag plus slack -- a
#: filer that has published nothing in thirteen months has stopped.
REPORTING_WINDOW_DAYS = 400


def is_reporting_at(conn: sqlite3.Connection, entity_id: int, as_of: str,
                    window_days: int = REPORTING_WINDOW_DAYS) -> bool:
    """Was this issuer still filing on `as_of`, by its own share-shaped facts?

    Deliberately NOT circular with the point-in-time ladder: it accepts ANY of
    the five concepts, including the weighted average and the public float,
    neither of which can ever answer the share-count question. So Meta -- which
    files the weighted average and nothing else usable -- counts as reporting
    and as a coverage FAILURE, while Bed Bath & Beyond in 2024 counts as
    neither. That separation is the whole point: "no count because the company
    is gone" and "no count because the tags are dimensioned" are different
    findings and must not be averaged together.
    """
    floor = (_dt.date.fromisoformat(str(as_of)[:10])
             - _dt.timedelta(days=window_days)).isoformat()
    row = conn.execute(
        """SELECT 1 FROM pit_share_obs
            WHERE entity_id = ? AND period_end <= ? AND period_end >= ?
              AND available_date <= ?
            LIMIT 1""",
        (entity_id, str(as_of)[:10], floor, str(as_of)[:10]),
    ).fetchone()
    return row is not None


def coverage_report(conn: sqlite3.Connection, entity_ids: Sequence[int],
                    as_of_dates: Sequence[str], *,
                    ladder: Sequence[str] = SHARES_LADDER_STRICT,
                    cohorts: Optional[dict[int, str]] = None) -> dict[str, Any]:
    """Measure what the share complement actually buys, per as-of date.

    Answers, for a sample of entities and a set of as-of dates: what share had a
    usable contemporaneous count, which concept won, how old the winning count
    was, what the reporting lag looked like, and -- the one the brief singles
    out -- how often the ONLY thing on file was the weighted-average count, so
    that the honest answer is "unavailable" rather than a substitution.

    Every percentage is over the WHOLE sample, survivors and the dead cohort
    together. Dividing by the issuers that happen to have data is how a coverage
    number becomes survivorship bias with a decimal point.
    """
    cohorts = cohorts or {}
    out: dict[str, Any] = {
        "n_entities": len(entity_ids),
        "ladder": list(ladder),
        "as_of": {},
        "concept_wins": {},
        "reasons": {},
        "reporting_lag_days": {},
        "age_at_use_days": {},
    }
    lag_all: list[float] = []
    age_all: list[float] = []

    for as_of in as_of_dates:
        wins: dict[str, int] = {}
        reasons: dict[str, int] = {}
        lags: list[float] = []
        ages: list[float] = []
        by_cohort: dict[str, dict[str, int]] = {}
        available = 0
        zero_counts = 0
        reporting = 0
        reporting_available = 0
        reporting_reasons: dict[str, int] = {}

        for entity_id in entity_ids:
            record = shares_as_of_detail(conn, entity_id, as_of, ladder=ladder)
            cohort = cohorts.get(entity_id, "all")
            bucket = by_cohort.setdefault(cohort, {"n": 0, "available": 0})
            bucket["n"] += 1
            live = is_reporting_at(conn, entity_id, as_of)
            if live:
                reporting += 1
            if record["available"]:
                available += 1
                bucket["available"] += 1
                if live:
                    reporting_available += 1
                wins[record["concept_key"]] = wins.get(record["concept_key"], 0) + 1
                lags.append(record["reporting_lag_days"])
                ages.append(record["age_days"])
                if record["zero_count"]:
                    zero_counts += 1
            else:
                reasons[record["reason"]] = reasons.get(record["reason"], 0) + 1
                if live:
                    reporting_reasons[record["reason"]] = (
                        reporting_reasons.get(record["reason"], 0) + 1)

        lag_all.extend(lags)
        age_all.extend(ages)
        out["as_of"][as_of] = {
            "n": len(entity_ids),
            "available": available,
            "pct_available": round(100.0 * available / len(entity_ids), 1) if entity_ids else 0.0,
            "n_reporting": reporting,
            "reporting_available": reporting_available,
            "pct_available_among_reporting": (
                round(100.0 * reporting_available / reporting, 1) if reporting else 0.0),
            "reporting_reasons": dict(sorted(reporting_reasons.items())),
            "zero_counts": zero_counts,
            "concept_wins": dict(sorted(wins.items())),
            "reasons": dict(sorted(reasons.items())),
            "reporting_lag_days": _percentiles(lags),
            "age_at_use_days": _percentiles(ages),
            "by_cohort": {k: {**v,
                              "pct": round(100.0 * v["available"] / v["n"], 1) if v["n"] else 0.0}
                          for k, v in sorted(by_cohort.items())},
        }
        for key, count in wins.items():
            out["concept_wins"][key] = out["concept_wins"].get(key, 0) + count
        for key, count in reasons.items():
            out["reasons"][key] = out["reasons"].get(key, 0) + count

    out["reporting_lag_days"] = _percentiles(lag_all)
    out["age_at_use_days"] = _percentiles(age_all)
    total_attempts = len(entity_ids) * len(as_of_dates)
    got = sum(out["as_of"][d]["available"] for d in as_of_dates)
    reporting_total = sum(out["as_of"][d]["n_reporting"] for d in as_of_dates)
    reporting_got = sum(out["as_of"][d]["reporting_available"] for d in as_of_dates)
    out["pct_available_overall"] = (
        round(100.0 * got / total_attempts, 1) if total_attempts else 0.0)
    out["pct_available_among_reporting"] = (
        round(100.0 * reporting_got / reporting_total, 1) if reporting_total else 0.0)
    out["valuation_retained_projection"] = {
        "pct_of_all_entity_dates": out["pct_available_overall"],
        "pct_of_reporting_entity_dates": out["pct_available_among_reporting"],
        "which_to_quote": ("the REPORTING figure. A scored candidate is by "
                           "definition an issuer still filing on the as-of date, "
                           "so an entity that had already stopped filing was never "
                           "a candidate and its absence is correct behaviour, not "
                           "missing coverage."),
        "weights_when_retained": {"valuation": 0.40, "growth": 0.25,
                                  "profitability": 0.20, "debt": 0.15},
        "weights_when_dropped": dict(VALUATION_DROPPED_WEIGHTS),
        "caveat": ("An upper bound on the scored cross-section. A share count is "
                   "necessary for market cap and not sufficient for the valuation "
                   "factor, which also needs a raw price on the date and an EBITDA "
                   "peer cohort -- operating income caps that at ~85%."),
    }
    return out


# ==========================================================================
# PILOT CLI
# ==========================================================================

#: A purposive pilot sample: mega caps, ordinary mid caps, the awkward
#: multi-class and holding-company structures, and the dead cohort -- including
#: four issuers that died before XBRL and therefore CANNOT have a count. The
#: dead are over-weighted on purpose: a coverage number measured on survivors
#: alone is the bias this whole store exists to avoid.
PILOT_SAMPLE: tuple[tuple[str, str, str], ...] = (
    ("320193", "AAPL", "mega"),
    ("789019", "MSFT", "mega"),
    ("1018724", "AMZN", "mega"),
    ("1652044", "GOOGL", "mega_multiclass"),
    ("1326801", "META", "mega_multiclass"),
    ("1045810", "NVDA", "mega"),
    ("1318605", "TSLA", "mega"),
    ("1067983", "BRK", "mega_multiclass"),
    ("19617", "JPM", "mega_financial"),
    ("200406", "JNJ", "mega"),
    ("34088", "XOM", "mega"),
    ("104169", "WMT", "mega"),
    ("80424", "PG", "mega"),
    ("21344", "KO", "mega"),
    ("93410", "CVX", "mega"),
    ("78003", "PFE", "mega"),
    ("50863", "INTC", "mega"),
    ("858877", "CSCO", "mega"),
    ("1341439", "ORCL", "mega"),
    ("732712", "VZ", "mega"),
    ("732717", "T", "mega"),
    ("1065280", "NFLX", "large"),
    ("40545", "GE", "large"),
    ("1744489", "DIS", "large"),
    ("1637459", "KHC", "large"),
    ("109380", "ZION", "mid_financial"),
    ("29534", "DG", "mid"),
    ("4977", "AFL", "mid_financial"),
    ("91419", "SJM", "mid"),
    ("1099590", "MELI", "mid"),
    ("814361", "TBL", "mid_dead_acquired"),
    ("1326380", "GME", "small"),
    ("1411579", "AMC", "small"),
    ("1639825", "PTON", "small"),
    ("84129", "RAD", "small_distressed"),
    ("47129", "HTZ", "small_distressed"),
    ("895126", "CHK", "small_distressed"),
    ("1004980", "PCG", "mid_distressed"),
    ("885590", "BHC", "mid_distressed"),
    ("931336", "DF", "small_dead"),
    ("96289", "RSH", "small_dead"),
    ("1166126", "JCP", "small_dead"),
    ("1005414", "TOY", "small_dead"),
    ("886158", "BBBY", "dead"),
    ("1310067", "SHLD", "dead"),
    ("719739", "SIVB", "dead"),
    ("718877", "ATVI", "dead_acquired"),
    ("1418091", "TWTR", "dead_acquired"),
    ("1110783", "MON", "dead_acquired"),
    ("1105705", "TWX", "dead_acquired"),
    ("101830", "S", "dead_acquired"),
    ("1467858", "GM", "large"),
    ("40730", "GM_OLD", "dead_pre_xbrl"),
    ("806085", "LEH", "dead_pre_xbrl"),
    ("1024401", "ENRN", "dead_pre_xbrl"),
    # CIK 933136 is one legal chain with four names: Washington Mutual, Inc. ->
    # WMI Holdings -> Mr. Cooper Group -> Maverick Merger Sub 2, LLC. A bank
    # that failed in 2008 and a mortgage servicer acquired in 2025 share an
    # entity_id, which is exactly what CIK-keyed identity is for.
    ("933136", "WAMU_CHAIN", "dead_pre_xbrl"),
    ("777001", "BSC", "dead_pre_xbrl"),
    ("25191", "CFC", "dead_pre_xbrl"),
    ("36995", "WB", "dead_pre_xbrl"),
    ("1132979", "FRC", "dead_no_10k"),
)

#: As-of dates for the pilot: one pre-ASC-606 mid-cycle date, one late-cycle
#: date before the 2020 split wave, one recent. All three are NYSE sessions.
PILOT_AS_OF: tuple[str, ...] = ("2015-06-30", "2019-06-28", "2024-06-28")

DEFAULT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "shafferfineval_pit_shares_cache")


def _seed_calendar(conn: sqlite3.Connection, source_db: Optional[str],
                   market: str = "XNYS") -> str:
    """Copy `pit_calendar` from an existing store, READ-ONLY, into `conn`.

    A pilot convenience and nothing more. The availability policy needs a
    session calendar to resolve a weekend availability date onto the next
    session; the production store already holds 8,467 sessions and rebuilding
    them here would be a second source of truth. Opened with `mode=ro` so a
    running ingest holding the write lock is never disturbed, and failure is
    reported rather than raised -- without a calendar the policy falls back to
    the calendar day and flags `session_resolved=False`.
    """
    if not source_db or not os.path.exists(source_db):
        return "no_source"
    try:
        src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True, timeout=5.0)
        src.row_factory = sqlite3.Row
        sessions = [r["session_date"] for r in src.execute(
            "SELECT session_date FROM pit_calendar WHERE market = ?", (market,))]
        src.close()
    except sqlite3.Error as exc:
        return f"unavailable: {exc}"
    if not sessions:
        return "empty"
    pit_store.insert_calendar(conn, market, sessions, "copied:read_only")
    return f"copied {len(sessions)} sessions"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the pilot ingest and print the coverage measurement as JSON."""
    parser = argparse.ArgumentParser(description="Point-in-time share count pilot.")
    parser.add_argument("--db", default=os.path.join(
        tempfile.gettempdir(), "pit_shares_pilot", "pit_shares_pilot.db"),
        help="throwaway database to write (never the production PIT store)")
    parser.add_argument("--calendar-from", default=pit_store.DEFAULT_PIT_DB_PATH,
                        help="existing store to copy pit_calendar from, read-only")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--limit", type=int, default=0,
                        help="only the first N sample issuers")
    parser.add_argument("--no-ingest", action="store_true",
                        help="measure an already-populated database")
    parser.add_argument("--out", default="", help="write the report JSON here")
    args = parser.parse_args(argv)

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    os.makedirs(args.cache_dir, exist_ok=True)
    conn = pit_store.init_db(args.db)
    ensure_schema(conn)
    calendar_status = _seed_calendar(conn, args.calendar_from)
    next_session = None
    try:
        import pit_dera
        next_session = pit_dera.session_resolver(conn)
    except Exception as exc:                        # pragma: no cover - pilot only
        print(f"session resolver unavailable: {exc}", file=sys.stderr)

    sample = PILOT_SAMPLE[:args.limit] if args.limit else PILOT_SAMPLE
    cohorts: dict[int, str] = {}
    labels: dict[int, str] = {}
    entity_ids: list[int] = []
    reports: list[dict[str, Any]] = []

    for cik, label, cohort in sample:
        entity_id = pit_store.upsert_entity(conn, cik)
        entity_ids.append(entity_id)
        cohorts[entity_id] = cohort
        labels[entity_id] = label
        if args.no_ingest:
            continue
        report = ingest_entity_shares(conn, entity_id, cik,
                                      cache_dir=args.cache_dir,
                                      next_session=next_session)
        report["label"] = label
        report["cohort"] = cohort
        reports.append(report)
        print(f"{label:8s} CIK {cik:>10s}  rows={report['rows_written']:5d}  "
              f"notes={','.join(report['notes']) or '-'}", file=sys.stderr)

    coverage = coverage_report(conn, entity_ids, PILOT_AS_OF, cohorts=cohorts)
    payload = {
        "calendar": calendar_status,
        "session_resolver": next_session is not None,
        "db": os.path.abspath(args.db),
        "user_agent": SEC_USER_AGENT,
        "spec": concept_spec(),
        "ingest": reports,
        "coverage": coverage,
    }
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
