"""Every instrument, product and facility a finsim book can trade, as one Excel workbook — one sheet per category.

    python3 tools/universe_workbook.py                       # from the shipped universe and the engines' own tables
    python3 tools/universe_workbook.py --server http://127.0.0.1:8765 --world W-xxxx   # a live save's securities (live prices, listed contracts)
    python3 tools/universe_workbook.py --out finsim-universe.xlsx

Standard library only (the workbook is written by tools/xlsx_writer.py).
"""
import argparse, json, os, sys, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import xlsx_writer  # noqa: E402
from finsim.engines.market import EQUITY_SEED, UNIVERSE, UNIVERSE_AS_OF  # noqa: E402
from finsim.engines.commodities import SPECS as COMMODITY_SPECS  # noqa: E402
from finsim.engines.fx_market import SPECS as FX_SPECS  # noqa: E402
from finsim.engines.otc_extra import NDF_CURRENCIES  # noqa: E402
from finsim.engines.otc import CDS_INDICES  # noqa: E402
from finsim.engines.private_credit import FACILITIES, BORROWERS, SPONSORS  # noqa: E402
from finsim.engines.repo import REPO_SPREAD, COUNTERPARTIES as REPO_COUNTERPARTIES  # noqa: E402
from finsim.engines.private_equity import SECTORS as PE_SECTORS  # noqa: E402
from finsim.engines.investment_banking import KINDS as IB_KINDS  # noqa: E402
from finsim.engines.playbook import GROUPS as PB_GROUPS, STRATEGIES as PB_STRATEGIES  # noqa: E402
from finsim.domain.otc_models import PRODUCTS, BUCKET_BY_PRODUCT  # noqa: E402

CCY_NAME = {"EUR": "Euro", "GBP": "British pound", "JPY": "Japanese yen", "CHF": "Swiss franc", "CAD": "Canadian dollar", "AUD": "Australian dollar", "NZD": "New Zealand dollar", "SEK": "Swedish krona", "NOK": "Norwegian krone", "MXN": "Mexican peso", "BRL": "Brazilian real", "CNH": "Chinese yuan (offshore)", "HKD": "Hong Kong dollar", "SGD": "Singapore dollar", "KRW": "Korean won", "INR": "Indian rupee", "ZAR": "South African rand", "PLN": "Polish zloty"}
CLASS_LABEL = {"EQUITY": "Stock", "ADR": "ADR (foreign stock)", "REIT": "REIT", "PREFERRED": "Preferred stock", "ETF": "ETF", "CRYPTO": "Crypto (spot coin)",
               "INDEX": "Index (options only)", "GOVT_BOND": "US Treasury", "CORP_BOND": "Corporate / sovereign bond", "FUTURE": "Future"}
OTC = {  # product -> (title, family, what you trade, quoted as, on what)
    "IRS": ("Interest rate swap", "Rates", "fixed vs floating (TERM3M) for 1–30y, no principal", "fixed rate", "USD"),
    "OIS": ("Overnight index swap", "Rates", "fixed vs compounded policy rate", "fixed rate", "USD"),
    "FRA": ("Forward rate agreement", "Rates", "one future period's rate, cash-settled", "fixed rate", "USD"),
    "CAP": ("Interest rate cap", "Rates", "strip of options paying when the floating rate fixes above the strike", "upfront premium", "USD"),
    "FLOOR": ("Interest rate floor", "Rates", "strip of options paying when the floating rate fixes below the strike", "upfront premium", "USD"),
    "SWAPTION": ("Swaption", "Rates", "option to enter a payer or receiver swap", "upfront premium", "USD"),
    "INFLATION_SWAP": ("Inflation swap", "Rates", "zero-coupon CPI ratio vs fixed", "fixed rate", "US CPI"),
    "XCCY": ("Cross-currency swap", "FX", "exchange principals and each currency's interest, swap back at the end", "basis spread", "every currency on the Currencies sheet"),
    "FX_FORWARD": ("FX forward", "FX", "buy/sell a currency for a future date at the forward rate", "forward rate", "every deliverable currency"),
    "NDF": ("Non-deliverable forward", "FX", "dollar-settled forward on a non-deliverable currency", "forward rate", ", ".join(NDF_CURRENCIES)),
    "FX_SWAP": ("FX swap", "FX", "spot exchange reversed at the forward: a collateralised currency loan", "far-leg rate", "every currency"),
    "FX_OPTION": ("FX option", "FX", "call or put on a currency vs USD, any strike and expiry", "upfront premium", "every currency"),
    "TRS": ("Total return swap", "Equities", "receive or pay a name's total return vs funding + spread; index TRS on SPY/QQQ/IWM/DIA", "spread over funding (bp)", "any stock, ADR, REIT, ETF or coin"),
    "EQUITY_OPTION": ("OTC equity option", "Equities", "call or put at any strike and expiry, dealer-written", "upfront premium", "any stock/ETF or SPX"),
    "EQUITY_FORWARD": ("Equity forward", "Equities", "buy or sell a name for a future date at the forward price", "forward price", "any stock/ETF"),
    "VARIANCE_SWAP": ("Variance swap", "Volatility", "realised variance vs strike squared × variance notional", "strike (vol points)", "any liquid stock, ETF or SPX"),
    "VOL_SWAP": ("Volatility swap", "Volatility", "notional × (realised vol − strike)", "strike (vol points)", "any liquid stock, ETF or SPX"),
    "DIVIDEND_SWAP": ("Dividend swap", "Equities", "realised dividends vs a fixed amount per unit", "fixed per unit", "any dividend payer"),
    "BARRIER_OPTION": ("Barrier option", "Volatility", "knock-in / knock-out call or put", "upfront premium", "any stock/ETF, SPX, or currency"),
    "DIGITAL_OPTION": ("Digital (binary) option", "Volatility", "fixed payout above/below the strike", "upfront premium", "any stock/ETF, SPX, or currency"),
    "CDS": ("Credit default swap", "Credit", "buy or sell protection on an issuer, sovereign or index; 100/500 running", "spread (bp) / upfront", "every issuer on the CDS names sheet"),
    "COMMODITY_SWAP": ("Commodity swap", "Commodities", "fixed price vs the period-average floating price", "fixed price", "every physical commodity"),
    "COMMODITY_FORWARD": ("Commodity forward", "Commodities", "buy or sell a quantity for a future date at the forward", "forward price", "every physical commodity"),
    "ASIAN_OPTION": ("Asian option", "Commodities", "option on the average spot over its life", "upfront premium", "every physical commodity"),
    "CRYPTO_PERP": ("Crypto perpetual swap", "Crypto", "no-expiry future on a coin, daily funding", "price", "every coin on the Equities & funds sheet"),
    "PPN": ("Principal-protected note", "Structured notes", "zero + call: principal back plus participation in the index gain", "price (% of par)", "SPY/QQQ/IWM/DIA or a stock"),
    "REVERSE_CONVERTIBLE": ("Reverse convertible", "Structured notes", "high coupon plus a down-and-in put on a stock", "coupon", "any stock/ETF"),
    "AUTOCALLABLE": ("Autocallable", "Structured notes", "early redemption with coupons on yearly observations at or above the start", "coupon", "SPY/QQQ/IWM/DIA or a stock"),
}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=600) as r:
        return json.loads(r.read().decode())


def securities_from_seed():
    rows = []
    for t in EQUITY_SEED:
        rows.append({"id": t[0], "name": t[1], "asset_class": t[2], "sector": t[3], "country": t[4], "currency": t[5], "last": t[6], "beta": t[7], "realized_vol": t[8], "adv": t[9], "spread_bps": t[10], "liquidity_tier": t[11], "dividend_yield": t[12], "yahoo": t[14]})
    for b in UNIVERSE.get("bonds", []):
        rows.append({"id": b.get("id") or b.get("ticker"), "name": b.get("name"), "asset_class": "CORP_BOND", "issuer": b.get("issuer"), "coupon": b.get("coupon"), "rating": b.get("rating"), "sector": b.get("sector"), "currency": "USD", "underlying": b.get("equity")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", help="a running finsim server, e.g. http://127.0.0.1:8765")
    ap.add_argument("--world", help="the save whose securities (listed futures, Treasury ladder, live prices) to list")
    ap.add_argument("--portfolio", help="a book in that save (for the listed-option underlyings); the first book if omitted")
    ap.add_argument("--securities-json", help="a saved GET /worlds/{w}/securities payload instead of a server")
    ap.add_argument("--options-json", help="a saved GET .../options payload (underlyings)")
    ap.add_argument("--out", default=f"finsim-universe-{date.today().isoformat()}.xlsx")
    a = ap.parse_args()
    secs, under = None, None
    if a.securities_json:
        secs = json.load(open(a.securities_json))
    elif a.server and a.world:
        secs = fetch(f"{a.server}/api/worlds/{a.world}/securities")
        pf = a.portfolio or fetch(f"{a.server}/api/worlds/{a.world}")["portfolios"][0]["id"]
        under = fetch(f"{a.server}/api/worlds/{a.world}/portfolios/{pf}/options").get("underlyings")
    if a.options_json:
        under = json.load(open(a.options_json)).get("underlyings")
    live = secs is not None
    if secs is None:
        secs = securities_from_seed()
    under = set(under or [])
    by_class = {}
    for s in secs:
        by_class.setdefault(s["asset_class"], []).append(s)
    n = lambda v: None if v is None else float(v)  # noqa: E731

    # ---- Equities & funds
    eq_classes = ["EQUITY", "ADR", "REIT", "PREFERRED", "ETF", "CRYPTO", "INDEX"]
    eq_rows = []
    for cls in eq_classes:
        for s in sorted(by_class.get(cls, []), key=lambda x: x["id"]):
            tradeable = "options only" if cls == "INDEX" else "buy / sell short"
            eq_rows.append([s["id"], s.get("name"), CLASS_LABEL.get(cls, cls), s.get("sector"), s.get("country"), s.get("currency", "USD"), n(s.get("last")), n(s.get("change_pct")), s.get("liquidity_tier"), n(s.get("adv")), n(s.get("dividend_yield")), n(s.get("beta")), n(s.get("realized_vol")), tradeable,
                           "yes" if (s["id"] in under or cls == "INDEX") else "no", "yes" if cls not in ("INDEX",) else "no", "yes" if cls in ("ETF", "EQUITY", "ADR", "REIT") else "no", "yes" if cls != "INDEX" else "no", "yes" if cls == "CRYPTO" else "no", s.get("yahoo") or ""])
    eq_hdr = ["Ticker", "Name", "Category", "Sector", "Country", "Ccy", "Last", "Change %", "Liquidity tier", "Avg daily volume", "Dividend yield", "Beta", "Realised vol", "Cash trade", "Listed options", "Securities lending (borrow to short)", "Repo-able", "TRS / OTC option", "Perpetual swap", "Yahoo symbol"]

    # ---- Bonds
    bond_rows = []
    for cls in ("GOVT_BOND", "CORP_BOND"):
        for s in sorted(by_class.get(cls, []), key=lambda x: (x.get("maturity") or "", x["id"])):
            kind = "US Treasury bill" if cls == "GOVT_BOND" and (s.get("coupon") in (0, 0.0, None) or s.get("is_bill")) else CLASS_LABEL[cls]
            if cls == "CORP_BOND" and str(s["id"]).startswith("SOV-"):
                kind = "Sovereign bond (USD)"
            bond_rows.append([s["id"], s.get("name"), kind, s.get("issuer"), s.get("sector"), s.get("rating"), n(s.get("coupon")), s.get("maturity"), n(s.get("years_to_maturity")), n(s.get("last")), n(s.get("ytm")), n(s.get("spread_to_curve_bps") or s.get("credit_spread_bps")), n(s.get("modified_duration")), n(s.get("dv01_per_100")), s.get("underlying") or "", "yes", "yes" if cls == "CORP_BOND" else "no (Treasuries carry no CDS)", "yes"])
    bond_hdr = ["ID", "Name", "Category", "Issuer", "Sector", "Rating", "Coupon", "Maturity", "Years", "Price", "YTM", "Spread bp", "Mod. duration", "DV01 / 100", "Issuer's stock", "Cash trade", "CDS on the issuer", "Repo / reverse repo"]

    # ---- Futures
    spec = {s.code: s for s in COMMODITY_SPECS}
    fut_rows = []
    for s in sorted(by_class.get("FUTURE", []), key=lambda x: ((x.get("underlying_class") or ""), x.get("underlying") or "", x.get("expiry") or "")):
        code = s.get("underlying") or s["id"][:-3]
        sp = spec.get(code)
        fut_rows.append([s["id"], s.get("name"), (s.get("underlying_class") or (sp.group if sp else "")).replace("_", " ").title(), code, s.get("contract_month"), s.get("expiry"), n(s.get("last")), n(s.get("multiplier")), s.get("unit") or (sp.unit if sp else ""), n(s.get("tick_size")), n(s.get("initial_margin")), n(s.get("notional_per_contract")), "yes" if s["id"] in under else "no", "physical (inventory on delivery)" if sp and sp.group.startswith("COMMODITY") else "cash-settled / financial"])
    if not fut_rows:
        for sp in COMMODITY_SPECS:
            fut_rows.append([sp.code + "<month>", sp.name, sp.group.replace("_", " ").title(), sp.code, "months " + sp.months, "", None, sp.multiplier, sp.unit, sp.tick, None, None, "front two contracts" if sp.group.startswith("COMMODITY") else "no", "physical" if sp.group.startswith("COMMODITY") else "financial"])
    fut_hdr = ["Contract", "Name", "Category", "Root", "Month", "Expiry / last trade", "Settlement", "Multiplier", "Unit", "Tick", "Initial margin", "Notional / contract", "Listed options", "Delivery"]

    # ---- Listed options
    opt_rows = []
    for u in sorted(under):
        s = next((x for x in secs if x["id"] == u), None)
        cls = s["asset_class"] if s else ("INDEX" if u in ("SPX", "NDX", "RUT", "DJX", "VIX") else "FUTURE")
        if cls == "FUTURE":
            rule = "one expiry three sessions before the contract's last trade; 13 strikes around the settlement; American; delivers the future"
        elif cls == "INDEX":
            rule = "four monthly third-Friday expiries + two quarterlies; 13 strikes around spot; European, cash-settled"
        elif cls == "CRYPTO":
            rule = "four monthly + two quarterly expiries; 13 strikes around spot; delivers the coin"
        else:
            rule = "four monthly third-Friday expiries + two quarterlies; 13 strikes around spot; American, 100 shares"
        opt_rows.append([u, s.get("name") if s else u, CLASS_LABEL.get(cls, cls), "calls and puts", rule, "16 packages: covered call, protective put, collar, verticals, straddle, strangle, butterfly, condor, calendar, diagonal, ratio, risk reversal…"])
    opt_hdr = ["Underlying", "Name", "Underlying type", "Contracts", "Listing rule", "Strategies (one instruction)"]

    # ---- Currencies
    ccy_rows = [["USD", "US dollar", "base currency", None, None, "cash accounts in every currency; interest accrues at each currency's policy rate", "", "", "", "", ""]]
    fxu = UNIVERSE.get("fx", {})
    for code, sp in FX_SPECS.items():
        ndf = code in NDF_CURRENCIES
        ccy_rows.append([code, CCY_NAME.get(code, code), "deliverable" if not ndf else "non-deliverable (NDF)", n(fxu.get(code)) if not isinstance(fxu.get(code), dict) else n(fxu[code].get("spot")), sp.rate0, "spot (T+2)", "no (NDF)" if ndf else "yes", "yes", "yes", "yes", "yes"])
    ccy_hdr = ["Currency", "Name", "Category", "Spot (USD per unit, as of universe)", "Policy rate (sim)", "Spot", "Forward", "FX swap", "Cross-currency swap", "FX option (vanilla, barrier, digital)", "Futures / index"]
    for r in ccy_rows[1:]:
        code = r[0]
        r[6] = "NDF (dollar-settled)" if code in NDF_CURRENCIES else "yes"
        r[10] = "DX future (dollar index)" if code in ("EUR", "JPY", "GBP", "CAD", "SEK", "CHF") else ""

    # ---- Commodities
    com_rows = []
    cu = UNIVERSE.get("commodities", {})
    for sp in COMMODITY_SPECS:
        phys = sp.group.startswith("COMMODITY")
        com_rows.append([sp.code, sp.name, sp.group.replace("_", " ").title(), sp.unit, n(cu.get(sp.code)) if phys else None, "SPOT:" + sp.code if phys else "", "months " + sp.months, sp.multiplier, "yes" if phys else "no", "yes" if phys else "no", "yes" if phys else "no", "yes" if phys else "no", "front two contracts" if phys else "no", "physical delivery into inventory; storage charged daily" if phys else "cash-settled"])
    com_hdr = ["Code", "Name", "Category", "Unit", "Spot (universe)", "Spot quote id", "Futures months", "Contract size", "Futures", "Commodity swap", "Commodity forward", "Asian option", "Options on futures", "Delivery"]

    # ---- OTC
    otc_rows = [[p, OTC[p][0], OTC[p][1], BUCKET_BY_PRODUCT.get(p, ""), OTC[p][2], OTC[p][3], OTC[p][4], "RFQ to seven dealers under ISDA/CSA; unwind any day at the dealer's price"] for p in PRODUCTS]
    otc_hdr = ["Product", "Name", "Family", "P&L bucket", "What you trade", "Quoted as", "Reference universe", "How"]

    # ---- CDS names
    cds_rows = []
    seen = set()
    for s in by_class.get("CORP_BOND", []):
        iss = s.get("issuer") or s["id"]
        if iss in seen:
            continue
        seen.add(iss)
        sov = str(s["id"]).startswith("SOV-")
        cds_rows.append([iss, "Sovereign" if sov else "Corporate", s.get("sector"), s.get("rating"), ", ".join(sorted(x["id"] for x in by_class["CORP_BOND"] if (x.get("issuer") or x["id"]) == iss)), s.get("underlying") or "", "single-name CDS, 1–10y, 100 or 500 running"])
    for k, v in CDS_INDICES.items():
        cds_rows.append([k, "Index", v[0], v[1].upper(), "", "", "index CDS, 5y, 100 or 500 running"])
    cds_hdr = ["Reference entity", "Category", "Sector", "Rating", "Deliverable bonds", "Issuer's stock", "Contract"]

    # ---- Financing & lending
    fin_rows = [
        ["Securities lending", "Borrow to short", "every stock, ADR, REIT, ETF and coin", "borrow rate = GC / hard-to-borrow / special by utilisation; cash or securities collateral; recalls", "State Street, BNY Mellon, J.P. Morgan agency lending"],
        ["Securities lending", "Lend your longs", "any long position", "fee income against cash collateral with a rebate", "Citadel, Millennium, Morgan Stanley, Goldman Sachs borrowers"],
        ["Repo", "Repo (borrow cash)", "Treasuries, IG and HY corporates, ETFs, large and mid-cap stocks", "spread over policy: " + ", ".join(f"{k} {v*1e4:.0f}bp" for k, v in REPO_SPREAD.items()) + "; haircuts; term or open; rolls", ", ".join(REPO_COUNTERPARTIES)],
        ["Repo", "Reverse repo (lend cash)", "same collateral set", "earn the repo rate against collateral", ", ".join(REPO_COUNTERPARTIES)],
        ["Prime brokerage", "Margin loan", "the whole book", "excess liquidity, margin calls, financing at policy + spread", "prime broker"],
        ["FX", "Multi-currency cash", "every currency on the Currencies sheet", "settled and pending balances; interest at each currency's policy rate", "Citi FX"],
    ]
    for f, (bp, oid, rec, lev, ratings) in FACILITIES.items():
        fin_rows.append(["Private credit", f.replace("_", " ").title(), f"sponsor-backed borrowers in {len(BORROWERS)} sectors ({', '.join(sorted(BORROWERS))})", f"~{bp}bp over policy, OID {oid}pt, recovery {rec:.0%}, leverage {lev[0]}–{lev[1]}x, ratings {'/'.join(sorted(set(ratings)))}", ", ".join(SPONSORS[:6]) + "…"])
    fin_hdr = ["Category", "Facility", "Eligible", "Terms", "Counterparties"]

    # ---- Playbook
    pb_rows = []
    groups = dict(PB_GROUPS)
    strategies = PB_STRATEGIES
    for st in strategies:
        legs = "; ".join(f"{l.get('side', '').lower()} {l.get('kind', '').lower()}{' ' + l['type'] if l.get('type') else ''}{' @ ' + str(l['strike']) if l.get('strike') else ''}{' ×' + str(l['size']) if l.get('size') not in (None, 1) else ''}" for l in st.get("legs", []))
        pb_rows.append([st.get("key"), st.get("title"), groups.get(st.get("group"), st.get("group")), st.get("simple") or st.get("thesis"), st.get("best_when"), st.get("risk"), legs])
    pb_hdr = ["Key", "Strategy", "Group", "What you're trading", "Best when", "Risk", "Legs"]

    # ---- Desks (simulated saves only)
    desk_rows = [["Private equity", "Leveraged buyouts", ", ".join(PE_SECTORS), "monthly deal flow, diligence, bid at a multiple with a debt package, quarterly reports, covenants, add-ons and cost programs, dividend recaps, sale or IPO exit", "simulated saves; switched off when a save tracks the real market"]]
    for k, v in IB_KINDS.items():
        desk_rows.append(["Investment banking", v["label"], "invented clients", v.get("note", ""), "simulated saves only"])
    desk_hdr = ["Desk", "Product", "Universe", "How it works", "Availability"]

    # ---- Summary
    summary = [
        ["Equities & funds", len(eq_rows), "cash long or short, listed options, TRS, OTC options and forwards, securities lending, repo (ETFs, large/mid caps), perps on coins", "Market Place → Equities / ETFs / Crypto"],
        ["Bonds", len(bond_rows), "Treasury bills (1M/3M/6M) and notes/bonds, corporates, USD sovereigns; repo; CDS on the issuer", "Market Place → Bonds"],
        ["Futures", len(fut_rows), "energy, metals, ags, livestock, equity index, Treasuries, SOFR, BTC/ETH, dollar index, VIX; options on the physical commodities' front contracts", "Market Place → Futures"],
        ["Listed options", len(opt_rows), "chains on every large-cap name, ETF and liquid coin, the indices (SPX/NDX/RUT/DJX/VIX) and the front commodity futures; 16 one-instruction packages", "Options"],
        ["Currencies", len(ccy_rows), "spot, forwards, NDFs, FX swaps, cross-currency swaps, FX options; cash accounts in every currency", "Market Place → FX / Treasury"],
        ["Commodities (spot / OTC)", len(com_rows), "physical inventory via delivery, commodity swaps, forwards, Asian options", "Commodity desk"],
        ["OTC derivatives", len(otc_rows), "28 dealer products under ISDA/CSA across rates, FX, equities, volatility, credit, commodities, crypto and structured notes", "OTC Derivatives"],
        ["CDS names", len(cds_rows), "single-name CDS on every bond issuer and sovereign plus CDX IG/HY and iTraxx Main/Xover", "OTC Derivatives → CDS"],
        ["Financing & lending", len(fin_rows), "securities lending, repo / reverse repo, prime-broker margin, private credit facilities", "Lending"],
        ["Playbook strategies", len(pb_rows), "sized, previewed and executed as one package", "Calculations"],
        ["Desks (simulated saves)", len(desk_rows), "private equity buyouts and investment-banking mandates", "Private Equity / Investment Banking"],
    ]
    summary_hdr = ["Category", "Rows", "What you can do", "Where in finsim"]
    note = [["", None, f"Universe as of {UNIVERSE_AS_OF}; " + ("prices and listed contracts from the live save on " + date.today().isoformat() if live else "listed contracts are generated per save (this file shows the roots and rules)"), ""]]

    xlsx_writer.write(a.out, [
        ("Summary", summary_hdr, summary + note),
        ("Equities & funds", eq_hdr, eq_rows, None, [7, 10, 12]),
        ("Bonds", bond_hdr, bond_rows, None, [6, 10]),
        ("Futures", fut_hdr, fut_rows),
        ("Listed options", opt_hdr, opt_rows, [12, 40, 22, 16, 90, 90]),
        ("Currencies", ccy_hdr, ccy_rows, None, [4]),
        ("Commodities", com_hdr, com_rows),
        ("OTC derivatives", otc_hdr, otc_rows, [20, 28, 16, 12, 70, 24, 44, 60]),
        ("CDS names", cds_hdr, cds_rows, [40, 12, 26, 8, 40, 12, 40]),
        ("Financing & lending", fin_hdr, fin_rows, [18, 26, 60, 90, 60]),
        ("Playbook strategies", pb_hdr, pb_rows, [24, 30, 24, 80, 60, 60, 60]),
        ("Desks", desk_hdr, desk_rows, [20, 30, 60, 100, 40]),
    ])
    print(f"wrote {a.out}: " + ", ".join(f"{r[0]} {r[1]}" for r in summary))


if __name__ == "__main__":
    main()
