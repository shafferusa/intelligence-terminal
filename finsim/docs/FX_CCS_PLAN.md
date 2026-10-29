# FinSim1 FX trades with Goldman Sachs: EUR/GBP 24M deliverable forward and EUR/USD ESTR / SOFR cross-currency swap (design, build packages, auto-booking)

Designer plan, revision 2, written 2026-10-28 19:10-20:30 EDT by the designer of finsim-fx-ccs-trades-design (wf_44a28643-3a3).
Revision 1 (18:15 EDT) went to the independent critic. Each of its 19 findings (FX-C-01..FX-C-19) was verified; the body carries the
fixes and section 15 answers each finding in one line. Status: revised plan for the orchestrator.

Read in full before writing:
- `docs\OWNER_FX_TRADES_2026-10-28.md` (the owner's instruction, verbatim) and `docs\OWNER_DIRECTIVE_FINSIM1_2026-10-27.md`;
- `docs\FINSIM1_PLAN.md` sections 3, 5, 12 and 15 (15.15 is the owner's "OTC market is bad for forwards and cross currecny stuff";
  15.15b hands the audit's redesign items to this build);
- `docs\OTC_FX_XCCY_AUDIT_2026-10-28.json` (23 findings; section 1.5 maps every one);
- the research result of this workflow (17:41-17:54 EDT) and the critic's findings (18:44-19:08 EDT);
- the code of the 1.0.6 working tree (read-only).

This file is the only file written. Revision 2's worked numbers were recomputed with exact Decimal arithmetic (precision 34) in
`scratchpad\fxd\scripts\common4.py`, `worked4.py`, `extra4.py` and `dates4.py`, on the 2026-10-27 rows saved in
`scratchpad\fxd\boe_20261027.json` (the Bank of England OIS row) and `scratchpad\fxd\dl\ecb_sr.csv` (the ECB AAA spot rates). Revision
1's scripts in `scratchpad\fxccs-design\` are superseded.

**What changed from revision 1.**
- GBP is priced on the Bank of England OIS curve, which is observed: the Bank serves the terminal's honest project agent. EUR is priced on
  the ECB AAA government curve as an OIS proxy plus a disclosed GS schedule spread. USD uses the IRS desk's Treasury curve. Flat overnight
  curves survive only as a cross-check (FX-C-01, FX-C-02).
- The EURGBP spot is the cross derived from the two USD pairs; the cross print is stored beside it with the difference. Every worked
  number and money test is re-pinned on one consistent fixture (FX-C-03).
- The dealer quotes the CCS at its par spread plus the half-width; the par spread prices the swap's own conventions. Every mark is a full
  valuation for the spot date (D4, D19).
- Risk hooks return only the off-ledger part (FX-C-06). The CSA exposure is the full (dirty) value (FX-C-16). A CCS terminates in USD cash
  (FX-C-07). VM keys survive NULL trade dates (FX-C-08).
- The booking program re-derives every price with its own pricer (FX-C-05). The checker attributes each change to the command that made
  it instead of freezing rows (FX-C-04). The idempotency edge cases are closed (FX-C-13).
- The other changes:
  - the 23 audit findings are mapped (FX-C-09); notional limits are disclosed (FX-C-10); the forward's basis is recorded (FX-C-11);
  - the forward check on an owner-database copy is mandatory and has a START runbook (FX-C-12);
  - the labels and calendars are corrected (FX-C-14, FX-C-15); data robustness (FX-C-18);
  - the 2028 funding gap is stated as a number (FX-C-19); the otcplan test server is P-D's own (FX-C-17).

Spelling in this file:

| Term | Meaning |
|---|---|
| ESTR | the euro short-term rate (the ECB's "€STR") |
| SONIA | the Bank of England's sterling overnight index average |
| SOFR | the NY Fed's secured overnight financing rate |
| RFR | an overnight risk-free rate (SOFR, ESTR, SONIA) |
| OIS curve | a term curve of overnight-index-swap rates |
| cc | continuously compounded |
| CCS | cross-currency swap |
| SFI | standing settlement-funding instruction (section 4.3) |
| BD | business day |
| MF | modified following |
| CTM | collateralised-to-market |
| PvP | payment versus payment (both legs settle in one step) |
| USGS day | a US government securities business day (SIFMA's calendar): SOFR and the SOFR Index are published on these days only |
| GOV_UA | the terminal's honest project agent, "LoganTerminal/1.0 (loganshaffer87@gmail.com)" (`config.GOV_UA`) |
| TERMINAL_UA | the terminal's generic honest agent, "FinSimTerminal/1.0 (paper trading; python stdlib)" |

## 0. Summary

### What gets built and booked

**Trade A, a EUR/USD cross-currency swap with Goldman Sachs (F1:PB_GS).**
- The Fund pays EUR on compounded ESTR plus the dealt spread and receives USD on compounded SOFR flat, quarterly, ACT/360.
- The dealt spread is the dealer's par spread for this swap plus its 1.0 bp half-width, rounded up on the 0.1 bp grid.
  - The par spread follows from the curves, the disclosed basis level (0.0 bp, ASSUMED) and the swap's own conventions.
  - In the example it is -0.92 bp, so the Fund pays ESTR + 0.1 bp (D4).
- Notionals: EUR 2,000,000 against USD at the deal rate. Term: 24 months from the spot date.
- Initial and final exchanges at the deal rate, with no notional reset.
- The swap on its own is EUR-flat: it receives EUR 2,000,000 at the start and owes it back at maturity.
  - So, as the instruction asks, a separate, audited FX spot deal sells the EUR received at the start for USD: the "inception
    conversion".
  - The conversion is booked in the same command as the swap and settles PvP with the start exchange.
- Net position: short EUR 2,000,000 (owed at maturity), long USD 2,231,600 (lent, returned at maturity), plus the USD-minus-EUR rate
  carry.

**Trade B, a EUR/GBP deliverable forward with Goldman Sachs.**
- SELL EUR 2,000,000 / BUY GBP, value date = spot + 24 months.
- Dealt at the covered-interest-parity outright less the GS forward half-width:
  - the spot is the EURGBP cross derived from EURUSD and GBPUSD (the cross print is stored with its difference);
  - GBP is discounted on the Bank of England's UK OIS curve (observed);
  - EUR is discounted on the ECB euro-area AAA government curve, used as the EUR OIS proxy, plus a GS schedule spread (0.0 bp, ASSUMED);
  - the EUR/USD and GBP/USD basis levels (GS schedule terms, 0.0 bp each, ASSUMED) sit on both discount curves.
- Both currencies are delivered on the value date.

**How they are booked.** An idempotent booking program books both through the 1.0.6 RFQ desk: request, simulated dealer quote, accept.
- Every quote stores its inputs with their source and time:
  - the spot prints and the derived cross;
  - each curve with its as-of date and the grid points used, and the overnight prints;
  - the discount factors and forward points;
  - the basis levels, the dealer terms and the method.
- Before accepting, the program re-derives every price from those inputs with its own pricer and accepts only on exact equality (10.2).
- IM follows the GS schedule. VM is daily CTM on the CSA exposure (the full value) under the existing CSA.
- The full lifecycle runs:
  - the start exchange;
  - the coupons, set from the published compounded indices;
  - the final exchange and the forward delivery;
  - an audited EUR purchase before every EUR obligation (the SFI).

**What must not change.** The owner's ORCL / META / MU / USO TRS, CDX IG / HY, USD IRS, equities, futures and options stay as they are.
The checker proves it by tracing every row written after its snapshot to the command that wrote it (section 10.3).

### Release, hours and the worked example

**Release.** 1.0.6.1, built by 4 builders (packages P-A..P-D, section 8) on the 1.0.6 tree once its gate has committed it.
- The 1.0.6 fixers are still editing `otc_options.py`, `swaps_trs.py`, `fx.py`, `risk.py`, `otcticket.js` and `swapticket.js` for 15.15.
- The single-name CDS plan (1.0.7) rebases on 1.0.6.1. The files both plans touch are `swaps.py`, `prime.py`, `risk.py` and
  `swapticket.js`.

**Hours.** About 17-22.25 h of wall clock from the 1.0.6 commit to both trades booked (section 11).
- Plus the wait for the booking window: an FX business day, 08:00-16:30 ET.
- Plus a 30-minute watch of the start exchange at 00:45 ET on the spot date (10.8).

**Worked example.**
- Inputs:
  - fixture spot EURUSD 1.1158 and GBPUSD 1.3323 (the 10-28 17:42 EDT prints);
  - EURGBP print 0.83725, 2.49 pips under the derived cross, as measured on 10-28 (1.3);
  - the curves and overnight prints of 2026-10-27 (SONIA of 10-26); trade date 10-29.
- The live run re-prices everything at booking.

| | Trade A (CCS + conversion) | Trade B (forward) |
|---|---|---|
| Spot / outright | EURUSD mid 1.1158, used for both exchanges | EURGBP derived 0.837499 (print 0.83725); outright 0.866797 (+292.98 pips) |
| Dealt | pay ESTR + 0.1 bp (par -0.92 bp + 1.0 bp half-width, rounded up) / receive SOFR flat | K = 0.866624 (F less 2.0 bp of F) |
| Notionals | EUR 2,000,000.00 / USD 2,231,600.00 | sell EUR 2,000,000.00 / buy GBP 1,733,248.00 |
| Inception conversion | SELL EUR 2,000,000.00 at 1.11578 = USD 2,231,560.00, value 2026-11-02 | none |
| Dates | start 2026-11-02; 8 quarterly periods; last payment and final exchange 2028-11-06 | value date 2028-11-02 (731 days) |
| Day-one value (cost of dealing) | -447.82 (CCS) and -40.00 (conversion spread) | -419.25 |
| IM (GS schedule) | 2 % x 2,231,600.00 = 44,632.00 | 6 % x 2,231,600.00 = 133,896.00 |
| Labels | MODEL; curves PROXY (Treasury for USD, ECB AAA for EUR); basis level ASSUMED | MODEL; GBP curve observed; EUR curve PROXY; spread and basis levels ASSUMED |

### Owner choices

Each has a default (section 13), so none blocks the build or the booking.

| Question | Default | Why a choice is needed |
|---|---|---|
| Q1 | Trade B on the BoE OIS curve (GBP, observed) and the ECB AAA curve + 0.0 bp (EUR proxy) | no free EUR OIS curve |
| Q2 | the EUR/USD and GBP/USD basis levels are GS schedule terms, 0.0 bp ASSUMED, with a 1.0 bp CCS half-width | no free basis source |
| Q3 | every EUR obligation is funded by an audited EUR purchase two BD before it (the SFI), ON for both contracts | the Fund holds no EUR |
| Q4-Q8 | IM percentages, the final-exchange date, the window, the 2028 funding gap, notional caps | section 13 |

## 1. Where the build starts

Line numbers are those of the pre-gate tree. Every builder re-reads its files after the 1.0.6 commit.

### 1.1 Code gaps (research 17:38-17:53 EDT, rechecked 18:00-18:20 and 19:15-19:35 EDT)

| # | Gap | Where | Fixed by |
|---|---|---|---|
| G1 | Floating XCCY legs are refused: any `pay_index`, `recv_index` or fixing field answers 400 NOT_IN_THIS_BUILD, so both legs are fixed | `swaps_trs.parse_xccy_terms` 1006-1048 (1013); `xccy_leg_spec` 1654-1671 | P-C |
| G2 | XCCY periods are semi-annual only, start T+0 on the NYSE calendar, have no payment delay and no TARGET2 / New York calendars | `XCCY_PERIOD_MONTHS` 156; `swaps.effective_date` 271-273; `period_schedule` 276-292 | P-A (calendars), P-C |
| G3 | There is no cross-currency basis anywhere, and the foreign leg sits on a flat curve at the policy rate (EUR = ECBDFR 2.50, not ESTR) | `_xccy_deal` 1177-1224; `foreign_curve` 1063-1075; `tables.POLICY_INDEX` | P-A (curves), P-C (basis) |
| G4 | There is no ESTR fixing and no compounded index. SOFR compounds per calendar night on the latest print, not by ISDA's weights, and there is no index method | `swaps.fixing_for` 800-812; `accrue_leg` 1595-1665 | P-A, P-C |
| G5 | XCCY VM leaves out the FX move on the notionals and the accrued coupons. The mark is right to omit them for NAV, but GS would call VM on them under a CSA | `_xccy_value` 1478-1503; `swaps._vm_core` 1365-1476 | P-C |
| G6 | A T+0 start exchange cannot be funded from today's cash, and an FX spot sale cannot count the CCS's EUR receipt (the OA-11 check) | `_start_exchange` 1330-1366; `fx.funding_check` 531-569 | P-C, P-D |
| G7 | A EUR coupon or repayment with no EUR in the account overdraws EUR (FUNDING_SHORTFALL) and is never funded ("never automatic FX") | `swaps_trs._fund` 555-586; `fx.fund_ccy` 1190-1223 | P-D (SFI) |
| G8 | OTC FX forwards stop at 1Y / 400 days, and the offered `2W` tenor answers 400 TENOR | `otc_options.TENORS` 98, `MAX_DAYS` 101, `_expiry` 442-462; `fx.tenor_date` 245-263 | P-A, P-B |
| G9 | Forwards price by simple-interest CIP on ONE overnight or policy rate per currency (EUR = ECBDFR), with no term structure and no basis | `fx.forward_rate` 720-756; `fx.rate_for` 683-717 | P-A (`fxcurves`), P-B |
| G10 | Value dates on US-only and TARGET2-only holidays are accepted (Veterans Day 2026-11-11, Easter Monday 2027-03-29) | `fx.spot_date` / `tenor_date` (NYSE calendar only) | P-A, P-D |
| G11 | The delivery check always shows an empty note: it reads `note`, but `fx.funding_check` returns `text` | `otc_options._preview_dict` about line 1089 | P-B |
| G12 | Forward delivery runs at 17:05 ET of the value date, after the FX day has rolled, so the delivery deal can carry the next day as its value date | `EXPIRY_ET` / `h_expire` / `_deliver` 1551-1635 | P-B |
| G13 | Risk leaves PB OTC FX forwards out of `exposure_by_currency` and the FX scenario but counts them in the headline; the FX scenario is one +/-5 % USD move | `risk._currency_balances` 863-885; `_pos_risk` 534-536; scenarios 1043-1047 | P-B |
| G14 | A contract carries no owner reference, so a re-run could book twice; the XCCY RFQ comparison knows fixed rates only | `swaps.h_book` 1046-1073; `_quote_terms` / `_quote_moved` 2973-3152 | P-C |
| G15 | The XCCY trade date is the NY calendar date while FX trade dates roll at 17:00 ET, so an evening booking shows 10-28 against 10-29 | `_book_xccy` 1283; `fx.fx_trade_date` 230-238 | P-C |
| G16 | VM keys start at the contract's `start_date`, so a spot-starting CCS would get no VM on its trade date or the next BD | `swaps.vm_keys` 2057-2083; `_vm_core` date guard 1376-1379 | P-C |
| G17 | Three of the owner's live TRS have `trade_date` NULL, and SWP-000006 has no VM row yet. `vm_keys` loops over every open contract, so a key rule that parses `trade_date` would raise and stop VM for the whole book | live read (1.2); `swaps.vm_keys` 2057-2083 | P-C |
| G18 | An XCCY termination funds the foreign notional and accruals with `fx.fund_ccy`, which never converts: a Fund that holds no EUR can never terminate (409 INSUFFICIENT_FUNDS_CCY) | `swaps_trs._terminate_xccy` 2135-2170 (2153); `_fund` 555-586; `fx.fund_ccy` 1190-1223 | P-C |
| G19 | `risk._currency_balances` already sums every non-USD asset and liability GL (2350, 2360, 2340 ...). A product hook that also returned the full position delta would count it twice | `risk.py` 863-885 | P-B, P-C |
| G20 | A cross's spot is its own print whenever that print is executable (`pair_mid`). On 10-28 the EURGBP print sat 2.45 pips under the derived cross all day (1.3) | `fx.pair_mid` 322-353 | P-A (`fxcurves.spot`) |

**The 15.15 walk's defects** (replaces revision 1's merge rule; FX-C-09).
- At pool start the orchestrator maps each forward or XCCY defect that the 1.0.6 P8 walk reported and 1.0.6 did not fix.
- Each goes either to a task already in section 8 (no new hours) or to section 14 with a reason the owner can read.
- Nothing is added to a package silently. The fixes step (section 11) carries a 0.5 h reserve for mapped items.
- The audit's 23 findings are mapped in 1.5.

### 1.2 The owner's book (live database read with `mode=ro`)

Read at state_version 3867 (about 17:45 EDT) and again at state_version 3897 (about 19:30 EDT); the cash and margin lines did not change.

**Cash.**
- F1:TREASURY USD 2,103,950.48. F1:US_EXCHANGE USD 697,070.38 (the cascade never sweeps it). F1:PB_GS USD 0.00.
- No EUR or GBP in any F1 account. PB financing is off (`pb_financing` 0, `pb_limit` 0).

**Goldman Sachs margin.** IM held (1330) 1,594,691.30; VM posted (1320) 945,325.99; VM received (2330) 200.00.

**Goldman Sachs schedule.**
- Stored: `irs_half_width_bp` 1.0, `otc_spread_mult` 1.0, CTM.
- From the seeds: `xccy_im_pct` 0.02, `forward_im_pct` 0.06, `forward_hw_bp` 2.0.
- There is no notional or exposure limit field (`prime.FIELD_SPECS`), and compliance has no OTC limit (D18).

**Open contracts. None of them may change.**
- CDX-000001 (IG) and CDX-000002 (HY).
- SWP-000001 TRS ORCL pay 38,700.
- SWP-000003 IRS pay 4.72 % 7,500,000 to 2029-10-22.
- SWP-000004 USO pay 20,000; SWP-000005 MU pay 2,000; SWP-000006 ORCL pay 11,300.
  - Booked 10-28 in 1.0.5: `trade_date`, `value_date`, `confirm_status` and `booking` are NULL on all three.
  - SWP-000004 and -005 have a VM row for 10-28; SWP-000006 has none yet (G17).
- SWP-000002 (META) is TERMINATED.

**Next ids.** SWP-000007 and OTC-000001 (no `otc` sequence exists yet). No `fx_deals` rows. No `rfqs` table (it arrives with 1.0.6).

**Rate series stored.** ECBDFR, IUDSOIA (10-26), SOFR (10-27), UST_PAR and EUR_AAA_PAR (10-27). There is no ESTR series, no BoE curve and no
ECB spot-rate curve.

### 1.3 Free data (rechecked 19:18-19:30 EDT with the project agent; the critic's fetches 18:44-19:03 EDT)

| Input | Source (primary / fallback) | Value seen | Use |
|---|---|---|---|
| ESTR daily | ECB data portal EST `B.EU000A2X2A25.WT` / FRED `ECBESTRVOLWGTTRMDMNRT` | 2.440 % for 10-27 | EUR overnight node, EUR accrual estimates |
| ESTR compounded index | ECB EST `B.EU000A2QQF08.CI` | 110.09953057 (10-28) | EUR coupons (index method) |
| SOFR daily | NY Fed / FRED `SOFR` (stored) | 3.95 % for 10-27 | USD overnight node, USD accruals |
| SOFR Index | NY Fed `rates/secured/sofrai` / FRED `SOFRINDEX` | 1.26467626 (10-28) | USD coupons (index method) |
| SONIA daily | Bank of England IADB `IUDSOIA` (GOV_UA) / FRED `IUDSOIA` | 3.7328 % for 10-26 on both; at 19:18 EDT neither had 10-27 yet | GBP overnight node |
| SONIA compounded index | BoE IADB `IUDZOS2` | 121.79978731 (10-27) | stored; for the GBP float leg (deferred, section 14) |
| GBP OIS curve | BoE `latest-yield-curve-data.zip`, workbook "OIS daily data current month.xlsx", sheet "3. spot, short end" (1M..60M; sheet "4. spot curve" to 25Y) | 10-27: 1M 3.89939, 1Y 4.47782, 2Y 4.72745, 25M 4.73452 (% cc) | GBP curve (observed) |
| EUR AAA government curve | ECB YC `B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M` .. `SR_10Y`; the series title reads "Svensson model - continuous compounding" | 10-27: 3M 2.542643, 1Y 2.878760, 2Y 3.015032, 3Y 3.051054 (% cc) | EUR proxy curve |
| UST par curve | treasury.gov (stored as UST_PAR) | 10-27: 6M 4.34, 1Y 4.50, 2Y 4.70, 3Y 4.80 | USD proxy curve (the IRS desk's) |
| FX spot | Yahoo (browser user agent, allowed for Yahoo only) | EURUSD 1.1158, GBPUSD 1.3323 at 17:42 EDT | deal rates, the derived cross, translation |
| EURGBP print against the derived cross | Yahoo 1-minute bars of 10-28, 08:00-16:30 ET | 509 minutes: mean -2.45 pips, sd 0.44, 99th percentile of the absolute difference 3.44, maximum 3.79; 17:30-17:50 ET: -3.8 to -9.2 | `CROSS_TOL_PIPS` 5.0; the derived cross prices (D3) |
| ECB reference rates, 10-28 14:15 CET | ECB EXR | EURGBP 0.83785, EURUSD 1.1133 | at 13:15Z Yahoo's derived cross was 0.83788 and its print 0.83766 |
| Forward points, cross-currency basis | none free: Yahoo `EURGBP1Y=X` answers 404; CME 6E / RP futures stop short of 24M and are thin | none | basis = GS schedule term; points = model |

**The Bank of England is reachable; revision 1 was wrong (FX-C-01).**
- The IADB CSV (`IUDSOIA`, `IUDZOS2`) and the yield-curve zip answer HTTP 200 to GOV_UA. The critic fetched them at 18:44-19:03 EDT; the
  designer fetched them once each at 19:18 EDT (CSV 639 bytes in 0.7 s; zip 424,114 bytes in 0.3 s).
- The same IADB URL answered 403 "Access Denied" (Akamai) to TERMINAL_UA 16 s before a 200 to GOV_UA. The research's 17:43 403 is the
  same page. The block depends on the agent; it is not a wall.
- GOV_UA is the terminal's honest identity with the owner's e-mail. The terminal already sends it to FRED and FINRA, which are not .gov
  (`quotes\http.py` 7-9). Sending it to the Bank of England is not circumvention: it is never a browser agent, the provider uses no other
  agent, and a 403 is final ("unavailable", never retried with another agent).

**The BoE convention is verified.** The workbook publishes instantaneous forwards and spot rates.
- On 10-01 and 10-27 the 1Y and 2Y spot rates equal the average of the published forwards to 0.5 bp (2Y on 10-27: spot 4.7274, forwards
  averaged 4.7297). Annual compounding would predict about 10 bp more (4.8433).
- So the spot rates are continuously compounded. P-A's self-check repeats this test on every run (P-A task 5).

**Where the critic's prices came from.** At revision 1's inputs (S 0.8369, 731 days) the critic priced F 0.875538 on flat ESTR / BoE 2Y
OIS and 0.866099-0.866872 on the ECB AAA 2Y / BoE 2Y. The designer reproduced both (0.875538 and 0.866099, with every rate continuously
compounded). Revision 2's default, on the derived spot and the full curves, is 0.866797 (3.2).

### 1.4 Conventions (market practice; each is written into the contract's confirmation)

**ESTR leg.**
- ACT/360. The ECB publishes each day's rate at 08:00 CET on the next TARGET2 day.
- Compounded in arrears over TARGET2 days with ISDA n_i / 360 weights, which equals the ratio of the ECB's compounded index.

**SOFR leg.**
- ACT/360. The NY Fed publishes at about 08:00 ET on the next USGS day.
- Compounded in arrears, which equals the ratio of the SOFR Index.
- The SOFR Index exists for USGS days only, so a SOFR period starts and ends on USGS days (D10).

**SONIA.** ACT/365F.

**RFR CCS.**
- Spot start (T+2); quarterly periods on both legs; MF on TARGET2 + New York.
- Compounded in arrears with no lookback and a 2 BD payment delay.
- The spread is quoted on the EUR leg: ESTR + spread against SOFR flat.
- Both exchanges at the initial spot rate (no reset).

**FX dates.**
- USD pairs: T+1 needs to be a business day of the non-USD currency only (a New York holiday on T+1 does not delay spot); the spot date is
  a business day of both.
- Crosses (EURGBP): T+1 is a business day of both currencies. The spot date and every value date are also New York business days,
  because the SFI buys the EUR against USD for the same value date (this build's rule).
- The 24M value date is spot + 24 months, MF, with the end-of-month rule.

### 1.5 Coverage of the OTC audit (OTC-01..OTC-23; FINSIM1_PLAN 15.15b; FX-C-09)

Hours of the items fixed here are inside the package hours of section 8; the column gives each item's share.

| Audit id | Severity | 1.0.6 fixes | Here (package, test, about h) | Deferred, with the reason the owner reads |
|---|---|---|---|---|
| OTC-01 stale CHF rate, no age limit | critical | refuse stale rates, show the rate's age | USD / EUR / GBP: overnight prints at most 10 days old, curves at most 3 BD old (P-A; fxcurves tests; 0.25) | other currencies keep 1.0.6's rule |
| OTC-02 forwards missing from currency exposure | critical | forwards counted until delivery | the hook rule that counts nothing twice (P-B, P-C; B-T6, B-T12; 0.5) | - |
| OTC-03 XCCY start T+0 / previous BD after 17:00 | critical | start on the spot date | float / float on `ccycal` spot dates and the FX trade date (P-C; C-T1; in G2 / G15) | - |
| OTC-04 XCCY legs on flat overnight curves; forward and XCCY disagree | critical | - | one curve per currency for USD / EUR / GBP; the PB forward and the CCS on the same curves (P-A, P-B, P-C; B-T11; 3.5) | the FX desk's forwards move in 1.0.6.2 (17.1 pips apart at 1Y EURUSD in the example; the PB ticket shows the FX desk's outright beside its own); JPY / CHF / CAD / AUD have no daily RFR source yet |
| OTC-05 forward points: one rate, no basis | major | - | PB desk, USD / EUR / GBP pairs: term curves and the basis levels (P-B; B-T1, B-T9; in OTC-04) | the FX desk and other currencies, as above |
| OTC-06 NYSE calendar only | major | - | TARGET2, New York, London and USGS calendars for USD / EUR / GBP (P-A, P-D; ccycal tests, B-T3; 1.5) | Tokyo, Zurich, Toronto and Sydney calendars: those pairs keep the NYSE calendar, labelled "local holidays not modelled" |
| OTC-07 2W offered but refused | major | 2W removed or priced | 2W priced for USD / EUR / GBP pairs (P-B; B-T2; 0.1) | - |
| OTC-08 PB spread flat 2 bp at every tenor and size; one-way price | major | - | ticket shows spot, spot date, days from spot, points and the outright (P-B; 0.25) | the spread by tenor and size goes to 1.0.6.2: it is a dealer-terms redesign for every pair; at 2Y the GS 2.0 bp (1.73 pips) is close to the FX desk's 1.40 pips at 1Y |
| OTC-09 rate risk in one currency, wrong sign | major | - | rate01 per currency for PB forwards and the CCS; OTC FX contracts leave the USD DV01 rows and the rates scenario (P-B; B-T8; 0.5) | - |
| OTC-10 OTC FX forwards in the headline | major | - | PB OTC FX forwards leave the headline (P-B; B-T7; 0.1) | - |
| OTC-11 flat 6 % IM, no netting | major | - | - | 1.0.6.2: netting and risk-based IM change the IM of every open OTC contract and need their own acceptance; the GS percentages stay (Q4) |
| OTC-12 equity forwards financed at SOFR + 1.25 % both ways | major | - | - | 1.0.6.2, first item: a money defect (a sale is paid about 1.2 %/yr too much); no equity forward is open in the owner's books |
| OTC-13 equity forwards on a continuous trailing yield | major | - | - | 1.0.6.2: discrete dividends need a dividend schedule source |
| OTC-14 XCCY fixed / fixed only | major | - | EUR / USD float / float (ESTR / SOFR) with the dealer's spread and the start conversion (P-C; C-T1..C-T15; 4.5) | GBP SONIA legs (data verified available: `IUDZOS2`); JPY / CHF / CAD / AUD legs (no daily RFR source); fixed / float |
| OTC-15 deal rate in USD per foreign unit; JPY decimals | major | - | the float CCS shows EURUSD 1.1158 (market convention) (P-C, P-B; 0.1) | other pairs' display and whole-unit JPY go to 1.0.6.2 |
| OTC-16 XCCY VM ignores the FX move on the notionals | major | - | VM on the CSA exposure, the full value, for every XCCY (P-C; C-T7, C-T11; 0.75) | - |
| OTC-17 delivery waits for a 17:00 close | major | - | delivery at 00:30 ET on the value date, no price input (P-B; B-T4; 0.25) | - |
| OTC-18 positions and drawer show forwards wrongly | major | fixed | the new drawers build on it | - |
| OTC-19 XCCY conventions and limits | minor | - | float / float: quarterly, ACT/360, 3-month steps, TARGET2 + New York (P-C, P-B; C-T1) | per-currency fixed-leg conventions, non-USD pairs, 7Y-30Y in the UI |
| OTC-20 unpriceable pairs listed; 8-decimal derived spot | minor | - | the PB forward's derived cross at 6 dp (P-A; fxcurves tests) | pruning the pair list and sourcing NZD / SEK / NOK rates |
| OTC-21 JPY / CAD / AUD on a 360-day year | minor | - | - | 1.0.6.2, with those currencies' daily RFR sources: the change moves the legacy prices of three products |
| OTC-22 ticket gaps and wording | minor | - | spot date and days from spot, the delivery note (G11), the "beyond 2 years" wording, the value-date field highlighted (P-B; B-T5) | the dealt-currency selector, FX swaps and NDFs |
| OTC-23 RFQ row and Accept dialog without amounts | minor | fixed | the float CCS row shows both notionals, the dates and the deal rate (P-C; C-T9) | - |

**Amending 15.15b.** Before the pool starts, the orchestrator adds the deferrals above to FINSIM1_PLAN 15.15b. The owner's report (10.7)
lists them: he was told that 15.15 fixes forwards and cross-currency swaps.

**The PB desk for other pairs.** A pair with a currency outside USD / EUR / GBP keeps the pre-1.0.6.1 method on the PB desk.
- The method: one overnight or policy rate per currency, simple interest, up to 1Y, under 1.0.6's age limit (OTC-01).
- Its label reads "LEGACY METHOD - LIMITED: one overnight / policy rate per currency, no term structure, no basis".
- 18M, 2Y and any broken date beyond 1Y answer 400 TENOR "beyond 1 year <pair> has no term curve in this build".
- Institutional V3's JPY forwards (3, 6 and 12 months) take this path, and its JPY rate is a monthly OECD series. The orchestrator checks
  it against 1.0.6's age limit before V3 books.

## 2. Binding decisions

### D1. Release and sequencing

- 1.0.6.1 is built on the committed 1.0.6 tree. No edit touches the tree before the 1.0.6 gate has committed it and verified that commit
  from its archive.
- Builders re-read every file they own.
- At most 4 builders run at once (FINSIM1_PLAN section 5, 7.6 GB of memory).
- The CDS single-name plan (`docs\CDS_SINGLE_NAME_PLAN.md`, 1.0.7) rebases on 1.0.6.1. The two pools never run at the same time.
- 1.0.6.1 deploys under the owner's standing pre-approval (FINSIM1_PLAN section 12 step 6).
- The live booking runs under his explicit instruction of 10-28 (section 10.5). Only the orchestrator does either; no builder does.

### D2. FX curves: one curve per currency; every input observed or a disclosed schedule term (FX-C-01, FX-C-02)

**The rule.** Every currency has one zero curve z_c(t):
- continuously compounded;
- t = ACT/365 days from the curve's as-of date d0;
- linear in z between nodes; flat before the first grid node; no extrapolation beyond the last grid node (409 CURVE_SHORT).
- The discount factor is DF_c(a, b) = exp(-z(t_b) x t_b + z(t_a) x t_a).

| Currency | First node (t = 1/365) | Grid | Kind |
|---|---|---|---|
| GBP | SONIA (BoE IADB; FRED fallback) | BoE UK OIS spot curve, 1M..60M monthly | observed |
| EUR | ESTR (ECB; FRED fallback) | ECB AAA spot rates SR_3M..SR_10Y, plus `eur_ois_spread_bp` (GS 0.0, D5) | PROXY + ASSUMED spread |
| USD | SOFR (NY Fed; FRED fallback) | the Treasury par curve bootstrapped semi-annually (the IRS desk's `swaps.dfs_for`), z_i = -ln(DF_i) / t_i at t_i = 0.5, 1.0, ... | PROXY |

- The first node converts the latest overnight print r, at most 10 calendar days old, to z = 365 x ln(1 + r / B), with B = 360 for ESTR and
  SOFR and 365 for SONIA. In the example: SONIA 3.732609 %, ESTR 2.473805 %, SOFR 4.004641 %.
- A grid more than 3 business days of its own calendar older than the valuation date answers 409 CURVE_UNAVAILABLE. This is the IRS desk's
  dated-curve rule (`CurveStore.for_date`, 3 BD). A quote is then refused; a mark keeps the existing stale rule (the stored mark, labelled
  stale) and its VM waits and retries.
- A missing node blocks: nothing falls back to a flat rate, to another curve or to the policy rate.
- Under the USD CSA, the discount curve of EUR or GBP adds that currency's basis level (D5) to every node; projection never does.

**The labels.**
- "observed: Bank of England UK OIS curve of <date> (continuously compounded)".
- "PROXY: ECB euro-area AAA government curve of <date> + <x> bp (Goldman Sachs schedule, ASSUMED): no free EUR OIS curve".
- "PROXY: Treasury par curve of <date>, the IRS desk's stand-in for the SOFR curve".

**Why.** The owner allows three forms for an input: observed, a disclosed and editable PB schedule term, or a block. Revision 1's flat
overnight rates, held for 24 months, were none of the three.
- The cost of that error ran against the Fund:
  - the GBP curve rises 99 bp from SONIA to 2Y; the EUR AAA curve rises only 57 bp above ESTR;
  - so flat curves priced the EURGBP 2Y outright 79 pips under the default here;
  - that is GBP 15,830 fewer on EUR 2M, while the mark on the same flat curves would have hidden the gap.
- The EUR proxy:
  - the ECB AAA curve is published daily and already stored (par yields; revision 2 adds the spot rates);
  - the IRS desk already uses the same kind of stand-in for USD (Treasury par for the SOFR curve);
  - the gap between the AAA curve and EUR OIS is a disclosed, editable term that the owner may set or use to block (Q1).

**Cross-checks shown and stored, never priced.**
- The flat-overnight outright (revision 1's method).
- The outright on the cross print instead of the derived cross.
- The FX desk's outright for the same value date, when the FX desk quotes it (up to 1Y).

**FX desk forwards stay on their method in this release.** `fx.forward_rate` (up to 1Y, simple interest on the policy series) moves to
`fxcurves` in 1.0.6.2 (1.5, section 14). In the example the two desks' 1Y EURUSD outrights differ by 17.1 pips; the PB ticket prints the
FX desk's outright and the difference beside its own.

### D3. Trade B: the PB OTC FX forward out to 2 years (USD / EUR / GBP pairs)

- **Tenors:** 1W, 2W, 1M, 2M, 3M, 6M, 9M, 1Y, 18M and 2Y (24M is an alias of 2Y), plus broken dates up to the 2Y date.
  - Beyond that: 400 TENOR "beyond 2 years the PB forward is not offered".
  - Pairs with another currency keep the legacy method up to 1Y (1.5).
- **Value dates:** on the pair's value calendar (K1): both currencies for a USD pair; both currencies and New York for a cross.
- **Spot (G20, FX-C-03):**
  - a USD pair: its executable print;
  - a cross: the cross derived from the two USD pairs, quantised to 6 dp. It is executable only when both USD prints are executable and
    their quote times are within 120 s of each other.
  - The cross print, when one exists, is stored with the difference. A difference beyond `CROSS_TOL_PIPS` = 5.0 pips answers
    409 SPOT_INCONSISTENT (retried).
- **Why the derived cross.**
  - It is consistent with the EURUSD that sets the IM, the USD notional and Trade A, and with NAV translation, which uses the USD pairs.
  - On 10-28 the ECB's 14:15 CET reference EURGBP (0.83785) matched the derived cross (0.83788), not the print (0.83766).
  - Pricing off the print would have sold the EUR about 2.45 pips low, about GBP 490 on EUR 2M: more than the 1.73-pip half-width.
- **Why 5.0 pips.** On 10-28, 08:00-16:30 ET, the largest difference was 3.79 pips (99th percentile 3.44). The guard catches a stale or
  broken leg, not the normal offset.
- **Price:**
  - F = S x DF*_base(S, V) / DF*_quote(S, V), where DF* is the discount curve with the basis level; 6 dp.
  - K = F less (for a sale) or plus (for a purchase) `forward_hw_bp` x `otc_spread_mult` bp of F, always against the client.
  - The GS seed is 2.0 bp (ASSUMED, editable).
- **Delivery:** ON the value date at 00:30 ET, not 17:05 (fixes G12).

### D4. Trade A: the RFR float / float CCS (EUR/USD only in this release)

**Legs.**
- EUR on ESTR and USD on SOFR, each "compounded in arrears, no lookback, 2 BD payment delay, ACT/360".
- The spread sits on the EUR leg only and is not compounded: ISDA "compounding with spread excluded".

**Coupon of period k:**

    coupon_k = N x (R_k + spread) x d_k / 360
    R_k      = (I(e_k) / I(s_k) - 1) x 360 / d_k     rounded to 7 decimals of a fraction (0.00001 %)

- I is the administrator's published compounded index: the ECB's compounded ESTR index, or the NY Fed's SOFR Index.
- The rounding follows ARRC / ISDA practice.
- EUR fallback: the same ratio computed from the daily ESTR prints over TARGET2 days with n_i weights.
- With no index and no fallback, the payment waits (INDEX_PENDING). It is never estimated.

**Schedule.**
- Start = the spot date (T+2 on TARGET2 + New York, D10).
- Period ends = start + 3k months, MF on TARGET2 + New York. Each end is computed from the start, so dates never drift.
- Payment dates = period end + 2 BD.
- Every SOFR period start and end is a USGS day, else 400 SCHEDULE (D10).
- The final exchange is paid WITH the last coupon on the last payment date: one maturity event (Q5). Accrual stops at the termination
  date.

**The dealer's quote (the "dealer pricing engine" of the instruction).**
- The par spread s_par is the EUR-leg spread at which the full value of the swap (3.3) is zero on the dealer's curves, at the basis level
  of D5.
- The value is linear in the spread: PV(s) = PV(0) - X0 x N_EUR x A* x s, where A* is the EUR annuity on the discount curve. So
  s_par = PV(0) / (X0 x N_EUR x A*).
- The dealt spread is s_par + `xccy_basis_hw_bp` x `otc_spread_mult` for the payer of the EUR leg (minus for its receiver). It is rounded
  on the 0.1 bp grid against the client: up for a payer.
- So the day-one value is minus the half-width annuity minus the rounding, never a gain. A client spread better than the dealt one answers
  400 OFF_MARKET.
- **Why a par spread and not "basis level + half-width".** At the basis level the swap is not worth zero: its conventions have a value.
  - The final exchange is paid with the last coupon, 4 days after the end. The Fund then receives USD 4 days later, at 4.65 %, and pays
    EUR 4 days later, at about 3.0 %: worth -370.75 USD.
  - The coupon payment delays are worth -33.23 USD.
  - A dealer that quoted "level + half-width" would take those 403.98 USD on top of its half-width.

**Exchanges.**
- Both are at the deal rate X0 (D6): the initial one on the start date, the final one on the last payment date.
- No notional reset: `mtm_reset` must be false. True answers 400 NOT_IN_THIS_BUILD for float / float.

**Direction.** The existing XCCY rule names legs by their coupons:
- pay EUR = EUR borrowed: received at the start (2360) and repaid at maturity;
- receive USD = USD lent: paid at the start (1360) and received back at maturity.

**What stays out** (400 NOT_IN_THIS_BUILD, each with its reason):
- GBP float legs: "not in this build yet: the SONIA index (Bank of England IUDZOS2) is stored; the GBP leg is the next currency (1.0.6.2)";
- JPY, CHF, CAD and AUD float legs: "no daily overnight rate is sourced for <ccy> in this terminal";
- fixed / float.
- The legacy fixed / fixed product keeps its pricing (semi-annual; T+0 or 1.0.6's spot start) except for D8, D11, D17 and G16 / G17.

### D5. Basis levels and the EUR OIS spread: disclosed, editable GS schedule terms

**New fields** in `prime.FIELD_SPECS`, on all nine brokers' schedules:

| Field | Label | Unit and range | Seed |
|---|---|---|---|
| `xccy_basis_eur_bp` | "EUR/USD cross-currency basis level (mid)" | bp, -200 to 200 | 0.0 for every broker |
| `xccy_basis_gbp_bp` | "GBP/USD cross-currency basis level (mid)" | bp, -200 to 200 | 0.0 for every broker |
| `xccy_basis_hw_bp` | "cross-currency swaps: dealing half-width" | bp, 0 to 100 | the broker's `irs_half_width_bp` seed: GS 1.0, JPM 1.0, MS 1.2, DB 1.5, BARC 1.2, CITI 1.0, UBS 1.2, BNP 1.5, BOFA 1.0 |
| `eur_ois_spread_bp` | "EUR OIS curve: spread over the ECB AAA government curve" | bp, -100 to 100 | 0.0 for every broker |

**Where they enter.**
- Each level is a parallel spread on that currency's discount curve under the USD CSA (D2).
- So it moves the PB forward of any pair with that currency, and the CCS's par spread (D4).
- The EUR OIS spread moves the EUR grid nodes, for projection and discounting alike.
- The FX desk (not a PB) uses 0.0 for all three, labelled.

**The labels say what they are (FX-C-14).**
- "basis level 0.0 bp ASSUMED: an assumed level, not a neutral one (the EUR/USD basis has been negative for most of the time since 2008); each
  1 bp is worth USD 437.98 on Trade A and 420.66 on Trade B".
- "GBP/USD basis level 0.0 bp ASSUMED; each 1 bp is worth USD -420.57 on Trade B".
- "EUR OIS spread 0.0 bp ASSUMED: the gap between EUR OIS and the AAA government curve is not observed; each 1 bp is worth USD 423.92 on
  Trade B and 2.32 on Trade A" (it moves the AAA grid nodes, not the ESTR node, so it differs a little from the EUR rate01).
- (All per +1 bp, at the example's trade date.)

**Editing them.**
- An edit re-marks every open contract at that PB that uses them at the next VM. The field's hint says so.
- The edit writes one SETTINGS_CHANGED event with the before and after values.
- The owner's report shows each such re-mark as its own NAV-bridge line, never inside "other P&L" (10.7).

**Why not block instead.** The instruction allows "a disclosed, editable prime-broker schedule term" and asks for auto-booking. Blocking is
the answer only if the owner says so (Q1, Q2).

### D6. The deal rate and the inception conversion

**The CCS deal rate X0** is the executable EURUSD mid: a fresh print in the FX session.
- The PO-08 rules are unchanged: never the ECB reference rate, never a stale print.
- Both exchanges use X0. A spread on X0 would wash out between the two exchanges; on a CCS the dealer's charge is the spread's half-width,
  which is the market convention.
- The USD notional is to_c(N_EUR x X0).

**The inception conversion** (term `start_conversion` true; required for the owner's trade) is an FX desk spot deal in F1:PB_GS:
- SELL EUR N_EUR / BUY USD at the desk's bid on the same mid: X0 - 0.2 pip, the desk's disclosed spread.
- Value date = the CCS start date.
- Booked in the SAME command as the CCS, so it is atomic: both or neither.
- Linked both ways: the deal's inputs carry `link {type: "xccy_start", id: <contract>}`, and the contract's terms carry
  `start_conversion.deal_id`.
- Its settlement rows are tagged `ref_type` xccy_start, `ref_id` = the contract.

### D7. The start-date PvP, and linked rows that wait (FX-C-12)

The XCCY START event (00:45 ET on the start date) settles its linked conversion inside its own command, in this order:
1. the conversion's receivable leg: USD in;
2. the exchange: USD lent out, EUR borrowed in;
3. the conversion's payable leg: EUR out.

Only the net shortfall per currency is funded: USD 40.00 in the example, the conversion spread.

**Linked rows wait; there is no next-day fallback.** Revision 1 let SETTLE_DUE settle such rows the day after their due date. That would
pay EUR 2,000,000 that the Fund does not hold if the START failed, so it is removed.
- SETTLE_DUE never settles a row tagged xccy_start or xccy_event while its contract is OPEN and the event that settles it has not run.
- An event not done by 06:00 ET of its date raises ERROR XCCY_EVENT_LATE, naming the event and its last skip reason. The START runbook
  (10.8) says what the orchestrator does then.
- When a contract closes (MATURED or TERMINATED), its remaining linked rows lose the tag and settle through SETTLE_DUE: they are trades.

### D8. VM of a cross-currency swap: CTM on the CSA exposure, from the trade date (FX-C-08, FX-C-16)

- NAV keeps the clean mark. The notionals sit on the balance sheet in 1360 / 2360 and the accrued coupons in 1225 / 2340. They are
  revalued through the FX result. This is the existing rule, and it counts nothing twice.
- VM follows the CSA exposure, which is the swap's FULL value: the clean mark plus every balance-sheet item of the contract at the FX close.

      CSA exposure = Full PV = clean mark + balance(1360, USD) - X_t x balance(2360, EUR) + balance(1225, USD) - X_t x balance(2340, EUR)

  - A CSA collateralises the dirty value, so the accrued coupons count: at a period end they are about USD 22.6k against EUR 12.5k,
    which revision 1 left uncollateralised (about USD 8.7k: 22,639.60 - 1.1158 x 12,514.76).
  - VM moves cash between 1010 and 1320 / 2330 only, so it is NAV-neutral.
- The rule applies to every XCCY, fixed / fixed included. No XCCY is open in the live book.
- STM keeps the clean mark: settling the notional FX under STM would count it twice.
- **Where VM keys start (G16, G17).**
  - While a contract has no `otc_vm` row, its first key is min(start_date, COALESCE(trade_date, start_date)).
  - After the first VM, keys continue from the last VM date + 1, as today.
  - `_vm_core`'s date guard uses the same start.
  - A NULL `trade_date` never raises. The owner's SWP-000004..6 keep exactly the keys they have today; SWP-000006, with no VM row yet,
    starts at its `start_date` 2026-10-28, as now.

### D9. EUR obligations: the standing settlement-funding instruction (SFI)

**The term.** `settlement_funding {"EUR": "BUY_WITH_USD"}`.
- The booking program sets it on both owner contracts. Any contract may carry it.
- The owner can switch it off with `otc.settlement_funding`.

**The job, OTC_CCY_FUNDING.**
- It buys exactly the shortfall two joint BD before each obligation in that currency, on the trade date whose spot date is the due date.
- The shortfall is the obligations due that day minus the currency the account will hold by then.
- One audited FX desk spot deal per (account, currency, due date), linked to the contracts it funds.
- A CCS coupon becomes known at its period end, when the index is published, exactly two BD before its payment. So the same job funds the
  coupons, the final exchange and the forward delivery.
- Its deals settle PvP inside the CCS event (`ref_type` xccy_event), or through SETTLE_DUE before the forward's 00:30 delivery.

**Never silent.** Each purchase is a deal in Completed Trades that names the obligation it funds. The instruction is printed on the
confirmation and in the contract drawer.

**Why it is needed.** Without it the first EUR coupon, about EUR 12,950 on 2027-02-04, would overdraw EUR with an ERROR, because the owner
deliberately holds no EUR. The SFI is the only change of currency that is not a trade the owner places. It exists because he asked for the
settlement arrangements to be in place.

**Settlement funding, as a number (FX-C-19).** On 2028-10-31 the SFI must buy EUR 2,000,000 for the forward's delivery with USD.
- Need: about USD 2,231,600.00 at today's spot, or 2,306,059.76 at the 2-year outright 1.153030.
- The USD the SFI can draw on (the PB account's cash, then Treasury) will be about USD 1,989,338.43 by then:
  - 1,925,382.48 after S;
  - plus the projected coupons 1-7: USD 181,172.68 in, less EUR 105,051.74 out (USD 117,216.73 at today's spot).
- So at today's balances it is short by about USD 242,261.57 (318,682.31 at the outright). VM, other trades and the owner's transfers
  will move this.
- The quote, the pricing record, the Risk ladder and the owner's report print the gap. Q7 names this exact obligation.
- If the cash is not there on the day, the SFI deal's USD leg waits SETTLEMENT_UNFUNDED with an ERROR, and so does the delivery. Nothing
  converts or moves cash on its own.

### D10. Calendars (FX-C-15)

**A new module `ccycal.py` (P-A)** holds rule-based calendars for 2000-2099:
- TARGET2;
- New York: Federal Reserve holidays (a Saturday holiday is not observed; a Sunday one moves to Monday);
- London: England and Wales bank holidays with their substitute-day rules;
- USGS: New York plus Good Friday, plus a hand-maintained list of SIFMA one-off closes and of years in which SIFMA recommends only an
  early close on Good Friday. The label says the list is hand-maintained.

**Dates.**
- Spot, USD pairs: T+1 is the next business day of the non-USD currency (a New York holiday on T+1 does not count). The spot date is the
  next day after it that is good in both.
  - EURUSD traded on 2026-11-10 settles on 2026-11-12, not 11-13 (11-11 is Veterans Day).
- Spot, crosses: T+1 is the next business day of both currencies; the spot date is the next day after it that is good in both and in New
  York.
  - EURGBP traded on 2026-11-24 settles on 2026-11-27 (11-26 is Thanksgiving).
- Tenor dates: months from the spot date, MF on the value calendar, with the end-of-month rule; weeks roll following.
- A broken date that is not a business day of the value calendar is refused with the calendar named. EURGBP on 2026-11-26 reads "not a
  business day in USD (New York): the EUR is bought against USD for the same value date".
- CCS schedules roll on TARGET2 + New York. A SOFR period start or end that is not a USGS day answers 400 SCHEDULE "SOFR is not
  published on <date> (SIFMA close)".
  - For EUR/USD this can only happen on a one-off SIFMA close, because Good Friday is a TARGET2 holiday. The guard and its test exist for
    that case.

**Edge cases.**
- One-off UK holidays announced later (jubilees, funerals) live in a hand-maintained list. None is known for 2026-2030, and the label
  says so.
- A currency without a calendar keeps the NYSE calendar, labelled "local holidays not modelled".

**The FX desk follows.** `fx.spot_date` and `fx.tenor_date` move to `ccycal` for USD / EUR / GBP pairs (P-D). The FX desk and the PB desk
then agree on value dates.

### D11. Trade date

- Both products and the conversion use the FX trade date (`fx.fx_trade_date`: the FX day rolls at 17:00 ET).
- The booking timestamp (the accept command's time, ET) is shown beside it.
- The live run happens 08:00-16:30 ET on an FX business day, so the trade date is the calendar date of the booking (section 10.5).

### D12. Data (FX-C-01, FX-C-18)

- New stored series and curve rows (P-A), each with its source and as-of date:
  - ESTR, the ESTR compounded index, the SOFR Index, the BoE SONIA (IADB) and the SONIA compounded index;
  - the BoE OIS spot rows (1M..60M);
  - the ECB AAA spot rows (SR_3M..SR_10Y) and the six Svensson parameters (for the self-check).
- The Bank of England provider:
  - always sends GOV_UA, never TERMINAL_UA and never a browser agent;
  - a 403 is recorded as "unavailable" with the response's reference, and nothing is retried with another agent.
- SONIA is read from the BoE IADB first and from FRED as the fallback. Both as-of dates are recorded; the newer print wins.
- The ECB requests (the rfr and curves slots) use a 60 s timeout and 2 attempts. An HTML body (the ECB's error page) is "unavailable",
  never parsed.
- Pricing never falls back to another rate or curve. A missing or stale input blocks the quote with its reason, and the quote is
  retried.

### D13. Idempotency (FX-C-13)

**The owner reference.**
- Each trade carries `client_ref`, unique on `otc_contracts` (a partial unique index).
- ONE owner writes it: `swaps.h_book` (P-C), for every kind.
  - It checks first: a contract with that ref in any status answers 409 DUPLICATE_CLIENT_REF, and nothing is written.
  - It then writes the column after the product's book function returns, in the same command.
  - So `otc_options`' INSERT does not change.
- `otc.rfq.request` refuses with DUPLICATE_CLIENT_REF only when a contract with the ref exists, or an RFQ with the ref is REQUESTED or
  QUOTED. An EXPIRED or DECLINED RFQ does not block a new request.

**Request ids carry the run.**
- The form is `OWNER-FX-2026-10-28:<run>:<key>:<n>:<rfq|acc|dec>`, where run = the ET start time of the run, for example
  `20261030T081205` (10.1).
- Attempts are counted within the run, so a later day's run can retry after a day of expiries.
- Within a run, a refused request leaves no row, so its id is reused.

**After an accept.**
- A transport error, a timeout or a 5xx: re-POST the same `acc` id with the same payload. The command bus replays a committed result for
  the same request id and payload (`commands.py` 338-345).
- If that fails too, look the `client_ref` up before classifying. A trade is never called UNEXECUTED while its ref may be booked.
- QUOTE_MOVED or QUOTE_EXPIRED: decline the still-QUOTED RFQ with the `dec` id, then go to the next attempt.

**Before anything.** The program looks for the `client_ref` before it asks for anything. A re-run books nothing new.

### D14. Risk: the ledger part once, the off-ledger part from the hooks (FX-C-06)

**One rule.**
- The ledger part of a currency exposure comes only from `risk._currency_balances`: every non-USD asset and liability GL, such as 2350,
  2360 and 2340.
- A product hook (`risk_sensitivities`) returns only what the ledger does not hold yet:
  - XCCY: `fx_delta` = the clean mark's FX delta, in currency units (re-priced). `fx_nominal` = the exchanges not yet on the ledger:
    before S the start and the final exchange, which net to 0 without a reset; after S, none, because 2360 holds the final exchange.
  - FX_FORWARD: both legs at nominal and their PV deltas until delivery; after delivery nothing, because the delivery deal is on the
    ledger.
- So by currency: nominal = ledger + hook nominal; PV delta = ledger balances + hook PV delta.
- The test (B-T12) holds both trades after S with the ledger balances present: EUR nominal -4,000,000.00, EUR PV delta -3,882,523.08.

**The Risk page.**
- PB OTC FX forwards leave the headline long / short (fixes G13).
- OTC FX contracts leave the USD DV01 rows and the "rates +100 bp" scenario; their rate risk is shown per currency (OTC-09).
- The FX block shows:
  - per currency, the nominal and the PV delta, with the USD effect of a 1 % and a 5 % move;
  - the pair scenarios: EURUSD, GBPUSD, and EURGBP with either leg held;
  - rate01 per currency, basis01 per pair and points01;
  - the Goldman counterparty and collateral line;
  - the liquidity ladder, with the 2028 funding gap (section 6).

### D15. Honest refusals

- Every refusal carries the precise reason in trader words (section 12 lists them).
- A refused or expired quote stays visible: Pending Trades, rejected or expired. 1.0.6 already records refusals of otc.rfq.request,
  otc.rfq.accept and otc.book as ticket rejections.

### D16. Labels

| Label | Applies to |
|---|---|
| MODEL | every model number, shown with its inputs |
| observed | inputs shown with their source and time: prints, the BoE OIS curve, the ECB and Treasury curves as data |
| PROXY | a curve standing in for one the terminal cannot source: the ECB AAA curve for EUR OIS, the Treasury curve for SOFR |
| ASSUMED | schedule terms: half-widths, the basis levels, the EUR OIS spread, IM percentages, the FX desk spread |
| LIMITED | only the legacy method of other pairs (1.5) and the NYSE-calendar fallback |

Nothing reads "dealer-confirmed":
- quotes read "indicative model quote, simulated dealer" (C14);
- confirmations read "CONFIRMED - simulated confirmation".

### D17. Terminating a cross-currency swap (FX-C-07)

**Why.** Revision 1 kept "the notionals are re-exchanged at face". That cannot work for this Fund:
- `_terminate_xccy` funds the EUR (2360 + 2340) through `fx.fund_ccy`, which never converts, and the Fund holds no EUR by design;
- EUR bought on the FX desk arrives at T+2 at the earliest;
- so every termination would answer 409 INSUFFICIENT_FUNDS_CCY (G18).

**The rule, for every XCCY (both variants).**
- **Before S, with a linked inception conversion:** 409 XCCY_NOT_STARTED, "the start exchange and its conversion settle together on <S>;
  terminate from <S> 00:45 ET". Nothing is written. Without that refusal the conversion would owe EUR 2,000,000 that never arrives.
- **Before S without a conversion:** cash-settled in USD at the full value, which includes the cancelled start exchange.
- **From S:** cash-settled in USD, with no physical re-exchange:
  - the value is the full PV (3.3) re-priced with the basis level moved by the half-width against the Fund (down for the payer of the
    EUR leg);
  - the remaining coupons and the final exchange are cancelled;
  - the accruals are caught up first, so 1225 / 2340 hold the period so far.
- **The cash** moves in USD only (`fund_commitment`: own cash, then one Treasury sweep), never in EUR.
- **The entry clears the balance sheet** through 3900 at X_t:
  - Cr 1360 USD, Dr 2360 EUR, Cr 1225 USD and Dr 2340 EUR;
  - the difference to the close-out value goes to 4450 (close-outs), and the EUR lines' translation goes to `fx_result` (the ledger's
    existing rule).
- **Then:** VM unwound, IM released, status TERMINATED.
- **Linked SFI deals** that are already booked stay: they are trades. They lose their tags and settle through SETTLE_DUE, so the Fund may
  receive EUR it no longer needs; the termination result says so.
- **Example**, after S at the example's inputs: the Fund pays USD 696.34 (the full PV -259.44 re-priced 1.0 bp down); no EUR moves.
- The fixed / fixed tests change with this reason (C-T11).

### D18. Notional limits (FX-C-10)

- None are configured. The GS schedule has no OTC notional field, and compliance has no OTC limit.
- Every quote's `limits` block, the pricing record and the owner's report say so, with these numbers before and after the trade:
  - the headline gross exposure / NAV, against the Risk page's 2x warning limit;
  - the gross notional / NAV.
- Both trades sit outside the headline (D14), so the headline gross does not move; the gross notional rises by USD 4,463,200.00.
- Q8 offers editable caps for a later build.

### D19. Values are for the spot date

- Every OTC FX mark and the CCS's full PV are values for the spot date of the valuation date, converted at that day's spot. This is the FX
  convention: the spot rate is a price for the spot date.
- On the trade date the start exchange at the deal rate is then worth exactly 0: the deal rate is the price for value S.
- Once the CCS has started, its clean mark carries about two days of net accrual ahead of the ledger, which posts accruals nightly. In the
  example that is +188.38 on the start date. The amount does not grow; it unwinds at maturity.
- The forward's value rolls with its points at an unchanged spot (+152.18 from T to S in the example).

## 3. Pricing (stored with every quote and every booked contract)

### 3.1 Common rules (module `fxcurves.py`, P-A)

**`curve(services, ccy, on, pb=None)`** returns a Curve for USD, EUR or GBP (D2):
- its fields: `ccy`, `d0` (the grid's as-of date), `kind` ("observed" or "PROXY"), `nodes` [(t, z %)], `source`, `labels`;
- `first_node` {series, obs_date, value_pct, z};
- `spread_bp`: EUR only, read from the PB's `eur_ois_spread_bp` (0.0 with no PB);
- `record(until)`: the nodes from the first node to the first node beyond `until`, which is enough to recompute every number.
- Refusals are ConflictError:
  - RATE_UNAVAILABLE: no overnight print within 10 days;
  - CURVE_UNAVAILABLE: no grid within 3 BD of `on`, naming each source tried, for example "Bank of England yield-curve file: HTTP 403 at
    09:12 ET";
  - CURVE_SHORT: a date beyond the last node.
- A rate or a curve is never assumed.

**`discount(curve, level_bp)`** is the curve with the basis level added to every node (D5; USD has none).

**`df(curve, a, b)`** = exp(-z(t_b) x t_b + z(t_a) x t_a), with t = (date - d0) / 365.
- Quantised to 1e-10 HALF_EVEN wherever a quote or a record shows it (the forward).
- Unquantised inside the CCS's full PV (Decimal precision 34).

**`spot(services, pair, snapshot)`** returns {mid, basis, legs, legs_gap_s, print, diff_pips, tolerance_pips, executable, reason}.
- `basis` is "pair print" or "derived from the USD pairs".
- The derived cross is base-USD rate / quote-USD rate, quantised to 6 dp (OTC-20: never 8 decimals).
- Executable only when both legs are executable, their quote times are within `LEG_TIME_TOL_S` = 120 s, and the print (when present)
  is within `CROSS_TOL_PIPS` = 5.0 pips.

**`forward(services, pair, value_date, snapshot, pb)`**
- Dates from `ccycal`: the spot date; days = value date - spot date.
- F = S x DF*_base(S, V) / DF*_quote(S, V), quantised to the pair's precision (6 dp; JPY-quoted pairs 4 dp).
- points = (F - S) / pip, to 2 dp.
- Returns the record of 3.2.

**`index_factor(services, ccy, s, e)`**
- Returns I(e) / I(s) from the stored index series. Both dates must be published.
- EUR fallback: the product over TARGET2 BDs i in [s, e) of (1 + ESTR_i x n_i / 360), where n_i = calendar days to the next TARGET2 BD.
  Every BD's print is required.
- USD has no fallback: the SOFR Index only. Its dates are USGS days by construction (D10).
- Otherwise ConflictError INDEX_PENDING.

**`compounded_rate(factor, days)`** = ((factor - 1) x 360 / days), quantised to 0.0000001 (7 dp of a fraction).

**`cross_checks(...)`**, shown and stored, never priced:
- the flat-overnight outright (revision 1's method);
- the outright on the cross print;
- the FX desk's outright for the same value date, when the FX desk quotes it (up to 1Y), with its method and the difference in pips.

**Constants.** `RFR_MAX_AGE_DAYS` 10, `CURVE_MAX_AGE_BD` 3, `CROSS_TOL_PIPS` 5.0, `LEG_TIME_TOL_S` 120, `MODEL_TEXT`, `PROXY_TEXT`.

**`fixture_curves(services, d0=date(2026, 10, 27))`** installs the example's rows, for every package's tests:
- the BoE 1M..60M row of 10-27;
- the seven ECB spot rates;
- the UST par row of 10-27;
- SONIA 3.7328 % (10-26), ESTR 2.440 % (10-27) and SOFR 3.95 % (10-27).

### 3.2 Trade B: the EUR/GBP deliverable forward (`products\otc_options.py` kind FX_FORWARD, P-B)

**Pair and side.**
- EURGBP: base EUR, quote GBP. SELL = sell EUR / buy GBP.
- GBPEUR is refused with "quoted the other way round: use EURGBP" (the existing `_pair` rule).

**Formulas.**
- Dates:
  - trade date T = `fx_trade_date`;
  - spot date S = `ccycal.spot_date(EURGBP, T)` (both currencies and New York, D10);
  - value date V = `ccycal.tenor_date(EURGBP, "2Y", T)`;
  - days d = V - S.
- Spot: S_EURGBP = `fxcurves.spot` (the derived cross).
- Discount factors (D2, D5): DF*_EUR = df(discount(EUR curve, `xccy_basis_eur_bp`), S, V); DF*_GBP = df(discount(GBP curve,
  `xccy_basis_gbp_bp`), S, V).
- Outright and dealt rate:
  - F = S_EURGBP x DF*_EUR / DF*_GBP (6 dp);
  - hw_px = F x `forward_hw_bp` x `otc_spread_mult` / 10,000 (6 dp);
  - K = F - hw_px for a sale of the base; a purchase adds it.
- Amounts:
  - sell EUR A = 2,000,000.00 and buy GBP to_c(A x K) (the cross's quote leg, the existing rule);
  - USD notional = to_c(A x the EURUSD mid);
  - IM = to_c(USD notional x `forward_im_pct`).
- **Value to the Fund** (the mark, USD, for the spot date S_t of the valuation date, D19):
  - sign x A x (F_t - K) x DF*_GBP(S_t, V) x GBPUSD_t, with sign -1 for a sale;
  - F_t uses the same quantisations as the quote, so mark0 is exactly the quote's.
- **Sensitivities**, each by re-pricing without intermediate rounding, in USD:
  - the FX rows: EURUSD or GBPUSD moved, the derived cross moving with them;
  - rate01 per currency: +1 bp on every node of that currency's curve;
  - basis01 per pair: +1 bp on that level;
  - EUR OIS spread: +1 bp on the EUR grid nodes;
  - points01: F_t + 1 pip.
  - The PV deltas, -A x DF*_EUR(S_t, V) in EUR and +A x K x DF*_GBP(S_t, V) in GBP, equal the 1 % rows divided by 1 % of the USD rate,
    because S x GBPUSD = EURUSD for the derived cross.

**Worked example** (T = 2026-10-29; curves of 2026-10-27; EURUSD 1.1158, GBPUSD 1.3323, EURGBP print 0.83725).

| Quantity | Value |
|---|---|
| Spot date / value date / days | 2026-11-02 / 2028-11-02 / 731 |
| Spot | derived 1.1158 / 1.3323 = 0.83749906 -> 0.837499; print 0.83725 (-2.49 pips, inside 5.0) |
| Curve date d0; t_S / t_V | 2026-10-27; 6 / 365 = 0.016438 / 737 / 365 = 2.019178 |
| GBP z(t_S) / z(t_V) | 3.760957 % (between the SONIA node 3.732609 and 1M 3.899390) / 4.729075 % (between 24M 4.727447 and 25M 4.734519) |
| EUR z(t_S) / z(t_V) | 2.477619 % (between the ESTR node 2.473805 and 3M 2.542643) / 3.015723 % (between 2Y 3.015032 and 3Y 3.051054); spread 0.0 bp |
| DF*_EUR(S, V) / DF*_GBP(S, V) | 0.9413073853 / 0.9094909816 (basis levels 0.0 bp) |
| F = 0.837499 x 0.9413073853 / 0.9094909816 | 0.866797 (+292.98 pips) |
| hw_px / K | 0.000173 / 0.866624 |
| GBP received | 1,733,248.00 |
| USD notional / IM | 2,231,600.00 / 133,896.00 |
| mark0 = -2,000,000 x 0.000173 x 0.9094909816 x 1.3323 | -419.25 |
| PV deltas at T | EUR -1,882,614.77; GBP +1,576,373.42 |
| At T: rate01 EUR / GBP | +420.66 / -420.57 |
| At T: basis01 EUR/USD / GBP/USD | +420.66 / -420.57 (the levels move the same discount curves) |
| At T: EUR OIS spread, points01 | +423.92; -242.34 per pip |
| Cross-checks shown | flat overnight rates 0.858881 (-79.16 pips; revision 1's method); on the print 0.866539 (-2.58 pips); FX desk: no quote beyond 1Y |

**The stored pricing record.**
- It lives in the preview, in the RFQ's `quote_json` and in the contract's `terms_json.pricing_record`.
- Every field is required; a missing one refuses the quote.

| Group | Fields |
|---|---|
| Header | method "covered interest parity on the discount curves under the USD CSA: GBP observed (Bank of England OIS), EUR PROXY (ECB AAA + spread); MODEL"; `model_version` "fxcurves-2"; `valuation_ts`; `trade_date`; `spot_date`; `value_date`; `days` |
| `spot` | pair; mid; basis "derived from the USD pairs"; `legs` [EURUSD and GBPUSD, each {mid, `quote_time`, source, quality}]; `legs_gap_s`; `print` {mid, `quote_time`, source}; `diff_pips`; `tolerance_pips`; executable |
| `curves` | per currency: kind, source, `as_of`, `first_node` {series, `obs_date`, `value_pct`, z}, the nodes [(t, z)] up to the first node beyond V, `spread_bp` (EUR) with its schedule source and `updated_ts`, "continuously compounded"; t_S, t_V, z(t_S), z(t_V) |
| `dfs` | DF*_EUR and DF*_GBP (1e-10) with their arithmetic |
| `forward` | outright F, points, pip, arithmetic "F = 0.837499 x 0.9413073853 / 0.9094909816 = 0.866797" |
| `basis` | pair EURGBP; `eur_usd_level_bp` 0.0; `gbp_usd_level_bp` 0.0; `cross_bp` 0.0 (EUR minus GBP); on "both discount curves"; source "Goldman Sachs schedule (seed / as edited), ASSUMED: no free cross-currency basis source"; `updated_ts` (FX-C-11) |
| `dealer` | broker "Goldman Sachs", `forward_hw_bp`, `otc_spread_mult`, `hw_px`, K, side, `schedule_source` "GS schedule (seed / as edited, ASSUMED)", `schedule_updated_ts` |
| `im` | pct, `usd_notional_c`, `im_c`, rule |
| `usd_rates` | the EURUSD and GBPUSD mids, each with its time (the legs of the cross) |
| `limits` | "notional limits: none configured in the Goldman Sachs schedule; compliance has no OTC limit"; headline gross / NAV before and after; gross notional / NAV before and after (D18) |
| `funding` | the SFI line; the 2028 settlement-funding gap at today's balances (D9) |
| `cross_checks` | the flat-overnight outright and its difference; the print-based outright; the FX desk's outright or "none beyond 1Y" |
| `labels` | "MODEL"; "observed: Bank of England UK OIS curve of <date>"; "PROXY: ECB euro-area AAA government curve of <date> + 0.0 bp (Goldman Sachs schedule, ASSUMED): no free EUR OIS curve"; "ASSUMED: the basis levels, the dealer half-width and IM are Goldman Sachs schedule terms" |
| `settlement` | `value_date`; deliver EUR 2,000,000.00; receive GBP 1,733,248.00; the SFI funding line |

**Blocked.** The dealer gives no quote and nothing is written; the RFQ goes EXPIRED with the reason, or the request is refused, when:
- either USD leg has no executable print, or the legs' quote times are more than 120 s apart (NO_QUOTE / QUOTE_STALE);
- the cross print and the derived cross differ by more than 5.0 pips (SPOT_INCONSISTENT);
- an overnight print is older than 10 days (RATE_UNAVAILABLE), or a curve is missing or older than 3 BD (CURVE_UNAVAILABLE, naming the
  source);
- the value date lies beyond the last node of a curve (CURVE_SHORT);
- the value date is not a business day of the value calendar (VALUE_DATE, naming the calendar);
- the tenor is beyond 2Y (TENOR);
- the IM cannot be funded (INSUFFICIENT_FUNDS);
- the lifetime is not eligible.

### 3.3 Trade A: the EUR/USD float / float CCS and its inception conversion (`products\swaps_trs.py` kind XCCY, P-C)

**Terms** accepted by otc.preview, otc.rfq.request and otc.book:

    {pay_ccy "EUR", recv_ccy "USD", pay_notional "2000000.00" (or recv_notional; the other is struck at X0),
     pay_index "ESTR", recv_index "SOFR", months 24 (a multiple of 3, from 3 to 120), mtm_reset false,
     pay_spread_bp (optional; blank = the dealer's quote), start_conversion true|false,
     settlement_funding {"EUR": "BUY_WITH_USD"} (optional), client_ref (optional, unique)}

- Both indices given: the FLOAT_FLOAT variant.
- Only one given: 400 NOT_IN_THIS_BUILD "fixed / floating cross-currency swaps are not in this build".
- Each index must match its currency (USD with SOFR, EUR with ESTR), else 400 TERMS.
- `pay_basis` and `recv_basis` must be absent or ACT/360.

**Dates.** T = `fx_trade_date`; the calendar is TARGET2 + New York; SOFR dates are also USGS days (D10).
- Start S = the spot date.
- e_k = MF(S + 3k months) for k = 1..n, where n = months / 3.
- p_k = e_k + 2 BD.
- The termination date is e_n. The contract row's `maturity_date` is p_n: the last payment and the final exchange.

Example, T = 2026-10-29 (S = 2026-11-02):

| k | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| e_k | 2027-02-02 | 2027-05-03 | 2027-08-02 | 2027-11-02 | 2028-02-02 | 2028-05-02 | 2028-08-02 | 2028-11-02 |
| p_k | 2027-02-04 | 2027-05-05 | 2027-08-04 | 2027-11-04 | 2028-02-04 | 2028-05-04 | 2028-08-04 | 2028-11-06 |

With T = 10-30 instead:
- S is 2026-11-03.
- Ends: 2027-02-03, 05-03, 08-03, 11-03, 2028-02-03, 05-03, 08-03, 11-03.
- Payments: 2027-02-05, 05-05, 08-05, 11-05, 2028-02-07, 05-05, 08-07, 11-07.
- The forward's value date is 2028-11-03.

**The full value** (USD, for the spot date S_t of the valuation date t, D19):

    Full PV(t) =        sum over USD flows d of  amount_d x DF_USD(S_t, d)
                 + X_t x sum over EUR flows d of  amount_d x DF*_EUR(S_t, d)          (for d < S_t, DF(S_t, d) = 1 / DF(d, S_t))

- The flows, from the Fund's side:
  - USD: -N_USD at S (until exchanged), +C_USD,k at p_k, +N_USD at p_n;
  - EUR: +N_EUR at S (until exchanged), -C_EUR,k at p_k, -N_EUR at p_n.
- The coupons: C_L,k = N_L x (R_L,k x P_L,k - 1) + N_L x spread_L x d_k / 360.
  - R is the realised factor of the current period (`accrual_state`), or 1 before s_k.
  - P = 1 / DF_L(a, e_k), where a = s_k, or the day after the last accrued night once the period has started; it is read on the leg's
    projection curve, the curve without the basis level.
- DF_USD is the USD curve; DF*_EUR is the EUR curve plus the EUR/USD basis level. All discount factors are unquantised; the PV is rounded
  to the cent at the end.

**The balance-sheet items and the two values.**

    B(t) = balance(1360, USD) - X_t x balance(2360, EUR) + balance(1225, USD) - X_t x balance(2340, EUR)

    Clean mark (NAV overlay and the position's MV) = Full PV - B(t)
    CSA exposure (CTM VM, D8)                      = Full PV

**Deal.**
- X0 = the executable EURUSD mid pinned by the RFQ. N_EUR = 2,000,000.00; N_USD = to_c(N_EUR x X0) = 2,231,600.00.
- A* = sum over k of (d_k / 360) x DF*_EUR(S, p_k) = 1.9646274388 (1e-10). X0 x N_EUR x A* x 1 bp = 438.43.
- PV(0) = the full value with the EUR spread at 0 = -403.98:
  - -370.75 because the final exchange is paid on 2028-11-06, not 2028-11-02;
  - -33.23 from the coupons' payment delays.
- s_par = PV(0) / (X0 x N_EUR x A*) = -0.9214 bp.
- Dealt = s_par + 1.0 bp = +0.0786 bp, rounded up on the 0.1 bp grid: **+0.1 bp**. The Fund pays ESTR + 0.1 bp and receives SOFR flat.
- mark0 = PV(0) - X0 x N_EUR x A* x 0.1 bp = **-447.82**: the 1.0 bp half-width (438.43) plus the rounding (9.39). It is the cost of
  dealing, never a day-one gain.
- IM = to_c(N_USD x `xccy_im_pct`) = 44,632.00 (GS 2 %, ASSUMED).
- Indicative coupons (projected; not payable; the index decides): period 1 (92 days) EUR 12,945.83 and USD 23,571.46. The USD figures are
  projected on the Treasury proxy, which sits above SOFR; the published index decides the payment.

**Sensitivities**, each by re-pricing:
- at T: basis01 (the level +1 bp on the EUR discount curve) +437.98; rate01 USD -2.25 and EUR +2.32; EUR OIS spread +2.32; EURUSD +1 %
  (before S, the CCS alone) +6.92;
- after S (t = 2026-11-02 17:05, START done, value 2026-11-04):
  - Full PV = CSA exposure -259.44; B(t) 0.00 at X0; clean mark -259.44 (+188.38 against mark0, D19);
  - EUR PV delta -1,999,652.08: the ledger's -2,000,000.00 plus the hook's +347.92;
  - rate01 USD -1.03 and EUR +1.10; basis01 +436.82;
  - at a EURUSD close of 1.1200 the CSA exposure is -8,657.97: the notional net -8,400.00 plus the clean mark -257.97.

**The inception conversion** (`start_conversion` true; `fx.book_linked_spot`, K4):
- SELL EUR N_EUR / BUY USD at X0 - 0.2 pip = 1.11578;
- USD = fx_c(2,000,000.00, 1.11578) = 2,231,560.00, so the spread cost is 40.00;
- value date S, trade date T, linked both ways.

**The stored pricing record** (`terms_json.pricing_record`; the RFQ quote and the preview carry the same):

| Group | Fields |
|---|---|
| Header | method "RFR float / float cross-currency swap: ESTR and SOFR compounded in arrears (published indices), 2 BD payment delay, quarterly, ACT/360, spread on the EUR leg; dealer quote = par spread + half-width; MODEL on PROXY curves; basis level ASSUMED"; `model_version` "fxcurves-2" |
| `deal_fx` | pair, mid, source, `quote_time`, quality |
| `curves` | USD and EUR as in 3.2 (kind, source, `as_of`, first node, nodes up to the first beyond p_n, EUR `spread_bp`) |
| `legs` | per leg: currency, index, current print with its date and source, spread, day count, compounding, `payment_delay_bd`, calendar |
| `basis` | pair EURUSD; `level_bp`; `par_bp`; `hw_bp`; `dealt_bp`; `rounding_bp`; `on_leg` EUR; source "Goldman Sachs schedule (seed / as edited), ASSUMED: no free cross-currency basis source"; `updated_ts` |
| `schedule` | one row per period: s, e, p, days, `eur_indicative_c`, `usd_indicative_c` |
| `exchanges` | initial {date S, we_receive EUR, we_pay USD, rate}; final {date p_n, we_pay EUR, we_receive USD, rate} |
| `conversion` | in the preview: mid, rate, sell, buy, `spread_cost_c`, `value_date`; once booked: `deal_id` |
| Results | `pv_zero_c` with its parts (`final_exchange_timing_c`, `coupon_delays_c`), `annuity_star`, `mark0_c`, `basis01_c`, `rate01_c` {USD, EUR}, `net_start_flows` {USD -40.00, EUR 0.00}, `im` {pct, `im_c`, rule} |
| `limits`, `funding`, `labels` | as in 3.2; `eur_obligations` (dates, amounts, how each is funded) |

**Blocked**, each with its own code:
- no executable EURUSD print: 409 QUOTE_STALE or NO_QUOTE; only the ECB reference rate: FX_STALE (PO-08);
- ESTR or SOFR older than 10 days (RATE_UNAVAILABLE); a curve missing or older than 3 BD (CURVE_UNAVAILABLE); a date beyond the last node
  (CURVE_SHORT);
- a SOFR period start or end that is not a USGS day (SCHEDULE);
- IM or the start-date net cannot be funded: INSUFFICIENT_FUNDS;
- the conversion cannot be booked: its own blocker;
- the `client_ref` is already booked: DUPLICATE_CLIENT_REF;
- the lifetime is not eligible.

## 4. The contracts on F1:PB_GS and their lifecycle

### 4.1 Trade B (`otc_contracts` kind FX_FORWARD; OTC-000001 if it is the first)

**Booking** (the RFQ accept calls otc.book), in one command:
- IM: fund_commitment makes one Treasury sweep of 133,896.00, then OTC_IM `Dr 1330 USD 133,896.00 (OTC-...) / Cr 1010 USD`.
- The contract row:
  - `start_date` T, `maturity_date` V;
  - `notional_c` = the USD notional, `mark_c` = mark0;
  - `terms_json` with the pricing record and `settlement_funding`;
  - `client_ref`, written by `swaps.h_book` (D13).
- The SWAP instrument row; the RFQ goes ACCEPTED; the contract reads PENDING_CONFIRM until 17:05 ET (OTC_CONFIRM).

**Daily VM** (SWAP_VM, 17:05 ET from T).
- Priced at the EURUSD and GBPUSD 17:00 closes of the VM date (the derived cross), on the curves and overnight prints on or before it.
- The change is measured against the previous VM, or against 0 before the first, so day one posts the cost of dealing (-419.25).
- CTM: `Dr 1320 / Cr 1010` when the Fund posts, `Dr 1010 / Cr 2330` when it receives; funded with CSA netting.

**SFI.** On V - 2 joint BD (TARGET2 + New York for the EURUSD purchase; 2028-10-31 in the example), the job buys with USD the EUR
2,000,000.00 not already held by V, value V. The 2028 funding gap (D9) shows from day one.

**Delivery.** OTC_EXPIRY for FX forwards moves to 00:30 ET of V (fixes G12); no price input is needed.
1. `close_contract`:
   - value 0 at delivery, because the contract's value passes into the delivery deal;
   - the VM balance is unwound;
   - the IM is released, `Dr 1010 / Cr 1330` 133,896.00.
2. The linked FX deal at K: `fx_deals` FORWARD, value V, posted at once with the cross's ledger rule: `Dr 1250 GBP 1,733,248.00 / Cr 3900`
   and `Dr 3900 / Cr 2350 EUR 2,000,000.00`.
3. SETTLE_DUE settles its two rows in the same tick: the EUR comes from the SFI deal settled at 00:30, the GBP goes into 1010 GBP.
4. The P&L since the last VM is realised through `fx_result`. Status MATURED.

**Termination by the owner.** At the model value now, with the half-width against him (the existing rule); VM unwound, IM released.

### 4.2 Trade A (`otc_contracts` kind XCCY, variant FLOAT_FLOAT; SWP-000007 if it is the first; the conversion is an `fx_deals` SPOT row)

**Booking**, one command (the RFQ accept). There is no exchange before S, because the start is the spot date.
1. OTC_IM `Dr 1330 USD 44,632.00 / Cr 1010 USD`, after one Treasury sweep of 44,632.00 (fund_commitment).
2. The contract row:
   - `start_date` S, `trade_date` T (C10), `maturity_date` p_n;
   - `notional_c` 2,231,600.00 (USD), `mark_c` = mark0 (-447.82);
   - the terms of 3.3; `client_ref` (written by `swaps.h_book`).
3. The inception conversion (`fx.book_linked_spot`):
   - entry FX `Dr 1250 USD 2,231,560.00 / Cr 3900 USD` and `Dr 3900 EUR 2,000,000.00 / Cr 2350 EUR 2,000,000.00`, the EUR lines at the 8-dp
     ledger rate 1.11578;
   - settlement rows BUY_CCY USD +2,231,560.00 and SELL_CCY EUR -2,000,000.00, due S, tagged `ref_type` xccy_start and `ref_id` the
     contract.
4. The SWAP instrument row, the RFQ ACCEPTED, the events, PENDING_CONFIRM.

**START.** Key `{cid}:{S}:START`, 00:45 ET of S, one command, idem `xccy:{cid}:start`:
1. `fx.settle_linked(xccy_start, cid, S, "receive")`: `Dr 1010 USD 2,231,560.00 / Cr 1250 USD`.
2. Fund the USD lent: the need is 2,231,600.00 against 2,231,560.00 settled, so one Treasury sweep of 40.00 (fund_commitment).
3. The exchange (XCCY_EXCHANGE): `Dr 1360 USD 2,231,600.00 / Cr 1010 USD 2,231,600.00` and `Dr 1010 EUR 2,000,000.00 / Cr 2360 EUR
   2,000,000.00`, the EUR lines at X0.
4. `fx.settle_linked(xccy_start, cid, S, "pay")`: `Dr 2350 EUR 2,000,000.00 / Cr 1010 EUR 2,000,000.00`. The conversion deal is SETTLED.
5. `otc_resets` NOTIONAL_START, with detail: both legs, the conversion's deal id and legs, the funding.

If any step fails, a SAVEPOINT rolls back the whole command: SKIPPED with an ERROR attention that names the reason, then retried. The
linked rows wait; at 06:00 ET an unfinished START raises XCCY_EVENT_LATE (D7, 10.8).

**Accruals.** ACCRUE_DAILY kinds swaprcv / swappay, nights S .. e_n - 1, each from 00:30 ET of the next day.
- USD leg (received, SOFR):
  - the factor is `F_D = F_(D-1) x (1 + SOFR_(<=D) / 360)`, cumulative to_c(N_USD x (F - 1));
  - posted `Dr 1225 USD / Cr 4453 USD`.
- EUR leg (paid, ESTR + b, b = the dealt spread):
  - the factor is `F_D = F_(D-1) x (1 + ESTR_(<=D) / 360)`, cumulative to_c(N_EUR x ((F - 1) + b x n / 360)), where n = the nights of
    the period so far;
  - posted `Dr 4453 EUR / Cr 2340 EUR`, base at the command's FX rate.
- Night 1 on flat fixtures: USD 244.86 (2,231,600 x 0.0395 / 360 = 244.856...) and EUR 135.61 (2,000,000 x (0.0244 + 0.00001) / 360 =
  135.611...).
- These are estimates: they use the latest print, which runs one BD behind. Each payment trues them up to the index.

**PAY.** Key `{cid}:{p_k}:PAY`, 00:45 ET of p_k for k < n, idem `xccypay:{cid}:{p_k}`:
1. Check that both legs are accrued through e_k - 1, else SKIPPED ACCRUALS_PENDING. Check that both indices, at s_k and e_k, are
   published, else SKIPPED INDEX_PENDING (quiet on the first day, a WARN after).
2. Exact coupons: R = compounded_rate(I(e_k) / I(s_k), d_k); USD = to_c(N_USD x R x d_k / 360); EUR = to_c(N_EUR x (R + b) x d_k / 360).
3. True-up, per leg:
   - exact minus what was posted for the period, i.e. the sum of the period's entries `accr:{kind}:{cid}:{night}` for nights s_k .. e_k - 1;
   - one ACCRUAL entry, idem `accr:{kind}:{cid}:trueup:{e_k}`, memo "true-up of the nightly estimate to the published index".
4. `fx.settle_linked(xccy_event, cid, p_k, "receive")`: the SFI EUR purchase for this coupon.
5. The payment (XCCY_PAY):
   - `Dr 1010 USD / Cr 1225 USD` for the USD coupon and `Dr 2340 EUR / Cr 1010 EUR` for the EUR coupon;
   - the EUR comes from the account, i.e. the SFI deal's EUR, else the existing overdraft rule applies (FUNDING_SHORTFALL ERROR).
6. `fx.settle_linked(xccy_event, cid, p_k, "pay")`: the SFI deal's USD leg.
7. `otc_resets` PAY, with both legs' exact amounts, R, the index values and dates, and the true-ups.

After the payment, 1225 / 2340 hold only the first nights of the next period (the payment delay), never anything already paid.

**END.** Key `{cid}:{p_n}:END`, 00:45 ET of p_n:
1. the last PAY, as above;
2. the reverse exchange, `Dr 1010 USD 2,231,600.00 / Cr 1360` and `Dr 2360 EUR 2,000,000.00 / Cr 1010 EUR`, with the SFI deal settled PvP
   (its receive phase before, its pay phase after);
3. `close_contract`: VM unwound, IM 44,632.00 released, MATURED.

**VM.** SWAP_VM at 17:05 ET from T (D8).
- The change is the CSA exposure (the full value, 3.3) less the reference: the previous VM's exposure (`otc_vm.mark_c`), or 0 before the
  first. Day one posts -447.82.
- The overlay `mark_c` is the clean mark.
- Before S the exposure equals the clean mark. From S it includes the notionals' FX move since X0 and the accrued coupons.

**Termination by the owner** (otc.terminate): D17. Before S it is refused while the conversion is linked. From S it is cash-settled in USD
at the full value, with the half-width against the Fund; no EUR moves.

### 4.3 The settlement-funding instruction (`ccyfunding.py`, P-D)

**Data.**
- The contract term `settlement_funding {ccy: "BUY_WITH_USD" | "NONE"}`.
- Each contract's scheduled flows (K5, K6).

**Job OTC_CCY_FUNDING.** Added with `add_product_job` before SNAPSHOT. It runs 10:00-16:30 ET on FX business days of the funding pair, key
`{account}:{ccy}:{due}`, and is quiet when nothing falls due within 3 BD.
- It picks the due dates that carry an outflow in that currency from a contract whose instruction is ON, and whose funding trade date (the
  date whose spot date is the due date, on the pair's value calendar) is today or past.
- The need is the sum of the obligations due that day, minus the currency the account will hold on the due date:
  - only known amounts count; a CCS coupon is known once its index is published;
  - the holding is settled + receivables due by then - payables due by then, leaving out the conversion's own legs.
- When the need is above zero, it books one `fx.book_linked_spot`:
  - BUY the currency (the need) / SELL USD, at the executable mid plus the desk spread, value = the due date;
  - link `{type: "otc_funding", contracts: [...], due, obligations: [...]}`;
  - `settle_ref` (xccy_event, cid) when every obligation it funds is one CCS's event; otherwise none, and SETTLE_DUE settles it before the
    forward's 00:30 delivery.
- Pending outcomes:
  - no executable quote: SKIPPED NO_QUOTE, retried every 5 minutes until 16:30, with a WARN at 16:30 that the obligation may overdraw;
  - a coupon not yet known: SKIPPED INDEX_PENDING;
  - USD not available on the value date: the deal is booked and its USD leg waits SETTLEMENT_UNFUNDED with an ERROR (D9); nothing moves
    cash on its own.

**Light command `otc.settlement_funding {contract_id, currency, mode}`** (actor ui allowed).
- It writes a SETTINGS_CHANGED event with the before and after values.
- SFI deals already booked stay: they are trades.

**`plan(services, account_id)`** (pure) lists the upcoming obligations, the planned purchases and the USD the account and Treasury will
hold for each, so the previews, the drawers, the Risk ladder and the report all show the same gap.

**Example.**

| Obligation | Funding trade date | Purchase | Value date |
|---|---|---|---|
| CCS coupon 1 | 2027-02-02 (e_1, the index is known) | EUR 12,9xx.xx (the exact coupon) | 2027-02-04 |
| Forward delivery | 2028-10-31 | EUR 2,000,000.00 (about USD 2.23-2.31 million; the gap of D9) | 2028-11-02 |
| CCS final exchange + coupon 8 | 2028-11-02 | EUR 2,000,000.00 + the coupon | 2028-11-06 |

### 4.4 Currency balances before and after (F1:PB_GS and F1:TREASURY; the example, ignoring other activity)

| Stage | PB 1250 USD | PB 2350 EUR | PB 1360 USD | PB 2360 EUR | PB 1010 USD / EUR | PB 1330 (IM) | TREASURY 1010 USD | EUR exposure (nominal) | GBP exposure |
|---|---|---|---|---|---|---|---|---|---|
| 0. Before | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 / 0.00 | 1,594,691.30 | 2,103,950.48 | 0.00 | 0.00 |
| 1. Booked (T) | +2,231,560.00 | -2,000,000.00 | 0.00 | 0.00 | 0.00 / 0.00 | 1,773,219.30 | 1,925,422.48 | -4,000,000.00 (conversion payable 2.0 M + forward 2.0 M) | +1,733,248.00 (forward) |
| 2. After START (S) | 0.00 | 0.00 | +2,231,600.00 | -2,000,000.00 | 0.00 / 0.00 | 1,773,219.30 | 1,925,382.48 | -4,000,000.00 (CCS 2.0 M + forward 2.0 M) | +1,733,248.00 |
| 3. After the 2028 delivery and final exchange | 0.00 | 0.00 | 0.00 | 0.00 | USD +2,231,600.00 less the SFI USD paid / EUR 0.00 | the IM of the other contracts | per the 2028 flows | 0.00 | GBP cash +1,733,248.00 |

Every non-USD amount is a ledger balance posted by its own entry. Nothing is unaccounted for.

## 5. Cash and NAV

**Translation.** NAV = the sum over currencies of fx_c(S_c + U_c) (accounting rule FX_RESULT).
- EUR and GBP balances (1010, 1250 / 2350, 1360 / 2360, 1225 / 2340) are translated at the current mid.
- The contracts' marks are in USD.

**Day-one NAV effect, at mid: -907.07.**

| Item | Amount |
|---|---|
| The conversion's spread, in `fx_result` once the 2350 EUR payable is valued at mid | -40.00 |
| CCS mark0 (the half-width 438.43 and the rounding 9.39) | -447.82 |
| Forward mark0 | -419.25 |
| IM (1330) and VM (1320 / 2330) | 0.00: collateral, NAV-neutral |
| Total | -907.07 |

**Nothing is counted twice.**
1. The CCS mark leaves out the notionals and the accrued coupons, which are balance-sheet lines.
2. VM on the CSA exposure moves cash against 1320 / 2330 only.
3. Until delivery the forward's value is only its mark. At delivery the mark closes at 0 (VM unwound) in the same command that books the
   delivery deal at K, and the value is realised through the FX result of the delivered legs.
4. SFI purchases are spot deals; their spread goes to `fx_result`.
5. Accrual true-ups move 1225 / 2340 against 4453 only.
6. The spot-date convention (D19) is not a second count. A started CCS's clean mark carries about two days of net accrual ahead of the
   ledger; each night's posting moves one day from the mark into 1225 / 2340.
7. Risk counts the ledger part once and the hooks' off-ledger part once (D14).

**P&L lines.**

| Source | Line |
|---|---|
| Coupons and their true-ups | 4453 (financing / income bucket, per C11) |
| Contract marks | the overlay (unrealised) |
| Conversions and FX revaluation | `fx_result` |
| Close-outs (terminations, D17) | 4450 |
| Re-marks caused by editing a schedule term (D5) | their own named line in the owner's NAV bridge (10.7) |

**Cash never goes below zero.** The checker verifies that F1:PB_GS and F1:TREASURY are at or above zero in USD, EUR and GBP after the
booking (C5). The forward check (9 item 5) verifies the same at S.

## 6. Risk, margin and liquidity (what the booking report and the Risk page show)

**Per trade and combined.**
- Example inputs; the valuation is at 2026-11-02 17:05, after START, with values for 2026-11-04.
- The ledger part comes from `_currency_balances` and the rest from the hooks (D14). Every number is re-priced.

| Measure | Trade A (CCS + conversion) | Trade B (forward) | Combined |
|---|---|---|---|
| EUR nominal | -2,000,000.00 (2360; 2350 before S) | -2,000,000.00 | -4,000,000.00 |
| EUR PV delta | -1,999,652.08 (ledger -2,000,000.00, hook +347.92) | -1,882,871.00 | -3,882,523.08 |
| USD nominal | +2,231,600.00 (1360) | none | +2,231,600.00 |
| GBP nominal / PV delta | none | +1,733,248.00 / +1,576,702.24 | +1,733,248.00 / +1,576,702.24 |
| EURUSD +1 % (GBPUSD held) | -22,312.12 | -21,009.07 | -43,321.19 |
| GBPUSD +1 % (EURUSD held) | 0.00 | +21,006.40 | +21,006.40 |
| EURGBP +1 % (EURUSD held, so GBPUSD / 1.01) | 0.00 | -20,798.42 | -20,798.42 |
| EURGBP +1 % (GBPUSD held: the same as EURUSD +1 %) | -22,312.12 | -21,009.07 | -43,321.19 |
| EURUSD +5 % / -5 % (GBPUSD held) | -111,560.59 / +111,560.59 | -105,045.37 / +105,045.37 | -216,605.96 / +216,605.96 |
| GBPUSD +5 % / -5 % (EURUSD held) | 0.00 | +105,032.02 / -105,032.02 | +105,032.02 / -105,032.02 |
| EURGBP +5 % / -5 % (EURUSD held) | 0.00 | -100,030.49 / +110,560.02 | -100,030.49 / +110,560.02 |
| EUR rates +1 bp | +1.10 | +419.56 | +420.66 |
| GBP rates +1 bp | 0.00 | -419.51 | -419.51 |
| USD rates +1 bp | -1.03 | 0.00 (USD only translates) | -1.03 |
| EUR/USD basis level +1 bp | +436.82 | +419.56 (its EUR discount curve) | +856.38 |
| GBP/USD basis level +1 bp | 0.00 | -419.51 | -419.51 |
| Forward points +1 pip | 0.00 | -242.39 | -242.39 |

Before S, on T, the picture differs:
- the forward alone moves -105,031.08 for EURUSD +5 %;
- the CCS alone moves +34.59, because its start and final exchanges offset;
- the conversion's EUR 2,000,000 payable (2350) moves NAV by -111,580.00 but no VM.

**What the combination is.** It is neither a USD bridge nor a pure EUR/GBP position.
- The CCS is short EUR / long USD; the forward is short EUR / long GBP.
- Together: short EUR about 3.88 million in PV, against long USD 2.23 million and long GBP 1.58 million in PV.

**Counterparty and collateral (Goldman Sachs).**
- IM held: 1,594,691.30, rising to 1,773,219.30 (+178,528.00).
- VM posted: 945,325.99, plus the day-one VM of 867.07 at the first 17:05 (447.82 + 419.25).
- The two new contracts are worth about -867 to the Fund on day one: owed to GS, and collateralised.
- The Fund's exposure to a GS default = the IM posted (assumed not segregated, ASSUMED) + any VM posted beyond the contracts' negative value.
  It is shown per prime broker on the Risk page.

**Notional limits (D18).** None configured.
- The headline gross exposure / NAV does not move, because both trades sit outside the headline.
- The gross notional rises by USD 4,463,200.00.
- The report prints both ratios before and after.

**Liquidity.**
- Treasury USD: 2,103,950.48, falling to 1,925,422.48 at booking (IM sweeps 178,528.00), and by a further 40.00 at S.
- VM stress, EURUSD +5 % with GBPUSD held:
  - after S, VM calls of 216,605.96;
  - before S, 104,996.49 (the forward's 105,031.08 less the CCS's 34.59); the conversion's 111,580.00 shows only in NAV until S.
- Scheduled flows over the next 24 months (projected; the indices decide):
  - USD coupons in, about 23,600-28,300 a quarter;
  - EUR coupons out, about 12,900-16,400 a quarter, each funded by an SFI EUR purchase of the same size (about USD 14,400-18,300 a
    quarter at today's spot);
  - 2028-11-02: deliver EUR 2,000,000.00, receive GBP 1,733,248.00;
  - 2028-11-06: pay EUR 2,000,000.00 plus the last coupon, receive USD 2,231,600.00 plus the last coupon.
- **The settlement-funding gap (D9).** The 2028-10-31 purchase for the forward is short by about USD 242,261.57 at today's balances and spot
  (318,682.31 at the outright 1.153030).
  - The final exchange of 2028-11-06 is self-funding apart from the FX move: its SFI purchase settles PvP against the USD 2,231,600.00
    returned.
  - The GBP 1,733,248.00 received stays GBP cash unless the owner converts it.

**Risk page changes** (P-B, D14):
- `_currency_balances` stays the only ledger part; the hooks add the off-ledger part.
- `_pos_risk` puts OTC FX_FORWARD in the "FX forwards (notional)" bucket with headline False. EQ_FORWARD stays in the headline: it is
  equity exposure.
- A new `fx_sensitivities` block (K7) replaces the single +/-5 % USD scenario with the rows above.
- OTC FX contracts leave the USD DV01 rows and the rates scenario; their rate01 is shown per currency (the IRS DV01 on the USD par curve stays
  as it is).
- The counterparty block gains the per-PB line above; the liquidity ladder gains the SFI purchases and the gap.
- Every number is labelled MODEL, with PROXY or ASSUMED where its curve or level is.

## 7. Contracts between packages

Each contract is built first by its owner; consumers code against it.

### K1. `ccycal` (P-A; pure)

**Calendars.**
- `TARGET`, `USNY`, `GBLO` and `USGS` are calendar objects. `for_ccy(ccy)` returns the calendar for USD, EUR or GBP, else None.
- Each object has `is_business_day`, `roll_following`, `roll_back`, `modified_following`, `add_business_days`, `month_end`,
  `holidays(year)`, `name`, `limited` and `note`.
- `joint(*cals)`; `value_calendar(services, pair)` = both currencies for a USD pair, both currencies and USNY for a cross.
- A currency without a calendar uses `services.cal` with `limited` True.

**Dates.**
- `spot_date(services, pair, trade_date)`: `instruments.fx_spot_days(pair)` days with the USD T+1 rule and the cross rule of D10.
- `tenor_date(services, pair, tenor, trade_date)`: ON, 1W, 2W, 1M-12M, 18M, 1Y, 2Y, 24M. Months count from the spot date, MF on the value
  calendar with the end-of-month rule; weeks roll following.
- `OTC_TENORS = ("1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "18M", "2Y")`.
- `schedule(cal, anchor, months, step_months, pay_delay_bd=0, sofr=False)` returns [(s, e, p)] with e = MF(anchor + k x step) and p =
  add_business_days(e, pay_delay_bd). With `sofr`, a start or end that is not a USGS day raises 400 SCHEDULE.

### K2. Rate series, curve rows, tables and the PB terms (P-A)

**`tables.RFR`:**

| Currency | series | index | basis | name | administrator / primary source |
|---|---|---|---|---|---|
| USD | SOFR | SOFR_INDEX | 360 | SOFR | NY Fed (FRED fallback) |
| EUR | ESTR | ESTR_INDEX | 360 | ESTR | ECB (FRED fallback) |
| GBP | IUDSOIA | SONIA_INDEX (IUDZOS2) | 365 | SONIA | Bank of England IADB (FRED fallback) |

**`tables.CURVES`:**

| Currency | Curve id | Source | Grid | Kind |
|---|---|---|---|---|
| GBP | BOE_OIS_SPOT | BoE `latest-yield-curve-data.zip`, "OIS daily data current month.xlsx", sheet "3. spot, short end" | 1M..60M, % cc | observed |
| EUR | ECB_AAA_SPOT | ECB YC `B.U2.EUR.4F.G_N_A.SV_C_YM.SR_<tenor>` | 3M, 6M, 9M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, % cc | PROXY (+ `eur_ois_spread_bp`) |
| USD | UST_PAR | treasury.gov (existing) | the IRS desk's semi-annual bootstrap | PROXY |

**Storage.**
- New `RateStore` series: ESTR, ESTR_INDEX, SOFR_INDEX and SONIA_INDEX; IUDSOIA is re-sourced (the BoE first).
- New curve rows in `curve_points` (series, as_of, tenor, value) through `rates.record`: BOE_OIS_SPOT, ECB_AAA_SPOT, and ECB_AAA_SV (the six
  Svensson parameters, for the self-check only).
- Index levels are not percents; the units note in `quotes\rates.py` says so.

**Providers.**
- `EcbData`: `estr(n)`, `estr_index(n)` and `aaa_spot(n)`, with a 60 s timeout, 2 attempts, and an HTML body read as unavailable.
- `NyFed.sofr_index(n)`.
- New `BoeData`:
  - `iadb(series, since)` reads the CSV; `ois_curve()` reads the zip with stdlib `zipfile` and `xml.etree`;
  - it always sends GOV_UA;
  - a 403 is "unavailable" with the response's reference, and no other agent is ever tried;
  - the providers.py docstring that says "never fetched" is rewritten to this rule.
- The FRED fallbacks.

**Worker.**
- Slot `rfr`: every 30 min from 02:00 to 12:00 ET on weekdays until the day's prints arrive, then every 3 h.
- Slot `curves`: once per business day, the BoE zip after 07:00 ET and the ECB curve after 08:00 ET. Hourly retries until the new date
  appears, at most 6 a day.
- Health rows `ecb`, `nyfed`, `boe` and `fred`.

**`prime.py`:** the four fields of D5, their seeds, signed values for the levels and the spread, and the hints. P-A owns `prime.py` in this
release.

### K3. `fxcurves` (P-A)

- `curve`, `discount`, `df`, `spot`, `forward`, `index_factor`, `compounded_rate`, `cross_checks` and `fixture_curves`, as specified in
  3.1.
- Constants: `RFR_MAX_AGE_DAYS`, `CURVE_MAX_AGE_BD`, `CROSS_TOL_PIPS`, `LEG_TIME_TOL_S`, `MODEL_TEXT`, `PROXY_TEXT`.
- Errors are ConflictError with RATE_UNAVAILABLE, CURVE_UNAVAILABLE, CURVE_SHORT, INDEX_PENDING, NO_QUOTE or SPOT_INCONSISTENT.

### K4. `fx.py` (P-D)

**Value dates.** `spot_date` and `tenor_date` use `ccycal` for USD / EUR / GBP pairs; other pairs are unchanged.

**Funding check.**
- `scheduled_otc_flows(conn, account_id, ccy, by_date, services)` returns (recv_c, pay_c): the unposted OTC exchange or delivery legs due on
  or before `by_date`, read through each product's `scheduled_flows`.
- `funding_check` adds them, reported separately as `otc_flows_c`.

**Linked spot deals.** `book_linked_spot(ctx, account_id, buy_ccy, sell_ccy, amount_c, amount_ccy, value_date, mid, link, settle_ref=None)`
returns {deal, entry_id, settlements}:
- an FX desk spot deal at mid -/+ the desk spread;
- the value date must equal `ccycal.spot_date`;
- the settlement rows are tagged with `settle_ref`;
- the OA-11 check counts the scheduled OTC flows.

**Linked settlement.**
- `settle_linked(ctx, ref_type, ref_id, due_date, phase)` settles the "receive" or the "pay" legs of the tagged rows due on or before
  `due_date`, through `settle_deal(legs=...)`.
- `LINKED_SETTLE_TYPES = ("xccy_start", "xccy_event")`.
- `due_deals` never returns a tagged row while its contract is OPEN and its event has not run. When the contract closes, the rows are
  untagged and returned like any other (D7).

### K5. `swaps.py` and `swaps_trs.py` (P-C)

**Owner reference (D13).**
- The column `otc_contracts.client_ref` with a partial unique index (where not null).
- `h_book` checks and writes it for every kind: a contract with the ref in any status answers 409 DUPLICATE_CLIENT_REF and writes nothing.
- `otc.rfq.request` refuses only while a contract with the ref exists or an RFQ with the ref is REQUESTED or QUOTED. Its accept passes
  `terms.client_ref` into the booking.
- `contract_view` adds `client_ref`, `linked_deals` and `confirmation`.

**Leg and fixing hooks.**
- `LegSpec` gains `index="SOFR"` and `spread=Decimal(0)`. Defaults keep the IRS exactly as it is.
- `fixing_for(services, d, series="SOFR")`.

**VM hooks.**
- `_vm_core`, under CTM, uses `inputs["csa_exposure_c"]` (the full value) when present: the reference becomes `otc_vm.mark_c` (that
  exposure) and the overlay stays `mark_c`.
- The key start of D8 (G16, G17), the same in `vm_keys` and in `_vm_core`'s date guard. A NULL `trade_date` never raises.

**Contract hooks in `swaps_trs`.**
- `scheduled_flows(conn, row)` returns [{date, ccy, amount_c (+ in / - out), what, event, known}].
- `risk_sensitivities(services, row, snapshot=None)` returns ONLY the off-ledger part (D14):
  - `fx_delta` {ccy: units_c} is the clean mark's FX delta;
  - `fx_nominal` {ccy: units_c} is the exchanges not yet on the ledger;
  - `rate01_c` {ccy: usd_c}, `basis01_c` {pair: usd_c}, `notes`.
- `terminate` follows D17 for both variants.
- The terms JSON of the float variant is that of 3.3, with `variant` "FLOAT_FLOAT", periods [[s, e, p], ...], `period_months` 3,
  `payment_delay_bd` 2, calendars, `termination_date`, the spread block, `start_conversion` and `pricing_record`.

**UI fixture.** `tests\fixtures\terminal\xccy_float_preview.json`, a real preview payload for P-B's UI.

### K6. `otc_options.py` (P-B)

- `scheduled_flows(conn, row)`: FX_FORWARD's two delivery legs on the value date.
- `risk_sensitivities(services, row, snapshot=None)`: {fx_delta, fx_nominal (both legs until delivery, then nothing), rate01_c, basis01_c,
  points01_c}.
- The FX_FORWARD terms gain `pricing_record`, `points`, `df_base`, `df_quote` and `settlement_funding`.

### K7. `risk` (P-B)

- `report(...)` carries `fx_sensitivities`:
  - `by_currency` [{ccy, nominal, nominal_ledger, nominal_hooks, pv_delta, pv_delta_ledger, pv_delta_hooks, usd_per_1pct, usd_per_5pct,
    rows}];
  - `pairs` [{pair, shock, held, pnl}];
  - `rates01` [{ccy, usd_per_bp, rows}], `basis01` [{pair, usd_per_bp, rows}], `points01` [...];
  - `counterparty` [{broker, mtm, im_posted, vm_posted, vm_received, exposure}];
  - `ladder` [{date, ccy, amount, what}], with the SFI purchases and the funding gap from `ccyfunding.plan` (read lazily);
  - `limits` {headline_gross_nav, gross_notional_nav, caps: "none configured"}, and `notes`.
- `exposure_by_currency` = the ledger part + the hooks' part.
- `otcplan`'s report reads this block.

### K8. `ccyfunding` (P-D)

- The job OTC_CCY_FUNDING and the command `otc.settlement_funding`.
- `plan(services, account_id)` is pure. It returns the upcoming obligations, the planned purchases and the USD cash on each date, so the
  previews, the drawers, the ladder and the report share one gap.

### K9. `otcplan.reprice` (P-D)

- An independent pricer for the PB forward and the float / float CCS, computed from a pricing record alone.
- Stdlib `decimal` only. It never imports `fxcurves`, `ccycal`, `fx` or the products, so a pricing bug shared by the quote and the
  contract cannot pass the checks (FX-C-05).
- Rules: the formulas of 3.2 and 3.3, the record's dates and nodes, Decimal precision 34. The forward's discount factors are quantised
  to 1e-10 and F, hw_px and K to 6 dp. The CCS's discount factors stay unquantised; its PV is rounded to the cent at the end. The par
  spread stays unrounded before the 0.1 bp ceiling.

## 8. Packages (4 builders, file-disjoint)

**FINSIM1_PLAN section 3 applies unchanged.**
- Ownership and hooks first.
- Both suites green on py-3.12 and py-3.14 (`tests\run_terminal_suite.py`).
- Hand-worked money tests, with the arithmetic in comments.
- Honesty and the UI rules.
- Verify like Build 2, and never deploy, run git, change VERSION, write `~\.finsim` or touch ports 8765 / 8865.
- One log line in the result; the orchestrator is the only writer of BUILD_LOG.

**Tier 1, within about 45-60 minutes:** the hooks K1-K9 marked "tier 1" below, so that every consumer imports and runs.
- A consumer whose hook has not landed calls it lazily, inside the function, with a fallback that answers "unavailable".
- Any new optional module keeps its import, `register` and `bind` exception-free at every save (15.2 step 3, critic S-14).
- Every package's tests use `fxcurves.fixture_curves`: the curves of the worked example.

### P-A: RFR and curve data, currency calendars, FX curves, the PB curve terms (6.75-8 h)

**Owns:**
- `quotes\providers.py`, `quotes\parsers.py`, `quotes\rates.py`, `quotes\worker.py`, `tables.py`, `prime.py`;
- NEW `ccycal.py` and `fxcurves.py`;
- NEW tests `test_terminal_ccycal.py`, `test_terminal_rfr_data.py` and `test_terminal_fxcurves.py`; `test_terminal_prime.py` for intended
  changes; NEW `tests\terminal_rfr_selfcheck.py`;
- NEW fixtures `tests\fixtures\terminal\rfr\`: the 10-27 rows, a two-row synthetic workbook in the BoE layout, small CSV samples.

**Tier 1:**
- `ccycal` complete (pure, small);
- `tables.RFR` and `tables.CURVES`;
- the D5 fields and seeds in `prime.py`;
- `fxcurves` with every signature and `fixture_curves`. Live curves answer CURVE_UNAVAILABLE until task 2 lands.

**Tasks (hours).**
1. `ccycal` (D10, K1) (1.5 h). Test with these holiday tables, checked by hand and by the designer's `common4.py` / `dates4.py`:

   | Year | TARGET2 | New York (Fed) | London |
   |---|---|---|---|
   | 2026 | 01-01, 04-03, 04-06, 05-01, 12-25, 12-26 | 01-01, 01-19, 02-16, 05-25, 06-19, 09-07, 10-12, 11-11, 11-26, 12-25 | 01-01, 04-03, 04-06, 05-04, 05-25, 08-31, 12-25, 12-28 |
   | 2027 | 01-01, 03-26, 03-29, 05-01, 12-25, 12-26 | 01-01, 01-18, 02-15, 05-31, 07-05, 09-06, 10-11, 11-11, 11-25 (no 06-19 or 12-24: Saturday holidays are not observed) | 01-01, 03-26, 03-29, 05-03, 05-31, 08-30, 12-27, 12-28 |
   | 2028 | 01-01, 04-14, 04-17, 05-01, 12-25, 12-26 | 01-17, 02-21, 05-29, 06-19, 07-04, 09-04, 10-09, 11-23, 12-25 | 01-03, 04-14, 04-17, 05-01, 05-29, 08-28, 12-25, 12-26 |

   USGS adds Good Friday (2026-04-03, 2027-03-26, 2028-04-14) unless the hand-maintained list says SIFMA recommended only an early close.

   Date cases to pin:
   - spot dates: EURUSD and EURGBP traded 2026-10-29 settle 2026-11-02; EURUSD traded 2027-03-25 settles 2027-03-31;
   - the USD T+1 rule: EURUSD traded 2026-11-10 settles 2026-11-12; the cross rule: EURGBP traded 2026-11-24 settles 2026-11-27;
   - 2W EURGBP from 2026-10-29: 2026-11-16; 24M EURGBP from 2026-10-29: 2028-11-02; from 2026-10-30: 2028-11-03;
   - end of month: spot 2026-11-30 + 24M = 2028-11-30;
   - the CCS schedules of 3.3, from T = 10-29 and T = 10-30;
   - a SOFR schedule (New York only, a synthetic test) ending on Good Friday 2027-03-26 is refused.
2. Data (K2, D12) (2.5-3 h):
   - the providers and parsers: ECB (ESTR, the index, the AAA spot rows and the Svensson parameters), NY Fed (SOFR Index), BoE (IADB CSV,
     yield-curve zip), and the FRED fallbacks;
   - the `rates.py` series, the curve rows and the units note; the worker slots and the health rows;
   - parser tests on the recorded samples:
     - a test getter asserts GOV_UA on every BoE request;
     - a BoE 403 is "unavailable" and no second request is sent;
     - an ECB HTML body is "unavailable"; the 60 s timeout is passed through;
     - SONIA from the BoE first, FRED only when the BoE fails, both as-of dates recorded.
3. `fxcurves` (3.1, K3) (1.5-2 h). Tests:
   - the first nodes: SONIA 3.732609, ESTR 2.473805, SOFR 4.004641 (% cc);
   - the USD bootstrap: DF 0.5 / 1.0 / 1.5 / 2.0 / 2.5 / 3.0 = 0.9787608887 / 0.9564575844 / 0.9340077958 / 0.9111609015 / 0.8890996871 /
     0.8671214018, zero rates 4.293581 / 4.451884 / 4.551366 / 4.651789 / 4.701837 / 4.752543;
   - z_GBP(t_S) 3.760957 and z_GBP(t_V) 4.729075; z_EUR(t_S) 2.477619 and z_EUR(t_V) 3.015723;
   - DF*_EUR(S, V) 0.9413073853, DF*_GBP(S, V) 0.9094909816, F 0.866797, points 292.98;
   - the spot: derived 0.837499 with the print 0.83725 (-2.49 pips) accepted; a print of 0.83690 (-5.99) answers SPOT_INCONSISTENT; legs
     121 s apart are not executable;
   - staleness: an overnight print 10 vs 11 days old; a grid 3 vs 4 BD old; CURVE_SHORT at 61 months for GBP;
   - `index_factor` from published indices, and the EUR fallback, which must equal the index ratio to 1e-9 on a synthetic quarter with
     weekends; `compounded_rate` rounding;
   - the cross-checks: flat overnight 0.858881; on the print 0.866539.
4. `prime.py` (D5) (0.5 h): the four fields, the seeds on all nine brokers, signed values for the levels and the spread, the hints, and
   SETTINGS_CHANGED on edit (the existing rule).
5. `terminal_rfr_selfcheck.py` (read-only, network, run by V) (0.5 h):
   - over the last full calendar quarter, compare the ECB index ratio with compounding the ECB daily ESTR on TARGET2 weights, and the NY
     Fed SOFR Index ratio with compounding NY Fed daily SOFR over the published days; |difference| must be below 1e-8 (risk R6);
   - on the latest BoE row, the 1Y and 2Y spot rates must equal the average of the published forwards within 1 bp (pins continuous
     compounding);
   - on the latest ECB row, SR_2Y must equal the Svensson formula of the published parameters at 2Y within 1e-6.

**Acceptance.** Both suites green; tasks 1-5 pass; real fetches (gently, once each, GOV_UA for the BoE) return ESTR, ESTR_INDEX,
SOFR_INDEX, SONIA from the BoE, the BoE OIS row and the ECB spot row.

### P-B: PB OTC FX forward to 2 years, risk, and the PB OTC screens (7-8.5 h)

**Owns:**
- `products\otc_options.py` and `risk.py`;
- `static\components\otcticket.js`, `swapticket.js` and `pbschedule.js`;
- `static\pages\risk.js`, `derivatives.js` and `markets_otc.js` (if needed);
- NEW tests `test_terminal_otc_fx_forward.py` and `test_terminal_risk_fx.py`; `test_terminal_risk.py` for intended changes.

**Tier 1:** stubs of `otc_options.scheduled_flows` and `risk_sensitivities` (K6); the `fx_sensitivities` block's shape (K7), with empty
rows.

**Tasks (hours).**
1. FX_FORWARD (3.2, D3) (3-3.5 h):
   - `fxcurves` for USD / EUR / GBP pairs: tenors to 2Y with 2W and 18M; broken dates; the value calendar;
   - the derived spot and the 5-pip guard; the basis levels;
   - the full pricing record (curves, basis, limits, funding, cross-checks with the FX desk's outright);
   - the legacy path for other pairs: up to 1Y, the LIMITED label, 400 TENOR beyond;
   - the sensitivities by re-pricing; the delivery-check note (G11);
   - delivery at 00:30 ET on V (G12; EXPIRY_ET for FX_FORWARD; no close needed, the memo cites K);
   - `settlement_funding` in the terms; `scheduled_flows` and `risk_sensitivities` (the off-ledger rule, D14);
   - a confirmation block in `contract_detail`: parties, dates, amounts, rate, CSA, IM, settlement instructions (4.1 and section 10.7),
     the pricing record.
2. Risk (section 6, D14, K7) (1.5-2 h):
   - the ledger + hook rule, and the headline bucket;
   - `fx_sensitivities`, with per-currency rate rows; OTC FX contracts leave the USD DV01 rows and the rates scenario;
   - the counterparty line, the ladder (`ccyfunding.plan`, read lazily) and the limits block.
3. UI (2-2.5 h):
   - Forwards ticket:
     - 18M and 2Y;
     - the record: the spot (derived, print, difference), the curves with their dates and kinds, the discount factors, points, F, the
       half-width, K;
     - the labels and the cross-checks (with the FX desk's outright); the spot date and days from spot;
     - the delivery, SFI, limits and funding lines.
   - Swap ticket, XCCY:
     - a fixed / floating choice per leg (floating is EUR ESTR or USD SOFR only; anything else explains why not);
     - months in 3-month steps for floating legs;
     - the spread lines: the basis level (ASSUMED), the par spread with its two parts, the half-width and the dealt spread;
     - the schedule with payment dates; the exchanges;
     - a checkbox "sell the EUR received at the start for USD (inception conversion)" and the SFI checkbox;
     - the net start flows.
   - Drawers: legs and fixings, true-ups, linked deals, the confirmation.
   - GS schedule editor: the four new fields under "OTC dealing", with signed bp values allowed for the levels and the spread, and the
     re-mark hint.
   - Risk FX block.
   - 0 console errors at 1440 and 1100 px.

**Money tests:**
- B-T1: the 3.2 example. S 0.837499 (print 0.83725, -2.49 pips); DF*_EUR 0.9413073853; DF*_GBP 0.9094909816; F 0.866797; points 292.98;
  hw_px 0.000173; K 0.866624; GBP 1,733,248.00; IM 133,896.00; mark0 -419.25; V 2028-11-02. The labels: observed GBP, PROXY EUR, ASSUMED.
- B-T2: 2W = 2026-11-16.
- B-T3: holiday value dates refused.
  - EURGBP on 2027-03-29: "not a business day in EUR (TARGET2)".
  - EURUSD on 2026-11-11: "not a business day in USD (New York)".
  - EURGBP on 2026-11-26: "not a business day in USD (New York): the EUR is bought against USD for the same value date".
- B-T4: delivery at 00:30 ET on V.
  - With the SFI deal settled first: `Dr 1250 GBP 1,733,248.00 / Cr 3900` and `Dr 3900 / Cr 2350 EUR 2,000,000.00`, both settled;
    IM 133,896.00 released; MATURED.
  - Without EUR: SETTLEMENT_UNFUNDED, retried. Never an invented conversion.
- B-T5: the delivery note is not empty; the value-date refusal highlights the date field.
- B-T6: risk after B alone, at T.
  - EUR -2,000,000.00 and GBP +1,733,248.00 nominal; PV deltas EUR -1,882,614.77 and GBP +1,576,373.42.
  - EURUSD +5 % gives -105,031.08; GBPUSD +5 % gives +105,010.12; EURGBP +5 % (EURUSD held) gives -100,009.63.
  - A fixture EUR balance in 2360 is counted once: the forward's hook adds only its own legs.
- B-T7: the OTC FX forward is not in the headline.
- B-T8: at T, rate01 EUR +420.66 and GBP -420.57; basis01 EUR/USD +420.66 and GBP/USD -420.57; EUR OIS spread +423.92; points01 -242.34.
- B-T9: the record carries the basis block (EURGBP, 0.0 / 0.0 / cross 0.0, GS schedule ASSUMED), the limits block ("none configured"),
  the funding line and the cross-checks (flat 0.858881; print 0.866539; FX desk "none beyond 1Y").
- B-T10: a legacy pair (USDJPY 1Y) keeps the legacy method with its label; USDJPY 18M answers 400 TENOR.
- B-T11: the PB EURUSD 2Y outright from 2026-11-02 equals the CCS's curves: 1.1158 x DF*_EUR / DF_USD = 1.153030 (OTC-04).
- B-T12 (in integration step 2, once P-C's hooks have landed): both trades after S. The section-6 table: EUR nominal -4,000,000.00; EUR PV
  delta -3,882,523.08; EURUSD +5 % -216,605.96; EUR/USD basis01 +856.38.

### P-C: XCCY float / float, VM on the CSA exposure, owner reference, RFQ, termination (7.5-8.5 h)

**Owns:**
- `products\swaps_trs.py` and `products\swaps.py`;
- NEW `tests\fixtures\terminal\xccy_float_preview.json`;
- NEW test `test_terminal_xccy_float.py`; `test_terminal_swaps_trs.py`, `test_terminal_swaps.py` and `test_terminal_rfq.py` for intended
  changes, each with its reason.

**Tier 1:**
- `LegSpec(index, spread)` and `fixing_for(series)`;
- the `client_ref` column, its index, and the `h_book` check and write for every kind;
- the `_vm_core` CSA hook and the key start with the NULL guard;
- stubs of `swaps_trs.scheduled_flows` and `risk_sensitivities` (fixed / fixed: start and end exchanges; float: once task 2 lands);
- the preview fixture, from a stub preview, refreshed when task 2 lands.

**Tasks (hours).**
1. `swaps.py` (2.5-3 h):
   - `accrue_leg` COMPOUND_ARREARS with `spec.index` and `spec.spread` (the cumulative formula of 4.2), the IRS path byte-identical;
   - `_vm_core` on the CSA exposure (D8); the VM key start (G16, G17);
   - `client_ref` and the RFQ request rule (D13);
   - `contract_view` and `contract_timeline`: the client_ref, the conversion, the true-ups, the SFI deals;
   - `_quote_terms`, `_quote_moved` and `rfq_describe` for the float variant:
     - the compare covers `deal_rate`, `pay_spread_bp` and `recv_spread_bp`;
     - the words read "deal FX 1.1158; pay EUR ESTR + 0.1 bp / receive USD SOFR flat; EUR 2,000,000.00 / USD 2,231,600.00; 2026-11-02 to
       2028-11-06";
   - `_display_fields` for the float variant.
2. `swaps_trs.py`, the float variant (3.3, 4.2, D4-D8, D17, G1-G6, G15-G18) (4.5-5 h):
   - parse; dates (`ccycal.schedule` with the USGS guard);
   - the deal: the `fxcurves` curves, PV(0) and its two parts, the par spread, the dealt spread, mark0;
   - the preview dict; booking with the conversion through `fx.book_linked_spot` (K4);
   - the START PvP; `leg_specs` / `xccy_leg_spec` for the float legs; PAY with the index true-up and `settle_linked`; END;
   - the mark: the full value for the spot date, `csa_exposure_c`, the clean mark; `dv01_c` (the USD rate01);
   - `risk_sensitivities` (off-ledger only), `scheduled_flows`;
   - `next_events` ("indicative" until the index is published); `contract_detail` (legs with fixings, true-ups, linked deals, the
     confirmation with settlement instructions);
   - termination (D17, both variants);
   - the trade date = `fx_trade_date`.
   - Fixed / fixed keeps its behaviour, except D8, D11, D17 and G16 / G17.

**Money tests:**
- C-T1: parse and dates. 8 periods with the dates of 3.3; start 2026-11-02; termination 2028-11-02; maturity 2028-11-06.
- C-T2: the deal.
  - N_USD 2,231,600.00; A* 1.9646274388.
  - PV(0) -403.98: final-exchange timing -370.75, coupon delays -33.23.
  - Par -0.9214 bp; dealt +0.1 bp; mark0 -447.82.
  - Basis01 +437.98; rate01 USD -2.25 and EUR +2.32; IM 44,632.00.
  - Period-1 indicative EUR 12,945.83 and USD 23,571.46.
- C-T3: the booking with the conversion.
  - One command: the contract, `Dr 1330 44,632.00`, the conversion SELL EUR 2,000,000.00 / BUY USD 2,231,560.00 at 1.11578 value
    2026-11-02, its rows tagged, spread cost 40.00.
  - No exchange; a duplicate `client_ref` answers 409 and writes nothing.
- C-T4: START PvP in one command, with the lines of 4.2.
  - Sweep 40.00.
  - End balances: 1010 USD 0, 1010 EUR 0, 1360 USD 2,231,600.00, 2360 EUR 2,000,000.00, 1250 and 2350 at 0.
  - SETTLE_DUE at 00:30 did not touch the tagged rows; with the START failing, they are still untouched the next day, and XCCY_EVENT_LATE is
    raised at 06:00 ET.
- C-T5: accrual night 1. USD 244.86; EUR 135.61. The IRS COMPOUND_ARREARS tests are unchanged.
- C-T6: PAY 2027-02-04.
  - Fixture indices are built from a flat 2.440 % ESTR on TARGET2 days and a flat 3.95 % SOFR on New York days, with weekend weights.
  - Exact coupons with b = +0.1 bp, the true-up (the estimate's calendar-night compounding against the business-day index; pinned to the
    cent with the arithmetic), both payments, the 1225 / 2340 remainders.
- C-T7: VM on the CSA exposure.
  - After S at X0: exposure = full value -259.44, overlay -259.44.
  - With a EURUSD close of 1.1200: exposure -8,657.97 (the notional net -8,400.00 plus the clean mark -257.97); the VM posts the change
    from the previous exposure; the overlay is the clean mark.
  - With fixture balances in 1225 / 2340, the exposure includes them at X_t.
  - The owner's IRS-style VM is unchanged.
- C-T8: END on 2028-11-06. The last coupons and the reverse exchange, with an SFI deal settled PvP; IM released; MATURED.
- C-T9: RFQ.
  - Request, QUOTED, accept, booked with the same terms.
  - DUPLICATE_CLIENT_REF at the request while a contract or a REQUESTED / QUOTED RFQ carries the ref, and at the booking.
  - A new request after an EXPIRED or DECLINED one is accepted.
- C-T10: a schedule edit of the EUR/USD basis level to -12.5 bp writes SETTINGS_CHANGED.
  - A new quote's par spread moves by about -12.5 bp.
  - The open float CCS's mark moves by the re-priced amount (about -12.5 x 437.98).
- C-T11: fixed / fixed. VM on the CSA exposure, and termination in USD (D17); the existing tests are updated, with the reason; everything
  else is unchanged.
- C-T12: INDEX_PENDING. With the index of e_k missing, the PAY skips quietly and pays once it arrives. The EUR fallback from daily prints
  gives the same coupon.
- C-T13: VM keys.
  - A new CCS gets VM from T.
  - Contracts shaped like SWP-000004..6 (NULL `trade_date`; one with no VM row) keep their keys from `start_date`.
  - `vm_keys` never raises; the other contracts' keys are unchanged.
- C-T14: termination.
  - Before S with the conversion: 409 XCCY_NOT_STARTED, nothing written.
  - After S at the example state, with no EUR in the account: the Fund pays USD 696.34, no EUR moves, 1360 / 2360 cleared through 3900,
    IM 44,632.00 released, VM unwound.
- C-T15: `client_ref` written by `h_book` for an FX_FORWARD and for an XCCY; the duplicate check for both kinds.

### P-D: funding plumbing, SFI, the booking program and checker, the forward-check harness, blotter labels (7.5-9 h)

**Owns:**
- `fx.py`, NEW `ccyfunding.py`, NEW `otcplan.py`, `app.py` (the `otcplan` subcommand only), `server.py` (OPTIONAL_MODULES +=
  `"ccyfunding": "ccyfunding"` only) and `blotters.py`;
- NEW `docs\plans\OWNER_FX_TRADES_2026-10-28.plan.json`;
- NEW `tests\terminal_fx_forward_check.py`;
- NEW tests `test_terminal_fx_linked.py`, `test_terminal_ccyfunding.py` and `test_terminal_otcplan.py`, the last with its own small
  stateful stdlib fake server (FX-C-17); `test_terminal_fx.py` and `test_terminal_blotters.py` for intended changes.
- `tests\terminal_fixture_server.py` is not touched.

**Tier 1:**
- `fx.book_linked_spot`, `settle_linked`, the `due_deals` rule and `scheduled_otc_flows` (until K5 / K6 land it reads the XCCY START / END
  exchanges and the FX_FORWARD legs straight from `terms_json`);
- `ccyfunding` importable, with the job registered and quiet and a `plan()` stub;
- `python -m finsim otcplan --help`; the `otcplan.reprice` signatures.

**Tasks (hours).**
1. `fx.py` (K4, D7, D10, G6, G10) (1.5-2 h):
   - `spot_date` and `tenor_date` through `ccycal`;
   - scheduled OTC flows in `funding_check`;
   - the linked spot and the linked settlement; `settle_deal(legs=...)`; the `due_deals` rule.
2. `ccyfunding.py` (4.3, K8) (1.5 h): the job, `plan()` and the command.
3. `otcplan.py` with `app.py` (section 10) (3-3.5 h):
   - `run`, `snapshot`, `check` and `reprice`, plus the report JSON and text;
   - an HTTP client on stdlib `urllib`, honouring the codec: cents fields arrive without `_c` as decimal strings, `*_ts` as epochs with
     `*_ny`;
   - the checker reads the database only through a sqlite3 URI with `mode=ro`.
4. `tests\terminal_fx_forward_check.py` (9 item 5) (0.75-1 h):
   - it builds two runtimes with a FakeClock (the code of `build_runtime`, as Build 2's proofs did) on two copies: with the trades and
     without them;
   - offline providers serve each copy's last recorded marks, curves and rates, held flat and labelled TEST;
   - it ticks the lifecycle through every scheduled job time from the booking to S + 1 08:00 ET;
   - it prints the new contracts' events, and diffs every pre-existing contract's job results and rows between the copies.
5. `blotters.py` (0.25 h):
   - the source label gains ", booked by the operator at the owner's instruction (ref OWNER-FX-2026-10-28-A)" when a `client_ref` is set;
   - the conversion and SFI deal rows name the contract and obligation they serve (from `inputs.link`).
6. The plan file (10.1).

**Money tests:**
- D-T1: selling EUR 2,000,000.00 value 2026-11-02 with a CCS start receipt of EUR 2,000,000.00 due that day is fundable; without it,
  SETTLEMENT_UNFUNDABLE.
- D-T2: `due_deals` skips tagged rows while the contract is OPEN and its event has not run, on the due date and after it (no next-day
  fallback); after the contract closes, they are returned.
- D-T3: `settle_linked` receive and pay phases.
- D-T4: SFI.
  - Coupon known on e_1: one deal on e_1 BUY EUR (coupon - EUR held) value p_1, settled PvP in PAY.
  - Forward: a deal on V - 2 BD for EUR 2,000,000.00 - EUR held.
  - Switch off: no deal.
  - No quote: SKIPPED, then a WARN at 16:30.
  - USD short on the value date: the deal waits SETTLEMENT_UNFUNDED with an ERROR, and nothing else moves cash.
  - `plan()` shows the 2028 gap of D9 on the fixture balances.
- D-T5: `otcplan run` against the test's stateful fake server.
  - Both trades are booked through the RFQ path; a re-run books nothing (the client_ref is found and no requests are sent).
  - A run with the GBP curve missing books A only and reports B UNEXECUTED with its exact reason.
  - A refused attempt reuses its request id. A QUOTE_MOVED is declined, then attempt n+1 follows.
  - A transport error after a committed accept: the re-POST replays the result, and the trade is EXECUTED.
  - Attempts are counted per run: a second day's run retries after a first day of three expiries.
- D-T6: `otcplan check` passes on the fixture and fails each of at least 13 tamper cases with the evidence:
  - a pre-existing contract row edited by a command that is not its lifecycle; an old entry altered;
  - negative cash in a scoped account; K changed by 0.000001; a curve node of the record changed;
  - the conversion missing; a second contract with the same ref;
  - a missing label; a missing source time; an unbalanced trial balance;
  - an IM that differs from the schedule; a PV delta off by 0.02; a run command that edits the GS schedule.
  - It PASSES, listing them as other activity, when an owner order fills and a UI command runs between the snapshot and the check.
- D-T7: Completed Trades shows 2 contracts and the conversion with their labels; Pending shows an expired RFQ.
- D-T8: `otcplan.reprice` reproduces the numbers of 3.2 and 3.3 to the cent and to the 1e-10 / 6 dp quantisations. The test fails if
  `otcplan` imports `fxcurves`, `ccycal`, `fx` or a product module.
- D-T9: the forward-check harness, on the fixture world, runs to S + 1 and its control diff is empty.

## 9. Acceptance (package V, after integration)

> ORCHESTRATOR NOTE FOR V (2026-10-29 02:01 ET): the pool finished at 02:00 ET, so V runs before the day's curves exist: the new curves job fetches the Bank of England OIS curve only after 07:00 ET and the ECB AAA curve only after 08:00 ET (P-A). Do every item that needs no live curve first and in full (1 suites, 2 the 1.0.6 comparison, 6 the real-data self-check, 7 the money-test review, 8 clean-up, and every part of 3 / 4 / 5 that does not need a priced EUR or GBP curve). Never use fixture curves or any fallback for items 3 / 4 / 5 and never sleep to wait for the slots: if a copy cannot price for lack of the day's curves, report those items in acceptance as passed=false with 'BLOCKED_BY_DATA_TIME: <what is missing>' and do NOT list that as a defect. The orchestrator runs items 3 / 4 / 5 with real curves after 08:00 ET, before the gate.

1. **Suites.** `tests\run_terminal_suite.py` on py-3.12 and py-3.14: 0 failures, 0 errors, 0 ResourceWarnings.
2. **1.0.6 against 1.0.6.1, before any booking.** On a fresh post-import copy of the owner's database (sqlite3 backup, source `mode=ro`, a
   new folder under `scratchpad\checks\fxccs-*`), the method of 15.9 item 7.
   - These must be equal: his positions (quantities and cost), the restated NAV versions, the ledger balances, and every `otc_contracts`,
     `otc_vm` and `otc_resets` row.
   - Allowed differences: display-only blocks (the risk FX block), listed. None in the ledger.
3. **Browser walk** (Playwright py-3.14 headless, 1440 and 1100 px) on that copy, served with REAL delayed data (not `--offline`):
   - Forwards: an EURGBP 2Y SELL 2,000,000 preview, showing the spot (derived, print, difference), the curves with their dates and kinds,
     the labels, the cross-checks, delivery, SFI, limits and funding.
   - Swaps: an XCCY float EUR/USD with the conversion, showing the legs, the spread lines (level ASSUMED, par, half-width, dealt), the
     schedule, the exchanges, the conversion and the net start flows.
   - RFQ request, quote and accept for each trade, on the copy.
   - The contract drawers and timelines.
   - Completed Trades: the 2 contracts and the conversion.
   - Pending: one RFQ left to expire after more than 120 s.
   - The Risk FX block.
   - The GS schedule editor: edit the EUR/USD basis level to -5.0, then back.
   - 0 console errors; no "undefined", "NaN" or "[object Object]".
4. **Dry run** of the booking program on another fresh copy (section 10.4).
   - The checker PASSes, the second run books nothing (idempotency), and restart persistence holds.
5. **Forward check, mandatory (FX-C-12).** `tests\terminal_fx_forward_check.py` on the dry run's copy and its control copy (the pre-run
   backup), run to S + 1 08:00 ET with the copies' last recorded inputs held flat and labelled TEST.
   - It must show:
     - the START PvP (net USD -40.00 class) and the conversion SETTLED;
     - both legs' first accruals;
     - the first VM on the CSA exposure;
     - SETTLE_DUE leaving the tagged rows alone until START.
   - On the owner's book:
     - SWP-000004..6 keep their VM keys and jobs (NULL trade dates; SWP-000006 with no VM row);
     - the CDX, IRS and TRS jobs run exactly as on the control;
     - the diff of the pre-existing contracts' job results and rows is empty.
   - There is no fixture-test fallback. If the harness cannot drive the copy, the gate waits; the check is a precondition of the live
     booking (10.5).
   - Budget: 0.75-1 h inside V's hours.
6. **Real-data self-check.** `tests\terminal_rfr_selfcheck.py` (P-A task 5).
7. **Money-test review.** Every money test is hand-worked in its comments. `otcplan.reprice` reproduces the numbers of sections 3-6, and so
   do the designer's scripts.
8. **Clean-up.** Delete every database copy at the end (disk: about 7 GB free); keep the reports.

## 10. The booking program, the dry run and the live run

### 10.1 The plan file (`docs\plans\OWNER_FX_TRADES_2026-10-28.plan.json`, committed)

```json
{
  "plan_id": "OWNER-FX-2026-10-28",
  "format": 2,
  "instruction": "finsim/docs/OWNER_FX_TRADES_2026-10-28.md",
  "account_id": "F1:PB_GS",
  "window_et": {"from": "08:00", "to": "16:30", "fx_business_day": true},
  "max_attempts_per_run": 3, "retry_wait_s": 45, "quote_wait_s": 150, "accept_replays": 2,
  "trades": [
    {"key": "A", "client_ref": "OWNER-FX-2026-10-28-A", "kind": "XCCY",
     "terms": {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional": "2000000.00", "pay_index": "ESTR", "recv_index": "SOFR",
               "months": 24, "mtm_reset": false, "start_conversion": true, "settlement_funding": {"EUR": "BUY_WITH_USD"},
               "client_ref": "OWNER-FX-2026-10-28-A"},
     "expect": {"variant": "FLOAT_FLOAT", "pay_notional_c": 200000000, "labels": ["MODEL", "PROXY", "ASSUMED"]}},
    {"key": "B", "client_ref": "OWNER-FX-2026-10-28-B", "kind": "FX_FORWARD",
     "terms": {"pair": "EURGBP", "side": "SELL", "notional": "2000000.00", "tenor": "2Y",
               "settlement_funding": {"EUR": "BUY_WITH_USD"}, "client_ref": "OWNER-FX-2026-10-28-B"},
     "expect": {"pair": "EURGBP", "side": "SELL", "notional_c": 200000000, "labels": ["MODEL", "observed", "PROXY", "ASSUMED"]}}
  ],
  "untouched": {"contracts": ["CDX-000001", "CDX-000002", "SWP-000001", "SWP-000002", "SWP-000003", "SWP-000004", "SWP-000005",
                              "SWP-000006"]}
}
```

**Request ids.**
- The form is `OWNER-FX-2026-10-28:<run>:<key>:<n>:rfq`, `...:acc` and `...:dec`, where run = the ET start time of the run, for example
  `20261030T081205`.
- They are at most 80 characters (44 here) and match `^[A-Za-z0-9:_.-]{8,80}$`.
- Attempts are counted within the run (D13).

### 10.2 `python -m finsim otcplan run <plan> --base-url <url> --report <dir> [--window any]`

1. **Load and validate the plan;** record its sha256 and the run id.
2. **Check the server** with GET `/api/health`:
   - version 1.0.6.1 or later;
   - the modules swaps, swaps_trs, otc_options, fx and ccyfunding bound;
   - no integrity hold; the lifecycle running;
   - the health rows `boe`, `ecb` and `nyfed` ok.
   If any check fails: exit 2, nothing sent.
3. **Check the window** against the server's ET time. Outside it: exit 3, nothing sent. `--window any` is for dry runs and the report says
   it was used.
4. **Find earlier bookings.** GET `/api/otc/contracts?account_id=F1:PB_GS` across all statuses. A contract carrying a trade's `client_ref`
   makes that trade ALREADY_BOOKED: it is reported with its id and nothing is sent for it.
5. **For each remaining trade**, run attempts n = 1 .. `max_attempts_per_run`:
   1. **Preview.** POST `/api/otc/preview`. A blocker that is not retryable makes the trade UNEXECUTED, with the reason.
   2. **Request.** POST `/api/otc/rfqs` with the `rfq` id.
      - Retryable 409 (QUOTE_STALE, QUOTE_UNAVAILABLE, SESSION_CLOSED, NO_QUOTE, SPOT_INCONSISTENT, RATE_UNAVAILABLE, CURVE_UNAVAILABLE,
        WRITER_BUSY): wait `retry_wait_s` and go to the next attempt.
      - DUPLICATE_CLIENT_REF: look the ref up.
        - A contract: ALREADY_BOOKED.
        - An open RFQ from an earlier run: decline it with this run's `dec` id, then go to the next attempt.
      - Any other code: UNEXECUTED.
   3. **Wait for the quote.** Poll GET `/api/otc/rfqs/{id}` every 5 s for up to `quote_wait_s`.
      - QUOTED: validate the quote (below). It passes: accept with the `acc` id. It fails: decline with the `dec` id. A failure of the
        market data's timing only (a print too old or not executable when quoted, the legs apart, the cross print beyond 5.0 pips) goes to the
        next attempt after `retry_wait_s`, as a QUOTE_STALE request does; any other failure is UNEXECUTED, naming the check that failed.
      - EXPIRED (the dealer did not quote): next attempt.
      - Still REQUESTED: decline, then next attempt.
   4. **Accept.**
      - 200: EXECUTED. Record the contract id, the conversion deal id, the entries and the booking time.
      - 409 QUOTE_MOVED or QUOTE_EXPIRED: decline the RFQ if it is still QUOTED, then next attempt.
      - A transport error, a timeout or a 5xx:
        - re-POST the same `acc` id with the same payload, up to `accept_replays` times; a committed command replays its result;
        - then look the `client_ref` up: a contract means EXECUTED (reported as "booked; the first response was lost"); none means the
          next attempt.
      - Any other 4xx: UNEXECUTED.
6. **Report** (10.7). Gather the contracts, GET `/api/risk` (the FX block for F1:PB_GS and for the book), the GS schedule, and the cash
   and collateral views.
7. **Exit.** 0 when every trade is EXECUTED or ALREADY_BOOKED; 1 when any trade is UNEXECUTED (the report says why).

**Quote validation, before any accept (FX-C-05).** The checks:
- The expected labels are present (`expect.labels`).
- Every input of the pricing record has its source and time. Each overnight print is at most 10 days old and each curve at most 3 BD.
- The spot legs are within 120 s of each other. Each FX print the quote is priced on (the forward's spot legs, the swap's deal FX) is at most
  300 s older than the quote and was executable when quoted (quality delayed / live).
  - Why 300 s (1.0.6.1 fix round 1, V1061-01, 2026-10-29): revision 2 said "the quote within 120 s of the spot".
    - The only FX source, Yahoo's EURUSD=X / GBPUSD=X, stamps its prints on a 60 s grid and serves them 168-228 s after their own time (24
      fetches 04:46-04:52 ET: 167.7-200.8 s; acceptance V: 159-220 s). So the 120 s rule declined every real quote and both trades ended
      UNEXECUTED.
    - 300 s is that lag plus the terminal's 15 s ORDER-priority fetch and the dealer job. It is also the terminal's own fetch and staleness
      grace.
    - The real-data dry run of 05:13 ET booked both trades at print ages of 213 s and 183 s, and the checker passed C1-C11.
- The cross difference is at most 5.0 pips.
- **Independent re-derivation** with `otcplan.reprice` (K9), with exact equality at the stated quantisation:
  - B: DF*_EUR and DF*_GBP (1e-10); F, points, hw_px and K (6 dp); K = F - hw_px; the GBP amount, the USD notional and the IM (cents).
  - A: X0 equals the RFQ's pinned EURUSD print, in value and time; N_USD = to_c(N_EUR x X0).
  - A: A*, PV(0) and its two parts, s_par, dealt = the 0.1 bp ceiling of s_par + hw x mult, and mark0 (cents).
  - A: the conversion rate X0 - 0.2 pip and its USD amount; the IM.
- The variant, tenor, side and notional equal the plan's `expect`.
- The `limits` and `funding` blocks are present.

A quote that fails a check is never accepted. Revision 1's "K within 50 pips of F" is gone: it would have passed a 29-half-width error.

### 10.3 `otcplan snapshot` and `otcplan check` (read-only, sqlite3 URI `mode=ro`; FX-C-04)

`snapshot --db <terminal.db> --out before.json` records:
- the maximum ids of `commands`, `journal_entries` and `events`;
- every row of `otc_contracts`, `otc_vm`, `otc_resets`, `fx_deals` and the F1 accounts' `settlements`;
- the GS schedule fields, the state_version and the time.

`check --db <terminal.db> --before before.json --run <report.json> [--second <report2.json>] [--after-restart <views.json>]` then checks:

| Item | Check |
|---|---|
| C1 Untouched, by attribution | Every row written or changed after the snapshot is traced to its command. The paths: `journal_entries.command_id`, `events.command_id`, `fx_deals.command_id`, `otc_contracts.command_id`, `otc_vm` / `otc_resets` through `entry_id`, fills and lots through their order's command, settlements through their fill or deal. Each command falls in one class: (1) the run's, by the request-id prefix `OWNER-FX-2026-10-28:<run>`; (2) the run's lifecycle: actor `lifecycle`, a job key that names a new contract or deal of the run; (3) a pre-existing contract's lifecycle: a job key that names CDX-000001 .. SWP-000006; (4) everything else. PASS when: classes 1 and 2 wrote only the new contracts, their deals and the expected IM, settlement, RFQ and VM rows; class 3 changed pre-existing contract rows only in the columns its job owns (`mark_c`, `mark_ts`, `vm_balance_c`, `inputs_json`, its own `otc_vm` / `otc_resets` rows and their entries); class 4 changed no pre-existing contract row and no row of the run. Class 4 is listed as other activity (the owner's fills, UI commands, other books), never a failure on its own. Journal entries up to the snapshot's maximum id are byte-equal. |
| C2 Exactly the new rows | For each EXECUTED trade: one contract carrying its client_ref. For A, also: the linked conversion (SPOT, `link xccy_start` to the contract), its 2 tagged settlement rows and its FX entry. The IM entries, the instrument rows and the RFQ rows (ACCEPTED, plus any EXPIRED or DECLINED attempts). Nothing else from classes 1 and 2. |
| C3 Terms as quoted, re-derived | The contract terms equal the accepted quote's compare: A `deal_rate`, `pay_spread_bp`, `recv_spread_bp`; B strike. S, V and the schedule are re-derived from T with `ccycal`. Every price is re-derived from the record with `otcplan.reprice`, as in 10.2. |
| C4 Pricing record and its inputs | Complete: every input has its source and time, and the labels are present. Each input equals the stored row of its as-of date: the curve nodes (`curve_points`), the overnight prints (the rate series), the spot legs and the print (the RFQ's pinned prints, value and time), and the GS schedule values in force at the quote (the schedule and its SETTINGS_CHANGED history). |
| C5 Cash, scoped | F1:PB_GS and F1:TREASURY in USD, EUR and GBP: settled 1010 at or above 0, and no FUNDING_SHORTFALL attention for those accounts and currencies raised after the snapshot. Other accounts' attentions are listed. |
| C6 Ledger | Integrity full ok; the trial balance balances in base and per currency; 1330 per contract = `im_c`; the L22 VM rule holds. |
| C7 Exposure | EUR -4,000,000.00 nominal (A and B); GBP +to_c(2,000,000 x K). The per-currency PV delta of the Risk block equals the ledger balances plus `otcplan.reprice`'s off-ledger deltas, to within 0.01. The report's sensitivities are recomputed to within 0.01. When the run booked no FX contract (every trade UNEXECUTED), C7 passes as not applicable and says so: `by_currency` is then legitimately empty. |
| C8 Idempotency (`--second`) | The second report shows ALREADY_BOOKED for both trades. No command carries the second run's prefix, and no new row is attributed to the plan. |
| C9 Restart (`--after-restart`) | Contract views and positions served after a stop and start equal those served before. |
| C10 Words | Nothing says "dealer-confirmed"; the source labels name the owner's instruction reference. |
| C11 Schedule edits | SETTINGS_CHANGED events after the snapshot are listed, each with its re-mark of each new contract (`otcplan.reprice` with the old and the new value). A run command that edits the schedule FAILs. |

The output is PASS or FAIL per item, with evidence. Exit 0 only when every item passes.

`check`, `run`, `snapshot` and `reprice` answer a missing or unreadable input file, a file that is not JSON, or a database that cannot be opened read-only with one line and exit 2.

### 10.4 The dry run on a fresh copy (V, then again by the orchestrator in the booking window on the day of the live run)

1. Take two sqlite3 backups of `%USERPROFILE%\.finsim\terminal.db` (source opened `mode=ro`) into a new folder,
   `C:\Users\logan\finsim-orchestrator\fxccs\dryrun-<ts>\`: C0 (the control) and C1 (the dry run). Each is about 30 MB and stays outside
   %TEMP%.
2. Start `py -3.12 -m finsim serve --home <folder of C1> --port 8975 --no-open` with real data. Wait until EURUSD, EURGBP and GBPUSD are fresh
   and the overnight series and the BoE and ECB curves are loaded.
3. Run `otcplan snapshot` on C1.
4. Run `otcplan run docs\plans\OWNER_FX_TRADES_2026-10-28.plan.json --base-url http://127.0.0.1:8975 --report <folder>\run1`.
5. Run `otcplan check`. Every item must PASS.
6. Run the program again (`run2`), then `check --second`. Nothing new may be booked.
7. POST `/api/shutdown`, start the server again, save the contract views, then `check --after-restart`.
8. Stop the server and confirm in tasklist that its pid is gone.
9. Run the forward check (9 item 5) on C1 with C0 as the control. It must pass.
10. Keep the reports. Delete both copies.

V's acceptance dry run may fall outside 08:00-16:30 ET.
- It then runs with `--window any`, which its report records.
- It still needs the FX session open (Sunday 17:00 to Friday 17:00 ET) for executable quotes. Outside the session it waits; it never books
  on stale prints.
- The orchestrator's same-day dry run always runs inside the window.

### 10.5 The live run (orchestrator only; after the 1.0.6.1 deploy)

It runs under the owner's standing pre-approval and his explicit instruction of 10-28 ("This is an explicit instruction to auto-book both
contracts once the simulator can produce valid dealer quotes, cash flows, and margin calculations").

1. **Preconditions:**
   - the same day's dry run (10.4) passed, its forward check included;
   - 08:00-16:30 ET on an FX business day, never 17:00-18:00 ET;
   - fresh FX prints; the BoE and ECB curves within 3 BD; the overnight prints within 10 days;
   - integrity ok;
   - the GS schedule's `xccy_basis_eur_bp`, `xccy_basis_gbp_bp`, `xccy_basis_hw_bp`, `eur_ois_spread_bp`, `forward_hw_bp`, `xccy_im_pct`
     and `forward_im_pct` recorded, and still at the values the owner left them (Q1, Q2).
2. **Back up the live database.** sqlite3 backup API (source `mode=ro`) to
   `%USERPROFILE%\.finsim\backups\fx\terminal-pre-OWNER-FX-2026-10-28-<ts>.db`. Write its sha256 and re-read it.
3. Run `otcplan snapshot` on the live database (`mode=ro`).
4. Run `otcplan run ... --base-url http://127.0.0.1:8765 --report C:\Users\logan\finsim-orchestrator\fxccs\live-<ts>`.
5. Run `otcplan check` on the live database. Every item must PASS; other activity during the run is listed, not failed (C1).
6. Report to the owner (10.7) and add the BUILD_LOG line.
7. Schedule the START watch (10.8).

### 10.6 Restore and corrections

- A defect found after the live booking may be undone by restoring the pre-booking backup (FINSIM1_PLAN section 7's procedure) only while
  no owner command (actor `ui`) and no fill of his orders has run since the run's last command.
- After that, a correction goes forward with his decision: terminate and rebook, or a correction command as in 15.9 item 8. Nothing is
  ever edited in place.

### 10.7 The owner's report (each item of his section 5, and the checks he asked for)

| His item | Where it comes from |
|---|---|
| Contract ids and executed statuses | the run report per trade: EXECUTED / ALREADY_BOOKED / UNEXECUTED with the reason; the contract ids, the conversion deal id and the RFQ ids |
| Simulated booking timestamps | the accept command's `ts` (ET) and the trade date (D11) |
| Executed EUR/USD deal spot and CCS notionals | the A pricing record: X0, its source and time, EUR 2,000,000.00 / USD N_USD; the conversion rate and USD amount |
| CCS pay and receive rates, basis, principal exchanges | pay ESTR + dealt bp / receive SOFR flat; the current prints; the basis level (ASSUMED), the par spread with its two parts, the half-width, the dealt spread; both exchanges with dates and the settlement instructions (below) |
| EUR/GBP forward rate and GBP receivable | the spot (derived, print, difference), the curves with their dates and kinds, F, points, K, GBP to_c(2,000,000 x K), V |
| Initial margin and required cash transfers | IM per contract; the Treasury sweeps (178,528.00 at booking, the start-date net); the VM rule; the SFI purchases planned |
| Current mark-to-market and the daily valuation method | the marks now with their inputs; the methods of section 3 (values for the spot date, D19); VM at 17:05 ET under CTM on the CSA exposure |
| Interest and settlement schedules | the CCS schedule (s, e, p, indicative coupons); the forward's value date; the SFI dates |
| Available Treasury cash and Goldman collateral after booking | Treasury per currency; GS IM, VM posted and received |
| Reconciled fund NAV | NAV before and after, bridged: day-one costs (-907.07 in the example); the conversion's `fx_result`; one named line per schedule-term re-mark (D5); other P&L over the interval; residual 0 |
| Completed Trades confirmation | the execution rows (2 contracts + the conversion) and each contract's confirmation block |
| Failed checks and unexecuted trades | the checker's items and any UNEXECUTED reason, verbatim |
| Validations he listed (his item 4) | currency availability and settlement arrangements (the SFI plan); "notional limits: none configured" with gross / NAV before and after (D18); the IM; the funding check; the 2028 settlement-funding gap as a number (D9) |
| Sensitivities (his section 4) | the section-6 rows from the Risk FX block; the assumptions with their value per bp (basis levels, EUR OIS spread) |
| What 15.15 did not fix | the deferred audit items of 1.5, each with its reason |

**Settlement instructions** (printed on the confirmations; simulator accounts). Goldman Sachs PB is the counterparty; the Fund's cash
accounts are its GS prime-brokerage cash in each currency (F1:PB_GS).
- **A, initial exchange, value S.**
  - GS pays EUR 2,000,000.00; the Fund pays USD N_USD.
  - Settled PvP with the inception conversion: the Fund delivers EUR 2,000,000.00 to the FX desk for USD at X0 - 0.2 pip.
  - The net USD (-40.00 in the example) comes from the account, else Treasury.
- **A, coupons, on each p_k.**
  - The Fund pays EUR on ESTR compounded + the dealt spread; GS pays USD on SOFR compounded; ACT/360.
  - The EUR is bought under the SFI on e_k, value p_k.
- **A, final exchange, value p_n**, together with the last coupons: the Fund pays EUR 2,000,000.00; GS pays USD N_USD.
- **A, termination:** cash-settled in USD at the full value with the half-width against the Fund; refused before S while the conversion is
  linked (D17).
- **B, value V.**
  - The Fund delivers EUR 2,000,000.00 (bought under the SFI two BD before); GS delivers GBP to_c(2,000,000 x K) into the Fund's GBP cash.
- **Both:** VM daily in USD (CTM) on the CSA exposure; IM in USD held by GS.

### 10.8 The START watch (orchestrator; FX-C-12)

**When.** On S from 00:45 to 01:30 ET: Tuesday 2026-11-03 for a booking on 10-30. US daylight time ends on 11-01, so this is 05:45-06:30
UTC.

**00:50 ET:** read the contract, its timeline and the START job row.
- DONE: verify 1360 USD 2,231,600.00, 2360 EUR 2,000,000.00, 1250 and 2350 at 0, the conversion SETTLED and the 40.00 sweep. Log it; the
  watch ends.
- SKIPPED with a retryable reason (WRITER_BUSY, a lock): wait for the retry and look again every 15 minutes until 06:00 ET.
- SKIPPED with an ERROR (a code or data defect): record the reason.
  - Nothing is overdrawn, because the linked rows wait (D7).
  - Post nothing by hand, and do not restart the server in a loop.

**06:00 ET:** XCCY_EVENT_LATE appears if START is not done.
- Before 08:00 ET the orchestrator reports to the owner with the reason and his choices:
  - (a) a fix-forward release (1.0.6.1.x through the gate), after which START runs and catches up;
  - (b) the 10.6 restore, if no owner command has run since the booking;
  - (c) a correction under 15.9 item 8 that cancels the conversion and the CCS together with reversing entries, on his decision.
- Nothing settles the conversion alone. Until START is done, the conversion's EUR leg and the CCS's EUR receipt stay unsettled together,
  and the Fund's EUR stays at 0.

**Later events.** The first PAY (2027-02-04) and the END are covered by XCCY_EVENT_LATE and the orchestrator's daily attention sweep.

## 11. Order of work and hours (wall clock)

| Step | Hours |
|---|---|
| 0. Precondition: the 1.0.6 gate has committed and verified its archive; the orchestrator amends 15.15b (1.5) and maps the 15.15 walk's open defects (1.1) | not counted |
| 1. Pool of 4: P-A 6.75-8, P-B 7-8.5, P-C 7.5-8.5, P-D 7.5-9; tier-1 hooks in the first 45-60 min | 8.5-9.5 |
| 2. Integration and remainders (B-T12); both suites green | 1-1.5 |
| 3. Acceptance V (section 9), the mandatory forward check included | 3-4 |
| 4. Fixes, at most 2 rounds, each re-verified by V items 1, 3, 4 and 5; 0.5 h reserve for the mapped 15.15 defects | 1.5-3 |
| 5. Gate 1.0.6.1 (FINSIM1_PLAN section 12: VERSION, CHANGELOG, commit, archive verify, release, deploy) | 1.5-2 |
| 6. The same-day dry run and forward check in the booking window (10.4) | 1-1.25 |
| 7. The live booking, the checker and the owner's report (10.5) | 0.5-1 |
| Total | 17-22.25, plus waiting for the window and the 0.5 h START watch on S |

Revision 1 counted 15-20.25 h. The 2 h more pay for the curve data, the par-spread pricer, the CCS termination, the independent re-pricer
and attribution checker, and the mandatory forward check.

**Timeline.**
- If the 1.0.6 gate finishes overnight on 10-28 / 10-29, the pool starts about 02:00 EDT on 10-29.
- The gate of 1.0.6.1 then falls about 17:00-23:30 EDT on 10-29.
- The booking falls on Friday 10-30 at 08:00-12:00 ET: trade date 10-30, start 2026-11-03, value date 2028-11-03, and the START watch on
  Tuesday 11-03 at 00:45 ET.
- If the gate slips past 10-30 16:30 ET, the booking moves to Monday 11-02: start 2026-11-04, value date 2028-11-06, last CCS payment
  2028-11-08.

**Machine.** 7.6 GB RAM: at most 4 builders, one server and one browser per agent at a time. Disk: about 7 GB free; each database copy is
about 30 MB and is deleted after use.

## 12. Blocking risks and what each trade does then

| # | Risk | What happens |
|---|---|---|
| R1 | The Bank of England stops serving GOV_UA, or its curve is older than 3 BD | B is refused CURVE_UNAVAILABLE; 3 attempts, then UNEXECUTED: "EURGBP 24M: the Bank of England OIS curve is unavailable (<HTTP status or last date>, <time>); the forward is not priced from any other curve". No other agent is tried. A is unaffected. |
| R2 | No free cross-currency basis | A and B use the disclosed GS levels, 0.0 bp ASSUMED (Q2). If the owner says block: A UNEXECUTED, "no free EUR/USD cross-currency basis source; the dealer's basis level is not set". |
| R3 | ESTR, SONIA or SOFR stale, or the ECB curve stale (an ECB or NY Fed outage, FRED maintenance, the ECB's 20-30 s responses) | The quote is refused RATE_UNAVAILABLE or CURVE_UNAVAILABLE with the series, its last date and the sources tried; 3 attempts, then UNEXECUTED with that text. Payments wait INDEX_PENDING. The 60 s timeout and 2 attempts make a slow ECB answer a delay, not a failure. |
| R4 | FX prints stale or inconsistent, or the session closed (17:00-18:00 ET, weekends) | Retried. The window avoids it. After 3 attempts: UNEXECUTED, "EURGBP: the cross print differs from the USD pairs by X pips (limit 5.0); no quote". |
| R5 | The 1.0.6 gate slips, or the 1.0.6 fixers rewrite the same files | The pool starts later; builders re-read their files; the 15.15 walk's defects are mapped (1.1). |
| R6 | The ECB or NY Fed index is dated differently from 3.1, or a curve's compounding differs | P-A's self-check (task 5) catches it before the gate. Unfixed, every coupon would be off by one day's interest (about EUR 136 / USD 245); a wrong compounding would move the forward about 19 pips. |
| R7 | One-off UK holidays or SIFMA closes | Hand-maintained lists (disclosed). A missed one gives a value date on a holiday or an INDEX_PENDING. |
| R8 | Funding | IM 178,528.00 comes from Treasury USD 2.10 M; S needs 40.00. The SFI needs USD each quarter, and about USD 2.23-2.31 M on 2028-10-31: short by about 242,261.57 at today's balances (D9). Shown in the ladder from day one; Q7. |
| R9 | Machine limits | At most 4 builders; one server per agent; copies deleted; no full copies kept. |
| R10 | The CDS plan touches the same files | Sequenced: 1.0.6.1 first. |
| R11 | QUOTE_MOVED on accept (a rate series refreshed inside the 120 s) | The RFQ is declined; next attempt. |
| R12 | A booking after 17:00 ET gets the next day's trade date | The window. |
| R13 | The EUR proxy: the gap between EUR OIS and the AAA curve is not observed | A disclosed GS term, 0.0 bp ASSUMED; each 10 bp moves B by about GBP 3,470 (Q1). |
| R14 | The START fails on S | The linked rows wait (D7); the START watch (10.8); nothing is overdrawn. |
| R15 | Institutional V3's JPY forwards use the legacy path, whose JPY rate is a monthly series | The orchestrator checks 1.0.6's age limit before V3 books (1.5). |

## 13. Owner questions (each has a default; none blocks the build or the booking)

**Q1. How should Trade B (EUR/GBP 24M) be priced?**
- The variants, side by side, on the example's inputs (spot 0.837499, the 2.0 bp half-width):

  | Variant | Outright F | K | GBP received | Against the default |
  |---|---|---|---|---|
  | Default: GBP on the BoE OIS curve (observed); EUR on the ECB AAA curve + 0.0 bp (PROXY, ASSUMED) | 0.866797 | 0.866624 | 1,733,248.00 | 0 |
  | The same, with your EUR OIS spread at +10 bp | 0.865063 | 0.864890 | 1,729,780.00 | -3,468.00 |
  | The same, with your EUR OIS spread at -10 bp | 0.868535 | 0.868361 | 1,736,722.00 | +3,474.00 |
  | Block B until a free EUR OIS curve exists | - | - | - | - |
  | Cross-check only, not offered: flat ESTR / BoE OIS | 0.876333 | 0.876158 | 1,752,316.00 | +19,068.00 |
  | Cross-check only, not offered: flat ESTR / flat SONIA (revision 1's method) | 0.858881 | 0.858709 | 1,717,418.00 | -15,830.00 |

- The flat rows are not offered: a 24-month rate held at today's overnight rate has no source, which the instruction forbids.
- **Default: the first row.**

**Q2. The cross-currency basis levels.** EUR/USD and GBP/USD at 0.0 bp ASSUMED (an assumed level, not a neutral one), with a 1.0 bp CCS
half-width.
- Or set levels of your own before the booking. Each 1 bp of the EUR/USD level is worth USD 437.98 on Trade A and 420.66 on Trade B; each
  1 bp of the GBP/USD level is worth -420.57 on Trade B.
- Or block Trade A.
- **Default: 0.0 bp and 0.0 bp, 1.0 bp half-width.**

**Q3. Fund every EUR obligation** (coupons about EUR 12,900-16,400 a quarter, EUR 2,000,000 on each maturity) by buying the EUR two
business days before, as an audited spot deal? **Default: ON for both contracts.**

**Q4. IM.** The GS schedule sets CCS IM at 2 % (USD 44,632) and forward IM at 6 % (USD 133,896) for the same EUR risk: keep, or align the
CCS to 6 %? **Default: keep the schedule.**

**Q5. The CCS's final exchange date.**
- Pay it together with the last coupon (2028-11-06, 2 BD after the termination date), or on the termination date (2028-11-02) with the
  last coupon 2 BD later?
- It is priced either way:
  - together: the par spread is -0.92 bp, because the Fund receives the USD 4 days later at 4.65 % and pays the EUR 4 days later at
    about 3.0 %, which is worth -370.75;
  - on the termination date: the par spread is about -0.08 bp.
- **Default: together (one maturity event).**

**Q6. Book between 08:00 and 16:30 ET on an FX business day,** so that the trade date is the booking date? **Default: yes.**

**Q7. Funding.**
- The 2028-10-31 EUR purchase that settles the forward is short by about USD 242,261.57 at today's balances (318,682.31 at the outright).
- If Treasury USD is short then, or at booking, the start date or any EUR obligation: move cash from US Exchange (USD 697,070.38), or add
  cash?
- **Default: no automatic move.** The ladder shows the gap from day one. On the day, the SFI purchase's USD leg waits
  SETTLEMENT_UNFUNDED with an ERROR, and so does the delivery, until you move cash or close the forward.

**Q8. Notional limits.** None exist in the terminal today: no field in the GS schedule, no OTC limit in compliance.
- Do you want editable per-contract and per-PB OTC notional caps added to the PB schedules in a later build?
- **Default: no caps.** Every report states "notional limits: none configured", with gross / NAV before and after.

## 14. Deferred (with reasons; the same items as 1.5)

- **FX desk forwards** (`fx.forward_rate`, up to 1Y) move to `fxcurves` in 1.0.6.2.
  - In this release they stay simple-interest CIP on the policy series (EUR ECBDFR), 17.1 pips from the PB desk at 1Y EURUSD in the example.
  - The PB ticket shows the difference.
  - Moving them now would re-price every FX desk forward test in seven test files, inside a release that must book the owner's trades.
- **The FX desk's cross spot** keeps the print. The 10-28 measurement (1.3) argues for the derived cross; it moves with the FX desk forwards
  after a multi-day measurement.
- **GBP SONIA float legs.** The data is verified and stored (IUDZOS2), and the leg code is currency-generic. They are held back only to keep
  this release on the booking path; they are the next currency (1.0.6.2).
- **JPY, CHF, CAD and AUD.** Float legs, term curves and calendars wait for daily overnight-rate sources: the BoJ (TONA), the SNB (SARON),
  the Bank of Canada (CORRA) and the RBA (AONIA), each to be verified with the honest agent first. Until then those pairs keep the legacy
  method, labelled.
- **Fixed / float CCS; MTM-reset float / float CCS.** Not needed for the owner's trades.
- **The legacy fixed / fixed XCCY** keeps its pricing; only D8, D11, D17 and G16 / G17 change it.
- **The PB spread by tenor and size; IM netting and risk-based IM** (OTC-08, OTC-11). One dealer-terms redesign, with its own acceptance,
  in 1.0.6.2.
- **Equity forwards** (OTC-12, OTC-13).
  - OTC-12 is a money defect: a client sale is paid about 1.2 %/yr too much. It is the first item of 1.0.6.2.
  - No equity forward is open in the owner's books.
- **ACT/365 for JPY / CAD / AUD** (OTC-21). It goes with those currencies' daily rate sources, because it moves the legacy prices of three
  products.
- **OTC notional caps** (Q8), if the owner wants them.
- **The Bank of England gilt curve.** The BoE is reachable with GOV_UA, so gilts could leave their LIMITED monthly proxy. Out of this
  build's scope; noted for the gilt owner.
- **The CME FX futures cross-check of implied points.** The contracts are thin and stop short of 24M.
- **CSA interest on cash collateral.** Not simulated, as today.

## 15. Critic answers (FX-C-01..FX-C-19)

| Id | Verdict | Answer |
|---|---|---|
| FX-C-01 | accepted, verified | BoE IADB and the yield-curve zip answered 200 to GOV_UA at 19:18 EDT (the critic saw 403 to TERMINAL_UA) and the spot rates are proven cc (spot = averaged forwards, 0.5 bp); GBP now prices on the observed BoE OIS curve via a GOV_UA-only provider (403 = unavailable, no other agent), with 1.3, D2, D4, D12, R1, Q1 and section 14 rewritten and B-T1 / B-T6 / B-T8 re-pinned. |
| FX-C-02 | accepted | Every 2Y rate now has an allowed form: GBP observed (BoE OIS), EUR the ECB AAA curve (cc) as a labelled PROXY plus `eur_ois_spread_bp` (GS schedule, 0.0 ASSUMED), USD the IRS desk's Treasury proxy; flat overnight rates are a cross-check only, and Q1's default is the observed / proxy row (D2, D5, Q1). |
| FX-C-03 | accepted, verified | The 6-pip print was confirmed and measured (10-28, 509 minutes: mean -2.45 pips, max 3.79; the ECB 14:15 reference matched the derived cross): the PB forward now prices off the derived cross with the print and difference stored, `CROSS_TOL_PIPS` = 5.0 from that distribution, every sensitivity is defined once by re-pricing, and all pins come from one consistent fixture (D3, 3.2, B-T1, B-T6, B-T8). |
| FX-C-04 | accepted | C1 / C2 / C8 now trace every new or changed row to its command (journal, events, deals, contracts, VM, fills via orders) in four classes, and pass when the run and its lifecycle wrote exactly the expected set and pre-existing contracts changed only through their own jobs; C5 is scoped to F1:PB_GS / F1:TREASURY and post-snapshot attentions; a concurrent owner fill is a PASS tamper case (10.3, D-T6). |
| FX-C-05 | accepted | The 50-pip check is gone: `otcplan.reprice`, an independent stdlib pricer that may not import the pricing code, re-derives DFs, F, points, hw_px, K, X0, N_USD, A*, PV(0), the par and dealt spreads, mark0 and the conversion before accept and in C3, and C4 matches every input to its stored row (K9, 10.2, 10.3, D-T8). |
| FX-C-06 | accepted, verified | `_currency_balances` (risk.py 863-885) already sums 2350 / 2360 / 2340, so hooks now return only the off-ledger part (the clean mark's FX delta and unposted exchanges; the forward's legs until delivery); C7 adds PV-delta equality and B-T12 pins EUR nominal -4,000,000.00 and PV delta -3,882,523.08 after S (D14, K5-K7). |
| FX-C-07 | accepted, verified | `_terminate_xccy` funds EUR via `fx.fund_ccy`, which never converts (swaps_trs 2153, fx 1190-1223); termination is now cash-settled in USD at the full value with the half-width against the Fund (USD 696.34 in the example), refused before S while the conversion is linked, for both XCCY variants (D17, C-T14). |
| FX-C-08 | accepted, verified | Live read at state_version 3897: SWP-000004..6 have NULL `trade_date` and SWP-000006 has no VM row; the key start is min(start_date, COALESCE(trade_date, start_date)) only while no VM exists, in both `vm_keys` and `_vm_core`, never raising, pinned by C-T13 and checked on the owner copy by the forward check (D8, G17, 9 item 5). |
| FX-C-09 | accepted | 1.5 maps OTC-01..23 to fixed in 1.0.6 / package-test-hours here / deferred with the owner-visible reason; the orchestrator amends 15.15b before the pool and the report lists the deferrals; the PB desk keeps a labelled legacy path for other pairs up to 1Y; the open-ended merge rule is replaced by a mapping with a 0.5 h reserve; hours re-budgeted to 17-22.25 h. |
| FX-C-10 | accepted, verified | No limit field exists (`prime.FIELD_SPECS`, compliance); every quote, record and report now states "notional limits: none configured" with headline gross / NAV (2x warning) and gross notional / NAV before and after, and Q8 offers caps (D18, 10.7). |
| FX-C-11 | accepted | B's record now carries a basis block (EUR/USD and GBP/USD levels from the GS schedule, cross 0.0 bp, ASSUMED) applied to both discount curves, and basis01 is reported per pair (EUR/USD +436.82 A, +419.56 B; GBP/USD -419.51 B after S) instead of 0 for the forward (D5, 3.2, section 6, B-T8, B-T9). |
| FX-C-12 | accepted | The owner-copy forward check to S+1 with a control-copy job diff is mandatory, has no fixture fallback, runs in V and in the same-day dry run, and is a precondition of the live booking; SETTLE_DUE no longer settles linked rows on S+1, and 10.8 is the START runbook (9 item 5, 10.4, 10.5, D7, 10.8). |
| FX-C-13 | accepted, verified | (a) a lost accept is re-POSTed with the same id (commands.py 338-345 replays) then looked up by client_ref; (b) DUPLICATE_CLIENT_REF only for a contract or a REQUESTED / QUOTED RFQ; (c) attempts per run, run id in the request ids; (d) a QUOTED RFQ is declined before the next attempt; (e) `swaps.h_book` (P-C) checks and writes client_ref for every kind, C-T15 (D13, 10.2). |
| FX-C-14 | accepted | The basis label now reads "an assumed level, not a neutral one" with its value per bp; schedule-term re-marks get their own named NAV-bridge line, and SETTINGS_CHANGED events are listed in the report and by checker item C11 (D5, 5, 10.3, 10.7). |
| FX-C-15 | accepted, verified | (a) USD pairs use the T+1 exception (EURUSD 11-10 settles 11-12); (b) a cross's value dates are also New York days (EURGBP 11-26 refused; 11-24 settles 11-27); (c) a USGS (SIFMA) calendar refuses SOFR period dates without a published index; each pinned in P-A / B-T3 (D10, K1). |
| FX-C-16 | accepted | The CSA exposure is now the full (dirty) value, accrued coupons included at X_t (D8, 3.3), pinned by C-T7 with 1225 / 2340 balances. |
| FX-C-17 | accepted, verified | `tests\terminal_fixture_server.py` is a static route-to-JSON server owned by no package; D-T5 now uses a small stateful stdlib fake server written inside P-D's own `test_terminal_otcplan.py`, and the shared server is not touched (P-D). |
| FX-C-18 | accepted | The rfr and curves slots' ECB requests use a 60 s timeout with 2 attempts (an HTML error page = unavailable); SONIA is read from the BoE IADB with GOV_UA first and FRED as fallback, both as-of dates recorded (D12, K2). |
| FX-C-19 | accepted | The gap is stated as a number in the quote, record, ladder and report: the 2028-10-31 purchase needs about USD 2,231,600 (2,306,060 at the outright) against about 1,989,338 of PB and Treasury USD after S and carry, short about 242,261.57 (318,682.31); Q7 names this obligation (D9, section 6, 13). |

## Appendix A. Sources (each verified 2026-10-28; raw responses in `scratchpad\fxccs-research\dl\` and `scratchpad\fxd\dl\`)

| Source | Notes |
|---|---|
| https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT?lastNObservations=12&format=csvdata | ESTR; HTTP 200 at 17:42 EDT |
| https://data-api.ecb.europa.eu/service/data/EST/B..?lastNObservations=1&format=csvdata | the ESTR compounded index EU000A2QQF08.CI and the compounded averages |
| https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M+SR_6M+SR_9M+SR_1Y+SR_2Y+SR_3Y+SR_5Y+BETA0+BETA1+BETA2+BETA3+TAU1+TAU2?lastNObservations=3&format=csvdata | the AAA spot curve and the Svensson parameters; HTTP 200 in 1.6 s at 19:25 EDT (the critic saw 20-30 s and an HTML error page) |
| https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV | DFR 2.50 |
| https://data-api.ecb.europa.eu/service/data/EXR/D.USD+GBP.EUR.SP00.A | ECB reference FX: 10-28 EURGBP 0.83785, EURUSD 1.1133 |
| https://markets.newyorkfed.org/api/rates/secured/sofr/last/8.json | SOFR |
| https://markets.newyorkfed.org/api/rates/secured/sofrai/last/3.json | the SOFR Index and averages |
| https://home.treasury.gov/.../daily_treasury_yield_curve&field_tdr_date_value_month=202610 | UST par |
| https://fred.stlouisfed.org/graph/fredgraph.csv?id=IUDSOIA | SONIA (also ECBESTRVOLWGTTRMDMNRT, ECBDFR, SOFR, SOFRINDEX) |
| https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Oct/2026&Dateto=now&SeriesCodes=IUDSOIA,IUDZOS2&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N | HTTP 200 to GOV_UA (639 bytes, 19:18 EDT); 403 to TERMINAL_UA (critic) |
| https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip | HTTP 200 to GOV_UA (424,114 bytes, 19:18 EDT); "OIS daily data current month.xlsx": sheets info, 1. fwds short end, 2. fwd curve, 3. spot short end, 4. spot curve; 2026-10-01..10-27 |
| UK DMO XmlDataReport | bot-manager challenge: not used, not needed |
| https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X (also EURGBP=X, GBPUSD=X; `interval=1m` for 10-28 12:00-22:00Z; 6EZ27.CME; EURGBP1Y=X answers 404) | FX spot, the 1-minute cross measurement and the futures probe |
| `C:\Users\logan\.finsim\terminal.db` read through `mode=ro` at about 17:45 EDT (state_version 3867) and 19:30 EDT (3897) | the live book; NULL trade dates; SWP-000006 without a VM row |
| The 1.0.6 tree (read-only) | `products\swaps_trs.py`, `swaps.py`, `otc_options.py`, `fx.py`, `prime.py`, `risk.py`, `tables.py`, `calendar.py`, `commands.py`, `db.py`, `quotes\providers.py`, `quotes\rates.py`, `quotes\http.py`, `config.py`, `lifecycle.py`, `lifecycle_eod.py`, `lifecycle_jobs.py`, `blotters.py`, `server.py`, `api.py`, `static\components\swapticket.js`, `otcticket.js`, `pbschedule.js`, `static\pages\risk.js`; `tests\terminal_fixture_server.py`, `tests\terminal_helpers.py` |

## Orchestrator addenda (1.0.6.1 as built)

Notes the 1.0.6.1 builders wrote for this plan (they may not edit docs); applied verbatim by the orchestrator at the 1.0.6.1 gate.

### P-A, to D10 / 1.4

P-A build note (1.0.6.1, 2026-10-29). The four calendars were checked against real publication days on FRED with the project agent, and each reproduces them exactly: TARGET2 against every ESTR day since 2019-10-01; London (England and Wales) against every SONIA day since 2000-01-04, including every substitute day and the one-offs of 2002, 2011, 2012, 2020, 2022 and 2023; New York (Federal Reserve) against every EFFR day since 2016-03-01; USGS against every SOFR day since 2018-04-03. The evidence changed two USGS assumptions. (1) No SOFR was published on Good Friday 2021-04-02 or 2023-04-07, so the hand-maintained list of early-close-only Good Fridays is empty. (2) SIFMA closes the Friday before an Independence Day or Christmas Day that falls on a Saturday (no SOFR on 2020-07-03, 2021-12-24 or 2026-07-03); the same is ASSUMED for Juneteenth from 2022 (first case 2027-06-18). A New Year's Day or Veterans Day on a Saturday closes nothing (SOFR was published on 2021-12-31 and 2023-11-10). One-off closes on the list: 2001-09-11/12, 2012-10-30 and 2018-12-05; 2025-01-09 was an early close only (SOFR was published). USGS 2026-2028 is therefore New York plus 2026-04-03 and 2026-07-03; 2027-03-26, 2027-06-18 and 2027-12-24; 2028-04-14.

### P-A, to Q1 / R13

Implementation note (P-A, 2026-10-29). Following D2 and D5, the EUR OIS spread is added to the ECB grid nodes only, never to the ESTR first node; this is the same rule behind the +423.92 per bp of B-T8. Under that rule, Q1's rows read: +10 bp F 0.865049, K 0.864876, GBP 1,729,752.00 (-3,496.00 against the default); -10 bp F 0.868548, K 0.868374, GBP 1,736,748.00 (+3,500.00). The plan's figures (0.865063 / 1,729,780.00 / -3,468.00 and 0.868535 / 1,736,722.00 / +3,474.00) are a parallel shift of the whole EUR curve, ESTR node included. R13's 'about GBP 3,470 per 10 bp' becomes about GBP 3,500. The default row (0.0 bp, F 0.866797, K 0.866624, GBP 1,733,248.00) is unaffected. tests/test_terminal_fxcurves.py pins both variants.

### P-B, B-T12

B-T12 as built (P-B, 2026-10-29): the section-6 figures value the 2026-10-27 curves at 2026-11-02, which the 3-business-day curve age rule (D2) refuses; the test values the same rows re-dated 2026-10-30 (fxcurves.fixture_curves(d0=2026-10-30)): EUR nominal -4,000,000.00 (unchanged), EUR PV delta -3,882,616.37 (A -1,999,651.71 = ledger -2,000,000.00 + hook +348.29; B -1,882,964.66), EURUSD +5 % -216,611.17, EUR/USD basis01 +856.41 (A +436.83, B +419.58) - the designer's own functions re-run with T0 = 2026-10-30.

### P-D, operator note for 10.4 / 10.5 / 9 item 5

1. otcplan run checks /api/health. It accepts VERSION 1.0.6 as the 1.0.6.1 code only when the 1.0.6.1 modules (swaps, swaps_trs, otc_options, fx, ccyfunding) are bound, so V's pre-gate dry run is not blocked; the report says so. It requires provider rows boe, ecb and nyfed with ok=true, so start the dry-run server early enough for P-A's rfr/curves slots to fetch (BoE after 07:00 ET, ECB after 08:00 ET); before that the server check fails (exit 2) and nothing is sent.
2. Run the checker with the run report written by run (report.json in --report): py -3.12 -m finsim otcplan check --db <copy> --before before.json --run <dir>\report.json [--second <dir2>\report.json] [--after-restart views.json]. --after-restart takes {"before": {...views}, "after": {...views}}; C9 compares the stored facts of each contract, not live marks.
3. Forward check: py -3.12 tests\terminal_fx_forward_check.py --run <C1.db> --control <C0.db> --report <dir>. It never writes C0/C1: it works on backups in --work (deleted at the end) and needs about 2 x 35 MB of disk. On the owner's book with both trades it ran 134 ticks per copy in 151 s.
4. Until the orchestrator adds the SettleDue filter (request 1), the forward check and the live START morning show two WARN LINKED_SETTLEMENT items from SETTLE_DUE at 00:30 on S. They are expected; the START resolves them at 00:45.

### Fix round 2, to 9 item 5 and 10.4 step 9 (the forward check)

Forward check (V1061R1-01 / -03 / -04).
- One set of inputs: before the first tick, the control's working copy receives the run copy's quote_cache, curve_points, marks, dividends, debt_prices and finra_short. The report (inputs_before_alignment) lists what differed, so a dry run inside the equity session no longer fails on input drift.
- Held inputs: overnight prints (daily series only), term-curve grids and the par curve are re-dated once per New York business day, at the first tick from 08:00 ET, to the previous business day. They are never re-dated on a weekend or holiday, so no weekend VM posts that production would not post.
- Comparison: pre-existing contracts are compared on the rows written since the fork (the control's last command).
- START of each cross-currency swap: the report prints settled cash per currency in the swap's account (funding sweeps apart). It checks each net against the quote's net_start_flows (USD = minus the conversion spread, the -40.00 class; EUR 0.00) and against the exchange and conversion rows. It also shows the funding (source, own cash available, shortfall, Treasury sweep) and checks that the account's and Treasury's settled cash are at or above zero after the START and at the end of the run.

### Fix round 3, to 10.2 step 6 and 10.3 C9 (cash and collateral views)

Cash and collateral views (V1061R2-02).
- 10.2 step 6 reads cash from GET /api/accounting/cash?scope=F1:ALL (every account per currency, and the Treasury) and collateral from GET /api/collateral/summary?scope=F1:ALL (IM and VM per counterparty). GET /api/cash and GET /api/collateral never existed.
- report.txt prints the 10.7 item:
  - the Treasury's settled cash per currency and its available USD;
  - the plan account's prime broker: IM posted (required), VM posted and VM received.
- C9 Restart compares, before and after a stop and start, the stored facts of five views:
  - the contracts and the positions (Risk, scope F1:ALL and the PB account);
  - the cash: ledger cash per account and currency (settled, receivable, payable, held at a lender, margin, accrued, OTC margin) and the Treasury's settled cash per currency;
  - the collateral: each prime broker's IM posted and required, VM posted and received.
- Available and reserved cash and live marks are re-computed and not compared.
- A view that could not be read before or after the restart fails C9. Before this fix, two "unavailable" answers compared equal.
