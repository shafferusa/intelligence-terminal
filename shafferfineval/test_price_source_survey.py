"""Offline tests for the survivorship-free price-source survey.

Pure stdlib, no network by default:   python test_price_source_survey.py
Add the network probes (Yahoo + SEC):  python test_price_source_survey.py --online

WHY THIS FILE EXISTS. The survey in docs/price_source_survey.json is a buying
recommendation, and a buying recommendation made of half-remembered vendor
claims is worse than no recommendation at all. These tests pin the three things
that could rot and quietly turn the document into fiction:

  Section 1 -- THE SURVEY MUST STAY HONEST. Every source row carries all nine
  comparison fields, a verification label saying how the claim was obtained, and
  at least one piece of evidence. An unknown must read UNKNOWN; it may never be
  an empty string, and it may never be 0 -- an unmeasured quantity reported as
  zero is the specific failure this project forbids. A row whose verification is
  `not_verified` may not carry a dollar figure: that is how an invented price
  gets laundered into a decision.

  Section 2 -- THE SURVIVOR AUDIT MUST KEEP HOLDING. crash_prices.json was
  offered as a possible free rescue for the survivor-only price sample. It is
  not one, and the proof is structural rather than statistical: the only two
  entries in the file that correspond to companies which stopped trading (AVP,
  acquired 2020; X, acquired 2025) are JSON null, while all 41 survivors run to
  August 2026. The same file also contains the cleanest illustration of the
  failure mode we are trying to price our way out of: GM begins 2010-11-18, the
  successor's IPO, and the General Motors Corporation that went to zero in 2009
  is simply absent under the same three letters.

  Section 3 -- THE GAP IN OUR OWN STORE MUST STAY VISIBLE. 8,732 entities
  stopped filing before 2024 and only 77 of them have a price line. If that
  ratio ever silently improves without an ingest, something is inventing names;
  if it silently worsens, something is dropping the dead. Either way a human
  should look. The frozen tables (pit_feature, pit_score, pit_replay_run) are
  asserted empty in the same pass, because this research must never be the thing
  that quietly starts the official replay.

The store is opened read-only and every transaction is short: a long-lived read
snapshot blocks the checkpointer, and a blocked checkpointer is how a 9.3 GB WAL
once filled this disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SURVEY_PATH = os.path.join(HERE, "docs", "price_source_survey.json")
STORE_PATH = os.path.join(HERE, "shafferfineval_pit.db")
CRASH_PRICES_PATH = "C:/Users/logan/AI-Bubble-Research/scripts/crash_prices.json"

#: Every source row must answer all of these, or the comparison is not a
#: comparison. The owner asked for exactly these columns.
REQUIRED_SOURCE_FIELDS = (
    "source_id",
    "name",
    "tier",
    "delisted_coverage",
    "delisted_from",
    "earliest_date",
    "corporate_actions",
    "permanent_identifiers",
    "adjustment_methodology",
    "unadjusted_retrievable",
    "api_format",
    "licensing",
    "cost_category",
    "cost_evidence",
    "integration_complexity",
    "evidence",
    "verification",
)

COST_CATEGORIES = {
    "free",
    "one_time_low_hundreds",
    "low_hundreds_per_year",
    "low_thousands_per_year",
    "institutional_negotiated",
}

VERIFICATIONS = {
    "measured_locally",
    "vendor_page",
    "vendor_doc",
    "third_party",
    "not_verified",
}

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _readonly_conn() -> sqlite3.Connection:
    """Read-only, short-lived. See the module docstring on WAL exposure."""
    uri = "file:" + STORE_PATH.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


# --------------------------------------------------------------------------
# 1. The survey document
# --------------------------------------------------------------------------

def test_survey_document() -> None:
    print("\n1. survey document is complete and honest")
    check("survey file exists", os.path.exists(SURVEY_PATH), SURVEY_PATH)
    if not os.path.exists(SURVEY_PATH):
        return
    with open(SURVEY_PATH, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    sources = doc.get("sources", [])
    check("survey carries at least 15 candidate sources", len(sources) >= 15, len(sources))

    missing: list[str] = []
    empties: list[str] = []
    for src in sources:
        sid = src.get("source_id", "<no id>")
        for field in REQUIRED_SOURCE_FIELDS:
            if field not in src:
                missing.append(f"{sid}.{field}")
                continue
            val = src[field]
            # An unknown must SAY so. Empty string, empty list and 0 are all
            # ways of reporting "I did not measure this" as if it were data.
            if val is None and field not in ("delisted_from", "earliest_date"):
                empties.append(f"{sid}.{field}=None")
            elif isinstance(val, str) and not val.strip():
                empties.append(f"{sid}.{field}=''")
            elif val == 0:
                empties.append(f"{sid}.{field}=0")
    check("every source answers every comparison column", not missing, missing[:6])
    check("no column is blank or zero instead of UNKNOWN", not empties, empties[:6])

    bad_cost = [s["source_id"] for s in sources
                if s.get("cost_category") not in COST_CATEGORIES
                and not str(s.get("cost_category", "")).startswith("UNKNOWN")]
    check("cost_category uses the vocabulary or says UNKNOWN", not bad_cost, bad_cost)

    bad_ver = [s["source_id"] for s in sources if s.get("verification") not in VERIFICATIONS]
    check("verification label is from the vocabulary", not bad_ver, bad_ver)

    no_ev = [s["source_id"] for s in sources if not s.get("evidence")]
    check("every source cites at least one piece of evidence", not no_ev, no_ev)

    # The laundering guard: an unverified row may not carry a dollar figure.
    laundered = [s["source_id"] for s in sources
                 if s.get("verification") == "not_verified" and "$" in str(s.get("cost_evidence", ""))]
    check("unverified rows carry no dollar figure", not laundered, laundered)

    # The requirement list must still contain the unadjusted-price clause: it is
    # the one that eliminates otherwise attractive vendors.
    req = " ".join(doc.get("requirement", []))
    check("requirement still demands raw as-traded prices", "AS-TRADED" in req.upper(), req[:60])

    rec = doc.get("recommendation", {})
    check("a decision is recorded", bool(rec.get("decision")), rec.get("decision"))
    check("the 'no paid source yet' option is explicit",
          "NO PAID SOURCE" in str(rec.get("decision", "")).upper(), rec.get("decision"))

    rm = doc.get("resource_model", {})
    bpb = rm.get("measured_bytes_per_price_bar")
    check("bytes-per-bar is a measured positive number", isinstance(bpb, (int, float)) and bpb > 0, bpb)
    check("bytes-per-bar records its method", "VACUUM" in str(rm.get("method", "")).upper(), rm.get("method"))


# --------------------------------------------------------------------------
# 2. The crash_prices.json survivor audit
# --------------------------------------------------------------------------

def test_crash_prices_audit() -> None:
    print("\n2. crash_prices.json is a survivor set and cannot rescue anything")
    if not os.path.exists(CRASH_PRICES_PATH):
        check("crash_prices.json present", False, CRASH_PRICES_PATH)
        return
    with open(CRASH_PRICES_PATH, "r", encoding="utf-8") as fh:
        d = json.load(fh)

    check("43 tickers", len(d) == 43, len(d))

    nulls = sorted(k for k, v in d.items() if not isinstance(v, dict))
    check("the only two null entries are the two dead names (AVP, X)",
          nulls == ["AVP", "X"], nulls)

    series = {k: v for k, v in d.items() if isinstance(v, dict) and v.get("series")}
    check("41 tickers actually carry a series", len(series) == 41, len(series))

    lasts = {k: max(v["series"]) for k, v in series.items()}
    check("zero series end before 2020",
          not [k for k, l in lasts.items() if l < "2020-01-01"],
          [k for k, l in lasts.items() if l < "2020-01-01"])
    check("zero series end before 2026 -- every survivor runs to the pull date",
          not [k for k, l in lasts.items() if l < "2026-01-01"],
          [k for k, l in lasts.items() if l < "2026-01-01"])

    # The smoking gun: the bankrupt predecessor is gone and the ticker silently
    # resolves to its successor.
    gm = min(series["GM"]["series"]) if "GM" in series else None
    check("GM begins at the 2010 successor IPO, not 1916", gm == "2010-11-18", gm)

    # Back-adjustment: 1962 closes an order of magnitude below as-traded levels.
    dis = series["DIS"]["series"][min(series["DIS"]["series"])]
    check("prices are back-adjusted (DIS 1962 close under $1)", dis < 1.0, dis)

    check("no terminal/delisting metadata of any kind",
          all(set(v) <= {"first", "last", "last_close", "series"} for v in series.values()),
          sorted({k for v in series.values() for k in v}))


# --------------------------------------------------------------------------
# 3. The gap in our own store, and the frozen tables
# --------------------------------------------------------------------------

def test_store_gap() -> None:
    print("\n3. the dead-entity gap in the local store is still visible")
    if not os.path.exists(STORE_PATH):
        check("pit store present", False, STORE_PATH)
        return
    conn = _readonly_conn()
    try:
        entities = conn.execute("select count(*) from pit_entity").fetchone()[0]
        # COMPANY listings. `instrument_kind` was added on 2026-09-21 when two
        # benchmark ETFs were seeded (see pit_benchmark): they are listings with
        # a NULL entity_id and they are not part of the issuer price sample this
        # survey is about. Counting them here would make the survivor-only
        # sample look as if it had grown, which is the one thing this number
        # exists to report honestly.
        listings = conn.execute(
            "select count(*) from pit_listing where instrument_kind = 'company'"
        ).fetchone()[0]
        dead = conn.execute(
            "select count(*) from pit_entity where last_filing_date < '2024-01-01'").fetchone()[0]
        dead_priced = conn.execute(
            "select count(*) from pit_entity e where e.last_filing_date < '2024-01-01'"
            "  and exists (select 1 from pit_listing l where l.entity_id = e.entity_id)").fetchone()[0]
        frozen = {t: conn.execute("select count(*) from " + t).fetchone()[0]
                  for t in ("pit_feature", "pit_score", "pit_replay_run")}
        exit_sources = [r[0] for r in conn.execute(
            "select distinct exit_date_source from pit_entity_exit where exit_date_source is not null")]
    finally:
        conn.close()

    check("16,890 entities", entities == 16890, entities)
    check("2,574 listings -- the survivor-only price sample", listings == 2574, listings)
    check("8,732 entities stopped filing before 2024", dead == 8732, dead)
    check("only 77 of them have any price line at all", dead_priced == 77, dead_priced)
    check("so ~8,655 dead entities are priced by nothing",
          dead - dead_priced == 8655, dead - dead_priced)
    check("pit_feature / pit_score / pit_replay_run remain empty",
          all(v == 0 for v in frozen.values()), frozen)
    check("pit_entity_exit already speaks the free SEC vocabulary",
          set(exit_sources) <= {"8k_item_301", "form_25nse", "last_volume_bar"}
          and "8k_item_301" in exit_sources, exit_sources)


# --------------------------------------------------------------------------
# 4. Optional network probes (--online)
# --------------------------------------------------------------------------

def test_online() -> None:
    print("\n4. network probes (--online)")
    import gzip
    import urllib.error
    import urllib.request

    def fetch(url: str, ua: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw

    # A browser UA is acceptable for Yahoo only. Never on .gov.
    yahoo_ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    sec_ua = "ShafferFinEval/1.0 (loganshaffer87@gmail.com)"

    dead = ["AVP", "X", "BBBYQ", "SHLDQ", "LEHMQ", "TWTR", "CBS"]
    gone = 0
    for sym in dead:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + sym
               + "?period1=946684800&period2=1790000000&interval=1d")
        try:
            fetch(url, yahoo_ua)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                gone += 1
    check("Yahoo serves none of the seven dead tickers", gone == len(dead), f"{gone}/{len(dead)} were 404")

    try:
        raw = fetch("https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
                    "?period1=946684800&period2=1790000000&interval=1d", yahoo_ua)
        live_ok = b"timestamp" in raw
    except Exception:
        live_ok = False
    check("...while the live control AAPL returns bars (so 404 means absent, not blocked)", live_ok)

    # The free SEC route: a dead issuer exposes both delisting instruments.
    raw = fetch("https://data.sec.gov/submissions/CIK0000886158.json", sec_ua)  # Bed Bath & Beyond
    sub = json.loads(raw)
    recent = sub["filings"]["recent"]
    forms = recent["form"]
    items = recent.get("items", [""] * len(forms))
    has_25 = any(f.startswith("25") for f in forms)
    has_301 = any(f == "8-K" and "3.01" in (items[i] or "") for i, f in enumerate(forms))
    check("SEC submissions expose a Form 25/25-NSE for a dead issuer", has_25)
    check("SEC submissions expose an 8-K item 3.01 without fetching the document", has_301)


def main() -> int:
    print("price-source survey checks")
    test_survey_document()
    test_crash_prices_audit()
    test_store_gap()
    if "--online" in sys.argv:
        test_online()
    else:
        print("\n4. network probes skipped (pass --online to run them)")

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s) failed: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
