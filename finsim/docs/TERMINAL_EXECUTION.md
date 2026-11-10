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
  `HEDGE_LEG_ENDED` item names it and the primary that still works.
- **All-or-none (option-only, one underlying)**: every leg needs a fresh Cboe quote with displayed size for its whole
  remaining quantity in the **same** chain observation; otherwise nothing fills (`AON_SIZE`, `AON_STALE`) and every leg
  keeps working. The fill is one command for all legs with one option-margin recomputation; cancelling any leg cancels
  the package. Example (§19.22, iron condor ×10): premiums −2,100 + 4,000 + 3,500 − 1,200 = +4,200.00 credit, fees
  4 × 10 × 0.65 = 26.00, margin = max(put spread 30,000 − 1,900, call spread 30,000 − 2,300) = 28,100.00.
- **Your own legs (key CUSTOM, 1.0.7.2: the Smart Ticket's combinations, `strategies._custom_spec`)**: 2 to 8 exact
  legs `{instrument_id, side, qty, order_type?, limit_px?}` in a US Exchange or prime-broker account, as one package.
  Options on one name are all-or-none as above: every leg is MARKET at its executable bid / ask. A leg with a limit or
  another order type is refused `LIMIT_IN_AON`: there is no net limit on a combination in this version, so send that
  leg as an order of its own. One share leg plus options on that same name is sequential as above. The shares are the
  primary (MARKET or LIMIT) and are listed first whatever order they were named in; the option legs wait for its fills
  and keep their own MARKET / LIMIT terms (`PackageLeg.limit_px` carries a limit to `order.submit`). A waiting leg is
  released in whole numbers: floor(shares filled x its own quantity / the primary's quantity, as confirmed), so every leg
  is released in full once the shares have filled, whatever the ratio (1 call for 300 shares is released at the 300th
  share), also over a replace or an amend of the shares, as for every sequential package above. Anything else is
  refused (`COMBO_INVALID` / `LEGS`): two names, a futures leg, two share legs, or one contract named twice. A package
  holds one name: its shares and its options. Option sides are BTO / STO / BTC / STC and share sides BUY / SELL /
  SELL_SHORT / BUY_TO_COVER (`SIDE_INVALID` otherwise). An option leg must be in the master or on the cached chain
  (`CHAIN_PENDING` while it loads); neither is `NOT_LISTED` and nothing is invented, and a leg the cached chain does
  not quote is `NOT_AVAILABLE`. Prices are as the templates: the chain's ask to buy (BTO / BTC), its bid to sell (STO
  / STC), the shares at your limit or the quote. A sold call counts as covered by the shares the account holds after
  the package's share leg. `POST /api/orders` lists the package's contracts and records their chain quotes before the
  command, as for a single option order. So an all-or-none combination whose legs are executable fills at the submit
  from that one observation; otherwise it works until one observation can fill every leg. Request ids: the
  combination's own id, `<id>:L1..` for its all-or-none legs, `<id>:H1..` for a sequential package's waiting legs.
  Pending and the order drawer name it 'your own legs (Smart Ticket)'. It is never listed among the 60 templates.
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
  a leg you ticked before its primary, and says when a leg you ticked alone is kept; "Cancel all open" and the Smart
  Ticket's "Cancel what this ticket sent" list every leg before its primary (the confirm names them all) and keep a
  primary with its legs in one batch of 500. An order that had already ended is skipped (`NOT_OPEN`); a refused cancel is reported with
  its code. The answer lists what was cancelled, skipped, failed and kept working. Resending the same batch after a
  lost answer replays every cancel already made (`X-Replayed: 1`), cancels nothing twice and gives the same answer -
  fifth review: the legs that ended with a listed primary are named again (`with: primary`), listed or not. The Smart
  Ticket's confirm says, for a leg whose primary is not listed, whether that primary (or the order that replaced it)
  still works (kept) or has ended (the leg is cancelled now).
- **Resending an order under its own request id (1.0.7.2)**: a body that differs from the stored command only in
  `preview_id`, `confirm_large` or the Smart Ticket's `smart` note is the same order and is answered as its replay
  (`X-Replayed: 1`, the order that was placed): every send takes a fresh preview first. Any other difference is still
  409 `REQUEST_ID_REUSED`.

## 10. What is not simulated

No invented fills, no random prices, no synthetic events, no early assignment, no forced liquidation. When the free feed
cannot give an executable price, the order rests and the preview says why.
