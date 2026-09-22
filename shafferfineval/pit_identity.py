"""Historical identity for the point-in-time research store.

This is the layer the whole PIT build rests on, and it exists because of one
measured fact: of the 9,320 CIKs that filed in 2008Q4, only 2,193 (23.5%) appear
in today's `company_tickers.json`. Three companies in four are simply gone from
any current map. A research store keyed on today's symbols cannot represent them
and therefore cannot measure anything about failure.

Three rules, in order of how much damage breaking them does:

1. AN ISSUER IS A CIK. `pit_entity.entity_id` is CIK-backed and is the join key
   everywhere. The live `assets` table is `UNIQUE (symbol, asset_class)`, holds
   1,721 current rows and has no `cik` column; querying it for LEH, SIVB, FRC,
   BBBY, ATVI, TWTR, SHLD or WAMU returns zero rows. It must never decide
   whether a historical entity may exist, so nothing here reads it.

2. A TRADEABLE LINE IS A LISTING, NOT A TICKER. `BBBY` today returns HTTP 200
   with `longName` "Bed Bath & Beyond, Inc." on NYSE. The name matches the dead
   retailer, the exchange matches, and the instrument is a different company
   whose first bar is 2026-07-17. `meta.firstTradeDate` is the only field that
   separates them, so `resolve_listing` gates on it and refuses to guess.

3. NOTHING FROM THE CURRENT SNAPSHOT MAY REACH A REPLAY. The submissions API's
   `tickers`, `exchanges`, `sic`, `name` and `entityType` are all mutable
   current-state fields with no validity interval. They are recorded in the
   ingest report -- which no selector reads -- and nowhere else. The dated
   substitutes are `pit_entity_name` (from `formerNames`) and `pit_entity_sic`
   (from each filing's own `<ASSIGNED-SIC>` header).

The schema, the policy versions and every write helper live in `pit_store`; this
module only decides WHAT is true and hands it over. Stdlib plus `requests`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import xml.etree.ElementTree as _ET
from typing import Any, Iterable, Optional, Sequence

import requests

import pit_store
from market_data import BROWSER_UA, HTTP_TIMEOUT, YAHOO_HOSTS

# --------------------------------------------------------------------------
# Endpoints and politeness
# --------------------------------------------------------------------------

#: Identifying UA with a contact address, as the SEC requires. A browser UA is
#: never spoofed on a .gov host.
SEC_USER_AGENT = "ShafferFinEval/1.0 (loganshaffer87@gmail.com)"

#: The SEC's published ceiling is 10 requests/second; 0.125s holds the floor
#: at 8/s so a burst arriving slightly early still cannot cross the line.
SEC_MIN_INTERVAL = 0.125
YAHOO_MIN_INTERVAL = 0.10

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
FILING_DIR_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}"
CHART_URL = "https://{host}/v8/finance/chart/{symbol}"

#: Row provenance. Every identity row says which document produced it.
SOURCE_SUBMISSIONS = "sec:submissions"
SOURCE_SUBMISSIONS_CURRENT = "sec:submissions:current_snapshot"
SOURCE_FILING_HEADER = "sec:filing_header"
SOURCE_YAHOO_META = "yahoo:chart_meta"

# --------------------------------------------------------------------------
# Form vocabulary
# --------------------------------------------------------------------------

#: Annual reports. `pit_entity_sic` carries one row per annual report, which
#: makes it double as the dated periodic-filing index the peer universe reads.
ANNUAL_FORMS = frozenset({
    "10-K", "10-K/A", "10-K405", "10-K405/A", "10-KSB", "10-KSB/A",
    "10-KT", "10-KT/A",
})
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A", "10-QT", "10-QT/A"})
PERIODIC_FORMS = ANNUAL_FORMS | QUARTERLY_FORMS

#: Foreign private issuers are counted and reported but are NOT periodic filers
#: for this store: they file `ifrs-full` in local currency, and §5 of the data
#: map excludes ADRs rather than pretending to handle them.
FOREIGN_PERIODIC_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A"})

#: Item 3.01 -- "Notice of Delisting or Failure to Satisfy a Continued Listing
#: Rule or Transfer of Listing". The last clause is why this can never date an
#: exit on its own: Berkshire has filed two and is not delisted.
ITEM_DELISTING_NOTICE = "3.01"

EXIT_SOURCE_8K = "8k_item_301"
EXIT_SOURCE_LAST_BAR = "last_volume_bar"
EXIT_SOURCE_FORM_25 = "form_25nse"

EXIT_TYPE_MERGER = "merger"
EXIT_TYPE_EXCHANGE_DELISTING = "exchange_delisting"
EXIT_TYPE_ISSUER_WITHDRAWAL = "issuer_withdrawal"

#: How far before a qualifying Form 25 an Item 3.01 notice is still the same
#: event. The notice precedes the form by weeks (BBBY: 2023-04-25 -> 2023-07-10;
#: Sears: 2018-10-15 -> 2018-11-15); a year of slack covers a drawn-out hearing
#: while still refusing an unrelated listing-transfer notice from years earlier.
EXIT_8K_LOOKBACK_DAYS = 400

#: For an issuer STILL FILING periodic reports, a Form 25 older than this
#: relative to its last report removed some other security, not the company --
#: a delisted second share class, say. It is deliberately not applied to an
#: issuer that has stopped reporting: Motors Liquidation kept filing 10-Qs until
#: 2021-02-12, twelve years after old GM's common stock was removed, and a floor
#: measured from that would throw the real Form 25 away.
EXIT_FORM25_GRACE_DAYS = 400

#: An issuer that has not filed a periodic report in this long has stopped
#: reporting. Used only to decide whether a MISSING exit record means "still
#: listed" (Apple) or "ended, undatable" (Enron). Never a replay input.
DORMANT_AFTER_DAYS = 400

#: Cap on Form 25 documents fetched per issuer. Lehman has 51.
MAX_FORM25_DOCS = 80

COMMON_STOCK_PREFIX = "common stock"

#: Words that make a description something other than the common equity line.
#: The approved list, plus the plural of each singular in it -- word-boundary
#: matching would otherwise let "Senior Notes"/"Warrants"/"Units" through.
NON_COMMON_WORDS = frozenset({
    "depositary", "preferred", "note", "notes", "right", "rights",
    "warrant", "warrants", "unit", "units", "debenture", "debentures",
    "trust", "trusts", "series", "due", "maturing", "linked",
})

# --------------------------------------------------------------------------
# Listing resolution
# --------------------------------------------------------------------------

CONFIDENCE_PROVED = "proved"
CONFIDENCE_INFERRED = "inferred"
CONFIDENCE_UNVERIFIED = "unverified"

EXPECTED_INSTRUMENT_TYPE = "EQUITY"

#: Venue names that mean "not a national exchange". A CIK whose common stock
#: has fallen to the pink sheets is a different tradeable proposition from the
#: listed line the score was built on.
OTC_EXCHANGE_MARKERS = ("otc", "pink", "other otc", "otcmkts", "otcqb", "otcqx")

#: Probe ranges for the meta-only chart call, cheapest first. A historical
#: window cannot be used for the gate: Yahoo answers HTTP 400 with no `meta` at
#: all when the symbol has no bars in the requested window, which is exactly the
#: reused-ticker case the gate has to catch.
META_PROBE_RANGES = ("5d", "1mo")

REJECT_NO_YAHOO_DATA = "no_yahoo_data"
REJECT_NO_META = "no_meta"
REJECT_NOT_DAILY = "not_daily_granularity"
REJECT_NO_FIRST_TRADE = "no_first_trade_date"
REJECT_FIRST_TRADE_AFTER = "first_trade_after_as_of"
REJECT_INSTRUMENT_TYPE = "instrument_type_mismatch"
REJECT_OTC = "otc_where_national_expected"
ACCEPT_REASON = "first_trade_date_gate_passed"

#: Default staleness bound for peer membership: one annual report plus slack for
#: a late filer. It is what makes the annual cadence of `pit_entity_sic` a sound
#: membership index.
DEFAULT_PEER_MAX_FILING_AGE_DAYS = 400

#: A name is only "priced" at an as-of date if a real bar sits close behind it.
DEFAULT_PRICE_MAX_AGE_DAYS = 10

#: The survivorship gate cohort. Every one of these is absent from today's live
#: universe, and the store must be able to hold all of them.
SURVIVORSHIP_CIKS: dict[str, str] = {
    "Lehman Brothers Holdings": "806085",
    "SVB Financial Group": "719739",
    "Sears Holdings": "1310067",
    "Bed Bath & Beyond": "886158",
    "Enron": "1024401",
    "Motors Liquidation (old GM)": "40730",
    "General Motors Co (new GM)": "1467858",
    "Berkshire Hathaway": "1067983",
    "First Republic Bank": "1132979",
}


# --------------------------------------------------------------------------
# Small date and text helpers
# --------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return _dt.date.today().isoformat()


def _day(value: Any) -> Optional[str]:
    """A 'YYYY-MM-DD' day from an ISO date, an ISO timestamp, or None."""
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


def _shift_days(day: str, days: int) -> str:
    return (_dt.date.fromisoformat(day[:10]) + _dt.timedelta(days=days)).isoformat()


def _days_between(start: str, end: str) -> int:
    """Calendar days from `start` to `end`; a huge number if either is unusable.

    The sentinel makes an unparseable date read as "impossibly stale", so a bad
    date can never be mistaken for a fresh one.
    """
    a, b = _day(start), _day(end)
    if a is None or b is None:
        return 10 ** 6
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days


def cik10(cik: Any) -> str:
    """The zero-padded 10-digit CIK, matching `pit_store.upsert_entity`.

    Both spellings reach this module -- '40730' from a config file, '0000040730'
    from an EDGAR document -- and a store that held both would silently split one
    issuer in two.
    """
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError(f"not a CIK: {cik!r}")
    return str(int(digits)).zfill(10)


def _unescape(text: str) -> str:
    return (text.replace("&amp;", "&").replace("&quot;", '"')
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))


def is_common_stock_class(description: Optional[str]) -> bool:
    """Is this Form 25 `descriptionClassSecurity` the common equity line?

    A Form 25 is filed PER SECURITY. Berkshire has six and is not delisted --
    every one of them names a senior note. Without this filter the earliest
    Form 25 on an issuer's file dates its "exit" years early: Lehman's is
    2002-01-07, and it removed a structured note.

    Three accepting shapes, in the order they are tested:
      * exactly "common stock";
      * starting with it -- "Common Stock, $0.01 par value", and SVB's
        "Common stock and preferred stock", which really did remove the common;
      * containing it while containing none of NON_COMMON_WORDS, which throws
        out Lehman's 2008-10-21 filing whose description contains the phrase
        "Common Stock of General Electric Company" inside a list of notes.

    Known limit, recorded rather than patched around: "Common Stock Purchase
    Warrants" is accepted by the second shape. No such case appears in the
    survivorship cohort, and the matched text is written to
    `pit_entity_exit.notes` so a human can audit every accepted description.
    """
    text = " ".join(_unescape(description or "").lower().split())
    if not text:
        return False
    if text.startswith(COMMON_STOCK_PREFIX):
        return True
    if COMMON_STOCK_PREFIX not in text:
        return False
    words = set(re.findall(r"[a-z]+", text))
    return not (words & NON_COMMON_WORDS)


def _exit_type_for(rule_provision: Optional[str]) -> Optional[str]:
    """Map Rule 12d2-2's subsection onto the schema's exit_type vocabulary."""
    text = (rule_provision or "").lower()
    if "(a)(3)" in text:
        return EXIT_TYPE_MERGER          # the security itself ceased to exist
    if "(b)" in text:
        return EXIT_TYPE_EXCHANGE_DELISTING
    if "(c)" in text:
        return EXIT_TYPE_ISSUER_WITHDRAWAL
    return None


# --------------------------------------------------------------------------
# HTTP: throttled, cached, and never raising for an ordinary miss
# --------------------------------------------------------------------------

_sec_lock = threading.Lock()
_sec_last = [0.0]
_yahoo_lock = threading.Lock()
_yahoo_last = [0.0]
_session_lock = threading.Lock()
_sec_session: Optional[requests.Session] = None
_yahoo_session: Optional[requests.Session] = None


def _sec_http() -> requests.Session:
    global _sec_session
    with _session_lock:
        if _sec_session is None:
            _sec_session = requests.Session()
            _sec_session.headers.update({
                "User-Agent": SEC_USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json, text/plain, */*",
            })
        return _sec_session


def _yahoo_http() -> requests.Session:
    """A browser UA is acceptable for Yahoo Finance, and only for Yahoo."""
    global _yahoo_session
    with _session_lock:
        if _yahoo_session is None:
            _yahoo_session = requests.Session()
            _yahoo_session.headers.update({
                "User-Agent": BROWSER_UA,
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            })
        return _yahoo_session


def _throttle(lock: threading.Lock, last: list, interval: float) -> None:
    with lock:
        wait = interval - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()


def _cache_file(cache_dir: str, url: str) -> str:
    return os.path.join(cache_dir, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")


def _cache_read(cache_dir: Optional[str], url: str) -> Optional[dict]:
    if not cache_dir:
        return None
    path = _cache_file(cache_dir, url)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _cache_write(cache_dir: Optional[str], url: str, payload: dict) -> None:
    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_file(cache_dir, url), "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        pass


def fetch_sec(url: str, cache_dir: Optional[str] = None) -> dict:
    """GET one SEC document. Returns {'status', 'text'}; never raises for a 404.

    A 404 is a FACT about the issuer, not a failure -- `companyfacts` 404s for
    every pre-XBRL filer and for First Republic, which reported to a banking
    regulator under Exchange Act 12(i) and never filed a 10-K. Negative results
    are cached alongside positive ones so a re-run does not re-ask.
    """
    cached = _cache_read(cache_dir, url)
    if cached is not None:
        return cached
    _throttle(_sec_lock, _sec_last, SEC_MIN_INTERVAL)
    try:
        response = _sec_http().get(url, timeout=HTTP_TIMEOUT)
        payload = {"status": response.status_code,
                   "text": response.text if response.status_code == 200 else ""}
    except requests.RequestException as exc:
        return {"status": 0, "text": "", "error": str(exc)}
    _cache_write(cache_dir, url, payload)
    return payload


def fetch_sec_json(url: str, cache_dir: Optional[str] = None) -> tuple[int, Optional[dict]]:
    result = fetch_sec(url, cache_dir)
    if result["status"] != 200:
        return result["status"], None
    try:
        return 200, json.loads(result["text"])
    except ValueError:
        return 200, None


def probe_companyfacts(cik: Any, cache_dir: Optional[str] = None) -> int:
    """HTTP status of the XBRL companyfacts document, without downloading it.

    Streamed and closed after the status line: a live filer's companyfacts runs
    to megabytes and the only question here is whether the document exists.
    """
    url = COMPANYFACTS_URL.format(cik10=cik10(cik))
    cached = _cache_read(cache_dir, url)
    if cached is not None:
        return int(cached.get("status", 0))
    _throttle(_sec_lock, _sec_last, SEC_MIN_INTERVAL)
    try:
        response = _sec_http().get(url, timeout=HTTP_TIMEOUT, stream=True)
        status = response.status_code
        response.close()
    except requests.RequestException:
        return 0
    _cache_write(cache_dir, url, {"status": status, "text": ""})
    return status


def _yahoo_chart(symbol: str, params: dict, cache_dir: Optional[str] = None) -> tuple[int, Optional[dict]]:
    """One Yahoo chart call, failing over between hosts. Never raises."""
    key = f"yahoo:{symbol}:{sorted(params.items())}"
    cached = _cache_read(cache_dir, key)
    if cached is not None:
        return int(cached["status"]), (json.loads(cached["text"]) if cached["text"] else None)
    status, body = 0, None
    for host in YAHOO_HOSTS:
        _throttle(_yahoo_lock, _yahoo_last, YAHOO_MIN_INTERVAL)
        try:
            response = _yahoo_http().get(
                CHART_URL.format(host=host, symbol=symbol), params=params,
                timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            continue
        status = response.status_code
        if status == 200:
            try:
                body = response.json()
            except ValueError:
                body = None
            break
        if status in (400, 404):
            break                      # a definite answer about this symbol
    _cache_write(cache_dir, key, {"status": status,
                                  "text": json.dumps(body) if body else ""})
    return status, body


def chart_meta(symbol: str, cache_dir: Optional[str] = None) -> dict:
    """Yahoo's `meta` block for one symbol, with no historical window.

    Deliberately a RECENT-range probe. Asking for the as-of window instead
    returns HTTP 400 and no `meta` whenever the symbol has no bars then --
    precisely the reused-ticker case -- so the gate would have nothing to test.
    `range=max` is never used: it silently coarsens to monthly bars.

    Returns {'status', 'meta', 'granularity'}; meta is None when Yahoo has never
    heard of the symbol (LEHMQ and SIVBQ both 404).
    """
    for probe in META_PROBE_RANGES:
        status, body = _yahoo_chart(
            symbol, {"range": probe, "interval": "1d", "events": "div,split"},
            cache_dir)
        result = ((body or {}).get("chart") or {}).get("result") or []
        if status == 200 and result:
            meta = result[0].get("meta") or {}
            return {"status": status, "meta": meta,
                    "granularity": meta.get("dataGranularity")}
        if status == 404:
            return {"status": status, "meta": None, "granularity": None}
    return {"status": status, "meta": None, "granularity": None}


#: The Unix epoch as an aware instant. `_exchange_date` counts from here
#: rather than calling `datetime.fromtimestamp`, which is not portable for
#: timestamps before it -- see that function's docstring.
_UNIX_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def _exchange_date(epoch: Any, gmtoffset: Any) -> Optional[str]:
    """An epoch second as the exchange's own calendar day.

    Yahoo stamps the first trade in UTC; a 09:30 New York open is 14:30 UTC, so
    UTC and local agree for US venues, but the exchange's `gmtoffset` is
    applied anyway rather than assumed, so a venue whose local day differs from
    the UTC day is dated by its OWN calendar, not by London's.

    PRE-1970 LISTINGS ARE THE POINT. This used to read
    `datetime.fromtimestamp(seconds, timezone.utc)`, and on Windows the CRT
    behind that call rejects a sufficiently negative timestamp with
    OSError [Errno 22] -- so every issuer whose first trade predates 1970 blew
    up the caller. Yahoo dates Boeing, GE, IBM, Coca-Cola, Exxon, 3M and the
    rest of the pre-CRSP NYSE roll at epoch -252322200 (1962-01-02), i.e. the
    oldest and most useful names in the store, and one of them killed a price
    ingest outright. Adding `timedelta(seconds=epoch)` to a fixed origin is the
    portable route: pure `datetime` arithmetic, no platform clock, no C library.

    INVARIANT: for every epoch the old call could convert -- which is every
    non-negative one, and, measured on this machine, negatives no smaller than
    -43200 (-43201 raises) -- this returns the identical string, because
    `datetime.fromtimestamp(s, utc) == _UNIX_EPOCH + timedelta(seconds=s)`
    by definition for an integer `s`. Only the crashing range changes, and it
    changes from a crash to an answer. An epoch outside `datetime`'s own year
    range still raises, as it always did.

    Returns None when `epoch` is not a number; a non-numeric `gmtoffset` is
    treated as zero, because a missing offset is a missing refinement and not
    a reason to discard a known trade date.
    """
    try:
        seconds = int(epoch)
    except (TypeError, ValueError):
        return None
    try:
        offset = int(gmtoffset or 0)
    except (TypeError, ValueError):
        offset = 0
    moment = _UNIX_EPOCH + _dt.timedelta(seconds=seconds) + _dt.timedelta(seconds=offset)
    return moment.date().isoformat()


# --------------------------------------------------------------------------
# The submissions index
# --------------------------------------------------------------------------

def submission_index(cik: Any, cache_dir: Optional[str] = None) -> dict:
    """Every EDGAR filing for one CIK, with the company block kept separate.

    `filings.recent` holds the newest 1,000 filings; everything older lives in
    the files named by `filings.files[]`. Lehman has 6,341 filings across four
    documents and its Form 25 history starts in the oldest one, so an
    implementation that reads only `recent` would mis-date half the cohort.

    Returns {'status', 'company', 'rows'}, where `company` is the CURRENT-STATE
    block -- kept for the ingest report and never written to a dated table.
    """
    url = SUBMISSIONS_URL.format(cik10=cik10(cik))
    status, payload = fetch_sec_json(url, cache_dir)
    if status != 200 or not payload:
        return {"status": status, "company": {}, "rows": []}

    company = {key: payload.get(key) for key in
               ("name", "tickers", "exchanges", "sic", "sicDescription",
                "entityType", "ein", "stateOfIncorporation", "formerNames")}

    blocks = [payload.get("filings", {}).get("recent", {})]
    for page in payload.get("filings", {}).get("files", []) or []:
        name = page.get("name")
        if not name:
            continue
        page_status, page_payload = fetch_sec_json(
            SUBMISSIONS_PAGE_URL.format(name=name), cache_dir)
        if page_status == 200 and page_payload:
            blocks.append(page_payload)

    rows: list[dict] = []
    for block in blocks:
        forms = block.get("form") or []
        filed = block.get("filingDate") or []
        accns = block.get("accessionNumber") or []
        items = block.get("items") or []
        primary = block.get("primaryDocument") or []
        accepted = block.get("acceptanceDateTime") or []
        reported = block.get("reportDate") or []
        for index, form in enumerate(forms):
            rows.append({
                "form": form,
                "filed": _day(filed[index]) if index < len(filed) else None,
                "accn": accns[index] if index < len(accns) else "",
                "items": items[index] if index < len(items) else "",
                "primary_document": primary[index] if index < len(primary) else "",
                "accepted_at": accepted[index] if index < len(accepted) else None,
                "report_date": _day(reported[index]) if index < len(reported) else None,
            })
    rows = [row for row in rows if row["filed"] and row["accn"]]
    rows.sort(key=lambda row: (row["filed"], row["accn"]))
    return {"status": status, "company": company, "rows": rows}


def _periodic(rows: Sequence[dict]) -> list[dict]:
    return [row for row in rows if row["form"] in PERIODIC_FORMS]


def _annual(rows: Sequence[dict]) -> list[dict]:
    return [row for row in rows if row["form"] in ANNUAL_FORMS]


# --------------------------------------------------------------------------
# Historical SIC, read from each filing's own header
# --------------------------------------------------------------------------

_COMPANY_BLOCK = re.compile(r"<COMPANY-DATA>(.*?)</COMPANY-DATA>", re.S | re.I)
_HDR_CIK = re.compile(r"<CIK>\s*(\d+)", re.I)
_HDR_SIC = re.compile(r"<ASSIGNED-SIC>\s*(\d+)", re.I)


def filing_sic(cik: Any, accession: str, cache_dir: Optional[str] = None) -> Optional[str]:
    """The SIC EDGAR had assigned to this issuer when this filing was accepted.

    Read from the ~800-byte `.hdr.sgml` that accompanies every accession back to
    1994. The submissions API's company-level `sic` is a CURRENT field and is
    wrong for 13.6% of historical rows -- Enron's header says 5172 in 1997 and
    6211 in 2001, while the current record says 6200.

    The header can carry several COMPANY-DATA blocks (FILER, SUBJECT-COMPANY,
    FILED-BY); a Form 25's filer is the exchange, so the block is matched on our
    own CIK and any other issuer's SIC is ignored.
    """
    padded = cik10(cik)
    url = (FILING_DIR_URL.format(cik=int(padded), accn_nodash=accession.replace("-", ""))
           + f"/{accession}.hdr.sgml")
    result = fetch_sec(url, cache_dir)
    if result["status"] != 200:
        return None
    for block in _COMPANY_BLOCK.findall(result["text"]):
        block_cik = _HDR_CIK.search(block)
        block_sic = _HDR_SIC.search(block)
        if block_cik and block_sic and int(block_cik.group(1)) == int(padded):
            return block_sic.group(1)
    return None


# --------------------------------------------------------------------------
# Form 25 security class
# --------------------------------------------------------------------------

def form_25_security(cik: Any, accession: str,
                     cache_dir: Optional[str] = None) -> dict:
    """The security class and rule provision of one Form 25 / 25-NSE.

    Returns {'description', 'rule_provision', 'common', 'status'}. `common` is
    False whenever the document cannot be read, which is the conservative
    direction: Lehman's twenty pre-2006 Form 25s are paper filings under
    `9999999997-*` accessions with no `primary_doc.xml`, and an unreadable form
    must never be allowed to invent an exit date.
    """
    url = (FILING_DIR_URL.format(cik=int(cik10(cik)), accn_nodash=accession.replace("-", ""))
           + "/primary_doc.xml")
    result = fetch_sec(url, cache_dir)
    out = {"description": None, "rule_provision": None, "common": False,
           "status": result["status"]}
    if result["status"] != 200 or not result["text"].strip():
        return out

    description = rule = None
    try:
        root = _ET.fromstring(result["text"])
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "descriptionClassSecurity" and node.text:
                description = node.text
            elif tag == "ruleProvision" and node.text:
                rule = node.text
    except _ET.ParseError:
        found = re.search(r"<descriptionClassSecurity>(.*?)</descriptionClassSecurity>",
                          result["text"], re.S)
        description = found.group(1) if found else None
        found = re.search(r"<ruleProvision>(.*?)</ruleProvision>", result["text"], re.S)
        rule = found.group(1) if found else None

    out["description"] = " ".join(_unescape(description).split()) if description else None
    out["rule_provision"] = " ".join(rule.split()) if rule else None
    out["common"] = is_common_stock_class(out["description"])
    return out


# --------------------------------------------------------------------------
# 1. Ingest
# --------------------------------------------------------------------------

def ingest_entity(conn: sqlite3.Connection, cik: Any, *,
                  cache_dir: Optional[str] = None,
                  sic_forms: Iterable[str] = ANNUAL_FORMS,
                  max_sic_filings: Optional[int] = None,
                  observed_on: Optional[str] = None) -> dict:
    """Register one issuer in the PIT store from its EDGAR submissions index.

    Writes, all dated and all idempotent:
      * `pit_entity`        -- the CIK and the span of its filing history;
      * `pit_entity_name`   -- every `formerNames` entry with its from/to dates,
                               plus the name in force now, dated from the last
                               former name's `to`;
      * `pit_entity_sic`    -- one row per annual report, carrying the SIC from
                               that filing's own header. This table doubles as
                               the dated periodic-filing index that
                               `peer_universe_as_of` reads, which is why it is
                               written from the filing list and not from a
                               company-level field.
      * `pit_ingest_run`    -- the full report, including everything deliberately
                               NOT written.

    What is deliberately NOT written anywhere: `tickers`, `exchanges`, `sic`,
    `name` and `entityType` as company-level fields. They are a current-state
    snapshot with no validity interval, and the whole point of this store is
    that a 2015 replay cannot see 2026's opinion of who a company is. BBBY's CIK
    is currently named "20230930-DK-Butterfly-1, Inc." with an empty `tickers`
    array; writing that as the issuer's identity would erase the retailer. They
    are kept in the report under `current_snapshot_hint`, which no selector
    reads, so a human can see what was discarded.

    `pit_entity.entity_kind` is left NULL for the same reason: `entityType` is a
    current field, and no dated substitute exists yet.

    Returns a report dict. `known_holes` is a list, never an exception: an
    issuer that never filed a periodic report (First Republic, CIK 1132979,
    which reported to a banking regulator under Exchange Act 12(i)) is a
    documented gap in the source, and the store records it rather than crashing
    or skipping silently.
    """
    padded = cik10(cik)
    observed_on = _day(observed_on) or _today()
    started = _now()
    index = submission_index(padded, cache_dir)
    rows = index["rows"]
    company = index["company"]

    report: dict[str, Any] = {
        "cik": padded,
        "submissions_status": index["status"],
        "observed_on": observed_on,
        "n_filings": len(rows),
        "known_holes": [],
        "errors": [],
    }

    if index["status"] != 200 or not rows:
        report["known_holes"].append("submissions_unavailable")
        report["errors"].append(f"submissions HTTP {index['status']}")
        ingest_id = pit_store.start_ingest(conn, SOURCE_SUBMISSIONS, f"CIK{padded}")
        pit_store.finish_ingest(conn, ingest_id, status="error", rows_read=0,
                                summary=report)
        report["entity_id"] = None
        report["ingest_id"] = ingest_id
        return report

    periodic = _periodic(rows)
    annual = _annual(rows)
    foreign = [row for row in rows if row["form"] in FOREIGN_PERIODIC_FORMS]

    entity_id = pit_store.upsert_entity(
        conn, padded,
        first_filing_date=rows[0]["filed"],
        last_filing_date=rows[-1]["filed"],
    )

    # ---- names, dated -------------------------------------------------
    former = company.get("formerNames") or []
    names_written = 0
    latest_former_to: Optional[str] = None
    for entry in former:
        name = (entry or {}).get("name")
        if not name:
            continue
        valid_from = _day(entry.get("from")) or rows[0]["filed"]
        valid_to = _day(entry.get("to"))
        pit_store.add_entity_name(conn, entity_id, name, valid_from, valid_to,
                                  SOURCE_SUBMISSIONS)
        names_written += 1
        if valid_to and (latest_former_to is None or valid_to > latest_former_to):
            latest_former_to = valid_to

    current_name = company.get("name")
    if current_name:
        pit_store.add_entity_name(
            conn, entity_id, current_name,
            latest_former_to or rows[0]["filed"], None,
            SOURCE_SUBMISSIONS_CURRENT)
        names_written += 1

    # ---- SIC per filing, from the filing's own header ------------------
    wanted = frozenset(sic_forms)
    sic_rows = [row for row in rows if row["form"] in wanted]
    if max_sic_filings is not None and len(sic_rows) > max_sic_filings:
        sic_rows = sic_rows[-max_sic_filings:]
    sic_written, sic_missing = 0, 0
    for row in sic_rows:
        sic = filing_sic(padded, row["accn"], cache_dir)
        if sic is None:
            sic_missing += 1
            continue
        pit_store.add_entity_sic(conn, entity_id, sic, row["filed"], row["accn"],
                                 SOURCE_FILING_HEADER)
        sic_written += 1

    # ---- holes, recorded explicitly ------------------------------------
    if not periodic:
        report["known_holes"].append("no_periodic_filings")
        report["companyfacts_status"] = probe_companyfacts(padded, cache_dir)
        if report["companyfacts_status"] == 404:
            report["known_holes"].append("companyfacts_404")
    if periodic and not annual:
        report["known_holes"].append("no_annual_report")
    if sic_rows and sic_written == 0:
        report["known_holes"].append("no_filing_header_sic")
    if foreign and not periodic:
        report["known_holes"].append("foreign_private_issuer_only")

    report.update({
        "entity_id": entity_id,
        "n_periodic_filings": len(periodic),
        "n_annual_filings": len(annual),
        "n_foreign_periodic_filings": len(foreign),
        "first_filing_date": rows[0]["filed"],
        "last_filing_date": rows[-1]["filed"],
        "first_periodic_filing": periodic[0]["filed"] if periodic else None,
        "last_periodic_filing": periodic[-1]["filed"] if periodic else None,
        "names_written": names_written,
        "sic_rows_written": sic_written,
        "sic_headers_missing": sic_missing,
        # Current-state only. Never written to a dated table; never read by a
        # replay. Present so the discarded snapshot is visible to a human.
        "current_snapshot_hint": {
            "name": company.get("name"),
            "tickers": company.get("tickers"),
            "exchanges": company.get("exchanges"),
            "sic": company.get("sic"),
            "entity_type": company.get("entityType"),
            "warning": "current snapshot; not valid for any historical as-of date",
        },
    })

    ingest_id = pit_store.start_ingest(conn, SOURCE_SUBMISSIONS, f"CIK{padded}")
    pit_store.finish_ingest(
        conn, ingest_id,
        status="complete" if not report["errors"] else "partial",
        rows_read=len(rows), rows_kept=names_written + sic_written,
        rows_rejected=sic_missing, summary=report)
    report["ingest_id"] = ingest_id
    report["started_at"] = started
    return report


def name_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str) -> Optional[str]:
    """The name this issuer carried on `as_of`, from the dated name history.

    Exists so a report can print what a company was CALLED then. It is never an
    identity: CIK 886158 is "20230930-DK-Butterfly-1, Inc." today and was "BED
    BATH & BEYOND INC" in 2015, and only the CIK connects the two.
    """
    row = conn.execute(
        """SELECT name FROM pit_entity_name
            WHERE entity_id = ?
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_to IS NULL OR valid_to >= ?)
            ORDER BY valid_from DESC LIMIT 1""",
        (entity_id, as_of, as_of),
    ).fetchone()
    return row["name"] if row else None


# --------------------------------------------------------------------------
# 2. Exit dating
# --------------------------------------------------------------------------

def _last_volume_bar(conn: sqlite3.Connection, entity_id: int) -> Optional[str]:
    row = conn.execute(
        """SELECT MAX(b.bar_date) AS last_bar
             FROM pit_price_bar b
             JOIN pit_listing l ON l.listing_id = b.listing_id
            WHERE l.entity_id = ? AND b.volume IS NOT NULL AND b.volume > 0""",
        (entity_id,),
    ).fetchone()
    return _day(row["last_bar"]) if row and row["last_bar"] else None


def exit_date_for(conn: sqlite3.Connection, cik: Any, *,
                  cache_dir: Optional[str] = None,
                  observed_on: Optional[str] = None,
                  persist: bool = True) -> dict:
    """The earliest defensible date this issuer's common equity stopped trading.

    Each of the three available sources is wrong on its own:

      * Form 25 / 25-NSE is filed PER SECURITY. Berkshire has six, every one a
        senior note, and Berkshire is not delisted. It also LAGS the last trade
        -- BBBY stopped trading around 2023-05-03 and its Form 25 was filed
        2023-07-10, 68 days later.
      * 8-K Item 3.01 also covers a TRANSFER of listing, so Berkshire has two of
        those as well. It cannot establish an exit; it can only date one.
      * The last bar with volume > 0 is only meaningful if the price file
        actually ran past that date, or the issuer is otherwise known to have
        ended. Otherwise every company "exits" on the last day of the pull.
      * Form 15 is DEREGISTRATION, not delisting, and can post years late
        (Lehman's is 2012, four years after the failure). It is recorded in
        `dereg_date` and is never allowed to be the exit date.

    So a COMMON-STOCK Form 25 is the qualifying event -- it is the only document
    that says this issuer's common equity line was removed from an exchange --
    and the other two sources may only pull the date EARLIER. Three guards keep
    a per-security form from dating a company's death:

      * the class filter (`is_common_stock_class`), which is also what discards
        Lehman's twenty pre-2006 paper Form 25s: they have no readable document,
        so they cannot qualify;
      * the LAST common-stock form wins, not the first, because an earlier one
        is by definition followed by more trading;
      * for an issuer still filing periodic reports, a form older than
        EXIT_FORM25_GRACE_DAYS relative to its last report is ignored. That
        floor is deliberately NOT applied once an issuer has gone quiet.

    When no qualifying form exists the issuer is not given a date. If it also
    stopped filing periodic reports long ago it is QUARANTINED with
    `no_exit_record = 1` -- Enron filed neither a Form 25 nor a Form 15, and a
    fabricated date would be worse than an admitted gap. If it is still filing,
    no exit row is written at all, which is how Berkshire and the current GM
    come through untouched.

    `observed_on` (default today) decides only whether silence means "still
    listed" or "ended, undatable". It is an ingest-time property and is never
    read by a replay selector.
    """
    padded = cik10(cik)
    entity_id = pit_store.entity_id_for_cik(conn, padded)
    if entity_id is None:
        raise ValueError(f"CIK {padded} is not in pit_entity; ingest_entity first")

    observed_on = _day(observed_on) or _today()
    rows = submission_index(padded, cache_dir)["rows"]
    periodic = _periodic(rows)
    last_periodic = periodic[-1]["filed"] if periodic else None
    dormant = (last_periodic is None
               or _days_between(last_periodic, observed_on) > DORMANT_AFTER_DAYS)

    result: dict[str, Any] = {
        "cik": padded, "entity_id": entity_id, "observed_on": observed_on,
        "exit_date": None, "exit_type": None, "exit_date_source": None,
        "exit_source_accession": None, "dereg_date": None, "no_exit_record": 0,
        "dormant": dormant, "last_periodic_filing": last_periodic,
        "candidates": {}, "form_25_examined": 0, "notes": None,
        "status": "no_exit_evidence",
    }

    # ---- Form 15: recorded, never an exit date -------------------------
    form_15 = [row for row in rows if row["form"].upper().startswith("15-")
               or row["form"].upper() == "15"]
    if form_15:
        result["dereg_date"] = form_15[0]["filed"]

    # ---- the qualifying common-stock Form 25 ---------------------------
    # Newest first, stopping at the first common-stock class: an issuer can
    # have several common-stock removals across its life (a transfer between
    # exchanges, a relisting), and only the LAST one ends the line -- an earlier
    # one is, by definition, followed by more trading.
    #
    # The recency floor is applied AFTER the class filter, never before it.
    # Filtering first would mean never reading the classes at all for a live
    # issuer -- Berkshire's six Form 25s would be discarded on their dates, and
    # the fact that every one of them is a senior note would go unrecorded.
    floor = (_shift_days(last_periodic, -EXIT_FORM25_GRACE_DAYS)
             if last_periodic and not dormant else None)
    form_25 = [row for row in rows if row["form"].upper().startswith("25")]

    qualifying: Optional[dict] = None
    stale_form: Optional[dict] = None
    for row in reversed(form_25):
        if result["form_25_examined"] >= MAX_FORM25_DOCS:
            result["status"] = "form_25_scan_truncated"
            break
        detail = form_25_security(padded, row["accn"], cache_dir)
        result["form_25_examined"] += 1
        if not detail["common"]:
            continue
        if floor is not None and row["filed"] < floor:
            # Common stock, but removed long before this issuer's latest
            # periodic report: a second share class, not the company.
            stale_form = {"filed": row["filed"], "accn": row["accn"], **detail}
            break
        qualifying = {"filed": row["filed"], "accn": row["accn"], **detail}
        break
    result["form_25_rejected_as_stale"] = (
        stale_form["filed"] if stale_form else None)

    # ---- Item 3.01 notices ---------------------------------------------
    notices = [(row["filed"], row["accn"]) for row in rows
               if row["form"].upper().startswith("8-K")
               and ITEM_DELISTING_NOTICE in (row["items"] or "")]

    candidates: dict[str, tuple[str, Optional[str]]] = {}
    if qualifying:
        candidates[EXIT_SOURCE_FORM_25] = (qualifying["filed"], qualifying["accn"])
        window_lo = _shift_days(qualifying["filed"], -EXIT_8K_LOOKBACK_DAYS)
        in_window = [item for item in notices
                     if window_lo <= item[0] <= qualifying["filed"]]
        if in_window:
            candidates[EXIT_SOURCE_8K] = min(in_window)
    elif dormant and notices:
        # No qualifying form, but the issuer went dark. The last delisting
        # notice before it stopped reporting is the best available evidence.
        candidates[EXIT_SOURCE_8K] = max(notices)

    if qualifying or dormant:
        last_bar = _last_volume_bar(conn, entity_id)
        if last_bar:
            candidates[EXIT_SOURCE_LAST_BAR] = (last_bar, None)

    result["candidates"] = {source: day for source, (day, _accn) in candidates.items()}

    notes_parts: list[str] = []
    if qualifying:
        notes_parts.append(
            f"form 25 {qualifying['filed']} [{qualifying['accn']}] "
            f"class={qualifying['description']!r} rule={qualifying['rule_provision']}")
    if stale_form:
        notes_parts.append(
            f"common-stock form 25 {stale_form['filed']} rejected as stale: "
            f"issuer still reporting at {last_periodic}")
    if notices:
        notes_parts.append("8-K item 3.01 filed "
                           + ", ".join(sorted({day for day, _ in notices})))
    if result["dereg_date"]:
        notes_parts.append(f"form 15 {result['dereg_date']} (deregistration, not delisting)")
    notes_parts.append(
        f"last periodic report {last_periodic}" if last_periodic
        else "KNOWN HOLE: no 10-K/10-Q ever filed "
             "(Exchange Act 12(i) bank reporting; companyfacts unavailable)")

    if candidates:
        source, (day, accn) = min(candidates.items(), key=lambda item: item[1][0])
        result.update({
            "exit_date": day, "exit_date_source": source,
            "exit_source_accession": accn,
            "exit_type": _exit_type_for(qualifying["rule_provision"]) if qualifying else None,
            "status": "dated",
        })
    elif dormant:
        result["no_exit_record"] = 1
        result["status"] = "quarantined_no_exit_record"
        notes_parts.append(
            "no common-stock Form 25 and no usable Item 3.01 notice: "
            "quarantined, not dated")

    result["notes"] = "; ".join(notes_parts)

    if persist and (result["exit_date"] or result["no_exit_record"]
                    or result["dereg_date"]):
        pit_store.set_entity_exit(
            conn, entity_id,
            exit_date=result["exit_date"], exit_type=result["exit_type"],
            exit_date_source=result["exit_date_source"],
            exit_source_accession=result["exit_source_accession"],
            dereg_date=result["dereg_date"],
            no_exit_record=result["no_exit_record"], notes=result["notes"])
    return result


# --------------------------------------------------------------------------
# 3. Listing resolution -- the first-trade-date gate
# --------------------------------------------------------------------------

def resolve_listing(conn: sqlite3.Connection, symbol: str, entity_id: int,
                    as_of: str, *,
                    cache_dir: Optional[str] = None,
                    expected_instrument_type: str = EXPECTED_INSTRUMENT_TYPE,
                    expect_national_exchange: bool = True,
                    proof: Optional[str] = None,
                    persist: bool = True) -> dict:
    """Decide whether `symbol` was this entity's tradeable line on `as_of`.

    The gate that matters is `meta.firstTradeDate`. A reused ticker answers HTTP
    200 with everything else looking right: BBBY today is EQUITY on NYSE with
    `longName` "Bed Bath & Beyond, Inc." -- the name AND the exchange both match
    the retailer that went bankrupt in 2023 -- and the only thing that gives it
    away is a first bar on 2026-07-17. A first trade date LATER than the as-of
    date means the symbol now belongs to a younger instrument, so the pull is
    refused rather than silently attributed to the dead issuer.

    Two corroborating checks, each of which catches a real case in the cohort:
    `instrumentType` (SHLD today is an ETF, "Global X Defense Tech ETF") and
    `fullExchangeName` (a common stock that has fallen to the pink sheets is not
    the listed line the score was built on).

    THE LIMIT THIS CANNOT CATCH, stated plainly: a ticker that migrated to an
    OLDER company. `T` before 2005 was AT&T Corp, not SBC; `META` before 2022
    was Meta Materials, not Facebook. In both cases `firstTradeDate` is EARLIER
    than the as-of date, so the gate passes and the wrong issuer's prices are
    accepted. Catching it needs a DATED ticker-to-CIK map, and no free one
    exists -- `company_tickers.json` is a current survivor snapshot and
    `companyconcept` for `dei/TradingSymbol` 404s. Until one exists the honest
    confidence for a passing symbol is `inferred`, never `proved`.

    `proof` is for a caller that HAS a dated attribution -- a `dei:TradingSymbol`
    read off the filed cover page, for instance. Passing one, and only then,
    raises the recorded confidence to `proved`.

    A rejected symbol is NOT written to `pit_listing`. A listing row asserts that
    this line was tradeable for this issuer, and the replay's job at a failed
    gate is to find no listing rather than to filter a bad one out. The reason is
    returned to the caller and belongs in the ingest report.
    """
    as_of_day = _day(as_of)
    if as_of_day is None:
        raise ValueError(f"as_of must be a date: {as_of!r}")
    symbol = (symbol or "").strip().upper()

    result: dict[str, Any] = {
        "symbol": symbol, "entity_id": entity_id, "as_of": as_of_day,
        "accepted": False, "listing_id": None,
        "confidence": CONFIDENCE_UNVERIFIED, "reason": REJECT_NO_YAHOO_DATA,
        "first_trade_date": None, "instrument_type": None, "exchange": None,
        "currency": None, "long_name": None, "http_status": None,
        "granularity": None,
    }
    if not symbol:
        return result

    probe = chart_meta(symbol, cache_dir)
    result["http_status"] = probe["status"]
    result["granularity"] = probe["granularity"]
    meta = probe["meta"]
    if not meta:
        result["reason"] = (REJECT_NO_YAHOO_DATA if probe["status"] in (0, 404)
                            else REJECT_NO_META)
        return result

    # Daily bars, asserted. A coarsened series would make every date comparison
    # here meaningless.
    if probe["granularity"] != "1d":
        result["reason"] = REJECT_NOT_DAILY
        return result

    first_trade = _exchange_date(meta.get("firstTradeDate"), meta.get("gmtoffset"))
    result.update({
        "first_trade_date": first_trade,
        "instrument_type": meta.get("instrumentType"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "long_name": meta.get("longName") or meta.get("shortName"),
    })

    if first_trade is None:
        result["reason"] = REJECT_NO_FIRST_TRADE
        return result
    if first_trade > as_of_day:
        result["reason"] = REJECT_FIRST_TRADE_AFTER
        return result
    if expected_instrument_type and (result["instrument_type"] or "").upper() != \
            expected_instrument_type.upper():
        result["reason"] = REJECT_INSTRUMENT_TYPE
        return result
    venue = (result["exchange"] or "").lower()
    if expect_national_exchange and any(marker in venue for marker in OTC_EXCHANGE_MARKERS):
        result["reason"] = REJECT_OTC
        return result

    result["accepted"] = True
    result["reason"] = ACCEPT_REASON
    result["confidence"] = CONFIDENCE_PROVED if proof else CONFIDENCE_INFERRED
    if proof:
        result["proof"] = proof

    if persist:
        exit_row = conn.execute(
            "SELECT exit_date FROM pit_entity_exit WHERE entity_id = ?",
            (entity_id,)).fetchone()
        result["listing_id"] = pit_store.upsert_listing(
            conn, symbol, first_trade, entity_id=entity_id,
            exchange=result["exchange"], instrument_type=result["instrument_type"],
            currency=result["currency"], valid_from=first_trade,
            valid_to=(_day(exit_row["exit_date"]) if exit_row else None),
            confidence=result["confidence"], source=SOURCE_YAHOO_META)
    return result


# --------------------------------------------------------------------------
# 4-5. The two universes
# --------------------------------------------------------------------------

#: Shared membership joins. `pit_entity_sic` carries one row per annual report,
#: so joining it IS the "filed a periodic report by as_of" test, and the
#: staleness floor in the WHERE clause is what stops a company that filed in
#: 2011 and went dark from counting as a 2015 peer.
_PEER_JOINS = """
      FROM pit_entity e
      JOIN pit_entity_sic s ON s.entity_id = e.entity_id
 LEFT JOIN pit_entity_exit x ON x.entity_id = e.entity_id
"""

_PEER_WHERE = """
     WHERE s.filed <= ? AND s.filed >= ?
       AND (x.exit_date IS NULL OR x.exit_date >= ?)
"""

#: The classification in force at the as-of date. Mirrors `pit_store.sic_as_of`
#: exactly, including the accession tie-break -- the two must not drift, and
#: test_pit_identity asserts they agree row for row.
_SIC_AS_OF = """
    (SELECT s2.sic FROM pit_entity_sic s2
      WHERE s2.entity_id = e.entity_id AND s2.filed <= ?
      ORDER BY s2.filed DESC, s2.source_accession DESC LIMIT 1)
"""


def peer_universe_as_of(conn: sqlite3.Connection, as_of: str,
                        max_filing_age_days: int = DEFAULT_PEER_MAX_FILING_AGE_DAYS
                        ) -> list[sqlite3.Row]:
    """Every issuer that was a live reporting company on `as_of`. CIK-keyed.

    Survivorship-free by construction, and no ticker is required. Six of the
    seven equity sub-factors need no price at all, so this -- not the priced set
    -- is the cohort behind the growth, profitability and debt percentiles. A
    peer set restricted to names that still trade today would shift every
    percentile toward the survivors and would do it without raising an error.

    Membership is two conditions and nothing else:
      * an annual report filed at or before `as_of` and no more than
        `max_filing_age_days` before it. The default of 400 days is one annual
        cadence plus slack for a late filer; it is what stops SVB, whose
        NetIncomeLoss stops being filed in 2012, from counting as a 2019 peer.
      * not exited before `as_of`.

    A QUARANTINED entity (`no_exit_record = 1`) stays IN. Enron cannot be dated,
    but at 2001-06-30 it was unambiguously a live reporting company, and
    dropping every undatable failure is exactly the bias this store exists to
    remove. The staleness bound retires it naturally once it stops filing.
    Quarantine bites in `scored_universe_as_of`, where investability is decided.

    Returns rows of (entity_id, cik, sic, last_annual_filed, n_annual_filings).
    """
    as_of_day = _day(as_of)
    if as_of_day is None:
        raise ValueError(f"as_of must be a date: {as_of!r}")
    floor = _shift_days(as_of_day, -int(max_filing_age_days))
    return conn.execute(
        f"""SELECT e.entity_id, e.cik, {_SIC_AS_OF} AS sic,
                   MAX(s.filed) AS last_annual_filed,
                   COUNT(*) AS n_annual_filings
            {_PEER_JOINS}
            {_PEER_WHERE}
            GROUP BY e.entity_id, e.cik
            ORDER BY e.cik""",
        (as_of_day, as_of_day, floor, as_of_day),
    ).fetchall()


def scored_universe_as_of(conn: sqlite3.Connection, as_of: str, *,
                          max_filing_age_days: int = DEFAULT_PEER_MAX_FILING_AGE_DAYS,
                          max_price_age_days: int = DEFAULT_PRICE_MAX_AGE_DAYS,
                          allowed_confidence: Sequence[str] = (CONFIDENCE_PROVED,
                                                              CONFIDENCE_INFERRED),
                          ) -> list[sqlite3.Row]:
    """The peer subset that can actually be scored, labelled and held.

    Smaller than the peer universe by construction, and that gap is a reported
    number rather than a defect. A scored name needs everything a peer needs,
    plus three things a peer does not:

      * a `pit_listing` whose first trade date is at or before `as_of` and whose
        validity interval covers it -- the `firstTradeDate` gate, persisted;
      * a real bar with volume > 0 within `max_price_age_days` of `as_of`, so a
        halted or already-dead line is not priced from a stale close;
      * no quarantine. An entity whose end cannot be dated may inform a
        percentile, but it must never be given a position, because its forward
        label cannot be honestly resolved.

    Rows carry listing_id as well as entity_id, because a label and a price
    belong to the LINE and a fundamental belongs to the ISSUER.
    """
    as_of_day = _day(as_of)
    if as_of_day is None:
        raise ValueError(f"as_of must be a date: {as_of!r}")
    floor = _shift_days(as_of_day, -int(max_filing_age_days))
    price_floor = _shift_days(as_of_day, -int(max_price_age_days))
    confidences = tuple(allowed_confidence) or (CONFIDENCE_PROVED,)
    placeholders = ", ".join("?" * len(confidences))
    return conn.execute(
        f"""SELECT e.entity_id, e.cik, l.listing_id, l.symbol, l.exchange,
                   l.first_trade_date, l.confidence,
                   {_SIC_AS_OF} AS sic,
                   MAX(s.filed) AS last_annual_filed
            {_PEER_JOINS}
            JOIN pit_listing l ON l.entity_id = e.entity_id
                 AND l.confidence IN ({placeholders})
                 AND l.valid_from <= ?
                 AND (l.valid_to IS NULL OR l.valid_to >= ?)
                 AND (l.first_trade_date IS NULL OR l.first_trade_date <= ?)
            {_PEER_WHERE}
              AND COALESCE(x.no_exit_record, 0) = 0
              AND EXISTS (SELECT 1 FROM pit_price_bar b
                           WHERE b.listing_id = l.listing_id
                             AND b.bar_date <= ? AND b.bar_date >= ?
                             AND b.volume IS NOT NULL AND b.volume > 0)
            GROUP BY e.entity_id, e.cik, l.listing_id
            ORDER BY e.cik, l.symbol""",
        (as_of_day,) + confidences
        + (as_of_day, as_of_day, as_of_day,          # listing validity window
           as_of_day, floor, as_of_day,              # _PEER_WHERE
           as_of_day, price_floor),                  # price recency
    ).fetchall()


# --------------------------------------------------------------------------
# CLI -- seeds the real PIT store
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Ingest CIKs into the PIT store and date their exits.

        python pit_identity.py --cohort
        python pit_identity.py 806085 1067983 --db /tmp/scratch.db

    Defaults to `pit_store.DEFAULT_PIT_DB_PATH`. Idempotent: every write below
    is an upsert keyed on the CIK or the accession.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    cache_dir = None
    ciks: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--db" and index + 1 < len(argv):
            db_path = argv[index + 1]; index += 2
        elif token == "--cache" and index + 1 < len(argv):
            cache_dir = argv[index + 1]; index += 2
        elif token == "--cohort":
            ciks.extend(SURVIVORSHIP_CIKS.values()); index += 1
        else:
            ciks.append(token); index += 1
    if not ciks:
        print(__doc__)
        print("usage: python pit_identity.py [--cohort] [CIK ...] "
              "[--db PATH] [--cache DIR]")
        return 2

    conn = pit_store.init_db(db_path)
    print(f"PIT store: {db_path}")
    for cik in ciks:
        report = ingest_entity(conn, cik, cache_dir=cache_dir)
        if report.get("entity_id") is None:
            print(f"  CIK {report['cik']}  UNAVAILABLE  {report['errors']}")
            continue
        exit_row = exit_date_for(conn, cik, cache_dir=cache_dir)
        print(f"  CIK {report['cik']}  entity_id={report['entity_id']}  "
              f"filings={report['n_filings']}  periodic={report['n_periodic_filings']}  "
              f"sic_rows={report['sic_rows_written']}  "
              f"exit={exit_row['exit_date']} ({exit_row['exit_date_source']}) "
              f"quarantined={exit_row['no_exit_record']}  "
              f"holes={report['known_holes'] or '-'}")
    stats = pit_store.store_stats(conn)
    print("  store:", {k: v for k, v in stats.items() if k.startswith("pit_entity")
                       or k == "pit_listing"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
