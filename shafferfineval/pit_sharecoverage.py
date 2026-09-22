"""SHARE-COUNT / MARKET-CAP COVERAGE STUDY. Read-only, diagnostic, no replay.

Owner work orders #5 and #6. This module MEASURES what the store can and cannot
say about `SharesOutstandingKnownAt_t`, and it writes nothing: `pit_feature`,
`pit_score` and `pit_replay_run` stay at zero rows. It is the diagnostic half
of `pit_shares` -- that module designs the ladder and owns the (not yet
created) `pit_share_obs` table; this one asks what the ladder actually resolves
against the 14,072,934-row archive as it exists today, and what a targeted
fetch would cost to change the answer.

    MarketCap_t = Price_t x SharesOutstandingKnownAt_t

Both terms are scarce, and they are scarce for unrelated reasons, so the joint
coverage is much worse than either. This module reports the join, not the
factors, because the join is what a valuation percentile actually needs.

==========================================================================
THE FINDING THAT REFRAMES THE QUESTION
==========================================================================

`pit_fact` holds 37 DISTINCT TAGS. It is a curated financial-statement ingest
(`pit_dera.TAG_FILTER`), not an XBRL mirror, and three of the candidates the
work order names ARE NOT IN IT AT ALL:

    us-gaap:CommonStockSharesIssued        0 rows   never ingested
    us-gaap:TreasuryStockShares            0 rows   never ingested
    dei:Security12bTitle                   0 rows   never ingested (see below)
    us-gaap:CommonStockSharesAuthorized    0 rows   never ingested

So the treasury-stock question cannot be asked of this store at any price in
SQL. It is a FETCH question, and it is priced below rather than answered -- and
it matters more than it looks, because issued MINUS treasury is not a proxy for
outstanding, it is the identity that defines it, and it is the only rung that
recovers an issuer tagging neither outstanding count (Coca-Cola among them).

What the store does hold, measured 2026-09-21:

    us-gaap:CommonStockSharesOutstanding        589,989 rows  12,689 entities
    us-gaap:WeightedAverageNumberOfDiluted...   696,357 rows  10,335 entities
    dei:EntityCommonStockSharesOutstanding          606 rows     150 entities
    dei:EntityPublicFloat                           130 rows      78 entities

The designed LEAD rung is the third line. It exists for 150 entities in the
whole archive and resolves, freshly, for 11 / 1 / 1 entities at the three
study dates. The ladder's designed first choice is, in practice, absent --
not because the tag is rare at the SEC (it is near-universal; see the fetch
measurement) but because DERA's compact `num.txt` drops cover-page facts.

==========================================================================
THE INVARIANT THIS MODULE DEFENDS
==========================================================================

A SHARE COUNT MAY ONLY BE READ FORWARD. Selection takes the newest
`period_end` among facts whose `available_date` and `period_end` both precede
the as-of date, tie-broken by the later availability and then the later
filing. A restatement of the SAME instant supersedes only once it is public.
That rule is what makes the 4:1 Apple split of 2020-08-31 invisible to a
2020-01-31 as-of, and it is implemented once, in `select_share_pit`.

The mirror of that rule is not a selector but an arithmetic warning, and it is
`pit_shares`' to enforce: a count measured on D and a price measured on A > D
are in different share bases if a split falls in (D, A]. This module reports
how often that window is dirty; it does not compute a market cap.

==========================================================================
WHAT IS DELIBERATELY NOT HERE
==========================================================================

No substitution of today's shares. No weighted-average diluted count promoted
to a point-in-time count -- it is measured precisely so its unsuitability is a
number rather than an opinion. No share count inferred from a future corporate
action. An issuer the store cannot count is reported as uncountable, and stays
in the peer universe, because dropping it is the survivorship bias the store
exists to remove.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import pit_policy

__all__ = [
    "ShareCandidate", "SHARE_CANDIDATES", "candidate_for", "candidate_spec",
    "TAG_COVER", "TAG_GAAP_OUTSTANDING", "TAG_GAAP_ISSUED",
    "TAG_WEIGHTED_DILUTED", "TAG_PUBLIC_FLOAT", "STUDY_DATES",
    "connect_readonly", "peer_universe", "share_facts", "select_share_pit",
    "coverage_at", "classify_security_title", "multiclass_at",
    "fetch_scope", "fetch_cost", "MEASURED", "multiclass_verdict",
]

# --------------------------------------------------------------------------
# Tags, exactly as they appear in `pit_fact.tag` (bare, taxonomy in `taxonomy`)
# --------------------------------------------------------------------------

TAG_COVER = "EntityCommonStockSharesOutstanding"
TAG_GAAP_OUTSTANDING = "CommonStockSharesOutstanding"
TAG_GAAP_ISSUED = "CommonStockSharesIssued"
TAG_WEIGHTED_DILUTED = "WeightedAverageNumberOfDilutedSharesOutstanding"
TAG_PUBLIC_FLOAT = "EntityPublicFloat"

#: The three cross-sections every earlier phase reported on, kept identical so
#: this study's numbers sit beside the peer-universe and EBITDA measurements
#: rather than beside nothing.
STUDY_DATES: tuple[str, ...] = ("2015-06-30", "2019-06-28", "2024-06-28")

#: The staleness concept. `pit_policy` gives the share count a TIGHTER budget
#: than an ordinary fact -- 4 months quarterly, 12 annual -- because a stale
#: count mis-scales the whole market cap rather than degrading one factor.
STALENESS_CONCEPT = "shares_outstanding"


@dataclass(frozen=True)
class ShareCandidate:
    """One candidate source for `SharesOutstandingKnownAt_t`, with its verdict.

    `economically_correct` is the field that matters and it is separate from
    `in_store` on purpose: the work order asks which candidates are correct and
    which merely LOOK better, and a tag that raises coverage while changing the
    measured quantity is the second kind. A candidate may be present, well
    covered, and still refused.
    """

    key: str
    taxonomy: str
    tag: str
    unit: str
    dates_at: str
    point_in_time: bool
    economically_correct: bool
    reconstructs_market_cap: bool
    rows_in_store: int
    entities_in_store: int
    meaning: str
    distortions: str
    verdict: str

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


SHARE_CANDIDATES: tuple[ShareCandidate, ...] = (
    ShareCandidate(
        key="dei_cover_outstanding",
        taxonomy="dei", tag=TAG_COVER, unit="shares",
        dates_at="cover_page_date (weeks AFTER period end)",
        point_in_time=True, economically_correct=True,
        reconstructs_market_cap=True,
        rows_in_store=606, entities_in_store=150,
        meaning=("Shares outstanding as of the COVER PAGE date -- the count the "
                 "registrant certifies at filing time, typically 2-6 weeks after "
                 "period end. The freshest defensible count that exists."),
        distortions=("Undimensioned only. A multi-class issuer tags this once PER "
                     "CLASS, and both DERA and the companyfacts API drop dimensional "
                     "facts, so the tag VANISHES for exactly the issuers whose share "
                     "count is hardest. Alphabet has zero rows of it."),
        verdict=("CORRECT AND ABSENT. The designed lead rung. 606 rows / 150 entities "
                 "in a 14.07M-row archive; resolves freshly for 11 / 1 / 1 entities at "
                 "the three dates. Its scarcity is an artefact of the DERA compact "
                 "dataset, NOT of the SEC -- a targeted fetch recovers it."),
    ),
    ShareCandidate(
        key="gaap_balance_sheet_outstanding",
        taxonomy="us-gaap", tag=TAG_GAAP_OUTSTANDING, unit="shares",
        dates_at="balance_sheet_date",
        point_in_time=True, economically_correct=True,
        reconstructs_market_cap=True,
        rows_in_store=589989, entities_in_store=12689,
        meaning=("The same quantity as the cover page, measured at the balance-sheet "
                 "instant instead -- older by the filing lag, and identical in kind."),
        distortions=("Dated at period end, so it is ~91 days old at every month-end "
                     "as-of (measured median 91/89/89 days). Issuers drift off it: "
                     "Walmart tagged it 6 times, all before 2013, while tagging the "
                     "cover count 70 times through 2026. Berkshire never tags it."),
        verdict=("CORRECT AND THE ONLY WORKING RUNG. Carries essentially all of the "
                 "store's share coverage: 55.41% / 55.08% / 51.91% of peers fresh."),
    ),
    ShareCandidate(
        key="gaap_balance_sheet_issued",
        taxonomy="us-gaap", tag=TAG_GAAP_ISSUED, unit="shares",
        dates_at="balance_sheet_date",
        point_in_time=True, economically_correct=False,
        reconstructs_market_cap=False,
        rows_in_store=0, entities_in_store=0,
        meaning=("Shares ISSUED, which INCLUDES shares held in treasury. Equal to "
                 "outstanding only for an issuer that retires what it repurchases."),
        distortions=("An UPPER BOUND, and an unbounded one: a serial repurchaser that "
                     "holds treasury stock can carry issued far above outstanding, so "
                     "substituting it overstates market cap by an issuer-specific and "
                     "unknowable factor. Apple's issued equals its outstanding (it "
                     "retires); that coincidence is not a rule."),
        verdict=("REFUSED AS A SUBSTITUTE, AND ABSENT ANYWAY. Zero rows in the store. "
                 "Worth fetching ONLY as the second half of a treasury-stock "
                 "diagnostic (issued minus outstanding), never as a count."),
    ),
    ShareCandidate(
        key="gaap_weighted_average_diluted",
        taxonomy="us-gaap", tag=TAG_WEIGHTED_DILUTED, unit="shares",
        dates_at="period_end_of_an_AVERAGING_WINDOW",
        point_in_time=False, economically_correct=False,
        reconstructs_market_cap=False,
        rows_in_store=696357, entities_in_store=10335,
        meaning=("A DURATION average over the reporting period, built for earnings "
                 "per share, and inflated by the treasury-stock method to include "
                 "options, RSUs and convertibles that are not outstanding shares."),
        distortions=("It is not a count on any date. For an issuer that halved its "
                     "share base mid-period the average names a share count that never "
                     "existed. Measured against the balance-sheet count at the three "
                     "dates, the ratio outstanding/diluted has median 1.00 but a 10th "
                     "percentile of 0.93 / 0.91 / 0.89 and a long low tail."),
        verdict=("NEVER A SUBSTITUTE. Measured here only so that 'only a period "
                 "average exists' is a number: 9.91% / 8.74% / 10.19% of peers. Those "
                 "are UNAVAILABLE, and the frozen drop-and-renormalise rule applies. "
                 "Using it would raise apparent coverage by ~10pp and silently change "
                 "the measured quantity -- the exact shortcut this study refuses."),
    ),
    ShareCandidate(
        key="gaap_issued_minus_treasury",
        taxonomy="us-gaap", tag="CommonStockSharesIssued - TreasuryStockShares",
        unit="shares",
        dates_at="balance_sheet_date (BOTH legs, same instant)",
        point_in_time=True, economically_correct=True,
        reconstructs_market_cap=True,
        rows_in_store=0, entities_in_store=0,
        meaning=("Outstanding = issued MINUS shares held in treasury. Not an "
                 "approximation and not a proxy -- it is the accounting identity "
                 "that DEFINES outstanding, so where both legs are tagged on the "
                 "same instant the result is the count itself."),
        distortions=(
            "Three traps, all measured. (1) BOTH LEGS OR NOTHING: the identity needs "
            "the same instant on both sides, and treasury is tagged less often than "
            "issued (measured: 56-57 treasury instants against 69-70 issued for the "
            "same issuers), so coverage is the INTERSECTION, never the union. "
            "(2) A TAG CHANGE MID-HISTORY: us-gaap:TreasuryStockShares runs to "
            "~2023 and us-gaap:TreasuryStockCommonShares takes over from ~2020-2022; "
            "a ladder naming only the first silently loses the recent era and only "
            "the second loses everything before it. (3) Treasury held against a "
            "DIFFERENT class than the issued figure reintroduces the multi-class "
            "problem, and some issuers tag treasury at COST in USD rather than in "
            "shares -- the unit must be checked, not assumed."),
        verdict=(
            "CORRECT, ABSENT FROM THE STORE, AND THE BEST UNTAPPED RUNG. Neither leg "
            "was ingested. It is the only candidate here that recovers issuers with "
            "NO outstanding tag at all: Coca-Cola (CIK 21344) has zero "
            "CommonStockSharesOutstanding rows in companyfacts and 69 issued plus 57 "
            "treasury spanning 2008-2026, and CIK 37785 the same shape. Those are "
            "not marginal names. Recommended for the targeted fetch as a THIRD rung "
            "below the two direct counts -- never above them, because a reconstructed "
            "count inherits the error of both legs."),
    ),
    ShareCandidate(
        key="dei_public_float",
        taxonomy="dei", tag=TAG_PUBLIC_FLOAT, unit="USD",
        dates_at="prior_fiscal_q2_last_business_day",
        point_in_time=True, economically_correct=False,
        reconstructs_market_cap=False,
        rows_in_store=130, entities_in_store=78,
        meaning=("A USD MARKET VALUE, not a count: the aggregate value of shares held "
                 "by NON-AFFILIATES, disclosed annually as of the last business day of "
                 "the prior fiscal second quarter."),
        distortions=("Excludes affiliate and insider holdings, so it understates market "
                     "cap by an amount that is largest exactly where it matters -- "
                     "founder-controlled and dual-class issuers. Annual, and up to 15 "
                     "months stale at use. It also cannot be re-priced: it is a value "
                     "struck on ITS date, so it can never be multiplied by Price_t."),
        verdict=("GENUINELY POINT-IN-TIME AND STILL WRONG FOR THIS PURPOSE. It is a "
                 "stale market value, not a share count, and a market cap built from it "
                 "would be a different quantity wearing the right name. 130 rows."),
    ),
)

_BY_KEY = {c.key: c for c in SHARE_CANDIDATES}


def candidate_for(key: str) -> ShareCandidate:
    """One candidate by key. Raises on an unknown key rather than guessing."""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(
            f"unknown share candidate {key!r}; known: {', '.join(sorted(_BY_KEY))}"
        ) from None


def candidate_spec() -> dict[str, Any]:
    """The whole candidate verdict table as a JSON-serialisable record."""
    return {
        "module": "pit_sharecoverage",
        "staleness_concept": STALENESS_CONCEPT,
        "max_age_months": {
            "quarterly": pit_policy.max_age_months(0, STALENESS_CONCEPT),
            "annual": pit_policy.max_age_months(4, STALENESS_CONCEPT),
        },
        "study_dates": list(STUDY_DATES),
        "candidates": [c.as_dict() for c in SHARE_CANDIDATES],
        "refused_substitutions": [
            c.key for c in SHARE_CANDIDATES if not c.economically_correct],
    }


# --------------------------------------------------------------------------
# Read-only access. A writer may be active; every transaction here is short.
# --------------------------------------------------------------------------

def connect_readonly(db_path: str) -> sqlite3.Connection:
    """Open the store in SQLite's enforced read-only mode.

    `mode=ro` is not politeness -- it is the guarantee that a study cannot
    write, so the rule 'pit_feature and pit_score stay empty' is held by the
    file handle rather than by the author's care. It also means this process
    can never hold the write lock away from a live ingest.
    """
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def peer_universe(conn: sqlite3.Connection, as_of: str) -> set[int]:
    """Every entity in any peer cohort on `as_of`.

    PeerUniverse_PIT, and deliberately independent of PricedUniverse_PIT: a
    company with no price is still a peer if it legitimately existed then.
    """
    return {int(r[0]) for r in conn.execute(
        """SELECT DISTINCT m.entity_id
             FROM pit_peer_member m
             JOIN pit_peer_set s ON s.peer_set_id = m.peer_set_id
            WHERE s.as_of_date = ?""", (as_of,))}


_SHARE_TAGS = (TAG_COVER, TAG_GAAP_OUTSTANDING, TAG_WEIGHTED_DILUTED,
               TAG_PUBLIC_FLOAT)


def share_facts(conn: sqlite3.Connection, entity_id: int,
                tags: Sequence[str] = _SHARE_TAGS) -> dict[str, list[tuple]]:
    """Every share-shaped fact this entity ever filed, grouped by tag.

    Queried PER ENTITY on purpose. `idx_pit_fact_select` leads with
    `entity_id`, so an entity-keyed lookup is an index seek while a
    `WHERE tag = ?` scan costs a full pass over 14M rows (measured: 34s each).
    The whole peer union of 12,236 entities takes 157s this way.
    """
    out: dict[str, list[tuple]] = {}
    sql = ("SELECT tag, period_end, available_date, filed, form, qtrs, val, unit, accn "
           "FROM pit_fact WHERE entity_id = ? AND tag IN (%s)"
           % ",".join("?" * len(tags)))
    for row in conn.execute(sql, (entity_id,) + tuple(tags)):
        out.setdefault(row[0], []).append(tuple(row[1:]))
    return out


def select_share_pit(rows: Iterable[tuple], as_of: str) -> Optional[tuple]:
    """The one observation readable on `as_of`, or None.

    Rows are `(period_end, available_date, filed, form, qtrs, val, unit, accn)`
    as `share_facts` returns them.

    INVARIANT: nothing is knowable before it was available. A row is eligible
    only when BOTH `available_date <= as_of` (it had been published) and
    `period_end <= as_of` (it had happened). Among the eligible, the newest
    period wins; ties -- a restatement of the SAME instant -- go to the later
    availability and then the later filing, so a restated count supersedes the
    original only once the restatement itself is public. That ordering is what
    keeps Apple's 4:1 2020 split out of a 2020-01-31 as-of.
    """
    best_key = None
    best_row = None
    for row in rows:
        period_end, available_date, filed = row[0], row[1], row[2]
        if available_date > as_of or period_end > as_of:
            continue
        key = (period_end, available_date, filed)
        if best_key is None or key > best_key:
            best_key, best_row = key, row
    return best_row


def _age_days(period_end: str, as_of: str) -> int:
    return (_dt.date.fromisoformat(as_of) - _dt.date.fromisoformat(period_end)).days


def coverage_at(conn: sqlite3.Connection, as_of: str,
                entity_ids: Optional[Iterable[int]] = None) -> dict[str, Any]:
    """Per-candidate coverage of the peer universe on one as-of date.

    Counts are reported BOTH fresh and stale-accepted, because the difference
    is the whole argument: the balance-sheet count exists for ~76-79% of peers
    and is USABLE for ~52-55%, and a report that gave only one of those numbers
    would be arguing for a conclusion instead of measuring.
    """
    ids = set(entity_ids) if entity_ids is not None else peer_universe(conn, as_of)
    n = len(ids)
    tally = {k: 0 for k in (
        "cover_any", "cover_fresh", "gaap_any", "gaap_fresh",
        "ladder_fresh", "ladder_any", "float_any", "diluted_any",
        "diluted_fresh", "only_period_average", "only_public_float",
        "zero_count", "no_share_fact_at_all", "stale_only")}
    ages: list[int] = []
    for entity_id in sorted(ids):
        by_tag = share_facts(conn, entity_id)
        picked: dict[str, Any] = {}
        for tag, rows in by_tag.items():
            sel = select_share_pit(rows, as_of)
            if sel is None:
                continue
            period_end, _avail, _filed, _form, qtrs, val = sel[0], sel[1], sel[2], sel[3], sel[4], sel[5]
            picked[tag] = {
                "period_end": period_end, "val": val,
                "age_days": _age_days(period_end, as_of),
                "stale": bool(pit_policy.is_stale(period_end, as_of, qtrs,
                                                  STALENESS_CONCEPT)),
            }
        cover, gaap = picked.get(TAG_COVER), picked.get(TAG_GAAP_OUTSTANDING)
        diluted, flt = picked.get(TAG_WEIGHTED_DILUTED), picked.get(TAG_PUBLIC_FLOAT)
        cover_fresh = bool(cover and not cover["stale"])
        gaap_fresh = bool(gaap and not gaap["stale"])
        tally["cover_any"] += bool(cover)
        tally["cover_fresh"] += cover_fresh
        tally["gaap_any"] += bool(gaap)
        tally["gaap_fresh"] += gaap_fresh
        tally["float_any"] += bool(flt)
        tally["diluted_any"] += bool(diluted)
        tally["diluted_fresh"] += bool(diluted and not diluted["stale"])
        tally["zero_count"] += bool((cover and cover["val"] == 0)
                                    or (gaap and gaap["val"] == 0))
        if cover_fresh or gaap_fresh:
            tally["ladder_fresh"] += 1
            # Ladder order, not "whichever is fresher": the cover page leads
            # because it is the same quantity measured LATER, so when both are
            # usable the cover page's age is the one the replay would carry.
            ages.append(cover["age_days"] if cover_fresh else gaap["age_days"])
        if cover or gaap:
            tally["ladder_any"] += 1
            if not (cover_fresh or gaap_fresh):
                tally["stale_only"] += 1
        elif diluted:
            tally["only_period_average"] += 1
        elif flt:
            # Only a USD market value, and an annual, up-to-15-month-stale one.
            # Its own bucket: folding it into "no share fact" would hide a real
            # (if unusable) disclosure, and folding it into the period-average
            # bucket would merge two different refusals.
            tally["only_public_float"] += 1
        else:
            tally["no_share_fact_at_all"] += 1
    ages.sort()
    return {
        "as_of_date": as_of,
        "n_peers": n,
        "counts": tally,
        "pct": {k: round(100.0 * v / n, 2) if n else 0.0 for k, v in tally.items()},
        "age_at_use_days": {
            "n": len(ages),
            "min": ages[0] if ages else None,
            "median": ages[len(ages) // 2] if ages else None,
            "max": ages[-1] if ages else None,
        },
    }


# --------------------------------------------------------------------------
# Multi-class. The silent 2x, and the era in which it is invisible.
# --------------------------------------------------------------------------

#: A title naming a non-equity instrument. Tested FIRST, because
#: "Warrants to purchase Common Stock" contains the word "common".
_NON_EQUITY = re.compile(
    r"warrant|units?\b|rights?\b|preferred|depositar|notes?\b|debenture|bond|"
    r"subordinat|purchase contract|%|due 20|senior|capital securit|"
    r"trust securit|contingent value", re.I)
_IS_COMMON = re.compile(
    r"common|ordinary|beneficial interest|limited partnership|membership interest", re.I)
_CLASS = re.compile(r"\bclass\s+([A-Za-z0-9]{1,3})\b", re.I)


def classify_security_title(title: Optional[str]) -> Optional[str]:
    """Which common-equity CLASS a `dei:Security12bTitle` names, or None.

    13,565 distinct spellings exist in `pit_symbol_obs` for what is
    economically a handful of instruments, so this is a classifier and not a
    lookup. Returning None means "not common equity" -- never "unknown class":
    an unparseable common-stock title returns `unclassed`, which still counts
    as one class, because collapsing it to None would make a single-class
    issuer look like a no-class one.
    """
    if not title:
        return None
    if _NON_EQUITY.search(title):
        return None
    if not _IS_COMMON.search(title):
        return None
    match = _CLASS.search(title)
    return ("class_" + match.group(1).upper()) if match else "unclassed"


#: How far back a cover page still describes the issuer's live capital
#: structure. Matches `pit_shares.REPORTING_WINDOW_DAYS`.
REPORTING_WINDOW_DAYS = 400


def multiclass_at(conn: sqlite3.Connection, as_of: str,
                  window_days: int = REPORTING_WINDOW_DAYS) -> dict[str, Any]:
    """Multi-class detection on one as-of date, and its blindness.

    `detectable` is reported beside `proved_multi_class` because the ratio of
    the two IS the era effect: `dei:Security12bTitle` does not exist before the
    2019 cover-page rule, so before it the denominator is zero and the answer
    is not "no multi-class issuers" but "no instrument". A study that reported
    only the numerator would read 2015 as a clean year.
    """
    low = (_dt.date.fromisoformat(as_of)
           - _dt.timedelta(days=window_days)).isoformat()
    per: dict[int, dict[str, set]] = {}
    for entity_id, symbol, title in conn.execute(
            """SELECT entity_id, symbol, security_title FROM pit_symbol_obs
                WHERE available_date <= ? AND filed >= ? AND filed <= ?""",
            (as_of, low, as_of)):
        rec = per.setdefault(int(entity_id), {"sym": set(), "cls": set(), "titled": set()})
        rec["sym"].add(symbol)
        if title:
            rec["titled"].add(title)
            cls = classify_security_title(title)
            if cls:
                rec["cls"].add(cls)
    filers = len(per)
    detectable = sum(1 for v in per.values() if v["titled"])
    multi = sum(1 for v in per.values() if len(v["cls"]) >= 2)
    multi_symbol = sum(1 for v in per.values() if len(v["sym"]) >= 2)
    return {
        "as_of_date": as_of,
        "filers_in_window": filers,
        "detectable": detectable,
        "detectable_pct": round(100.0 * detectable / filers, 2) if filers else 0.0,
        "proved_multi_class": multi,
        "multi_class_pct_of_detectable": (
            round(100.0 * multi / detectable, 2) if detectable else None),
        "multi_symbol_proxy": multi_symbol,
        "blind": detectable == 0,
    }


# --------------------------------------------------------------------------
# The decisive question: what would a targeted fetch cost, and what would it buy
# --------------------------------------------------------------------------

def fetch_scope(conn: sqlite3.Connection,
                include_fact_scan: bool = False) -> dict[str, Any]:
    """How many companyfacts requests each candidate scope actually needs.

    One request serves EVERY as-of date for that issuer, so the request count
    is a count of ENTITIES and never of (entity, date) pairs -- the difference
    between 6,815 and 10,246 in the three-date study, and far larger across the
    165-date replay grid.

    `include_fact_scan` is off by default and is not a convenience flag.
    `pit_fact` has no index on `tag`, so the one line it adds costs a full
    covering-index pass over 14,072,934 rows -- measured at 34 seconds. The
    other two lines are metadata. A caller that does not need the third should
    not pay for it, and a caller that does should know it is paying.
    """
    one = lambda sql: int(conn.execute(sql).fetchone()[0])
    scope: dict[str, Any] = {
        "pit_entity_total": one("SELECT COUNT(*) FROM pit_entity"),
        "peer_cohort_entities": one(
            "SELECT COUNT(DISTINCT entity_id) FROM pit_peer_member"),
        "entities_with_a_gaap_count_anywhere": None,   # UNKNOWN unless scanned
    }
    if include_fact_scan:
        scope["entities_with_a_gaap_count_anywhere"] = one(
            "SELECT COUNT(DISTINCT entity_id) FROM pit_fact "
            "WHERE tag = '%s'" % TAG_GAAP_OUTSTANDING)
    return scope


#: Everything measured live on 2026-09-21. Gathered here rather than left in a
#: transcript so a later reader can price the work without re-fetching, and can
#: see the sample size behind every number. UNKNOWN is written as None and is
#: never written as 0 -- an unmeasured quantity reported as zero is how a disk
#: fills.
MEASURED: dict[str, Any] = {
    "measured_on": "2026-09-21",
    "store_bytes": 8965951488,
    "volume_free_bytes": 11217866752,
    "tags_in_pit_fact": 37,
    "companyfacts_probe": {
        "n": 60,
        "scope": "peers with NO fresh PIT count at a sampled as-of date",
        "http_200": 60,
        "fresh_count_recovered": 29,
        "fresh_count_recovered_pct": 48.3,
        "recovered_by_cover_tag": 29,
        "recovered_by_balance_sheet_tag": 3,
        "stale_count_only": 24,
        "no_share_fact_at_all": 7,
        "wire_bytes_gzip": {"min": 5181, "median": 141892,
                            "mean": 148230, "max": 346381},
        "decompressed_bytes": {"min": 39703, "median": 2032400,
                               "mean": 2098762, "max": 5164868},
        "retained_rows_three_count_concepts": {"min": 0, "median": 104,
                                               "mean": 124, "max": 328},
    },
    "bytes_per_stored_row": 481.0,
    "bytes_per_row_method": ("200,000 synthetic pit_share_obs rows on a throwaway "
                             "database, VACUUMed, including the UNIQUE index and "
                             "idx_pit_share_select"),
    "wal_peak_bytes_200k_rows_no_contention": 26742952,
    "wal_peak_bytes_under_concurrent_reader": None,   # UNKNOWN, see rule 8

    # ONE DERA quarter re-read live, streamed, retained nothing. This is the
    # only measurement that prices the multi-class fix, because companyfacts
    # cannot supply it at any request count: the API returns undimensioned
    # facts only, which is the SAME blindness as the ingest filter.
    "dera_quarter_probe": {
        "quarter": "2024q2",
        "zip_bytes": 119119954,
        "num_txt_bytes": 482341379,
        "num_txt_rows": 3426170,
        "undimensioned": {"CommonStockSharesOutstanding": 11203,
                          "CommonStockSharesIssued": 11455,
                          "EntityCommonStockSharesOutstanding": 3},
        "dimensional": {"CommonStockSharesOutstanding": 17851,
                        "CommonStockSharesIssued": 10579,
                        "EntityCommonStockSharesOutstanding": 5},
        "accessions_dimensional_only": 1094,
        "note": ("Two separate losses, and only one of them was deliberate. "
                 "(a) DIMENSIONAL: 17,851 per-class CommonStockSharesOutstanding "
                 "rows against 11,203 undimensioned -- MORE than half the share "
                 "rows in the quarter were dropped by the consolidated-only "
                 "filter, and 1,094 accessions lost their share count entirely "
                 "because they had no undimensioned row at all. (b) TAG_FILTER: "
                 "CommonStockSharesIssued has 11,455 UNDIMENSIONED rows in this "
                 "one quarter and zero in the store -- it was never in "
                 "pit_dera.TAG_FILTER. That one is recoverable by re-reading "
                 "the archive, with no new source and no purchase."),
    },
    "dera_archive_bytes_all_quarters": None,   # UNKNOWN: only 2024q2 measured

    # An UNRESOLVED disagreement with an earlier phase, recorded rather than
    # smoothed over. Two of three dates reproduce and the third does not, which
    # is the pattern of a changed DEFINITION, not of a changed table -- so the
    # two "defensible share" series are not comparable and must not be read as
    # a trend.
    # THE JOIN. A share count is only half of a market cap, and the other half
    # is the survivor-only price sample -- so these are the numbers that say
    # what MarketCap_t can actually be computed on, and they carry the
    # survivorship label that every price-derived result in this store carries.
    # WHICH ENDPOINT. docs/HISTORICAL-DATA-MAP.md sec 2.1.4 specifies
    # `companyconcept`; measured, `companyfacts` is strictly better and the
    # difference is a factor of five in requests against a 10/s ceiling.
    "endpoint_choice": {
        "companyconcept_requests_per_issuer": 5,   # one per concept
        "companyfacts_requests_per_issuer": 1,     # all concepts in one payload
        "companyconcept_requests_for_6815_issuers": 34075,
        "companyfacts_requests_for_6815_issuers": 6815,
        "minutes_at_10_per_second": {"companyconcept": 56.8, "companyfacts": 11.4},
        "why_companyfacts": (
            "One payload carries every concept, so the five-concept ladder costs one "
            "request instead of five. companyconcept also 404s where it is needed "
            "most -- Alphabet returns 404 on dei:EntityCommonStockSharesOutstanding "
            "because its cover count is tagged per class -- and a 404 that means "
            "'dimensional' is indistinguishable from one that means 'never filed'. "
            "companyfacts at least lets the absence be seen beside the tags that DID "
            "resolve for the same issuer. The bytes are the price: a companyfacts "
            "payload is a whole company's history, mean 2.10 MB decompressed, which "
            "is why it must be filtered in flight and never stored whole."),
    },

    "market_cap_reachability": {
        "sample_scope": "SURVIVOR_ONLY_DIAGNOSTIC",
        "sample_scope_note": (
            "Market cap needs a price, every price here comes from the 2,574-listing "
            "sample, and every listing in it is alive in 2026. A market-cap coverage "
            "rate measured on it answers 'among survivors', never 'in the market' -- "
            "and the dead issuers it omits are exactly the ones whose share counts "
            "are hardest, so the true coverage is LOWER than these numbers."),
        "entities_with_any_company_listing": 2542,
        "fresh_count_and_a_listing": {"2015-06-30": 1261, "2019-06-28": 1583,
                                      "2024-06-28": 1619},
        "pct_of_listed_entities": {"2015-06-30": 49.6, "2019-06-28": 62.3,
                                   "2024-06-28": 63.7},
        "split_in_count_to_asof_window": {"2015-06-30": 11, "2019-06-28": 22,
                                          "2024-06-28": 22},
        "split_contaminated_pct": {"2015-06-30": 0.87, "2019-06-28": 1.39,
                                   "2024-06-28": 1.36},
        "split_note": (
            "Small, and not negligible: a dirty window is not a rounding error but a "
            "whole-number factor (Apple's 4:1 is a 4x error in market cap). These "
            "must be un-applied or the row refused -- never silently assumed 1.0."),
    },

    "symbol_ceiling_reconciliation": {
        "prior_recorded": {"2015-06-30": 43.61, "2019-06-28": 63.18,
                           "2024-06-28": 68.10},
        "this_study_symbol_observed_by_as_of": {"2015-06-30": 44.15,
                                                "2019-06-28": 63.73,
                                                "2024-06-28": 92.71},
        "gates_tried_at_2024": {"ever_by_as_of": 92.71, "within_400_days": 86.14,
                                "within_200_days": 81.26,
                                "excluding_dera_notes_2024_quarters": 90.47,
                                "excluding_dera_notes_from_2023q3": 88.78},
        "status": ("UNRESOLVED. 2015 and 2019 reproduce to within 0.6pp. 2024 does "
                   "not, and the 'a later symbol ingest added rows' hypothesis was "
                   "TESTED AND REJECTED -- removing every dera_notes quarter from "
                   "2023q3 on still leaves 88.78%, nowhere near 68.10%. The gate "
                   "behind 68.10% is therefore something other than symbol "
                   "observation (most likely a listing-RESOLUTION gate), and it is "
                   "UNKNOWN to this study."),
        "consequence": ("This study's defensible-share numbers -- 26.04 / 36.16 / "
                        "48.45% -- are stated against the gate named here. Do NOT "
                        "compare them with 25.35 / 35.20 / 41.35% and read an "
                        "improvement; at 2024 the two measure different things."),
    },
}


def fetch_cost(n_requests: int,
               measured: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Price a targeted companyfacts fetch of `n_requests` issuers.

    Every figure traces to a measured per-request quantity in `MEASURED`.
    `peak_temporary_bytes` is ONE payload, not the whole fetch, and that is the
    design claim being priced: the payload is filtered in flight to five
    concepts and never lands whole, so the 24 GB of decompressed JSON this
    scope would otherwise imply never exists on a volume with 10.45 GiB free.
    """
    m = measured or MEASURED
    probe = m["companyfacts_probe"]
    download = n_requests * probe["wire_bytes_gzip"]["mean"]
    rows = n_requests * probe["retained_rows_three_count_concepts"]["mean"]
    retained = rows * m["bytes_per_stored_row"]
    return {
        "n_requests": n_requests,
        "download_bytes_streamed": int(download),
        "download_mib": round(download / 1048576, 1),
        "decompressed_bytes_if_ever_materialised": int(
            n_requests * probe["decompressed_bytes"]["mean"]),
        "peak_temporary_bytes": probe["decompressed_bytes"]["max"],
        "expected_rows": int(rows),
        "retained_bytes": int(retained),
        "retained_mib": round(retained / 1048576, 1),
        "wal_exposure_bytes": m["wal_peak_bytes_200k_rows_no_contention"],
        "wal_exposure_note": ("Measured with NO concurrent reader. A long-lived read "
                             "snapshot blocks the checkpointer and the bound does not "
                             "hold -- read the return of PRAGMA wal_checkpoint(TRUNCATE) "
                             "every time; it reports busy and does nothing silently."),
        "memory_bytes_expected": probe["decompressed_bytes"]["max"] * 5,
        "memory_note": "one payload decompressed, times a JSON-parse overhead factor",
        "seconds_at_10_per_second": round(n_requests / 10.0, 1),
        "vacuum_possible": False,
        "vacuum_note": ("VACUUM needs a second copy of the whole 8.35 GiB file against "
                        "10.45 GiB free, leaving 2.10 GiB. Not attempted."),
    }


def multiclass_verdict() -> dict[str, Any]:
    """The honest answer for a multi-class issuer, with its measurements.

    Stated as a function rather than a comment because it is a POLICY: the
    undimensioned count for a proved multi-class issuer must be treated as
    UNVERIFIED, never as the consolidated total. The measurements behind that
    are a 2024-06-28 cross-tab -- the only era in which multi-class membership
    is knowable at all.
    """
    return {
        "as_of_measured": "2024-06-28",
        "peers_with_a_detectable_class": 6257,
        "proved_multi_class": 443,
        "proved_multi_class_pct": 7.08,
        "has_undimensioned_count": {"multi_class": 204, "multi_class_pct": 46.0,
                                    "single_class": 4185, "single_class_pct": 80.1},
        "coverage_penalty_pp": 34.1,
        "outstanding_over_diluted_below_0_75": {
            "multi_class_pct": 20.4, "single_class_baseline_pct": 5.6,
            "excess_pp": 14.8,
            "why": ("For a single-class issuer the two should be close; the 5.6% "
                    "baseline is genuine dilution, buybacks and convertibles. The "
                    "14.8pp excess is the share of multi-class issuers whose "
                    "undimensioned count is measurably NOT the consolidated base "
                    "-- roughly 30 of the 204."),
        },
        "the_error_is_not_symmetric": (
            "The feared failure was a silent 2x OVERSTATEMENT from summing classes "
            "wrongly. That one cannot happen here: per-class rows never reached the "
            "store, so nothing is summed. The error that DOES happen is the mirror "
            "image -- one class taken as the whole company, an UNDERSTATEMENT, and "
            "the measured 10th percentile of outstanding/diluted for multi-class "
            "issuers is 0.18, a 5.5x understatement of market cap."),
        "honest_answer": (
            "1. 54% of proved multi-class issuers get no count at all -- correctly "
            "unavailable; valuation drops and the frozen weights renormalise. "
            "2. Of the 46% that do, about one in five is demonstrably not the "
            "consolidated base, and the store cannot say WHICH. "
            "3. Therefore the count is UNVERIFIED for every proved multi-class "
            "issuer, and market cap must not be published for them on this data."),
        "why_the_fetch_cannot_fix_it": (
            "companyfacts returns undimensioned facts only -- the same blindness as "
            "the ingest filter, at a different layer. Alphabet has ZERO rows of "
            "dei:EntityCommonStockSharesOutstanding there. No number of requests "
            "changes that. The only sources that carry per-class counts are DERA's "
            "dimensional num.txt rows (measured: 17,851 per-class rows in 2024q2 "
            "alone) and the raw XBRL instance documents."),
        "pre_2019": (
            "Unknowable. dei:Security12bTitle has zero rows before 2019 and the "
            "pre-2019 cover pages are 82,241/82,633 undimensioned -- one symbol, no "
            "class dimension. Multi-class membership at 2015-06-30 cannot be "
            "established from this store by any query."),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the study as JSON. Read-only; writes nothing anywhere."""
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "shafferfineval_pit.db"))
    parser.add_argument("--dates", nargs="*", default=list(STUDY_DATES))
    parser.add_argument("--spec-only", action="store_true")
    args = parser.parse_args(argv)
    if args.spec_only:
        print(json.dumps(candidate_spec(), indent=1, sort_keys=True))
        return 0
    conn = connect_readonly(args.db)
    try:
        out = {"spec": candidate_spec(),
               "scope": fetch_scope(conn, include_fact_scan=True),
               "multiclass": [multiclass_at(conn, d) for d in args.dates],
               "multiclass_verdict": multiclass_verdict(),
               "cost": {str(n): fetch_cost(n) for n in (6815, 16148)}}
    finally:
        conn.close()
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
