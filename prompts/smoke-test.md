# Smoke Test — Phase 1 delivery-proof run

Purpose: verify the cloud environment end-to-end BEFORE the scheduled reports rely on it.
This run generates NO report. It checks connectivity, permissions, secrets, and delivery,
then messages the results to Telegram. Follow the steps in order; never print secret values.

## 1. Runtime checks

- `TZ="America/New_York" date` — record current ET time and UTC time.
- Confirm the repo cloned: `git log --oneline -1` and `git status`.

## 2. Environment variables

For each of: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, FRED_API_KEY, TWELVE_DATA_KEY,
FINNHUB_KEY, ALPHA_VANTAGE_KEY, COINGECKO_KEY, MASSIVE_KEY — record only SET or MISSING
(test with `[ -n "$VAR" ]`). NEVER echo values.

**2b. If any are MISSING from the process environment, DISCOVER where the platform put them**
— claude.ai environment variables may be delivered as a `.env`-style file instead of process
exports. Search (printing FILE PATHS ONLY, never contents):

```bash
ls -la ~ ; ls -la ~/.claude 2>/dev/null ; ls -la /run /etc/profile.d 2>/dev/null | head -40
grep -rls "TELEGRAM_BOT_TOKEN" ~ /run /etc /opt /workspace 2>/dev/null | grep -v intelligence-terminal | head -10
env | cut -d= -f1 | sort   # names only — see what IS exported
```

If a file is found, source it (`set -a; . <file>; set +a`), re-run the §2 checks, and record
in the diagnosis: `"env_delivery":"process|file:<path>|not_found"`. This discovery is the
single most important output of the smoke test — the scheduled report runs will use the same
mechanism.

## 3. Network egress (allowlist verification)

`curl -sS -o /dev/null -w "%{http_code}" --max-time 20` each of the following. Record the
HTTP code per endpoint. A `403` accompanied by response header `x-deny-reason: host_not_allowed`
means the domain is missing from the environment allowlist — record it as BLOCKED.

1. `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026`
2. `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10`
3. `https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json`
4. `https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR`
5. `https://ll.thespacedevs.com/2.3.0/api-throttle/`
6. `https://data.sec.gov/submissions/CIK0001181412.json` — with header `User-Agent: LoganTerminal/1.0 (loganshaffer87@gmail.com)`
7. `https://query1.finance.yahoo.com/v8/finance/chart/ES=F?interval=1d&range=1d` — with a browser User-Agent (Yahoo only)
8. `https://api.coingecko.com/api/v3/ping`
9. `https://api.telegram.org` (bare GET; any HTTP response ≠ blocked is a pass for egress)

## 3b. Authenticated API checks (only for keys found SET in §2)

One real call per configured key — record `ok` (HTTP 200 + parseable payload) or the failure
status per API. Never print key values or full responses; a one-line summary field is fine.

```bash
curl -s "https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key=${FRED_API_KEY}&file_type=json&sort_order=desc&limit=1"
curl -s "https://api.twelvedata.com/quote?symbol=SPY&apikey=${TWELVE_DATA_KEY}"
curl -s "https://finnhub.io/api/v1/quote?symbol=AAPL&token=${FINNHUB_KEY}"
curl -s "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey=${ALPHA_VANTAGE_KEY}"   # uses 1 of the 25/day budget
curl -s "https://api.polygon.io/v2/aggs/ticker/SPY/prev?apiKey=${MASSIVE_KEY}"
```

Add to the §7 diagnosis JSON: `"api_checks":{"fred":"ok|<err>","twelvedata":"ok|<err>","finnhub":"ok|<err>","alphavantage":"ok|<err>","massive":"ok|<err>"}`.

## 4. Git push permission (direct-to-main)

Append one line to `state/run-log.jsonl`:
`{"ts":"<UTC ISO>","slot":"smoke-test","ok":null,"note":"connectivity check"}`
Commit as `Smoke test: connectivity check` and `git push origin main`.
- Success → PUSH OK (the "Allow unrestricted branch pushes" toggle works).
- Rejection mentioning `claude/` branches or 403 from the git proxy → PUSH BLOCKED (the
  toggle is off for this repo — flag it loudly in the results).

## 5. Site reachability

`curl -s -o /dev/null -w "%{http_code}" https://shafferusa.github.io/intelligence-terminal/` — expect 200.

## 6. Telegram delivery

POST the results to `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage` with
`parse_mode: HTML`, chat_id `${TELEGRAM_CHAT_ID}`:

```
<b>Smoke test — Personal Intelligence Terminal</b>
Run at: <ET time> ET
Env vars: N/8 set (missing: …)
Egress: N/9 reachable (blocked: …)
Git push to main: OK | BLOCKED
Site: 200 | error
Verdict: READY FOR SCHEDULED RUNS | NOT READY — <reason>
```

Check the response contains `"ok":true`. Retry once on failure.

## 7. Wrap up

Update the run-log line: set `"ok": true|false` per the overall verdict, and REPLACE the
`"note"` field with a compact JSON diagnosis (so the operator can triage from the repo even
when Telegram delivery itself is broken) — NEVER including secret values:

```json
{"env_set":["NAME",...],"env_missing":["NAME",...],
 "egress_ok":["host",...],"egress_blocked":["host",...],
 "push":"ok|blocked","site":200,"telegram":"ok|failed|skipped"}
```

Commit `Smoke test: results` and push. End the session with the same results summary as your
final message.
