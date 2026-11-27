"""FinSim2 data layer: research universe, SQLite research store and the downloaders (Yahoo, FRED, SEC).

Standard library only. Everything fetched from the network is untrusted data: it is parsed defensively
(``json.loads`` / ``csv``, explicit type checks, finite-number checks) and never evaluated.

Shared HTTP plumbing lives here so every downloader behaves the same way (timeouts, gzip, size cap).
Downloaders call :func:`http_get` through a module-level ``_get`` wrapper so tests can patch them.
"""
from __future__ import annotations

import gzip
import math
import urllib.error
import urllib.request

# Every .gov request (SEC, FRED) identifies itself with this header. Never spoof a browser UA on .gov.
GOV_UA = "LoganTerminal/1.0 (loganshaffer87@gmail.com)"
# A browser-style UA is acceptable for Yahoo Finance only.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MAX_BYTES = 64 * 1024 * 1024  # refuse absurd payloads

_PLACEHOLDERS = {"your-key-here", "your_key_here", "yourkey", "changeme", "xxx", "key", "none", "null", "..."}


def env_key(name: str):
    """An API key from the environment, or None when it is unset or still a placeholder (``setx X "your-key-here"``).
    The value is never logged or put in an error message."""
    import os
    v = (os.environ.get(name) or "").strip().strip('"').strip("'")
    if not v or v.lower() in _PLACEHOLDERS or v.startswith("<") or "your" in v.lower():
        return None
    return v


def placeholder_key(name: str) -> bool:
    """True when the variable is set but to a placeholder (so the message can say so)."""
    import os
    return bool((os.environ.get(name) or "").strip()) and env_key(name) is None


class FetchError(Exception):
    """A download failed. ``status`` is the HTTP status when there was one (else None).

    Messages never contain request URLs, so API keys in query strings cannot leak into logs.
    """

    def __init__(self, message, status=None, body=b""):
        super().__init__(message)
        self.status = status
        self.body = body


def http_get(url: str, headers: dict | None = None, timeout: float = 30.0) -> bytes:
    """GET ``url`` and return the (decompressed) body. Raises :class:`FetchError`."""
    hdrs = {"Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:  # body may carry a JSON error message
        body = b""
        try:
            body = exc.read(1024 * 1024) or b""
            if (exc.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
        except Exception:  # noqa: BLE001 - best effort only
            body = b""
        raise FetchError(f"HTTP {exc.code}", status=exc.code, body=body) from None
    except urllib.error.URLError as exc:
        raise FetchError(f"network error: {type(exc.reason).__name__}") from None
    except (TimeoutError, OSError) as exc:
        raise FetchError(f"network error: {type(exc).__name__}") from None
    if len(raw) > MAX_BYTES:
        raise FetchError("response too large")
    if enc == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            raise FetchError("bad gzip body") from None
    return raw


def num(x):
    """Return ``x`` as a finite float, or None (untrusted input: strings, NaN, inf, bools are rejected)."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    elif isinstance(x, str):
        try:
            v = float(x.strip())
        except ValueError:
            return None
    else:
        return None
    return v if math.isfinite(v) else None


__all__ = ["GOV_UA", "BROWSER_UA", "FetchError", "http_get", "num"]
