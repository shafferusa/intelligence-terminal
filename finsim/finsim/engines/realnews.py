"""Real headlines for career saves.

Sources (all public, no key): Yahoo Finance's news search (the same query2 host the price feed uses) for the
market as a whole and for the names in the book, and the Federal Reserve's press-release feed. Headlines are
fetched when a session is processed and stored as NEWS_PUBLISHED events, so replay never touches the network.
Everything fetched is data: titles are shown as text, never interpreted.

Standard library only; failures leave the day without real headlines rather than failing the session.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

SEARCH = "https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=0&newsCount={n}"
FED_RSS = "https://www.federalreserve.gov/feeds/press_all.xml"
UA = "Mozilla/5.0"
NY = ZoneInfo("America/New_York")
MARKET_QUERIES = ["SPY", "^GSPC", "^TNX", "^VIX"]


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _yahoo(q: str, n: int = 10) -> List[Dict]:
    j = json.loads(_get(SEARCH.format(q=urllib.parse.quote(q), n=n)).decode("utf-8"))
    out = []
    for x in j.get("news", []) or []:
        t = x.get("providerPublishTime")
        if not t or not x.get("title"):
            continue
        out.append({"title": str(x["title"]).strip(), "publisher": str(x.get("publisher") or ""), "link": str(x.get("link") or ""),
                    "time": int(t), "tickers": [str(s) for s in (x.get("relatedTickers") or [])]})
    return out


def _fed() -> List[Dict]:
    root = ET.fromstring(_get(FED_RSS))
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title or not pub:
            continue
        try:
            t = int(datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
        out.append({"title": title, "publisher": "Federal Reserve", "link": link, "time": t, "tickers": ["FED"]})
    return out


# ---------------------------------------------------------------- the daily newspaper (this repository's own intelligence reports)
PAPER_NAME = "Logan's Daily Newspaper"
PAPER_URL = "https://shafferusa.github.io/intelligence-terminal/"
# the sections a markets desk reads, in the order they run, and the category each becomes; science, technology and local news stay out
PAPER_SECTIONS = [("s-top", "TOP STORIES", "TOP"), ("s-changed", "What Changed Today", "POLICY"), ("s-econ", "The Economy", "ECONOMY"), ("s-business", "Business", "BUSINESS"),
                  ("s-moved", "What Moved Markets", "MARKETS"), ("s-winners", "Winners & Losers", "MARKETS"), ("s-tomorrow", "Tomorrow", "CALENDAR")]
EDITION_TIME = {"am": "06:30", "pm": "16:30", "learn": "06:00"}


def newspaper_dir() -> Optional[str]:
    """Where the reports live: FINSIM_NEWSPAPER, else the repository's site/reports next to this package."""
    import os
    env = os.environ.get("FINSIM_NEWSPAPER")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # …/finsim (the package's parent)
    for base in (here, os.path.dirname(here)):
        cand = os.path.join(base, "site", "reports")
        if os.path.isfile(os.path.join(cand, "index.json")):
            return cand
    return None


def _untag(html: str) -> str:
    import html as _h
    return clean_title(_h.unescape(re.sub(r"<[^>]+>", " ", html)))


def _first_sentence(text: str, limit: int = 170) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    head = m.group(1) if m else text
    if len(head) > limit:
        cut = head[:limit].rsplit(" ", 1)[0]
        head = cut + "…"
    return head


def parse_newspaper(html: str, link_base: str, when_iso: str) -> List[Dict]:
    """Stories from one edition: an <article> gives its <h3> and deck; a prose section gives one item per paragraph."""
    out: List[Dict] = []
    for sid, title, category in PAPER_SECTIONS:
        m = re.search(r'<section[^>]*aria-labelledby="%s"[^>]*>(.*?)</section>' % re.escape(sid), html, re.S)
        if not m:
            continue
        body = m.group(1)
        arts = re.findall(r"<article[^>]*>(.*?)</article>", body, re.S)
        if arts:
            for art in arts:
                h = re.search(r"<h3[^>]*>(.*?)</h3>", art, re.S)
                if not h:
                    continue
                deck = re.search(r'<p class="story-deck">(.*?)</p>', art, re.S)
                out.append({"title": _untag(h.group(1)), "body": _untag(deck.group(1)) if deck else "", "section": title, "category": category,
                            "link": f"{link_base}#{sid}", "time": when_iso})
            continue
        for para in re.findall(r"<p(?![^>]*class=\"story-(?:deck|sourceline)\")[^>]*>(.*?)</p>", body, re.S):
            text = _untag(para)
            if len(text) < 40:
                continue
            out.append({"title": _first_sentence(text), "body": text[:600], "section": title, "category": category, "link": f"{link_base}#{sid}", "time": when_iso})
    return out


class NewspaperNews:
    """The repository's own daily reports (site/reports): the political, economic and financial sections of each edition
    published on the session day, read from disk — no network, nothing invented."""

    def __init__(self, directory: Optional[str] = None):
        self.dir = directory if directory is not None else newspaper_dir()

    def editions(self, d) -> List[Dict]:
        import os
        if not self.dir:
            return []
        try:
            with open(os.path.join(self.dir, "index.json"), encoding="utf-8") as f:
                idx = json.load(f)
        except Exception:
            return []
        iso = d.isoformat()
        return [e for e in idx if e.get("date") == iso and e.get("slot") in ("am", "pm")]

    def headlines_for(self, d, max_per_edition: int = 24) -> List[Dict]:
        import os
        out: List[Dict] = []
        for e in self.editions(d):
            path = os.path.join(self.dir, os.path.relpath(e["path"], "reports")) if e.get("path", "").startswith("reports/") else os.path.join(self.dir, e.get("path", ""))
            try:
                with open(path, encoding="utf-8") as f:
                    html = f.read()
            except Exception:
                continue
            when = f"{d.isoformat()}T{EDITION_TIME.get(e.get('slot'), '06:30')}:00-04:00"
            link = PAPER_URL + e["path"]
            items = parse_newspaper(html, link, when)[:max_per_edition]
            for it in items:
                it["publisher"] = f"{PAPER_NAME} ({e.get('slot', '').upper()} edition · {it['section']})"
                it["edition"] = e.get("slot")
            out.extend(items)
        return out


class RealNews:
    """Fetches and filters headlines; `fetch_yahoo`/`fetch_fed` are injectable for tests."""

    def __init__(self, fetch_yahoo=None, fetch_fed=None, paper: Optional[NewspaperNews] = None):
        self._yahoo = fetch_yahoo or _yahoo
        self._fed = fetch_fed or _fed
        self.paper = paper if paper is not None else NewspaperNews()

    def headlines_for(self, d, tickers: List[str], known_links: Optional[set] = None, max_items: int = 30) -> List[Dict]:
        """Headlines published on session day `d` (New York): the day's newspaper editions first (politics, economy, business,
        markets), then the wire for the market and for `tickers`, newest first, deduplicated."""
        known = set(known_links or ())
        items: Dict[str, Dict] = {}
        paper_items: List[Dict] = []
        try:
            for it in (self.paper.headlines_for(d) if self.paper else []):
                key = f"{it['link']}|{it['title']}"
                if key in known or key in items:
                    continue
                refs = sorted({t for t in tickers if re.search(r"\b%s\b" % re.escape(t), it["title"] + " " + it.get("body", ""))})
                paper_items.append({"headline": it["title"], "body": it.get("body", ""), "publisher": it["publisher"], "link": it["link"], "time": it["time"],
                                    "category": it["category"], "refs": refs, "key": key})
        except Exception:
            paper_items = []

        def add(x: Dict, category: str):
            key = x.get("link") or x["title"]
            if key in known or key in items:
                return
            when = datetime.fromtimestamp(x["time"], tz=timezone.utc).astimezone(NY)
            if when.date() != d:
                return
            items[key] = {"headline": x["title"], "publisher": x["publisher"], "link": x.get("link", ""), "time": when.isoformat(), "category": category,
                          "refs": sorted(set(t for t in x.get("tickers", []) if t and not t.startswith("^"))), "key": key}
        for q in MARKET_QUERIES:
            try:
                for x in self._yahoo(q, 12):
                    add(x, "MARKET")
            except Exception:
                continue
        for t in tickers[:12]:
            try:
                for x in self._yahoo(t, 6):
                    add(x, "COMPANY")
            except Exception:
                continue
        try:
            for x in self._fed():
                add(x, "FED")
        except Exception:
            pass
        out = sorted(items.values(), key=lambda x: x["time"], reverse=True)
        return paper_items + out[:max_items]


def clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()[:220]
