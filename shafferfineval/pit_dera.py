"""SEC DERA "Financial Statement Data Sets" -- estimator and bulk ingest.

The DERA quarterly archive is the only affordable way to assemble a
point-in-time fundamentals history for the whole US cross-section. EDGAR's
`companyfacts` endpoint is one HTTP request PER COMPANY; at ~6,500 filers and
<=10 requests a second that is a bounded but slow walk, and every payload
carries the `frame` field, which always holds the LATEST restated value. DERA
publishes the same XBRL observations pre-joined, one ZIP per quarter, with no
`frame` at all -- so it is both cheaper and structurally safer.

    companyfacts -> one issuer, every vintage, one request each.  SPOT source.
    DERA (here)  -> every issuer, one quarter, one 90 MB ZIP.     BULK source.

THIS MODULE MEASURES BEFORE IT MOVES. `estimate_ingest()` answers "what will
this cost" from numbers that were actually measured -- a HEAD of every quarter
in the archive and a full parse of one representative quarter -- rather than
from a guess. `ingest_quarter()` is the real bulk path and is deliberately not
wired to a loop over the archive: the owner runs the archive, this module only
makes one quarter cheap and idempotent.

Why the tag filter is the whole game
------------------------------------
`tag.txt` in 2021q2 lists 97,000 tags, of which 86,781 (89.5%) are company
extensions -- one filer's private name for one line item, usable by nobody
else. `num.txt` holds 2,833,915 rows. Keeping the 38 standard tags the concept
ladders in `pit_policy` actually read, consolidated rows only, leaves 216,702 --
202,241 once the filter is narrowed to periodic reports. A 14.0x reduction,
measured, not assumed. Everything about the affordability of this ingest
follows from that ratio: 5.3 GiB of source ZIPs become a 4.4 GiB database
holding 12.6 million facts, in about 32 minutes.

What is deliberately dropped
----------------------------
* `prevrpt` -- sub.txt's "this submission was later amended" flag. It is
  computed from the FUTURE. Note that the rows are KEPT: an amended original is
  exactly the vintage a point-in-time replay must see. Only the flag is dropped,
  because a filter on it would be hindsight survivorship.
* `fy` / `fp` -- they describe the FILING, not the fact. `ddate` + `qtrs` are
  the fact's period.
* `pre.txt` and `tag.txt` -- presentation and label metadata, 120 MB a quarter,
  and nothing in the replay reads either.
* every non-`us-gaap` / non-`dei` taxonomy. See IFRS_COLLISION_NOTE.

Nothing here writes to `storage.py`'s production tables.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import sqlite3
import sys
import time
import zipfile
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

import pit_policy
import pit_store

# --------------------------------------------------------------------------
# Source
# --------------------------------------------------------------------------

DERA_URL = ("https://www.sec.gov/files/dera/data/financial-statement-data-sets/"
            "{quarter}.zip")

#: Required on every .gov request. Never a browser UA on an SEC host.
SEC_USER_AGENT = "ShafferFinEval/1.0 (loganshaffer87@gmail.com)"

#: SEC's published ceiling is 10 requests a second. 0.15s is ~6.7/s with room
#: for clock jitter, and nothing here is latency-sensitive.
SEC_MIN_INTERVAL = 0.15

HTTP_TIMEOUT = 300

#: Where downloaded quarters land. Deliberately NOT
#: `pit_store.DEFAULT_ARCHIVE_DIR`, which is the option-chain archive: those
#: files are replayable research evidence and must never be mixed with 5 GB of
#: re-downloadable source ZIPs somebody will one day delete.
DEFAULT_DERA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pit_archive", "dera")

#: The archive as it stood when the sizes below were measured.
ARCHIVE_FIRST_QUARTER = "2009q2"
ARCHIVE_LAST_QUARTER = "2026q2"

#: The approved first scored date for the historical replay.
SCORING_START = "2013-01-01"

#: The BINDING warm-up floor: 12 quarters. To score 2013-01-01 the growth
#: factor needs the latest annual period knowable then plus prior and
#: prior-prior -- FY2011, FY2010, FY2009 for a December filer -- and those
#: three were ORIGINALLY filed in calendar 2012, 2011 and 2010.
WARMUP_MINIMUM_QUARTER = "2010q1"

#: The RECOMMENDED start, and the one `main()` reports: the whole archive.
#: A June or September fiscal-year filer files its FY2009 10-K inside calendar
#: 2009 -- Apple's FY2009 ended 2009-09-26 and was filed 2009-10-27, which is
#: 2009q4. Starting at 2010q1 loses those originals, and the point-in-time
#: selector then falls back to the FY2009 COMPARATIVE inside a later 10-K: a
#: restated number wearing the original's period end. The three extra quarters
#: cost 7,739,909 bytes, 0.14% of the window, so there is no case for the
#: cheaper, blinder option.
RECOMMENDED_FIRST_QUARTER = "2009q2"

#: Read this before quoting any pre-2012 cross-section. XBRL was phased in:
#: fiscal periods ending after 2009-06-15 for domestic large accelerated filers
#: over $5B public float, after 2010-06-15 for all large accelerated filers,
#: and only after 2011-06-15 for everyone else. MEASURED: 2010q1's sub.txt
#: carries 495 submissions from 484 distinct CIKs, against 7,810 from 6,518 in
#: 2021q2 -- 13.5x fewer issuers. The early archive is therefore a LARGE-CAP
#: SAMPLE, not a small universe: a size bias, not survivorship. A peer
#: percentile drawn from 2010's 484 filers is not the same statistic as one
#: drawn from 2021's 6,265, and the warm-up years must never be reported as a
#: cross-section.
XBRL_PHASE_IN_NOTE = (
    "XBRL phase-in: >$5B float from FY periods ending 2009-06-15, all large "
    "accelerated from 2010-06-15, all filers from 2011-06-15. 2010q1 carries "
    "484 distinct CIKs vs 6,518 in 2021q2 -- the warm-up window is a large-cap "
    "sample, not a cross-section."
)

#: Measured 2026-09-20 by HTTP HEAD against every quarter in the archive,
#: reading Content-Length. 2026q3 and later 404. Kept as a constant so
#: `estimate_ingest` runs offline and deterministically; `head_quarter_sizes()`
#: re-measures.
QUARTER_BYTES: dict[str, int] = {
    "2009q2": 144894, "2009q3": 3544077, "2009q4": 4050938,
    "2010q1": 5311282, "2010q2": 3994236, "2010q3": 13745950, "2010q4": 14679781,
    "2011q1": 19615544, "2011q2": 15001310, "2011q3": 57941919, "2011q4": 64306658,
    "2012q1": 85474601, "2012q2": 80075771, "2012q3": 89608234, "2012q4": 95805256,
    "2013q1": 99173442, "2013q2": 96917655, "2013q3": 98355005, "2013q4": 92335773,
    "2014q1": 102843522, "2014q2": 85032839, "2014q3": 87408142, "2014q4": 87639973,
    "2015q1": 98944359, "2015q2": 78908272, "2015q3": 82751989, "2015q4": 83007568,
    "2016q1": 95470843, "2016q2": 73848835, "2016q3": 71116399, "2016q4": 79400707,
    "2017q1": 92433981, "2017q2": 71429748, "2017q3": 74356472, "2017q4": 76051784,
    "2018q1": 93176693, "2018q2": 91972487, "2018q3": 87202498, "2018q4": 62895777,
    "2019q1": 95895180, "2019q2": 90155728, "2019q3": 89829588, "2019q4": 98753299,
    "2020q1": 96584608, "2020q2": 82008710, "2020q3": 87582976, "2020q4": 89811373,
    "2021q1": 97900752, "2021q2": 90864175, "2021q3": 97252395, "2021q4": 102642081,
    "2022q1": 103669919, "2022q2": 97089251, "2022q3": 97589396, "2022q4": 111091525,
    "2023q1": 113974946, "2023q2": 116914392, "2023q3": 117776944, "2023q4": 120272357,
    "2024q1": 124336804, "2024q2": 119119954, "2024q3": 118280418, "2024q4": 122932548,
    "2025q1": 127765057, "2025q2": 78973768, "2025q3": 127842298, "2025q4": 65830170,
    "2026q1": 85259424, "2026q2": 60419016,
}

QUARTER_BYTES_MEASURED_ON = "2026-09-20"


# --------------------------------------------------------------------------
# The tag filter -- built FROM pit_policy, never re-typed beside it
# --------------------------------------------------------------------------

#: Cover-page facts the ladders do not name but the valuation factor needs.
#: `EntityCommonStockSharesOutstanding` is already the first rung of the
#: shares_outstanding ladder; `EntityPublicFloat` is the accelerated-filer
#: threshold and a free float proxy.
#:
#: MEASURED WARNING: DERA's num.txt is built from the FACE FINANCIAL STATEMENTS
#: and carries essentially no cover-page dei facts. 2021q2 holds 3 rows of
#: EntityCommonStockSharesOutstanding in 2,833,915 and ZERO EntityPublicFloat.
#: They stay in the filter because they cost nothing and because a source that
#: silently starts supplying them should be picked up -- but shares outstanding
#: for a point-in-time market cap has to come from companyfacts, which does
#: carry dei. Treat that as a known hole in this source, not as a zero.
DEI_COVER_TAGS = frozenset({
    "EntityCommonStockSharesOutstanding",
    "EntityPublicFloat",
})

DEI_COVERAGE_NOTE = (
    "DERA num.txt carried 3 EntityCommonStockSharesOutstanding rows and 0 "
    "EntityPublicFloat rows in 2021q2 (of 2,833,915). Cover-page dei facts are "
    "a companyfacts responsibility; DERA cannot supply shares outstanding."
)

#: Only these taxonomy prefixes are accepted from num.txt's `version` column.
#: A company extension carries the filer's own accession there, which is how a
#: private tag is told from a standard one without reading tag.txt at all.
ACCEPTED_VERSION_PREFIXES = ("us-gaap/", "dei/")

#: WHY the taxonomy gate is a filter and not just a label. `pit_store.fact_as_of`
#: selects on (entity_id, tag, unit, period_end, qtrs) and does NOT filter on
#: `taxonomy`. IFRS shares local names with us-gaap: 2021q2 has 795 consolidated
#: `ProfitLoss` rows, 604 `Assets`, 403 `GrossProfit` and 207 `InterestExpense`
#: under an `ifrs/*` version. Storing them would let a 20-F filer's IFRS figure
#: answer a us-gaap ladder rung with no error anywhere. Until the selector takes
#: a taxonomy, the safe place to stop it is here.
IFRS_COLLISION_NOTE = (
    "2,009 consolidated rows in 2021q2 carry a us-gaap ladder tag name under an "
    "ifrs/* version (ProfitLoss 795, Assets 604, GrossProfit 403, InterestExpense "
    "207). pit_store.fact_as_of does not filter on taxonomy, so these are "
    "excluded at ingest rather than stored."
)

#: sub.txt columns this ingest refuses to read. See the module docstring.
DROPPED_SUB_COLUMNS = ("prevrpt", "fy", "fp")

#: Periodic reports. The default, because a registration statement's filer CIK
#: is not reliably the economic subject of the facts inside it -- an S-4 filed
#: by an acquirer carries the TARGET's financials -- and because a fundamental
#: score should rest on periodic reporting. 93.33% of 2021q2's retained rows
#: come from these forms; pass `forms=None` to keep everything.
PERIODIC_FORMS = frozenset({
    "10-K", "10-Q", "20-F", "40-F",
    "10-K/A", "10-Q/A", "20-F/A", "40-F/A",
    "10-KT", "10-QT", "10-KT/A", "10-QT/A",
})

#: pit_fact's CHECK constraint. DERA also publishes cumulative periods (qtrs 5,
#: 6, 8, 40) which the ladders have no use for.
ACCEPTED_QTRS = frozenset({0, 1, 2, 3, 4})

#: How far a `ddate` may sit past its filing date before the row is refused.
#:
#: A period cannot end after the filing that reports it, but the month-end
#: rounding makes a small overhang legitimate: a quarter ending 2021-05-26 and
#: filed the same day is published as 20210531, five days "after" `filed`. The
#: maximum honest overhang is 15 days -- a period ending on the 16th of a
#: 31-day month rounds forward to the 31st.
#:
#: MEASURED on 2021q2, the gap distribution splits cleanly at exactly that
#: boundary: 15 rows sit at +1 to +9 days (the rounding artifact) and 12 sit at
#: +40 to +240 days. The second group is a balance sheet dated 2021-12-31
#: inside a 10-Q filed 2021-05-10 -- not a reported period. It is only 12 rows
#: in 202,000, but `pit_store.latest_period_as_of` orders by `period_end DESC`,
#: so ONE of them becomes "the most recent period" for that issuer and stays
#: there for months.
MAX_PERIOD_AHEAD_OF_FILED_DAYS = 15


def _ladder_tags() -> tuple[frozenset[str], frozenset[str]]:
    """Every tag named by `pit_policy`'s concept ladders, split by taxonomy.

    Read from LADDERS at import rather than copied, so a v2 ladder cannot
    silently diverge from what the ingest actually retained. A composite rung
    contributes all of its components -- total debt's first rung needs three
    tags and two of three is a wrong number, not a partial one.
    """
    gaap: set[str] = set()
    dei: set[str] = set()
    for concept in pit_policy.concepts():
        for rung in pit_policy.ladder_for(concept).rungs:
            target = dei if rung.taxonomy == pit_policy.TAXONOMY_DEI else gaap
            target.update(rung.tags)
    return frozenset(gaap), frozenset(dei)


GAAP_TAGS, _LADDER_DEI_TAGS = _ladder_tags()
DEI_TAGS = _LADDER_DEI_TAGS | DEI_COVER_TAGS

#: THE filter. 38 tags against tag.txt's 97,000 -- and against the 10,219 that
#: are standard rather than company extensions. See `tag_filter_saving()`.
TAG_FILTER: frozenset[str] = GAAP_TAGS | DEI_TAGS

#: tag -> the `taxonomy` value pit_fact stores. Built from the same source as
#: the filter, so a tag can never be retained without a taxonomy for it.
TAG_TAXONOMY: dict[str, str] = {
    **{t: pit_policy.TAXONOMY_US_GAAP for t in GAAP_TAGS},
    **{t: pit_policy.TAXONOMY_DEI for t in DEI_TAGS},
}


def tag_filter_saving() -> dict[str, Any]:
    """What the tag filter buys, in measured numbers.

    Quoted from 2021q2: tag.txt lists 97,000 tags, 86,781 of them (89.5%)
    company extensions. The filter keeps 38.
    """
    total_tags = MEASURED_QUARTER["tag_txt_rows"]
    custom = MEASURED_QUARTER["tag_txt_custom"]
    standard = total_tags - custom
    return {
        "measured_on_quarter": MEASURED_QUARTER["quarter"],
        "tags_in_tag_txt": total_tags,
        "company_extensions": custom,
        "company_extension_share": round(custom / total_tags, 4),
        "standard_tags": standard,
        "tags_retained": len(TAG_FILTER),
        "us_gaap_retained": len(GAAP_TAGS),
        "dei_retained": len(DEI_TAGS),
        "share_of_all_tags": round(len(TAG_FILTER) / total_tags, 6),
        "share_of_standard_tags": round(len(TAG_FILTER) / standard, 6),
        "rows_dropped_by_tag_filter": (MEASURED_QUARTER["after_consolidated"]
                                       - MEASURED_QUARTER["after_tag"]),
        "why": ("The ingest is affordable because it reads 38 tags, not 97,000. "
                "Dropping the extensions is not a simplification -- a company "
                "extension is one filer's private name and is unusable in a "
                "cross-section by construction."),
    }


# --------------------------------------------------------------------------
# Measured basis. Every projection in this module traces to one of these.
# --------------------------------------------------------------------------

#: One full parse of 2021q2 on 2026-09-20. Reproduce with
#: `python pit_dera.py --measure <path-to-2021q2.zip>`.
MEASURED_QUARTER: dict[str, Any] = {
    "quarter": "2021q2",
    "measured_on": "2026-09-20",
    "zip_bytes": 90864175,
    "num_txt_bytes": 368465091,
    "sub_txt_bytes": 2354653,
    "pre_txt_bytes": 98882121,      # never read
    "tag_txt_bytes": 21018384,      # never read
    "tag_txt_rows": 97000,
    "tag_txt_custom": 86781,
    "sub_rows": 7810,
    "sub_periodic_rows": 7337,
    "sub_distinct_ciks": 6518,
    "sub_periodic_ciks": 6455,
    "num_total_rows": 2833915,
    "after_consolidated": 1500841,  # segments == '' AND coreg == ''
    "after_tag": 219207,            # + tag in TAG_FILTER, us-gaap/dei only
    "after_qtrs": 219157,           # + qtrs in (0,1,2,3,4)
    "retained": 216702,             # + value non-empty
    "retained_periodic": 202241,    # + periodic forms, + period not after filing
    "rows_before_period_guard": 202253,
    "period_after_filing": 12,      # see MAX_PERIOD_AHEAD_OF_FILED_DAYS
    "distinct_ciks_retained": 6265,          # all forms
    "distinct_ciks_retained_periodic": 6226,  # what actually lands in pit_fact
    "entities_registered": 6455,             # CIKs filing a periodic form
    "ifrs_rows_excluded": 2009,
    "retained_raw_field_bytes": 18725171,
    "download_seconds": 2.2,
    "parse_seconds": 22.61,
    "parse_rows_per_second": 125316,
    "register_entities_seconds": 0.73,
    "register_entities_sic_seconds": 0.93,
    # Units are NOT filtered. A Chinese issuer reporting in CNY is a real
    # observation; `pit_store.fact_as_of` takes the unit as an argument, so the
    # selector -- not the ingest -- decides what a USD ladder may read.
    "units": {"USD": 181651, "shares": 16902, "CNY": 3178, "CAD": 204,
              "JPY": 150, "EUR": 52, "RUB": 40, "HKD": 24, "AUD": 24,
              "KRW": 7, "MXN": 4, "CLP": 3, "USN": 1, "SGD": 1},
}

#: One real load of 2021q2 into a throwaway SQLite file, 2026-09-20.
#: Bytes are `PRAGMA page_count * page_size` differenced against the empty
#: schema, with BOTH sides VACUUMed so free-page fragmentation cannot inflate
#: the figure. The table/index split comes from building the same quarter twice,
#: once with `idx_pit_fact_select` and `idx_pit_fact_period` dropped before the
#: load. Nothing here is a guess.
MEASURED_INSERT: dict[str, Any] = {
    "measured_on": "2026-09-20",
    "quarter": "2021q2",
    "rows_inserted": 202241,
    "insert_seconds": 6.28,          # pure executemany, entities pre-registered
    "rows_per_second": 32191,
    "batch_size": 20000,
    "sqlite_page_size": 4096,
    "bytes_total": 75325440,         # VACUUMed, both secondary indexes present
    "bytes_table_and_unique": 57716736,   # VACUUMed, secondary indexes dropped
    "bytes_secondary_indexes": 17608704,
    "bytes_per_row_total": 372.5,
    "end_to_end_seconds": 31.23,     # parse + entities + insert, no download
    "reinsert_20000_new_rows": 0,    # idempotency, second pass over the same rows
}


# --------------------------------------------------------------------------
# Quarter arithmetic
# --------------------------------------------------------------------------

def parse_quarter(quarter: str) -> tuple[int, int]:
    """'2021q2' -> (2021, 2). Raises on anything else."""
    text = str(quarter).strip().lower()
    if len(text) != 6 or text[4] != "q" or not text[:4].isdigit() or text[5] not in "1234":
        raise ValueError(f"not a DERA quarter id: {quarter!r} (want e.g. '2021q2')")
    return int(text[:4]), int(text[5])


def quarters_between(first: str, last: str) -> list[str]:
    """Every DERA quarter id from `first` to `last`, inclusive and in order."""
    y, q = parse_quarter(first)
    end = parse_quarter(last)
    out: list[str] = []
    while (y, q) <= end:
        out.append(f"{y}q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def quarter_url(quarter: str) -> str:
    parse_quarter(quarter)
    return DERA_URL.format(quarter=quarter)


def archive_quarters() -> list[str]:
    """The whole measured archive, oldest first."""
    return quarters_between(ARCHIVE_FIRST_QUARTER, ARCHIVE_LAST_QUARTER)


def warmup_window(first: str = RECOMMENDED_FIRST_QUARTER,
                  scoring_start: str = SCORING_START) -> dict[str, Any]:
    """The warm-up decision, with its reasoning and its byte cost.

    Returns the quarters BEFORE the first scored date (the warm-up proper) and
    the whole ingest window, so a caller can see what the warm-up costs on its
    own rather than only as part of the total.
    """
    scoring_quarter = f"{scoring_start[:4]}q{(int(scoring_start[5:7]) - 1) // 3 + 1}"
    window = quarters_between(first, ARCHIVE_LAST_QUARTER)
    warmup = [q for q in window if q < scoring_quarter]
    minimum = quarters_between(WARMUP_MINIMUM_QUARTER, ARCHIVE_LAST_QUARTER)
    return {
        "scoring_start": scoring_start,
        "first_scored_quarter": scoring_quarter,
        "first_quarter": first,
        "warmup_quarters": warmup,
        "n_warmup_quarters": len(warmup),
        "window_quarters": window,
        "n_window_quarters": len(window),
        "window_bytes": sum(QUARTER_BYTES.get(q, 0) for q in window),
        "warmup_bytes": sum(QUARTER_BYTES.get(q, 0) for q in warmup),
        "minimum_first_quarter": WARMUP_MINIMUM_QUARTER,
        "minimum_window_bytes": sum(QUARTER_BYTES.get(q, 0) for q in minimum),
        "extra_bytes_over_minimum": (sum(QUARTER_BYTES.get(q, 0) for q in window)
                                     - sum(QUARTER_BYTES.get(q, 0) for q in minimum)),
        "why_minimum": (
            "Scoring 2013-01-01 needs the latest annual period knowable then "
            "plus prior and prior-prior -- FY2011/FY2010/FY2009 for a December "
            "filer -- originally filed in calendar 2012/2011/2010. Twelve "
            "quarters, 2010q1 onward."),
        "why_recommended": (
            "A June or September fiscal-year filer files its FY2009 10-K inside "
            "calendar 2009 (Apple's FY2009 ended 2009-09-26, filed 2009-10-27 = "
            "2009q4). Starting at 2010q1 loses those original prints and the PIT "
            "selector silently substitutes the FY2009 comparative from a later "
            "10-K -- a restated number wearing the original's period end."),
        "phase_in_warning": XBRL_PHASE_IN_NOTE,
    }


# --------------------------------------------------------------------------
# DERA's month-end rounding
# --------------------------------------------------------------------------

def nearest_month_end(day: Any) -> str:
    """The canonical period key: the month end NEAREST to a true period end.

    DERA rounds `ddate` to the nearest month end, so Apple's FY2009, which
    really ended 2009-09-26, is published as 20090930 -- and a 52/53-week
    retailer whose year ended 2013-01-02 is published as 20121231, rounded
    BACKWARDS. Verified on 2021q2: 216,702 of 216,702 retained `ddate` values
    are a calendar month end.

    THIS IS THE RECONCILIATION RULE WITH companyfacts. companyfacts publishes
    the true `end` date, so the same fact keys differently in the two sources
    and `pit_fact`'s UNIQUE constraint would store it twice while
    `fact_as_of(period_end=...)` found only one. The rule is: the point-in-time
    store's `period_end` is ALWAYS the DERA-style nearest month end, and the
    companyfacts ingest must pass its `end` through this same function before
    writing or querying. Rounding DERA's value up to a true date is impossible
    -- the information is gone -- so the coarser key is the only one both
    sources can agree on.

    The cost is honest and bounded: two period ends inside the same month
    collide. That cannot happen for one (entity, tag, qtrs) series, because no
    issuer closes two fiscal quarters of the same length in one month.
    """
    date = day if isinstance(day, _dt.date) else _dt.date.fromisoformat(str(day)[:10])
    first = _dt.date(date.year, date.month, 1)
    this_end = _month_end(first)
    prev_end = first - _dt.timedelta(days=1)
    next_end = _month_end(_add_one_month(first))
    # Nearest, ties resolved to the month the date falls in.
    best = min((this_end, prev_end, next_end), key=lambda d: (abs((d - date).days), d != this_end))
    return best.isoformat()


def _month_end(first_of_month: _dt.date) -> _dt.date:
    return _add_one_month(first_of_month) - _dt.timedelta(days=1)


def _add_one_month(first_of_month: _dt.date) -> _dt.date:
    if first_of_month.month == 12:
        return _dt.date(first_of_month.year + 1, 1, 1)
    return _dt.date(first_of_month.year, first_of_month.month + 1, 1)


def dera_date(compact: str, label: str = "date") -> str:
    """DERA's 'YYYYMMDD' -> house-style 'YYYY-MM-DD'. Raises on anything else."""
    text = str(compact).strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"{label} is not a DERA YYYYMMDD value: {compact!r}")
    iso = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    _dt.date.fromisoformat(iso)          # raises on 20210230
    return iso


def dera_accepted(text: Any) -> Optional[str]:
    """DERA's '2021-05-06 16:28:00.0' -> '2021-05-06T16:28:00', or None.

    Eastern wall clock, and left naive on purpose: `pit_policy` reads a naive
    stamp as Eastern, which is what EDGAR actually publishes. Attaching a
    designator here would hand the latency policy the exact lie it exists to
    ignore.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    raw = raw.replace(" ", "T", 1)
    if raw.endswith(".0"):
        raw = raw[:-2]
    try:
        return _dt.datetime.fromisoformat(raw).replace(tzinfo=None).isoformat(
            timespec="seconds")
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Session resolution for the latency policy
# --------------------------------------------------------------------------

def session_resolver(conn: sqlite3.Connection,
                     market: str = "XNYS") -> Optional[Callable[[str], Optional[str]]]:
    """A `next_session` callable for `pit_policy.available_date`, or None.

    `pit_policy` holds no database code, so it takes the calendar as a callable.
    Returns None when `pit_calendar` is empty for this market, which makes the
    policy fall back to the calendar day and flag `session_resolved=False` --
    equivalent on a month-end session grid, and flagged rather than hidden.

    The lookups are memoised: one DERA quarter has ~7,800 submissions but only
    ~60 distinct availability base dates, so the cache turns thousands of
    queries into dozens.
    """
    row = conn.execute("SELECT COUNT(*) AS n FROM pit_calendar WHERE market = ?",
                       (market,)).fetchone()
    if not row or not row["n"]:
        return None
    cache: dict[str, Optional[str]] = {}

    def next_session(day: str) -> Optional[str]:
        if day not in cache:
            found = conn.execute(
                """SELECT MIN(session_date) AS d FROM pit_calendar
                    WHERE market = ? AND session_date >= ?""",
                (market, day),
            ).fetchone()
            cache[day] = found["d"] if found and found["d"] else None
        return cache[day]

    return next_session


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = SEC_MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def head_quarter_sizes(quarters: Sequence[str]) -> dict[str, Optional[int]]:
    """HEAD each quarter and return Content-Length. Re-measures QUARTER_BYTES.

    Network. Keeps to SEC_MIN_INTERVAL; a 404 (a quarter not yet published)
    comes back as None rather than raising.
    """
    import requests                                   # local: estimate is offline

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "identity"})
    out: dict[str, Optional[int]] = {}
    for quarter in quarters:
        _throttle()
        try:
            resp = session.head(quarter_url(quarter), timeout=60, allow_redirects=True)
        except Exception:
            out[quarter] = None
            continue
        length = resp.headers.get("Content-Length")
        out[quarter] = (int(length) if resp.status_code == 200 and length
                        and length.isdigit() else None)
    return out


def download_quarter(quarter: str, dest_dir: str) -> str:
    """Fetch one quarterly ZIP to `dest_dir` and return its path.

    Streamed to disk rather than held in memory -- a quarter is ~90 MB
    compressed and `zipfile` needs a seekable file anyway. An existing file of
    the expected size is reused, so a re-run costs nothing.
    """
    import requests

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{quarter}.zip")
    expected = QUARTER_BYTES.get(quarter)
    if os.path.exists(path) and (expected is None or os.path.getsize(path) == expected):
        return path

    _throttle()
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    partial = path + ".part"
    with session.get(quarter_url(quarter), timeout=HTTP_TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        with open(partial, "wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
    os.replace(partial, path)
    return path


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

#: Only these sub.txt fields are read. `prevrpt`, `fy` and `fp` are absent by
#: construction rather than by discipline -- see DROPPED_SUB_COLUMNS.
_SUB_FIELDS = ("cik", "name", "sic", "form", "filed", "accepted")


def _reader(zf: zipfile.ZipFile, member: str) -> Iterator[dict[str, str]]:
    """Stream one tab-delimited member as dicts, never loading the file.

    `ZipFile.open` decompresses lazily, so a 368 MB num.txt moves through a
    buffer rather than through memory. Fields are read BY NAME: the layout has
    been stable since 2010q1, but a positional reader would corrupt silently if
    DERA ever inserted a column, and this one would simply not find it.
    """
    with zf.open(member) as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        yield from csv.DictReader(stream, delimiter="\t")


def read_submissions(zf: zipfile.ZipFile,
                     forms: Optional[Iterable[str]] = PERIODIC_FORMS) -> dict[str, dict]:
    """adsh -> the handful of sub.txt fields this ingest uses.

    sub.txt is the one member held in memory: ~7,800 rows a quarter, and the
    num.txt join needs random access to it. Six fields per row rather than the
    full 36 keeps that under a megabyte and makes the dropped columns
    unreachable rather than merely unused.
    """
    keep = frozenset(forms) if forms is not None else None
    out: dict[str, dict] = {}
    for row in _reader(zf, "sub.txt"):
        if keep is not None and row.get("form") not in keep:
            continue
        out[row["adsh"]] = {field: (row.get(field) or "") for field in _SUB_FIELDS}
    return out


def submission_availability(sub: dict,
                            next_session: Optional[Callable[[str], Optional[str]]]
                            ) -> tuple[str, str, Optional[str], Optional[str], str]:
    """(filed, available_date, accepted_raw, accepted_eastern, max_period_end).

    Computed ONCE PER SUBMISSION and reused across its facts. A quarter has
    ~7,800 submissions and ~200,000 retained facts, so caching here is a 26x
    saving on the policy call -- and it is also the only correct shape: the
    latency policy is a property of the FILING, not of each number in it.

    `max_period_end` is `filed` plus MAX_PERIOD_AHEAD_OF_FILED_DAYS, precomputed
    so the per-row check is a string comparison rather than date arithmetic on
    every one of 180 million rows.
    """
    filed = dera_date(sub["filed"], "sub.filed")
    accepted_raw = (str(sub["accepted"]).strip() or None) if sub.get("accepted") else None
    accepted = dera_accepted(sub["accepted"])
    horizon = (_dt.date.fromisoformat(filed)
               + _dt.timedelta(days=MAX_PERIOD_AHEAD_OF_FILED_DAYS)).isoformat()
    return (filed, pit_policy.available_date(accepted, filed, next_session),
            accepted_raw, accepted, horizon)


def iter_quarter_facts(zf: zipfile.ZipFile, quarter: str, entity_ids: dict[str, int],
                       subs: dict[str, dict],
                       next_session: Optional[Callable[[str], Optional[str]]] = None,
                       limit: Optional[int] = None,
                       stats: Optional[dict[str, int]] = None
                       ) -> Iterator[tuple]:
    """Stream num.txt as tuples in `pit_store.FACT_COLUMNS` order.

    The filter chain, in the order that discards most first (measured on
    2021q2, 2,833,915 rows in):

        segments == '' and coreg == ''   -> 1,500,841   52.96%
        tag in TAG_FILTER, us-gaap/dei   ->   219,207    7.74%
        qtrs in (0,1,2,3,4)              ->   219,157    7.73%
        value non-empty                  ->   216,702    7.65%
        form in PERIODIC_FORMS           ->   202,253    7.14%
        period not after the filing      ->   202,241    7.14%

    A generator rather than a list: 200,000 tuples a quarter is small, but
    15 million across the archive is not, and the caller batches from this
    straight into executemany.
    """
    counters = stats if stats is not None else {}
    for key in ("rows_read", "dimensional", "tag_rejected", "nonstandard_taxonomy",
                "qtrs_rejected", "empty_value", "submission_not_kept",
                "unmapped_cik", "bad_date", "period_after_filing", "kept"):
        counters.setdefault(key, 0)

    availability: dict[str, tuple[str, str, Optional[str], str]] = {}
    source = f"dera:{quarter}"
    policy_version = pit_policy.LATENCY_POLICY_VERSION

    for row in _reader(zf, "num.txt"):
        counters["rows_read"] += 1
        if (row.get("segments") or "").strip() or (row.get("coreg") or "").strip():
            counters["dimensional"] += 1
            continue
        tag = row.get("tag") or ""
        if tag not in TAG_FILTER:
            counters["tag_rejected"] += 1
            continue
        version = row.get("version") or ""
        if not version.startswith(ACCEPTED_VERSION_PREFIXES):
            # IFRS (2,009 rows in 2021q2) and the handful of company extensions
            # that reuse a standard local name (14). See IFRS_COLLISION_NOTE.
            counters["nonstandard_taxonomy"] += 1
            continue
        try:
            qtrs = int(row["qtrs"])
        except (TypeError, ValueError, KeyError):
            counters["qtrs_rejected"] += 1
            continue
        if qtrs not in ACCEPTED_QTRS:
            counters["qtrs_rejected"] += 1
            continue
        raw_value = (row.get("value") or "").strip()
        if not raw_value:
            # A tagged null with a footnote. pit_fact.val is NOT NULL and an
            # absent value is not a zero.
            counters["empty_value"] += 1
            continue
        adsh = row.get("adsh") or ""
        sub = subs.get(adsh)
        if sub is None:
            # Almost always a form `read_submissions` filtered out (14,449 rows
            # in 2021q2, all from S-1/S-4/POS AM/8-K), not a broken join.
            counters["submission_not_kept"] += 1
            continue
        entity_id = entity_ids.get(sub["cik"])
        if entity_id is None:
            counters["unmapped_cik"] += 1
            continue
        try:
            value = float(raw_value)
            period_end = dera_date(row["ddate"], "num.ddate")
        except (TypeError, ValueError, KeyError):
            counters["bad_date"] += 1
            continue
        if adsh not in availability:
            try:
                availability[adsh] = submission_availability(sub, next_session)
            except ValueError:
                availability[adsh] = ("", "", None, None, "")
        (filed, available, accepted_raw, accepted,
         max_period_end) = availability[adsh]
        if not filed:
            counters["bad_date"] += 1
            continue
        if period_end > max_period_end:
            # A period that ends after the filing reporting it. See
            # MAX_PERIOD_AHEAD_OF_FILED_DAYS.
            counters["period_after_filing"] += 1
            continue

        counters["kept"] += 1
        yield (
            entity_id,
            TAG_TAXONOMY[tag],
            tag,
            row.get("uom") or "",
            None,                 # period_start: DERA publishes none. See below.
            period_end,
            qtrs,
            "",                   # segments: filtered to consolidated only
            "",                   # coreg
            value,
            adsh,
            sub["form"],
            filed,
            accepted_raw,
            accepted,
            available,
            policy_version,
            source,
        )
        if limit is not None and counters["kept"] >= limit:
            return


# `period_start` is NULL for every DERA row on purpose. DERA publishes `ddate`
# and `qtrs` and no start date. A start could be derived -- qtrs=2 ending
# 2021-06-30 implies 2021-01-01 -- but it would be a DERIVATION sitting in a
# table whose whole contract is "observations as published", and it would be
# wrong for a 52/53-week filer whose quarter really began 2020-12-27. `qtrs`
# already carries the duration exactly, and `pit_fact`'s UNIQUE key and
# `fact_as_of` both select on (period_end, qtrs), never on the start.


# --------------------------------------------------------------------------
# Entity registration
# --------------------------------------------------------------------------

def register_entities(conn: sqlite3.Connection, subs: dict[str, dict],
                      capture_sic: bool = True) -> dict[str, int]:
    """Map every CIK in this quarter to an entity_id. Returns {cik: entity_id}.

    One `pit_store.upsert_entity` call per DISTINCT CIK -- ~6,500 a quarter,
    not one per fact -- with first/last filing dates folded in beforehand so
    each CIK is touched once. That is the line the owner drew: per-row helper
    calls are forbidden at the FACT scale (millions), not at the issuer scale.

    `capture_sic` also writes `pit_entity_sic`, one row per submission, keyed
    UNIQUE(entity_id, source_accession). This is the cheapest correct source
    for it: sub.txt's `sic` is the classification carried in THAT FILING's
    header, which is exactly the point-in-time value the submissions API's
    current-only `sic` field cannot give. Measured cost: 6,455 CIKs in 0.73s
    plus 7,337 SIC rows in 0.93s on 2021q2, inside bulk_load -- 5% of the
    quarter's 31 s.

    Entity NAMES are deliberately not written here. `pit_entity_name`'s natural
    key is (entity_id, name, valid_from) and a per-filing valid_from would write
    a near-duplicate row for every filing of an unchanged name. sub.txt's
    `former`/`changed` pair is the real name-history source and belongs in an
    identity pass, not in a fact ingest.
    """
    per_cik: dict[str, list[str]] = {}
    for sub in subs.values():
        cik = str(sub["cik"]).strip()
        if not cik:
            continue
        try:
            filed = dera_date(sub["filed"], "sub.filed")
        except ValueError:
            continue
        per_cik.setdefault(cik, []).append(filed)

    ids: dict[str, int] = {}
    for cik, dates in per_cik.items():
        ids[cik] = pit_store.upsert_entity(
            conn, cik, first_filing_date=min(dates), last_filing_date=max(dates))

    if capture_sic:
        for adsh, sub in subs.items():
            sic = str(sub.get("sic") or "").strip()
            entity_id = ids.get(str(sub["cik"]).strip())
            if not sic or entity_id is None:
                continue
            try:
                filed = dera_date(sub["filed"], "sub.filed")
            except ValueError:
                continue
            pit_store.add_entity_sic(conn, entity_id, sic, filed, adsh,
                                     "dera:sub.txt")
    return ids


# --------------------------------------------------------------------------
# The bulk path
# --------------------------------------------------------------------------

DEFAULT_BATCH_SIZE = 20000


def ingest_quarter(conn: sqlite3.Connection, quarter: str, *,
                   zip_path: Optional[str] = None,
                   dest_dir: Optional[str] = None,
                   forms: Optional[Iterable[str]] = PERIODIC_FORMS,
                   batch_size: int = DEFAULT_BATCH_SIZE,
                   next_session: Optional[Callable[[str], Optional[str]]] = None,
                   capture_sic: bool = True,
                   limit: Optional[int] = None,
                   force: bool = False) -> dict[str, Any]:
    """Load one DERA quarter into `pit_fact`. Returns a status record.

    `status` is 'written' or 'exists', matching the house convention: a quarter
    already recorded complete in `pit_ingest_run` is skipped rather than
    re-parsed, so re-running the archive after a failure costs nothing for the
    quarters that landed. `force=True` re-reads one anyway, which is safe
    because `pit_store.insert_facts` is INSERT OR IGNORE against a UNIQUE key
    that includes the accession -- a conflict means the identical published
    observation is already stored, and the immutability trigger means it can
    never be overwritten.

    The path, in order:

      1. sub.txt into memory (~7,800 rows), periodic forms only by default;
      2. every distinct CIK registered, once, via `pit_store.upsert_entity`;
      3. num.txt STREAMED from the ZIP through `iter_quarter_facts`;
      4. `available_date` from `pit_policy`'s latency policy, computed once per
         submission from sub.txt's `accepted` timestamp;
      5. `ddate` read as the canonical month-end period key (`nearest_month_end`
         is the companyfacts side of the same rule);
      6. executemany in batches of `batch_size` inside `pit_store.bulk_load`,
         one commit per batch.

    Measured on 2021q2: 2,833,915 num.txt rows read, 202,241 kept, in 31.2 s
    (22.6 s parse, 0.9 s entities + SIC, 6.3 s insert at 32,191 rows/s), plus
    2.2 s to download the 86.6 MiB ZIP.
    """
    scope = f"dera:{quarter}"
    if not force:
        done = conn.execute(
            """SELECT ingest_id, rows_kept FROM pit_ingest_run
                WHERE source = ? AND status = 'complete'
                ORDER BY ingest_id DESC LIMIT 1""",
            (scope,),
        ).fetchone()
        if done:
            return {"status": "exists", "quarter": quarter,
                    "ingest_id": int(done["ingest_id"]),
                    "rows_kept": int(done["rows_kept"] or 0)}

    if zip_path is None:
        zip_path = download_quarter(quarter, dest_dir or DEFAULT_DERA_DIR)
    zip_bytes = os.path.getsize(zip_path)

    ingest_id = pit_store.start_ingest(conn, scope, scope)
    started = time.time()
    stats: dict[str, int] = {}
    inserted = 0
    batches = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            subs = read_submissions(zf, forms)
            with pit_store.bulk_load(conn):
                entity_ids = register_entities(conn, subs, capture_sic=capture_sic)
                batch: list[tuple] = []
                for row in iter_quarter_facts(zf, quarter, entity_ids, subs,
                                              next_session=next_session,
                                              limit=limit, stats=stats):
                    batch.append(row)
                    if len(batch) >= batch_size:
                        inserted += pit_store.insert_facts(conn, batch)
                        conn.commit()
                        batches += 1
                        batch.clear()
                if batch:
                    inserted += pit_store.insert_facts(conn, batch)
                    conn.commit()
                    batches += 1
    except Exception as exc:
        pit_store.finish_ingest(conn, ingest_id, status="error",
                                rows_read=stats.get("rows_read", 0),
                                bytes_downloaded=zip_bytes,
                                summary={"error": str(exc), "quarter": quarter})
        raise

    elapsed = time.time() - started
    summary = {
        "quarter": quarter,
        "zip_bytes": zip_bytes,
        "submissions": len(subs),
        "entities": len(entity_ids),
        "batches": batches,
        "seconds": round(elapsed, 2),
        "rows_per_second": round(stats.get("kept", 0) / elapsed, 1) if elapsed else None,
        "filters": dict(stats),
        "tag_filter_size": len(TAG_FILTER),
        "forms": sorted(forms) if forms is not None else "all",
        "ladder_version": pit_policy.LADDER_VERSION,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
        "session_resolved": next_session is not None,
    }
    # A `limit` run is recorded 'partial', never 'complete'. The idempotency
    # check above only skips a COMPLETE quarter, so a truncated smoke-test run
    # can never masquerade as a finished one and block the real ingest.
    pit_store.finish_ingest(
        conn, ingest_id, status="complete" if limit is None else "partial",
        rows_read=stats.get("rows_read", 0),
        rows_kept=stats.get("kept", 0),
        rows_rejected=stats.get("rows_read", 0) - stats.get("kept", 0),
        bytes_downloaded=zip_bytes, summary=summary)

    return {"status": "written", "quarter": quarter, "ingest_id": ingest_id,
            "rows_read": stats.get("rows_read", 0), "rows_kept": stats.get("kept", 0),
            "rows_inserted": inserted, "duplicates_ignored":
                stats.get("kept", 0) - inserted,
            "entities": len(entity_ids), "seconds": round(elapsed, 2),
            "filters": dict(stats)}


# --------------------------------------------------------------------------
# The estimate
# --------------------------------------------------------------------------

def estimate_ingest(quarters: Sequence[str],
                    sizes: Optional[dict[str, int]] = None,
                    forms: Optional[Iterable[str]] = PERIODIC_FORMS) -> dict[str, Any]:
    """Project the cost of ingesting `quarters`, from measured numbers only.

    Every rate below traces to one measured quantity, and each is labelled with
    how it was obtained. The single modelling assumption is that retained rows
    scale with COMPRESSED ZIP BYTES -- num.txt is 81% of a quarter's compressed
    volume, so the two track closely, but a quarter whose pre.txt grew faster
    than its num.txt would be over-projected. That assumption is named in the
    returned record rather than buried.

    Returns a structured dict; nothing prints and nothing touches the network.
    """
    table = dict(QUARTER_BYTES)
    if sizes:
        table.update({k: v for k, v in sizes.items() if v})
    known = [q for q in quarters if table.get(q)]
    missing = [q for q in quarters if not table.get(q)]
    total_bytes = sum(table[q] for q in known)

    m = MEASURED_QUARTER
    i = MEASURED_INSERT
    basis_bytes = m["zip_bytes"]

    rows_per_byte = m["num_total_rows"] / basis_bytes
    retained_key = "retained_periodic" if forms is not None else "retained"
    retained_per_byte = m[retained_key] / basis_bytes

    num_rows = total_bytes * rows_per_byte
    retained_rows = total_bytes * retained_per_byte

    bytes_per_row_table = i["bytes_table_and_unique"] / i["rows_inserted"]
    bytes_per_row_index = i["bytes_secondary_indexes"] / i["rows_inserted"]
    fact_bytes = int(retained_rows * bytes_per_row_table)
    index_bytes = int(retained_rows * bytes_per_row_index)

    download_rate = m["zip_bytes"] / m["download_seconds"]
    parse_rate = m["num_total_rows"] / m["parse_seconds"]
    insert_rate = i["rows_per_second"]

    download_seconds = total_bytes / download_rate
    parse_seconds = num_rows / parse_rate
    insert_seconds = retained_rows / insert_rate

    return {
        "quarters": list(quarters),
        "n_quarters": len(quarters),
        "quarters_without_a_measured_size": missing,
        "download": {
            "total_bytes": int(total_bytes),
            "total_gib": round(total_bytes / (1 << 30), 2),
            "per_quarter_bytes": {q: table[q] for q in known},
            "seconds": round(download_seconds, 1),
            "measured_rate_mb_s": round(download_rate / 1e6, 1),
        },
        "rows": {
            "num_txt_rows": int(num_rows),
            "retained_rows": int(retained_rows),
            "reduction_ratio": round(num_rows / retained_rows, 1) if retained_rows else None,
            "retained_share": round(retained_rows / num_rows, 4) if num_rows else None,
            "forms": "periodic only" if forms is not None else "all forms",
        },
        "database": {
            "bytes_per_row_table_and_unique": round(bytes_per_row_table, 1),
            "bytes_per_row_secondary_indexes": round(bytes_per_row_index, 1),
            "bytes_per_row_total": round(bytes_per_row_table + bytes_per_row_index, 1),
            "pit_fact_bytes": fact_bytes,
            "index_bytes": index_bytes,
            "total_bytes": fact_bytes + index_bytes,
            "total_gib": round((fact_bytes + index_bytes) / (1 << 30), 2),
            "sqlite_page_size": i["sqlite_page_size"],
        },
        "runtime": {
            "download_seconds": round(download_seconds, 1),
            "parse_seconds": round(parse_seconds, 1),
            "insert_seconds": round(insert_seconds, 1),
            "total_seconds": round(download_seconds + parse_seconds + insert_seconds, 1),
            "total_minutes": round(
                (download_seconds + parse_seconds + insert_seconds) / 60, 1),
            "measured_parse_rows_per_second": int(parse_rate),
            "measured_insert_rows_per_second": int(insert_rate),
        },
        "tag_filter": tag_filter_saving(),
        "basis": {
            "quarter_sizes": f"HTTP HEAD Content-Length, measured {QUARTER_BYTES_MEASURED_ON}",
            "row_counts": (f"full parse of {m['quarter']} "
                           f"({m['num_total_rows']:,} num.txt rows), {m['measured_on']}"),
            "bytes_per_row": (f"real insert of {i['rows_inserted']:,} rows into a "
                              f"throwaway SQLite database; page_count * page_size "
                              f"differenced against the empty schema"),
            "rates": (f"wall clock: {m['download_seconds']}s download, "
                      f"{m['parse_seconds']}s parse, {i['insert_seconds']}s insert "
                      f"on {m['quarter']}"),
            "scaling_assumption": (
                "retained rows scale linearly with compressed ZIP bytes. num.txt is "
                "80.5% of compressed volume in 2021q2 and 82.7% in 2014q1, but only "
                "66.7% in 2010q1, so the thin early quarters are over-projected by "
                "roughly a fifth. 2009q2-2011q4 is 3.6% of the window's bytes, so "
                "the effect on the total is under 1%."),
            "not_projected": ("pre.txt and tag.txt are never read, so their bytes are "
                              "downloaded but never parsed or stored"),
            "known_holes": [DEI_COVERAGE_NOTE, IFRS_COLLISION_NOTE, XBRL_PHASE_IN_NOTE],
        },
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def measure_quarter(zip_path: str, quarter: Optional[str] = None) -> dict[str, Any]:
    """Re-derive MEASURED_QUARTER from a local ZIP. Reads, never writes.

    Deliberately a SEPARATE pass from `iter_quarter_facts`, which short-circuits
    at the first filter that rejects a row and therefore cannot report what each
    stage cost. This one counts every stage on the same rows, which is what makes
    the reduction table in `report()` an observation rather than a story.

    `tag.txt` is read here and ONLY here -- to count how many tags exist and how
    many are company extensions. The ingest never opens it.
    """
    quarter = quarter or os.path.splitext(os.path.basename(zip_path))[0]
    out: dict[str, Any] = {"quarter": quarter, "zip_bytes": os.path.getsize(zip_path)}

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            out[f"{info.filename.replace('.', '_')}_bytes"] = info.file_size

        tag_rows = tag_custom = 0
        for row in _reader(zf, "tag.txt"):
            tag_rows += 1
            tag_custom += 1 if (row.get("custom") or "").strip() == "1" else 0
        out["tag_txt_rows"] = tag_rows
        out["tag_txt_custom"] = tag_custom

        subs_all = read_submissions(zf, forms=None)
        subs_periodic = read_submissions(zf)
        out["sub_rows"] = len(subs_all)
        out["sub_periodic_rows"] = len(subs_periodic)
        out["sub_distinct_ciks"] = len({s["cik"] for s in subs_all.values()})
        out["sub_periodic_ciks"] = len({s["cik"] for s in subs_periodic.values()})

        horizons: dict[str, str] = {}
        for adsh, sub in subs_periodic.items():
            try:
                horizons[adsh] = (
                    _dt.date.fromisoformat(dera_date(sub["filed"]))
                    + _dt.timedelta(days=MAX_PERIOD_AHEAD_OF_FILED_DAYS)).isoformat()
            except ValueError:
                horizons[adsh] = "0000-00-00"

        total = consolidated = tagged = qtrs_ok = valued = periodic = kept = 0
        ifrs = 0
        ciks_all: set[str] = set()
        ciks_kept: set[str] = set()
        units: dict[str, int] = {}
        for row in _reader(zf, "num.txt"):
            total += 1
            if (row.get("segments") or "").strip() or (row.get("coreg") or "").strip():
                continue
            consolidated += 1
            if (row.get("tag") or "") not in TAG_FILTER:
                continue
            version = row.get("version") or ""
            if not version.startswith(ACCEPTED_VERSION_PREFIXES):
                ifrs += 1 if version.startswith("ifrs") else 0
                continue
            tagged += 1
            try:
                if int(row["qtrs"]) not in ACCEPTED_QTRS:
                    continue
            except (TypeError, ValueError, KeyError):
                continue
            qtrs_ok += 1
            if not (row.get("value") or "").strip():
                continue
            valued += 1
            adsh = row.get("adsh") or ""
            if adsh in subs_all:
                ciks_all.add(subs_all[adsh]["cik"])
            if adsh not in subs_periodic:
                continue
            periodic += 1
            try:
                period_end = dera_date(row["ddate"])
            except (ValueError, KeyError):
                continue
            if period_end > horizons[adsh]:
                continue
            kept += 1
            ciks_kept.add(subs_periodic[adsh]["cik"])
            unit = row.get("uom") or ""
            units[unit] = units.get(unit, 0) + 1

    out.update({
        "num_total_rows": total,
        "after_consolidated": consolidated,
        "after_tag": tagged,
        "after_qtrs": qtrs_ok,
        "retained": valued,
        "rows_before_period_guard": periodic,
        "retained_periodic": kept,
        "period_after_filing": periodic - kept,
        "ifrs_rows_excluded": ifrs,
        "distinct_ciks_retained": len(ciks_all),
        "distinct_ciks_retained_periodic": len(ciks_kept),
        "units": dict(sorted(units.items(), key=lambda kv: -kv[1])),
        "tag_filter_size": len(TAG_FILTER),
    })
    return out


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,.0f} B"
        n /= 1024
    return f"{n:,.1f} GiB"


def report(quarters: Optional[Sequence[str]] = None) -> str:
    """The estimate as a readable block of text."""
    window = warmup_window()
    quarters = list(quarters or window["window_quarters"])
    est = estimate_ingest(quarters)
    saving = est["tag_filter"]
    lines: list[str] = []
    add = lines.append

    add("=" * 74)
    add("DERA INGEST ESTIMATE -- measured, not guessed")
    add("=" * 74)
    add("")
    add(f"Archive          {ARCHIVE_FIRST_QUARTER} .. {ARCHIVE_LAST_QUARTER}  "
        f"({len(archive_quarters())} quarters, "
        f"{_fmt_bytes(sum(QUARTER_BYTES.values()))})")
    add(f"Sizes measured   {QUARTER_BYTES_MEASURED_ON} by HTTP HEAD, Content-Length")
    add("")
    add("-- WARM-UP DECISION " + "-" * 54)
    add(f"  first scored date         {window['scoring_start']}")
    add(f"  binding minimum           {window['minimum_first_quarter']} "
        f"(12 warm-up quarters)")
    add(f"    {window['why_minimum']}")
    add(f"  recommended start         {window['first_quarter']} "
        f"({window['n_warmup_quarters']} warm-up quarters)")
    add(f"    {window['why_recommended']}")
    add(f"  warm-up quarters cost     {_fmt_bytes(window['warmup_bytes'])}")
    add(f"  extra over the minimum    {_fmt_bytes(window['extra_bytes_over_minimum'])} "
        f"({100.0 * window['extra_bytes_over_minimum'] / window['window_bytes']:.2f}% "
        f"of the window)")
    add(f"  INGEST WINDOW             {window['first_quarter']} .. "
        f"{ARCHIVE_LAST_QUARTER} = {window['n_window_quarters']} quarters, "
        f"{_fmt_bytes(window['window_bytes'])}")
    add("")
    add("  candidate windows, all ending " + ARCHIVE_LAST_QUARTER + ":")
    for label, start in (("recommended", RECOMMENDED_FIRST_QUARTER),
                         ("binding minimum", WARMUP_MINIMUM_QUARTER),
                         ("2012q1", "2012q1"),
                         ("no warm-up at all", "2013q1")):
        qs = quarters_between(start, ARCHIVE_LAST_QUARTER)
        total = sum(QUARTER_BYTES.get(q, 0) for q in qs)
        add(f"    {start}  {len(qs):>3} quarters  {total:>15,} B  "
            f"{_fmt_bytes(total):>10}   {label}")
    add(f"  ! {XBRL_PHASE_IN_NOTE}")
    add("")
    add("-- TAG FILTER " + "-" * 60)
    add(f"  tags in tag.txt           {saving['tags_in_tag_txt']:,} "
        f"({saving['company_extensions']:,} company extensions = "
        f"{100 * saving['company_extension_share']:.1f}%)")
    add(f"  standard tags             {saving['standard_tags']:,}")
    add(f"  TAG_FILTER retains        {saving['tags_retained']} "
        f"({saving['us_gaap_retained']} us-gaap + {saving['dei_retained']} dei)")
    add(f"  share of all tags         {100 * saving['share_of_all_tags']:.3f}%")
    add(f"  rows it discards          {saving['rows_dropped_by_tag_filter']:,} "
        f"of a quarter's consolidated rows")
    add("")
    add("-- ONE MEASURED QUARTER (" + MEASURED_QUARTER["quarter"] + ") " + "-" * 36)
    m = MEASURED_QUARTER
    total = m["num_total_rows"]
    for label, value in (
            ("num.txt rows", m["num_total_rows"]),
            ("segments=='' and coreg==''", m["after_consolidated"]),
            ("+ tag in TAG_FILTER", m["after_tag"]),
            ("+ qtrs in (0,1,2,3,4)", m["after_qtrs"]),
            ("+ value non-empty", m["retained"]),
            ("+ periodic forms", m["rows_before_period_guard"]),
            ("+ period <= filed+15d (KEPT)", m["retained_periodic"])):
        add(f"  {label:<30}{value:>12,}   {100.0 * value / total:6.2f}%")
    add(f"  {'reduction':<30}{total / m['retained_periodic']:>11.1f}x")
    add(f"  {'issuers with >=1 fact':<30}{m['distinct_ciks_retained_periodic']:>12,}")
    add(f"  {'IFRS rows excluded':<30}{m['ifrs_rows_excluded']:>12,}")
    add(f"  {'future-dated periods refused':<30}{m['period_after_filing']:>12,}")
    add("")
    add("-- PROJECTION OVER THE INGEST WINDOW " + "-" * 37)
    add(f"  quarters                  {est['n_quarters']}")
    add(f"  download                  {_fmt_bytes(est['download']['total_bytes'])}")
    add(f"  num.txt rows parsed       {est['rows']['num_txt_rows']:,}")
    add(f"  rows retained             {est['rows']['retained_rows']:,} "
        f"({100 * est['rows']['retained_share']:.2f}%)")
    db = est["database"]
    add(f"  bytes/row (table+unique)  {db['bytes_per_row_table_and_unique']}")
    add(f"  bytes/row (2 indexes)     {db['bytes_per_row_secondary_indexes']}")
    add(f"  pit_fact                  {_fmt_bytes(db['pit_fact_bytes'])}")
    add(f"  indexes                   {_fmt_bytes(db['index_bytes'])}")
    add(f"  DATABASE FOOTPRINT        {_fmt_bytes(db['total_bytes'])}")
    rt = est["runtime"]
    add("")
    add(f"  download {rt['download_seconds']:>8.1f}s  at "
        f"{est['download']['measured_rate_mb_s']} MB/s")
    add(f"  parse    {rt['parse_seconds']:>8.1f}s  at "
        f"{rt['measured_parse_rows_per_second']:,} rows/s")
    add(f"  insert   {rt['insert_seconds']:>8.1f}s  at "
        f"{rt['measured_insert_rows_per_second']:,} rows/s")
    add(f"  TOTAL    {rt['total_seconds']:>8.1f}s = {rt['total_minutes']} minutes")
    add("")
    add("-- KNOWN HOLES " + "-" * 59)
    for note in est["basis"]["known_holes"]:
        add(f"  ! {note}")
    add("")
    add("-- BASIS " + "-" * 65)
    for key in ("quarter_sizes", "row_counts", "bytes_per_row", "rates",
                "scaling_assumption", "not_projected"):
        add(f"  {key:<22}{est['basis'][key]}")
    add("")
    add("This module does NOT run the archive. ingest_quarter(conn, '2021q2') "
        "loads one.")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the estimate.

    Offline by default. `--head` re-measures every quarter's Content-Length
    over the network; `--measure <path>` re-derives MEASURED_QUARTER from a
    local ZIP so none of the constants above has to be taken on trust.
    """
    args = list(argv if argv is not None else sys.argv[1:])
    if "--measure" in args:
        path = args[args.index("--measure") + 1]
        measured = measure_quarter(path)
        width = max(len(k) for k in measured)
        for key, value in measured.items():
            print(f"  {key:<{width}}  {value:,}" if isinstance(value, int)
                  else f"  {key:<{width}}  {value}")
        return 0
    if "--head" in args:
        print(f"HEAD {len(archive_quarters())} quarters at <= "
              f"{1 / SEC_MIN_INTERVAL:.1f} req/s ...")
        sizes = head_quarter_sizes(archive_quarters())
        for quarter, size in sizes.items():
            print(f"  {quarter}  {size if size is not None else 'MISSING'}")
        QUARTER_BYTES.update({k: v for k, v in sizes.items() if v})
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
