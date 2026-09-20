"""
ShafferFinEval -- data layer.

Everything that touches the outside world lives here. The scoring engine
(`scoring.py`) never imports this module, so the two can be moved apart.

Data source: Yahoo Finance, via its public query endpoints (no API key, no
crumb/cookie handshake, no scraping of the HTML site):

  * v8/finance/chart              -> current price, currency
  * v1/finance/search             -> name, quoteType, sector, industry
  * ws/fundamentals-timeseries    -> revenue, total debt, forward P/E,
                                     market cap, trailing P/E, EPS

`fetch_raw_info` assembles those three responses into one flat dict, so the
rest of this module sees a single payload per symbol.

Improving peer selection later means editing SECTOR_PEER_UNIVERSE and/or
`get_sector_peers` -- nothing above this line needs to change.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import requests

from statlib import is_finite

# --------------------------------------------------------------------------
# Instrument routing
#
# V1 scores EQUITIES only. The other branches are recognised and reported
# honestly rather than scored with an equity model that does not apply to them.
# --------------------------------------------------------------------------

EQUITY = "EQUITY"

#: Yahoo `quoteType` -> our internal instrument class.
INSTRUMENT_TYPES = {
    "EQUITY": EQUITY,
    "ETF": "ETF",
    "MUTUALFUND": "FUND",
    "INDEX": "INDEX",
    "CURRENCY": "CURRENCY",
    "CRYPTOCURRENCY": "CRYPTO",
    "FUTURE": "COMMODITY",
    "BOND": "BOND",
}

SUPPORTED_INSTRUMENTS = {EQUITY}

# --------------------------------------------------------------------------
# Peer universe
#
# Yahoo does not expose "every company in a sector" on the free tier, so V1
# uses a curated representative sample of large, liquid names per Yahoo sector.
# Keys MUST match Yahoo's `sector` strings exactly.
#
# The peer group is narrowed to the company's own INDUSTRY when enough of these
# names share it (see get_sector_peers / build_sector_benchmark), which is how
# NVDA ends up benchmarked against semiconductors rather than all of Technology.
# --------------------------------------------------------------------------

SECTOR_PEER_UNIVERSE: dict[str, list[str]] = {
    "Technology": [
        "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "INTC", "QCOM", "TXN", "MU",
        "ADI", "AMAT", "LRCX", "KLAC", "ORCL", "CRM", "ADBE", "CSCO", "ACN",
        "IBM", "NOW",
    ],
    "Financial Services": [
        "JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "SCHW",
        "BK", "STT", "COF", "AXP", "BLK", "SPGI", "CME", "ICE", "V", "MA",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC", "VLO", "OXY", "HES",
        "DVN", "FANG", "HAL", "BKR", "WMB", "KMI", "OKE", "TRGP", "APA", "CTRA",
    ],
    "Healthcare": [
        "JNJ", "LLY", "PFE", "MRK", "ABBV", "BMY", "AMGN", "GILD", "UNH",
        "CVS", "CI", "ELV", "HCA", "TMO", "DHR", "ABT", "MDT", "SYK", "BSX",
        "ZTS",
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "LOW", "MCD", "SBUX", "NKE", "TJX", "BKNG",
        "GM", "F", "CMG", "ORLY", "AZO", "ROST", "YUM", "MAR", "HLT", "DHI",
        "LEN",
    ],
    "Consumer Defensive": [
        "PG", "KO", "PEP", "COST", "WMT", "TGT", "MDLZ", "MO", "PM", "CL",
        "KMB", "GIS", "KHC", "SYY", "STZ", "HSY", "K", "KR", "DG", "ADM",
    ],
    "Industrials": [
        "CAT", "DE", "HON", "GE", "BA", "LMT", "RTX", "NOC", "GD", "UNP",
        "CSX", "NSC", "UPS", "FDX", "MMM", "EMR", "ETN", "ITW", "PH", "CMI",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR",
        "EA", "TTWO", "OMC", "IPG", "WBD", "PARA", "LYV", "FOXA", "NWSA",
        "MTCH", "PINS",
    ],
    "Basic Materials": [
        "LIN", "APD", "SHW", "ECL", "DD", "DOW", "LYB", "PPG", "NUE", "STLD",
        "FCX", "NEM", "ALB", "IFF", "CE", "EMN", "MOS", "CF", "VMC", "MLM",
    ],
    "Real Estate": [
        "PLD", "AMT", "EQIX", "CCI", "PSA", "SPG", "O", "WELL", "DLR", "VTR",
        "AVB", "EQR", "ESS", "MAA", "INVH", "ARE", "BXP", "KIM", "REG", "HST",
    ],
    "Utilities": [
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "WEC",
        "ES", "PEG", "EIX", "DTE", "PPL", "AEE", "CMS", "CNP", "ATO", "NI",
    ],
}

#: How many peers to fetch per analysis.
PEER_FETCH_LIMIT = 20

#: Narrow to the company's own industry only if at least this many peers match.
MIN_INDUSTRY_PEERS = 5

#: Parallel peer fetches. Keep modest -- this is an unofficial API.
PEER_FETCH_WORKERS = 8


class TickerNotFound(Exception):
    """Raised when a symbol cannot be resolved at all."""


@dataclass
class SecurityData:
    """Everything the model and the DATA USED panel need for one symbol."""

    ticker: str
    name: Optional[str] = None
    instrument_type: Optional[str] = None
    quote_type: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: Optional[str] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None
    revenue: Optional[float] = None
    total_debt: Optional[float] = None
    forward_pe: Optional[float] = None
    forward_eps: Optional[float] = None
    trailing_pe: Optional[float] = None
    debt_revenue: Optional[float] = None
    revenue_as_of: Optional[str] = None
    debt_as_of: Optional[str] = None
    forward_pe_as_of: Optional[str] = None
    retrieved_at: Optional[_dt.datetime] = None
    missing_fields: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_equity(self) -> bool:
        return self.instrument_type == EQUITY


# --------------------------------------------------------------------------
# Raw fetch -- the single seam where the network is touched
# --------------------------------------------------------------------------

def _num(value) -> Optional[float]:
    """Coerce to float, or None. Never invents a value."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return out


# Yahoo serves these from either host; we fail over between them.
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")

#: A browser-style User-Agent is acceptable for Yahoo Finance (and only Yahoo).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

HTTP_TIMEOUT = 20      # seconds per request
HTTP_RETRIES = 2       # per host
MIN_REQUEST_INTERVAL = 0.06   # seconds; politeness throttle across all threads

#: fundamentals-timeseries fields we ask for, most-current variants first.
#: "trailing" is a rolling as-of-today figure; "quarterly" is the latest
#: reported quarter; "annual" is the last fiscal year.
TIMESERIES_TYPES = (
    "trailingForwardPeRatio",
    "trailingPeRatio",
    "trailingMarketCap",
    "trailingTotalRevenue",
    "quarterlyTotalRevenue",
    "quarterlyTotalDebt",
    "annualTotalDebt",
    "trailingDilutedEPS",
    # --- sector engine: ROE / ROA / explicit market cap ---
    "trailingNetIncome",
    "annualNetIncome",
    "quarterlyStockholdersEquity",
    "annualStockholdersEquity",
    "quarterlyTotalAssets",
    "annualTotalAssets",
    "quarterlyOrdinarySharesNumber",
    "annualOrdinarySharesNumber",
    # --- company model: EV/EBITDA, growth series, cash ---
    "trailingEBITDA",
    "annualEBITDA",
    "annualTotalRevenue",
    "quarterlyCashCashEquivalentsAndShortTermInvestments",
    "quarterlyCashAndCashEquivalents",
    "annualCashCashEquivalentsAndShortTermInvestments",
    "annualCashAndCashEquivalents",
)

#: Yahoo does NOT publish returnOnEquity / returnOnAssets on these endpoints
#: (they live behind the crumb-gated `financialData` module), so ROE and ROA
#: are computed from the statement fields above. The "supplied" path is kept in
#: sector_scoring so a future source can short-circuit the calculation.

_session_lock = threading.Lock()
_throttle_lock = threading.Lock()
_last_request_at = [0.0]
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """One shared, thread-safe session so connections are reused."""
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers.update(
                {
                    "User-Agent": BROWSER_UA,
                    "Accept": "application/json",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
        return _session


def _throttle() -> None:
    """Space requests out a little. Yahoo's endpoints are free but unofficial."""
    with _throttle_lock:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at[0])
        if wait > 0:
            time.sleep(wait)
        _last_request_at[0] = time.monotonic()


def _get_json(path: str, params: dict) -> Optional[dict]:
    """GET a Yahoo query endpoint, failing over between hosts.

    Returns the decoded JSON, or None if the resource does not exist (404) or
    every attempt failed. Never raises for an ordinary miss.
    """
    last_error: Optional[Exception] = None
    session = _get_session()

    for host in YAHOO_HOSTS:
        url = f"https://{host}/{path}"
        for attempt in range(HTTP_RETRIES):
            _throttle()
            try:
                response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                last_error = exc
                continue
            if response.status_code == 404:
                return None           # symbol genuinely does not exist
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    last_error = exc
                    continue
            if response.status_code in (429, 999):
                time.sleep(0.5 * (attempt + 1))   # backed off, then retried
                last_error = RuntimeError(f"HTTP {response.status_code} from {host}")
                continue
            last_error = RuntimeError(f"HTTP {response.status_code} from {host}")

    if last_error is not None:
        raise RuntimeError(f"Yahoo request failed ({path}): {last_error}")
    return None


def _fetch_chart(symbol: str) -> dict:
    """Current price and currency. Also the cleanest existence check: 404."""
    payload = _get_json(
        f"v8/finance/chart/{symbol}", {"range": "1d", "interval": "1d"}
    )
    if not payload:
        return {}
    try:
        return payload["chart"]["result"][0]["meta"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def _fetch_search(symbol: str) -> dict:
    """Name, quoteType, sector and industry.

    Yahoo's search is fuzzy, so only an EXACT symbol match is accepted --
    otherwise "ASDFXYZ" would silently resolve to whatever Yahoo suggests.
    """
    payload = _get_json(
        "v1/finance/search",
        {"q": symbol, "quotesCount": 8, "newsCount": 0, "listsCount": 0},
    )
    if not payload:
        return {}
    for quote in payload.get("quotes") or []:
        if str(quote.get("symbol", "")).upper() == symbol.upper():
            return quote
    return {}


def _fetch_timeseries(symbol: str) -> dict:
    """Latest value and as-of date for each fundamentals field.

    Returns {type: {"value": float, "as_of": "YYYY-MM-DD", "series": [floats]}},
    where `series` is every reported point oldest-first (needed for growth and
    acceleration, which compare consecutive fiscal periods).
    """
    now = int(time.time())
    payload = _get_json(
        f"ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
        {
            "symbol": symbol,
            "type": ",".join(TIMESERIES_TYPES),
            "period1": now - 5 * 365 * 24 * 3600,
            "period2": now + 24 * 3600,
        },
    )
    out: dict[str, dict] = {}
    if not payload:
        return out

    for result in (payload.get("timeseries") or {}).get("result") or []:
        try:
            field_name = result["meta"]["type"][0]
        except (KeyError, IndexError, TypeError):
            continue
        points = [p for p in (result.get(field_name) or []) if p]
        if not points:
            continue
        latest = points[-1]           # Yahoo returns these oldest-first
        value = _num((latest.get("reportedValue") or {}).get("raw"))
        if value is None:
            continue
        series = [
            v for v in (
                _num((point.get("reportedValue") or {}).get("raw")) for point in points
            ) if v is not None
        ]
        out[field_name] = {
            "value": value,
            "as_of": latest.get("asOfDate"),
            "series": series,
        }
    return out


def _series(series: dict, *names: str) -> list[float]:
    """First available multi-period series from `names`, oldest-first."""
    for name in names:
        entry = series.get(name)
        if entry and entry.get("series"):
            return list(entry["series"])
    return []


def _pick(series: dict, *names: str) -> tuple[Optional[float], Optional[str]]:
    """First available field from `names`, in preference order."""
    for name in names:
        entry = series.get(name)
        if entry is not None:
            return entry["value"], entry["as_of"]
    return None, None


def fetch_raw_info(ticker: str, with_price: bool = True) -> dict:
    """Assemble one flat payload for a symbol from Yahoo's query endpoints.

    This is the only function in the project that talks to the network. Swap it
    (or monkeypatch it in tests) to change data providers.

    `with_price=False` skips the chart call; peers do not need a quote, which
    saves one HTTP request per peer.
    """
    symbol = ticker.upper()
    meta = _fetch_chart(symbol) if with_price else {}
    quote = _fetch_search(symbol)
    series = _fetch_timeseries(symbol)

    if not meta and not quote and not series:
        return {}                     # caller raises TickerNotFound

    revenue, revenue_as_of = _pick(
        series, "trailingTotalRevenue", "quarterlyTotalRevenue"
    )
    # Most recent balance sheet first: the last quarter, then the fiscal year.
    debt, debt_as_of = _pick(series, "quarterlyTotalDebt", "annualTotalDebt")
    forward_pe, forward_pe_as_of = _pick(series, "trailingForwardPeRatio")
    trailing_pe, _ = _pick(series, "trailingPeRatio")
    market_cap, _ = _pick(series, "trailingMarketCap")

    price = _num(meta.get("regularMarketPrice"))

    # Yahoo publishes no forward-EPS field on these endpoints. Derive it only
    # when both inputs are real, and label it as derived wherever it is shown.
    forward_eps = None
    if price is not None and forward_pe not in (None, 0):
        forward_eps = price / forward_pe

    return {
        "symbol": symbol,
        "longName": quote.get("longname") or meta.get("longName"),
        "shortName": quote.get("shortname") or meta.get("shortName"),
        "quoteType": quote.get("quoteType") or meta.get("instrumentType"),
        "sector": quote.get("sector"),
        "industry": quote.get("industry"),
        "currency": meta.get("currency"),
        "currentPrice": price,
        "regularMarketPrice": price,
        "marketCap": market_cap,
        "totalRevenue": revenue,
        "totalDebt": debt,
        "forwardPE": forward_pe,
        "forwardEps": forward_eps,
        "trailingPE": trailing_pe,
        "netIncome": _pick(series, "trailingNetIncome", "annualNetIncome")[0],
        "shareholdersEquity": _pick(
            series, "quarterlyStockholdersEquity", "annualStockholdersEquity"
        )[0],
        "totalAssets": _pick(series, "quarterlyTotalAssets", "annualTotalAssets")[0],
        "sharesOutstanding": _pick(
            series, "quarterlyOrdinarySharesNumber", "annualOrdinarySharesNumber"
        )[0],
        "ebitda": _pick(series, "trailingEBITDA", "annualEBITDA")[0],
        "cash": _pick(
            series,
            "quarterlyCashCashEquivalentsAndShortTermInvestments",
            "quarterlyCashAndCashEquivalents",
            "annualCashCashEquivalentsAndShortTermInvestments",
            "annualCashAndCashEquivalents",
        )[0],
        "revenueAnnual": _series(series, "annualTotalRevenue"),
        "ebitdaAnnual": _series(series, "annualEBITDA"),
        "revenueAsOf": revenue_as_of,
        "debtAsOf": debt_as_of,
        "forwardPeAsOf": forward_pe_as_of,
    }


def get_security_data(ticker: str, with_price: bool = True) -> SecurityData:
    """Fetch and normalise one symbol.

    Raises TickerNotFound if the symbol does not resolve. Missing individual
    metrics are recorded in `missing_fields` and left as None -- never filled in.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise TickerNotFound("No ticker entered.")

    try:
        info = fetch_raw_info(symbol, with_price=with_price)
    except Exception as exc:  # network failure, malformed response
        raise TickerNotFound(f"Could not retrieve data for {symbol} ({exc}).") from exc

    # Yahoo yields nothing identifying for a symbol that does not exist.
    if not info or not (
        info.get("quoteType")
        or info.get("symbol")
        or info.get("regularMarketPrice") is not None
    ):
        raise TickerNotFound(f"Ticker not found: {symbol}")

    quote_type = info.get("quoteType")
    data = SecurityData(
        ticker=symbol,
        name=info.get("longName") or info.get("shortName") or symbol,
        quote_type=quote_type,
        instrument_type=INSTRUMENT_TYPES.get(quote_type, quote_type),
        sector=info.get("sector"),
        industry=info.get("industry"),
        currency=info.get("currency"),
        price=_num(
            info.get("currentPrice")
            if info.get("currentPrice") is not None
            else info.get("regularMarketPrice")
        ),
        market_cap=_num(info.get("marketCap")),
        revenue=_num(info.get("totalRevenue")),
        total_debt=_num(info.get("totalDebt")),
        forward_pe=_num(info.get("forwardPE")),
        forward_eps=_num(info.get("forwardEps")),
        trailing_pe=_num(info.get("trailingPE")),
        revenue_as_of=info.get("revenueAsOf"),
        debt_as_of=info.get("debtAsOf"),
        forward_pe_as_of=info.get("forwardPeAsOf"),
        retrieved_at=_dt.datetime.now(_dt.timezone.utc),
    )

    if is_finite(data.total_debt) and is_finite(data.revenue) and float(data.revenue) > 0:
        data.debt_revenue = float(data.total_debt) / float(data.revenue)
    else:
        data.notes.append(
            "Debt/Revenue unavailable (revenue or total debt missing or zero)."
        )

    for label, value in (
        ("Price", data.price if with_price else 0),
        ("Market cap", data.market_cap),
        ("Revenue", data.revenue),
        ("Total debt", data.total_debt),
        ("Sector", data.sector),
        ("Industry", data.industry),
    ):
        if value is None:
            data.missing_fields.append(label)

    if not is_finite(data.forward_pe) or float(data.forward_pe) <= 0:
        data.missing_fields.append("Forward P/E")

    return data


# ==========================================================================
# SECTOR ENGINE ADAPTER
#
# Retrieval only. Every calculation lives in sector_scoring.py, which receives
# plain CompanyObservation records and knows nothing about Yahoo or HTTP.
# ==========================================================================

#: Total US market benchmark for the growth-acceleration factor.
VTI_TICKER = "VTI"

#: One year of daily bars comfortably covers the 127 needed for two 63-day windows.
HISTORY_RANGE = "1y"

#: Universe fetches are the heaviest thing the app does; bound them.
MAX_UNIVERSE_COMPANIES = 700
UNIVERSE_FETCH_WORKERS = 10


def fetch_price_history(symbol: str) -> list[float]:
    """Adjusted daily closes, oldest first. Empty list when unavailable.

    Adjusted close is used consistently so splits and dividends cannot
    masquerade as price performance.
    """
    payload = _get_json(
        f"v8/finance/chart/{symbol}",
        {"range": HISTORY_RANGE, "interval": "1d", "events": "div,split"},
    )
    if not payload:
        return []
    try:
        result = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return []

    indicators = result.get("indicators") or {}
    series = None
    adjclose = indicators.get("adjclose")
    if adjclose:
        series = (adjclose[0] or {}).get("adjclose")
    if not series:
        quote = indicators.get("quote")
        series = (quote[0] or {}).get("close") if quote else None
    if not series:
        return []

    return [float(v) for v in series if _num(v) is not None and float(v) > 0]


def get_vti_history() -> list[float]:
    """Adjusted close history for VTI, the growth-acceleration benchmark."""
    return fetch_price_history(VTI_TICKER)


def fetch_company_record(ticker: str, sector_hint: Optional[str] = None):
    """Fetch one company once and build BOTH engines' input records.

    Returns (CompanyObservation, CompanyFinancials) or None if the symbol is
    not an operating-company equity. Both engines read the same three HTTP
    responses, so adding the company model cost no extra requests.

    Yahoo's `quoteType` is the authoritative equity test -- a workbook's own
    type column is only ever a cost-saving pre-filter.
    """
    from company_scoring import CompanyFinancials
    from sector_scoring import CompanyObservation

    symbol = (ticker or "").strip().upper()
    if not symbol:
        return None

    try:
        info = fetch_raw_info(symbol, with_price=True)
    except Exception:
        return None
    if not info:
        return None

    if INSTRUMENT_TYPES.get(info.get("quoteType"), info.get("quoteType")) != EQUITY:
        return None

    sector = info.get("sector") or sector_hint
    if not sector:
        return None

    name = info.get("longName") or info.get("shortName") or symbol
    industry = info.get("industry")
    price = _num(info.get("currentPrice"))
    market_cap = _num(info.get("marketCap"))
    shares = _num(info.get("sharesOutstanding"))
    total_debt = _num(info.get("totalDebt"))
    net_income = _num(info.get("netIncome"))
    total_assets = _num(info.get("totalAssets"))

    prices = fetch_price_history(symbol)

    observation = CompanyObservation(
        ticker=symbol,
        name=name,
        sector=sector,
        prices=prices or None,
        market_cap=market_cap,
        total_debt=total_debt,
        net_income=net_income,
        shareholders_equity=_num(info.get("shareholdersEquity")),
        total_assets=total_assets,
        price=price,
        shares_outstanding=shares,
    )

    financials = CompanyFinancials(
        ticker=symbol,
        name=name,
        sector=sector,
        industry=industry,
        price=price,
        shares_outstanding=shares,
        market_cap=market_cap,
        total_debt=total_debt,
        cash=_num(info.get("cash")),
        ebitda=_num(info.get("ebitda")),
        revenue=_num(info.get("totalRevenue")),
        net_income=net_income,
        total_assets=total_assets,
        revenue_annual=list(info.get("revenueAnnual") or []),
        ebitda_annual=list(info.get("ebitdaAnnual") or []),
    )
    return observation, financials


def fetch_company_observation(ticker: str, sector_hint: Optional[str] = None):
    """Sector-engine record only. Kept for callers that need just that."""
    record = fetch_company_record(ticker, sector_hint)
    return record[0] if record else None


def fallback_universe_rows() -> list[tuple[str, Optional[str]]]:
    """The built-in list used ONLY when no universe workbook is present.

    Deliberately the same curated names as the equity engine's peer universe.
    It is a stand-in, not the source of truth, and the UI says so.
    """
    rows: list[tuple[str, Optional[str]]] = []
    for sector, tickers in SECTOR_PEER_UNIVERSE.items():
        for ticker in tickers:
            rows.append((ticker, sector))
    return rows


def build_universe_records(
    universe_rows: list[tuple[str, Optional[str]]],
    limit: int = MAX_UNIVERSE_COMPANIES,
):
    """Fetch the whole tradeable universe once, for both scoring engines.

    Returns (observations_by_sector, financials, stats). Symbols that fail, are
    not equities, or carry no sector are counted in `stats` and dropped --
    never invented.
    """
    trimmed = universe_rows[:limit]
    stats = {
        "requested": len(universe_rows),
        "attempted": len(trimmed),
        "resolved": 0,
        "dropped_not_equity_or_no_data": 0,
        "truncated": max(0, len(universe_rows) - len(trimmed)),
    }

    def one(row: tuple[str, Optional[str]]):
        ticker, hint = row
        try:
            return fetch_company_record(ticker, hint)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=UNIVERSE_FETCH_WORKERS) as pool:
        records = list(pool.map(one, trimmed))

    by_sector: dict[str, list] = {}
    financials: list = []
    for record in records:
        if record is None:
            stats["dropped_not_equity_or_no_data"] += 1
            continue
        observation, company = record
        stats["resolved"] += 1
        by_sector.setdefault(observation.sector, []).append(observation)
        financials.append(company)

    stats["sectors"] = len(by_sector)
    stats["industries"] = len({c.industry for c in financials if c.industry})
    return by_sector, financials, stats


def build_sector_observations(
    universe_rows: list[tuple[str, Optional[str]]],
    limit: int = MAX_UNIVERSE_COMPANIES,
):
    """Sector-engine view of the universe sweep."""
    by_sector, _financials, stats = build_universe_records(universe_rows, limit)
    return by_sector, stats
