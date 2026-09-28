"""News headlines (WSJ · Barron's · MarketWatch) -> point-in-time research records. Research only.

Protocol: NEWS_SIGNALS_PROTOCOL.md (fixed 2026-09-28, before any data). Nothing here enters the Shaffer Score, the
Hedge or any production output; the records are evaluated forward only (engine/newslive.py).

Sources, and the rules that bind them (the repository owner's, from Dow Jones' subscriber terms):
    * automated: Dow Jones' official public RSS feeds on ``feeds.content.dowjones.io`` only — one GET per feed per run,
      no pagination, no archive crawling, never a log-in, cookie or article page. The old host ``feeds.a.dj.com`` is
      frozen (it still answers with stale RSS) and is refused. A user-added feed must be https on the same host.
    * manual: an article the user is reading in their own browser, pasted in (the News page's bookmarklet copies its
      URL, title, publication time and text). Only ``wsj.com``, ``barrons.com`` and ``marketwatch.com`` URLs (and their
      subdomains) are accepted. The text is used once to compute features and is **never stored** — only its length
      and SHA-256.

Point in time: ``known_at`` = when FinSim2 first saw the article (the fetch or the import), never the publisher's
``published_at`` (stored separately). ``session`` = the trading session whose close is the first close at or after
``known_at`` in New York: 16:00 or later, or a weekend / holiday, counts toward the next session.

Everything fetched is untrusted data: XML declaring a DOCTYPE or entities is refused, sizes are capped, HTML is
stripped to text, links are reduced to https URLs on the three article hosts, and nothing is ever evaluated.
Instruction-like text in a feed is just text.

Per article: ``features`` (``lex1``: a small finance sentiment lexicon with negation, uncertainty words and 16 event
tags) and ``tickers`` (explicit tickers, company names from the store's asset names, a small hand alias table).
Per asset and session: ``aggregate`` -> alt_data ``news_dj`` (published = the session date).
"""
from __future__ import annotations

import bisect
import datetime as _dt
import email.utils
import hashlib
import html
import html.parser
import json
import math
import re
import urllib.parse
from typing import Dict, Iterable, List, Optional, Tuple

from . import FetchError, http_get

DATASET = "news_dj"
LEX = "lex1"
HOST = "feeds.content.dowjones.io"
FROZEN_HOSTS = ("feeds.a.dj.com",)
UA = "FinSim2/1.0"
FEED_BASE = "https://feeds.content.dowjones.io/public/rss/"
# (source, feed name, url, verified). The three WSJ feeds are the ones .github/scripts/breaking_alerts.py reads daily;
# the others are Dow Jones' published feed names on the same host, unverified from here (the cloud research
# environment blocks the host) — a wrong name shows up as "http error" in the per-feed status and harms nothing.
# Barron's has no guessed URL: it comes in through manual import or a feed URL the user adds.
FEEDS: List[Tuple[str, str, str, bool]] = [
    ("WSJ", "World News", FEED_BASE + "RSSWorldNews", True),
    ("WSJ", "US Business", FEED_BASE + "WSJcomUSBusiness", True),
    ("WSJ", "Markets", FEED_BASE + "RSSMarketsMain", True),
    ("WSJ", "Technology", FEED_BASE + "RSSWSJD", False),
    ("WSJ", "US News", FEED_BASE + "RSSUSnews", False),
    ("MarketWatch", "Top Stories", FEED_BASE + "mw_topstories", False),
    ("MarketWatch", "Real-time Headlines", FEED_BASE + "mw_realtimeheadlines", False),
    ("MarketWatch", "Market Pulse", FEED_BASE + "mw_marketpulse", False),
    ("MarketWatch", "Bulletins", FEED_BASE + "mw_bulletins", False),
]
EXTRA_KEY = "news:feeds_extra"          # user-added feed URLs (validated: https on HOST)
STATUS_KEY = "news:feed_status"         # per-feed state of the last run
LAST_KEY = "news:last_run"
ARTICLE_HOSTS = {"wsj.com": "WSJ", "barrons.com": "Barron's", "marketwatch.com": "MarketWatch"}
RESEARCH_ONLY = "research only — not part of the Shaffer Score"

MAX_FEED_BYTES = 4 * 1024 * 1024        # an RSS feed is ~50-200 KB; anything near this is not a feed
MAX_ITEMS = 300                         # items read per feed
MAX_TITLE = 500
MAX_SUMMARY = 2000
MAX_TEXT = 200_000                      # characters of a manual import used for features (the bookmarklet caps at 100k)
STALE_DAYS = 3                          # a feed whose newest item is older is "stale" (it can rot while returning 200)
MAX_ITEM_AGE_DAYS = 3                   # older feed items are not ingested (old news, not news for this session)
CLOSE_NY = _dt.time(16, 0)
NOVELTY_LOOKBACK = 20
AGG_WINDOW = 25                         # sessions re-aggregated by default (idempotent)


# ------------------------------------------------------------------ time
def _ny():
    from finsim.tzfallback import get_zone
    return get_zone("America/New_York")


def utc_iso(t: _dt.datetime) -> str:
    """Aware or naive-UTC datetime -> 'YYYY-MM-DDTHH:MM:SSZ'."""
    if t.tzinfo is not None:
        t = t.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return t.replace(microsecond=0).isoformat() + "Z"


def parse_time(s) -> Optional[str]:
    """RFC 822 (RSS pubDate) or ISO 8601 -> UTC ISO string; None when missing, garbled or implausible."""
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()[:64]
    t = None
    try:
        t = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError, OverflowError):
        t = None
    if t is None:
        try:
            t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if t.tzinfo is None:                 # "-0000" / no zone: RFC 5322 says UTC with no information; take it as UTC
        t = t.replace(tzinfo=_dt.timezone.utc)
    if not 1990 <= t.year <= 2100:
        return None
    return utc_iso(t)


def _from_iso(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _holidays(year: int) -> set:
    from finsim.calendar import us_equity_holidays
    return {d.isoformat() for d in us_equity_holidays(year)}


def _is_session(d: _dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in _holidays(d.year)


def session_for(known_at, calendar: List[str]) -> str:
    """The session whose close is the first close at or after ``known_at`` (UTC ISO string or datetime), New York time.
    Dates inside ``calendar`` (the SPY trading days) come from it; later dates are projected over weekdays that are not
    NYSE holidays (aggregation snaps them to the real calendar once it has them)."""
    t = _from_iso(known_at) if isinstance(known_at, str) else known_at
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    ny = t.astimezone(_ny())
    d = ny.date() + _dt.timedelta(days=1 if ny.time() >= CLOSE_NY else 0)
    iso = d.isoformat()
    if calendar and iso <= calendar[-1]:
        return calendar[bisect.bisect_left(calendar, iso)]
    while not _is_session(d):
        d += _dt.timedelta(days=1)
    return d.isoformat()


def snap(session: str, calendar: List[str]) -> Optional[str]:
    """The first calendar date on or after ``session`` (None if the calendar does not reach it yet)."""
    i = bisect.bisect_left(calendar, session)
    return calendar[i] if i < len(calendar) else None


def _calendar(store) -> List[str]:
    return [r[0] for r in store._q("SELECT date FROM prices WHERE asset_id = 'SPY' ORDER BY date")]


# ------------------------------------------------------------------ untrusted text and URLs
class _Text(html.parser.HTMLParser):
    """HTML -> text: character data only (entities unescaped), script / style dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def strip_html(s, cap: Optional[int] = None) -> str:
    """Untrusted HTML or text -> plain text with entities unescaped and whitespace collapsed."""
    if not s:
        return ""
    s = str(s)
    if cap:
        s = s[: cap * 4]
    if "<" in s and ">" in s:
        p = _Text()
        try:
            p.feed(s)
            p.close()
            s = "".join(p.out)
        except Exception:  # noqa: BLE001 - malformed markup: fall back to a crude tag strip
            s = re.sub(r"<[^>]*>", " ", s)
    s = html.unescape(s)                  # double-escaped entities (&amp;#8217;) survive one pass of the parser
    s = re.sub(r"\s+", " ", s).strip()
    return s[:cap] if cap else s


def _host_ok(host: str, allowed: Iterable[str]) -> Optional[str]:
    host = (host or "").lower().rstrip(".")
    return next((a for a in allowed if host == a or host.endswith("." + a)), None)


def canonical_url(url, hosts: Optional[Iterable[str]] = None) -> Optional[str]:
    """https URL without query, fragment, credentials or port; None if not http(s) or (with ``hosts``) not on them."""
    if not isinstance(url, str):
        return None
    try:
        u = urllib.parse.urlsplit(url.strip())
        port = u.port
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https") or not u.hostname or u.username or u.password or port not in (None, 80, 443):
        return None
    host = u.hostname.lower().rstrip(".")
    if hosts is not None and not _host_ok(host, hosts):
        return None
    path = u.path or "/"
    if re.search(r"[\s<>\"']", path):
        return None
    return urllib.parse.urlunsplit(("https", host, path, "", ""))


def article_id(canonical: str) -> str:
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def source_of(url: str) -> Optional[str]:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return None
    a = _host_ok(host, ARTICLE_HOSTS)
    return ARTICLE_HOSTS[a] if a else None


def validate_feed_url(url) -> str:
    """A feed URL FinSim2 may fetch: https on feeds.content.dowjones.io, nothing else. Raises ValueError."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("feed URL required")
    try:
        u = urllib.parse.urlsplit(url.strip())
        port = u.port
    except ValueError:
        raise ValueError("not a valid URL") from None
    host = (u.hostname or "").lower().rstrip(".")
    if host in FROZEN_HOSTS:
        raise ValueError(f"{host} is frozen (it still serves stale RSS): use {HOST}")
    if u.scheme != "https" or host != HOST or u.username or u.password or port not in (None, 443):
        raise ValueError(f"only https feeds on {HOST} are allowed")
    if not u.path.startswith("/") or re.search(r"[\s<>\"']", u.path):
        raise ValueError("bad feed path")
    return urllib.parse.urlunsplit(("https", HOST, u.path, u.query, ""))


# ------------------------------------------------------------------ RSS
class FeedError(Exception):
    """A feed body that is not acceptable RSS (too large, entity declarations, not XML)."""


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_rss(body: bytes) -> List[dict]:
    """RSS 2.0 bytes -> [{id, title, link, description, published_at, guid}] (newest first as in the feed).
    Raises FeedError for a body that declares a DOCTYPE or entities, is too large, or is not XML. Items without a link
    on wsj.com / barrons.com / marketwatch.com are dropped (their links could point anywhere)."""
    import xml.etree.ElementTree as ET
    if not isinstance(body, (bytes, bytearray)):
        raise FeedError("not bytes")
    if len(body) > MAX_FEED_BYTES:
        raise FeedError("feed too large")
    up = bytes(body).upper()
    if b"<!DOCTYPE" in up or b"<!ENTITY" in up:
        raise FeedError("DOCTYPE / ENTITY declarations refused")
    try:
        root = ET.fromstring(bytes(body))
    except ET.ParseError as e:
        raise FeedError(f"not XML ({e.__class__.__name__})") from None
    out, seen = [], set()
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        f: Dict[str, str] = {}
        for ch in item:
            k = _local(ch.tag)
            if k in ("title", "link", "description", "pubDate", "guid", "date", "published", "updated") and k not in f:
                f[k] = "".join(ch.itertext())
        link = canonical_url(f.get("link") or (f.get("guid") if (f.get("guid") or "").startswith("http") else None), ARTICLE_HOSTS)
        title = strip_html(f.get("title"), MAX_TITLE)
        if not link or not title:
            continue
        aid = article_id(link)
        if aid in seen:
            continue
        seen.add(aid)
        out.append({"id": aid, "title": title, "link": link, "description": strip_html(f.get("description"), MAX_SUMMARY),
                    "published_at": parse_time(f.get("pubDate") or f.get("date") or f.get("published") or f.get("updated")),
                    "guid": strip_html(f.get("guid"), 300)})
        if len(out) >= MAX_ITEMS:
            break
    return out


def feed_state(items: List[dict], now: _dt.datetime) -> Tuple[str, Optional[str]]:
    """('ok' | 'stale', newest published_at): stale when there are no items or the newest is > STALE_DAYS old."""
    dates = [i["published_at"] for i in items if i.get("published_at")]
    newest = max(dates) if dates else None
    if not items or newest is None:
        return "stale", newest
    age = (now - _from_iso(newest)).total_seconds() / 86400
    return ("stale" if age > STALE_DAYS else "ok"), newest


def _get(url: str) -> bytes:
    """The one network call (tests patch it): a plain GET with FinSim2's own user agent; no cookies, no log-in."""
    return http_get(validate_feed_url(url), headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.1"},
                    timeout=20.0)


def feeds(store) -> List[dict]:
    """The feeds a run fetches: the built-in list plus the user's (kv ``news:feeds_extra``), each once."""
    out, seen = [], set()
    for src, name, url, ok in FEEDS:
        out.append({"source": src, "name": name, "url": url, "verified": ok, "user": False})
        seen.add(url)
    for url in store.kv_get(EXTRA_KEY) or []:
        try:
            u = validate_feed_url(url)
        except ValueError:
            continue
        if u not in seen:
            seen.add(u)
            out.append({"source": "user", "name": u.rsplit("/", 1)[-1][:60], "url": u, "verified": False, "user": True})
    return out


def add_feed(store, url: str) -> List[str]:
    u = validate_feed_url(url)
    cur = [x for x in store.kv_get(EXTRA_KEY) or [] if isinstance(x, str)]
    if u not in cur and u not in {f[2] for f in FEEDS}:
        cur.append(u)
        store.kv_set(EXTRA_KEY, cur)
    return cur


def remove_feed(store, url: str) -> List[str]:
    cur = [x for x in store.kv_get(EXTRA_KEY) or [] if isinstance(x, str)]
    try:
        u = validate_feed_url(url)
    except ValueError:
        u = (url or "").strip()
    keep = [x for x in cur if x not in (u, (url or "").strip())]
    store.kv_set(EXTRA_KEY, keep)
    return keep


def refresh(store, progress=None, now: Optional[_dt.datetime] = None) -> dict:
    """Fetch every feed once and store the new articles. A failing feed never stops the others; each feed's state
    (ok / http error / stale / parse error) is recorded in kv ``news:feed_status`` and returned."""
    say = progress or (lambda m: None)
    now = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    seen_at = utc_iso(now)
    cal = _calendar(store)
    index = ticker_index(store)
    status, errors, new_total, stored = {}, [], 0, 0
    for f in feeds(store):
        st = {"source": f["source"], "name": f["name"], "verified": f["verified"], "user": f["user"], "checked_at": seen_at,
              "state": None, "http_status": None, "items": 0, "new": 0, "old_skipped": 0, "newest": None, "error": None}
        try:
            items = parse_rss(_get(f["url"]))
        except FetchError as e:
            st.update(state="http error", http_status=e.status, error=str(e))
        except (FeedError, ValueError) as e:
            st.update(state="parse error", error=str(e))
        else:
            state, newest = feed_state(items, now)
            st.update(state=state, newest=newest, items=len(items))
            rows = []
            for it in items:
                p = it["published_at"]
                if p and (_from_iso(p) - now).total_seconds() > 86400:
                    p = None                                   # a date in the future is garbage, not information
                if p and (now - _from_iso(p)).total_seconds() > MAX_ITEM_AGE_DAYS * 86400:
                    st["old_skipped"] += 1
                    continue
                rows.append(_record(it["id"], source_of(it["link"]) or f["source"], f["name"], it["link"], it["title"],
                                    it["description"], p, seen_at, cal, index, "rss", it["title"], it["description"]))
            new = store.add_news(rows)
            st["new"] = len(new)
            new_total += len(new)
            stored += len(rows)
        if st["state"] != "ok":
            errors.append(f"{f['name']}: {st['state']}" + (f" ({st['error']})" if st["error"] else ""))
        status[f["url"]] = st
        say(f"{f['source']} {f['name']}: {st['state']} — {st['items']} items, {st['new']} new")
    store.kv_set(STATUS_KEY, status)
    out = {"rows": stored, "new": new_total, "feeds": len(status), "ok": sum(1 for s in status.values() if s["state"] == "ok"),
           "errors": errors, "at": seen_at}
    store.kv_set(LAST_KEY, out)
    return out


def _record(aid, source, feed, url, title, summary, published, seen_at, cal, index, kind, ftitle, ftext, **extra) -> dict:
    feats = features(ftitle, ftext)
    return {"id": aid, "source": source, "feed": feed, "url": url, "title": title, "summary": summary or None,
            "published_at": published, "first_seen": seen_at, "known_at": seen_at, "session": session_for(seen_at, cal),
            "kind": kind, "tickers": link_tickers(ftitle, ftext, index), "features": feats, "lex": LEX, **extra}


# ------------------------------------------------------------------ ticker linking
# Legal suffixes removed from asset names to form the company alias ("JPMorgan Chase & Co." -> "JPMorgan Chase").
_SUFFIX = re.compile(r"(?:[\s,]+(?:inc|incorporated|corp|corporation|co|company|companies|holdings?|group|plc|ltd|limited|llc|lp|l\.p|n\.v|s\.a|ag|se|sa|nv"
                     r"|class\s+[a-c]|cl\s+[a-c]|the|and|&)\.?)+\s*$", re.I)
# Single names that are ordinary English words (or other things) in a headline: never linked by name alone; the full
# legal form ("Target Corp", "Visa Inc") still links. Judgement, documented in NEW_DATA_SOURCES.md.
STOPLIST = frozenset("""
Target Gap Block Visa Match Ball Dow Progressive Southern Discover Carrier Unity Snap Square Shift Zoom Toast Affirm Lucid
Mosaic Monster Genuine Coherent Equity Apollo Delta United American General National First Global International Capital
Energy Health Digital Service Services Systems Solutions Partners Trust Life Industries Brands Foods Motors Airlines Bank
Financial Resources Realty Properties Enterprises Technologies Technology Networks Software Entertainment Communications
Pharmaceuticals Therapeutics Semiconductor Energy Chemical Materials Insurance Holdings Group Company Corp Fortune Sun
Crown Liberty Frontier Pioneer Eagle Summit Pinnacle Vista Premier Select Quest Alliance Union Republic Standard Federal
Guardian Harmony Echo Clear Arch Core Cable Gold Silver Copper Steel Oil Gas Power Water Home Wave Signal Edge Globe Atlas
Genesis Legacy Nuance Rocket Opendoor Compass Mercury Pulse Spirit Hub Marathon Chord Cadence Fair Commerce Evergy Bread
Northern Western Eastern Pacific Atlantic Central Texas Florida Hawaiian Alaska America Americas Workday News Fox
Travelers Carnival Booking Pool Williams Nasdaq MSCI Charter Applied Hartford Premier Target Meta
""".split())
STOP_PHRASES = frozenset({"Palo Alto", "Wall Street", "White House", "Federal Reserve", "New York", "United States"})
# a trailing generic word dropped for a second, shorter alias ("Cisco Systems" -> "Cisco", "Costco Wholesale" -> "Costco")
_SHORTEN = re.compile(r"\s+(?:Systems|Communications|Technology|Technologies|Networks|Financial|Services|Solutions|Industries|Brands|"
                      r"Enterprises|Platforms|Semiconductor|Pharmaceuticals|Therapeutics|Entertainment|Wholesale|Resources|"
                      r"International|Worldwide|Global|Interactive)$")
# phrases blanked out before name matching (they contain an alias but mean something else)
_MASK = re.compile(r"Oracle of Omaha|Dow Jones|Big Apple|Apple Daily|Target date|Intel(?:ligence)? (?:agencies|officials|community)")
# a small hand table for obvious references the legal names miss; only used when the asset is in the store
HAND_ALIASES = {
    "GOOGL": ["Google", "Alphabet"], "META": ["Facebook", "Meta Platforms", "Meta", "Instagram", "WhatsApp"],
    "BRK-B": ["Berkshire", "Berkshire Hathaway"], "JPM": ["JPMorgan", "JPMorgan Chase", "JP Morgan"], "AMZN": ["Amazon"],
    "NVDA": ["Nvidia"], "MSFT": ["Microsoft"], "AAPL": ["Apple"], "GS": ["Goldman", "Goldman Sachs"], "BAC": ["Bank of America", "BofA"],
    "XOM": ["Exxon", "ExxonMobil", "Exxon Mobil"], "DIS": ["Disney"], "KO": ["Coca-Cola"], "PG": ["Procter & Gamble", "P&G"],
    "IBM": ["IBM"], "AMD": ["AMD"], "CSCO": ["Cisco"], "VZ": ["Verizon"], "T": ["AT&T"], "COST": ["Costco"], "WMT": ["Walmart"],
    "LLY": ["Eli Lilly"], "UNH": ["UnitedHealth"], "MA": ["Mastercard"], "V": ["Visa Inc"], "TSLA": ["Tesla"], "NFLX": ["Netflix"],
    "INTC": ["Intel"], "ORCL": ["Oracle"], "CRM": ["Salesforce"], "AVGO": ["Broadcom"], "MCD": ["McDonald's"], "HD": ["Home Depot"],
    "JNJ": ["Johnson & Johnson", "J&J"], "PFE": ["Pfizer"], "MRK": ["Merck"], "BA": ["Boeing"], "CAT": ["Caterpillar"],
    "MS": ["Morgan Stanley"], "C": ["Citigroup", "Citi"], "WFC": ["Wells Fargo"], "GE": ["General Electric", "GE Aerospace"],
    "GM": ["General Motors"], "F": ["Ford Motor"], "UBER": ["Uber"], "TGT": ["Target Corp"], "DAL": ["Delta Air Lines"],
}
# bare "(XYZ)" is ambiguous in prose; these acronyms are never read as tickers there (exchange-prefixed and $ forms are)
_BARE_STOP = frozenset("CEO CFO COO CTO AI IT US USA UK EU UN GDP CPI IPO ETF SEC FED FDA DOJ FTC FCC EPA IRS NATO OPEC ECB IMF "
                       "EV EVS ON ALL ARE NOW KEY WELL FAST CAR PLAY BIG OPEN ANY HAS CAN EAT GOOD LOVE FUN RUN WORK TRUE REAL PEAK "
                       "HOPE LOW HIGH NEW ONE TWO BEST SAFE CASH GOLD PAY TV PC PR HR AM PM ET PT EST EDT UTC Q1 Q2 Q3 Q4".split())
_EXPLICIT = re.compile(r"\((?:NYSE(?:\s+American|\s+Arca|\s+MKT)?|NASDAQ|Nasdaq|AMEX|Cboe|BATS|TSX|LSE)\s*:\s*([A-Z]{1,5}(?:[.\-][A-Z])?)\)"
                       r"|\$([A-Z]{1,5}(?:[.\-][A-Z])?)\b"
                       r"|\(([A-Z]{1,5}(?:[.\-][A-Z])?)\)")
_WORD_CHARS = r"\w&'’.-"


def _aliases_for(name: str) -> List[str]:
    """Company aliases from an asset name: the name with legal suffixes and a leading 'The' removed, and the full form
    without its trailing dot; all-caps names also get their capitalised form ('NVIDIA' -> 'Nvidia')."""
    if not name:
        return []
    n = re.sub(r"\s*\([^)]*\)", "", name).strip().rstrip(".").strip()          # "(Class A)", "(Nasdaq-100)"
    full = n
    base = n
    for _ in range(4):
        b = _SUFFIX.sub("", base).strip().rstrip(",").strip()
        if b == base:
            break
        base = b
    base = re.sub(r"^The\s+", "", base).strip()
    cands = [base]
    if base.endswith(".com") and len(base) > 8:            # "Amazon.com" -> also "Amazon"
        cands.append(base[:-4])
    short = _SHORTEN.sub("", base)
    if short != base:
        cands.append(short)
    out = []
    for c in cands:
        if not c or c in STOP_PHRASES or (" " not in c and c in STOPLIST):
            continue                                           # single generic words never link by name alone
        out.append(c)
        if c.isupper() and c.isalpha() and len(c) >= 5:
            out.append(c.capitalize())
    if full != base and len(full) >= 4:
        out.append(full)
    return [a for a in dict.fromkeys(out) if len(a) >= 4]


def ticker_index(store) -> dict:
    """{tickers: set, names: {alias: asset_id}, rx: compiled alternation} over the store's EQUITY and ETF assets.
    Company names come from EQUITY names only (ETF names are product names, linked by explicit ticker only)."""
    equities = store.assets("EQUITY")
    tickers = {a["id"] for a in equities + store.assets("ETF")}
    names: Dict[str, str] = {}
    ambiguous = set()
    for a in equities:
        for al in _aliases_for(a.get("name") or ""):
            if al in names and names[al] != a["id"]:
                ambiguous.add(al)
            names.setdefault(al, a["id"])
    for al in ambiguous:                   # two companies (or share classes), one alias: link neither ...
        names.pop(al, None)
    for aid, als in HAND_ALIASES.items():  # ... unless the hand table says which one is meant
        if aid in tickers:
            for al in als:
                names[al] = aid
    alts = sorted(names, key=len, reverse=True)
    rx = re.compile(r"(?<![" + _WORD_CHARS + r"])(" + "|".join(re.escape(a) for a in alts) + r")(?![\w&]|[-’'][A-Za-z]{2,}|\.[A-Za-z])") if alts else None
    return {"tickers": tickers, "names": names, "rx": rx}


def _norm_ticker(t: str) -> str:
    return t.replace(".", "-")


def link_tickers(title: str, text: str, index: dict) -> List[Tuple[str, float, str]]:
    """[(asset_id, confidence, where)], strongest first: explicit tickers ('(NASDAQ: AAPL)', '$AAPL', '(AAPL)') on
    stocks / ETFs in the store = 1.0; company names (case-sensitive, word boundaries) = 0.9 in the title, 0.7 in the
    body. One entry per asset (its strongest evidence)."""
    best: Dict[str, Tuple[float, str]] = {}

    def add(a, c, w):
        if a not in best or c > best[a][0]:
            best[a] = (c, w)
    title, text = (title or "").replace("’", "'"), (text or "").replace("’", "'")
    for where, s in (("title", title), ("body", text)):
        for m in _EXPLICIT.finditer(s):
            ex, dollar, bare = m.groups()
            t = ex or dollar or bare
            if bare and (bare in _BARE_STOP or len(bare) == 1):
                continue
            t = _norm_ticker(t)
            if t in index["tickers"]:
                add(t, 1.0, "ticker")
        rx = index.get("rx")
        if rx is not None:
            for m in rx.finditer(_MASK.sub(" ", s)):
                a = index["names"].get(m.group(1))
                if a:
                    add(a, 0.9 if where == "title" else 0.7, where)
    return sorted(((a, c, w) for a, (c, w) in best.items()), key=lambda x: (-x[1], x[0]))


# ------------------------------------------------------------------ features (lex1)
# A compact finance sentiment lexicon in the spirit of Loughran-McDonald, written for FinSim2 (not their list).
# Words are matched lower-case after light stemming (plural / -ed / -ing / -ly).
POSITIVE = frozenset("""
beat beats outperform outperformed exceed exceeded surpass surpassed record strong stronger strongest robust solid gain
gains gained rally rallied rallies surge surged soar soared jump jumped climb climbed rise rose rises rebound rebounded
recover recovery improve improved improvement upgrade upgraded boost boosted growth grow grew expand expansion profit
profitable profitability win won wins success successful breakthrough approve approved approval optimism optimistic
upbeat bullish confident confidence momentum accelerate accelerated raise raised upside positive favorable benefit
beneficial best high higher highs top topped lead leading leader advance advanced tailwind resilient resilience
milestone opportunity opportunities efficient efficiency innovative reward rewarding healthy thrive thriving stellar
attractive premium upswing boom booming
""".split())
NEGATIVE = frozenset("""
miss missed misses loss losses lose lost decline declined declines drop dropped fall fell falls slump slumped plunge
plunged tumble tumbled sink sank slide slid crash crashed weak weaker weakest weakness slow slowed slowdown slowing
downgrade downgraded cut cuts cutting warn warned warning warnings risk risks risky concern concerns worried worry
worries fear fears threat threats lawsuit sued sue litigation fraud probe investigation penalty fined fine violation
recall recalled bankrupt bankruptcy default defaults layoff layoffs lay-off fire fired resign resigned resignation
halt halted suspend suspended delay delayed disappoint disappointed disappointing disappointment shortfall deficit
negative adverse unfavorable bearish pessimism pessimistic downturn recession crisis turmoil volatile volatility
pressure pressured headwind headwinds struggle struggled struggling fail failed failure problem problems trouble
troubled damage damaged breach hack hacked outage shortage low lower lows worst worse plummet plummeted erode eroded
erosion writedown impairment scandal inflation tariff tariffs sanction sanctions downside collapse collapsed bad
poor hurt hurts sluggish
""".split())
UNCERTAIN = frozenset("""
may might could uncertain uncertainty unclear unknown unpredictable possibly perhaps approximately depend depends
depending contingent volatile risk risks tentative preliminary pending speculate speculation rumor rumors rumored
reportedly consider considering weigh weighing doubt doubts question questions unsettled ambiguous fluctuate
""".split())
NEGATORS = frozenset({"not", "no", "never", "without", "cannot", "neither", "nor"})
NEG_WINDOW = 3
_TOKEN = re.compile(r"[a-z][a-z'’-]*")


def _tokens(s: str) -> List[str]:
    return _TOKEN.findall((s or "").lower())


def _stem_in(w: str, lex) -> bool:
    if w in lex:
        return True
    for suf in ("'s", "’s", "s", "es", "ed", "d", "ing", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and w[: -len(suf)] in lex:
            return True
    return False


def _polarity(tokens: List[str]) -> Tuple[int, int, int]:
    """(positive, negative, uncertainty) counts; a negator within the 3 previous tokens flips a word's sign."""
    pos = neg = unc = 0
    for i, w in enumerate(tokens):
        if _stem_in(w, UNCERTAIN):
            unc += 1
        p, n = _stem_in(w, POSITIVE), _stem_in(w, NEGATIVE)
        if p == n:                              # neither list, or (after stemming) both: no polarity
            continue
        flip = any(t in NEGATORS or t.endswith("n't") or t.endswith("n’t") for t in tokens[max(0, i - NEG_WINDOW):i])
        if p != flip:
            pos += 1
        else:
            neg += 1
    return pos, neg, unc


EVENTS = {
    "earnings_beat": r"\b(?:beat|beats|beating|topped|tops|exceeded|exceeds|surpassed|surpasses)\b[^.]{0,40}\b(?:estimates?|expectations|forecasts?|consensus|views?)\b"
                     r"|\bbetter[- ]than[- ]expected\b[^.]{0,20}\b(?:earnings|profit|results|revenue|sales|quarter)",
    "earnings_miss": r"\b(?:miss|missed|misses|missing|fell short of|falls short of|trailed)\b[^.]{0,40}\b(?:estimates?|expectations|forecasts?|consensus|views?)\b"
                     r"|\bworse[- ]than[- ]expected\b[^.]{0,20}\b(?:earnings|profit|results|revenue|sales|quarter)",
    "guidance_up": r"\b(?:raise[sd]?|raising|lift(?:s|ed)?|boost(?:s|ed)?|increase[sd]?|hike[sd]?|ups)\b[^.]{0,30}\b(?:guidance|outlook|forecast|full-year|annual (?:sales|profit|revenue|earnings))",
    "guidance_down": r"\b(?:cut(?:s|ting)?|lower(?:s|ed)?|reduce[sd]?|slash(?:es|ed)?|trim(?:s|med)?)\b[^.]{0,30}\b(?:guidance|outlook|full-year (?:forecast|outlook))"
                     r"|\bprofit warning\b|\bwarns? (?:on|of) (?:profit|sales|earnings|revenue)",
    "upgrade": r"\bupgrade[sd]?\b[^.]{0,40}\b(?:to buy|to outperform|to overweight|rating|analysts?|shares|stock)\b|\banalysts?\b[^.]{0,40}\bupgrade",
    "downgrade": r"\bdowngrade[sd]?\b[^.]{0,40}\b(?:to sell|to underperform|to underweight|to neutral|to hold|rating|analysts?|shares|stock|credit|debt)\b"
                 r"|\b(?:analysts?|moody's|s&p|fitch)\b[^.]{0,40}\bdowngrade",
    "mna": r"\b(?:acquire[sd]?|acquisition|acquiring|merger|merge[sd]?|merging|takeover|buyout|tender offer|deal to buy|agreed to buy|agrees to buy|bid for|to be acquired)\b",
    "legal": r"\b(?:lawsuit|lawsuits|sued|sues|suing|antitrust|indicted|indictment|class action|subpoena(?:ed)?|probe|investigation|fined|penalty|settle[sd]?|settlement)\b",
    "mgmt_change": r"\b(?:ceo|chief executive|cfo|chief financial officer|chairman|chairwoman|president)\b[^.]{0,40}\b(?:resign(?:s|ed)?|steps? down|stepped down|depart(?:s|ed|ure)?|retire[sd]?|ousted|fired|replace[sd]?|succeed(?:s|ed)?|to leave)\b"
                   r"|\b(?:names|named|appoints|appointed|hires|hired|taps|tapped)\b[^.]{0,30}\b(?:ceo|chief executive|cfo|chief financial officer|chairman|president)\b",
    "layoffs": r"\b(?:layoffs?|lay off|laying off|laid off|job cuts?|cut(?:s|ting)? (?:about |some |nearly |roughly )?[\d,.]+ (?:jobs|positions|employees|workers)|workforce reduction|reduce (?:its )?workforce|cut(?:s|ting)? (?:its )?workforce)\b",
    "buyback": r"\b(?:buybacks?|buy back|share repurchases?|stock repurchases?|repurchase program|repurchase plan)\b",
    "dividend_up": r"\b(?:raise[sd]?|raising|increase[sd]?|boost(?:s|ed)?|hike[sd]?|lift(?:s|ed)?)\b[^.]{0,25}\bdividends?\b|\bdividend (?:increase|hike|boost)\b",
    "dividend_cut": r"\b(?:cut(?:s|ting)?|slash(?:es|ed)?|suspend(?:s|ed)?|eliminate[sd]?|reduce[sd]?|omit(?:s|ted)?|halt(?:s|ed)?)\b[^.]{0,25}\bdividends?\b|\bdividend (?:cut|suspension)\b",
    "bankruptcy": r"\b(?:bankruptcy|bankrupt|chapter 11|chapter 7|insolvency|insolvent|creditor protection)\b",
    "offering": r"\b(?:share offering|stock offering|secondary offering|public offering|initial public offering|ipo|convertible (?:notes|bonds|offering)|debt offering|bond (?:sale|offering)|at-the-market offering)\b",
    "recall_outage_cyber": r"\b(?:recalls?|recalled|recalling|outages?|cyberattacks?|cyber-attacks?|cyber attacks?|hacked|hackers?|data breach|security breach|ransomware)\b",
}
_EVENT_RX = {k: re.compile(v, re.I) for k, v in EVENTS.items()}
# an event phrase directly after a negator ("did not beat estimates", "no layoffs") is not that event
_NEGATED = re.compile(r"(?:\b(?:not|no|never|without|cannot)|n[’']t)\s+(?:[\w’'-]+\s+){0,2}$", re.I)


def _event(rx, blob: str) -> bool:
    return any(not _NEGATED.search(blob[max(0, m.start() - 40):m.start()]) for m in rx.finditer(blob))


def features(title: str, text: str) -> Dict[str, float]:
    """lex1 features of one article: sent, sent_title, pos, neg, uncertainty, n_tokens (floats) and one 0/1 flag per
    event tag (ev_<tag>)."""
    title, text = title or "", text or ""
    tt = _tokens(title)
    body = _tokens(text)
    pos, neg, unc = _polarity(tt + ["."] * NEG_WINDOW + body)          # a gap so a title negator never reaches the body
    tp, tn, _ = _polarity(tt)
    out = {"sent": (pos - neg) / (pos + neg + 1.0), "sent_title": (tp - tn) / (tp + tn + 1.0), "pos": float(pos), "neg": float(neg),
           "uncertainty": float(unc), "n_tokens": float(len(tt) + len(body))}
    blob = title + " . " + text
    for k, rx in _EVENT_RX.items():
        out[f"ev_{k}"] = 1.0 if _event(rx, blob) else 0.0
    return out


# ------------------------------------------------------------------ manual import
def import_article(store, url: str, title: str, text: str, published=None, source=None, now: Optional[_dt.datetime] = None) -> dict:
    """An article the user is reading, pasted in. ``known_at`` = the import time (never earlier, whatever ``published``
    says; that is stored separately). Features and tickers are computed from the full text, which is then discarded:
    only its length and SHA-256 are stored. The source is always inferred from the host (``source`` is accepted for
    callers' convenience and ignored: a label cannot disagree with the URL). Raises ValueError for another host or a
    missing title."""
    canon = canonical_url(url, ARTICLE_HOSTS)
    if not canon:
        raise ValueError("only article URLs on wsj.com, barrons.com or marketwatch.com can be imported")
    src = source_of(canon)
    title = strip_html(title, MAX_TITLE)
    raw = text if isinstance(text, str) else ""
    body = strip_html(raw[: MAX_TEXT * 2], MAX_TEXT) if ("<" in raw and ">" in raw) else re.sub(r"\s+", " ", raw[:MAX_TEXT]).strip()
    if not title:
        raise ValueError("a title is required")
    now = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    seen_at = utc_iso(now)
    aid = article_id(canon)
    existing = store.news_article(aid)
    dup_of = None
    if existing is not None:
        if existing["kind"] == "manual":
            return {"ok": True, "duplicate": True, "article": _public(existing)}
        dup_of, aid = aid, article_id(canon + "#manual")         # already known from a feed: kept, not counted twice
        again = store.news_article(aid)
        if again is not None:
            return {"ok": True, "duplicate": True, "article": _public(again)}
    rec = _record(aid, src, "manual import", canon, title, None, parse_time(published) if isinstance(published, str) else None,
                  seen_at, _calendar(store), ticker_index(store), "manual", title, body,
                  text_length=len(body), text_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(), dup_of=dup_of)
    store.add_news([rec])
    store.audit("news.import", aid, {"url": canon, "source": src, "text_length": len(body), "dup_of": dup_of})
    return {"ok": True, "duplicate": False, "article": _public(store.news_article(aid))}


def _public(a: dict) -> dict:
    """An article for the API / CLI (nothing here is article text beyond the feed's own title and teaser)."""
    f = a.get("features") or {}
    return {"id": a["id"], "source": a["source"], "feed": a.get("feed"), "url": a["url"], "title": a["title"], "summary": a.get("summary"),
            "published_at": a.get("published_at"), "known_at": a["known_at"], "session": a.get("session"), "kind": a["kind"],
            "tickers": [{"asset": t[0], "confidence": t[1], "where": t[2]} for t in a.get("tickers") or []],
            "sent": f.get("sent"), "sent_title": f.get("sent_title"), "events": [k[3:] for k, v in f.items() if k.startswith("ev_") and v],
            "text_length": a.get("text_length"), "dup_of": a.get("dup_of")}


# ------------------------------------------------------------------ daily aggregation
TAGS = list(EVENTS)


def aggregate(store, research, sessions=None) -> dict:
    """Per (asset, session) news features -> alt_data ``news_dj`` (published = the session date). Idempotent.
    ``sessions``: None = the last AGG_WINDOW calendar sessions, "all" = every session with news, or a list of dates.
    Also writes asset ``news:all`` (n_articles over every ingested article, linked or not) and, for the session the
    move-size snapshot is dated, its σ 1D / 1W (and P(up) 1D when stored) for every asset that has one."""
    cal = research.panel().calendar()
    if not cal:
        return {"rows": 0, "sessions": 0}
    if sessions is None:
        want = set(cal[-AGG_WINDOW:])
    elif sessions == "all":
        want = None
    else:
        cs = set(cal)
        want = {d for d in sessions if d in cs}
    lo_i = 0 if want is None else max(0, bisect.bisect_left(cal, min(want)) - NOVELTY_LOOKBACK) if want else len(cal)
    since = cal[lo_i] if lo_i < len(cal) else cal[-1]
    counts: Dict[str, Dict[str, int]] = {}             # asset -> session -> n_articles (for novelty)
    acc: Dict[Tuple[str, str], dict] = {}
    total: Dict[str, int] = {}
    for art in store._q("SELECT id, source, session, features, tickers FROM news_articles WHERE dup_of IS NULL AND session >= ?",
                        (_prev_day(since),)):
        s = snap(art["session"], cal) if art["session"] else None
        if s is None or s < since:
            continue
        total[s] = total.get(s, 0) + 1
        f = _json(art["features"], {})
        for t in _json(art["tickers"], []):
            if not isinstance(t, list) or len(t) != 3:
                continue
            a, c = t[0], float(t[1])
            counts.setdefault(a, {})[s] = counts.setdefault(a, {}).get(s, 0) + 1
            if want is not None and s not in want:
                continue
            g = acc.setdefault((a, s), {"n": 0, "w": 0.0, "sent": 0.0, "title": 0.0, "abs": 0.0, "src": set(), "ev": dict.fromkeys(TAGS, 0)})
            g["n"] += 1
            g["w"] += c
            g["sent"] += c * float(f.get("sent") or 0.0)
            g["title"] += c * float(f.get("sent_title") or 0.0)
            g["abs"] += c * abs(float(f.get("sent") or 0.0))
            g["src"].add(art["source"])
            for k in TAGS:
                g["ev"][k] += int(bool(f.get(f"ev_{k}")))
    pos = {d: i for i, d in enumerate(cal)}
    rows = []
    for (a, s), g in acc.items():
        i = pos[s]
        prev = [counts.get(a, {}).get(cal[j], 0) for j in range(max(0, i - NOVELTY_LOOKBACK), i)]
        mean_prev = sum(prev) / len(prev) if prev else 0.0
        vals = {"n_articles": g["n"], "n_sources": len(g["src"]), "sent_mean": g["sent"] / g["w"] if g["w"] else 0.0,
                "sent_title_mean": g["title"] / g["w"] if g["w"] else 0.0, "abs_sent": g["abs"] / g["w"] if g["w"] else 0.0,
                "novelty": g["n"] / (1.0 + mean_prev)}
        vals.update({f"ev_{k}": v for k, v in g["ev"].items()})
        rows += [(a, s, k, float(v), s) for k, v in vals.items()]
    for s, n in total.items():
        if want is None or s in want:
            rows.append(("news:all", s, "n_articles", float(n), s))
    rows += _movesize_rows(store, cal, want)
    if rows:
        store.put_alt(DATASET, rows)
    return {"rows": len(rows), "asset_sessions": len(acc), "sessions": len({s for _, s in acc} | set(total))}


def _movesize_rows(store, cal: List[str], want) -> List[tuple]:
    """σ 1D / 1W (and P(up) 1D) from kv movesize:today, only for the session the snapshot is dated."""
    snap_ = store.kv_get("movesize:today") or {}
    d = snap_.get("date")
    if not isinstance(d, str) or d not in set(cal) or (want is not None and d not in want):
        return []
    out = []
    for a, it in (snap_.get("items") or {}).items():
        if not isinstance(it, dict) or it.get("date") != d:
            continue
        hz = it.get("horizons") or {}
        for lab, fld in (("1D", "ms_sigma_1d"), ("1W", "ms_sigma_1w")):
            v = (hz.get(lab) or {}).get("sigma")
            if isinstance(v, (int, float)) and math.isfinite(v) and v > 0:
                out.append((a, d, fld, float(v), d))
        p = (hz.get("1D") or {}).get("p_up")
        if isinstance(p, (int, float)) and math.isfinite(p):
            out.append((a, d, "p_up_1d", float(p), d))
    return out


def _prev_day(d: str) -> str:
    return (_dt.date.fromisoformat(d) - _dt.timedelta(days=10)).isoformat()


def _json(s, default):
    try:
        v = json.loads(s) if s else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


# ------------------------------------------------------------------ views
def status(store) -> dict:
    """Per-feed state of the last run, counts and the user's feeds."""
    st = store.kv_get(STATUS_KEY) or {}
    fl = feeds(store)
    return {"label": RESEARCH_ONLY, "last_run": store.kv_get(LAST_KEY), "counts": store.news_counts(),
            "feeds": [{**f, **(st.get(f["url"]) or {"state": "not run"})} for f in fl],
            "hosts": {"feeds": HOST, "articles": sorted(ARTICLE_HOSTS), "frozen": list(FROZEN_HOSTS)}}


def recent(store, limit: int = 50, asset_id: Optional[str] = None) -> List[dict]:
    return [_public(a) for a in store.news_articles(asset_id=asset_id, limit=max(1, min(int(limit), 500)))]


def asset_view(store, asset_id: str, limit: int = 30) -> dict:
    """The asset page's news card: recent linked articles and the last sessions' aggregates."""
    daily: Dict[str, dict] = {}
    for _, d, f, v, _ in store.alt(DATASET, asset_id):
        daily.setdefault(d, {})[f] = v
    days = [{"session": d, **daily[d]} for d in sorted(daily)[-20:] if "n_articles" in daily[d]]
    return {"asset": asset_id, "label": RESEARCH_ONLY, "articles": recent(store, limit, asset_id), "daily": days[::-1]}
