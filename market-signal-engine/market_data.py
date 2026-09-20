"""
MARKET SIGNAL ENGINE -- data layer.

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

from scoring import (
    MIN_PEERS_FOR_CONFIDENCE,
    SectorBenchmark,
    calculate_debt_revenue,
    is_valid_forward_pe,
    median_forward_pe,
    median_of,
)

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
)

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

    Returns {type: {"value": float, "as_of": "YYYY-MM-DD"}}.
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
        out[field_name] = {"value": value, "as_of": latest.get("asOfDate")}
    return out


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

    ratio, ratio_notes = calculate_debt_revenue(data.total_debt, data.revenue)
    data.debt_revenue = ratio
    data.notes.extend(ratio_notes)

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

    if not is_valid_forward_pe(data.forward_pe):
        data.missing_fields.append("Forward P/E")

    return data


# --------------------------------------------------------------------------
# Peer selection
# --------------------------------------------------------------------------

def get_sector_peers(ticker: str, sector: Optional[str]) -> list[str]:
    """The candidate peer list for a symbol: same Yahoo sector, minus itself.

    MVP approach: a curated, representative peer universe per sector (see
    SECTOR_PEER_UNIVERSE). Swap in a screener here when one is available.
    """
    if not sector:
        return []
    symbol = (ticker or "").strip().upper()
    peers = [t for t in SECTOR_PEER_UNIVERSE.get(sector, []) if t != symbol]
    return peers[:PEER_FETCH_LIMIT]


def fetch_peer_rows(tickers: list[str]) -> list[dict]:
    """Fetch the two model inputs for each peer. Failures are skipped, not faked."""
    if not tickers:
        return []

    def one(symbol: str) -> Optional[dict]:
        try:
            data = get_security_data(symbol, with_price=False)
        except Exception:
            return None
        return {
            "ticker": data.ticker,
            "name": data.name,
            "industry": data.industry,
            "debt_revenue": data.debt_revenue,
            "forward_pe": data.forward_pe if is_valid_forward_pe(data.forward_pe) else None,
        }

    with ThreadPoolExecutor(max_workers=PEER_FETCH_WORKERS) as pool:
        rows = list(pool.map(one, tickers))

    return [row for row in rows if row is not None]


def build_sector_benchmark(company: SecurityData, peer_fetcher=None) -> SectorBenchmark:
    """Median Debt/Revenue and forward P/E for the company's peer group.

    Prefers the company's own industry when at least MIN_INDUSTRY_PEERS of the
    sector universe share it, otherwise falls back to the whole sector.

    `peer_fetcher` accepts a list of tickers and returns peer rows. It exists so
    the UI can wrap the network call in its own cache; it defaults to
    `fetch_peer_rows`.
    """
    fetch = peer_fetcher or fetch_peer_rows
    notes: list[str] = []
    candidates = get_sector_peers(company.ticker, company.sector)

    if not candidates:
        notes.append(
            f"No peer universe configured for sector '{company.sector}'. "
            "Add one to SECTOR_PEER_UNIVERSE in market_data.py."
            if company.sector
            else "Sector unavailable for this symbol -- no peer group could be built."
        )
        return SectorBenchmark(
            group_label=company.sector or "Unknown",
            grouping="sector",
            debt_revenue_median=None,
            forward_pe_median=None,
            debt_revenue_n=0,
            forward_pe_n=0,
            peers=[],
            notes=notes,
        )

    rows = fetch(candidates)
    failed = len(candidates) - len(rows)
    if failed:
        notes.append(f"{failed} of {len(candidates)} peers returned no data and were skipped.")

    grouping = "sector"
    group_label = company.sector or "Unknown"
    if company.industry:
        same_industry = [r for r in rows if r["industry"] == company.industry]
        if len(same_industry) >= MIN_INDUSTRY_PEERS:
            rows = same_industry
            grouping = "industry"
            group_label = company.industry
            notes.append(
                f"Peer group narrowed to the '{company.industry}' industry "
                f"({len(rows)} peers) within the {company.sector} sector."
            )
        else:
            notes.append(
                f"Only {len(same_industry)} '{company.industry}' peers available "
                f"(need {MIN_INDUSTRY_PEERS}) -- benchmarking against the whole "
                f"{company.sector} sector instead."
            )

    debt_median, debt_n = median_of(r["debt_revenue"] for r in rows)
    pe_median, pe_n = median_forward_pe(r["forward_pe"] for r in rows)

    if debt_n < MIN_PEERS_FOR_CONFIDENCE:
        notes.append(
            f"LOW CONFIDENCE: only {debt_n} peers supplied a usable Debt/Revenue."
        )
    if pe_n < MIN_PEERS_FOR_CONFIDENCE:
        notes.append(
            f"LOW CONFIDENCE: only {pe_n} peers supplied a usable forward P/E."
        )

    rows.sort(key=lambda r: r["ticker"])

    return SectorBenchmark(
        group_label=group_label,
        grouping=grouping,
        debt_revenue_median=debt_median,
        forward_pe_median=pe_median,
        debt_revenue_n=debt_n,
        forward_pe_n=pe_n,
        peers=rows,
        notes=notes,
    )
