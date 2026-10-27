# Owner's portfolio submissions, 2026-10-21 (verbatim, saved so nothing is lost)

## Message 1 (about 19:10 ET): positions to open - Fund book, 25,000,000

1. Long stocks - $18.5M (reference prices from the owner's earlier calculation, not executable prices; quantities to be recalculated when placed)
Stock - Reference price | Shares - Position value
Cybersecurity $3.50M: CRWD $239.40 5,010 $1.199M; ZS $231.26 4,760 $1.101M; PANW $347.21 3,460 $1.201M
AI software $3.00M: SNOW $322.87 4,340 $1.401M; NOW $153.10 10,450 $1.600M
Consumer $2.50M: WMT $117.73 8,490 $1.000M; PG $156.92 4,790 $752K; NKE $37.53 19,980 $750K
Space $1.25M: SPCX $159.27 7,850 $1.250M
Defense $3.50M: LMT $551.97 2,710 $1.496M; NOC $539.28 2,320 $1.251M; KTOS $38.74 19,360 $750K
Logistics $1.25M: GXO $48.45 25,800 $1.250M
Healthcare $3.50M: JNJ $257.68 7,760 $2.000M; TMO $694.18 2,160 $1.499M
Exact modeled equity total $18,499,992.70 (PG and LMT share counts rounded so the funded positions fit 25M).

2. Physical short positions (short stock - reference price - shares short - exposure)
STT $168.90 8,880 $1.500M; C $123.83 12,110 $1.500M; DLR $176.41 8,500 $1.499M; VMRK $61.97 24,210 $1.500M; IBIT $47.84 10,500 $502K;
total $6,501,512 short. (VMRK = the residential REIT formed by the August 2026 AvalonBay - Equity Residential merger; replaces AVB.)

3. OTC equity total return swaps
Short ORCL TRS 38,700 shares $4.996M; Short META TRS 7,200 shares $5.029M; total short TRS $10.025M. No long TRS. Share equivalents rounded to
100-share lots to match the protective calls.

4. Futures, commodities and CDS
Long Gold GCZ26 5 contracts ~$2.065M; Short Treasury ZNZ26 19 contracts ~$2.002M; Long PDBC ETF $19.98 50,050 shares $999,999;
Buy CDX.NA.IG 5Y protection $3.000M notional; Buy CDX.NA.HY 5Y protection $4.000M notional (running coupons, upfront values and collateral
need dealer or clearing quotes).

5. Options insurance - $500,000 premium budget
Long ORCL calls 387 contracts $150K; Long META calls 72 contracts $180K; Long IBIT calls 105 contracts $50K; Long QQQ put spreads (TBD) $120K.
Strikes / expiries / premiums not yet chosen. STT, C, DLR and VMRK shorts have no separate options protection.

6. Cash and collateral - reconciliation
Long equity $18,499,992.70; PDBC $999,999.00; options premium allocation $500,000.00; Treasury bills $5,000,000.00; rounding cash $8.30;
total $25,000,000.00. Collateral (illustrative): short-sale proceeds retained by broker $6.502M; fund's own initial short margin at 50 %
$3.251M; T-bills potentially remaining after that $1.749M; additional futures, TRS and CDS collateral not yet quoted.

## Message 2 (about 19:20 ET): interest rate swap with the prime broker
USD SOFR Interest Rate Swap (rates up): $7,500,000 OTC notional (30 % of fund NAV); pay fixed / receive floating; tenor 3 years; fixed rate
~4.66 % indicative; floating benchmark compounded overnight SOFR; counterparty OTC swap dealer; collateral eligible T-bills / cash under CSA.

## Orders placed in the app
Every order, fill and position the owner enters in FinSim is recorded in the app's ledger (~/.finsim/terminal.db) and survives restarts and
upgrades; install_release.ps1 backs the database up before every deploy, and online backups are taken to ~/.finsim/backups.

## Execution log (orchestrator, at the owner's request, live Fund book, 2026-10-21 19:59 EDT)
Reconciled first (read-only database): 15 long market GTC orders working in F1:US_EXCHANGE (O-000002..O-000016); STT SELL_SHORT 8,800 DAY
(O-000017) and C SELL_SHORT 12,000 GTC (O-000019) working with borrows B-000001 (collateral 1,516,495.20) and B-000002 (1,515,924.00);
transfers: initial capital 25,000,000 to F1:TREASURY; owner transfers 6,500,000 + 14,000,000 Treasury -> US Exchange; borrow sweeps
1,516,495.20 + 1,515,924.00.
Actions: cancelled O-000008 (PG BUY 6,400); placed O-000020 PG BUY 4,790 market GTC; cancelled O-000018 (STT 19 Feb 2027 180 calls, BTO 88:
owner "didnt mean to buy calls with StateStreet"); placed O-000021 GCZ26 BUY 5 market DAY (initial margin 60,000.00; 3 filled at 4,140.7333 at
the first check, the rest fills under the 10 % volume cap).
Blocked tonight: PDBC BUY 50,050 (needs 1,000,249.25, Treasury available 91,463.94 before the gold margin); ZNZ26 SELL 19 (needs 49,442.75 of
margin; about 31K free after gold); DLR / IBIT shorts (102 % cash collateral rule until the 1.0.3.1 hotfix); VMRK (no borrow for names without
volume in the master); options insurance (waits for cash and for the shorts / TRS it protects); TRS, IRS, T-bills (1.0.4), CDX (being built).
2026-10-21 21:06 EDT: owner said "deploy" -> 1.0.3.1 installed 21:04 (short collateral funded by the sale proceeds, queue for funds, ADV for on-demand
names). Then placed: O-000022 PDBC BUY 50,050 GTC (working); O-000023 ZNZ26 SELL 19 DAY (2 filled at 105.3281, rest filling); O-000024 DLR SELL_SHORT
8,500 GTC; O-000025 IBIT SELL_SHORT 10,500 GTC; O-000026 VMRK SELL_SHORT 24,210 GTC (borrow GC 0.30 %, collateral 1,530,793.46 at the fill).
Still not in the live app: TRS ORCL / META and the IRS with F1:PB_GS, CDX IG / HY, T-bills (1.0.4); options insurance not placed yet.
