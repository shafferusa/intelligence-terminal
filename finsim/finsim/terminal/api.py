"""HTTP API: ``Router`` (routes -> services / bus), the JSON codec and the error mapping (TERMINAL_DESIGN.md §15; WP-11).

Conventions (every one is enforced here, not left to goodwill)
- ``Router.handle(method, path, query, body, client_ip, headers=None) -> (status, body, headers)`` is testable without
  sockets: ``body`` is already the JSON-able structure (the codec has run), a ``str`` for CSV and ``bytes`` for static
  files; ``headers`` carries ``Content-Type``, ``Cache-Control: no-store`` and, when they apply, ``X-Replayed: 1`` and
  ``Retry-After: 1``.
- Codec: ``Decimal`` -> string; dates -> ISO; a key ending in ``_c`` whose value is an integer number of cents is
  emitted under the same name WITHOUT ``_c`` as a 2-dp decimal string (``nav_c`` -> ``nav``; ``nav0_c`` -> ``nav_0``;
  a dict of cents such as ``qp_threshold_c`` is converted value by value); when the stripped name already exists in
  the same dict (``sweep_in`` next to ``sweep_in_c``) the ``_c`` key is kept as it is, so nothing is ever overwritten;
  a key ending in ``_ts`` holding an epoch gains a ``*_ny`` display string ("YYYY-MM-DD HH:MM:SS ET"); ``quote_time``
  / ``fetched_at`` stay integers and quote records carry ``age_s``.
- Every mutating route (POST / PATCH / DELETE) needs ``request_id`` in the body or the ``X-Request-Id`` header;
  missing -> 400 ``BAD_REQUEST_ID`` and nothing reaches the bus.  Mutating handlers build one ``Command`` and map
  ``bus.execute`` to HTTP: a replayed request returns the stored status and body plus ``X-Replayed: 1``; 503
  ``WRITER_BUSY`` / ``RETRY`` carry ``Retry-After: 1``.  A 4xx from ``order.submit`` is recorded through
  ``orders.record_rejection`` (handoff §7: rejected orders are preserved).
- C5 (FINSIM1_PLAN): ``add_refusal_recorder(kinds, fn)`` - ``run_command`` hands every 4xx result of those command kinds
  whose code is not in ``NOT_REJECTIONS`` (and that is not a replay) to ``fn(req, kind, payload, result)``; a recorder never
  changes the HTTP answer and never raises (its exception is logged).
- The "Requires your attention" summary (``attention.attention_summary``) is served by ``GET /api/attention/summary?book=``;
  the global strip keeps ``GET /api/attention``.
- Read handlers call ``Accounting`` / ``QuoteCache`` / ``MarkProvider`` / ``InstrumentMaster`` / ``SessionTable`` on
  the request thread with a pooled reader — never the writer lock, never the network.  The only fetch-triggering reads
  (``/chain``, ``/hedges``, ``/resolve``, ``/bars`` for an unsubscribed symbol, ``/strategies/preview``) enqueue work
  for the quote worker and answer 202 ``{status: "fetching"|"resolving", retry_ms: 1500}`` until the cache has it.
- The ``book`` query parameter resolves scope ``ALL`` to that book's OPEN lifetime (``{lifetime}:ALL``) per request,
  so two requests for different books never share state; without it the Accounting's ``selected_book`` or the first
  open lifetime applies; ``ALL_BOOKS`` consolidates the open lifetimes.
- A route whose product module is not merged answers 501 ``NOT_BUILT`` with the package id (``details.package``),
  never a fake number.  Unknown scope / account / order -> 404 ``NOT_FOUND``; validation -> 400; ``RejectError`` /
  ``ConflictError`` keep their own http (400 / 409); a reader-pool timeout -> 503 ``BUSY``; anything else -> 500
  ``INTERNAL`` with an ``incident_id`` (an ERROR attention row written through the light command ``attention.open``)
  and the traceback in the log — failures surface, they are never swallowed.
- ``/api/shutdown`` and ``/api/jobs/run`` answer 403 ``FORBIDDEN`` off loopback.  Light commands registered here
  (``register(bus)``): ``attention.resolve``, ``attention.open``, ``watchlist.add`` / ``watchlist.remove``,
  ``settings.set`` (§4.1's "api.py" row).
- Body: ``{"error": {"code", "message", "details"}, "request_id"}`` on every error; cents fields and epochs follow
  the codec above on every success.  Static files (``/`` and ``/static/*``) are served from the package's ``static``
  folder with ``no-store`` and a path-traversal guard.
"""
from __future__ import annotations

import dataclasses
import json
import mimetypes
import os
import re
import sqlite3
import time as _time
import traceback
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote

from . import APP_ID, SCHEMA_VERSION, VERSION, config
from . import books as _books
from .accounting import ScopeError
from .clock import accounting_day, iso as _iso, ny_str
from .commands import Command, ConflictError, RejectError, Result
from .db import BusyError, DatabaseError, dumps, get_setting, set_setting
from .attention import attention_book, attention_in_books, attention_summary
from .tables import BOOK_PROFILES, CONVENTIONS
from .units import fmt_money, from_c, to_c

LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")
RETRY_MS = 1500
MAX_QUOTE_IDS = 200
OPEN_ORDER_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
NO_STORE = "no-store"
JSON_CT = "application/json; charset=utf-8"
CSV_CT = "text/csv; charset=utf-8"

# module attribute on the Runtime -> the work package that builds it (501 NOT_BUILT names it)
PACKAGES = {"worker": "WP-01", "cache": "WP-01", "chains": "WP-01", "curve": "WP-01", "rates": "WP-01", "orders": "WP-02", "matcher": "WP-02",
            "options": "WP-03", "hedges": "WP-03", "futures": "WP-04", "fx": "WP-05", "credit": "WP-05", "lending": "WP-06", "repo": "WP-06",
            "prime": "WP-07", "lifecycle": "WP-08", "risk": "WP-09", "compliance": "WP-09", "bonds": "WP-10", "swaps": "WP-17", "dividends": "WP-18",
            "private": "WP-19", "performance": "WP-20", "private_credit": "WP-21", "lifetimes": "WP-23", "strategies": "WP-24",
            "glossary": "WP-25", "swaps_trs": "WP-26", "otc_options": "B2-P12", "counterparties": "B2-P08", "treasuries": "B2-P07",
            # CDS-107 (CDS_SINGLE_NAME_PLAN, release 1.0.7): the single-name CDS modules
            "credit_entities": "CDS-B2", "cds_model": "CDS-B3", "swaps_cds_names": "CDS-B1",
            # FINSIM1_PLAN C2: the optional modules of the FinSim1 completion build (a module not in the tree stays "missing")
            "dashboard": "F1-P1", "blotters": "F1-P2", "navhistory": "F1-P3", "collateral": "F1-P6", "importer": "F1-P5"}

# C3 (BUILD2_PLAN): the optional UI files planned by Build 2 packages.  While one is missing on disk, /static/<path> answers 200 with a
# one-line JavaScript comment (so index.html can list it and the console shows no 404); the real file replaces the stub as soon as it lands.
PLANNED_STATIC = ("pages/markets_debt.js", "components/bondticket.js", "pages/markets_fx.js", "components/fxdesk.js", "pages/markets_credit.js",
                  "components/creditdesk.js", "pages/markets_otc.js", "components/otcticket.js",
                  "components/cdsticket.js",
                  "components/cdsnames.js",                    # CDS-107: the single-name CDS desk (stubbed with a 200 until it lands)
                  # FINSIM1_PLAN C1: the pages of the new navigation (Pending / Completed Trades, Accounts, Cash & Collateral,
                  # Derivatives) and the import panel; each route shows NOT YET BUILT until its file lands
                  "pages/pending.js", "pages/completed.js", "pages/accounts.js", "pages/cash_collateral.js", "pages/derivatives.js",
                  "components/imports.js")

# §20 — what the terminal does NOT do, or does only in part, served by GET /api/unsupported (Settings > Unsupported, search).
# SHELL-07 / APIUI-15 (2026-10-21): trader wording only (no endpoints, package ids, doc or glossary references).  The static rows are
# the decided ones (not offered, not simulated) and the standing disclosures (delayed data, approximations); what a product module
# still lacks comes from the module itself (``unsupported()`` rows) or from ``PLANNED`` below while that module / capability is
# not in the running build - so nothing that is built is ever listed as missing.
UNSUPPORTED = [
    {"item": "Private-equity deal flow (buyouts), investment banking, client / investor management", "state": "NOT_OFFERED",
     "wording": "not a trading product; nothing in the terminal offers it", "where": "nowhere"},
    {"item": "Mortgage-backed and structured products", "state": "NOT_OFFERED", "wording": "no free, reliable price source; never priced with invented data",
     "where": "not listed"},
    {"item": "Exotic OTC derivatives: variance / volatility / dividend / inflation swaps, barrier options", "state": "NOT_OFFERED",
     "wording": "no free source for the volatility and inflation inputs they need; never priced with invented data", "where": "prime-broker OTC tab"},
    {"item": "Physical commodities (owning or delivering the commodity)", "state": "NOT_OFFERED",
     "wording": "commodity exposure is through listed futures only", "where": "Futures tab"},
    {"item": "Crypto", "state": "NOT_OFFERED", "wording": "not tradeable; search says so", "where": "search"},
    {"item": "Options on futures", "state": "NOT_OFFERED", "wording": "no free delayed source for CME option prices", "where": "hedge panel of a futures position"},
    {"item": "Early assignment of short options", "state": "NOT_SIMULATED",
     "wording": "short options are exercised / assigned only at expiry, by moneyness at the close", "where": "exercise and assignment notes on the instrument page and on positions"},
    {"item": "Extended-hours trading", "state": "NOT_SIMULATED",
     "wording": "an order entered outside the regular session rests until the next regular open", "where": "order ticket"},
    # C6 / 15.7 item 6: the wording follows the margin calls (unsupported_rows swaps it in once collateral.margin_calls_rows exists)
    {"item": "Forced liquidation", "state": "NEVER", "wording": "positions are never sold for you; a margin shortfall raises a warning and an attention item",
     "where": "Risk page, attention strip"},
    {"item": "CSA interest on cash collateral", "state": "NOT_SIMULATED",
     "wording": "variation margin received under a CSA is restricted and earns no PB credit interest; collateral posted earns none",
     "where": "prime-broker Cash & financing"},
    {"item": "Stock splits applied to lots", "state": "LIMITED", "wording": "a split raises an attention warning for manual review; lots are not adjusted",
     "where": "attention strip, position notes"},
    {"item": "Recall notices from lender data", "state": "LIMITED", "wording": "an information notice from the FINRA short-volume heuristic only; no buy-ins ever",
     "where": "attention strip"},
    {"item": "Borrow availability and fee tiers", "state": "HEURISTIC", "wording": "estimated from FINRA short volume and average daily volume, disclosed",
     "where": "ticket borrow block"},
    {"item": "Futures initial / maintenance margins and position limits", "state": "APPROXIMATED",
     "wording": "static tables (assumed values; edit them in Settings > Margins & limits); verify current exchange values", "where": "Settings > Margins & limits"},
    {"item": "CME holiday calendar; futures settlement prices", "state": "APPROXIMATED",
     "wording": "CME holidays follow the NYSE calendar; the settlement is the observed print at settle time", "where": "Settings > Calendar, futures notes"},
    {"item": "Live quotes", "state": "NONE", "wording": "every price is delayed, a close or computed; 'live' is never claimed", "where": "the freshness chip on every price"},
    {"item": "Cboe NBBO for equity fills", "state": "OPPORTUNISTIC", "wording": "used only when a fresh option chain is already cached; otherwise the Yahoo last",
     "where": "the price basis in the order preview"},
    {"item": "Executions while Yahoo / Cboe are down", "state": "PAUSED", "wording": "paused as stale: there is no second equity source",
     "where": "attention strip, ticket"},
    {"item": "Treasury interest on non-USD cash", "state": "ZERO", "wording": "0 %", "where": "Treasury"},
    {"item": "Policy rates for JPY / CHF / CAD / AUD loans", "state": "MONTHLY_PROXY", "wording": "monthly OECD series, its as-of month shown",
     "where": "loan form"},
    {"item": "Risk and compliance", "state": "INFORM_ONLY", "wording": "warnings and notices only; not a compliance system", "where": "Risk page"},
    {"item": "Private marks under scenario shocks; value at risk", "state": "NOT_APPLIED", "wording": "private marks are not shocked; no VaR",
     "where": "Risk page"},
    {"item": "OTC dealt rates", "state": "MODELLED", "wording": "the model par rate plus or minus the dealer's half-width; margins from the dealer schedule",
     "where": "the swap preview's cost of dealing"},
    {"item": "Phone / LAN access", "state": "NOT_OFFERED", "wording": "the terminal answers on this computer only (127.0.0.1)", "where": "-"},
]
# A row shown while the running build lacks the module / capability that removes it (``present(rt)`` false).  Each test is the
# hook the owning package adds (BUILD2_PLAN contracts), so the row disappears by itself when that package ships.
PLANNED = [
    {"present": lambda rt: getattr(rt, "otc_options", None) is not None,
     "row": {"item": "OTC equity and FX options and forwards with a prime broker", "state": "NOT_YET", "wording": "not in this version yet",
             "where": "prime-broker OTC tab"}},
    {"present": lambda rt: callable(getattr(getattr(rt, "fx", None), "forward_rate", None)),
     "row": {"item": "FX forwards and FX crosses", "state": "NOT_YET", "wording": "spot only in this version", "where": "Foreign Markets & FX"}},
    {"present": lambda rt: callable(getattr(getattr(rt, "fx", None), "venue_fee_c", None)),
     "row": {"item": "Trading foreign listings", "state": "NOT_YET", "wording": "quotes and marks only in this version", "where": "Foreign Markets & FX"}},
    {"present": lambda rt: callable(getattr(getattr(rt, "bonds", None), "dv01_c", None)),
     "row": {"item": "Bond execution (Treasuries, corporates, foreign government bonds)", "state": "LIMITED",
             "wording": "model price only, not executable in this version", "where": "Debt Instruments"}},
    {"present": lambda rt: getattr(rt, "counterparties", None) is not None,
     "row": {"item": "Repo / reverse repo, securities lending out of inventory, the counterparty directory", "state": "NOT_YET",
             "wording": "not in this version yet", "where": "Credit Instruments"}},
    # FINSIM1_PLAN C6 / 15.3 E1 / 15.7 item 6: a function probe is callable(<module>.<function>); each function ships with its feature
    {"present": lambda rt: _has_fn(rt, "collateral", "margin_calls_rows"),
     "row": {"item": "Margin calls", "state": "NOT_YET", "wording": "not in this version yet: a margin shortfall raises a warning and an attention item",
             "where": "Risk page, attention strip"}},
    {"present": lambda rt: _has_fn(rt, "blotters", "execution_detail"),
     "row": {"item": "Simulated counterparty settlement fails", "state": "NOT_YET",
             "wording": "unfunded payables are already marked failed and retried automatically; the fail you trigger yourself, and its resolution, "
                        "are not in this version yet", "where": "Activity, account Cash & financing"}},
    {"present": lambda rt: _has_fn(rt, "swaps", "rfq_rows"),
     "row": {"item": "OTC dealer quotes (request a quote, then accept or decline it)", "state": "NOT_YET",
             "wording": "not in this version yet: an OTC contract books at once, at the dealer's model price plus or minus its half-width",
             "where": "prime-broker OTC tab"}},
    {"present": lambda rt: _has_fn(rt, "orders", "gtd_supported"),
     "row": {"item": "Good-till-date and next-session orders", "state": "NOT_YET", "wording": "not in this version yet: orders are DAY or GTC",
             "where": "order ticket"}},
    {"present": lambda rt: getattr(rt, "importer", None) is not None,
     "row": {"item": "Historical trade import", "state": "NOT_YET", "wording": "not in this version yet", "where": "-"}},
    {"present": lambda rt: _has_fn(rt, "collateral", "pledges_view"),
     "row": {"item": "Securities pledged as CSA collateral", "state": "NOT_YET", "wording": "not in this version yet: collateral under a CSA is cash",
             "where": "prime-broker account"}},
]
#: C6 / 15.7 item 6: the forced-liquidation row's wording once margin calls ship (collateral.margin_calls_rows)
FORCED_LIQUIDATION_ITEM = "Forced liquidation"
FORCED_LIQUIDATION_WITH_CALLS = "positions are never sold for you; margin calls are recorded and met from cash / Treasury / PB headroom or stay open"


def _has_fn(rt, attr: str, fn: str) -> bool:
    """A C6 function probe: ``callable(rt.<attr>.<fn>)`` (False when the module is not bound)."""
    return callable(getattr(getattr(rt, attr, None), fn, None))


# ---- errors -------------------------------------------------------------------------------------------------------------------
class ApiError(Exception):
    """An HTTP error raised by a handler: ``http``, ``code``, ``message``, ``details``."""

    def __init__(self, http: int, code: str, message: str = "", details: Optional[dict] = None):
        super().__init__(f"{http} {code}: {message}")
        self.http, self.code, self.message, self.details = int(http), code, message or code, details or {}


class NotFound(ApiError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(404, "NOT_FOUND", message, details)


class NotBuilt(ApiError):
    def __init__(self, package: str, module: str, message: str = ""):
        super().__init__(501, "NOT_BUILT", message or f"this route needs {module} ({package}), which is not built yet", {"package": package, "module": module})


class NotFoundReject(RejectError):
    """A 404 raised from inside a light command (``attention.resolve`` on a missing id)."""
    http = 404


def error_body(code: str, message: str, details: Optional[dict] = None, request_id: Optional[str] = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}, "request_id": request_id}


# ---- codec ------------------------------------------------------------------------------------------------------------------
def encode(x: Any) -> Any:
    """The §15 JSON codec (see the module docstring)."""
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, sqlite3.Row):
        return _encode_dict({k: x[k] for k in x.keys()})
    if isinstance(x, dict):
        return _encode_dict(x)
    if isinstance(x, (set, frozenset)):
        return [encode(v) for v in sorted(x, key=str)]
    if isinstance(x, (list, tuple)):
        return [encode(v) for v in x]
    if hasattr(x, "to_dict"):
        return encode(x.to_dict())
    if dataclasses.is_dataclass(x):
        return encode({f.name: getattr(x, f.name) for f in dataclasses.fields(x)})
    if isinstance(x, (bytes, bytearray)):
        return bytes(x).decode("utf-8", "replace")
    return str(x)


def _cents(v: int) -> str:
    return str(from_c(int(v)))


def _encode_dict(d: dict) -> dict:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        k = str(k)
        if k.endswith("_c") and len(k) > 2:
            base = "nav_0" if k == "nav0_c" else k[:-2]
            if base in d or isinstance(v, bool):
                out[k] = encode(v)
            elif isinstance(v, int):
                out[base] = _cents(v)
            elif v is None:
                out[base] = None
            elif isinstance(v, dict) and v and all(isinstance(t, int) and not isinstance(t, bool) or t is None for t in v.values()):
                out[base] = {str(kk): (None if t is None else _cents(t)) for kk, t in v.items()}
            else:
                out[k] = encode(v)
            continue
        out[k] = encode(v)
        if k.endswith("_ts") and isinstance(v, int) and not isinstance(v, bool) and v > 0 and (k[:-3] + "_ny") not in d:
            out[k[:-3] + "_ny"] = ny_str(v)
    return out


def quote_dict(rec, now: int) -> Optional[dict]:
    """A ``QuoteRecord`` as the API shows it: the record plus ``age_s`` (seconds since the provider's quote time)."""
    if rec is None:
        return None
    d = rec.to_dict() if hasattr(rec, "to_dict") else dict(rec)
    qt = d.get("quote_time")
    d["age_s"] = None if qt is None else max(0, int(now) - int(qt))
    return d


def mark_dict(view) -> Optional[dict]:
    """A ``MarkView`` with both spellings: ``value`` / ``as_of`` (§15) and ``mark`` / ``quote_time`` (the UI fixtures)."""
    if view is None:
        return None
    d = view.to_dict() if hasattr(view, "to_dict") else dict(view)
    d.setdefault("mark", d.get("value"))
    d.setdefault("quote_time", d.get("as_of"))
    return d


# ---- request / response ----------------------------------------------------------------------------------------------------
class Request:
    def __init__(self, rt, method: str, path: str, params: Tuple[str, ...], query: Dict[str, str], body: Optional[dict], client_ip: str, headers: Dict[str, str]):
        self.rt, self.method, self.path, self.params, self.query, self.body, self.client_ip, self.headers = rt, method, path, params, query, body, client_ip, headers
        self.request_id: Optional[str] = None
        self.now = int(rt.clock.epoch())

    # ---- query helpers ------------------------------------------------------------------------------------------------
    def q(self, name: str, default: Optional[str] = None) -> Optional[str]:
        v = self.query.get(name)
        return default if v is None or v == "" else v

    def q_int(self, name: str, default: int, lo: int = 0, hi: int = 10_000) -> int:
        v = self.query.get(name)
        if v is None or v == "":
            return default
        try:
            n = int(v)
        except ValueError:
            raise ApiError(400, "BAD_REQUEST", f"{name} must be an integer, got {v!r}")
        return max(lo, min(hi, n))

    def loopback(self) -> bool:
        return str(self.client_ip or "") in LOOPBACK

    # ---- body helpers -------------------------------------------------------------------------------------------------
    def b(self, name: str, default=None):
        return (self.body or {}).get(name, default)

    def amount_c(self, key_c: str = "amount_c", key: str = "amount") -> int:
        from .commands import require_amount_c
        return require_amount_c(self.body or {}, key_c, key)

    def payload(self, *keys: str, **extra) -> dict:
        """The body restricted to ``keys`` (plus ``extra``), never ``request_id``."""
        src = self.body or {}
        out = {k: src[k] for k in keys if k in src}
        out.update({k: v for k, v in extra.items() if v is not None})
        return out

    # ---- scope / book -------------------------------------------------------------------------------------------------
    def lifetime(self) -> str:
        return lifetime_for(self.rt, self.q("book") or self.b("book"))

    def scope(self, default: str = "ALL") -> str:
        s = self.q("scope") or default
        if s == "ALL":
            try:
                return f"{self.lifetime()}:ALL"
            except NotFound:
                raise
            except ScopeError:
                return s
        return s


def lifetime_for(rt, book_code: Optional[str] = None) -> str:
    """The OPEN lifetime of ``book_code`` (404 UNKNOWN_BOOK / NO_OPEN_LIFETIME), else of the Accounting's selected
    book, else the first open lifetime."""
    with rt.db.reader() as conn:
        if book_code:
            if _books.book_of(conn, str(book_code)) is None:
                raise ApiError(404, "UNKNOWN_BOOK", f"book {book_code!r} does not exist")
            row = _books.open_lifetime(conn, str(book_code))
            if row is None:
                raise ApiError(404, "NO_OPEN_LIFETIME", f"book {book_code} has no open lifetime")
            return row["lifetime_id"]
        sel = getattr(rt.accounting, "selected_book", None)
        if sel:
            row = _books.open_lifetime(conn, sel)
            if row is not None:
                return row["lifetime_id"]
        lids = _books.open_lifetimes(conn)
    if not lids:
        raise ScopeError("no open lifetime")
    return lids[0]


def unsupported_rows(rt) -> List[dict]:
    """SHELL-07: the static rows, then every bound product module's own ``unsupported()`` rows (OPTIONAL_MODULES order), then the
    ``PLANNED`` rows whose capability the running build lacks.  Each row: item, state, wording, where (trader wording)."""
    rows: List[dict] = [dict(r) for r in UNSUPPORTED]
    try:
        calls = _has_fn(rt, "collateral", "margin_calls_rows")
    except Exception:   # noqa: BLE001 - a failing probe means "not present"
        calls = False
    if calls:
        for r in rows:
            if r["item"] == FORCED_LIQUIDATION_ITEM:
                r["wording"] = FORCED_LIQUIDATION_WITH_CALLS
    try:
        from .server import OPTIONAL_MODULES as attrs
    except Exception:   # noqa: BLE001 - a stand-in runtime: the static rows only
        attrs = {}
    seen = set()
    for attr in attrs:
        mod = getattr(rt, attr, None)
        fn = getattr(mod, "unsupported", None) if mod is not None else None
        if not callable(fn) or id(mod) in seen:
            continue
        seen.add(id(mod))
        try:
            got = fn() or []
        except Exception as e:   # noqa: BLE001 - named, never hidden
            got = [{"item": f"{attr}: the list of what it does not do failed ({type(e).__name__})", "state": "UNKNOWN", "wording": str(e)[:200], "where": "-"}]
        for r in got:
            if isinstance(r, dict) and r.get("item"):
                rows.append({"item": str(r.get("item")), "state": str(r.get("state") or "NOT_OFFERED"), "wording": str(r.get("wording") or ""),
                             "where": str(r.get("where") or "")})
    for p in PLANNED:
        try:
            have = bool(p["present"](rt))
        except Exception:   # noqa: BLE001 - a failing probe means "not present"
            have = False
        if not have:
            rows.append(dict(p["row"]))
    return rows


def need(rt, attr: str):
    """The bound product module / service or 501 NOT_BUILT with its package id."""
    mod = getattr(rt, attr, None)
    if mod is None:
        raise NotBuilt(PACKAGES.get(attr, "?"), attr)
    return mod


# ---- the router -------------------------------------------------------------------------------------------------------------
Handler = Callable[[Request], Any]


class Router:
    """Routes -> handlers.  ``handle`` is the single entry point (the HTTP server and the tests both call it)."""

    def __init__(self, rt):
        self.rt = rt
        self.routes: List[Tuple[str, "re.Pattern[str]", Handler, bool, bool]] = []
        self.static_dir = os.path.join(config.package_dir(), "static")
        self.product_routes: Dict[str, int] = {}
        from . import api_views, api_products
        _register_core(self)
        self._register_products()
        api_views.register_routes(self)
        api_products.register_routes(self)

    def _register_products(self) -> None:
        """C1 (BUILD2_PLAN): every bound optional product module that defines ``register_routes(router)`` adds its routes
        BEFORE ``api_views`` / ``api_products`` (``server.OPTIONAL_MODULES`` order, ``rt.<attr>``), so a product route can
        take over a path there (first match wins); the core routes (health, state, settings, static ...) stay first.  A module
        whose ``register_routes`` raises is reported in ``rt.modules`` (``error: register_routes: ...``) and logged; its
        routes are dropped and the server still starts."""
        rt = self.rt
        try:
            from .server import OPTIONAL_MODULES as attrs
        except Exception:   # noqa: BLE001 - a stand-in runtime without the server module: no product routes
            return
        seen = set()
        for attr in attrs:
            mod = getattr(rt, attr, None)
            fn = getattr(mod, "register_routes", None) if mod is not None else None
            if not callable(fn) or id(mod) in seen:
                continue
            seen.add(id(mod))
            before = len(self.routes)
            try:
                fn(self)
            except Exception as e:   # noqa: BLE001 - reported, never hidden; the rest of the API still serves
                del self.routes[before:]
                mods = getattr(rt, "modules", None)
                if isinstance(mods, dict):
                    mods[attr] = f"error: register_routes: {type(e).__name__}: {e}"
                if getattr(rt, "log", None):
                    rt.log.exception("optional module %s: register_routes failed", attr)
                continue
            self.product_routes[attr] = len(self.routes) - before

    def add(self, method: str, pattern: str, fn: Handler, *, mutating: Optional[bool] = None, loopback: bool = False) -> None:
        mut = (method in ("POST", "PATCH", "DELETE")) if mutating is None else bool(mutating)
        self.routes.append((method, re.compile("^" + pattern + "$"), fn, mut, loopback))

    # ---- dispatch -------------------------------------------------------------------------------------------------
    def match(self, method: str, path: str):
        allowed = False
        for m, rx, fn, mut, loop in self.routes:
            mo = rx.match(path)
            if mo is None:
                continue
            if m == method or (method == "HEAD" and m == "GET"):
                return fn, tuple(unquote(g) for g in mo.groups()), mut, loop
            allowed = True
        return None, (), allowed, False

    def handle(self, method: str, path: str, query=None, body=None, client_ip: str = "127.0.0.1", headers: Optional[Dict[str, str]] = None):
        rt = self.rt
        method = (method or "GET").upper()
        if "?" in path:
            path, _, qs = path.partition("?")
            query = query or qs
        if isinstance(query, str):
            query = {k: v[-1] for k, v in parse_qs(query, keep_blank_values=True).items()}
        query = dict(query or {})
        hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        rid: Optional[str] = None
        try:
            if isinstance(body, (bytes, bytearray)):
                body = json.loads(bytes(body).decode("utf-8")) if bytes(body).strip() else None
            elif isinstance(body, str):
                body = json.loads(body) if body.strip() else None
        except ValueError as e:
            return self._error(400, "BAD_JSON", f"the body is not valid JSON: {e}", None, None)
        if method in ("POST", "PATCH", "DELETE", "PUT"):
            if body is None:
                body = {}
            if not isinstance(body, dict):
                return self._error(400, "BAD_REQUEST", "the body must be a JSON object", None, None)
            rid = body.get("request_id") or hdrs.get("x-request-id")
            rid = str(rid) if rid else None
        fn, params, mut, loop = self.match(method, path)
        if fn is None:
            if mut is True:
                return self._error(405, "METHOD_NOT_ALLOWED", f"{method} is not allowed on {path}", None, rid)
            return self._error(404, "NOT_FOUND", f"no route {method} {path}", None, rid)
        if loop and str(client_ip or "") not in LOOPBACK:
            return self._error(403, "FORBIDDEN", "this route answers on loopback only", {"client_ip": client_ip}, rid)
        if mut and not rid:
            return self._error(400, "BAD_REQUEST_ID", "request_id is required in the body or the X-Request-Id header", None, None)
        req = Request(rt, method, path, params, query, body if isinstance(body, dict) else None, client_ip, hdrs)
        req.request_id = rid
        try:
            out = fn(req)
        except ApiError as e:
            return self._error(e.http, e.code, e.message, e.details, rid)
        except RejectError as e:                      # RejectError 400, ConflictError 409, orders.NotBuilt 501, options.ServiceUnavailable 503
            return self._error(int(getattr(e, "http", 400) or 400), e.code, e.message, _plain(e.details), rid)
        except ScopeError as e:
            return self._error(404, "NOT_FOUND", str(e.args[0]) if e.args else "unknown scope", None, rid)
        except BusyError as e:
            return self._error(503, "BUSY", str(e), None, rid, retry=True)
        except (ValueError, TypeError, KeyError) as e:
            if rt.log:
                rt.log.exception("api %s %s: bad request", method, path)
            return self._error(400, "BAD_REQUEST", f"{type(e).__name__}: {e}", None, rid)
        except Exception as e:   # noqa: BLE001 — surfaced as 500 with an incident id; never swallowed
            tb = traceback.format_exc()
            if rt.log:
                rt.log.exception("api %s %s failed", method, path)
            incident = self._incident(method, path, e, tb)
            return self._error(500, "INTERNAL", f"{type(e).__name__}: {e}", {"incident_id": incident}, rid)
        return self._finish(out, rid, method)

    # ---- responses ----------------------------------------------------------------------------------------------------
    def _finish(self, out, rid: Optional[str], method: str):
        status, body, headers = 200, out, {}
        if isinstance(out, Result):
            return self._from_result(out, rid)
        if isinstance(out, tuple):
            if len(out) == 2:
                status, body = out
            elif len(out) == 3:
                status, body, headers = out
        if isinstance(body, Result):
            res, body = body, None
            s2, b2, h2 = self._from_result(res, rid)
            if s2 >= 400:
                return s2, b2, h2
            headers = dict(h2, **headers)
            body = b2
        headers = dict(headers)
        if isinstance(body, (bytes, bytearray)):
            headers.setdefault("Content-Type", "application/octet-stream")
        elif isinstance(body, str):
            headers.setdefault("Content-Type", CSV_CT if "csv" in headers.get("Content-Type", "csv") else "text/plain; charset=utf-8")
        else:
            body = encode(body)
            headers.setdefault("Content-Type", JSON_CT)
        headers.setdefault("Cache-Control", NO_STORE)
        if status == 503:
            headers.setdefault("Retry-After", "1")
        if method == "HEAD":
            body = b"" if isinstance(body, (bytes, bytearray)) else body
        return int(status), body, headers

    def _from_result(self, res: Result, rid: Optional[str]):
        headers = {"Content-Type": JSON_CT, "Cache-Control": NO_STORE}
        if res.replayed:
            headers["X-Replayed"] = "1"
        if res.retry_after or res.http == 503:
            headers["Retry-After"] = str(res.retry_after or 1)
        body = res.body
        if not res.ok and isinstance(body, dict) and "error" in body:
            body = dict(body)
            body.setdefault("request_id", rid)
        return int(res.http), encode(body), headers

    def _error(self, status: int, code: str, message: str, details: Optional[dict], rid: Optional[str], retry: bool = False):
        headers = {"Content-Type": JSON_CT, "Cache-Control": NO_STORE}
        if retry or status == 503:
            headers["Retry-After"] = "1"
        return int(status), encode(error_body(code, message, details, rid)), headers

    def _incident(self, method: str, path: str, e: Exception, tb: str) -> str:
        rt = self.rt
        incident = f"api:{uuid.uuid4().hex[:12]}"
        bus = getattr(rt, "bus", None)
        if bus is None or "attention.open" not in bus.kinds():
            return incident
        try:
            res = bus.execute(Command("attention.open", {"severity": "ERROR", "source": "api", "code": "REQUEST_FAILED",
                                                         "message": f"{method} {path}: {type(e).__name__}: {str(e)[:300]}",
                                                         "details": {"traceback": tb[-2000:], "incident_id": incident}}, f"inc:{incident}", "api"))
            if res.ok and isinstance(res.body, dict) and res.body.get("id") is not None:
                return f"attention:{res.body['id']}"
        except Exception:   # noqa: BLE001 — the incident row is best effort; the log already has the traceback
            pass
        return incident

    # ---- static -------------------------------------------------------------------------------------------------------
    def static(self, rel: str):
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel:
            rel = "index.html"
        base = os.path.abspath(self.static_dir)
        full = os.path.abspath(os.path.join(base, *rel.split("/")))
        if ".." not in rel.split("/") and rel in PLANNED_STATIC and (full == base or full.startswith(base + os.sep)) and not os.path.isfile(full):
            return 200, f"/* {rel}: not built yet */\n".encode("ascii"), {"Content-Type": "application/javascript; charset=utf-8", "Cache-Control": NO_STORE}
        if ".." in rel.split("/") or not (full == base or full.startswith(base + os.sep)) or not os.path.isfile(full):
            raise NotFound(f"no static file {rel}")
        with open(full, "rb") as f:
            data = f.read()
        ct = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ct.startswith("text/") or ct in ("application/javascript", "application/json"):
            ct += "; charset=utf-8"
        return 200, data, {"Content-Type": ct, "Cache-Control": NO_STORE}


def _plain(x):
    """Error details become plain JSON (Decimals -> strings)."""
    return encode(x) if x is not None else {}


# ---- commands from the API --------------------------------------------------------------------------------------------------
#: the "come back with more" answers that are not rejections (never recorded): a confirmation or a fresh preview is needed, or the
#: request id was reused / malformed
NOT_REJECTIONS = ("CONFIRM_REQUIRED", "PREVIEW_STALE", "REQUEST_ID_REUSED", "BAD_REQUEST_ID")
#: C5: [(frozenset of command kinds, fn(req, kind, payload, result))] - see add_refusal_recorder
_REFUSAL_RECORDERS: List[Tuple[frozenset, Callable]] = []


def add_refusal_recorder(kinds, fn: Callable) -> None:
    """C5 (FINSIM1_PLAN): ``run_command`` calls ``fn(req, kind, payload, result)`` for every 4xx result of a command whose kind is in
    ``kinds`` (one kind or an iterable) and whose code is not in ``NOT_REJECTIONS`` (a replay is not a new refusal).  The recorder
    never changes the HTTP answer: an exception it raises is logged and swallowed.  Registering the same function for the same
    kinds again is a no-op (a module bound twice records once)."""
    ks = frozenset([kinds] if isinstance(kinds, str) else (str(k) for k in kinds))
    if not ks or not callable(fn):
        raise ValueError("add_refusal_recorder needs command kinds and a callable")
    if any(k == ks and f is fn for k, f in _REFUSAL_RECORDERS):
        return
    _REFUSAL_RECORDERS.append((ks, fn))


def remove_refusal_recorder(fn: Callable) -> int:
    """Unregister every C5 recorder entry of ``fn`` (tests); returns how many were removed."""
    before = len(_REFUSAL_RECORDERS)
    _REFUSAL_RECORDERS[:] = [(k, f) for k, f in _REFUSAL_RECORDERS if f is not fn]
    return before - len(_REFUSAL_RECORDERS)


def refusal_recorders() -> List[Tuple[frozenset, Callable]]:
    """The registered C5 recorders (a copy)."""
    return list(_REFUSAL_RECORDERS)


def _record_refusal(req: Request, kind: str, payload: dict, res: Result) -> None:
    if not (400 <= int(res.http) < 500) or res.replayed or res.code in NOT_REJECTIONS:
        return
    for kinds, fn in list(_REFUSAL_RECORDERS):
        if kind not in kinds:
            continue
        try:
            fn(req, kind, payload, res)
        except Exception:   # noqa: BLE001 - C5: a recorder never changes the answer; its failure is logged
            log = getattr(req.rt, "log", None)
            if log:
                log.exception("refusal recorder %s failed for %s", getattr(fn, "__name__", fn), kind)


def run_command(req: Request, kind: str, payload: dict, actor: str = "ui") -> Result:
    """Execute one command for this request; a missing handler is 501 NOT_BUILT (the owning package is named).  A 4xx
    result goes to the C5 refusal recorders of its kind (``add_refusal_recorder``); the answer itself is returned unchanged."""
    bus = req.rt.bus
    if kind not in bus.kinds():
        owner = {"order": "orders", "credit.borrow": "lending", "credit.return": "lending", "credit.loan": "credit", "credit.pb": "credit", "fx": "fx",
                 "options": "options", "otc": "swaps", "swap": "swaps", "private": "private", "pc": "private_credit", "book.reset": "lifetimes",
                 "book.onboard": "lifetimes", "account.open_pb": "prime", "account.update_schedule": "prime", "lifecycle": "lifecycle"}
        attr = next((v for k, v in owner.items() if kind.startswith(k)), kind.split(".")[0])
        raise NotBuilt(PACKAGES.get(attr, "?"), attr, f"command {kind} has no handler in this build")
    res = bus.execute(Command(kind, payload, req.request_id or "", actor))
    if _REFUSAL_RECORDERS and not res.ok:
        _record_refusal(req, kind, payload, res)
    return res


def ok_or_raise(res: Result) -> dict:
    """The command's body on success; on failure the stored error becomes the HTTP answer (status + body kept)."""
    if res.ok:
        return res.body if isinstance(res.body, dict) else {}
    raise _ResultError(res)


class _ResultError(ApiError):
    def __init__(self, res: Result):
        err = (res.body or {}).get("error") if isinstance(res.body, dict) else None
        code = (err or {}).get("code") or "ERROR"
        super().__init__(res.http, code, (err or {}).get("message") or code, (err or {}).get("details") or {})
        self.result = res


# ---- light commands owned by the API layer (§4.1 "api.py") ---------------------------------------------------------------
SETTING_KEYS = {"participation_rate", "sweep_out_time_et", "snapshot_time_et", "treasury_reserve_c", "session_overrides", "sweep_confirm_threshold_c", "theme",
                "pb_otc_threshold_c", "qp_threshold_c", "book_defaults", "private_defaults", "build2_bonds",
                "treasury_reserve_by_book", "futures_margin_overrides", "position_limit_overrides", "keybinds"}
CENT_SETTINGS = {"treasury_reserve_c", "sweep_confirm_threshold_c", "pb_otc_threshold_c"}
#: the words a refusal uses for a setting (BM-02: never the internal key)
SETTING_LABELS = {"participation_rate": "participation rate", "sweep_out_time_et": "sweep-out time", "snapshot_time_et": "snapshot time",
                  "treasury_reserve_c": "Treasury reserve", "treasury_reserve_by_book": "Treasury reserve", "session_overrides": "session overrides",
                  "sweep_confirm_threshold_c": "sweep confirmation threshold", "theme": "theme", "pb_otc_threshold_c": "PB / OTC eligibility threshold",
                  "qp_threshold_c": "qualified-purchaser threshold", "book_defaults": "book names and starting capitals", "private_defaults": "private markets defaults",
                  "build2_bonds": "bond execution", "futures_margin_overrides": "futures margins",
                  "position_limit_overrides": "position limits", "keybinds": "keyboard keys"}
#: the settings ``book.update_settings`` owns (names rename the book at once; SR-07): a Settings save of these goes through it
BOOK_SETTING_KEYS = ("book_defaults", "pb_otc_threshold", "pb_otc_threshold_c", "qp_threshold", "qp_threshold_c")
OPTION_LIMIT_KEY = "OPTIONS"            # position_limit_overrides key of the default listed-option position limit (contracts)
_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---- keyboard keys (owner 2026-10-21 17:10: the F-keys, and his own keys saved in the app; docs/KEYBINDS.md) -----------------
# static/keys.js holds every action and its DEFAULT key; the setting ``keybinds`` stores only the owner's overrides
# ({action_id: key | null}, null = no key).  A save sends {"keybinds": {...changes}, "keybind_defaults": {...every default}}:
# the defaults are used to check the result (never stored).  The rules below mirror keys.js (parse / scopeOf / REFUSED).
KEYBIND_ID_RE = re.compile(r"^[a-z0-9_.]{1,64}$")
KEYBIND_NAMED = frozenset({"Enter", "Escape", "Space", "Tab", "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
                           "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"})
KEYBIND_PUNCT = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
_KEYBIND_FKEY_RE = re.compile(r"^F([1-9]|1[0-9]|2[0-4])$")
#: keys a browser or Windows never hands to a page (or that the page needs), refused with the reason (keys.js REFUSED)
KEYBIND_REFUSED = {
    "Ctrl+W": "the browser closes the tab or window with it - the app never sees it",
    "Ctrl+Shift+W": "the browser closes the window with it - the app never sees it",
    "Ctrl+T": "the browser opens a new tab with it - the app never sees it",
    "Ctrl+Shift+T": "the browser reopens the last closed tab with it - the app never sees it",
    "Ctrl+N": "the browser opens a new window with it - the app never sees it",
    "Ctrl+Shift+N": "the browser opens a private window with it - the app never sees it",
    "Ctrl+Tab": "the browser switches tabs with it - the app never sees it",
    "Ctrl+Shift+Tab": "the browser switches tabs with it - the app never sees it",
    "Ctrl+L": "the browser puts the cursor in its address bar with it",
    "Alt+F4": "Windows closes the window with it - the app never sees it",
    "Alt+Tab": "Windows switches windows with it - the app never sees it",
    "Alt+Shift+Tab": "Windows switches windows with it - the app never sees it",
    "Ctrl+F4": "the browser closes the tab with it - the app never sees it",
    "Ctrl+PageUp": "the browser switches tabs with it - the app never sees it",
    "Ctrl+PageDown": "the browser switches tabs with it - the app never sees it",
    "Ctrl+Escape": "Windows opens the Start menu with it - the app never sees it",
    "Ctrl+Shift+Escape": "Windows opens the Task Manager with it - the app never sees it",
    "Alt+Escape": "Windows switches windows with it - the app never sees it",
    "Tab": "it moves the cursor between boxes and buttons",
    "Shift+Tab": "it moves the cursor between boxes and buttons",
}
KEYBIND_WIDGET_SCOPES = ("ticket", "grid", "dialog")
KEYBIND_MAX = 400


def keybind_canonical(text) -> Optional[str]:
    """``text`` when it is a key in the ONE canonical form, else None: ``Ctrl+Alt+Shift+Key`` (modifiers optional, in that
    order); Key = F1-F24, A-Z, 0-9, a named key (Enter, Escape, Space, Tab, Backspace, Delete, Insert, Home, End, PageUp,
    PageDown, ArrowUp/Down/Left/Right) or one ASCII punctuation character - never with Shift (the character carries it:
    '?', not 'Shift+/'; '!' not 'Shift+1')."""
    if not isinstance(text, str) or not text:
        return None
    s, shift = text, False
    for mod in ("Ctrl+", "Alt+", "Shift+"):
        if s.startswith(mod) and len(s) > len(mod):
            s = s[len(mod):]
            shift = shift or mod == "Shift+"
    single = len(s) == 1
    ok = bool(_KEYBIND_FKEY_RE.fullmatch(s)) or s in KEYBIND_NAMED or (single and ("A" <= s <= "Z" or "0" <= s <= "9" or s in KEYBIND_PUNCT))
    if not ok or (shift and single and not "A" <= s <= "Z"):
        return None
    return text


def keybind_scope(action_id) -> str:
    """Where an action acts, from its id (keys.js scopeOf): ticket.* the dock ticket, grid.* a table, dialog.* an open dialog,
    everything else global.  Two actions may share a key only in different widget scopes (neither global)."""
    head = str(action_id).split(".", 1)[0]
    return head if head in KEYBIND_WIDGET_SCOPES else "global"


def _keybind_map(value, label: str) -> Dict[str, Optional[str]]:
    """{action_id: canonical key | None}; every refusal names the field (and the action / key)."""
    if not isinstance(value, dict):
        raise RejectError("SETTING_VALUE", f"{label}: expected a map of action to key (or null for no key)", {"field": label})
    if len(value) > KEYBIND_MAX:
        raise RejectError("SETTING_VALUE", f"{label}: {len(value)} entries (at most {KEYBIND_MAX})", {"field": label})
    out: Dict[str, Optional[str]] = {}
    for aid, key in value.items():
        a = str(aid)
        if not KEYBIND_ID_RE.fullmatch(a):                     # fullmatch: 'page.news\n' is not an action name ('$' matches before a final newline)
            raise RejectError("SETTING_VALUE", f"{label}: {a!r} is not an action name (lower-case letters, digits, '_' and '.')", {"field": label, "action": a})
        if key is None:
            out[a] = None
            continue
        if not isinstance(key, str) or keybind_canonical(key) is None:
            raise RejectError("SETTING_VALUE", f"{label}: {key!r} is not a key in the one form the app stores (Ctrl+Alt+Shift+Key: F1-F24, "
                              "A-Z, 0-9, Enter, Escape, Space, the arrows ... or one punctuation character)", {"field": label, "action": a, "key": key})
        if key in KEYBIND_REFUSED:
            raise RejectError("SETTING_VALUE", f"{label}: {key} cannot be used: {KEYBIND_REFUSED[key]}", {"field": label, "action": a, "key": key})
        out[a] = key
    return out


def keybind_clash(binding: Dict[str, Optional[str]]) -> Optional[Tuple[str, str, str]]:
    """The first key held by two actions that can act together (the same scope, or either global): (key, id_a, id_b)."""
    by_key: Dict[str, List[str]] = {}
    for aid in sorted(binding):
        key = binding[aid]
        if key:
            by_key.setdefault(key, []).append(aid)
    for key in sorted(by_key):
        ids = by_key[key]
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                sa, sb = keybind_scope(a), keybind_scope(b)
                if sa == sb or "global" in (sa, sb):
                    return key, a, b
    return None


def _keybinds_merge(conn, new: Dict[str, Optional[str]], defaults: Optional[Dict[str, Optional[str]]]) -> Dict[str, Optional[str]]:
    """The overrides stored after this save: the stored ones updated with ``new``.  The client's defaults
    (``keybind_defaults``, every action of keys.js) are required: an action this build does not have is refused, an
    override equal to its default (or of an action the build no longer has) is dropped - only the owner's changes are kept,
    at most KEYBIND_MAX.  The keys in force (defaults + overrides) must never put one key on two actions that can act
    together - else nothing is written and the refusal names the key and both actions."""
    label = SETTING_LABELS["keybinds"]
    if defaults is None:                                       # KB-R1-08: every check needs the build's actions and default keys
        raise RejectError("SETTING_VALUE", f"{label}: send keybind_defaults (every action of this build with its default key) with keybinds",
                          {"field": label})
    cur = get_setting(conn, "keybinds", {})
    merged = dict(cur) if isinstance(cur, dict) else {}
    merged.update(new)
    unknown = sorted(a for a in new if a not in defaults)
    if unknown:
        raise RejectError("SETTING_VALUE", f"{label}: {unknown[0]!r} is not an action of this build", {"field": label, "action": unknown[0]})
    # only the owner's changes of actions this build has: an override equal to its default, or of an action that no longer
    # exists, is dropped (the stored map never grows past the actions of the build)
    merged = {a: k for a, k in merged.items() if a in defaults and defaults[a] != k}
    if len(merged) > KEYBIND_MAX:
        raise RejectError("SETTING_VALUE", f"{label}: {len(merged)} changed keys (at most {KEYBIND_MAX})", {"field": label})
    in_force = dict(defaults)
    in_force.update(merged)
    hit = keybind_clash(in_force)
    if hit:
        key, a, b = hit
        raise RejectError("SETTING_VALUE", f"{label}: {key} would be the key of two actions ({a} and {b}); swap them or pick another key",
                          {"field": label, "key": key, "actions": [a, b]})
    return merged


def h_attention_open(ctx, payload: dict) -> dict:
    sev = str(payload.get("severity") or "WARN").upper()
    if sev not in ("INFO", "WARN", "ERROR"):
        raise RejectError("SEVERITY", f"severity must be INFO, WARN or ERROR, got {sev!r}")
    details = dict(payload.get("details") or {})
    for k in ("account_id", "ref_type", "ref_id", "auto_resolve"):
        if payload.get(k) is not None:
            details[k] = payload[k]
    aid = ctx.attention(sev, str(payload.get("source") or "api"), str(payload.get("code") or "NOTE"), str(payload.get("message") or ""), details)
    return {"id": aid}


def h_attention_resolve(ctx, payload: dict) -> dict:
    try:
        aid = int(payload.get("id"))
    except (TypeError, ValueError) as e:
        raise RejectError("BAD_REQUEST", "id must be an integer") from e
    row = ctx.conn.execute("SELECT * FROM attention WHERE id=?", (aid,)).fetchone()
    if row is None:
        raise NotFoundReject("NOT_FOUND", f"attention item {aid} does not exist")
    if row["resolved_ts"] is not None:
        return dict(row)
    services = ctx.services
    if row["source"] == "integrity" and row["code"] == "INTEGRITY" and row["ref_type"] == "lifetime":
        held = services is not None and getattr(services, "accounting", None) is not None and services.accounting.is_held(row["ref_id"])
        if held:
            raise ConflictError("PERSISTENT", f"{row['ref_id']} is still held by the integrity check; it resolves itself on the next clean run", {"id": aid})
    if row["code"] in ("PROVIDER_DOWN", "CBOE_REFUSED_UA") or row["source"] in ("provider", "provider_health"):
        providers = getattr(services, "providers", None) if services is not None else None
        name = row["ref_id"] or (row["details_json"] and json.loads(row["details_json"]).get("provider"))
        if providers is not None and getattr(providers, "health", None) is not None and name:
            state = providers.health.get(str(name))
            if state.get("ok") is False or state.get("down_until_ts"):
                raise ConflictError("PERSISTENT", f"provider {name} is still failing; the item resolves itself on the next success", {"id": aid, "provider": name})
    ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE id=?", (ctx.ts, aid))
    return dict(ctx.conn.execute("SELECT * FROM attention WHERE id=?", (aid,)).fetchone())


def _wl_args(payload: dict) -> Tuple[str, str, str]:
    aid, name, iid = str(payload.get("account_id") or ""), str(payload.get("name") or "default"), str(payload.get("instrument_id") or "").upper()
    if not aid or not iid:
        raise RejectError("BAD_REQUEST", "account_id and instrument_id are required")
    return aid, name, iid


def h_watchlist_add(ctx, payload: dict) -> dict:
    aid, name, iid = _wl_args(payload)
    if ctx.conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (aid,)).fetchone() is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
    if ctx.conn.execute("SELECT 1 FROM instruments WHERE instrument_id=?", (iid,)).fetchone() is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid} is not in the master (resolve it first)")
    priv = getattr(ctx.services, "private", None) if ctx.services is not None else None
    tp = getattr(priv, "tradeable_private", None)
    krow = ctx.conn.execute("SELECT kind FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
    if callable(tp) and krow is not None and krow[0] == "PRIVATE":
        ok, why, trade_in = tp(ctx.services, iid, conn=ctx.conn, now=ctx.ts)
        if not ok:
            raise RejectError("NOT_PRIVATE", why, {"instrument_id": iid, "trade_in": trade_in})
    pos = ctx.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM watchlists WHERE account_id=? AND name=?", (aid, name)).fetchone()[0]
    ctx.conn.execute("INSERT OR IGNORE INTO watchlists(account_id, name, instrument_id, position) VALUES (?,?,?,?)", (aid, name, iid, int(pos)))
    return {"account_id": aid, "name": name, "instrument_id": iid, "added": True}


def h_watchlist_remove(ctx, payload: dict) -> dict:
    aid, name, iid = _wl_args(payload)
    cur = ctx.conn.execute("DELETE FROM watchlists WHERE account_id=? AND name=? AND instrument_id=?", (aid, name, iid))
    return {"account_id": aid, "name": name, "instrument_id": iid, "removed": int(cur.rowcount or 0)}


def _money_c(key: str, value, minimum: int = 0) -> int:
    """A decimal USD amount (or integer cents for ``*_c`` callers that send ints) -> cents; refused in plain words."""
    label = SETTING_LABELS.get(key, key)
    if isinstance(value, bool) or value is None or (isinstance(value, str) and not value.strip()):
        raise RejectError("SETTING_VALUE", f"{label}: enter an amount", {"field": label})
    try:
        c = to_c(str(value).strip().replace(",", ""))
    except (ValueError, ArithmeticError, TypeError):
        raise RejectError("SETTING_VALUE", f"{label}: {value!r} is not an amount", {"field": label}) from None
    if c < minimum:
        raise RejectError("SETTING_VALUE", ("reserve must be 0 or more" if "reserve" in key else f"{label} must be {from_c(minimum)} or more"), {"field": label})
    return c


def _margin_overrides(value) -> dict:
    """SR-11: ``{root: {im, mm}}`` (USD per contract; ``im_c`` / ``mm_c`` cents accepted) -> ``{root: {im_c, mm_c}}``; an empty
    entry (both blank) removes the root's override.  IM > 0, 0 < MM <= IM, root in the futures table."""
    from .instruments import FUTURES_SPECS
    if not isinstance(value, dict):
        _bad("futures_margin_overrides", value)
    out = {}
    for root, d in value.items():
        r = str(root).strip().upper()
        if r not in FUTURES_SPECS:
            raise RejectError("SETTING_VALUE", f"futures margins: {root!r} is not a futures root of the table", {"field": "futures margins", "roots": sorted(FUTURES_SPECS)})
        if d is None or (isinstance(d, dict) and all(d.get(k) in (None, "") for k in ("im", "mm", "im_c", "mm_c"))):
            continue
        if not isinstance(d, dict):
            _bad("futures_margin_overrides", value)
        im = int(d["im_c"]) if isinstance(d.get("im_c"), int) and not isinstance(d.get("im_c"), bool) else _money_c("futures_margin_overrides", d.get("im"), 1)
        mm = int(d["mm_c"]) if isinstance(d.get("mm_c"), int) and not isinstance(d.get("mm_c"), bool) else _money_c("futures_margin_overrides", d.get("mm"), 1)
        if im <= 0 or mm <= 0 or mm > im:
            raise RejectError("SETTING_VALUE", f"futures margins {r}: initial must be above 0 and maintenance between 0 and the initial margin", {"field": "futures margins", "root": r})
        out[r] = {"im_c": im, "mm_c": mm}
    return out


def _limit_overrides(value) -> dict:
    """SR-11: ``{key: limit}`` whole contracts > 0: a futures root (spot-month limit) or ``OPTIONS`` (the listed-option position
    limit used when no tier is known); an empty value removes the override."""
    from .instruments import FUTURES_SPECS
    if not isinstance(value, dict):
        _bad("position_limit_overrides", value)
    out = {}
    for key, v in value.items():
        k = str(key).strip().upper()
        if k != OPTION_LIMIT_KEY and k not in FUTURES_SPECS:
            raise RejectError("SETTING_VALUE", f"position limits: {key!r} is not a futures root or {OPTION_LIMIT_KEY}", {"field": "position limits"})
        if v in (None, ""):
            continue
        try:
            n = int(str(v).strip().replace(",", ""))
        except (TypeError, ValueError):
            raise RejectError("SETTING_VALUE", f"position limits {k}: {v!r} is not a whole number of contracts", {"field": "position limits"}) from None
        if n <= 0:
            raise RejectError("SETTING_VALUE", f"position limits {k}: the limit must be 1 contract or more", {"field": "position limits"})
        out[k] = n
    return out


def _session_overrides(value) -> dict:
    """``{"YYYY-MM-DD": {group | "*": "HH:MM" | "CLOSED"}}`` (what SessionTable applies); every part checked."""
    from .tables import SESSIONS
    if not isinstance(value, dict):
        _bad("session_overrides", value)
    out: Dict[str, Dict[str, str]] = {}
    for day, groups in value.items():
        if not _DATE_RE.match(str(day)) or not isinstance(groups, dict):
            raise RejectError("SETTING_VALUE", f"session overrides: {day!r} is not a date (YYYY-MM-DD) with its groups", {"field": "session overrides"})
        try:
            date.fromisoformat(str(day))
        except ValueError:
            raise RejectError("SETTING_VALUE", f"session overrides: {day!r} is not a calendar date", {"field": "session overrides"}) from None
        row = {}
        for g, v in groups.items():
            g = str(g).strip()
            if g != "*" and g not in SESSIONS:
                raise RejectError("SETTING_VALUE", f"session overrides {day}: unknown session group {g!r}", {"field": "session overrides", "groups": sorted(SESSIONS)})
            v = str(v or "").strip().upper()
            if v != "CLOSED" and not _HHMM_RE.match(v):
                raise RejectError("SETTING_VALUE", f"session overrides {day} {g}: {v!r} is not a close time (HH:MM) or CLOSED", {"field": "session overrides"})
            row[g] = v
        if row:
            out[str(day)] = row
    return out


def _setting_value(key: str, value):
    if key == "keybinds":                                      # merged with the stored overrides and checked in h_settings_set
        return _keybind_map(value, SETTING_LABELS[key])
    if key == "treasury_reserve_by_book":
        if not isinstance(value, dict) or not value:
            _bad(key, value)
        return {str(k).upper(): _money_c(key, v, 0) for k, v in value.items()}
    if key == "futures_margin_overrides":
        return _margin_overrides(value)
    if key == "position_limit_overrides":
        return _limit_overrides(value)
    if key == "session_overrides":
        return _session_overrides(value)
    if key == "treasury_reserve_c":
        return _money_c(key, value, 0)
    if key in CENT_SETTINGS:
        return to_c(str(value)) if not isinstance(value, bool) and isinstance(value, (int, str, float, Decimal)) else _bad(key, value)
    if key == "qp_threshold_c":
        if not isinstance(value, dict):
            _bad(key, value)
        return {str(k): to_c(str(v)) for k, v in value.items()}
    if key == "book_defaults":
        if not isinstance(value, dict):
            _bad(key, value)
        out = {}
        for code, d in value.items():
            if not isinstance(d, dict):
                _bad(key, value)
            row = {}
            if d.get("name") is not None:
                row["name"] = str(d["name"])
            cap = d.get("starting_capital_c", d.get("starting_capital"))
            if cap is not None:
                row["starting_capital_c"] = to_c(str(cap))
            out[str(code)] = row
        return out
    if key == "participation_rate":
        v = Decimal(str(value))
        if not (Decimal("0.01") <= v <= Decimal(1)):
            _bad(key, value)
        return str(v)
    if key == "build2_bonds":                                  # WP-10: DEBT execution behind this flag (default off)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "on", "yes"):
            return True
        if text in ("0", "false", "off", "no"):
            return False
        _bad(key, value)
    if key in ("session_overrides", "private_defaults"):
        if not isinstance(value, dict):
            _bad(key, value)
        return value
    return value


def _bad(key, value):
    label = SETTING_LABELS.get(key, key)
    raise RejectError("SETTING_VALUE", f"{label}: {value!r} is not a valid value", {"field": label})


def h_settings_set(ctx, payload: dict) -> dict:
    payload = dict(payload)
    kb_defaults = payload.pop("keybind_defaults", None)        # keys.js defaults: checks a keybinds save, never stored
    if kb_defaults is not None:
        kb_defaults = _keybind_map(kb_defaults, "keyboard defaults")
    before, after = {}, {}
    for key, value in payload.items():
        k = str(key)
        if k == "request_id":
            continue
        if k not in SETTING_KEYS and k + "_c" in SETTING_KEYS:
            k = k + "_c"
        if k not in SETTING_KEYS:
            raise RejectError("SETTING_UNKNOWN", f"{key!r} is not a setting", {"allowed": sorted(SETTING_KEYS)})
        try:
            v = _setting_value(k, value)
        except (ValueError, ArithmeticError) as e:
            label = SETTING_LABELS.get(k, k)
            raise RejectError("SETTING_VALUE", f"{label}: {value!r} is not a valid value", {"field": label}) from e
        if k == "keybinds":                                    # only the owner's overrides; one key never on two actions
            v = _keybinds_merge(ctx.conn, v, kb_defaults)
        elif k == "book_defaults":
            cur = get_setting(ctx.conn, k, {}) or {}
            for code, row in v.items():
                cur[code] = dict(cur.get(code) or {}, **row)
            v = cur
        elif k == "treasury_reserve_by_book":                 # merge: one book's reserve never touches another's (BM-01)
            cur = get_setting(ctx.conn, k, None)
            if not isinstance(cur, dict):                     # first per-book save: every book starts from the old global value
                old = int(get_setting(ctx.conn, "treasury_reserve_c", 0) or 0)
                cur = {r["book_code"]: old for r in ctx.conn.execute("SELECT book_code FROM books ORDER BY sort_order").fetchall()}
            unknown = [c for c in v if ctx.conn.execute("SELECT 1 FROM books WHERE book_code=?", (c,)).fetchone() is None]
            if unknown:
                raise RejectError("BOOK_UNKNOWN", f"no book {unknown[0]!r}", {"field": "Treasury reserve"})
            v = dict(cur, **v)
        before[k] = get_setting(ctx.conn, k)
        set_setting(ctx.conn, k, v)
        after[k] = v
    if not after:
        raise RejectError("NOTHING_TO_UPDATE", "no setting given")
    ctx.conn.execute("INSERT INTO events(command_id, ts, type, account_id, instrument_id, ref_type, ref_id, payload_json) VALUES (?,?,?,?,?,?,?,?)",
                     (ctx.command_id, ctx.ts, "SETTINGS_CHANGED", None, None, "settings", ",".join(sorted(after)), dumps({"before": before, "after": after})))
    return {"before": before, "after": after}


def register(bus) -> None:
    bus.register("attention.open", h_attention_open, light=True)
    bus.register("attention.resolve", h_attention_resolve, light=True)
    bus.register("watchlist.add", h_watchlist_add, light=True)
    bus.register("watchlist.remove", h_watchlist_remove, light=True)
    bus.register("settings.set", h_settings_set, light=True)


# ---- core routes ----------------------------------------------------------------------------------------------------------------
def settings_map(rt) -> dict:
    with rt.db.reader() as conn:
        rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
    out: Dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value_json"])
        except ValueError:
            out[r["key"]] = r["value_json"]
    out["gtc_days"] = config.GTC_DAYS
    out["ecp_floor_c"] = config.ECP_FLOOR_C
    out["tables"] = static_tables()
    rb = out.get("treasury_reserve_by_book")
    if isinstance(rb, dict):                                  # cents per book -> USD strings, like every other amount the API serves
        out["treasury_reserve_by_book"] = {str(k): (_cents(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in rb.items()}
    return out


def static_tables() -> dict:
    """SR-11: the static futures margin table and position limits the overrides replace (assumed values, labelled so)."""
    from .instruments import FUTURES_SPECS
    from .tables import COMPLIANCE_LIMITS
    spot = COMPLIANCE_LIMITS.get("FUTURES_SPOT_MONTH") or {}
    opt = COMPLIANCE_LIMITS.get("OPTION_POSITION_LIMITS") or {}
    return {"futures": [{"root": r, "name": s.name, "group": s.group, "im_c": int(s.im_c), "mm_c": int(s.mm_c), "spot_month_limit": spot.get(r)}
                        for r, s in FUTURES_SPECS.items()],
            "option_limit": {"key": OPTION_LIMIT_KEY, "default": opt.get("default"), "tiers": opt.get("tiers"), "unit": opt.get("unit", "contracts")},
            "note": "assumed values - verify current exchange values; an override applies to new requirements and new checks"}


_COUNT_WORDS = {1: "One book", 2: "Two books", 3: "Three books", 4: "Four books", 5: "Five books"}


def conventions_view(rt) -> dict:
    """``CONVENTIONS`` with the BOOKS / QP / ELIGIBILITY sentences built from the live settings (SHELL-22 / C10): the book names
    and the starting capitals of new lifetimes (``book_defaults``), the qualified-purchaser thresholds per profile and the PB /
    OTC threshold - never a fixed number that Settings can change."""
    conv = dict(CONVENTIONS)
    try:
        with rt.db.reader() as conn:
            books = _books.books(conn)
            defaults = get_setting(conn, "book_defaults", {}) or {}
            thr = _books.pb_otc_threshold_c(conn)
            qps = {b.code: _books.qp_threshold_c(conn, b.profile) for b in books}
    except Exception:   # noqa: BLE001 - the static sentences stand rather than a failing page
        return conv
    if not books:
        return conv
    parts, qp_parts = [], []
    for b in books:
        d = defaults.get(b.code) if isinstance(defaults, dict) else None
        cap = int((d or {}).get("starting_capital_c", b.starting_capital_c) or b.starting_capital_c)
        parts.append(f"{b.name} (USD {fmt_money(cap)})")
        qp = qps[b.code]
        fund_day_one = b.profile == "FUND" and qp > 0 and cap >= qp
        qp_parts.append(f"from the start for the {b.name}" if qp == 0 else
                        (f"from day one for the {b.name} (it starts at or above USD {fmt_money(qp)})" if fund_day_one else
                         f"at USD {fmt_money(qp)} of NAV for the {b.name}"))
    count = _COUNT_WORDS.get(len(books), f"{len(books)} books")
    conv["BOOKS"] = (f"{count}, each its own lifetime with its own Treasury, accounts and ledger: {', '.join(parts)} - the capital a new lifetime starts with "
                     f"(Settings > Books); books never transfer to each other.")
    conv["QP"] = ("Private markets need accredited-investor / qualified-purchaser status (Rule 506, Rule 144 transfer restrictions); here the Private "
                  f"Markets account opens {'; '.join(qp_parts)} (editable in Settings > Books); once open it stays open.")
    conv["ELIGIBILITY"] = ("OTC swaps need Eligible Contract Participant status (CEA 1a(18): more than USD 10M invested) and a dealer's ISDA Master + CSA + "
                           f"Prime Brokerage Agreement; this terminal unlocks prime brokers and OTC when a book's NAV reaches USD {fmt_money(thr)} "
                           "(Settings > Books; the Fund and Institutional books from day one) - dealers' real minimums vary; relationships already open "
                           "stay open if NAV later falls.")
    return conv


def prime_brokers_view(rt) -> List[dict]:
    """C10: ``prime.brokers()`` (each dealer with its seed schedule, ASSUMED and editable once opened), [] when prime is not bound."""
    prime = getattr(rt, "prime", None)
    fn = getattr(prime, "brokers", None) if prime is not None else None
    if not callable(fn):
        try:
            from . import prime as _prime   # the module when rt.prime is a service object
            fn = getattr(_prime, "brokers", None)
        except Exception:   # noqa: BLE001
            fn = None
    if not callable(fn):
        return []
    try:
        rows = list(fn() or [])
    except Exception:   # noqa: BLE001 - the modal says so instead of a failing /api/state
        return []
    for r in rows:
        if isinstance(r, dict):
            r.setdefault("assumed", True)
            r.setdefault("note", "modelled assumptions (not the dealer's real terms); editable in Settings > Prime brokers once the account is open")
    return rows


def thread_alive(th) -> bool:
    """``Thread.is_alive`` that tolerates a subclass shadowing ``threading.Thread._stop`` with an Event (the matcher
    and the quote worker do): on Python 3.12 that raises ``TypeError`` only once the thread has finished, so a
    TypeError means "not alive"."""
    if th is None or not hasattr(th, "is_alive"):
        return False
    try:
        return bool(th.is_alive())
    except TypeError:
        return False


def _worker_health(th, last_tick_ts, errors, last_error, cycle_s: int, now: int, note: Optional[str] = None, progress_ts=None) -> dict:
    """One worker's health: alive, its last tick, stalled (no tick ended AND no progress inside a tick - ``progress_ts``, the quote
    worker's heartbeat, V104R2-BW-10 - for more than 3 cycles), errors in the last hour, the last error."""
    alive = thread_alive(th)
    seen = [int(t) for t in (last_tick_ts, progress_ts) if t is not None]
    last = max(seen) if seen else None
    stalled = bool(alive and last is not None and now - last > 3 * int(cycle_s))
    d = {"alive": alive, "last_tick_ts": last_tick_ts, "stalled": stalled, "errors_1h": int(errors or 0), "last_error": last_error}
    if progress_ts is not None:
        d["last_progress_ts"] = progress_ts
    if note:
        d["note"] = note
    return d


def _db_files(rt) -> dict:
    """SHELL-14: the database's main file and its WAL (the WAL holds recent commits until a checkpoint)."""
    path = getattr(rt.db, "path", None) or getattr(rt, "db_path", None) or ""
    out = {}
    for name, p in (("file", path), ("wal", path + "-wal"), ("shm", path + "-shm")):
        try:
            out[name] = os.path.getsize(p)
        except OSError:
            out[name] = 0
    return out


def _db_size(rt) -> int:
    f = _db_files(rt)
    return int(f.get("file", 0)) + int(f.get("wal", 0))


#: the clock-time jobs of the lifecycle scheduler (their ``when`` in lifecycle.REGISTRY), for "next job" in Data Status (SHELL-14);
#: (job, New York time or a settings key holding one, trading days only)
LIFECYCLE_TIMES = (("HOUSEKEEPING", "00:15", False), ("SETTLE_DUE / ACCRUE_DAILY", "00:30", False), ("DIVIDEND / PC_PAY / BOND_COUPON / SWAP_FIX", "00:40", False),
                   ("MARKS_RECORD", "16:20", True), ("SWAP_VM", "17:05", False), ("COLLATERAL_MARK / OPTION_MARGIN_MARK", "17:10", True),
                   ("SWEEP_OUT", "sweep_out_time_et", False), ("SNAPSHOT", "snapshot_time_et", False))


def lifecycle_times(rt, now: int) -> Dict[str, int]:
    """V104R2-OTC-07: the next run time (epoch) of every clock-time job of ``LIFECYCLE_TIMES`` (the next few days, New York time,
    trading-day jobs on business days only) by job name, plus INTEGRITY_FULL (hourly at :05); the jobs that run on every tick or on
    an event are not in it."""
    from datetime import timedelta
    from .clock import epoch_of, ny_of
    with rt.db.reader() as conn:
        settings = {k: get_setting(conn, k) for k in ("sweep_out_time_et", "snapshot_time_et")}
    today = ny_of(now).date()
    out: Dict[str, int] = {}
    for i in range(5):                                     # a long weekend: the trading-day jobs' next run is a few days out
        d = today + timedelta(days=i)
        for label, when, trading in LIFECYCLE_TIMES:
            hhmm = settings.get(when) or {"sweep_out_time_et": config.SWEEP_OUT_TIME_ET, "snapshot_time_et": config.SNAPSHOT_TIME_ET}.get(when, when)
            if trading and not rt.cal.is_business_day(d):
                continue
            ts = epoch_of(d, str(hhmm))
            if ts <= now:
                continue
            for name in label.split(" / "):
                out.setdefault(name, ts)
    hour = ny_of(now).replace(minute=5, second=0, microsecond=0)
    nxt = hour if int(hour.timestamp()) > now else hour + timedelta(hours=1)
    out.setdefault("INTEGRITY_FULL", int(nxt.timestamp()))
    return out


def _next_due_of(name, conn, now: int):
    """P8B-15: ``(epoch, ref)`` from the job's product module (``lifecycle_eod.next_due_of``), else None."""
    try:
        from .lifecycle_eod import next_due_of
    except ImportError:
        return None
    return next_due_of(str(name or ""), conn, int(now))


def jobs_view(rt, now: int) -> dict:
    """``GET /api/jobs`` (V104R2-OTC-07): the lifecycle scheduler's own view (``LifecycleScheduler.status()``: every job with its kind,
    its schedule in words (``when``, also as ``schedule``), phase, availability, last run / key / status / note) plus, per job, the
    failures recorded in ``job_failures`` (``failures``, ``last_error``) and, for the clock-time jobs, ``next_due_ts`` and ``next_due``
    (epoch, ``lifecycle_times``); a product job without a clock time (OTC_EXPIRY, XCCY_EVENTS, FX_FORWARD_DUE: P8B-15) gets the earliest
    date its open contracts / deals carry (``next_due_ts`` with ``next_due_ref``, e.g. "OTC-000002 maturity 2027-01-28"), and a job with
    nothing scheduled keeps ``next_due`` = its schedule in words.  A scheduler without
    ``status()`` (a stand-in) gets the table-based answer of earlier builds, with the clock times."""
    lc = need(rt, "lifecycle")
    with rt.db.reader() as conn:
        last = {r["job"]: dict(r) for r in conn.execute("SELECT job, MAX(run_ts) AS run_ts, key FROM jobs_done GROUP BY job").fetchall()}
        fails = {r["job"]: dict(r) for r in conn.execute("SELECT job, COUNT(*) AS n, MAX(last_error) AS last_error FROM job_failures GROUP BY job").fetchall()}
    try:
        times = lifecycle_times(rt, now)
    except Exception:   # noqa: BLE001 - a missing calendar / setting: no clock times; the schedule words still show
        times = {}
    status_fn = getattr(lc, "status", None)
    if callable(status_fn):
        view = dict(status_fn() or {})
        rows = []
        with rt.db.reader() as conn:
            for j in view.get("jobs") or []:
                row = dict(j)
                name = row.get("name")
                f = fails.get(name)
                row["failures"] = int(f["n"]) if f else 0
                row["failing"] = bool(f)
                row["last_error"] = f["last_error"] if f else None
                row["schedule"] = row.get("when")
                row["next_due_ts"] = times.get(name)
                if row["next_due_ts"] is None:
                    # P8B-15: a product job with no clock time: the earliest date its open contracts / deals carry (lifecycle_eod.next_due_of)
                    nd = _next_due_of(name, conn, now)
                    if nd is not None:
                        row["next_due_ts"], row["next_due_ref"] = nd
                if row["next_due_ts"] is not None:
                    row["next_due"] = row["next_due_ts"]
                rows.append(row)
        view["jobs"] = rows
        view.setdefault("last_tick_ts", getattr(lc, "last_tick_ts", None))
        return view
    names = [getattr(j, "name", str(j)) for j in getattr(lc, "jobs", getattr(lc, "registry", []))] or sorted(set(last) | set(fails))
    out = []
    for n in names:
        l, f = last.get(n), fails.get(n)
        out.append({"name": n, "last_run_ts": l["run_ts"] if l else None, "last_key": l["key"] if l else None, "next_due": times.get(n), "next_due_ts": times.get(n),
                    "failing": bool(f), "failures": int(f["n"]) if f else 0, "last_error": f["last_error"] if f else None})
    return {"jobs": out, "last_tick_ts": getattr(lc, "last_tick_ts", None)}


def next_lifecycle_job(rt, now: int) -> dict:
    """The next clock-time job of the scheduler: the scheduler's own ``next_due()`` when it has one, else the earliest of
    ``LIFECYCLE_TIMES`` (today / tomorrow, New York time, trading-day jobs on business days) and INTEGRITY_FULL (hourly at :05)."""
    from datetime import timedelta
    from .clock import epoch_of, ny_of
    lc = getattr(rt, "lifecycle", None)
    fn = getattr(lc, "next_due", None)
    if callable(fn):
        got = fn()
        if isinstance(got, dict) and got.get("job"):
            return got
    with rt.db.reader() as conn:
        settings = {k: get_setting(conn, k) for k in ("sweep_out_time_et", "snapshot_time_et")}
    today = ny_of(now).date()
    cands = []
    for d in (today, today + timedelta(days=1)):
        for job, when, trading in LIFECYCLE_TIMES:
            hhmm = settings.get(when) or {"sweep_out_time_et": config.SWEEP_OUT_TIME_ET, "snapshot_time_et": config.SNAPSHOT_TIME_ET}.get(when, when)
            if trading and not rt.cal.is_business_day(d):
                continue
            ts = epoch_of(d, str(hhmm))
            if ts > now:
                cands.append((ts, job))
    hour = ny_of(now).replace(minute=5, second=0, microsecond=0)
    nxt = hour if int(hour.timestamp()) > now else hour + timedelta(hours=1)
    cands.append((int(nxt.timestamp()), "INTEGRITY_FULL"))
    ts, job = min(cands)
    return {"job": job, "at_ts": ts, "source": "the scheduler's documented times"}


def health_view(rt) -> dict:
    """§15 ``GET /api/health`` — never touches the writer lock (readers + in-memory counters only)."""
    now = int(rt.clock.epoch())
    ny = rt.clock.ny()
    try:
        state_version = rt.db.state_version()
    except BusyError:
        state_version = None
    with rt.db.reader() as conn:
        attention_open = int(conn.execute("SELECT COUNT(*) FROM attention WHERE resolved_ts IS NULL").fetchone()[0])
        failing = [f"{r['job']}:{r['key']}" for r in conn.execute("SELECT job, key FROM job_failures ORDER BY last_ts DESC LIMIT 20").fetchall()]
    rep = rt.accounting.last_report() or {}
    holds = rt.accounting.hold_state()
    integrity = {"ok": rep.get("ok", None), "hold": bool(holds), "checked_at": rep.get("checked_at"), "problems": rep.get("problems", []),
                 "failing": rep.get("failing", []), "holds": holds, "mode": rep.get("mode")}
    providers = {}
    ph = getattr(getattr(rt, "providers", None), "health", None)
    if ph is not None:
        providers = ph.snapshot()
    sup = getattr(rt, "supervisor", None)
    errs = sup.errors_1h(now) if sup is not None else {}
    w = getattr(rt, "worker", None)
    ws = w.stats() if w is not None else {}
    m = getattr(rt, "matcher", None)
    lc = getattr(rt, "lifecycle", None)
    workers = {
        "quotes": _worker_health(w, ws.get("last_tick_ts"), errs.get("quotes", ws.get("loop_errors")), ws.get("last_error"), 15, now,
                                 None if w is not None else "no quote worker in this runtime", progress_ts=ws.get("last_progress_ts")),
        "matcher": _worker_health(m, getattr(m, "last_tick_ts", None), errs.get("matcher", getattr(m, "errors", 0)), getattr(m, "last_error", None), 15, now),
        "lifecycle": _worker_health(lc, getattr(lc, "last_tick_ts", None), errs.get("lifecycle", getattr(lc, "errors", 0)), getattr(lc, "last_error", None), 30, now,
                                    None if lc is not None else f"NOT_BUILT {PACKAGES['lifecycle']}"),
        "supervisor": {"alive": bool(sup is not None and sup.alive), "last_tick_ts": getattr(sup, "last_tick_ts", None), "stalled": False,
                       "errors_1h": 0, "last_error": getattr(sup, "last_error", None)},
    }
    lifecycle = {"due": (lc.due_count() if lc is not None and hasattr(lc, "due_count") else None), "failing": failing}
    if lc is None:
        lifecycle["note"] = "no scheduled jobs run in this build"
    else:
        try:
            lifecycle["next_due"] = next_lifecycle_job(rt, now)
        except Exception as e:   # noqa: BLE001 - shown as unknown, never a failing health
            lifecycle["next_due"] = {"job": None, "error": f"{type(e).__name__}: {e}"}
        try:
            busy = list((getattr(lc, "_busy", None) or {}).items())
            lifecycle["blocked"] = [{"job": jk[0], "key": jk[1], "retries": b.get("n"), "since": b.get("since"), "cause": b.get("cause")} for jk, b in sorted(busy)]
        except Exception:   # noqa: BLE001 - a scheduler without the busy map: nothing blocked is reported
            lifecycle["blocked"] = []
    lock = getattr(rt, "lock", None)
    started = int(getattr(rt, "started_ts", now) or now)
    return {"app": APP_ID, "version": VERSION, "schema": SCHEMA_VERSION, "pid": os.getpid(), "uptime_s": max(0, now - started), "started_ts": started,
            "db_path": getattr(rt, "db_path", None) or getattr(rt.db, "path", None), "db_size": _db_size(rt), "db_files": _db_files(rt), "state_version": state_version,
            "offline": bool(getattr(rt, "offline", False)), "ready": bool(getattr(rt, "ready", True)),
            "clock": {"utc": _iso(rt.clock.now()), "ny": ny.strftime("%Y-%m-%d %H:%M:%S ET"), "trading_day": rt.clock.today().isoformat(),
                      "accounting_day": accounting_day(rt.clock.now()).isoformat(), "epoch": now},
            "integrity": integrity, "providers": providers, "workers": workers, "lifecycle": lifecycle, "attention_open": attention_open,
            "calendar": rt.cal.source(), "lock": {"path": getattr(lock, "path", None), "pid": os.getpid() if lock is not None else None},
            "port": getattr(rt, "port", None), "home": getattr(rt, "home", None),
            "modules": dict(getattr(rt, "modules", {})), "refreshers": _refreshers(w)}


def _refreshers(w) -> dict:
    """The quote worker's registered product refreshers (C14) with their schedule and last outcome, for Data Status."""
    fn = getattr(w, "refreshers", None) if w is not None else None
    if not callable(fn):
        return {}
    try:
        return dict(fn() or {})
    except Exception:   # noqa: BLE001 - Data Status shows none rather than failing health
        return {}


def _register_core(router: Router) -> None:
    from . import api_views
    r = router

    def health(req: Request):
        return health_view(req.rt)

    def shutdown(req: Request):
        ev = getattr(req.rt, "stop_event", None)
        if ev is None:
            raise ApiError(503, "NOT_SERVING", "this runtime has no server to stop")
        ev.set()
        if req.rt.log:
            req.rt.log.info("shutdown requested over loopback")
        return {"shutting_down": True}

    def state(req: Request):
        rt = req.rt
        lid = req.lifetime()
        snap = rt.marks.snapshot()
        with rt.db.reader() as conn:
            book_code = _books.book_code_of(lid)
            attention = [dict(x) for x in conn.execute("SELECT * FROM attention WHERE resolved_ts IS NULL ORDER BY ts DESC, id DESC LIMIT 300").fetchall()
                         if attention_in_books(x, {book_code})][:100]
        rep = rt.accounting.last_report() or {}
        return {"app": APP_ID, "version": VERSION, "offline": bool(getattr(rt, "offline", False)), "book": book_code, "lifetime_id": lid,
                "accounts": api_views.account_rows(rt, lid, snap), "books": api_views.books_view(rt, snap, unlock=False), "settings": settings_map(rt),
                "sessions": rt.sessions.states(), "clock": {"utc": _iso(rt.clock.now()), "ny": rt.clock.ny().strftime("%Y-%m-%d %H:%M:%S ET"),
                                                            "trading_day": rt.clock.today().isoformat(), "accounting_day": accounting_day(rt.clock.now()).isoformat(),
                                                            "epoch": req.now},
                "attention": attention, "integrity": {"ok": rep.get("ok"), "hold": bool(rt.accounting.hold_state()), "checked_at": rep.get("checked_at"),
                                                      "failing": rep.get("failing", [])},
                "conventions": conventions_view(rt), "book_profiles": {k: v["label"] for k, v in BOOK_PROFILES.items()},
                "prime_brokers": prime_brokers_view(rt), "unsupported": unsupported_rows(rt), "state_version": rt.db.state_version(),
                "started_ts": getattr(rt, "started_ts", None)}

    def sessions(req: Request):
        return {"sessions": req.rt.sessions.states(), "rules": req.rt.sessions.rules(), "server_time": req.now,
                "overrides": get_setting_safe(req.rt, "session_overrides", {})}

    def calendar(req: Request):
        year = req.q_int("year", req.rt.clock.today().year, 2000, 2100)
        cal = req.rt.cal
        src = cal.source()
        return {"year": year, "holidays": [d.isoformat() for d in cal.holidays(year)], "early_closes": [d.isoformat() for d in sorted(cal.early_closes(year))],
                "source": src.get("file"), "years": src.get("years"), "fallback_years": src.get("fallback_years"), "fallback": year in (src.get("fallback_years") or [])}

    def attention(req: Request):
        """SHELL-03: the selected book's rows (any of its lifetimes: an account of it, a lifetime ref or details.lifetime_id) plus the
        global rows (providers, integrity, curve, workers); ``?all=1`` = every book (consolidated); ``?resolved=1`` adds resolved rows."""
        books = attention_books(req)
        with req.rt.db.reader() as conn:
            if req.q("resolved") in ("1", "true", "yes"):
                rows = conn.execute("SELECT * FROM attention ORDER BY ts DESC, id DESC LIMIT ?", (req.q_int("limit", 200, 1, 2000),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM attention WHERE resolved_ts IS NULL ORDER BY ts DESC, id DESC LIMIT ?", (req.q_int("limit", 500, 1, 2000),)).fetchall()
        return [dict(x) for x in rows if attention_in_books(x, books)]

    def attention_summary_view(req: Request):
        """15.7 item 1 / C3: the "Requires your attention" summary of the selected book (``?book=``; ``?all=1`` = every book,
        consolidated): ``attention.attention_summary`` over ``{lifetime}:ALL``."""
        if req.q("all") in ("1", "true", "yes"):
            scope = "ALL_BOOKS"
        else:
            try:
                scope = req.scope("ALL")
            except ScopeError:
                scope = "ALL_BOOKS"
        return attention_summary(req.rt, scope)

    def attention_resolve(req: Request):
        try:
            aid = int(req.params[0])
        except ValueError:
            raise NotFound(f"attention item {req.params[0]!r} does not exist")
        sel = req.q("book")
        if sel:
            with req.rt.db.reader() as conn:
                row = conn.execute("SELECT * FROM attention WHERE id=?", (aid,)).fetchone()
                owner = attention_book(row) if row is not None else None
                if owner is not None and owner != str(sel).upper():
                    names = {b.code: b.name for b in _books.books(conn)}
                    raise ApiError(409, "BOOK_MISMATCH", f"this item belongs to the {names.get(owner, owner)}; switch to that book to resolve it",
                                   {"id": aid, "book": owner, "selected": str(sel).upper()})
        return run_command(req, "attention.resolve", {"id": aid})

    def jobs(req: Request):
        return jobs_view(req.rt, req.now)

    def jobs_run(req: Request):
        lc = need(req.rt, "lifecycle")
        job = req.b("job")
        if job and hasattr(lc, "jobs") and str(job) not in [getattr(j, "name", None) for j in lc.jobs]:
            raise ApiError(400, "BAD_REQUEST", f"unknown job {job!r}", {"jobs": [getattr(j, "name", str(j)) for j in lc.jobs]})
        if job and hasattr(lc, "run_job"):
            res = lc.run_job(str(job))
        elif job:
            res = lc.tick(only=str(job))                       # one job now ({"job": "SWAP_VM"}), as lifecycle.run_now does
        else:
            res = lc.tick()
        ran = res.get("ran", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
        return {"ran": ran, "result": res if isinstance(res, dict) else None}

    def settings_get(req: Request):
        return settings_map(req.rt)

    def settings_patch(req: Request):
        rt = req.rt
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        book = payload.pop("book", None) or req.q("book")
        # BM-01 / APIUI-04: the Treasury reserve is per book ({treasury_reserve, book} from the Treasury page of that book)
        for key in ("treasury_reserve", "treasury_reserve_c"):
            if key in payload:
                value = payload.pop(key)
                if not book:
                    raise ApiError(400, "BOOK_REQUIRED", "choose the book whose Treasury reserve this is", {"field": "Treasury reserve"})
                code = str(book).upper()
                with rt.db.reader() as conn:
                    if _books.book_of(conn, code) is None:
                        raise ApiError(404, "UNKNOWN_BOOK", f"book {book!r} does not exist")
                payload["treasury_reserve_by_book"] = dict(payload.get("treasury_reserve_by_book") or {}, **{code: value})
        # SR-07: names, starting capitals and thresholds go through book.update_settings (a name renames the book at once)
        book_part = {k: payload.pop(k) for k in list(payload) if k in BOOK_SETTING_KEYS}
        if book_part and "book.update_settings" not in rt.bus.kinds():
            payload.update(book_part)                         # no lifetimes module: stored as plain settings, as before
            book_part = {}
        replayed = False
        results = []
        if book_part:
            rid = req.request_id if not payload else f"{req.request_id}:books"
            res = rt.bus.execute(Command("book.update_settings", book_part, rid, "ui"))
            ok_or_raise(res)
            replayed = replayed or res.replayed
            results.append(res)
        if payload or not book_part:
            rid = req.request_id if not book_part else f"{req.request_id}:settings"
            res = rt.bus.execute(Command("settings.set", payload, rid, "ui")) if "settings.set" in rt.bus.kinds() else run_command(req, "settings.set", payload)
            ok_or_raise(res)
            replayed = replayed or res.replayed
            after = (res.body or {}).get("after", {}) if isinstance(res.body, dict) else {}
            if "session_overrides" in after and hasattr(rt.sessions, "set_overrides"):
                rt.sessions.set_overrides(after["session_overrides"])
        return 200, settings_map(rt), ({"X-Replayed": "1"} if replayed else {})

    def unsupported(req: Request):
        rows = unsupported_rows(req.rt)
        return {"items": rows, "count": len(rows)}

    def lifetime_view(req: Request):
        """The read model of one lifetime (final figures, accounts, the signed agreements with each dealer - PO-16)."""
        mod = need(req.rt, "lifetimes")
        try:
            return mod.history(req.rt, req.params[0])
        except RejectError as e:
            raise NotFound(e.message)

    def static_root(req: Request):
        return r.static("index.html")

    def static_file(req: Request):
        return r.static(req.params[0])

    r.add("GET", r"/api/health", health)
    r.add("POST", r"/api/shutdown", shutdown, mutating=False, loopback=True)
    r.add("GET", r"/api/state", state)
    r.add("GET", r"/api/sessions", sessions)
    r.add("GET", r"/api/calendar", calendar)
    r.add("GET", r"/api/attention", attention)
    r.add("GET", r"/api/attention/summary", attention_summary_view)
    r.add("POST", r"/api/attention/([^/]+)/resolve", attention_resolve)
    r.add("GET", r"/api/jobs", jobs)
    r.add("POST", r"/api/jobs/run", jobs_run, loopback=True)
    r.add("GET", r"/api/settings", settings_get)
    r.add("PATCH", r"/api/settings", settings_patch)
    r.add("GET", r"/api/unsupported", unsupported)
    r.add("GET", r"/api/lifetimes/([A-Z]+[0-9]+)", lifetime_view)
    r.add("GET", r"/", static_root)
    r.add("GET", r"/index\.html", static_root)
    r.add("GET", r"/static/(.+)", static_file)


def attention_books(req: "Request"):
    """SHELL-03: the book codes the attention strip of this request shows (None = every book, ``?all=1``): ``?book=`` else the
    selected book (the Accounting's, else the first open lifetime's)."""
    if req.q("all") in ("1", "true", "yes"):
        return None
    try:
        lid = req.lifetime()
    except ScopeError:
        return None
    return {_books.book_code_of(lid)}


def get_setting_safe(rt, key: str, default=None):
    with rt.db.reader() as conn:
        return get_setting(conn, key, default)


__all__ = ["Router", "Request", "ApiError", "NotFound", "NotBuilt", "encode", "quote_dict", "mark_dict", "error_body", "run_command", "ok_or_raise", "need",
           "lifetime_for", "register", "health_view", "settings_map", "thread_alive", "jobs_view", "lifecycle_times", "UNSUPPORTED", "PACKAGES", "LOOPBACK", "RETRY_MS", "MAX_QUOTE_IDS",
           "OPEN_ORDER_STATUSES", "PLANNED_STATIC", "PLANNED", "NOT_REJECTIONS", "add_refusal_recorder", "remove_refusal_recorder", "refusal_recorders", "unsupported_rows"]
