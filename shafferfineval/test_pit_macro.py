"""Tests for pit_macro: vintage validation, availability, and the two selectors.

A plain script, run with `python test_pit_macro.py`. No pytest, no network.

The suite is in two halves. The first builds a throwaway store and checks the
machinery against payloads shaped exactly like the ones measured live on
2026-09-21 -- including the one that matters most, ALFRED's HTTP 200 answer to
a vintage it will not serve, which carries today's revised values under a
header naming today's date. A parser that trusts the status code accepts it.

The second half reads the real store if it has rows and runs THE
DEMONSTRATION: one CPI observation read at two as-of dates, printing the first
print and the current value side by side. That single pair of numbers is the
evidence the vintage machinery is real rather than a schema with a hopeful
docstring, so it is a test rather than a note, and it FAILS if the two numbers
are equal.
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import sys
import tempfile

import pit_macro
import pit_store

_FAILURES: list[str] = []
_CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _CHECKS
    _CHECKS += 1
    if condition:
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
        return True
    print(f"  FAIL  {name}" + (f"  [{detail}]" if detail else ""))
    _FAILURES.append(name)
    return False


def section(title: str) -> None:
    print(f"\n--- {title}")


# --------------------------------------------------------------------------
# Fixtures shaped like the measured payloads
# --------------------------------------------------------------------------

#: A real ALFRED answer: CPIAUCSL as it stood on 2015-06-30.
ALFRED_GOOD = (
    "observation_date,CPIAUCSL_20150630\n"
    "2014-01-01,235.128\n"
    "2014-02-01,235.356\n"
    "2014-03-01,235.790\n"
    "2015-05-01,237.805\n"
)

#: THE SILENT SUBSTITUTION. Measured: vintage_date=2030-06-30 returns HTTP 200
#: with the CURRENT series under a header naming TODAY. Same status, same
#: shape, same column count -- different numbers for the same observations.
ALFRED_SUBSTITUTED = (
    "observation_date,CPIAUCSL_20260921\n"
    "2014-01-01,235.288\n"
    "2014-02-01,235.547\n"
    "2014-03-01,236.028\n"
)

FRED_GOOD = (
    "observation_date,DGS10\n"
    "1990-01-02,7.94\n"
    "2015-06-30,2.35\n"
    "2015-07-01,2.42\n"
    "2015-07-03,.\n"
)

#: 2003 and 2005: the "30 Yr" column is ABSENT, not blank. The last numeric
#: field on each row is the 20-year. A positional reader takes it for a
#: 30-year yield and nothing in the output looks wrong.
TREASURY_2003 = (
    'Date,"1 Mo","3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr"\n'
    "12/31/2003,0.90,0.95,1.02,1.26,1.84,2.37,3.25,3.77,4.27,5.10\n"
    "01/02/2003,1.18,1.22,1.25,1.42,1.80,2.22,3.05,3.62,4.07,5.05\n"
)

#: 2006: "30 Yr" is back in the header but BLANK until 2006-02-09.
TREASURY_2006 = (
    'Date,"1 Mo","3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
    "02/09/2006,4.50,4.53,4.68,4.70,4.66,4.63,4.58,4.58,4.57,4.73,4.66\n"
    "01/03/2006,4.05,4.16,4.40,4.38,4.34,4.30,4.30,4.32,4.37,4.62,\n"
)


def temp_store() -> tuple[sqlite3.Connection, str]:
    handle, path = tempfile.mkstemp(suffix=".db", prefix="pit_macro_test_")
    os.close(handle)
    os.unlink(path)
    return pit_store.init_db(path), path


def drop_store(conn: sqlite3.Connection, path: str) -> None:
    conn.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


# --------------------------------------------------------------------------
# 1. Source validation
# --------------------------------------------------------------------------

def test_alfred_validation() -> None:
    section("ALFRED response validation")

    rows = pit_macro.parse_alfred_csv(ALFRED_GOOD, "CPIAUCSL", "2015-06-30")
    check("a matching header parses", len(rows) == 4, f"{len(rows)} rows")
    check("first observation is the vintage's value",
          rows[0] == ("2014-01-01", 235.128), str(rows[0]))

    raised = ""
    try:
        pit_macro.parse_alfred_csv(ALFRED_SUBSTITUTED, "CPIAUCSL", "2030-06-30")
    except pit_macro.AlfredHeaderMismatch as exc:
        raised = str(exc)
    check("the today-substituted payload is REJECTED", bool(raised),
          raised[:70] or "no exception raised -- a leak")
    check("the rejection names both vintages",
          "CPIAUCSL_20260921" in raised and "CPIAUCSL_20300630" in raised)

    # The canary: the substituted payload is well-formed, so anything that
    # checks only structure accepts it and back-dates today's numbers.
    substituted_values = [line.split(",")[1] for line in
                          ALFRED_SUBSTITUTED.strip().splitlines()[1:]]
    good_values = [line.split(",")[1] for line in
                   ALFRED_GOOD.strip().splitlines()[1:4]]
    check("the substituted values genuinely differ from the vintage's",
          substituted_values != good_values,
          f"{good_values[0]} then vs {substituted_values[0]} now")

    raised = ""
    try:
        pit_macro.parse_alfred_csv("", "CPIAUCSL", "2015-06-30")
    except pit_macro.SourceRejected as exc:
        raised = str(exc)
    check("an empty body is rejected, not read as zero observations",
          bool(raised))

    check("header field is built from the request",
          pit_macro.alfred_header_field("CPIAUCSL", "2015-06-30")
          == "CPIAUCSL_20150630")


def test_fred_parsing() -> None:
    section("FRED current-series parsing")
    rows = pit_macro.parse_fred_csv(FRED_GOOD, "DGS10")
    check("all observations parse", len(rows) == 4, f"{len(rows)} rows")
    check("a '.' becomes None, never 0.0", rows[3][1] is None, str(rows[3]))
    sliced = pit_macro.parse_fred_csv(FRED_GOOD, "DGS10", start="2015-01-01")
    check("the local slice drops earlier observations", len(sliced) == 3)

    raised = ""
    try:
        pit_macro.parse_fred_csv("observation_date,WRONGID\n2015-01-01,1\n",
                                 "DGS10")
    except pit_macro.SourceRejected as exc:
        raised = str(exc)
    check("a column for another series is rejected", bool(raised))


def test_treasury_parsing() -> None:
    section("Treasury curve: read by NAME, gated by the data")
    y2003 = pit_macro.parse_treasury_csv(TREASURY_2003, 2003)
    tenors = set(y2003.tenors_present)
    check("2003 offers ten tenors", len(y2003.tenors_present) == 10,
          ",".join(y2003.tenors_present))
    check("2003 has NO 30-year column at all", "30Y" not in tenors)
    values = {(d, t): v for d, t, v in y2003.rows}
    check("the 20-year is 5.10 on 2003-12-31",
          values[("2003-12-31", "20Y")] == 5.10)
    check("POSITIONAL-READER CANARY: nothing landed in 30Y",
          not any(t == "30Y" for _, t, _ in y2003.rows),
          "a positional reader would have put 5.10 here")
    check("dates are normalised out of M/D/YYYY",
          {d for d, _, _ in y2003.rows} == {"2003-12-31", "2003-01-02"})

    y2006 = pit_macro.parse_treasury_csv(TREASURY_2006, 2006)
    values = {(d, t): v for d, t, v in y2006.rows}
    check("2006 has a 30-year column", "30Y" in set(y2006.tenors_present))
    check("2006-02-09 carries a 30-year value",
          values.get(("2006-02-09", "30Y")) == 4.66)
    check("2006-01-03's blank 30-year produces NO row",
          ("2006-01-03", "30Y") not in values)
    check("and the blank is counted rather than forgotten",
          y2006.blank_cells.get("30Y") == 1, str(y2006.blank_cells))
    check("no column went unmapped", y2006.unmapped_columns == [],
          str(y2006.unmapped_columns))

    raised = ""
    try:
        pit_macro.parse_treasury_csv("", 1989)
    except pit_macro.YearNotCovered as exc:
        raised = str(exc)
    check("a zero-length body is 'year not covered', not an empty year",
          bool(raised), raised[:60])

    check("every measured header column maps to a tenor",
          all(name in pit_macro.TREASURY_TENORS for name in
              ("1 Mo", "1.5 Month", "2 Mo", "3 Mo", "4 Mo", "6 Mo", "1 Yr",
               "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr")))


# --------------------------------------------------------------------------
# 2. Derivations
# --------------------------------------------------------------------------

def test_base_tag() -> None:
    section("Base tag is measured from the observations, never asserted")
    rebased = [(f"2017-{m:02d}-01", 100.0) for m in range(1, 13)]
    rebased += [(f"2018-{m:02d}-01", 102.0) for m in range(1, 13)]
    check("a year averaging 100 is found",
          pit_macro.derive_base_tag(rebased, "monthly") == "2017=100",
          str(pit_macro.derive_base_tag(rebased, "monthly")))

    far_base = [(f"2015-{m:02d}-01", 237.0 + m) for m in range(1, 13)]
    check("a base outside the window returns None, not a guess",
          pit_macro.derive_base_tag(far_base, "monthly") is None)
    check("a daily series has no base tag",
          pit_macro.derive_base_tag(rebased, "daily") is None)


def test_disk_guard() -> None:
    section("Disk guard")
    detail = pit_macro.require_disk(pit_store.DEFAULT_PIT_DB_PATH, 0.001)
    check("free space is reported", "free=" in detail, detail)
    raised = ""
    try:
        pit_macro.require_disk(pit_store.DEFAULT_PIT_DB_PATH, 10_000.0)
    except pit_macro.DiskTooFull as exc:
        raised = str(exc)
    check("an impossible floor refuses to fetch", bool(raised))
    check("and the refusal says not to lower the floor",
          "do not lower the floor" in raised)


# --------------------------------------------------------------------------
# 3. Availability
# --------------------------------------------------------------------------

def test_availability(conn: sqlite3.Connection) -> None:
    section("Availability is counted in trading sessions")
    pit_store.insert_calendar(conn, "XNYS", [
        "2015-06-29", "2015-06-30", "2015-07-01", "2015-07-02",
        "2015-07-06", "2015-07-07"], "test")
    resolve = pit_macro.availability_resolver(conn)
    check("+1 session moves one session forward",
          resolve("2015-06-30", 1) == "2015-07-01", resolve("2015-06-30", 1))
    check("+1 session jumps the 4 July holiday AND the weekend",
          resolve("2015-07-02", 1) == "2015-07-06", resolve("2015-07-02", 1))
    check("+2 sessions is two sessions, not two days",
          resolve("2015-07-01", 2) == "2015-07-06", resolve("2015-07-01", 2))
    check("a non-session observation resolves to the next session",
          resolve("2015-07-04", 1) == "2015-07-06", resolve("2015-07-04", 1))
    check("+0 on a session is that session",
          resolve("2015-06-30", 0) == "2015-06-30")
    check("+0 on a NON-session is the next session, never a day in the past",
          resolve("2015-07-04", 0) == "2015-07-06", resolve("2015-07-04", 0))
    check("+0 on a weekend resolves forward",
          resolve("2015-07-05", 0) == "2015-07-06", resolve("2015-07-05", 0))
    check("past the calendar's end it falls back to weekdays",
          resolve("2015-07-07", 1) == "2015-07-08", resolve("2015-07-07", 1))


# --------------------------------------------------------------------------
# 4. Store behaviour on a throwaway database
# --------------------------------------------------------------------------

def _row(series: str, obs: str, vintage: str, value, klass=pit_macro.REVISED,
         base=None):
    return (series, obs, vintage, value, vintage, klass, base, "test")


def test_store_roundtrip(conn: sqlite3.Connection) -> None:
    section("Vintage selection on a throwaway store")
    rows = [
        # A first print, then the same observation revised twice.
        _row("XCPI", "2015-06-01", "2015-07-31", 237.805),
        _row("XCPI", "2015-06-01", "2016-02-29", 237.900),
        _row("XCPI", "2015-06-01", "2026-09-18", 238.140),
        _row("XCPI", "2015-05-01", "2015-07-31", 237.000),
        _row("XCPI", "2015-05-01", "2026-09-18", 237.300),
    ]
    with pit_store.transaction(conn):
        written = pit_store.insert_macro_obs(conn, rows)
    check("five vintage rows written", written == 5, str(written))
    with pit_store.transaction(conn):
        again = pit_store.insert_macro_obs(conn, rows)
    check("re-inserting the same rows writes nothing (UNIQUE idempotency)",
          again == 0, str(again))

    first = pit_store.macro_value_as_of(conn, "XCPI", "2015-06-01", "2015-08-31")
    latest = pit_store.macro_value_as_of(conn, "XCPI", "2015-06-01", "2026-09-21")
    check("an August 2015 as-of reads the FIRST PRINT",
          first["value"] == 237.805, str(first["value"]))
    check("a 2026 as-of reads the revised value",
          latest["value"] == 238.140, str(latest["value"]))
    check("the two differ -- which is the whole point",
          first["value"] != latest["value"])
    check("an as-of before the first vintage sees nothing",
          pit_store.macro_value_as_of(conn, "XCPI", "2015-06-01", "2015-07-01")
          is None)

    # One vintage, never a stitch: a synthetic rebase between two vintages.
    rebase = []
    for month in range(1, 13):
        rebase.append(_row("XIND", f"2016-{month:02d}-01", "2017-06-30",
                           100.0 + month, base="2012=100"))
    for month in range(1, 13):
        rebase.append(_row("XIND", f"2016-{month:02d}-01", "2018-06-29",
                           (100.0 + month) * 0.9, base="2017=100"))
    with pit_store.transaction(conn):
        pit_store.insert_macro_obs(conn, rebase)

    early = pit_store.macro_series_as_of(conn, "XIND", "2017-12-29")
    late = pit_store.macro_series_as_of(conn, "XIND", "2019-01-31")
    check("a series read as-of 2017 comes from the 2017 vintage",
          {r["vintage_date"] for r in early} == {"2017-06-30"},
          str({r["vintage_date"] for r in early}))
    check("a series read as-of 2019 comes from the 2018 vintage",
          {r["vintage_date"] for r in late} == {"2018-06-29"})
    check("every returned row shares ONE vintage",
          len({r["vintage_date"] for r in late}) == 1)

    # The shock a stitched read would manufacture.
    stitched = ((rebase[12][3] / rebase[11][3]) - 1.0) * 100.0
    honest = ((late[1]["value"] / late[0]["value"]) - 1.0) * 100.0
    check("a stitched month-over-month invents a large move",
          abs(stitched) > 5.0, f"{stitched:+.2f}%")
    check("the single-vintage month-over-month is the real one",
          abs(honest) < 2.0, f"{honest:+.2f}%")

    found = pit_macro.detect_rebases(conn, "XIND")
    check("the rebase is detected as a constant ratio", len(found) == 1,
          str(found))
    if found:
        check("and its ratio is the 0.9 level shift",
              abs(found[0]["ratio"] - 0.9) < 1e-6, str(found[0]["ratio"]))


def test_gap_rows(conn: sqlite3.Connection) -> None:
    section("An absence is recorded, never papered over")
    source = pit_macro._alfred_source("2026-09-21", pit_macro.MISSING_PRE_ARCHIVE)
    with pit_store.transaction(conn):
        pit_store.insert_macro_obs(conn, [
            ("XFX", "2015-06-30", "2015-06-30", None, "2015-06-30",
             pit_macro.NO_VINTAGE, None, source)])
    raw = pit_store.macro_series_as_of(conn, "XFX", "2015-07-31")
    safe = pit_macro.series_as_of(conn, "XFX", "2015-07-31")
    check("the raw selector hands back the marker as a NULL row",
          len(raw) == 1 and raw[0]["value"] is None, str(len(raw)))
    check("series_as_of drops it so no transform sees a phantom point",
          safe == [], str(safe))
    check("value_as_of drops it too",
          pit_macro.value_as_of(conn, "XFX", "2015-06-30", "2015-07-31") is None)
    listed = pit_macro.gaps(conn, "XFX")
    check("the gap is queryable with its reason", len(listed) == 1
          and pit_macro.MISSING_PRE_ARCHIVE in listed[0]["source"],
          listed[0]["source"] if listed else "none")


def test_curve_store(conn: sqlite3.Connection) -> None:
    section("Curve storage and availability gating")
    rows = [
        ("ust_par", "2006-01-03", "10Y", 4.37, "2006-01-04", "test"),
        ("ust_par", "2006-01-03", "20Y", 4.62, "2006-01-04", "test"),
        ("ust_par", "2006-02-09", "30Y", 4.66, "2006-02-10", "test"),
        ("ust_par", "2006-02-09", "10Y", 4.57, "2006-02-10", "test"),
    ]
    with pit_store.transaction(conn):
        written = pit_macro.insert_curve_obs(conn, rows)
    check("four curve rows written", written == 4, str(written))
    with pit_store.transaction(conn):
        again = pit_macro.insert_curve_obs(conn, rows)
    check("re-inserting writes nothing (UNIQUE idempotency)", again == 0)

    early = pit_macro.curve_as_of(conn, "2006-01-03", "2006-01-31")
    check("the 30-year is ABSENT before it resumed, not zero",
          "30Y" not in early, str(sorted(early)))
    check("the tenors present are ordered shortest first",
          list(early) == ["10Y", "20Y"], str(list(early)))
    later = pit_macro.curve_as_of(conn, "2006-02-09", "2006-03-31")
    check("the 30-year appears once it resumed", later.get("30Y") == 4.66)
    check("a curve is invisible before its availability date",
          pit_macro.curve_as_of(conn, "2006-02-09", "2006-02-09") == {})


def test_checkpoint(conn: sqlite3.Connection, path: str) -> None:
    section("WAL checkpoint reports what actually happened")
    note = pit_macro.checkpoint(conn, path)
    check("the checkpoint returns a readable status", note.startswith("checkpoint"),
          note)
    check("and it is not silently assumed to have worked",
          any(word in note for word in
              ("ok", "busy", "BUSY", "not applicable")), note)
    check("a successful checkpoint says how many pages moved",
          "pages" in note or "not applicable" in note, note)
    check("busy timeout is set for a store other processes are writing",
          "busy_timeout=" in pit_macro.prepare_connection(conn))


def test_publication_lag(conn: sqlite3.Connection) -> None:
    section("Publication lag derived from first-appearance, not declared")
    rows = []
    # Each month's observation first appears in the vintage ~15 days later.
    for month in range(1, 13):
        obs = _dt.date(2016, month, 1)
        for offset in (0, 1, 2):
            vintage = obs + _dt.timedelta(days=15 + 30 * offset)
            rows.append(_row("XLAG", obs.isoformat(), vintage.isoformat(),
                             100.0 + month + offset))
    with pit_store.transaction(conn):
        pit_store.insert_macro_obs(conn, rows)
    lag = pit_macro.derive_publication_lag(conn, "XLAG")
    check("the measured lag is ~15 days", lag is not None and 13 <= lag <= 17,
          str(lag))


# --------------------------------------------------------------------------
# 5. The live store: THE DEMONSTRATION
# --------------------------------------------------------------------------

def live_store() -> tuple[sqlite3.Connection, int]:
    path = pit_store.DEFAULT_PIT_DB_PATH
    if not os.path.exists(path):
        return None, 0
    conn = pit_store.connect(path)
    count = conn.execute("SELECT COUNT(*) FROM pit_macro_obs").fetchone()[0]
    return conn, int(count)


def test_live(conn: sqlite3.Connection) -> None:
    section("LIVE STORE: the vintage machinery on real CPI")

    grid = pit_macro.replay_grid(conn, "2012-01-01")
    check("the replay grid is the 177 month-end sessions from 2012",
          len(grid) == 177 and grid[0] == "2012-01-31", f"{len(grid)} dates")

    # Pick the first CPI observation whose first print and current print
    # disagree. There is no need to guess which month was revised -- ask.
    demo = conn.execute(
        """SELECT obs_date,
                  MIN(vintage_date)  AS first_vintage,
                  MAX(vintage_date)  AS last_vintage
             FROM pit_macro_obs
            WHERE series_id = 'CPIAUCSL' AND revision_class != ?
              AND obs_date BETWEEN '2015-01-01' AND '2015-12-01'
            GROUP BY obs_date
            HAVING COUNT(DISTINCT vintage_date) > 5
            ORDER BY obs_date""", (pit_macro.NO_VINTAGE,)).fetchall()
    if not check("CPI observations from 2015 carry several vintages",
                 bool(demo), f"{len(demo)} observations"):
        return

    shown = 0
    for candidate in demo:
        obs = str(candidate["obs_date"])
        result = pit_macro.first_print_demo(conn, "CPIAUCSL", obs)
        if result["status"] != "ok" or result["first_print"] is None:
            continue
        if result["first_print"] == result["current"]:
            continue
        drift = result["current"] - result["first_print"]
        print(f"\n    CPIAUCSL observation {obs}"
              f"   ({result['distinct_vintages']} vintages stored)")
        print(f"      FIRST PRINT   {result['first_print']:>10.3f}"
              f"   vintage {result['first_print_vintage']}")
        print(f"      TODAY         {result['current']:>10.3f}"
              f"   vintage {result['current_vintage']}"
              f"   (read as of {result['as_of_current']})")
        print(f"      revision      {drift:>+10.3f} index points"
              f"  ({100.0 * drift / result['first_print']:+.3f}%)\n")
        check("THE DEMONSTRATION: the first print differs from today's value",
              result["first_print"] != result["current"],
              f"{result['first_print']} -> {result['current']}")
        check("the first print comes from an early vintage",
              str(result["first_print_vintage"]) < "2017-01-01",
              str(result["first_print_vintage"]))
        check("today's value comes from a later vintage",
              str(result["current_vintage"])
              > str(result["first_print_vintage"]))
        check("the observation carries many vintages, not two",
              result["distinct_vintages"] > 50,
              str(result["distinct_vintages"]))
        shown += 1
        break
    check("a revised CPI observation was found and shown", shown == 1)

    # One vintage per read.
    as_of = "2016-06-30"
    series = pit_macro.series_as_of(conn, "CPIAUCSL", as_of, "2015-01-01")
    vintages = {str(r["vintage_date"]) for r in series}
    check("macro_series_as_of returns points from ONE vintage",
          len(vintages) == 1, f"{len(series)} points, vintage {vintages}")
    if series:
        print(f"      first 4 of {len(series)} points at vintage "
              f"{sorted(vintages)[0]}:")
        for row in series[:4]:
            print(f"        {row['obs_date']}  {row['value']}")
    check("no returned observation post-dates the as-of",
          all(str(r["obs_date"]) <= as_of for r in series))
    check("no returned row was available after the as-of",
          all(str(r["available_date"]) <= as_of for r in series))

    # The same read a year later is a different vintage of the same window.
    later = pit_macro.series_as_of(conn, "CPIAUCSL", "2017-06-30", "2015-01-01")
    later_vintages = {str(r["vintage_date"]) for r in later}
    check("a later as-of reads a later vintage of the same observations",
          bool(later_vintages) and later_vintages != vintages,
          f"{sorted(vintages)} then {sorted(later_vintages)}")
    overlap = {str(r["obs_date"]): r["value"] for r in series}
    changed = sum(1 for r in later
                  if str(r["obs_date"]) in overlap
                  and r["value"] != overlap[str(r["obs_date"])])
    check("and some of those observations were restated between the two",
          changed > 0, f"{changed} of {len(overlap)} restated")


def test_live_rebases(conn: sqlite3.Connection) -> None:
    section("LIVE STORE: rebasings inside the replay window")

    bases = [(str(r["base_tag"]), str(r["lo"]), str(r["hi"])) for r in conn.execute(
        """SELECT base_tag, MIN(vintage_date) AS lo, MAX(vintage_date) AS hi
             FROM pit_macro_obs
            WHERE series_id = 'PCEPILFE' AND revision_class = ?
            GROUP BY base_tag ORDER BY lo""", (pit_macro.REVISED,))]
    for tag, lo, hi in bases:
        print(f"      PCEPILFE base {tag:<10} vintages {lo} .. {hi}")
    check("PCEPILFE carries several different bases across its vintages",
          len({t for t, _, _ in bases if t != "None"}) >= 3,
          str([t for t, _, _ in bases]))

    found = pit_macro.detect_rebases(conn, "PCEPILFE")
    for f in found:
        print(f"      rebase {f['from_vintage']} -> {f['to_vintage']}  "
              f"level {f['shift_pct']:+.2f}%  scatter/shift "
              f"{f['spread_over_shift']:.3f}  over {f['overlap']} months")
    check("the rebases are detected as one-factor level shifts",
          len(found) >= 3, f"{len(found)} found")
    check("every detected rebase is a large level move",
          all(abs(f["shift_pct"]) > 1.0 for f in found))

    # The consequence, on real numbers: what a stitched read would invent.
    if found:
        edge = found[-1]
        before = {str(r["obs_date"]): r["value"] for r in conn.execute(
            """SELECT obs_date, value FROM pit_macro_obs
                WHERE series_id = 'PCEPILFE' AND vintage_date = ?""",
            (edge["from_vintage"],))}
        after = {str(r["obs_date"]): r["value"] for r in conn.execute(
            """SELECT obs_date, value FROM pit_macro_obs
                WHERE series_id = 'PCEPILFE' AND vintage_date = ?""",
            (edge["to_vintage"],))}
        shared = sorted(set(before) & set(after))
        if len(shared) > 2:
            month = shared[-1]
            stitched = ((after[month] / before[shared[-2]]) - 1.0) * 100.0
            honest = ((after[month] / after[shared[-2]]) - 1.0) * 100.0
            print(f"      month-over-month at {month}: "
                  f"stitched across vintages {stitched:+.2f}%, "
                  f"inside one vintage {honest:+.2f}%")
            check("a stitched month-over-month invents a shock on real data",
                  abs(stitched - honest) > 1.0,
                  f"{stitched:+.2f}% vs {honest:+.2f}%")

    check("CPIAUCSL shows no rebase (its 1982-1984 base never moved)",
          pit_macro.detect_rebases(conn, "CPIAUCSL") == [])


def test_live_gaps(conn: sqlite3.Connection) -> None:
    section("LIVE STORE: recorded absences")
    for row in pit_macro.gaps(conn):
        print(f"      {row['series_id']}: {row['n']} NO_VINTAGE rows, "
              f"{row['first']} .. {row['last']}")
    fx = pit_macro.gaps(conn, "DTWEXBGS")
    check("DTWEXBGS records an absence for every pre-2019 grid date",
          len(fx) > 50, f"{len(fx)} rows")
    check("every one is before the measured archive floor",
          all(str(r["vintage_date"]) < pit_macro.VINTAGE_FLOORS["DTWEXBGS"]
              for r in fx),
          pit_macro.VINTAGE_FLOORS["DTWEXBGS"])
    check("and none of them silently carries the current series",
          pit_macro.series_as_of(conn, "DTWEXBGS", "2015-06-30") == [])
    check("while a post-floor as-of does return values",
          len(pit_macro.series_as_of(conn, "DTWEXBGS", "2020-06-30")) > 0)


def test_live_integrity(conn: sqlite3.Connection) -> None:
    section("LIVE STORE: integrity of what was written")
    bad = conn.execute(
        "SELECT COUNT(*) FROM pit_macro_obs WHERE available_date < obs_date"
    ).fetchone()[0]
    check("no observation is available before it was observed", bad == 0,
          str(bad))
    bad = conn.execute(
        "SELECT COUNT(*) FROM pit_macro_obs WHERE vintage_date < obs_date"
    ).fetchone()[0]
    check("no vintage predates its observation", bad == 0, str(bad))
    bad = conn.execute(
        """SELECT COUNT(*) FROM pit_macro_obs
            WHERE revision_class NOT IN (?, ?, ?)""",
        (pit_macro.REVISED, pit_macro.NOT_REVISED, pit_macro.NO_VINTAGE)
    ).fetchone()[0]
    check("every row carries a known revision class", bad == 0, str(bad))
    bad = conn.execute(
        """SELECT COUNT(*) FROM pit_macro_obs
            WHERE revision_class = ? AND value IS NOT NULL""",
        (pit_macro.NO_VINTAGE,)).fetchone()[0]
    check("no NO_VINTAGE marker carries a value", bad == 0, str(bad))
    bad = conn.execute(
        """SELECT COUNT(*) FROM pit_macro_obs
            WHERE revision_class = ? AND source NOT LIKE '%missing=%'""",
        (pit_macro.NO_VINTAGE,)).fetchone()[0]
    check("every NO_VINTAGE marker states its reason", bad == 0, str(bad))
    bad = conn.execute(
        """SELECT COUNT(*) FROM pit_macro_obs
            WHERE revision_class = ? AND source NOT LIKE '%retrieved=%'""",
        (pit_macro.NOT_REVISED,)).fetchone()[0]
    check("every never-revised row records when it was retrieved", bad == 0,
          str(bad))

    manifest = {r["series_id"]: r for r in conn.execute(
        "SELECT * FROM pit_macro_manifest")}
    expected = set(pit_macro.ALFRED_SERIES) | set(pit_macro.FRED_SERIES)
    check("the manifest covers every ingested series",
          expected.issubset(set(manifest)),
          f"{len(manifest)} rows, missing {sorted(expected - set(manifest))}")
    oil = manifest.get("DCOILWTICO")
    if oil is not None:
        note = str(oil["units_note"])
        check("the DCOILWTICO observation-set defect is recorded on the row",
              "12 observations" in note and "BLANK" in note,
              note[:80])
    for series_id in ("BAA10Y", "AAA10Y"):
        row = manifest.get(series_id)
        if row is None:
            check(f"{series_id} is in the manifest", False)
            continue
        note = str(row["units_note"])
        check(f"{series_id} is labelled option-unadjusted",
              "option-unadjusted" in note.lower() or "OPTION-UNADJUSTED" in note)
        check(f"{series_id} is explicitly NOT called an OAS",
              "NEVER an OAS" in note)

    curve = conn.execute(
        """SELECT COUNT(*) AS n, MIN(obs_date) AS lo, MAX(obs_date) AS hi,
                  COUNT(DISTINCT tenor) AS tenors FROM pit_curve_obs"""
    ).fetchone()
    check("the curve has rows", int(curve["n"]) > 0, str(curve["n"]))
    if int(curve["n"]) > 0:
        print(f"      curve: {curve['n']} rows, {curve['tenors']} tenors, "
              f"{curve['lo']}..{curve['hi']}")
        gap = conn.execute(
            """SELECT COUNT(*) FROM pit_curve_obs
                WHERE tenor = '30Y' AND obs_date BETWEEN '2002-03-01'
                  AND '2006-02-08'""").fetchone()[0]
        check("the 30-year is absent through its 2002-2006 discontinuation",
              gap == 0, f"{gap} rows found")
        resumed = conn.execute(
            """SELECT MIN(obs_date) FROM pit_curve_obs
                WHERE tenor = '30Y' AND obs_date > '2004-01-01'"""
        ).fetchone()[0]
        check("and resumes on 2006-02-09", str(resumed) == "2006-02-09",
              str(resumed))
        bad = conn.execute(
            "SELECT COUNT(*) FROM pit_curve_obs WHERE available_date <= obs_date"
        ).fetchone()[0]
        check("no curve point is available on its own observation date",
              bad == 0, str(bad))

        # Each tenor is gated by the data, so its first row IS its start date.
        starts = {r["tenor"]: str(r["lo"]) for r in conn.execute(
            "SELECT tenor, MIN(obs_date) AS lo FROM pit_curve_obs GROUP BY tenor")}
        for tenor, expected in (("1M", "2001-07-31"), ("2M", "2018-10-16"),
                                ("4M", "2022-10-19"), ("6W", "2025-02-18"),
                                ("20Y", "1993-10-01")):
            check(f"{tenor} starts where the files say it starts",
                  starts.get(tenor) == expected,
                  f"{starts.get(tenor)} vs {expected}")

        # THE POSITIONAL-READER CANARY, on the real rows this time.
        y2003 = pit_macro.curve_as_of(conn, "2003-12-31", "2004-01-31")
        check("the real 2003 curve has no 30-year", "30Y" not in y2003,
              " ".join(y2003))
        check("and its longest tenor is the 20-year at 5.10",
              y2003.get("20Y") == 5.10, str(y2003.get("20Y")))
        check("the real curve comes back shortest tenor first",
              list(y2003) == [t for t in pit_macro.TENOR_ORDER if t in y2003],
              " ".join(y2003))

    print("\n      coverage: " + str(pit_macro.coverage(conn)))


# --------------------------------------------------------------------------

def main() -> int:
    print("test_pit_macro")
    conn, path = temp_store()
    try:
        test_alfred_validation()
        test_fred_parsing()
        test_treasury_parsing()
        test_base_tag()
        test_disk_guard()
        test_availability(conn)
        test_store_roundtrip(conn)
        test_gap_rows(conn)
        test_curve_store(conn)
        test_publication_lag(conn)
        test_checkpoint(conn, path)
    finally:
        drop_store(conn, path)

    live, rows = live_store()
    if live is None:
        section("LIVE STORE")
        print("  SKIP  the production store is not present")
    elif rows == 0:
        section("LIVE STORE")
        print("  SKIP  pit_macro_obs is empty -- run `python pit_macro.py` first")
        live.close()
    else:
        try:
            test_live(live)
            test_live_rebases(live)
            test_live_gaps(live)
            test_live_integrity(live)
        finally:
            live.close()

    print(f"\n{_CHECKS - len(_FAILURES)}/{_CHECKS} checks passed")
    if _FAILURES:
        print("FAILED: " + ", ".join(_FAILURES))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
