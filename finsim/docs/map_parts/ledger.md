# FinSim map part: ledger, accounting, Treasury and financing

Scope: `finsim/finsim/engines/{ledger,pnl,treasury,accruals,settlement,collateral,repo,prime,seclending,lending_market,ccy_funding,private_credit,counterparties,clients,investors}.py`
plus the places `world.py` and `api/service.py` (and `api/server.py`) call them.
Paths below are relative to `C:\Users\logan\intelligence-terminal\finsim\finsim\`.
Every claim is marked VERIFIED (code read, path:line) or INFERRED.
Mapped read-only on 2026-10-20 (no code run, no tests run).

(Written incrementally; see STATUS line at the end.)

---

## 0. Headline findings (read this first)

1. TWO PARALLEL CASH/NAV SYSTEMS. Cash lives twice: (a) `Portfolio.cash[ccy].balance` / `.base_value`
   (the "cash subledger", mutated directly by every engine handler) and (b) ledger accounts `1010:<CCY>`
   (posted via LEDGER_POSTED events). The economic NAV (`pnl.compute_summary`, pnl.py:136-184) reads (a);
   the ledger NAV (`Ledger.nav`, ledger.py:242-245) reads (b). They are only compared at load time
   (`World.check_integrity`, world.py:180-203) and the result is just recorded in `w.integrity` - nothing
   stops or repairs a divergence. VERIFIED.
2. THE "TREASURY" IS A BOOK, NOT A POOL. Since the treasury redesign the save's Treasury is a `Portfolio`
   with `portfolio_type == "TREASURY"` (world.py:395-398). Its cash is ordinary book cash. A legacy
   pool `World.treasury_cash` (world.py:69) is still replayed (TREASURY_FUNDED, world.py:400-404) and is
   ADDED to the Treasury book's balances in the Treasury view (`Service._treasury_state`,
   api/service.py:740-753). VERIFIED.
3. A HARD-CODED 10,000,000 EXISTS IN THE CAPITAL PATHS: `World.create_portfolio(... capital=D(10_000_000))`
   default (world.py:732) and the HTTP route default `body.get("capital", 10_000_000)` for
   `POST /worlds/{id}/portfolios` (api/server.py:131). Any create call that omits `capital` contributes
   10,000,000 of FRESH capital (a CAPITAL_CONTRIBUTED event, world.py:746-747, 766-773) - including a
   Treasury book created that way (service.py:343-344: the TREASURY type never goes through the
   `from_treasury` path). The XCCY default USD notional in the ticket is also 10,000,000 (static/app.js:648,
   "the dollar principal exchanged at the start"). Full candidate list in section 5. VERIFIED (defaults);
   whether the owner's 10M came from one of these is INFERRED.
4. `emit()` IS LIVE DURING REPLAY; only `derive()` is suppressed (world.py:128-142). Any registered
   handler that calls `w.emit(...)` (instead of `w.derive`) would re-run its side effects on every load.
   Checked by script over every file in this area: none of their handlers emit (collateral/seclending use
   `derive` on purpose, collateral.py:122). VERIFIED.
5. REAL CASH-CREATING BUG FOUND: hedge-fund investor SUBSCRIPTIONS are never removed from `pending`, so every
   month end re-credits every past subscription as new CAPITAL_CONTRIBUTED (investors.py:131-137, world.py:585-598;
   only redemptions are removed, investors.py:182). Section 4.1 B1. VERIFIED.
6. The Overall page's "Treasury" tile is the Treasury book's USD settled cash plus any legacy pool
   (`t.treasury = ts["total_usd"]`, service.py:752, 861; static/app.js:1877). Ten candidate causes of the
   ~10,000,000 are in section 5; the strongest exact-amount matches are C1-C3.

---

## 1. Files and what each does

| File | Lines | Role | Tied to day advance? |
|---|---|---|---|
| engines/ledger.py | 257 | Chart of accounts (`CHART`), `JournalLine`/`JournalEntry` dataclasses, `Ledger` (post, balances, trial balance, nav, income statement). Pure in-memory. VERIFIED | No |
| engines/pnl.py | 294 | `PnLEngine`: marks positions (VALUATION_MARKED -> MTM journal), `compute_summary` (economic NAV from state), `snapshot` (NAV_SNAPSHOT + P&L explain buckets), greek attribution. VERIFIED | Yes: `mark_all`/`snapshot` run in the daily process (engines/simulation.py:120,128,131) |
| engines/treasury.py | 177 | `TreasuryEngine` = the CORPORATE TREASURER JOB (job TREASURY_MANAGER): seeded debt stack, monthly operating FX receipts/payments, debt service. NOT the save's Treasury book. VERIFIED | Entirely (simulation.py:126) |
| engines/accruals.py | 119 | Bond accrued interest true-up; cash interest (base ccy accrued, settled monthly; foreign ccy paid daily). VERIFIED | Entirely (simulation.py:124) |
| engines/settlement.py | 192 | Post-trade lifecycle (CAPTURED..SETTLED, seeded 3% match breaks) and DVP/RVP settlement moving payable/receivable into cash; Treasury/prime backstop on short cash. VERIFIED | Entirely (simulation.py:99-100) |
| engines/repo.py | 389 | Repo / reverse repo: open, close, roll, interest, marks, calls, cash margin, substitution. VERIFIED | Interest/rolls/marks/maturities in process_day (simulation.py:112) |
| engines/prime.py | 245 | Prime broker: margin loan (2700), daily sweep (negative base cash -> loan; free cash -> repay), excess liquidity, margin call, forced liquidation; `affordable()` buying-power check used by tickets. VERIFIED | Sweep/interest/calls in process_day (simulation.py:123) |
| engines/investors.py | 215 | Hedge-fund investors (job HEDGE_FUND): seeded holders, monthly subscriptions/redemptions, mgmt and performance fees. VERIFIED | Entirely (simulation.py:127) |
| other area files | | see section 3 (filled in as read) | |
| world.py (parts) | 1179 | Event bus (`emit`/`derive`/`replay`), core handlers for capital and the Treasury book (`_move_capital`, `_h_capital`, `_h_treasury_allocated`), `post()` -> LEDGER_POSTED, `record_cash_movement`, `check_integrity`, `create_portfolio`, `advance`/`catch_up`. VERIFIED | `advance`/`catch_up` are the day machine |
| api/service.py (parts) | 2042 | Read models: `_treasury_state`, `treasury`, `balance_sheet_rows`, `overall`, `dashboard` (NAV/day P&L/MTD/YTD recomputed here); book creation with capital defaults. VERIFIED | `dashboard` "live_delta" assumes once-per-day snapshots |

## 2. Key functions and data structures

### 2.1 Ledger (engines/ledger.py) - VERIFIED
- `CHART: Dict[str, (name, type)]` ledger.py:19-112. Cash is `1010` with a per-currency suffix `1010:USD`
  (ledger.py:20). Equity has ONE account, `3000 Contributed Capital` (ledger.py:56); no retained-earnings
  account, income/expense accounts are never closed, so ledger NAV = contributed capital + cumulative net income.
- DUPLICATE KEY BUG: `"2800"` is defined twice - "Derivative Liabilities - FX Forwards" (ledger.py:51) and
  "Derivative Liabilities - OTC (PV)" (ledger.py:54); Python keeps the second. `compute_summary` counts all of 2800
  as `otc_liabilities` (pnl.py:154). VERIFIED (dict literal); effect on FX forwards INFERRED (fx.py not mapped).
- `cost_account(sec, short)` ledger.py:117-123 (1100 equities / 1110 bonds / 1700 long options / 2600 shorts /
  2900 written options); `adj_account` ledger.py:126-129 (1150/1750 long MTM, 2610/2910 short MTM).
- `dr(account, amount, security_id=None, memo="") -> Dict` / `cr(...)` ledger.py:175-187: a negative amount flips side.
- `JournalLine(account, debit, credit, security_id, memo)` ledger.py:144-150;
  `JournalEntry(id, portfolio_id, date, memo, lines, event_id, cause_id, reference)` ledger.py:153-168.
- `Ledger.post(entry_id, date, memo, lines, event_id, cause_id, reference) -> JournalEntry` ledger.py:198-216:
  drops zero lines; raises `LedgerError` if empty, unbalanced or unknown account. It does NOT reject a duplicate
  `entry_id`: a repeat is added to `balances` again and overwrites `entries_by_id` (ledger.py:210-215). No
  idempotency guard. VERIFIED.
- Balances are running dicts `balances[account]` (debit-positive) and `security_balances[sid][account]`
  (ledger.py:194-195, 206-213), never re-derived from `entries`. `balance()` natural sign ledger.py:218-221.
- `trial_balance()` ledger.py:230-240; `nav()` = sum(ASSET) - sum(LIABILITY) ledger.py:242-245;
  `income_statement()` ledger.py:247-252; `net_income()` ledger.py:254-257.
- Entry ids: `entry_id = f"JE-{self.next_seq():06d}"` (world.py:635), `next_seq() = len(self.events)+1`
  (world.py:122-123). VERIFIED.

### 2.2 How a posting happens (world.py) - VERIFIED
- `World.post(portfolio_id, memo, lines, cause, reference) -> Optional[Event]` world.py:630-636 -> `derive(E.LEDGER_POSTED)`.
  `derive` is a no-op during replay (world.py:138-142): on load the ledger is rebuilt from the STORED LEDGER_POSTED
  events (`_h_ledger_posted`, world.py:612-614) while `Portfolio.cash`, positions, repos, loans etc. are rebuilt by
  RE-RUNNING the business handlers (which mutate state directly). If handler code changed between save and load,
  state and ledger disagree after load. `check_integrity` (world.py:180-203, called from `World.load` world.py:718)
  detects `ledger NAV != economic NAV` and only records it in `w.integrity["problems"]`. VERIFIED.
- `emit()` is NOT suppressed during replay (world.py:128-136); only `derive()` is. VERIFIED.
- `World.record_cash_movement(pf, ccy, amount, kind, reference, ev)` world.py:638-642: display log only.

### 2.3 Cash: two representations - VERIFIED
- `CashAccount(portfolio_id, currency, balance, accrued_interest, is_base, _base_value)` domain/models.py:188-204.
  `base_value` is a property: for the base currency it IS `balance` and the setter is a no-op (models.py:196-204);
  for foreign currencies `_base_value` is maintained separately. `Portfolio.cash_account(ccy)` creates on first use
  (models.py:599-602).
- Every engine handler that moves cash does BOTH `ca.balance += x` (and `ca.base_value += b`) AND posts `1010:<CCY>`.
  Examples: capital world.py:591-598; Treasury moves world.py:416-432; settlement settlement.py:162-183; accruals
  accruals.py:87-96, 110-119; repo repo.py:263-275, 296-309, 348-354, 368-387; prime prime.py:216-223, 240-245;
  investors investors.py:173-185, 204-215; corporate treasury treasury.py:149-158, 168-177. There is no single
  "move cash" function: about every engine re-implements it (prime candidate for the ONE accounting module).
- Ledger `1010:<CCY>` holds BASE units (e.g. world.py:422, 432, 598) while `CashAccount.balance` is LOCAL units.

### 2.4 Every backend place that computes NAV / P&L / cash / market value
1. `Ledger.nav()` ledger.py:242-245. VERIFIED
2. `PnLEngine.compute_summary(pf)` pnl.py:136-184 - economic NAV from state (pnl.py:172-173):
   cash_base (`pf.cash[*].base_value`, pnl.py:141) + position market values (pnl.py:142) + ledger receivables/
   margin/collateral/reverse repo/FX fwd/OTC/operating/underwriting - ledger payables/repo/margin loan/ccy loans/
   OTC liab/VM received; private credit from `pf.private_loans` par x price + accrued (pnl.py:156), NOT ledger
   1170/1175/1225; private equity from `pf.pe_companies[*].mark_value` (pnl.py:157), NOT ledger 1180/1185.
   Side effect inside a read: futures notional written back to `p.notional` (pnl.py:166-167). Also `ledger_nav`
   (pnl.py:176), `realized = 4000 + 4400` (pnl.py:180), exposures, `leverage = gross/nav` (pnl.py:183). VERIFIED
3. `PnLEngine.snapshot(cause)` pnl.py:186-240 - day P&L = sum of explain buckets from deltas of PNL_ACCOUNTS since
   the previous snapshot; `reconciles` = `nav - prev_nav - day_capital_flows == day_pnl` (pnl.py:236). Snapshots
   EVERY portfolio whoever calls it (pnl.py:188) - including `create_portfolio` (world.py:760), which therefore
   adds an extra same-date snapshot to every existing book. VERIFIED
4. `PnLEngine.mark_position` pnl.py:34-67: option marks from `w.options.quote` mid, others `market.last_bar(sid).close`
   via `Instrument.market_value`, foreign x `fx.k` (pnl.py:54-58). Marks come from the SIMULATED/stored daily bar,
   not a live quote. VERIFIED
5. `Service.dashboard` api/service.py:889-939 - `live_delta = nav - (last.nav or contributed) - day_capital_flows`,
   `day_pnl_live = last.day_pnl + live_delta` (service.py:895-896); MTD/YTD = sum(snapshot day_pnl) + live_delta
   (service.py:902-907); since-inception = nav - contributed (service.py:915, 920); income from ledger
   4200/4300/5000/5100 (service.py:924-925); collateral_posted adds pledges at market (service.py:928). VERIFIED
6. `Service.balance_sheet_rows` service.py:783-816 - adds a LIABILITY "Collateral received" (non-cash collateral
   held, service.py:812) that is NOT in compute_summary's NAV, so displayed assets - liabilities != displayed NAV
   whenever a book has a reverse repo (repo.py:272) or other received collateral. VERIFIED (lines); display INFERRED.
7. `Service.overall` service.py:818-887 - per-book `dashboard` + `compute_summary` again; `nav_with_treasury = nav +
   legacy pool` (service.py:860-862); consolidated balance sheet by summing per-book rows (service.py:869-879);
   per-book allocated = treasury_log TO_BOOK - TO_TREASURY (service.py:880-885). VERIFIED
8. `Service._treasury_state` service.py:740-753 - Treasury book cash per currency PLUS legacy pool `w.treasury_cash`
   (service.py:742-744); `total_usd = bal["USD"]` only (service.py:752). VERIFIED
9. Career metrics service.py:655-670 (Sharpe/drawdown/return from nav_history and contributed capital). VERIFIED
10. `PrimeEngine.financing` prime.py:43-68 (excess liquidity) and `affordable` prime.py:70-88, which adds the
    Treasury book's spare cash to every book's buying power (prime.py:76-77). VERIFIED
11. `World.spare_cash` = max(0, min(settled, trading.projected_cash)) world.py:463-465; `treasury_spare_for`
    world.py:467-472. VERIFIED
12. `TreasuryEngine.dashboard` treasury.py:105-126. VERIFIED
13. `InvestorEngine.process_day` fee on `compute_summary` NAV (investors.py:68-73); `_trailing` (investors.py:94-101). VERIFIED

### 2.5 The P&L categories - VERIFIED pnl.py:198-227
Price P&L per security = 4000 + 4100 + 4400 + 4800 + 4900 + 4910 (pnl.py:204), bucketed by `bucket_of`
(pnl.py:110-133) into equities / commodities / rates / credit / options / fx / crypto. Then `fx` = 4600 + 4650 +
4700 + fx bucket; dividends 4200; manufactured_dividends -5400; bond_interest (4300 on bonds); cash_interest =
4300 - bond - 5100; borrow_fees = 4350 - 5300 - 5700; repo_financing = 4500 - 5500; margin_financing = -5600;
fees = -5000 - 5800; lending_income = 4360 - 5310; fund_fees = -5900 - 5910; operating = 4980 - 5950;
storage = -5320; private_credit = 4320 + 4330 + 4340 - 5350; private_equity = 4370 + 4380 - 5360;
investment_banking = 4390 + 4395 - 5370.
GAP: 5150 "Currency Loan Interest Expense" is NOT in `PNL_ACCOUNTS` (pnl.py:21-22); if anything posts 5150 the
day explain misses it and `reconciles` is False that day. VERIFIED (list). 5150 IS posted on every currency-loan
accrual (ccy_funding.py:36, 176-177) - so on any day a currency loan is open, the NAV snapshot's explain omits the
loan interest and `reconciles` is False. VERIFIED.

---

## 3. Data flow per area (who calls whom, what state is read/written)

### 3.1 The save's Treasury book (world.py, api/service.py, api/server.py) - VERIFIED
- Identity: `World.TREASURY_TYPE = "TREASURY"`; `treasury_book()` = first portfolio with that type (world.py:395-398).
  Creation: `Service.create_world(..., treasury=True)` makes the save's first book "Treasury" with
  `cap = _capital(capital, job)` (service.py:148-152) - `_capital` returns the JOB'S STANDARD CAPITAL when capital is
  None/""/0/"0" (service.py:52-64), e.g. SANDBOX = 1,000,000,000 (careers.py:40). Later: `Service.create_portfolio`
  with `portfolio_type="TREASURY"` (service.py:332-334) goes through the fresh-capital path
  `cap = D(0) if capital == 0 else _capital(capital, job)` (service.py:343-344).
- HTTP: `POST /worlds/{id}/portfolios` passes `body.get("capital", 10_000_000)` (api/server.py:131). The current UI
  always sends the key (`capital: value ? +value : null`, static/app.js:277), so from the UI a blank becomes None ->
  job standard capital; a client that omits the key gets 10,000,000.
- Fresh capital: `World.contribute_capital` emits CAPITAL_CONTRIBUTED with `base_amount` (world.py:766-773) ->
  `_h_capital` (world.py:585-598): cash += amt, contributed_capital += base, day_capital_flows += base, posts
  Dr 1010:CCY / Cr 3000.
- Moves between books: `allocate_from_treasury` (world.py:495-509), `return_to_treasury` (world.py:511-525),
  automatic `treasury_backstop` (world.py:474-484) and `treasury_cover_overdrafts` (world.py:486-493), and book
  deletion returning cash (world.py:573-580) all emit TREASURY_ALLOCATED -> `_h_treasury_allocated`
  (world.py:436-447) -> `_move_capital(src, dst, ...)` (world.py:410-434): src cash -= amt, src contributed -= base,
  Dr 3000 / Cr 1010 in src; dst cash += amt, dst contributed += base, Dr 1010 / Cr 3000 in dst; `None` side = legacy
  pool `treasury_cash`. Every move is also appended to `w.treasury_log` (world.py:446-447).
- Automatic Treasury draws happen inside the DAY PROCESS: `settlement._check` -> `treasury_backstop` for a short RVP
  (settlement.py:94); `treasury_cover_overdrafts` twice per day (simulation.py:101 and 122).
- Buying power: `prime.affordable` counts the Treasury's spare cash for every other book (prime.py:76-77).
- Legacy pool: `World.treasury_cash` / TREASURY_FUNDED (world.py:69, 400-404) - nothing emits TREASURY_FUNDED any more
  (grep: only events.py:23 and world.py:214), so it exists only in old saves; it is added to the Treasury view
  (service.py:742-744) and to `overall` (service.py:860-864).

### 3.2 Corporate Treasurer job (engines/treasury.py) - VERIFIED
`TreasuryEngine.applies(pf)` = `pf.job == "TREASURY_MANAGER"` (treasury.py:52-53). `setup` (treasury.py:55-63) is
called from `create_portfolio` (world.py:752-753) or lazily from `process_day` (treasury.py:73-75); `_h_setup` posts
Dr 1900 operating assets / Cr 2380 long-term debt for 600,000,000 (treasury.py:30-34, 135-137; no cash). Monthly on
the 20th (rolled) `OPERATING_FLOW`: receipts EUR 9,000,000 / GBP 4,000,000 / JPY 900,000,000 x U(0.85,1.15) plus
base 6,000,000 x U(0.9,1.1) - 14,000,000 x U(0.95,1.05) (treasury.py:35-37, 77-82) -> `_h_flow` adds each leg to cash
and posts Dr 1010:CCY / Cr 4980 (treasury.py:139-158). Debt service from a coupon schedule (treasury.py:84-99,
`_h_debt` treasury.py:160-177). Note: the job is a property of a BOOK; a save created with job TREASURY_MANAGER and
`treasury=True` gives the Treasury book (type TREASURY) this job and therefore these flows. INFERRED from
service.py:152 passing `job` + treasury.py:52-53.

### 3.3 Settlement conventions and lifecycle (engines/settlement.py) - VERIFIED
- Cycles come from `SettlementConfig().cycles`, frozen into WORLD_CREATED (world.py:672, 267-268). Values
  (calendar.py:139-153, business days after trade date): US_EQUITY 1, US_TREASURY 1, US_CORP_BOND 1, US_MBS 1,
  EU_GOVT 2, UK_GOVT 1, JP_GOVT 1, US_MONEY_MARKET 0, EU/UK/HK/KR/TW/SA/CH/AU/JP_EQUITY 2, IN_EQUITY 1, FX_SPOT 2,
  FUTURES 0 (margined daily, no DVP), US_OPTIONS 1, PHYSICAL 2, CRYPTO 0, INDEX 0 (lines after 153 not read). VERIFIED.
  The class -> market mapping lives in trading/calendar code (not mapped here). Instructions are created by trading (SETTLEMENT_INSTRUCTION_CREATED
  -> `_h_created`, settlement.py:127-137); futures skip the lifecycle (settlement.py:47-48).
- Daily `process_lifecycle` (settlement.py:41-65): EXECUTED->CAPTURED->(3% seeded match break)->MATCHED->AFFIRMED->CLEARED
  ->SETTLEMENT_PENDING; SI PENDING->MATCHED. All by `emit` (settlement.py:118-124).
- Daily `process_due` (settlement.py:67-87): due SIs sorted receipts-first; PENDING on due date -> FAILED; `_check`
  (settlement.py:89-110): RVP short cash -> Treasury backstop -> prime `fund_settlement` (base ccy) -> for foreign ccy
  it lets the book go overdrawn if base cash covers it; DVP needs settled custody (options exempt).
- `_h_completed` (settlement.py:148-192): RVP: cash -= amount (foreign via `fx._adjust_cash`), settled_quantity += q,
  Dr 2100 / Cr 1010 (+ 4650 realised FX); DVP: cash += amount, settled_quantity -= q, Dr 1010 / Cr 1200. Trade-date
  accounting: cost and payable/receivable were booked at execution (trading.py, not mapped).
- A trade only becomes SETTLED inside the day process: with no day advance, nothing ever settles (cash stays in
  2100/1200 and `settled_quantity` never moves). VERIFIED (only caller simulation.py:99-100).

### 3.4 Accruals (engines/accruals.py) - VERIFIED
`process_day(cause, prev_date)` (accruals.py:30-72): bond accrued interest trued up to `BondPricer.accrued_per_100`
(INTEREST_ACCRUED BOND -> Dr 1220 / Cr 4300, accruals.py:79-85); base-cash interest at policy - 25bp on positive
balances only, Act/360 x calendar days since `prev_date` (accruals.py:47-60) -> Dr 1230 / Cr 4300 accrued, settled on
the first processed day of a new month (accruals.py:68-72, 106-119); foreign cash at that currency's rate +/- spread,
PAID DAILY into cash (accruals.py:61-67, 86-96). Weekend: Friday->Monday accrues 3 days (days = (today - prev).days).

### 3.5 Repo / reverse repo (engines/repo.py) - VERIFIED
- REPO (borrow cash): `_h_opened` pledges collateral (derive COLLATERAL_PLEDGED via collateral.pledge), cash +=
  principal, Dr 1010 / Cr 2500 (repo.py:261-268). REVERSE (lend cash): cash -= principal, Dr 1500 / Cr 1010; the
  received collateral is recorded only in `pf.collateral_received[repo_id]` (off-ledger) (repo.py:269-276).
- Daily (repo.py:167-199): interest accrual (REPO Dr 5500 / Cr 2330; REVERSE Dr 1240 / Cr 4500, repo.py:278-289);
  overnight auto-roll pays accrued interest in cash (repo.py:180-184, 311-320); maturity -> `close` -> `_h_closed`
  pays interest, repays/receives principal, releases pledges, returns cash margin (repo.py:361-389); marks/calls
  (repo.py:201-228); unmet call after 1 cycle -> counterparty reduces/closes (repo.py:190-199).
- Principal is returned exactly once: `close()` refuses a non-OPEN repo (repo.py:96-98) and `_h_closed` sets CLOSED
  (repo.py:388). No double-return path found. VERIFIED.
- `quote()` picks the counterparty with Python `hash()` of a tuple containing strings (repo.py:55) - salted per
  process, so the quoted counterparty differs between runs (stored in the event, so replay is unaffected). VERIFIED

### 3.6 Prime broker (engines/prime.py) - VERIFIED
- MARGIN_LOAN_CHANGED -> `_h_loan`: cash += amt, `pf.margin_loan += amt`, Dr 1010 / Cr 2700 or reverse (prime.py:210-223).
- Daily sweep (prime.py:136-145): negative base cash -> automatic draw; positive cash minus pending RVP cash ->
  automatic repayment. Interest accrued daily Dr 5600 / Cr 2340, paid monthly (prime.py:130-135, 225-245).
- Margin call when excess liquidity < 0; forced liquidation after one cycle (prime.py:146-207).
- `pf.margin_loan` (state) and ledger 2700 are two copies of the same number; compute_summary uses the ledger
  (pnl.py:151), financing() uses the state field (prime.py:64). VERIFIED

### 3.7 Collateral (engines/collateral.py) - VERIFIED
Haircut schedule (collateral.py:28-47), encumbrance registry `pf.pledges` (available = settled - pending deliver -
pledged - covered-call reserve, collateral.py:104-111). `pledge`/`release`/`move_cash` use `derive` with an explicit
replay comment (collateral.py:122-141) - replay-safe. `_h_cash_moved`: cash -= amt, `pf.cash_collateral[ref] += amt`,
Dr 1400 / Cr 1010 (collateral.py:228-244). Calls are one object, emitted with `emit` from the daily process
(collateral.py:147-161). `pledge()` re-validates availability and raises CommandError even while it runs inside a
replayed handler (collateral.py:113-121 called from repo.py:262, seclending.py:603) - a validation that fails on
replay turns into a ReplayError. INFERRED risk.

### 3.8 Securities borrowing and lending (engines/seclending.py) - VERIFIED for the parts read
- Borrow side (short sales): `request_locate` -> `borrow(pf, locate_id, quantity, collateral_type)` (seclending.py:85-120)
  -> SECURITY_LOAN_OPENED -> `_h_opened` adds `settled_quantity` and `borrowed_quantity`, posts cash collateral 102%
  via `collateral.move_cash` (Dr 1400 / Cr 1010) or pledges Treasuries (seclending.py:294-322). Borrow fees accrue
  daily, settle monthly or on return (`_h_fee_settled` seclending.py:375-397; `_h_returned` derives BORROW_FEE_SETTLED,
  seclending.py:438-441). Constants: COLLATERALIZATION 1.02, RECALL_DAYS 2, BUY_IN_PENALTY 1%, HOLD_DAYS 5
  (seclending.py:24-27). Locates are valid through the NEXT BUSINESS DAY of `w.current_date` (seclending.py:66-83) and
  `borrow` rejects a locate whose `valid_through < current_date` (seclending.py:93-94): with no day advance,
  `current_date` never moves, so locates would never expire. Daily (seclending.py:142-173): re-rate to the lending
  market, fee accrual Dr 5300 / Cr 2310 and rebate Dr 1410 / Cr 4350 (seclending.py:358-373), collateral marks
  (seclending.py:175-198, 330-356, see B11), seeded random lender recalls (seclending.py:199-210), buy-in after
  RECALL_DAYS with a 1% cash penalty Dr 5700 and a forced BUY (seclending.py:212-227, 443-457), automatic return of
  excess borrow once covered and settled (seclending.py:229-258), monthly fee settlement (seclending.py:260-265,
  375-397). `_h_returned` reduces `settled_quantity`/`borrowed_quantity` and returns collateral pro rata
  (seclending.py:405-441).
- Lend side (`LendDeskEngine`, jobs SEC_LENDING/SANDBOX/HEDGE_FUND/PORTFOLIO_MANAGER/FIXED_INCOME_PM per service.py:698):
  `lend` (seclending.py:493-518) -> SECURITY_LENT -> `_h_lent`: pledges the lent shares as "LENT", and the book RECEIVES
  cash collateral = market value x LEND_COLLATERAL (1.02, seclending.py:471) into its CASH, Dr 1010 / Cr 2460
  (seclending.py:595-610). Daily
  marks move that cash up/down (`_h_marked`, seclending.py:612-625); return pays it back (`_h_returned`,
  seclending.py:664-687). Fees: Dr 1430 / Cr 4360, rebate Dr 5310 / Cr 2370 (seclending.py:627-637), settled monthly
  (seclending.py:639-656). So lending inventory raises the book's cash balance with no new equity (the liability is in
  2460, inside "payables").

### 3.9 Currency loans = the existing "cash borrowing" (engines/ccy_funding.py) - VERIFIED
`borrow(pf, ccy, amount, term_days, tag)` (ccy_funding.py:79-100): capacity = 2 x NAV less outstanding
(`capacity`, ccy_funding.py:74-76; constant CAPACITY_OF_NAV not read); emits CCY_LOAN_OPENED -> `_h_opened`: cash via
`fx._adjust_cash` (+principal in that currency), Dr 1010:CCY `received` / Cr 2750 `base` (ccy_funding.py:153-165).
The entry balances because `fx._adjust_cash` returns the `base_value_hint` for an inflow (engines/fx.py:212-224);
see B10. VERIFIED. Daily: interest Dr 5150 / Cr 2755 (ccy_funding.py:
136-139, 167-177), retranslation Dr 4600 / Cr 2750 (ccy_funding.py:140-143, 179-187), maturity repay-or-roll
(ccy_funding.py:144-150). `repay` (ccy_funding.py:102-124, `_h_repaid` 189-207) - partial repayment pays no interest.
`pf.ccy_loans[*].base_value` and ledger 2750 are two copies of the principal.

### 3.10 Private credit (engines/private_credit.py) - PARTIAL (commands, daily process, cash handlers read)
`commit(pf, deal_id, amount)` (private_credit.py:158-183): min commitment, 100,000 multiples, `prime.affordable` check
(which counts the Treasury's spare cash); funds IMMEDIATELY - no settlement cycle. Initial price = 1 - OID
(private_credit.py:330), so economic value par x price equals ledger 1170 - 1175 at funding.
`sell` at `bid` = mark less a regime haircut (private_credit.py:185-207).
Commit funds par less OID: cash -= funded, Dr 1170 par / Cr 1010 funded / Cr 1175 discount (private_credit.py:319-341);
coupons accrue Dr 1225 / Cr 4320 and are paid into cash (private_credit.py:343-370); marks (372-384), amendments fee
into cash (392-407), default/recovery/sale/maturity cash flows (410-506). compute_summary values the book from
`pf.private_loans` (par x price + accrued, pnl.py:156) rather than the ledger accounts - a second copy.

### 3.11 Investors (engines/investors.py) - VERIFIED
`process_day` (investors.py:58-82): seeds 6 holders from contributed capital (investors.py:84-92); daily mgmt fee
accrual on NAV (Dr 5900 / Cr 2360); notices from the 15th (investors.py:103-125); month-end `_deal` (investors.py:
127-140): SUBSCRIPTION -> emits plain "CAPITAL_CONTRIBUTED" (handled by `World._h_capital`), REDEMPTION ->
CAPITAL_WITHDRAWN (`_h_withdrawn`, investors.py:168-185, removes the pending item and reduces the holder).
Fees paid in cash monthly / at year end (investors.py:79-82, 142-151, 198-215).

### 3.12 Clients, counterparties, lending market - PARTIAL (docstrings and grep only)
`clients.py` (job flow RFQs, `applies` = CLIENT_JOBS, clients.py:51-52) has no direct cash/ledger code (grep found no
`ca.balance`/`w.post`). RFQs arrive and resolve only in `process_day` (clients.py:55, called at simulation.py:93),
expire the next business day (clients.py:~103), and a WON quote is booked through `trading.system_order` (blocks) or
`otc.open_client_trade` (clients.py:~155-177) - so its cash follows the trading/OTC settlement paths. `counterparties.py` = dealer credit + ISDA/CSA terms
(docstring, counterparties.py:1-12); no cash code. `lending_market.py` = per-stock borrow supply/rates/specials
(docstring, lending_market.py:1-9); no cash code.
- counterparties.py: `DealerSpec` (counterparties.py:26-39), quote widths `HALF_WIDTH`/`REGIME_WIDTH`
  (counterparties.py:66-77), per-dealer ISDA/CSA `CSA_TERMS` threshold / MTA / initial-margin rates
  (counterparties.py:80-88), extended at IMPORT time by a module-level loop that mutates the dict (counterparties.py:
  92-94), `CLIENT_CSA` (counterparties.py:183), `DealerModel.step(d, regime, hy_bps, mkt)` = a seeded per-date random
  walk of each dealer's CDS/rating/stress (counterparties.py:115-149), `ingest` for replay (150-155), `mark_defaulted`
  (160-163). VERIFIED (lines read). The dealer credit model is SIMULATION and only advances with the day.
- lending_market.py: `LendingState`, `LendingMarket.step(d, securities, regime, realized_vol, player_on_loan,
  weekend_days)` = seeded per-date random walk of other borrowers' demand, utilisation, rate and specials
  (lending_market.py:49-90), `ingest` (91-102), `available` (103-108), `lender_for` (109-110). Borrow rates for
  locates, fees and the lend desk all come from this simulated state (seclending.py:72-82, 151-153, 499-506).
  VERIFIED (lines read). SIMULATION; advances only with the day. Bodies of step() beyond the lines cited NOT READ.

---

## 4. FINDINGS FOR THE REPAIR

### 4.1 Suspected bugs (exact evidence)
B1. HEDGE-FUND SUBSCRIPTIONS ARE RE-DEALT EVERY MONTH END (cash appears repeatedly). `_deal` iterates
    `st["pending"]` and emits CAPITAL_CONTRIBUTED for every SUBSCRIPTION whose dealing date has passed
    (investors.py:131-137) but nothing ever removes a subscription from `pending`: the handler is the generic
    `World._h_capital` (world.py:585-598), which does not touch `pf.investors`; only redemptions are removed
    (investors.py:182). Every later month end re-credits every past subscription (fresh CAPITAL_CONTRIBUTED,
    contributed capital and cash both rise). Holders are never increased on subscription either. VERIFIED.
B2. Failed command = half-applied state + events persisted later. `emit` appends to `events` and `_pending`
    BEFORE running handlers (world.py:128-136, 144-150); if a handler raises (e.g. `Ledger.post` LedgerError,
    `collateral.pledge` CommandError inside a handler), the exception propagates but the event(s) stay in `_pending`
    and the next successful command's `flush()` (world.py:152-155) writes them. The save then contains an event whose
    replay fails -> strict load raises / resilient load records `replay_errors` and skips its effects. Worse, the
    financing commands go through `World._cmd`, which calls `flush()` in a `finally:` (world.py:837-841) - so the
    events of a command that FAILED half-way are written to the save at once (repo, prime, seclending commands,
    world.py:843-912). VERIFIED mechanism; specific triggering cases INFERRED. (Directly relevant to "trades
    disappear after load" and to "atomic save/load".)
B3. `Ledger.post` has no duplicate-entry guard (ledger.py:198-216). VERIFIED.
B4. `CHART` duplicate key "2800" (ledger.py:51 vs 54). VERIFIED.
B5. 5150 missing from PNL_ACCOUNTS (pnl.py:21-22) while ccy_funding posts it (ccy_funding.py:176-177): daily
    explain omits currency-loan interest; `reconciles` False. VERIFIED.
B6. Balance sheet "Collateral received" liability (service.py:812) is not part of NAV -> displayed sheet does not
    tie (assets - liabilities != NAV row) for books with reverse repos or lent-against-securities collateral.
    VERIFIED lines; display INFERRED.
B7. `compute_summary` writes `p.notional` (pnl.py:166-167) - a read path mutating state. VERIFIED.
B8. `create_portfolio` calls `pnl.snapshot(cause)` (world.py:760) which snapshots EVERY book (pnl.py:188), adding
    an extra same-date NAV snapshot to all existing books and resetting their `day_capital_flows` (pnl.py:254).
    VERIFIED.
B9. `day_capital_flows` is reset in two places: `_h_snapshot` (pnl.py:254) and `_h_day_closed` (world.py:625); the
    dashboard's `live_delta` (service.py:895) relies on it. VERIFIED.
B10. CHECKED, NOT A BUG: `ccy_funding._h_opened` debits 1010 with `received` and credits 2750 with `base`
    (ccy_funding.py:163-165); `fx._adjust_cash` returns the `base_value_hint` for an inflow (engines/fx.py:212-224,
    line 216), so the entry balances. VERIFIED.
B11. SEC-BORROW CASH TOP-UP REPEATS DAILY. For a borrow collateralised with Treasuries, `_mark` measures only the
    pledged securities (`value`, seclending.py:186-189) and ignores earlier cash top-ups; when Treasuries run out,
    `_h_marked` posts a fresh cash top-up `required - collateral_value` every day (seclending.py:349-353) without
    adding it to `loan.collateral_amount`. Cash collateral (1400) piles up daily, and all of it comes back to cash in
    one lump only when the loan is fully returned (seclending.py:433-435). Cash falls daily, then jumps on return.
    VERIFIED.
B12. RESILIENT REPLAY BY DEFAULT: `Service.strict_replay` is False unless env FINSIM_STRICT_REPLAY=1 (service.py:74);
    a failing event on load is skipped and only logged (world.py:172-176, service.py:267-268). With B2 this is a
    direct mechanism for "things vanish after a restart". VERIFIED (mechanism); trade-specific cases belong to the
    trading map.
B13. EVERY API READ CAN PROCESS DAYS: `Service.world(world_id)` calls `w.catch_up(at)` by default (service.py:258-273),
    so for a REAL_TIME save any request after the update time runs the whole day process (interest, fees, investor
    deals, career capital, Treasury backstops, settlements) - including after a weekend. VERIFIED.

### 4.2 Duplicate computations (candidates for the ONE accounting module)
- Cash: `CashAccount.balance/_base_value` vs ledger `1010:<CCY>` - maintained in parallel by ~15 handlers (2.3).
- NAV: `Ledger.nav()` vs `compute_summary()["nav"]`; both returned side by side (pnl.py:172-176) and compared only on
  load (world.py:192-194).
- Day/MTD/YTD/since-inception P&L: snapshot explain (pnl.py:186-240) vs dashboard live_delta (service.py:895-907) vs
  overall sums (service.py:835-837) vs careers benchmark/returns (careers.py:441-442) vs investors trailing
  (investors.py:94-101).
- Margin loan: `pf.margin_loan` vs ledger 2700. Currency loans: `pf.ccy_loans[*].base_value/accrued_base` vs ledger
  2750/2755. Private credit: `pf.private_loans` par x price vs ledger 1170/1175/1225. Private equity:
  `pe_companies[*].mark_value` vs ledger 1180/1185. Cash collateral: `pf.cash_collateral` vs ledger 1400. Lending:
  `pf.lends[*].collateral` vs 2460. Repo: `r.principal` vs 2500/1500. Contributed capital: `pf.contributed_capital`
  vs ledger 3000.
- Balance sheet: `balance_sheet_rows` (service.py:783-816) rebuilds from compute_summary instead of the trial balance.
- Treasury totals: `_treasury_state` (service.py:740-753) vs `overall` (service.py:859-864) vs per-book `allocated`
  re-summed from `treasury_log` (service.py:880-885).

### 4.3 Everything tied to the simulated day advance (must be replaced for "live prices, no simulation")
- The day machine: `World.advance` (world.py:1079-1097), `World.catch_up` (world.py:1099-1115),
  `SimulationEngine.run_daily_process` (engines/simulation.py:34-140, called from `World.create`, world.py:702-703),
  `market.generate_day` (simulation.py:40) -> MARKET_CLOSE -> `_h_market_close` sets `current_date` (world.py:600-610).
- In my area, ONLY the day process moves these: settlement lifecycle + DVP (simulation.py:99-100); Treasury backstops
  (simulation.py:101, 122); repo interest/rolls/maturities/marks/calls (simulation.py:112); currency-loan interest,
  translation, maturity (simulation.py:113); private credit accrual/coupons/marks/defaults (simulation.py:114);
  seclending fees/marks/recalls/buy-ins and lending-desk fees/marks/returns (simulation.py:110-111); prime sweep,
  interest, calls, forced liquidation (simulation.py:123); cash and bond accruals (simulation.py:124); corporate
  treasurer flows and debt service (simulation.py:126); investors (simulation.py:127); marks + NAV snapshot
  (simulation.py:120, 128, 131); careers missions/reviews and their capital bonuses (simulation.py:134-135).
- Interest math everywhere uses `days = (today - prev_date).days` (accruals.py:33, repo.py:170, prime.py:126,
  ccy_funding.py:130, investors.py:69, seclending.py:537) - needs a wall-clock accrual basis.
- Marks use `w.market.last_bar(sid).close` (pnl.py:54) and option model mids (pnl.py:46-47) - the simulated/stored
  daily bar, not a live quote.
- REAL-market saves use the SAME day machine: `advance`/`catch_up` first ask `real_market_ready(next_business_day)`
  (world.py:1086, 1105, 342-363), which waits for the 16:00 New York close and refreshes the network feed
  (`feed.refresh("1mo")`, world.py:353-356); the day's bar is then the real close. `switch_to_real` (world.py:293-311)
  turns a simulated save into a real one via MARKET_SOURCE_CHANGED + REAL_HISTORY_LOADED events. So even "live" saves
  only settle, accrue, mark and snapshot once per processed session; live quotes/tickets (world.py:775-823, not mapped
  here) do not drive any of the accounting in this area. VERIFIED (lines read); live-ticket internals NOT READ.
- Seeded randomness in accounting-relevant flows: settlement match breaks (settlement.py:52-55), corporate-treasurer
  operating flows (treasury.py:79-81), investor flows (investors.py:85-92, 106-123), lend-desk borrower and early
  returns (seclending.py:513, 545-547).

### 4.4 Surprising things
- "Treasury" means two different things: the save's Treasury BOOK (world.py:395) and the Corporate Treasurer JOB
  engine (`w.treasury`, world.py:95). The names collide in code (`w.treasury` is the job engine; the book is
  `w.treasury_book()`).
- A book's JOB drives engines (investors, treasury flows, client RFQs, lending desk, missions, reviews, promotions),
  and the Treasury book is created with the save's job (service.py:152), so the Treasury book itself can receive
  investor subscriptions, mission bonuses, promotion capital and corporate operating flows. VERIFIED (code paths);
  whether it happened in the owner's save INFERRED.
- The Treasury book's own overdrafts are never backstopped (excluded at world.py:489-490) and become prime-broker
  margin loans (prime.py:137-139). VERIFIED.

---

## 5. Candidate causes of the ~10,000,000 unexplained Treasury cash (ranked by fit)

Treasury view = Treasury book cash per currency + legacy pool, USD headline `total_usd` (service.py:740-753).
Anything that raises the Treasury book's USD cash, or adds to the legacy pool, shows here.

C1. HARD-CODED 10,000,000 DEFAULTS - fresh capital instead of a draw. `World.create_portfolio(..., capital=D(10_000_000))`
    (world.py:732) and `POST .../portfolios` default `body.get("capital", 10_000_000)` (api/server.py:131). A Treasury
    book created through the API without a `capital` key, or any caller using the World default, receives a
    CAPITAL_CONTRIBUTED of exactly 10,000,000 (world.py:746-747). Also `demo.py:54` creates a 10,000,000 book.
    Exact amount match. VERIFIED defaults; occurrence INFERRED.
C2. A 10,000,000 BOOK DELETED INTO THE TREASURY. Deleting a book sends its whole cash to the Treasury as
    TREASURY_ALLOCATED TO_TREASURY (world.py:573-580). A book that was funded with FRESH capital (C1, or the 10,000,000
    UI/API default, or `from_treasury` unticked) never drew from the Treasury, so the Treasury gains cash and
    contributed capital it never sent (logged only as "book closed"). VERIFIED path; occurrence INFERRED.
C3. XCCY notional exchanges with the ticket default USD notional 10,000,000 (static/app.js:648; default
    direction = first option BORROW_FOREIGN). LEND_FOREIGN: the book RECEIVES USD 10,000,000 now and pays the
    foreign notional (otc.py:757-758); BORROW_FOREIGN: pays USD 10,000,000 now and receives it back at maturity
    (otc.py:755-756 and the final exchange otc.py:1040-1044). Either way the USD-only "Treasury" tile
    (`t.treasury` = `total_usd`, service.py:752, 861; static/app.js:1877) moves by exactly 10,000,000 while the
    offsetting foreign-currency balance is not in that tile. If the swap was dealt in the Treasury book (or a book
    whose USD shortfall the Treasury then backstopped, world.py:486-493), exact match. VERIFIED lines (otc.py read
    only around these lines; OTC is not otherwise mapped here).
C4. HEDGE_FUND SUBSCRIPTIONS RE-CREDITED EVERY MONTH END (bug B1, investors.py:131-137 + world.py:585-598). If the
    save's job is HEDGE_FUND and the Treasury book carries that job (service.py:152), the Treasury receives the
    same subscriptions again every month end. Amounts are holder x score (investors.py:118-120) - easily ~10M on a
    500M fund. Also hits any HEDGE_FUND book whose cash is later returned to the Treasury. VERIFIED bug; amount INFERRED.
C5. CAREER CAPITAL AUTOMATICALLY ADDED by the day process to any book with a non-SANDBOX job, including the
    Treasury book: mission bonus = 5% of job capital (careers.py:389-393; e.g. 10,000,000 for a 200,000,000 job),
    promotion = job capital x capital_step (careers.py:537-542). Both are CAPITAL_CONTRIBUTED without `base_amount`.
    VERIFIED code; amount match INFERRED.
C6. CORPORATE-TREASURER OPERATING FLOWS if the Treasury book's job is TREASURY_MANAGER: monthly EUR ~9,000,000
    (x0.85-1.15), GBP ~4,000,000, JPY ~900,000,000 receipts credited to cash with no capital event
    (treasury.py:35-37, 77-82, 139-158). "About 10M" fits EUR 9M x ~1.1. VERIFIED code; INFERRED occurrence.
C7. LEGACY POOL DOUBLE-PRESENTED: an old save's `treasury_cash` (TREASURY_FUNDED) is added on top of the Treasury
    book's cash in the view (service.py:742-744) and in `overall` (service.py:860-864). If the old pool was later
    effectively moved into the book (or represents the same money), it shows twice. VERIFIED presentation; INFERRED.
C8. CASH COLLATERAL / BORROWED CASH COUNTED AS CASH: repo borrowing cash (repo.py:263-264), securities-lending cash
    collateral received at LEND_COLLATERAL x MV (seclending.py:604-606, marked daily 619-622), prime margin loan draws
    (prime.py:216-219, automatic sweep prime.py:137-139), currency loans (ccy_funding.py:163). All raise CASH with an
    offsetting liability that the Treasury cash view does not show. VERIFIED mechanics; INFERRED as the cause.
C9. CAPITAL DEFAULT WHEN THE FIELD IS BLANK/0: `_capital` treats None/""/0/"0" as "job standard" (service.py:55-59);
    `create_world` therefore funds the Treasury with the job's standard capital even when the user entered 0
    (service.py:148-152) - the amount would be the job capital (1B sandbox, 100M PM, ...), not 10M. VERIFIED.
C11. OLD-SAVE FOREIGN-CURRENCY CAPITAL BOOKED ONE-FOR-ONE: a TREASURY_ALLOCATED or CAPITAL_CONTRIBUTED event without
    `base_amount` (saves before 2026-09-24) books the LOCAL amount as the dollar value (world.py:412-414, 590), so e.g.
    JPY or EUR moved into/out of the Treasury carries a wrong `base_value` and a wrong 3000/1010 posting forever after.
    Distorts NAV/base totals rather than the USD tile. VERIFIED code; relevance to the owner's save INFERRED.
C10. Items checked and NOT found as causes: private-credit principal returned twice (PC_MATURED once, then status
    MATURED is skipped, private_credit.py:217-218, 238-241, 488-506; a same-day coupon empties `accrued_interest`
    before the maturity payload is built, private_credit.py:229-241); repo principal returned twice (guarded, repo.py:96-98, 388); reverse-repo
    collateral counted as own cash (it is off-ledger in `collateral_received`, not in cash: repo.py:272); variation
    margin posted then settled again (futures.py not in my area - NOT CHECKED); settlement posted twice (`_h_completed`
    sets SETTLED and `process_due` only takes PENDING/MATCHED/FAILED, settlement.py:71, 185 - no double found);
    opening capital re-applied on load (`create_portfolio`'s contribution is a stored CAPITAL_CONTRIBUTED replayed
    once; `derive` is suppressed on replay; no handler in my area calls `emit` - checked by script over all area files).

Suggested ledger-integrity check for the repair (from this map): per book assert
(a) trial balance balances; (b) for every currency, `pf.cash[c].base_value == ledger 1010:c`; (c) `pf.margin_loan ==
2700`; (d) `sum(open repo principal) == 2500 / 1500`; (e) `sum(ccy loan base) == 2750`; (f) `pf.contributed_capital ==
3000`; (g) `Ledger.nav() == compute_summary nav`; (h) Treasury: sum of all books' contributed capital == sum of
CAPITAL_CONTRIBUTED - CAPITAL_WITHDRAWN events (allocations net to zero).

---

STATUS: PARTIAL - not yet read: engines/seclending.py lines 1-23, 28-46 (imports, events registration);
engines/private_credit.py lines 1-157, 246-318,
372-433 (deal pipeline, reviews/defaults, marks; cash/ledger lines were grepped); engines/clients.py (only grepped),
engines/counterparties.py lines 13-25, 40-62, 98-114, 125-149 (rest), 156-159, 165-195 and engines/lending_market.py
lines 10-48, 60-110 (bodies of the steppers; signatures and cash-freeness checked);
engines/ccy_funding.py lines 1-35 (TIER/CAPACITY constants); engines/collateral.py lines 41-103 (rest of the haircut
table, regime multiplier); world.py lines 1-39, 366-379, 780-824, 861-895, 916-1078, 1120-1179; api/service.py outside lines 48-167,
258-275, 320-359, 655-955; engines/fx.py, futures.py, otc.py, trading.py, careers.py, simulation.py (outside the
lines cited) are other areas.
