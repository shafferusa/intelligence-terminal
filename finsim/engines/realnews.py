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


class RealNews:
    """Fetches and filters headlines; `fetch_yahoo`/`fetch_fed` are injectable for tests."""

    def __init__(self, fetch_yahoo=None, fetch_fed=None):
        self._yahoo = fetch_yahoo or _yahoo
        self._fed = fetch_fed or _fed

    def headlines_for(self, d, tickers: List[str], known_links: Optional[set] = None, max_items: int = 30) -> List[Dict]:
        """Headlines published on session day `d` (New York), for the market and for `tickers`, newest first, deduplicated."""
        known = set(known_links or ())
        items: Dict[str, Dict] = {}

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
        # company items first go by relevance to the book: keep at most three per ticker query
        return out[:max_items]


def clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()[:220]
