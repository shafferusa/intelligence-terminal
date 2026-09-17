"""Refresh the real-world universe snapshot (finsim/data/universe.json).

Sources (both free, no keys):
  * Yahoo Finance chart API — last price, one year of daily closes (realized vol, beta vs SPY), 3-month
    average volume, trailing dividends. Browser-style User-Agent as the project runbook allows for Yahoo.
  * SEC EDGAR company facts (XBRL) — shares outstanding, latest annual revenue / net income / diluted EPS,
    latest quarterly debt, cash, equity, operating cash flow and capex. Identified User-Agent, <10 req/s.

The snapshot is what the game starts from; from day one the simulation generates its own prices.
Run:  python3 tools/refresh_universe.py [--out finsim/data/universe.json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

YAHOO_UA = "Mozilla/5.0"
SEC_UA = "LoganTerminal/1.0 (loganshaffer87@gmail.com)"

# ticker, name, asset class, GICS sector, country, yahoo symbol (if different), EDGAR ticker (if different)
UNIVERSE = [
    # Information Technology
    ("AAPL", "Apple Inc.", "EQUITY", "Information Technology", "US"),
    ("MSFT", "Microsoft Corporation", "EQUITY", "Information Technology", "US"),
    ("NVDA", "NVIDIA Corporation", "EQUITY", "Information Technology", "US"),
    ("AVGO", "Broadcom Inc.", "EQUITY", "Information Technology", "US"),
    ("ORCL", "Oracle Corporation", "EQUITY", "Information Technology", "US"),
    ("CRM", "Salesforce, Inc.", "EQUITY", "Information Technology", "US"),
    ("AMD", "Advanced Micro Devices, Inc.", "EQUITY", "Information Technology", "US"),
    ("INTC", "Intel Corporation", "EQUITY", "Information Technology", "US"),
    # Communication Services
    ("GOOGL", "Alphabet Inc.", "EQUITY", "Communication Services", "US"),
    ("META", "Meta Platforms, Inc.", "EQUITY", "Communication Services", "US"),
    ("NFLX", "Netflix, Inc.", "EQUITY", "Communication Services", "US"),
    ("DIS", "The Walt Disney Company", "EQUITY", "Communication Services", "US"),
    ("VZ", "Verizon Communications Inc.", "EQUITY", "Communication Services", "US"),
    ("T", "AT&T Inc.", "EQUITY", "Communication Services", "US"),
    # Consumer Discretionary
    ("AMZN", "Amazon.com, Inc.", "EQUITY", "Consumer Discretionary", "US"),
    ("TSLA", "Tesla, Inc.", "EQUITY", "Consumer Discretionary", "US"),
    ("HD", "The Home Depot, Inc.", "EQUITY", "Consumer Discretionary", "US"),
    ("MCD", "McDonald's Corporation", "EQUITY", "Consumer Discretionary", "US"),
    ("NKE", "NIKE, Inc.", "EQUITY", "Consumer Discretionary", "US"),
    ("F", "Ford Motor Company", "EQUITY", "Consumer Discretionary", "US"),
    ("RIVN", "Rivian Automotive, Inc.", "EQUITY", "Consumer Discretionary", "US"),
    # Consumer Staples
    ("PG", "The Procter & Gamble Company", "EQUITY", "Consumer Staples", "US"),
    ("KO", "The Coca-Cola Company", "EQUITY", "Consumer Staples", "US"),
    ("PEP", "PepsiCo, Inc.", "EQUITY", "Consumer Staples", "US"),
    ("WMT", "Walmart Inc.", "EQUITY", "Consumer Staples", "US"),
    ("COST", "Costco Wholesale Corporation", "EQUITY", "Consumer Staples", "US"),
    ("BYND", "Beyond Meat, Inc.", "EQUITY", "Consumer Staples", "US"),
    # Health Care
    ("LLY", "Eli Lilly and Company", "EQUITY", "Health Care", "US"),
    ("UNH", "UnitedHealth Group Incorporated", "EQUITY", "Health Care", "US"),
    ("JNJ", "Johnson & Johnson", "EQUITY", "Health Care", "US"),
    ("PFE", "Pfizer Inc.", "EQUITY", "Health Care", "US"),
    ("MRK", "Merck & Co., Inc.", "EQUITY", "Health Care", "US"),
    # Financials
    ("JPM", "JPMorgan Chase & Co.", "EQUITY", "Financials", "US"),
    ("GS", "The Goldman Sachs Group, Inc.", "EQUITY", "Financials", "US"),
    ("BAC", "Bank of America Corporation", "EQUITY", "Financials", "US"),
    ("MS", "Morgan Stanley", "EQUITY", "Financials", "US"),
    ("BRK-B", "Berkshire Hathaway Inc. (Class B)", "EQUITY", "Financials", "US"),
    ("V", "Visa Inc.", "EQUITY", "Financials", "US"),
    ("BLK", "BlackRock, Inc.", "EQUITY", "Financials", "US"),
    # Industrials
    ("CAT", "Caterpillar Inc.", "EQUITY", "Industrials", "US"),
    ("GE", "GE Aerospace", "EQUITY", "Industrials", "US"),
    ("UNP", "Union Pacific Corporation", "EQUITY", "Industrials", "US"),
    ("BA", "The Boeing Company", "EQUITY", "Industrials", "US"),
    ("RTX", "RTX Corporation", "EQUITY", "Industrials", "US"),
    ("UPS", "United Parcel Service, Inc.", "EQUITY", "Industrials", "US"),
    ("AAL", "American Airlines Group Inc.", "EQUITY", "Industrials", "US"),
    # Energy
    ("XOM", "Exxon Mobil Corporation", "EQUITY", "Energy", "US"),
    ("CVX", "Chevron Corporation", "EQUITY", "Energy", "US"),
    ("COP", "ConocoPhillips", "EQUITY", "Energy", "US"),
    ("SLB", "Schlumberger Limited", "EQUITY", "Energy", "US"),
    ("OXY", "Occidental Petroleum Corporation", "EQUITY", "Energy", "US"),
    # Materials
    ("LIN", "Linde plc", "EQUITY", "Materials", "US"),
    ("FCX", "Freeport-McMoRan Inc.", "EQUITY", "Materials", "US"),
    ("NEM", "Newmont Corporation", "EQUITY", "Materials", "US"),
    ("NUE", "Nucor Corporation", "EQUITY", "Materials", "US"),
    # Utilities
    ("NEE", "NextEra Energy, Inc.", "EQUITY", "Utilities", "US"),
    ("DUK", "Duke Energy Corporation", "EQUITY", "Utilities", "US"),
    ("SO", "The Southern Company", "EQUITY", "Utilities", "US"),
    # Real Estate
    ("PLD", "Prologis, Inc.", "REIT", "Real Estate", "US"),
    ("AMT", "American Tower Corporation", "REIT", "Real Estate", "US"),
    ("O", "Realty Income Corporation", "REIT", "Real Estate", "US"),
    # ADRs
    ("TSM", "Taiwan Semiconductor Manufacturing (ADR)", "ADR", "Information Technology", "TW"),
    ("TM", "Toyota Motor Corporation (ADR)", "ADR", "Consumer Discretionary", "JP"),
    # Preferred
    ("BAC-PL", "Bank of America 7.25% Series L Preferred", "PREFERRED", "Financials", "US"),
    # ETFs
    ("SPY", "SPDR S&P 500 ETF Trust", "ETF", "Index", "US"),
    ("QQQ", "Invesco QQQ Trust", "ETF", "Index", "US"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", "ETF", "Government", "US"),
    ("HYG", "iShares iBoxx $ High Yield Corporate Bond ETF", "ETF", "Credit", "US"),
    ("SHV", "iShares Short Treasury Bond ETF", "ETF", "Government", "US"),
]

# representative senior unsecured issues of real issuers (coupon, years to maturity, S&P-style rating, spread bps, ADV face)
BONDS = [
    ("JPM-29", "JPMorgan Chase 4.85% 2029", "JPMorgan Chase & Co.", "JPM", 0.0485, 3.0, "A", 80.0, 25_000_000, "Financials"),
    ("F-32", "Ford Motor Credit 6.10% 2032", "Ford Motor Credit Company", "F", 0.061, 6.0, "BBB-", 190.0, 18_000_000, "Consumer Discretionary"),
    ("AAL-28", "American Airlines 7.25% 2028", "American Airlines Group Inc.", "AAL", 0.0725, 2.5, "B+", 420.0, 8_000_000, "Industrials"),
]

RATE_SYMBOLS = {"bill_13w": "^IRX", "y5": "^FVX", "y10": "^TNX", "y30": "^TYX"}
# front-month futures as the spot anchor for each simulated commodity (Yahoo continuous symbols)
COMMODITY_SYMBOLS = {"CL": "CL=F", "BRN": "BZ=F", "NG": "NG=F", "RB": "RB=F", "HO": "HO=F", "GC": "GC=F", "SI": "SI=F", "HG": "HG=F", "PL": "PL=F", "PA": "PA=F",
                     "ALI": "ALI=F", "ZC": "ZC=F", "ZW": "ZW=F", "ZS": "ZS=F", "KC": "KC=F", "SB": "SB=F", "CT": "CT=F", "CC": "CC=F", "LE": "LE=F", "GF": "GF=F", "HE": "HE=F"}
CENTS_QUOTED = {"ZC", "ZW", "ZS", "KC", "SB", "CT", "LE", "GF", "HE"}     # Yahoo quotes these in cents; the simulator uses dollars per unit
# spot in USD per unit of foreign currency
FX_SYMBOLS = {"EUR": ("EURUSD=X", False), "GBP": ("GBPUSD=X", False), "JPY": ("JPY=X", True), "CHF": ("CHF=X", True), "CAD": ("CAD=X", True), "AUD": ("AUDUSD=X", False)}


_last_yahoo = [0.0]


def get_json(url: str, ua: str, retries: int = 5):
    for i in range(retries):
        try:
            if "yahoo.com" in url:                      # polite pacing: Yahoo rate-limits bursts
                wait = 2.0 - (time.time() - _last_yahoo[0])
                if wait > 0:
                    time.sleep(wait)
                _last_yahoo[0] = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(20 * (i + 1))
                continue
            if i == retries - 1:
                print(f"  ! {url}: {e!r}", file=sys.stderr)
                return None
            time.sleep(1.5 * (i + 1))
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                print(f"  ! {url}: {e!r}", file=sys.stderr)
                return None
            time.sleep(1.5 * (i + 1))


def yahoo_chart(symbol: str, rng: str = "1y"):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d&events=div"
    d = get_json(url, YAHOO_UA)
    try:
        res = d["chart"]["result"][0]
    except Exception:  # noqa: BLE001
        return None
    q = res["indicators"]["quote"][0]
    closes = [c for c in q.get("close", []) if c is not None]
    vols = [v for v in q.get("volume", []) if v is not None]
    divs = [v["amount"] for v in (res.get("events", {}).get("dividends", {}) or {}).values()]
    meta = res["meta"]
    return {"price": meta.get("regularMarketPrice"), "closes": closes, "volumes": vols, "dividends": divs,
            "high52": meta.get("fiftyTwoWeekHigh"), "low52": meta.get("fiftyTwoWeekLow"), "name": meta.get("longName") or meta.get("shortName")}


def log_returns(closes):
    return [math.log(b / a) for a, b in zip(closes[:-1], closes[1:]) if a and b and a > 0 and b > 0]


def edgar_facts(cik: int):
    d = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", SEC_UA)
    time.sleep(0.15)
    if not d:
        return {}
    facts = d.get("facts", {})
    us = facts.get("us-gaap", {})
    dei = facts.get("dei", {})

    def latest(tag, ns, forms=("10-K", "10-Q", "20-F", "40-F"), annual=False):
        v = ns.get(tag)
        if not v:
            return None
        rows = []
        for unit, arr in v["units"].items():
            for r in arr:
                if r.get("form") not in forms:
                    continue
                if annual:
                    if not (r.get("fp") == "FY" and r.get("start") and r.get("end")):
                        continue
                    from datetime import date as _d
                    days = (_d.fromisoformat(r["end"]) - _d.fromisoformat(r["start"])).days
                    if days < 300:
                        continue
                rows.append(r)
        if not rows:
            return None
        rows.sort(key=lambda r: (r["end"], r.get("filed", "")))
        return rows[-1]["val"], rows[-1]["end"]

    def first(tags, ns=us, **kw):
        for t in tags:
            x = latest(t, ns, **kw)
            if x is not None:
                return x
        return None

    out = {}
    sh = first(["EntityCommonStockSharesOutstanding"], dei)
    if sh:
        out["shares_outstanding"], out["shares_asof"] = sh
    rev = first(["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenuesNetOfInterestExpense",
                 "InterestAndDividendIncomeOperating"], annual=True)
    if rev:
        out["revenue"], out["fiscal_year_end"] = rev
    ni = first(["NetIncomeLoss", "ProfitLoss"], annual=True)
    if ni:
        out["net_income"] = ni[0]
    eps = first(["EarningsPerShareDiluted", "EarningsPerShareBasic"], annual=True)
    if eps:
        out["eps"] = eps[0]
    debt = first(["LongTermDebt", "LongTermDebtNoncurrent", "DebtInstrumentCarryingAmount", "LongTermDebtAndCapitalLeaseObligations"])
    if debt:
        out["total_debt"] = debt[0]
    cash = first(["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"])
    if cash:
        out["cash"] = cash[0]
    eq = first(["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])
    if eq:
        out["equity"] = eq[0]
    cfo = first(["NetCashProvidedByUsedInOperatingActivities"], annual=True)
    capex = first(["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], annual=True)
    if cfo:
        out["cfo"] = cfo[0]
        if capex:
            out["capex"] = capex[0]
    return out


def fetch_extras():
    rates = {}
    for k, sym in RATE_SYMBOLS.items():
        ch = yahoo_chart(sym, "5d")
        if ch and ch["price"] is not None:
            rates[k] = round(float(ch["price"]) / 100, 5)
    commodities = {}
    for code, sym in COMMODITY_SYMBOLS.items():
        ch = yahoo_chart(sym, "5d")
        if ch and ch["price"] is not None:
            commodities[code] = round(float(ch["price"]) / (100.0 if code in CENTS_QUOTED else 1.0), 4)
    fx = {}
    for ccy, (sym, inverse) in FX_SYMBOLS.items():
        ch = yahoo_chart(sym, "5d")
        if ch and ch["price"] is not None:
            v = float(ch["price"])
            fx[ccy] = round((1.0 / v) if inverse else v, 6)
    return {"rates": rates, "commodities": commodities, "fx": fx}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "data", "universe.json"))
    ap.add_argument("--extras-only", action="store_true", help="keep the equities in the existing file; refresh rates, commodities and FX only")
    args = ap.parse_args()
    if args.extras_only:
        with open(args.out) as f:
            old = json.load(f)
        extras = fetch_extras()
        old.update(extras)
        with open(args.out, "w") as f:
            json.dump(old, f, indent=1)
        print(f"updated {args.out}: {extras}")
        return
    print("fetching SEC ticker map …")
    tick = get_json("https://www.sec.gov/files/company_tickers.json", SEC_UA) or {}
    cik_by_ticker = {v["ticker"].upper(): int(v["cik_str"]) for v in tick.values()}
    spy = yahoo_chart("SPY")
    spy_r = log_returns(spy["closes"]) if spy else []
    equities = []
    for row in UNIVERSE:
        t, name, ac, sector, country = row[:5]
        print(f"{t:8s} {name}")
        ch = yahoo_chart(t)
        if not ch or not ch["closes"]:
            print("  ! no price data, skipped", file=sys.stderr)
            continue
        r = log_returns(ch["closes"])
        sigma = statistics.pstdev(r) * math.sqrt(252) if len(r) > 20 else 0.3
        beta = 0.0
        if len(r) > 20 and len(spy_r) > 20:
            n = min(len(r), len(spy_r))
            a, b = r[-n:], spy_r[-n:]
            ma, mb = sum(a) / n, sum(b) / n
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
            var = sum((y - mb) ** 2 for y in b) / n
            beta = cov / var if var else 0.0
        adv = int(sum(ch["volumes"][-63:]) / max(1, len(ch["volumes"][-63:]))) if ch["volumes"] else 0
        divs = sum(ch["dividends"])
        px = float(ch["price"])
        dy = divs / px if px else 0.0
        rec = {"ticker": t, "name": name, "asset_class": ac, "sector": sector, "country": country, "price": round(px, 4), "beta": round(beta, 3),
               "sigma_annual": round(sigma, 4), "adv": adv, "dividend_yield": round(dy, 5), "trailing_dividends": round(divs, 4),
               "high_52w": ch["high52"], "low_52w": ch["low52"]}
        if ac in ("EQUITY", "REIT", "ADR"):
            cik = cik_by_ticker.get(t.replace("-", ""))
            if cik:
                rec["cik"] = cik
                rec.update(edgar_facts(cik))
        if ac == "PREFERRED":
            rec["shares_outstanding"] = 6_900_000            # BAC Series L: ~6.9M depositary shares (prospectus)
        equities.append(rec)
    out = {"as_of": date.today().isoformat(), "sources": {"prices": "Yahoo Finance chart API (last price, 1y closes, 3m volume, trailing dividends)",
                                                          "fundamentals": "SEC EDGAR XBRL company facts (latest annual income statement, latest quarterly balance sheet, shares outstanding)",
                                                          "rates": "Yahoo Finance ^IRX ^FVX ^TNX ^TYX", "commodities": "Yahoo Finance front-month continuous futures",
                                                          "fx": "Yahoo Finance spot crosses"},
           "equities": equities, "bonds": [dict(zip(("id", "name", "issuer", "issuer_ticker", "coupon", "years", "rating", "spread_bps", "adv", "sector"), b)) for b in BONDS]}
    out.update(fetch_extras())
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {args.out}: {len(equities)} equities, rates {rates}")


if __name__ == "__main__":
    main()
