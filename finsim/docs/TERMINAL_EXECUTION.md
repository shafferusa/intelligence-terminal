# How orders execute in the FinSim Live Terminal

This page states, in plain words, every rule the terminal applies between the moment you press *Submit* and the moment a
fill is written into the ledger. Nothing on this page is a simulation: every price is an observed, delayed market print,
every rule below is enforced by code (`finsim/finsim/terminal/orders_rules.py`, `orders_preview.py`, `orders.py`,
`matcher.py`, `execution.py`) and tested by hand-worked scenarios (`tests/test_terminal_orders.py`,
`test_terminal_equities.py`, `test_terminal_sweeps.py`). Authoritative detail: `TERMINAL_DESIGN.md` §10.

## The sentences the ticket shows (TERMINAL_DESIGN.md §1.3, verbatim)

| key | sentence |
|---|---|
| FILL_PRICE | Fills happen at the last observed trade (Yahoo, up to 15 min delayed) or, for options, at the Cboe delayed bid/ask; no price is invented. |
| LIMIT_RULE | A marketable limit fills at the observed price on its first look; afterwards a crossed limit fills at the limit price. |
| STOP_RULE | Stops trigger on the observed last trade and fill at that print; a gap through the stop fills at the gap price. |
| TRAILING | A trailing stop ratchets with each observed print in your favour (sell: highest last minus the trail; buy: lowest last plus the trail) and never moves back; it triggers on the observed last and fills at that print. |
| PARTICIPATION | Each new 1-minute bar lets at most 10 % of its volume fill; the remainder keeps working. |
| NO_VOLUME | With no volume information the order fills in full. |
| SESSION | Orders entered outside the session rest until it opens; DAY orders expire at that session's close; GTC after 90 days. |
| STALE | A quote older than its stated delay plus 5 minutes is stale and blocks executions; valuation keeps the last valid mark with its time. |
| AON | An option-only package fills all-or-none: every leg must have a fresh Cboe quote with displayed size for the whole quantity in the same observation, or nothing fills; packages with a stock or futures leg fill sequentially (the hedge legs after the primary, resized on partial fills). |
| COST_BASIS | Cost basis is gross; commissions are in Fees. |
| FIFO | Lots are relieved first-in, first-out. |
| SWEEP | When an account needs more cash than it holds, exactly the shortfall moves from Treasury as one linked transfer; idle cash above the account's needs returns to Treasury after 17:30 ET. |
| AVAILABLE | Available = settled cash + receivables due by the order's settlement date − payables − cash reserved by working orders (per account, per currency). |

## 1. What a quote must be before anything fills

A fill needs an **executable** observation (`quotes.is_executable`): the session of the instrument's group is open; the
record is `delayed` (not `stale`, not `computed`, not `private`, not `unavailable`); its own timestamp is newer than the
session's open (a print from yesterday never fills today — the laptop-sleep guard); the provider's own session has not
ended; and for options the Cboe record carries `bid > 0` and `ask >= bid`. A quote whose age exceeds its stated delay
(15 min) plus 5 minutes, or that nobody has refreshed for 5 minutes, is stale and blocks executions. Valuation is never
blocked: a stale or closing record stays the last valid mark, shown with its time.

## 2. The price every fill uses (`orders_rules.exec_price`)

- Listed options: the Cboe delayed **ask** for buys (BTO / BTC) and **bid** for sells (STO / STC).
- Equities / ETFs: the Cboe underlying ask / bid when a fresh chain for that name is already cached (it is never fetched
  for this), else Yahoo's **last** trade. The free Yahoo feed carries no bid/ask, and the preview says so.
- Futures: the observed **last**. FX spot: the Yahoo mid plus or minus 0.2 pip against you.

The basis is recorded on every fill (`fills.price_basis`: `last`, `ask`, `bid`, `limit`, `stop_print`) together with the
whole quote record (`fills.quote_json`), so every fill can be re-read without the network.

## 3. Order types (`orders_rules.evaluate`, pure and table-tested)

1. **MARKET** fills at the executable price.
2. **LIMIT**: on the order's first executable look a *marketable* limit (executable price at or better than the limit)
   fills at the observed price; afterwards the order rests and fills **at the limit price** when the observed last crosses
   it (buy: last ≤ limit; sell: last ≥ limit). A look that was not executable (closed session, stale quote) does not count
   as the first look.
3. **STOP**: triggers when the observed last reaches the stop (buy: last ≥ stop; sell: last ≤ stop) and fills at that
   print in the same pass. A gap through the stop fills at the gap price, and the fill says so. A buy stop at or below the
   last (or a sell stop at or above it) is refused at submit with `STOP_SIDE` ("use a limit").
4. **STOP_LIMIT**: triggers like a stop, then behaves like a limit.
5. **TRAILING_STOP** (`trail_amt` in price units **or** `trail_pct`, exactly one): at submit the reference is the observed
   last and the stop is `last − trail` (sell) / `last + trail` (buy). Every new print in your favour ratchets the reference
   and the stop (sell: up only; buy: down only); a percentage trail is re-derived from the new reference and the stop is
   rounded to the tick. The ratchet is written to the order (`stop_px`, `trail_ref_px`, one order event per change), so a
   restart keeps it. It triggers on the observed last and fills at that print.

Worked example (§19.22): SELL 1,000 AAPL, trail 2.00, last 339.50 → stop 337.50. Last 341.00 → stop 339.00. Last 340.00 →
unchanged. Last 338.90 → triggers, fills 1,000 @ 338.90. With `trail_pct 1.00` at 341.00 the stop is 341.00 × 0.99 = 337.59.

## 4. How much fills per pass (`orders_rules.fill_cap`)

Each **new** completed 1-minute bar lets at most the participation rate (default 10 %, a setting, printed in every
preview) of its volume fill, rounded down to whole lots and never below one lot; the remainder keeps working. The same bar
never fills twice (the order remembers `last_bar_ts`). A record with no bar or no volume figure fills in full (disclosed
`NO_VOLUME`); a completed bar that traded nothing fills nothing. Options are capped by the displayed Cboe size on the side
you take (no displayed size → the whole quantity, disclosed; size 0 → nothing). FX has no volume concept.

Example (§19.9): MARKET BUY 50,000 SPY; bar volume 120,000 → 12,000 fill; the same bar → nothing; the next bar 90,000 →
9,000 more; a bar with 0 volume → nothing; cancel → CANCELLED with 21,000 filled and the volume-weighted average price.

## 5. Sessions and expiry

Orders entered outside the session are accepted and **rest** (the preview says when they can first trade); nothing
executes outside the regular session (US equities and equity options 09:30–16:00 ET, 13:00 on early-close days; index
options to 16:15; CME groups per the session table with the 17:00–18:00 break; FX Sun 17:00–Fri 17:00). A DAY order
expires at the close of the session it can first trade in (placed Tuesday 17:30 → expires Wednesday 16:00; the day after
Thanksgiving → 13:00). GTC expires after 90 days or at the instrument's expiry. Expiry is the lifecycle's job: an order
past its expiry is never filled, even if a fresh quote arrives first. No new opening option trades after 15:30 ET on the
option's expiry day.

## 6. The matcher and idempotency (`matcher.Matcher`, `order.match`)

The matcher evaluates every open order of an instrument each time the quote worker refreshes it, once at submit, and
sweeps all open orders every 15 s. Each decision becomes **one command** per order (`match:<order>:<quote time>:<bar>`),
so a second look at the same observation is a replay and nothing fills twice, and one order's funding failure cannot roll
back another's fill. Inside the command the order's status, filled quantity and expiry are re-read and the quote is
re-checked against the command clock: a cancel that committed in between wins.

## 7. What a fill does to the books (`execution.apply_fill`, one transaction)

1. Economics: gross = quantity × price × multiplier; fee from the account's schedule (US equities USD 0.005/share, minimum
   1.00; options 0.65/contract; futures 2.25/contract/side; PB accounts their own schedule). Cost basis is gross; the fee is
   an expense.
2. **Funding before posting** (buys: gross + fee; futures: initial margin + fee): own available cash at the fill's
   settlement date → a Treasury **sweep-in of exactly the shortfall** as one linked transfer → PB financing up to headroom
   → otherwise the fill is refused: a resting order becomes **SUSPENDED** (resume after a deposit or transfer); a new order
   at submit is refused with `INSUFFICIENT_FUNDS` and nothing is written.
3. The fill row, the journal entry (`Dr 1100 gross / Dr 5000 fee / Cr 2100 gross + fee` for an equity buy; the sale
   relieves lots FIFO and realises to 4000), the lot, and the settlement row due T+1 (business days; private secondaries
   after the negotiated calendar days). Settlement moves the payable or receivable into settled cash on the due date.
4. Shorts consume their borrow; covers return it. Option fills recompute the account's option margin (moved to 1310).
5. The order's filled quantity, average price, status (PARTIAL / FILLED), fees and the bar it used are updated; hedge
   children of a primary are resized to floor(shares filled × leg quantity / shares quantity) (section 9).
6. The ledger-level integrity set runs on the touched keys before COMMIT.

Worked example (D2): BUY 100 AAPL at the observed last 339.50 with no cash in the account: gross 33,950.00, fee
max(100 × 0.005, 1.00) = 1.00, need 33,951.00, sweep-in 33,951.00 linked to the fill, settles the next business day,
NAV falls by exactly the 1.00 commission.

## 8. Fat-finger and stale-preview guards (409, nothing written)

- `CONFIRM_REQUIRED`: the sweep-in the fill would need exceeds the confirm threshold (default USD 2,000,000.00) or the
  notional exceeds 25 % of the lifetime's NAV; resubmit the same request with `confirm_large: true`.
- `PREVIEW_STALE`: the `preview_id` is unknown or older than 120 s, or the executable price moved more than 0.5 % against
  you since the preview; the response carries the fresh preview and the difference. 1.0.7.2: that fresh preview is stored
  (it has its own `preview_id`, as the `CONFIRM_REQUIRED` one does), and the tickets' "Accept" sends it: a price that moves
  again while the dialog is open comes back stale and asks again, never filled at figures you did not see.
- `QUOTE_STALE`: a MARKET / STOP / STOP_LIMIT / TRAILING_STOP submitted while the session is open against a quote that is
  not executable (a LIMIT may rest).
- `INSUFFICIENT_FUNDS`: the worst-case need exceeds own available + Treasury available + PB headroom; the message carries
  the three amounts. `FUNDING_SHORTFALL`: the account has an open funding shortfall in that currency.
- `OVERSELL`: a SELL larger than the long position net of working sells (enter SELL for the long quantity and SELL_SHORT
  with a borrow for the rest); `CLOSE_ONLY_MISMATCH` for covers and option closes.

## 9. Packages

- **Sequential (stock or futures primary + option legs)**: the primary works on its own; the hedge legs wait
  (`WAITING_PARENT`) and are released, sized to floor(primary filled × ratio), on each fill of the primary (12,000 of
  50,000 SPY with ratio 0.01 → 120 contracts; 21,000 → 210). Legs then match on their own quotes. 1.0.7.2: the release
  is in whole numbers, floor(shares filled × leg quantity / shares quantity), with the two quantities as they were
  confirmed (1 call for 300 shares is released at the 300th share), whatever quantity an amend or a replace gave the
  primary: 300 shares + 3 calls amended to 350 release 3 calls, at 250 shares 2, and 3,000,000 shares + 30,000 calls
  amended to 2,999,999 release 29,999 - never one more than the shares cover, at any size. Over a
  replace the shares are counted over the chain (what the replaced orders filled plus the replacement's fill, against
  what they filled plus its quantity), and the legs released before the replace are resized with it. When the primary
  ends (filled, cancelled or expired), a leg still waiting ends with it (cancelled: "primary ended unfilled" when no share
  was bought, else "primary ended before it was due"), and a leg already released keeps working at its size. Such a leg
  is always said: cancelling the primary yourself names it in the answer (`ended_with_it`; Pending's x says so in its
  confirm and its toast), and when a fill (an amended quantity the ratio does not fit) or the session close ends the
  primary, one information item (`HEDGE_LEG_ENDED`) names the legs that ended with it. 1.0.7.2 fifth review: a leg lives
  exactly as long as its primary - it takes the primary's time in force, expiry, good-till date and next-session start when
  it is created, and again when the primary is amended or re-priced (replace) to another time in force (a DAY package kept
  working as GTC keeps its waiting puts with it to the next session, where they are released as the shares fill). A leg
  still waiting is never expired on its own clock while its primary works (a leg left with an older expiry is kept, and
  takes the primary's lifetime when released); only its own contract's end (or a forced expiry) ends it first, and then one
  `HEDGE_LEG_ENDED` item names it and the primary that still works. 1.0.7.2 sixth review: that rule is exact for the legs still
  waiting only. A leg already released for shares bought only ever lives LONGER when its primary is amended or re-priced: it takes
  the primary's later expiry (the answer names it, `hedge_legs_longer`, and the ticket's status line says so), never an earlier one
  (GTC -> DAY, an earlier GTD), never a next-session start (a primary queued for the next session makes it good till that session's
  close, still working now), and a time in force you gave the leg with its own amend is kept. Every later release - a working leg
  grown, a filled leg re-opened by a later day's fill - gives the leg the primary's lifetime when that is longer, so it can still
  fill. A released leg that expires with part of it unfilled is said by one `HEDGE_LEG_ENDED` item also when its primary has ended.
- **All-or-none (option-only, one underlying)**: every leg needs a fresh Cboe quote with displayed size for its whole
  remaining quantity in the **same** chain observation; otherwise nothing fills (`AON_SIZE`, `AON_STALE`) and every leg
  keeps working. The fill is one command for all legs with one option-margin recomputation; cancelling any leg cancels
  the package. Example (§19.22, iron condor ×10): premiums −2,100 + 4,000 + 3,500 − 1,200 = +4,200.00 credit, fees
  4 × 10 × 0.65 = 26.00, margin = max(put spread 30,000 − 1,900, call spread 30,000 − 2,300) = 28,100.00.
- **The cover of calls (1.0.7.2, every ticket and client; SIMPLIFY_PLAN s5)**: one read-only function,
  `orders_preview.cover_state(services, conn, account_id, underlying_id, exclude_order_id=None, claims=False)`, says per
  account and name: `held` (long shares), `working_sells` (the remaining of working SELL / STC orders, WAITING_PARENT
  included), `working_shorts` (working SELL_SHORT orders), `lent`, and the shares reserved for short calls in WHOLE
  contracts as the margin assigns them (`options.margin_requirement_c` step 1: per short call, in expiry then strike order,
  cover = min(its contracts, shares left // its multiplier); index options never) - first the calls already sold
  (`reserved_sold`, read from the positions at the check, never from the last `margin_state` snapshot), then the
  unfilled contracts of working STO call orders (`reserved_working`). With 150 held and 2 calls sold, 100 are reserved and
  50 can be sold. `free = held - working_sells - reserved_sold - reserved_working - lent` (what a sale, a loan or an
  exercise may take) and `free_for_calls = free - working_shorts` (what new calls may take as cover), never below 0. With
  `claims=True` (the order route and the order preview; never the Smart Ticket's parse) it also lists `claimed`: the
  account's calls on the name sold as covered and still open (a working STO call order whose `order.submit` command carries
  `covered: true` - its own, its replace chain's first order's or its package's - and, per contract, the short contracts
  its claimed orders filled). The rules, each a refusal only:
  - INV-1: an order or all-or-none package sent with `covered: true` (a call sold to open marked "sold as covered" in the
    ticket) is placed only while `free_for_calls` >= the multiplier x the contracts it sells to open; otherwise 409
    `NOT_COVERED` and nothing is written (also shown by the order preview as a blocker). 400 `COVERED_INVALID` on an order
    that sells no call to open, or on a sequential package.
  - INV-2: the cover of every working STO call order (WORKING, PARTIAL, SUSPENDED; next session, GTC, a released hedge leg;
    not WAITING_PARENT), claimed or not, is reserved from acceptance until it ends; INV-3: the cover of calls already sold
    is read from the positions at each check. A sale (new, amend, replace), the owner's early put exercise and a share loan
    never take reserved shares: 400 `OVERSELL` whose hint names the calls ("300 ACME cover calls being sold (O-...): cancel
    or close them first (SHARES_RESERVED)"; "cover calls already sold (<OCC>): buy them back first"), 409 `SHARES_RESERVED`
    on an exercise, and `lending_accruals.free_to_lend` lends only free shares.
  - INV-4: a SELL_SHORT (new, amend, replace) while the account holds the name long is refused 400 `SHORT_WHILE_LONG`
    ("sell them first"); the OVERSELL hint reads "sell N; once they have sold, SELL_SHORT M with a borrow".
  - INV-5: an amend or replace that raises the contracts of an STO call placed with `covered: true` (the claim read from its
    replace chain's first order) re-runs INV-1 for the added contracts, the order itself excluded: 409 `NOT_COVERED`,
    nothing changed.
  - INV-9: while calls on a name are sold as covered (`claimed`), every NEW call sold to open on that name - claimed or not,
    a single order, an all-or-none package's STO call legs, an amend or replace raising a plain call - is placed only while
    `free_for_calls` >= its contracts x multiplier (409 `SHARES_RESERVED`, the claimed calls named): the margin would
    otherwise give an earlier-expiring or lower-strike call their cover. A sequential package whose shares order buys that
    name covers its call legs with those shares (released only as they fill): allowed.
  Known limits, each said: the automatic exercise at expiry of a long put whose short call expires later still delivers the
  shares - when that takes the cover of calls sold as covered, one WARN attention item `COVER_DELIVERED` names the put and
  the calls (now naked); an as-of booking (the record of an execution done elsewhere) is never refused for cover; a short
  sale placed while flat and still resting when the account buys the name keeps 1.0.7.1's fill rules, and `working_shorts`
  keeps new calls off those shares. The Smart Ticket's earlier "your own legs" package (key CUSTOM, never deployed) is gone:
  `CUSTOM` is 400 `UNKNOWN_STRATEGY` again, as in 1.0.7.1; a CUSTOM package row written by the never-deployed candidate
  still loads, reads "your own legs (Smart Ticket)" and can be cancelled.
- **Cancel many at once (`POST /api/orders/cancel[?book=]`, 1.0.7.2)**: explicit order ids only (1 to 500, each named
  once) and one reason (at most 120 characters; 'cancelled by the owner' when none is given). The request id is the
  batch id (8 to 60 characters). The whole list is checked before anything is cancelled, and nothing is cancelled if
  any check fails: a malformed body or batch id (400), an unknown order (404, the ids named), or, with `?book=`, an
  order of another book (409 `BOOK_MISMATCH`, the orders named). Then there is one `order.cancel` per target, in the
  order given, never in parallel, each with the request id `<batch id>:<order or package id>`. An order of an
  all-or-none package cancels its whole package once, and its other legs, listed or not, are reported with it. Any
  other order is cancelled itself, and a primary's legs still waiting end with it, reported with it whether listed or
  not. A hedge leg is never cancelled while its shares order keeps buying (fourth review); its shares order is the
  primary or, after a re-price by replace, the order that replaced it (the legs still waiting move to it). A hedge leg
  listed AFTER its primary (or after an order that primary replaced) is listed with it and never gets a command of its
  own: still waiting when its primary's cancel went through, it ended with it; released for shares bought (before the
  batch, or while the confirm was open), or with a primary whose cancel did not go through (it had already ended, or it
  was refused), or whose primary was replaced meanwhile by an order that keeps working, it keeps working and is reported
  as kept (`RELEASED`, `PRIMARY_ENDED`, `PRIMARY_NOT_CANCELLED`, `PRIMARY_REPLACED`), so a protective leg is never
  cancelled for shares you hold unless you ticked it. A leg listed BEFORE its primary is your own tick: its cancel goes
  right after the primary's, and only once the primary has ended (cancelled here, or already ended); a primary whose
  cancel is refused, or that was replaced by an order that keeps working, keeps it (kept). A leg listed without its
  primary is cancelled by its own command once that primary has ended, and kept (`PRIMARY_WORKING`) while it still
  works - cancel the primary with it, or the leg on its own with its x. Pending Trades lists the waiting legs of a
  ticked primary in its confirm ("ends with its primary") right after it, the released ones apart as "keeps working",
  a leg you ticked before its primary, and says when a leg you ticked alone is kept - seventh review: a leg's primary is
  read over the replace chain there too (a leg released before a re-price goes before the replacement you ticked, and is
  said kept while that replacement works; the answer names the order that bought its shares); "Cancel all open" and the Smart
  Ticket's "Cancel what this ticket sent" list every leg before its primary (the confirm names them all) and keep a
  primary with its legs in one batch of 500. An order that had already ended is skipped (`NOT_OPEN`); a refused cancel is reported with
  its code. The answer lists what was cancelled, skipped, failed and kept working. Resending the same batch after a
  lost answer replays every cancel already made (`X-Replayed: 1`), cancels nothing twice and gives the same answer -
  fifth review: the legs that ended with a listed primary are named again (`with: primary`), listed or not. Pending's
  confirm says, for a leg whose primary is not listed, whether that primary (or the order that replaced it) still works
  (kept) or has ended (the leg is cancelled now).
- **Resending an order under its own request id (1.0.7.2)**: a body that differs from the stored command only in
  `preview_id`, `confirm_large` or a `smart` note (a body of the never-deployed candidate) is the same order and is answered as its replay
  (`X-Replayed: 1`, the order that was placed): every send takes a fresh preview first. Any other difference is still
  409 `REQUEST_ID_REUSED`.

## 10. What is not simulated

No invented fills, no random prices, no synthetic events, no early assignment, no forced liquidation. When the free feed
cannot give an executable price, the order rests and the preview says why.
