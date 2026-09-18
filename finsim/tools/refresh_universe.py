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

# the names: tools/universe_seed.py (stocks, ETFs, crypto, bonds)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe_seed import UNIVERSE, BONDS, CRYPTO_SUPPLY_M   # noqa: E402

RATE_SYMBOLS = {"bill_13w": "^IRX", "y5": "^FVX", "y10": "^TNX", "y30": "^TYX"}
# front-month futures as the spot anchor for each simulated commodity (Yahoo continuous symbols)
COMMODITY_SYMBOLS = {"CL": "CL=F", "BRN": "BZ=F", "NG": "NG=F", "RB": "RB=F", "HO": "HO=F", "GC": "GC=F", "SI": "SI=F", "HG": "HG=F", "PL": "PL=F", "PA": "PA=F",
                     "ALI": "ALI=F", "ZC": "ZC=F", "ZW": "ZW=F", "ZS": "ZS=F", "KC": "KC=F", "SB": "SB=F", "CT": "CT=F", "CC": "CC=F", "LE": "LE=F", "GF": "GF=F", "HE": "HE=F"}
CENTS_QUOTED = {"ZC", "ZW", "ZS", "KC", "SB", "CT", "LE", "GF", "HE"}     # Yahoo quotes these in cents; the simulator uses dollars per unit
# spot in USD per unit of foreign currency
FX_SYMBOLS = {"EUR": ("EURUSD=X", False), "GBP": ("GBPUSD=X", False), "JPY": ("JPY=X", True), "CHF": ("CHF=X", True), "CAD": ("CAD=X", True), "AUD": ("AUDUSD=X", False),
              "NZD": ("NZDUSD=X", False), "SEK": ("SEK=X", True), "NOK": ("NOK=X", True), "MXN": ("MXN=X", True), "BRL": ("BRL=X", True), "CNH": ("CNH=X", True),
              "HKD": ("HKD=X", True), "SGD": ("SGD=X", True), "KRW": ("KRW=X", True), "INR": ("INR=X", True), "ZAR": ("ZAR=X", True), "PLN": ("PLN=X", True)}


_last_yahoo = [0.0]


def get_json(url: str, ua: str, retries: int = 5):
    for i in range(retries):
        try:
            if "yahoo.com" in url:                      # polite pacing: Yahoo rate-limits bursts
                wait = 0.8 - (time.time() - _last_yahoo[0])
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


INDEX_SYMBOLS = {"DXY": "DX-Y.NYB", "VIX": "^VIX"}


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
    indices = {}
    for code, sym in INDEX_SYMBOLS.items():
        ch = yahoo_chart(sym, "1y")
        if ch and ch["price"] is not None:
            r = log_returns(ch["closes"])
            indices[code] = {"price": round(float(ch["price"]), 4), "sigma_annual": round(statistics.pstdev(r) * math.sqrt(252), 4) if len(r) > 20 else 0.1}
    return {"rates": rates, "commodities": commodities, "fx": fx, "indices": indices}


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
        ysym = row[5] if len(row) > 5 and row[5] else t
        print(f"{t:8s} {name}", flush=True)
        ch = yahoo_chart(ysym)
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
        if ysym != t:
            rec["yahoo"] = ysym
        if ac == "CRYPTO":
            rec["dividend_yield"], rec["trailing_dividends"] = 0.0, 0.0
            rec["shares_outstanding"] = int(CRYPTO_SUPPLY_M.get(t, 100.0) * 1e6)          # circulating supply, approximate
        if ac in ("EQUITY", "REIT", "ADR"):
            cik = cik_by_ticker.get(t.replace("-", ""))
            if cik:
                rec["cik"] = cik
                rec.update(edgar_facts(cik))
        if ac == "PREFERRED":
            rec["shares_outstanding"] = {"BAC-PL": 6_900_000, "WFC-PL": 4_025_000}.get(t, 20_000_000)   # depositary shares (prospectuses; approximate for the rest)
        equities.append(rec)
    out = {"as_of": date.today().isoformat(), "sources": {"prices": "Yahoo Finance chart API (last price, 1y closes, 3m volume, trailing dividends)",
                                                          "fundamentals": "SEC EDGAR XBRL company facts (latest annual income statement, latest quarterly balance sheet, shares outstanding)",
                                                          "rates": "Yahoo Finance ^IRX ^FVX ^TNX ^TYX", "commodities": "Yahoo Finance front-month continuous futures",
                                                          "fx": "Yahoo Finance spot crosses"},
           "equities": equities, "bonds": [dict(zip(("id", "name", "issuer", "issuer_ticker", "coupon", "years", "rating", "spread_bps", "adv", "sector"), b)) for b in BONDS]}
    extras = fetch_extras()
    out.update(extras)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {args.out}: {len(equities)} equities, {len(extras.get('commodities', {}))} commodities, rates {extras.get('rates')}")


if __name__ == "__main__":
    main()
