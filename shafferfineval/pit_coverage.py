"""Block-level coverage of the candidate v2 factors, by sector and by distress.

READ-ONLY against the store. Nothing here writes a row, opens a write
transaction, or touches `pit_feature`, `pit_score` or `pit_replay_run`: this
module answers "what could the candidate score even be computed on, and for
whom does it go missing" -- a question about the DATA, not a replay of the
model. The official historical Shaffer Score replay is not written and is not
run here.

Three questions, one pass over the facts:

  (1) COVERAGE BY GROUP. For every candidate v2 sub-factor that needs no market
      capitalisation, the share of the base universe it can actually be
      computed for, cut by SIC division, size, profitability, distress state,
      revenue status and company age.

  (2) MISSINGNESS AS A DATA-GENERATING PROCESS. P(feature available | company
      characteristics). If availability is not independent of those
      characteristics -- and it is not -- then an availability flag is a
      legitimate ML input and is NOT a correction for selection bias. A flag
      tells the model that a value is missing; it does not restore the
      companies whose values went missing in a way correlated with the outcome.

  (3) THE HEADLINE. The share of OPERATING-COMPANY observations that can
      support all four blocks of the candidate score at once, as things stand
      and with the share-count gap closed. Financials (SIC 60-67) are excluded
      from the operating-company model by design, so the denominator is stated
      rather than assumed.

THE SIZE PROXY IS TOTAL ASSETS, NOT MARKET CAPITALISATION, and the substitution
is not cosmetic. Market cap needs a point-in-time share count, which exists for
25.35% / 35.20% / 41.35% of peers at the three measurement dates -- so cutting
coverage by market-cap decile would condition on the very quantity whose
absence is the headline finding, and would report the coverage of the covered.
Assets resolve for 100% of the base by construction (the base is defined by
having them), which is the only size measure available on the whole
denominator.

ANY RESULT LINKING AVAILABILITY TO RETURNS IS SURVIVOR_ONLY_DIAGNOSTIC. The
price sample behind `pit_label` is 2,574 listings every one of which is alive
in 2026. A return difference measured on it answers "among survivors", never
"in the market", and every such row carries the label in its own field so a
reader who never opens this docstring still cannot mistake it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
from typing import Any, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pit_policy
import pit_store

#: The three measurement dates every other number in this project is quoted at.
#: Mid-year, so an annual filer's most recent 10-K is roughly six months old --
#: the ordinary case, not the best or the worst one.
MEASURED_DATES = ("2015-06-30", "2019-06-28", "2024-06-28")

#: The sample-scope label every return-linked row carries. Imported, never
#: redeclared, so it cannot drift from the label set's own vocabulary.
SURVIVOR_ONLY = pit_store.SAMPLE_SURVIVOR_ONLY

#: How far back the fact index reaches. Acceleration needs EBITDA two years
#: before the as-of date, and a filer whose period ends in March reports it up
#: to fifteen months later, so a three-year window is the minimum that does not
#: manufacture a missing value out of a short window.
INDEX_WINDOW_DAYS = 1170

#: How close a lagged period must sit to its target. "A year earlier" means the
#: matching fiscal period, and fiscal calendars move by a few days a year
#: (52/53-week filers) without the period changing meaning.
LAG_TOLERANCE_DAYS = 45

#: The smallest cohort from which a 50-75 benchmark margin may be computed.
#: From pit_derive: below three members the band is one company and the
#: "benchmark" is that company's own margin.
MIN_EBITDA_COHORT = 3

#: The stored peer ladder, mirrored rather than imported so a read-only census
#: does not drag pit_peers (and its price helpers) in. test_pit_coverage
#: asserts the two agree.
RUNG_LADDER = ("sic4", "sic3", "sic2", "office")
MIN_COHORT_N = 12


# ==========================================================================
# (1) CHARACTERISTICS -- how a company is described before its data is judged
# ==========================================================================

#: SIC divisions, as the ranges the classification is built from. The two-digit
#: prefix is the only part stable across an era: a 4-digit code is re-pointed
#: by the SEC without the company changing business.
SIC_DIVISIONS: tuple[tuple[str, int, int, str], ...] = (
    ("A", 1, 9, "Agriculture, Forestry, Fishing"),
    ("B", 10, 14, "Mining"),
    ("C", 15, 17, "Construction"),
    ("D", 20, 39, "Manufacturing"),
    ("E", 40, 49, "Transportation & Public Utilities"),
    ("F", 50, 51, "Wholesale Trade"),
    ("G", 52, 59, "Retail Trade"),
    ("H", 60, 67, "Finance, Insurance, Real Estate"),
    ("I", 70, 89, "Services"),
    ("J", 91, 99, "Public Administration"),
)

DIVISION_UNKNOWN = "?"
DIVISION_FINANCE = "H"

#: SIC 6770, "blank checks". Reported as its own bucket rather than inside
#: division H: a blank-check shell has no operations by definition, and letting
#: it sit in finance makes finance's coverage look worse for a reason that has
#: nothing to do with banking.
SIC_BLANK_CHECK = "6770"


def sic_division(sic: Optional[str]) -> str:
    """The division letter for a SIC code, or '?' when it cannot be read.

    Returns a letter, never a guess: an entity with no SIC filed by the as-of
    date is '?', which is a real state (a first-time filer) and is reported as
    its own row rather than dropped into the largest division.
    """
    if not sic:
        return DIVISION_UNKNOWN
    text = str(sic).strip()
    if len(text) < 2 or not text[:2].isdigit():
        return DIVISION_UNKNOWN
    major = int(text[:2])
    for letter, lo, hi, _name in SIC_DIVISIONS:
        if lo <= major <= hi:
            return letter
    return DIVISION_UNKNOWN


def division_name(letter: str) -> str:
    for code, _lo, _hi, name in SIC_DIVISIONS:
        if code == letter:
            return name
    return "Unclassified"


def is_financial(sic: Optional[str]) -> bool:
    """SIC 60-67. The operating-company model excludes these by design.

    EBITDA is not defined for a depository -- interest expense is an operating
    cost there, not a financing one -- so a bank scoring badly on an EBITDA
    block is the model being asked the wrong question, not the bank being
    unprofitable.
    """
    return sic_division(sic) == DIVISION_FINANCE


#: Distress flags. Each is a DIFFERENT observable and each can be unknown
#: independently of the others, so they are reported as three flags plus an
#: explicit unknown rather than as one boolean.
DISTRESS_NEGATIVE_EQUITY = "negative_equity"
DISTRESS_NEGATIVE_OPERATING_INCOME = "negative_operating_income"
DISTRESS_INTEREST_EXCEEDS_OPERATING_INCOME = "interest_exceeds_operating_income"
DISTRESS_FLAGS = (DISTRESS_NEGATIVE_EQUITY,
                  DISTRESS_NEGATIVE_OPERATING_INCOME,
                  DISTRESS_INTEREST_EXCEEDS_OPERATING_INCOME)

DISTRESS_UNKNOWN = "unknown"
DISTRESS_NONE = "none"
DISTRESS_YES = "distressed"


def distress_state(equity: Optional[float], operating_income: Optional[float],
                   interest_expense: Optional[float]) -> dict[str, Any]:
    """Distress, defined from what this store actually holds.

    THE DEFINITION, stated because there is no standard one and a reader must
    be able to disagree with it precisely:

      negative_equity                     equity < 0 (book insolvency)
      negative_operating_income           operating income < 0
      interest_exceeds_operating_income   interest expense > operating income,
                                          i.e. interest coverage below 1x

    Deliberately NOT negative EBITDA: EBITDA needs depreciation, the scarcest
    of the inputs, so a negative-EBITDA definition would make "distressed" and
    "has a D&A tag" the same variable and would guarantee the correlation this
    census exists to measure. Operating income is the same economic signal one
    line higher and is nine points better covered.

    A company none of whose three inputs is available is UNKNOWN, never "not
    distressed". That distinction is the whole reason this returns a record:
    treating unknown as healthy is how a missing-data problem is laundered into
    a finding about solvency.

    READ THE DISTRESS CUT WITH THIS IN MIND. Two of the three flags need
    OPERATING INCOME, which is also the EBITDA numerator, so a company can only
    be called "distressed" if it filed the tag that EBITDA needs -- while
    "none" can be reached on the equity tag alone. That is why the distressed
    bucket measures HIGHER EBITDA coverage than the healthy one (56.6% against
    47.0% at 2024-06-28), and it is a conditioning artefact of the definition,
    not evidence that failing companies file better. The cut that is free of it
    is `operating_income_sign`, where both signs condition on the same tag:
    there, coverage is 76.1% for positive operating income against 58.0% for
    negative, and the real gradient is visible.
    """
    flags: list[str] = []
    known = 0
    if equity is not None:
        known += 1
        if equity < 0.0:
            flags.append(DISTRESS_NEGATIVE_EQUITY)
    if operating_income is not None:
        known += 1
        if operating_income < 0.0:
            flags.append(DISTRESS_NEGATIVE_OPERATING_INCOME)
    if operating_income is not None and interest_expense is not None:
        known += 1
        if interest_expense > operating_income:
            flags.append(DISTRESS_INTEREST_EXCEEDS_OPERATING_INCOME)
    if known == 0:
        state = DISTRESS_UNKNOWN
    elif flags:
        state = DISTRESS_YES
    else:
        state = DISTRESS_NONE
    return {"state": state, "flags": tuple(flags), "n_flags": len(flags),
            "n_inputs_known": known}


#: Revenue states. "No revenue tag" and "a tagged zero" are different facts --
#: pit_store says so for every concept -- and a pre-revenue biotech is a real
#: company whose growth factors cannot exist, not a data error.
REVENUE_NO_TAG = "no_revenue_tag"
REVENUE_ZERO = "zero_revenue"
REVENUE_NEAR_ZERO = "near_zero_under_1m"
REVENUE_NORMAL = "revenue_1m_or_more"


def revenue_state(revenue: Optional[float]) -> str:
    if revenue is None:
        return REVENUE_NO_TAG
    if revenue <= 0.0:
        return REVENUE_ZERO
    if revenue < 1_000_000.0:
        return REVENUE_NEAR_ZERO
    return REVENUE_NORMAL


AGE_BUCKETS = ((0.0, 2.0, "0-2y"), (2.0, 5.0, "2-5y"), (5.0, 10.0, "5-10y"),
               (10.0, 20.0, "10-20y"), (20.0, 1e4, "20y+"))


def age_bucket(first_filing_date: Optional[str], as_of: str) -> str:
    """Company age IN THE STORE, which is not company age.

    `pit_entity.first_filing_date` is the first filing this store ingested, so
    an issuer that listed in 1960 is "20y+" only because EDGAR begins in 1993.
    The bucket is honest about what it measures, and the young buckets carry
    the real information: a 0-2y entity genuinely has no lagged fundamentals,
    whatever its true age.
    """
    if not first_filing_date:
        return "unknown"
    try:
        start = _dt.date.fromisoformat(str(first_filing_date)[:10])
        day = _dt.date.fromisoformat(str(as_of)[:10])
    except (TypeError, ValueError):
        return "unknown"
    years = (day - start).days / 365.25
    if years < 0:
        return "unknown"
    for lo, hi, label in AGE_BUCKETS:
        if lo <= years < hi:
            return label
    return "20y+"


def quantile_cuts(values: Sequence[float], n_buckets: int = 5) -> list[float]:
    """Cut points for equal-count buckets. Stdlib, no numpy, no interpolation.

    Ties are left where they fall: a bucket may hold more than 1/n of the
    sample when many companies share a value, which is the truthful behaviour
    for a variable with mass points.
    """
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return []
    cuts: list[float] = []
    for index in range(1, n_buckets):
        position = int(round(index * len(ordered) / float(n_buckets)))
        cuts.append(ordered[max(0, min(len(ordered) - 1, position))])
    return cuts


def bucket_of(value: Optional[float], cuts: Sequence[float],
              labels: Sequence[str]) -> str:
    if value is None:
        return "unknown"
    index = 0
    while index < len(cuts) and value >= cuts[index]:
        index += 1
    return labels[min(index, len(labels) - 1)]


SIZE_LABELS = ("Q1_smallest", "Q2", "Q3", "Q4", "Q5_largest")


def sign_label(value: Optional[float], positive: str, negative: str) -> str:
    """A sign, with 'unknown' as a first-class third state.

    Conditioning coverage on a sign that is itself missing 30% of the time is
    how a coverage table silently becomes a table about the covered; the
    unknown row keeps the denominator whole.
    """
    if value is None:
        return "unknown"
    return positive if value >= 0.0 else negative


# ==========================================================================
# (2) THE FACT INDEX -- one read-only pass, one as-of date at a time
# ==========================================================================

#: Every tag the census can consult, derived from the ladders themselves so a
#: ladder edit cannot silently narrow the scan. Deriving it is the point: a
#: hand-maintained tag list is how a census quietly stops measuring the rung
#: someone added last week.
def census_tags(ladder_version: Optional[str] = None) -> tuple[str, ...]:
    tags = set(pit_policy.ladder_tags(ladder_version or pit_store.LADDER_VERSION))
    tags |= set(pit_policy.ladder_tags(pit_store.LADDER_VERSION_V2))
    return tuple(sorted(tags))


def tag_units(ladder_version: Optional[str] = None) -> dict[str, str]:
    """tag -> the unit its ladder declares. The reason this exists:

    `Assets` is filed in twenty currencies -- 827,312 USD rows and 5,612 in
    CNY, CAD, JPY and fifteen others. A census that ignores the unit counts a
    foreign-currency balance sheet as a usable total-assets fact and silently
    inflates the base universe (measured: +44 / +36 / +36 companies, 0.6%).
    A number in yen is not a number in dollars, and the ladder says which one
    the concept means.
    """
    units: dict[str, str] = {}
    for version in (ladder_version or pit_store.LADDER_VERSION,
                    pit_store.LADDER_VERSION_V2):
        for ladder in pit_policy.ladder_set(version).values():
            for rung in ladder.rungs:
                for tag in rung.tags:
                    units[tag] = ladder.unit
    return units


def _day(value: Any) -> _dt.date:
    return _dt.date.fromisoformat(str(value)[:10])


def _shift(day: _dt.date, days: int) -> str:
    return (day + _dt.timedelta(days=days)).isoformat()


def load_fact_index(conn: sqlite3.Connection, as_of: str,
                    entity_ids: Iterable[int],
                    *, window_days: int = INDEX_WINDOW_DAYS,
                    ladder_version: Optional[str] = None
                    ) -> dict[tuple[int, str, int], dict[str, float]]:
    """Every ladder fact for these entities, knowable on `as_of`, in memory.

    Returns {(entity_id, tag, qtrs): {period_end: value}} where `value` is the
    LATEST VINTAGE available on `as_of` -- the restatement a reader could have
    seen then, which is the same rule `pit_store.fact_as_of` applies one fact at
    a time. Doing it in bulk is the only way this census is affordable: 7,000
    entities times 13 concepts times three lagged periods is a quarter of a
    million point lookups, and the per-fact selector is an indexed query each.

    ONE AS-OF AT A TIME, deliberately. The three windows together are ~3M rows;
    held at once that is a working set large enough to matter on a box whose OS
    has already killed a process for memory. The caller is expected to let each
    index fall out of scope before building the next.

    Dimensional rows are excluded here exactly as `fact_as_of` excludes them:
    47% of DERA rows are segment or co-registrant rows, and a segment's revenue
    substituted for the company's corrupts the census with no error.
    """
    day = _day(as_of)
    low = _shift(day, -int(window_days))
    high = day.isoformat()
    wanted = set(int(e) for e in entity_ids)
    tags = census_tags(ladder_version)
    units = tag_units(ladder_version)
    placeholders = ", ".join("?" * len(tags))
    sql = f"""SELECT entity_id, tag, qtrs, period_end, val, available_date, filed,
                     unit
                FROM pit_fact
               WHERE period_end BETWEEN ? AND ?
                 AND available_date <= ?
                 AND segments = '' AND coreg = ''
                 AND qtrs IN (0, 4)
                 AND unit IN ('USD', 'shares')
                 AND tag IN ({placeholders})"""
    # Values are carried as (available_date, filed, val) WHILE SCANNING so the
    # latest vintage can be chosen without a second million-entry dictionary,
    # then flattened per key. A parallel stamp dict would double the working
    # set of the largest structure this census builds.
    staged: dict[tuple[int, str, int], dict[str, tuple[str, str, float]]] = {}
    intern: dict[str, str] = {}
    for row in conn.execute(sql, [low, high, high, *tags]):
        entity_id = row[0]
        if entity_id not in wanted:
            continue
        if row[7] != units.get(row[1]):
            continue                      # a balance sheet in yen is not one in dollars
        tag = intern.setdefault(row[1], row[1])
        period_end = intern.setdefault(row[3], row[3])
        cell = staged.setdefault((entity_id, tag, int(row[2])), {})
        seen = cell.get(period_end)
        if seen is None or (row[5], row[6]) > (seen[0], seen[1]):
            cell[period_end] = (row[5], row[6], row[4])
    index: dict[tuple[int, str, int], dict[str, float]] = {}
    for key in list(staged):
        index[key] = {p: float(v[2]) for p, v in staged.pop(key).items()}
    return index


def rung_periods(index: Mapping[tuple[int, str, int], Mapping[str, float]],
                 entity_id: int, rung_key: Optional[str], qtrs: int
                 ) -> dict[str, float]:
    """Every period this entity reported ON ONE RUNG. The growth map.

    A growth rate must come from the same rung in both years: subtracting
    SalesRevenueNet from Revenues is a measurement discontinuity wearing the
    shape of a business change, which is what `pit_feature.source_tag_changed`
    exists to flag. Composite (summed) rungs have no single tag and are not
    differenced here; they return nothing rather than a partial sum.
    """
    if not rung_key or rung_key.startswith("SUM("):
        return {}
    return dict(index.get((entity_id, rung_key, qtrs)) or {})


def concept_periods(index: Mapping[tuple[int, str, int], Mapping[str, float]],
                    entity_id: int, tags: Sequence[str], qtrs: int
                    ) -> dict[str, float]:
    """The ladder resolved SEPARATELY AT EVERY PERIOD: first tag wins per year.

    Different from `periods_for` on purpose, and the difference is the
    difference between a level and a growth rate. A LEVEL (the margin's
    revenue, say) should take the best rung available for that year -- a filer
    who moved from SalesRevenueNet to Revenues still has a revenue in both. A
    GROWTH RATE must not: subtracting one rung from another is a measurement
    discontinuity wearing the shape of a business change, which is exactly
    what `pit_feature.source_tag_changed` exists to flag.
    """
    out: dict[str, float] = {}
    for tag in tags:
        found = index.get((entity_id, tag, qtrs))
        if not found:
            continue
        for period, value in found.items():
            out.setdefault(period, float(value))
    return out


def resolve_concept(index: Mapping[tuple[int, str, int], Mapping[str, float]],
                    entity_id: int, concept: str, as_of: str,
                    ladders: Mapping[str, Any]
                    ) -> tuple[Optional[str], Optional[str], Optional[float]]:
    """(rung_key, period_end, value) for one concept, or (None, None, None).

    The ladder is walked in order and the FIRST rung with a non-stale fact
    wins; a rung whose newest period is stale does not fall back to an older
    period of the same rung, because an older period of the same tag is the
    same stale number. Staleness is `pit_policy.is_stale`, so this census and
    the replay that will one day run agree by construction rather than by
    coincidence.
    """
    ladder = ladders[concept]
    qtrs = 0 if ladder.period_kind == pit_policy.PERIOD_INSTANT else 4
    for rung in ladder.rungs:
        if not rung.eligible(as_of):
            continue
        if rung.combine == pit_policy.COMBINE_SUM:
            maps = [index.get((entity_id, tag, qtrs)) for tag in rung.tags]
            if not all(maps):
                continue
            common = set(maps[0])
            for other in maps[1:]:
                common &= set(other)
            if not common:
                continue
            period = max(common)
            if pit_policy.is_stale(period, as_of, qtrs, concept):
                continue
            return rung.key, period, sum(float(m[period]) for m in maps)
        found = index.get((entity_id, rung.tags[0], qtrs))
        if not found:
            continue
        period = max(found)
        if pit_policy.is_stale(period, as_of, qtrs, concept):
            continue
        return rung.key, period, float(found[period])
    return None, None, None


def nearest_period(candidates: Iterable[str], target_period: str, years_back: int,
                   tolerance_days: int = LAG_TOLERANCE_DAYS) -> Optional[str]:
    """The period `years_back` years before `target_period`, within tolerance.

    A calendar year, not four quarters counted: `qtrs = 4` rows ARE annual, so
    the lag is the matching fiscal period one year earlier. Nothing is returned
    when no period lands close enough -- a transition period or a changed
    fiscal year-end means the comparison does not exist, and inventing the
    nearest available period would silently compare 9 months with 12.
    """
    try:
        anchor = _day(target_period)
    except (TypeError, ValueError):
        return None
    target = _dt.date(anchor.year - years_back, anchor.month,
                      min(anchor.day, 28))
    best: Optional[str] = None
    best_gap = tolerance_days + 1
    for period in candidates:
        try:
            gap = abs((_day(period) - target).days)
        except (TypeError, ValueError):
            continue
        if gap <= tolerance_days and gap < best_gap:
            best, best_gap = period, gap
    return best


# ==========================================================================
# (3) THE CANDIDATE v2 SUB-FACTORS, as availability questions
# ==========================================================================

#: The sub-factors measured here, and the block each belongs to. Every one of
#: them is computable WITHOUT a market capitalisation -- that is the selection
#: rule for this table, and it is why valuation appears only in the headline
#: projection, where its absence is the finding.
FEATURE_BLOCK: dict[str, str] = {
    "ebitda_level": "E",
    "ebitda_scale": "E",
    "ebitda_margin": "E",
    "ebitda_growth": "E",
    "ebitda_acceleration": "E",
    "cohort_benchmark_50_75": "E",
    "excess_efficiency_level": "E",
    "revenue_growth_nominal": "G",
    "revenue_growth_real": "G",
    "quality_roa": "Q",
    "quality_fcf_conversion": "Q",
    "quality_interest_coverage": "Q",
    "quality_leverage_v2": "Q",
}

FEATURE_KEYS = tuple(FEATURE_BLOCK)

FEATURE_NOTE: dict[str, str] = {
    "ebitda_level": "operating income + D&A at a COMMON period end, non-stale",
    "ebitda_scale": "the same assembly, ranked in dollars -- identical coverage "
                    "to ebitda_level by construction, listed so the block's "
                    "sub-factor count is not quietly one short",
    "ebitda_margin": "ebitda / revenue, revenue required at the SAME period end",
    "ebitda_growth": "(E_t - E_t-1y) / |E_t-1y|, base policy "
                     "pit_factor_spec.EBITDA_GROWTH_BASE_POLICY_V1 (availability "
                     "counts the two observations, not the base test)",
    "ebitda_acceleration": "growth_t - growth_t-1y: three EBITDA observations, "
                           "no asset base",
    "cohort_benchmark_50_75": f"a stored cohort with >= {MIN_EBITDA_COHORT} "
                              "members carrying both EBITDA and revenue",
    "excess_efficiency_level": "own margin AND the cohort benchmark; the "
                               "BENCHMARK_ANCHORED form, so the bar survives "
                               "into the score",
    "revenue_growth_nominal": "revenue_t and revenue_t-1y on the same rung",
    "revenue_growth_real": "nominal growth deflated over the GROWTH WINDOW by "
                           "the CPI vintage readable on the as-of date",
    "quality_roa": "net income / total assets",
    "quality_fcf_conversion": "(operating cash flow - capex) / EBITDA",
    "quality_interest_coverage": "operating income / interest expense at the "
                                 "SAME (period_end, qtrs); interest expense > 0 "
                                 "(owner policy 2026-09-22); negative operating "
                                 "income allowed",
    "quality_leverage_v2": "total debt (concept_ladder_v2) / total assets -- "
                           "see total_debt_bound_mix: most of v2's recovery is "
                           "a LOWER_BOUND rung, which is coverage of a "
                           "different quantity than the factor names",
}


def _ebitda_periods(index: Mapping[tuple[int, str, int], Mapping[str, float]],
                    entity_id: int, ladders: Mapping[str, Any]
                    ) -> dict[str, float]:
    """Every period for which EBITDA can be ASSEMBLED, with its value.

    The assembly rule is pit_policy's and is not relaxed here: both components
    share a period end and a duration, because a quarterly operating income
    added to an annual D&A is not EBITDA. D&A walks its own ladder per period,
    so a filer that switched from DepreciationDepletionAndAmortization to
    DepreciationAndAmortization mid-history keeps both years.
    """
    operating = index.get((entity_id, "OperatingIncomeLoss", 4)) or {}
    if not operating:
        return {}
    da_tags = [rung.tags[0] for rung in ladders["depreciation_amortisation"].rungs]
    out: dict[str, float] = {}
    for period, value in operating.items():
        for tag in da_tags:
            found = index.get((entity_id, tag, 4))
            if found and period in found:
                out[period] = float(value) + float(found[period])
                break
    return out


def cpi_index_as_of(conn: sqlite3.Connection, reference_date: str, as_of: str,
                    series_id: str = "CPIAUCSL") -> Optional[float]:
    """The CPI level for `reference_date` AS PUBLISHED on `as_of`.

    Both bounds, for the reason `pit_store.macro_value_as_of` gives: a row
    whose available_date is in the past can still carry a vintage from the
    future, and a deflator taken from today's revised series is a leak wearing
    a macro hat.
    """
    row = conn.execute(
        """SELECT value FROM pit_macro_obs
            WHERE series_id = ? AND obs_date <= ?
              AND vintage_date <= ? AND available_date <= ?
            ORDER BY obs_date DESC, vintage_date DESC LIMIT 1""",
        (series_id, str(reference_date)[:10], str(as_of)[:10], str(as_of)[:10]),
    ).fetchone()
    return None if row is None or row[0] is None else float(row[0])


# ==========================================================================
# (4) ONE CROSS-SECTION, MEASURED
# ==========================================================================

def stored_cohorts(conn: sqlite3.Connection, as_of: str,
                   model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION
                   ) -> dict[int, dict[str, Any]]:
    """entity_id -> the cohort it is actually ranked in, from the STORED sets.

    Walks the same ladder `pit_peers.peer_set_for_entity` walks -- finest rung
    first, a set below MIN_COHORT_N skipped -- but resolves the whole
    cross-section in two queries instead of eight thousand. Reading the stored
    sets rather than rebuilding them is deliberate: a census that re-derives
    the cohorts measures its own rebuild, not the cohorts the replay would use.
    """
    sets = {int(r["peer_set_id"]): (r["rung"], r["key"], int(r["n_members"]))
            for r in conn.execute(
                """SELECT peer_set_id, rung, key, n_members FROM pit_peer_set
                    WHERE as_of_date = ? AND model_version = ?""",
                (str(as_of)[:10], model_version))}
    if not sets:
        return {}
    order = {rung: position for position, rung in enumerate(RUNG_LADDER)}
    best: dict[int, dict[str, Any]] = {}
    members: dict[int, list[int]] = {}
    for set_id, entity_id in conn.execute(
            """SELECT m.peer_set_id, m.entity_id
                 FROM pit_peer_member m JOIN pit_peer_set s
                   ON s.peer_set_id = m.peer_set_id
                WHERE s.as_of_date = ? AND s.model_version = ?""",
            (str(as_of)[:10], model_version)):
        info = sets.get(int(set_id))
        if info is None or info[2] < MIN_COHORT_N:
            continue
        members.setdefault(int(set_id), []).append(int(entity_id))
        rank = order.get(info[0], len(order))
        current = best.get(int(entity_id))
        if current is None or rank < current["rank"]:
            best[int(entity_id)] = {"peer_set_id": int(set_id), "rung": info[0],
                                    "key": info[1], "n_members": info[2],
                                    "rank": rank}
    for record in best.values():
        record["members"] = members.get(record["peer_set_id"], [])
    return best


def measure_as_of(conn: sqlite3.Connection, as_of: str, *,
                  ladder_version: str = pit_store.LADDER_VERSION_V2,
                  model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION
                  ) -> dict[str, Any]:
    """The whole cross-section, as one record per base-universe company.

    READ-ONLY and short-lived: every query here is a SELECT, and the connection
    is expected to be opened `mode=ro`. No transaction is held across the
    Python work, because a long read snapshot blocks the checkpointer and a
    blocked checkpointer is how a 9.3 GB WAL once filled this volume.

    The denominator is the BASE UNIVERSE -- peers with a usable, non-stale
    total-assets fact -- because a company with no balance sheet at all cannot
    be scored by any model and counting it would flatter nothing but the
    excuse. The peer count and the base count are both returned, so the gap
    between them stays visible.
    """
    import pit_identity                       # local: heavy, and only needed here

    day = str(as_of)[:10]
    ladders = pit_policy.ladder_set(ladder_version)
    peers = pit_identity.peer_universe_as_of(conn, day)
    peer_ids = [int(r["entity_id"]) for r in peers]
    sic_of = {int(r["entity_id"]): r["sic"] for r in peers}
    first_filing = {int(r[0]): r[1] for r in conn.execute(
        "SELECT entity_id, first_filing_date FROM pit_entity")}
    index = load_fact_index(conn, day, peer_ids, ladder_version=ladder_version)

    # --- pass 1: resolve every entity's own facts -------------------------
    raw: dict[int, dict[str, Any]] = {}
    for entity_id in peer_ids:
        ebitda_periods = _ebitda_periods(index, entity_id, ladders)
        record: dict[str, Any] = {"entity_id": entity_id,
                                  "ebitda_periods": ebitda_periods}
        for concept in ("total_assets", "revenue", "net_income", "equity",
                        "operating_income", "operating_cash_flow", "capex",
                        "interest_expense", "total_debt", "shares_outstanding"):
            rung, period, value = resolve_concept(index, entity_id, concept,
                                                  day, ladders)
            record[concept] = value
            record[concept + "_period"] = period
            record[concept + "_rung"] = rung
        # EBITDA's own period must pass the same staleness test the concepts do.
        current = None
        if ebitda_periods:
            newest = max(ebitda_periods)
            if not pit_policy.is_stale(newest, day, 4, "operating_income"):
                current = newest
        record["ebitda_period"] = current
        record["ebitda"] = ebitda_periods.get(current) if current else None
        raw[entity_id] = record

    base_ids = [e for e in peer_ids if raw[e]["total_assets"] is not None]
    cohorts = stored_cohorts(conn, day, model_version)

    # --- pass 2: margins, for the entity and for its cohort ---------------
    margin_of: dict[int, float] = {}
    for entity_id in peer_ids:
        record = raw[entity_id]
        period = record["ebitda_period"]
        if not period:
            continue
        # A LEVEL: the ladder is resolved at the EBITDA period itself, because a
        # margin is a ratio of two numbers from one year, not a growth rate.
        revenue_at = concept_periods(
            index, entity_id,
            [r.tags[0] for r in ladders["revenue"].rungs if r.eligible(day)],
            4).get(period)
        record["revenue_at_ebitda_period"] = revenue_at
        if revenue_at:
            margin_of[entity_id] = float(record["ebitda"]) / float(revenue_at)
    cohort_margin_n: dict[int, int] = {}
    for record in cohorts.values():
        set_id = record["peer_set_id"]
        if set_id not in cohort_margin_n:
            cohort_margin_n[set_id] = sum(
                1 for member in record["members"] if member in margin_of)

    # --- pass 3: the availability record per base company -----------------
    cpi_cache: dict[str, Optional[float]] = {}

    def cpi(reference: Optional[str]) -> Optional[float]:
        if not reference:
            return None
        if reference not in cpi_cache:
            cpi_cache[reference] = cpi_index_as_of(conn, reference, day)
        return cpi_cache[reference]

    priced = {int(r["entity_id"])
              for r in pit_identity.scored_universe_as_of(conn, day)}
    assets_values = [raw[e]["total_assets"] for e in base_ids]
    cuts = quantile_cuts(assets_values, len(SIZE_LABELS))

    rows: list[dict[str, Any]] = []
    for entity_id in base_ids:
        record = raw[entity_id]
        periods = record["ebitda_periods"]
        current = record["ebitda_period"]
        lag1 = nearest_period(periods, current, 1) if current else None
        lag2 = nearest_period(periods, current, 2) if current else None
        # A GROWTH RATE: the SAME rung in both years. The resolved rung's own
        # tag map, not the ladder merged per period -- a filer who moved from
        # SalesRevenueNet to Revenues has a discontinuity, and a discontinuity
        # differenced against itself is not growth.
        revenue_map = rung_periods(index, entity_id, record["revenue_rung"], 4)
        revenue_period = record["revenue_period"]
        revenue_lag1 = (nearest_period(revenue_map, revenue_period, 1)
                        if revenue_period else None)
        margin = margin_of.get(entity_id)
        cohort = cohorts.get(entity_id)
        benchmark_ok = bool(cohort
                            and cohort_margin_n.get(cohort["peer_set_id"], 0)
                            >= MIN_EBITDA_COHORT)

        features = {
            "ebitda_level": record["ebitda"] is not None,
            "ebitda_scale": record["ebitda"] is not None,
            "ebitda_margin": margin is not None,
            "ebitda_growth": bool(current and lag1),
            "ebitda_acceleration": bool(current and lag1 and lag2),
            "cohort_benchmark_50_75": benchmark_ok,
            "excess_efficiency_level": bool(benchmark_ok and margin is not None),
            "revenue_growth_nominal": bool(revenue_period and revenue_lag1
                                           and revenue_map.get(revenue_lag1)),
            "revenue_growth_real": False,
            "quality_roa": bool(record["net_income"] is not None
                                and record["total_assets"]),
            "quality_fcf_conversion": bool(record["operating_cash_flow"] is not None
                                           and record["capex"] is not None
                                           and record["ebitda"]),
            # the census approximates the engine's rule (a LEVEL at the OI
            # period across rungs) with the resolved rung's newest period
            "quality_interest_coverage": bool(
                record["operating_income"] is not None
                and record["interest_expense"] is not None
                and record["interest_expense"] > 0.0
                and record["interest_expense_period"]
                == record["operating_income_period"]),
            "quality_leverage_v2": bool(record["total_debt"] is not None
                                        and record["total_assets"]),
        }
        if features["revenue_growth_nominal"]:
            features["revenue_growth_real"] = bool(cpi(revenue_period)
                                                   and cpi(revenue_lag1))

        # WHAT the debt number actually measured. v2 recovers coverage largely
        # through LOWER_BOUND rungs -- long-term debt standing in for total
        # debt -- and a leverage leg counted as "available" on a lower bound is
        # available for a different quantity than the one the factor names.
        debt_bound = pit_policy.quantity_for(
            "total_debt", record["total_debt_rung"] or "", ladder_version)

        distress = distress_state(record["equity"], record["operating_income"],
                                  record["interest_expense"])
        sic = sic_of.get(entity_id)
        shares_ok = record["shares_outstanding"] is not None
        price_ok = entity_id in priced
        rows.append({
            "entity_id": entity_id,
            "sic": sic,
            "division": (SIC_BLANK_CHECK if str(sic or "") == SIC_BLANK_CHECK
                         else sic_division(sic)),
            "is_financial": is_financial(sic),
            "size_bucket": bucket_of(record["total_assets"], cuts, SIZE_LABELS),
            "total_assets": record["total_assets"],
            "net_income_sign": sign_label(record["net_income"],
                                          "net_income_positive",
                                          "net_income_negative"),
            "ebitda_sign": sign_label(record["ebitda"], "ebitda_positive",
                                      "ebitda_negative"),
            "operating_income_sign": sign_label(record["operating_income"],
                                                "oi_positive", "oi_negative"),
            "distress": distress["state"],
            "distress_flags": list(distress["flags"]),
            "n_distress_flags": distress["n_flags"],
            "revenue_state": revenue_state(record["revenue"]),
            "age_bucket": age_bucket(first_filing.get(entity_id), day),
            "features": features,
            "total_debt_bound": (debt_bound["bound"] or "unknown_or_v1_row"
                                 if record["total_debt"] is not None else None),
            "shares_ok": shares_ok,
            "price_ok": price_ok,
            "valuation_ok": bool(shares_ok and price_ok),
        })
    return {"as_of": day, "ladder_version": ladder_version,
            "n_peers": len(peer_ids), "n_base": len(rows),
            "n_cohorts": len(set(c["peer_set_id"] for c in cohorts.values())),
            "size_cuts": cuts, "rows": rows}


# ==========================================================================
# (5) TABULATION -- P(feature available | characteristic)
# ==========================================================================

#: Characteristics the coverage tables are cut by. Order matters only for
#: reading; each is measured against the same denominator.
CHARACTERISTICS = ("division", "size_bucket", "net_income_sign", "ebitda_sign",
                   "operating_income_sign", "distress", "revenue_state",
                   "age_bucket")

#: Below this many companies a group's rate is printed but never compared: a
#: three-company division swinging 0-100% is noise wearing a percentage sign.
MIN_GROUP_N = 30


def tabulate(rows: Sequence[Mapping[str, Any]], characteristic: str,
             feature_key: str) -> dict[str, Any]:
    """P(feature available | characteristic), with the counts that made it.

    Returns the rate per group AND the group size, because a rate without its
    denominator is the single easiest way to publish a coverage number that
    cannot be checked.
    """
    groups: dict[str, list[int]] = {}
    for row in rows:
        bucket = str(row.get(characteristic))
        available = 1 if row["features"].get(feature_key) else 0
        slot = groups.setdefault(bucket, [0, 0])
        slot[0] += available
        slot[1] += 1
    out = {name: {"n_available": hit, "n": total,
                  "pct": (100.0 * hit / total) if total else 0.0}
           for name, (hit, total) in sorted(groups.items())}
    usable = [v["pct"] for v in out.values() if v["n"] >= MIN_GROUP_N]
    total_n = sum(v["n"] for v in out.values())
    total_hit = sum(v["n_available"] for v in out.values())
    return {"characteristic": characteristic, "feature": feature_key,
            "overall_pct": (100.0 * total_hit / total_n) if total_n else 0.0,
            "n": total_n,
            "spread_pct": (max(usable) - min(usable)) if len(usable) > 1 else None,
            "groups": out}


def missingness_summary(rows: Sequence[Mapping[str, Any]],
                        features: Sequence[str] = FEATURE_KEYS,
                        characteristics: Sequence[str] = CHARACTERISTICS
                        ) -> dict[str, Any]:
    """How far availability is from independent of company characteristics.

    The spread -- the widest gap in availability between two groups of at least
    MIN_GROUP_N companies -- is the whole finding in one number per cut. Under
    MCAR (missing completely at random) every spread would be sampling noise
    around zero. They are not, and that is the measured statement behind the
    owner's rule: AN AVAILABILITY FLAG IS A LEGITIMATE ML INPUT AND IS NOT A
    CORRECTION FOR SELECTION BIAS. The flag lets a model condition on the fact
    that a number is missing; it cannot put back the companies whose numbers
    are missing for reasons that also move the outcome.
    """
    out: dict[str, Any] = {}
    for feature in features:
        per_characteristic = {}
        for characteristic in characteristics:
            table = tabulate(rows, characteristic, feature)
            per_characteristic[characteristic] = {
                "spread_pct": table["spread_pct"],
                "groups": {name: round(value["pct"], 2)
                           for name, value in table["groups"].items()
                           if value["n"] >= MIN_GROUP_N},
            }
        overall = tabulate(rows, "division", feature)["overall_pct"]
        widest = max((v["spread_pct"] or 0.0)
                     for v in per_characteristic.values())
        out[feature] = {"overall_pct": overall, "widest_spread_pct": widest,
                        "by_characteristic": per_characteristic}
    return out


# ==========================================================================
# (6) DOES AVAILABILITY PREDICT THE OUTCOME? -- SURVIVOR_ONLY_DIAGNOSTIC
# ==========================================================================

#: The measured intraclass correlation of 12M forward returns (2026-09-21:
#: ICC 0.0743, design effect 174.8 over 355,727 rows -> 160.7 independent
#: observations). It is carried here because a cross-sectional return gap
#: quoted against n = 2,000 is an overstatement by more than an order of
#: magnitude, and the correction belongs beside the number, not in a footnote.
RETURN_ICC = 0.0743


def effective_n(n: int, icc: float = RETURN_ICC) -> float:
    """Independent observations inside one correlated cross-section.

    n / (1 + (n - 1) * ICC), the standard design-effect deflation, applied with
    the whole cross-section treated as ONE cluster -- which it is: every
    company at a single as-of date shares the same market, so the returns are
    not 2,000 independent draws and never were.
    """
    if n <= 1:
        return float(n)
    return n / (1.0 + (n - 1) * icc)


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def forward_returns(conn: sqlite3.Connection, as_of: str,
                    horizon: str = "12M") -> dict[int, float]:
    """entity_id -> forward return, from the SURVIVOR-ONLY price sample.

    The sample is 2,574 listings, every one alive in 2026. This function is
    named for what it returns and labelled for what it is: nothing computed
    from it may promote a model, and every row derived from it carries
    `sample_scope = SURVIVOR_ONLY_DIAGNOSTIC` in its own field.

    A censored row, or one whose terminal reason is OUTCOME_UNKNOWN, is
    DROPPED rather than filled. There is no imputation here -- not -100%, not
    zero, not a carried-forward last price.
    """
    out: dict[int, float] = {}
    for row in conn.execute(
            """SELECT lab.entity_id, lab.forward_return, lab.censored,
                      lab.terminal_reason
                 FROM pit_label lab
                WHERE lab.as_of_date = ? AND lab.horizon = ?""",
            (str(as_of)[:10], horizon)):
        entity_id, value, censored, reason = row
        if entity_id is None or value is None:
            continue
        if censored or reason == pit_store.OUTCOME_UNKNOWN:
            continue
        out.setdefault(int(entity_id), float(value))
    return out


def availability_vs_returns(rows: Sequence[Mapping[str, Any]],
                            returns: Mapping[int, float],
                            features: Sequence[str] = FEATURE_KEYS
                            ) -> dict[str, Any]:
    """Does whether a feature EXISTS predict the forward return?

    If it does, availability is not ignorable and the missingness is not MAR
    conditional on the observed characteristics alone -- which is the fact that
    an availability flag cannot repair. The flag is still a legitimate model
    input; it is simply not a correction.

    SURVIVOR_ONLY_DIAGNOSTIC, in the output and in every stored row: the
    comparison is between two groups of SURVIVORS, so a gap here is a lower
    bound on the real one in exactly the direction that flatters the data.
    """
    out: dict[str, Any] = {"sample_scope": SURVIVOR_ONLY,
                           "may_promote_a_model": False,
                           "n_rows_with_return": 0, "features": {}}
    linked = [(row, returns[row["entity_id"]]) for row in rows
              if row["entity_id"] in returns]
    out["n_rows_with_return"] = len(linked)
    out["effective_independent_n"] = round(effective_n(len(linked)), 1)
    for feature in features:
        have = [value for row, value in linked if row["features"].get(feature)]
        lack = [value for row, value in linked if not row["features"].get(feature)]
        mean_have, mean_lack = _mean(have), _mean(lack)
        out["features"][feature] = {
            "sample_scope": SURVIVOR_ONLY,
            "n_available": len(have), "n_unavailable": len(lack),
            "mean_return_available": mean_have,
            "mean_return_unavailable": mean_lack,
            "median_return_available": _median(have),
            "median_return_unavailable": _median(lack),
            "mean_gap": (None if mean_have is None or mean_lack is None
                         else mean_have - mean_lack),
            "effective_independent_n": round(effective_n(len(linked)), 1),
        }
    return out


# ==========================================================================
# (7) THE HEADLINE -- how much of the OPERATING universe can be scored at all
# ==========================================================================

#: What each block needs, minimally, to produce a score rather than a hole.
BLOCK_RULES = {
    "E": "ebitda_level assembles",
    "G": "revenue growth OR EBITDA growth",
    "Q": "at least one quality leg (ROA, FCF conversion, interest cover, leverage)",
    "V": "a point-in-time share count AND a usable price",
}


def block_states(row: Mapping[str, Any]) -> dict[str, bool]:
    features = row["features"]
    quality = ("quality_roa", "quality_fcf_conversion",
               "quality_interest_coverage", "quality_leverage_v2")
    return {
        "E": bool(features["ebitda_level"]),
        "G": bool(features["revenue_growth_nominal"] or features["ebitda_growth"]),
        "Q": any(features[key] for key in quality),
        "Q_all_legs": all(features[key] for key in quality),
        "V": bool(row["valuation_ok"]),
        "V_if_shares_closed": bool(row["price_ok"]),
    }


def _bound_mix(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How much of the leverage leg's coverage is an EXACT total debt.

    The v2 ladder lifts total_debt from ~16% to ~38% of the base, and the
    owner's standing note is that 94% of that recovery is a lower_bound rung.
    A census that reported the 38% without this split would be reporting the
    coverage of a quantity the factor does not name.
    """
    counts: dict[str, int] = {}
    for row in rows:
        bound = row.get("total_debt_bound")
        if bound is None:
            continue
        counts[bound] = counts.get(bound, 0) + 1
    total = sum(counts.values())
    return {"n_with_total_debt": total,
            "counts": dict(sorted(counts.items())),
            "pct": {name: (100.0 * value / total) if total else 0.0
                    for name, value in sorted(counts.items())}}


def valuation_coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The share count and the price, separately, over both denominators.

    Separately because they fail for different reasons and would be repaired by
    different work: the price is missing because the historical listing could
    not be proved, the share count because the issuer never tagged a count this
    store will accept. Reported over the base AND over the operating subset so
    the headline's valuation term can be checked against the share-count survey
    without re-deriving either.
    """
    operating = [row for row in rows
                 if not row["is_financial"] and row["division"] != SIC_BLANK_CHECK]

    def share(subset: Sequence[Mapping[str, Any]], key: str) -> float:
        return (100.0 * sum(1 for row in subset if row[key]) / len(subset)
                if subset else 0.0)

    return {"n_base": len(rows), "n_operating": len(operating),
            "base_pct_shares": share(rows, "shares_ok"),
            "base_pct_price": share(rows, "price_ok"),
            "base_pct_both": share(rows, "valuation_ok"),
            "operating_pct_shares": share(operating, "shares_ok"),
            "operating_pct_price": share(operating, "price_ok"),
            "operating_pct_both": share(operating, "valuation_ok")}


def headline(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The share of OPERATING-COMPANY observations that support all four blocks.

    THE DENOMINATOR IS STATED, not assumed. Financials (SIC 60-67) are excluded
    from the operating-company model by design -- EBITDA is not defined for a
    depository -- and so are SIC 6770 blank cheques, which have no operations
    to score. Quoting this number against the whole universe would credit the
    model for the companies it was never meant to cover, in both directions.

    Two scenarios, and the difference between them is the entire share-count
    argument:

      (i)  AS THINGS STAND. Valuation needs a point-in-time share count and a
           usable price.
      (ii) SHARE-COUNT GAP CLOSED. Valuation needs only the price. This is a
           projection of a repair, not a measurement of one, and it is an upper
           bound: it assumes every priced company would also get a defensible
           share count, which is exactly the assumption the repair has to earn.
    """
    operating = [row for row in rows
                 if not row["is_financial"] and row["division"] != SIC_BLANK_CHECK]
    n = len(operating)
    counts = {"E": 0, "G": 0, "Q": 0, "Q_all_legs": 0, "V": 0,
              "V_if_shares_closed": 0, "EGQ": 0, "full_now": 0,
              "full_shares_closed": 0, "full_all_quality_legs": 0}
    for row in operating:
        state = block_states(row)
        for key in ("E", "G", "Q", "Q_all_legs", "V", "V_if_shares_closed"):
            counts[key] += 1 if state[key] else 0
        egq = state["E"] and state["G"] and state["Q"]
        counts["EGQ"] += 1 if egq else 0
        counts["full_now"] += 1 if egq and state["V"] else 0
        counts["full_shares_closed"] += 1 if egq and state["V_if_shares_closed"] else 0
        counts["full_all_quality_legs"] += (
            1 if (state["E"] and state["G"] and state["Q_all_legs"]
                  and state["V_if_shares_closed"]) else 0)
    pct = lambda key: (100.0 * counts[key] / n) if n else 0.0
    return {
        "denominator": "operating companies = base universe less SIC 60-67 "
                       "and less SIC 6770 blank cheques",
        "n_operating": n,
        "n_base": len(rows),
        "n_financial_excluded": sum(1 for r in rows if r["is_financial"]),
        "n_blank_cheque_excluded": sum(1 for r in rows
                                       if r["division"] == SIC_BLANK_CHECK),
        "block_rules": dict(BLOCK_RULES),
        "counts": counts,
        "pct_block_E": pct("E"), "pct_block_G": pct("G"), "pct_block_Q": pct("Q"),
        "pct_block_Q_all_legs": pct("Q_all_legs"),
        "pct_block_V_now": pct("V"),
        "pct_block_V_shares_closed": pct("V_if_shares_closed"),
        "pct_three_blocks_EGQ": pct("EGQ"),
        "pct_full_score_now": pct("full_now"),
        "pct_full_score_shares_closed": pct("full_shares_closed"),
        "pct_full_score_all_quality_legs_shares_closed":
            pct("full_all_quality_legs"),
    }


# ==========================================================================
# (8) CLI
# ==========================================================================

def _peak_working_set_mib() -> Optional[float]:
    """Peak working set of this process, in MiB, or None if it cannot be read.

    Reported rather than estimated because the owner's rule is that an
    unmeasured quantity is UNKNOWN, never 0 -- and a census that says "memory:
    fine" without a number is the same claim as saying nothing.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        # argtypes are NOT optional here: a HANDLE passed as ctypes' default
        # C int is truncated on 64-bit Windows and the call fails silently,
        # returning a peak of 0 -- which under the owner's rule would be a
        # fabricated measurement, not a missing one.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        query = kernel32.K32GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters),
                          wintypes.DWORD]
        query.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not query(kernel32.GetCurrentProcess(), ctypes.byref(counters),
                     counters.cb):
            return None
        return round(counters.PeakWorkingSetSize / 2 ** 20, 1)
    except Exception:
        return None


def free_disk_gib(path: str = "C:/") -> Optional[float]:
    try:
        import shutil
        return shutil.disk_usage(path).free / 2 ** 30
    except Exception:
        return None


def open_read_only(db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """A connection that CANNOT write. Belt and braces: `mode=ro` on the URI
    and `query_only` on the connection, so neither a stray INSERT nor an
    accidental schema call can touch an 8.13 GiB store during a census."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=180)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def run(as_of: str, out_dir: str, *,
        db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
        ladder_version: str = pit_store.LADDER_VERSION_V2) -> dict[str, Any]:
    """Measure one cross-section and write its JSON. Read-only, one date."""
    import time

    started = time.time()
    conn = open_read_only(db_path)
    try:
        measured = measure_as_of(conn, as_of, ladder_version=ladder_version)
        rows = measured["rows"]
        returns = forward_returns(conn, as_of)
    finally:
        conn.close()
    payload = {
        "as_of": measured["as_of"],
        "ladder_version": ladder_version,
        "read_only": True,
        "n_peers": measured["n_peers"],
        "n_base": measured["n_base"],
        "n_cohorts": measured["n_cohorts"],
        "size_cuts": measured["size_cuts"],
        "coverage": {feature: {characteristic:
                               tabulate(rows, characteristic, feature)
                               for characteristic in CHARACTERISTICS}
                     for feature in FEATURE_KEYS},
        "missingness": missingness_summary(rows),
        "availability_vs_returns": availability_vs_returns(rows, returns),
        "valuation_coverage": valuation_coverage(rows),
        "total_debt_bound_mix": _bound_mix(rows),
        "headline": headline(rows),
        "feature_notes": dict(FEATURE_NOTE),
        "elapsed_seconds": round(time.time() - started, 1),
        "peak_working_set_mib": _peak_working_set_mib(),
        "free_disk_gib_after": free_disk_gib(),
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"coverage_{measured['as_of']}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
    payload["output_path"] = path
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    """python pit_coverage.py [--as-of YYYY-MM-DD] [--out DIR] [--all]"""
    argv = list(sys.argv[1:] if argv is None else argv)
    dates: list[str] = []
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pit_archive")
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--as-of" and index + 1 < len(argv):
            dates.append(argv[index + 1]); index += 2
        elif token == "--out" and index + 1 < len(argv):
            out_dir = argv[index + 1]; index += 2
        elif token == "--db" and index + 1 < len(argv):
            db_path = argv[index + 1]; index += 2
        elif token == "--all":
            dates.extend(MEASURED_DATES); index += 1
        else:
            print(main.__doc__)
            return 2
    if not dates:
        dates = list(MEASURED_DATES)
    print(f"free disk before: {free_disk_gib():.2f} GiB")
    for date in dates:
        payload = run(date, out_dir, db_path=db_path)
        head = payload["headline"]
        print(f"{date}: peers={payload['n_peers']:,} base={payload['n_base']:,} "
              f"operating={head['n_operating']:,} "
              f"full_now={head['pct_full_score_now']:.2f}% "
              f"full_shares_closed={head['pct_full_score_shares_closed']:.2f}% "
              f"EGQ={head['pct_three_blocks_EGQ']:.2f}% "
              f"[{payload['elapsed_seconds']}s, peak "
              f"{payload['peak_working_set_mib']} MiB] -> {payload['output_path']}")
    print(f"free disk after: {free_disk_gib():.2f} GiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
