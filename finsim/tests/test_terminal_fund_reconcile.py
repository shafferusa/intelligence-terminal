"""FINSIM1 P8-lite, TEST-04: the whole-fund reconciliation (docs/FINSIM1_PLAN.md P8 task 4 as section 15.6 item 2 scopes it for
1.0.5; the owner's directive sections 7-14 and 18 #12-#23).  Every expected number is worked by hand in a comment from the inputs
and the documented rules; nothing is pasted from the code's output.

ONE database, the REAL runtime (``server.build_runtime``: every module bound exactly as ``serve`` binds it, the Router, the
lifecycle scheduler), driven through the HTTP routes the UI uses, a FakeClock and a FakeProvider (no network).  The Fund Book F1
alone: 25,000,000.00 in F1:TREASURY, Goldman Sachs onboarded as prime broker (F1:PB_GS).

THE MIXED FUND, booked on Tue 2026-10-20 from 14:00 ET (prints 2 minutes old; SOFR 3.87 % and the ICE BofA US Corporate OAS 0.88 %
observed 2026-10-19; the Treasury curve of 10-19 = the flat 4.00 % semi-annual factors DFS_400, df(k) = 1 / 1.02^k to 8 dp):
  F1:US_EXCHANGE
  - equity long: BUY 1,000 AAPL @ 339.50 = 339,500.00, fee max(1,000 x 0.005, 1.00) = 5.00; payable 339,505.00 due 10-21, swept
    from Treasury at the fill.
  - physical short: SELL_SHORT 1,000 MSFT @ 500.00, fee 5.00; borrow GC (FINRA ratio 0.30, ADV 33.6 M) fee 0.30 %, rebate SOFR -
    0.50 % = 3.37 %; collateral 1.02 x 1,000 x 500.00 = 510,000.00 posted at the fill = the sale's own proceeds 499,995.00 + 10,005.00
    of cash.
  - futures long / short: BUY 2 GCZ26 @ 4,140.00 (fee 2 x 2.25 = 4.50, IM 2 x 12,000.00 = 24,000.00); SELL 3 ZNZ26 @ 112.50 (fee
    6.75, IM 3 x 2,600.00 = 7,800.00); each swept from Treasury (IM + fee): 24,004.50 and 7,806.75.
  - the option spread: BTO 10 QQQ 2026-11-20 700P @ 17.64 (the ask; premium 17,640.00, fee 10 x 0.65 = 6.50, payable 17,646.50
    due 10-21, swept), STO 10 QQQ 675P @ 10.25 (the bid; 10,250.00 - 6.50 = 10,243.50 receivable due 10-21); a bear put spread
    (the long 700P covers the short 675P): option margin 0.  Marks are the Cboe mids: 700P (17.50 + 17.64) / 2 = 17.57, 675P
    (10.25 + 10.39) / 2 = 10.32, so each leg shows -70.00 at once.
  F1:PB_GS (GS schedule: TRS IM 10 %, financing SOFR - 0.60 %; CDX IG IM 2 %, half-spread 0.50 bp; IRS IM |DV01| x 50, half-width 1 bp)
  - short TRS on 10,000 ORCL (pay the total return) at the reset 129.13 = the print: notional 1,291,300.00, IM 129,130.00, mark0 0.00.
  - CDX.NA.IG S47 5Y buy protection 3,000,000.00: proxy 0.88 % = 88 bp + basis 0 + half-spread 0.5 = dealt 88.5 bp; IM 60,000.00;
    upfront (88.5 - 100) bp x RPV01(88.5 bp) x 3,000,000 = -0.00115 x 4.546561 x 3,000,000 = -15,685.64 (received: the contract's
    cost, GL 2345); accrued rebate 3,000,000 x 1 % x 30 / 360 = 2,500.00 (09-21 .. 10-20); initial payment 15,685.64 + 2,500.00
    = 18,185.64 receivable due T+1 = 10-21; mark0 at mid 88 bp = -0.0012 x 4.547496 x 3,000,000 = -16,370.98; cost of dealing
    -16,370.98 + 15,685.64 = -685.34.  (RPV01 from the model swaps_cds documents, re-derived independently below: ``cdx_value_c``.)
  - pay-fixed USD SOFR IRS 10,000,000.00, 5 years, 30/360 semi-annual vs SOFR quarterly: on DFS_400 the annuity A = 0.5 x 8.98258501
    = 4.491292505, par = 2 x (1 - 0.82034830) / 8.98258501 = 0.04000, dealt 0.04000 + 0.00010 = 0.04010; mark0 = 10,000,000 x
    (0.04000 - 0.04010) x A = -4,491.29; DV01 4,491.29; IM 50 x 4,491.29 = 224,564.50.
  NAV: 25,000,000.00 - 5.00 - 5.00 - 4.50 - 6.75 - 76.50 - 76.50 + 0.00 - 685.34 - 4,491.29 = 24,994,649.12.
  Treasury: 25,000,000.00 - (339,505.00 + 24,004.50 + 7,806.75 + 17,646.50 + 129,130.00 + 60,000.00 + 224,564.50 = 802,657.25)
  = 24,197,342.75.

TWO DAYS OF LIFECYCLE JOBS (``LifecycleScheduler.tick(at=...)``; the inputs arrive as they would: closes at 16:00, futures prints at
or before the settle time, the next day's FRED prints and the Treasury curve the next morning):
  Tue 10-20 evening
  - 14:36 FUTURES_VM GC (settle 14:30, print 4,150.00): 2 x (4,150 - 4,140) x 100 = +2,000.00; NAV 24,996,649.12.
  - 16:21 closes AAPL 340.00, MSFT 495.00, ORCL 127.13, QQQ 688.00; ZN print 112.25 (15:59:30); chain 700P 18.40 / 18.60 (mid
    18.50), 675P 10.90 / 11.10 (mid 11.00).  ZN VM -3 x (112.25 - 112.50) x 1,000 = +750.00.  NAV +500.00 (AAPL) +5,000.00 (MSFT)
    +20,000.00 (TRS model, intraday: -10,000 x (127.13 - 129.13)) +930.00 (700P (18.50 - 17.57) x 1,000) -680.00 (675P -(11.00 -
    10.32) x 1,000) +750.00 (ZN) = +26,500.00 -> 25,023,149.12.
  - 17:06 SWAP_VM 10-20: TRS mark +20,000.00 -> 20,000.00 received (Dr 1010 / Cr 2330); IRS mark on the 10-19 curve -4,491.29 ->
    posted (Dr 1320 / Cr 1010); CDX CLOSE_PENDING (FRED publishes 10-20 on 10-21).  NAV unchanged: VM is collateral.
  - 17:11 COLLATERAL_MARK MSFT: 1.02 x 1,000 x 495.00 = 504,900.00 -> 5,100.00 back to cash.  NAV unchanged.
  - 17:31 SWEEP_OUT: US Exchange settled 354,996.50 - payables 357,151.50 - buffer 10,000.00 < 0, nothing; PB settled 15,508.71 -
    restricted 15,508.71 (= 20,000.00 received - 4,491.29 posted under the one GS CSA) - buffer < 0, nothing: GS's collateral stays.
  - 17:46 SNAPSHOT 10-20: F1:ALL 25,023,149.12 = Treasury 24,197,342.75 + US Exchange 397,288.50 (advances 388,962.75 + P&L
    8,325.75) + PB 428,517.87 (advances 413,694.50 + 20,000.00 - 685.34 - 4,491.29).
  Night 10-20 (Wed 00:36; SOFR 3.87 of 10-19 is the latest print)
  - SETTLE_DUE: +10,243.50 (675P), +18,185.64 (CDX initial payment), -339,505.00 (AAPL), -17,646.50 (700P).
  - borrow fee 495,000.00 x 0.30 % / 360 = 4.125 -> 4.12 (half-even); rebate 504,900.00 x 3.37 % / 360 = 47.26; Treasury interest
    24,197,342.75 x (3.87 - 0.50) % / 360 = 2,265.14; PB credit max(0, 15,508.71 - restricted 15,508.71) = 0 -> 0.00; IRS float
    10,000,000 x 3.87 % / 360 = 1,075.00 received, fixed 10,000,000 x 4.01 % / 360 = 1,113.89 paid (4453); TRS financing
    1,291,300 x (3.87 - 0.60) % / 360 = 117.29 received (4451); CDX: night 10-20 is in the rebate, nothing.  NAV +2,386.68 ->
    25,025,535.80.
  Wed 10-21
  - 09:00 FRED 10-20 (SOFR 3.88, IG OAS 0.80 %) and the 10-20 curve (flat 4.10 %: DFS_410, df(k) = 1 / 1.0205^k).  IRS model
    on it: par 0.04100, A = 4.479565725 -> 10,000,000 x 0.0009 x A = +40,316.09 (NAV +44,807.38).  CDX VM 10-20 at 80 bp on the
    10-20 curve: -0.0020 x 4.5509075 x 3,000,000 = -27,305.45 posted; GS may be paid with GS's own collateral (C21), so the PB's own
    cash at the close of 10-20 is 15,508.71 and the as-of sweep (C20 / D8, dated 10-20) brings the 11,796.74 rest: the PB closes
    10-20 at exactly 0.00 and Treasury at 24,185,546.01.  NAV -27,305.45 + 16,370.98 = -10,934.47 -> 25,059,408.71.
  - 10:00 prints AAPL 341.00, MSFT 497.00, ORCL 128.00, GC 4,135.00, ZN 112.375; chain 700P 17.40 / 17.60, 675P 10.20 / 10.40:
    +1,000.00 -2,000.00 -8,700.00 (TRS +11,300.00) -1,000.00 +700.00 -3,000.00 (GC vs the 4,150 settle) -375.00 = -13,375.00 ->
    25,046,033.71.  Securities lending: 200 AAPL through BNY (GC tier, your share 0.30 % x 0.85 = 0.255 %): cash collateral 1.02 x
    200 x 341.00 = 69,564.00 received (Dr 1010 / Cr 2520), rebate SOFR 3.88 - 0.50 = 3.38 % paid on it; NAV unchanged.
  - 11:00 SELL 400 AAPL @ 341.00: 136,400.00 - fee 2.00 = 136,398.00 due 10-22; cost 400 x 339.50 = 135,800.00, realised +600.00;
    NAV -2.00 -> 25,046,031.71.
  - 14:36 GC settle 4,130.00: VM 2 x (4,130 - 4,150) x 100 = -4,000.00; NAV 2 x (4,130 - 4,135) x 100 = -1,000.00 -> 25,045,031.71.
  - 16:21 closes AAPL 341.00, MSFT 498.00, ORCL 128.13, QQQ 692.00; ZN 112.40625; chain 700P 16.90 / 17.10, 675P 9.70 / 9.90.  ZN VM
    -3 x (112.40625 - 112.25) x 1,000 = -468.75.  NAV 0.00 - 1,000.00 - 1,300.00 (TRS +10,000.00) - 500.00 + 500.00 - 93.75 =
    -2,393.75 -> 25,042,637.96.
  - 17:06 SWAP_VM 10-21: TRS +10,000.00 vs +20,000.00 -> 10,000.00 back to GS (Dr 2330 / Cr 1010); IRS +40,316.09 vs -4,491.29 ->
    +44,807.38 (the posted 4,491.29 released, 40,316.09 received); CDX CLOSE_PENDING.  NAV unchanged.
  - 17:11 COLLATERAL_MARK MSFT 1.02 x 1,000 x 498.00 = 507,960.00 -> 3,060.00 more to the lender; the lent AAPL closes at the lend
    price (69,564.00 required, nothing moves).  NAV unchanged.
  - 17:31 SWEEP_OUT: PB settled 15,508.71 + 18,185.64 + 11,796.74 - 27,305.45 - 10,000.00 + 44,807.38 = 52,993.02, restricted
    max(0, (20,000.00 - 10,000.00 + 40,316.09) - 27,305.45) = 23,010.64, buffer 10,000.00 -> 19,982.38 swept to Treasury (GS's
    23,010.64 stays); US Exchange settled 70,123.75 - restricted 69,564.00 (the borrower's cash collateral) - buffer < 0, nothing.
  - 17:46 SNAPSHOT 10-21: F1:ALL 25,042,637.96 = Treasury 24,207,793.53 (cash 24,205,528.39 + accrued 2,265.14) + US Exchange
    390,560.89 + PB 444,283.54.
  Night 10-21 (Thu 00:36; SOFR 3.88 of 10-20)
  - SETTLE_DUE +136,398.00.  Borrow fee 498,000.00 x 0.30 % / 360 = 4.15, rebate 507,960.00 x 3.38 % / 360 = 47.69; Treasury
    interest on its business-date cash at the close of 10-21, 24,185,546.01 (the 17:31 sweep carries business date 10-22) x 3.38 %
    / 360 = 2,270.75; PB credit (29,982.38 = 52,993.02 - 23,010.64) x 3.13 % / 360 = 2.61; IRS float 1,075.00, fixed 2,227.78 -
    1,113.89 = 1,113.89 (cumulative per segment); TRS financing 1,291,300 x 3.28 % / 360 = 117.65; CDX premium 3,000,000 x 1 % x
    31 / 360 = 2,583.33 - 2,500.00 = 83.33 paid; securities lending fee 200 x 341.00 x 0.255 % / 360 = 0.48 earned, rebate
    69,564.00 x 3.38 % / 360 = 6.53 paid.  NAV +2,306.28 -> 25,044,944.24.
  Thu 10-22
  - 09:00 FRED 10-21 (IG OAS 0.84 %): CDX VM 10-21 at 84 bp on the 10-20 curve: -0.0016 x 4.5413499 x 3,000,000 = -21,798.48 ->
    5,506.97 released to the fund.  NAV +5,506.97 -> 25,050,451.21.

AFTER EVERY STEP ``reconcile()`` asserts, for F1:ALL and each F1 account (the P8 task 4 list):
  - integrity full is ok (no ERROR, nothing failing) and no lifecycle job failed;
  - NAV = assets - liabilities (rounding 0), the NAV identity (capital + advances + realised + income - financing - fees +
    unrealised + fx) and P&L ITD with residual 0;
  - TRS / IRS / CDS notionals are never carried at face: no GL balance and no balance-sheet row is a notional, each contract sits at
    its mark (hand-worked per step) and the swap rows of the balance sheet are the sum of those marks;
  - collateral transfers change no P&L: no initial-margin, variation-margin, collateral, securities-lending, sweep or settlement
    entry carries an income or expense line (and the steps that only move collateral leave NAV exactly unchanged);
  - restricted cash = max(0, VM received 2330 - VM posted 1320) + securities-lending cash collateral 2520, per account (computed here
    from the ledger, compared with ``collateral.restricted_c`` and the C9b cash adjuster), never below the account's settled cash and
    never swept;
  - every account's business-date settled cash (C9h ``cash_view_at``) is >= 0 at the close of every date since the book started, and
    its current settled cash too;
  - the PB balances tie to the consolidated balance sheet: every row of F1:ALL is the sum of the accounts' rows, the trial balance
    is 0 natively and in base, advances net to 0;
  - futures VM ties to the cash ledger: the 4400 lines of the VM entries are exactly their 1010 lines, per contract equal to the
    hand-worked daily VM, and the 4400 balance is their sum.

Phase 2 (1.0.6, plan 15.6 item 2 / 15.9 item 5) adds the T-bill pledged under the CSA and the T-bill half of "collateral transfers
change no P&L" (P6 Part B ``collateral.pledge`` / ``pledges_view``): not in this file yet, on purpose.

``test_evening_jobs_post_on_their_own_business_date`` (POSTCLOSE-06, FINSIM1_PLAN 15.13: "A lifecycle job of business date D stamps
its entries and its funding sweeps with D") runs the same book with the lent AAPL closing above its lend price, so the securities-
lending collateral mark posts, and checks the business date of every evening posting.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import math
import os
import shutil
import tempfile
import unittest
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN

from tests.terminal_helpers import _Log
from finsim.terminal import config, server as _server
from finsim.terminal.accounting import cash_view_at
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.ledger import CHART, natural
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying, FakeProvider
from finsim.terminal.units import qprice

# A release may leave the swap modules out (test_terminal_integration's rule) and a pre-1.0.5 tree has no collateral module: skip, said.
HAVE_MODULES = all(importlib.util.find_spec(m) is not None for m in (
    "finsim.terminal.products.swaps", "finsim.terminal.products.swaps_trs", "finsim.terminal.products.swaps_cds", "finsim.terminal.collateral"))
NO_MODULES = "the swap / CDX / collateral modules are not in this build"

D = Decimal
F1 = "F1"
ALL = "F1:ALL"
TRE, USX, PB = "F1:TREASURY", "F1:US_EXCHANGE", "F1:PB_GS"
TRS_ID, CDX_ID, IRS_ID = "SWP-000001", "CDX-000001", "SWP-000002"
P700 = occ_symbol("QQQ", date(2026, 11, 20), "P", D("700"))       # QQQ261120P00700000
P675 = occ_symbol("QQQ", date(2026, 11, 20), "P", D("675"))       # QQQ261120P00675000
START = date(2026, 10, 20)
DFS_400 = {"0.5": "0.98039216", "1.0": "0.96116878", "1.5": "0.94232233", "2.0": "0.92384543", "2.5": "0.90573081", "3.0": "0.88797138",
           "3.5": "0.87056018", "4.0": "0.85349037", "4.5": "0.83675527", "5.0": "0.82034830", "5.5": "0.80426304", "6.0": "0.78849318"}
DFS_410 = {"0.5": "0.97991181", "1.0": "0.96022715", "1.5": "0.94093792", "2.0": "0.92203618", "2.5": "0.90351414", "3.0": "0.88536418",
           "3.5": "0.86757881", "4.0": "0.85015072", "4.5": "0.83307273", "5.0": "0.81633781", "5.5": "0.79993905", "6.0": "0.78386973"}
IRS_5Y = {"notional": "10000000.00", "currency": "USD", "pay_fixed": True, "tenor_months": 60, "fixed_rate": None, "fixed_basis": "30/360", "fixed_months": 6,
          "float_index": "SOFR", "float_basis": "ACT/360", "float_months": 3, "fixing": "TERM"}
TRS_TERMS = {"underlying_id": "ORCL", "units": 10000, "receiver": False, "reset_months": 1, "tenor_months": 12}
CDX_TERMS = {"index": "IG", "side": "BUY_PROTECTION", "notional": "3000000.00"}
NOTIONAL_C = {TRS_ID: 129130000, CDX_ID: 300000000, IRS_ID: 1000000000}     # 10,000 x 129.13; 3,000,000.00; 10,000,000.00
PNL_KINDS = ("INCOME", "EXPENSE")
#: entries that only move collateral, margin or cash between accounts / counterparties: never a P&L line
COLLATERAL_ENTRY_KINDS = ("OTC_IM", "COLLATERAL", "FUT_MARGIN", "SECLEND", "SWEEP_IN", "SWEEP_OUT", "TRANSFER", "SETTLE")


# ---- the CDX model, re-derived from the swaps_cds module DOCSTRING (not its code) ----------------------------------------------------
# flat hazard lambda = S / (1 - R), R 40 % (IG); Q(t) = exp(-lambda t), t = calendar days from the valuation date / 365; RPV01 = sum over
# the remaining coupon periods of accrual (ACT/360) x DF(payment date) x (Q(start) + Q(end)) / 2, the first period from the step-in date
# (valuation + 1); coupon dates 20 Mar / Jun / Sep / Dec rolled to the next business day (S47: accrual start 2026-09-21, maturity
# 2031-12-20, the last period to maturity + 1 day paid on the maturity rolled); DF log-linear between the semi-annual nodes, DF(0) = 1.
@functools.lru_cache(maxsize=1)
def _holidays() -> frozenset:
    """The NYSE full-day closures of ``data/nyse-holidays.json`` (the calendar the terminal rolls its coupon dates on)."""
    with open(config.holidays_path(), "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return frozenset(h["date"] for y in (raw.get("years") or {}).values() for h in (y.get("holidays") or []))


def _business(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in _holidays()


def _roll(d: date) -> date:
    while not _business(d):
        d += timedelta(days=1)
    return d


def cdx_value_c(valuation: date, spread_bp: float, dfs: dict, notional: float = 3_000_000.0, coupon_bp: float = 100.0) -> int:
    """A protection buyer's value (S - C) x RPV01(S) x N in cents, half-even."""
    nodes = [(0.0, 1.0)] + sorted((float(k), float(v)) for k, v in dfs.items())

    def df(t: float) -> float:
        for (t0, d0), (t1, d1) in zip(nodes, nodes[1:]):
            if t <= t1:
                return math.exp(math.log(d0) + (t - t0) / (t1 - t0) * (math.log(d1) - math.log(d0)))
        raise ValueError(t)
    maturity = date(2031, 12, 20)
    coupons = [_roll(date(y, m, 20)) for y in range(2026, 2032) for m in (3, 6, 9, 12)]
    start = max(c for c in coupons if c <= valuation)
    ends = [c for c in coupons if start < c < maturity]
    periods = list(zip([start] + ends, ends, ends)) + [((ends[-1]), maturity + timedelta(days=1), _roll(maturity))]
    lam = spread_bp / 10000.0 / 0.60
    step_in = valuation + timedelta(days=1)
    rpv = 0.0
    for s, e, pay in periods:
        if e <= step_in:
            continue
        s = max(s, step_in)
        ts, te, tp = (s - valuation).days / 365.0, (e - valuation).days / 365.0, (pay - valuation).days / 365.0
        rpv += (e - s).days / 360.0 * df(tp) * (math.exp(-lam * ts) + math.exp(-lam * te)) / 2
    return int((D(repr(notional * (spread_bp - coupon_bp) / 10000.0 * rpv)) * 100).quantize(D(1), ROUND_HALF_EVEN))


def ep(day: str, hhmm: str) -> int:
    return epoch_of(date.fromisoformat(day), hhmm)


def ny(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}:00-04:00"


class FundBook:
    """The runtime, its market and the steps of the mixed fund (shared by the tests of this module)."""

    def __init__(self, tc: unittest.TestCase, tmp: str):
        self.tc = tc
        os.environ[config.HOME_ENV] = tmp
        os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
        self.clock = FakeClock(ny("2026-10-20", "14:00"))
        self.fp = FakeProvider(self.clock)
        self.rt = _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=self.clock, offline=True, fake_provider=self.fp, log=_Log(),
                                        books=[config.BOOKS[1]], fetch_threads=0)

    # ---- the market -------------------------------------------------------------------------------------------------------------
    def prints(self, day: str, groups: dict, volume: int = 61900) -> None:
        """Yahoo prints by quote time ``{"16:00": {iid: last}}`` (fetched now, completed 1-minute bar of ``volume``) and ONE worker tick:
        every print of a moment goes in one call, because a tick fetches every due instrument from what the provider holds then."""
        for hhmm, pxs in groups.items():
            qt = ep(day, hhmm) if len(hhmm) == 5 else epoch_of(date.fromisoformat(day), hhmm[:5]) + int(hhmm[6:8])
            for iid, last in pxs.items():
                inst = self.rt.instruments.get(iid)
                self.fp.set(inst.yahoo_symbol, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, volume))
                self.rt.cache.subscribe([iid], 86400, "PAGE")
        self.rt.worker.tick()

    def chain(self, day: str, hhmm: str, spot: str, p700, p675) -> None:
        """The QQQ Cboe chain (the two strikes) observed at ``day hhmm``, fetched now, through the worker's chain fetch."""
        qt, now = ep(day, hhmm), self.rt.clock.epoch()
        rows = [ChainRow(P700, "2026-11-20", "P", qprice("700"), qprice(p700[0]), qprice(p700[1]), 50, 50, None, None, 0.2, -0.5, None, None, None, 100, 10),
                ChainRow(P675, "2026-11-20", "P", qprice("675"), qprice(p675[0]), qprice(p675[1]), 50, 50, None, None, 0.2, -0.3, None, None, None, 100, 10)]
        self.fp.set_chain("QQQ", ChainSnapshot("QQQ", "QQQ", ChainUnderlying("QQQ", qprice(spot), None, None, 10, 10, qt, None, 0.18), rows, now, qt, len(rows)))
        self.rt.worker._fetch_chain("QQQ", now)

    def rates(self, sofr: list, ig_oas: list) -> None:
        """FRED prints as the 6-hourly refresher stores them (``rates.record`` through the RateStore)."""
        self.fp.set_rate("SOFR", sofr)
        self.fp.set_rate("BAMLC0A0CM", ig_oas)
        for series, obs in (("SOFR", sofr), ("BAMLC0A0CM", ig_oas)):
            self.rt.rates.put(series, [(date.fromisoformat(d), D(v)) for d, v in obs])

    def curve(self, dfs: dict, as_of: str) -> None:
        """The Treasury curve published after 18:00 ET (the worker's curve slot): fixed discount factors of ``as_of``."""
        self.fp.set_curve_dfs(dfs, as_of)
        self.rt.curve.set_fixed_dfs(dfs, date.fromisoformat(as_of))

    def finra(self, symbol: str, days) -> None:
        """FINRA daily short volume (what the 18:00 ET refresher writes): ratio 0.30 on each day."""
        with self.rt.db.writer() as tx:
            for d in days:
                tx.conn.execute("INSERT OR REPLACE INTO finra_short(symbol, day, short_volume, total_volume) VALUES (?,?,?,?)", (symbol, d, 30000, 100000))

    # ---- API and jobs -------------------------------------------------------------------------------------------------------------
    def ok(self, method: str, path: str, body=None) -> dict:
        status, out, _ = self.rt.router.handle(method, path, None, body, "127.0.0.1", None)
        self.tc.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out

    def order(self, rid: str, **req) -> dict:
        out = self.ok("POST", "/api/orders", dict(req, account_id=req.get("account_id", USX), tif="DAY", request_id=f"ui:fund-{rid}"))
        self.tc.assertEqual("FILLED", out["order"]["status"], out["order"])
        return out

    def tick(self, at: str) -> dict:
        out = self.rt.lifecycle.tick(at=at)
        with self.rt.db.reader() as conn:
            failed = [dict(r) for r in conn.execute("SELECT job, key, last_error FROM job_failures").fetchall()]
        self.tc.assertEqual([], failed, f"lifecycle failures at {at}")
        return {(i["job"], i["key"]): i["status"] for i in out["ran"]}

    def q(self, sql: str, params=()) -> list:
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def bal(self, account: str, gl: str) -> int:
        """Natural-sign balance of (account, gl) in USD over every instrument."""
        rows = self.q("SELECT COALESCE(SUM(balance_c), 0) n FROM gl_balances WHERE account_id=? AND gl=? AND currency='USD'", (account, gl))
        return natural(gl, int(rows[0]["n"]))

    def lines(self, idem: str) -> list:
        return [(r["account_id"], r["gl"], r["amount_c"]) for r in self.q(
            "SELECT l.account_id, l.gl, l.amount_c FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.idem_key=? ORDER BY l.line_no", (idem,))]

    def posted(self, idem: str) -> dict:
        """``{(account, gl): storage-sign cents}`` of one entry (debit +, credit -), whatever the order of its lines."""
        out: dict = defaultdict(int)
        for a, gl, amount in self.lines(idem):
            out[(a, gl)] += int(amount)
        return {k: v for k, v in out.items() if v}

    def entry(self, idem: str) -> dict:
        rows = self.q("SELECT * FROM journal_entries WHERE idem_key=?", (idem,))
        self.tc.assertEqual(1, len(rows), idem)
        return rows[0]

    # ---- the book, step by step ---------------------------------------------------------------------------------------------------
    def open_market(self) -> None:
        """Tue 2026-10-20 14:00 ET: curve of 10-19, SOFR / IG OAS of 10-19, FINRA files, the 13:58 prints and the QQQ chain."""
        self.curve(DFS_400, "2026-10-19")
        self.rates([("2026-10-19", "3.87")], [("2026-10-19", "0.88")])
        self.finra("MSFT", ("2026-10-15", "2026-10-16", "2026-10-19"))
        self.finra("AAPL", ("2026-10-15", "2026-10-16", "2026-10-19"))
        self.prints("2026-10-20", {"13:58": {"AAPL": "339.50", "MSFT": "500.00", "ORCL": "129.13", "QQQ": "690.00", "SPY": "769.32", "GCZ26": "4140.00",
                                             "ZNZ26": "112.500000"}})
        self.chain("2026-10-20", "13:58", "690.00", ("17.50", "17.64"), ("10.25", "10.39"))


@unittest.skipUnless(HAVE_MODULES, NO_MODULES)
class FundReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fund-reconcile-")
        self.f = None

    def tearDown(self):
        if self.f is not None:
            self.f.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ==== the reconciliation, after every step =====================================================================================
    def reconcile(self, where: str, nav=None, marks=None, restricted=None, fut_vm=None) -> dict:
        """The P8 task 4 checks (module docstring); ``nav`` = the hand-worked NAV of F1:ALL in cents, ``marks`` = {contract: hand-worked
        MV cents}, ``restricted`` = {account: hand-worked restricted cents} (non-zero ones), ``fut_vm`` = {VM idem: hand-worked cash
        cents}.  Returns {scope: nav_c}."""
        f, rt = self.f, self.f.rt
        acc = rt.accounting
        snap = rt.marks.snapshot()
        # -- integrity full ok
        rep = acc.integrity("full", snapshot=snap)
        self.assertEqual([], [p.to_dict() for p in rep.problems if p.severity == "ERROR"], f"{where}: integrity ERROR")
        self.assertTrue(rep.ok, f"{where}: integrity failing {rep.failing}")
        accounts = [r["account_id"] for r in f.q("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY account_id", (F1,))]
        # -- NAV = A - L; the NAV identity; the accounts sum to the book
        navs, sheets = {}, {}
        for scope in [ALL] + accounts:
            n = acc.nav(scope, snapshot=snap)
            bs = acc.balance_sheet(scope, snapshot=snap)
            self.assertEqual((n.nav_c, 0), (bs.assets_c - bs.liabilities_c, bs.rounding_c), f"{where} {scope}: NAV = assets - liabilities")
            ident = n.capital_c + n.transfers_c + n.realised_c + n.income_c - n.financing_c - n.fees_c + n.unrealised_c + n.fx_result_c
            self.assertEqual(n.nav_c, ident, f"{where} {scope}: NAV = capital + advances + realised + income - financing - fees + unrealised + fx")
            navs[scope], sheets[scope] = n.nav_c, bs
        if nav is not None:
            self.assertEqual(nav, navs[ALL], f"{where}: hand-worked NAV of F1:ALL")
        self.assertEqual(navs[ALL], sum(navs[a] for a in accounts), f"{where}: the accounts' NAVs sum to F1:ALL")
        pnl = acc.pnl(ALL, "ITD", snapshot=snap)
        self.assertEqual((0, navs[ALL] - 2500000000), (pnl.residual_c, pnl.pnl_c), f"{where}: P&L ITD = NAV - capital, residual 0")
        # -- the PB balances tie to the consolidated balance sheet: every row of F1:ALL is the sum of the accounts' rows
        by_key = defaultdict(int)
        for a in accounts:
            for r in sheets[a].rows:
                by_key[r["key"]] += int(r["base_c"])
        self.assertEqual({r["key"]: int(r["base_c"]) for r in sheets[ALL].rows}, {k: v for k, v in by_key.items() if v}, f"{where}: rows of F1:ALL = sum of the accounts")
        tb = f.q("SELECT g.currency, SUM(g.balance_c) n, SUM(g.base_c) b FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id WHERE a.lifetime_id=?"
                 " GROUP BY g.currency", (F1,))
        self.assertEqual([("USD", 0, 0)], [(r["currency"], int(r["n"]), int(r["b"])) for r in tb], f"{where}: trial balance")
        self.assertEqual(0, sum(f.bal(a, "3100") for a in accounts), f"{where}: Treasury advances net to 0 over the book")
        # -- notionals never at face: each contract at its mark, the swap rows = the sum of the marks, no balance is a notional
        otc = {p.instrument_id: p for p in acc.positions(PB, snapshot=snap) if p.instrument_id in NOTIONAL_C} if PB in accounts else {}
        if marks is not None:
            self.assertEqual(marks, {k: p.mv_c for k, p in otc.items()}, f"{where}: contract marks")
        pos_sum = sum(max(0, p.mv_c) for p in otc.values())
        neg_sum = sum(max(0, -p.mv_c) for p in otc.values())
        rows = {r["key"]: int(r["base_c"]) for r in sheets[ALL].rows}
        self.assertEqual((pos_sum, neg_sum), (rows.get("swaps_pos", 0), rows.get("swaps_neg", 0)), f"{where}: swaps on the balance sheet at market")
        for cid, p in otc.items():
            self.assertLess(abs(p.mv_c), NOTIONAL_C[cid] // 10, f"{where}: {cid} carried near its notional")
        balances = {abs(int(r["balance_c"])) for r in f.q("SELECT balance_c FROM gl_balances")}
        row_values = {abs(int(r["base_c"])) for s in sheets.values() for r in s.rows}
        self.assertEqual(set(), (balances | row_values) & set(NOTIONAL_C.values()), f"{where}: a notional on the books")
        # -- collateral transfers change no P&L
        bad = f.q(f"SELECT e.entry_id, e.kind, e.idem_key, l.gl FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.entry_id"
                  f" WHERE (e.kind IN ({','.join('?' for _ in COLLATERAL_ENTRY_KINDS)}) OR e.idem_key LIKE 'swapvm:%')"
                  f" AND l.gl IN ({','.join('?' for g in CHART if CHART[g].kind in PNL_KINDS)})",
                  list(COLLATERAL_ENTRY_KINDS) + [g for g in CHART if CHART[g].kind in PNL_KINDS])
        self.assertEqual([], bad, f"{where}: a collateral / margin / sweep / settlement entry with a P&L line")
        # -- restricted = max(0, 2330 - 1320) + 2520, never more than the settled cash (never spent, never swept)
        from finsim.terminal import collateral
        with rt.db.reader() as conn:
            got = {a: collateral.restricted_c(conn, a, "USD") for a in accounts}
        mine = {a: max(0, f.bal(a, "2330") - f.bal(a, "1320")) + f.bal(a, "2520") for a in accounts}
        self.assertEqual(mine, got, f"{where}: restricted = net CSA collateral received + securities-lending cash collateral")
        if restricted is not None:
            self.assertEqual(restricted, {a: v for a, v in got.items() if v}, f"{where}: hand-worked restricted cash")
        for a in accounts:
            cash = acc.cash(a, snapshot=snap).get("USD")
            adj = {x["key"]: int(x["amount_c"]) for x in (cash.adjustments if cash is not None else [])}
            self.assertEqual(got[a], adj.get("restricted", 0), f"{where} {a}: the C9b adjuster 'restricted (collateral received)'")
            self.assertGreaterEqual(f.bal(a, "1010"), got[a], f"{where} {a}: restricted cash spent or swept")
        # -- business-date settled cash >= 0 at the close of every date, and the current settled cash
        today = rt.clock.today()
        with rt.db.reader() as conn:
            for a in accounts:
                d = START
                while d <= today:
                    self.assertGreaterEqual(cash_view_at(conn, a, "USD", d)["settled_c"], 0, f"{where} {a}: business-date cash below zero at the close of {d}")
                    d += timedelta(days=1)
                self.assertGreaterEqual(f.bal(a, "1010"), 0, f"{where} {a}: settled cash below zero now")
        # -- futures VM ties to the cash ledger
        vm = f.q("SELECT e.idem_key, l.gl, l.amount_c FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.entry_id WHERE e.idem_key LIKE 'vm:%'")
        by_entry = defaultdict(lambda: defaultdict(int))
        for r in vm:
            by_entry[r["idem_key"]][r["gl"]] += int(r["amount_c"])
        for idem, g in by_entry.items():
            self.assertEqual({"1010", "4400"}, set(g), f"{where} {idem}: a futures VM entry moves cash and 4400 only")
            self.assertEqual(g["1010"], -g["4400"], f"{where} {idem}: VM = the cash it moved")
        if fut_vm is not None:
            self.assertEqual(fut_vm, {k: g["1010"] for k, g in by_entry.items()}, f"{where}: hand-worked futures VM")
        self.assertEqual(sum(g["1010"] for g in by_entry.values()), sum(f.bal(a, "4400") for a in accounts), f"{where}: 4400 (natural) = the cash the VM moved")
        return navs

    # ---- small readers ----------------------------------------------------------------------------------------------------------------
    def pl(self, idem: str) -> int:
        """The NAV effect of one entry's P&L lines in cents (income +, expense -): minus the sum of its INCOME / EXPENSE lines."""
        return -sum(a for _, gl, a in self.f.lines(idem) if CHART[gl].kind in PNL_KINDS)

    def transfers(self, since: int = 0) -> list:
        return [(r["kind"], r["from_account"], r["to_account"], int(r["amount_c"]), r["business_date"]) for r in self.f.q(
            "SELECT * FROM transfers WHERE rowid > ? AND kind IN ('SWEEP_IN','SWEEP_OUT','TRANSFER') ORDER BY rowid", (since,))]

    def last_rowid(self, table: str) -> int:
        return int(self.f.q(f"SELECT COALESCE(MAX(rowid), 0) n FROM {table}")[0]["n"])

    # ==== TEST-04 ===================================================================================================================
    def test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step(self):
        self.f = f = FundBook(self, self.tmp)
        f.open_market()
        self.reconcile("start", nav=2500000000, marks={}, restricted={}, fut_vm={})
        # the model the CDX numbers below come from (module docstring), worked independently of the swaps_cds code
        self.assertEqual((-1568564, -1637098), (cdx_value_c(START, 88.5, DFS_400), cdx_value_c(START, 88.0, DFS_400)))
        self.assertEqual((-2730545, -2179848), (cdx_value_c(START, 80.0, DFS_410), cdx_value_c(date(2026, 10, 21), 84.0, DFS_410)))

        # ---- Tue 10-20 14:00: the bookings ------------------------------------------------------------------------------------------
        out = f.ok("POST", "/api/books/F/onboard", {"request_id": "ui:fund-onboard-gs", "broker": "GS"})
        self.assertEqual((PB, ["CSA", "ISDA_MASTER", "PBA"]), (out["account_id"], sorted(a["kind"] for a in out["agreements"])))
        self.reconcile("onboard GS", nav=2500000000)
        # equity long: 339,500.00 + 5.00 swept from Treasury, payable T+1; NAV - 5.00
        out = f.order("aapl", instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET")
        self.assertEqual([("339500.00", "5.00", "2026-10-21")], [(x["gross"], x["fee"], x["settle_date"]) for x in out["fills"]])
        self.assertEqual([("339505.00", TRE)], [(s["amount"], s["from_account"]) for s in out["sweeps"]])
        self.reconcile("BUY 1,000 AAPL", nav=2499999500)
        # physical short: 102 % collateral 510,000.00 = proceeds 499,995.00 + 10,005.00 of the account's own cash; NAV - 5.00
        out = f.order("msft", instrument_id="MSFT", side="SELL_SHORT", qty=1000, order_type="MARKET")
        self.assertEqual([("500000.00", "5.00")], [(x["gross"], x["fee"]) for x in out["fills"]])
        self.assertEqual([("GC", D("0.003"), D("0.0337"), 51000000)],
                         [(b["tier"], D(b["fee_rate"]), D(b["rebate_rate"]), int(b["collateral_c"])) for b in f.q("SELECT * FROM borrows")])
        self.assertEqual({(USX, "1400"): 51000000, (USX, "1200"): -49999500, (USX, "1010"): -1000500}, f.posted("collat:B-000001:fill:F-000002"))
        self.reconcile("SELL_SHORT 1,000 MSFT", nav=2499999000)
        # futures: IM + fee swept, the fee is cash the same day (no settlement row); NAV - 4.50 - 6.75
        out = f.order("gc", instrument_id="GCZ26", side="BUY", qty=2, order_type="MARKET")
        self.assertEqual(([("4.50", None)], [("24004.50", TRE)]), ([(x["fee"], x["settle_date"]) for x in out["fills"]], [(s["amount"], s["from_account"]) for s in out["sweeps"]]))
        out = f.order("zn", instrument_id="ZNZ26", side="SELL", qty=3, order_type="MARKET")
        self.assertEqual(([("6.75", None)], [("7806.75", TRE)]), ([(x["fee"], x["settle_date"]) for x in out["fills"]], [(s["amount"], s["from_account"]) for s in out["sweeps"]]))
        self.assertEqual(3180000, f.bal(USX, "1300"))                            # IM 24,000.00 + 7,800.00
        self.reconcile("futures long GC / short ZN", nav=2499997875)
        # the option spread, long leg first: premium 17,640.00 + 6.50 swept for the T+1 payable; 10,250.00 - 6.50 receivable; each leg -70.00
        out = f.order("p700", instrument_id=P700, side="BTO", qty=10, order_type="LIMIT", limit_px="17.64")
        self.assertEqual(([(D("17.64"), "17640.00", "6.50", "2026-10-21")], [("17646.50", TRE)]),
                         ([(D(str(x["price"])), x["gross"], x["fee"], x["settle_date"]) for x in out["fills"]], [(s["amount"], s["from_account"]) for s in out["sweeps"]]))
        self.reconcile("BTO 10 QQQ 700P", nav=2499990225)
        out = f.order("p675", instrument_id=P675, side="STO", qty=10, order_type="LIMIT", limit_px="10.25")
        self.assertEqual(([(D("10.25"), "10250.00", "6.50", "2026-10-21")], []),
                         ([(D(str(x["price"])), x["gross"], x["fee"], x["settle_date"]) for x in out["fills"]], [(s["amount"], s["from_account"]) for s in out.get("sweeps") or []]))
        self.reconcile("STO 10 QQQ 675P", nav=2499982575)
        # the OTC contracts in F1:PB_GS: IM swept and posted (Dr 1330 / Cr 1010); NAV moves by the cost of dealing only
        p = f.ok("POST", "/api/otc/preview", {"account_id": PB, "kind": "TRS", "terms": TRS_TERMS})
        self.assertEqual(("1291300.00", "129130.00", "0.00"), (p["notional"], p["im"], p["mark0"]))
        out = f.ok("POST", "/api/otc/contracts", {"request_id": "ui:fund-trs", "account_id": PB, "kind": "TRS", "terms": TRS_TERMS})
        self.assertEqual((TRS_ID, "129130.00"), (out["contract_id"], out["im_transfer"]["amount"]))
        self.reconcile("TRS short 10,000 ORCL", nav=2499982575, marks={TRS_ID: 0})
        p = f.ok("POST", "/api/otc/preview", {"account_id": PB, "kind": "CDX", "terms": CDX_TERMS})
        self.assertEqual(("60000.00", "-16370.98", "-15685.64", "18185.64", "received", "2026-10-21"),
                         (p["im"], p["mark0"], p["upfront"], p["initial_payment"]["amount"], p["initial_payment"]["direction"], p["initial_payment"]["settle_date"]))
        out = f.ok("POST", "/api/otc/contracts", {"request_id": "ui:fund-cdx", "account_id": PB, "kind": "CDX", "terms": CDX_TERMS})
        self.assertEqual((CDX_ID, "60000.00"), (out["contract_id"], out["im_transfer"]["amount"]))
        # the upfront received is the contract's cost (2345), the rebate the pre-trade part of the first coupon (2340): no day-one gain
        self.assertEqual({(PB, "2345"): -1568564, (PB, "2340"): -250000, (PB, "1200"): 1818564}, f.posted("otc:CDX-000001:upfront"))
        self.reconcile("CDX IG buy protection 3,000,000", nav=2499914041, marks={TRS_ID: 0, CDX_ID: -1637098})
        p = f.ok("POST", "/api/otc/preview", {"account_id": PB, "kind": "IRS", "terms": IRS_5Y})
        self.assertEqual(("0.04000", "0.04010", "-4491.29", "4491.29", "224564.50"), (p["par_rate"], p["dealt_rate"], p["mark0"], p["dv01"], p["im"]))
        out = f.ok("POST", "/api/otc/contracts", {"request_id": "ui:fund-irs", "account_id": PB, "kind": "IRS", "terms": IRS_5Y})
        self.assertEqual((IRS_ID, "224564.50"), (out["contract_id"], out["im_transfer"]["amount"]))
        navs = self.reconcile("pay-fixed IRS 10,000,000", nav=2499464912, marks={TRS_ID: 0, CDX_ID: -1637098, IRS_ID: -449129})
        self.assertEqual((2419734275, 0, 41369450), (navs[TRE], f.bal(PB, "1010"), f.bal(PB, "1330")))   # IM at GS 129,130.00 + 60,000.00 + 224,564.50

        # ---- Tue 10-20 evening -------------------------------------------------------------------------------------------------------
        f.rt.clock.set(ny("2026-10-20", "14:36"))
        f.prints("2026-10-20", {"14:29:30": {"GCZ26": "4150.00"}, "14:34": {"AAPL": "339.50", "MSFT": "500.00", "ORCL": "129.13", "QQQ": "690.00", "SPY": "769.32",
                                                                        "ZNZ26": "112.500000"}})
        f.chain("2026-10-20", "14:34", "690.00", ("17.50", "17.64"), ("10.25", "10.39"))
        ran = f.tick(ny("2026-10-20", "14:36"))
        self.assertEqual("DONE", ran[("FUTURES_VM", f"{USX}:GCZ26:2026-10-20")])
        gc1 = {f"vm:{USX}:GCZ26:2026-10-20": 200000}                            # 2 x (4,150 - 4,140) x 100
        self.reconcile("GC settle 10-20", nav=2499664912, fut_vm=gc1)
        f.rt.clock.set(ny("2026-10-20", "16:21"))
        f.prints("2026-10-20", {"16:00": {"AAPL": "340.00", "MSFT": "495.00", "ORCL": "127.13", "QQQ": "688.00", "SPY": "770.00"},
                                "15:59:30": {"ZNZ26": "112.250000"}})
        f.chain("2026-10-20", "16:00", "688.00", ("18.40", "18.60"), ("10.90", "11.10"))
        ran = f.tick(ny("2026-10-20", "16:21"))
        self.assertEqual("DONE", ran[("FUTURES_VM", f"{USX}:ZNZ26:2026-10-20")])
        fut_d1 = dict(gc1, **{f"vm:{USX}:ZNZ26:2026-10-20": 75000})              # -3 x (112.25 - 112.50) x 1,000
        marks_d1 = {TRS_ID: 2000000, CDX_ID: -1637098, IRS_ID: -449129}         # TRS model, intraday: -10,000 x (127.13 - 129.13)
        self.reconcile("closes 10-20", nav=2502314912, marks=marks_d1, fut_vm=fut_d1)
        ran = f.tick(ny("2026-10-20", "17:06"))
        self.assertEqual(("DONE", "DONE"), (ran[("SWAP_VM", f"{TRS_ID}:2026-10-20")], ran[("SWAP_VM", f"{IRS_ID}:2026-10-20")]))
        self.assertEqual({(PB, "1010"): 2000000, (PB, "2330"): -2000000}, f.posted(f"swapvm:{TRS_ID}:2026-10-20"))
        self.assertEqual({(PB, "1320"): 449129, (PB, "1010"): -449129}, f.posted(f"swapvm:{IRS_ID}:2026-10-20"))
        self.assertEqual({}, f.posted(f"swapvm:{CDX_ID}:2026-10-20"))           # the IG OAS of 10-20 is published on 10-21: pending, nothing posted
        self.reconcile("SWAP_VM 10-20", nav=2502314912, marks=marks_d1, restricted={PB: 1550871}, fut_vm=fut_d1)
        f.tick(ny("2026-10-20", "17:11"))
        self.assertEqual({(USX, "1010"): 510000, (USX, "1400"): -510000}, f.posted("cmark:B-000001:2026-10-20"))   # 510,000.00 - 504,900.00
        self.reconcile("collateral marks 10-20", nav=2502314912, marks=marks_d1, restricted={PB: 1550871}, fut_vm=fut_d1)
        x0 = self.last_rowid("transfers")
        f.tick(ny("2026-10-20", "17:31"))
        self.assertEqual([], self.transfers(x0))                                 # nothing idle: GS's 15,508.71 stays in the PB
        self.reconcile("SWEEP_OUT 10-20", nav=2502314912, marks=marks_d1, restricted={PB: 1550871}, fut_vm=fut_d1)
        f.tick(ny("2026-10-20", "17:46"))
        snaps = {r["account_id"]: int(r["nav_c"]) for r in f.q("SELECT account_id, nav_c FROM nav_snapshots WHERE snap_date='2026-10-20'")}
        self.assertEqual({ALL: 2502314912, TRE: 2419734275, USX: 39728850, PB: 42851787}, {k: v for k, v in snaps.items() if v})
        self.reconcile("SNAPSHOT 10-20", nav=2502314912, marks=marks_d1, restricted={PB: 1550871}, fut_vm=fut_d1)

        # ---- night 10-20 (Wed 00:36): settlements and the night's accruals -------------------------------------------------------------
        f.tick(ny("2026-10-21", "00:36"))
        self.assertEqual(["S-000001", "S-000003", "S-000004", "S-000005"],
                         [r["settle_id"] for r in f.q("SELECT settle_id FROM settlements WHERE status='SETTLED' AND amount_c<>0 ORDER BY settle_id")])
        night1 = {"accr:bfee:B-000001:2026-10-20": -412, "accr:rebate:B-000001:2026-10-20": 4726, "accr:cashint:F1:USD:2026-10-20": 226514,
                  f"accr:swaprcv:{IRS_ID}:2026-10-20": 107500, f"accr:swappay:{IRS_ID}:2026-10-20": -111389, f"accr:trsfin:{TRS_ID}:2026-10-20": 11729}
        self.assertEqual(night1, {k: self.pl(k) for k in night1})
        self.assertEqual([], f.q("SELECT idem_key FROM journal_entries WHERE idem_key LIKE ?", (f"accr:%{CDX_ID}:2026-10-20",)))   # in the rebate
        self.assertEqual(({(PB, "4453"): 111389, (PB, "2340"): -111389}, {(PB, "1225"): 11729, (PB, "4451"): -11729}),
                         (f.posted(f"accr:swappay:{IRS_ID}:2026-10-20"), f.posted(f"accr:trsfin:{TRS_ID}:2026-10-20")))   # PORTFOLIO-08: 4453 / 4451
        self.reconcile("night 10-20", nav=2502553580, marks=marks_d1, restricted={PB: 1550871}, fut_vm=fut_d1)

        # ---- Wed 10-21 ------------------------------------------------------------------------------------------------------------------
        f.rt.clock.set(ny("2026-10-21", "09:00"))
        f.rates([("2026-10-19", "3.87"), ("2026-10-20", "3.88")], [("2026-10-19", "0.88"), ("2026-10-20", "0.80")])
        f.curve(DFS_410, "2026-10-20")
        x0 = self.last_rowid("transfers")
        ran = f.tick(ny("2026-10-21", "09:00"))
        self.assertEqual("DONE", ran[("SWAP_VM", f"{CDX_ID}:2026-10-20")])
        self.assertEqual({(PB, "1320"): 2730545, (PB, "1010"): -2730545}, f.posted(f"swapvm:{CDX_ID}:2026-10-20"))
        self.assertEqual("2026-10-20", f.entry(f"swapvm:{CDX_ID}:2026-10-20")["business_date"])
        # C20 / D8: the PB's own cash at the close of 10-20 (15,508.71: GS's collateral may pay GS) + one as-of sweep dated 10-20 of the rest
        self.assertEqual([("SWEEP_IN", TRE, PB, 1179674, "2026-10-20")], self.transfers(x0))
        marks_d2 = {TRS_ID: 2000000, CDX_ID: -2730545, IRS_ID: 4031609}        # IRS on the 10-20 curve: 10,000,000 x 0.0009 x 4.479565725
        self.reconcile("FRED / curve 10-20, CDX VM 10-20", nav=2505940871, marks=marks_d2, restricted={}, fut_vm=fut_d1)
        f.rt.clock.set(ny("2026-10-21", "10:00"))
        f.prints("2026-10-21", {"09:58": {"AAPL": "341.00", "MSFT": "497.00", "ORCL": "128.00", "QQQ": "690.00", "SPY": "771.00", "GCZ26": "4135.00",
                                          "ZNZ26": "112.375000"}})
        f.chain("2026-10-21", "09:58", "690.00", ("17.40", "17.60"), ("10.20", "10.40"))
        marks_d2 = dict(marks_d2, **{TRS_ID: 1130000})                          # -10,000 x (128.00 - 129.13)
        self.reconcile("prints 10-21 10:00", nav=2504603371, marks=marks_d2, restricted={}, fut_vm=fut_d1)
        f.finra("AAPL", ("2026-10-20",))
        out = f.ok("POST", "/api/credit/lending", {"request_id": "ui:fund-lend-aapl", "account_id": USX, "instrument_id": "AAPL", "qty": 200, "counterparty_id": "AGENT_BNY"})
        pv = out["preview"]
        self.assertEqual(("GC", D("0.00255"), D("0.0338"), "69564.00"), (pv["tier"], D(pv["own_fee_rate"]), D(pv["rebate_rate"]), pv["collateral"]))
        self.assertEqual({(USX, "1010"): 6956400, (USX, "2520"): -6956400}, f.posted("seclend:SL-000001:open"))   # 1.02 x 200 x 341.00
        self.reconcile("lend 200 AAPL", nav=2504603371, marks=marks_d2, restricted={USX: 6956400}, fut_vm=fut_d1)
        f.rt.clock.set(ny("2026-10-21", "11:00"))                              # a live feed: every name printed again, fresh (C13 reads fresh inputs only)
        f.prints("2026-10-21", {"10:58": {"AAPL": "341.00", "MSFT": "497.00", "ORCL": "128.00", "QQQ": "690.00", "SPY": "771.00", "GCZ26": "4135.00",
                                          "ZNZ26": "112.375000"}})
        f.chain("2026-10-21", "10:58", "690.00", ("17.40", "17.60"), ("10.20", "10.40"))
        out = f.order("aapl-sell", instrument_id="AAPL", side="SELL", qty=400, order_type="MARKET")
        self.assertEqual([("136400.00", "2.00", "2026-10-22")], [(x["gross"], x["fee"], x["settle_date"]) for x in out["fills"]])
        self.assertEqual(60000, self.pl("fill:F-000007") + 200)                 # realised (341.00 - 339.50) x 400 = 600.00, the fee 2.00 apart
        self.reconcile("SELL 400 AAPL", nav=2504603171, marks=marks_d2, restricted={USX: 6956400}, fut_vm=fut_d1)
        f.rt.clock.set(ny("2026-10-21", "14:36"))
        f.prints("2026-10-21", {"14:29:30": {"GCZ26": "4130.00"}, "14:34": {"AAPL": "341.00", "MSFT": "497.00", "ORCL": "128.00", "QQQ": "690.00", "SPY": "771.00",
                                                                        "ZNZ26": "112.375000"}})
        f.chain("2026-10-21", "14:34", "690.00", ("17.40", "17.60"), ("10.20", "10.40"))
        f.tick(ny("2026-10-21", "14:36"))
        fut_d2 = dict(fut_d1, **{f"vm:{USX}:GCZ26:2026-10-21": -400000})         # 2 x (4,130 - 4,150) x 100
        self.reconcile("GC settle 10-21", nav=2504503171, marks=marks_d2, restricted={USX: 6956400}, fut_vm=fut_d2)
        f.rt.clock.set(ny("2026-10-21", "16:21"))
        f.prints("2026-10-21", {"16:00": {"AAPL": "341.00", "MSFT": "498.00", "ORCL": "128.13", "QQQ": "692.00", "SPY": "772.00"},
                                "15:59:30": {"ZNZ26": "112.406250"}})
        f.chain("2026-10-21", "16:00", "692.00", ("16.90", "17.10"), ("9.70", "9.90"))
        f.tick(ny("2026-10-21", "16:21"))
        fut_d2[f"vm:{USX}:ZNZ26:2026-10-21"] = -46875                           # -3 x (112.40625 - 112.25) x 1,000
        marks_d2 = dict(marks_d2, **{TRS_ID: 1000000})                          # -10,000 x (128.13 - 129.13)
        self.reconcile("closes 10-21", nav=2504263796, marks=marks_d2, restricted={USX: 6956400}, fut_vm=fut_d2)
        ran = f.tick(ny("2026-10-21", "17:06"))
        self.assertEqual(("DONE", "DONE"), (ran[("SWAP_VM", f"{TRS_ID}:2026-10-21")], ran[("SWAP_VM", f"{IRS_ID}:2026-10-21")]))
        self.assertEqual({(PB, "2330"): 1000000, (PB, "1010"): -1000000}, f.posted(f"swapvm:{TRS_ID}:2026-10-21"))
        # the IRS crosses zero: the 4,491.29 posted comes back and 40,316.09 is received, 44,807.38 of cash in all
        self.assertEqual({(PB, "1010"): 4480738, (PB, "1320"): -449129, (PB, "2330"): -4031609}, f.posted(f"swapvm:{IRS_ID}:2026-10-21"))
        self.assertEqual({}, f.posted(f"swapvm:{CDX_ID}:2026-10-21"))
        restricted_d2 = {PB: 2301064, USX: 6956400}                             # (20,000.00 - 10,000.00 + 40,316.09) - 27,305.45
        self.reconcile("SWAP_VM 10-21", nav=2504263796, marks=marks_d2, restricted=restricted_d2, fut_vm=fut_d2)
        f.tick(ny("2026-10-21", "17:11"))
        self.assertEqual({(USX, "1400"): 306000, (USX, "1010"): -306000}, f.posted("cmark:B-000001:2026-10-21"))   # 507,960.00 - 504,900.00
        self.assertEqual([], f.q("SELECT idem_key FROM journal_entries WHERE idem_key LIKE 'slmark:%'"))         # the lent AAPL closed at 341.00
        self.reconcile("collateral marks 10-21", nav=2504263796, marks=marks_d2, restricted=restricted_d2, fut_vm=fut_d2)
        x0 = self.last_rowid("transfers")
        f.tick(ny("2026-10-21", "17:31"))
        self.assertEqual([("SWEEP_OUT", PB, TRE, 1998238)], [t[:4] for t in self.transfers(x0)])   # 52,993.02 - restricted 23,010.64 - buffer 10,000.00
        self.assertEqual(3301064, f.bal(PB, "1010"))                            # GS's 23,010.64 and the buffer stay
        self.reconcile("SWEEP_OUT 10-21", nav=2504263796, marks=marks_d2, restricted=restricted_d2, fut_vm=fut_d2)
        f.tick(ny("2026-10-21", "17:46"))
        snaps = {r["account_id"]: int(r["nav_c"]) for r in f.q("SELECT account_id, nav_c FROM nav_snapshots WHERE snap_date='2026-10-21'")}
        self.assertEqual({ALL: 2504263796, TRE: 2420779353, USX: 39056089, PB: 44428354}, {k: v for k, v in snaps.items() if v})
        self.reconcile("SNAPSHOT 10-21", nav=2504263796, marks=marks_d2, restricted=restricted_d2, fut_vm=fut_d2)

        # ---- night 10-21 (Thu 00:36) ------------------------------------------------------------------------------------------------------
        f.tick(ny("2026-10-22", "00:36"))
        night2 = {"accr:bfee:B-000001:2026-10-21": -415, "accr:rebate:B-000001:2026-10-21": 4769, "accr:cashint:F1:USD:2026-10-21": 227075,
                  "accr:pbcredit:F1:PB_GS:2026-10-21": 261, f"accr:swaprcv:{IRS_ID}:2026-10-21": 107500, f"accr:swappay:{IRS_ID}:2026-10-21": -111389,
                  f"accr:swappay:{CDX_ID}:2026-10-21": -8333, f"accr:trsfin:{TRS_ID}:2026-10-21": 11765}
        self.assertEqual(night2, {k: self.pl(k) for k in night2})
        sl = {r["idem_key"]: self.pl(r["idem_key"]) for r in f.q("SELECT idem_key FROM journal_entries WHERE idem_key LIKE 'accr:sl%:SL-000001:2026-10-21'")}
        self.assertEqual(48 - 653, sum(sl.values()), sl)                        # fee 0.48 earned, rebate 6.53 paid to the borrower
        self.reconcile("night 10-21", nav=2504494424, marks=marks_d2, restricted=restricted_d2, fut_vm=fut_d2)

        # ---- Thu 10-22 09:00: FRED 10-21, the CDX VM of 10-21 ---------------------------------------------------------------------------
        f.rt.clock.set(ny("2026-10-22", "09:00"))
        f.rates([("2026-10-19", "3.87"), ("2026-10-20", "3.88"), ("2026-10-21", "3.90")], [("2026-10-19", "0.88"), ("2026-10-20", "0.80"), ("2026-10-21", "0.84")])
        ran = f.tick(ny("2026-10-22", "09:00"))
        self.assertEqual("DONE", ran[("SWAP_VM", f"{CDX_ID}:2026-10-21")])
        self.assertEqual({(PB, "1010"): 550697, (PB, "1320"): -550697}, f.posted(f"swapvm:{CDX_ID}:2026-10-21"))   # -21,798.48 - (-27,305.45)
        marks_d3 = dict(marks_d2, **{CDX_ID: -2179848})
        self.reconcile("CDX VM 10-21", nav=2505045121, marks=marks_d3, restricted={PB: 2851761, USX: 6956400}, fut_vm=fut_d2)
        # the business-date cash table, hand-worked (C9h): Treasury 25,000,000.00 - 802,657.25 - 11,796.74 (the as-of sweep of 10-20); the PB
        # at exactly 0.00 on 10-20; 10-21 + 18,185.64 - 10,000.00 + 44,807.38 + 5,506.97; 10-22 - the 19,982.38 sweep-out of 17:31 (dated 10-22)
        with f.rt.db.reader() as conn:
            table = {(a, d): cash_view_at(conn, a, "USD", d)["settled_c"] for a in (TRE, USX, PB) for d in ("2026-10-20", "2026-10-21", "2026-10-22")}
        self.assertEqual({(TRE, "2026-10-20"): 2418554601, (TRE, "2026-10-21"): 2418554601, (TRE, "2026-10-22"): 2420552839,
                          (USX, "2026-10-20"): 35499650, (USX, "2026-10-21"): 7012375, (USX, "2026-10-22"): 20652175,
                          (PB, "2026-10-20"): 0, (PB, "2026-10-21"): 5849999, (PB, "2026-10-22"): 3851761}, table)

    # ==== POSTCLOSE-06: an evening job of business date D posts on D ===================================================================
    def test_evening_jobs_post_on_their_own_business_date(self):
        """FINSIM1_PLAN 15.13 POSTCLOSE-06 ("A lifecycle job of business date D stamps its entries and its funding sweeps with D"): the
        17:05 swap VM, the futures VM, the 17:10 stock-borrow collateral mark AND the 17:10 securities-lending collateral mark of D carry
        business date D, like a VM caught up the next morning and its as-of sweep.  Here the lent AAPL closes 10-21 at 342.00 against its
        341.00 lend price, so the lending mark moves 1.02 x 200 x 1.00 = 204.00 of collateral in.  (SWEEP_OUT, 17:30 ET, is not part of
        this rule: it sweeps the CURRENT idle cash, which may hold post-cut-off cash of D+1, so it is dated by the accounting day; a D
        dating would let it sweep cash the account only receives on D+1.)"""
        self.f = f = FundBook(self, self.tmp)
        f.open_market()
        f.ok("POST", "/api/books/F/onboard", {"request_id": "ui:fund-onboard-gs", "broker": "GS"})
        f.order("aapl", instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET")
        f.order("msft", instrument_id="MSFT", side="SELL_SHORT", qty=1000, order_type="MARKET")
        f.order("gc", instrument_id="GCZ26", side="BUY", qty=2, order_type="MARKET")
        f.order("zn", instrument_id="ZNZ26", side="SELL", qty=3, order_type="MARKET")
        for kind, terms in (("TRS", TRS_TERMS), ("CDX", CDX_TERMS), ("IRS", IRS_5Y)):
            f.ok("POST", "/api/otc/contracts", {"request_id": f"ui:fund-{kind.lower()}", "account_id": PB, "kind": kind, "terms": terms})
        f.rt.clock.set(ny("2026-10-20", "16:21"))
        f.prints("2026-10-20", {"14:29:30": {"GCZ26": "4150.00"}, "15:59:30": {"ZNZ26": "112.250000"},
                                "16:00": {"AAPL": "340.00", "MSFT": "495.00", "ORCL": "127.13", "QQQ": "688.00", "SPY": "770.00"}})
        for at in ("16:21", "17:06", "17:11", "17:31", "17:46"):
            f.tick(ny("2026-10-20", at))
        f.tick(ny("2026-10-21", "00:36"))
        f.rt.clock.set(ny("2026-10-21", "09:00"))
        f.rates([("2026-10-19", "3.87"), ("2026-10-20", "3.88")], [("2026-10-19", "0.88"), ("2026-10-20", "0.80")])
        f.curve(DFS_410, "2026-10-20")
        f.tick(ny("2026-10-21", "09:00"))
        f.rt.clock.set(ny("2026-10-21", "10:00"))
        f.prints("2026-10-21", {"09:58": {"AAPL": "341.00", "MSFT": "497.00", "ORCL": "128.00", "QQQ": "690.00", "SPY": "771.00", "GCZ26": "4135.00",
                                          "ZNZ26": "112.375000"}})
        f.finra("AAPL", ("2026-10-20",))
        f.ok("POST", "/api/credit/lending", {"request_id": "ui:fund-lend-aapl", "account_id": USX, "instrument_id": "AAPL", "qty": 200, "counterparty_id": "AGENT_BNY"})
        f.rt.clock.set(ny("2026-10-21", "16:21"))
        f.prints("2026-10-21", {"14:29:30": {"GCZ26": "4130.00"}, "15:59:30": {"ZNZ26": "112.406250"},
                                "16:00": {"AAPL": "342.00", "MSFT": "498.00", "ORCL": "128.13", "QQQ": "692.00", "SPY": "772.00"}})
        for at in ("16:21", "17:06", "17:11", "17:31", "17:46"):
            f.tick(ny("2026-10-21", at))
        self.assertEqual({(USX, "1010"): 20400, (USX, "2520"): -20400}, f.posted("slmark:SL-000001:2026-10-21"))   # 1.02 x 200 x (342.00 - 341.00)
        dated = f.q("SELECT idem_key, business_date FROM journal_entries WHERE idem_key LIKE 'swapvm:%' OR idem_key LIKE 'vm:%' OR idem_key LIKE 'cmark:%'"
                    " OR idem_key LIKE 'slmark:%' ORDER BY entry_id")
        self.assertEqual({"swapvm", "vm", "cmark", "slmark"}, {r["idem_key"].split(":", 1)[0] for r in dated})
        self.assertEqual([], [(r["idem_key"], r["business_date"]) for r in dated if r["business_date"] != r["idem_key"].rsplit(":", 1)[1]],
                         "an evening posting of date D carries another business date")
        # their funding sweeps share the date: the CDX VM of 10-20, posted on 10-21 09:00, was funded by a sweep of 27,305.45 - 15,508.71 dated 10-20
        self.assertEqual([("SWEEP_IN", TRE, PB, 1179674, "2026-10-20")], [t for t in self.transfers() if t[3] == 1179674])


if __name__ == "__main__":
    unittest.main()
