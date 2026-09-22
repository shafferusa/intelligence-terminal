"""Macro vintages and the Treasury par curve for the point-in-time replay.

`pit_macro_obs`, `pit_macro_manifest` and `pit_curve_obs` were empty, which
blocks the inflation deflator outright: the v2 candidate's real-growth factor
cannot be computed at all until a CPI level exists AS IT WAS PUBLISHED at each
as-of date. This module fills those three tables and nothing else. It never
writes to `pit_fact`, `pit_entity` or any production table.

Four invariants this module exists to enforce:

1. A VINTAGE IS EVIDENCE; A CURRENT VALUE IS NOT. ALFRED answers
   "what did the series look like on date D", and that is the only acceptable
   input to a replay. The revised series is a different number: CPI for
   2014-01 reads 235.128 in the 2015-06-30 vintage and 235.288 today, because
   the seasonal factors were re-estimated twice since. A backtest fed today's
   value has seen three years into its own future.

2. THE SOURCE LIES BY OMISSION, SO EVERY RESPONSE IS VALIDATED. Measured
   2026-09-21: `vintage_date=2030-06-30` returns **HTTP 200** with today's
   fully revised values, under a header that reads `CPIAUCSL_20260921`. There
   is no error, no warning and no empty body -- only the header betrays it. So
   the header's second field must equal `<ID>_<vintage with dashes stripped>`
   or the payload is rejected unparsed. This is the single most important line
   of code in the file.

3. AN ABSENCE IS RECORDED, NEVER PAPERED OVER. Each series has a real archive
   floor (DTWEXBGS has no vintage before 2019-02-04 and is revised on every
   single observation). A grid date below the floor gets a NULL row carrying
   `revision_class = NO_VINTAGE` and a reason in `source`. It never gets the
   current series wearing a historical vintage date.

4. A TRANSFORM STAYS INSIDE ONE VINTAGE. INDPRO and PCEPILFE were both rebased
   inside the replay window; a month-over-month computed across a rebase
   manufactures a shock of several percent that never happened. Read a series
   through `series_as_of`, which returns one vintage, never a stitched one.

DISK. The store is 5.45 GiB on a volume that was at 95% full, and an earlier
ingest failed by filling it: a SQLite WAL reached 9.3 GB because concurrent
readers kept `wal_checkpoint` busy and it silently did nothing. Every phase
here calls `require_disk` before it fetches, every checkpoint's return value is
READ (a leading 1 means the checkpoint was refused), and nothing is ever
downloaded to disk -- responses are parsed from memory and discarded. The whole
ingest adds well under 100 MB.

Sources, all free and keyless. FRED_API_KEY is neither set nor needed:

    ALFRED vintages   alfred.stlouisfed.org/graph/alfredgraph.csv
    FRED current      fred.stlouisfed.org/graph/fredgraph.csv
    Treasury curve    home.treasury.gov/resource-center/... (.gov: house UA)

WHAT THE 2026-09-21 RUN MEASURED
--------------------------------
248,934 rows in pit_macro_obs across 24 series, 99,558 in pit_curve_obs across
14 tenors and 9,185 sessions, 24 manifest rows. The three tables and their
indexes occupy 68.7 MiB, measured by extracting them into a fresh database
rather than by differencing the shared store, which several other processes
were writing at the same time.

    7 monthly/quarterly series x 177 grid dates = 1,239 vintages fetched,
      plus DTWEXBGS at 92 (the 85 grid dates below its floor are not fetched)
    85 NO_VINTAGE rows, every one DTWEXBGS below its 2019-02-04 floor
    0 header mismatches, 0 unexpected 404s

The demonstration, from the store rather than from a docstring:

    CPIAUCSL 2015-01-01   first print 234.677 (vintage 2015-02-27)
                          today       234.747 (vintage 2024-12-31)

and with the release date pinned by bisection rather than by the grid:

    CPIAUCSL 2015-06-01   first print 237.786 (vintage 2015-07-17, 6 probes)
                          today       237.657 (vintage 2025-06-30)

-- a first print that is HIGHER than today's value, which is the direction a
reader who assumes revisions only add would get wrong.

Three findings the run produced that were not inputs to it:

1. PCEPILFE was rebased THREE times inside the replay window, not once:
   2005=100 -> 2009=100 (vintage 2013-08-30), -> 2012=100 (2018-07-31),
   -> 2017=100 (2023-09-29). A month-over-month stitched across the last of
   those reads -6.90% where the same month inside one vintage reads +0.22%.
   INDPRO carries three bases over the same window (2012, 2017, 2021).

2. DCOILWTICO loses observations. Against the 2015-06-30 vintage, 0 of 255
   shared values differ but 12 that were present then are BLANK now. Values
   are not revised; the observation SET is not stable. See its manifest row.

3. The store is shared. `wal_checkpoint(TRUNCATE)` returned busy while other
   processes held it -- measured (1, 73, 2) with PASSIVE returning (0, 73, 2)
   one statement later -- which is the same condition that let an earlier WAL
   reach 9.3 GB unnoticed. `checkpoint` now reads the tuple, falls back to
   PASSIVE, and stops the run at a 512 MiB WAL. Peak WAL this run: 24 MiB.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import os
import shutil
import sqlite3
import statistics
import sys
import time
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

import pit_store

__all__ = [
    "MACRO_SOURCE_VERSION", "CURVE_SOURCE_VERSION",
    "REVISED", "NOT_REVISED", "NO_VINTAGE",
    "MISSING_PRE_ARCHIVE", "MISSING_HTTP_404", "MISSING_HEADER_MISMATCH",
    "ALFRED_SERIES", "FRED_SERIES", "VINTAGE_FLOORS", "TREASURY_TENORS",
    "AlfredHeaderMismatch", "SourceRejected", "DiskTooFull",
    "require_disk", "checkpoint", "replay_grid", "prepare_connection",
    "alfred_url", "fred_url", "treasury_url",
    "parse_alfred_csv", "parse_fred_csv", "parse_treasury_csv",
    "availability_resolver", "available_after",
    "derive_base_tag", "derive_publication_lag", "detect_rebases",
    "refine_first_vintage",
    "insert_curve_obs", "upsert_manifest",
    "ingest_alfred_series", "ingest_fred_series", "ingest_curve_year",
    "ingest_curve", "ingest_all",
    "series_as_of", "value_as_of", "gaps", "curve_as_of", "coverage",
    "first_print_demo", "rebuild_manifest",
]


# --------------------------------------------------------------------------
# Versions. Never edited in place -- a change means a v2 constant beside v1.
# --------------------------------------------------------------------------

#: How a macro row was produced: which endpoint, which validation, which
#: availability rule. Stored so a later reader can tell a v1 row from a v2 one
#: without trusting a changelog.
MACRO_SOURCE_VERSION = "macro_obs_v1_alfred_vintage"

#: How a curve row was produced. Columns are read BY NAME and a tenor absent
#: from a year's header produces no rows for that year.
CURVE_SOURCE_VERSION = "curve_obs_v1_treasury_par_by_name"

#: `pit_macro_obs.revision_class`. The first two are the schema's documented
#: vocabulary. NO_VINTAGE is the third state the schema comment did not
#: anticipate and the world requires: "we asked the archive and it has nothing
#: here", which is a different fact from "the value is zero" and from "we never
#: looked". A reader that treats NO_VINTAGE as an observation is wrong; use
#: `series_as_of`, which drops them.
REVISED = "REVISED"
NOT_REVISED = "NOT_REVISED"
NO_VINTAGE = "NO_VINTAGE"

#: Why a NO_VINTAGE row exists. Appended to `source` so the reason survives in
#: the row itself rather than in a log nobody keeps.
MISSING_PRE_ARCHIVE = "pre_archive_floor"
MISSING_HTTP_404 = "http_404"
MISSING_HEADER_MISMATCH = "header_mismatch"

SOURCE_ALFRED = "alfred:alfredgraph.csv"
SOURCE_FRED = "fred:fredgraph.csv"
SOURCE_TREASURY = "ust:daily_treasury_yield_curve"

CURVE_UST_PAR = "ust_par"


# --------------------------------------------------------------------------
# HTTP. Politeness is not optional and the .gov UA is a house rule.
# --------------------------------------------------------------------------

#: The house User-Agent. Mandatory on every .gov fetch; harmless and honest
#: everywhere else, so it is used everywhere. No browser UA is ever sent to a
#: .gov host and no anti-bot wall is circumvented anywhere.
HOUSE_USER_AGENT = "ShafferFinEval/1.0 (loganshaffer87@gmail.com)"

HTTP_TIMEOUT = 90

#: Minimum seconds between requests to one host. Natural latency is ~0.55 s a
#: call, so this rarely binds; it exists so a cached or failing host cannot be
#: hammered at loop speed.
MIN_REQUEST_INTERVAL = 0.12

#: Retries for a transient failure. A 404 is NOT transient -- it is the
#: archive telling the truth about its floor -- and is never retried.
HTTP_RETRIES = 3
HTTP_BACKOFF = 2.0

_last_request_at = 0.0
_session_obj: Any = None


def _session() -> Any:
    global _session_obj
    if _session_obj is None:
        import requests
        _session_obj = requests.Session()
        _session_obj.headers.update({"User-Agent": HOUSE_USER_AGENT})
    return _session_obj


def _throttle() -> None:
    global _last_request_at
    wait = MIN_REQUEST_INTERVAL - (time.time() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.time()


def _get(url: str) -> tuple[int, str]:
    """(status_code, body). Retries transient failures; never retries a 404.

    The body is returned as text and never written to disk. Every payload this
    module fetches is at most a few hundred kilobytes, and the disk is the
    binding constraint on this machine.
    """
    import requests

    last: Optional[Exception] = None
    for attempt in range(HTTP_RETRIES):
        _throttle()
        try:
            response = _session().get(url, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:      # transient by assumption
            last = exc
            time.sleep(HTTP_BACKOFF * (attempt + 1))
            continue
        if response.status_code == 404:
            return 404, ""
        if response.status_code >= 500:
            last = RuntimeError(f"HTTP {response.status_code}")
            time.sleep(HTTP_BACKOFF * (attempt + 1))
            continue
        return response.status_code, response.text
    raise SourceRejected(f"{url} failed after {HTTP_RETRIES} attempts: {last}")


class SourceRejected(RuntimeError):
    """A response could not be trusted and was discarded unparsed."""


class AlfredHeaderMismatch(SourceRejected):
    """ALFRED answered 200 with a vintage other than the one requested.

    This is the failure mode that has no other symptom: a future or otherwise
    unserviceable vintage_date returns today's revised series under a header
    naming today's date. Silently accepting it back-dates today's values.
    """


class DiskTooFull(RuntimeError):
    """Refused to start a phase because free space is below the floor."""


# --------------------------------------------------------------------------
# Disk. Checked before every phase, never after the damage.
# --------------------------------------------------------------------------

#: Refuse to begin a fetch below this. The whole ingest writes well under
#: 100 MB, so 2 GiB is not a budget -- it is a margin wide enough that a
#: surprise (a WAL that will not checkpoint) still has somewhere to go.
DISK_FLOOR_GIB = 2.0

#: Abort if the WAL grows past this while checkpoints keep coming back busy.
#: The earlier failure reached 9.3 GB precisely because nothing watched it.
WAL_CEILING_MIB = 512.0


def require_disk(path: str = pit_store.DEFAULT_PIT_DB_PATH,
                 floor_gib: float = DISK_FLOOR_GIB) -> str:
    """Raise DiskTooFull unless `floor_gib` is free on the store's volume.

    Called BEFORE each phase fetches anything. The failure this guards against
    already happened once on this machine: an ingest filled the volume and the
    error surfaced as a corrupted write rather than as a refusal.
    """
    target = path if os.path.exists(path) else os.path.dirname(os.path.abspath(path))
    usage = shutil.disk_usage(target)
    free_gib = usage.free / float(1 << 30)
    detail = (f"free={free_gib:.2f} GiB of {usage.total / float(1 << 30):.2f} GiB "
              f"({100.0 * usage.used / usage.total:.1f}% full)")
    if free_gib < floor_gib:
        raise DiskTooFull(
            f"refusing to fetch: {detail}, floor is {floor_gib:.2f} GiB. "
            f"Free space before re-running; do not lower the floor.")
    return detail


def wal_bytes(path: str = pit_store.DEFAULT_PIT_DB_PATH) -> int:
    wal = path + "-wal"
    return os.path.getsize(wal) if os.path.exists(wal) else 0


def checkpoint(conn: sqlite3.Connection,
               db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> str:
    """Truncate the WAL and REPORT WHAT ACTUALLY HAPPENED.

    `PRAGMA wal_checkpoint(TRUNCATE)` returns `(busy, log_pages, checkpointed)`
    and a leading 1 means it was refused and did nothing at all -- it does not
    raise, it does not warn, and the WAL keeps growing. That silence is how the
    earlier 9.3 GB WAL became invisible. This wrapper reads the tuple and says
    so, and it raises once the WAL is past `WAL_CEILING_MIB` with the
    checkpoint still refused, because at that point the only honest move is to
    stop rather than to keep writing into a log nothing can drain.

    TRUNCATE needs EVERY reader gone, so on a store other processes are using
    it is refused as a matter of course -- measured on this machine while a
    concurrent symbol ingest held the store: TRUNCATE returned (1, 73, 2) and
    PASSIVE returned (0, 73, 2) one statement later. PASSIVE is therefore tried
    as a fallback: it writes back what it can without waiting for anyone, which
    keeps the WAL from growing without bound even when it cannot be reset to
    zero. The status names which one ran.

    Must be called outside a transaction and with no open cursors, or SQLite
    reports busy against this very connection.
    """
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is None:                                  # not in WAL mode
        return "checkpoint: not applicable (journal mode is not WAL)"
    busy, log_pages, checkpointed = int(row[0]), int(row[1]), int(row[2])
    if not busy:
        return (f"checkpoint ok (TRUNCATE): {checkpointed} of {log_pages} "
                f"pages, wal now {wal_bytes(db_path) / float(1 << 20):.1f} MiB")

    fallback = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    p_busy, p_log, p_done = (int(fallback[0]), int(fallback[1]),
                             int(fallback[2])) if fallback else (1, log_pages, 0)
    size_mib = wal_bytes(db_path) / float(1 << 20)
    message = (f"checkpoint TRUNCATE busy (another process holds the store); "
               f"PASSIVE wrote back {p_done} of {p_log} pages, "
               f"wal {size_mib:.1f} MiB")
    if p_busy:
        message = (f"checkpoint BUSY both ways: nothing written back "
                   f"(log_pages={log_pages}, wal={size_mib:.1f} MiB)")
    if size_mib > WAL_CEILING_MIB:
        raise DiskTooFull(
            message + f" -- WAL is past the {WAL_CEILING_MIB:.0f} MiB ceiling "
                      f"and a reader is blocking it. Stopping before the "
                      f"volume fills, which is how the earlier ingest died.")
    return message


# --------------------------------------------------------------------------
# What is fetched, and why each choice was made
# --------------------------------------------------------------------------

#: Measured first vintage in the ALFRED archive, per series. These are hard
#: constraints, not guidance: below the floor the endpoint answers 404 and the
#: only correct row is a NO_VINTAGE marker. DTWEXBGS is the binding one -- it
#: has no vintage before 2019-02-04 and is revised on every observation
#: (143/143 against the 2020-01-02 vintage), so the dollar index simply does
#: not exist as a point-in-time input for the first seven years of the replay.
VINTAGE_FLOORS: dict[str, str] = {
    "INDPRO": "1927-01-26",
    "PAYEMS": "1955-05-06",
    "UNRATE": "1960-03-15",
    "CPIAUCSL": "1972-07-21",
    "T10Y2Y": "2014-01-27",
    "T10YIE": "2014-01-27",
    "VIXCLS": "2010-11-22",
    "SOFR": "2019-03-29",
    "DTWEXBGS": "2019-02-04",
}


class AlfredSpec:
    """One vintage-archived series and the cadence chosen for it.

    `window_years` is how far back of history each vintage request asks for.
    It is a real cost lever: a vintage is a whole series, so a 10-year window
    on a daily series is 2,500 rows per grid date and 20 on a quarterly one.
    The rule applied below is "the shortest window that lets every transform
    the deflator needs be computed inside ONE vintage" -- year-over-year needs
    13 months, a five-year real conversion needs 61.
    """

    __slots__ = ("series_id", "label", "frequency", "feature_role",
                 "window_years", "cadence", "units_note")

    def __init__(self, series_id: str, label: str, frequency: str,
                 feature_role: str, window_years: int, cadence: str,
                 units_note: str) -> None:
        self.series_id = series_id
        self.label = label
        self.frequency = frequency
        self.feature_role = feature_role
        self.window_years = window_years
        self.cadence = cadence
        self.units_note = units_note


#: CADENCE. Every series below is fetched at EVERY grid date (177 month-end
#: sessions from 2012-01-31), not a coarser cadence. Measured cost: ~0.55 s a
#: call, so a series is ~100 s and the whole set is ~20 minutes -- affordable,
#: and the alternative is worse than expensive. A quarterly vintage cadence
#: would mean a February as-of reading a December vintage, which for CPI is the
#: one substitution that is guaranteed wrong: the annual seasonal-factor
#: revision lands every February and rewrites five years of history at once.
#: DTWEXBGS is the single exception on window, not cadence: it is daily, so a
#: 10-year window would be 200,000 rows of mostly-redundant history.
ALFRED_SERIES: dict[str, AlfredSpec] = {
    "CPIAUCSL": AlfredSpec(
        "CPIAUCSL", "CPI, All Urban Consumers, All Items, SA", "monthly",
        "production", 10, "every_grid_date",
        "Index 1982-1984=100, seasonally adjusted. THE DEFLATOR. Revised every "
        "February when BLS re-estimates seasonal factors over the prior five "
        "years, so any given month has several prints that differ."),
    "PCEPILFE": AlfredSpec(
        "PCEPILFE", "PCE Price Index excluding Food and Energy", "monthly",
        "research_candidate", 10, "every_grid_date",
        "Chain-type index, rebased inside the replay window (2012=100 -> "
        "2017=100). A level from two vintages is two different units."),
    "PAYEMS": AlfredSpec(
        "PAYEMS", "All Employees, Total Nonfarm", "monthly",
        "research_candidate", 5, "every_grid_date",
        "Thousands of persons, SA. Revised in each of the two months after "
        "first print, then again at the annual benchmark."),
    "UNRATE": AlfredSpec(
        "UNRATE", "Unemployment Rate", "monthly",
        "research_candidate", 5, "every_grid_date",
        "Percent, SA. The level is rarely restated but the seasonal factors "
        "are, so history moves by a tenth."),
    "GDP": AlfredSpec(
        "GDP", "Gross Domestic Product", "quarterly",
        "research_candidate", 10, "every_grid_date",
        "Billions of dollars, SAAR. Three prints a quarter (advance, second, "
        "third) plus annual and comprehensive revisions."),
    "INDPRO": AlfredSpec(
        "INDPRO", "Industrial Production, Total Index", "monthly",
        "research_candidate", 5, "every_grid_date",
        "Index, rebased inside the replay window (2012=100 -> 2017=100) and "
        "re-benchmarked annually."),
    "RSAFS": AlfredSpec(
        "RSAFS", "Advance Retail Sales, Retail and Food Services", "monthly",
        "research_candidate", 5, "every_grid_date",
        "Millions of dollars, SA. The advance estimate is superseded twice."),
    "DTWEXBGS": AlfredSpec(
        "DTWEXBGS", "Nominal Broad U.S. Dollar Index", "daily",
        "research_candidate", 1, "every_grid_date_from_2019_02_04",
        "Index 2006-01-02=100. No vintage exists before 2019-02-04 and every "
        "observation is revised (143/143 against the 2020-01-02 vintage), so "
        "pre-2019 grid dates carry NO_VINTAGE rows, not values."),
}


class FredSpec:
    """One never-revised series, read once and sliced locally.

    `lag_sessions` is how many trading sessions after the observation date the
    value could first be read. "Never revised" is a statement about VALUES, not
    about the observation SET: DCOILWTICO values that were present in a 2015
    vintage are blank in today's file, so `retrieved_at` is recorded on every
    row and the manifest says so out loud.
    """

    __slots__ = ("series_id", "label", "frequency", "feature_role",
                 "lag_sessions", "lag_reason", "units_note")

    def __init__(self, series_id: str, label: str, frequency: str,
                 feature_role: str, lag_sessions: int, lag_reason: str,
                 units_note: str) -> None:
        self.series_id = series_id
        self.label = label
        self.frequency = frequency
        self.feature_role = feature_role
        self.lag_sessions = lag_sessions
        self.lag_reason = lag_reason
        self.units_note = units_note


_H15 = ("H.15 is released at about 16:15 ET on the observation day, after the "
        "15:30 ET decision cutoff in information_latency_policy_v1, so the "
        "value is first usable on the next trading session.")

#: THE NOT_REVISED LABEL WAS TESTED, NOT ASSUMED. Four of these series do have
#: an ALFRED vintage archive, so the claim is falsifiable and was falsified
#: where it is false. Measured 2026-09-21 against the 2015-06-30 vintage over
#: 2014-06-02..2015-06-30:
#:
#:   T10Y2Y      271 shared values, 0 differ, 0 vanished   label holds
#:   T10YIE      271 shared values, 0 differ, 0 vanished   label holds
#:   VIXCLS      272 shared values, 0 differ, 0 vanished   label holds
#:   DGS10       271 shared values, 0 differ, 0 vanished   label holds
#:   DCOILWTICO  255 shared values, 0 differ, 12 VANISHED  values hold, set does not
#:
#: The never-revised set. BAA10Y and AAA10Y are the approved credit spread and
#: are labelled precisely: they are OPTION-UNADJUSTED SEASONED-CORPORATE YIELD
#: SPREADS OVER THE 10-YEAR TREASURY. They are NOT option-adjusted spreads. An
#: OAS strips the value of embedded calls out of the spread; these do not, so a
#: widening here can be a call option repricing rather than credit risk. Never
#: label, chart or describe them as an OAS.
FRED_SERIES: dict[str, FredSpec] = {
    "DGS10": FredSpec("DGS10", "10-Year Treasury Constant Maturity", "daily",
                      "production", 1, _H15, "Percent per annum."),
    "DGS2": FredSpec("DGS2", "2-Year Treasury Constant Maturity", "daily",
                     "production", 1, _H15, "Percent per annum."),
    "DGS3MO": FredSpec("DGS3MO", "3-Month Treasury Constant Maturity", "daily",
                       "production", 1, _H15, "Percent per annum."),
    "DGS30": FredSpec("DGS30", "30-Year Treasury Constant Maturity", "daily",
                      "research_candidate", 1, _H15,
                      "Percent per annum. Not published 2002-02-19..2006-02-08, "
                      "when the 30-year bond was not issued."),
    "DTB3": FredSpec("DTB3", "3-Month Treasury Bill, Secondary Market", "daily",
                     "research_candidate", 1, _H15,
                     "Percent, discount basis -- NOT comparable to DGS3MO's "
                     "coupon-equivalent constant maturity without conversion."),
    "DFII10": FredSpec("DFII10", "10-Year TIPS Constant Maturity", "daily",
                       "research_candidate", 1, _H15,
                       "Percent per annum, real (inflation-indexed)."),
    "T10Y2Y": FredSpec("T10Y2Y", "10-Year minus 2-Year Treasury", "daily",
                       "production", 1, _H15,
                       "Percentage points. A derived difference of two H.15 "
                       "series, published with them."),
    "T10YIE": FredSpec("T10YIE", "10-Year Breakeven Inflation Rate", "daily",
                       "production", 1, _H15,
                       "Percentage points. DGS10 minus DFII10; a breakeven, "
                       "which is expected inflation PLUS an unobserved "
                       "inflation risk premium and liquidity premium."),
    "BAA10Y": FredSpec("BAA10Y", "Moody's Baa Corporate Yield minus 10-Year "
                       "Treasury", "daily", "production", 1, _H15,
                       "Percentage points. An OPTION-UNADJUSTED seasoned "
                       "long-term corporate yield spread over the 10-year. "
                       "NEVER an OAS: embedded call options are not stripped "
                       "out, so part of any move is optionality, not credit."),
    "AAA10Y": FredSpec("AAA10Y", "Moody's Aaa Corporate Yield minus 10-Year "
                       "Treasury", "daily", "production", 1, _H15,
                       "Percentage points. Option-unadjusted seasoned "
                       "corporate spread over the 10-year. NEVER an OAS."),
    "DFF": FredSpec("DFF", "Effective Federal Funds Rate", "daily",
                    "research_candidate", 1,
                    "The New York Fed publishes the prior day's effective rate "
                    "at about 09:00 ET, so it is first usable the next session.",
                    "Percent per annum."),
    "SOFR": FredSpec("SOFR", "Secured Overnight Financing Rate", "daily",
                     "research_candidate", 1,
                     "Published at about 08:00 ET for the prior business day.",
                     "Percent per annum. Series begins 2018-04-03; it did not "
                     "exist for the first six years of the replay window."),
    "VIXCLS": FredSpec("VIXCLS", "CBOE Volatility Index, Close", "daily",
                       "production", 1,
                       "A closing value is only knowable after the close, so "
                       "it is actionable on the next session.",
                       "Index points."),
    "DCOILWTICO": FredSpec("DCOILWTICO", "Crude Oil Prices: WTI, Cushing",
                           "daily", "research_candidate", 2,
                           "EIA posts its daily spot series with a two-session "
                           "lag, which is why the file's last observation runs "
                           "two days behind the H.15 series in the same pull.",
                           "Dollars per barrel. MEASURED DEFECT, not a "
                           "caveat: against the 2015-06-30 ALFRED vintage over "
                           "2014-06-02..2015-06-30, 0 of 255 shared values "
                           "differ but 12 observations PRESENT then are BLANK "
                           "today (2014-10-14, 2014-10-27, 2014-12-03 among "
                           "them). 'Not revised' governs VALUES, not the "
                           "observation SET, so a replay reading this series "
                           "at a 2015 as-of sees ~4.5% fewer points than the "
                           "strategy would have seen. Fix by ingesting it as a "
                           "vintage series if that gap ever matters."),
    "DHHNGSP": FredSpec("DHHNGSP", "Henry Hub Natural Gas Spot Price", "daily",
                        "research_candidate", 2,
                        "Same EIA daily pipeline as DCOILWTICO; two sessions.",
                        "Dollars per million BTU."),
    "MORTGAGE30US": FredSpec("MORTGAGE30US", "30-Year Fixed Rate Mortgage "
                             "Average", "weekly", "research_candidate", 1,
                             "The PMMS survey is released Thursday morning for "
                             "the week dated that Thursday; the next session is "
                             "the conservative read.",
                             "Percent per annum."),
}

#: How far back the never-revised series are sliced. The replay window starts
#: ~2012 (the peer universe is 24 entities at mid-2009 and 1,733 at mid-2011),
#: but rate history is cheap and a regime study wants more than the window, so
#: the slice starts where the Treasury curve does.
FRED_SLICE_START = "1990-01-01"

#: Treasury par-curve years. 1990 is the first year the endpoint serves; 1989
#: and earlier answer 200 with a ZERO-LENGTH BODY, which means "year not
#: covered" and must never be read as "a year with no trading days".
CURVE_FIRST_YEAR = 1990

#: Treasury column headings -> canonical tenor codes. Read BY NAME, always.
#: The "30 Yr" column is ABSENT from the 2003 and 2005 files and returns on
#: 2006-02-09; a positional reader would silently shift 20-year yields into the
#: 30-year slot for two whole years and nothing would look wrong. Confirmed
#: live after ingest: the 2003-12-31 curve ends at 20Y = 5.10 and has no 30Y.
#:
#: Introduction and gap dates, MEASURED from the 37 fetched files rather than
#: asserted -- each tenor's first non-blank cell, which is what gates it:
#:
#:   10Y 7Y 5Y 3Y 2Y 1Y 6M 3M   1990-01-02 (the endpoint's first covered year)
#:   20Y                        1993-10-01 (absent 1987-1993)
#:   30Y                        1990-01-02, then NOTHING from 2002-02-16
#:                              through 2006-02-08, resuming 2006-02-09
#:   1M                         2001-07-31
#:   2M                         2018-10-16
#:   4M                         2022-10-19
#:   6W ("1.5 Month")           2025-02-18
TREASURY_TENORS: dict[str, str] = {
    "1 Mo": "1M",
    "1.5 Month": "6W",
    "2 Mo": "2M",
    "3 Mo": "3M",
    "4 Mo": "4M",
    "6 Mo": "6M",
    "1 Yr": "1Y",
    "2 Yr": "2Y",
    "3 Yr": "3Y",
    "5 Yr": "5Y",
    "7 Yr": "7Y",
    "10 Yr": "10Y",
    "20 Yr": "20Y",
    "30 Yr": "30Y",
}

#: Ordering for display and for a slope computation, shortest first.
TENOR_ORDER = ("1M", "6W", "2M", "3M", "4M", "6M",
               "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y")


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

def alfred_url(series_id: str, vintage: str, start: str, end: str) -> str:
    """One ALFRED vintage of one series over [start, end]."""
    return ("https://alfred.stlouisfed.org/graph/alfredgraph.csv"
            f"?id={series_id}&vintage_date={vintage}&cosd={start}&coed={end}")


def fred_url(series_id: str) -> str:
    """The whole current series, read once and sliced locally."""
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"


def treasury_url(year: int) -> str:
    """One calendar year of the daily par yield curve."""
    return ("https://home.treasury.gov/resource-center/data-chart-center/"
            f"interest-rates/daily-treasury-rates.csv/{year}/all"
            f"?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
            "&page&_format=csv")


# --------------------------------------------------------------------------
# Parsing. Each parser validates before it yields a single value.
# --------------------------------------------------------------------------

def _number(text: str) -> Optional[float]:
    """FRED writes a missing observation as '.', Treasury as ''."""
    text = (text or "").strip()
    if not text or text == ".":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def alfred_header_field(series_id: str, vintage: str) -> str:
    """The exact second header field ALFRED must return for this request."""
    return f"{series_id}_{vintage.replace('-', '')}"


def parse_alfred_csv(text: str, series_id: str, vintage: str
                     ) -> list[tuple[str, Optional[float]]]:
    """[(obs_date, value)] from one ALFRED vintage payload, or raise.

    THE VALIDATION IS THE POINT. Measured 2026-09-21: a vintage_date in the
    future returns HTTP 200 and today's revised values under the header
    `CPIAUCSL_20260921` -- the correct-looking response for the wrong date.
    The header's second field is the only field that differs, so it is checked
    against the request before any row is read, and a mismatch raises rather
    than returning a shorter list or a flag a caller can forget to test.
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2:
        raise SourceRejected(
            f"{series_id} vintage {vintage}: no CSV header in {len(text)} bytes")
    header = [h.strip() for h in rows[0]]
    expected = alfred_header_field(series_id, vintage)
    if header[1] != expected:
        raise AlfredHeaderMismatch(
            f"{series_id} vintage {vintage}: ALFRED answered with header "
            f"'{header[1]}', not '{expected}'. This is the silent-substitution "
            f"failure -- the payload carries a different vintage and has been "
            f"discarded unparsed.")
    out: list[tuple[str, Optional[float]]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        obs = row[0].strip()
        if len(obs) != 10 or obs[4] != "-":
            continue
        out.append((obs, _number(row[1])))
    return out


def parse_fred_csv(text: str, series_id: str,
                   start: Optional[str] = None
                   ) -> list[tuple[str, Optional[float]]]:
    """[(obs_date, value)] from the current-series payload, sliced locally.

    The header's second field is the bare series id here (there is no vintage
    to echo), and it is still checked: a mistyped id returns an HTML error page
    with a 200, and a parser that only looked for date-shaped lines would
    return an empty list and call it a series with no observations.
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2:
        raise SourceRejected(f"{series_id}: no CSV header in {len(text)} bytes")
    header = [h.strip() for h in rows[0]]
    if header[1] != series_id:
        raise SourceRejected(
            f"{series_id}: FRED answered with column '{header[1]}'")
    out: list[tuple[str, Optional[float]]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        obs = row[0].strip()
        if len(obs) != 10 or obs[4] != "-":
            continue
        if start and obs < start:
            continue
        out.append((obs, _number(row[1])))
    return out


class CurveYear:
    """One parsed Treasury year: rows, the tenors present, and what was blank."""

    __slots__ = ("year", "rows", "tenors_present", "unmapped_columns",
                 "blank_cells", "first_by_tenor", "last_by_tenor")

    def __init__(self, year: int) -> None:
        self.year = year
        self.rows: list[tuple[str, str, float]] = []      # (obs_date, tenor, value)
        self.tenors_present: list[str] = []
        self.unmapped_columns: list[str] = []
        self.blank_cells: dict[str, int] = {}
        self.first_by_tenor: dict[str, str] = {}
        self.last_by_tenor: dict[str, str] = {}


class YearNotCovered(SourceRejected):
    """The endpoint answered 200 with an empty body: no such year in the data.

    Distinct from a year of closures. 1989 and 2027 both return zero bytes
    today; treating that as "a year with no trading days" would silently create
    a hole in the curve that looks like a market event.
    """


def parse_treasury_csv(text: str, year: int) -> CurveYear:
    """One year of the daily par yield curve, read BY COLUMN NAME.

    Three measured facts drive this parser:

    * An uncovered year answers HTTP 200 with a ZERO-LENGTH BODY. That is
      `YearNotCovered`, never an empty but valid year.
    * The "30 Yr" column is absent from the 2003 and 2005 headers entirely and
      reappears on 2006-02-09. A tenor is therefore gated by the data: absent
      column -> no rows; present but blank cell -> no row, counted.
    * Rows arrive newest-first in M/D/YYYY. Both are normalised here so the
      caller never sees a provider's presentation choices.
    """
    if not text.strip():
        raise YearNotCovered(
            f"{year}: HTTP 200 with a zero-length body -- year not covered by "
            f"the endpoint. This is not a year without trading days.")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    if not fieldnames or fieldnames[0] != "Date":
        raise SourceRejected(f"{year}: unexpected header {fieldnames[:3]}")

    out = CurveYear(year)
    mapped: list[tuple[str, str]] = []
    for name in fieldnames[1:]:
        tenor = TREASURY_TENORS.get(name)
        if tenor is None:
            out.unmapped_columns.append(name)         # recorded, never guessed
            continue
        mapped.append((name, tenor))
    out.tenors_present = [t for _, t in mapped]

    for raw in reader:
        stamp = (raw.get("Date") or "").strip()
        obs = _treasury_date(stamp)
        if obs is None:
            continue
        for name, tenor in mapped:
            value = _number(raw.get(name) or "")
            if value is None:
                out.blank_cells[tenor] = out.blank_cells.get(tenor, 0) + 1
                continue
            out.rows.append((obs, tenor, value))
            if tenor not in out.first_by_tenor or obs < out.first_by_tenor[tenor]:
                out.first_by_tenor[tenor] = obs
            if tenor not in out.last_by_tenor or obs > out.last_by_tenor[tenor]:
                out.last_by_tenor[tenor] = obs
    out.rows.sort()
    return out


def _treasury_date(stamp: str) -> Optional[str]:
    """'12/31/2003' or '12/31/2003 12:00:00 AM' -> '2003-12-31'."""
    head = stamp.split(" ")[0]
    parts = head.split("/")
    if len(parts) != 3:
        return None
    try:
        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Availability. A value is available on a SESSION, because the grid is sessions.
# --------------------------------------------------------------------------

def availability_resolver(conn: sqlite3.Connection, market: str = "XNYS"
                          ) -> Callable[[str, int], str]:
    """Build `available_after(obs_date, sessions)` backed by `pit_calendar`.

    Availability is counted in TRADING SESSIONS rather than calendar days for
    the same reason horizons are: the replay only ever asks a question on a
    session, so "one business day later" has to land on one. The sessions are
    XNYS because that is the market the strategy trades -- the bond market's
    own calendar differs (it is closed on Columbus Day and Veterans Day, when
    the NYSE is open), but a rate the strategy cannot act on until the equity
    market opens is not available to it any earlier.

    The whole calendar is read once into memory (8,467 dates, well under a
    megabyte) because this is called once per observation across ~150,000 of
    them and a query each time would dominate the ingest.

    Past the end of the calendar it falls back to calendar days skipping
    weekends. That fallback is correct for the tail of a daily series fetched
    after the calendar was last extended, and it is the only place in this
    module where a date is not a known session.
    """
    sessions = [r[0] for r in conn.execute(
        "SELECT session_date FROM pit_calendar WHERE market = ? ORDER BY session_date",
        (market,))]
    index = {day: i for i, day in enumerate(sessions)}
    last = sessions[-1] if sessions else ""

    def resolve(obs_date: str, lag_sessions: int) -> str:
        if lag_sessions <= 0:
            # Zero lag means "the first session ON OR AFTER", not "this date".
            # A vintage published on a Saturday is first actionable on Monday,
            # and returning the Saturday would put an availability date on a
            # day the grid never asks about.
            above = _bisect_right(sessions, obs_date)
            if above and sessions[above - 1] == obs_date:
                return obs_date
            if above < len(sessions):
                return sessions[above]
            return _weekday_on_or_after(obs_date)
        position = index.get(obs_date)
        if position is None:
            position = _bisect_right(sessions, obs_date) - 1
        target = position + lag_sessions
        if 0 <= target < len(sessions):
            return sessions[target]
        return _weekday_offset(max(obs_date, last), lag_sessions)

    return resolve


def _bisect_right(ordered: Sequence[str], value: str) -> int:
    low, high = 0, len(ordered)
    while low < high:
        middle = (low + high) // 2
        if value < ordered[middle]:
            high = middle
        else:
            low = middle + 1
    return low


def _weekday_on_or_after(start: str) -> str:
    """The first weekday on or after `start`. Used only past the calendar's end."""
    current = _dt.date.fromisoformat(start)
    while current.weekday() >= 5:
        current += _dt.timedelta(days=1)
    return current.isoformat()


def _weekday_offset(start: str, days: int) -> str:
    """`days` weekdays after `start`. Used only past the calendar's end."""
    current = _dt.date.fromisoformat(start)
    moved = 0
    while moved < days:
        current += _dt.timedelta(days=1)
        if current.weekday() < 5:
            moved += 1
    return current.isoformat()


def available_after(conn: sqlite3.Connection, obs_date: str,
                    lag_sessions: int, market: str = "XNYS") -> str:
    """One-shot form of `availability_resolver` for a caller with one date."""
    return availability_resolver(conn, market)(obs_date, lag_sessions)


# --------------------------------------------------------------------------
# Derivations. Measured from the rows fetched, never asserted.
# --------------------------------------------------------------------------

def derive_base_tag(observations: Sequence[tuple[str, Optional[float]]],
                    frequency: str, tolerance: float = 0.05) -> Optional[str]:
    """'2017=100' if a calendar year inside the window averages 100, else None.

    An index's base period is not carried anywhere in the CSV, and guessing it
    from memory is exactly the kind of assertion this store exists to avoid. So
    it is MEASURED: an index based on year Y has, by construction, a mean of
    100.0 across Y's observations. When the window does not reach the base year
    the answer is None -- which is honest, and is what CPIAUCSL returns here
    because its 1982-1984 base is decades before any fetched window.

    None means "not determinable from these observations", never "no base".
    """
    if frequency not in ("monthly", "quarterly"):
        return None
    by_year: dict[str, list[float]] = {}
    for obs, value in observations:
        if value is None:
            continue
        by_year.setdefault(obs[:4], []).append(value)
    expected = 12 if frequency == "monthly" else 4
    for year in sorted(by_year, reverse=True):
        values = by_year[year]
        if len(values) != expected:
            continue
        if abs(statistics.fmean(values) - 100.0) <= tolerance:
            return f"{year}=100"
    return None


def derive_publication_lag(conn: sqlite3.Connection, series_id: str
                           ) -> Optional[int]:
    """Median days from an observation date to the FIRST vintage carrying it.

    Measured rather than declared: the earliest vintage in which an
    observation appears is the release that first printed it, so
    `min(vintage_date) - obs_date` per observation is the lag and the median
    across observations is the series' cadence.

    READ THIS AS AN UPPER BOUND, NOT A RELEASE LAG. Its resolution is the
    resolution of the vintage grid, and the grid is MONTH-END. CPI for
    2012-02-01 was published 2012-03-16, but the earliest vintage this store
    holds for it is 2012-03-30, so the number derived here is ~58 days where
    the truth is ~16. That is the honest consequence of a month-end cadence,
    not an error -- and `refine_first_vintage` exists to pin the exact release
    date for an observation that matters.

    Returns None when fewer than four observations qualify. The first and last
    observations are excluded: the oldest is censored by the fetch window (its
    true first vintage predates anything fetched) and the newest may not have
    been printed yet.
    """
    rows = conn.execute(
        """SELECT obs_date, MIN(vintage_date) AS first_vintage
             FROM pit_macro_obs
            WHERE series_id = ? AND revision_class != ?
            GROUP BY obs_date
            ORDER BY obs_date""",
        (series_id, NO_VINTAGE)).fetchall()
    if len(rows) < 6:
        return None
    oldest_vintage = min(str(r["first_vintage"]) for r in rows)
    lags: list[int] = []
    for row in rows[1:-1]:
        first = str(row["first_vintage"])
        if first <= oldest_vintage:          # censored by the fetch window
            continue
        lags.append((_dt.date.fromisoformat(first)
                     - _dt.date.fromisoformat(str(row["obs_date"]))).days)
    if len(lags) < 4:
        return None
    return int(round(statistics.median(lags)))


def refine_first_vintage(conn: sqlite3.Connection, spec: AlfredSpec,
                         obs_date: str, low: str, high: str,
                         write: bool = True,
                         db_path: str = pit_store.DEFAULT_PIT_DB_PATH
                         ) -> dict[str, Any]:
    """Binary-search ALFRED for the exact vintage that FIRST printed one value.

    The month-end grid can only say "this observation existed by the 30th". It
    cannot say when it was released, and for a first-print demonstration that
    difference is the whole claim: a value read from the 2012-03-30 vintage is
    the first print only if nothing revised it between the 16th and the 30th.

    So this asks directly. Each probe is one ALFRED call for a single
    observation date (`cosd = coed = obs_date`), which is a few hundred bytes,
    and the search over a one-month bracket converges in about five of them.
    The invariant the search relies on is monotone: once an observation has
    been published it appears in every later vintage, so "present" is a
    step function of the vintage date and bisection is valid.

    A 404 inside the bracket means the archive has no vintage for that day at
    all (ALFRED does not archive every calendar date), which is treated as
    "not yet published" -- the conservative direction, because it can only
    push the answer later, never earlier.

    When `write` is true the discovered vintage is stored like any other row.
    Its `available_date` is resolved forward to the first trading session on
    or after the release, because a Friday-evening release is not actionable
    until Monday and the replay only ever asks on sessions.
    """
    require_disk(db_path)
    retrieved = _dt.date.today().isoformat()
    resolve = availability_resolver(conn)
    probes: list[tuple[str, bool]] = []

    def present(vintage: str) -> bool:
        status, body = _get(alfred_url(spec.series_id, vintage,
                                       obs_date, obs_date))
        if status == 404:
            probes.append((vintage, False))
            return False
        try:
            rows = parse_alfred_csv(body, spec.series_id, vintage)
        except AlfredHeaderMismatch:
            probes.append((vintage, False))
            return False
        found = any(o == obs_date and v is not None for o, v in rows)
        probes.append((vintage, found))
        return found

    lo = _dt.date.fromisoformat(low)
    hi = _dt.date.fromisoformat(high)
    if not present(hi.isoformat()):
        return {"series_id": spec.series_id, "obs_date": obs_date,
                "first_vintage": None, "probes": len(probes),
                "status": "not published by " + high}
    while lo < hi:
        middle = lo + (hi - lo) // 2
        if present(middle.isoformat()):
            hi = middle
        else:
            lo = middle + _dt.timedelta(days=1)

    first_vintage = lo.isoformat()
    status, body = _get(alfred_url(spec.series_id, first_vintage,
                                   _minus_years(first_vintage,
                                                spec.window_years),
                                   first_vintage))
    value = None
    written = 0
    if status == 200:
        rows = parse_alfred_csv(body, spec.series_id, first_vintage)
        base_tag = derive_base_tag(rows, spec.frequency)
        available = resolve(first_vintage, 0)
        source = _alfred_source(retrieved)
        batch = [(spec.series_id, o, first_vintage, v, available, REVISED,
                  base_tag, source) for o, v in rows if o <= first_vintage]
        value = next((v for o, v in rows if o == obs_date), None)
        if write:
            written = _flush(conn, batch)
            checkpoint(conn, db_path)
    return {"series_id": spec.series_id, "obs_date": obs_date,
            "first_vintage": first_vintage, "first_value": value,
            "lag_days": (_dt.date.fromisoformat(first_vintage)
                         - _dt.date.fromisoformat(obs_date)).days,
            "probes": len(probes), "rows_written": written, "status": "ok"}


def detect_rebases(conn: sqlite3.Connection, series_id: str,
                   min_shift: float = 0.01,
                   max_spread_fraction: float = 0.25) -> list[dict[str, Any]]:
    """Consecutive vintage pairs whose whole overlap moved by one factor.

    A revision moves SOME observations. A REBASE moves ALL of them by roughly
    the same multiplicative factor, because the units changed. The two are
    indistinguishable inside a single vintage and completely different in
    consequence: a month-over-month computed across a rebase manufactures a
    several-percent shock on a date when nothing happened.

    THE THRESHOLDS ARE MEASURED, not chosen. On PCEPILFE in this store, three
    rebase pairs and a representative ordinary pair are:

        2013-07-31 -> 2013-08-30   median 0.9235   spread 0.0108
        2018-06-29 -> 2018-07-31   median 0.9546   spread 0.0031
        2023-08-31 -> 2023-09-29   median 0.9269   spread 0.0051
        2015-05-29 -> 2015-06-30   median 1.000000 spread 0.000185

    Spread alone does NOT separate them: a real rebase arrives bundled with a
    comprehensive revision, so its scatter is ten to fifty times an ordinary
    month's. What separates them is the SHIFT -- 4.5% to 7.7% against 0.0000%.
    The rule is therefore "the level moved by more than `min_shift`, and the
    scatter around that move is a small fraction of it".

    An earlier version of this function tested spread alone against 0.002 and
    found NONE of the three real rebases. That is recorded here because a
    detector that silently returns an empty list is worse than no detector.
    """
    vintages = [str(r[0]) for r in conn.execute(
        """SELECT DISTINCT vintage_date FROM pit_macro_obs
            WHERE series_id = ? AND revision_class != ?
            ORDER BY vintage_date""", (series_id, NO_VINTAGE))]
    found: list[dict[str, Any]] = []
    previous: Optional[dict[str, float]] = None
    previous_vintage = ""
    for vintage in vintages:
        current = {str(r["obs_date"]): float(r["value"]) for r in conn.execute(
            """SELECT obs_date, value FROM pit_macro_obs
                WHERE series_id = ? AND vintage_date = ? AND value IS NOT NULL""",
            (series_id, vintage))}
        if previous:
            shared = [k for k in current if k in previous and previous[k]]
            if len(shared) >= 12:
                ratios = sorted(current[k] / previous[k] for k in shared)
                middle = statistics.median(ratios)
                spread = ratios[-1] - ratios[0]
                shift = abs(middle - 1.0)
                if shift > min_shift and spread <= max_spread_fraction * shift:
                    found.append({
                        "series_id": series_id,
                        "from_vintage": previous_vintage,
                        "to_vintage": vintage,
                        "ratio": round(middle, 6),
                        "overlap": len(shared),
                        "ratio_spread": round(spread, 6),
                        "shift_pct": round(100.0 * (middle - 1.0), 3),
                        "spread_over_shift": round(spread / shift, 3),
                    })
        previous, previous_vintage = current, vintage
    return found


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def insert_curve_obs(conn: sqlite3.Connection, rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert (curve_id, obs_date, tenor, value, available_date, source).

    INSERT OR IGNORE against `UNIQUE (curve_id, obs_date, tenor)`, so a re-run
    of a year costs nothing and cannot double-count. pit_store has no helper
    for this table; it does for every other one this module touches.
    """
    cursor = conn.executemany(
        """INSERT OR IGNORE INTO pit_curve_obs
               (curve_id, obs_date, tenor, value, available_date, source)
           VALUES (?, ?, ?, ?, ?, ?)""", rows)
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def upsert_manifest(conn: sqlite3.Connection, series_id: str, label: str,
                    frequency: str, revision_class: str,
                    vintage_coverage_start: Optional[str],
                    series_first_obs: Optional[str],
                    publication_lag_days: Optional[int],
                    feature_role: str, units_note: str) -> str:
    """One manifest row per series, replaced wholesale on re-ingest.

    The manifest is DERIVED STATE, not evidence: every field is re-measured
    from the rows that were just written, so REPLACE is correct here in a way
    it never is for `pit_macro_obs`. `vintage_coverage_start` is the earliest
    vintage actually in the store, not the archive's advertised floor.
    """
    with pit_store.transaction(conn):
        conn.execute(
            """INSERT OR REPLACE INTO pit_macro_manifest
                   (series_id, label, frequency, revision_class,
                    vintage_coverage_start, series_first_obs,
                    publication_lag_days, feature_role, units_note, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (series_id, label, frequency, revision_class,
             vintage_coverage_start, series_first_obs, publication_lag_days,
             feature_role, units_note,
             _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")))
    return f"manifest {series_id}: {revision_class}, role={feature_role}"


def _alfred_source(retrieved: str, missing: Optional[str] = None) -> str:
    base = f"{SOURCE_ALFRED};v={MACRO_SOURCE_VERSION};retrieved={retrieved}"
    return base + (f";missing={missing}" if missing else "")


def _fred_source(retrieved: str) -> str:
    return f"{SOURCE_FRED};v={MACRO_SOURCE_VERSION};retrieved={retrieved}"


def replay_grid(conn: sqlite3.Connection, start: str = "2012-01-01",
                end: str = "2099-12-31", market: str = "XNYS") -> list[str]:
    """The month-end sessions that are the replay's as-of grid.

    Measured in this store: 177 dates from 2012-01-31, of which 165 are from
    2013. The window genuinely starts around 2012 -- the peer universe is 24
    entities at mid-2009, 542 at mid-2010 and 1,733 at mid-2011, so an earlier
    grid date has no cross-section to score.

    CAVEAT, and it is a real one: `pit_calendar` currently ends 2026-09-18, and
    `insert_calendar` marks the last session of each month as a month end, so
    the final grid date is a truncation artifact rather than a true month end.
    It is kept because for a macro vintage it is simply "the most recent date
    we can ask about", but a replay that treats it as a month boundary is
    wrong.
    """
    return pit_store.month_end_sessions(conn, start, end, market)


def ingest_alfred_series(conn: sqlite3.Connection, spec: AlfredSpec,
                         grid: Sequence[str],
                         db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                         checkpoint_every: int = 40,
                         progress: Optional[Callable[[str], None]] = None
                         ) -> dict[str, Any]:
    """Fetch one vintage per grid date and write it. Returns a status record.

    Below the series' archive floor NOTHING IS FETCHED and a NO_VINTAGE row is
    written instead, one per skipped grid date, carrying the reason. That is
    the whole point of the floors: DTWEXBGS has no vintage before 2019-02-04,
    so the honest historical answer for 2015 is "unavailable", and the
    dishonest one -- the current series wearing a 2015 vintage date -- is
    exactly what the 404 is protecting us from.

    `available_date` is the vintage date itself. A row IS a publication: it
    says "on this date the series read like this", so the date it could first
    be read is the date it was published. Earlier availability lives in earlier
    vintage rows, which is where it belongs.
    """
    require_disk(db_path)
    floor = VINTAGE_FLOORS.get(spec.series_id)
    retrieved = _dt.date.today().isoformat()
    report: dict[str, Any] = {
        "series_id": spec.series_id, "requested": len(grid), "fetched": 0,
        "rows": 0, "skipped_pre_floor": 0, "http_404": 0,
        "header_mismatch": 0, "gap_rows": 0, "base_tags": set(),
        "first_vintage": None, "first_obs": None, "checkpoints": [],
    }
    pending: list[tuple] = []
    gap_rows: list[tuple] = []

    for position, vintage in enumerate(grid, start=1):
        if floor and vintage < floor:
            gap_rows.append((spec.series_id, vintage, vintage, None, vintage,
                             NO_VINTAGE, None,
                             _alfred_source(retrieved, MISSING_PRE_ARCHIVE)))
            report["skipped_pre_floor"] += 1
            continue

        start = _minus_years(vintage, spec.window_years)
        status, body = _get(alfred_url(spec.series_id, vintage, start, vintage))
        if status == 404:
            gap_rows.append((spec.series_id, vintage, vintage, None, vintage,
                             NO_VINTAGE, None,
                             _alfred_source(retrieved, MISSING_HTTP_404)))
            report["http_404"] += 1
            continue
        if status != 200:
            raise SourceRejected(
                f"{spec.series_id} vintage {vintage}: HTTP {status}")
        try:
            observations = parse_alfred_csv(body, spec.series_id, vintage)
        except AlfredHeaderMismatch:
            gap_rows.append((spec.series_id, vintage, vintage, None, vintage,
                             NO_VINTAGE, None,
                             _alfred_source(retrieved, MISSING_HEADER_MISMATCH)))
            report["header_mismatch"] += 1
            continue

        report["fetched"] += 1
        base_tag = derive_base_tag(observations, spec.frequency)
        if base_tag:
            report["base_tags"].add(base_tag)
        source = _alfred_source(retrieved)
        for obs, value in observations:
            if obs > vintage:                # CHECK (vintage_date >= obs_date)
                continue
            pending.append((spec.series_id, obs, vintage, value, vintage,
                            REVISED, base_tag, source))

        if len(pending) >= 20000:
            report["rows"] += _flush(conn, pending)
        if position % checkpoint_every == 0:
            report["rows"] += _flush(conn, pending)
            note = checkpoint(conn, db_path)
            report["checkpoints"].append(note)
            if progress:
                progress(f"  {spec.series_id} {position}/{len(grid)}  {note}")

    report["rows"] += _flush(conn, pending)
    report["gap_rows"] = _flush(conn, gap_rows)
    report["checkpoints"].append(checkpoint(conn, db_path))
    report["base_tags"] = sorted(report["base_tags"])

    measured = conn.execute(
        """SELECT MIN(vintage_date) AS v, MIN(obs_date) AS o, COUNT(*) AS n
             FROM pit_macro_obs WHERE series_id = ? AND revision_class != ?""",
        (spec.series_id, NO_VINTAGE)).fetchone()
    report["first_vintage"] = measured["v"]
    report["first_obs"] = measured["o"]
    report["rows_in_store"] = int(measured["n"] or 0)

    lag = derive_publication_lag(conn, spec.series_id)
    report["publication_lag_days"] = lag
    upsert_manifest(
        conn, spec.series_id, spec.label, spec.frequency, REVISED,
        measured["v"], measured["o"], lag, spec.feature_role,
        f"{spec.units_note} Cadence: {spec.cadence}; window "
        f"{spec.window_years}y per vintage; archive floor "
        f"{floor or 'none measured'}. publication_lag_days is measured at "
        f"MONTH-END GRID RESOLUTION and is therefore an UPPER BOUND on the "
        f"release lag, not the release lag -- use refine_first_vintage to pin "
        f"an exact release date.")
    return report


#: How long to wait for another process's write lock before giving up. The
#: store is shared: a DERA/symbol ingest in another process holds the write
#: lock for seconds at a time, and Python's 5-second default turns that into
#: "database is locked" halfway through a phase.
BUSY_TIMEOUT_MS = 120000

LOCK_RETRIES = 5
LOCK_BACKOFF = 5.0


def prepare_connection(conn: sqlite3.Connection,
                       busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> str:
    """Make this connection tolerant of a store another process is writing.

    Connection-local, so it cannot change how anything else behaves. Called by
    `main` and by `ingest_all`; a caller wiring its own connection should call
    it too.
    """
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return f"busy_timeout={busy_timeout_ms} ms"


def _flush(conn: sqlite3.Connection, pending: list[tuple]) -> int:
    """Write a batch, waiting out another process's write lock if need be.

    `pending` is cleared only after a successful commit, so a retry re-sends
    exactly the same rows and UNIQUE makes the repeat free.
    """
    if not pending:
        return 0
    for attempt in range(LOCK_RETRIES):
        try:
            with pit_store.transaction(conn):
                written = pit_store.insert_macro_obs(conn, pending)
            pending.clear()
            return written
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc).lower():
                raise
            if attempt == LOCK_RETRIES - 1:
                raise
            time.sleep(LOCK_BACKOFF * (attempt + 1))
    return 0


def _flush_curve(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """`_flush` for `pit_curve_obs`, with the same shared-store lock patience."""
    if not rows:
        return 0
    for attempt in range(LOCK_RETRIES):
        try:
            with pit_store.transaction(conn):
                return insert_curve_obs(conn, rows)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc).lower():
                raise
            if attempt == LOCK_RETRIES - 1:
                raise
            time.sleep(LOCK_BACKOFF * (attempt + 1))
    return 0


def _minus_years(day: str, years: int) -> str:
    date = _dt.date.fromisoformat(day)
    try:
        return date.replace(year=date.year - years).isoformat()
    except ValueError:                                # 29 February
        return date.replace(year=date.year - years, day=28).isoformat()


def ingest_fred_series(conn: sqlite3.Connection, spec: FredSpec,
                       resolve: Callable[[str, int], str],
                       start: str = FRED_SLICE_START,
                       db_path: str = pit_store.DEFAULT_PIT_DB_PATH
                       ) -> dict[str, Any]:
    """Read one never-revised series once and slice it locally.

    `vintage_date` is set to the value's own publication date rather than to
    today. That is the modelling decision this function turns on. A
    never-revised series has exactly one print, so its vintage IS its release,
    and stamping today's date instead would make `macro_value_as_of` -- which
    filters `vintage_date <= as_of` -- return nothing at all for every
    historical as-of. The row then reads correctly in both selectors: it
    existed from its release, and it never changed.

    "Never revised" is a claim about VALUES. The observation SET is not stable:
    DCOILWTICO values that a 2015 vintage carried are blank in today's file.
    So `source` carries retrieved=<date> and the manifest says it in words.
    """
    require_disk(db_path)
    retrieved = _dt.date.today().isoformat()
    status, body = _get(fred_url(spec.series_id))
    if status != 200:
        raise SourceRejected(f"{spec.series_id}: HTTP {status} from FRED")
    observations = parse_fred_csv(body, spec.series_id, start)
    source = _fred_source(retrieved)

    rows: list[tuple] = []
    present = 0
    lags: list[int] = []
    for obs, value in observations:
        if value is not None:
            present += 1
        published = resolve(obs, spec.lag_sessions)
        lags.append((_dt.date.fromisoformat(published)
                     - _dt.date.fromisoformat(obs)).days)
        rows.append((spec.series_id, obs, published, value, published,
                     NOT_REVISED, None, source))
    written = _flush(conn, rows)
    note = checkpoint(conn, db_path)

    # The manifest column is in DAYS, and the policy above is in SESSIONS, so
    # the stored number is measured rather than copied: the median calendar
    # gap the session ladder actually produced. It comes out above the session
    # count wherever weekends and holidays intervene, which is the honest
    # answer to "how long until I could read this".
    lag_days = int(round(statistics.median(lags))) if lags else None
    first = observations[0][0] if observations else None
    last = observations[-1][0] if observations else None
    upsert_manifest(
        conn, spec.series_id, spec.label, spec.frequency, NOT_REVISED,
        None, first, lag_days, spec.feature_role,
        f"{spec.units_note} Availability: +{spec.lag_sessions} trading "
        f"session(s), a measured median of {lag_days} calendar days. "
        f"{spec.lag_reason} Retrieved {retrieved} as a single current pull; "
        f"values are never revised but the OBSERVATION SET is not stable "
        f"across vintages, so a row absent here may have been present in an "
        f"older vintage of the same series.")
    return {"series_id": spec.series_id, "observations": len(observations),
            "non_null": present, "rows": written, "first_obs": first,
            "last_obs": last, "retrieved_at": retrieved,
            "median_lag_days": lag_days, "checkpoint": note}


def ingest_curve_year(conn: sqlite3.Connection, year: int,
                      resolve: Callable[[str, int], str],
                      db_path: str = pit_store.DEFAULT_PIT_DB_PATH
                      ) -> dict[str, Any]:
    """One calendar year of the Treasury par curve, or a 'not covered' status.

    Availability is +1 trading session for the same reason the H.15 series are:
    the curve is posted after the close, so a strategy deciding at 15:30 ET
    cannot have seen that day's curve.
    """
    require_disk(db_path)
    status, body = _get(treasury_url(year))
    if status != 200:
        raise SourceRejected(f"treasury {year}: HTTP {status}")
    try:
        parsed = parse_treasury_csv(body, year)
    except YearNotCovered as exc:
        return {"year": year, "status": "not_covered", "rows": 0,
                "detail": str(exc)}
    source = f"{SOURCE_TREASURY};v={CURVE_SOURCE_VERSION}"
    rows = [(CURVE_UST_PAR, obs, tenor, value, resolve(obs, 1), source)
            for obs, tenor, value in parsed.rows]
    written = _flush_curve(conn, rows)
    return {"year": year, "status": "ok", "rows": written,
            "offered": len(parsed.rows),
            "tenors": parsed.tenors_present,
            "blank_cells": parsed.blank_cells,
            "unmapped_columns": parsed.unmapped_columns,
            "first_by_tenor": parsed.first_by_tenor}


def ingest_curve(conn: sqlite3.Connection, first_year: int = CURVE_FIRST_YEAR,
                 last_year: Optional[int] = None,
                 db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                 progress: Optional[Callable[[str], None]] = None
                 ) -> dict[str, Any]:
    """Every covered year of the par curve, with per-tenor introduction dates.

    The introduction dates are MEASURED from the files rather than asserted:
    each tenor's first non-blank cell across all years is where it starts. That
    is what gates a tenor -- the 30-year's 2002-2006 absence and the 2-month
    and 4-month bills' late arrival fall out of the data instead of out of a
    table someone has to maintain.
    """
    last_year = last_year or _dt.date.today().year
    resolve = availability_resolver(conn)
    summary: dict[str, Any] = {"years_ok": [], "years_not_covered": [],
                               "rows": 0, "first_by_tenor": {},
                               "unmapped_columns": set(), "checkpoints": []}
    for index, year in enumerate(range(first_year, last_year + 1), start=1):
        result = ingest_curve_year(conn, year, resolve, db_path)
        if result["status"] == "not_covered":
            summary["years_not_covered"].append(year)
        else:
            summary["years_ok"].append(year)
            summary["rows"] += int(result["rows"])
            for tenor, first in result["first_by_tenor"].items():
                if (tenor not in summary["first_by_tenor"]
                        or first < summary["first_by_tenor"][tenor]):
                    summary["first_by_tenor"][tenor] = first
            summary["unmapped_columns"].update(result["unmapped_columns"])
        if progress:
            progress(f"  curve {year}: {result['status']} "
                     f"rows={result['rows']}")
        if index % 10 == 0:
            summary["checkpoints"].append(checkpoint(conn, db_path))
    summary["checkpoints"].append(checkpoint(conn, db_path))
    summary["unmapped_columns"] = sorted(summary["unmapped_columns"])
    return summary


# --------------------------------------------------------------------------
# Readers. Thin, and each one exists because the raw selector has a sharp edge.
# --------------------------------------------------------------------------

def series_as_of(conn: sqlite3.Connection, series_id: str, as_of: str,
                 start: Optional[str] = None) -> list[sqlite3.Row]:
    """`pit_store.macro_series_as_of` with NO_VINTAGE markers dropped.

    USE THIS, not the raw selector, whenever a transform follows. The raw one
    correctly returns one vintage; what it cannot know is that a NO_VINTAGE
    marker is a record of absence rather than an observation, so for a series
    below its archive floor it would hand back a single NULL-valued row that
    looks like a data point.
    """
    return [r for r in pit_store.macro_series_as_of(conn, series_id, as_of, start)
            if r["revision_class"] != NO_VINTAGE]


def value_as_of(conn: sqlite3.Connection, series_id: str, obs_date: str,
                as_of: str) -> Optional[sqlite3.Row]:
    """`pit_store.macro_value_as_of` with NO_VINTAGE markers dropped."""
    row = pit_store.macro_value_as_of(conn, series_id, obs_date, as_of)
    if row is not None and row["revision_class"] == NO_VINTAGE:
        return None
    return row


def gaps(conn: sqlite3.Connection, series_id: Optional[str] = None
         ) -> list[sqlite3.Row]:
    """Every recorded absence: which series, which vintage date, and why."""
    if series_id:
        return conn.execute(
            """SELECT series_id, vintage_date, source FROM pit_macro_obs
                WHERE revision_class = ? AND series_id = ?
                ORDER BY vintage_date""", (NO_VINTAGE, series_id)).fetchall()
    return conn.execute(
        """SELECT series_id, COUNT(*) AS n, MIN(vintage_date) AS first,
                  MAX(vintage_date) AS last
             FROM pit_macro_obs WHERE revision_class = ?
            GROUP BY series_id ORDER BY series_id""", (NO_VINTAGE,)).fetchall()


def curve_as_of(conn: sqlite3.Connection, obs_date: str, as_of: str,
                curve_id: str = CURVE_UST_PAR) -> dict[str, float]:
    """{tenor: yield} for one curve date, only if it was available by `as_of`.

    Returns the tenors that existed on that date. A tenor missing from the
    result was not quoted -- the 30-year between 2002-02-19 and 2006-02-08 is
    absent, not zero, and never interpolated from its neighbours here.
    """
    rows = conn.execute(
        """SELECT tenor, value FROM pit_curve_obs
            WHERE curve_id = ? AND obs_date = ? AND available_date <= ?
              AND value IS NOT NULL""",
        (curve_id, obs_date, as_of)).fetchall()
    order = {t: i for i, t in enumerate(TENOR_ORDER)}
    return {r["tenor"]: float(r["value"])
            for r in sorted(rows, key=lambda r: order.get(r["tenor"], 99))}


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """What is actually in the three tables, measured, for a status line."""
    macro = conn.execute(
        """SELECT COUNT(*) AS rows, COUNT(DISTINCT series_id) AS series,
                  COUNT(DISTINCT vintage_date) AS vintages,
                  SUM(CASE WHEN revision_class = ? THEN 1 ELSE 0 END) AS gaps,
                  MIN(obs_date) AS first_obs, MAX(obs_date) AS last_obs
             FROM pit_macro_obs""", (NO_VINTAGE,)).fetchone()
    curve = conn.execute(
        """SELECT COUNT(*) AS rows, COUNT(DISTINCT obs_date) AS dates,
                  COUNT(DISTINCT tenor) AS tenors,
                  MIN(obs_date) AS first_obs, MAX(obs_date) AS last_obs
             FROM pit_curve_obs""").fetchone()
    manifest = conn.execute(
        "SELECT COUNT(*) AS rows FROM pit_macro_manifest").fetchone()
    return {"macro": dict(macro), "curve": dict(curve),
            "manifest_rows": int(manifest["rows"])}


# --------------------------------------------------------------------------
# The whole ingest
# --------------------------------------------------------------------------

def ingest_all(conn: sqlite3.Connection,
               db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
               grid_start: str = "2012-01-01",
               do_alfred: bool = True, do_fred: bool = True,
               do_curve: bool = True,
               only: Optional[Iterable[str]] = None,
               progress: Optional[Callable[[str], None]] = None
               ) -> dict[str, Any]:
    """Run every phase, disk-guarded, and return what each one measured."""
    say = progress or (lambda _m: None)
    say(f"connection: {prepare_connection(conn)}")
    say(f"disk: {require_disk(db_path)}")
    grid = replay_grid(conn, grid_start)
    say(f"grid: {len(grid)} month-end sessions {grid[0]}..{grid[-1]}")
    keep = set(only) if only else None
    out: dict[str, Any] = {"grid": len(grid), "alfred": {}, "fred": {},
                           "curve": {}}

    if do_alfred:
        for series_id, spec in ALFRED_SERIES.items():
            if keep and series_id not in keep:
                continue
            say(f"alfred {series_id} ({spec.frequency}, {spec.window_years}y window)")
            out["alfred"][series_id] = ingest_alfred_series(
                conn, spec, grid, db_path, progress=progress)
            report = out["alfred"][series_id]
            say(f"  {series_id}: fetched {report['fetched']}, rows "
                f"{report['rows']}, gaps {report['gap_rows']}, "
                f"base {report['base_tags'] or 'not determinable'}")

    if do_fred:
        resolve = availability_resolver(conn)
        for series_id, fspec in FRED_SERIES.items():
            if keep and series_id not in keep:
                continue
            out["fred"][series_id] = ingest_fred_series(
                conn, fspec, resolve, FRED_SLICE_START, db_path)
            report = out["fred"][series_id]
            say(f"fred {series_id}: {report['rows']} rows, "
                f"{report['first_obs']}..{report['last_obs']}")

    if do_curve:
        say("curve: US Treasury daily par yields")
        out["curve"] = ingest_curve(conn, CURVE_FIRST_YEAR, progress=progress)
        say(f"  curve rows {out['curve']['rows']}, not covered "
            f"{out['curve']['years_not_covered']}")

    say(f"disk after: {require_disk(db_path)}")
    out["coverage"] = coverage(conn)
    return out


def rebuild_manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    """Re-derive every manifest row from the stored observations. No fetching.

    The manifest is derived state, so it must be reproducible from the rows
    alone -- and it has to be, because a long ingest runs with whatever module
    text was loaded when its process started. Re-deriving here is how the
    manifest catches up with the module without re-fetching a thousand
    vintages, and it is also the honest check: if a field cannot be recomputed
    from `pit_macro_obs`, it was never a measurement.

    Every number below is measured from the store:
      vintage_coverage_start  earliest non-gap vintage actually held
      series_first_obs        earliest observation actually held
      publication_lag_days    for a vintage series, the median days to first
                              appearance (an UPPER BOUND at grid resolution);
                              for a never-revised series, the median calendar
                              gap between obs_date and available_date
    """
    written: dict[str, Any] = {}
    for series_id, spec in ALFRED_SERIES.items():
        row = conn.execute(
            """SELECT MIN(vintage_date) AS v, MIN(obs_date) AS o,
                      COUNT(*) AS n
                 FROM pit_macro_obs
                WHERE series_id = ? AND revision_class != ?""",
            (series_id, NO_VINTAGE)).fetchone()
        if not row or not row["n"]:
            continue
        lag = derive_publication_lag(conn, series_id)
        floor = VINTAGE_FLOORS.get(series_id)
        upsert_manifest(
            conn, series_id, spec.label, spec.frequency, REVISED,
            row["v"], row["o"], lag, spec.feature_role,
            f"{spec.units_note} Cadence: {spec.cadence}; window "
            f"{spec.window_years}y per vintage; archive floor "
            f"{floor or 'none measured'}. publication_lag_days is measured at "
            f"MONTH-END GRID RESOLUTION and is therefore an UPPER BOUND on the "
            f"release lag, not the release lag -- use refine_first_vintage to "
            f"pin an exact release date.")
        written[series_id] = {"rows": int(row["n"]), "lag_days": lag,
                              "coverage_start": row["v"]}

    for series_id, fspec in FRED_SERIES.items():
        row = conn.execute(
            """SELECT MIN(obs_date) AS o, COUNT(*) AS n,
                      MAX(obs_date) AS hi
                 FROM pit_macro_obs
                WHERE series_id = ? AND revision_class = ?""",
            (series_id, NOT_REVISED)).fetchone()
        if not row or not row["n"]:
            continue
        lags = [(_dt.date.fromisoformat(str(r["available_date"]))
                 - _dt.date.fromisoformat(str(r["obs_date"]))).days
                for r in conn.execute(
                    """SELECT obs_date, available_date FROM pit_macro_obs
                        WHERE series_id = ? AND revision_class = ?""",
                    (series_id, NOT_REVISED))]
        lag = int(round(statistics.median(lags))) if lags else None
        retrieved = conn.execute(
            """SELECT source FROM pit_macro_obs
                WHERE series_id = ? AND revision_class = ? LIMIT 1""",
            (series_id, NOT_REVISED)).fetchone()["source"]
        retrieved = retrieved.partition("retrieved=")[2] or "unknown"
        upsert_manifest(
            conn, series_id, fspec.label, fspec.frequency, NOT_REVISED,
            None, row["o"], lag, fspec.feature_role,
            f"{fspec.units_note} Availability: +{fspec.lag_sessions} trading "
            f"session(s), a measured median of {lag} calendar days. "
            f"{fspec.lag_reason} Retrieved {retrieved} as a single current "
            f"pull; values are never revised but the OBSERVATION SET is not "
            f"stable across vintages, so a row absent here may have been "
            f"present in an older vintage of the same series.")
        written[series_id] = {"rows": int(row["n"]), "lag_days": lag,
                              "last_obs": row["hi"]}
    return written


def first_print_demo(conn: sqlite3.Connection, series_id: str = "CPIAUCSL",
                     obs_date: str = "2015-06-01",
                     refine: bool = False,
                     db_path: str = pit_store.DEFAULT_PIT_DB_PATH
                     ) -> dict[str, Any]:
    """Read one observation at two as-of dates and show that they differ.

    This is the evidence that the vintage machinery is real rather than a
    schema with a hopeful docstring. If the first print and today's value are
    the same number, either the ingest stored only one vintage or the selector
    is ignoring `vintage_date` -- and both failures are invisible in a row
    count.

    With `refine`, the exact release vintage is found by bisection first, so
    the "first print" is the number that was actually on the wire on release
    day rather than the earliest one the month-end grid happens to hold.
    """
    grid = replay_grid(conn, "2012-01-01")
    spec = ALFRED_SERIES.get(series_id)
    refined: Optional[dict[str, Any]] = None
    if refine and spec is not None:
        # The store already brackets the release: the earliest grid vintage
        # that carries the observation is an upper bound, and the grid date
        # before it is a lower bound, because that vintage did not carry it.
        # Bisecting that bracket costs ~5 calls instead of ~30.
        held = conn.execute(
            """SELECT MIN(vintage_date) AS v FROM pit_macro_obs
                WHERE series_id = ? AND obs_date = ? AND value IS NOT NULL
                  AND revision_class != ?""",
            (series_id, obs_date, NO_VINTAGE)).fetchone()
        bracket_end = str(held["v"]) if held and held["v"] else grid[-1]
        earlier = [d for d in grid if d < bracket_end]
        bracket_start = max(earlier[-1], obs_date) if earlier else obs_date
        refined = refine_first_vintage(
            conn, spec, obs_date, bracket_start, bracket_end, True, db_path)

    row = conn.execute(
        """SELECT MIN(vintage_date) AS first_vintage
             FROM pit_macro_obs
            WHERE series_id = ? AND obs_date = ? AND revision_class != ?
              AND value IS NOT NULL""",
        (series_id, obs_date, NO_VINTAGE)).fetchone()
    if not row or row["first_vintage"] is None:
        return {"status": "no vintages stored for "
                          f"{series_id} {obs_date}", "refined": refined}

    first_vintage = str(row["first_vintage"])
    first = value_as_of(conn, series_id, obs_date, first_vintage)
    current = value_as_of(conn, series_id, obs_date, grid[-1])
    vintages = conn.execute(
        """SELECT COUNT(DISTINCT vintage_date) FROM pit_macro_obs
            WHERE series_id = ? AND obs_date = ? AND revision_class != ?""",
        (series_id, obs_date, NO_VINTAGE)).fetchone()[0]
    return {
        "status": "ok", "series_id": series_id, "obs_date": obs_date,
        "first_print": first["value"] if first else None,
        "first_print_vintage": first["vintage_date"] if first else None,
        "current": current["value"] if current else None,
        "current_vintage": current["vintage_date"] if current else None,
        "as_of_current": grid[-1],
        "distinct_vintages": int(vintages),
        "refined": refined,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=pit_store.DEFAULT_PIT_DB_PATH)
    parser.add_argument("--grid-start", default="2012-01-01")
    parser.add_argument("--only", nargs="*", default=None,
                        help="restrict to these series ids")
    parser.add_argument("--skip-alfred", action="store_true")
    parser.add_argument("--skip-fred", action="store_true")
    parser.add_argument("--skip-curve", action="store_true")
    parser.add_argument("--status", action="store_true",
                        help="report coverage and exit without fetching")
    parser.add_argument("--demo", nargs="?", const="CPIAUCSL:2015-06-01",
                        default=None, metavar="SERIES:OBS_DATE",
                        help="show one observation's first print beside its "
                             "current value and exit")
    parser.add_argument("--refine", action="store_true",
                        help="with --demo, bisect ALFRED for the exact "
                             "release vintage first")
    parser.add_argument("--manifest-only", action="store_true",
                        help="re-derive pit_macro_manifest from the stored "
                             "rows and exit, fetching nothing")
    args = parser.parse_args(argv)

    conn = pit_store.init_db(args.db)
    prepare_connection(conn)
    try:
        if args.status:
            for key, value in coverage(conn).items():
                print(f"{key}: {value}")
            return 0
        if args.manifest_only:
            for series_id, detail in rebuild_manifest(conn).items():
                print(f"{series_id}: {detail}")
            return 0
        if args.demo:
            series_id, _, obs_date = args.demo.partition(":")
            result = first_print_demo(conn, series_id,
                                      obs_date or "2015-06-01",
                                      args.refine, args.db)
            for key, value in result.items():
                print(f"{key}: {value}")
            return 0 if result.get("status") == "ok" else 1
        ingest_all(conn, args.db, args.grid_start,
                   do_alfred=not args.skip_alfred,
                   do_fred=not args.skip_fred,
                   do_curve=not args.skip_curve,
                   only=args.only,
                   progress=lambda m: print(m, flush=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
