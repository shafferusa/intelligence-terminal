"""Regression guard for the Yahoo option-chain crumb handshake header.

Offline (the regression guard):      python test_option_crumb.py
Offline + live proof (network):      python test_option_crumb.py --live

# ---------------------------------------------------------------------------
# MEASURED EVIDENCE -- 2026-09-20, from this machine, cookie obtained first
# from https://fc.yahoo.com, then GET https://query{1,2}.finance.yahoo.com/
# v1/test/getcrumb on the SAME session, varying only the Accept header:
#
#   Accept: application/json   query1  ->  HTTP 406   body {"finance":{"result":null,
#                                                     "error":{"code":"Not Acceptable"...
#   Accept: application/json   query2  ->  HTTP 406   same body
#   Accept: */*                query1  ->  HTTP 200   11-character crumb
#   Accept: */*                query2  ->  HTTP 200   11-character crumb
#
# One header, two outcomes. With `application/json` the handshake never
# completes, so option_data returns an UNAVAILABLE chain for EVERY underlying
# and the hedge engine reports strikes as theoretical targets -- a content
# negotiation failure that is indistinguishable, downstream, from Yahoo simply
# having no options for the name. Rerun with --live to re-measure.
# ---------------------------------------------------------------------------

The offline half is the guard that has to survive: it drives the real
`_authenticated_session` against a fake transport that reproduces the measured
406/200 split, so reverting the header fails the test on this machine with no
network at all. The live half is proof, not a gate; it is skipped by default
because a test that needs Yahoo to be up is not a regression test.

This is infrastructure. It touches no factor weight, no scaling constant, no
sector formula, no classification band, no calibration and no hedge
arithmetic -- only whether the bytes arrive.
"""

from __future__ import annotations

import os
import sys
import types

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import option_data as OD
from market_data import BROWSER_UA, HTTP_TIMEOUT

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


#: What Yahoo actually answers, measured 2026-09-20. The fake transport below
#: reproduces exactly this rule and nothing else.
GOOD_ACCEPT = "*/*"
BAD_ACCEPT = "application/json"
FAKE_CRUMB = "zTESTcrumb1"          # same shape as the real 11-char token


# ---------------------------------------------------------------------------
# Offline transport. A real requests.Session subclass, so requests itself does
# the session-header -> request-header merge; only send() is short-circuited.
# That matters: asserting on a dict the module happened to build would not
# prove what goes on the wire, and the bug WAS a wire header.
# ---------------------------------------------------------------------------

def _make_response(url: str, status: int, body: str) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = url
    return resp


class _YahooLikeSession(requests.Session):
    """Answers the crumb path the way Yahoo did on 2026-09-20.

    Invariant reproduced: 200 + crumb for `Accept: */*`, 406 + a JSON error
    envelope for anything narrower. Cookie hosts always hand out a cookie, so
    a failure here can only be the Accept header.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):          # type: ignore[override]
        self.sent.append(request)
        url = request.url or ""
        if OD.CRUMB_PATH not in url:
            # A cookie host. Yahoo sets a session cookie here.
            self.cookies.set("A1", "fake-session-cookie", domain=".yahoo.com")
            return _make_response(url, 200, "<html>ok</html>")
        accept = request.headers.get("Accept", "")
        if accept == GOOD_ACCEPT:
            return _make_response(url, 200, FAKE_CRUMB)
        return _make_response(
            url, 406,
            '{"finance":{"result":null,"error":{"code":"Not Acceptable",'
            '"description":"Invalid Request"}}}',
        )


def _run_handshake_offline(session: requests.Session):
    """Drive the production handshake with `session` as its transport.

    Patches only option_data's view of `requests`, restores it unconditionally,
    and clears the module's cached session both before and after so one test
    cannot leak a crumb into the next.
    """
    original = OD.requests
    OD.reset_session()
    try:
        OD.requests = types.SimpleNamespace(
            Session=lambda: session,
            RequestException=requests.RequestException,
        )
        return OD._authenticated_session()
    finally:
        OD.requests = original
        OD.reset_session()


def _crumb_requests(session: _YahooLikeSession) -> list[requests.PreparedRequest]:
    return [r for r in session.sent if OD.CRUMB_PATH in (r.url or "")]


# ---------------------------------------------------------------------------
# Offline tests -- the regression guard.
# ---------------------------------------------------------------------------

def test_offline() -> None:
    print("== the handshake sends Accept: */* on the wire ==")
    session = _YahooLikeSession()
    got_session, crumb, reason = _run_handshake_offline(session)

    crumb_reqs = _crumb_requests(session)
    check("the handshake actually reached the crumb endpoint",
          len(crumb_reqs) >= 1, len(crumb_reqs))

    accepts = [r.headers.get("Accept") for r in crumb_reqs]
    check(f"every crumb request sends Accept: {GOOD_ACCEPT!r}",
          bool(accepts) and all(a == GOOD_ACCEPT for a in accepts), accepts)
    check(f"no crumb request sends Accept: {BAD_ACCEPT!r} (the 406 header)",
          all(a != BAD_ACCEPT for a in accepts), accepts)
    check("the crumb request is a plain GET",
          all(r.method == "GET" for r in crumb_reqs),
          [r.method for r in crumb_reqs])
    check("the browser UA from market_data is still sent",
          all(r.headers.get("User-Agent") == BROWSER_UA for r in crumb_reqs))

    print("== against the measured Yahoo behaviour, the handshake completes ==")
    check("a session is returned", got_session is session, got_session)
    check("the crumb is the token the endpoint returned",
          crumb == FAKE_CRUMB, crumb)
    check("no failure reason is reported", reason is None, reason)

    print("== the guard bites: the old header would fail this ==")
    # Proof that the check above is not vacuous. Same production code, same
    # fake Yahoo, only the Accept header forced back to application/json --
    # which is exactly the pre-fix code path.
    class _PinnedBadAccept(_YahooLikeSession):
        def send(self, request, **kwargs):      # type: ignore[override]
            if OD.CRUMB_PATH in (request.url or ""):
                request.headers["Accept"] = BAD_ACCEPT
            return super().send(request, **kwargs)

    bad = _PinnedBadAccept()
    bad_session, bad_crumb, bad_reason = _run_handshake_offline(bad)
    bad_accepts = [r.headers.get("Accept") for r in _crumb_requests(bad)]
    check("the pre-fix header really does send application/json",
          bad_accepts and all(a == BAD_ACCEPT for a in bad_accepts), bad_accepts)
    check("with the pre-fix header the handshake fails",
          bad_session is None and bad_crumb is None, (bad_session, bad_crumb))
    check("and the failure is reported as a reason, not an exception",
          isinstance(bad_reason, str) and "crumb" in bad_reason.lower(), bad_reason)

    print("== every chain would be reported unavailable, not invented ==")
    # The downstream symptom, asserted so the cost of a revert is visible in
    # the test output and not only in the hedge page.
    original = OD.requests
    OD.reset_session()
    try:
        OD.requests = types.SimpleNamespace(
            Session=lambda: _PinnedBadAccept(),
            RequestException=requests.RequestException,
        )
        chain = OD.fetch_option_chain("AAPL")
    finally:
        OD.requests = original
        OD.reset_session()
    check("the chain is UNAVAILABLE", chain.available is False, chain.available)
    check("no strikes are fabricated", not chain.quotes, len(chain.quotes))
    check("the note explains why", isinstance(chain.note, str) and bool(chain.note),
          chain.note)

    print("== the source no longer carries the 406 header ==")
    import inspect
    src = inspect.getsource(OD._authenticated_session)
    header_lines = [
        line for line in src.splitlines()
        if '"Accept"' in line and not line.strip().startswith("#")
    ]
    check("exactly one Accept header is set", len(header_lines) == 1, header_lines)
    check(f"and it is {GOOD_ACCEPT!r}",
          bool(header_lines) and GOOD_ACCEPT in header_lines[0], header_lines)

    print("== the module still points at the crumb endpoint it was measured on ==")
    check("crumb path unchanged", OD.CRUMB_PATH == "v1/test/getcrumb", OD.CRUMB_PATH)
    check("cookie hosts unchanged",
          OD.COOKIE_HOSTS == ("https://fc.yahoo.com", "https://finance.yahoo.com"),
          OD.COOKIE_HOSTS)


# ---------------------------------------------------------------------------
# Live proof -- evidence, not a gate. Requires the network and Yahoo being up.
# ---------------------------------------------------------------------------

def test_live() -> None:
    print("== LIVE: the same request, two Accept headers ==")
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept-Language": "en-US,en;q=0.9",
    })

    cookie_host = None
    for host in OD.COOKIE_HOSTS:
        try:
            session.get(host, timeout=HTTP_TIMEOUT, allow_redirects=True)
        except requests.RequestException as exc:
            print(f"  NOTE  cookie host {host} failed: {exc}")
            continue
        if session.cookies:
            cookie_host = host
            break
    check("Yahoo issued a session cookie", bool(session.cookies), cookie_host)
    if not session.cookies:
        print("  NOTE  no cookie, so the crumb comparison cannot be made")
        return
    print(f"  NOTE  cookie from {cookie_host}, {len(session.cookies)} cookie(s)")

    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/{OD.CRUMB_PATH}"

        try:
            bad = session.get(url, headers={"Accept": BAD_ACCEPT}, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            check(f"{host} responded to {BAD_ACCEPT}", False, exc)
            continue
        body = (bad.text or "").strip()
        print(f"  NOTE  {host}  Accept: {BAD_ACCEPT:18}  HTTP {bad.status_code}  "
              f"body[:72]={body[:72]!r}")
        check(f"{host}: Accept: {BAD_ACCEPT} is refused with HTTP 406",
              bad.status_code == 406, bad.status_code)

        try:
            good = session.get(url, headers={"Accept": GOOD_ACCEPT}, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            check(f"{host} responded to {GOOD_ACCEPT}", False, exc)
            continue
        crumb = (good.text or "").strip()
        masked = (crumb[:2] + "*" * max(0, len(crumb) - 2)) if crumb else ""
        print(f"  NOTE  {host}  Accept: {GOOD_ACCEPT:18}  HTTP {good.status_code}  "
              f"crumb={masked!r} ({len(crumb)} chars)")
        check(f"{host}: Accept: {GOOD_ACCEPT} returns HTTP 200",
              good.status_code == 200, good.status_code)
        check(f"{host}: and the body is a non-empty crumb, not a JSON error",
              bool(crumb) and "{" not in crumb, crumb[:72])

    print("== LIVE: the production handshake itself ==")
    OD.reset_session()
    try:
        live_session, live_crumb, live_reason = OD._authenticated_session()
        check("_authenticated_session completes against the real endpoint",
              live_session is not None and bool(live_crumb), live_reason)
        if live_crumb:
            print(f"  NOTE  production crumb length {len(live_crumb)}")
    finally:
        OD.reset_session()


def main() -> int:
    live = "--live" in sys.argv[1:]
    test_offline()
    if live:
        print()
        test_live()
    else:
        print()
        print("== LIVE proof skipped (pass --live to re-measure against Yahoo) ==")

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
