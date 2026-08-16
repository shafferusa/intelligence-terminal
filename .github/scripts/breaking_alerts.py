#!/usr/bin/env python3
"""Breaking-news alerts for Logan's Daily Newspaper.

Sends a SHORT Telegram push when something genuinely big breaks between
editions. Deliberately not descriptive: Logan reads the actual story in the
next morning or closing brief. The alert exists only so he is not the last
person to know for eight hours.

WHAT COUNTS AS BIG
Cross-source corroboration, not keyword matching. A story alerts when N
independent major outlets publish the same event inside the same window.
Keyword lists fire on the word "crisis" in an opinion column; three wires
independently leading with the same event at the same time is a real signal
and needs no model to judge it.

A tight severity list exists as a SECOND path (it lowers the corroboration
bar from 3 sources to 2, never below that) for events where outlets stagger:
a head of state dying, a coup, a major quake. It can never fire alone.

News only. No market-move alerts -- Logan asked for breaking news only.

Deliberate non-goals: no summaries, no links, no analysis, no images.
Headline, who has it, what time. That is the whole message.

Stdlib only, so nothing can rot out from under a scheduled job.
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET_TZ = ZoneInfo("America/New_York")
STATE_PATH = "state/alerts.json"
UA = "LoganTerminal/1.0 (loganshaffer87@gmail.com)"

# Independent major outlets. Diversity matters more than count: three feeds
# that all republish the same wire are ONE source, not three, so no two
# entries here should share a newsroom.
#
# The WSJ feeds serve double duty -- they corroborate like any other source,
# AND they supply the link the alert carries. Logan asked for a WSJ link and
# HAS a WSJ subscription (confirmed 2026-08-16), so the paywall is not a
# problem and WSJ stays first in LINK_PREFERENCE. It is preferred but never
# required: when WSJ has not picked a story up, the alert still fires and
# links the strongest other source rather than staying silent.
FEEDS = [
    ("WSJ",        "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("WSJ",        "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("WSJ",        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("BBC",        "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("NPR",        "https://feeds.npr.org/1001/rss.xml"),
    ("Guardian",   "https://www.theguardian.com/world/rss"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("CNBC",       "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("Sky News",   "https://feeds.skynews.com/feeds/rss/world.xml"),
    ("CBS",        "https://www.cbsnews.com/latest/rss/world"),
    ("Deutsche Welle", "https://rss.dw.com/rdf/rss-en-world"),
]

# Order used when WSJ has not covered the story and a fallback link is needed.
LINK_PREFERENCE = ["WSJ", "CNBC", "BBC", "Guardian", "NPR", "CBS",
                   "Sky News", "Al Jazeera", "Deutsche Welle"]

# Corroboration answers "is this real and big" -- it does not answer "is this
# Logan's kind of news". On the night this was built, three outlets all led
# with four Renaissance paintings stolen from a Sicilian museum: perfectly
# corroborated, and not something to buzz a phone for. So a topic gate runs
# alongside the source count.
#
# BEAT is deliberately broad -- it is a topic classifier, not a severity
# judgement, which is a far more forgiving job than keyword-matching
# importance. A story must touch at least one of Logan's beats.
BEAT = [
    # war, security, diplomacy
    "war", "military", "troops", "strike", "airstrike", "missile", "drone",
    "invasion", "invades", "ceasefire", "hostage", "sanction", "treaty",
    "summit", "diplomat", "embassy", "nuclear", "nato", "hamas", "israel",
    "gaza", "ukraine", "russia", "china", "taiwan", "iran", "north korea",
    "venezuela", "border", "terror", "coup", "militant", "rebel",
    # government, law, politics
    "president", "congress", "senate", "house of representatives", "supreme court",
    "federal", "election", "ballot", "voter", "impeach", "indict", "court",
    "judge", "ruling", "lawsuit", "veto", "executive order", "governor",
    "parliament", "prime minister", "chancellor", "minister", "shutdown",
    "legislation", "subpoena", "attorney general", "pentagon", "white house",
    # economy and markets
    "fed", "federal reserve", "inflation", "cpi", "jobs report", "unemployment",
    "gdp", "recession", "tariff", "trade deal", "economy", "stocks", "bond",
    "yields", "dollar", "oil", "opec", "central bank", "earnings", "merger",
    "acquisition", "bankruptcy", "ipo", "layoffs", "interest rate", "default",
    # tech, ai, cyber
    "artificial intelligence", " ai ", "openai", "anthropic", "nvidia", "chip",
    "semiconductor", "cyber", "hack", "breach", "ransomware", "outage",
    # disasters, public safety, health
    "earthquake", "hurricane", "wildfire", "flood", "tornado", "explosion",
    "crash", "derail", "outbreak", "virus", "pandemic", "evacuat", "emergency",
    "shooting", "killed", "dead", "death toll", "casualties", "wounded",
    "collapse", "famine", "quarantine",
    # space
    "spacex", "nasa", "rocket", "satellite", "astronaut", "orbit", "starship",
]

# Vetoes a story even when corroborated -- unless SEVERITY also matches, so a
# shooting at a museum still gets through.
SOFT = [
    "museum", "painting", "sculpture", "statue", "artist", "exhibition",
    "novel", "film", "movie", "actor", "actress", "singer", "album", "concert",
    "celebrity", "royal", "prince", "princess", "duchess", "wedding",
    "fashion", "recipe", "restaurant", "tourist", "zoo", "panda",
    "world's tallest", "world's largest", "unveiled", "festival", "olympic",
    "football", "soccer", "cricket", "tennis", "nba", "nfl", "world cup",
    "golf", "premier league", "box office", "grammy", "oscar",
]

# Lowers the bar from 3 sources to 2, and overrides a SOFT veto. Never fires
# on its own, and every term here has to be a thing that is big by definition
# rather than by adjective.
SEVERITY = [
    "assassinat", "coup ", "coup d", "martial law", "state of emergency",
    "declares war", "invasion", "invades", "nuclear test", "nuclear strike",
    "impeach", "resigns", "steps down", "dies", "dead at", "killed in",
    "magnitude", "earthquake", "tsunami", "mass shooting", "airstrike",
    "ceasefire", "hostage", "evacuat", "shot dead", "plane crash",
    "emergency landing", "outbreak", "pandemic", "default",
]

# Words that carry no event content, so they never help decide that two
# headlines describe the same story.
STOP = set("""a an the of in on at to for from by with and or but as is are was were
be been being it its this that these those he she they them his her their our your my
after before over under new says say said report reports amid into out up down more
than what how why who when where which will would could should may might can us uk eu
live updates latest breaking video watch photos opinion analysis""".split())


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_feed(name, raw):
    """Return [(title, link, published_datetime_utc)] from RSS or Atom."""
    out = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out

    # RSS <item> and Atom <entry>, namespace-agnostic.
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = None
        link = None
        when = None
        for child in node:
            ctag = child.tag.rsplit("}", 1)[-1].lower()
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag == "link":
                # RSS puts the URL in the text, Atom in href="".
                href = child.get("href")
                if href:
                    if child.get("rel") in (None, "alternate") and not link:
                        link = href.strip()
                elif child.text and child.text.strip():
                    link = child.text.strip()
            elif ctag == "guid" and child.text and not link:
                # Some feeds only carry a usable URL as an isPermaLink guid.
                text = child.text.strip()
                if text.startswith("http"):
                    link = text
            elif ctag in ("pubdate", "published", "updated", "date") and child.text:
                when = parse_date(child.text.strip())
        if title:
            out.append((html.unescape(title), link, when))
    return out


DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
]


def parse_date(text):
    text = text.replace("GMT", "+0000").replace("UTC", "+0000")
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def tokens(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 2 and w not in STOP}


def similar(a, b):
    """Jaccard overlap of content words."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault("sent", [])
    return data


def save_state(state):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    state["sent"] = [s for s in state["sent"] if s.get("ts", "") >= cutoff][-200:]
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def already_sent(state, toks):
    """True if this event was alerted recently, however it was worded."""
    for prev in state["sent"]:
        if similar(toks, set(prev.get("tokens", []))) >= 0.45:
            return True
    return False


def in_quiet_hours(now_et):
    """23:00-06:30 ET: only severity-flagged events get through."""
    minutes = now_et.hour * 60 + now_et.minute
    return minutes >= 23 * 60 or minutes < 6 * 60 + 30


def send(text):
    token = os.environ.get("TG_TOKEN")
    chat = os.environ.get("TG_CHAT")
    if not token or not chat:
        log("no telegram credentials -- would have sent:\n" + text)
        return False
    body = json.dumps({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp).get("ok", False)
    except Exception as exc:  # noqa: BLE001
        log("telegram send failed: %s" % exc)
        return False


def main():
    now = datetime.now(timezone.utc)
    now_et = now.astimezone(ET_TZ)
    # 90 minutes, not 45: outlets stagger their filing by an hour on a big
    # story, and a tighter window was leaving too few items to corroborate
    # against. Dedupe stops the wider window causing repeats.
    window = now - timedelta(minutes=90)

    state = load_state()

    # Rate limits: never more than one push per 20 minutes, or 6 per ET day.
    recent = [s for s in state["sent"] if s.get("ts", "") >= (now - timedelta(minutes=20)).isoformat()]
    if recent:
        log("within 20-minute cooldown -- nothing sent")
        return 0
    today_et = now_et.date().isoformat()
    todays = [s for s in state["sent"] if s.get("et_date") == today_et]
    if len(todays) >= 6:
        log("daily alert cap reached (6) -- nothing sent")
        return 0

    # Gather. A dead feed is normal and must never break the job.
    items = []          # (source, title, link, tokens)
    live = set()
    for name, url in FEEDS:
        try:
            raw = fetch(url)
        except Exception as exc:  # noqa: BLE001
            log("feed down: %s (%s)" % (name, exc))
            continue
        entries = parse_feed(name, raw)
        if not entries:
            log("feed empty/unparsed: %s <%s>" % (name, url))
            continue
        live.add(name)
        for title, link, when in entries:
            # No timestamp: keep it, feeds order newest-first and a missing
            # date is far more often a formatting quirk than an old story.
            if when and when < window:
                continue
            items.append((name, title, link, tokens(title)))

    log("%d live newsrooms (%s), %d recent items"
        % (len(live), ", ".join(sorted(live)), len(items)))
    if len(live) < 3:
        log("fewer than 3 live newsrooms -- cannot corroborate, standing down")
        return 0

    # Cluster by headline similarity. `links` keeps one URL per newsroom so the
    # alert can prefer WSJ's and fall back cleanly when WSJ hasn't covered it.
    clusters = []       # [{sources:set, titles:[], links:{src:url}, tokens:set}]
    for src, title, link, toks in items:
        placed = False
        for c in clusters:
            if similar(toks, c["tokens"]) >= 0.35:
                c["sources"].add(src)
                c["titles"].append(title)
                c["tokens"] |= toks
                if link and src not in c["links"]:
                    c["links"][src] = link
                placed = True
                break
        if not placed:
            clusters.append({
                "sources": {src},
                "titles": [title],
                "links": {src: link} if link else {},
                "tokens": set(toks),
            })

    # Score and pick the strongest qualifying cluster.
    best = None
    for c in clusters:
        n = len(c["sources"])
        text = " " + " ".join(c["titles"]).lower() + " "
        severe = any(k in text for k in SEVERITY)

        # Topic gate: on Logan's beats, and not soft news. SEVERITY overrides
        # the soft veto so a shooting at a museum is not filtered as "museum".
        if not any(k in text for k in BEAT):
            continue
        if any(k in text for k in SOFT) and not severe:
            continue

        threshold = 2 if severe else 3
        if n < threshold:
            continue
        if in_quiet_hours(now_et) and not severe:
            continue
        if already_sent(state, c["tokens"]):
            continue
        score = (n, severe)
        if best is None or score > best[0]:
            best = (score, c)

    if not best:
        log("nothing cleared the bar")
        return 0

    (n_sources, severe), cluster = best

    # Prefer WSJ's headline and URL when WSJ has the story, since that is the
    # link Logan reads. Otherwise take the shortest headline in the cluster --
    # shortest is reliably the least editorialised -- and the best link going.
    link_src = next((s for s in LINK_PREFERENCE if s in cluster["links"]), None)
    link = cluster["links"].get(link_src) if link_src else None

    if link_src == "WSJ":
        wsj_titles = [t for (s, t, _l, _k) in items
                      if s == "WSJ" and similar(tokens(t), cluster["tokens"]) >= 0.35]
        headline = min(wsj_titles, key=len) if wsj_titles else min(cluster["titles"], key=len)
    else:
        headline = min(cluster["titles"], key=len)

    # BREAKING, the headline, the link. Nothing else -- Logan reads the actual
    # story in the next edition; this is only so he knows it happened.
    text = "\U0001F534 <b>BREAKING</b> — %s" % html.escape(headline)
    if link:
        text += '\n\n<a href="%s">Read at %s</a>' % (html.escape(link, quote=True), link_src)

    if not send(text):
        log("send failed -- state not updated, will retry next run")
        return 1

    state["sent"].append({
        "ts": now.isoformat(),
        "et_date": today_et,
        "headline": headline,
        "sources": sorted(cluster["sources"]),
        "link": link,
        "link_source": link_src,
        "severe": severe,
        "tokens": sorted(cluster["tokens"])[:40],
    })
    save_state(state)
    log("ALERTED (%d sources, severe=%s): %s" % (n_sources, severe, headline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
