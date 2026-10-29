"""Single-name CDS reference entities and their SEC fundamentals (CDS_SINGLE_NAME_PLAN rev 2, release 1.0.7, package B2; contract C-ENT).

Bound as ``services.credit_entities`` (``server.OPTIONAL_MODULES``, hook H1-5).  It answers two questions for the single-name CDS desk:
which US-listed companies can have a CDS, and what each one owes - from SEC EDGAR only, every number with its XBRL tag, its balance-sheet
date and the day the terminal first knew it.  Nothing is invented: a company the rules cannot cover is "not offered" with its reason in
words, never given a default.  DTCC data is never used (plan section 1).

THE UNIVERSE (plan 4.1)
- SEC ``company_tickers_exchange.json`` (CIK, name, ticker, exchange).  Exchanges kept: {NYSE, Nasdaq, CBOE, TXSE}, compared without case;
  OTC and null are dropped; any other string is dropped too and counted once per refresh (``unknown_exchanges``), so a new exchange name
  is seen, never silently kept or lost.
- One primary ticker per CIK: the ticker that is in the instrument master; else the first common-stock-looking ticker in the file's order
  (no -P / -W / -U / -R suffix, no Nasdaq fifth-letter W / U / R / P); the other tickers of the CIK go in ``tickers_json``.  (The
  research's title match picked JPMorgan as AMJB, an ETN listed under its CIK: the master rule picks JPM.)
- A CIK with no common-stock-looking ticker and none in the master (only preferred / warrant / unit / right symbols) has no listed common
  stock: it stays in the list (searchable, its first symbol shown) but is NOT_OFFERED with reason NO_LISTED_COMMON, because the model needs
  the share price of listed common stock (the live check of 2026-10-29 found 9 such names offered on a preferred's price: CMS-PB, SCE-PG,
  SNUS-PH, TRTN-PA, AHL-PD, ANG-PD, CDR-PB, CFTR-PA, PHXE-P).

FUNDAMENTALS (plan 3.2), from XBRL facts without a dimension
- WHICH BALANCE SHEET: ``end`` = the latest ``end`` among the facts of the debt-recipe tags (R1-R6); every component comes from facts of
  that same ``end``; older tags are ignored (JPMorgan's 2014 ``LongTermDebt`` never counts).  Other balance-sheet facts never set it.
- RECIPES (``compute_debt``): R1-R4 apply when their first tag is present and above 0; otherwise R5a, R5b and R6 are all tried and the
  LARGEST with debt above 0 wins (a tie keeps the earlier one; integration B2-R3: the plan's "first above 0" took a filer's SecuredDebt alone
  when its unsecured notes sit under R5b or R6 tags - Realty Income 37,085,000 against 25,091,588,000):
    R1  DebtLongtermAndShorttermCombinedAmount
    R2  LongTermDebt + STB + CP
    R3  LongTermDebtNoncurrent + (DebtCurrent if present, else LongTermDebtCurrent + STB + CP)
    R4a LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities + STB + CP
    R4b LongTermDebtAndCapitalLeaseObligations + (LongTermDebtAndCapitalLeaseObligationsCurrent, else LongTermDebtCurrent, else
        DebtCurrent) + STB + CP
    R5a SecuredDebt + UnsecuredDebt
    R5b NotesPayable (with SeniorNotes / ConvertibleNotesPayable under the parts rule; both summed when NotesPayable is absent)
        + LinesOfCreditCurrent + LongTermLineOfCredit + OtherLongTermDebtNoncurrent
    R6  the larger of NotesAndLoansPayable, LoansPayable and NC + CUR; NC = the largest of LongTermNotesAndLoans, LongTermNotesPayable,
        SeniorLongTermNotes, LongTermLoansPayable, OtherLongTermDebt and SecuredLongTermDebt + UnsecuredLongTermDebt (alternative names
        of one total); CUR = NotesPayableCurrent, else DebtCurrent, else LongTermDebtCurrent + STB + CP
  "STB + CP" = ShortTermBorrowings + CommercialPaper under the PARTS RULE: a part counts only when it is larger than its whole (then it
  cannot be inside it); otherwise the whole is used alone and the part is named as left out ("CommercialPaper 6,100,000,000 taken as
  part of ShortTermBorrowings 10,602,000,000").  Deposits, repos and policy liabilities are never debt.
- A sum above ``Liabilities`` of the same ``end`` is rejected (DEBT_GT_LIABILITIES).
- A JOINT FILING'S FIGURES ARE ITS PARENT'S: the SEC frames API lists the non-dimensional facts of a parent-and-subsidiaries 10-Q / 10-K
  (the parent's consolidated figures, ``entityName`` the parent's) under ONE co-registrant's CIK, often a subsidiary's (2026-10-29: Entergy
  Corp's 2026-06-30 10-Q under Entergy Arkansas, EAI; Edison International's under Southern California Edison; CMS Energy's 2026-09-30 under
  Consumers Energy).  A frames fact whose accession was submitted by ANOTHER listed registrant and whose ``entityName`` is that registrant's
  name, not this CIK's own, is never taken as this CIK's fact (``other_registrant``); the run counts them.  (The parent keeps its own older
  balance sheet, labelled with its date: plan 12.5.)
- SHARES: dei EntityCommonStockSharesOutstanding (the cover page, the latest) -> us-gaap CommonStockSharesOutstanding (the same end) ->
  us-gaap WeightedAverageNumberOfDilutedSharesOutstanding (the latest quarter, labelled "diluted weighted average"); else NO_SHARES.
  ``split_adjusted_shares`` multiplies by the splits dated after the share count's date (the model applies them, plan 3.2).
- TWO ROWS OF ONE END (an amendment, a later filing repeating the balance sheet): the latest ``filed`` when both are known (companyfacts),
  else the latest ``known_from``, then the higher ``accn``.
- POINT IN TIME: every row carries ``known_from`` = the New York date the terminal first saw that (cik, end, accn);
  ``fundamentals_on(conn, cik, d)`` answers with what was known on d.

FLAGS (plan 3.5): FINANCIAL (banks, insurers, finance companies) and REIT.  Until a name's SIC code is checked (``refresh_entity``, from
``submissions``) the flag comes from the filing tags of the balance sheet, labelled "from filing tags; SIC not checked yet":
``Deposits`` > 0, or one of the five insurer tags > 0 -> FINANCIAL; ``RealEstateInvestmentPropertyNet`` >= 50 % of ``Assets`` -> REIT.
Once checked: SIC 6000-6499 -> FINANCIAL, SIC 6798 (or the asset test) -> REIT.  A financial name is offered LIMITED.

STATUS (plan 3.5, 4.4; ``derive``): OFFERED, or NOT_OFFERED with a reason - NOT_LISTED, NO_LISTED_COMMON (only preferred / warrant /
unit / right symbols listed), FOREIGN_FILER (latest periodic form 20-F / 40-F,
an IFRS-only filer, or an ADR in the master), NO_DEBT, DEBT_GT_LIABILITIES, NO_SHARES, STALE_FUNDAMENTALS (balance sheet or share count
more than 465 days old), BELOW_MIN_DEBT (under USD 100,000,000, assumed).  The status depends on today's date, so every reader derives it
again with its own date; the stored column is the value at the last write.

REFRESH (plan 4.3)
- Refresher ``cds_fundamentals`` (quote worker, after 06:30 ET; its first run at the worker's first tick): the current and the previous
  calendar quarter of XBRL frames for 37 debt / liability / flag tags and 3 share tags (about 80 requests); the first run (no universe yet)
  and Monday runs also fetch the tickers file and the last 5 quarters (about 201 requests, about 22 MB, 2-3 minutes).  A run already
  done today (after a restart) is not repeated.
- On demand (``refresh_entity``, the CDS model's input queue on a worker thread): ``submissions`` (SIC, the latest periodic form) and,
  when a newer 10-Q / 10-K than the stored balance sheet exists, ``companyfacts`` (over the 16 MB cap: the frames row stays, labelled).
- SEC requests: ``quotes.http.get_with_policy`` (the GOV user agent ``config.GOV_UA`` by host), this module's own pacer of 4 requests a
  second at most (one every 0.25 s, no burst; timed on ``time.perf_counter``, and each start at least 0.25 s after the previous ACTUAL
  start, so a late OS wake-up never brings two requests closer), provider health under ``sec`` (``allow`` checked first, every answer
  recorded).
- Writes go through two light commands with request id ``<prefix>:<sha256(payload)[:12]>`` (a replay is a no-op), at most 500 rows
  each, changed rows only: ``credit.fundamentals_upsert`` then ``credit.entities_upsert`` (the last chunk carries the run record).
- Offline (``--offline``, the tests): the refresher returns at once without a network call; tests pass a ``getter`` that serves fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time as _time
from collections import namedtuple
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import config
from .clock import epoch_of, ny_of
from .commands import Command, RejectError
from .db import canonical_json, get_meta
from .marks import shared_reader
from .quotes.http import FetchError, get_with_policy

SOURCE = "credit_entities"
PROVIDER = "sec"
SEC_RPS = 4                                     # SEC asks for 10 a second at most; this module keeps to 4
MIN_DEBT_C = 100_000_000_00                     # USD 100M (assumed, owner question Q8)
STALE_DAYS, AGE_WARN_DAYS = 465, 183            # 15 months refuses; 6-15 months is offered with an amber age note (plan 4.4)
CHECK_DAYS = 7                                  # a name's submissions check is redone after 7 days (plan 4.3)
CHUNK = 500                                     # rows per light command (plan 6.2)
FULL_QUARTERS, DAILY_QUARTERS = 5, 2
REFRESH_ET = "06:30"
REFRESHER = "cds_fundamentals"
UNIVERSE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
EXCHANGES = {"nyse": "NYSE", "nasdaq": "Nasdaq", "cboe": "CBOE", "txse": "TXSE"}
OFFERED, NOT_OFFERED = "OFFERED", "NOT_OFFERED"
FINANCIAL, REIT = "FINANCIAL", "REIT"
REASONS = ("NOT_LISTED", "NO_LISTED_COMMON", "FOREIGN_FILER", "NO_DEBT", "DEBT_GT_LIABILITIES", "NO_SHARES", "STALE_FUNDAMENTALS",
           "BELOW_MIN_DEBT")
STOCK_KINDS = ("EQUITY", "ADR", "REIT")
PERIODIC = ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F", "20-F/A", "40-F/A", "10-KT", "10-QT")
FOREIGN_FORMS = ("20-F", "40-F")
CF_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
SRC_LABEL = "SEC EDGAR"
FINANCIAL_NOTE = ("financial firm: CreditGrades was calibrated for industrial firms (its 2002 document excludes banks and finance firms);"
                  " deposits, repos and policy liabilities are left out of debt - treat this spread with care")

# ---- the XBRL tags (plan 4.3: 37 debt / liability / flag tags and 3 share tags) -----------------------------------------------------
STB, CP = "ShortTermBorrowings", "CommercialPaper"
LTDC, DC = "LongTermDebtCurrent", "DebtCurrent"
DEBT_TAGS = ("DebtLongtermAndShorttermCombinedAmount", "LongTermDebt", "LongTermDebtNoncurrent", LTDC, DC, STB, CP,
             "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "LongTermDebtAndCapitalLeaseObligations",
             "LongTermDebtAndCapitalLeaseObligationsCurrent", "NotesPayable", "SecuredDebt", "UnsecuredDebt", "SeniorNotes",
             "ConvertibleNotesPayable", "OtherLongTermDebtNoncurrent", "LinesOfCreditCurrent", "LongTermLineOfCredit",
             # R6 (the critic's CDS-C-04)
             "NotesAndLoansPayable", "LongTermNotesAndLoans", "LongTermNotesPayable", "NotesPayableCurrent", "LongTermLoansPayable",
             "OtherLongTermDebt", "SeniorLongTermNotes", "SecuredLongTermDebt", "UnsecuredLongTermDebt", "LoansPayable")
INSURER_TAGS = ("LiabilityForFuturePolicyBenefits", "LiabilityForClaimsAndClaimsAdjustmentExpense",
                "LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseNet", "PolicyholderFunds", "SeparateAccountsLiability")
REIT_TAG = "RealEstateInvestmentPropertyNet"
OTHER_TAGS = ("Liabilities", "Assets", "Deposits", REIT_TAG) + INSURER_TAGS
FLAG_TAGS = ("Assets", "Deposits") + INSURER_TAGS + (REIT_TAG,)          # the eight tags B2 task 1 verifies live
DEI_SHARES, CS_SHARES, WA_SHARES = "dei:EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding", \
    "WeightedAverageNumberOfDilutedSharesOutstanding"
SHARE_LABEL = {DEI_SHARES: "shares outstanding (cover page)", CS_SHARES: "common shares outstanding (balance sheet)",
               WA_SHARES: "diluted weighted average"}
#: (taxonomy, tag, unit, instant?) of every frame fetched per quarter: 37 + 3 = 40
FRAME_SPECS: Tuple[Tuple[str, str, str, bool], ...] = tuple(("us-gaap", t, "USD", True) for t in DEBT_TAGS + OTHER_TAGS) + (
    ("dei", "EntityCommonStockSharesOutstanding", "shares", True), ("us-gaap", CS_SHARES, "shares", True), ("us-gaap", WA_SHARES, "shares", False))

Fact = namedtuple("Fact", "end value accn filed form src")

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS credit_entities (cik INTEGER PRIMARY KEY, ticker TEXT NOT NULL, name TEXT NOT NULL, exchange TEXT,"
    " tickers_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, status_reason TEXT, flag TEXT NOT NULL DEFAULT '', flag_source TEXT,"
    " sic TEXT, sic_desc TEXT, latest_form TEXT, latest_filed TEXT, checked_ts INTEGER, updated_ts INTEGER NOT NULL,"
    " check_json TEXT NOT NULL DEFAULT '{}')",
    "CREATE INDEX IF NOT EXISTS ix_credit_entities_ticker ON credit_entities(ticker)",
    "CREATE TABLE IF NOT EXISTS credit_fundamentals (cik INTEGER NOT NULL, end_date TEXT NOT NULL, accn TEXT NOT NULL, known_from TEXT NOT NULL,"
    " filed TEXT, form TEXT, debt_c INTEGER, debt_recipe TEXT, debt_parts_json TEXT NOT NULL DEFAULT '{}', liabilities_c INTEGER, shares INTEGER,"
    " shares_end TEXT, shares_tag TEXT, source TEXT NOT NULL, fetched_ts INTEGER NOT NULL, PRIMARY KEY (cik, end_date, accn))",
    # the refresh runs (this module's own record: the desk's "refreshed 06:30 ET", the request / byte counts, the unknown exchange strings)
    "CREATE TABLE IF NOT EXISTS credit_refresh_runs (run_ts INTEGER PRIMARY KEY, mode TEXT NOT NULL, quarters TEXT NOT NULL, requests INTEGER NOT NULL,"
    " bytes INTEGER NOT NULL, seconds TEXT NOT NULL, listed INTEGER NOT NULL, offered INTEGER NOT NULL, entities_changed INTEGER NOT NULL,"
    " fundamentals_changed INTEGER NOT NULL, unknown_exchanges_json TEXT NOT NULL DEFAULT '{}', errors_json TEXT NOT NULL DEFAULT '[]', note TEXT)",
]

FILTERS = [{"key": "all", "label": "all"},
           {"key": "nonfinancial", "label": "non-financial", "caption": "known so far: no financial or REIT flag from the filing tags or the SIC code"},
           {"key": "financial", "label": "financial", "caption": "known so far: banks, insurers and finance companies (filing tags until the SIC code is checked)"},
           {"key": "reit", "label": "REIT", "caption": "known so far: REITs (filing tags until the SIC code is checked)"},
           {"key": "mine", "label": "my contracts", "caption": "names with an open single-name CDS in this account"}]
_FILTER_ALIASES = {"": "all", "all": "all", "nonfinancial": "nonfinancial", "non-financial": "nonfinancial", "non_financial": "nonfinancial",
                   "financial": "financial", "reit": "reit", "mine": "mine", "my": "mine", "held": "mine", "contracts": "mine", "my contracts": "mine"}


# ---- small helpers ----------------------------------------------------------------------------------------------------------------
def _to_c(v) -> Optional[int]:
    """An XBRL USD value (dollars, possibly with decimals) -> cents; None when unreadable."""
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    return int((d * 100).to_integral_value(ROUND_HALF_EVEN))


def _to_int(v) -> Optional[int]:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    return int(d.to_integral_value(ROUND_HALF_EVEN))


def _iso(v) -> Optional[str]:
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _usd(c: Optional[int]) -> str:
    """Cents as whole dollars with separators ("125,337,000,000"), cents shown only when there are any."""
    if c is None:
        return "n/a"
    c = int(c)
    sign = "-" if c < 0 else ""
    c = abs(c)
    whole, cents = divmod(c, 100)
    return f"{sign}{whole:,}" + (f".{cents:02d}" if cents else "")


def _usd_short(c: Optional[int]) -> str:
    """Cents as "USD 125.3B" / "USD 30.0M" (the browse column; the exact figure is in the hover)."""
    if c is None:
        return "n/a"
    v = Decimal(int(c)) / 100
    for div, suffix in ((Decimal(10) ** 12, "T"), (Decimal(10) ** 9, "B"), (Decimal(10) ** 6, "M"), (Decimal(10) ** 3, "K")):
        if abs(v) >= div:
            return f"USD {(v / div).quantize(Decimal('0.1'))}{suffix}"
    return f"USD {v.quantize(Decimal('0.01'))}"


def _plain(d: Decimal) -> str:
    """A Decimal without an exponent and without trailing zeros: 10 -> "10", 1.50 -> "1.5"."""
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _rid(prefix: str, payload) -> str:
    return f"{prefix}:{hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()[:12]}"


def _months(days: int) -> int:
    return int((Decimal(int(days)) / Decimal("30.4375")).to_integral_value(ROUND_HALF_EVEN))


def _age_days(end: Optional[str], today: date) -> Optional[int]:
    e = _iso(end)
    return None if e is None else (today - date.fromisoformat(e)).days


_CLOCK: List[object] = [None]                  # the bound runtime's clock (a reader without an explicit ``today`` uses it)


def _today(today: Optional[date] = None) -> date:
    if today is not None:
        return today if isinstance(today, date) else date.fromisoformat(str(today)[:10])
    clk = _CLOCK[0]
    if clk is not None:
        try:
            return clk.today()
        except Exception:   # noqa: BLE001 - a broken clock falls back to the real New York date
            pass
    return ny_of(int(_time.time())).date()


def _sic_int(sic) -> Optional[int]:
    try:
        return int(str(sic).strip())
    except (TypeError, ValueError):
        return None


# ---- the SEC client (B2 task 1) -----------------------------------------------------------------------------------------------------
class Pacer:
    """At most ``rps`` requests a second with no burst: each request gets its own slot ``1 / rps`` seconds after the previous one, reserved
    under a lock and waited for outside it (concurrent worker threads queue in order).  A request starts only once ``1 / rps`` has passed
    since the previous ACTUAL start (checked under the lock after the wait), so a late wake-up of the OS timer never brings two starts
    closer and no one-second window ever holds more than ``rps`` starts.  The clock is ``time.perf_counter``: on Windows ``time.monotonic``
    ticks in 15.6 ms steps, and the live check of 2026-10-29 measured 5 starts inside one second 23 times on it.  ``sleep`` / ``now`` are
    injectable (tests)."""

    def __init__(self, rps: float = SEC_RPS, sleep: Callable[[float], None] = _time.sleep, now: Callable[[], float] = _time.perf_counter):
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.interval = 1.0 / float(rps)
        self._sleep, self._now = sleep, now
        self._next = None                       # the slot the next request is given
        self._last = None                       # the actual start of the latest request
        self._lock = threading.Lock()
        self.count = 0

    def wait(self) -> None:
        with self._lock:
            t = self._now()
            slot = t if self._next is None else max(t, self._next)
            self._next = slot + self.interval
            self.count += 1
        while True:
            if slot > t:
                self._sleep(slot - t)
            with self._lock:
                t = self._now()
                if self._last is not None:
                    slot = max(slot, self._last + self.interval)
                if t >= slot:
                    self._last = t
                    self._next = max(self._next, t + self.interval)
                    return


PACER = Pacer()


class Stats:
    """Requests and bytes of one refresh (the run record and the live-check report)."""

    def __init__(self):
        self.requests = 0
        self.bytes = 0
        self.not_found = 0
        self.errors: List[str] = []
        self.lock = threading.Lock()

    def add(self, n: int) -> None:
        with self.lock:
            self.requests += 1
            self.bytes += int(n)


def _providers(services):
    return getattr(services, "providers", None) if services is not None else None


def is_offline(services) -> bool:
    """No network: no provider set (a test runtime) or the FakeProvider set (``--offline``)."""
    p = _providers(services)
    return p is None or bool(getattr(p, "offline", False))


def _health(services):
    p = _providers(services)
    return getattr(p, "health", None) if p is not None else None


def sec_get(services, url: str, getter=None, stats: Optional[Stats] = None, timeout: float = 30.0) -> bytes:
    """One SEC request: provider health ``sec`` must allow it, the pacer (4 a second at most), ``get_with_policy`` (the GOV user agent
    by host; ``getter`` replaces the network in tests and receives ``(url, ua, timeout, accept)``), the answer recorded in health.
    Raises ``FetchError`` (status kept)."""
    health = _health(services)
    if health is not None and not health.allow(PROVIDER):
        raise FetchError("SEC EDGAR is paused after repeated errors; it is tried again by itself", retryable=True)
    PACER.wait()
    try:
        raw = get_with_policy(url, timeout=timeout, getter=getter, max_bytes=config.MAX_BYTES)
    except FetchError as e:
        if health is not None:
            health.record(PROVIDER, False, e.status, str(e))
        if stats is not None:
            with stats.lock:
                stats.requests += 1
                if e.status == 404:
                    stats.not_found += 1
        raise
    if health is not None:
        health.record(PROVIDER, True, 200)
    if stats is not None:
        stats.add(len(raw or b""))
    return raw


_sec_get = sec_get                              # the plan's name (B2 task 1)


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise FetchError(f"SEC answered with unreadable JSON ({type(e).__name__})", retryable=False) from None


# ---- the universe (B2 task 2, plan 4.1) ---------------------------------------------------------------------------------------------
def _common_looking(ticker: str, exchange: Optional[str]) -> bool:
    """A common-stock-looking ticker: no preferred / warrant / unit / right suffix (-P.., -W.., -U.., -R..), and on Nasdaq no fifth letter
    W / U / R / P (warrants, units, rights, preferred).  Class shares (BRK-B, BF-B) are common."""
    t = str(ticker or "").upper().strip()
    if not t:
        return False
    for sep in ("-", "."):
        if sep in t:
            suffix = t.rsplit(sep, 1)[1]
            return not (suffix[:1] in ("P", "W", "U", "R"))
    if str(exchange or "").lower() == "nasdaq" and len(t) == 5 and t[-1] in "WURP":
        return False
    return True


def parse_universe(raw, master: Optional[Set[str]] = None) -> dict:
    """``company_tickers_exchange.json`` -> ``{entities: {cik: {cik, name, ticker, exchange, tickers}}, rows_by_exchange, unknown_exchanges,
    dropped}``.  Kept exchanges: {NYSE, Nasdaq, CBOE, TXSE} without case; OTC and null dropped; any other string dropped and counted.
    ``master``: the instrument master's ids (the primary-ticker rule 1)."""
    doc = _json(raw) if isinstance(raw, (bytes, bytearray, str)) else raw
    fields = [str(f).lower() for f in (doc.get("fields") or [])] if isinstance(doc, dict) else []
    data = doc.get("data") if isinstance(doc, dict) else None
    if not fields or not isinstance(data, list):
        raise FetchError("SEC tickers file: no fields / data arrays", retryable=False)
    ix = {k: fields.index(k) for k in ("cik", "name", "ticker", "exchange") if k in fields}
    if len(ix) < 4:
        raise FetchError("SEC tickers file: a field is missing (cik, name, ticker, exchange)", retryable=False)
    master = {str(m).upper() for m in (master or set())}
    by_cik: Dict[int, List[Tuple[str, str, str]]] = {}
    names: Dict[int, str] = {}
    counts: Dict[str, int] = {}
    unknown: Dict[str, int] = {}
    dropped = 0
    for row in data:
        if not isinstance(row, (list, tuple)) or len(row) < len(fields):
            dropped += 1
            continue
        exch_raw = row[ix["exchange"]]
        key = "null" if exch_raw is None else str(exch_raw)
        counts[key] = counts.get(key, 0) + 1
        exch = EXCHANGES.get(str(exch_raw).strip().lower()) if exch_raw is not None else None
        if exch is None:
            if exch_raw is not None and str(exch_raw).strip().lower() != "otc":
                unknown[str(exch_raw)] = unknown.get(str(exch_raw), 0) + 1
            dropped += 1
            continue
        cik = _to_int(row[ix["cik"]])
        ticker = str(row[ix["ticker"]] or "").strip().upper()
        if cik is None or cik <= 0 or not ticker:
            dropped += 1
            continue
        names.setdefault(cik, str(row[ix["name"]] or "").strip() or ticker)
        by_cik.setdefault(cik, []).append((ticker, exch, str(row[ix["name"]] or "")))
    ents: Dict[int, dict] = {}
    for cik, rows in by_cik.items():
        tickers = []
        for t, _e, _n in rows:
            if t not in tickers:
                tickers.append(t)
        primary = next((t for t, _e, _n in rows if t in master or t.replace("-", ".") in master or t.replace(".", "-") in master), None)
        if primary is None:
            primary = next((t for t, e, _n in rows if _common_looking(t, e)), None) or rows[0][0]
        exch = next(e for t, e, _n in rows if t == primary)
        ents[cik] = {"cik": cik, "name": names[cik], "ticker": primary, "exchange": exch, "tickers": [t for t in tickers if t != primary]}
    return {"entities": ents, "rows_by_exchange": counts, "unknown_exchanges": unknown, "dropped": dropped}


def fetch_universe(services, getter=None, stats: Optional[Stats] = None, master: Optional[Set[str]] = None) -> dict:
    """GET the SEC tickers file and parse it (``parse_universe``)."""
    raw = sec_get(services, UNIVERSE_URL, getter, stats)
    return parse_universe(raw, master if master is not None else _master_ids(services))


def _master_ids(services) -> Set[str]:
    db = getattr(services, "db", None) if services is not None else None
    if db is None:
        return set()
    with shared_reader(db) as conn:
        return {str(r[0]).upper() for r in conn.execute("SELECT instrument_id FROM instruments WHERE kind IN ('EQUITY','ADR','REIT')").fetchall()}


# ---- frames (B2 task 3) -----------------------------------------------------------------------------------------------------------------
def quarters(d: date, n: int) -> List[Tuple[int, int]]:
    """The calendar quarter of ``d`` and the ``n - 1`` before it, newest first: 2026-10-29, 2 -> [(2026, 4), (2026, 3)]."""
    y, q = d.year, (d.month - 1) // 3 + 1
    out = []
    for _ in range(max(1, int(n))):
        out.append((y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def frame_period(y: int, q: int, instant: bool = True) -> str:
    return f"CY{y}Q{q}" + ("I" if instant else "")


def frame_url(taxonomy: str, tag: str, unit: str, period: str) -> str:
    return FRAMES_URL.format(taxonomy=taxonomy, tag=tag, unit=unit, period=period)


def parse_frame_rows(raw, usd: bool = True) -> List[Tuple[int, str, int, str, str]]:
    """An XBRL frames answer -> ``[(cik, end, value, accn, entityName)]`` (value in cents for USD, a whole count for shares); unreadable
    rows are skipped (the payload is untrusted data)."""
    doc = _json(raw) if isinstance(raw, (bytes, bytearray, str)) else raw
    out = []
    for r in (doc.get("data") or []) if isinstance(doc, dict) else []:
        if not isinstance(r, dict):
            continue
        cik = _to_int(r.get("cik"))
        end = _iso(r.get("end"))
        val = _to_c(r.get("val")) if usd else _to_int(r.get("val"))
        if cik is None or end is None or val is None:
            continue
        out.append((cik, end, val, str(r.get("accn") or ""), str(r.get("entityName") or "")))
    return out


def parse_frame(raw, usd: bool = True) -> List[Tuple[int, str, int, str]]:
    """An XBRL frames answer -> ``[(cik, end, value, accn)]`` (``parse_frame_rows`` without the entity name)."""
    return [r[:4] for r in parse_frame_rows(raw, usd)]


_LEGAL_WORDS = frozenset({"THE", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LLC", "LP", "LTD", "LIMITED", "PLC", "NV", "SA",
                          "AG", "SE", "L", "P", "N", "V"})
_STATE_TAG = re.compile(r"[/\\][A-Z ]{1,6}[/\\]")
_NOT_WORD = re.compile(r"[^A-Z0-9]+")
_ACCN = re.compile(r"^(\d{10})-\d{2}-\d{6}$")


def name_key(name) -> Tuple[str, ...]:
    """A company name compared without case, punctuation, a state tag (``/DE/``) or its legal form: "ENTERGY CORP /DE/" and "Entergy
    Corporation" -> ('ENTERGY',); "The Southern Company" -> ('SOUTHERN',); "PG&E Corp" -> ('PG', 'AND', 'E').  Words such as GROUP or
    HOLDINGS are kept ("UBS Group AG" is not "UBS AG")."""
    s = str(name or "").upper().replace("&", " AND ").replace("\u2019", "").replace("'", "")
    s = _STATE_TAG.sub(" ", s)
    return tuple(w for w in _NOT_WORD.sub(" ", s).split() if w not in _LEGAL_WORDS)


def submitter_cik(accn) -> Optional[int]:
    """The CIK that submitted a filing: the first 10 digits of its accession number (a registrant's own or its filing agent's)."""
    m = _ACCN.match(str(accn or "").strip())
    return int(m.group(1)) if m else None


def other_registrant(cik: int, accn, entity_name, names: Dict[int, str]) -> Optional[int]:
    """The listed registrant whose figure a frames fact really is, when it is not ``cik``'s own: the accession was submitted by ANOTHER
    listed registrant (``names``: CIK -> SEC name of every listed CIK) and the fact's ``entityName`` is that registrant's name and not
    ``cik``'s own.  The SEC's frames API files a joint 10-Q's consolidated (parent) facts under one co-registrant's CIK: Entergy Corp's
    2026-06-30 10-Q (accession 0000065984-26-000283, entityName "ENTERGY CORPORATION") under Entergy Arkansas (CIK 7323).  None otherwise
    (a filing agent's accession, the CIK's own name, an unknown name)."""
    sub = submitter_cik(accn)
    if sub is None or sub == int(cik) or sub not in names:
        return None
    key = name_key(entity_name)
    if not key or key != name_key(names[sub]) or (int(cik) in names and key == name_key(names[int(cik)])):
        return None
    return sub


def fetch_frames(services, qtrs: Sequence[Tuple[int, int]], ciks: Optional[Set[int]] = None, getter=None, stats: Optional[Stats] = None,
                 specs: Sequence[Tuple[str, str, str, bool]] = FRAME_SPECS, pkey: Optional[str] = None, names: Optional[Dict[int, str]] = None) -> dict:
    """Every frame of ``specs`` for each quarter: ``{facts: {cik: {key: [Fact]}}, rows: {"tag CYyyyyQq[I]": n}, missing: [...], errors: [...],
    others: {cik: {"by": registrant CIK, "facts": n, "example": words}}}`` (key = the us-gaap tag, or ``dei:<tag>``).  ``names`` (CIK -> SEC
    name of every listed CIK) turns on the joint-filing rule (``other_registrant``): such a fact is left out and counted in ``others``.
    A 404 is a frame with no data (kept as 0 rows); another failure is recorded and the run goes on; more than a quarter of the frames
    failing raises (nothing is written from a half-fetched set)."""
    facts: Dict[int, Dict[str, List[Fact]]] = {}
    rows: Dict[str, int] = {}
    errors: List[str] = []
    missing: List[str] = []
    others: Dict[int, dict] = {}
    total = 0
    for (y, q) in qtrs:
        for taxonomy, tag, unit, instant in specs:
            period = frame_period(y, q, instant)
            label = f"{taxonomy}:{tag} {period}"
            total += 1
            _progress(pkey, step=f"XBRL frame {tag} {period}", done_inc=1)
            try:
                raw = sec_get(services, frame_url(taxonomy, tag, unit, period), getter, stats)
            except FetchError as e:
                if e.status == 404:
                    rows[label] = 0
                    missing.append(label)
                    continue
                errors.append(f"{label}: {e}")
                continue
            key = tag if taxonomy == "us-gaap" else f"{taxonomy}:{tag}"
            got = parse_frame_rows(raw, usd=(unit == "USD"))
            rows[label] = len(got)
            for cik, end, val, accn, ename in got:
                if ciks is not None and cik not in ciks:
                    continue
                if names is not None:
                    by = other_registrant(cik, accn, ename, names)
                    if by is not None:
                        o = others.setdefault(cik, {"by": by, "facts": 0, "example": f"{key} {end} of {ename} (accession {accn})"})
                        o["facts"] += 1
                        continue
                facts.setdefault(cik, {}).setdefault(key, []).append(Fact(end, val, accn, None, None, period))
    if total and len(errors) * 4 > total:
        raise FetchError(f"SEC XBRL frames: {len(errors)} of {total} requests failed ({errors[0]})")
    return {"facts": facts, "rows": rows, "missing": missing, "errors": errors, "requests": total, "others": others}


# ---- the recipes (plan 3.2) -------------------------------------------------------------------------------------------------------------
def _add(parts: Dict[str, int], tag: str, v: Optional[int]) -> int:
    if v:
        parts[tag] = parts.get(tag, 0) + int(v)
    return int(v or 0)


def _stb_cp(f: Dict[str, int], parts: Dict[str, int], left: List[dict]) -> int:
    """ShortTermBorrowings + CommercialPaper under the parts rule: CP counts only when it is larger than STB."""
    stb, cp = f.get(STB), f.get(CP)
    if stb is None:
        return _add(parts, CP, cp) if cp is not None else 0
    total = _add(parts, STB, stb)
    if cp is not None:
        if cp > stb:
            total += _add(parts, CP, cp)
        elif cp:
            left.append({"tag": CP, "value_c": int(cp), "whole": STB, "whole_c": int(stb), "why": "part"})
    return total


def _first_positive(f: Dict[str, int], tag: str) -> Optional[int]:
    v = f.get(tag)
    return int(v) if v is not None and int(v) > 0 else None


def _r1(f, parts, left):
    v = _first_positive(f, "DebtLongtermAndShorttermCombinedAmount")
    return None if v is None else _add(parts, "DebtLongtermAndShorttermCombinedAmount", v)


def _r2(f, parts, left):
    v = _first_positive(f, "LongTermDebt")
    return None if v is None else _add(parts, "LongTermDebt", v) + _stb_cp(f, parts, left)


def _r3(f, parts, left):
    v = _first_positive(f, "LongTermDebtNoncurrent")
    if v is None:
        return None
    total = _add(parts, "LongTermDebtNoncurrent", v)
    if f.get(DC) is not None:
        return total + _add(parts, DC, f[DC])
    return total + _add(parts, LTDC, f.get(LTDC)) + _stb_cp(f, parts, left)


def _r4a(f, parts, left):
    t = "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"
    v = _first_positive(f, t)
    return None if v is None else _add(parts, t, v) + _stb_cp(f, parts, left)


def _r4b(f, parts, left):
    t = "LongTermDebtAndCapitalLeaseObligations"
    v = _first_positive(f, t)
    if v is None:
        return None
    total = _add(parts, t, v)
    for cur in ("LongTermDebtAndCapitalLeaseObligationsCurrent", LTDC, DC):
        if f.get(cur) is not None:
            total += _add(parts, cur, f[cur])
            break
    return total + _stb_cp(f, parts, left)


def _r5a(f, parts, left):
    if f.get("SecuredDebt") is None and f.get("UnsecuredDebt") is None:
        return None
    return _add(parts, "SecuredDebt", f.get("SecuredDebt")) + _add(parts, "UnsecuredDebt", f.get("UnsecuredDebt"))


def _r5b(f, parts, left):
    tags = ("NotesPayable", "SeniorNotes", "ConvertibleNotesPayable", "LinesOfCreditCurrent", "LongTermLineOfCredit", "OtherLongTermDebtNoncurrent")
    if all(f.get(t) is None for t in tags):
        return None
    total = 0
    np_ = f.get("NotesPayable")
    if np_ is not None:
        total += _add(parts, "NotesPayable", np_)
        for t in ("SeniorNotes", "ConvertibleNotesPayable"):
            v = f.get(t)
            if v is None:
                continue
            if v > np_:
                total += _add(parts, t, v)
            elif v:
                left.append({"tag": t, "value_c": int(v), "whole": "NotesPayable", "whole_c": int(np_), "why": "part"})
    else:
        total += _add(parts, "SeniorNotes", f.get("SeniorNotes")) + _add(parts, "ConvertibleNotesPayable", f.get("ConvertibleNotesPayable"))
    for t in ("LinesOfCreditCurrent", "LongTermLineOfCredit", "OtherLongTermDebtNoncurrent"):
        total += _add(parts, t, f.get(t))
    return total


R6_NC = ("LongTermNotesAndLoans", "LongTermNotesPayable", "SeniorLongTermNotes", "LongTermLoansPayable", "OtherLongTermDebt")


def _r6(f, parts, left):
    cands: List[Tuple[int, Dict[str, int], List[dict]]] = []
    for t in ("NotesAndLoansPayable", "LoansPayable"):
        if f.get(t) is not None:
            cands.append((int(f[t]), {t: int(f[t])} if f[t] else {}, []))
    nc_opts: List[Tuple[int, Dict[str, int]]] = [(int(f[t]), {t: int(f[t])}) for t in R6_NC if f.get(t) is not None]
    if f.get("SecuredLongTermDebt") is not None or f.get("UnsecuredLongTermDebt") is not None:
        s, u = int(f.get("SecuredLongTermDebt") or 0), int(f.get("UnsecuredLongTermDebt") or 0)
        nc_opts.append((s + u, {k: v for k, v in (("SecuredLongTermDebt", s), ("UnsecuredLongTermDebt", u)) if v}))
    cur_parts: Dict[str, int] = {}
    cur_left: List[dict] = []
    cur_present = False
    if f.get("NotesPayableCurrent") is not None:
        cur, cur_present = _add(cur_parts, "NotesPayableCurrent", f["NotesPayableCurrent"]), True
    elif f.get(DC) is not None:
        cur, cur_present = _add(cur_parts, DC, f[DC]), True
    else:
        cur = _add(cur_parts, LTDC, f.get(LTDC)) + _stb_cp(f, cur_parts, cur_left)
        cur_present = bool(cur_parts) or f.get(LTDC) is not None or f.get(STB) is not None or f.get(CP) is not None
    if nc_opts or cur_present:
        nc_v, nc_p = max(nc_opts, key=lambda x: x[0]) if nc_opts else (0, {})
        others = [{"tag": t, "value_c": v, "whole": next(iter(nc_p), None), "whole_c": nc_v, "why": "same total"}
                  for v, p in nc_opts if p is not nc_p for t in p if v]
        merged = dict(nc_p)
        for k, v in cur_parts.items():
            merged[k] = merged.get(k, 0) + v
        cands.append((nc_v + cur, {k: v for k, v in merged.items() if v}, cur_left + others))
    if not cands:
        return None
    best = max(cands, key=lambda x: x[0])
    for k, v in best[1].items():
        _add(parts, k, v)
    left.extend(best[2])
    return best[0]


RECIPES_FIRST = (("R1", _r1), ("R2", _r2), ("R3", _r3), ("R4a", _r4a), ("R4b", _r4b))
RECIPES_THEN = (("R5a", _r5a), ("R5b", _r5b), ("R6", _r6))


def compute_debt(f: Dict[str, int]) -> dict:
    """The debt recipes R1-R6 with the parts rule (plan 3.2) on the facts of ONE balance-sheet date: ``f`` = {us-gaap tag: cents}, a tag
    absent when it is not reported.  ``{recipe, debt_c, parts {tag: cents}, left_out [{tag, value_c, whole, whole_c, why}]}``; recipe None
    and debt_c 0 when no recipe gives debt above 0."""
    f = {k: int(v) for k, v in (f or {}).items() if v is not None}
    for name, fn in RECIPES_FIRST:
        parts: Dict[str, int] = {}
        left: List[dict] = []
        v = fn(f, parts, left)
        if v is not None:
            return {"recipe": name, "debt_c": int(v), "parts": parts, "left_out": left}
    best = None                              # B2-R3 (integration): the largest of R5a / R5b / R6 above 0, a tie keeps the earlier recipe
    for name, fn in RECIPES_THEN:
        parts, left = {}, []
        v = fn(f, parts, left)
        if v is not None and v > 0 and (best is None or int(v) > best["debt_c"]):
            best = {"recipe": name, "debt_c": int(v), "parts": parts, "left_out": left}
    if best is not None:
        return best
    return {"recipe": None, "debt_c": 0, "parts": {}, "left_out": []}


def tags_flag(at: Dict[str, int]) -> Tuple[str, str]:
    """The flag from the filing tags of one balance sheet: (FINANCIAL | REIT | '', the words of why)."""
    dep = at.get("Deposits")
    if dep is not None and dep > 0:
        return FINANCIAL, f"Deposits {_usd(dep)}"
    for t in INSURER_TAGS:
        v = at.get(t)
        if v is not None and v > 0:
            return FINANCIAL, f"{t} {_usd(v)}"
    re_, assets = at.get(REIT_TAG), at.get("Assets")
    if re_ is not None and assets and assets > 0 and re_ * 2 >= assets:
        pct = (Decimal(int(re_)) * 100 / Decimal(int(assets))).quantize(Decimal("1"))
        return REIT, f"investment property {pct} % of assets"
    return "", ""


def _pick(cands: List[Fact]) -> Fact:
    return max(cands, key=lambda x: (x.filed or "", x.accn or ""))


def build_fundamentals(cik: int, facts: Dict[str, List[Fact]], known_from: str, fetched_ts: int, source: str) -> Optional[dict]:
    """One CIK's facts -> its fundamentals row (plan 3.2): the balance-sheet ``end`` = the latest end among the debt-recipe tags, the
    recipe on that end's facts, the Liabilities of that end, the share chain, the tags flag; None when the CIK has no debt-recipe fact."""
    ends = [x.end for t in DEBT_TAGS for x in facts.get(t, ())]
    if not ends:
        return None
    end = max(ends)
    at: Dict[str, Fact] = {}
    for t in DEBT_TAGS + OTHER_TAGS:
        c = [x for x in facts.get(t, ()) if x.end == end]
        if c:
            at[t] = _pick(c)
    res = compute_debt({t: x.value for t, x in at.items() if t in DEBT_TAGS})
    used = [at[t] for t in res["parts"] if t in at] or [at[t] for t in at if t in DEBT_TAGS]
    top = max(used, key=lambda x: (x.filed or "", x.accn or ""))
    shares = shares_end = shares_tag = None
    # B3-R3 (integration): a cover share count dated after its own filing is an issuer typo (AAL's 10-Q filed 2026-07-23 carries 2027-07-17)
    dei = [x for x in facts.get(DEI_SHARES, ()) if x.value and x.value > 0 and not (x.filed and x.end > x.filed)]
    cs = [x for x in facts.get(CS_SHARES, ()) if x.end == end and x.value and x.value > 0]
    wa = [x for x in facts.get(WA_SHARES, ()) if x.value and x.value > 0]
    if dei:
        b = max(dei, key=lambda x: (x.end, x.filed or "", x.accn or ""))
        shares, shares_end, shares_tag = int(b.value), b.end, DEI_SHARES
    elif cs:
        b = _pick(cs)
        shares, shares_end, shares_tag = int(b.value), b.end, CS_SHARES
    elif wa:
        b = max(wa, key=lambda x: (x.end, x.filed or "", x.accn or ""))
        shares, shares_end, shares_tag = int(b.value), b.end, WA_SHARES
    other = {t: int(x.value) for t, x in at.items() if t in OTHER_TAGS}
    flag, why = tags_flag(other)
    liab = at.get("Liabilities")
    src = source if source else f"SEC XBRL frames {top.src}"
    return {"cik": int(cik), "end_date": end, "accn": top.accn or "", "known_from": known_from, "filed": top.filed, "form": top.form,
            "debt_c": int(res["debt_c"]), "debt_recipe": res["recipe"],
            "debt_parts_json": canonical_json({"parts": res["parts"], "left_out": res["left_out"], "facts": other, "tags_flag": [flag, why]}),
            "liabilities_c": None if liab is None else int(liab.value), "shares": shares, "shares_end": shares_end, "shares_tag": shares_tag,
            "source": src, "fetched_ts": int(fetched_ts)}


# ---- companyfacts (B2 task 4) -----------------------------------------------------------------------------------------------------------
def facts_from_companyfacts(doc) -> Tuple[Dict[str, List[Fact]], bool]:
    """``companyfacts`` -> (facts in the frames shape, ifrs_only).  Instants without a dimension of forms 10-K / 10-Q and their /A
    versions (companyfacts already drops dimensional facts); the diluted weighted average as a quarterly duration (61-121 days)."""
    if not isinstance(doc, dict):
        raise FetchError("SEC companyfacts: not an object", retryable=False)
    root = doc.get("facts") or {}
    gaap = root.get("us-gaap") or {}
    dei = root.get("dei") or {}
    out: Dict[str, List[Fact]] = {}

    def take(key: str, entry, unit: str, instant: bool) -> None:
        for r in ((entry or {}).get("units") or {}).get(unit) or []:
            if not isinstance(r, dict):
                continue
            form = str(r.get("form") or "")
            if form not in CF_FORMS:
                continue
            end = _iso(r.get("end"))
            if end is None:
                continue
            start = _iso(r.get("start"))
            if instant and start is not None:
                continue
            if not instant:
                if start is None:
                    continue
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                if not 61 <= days <= 121:
                    continue
            val = _to_c(r.get("val")) if unit == "USD" else _to_int(r.get("val"))
            if val is None:
                continue
            out.setdefault(key, []).append(Fact(end, val, str(r.get("accn") or ""), _iso(r.get("filed")), form, "companyfacts"))

    for t in DEBT_TAGS + OTHER_TAGS:
        take(t, gaap.get(t), "USD", True)
    take(CS_SHARES, gaap.get(CS_SHARES), "shares", True)
    take(WA_SHARES, gaap.get(WA_SHARES), "shares", False)
    take(DEI_SHARES, dei.get("EntityCommonStockSharesOutstanding"), "shares", True)
    ifrs_only = bool(root.get("ifrs-full")) and not any(out.get(t) for t in DEBT_TAGS)
    return out, ifrs_only


def _latest_periodic(sub) -> Optional[dict]:
    """The latest periodic filing of ``submissions`` (``filings.recent`` is newest first): {form, filed, report, accn}."""
    rec = ((sub or {}).get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    best = None
    for i, form in enumerate(forms):
        f = str(form or "").upper()
        if f not in PERIODIC:
            continue
        try:
            filed = _iso(rec.get("filingDate", [])[i])
            report = _iso((rec.get("reportDate") or [None] * len(forms))[i])
            accn = str((rec.get("accessionNumber") or [""] * len(forms))[i] or "")
        except (IndexError, TypeError):
            continue
        cand = {"form": f, "filed": filed, "report": report, "accn": accn}
        if best is None or (filed or "") > (best["filed"] or ""):
            best = cand
    return best


# ---- point in time (plan 3.2) -----------------------------------------------------------------------------------------------------------
def _better(a: dict, b: dict) -> bool:
    """Two rows of one ``end``: is ``a`` the one to use?  The latest ``filed`` when both are known, else the latest ``known_from``, then
    the higher ``accn``."""
    if a.get("filed") and b.get("filed") and a["filed"] != b["filed"]:
        return a["filed"] > b["filed"]
    if (a.get("known_from") or "") != (b.get("known_from") or ""):
        return (a.get("known_from") or "") > (b.get("known_from") or "")
    return (a.get("accn") or "") > (b.get("accn") or "")


def _choose(rows: Iterable[dict]) -> Optional[dict]:
    best = None
    for r in rows:
        if best is None or r["end_date"] > best["end_date"] or (r["end_date"] == best["end_date"] and _better(r, best)):
            best = r
    return best


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _fund_rows(conn, cik: int) -> List[dict]:
    if not _has_table(conn, "credit_fundamentals"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM credit_fundamentals WHERE cik=?", (int(cik),)).fetchall()]


def _fund_view(row: Optional[dict], today: date) -> Optional[dict]:
    """A stored fundamentals row -> the C-ENT shape (plan 6.3) with the parts, the words and the age."""
    if row is None:
        return None
    try:
        pj = json.loads(row.get("debt_parts_json") or "{}")
    except ValueError:
        pj = {}
    parts = {str(k): int(v) for k, v in (pj.get("parts") or {}).items()}
    left = [dict(x) for x in (pj.get("left_out") or []) if isinstance(x, dict)]
    age = _age_days(row.get("end_date"), today)
    notes = []
    for x in left:
        if x.get("why") == "same total":
            notes.append(f"{x.get('tag')} {_usd(x.get('value_c'))} taken as another name of the same total as {x.get('whole')}")
        else:
            notes.append(f"{x.get('tag')} {_usd(x.get('value_c'))} taken as part of {x.get('whole')} {_usd(x.get('whole_c'))}")
    form = row.get("form")
    where = f"{form + ', ' if form else ''}balance sheet {row.get('end_date')}"
    parts_text = " + ".join(f"{t} {_usd(v)}" for t, v in parts.items()) if parts else "no debt tag above 0"
    amber = age is not None and AGE_WARN_DAYS <= age <= STALE_DAYS
    age_note = None
    if age is not None and age >= AGE_WARN_DAYS:
        age_note = f"balance sheet {row.get('end_date')} is {_months(age)} months old"
    flag = pj.get("tags_flag") or ["", ""]
    return {"cik": int(row["cik"]), "end_date": row.get("end_date"), "accn": row.get("accn"), "known_from": row.get("known_from"),
            "filed": row.get("filed"), "form": form, "debt_c": None if row.get("debt_c") is None else int(row["debt_c"]),
            "debt_recipe": row.get("debt_recipe"), "debt_parts": parts, "debt_left_out": left, "debt_note": "; ".join(notes) or None,
            "debt_text": f"{parts_text} ({where}; {row.get('source')})", "liabilities_c": row.get("liabilities_c"),
            "shares": None if row.get("shares") is None else int(row["shares"]), "shares_end": row.get("shares_end"),
            "shares_tag": row.get("shares_tag"), "shares_label": SHARE_LABEL.get(row.get("shares_tag") or "", None), "source": row.get("source"),
            "fetched_ts": row.get("fetched_ts"), "age_days": age, "age_months": None if age is None else _months(age), "age_note": age_note,
            "amber": bool(amber), "facts": {str(k): int(v) for k, v in (pj.get("facts") or {}).items()},
            "tags_flag": str(flag[0] or ""), "tags_flag_note": str(flag[1] or "")}


def fundamentals_on(conn, cik: int, d: date, today: Optional[date] = None) -> Optional[dict]:
    """Point in time: among the rows the terminal knew on or before ``d`` (``known_from <= d``), the one with the latest ``end`` (two rows
    of one end: the tie-break of the debt rules).  ``age_days`` is counted to ``d`` unless ``today`` is given."""
    d = _today(d)
    rows = [r for r in _fund_rows(conn, cik) if (r.get("known_from") or "9999") <= d.isoformat()]
    return _fund_view(_choose(rows), today or d)


def fundamentals_now(conn, cik: int, today: Optional[date] = None) -> Optional[dict]:
    """The latest balance sheet stored for ``cik`` (C-ENT shape): {cik, end_date, accn, known_from, filed, form, debt_c, debt_recipe,
    debt_parts {tag: cents}, debt_note, liabilities_c, shares, shares_end, shares_tag, source, age_days, ...}; None when there is none."""
    return _fund_view(_choose(_fund_rows(conn, cik)), _today(today))


def split_adjusted_shares(shares: Optional[int], shares_end: Optional[str], splits: Iterable, through: Optional[date] = None) -> Tuple[Optional[int], Optional[str], List[dict]]:
    """Shares x numerator / denominator of every split dated AFTER the share count's date (and on or before ``through``), so a 10:1 split
    does not make debt per share ten times too large (plan 3.2).  ``splits``: (date, numerator, denominator) tuples or dicts with
    split_date / numerator / denominator.  Returns (shares, words or None, the splits applied)."""
    if shares is None:
        return None, None, []
    se = _iso(shares_end)
    thr = _iso(through) if through is not None else None
    applied = []
    n = Decimal(int(shares))
    for s in splits or []:
        if isinstance(s, dict):
            sd, num, den = s.get("split_date") or s.get("date"), s.get("numerator"), s.get("denominator")
        else:
            sd, num, den = s[0], s[1], s[2]
        sd = _iso(sd)
        try:
            num, den = Decimal(str(num)), Decimal(str(den))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if sd is None or den <= 0 or num <= 0 or (se is not None and sd <= se) or (thr is not None and sd > thr):
            continue
        n = n * num / den
        applied.append({"split_date": sd, "numerator": _plain(num), "denominator": _plain(den)})
    if not applied:
        return int(shares), None, []
    out = int(n.to_integral_value(ROUND_HALF_EVEN))
    words = "; ".join(f"x {a['numerator']}/{a['denominator']} for the split of {a['split_date']}" for a in applied)
    return out, f"{int(shares):,} shares at {se} {words} = {out:,}", applied


# ---- status and flags (plan 3.5, 4.4) ------------------------------------------------------------------------------------------------------
def _check(ent: dict) -> dict:
    try:
        c = json.loads(ent.get("check_json") or "{}") if isinstance(ent.get("check_json"), str) else dict(ent.get("check_json") or {})
    except ValueError:
        c = {}
    return c if isinstance(c, dict) else {}


def has_listed_common(ent: dict, stock: bool = False) -> bool:
    """Does the entity have listed common stock?  Its primary ticker is in the instrument master as a stock (``stock``) or looks like
    common stock; the universe rule picks such a ticker whenever the CIK has one, so a primary that is neither means the SEC file lists
    only preferred / warrant / unit / right symbols for the CIK."""
    return bool(stock) or _common_looking(ent.get("ticker") or "", ent.get("exchange"))


def derive(ent: dict, fund: Optional[dict], today: date, adr: bool = False, stock: bool = False) -> dict:
    """The status and flag of one entity row with its latest fundamentals (``fundamentals_now`` shape) as of ``today``:
    {status, status_reason, flag, flag_source, flag_note, limited}.  ``stock``: the primary ticker is in the instrument master as a stock.
    The order of the reasons is fixed: not listed, no listed common stock, foreign filer, no debt, debt above liabilities, no shares,
    stale, below the minimum."""
    chk = _check(ent)
    sic = _sic_int(ent.get("sic"))
    tflag, tnote = ((fund or {}).get("tags_flag") or ""), ((fund or {}).get("tags_flag_note") or "")
    if sic is not None:
        source = "sic"
        if 6000 <= sic <= 6499:
            flag, note = FINANCIAL, f"financial (SIC {sic} {ent.get('sic_desc') or ''})".replace(" )", ")")
        elif sic == 6798:
            flag, note = REIT, f"REIT (SIC 6798 {ent.get('sic_desc') or 'Real Estate Investment Trusts'})"
        elif tflag == REIT:
            flag, note = REIT, f"REIT ({tnote})"
        else:
            flag, note = "", ""
    elif tflag:
        source = "tags"
        flag = tflag
        note = (f"financial (from filing tags: {tnote}; SIC not checked yet)" if tflag == FINANCIAL
                else f"REIT (from filing tags: {tnote}; SIC not checked yet)")
    else:
        source, flag, note = None, "", ""
    out = {"flag": flag, "flag_source": source if flag else (source if sic is not None else None), "flag_note": note or None,
           "limited": flag == FINANCIAL, "limited_reason": FINANCIAL_NOTE if flag == FINANCIAL else None}
    form = str(ent.get("latest_form") or "").upper()
    if not ent.get("exchange"):
        reason = "NOT_LISTED"
    elif not has_listed_common(ent, stock or adr):
        reason = "NO_LISTED_COMMON"
    elif form.startswith(FOREIGN_FORMS) or adr or chk.get("ifrs"):
        reason = "FOREIGN_FILER"
    elif fund is None or not fund.get("debt_c") or int(fund["debt_c"]) <= 0:
        reason = "NO_DEBT"
    elif fund.get("liabilities_c") is not None and int(fund["debt_c"]) > int(fund["liabilities_c"]):
        reason = "DEBT_GT_LIABILITIES"
    elif not fund.get("shares"):
        reason = "NO_SHARES"
    elif ((_age_days(fund.get("end_date"), today) or 0) > STALE_DAYS) or ((_age_days(fund.get("shares_end"), today) or 0) > STALE_DAYS):
        reason = "STALE_FUNDAMENTALS"
    elif int(fund["debt_c"]) < MIN_DEBT_C:
        reason = "BELOW_MIN_DEBT"
    else:
        reason = None
    out.update(status=OFFERED if reason is None else NOT_OFFERED, status_reason=reason)
    return out


def status_words(reason: Optional[str], fund: Optional[dict] = None, ent: Optional[dict] = None, today: Optional[date] = None) -> str:
    """The reason in trader words (the code goes in the hover only, BW-04)."""
    if reason is None:
        f = fund or {}
        if f.get("amber") and f.get("age_note"):
            return f"offered ({f['age_note']})"
        return "offered"
    if reason == "NOT_LISTED":
        return "not offered: no longer listed on NYSE, Nasdaq, Cboe or the Texas Stock Exchange in the SEC's exchange file"
    if reason == "NO_LISTED_COMMON":
        e = ent or {}
        raw = e.get("tickers_json") if e.get("tickers_json") is not None else e.get("tickers")
        try:
            others = [str(x) for x in (json.loads(raw) if isinstance(raw, str) else (raw or []))]
        except ValueError:
            others = []
        syms = ", ".join([s for s in [str(e.get("ticker") or "")] + others[:4] if s]) + (" ..." if len(others) > 4 else "")
        return ("not offered: no common stock listed - the SEC's exchange file lists only " + (syms or "other symbols") +
                " (preferred shares, warrants, units or rights); the model needs the share price of listed common stock")
    if reason == "FOREIGN_FILER":
        return "not offered: foreign private issuer (ADR ratios and IFRS reports are not modelled in this release)"
    if reason == "NO_DEBT":
        return "not offered: no debt reported in the latest 10-Q / 10-K XBRL (or it uses the company's own tags)"
    f = fund or {}
    if reason == "DEBT_GT_LIABILITIES":
        return (f"not offered: the debt found ({_usd(f.get('debt_c'))}) is above the company's total liabilities ({_usd(f.get('liabilities_c'))})"
                f" on the same balance sheet {f.get('end_date')} - the filing's tags do not add up")
    if reason == "NO_SHARES":
        return "not offered: share count not reported (multi-class issuer?)"
    if reason == "STALE_FUNDAMENTALS":
        t = _today(today)
        a = _age_days(f.get("end_date"), t)
        if a is not None and a > STALE_DAYS:
            return f"not offered: balance sheet {f.get('end_date')} is {_months(a)} months old"
        s = _age_days(f.get("shares_end"), t)
        return f"not offered: the share count of {f.get('shares_end')} is {_months(s or 0)} months old"
    if reason == "BELOW_MIN_DEBT":
        return f"not offered: reported debt USD {_usd(f.get('debt_c'))} is below the USD {_usd(MIN_DEBT_C)} minimum (assumed)"
    return f"not offered ({reason})"


# ---- reads (contract C-ENT, plan 6.3) ------------------------------------------------------------------------------------------------------
def _ticker_variants(t: str) -> List[str]:
    t = str(t or "").strip().upper()
    out = []
    for v in (t, t.replace("-", "."), t.replace(".", "-")):
        if v and v not in out:
            out.append(v)
    return out


def _master_hit(conn, ticker: str) -> Tuple[Optional[str], Optional[str]]:
    """(instrument id, kind) of the master row for the ticker (or its . / - variant) among the stock kinds; (None, None) otherwise."""
    for v in _ticker_variants(ticker):
        r = conn.execute("SELECT instrument_id, kind FROM instruments WHERE instrument_id=? AND kind IN ('EQUITY','ADR','REIT')", (v,)).fetchone()
        if r is not None:
            return str(r[0]), str(r[1])
    return None, None


def _ent_row(conn, key) -> Optional[dict]:
    if not _has_table(conn, "credit_entities"):
        return None
    cik = None
    if isinstance(key, int) and not isinstance(key, bool):
        cik = key
    elif isinstance(key, str) and key.strip().isdigit():
        cik = int(key.strip())
    if cik is not None:
        r = conn.execute("SELECT * FROM credit_entities WHERE cik=?", (cik,)).fetchone()
        return None if r is None else dict(r)
    t = str(key or "").strip().upper()
    if not t:
        return None
    for v in _ticker_variants(t):
        r = conn.execute("SELECT * FROM credit_entities WHERE ticker=?", (v,)).fetchone()
        if r is not None:
            return dict(r)
    for v in _ticker_variants(t):
        for r in conn.execute("SELECT * FROM credit_entities WHERE tickers_json LIKE ?", (f'%"{v}"%',)).fetchall():
            try:
                if v in json.loads(r["tickers_json"] or "[]"):
                    return dict(r)
            except ValueError:
                continue
    return None


def _entity_view(ent: dict, fund: Optional[dict], today: date, iid: Optional[str], kind: Optional[str]) -> dict:
    d = derive(ent, fund, today, adr=(kind == "ADR"), stock=kind is not None)
    chk = _check(ent)
    try:
        tickers = json.loads(ent.get("tickers_json") or "[]")
    except ValueError:
        tickers = []
    return {"cik": int(ent["cik"]), "ticker": ent["ticker"], "name": ent["name"], "exchange": ent.get("exchange"), "tickers": tickers,
            "status": d["status"], "status_reason": d["status_reason"], "status_words": status_words(d["status_reason"], fund, ent, today),
            "offered": d["status"] == OFFERED, "flag": d["flag"], "flag_source": d["flag_source"], "flag_note": d["flag_note"],
            "limited": d["limited"], "limited_reason": d["limited_reason"], "sic": ent.get("sic"), "sic_desc": ent.get("sic_desc"),
            "latest_form": ent.get("latest_form"), "latest_filed": ent.get("latest_filed"), "latest_report": chk.get("latest_report"),
            "checked_ts": ent.get("checked_ts"), "check_note": chk.get("note"), "instrument_id": iid, "instrument_kind": kind,
            "updated_ts": ent.get("updated_ts"), "fundamentals": fund, "source": SRC_LABEL}


def entity(conn, key, today: Optional[date] = None) -> Optional[dict]:
    """One reference entity by CIK (int, or a string of digits) or ticker (the primary one or any of ``tickers_json``; BRK.B = BRK-B);
    None when unknown.  {cik, ticker, name, exchange, tickers, status, status_reason, status_words, offered, flag, flag_source, flag_note,
    limited, limited_reason, sic, sic_desc, latest_form, latest_filed, latest_report, checked_ts, check_note, instrument_id (master id or
    None), fundamentals (``fundamentals_now``)} - status and flag derived as of ``today``."""
    ent = _ent_row(conn, key)
    if ent is None:
        return None
    t = _today(today)
    fund = fundamentals_now(conn, int(ent["cik"]), t)
    iid, kind = _master_hit(conn, ent["ticker"])
    return _entity_view(ent, fund, t, iid, kind)


def needs_check(ent: Optional[dict], now: int) -> bool:
    """True when the name's SIC / latest-filing check is missing or older than 7 days (plan 4.3): ``refresh_entity`` is due."""
    ts = (ent or {}).get("checked_ts")
    return ts is None or int(now) - int(ts) > CHECK_DAYS * 86400


# the whole list (entities with their latest fundamentals), cached per database file and state version (every write goes through a command)
_SNAP_LOCK = threading.Lock()
_SNAP: Dict[str, Tuple[tuple, List[Tuple[dict, Optional[dict]]], Dict[str, str]]] = {}


def _db_key(conn) -> str:
    try:
        for r in conn.execute("PRAGMA database_list").fetchall():
            if r[1] == "main":
                return str(r[2] or id(conn))
    except Exception:   # noqa: BLE001
        pass
    return str(id(conn))


def _snapshot(conn) -> Tuple[List[Tuple[dict, dict]], Dict[str, str]]:
    """[(entity row, latest fundamentals row (raw) or None)] and the master's stock ids with their kind ({id: EQUITY | ADR | REIT})."""
    rows, kinds, _ver = _snapshot_v(conn)
    return rows, kinds


def _master_kind(kinds: Dict[str, str], ticker) -> Optional[str]:
    """The master kind of the ticker (or its . / - variant) among the stock kinds, else None."""
    for v in _ticker_variants(ticker):
        k = kinds.get(v)
        if k is not None:
            return k
    return None


def _snapshot_v(conn) -> Tuple[List[Tuple[dict, dict]], Dict[str, str], tuple]:
    if not _has_table(conn, "credit_entities"):
        return [], {}, ()
    ver = (get_meta(conn, "state_version", "0"),) + tuple(conn.execute("SELECT COUNT(*), COALESCE(MAX(updated_ts), 0) FROM credit_entities").fetchone()) \
        + tuple(conn.execute("SELECT COUNT(*), COALESCE(MAX(fetched_ts), 0) FROM credit_fundamentals").fetchone())
    key = _db_key(conn)
    with _SNAP_LOCK:
        hit = _SNAP.get(key)
        if hit is not None and hit[0] == ver:
            return hit[1], hit[2], ver
    by_cik: Dict[int, List[dict]] = {}
    for r in conn.execute("SELECT * FROM credit_fundamentals").fetchall():
        by_cik.setdefault(int(r["cik"]), []).append(dict(r))
    rows = []
    for r in conn.execute("SELECT * FROM credit_entities ORDER BY ticker").fetchall():
        e = dict(r)
        rows.append((e, _choose(by_cik.get(int(e["cik"]), []))))
    kinds = {str(r[0]).upper(): str(r[1]) for r in conn.execute("SELECT instrument_id, kind FROM instruments WHERE kind IN ('EQUITY','ADR','REIT')")
             .fetchall()}
    with _SNAP_LOCK:
        _SNAP.clear()                                # one database per process in production; tests make many: keep the latest only
        _SNAP[key] = (ver, rows, kinds)
    return rows, kinds, ver


_VIEWS: Dict[str, Tuple[tuple, str, List[dict]]] = {}


def _views(conn, today: date) -> List[dict]:
    """Every entity as a browse row (``_row_view``) derived as of ``today``, cached per database file, state version and date (the
    typeahead calls this on every key)."""
    rows, kinds, ver = _snapshot_v(conn)
    key = _db_key(conn)
    with _SNAP_LOCK:
        hit = _VIEWS.get(key)
        if hit is not None and hit[0] == ver and hit[1] == today.isoformat():
            return hit[2]
    views = [_row_view(e, fraw, today, kinds) for e, fraw in rows]
    with _SNAP_LOCK:
        _VIEWS.clear()
        _VIEWS[key] = (ver, today.isoformat(), views)
    return views


def _row_view(e: dict, fraw: Optional[dict], today: date, kinds: Dict[str, str]) -> dict:
    fund = _fund_view(fraw, today)
    kind = _master_kind(kinds, e["ticker"])
    d = derive(e, fund, today, adr=(kind == "ADR"), stock=kind is not None)
    f = fund or {}
    try:
        tickers = json.loads(e.get("tickers_json") or "[]")
    except ValueError:
        tickers = []
    return {"cik": int(e["cik"]), "ticker": e["ticker"], "name": e["name"], "exchange": e.get("exchange"), "tickers": tickers,
            "flag": d["flag"], "flag_source": d["flag_source"], "flag_note": d["flag_note"], "limited": d["limited"],
            "status": d["status"], "status_reason": d["status_reason"], "status_words": status_words(d["status_reason"], fund, e, today),
            "offered": d["status"] == OFFERED, "debt_c": f.get("debt_c"), "debt_short": _usd_short(f.get("debt_c")) if f.get("debt_c") else None,
            "debt_end": f.get("end_date"), "debt_recipe": f.get("debt_recipe"), "debt_text": f.get("debt_text"),
            "debt_parts_list": [{"tag": t, "value_c": v} for t, v in (f.get("debt_parts") or {}).items()], "debt_note": f.get("debt_note"),
            "age_days": f.get("age_days"), "age_note": f.get("age_note"), "amber": bool(f.get("amber")), "shares": f.get("shares"),
            "shares_end": f.get("shares_end"), "shares_tag": f.get("shares_tag")}


def _norm_filter(flt) -> str:
    k = str(flt or "all").strip().lower()
    if k not in _FILTER_ALIASES:
        raise RejectError("FILTER", f"unknown filter {flt!r}: use all, nonfinancial, financial, reit or mine", {"filter": flt})
    return _FILTER_ALIASES[k]


def search(conn, q: str = "", limit: int = 50, offset: int = 0, flt: str = "all", held: Optional[Set[int]] = None, sort: str = "ticker",
           desc: bool = False, today: Optional[date] = None, offered_only: bool = False) -> dict:
    """The browse / search list (plan 4.2): ``q`` matches a ticker (exact first, then prefix, any of the CIK's tickers), a CIK, or words of
    the SEC name; ``flt`` all | nonfinancial | financial | reit | mine (``held`` = the CIKs of the account's open CDS contracts); sorted by
    ticker, name or debt (relevance first when ``q`` is given); paged.  {total, offset, limit, rows: [{cik, ticker, name, exchange, flag,
    flag_source, status, status_reason, status_words, debt_c, debt_end, age_days, shares, ...}], filter, sort, desc, caption}."""
    t = _today(today)
    k = _norm_filter(flt)
    srt = str(sort or "ticker").lower()
    if srt not in ("ticker", "name", "debt"):
        raise RejectError("SORT", f"unknown sort {sort!r}: use ticker, name or debt", {"sort": sort})
    limit = max(1, min(500, int(limit or 50)))
    offset = max(0, int(offset or 0))
    held = {int(x) for x in (held or set())}
    qq = str(q or "").strip().upper()
    words = [w for w in qq.replace(",", " ").split() if w]
    picked: List[Tuple[tuple, dict]] = []
    for v in _views(conn, t):
        rank = 9
        if qq:
            tick = str(v["ticker"]).upper()
            others = [str(x).upper() for x in (v.get("tickers") or [])]
            name = str(v["name"]).upper()
            if tick == qq or qq in _ticker_variants(tick):
                rank = 0
            elif qq in others:
                rank = 1
            elif tick.startswith(qq):
                rank = 2
            elif qq.isdigit() and str(v["cik"]) == qq.lstrip("0"):
                rank = 0
            elif any(o.startswith(qq) for o in others):
                rank = 3
            elif words and all(any(nw.startswith(w) for nw in name.replace(",", " ").replace(".", " ").split()) for w in words):
                rank = 4
            elif qq in name:
                rank = 5
            else:
                continue
        if k == "nonfinancial" and v["flag"]:
            continue
        if k == "financial" and v["flag"] != FINANCIAL:
            continue
        if k == "reit" and v["flag"] != REIT:
            continue
        if k == "mine" and v["cik"] not in held:
            continue
        if offered_only and not v["offered"]:
            continue
        if srt == "debt":                                   # names without debt last in both directions
            sk = (v["debt_c"] is None, -(v["debt_c"] or 0) if desc else (v["debt_c"] or 0), v["ticker"])
        elif srt == "name":
            sk = (str(v["name"]).upper(), v["ticker"])
        else:
            sk = (v["ticker"],)
        picked.append((rank, sk, v))
    picked.sort(key=lambda x: x[1], reverse=bool(desc) and srt != "debt")
    if qq:
        picked.sort(key=lambda x: x[0])                     # stable: relevance first, the chosen column within it
    page = [dict(v) for _r, _k, v in picked[offset:offset + limit]]          # copies: the cached views are shared
    cap = next((f.get("caption") for f in FILTERS if f["key"] == k), None)
    return {"total": len(picked), "offset": offset, "limit": limit, "rows": page, "q": q or "", "filter": k, "sort": srt, "desc": bool(desc),
            "caption": cap, "filters": FILTERS, "as_of": t.isoformat()}


# ---- the refresh progress (in memory, per database file: the desk's loading line) --------------------------------------------------------
_PLOCK = threading.Lock()
_PROGRESS: Dict[str, dict] = {}
_IDLE = {"state": "idle", "mode": None, "started_ts": None, "finished_ts": None, "step": "", "done": 0, "total": 0, "error": None, "result": None}


def _norm_path(p) -> str:
    return os.path.normcase(os.path.abspath(str(p))) if p else ""


def _pkey(services) -> str:
    """The progress key of a runtime: its database file."""
    db = getattr(services, "db", None) if services is not None else None
    return _norm_path(getattr(db, "path", None) or getattr(services, "db_path", None))


def _progress(key: Optional[str], **kw) -> None:
    if not key:
        return
    with _PLOCK:
        cur = _PROGRESS.setdefault(key, dict(_IDLE))
        inc = kw.pop("done_inc", None)
        if inc:
            cur["done"] = int(cur.get("done") or 0) + int(inc)
        cur.update(kw)


def progress(key: Optional[str] = None) -> dict:
    """The refresh progress of one database file (``key``: its path); idle when no refresh ran in this process."""
    with _PLOCK:
        return dict(_PROGRESS.get(_norm_path(key) if key else "", _IDLE))


def _last_run(conn) -> Optional[dict]:
    if not _has_table(conn, "credit_refresh_runs"):
        return None
    r = conn.execute("SELECT * FROM credit_refresh_runs ORDER BY run_ts DESC LIMIT 1").fetchone()
    return None if r is None else dict(r)


def universe_status(conn, today: Optional[date] = None, services=None) -> dict:
    """{offered, listed, by_status {OFFERED: n, NO_DEBT: n, ...}, by_flag {FINANCIAL: {tags: n, sic: n}, REIT: {...}}, refreshed_ts,
    source, state (ready | loading | refreshing | empty | offline | error), message (the desk's words), last_run}.  ``services`` (the
    route's runtime) says "offline" before the refresher's first run; without it the refresher's last run in this process says so."""
    t = _today(today)
    rows = _views(conn, t)
    by_status: Dict[str, int] = {}
    by_flag: Dict[str, Dict[str, int]] = {}
    listed = offered = 0
    for d in rows:
        key = OFFERED if d["status"] == OFFERED else str(d["status_reason"])
        by_status[key] = by_status.get(key, 0) + 1
        if d.get("exchange"):
            listed += 1
        if d["status"] == OFFERED:
            offered += 1
        if d["flag"]:
            fb = by_flag.setdefault(d["flag"], {})
            fb[str(d["flag_source"])] = fb.get(str(d["flag_source"]), 0) + 1
    last = _last_run(conn)
    p = progress(_db_key(conn))
    if services is not None and is_offline(services) and p.get("state") != "running":
        p = dict(p, state="offline")
    running = p.get("state") == "running"
    if running and not rows:
        state = "loading"
        msg = "loading the reference-entity list from SEC EDGAR (first run, about 2-3 minutes)"
    elif rows:
        state = "refreshing" if running else "ready"
        when = ny_of(int(last["run_ts"])).strftime("%Y-%m-%d %H:%M ET") if last else "time unknown"
        msg = f"{offered:,} reference entities offered of {listed:,} listed, from SEC EDGAR, refreshed {when}"
    elif p.get("state") == "offline":
        state, msg = "offline", "the reference-entity list is not loaded: the terminal runs offline (SEC EDGAR is not contacted)"
    elif p.get("state") == "error":
        state, msg = "error", f"the reference-entity list could not be loaded from SEC EDGAR ({p.get('error')}); it is tried again by itself"
    else:
        state, msg = "empty", "the reference-entity list is not loaded yet: it loads from SEC EDGAR at the first refresh (about 2-3 minutes)"
    return {"offered": offered, "listed": listed, "entities": len(rows), "by_status": by_status, "by_flag": by_flag,
            "refreshed_ts": None if last is None else int(last["run_ts"]), "source": "SEC EDGAR: company_tickers_exchange.json and the XBRL frames API",
            "state": state, "message": msg, "progress": {k: p.get(k) for k in ("state", "mode", "step", "done", "total", "started_ts", "error")},
            "last_run": last, "minimum_debt_c": MIN_DEBT_C, "exchanges": sorted(EXCHANGES.values())}


# ---- the light commands (plan 6.2) ---------------------------------------------------------------------------------------------------------
def _ensure(conn) -> None:
    for s in SCHEMA:
        conn.execute(s)


def _recompute(conn, cik: int, ts: int, today: date) -> bool:
    """Derive the stored status / flag columns of one entity again from the stored state; True when they changed."""
    r = conn.execute("SELECT * FROM credit_entities WHERE cik=?", (int(cik),)).fetchone()
    if r is None:
        return False
    e = dict(r)
    fund = fundamentals_now(conn, int(cik), today)
    _iid, kind = _master_hit(conn, e["ticker"])
    d = derive(e, fund, today, adr=(kind == "ADR"), stock=kind is not None)
    new = (d["status"], d["status_reason"], d["flag"] or "", d["flag_source"])
    if new == (e["status"], e["status_reason"], e["flag"] or "", e["flag_source"]):
        return False
    conn.execute("UPDATE credit_entities SET status=?, status_reason=?, flag=?, flag_source=?, updated_ts=? WHERE cik=?", new + (int(ts), int(cik)))
    return True


ENT_FIELDS = ("ticker", "name", "exchange", "tickers_json", "sic", "sic_desc", "latest_form", "latest_filed", "checked_ts", "check_json")


def h_entities_upsert(ctx, payload: dict) -> dict:
    """``credit.entities_upsert {rows: [{cik, ticker?, name?, exchange? (null = no longer listed), tickers?, sic?, sic_desc?, latest_form?,
    latest_filed?, checked_ts?, check?}], run?}`` (light): each row merges only the fields it carries (a universe refresh never overwrites
    a name's SIC check, a check never overwrites the universe fields); the status and flag columns are then derived again from the stored
    state.  ``run`` records one refresh in ``credit_refresh_runs``."""
    conn = ctx.conn
    _ensure(conn)
    ts = int(ctx.ts)
    today = ny_of(ts).date()
    new = upd = 0
    touched = []
    for r in payload.get("rows") or []:
        cik = _to_int(r.get("cik"))
        if cik is None or cik <= 0:
            raise RejectError("ROW", f"credit.entities_upsert: bad cik {r.get('cik')!r}")
        vals: Dict[str, object] = {}
        for k in ENT_FIELDS:
            if k in r:
                vals[k] = r[k]
        if "tickers" in r:
            vals["tickers_json"] = json.dumps([str(x) for x in (r.get("tickers") or [])])
        if "check" in r:
            vals["check_json"] = canonical_json(r.get("check") or {})
        old = conn.execute("SELECT * FROM credit_entities WHERE cik=?", (cik,)).fetchone()
        if old is None:
            if not vals.get("ticker") or not vals.get("name"):
                continue                                  # a check of a name the universe does not list (yet): nothing to merge into
            cols = ["cik", "status", "updated_ts"] + list(vals)
            conn.execute(f"INSERT INTO credit_entities({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                         [cik, NOT_OFFERED, ts] + [vals[k] for k in vals])
            new += 1
        else:
            changed = {k: v for k, v in vals.items() if old[k] != v}
            if changed:
                conn.execute(f"UPDATE credit_entities SET {', '.join(k + '=?' for k in changed)}, updated_ts=? WHERE cik=?",
                             list(changed.values()) + [ts, cik])
                upd += 1
        touched.append(cik)
    derived = sum(1 for cik in touched if _recompute(conn, cik, ts, today))
    run = payload.get("run")
    if isinstance(run, dict) and run.get("run_ts") is not None:
        conn.execute("INSERT OR REPLACE INTO credit_refresh_runs(run_ts, mode, quarters, requests, bytes, seconds, listed, offered, entities_changed,"
                     " fundamentals_changed, unknown_exchanges_json, errors_json, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (int(run["run_ts"]), str(run.get("mode") or ""), str(run.get("quarters") or ""), int(run.get("requests") or 0), int(run.get("bytes") or 0),
                      str(run.get("seconds") or "0"), int(run.get("listed") or 0), int(run.get("offered") or 0), int(run.get("entities_changed") or 0),
                      int(run.get("fundamentals_changed") or 0), canonical_json(run.get("unknown_exchanges") or {}), canonical_json(run.get("errors") or []),
                      None if run.get("note") is None else str(run.get("note"))[:500]))
    return {"new": new, "updated": upd, "status_changed": derived, "posted": False}


FUND_FIELDS = ("filed", "form", "debt_c", "debt_recipe", "debt_parts_json", "liabilities_c", "shares", "shares_end", "shares_tag", "source")


def h_fundamentals_upsert(ctx, payload: dict) -> dict:
    """``credit.fundamentals_upsert {rows: [credit_fundamentals row]}`` (light): a new (cik, end, accn) is inserted with its ``known_from``
    (the New York date the refresh first saw it); an existing one keeps its ``known_from`` and takes the new values (a companyfacts row of
    the same filing adds ``filed`` / ``form``).  The entities of those CIKs have their status derived again."""
    conn = ctx.conn
    _ensure(conn)
    ts = int(ctx.ts)
    today = ny_of(ts).date()
    new = upd = 0
    ciks = set()
    for r in payload.get("rows") or []:
        cik, end, accn = _to_int(r.get("cik")), _iso(r.get("end_date")), str(r.get("accn") or "")
        if cik is None or end is None:
            raise RejectError("ROW", "credit.fundamentals_upsert: a row needs cik and end_date")
        kf = _iso(r.get("known_from")) or today.isoformat()
        old = conn.execute("SELECT * FROM credit_fundamentals WHERE cik=? AND end_date=? AND accn=?", (cik, end, accn)).fetchone()
        vals = {k: r.get(k) for k in FUND_FIELDS}
        if vals.get("debt_parts_json") is None:
            vals["debt_parts_json"] = "{}"
        if not vals.get("source"):
            vals["source"] = SRC_LABEL
        if old is None:
            conn.execute("INSERT INTO credit_fundamentals(cik, end_date, accn, known_from, filed, form, debt_c, debt_recipe, debt_parts_json, liabilities_c,"
                         " shares, shares_end, shares_tag, source, fetched_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cik, end, accn, kf, vals["filed"], vals["form"], vals["debt_c"], vals["debt_recipe"], vals["debt_parts_json"], vals["liabilities_c"],
                          vals["shares"], vals["shares_end"], vals["shares_tag"], vals["source"], int(r.get("fetched_ts") or ts)))
            new += 1
        else:
            if vals["filed"] is None:
                vals["filed"] = old["filed"]
            if vals["form"] is None:
                vals["form"] = old["form"]
            changed = {k: v for k, v in vals.items() if old[k] != v}
            if changed:
                conn.execute(f"UPDATE credit_fundamentals SET {', '.join(k + '=?' for k in changed)}, fetched_ts=? WHERE cik=? AND end_date=? AND accn=?",
                             list(changed.values()) + [int(r.get("fetched_ts") or ts), cik, end, accn])
                upd += 1
        ciks.add(cik)
    derived = sum(1 for c in sorted(ciks) if _recompute(conn, c, ts, today))
    return {"new": new, "updated": upd, "status_changed": derived, "posted": False}


def _send(services, kind: str, prefix: str, rows: List[dict], run: Optional[dict] = None) -> dict:
    """Write ``rows`` through the bus in chunks of 500 (request id = the payload's content hash); the run record rides on the last chunk."""
    bus = getattr(services, "bus", None)
    if bus is None:
        raise RuntimeError(f"{kind}: no command bus")
    out = {"new": 0, "updated": 0, "status_changed": 0, "commands": 0}
    chunks = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)] or ([[]] if run is not None else [])
    for i, part in enumerate(chunks):
        payload: dict = {"rows": part}
        if run is not None and i == len(chunks) - 1:
            payload["run"] = run
        res = bus.execute(Command(kind, payload, _rid(prefix, payload), "worker"))
        if not res.ok:
            raise RuntimeError(f"{kind} failed: {res.code} {res.message}")
        out["commands"] += 1
        for k in ("new", "updated", "status_changed"):
            out[k] += int((res.body or {}).get(k) or 0)
    return out


# ---- the refresher (B2 task 5, plan 4.3) ----------------------------------------------------------------------------------------------------
_RUN_LOCK = threading.Lock()


def refresh(services, now: int, getter=None) -> str:
    """The ``cds_fundamentals`` refresher (quote worker, called as ``fn(services, now)``): a full run (tickers file + 5 quarters) when there
    is no universe yet or on a Monday not yet done; else a daily run (2 quarters) unless one already ran today.  Returns words for the
    worker's last_result.  ``getter`` (tests only) serves the fixtures; offline returns at once whatever it is."""
    if is_offline(services):
        _progress(_pkey(services), state="offline", error=None)
        return "offline: SEC EDGAR is not contacted"
    db = getattr(services, "db", None)
    if db is None:
        return "no database"
    today = ny_of(int(now)).date()
    with shared_reader(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM credit_entities").fetchone()[0] if _has_table(conn, "credit_entities") else 0
        last = _last_run(conn)
        full_today = _has_table(conn, "credit_refresh_runs") and conn.execute(
            "SELECT 1 FROM credit_refresh_runs WHERE mode='full' AND run_ts >= ?", (epoch_of(today, "00:00"),)).fetchone() is not None
    if n == 0:
        mode = "full"
    elif today.weekday() == 0 and not full_today:
        mode = "full"
    elif last is not None and ny_of(int(last["run_ts"])).date() == today:
        return "already refreshed today"
    else:
        mode = "daily"
    res = run_refresh(services, int(now), mode, getter=getter)
    return (f"{mode}: {res['offered']} offered of {res['listed']} listed; {res['requests']} requests, {res['bytes']} bytes, {res['seconds']} s;"
            f" {res['fundamentals_changed']} balance sheets and {res['entities_changed']} names changed")


def run_refresh(services, now: int, mode: str = "full", getter=None, qtrs: Optional[Sequence[Tuple[int, int]]] = None) -> dict:
    """One refresh: ``full`` = the tickers file and the last 5 quarters, ``daily`` = the stored universe and 2 quarters.  Computes every
    listed CIK's balance sheet from the frames and writes the changed rows (fundamentals first, then the entities with the run record).
    Raises on a failure before any write (the refresher's back-off retries)."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("a refresh of the reference entities is already running")
    t0 = _time.monotonic()
    stats = Stats()
    pk = _pkey(services)
    try:
        today = ny_of(int(now)).date()
        qs = list(qtrs) if qtrs is not None else quarters(today, FULL_QUARTERS if mode == "full" else DAILY_QUARTERS)
        total = len(qs) * len(FRAME_SPECS) + (1 if mode == "full" else 0)
        _progress(pk, state="running", mode=mode, started_ts=int(now), finished_ts=None, step="starting", done=0, total=total, error=None)
        db = services.db
        with shared_reader(db) as conn:
            stored = {int(r["cik"]): dict(r) for r in conn.execute("SELECT * FROM credit_entities").fetchall()} if _has_table(conn, "credit_entities") else {}
        universe = None
        unknown: Dict[str, int] = {}
        if mode == "full":
            _progress(pk, step="SEC tickers file", done_inc=1)
            universe = fetch_universe(services, getter, stats)
            unknown = dict(universe["unknown_exchanges"])
            if unknown and getattr(services, "log", None) is not None:
                services.log.warning("SEC tickers file: exchange strings outside the allowlist dropped: %s", unknown)
            listed = universe["entities"]
        else:
            listed = {c: {"cik": c, "name": e["name"], "ticker": e["ticker"], "exchange": e.get("exchange"),
                          "tickers": json.loads(e.get("tickers_json") or "[]")} for c, e in stored.items() if e.get("exchange")}
        fr = fetch_frames(services, qs, set(listed), getter, stats, pkey=pk, names={c: str(u.get("name") or "") for c, u in listed.items()})
        known_from = today.isoformat()
        fund_rows: List[dict] = []
        with shared_reader(db) as conn:
            old_rows: Dict[Tuple[int, str, str], dict] = {}
            if _has_table(conn, "credit_fundamentals"):
                for r in conn.execute("SELECT * FROM credit_fundamentals").fetchall():
                    old_rows[(int(r["cik"]), r["end_date"], r["accn"])] = dict(r)
        for cik, facts in fr["facts"].items():
            row = build_fundamentals(cik, facts, known_from, int(now), "")
            if row is None:
                continue
            old = old_rows.get((cik, row["end_date"], row["accn"]))
            if old is not None:
                row["known_from"] = old["known_from"]
                if str(old.get("source") or "").startswith("SEC companyfacts"):
                    continue                              # the name's own companyfacts row of the same filing is kept as it is
                if all(old.get(k) == row.get(k) for k in FUND_FIELDS if k not in ("filed", "form")):
                    continue                              # unchanged
            fund_rows.append(row)
        ent_rows: List[dict] = []
        for cik, u in listed.items():
            e = stored.get(cik)
            if e is None or e["ticker"] != u["ticker"] or e["name"] != u["name"] or e.get("exchange") != u.get("exchange") \
                    or json.loads(e.get("tickers_json") or "[]") != list(u.get("tickers") or []):
                ent_rows.append({"cik": cik, "ticker": u["ticker"], "name": u["name"], "exchange": u.get("exchange"), "tickers": list(u.get("tickers") or [])})
        if mode == "full":
            for cik, e in stored.items():
                if cik not in listed and e.get("exchange"):
                    ent_rows.append({"cik": cik, "exchange": None})        # no longer listed on a kept exchange: NOT_LISTED
        # the names whose status only moved with the date (a balance sheet crossing 15 months): their stored columns are derived again
        changed = {r["cik"] for r in fund_rows} | {r["cik"] for r in ent_rows}
        with shared_reader(db) as conn:
            snap, kinds = _snapshot(conn)
            for e, fraw in snap:
                if e["cik"] in changed:
                    continue
                kind = _master_kind(kinds, e["ticker"])
                d = derive(e, _fund_view(fraw, today), today, adr=(kind == "ADR"), stock=kind is not None)
                if (d["status"], d["status_reason"]) != (e["status"], e["status_reason"]):
                    ent_rows.append({"cik": int(e["cik"])})
        _progress(pk, step="writing")
        fres = _send(services, "credit.fundamentals_upsert", "credit:fund", fund_rows)
        eres = _send(services, "credit.entities_upsert", "credit:ent", ent_rows)
        with shared_reader(db) as conn:
            st = universe_status(conn, today)          # listed / offered as the stored state now says
        run = {"run_ts": int(now), "mode": mode, "quarters": ",".join(frame_period(y, q) for y, q in qs), "requests": stats.requests, "bytes": stats.bytes,
               "seconds": str(Decimal(str(round(_time.monotonic() - t0, 1)))), "listed": st["listed"], "offered": st["offered"],
               "entities_changed": len(ent_rows), "fundamentals_changed": len(fund_rows), "unknown_exchanges": unknown, "errors": fr["errors"][:20],
               "note": f"{len(fr['missing'])} frames without data (404); {sum(o['facts'] for o in fr['others'].values())} facts filed under"
                       f" {len(fr['others'])} other CIK(s) were another listed registrant's joint-filing figures and were left out"}
        _send(services, "credit.entities_upsert", "credit:ent", [], run)
        out = {"mode": mode, "quarters": run["quarters"], "requests": stats.requests, "bytes": stats.bytes, "seconds": run["seconds"],
               "listed": st["listed"], "offered": st["offered"], "by_status": st["by_status"], "by_flag": st["by_flag"],
               "entities_changed": len(ent_rows), "fundamentals_changed": len(fund_rows), "unknown_exchanges": unknown,
               "rows_by_exchange": (universe or {}).get("rows_by_exchange"), "frame_rows": fr["rows"], "missing_frames": fr["missing"],
               "errors": fr["errors"], "fund_write": fres, "ent_write": eres, "others": fr["others"]}
        _progress(pk, state="idle", finished_ts=int(now), step="done", result={k: out[k] for k in ("mode", "listed", "offered", "requests", "bytes", "seconds")})
        return out
    except Exception as e:
        _progress(pk, state="error", error=f"{type(e).__name__}: {e}"[:200])
        raise
    finally:
        _RUN_LOCK.release()


# ---- on demand: one name's SEC check (B2 task 4) ----------------------------------------------------------------------------------------
def refresh_entity(services, cik: int, getter=None) -> dict:
    """One name, on a worker thread only (the CDS model's input queue): ``submissions`` (the SIC code and its flag, the latest periodic
    form) and, when that filing's period is newer than the stored balance sheet, ``companyfacts`` (the same recipe on the non-dimensional
    instants of 10-K / 10-Q and their amendments; over the 16 MB cap, or still without the newest filing, the stored row stays and the
    name says so).  A 20-F / 40-F (or IFRS-only facts) makes it FOREIGN_FILER.  Writes the entity's check fields and any new balance
    sheet through the light commands.  {ok, cik, ticker, status, status_reason, status_words, flag, flag_source, requests, changed, note}."""
    cik = int(cik)
    if getter is None and is_offline(services):
        return {"ok": False, "cik": cik, "code": "OFFLINE", "message": "offline: SEC EDGAR is not contacted", "requests": 0, "changed": False}
    db = services.db
    now = int(services.clock.epoch())
    today = ny_of(now).date()
    stats = Stats()
    with shared_reader(db) as conn:
        ent = _ent_row(conn, cik)
        fund = fundamentals_now(conn, cik, today)
    if ent is None:
        return {"ok": False, "cik": cik, "code": "ENTITY_UNKNOWN", "message": f"CIK {cik} is not in the reference-entity list", "requests": 0,
                "changed": False}
    sub = _json(sec_get(services, SUBMISSIONS_URL.format(cik=cik), getter, stats))
    sic = sub.get("sic") if isinstance(sub, dict) else None
    sic_desc = sub.get("sicDescription") if isinstance(sub, dict) else None
    latest = _latest_periodic(sub)
    check = dict(_check(ent))
    check.pop("note", None)
    check["latest_report"] = (latest or {}).get("report")
    check["latest_accn"] = (latest or {}).get("accn")
    note = None
    new_fund = None
    foreign = bool(latest and latest["form"].startswith(FOREIGN_FORMS))
    if not foreign and latest and latest.get("report") and (fund is None or latest["report"] > (fund.get("end_date") or "")):
        try:
            raw = sec_get(services, COMPANYFACTS_URL.format(cik=cik), getter, stats)
            if len(raw or b"") > config.MAX_BYTES:
                # the 16 MB cap applies to the file as read (the network layer caps the compressed bytes only): a larger JSON is not parsed
                raise FetchError("response too large", retryable=False)
            doc = _json(raw)
        except FetchError as e:
            too_large = e.status is None and "too large" in str(e)
            if not (too_large or e.status == 404):
                raise
            doc = None
            base = f"using balance sheet {fund['end_date']}" if fund else "no balance sheet in the XBRL frames either"
            note = (f"the newest {latest['form']} (period {latest['report']}) is in a company-facts file over the 16 MB limit; {base}"
                    if too_large else f"the newest {latest['form']} is not in the SEC's XBRL API yet; {base}")
        if doc is not None:
            facts, ifrs_only = facts_from_companyfacts(doc)
            if ifrs_only:
                check["ifrs"] = True
                foreign = True
            else:
                row = build_fundamentals(cik, facts, today.isoformat(), now, "")
                # a joint filing's figures are its parent's: the SEC's API lists a parent-and-subsidiaries filing's consolidated facts under a
                # co-registrant's CIK (Entergy Corp's 2026-06-30 10-Q under Entergy Arkansas) - a balance sheet whose filing was submitted by
                # ANOTHER listed registrant is that registrant's, never taken as this company's
                parent = None
                sub = submitter_cik(row["accn"]) if row is not None else None
                if sub is not None and sub != cik:
                    with shared_reader(db) as conn:
                        parent = _ent_row(conn, sub)
                    if parent is not None and not parent.get("exchange"):
                        parent = None
                # a company that files current reports but whose newest balance sheet WITH a standard debt tag is past the 15-month limit
                # reports its debt in its own tags since (Ford: R3 291,000,000 at 2020-12-31): that old row is not its balance sheet - the
                # name stays "no debt reported", with the old date named, rather than "a balance sheet 70 months old"
                filing = latest.get("report") and (today - date.fromisoformat(latest["report"])).days <= STALE_DAYS
                too_old = row is not None and (today - date.fromisoformat(row["end_date"])).days > STALE_DAYS
                if parent is not None:
                    pname = " ".join(_STATE_TAG.sub(" ", str(parent["name"])).split())
                    note = (f"its newest balance sheet in the SEC's XBRL API ({row['end_date']}, accession {row['accn']}) is a joint filing submitted by"
                            f" {pname} ({parent['ticker']}): those are {pname}'s consolidated figures, not this company's"
                            + (f"; using balance sheet {fund['end_date']}" if fund else ""))
                elif row is not None and fund is None and too_old and filing:
                    note = (f"its newest balance sheet with a standard debt tag is {row['end_date']} ({row['debt_recipe'] or 'no recipe'},"
                            f" USD {_usd(row['debt_c'])}); its later reports (the newest {latest['form']}, period {latest['report']}) give its debt"
                            " in the company's own tags")
                elif row is not None and (fund is None or row["end_date"] > fund["end_date"] or
                                          (row["end_date"] == fund["end_date"] and row["accn"] != fund.get("accn"))):
                    row["source"] = f"SEC companyfacts ({row.get('form') or 'filing'} filed {row.get('filed') or '?'})"
                    new_fund = row
                elif fund is not None:
                    note = (f"the newest {latest['form']} (period {latest['report']}, filed {latest['filed']}) has no standard debt tag in the SEC's"
                            f" XBRL API yet; using balance sheet {fund['end_date']}")
    if note:
        check["note"] = note
    if new_fund is not None:
        _send(services, "credit.fundamentals_upsert", "credit:fund", [new_fund])
    upd = {"cik": cik, "sic": None if sic in (None, "") else str(sic), "sic_desc": None if not sic_desc else str(sic_desc)[:120],
           "latest_form": (latest or {}).get("form"), "latest_filed": (latest or {}).get("filed"), "checked_ts": now, "check": check}
    _send(services, "credit.entities_upsert", "credit:ent", [upd])
    with shared_reader(db) as conn:
        out = entity(conn, cik, today)
    return {"ok": True, "cik": cik, "ticker": out["ticker"], "status": out["status"], "status_reason": out["status_reason"],
            "status_words": out["status_words"], "flag": out["flag"], "flag_source": out["flag_source"], "requests": stats.requests, "bytes": stats.bytes,
            "changed": new_fund is not None, "note": note, "foreign": foreign}


def instrument_for(services, ent: dict) -> Tuple[Optional[str], str]:
    """The reference equity's instrument id: the master id for the ticker, then its . / - variant (BRK.B / BRK-B); else the quote worker
    is asked to resolve the ticker (a Yahoo lookup on its own thread): ('ORCL', 'ok') | (None, 'resolving') | (None, 'error: <words>') |
    (None, 'unavailable: <words>')."""
    tick = str((ent or {}).get("ticker") or "").strip().upper()
    if not tick:
        return None, "unavailable: the entity has no ticker"
    master = getattr(services, "instruments", None)
    if master is not None:
        for v in _ticker_variants(tick):
            try:
                inst = master.get(v)
            except Exception:   # noqa: BLE001 - a master that cannot read answers "not found"
                inst = None
            if inst is not None and getattr(inst, "kind", None) in STOCK_KINDS:
                return inst.instrument_id, "ok"
    w = getattr(services, "worker", None)
    if w is None or not hasattr(w, "resolve"):
        return None, "unavailable: no quote worker to look the ticker up"
    st = w.resolve(tick) or {}
    if st.get("status") == "error":
        return None, f"error: {st.get('message') or 'the ticker lookup failed'}"
    return None, "resolving"


# ---- routes, registry, binding --------------------------------------------------------------------------------------------------------------
def _held_ciks(conn, account_id: Optional[str]) -> Set[int]:
    """The CIKs of the account's OPEN single-name CDS contracts (the "my contracts" filter)."""
    if not account_id or not _has_table(conn, "otc_contracts"):
        return set()
    out = set()
    for r in conn.execute("SELECT terms_json FROM otc_contracts WHERE account_id=? AND kind='CDS' AND status='OPEN'", (str(account_id),)).fetchall():
        try:
            c = _to_int(json.loads(r[0] or "{}").get("cik"))
        except ValueError:
            c = None
        if c is not None:
            out.add(c)
    return out


def register_routes(router) -> None:
    """``GET /api/otc/cds/entities?q=&filter=&limit=&offset=&sort=&desc=&account_id=`` (plan 4.2, 6.5): the browse / search list with the
    universe line; each row carries ``model_5y`` = the spread computed in this session (``cds_model.last_models``) or None ("on request")."""
    from .api import ApiError

    def entities(req):
        rt = req.rt
        today = rt.clock.today()
        limit = req.q_int("limit", 50, 1, 500)
        offset = req.q_int("offset", 0, 0, 10_000_000)
        desc = str(req.q("desc") or "").lower() in ("1", "true", "yes", "desc")
        try:
            with shared_reader(rt.db) as conn:
                held = _held_ciks(conn, req.q("account_id"))
                out = search(conn, req.q("q") or "", limit, offset, req.q("filter") or "all", held, sort=req.q("sort") or "ticker", desc=desc,
                             today=today, offered_only=str(req.q("offered") or "").lower() in ("1", "true", "yes"))
                out["universe"] = universe_status(conn, today, services=rt)
        except RejectError as e:
            raise ApiError(400, e.code, e.message, e.details)
        model = getattr(rt, "cds_model", None)
        fn = getattr(model, "last_models", None) if model is not None else None
        got = {}
        if callable(fn) and out["rows"]:
            try:
                got = fn([r["cik"] for r in out["rows"]]) or {}
            except Exception:   # noqa: BLE001 - the column then says "on request"
                got = {}
        for r in out["rows"]:
            m = got.get(r["cik"]) if isinstance(got, dict) else None
            r["model_5y"] = None if not isinstance(m, dict) else {k: m.get(k) for k in ("ok", "spread_bp", "bucket", "bucket_label", "quality", "valuation_date",
                                                                                         "label", "code", "reason") if k in m}
        out["account_id"] = req.q("account_id")
        out["held"] = len(held)
        return out

    router.add("GET", r"/api/otc/cds/entities", entities)


def register(bus, services=None) -> None:
    bus.register("credit.entities_upsert", h_entities_upsert, light=True)
    bus.register("credit.fundamentals_upsert", h_fundamentals_upsert, light=True)


def bind(rt) -> None:
    """The tables (C4 ``ensure_schema_ext``) and the ``cds_fundamentals`` refresher on the quote worker (C14)."""
    db = getattr(rt, "db", None)
    if db is not None and hasattr(db, "ensure_schema_ext"):
        db.ensure_schema_ext(SOURCE, SCHEMA)
    if getattr(rt, "clock", None) is not None:
        _CLOCK[0] = rt.clock
    try:
        from .quotes import worker as _worker
    except ImportError:
        _worker = None
    if _worker is not None:
        _worker.PROVIDER_LABEL.setdefault(PROVIDER, "SEC EDGAR")       # the health row of the SEC requests, in words (as a refresher's label is)
        _worker.register_refresher(REFRESHER, None, refresh, run_after_et=REFRESH_ET, label="SEC EDGAR reference entities (single-name CDS)")


__all__ = ["MIN_DEBT_C", "STALE_DAYS", "AGE_WARN_DAYS", "OFFERED", "NOT_OFFERED", "FINANCIAL", "REIT", "REASONS", "FILTERS", "DEBT_TAGS", "OTHER_TAGS",
           "FLAG_TAGS", "FRAME_SPECS", "entity", "search", "fundamentals_on", "fundamentals_now", "refresh_entity", "instrument_for", "universe_status",
           "split_adjusted_shares", "needs_check", "compute_debt", "build_fundamentals", "tags_flag", "derive", "status_words", "parse_universe",
           "parse_frame", "parse_frame_rows", "name_key", "submitter_cik",
           "other_registrant", "has_listed_common", "fetch_universe", "fetch_frames", "facts_from_companyfacts", "quarters", "sec_get", "is_offline", "refresh", "run_refresh",
           "progress", "Pacer", "Stats", "h_entities_upsert", "h_fundamentals_upsert", "register_routes", "register", "bind"]
