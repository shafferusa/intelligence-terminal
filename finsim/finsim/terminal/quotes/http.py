"""HTTP plumbing and the User-Agent policy for every provider (TERMINAL_DESIGN.md §8.3).

Conventions
- ``http_get`` is the one network primitive (copied from ``finsim2.data._http_get``, without ``reqctx``): plain GET,
  gzip accepted, a byte cap, ``FetchError`` carrying the HTTP status; messages NEVER contain the URL (so a key in a
  query string can never leak into a log or an attention item).
- ``ua_for(host)``: ``*.gov``, ``*.stlouisfed.org`` (FRED) and ``cdn.finra.org`` get ``GOV_UA`` (the honest terminal
  identity with the owner's e-mail, iron rule 6); ``*.finance.yahoo.com`` gets ``BROWSER_UA`` (a browser-style UA is
  acceptable for Yahoo only, iron rule 7); everything else (Cboe, Frankfurter) gets the honest ``TERMINAL_UA``.
  Never a crumb, never Nasdaq Trader, never Stooq, never a bypass of an anti-bot wall.
- ``RateLimiter`` is a thread-safe token bucket (Yahoo ≤ 2 rps, Cboe ≤ 1 rps, FRED ≤ 1 rps).
- ``CircuitBreaker``: HTTP 429 → the provider is down for 60 s; 5 consecutive failures → down 120 s, doubling on every
  further failure up to 10 min; a success resets.  400/404 answers are per-symbol facts, not provider failures.
- ``ProviderHealth`` keeps the per-provider counters the health endpoint shows and the ``provider_health`` table
  persists (through the light command ``provider_health.update``); a Cboe 403 to the honest UA sets ``refused_ua``.
  ``ProviderHealth.mark_unavailable(provider, reason)`` records a provider that ANSWERED but served no data (FRED's HTML
  "Site Maintenance" page arrives with HTTP 200): the row reads ok=false with the plain reason ("FRED unavailable
  (maintenance)") instead of the success the 200 had counted.
- The Build 2 debt sources (2026-10-21) have their own rows: ``fiscaldata`` (api.fiscaldata.treasury.gov: MSPD and
  auctions), ``treasurydirect`` (FedInvest prices), ``nyfed`` (SOFR fallback), ``ecb`` (deposit rate fallback, euro-area
  AAA par curve), ``mof`` (JGB yields).  ``FetchError.kind`` names a failure class the caller acts on (``maintenance``).
- Nothing here sleeps while holding any lock; nothing here touches the database.
"""
from __future__ import annotations

import gzip
import threading
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional

from ..config import BROWSER_UA, GOV_UA, MAX_BYTES, TERMINAL_UA

PROVIDERS = ("yahoo", "cboe", "treasury", "fred", "finra", "frankfurter", "fiscaldata", "treasurydirect", "nyfed", "ecb", "mof")
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
DOWN_429_S = 60
DOWN_FAIL_S = 120
DOWN_MAX_S = 600
FAILS_TO_OPEN = 5
GOV_HOSTS = ("cdn.finra.org", "stlouisfed.org")


class FetchError(Exception):
    """A download failed.  ``status`` is the HTTP status when there was one (else None); ``body`` the first MiB of
    an error body (untrusted bytes); ``retryable`` says whether a retry with backoff makes sense (429 / 5xx /
    network errors) — 4xx other than 429 are final for that request."""

    def __init__(self, message: str, status: Optional[int] = None, body: bytes = b"", retryable: Optional[bool] = None, kind: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body or b""
        if retryable is None:
            retryable = status is None or status in RETRY_STATUS
        self.retryable = bool(retryable)
        self.kind = kind                  # "maintenance": the host answered with its maintenance page (no data, not a network error)


def http_get(url: str, ua: str, timeout: float = 20.0, max_bytes: int = MAX_BYTES, accept: str = "application/json") -> bytes:
    """GET ``url`` with the given User-Agent and return the (decompressed) body.  Raises ``FetchError``; the message
    names the status or the error class only, never the URL."""
    hdrs = {"Accept-Encoding": "gzip", "User-Agent": ua, "Accept": accept}
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 — https URLs built from constants
            raw = resp.read(max_bytes + 1)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:   # the body may carry a JSON error message (Yahoo's chart.error)
        body = b""
        try:
            body = exc.read(1024 * 1024) or b""
            if (exc.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
        except Exception:   # noqa: BLE001 — best effort only
            body = b""
        raise FetchError(f"HTTP {exc.code}", status=int(exc.code), body=body) from None
    except urllib.error.URLError as exc:
        raise FetchError(f"network error: {type(exc.reason).__name__}") from None
    except (TimeoutError, OSError) as exc:
        raise FetchError(f"network error: {type(exc).__name__}") from None
    if len(raw) > max_bytes:
        raise FetchError("response too large", retryable=False)
    if enc == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            raise FetchError("bad gzip body") from None
    return raw


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def ua_for(host: str) -> str:
    """The User-Agent policy per host (§8.3).  ``host`` may be a bare host or a full URL."""
    h = host_of(host) if "://" in host else host.lower().strip()
    if h.endswith(".gov") or h == "gov":
        return GOV_UA
    for g in GOV_HOSTS:
        if h == g or h.endswith("." + g):
            return GOV_UA
    if h.endswith(".finance.yahoo.com") or h == "finance.yahoo.com":
        return BROWSER_UA
    return TERMINAL_UA


Getter = Callable[..., bytes]


def get_with_policy(url: str, timeout: float = 20.0, accept: str = "application/json", getter: Optional[Getter] = None, max_bytes: int = MAX_BYTES) -> bytes:
    """``http_get`` with the UA chosen by ``ua_for(url)``.  ``getter`` (tests) replaces the network call and receives
    ``(url, ua, timeout, accept)``."""
    ua = ua_for(url)
    if getter is not None:
        return getter(url, ua, timeout, accept)
    return http_get(url, ua, timeout=timeout, max_bytes=max_bytes, accept=accept)


class RateLimiter:
    """Token bucket: ``rps`` tokens per second, burst of one second.  ``wait()`` blocks until a token is available;
    thread-safe; ``sleep`` and ``now`` are injectable for tests."""

    def __init__(self, rps: float, sleep: Callable[[float], None] = _time.sleep, now: Callable[[], float] = _time.monotonic):
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.rps = float(rps)
        self._capacity = max(1.0, self.rps)
        self._tokens = self._capacity
        self._last = now()
        self._sleep = sleep
        self._now = now
        self._lock = threading.Lock()
        self.waits = 0

    def _refill(self) -> None:
        t = self._now()
        self._tokens = min(self._capacity, self._tokens + (t - self._last) * self.rps)
        self._last = t

    def try_acquire(self) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def wait(self) -> None:
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                need = (1.0 - self._tokens) / self.rps
            self.waits += 1
            self._sleep(max(0.01, need))


class CircuitBreaker:
    """Per-provider open/closed state.  ``now`` is an epoch-seconds callable (the terminal clock in production, a
    FakeClock in tests)."""

    def __init__(self, now: Callable[[], int]):
        self._now = now
        self._lock = threading.Lock()
        self._fails: Dict[str, int] = {}
        self._down_until: Dict[str, int] = {}
        self._penalty: Dict[str, int] = {}

    def allow(self, provider: str) -> bool:
        with self._lock:
            until = self._down_until.get(provider)
            return until is None or int(self._now()) >= until

    def down_until(self, provider: str) -> Optional[int]:
        with self._lock:
            until = self._down_until.get(provider)
            return until if until is not None and int(self._now()) < until else None

    def consecutive_failures(self, provider: str) -> int:
        with self._lock:
            return self._fails.get(provider, 0)

    def record(self, provider: str, ok: bool, status: Optional[int] = None) -> None:
        now = int(self._now())
        with self._lock:
            if ok:
                self._fails[provider] = 0
                self._down_until.pop(provider, None)
                self._penalty.pop(provider, None)
                return
            if status in (400, 404):      # a per-symbol fact (unknown symbol), the provider itself is up
                return
            if status == 429:
                self._down_until[provider] = max(self._down_until.get(provider, 0), now + DOWN_429_S)
                return
            n = self._fails.get(provider, 0) + 1
            self._fails[provider] = n
            if n >= FAILS_TO_OPEN:
                pen = self._penalty.get(provider)
                pen = DOWN_FAIL_S if pen is None else min(DOWN_MAX_S, pen * 2)
                self._penalty[provider] = pen
                self._down_until[provider] = now + pen


class ProviderHealth:
    """Counters per provider for ``/api/health.providers`` and the ``provider_health`` table."""

    def __init__(self, now: Callable[[], int], breaker: Optional[CircuitBreaker] = None, day: Optional[Callable[[], str]] = None):
        self._now = now
        self.breaker = breaker or CircuitBreaker(now)
        self._day = day
        self._lock = threading.Lock()
        self._rows: Dict[str, dict] = {p: self._blank(p) for p in PROVIDERS}

    @staticmethod
    def _blank(provider: str) -> dict:
        return {"provider": provider, "ok": None, "last_ok_ts": None, "last_err_ts": None, "last_error": None,
                "consecutive_errors": 0, "requests_today": 0, "day": None, "down_until_ts": None, "refused_ua": False}

    def _today(self) -> Optional[str]:
        return self._day() if self._day is not None else None

    def allow(self, provider: str) -> bool:
        return self.breaker.allow(provider)

    def record(self, provider: str, ok: bool, status: Optional[int] = None, error: Optional[str] = None, count: bool = True) -> None:
        now = int(self._now())
        self.breaker.record(provider, ok, status)
        with self._lock:
            row = self._rows.setdefault(provider, self._blank(provider))
            day = self._today()
            if day is not None and row["day"] != day:
                row["day"], row["requests_today"] = day, 0
            if count:
                row["requests_today"] += 1
            if ok:
                row.update(ok=True, last_ok_ts=now, consecutive_errors=0, down_until_ts=None, last_error=None)
                if status != 403:
                    row["refused_ua"] = False
            else:
                if status in (400, 404):          # unknown symbol: the provider answered
                    row.update(ok=True, last_ok_ts=now)
                    return
                row.update(ok=False, last_err_ts=now, last_error=(error or f"HTTP {status}" if status else error or "error")[:200])
                row["consecutive_errors"] = self.breaker.consecutive_failures(provider) if status != 429 else row["consecutive_errors"] + 1
                row["down_until_ts"] = self.breaker.down_until(provider)
                if status == 403:
                    row["refused_ua"] = True

    def mark_unavailable(self, provider: str, reason: str, status: Optional[int] = None) -> None:
        """The provider answered (often HTTP 200) but served no data - FRED's "Site Maintenance" page: count it as a failure
        with the plain ``reason`` so Data Status / the attention strip say so (the breaker counts it like any failure)."""
        self.record(provider, False, status, reason, count=False)      # the request itself was already counted

    def set_refused_ua(self, provider: str, refused: bool) -> None:
        with self._lock:
            self._rows.setdefault(provider, self._blank(provider))["refused_ua"] = bool(refused)

    def refused_ua(self, provider: str) -> bool:
        with self._lock:
            return bool(self._rows.get(provider, {}).get("refused_ua"))

    def get(self, provider: str) -> dict:
        with self._lock:
            return dict(self._rows.get(provider) or self._blank(provider))

    def snapshot(self) -> dict:
        with self._lock:
            out = {}
            for p, r in self._rows.items():
                d = dict(r)
                d["down_until_ts"] = self.breaker.down_until(p)
                d["down"] = d["down_until_ts"] is not None
                out[p] = d
            return out

    def load(self, rows) -> None:
        """Restore persisted rows (counters only; the breaker starts closed after a restart)."""
        with self._lock:
            for r in rows:
                p = r["provider"]
                row = self._rows.setdefault(p, self._blank(p))
                for k in ("last_ok_ts", "last_err_ts", "last_error", "consecutive_errors", "requests_today", "day", "down_until_ts"):
                    if k in r.keys():
                        row[k] = r[k]
                row["ok"] = None if row["last_ok_ts"] is None and row["last_err_ts"] is None else (row["last_err_ts"] or 0) <= (row["last_ok_ts"] or 0)


def retrying(fn: Callable[[], bytes], attempts: int = 3, backoff: float = 1.0, sleep: Callable[[float], None] = _time.sleep) -> bytes:
    """Call ``fn`` up to ``attempts`` times, sleeping ``backoff``, ``2×backoff``, … between retryable failures
    (429 / 5xx / network); a non-retryable ``FetchError`` propagates at once."""
    delay = backoff
    last: Optional[FetchError] = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except FetchError as e:
            last = e
            if not e.retryable or i == attempts - 1:
                raise
            sleep(delay)
            delay *= 2
    assert last is not None
    raise last
