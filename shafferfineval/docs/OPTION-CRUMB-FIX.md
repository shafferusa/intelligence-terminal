# The option-chain crumb fix — one header, every chain

**File:** `option_data.py`, `_authenticated_session`
**Change:** `"Accept": "application/json"` → `"Accept": "*/*"`
**Measured:** 2026-09-20
**Guard:** `test_option_crumb.py`
**Class:** infrastructure. Not a model change.

---

## What broke

Yahoo's option endpoint (`/v7/finance/options/<symbol>`) is crumb-gated, unlike the quote and
fundamentals endpoints the rest of the app uses. `option_data._authenticated_session` therefore
does a two-step handshake: collect a session cookie from `fc.yahoo.com`, then exchange it for a
crumb at `https://query{1,2}.finance.yahoo.com/v1/test/getcrumb`.

That handshake was sending `Accept: application/json`.

The crumb endpoint does not return JSON. It returns eleven bytes of bare text. Asked for
`application/json` it refuses the whole request with **HTTP 406 Not Acceptable** — and, with a
straight face, delivers the refusal *as* a JSON error envelope. The same request with `Accept: */*`
returns **HTTP 200** and the crumb.

## The symptom, and why it was expensive to see

No crumb means `_authenticated_session` returns `(None, None, reason)`, so `fetch_option_chain`
returns an `OptionChain` with `available = False` for **every underlying**. Downstream the hedge
engine does the honest thing: it reports strikes as theoretical targets and marks premium, delta,
IV and liquidity as missing rather than inventing them.

That is correct behaviour on missing data — which is exactly the problem. A content-negotiation
failure in the handshake is indistinguishable, at the hedge page, from Yahoo genuinely having no
options for the name. Nothing crashed, nothing logged an error, no number was wrong. The app just
quietly had no listed-option data at all, and said so politely.

## The measured evidence, both ways

One `requests.Session`, cookie obtained first from `https://fc.yahoo.com`, then
`GET /v1/test/getcrumb` varying **only** the Accept header. 2026-09-20, from this machine:

| Host | `Accept` | Status | Body |
|---|---|---|---|
| query1.finance.yahoo.com | `application/json` | **406** | `{"finance":{"result":null,"error":{"code":"Not Acceptable",…` |
| query2.finance.yahoo.com | `application/json` | **406** | same |
| query1.finance.yahoo.com | `*/*` | **200** | 11-character crumb |
| query2.finance.yahoo.com | `*/*` | **200** | 11-character crumb |

Both hosts, both directions, same session, same cookie. The header is the only variable.

End-to-end after the fix, same session:

```
fetch_option_chain('AAPL') -> available=True, 23 expiries, 83 contracts on the front expiry
```

Before the fix that call returned `available=False` with the crumb-rejection note.

## The fix

One line, plus a comment carrying the measurement so the next reader does not re-derive it:

```python
session.headers.update({
    "User-Agent": BROWSER_UA,
    # `*/*`, NOT `application/json`. Measured 2026-09-20: the crumb
    # endpoint answers HTTP 406 "Not Acceptable" to an
    # `Accept: application/json` request and HTTP 200 with a crumb to
    # the same request with `*/*`. ...
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
})
```

No retry loop, no fallback, no new dependency, no scraping trick. This is not circumventing an
anti-bot wall: Yahoo is willing to hand over the crumb, and was only ever objecting to the content
type we claimed to accept.

## Why this is infrastructure, not a model change

The Shaffer production core is frozen, and this change stays outside it. It touches:

- no factor weight, no scaling constant, no sector formula;
- no classification band, no 0.20 calibration;
- no hedge arithmetic, no GPI rule, no asset-class v1 formula;
- no ML feature, label, training path or inference behaviour.

It changes **whether the bytes arrive**, nothing about what is computed from them. Identical inputs
still produce identical outputs; the difference is that inputs now exist. Scores computed while the
handshake was broken were not wrong — they were computed with listed-option data correctly and
explicitly marked unavailable. Any comparison across that boundary should note that option-derived
hedge detail is present after the fix and absent before it.

## The test that guards it

`test_option_crumb.py`. Plain script, stdlib plus `requests`, PASS/FAIL per line, nonzero exit on
failure.

**Offline (the actual regression guard, no network).** A `requests.Session` *subclass* whose
`send()` is short-circuited reproduces the measured rule — 200 + crumb for `*/*`, 406 for anything
narrower. Because it is a real Session, `requests` itself performs the session-header → request-header
merge, so the assertion is about what goes on the wire, not about a dict the module happened to
build. It then asserts the production handshake completes, that the crumb request carries `Accept: */*`
and never `application/json`, and — so the check is not vacuous — that pinning the header back to
`application/json` makes the same production code fail and report every chain unavailable with no
fabricated strikes. A source-level check pins that exactly one `Accept` header is set and that it is
`*/*`.

Verified to bite: with the header temporarily reverted, the offline run reported
**6 FAIL and exit 1** (`every crumb request sends Accept: '*/*'`, `no crumb request sends
Accept: 'application/json'`, `a session is returned`, `the crumb is the token the endpoint
returned`, `no failure reason is reported`, `and it is '*/*'`). Restored, it is 18 PASS and exit 0.

**Live (`--live`, proof not gate).** Re-measures both halves against Yahoo and prints the status
codes and crumb length. Skipped by default: a test that needs a vendor to be up is not a regression
test. Observed 2026-09-20: 26 PASS (18 offline + 8 live), 0 FAIL, exit 0.

```
NOTE  cookie from https://fc.yahoo.com, 1 cookie(s)
NOTE  query1.finance.yahoo.com  Accept: application/json    HTTP 406  body[:72]='{"finance":{"result":null,"error":{"code":"Not Acceptable","description"'
NOTE  query1.finance.yahoo.com  Accept: */*                 HTTP 200  crumb='V2*********' (11 chars)
NOTE  query2.finance.yahoo.com  Accept: application/json    HTTP 406  body[:72]='{"finance":{"result":null,"error":{"code":"Not Acceptable","description"'
NOTE  query2.finance.yahoo.com  Accept: */*                 HTTP 200  crumb='V2*********' (11 chars)
NOTE  production crumb length 11
```

(The crumb is masked in output on purpose — it is a short-lived session token, and there is no
reason for one to land in a log or a commit.)

## Caveat on durability

This is a vendor behaviour, not a contract. Yahoo can change it without notice, and the crumb
endpoint has moved before. The offline guard protects against *us* reverting the header; it cannot
protect against Yahoo changing its mind. If chains go universally unavailable again, run
`python test_option_crumb.py --live` first — it distinguishes "our header regressed" from "Yahoo
moved" in one command.

## Commit scope

This stands alone: `option_data.py` (one header + comment), `test_option_crumb.py` (new),
`docs/OPTION-CRUMB-FIX.md` (this note). It must not be folded into the historical point-in-time
data work — different subject, different risk, different review.
