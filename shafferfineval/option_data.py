"""
ShafferFinEval -- listed option chain adapter (Yahoo Finance).

Retrieval only; no scoring. Yahoo's option endpoint is crumb-gated, unlike the
quote/fundamentals endpoints the rest of the app uses, so this module performs
the cookie+crumb handshake. When that handshake cannot complete, it returns an
UNAVAILABLE chain with the reason attached -- the hedge engine then reports
strikes as theoretical targets and marks premium, delta, IV and liquidity as
missing rather than inventing them.
"""

from __future__ import annotations

import datetime as _dt
import threading
from typing import Optional

import requests

from hedging import OptionChain, OptionQuote
from market_data import BROWSER_UA, HTTP_TIMEOUT, YAHOO_HOSTS

#: Yahoo hands out the cookie from these hosts; the crumb comes from query1/2.
COOKIE_HOSTS = ("https://fc.yahoo.com", "https://finance.yahoo.com")
CRUMB_PATH = "v1/test/getcrumb"

_lock = threading.Lock()
_session: Optional[requests.Session] = None
_crumb: Optional[str] = None
_crumb_error: Optional[str] = None


def _authenticated_session() -> tuple[Optional[requests.Session], Optional[str], Optional[str]]:
    """Session + crumb for the option endpoint, or (None, None, reason)."""
    global _session, _crumb, _crumb_error
    with _lock:
        if _session is not None and _crumb:
            return _session, _crumb, None
        if _crumb_error is not None:
            return None, None, _crumb_error

        session = requests.Session()
        session.headers.update({
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })

        cookie_error = None
        for host in COOKIE_HOSTS:
            try:
                session.get(host, timeout=HTTP_TIMEOUT, allow_redirects=True)
            except requests.RequestException as exc:
                cookie_error = str(exc)
                continue
            if session.cookies:
                break

        if not session.cookies:
            _crumb_error = (
                "Yahoo would not issue a session cookie"
                + (f" ({cookie_error})" if cookie_error else "")
                + ". Option chains are crumb-gated, so no listed strikes, "
                "premiums or Greeks are available."
            )
            return None, None, _crumb_error

        for host in YAHOO_HOSTS:
            try:
                response = session.get(
                    f"https://{host}/{CRUMB_PATH}", timeout=HTTP_TIMEOUT
                )
            except requests.RequestException as exc:
                cookie_error = str(exc)
                continue
            text = (response.text or "").strip()
            if response.status_code == 200 and text and "{" not in text:
                _session, _crumb = session, text
                return _session, _crumb, None

        _crumb_error = (
            "Yahoo rejected the crumb request"
            + (f" ({cookie_error})" if cookie_error else "")
            + ". Option chains are unavailable."
        )
        return None, None, _crumb_error


def reset_session() -> None:
    """Forget a cached session/crumb so the handshake is retried."""
    global _session, _crumb, _crumb_error
    with _lock:
        _session, _crumb, _crumb_error = None, None, None


def _to_date(epoch) -> Optional[_dt.date]:
    try:
        return _dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _num(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def parse_option_payload(ticker: str, payload: dict) -> OptionChain:
    """Turn Yahoo's optionChain JSON into an OptionChain. Pure, so it is testable."""
    chain = OptionChain(ticker=ticker)
    try:
        result = payload["optionChain"]["result"][0]
    except (KeyError, IndexError, TypeError):
        chain.note = "Yahoo returned no option chain for this symbol."
        return chain

    chain.expiries = [d for d in (_to_date(e) for e in result.get("expirationDates") or []) if d]

    for block in result.get("options") or []:
        expiry = _to_date(block.get("expirationDate"))
        if expiry is None:
            continue
        quotes: list[OptionQuote] = []
        for right, key in ((("P"), "puts"), (("C"), "calls")):
            for raw in block.get(key) or []:
                strike = _num(raw.get("strike"))
                if strike is None:
                    continue
                quotes.append(OptionQuote(
                    strike=strike, right=right, expiry=expiry,
                    bid=_num(raw.get("bid")), ask=_num(raw.get("ask")),
                    last=_num(raw.get("lastPrice")),
                    volume=_num(raw.get("volume")),
                    open_interest=_num(raw.get("openInterest")),
                    implied_volatility=_num(raw.get("impliedVolatility")),
                    # Yahoo does not publish Greeks on this endpoint.
                    delta=None,
                ))
        if quotes:
            chain.quotes[expiry] = quotes
            if expiry not in chain.expiries:
                chain.expiries.append(expiry)

    chain.expiries.sort()
    chain.available = bool(chain.quotes)
    if not chain.available:
        chain.note = "Yahoo returned expiries but no contracts."
    return chain


def fetch_option_chain(
    ticker: str, expiry: Optional[_dt.date] = None
) -> OptionChain:
    """Listed options for one underlying.

    Yahoo returns contracts for ONE expiry per call plus the full list of
    expiration dates, so callers wanting a specific expiry pass it explicitly.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        return OptionChain(ticker=symbol, note="No ticker supplied.")

    session, crumb, reason = _authenticated_session()
    if session is None:
        return OptionChain(ticker=symbol, note=reason)

    params = {"crumb": crumb}
    if expiry is not None:
        params["date"] = int(
            _dt.datetime(expiry.year, expiry.month, expiry.day,
                         tzinfo=_dt.timezone.utc).timestamp()
        )

    last_error = None
    for host in YAHOO_HOSTS:
        try:
            response = session.get(
                f"https://{host}/v7/finance/options/{symbol}",
                params=params, timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        if response.status_code == 404:
            return OptionChain(ticker=symbol, note=f"No listed options for {symbol}.")
        if response.status_code == 401:
            reset_session()
            return OptionChain(
                ticker=symbol,
                note="Yahoo rejected the option-chain crumb; chain unavailable.",
            )
        if response.status_code == 200:
            try:
                return parse_option_payload(symbol, response.json())
            except ValueError as exc:
                last_error = str(exc)
                continue
        last_error = f"HTTP {response.status_code}"

    return OptionChain(
        ticker=symbol,
        note=f"Option chain request failed ({last_error}).",
    )


def fetch_chain_for_target_expiry(ticker: str, target_dte: int = 90) -> OptionChain:
    """Fetch the expiry list, then the contracts for the expiry nearest target.

    Two calls, because Yahoo returns contracts one expiry at a time.
    """
    from hedging import select_target_expiry

    first = fetch_option_chain(ticker)
    if not first.available or not first.expiries:
        return first

    expiry, _dte, _reason = select_target_expiry(first.expiries, target_dte=target_dte)
    if expiry is None or expiry in first.quotes:
        return first

    detailed = fetch_option_chain(ticker, expiry)
    if detailed.available:
        detailed.expiries = first.expiries or detailed.expiries
        return detailed
    return first
