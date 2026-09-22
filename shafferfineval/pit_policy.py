"""Versioned research policies for the point-in-time store.

Every historical row in `pit_store` records WHICH policy produced it. This
module is where those policies are written down: what a filing's information
becomes usable, which XBRL tags a financial concept may be read from, how old
a backing fact may be, and how EBITDA is assembled. Nothing here touches the
network or the database -- it is pure functions and versioned constants, so the
ingest, the replay and the option archive can all depend on it.

The version ids live in `pit_store` (LATENCY_POLICY_VERSION, LADDER_VERSION,
LADDER_VERSION_V2) and are imported, never re-declared: a row that says
`latency_policy_version = 'information_latency_policy_v1'` must mean exactly
one thing forever. A policy is NEVER edited in place. Changing a cutoff, a tag
order or a staleness bound means adding a v2 constant and a v2 ladder beside
the v1 that produced the existing rows, because a replay that cannot be
reproduced is not evidence.

That rule is now load-bearing rather than aspirational. `concept_ladder_v2`
exists (see section 2b) and is selected explicitly: `LADDER_SETS` is keyed by
the id stored on the row, and `resolve`, `ladder_for`, `concepts`,
`ladder_spec` and `policy_bundle` all take `version=` and all DEFAULT TO v1.
v1's ladders are read, never mutated -- `LADDERS_V2[k] is LADDERS[k]` for every
concept but `total_debt`, and `ladder_spec()` still serialises to the same
13,039 bytes it did before v2 was written (sha256 pinned in test_pit_policy).

Three things this module exists to prevent:

1. TRADING ON INFORMATION THAT HAD NOT LANDED. 65.9% of periodic filings are
   accepted at or after 16:00 ET, so `filed` -- a date, not a timestamp -- is
   not an availability date. See `available_date`.

2. A TAG THAT DIES QUIETLY. SVB Financial's `NetIncomeLoss` stops in 2012 while
   the company filed through 2023 using `ProfitLoss`; a single-tag extractor
   returns the FY2011 value at a 2019 as-of -- a real number, correctly
   PIT-selected, and eight years stale. See `resolve` and `is_stale`.

3. READING THE FUTURE THROUGH A TAG THAT DID NOT EXIST YET. 99.0% of the CY2017
   values of the two ASC 606 revenue tags were filed in 2019 or later, so
   consulting those tags at a 2017 as-of reads a restatement. The gate is a
   property of the ladder rung (`min_filed_date`), not a comment.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from pit_store import LADDER_VERSION, LADDER_VERSION_V2, LATENCY_POLICY_VERSION

__all__ = [
    "LATENCY_POLICY_VERSION", "LADDER_VERSION", "LADDER_VERSION_V2",
    "DEFAULT_LADDER_VERSION", "LADDER_SETS", "ladder_versions", "ladder_set",
    "available_date", "available_date_detail", "latency_policy",
    "resolve", "ladder_for", "concepts", "ladder_spec", "rung_key",
    "rung_for_key", "quantity_for", "ladder_tags", "all_ladder_tags",
    "absence_limits", "BOUND_EXACT", "BOUND_LOWER",
    "is_stale", "max_age_months", "max_age_days", "staleness_policy",
    "ebitda_assembly_spec", "mixed_accession", "policy_bundle",
]


# ==========================================================================
# (1) INFORMATION LATENCY POLICY -- 'information_latency_policy_v1'
# ==========================================================================

#: The exchange whose sessions define eligibility. Horizons and availability
#: are counted in trading sessions, never in calendar days.
MARKET_TZ_NAME = "America/New_York"

_MARKET_TZ: Optional[ZoneInfo] = None


def _market_tz() -> ZoneInfo:
    """The Eastern zone, built on first use and cached.

    Deliberately NOT constructed at import. `zoneinfo` is stdlib, but on Windows
    it reads its rules from the `tzdata` package, which this project gets only
    transitively (streamlit -> pandas -> tzdata; it is not in requirements.txt).
    Constructing the zone at import would make `import pit_policy` fail on a
    bare interpreter, and the zone is needed on ONE path only -- an acceptance
    timestamp carrying a real non-UTC offset, which EDGAR does not emit. Every
    ordinary filing is read as an Eastern wall clock and needs no rules at all.
    """
    global _MARKET_TZ
    if _MARKET_TZ is None:
        try:
            _MARKET_TZ = ZoneInfo(MARKET_TZ_NAME)
        except Exception as exc:     # ZoneInfoNotFoundError and friends
            raise RuntimeError(
                f"cannot load the {MARKET_TZ_NAME} zone ({exc}); an acceptance "
                "timestamp with a non-UTC offset cannot be converted. Install "
                "tzdata, or feed Eastern wall-clock timestamps as EDGAR does."
            ) from exc
    return _MARKET_TZ


#: A filing accepted at or before 15:30 ET is eligible for that day's close;
#: after 15:30 it is eligible from the next valid trading session.
#:
#: 15:30 and not 16:00 is a deliberate 30-minute processing buffer. A filing
#: that hits the wire seconds before the bell could not realistically be read,
#: scored and traded into that close, and a backtest that assumes otherwise is
#: buying at a price the strategy could not have reached.
LATENCY_CUTOFF = _dt.time(15, 30)
LATENCY_CUTOFF_TEXT = "15:30"
LATENCY_BUFFER_MINUTES = 30

#: How the acceptance timestamp was read. Stored alongside the decision because
#: the interpretation is the part a future reader will doubt.
ACCEPTED_MISSING = "missing"
ACCEPTED_NAIVE_AS_ET = "naive_read_as_et"
ACCEPTED_UTC_AS_ET = "utc_designator_read_as_et"
ACCEPTED_OFFSET_CONVERTED = "offset_converted_to_et"
ACCEPTED_UNPARSEABLE = "unparseable"

#: Which branch of the policy decided the date.
RULE_SAME_SESSION = "same_session"
RULE_NEXT_SESSION = "next_session"
RULE_NO_ACCEPTANCE_TIME = "next_session_no_acceptance_time"

_ZERO = _dt.timedelta(0)


def _as_date(value: Any, label: str) -> _dt.date:
    """Parse a 'YYYY-MM-DD' (or longer ISO) TEXT date, loudly."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ValueError(f"{label} is not an ISO date: {value!r}") from None


def _accepted_et(accepted_at: Any) -> tuple[Optional[_dt.datetime], str]:
    """EDGAR's acceptanceDateTime as an Eastern wall-clock datetime.

    EDGAR publishes acceptanceDateTime as an Eastern-Time wall clock. In the
    submissions API it is frequently stamped with a `Z` designator anyway, and
    that `Z` is a lie: the surge of acceptances sits at and after 16:00 in those
    values, which is the US market close, not noon.

    So the rule is:

    * naive          -> read as Eastern wall clock (the documented EDGAR form);
    * `Z` / +00:00   -> read as Eastern wall clock, ignoring the designator;
    * any other      -> genuinely zoned, converted to Eastern with astimezone.

    Ignoring a UTC designator can only ever be CONSERVATIVE. If a timestamp
    really were UTC, reading 20:05Z as 20:05 ET pushes a filing past the cutoff
    that was already past it, and reading 19:00Z (15:00 ET, eligible) as 19:00
    ET delays it by one session. The error never advances availability, which
    is the only direction that would manufacture a profit.

    Returns (eastern_naive_datetime | None, interpretation).
    """
    if accepted_at is None:
        return None, ACCEPTED_MISSING
    if isinstance(accepted_at, _dt.datetime):
        if accepted_at.tzinfo is None:
            return accepted_at, ACCEPTED_NAIVE_AS_ET
        if accepted_at.utcoffset() == _ZERO:
            return accepted_at.replace(tzinfo=None), ACCEPTED_UTC_AS_ET
        eastern = accepted_at.astimezone(_market_tz())
        return eastern.replace(tzinfo=None), ACCEPTED_OFFSET_CONVERTED

    text = str(accepted_at).strip()
    if not text:
        return None, ACCEPTED_MISSING

    # DERA writes 'YYYY-MM-DD HH:MM:SS'; the submissions API writes ISO-T.
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    zulu = text.endswith(("Z", "z"))
    if zulu:
        text = text[:-1]
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None, ACCEPTED_UNPARSEABLE

    if parsed.tzinfo is None:
        return parsed, ACCEPTED_UTC_AS_ET if zulu else ACCEPTED_NAIVE_AS_ET
    if parsed.utcoffset() == _ZERO:
        return parsed.replace(tzinfo=None), ACCEPTED_UTC_AS_ET
    return parsed.astimezone(_market_tz()).replace(tzinfo=None), ACCEPTED_OFFSET_CONVERTED


def _resolve_session(day: _dt.date,
                     next_session: Optional[Callable[[str], Optional[str]]]) -> tuple[str, bool]:
    """First trading session ON OR AFTER `day`, via the caller's calendar.

    `next_session` is a callable the caller backs with `pit_calendar` (this
    module holds no database code). It takes an ISO date and returns the first
    session on or after it, or None past the end of the calendar.

    With no resolver, or past the end of the calendar, the calendar day itself
    is returned and the caller is told the date is unresolved. That fallback is
    equivalent for a session-grid replay -- every as-of date IS a session, so a
    Saturday availability date and the following Monday select identically --
    but it is flagged rather than hidden, because it would not be equivalent on
    a calendar-day grid.
    """
    iso = day.isoformat()
    if next_session is None:
        return iso, False
    resolved = next_session(iso)
    if not resolved:
        return iso, False
    return str(resolved)[:10], True


def available_date_detail(
    accepted_at: Any,
    filed: Any,
    next_session: Optional[Callable[[str], Optional[str]]] = None,
) -> dict[str, Any]:
    """When one filing's contents first become usable, with the reasoning.

    The rule (`information_latency_policy_v1`):

        accepted at or before 15:30 ET  -> that day's session
        accepted after 15:30 ET         -> the next valid trading session
        acceptance time unknown         -> the next valid trading session
                                           AFTER `filed`

    The unknown case is deliberately the pessimistic one: 65.9% of periodic
    filings are accepted at or after 16:00 ET, so assuming same-day eligibility
    for a filing with no timestamp would be wrong about two times in three, and
    wrong in the direction that flatters the backtest.

    The result is never earlier than `filed`, which keeps every row inside
    pit_fact's `CHECK (available_date >= filed)`. That clamp is load-bearing
    rather than cosmetic: EDGAR assigns the NEXT business day as the filing date
    to anything accepted after 17:30 ET, so the acceptance day and `filed` are
    genuinely different dates for late filings.

    Returns a record: available_date, rule, accepted_et, interpretation,
    base_date, session_resolved, latency_policy_version.
    """
    filed_date = _as_date(filed, "filed")
    accepted, interpretation = _accepted_et(accepted_at)

    if accepted is None:
        rule = RULE_NO_ACCEPTANCE_TIME
        base = filed_date + _dt.timedelta(days=1)
    elif accepted.time() <= LATENCY_CUTOFF:
        rule = RULE_SAME_SESSION
        base = accepted.date()
    else:
        rule = RULE_NEXT_SESSION
        base = accepted.date() + _dt.timedelta(days=1)

    if base < filed_date:
        base = filed_date

    resolved, from_calendar = _resolve_session(base, next_session)
    if resolved < filed_date.isoformat():      # a resolver that moved backwards
        resolved = filed_date.isoformat()

    return {
        "available_date": resolved,
        "rule": rule,
        "accepted_et": accepted.isoformat(timespec="seconds") if accepted else None,
        "interpretation": interpretation,
        "base_date": base.isoformat(),
        "session_resolved": from_calendar,
        "filed": filed_date.isoformat(),
        "latency_policy_version": LATENCY_POLICY_VERSION,
    }


def available_date(
    accepted_at: Any,
    filed: Any,
    next_session: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """The date for `pit_fact.available_date`. See `available_date_detail`."""
    return available_date_detail(accepted_at, filed, next_session)["available_date"]


def latency_policy() -> dict[str, Any]:
    """The latency policy as a serialisable record, for a replay run to store."""
    return {
        "version": LATENCY_POLICY_VERSION,
        "cutoff_local_time": LATENCY_CUTOFF_TEXT,
        "timezone": MARKET_TZ_NAME,
        "buffer_minutes": LATENCY_BUFFER_MINUTES,
        "eligible_at_or_before_cutoff": "same trading session",
        "eligible_after_cutoff": "next valid trading session",
        "no_acceptance_time": "next valid trading session after `filed`",
        "acceptance_timestamp_reading": {
            "naive": "Eastern wall clock",
            "utc_designator": "Eastern wall clock; the EDGAR `Z` is ignored",
            "other_offset": "converted to Eastern with astimezone",
            "why": ("EDGAR stamps Eastern wall-clock values with Z. Trusting it "
                    "would move a 16:05 ET filing to 12:05 ET and make it "
                    "same-day eligible, which is a look-ahead leak. Misreading a "
                    "genuine UTC stamp as Eastern can only delay availability."),
        },
        "session_source": "pit_calendar via the caller's next_session callable",
        "clamp": "never earlier than `filed` (pit_fact CHECK available_date >= filed)",
        "measured_basis": ("56.75% of 406,252 accessions in the loaded store are "
                           "accepted at or after 16:00 ET, and 60.57% at this "
                           "policy's actual 15:30 cutoff (measured 2026-09-21; "
                           "the 65.9% previously quoted here came from a "
                           "six-company probe). The pessimistic default for a "
                           "missing timestamp is unaffected."),
    }


# ==========================================================================
# (2) CONCEPT LADDERS -- 'concept_ladder_v1'
# ==========================================================================

TAXONOMY_US_GAAP = "us-gaap"
TAXONOMY_DEI = "dei"

COMBINE_SINGLE = "single"
COMBINE_SUM = "sum"

PERIOD_DURATION = "duration"     # qtrs 1..4 in pit_fact
PERIOD_INSTANT = "instant"       # qtrs 0 in pit_fact

UNIT_USD = "USD"
UNIT_SHARES = "shares"

#: ASC 606 took effect for public filers in fiscal years beginning after
#: 2017-12-15. 99.0% of the CY2017 values carried by the two
#: RevenueFromContractWithCustomer* tags were filed in 2019 or later -- they are
#: comparatives inside a later filing, not what was on the wire in 2017. A
#: ladder that consults them at an earlier as-of reads the future.
ASC606_FILED_GATE = "2018-01-01"

#: How faithfully a rung's value stands for the concept it is filed under.
#: `exact` means the rung IS the concept; `lower_bound` means the rung is a
#: real, usable number that is systematically SMALLER than the concept, and a
#: consumer that ranks on it is ranking a mixture. Introduced with
#: `concept_ladder_v2`; v1 rungs carry neither field, because v1 made no such
#: distinction and its serialised spec must not change.
BOUND_EXACT = "exact"
BOUND_LOWER = "lower_bound"

#: The economic quantities a `total_debt` rung can actually deliver, ordered
#: from complete to least complete. These are NOT synonyms and the ladder is
#: ordered by them rather than by coverage: "long-term debt only" is not
#: "total debt", and a growth or leverage factor that compares one against the
#: other across two dates reads a rung change as a balance-sheet change.
#:
#: MEASURED on 337,134 co-tagged balance sheets in the loaded store
#: (2026-09-21), as a share of the full three-way total:
#:   noncurrent only             median 0.862, mean 0.782, p10 0.434
#:   noncurrent + current        median 0.932, mean 0.860, p10 0.619
#: So the noncurrent-only rung understates leverage by a median 14% and by more
#: than half for the worst decile of filers.
DEBT_Q_TOTAL = "debt_total_incl_short_term"
DEBT_Q_LONG_TERM_ALL = "debt_long_term_all_maturities"
DEBT_Q_LONG_TERM_WITH_LEASES = "debt_long_term_with_capital_leases"
DEBT_Q_LONG_TERM_NONCURRENT = "debt_long_term_noncurrent_only"


@dataclass(frozen=True)
class Rung:
    """One step of a concept ladder.

    `tags` holds one tag in the ordinary case. A rung with `combine == 'sum'`
    holds several and means their ARITHMETIC SUM, every component required --
    total debt's first rung is long-term noncurrent + long-term current + short
    term borrowings, and two of three is a wrong number, not a partial one.

    `min_filed_date` gates the rung on the backing fact's `filed` date. It is a
    field and not a comment so that `resolve` can enforce it and
    `pit_feature_definition.tag_ladder_json` can record it.

    `quantity` and `bound` say WHAT the rung measures, for a ladder whose rungs
    are deliberately different economic quantities. They default to empty and
    `exact`, and `as_dict` omits them when unset, so a v1 rung serialises
    exactly as it always did -- v1's `ladder_spec()` JSON is frozen, and this
    is the mechanism that keeps it frozen while v2 says more.
    """

    tags: tuple[str, ...]
    taxonomy: str = TAXONOMY_US_GAAP
    combine: str = COMBINE_SINGLE
    min_filed_date: Optional[str] = None
    note: str = ""
    quantity: str = ""
    bound: str = BOUND_EXACT

    @property
    def tag(self) -> str:
        """The single tag. Raises on a composite rung, which has no single tag."""
        if self.combine != COMBINE_SINGLE or len(self.tags) != 1:
            raise ValueError(f"{self.key} is composite: read .tags, not .tag")
        return self.tags[0]

    @property
    def key(self) -> str:
        """Stable identity for `pit_feature.source_tag`.

        A change of this value between adjacent as-of dates is a measurement
        DISCONTINUITY, not a fundamental change, and must set
        `source_tag_changed` so growth factors can exclude the step.
        """
        if self.combine == COMBINE_SUM:
            return "SUM(" + "+".join(self.tags) + ")"
        return self.tags[0]

    def eligible(self, as_of: str) -> bool:
        """Whether this rung may be consulted at `as_of`."""
        return self.min_filed_date is None or self.min_filed_date <= str(as_of)[:10]

    def as_dict(self) -> dict[str, Any]:
        """The rung as JSON. `quantity`/`bound` appear only when the rung sets
        them, which is what keeps `concept_ladder_v1`'s serialised spec
        byte-identical to what it was before v2 existed."""
        out = {
            "key": self.key,
            "tags": list(self.tags),
            "taxonomy": self.taxonomy,
            "combine": self.combine,
            "min_filed_date": self.min_filed_date,
            "note": self.note,
        }
        if self.quantity:
            out["quantity"] = self.quantity
            out["bound"] = self.bound
        return out


@dataclass(frozen=True)
class ConceptLadder:
    """An ordered fallback ladder for one financial concept.

    `coverage_cy2019` is the measured count of CY2019 filers the ladder
    resolves, from the 2026-09-20 survey. `measured` is False where the tag set
    was authored rather than measured -- those ladders are plausible and
    unverified, and nothing should quote a coverage number for them.
    """

    concept: str
    rungs: tuple[Rung, ...]
    period_kind: str
    unit: str
    coverage_cy2019: Optional[int] = None
    measured: bool = True
    why: str = ""
    consumer_rule: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The ladder as JSON. `consumer_rule` is emitted only when the ladder
        sets one, so v1's serialised spec is unchanged by its existence."""
        out = {
            "concept": self.concept,
            "period_kind": self.period_kind,
            "unit": self.unit,
            "coverage_cy2019": self.coverage_cy2019,
            "measured": self.measured,
            "max_age_months": {
                "annual": max_age_months(4, self.concept),
                "quarterly": max_age_months(1, self.concept),
            },
            "why": self.why,
            "rungs": [r.as_dict() for r in self.rungs],
        }
        if self.consumer_rule:
            out["consumer_rule"] = self.consumer_rule
        return out


def _gaap(*tags: str, **kw: Any) -> Rung:
    return Rung(tags=tuple(tags), **kw)


LADDERS: dict[str, ConceptLadder] = {
    "revenue": ConceptLadder(
        concept="revenue",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        why=("The two ASC 606 tags lead the ladder from 2018 and are invisible "
             "before it; `Revenues` carries the pre-606 cross-section and the "
             "SalesRevenue* tags are the pre-2018 remainder."),
        rungs=(
            _gaap("RevenueFromContractWithCustomerExcludingAssessedTax",
                  min_filed_date=ASC606_FILED_GATE,
                  note="ASC 606. 99.0% of its CY2017 values were filed 2019+."),
            _gaap("Revenues"),
            _gaap("RevenueFromContractWithCustomerIncludingAssessedTax",
                  min_filed_date=ASC606_FILED_GATE,
                  note="ASC 606, gross of assessed tax. Same gate."),
            _gaap("SalesRevenueNet"),
            _gaap("SalesRevenueGoodsNet"),
            _gaap("SalesRevenueServicesNet"),
        ),
    ),
    "net_income": ConceptLadder(
        concept="net_income",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        coverage_cy2019=6547,
        why=("SVB Financial's NetIncomeLoss stops in 2012 while the company "
             "filed through 2023 under ProfitLoss. The second rung is the whole "
             "reason ladders exist."),
        rungs=(
            _gaap("NetIncomeLoss"),
            _gaap("ProfitLoss",
                  note="Includes noncontrolling interests; the only rung SVB had after 2012."),
            _gaap("NetIncomeLossAvailableToCommonStockholdersBasic",
                  note="After preferred dividends. Last resort: a different quantity."),
        ),
    ),
    "operating_income": ConceptLadder(
        concept="operating_income",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        coverage_cy2019=5234,
        why=(f"{""}"
             "CORRECTED 2026-09-21: this ladder is NOT the binding constraint on "
             "EBITDA, and the original note had it backwards. Measured, operating "
             "income resolves for 70.1% / 74.1% / 78.0% of base while D&A resolves "
             "for 61.4% / 63.8% / 62.8% -- D&A is the SCARCER side at every date. "
             "Coverage is won or lost on the depreciation ladder. Superseded: "
             "The BINDING constraint on EBITDA: 5,234 filers against 6,144 for "
             "D&A, so operating income is what caps the EBITDA cross-section. "
             "Banks and insurers largely do not tag it at all, which is a "
             "structural absence, not a gap to be filled by a proxy."),
        rungs=(
            _gaap("OperatingIncomeLoss"),
        ),
    ),
    "depreciation_amortisation": ConceptLadder(
        concept="depreciation_amortisation",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        coverage_cy2019=6144,
        why=("6,144 is the UNION of the four tags; no single tag comes close. "
             "The cash-flow-statement variants lead because that is where D&A is "
             "reported as an add-back."),
        rungs=(
            _gaap("DepreciationDepletionAndAmortization"),
            _gaap("DepreciationAmortizationAndAccretionNet"),
            _gaap("DepreciationAndAmortization"),
            _gaap("DepreciationDepletionAndAmortizationExcludingAmortizationOfDeferredCharges"),
        ),
    ),
    "cash": ConceptLadder(
        concept="cash",
        period_kind=PERIOD_INSTANT,
        unit=UNIT_USD,
        why=("The restricted-cash tag is the ASU 2016-18 successor and is a "
             "DIFFERENT quantity -- it includes restricted balances -- so it is "
             "a fallback, never a preference."),
        rungs=(
            _gaap("CashAndCashEquivalentsAtCarryingValue"),
            _gaap("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
                  note="Includes restricted cash: an upper bound on the first rung."),
        ),
    ),
    "total_debt": ConceptLadder(
        concept="total_debt",
        period_kind=PERIOD_INSTANT,
        unit=UNIT_USD,
        coverage_cy2019=3459,
        why=("CORRECTED 2026-09-21. MEASURED 15.9% / 17.8% / 14.5% of base "
             "(1,122 / 1,062 / 864 entities), not 56% -- overstated by ~3.3x. "
             "The mechanism is the ladder's own shape: the composite first rung "
             "requires ShortTermBorrowings, which only ~7.4% of peers tag, and the "
             "best-covered debt tag (LongTermDebtNoncurrent, ~22%) is reachable "
             "ONLY inside that three-way sum. The composite rung wins for just "
             "10-13% of the entities where total_debt resolves at all. A rung that "
             "asked for LongTermDebtNoncurrent alone would cover more than the "
             "whole ladder does now -- a v2 ladder should test that. Superseded: "
             "3,459 of ~6,180 filers = 56%. This is a STRUCTURAL HOLE, not a "
             "tag-choice problem, and listwise deletion on it would silently "
             "discard 44% of the cross-section. Absent debt must be resolved to "
             "one of never_tagged / tagged_zero / company_had_none -- Apple's "
             "first long-term-debt observation is 2013-07-24 because Apple "
             "genuinely had none."),
        rungs=(
            Rung(tags=("LongTermDebtNoncurrent", "LongTermDebtCurrent",
                       "ShortTermBorrowings"),
                 combine=COMBINE_SUM,
                 note="All three components required; a partial sum is a wrong number."),
            _gaap("LongTermDebt", note="Omits short-term borrowings."),
            _gaap("LongTermDebtAndCapitalLeaseObligations"),
            _gaap("DebtLongtermAndShorttermCombinedAmount"),
        ),
    ),
    "total_assets": ConceptLadder(
        concept="total_assets",
        period_kind=PERIOD_INSTANT,
        unit=UNIT_USD,
        coverage_cy2019=6180,
        why="One tag, effectively universal; it is the denominator of the cross-section.",
        rungs=(
            _gaap("Assets"),
        ),
    ),
    "equity": ConceptLadder(
        concept="equity",
        period_kind=PERIOD_INSTANT,
        unit=UNIT_USD,
        why=("The parent-only measure leads, because ROE against a total that "
             "includes noncontrolling interests is a different ratio."),
        rungs=(
            _gaap("StockholdersEquity"),
            _gaap("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                  note="Includes NCI: a different denominator, so second."),
        ),
    ),
    "operating_cash_flow": ConceptLadder(
        concept="operating_cash_flow",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        why="The continuing-operations variant excludes discontinued operations.",
        rungs=(
            _gaap("NetCashProvidedByUsedInOperatingActivities"),
            _gaap("NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        ),
    ),
    "capex": ConceptLadder(
        concept="capex",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        why=("A cash OUTFLOW, filed as a positive number. Sign handling belongs "
             "to the feature builder, not the ladder."),
        rungs=(
            _gaap("PaymentsToAcquirePropertyPlantAndEquipment"),
            _gaap("PaymentsToAcquireProductiveAssets"),
        ),
    ),
    "shares_outstanding": ConceptLadder(
        concept="shares_outstanding",
        period_kind=PERIOD_INSTANT,
        unit=UNIT_SHARES,
        why=("The dei cover-page count is the as-of-the-filing-date share count "
             "and is refreshed every filing, which is why it leads and why it "
             "carries a tighter staleness bound. The weighted-average diluted "
             "count is a DURATION average and a last resort; a split between "
             "the period end and the as-of date is un-applied by the corporate "
             "action table, not by choosing a different tag."),
        rungs=(
            Rung(tags=("EntityCommonStockSharesOutstanding",), taxonomy=TAXONOMY_DEI,
                 note="Cover page, dated at the filing, not at the period end."),
            _gaap("CommonStockSharesOutstanding",
                  note="Per class: sum across classes is the feature builder's job."),
            _gaap("WeightedAverageNumberOfDilutedSharesOutstanding",
                  note="A period average, not a point count. Last resort."),
        ),
    ),
    "interest_expense": ConceptLadder(
        concept="interest_expense",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        measured=False,
        why=("AUTHORED, NOT MEASURED -- no coverage survey has been run on this "
             "tag set, so no coverage claim may be made from it. Deliberately "
             "excludes InterestIncomeExpenseNet: for a bank that is a net "
             "figure of the opposite sign and would invert a coverage ratio."),
        rungs=(
            _gaap("InterestExpense"),
            _gaap("InterestExpenseDebt"),
            _gaap("InterestAndDebtExpense", note="Includes debt-extinguishment items."),
            _gaap("InterestExpenseNonoperating"),
        ),
    ),
    "gross_profit": ConceptLadder(
        concept="gross_profit",
        period_kind=PERIOD_DURATION,
        unit=UNIT_USD,
        measured=False,
        why=("AUTHORED, NOT MEASURED. One rung on purpose: revenue minus cost of "
             "sales is an ASSEMBLY of two concepts from possibly different "
             "accessions, exactly like EBITDA, and belongs in an assembly spec "
             "where the provenance array can record both sources -- not in a "
             "ladder rung, whose sum semantics are same-sign components of one "
             "quantity."),
        rungs=(
            _gaap("GrossProfit"),
        ),
    ),
}


# ==========================================================================
# (2b) CONCEPT LADDERS -- 'concept_ladder_v2'
#
# v2 exists for ONE reason: v1's total_debt ladder hides its best-covered tag
# inside a composite rung. Measured on the loaded store (full census,
# 2026-09-21, base = peers with a usable non-stale Assets fact, 7,073 / 5,959 /
# 5,944 at 2015-06-30 / 2019-06-28 / 2024-06-28):
#
#   v1 total_debt resolves                     15.9% / 17.8% / 14.5%
#   union of the tags v1's own ladder names    42.8% / 46.7% / 42.7%
#   LongTermDebtNoncurrent alone               24.8% / 27.5% / 25.7%
#   ShortTermBorrowings alone                   8.3% /  8.8% /  8.8%
#   all three components at a matched period    1.9% /  1.8% /  1.8%
#
# The first rung REQUIRES ShortTermBorrowings, which fewer than one peer in
# eleven tags, so the rung that carries LongTermDebtNoncurrent fires for under
# 2% of the base and the 24.8% tag is unreachable anywhere else in the ladder.
# total_debt is the ONLY ladder in v1 whose realised coverage is below the union
# of its own tags: every other concept measured realised == union exactly, which
# is the signature of a composite (or a date gate), not of a tag order. Ordering
# single-tag rungs can never change coverage; it only changes which rung wins.
#
# v2 does not reorder for coverage. It adds the partial quantities as EXPLICIT,
# LABELLED rungs, so that a smaller-but-real number is reachable and is never
# silently passed off as the total. Everything else in v2 is the SAME OBJECT as
# in v1 -- `LADDERS_V2[k] is LADDERS[k]` for every concept but total_debt.
# ==========================================================================

#: How a consumer must read a `total_debt` row resolved under v2. Stored with
#: the ladder (and so inside `pit_feature_definition.tag_ladder_json`) because
#: a rule that lives only in a docstring is a rule the replay can violate.
_TOTAL_DEBT_V2_CONSUMER_RULE = (
    "Every v2 total_debt row carries ladder_version='concept_ladder_v2' and "
    "source_tag = the winning rung key. The rungs are DIFFERENT ECONOMIC "
    "QUANTITIES (rung.quantity, rung.bound), so three obligations follow.\n"
    "(1) NO CHANGE-BASED FACTOR MAY CROSS A RUNG CHANGE. The feature builder "
    "sets pit_feature.source_tag_changed = 1 whenever this entity's source_tag "
    "for this feature differs from its value at the previous as_of on the grid. "
    "Debt growth, change in leverage, net-debt delta and any other first "
    "difference MUST drop that step -- treat it as missing, not as zero. A move "
    "from SUM(LongTermDebtNoncurrent+LongTermDebtCurrent) to "
    "LongTermDebtNoncurrent is a measurement discontinuity of a median 7.5% of "
    "the balance and up to half of it, in the same direction for every filer, "
    "and a cross-sectional ranking of that difference would be a ranking of "
    "tagging practice.\n"
    "(2) THE CROSS-SECTION IS A MIXTURE, AND THE BIAS HAS A DIRECTION. Rungs "
    "with bound='lower_bound' UNDERSTATE debt, so the entity resolved on them "
    "looks LESS levered than it is. Both debt factors in the equity model "
    "(net_debt_ebitda, debt_market_cap) are lower-is-better, so a lower-bound "
    "rung FLATTERS the score. A percentile computed over a mixture of rungs "
    "must either report the rung distribution beside it or be computed within "
    "rung groups; it may not be presented as a clean leverage percentile.\n"
    "(3) THE RUNG IS DATA, NOT DIAGNOSTICS. quantity_for(concept, source_tag, "
    "version) maps a stored source_tag back to its quantity and bound without "
    "re-deriving the ladder, so a downstream model can carry the rung as a "
    "covariate. Do NOT rescale a lower-bound rung up to an estimated total: "
    "the measured ratios are a distribution, not a factor, and imputing one "
    "would put a modelled number where the filing has an absence.\n"
    "(4) AN UNRESOLVED ROW IS STILL 'ladder_exhausted', NOT 'no debt'. v2 "
    "widens what is reachable; it cannot tell a company that reported no debt "
    "from one that never tagged it. See absence_limits()."
)

_TOTAL_DEBT_V2 = ConceptLadder(
    concept="total_debt",
    period_kind=PERIOD_INSTANT,
    unit=UNIT_USD,
    coverage_cy2019=None,
    why=("v1's shape, not its tag choice, was the constraint: its best tag "
         "(LongTermDebtNoncurrent, 24.8% / 27.5% / 25.7% of base) existed only "
         "inside a three-way sum that required ShortTermBorrowings (8.3% / "
         "8.8% / 8.8%), so the sum fired for 1.9% / 1.8% / 1.8%. v2 keeps that "
         "sum FIRST -- it is the only rung that is actually total debt -- and "
         "then descends through strictly smaller, explicitly labelled "
         "quantities. Order is by economic completeness, never by coverage. "
         "Two rungs are deliberately NOT here: LongTermDebtCurrent alone (next "
         "twelve months' maturities, a fragment of the balance rather than a "
         "bound on it) and ShortTermBorrowings alone (the same objection). "
         "Reachability is not the test; being a usable understatement of total "
         "debt is -- the two rejected rungs would have added a further 4.8 / "
         "4.4 / 5.0 points and are still not worth it. MEASURED RESULT "
         "(2026-09-21, same base as v1's census): total_debt resolves for "
         "37.9% / 42.3% / 37.7% against v1's 15.9% / 17.8% / 14.5% -- 2.4x, "
         "+22.1 / +24.5 / +23.2 points, 1,561 / 1,460 / 1,379 more entities. "
         "The price is stated rather than hidden: 94.4% / 94.5% / 93.4% of the "
         "resolved cross-section now sits on a lower_bound rung, and only "
         "2.1% / 2.3% / 2.5% of the base has a rung that is genuinely total "
         "debt. Of the entities v1 already resolved, 26 / 35 / 20 change rung, "
         "every one of them upward into a MORE complete quantity."),
    consumer_rule=_TOTAL_DEBT_V2_CONSUMER_RULE,
    rungs=(
        Rung(tags=("LongTermDebtNoncurrent", "LongTermDebtCurrent",
                   "ShortTermBorrowings"),
             combine=COMBINE_SUM,
             quantity=DEBT_Q_TOTAL, bound=BOUND_EXACT,
             note=("All three components required at a MATCHED period end; a "
                   "partial sum is a wrong number, which is why the partials "
                   "below are separate rungs with their own labels.")),
        _gaap("DebtLongtermAndShorttermCombinedAmount",
              quantity=DEBT_Q_TOTAL, bound=BOUND_EXACT,
              note=("Total debt as one tag. Second and not first because it is "
                    "rare (0.2% / 0.5% / 0.7% of base) and thinly attested: of "
                    "18 balance sheets that carry it beside the long-term "
                    "components, 13 equal long-term debt alone -- consistent "
                    "with filers that had no short-term debt, but too few to "
                    "verify the element's scope from data.")),
        Rung(tags=("LongTermDebtNoncurrent", "LongTermDebtCurrent"),
             combine=COMBINE_SUM,
             quantity=DEBT_Q_LONG_TERM_ALL, bound=BOUND_LOWER,
             note=("All maturities of long-term debt, short-term borrowings "
                   "excluded. Measured median 0.932 / mean 0.860 of the full "
                   "three-way total. Leads the long-term band because its "
                   "composition is known exactly -- unlike the single tags "
                   "below, nothing has to be assumed about what it includes.")),
        _gaap("LongTermDebt",
              quantity=DEBT_Q_LONG_TERM_ALL, bound=BOUND_LOWER,
              note=("Nominally the same quantity as the rung above. Kept below "
                    "it because the store disagrees with the label: of 497 "
                    "balance sheets carrying all three, LongTermDebt equals "
                    "noncurrent+current 50.3% of the time and noncurrent alone "
                    "21.7%, so its scope is a coin-flip per filer.")),
        _gaap("LongTermDebtAndCapitalLeaseObligations",
              quantity=DEBT_Q_LONG_TERM_WITH_LEASES, bound=BOUND_LOWER,
              note=("Long-term debt WITH capital-lease obligations -- a "
                    "different quantity again, and not a strict superset of "
                    "the rung above. Its maturity scope could not be settled "
                    "empirically (408 co-tagged balance sheets, 13.5% equal to "
                    "noncurrent, 16.4% to noncurrent+current), so it is "
                    "labelled a lower bound and kept in v1's relative position "
                    "rather than promoted on evidence that does not exist.")),
        _gaap("LongTermDebtNoncurrent",
              quantity=DEBT_Q_LONG_TERM_NONCURRENT, bound=BOUND_LOWER,
              note=("The single best-covered debt tag in the store and the "
                    "whole reason v2 exists -- unreachable on its own under "
                    "v1. LAST, because it is the least complete: median 0.862, "
                    "mean 0.782, p10 0.434 of the full total. It is a real "
                    "number about a real balance sheet and it is not total "
                    "debt; bound='lower_bound' is how the row says so.")),
    ),
)

#: v2 = v1 with ONE concept replaced. Every other entry is the SAME OBJECT, so
#: the two versions cannot drift apart by accident and a diff of the two specs
#: shows exactly one concept. v2 introduces NO new tag, which is why
#: `pit_dera.TAG_FILTER` (built from the ladders) is unchanged and the loaded
#: 14,072,934-row store can serve v2 with no re-ingest.
LADDERS_V2: dict[str, ConceptLadder] = dict(LADDERS)
LADDERS_V2["total_debt"] = _TOTAL_DEBT_V2

#: Every ladder set that has ever been stamped on a row, keyed by the id stored
#: in `pit_feature.ladder_version`. v1 is FROZEN: it is never edited, only read.
LADDER_SETS: dict[str, dict[str, ConceptLadder]] = {
    LADDER_VERSION: LADDERS,
    LADDER_VERSION_V2: LADDERS_V2,
}

#: What every existing caller gets when it passes no version. Deliberately v1:
#: adding v2 must not silently change what an un-updated caller resolves.
DEFAULT_LADDER_VERSION = LADDER_VERSION


def ladder_versions() -> tuple[str, ...]:
    """Every selectable ladder version, oldest first."""
    return (LADDER_VERSION, LADDER_VERSION_V2)


def ladder_set(version: Optional[str] = None) -> dict[str, ConceptLadder]:
    """The ladders for one version. Raises on an unknown version id."""
    name = version or DEFAULT_LADDER_VERSION
    try:
        return LADDER_SETS[name]
    except KeyError:
        raise ValueError(
            f"unknown ladder version {name!r}; known: "
            f"{', '.join(ladder_versions())}") from None


def concepts(version: Optional[str] = None) -> tuple[str, ...]:
    """Every concept this ladder version defines, in a stable order."""
    return tuple(sorted(ladder_set(version)))


def ladder_for(concept: str, version: Optional[str] = None) -> ConceptLadder:
    """The whole ladder for one concept, ungated. Raises on an unknown concept."""
    ladders = ladder_set(version)
    try:
        return ladders[concept]
    except KeyError:
        raise ValueError(
            f"unknown concept {concept!r}; known: "
            f"{', '.join(concepts(version))}") from None


def resolve(concept: str, as_of: str,
            version: Optional[str] = None) -> tuple[Rung, ...]:
    """The ordered rungs that may be consulted at `as_of`.

    Ordered means TRIED IN ORDER and the first that yields a fact wins; the
    winning rung's `key` goes into `pit_feature.source_tag`.

    Date-eligible means a rung gated on `min_filed_date` is INVISIBLE at an
    earlier as-of. At 2016 the revenue ladder is four rungs long and starts at
    `Revenues`; at 2019 it is six and starts at the ASC 606 tag. That is the
    whole point -- a 2016 replay that could see the ASC 606 tag would be reading
    a restatement filed three years later.

    `version` selects the ladder set and DEFAULTS TO v1, so a caller written
    before v2 existed keeps resolving exactly what it always resolved. The
    version that produced a row belongs on the row: pass the same id to
    `pit_feature.ladder_version` that you passed here.
    """
    day = str(as_of)[:10]
    return tuple(rung for rung in ladder_for(concept, version).rungs
                 if rung.eligible(day))


def rung_key(rung: Rung) -> str:
    """The `source_tag` value for a rung. See `Rung.key`."""
    return rung.key


def rung_for_key(concept: str, source_tag: str,
                 version: Optional[str] = None) -> Optional[Rung]:
    """The rung a stored `pit_feature.source_tag` came from, or None.

    The inverse of `rung_key`, so a consumer reading rows back out of the store
    can recover what the number MEANS without re-deriving the ladder. Pass the
    row's own `ladder_version`: the same source_tag can sit at a different rung
    position in a different version.
    """
    for rung in ladder_for(concept, version).rungs:
        if rung.key == source_tag:
            return rung
    return None


def quantity_for(concept: str, source_tag: str,
                 version: Optional[str] = None) -> dict[str, Any]:
    """What a stored row actually measured: its quantity and its bound.

    Returns {'quantity', 'bound', 'rung_index', 'known'}. `known` is False for
    an unrecognised source_tag AND for a v1 row, which carries no quantity
    label at all -- v1 could not distinguish total debt from long-term debt,
    and saying so is more useful than inventing a label for it now.
    """
    rung = rung_for_key(concept, source_tag, version)
    if rung is None:
        return {"quantity": "", "bound": "", "rung_index": None, "known": False}
    rungs = ladder_for(concept, version).rungs
    return {
        "quantity": rung.quantity,
        "bound": rung.bound if rung.quantity else "",
        "rung_index": rungs.index(rung),
        "known": bool(rung.quantity),
    }


def ladder_tags(version: Optional[str] = None) -> frozenset[str]:
    """Every tag named by one ladder version, taxonomy ignored.

    `pit_dera.TAG_FILTER` is built from the ladders, so this is the set the
    ingest must retain for that version to be resolvable at all. v2 is a subset
    of v1: it renames no tag and adds none, which is the whole reason it can be
    measured against the store that is already loaded.
    """
    return frozenset(t for ladder in ladder_set(version).values()
                     for rung in ladder.rungs for t in rung.tags)


def all_ladder_tags() -> frozenset[str]:
    """The union over every ladder version -- what an ingest must retain to
    keep ALL versions resolvable, not merely the default one."""
    return frozenset().union(*(ladder_tags(v) for v in ladder_versions()))


def ladder_spec(version: Optional[str] = None) -> dict[str, Any]:
    """The whole ladder set as a JSON-serialisable dict.

    This is what `pit_feature_definition.tag_ladder_json` stores, so a feature
    row can always be traced back to the exact tag order and date gates that
    produced it -- including the rungs that were gated OUT at its as-of date.

    With no `version` this returns v1, byte-for-byte what it returned before v2
    was written. `test_pit_policy` pins its sha256.
    """
    name = version or DEFAULT_LADDER_VERSION
    ladders = ladder_set(name)
    return {
        "ladder_version": name,
        "asc606_filed_gate": ASC606_FILED_GATE,
        "combine_modes": [COMBINE_SINGLE, COMBINE_SUM],
        "gate_semantics": ("min_filed_date gates the backing fact's `filed` date; "
                           "resolve() hides the rung at an earlier as_of"),
        "concepts": {name_: ladders[name_].as_dict() for name_ in concepts(name)},
    }


#: What NO ladder version can repair, because the evidence was dropped before
#: it reached the store. Kept as data rather than as a comment so a replay can
#: serialise it next to the coverage numbers it qualifies.
_ABSENCE_LIMITS: dict[str, Any] = {
    "measured_on": "2026-09-21",
    "store": "shafferfineval_pit.db, 14,072,934 pit_fact rows, 69 quarters "
             "2009q2..2026q2",
    "headline": ("A total_debt row that does not resolve means 'this ladder "
                 "found nothing', and NOTHING MORE. pit_store distinguishes "
                 "never_tagged, tagged_zero and company_had_none; none of the "
                 "three is computable from the loaded data."),
    "why_not_computable": {
        "rows_discarded_at_ingest": 159522,
        "share_of_rows_read": "0.086% of 184,959,880 num.txt rows read",
        "mechanism": ("DERA's num.txt carries rows whose `value` field is empty "
                      "-- a tagged fact with a footnote and no number. "
                      "pit_fact.val is NOT NULL and an absent value is not a "
                      "zero, so pit_dera counts them in `filters.empty_value` "
                      "and drops them. The COUNT survives in "
                      "pit_ingest_run.summary_json; the identity of the rows "
                      "(entity, tag, period, accession) does not."),
        "consequence": ("'the filer tagged this and reported no amount' and "
                        "'the filer never tagged this' are the same absence in "
                        "the store today. Apple is the clean example: its first "
                        "long-term-debt fact is period 2012-09-30, filed "
                        "2013-07-24, and it carries no ShortTermBorrowings fact "
                        "in 88 filings. Apple genuinely had no long-term debt "
                        "before the 2013 bond issue -- that is a real, "
                        "informative zero, and it is indistinguishable from an "
                        "untagged absence."),
    },
    "what_it_would_take": {
        "1_schema": ("A SIDECAR table -- pit_fact_absence(entity_id, taxonomy, "
                     "tag, period_end, qtrs, accn, form, filed, "
                     "available_date, absence_kind) with the same UNIQUE key "
                     "as pit_fact minus `val`. NOT a nullable pit_fact.val: "
                     "that column's NOT NULL is what stops an empty value "
                     "being read as a zero, and pit_fact is append-only with "
                     "immutability triggers, so it cannot be migrated in "
                     "place."),
        "2_repass": ("A re-pass over the DERA archive. 5.26 GiB of zips across "
                     "69 quarters, measured from the ingest run summaries "
                     "(sum of zip_bytes). It is a STREAM-AND-DISCARD pass: "
                     "keep only rows whose tag is in TAG_FILTER and whose "
                     "value is empty, write the absence row, delete the "
                     "quarter's zip before fetching the next. Peak disk is one "
                     "quarter's archive plus the new table."),
        "3_size": ("~159,522 absence rows, on the order of 15 MB -- three "
                   "orders of magnitude smaller than the facts they explain."),
        "4_then": ("Only then can unavailable_reason separate never_tagged "
                   "(no row at all) from tagged_zero (an absence row) from "
                   "company_had_none (an absence row, or a tagged 0, in a "
                   "filing that also carries the neighbouring debt tags)."),
        "disk_precondition": ("The volume held 13.4 GiB free of 238 GiB on "
                              "2026-09-21. Check shutil.disk_usage before the "
                              "pass and abort rather than fill it; an earlier "
                              "ingest failed by letting a SQLite WAL reach "
                              "9.3 GB."),
    },
    "dead_rung": {
        "concept": "depreciation_amortisation",
        "tag": "DepreciationDepletionAndAmortizationExcludingAmortization"
               "OfDeferredCharges",
        "rows_in_store": 0,
        "note": ("The fourth D&A rung is in TAG_FILTER and was therefore "
                 "retained by the ingest, but no consolidated row carries it "
                 "in 69 quarters. It costs nothing and resolves nothing. It is "
                 "recorded here rather than removed, because removing it would "
                 "mean editing a frozen ladder."),
    },
}


def absence_limits() -> dict[str, Any]:
    """What no ladder version can repair. Declarative; computes nothing.

    A coverage number is only honest beside the absences it cannot explain, so
    this record travels with the coverage claims rather than living in a report
    nobody reads twice. Returned as a copy: a caller may not mutate the policy.
    """
    return json.loads(json.dumps(_ABSENCE_LIMITS))


# ==========================================================================
# (3) STALENESS POLICY
# ==========================================================================

#: An annual fact may back a feature for 15 months: a 10-K lands roughly two to
#: three months after the fiscal year end, so 15 months is one reporting cycle
#: plus the filing lag, and anything older means the next 10-K never arrived.
MAX_AGE_ANNUAL_MONTHS = 15

#: A quarterly fact may back a feature for 6 months -- two missed 10-Qs.
MAX_AGE_QUARTERLY_MONTHS = 6

#: Per-concept overrides, only where the default is genuinely wrong.
#: The cover-page share count is refreshed at EVERY filing, so a 15-month-old
#: count is not a slow-moving number, it is a missed filing; and because the
#: share count multiplies into market cap, a stale one mis-scales valuation for
#: the whole company rather than degrading one factor.
CONCEPT_MAX_AGE_MONTHS: dict[str, dict[str, int]] = {
    "shares_outstanding": {"annual": 12, "quarterly": 4},
}

#: Days per month used ONLY to derive the loose day bound below.
_DAYS_PER_MONTH_UPPER = 31


def max_age_months(qtrs: int, concept: Optional[str] = None) -> int:
    """The freshness budget in months for a fact of this period length.

    qtrs >= 4 is annual. Everything shorter -- including qtrs 0, the
    instantaneous balance-sheet facts -- gets the quarterly budget, because a
    balance sheet arrives with every 10-Q and a six-month-old one means a
    filing was missed.
    """
    bucket = "annual" if int(qtrs) >= 4 else "quarterly"
    override = CONCEPT_MAX_AGE_MONTHS.get(concept or "")
    if override and bucket in override:
        return int(override[bucket])
    return MAX_AGE_ANNUAL_MONTHS if bucket == "annual" else MAX_AGE_QUARTERLY_MONTHS


def max_age_days(qtrs: int, concept: Optional[str] = None) -> int:
    """The same budget as a DAY count, for `pit_store.latest_period_as_of`.

    Deliberately the loosest reading of the month rule (31 days a month), so
    the SQL pre-filter can only ever be more permissive than `is_stale`. The
    month arithmetic is the authority; this is a bound, and a fact that passes
    it still has to pass `is_stale`. Also what goes into
    `pit_feature.fact_max_age_days`.
    """
    return max_age_months(qtrs, concept) * _DAYS_PER_MONTH_UPPER


def _add_months(day: _dt.date, months: int) -> _dt.date:
    """Calendar month addition, clamping the day into the target month."""
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    if month == 12:
        last = 31
    else:
        last = (_dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(day.day, last))


def is_stale(period_end: Any, as_of: Any, qtrs: int,
             concept: Optional[str] = None) -> bool:
    """Whether a fact's period is too old to back a feature on `as_of`.

    The deadline is exact calendar month arithmetic from `period_end`, and the
    boundary is INCLUSIVE: an annual fact for 2019-12-31 is usable through
    2021-03-31 and stale from 2021-04-01.

    Staleness is the second half of the ladder defence. A ladder stops a dead
    tag returning nothing; the age bound stops a dead COMPANY returning
    something -- SVB's FY2011 net income is a real, correctly point-in-time
    selected number at a 2019 as-of, and using it is the failure this test
    catches.

    An unparseable period end is treated as stale: unusable data is never
    resolved in favour of the feature.
    """
    try:
        end = _as_date(period_end, "period_end")
        day = _as_date(as_of, "as_of")
    except ValueError:
        return True
    return day > _add_months(end, max_age_months(qtrs, concept))


def staleness_policy() -> dict[str, Any]:
    """The staleness policy as a serialisable record."""
    return {
        "ladder_version": LADDER_VERSION,
        "annual_max_age_months": MAX_AGE_ANNUAL_MONTHS,
        "quarterly_max_age_months": MAX_AGE_QUARTERLY_MONTHS,
        "annual_bucket": "qtrs >= 4",
        "quarterly_bucket": "qtrs 0..3, instantaneous facts included",
        "boundary": "inclusive: stale strictly after period_end + max_age_months",
        "concept_overrides": {k: dict(v) for k, v in CONCEPT_MAX_AGE_MONTHS.items()},
        "day_bound_note": ("max_age_days is months * 31, a deliberately loose SQL "
                           "pre-filter; the month rule in is_stale is the authority"),
        "unavailable_reason": "stale_beyond_max_age",
    }


# ==========================================================================
# (4) EBITDA ASSEMBLY SPEC -- declarative, not the computation
# ==========================================================================

#: The flag a feature row carries when its components came from different
#: accessions. Not an error -- a legitimate, and common, provenance state.
EBITDA_MIXED_ACCESSION_FLAG = "ebitda_mixed_accession"

_EBITDA_ASSEMBLY: dict[str, Any] = {
    "feature_key": "ebitda",
    "ladder_version": LADDER_VERSION,
    "formula": "operating_income + depreciation_amortisation",
    "components": [
        {"concept": "operating_income", "sign": 1, "required": True,
         "why": "The binding constraint: 5,234 CY2019 filers."},
        {"concept": "depreciation_amortisation", "sign": 1, "required": True,
         "why": "6,144 CY2019 filers as a four-tag union."},
    ],
    "not_a_tag": ("EBITDA is not a concept in the XBRL taxonomy. It is assembled, "
                  "and the assembly is the thing that must be versioned."),
    "same_filing_preferred": True,
    "same_filing_rule": ("Prefer both components from the SAME accession. Operating "
                         "income is an income-statement line and D&A is usually a "
                         "cash-flow-statement add-back, so they normally share an "
                         "accession -- but a restatement of one and not the other, "
                         "or a ladder rung that resolves to an older filing, breaks "
                         "that and must be visible rather than assumed."),
    "mixed_accession_flag": EBITDA_MIXED_ACCESSION_FLAG,
    "availability_rule": ("feature_available_date = MAX(component available_date); "
                          "the assembly is knowable only once its LAST component is."),
    "staleness_rule": ("Each component is tested with is_stale independently; one "
                       "stale component makes the assembly unavailable, never partial."),
    "period_rule": ("Both components must share period_end and qtrs. A quarterly "
                    "operating income added to an annual D&A is not EBITDA."),
    "availability_on_missing": {
        "availability": "unavailable",
        "reason": "ladder_exhausted",
        "note": ("Never substituted with net income plus D&A, and never imputed. "
                 "Absent EBITDA removes 67% of the equity score's weight, which is "
                 "a fact about coverage the replay must report, not repair."),
    },
    "why_sources_json_is_an_array": (
        "This is the reason pit_feature.sources_json is an ARRAY. One accession "
        "could never answer 'what exactly did the system know' for a two-component "
        "assembly whose components are frequently filed on different dates; the "
        "array carries one entry per component with its own tag, accession, filed "
        "date and available_date."),
    "coverage_note": ("At most ~85% of the cross-section, bounded by operating "
                      "income. The intersection of the two ladders has not been "
                      "measured -- do not quote one."),
}


def ebitda_assembly_spec() -> dict[str, Any]:
    """How EBITDA is assembled, as a record. Declarative: it computes nothing."""
    return json.loads(json.dumps(_EBITDA_ASSEMBLY))


def mixed_accession(accessions: Iterable[Optional[str]]) -> bool:
    """Whether an assembly's components came from more than one filing.

    The single definition of `ebitda_mixed_accession`, so the flag means the
    same thing wherever it is set. A missing accession counts as its own
    source: unknown provenance is not shared provenance.
    """
    seen = {(a if a else "") for a in accessions}
    return len(seen) > 1


# ==========================================================================
# The bundle a replay run serialises
# ==========================================================================

def policy_bundle(ladder_version: Optional[str] = None) -> dict[str, Any]:
    """Every policy in this module, for `pit_replay_run.source_versions_json`.

    A replay that cannot say which policies produced it is not reproducible, so
    the run stores the policies themselves and not merely their version ids.

    `ladder_version` selects which ladder set the run used and defaults to v1,
    so a bundle written by an un-updated caller is unchanged. Passing v2 swaps
    the ladder spec AND restamps the staleness and EBITDA records, because a
    bundle that said v1 while the replay resolved v2 would be worse than no
    bundle at all.
    """
    name = ladder_version or DEFAULT_LADDER_VERSION
    ladder_set(name)                       # raises on an unknown version
    staleness = staleness_policy()
    ebitda = ebitda_assembly_spec()
    staleness["ladder_version"] = name
    ebitda["ladder_version"] = name
    return {
        "latency": latency_policy(),
        "ladders": ladder_spec(name),
        "staleness": staleness,
        "ebitda": ebitda,
    }
