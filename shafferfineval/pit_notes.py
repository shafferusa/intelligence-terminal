"""The SEC Financial Statement AND NOTES data sets, read WITHOUT downloading them.

This module exists for one fact that the compact DERA sets cannot carry, and it
is shaped end to end by one constraint that is not about data at all.

THE FACT. `dei:TradingSymbol` is a NON-NUMERIC tag. DERA's Financial Statement
`num.txt` holds numeric facts only, so it cannot carry a ticker at any price --
verified rather than assumed: `companyconcept` for `dei/TradingSymbol` answers
HTTP 404 for every CIK tried while a control concept answers 200, and
`companyfacts` carries zero TradingSymbol rows under the `dei` taxonomy. The
Financial Statement and Notes sets publish a `txt.tsv` member holding every
non-numeric tagged fact at accession level, and that is the only bulk, as-filed,
point-in-time source for this tag that exists. Everything else on offer -- the
submissions API's `tickers` array, any vendor symbol map -- is a snapshot of
TODAY'S SURVIVORS, which is the exact bias the point-in-time store was built to
remove.

THE CONSTRAINT. The whole archive is 27,649,515,145 bytes (25.75 GiB) across 80
files, and the machine it must land on has ~13 GiB free on a volume at 95% full.
An earlier ingest on this machine already failed by filling the disk. So this
module NEVER DOWNLOADS A FILE. It parses each zip's central directory from the
tail over HTTP Range, then range-fetches only the two members it needs --
`sub.tsv` and `txt.tsv` -- and inflates them through a `zlib` decompressor in
streaming fashion, holding one buffer at a time and writing nothing to disk but
the database rows themselves. Measured by the pilot across ten quarters: 1,582
MiB of 3,480 MiB fetched (45.5%) at 18.0 MiB/s, projecting ~11.71 GiB for the
full archive. Peak disk cost is the SQLite growth and nothing else.

That is also why `zipfile` is not used. `zipfile.ZipFile` needs a seekable file
object, and giving it one means either downloading the member or writing a temp
file -- both of which are the failure this module is built to avoid.

THREE INVARIANTS.

1. NOTHING BUT DATABASE ROWS TOUCHES THE DISK. No temp file, no `.part`, no
   cache. `ingest_period` checks `shutil.disk_usage` BEFORE each period and
   returns `aborted_low_disk` rather than proceeding, because a partial symbol
   ingest is resumable and a full disk is not.

2. AVAILABILITY IS COMPUTED BY THE SAME POLICY AS EVERY OTHER FACT. A cover page
   is not special. `pit_policy.available_date` decides, from `sub.tsv`'s own
   `accepted` stamp, with the store's trading calendar as the session resolver.
   An 8-K accepted at 16:31 ET announcing a ticker is not tradeable information
   that afternoon.

3. A REJECTED VALUE IS STORED WITH ITS STATUS, NEVER DROPPED. Old GM filed the
   literal string `CK0000040730`; `AIG PRA` and `SNV - PrE` are preferred series,
   not tickers. Each is written with a status that excludes it from
   `pit_symbols.symbol_as_of` and keeps it countable in a coverage report. A
   silently discarded row is a finding nobody can ever audit.

MEASURED, 2026-09-21, live:

    landing page    https://www.sec.gov/data-research/financial-statement-notes-data-sets
    file path       /files/dera/data/financial-statement-notes-data-sets/
                    (NOT ".../financial-statement-and-notes-data-sets/", which
                     404s, and not the /dera/data/...html path either)
    80 files        66 quarterly 2009q1..2025q2 + 14 monthly 2025_07..2026_08;
                    monthly REPLACES quarterly from 2025q3
    three names     are irregular and must not be generated: 2010q1_notes_1.zip,
                    2010q2_notes_0.zip, 2010q3_notes_0.zip
    Range           Accept-Ranges: bytes, HTTP 206 honoured on every file tried
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import sqlite3
import struct
import time
import zlib
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

import pit_policy
import pit_store
import pit_symbols
from pit_store import bulk_load

__all__ = [
    "NOTES_BASE_URL", "NOTES_LANDING_URL", "NOTES_FILENAME_OVERRIDES",
    "SEC_USER_AGENT", "SEC_MIN_INTERVAL", "SYMBOL_FIRST_PERIOD",
    "MIN_FREE_BYTES", "INGEST_SOURCE", "SYMBOL_TAG", "COMPANION_TAGS",
    "quarterly_periods", "monthly_periods", "archive_periods",
    "symbol_periods", "notes_filename", "notes_url", "discover_notes_files",
    "free_bytes", "disk_report", "checkpoint_wal",
    "http_session", "head_notes_file", "central_directory", "stream_member",
    "iter_tsv_rows", "read_submissions", "collect_symbol_rows",
    "entity_id_map", "period_already_ingested", "ingest_period", "ingest_archive",
    "symbol_coverage_by_year", "universe_symbol_coverage",
]


# --------------------------------------------------------------------------
# Source
# --------------------------------------------------------------------------

NOTES_BASE_URL = ("https://www.sec.gov/files/dera/data/"
                  "financial-statement-notes-data-sets/")

NOTES_LANDING_URL = ("https://www.sec.gov/data-research/"
                     "financial-statement-notes-data-sets")

#: Three published names do not follow the pattern. They are listed rather than
#: generated because a generated `2010q1_notes.zip` 404s and would silently cost
#: the first quarter in which the tag is worth having.
NOTES_FILENAME_OVERRIDES = {
    "2010q1": "2010q1_notes_1.zip",
    "2010q2": "2010q2_notes_0.zip",
    "2010q3": "2010q3_notes_0.zip",
}

#: Required on every .gov request. Never a browser UA on an SEC host.
SEC_USER_AGENT = "ShafferFinEval/1.0 (loganshaffer87@gmail.com)"

#: SEC's published ceiling is 10 requests a second. 0.15s is ~6.7/s.
SEC_MIN_INTERVAL = 0.15

HTTP_TIMEOUT = 300

#: The archive's first and last published periods, measured 2026-09-21.
ARCHIVE_FIRST_QUARTER = "2009q1"
ARCHIVE_LAST_QUARTER = "2025q2"
ARCHIVE_FIRST_MONTH = "2025_07"
ARCHIVE_LAST_MONTH = "2026_08"

#: Where the symbol ingest starts, and WHY it is not the archive's first file.
#: `dei:TradingSymbol` is populated from 2009-10-01. 2009q1..q3 carry the tag on
#: no filing at all, so fetching them would cost transfer for nothing. This is
#: NOT the 2019 cover-page rule, which added Security12bTitle and
#: SecurityExchangeName rather than TradingSymbol.
SYMBOL_FIRST_PERIOD = "2009q4"

#: Abort threshold. Below this much free space the ingest stops rather than
#: continues, because the failure mode of continuing is an unusable machine and
#: the failure mode of stopping is a resumable gap.
MIN_FREE_BYTES = 3 * (1 << 30)

#: `pit_ingest_run.source` for this loader. One row per period, which is what
#: makes the ingest resumable after an abort.
INGEST_SOURCE = "dera_notes_symbols"

#: The tag this module exists for, and the two that qualify it. The companions
#: are what separates a common share class from a listed note, and they exist
#: only from the 2019 cover-page rule -- so a NULL title is "unknown", never
#: "not common stock". `pit_symbols` handles that tri-state explicitly.
SYMBOL_TAG = "TradingSymbol"
TITLE_TAG = "Security12bTitle"
EXCHANGE_TAG = "SecurityExchangeName"
COMPANION_TAGS = frozenset({TITLE_TAG, EXCHANGE_TAG})
WANTED_TAGS = frozenset({SYMBOL_TAG}) | COMPANION_TAGS

#: Only the `dei` taxonomy counts. A filer's own namespace may define a tag of
#: the same name meaning something else, and a custom `TradingSymbol` is not the
#: cover-page assertion this module claims to be reading.
DEI_VERSION_PREFIX = "dei"

#: The two members read out of a ~1 GB zip. Everything else -- num.tsv, pre.tsv,
#: cal.tsv, ren.tsv, dim.tsv, tag.tsv -- is never fetched.
SUB_MEMBER = "sub.tsv"
TXT_MEMBER = "txt.tsv"

#: sub.tsv fields this loader reads. Read BY NAME: the layout has been stable,
#: but a positional reader would corrupt silently if SEC inserted a column and
#: this one simply fails to find it.
_SUB_FIELDS = ("adsh", "cik", "form", "period", "filed", "accepted")

#: txt.tsv fields this loader reads.
_TXT_FIELDS = ("adsh", "tag", "version", "ddate", "qtrs", "dimh", "coreg", "value")


# --------------------------------------------------------------------------
# Period arithmetic
# --------------------------------------------------------------------------

_QUARTER_RE = re.compile(r"^(\d{4})q([1-4])$")
_MONTH_RE = re.compile(r"^(\d{4})_(0[1-9]|1[0-2])$")


def _parse_period(period: str) -> tuple[int, int, bool]:
    """('2021q2' | '2025_07') -> (year, index, is_monthly). Raises otherwise."""
    text = str(period).strip().lower()
    quarter = _QUARTER_RE.match(text)
    if quarter:
        return int(quarter.group(1)), int(quarter.group(2)), False
    month = _MONTH_RE.match(text)
    if month:
        return int(month.group(1)), int(month.group(2)), True
    raise ValueError(f"not a notes period id: {period!r} (want '2021q2' or '2025_07')")


def quarterly_periods(first: str = ARCHIVE_FIRST_QUARTER,
                      last: str = ARCHIVE_LAST_QUARTER) -> list[str]:
    """Every quarterly period id from `first` to `last`, inclusive, in order."""
    y, q, monthly = _parse_period(first)
    if monthly:
        raise ValueError(f"{first!r} is a month, not a quarter")
    end_y, end_q, end_monthly = _parse_period(last)
    if end_monthly:
        raise ValueError(f"{last!r} is a month, not a quarter")
    out: list[str] = []
    while (y, q) <= (end_y, end_q):
        out.append(f"{y}q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def monthly_periods(first: str = ARCHIVE_FIRST_MONTH,
                    last: str = ARCHIVE_LAST_MONTH) -> list[str]:
    """Every monthly period id from `first` to `last`, inclusive, in order."""
    y, m, monthly = _parse_period(first)
    if not monthly:
        raise ValueError(f"{first!r} is a quarter, not a month")
    end_y, end_m, end_monthly = _parse_period(last)
    if not end_monthly:
        raise ValueError(f"{last!r} is a quarter, not a month")
    out: list[str] = []
    while (y, m) <= (end_y, end_m):
        out.append(f"{y}_{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def archive_periods() -> list[str]:
    """The whole published archive, oldest first: quarters then months.

    Quarterly and monthly do NOT overlap. SEC switched cadence at 2025q3: the
    quarterly series stops at 2025q2 and the monthly series starts at 2025_07,
    so concatenating them covers each filing exactly once. Treating them as
    alternative views of the same window would double-count 2025H2.
    """
    return quarterly_periods() + monthly_periods()


def symbol_periods(first: str = SYMBOL_FIRST_PERIOD) -> list[str]:
    """The archive from the first period in which `dei:TradingSymbol` exists."""
    all_periods = archive_periods()
    if first not in all_periods:
        raise ValueError(f"{first!r} is not a published notes period")
    return all_periods[all_periods.index(first):]


def notes_filename(period: str) -> str:
    """The published zip name for a period, honouring the three irregulars."""
    _parse_period(period)
    key = str(period).strip().lower()
    return NOTES_FILENAME_OVERRIDES.get(key, f"{key}_notes.zip")


def notes_url(period: str) -> str:
    """The full URL for one period's notes zip."""
    return NOTES_BASE_URL + notes_filename(period)


def discover_notes_files(session: Any = None) -> dict[str, str]:
    """period -> filename, read LIVE from the landing page. Network.

    The hardcoded map is what the ingest runs on, because an ingest that
    silently re-scrapes its own source list cannot be reproduced. This function
    is the check on that map: run it, diff it, and update the constants
    deliberately when SEC publishes a new month.
    """
    session = session or http_session()
    _throttle()
    resp = session.get(NOTES_LANDING_URL, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    found: dict[str, str] = {}
    for href in re.findall(
            r'href="(/files/dera/data/financial-statement-notes-data-sets/[^"]+\.zip)"',
            resp.text):
        name = href.rsplit("/", 1)[1]
        stem = name[:-4]
        for suffix in ("_notes_1", "_notes_0", "_notes"):
            if stem.endswith(suffix):
                found[stem[: -len(suffix)]] = name
                break
    return dict(sorted(found.items()))


# --------------------------------------------------------------------------
# Disk, which is the binding constraint
# --------------------------------------------------------------------------

def free_bytes(path: str) -> int:
    """Free bytes on the volume holding `path`. The gate on every period."""
    target = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    return shutil.disk_usage(target or ".").free


def disk_report(path: str) -> dict[str, Any]:
    """total / used / free for the volume holding `path`, in bytes and GiB."""
    target = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    total, used, free = shutil.disk_usage(target or ".")
    return {"total_bytes": total, "used_bytes": used, "free_bytes": free,
            "total_gib": round(total / (1 << 30), 2),
            "used_gib": round(used / (1 << 30), 2),
            "free_gib": round(free / (1 << 30), 2)}


def checkpoint_wal(conn: sqlite3.Connection) -> dict[str, Any]:
    """TRUNCATE the write-ahead log AND REPORT WHETHER IT ACTUALLY HAPPENED.

    `PRAGMA wal_checkpoint(TRUNCATE)` returns `(busy, log_pages, checkpointed)`
    and returns `(1, ...)` -- busy, nothing done -- whenever another connection
    holds a read lock. It raises nothing and prints nothing. That silence is
    exactly how an earlier ingest on this machine grew a 9.3 GB WAL and filled
    the disk: the checkpoint was being called on schedule and failing on every
    call.

    So the return value is read, named, and handed back to the caller, which is
    expected to treat `busy` as a condition to report rather than a no-op.
    """
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is None:
        return {"status": "no_result", "busy": None, "log_pages": None,
                "checkpointed_pages": None}
    busy, log_pages, checkpointed = int(row[0]), row[1], row[2]
    return {"status": "busy" if busy else "truncated", "busy": busy,
            "log_pages": log_pages, "checkpointed_pages": checkpointed}


# --------------------------------------------------------------------------
# HTTP, range-only
# --------------------------------------------------------------------------

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = SEC_MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def http_session() -> Any:
    """A requests session carrying the SEC user agent and NO content encoding.

    `Accept-Encoding: identity` is not a nicety. A range request whose response
    the server gzips returns bytes whose offsets do not match the zip structure
    this module just parsed, and the failure would look like a corrupt archive.
    """
    import requests                    # local: the period arithmetic is offline

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT,
                            "Accept-Encoding": "identity"})
    return session


def head_notes_file(period: str, session: Any = None) -> dict[str, Any]:
    """Size and range support for one period's zip, without fetching a byte."""
    session = session or http_session()
    url = notes_url(period)
    _throttle()
    resp = session.head(url, timeout=60, allow_redirects=True)
    length = resp.headers.get("Content-Length")
    return {"period": period, "url": url, "http_status": resp.status_code,
            "size_bytes": int(length) if length and length.isdigit() else None,
            "accept_ranges": (resp.headers.get("Accept-Ranges") or "").lower(),
            "ok": resp.status_code == 200}


def _range_get(session: Any, url: str, start: int, length: int) -> bytes:
    """Exactly `length` bytes from `start`. Raises if the server ignores Range.

    A server that answers 200 to a Range request is sending the WHOLE FILE, and
    on a 1 GB member that is the disk-filling failure this module exists to
    avoid. So a non-206 status is an error rather than a slow path.
    """
    if length <= 0:
        return b""
    end = start + length - 1
    _throttle()
    resp = session.get(url, headers={"Range": f"bytes={start}-{end}"},
                       timeout=HTTP_TIMEOUT, stream=True)
    try:
        if resp.status_code != 206:
            resp.close()
            raise OSError(
                f"range request was not honoured: HTTP {resp.status_code} for "
                f"bytes={start}-{end} of {url}")
        return resp.content
    finally:
        resp.close()


# --------------------------------------------------------------------------
# Zip central directory, parsed from the tail
# --------------------------------------------------------------------------

_EOCD_SIG = b"PK\x05\x06"
_EOCD64_LOCATOR_SIG = b"PK\x06\x07"
_EOCD64_SIG = b"PK\x06\x06"
_CENTRAL_SIG = b"PK\x01\x02"
_LOCAL_SIG = b"PK\x03\x04"

#: The end-of-central-directory record is 22 bytes plus a comment of at most
#: 65,535, so it is always inside the last 65,557 bytes. Nothing larger needs
#: fetching to find it.
_EOCD_SEARCH_BYTES = 65557


def _zip64_extra(extra: bytes, wanted: int) -> list[int]:
    """The first `wanted` 8-byte values from a Zip64 extended-information field.

    Zip64 writes only the fields that overflowed, in a fixed order -- uncompressed
    size, compressed size, local header offset, disk number -- so the caller asks
    for as many as it knows it needs and reads them positionally from the start.
    """
    pos = 0
    while pos + 4 <= len(extra):
        header_id, size = struct.unpack("<HH", extra[pos:pos + 4])
        body = extra[pos + 4: pos + 4 + size]
        if header_id == 0x0001:
            out: list[int] = []
            for i in range(wanted):
                chunk = body[i * 8: i * 8 + 8]
                if len(chunk) < 8:
                    break
                out.append(struct.unpack("<Q", chunk)[0])
            return out
        pos += 4 + size
    return []


def central_directory(session: Any, url: str, size: int) -> dict[str, dict[str, Any]]:
    """name -> {method, comp_size, uncomp_size, header_offset} for one remote zip.

    The zip format's index lives at the END of the file, which is the whole
    reason this module can work over Range at all: ~1 KB of tail tells you where
    every member starts, and the 1 GB in between is never requested.

    Zip64 is handled because the archive's later quarters are large enough to
    reach it, and a silently truncated 32-bit offset would range-fetch the wrong
    bytes rather than fail.
    """
    tail_len = min(size, _EOCD_SEARCH_BYTES)
    tail = _range_get(session, url, size - tail_len, tail_len)
    idx = tail.rfind(_EOCD_SIG)
    if idx < 0:
        raise OSError(f"no end-of-central-directory record in the last "
                      f"{tail_len} bytes of {url}")
    eocd = tail[idx: idx + 22]
    entries, cd_size, cd_offset = struct.unpack("<HLL", eocd[10:20])

    locator = tail.rfind(_EOCD64_LOCATOR_SIG, 0, idx)
    if locator >= 0 and (entries == 0xFFFF or cd_size == 0xFFFFFFFF
                         or cd_offset == 0xFFFFFFFF):
        eocd64_offset = struct.unpack("<Q", tail[locator + 8: locator + 16])[0]
        head = _range_get(session, url, eocd64_offset, 56)
        if head[:4] != _EOCD64_SIG:
            raise OSError(f"Zip64 locator points at non-Zip64 record in {url}")
        entries = struct.unpack("<Q", head[32:40])[0]
        cd_size, cd_offset = struct.unpack("<QQ", head[40:56])

    raw = _range_get(session, url, cd_offset, cd_size)
    members: dict[str, dict[str, Any]] = {}
    pos = 0
    for _ in range(int(entries)):
        if raw[pos: pos + 4] != _CENTRAL_SIG:
            raise OSError(f"corrupt central directory at byte {pos} of {url}")
        method, = struct.unpack("<H", raw[pos + 10: pos + 12])
        comp_size, uncomp_size = struct.unpack("<LL", raw[pos + 20: pos + 28])
        name_len, extra_len, comment_len = struct.unpack("<HHH", raw[pos + 28: pos + 34])
        header_offset, = struct.unpack("<L", raw[pos + 42: pos + 46])
        name = raw[pos + 46: pos + 46 + name_len].decode("utf-8", "replace")
        extra = raw[pos + 46 + name_len: pos + 46 + name_len + extra_len]

        overflowed = [uncomp_size == 0xFFFFFFFF, comp_size == 0xFFFFFFFF,
                      header_offset == 0xFFFFFFFF]
        if any(overflowed):
            values = _zip64_extra(extra, sum(overflowed))
            taken = 0
            if overflowed[0] and taken < len(values):
                uncomp_size = values[taken]; taken += 1
            if overflowed[1] and taken < len(values):
                comp_size = values[taken]; taken += 1
            if overflowed[2] and taken < len(values):
                header_offset = values[taken]; taken += 1

        members[name] = {"name": name, "method": method, "comp_size": comp_size,
                         "uncomp_size": uncomp_size, "header_offset": header_offset}
        pos += 46 + name_len + extra_len + comment_len
    return members


def stream_member(session: Any, url: str, member: dict[str, Any], *,
                  chunk_bytes: int = 1 << 20,
                  counter: Optional[dict[str, int]] = None) -> Iterator[bytes]:
    """Inflate ONE zip member straight off the wire. Never touches the disk.

    The local file header must be read first: the central directory records
    where it starts, but only the local header knows how long its own name and
    extra fields are, and the compressed data begins after them. Reading 30
    bytes to learn that is cheaper by six orders of magnitude than guessing.

    `counter['bytes']` accumulates COMPRESSED bytes actually transferred, which
    is the number this module is judged on.
    """
    head = _range_get(session, url, member["header_offset"], 30)
    if head[:4] != _LOCAL_SIG:
        raise OSError(f"no local file header for {member['name']} in {url}")
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    start = member["header_offset"] + 30 + name_len + extra_len
    size = member["comp_size"]
    if size <= 0:
        return
    end = start + size - 1

    _throttle()
    resp = session.get(url, headers={"Range": f"bytes={start}-{end}"},
                       timeout=HTTP_TIMEOUT, stream=True)
    try:
        if resp.status_code != 206:
            raise OSError(f"range request was not honoured for {member['name']}: "
                          f"HTTP {resp.status_code}")
        if member["method"] == 0:
            for block in resp.iter_content(chunk_bytes):
                if counter is not None:
                    counter["bytes"] = counter.get("bytes", 0) + len(block)
                if block:
                    yield block
            return
        if member["method"] != 8:
            raise OSError(f"unsupported zip compression method "
                          f"{member['method']} for {member['name']}")
        engine = zlib.decompressobj(-zlib.MAX_WBITS)
        for block in resp.iter_content(chunk_bytes):
            if counter is not None:
                counter["bytes"] = counter.get("bytes", 0) + len(block)
            out = engine.decompress(block)
            if out:
                yield out
        tail = engine.flush()
        if tail:
            yield tail
    finally:
        resp.close()


# --------------------------------------------------------------------------
# Tab-separated parsing, by column name
# --------------------------------------------------------------------------

#: The wanted tags as BYTES, for the prefilter below.
_WANTED_TAG_BYTES = frozenset(tag.encode("utf-8") for tag in WANTED_TAGS)


def wanted_tag_line(line: bytes) -> bool:
    """Whether a raw txt.tsv line carries a tag this loader wants. Bytes only.

    The prefilter, and it earns its place. txt.tsv is millions of rows a quarter
    and fewer than one in a hundred is a cover-page tag; the rest are text
    blocks and footnotes running to thousands of characters each. Decoding and
    splitting every one of those to discover it is a `PolicyTextBlock` costs
    more than the network does. `tag` is the second tab-delimited field, so two
    `bytes.find` calls at C speed decide it without touching the remainder of
    the line.
    """
    first = line.find(b"\t")
    if first < 0:
        return False
    second = line.find(b"\t", first + 1)
    if second < 0:
        return False
    return line[first + 1: second] in _WANTED_TAG_BYTES


def iter_tsv_rows(blocks: Iterable[bytes], fields: Sequence[str], *,
                  line_filter: Optional[Callable[[bytes], bool]] = None,
                  counter: Optional[dict[str, int]] = None
                  ) -> Iterator[dict[str, str]]:
    """Stream a tab-separated member as dicts of ONLY `fields`. Memory-flat.

    `csv.DictReader` is not used for two reasons. It would build a dict of all
    twenty txt.tsv columns per row -- including `value`, which on a text-block
    row is thousands of characters this module discards -- and it needs a text
    file object, which would mean materialising the member. This reads the
    header once, records the indices it wants, and never allocates the rest.

    Fields are read BY NAME and a missing one raises, so a column inserted
    upstream stops the ingest instead of shifting every value by one.

    THE SPLIT IS PER BLOCK, NOT PER LINE, and that is a correctness-of-runtime
    matter rather than a micro-optimisation. Finding one newline at a time and
    re-slicing the tail copies the whole remaining buffer once per line: on a
    1 MB block inflating to ~8 MB of ~200-byte rows that is forty thousand
    copies of eight megabytes, and it turns a thirty-second quarter into one
    that never finishes. Measured on 2023q1 before the fix: a single quarter had
    not completed in ten minutes. After: 15.0 seconds.

    `counter['lines_seen']` counts EVERY data line, including the ones
    `line_filter` rejects before they are decoded. It is kept separately
    because the filter makes the yielded rows a tiny fraction of the member,
    and a coverage report that quoted the yielded count as "rows scanned"
    would overstate this loader's reach by three orders of magnitude.
    """
    wanted = tuple(fields)
    seen = 0
    index: dict[str, int] = {}
    max_index = 0
    buffer = b""
    header_done = False
    for block in blocks:
        buffer += block
        if b"\n" not in buffer:
            continue
        lines = buffer.split(b"\n")
        buffer = lines.pop()                    # the trailing partial line
        for line in lines:
            if line.endswith(b"\r"):
                line = line[:-1]
            if not header_done:
                names = line.decode("utf-8", "replace").split("\t")
                index = {name: pos for pos, name in enumerate(names) if name in wanted}
                missing = [name for name in wanted if name not in index]
                if missing:
                    raise OSError(f"tsv member is missing columns {missing}; "
                                  f"header was {names[:12]}")
                max_index = max(index.values())
                header_done = True
                continue
            if not line:
                continue
            seen += 1
            if line_filter is not None and not line_filter(line):
                continue
            parts = line.decode("utf-8", "replace").split("\t")
            if len(parts) <= max_index:
                continue
            yield {name: parts[pos] for name, pos in index.items()}
    if buffer and header_done:
        seen += 1
        if line_filter is None or line_filter(buffer):
            parts = buffer.decode("utf-8", "replace").rstrip("\r").split("\t")
            if index and len(parts) > max_index:
                yield {name: parts[pos] for name, pos in index.items()}
    if counter is not None:
        counter["lines_seen"] = counter.get("lines_seen", 0) + seen


def read_submissions(session: Any, url: str, members: dict[str, dict[str, Any]],
                     counter: Optional[dict[str, int]] = None) -> dict[str, dict]:
    """adsh -> the six sub.tsv fields the symbol loader needs.

    sub.tsv is the one member held in memory, and it is small: this dataset's
    submission table is tens of thousands of rows a quarter against txt.tsv's
    millions. It must be in memory because availability and CIK are properties
    of the FILING and txt.tsv carries neither.
    """
    if SUB_MEMBER not in members:
        raise OSError(f"{SUB_MEMBER} is not a member of {url}")
    out: dict[str, dict] = {}
    blocks = stream_member(session, url, members[SUB_MEMBER], counter=counter)
    for row in iter_tsv_rows(blocks, _SUB_FIELDS):
        adsh = row.get("adsh") or ""
        if adsh:
            out[adsh] = row
    return out


def collect_symbol_rows(session: Any, url: str, members: dict[str, dict[str, Any]],
                        counter: Optional[dict[str, int]] = None
                        ) -> tuple[list[dict], dict[tuple, str], dict[tuple, str], dict]:
    """(symbol rows, titles, exchanges, stats) from one period's txt.tsv.

    The companions are keyed on `(adsh, dimh, coreg)` -- the SAME dimension as
    the symbol -- because that triple is what identifies one security within a
    filing. Berkshire's 2023 10-K reports thirteen symbols in thirteen
    dimensions, and joining its title rows on the accession alone would label
    every listed note "Class A common stock". The pilot measured 99.5% join
    coverage on the triple.

    Returned rather than inserted so that the join happens after the single
    streaming pass: a companion may appear in txt.tsv either side of its symbol.
    """
    if TXT_MEMBER not in members:
        raise OSError(f"{TXT_MEMBER} is not a member of {url}")
    symbols: list[dict] = []
    titles: dict[tuple, str] = {}
    exchanges: dict[tuple, str] = {}
    stats = {"txt_rows": 0, "cover_tag_rows": 0, "dei_tag_rows": 0,
             "non_dei_rejected": 0}
    lines: dict[str, int] = {}
    blocks = stream_member(session, url, members[TXT_MEMBER], counter=counter)
    for row in iter_tsv_rows(blocks, _TXT_FIELDS, line_filter=wanted_tag_line,
                             counter=lines):
        stats["cover_tag_rows"] += 1
        tag = row.get("tag") or ""
        if tag not in WANTED_TAGS:
            continue
        version = (row.get("version") or "")
        if not version.startswith(DEI_VERSION_PREFIX):
            stats["non_dei_rejected"] += 1
            continue
        stats["dei_tag_rows"] += 1
        key = (row.get("adsh") or "", row.get("dimh") or "", row.get("coreg") or "")
        value = (row.get("value") or "").strip()
        if tag == SYMBOL_TAG:
            symbols.append(row)
        elif tag == TITLE_TAG:
            if value:
                titles.setdefault(key, value)
        elif value:
            exchanges.setdefault(key, value)
    stats["txt_rows"] = lines.get("lines_seen", 0)
    return symbols, titles, exchanges, stats


# --------------------------------------------------------------------------
# The ingest
# --------------------------------------------------------------------------

def entity_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    """cik -> entity_id for every registered issuer, zero-padded and bare.

    Both spellings are keys on purpose. `pit_entity.cik` is zero-padded to ten
    and the notes `sub.tsv` writes the bare integer, and a lookup that missed on
    that difference would report "this issuer is unknown" for all 16,890 of them.
    """
    out: dict[str, int] = {}
    for row in conn.execute("SELECT entity_id, cik FROM pit_entity"):
        cik = str(row["cik"])
        out[cik] = int(row["entity_id"])
        out[cik.lstrip("0") or "0"] = int(row["entity_id"])
    return out


def period_already_ingested(conn: sqlite3.Connection, period: str) -> bool:
    """Whether this period was completed by an earlier run. The resume gate.

    Idempotency at the row level is already guaranteed by pit_symbol_obs's
    UNIQUE key, so a re-run would be correct -- but it would also re-transfer a
    gigabyte to insert nothing, and transfer is the scarce resource here.
    """
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM pit_ingest_run
            WHERE source = ? AND scope = ? AND status = 'complete'""",
        (INGEST_SOURCE, period),
    ).fetchone()
    return bool(row and row["n"])


def _day(value: Any) -> Optional[str]:
    """A DERA 'YYYYMMDD' or ISO date as 'YYYY-MM-DD'; None where unusable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return _dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _accepted(text: Any) -> Optional[str]:
    """'2021-05-06 16:28:00.0' -> '2021-05-06T16:28:00', naive, or None.

    Left NAIVE deliberately: `pit_policy` reads a naive stamp as Eastern wall
    clock, which is what EDGAR actually publishes. Attaching a UTC designator
    here would hand the latency policy the exact lie it exists to ignore.
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


DEFAULT_BATCH_SIZE = 5000


def ingest_period(conn: sqlite3.Connection, period: str, *,
                  session: Any = None,
                  entity_ids: Optional[dict[str, int]] = None,
                  next_session: Optional[Callable[[str], Optional[str]]] = None,
                  batch_size: int = DEFAULT_BATCH_SIZE,
                  min_free_bytes: int = MIN_FREE_BYTES,
                  db_path: Optional[str] = None,
                  force: bool = False) -> dict[str, Any]:
    """Load one period's reported trading symbols. Streams; writes only rows.

    The order of operations is the safety argument:

      1. free space is checked FIRST and the period is refused if short, because
         a refused period is a resumable gap and a full disk is not;
      2. the zip's index is read from its tail over Range -- ~1 KB;
      3. sub.tsv is streamed and held (tens of thousands of rows);
      4. txt.tsv is streamed, matched rows kept, everything else discarded as it
         passes;
      5. availability is computed ONCE PER FILING through `pit_policy`, not once
         per symbol, because latency is a property of the filing;
      6. rows are inserted in batches and the WAL is checkpointed with its
         return value checked.

    Returns a status record. `status` is one of 'complete', 'already_ingested',
    'aborted_low_disk', 'unavailable' or 'failed'.
    """
    started = time.time()
    db_path = db_path or pit_store.DEFAULT_PIT_DB_PATH
    before = disk_report(db_path)
    if before["free_bytes"] < min_free_bytes:
        return {"period": period, "status": "aborted_low_disk",
                "free_gib": before["free_gib"],
                "required_gib": round(min_free_bytes / (1 << 30), 2),
                "rows_inserted": 0, "bytes_downloaded": 0}
    if not force and period_already_ingested(conn, period):
        return {"period": period, "status": "already_ingested",
                "rows_inserted": 0, "bytes_downloaded": 0}

    session = session or http_session()
    entity_ids = entity_ids if entity_ids is not None else entity_id_map(conn)
    url = notes_url(period)
    counter: dict[str, int] = {"bytes": 0}
    ingest_id = pit_store.start_ingest(conn, INGEST_SOURCE, period)

    try:
        head = head_notes_file(period, session)
        if not head["ok"] or not head["size_bytes"]:
            pit_store.finish_ingest(conn, ingest_id, status="unavailable",
                                    summary={"http_status": head["http_status"]})
            return {"period": period, "status": "unavailable",
                    "http_status": head["http_status"], "rows_inserted": 0,
                    "bytes_downloaded": 0}

        members = central_directory(session, url, head["size_bytes"])
        subs = read_submissions(session, url, members, counter)
        symbols, titles, exchanges, stats = collect_symbol_rows(
            session, url, members, counter)

        availability: dict[str, dict] = {}
        rows: list[tuple] = []
        counts = {"ok": 0, "cik_placeholder": 0, "not_ticker_shaped": 0,
                  "empty": 0, "unknown_adsh": 0, "unmapped_cik": 0,
                  "bad_filed": 0, "with_title": 0, "with_exchange": 0}
        entities_seen: set[int] = set()
        created = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        source = f"{pit_symbols.SOURCE_DERA_NOTES}:{period}"
        inserted = 0

        with bulk_load(conn):
            for row in symbols:
                adsh = row.get("adsh") or ""
                sub = subs.get(adsh)
                if sub is None:
                    counts["unknown_adsh"] += 1
                    continue
                entity_id = entity_ids.get(str(sub.get("cik") or "").strip())
                if entity_id is None:
                    counts["unmapped_cik"] += 1
                    continue
                detail = availability.get(adsh)
                if detail is None:
                    filed = _day(sub.get("filed"))
                    if filed is None:
                        counts["bad_filed"] += 1
                        continue
                    accepted = _accepted(sub.get("accepted"))
                    detail = dict(pit_policy.available_date_detail(
                        accepted, filed, next_session))
                    detail["filed_day"] = filed
                    detail["accepted_raw"] = (str(sub.get("accepted")).strip()
                                              or None) if sub.get("accepted") else None
                    detail["form"] = (sub.get("form") or "") or None
                    availability[adsh] = detail

                raw_value = row.get("value") or ""
                symbol, status = pit_symbols.normalise_symbol(raw_value)
                counts[status] = counts.get(status, 0) + 1
                if not symbol:
                    # An empty value carries no symbol to key a UNIQUE row on.
                    # Counted, reported, and not stored -- the one case where a
                    # rejected value has nothing to store.
                    continue
                segments = row.get("dimh") or ""
                coreg = row.get("coreg") or ""
                key = (adsh, segments, coreg)
                title = titles.get(key)
                exchange = exchanges.get(key)
                if title:
                    counts["with_title"] += 1
                if exchange:
                    counts["with_exchange"] += 1
                period_end = _day(row.get("ddate"))
                try:
                    qtrs = int(row.get("qtrs") or 0)
                except ValueError:
                    qtrs = 0
                entities_seen.add(entity_id)
                rows.append((entity_id, symbol, raw_value, adsh, detail["form"],
                             period_end, qtrs, segments, coreg, title, exchange,
                             period_end, detail["filed_day"], detail["accepted_raw"],
                             detail["accepted_et"], detail["available_date"],
                             detail["latency_policy_version"], status, source,
                             pit_symbols.SYMBOL_SOURCE_VERSION, created))
                if len(rows) >= batch_size:
                    inserted += pit_symbols.insert_symbol_obs(conn, rows)
                    conn.commit()
                    rows = []
            if rows:
                inserted += pit_symbols.insert_symbol_obs(conn, rows)
                conn.commit()

        checkpoint = checkpoint_wal(conn)
        after = disk_report(db_path)
        record = {
            "period": period, "status": "complete",
            "rows_inserted": inserted,
            "symbol_rows_seen": len(symbols),
            "txt_rows_scanned": stats["txt_rows"],
            "submissions": len(subs),
            "entities": len(entities_seen),
            "by_status": {k: v for k, v in counts.items() if v},
            "bytes_downloaded": counter["bytes"],
            "zip_bytes": head["size_bytes"],
            "fraction_of_zip": round(counter["bytes"] / head["size_bytes"], 4),
            "checkpoint": checkpoint,
            "free_gib_after": after["free_gib"],
            "db_growth_bytes": before["free_bytes"] - after["free_bytes"],
            "seconds": round(time.time() - started, 1),
        }
        pit_store.finish_ingest(conn, ingest_id, status="complete",
                                rows_read=stats["txt_rows"], rows_kept=inserted,
                                rows_rejected=len(symbols) - inserted,
                                bytes_downloaded=counter["bytes"], summary=record)
        return record
    except Exception as exc:
        pit_store.finish_ingest(conn, ingest_id, status="failed",
                                bytes_downloaded=counter["bytes"],
                                summary={"error": f"{type(exc).__name__}: {exc}"})
        return {"period": period, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "rows_inserted": 0, "bytes_downloaded": counter["bytes"]}


def ingest_archive(conn: sqlite3.Connection, periods: Optional[Sequence[str]] = None, *,
                   session: Any = None,
                   next_session: Optional[Callable[[str], Optional[str]]] = None,
                   min_free_bytes: int = MIN_FREE_BYTES,
                   db_path: Optional[str] = None,
                   progress: Optional[Callable[[dict], None]] = None
                   ) -> dict[str, Any]:
    """Every period in order, stopping CLEANLY the moment disk runs short.

    The loop re-checks free space before each period rather than trusting the
    check at the start, because the database is growing underneath it. An abort
    returns everything completed so far and the period it stopped at, which is
    all a resume needs: `period_already_ingested` skips the rest.

    `peak_disk_used_bytes` is measured, not estimated -- the largest drop in
    free space observed against the reading taken before the first period.
    """
    periods = list(periods) if periods is not None else symbol_periods()
    session = session or http_session()
    db_path = db_path or pit_store.DEFAULT_PIT_DB_PATH
    entity_ids = entity_id_map(conn)
    start_disk = disk_report(db_path)
    min_free = start_disk["free_bytes"]
    results: list[dict] = []
    started = time.time()

    for period in periods:
        current = free_bytes(db_path)
        min_free = min(min_free, current)
        if current < min_free_bytes:
            return _archive_summary(results, periods, period, start_disk, min_free,
                                    started, "aborted_low_disk")
        record = ingest_period(conn, period, session=session,
                               entity_ids=entity_ids, next_session=next_session,
                               min_free_bytes=min_free_bytes, db_path=db_path)
        results.append(record)
        min_free = min(min_free, free_bytes(db_path))
        if progress is not None:
            progress(record)
        if record["status"] == "aborted_low_disk":
            return _archive_summary(results, periods, period, start_disk, min_free,
                                    started, "aborted_low_disk")
    return _archive_summary(results, periods, None, start_disk, min_free,
                            started, "complete")


def _archive_summary(results: list[dict], periods: Sequence[str],
                     stopped_at: Optional[str], start_disk: dict,
                     min_free: int, started: float, status: str) -> dict[str, Any]:
    done = [r for r in results if r["status"] in ("complete", "already_ingested")]
    return {
        "status": status,
        "periods_requested": len(periods),
        "periods_done": len(done),
        "stopped_at": stopped_at,
        "rows_inserted": sum(r.get("rows_inserted", 0) for r in results),
        "bytes_downloaded": sum(r.get("bytes_downloaded", 0) for r in results),
        "failed": [r["period"] for r in results if r["status"] == "failed"],
        "unavailable": [r["period"] for r in results if r["status"] == "unavailable"],
        "free_gib_at_start": start_disk["free_gib"],
        "min_free_gib": round(min_free / (1 << 30), 2),
        "peak_disk_used_bytes": start_disk["free_bytes"] - min_free,
        "peak_disk_used_gib": round(
            (start_disk["free_bytes"] - min_free) / (1 << 30), 3),
        "seconds": round(time.time() - started, 1),
        "results": results,
    }


# --------------------------------------------------------------------------
# Coverage reporting
# --------------------------------------------------------------------------

def symbol_coverage_by_year(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Distinct entities with at least one USABLE symbol observation, by year.

    Keyed on `available_date`, not `filed`, so the number answers the question a
    replay asks -- how many issuers had a symbol a reader could already have
    seen in that year -- rather than how many filings were stamped with it.
    """
    rows = conn.execute(
        """SELECT substr(available_date, 1, 4) AS year,
                  COUNT(DISTINCT entity_id) AS entities,
                  COUNT(*) AS observations
             FROM pit_symbol_obs
            WHERE status = ?
            GROUP BY year ORDER BY year""",
        (pit_symbols.OBS_OK,),
    ).fetchall()
    return [{"year": r["year"], "entities": r["entities"],
             "observations": r["observations"]} for r in rows]


def universe_symbol_coverage(conn: sqlite3.Connection, as_of: str) -> dict[str, Any]:
    """How much of the peer universe has a resolvable symbol on `as_of`.

    THIS IS THE CEILING ON THE SCORED UNIVERSE. An issuer with no symbol on the
    date cannot be matched to a price series, cannot be given a forward label,
    and therefore cannot be held -- however complete its fundamentals are. It
    stays in the peer set, because its percentile contribution is real; it
    cannot enter the scored set, because a position cannot be taken in a line
    that cannot be identified.

    Resolution goes through `pit_symbols.symbol_as_of`, which means it obeys the
    availability bound and the share-class rule rather than merely asking
    whether any row exists.
    """
    peers = pit_symbols.peer_universe_as_of(conn, as_of)
    resolved = 0
    for peer in peers:
        if pit_symbols.symbol_as_of(conn, peer["entity_id"], as_of) is not None:
            resolved += 1
    total = len(peers)
    return {"as_of": str(as_of)[:10], "peer_universe": total,
            "with_symbol": resolved, "without_symbol": total - resolved,
            "coverage": round(resolved / total, 4) if total else 0.0}
