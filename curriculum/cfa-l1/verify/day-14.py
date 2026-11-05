#!/usr/bin/env python3
"""Day 14 of CFA Math: every number that appears on day-14.html.

Week 2 review: streams of cash and returns (Days 8-13: perpetuities, amortizing
loans, NPV and IRR, holding-period and chain-linked returns, the three means,
money- vs time-weighted returns). CFA Program 2027 Level I, Quantitative Methods.
Plain Python 3, standard library only. Run it and copy the printed numbers.

Calculator displays are shown at the 4 decimals both calculators are set to.
The BA II Plus shows thousands separators (PMT = -1,596.6470), the TI-84 not,
and the TI-84 drops the leading zero below 1 (.1355).
"""

import math
from decimal import Decimal


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus style, with separators)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """A TI-84 FLOAT 4 display: no thousands separators, no leading zero below 1."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def pct(x, nd=2):
    return "{:.{}f}%".format(100 * x, nd)


def show(label, value):
    print("  {:<64} {}".format(label, value))


def head(title):
    print()
    print("=== " + title + " ===")


def npv(rate, cfs):
    """cfs[t] is the cash flow at the end of period t (t = 0 is today)."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cfs))


def irr(cfs, lo=-0.99, hi=10.0):
    """Bisection on NPV; every stream used here has exactly one sign change."""
    f_lo = npv(lo, cfs)
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid, cfs)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def annuity_pmt(pv, r, n):
    return pv * r / (1 - (1 + r) ** -n)


def fv_annuity(a, r, n):
    return a * ((1 + r) ** n - 1) / r


def amortize(pv, r, pmt, k, round_to=None):
    """Balance, principal and interest over payments 1..k.
    round_to=4 imitates a calculator that rounds each payment's interest
    (and so the balance) to the 4 display decimals."""
    bal, prn, intr = pv, 0.0, 0.0
    for _ in range(k):
        i = bal * r
        if round_to is not None:
            i = round(i, round_to)
        p = pmt - i
        if round_to is not None:
            p = round(p, round_to)
        bal -= p
        if round_to is not None:
            bal = round(bal, round_to)
        prn += p
        intr += i
    return bal, prn, intr


# ---------------------------------------------------------------- warm-up
head("Warm-up w1 (from Day 13): TWR with a mid-year deposit")
v0, v_mid, dep, v_end = 50_000.0, 56_000.0, 24_000.0, 76_000.0
h1 = v_mid / v0 - 1
start2 = v_mid + dep
h2 = v_end / start2 - 1
twr_w1 = (1 + h1) * (1 + h2) - 1
show("HPR first half: 56,000 / 50,000 - 1", pct(h1))
show("value after the deposit: 56,000 + 24,000", money(start2))
show("HPR second half: 76,000 / 80,000 - 1", pct(h2))
show("growth factors 1.12 x 0.95", "{:.4f}".format((1 + h1) * (1 + h2)))
show("one-line chain 56000/50000 x 76000/80000 (BA / TI)",
     d4(v_mid / v0 * v_end / start2) + " / " + ti4(v_mid / v0 * v_end / start2))
show("w1 TWR (right answer)", "{} -> {}".format(twr_w1, pct(twr_w1)))
show("w1 trap: sub-period returns added 12% + (-5%)", pct(h1 + h2))
show("w1 trap: (76,000 - 24,000 - 50,000) / 50,000", pct((v_end - dep - v0) / v0))
# MWR with half-year periods: -50,000 at 0, -24,000 at 1/2, +76,000 at 1
x_half = irr([-v0, -dep, v_end])
mwr_w1 = (1 + x_half) ** 2 - 1
show("w1 MWR: half-year IRR of -50,000, -24,000, +76,000", pct(x_half, 4))
show("w1 MWR annualized (1 + x)^2 - 1", pct(mwr_w1))

head("Warm-up w2 (from Day 5): FV of $250 a month for 20 years at 6% stated")
a_w2, r_w2, n_w2 = 250.0, 0.06 / 12, 20 * 12
fv_w2 = fv_annuity(a_w2, r_w2, n_w2)
show("N = 20 x 12", n_w2)
show("I/Y = 6 / 12 (percent per month)", d4(6 / 12) + " (TI " + ti4(6 / 12) + ")")
show("w2 FV (answer)", "{} -> {}".format(fv_w2, money(fv_w2)))
show("w2 BA display (PMT entered as -250)", "FV = " + d4(fv_w2))
show("w2 TI display", "FV=" + ti4(fv_w2))
show("total deposited 240 x 250", money(n_w2 * a_w2))
show("interest earned on top", money(fv_w2 - n_w2 * a_w2))
show("w2 tolerance check: |115510.22 - fv|", "{:.4f}".format(abs(115510.22 - fv_w2)))

# ---------------------------------------------------------------- traps
head("Exam traps (illustrations)")
show("+50% then -50%: arithmetic mean", pct((0.5 - 0.5) / 2))
show("$100 x 1.5", money(100 * 1.5))
show("$100 x 1.5 x 0.5", money(100 * 1.5 * 0.5))
show("geometric mean sqrt(0.75) - 1", pct(math.sqrt(0.75) - 1))

# ---------------------------------------------------------------- calculator check
head("On your calculator: test runs you can check in your head")
pmt_t = annuity_pmt(1000.0, 0.10, 1)
show("one-payment loan: 1,000 at 10%, N = 1 -> PMT", "PMT = " + d4(-pmt_t) + " (TI " + ti4(-pmt_t) + ")")
bal_t, prn_t, int_t = amortize(1000.0, 0.10, pmt_t, 1)
show("AMORT P1 = P2 = 1: BAL / PRN / INT", "{} / {} / {}".format(d4(abs(bal_t)), d4(-prn_t), d4(-int_t)))
show("TI SumInt(1,1)", ti4(-int_t))
# diagnostics: BGN left on (payment at t = 0) and P/Y left at 12 (one month at 10/12 %)
show("if BGN is on: the one payment is today, PMT", d4(-1000.0))
show("if P/Y is 12: PMT = 1,000 x (1 + 0.10/12)", d4(-1000 * (1 + 0.10 / 12)))
cf_t = [-100.0, 110.0]
# Both calculators do decimal arithmetic, where 110 / 1.1 is exactly 100;
# binary floats print -0.0000 here, so compute this one in Decimal.
npv_t = Decimal(-100) + Decimal(110) / Decimal("1.1")
show("CF test: -100 then 110: NPV at 10% (decimal arithmetic)", "{:.4f}".format(npv_t))
show("CF test: IRR", d4(100 * irr(cf_t)) + " (TI " + ti4(100 * irr(cf_t)) + ")")
show("if an old C02 = 50 were left over: NPV at 10%", d4(npv(0.10, cf_t + [50.0])))

# ---------------------------------------------------------------- check
head("c1 (Day 12, harmonic): $600 each month at prices 16, 20, 25")
prices = [16.0, 20.0, 25.0]
shares = [600 / p for p in prices]
inv_sum = sum(1 / p for p in prices)
harm = len(prices) / inv_sum
show("shares bought 600/16, 600/20, 600/25", ", ".join("{:.2f}".format(s) for s in shares))
show("total shares", "{:.2f}".format(sum(shares)))
show("money spent 3 x 600", money(1800))
show("1,800 / total shares", "{:.4f}".format(1800 / sum(shares)))
show("1/16 + 1/20 + 1/25 (BA / TI)", d4(inv_sum) + " / " + ti4(inv_sum))
show("1 / that (BA 1/x)", d4(1 / inv_sum))
show("c1 harmonic mean 3 / sum (answer)", "{} -> {} -> ${:.2f}".format(harm, d4(harm), harm))
show("c1 trap: arithmetic mean of the prices", "{:.4f} -> ${:.2f}".format(sum(prices) / 3, sum(prices) / 3))

head("c2 (Day 8, mc): 10 a year forever, first payment end of year 4, r = 8%")
a2, r2, t2 = 10.0, 0.08, 4
v_at_t3 = a2 / r2
pv_right = v_at_t3 / (1 + r2) ** (t2 - 1)
pv_too_many = v_at_t3 / (1 + r2) ** t2
show("A / r = 10 / 0.08 (value at t = 3)", d4(v_at_t3))
show("1.08^3", "{:.6f}".format(1.08 ** 3))
show("c2 right: 125 / 1.08^3", "{} -> {}".format(pv_right, money(pv_right)))
show("c2 BA TVM: N=3 I/Y=8 PMT=0 FV=125 CPT PV", "PV = " + d4(-pv_right))
show("c2 TI: PV=", ti4(-pv_right))
show("c2 trap: 125 / 1.08^4 (discounted 4 years)", money(pv_too_many))
show("c2 trap: 125 not discounted", money(v_at_t3))
# cross-check by brute-force summing the first 3000 payments
brute = sum(a2 / (1 + r2) ** t for t in range(4, 3004))
show("check: sum of 10/1.08^t for t = 4..3003", money(brute))

head("c3 (Day 9): $250,000, 30 years, 6.6% stated, monthly")
pv3, r3, n3 = 250_000.0, 0.066 / 12, 360
pmt3 = annuity_pmt(pv3, r3, n3)
show("I/Y = 6.6 / 12", d4(6.6 / 12) + " (TI " + ti4(6.6 / 12) + ")")
show("c3 PMT (answer)", "{} -> {}".format(pmt3, money(pmt3)))
show("c3 BA display", "PMT = " + d4(-pmt3))
show("c3 TI display", "PMT=" + ti4(-pmt3))
show("first month's interest 0.0055 x 250,000", money(r3 * pv3))
show("first month's principal", money(pmt3 - r3 * pv3))
show("trap: annual rate with monthly N (I/Y = 6.6, N = 360)", money(annuity_pmt(pv3, 0.066, n3)))
show("trap: annual payment / 12 (N = 30, I/Y = 6.6)", money(annuity_pmt(pv3, 0.066, 30) / 12))

head("c4 (Day 9, AMORT / SumInt): payments 1-12 of the c3 loan")
bal_u, prn_u, int_u = amortize(pv3, r3, pmt3, 12)
bal_r, prn_r, int_r = amortize(pv3, r3, pmt3, 12, round_to=4)
show("unrounded: balance after 12", "{} -> {}".format(bal_u, money(bal_u)))
show("unrounded: principal repaid in 1-12", "{} -> {}".format(prn_u, money(prn_u)))
show("unrounded: interest paid in 1-12 (answer)", "{} -> {}".format(int_u, money(int_u)))
show("4-decimal rounding each month: BAL", d4(bal_r))
show("4-decimal rounding each month: PRN", d4(-prn_r))
show("4-decimal rounding each month: INT", d4(-int_r))
show("TI bal(12) / SumPrn(1,12) / SumInt(1,12) (unrounded)",
     "{} / {} / {}".format(ti4(bal_u), ti4(-prn_u), ti4(-int_u)))
show("BA AMORT BAL / PRN / INT (unrounded, 4 dp)",
     "{} / {} / {}".format(d4(bal_u), d4(-prn_u), d4(-int_u)))
show("spread between the two INT figures", "{:.4f}".format(abs(int_u - int_r)))
bal_2, prn_2, int_2 = amortize(pv3, r3, pmt3, 12, round_to=2)
show("if the calculator is left at 2 decimals: INT (rounded monthly)", "{:.4f}".format(int_2))
bal_p, prn_p, int_p = amortize(pv3, r3, round(pmt3, 2), 12)
show("if the rounded PMT 1,596.65 is retyped: INT / BAL", "{:.4f} / {:.4f}".format(int_p, bal_p))
show("c4 tolerance 0.10 covers: |16418.05 - each INT above|",
     ", ".join("{:.4f}".format(abs(16418.05 - x)) for x in (int_u, int_r, int_2, int_p)))
show("c4 worst case vs tolerance 0.10",
     "{:.4f} <= 0.10: {}".format(max(abs(int_u - x) for x in (int_r, int_2, int_p)),
                                  max(abs(int_u - x) for x in (int_r, int_2, int_p)) <= 0.10))
# balance after 12 as the PV of the remaining 348 payments
bal_pv = pmt3 * (1 - (1 + r3) ** -(n3 - 12)) / r3
show("check: balance = PV of the 348 remaining payments", money(bal_pv))
show("PMT to 6 decimals (used in the hand check)", "{:.6f}".format(pmt3))
show("check: 12 x PMT", money(12 * pmt3) + " (4 dp " + d4(12 * pmt3) + ")")
show("check: 12 x 1,596.647045 (the page's hand-check product)", "{:,.4f}".format(12 * 1596.647045))
show("note: 12 x 1,596.6470 (4-dp PMT) would give", "{:,.4f}".format(12 * 1596.6470))
show("check: 250,000 - balance (4 dp)", d4(pv3 - bal_u))
show("check: 12 x PMT - (250,000 - balance), 4 dp", d4(12 * pmt3 - (pv3 - bal_u)))
show("note: cent-rounded 19,159.76 - 2,741.72", money(19159.76 - 2741.72) + " (why the page uses 4 decimals)")
show("check: 12 x PMT - principal repaid = interest", money(12 * pmt3 - prn_u))
show("share of year-1 payments that was interest", pct(int_u / (12 * pmt3)))

head("c5 / c6 (Day 10): -5,000; 1,500; 2,000; 2,500; 1,000")
cfs5 = [-5000.0, 1500.0, 2000.0, 2500.0, 1000.0]
npv5 = npv(0.10, cfs5)
irr5 = irr(cfs5)
for t, cf in enumerate(cfs5[1:], start=1):
    show("PV of year {} at 10%: {:,.0f} / 1.1^{}".format(t, cf, t), money(cf / 1.1 ** t))
show("sum of the PVs of the inflows", money(npv5 + 5000))
show("c5 NPV at 10% (answer)", "{} -> {}".format(npv5, money(npv5)))
show("c5 BA display / TI display", "NPV = " + d4(npv5) + " / " + ti4(npv5))
show("c6 IRR (answer, percent)", "{} -> {}".format(100 * irr5, pct(irr5)))
show("c6 BA display / TI display", "IRR = " + d4(100 * irr5) + " / " + ti4(100 * irr5))
show("check: NPV at the IRR", "{:.8f}".format(npv(irr5, cfs5)))
show("trap: CF0 forgotten (NPV of inflows only)", money(npv5 + 5000))
show("trap: an old C05 = 800 left in the worksheet", money(npv(0.10, cfs5 + [800.0])))

head("c7 (Day 11): bought 80.00, dividend 2.00, sold 86.00 after nine months")
hpr7 = (86.0 - 80.0 + 2.0) / 80.0
ann7 = (1 + hpr7) ** (12 / 9) - 1
show("86 - 80 + 2", d4(86 - 80 + 2))
show("HPR = 8 / 80", "{} (TI {})".format(d4(hpr7), ti4(hpr7)))
show("12 / 9", d4(12 / 9))
show("1.10^(12/9)", "{} (TI {})".format(d4(1 + ann7), ti4(1 + ann7)))
show("c7 annualized HPR (answer)", "{} -> {}".format(100 * ann7, pct(ann7)))
show("c7 displays after -1 (BA / TI) and x 100", "{} / {} ; {}".format(d4(ann7), ti4(ann7), d4(100 * ann7)))
show("c7 trap: simple scaling 10% x 12/9", pct(hpr7 * 12 / 9))
show("c7 trap: dividend left out, (86/80)^(12/9) - 1", pct((86 / 80) ** (12 / 9) - 1))

head("c8 (Day 11, mc): -30% then +30%")
tot8 = 0.70 * 1.30 - 1
show("0.70 x 1.30", "{:.4f}".format(0.70 * 1.30))
show("c8 right: two-year return", pct(tot8))
show("c8 trap: returns added", pct(-0.30 + 0.30))
show("c8 trap: annualized geometric mean sqrt(0.91) - 1", pct(math.sqrt(0.91) - 1))
show("$1,000 -> after year 1 -> after year 2", "{} -> {}".format(money(1000 * 0.7), money(1000 * 0.7 * 1.3)))
show("gain needed after -30%: 0.30 / 0.70", pct(0.30 / 0.70))

head("c9 (Day 12): +20%, -10%, +15%")
rets9 = [0.20, -0.10, 0.15]
arith9 = sum(rets9) / 3
prod9 = 1.0
for r in rets9:
    prod9 *= 1 + r
root9 = prod9 ** (1 / 3)
geo9 = root9 - 1
show("1-V x-bar (percent)", d4(100 * arith9) + " (TI " + ti4(100 * arith9) + ") -> " + pct(arith9))
show("product 1.2 x 0.9 x 1.15", d4(prod9) + " (TI " + ti4(prod9) + ")")
show("cube root", d4(root9) + " (TI " + ti4(root9) + ")")
show("root - 1", d4(geo9) + " (TI " + ti4(geo9) + ")")
show("c9 geometric mean (answer, percent)", "{} -> {} -> {}".format(100 * geo9, d4(100 * geo9), pct(geo9)))
show("check: $10,000 x product", money(10000 * prod9))
show("check: $10,000 x root^3", money(10000 * root9 ** 3))
show("gap arithmetic - geometric (points)", "{:.2f}".format(100 * (arith9 - geo9)))

head("c10 (Day 13, mc): 100 sh at 20, 100 more at 22 a year later, sell 200 at 21.50")
cf10 = [-2000.0, -2200.0, 4300.0]
mwr10 = irr(cf10)
h10_1 = 22 / 20 - 1
h10_2 = 21.5 / 22 - 1
twr10_2y = (1 + h10_1) * (1 + h10_2) - 1
twr10 = math.sqrt(1 + twr10_2y) - 1
show("CF0 = -100 x 20", money(cf10[0]))
show("CF1 = -100 x 22", money(cf10[1]))
show("CF2 = +200 x 21.50", money(cf10[2]))
show("c10 MWR = IRR (BA IRR / TI irr)", "{} -> {} / {} -> {}".format(100 * mwr10, d4(100 * mwr10), ti4(100 * mwr10), pct(mwr10)))
show("check: NPV at the MWR", "{:.8f}".format(npv(mwr10, cf10)))
# by hand: 4300 v^2 - 2200 v - 2000 = 0
disc = 2200 ** 2 + 4 * 4300 * 2000
v = (2200 + math.sqrt(disc)) / (2 * 4300)
show("by hand: v = (2,200 + sqrt(2,200^2 + 4 x 4,300 x 2,000)) / 8,600", "{:.6f} -> MWR {}".format(v, pct(1 / v - 1)))
show("HPR year 1: 22/20 - 1", pct(h10_1))
show("HPR year 2: 21.50/22 - 1", pct(h10_2, 4))
show("1.1 x 21.5 / 22 (BA / TI)", d4((1 + h10_1) * (1 + h10_2)) + " / " + ti4((1 + h10_1) * (1 + h10_2)))
show("TWR two years", pct(twr10_2y))
show("sqrt(1.075)", d4(math.sqrt(1 + twr10_2y)) + " (TI " + ti4(math.sqrt(1 + twr10_2y)) + ")")
show("c10 TWR annualized", "{} -> {} (TI {}) -> {}".format(twr10, d4(twr10), ti4(twr10), pct(twr10)))
show("c10 trap: two-year TWR halved", pct(twr10_2y / 2))
show("money in during year 1 / year 2", "{} / {}".format(money(2000), money(2 * 2200)))
show("dollar gain: 4,300 - 2,000 - 2,200", money(4300 - 2000 - 2200))

head("Summary: data-answer values (full precision)")
show("w1 (mc) right answer TWR", pct(twr_w1))
show("w2 FV", repr(fv_w2))
show("c1 harmonic", repr(harm))
show("c2 (mc) right answer", money(pv_right))
show("c3 PMT", repr(pmt3))
show("c4 interest 1-12", repr(int_u))
show("c5 NPV", repr(npv5))
show("c6 IRR percent", repr(100 * irr5))
show("c7 annualized HPR percent", repr(100 * ann7))
show("c8 (mc) right answer", pct(tot8))
show("c9 geometric mean percent", repr(100 * geo9))
show("c10 (mc) right answer", "MWR {}, TWR {}".format(pct(mwr10), pct(twr10)))
