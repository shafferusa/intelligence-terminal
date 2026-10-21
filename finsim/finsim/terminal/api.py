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
from .tables import BOOK_PROFILES, CONVENTIONS
from .units import from_c, to_c

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
            "private": "WP-19", "performance": "WP-20", "private_credit": "WP-21", "news": "WP-22", "lifetimes": "WP-23", "strategies": "WP-24",
            "glossary": "WP-25", "swaps_trs": "WP-26"}

# §20 — the unsupported / limited list, served by GET /api/unsupported (rendered in Settings and in search)
UNSUPPORTED = [
    {"item": "Private equity deal flow (buyouts), investment banking, clients / institutions / investors, careers / missions", "state": "NOT_IN_THIS_BUILD",
     "wording": "NOT IN THIS BUILD", "where": "nowhere in the terminal (TERMINAL_DECISIONS s5)"},
    {"item": "MBS / structured, exotic OTC (variance, vol, dividend, inflation swaps, barriers), commodity physicals, crypto", "state": "NOT_IN_THIS_BUILD",
     "wording": "grey rows wherever a list would show them; search says 'not in this build'", "where": "Markets lists, search"},
    {"item": "Private credit (direct loans, OID, amendment fees, quarterly payments)", "state": "SUPPORTED", "wording": "computed marks with their inputs",
     "where": "Credit page section (WP-21)"},
    {"item": "Private markets (secondaries at negotiated prices, last-round marks)", "state": "SUPPORTED", "wording": "in eligible lifetimes; 'valuation only' without a sourced price",
     "where": "Private Markets account page (WP-19); QP sentence when locked"},
    {"item": "PB OTC swaps: IRS, TRS, cross-currency", "state": "SUPPORTED", "wording": "computed marks, daily VM and resets; dealt at the model par rate +/- the PB's half-width",
     "where": "PB page OTC tab (WP-17 / WP-26)"},
    {"item": "Trailing stops; all-or-none option packages; the 60 strategy templates", "state": "SUPPORTED", "wording": "ticket type TRAIL; hedge panel Strategies tab",
     "where": "ticket, hedge panel"},
    {"item": "Bonds (Treasury benchmarks, curated corporates): execution", "state": "LIMITED", "wording": "model price, not executable in this build",
     "where": "Debt Instruments page (curve + list)"},
    {"item": "Foreign listings, foreign bonds", "state": "NOT_IN_THIS_BUILD", "wording": "resolve refuses non-US exchanges", "where": "Foreign Markets & FX page shows FX spot only"},
    {"item": "FX forwards, FX options", "state": "NOT_IN_THIS_BUILD", "wording": "fx.forward -> 400", "where": "hedge panel label for FX primaries"},
    {"item": "Repo / reverse repo, securities lending out of inventory", "state": "NOT_IN_THIS_BUILD", "wording": "Credit page sections present with the label",
     "where": "Credit Instruments page"},
    {"item": "PB OTC options and forwards (FX / equity options, FRAs, caps)", "state": "NOT_IN_THIS_BUILD", "wording": "/api/otc/preview -> 400 NOT_IN_THIS_BUILD; swaps are live",
     "where": "PB page OTC tab"},
    {"item": "Splits applied to lots", "state": "NOT_IN_THIS_BUILD", "wording": "a Yahoo split event raises an Attention WARN 'manual review'", "where": "positions row note"},
    {"item": "Recall notices from lender data", "state": "LIMITED", "wording": "INFO notice from the FINRA ratio heuristic only; no buy-ins ever", "where": "Attention INFO"},
    {"item": "Early assignment of short options", "state": "NOT_SIMULATED", "wording": "not simulated", "where": "glossary OCC sentence"},
    {"item": "Options on futures", "state": "NOT_IN_THIS_BUILD", "wording": "NOT IN THIS BUILD", "where": "hedge panel label for futures primaries"},
    {"item": "Borrow availability / tiers", "state": "HEURISTIC", "wording": "estimated from FINRA + ADV, disclosed", "where": "ticket borrow block (BORROW_AVAIL)"},
    {"item": "Futures initial margins, CME holiday calendar, settlement price", "state": "APPROXIMATED", "wording": "static table / NYSE approximation / observed print",
     "where": "sentences FUT_MARGIN, CME_CAL, FUT_SETTLE"},
    {"item": "Live quotes", "state": "NONE", "wording": "everything is delayed, close or computed; 'live' is reserved", "where": "freshness on every price"},
    {"item": "Cboe NBBO for equity fills", "state": "OPPORTUNISTIC", "wording": "only when a fresh chain is already cached; otherwise Yahoo last", "where": "preview price_basis"},
    {"item": "Executions when Yahoo / Cboe are down", "state": "PAUSED", "wording": "paused (stale) - no second equity source", "where": "Attention WARN, ticket blocker"},
    {"item": "Treasury interest on non-USD cash", "state": "ZERO", "wording": "0 %", "where": "sentence CASH_INT"},
    {"item": "Policy rates for JPY/CHF/CAD/AUD loans", "state": "MONTHLY_PROXY", "wording": "monthly OECD series (as-of month shown)", "where": "loan form note"},
    {"item": "Risk and compliance", "state": "INFORM_ONLY", "wording": "warnings and notices only; not a compliance system", "where": "'warnings only' header on the Risk page"},
    {"item": "Phone / LAN access, access keys", "state": "NOT_IN_THIS_BUILD", "wording": "loopback only", "where": "-"},
    {"item": "Performance statistics tab", "state": "SUPPORTED", "wording": "per lifetime; needs >= 2 daily snapshots", "where": "Portfolio -> Performance"},
    {"item": "Private marks under scenario shocks; VaR", "state": "NOT_APPLIED", "wording": "private marks are not shocked; no VaR in this build", "where": "Risk table note"},
    {"item": "OTC dealt rates", "state": "MODELLED", "wording": "model par rate +/- the PB's half-width; IM static (DV01 x 50 bp / 10 % TRS / 2 % XCCY)",
     "where": "preview 'cost of dealing'; sentence SWAP_MARK"},
]


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
        from . import api_views, api_products
        _register_core(self)
        api_views.register_routes(self)
        api_products.register_routes(self)

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
def run_command(req: Request, kind: str, payload: dict, actor: str = "ui") -> Result:
    """Execute one command for this request; a missing handler is 501 NOT_BUILT (the owning package is named)."""
    bus = req.rt.bus
    if kind not in bus.kinds():
        owner = {"order": "orders", "credit.borrow": "lending", "credit.return": "lending", "credit.loan": "credit", "credit.pb": "credit", "fx": "fx",
                 "options": "options", "otc": "swaps", "swap": "swaps", "private": "private", "pc": "private_credit", "book.reset": "lifetimes",
                 "book.onboard": "lifetimes", "account.open_pb": "prime", "account.update_schedule": "prime", "lifecycle": "lifecycle"}
        attr = next((v for k, v in owner.items() if kind.startswith(k)), kind.split(".")[0])
        raise NotBuilt(PACKAGES.get(attr, "?"), attr, f"command {kind} has no handler in this build")
    return bus.execute(Command(kind, payload, req.request_id or "", actor))


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
                "pb_otc_threshold_c", "qp_threshold_c", "book_defaults", "private_defaults", "news_enabled"}
CENT_SETTINGS = {"treasury_reserve_c", "sweep_confirm_threshold_c", "pb_otc_threshold_c"}


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
    pos = ctx.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM watchlists WHERE account_id=? AND name=?", (aid, name)).fetchone()[0]
    ctx.conn.execute("INSERT OR IGNORE INTO watchlists(account_id, name, instrument_id, position) VALUES (?,?,?,?)", (aid, name, iid, int(pos)))
    return {"account_id": aid, "name": name, "instrument_id": iid, "added": True}


def h_watchlist_remove(ctx, payload: dict) -> dict:
    aid, name, iid = _wl_args(payload)
    cur = ctx.conn.execute("DELETE FROM watchlists WHERE account_id=? AND name=? AND instrument_id=?", (aid, name, iid))
    return {"account_id": aid, "name": name, "instrument_id": iid, "removed": int(cur.rowcount or 0)}


def _setting_value(key: str, value):
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
    if key == "news_enabled":
        return bool(value)
    if key in ("session_overrides", "private_defaults"):
        if not isinstance(value, dict):
            _bad(key, value)
        return value
    return value


def _bad(key, value):
    raise RejectError("SETTING_VALUE", f"{key}: {value!r} is not a valid value")


def h_settings_set(ctx, payload: dict) -> dict:
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
            raise RejectError("SETTING_VALUE", f"{k}: {value!r} is not a valid value") from e
        if k == "book_defaults":
            cur = get_setting(ctx.conn, k, {}) or {}
            for code, row in v.items():
                cur[code] = dict(cur.get(code) or {}, **row)
            v = cur
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
    return out


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


def _worker_health(th, last_tick_ts, errors, last_error, cycle_s: int, now: int, note: Optional[str] = None) -> dict:
    alive = thread_alive(th)
    stalled = bool(alive and last_tick_ts is not None and now - int(last_tick_ts) > 3 * int(cycle_s))
    d = {"alive": alive, "last_tick_ts": last_tick_ts, "stalled": stalled, "errors_1h": int(errors or 0), "last_error": last_error}
    if note:
        d["note"] = note
    return d


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
                                 None if w is not None else "no quote worker in this runtime"),
        "matcher": _worker_health(m, getattr(m, "last_tick_ts", None), errs.get("matcher", getattr(m, "errors", 0)), getattr(m, "last_error", None), 15, now),
        "lifecycle": _worker_health(lc, getattr(lc, "last_tick_ts", None), errs.get("lifecycle", getattr(lc, "errors", 0)), getattr(lc, "last_error", None), 30, now,
                                    None if lc is not None else f"NOT_BUILT {PACKAGES['lifecycle']}"),
        "supervisor": {"alive": bool(sup is not None and sup.alive), "last_tick_ts": getattr(sup, "last_tick_ts", None), "stalled": False,
                       "errors_1h": 0, "last_error": getattr(sup, "last_error", None)},
    }
    lifecycle = {"due": (lc.due_count() if lc is not None and hasattr(lc, "due_count") else None), "failing": failing}
    if lc is None:
        lifecycle["note"] = f"NOT_BUILT {PACKAGES['lifecycle']}: no scheduled jobs run in this build"
    news = getattr(rt, "news", None)
    lock = getattr(rt, "lock", None)
    started = int(getattr(rt, "started_ts", now) or now)
    return {"app": APP_ID, "version": VERSION, "schema": SCHEMA_VERSION, "pid": os.getpid(), "uptime_s": max(0, now - started), "started_ts": started,
            "db_path": getattr(rt, "db_path", None) or getattr(rt.db, "path", None), "db_size": rt.db.size(), "state_version": state_version,
            "offline": bool(getattr(rt, "offline", False)), "ready": bool(getattr(rt, "ready", True)),
            "clock": {"utc": _iso(rt.clock.now()), "ny": ny.strftime("%Y-%m-%d %H:%M:%S ET"), "trading_day": rt.clock.today().isoformat(),
                      "accounting_day": accounting_day(rt.clock.now()).isoformat(), "epoch": now},
            "integrity": integrity, "providers": providers, "workers": workers, "lifecycle": lifecycle, "attention_open": attention_open,
            "calendar": rt.cal.source(), "lock": {"path": getattr(lock, "path", None), "pid": os.getpid() if lock is not None else None},
            "news": (news.health() if news is not None else None), "port": getattr(rt, "port", None), "home": getattr(rt, "home", None),
            "modules": dict(getattr(rt, "modules", {}))}


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
            attention = [dict(x) for x in conn.execute("SELECT * FROM attention WHERE resolved_ts IS NULL ORDER BY ts DESC, id DESC LIMIT 100").fetchall()]
            book_code = _books.book_code_of(lid)
        rep = rt.accounting.last_report() or {}
        return {"app": APP_ID, "version": VERSION, "offline": bool(getattr(rt, "offline", False)), "book": book_code, "lifetime_id": lid,
                "accounts": api_views.account_rows(rt, lid, snap), "books": api_views.books_view(rt, snap, unlock=False), "settings": settings_map(rt),
                "sessions": rt.sessions.states(), "clock": {"utc": _iso(rt.clock.now()), "ny": rt.clock.ny().strftime("%Y-%m-%d %H:%M:%S ET"),
                                                            "trading_day": rt.clock.today().isoformat(), "accounting_day": accounting_day(rt.clock.now()).isoformat(),
                                                            "epoch": req.now},
                "attention": attention, "integrity": {"ok": rep.get("ok"), "hold": bool(rt.accounting.hold_state()), "checked_at": rep.get("checked_at"),
                                                      "failing": rep.get("failing", [])},
                "conventions": dict(CONVENTIONS), "book_profiles": {k: v["label"] for k, v in BOOK_PROFILES.items()},
                "unsupported": UNSUPPORTED, "state_version": rt.db.state_version(), "started_ts": getattr(rt, "started_ts", None)}

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
        with req.rt.db.reader() as conn:
            if req.q("all") in ("1", "true", "yes"):
                rows = conn.execute("SELECT * FROM attention ORDER BY ts DESC, id DESC LIMIT ?", (req.q_int("limit", 200, 1, 2000),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM attention WHERE resolved_ts IS NULL ORDER BY ts DESC, id DESC LIMIT ?", (req.q_int("limit", 500, 1, 2000),)).fetchall()
        return [dict(x) for x in rows]

    def attention_resolve(req: Request):
        try:
            aid = int(req.params[0])
        except ValueError:
            raise NotFound(f"attention item {req.params[0]!r} does not exist")
        return run_command(req, "attention.resolve", {"id": aid})

    def jobs(req: Request):
        lc = need(req.rt, "lifecycle")
        with req.rt.db.reader() as conn:
            last = {r["job"]: dict(r) for r in conn.execute("SELECT job, MAX(run_ts) AS run_ts, key FROM jobs_done GROUP BY job").fetchall()}
            fails = {r["job"]: dict(r) for r in conn.execute("SELECT job, COUNT(*) AS n, MAX(last_error) AS last_error FROM job_failures GROUP BY job").fetchall()}
        names = [getattr(j, "name", str(j)) for j in getattr(lc, "jobs", getattr(lc, "registry", []))] or sorted(set(last) | set(fails))
        out = []
        for n in names:
            l, f = last.get(n), fails.get(n)
            out.append({"name": n, "last_run_ts": l["run_ts"] if l else None, "last_key": l["key"] if l else None,
                        "next_due": getattr(lc, "next_due", {}).get(n) if isinstance(getattr(lc, "next_due", None), dict) else None,
                        "failing": bool(f), "failures": int(f["n"]) if f else 0, "last_error": f["last_error"] if f else None})
        return {"jobs": out, "last_tick_ts": getattr(lc, "last_tick_ts", None)}

    def jobs_run(req: Request):
        lc = need(req.rt, "lifecycle")
        job = req.b("job")
        res = lc.tick() if not job or not hasattr(lc, "run_job") else lc.run_job(str(job))
        ran = res.get("ran", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
        return {"ran": ran, "result": res if isinstance(res, dict) else None}

    def settings_get(req: Request):
        return settings_map(req.rt)

    def settings_patch(req: Request):
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        res = run_command(req, "settings.set", payload)
        ok_or_raise(res)
        if "session_overrides" in res.body.get("after", {}) and hasattr(req.rt.sessions, "set_overrides"):
            req.rt.sessions.set_overrides(res.body["after"]["session_overrides"])
        return 200, settings_map(req.rt), ({"X-Replayed": "1"} if res.replayed else {})

    def unsupported(req: Request):
        return {"items": UNSUPPORTED, "count": len(UNSUPPORTED)}

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
    r.add("POST", r"/api/attention/([^/]+)/resolve", attention_resolve)
    r.add("GET", r"/api/jobs", jobs)
    r.add("POST", r"/api/jobs/run", jobs_run, loopback=True)
    r.add("GET", r"/api/settings", settings_get)
    r.add("PATCH", r"/api/settings", settings_patch)
    r.add("GET", r"/api/unsupported", unsupported)
    r.add("GET", r"/", static_root)
    r.add("GET", r"/index\.html", static_root)
    r.add("GET", r"/static/(.+)", static_file)


def get_setting_safe(rt, key: str, default=None):
    with rt.db.reader() as conn:
        return get_setting(conn, key, default)


__all__ = ["Router", "Request", "ApiError", "NotFound", "NotBuilt", "encode", "quote_dict", "mark_dict", "error_body", "run_command", "ok_or_raise", "need",
           "lifetime_for", "register", "health_view", "settings_map", "thread_alive", "UNSUPPORTED", "PACKAGES", "LOOPBACK", "RETRY_MS", "MAX_QUOTE_IDS",
           "OPEN_ORDER_STATUSES"]
