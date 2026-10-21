"""Daily News: ``NewsStore`` (three real sources), the fetchers, and ``attention_summary`` (TERMINAL_DESIGN.md §17.9, §19.21;
CLAUDE.md iron rule 2; directive §3.1).  WP-22.

Conventions (every one of them is a requirement, not a preference)
- Three sources, three identities.  (1) Yahoo Finance news search (``config.NEWS_YAHOO``) for the market queries
  ``SPY ^GSPC ^TNX ^VIX`` plus every ticker held or working in any OPEN lifetime (≤ ``max_queries`` = 40 queries in all),
  fetched with ``BROWSER_UA`` — Yahoo only, per the UA policy (iron rule 7).  (2) The Federal Reserve press feed
  (``config.NEWS_FED_RSS``) with ``GOV_UA = LoganTerminal/1.0 (loganshaffer87@gmail.com)`` (iron rule 6; the old
  ``finsim/engines/realnews.py`` sent a browser UA to the Fed — not allowed here), parsed with ``xml.etree``.
  (3) The repository's own newspaper editions from ``<repo>/site/reports/index.json`` read from disk (no network).
  The User-Agent is always ``quotes.http.ua_for(url)``; nothing here ever chooses a UA by hand.
- ``refresh(which)`` runs on the quote worker's refresher slot ONLY (``quotes.worker.RatesRefresher`` registers
  ``news_yahoo`` / ``news_fed`` / ``news_paper`` when ``services.news`` exists), never on a request thread.  The network
  happens without any lock; then ONE short writer transaction inserts the new rows, deduplicates by ``(source, link)``
  (the table's UNIQUE constraint; ``INSERT OR IGNORE``), trims the table to the newest ``RETENTION`` = 500 rows and
  recounts the injection flags.  A request thread only ever calls ``items()``, ``editions()``, ``health()``.
- Everything fetched is UNTRUSTED DATA (iron rule 2).  Titles and publishers are HTML-unescaped, tag-stripped,
  whitespace-collapsed, control characters removed and truncated to ``TITLE_LIMIT`` = 300 characters; a link must be an
  ``http(s)`` URL or the item is skipped; an item without a title or without a publish time is skipped (a fetch time
  is not a publish time — nothing is invented).  Text matching the instruction-like patterns (``ignore (all|previous)
  instructions``, ``system prompt``, ``you are now``) is still shown VERBATIM but counted in ``injection_flags``
  (``/api/health.news.injection_flags`` and the health footer); nothing is interpreted, scored or summarised.
- Newspaper rule: the ``am`` / ``pm`` (weekday) and ``sat`` / ``sun`` (weekend) slots are news; the ``learn`` slot is a
  lesson and is skipped.  The window is TODAY's editions (New York date); when today has none yet (before the 06:30
  morning brief) the previous day's editions are used instead, so the column is always "today's editions" as the page
  says.  One item per edition (its title; the summary travels with ``editions()``) and one per ``headlines[]`` entry
  (link = the edition link + ``#headline-N`` so the dedupe key stays unique).  Edition times are the publication
  schedule of the newspaper (``config/settings.yml``): am 06:30, pm 16:30, sat / sun 09:00 ET.
- Failures surface, never silently: a failing fetch keeps the last items, records the source state (``health()``:
  ok / last_ok_ts / last_err_ts / last_error / consecutive_errors), records it in the shared ``ProviderHealth`` when one
  is given (provider names ``yahoo_news`` / ``fed`` / ``newspaper``, so a news failure never trips the quote feed's
  breaker), and the exception propagates to the slot, which logs and counts it.  While the breaker holds a news source
  down, ``refresh`` returns 0 and the state says ``paused_until``.
- ``attention_summary(services, scope)``: every count comes from the ledger tables (``orders``, ``settlements``,
  ``borrows``, ``events``, ``otc_vm``, ``attention``, ``provider_health``), from ``Accounting.last_report()`` /
  ``hold_state()`` and from the optional ``risk`` / ``compliance`` modules — nothing is computed from marks and the page
  computes nothing.  "Today" is the accounting day (D−1 17:00 ET, D 17:00 ET], so a 17:10 collateral call still shows
  the next morning.  A missing optional module answers ``None`` (unavailable) with a note, never 0.
- Integration (WP-11): ``NewsStore(db, clock, index_path=config.newspaper_index(), log=log, health=providers.health)``
  bound as ``runtime.news`` BEFORE the ``QuoteWorker`` is built (the refresher reads ``services.news`` at construction).
"""
from __future__ import annotations

import email.utils
import html
import json
import os
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from .clock import accounting_day, epoch_of, ny_str
from .config import NEWS_FED_RSS, NEWS_INTERVALS, NEWS_PAPER_URL, NEWS_YAHOO, YAHOO_RPS
from .instruments import EQUITY_KINDS
from .quotes.http import FetchError, RateLimiter, http_get, ua_for

SOURCES = ("yahoo", "fed", "newspaper")
MARKET_QUERIES = ("SPY", "^GSPC", "^TNX", "^VIX")
MARKET_NEWS_COUNT = 10
TICKER_NEWS_COUNT = 6
MAX_QUERIES = 40
RETENTION = 500
TITLE_LIMIT = 300
FEED_MAX_BYTES = 2 << 20                      # a news feed above 2 MiB is not a news feed
NEWS_SLOTS = {"am": "06:30", "pm": "16:30", "sat": "09:00", "sun": "09:00"}        # publication schedule, New York time
SKIPPED_SLOTS = ("learn",)                                                         # a lesson, not news
SLOT_LABEL = {"am": "Morning brief", "pm": "Closing brief", "sat": "Weekly Review", "sun": "Week Ahead"}
PAPER_NAME = "Logan's Daily Newspaper"
FED_PUBLISHER = "Federal Reserve Board"
PROVIDER_NAMES = {"yahoo": "yahoo_news", "fed": "fed", "newspaper": "newspaper"}
OPEN_ORDER_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
SEVERITIES = ("ERROR", "WARN", "INFO")
ATTENTION_ITEMS = 25
PROBLEMS_SHOWN = 20

_INSTRUCTION_RE = re.compile(r"ignore\s+(?:all|previous)\s+instructions|system\s+prompt|you\s+are\s+now", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Getter = Callable[..., bytes]


class NewsError(Exception):
    """A news source could not be read (a non-HTTP failure: a bad feed shape, an unreadable newspaper index)."""


# ---- pure helpers ---------------------------------------------------------------------------------------------------------
def sanitize(text, limit: int = TITLE_LIMIT) -> str:
    """Untrusted text → plain text: HTML entities decoded, tags removed, control characters and runs of whitespace
    collapsed, truncated to ``limit`` characters.  The decode happens BEFORE the tag strip so an entity-encoded tag
    cannot survive as markup."""
    s = html.unescape(str(text if text is not None else ""))
    s = _TAG_RE.sub(" ", s)
    s = _CTRL_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    if len(s) > limit:
        s = s[:limit].rstrip()
    return s


def is_instruction_like(text: str) -> bool:
    """True when the text matches one of the instruction-like patterns of §17.9 (counted, never acted on)."""
    return bool(_INSTRUCTION_RE.search(text or ""))


def safe_link(url) -> Optional[str]:
    """An ``http(s)`` URL with a host, no whitespace or control characters, ≤ 2048 chars — else None (the item is skipped)."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u or len(u) > 2048 or _CTRL_RE.search(u) or _WS_RE.search(u):
        return None
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return u


def parse_yahoo_news(raw: bytes, category: str = "markets") -> List[dict]:
    """``{"news": [{title, publisher, link, providerPublishTime, relatedTickers}]}`` → item dicts.  Items without a title,
    without an http(s) link or without a publish time are skipped; tickers are sanitised and capped at 10."""
    try:
        j = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise NewsError(f"Yahoo news: bad JSON ({type(e).__name__})") from None
    news = j.get("news") if isinstance(j, dict) else None
    out: List[dict] = []
    for x in news or []:
        if not isinstance(x, dict):
            continue
        title = sanitize(x.get("title"))
        link = safe_link(x.get("link"))
        t = x.get("providerPublishTime")
        if isinstance(t, str) and t.strip().isdigit():
            t = int(t.strip())
        if not title or not link or isinstance(t, bool) or not isinstance(t, (int, float)) or t <= 0:
            continue
        tickers = [sanitize(s, 20) for s in (x.get("relatedTickers") or []) if isinstance(s, str)]
        tickers = [s for s in tickers if s][:10]
        out.append({"source": "yahoo", "title": title, "publisher": sanitize(x.get("publisher"), 100) or "Yahoo Finance", "link": link,
                    "time": int(t), "tickers": tickers, "category": category})
    return out


def _rfc2822_epoch(text: str) -> Optional[int]:
    try:
        dt = email.utils.parsedate_to_datetime(text.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        return None            # the Fed feed always carries a zone; a naive stamp is not trusted as a time
    return int(dt.timestamp())


def parse_fed_rss(raw: bytes) -> List[dict]:
    """``<rss><channel><item><title/><link/><pubDate/>`` → item dicts (publisher ``Federal Reserve Board``, category
    ``fed``).  Items without a title, link or parseable RFC 2822 ``pubDate`` are skipped."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise NewsError(f"Fed RSS: bad XML ({e})") from None
    out: List[dict] = []
    for item in root.iter("item"):
        title = sanitize(item.findtext("title"))
        link = safe_link(item.findtext("link"))
        t = _rfc2822_epoch(item.findtext("pubDate") or "")
        if not title or not link or t is None:
            continue
        out.append({"source": "fed", "title": title, "publisher": FED_PUBLISHER, "link": link, "time": t, "tickers": [], "category": "fed"})
    return out


def edition_time(d: date, slot: str) -> int:
    """Epoch of the edition's scheduled publication (New York wall time of ``NEWS_SLOTS[slot]`` on ``d``)."""
    return epoch_of(d, NEWS_SLOTS[slot])


def parse_newspaper_index(raw, today: date, base_url: str = NEWS_PAPER_URL) -> Tuple[List[dict], List[dict]]:
    """The repository's ``site/reports/index.json`` → ``(editions, items)`` for today's news editions, or the previous
    day's when today has none yet.  ``editions`` carry date, slot, label, title, summary, headlines, reading_minutes,
    path, link, time; ``items`` are one per edition plus one per headline (see the module conventions)."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise NewsError("newspaper index: not UTF-8") from None
    try:
        idx = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as e:
        raise NewsError(f"newspaper index: bad JSON ({type(e).__name__})") from None
    if not isinstance(idx, list):
        raise NewsError("newspaper index: not a list")
    by_date: Dict[str, List[dict]] = {}
    for e in idx:
        if not isinstance(e, dict):
            continue
        slot = e.get("slot")
        d = e.get("date")
        path = e.get("path")
        if slot in SKIPPED_SLOTS or slot not in NEWS_SLOTS:
            continue
        if not isinstance(d, str) or not _DATE_RE.match(d) or not isinstance(path, str) or not _PATH_RE.match(path) or ".." in path.split("/"):
            continue
        by_date.setdefault(d, []).append(e)
    pick = None
    for cand in (today, today - timedelta(days=1)):
        if cand.isoformat() in by_date:
            pick = cand
            break
    if pick is None:
        return [], []
    editions: List[dict] = []
    items: List[dict] = []
    order = ["am", "sat", "sun", "pm"]
    for e in sorted(by_date[pick.isoformat()], key=lambda x: order.index(x["slot"])):
        slot = e["slot"]
        link = base_url + e["path"]
        when = edition_time(pick, slot)
        title = sanitize(e.get("title")) or f"{PAPER_NAME} {SLOT_LABEL[slot]} {pick.isoformat()}"
        headlines = [sanitize(h) for h in (e.get("headlines") or []) if isinstance(h, str)]
        headlines = [h for h in headlines if h][:12]
        rm = e.get("reading_minutes")
        editions.append({"date": pick.isoformat(), "slot": slot, "label": SLOT_LABEL[slot], "title": title, "summary": sanitize(e.get("summary"), 1200),
                         "headlines": headlines, "reading_minutes": int(rm) if isinstance(rm, int) and not isinstance(rm, bool) else None,
                         "path": e["path"], "link": link, "time": when})
        publisher = f"{PAPER_NAME} ({SLOT_LABEL[slot]})"
        items.append({"source": "newspaper", "title": title, "publisher": publisher, "link": link, "time": when, "tickers": [], "category": "edition"})
        for i, h in enumerate(headlines, start=1):
            items.append({"source": "newspaper", "title": h, "publisher": publisher, "link": f"{link}#headline-{i}", "time": when, "tickers": [], "category": "headline"})
    return editions, items


# ---- the store -----------------------------------------------------------------------------------------------------------
@dataclass
class NewsItem:
    id: int
    source: str
    title: str
    publisher: str
    link: str
    time: int
    tickers: List[str]
    category: Optional[str]
    fetched_ts: int
    flagged: bool

    def to_dict(self) -> dict:
        return {"id": self.id, "source": self.source, "title": self.title, "publisher": self.publisher, "link": self.link, "time": self.time,
                "time_ny": ny_str(self.time), "tickers": list(self.tickers), "category": self.category, "fetched_ts": self.fetched_ts,
                "flagged": self.flagged}


@dataclass
class SourceState:
    """What a source has done lately — shown in the Data Status panel and the news footer ("last fetched hh:mm")."""
    name: str
    ok: Optional[bool] = None
    last_ok_ts: Optional[int] = None
    last_err_ts: Optional[int] = None
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    last_fetched: int = 0            # items the last successful refresh fetched (before dedupe)
    last_new: int = 0                # rows the last successful refresh inserted
    paused_until: Optional[int] = None
    runs: int = 0

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "last_ok_ts": self.last_ok_ts, "last_err_ts": self.last_err_ts, "last_error": self.last_error,
                "consecutive_errors": self.consecutive_errors, "last_fetched": self.last_fetched, "last_new": self.last_new,
                "paused_until": self.paused_until, "runs": self.runs}


class NewsStore:
    """The Daily News cache over the ``news_items`` table.  ``getter`` (tests) replaces ``http_get`` and receives
    ``(url, ua, timeout, accept)``; ``sleep`` is the Yahoo rate limiter's sleep (a no-op in tests); ``health`` is the
    shared ``ProviderHealth`` (optional); ``index_path`` the newspaper index (``config.newspaper_index()`` in production,
    a fixture in tests)."""

    def __init__(self, db, clock, getter: Optional[Getter] = None, index_path: Optional[str] = None, log=None, health=None,
                 sleep: Optional[Callable[[float], None]] = None, market_queries=MARKET_QUERIES, max_queries: int = MAX_QUERIES,
                 base_url: str = NEWS_PAPER_URL, timeout: float = 20.0):
        self.db = db
        self.clock = clock
        self._getter = getter
        self.index_path = index_path
        self.log = log
        self._provider_health = health          # the shared ProviderHealth (optional); health() below is the news state
        self.market_queries = [str(q) for q in market_queries]
        self.max_queries = max(len(self.market_queries), int(max_queries))
        self.base_url = base_url
        self.timeout = float(timeout)
        self._limiter = RateLimiter(YAHOO_RPS, sleep=sleep) if sleep is not None else RateLimiter(YAHOO_RPS)
        self._lock = threading.Lock()
        self._states: Dict[str, SourceState] = {s: SourceState(s) for s in SOURCES}
        self._flags: Optional[int] = None
        self._editions: List[dict] = []
        self._paper_mtime_ns: Optional[int] = None
        self._paper_day: Optional[str] = None

    # ---- network (no lock held) --------------------------------------------------------------------------------------
    def _get(self, url: str, accept: str) -> bytes:
        ua = ua_for(url)
        if self._getter is not None:
            return self._getter(url, ua, self.timeout, accept)
        return http_get(url, ua, timeout=self.timeout, max_bytes=FEED_MAX_BYTES, accept=accept)

    # ---- state ------------------------------------------------------------------------------------------------------------
    def _ok(self, source: str, fetched: int, new: int) -> None:
        now = int(self.clock.epoch())
        with self._lock:
            st = self._states[source]
            st.ok, st.last_ok_ts, st.last_error, st.consecutive_errors, st.last_fetched, st.last_new, st.paused_until, st.runs = \
                True, now, None, 0, fetched, new, None, st.runs + 1
        if self._provider_health is not None:
            self._provider_health.record(PROVIDER_NAMES[source], True)

    def _fail(self, source: str, error: str, status: Optional[int] = None) -> None:
        now = int(self.clock.epoch())
        with self._lock:
            st = self._states[source]
            st.ok, st.last_err_ts, st.last_error, st.consecutive_errors, st.runs = False, now, str(error)[:200], st.consecutive_errors + 1, st.runs + 1
        if self._provider_health is not None:
            self._provider_health.record(PROVIDER_NAMES[source], False, status, str(error)[:200])
        if self.log is not None:
            self.log.warning("news %s failed: %s", source, str(error)[:200])

    def _paused(self, source: str) -> Optional[int]:
        """The breaker's down_until for this source (None when it may run)."""
        if self._provider_health is None:
            return None
        name = PROVIDER_NAMES[source]
        if self._provider_health.allow(name):
            return None
        until = self._provider_health.breaker.down_until(name)
        with self._lock:
            self._states[source].paused_until = until
        return until or int(self.clock.epoch())

    # ---- queries --------------------------------------------------------------------------------------------------------
    def queries(self) -> List[str]:
        """The market queries plus the symbols held or working in any OPEN lifetime (equity kinds directly, an option's
        underlying when it is an equity kind; FX / futures / bonds / private rows are not news-search symbols), sorted,
        deduplicated, capped at ``max_queries`` in all."""
        with self.db.reader() as conn:
            accts = [r["account_id"] for r in conn.execute("SELECT a.account_id FROM accounts a JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE l.status='OPEN'").fetchall()]
            iids: set = set()
            if accts:
                q = ",".join("?" for _ in accts)
                iids.update(r["instrument_id"] for r in conn.execute(f"SELECT DISTINCT instrument_id FROM v_positions WHERE account_id IN ({q})", accts).fetchall())
                sts = ",".join("?" for _ in OPEN_ORDER_STATUSES)
                iids.update(r["instrument_id"] for r in conn.execute(f"SELECT DISTINCT instrument_id FROM orders WHERE status IN ({sts}) AND account_id IN ({q})",
                                                                      list(OPEN_ORDER_STATUSES) + accts).fetchall())
            symbols: set = set()
            for iid in sorted(iids):
                row = conn.execute("SELECT kind, symbol, yahoo_symbol, underlying_id FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
                if row is None:
                    continue
                if row["kind"] == "OPTION" and row["underlying_id"]:
                    row = conn.execute("SELECT kind, symbol, yahoo_symbol, underlying_id FROM instruments WHERE instrument_id=?", (row["underlying_id"],)).fetchone()
                    if row is None:
                        continue
                if row["kind"] in EQUITY_KINDS:
                    sym = sanitize(row["yahoo_symbol"] or row["symbol"], 20)
                    if sym and " " not in sym:
                        symbols.add(sym)
        out = list(self.market_queries)
        for s in sorted(symbols):
            if s not in out:
                out.append(s)
        return out[: self.max_queries]

    # ---- refresh (worker slot only) --------------------------------------------------------------------------------------
    def refresh(self, which: str) -> int:
        """Fetch one source (``yahoo`` | ``fed`` | ``newspaper`` / ``paper`` | ``all``) and store the new rows; returns the
        number of rows inserted.  Worker thread only.  Raises on failure after recording the state (the slot counts it)."""
        w = str(which or "").lower()
        if w == "all":
            return sum(self.refresh(s) for s in SOURCES)
        if w == "paper":
            w = "newspaper"
        if w not in SOURCES:
            raise ValueError(f"unknown news source {which!r}")
        if w == "yahoo":
            return self._refresh_yahoo()
        if w == "fed":
            return self._refresh_fed()
        return self._refresh_newspaper()

    def _refresh_yahoo(self) -> int:
        if self._paused("yahoo") is not None:
            return 0
        rows: List[dict] = []
        failure: Optional[FetchError] = None
        try:
            for q in self.queries():
                n = MARKET_NEWS_COUNT if q in self.market_queries else TICKER_NEWS_COUNT
                url = NEWS_YAHOO.format(q=urllib.parse.quote(q, safe=""), n=n)
                self._limiter.wait()
                try:
                    raw = self._get(url, "application/json")
                except FetchError as e:
                    if e.status in (400, 404):      # a per-symbol fact (unknown query), the provider itself answered
                        continue
                    failure = e
                    break
                rows.extend(parse_yahoo_news(raw, "markets" if q in self.market_queries else "company"))
        except Exception as e:   # noqa: BLE001 — bad shape, decode error: recorded, then re-raised below
            self._store(rows)
            self._fail("yahoo", f"{type(e).__name__}: {e}")
            raise
        new = self._store(rows)
        if failure is not None:
            self._fail("yahoo", str(failure), failure.status)
            raise failure
        self._ok("yahoo", len(rows), new)
        return new

    def _refresh_fed(self) -> int:
        if self._paused("fed") is not None:
            return 0
        try:
            raw = self._get(NEWS_FED_RSS, "application/rss+xml, application/xml, text/xml")
            rows = parse_fed_rss(raw)
        except FetchError as e:
            self._fail("fed", str(e), e.status)
            raise
        except Exception as e:   # noqa: BLE001
            self._fail("fed", f"{type(e).__name__}: {e}")
            raise
        new = self._store(rows)
        self._ok("fed", len(rows), new)
        return new

    def _refresh_newspaper(self) -> int:
        path = self.index_path
        today = self.clock.today()
        try:
            if not path:
                raise NewsError("newspaper index path not configured")
            st = os.stat(path)
            if self._paper_mtime_ns == st.st_mtime_ns and self._paper_day == today.isoformat():
                return 0                                  # unchanged since the last read on the same day
            with open(path, "rb") as fh:
                raw = fh.read(FEED_MAX_BYTES + 1)
            if len(raw) > FEED_MAX_BYTES:
                raise NewsError("newspaper index too large")
            editions, rows = parse_newspaper_index(raw, today, self.base_url)
        except Exception as e:   # noqa: BLE001 — OSError / NewsError / ValueError: recorded, re-raised
            self._fail("newspaper", f"{type(e).__name__}: {e}")
            raise
        new = self._store(rows)
        with self._lock:
            self._editions = editions
            self._paper_mtime_ns, self._paper_day = st.st_mtime_ns, today.isoformat()
        self._ok("newspaper", len(rows), new)
        return new

    # ---- storage --------------------------------------------------------------------------------------------------------
    def _store(self, rows: List[dict]) -> int:
        """One short writer transaction: INSERT OR IGNORE (dedupe on (source, link)), trim to RETENTION, recount flags."""
        now = int(self.clock.epoch())
        n = 0
        with self.db.writer(timeout=5.0) as tx:
            for r in rows:
                cur = tx.conn.execute("INSERT OR IGNORE INTO news_items(source, title, publisher, link, time_ts, tickers_json, category, fetched_ts) VALUES (?,?,?,?,?,?,?,?)",
                                      (r["source"], r["title"], r["publisher"], r["link"], int(r["time"]), json.dumps(list(r.get("tickers") or [])), r.get("category"), now))
                if cur.rowcount == 1:
                    n += 1
            tx.conn.execute("DELETE FROM news_items WHERE id IN (SELECT id FROM news_items ORDER BY time_ts DESC, id DESC LIMIT -1 OFFSET ?)", (RETENTION,))
            flags = self._count_flags(tx.conn)
        with self._lock:
            self._flags = flags
        return n

    @staticmethod
    def _count_flags(conn) -> int:
        return sum(1 for r in conn.execute("SELECT title, publisher FROM news_items").fetchall() if is_instruction_like(f"{r['title']} {r['publisher']}"))

    # ---- reads (any thread) ------------------------------------------------------------------------------------------------
    def items(self, limit: int = 60, source: Optional[str] = None) -> List[NewsItem]:
        """Newest first (``time_ts DESC, id ASC`` so an edition precedes its headlines); ``source`` filters; ``limit`` ≤ 200."""
        lim = max(1, min(int(limit), 200))
        with self.db.reader() as conn:
            if source:
                if source not in SOURCES:
                    raise ValueError(f"unknown news source {source!r}")
                rows = conn.execute("SELECT * FROM news_items WHERE source=? ORDER BY time_ts DESC, id ASC LIMIT ?", (source, lim)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM news_items ORDER BY time_ts DESC, id ASC LIMIT ?", (lim,)).fetchall()
        out = []
        for r in rows:
            try:
                tickers = [str(t) for t in json.loads(r["tickers_json"] or "[]")]
            except ValueError:
                tickers = []
            out.append(NewsItem(int(r["id"]), r["source"], r["title"], r["publisher"], r["link"], int(r["time_ts"]), tickers, r["category"], int(r["fetched_ts"]),
                                is_instruction_like(f"{r['title']} {r['publisher']}")))
        return out

    def editions(self) -> List[dict]:
        with self._lock:
            return [dict(e) for e in self._editions]

    @property
    def injection_flags(self) -> int:
        with self._lock:
            flags = self._flags
        if flags is None:
            with self.db.reader() as conn:
                flags = self._count_flags(conn)
            with self._lock:
                self._flags = flags
        return int(flags)

    def fetched(self) -> Dict[str, Optional[int]]:
        with self._lock:
            return {s: self._states[s].last_ok_ts for s in SOURCES}

    def states(self) -> Dict[str, dict]:
        with self._lock:
            return {s: self._states[s].to_dict() for s in SOURCES}

    def health(self) -> dict:
        """``/api/health.news``: injection flags, last fetch per source, the per-source states, the row count."""
        with self.db.reader() as conn:
            rows = int(conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0])
        return {"injection_flags": self.injection_flags, "fetched": self.fetched(), "sources": self.states(), "rows": rows,
                "intervals": {k.lower(): int(v) for k, v in NEWS_INTERVALS.items()}}


# ---- the attention summary -----------------------------------------------------------------------------------------------
def _in(col: str, values: List[str]) -> Tuple[str, list]:
    return f"{col} IN ({','.join('?' for _ in values)})", list(values)


def _resolve_scope(accounting, conn, scope: str):
    resolver = getattr(accounting, "scope", None) or getattr(accounting, "_scope")
    return resolver(conn, scope)


def _count(conn, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def attention_summary(services, scope: str = "ALL", check: bool = False) -> dict:
    """The "Requires your attention" strip (§17.9): integrity (ok / the failing checks, from the last recorded report —
    ``check=True`` runs the ledger-level set first), INTEGRITY_HOLD, open attention items by severity (+ the newest
    rows), risk warnings and compliance notices (``services.risk`` / ``services.compliance`` when loaded, else None),
    collateral calls today (COLLATERAL_MARKED events with ``delta_c > 0`` plus swap VM payments ``otc_vm.delta_c < 0``
    in the accounting day), recall notices, working orders, pending settlements, provider pauses, the news state.
    Every number is a row count or a recorded report; nothing here touches a mark."""
    acc = services.accounting
    db = services.db
    clock = services.clock
    now = int(clock.epoch())
    aday = accounting_day(clock.now())
    day_start = epoch_of(aday - timedelta(days=1), "17:00")
    notes: List[str] = []
    # integrity first: the last recorded whole-DB report, or the ledger-level set run now (check=True / nothing recorded yet) -
    # before the counts, so a hold cleared by this run is not still counted as an open ERROR below
    report = acc.last_report()
    if check or report is None:
        report = acc.integrity("ledger").to_dict()
    with db.reader() as conn:
        sc = _resolve_scope(acc, conn, scope)
        accounts = [a["account_id"] for a in sc.accounts]
        lids = list(sc.lifetime_ids)
        working = pending = recalls = marks = vm = 0
        open_by = {s: 0 for s in SEVERITIES}
        items: List[dict] = []
        if accounts:
            w, p = _in("account_id", accounts)
            sts, ps = _in("status", list(OPEN_ORDER_STATUSES))
            working = _count(conn, f"SELECT COUNT(*) FROM orders WHERE {sts} AND {w}", ps + p)
            pending = _count(conn, f"SELECT COUNT(*) FROM settlements WHERE status='PENDING' AND {w}", p)
            recalls = _count(conn, f"SELECT COUNT(*) FROM borrows WHERE status='OPEN' AND recall_notice IS NOT NULL AND {w}", p)
            for r in conn.execute(f"SELECT payload_json FROM events WHERE type='COLLATERAL_MARKED' AND ts > ? AND ts <= ? AND {w}", [day_start, now] + p).fetchall():
                try:
                    delta = int((json.loads(r["payload_json"] or "{}") or {}).get("delta_c") or 0)
                except (ValueError, TypeError):
                    delta = 0
                if delta > 0:
                    marks += 1
            wc, pc = _in("c.account_id", accounts)
            vm = _count(conn, "SELECT COUNT(*) FROM otc_vm v JOIN otc_contracts c ON c.contract_id=v.contract_id LEFT JOIN journal_entries e ON e.entry_id=v.entry_id"
                              f" WHERE v.delta_c < 0 AND {wc} AND ((e.ts IS NOT NULL AND e.ts > ? AND e.ts <= ?) OR (e.ts IS NULL AND v.vm_date IN (?, ?)))",
                        pc + [day_start, now, (aday - timedelta(days=1)).isoformat(), aday.isoformat()])
        # open attention: global rows (no account) + the scope's accounts; lifetime-scoped rows only for the scope's lifetimes
        wa, pa = (_in("account_id", accounts) if accounts else ("0", []))
        wl, pl = (_in("ref_id", lids) if lids else ("0", []))
        sql = ("SELECT id, ts, severity, source, code, message, account_id, ref_type, ref_id FROM attention WHERE resolved_ts IS NULL"
               f" AND (account_id IS NULL OR {wa}) AND (ref_type IS NULL OR ref_type <> 'lifetime' OR {wl}) ORDER BY id DESC")
        for r in conn.execute(sql, pa + pl).fetchall():
            sev = r["severity"] if r["severity"] in open_by else "INFO"
            open_by[sev] += 1
            if len(items) < ATTENTION_ITEMS:
                items.append({"id": int(r["id"]), "ts": int(r["ts"]), "severity": r["severity"], "source": r["source"], "code": r["code"], "message": r["message"],
                              "account_id": r["account_id"], "ref_type": r["ref_type"], "ref_id": r["ref_id"]})
        providers = _provider_pauses(services, conn, now)
    # the report filtered to the scope's lifetimes (a problem without a lifetime belongs to every scope)
    problems = [p for p in (report.get("problems") or []) if p.get("lifetime_id") in (None, *lids)]
    failing = sorted({p["id"] for p in problems if p.get("severity") == "ERROR"})
    integrity = {"ok": not failing, "checked_at": report.get("checked_at"), "mode": report.get("mode"), "failing": failing,
                 "warnings": sum(1 for p in problems if p.get("severity") == "WARN"), "problems": problems[:PROBLEMS_SHOWN]}
    holds = acc.hold_state()
    held = [lid for lid in lids if lid in holds]
    hold = {"held": bool(held), "lifetimes": held, "since": {lid: holds[lid].get("since") for lid in held}, "failing": {lid: holds[lid].get("failing") for lid in held}}
    warnings = _optional_count(services, "risk", ("warnings", "report"), scope, notes, "warnings")
    compliance = _optional_count(services, "compliance", ("notices", "holdings"), scope, notes, "notices")
    news = getattr(services, "news", None)
    news_state = None
    if news is not None and hasattr(news, "health"):
        try:
            h = news.health()
            news_state = {"injection_flags": h.get("injection_flags", 0), "fetched": h.get("fetched", {}),
                          "failing": sorted(s for s, st in (h.get("sources") or {}).items() if st.get("ok") is False)}
        except Exception as e:   # noqa: BLE001 — the strip must still answer
            notes.append(f"news state unavailable: {type(e).__name__}")
    return {"scope": scope, "scope_kind": sc.kind, "lifetimes": lids, "accounts": accounts, "as_of": now, "accounting_day": aday.isoformat(), "window_start": day_start,
            "integrity": integrity, "hold": hold, "open": open_by, "attention_open": sum(open_by.values()), "items": items,
            "warnings": warnings, "compliance": compliance,
            "collateral_calls_today": marks + vm, "collateral_marks_today": marks, "swap_vm_payments_today": vm,
            "recalls": recalls, "working_orders": working, "pending_settlements": pending,
            "providers": providers, "news": news_state, "notes": notes}


def _optional_count(services, module_attr: str, fn_names: Tuple[str, ...], scope: str, notes: List[str], list_attr: str) -> Optional[int]:
    """Count from an optional module: ``risk.warnings(services, scope)`` / ``risk.report(services, scope).warnings`` or
    ``compliance.notices(...)`` / ``compliance.holdings(...)``.  Absent module → None + a note (unavailable, never 0)."""
    mod = getattr(services, module_attr, None)
    if mod is None:
        notes.append(f"{module_attr} module not loaded")
        return None
    for name in fn_names:
        fn = getattr(mod, name, None)
        if fn is None:
            continue
        try:
            res = fn(services, scope)
        except Exception as e:   # noqa: BLE001 — the strip must still answer; the failure is named
            notes.append(f"{module_attr}.{name} failed: {type(e).__name__}")
            return None
        if isinstance(res, (list, tuple)):
            if module_attr == "compliance":
                return sum(1 for n in res if (getattr(n, "status", None) or (n.get("status") if isinstance(n, dict) else None)) != "ok")
            return len(res)
        seq = getattr(res, list_attr, None)
        if seq is None and isinstance(res, dict):
            seq = res.get(list_attr)
        if seq is not None:
            return len(seq)
        return 0
    notes.append(f"{module_attr} module has no {' / '.join(fn_names)}")
    return None


def _provider_pauses(services, conn, now: int) -> dict:
    """Providers currently down / refused: the in-memory ``ProviderHealth`` snapshot when the runtime has one, else the
    persisted ``provider_health`` rows."""
    down: List[str] = []
    refused: List[str] = []
    providers = getattr(services, "providers", None)
    health = getattr(providers, "health", None) if providers is not None else None
    if health is not None and hasattr(health, "snapshot"):
        for name, s in sorted(health.snapshot().items()):
            if s.get("down") or s.get("ok") is False:
                down.append(name)
            if s.get("refused_ua"):
                refused.append(name)
    else:
        for r in conn.execute("SELECT provider, last_ok_ts, last_err_ts, down_until_ts FROM provider_health ORDER BY provider").fetchall():
            if (r["down_until_ts"] or 0) > now or (r["last_err_ts"] is not None and (r["last_ok_ts"] is None or int(r["last_err_ts"]) > int(r["last_ok_ts"]))):
                down.append(r["provider"])
    return {"down": down, "refused_ua": refused, "paused": len(down)}


__all__ = ["NewsStore", "NewsItem", "SourceState", "NewsError", "attention_summary", "sanitize", "is_instruction_like", "safe_link",
           "parse_yahoo_news", "parse_fed_rss", "parse_newspaper_index", "edition_time", "SOURCES", "MARKET_QUERIES", "RETENTION", "TITLE_LIMIT",
           "NEWS_SLOTS", "SKIPPED_SLOTS", "OPEN_ORDER_STATUSES"]
