"""The FinSim2 research universe, FRED macro series and research horizons.

Every asset is a plain dict::

    {"id", "name", "asset_class", "sector", "country", "currency", "yahoo", "cik", "duration", "convexity",
     "fred", "benchmark", "meta"}

* ``yahoo``  - Yahoo Finance chart symbol (None for synthetic FRED-built indices).
* ``cik``    - SEC Central Index Key (int) for EQUITY fundamentals, else None. CIKs were taken from
               https://www.sec.gov/files/company_tickers.json (fetched 2026-09-24) and hard-coded.
* ``duration`` / ``convexity`` - approximate modified duration (years) and convexity for bond-like assets.
* ``fred``   - FRED yield series behind a synthetic TREASURY / CORP_BOND total-return index.
* ``meta``   - free-form extras (units, synthetic flags, predecessor/successor CIKs).

Asset classes: EQUITY, ETF, INDEX, TREASURY, CORP_BOND, COMMODITY, FUTURE, FX, CRYPTO.
"""
from __future__ import annotations

HORIZONS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252), ("3Y", 756), ("5Y", 1260),
            ("10Y", 2520)]
BENCHMARK = "SPY"
ASSET_CLASSES = ["EQUITY", "ETF", "INDEX", "TREASURY", "CORP_BOND", "COMMODITY", "FUTURE", "FX", "CRYPTO"]


def _asset(id, name, asset_class, sector, country, currency, yahoo, cik=None, duration=None, convexity=None,
           fred=None, benchmark=BENCHMARK, **meta):
    return {"id": id, "name": name, "asset_class": asset_class, "sector": sector, "country": country,
            "currency": currency, "yahoo": yahoo, "cik": cik, "duration": duration, "convexity": convexity,
            "fred": fred, "benchmark": benchmark, "meta": dict(meta)}


def _cx(d):
    """Convexity approximation for a bond ETF: roughly D^2 / 100."""
    return round(d * d / 100.0, 2)


UNIVERSE: list[dict] = []
_add = UNIVERSE.append

# ---------------------------------------------------------------- indices
for _id, _sym, _name, _country, _ccy in [
    ("SPX", "^GSPC", "S&P 500", "United States", "USD"),
    ("NDX", "^NDX", "Nasdaq-100", "United States", "USD"),
    ("RUT", "^RUT", "Russell 2000", "United States", "USD"),
    ("DJI", "^DJI", "Dow Jones Industrial Average", "United States", "USD"),
    ("N225", "^N225", "Nikkei 225", "Japan", "JPY"),
    ("FTSE", "^FTSE", "FTSE 100", "United Kingdom", "GBP"),
    ("DAX", "^GDAXI", "DAX 40", "Germany", "EUR"),
    ("SX5E", "^STOXX50E", "EURO STOXX 50", "Eurozone", "EUR"),
    ("HSI", "^HSI", "Hang Seng", "Hong Kong", "HKD"),
]:
    _add(_asset(_id, _name, "INDEX", "Equity Index", _country, _ccy, _sym, price_return=True))

# ---------------------------------------------------------------- equity ETFs
for _sym, _name, _sector, _country in [
    ("SPY", "SPDR S&P 500 ETF", "Broad Market", "United States"),
    ("QQQ", "Invesco QQQ (Nasdaq-100)", "Broad Market", "United States"),
    ("IWM", "iShares Russell 2000 ETF", "Broad Market", "United States"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF", "Broad Market", "United States"),
    ("VTI", "Vanguard Total Stock Market ETF", "Broad Market", "United States"),
    ("EFA", "iShares MSCI EAFE ETF", "International Equity", "Developed ex-US"),
    ("EEM", "iShares MSCI Emerging Markets ETF", "International Equity", "Emerging Markets"),
    ("EWJ", "iShares MSCI Japan ETF", "International Equity", "Japan"),
    ("FXI", "iShares China Large-Cap ETF", "International Equity", "China"),
    ("EWG", "iShares MSCI Germany ETF", "International Equity", "Germany"),
    ("EWU", "iShares MSCI United Kingdom ETF", "International Equity", "United Kingdom"),
    ("INDA", "iShares MSCI India ETF", "International Equity", "India"),
    ("EWZ", "iShares MSCI Brazil ETF", "International Equity", "Brazil"),
    ("XLK", "Technology Select Sector SPDR", "Information Technology", "United States"),
    ("XLF", "Financial Select Sector SPDR", "Financials", "United States"),
    ("XLE", "Energy Select Sector SPDR", "Energy", "United States"),
    ("XLV", "Health Care Select Sector SPDR", "Health Care", "United States"),
    ("XLY", "Consumer Discretionary Select Sector SPDR", "Consumer Discretionary", "United States"),
    ("XLP", "Consumer Staples Select Sector SPDR", "Consumer Staples", "United States"),
    ("XLI", "Industrial Select Sector SPDR", "Industrials", "United States"),
    ("XLB", "Materials Select Sector SPDR", "Materials", "United States"),
    ("XLU", "Utilities Select Sector SPDR", "Utilities", "United States"),
    ("XLRE", "Real Estate Select Sector SPDR", "Real Estate", "United States"),
    ("XLC", "Communication Services Select Sector SPDR", "Communication Services", "United States"),
    ("SMH", "VanEck Semiconductor ETF", "Information Technology", "United States"),
    ("KRE", "SPDR S&P Regional Banking ETF", "Financials", "United States"),
    ("MTUM", "iShares MSCI USA Momentum Factor ETF", "Factor", "United States"),
    ("USMV", "iShares MSCI USA Min Vol Factor ETF", "Factor", "United States"),
    ("QUAL", "iShares MSCI USA Quality Factor ETF", "Factor", "United States"),
    ("IWD", "iShares Russell 1000 Value ETF", "Factor", "United States"),
    ("IWF", "iShares Russell 1000 Growth ETF", "Factor", "United States"),
]:
    _add(_asset(_sym, _name, "ETF", _sector, _country, "USD", _sym))

# ---------------------------------------------------------------- bond ETFs (duration approximations)
for _sym, _name, _dur, _country in [
    ("SHY", "iShares 1-3 Year Treasury Bond ETF", 1.9, "United States"),
    ("IEI", "iShares 3-7 Year Treasury Bond ETF", 4.3, "United States"),
    ("IEF", "iShares 7-10 Year Treasury Bond ETF", 7.1, "United States"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", 16.5, "United States"),
    ("TIP", "iShares TIPS Bond ETF", 6.7, "United States"),
    ("AGG", "iShares Core US Aggregate Bond ETF", 6.0, "United States"),
    ("BND", "Vanguard Total Bond Market ETF", 6.1, "United States"),
    ("LQD", "iShares iBoxx $ Investment Grade Corporate Bond ETF", 8.3, "United States"),
    ("VCIT", "Vanguard Intermediate-Term Corporate Bond ETF", 6.1, "United States"),
    ("HYG", "iShares iBoxx $ High Yield Corporate Bond ETF", 3.2, "United States"),
    ("JNK", "SPDR Bloomberg High Yield Bond ETF", 3.3, "United States"),
    ("EMB", "iShares J.P. Morgan USD Emerging Markets Bond ETF", 6.9, "Emerging Markets"),
    ("MUB", "iShares National Muni Bond ETF", 6.2, "United States"),
    ("BNDX", "Vanguard Total International Bond ETF (USD hedged)", 6.8, "Developed ex-US"),
]:
    _add(_asset(_sym, _name, "ETF", "Fixed Income", _country, "USD", _sym, duration=_dur, convexity=_cx(_dur)))

# ---------------------------------------------------------------- commodity ETFs
for _sym, _name, _sector in [
    ("GLD", "SPDR Gold Shares", "Precious Metals"),
    ("SLV", "iShares Silver Trust", "Precious Metals"),
    ("USO", "United States Oil Fund", "Energy"),
    ("DBC", "Invesco DB Commodity Index Tracking Fund", "Broad Commodities"),
]:
    _add(_asset(_sym, _name, "ETF", _sector, "United States", "USD", _sym))

# ---------------------------------------------------------------- product-coverage funds (Shaffer Hedge / product registry)
# leveraged and inverse ETFs: meta leverage = daily multiple of the underlying index, reset daily
for _sym, _name, _lev, _und in [
    ("SH", "ProShares Short S&P500", -1, "SPX"), ("SDS", "ProShares UltraShort S&P500", -2, "SPX"),
    ("SSO", "ProShares Ultra S&P500", 2, "SPX"), ("PSQ", "ProShares Short QQQ", -1, "NDX"),
    ("QLD", "ProShares Ultra QQQ", 2, "NDX"), ("TQQQ", "ProShares UltraPro QQQ", 3, "NDX"), ("SQQQ", "ProShares UltraPro Short QQQ", -3, "NDX"),
]:
    _add(_asset(_sym, _name, "ETF", "Leveraged / Inverse", "United States", "USD", _sym, leverage=_lev, underlying_index=_und,
                product_type="inverse_etf" if _lev < 0 else "leveraged_etf"))
for _sym, _name, _sector, _dur, _pt, _extra in [
    ("VNQ", "Vanguard Real Estate ETF", "Real Estate", None, "reit", {}),
    ("PFF", "iShares Preferred and Income Securities ETF", "Fixed Income", 5.0, "preferred", {}),
    ("BKLN", "Invesco Senior Loan ETF", "Fixed Income", 0.3, "leveraged_loan", {"rate_duration": 0.2}),
    ("FLOT", "iShares Floating Rate Bond ETF", "Fixed Income", 0.1, "frn", {"rate_duration": 0.1}),
    ("MBB", "iShares MBS ETF", "Fixed Income", 5.8, "mbs", {"negative_convexity": True}),
    ("CWB", "SPDR Bloomberg Convertible Securities ETF", "Convertibles", None, "convertible", {}),
    ("BIL", "SPDR Bloomberg 1-3 Month T-Bill ETF", "Fixed Income", 0.1, "t_bill", {}),
    ("SGOV", "iShares 0-3 Month Treasury Bond ETF", "Fixed Income", 0.1, "money_market", {}),
    ("SCHP", "Schwab U.S. TIPS ETF", "Fixed Income", 6.5, "tips", {}),
    ("JAAA", "Janus Henderson AAA CLO ETF", "Fixed Income", 0.2, "abs_clo", {"rate_duration": 0.1}),
]:
    _add(_asset(_sym, _name, "ETF", _sector, "United States", "USD", _sym, duration=_dur, convexity=(_cx(_dur) if _dur else None),
                product_type=_pt, **_extra))
for _sym, _name, _sector, _pt, _extra in [
    ("VXX", "iPath Series B S&P 500 VIX Short-Term Futures ETN", "Volatility", "etn", {"issuer": "Barclays Bank PLC"}),
    ("IBIT", "iShares Bitcoin Trust ETF", "Crypto", "crypto_etf", {}),
    ("BITO", "ProShares Bitcoin Strategy ETF (futures)", "Crypto", "crypto_futures_etf", {}),
    ("FXE", "Invesco CurrencyShares Euro Trust", "Currency", "currency_trust", {}),
    ("FXY", "Invesco CurrencyShares Japanese Yen Trust", "Currency", "currency_trust", {}),
    ("FXB", "Invesco CurrencyShares British Pound Trust", "Currency", "currency_trust", {}),
    ("UUP", "Invesco DB US Dollar Index Bullish Fund", "Currency", "currency_fund", {}),
    ("UNG", "United States Natural Gas Fund", "Energy", "commodity_etf", {}),
    ("CPER", "United States Copper Index Fund", "Industrial Metals", "commodity_etf", {}),
    ("DBA", "Invesco DB Agriculture Fund", "Agriculture", "commodity_etf", {}),
]:
    _add(_asset(_sym, _name, "ETF", _sector, "United States", "USD", _sym, product_type=_pt, **_extra))

# ---------------------------------------------------------------- synthetic Treasury / corporate indices (FRED)
for _id, _name, _series, _dur, _cvx in [
    ("UST2Y", "US Treasury 2Y constant-maturity (total-return index)", "DGS2", 1.9, 0.05),
    ("UST5Y", "US Treasury 5Y constant-maturity (total-return index)", "DGS5", 4.5, 0.25),
    ("UST10Y", "US Treasury 10Y constant-maturity (total-return index)", "DGS10", 8.5, 0.85),
    ("UST30Y", "US Treasury 30Y constant-maturity (total-return index)", "DGS30", 17.5, 3.9),
]:
    _add(_asset(_id, _name, "TREASURY", "Government Bonds", "United States", "USD", None, duration=_dur,
                convexity=_cvx, fred=_series, synthetic=True, base=100.0))
_add(_asset("CORP_BAA", "Moody's Baa corporate (total-return index)", "CORP_BOND", "Corporate Bonds",
            "United States", "USD", None, duration=8.0, convexity=0.8, fred="DBAA", synthetic=True, base=100.0))

# ---------------------------------------------------------------- commodity futures (continuous front month)
for _id, _sym, _name, _sector, _unit in [
    ("GOLD", "GC=F", "Gold futures", "Precious Metals", "USD/troy oz"),
    ("SILVER", "SI=F", "Silver futures", "Precious Metals", "USD/troy oz"),
    ("WTI", "CL=F", "WTI crude oil futures", "Energy", "USD/bbl"),
    ("BRENT", "BZ=F", "Brent crude oil futures", "Energy", "USD/bbl"),
    ("NATGAS", "NG=F", "Natural gas futures", "Energy", "USD/MMBtu"),
    ("COPPER", "HG=F", "Copper futures", "Industrial Metals", "USD/lb"),
    ("PLATINUM", "PL=F", "Platinum futures", "Precious Metals", "USD/troy oz"),
    ("CORN", "ZC=F", "Corn futures", "Agriculture", "USc/bu"),
    ("WHEAT", "ZW=F", "Wheat futures", "Agriculture", "USc/bu"),
    ("SOYBEANS", "ZS=F", "Soybean futures", "Agriculture", "USc/bu"),
    ("COFFEE", "KC=F", "Coffee futures", "Agriculture", "USc/lb"),
]:
    _add(_asset(_id, _name, "COMMODITY", _sector, "Global", "USD", _sym, unit=_unit, continuous_front_month=True))

# ---------------------------------------------------------------- FX (currency = quote currency)
for _id, _sym, _name, _quote in [
    ("EURUSD", "EURUSD=X", "Euro / US dollar", "USD"),
    ("GBPUSD", "GBPUSD=X", "British pound / US dollar", "USD"),
    ("USDJPY", "USDJPY=X", "US dollar / Japanese yen", "JPY"),
    ("AUDUSD", "AUDUSD=X", "Australian dollar / US dollar", "USD"),
    ("USDCAD", "USDCAD=X", "US dollar / Canadian dollar", "CAD"),
    ("USDCHF", "USDCHF=X", "US dollar / Swiss franc", "CHF"),
    ("NZDUSD", "NZDUSD=X", "New Zealand dollar / US dollar", "USD"),
    ("USDCNY", "USDCNY=X", "US dollar / Chinese yuan", "CNY"),
    ("USDMXN", "USDMXN=X", "US dollar / Mexican peso", "MXN"),
    ("USDINR", "USDINR=X", "US dollar / Indian rupee", "INR"),
]:
    _add(_asset(_id, _name, "FX", "Currency", "Global", _quote, _sym, base_currency=_id[:3]))
_add(_asset("DXY", "US Dollar Index", "FX", "Currency", "United States", "USD", "DX-Y.NYB", price_return=True))

# ---------------------------------------------------------------- crypto
for _id, _sym, _name in [("BTC", "BTC-USD", "Bitcoin"), ("ETH", "ETH-USD", "Ethereum"), ("SOL", "SOL-USD", "Solana")]:
    _add(_asset(_id, _name, "CRYPTO", "Crypto", "Global", "USD", _sym, trades_weekends=True))

# ---------------------------------------------------------------- equities (SEC CIK for fundamentals)
_IT, _CD, _CS, _FIN = "Information Technology", "Consumer Discretionary", "Consumer Staples", "Financials"
_HC, _COM, _EN, _IND = "Health Care", "Communication Services", "Energy", "Industrials"
for _sym, _name, _sector, _cik, _extra in [
    ("AAPL", "Apple Inc.", _IT, 320193, None),
    ("MSFT", "Microsoft Corp.", _IT, 789019, None),
    ("NVDA", "NVIDIA Corp.", _IT, 1045810, None),
    ("AMZN", "Amazon.com Inc.", _CD, 1018724, None),
    ("GOOGL", "Alphabet Inc. (Class A)", _COM, 1652044, None),
    ("META", "Meta Platforms Inc.", _COM, 1326801, None),
    ("TSLA", "Tesla Inc.", _CD, 1318605, None),
    ("BRK-B", "Berkshire Hathaway Inc. (Class B)", _FIN, 1067983, None),
    ("JPM", "JPMorgan Chase & Co.", _FIN, 19617, None),
    ("V", "Visa Inc.", _FIN, 1403161, None),
    ("MA", "Mastercard Inc.", _FIN, 1141391, None),
    ("UNH", "UnitedHealth Group Inc.", _HC, 731766, None),
    ("LLY", "Eli Lilly and Co.", _HC, 59478, None),
    # Exxon Mobil Corp. (34088) holds the filing history; ExxonMobil Holdings Corp. (2115436) is the 2026
    # successor registrant listed for XOM in company_tickers.json. Both are fetched and merged.
    ("XOM", "Exxon Mobil Corp.", _EN, 34088, [2115436]),
    ("CVX", "Chevron Corp.", _EN, 93410, None),
    ("JNJ", "Johnson & Johnson", _HC, 200406, None),
    ("PG", "Procter & Gamble Co.", _CS, 80424, None),
    ("HD", "Home Depot Inc.", _CD, 354950, None),
    ("COST", "Costco Wholesale Corp.", _CS, 909832, None),
    ("AVGO", "Broadcom Inc.", _IT, 1730168, None),
    ("WMT", "Walmart Inc.", _CS, 104169, None),
    ("BAC", "Bank of America Corp.", _FIN, 70858, None),
    ("KO", "Coca-Cola Co.", _CS, 21344, None),
    ("PEP", "PepsiCo Inc.", _CS, 77476, None),
    ("ORCL", "Oracle Corp.", _IT, 1341439, None),
    ("CRM", "Salesforce Inc.", _IT, 1108524, None),
    ("AMD", "Advanced Micro Devices Inc.", _IT, 2488, None),
    ("NFLX", "Netflix Inc.", _COM, 1065280, None),
    ("INTC", "Intel Corp.", _IT, 50863, None),
    ("CSCO", "Cisco Systems Inc.", _IT, 858877, None),
    # Walt Disney Co. (1744489) since the 2019 Fox deal; pre-2019 filings are under TWDC Enterprises (1001039).
    ("DIS", "Walt Disney Co.", _COM, 1744489, [1001039]),
    ("MCD", "McDonald's Corp.", _CD, 63908, None),
    ("CAT", "Caterpillar Inc.", _IND, 18230, None),
    ("GS", "Goldman Sachs Group Inc.", _FIN, 886982, None),
    ("BA", "Boeing Co.", _IND, 12927, None),
    ("IBM", "International Business Machines Corp.", _IT, 51143, None),
    ("ADBE", "Adobe Inc.", _IT, 796343, None),
    ("QCOM", "Qualcomm Inc.", _IT, 804328, None),
    ("TXN", "Texas Instruments Inc.", _IT, 97476, None),
    ("NKE", "Nike Inc.", _CD, 320187, None),
    ("PFE", "Pfizer Inc.", _HC, 78003, None),
    ("MRK", "Merck & Co. Inc.", _HC, 310158, None),
    ("ABBV", "AbbVie Inc.", _HC, 1551152, None),
    ("T", "AT&T Inc.", _COM, 732717, None),
    ("VZ", "Verizon Communications Inc.", _COM, 732712, None),
]:
    _m = {"extra_ciks": _extra} if _extra else {}
    _add(_asset(_sym, _name, "EQUITY", _sector, "United States", "USD", _sym, cik=_cik, **_m))

# ---------------------------------------------------------------- FRED macro series
# lag_days = calendar days between the observation date and the date the value is assumed known.
FRED_SERIES: dict[str, dict] = {
    "DGS3MO": {"name": "3-Month Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS2": {"name": "2-Year Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS5": {"name": "5-Year Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS10": {"name": "10-Year Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS30": {"name": "30-Year Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "T10Y3M": {"name": "10Y minus 3M Treasury spread (pp)", "lag_days": 1, "freq": "daily"},
    "T10Y2Y": {"name": "10Y minus 2Y Treasury spread (pp)", "lag_days": 1, "freq": "daily"},
    "BAA10Y": {"name": "Moody's Baa corporate minus 10Y Treasury (pp)", "lag_days": 1, "freq": "daily"},
    "DBAA": {"name": "Moody's Baa corporate bond yield (%)", "lag_days": 1, "freq": "daily"},
    "CPIAUCSL": {"name": "CPI, all urban consumers (index, SA)", "lag_days": 45, "freq": "monthly"},
    "UNRATE": {"name": "Unemployment rate (%)", "lag_days": 35, "freq": "monthly"},
    "INDPRO": {"name": "Industrial production (index)", "lag_days": 45, "freq": "monthly"},
    "T5YIE": {"name": "5-Year breakeven inflation (%)", "lag_days": 1, "freq": "daily"},
    "DFII10": {"name": "10-Year TIPS real yield (%)", "lag_days": 1, "freq": "daily"},
    "NFCI": {"name": "Chicago Fed National Financial Conditions Index", "lag_days": 5, "freq": "weekly"},
    "WALCL": {"name": "Fed total assets ($mn)", "lag_days": 4, "freq": "weekly"},
    "M2SL": {"name": "M2 money stock ($bn, SA)", "lag_days": 45, "freq": "monthly"},
    "VIXCLS": {"name": "CBOE VIX (close)", "lag_days": 1, "freq": "daily"},
    "DTWEXBGS": {"name": "Nominal broad US dollar index", "lag_days": 1, "freq": "daily"},
    # curve points, money-market rates and credit spreads (Shaffer Hedge pricing and risk)
    "DGS1MO": {"name": "1-Month Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS6MO": {"name": "6-Month Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS1": {"name": "1-Year Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS7": {"name": "7-Year Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "DGS20": {"name": "20-Year Treasury constant maturity (%)", "lag_days": 1, "freq": "daily"},
    "SOFR": {"name": "Secured Overnight Financing Rate (%)", "lag_days": 1, "freq": "daily"},
    "DFF": {"name": "Effective federal funds rate (%)", "lag_days": 1, "freq": "daily"},
    "BAMLH0A0HYM2": {"name": "ICE BofA US High Yield option-adjusted spread (pp; FRED keeps 3 years)", "lag_days": 1, "freq": "daily"},
    "BAMLC0A0CM": {"name": "ICE BofA US Corporate (IG) option-adjusted spread (pp; FRED keeps 3 years)", "lag_days": 1, "freq": "daily"},
    # Cboe implied-volatility indices (option pricing for hedges; no option chains are available)
    "VXVCLS": {"name": "Cboe S&P 500 3-Month Volatility Index", "lag_days": 1, "freq": "daily"},
    "VXNCLS": {"name": "Cboe Nasdaq-100 Volatility Index", "lag_days": 1, "freq": "daily"},
    "RVXCLS": {"name": "Cboe Russell 2000 Volatility Index", "lag_days": 1, "freq": "daily"},
    "VXDCLS": {"name": "Cboe DJIA Volatility Index", "lag_days": 1, "freq": "daily"},
    "GVZCLS": {"name": "Cboe Gold ETF Volatility Index", "lag_days": 1, "freq": "daily"},
    "OVXCLS": {"name": "Cboe Crude Oil ETF Volatility Index", "lag_days": 1, "freq": "daily"},
    "VXEEMCLS": {"name": "Cboe Emerging Markets ETF Volatility Index", "lag_days": 1, "freq": "daily"},
    "VXEWZCLS": {"name": "Cboe Brazil ETF Volatility Index", "lag_days": 1, "freq": "daily"},
    "VXAPLCLS": {"name": "Cboe Equity VIX on Apple", "lag_days": 1, "freq": "daily"},
    "VXAZNCLS": {"name": "Cboe Equity VIX on Amazon", "lag_days": 1, "freq": "daily"},
    "VXGOGCLS": {"name": "Cboe Equity VIX on Google", "lag_days": 1, "freq": "daily"},
    "VXGSCLS": {"name": "Cboe Equity VIX on Goldman Sachs", "lag_days": 1, "freq": "daily"},
    "VXIBMCLS": {"name": "Cboe Equity VIX on IBM", "lag_days": 1, "freq": "daily"},
    # foreign short rates (FX forwards by covered interest parity)
    "ECBDFR": {"name": "ECB deposit facility rate (%)", "lag_days": 1, "freq": "daily"},
    "IUDSOIA": {"name": "Sterling overnight index average, SONIA (%)", "lag_days": 1, "freq": "daily"},
    "IRSTCI01JPM156N": {"name": "Japan call money rate, monthly (%)", "lag_days": 45, "freq": "monthly"},
    "IRSTCI01CAM156N": {"name": "Canada overnight rate, monthly (%)", "lag_days": 45, "freq": "monthly"},
    "IRSTCI01CHM156N": {"name": "Switzerland call money rate, monthly (%)", "lag_days": 45, "freq": "monthly"},
    "IRSTCI01AUM156N": {"name": "Australia interbank overnight rate, monthly (%)", "lag_days": 45, "freq": "monthly"},
}

_BY_ID = {a["id"]: a for a in UNIVERSE}
assert len(_BY_ID) == len(UNIVERSE), "duplicate asset ids in UNIVERSE"


def by_id(asset_id: str) -> dict | None:
    """The universe entry for ``asset_id`` (a copy), or None."""
    a = _BY_ID.get(asset_id)
    if a is None:
        return None
    out = dict(a)
    out["meta"] = dict(a["meta"])
    return out


def asset_classes() -> list[str]:
    """Asset classes present in the universe, in canonical order."""
    present = {a["asset_class"] for a in UNIVERSE}
    return [c for c in ASSET_CLASSES if c in present]


def horizon_days(label: str) -> int | None:
    """Trading days for a horizon label ("3M" -> 63)."""
    return dict(HORIZONS).get(label)


__all__ = ["UNIVERSE", "HORIZONS", "BENCHMARK", "ASSET_CLASSES", "FRED_SERIES", "by_id", "asset_classes",
           "horizon_days"]
