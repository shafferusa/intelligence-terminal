"""Fixture server for the FinSim Paper Terminal UI (TERMINAL_WORKPLAN.md WP-12; TERMINAL_DESIGN.md s15, s17).

Serves the terminal's static UI (``finsim/finsim/terminal/static``) and canned JSON for EVERY s15 route from
``tests/fixtures/terminal/api/*.json`` so the pages can be checked in a browser without the real server, the network
or a database.  Standard library only; never the network; bind 127.0.0.1 and port 0 in tests.

    py -3.12 tests/terminal_fixture_server.py --port 8777      # then open http://127.0.0.1:8777/
    from tests.terminal_fixture_server import FixtureServer; s = FixtureServer().start(); ... s.stop()

Conventions
- ROUTES maps (method, path regex) -> fixture name (+ an example path for the test).  First match wins, so specific
  paths precede ``[^/]+`` ones.  WP-13 owns ``preview_*``, ``hedges_*``, ``orders_submit_*``, ``strategies_*`` (listed in
  WP13_FIXTURES); a missing file answers 404 ``FIXTURE_MISSING`` with the owner named, never an invented body.
- Variants: a request picks ``<name>_<variant>.json`` when it exists, trying (in order) the account role + tab, the
  account role, then each of the query parameters ``fixture``, ``tab``, ``scope``, ``status``, ``mode``, ``period``
  (lower-cased, non-alphanumerics -> ``_``); then ``<name>_<scenario>.json`` for the active scenario; then
  ``<name>.json``.  So ``/api/accounts/P2:CREDIT/markets`` serves ``accounts_markets_credit.json`` and
  ``/api/portfolio?scope=ALL_BOOKS`` serves ``portfolio_all_books.json``.
- Scenarios: ``GET /__fixture/scenario?name=provider_down`` (empty name clears) switches every route that has a
  ``<name>_<scenario>.json`` (health_provider_down, quotes_provider_down, health_offline, health_integrity_hold ...).
  This is how the browser check forces a provider failure and stale quotes.
- Placeholders substituted at serve time (so ages are real whenever the page is opened): ``"$NOW"`` and
  ``"$NOW-245"`` / ``"$NOW+3600"`` -> integer epoch seconds; ``"$TODAY"`` / ``"$TODAY-1"`` / ``"$TODAY+2"`` -> the New
  York date (ISO); ``"$NY"`` -> the New York time string; ``"$NYTS-7000"`` -> "YYYY-MM-DD HH:MM" NY, offset seconds.
- A fixture may wrap its body: ``{"$status": 409, "$headers": {"X-Replayed": "1"}, "$body": {...}}``.
- ``format=csv`` or a ``.csv`` path serves ``<name>.csv`` as text/csv.
- Every answer carries ``Cache-Control: no-store``; ``POST /api/shutdown`` answers its fixture and does NOT stop the
  server (``stop()`` does).  Mutating routes without ``request_id`` (body or X-Request-Id) answer 400
  ``BAD_REQUEST_ID`` exactly like the real API, so the UI's request-id discipline is exercised.
- Missing OPTIONAL scripts (other packages' components / pages, listed in OPTIONAL_STATIC) answer an empty JS stub
  with 200 so the browser console stays clean; any other missing static file is a 404.  OPTIONAL_STATIC lists the same
  optional files as ``finsim.terminal.api.PLANNED_STATIC`` (FINSIM1_PLAN C1) plus the older optional components.
- There is no news route (the news feature was removed in 1.0.5): ``/api/news`` answers 404 like any unknown route.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                                   # finsim/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from finsim.tzfallback import get_zone  # noqa: E402

STATIC_DIR = os.path.join(ROOT, "finsim", "terminal", "static")
FIXTURE_DIR = os.path.join(HERE, "fixtures", "terminal", "api")
NY = get_zone("America/New_York")

WP13_FIXTURES = {"preview_aapl", "hedges_aapl", "orders_submit_aapl", "strategies_list", "strategies_iron_condor"}
OPTIONAL_STATIC = {"components/ticket.js", "components/preview.js", "components/hedge.js", "components/performance.js",
                   "components/lifetimes.js", "components/pbschedule.js", "components/swapticket.js",
                   "components/privateticket.js", "components/privatecredit.js", "pages/private_markets.js",
                   # the product plug-ins of Build 2 (BUILD2_PLAN C3 / C11): stubs until their packages build them
                   "pages/markets_debt.js", "components/bondticket.js",          # P07 Debt Instruments
                   "pages/markets_fx.js", "components/fxdesk.js",                # P06 Foreign Markets & FX
                   "pages/markets_credit.js", "components/creditdesk.js",        # P08 Credit Instruments
                   "pages/markets_otc.js", "components/otcticket.js",            # P12 PB OTC options / forwards
                   "components/cdsticket.js",                                    # CDX (CDS index) ticket
                   # FINSIM1_PLAN C1: the pages of the new navigation and the import panel (NOT YET BUILT until their packages land)
                   "pages/pending.js", "pages/completed.js", "pages/accounts.js", "pages/cash_collateral.js", "pages/derivatives.js",
                   "components/imports.js"}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json",
        ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8"}

A = "P2:US_EXCHANGE"
# (method, regex, fixture name, example path, example body or None)
ROUTES = [
    ("GET", r"/api/health$", "health", "/api/health", None),
    ("POST", r"/api/shutdown$", "shutdown", "/api/shutdown", {}),
    ("GET", r"/api/state$", "state", "/api/state", None),
    ("GET", r"/api/sessions$", "sessions", "/api/sessions", None),
    ("GET", r"/api/calendar$", "calendar", "/api/calendar?year=2026", None),
    ("GET", r"/api/accounts$", "accounts", "/api/accounts?book=P", None),
    ("POST", r"/api/accounts/pb$", "accounts_pb", "/api/accounts/pb", {"broker": "GS"}),
    ("PATCH", r"/api/accounts/[^/]+$", "accounts_patch", "/api/accounts/" + A, {"sweep_out": True}),
    ("GET", r"/api/accounts/[^/]+$", "accounts_detail", "/api/accounts/" + A, None),
    ("GET", r"/api/accounts/[^/]+/positions$", "accounts_positions", "/api/accounts/" + A + "/positions", None),
    ("GET", r"/api/accounts/[^/]+/positions/[^/]+$", "accounts_position_detail", "/api/accounts/" + A + "/positions/AAPL", None),
    ("GET", r"/api/accounts/[^/]+/orders$", "accounts_orders", "/api/accounts/" + A + "/orders?status=open", None),
    ("GET", r"/api/accounts/[^/]+/fills$", "accounts_fills", "/api/accounts/" + A + "/fills", None),
    ("GET", r"/api/accounts/[^/]+/transactions$", "accounts_transactions", "/api/accounts/" + A + "/transactions", None),
    ("GET", r"/api/accounts/[^/]+/cash$", "accounts_cash", "/api/accounts/" + A + "/cash", None),
    ("GET", r"/api/accounts/[^/]+/markets$", "accounts_markets", "/api/accounts/" + A + "/markets", None),
    ("GET", r"/api/accounts/[^/]+/schedule$", "accounts_schedule", "/api/accounts/P2:PB_GS/schedule", None),
    ("PATCH", r"/api/accounts/[^/]+/schedule$", "accounts_schedule", "/api/accounts/P2:PB_GS/schedule", {"equity_per_share": "0.0030"}),
    ("GET", r"/api/portfolio$", "portfolio", "/api/portfolio?scope=ALL", None),
    ("GET", r"/api/portfolio/performance$", "portfolio_performance", "/api/portfolio/performance?scope=ALL", None),
    ("GET", r"/api/treasury$", "treasury", "/api/treasury", None),
    ("POST", r"/api/treasury/deposit$", "treasury_deposit", "/api/treasury/deposit", {"currency": "USD", "amount": "100000.00"}),
    ("POST", r"/api/treasury/withdraw$", "treasury_withdraw", "/api/treasury/withdraw", {"currency": "USD", "amount": "50000.00"}),
    ("POST", r"/api/transfers$", "transfers", "/api/transfers", {"from_account": "P2:TREASURY", "to_account": "P2:CREDIT", "currency": "USD", "amount": "25000.00"}),
    ("GET", r"/api/instruments$", "instruments_search", "/api/instruments?q=AAP", None),
    ("GET", r"/api/instruments/resolve$", "instruments_resolve", "/api/instruments/resolve?symbol=AAPL", None),
    ("GET", r"/api/instruments/[^/]+$", "instrument", "/api/instruments/AAPL", None),
    ("GET", r"/api/instruments/[^/]+/bars$", "instrument_bars", "/api/instruments/AAPL/bars?range=1d", None),
    ("GET", r"/api/instruments/[^/]+/chain$", "instrument_chain", "/api/instruments/AAPL/chain?expiry=2027-01-15", None),
    ("GET", r"/api/quotes$", "quotes", "/api/quotes?ids=AAPL,SPY", None),
    ("GET", r"/api/curve$", "curve", "/api/curve", None),
    ("GET", r"/api/rates$", "rates", "/api/rates", None),
    ("POST", r"/api/orders/preview$", "preview_aapl", "/api/orders/preview", {"account_id": A, "instrument_id": "AAPL", "side": "BUY", "qty": 1, "order_type": "MARKET", "tif": "DAY"}),
    ("POST", r"/api/orders$", "orders_submit_aapl", "/api/orders", {"account_id": A, "instrument_id": "AAPL", "side": "BUY", "qty": 1, "order_type": "MARKET", "tif": "DAY"}),
    ("GET", r"/api/orders/[^/]+$", "order", "/api/orders/o:1", None),
    ("DELETE", r"/api/orders/[^/]+$", "order_cancel", "/api/orders/o:5", {}),
    ("POST", r"/api/orders/[^/]+/resume$", "order_resume", "/api/orders/o:5/resume", {}),
    ("GET", r"/api/credit/locate$", "credit_locate", "/api/credit/locate?account_id=" + A + "&instrument_id=SPY&qty=100", None),
    ("POST", r"/api/credit/borrows$", "credit_borrows", "/api/credit/borrows", {"account_id": A, "instrument_id": "SPY", "qty": 100}),
    ("POST", r"/api/credit/borrows/[^/]+/return$", "credit_borrow_return", "/api/credit/borrows/b:1/return", {"qty": 100}),
    ("POST", r"/api/credit/loans$", "credit_loans", "/api/credit/loans", {"account_id": "P2:CREDIT", "currency": "EUR", "amount": "100000.00"}),
    ("POST", r"/api/credit/loans/[^/]+/repay$", "credit_loan_repay", "/api/credit/loans/ln:1/repay", {"amount": "100000.00"}),
    ("POST", r"/api/credit/pb/[^/]+/(draw|repay)$", "credit_pb", "/api/credit/pb/P2:PB_GS/draw", {"amount": "50000.00"}),
    ("POST", r"/api/fx/preview$", "fx_preview", "/api/fx/preview", {"account_id": "P2:FOREIGN_FX", "buy_ccy": "EUR", "sell_ccy": "USD", "amount": "10000.00", "amount_ccy": "EUR"}),
    ("POST", r"/api/fx/spot$", "fx_spot", "/api/fx/spot", {"account_id": "P2:FOREIGN_FX", "buy_ccy": "EUR", "sell_ccy": "USD", "amount": "10000.00", "amount_ccy": "EUR"}),
    ("POST", r"/api/fx/forward$", "fx_forward", "/api/fx/forward", {"account_id": "P2:FOREIGN_FX"}),
    ("POST", r"/api/options/exercise$", "options_exercise", "/api/options/exercise", {"account_id": A, "instrument_id": "AAPL 270115P00315000", "qty": 5}),
    ("GET", r"/api/hedges$", "hedges_aapl", "/api/hedges?account_id=" + A + "&instrument_id=AAPL&side=BUY&qty=500", None),
    ("GET", r"/api/accounting/balance-sheet$", "accounting_balance_sheet", "/api/accounting/balance-sheet?scope=ALL", None),
    ("GET", r"/api/accounting/pnl$", "accounting_pnl", "/api/accounting/pnl?scope=ALL&period=TODAY", None),
    ("GET", r"/api/accounting/trial-balance$", "accounting_trial_balance", "/api/accounting/trial-balance?scope=ALL", None),
    ("GET", r"/api/accounting/journal$", "accounting_journal", "/api/accounting/journal?scope=ALL", None),
    ("GET", r"/api/accounting/journal/[^/]+$", "accounting_journal_entry", "/api/accounting/journal/5", None),
    ("GET", r"/api/accounting/integrity$", "accounting_integrity", "/api/accounting/integrity?mode=ledger", None),
    ("GET", r"/api/risk$", "risk", "/api/risk?scope=ALL", None),
    ("GET", r"/api/compliance$", "compliance", "/api/compliance?scope=ALL", None),
    ("GET", r"/api/activity$", "activity", "/api/activity?limit=50", None),
    ("GET", r"/api/attention$", "attention", "/api/attention", None),
    ("GET", r"/api/attention/summary$", "attention_summary", "/api/attention/summary?book=P", None),
    ("POST", r"/api/attention/[^/]+/resolve$", "attention_resolve", "/api/attention/2/resolve", {}),
    ("GET", r"/api/jobs$", "jobs", "/api/jobs", None),
    ("POST", r"/api/jobs/run$", "jobs_run", "/api/jobs/run", {"job": "SETTLE_DUE"}),
    ("GET", r"/api/settings$", "settings", "/api/settings", None),
    ("PATCH", r"/api/settings$", "settings", "/api/settings", {"participation_rate": "0.10"}),
    ("GET", r"/api/books$", "books", "/api/books", None),
    ("GET", r"/api/books/[^/]+/lifetimes$", "books_lifetimes", "/api/books/P/lifetimes", None),
    ("POST", r"/api/books/[^/]+/reset$", "books_reset", "/api/books/P/reset", {"confirm": "RESET", "starting_capital": "1000000.00"}),
    ("POST", r"/api/books/[^/]+/onboard$", "books_onboard", "/api/books/P/onboard", {"broker": "JPM"}),
    ("GET", r"/api/history/[^/]+/portfolio$", "history_portfolio", "/api/history/P1/portfolio", None),
    ("GET", r"/api/history/[^/]+/positions$", "history_positions", "/api/history/P1/positions", None),
    ("GET", r"/api/history/[^/]+/accounting/balance-sheet$", "history_accounting_balance_sheet", "/api/history/P1/accounting/balance-sheet", None),
    ("GET", r"/api/history/[^/]+/accounting/pnl$", "history_accounting_pnl", "/api/history/P1/accounting/pnl?period=ITD", None),
    ("GET", r"/api/history/[^/]+/accounting/trial-balance$", "history_accounting_trial_balance", "/api/history/P1/accounting/trial-balance", None),
    ("GET", r"/api/history/[^/]+/accounting/journal$", "history_accounting_journal", "/api/history/P1/accounting/journal", None),
    ("GET", r"/api/history/[^/]+/accounting/journal/[^/]+$", "history_accounting_journal_entry", "/api/history/P1/accounting/journal/1", None),
    ("GET", r"/api/history/[^/]+/accounting/integrity$", "history_accounting_integrity", "/api/history/P1/accounting/integrity", None),
    ("GET", r"/api/history/[^/]+/activity$", "history_activity", "/api/history/P1/activity", None),
    ("GET", r"/api/history/[^/]+/export/transactions\.csv$", "history_export_transactions", "/api/history/P1/export/transactions.csv", None),
    ("POST", r"/api/otc/preview$", "otc_preview", "/api/otc/preview", {"account_id": "P2:PB_GS", "kind": "IRS", "terms": {}}),
    ("POST", r"/api/otc/contracts$", "otc_contracts_post", "/api/otc/contracts", {"account_id": "P2:PB_GS", "kind": "IRS", "terms": {}}),
    ("GET", r"/api/otc/contracts$", "otc_contracts", "/api/otc/contracts?account_id=P2:PB_GS", None),
    ("POST", r"/api/otc/contracts/[^/]+/terminate$", "otc_terminate", "/api/otc/contracts/sw:1/terminate", {}),
    ("GET", r"/api/private/companies$", "private_companies", "/api/private/companies", None),
    ("POST", r"/api/private/companies$", "private_companies_post", "/api/private/companies", {"name": "NewCo", "sector": "AI", "country": "US", "status": "private"}),
    ("POST", r"/api/private/companies/[^/]+/rounds$", "private_rounds_post", "/api/private/companies/PRIV:ANTHROPIC/rounds", {"round_date": "2026-10-20", "round_type": "secondary", "source_url": "https://example.com"}),
    ("POST", r"/api/private/preview$", "private_preview", "/api/private/preview", {"account_id": "I1:PRIVATE", "company_id": "PRIV:ANTHROPIC", "side": "BUY", "qty": 20000, "price": "61.50", "kind": "SECONDARY"}),
    ("POST", r"/api/private/trades$", "private_trades", "/api/private/trades", {"account_id": "I1:PRIVATE", "company_id": "PRIV:ANTHROPIC", "side": "BUY", "qty": 20000, "price": "61.50", "kind": "SECONDARY"}),
    ("POST", r"/api/credit/private$", "credit_private_post", "/api/credit/private", {"account_id": "P2:CREDIT", "borrower": "NewCo", "currency": "USD", "principal": "5000000.00", "issue_price": "98.00", "index": "SOFR", "spread": "0.0650", "term_months": 36}),
    ("POST", r"/api/credit/private/[^/]+/(fee|repay)$", "credit_private_action", "/api/credit/private/pl:1/fee", {"amount": "25000.00"}),
    ("GET", r"/api/credit/private$", "credit_private", "/api/credit/private?scope=ALL", None),
    ("GET", r"/api/strategies$", "strategies_list", "/api/strategies?account_id=" + A, None),
    ("POST", r"/api/strategies/preview$", "strategies_iron_condor", "/api/strategies/preview", {"account_id": A, "key": "iron_condor", "params": {"underlying": "AAPL", "units": 1, "tenor_months": 3}}),
    ("GET", r"/api/dividends$", "dividends", "/api/dividends?account_id=" + A, None),
    ("GET", r"/api/unsupported$", "unsupported", "/api/unsupported", None),
    ("GET", r"/api/watchlists$", "watchlists", "/api/watchlists", None),
    ("POST", r"/api/watchlists$", "watchlists", "/api/watchlists", {"account_id": A, "name": "core", "instrument_id": "NVDA"}),
    ("DELETE", r"/api/watchlists$", "watchlists", "/api/watchlists", {"account_id": A, "name": "core", "instrument_id": "NVDA"}),
    ("GET", r"/api/export/transactions\.csv$", "export_transactions", "/api/export/transactions.csv?scope=ALL", None),
    ("GET", r"/api/export/positions\.csv$", "export_positions", "/api/export/positions.csv?scope=ALL", None),
]
_COMPILED = [(m, re.compile(rx), name) for (m, rx, name, _ex, _body) in ROUTES]
_PLACEHOLDER = re.compile(r"^\$(NOW|TODAY|NYTS)([+-]\d+)?$")
_EMBEDDED = re.compile(r"\$(NOW|TODAY|NYTS)([+-]\d+)?")


def _ny_now(now: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).astimezone(NY)


def _value(kind: str, off: int, now: float):
    if kind == "NOW":
        return int(now) + off
    if kind == "TODAY":
        return (_ny_now(now).date() + _dt.timedelta(days=off)).isoformat()
    return _ny_now(now + off).strftime("%Y-%m-%d %H:%M")


def substitute(obj, now: float):
    """Replace the time placeholders in a decoded fixture (recursively); everything else passes through.
    A string that IS a placeholder becomes the typed value (an int for $NOW); a placeholder embedded in a longer
    string ("settlement on $TODAY+1 needs ...") is replaced inline as text."""
    if isinstance(obj, str):
        if obj == "$NY":
            return _ny_now(now).strftime("%Y-%m-%d %H:%M:%S %Z")
        m = _PLACEHOLDER.match(obj)
        if m:
            return _value(m.group(1), int(m.group(2) or 0), now)
        if "$" in obj:
            return _EMBEDDED.sub(lambda mm: str(_value(mm.group(1), int(mm.group(2) or 0), now)), obj)
        return obj
    if isinstance(obj, list):
        return [substitute(v, now) for v in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, now) for k, v in obj.items()}
    return obj


def _variant_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def match_route(method: str, path: str):
    """(fixture name) for a method + path, or None."""
    for m, rx, name in _COMPILED:
        if m == method and rx.match(path):
            return name
    return None


def resolve_fixture(name: str, variants, scenario: str | None, ext: str = ".json"):
    """The fixture file for `name`: the first existing variant, then the scenario, then the plain file; None if absent."""
    for v in variants:
        if v:
            p = os.path.join(FIXTURE_DIR, "%s_%s%s" % (name, v, ext))
            if os.path.exists(p):
                return p
    if scenario:
        p = os.path.join(FIXTURE_DIR, "%s_%s%s" % (name, _variant_key(scenario), ext))
        if os.path.exists(p):
            return p
    p = os.path.join(FIXTURE_DIR, name + ext)
    return p if os.path.exists(p) else None


def variants_for(path: str, query: dict):
    """The variant keys tried for a request: role+tab, role (for account routes), then the query parameters."""
    out = []
    m = re.match(r"/api/accounts/([^/]+)/", path)
    tab = (query.get("tab") or [""])[0]
    if m:
        acct = unquote(m.group(1))
        role = acct.split(":", 1)[1] if ":" in acct else acct
        if tab:
            out.append(_variant_key(role + "_" + tab))
        out.append(_variant_key(role))
    for k in ("fixture", "tab", "scope", "status", "mode", "period"):
        v = (query.get(k) or [""])[0]
        if v:
            out.append(_variant_key(v))
    return out


class FixtureState:
    def __init__(self):
        self.scenario: str | None = None
        self.lock = threading.Lock()
        self.requests: list = []                              # (method, path) log, bounded


class _Handler(BaseHTTPRequestHandler):
    server_version = "FinSimFixture/1.0"
    state: FixtureState = None  # set per server

    def log_message(self, fmt, *args):                         # quiet unless FIXTURE_LOG is set
        if os.environ.get("FIXTURE_LOG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers -------------------------------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj, headers=None):
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json", headers)

    def _error(self, status: int, code: str, message: str, details=None):
        self._json(status, {"error": {"code": code, "message": message, "details": details}, "request_id": None})

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {"$raw": raw.decode("utf-8", "replace")}

    # ---- dispatch ------------------------------------------------------------------------------------------------
    def _handle(self, method: str):
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        st = self.state
        with st.lock:
            st.requests.append((method, self.path))
            del st.requests[:-500]
        if path.startswith("/__fixture/"):
            return self._control(path, query)
        if path.startswith("/api/"):
            return self._api(method, path, query)
        if method not in ("GET", "HEAD"):
            return self._error(405, "METHOD", "static files are read-only")
        return self._static(path)

    def _control(self, path, query):
        st = self.state
        if path == "/__fixture/scenario":
            name = (query.get("name") or [""])[0].strip()
            with st.lock:
                st.scenario = name or None
            return self._json(200, {"scenario": st.scenario})
        if path == "/__fixture/routes":
            return self._json(200, {"routes": [[m, rx, name] for (m, rx, name, _e, _b) in ROUTES]})
        if path == "/__fixture/requests":
            with st.lock:
                return self._json(200, {"requests": list(st.requests)})
        return self._error(404, "NOT_FOUND", "unknown control route")

    def _api(self, method, path, query):
        name = match_route(method, path)
        if not name:
            return self._error(404, "NOT_FOUND", "no fixture route for %s %s" % (method, path))
        body = self._read_body() if method in ("POST", "PATCH", "DELETE") else None
        if method in ("POST", "PATCH", "DELETE") and path != "/api/shutdown" and path not in ("/api/orders/preview", "/api/fx/preview", "/api/otc/preview", "/api/private/preview", "/api/strategies/preview"):
            rid = (body or {}).get("request_id") or self.headers.get("X-Request-Id")
            if not rid:
                return self._error(400, "BAD_REQUEST_ID", "request_id is required on every mutating route")
        with self.state.lock:
            scenario = self.state.scenario
        wants_csv = path.endswith(".csv") or (query.get("format") or [""])[0] == "csv"
        if wants_csv:
            p = resolve_fixture(name, variants_for(path, query), scenario, ".csv")
            if not p:
                return self._error(404, "FIXTURE_MISSING", "no CSV fixture %s.csv" % name)
            with open(p, "rb") as f:
                return self._send(200, f.read(), "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=%s.csv" % name})
        p = resolve_fixture(name, variants_for(path, query), scenario)
        if not p:
            owner = "WP-13" if name in WP13_FIXTURES else "WP-12"
            return self._error(404, "FIXTURE_MISSING", "fixture %s.json is not written yet (owner %s)" % (name, owner), {"fixture": name, "owner": owner})
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = substitute(data, time.time())
        status, headers = 200, {}
        if isinstance(data, dict) and "$body" in data:
            status = int(data.get("$status") or 200)
            headers = dict(data.get("$headers") or {})
            data = data["$body"]
        if isinstance(data, dict) and body and body.get("request_id") and "request_id" not in data:
            data = dict(data, request_id=body["request_id"])
        return self._json(status, data, headers)

    def _static(self, path):
        if path in ("/", "/index.html"):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = unquote(path[len("/static/"):])
        else:
            return self._error(404, "NOT_FOUND", "unknown path %s" % path)
        rel = rel.replace("\\", "/")
        if ".." in rel.split("/"):
            return self._error(403, "FORBIDDEN", "no parent paths")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.normpath(STATIC_DIR)):
            return self._error(403, "FORBIDDEN", "outside the static folder")
        ext = os.path.splitext(full)[1].lower()
        if not os.path.isfile(full):
            if rel in OPTIONAL_STATIC:
                return self._send(200, ("/* %s: optional component not built yet (fixture server stub) */\n" % rel).encode("ascii"), MIME[".js"], {"X-Fixture-Stub": "1"})
            return self._error(404, "NOT_FOUND", "no static file %s" % rel)
        with open(full, "rb") as f:
            return self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")


class FixtureServer:
    """A fixture server on 127.0.0.1:<port> (0 = ephemeral) in a daemon thread; `url` after start()."""

    def __init__(self, port: int = 0, host: str = "127.0.0.1"):
        self.host, self.port = host, port
        self.state = FixtureState()
        self.httpd = None
        self.thread = None
        self.url = None

    def start(self):
        handler = type("FixtureHandler", (_Handler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = "http://%s:%d" % (self.host, self.port)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="fixture-server")
        self.thread.start()
        return self

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None

    def set_scenario(self, name: str | None):
        with self.state.lock:
            self.state.scenario = name or None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FinSim terminal UI fixture server (no network, no database)")
    ap.add_argument("--port", type=int, default=8777, help="never 8765 (the old server) or 8865")
    ap.add_argument("--scenario", default=None)
    args = ap.parse_args(argv)
    if args.port in (8765, 8865):
        print("refusing port %d (reserved)" % args.port)
        return 2
    s = FixtureServer(port=args.port).start()
    s.set_scenario(args.scenario)
    print("fixture server at %s/  (Ctrl-C stops; scenario: %s)" % (s.url, s.state.scenario))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    s.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
