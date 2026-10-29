# Shaffer Institutional Book V3 (book I): opening order book, funding, execution procedure, builds and schedule

Planner of the V3 design workflow, written 2026-10-28 from 19:13 EDT; **revision 2 written 20:29-20:50 EDT** (clock from `date`) after the
independent critic's 14 findings (V3-C-01..V3-C-14), the owner's answers of about 19:40 ET and the orchestrator's binding notes of 20:08 ET.
Each finding was verified against the code, the data and this plan's own arithmetic, then fixed here or answered with evidence; the last
section, "Critic answers", has one line per finding. Design only: this file is the only repository file written. Nothing was submitted, booked
or deployed.

> ORCHESTRATOR NOTE (2026-10-28 20:53 ET): R-D (the TRS stale-listing guard, 409 STALE_LISTING) is built in release 1.0.6.1 by its package P-C as one small helper; package V-X of 1.0.8 reuses that helper instead of building its own. Everything else in 6.1 stands as this revision says (R-A / R-B on the FX plan's 1.0.6.2 list, R-C as V-R in 1.0.8, R-E dropped). The script's own stale-listing check (5.3 item 7) stays until the app's guard is live.

**Governing documents, read in full**
- `docs\OWNER_INSTITUTIONAL_V3_2026-10-28.md`: the owner's V3 directive (supersedes V2), with his 18:30 ET decisions ("Use VMRK instead";
  DTCC "Yes, if the terms allow"), the orchestrator's 18:35 ET DTCC terms finding (DTCC is NOT used), **his 19:40 ET answers to this plan**
  (Q1 SPCX "Trade as listed"; Q5 QQQ "Gross across legs"; Q3 "Copy the Fund's basis"; Q6 / Q11 "Redirect to CDX + ITB puts", DLR remainder
  blocked; every other question takes the plan's default) and **the orchestrator's 20:08 ET notes**, binding for this revision: (a) the ORCL
  CDS books only at the owner's own negotiated mid (CDS plan 9.5 step 7), never on the model; (b) V3's JPY forwards stay on the legacy PB
  pricing path: before any V3 FX line books, the JPY rate's age and the pricing path are checked, and a failing line stays UNEXECUTED with the
  exact reason.
- The feasibility pass of 2026-10-28 18:00-18:37 ET (105 rows and two notes, handed to this planner): listed sleeves priced on a served copy
  of the 1.0.6 tree, OTC sleeves on release r105 (1.0.5). Every number in section 3 comes from those rows unless it is marked otherwise.
- `docs\FX_CCS_PLAN.md` **revision 2** (19:10-20:30 EDT, release 1.0.6.1) and `docs\CDS_SINGLE_NAME_PLAN.md` (revised 19:59 EDT, release
  1.0.7): reused, not duplicated (sections 3.4, 3.12, 5.4.10 and 6.1). Revision 2 of the FX / CCS plan does **not** carry this plan's
  requested rates scope (R-A..R-E); section 6.1 says where each item went.
- `docs\FINSIM1_PLAN.md` sections 3 (package rules) and 12 / 15 (gate, phases); `docs\BUILD_LOG.md` through 18:32 ET;
  `docs\OWNER_PERSONAL_FUND_2026-10-28.md` and the personal-fund tooling (`C:\Users\logan\finsim-orchestrator\personal\`), whose script
  structure section 5 copies.
- Code read (1.0.6 working tree, read-only): `risk.py` (scenarios), `repo.py`, `collateral.py` (1.0.6 Reg T, margin calls with their due
  times, CSA pledges), `commands.py` (request-id idempotency), `orders.py` / `orders_preview.py` / `orders_rules.py` (`fill_cap`, the
  per-order `participation` copied from the setting), `matcher.py` (oldest order first), `sessions.py` (CBOT grains: two sessions a trade
  date), `server.py` (`OPTIONAL_MODULES`, the 2-entry chain cache), `quotes\cache.py` / `quotes\worker.py` (chain pins), `api.py`
  (`PACKAGES`, `PLANNED_STATIC`), `api_products.py` (`_materialise_option`), `products\options.py` (`chain_freshness`, `ensure_option`),
  `products\bonds.py` (accrued to the trade date), `products\swaps.py` (RFQ, C14), `products\swaps_trs.py`, `products\swaps_cds.py`
  (`cdx.terms`, the accrued rebate), `products\otc_options.py` (PB forwards: tenor or date), `fx.py` / `tables.py` (`rate_age`: a monthly
  proxy at most 62 days after its month), `db.py` (`curve_points`), `books.py` / `config.py`, `counterparties.py`, the API route tables,
  `tests\terminal_fixture_server.py` (`OPTIONAL_STATIC`); the live release copy `~\.finsim\releases\2026-10-28-r105` for its OTC routes.

**Contact with live data (all read-only)**
- Revision 1: one sqlite3 backup of `%USERPROFILE%\.finsim\terminal.db` (source opened `mode=ro`) at 18:48:53 ET into the NEW folder
  `scratchpad\checks\v3-184853\` (integrity `ok`); queried, then deleted.
- Revision 2: one more backup the same way at 20:19:10 ET into the NEW folder `scratchpad\checks\v3-201909\` (integrity `ok`, 25.9 MB);
  queried (positions, working orders and borrows of F1, P1 and I1; the `cdx_terms` rows; the master rows of ITB, XHB, SPCX, AVB and VMRK);
  deleted at 20:30:27 ET. No server was started, ports 8765 / 8865 were not touched, no browser was opened.
- Network, gently. Revision 1: Bank of Japan 4 requests with `LoganTerminal/1.0 (loganshaffer87@gmail.com)`; Yahoo chart 13 requests for
  the SR3 months (browser user agent, Yahoo only, 2 s apart). Revision 2 (20:15-20:25 ET): Cboe's delayed ITB chain, 1 request with the
  terminal's honest agent `FinSimTerminal/1.0`; Yahoo chart 2 requests (ITB daily, CLH27 one-minute bars; browser agent, Yahoo only); SEC
  EDGAR 4 requests with `LoganTerminal/1.0 (loganshaffer87@gmail.com)` (Oracle's filing index, two pricing term sheets, the 2025-09-25
  prospectus supplement), at most one a second. No DTCC, no Stooq, no other source.

**Spelling**

| Term | Meaning |
|---|---|
| I1 | the Institutional Book's lifetime (book I); its accounts are `I1:TREASURY`, `I1:US_EXCHANGE`, `I1:DEBT`, `I1:CREDIT`, `I1:FOREIGN_FX`, `I1:PRIVATE`, `I1:PB_GS` |
| F1 / P1 | the USD 25M Fund Book and the USD 1M Personal Book: never touched by anything in this plan |
| TRS, CDX, CDS, IRS | total return swap, credit index swap, single-name credit default swap, interest-rate swap |
| IM / VM / CSA | initial margin / variation margin / the ISDA credit support annex (GS, signed 2026-10-21 for I1) |
| DV01 / CS01 | P&L for +1 bp of rates / of credit spread, signed |
| RFQ | the 1.0.6 PB OTC quote flow: request, simulated dealer quote (about 30 s), accept within 120 s |
| SR3 | CME three-month SOFR futures |
| TONA / ESTR / SOFR | Japanese, euro and US overnight risk-free rates (BoJ, ECB, NY Fed) |
| observable / model / proposed | the three labels every figure in the blotter carries (D9) |
| child order | one slice of a large equity ticket, sent pass by pass so that later F1 / P1 orders on the same name are not starved (D18) |

---

## 0. Summary

**What gets booked, and when (owner answers of 19:40 ET and defaults applied; section 7 has the clock)**

| When | What | Release |
|---|---|---|
| Tonight 10-28 (CME Globex, trade date 10-29) | 10 futures lines: gold, the WTI short ladder, copper, silver, ES / RTY long, NQ short (the deferred CL months will fill mainly in the 10-29 day session: the night is thin) | live 1.0.5 |
| Tonight, resting for the 10-29 open | 17 physical longs (SPCX included: "Trade as listed"), 8 physical shorts (VMRK replaces AVB), the USD 100M IBIT short, as LIMIT DAY orders, the large tickets as their first child orders (D18); 18 option orders: the 17 legs of strategies A and C-H and B's long 700 P (B is 772 spreads, gross across legs; its 640 P follows as the 700 P fills) | live 1.0.5 (1.0.6 if deployed by then) |
| Thu 10-29 08:00-16:55 ET | the six Treasuries by CUSIP (USD 400M at market value; they settle 10-30) | live |
| Thu 10-29 09:30-14:20 ET | the three CBOT grain lines, in the day session only (a night-session DAY order expires at 08:45 ET) | live |
| Thu 10-29 09:25-15:40 ET | eight foreground passes: the next child orders, the reprice of off-market limits, B's 640 P as the 700 P fills | live |
| Thu 10-29 15:45-16:30 ET | Reg T top-up transfer into `I1:PB_GS` (about USD 90M, when 1.0.6 runs) | 1.0.6 |
| **Fri 10-30 10:00-15:30 ET** | **14 equity TRS (USD 1.05B, SPCX included), CDX IG USD 100M + HY USD 125M + IG USD 50M (the iTraxx redirect), after the I1 CDX basis is set. Moved from 10-29: the liquidity rule D13 counts repo only on settled Treasuries, and they settle 10-30** | 1.0.6 RFQ if deployed, else 1.0.5; 1.0.6.1 if deployed (`client_ref`) |
| First FX business day after the 1.0.6.1 deploy and the owner's own F1 FX booking (Fri 10-30 at the earliest), 08:00-16:30 ET | EUR forwards (USD 50M) on the 1.0.6.1 curves; JPY forwards (USD 50M) on the legacy PB method if the orchestrator's age and path checks pass, with Tokyo-valid value dates | 1.0.6.1 |
| After the 1.0.8 deploy (about Tue 11-03 to Wed 11-04) | the three pay-fixed IRS (USD 300M) on the SOFR futures curve (V-R) | 1.0.8 |
| After the 1.0.7 deploy, only at the owner's own negotiated mid | Oracle 5Y single-name CDS (USD 25M) | 1.0.7 |
| When a current chain and the deliverable check allow (owner 19:40: ITB) | ITB puts for the XHB remainder: at most about USD 3.7M of the USD 10.64M of delta | any |
| Not executable (no liquidity) | the DLR option remainder USD 1.67M (re-checked after the 10-29 earnings); the ITB residual about USD 6.98M | - |

**The exposure budget (V3 section 2 measures; USD)**

| | Amount | Share of 3,000M |
|---|---:|---:|
| Target | 3,000,000,000.00 | 100 % |
| Sized from the 10-28 closes (all lines) | 3,000,014,737.36 | 100.0 % |
| Executable (listed from 10-29, OTC on 10-30; the child lines finish over 2-4 sessions) | 2,562,702,980.34 | 85.4 % |
| Needs a build (IRS 300M on 1.0.8; FX 100M on 1.0.6.1; ORCL CDS 25M on 1.0.7 at the owner's level) | 425,000,000.02 | 14.2 % |
| HELD for a current chain and the deliverable check (ITB) | 3,660,000.00 | 0.1 % |
| Blocked (DLR remainder 1.67M; ITB residual 6.98M) | 8,651,757.00 | 0.3 % |
| **Gap after every build, ITB tranche booked** | **8,637,019.64** | **0.29 %** (achieved 2,991,362,980.36) |
| Gap after every build, no ITB | 12,297,019.64 | 0.41 % |

If the JPY forwards fail the orchestrator's checks, USD 50M more stays unexecuted; if the owner gives no ORCL CDS level, USD 25M more.

**Funding (section 4).** By the 10-29 close the book encumbers about USD 655.4M of its USD 1,000.0M of cash: Treasuries 400.1M, longs
120.1M, option premium 22.5M, futures IM 19.1M, short-sale excess 3.6M and the 1.0.6 Reg T reserve on the shorts 90.0M; about USD 344.6M
stays unencumbered. On 10-30 the TRS, CDX and FX add about USD 121.5M of IM; the CDX initial payments bring about 11.3M back on 11-02;
the IRS and the ORCL CDS later add about 8.6M. About **USD 225.7M stays unencumbered after every build** (22.6 % of NAV). Treasury repo is
**not drawn**: it is the contingency line, about USD 392.0M at the dealer's 2 % haircut, available once the bonds settle on 10-30. The
first-order planning stress puts the largest call at about **USD 430.6M** (scenario 8, all bearish theses against the fund: USD 382.2M due
the same day, 48.4M of Reg T the next morning) against USD 617.7M of cash plus repo (**1.43x**; 1.36x if SPCX is left unshocked). The fund is
never forced to USD 3B: the script stops before any line that would breach the liquidity floor (D13). Booking the TRS on 10-29, as revision
1 planned, would have stopped at the NVDA line (D13; the tooling tests that breach point).

**Builds (section 6).**
- 1.0.6.1 (FX / CCS revision 2) carries none of this plan's rates scope. The EUR forwards book on its curves; the JPY forwards stay on the
  legacy method (orchestrator); TONA and a Tokyo calendar stay on the FX / CCS plan's own 1.0.6.2 list (not duplicated here).
- Release 1.0.8, the V3 pack (5 packages, at most 4 builders at once): the stress engine with the ten V3 scenarios, due times per
  counterparty, margin calls, repo haircuts, a liquidation waterfall and explicit MARGIN_DEFAULT / INSOLVENT states (V-S); the fund book
  (mandate, institutional blotter, fund reconciliation with sub-ledger "accounts", the Directional Options P&L table with attribution and an
  exit monitor, per-fund metrics; V-B); its UI (V-U); execution and master fixes (chain pinning, size-aware option fills, AON slicing,
  AVB / VMRK corrections, one stale-listing helper with the order and TRS guards; V-X); the SOFR futures curve and SOFR swap conventions
  for the IRS (V-R).
- Before 1.0.8, the submission script's `report` mode gives the owner the blotter, cash and a first-order stress with due times every
  evening (R0, outside the app).

**Owner questions that matter most now (section 8; each has a default):** Q4 the IRS now wait for 1.0.8 (two to three business days more) or book
on the Treasury-par model now; Q8 re-opened: the JPY forwards on the legacy monthly proxy (the orchestrator's path) or held for TONA; Q12 the
ORCL CDS needs his own level; Q18 child orders for the large tickets (F1 / P1 interleave) or single tickets (faster); Q19 ITB: the
short-dated Nov-20 puts and the adjusted series.

**What changed in revision 2**
- The owner's answers: SPCX trades as listed (both lines NEXT); B is sized gross across legs (772 spreads); the I1 CDX basis copies F1's;
  the iTraxx USD 50M becomes CDX.NA.IG buy protection (line 4.3a); the XHB remainder goes to ITB puts (HELD under D19); the DLR remainder
  stays blocked.
- The TRS and CDX book on Fri 10-30, after the Treasuries settle (V3-C-01); the large equity tickets go as child orders (V3-C-04); the
  grains go in the day session (V3-C-07); the FX dates follow the real booking date (V3-C-08); the SFI deals sit in `I1:PB_GS` (V3-C-11).
- The Treasury faces follow the floor rule and the app's trade-date accrual; the cash tables start from cash, not NAV (V3-C-09).
- V-S drives every state with due times; scenario 9 acts on each strategy's net value; short-history betas default to 1.0 (V3-C-05).
- The 1.0.8 hooks register every module and static file (V3-C-06); option mechanics corrected (V3-C-12); CDX terms ids per index
  (V3-C-10); the rates scope moved to 1.0.8 (V3-C-13); the ORCL CDS carries an EDGAR-identified reference obligation and books only at the
  owner's level (V3-C-14, orchestrator note a).

**What changed against the feasibility rows (revision 1, still true)**
- DTCC is excluded (terms): every DTCC-derived figure in the feasibility notes is discarded and used nowhere (1.3).
- AVB rows are SUPERSEDED by the VMRK rows under the owner's 18:30 decision (D4).
- IRS: "needs build" means the SOFR futures curve from the free SR3 strip, not a DTCC curve (D11).
- CDX: "calibrated" bases from DTCC prints are void; the I1 basis copies F1's owner-calibrated basis (Q3, answered).

---

## 1. Facts this plan stands on

### 1.1 The live state (read-only copies, 18:48:53 ET and 20:19:10 ET)

**Book I (lifetime I1, profile INSTITUTION).**
- Cash: `I1:TREASURY` GL 1010 USD 1,000,000,000.00 plus 1230 interest receivable 661,944.45. NAV 1,000,661,944.45.
- Accounts (all OPEN, sweep on, `pb_financing` 0, `pb_limit` 0): TREASURY, US_EXCHANGE, DEBT, CREDIT, FOREIGN_FX, PRIVATE, PB_GS (broker GS).
- Goldman Sachs agreements for I1: ISDA Master, CSA and PBA, signed 2026-10-21 (command 1357).
- `I1:PB_GS` schedule = the GS seed: equities USD 0.0030 / share (minimum 1.00), options 0.55 / contract, futures 2.00 / side, GC borrow
  0.40 %, hard-to-borrow +0.10 %, TRS spread 0.60 %, IRS half-width 1.0 bp, OTC spread multiplier 1.0, CTM margin, PB debit SOFR + 1.25 %,
  credit interest SOFR - 0.75 % ("modelled assumptions; real PB schedules vary").
- No `cdx_terms` row for `I1:PB_GS`. F1's row: IG basis -24.5 bp, half-spread 0.50 bp, IM 2 %; HY basis +23 bp, half-spread 2.00 bp, IM 6 %.
- **No orders, no OTC contracts, no borrows, no repos in book I. No V1 or V2 batch exists anywhere in the app: nothing to cancel or
  supersede, and no fill to preserve.** The V3 mandate replaces the V1 / V2 instructions outright.
- Re-checked on the 20:19 ET copy (revision 2): still no I1 order, contract or borrow, and no `cdx_terms` row for `I1:PB_GS`.

**Shared settings.**
- `participation_rate` 0.10: each completed one-minute bar lets at most 10 % of its volume fill, shared by every book. The matcher serves
  orders oldest first and reads what earlier fills took from the same bar (EXEC-6), so the owner's older P1 / F1 orders keep priority. Each
  order stores the setting's value when it is submitted; there is no per-order field. The other side of the same rule: a large I1 ticket
  takes the whole cap of a shared name until it is done, so a LATER F1 / P1 order on that name would wait (BAR_CAP_USED). D18 sends the
  large tickets as child orders so that later orders interleave; the script never changes the setting.
- `sweep_confirm_threshold_c` holds 2,000,000.00, the old seed nobody chose, which the code treats as OFF. The 25 %-of-NAV notional confirm
  stays: no V3 order reaches USD 250M (the largest is the USD 100M IBIT short).
- Treasury sweep-ins are automatic at any size (decision s7.11). `treasury_reserve_c` 0. PB / OTC threshold 25,000,000.00; QP threshold for
  INSTITUTION 0.

**F1 (must never change through this plan).**
- 36 filled and 2 working orders: O-000040 STO 500 ORCL 2027-01-15 165 C and O-000041 BTO 100 ORCL 2027-01-15 150 C.
- `F1:PB_GS` OTC: CDX 2 open, IRS 1 open, TRS 4 open and 1 terminated. 5 open borrows.
- Cash: `F1:TREASURY` 2,103,950.48, `F1:US_EXCHANGE` 697,070.38, `F1:PB_GS` 0.00.
- GL fingerprint at 18:48 ET: 133 F1 balance rows, sha256 prefix `1d3154ec6bbecfda`. It changes with F1's own lifecycle (VM, accruals,
  fills), so the stage checks use a structural fingerprint (5.6).
- Positions on names V3 also trades (20:19 ET copy): short C 12,000, DLR 8,500, IBIT 10,500, STT 8,800, VMRK 24,210 (its five open
  borrows); long CRWD, GXO 26,000, JNJ, KTOS 19,000, LMT, NKE, NOC, NOW, PANW, PDBC, PG, SNOW, SPCX 12,850, TMO, WMT, ZS and 5 GCZ26;
  options on IBIT, META, ORCL and QQQ (Nov-20 expiries), which keep those chains pinned.

**P1.** At 18:48 ET the owner's 12 orders O-000043..O-000054; at 20:19 ET 18 orders O-000043..O-000060 (working or waiting for a parent:
shorts AAPL / NVDA / ORCL, buys CRWD / PANW / SPCX / LMT / TMO / NOC / SGOV, options on AAPL, NVDA, TSLA, GLD, USO and IBIT), 3 open
borrows: the personal-fund workflow's. Untouched here; all of them are older than any I1 order, so they keep priority on shared names.

**Sequences.** Next order O-000061 at 20:19 ET (O-000055 at 18:48), next swap SWP-000007, next CDX CDX-000003.

### 1.2 Releases and the other designs

| Release | State at 19:13 ET | What V3 uses from it |
|---|---|---|
| 1.0.5 (r105, commit cb3540b) | LIVE on port 8765 | orders, futures, bonds, TRS / CDX / IRS by direct booking (`POST /api/otc/contracts`), CDX terms (`POST /api/otc/cdx/terms`) |
| 1.0.6 | in P8 verification and fixes; gate expected early 10-29 (standing pre-approval covers the deploy) | RFQ-only OTC booking with PENDING_CONFIRM and the 17:05 ET simulated confirmation; account-level Reg T and the 17:20 ET margin calls; CSA Treasury pledges (cash IM is not released against them in this version); TRS booked at the official close after hours; NEXT_SESSION / GTD TIF; amend / replace; the fixes of the OTC audit's local findings (OTC-01, -02, -03, -07, -18, -23) |
| 1.0.6.1 (`docs\FX_CCS_PLAN.md`, revision 2 of 20:30 EDT) | design; pool starts after the 1.0.6 gate (about 17-22 h to the owner's own FX booking) | curves (`fxcurves`: the ECB AAA curve + a GS spread for EUR, the Treasury curve for USD, BoE OIS for GBP), calendars (`ccycal`: TARGET2, New York, London, USGS), PB OTC FX forwards to 2Y for USD / EUR / GBP pairs, `client_ref` on contracts, the `otcplan` program for the owner's F1 trades, the settlement-funding instruction (SFI). Pairs with JPY keep the legacy method (one rate per currency, simple interest, up to 1Y, NYSE calendar). It carries none of this plan's rates scope (6.1) |
| 1.0.7 (`docs\CDS_SINGLE_NAME_PLAN.md`, revised 19:59 EDT) | design; rebases on 1.0.6.1 | single-name CDS (kind `CDS`), CreditGrades equity-implied MODEL spread with the AAA floor, the dealing-level guard; the ORCL 5Y line books on it only at the owner's own negotiated mid (its 9.5 step 7) |
| 1.0.8 (this plan, section 6.2) | to build | the V3 pack, with the SOFR curve for the IRS (V-R) |

The two design pools never run at the same time on this 7.6 GB machine (FX_CCS_PLAN D1, CDS plan header); 1.0.8 follows the same rule.

### 1.3 Data rules

**DTCC is not used.** The owner said "Yes, if the terms allow"; DTCC's Terms of Use (dtcc.com/terms, 2026-03-11) prohibit automated
extraction and compiling the content into a database. Consequences for the feasibility notes, all of which are **discarded and used nowhere
in this plan** (no price, no calibration, no comparison):
- the "calibrated" CDX bases (IG about -27.4 bp, HY about +30 bp) and every "calibrated" CDX upfront;
- the iTraxx Main level (65.9 bp) and its economics;
- the Oracle CDS level (about 280 bp) and its upfront;
- the SOFR, ESTR and TONA OIS term rates and every forward-point or swap-rate comparison built on them.
The two other design documents were written before the terms finding and still mention DTCC (the CDS plan's design-time calibration check
and its Q10 "traded prints as 1.0.8"); those are theirs to revise. Nothing in V3 depends on them.

**Free sources V3 uses** (each figure carries its label, D9):

| Source | For | Label |
|---|---|---|
| Yahoo chart (browser user agent, Yahoo only) | equity / ETF / futures prints and bars, SR3 futures months, FX spot | observable |
| Cboe delayed chains (through the app) | option bid / ask / sizes / IV / Greeks / open interest | observable (Greeks: Cboe model) |
| TreasuryDirect FedInvest, fiscaldata, Treasury.gov par curve | Treasury marks, the Treasury master, the par curve | observable |
| FRED (SOFR, ICE BofA OAS), NY Fed (SOFR, SOFR Index) | SOFR fixings, CDX proxies, discounting | observable; CDX price = model |
| ECB data portal (the euro-area AAA curve, ESTR; through 1.0.6.1) | EUR leg of the FX forwards | observable; the curve is a PROXY for EUR OIS; forward = model |
| FRED `IRSTCI01JPM156N` (the OECD monthly JPY rate; the app's legacy input) | JPY leg of the FX forwards (D12) | observable, monthly: LIMITED; forward = model |
| Bank of Japan (TONA, daily xlsx) | a comparison for the JPY forwards only (TONA is the FX / CCS plan's 1.0.6.2 item) | observable |
| SEC EDGAR (`LoganTerminal/1.0` user agent) | listings, delistings, reference entities, debt | observable |

### 1.4 SPCX: both facts

1. **SPCX is a listed, SEC-reporting Nasdaq stock.** EDGAR CIK 1181412 "SPACE EXPLORATION TECHNOLOGIES CORP": S-1 2026-05-20, 8-A12B
   2026-06-10, EFFECT 2026-06-11, 424B4 2026-06-12 (IPO at USD 135), 10-Qs 2026-08-04 and 2026-10-22, Q3 earnings 8-K 2026-10-20. Yahoo quotes
   it (10-28 close 145.23; about 299 M shares traded in the day's one-minute bars). FinSim1's master has `SPCX` as EQUITY, FULL support,
   ACTIVE, CIK 1181412, beta 3.226; its private-company row `PRV-SPACEX` says status PUBLIC, listed 2026-06-12, and the Private Markets desk
   refuses listed companies. The Fund already holds 12,850 SPCX on `F1:US_EXCHANGE`.
2. **The owner's V3 calls SPCX "a private/simulated security" that "needs a defensible bilateral valuation mechanism", and says "Never invent
   an exchange-listed trade for it."**

**Proposed honest treatment, now the owner's decision.** A purchase at the real listed price is not an invented listing; a private
bilateral mark would be the fabrication now that a market price exists. **Owner, 19:40 ET (Q1): "Trade as listed"**: the USD 15M purchase
and the USD 75M receive TRS reference the observed Nasdaq listing (EDGAR CIK 1181412), labelled as exchange prices (resets and VM at the
Nasdaq official close). Both lines are NEXT. The master's beta 3.226 rests on about 95 sessions of history: the stress uses 1.0 until 250
daily returns exist (V-S). Also: the SPCX master aliases (Cursor / Anysphere / xAI) could not be verified (an OTC feasibility remark; a
master clean-up item in V-X).

### 1.5 AVB and VMRK: the facts and the decision

- **AvalonBay (AVB) no longer trades.** EDGAR CIK 915912: 8-K 2026-08-17 (items 2.01 / 3.01 / 3.03 / 5.01), Form 25-NSE 2026-08-17, Forms
  15-12G 2026-08-27 and 15-15D 2026-09-16. NYSE removed AVB before the open on 2026-08-17; each AVB share became 2.793 Vivmark shares. Its
  last Yahoo print is 184.06 on 2026-08-14 16:00 ET.
- **Vivmark Residential (VMRK)** is the surviving combined AvalonBay + Equity Residential apartment REIT: EDGAR CIK 906107 "VIVMARK
  RESIDENTIAL", NYSE, formerly EQUITY RESIDENTIAL (renamed 2026-08-12); 8-K item 2.01 on 2026-08-17. Yahoo and FinSim1's master both label
  VMRK "AvalonBay Communities, Inc." (wrong name).
- **App defects found** (fixed by V-X and the 6.1 guard; until then the script refuses AVB itself): the master still lists AVB ACTIVE; r105
  priced a new AVB TRS at the 75-day-old close with no blocker; the short preview offered AVB at tier T3 with a 25.10 % fee because FINRA has
  no rows. An AVB order could never fill (no fresh print) but it should be refused.
- **Owner decision 2026-10-28 ~18:30 ET: "Use VMRK instead"**, the same dollar amounts in both AVB lines: the USD 65M short TRS (line 3.8a)
  and the USD 12M physical short (line 8.4a). A VMRK short is broader than the AVB thesis: it carries the legacy Equity Residential
  portfolio. The Fund already shorts 24,210 VMRK on its own book (no conflict).

### 1.6 Facts the planner checked tonight

**TONA (a comparison for the JPY forwards; V3 does not price on it, D12).**
- The BoJ publishes the uncollateralized overnight call rate daily: provisional results about 17:15 JST for the day, final results about
  10:00 JST for the previous business day, each as a small xlsx (`.../mutan/d_release/mp/mpYYYYMMDD.xlsx`,
  `.../md/YYYY/mdYYYYMMDD.xlsx`), plus history files `pcall.xlsx` / `fcall.xlsx`.
- Final result for 2026-10-27: **1.227 %** (the file's next rows, 1.280 and 1.202, read as the day's high and low). The app's JPY input today is the OECD monthly proxy
  `IRSTCI01JPM156N` 1.043 % as of 2026-09-01: about 18 bp low and two months old.
- "Terms of Use for the Uncollateralized Overnight Call Rate" (BoJ PDF, read in full): disclaimers only (no warranty, corrections, possible
  discontinuation); it describes TONA as the yen risk-free rate referenced in OIS transactions and asks users to refer to the BoJ's
  published rate. It does not prohibit automated retrieval. The BoJ site-wide copyright page could not be read from the extract: whoever builds
  TONA (the FX / CCS plan's 1.0.6.2 item, 6.1) confirms it first.

**CME three-month SOFR futures on Yahoo (for a SOFR swap curve).** Yahoo quotes SR3 by contract month through Dec-2031 (`SR3Z33`, `SR3Z35`,
`SR3U36` answer 404). 10-28 last prices and day volumes:

| Month | Price | Implied 3M SOFR | Volume |
|---|---:|---:|---:|
| Z26 | 95.815 | 4.185 % | 3,778 |
| H27 | 95.580 | 4.420 % | 4,064 |
| M27 | 95.420 | 4.580 % | 4,861 |
| U27 | 95.365 | 4.635 % | 5,441 |
| Z27 | 95.380 | 4.620 % | 5,805 |
| M28 | 95.420 | 4.580 % | 2,963 |
| Z28 | 95.450 | 4.550 % | 2,291 |
| Z29 | 95.490 | 4.510 % | 765 |
| Z30 | 95.450 | 4.550 % | 479 |
| Z31 | 95.365 | 4.635 % | 329 (last print 13:37 ET) |

Planning arithmetic on this strip (simple averaging, no convexity adjustment, proposed): 2Y and 5Y SOFR swap rates near 4.5 %, roughly 15-35
bp below the app's Treasury-par IRS quotes (2Y par 4.670 %, 5Y 4.850 %). The build computes the exact curve (V-R in 1.0.8, 6.1).

The volumes are Yahoo's reported figures, a partial feed far below CME's real SR3 volume (Z26 showed 5,576 by 19:40 ET): shown for
information, never used as a liquidity test (6.1); the prices are consistent across months.

---

## 2. Binding decisions

**D1. One book, its own accounts.** Every V3 order and contract is in book I (lifetime I1). Account map:

| Sleeve | Account |
|---|---|
| Treasuries by CUSIP | `I1:DEBT` |
| Physical longs, listed options, futures | `I1:US_EXCHANGE` (book I has no futures-clearing role; D15) |
| Physical shorts and the IBIT short | `I1:PB_GS` (Goldman PB short financing: GS is the lender; D15) |
| TRS, CDX, CDS, IRS, FX forwards | `I1:PB_GS` (Goldman OTC, ISDA / CSA / PBA of 2026-10-21) |
| Repo (contingency only) | `I1:CREDIT` against `I1:DEBT` face |
| Currency for forward deliveries | the 1.0.6.1 SFI: audited FX desk spot deals in `I1:PB_GS`, the obligation's own account (FX_CCS_PLAN D9) |

The script refuses any payload whose `account_id` does not start with `I1:` and always sends `?book=I`.

**D2. Exposure measures (V3 section 2), shown beside the app's own headline.** Securities at market value (Treasuries at dirty value);
futures at |contracts x multiplier x price|; TRS at reference units x the underlying price; CDX / CDS at notional; IRS at notional; FX
forwards at the USD notional; options at absolute delta-adjusted exposure per strategy, a spread counting each leg's absolute delta (owner,
Q5: "Gross across legs"; B's USD 30M is the sum of its two legs), while the economic net delta uses B's net delta (-9.31M). The app's own headline gross (`risk.py`) leaves IRS, CDX and FX forwards out; the blotter shows both and
says which is which. Economic net delta, beta, DV01, CS01, gamma, vega and financing are reported separately (6.2, V-B) and never netted
into "diversification" without the common-underlying table (3.2).

**D3. SPCX trades as listed** (owner, 19:40 ET, Q1): the USD 15M purchase on `I1:US_EXCHANGE` and the USD 75M receive TRS reference the
observed Nasdaq listing (EDGAR CIK 1181412), labelled as exchange prices; no private or bilateral mark is used.

**D4. VMRK replaces AVB** in both AVB lines (owner, 18:30 ET). The AVB rows stay in the book as SUPERSEDED with the evidence; the script
refuses any AVB order or contract.

**D5. DTCC is not used** (1.3). iTraxx Main has no free source, so line 4.3 is SUPERSEDED: the owner redirected its USD 50M to CDX buy
protection (19:40 ET; the orchestrator's default CDX.NA.IG, the closest analogue of the investment-grade iTraxx Main; he may say HY). It
books as line 4.3a with its own request ids and `client_ref`. No euro proxy is substituted anywhere.

**D6. Options are a speculative profit centre.** Eight standalone strategies (A-H), each with its own thesis, P&L, attribution and exit
criteria. No protective option, no hedge label, no hedge-effectiveness rating. The only written option is the 640 put of strategy B, the
lower strike of a directional debit spread, never sold on its own (5.4.4).

**D7. Release targeting.** Listed sleeves trade on whatever release is live. TRS and CDX book on Fri 10-30 during US hours, after the
Treasuries settle (D13): through the RFQ path if 1.0.6 is live (with `client_ref` if 1.0.6.1 is), else the 1.0.5 direct path; all
idempotent (D17). The FX forwards wait for 1.0.6.1 (EUR on its curves; JPY on the legacy method after the orchestrator's checks, D12); the
IRS wait for 1.0.8's SOFR curve (V-R); the Oracle CDS waits for 1.0.7 and the owner's own level. The script detects the path by capability,
never by assumption (5.3).

**D8. Sizing at submission, from fresh prices.** The 10-28 quantities in section 3 are references. At submission:
- equities / ETFs: whole 100-share lots, `floor(target / price / 100) x 100` (the feasibility rule);
- futures: `round(target / (multiplier x price))`;
- options: contracts from the strategy's delta target on the fresh chain (`|delta| x S x 100` per contract; a spread from the sum of its
  legs' absolute deltas: B = 30,000,000 / ((0.3509 + 0.1847) x 725.61 x 100) = 771.9 -> 772), capped at 25 % of the strike's open
  interest (a plan rule, D6 / V-X);
- Treasuries: `floor(target / dirty / 1,000) x 1,000` face;
- TRS: whole reference units, `floor(target / S)`;
- CDX / CDS / IRS: the notional as given; FX: the USD notional, the EUR amount from the EURUSD mid at booking.

**D9. Labels.** Every figure in the blotter and the reports is **observable** (a print, a chain row, a published fixing, a ledger balance),
**model** (the app's pricing: TRS reset on an observed print, CDX FRED proxy plus the account basis, SOFR futures curve, the 1.0.6.1 FX
curves and the legacy JPY method, CreditGrades, Cboe Greeks, stress repricing) or **proposed** (targets, planning estimates in this document, stress parameters). Every
dealer term is ASSUMED (a Goldman Sachs schedule term, editable). "LIMITED" marks a flat term structure or a stand-in curve.

**D10. Limit orders only.** No market orders. Limit rules per class in 5.5; a line whose fresh price drifted past its gate is held for
review, never chased.

**D11. IRS on a SOFR curve built from free data.** The SR3 strip (Yahoo) gives 2Y and 5Y fully and 10Y to Dec-2031, the remainder on the
Treasury par slope (LIMITED). New IRS books on it with SOFR OIS conventions; the curve is stamped on each contract, so F1's Treasury-par IRS
never changes (V-R in 1.0.8, 6.1). Booking now on the Treasury-par model is the owner's alternative (Q4), not the default.

**D12. FX forwards, by currency (orchestrator, 20:08 ET; V3-C-08).** EUR (14.1-14.3) on the 1.0.6.1 curves (FX_CCS_PLAN D2: the ECB AAA
curve plus a GS spread for EUR, the Treasury curve for USD; MODEL, PROXY labels). JPY (14.4-14.6) on the legacy PB method: one rate per
currency, simple interest, up to 1Y; the JPY rate is the OECD monthly proxy `IRSTCI01JPM156N` (September 2026 average 1.043 %, about 18 bp
below the BoJ's TONA of 1.227 % for 10-27; LIMITED). Before any FX line books, the orchestrator checks the JPY rate's age (1.0.6's rule: a
monthly average at most 62 days after its month, so the September average passes until 2026-12-01) and the pricing path; a failing line
stays UNEXECUTED with the exact reason. The legacy path has no Tokyo calendar ("local holidays not modelled"), so the script sends the JPY
value dates explicitly, each a business day in both Tokyo and New York (its hand table of Japanese national holidays, from the Cabinet
Office list, labelled hand-maintained). Dates follow the real trade date, never revision 1's 10-29 table: for a Fri 10-30 trade, EURUSD spot
11-03 and 2027-02-03 / 05-03 / 11-03; USDJPY spot 11-04 (11-03 is Culture Day) and 2027-02-04 / 05-06 / 11-04 (2027-11-02 if the legacy
1Y limit refuses 11-04); a Mon 11-02 trade is in 3.12. No cross-currency basis (none free, FX_CCS_PLAN D5).

**D13. Liquidity floor; never forced to USD 3B.**
- Unencumbered cash (Treasury plus every I1 account's free cash, less restricted collateral, less reserved Reg T cash) stays at or above
  **USD 150M** (15 % of NAV) after every line;
- liquid resources (unencumbered cash plus repo capacity of settled, unpledged Treasuries at the dealer haircut) stay at or above **1.25x the
  largest call** of the ten V3 scenarios (its same-day calls plus the next-morning Reg T call);
- the script tests both before every line (with the line's need added) and stops the stage before the first line that would breach either,
  reporting the shortfall (Q10).
- **Repo capacity counts only settled, unpledged face (V3-C-01).** The Treasuries bought on 10-29 settle on 10-30, so on 10-29 the capacity
  is 0. Revision 1 booked the TRS and CDX on 10-29; under this rule that stage stops before line 3.4 (NVDA): from cash 434.6M before the
  stage, ORCL leaves 419.6M against 1.25 x 257.1M = 321.3M, META 409.6M against 358.8M, MU 400.6M against 392.6M, NVDA 393.1M against
  420.7M (scenario 8 with the planning shocks of 4.5; SPCX included). So the OTC stage books on Fri 10-30, once the face has settled
  (lowest cover while it books 1.44x; 4.5). The tooling's unit tests reproduce this breach point and the 10-30 pass.
- The rule is never relaxed during a live run; a change needs the owner's word.
- The R0 report and V-S also give the due-time view (4.5, 6.2): the same-day calls against cash plus same-day repo on settled face, then the
  next-morning calls against what is left plus the overnight resources. T+1 sale proceeds and unsettled bonds never meet a same-day call.

**D14. No repo at the opening.** Cash covers every sleeve with about USD 226M to spare; repo stays the contingency line (4.4), available
from 10-30.

**D15. "Distinct accounts" as sub-ledgers (default, Q7).** FinSim1 has no futures-clearing or short-financing account role, and new roles
would have to exist before the futures and shorts are booked (they cannot be moved later). The fund reconciliation (V-B) presents six
sub-ledgers on the existing accounts: Treasury (`I1:TREASURY`); Securities (`I1:US_EXCHANGE` equities, ETFs and options plus `I1:DEBT`);
Short-sale financing (`I1:PB_GS` borrows, collateral, fees, rebates, Reg T); Futures clearing (`I1:US_EXCHANGE` futures IM / VM); FX
(`I1:FOREIGN_FX` plus the forwards' settlement obligations and their SFI deals, which sit in `I1:PB_GS`); Goldman OTC (`I1:PB_GS` contracts, IM, VM, accruals). Real account roles are the
owner's alternative (Q7; a 1.0.9 item, about 6-8 h).

**D16. Risk reporting before the in-app engine.** From the first fills the script's `report` mode (R0) produces the blotter, cash and
collateral, the options book and a first-order stress every evening (5.4.11). The in-app stress engine and blotter arrive in 1.0.8 (6.2).

**D17. Idempotency everywhere** (5.2): deterministic request ids per line and attempt, `client_ref` on OTC contracts from 1.0.6.1, server-side
lookups before every send, a refused request leaves no row (the bus contract), a re-run sends nothing new.

**D18. Large equity tickets as child orders (V3-C-04; Q18).**
- *Why.* The matcher serves orders oldest first (`matcher.py`: `ORDER BY created_ts, order_id`) and the 10 % cap is one per (instrument,
  one-minute bar) across every order, account and book (`orders_rules.fill_cap`, EXEC-6); the rate is one global setting copied onto each
  order when it is submitted, with no per-order field. A single I1 ticket would take the whole cap of a shared name until it is done, and
  any F1 / P1 order the owner enters later on that name (for example covering F1's IBIT short) would get BAR_CAP_USED meanwhile. On 10-28's
  bars that is about 1.17 sessions on IBIT, 2.07 on GXO, and hours on LEN (353 min), VMRK (311), KTOS (281), STT (249), DHI (170), DLR (148),
  NOC (116) and C (78). F1 holds IBIT, VMRK, STT, DLR, C short and GXO, KTOS, NOC, LMT, CRWD, PANW, NOW, SNOW, SPCX, ZS, PDBC, JNJ, NKE,
  PG, TMO, WMT long (1.1). The "F1 / P1 untouched" check of 5.6 proves ledger state, not fill priority.
- *Rule.* Every equity / ETF line whose replay at the cap exceeds 30 minutes (IBIT, GXO, LEN, VMRK, KTOS, STT, DHI, DLR, NOC, C, PDBC, LMT,
  ZS) is worked as sequential child orders, one per foreground pass (5.8: 09:25, 10:15, 11:00, 12:00, 13:00, 14:00, 15:00 and 15:40 ET on
  each session day). A child is sized to the shares the cap would give in half the time to the next pass (10 % x the line's 10-28 day
  volume / 390 x minutes / 2), rounded down to 100 shares, at least 100, never above the remainder. While a child works it takes the whole
  cap (oldest first); once it is done the cap is free until the next pass, and every new child is younger than any F1 / P1 order already
  waiting. A later F1 / P1 order on the name waits at most about half a pass interval (20-30 minutes). Tonight's resting order of such a
  line is its first child (sized for 09:30-10:15 ET).
- *Never* a change to `participation_rate` (an owner setting for every book).
- *Cost.* I1 uses about half of each shared name's cap, so these tickets take about twice as long: IBIT about 2.3 sessions, GXO about 4.1,
  LEN / VMRK / KTOS / STT about 1.3-1.8, DHI / DLR / NOC / C / PDBC inside the first day. Q18 offers single tickets instead.
- Futures and options are not split this way (CME volume dwarfs I1's futures tickets except the thin legs of 3.7; option fills are capped by
  each observation's displayed size, and wide legs go in children no larger than it, 5.4.3). V-X's size-aware option fills (1.0.8) change
  F1 / P1 option fills too; the owner is told before that gate (6.2).

**D19. ITB puts for the XHB remainder (V3-C-03; owner 19:40 ET; Q19).** The ITB chain is a full session stale tonight and its long-dated
series are adjusted (3.9 D'). So: size and price only from a chain whose provider time is in the latest completed session or the current
one, else HELD_STALE; eligible series are standard strikes, or adjusted series once their deliverable is verified from the OCC information
memo and recorded with the line, else HELD_DELIVERABLE; expiries Jan-27 to Jun-27 (Nov-20 only with the owner's explicit OK); the 25 %-of-OI
cap and the wide-market limit; the residual BLOCKED with the numbers, never re-sized onto XHB. Expected: at most about USD 3.7M of the
10.64M.

---

## 3. The opening order book

### 3.0 How to read it

- **Reference prices** are the 10-28 closes (equities, ETFs, options) or the 18:02-18:08 ET Globex prints (futures), as the app quoted them;
  quantities are re-sized from fresh prices at submission (D8). Option quotes are Cboe delayed chains as of 10-28 15:59:59 ET.
- **Status:**
  - **NOW**: executable now, CME Globex is open (trade date 10-29);
  - **NEXT**: executable at the next session: equities / ETFs / options 09:30 ET 10-29, Treasuries 08:00 ET 10-29, CBOT grains 09:30 ET
    10-29 (day session), TRS / CDX 10:00-15:30 ET Fri 10-30 (D13);
  - **BUILD**: needs the named build;
  - **HELD**: waiting for a check or the owner's decision (now only D', the ITB puts);
  - **BLOCKED**: blocked, with the exact reason;
  - **SUPERSEDED**: not submitted, replaced by another line;
  - **UNEXECUTED**: a line whose precondition failed when it was due (the JPY checks, the ORCL level), with the exact reason.
- **Cash / margin** is what the app's preview asked for at the reference price (the app's own numbers), plus the account it comes from.
- Fee schedules: US Exchange equities 0.005 / share, options 0.65 / contract, futures 2.25 / contract / side; GS PB short sales 0.0030 /
  share; Treasuries 0.02 per 100 face.

### 3.1 Sleeve totals against USD 3B (V3 measures, D2)

| Sleeve | V3 target | Sized (10-28) | Executable (listed from 10-29, OTC 10-30) | HELD (check) | BUILD | BLOCKED |
|---|---:|---:|---:|---:|---:|---:|
| Equity TRS | 1,050,000,000.00 | 1,049,997,894.16 | 1,049,997,894.16 | 0.00 | 0.00 | 0.00 |
| CDX and single-name CDS | 300,000,000.00 | 300,000,000.00 | 275,000,000.00 | 0.00 | 25,000,000.00 | 0.00 |
| Interest-rate swaps | 300,000,000.00 | 300,000,000.00 | 0.00 | 0.00 | 300,000,000.00 | 0.00 |
| US Treasury cash bonds | 400,000,000.00 | 399,995,994.68 | 399,995,994.68 | 0.00 | 0.00 | 0.00 |
| Commodity futures | 250,000,000.00 | 249,843,212.50 | 249,843,212.50 | 0.00 | 0.00 | 0.00 |
| Equity index futures | 150,000,000.00 | 150,123,480.00 | 150,123,480.00 | 0.00 | 0.00 | 0.00 |
| Directional options (abs delta-adj; a spread's legs gross) | 150,000,000.00 | 150,004,334.00 | 137,692,577.00 | 3,660,000.00 | 0.00 | 8,651,757.00 |
| Physical long equities | 120,000,000.00 | 120,054,871.00 | 120,054,871.00 | 0.00 | 0.00 | 0.00 |
| Physical short equities | 80,000,000.00 | 79,995,729.00 | 79,995,729.00 | 0.00 | 0.00 | 0.00 |
| Physical IBIT short | 100,000,000.00 | 99,999,222.00 | 99,999,222.00 | 0.00 | 0.00 | 0.00 |
| FX forwards | 100,000,000.00 | 100,000,000.02 | 0.00 | 0.00 | 100,000,000.02 | 0.00 |
| **Total** | **3,000,000,000.00** | **3,000,014,737.36** | **2,562,702,980.34** | **3,660,000.00** | **425,000,000.02** | **8,651,757.00** |

- Gap at the 10-30 close: **437,297,019.66** (14.6 %), if every executable line fills (GXO and the IBIT short need more sessions as child
  orders; D18).
- Gap after every build with the ITB tranche booked: **8,637,019.64** (0.29 %); without it 12,297,019.64 (0.41 %): the blocked lines and
  the ITB check less the sizing residual (-14,737.36).
- "Executable" applies the owner's answers (SPCX as listed, B gross across legs, the I1 CDX basis copied from F1, iTraxx redirected to
  CDX.NA.IG) and VMRK for AVB. "BUILD" counts the ORCL CDS (1.0.7, only at the owner's own level), the FX forwards (1.0.6.1; the JPY half
  only if the orchestrator's checks pass) and the IRS (1.0.8).
- Treasuries are sized at the app's trade-date accrual and the floor rule (3.6), so they come in USD 4,005.32 under target.
- The app's own headline gross (D2) leaves the IRS, CDX / CDS and FX forwards out; with B at 772 spreads its option legs equal the V3
  measure. At full build it reads about 2,288M against the V3 measure's about 2,991M.

### 3.2 Common underlying risk (V3 section 2: offsetting or stacked positions are not independent diversification)

Signed USD exposure (negative = short / bearish), reference values; delta for options (B by its net delta here: it is a net exposure);
executable lines plus builds.

| Underlying risk | Lines that carry it | Net | % of NAV |
|---|---|---:|---:|
| Oracle equity and credit | TRS 3.1 (-150.0M), puts A (-35.0M delta), CDS 4.4 (25M protection at the owner's level, CS01 short credit) | -185.0M equity + CDS | -18.5 % |
| AI / mega-cap Nasdaq | TRS 3.2-3.6 (-390.0M), NQ 11.3 (-49.9M), QQQ spread B (net -9.3M; legs gross 30.0M), PLTR / TSLA shorts (-14.0M); against: CRWD / PANW TRS (+105.0M), cyber / software longs CRWD PANW ZS NOW SNOW (+39.0M) | -319.2M (Oracle apart) | -31.9 % |
| SpaceX (SPCX, listed 2026-06-12) | TRS 3.14 (+75.0M), long 7.2 (+15.0M); the master's beta 3.226 rests on about 95 sessions (the stress uses 1.0 until 250 daily returns exist, V-S) | +90.0M | +9.0 % |
| Data centres (DLR) | TRS 3.7 (-85.0M), short 8.3 (-12.0M), puts E (-8.3M) | -105.3M | -10.5 % |
| US housing | TRS LEN / DHI / VMRK (-185.0M), shorts LEN / DHI / VMRK (-32.0M), XHB puts D (-9.4M), ITB puts D' (HELD; at most -3.7M) | -226.4M (-230.1M with ITB) | -22.6 % |
| Bitcoin | IBIT short 9.1 (-100.0M), IBIT puts C (-25.0M) | -125.0M | -12.5 % |
| WTI crude | CL ladder (-99.9M), USO puts G (-10.0M) | -109.9M | -11.0 % |
| Gold | GC long (+100.0M), GLD calls F (+15.0M) | +115.0M | +11.5 % |
| Broad US equity beta (long) | SPY (+20.0M), ES (+65.2M), RTY (+35.0M), defence / defensive / logistics longs LMT NOC KTOS GXO JNJ TMO PG WMT NKE PDBC (+46.0M), LMT TRS (+60.0M) | +226.2M | +22.6 % |
| US financials | C, STT shorts | -22.0M | -2.2 % |
| Rates (DV01, +1 bp) | Treasuries -165,254; IRS pay fixed +132,576 (Treasury-par figures; the SOFR curve gives similar DV01s); TLT puts H about +8,300 | about -24,400 / bp (net long duration) | |
| Credit (CS01, +1 bp) | CDX IG 150M about +67,000 (4.1 and 4.3a), HY about +54,700, ORCL CDS about +10,000 (planning) | about +131,700 / bp (short credit) | |
| EUR and JPY vs USD | forwards: sell EUR 50M, sell JPY 50M (USD notional) | -100.0M foreign currency | -10.0 % |

**Economic net equity delta** (equities, ETFs, ES / RTY / NQ, TRS, equity options at their net delta; IBIT, gold, oil and TLT apart):
about **-542M** (-54 % of NAV): TRS -570.0M, longs +120.1M, shorts -80.0M, index futures +50.3M, options A + B net + D + E -62.0M; about
-545M with the ITB tranche. The book is deliberately net short equities; the long-equity sleeves do not offset the AI / housing /
data-centre shorts name for name.

### 3.3 Equity TRS with Goldman Sachs (`I1:PB_GS`), USD 1.05B

Terms for every line: 12 months, monthly resets (trade Fri 10-30, D13: first reset 2026-11-30, maturity 12 months on by the app's roll rule), TRS_RESET 16:25 ET at the
official close, nightly ACT/360 financing (payer receives SOFR - 0.60 %, receiver pays SOFR + 0.60 %: the spread goes against the client on
both sides), dividend equivalents on Yahoo ex-dates, daily CTM VM at 17:05 ET, IM 10 % swept from `I1:TREASURY`, cash settlement at each
reset. Reset price at booking: the observed executable mark in the regular session (1.0.6: the accepted RFQ's quoted reset price, a firm
kind). Labels: reset price observable; spread and IM ASSUMED (GS schedule seed). SOFR 3.95 % (FRED, 10-27).

| Line | Underlying | Side | Units at 10-28 close | Notional | IM | Financing / night (+ received) | Dividend equivalents / yr | Status |
|---|---|---|---|---:|---:|---:|---:|---|
| 3.1 | ORCL | PAY (short) | 1,037,775 @ 144.54 | 149,999,998.50 | 14,999,999.85 | +13,958.33 | -2,075,550.00 (0.50 / qtr) | NEXT |
| 3.2 | META | PAY | 163,267 @ 612.49 | 99,999,404.83 | 9,999,940.48 | +9,305.50 | -342,860.70 (0.525 / qtr) | NEXT |
| 3.3 | MU | PAY | 91,535 @ 983.23 | 89,999,958.05 | 8,999,995.80 | +8,375.00 | -54,921.00 (0.15 / qtr run-rate) | NEXT |
| 3.4 | NVDA | PAY | 348,221 @ 215.38 | 74,999,838.98 | 7,499,983.90 | +6,979.15 | -348,221.00 (0.25 / qtr) | NEXT |
| 3.5 | AVGO | PAY | 213,183 @ 351.81 | 74,999,911.23 | 7,499,991.12 | +6,979.16 | -554,275.80 (0.65 / qtr) | NEXT |
| 3.6 | AMZN | PAY | 196,386 @ 254.60 | 49,999,875.60 | 4,999,987.56 | +4,652.77 | 0.00 | NEXT |
| 3.7 | DLR | PAY | 472,826 @ 179.77 | 84,999,930.02 | 8,499,993.00 | +7,909.72 | -2,307,390.88 (1.22 / qtr) | NEXT (10-30, after DLR's 10-29 Q3 print) |
| 3.8 | AVB | PAY | none (r105 offered 353,145 @ 184.06, the 2026-08-14 close) | - | - | - | - | SUPERSEDED (delisted 2026-08-17; D4) |
| 3.8a | VMRK | PAY | 1,067,323 @ 60.90 | 64,999,970.70 | 6,499,997.07 | +6,048.61 | -3,001,312.00 (0.703 / qtr) | NEXT |
| 3.9 | LEN | PAY | 718,993 @ 83.45 | 59,999,965.85 | 5,999,996.58 | +5,583.33 | -1,437,986.00 (0.50 / qtr) | NEXT |
| 3.10 | DHI | PAY | 419,580 @ 143.00 | 59,999,940.00 | 5,999,994.00 | +5,583.33 | -755,244.00 (0.45 / qtr) | NEXT |
| 3.11 | CRWD | RECEIVE (long) | 225,005 @ 266.66 | 59,999,833.30 | 5,999,983.33 | -7,583.31 | 0.00 | NEXT |
| 3.12 | PANW | RECEIVE | 114,071 @ 394.49 | 44,999,868.79 | 4,499,986.88 | -5,687.48 | 0.00 | NEXT |
| 3.13 | LMT | RECEIVE | 95,275 @ 629.75 | 59,999,431.25 | 5,999,943.12 | -7,583.26 | +1,314,795.00 (3.45 / qtr) | NEXT |
| 3.14 | SPCX | RECEIVE | 516,422 @ 145.23 | 74,999,967.06 | 7,499,996.71 | -9,479.16 | 0.00 | NEXT (Q1: as listed) |
| | **Total** | short 809,998,793.76 / long 239,999,100.40 | | **1,049,997,894.16** | **104,999,789.40** | **+45,041.69** | **-9,562,966.38** | |
| | Without SPCX (revision 1, for comparison) | | | 974,997,927.10 | 97,499,792.69 | +54,520.85 | | |

Notes: ORCL rose 10.7 % on 10-28 (Investor Day; close 144.54); the Fund separately pays the total return on 38,700 + 11,300 ORCL (its own
book). Financing figures are indicative at SOFR 3.95 %; the ledger accrues the published fixing each night.

### 3.4 Credit derivatives with Goldman Sachs (`I1:PB_GS`), USD 300M buy protection

Trade date Fri 10-30 (D13). The I1 CDX basis copies F1's (owner, Q3): IG -24.5 bp, HY +23.0 bp, labelled ASSUMED; the half-spreads and IM
equal the GS seed, so only the basis is written (5.4.7). Accrued rebate = N x C x (T + 1 - accrual start) / 360 (`swaps_cds.rebate_c`):
accrual start 2026-09-21, so 40 days for a 10-30 trade.

| Line | Contract | Notional | Price source and label | Initial payment (trade 10-30, paid T+1 = 11-02) | IM | CS01 / running premium | Status |
|---|---|---:|---|---|---:|---|---|
| 4.1 | CDX.NA.IG S47 V1 5Y, maturity 2031-12-20, 100 bp coupon, recovery 40 % | USD 100,000,000 | model: FRED `BAMLC0A0CM` + the I1 basis -24.5 bp + GS half-spread 0.50 bp = dealt about 64 bp (0.88 % on 10-27) | about +1.73M received (upfront about 1.62M + rebate 111,111.11) | 2,000,000.00 (2 %) | about +44,600 / bp; 100 bp running about 1.01M / yr; first coupon 2026-12-21 | NEXT (10-30), after the basis term (5.4.7) |
| 4.2 | CDX.NA.HY S47 V1 5Y, 2031-12-20, 500 bp coupon, recovery 30 % (the app's assumption, labelled) | USD 125,000,000 | model: FRED `BAMLH0A0HYM2` + basis +23.0 bp + half-spread 2.0 bp = about 343 bp | about +8.66M received (upfront about 7.97M + rebate 694,444.44) | 7,500,000.00 (6 %) | about +54,700 / bp; 500 bp running about 6.34M / yr | NEXT (10-30), after the basis term |
| 4.3 | iTraxx Europe Main S46 5Y (EUR, about EUR 44.8M) | USD 50,000,000 | none: no free source (DTCC refused by its terms; FRED has no euro IG OAS); the app refuses it (400 TERMS "index must be IG or HY") | - | - | - | **SUPERSEDED** by 4.3a (owner, 19:40 ET) |
| 4.3a | CDX.NA.IG S47 V1 5Y, 2031-12-20, 100 bp (the iTraxx redirect; the owner may say HY instead) | USD 50,000,000 | as 4.1 (the same I1 basis and half-spread) | about +0.86M received (upfront about 0.81M + rebate 55,555.56) | 1,000,000.00 (2 %) | about +22,300 / bp; 100 bp running about 0.51M / yr | NEXT (10-30); its own request ids and `client_ref` V3I-1028-4.3a; on 1.0.5 its signature differs from 4.1 by notional |
| 4.4 | Oracle Corp senior unsecured 5Y CDS, maturity 2031-12-20, XR14, coupon by the CDS plan's bucket rule (100 bp for Oracle: ICE lists it at 100 bp only), recovery 40 % | USD 25,000,000 | **the owner's own negotiated mid only** (orchestrator 20:08 ET; CDS plan 9.5 step 7 and its dealing-level guard): the CreditGrades MODEL spread (about 113-116 bp, AAA floor) is shown beside it, labelled MODEL, and is never the dealing level; public news put Oracle's 5Y CDS at 295-315 bp on 10-19 | per the owner's level at booking (T+1) | GS tier BBB 5 % = 1.25M or BB 8 % = 2.0M | CS01 about +10,000 / bp (planning) | **BUILD** 1.0.7 + the owner's level (Q12); without a level it stays UNEXECUTED with that reason |

**Reference obligation for 4.4** (V3 section 4: "validate ... reference obligations"): Oracle Corporation 4.800 % Notes due 2032-09-26,
CUSIP 68389XDK8 / ISIN US68389XDK81, USD 3,000,000,000, issued 2025-09-26; senior unsecured (the prospectus supplement says the notes
rank equal in right of payment with Oracle's USD 91.3 billion of senior unsecured borrowings). Sources: SEC EDGAR, Oracle (CIK 1341439),
final pricing term sheet FWP of 2025-09-24 (accession 0001193125-25-215807) and prospectus supplement 424B2 of 2025-09-25 (accession
0001193125-25-217490). Labelled **"EDGAR-identified, not the ISDA standard reference obligation"** (the SRO list is not a verified free
source). The 1.0.7 contract has no reference-obligation field, so the line records it in `order_plan.json`, the R0 blotter and the 1.0.8
mandate line; the contract itself references the entity's senior unsecured level (CDS plan 2.1).

The upfronts above are planning figures (proposed) from the app's own RPV01s of 10-28 (IG 4.44 at 88.5 bp, HY 4.12 at 320 bp) adjusted to
the dealt spreads; the app computes the exact amounts at booking. Index series S47 / V1 and the 2031-12-20 maturity are the app's own
on-the-run rule (roll 2026-09-21, next roll 2027-03-22). The 125 / 100 legal reference entities of the two indices are not validated (the
app holds no index annex; labelled), as V3 section 4 asks to disclose.

### 3.5 Interest-rate swaps with Goldman Sachs (`I1:PB_GS`), USD 300M pay fixed

| Line | Tenor | Notional | Today's app (Treasury-par model, MODEL - LIMITED) | After the build (D11) | Status |
|---|---|---:|---|---|---|
| 5.1 | 2Y | 100,000,000 | par 4.670 %, dealt 4.680 %; annuity 1.8909; DV01 +18,908.57; IM 945,428.50 | SOFR futures curve (2Y fully from the SR3 strip); annual ACT/360 fixed vs compounded SOFR; spot start T+2 | BUILD (V-R, 1.0.8); book after its deploy (Q4) |
| 5.2 | 5Y | 125,000,000 | par 4.850 %, dealt 4.860 %; annuity 4.4016; DV01 +55,020.25; IM 2,751,012.50 | 5Y fully from the strip (convexity adjusted, its vol term ASSUMED) | BUILD |
| 5.3 | 10Y | 75,000,000 | par 5.050 %, dealt 5.060 %; annuity 7.8196; DV01 +58,647.21; IM 2,932,360.50 | strip to Dec-2031, Treasury-par slope beyond (LIMITED) | BUILD |

- These are directional pay-fixed trades (profit if swap rates rise). The Treasuries do not make them hedges; the net DV01 is in 3.2.
- IM stays |DV01| x 50 bp (the GS schedule, ASSUMED); daily CTM VM; no upfront; the fixed payer's carry is fixed minus compounded SOFR.
- Why not book them now: the app's quote is a Treasury-par rate with a semi-annual fixed leg and a T+0 start, not a SOFR swap rate. The free
  SR3 strip puts 2Y and 5Y SOFR swap rates near 4.5 % (1.6), so a payer at 4.68 % / 4.86 % would knowingly deal 15-35 bp off the observable
  futures-implied level. Q4 lets the owner book now anyway (labelled MODEL - LIMITED).

### 3.6 US Treasury cash bonds by CUSIP (`I1:DEBT`), USD 400M at market value

Price: TreasuryDirect FedInvest 10-28 (buy / sell; the app's mark is the mid, quality close) plus the app's ASSUMED dealer half-spread.
**Accrued interest is the app's: to the TRADE date** (`products\bonds.py`: "Accrued is that of the TRADE date ... real settlement is one day
later, a stated simplification"; the ticket computes `accrued_per_100(terms, tdate)`). The table is therefore re-stated for a 10-29 trade
(one more day than the 10-28 previews; settlement T+1 on 10-30). Face = `floor(target / dirty / 1,000) x 1,000` (D8, 5.4.5), so the tooling's
tested sizing reproduces it. All six are in FinSim1's Treasury master (fiscaldata MSPD 2026-09-30), support FULL.

| Line | CUSIP | Coupon / maturity | Face | Dealt clean (mid + half-spread) | Accrued / 100 (to 10-29) | Dirty | Pay (incl. fee; trade 10-29, settles 10-30) | Yield (10-28) | DV01 | Of the issue |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| 6.1 | 912810FT0 | 4.500 % 2036-02-15 | 111,866,000 | 97.414062 (97.382812 + 0.03125) | 0.9171 | 98.331162 | 110,021,510.89 | 4.848 % | -81,606.60 | 0.42 % of 26,397M |
| 6.2 | 912810FP8 | 5.375 % 2031-02-15 | 82,397,000 | 102.0625 (102.046875 + 0.015625) | 1.0954 | 103.157900 | 85,015,494.26 | 4.836 % | -32,026.93 | 0.50 % of 16,428M |
| 6.3 | 912810FM5 | 6.250 % 2030-05-15 | 65,229,000 | 104.476563 (104.460938 + 0.015625) | 2.8363 | 107.312863 | 70,012,153.21 | 4.860 % | -21,589.33 | 0.38 % of 17,043M |
| 6.4 | 912810FJ2 | 6.125 % 2029-08-15 | 52,577,000 | 103.359375 (103.34375 + 0.015625) | 1.2483 | 104.607675 | 55,010,092.68 | 4.824 % | -13,897.55 | 0.47 % of 11,179M |
| 6.5 | 912810FG8 | 5.250 % 2029-02-15 | 44,053,000 | 101.078125 (101.0625 + 0.015625) | 1.0700 | 102.148125 | 45,008,124.11 | 4.746 % | -9,556.43 | 0.39 % of 11,350M |
| 6.6 | 912810FF0 | 5.250 % 2028-11-15 | 33,888,000 | 100.898437 (100.882812 + 0.015625) | 2.3825 | 103.280937 | 35,006,621.53 | 4.783 % | -6,577.21 | 0.31 % of 10,947M |
| | **Total** | | **390,010,000** | | | value **399,995,994.68** | **400,073,996.68** (fees 78,002.00) | | **-165,254.05** | |

- Status NEXT: the bond ticket trades 08:00-17:00 ET (tonight's previews answer SESSION_CLOSED "reopens 08:00 ET Thu 29-Oct" but compute
  everything). Booked 10-29, settled T+1 on 10-30; the mark is FedInvest 10-28 shifted by Yahoo's yield move until FedInvest 10-29 posts
  (about 13:00 ET). The script re-sizes from the live preview (5.4.5); the table is the reference.
- Accrued convention, disclosed: the app charges accrued to the trade date. A street settlement on 10-30 would charge one more day: about
  USD 56,650 more on these faces (USD 113,300 more than the 10-28 previews in all). The app's figure is what the ledger books.
- Revision 1's faces were rounded, not floored: five of six were 1,000 above the rule (6.1 111,881,000 against the rule's 111,880,000 at
  the 10-28 dirty price; 6.2, 6.4, 6.5, 6.6 likewise).
- Fill: the app fills the whole face at once at mark + half-spread (no volume cap, no size-dependent spread; disclosed: a real dealer would
  quote these off-the-run clips wider).
- Income-generating investments and eligible collateral (repo 2 % haircut; GS CSA 4 % from 1.0.6). Not designated as protection for any
  sleeve. Repo capacity exists only once they settle (10-30): D13.

### 3.7 Commodity futures (`I1:US_EXCHANGE`), USD 250M

Quotes: Yahoo chart, up to 15 min delayed, last trade only. IM and maintenance: the app's static table ("verify against the exchange";
ASSUMED). Daily VM in cash at the observed settle print. The app's FUTURES_FINAL closes at the observed settle print: no physical delivery
and no first-notice logic beyond the roll date, so every physically delivered month is closed or rolled before its roll date (schedule, 7).

| Line | Contract | Side | Contracts | Last (10-28) | Notional | IM | Maintenance | Fees | Replay at 10 % (10-28 bars) | Close or roll by |
|---|---|---|---:|---|---:|---:|---:|---:|---|---|
| 10.1 | GCZ26 COMEX gold, 100 oz | BUY | 245 | 4,079.6 (18:08 ET) | 99,950,200.00 | 2,940,000.00 | 2,670,500.00 | 551.25 | about 1.4 h | 11-25 into GCG27 (app roll 11-28; first notice 11-30) |
| 10.2 | CLZ26 NYMEX WTI, 1,000 bbl | SELL | 245 | 101.95 | 24,977,750.00 | 1,592,500.00 | 1,445,500.00 | 551.25 | about 1.5 h | 11-12 (app roll 11-13; last trade 11-20) |
| 10.3 | CLF27 | SELL | 249 | 100.21 | 24,952,290.00 | 1,618,500.00 | 1,469,100.00 | 560.25 | about 3.6 h | 12-11 (roll 12-14) |
| 10.4 | CLG27 | SELL | 254 | 98.24 | 24,952,960.00 | 1,651,000.00 | 1,498,600.00 | 571.50 | about 3.9 h | 2027-01-12 |
| 10.5 | CLH27 | SELL | 260 | 96.13 | 24,993,800.00 | 1,690,000.00 | 1,534,000.00 | 585.00 | about 4.0 h | 2027-02-12 |
| 10.6 | HGZ26 COMEX copper, 25,000 lb | BUY | 150 | 6.676 | 25,035,000.00 | 900,000.00 | 825,000.00 | 337.50 | about 2.5 h | 11-25 into HGH27 |
| 10.7 | SIZ26 COMEX silver, 5,000 oz | BUY | 23 | 54.085 | 6,219,775.00 | 253,000.00 | 230,000.00 | 51.75 | about 0.4 h | 11-25 into SIH27 |
| 10.8 | ZCZ26 CBOT corn, USD 50 / point | BUY | 277 | 451.25 (14:19 ET day session) | 6,249,812.50 | 415,500.00 | 387,800.00 | 623.25 | about 3.1 h (day session, from 09:30 ET 10-29) | 11-25 |
| 10.9 | ZSF27 CBOT soybeans | BUY | 101 | 1,237 | 6,246,850.00 | 282,800.00 | 252,500.00 | 227.25 | about 2.1 h (day session) | 12-28 (roll 12-29) |
| 10.10 | ZWZ26 CBOT wheat | BUY | 209 | 599.5 | 6,264,775.00 | 459,800.00 | 418,000.00 | 470.25 | about 3.4 h (day session) | 11-25 |
| | **Total** | | | | **249,843,212.50** | **11,803,100.00** | **10,731,000.00** | **4,529.25** | | |

- Theses: gold a long-term bullish thesis (10.1; alternative GCG27, thinner: 243 contracts at 4,115.0, about 9.8 h replay); WTI a
  shorter-term bearish thesis, laddered over four months (10.2-10.5).
- The WTI curve is backwardated (Z26 101.95 > F27 100.21 > G27 98.24 > H27 96.13): the deferred legs are sold 1.7-5.7 % below the front, and
  with an unchanged curve each leg drifts up toward the front price as it nears expiry - a carry drag on the shorts (disclosed). The master
  has no CL month after Mar-27.
- The non-oil basket is four equal legs (silver, corn, soybeans, wheat; natural gas left out as energy). The master has no other non-oil
  roots (cattle, hogs, softs, PGMs: a master build, deferred).
- Status NOW for 10.1-10.7 (Globex open since 18:00 ET, trade date 10-29; a DAY order runs to 17:00 ET 10-29). **10.8-10.10 are sent at
  09:30 ET 10-29, in the CBOT day session only** (V3-C-07): CBOT grains have two sessions per trade date (`sessions.py`: night 20:00-08:45,
  day 09:30-14:20) and a DAY order ends at the close of its current session, so an order sent tonight would lapse at 08:45 ET. Unfilled
  grain quantity is re-sent in the next day session. HG and the NQ line below previewed NOT_MARKETABLE at their reference limits: the
  marketable-limit rule of 5.5 applies to every futures line.
- **Overnight liquidity.** The replay column was measured on 10-28's one-minute bars, mostly the day session. The night is thin: CLH27
  traded in 15 of 124 one-minute bars from 18:00 to 20:03 ET on 10-28 (155 lots, Yahoo). The deferred CL legs (10.3-10.5) will fill mainly
  in the 10-29 day session. The app's cap takes at least one lot from any bar with volume (`fill_cap`: `max(lot, floor(10 % x volume))`),
  so in a 1-lot bar I1 takes 100 %: a disclosed limit of the fill model, not changed in 1.0.8 (it would change F1 / P1 fills too).

### 3.8 Equity index futures (`I1:US_EXCHANGE`), USD 150M

| Line | Contract | Side | Contracts | Last (10-28) | Notional | IM | Maintenance | Fees | Replay | Roll by |
|---|---|---|---:|---|---:|---:|---:|---:|---|---|
| 11.1 | ESZ26 CME E-mini S&P 500, USD 50 x index | BUY | 168 | 7,757.25 | 65,160,900.00 | 2,940,000.00 | 2,671,200.00 | 378.00 | about 0.3 h | 12-10 into ESH27 (app roll 12-11, expiry 12-18) |
| 11.2 | RTYZ26 CME E-mini Russell 2000, USD 50 x index | BUY | 246 | 2,847.8 | 35,027,940.00 | 2,214,000.00 | 2,017,200.00 | 553.50 | about 3.4 h | 12-10 into RTYH27 |
| 11.3 | NQZ26 CME E-mini Nasdaq-100, USD 20 x index | SELL | 84 | 29,723 | 49,934,640.00 | 2,184,000.00 | 1,982,400.00 | 189.00 | about 0.25 h | 12-10 into NQH27 |
| | **Total** | | | | **150,123,480.00** | **7,338,000.00** | **6,670,800.00** | **1,120.50** | | |

All NOW. ES is kept apart from SPY and the TRS (V3 section 2); the NQ short shares the direction of strategy B and the AI TRS shorts (3.2), it
is not a hedge.

### 3.9 The Directional Options Book (`I1:US_EXCHANGE`), USD 150M absolute delta-adjusted

Eight standalone speculative strategies. None is protection for another sleeve; none carries a hedge label; each has its own P&L,
attribution and exit criteria (D6). All option contracts are materialised from the cached Cboe chain on preview / submit (x100, American).
Quotes: Cboe delayed chain as of 10-28 15:59:59 ET (quality close); Greeks per share from Cboe; dollar delta = delta x S x 100 x contracts;
dollar gamma per 1 % = gamma x S^2 / 100 x 100 x contracts; vega per vol point; theta per calendar day. Underlying marks: Yahoo 10-28 closes.
"Leg BE" is the leg's own break-even at expiry. Expiry P&L tables include fees, each leg at its own expiry.

#### A. ORCL bearish puts: USD 35.0M delta (NEXT)

**Thesis.** AI-capex and financing strain at Oracle. Oracle's 10-28 Investor Day (EDGAR 8-K, Ex. 99.1) guides positive free cash flow only
from 2H FY28 and up to USD 20B more capital raises through FY30; the stock jumped 10.7 % to 144.54 on the day (IV about 57 %). Profit
drivers: an ORCL decline toward 120-130 before March. Catalysts: FQ2 earnings 2026-12-14 (Yahoo calendarEvents, not an estimate), inside every
chosen expiry; FOMC 12-08/09. Not protection for the ORCL TRS: same direction, separate P&L.

| Line | Contract | BTO | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg BE | Delta-adj USD | Premium + fees = cash |
|---|---|---:|---|---:|---:|---|---|---:|---:|
| 12A.1 | ORCL 2027-03-19 140 P | 2,707 | 16.55 / 17.30 / 16.925 (4.4 %) | 57.1 % | -0.3833 | 19.2 % of 14,095 | 122.70 (-15.1 %) | -14,997,371 | 4,683,110.00 + 1,759.55 = 4,684,869.55 |
| 12A.2 | ORCL 2027-03-19 130 P | 2,254 | 11.80 / 13.00 / 12.40 (9.7 %) | 57.8 % | -0.3069 | 17.7 % of 12,749 | 117.00 (-19.1 %) | -9,998,592 | 2,930,200.00 + 1,465.10 = 2,931,665.10 |
| 12A.3 | ORCL 2027-01-15 140 P | 1,769 | 12.00 / 12.90 / 12.45 (7.2 %) | 57.3 % | -0.3912 | 10.7 % of 16,530 | 127.10 (-12.1 %) | -10,002,642 | 2,282,010.00 + 1,149.85 = 2,283,159.85 |

- **Strategy:** premium and fees 9,899,694.50 = maximum loss; delta -34,998,605; gamma +1,113,945 per 1 %; vega +209,308 per vol point;
  theta -48,392 a day.
- **Alternatives compared** (ask / delta / IV / theta / BE / OI / premium per USD 1M of delta): Dec-26 140 P 10.75 / -0.392 / 60.7 % /
  -0.1256 / 129.25 / 18,086 / 190k; Jan-27 140 P 12.90 / -0.391 / 57.3 % / -0.0931 / 127.10 / 16,530 / 228k; Mar-27 140 P 17.30 / -0.383 /
  57.1 % / -0.0663 / 122.70 / 14,095 / 312k; Jun-27 140 P 22.00 / -0.370 / 56.6 % / -0.0512 / 118.00 / 9,454 / 412k; Jan-27 130 P 8.35 /
  -0.291 / 57.7 % / -0.0837 / 121.65 / 17,874 / 198k; Mar-27 130 P 13.00 / -0.307 / 57.8 % / -0.0620 / 117.00 / 12,749 / 293k; Jun-27 130 P
  17.00 / -0.309 / 56.0 % / -0.0482 / 113.00 / 9,286 / 381k. Chosen: the March near-the-money and 10 % OTM strikes for time past the December
  print, the January 140 as the cheaper earnings-window leg; every leg at or below 20 % of its strike's open interest.
- **Expiry P&L:** ORCL 100: +14.77M; 115: +4.67M; 125: -2.06M; 130: -5.42M; 140: -9.90M; 144.54: -9.90M; 160: -9.90M.
- **Exit:** take profit at ORCL at or below 120 or strategy value at or above 2x premium; cut at 50 % of premium lost or an ORCL close above
  165; close or roll the January leg by 2027-01-08.
- **Fill pacing (app):** the displayed ask size per chain refresh (about 90 s): 78 / 75 / 84 at the close, about 0.6-0.9 h per leg (closing
  sizes understate intraday depth).

#### B. QQQ bearish put debit spread: USD 30.0M delta, gross across legs (NEXT)

**Thesis.** A standalone bearish Nasdaq-100 trade: a 5-12 % QQQ decline by March on AI-capex digestion and rich valuations. Catalysts: the
remaining mega-cap earnings and FOMC 12-08/09 (with projections). A directional return trade, not insurance.

**Structure.** Buy the Mar-27 700 P (3.5 % OTM), sell the Mar-27 640 P (11.8 % OTM), same expiry, **772 spreads**. Owner, 19:40 ET (Q5):
"Gross across legs": the USD 30M is the sum of both legs' absolute delta: 30,000,000 / ((0.3509 + 0.1847) x 725.61 x 100) = 771.9 -> 772.

| Line | Contract | Side / qty | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg delta USD | Cash |
|---|---|---|---|---:|---:|---|---:|---:|
| 12B.1 | QQQ 2027-03-19 700 P | BTO 772 | 25.75 / 26.41 / 26.08 (2.5 %) | 23.1 % | -0.3509 | 4.5 % of 17,011 | -19,656,398 | pays 2,038,852.00 + 501.80 = 2,039,353.80 |
| 12B.2 | QQQ 2027-03-19 640 P | STO 772 | 12.82 / 13.23 / 13.025 (3.1 %) | 26.9 % | -0.1847 | 6.9 % of 11,195 | +10,346,357 | receives 989,704.00 - 501.80 = 989,202.20 |

- **Economics:** net debit 13.59 = 1,049,148.00 + fees 1,003.60 = maximum loss 1,050,151.60; maximum profit (60 - 13.59) x 100 x 772 =
  3,582,852.00 (3,581,848.40 after fees), 3.4 : 1; break-even 686.41 (-5.4 %); option margin 0 once the 700 P is held (vertical spread
  covered, the app's strategy preview).
- **Greeks:** the V3 measure (legs gross) 30,002,754; the economic net delta -9,310,041 at the mark 725.61 (the app's strategy preview takes
  spot from the chain's stale underlying field, 718.85: a V-X fix); gamma +609,698 per 1 %; vega +35,072; theta -1,807 a day.
- **Alternatives** (per spread): Jan-27 700 / 640 net 11.03 (17.98 ask - 6.95 bid), BE 688.97; Dec-26 700 / 640 net 9.27, BE 690.73;
  Mar-27 710 / 650 net 15.27, BE 694.73. Chosen: March, for time.
- **Expiry P&L:** QQQ 580 or 640: +3.58M; 660: +2.04M; 686.41: about 0; 700 / 725.61 / 780: -1.05M.
- **Exit:** take profit at a spread value at or above 45 (75 % of the width) or QQQ at or below 650; cut at 50 % of the debit or QQQ above
  760; close before 2027-03-12 (the short leg is American; the app does not simulate early assignment).
- **Execution (5.4.4):** legged, long first. As one AON package, 772 lots would still not fill in this app (every leg's displayed size must
  cover the remaining quantity in one observation; sizes were 1 and 46 at the close). The 640 P is sent only for the 700 P quantity already
  held, at the bid, so it is never naked. The realised net debit is reported against 13.59 (legging risk, Q14).

#### C. IBIT bearish puts: USD 25.0M delta (NEXT)

**Thesis.** A Bitcoin decline and a contraction of speculative risk appetite, through listed IBIT puts. Not protection for the USD 100M IBIT
short; no calls. **IV check:** Jan-27 45 P 37.6 %, Jun-27 45 P 40.5 % against IBIT's 30-day IV 36.6 % (Cboe iv30): a moderate, not extreme,
crypto premium.

| Line | Contract | BTO | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg BE | Delta-adj USD | Premium + fees = cash |
|---|---|---:|---|---:|---:|---|---|---:|---:|
| 12C.1 | IBIT 2027-01-15 45 P | 6,775 | 2.45 / 2.57 / 2.51 (4.8 %) | 37.6 % | -0.3999 | 23.7 % of 28,630 | 42.43 (-8.0 %) | -12,500,814 | 1,741,175.00 + 4,403.75 = 1,745,578.75 |
| 12C.2 | IBIT 2027-06-17 45 P | 7,031 | 4.60 / 4.90 / 4.75 (6.3 %) | 40.5 % | -0.3853 | 21.1 % of 33,283 | 40.10 (-13.1 %) | -12,499,530 | 3,445,190.00 + 4,570.15 = 3,449,760.15 |

- **Strategy:** maximum loss 5,195,338.90; delta -25,000,344; gamma +1,094,802 per 1 %; vega +153,836; theta -22,196 a day.
- **Alternatives** (ask / delta / IV / theta / BE / OI / premium per USD 1M delta): Dec-26 45 P 1.99 / -0.398 / 37.5 % / -0.0246 / 43.01 / 8,318
  / 108k; Jan-27 45 P 2.57 / -0.400 / 37.6 % / -0.0201 / 42.43 / 28,630 / 139k; Mar-27 45 P 3.70 / -0.397 / 39.2 % / -0.0151 / 41.30 / 1,811 /
  202k (skipped: open interest); Jun-27 45 P 4.90 / -0.385 / 40.5 % / -0.0122 / 40.10 / 33,283 / 276k; Jun-27 40 P 2.91 / -0.258 / 42.1 % /
  -0.0107 / 37.09 / 30,194 / 245k.
- **Expiry P&L:** IBIT 30: +15.51M; 35: +8.61M; 40: +1.71M; 42: -1.05M; 45 / 46.14 / 55: -5.20M.
- **Exit:** take profit at IBIT at or below 38 or 2.5x premium; cut at 50 % of premium or IBIT above 55; close or roll the January leg by
  2027-01-08.
- **Liquidity:** displayed ask 18 and 40 at the close (about 9.4 h and 4.4 h of refreshes at those sizes); the June 45 P traded 2 contracts
  on 10-28.

#### D. XHB bearish puts: USD 9.36M of the USD 20M delta executable (NEXT; the remainder goes to ITB, D')

**Thesis.** Housing slowdown: affordability pressure, weaker homebuilder margins, valuation compression. Independent of the DHI / LEN
shorts (their common risk is in 3.2). Symbol confirmed: XHB = SPDR S&P Homebuilders ETF (master FULL, Cboe chain).

**Liquidity fails at full size.** The most open strike (Jan-27 100 P) has OI 5,962; USD 20M of delta is 4,139 of those contracts, 69 % of
it. The 10-28 closing markets were 38-180 % of mid wide and XHB puts trade tens of contracts a day per strike. The app's matcher would still
fill against the displayed size at every refresh (it is never depleted): an impossible fill. So the plan takes a tranche of at most 25 % of
each strike's open interest:

| Line | Contract | BTO | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg BE | Delta-adj USD | Premium + fees at the ask |
|---|---|---:|---|---:|---:|---|---|---:|---:|
| 12D.1 | XHB 2027-01-15 100 P | 1,490 | 5.50 / 8.10 / 6.80 (38.2 %) | 35.6 % | -0.4932 | 25.0 % of 5,962 | 91.90 (-6.2 %) | -7,199,502 | 1,206,900.00 + 968.50 = 1,207,868.50 |
| 12D.2 | XHB 2026-12-18 96 P | 341 | 1.91 / 5.00 / 3.455 (89.4 %) | 34.6 % | -0.3650 | 25.0 % of 1,365 | 91.00 (-7.1 %) | -1,219,384 | 170,500.00 + 221.65 = 170,721.65 |
| 12D.3 | XHB 2027-03-19 90 P | 357 | 2.33 / 5.00 / 3.665 (72.9 %) | 32.6 % | -0.2698 | 24.9 % of 1,431 | 85.00 (-13.2 %) | -943,633 | 178,500.00 + 232.05 = 178,732.05 |
| 12D.4 | the remainder: about 2,202 more Jan-27 100 P equivalents (not sized) | - | - | - | - | - | - | -10,637,481 | **SUPERSEDED**: redirected to ITB puts (owner, 19:40 ET; D') |

- **Tranche:** delta -9,362,519; premium 1.56M at the ask (1.26M at the mid); maximum loss 1,557,322.20 at the ask; gamma +586,402; vega
  +39,082; theta -7,696 a day. The remainder goes to ITB puts under D19 (D' below).
- **Expiry P&L (tranche):** XHB 75: +3.42M; 85: +1.23M; 90: +0.14M; 95: -0.78M; 97.97: -1.25M; 110: -1.56M.
- **Exit:** take profit at XHB at or below 88; cut at 50 % of premium or XHB above 108.
- **Limits:** the wide-market rule of 5.5 (mid plus a quarter of the spread, re-evaluated each pass), never the closing ask; fills uncertain.

#### D'. ITB bearish puts, the XHB remainder (owner 19:40 ET): HELD; at most about USD 3.7M of USD 10.64M of delta

**Thesis.** The same housing view as D (a housing slowdown, weaker homebuilder margins, valuation compression), through the iShares U.S.
Home Construction ETF (ITB: master ETF, ACTIVE, Cboe chain). The owner redirected D's remainder (USD 10,637,481 of delta) "into ITB puts
where the chain is liquid". It stacks on the homebuilder TRS, shorts and XHB puts (3.2).

**What the chain shows (checked 20:15 ET, Cboe delayed JSON, the terminal's honest agent).**
- **It is a full session stale.** Provider time 2026-10-28 10:02:14 UTC (06:02 ET, before the open), spot 90.35 with last trade
  2026-10-27 15:59:59: the 10-27 close. ITB closed 10-28 at 88.99 (Yahoo, 16:00 ET), 1.5 % lower. Outside the session the app grades any
  chain "close" (`options.chain_freshness` returns "close" when the option session is shut), so tonight's previews would size and price ITB
  from 10-27 data; the 5 % drift gate would not catch a 1.5 % gap. In the session the app refuses a stale chain.
- **The long-dated series are adjusted.** Every Jan-27-and-later put with open interest has a non-standard strike (x.46 or x.47: 80.46,
  82.46, 86.46, 88.47, 99.46, 109.46 ...). Their deliverable is not in the data the app holds, and the app books every contract at 100
  shares. Standard long-dated strikes have about no open interest in the useful range (Jan-27 70 P: OI 1,042 at delta -0.07; 75 P: OI 55).
- **Thin.** Volume 0-15 a strike on 10-27; markets 13-82 % of mid wide on the adjusted Jan-27 puts.

**Reach at 25 % of open interest, |delta| 0.20-0.45 (planning, on the stale chain at its spot):**

| Set | Series | Delta at 25 % of OI (USD) |
|---|---|---:|
| Adjusted Jan-27, Apr-27, Jun-27 (inside the app's 400-day chain window) | 19 series; the largest Jan-27 86.46 P (OI 1,011), 82.46 P (OI 1,267), 80.46 P (OI 824), 88.47 P (OI 414) | about 3,660,000 (Jan-27 2.77M, Apr-27 0.44M, Jun-27 0.45M) |
| Adjusted Jan-28, Jan-29 | outside the app's 400-day window (`config.CHAIN_MAX_DAYS`): not tradeable here | (2.1M + 0.8M, not reachable) |
| Standard Nov-20 (23 days) | 84-89 P; 87 P OI 5,639 alone gives 4.07M | about 7,200,000 |
| Standard Dec-26 and longer | OI 1-10 a strike | about 0 |

**Rules (D19).**
1. Size and price only from a chain whose provider time falls in the latest completed session or the current one; otherwise the line is
   HELD_STALE (the script checks the provider time itself: outside the session the app's quality "close" does not prove freshness).
2. Eligible series: standard strikes; adjusted series only once their deliverable is verified from the OCC information memo for ITB
   (free; read and recorded with the line by the orchestrator). Until then they are HELD_DELIVERABLE, and the owner is told why.
3. Expiries Jan-27 to Jun-27 fit the housing thesis. The Nov-20 standard puts (23 days) are a different, short-dated bet: only with the
   owner's explicit OK (Q19).
4. The 25 %-of-OI cap and the wide-market limit of 5.5 apply; legs are sent as child orders no larger than their displayed size (5.4.3).
5. Whatever cannot be reached is BLOCKED with these numbers, never re-sized onto XHB.

**Expected outcome.** At most about USD 3.7M of the 10.64M (if the adjusted series' deliverable checks out), 0 until then; the residual,
about USD 6.98M, is BLOCKED. With the owner's OK the Nov-20 puts could add up to about USD 7.2M of short-dated delta. Lines 12DI.1-12DI.n are
created by the script from the fresh chain under these rules (request ids allow no apostrophe, hence DI); none is sized tonight. **Exit:** as D (take profit at ITB at or below 80;
cut at 50 % of premium or ITB above 100); close Jan-27 legs by 2027-01-08.

#### E. DLR bearish puts: USD 8.33M of the USD 10M delta executable (NEXT; remainder BLOCKED)

**Thesis.** Data-centre economics and financing expectations deteriorate (DLR priced EUR 1bn 5.125 % 2036 notes on 10-06, EDGAR 8-K). It
stacks on the DLR TRS and physical short: USD 105.3M short DLR in all (3.2).

**Earnings.** Q3 2026 results Thu 10-29 after the close, call 17:00 ET (Yahoo calendarEvents, not an estimate). Buying on Thursday pays
event IV (Nov-Mar 33-36 %); waiting for Friday avoids the event premium but misses the print (Q15; default: buy Thursday).

**Liquidity.** Strike OI 397-3,528; the full USD 10M is about 1,700 contracts; the closing markets were 30-94 % of mid wide. Tranche at most
25 % of each strike's OI:

| Line | Contract | BTO | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg BE | Delta-adj USD | Premium + fees at the ask |
|---|---|---:|---|---:|---:|---|---|---:|---:|
| 12E.1 | DLR 2026-12-18 170 P | 439 | 4.50 / 6.10 / 5.30 (30.2 %) | 35.5 % | -0.3180 | 25.0 % of 1,759 | 163.90 (-8.8 %) | -2,509,625 | 267,790.00 + 285.35 = 268,075.35 |
| 12E.2 | DLR 2026-12-18 160 P | 882 | 1.20 / 3.30 / 2.25 (93.3 %) | 34.5 % | -0.1914 | 25.0 % of 3,528 | 156.70 (-12.8 %) | -3,034,784 | 291,060.00 + 573.30 = 291,633.30 |
| 12E.3 | DLR 2027-01-15 160 P | 381 | 1.85 / 5.10 / 3.475 (93.5 %) | 33.6 % | -0.2132 | 25.0 % of 1,525 | 154.90 (-13.8 %) | -1,460,257 | 194,310.00 + 247.65 = 194,557.65 |
| 12E.4 | DLR 2027-03-19 160 P | 290 | 4.80 / 7.80 / 6.30 (47.6 %) | 33.8 % | -0.2534 | 25.0 % of 1,162 | 152.20 (-15.3 %) | -1,321,058 | 226,200.00 + 188.50 = 226,388.50 |
| 12E.5 | the remainder: about 293 more Dec-26 170 P equivalents | - | - | - | - | - | - | -1,674,276 | **BLOCKED** (owner, 19:40 ET): re-check after the print, as new open interest forms |

- **Tranche:** delta -8,325,724; premium 0.98M at the ask (0.75M at the mid); maximum loss 980,654.80; gamma +723,428; vega +46,144; theta
  -12,654 a day.
- **Expiry P&L:** DLR 130: +5.43M; 150: +1.45M; 160: -0.54M; 170 / 179.77 / 200: -0.98M.
- **Exit:** take profit at DLR at or below 160; cut at 50 % of premium or DLR above 200; close the December legs by 12-11.

#### F. GLD bullish calls: USD 15.0M delta (NEXT)

**Thesis.** Gold appreciates on monetary uncertainty and inflation demand; a long expiry to match. It adds to the gold futures long (3.2); it
is not a hedge.

| Line | Contract | BTO | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg BE | Delta-adj USD | Premium + fees = cash |
|---|---|---:|---|---:|---:|---|---|---:|---:|
| 12F.1 | GLD 2027-06-17 400 C | 907 | 18.90 / 20.20 / 19.55 (6.6 %) | 22.0 % | +0.4428 | 4.9 % of 18,449 | 420.20 (+12.5 %) | +15,000,894 | 1,832,140.00 + 589.55 = 1,832,729.55 |

- **Strategy:** maximum loss 1,832,729.55; delta +15,000,894; gamma +759,212; vega +106,427; theta -4,744 a day.
- **Alternatives** (ask / delta / IV / theta / BE / OI / premium per USD 1M delta): Mar-27 400 C 11.85 / 0.371 / 21.2 % / -0.0626 / 411.85 /
  6,782 / 85k; Jun-27 380 C 28.50 / 0.558 / 21.9 % / -0.0507 / 408.50 / 7,160 / 137k; Jun-27 400 C 20.20 / 0.443 / 22.0 % / -0.0523 / 420.20
  / 18,449 / 122k; Sep-27 400 C 28.75 / 0.487 / 22.8 % / -0.0442 / 428.75 / 546 / 158k; Nov-27 400 C 33.50 / 0.512 / 22.8 % / -0.0402 /
  433.50 / 17 / 175k. September and November 2027 are thin and 11-13 % wide; January 2028 LEAPS are outside the app's 400-day chain window.
- **Expiry P&L:** GLD 330 / 373.51 / 400: -1.83M; 420.2: about 0; 450: +2.70M; 480: +5.42M.
- **Exit:** take profit at GLD at or above 450 or 2.5x premium; cut at 50 % of premium or GLD below 340; next catalyst FOMC 12-08/09.

#### G. USO bearish puts: USD 10.0M delta (NEXT)

**Thesis.** A near-term retreat in crude (WTI about USD 102). **USO is not spot WTI:** it holds front-month WTI futures and rolls monthly.
The app's CL curve is steeply backwardated (about -1.7 % a month), so with an unchanged curve USO earns about +1.7 % a month of roll yield
over the front contract: a headwind of about 3 % (Dec) to 4.5 % (Jan) against these puts. IV 50-53 % is high.

| Line | Contract | BTO | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg BE | Delta-adj USD | Premium + fees = cash |
|---|---|---:|---|---:|---:|---|---|---:|---:|
| 12G.1 | USO 2026-12-18 160 P | 600 | 8.90 / 10.00 / 9.45 (11.6 %) | 51.6 % | -0.3714 | 25.0 % of 2,401 | 150.00 (-9.9 %) | -3,711,846 | 600,000.00 + 390.00 = 600,390.00 |
| 12G.2 | USO 2027-01-15 150 P | 997 | 7.35 / 9.05 / 8.20 (20.7 %) | 50.4 % | -0.2783 | 25.0 % of 3,989 | 140.95 (-15.4 %) | -4,621,736 | 902,285.00 + 648.05 = 902,933.05 |
| 12G.3 | USO 2026-12-18 140 P | 626 | 1.71 / 3.35 / 2.53 (64.8 %) | 53.1 % | -0.1599 | 7.5 % of 8,295 | 136.65 (-18.0 %) | -1,667,322 | 209,710.00 + 406.90 = 210,116.90 |

- **Strategy:** maximum loss 1,713,439.95; delta -10,000,904; gamma +548,935; vega +49,632; theta -20,515 a day. G1 and G2 at 25 % of open
  interest still reach the full USD 10M with G3 sized to the remainder.
- **Expiry P&L:** USO 120: +4.93M; 135: +1.60M; 145: -0.31M; 150: -1.11M; 160 / 166.57 / 190: -1.71M.
- **Exit:** take profit at USO at or below 145 or 2x premium; cut at 50 % of premium or USO above 185; close the December legs by 12-11.
- **Liquidity:** 12G.1 showed a displayed ask size of 1 at the close (re-check at the open).

#### H. TLT bearish puts: USD 5.0M delta (NEXT)

**Thesis.** Longer-dated Treasury yields rise (supply, term premium); catalysts the quarterly refunding in early November and FOMC 12-08/09.
TLT IV is low (13.0 %). **Rate sensitivity** (approximate; TLT duration about 16, not sourced today): +50 bp in 20Y+ yields is about -8 %,
TLT about 74.5, about +0.5M on this line.

| Line | Contract | BTO | Bid / ask / mid (spread) | IV | Delta | Share of OI | Leg BE | Delta-adj USD | Premium + fees = cash |
|---|---|---:|---|---:|---:|---|---|---:|---:|
| 12H.1 | TLT 2027-03-19 80 P | 1,386 | 2.21 / 2.27 / 2.24 (2.7 %) | 13.0 % | -0.4455 | 3.8 % of 36,444 | 77.73 (-4.0 %) | -5,000,833 | 314,622.00 + 900.90 = 315,522.90 |

- **Strategy:** maximum loss 315,522.90; delta -5,000,833; gamma +562,752; vega +27,055; theta -1,178 a day. Alternative: Jan-27 80 P ask
  1.60, delta -0.440, OI 128,613, BE 78.40.
- **Expiry P&L:** TLT 70: +1.07M; 74: +0.52M; 77.73: about 0; 80 / 80.99 / 88: -0.32M.
- **Exit:** take profit at TLT at or below 75; cut at 50 % of premium or TLT above 86.

#### The options book in total

| Strategy | Delta-adj executable (V3 measure) | Held / blocked remainder | Premium + fees = maximum loss | Vega / vol point | Theta / day |
|---|---:|---:|---:|---:|---:|
| A ORCL puts | -34,998,605 | 0 | 9,899,694.50 | +209,308 | -48,392 |
| B QQQ put debit spread (legs gross; net -9,310,041) | 30,002,754 | 0 | 1,050,151.60 | +35,072 | -1,807 |
| C IBIT puts | -25,000,344 | 0 | 5,195,338.90 | +153,836 | -22,196 |
| D XHB puts | -9,362,519 | redirected to D' | 1,557,322.20 | +39,082 | -7,696 |
| D' ITB puts (the XHB remainder) | 0 tonight | HELD 3,660,000; BLOCKED 6,977,481 | - | - | - |
| E DLR puts | -8,325,724 | BLOCKED 1,674,276 | 980,654.80 | +46,144 | -12,654 |
| F GLD calls | +15,000,894 | 0 | 1,832,729.55 | +106,427 | -4,744 |
| G USO puts | -10,000,904 | 0 | 1,713,439.95 | +49,632 | -20,515 |
| H TLT puts | -5,000,833 | 0 | 315,522.90 | +27,055 | -1,178 |
| **Absolute total** | **137,692,577** | **HELD 3,660,000; BLOCKED 8,651,757** | **22,544,854.40** | **+666,556** | **-119,182** |

- Every strategy is measured as V3 section 2 asks (gross absolute delta-adjusted; a spread's legs gross, owner Q5). The economic delta of
  the book uses B's net delta (3.2).
- Cash: the long legs pay 23,534,056.60 including fees; B's short leg brings back 989,202.20. Value at the 10-28 mids: about 21.26M
  (planning; the R0 report and V-B mark it daily).
- Premium at risk = the whole 22.54M: every strategy is a long-premium position whose loss is capped at its premium.

### 3.10 Physical long equities (`I1:US_EXCHANGE`), USD 120M

App quote: Yahoo chart, 10-28 close (16:00 ET; PG 16:02), up to 15 min delayed, last only, no bid / ask. All in FinSim1's master (FULL,
US_EQUITY). Shares in 100-share lots. Cash = gross + fee 0.005 / share, swept from Treasury at the fill, settling T+1. Replay = the fill time
at 10 % of each one-minute bar of 10-28 (the app's cap, shared by all books). Lines whose replay exceeds 30 minutes go as child orders at
about half of the cap (D18): ZS, LMT, NOC, KTOS, GXO and PDBC.

| Line | Ticker | Shares @ ref | Market value | Cash | Replay (share of the 10-28 volume) | Compliance | Status |
|---|---|---|---:|---:|---|---|---|
| 7.1 | SPY | 25,900 @ 772.31 | 20,002,829.00 | 20,002,958.50 | about 1 min (0.03 % of 77,591,520) | 13F; Form 13H large-trader notice (at or above USD 20M in a day) | NEXT |
| 7.2 | SPCX | 103,300 @ 145.23 | 15,002,259.00 | 15,002,775.50 | about 1 min | 13F | NEXT (Q1: as listed) |
| 7.3 | CRWD | 37,500 @ 266.66 | 9,999,750.00 | 9,999,937.50 | about 6 min (0.52 % of 7,160,978) | 13F | NEXT |
| 7.4 | PANW | 20,300 @ 394.49 | 8,008,147.00 | 8,008,248.50 | about 7 min (0.73 % of 2,776,574) | 13F | NEXT |
| 7.5 | ZS | 27,400 @ 255.80 | 7,008,920.00 | 7,009,057.00 | about 32 min (1.32 % of 2,075,717) | 13F | NEXT |
| 7.6 | NOW | 53,300 @ 150.13 | 8,001,929.00 | 8,002,195.50 | about 1 min (0.20 % of 26,252,083) | 13F | NEXT |
| 7.7 | SNOW | 17,700 @ 339.91 | 6,016,407.00 | 6,016,495.50 | about 26 min (0.75 % of 2,361,644) | 13F | NEXT |
| 7.8 | LMT | 15,900 @ 629.75 | 10,013,025.00 | 10,013,104.50 | about 37 min (1.24 % of 1,279,749) | 13F | NEXT |
| 7.9 | NOC | 14,500 @ 552.77 | 8,015,165.00 | 8,015,237.50 | about 116 min (1.99 % of 729,025) | 13F | NEXT |
| 7.10 | KTOS | 121,400 @ 41.17 | 4,998,038.00 | 4,998,645.00 | about 281 min (5.42 % of 2,238,131) | 13F; 13D / G not measurable (no shares outstanding in the master) | NEXT |
| 7.11 | GXO | 101,300 @ 49.38 | 5,002,194.00 | 5,002,700.50 | about 2.07 sessions (20.6 % of 491,767; day 1 fills about 49,030) | 13F; 13D / G not measurable | NEXT as child orders (D18; about 4.1 sessions) |
| 7.12 | JNJ | 18,400 @ 271.04 | 4,987,136.00 | 4,987,228.00 | about 1 min (0.34 % of 5,408,040) | 13F | NEXT |
| 7.13 | TMO | 7,100 @ 704.15 | 4,999,465.00 | 4,999,500.50 | about 11 min (0.32 % of 2,251,204) | 13F | NEXT |
| 7.14 | PG | 25,300 @ 158.26 | 4,003,978.00 | 4,004,104.50 | about 9 min (0.72 % of 3,536,734) | 13F | NEXT |
| 7.15 | WMT | 16,800 @ 118.77 | 1,995,336.00 | 1,995,420.00 | about 1 min (0.16 % of 10,287,351) | 13F | NEXT |
| 7.16 | NKE | 26,900 @ 37.15 | 999,335.00 | 999,469.50 | about 1 min (0.09 % of 30,211,365) | 13F | NEXT |
| 7.17 | PDBC | 49,700 @ 20.14 | 1,000,958.00 | 1,001,206.50 | about 73 min (1.41 % of 3,513,213) | 13F | NEXT |
| | **Total** | | **120,054,871.00** | **120,058,284.50** | | | |
| | Without SPCX (revision 1) | | 105,052,612.00 | 105,055,509.00 | | | |

### 3.11 Physical short equities and the IBIT short (`I1:PB_GS`), USD 80M + USD 100M

Terms for every line: lender Goldman Sachs (assumed terms); fee ACT/360 on the loan; rebate 3.45 % = SOFR 3.95 % - 0.50 % on the cash
collateral; 102 % cash collateral posted at each fill from the sale proceeds (segregated) plus 2 % of own cash, marked daily both ways;
recalls are notices only (the app has no forced buy-in; disclosed). Reg SHO locate ok for every line (app ADV / FINRA tiers); Rule 201 off
on 10-28. Commission 0.0030 / share. From 1.0.6: account-level Reg T on `I1:PB_GS` (4.2). Alternative account (not used, D15):
`I1:US_EXCHANGE` with the agent lender BNY at 0.30 %.

| Line | Ticker | Shares @ ref | Market value | Locate tier (FINRA short ratio 10-28; ADV) | Fee / rebate a day | 102 % collateral at GS | Own cash (2 % + fee) | Replay | Status |
|---|---|---|---:|---|---|---:|---:|---|---|
| 8.1 | C | 96,700 @ 124.11 | 12,001,437.00 | GC 0.40 % (0.3451; 10,731,917) | 133 / 1,173 | 12,241,465.74 | 240,318.84 (fee 290.10) | about 78 min (2.09 %) | NEXT; Form SHO notice |
| 8.2 | STT | 57,600 @ 173.54 | 9,995,904.00 | GC (0.4476; 1,903,359) | 111 / 977 | 10,195,822.08 | 200,090.88 (172.80) | about 249 min (4.26 %) | NEXT |
| 8.3 | DLR | 66,800 @ 179.77 | 12,008,636.00 | GC (0.5127; 2,789,182) | 133 / 1,174 | 12,248,808.72 | 240,373.12 (200.40) | about 148 min (2.91 %) | NEXT; Form SHO notice; opened into the 10-29 print |
| 8.4 | AVB | 65,200 @ 184.06 (the 2026-08-14 print) | - | the preview offered T3 at 25.10 % (no FINRA rows) | - | - | - | could never fill | **SUPERSEDED** (delisted; D4) |
| 8.4a | VMRK | 197,000 @ 60.90 | 11,997,300.00 | GC (0.3765; 5,307,676) | 133 / 1,173 | 12,237,246.00 | 240,537.00 (591.00) | about 311 min (6.86 %) | NEXT; Form SHO notice |
| 8.5 | LEN | 119,800 @ 83.45 | 9,997,310.00 | GC (0.5853; 2,723,621) | 111 / 977 | 10,197,256.20 | 200,305.60 (359.40) | about 353 min (4.19 %) | NEXT |
| 8.6 | DHI | 69,900 @ 143.00 | 9,995,700.00 | T1 1.10 % (0.7084; 2,295,913) | 305 / 977 | 10,195,614.00 | 200,123.70 (209.70) | about 170 min (0.91 %) | NEXT |
| 8.7 | PLTR | 35,600 @ 196.81 | 7,006,436.00 | GC (0.4896; 38,881,735) | 78 / 685 | 7,146,564.72 | 140,235.52 (106.80) | about 1 min (0.24 %) | NEXT |
| 8.8 | TSLA | 17,300 @ 404.22 | 6,993,006.00 | GC (0.5366; 39,254,088) | 78 / 684 | 7,132,866.12 | 139,912.02 (51.90) | about 1 min (0.02 %) | NEXT |
| 9.1 | IBIT | 2,167,300 @ 46.14 | 99,999,222.00 | GC (0.2871; 47,490,484) | 1,111 / 9,775 | 101,999,206.44 | 2,006,486.34 (commission 6,501.90) | about 1.17 sessions (11.68 % of 18,563,160; day 1 fills about 1,856,134) | NEXT as child orders (D18; about 2.3 sessions); Form 13H, Form SHO; 10.0 % of NAV (INFO) |
| | **Total** | | **179,994,951.00** | | **2,193 / 17,595** | **183,594,850.02** | **3,608,383.02** | | |

The shorts whose replay exceeds 30 minutes (C, STT, DLR, VMRK, LEN, DHI and IBIT) go as child orders (D18); each child short carries its
own locate. The IBIT short is the outright bearish Bitcoin position of V3 section 9: no protective calls. A 75 % Bitcoin rally would call about USD 76M of
extra collateral at the lender plus about USD 37.5M of Reg T (planning stress 3).

### 3.12 FX forwards with Goldman Sachs (`I1:PB_GS`), USD 100M: sell EUR and sell JPY against USD, laddered 3 / 6 / 12 months

**Why BUILD, not NEXT.** The r105 / 1.0.6 PB OTC forward prices EUR off the ECB deposit rate by simple interest and JPY off the OECD monthly
proxy; the owner reported "OTC market is bad for forwards and cross currency stuff" (FINSIM1_PLAN 15.15) and the OTC audit found critical
defects (forwards missing from Risk, US-only value-date calendars). Release 1.0.6.1 (FX_CCS_PLAN revision 2) fixes the USD / EUR / GBP
pairs; **JPY pairs keep the legacy method** in that release (FX_CCS_PLAN 1.5 and its risk R15), and the orchestrator's 20:08 ET note keeps
V3's JPY forwards on it (D12).

**Booking date.** The first FX business day, 08:00-16:30 ET, after the 1.0.6.1 deploy and after the owner's own F1 FX trades are booked
(FX_CCS_PLAN 10.5): Fri 10-30 at the earliest, Mon 11-02 if 1.0.6.1 slips. The FX trade date is the booking day (`fx.fx_trade_date` rolls
at 17:00 ET). Every date below is for a **10-30 trade**; the script never takes them from this table: it reads the EUR dates from the
app's preview (`ccycal`, TARGET2 + New York) and checks the JPY dates against its own Tokyo table (5.4.8).

| Trade date | EURUSD spot | EUR 3M / 6M / 12M | USDJPY spot (Tokyo + New York) | JPY 3M / 6M / 12M (Tokyo-valid) |
|---|---|---|---|---|
| Fri 2026-10-30 | Tue 11-03 | 2027-02-03 / 2027-05-03 / 2027-11-03 | Wed 11-04 (11-03 is Culture Day in Tokyo) | 2027-02-04 / 2027-05-06 (05-03 to 05-05 are holidays) / 2027-11-04 |
| Mon 2026-11-02 | Wed 11-04 | 2027-02-04 / 2027-05-04 / 2027-11-04 | Thu 11-05 | 2027-02-05 / 2027-05-06 / 2027-11-05 |

The legacy JPY path has no Tokyo calendar (NYSE calendar, "local holidays not modelled"): from a 10-30 trade its own tenor dates would be
spot 11-03, 2027-05-03 and 2027-11-03, all Tokyo holidays. So the script sends the JPY lines with explicit value dates (the app's PB
forward takes `tenor` or `value_date`; a date must be a trading-calendar business day within 400 days in 1.0.6), and if 1.0.6.1's legacy
"beyond 1 year" rule refuses 2027-11-04, the 12M line uses 2027-11-02 (364 days from the legacy spot, a Tokyo and New York business day),
labelled.

**Indicative pricing (proposed; the live run re-prices everything).** Spot: Yahoo EURUSD 1.1159 and USDJPY 157.774 at 17:33 ET 10-28 (stale
at 18:10 ET). Dealt K = F less (sale of the base) or plus (purchase of the base) the GS half-width 2.0 bp of F (ASSUMED). IM 6 % of the USD
notional = 1,000,000.00 each (ASSUMED). No cross-currency basis (no free source).

| Line | Trade | Value date (10-30 trade) | Days | Notional | F (points) | K | Settlement | Method and label | Status |
|---|---|---|---:|---|---|---|---|---|---|
| 14.1 | EURUSD: sell EUR / buy USD, 3M | 2027-02-03 | 92 | EUR 14,935,627.45 (USD 16,666,666.67 at 1.1159) | 1.120214 (+43.14) | 1.119990 | receive USD 16,727,753.79, deliver EUR 14,935,627.45 | 1.0.6.1 curves (ECB AAA + GS spread for EUR, Treasury curve for USD); MODEL, PROXY. The F shown is the flat ESTR 2.440 % / SOFR 3.95 % cross-check, not that method | BUILD 1.0.6.1 |
| 14.2 | EURUSD sell EUR, 6M | 2027-05-03 | 181 | EUR 14,935,627.45 | 1.124403 (+85.03) | 1.124178 | receive USD 16,790,310.56 | as 14.1 | BUILD 1.0.6.1 |
| 14.3 | EURUSD sell EUR, 12M | 2027-11-03 | 365 | EUR 14,935,627.45 | 1.133114 (+172.14) | 1.132887 | receive USD 16,920,384.20 | as 14.1 | BUILD 1.0.6.1 |
| 14.4 | USDJPY: buy USD / sell JPY, 3M | 2027-02-04 | 93 | USD 16,666,666.67 | 156.6011 (-117.29) | 156.6324 | deliver JPY 2,610,540,753 | legacy: simple interest, JPY OECD proxy 1.043 % (Sept 2026 average) on 360, SOFR 3.95 % on 360; days from the legacy spot 11-03; MODEL - LIMITED | BUILD 1.0.6.1 + the orchestrator's checks |
| 14.5 | USDJPY buy USD, 6M | 2027-05-06 | 184 | USD 16,666,666.67 | 155.4762 (-229.78) | 155.5073 | deliver JPY 2,591,787,992 | as 14.4 | as 14.4 |
| 14.6 | USDJPY buy USD, 12M | 2027-11-04 (or 11-02) | 366 (364) | USD 16,666,666.67 | 153.2911 (-448.29) | 153.3218 | deliver JPY 2,555,362,543 | as 14.4 | as 14.4 |

- **The JPY rate.** The legacy method uses the OECD monthly proxy, 1.043 %, about 18 bp below the BoJ's TONA final for 10-27 (1.227 %):
  the legacy forwards come out 0.06 / 0.10 / 0.15 yen below TONA-based ones (JPY 1.0M / 1.7M / 2.5M on the three lines, about USD
  6,400 / 11,100 / 16,200, in the fund's favour): a known pricing error, labelled LIMITED. 1.0.6's age rule allows a monthly average up
  to 62 days after its month (`tables.rate_age`): the September average passes until 2026-12-01. The orchestrator checks the age and the
  pricing path before the lines book (orchestrator note b); a failure leaves the line UNEXECUTED with the exact reason. TONA itself is on the
  FX / CCS plan's 1.0.6.2 list; this plan does not build it (6.1).
- **Delivery.** The fund holds no EUR or JPY. The settlement-funding instruction of 1.0.6.1 (FX_CCS_PLAN D9, 4.3) buys each currency as one
  audited FX desk spot deal per (account, currency, due date) **in the obligation's own account, `I1:PB_GS`** (revision 1 wrongly named
  `I1:FOREIGN_FX`: a purchase there would leave `I1:PB_GS` short of the currency on the value date, SETTLEMENT_UNFUNDED). D15's FX
  sub-ledger reads those deals in `I1:PB_GS`. The EUR lines carry `settlement_funding {"EUR": "BUY_WITH_USD"}`; the JPY lines carry
  `{"JPY": "BUY_WITH_USD"}` only if 1.0.6.1 accepts JPY (its design names EUR; the preview answers). Otherwise the JPY deliveries need the
  owner's own JPY purchase two business days before each value date, or a close / roll of the forward; without either the delivery waits
  SETTLEMENT_UNFUNDED, never an invented conversion. The first JPY delivery is 2027-02-04.
- **GBP.** V3 asks the institutional book to stay neutral on GBP/USD: no GBP line in book I. (The EUR/GBP forward of the owner's separate FX
  instruction is F1's.)
- Totals: USD notional 100,000,000.02; IM 6,000,000.00 (3,000,000.00 if the JPY half stays unexecuted).

### 3.13 Accounts and margin

| Line | Item | State | Status |
|---|---|---|---|
| 15.1 | Goldman PB onboarding | ISDA Master, CSA, PBA signed 2026-10-21 (`book.onboard_pb`, command 1357); `I1:PB_GS` open since 2026-10-21 12:59:51 ET, flat. CSA modelled: zero-threshold daily CTM cash VM at 17:05 ET, IM held per contract (GL 1330), CSA netting for VM, coupons and resets (not IM) | done |
| 15.2 | Eligibility | PB / OTC eligible by profile; ECP (NAV above the 10M floor); QP threshold 0, Private Markets open | done |
| 15.3 | GS IM and dealing schedule (seed, ASSUMED) | IM: TRS 10 %; IRS \|DV01\| x 50 bp; XCCY 2 %; OTC forwards 6 %; sold OTC options 15 %; CDX IG 2 % / HY 6 %. Half-widths: IRS 1 bp, forwards 2 bp, equity options 1.00 vol, FX options 0.30 vol, CDX IG 0.5 / HY 2 bp; OTC spread multiplier 1.0. `I1:PB_GS` has no `cdx_terms` row yet (Q3 answered: copy F1's basis); CDS tiers arrive with 1.0.7 | NEXT: the CDX basis per index (5.4.7) |
| 15.4 | GS financing terms | PB debit SOFR + 1.25 %, but PB financing is OFF and `pb_limit` 0 on `I1:PB_GS`: every need is funded by a Treasury sweep (kept OFF, D14). Credit interest on positive settled USD: SOFR - 0.75 %, not on CSA collateral received. TRS spread 0.60 % each way; borrow GC 0.40 %, hard-to-borrow +0.10 %; commissions as 3.0. All seeds, not real GS terms | done |
| 15.5 | CSA collateral eligibility | r105: cash only. 1.0.6 adds Treasury pledges under the CSA (GS seed: bills 2 %, notes / bonds 4 % haircut) but does not release cash IM against pledged securities | no build needed: IM is posted in cash (4.2); pledging stays a stress lever |
| 15.6 | Distinct account: Treasury | `I1:TREASURY`: cash 1,000,000,000.00 + 661,944.45 interest; funds every other account by sweep; never trades | done |
| 15.7 | Distinct account: securities | `I1:US_EXCHANGE` (equities, ETFs, options) and `I1:DEBT` (Treasuries) | done |
| 15.8 | Distinct account: short-sale financing | No such role in FinSim1 (roles: TREASURY, US_EXCHANGE, DEBT, CREDIT, FOREIGN_FX, PRIVATE, PB_broker) | sub-ledger of `I1:PB_GS` in V-B (D15); a real role is Q7 |
| 15.9 | Distinct account: futures clearing | No FCM role; futures trade in US_EXCHANGE or PB accounts | sub-ledger of `I1:US_EXCHANGE` in V-B (D15); Q7 |
| 15.10 | Distinct account: FX | `I1:FOREIGN_FX` (FX spot, foreign listings); the GS forwards are OTC rows in `I1:PB_GS`, and their SFI purchases sit there too (the obligation's account, FX_CCS_PLAN D9); D15's FX sub-ledger reads both | done |
| 15.11 | Distinct account: Goldman OTC | `I1:PB_GS` (`otc_contracts`: TRS, CDX, CDS, IRS, FX forwards); per contract IM (1330), VM posted / received (1320 / 2330), upfront cost basis (1335 / 2345), accruals (1225 / 2340), financing GLs (4451 / 4452 / 4453) | done |

---

## 4. Funding and margin for book I

### 4.1 Cash needed, by sleeve and date

| Sleeve | Cash from `I1:TREASURY` (sweep) | When | Encumbered as |
|---|---:|---|---|
| Futures IM + fees | 19,141,100.00 + 5,649.75 | tonight's Globex fills and the 10-29 day session (grains) | IM at the FCM (`I1:US_EXCHANGE`) |
| Treasuries | 400,073,996.68 | trade 10-29, settle 10-30 | securities in `I1:DEBT` (repo-able once settled, 10-30) |
| Physical longs, SPCX included | 120,058,284.50 | child fills from 10-29 (GXO for about 4 sessions, D18), settle T+1 | securities in `I1:US_EXCHANGE` |
| Options, net | 22,544,854.40 (23,534,056.60 out, 989,202.20 back on B's short leg); ITB not yet | fills from 10-29 | premium = maximum loss |
| Short sales: 2 % excess + fees | 3,608,383.02 | child fills from 10-29 (IBIT for about 2.3 sessions) | part of the 102 % collateral at GS (the proceeds fund the other 100 %) |
| Reg T reserve on the shorts (1.0.6) | 89,997,475.50 moved into `I1:PB_GS` | 10-29 15:45-16:30 ET and each day while shorts fill (5.4.9) | reserved in `I1:PB_GS`, never spent or swept |
| **By the 10-29 close** | **655,429,743.85** (if every line has filled; the child orders spread it over several days) | | |
| TRS IM, SPCX included | 104,999,789.40 | Fri 10-30 | IM at GS |
| CDX IM (IG 100M, HY 125M, IG 50M) | 10,500,000.00 | Fri 10-30 | IM at GS |
| FX forwards IM | 6,000,000.00 (3,000,000.00 without the JPY half) | the 1.0.6.1 booking day | IM at GS |
| **By the 10-30 close** | **776,929,533.25** | | |
| CDX initial payments | about -11,255,000 (received T+1, Mon 11-02) | 11-02 | the contracts' cost basis (2345) |
| IRS IM | 6,628,801.50 (Treasury-par figures; the SOFR curve gives similar DV01s) | after the 1.0.8 deploy | IM at GS |
| ORCL CDS IM (+ the upfront at the owner's level) | about 1.25-2.0M | after the 1.0.7 deploy and his level | IM at GS |
| **After every build** | **about 774.3M net** | | |

### 4.2 Margin and collateral by counterparty (after every build)

| Counterparty | Initial margin or reserve | Daily variation | Notes |
|---|---|---|---|
| Goldman Sachs under the CSA (`I1:PB_GS`) | TRS 105.00M + CDX 10.50M + IRS 6.63M + FX 6.00M + CDS about 2.0M = about 130.1M | CTM cash VM at 17:05 ET, netted across every contract | zero threshold; cash only; IM fixed per contract |
| Goldman Sachs as stock lender | 102 % cash collateral 183,594,850.02 = proceeds 179,994,951.00 + own 3,599,899.02 (fees 8,484.00 apart) | marked daily both ways (17:10 ET) | rebate 3.45 %; recalls are notices |
| Reg T on `I1:PB_GS` (1.0.6) | 50 % of the short market value = 89,997,475.50 reserved (maintenance 30 % shown) | re-measured at the close; a deficit is a call at 17:20 ET, due 10:00 ET next business day | met by a Treasury transfer (5.4.9) |
| CME via the FCM (`I1:US_EXCHANGE`) | IM 19,141,100.00 (maintenance 17,401,800.00) | VM in cash at the settle print | static IM table, ASSUMED |
| Listed options (OCC) | none: long premium paid; B's 640 P covered by the 700 P | - | never naked (5.4.4) |
| Repo dealers | none | - | contingency (4.4) |

The shorts sit in `I1:PB_GS`, apart from the longs (V3 section 15's short-sale financing account). Booking them in `I1:US_EXCHANGE` instead
would let the longs' 50 % loan value (about 60.0M) count toward Reg T and cut the reserve to about 30.0M, but it would mix short financing
with the securities account and change the lender to the agent lender BNY: not the default (Q7 covers the account structure).

### 4.3 What stays unencumbered

| | USD |
|---|---:|
| Cash at 18:48 ET (`I1:TREASURY` GL 1010; the 661,944.45 interest receivable is not cash and is left out) | 1,000,000,000.00 |
| less encumbered by the 10-29 close | -655,429,743.85 |
| **= unencumbered at the 10-29 close** | **344,570,256.15 (34 % of NAV)** |
| less the 10-30 TRS, CDX and FX IM | -121,499,789.40 |
| **= unencumbered at the 10-30 close** | **223,070,466.75 (22 %)** |
| plus the CDX initial payments (about, 11-02) | +11,255,000 |
| less the IRS and ORCL CDS IM (about) | -8,628,801.50 |
| **= after every build** | **about 225.7M (22.6 %)** |

"Unencumbered" leaves out futures IM, OTC IM, the short collateral, the Reg T reserve, option premium and every security (the Treasuries are
liquid through repo once settled, but they are not cash). The unencumbered cash sits in `I1:TREASURY` (the 17:30 ET sweep-out returns
accounts' excess) and earns SOFR - 0.75 % (about USD 19,800 a day on 225.7M). The receivable (661,944.45) becomes cash when the month's
interest is paid.

### 4.4 Treasury repo: not used at the opening

- **What the app offers.** `repo.py` books a REPO in `I1:CREDIT` against settled, unpledged face in `I1:DEBT`, with a primary dealer from
  the counterparty directory (ASSUMED terms: notes / bonds 2 % haircut, bills 1 %, rate SOFR + 0.10 %, overnight / term / open), marked both
  ways at 17:15 ET, opened / rolled / closed only in the bond session (08:00-17:00 ET). Pledged face cannot be sold or pledged again.
- **Why not now.**
  1. Cash covers every sleeve with about USD 226M to spare: repo would only add a liability.
  2. Repo capacity is the fund's main contingency line in the stress (4.5): drawing it at the opening would spend the buffer before it is
     needed.
  3. V3 section 15: repo financing is neither profit nor new NAV. The carry (coupons about 4.8 % against SOFR + 0.10 % = 4.05 %) is not a
     reason to lever further.
  4. The face settles only on 10-30, so before then there is no capacity at all (D13).
- **Capacity from 10-30.** 399,995,994.68 x 98 % = about USD 392.0M (about 360.0M at a stressed 10 % haircut).
- **Proposed draw rule (V-S reports it; the owner or the orchestrator acts):** draw only when a margin call would take unencumbered cash
  below the USD 150M floor; overnight or open; repay as soon as cash recovers. A same-day call can be met with repo only if it is drawn in
  that day's bond session (before 17:00 ET), on face settled by then. CSA Treasury pledges (1.0.6) are not used: they do not release cash IM
  in that version, and pledged face cannot also back a repo.

### 4.5 Liquidity reserve and the planning stress

**Policy (D13).** Unencumbered cash at or above USD 150M after every line; liquid resources (unencumbered cash + repo capacity on settled,
unpledged face) at or above 1.25x the largest call of the ten scenarios (same-day calls plus the next-morning Reg T call).

**Planning stress (proposed, first-order; the V-S engine replaces it).** Delta-one positions linear; options re-priced by Black-Scholes at
unchanged IV (the 10-28 IVs, rates 3.95 %); Treasuries fully re-priced at the shifted yield; IRS and CDX through DV01 / CS01. Equity names
move by group (tech +30 %, SPX +21 %, RTY +18 %, other US equities +12 % in scenario 2); SPCX, with about 95 sessions of history, moves at
beta 1.0 (the V-S rule, 6.2), i.e. with tech; the 1.36x line shows it unshocked. Book: every executable line plus the builds (IRS, FX, the
ORCL CDS IM, not its spread), ITB not yet booked. Due times are the app's (`collateral.py`): GS CSA VM at 17:05 ET, futures VM at the settle,
the lender's collateral mark at 17:10 ET (all the same day), Reg T at 10:00 ET the next business day.

| # | Scenario (planning shocks) | Day P&L | Same-day calls (counterparty) | Next-morning (Reg T) | Afterwards (cash 225.7M; repo 392.0M from 10-30) |
|---|---|---:|---:|---:|---|
| 1 | ORCL +50 % | -83.2M | 75.0M (GS CSA) | 0 | 150.7M of cash left: met from cash (OK) |
| 2 | Nasdaq +30 % (groups as above); CDX IG -20 bp, HY -100 bp | -131.9M | 152.4M (GS CSA 140.0M, lender 12.4M) | 6.1M | 67.2M: below the floor, repo about 83M (REPO_REQUIRED) |
| 3 | Bitcoin +75 % (IBIT) | -79.8M | 76.5M (lender) | 37.5M | 111.7M: repo about 38M (REPO_REQUIRED) |
| 4 | Housing and homebuilders +30 % (XHB, LEN, DHI, VMRK) | -66.3M | 65.3M (GS CSA 55.5M, lender 9.8M) | 4.8M | 155.6M: cash (OK) |
| 5 | Oil +40 % (all CL months, USO) | -41.4M | 40.0M (FCM) | 0 | 185.7M: cash (OK) |
| 6 | Gold -20 % (GC, GLD) | -21.6M | 20.0M (FCM) | 0 | 205.7M: cash (OK) |
| 7 | Rates -150 bp parallel | +5.8M (Treasuries +26.0M unrealised, IRS -19.9M, TLT puts -0.3M) | 19.9M (GS CSA) | 0 | 205.8M: cash (OK); repo capacity rises with the bonds |
| 8 | All bearish theses against the fund: 1 + 2 + 3 + 4 + 5 + 7, DLR +30 %, C and STT +20 %, EUR and JPY +10 % against USD | -347.8M | 382.2M (GS CSA 248.5M, FCM 34.9M, lender 98.7M) | 48.4M | same day: 225.7M cash + 392.0M same-day repo = 617.7M covers 382.2M; then 48.4M at 10:00 from the rest; about 205M of repo used; NAV about 653M (REPO_REQUIRED) |
| 9 | Options lose 50 % of their premium value (time / vol), applied to each strategy's NET value (B: half its spread value, not half its long leg) | -10.6M | 0 | 0 | unchanged (OK) |
| 10 | PB collateral sharply higher: TRS IM 20 %, CDX IM 5 % / 15 %, IRS IM 100 bp x DV01, FX IM 12 %, CDS IM x 2, Reg T 100 %, borrow collateral 105 %, futures IM x 1.5, repo haircut 10 % | 0 | 5.4M (lender) | 234.9M (CSA and FCM IM, Reg T: 10:00 ET next day) | 240.3M against 225.7M of cash and 360.0M of repo at 10 %: repo about 165M (REPO_REQUIRED) |
| 8 + 10 | the liquidity spiral | -347.8M | 387.6M | 283.3M | resources 585.7M (cash + repo at 10 %): same day met with repo; next morning short by about 85M; LIQUIDATION_REQUIRED (sales of listed longs and long options on the shock day, settling T+1 overnight, before 10:00); solvent |

- **Coverage:** the largest call (8: 430.6M) against 225.7M + 392.0M = 617.7M is **1.43x** (1.36x with SPCX unshocked): the policy holds.
  The same-day part alone (382.2M) against cash plus same-day repo is 1.62x. Lowest cover while the 10-30 OTC stage books: 1.44x (after the
  DHI TRS, before the long TRS lines).
- **The 10-29 book alone** (before the OTC stage): the largest call is 182.1M (scenario 8: FCM 34.9M, lender 98.7M, Reg T 48.4M) against
  344.6M of cash with no repo yet: 1.89x.
- **Reverse stress (planning):** scaling scenario 8 by k, liquid resources including a sale of the listed longs run out near k = 1.7 (about
  1.05 under the scenario 10 margin regime) and NAV reaches zero near k = 2.6 (revision 1's book; V-S computes both points exactly, with
  option convexity, due times and the liquidation waterfall).
- No hypothetical protection is assumed anywhere: only lines in this order book.

### 4.6 Financing commitments and carry (planning, at SOFR 3.95 %)

| Item | Per day | Per year (about) | Label |
|---|---:|---:|---|
| TRS financing (short TRS receive SOFR - 0.60 %, long TRS pay SOFR + 0.60 %), SPCX included | +45,041.69 | +16.4M | model (nightly fixings) |
| TRS dividend equivalents, net paid | -26,200 | -9.56M | observable ex-dates |
| Stock-borrow fees (GC 0.40 %, DHI 1.10 %) | -2,193 | -0.80M | ASSUMED |
| Rebates on the short collateral (3.45 % on 183.6M) | +17,595 | +6.42M | ASSUMED |
| CDX running premium paid (IG 100 bp on 150M, HY 500 bp on 125M) | -21,528 | -7.75M | contract |
| IRS net carry (pay about 4.5 % fixed, receive SOFR; from 1.0.8) | about -4,600 | about -1.7M | model |
| Treasury coupons (390.01M face, 4.50-6.25 %) | about +57,100 | about +19.3M at the purchase yields | contract |
| Interest on unencumbered Treasury cash (SOFR - 0.75 % on about 225.7M) | about +19,800 | about +7.2M | ASSUMED |
| Options theta | -119,182 | (decays with time) | Cboe Greeks |
| **Net carry** | **about -34,000 a day** (about +85,000 before theta) | | |

The futures carry is in their prices (the WTI backwardation costs the short ladder as each leg rolls up the curve; 3.7).

### 4.7 Never forced to USD 3B

- No line is ever re-sized up to replace a held, blocked or unfilled line; the gap stays visible in the blotter (3.1).
- The script stops a stage before the first line that would breach D13 and reports the shortfall; the owner decides.
- Margin calls are met from cash, then repo (the draw rule), never by opening new positions.

---

## 5. The execution procedure

### 5.1 The tool

- **Where:** `C:\Users\logan\finsim-orchestrator\institutional\` (durable, next to `personal\`; never `%TEMP%`, which Storage Sense empties):
  - `order_plan.json`: plan id `v3i1028`, version 2; every line of section 3 with its line id, sleeve, class, account, instrument, side,
    target and measure, the 10-28 reference (price, quantity, Greeks for options), the sizing rule, the limit rule, the gates, the status,
    the strategy (A-H, D'), the thesis / exit text, and for 4.4 the reference obligation of 3.4; generated from this plan by the tooling
    agent with a mechanical check that every line id of section 3 appears exactly once;
  - `submit_institutional.py`: standard library only, `py -3.12`, the same structure as `personal\submit_personal.py` (an `Api` class,
    503 retries with the same request id, one JSON report per run); a hand table of Japanese national holidays 2026-2027 from the Cabinet
    Office list (labelled hand-maintained) for the JPY value dates (D12);
  - `runs\` (one report per run) and `reports\<date>\` (R0, 5.4.11).
- **Modes:** `check` (read-only), `futures`, `equities`, `options`, `bonds`, `cdxterms`, `otc`, `regt`, `fx`, `pass` (one foreground pass:
  the next child orders, the reprice of off-market limits, B's 640 P; D18, 5.8), `report`.
  Flags: `--base URL` (default `http://127.0.0.1:8765`), `--plan`, `--only LINE,...`, `--dry-run` (previews only), `--window any` (copies
  only), `--out`.
- **Exit codes:** 0 = every evaluated line is fine (sent, already there, or deliberately held); 1 = at least one line blocked or the stage
  stopped; 2 = a guard refused to run or the app was unreachable (nothing sent).
- **Who runs it:** a tooling agent builds and dry-runs it on a served copy (5.9); only the orchestrator runs it against the live app, in the
  windows of 5.8, under the owner's V3 authorization ("The portfolio manager authorizes creating this independent $1B paper fund and
  submitting eligible simulated orders"). No builder ever sends to the live app.

### 5.2 Request ids and idempotency (D17)

The bus contract (`commands.py`): a request id is unique in the database; the same id and payload replays the stored result; the same id with
another payload is 409 REQUEST_ID_REUSED; a refusal leaves no row, so the id can be retried after the cause is fixed. Ids match
`^[A-Za-z0-9:_.-]{8,80}$`.

| What | Request id / reference | Found again by |
|---|---|---|
| An order line, first send | `v3i1028:<line>` (e.g. `v3i1028:7.1`, `v3i1028:12B.2`) | `GET /api/accounts/{id}/orders?status=all&limit=2000`, request ids equal to `v3i1028:<line>` or starting with `v3i1028:<line>:` (the colon matters: `7.1` is a prefix of `7.10`, `10.1` of `10.10`, `3.1` of `3.10`) |
| A child order of a large equity ticket (D18) | `v3i1028:<line>:c<n>`, n = 1, 2 ... | the same list; the family is the line's own id and every id starting with `v3i1028:<line>:` |
| A re-send after a DAY expiry or a reprice | `v3i1028:<line>:r<n>`, n = 2, 3 ... | the same list |
| A bond line | `v3i1028:<line>` | the fills of `I1:DEBT` on that CUSIP carrying the line's request id; the same id on retry |
| An OTC line on 1.0.5 (direct) | `v3i1028:<line>:book:<n>` | `GET /api/otc/contracts?account_id=I1:PB_GS` (all statuses), matched by signature: kind, underlying or index, side, tenor, units or notional within 1 % (book I had no contract at 20:19 ET, so a match is ours; 4.1 and 4.3a, both CDX.NA.IG buy protection, differ by notional, 100M against 50M) |
| An OTC line on 1.0.6 (RFQ) | `v3i1028:<line>:q<n>` request, `:a<n>` accept, `:d<n>` decline | contracts by signature first, then `GET /api/otc/rfqs?account_id=I1:PB_GS&status=all`; n = 1 + the RFQs matching the signature |
| An OTC line on 1.0.6.1 and later | `client_ref` = `V3I-1028-<line>` (unique; 409 DUPLICATE_CLIENT_REF) plus the RFQ ids above | the contract's `client_ref` |
| The I1 CDX terms, one command per index | `v3i1028:cdxterms:IG:<n>` and `v3i1028:cdxterms:HY:<n>` (one index per `cdx.terms` command) | the stored row: skipped when that index already equals the target; 400 NOTHING_TO_UPDATE counts as already set |
| A Reg T transfer | `v3i1028:regt:<yyyymmdd>:<n>` | today's transfers into `I1:PB_GS` with that prefix |

Rules:
- Before any send, the line is looked up: a WORKING, PARTIAL, SUSPENDED or WAITING_PARENT order of the family means ALREADY_WORKING (nothing
  sent); a filled family means DONE; a booked contract means ALREADY_BOOKED.
- **After a cancel, re-read before a re-send.** A reprice or a child replacement cancels with `DELETE /api/orders/{id}`, then reads that
  order again and takes its final filled quantity; only then is the family's remainder computed (planned - the sum filled over the family)
  and the `:r<n>` or next `:c<n>` sized. A fill between the first read and the cancel is never sent twice.
- `200` with `X-Replayed: 1` is REPLAYED; `409 REQUEST_ID_REUSED` is recorded and nothing is sent (an earlier plan version); any other
  refusal is BLOCKED with the app's code and message.
- The run report is a record, never the source of truth: the server lookups decide. A restart of the app or of the script cannot duplicate a
  line.

### 5.3 Pre-flight gates (every mode; any failure: exit 2, nothing sent)

1. `GET /api/health`: the version is one this plan knows (1.0.5, 1.0.6, 1.0.6.1, 1.0.7, 1.0.8); no integrity hold; lifecycle and matcher
   running; providers healthy. The server's ET clock is the clock (not the machine's).
2. `GET /api/books`: lifetime I1 OPEN; NAV above USD 900M (sanity).
3. `GET /api/accounting/integrity`: every check ok.
4. Capabilities, detected never assumed: the RFQ path (`GET /api/otc/rfqs?account_id=I1:PB_GS` answers 200), Reg T
   (`GET /api/collateral/regt?account_id=I1:PB_GS` answers 200), `client_ref` (the contract view carries it), the CDS routes (1.0.7), the
   SOFR curve (the IRS preview accepts `curve: SOFR_FUT`, V-R in 1.0.8).
5. The F1, P1 and I1 structural fingerprints (5.6) are taken.
6. Host: never between 00:30 and 01:50 ET (this machine's futures-capture guard window); free RAM at least 0.5 GB.
7. Payload guard on every line: the account starts with `I1:`; `?book=I` on every route; the instrument (and a TRS underlying) is not AVB;
   an exchange-listed equity, ETF or REIT (or an option's or TRS's underlying of that kind) whose last observed trade is older than 5 NYSE
   sessions is refused (the script's own stale-listing check until V-X's guard lands; instruments without exchange prints, such as bonds,
   OTC contracts and FX, are exempt).
8. The D13 projection with the line's need added (cash floor and coverage, 4.5); a breach stops the stage before the line.

### 5.4 Each mode

#### 5.4.1 `futures` (CME Globex 18:00-17:00 ET; CBOT grains in the day session only)

For each line 10.1-10.7 and 11.x tonight, in the order GC, CL Z / F / G / H, HG, SI, ES, RTY, NQ; then 10.8-10.10 (ZC, ZS, ZW) at 09:30 ET
on 10-29:
1. fresh quote (`GET /api/quotes?ids=...`): executable quality, at most 15 minutes old; drift gate |last / reference - 1| at most 3 %, else
   HELD_DRIFT (reported; the orchestrator decides);
2. size `round(target / (multiplier x last))`;
3. marketable LIMIT: BUY at last + max(2 ticks, 0.10 % of last) rounded up to the tick; SELL at last - the same rounded down (the app fills at
   the bar's price, never at a worse one than the limit); TIF DAY, which the app ends at the close of the contract group's current session
   (`sessions.day_order_expiry`): 17:00 ET 10-29 for the Globex lines sent tonight; 14:20 ET for the grains sent at 09:30 ET (CBOT has a
   night session 20:00-08:45 and a day session 09:30-14:20 per trade date, so a grain order sent at night would lapse at 08:45 ET);
4. preview (`POST /api/orders/preview?book=I`): ok, need = IM + fee; the D13 test with the need added;
5. submit (`POST /api/orders?book=I`, `X-Request-Id`).

Unfilled grain quantity is re-sent (`:r<n>`, sized fresh) in the next day session's first pass; unfilled Globex quantity at the next
Globex pass. The deferred CL months fill mainly in the 10-29 day session (3.7).

#### 5.4.2 `equities` (physical longs 7.x, shorts 8.x and 9.1)

- Tonight the first orders rest for the 10-29 open; during a session the same mode sizes from the fresh last.
- Size: 100-share lots (D8). Limits: resting overnight, BUY at or below the 10-28 close x 1.01 (rounded up to the cent) and SELL_SHORT at or
  above the close x 0.99 (rounded down); in a session, the fresh last x (1 +/- 0.005). TIF DAY.
- **Large tickets as child orders (D18):** every line whose replay at the 10 % cap exceeds 30 minutes (IBIT, GXO, LEN, VMRK, KTOS, STT,
  DHI, DLR, NOC, C, PDBC, LMT, ZS) is sent one child per pass, each sized to the shares the cap would give in half the time to the next
  pass. Tonight's resting order of such a line is its first child (`:c1`, sized for 09:30-10:15 ET). The others go whole.
- Shorts: the preview must show the locate (tier, lender GS, fee) and Reg SHO ok; each child short has its own locate; the Rule 201 state is
  read at submission and reported.
- SPCX (7.2) is sent like any other long (owner Q1); lines 8.4 and 3.8 (AVB) never.

#### 5.4.3 `options` (per underlying: A, C, D, E, F, G, H, then B; D' only under D19)

1. **Chain, then preview at once.** For each underlying, `GET /api/instruments/<u>/chain?expiry=<e>` for every expiry of its legs, polling
   the 202 answer until 200, then preview every leg of that underlying immediately. The preview materialises the contract in the master
   (`api_products._materialise_option` -> `options.ensure_option`), and a materialised contract no longer needs the chain. NOT_LISTED means
   the underlying's chain has left the server's chain cache, which holds only 2 snapshots (`server.py` builds `ChainCache` with
   `max_entries` 2) and is churned every 90 s by the worker's refresh of every pinned underlying (F1 / P1 options already pin IBIT, META,
   ORCL, QQQ, AAPL, NVDA, TSLA, GLD and USO; I1 adds XHB, DLR, TLT and ITB). On NOT_LISTED: request the chain again and preview at once,
   up to 5 attempts with backoff 2 / 4 / 8 / 16 / 32 s; then HELD_NOT_LISTED for the next pass. Revision 1's "pruned about 90 s after the
   last chain view" was wrong.
2. Size: contracts = round(leg delta target / (|delta| x S x 100)) on the fresh chain (a spread: from the sum of its legs' absolute deltas,
   D8), capped at 25 % of the strike's open interest (A's legs at 20 %).
3. **No displayed size, no send.** A leg whose chain row carries no displayed ask (BTO) or bid (STO) size at all (the field missing, which
   the parser keeps as None; a size of 0 simply fills nothing) is HELD_NO_SIZE for that pass: the app would fill the whole remainder in one
   observation (`orders_rules.fill_cap`: "no displayed size: fills in full"), an impossible fill.
   A wide leg (spread above 10 % of mid: D, E, G partly, D') is sent as child orders no larger than its displayed size at sending (at least
   1), one per pass, so a later "no size" observation can never fill more than one child. R0 flags any option fill whose memo says "fills
   in full" for the owner's review.
4. Gates: an underlying more than 5 % from its 10-28 mark holds the whole strategy (HELD_DRIFT: the strikes are re-selected by the
   orchestrator and the owner, not by the script); premium + fees above 1.10x the line's 10-28 cash holds the line (HELD_BUDGET); for D',
   the chain's provider time must fall in the latest completed session or the current one (HELD_STALE), and an adjusted series needs its
   recorded deliverable check (HELD_DELIVERABLE; D19).
5. Limit: when the spread is at most 10 % of mid, the ask (BTO) or the bid (STO); when wider, mid + 0.25 x spread for a BTO, never above 1.10x
   the 10-28 ask, re-evaluated at each pass. TIF DAY.

#### 5.4.4 Strategy B, legged

- 12B.1 (BTO 772 QQQ Mar-27 700 P) is sent as in 5.4.3.
- 12B.2 (STO QQQ Mar-27 640 P) is sent only for the 700 P quantity already held and not yet matched by a 640 P sold or working, at the then
  bid, in as many increments (`:r<n>`) as needed, on every pass. It is never naked.
- On 10-29 every pass (5.8) runs it, so B is completed as the 700 P fills.
- If the 700 P fills but the 640 P cannot be sold the same day, the book carries the long put alone overnight (delta about -19.7M instead of
  -9.3M net); R0 and the blotter say "B: long leg only"; the 640 P goes again at the next session. The realised net debit is reported against
  13.59.
- Alternative (Q14): child all-or-none packages no larger than the displayed size of both legs, through the strategy path; V-X adds a slicing
  mode that does this inside the app.

#### 5.4.5 `bonds` (10-29, 08:00-16:55 ET; nothing after 16:55)

For each 6.x line: preview at a trial face (target / the preview's dirty), face = floor(target / dirty / 1,000) x 1,000, preview again, trade
(`POST /api/bonds/trade?book=I`, account `I1:DEBT`, BUY, face). The app fills the whole face at mark + half-spread with accrued to the trade
date (3.6); the report records clean, accrued, fee and the settlement date. Drift gate: the dealt yield within 15 bp of the 10-28 yield, else
HELD_DRIFT. The tooling's unit test pins the floor rule on the 3.6 table.

#### 5.4.6 `otc`: TRS and CDX (Fri 10-30, 10:00-15:30 ET, equity session open, after the Treasuries have settled)

Order: `cdxterms` (5.4.7); TRS 3.1-3.14 (3.8 never); CDX 4.1, 4.2, 4.3a. Before the first line the script confirms that the six bond fills
have SETTLED (their settlement rows, as `report` reads them) and that none of the face is pledged (`GET /api/collateral/pledges?book=I`
on 1.0.6): the D13 repo capacity counts only then; if not, the stage waits. Per line:
- **1.0.6 (RFQ):** preview (`POST /api/otc/preview`) and validate (5.4.8); request (`POST /api/otc/rfqs`); poll `GET /api/otc/rfqs/{id}` every
  5 s for up to 150 s; QUOTED: validate the quote again, then accept (`POST /api/otc/rfqs/{id}/accept`) and record the contract id; EXPIRED or
  still REQUESTED: decline, next attempt; QUOTE_MOVED / QUOTE_EXPIRED: next attempt; at most 3 attempts.
- **1.0.5 (direct):** preview, validate, `POST /api/otc/contracts` with the previewed terms.
- **Retryable** 409s (QUOTE_STALE, QUOTE_UNAVAILABLE, SESSION_CLOSED, CLOSE_PENDING, WRITER_BUSY): wait 45 s, next attempt. Anything else:
  BLOCKED with the code and the app's text.
- TRS units = floor(target / the fresh mark) (D8). New contracts are PENDING_CONFIRM until 17:05 ET (1.0.6); the first VM posts at 17:05 ET.
- The D13 test runs before every line with the settled face's repo capacity (4.5); the tooling's unit test reproduces revision 1's breach
  (the 10-29 stage with capacity 0 stops before 3.4 NVDA) and the 10-30 pass.

#### 5.4.7 `cdxterms`

`cdx.terms` takes one index per command (`{account_id, index, basis_bp?, half_spread_bp?, im_pct?}`) and answers 400 NOTHING_TO_UPDATE when
every given term already has that value. The I1 seed equals F1's half-spreads and IM (IG 0.50 bp / 2 %, HY 2.00 bp / 6 %), so only the basis
differs: `POST /api/otc/cdx/terms` with `{account_id: I1:PB_GS, index: IG, basis_bp: -24.5}` under `v3i1028:cdxterms:IG:<n>`, then
`{..., index: HY, basis_bp: 23.0}` under `v3i1028:cdxterms:HY:<n>`. Label: "ASSUMED: copied from F1's calibration to the owner's 10-21 dealt
spreads (64 / 352 bp)" (owner, Q3). NOTHING_TO_UPDATE counts as already set. F1's own row is not touched. Then the CDX lines are previewed:
dealt = FRED proxy + basis + half-spread.

#### 5.4.8 OTC validation (a quote that fails is never accepted)

- Kind, side, tenor, notional or units equal the line.
- The reference price or proxy carries its source and time and is fresh: an equity mark at most 15 minutes old in session; the FRED proxy at
  most 3 business days old; the IRS curve of the last business day; FX spot within 120 s and RFRs within their age limits (1.0.6: a daily
  rate 10 days, a monthly average 62 days after its month).
- **Dates come from the app, never from this plan's tables.** The EUR spot and value dates are the preview's (1.0.6.1 `ccycal`, TARGET2 +
  New York); the script checks that each is a business day in both. The JPY value dates are sent explicitly and checked against the
  script's Tokyo table and the New York calendar (D12); the Culture Day case (a 10-30 trade, spot 11-04) is one of the tooling's test
  vectors. The IRS start date is the preview's (spot, two New York business days).
- IM = the schedule percentage x notional (TRS 10 %, CDX 2 % / 6 %, forwards 6 %; IRS |DV01| x 50 bp).
- CDX series S47, maturity 2031-12-20; the TRS underlying passes the stale-listing check; funding ok without PB financing.
- The labels the line expects are present (MODEL; PROXY for the EUR curve; LIMITED for the JPY forwards and the 10Y IRS; ASSUMED for basis
  and dealer terms).

#### 5.4.9 `regt` (1.0.6 only)

- When: 10-29 15:45-16:30 ET, then each business day before 17:00 ET while shorts are open or filling.
- `GET /api/collateral/regt?account_id=I1:PB_GS` gives requirement, eligible collateral and deficit; transfer max(0, deficit x 1.05), rounded
  up to 100,000.00, from `I1:TREASURY` to `I1:PB_GS` (`POST /api/transfers`); re-read: no deficit. The 17:20 ET MARGIN_CALL job then records
  nothing, or a MET call.
- A margin call is always met: the D13 floor stops new lines, never a margin payment (a breach is reported, and the repo draw rule of 4.4
  applies).
- On 1.0.5 (no Reg T) the mode reports "not in this release" and does nothing.

#### 5.4.10 `fx`, IRS and the ORCL CDS (after their builds)

- **FX (1.0.6.1; orchestrator note b):** before anything, the orchestrator's check: the JPY rate's age (`tables.rate_age` under 1.0.6's limit)
  and the pricing path (the JPY preview's method label reads the legacy method and the expected rate series); a failure leaves 14.4-14.6
  UNEXECUTED with the exact reason, and 14.1-14.3 still go. The EUR lines: kind FX_FORWARD, pair EURUSD, side SELL, tenor 3M / 6M / 1Y,
  notional = 16,666,666.67 / the EURUSD mid at the run (to the cent), `settlement_funding {"EUR": "BUY_WITH_USD"}`, `client_ref`
  `V3I-1028-14.n`. The JPY lines: pair USDJPY, side BUY, notional USD 16,666,666.67, explicit `value_date` per D12, `settlement_funding
  {"JPY": "BUY_WITH_USD"}` only if the preview accepts it (3.12). FX_CCS_PLAN's `otcplan` program serves the owner's F1 trades; this plan's
  rates-scope request R-E (any PB account) was not taken (6.1), so the script's own RFQ loop (5.4.6) books the six lines with the same
  `client_ref`s and the validation of 5.4.8.
- **IRS (1.0.8, V-R):** `otc` mode, terms `pay_fixed` true, `tenor_months` 24 / 60 / 120, `curve` SOFR_FUT, `fixed_months` 12, ACT/360,
  `fixing` COMPOUND_ARREARS, spot start; validation adds the curve stamp, the par rate from the SOFR curve and LIMITED on the 10Y. If the owner
  chooses to book now (Q4), the same lines go on the Treasury-par model, labelled MODEL - LIMITED.
- **ORCL CDS (1.0.7; orchestrator note a):** only after the owner has given his own negotiated mid for ORCL, entered by him or given in
  words and labelled as his level (CDS plan 9.5 step 7). The script never sets a MODEL confirmation for ORCL; without a level the dealing-level
  guard refuses the deal (`DEALING_LEVEL_UNCONFIRMED`) and the line stays UNEXECUTED with that reason. Then `otc` mode, kind CDS, ORCL, 5Y,
  BUY_PROTECTION, 25,000,000.00, coupon 100 bp; validation adds the owner-level label, the MODEL spread beside it, dated inputs, and the
  reference obligation of 3.4 in the run report.

#### 5.4.11 `report` (R0: every evening after 17:30 ET and on demand; read-only)

- **Blotter per line:** target; submitted (orders or contracts found by request id or `client_ref`); filled quantity and average price;
  exposure now (V3 measure); delta-adjusted exposure (options); premium paid; IM and collateral; unrealised and realised P&L
  (`GET /api/portfolio?book=I`, `/api/accounts/{id}/positions`); status (pending, partial, filled, blocked, held, needs build, superseded,
  unexecuted) with the reason, including the app's own rejection records and any flagged option fill (5.4.3).
- **Fund:** sleeve totals against USD 3B and the gap; cash unrestricted (unencumbered) / restricted / reserved (Reg T) / IM / VM / collateral
  at the lender; NAV; leverage (V3 gross / NAV, the app's headline gross / NAV, gross notional / NAV).
- **Directional Options P&L** per strategy A-H and D': value at the Cboe mids, P&L since inception, Greeks, the exit criteria and whether one
  is hit (inform only).
- **Stress:** the 4.5 table recomputed on the actual positions (labelled planning-grade), with the due-time view: same-day calls by
  counterparty against cash plus same-day repo on settled face; the next-morning calls against what is left plus the overnight resources.
- **Shared-name pacing:** for each child-order line, the share of each bar's cap I1 took and the longest wait a later F1 / P1 order on that
  name could have had (D18).
- **Proof:** F1 / P1 untouched and integrity (5.6).
- Files `institutional\reports\<yyyy-mm-dd>\report.json` and `summary.txt`; the orchestrator sends the summary to the owner.

### 5.5 Price, limit and size rules at a glance

| Class | Size | Limit | TIF | Drift gate |
|---|---|---|---|---|
| Futures | round(target / (multiplier x last)) | last +/- max(2 ticks, 0.10 %), marketable | DAY: the group's current session (Globex to 17:00 ET; grains sent in the day session, to 14:20 ET) | 3 % |
| Equities / ETFs resting overnight | 100-share lots at the 10-28 close; large tickets as their first child | BUY at most close x 1.01; SHORT at least close x 0.99 | DAY | - |
| Equities / ETFs in session | 100-share lots at the fresh last; large tickets one child per pass (D18) | last x (1 +/- 0.005) | DAY | 4 % against the 10-28 close: HELD_DRIFT |
| Options | from the strategy delta on the fresh chain (a spread: legs gross), at most 25 % of OI; none without a displayed size; wide legs in children no larger than the displayed size | spread at most 10 % of mid: ask (BTO) / bid (STO); wider: mid + 0.25 x spread, at most 1.10x the 10-28 ask | DAY | underlying 5 %; D': chain of the latest session |
| Treasuries | floor(target / dirty / 1,000) x 1,000 | the app's mark + half-spread (no limit field) | immediate | yield 15 bp |
| TRS | floor(target / mark) units | the quoted reset price within 1 % of the fresh mark | - | 4 % |
| CDX | the notional | proxy + basis + half-spread | - | proxy at most 3 business days old |
| IRS | the notional | SOFR par + half-width | - | curve of the last business day |
| FX forwards | the USD notional (EUR from the mid) | K = F -/+ 2 bp of F | - | spot 120 s; rates within 1.0.6's age limits |

### 5.6 Checks after every stage (a failure stops the run before the next stage, exit 1)

1. **Integrity:** `GET /api/accounting/integrity`, every check ok (L1-L25).
2. **Cash at or above zero:** every I1 account's settled cash at or above zero, or covered by the app's open funding-shortfall rule (L18); no
   open FUNDING_SHORTFALL or SETTLEMENT_UNFUNDED attention on an I1 account.
3. **Floor:** D13 on the after-stage figures.
4. **F1 and P1 untouched:** a structural fingerprint of each book before and after the stage: order ids with status and filled quantity;
   positions (instrument, quantity); OTC contracts (id, status, units / notional); borrows (id, quantity, status); transfer ids. Cash and
   marks are left out (they move with the books' own lifecycle). Any difference must be one of those books' own events (their fills, VM,
   accruals), else the run stops. This proves the ledger state only; fill priority on shared names is D18's job and R0 reports it. After
   the 10-29 and 10-30 stages the orchestrator also runs a read-only check on a fresh `mode=ro` backup copy: every journal line of every
   command whose request id starts with `v3i1028` is on an I1 account; then the copy is deleted.
5. **I1 consistency:** one request-id family per line; no AVB; no option strategy over its delta target; no ITB series outside D19.

### 5.7 Refusals become blocked lines; nothing is fabricated

- Every refusal (a preview blocker, a 4xx, a gate) becomes the line's status in the run report, `BLOCKED_<code>` (or `HELD_<reason>` for a
  check that may pass later) with the app's message, and it stays in the blotter's blocked queue. The app keeps its own record too (order
  rejections, TX-7; RFQ refusals as ticket rejections in 1.0.6).
- A line without a fill is never shown as filled; a partial fill shows the filled part only; an OTC line counts as booked only once it has a
  contract id.
- V3 section 17's queues: unfilled orders stay in the app's Pending Trades; fills appear in Completed Trades; held, blocked and needs-build
  lines live in the blotter's clearly labelled blocked queue (R0 now, V-B in 1.0.8).

### 5.8 Order of sleeves and windows

| # | Stage (mode) | Window (server ET) | Release | Note |
|---|---|---|---|---|
| 1 | `check`, all sleeves | tonight, before any send | live | baseline, fingerprints, D13 projection |
| 2 | `futures`: metals, energy, index (10.1-10.7, 11.x) | tonight 21:00-00:30 ET | live 1.0.5 | executable now; the deferred CL legs fill mostly in the day session |
| 3 | `equities` (shorts, then longs; large tickets as their first child), resting for the open | tonight by 00:30 ET; or 08:00-09:25 ET Thursday | live | D18 |
| 4 | `options` (A, C, D, E, F, G, H, then B's long leg), resting | tonight by 00:30 ET; or 08:00-09:25 ET | live | the 10-28 closing chains; no-size legs held; ITB not sent |
| 5 | `bonds` | Thu 08:00-16:55 ET | live | |
| 6 | `futures`: grains (10.8-10.10) | Thu 09:30 ET (day session to 14:20) | live | |
| 7 | `pass` | Thu 09:25, 10:15, 11:00, 12:00, 13:00, 14:00, 15:00, 15:40 ET (foreground, no timers) | live | the next child orders; an off-market limit is cancelled, re-read and re-sent as `:r<n>`; B's 640 P; D' if a current chain and the checks allow |
| 8 | `regt` | Thu 15:45-16:30 ET | 1.0.6 | |
| 9 | `report` (R0) | Thu after 17:30 ET | live | owner summary |
| 10 | `pass` | Fri 09:25-15:40 ET as on Thursday (and each later session while a child line is open) | live | GXO, IBIT and the other child lines; grains remainders; options remainders |
| 11 | `cdxterms`, then `otc` (TRS 3.1-3.14, CDX 4.1, 4.2, 4.3a) | **Fri 10-30 10:00-15:30 ET**, once the face has settled | 1.0.6 RFQ (1.0.6.1 if deployed), else 1.0.5 direct | D13 with repo capacity |
| 12 | `fx` | first FX business day after the 1.0.6.1 deploy and the owner's F1 FX booking, 08:00-16:30 ET | 1.0.6.1 | the orchestrator's JPY checks first |
| 13 | ORCL CDS (`otc`) | after the 1.0.7 deploy and the owner's level, 10:00-15:30 ET | 1.0.7 | |
| 14 | IRS (`otc`) | after the 1.0.8 deploy, 10:00-15:30 ET | 1.0.8 (V-R) | or now on the Treasury-par model if the owner says so (Q4) |

### 5.9 Dry run on a served copy, then the live run

- **Build (tooling agent T, about 3.5-4.5 h):** `order_plan.json` and the script from sections 3 and 5; unit tests of the sizing (the 3.6
  floor faces, B's 772, the D18 child sizes), limit, request-id (the per-index cdxterms ids), signature (4.1 against 4.3a), D13 (revision
  1's breach before 3.4 NVDA with capacity 0; the 10-30 pass with capacity), the JPY value dates (the Culture Day vector) and the no-size
  hold; a mechanical check that every line id of section 3 is in the plan exactly once.
- **Dry run:** a fresh `mode=ro` sqlite3 backup of `%USERPROFILE%\.finsim\terminal.db` in a NEW folder under `scratchpad\checks\v3exec-<time>\`;
  `py -3.12 -m finsim serve --port <9420-9439> --home <folder>` from the live release's code (r105 tonight; 1.0.6 after its deploy); real
  data; free RAM at least 1.0 GB before starting; one server, no browser. Run `check`, `futures`, `equities`, `options`, then `bonds` and `otc`
  with `--window any`, `regt`, `pass`, `report`; then every mode again: zero new sends (idempotency). Stop the server with
  `POST /api/shutdown`, confirm its pid is gone, delete the folder.
- **Review:** one critic agent reads the script and the dry-run report before the first live send.
- **Live:** the orchestrator runs the stages of 5.8 against port 8765 and keeps each run report.

## 6. The builds FinSim1 lacks

**Rules for every package: FINSIM1_PLAN section 3 unchanged.** Ownership (edit only your files; edits to shared files go to the orchestrator
as anchored, idempotent hook scripts, the CDX / CDS precedent), both suites green on py-3.12 and py-3.14 (`tests\run_terminal_suite.py`),
hand-worked money tests with the arithmetic in comments, honesty (no invented prices, fills or balances; "unavailable" with the reason),
the UI rules, verification on fresh copies with one server and one browser per agent, never deploy / git / VERSION / `~\.finsim` / ports
8765-8865, one log line in the result. Builders re-read every file they own at the base commit.

### 6.1 The rates scope: not in 1.0.6.1; where each item went (V3-C-13)

Revision 1 asked the 1.0.6.1 pool for a rates scope (R-A..R-E). `docs\FX_CCS_PLAN.md` revision 2 (20:30 EDT) does not carry it, and the
orchestrator's 20:08 ET note keeps V3's JPY forwards on the legacy method. So nothing in V3 delays the owner's own F1 FX trades, and revision
1's open question ("may V3's IRS delay his F1 FX booking?") no longer arises. Each item now:

| Item | Revision 1 | Now |
|---|---|---|
| R-A TONA and a Tokyo calendar; R-B USDJPY forwards on `fxcurves`, JPY delivery funding | 1.0.6.1, P-A / P-B / P-D | **Not built by V3.** TONA, the BoJ source and a Tokyo calendar are already on the FX / CCS plan's own 1.0.6.2 list (its D2: "JPY ... wait for daily overnight-rate sources: the BoJ (TONA) ..."); V3 does not duplicate them. The facts of 1.6 (the BoJ files, the 10-27 final 1.227 %, the terms PDF) are handed to that item. Until then the JPY forwards use the legacy method (D12) |
| R-C the SOFR futures curve and SOFR-swap conventions for new IRS | 1.0.6.1, P-A + P-C | **Package V-R of 1.0.8** (6.2), same design, with the SR3 storage and liquidity rules below. The IRS book after the 1.0.8 deploy (Q4) |
| R-D the stale-listing guard for TRS | 1.0.6.1, P-C | **V-X of 1.0.8**, with the order guard: one helper (K-V5) for both. Until then the script's own check (5.3 gate 7) |
| R-E `otcplan` for any PB account | 1.0.6.1, P-D (optional) | **Dropped.** The script's own RFQ loop books the OTC lines with `client_ref` (5.4.6, 5.4.10) |

**V-R's SR3 storage and its use (V3-C-13 a, b).**
- `curve_points` is `(series, as_of, tenor, value, fetched_ts, source)` with no place for a quote time or volume, and `db.py` has no owner in
  this pool, so V-R's module creates its own table through `ensure_schema_ext` (the `cdx_terms` precedent): `sofr_fut_quotes (contract TEXT,
  trade_date TEXT, price TEXT, implied_rate TEXT, quote_ts INTEGER, yahoo_volume INTEGER, fetched_ts INTEGER, source TEXT, PRIMARY KEY
  (contract, trade_date))`, with `quote_ts` the last print's time.
- Yahoo's SR3 volumes are a partial feed, orders of magnitude below CME's (Z26: 3,778 lots reported on 10-28; 5,576 by 19:40 ET): stored for
  information, labelled "Yahoo-reported, partial", and **never used as a liquidity test**. The prices are consistent across months
  (Z26 95.815, U27 95.365, Z28 95.45, Z31 95.365). A month counts as quoted when its last print (`quote_ts`) is within 3 sessions; a month
  without one is dropped and interpolated, labelled; with fewer than 8 quoted months to 2Y the curve answers NO_CURVE.
- The rest of R-C is revision 1's text: `sofrcurve.py` (overnight stub from SOFR; IMM-to-IMM quarters at the implied rates less the
  convexity term 0.5 x sigma^2 x t1 x t2, `sofr_fut_vol_bp` ASSUMED seed 90; beyond the strip the Treasury-par slope, LIMITED); in
  `prime.FIELD_SPECS` `irs_curve` (UST_PAR | SOFR_FUT, seed UST_PAR everywhere) and `sofr_fut_vol_bp`; a new IRS stamps `terms.curve` and
  every mark, DV01, VM and termination uses its own stamp, so F1's SWP-000003 stays byte-identical (regression test); with SOFR_FUT: annual
  ACT/360 fixed, spot start two New York business days (`ccycal`), payment two business days after each period end with the SOFR Index on
  the float leg, modified following; money test: a hand-worked 2Y on a synthetic strip; the 10-28 strip of 1.6 as a recorded fixture.

**One stale-listing helper (V3-C-13 c).** `instruments.last_trade_age_sessions(services, instrument_id) -> Optional[int]` (K-V5, owned by
V-X) is the only implementation; V-X's order guard and its TRS guard (`products\swaps_trs.py`, R-D's 409 STALE_LISTING) both call it. Scope:
exchange-listed equities, ETFs and REITs (an option's or a TRS's underlying of those kinds); instruments without exchange prints (bonds,
bills, OTC contracts, private marks, FX) return None and are never refused. Tests: AVB (last print 2026-08-14) refused in both paths; VMRK
passes; a Treasury and a private company pass.

### 6.2 Release 1.0.8: the V3 pack (5 packages, file-disjoint; at most 4 builders at once)

**Base:** the gated 1.0.7 commit (or 1.0.6.1 if 1.0.7 slips: the pack touches none of B1-B4's new files, and its edits to shared files are
hook scripts re-anchored at the base). **Waves:** V-S, V-B, V-X and V-R start together; V-U starts when V-R (the shortest) finishes, which
also lets the UI build on finished back ends. **Hours:** pool about 9.5-10.5 h of wall clock (about 30-34 builder-hours); integration
1-1.5 h; verification 2.5-3.5 h; at most two fix rounds 1.5-3 h; gate 1.5-2 h: **about 16.5-20.5 h to a deployed 1.0.8.**

| Package | Owns (new files unless marked) | Hook edits (applied by the orchestrator) | Hours |
|---|---|---|---|
| V-S stress engine | `stress.py`, `stress_scenarios.py`, `stress_betas.py`; tests `test_terminal_stress.py`, `test_terminal_stress_scenarios.py`, `test_terminal_stress_states.py`, fixtures | see "Hooks" below | 7.5-8.5 |
| V-B fund book | `fundbook.py`, `optionsbook.py`, `fundmetrics.py`; tests `test_terminal_fundbook.py`, `test_terminal_optionsbook.py`, `test_terminal_fundmetrics.py` | one ProductJob OPTBOOK_SNAPSHOT (16:30 ET) in the lifecycle registry; see "Hooks" | 7-8 |
| V-U interface (second wave) | `static\pages\institutional.js`, `static\components\stressview.js`, `static\components\optionsbook.js`; test `test_terminal_institutional_ui.py` | `index.html` script tags; the "Institutional" navigation entry (`core.js` / `sidebar.js`); a "Stress tests" link on the Risk page; see "Hooks" | 5-6 |
| V-X execution and master fixes | existing `orders_preview.py`, `instruments.py`, `products\options.py`, `matcher.py`, `orders_rules.py`, `strategies.py`, `products\swaps_trs.py` (the TRS guard only); test `test_terminal_v3_execution.py` (+ intended changes to the order tests) | none | 6.5-7.5 |
| V-R SOFR curve (R-C) | new `sofrcurve.py` (with its `sofr_fut_quotes` table); existing `prime.py` (two FIELD_SPECS), `products\swaps.py` (the IRS curve stamp and SOFR conventions); test `test_terminal_sofrcurve.py`, the IRS money and regression tests | see "Hooks" | 4-4.5 |

None of V-X's or V-R's files is owned by a 1.0.6.1 or 1.0.7 package at the base (the CDS plan's B2 hook in `instruments.py` and its hooks in
`swaps.py` / `prime.py` land earlier, in 1.0.7; V-X and V-R re-read every file they own at the base commit).

**Hooks (V3-C-06; anchored at the base commit, the CDS precedent H4-1 / H4-2 / H4-4).** `server.OPTIONAL_MODULES` is a dict of `services`
attribute to module path, and a module that is not listed is never set on `services`; so every module another one calls through `services`
is listed:
- `server.OPTIONAL_MODULES` gains `"stress": "stress", "stress_betas": "stress_betas", "fundbook": "fundbook", "optionsbook": "optionsbook",
  "fundmetrics": "fundmetrics", "sofrcurve": "sofrcurve"` (`stress_scenarios` is imported by `stress` itself and needs no entry);
- `api.PACKAGES` gains `"stress": "V3-S", "stress_betas": "V3-S", "fundbook": "V3-B", "optionsbook": "V3-B", "fundmetrics": "V3-B",
  "sofrcurve": "V3-R"`;
- `api.PLANNED_STATIC` gains `"pages/institutional.js", "components/stressview.js", "components/optionsbook.js"`;
- `tests\terminal_fixture_server.py` `OPTIONAL_STATIC` gains the same three paths (comment `# Institutional V3 (1.0.8)`);
- `static\index.html` gains their three `data-optional="1"` script tags.
A test in each package asserts that `services.<attr>` is bound for each of its modules on a served fixture (K-V3 and K-V4 would otherwise
answer "unavailable" after integration).

#### V-S: the stress engine (V3 section 16)

**Inputs.** A scope (a lifetime such as `I1:ALL`, or one account); scenario ids; mode **LIVE** (booked positions on one mark snapshot, the
`risk.report` rule) or **PLAN** (a loaded mandate's planned lines at their reference prices and Greeks, labelled "planned book (not
booked)"); `horizon_days` (1 by default; N spreads the shock evenly over N days with theta and accruals, for a daily path).

**Scenario schema** (`stress_scenarios.py`; every value PROPOSED and editable):
- equity shocks by name, or by group (TECH, NDX, SPX, RTY, HOUSING, DATACENTRE, FINANCIALS, CRYPTO) times each name's beta to the group's
  proxy (QQQ, SPY, IWM, XHB ...) from `stress_betas`: OLS on at least 250 daily Yahoo returns, with R-squared, window and date, labelled
  MODEL; **a name with fewer than 250 returns gets beta 1.0, labelled "short history: beta 1.0"** (SPCX: about 95 sessions, master beta
  3.226: a capped one-year OLS would credit its USD 90M of longs with about +75 % in a Nasdaq +30 % shock); an owner override wins; the
  cap is 0-2.5;
- rates: parallel bp on the Treasury par curve and on the SOFR curve, separately;
- credit: IG and HY spread moves (bp or %), single names through the 1.0.7 model at the shocked equity price;
- commodities by root (GC, CL, HG, SI, ZC, ZS, ZW) and the commodity ETFs (GLD, USO, PDBC) by beta;
- FX by currency against USD; implied vol by underlying group (points); an option "premium decay" shock applied to **each strategy's net
  value** (a spread loses that fraction of its spread value, not of its long leg; a single option of its own value);
- the margin regime: TRS IM %, CDX IM %, CDS IM multiple, IRS IM bp, forward IM %, Reg T %, borrow collateral %, futures IM multiple, repo
  haircut, CSA haircut;
- liquidation terms: a listed equity / ETF sale costs 1 % of its price and is limited to 10 % of ADV a day; long options sell at the bid;
  long futures close and free their IM.

**The ten V3 scenarios (PROPOSED defaults), plus the spiral and the reverse stress:**

| # | Scenario | Default shocks |
|---|---|---|
| 1 | Oracle rallies 50 % | ORCL +50 %; ORCL options re-priced; the ORCL CDS re-priced by the model at the new equity price |
| 2 | Nasdaq rallies 30 % and credit spreads tighten | QQQ / NQ +30 %; every US equity +30 % x its beta to QQQ (the 250-return rule); CDX IG -30 % of spread, HY -30 %; equity IV -3 points |
| 3 | Bitcoin rallies 75 % | IBIT +75 % |
| 4 | Housing and homebuilders rally 30 % | XHB, ITB, LEN, DHI +30 %; VMRK +30 % (apartment REIT, PROPOSED as housing); other names x their beta to XHB |
| 5 | Oil jumps 40 % | every CL month +40 %; USO +40 %; PDBC x its beta to USO |
| 6 | Gold falls 20 % | GC and GLD -20 % |
| 7 | Rates fall 150 bp | Treasury par and SOFR curves -150 bp parallel; TLT re-priced from the 20Y+ yield (duration and convexity, MODEL) |
| 8 | All bearish theses against the fund at once | 1 + 2 + 3 + 4 + 5 + 7; DLR +30 %; C and STT +20 %; EUR and JPY +10 % against USD |
| 9 | Options lose 50 % of premium (time decay or vol compression) | every option strategy's net value -50 % |
| 10 | Prime-broker collateral requirements rise sharply | TRS IM 20 %, CDX IM 5 % / 15 %, CDS IM x 2, IRS IM 100 bp x DV01, forwards IM 12 %, Reg T 100 %, borrow collateral 105 %, futures IM x 1.5, repo haircut 10 %, CSA haircut 10 % |
| 8 + 10 | the liquidity spiral | both at once |
| R | reverse stress | the smallest k (bisection on 0-5, step 0.01) at which scenario 8 x k reaches MARGIN_DEFAULT, and INSOLVENT; with and without the scenario 10 regime |

**Re-pricing** through the app's own functions (read-only): equities and ETFs linear; listed options through `marks.price_option` at the
shocked spot, vol and time; futures linear (variation margin); TRS units x the price change; CDX through the `swaps_cds` pure functions at
proxy + basis + shock; single-name CDS through `cds_model`; IRS through the contract's own mark with `shift_bp` on its stamped curve;
Treasuries through `bonds.reprice_c`; FX forwards re-marked by their own pricing path at the shocked spot; repo collateral re-marked at the
haircut. Offsets are shown per line (which longs, VM receipts and option gains reduce each call).

**Calls by counterparty, each with its due time (the app's, `collateral.py`):** GS under the CSA: net OTC VM at 17:05 ET the same day; the
FCM: futures VM at the settle the same day; the stock lender: the collateral mark at 17:10 ET the same day; IM increases under the CSA and
at the FCM, and the Reg T deficit: 10:00 ET the next business day; repo dealers: collateral marks and haircut changes at 17:15 ET.

**Resources by due time (the waterfall):**
1. by the same day's calls: unencumbered cash; then repo drawn in that day's bond session (08:00-17:00 ET) on face settled by then, at the
   scenario haircut;
2. by 10:00 ET the next business day: what is left of those; plus repo on face settling overnight; plus the proceeds of listed sales made on
   the shock day within the day's limits (equities / ETFs at a 1 % cost and 10 % of ADV, long options at the bid), which settle T+1
   overnight (SETTLE_DUE); plus the IM freed by closing long futures; OTC terminations (pay the mark, free the IM) last.
T+1 proceeds and unsettled bonds never meet a same-day call. A same-day call met only overnight is flagged OVERDRAFT_OVERNIGHT with the
counterparty and the amount: the app takes VM into an overdraft (FUNDING_SHORTFALL) and the call stays OPEN until 10:00 ET next day; a real
CSA would treat a late transfer as a failure to deliver after its grace period, and the report says so.

**States, explicit:** OK (every call met from unencumbered cash by its due time, the USD 150M floor kept) / REPO_REQUIRED (met by its due
time only with repo) / LIQUIDATION_REQUIRED (met only with sale proceeds by 10:00 ET the next day; with the list of what is sold, in the
waterfall's order) / MARGIN_DEFAULT (a call unpaid at its final due time after every resource: the unpaid counterparty, the amount and the
due time; calls are paid in due-time order, GS CSA VM, FCM VM, lender, then the next-morning CSA IM, FCM IM and Reg T) / INSOLVENT (NAV after
the shock at or below zero).

**Outputs per scenario:** the day's P&L by sleeve, by sub-ledger and by options strategy (A-H, D'); NAV before and after; gross and net
exposure and leverage after (V3 measure and the app's headline); option premium at risk (premium paid, value before and after); calls by
counterparty and due time; resources by due time; repo haircuts and capacity before and after; the liquidation requirement; the state;
every parameter with its label. Scenario 8 also as a 5-day path (daily calls against resources).

**Honesty.** LIVE uses booked positions only and never assumes protection that is not booked; PLAN says so on every number; private marks and
private loans are not shocked (the `risk.py` rule); a position the engine cannot re-price is listed as unpriced, never valued at zero.

**Storage and routes.** Tables `stress_params` (scenario id, params JSON, version, updated) and `stress_runs` (run id, time, scope, mode,
params hash, result JSON). Light commands `stress.params {scenario_id, params}` (one SETTINGS_CHANGED event with before and after) and
`stress.run {scope, scenarios, mode, horizon_days}`. Routes: GET `/api/stress/scenarios`, PATCH `/api/stress/scenarios/{id}`, POST
`/api/stress/run`, GET `/api/stress/runs` and `/api/stress/runs/{id}`.

**Money tests (hand-worked, the arithmetic in comments; `test_terminal_stress_states.py` drives every state):**
- a seeded book with one short TRS, one long put, one long future, one short stock with its borrow, one unsettled and one settled Treasury,
  one CDX: every scenario's P&L, calls by counterparty and due time, the waterfall and the state;
- one fixture per state: OK; REPO_REQUIRED (a same-day call above cash, below cash + same-day repo); LIQUIDATION_REQUIRED (a next-morning
  call needing T+1 sale proceeds); OVERDRAFT_OVERNIGHT (a same-day call whose only cover is an unsettled bond); **MARGIN_DEFAULT naming the
  unpaid counterparty, the amount and the due time** (calls above every resource); **INSOLVENT** (a shock beyond NAV);
- the reverse-stress k with and without the scenario 10 regime, on a book where both points are hand-computable;
- scenario 9 on a vertical spread: half its net value, not half its long leg;
- the beta rule: a 95-return name gets 1.0, a 260-return name its OLS beta (capped);
- parity with the planning table of 4.5 on the PLAN of section 3 (delta-one lines exact; options within 5 %).
- **At the 1.0.8 gate** (verification, below): on the fresh copy of the live I1 book, the spiral (8 + 10) and the reverse stress in LIVE mode
  produce their states, with the counterparties and due times shown.

#### V-B: the fund book (V3 sections 15, 17 and 18)

1. **Mandate.** Tables `fund_mandates` (book, mandate id, version, title, source document, loaded time, sha256) and `fund_mandate_lines`
   (line id, sleeve, class, account, instrument or kind, side, target USD, measure, strategy, thesis, exit JSON {take_profit, stop,
   time_exit}, status, reason, request-id prefix, `client_ref`, reference data such as the ORCL CDS reference obligation). Light command
   `fund.mandate.load {book, mandate_id, version, lines}` (idempotent by version and sha256; one SETTINGS_CHANGED event). The orchestrator
   loads the V3 mandate from `order_plan.json` after the deploy: a document, not a trade.
2. **Linking.** Orders by request-id family (children and re-sends included); OTC contracts by `client_ref`, else by the request id of their
   booking command (the `commands` table maps it to the contract in its result); bond fills likewise; anything else by
   `fund.mandate.link {line_id, ref_type, ref_id}`.
3. **Institutional blotter** (V3 section 18), per line and per sleeve: target exposure, submitted exposure, filled exposure (quantity, average
   price), gross notional, delta-adjusted exposure, premium paid, margin required (futures IM, OTC IM, Reg T share, borrow collateral),
   unrealised and realised P&L, financing to date, status and reason; every figure labelled observable / model / proposed; the blocked queue
   (held, blocked, needs build, superseded, unexecuted, and the app's rejection records).
4. **Fund reconciliation:** NAV; cash by account (settled, restricted, reserved, unrestricted); IM / VM / collateral by counterparty (from
   `collateral.summary`); gross and net exposure (both measures); leverage; the check NAV = the sum of the sub-ledgers; the six sub-ledgers of
   D15 (the FX sub-ledger reads the SFI deals in `I1:PB_GS`).
5. **Directional Options P&L** (`optionsbook.py`), per strategy A-H and D': legs, contracts, premium paid with fees, value at the Cboe mid
   (observable) beside the model value, unrealised and realised P&L, P&L as % of premium, delta-adjusted exposure now (legs gross, the V3
   measure) and net delta against the target, dollar delta / gamma per 1 % / vega per point / theta per day, days to expiry, break-even and
   the distance to it, an **exit monitor** (take-profit, stop and time-exit conditions evaluated on marks: HIT, NEAR within 5 %, or no;
   inform only, never trades; one INFO attention per hit), and **performance attribution** day over day from the OPTBOOK_SNAPSHOT rows
   (delta, gamma, vega, theta, residual). Never a hedge-effectiveness rating.
6. **Per-fund metrics** (`fundmetrics.py`): net equity delta (USD, % of NAV) and beta-adjusted delta (K-V3), by group; DV01 (bonds, IRS, rates
   futures, TLT options through an assumed duration, labelled) and its net; CS01 (CDX, CDS); gamma, vega and theta totals; financing
   commitments per day and to maturity (TRS financing, dividend equivalents, borrow fees, rebates, repo interest, PB debit, CDX / CDS running
   premium, IRS net carry, Treasury coupons, cash interest); FX by currency; concentration by underlying (the 3.2 table, live).
7. **Routes:** GET `/api/fund/mandates`, `/api/fund/blotter`, `/api/fund/reconciliation`, `/api/fund/options-book`, `/api/fund/metrics` (each
   `?book=`); POST `/api/fund/mandates` and `/api/fund/mandates/{id}/links`.
8. **Money tests:** a seeded book with mandate lines and fills: blotter totals equal the ledger; the reconciliation sums to NAV; attribution
   sums to the day's option P&L within its residual; the exit monitor fires on a crossing; a line with child orders links every child.

#### V-U: the Institutional page

Tabs: **Blotter** (by sleeve, filters, the blocked queue), **Reconciliation** (cash, collateral, NAV, leverage, sub-ledgers), **Options book**
(the A-H and D' table, exit monitor, attribution), **Stress** (scenarios with their parameters and labels, owner edit, run, results: P&L by
sleeve, calls by counterparty and due time, the waterfall, state chips, the reverse stress), **Metrics**. Visible for every book with an
empty state when no mandate is loaded. FINSIM1_PLAN 3.5: vanilla JS, ASCII, no CDN, 1440 px and 1100 px, 0 console errors, every route
behind `T.hasModule`.

#### V-X: execution and master fixes

1. **Stale-listing guards, one helper:** `instruments.last_trade_age_sessions` (K-V5; 6.1). BUY, SELL_SHORT and BTO on an exchange-listed
   equity / ETF / REIT (or an option's underlying of that kind) whose last observed trade is older than 5 NYSE sessions answer 409
   STALE_LISTING in preview and submit; closing sales stay allowed (labelled). The TRS preview and booking refuse such an underlying the
   same way (R-D, in `products\swaps_trs.py`). Instruments without exchange prints are exempt.
2. **Audited master corrections:** an owner command `instrument.correct {instrument_id, status?, name?, evidence}` (one event with before,
   after and the evidence; refresh jobs never overwrite an owner correction). After the deploy the orchestrator applies: AVB status DELISTED
   (EDGAR 8-K 2026-08-17, Form 25-NSE, merged into VMRK at 2.793); VMRK name "Vivmark Residential" (EDGAR CIK 906107). The SPCX aliases are
   checked against EDGAR and removed when unverified.
3. **Chain pinning:** an option contract with a working order or an open position stays listed and resolvable when its underlying's chain
   snapshot leaves the 2-entry chain cache (the NOT_LISTED defect, 5.4.3).
4. **Size-aware option fills** (V3 section 13.8): within one chain observation the displayed size is consumed across orders (as EXEC-6 does
   for a bar's volume); a row with no displayed size fills nothing (instead of the whole remainder); an owner setting caps a contract's fills
   per session at a share of the prior day's open interest (`option_oi_session_cap`, PROPOSED 25 %, 0 = off); it paces fills and refuses
   nothing; the fill memo says so. **This changes F1's and P1's option fills too** (every book shares the matcher): the owner is told before
   the gate, the change is listed in the release notes, and the cap can be set to 0.
5. **AON slicing:** the strategy path may submit an option package as child all-or-none packages no larger than the smallest displayed size of
   its legs at each observation (opt-in flag `slice`), so B can trade as one strategy.
6. **The strategy preview takes spot from the app's mark,** not from the Cboe chain's underlying field (QQQ 718.85 against 725.61; ORCL 140.30
   against 144.54).
7. **Money tests:** two orders against one observation with 50 displayed fill 30 + 20, not 50 + 50; a no-size row fills 0; a 772-lot spread
   sliced across observations; AVB refused by the order and TRS paths with one helper; a bond and a private mark not refused; the correction
   command's event.

#### Contracts between the packages (built first by their owner)

| Id | Owner | Contract |
|---|---|---|
| K-V1 | V-S | `services.stress.run(scope, scenarios, mode, horizon_days, plan=None) -> dict` (the outputs above); `scenarios()`, `params(id)`, `STATES` (OK, REPO_REQUIRED, LIQUIDATION_REQUIRED, MARGIN_DEFAULT, INSOLVENT; flag OVERDRAFT_OVERNIGHT) |
| K-V2 | V-B | `services.fundbook.mandate(book)`, `plan_positions(mandate)` (PLAN-mode rows with reference prices and Greeks), `blotter(book)`, `reconciliation(book)`, `subledgers(book)` |
| K-V3 | V-S | `services.stress_betas.beta(instrument_id, proxy) -> {beta, r2, window, n_returns, as_of, label}` (beta 1.0 and the "short history" label below 250 returns) |
| K-V4 | V-B | `services.optionsbook.view(book)`, `services.fundmetrics.view(book)` |
| K-V5 | V-X | `instruments.last_trade_age_sessions(services, instrument_id) -> Optional[int]` (None for instruments without exchange prints); `STALE_LISTING_SESSIONS = 5` |
| K-V6 | V-R | `services.sofrcurve.curve(as_of=None) -> {dfs, par(tenor_months), record, label}`; `NO_CURVE` when fewer than 8 quoted months reach 2Y |

#### Integration, verification and gate of 1.0.8

- **Integration** (orchestrator): the hook scripts above (re-anchored at the base); both suites green; each package's `services.<attr>`
  binding test passes on the integrated tree.
- **Verification** (a P8-style agent): the suites; a fresh `mode=ro` copy of the live database served online (one server, ports 9420-9439):
  load the V3 mandate, check the blotter against the live I1 book (every booked line and child linked), the reconciliation sums to NAV, the
  options book by strategy, every scenario plus the spiral and the reverse stress in LIVE and PLAN modes with their states, due times and
  waterfalls (the spiral and the reverse stress must reach their states on the live book), AVB refused in both paths, VMRK fine, the
  correction command, chain pinning, a size-aware fill on a real chain, the SOFR curve on the day's strip and F1's SWP-000003 unchanged;
  F1 and P1 marks, VM and accruals identical to 1.0.7 on the same copy and day, apart from the option-fill rule of V-X item 4 (listed for
  the owner); a Playwright walk of the Institutional page at 1440 and 1100 px with 0 console errors.
- **Gate:** FINSIM1_PLAN section 12 (VERSION 1.0.8, CHANGELOG, commit, archive verify, release, deploy under the standing pre-approval).
  Then the orchestrator loads the mandate, applies the two master corrections and books the IRS on the live app; the in-app engine and
  blotter replace R0.

### 6.3 Not built, with reasons

- **iTraxx Europe Main:** no free source (DTCC refused by its terms; no free euro IG OAS); no product without a price. The owner redirected
  its USD 50M to CDX.NA.IG (4.3a).
- **TONA, the BoJ source and a Tokyo calendar:** on the FX / CCS plan's own 1.0.6.2 list (6.1); V3's JPY forwards use the legacy method
  until then (D12).
- **The one-lot floor of the futures participation cap:** not changed (it would change F1 / P1 fills too); disclosed (3.7, R16).
- **New account roles** (futures clearing, short financing): sub-ledgers instead (D15); a 1.0.9 item if the owner asks (Q7).
- **A size-dependent bond spread:** disclosed limitation; little effect on a buy-and-hold sleeve.
- **Cumulative Form SHO and 13D / G measurement:** the master has no shares outstanding; notices stay per order (INFO).
- **More commodity roots and CL months after Mar-27:** the chosen contracts do not need them.
- **Cash IM released against CSA Treasury pledges:** cash is ample.
- **A cross-currency basis for the forwards:** no free source.
- **Physical delivery and first-notice logic for futures:** the plan closes or rolls before the app's roll dates (7).
- **Early assignment of B's short 640 P:** B is closed before 2027-03-12 by its exit rule.

---

## 7. Schedule

**Tonight, Wed 10-28 (ET)**
- 19:15-20:50: critic of this plan, then this revision (the workflow).
- About 21:00-00:00: tooling agent T builds `order_plan.json` (version 2) and the script, dry-runs it on a served copy (r105 code; free RAM
  permitting), critic.
- About 00:00-00:30: the orchestrator runs `check`, `futures` (10.1-10.7, 11.x), then `equities` (large tickets as their first child) and
  `options` (A, C-H, B's long leg) as LIMIT DAY orders resting for the open. If the tool is not ready by 00:30, everything moves to
  08:00-09:25 ET Thursday (Globex stays open; the resting orders go in before the open).
- 00:30-01:50: no Python on this machine (the futures-capture guard window).
- Overnight: the 1.0.6 gate and deploy (orchestrator; standing pre-approval); resting orders survive the restart. The deferred CL legs
  barely trade overnight (3.7).

**Thursday 10-29**
- 08:00: `bonds` (the six Treasuries; settle 10-30).
- 09:25, 10:15, 11:00, 12:00, 13:00, 14:00, 15:00, 15:40: `pass` (child orders, reprices, B's 640 P; D' only under D19).
- 09:30: the grains in the day session; the resting orders fill at 10 % of each one-minute bar, the child lines at about half of it.
- 15:45-16:30: `regt` (1.0.6).
- 16:00: DLR reports after the close (its short and puts are opened into the print; its TRS books Friday, after it; Q15).
- 17:05 / 17:20: the first VM (futures, borrows); margin calls. After 17:30: `report` (R0) and the owner's summary.
- The 1.0.6.1 pool runs (FX / CCS only); its gate about 17-22 h after the 1.0.6 commit.

**Friday 10-30**
- Overnight the Treasuries settle: repo capacity exists from now on.
- 09:25-15:40: `pass` as on Thursday (GXO, IBIT and the other child lines; remainders).
- 10:00-15:30: `cdxterms` (IG, HY), then `otc`: the 14 TRS (SPCX included) and CDX 4.1, 4.2, 4.3a, each line through the D13 test with the
  settled face's repo capacity.
- If 1.0.6.1 is deployed and the owner's F1 FX trades are booked: `fx` (EUR lines; the JPY lines after the orchestrator's checks), 08:00-16:30.
  Otherwise Monday 11-02.
- After 17:30: R0 with the OTC lines.
- The 1.0.7 pool (single-name CDS) starts after the 1.0.6.1 gate (9.75-12.75 h); deploy about Saturday.

**Monday 11-02 and after**
- The CDX initial payments arrive (T+1). `pass` continues while a child line is open (IBIT into Monday at the default pacing).
- The ORCL CDS on 1.0.7, only when the owner has given his level (Q12).
- The 1.0.8 pool (about 16.5-20.5 h): deploy about Tue 11-03 to Wed 11-04; then the IRS on the SOFR curve, the mandate is loaded, the
  master corrected, and the in-app stress and blotter replace R0. (Q17 lets the owner run 1.0.8 before 1.0.7.)

**Calendar of the book**

| Date | Action |
|---|---|
| 11-12 | close or roll CLZ26 (app roll 11-13; last trade 11-20) |
| 11-20 | the Nov-20 ITB puts expire, if the owner allowed them (Q19) |
| 11-25 | roll GCZ26 into GCG27, HGZ26 into HGH27, SIZ26 into SIH27; roll or close ZCZ26 and ZWZ26 (app roll 11-28; first notice 11-30) |
| 11-30 | the first TRS resets (monthly) |
| 12-01 | the last day the September OECD JPY average passes 1.0.6's age rule (only matters if the JPY lines have not booked by then) |
| 12-08 / 12-09 | FOMC: a catalyst for A, B, F and H |
| 12-10 | roll ESZ26, RTYZ26, NQZ26 (app roll 12-11; expiry 12-18) |
| 12-11 | close the December option legs (D2, E1, E2, G1, G3); roll CLF27 by then (app roll 12-14) |
| 12-14 | Oracle FQ2 earnings (A) |
| 12-21 | the first CDX coupons |
| 12-28 | roll ZSF27 (app roll 12-29) |
| 2027-01-08 | close or roll the January option legs (A3, C1, D1, E3, G2 and any Jan-27 ITB legs) |
| 2027-01-12 | roll CLG27 |
| 2027-01-29 | the SFI buys EUR for the first EUR delivery of 02-03 (in `I1:PB_GS`); JPY for 02-04 if the SFI accepts JPY, else the owner's purchase |
| 2027-02-12 | roll CLH27 |
| 2027-03-12 | close strategy B |
| 2027-03-22 | the CDX roll to S48 (optional) |
| weekly | re-check the ITB chain (D19) and the DLR open interest for the remainders |

---

## 8. Owner questions (each has a default; none blocks the rest of the book)

| Q | Question | Default | Why it needs the owner |
|---|---|---|---|
| Q1 | SPCX: trade as the listed security? | **Answered 19:40 ET: "Trade as listed"** (both lines NEXT, exchange prices labelled) | - |
| Q2 | AVB or VMRK? | **Answered 18:30 ET: VMRK** for both lines (USD 65M TRS, USD 12M short) | - |
| Q3 | The I1 CDX basis | **Answered 19:40 ET: copy F1's** (IG -24.5 bp, HY +23.0 bp), labelled ASSUMED | - |
| Q4 | IRS: the SOFR futures curve is no longer in 1.0.6.1 (6.1); wait for 1.0.8's V-R (about Tue 11-03 to Wed 11-04), or book now on the Treasury-par model (MODEL - LIMITED, about 15-35 bp above the futures-implied SOFR swap rates)? | **Wait** (his earlier default, now two to three business days longer) | the wait got longer; the Treasury-par rate is knowingly off the free SOFR level |
| Q5 | Strategy B's USD 30M: net or gross delta? | **Answered 19:40 ET: gross across legs** (772 spreads) | - |
| Q6 / Q11 | The blocked exposure | **Answered 19:40 ET:** iTraxx USD 50M to CDX.NA.IG (4.3a; he may say HY instead); the XHB remainder to ITB puts (D', under D19); the DLR remainder stays blocked, re-checked after the 10-29 earnings | - |
| Q7 | Distinct accounts: sub-ledgers on the existing accounts, or new account roles (futures clearing, short financing) in a later release, re-opening the futures and shorts in them? | **Sub-ledgers** | new roles cannot hold positions booked before them |
| Q8 | **Re-opened.** JPY forwards: revision 1's default (build TONA and a Tokyo calendar) is not in 1.0.6.1. The orchestrator's binding note puts them on the legacy method (OECD monthly proxy 1.043 %, about 18 bp below TONA; LIMITED; Tokyo-valid dates sent explicitly). Book them that way, or hold them until TONA exists (the FX / CCS plan's 1.0.6.2)? | **Legacy method, if the orchestrator's age and path checks pass** (orchestrator, 20:08 ET) | his accepted default cannot be met in time |
| Q9 | FX forwards on the 1.0.6.1 curves (EUR on the ECB AAA proxy; MODEL, PROXY, no cross-currency basis), or block until a free basis exists? | **Book** (the FX / CCS plan's logic) | no free term curve or basis |
| Q10 | The liquidity floor: USD 150M unencumbered and 1.25x stress coverage? | **Yes** (it moved the TRS and CDX to 10-30) | his risk appetite |
| Q12 | **Changed.** The Oracle CDS books only at his own negotiated mid (CDS plan 9.5 step 7; orchestrator note a): what level? The model gives about 113-116 bp; public news put Oracle's 5Y CDS at 295-315 bp on 10-19 | **No level, no trade** (the line stays unexecuted, with that reason) | V3: "do not fabricate an executable CDS quote" |
| Q13 | Large tickets across sessions: DAY orders sized fresh each session, or GTC at a fixed limit? | **DAY, sized fresh** (now as child orders, Q18) | GTC would rest at a stale price |
| Q14 | Strategy B: leg it (long 700 P first), or child all-or-none packages? | **Leg it** | legging risk on the debit |
| Q15 | DLR puts: buy Thursday before the print (event IV), or Friday after it? | **Thursday** (as planned; the DLR TRS now books Friday, after the print) | event premium against the print |
| Q16 | Tonight: resting LIMIT DAY orders with a +/-1 % collar (equities) and the closing ask / wide-market rule (options), then the 10:15 pass; or send only after the open at fresh prices? | **Rest tonight** | gap risk against the certainty of being in the queue |
| Q17 | Build order: 1.0.6.1 -> 1.0.7 (CDS) -> 1.0.8 (V3 pack with V-R), or the V3 pack before the CDS release; or V-R alone as a small release first (the IRS about a day earlier)? | **As listed** (R0 covers the risk reports until 1.0.8) | the ORCL CDS against the in-app stress engine and the IRS |
| Q18 | **New (V3-C-04).** Large equity tickets: child orders at about half of each shared name's cap, so his own later F1 / P1 orders on those names interleave (IBIT about 2.3 sessions, GXO about 4.1), or single tickets (about half the time, but a later F1 / P1 order on IBIT, GXO, VMRK, LEN, KTOS, STT, DLR, NOC or C waits until I1's ticket is done, up to about 1-2 sessions)? | **Child orders** | his own trading on those names against I1's speed |
| Q19 | **New (V3-C-03).** ITB: the Jan-Jun 2027 series with open interest are adjusted (non-standard) series; use them once the OCC memo confirms their deliverable (about USD 3.7M of delta at most)? And may the short-dated Nov-20 standard puts (23 days, up to about USD 7.2M) be used? | **Adjusted series after the check; Nov-20 no** | the redirect cannot reach USD 10.64M honestly |

---

## 9. Risks and what happens then

| # | Risk | What happens |
|---|---|---|
| R1 | The 1.0.6 gate slips past Friday's OTC window | TRS and CDX book on 1.0.5 directly (D7); Reg T is not enforced until 1.0.6 (the reserve is in the funding plan anyway; `regt` reports "not in this release") |
| R2 | 1.0.6.1 slips | the FX lines wait for it (Monday or later); the JPY lines also need the orchestrator's checks; nothing else waits |
| R3 | Overnight gaps | resting limits do not fill; the passes re-send at fresh prices; drift gates hold lines for review |
| R4 | Thin option markets | slow or no fills at the wide-market limits; never the closing ask in a wide market; wide legs in children no larger than the displayed size; remainders the next session |
| R5 | Option contracts not listed (NOT_LISTED) | the 2-entry chain cache churns: chain, then preview at once, with retries and backoff (5.4.3); V-X pins listed contracts |
| R6 | A chain row with no displayed size | the leg is held for that pass; a fill that happened "in full" anyway is flagged in R0 (5.4.3); V-X makes it fill nothing |
| R7 | SR3 months missing or stale on Yahoo (1.0.8) | dropped and interpolated by their last print time (labelled); with fewer than 8 quoted months to 2Y, NO_CURVE and the IRS waits; Yahoo's volume is never a liquidity test |
| R8 | RAM (0.37 GB free at 18:50) and disk (7.4 GB free at 20:29) | one server per agent only when at least 1.0 GB is free; copies deleted; no browser except the 1.0.8 walk |
| R9 | A margin call larger than the cash | the repo draw rule (4.4) from 10-30; R0 and V-S show it, with due times, before it happens |
| R10 | The shared 10 % participation cap (V3-C-04) | the matcher serves the oldest order first and the cap is shared by every book, so a single large I1 ticket would starve LATER F1 / P1 orders on the same name for hours (IBIT about 1.2 sessions, GXO about 2): F1 is short IBIT, VMRK, STT, DLR, C and long GXO, KTOS, NOC, LMT, CRWD, PANW, NOW, SNOW, SPCX, ZS, PDBC, JNJ, NKE, PG, TMO, WMT and 5 GCZ26; P1 has older working orders on CRWD, PANW, ORCL, SPCX, LMT, TMO, NOC. D18's child orders let later orders interleave (a wait of about half a pass interval); older F1 / P1 orders keep priority anyway; the global rate is never changed; R0 reports I1's share of each bar's cap (Q18) |
| R11 | V-X's size-aware option fills (1.0.8) | they change F1's and P1's option fills too: told to the owner before the gate; the OI cap can be set to 0 |
| R12 | The two other plans still mention DTCC | theirs to revise; nothing in V3 uses it |
| R13 | ITB stays stale or its adjusted series cannot be verified | D' stays HELD; the residual is BLOCKED with the numbers of 3.9; nothing is re-sized onto XHB |
| R14 | Another delisted name in the master | the script's own stale-listing check; then V-X's guards in the app |
| R15 | The host's low-memory reaper kills background timers | no background timers without the owner's OK (memory note of 10-28); the passes run in the orchestrator's foreground at the times of 5.8; a missed pass only delays the next child |
| R16 | The futures cap's one-lot floor in thin bars (3.7) | I1 can take a whole 1-lot bar overnight on the deferred CL legs: disclosed; most of those legs fill in the day session |
| R17 | The owner gives no ORCL level | the line stays unexecuted with that reason; 0.8 % of the budget shown as a gap |

---

## Appendix A. Line index and status counts

108 lines: the feasibility's 105 rows, with the AVB / VMRK TRS row split into 3.8 (SUPERSEDED) and 3.8a (VMRK), plus 4.3a (the iTraxx
redirect) and D' (the ITB redirect; the script creates its legs from a fresh chain under D19, counted here once).

| Status | Lines | Count |
|---|---|---:|
| NOW (tonight) | 10.1-10.7, 11.1-11.3 | 10 |
| NEXT (10-29) | 10.8-10.10 (day session); 7.1-7.17; 8.1-8.3, 8.4a, 8.5-8.8; 9.1; 6.1-6.6; 12A.1-12A.3, 12B.1-12B.2, 12C.1-12C.2, 12D.1-12D.3, 12E.1-12E.4, 12F.1, 12G.1-12G.3, 12H.1 | 54 |
| NEXT (Fri 10-30) | 3.1-3.7, 3.8a, 3.9-3.14; 4.1, 4.2, 4.3a | 17 |
| HELD (check) | 12DI (D', the ITB puts, D19) | 1 |
| SUPERSEDED | 8.4, 3.8 (AVB), 4.3 (iTraxx, redirected), 12D.4 (XHB remainder, redirected to D') | 4 |
| BLOCKED | 12E.5 (DLR remainder) | 1 |
| BUILD | 4.4 (1.0.7 + the owner's level); 5.1-5.3 (1.0.8, V-R); 14.1-14.3 (1.0.6.1); 14.4-14.6 (1.0.6.1, legacy method, the orchestrator's checks) | 10 |
| Accounts and margin | 15.1, 15.2, 15.4-15.7, 15.10, 15.11 done; 15.3 NEXT (the I1 CDX row); 15.8, 15.9 sub-ledgers (V-B) | 11 |

## Appendix B. Sources (all USD 0)

| Source | Used for |
|---|---|
| Yahoo Finance chart v8 (through the app; browser user agent, Yahoo only) | equity, ETF, futures and FX prints; SR3 months (`SR3Z26.CME` ... `SR3Z31.CME`); ITB's 10-28 close 88.99; CLH27's overnight bars |
| Cboe delayed quotes (through the app; the terminal's honest agent) | option chains, sizes, IV, Greeks, open interest; IBIT iv30; the ITB chain (provider time 2026-10-28 06:02 ET) |
| TreasuryDirect FedInvest, fiscaldata MSPD, Treasury.gov par curve (through the app) | Treasury marks, the master, the par curve |
| FRED: `SOFR`, `BAMLC0A0CM`, `BAMLH0A0HYM2`, `IRSTCI01JPM156N` (the legacy JPY rate) | fixings, CDX proxies, the JPY forwards |
| ECB data portal (through 1.0.6.1) | the EUR curve (AAA proxy) and ESTR |
| Bank of Japan: https://www.boj.or.jp/en/statistics/market/short/mutan/index.htm, the TONA terms PDF `.../outline/exp/data/exmutan2.pdf`, `.../mutan/d_release/md/2026/md20261027.xlsx` | TONA (1.227 % for 10-27) as a comparison, and its terms (for the FX / CCS plan's 1.0.6.2 item) |
| SEC EDGAR (`LoganTerminal/1.0 (loganshaffer87@gmail.com)`) | SPCX (CIK 1181412), AvalonBay (CIK 915912), Vivmark (CIK 906107), Oracle 8-K Ex. 99.1, DLR 8-K; Oracle's FWP 0001193125-25-215807 and 424B2 0001193125-25-217490 (the 4.800 % 2032 notes, CUSIP 68389XDK8) |
| OCC information memos (theocc.com; to be read by the orchestrator before any adjusted ITB series) | the deliverable of the adjusted ITB series (D19) |
| Yahoo calendarEvents (browser user agent) | ORCL and DLR earnings dates |
| federalreserve.gov | FOMC 12-08 / 12-09 |
| Not used | DTCC public dissemination (terms), Stooq (anti-bot), any paid feed |

---

## Critic answers

One line per finding of the independent critic (V3-C-01..V3-C-14), each verified against the code, the data or this plan's own arithmetic.

| Id | Verdict | Answer |
|---|---|---|
| V3-C-01 | accepted; fix (b) | Verified: with repo capacity 0 until the bonds settle (10-30), D13 stops the 10-29 OTC stage before 3.4 NVDA (from cash 434.6M: 393.1M against 1.25 x 336.6M = 420.7M), and at the 10-29 close cash alone would cover about 0.6x. TRS and CDX now book Fri 10-30 after settlement; D13 is unchanged and never relaxed in a run; the tooling unit-tests the breach point; 4.5 recomputed (1.43x; lowest 1.44x while booking) (D7, D13, 4.5, 5.4.6, 5.8, 5.9, 7). |
| V3-C-02 | accepted | The owner's 19:40 ET answers are applied throughout: SPCX traded as listed (3.14, 7.2 NEXT; 5.6 item 5 no longer bars it); B gross across legs, 772 spreads (debit 1,049,148.00 + fees 1,003.60, max profit 3,582,852.00, BE 686.41); I1 basis copied from F1; line 4.3a CDX.NA.IG USD 50M (IM 1,000,000.00, about 0.86M received, CS01 about +22,300); D' ITB under D19; DLR blocked; 3.1, 3.2 (net equity delta about -542M), 4.1-4.6, Appendix A and section 8 recomputed. |
| V3-C-03 | accepted | Verified (refetched 20:15 ET): the ITB chain's provider time is 06:02 ET 10-28 with spot 90.35 (the 10-27 close; ITB closed 88.99), the app grades it "close" after hours, the Jan-27+ series with OI are adjusted strikes, and 25 % of OI reaches about 3.66M (adjusted Jan-Jun 27 inside the 400-day window; the critic's 4.9M counted series outside it) or 7.2M (Nov-20). New D19 and section D': HELD_STALE / HELD_DELIVERABLE rules, Nov-20 only with his OK (Q19), residual BLOCKED, the owner told about 3.7M of 10.64M. |
| V3-C-04 | accepted | Verified: matcher oldest-first, one cap per (instrument, bar) for all books, the order's participation copied from the one global setting; F1's and P1's positions and working orders on the shared names checked on the 20:19 copy (F1 is also long JNJ, NKE, PG, TMO, WMT). New D18: large tickets as child orders at about half of each name's cap, the global rate never changed, disclosed in 1.1, 9 (R10, R11) and the summary, Q18; V-X item 4 now states that it changes F1 / P1 option fills. |
| V3-C-05 | accepted | Verified against `collateral.py` (VM taken the same day, an unfunded VM is an overdraft, calls due 10:00 ET next day) and the 4.5 arithmetic. V-S now has due times per counterparty, resources by due time (no T+1 proceeds or unsettled bonds for same-day calls, OVERDRAFT_OVERNIGHT flagged), a test file driving every state including MARGIN_DEFAULT with counterparty, amount and due time and INSOLVENT, the reverse-stress k both ways, scenario 9 on net strategy value, beta 1.0 below 250 returns, offsets per line, and a gate check of the spiral and reverse stress on the live I1 copy. |
| V3-C-06 | accepted | Verified: `OPTIONAL_MODULES` is a dict and unlisted modules are never set on `services`. The hooks now list stress, stress_betas, fundbook, optionsbook, fundmetrics and sofrcurve, plus `api.PACKAGES`, `api.PLANNED_STATIC`, the fixture server's `OPTIONAL_STATIC` and `index.html` entries for the three JS files, anchored at the base, with a binding test per package (6.2). |
| V3-C-07 | accepted | Verified in `sessions.py` (CBOT night 20:00-08:45 and day 09:30-14:20; DAY = the current session's close) and on Yahoo (CLH27: 15 of 124 bars traded 18:00-20:03 ET, 155 lots). The grains are sent at 09:30 ET in the day session, re-sent the next day session; 5.4.1 / 5.5 corrected; the deferred CL legs are said to fill mainly in the day session; the one-lot floor is disclosed (3.7, R16). |
| V3-C-08 | accepted | Verified by calendar: a 10-30 trade gives EURUSD spot 11-03 (2027-02-03 / 05-03 / 11-03) and USDJPY spot 11-04 (2027-02-04 / 05-06 / 11-04); the IRS start comes from the preview. D12 and 3.12 restated for the real booking date (10-30 or 11-02 tables), JPY dates sent explicitly because the legacy path has no Tokyo calendar, 5.4.8 takes dates from the app and the script's Tokyo table, and the Culture Day case is a tooling test vector (TONA's R-A vectors moved with TONA to the FX / CCS plan). |
| V3-C-09 | partly accepted | Floor-rule faces (five of six were 1,000 high) and starting 4.3 from cash (the 661,944.45 receivable is not cash) are fixed. Rejected: "two more days of accrued": `products\bonds.py` charges accrued to the TRADE date ("real settlement is one day later, a stated simplification"; `accrued_per_100(terms, tdate)`), so a 10-29 trade carries one more day (about USD 56,650), not two; 3.6 is re-stated at that convention and discloses the street figure (about 113,300). |
| V3-C-10 | accepted | Verified in `swaps_cds.h_terms` (one index per command; 400 NOTHING_TO_UPDATE when every given term matches). Ids are now `v3i1028:cdxterms:IG:<n>` / `:HY:<n>`, only `basis_bp` is sent (the seed already has the half-spreads and IM), NOTHING_TO_UPDATE counts as set (5.2, 5.4.7). |
| V3-C-11 | accepted | Verified in FX_CCS_PLAN D9 / 4.3 (one FX desk deal per account, currency and due date, in the obligation's account). The SFI deals are in `I1:PB_GS`; D15's FX sub-ledger reads them there; the JPY instruction is used only if 1.0.6.1 accepts JPY (3.12, 3.13, V-B). |
| V3-C-12 | accepted | Verified: `ChainCache(max_entries=2)` churned by the pinned-chain refresh, `_materialise_option` at preview, `fill_cap` filling in full on a None size, and the cancel / re-send race. 5.4.3 previews right after each chain answer with 5 retries and backoff, holds a no-size leg and sends wide legs in children no larger than the displayed size, R0 flags in-full fills; 5.2 re-reads the cancelled order's filled quantity before any `:r<n>` or `:c<n>`. |
| V3-C-13 | accepted | Verified: `curve_points` has no quote time or volume, Yahoo's SR3 volumes are partial, and revision 1 implemented the stale rule twice. FX_CCS_PLAN revision 2 took none of R-A..R-E, so the owner's F1 FX trades are not delayed: R-C becomes V-R in 1.0.8 with its own `sofr_fut_quotes` table (quote time, volume for information only, never a liquidity test), R-D joins V-X behind one helper scoped to exchange-listed instruments, R-A / R-B stay on the FX / CCS plan's 1.0.6.2 list, R-E is dropped; Q4 re-asks the owner about the longer IRS wait (6.1). |
| V3-C-14 | accepted, with the orchestrator's note | The reference obligation is recorded: Oracle 4.800 % Notes due 2032-09-26, CUSIP 68389XDK8, senior unsecured, from EDGAR (FWP 0001193125-25-215807, 424B2 0001193125-25-217490), labelled "EDGAR-identified, not the ISDA standard reference obligation". The critic's "keep the MODEL label (Q12 default)" is superseded by the orchestrator's 20:08 ET note: the line books only at the owner's own negotiated mid, the MODEL spread shown beside it (3.4, 5.4.10, Q12). |
