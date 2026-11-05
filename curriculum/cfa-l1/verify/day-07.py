#!/usr/bin/env python3
"""Day 7 of CFA Math: every number that appears on day-07.html.

Week 1 review (Days 1-6): single sums, implied rate and N, real returns,
EAR and continuous compounding, ordinary annuities and annuities due
(CFA Program 2027 Level I, Quantitative Methods, LM1 and LM4).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal BA II Plus display (thousands separators)."""
    return "{:,.4f}".format(x)


def t4(x):
    """A 4-decimal TI-84 display (no separators, no leading zero)."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def pct(x, dp=4):
    return "{:.{}f}%".format(100 * x, dp)


def show(label, value):
    print("  {:<64} {}".format(label, value))


def pv_factor(r, n):
    """PV of 1 a period for n periods, paid at the END of each period."""
    return (1 - (1 + r) ** -n) / r


def same(a, b, tol=1e-9):
    assert abs(a - b) <= tol, (a, b)
    return "OK"


def solve(f, target, lo, hi):
    """Bisection for an increasing or decreasing f."""
    up = f(hi) > f(lo)
    for _ in range(400):
        mid = (lo + hi) / 2
        if (f(mid) < target) == up:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


print("=== Warm-up ===")
# w1 (Day 6): $1,000 at each year-end for 10 years at 7%, then paid in advance.
a, r, n = 1000.0, 0.07, 10
w1_ord = a * pv_factor(r, n)
w1_due = w1_ord * (1 + r)
show("w1  10-year factor at 7%", "{:.6f}".format(pv_factor(r, n)))
show("w1  PV ordinary (given in the stem)", money(w1_ord))
show("w1  B (right): PV due = ordinary x 1.07", "{!r} -> {}".format(w1_due, money(w1_due)))
show("w1  from the rounded stem: 7,023.58 x 1.07", money(7023.58 * 1.07))
show("w1  A: 1,000 + 7,023.58 (an 11th payment)", money(1000 + 7023.58))
show("w1  C: 7,023.58 / 1.07 (moved the wrong way)", "{}  exact {}".format(money(7023.58 / 1.07), money(w1_ord / 1.07)))
show("w1  split: 9-year factor at 7%", "{:.6f}".format(pv_factor(r, 9)))
show("w1  split: 1,000 + 9-payment ordinary", money(a + a * pv_factor(r, 9)))
show("w1  check split == due", same(a + a * pv_factor(r, 9), w1_due))

# w2 (Day 5): $30,000 car loan, 4.8% stated, 48 monthly payments at month-end.
pv, r, n = 30000.0, 0.048 / 12, 48
w2 = pv * r / (1 - (1 + r) ** -n)
show("w2  monthly rate 4.8 / 12 (%)", "{:.4f}".format(4.8 / 12))
show("w2  1.004^-48", "{:.6f}".format((1 + r) ** -n))
show("w2  48-month factor at 0.4%", "{:.6f}".format(pv_factor(r, n)))
show("w2  PMT (answer)", "{!r} -> {}  (BA PMT = {} / TI PMT={})".format(w2, money(w2), d4(-w2), t4(-w2)))
show("w2  by hand: 30,000 x 0.004", money(pv * r))
show("w2  by hand: 1 - 1.004^-48", "{:.6f}".format(1 - (1 + r) ** -n))
show("w2  by hand: 120 / 0.174377", money(120 / 0.174377))
show("w2  check 688.16 x 48-month factor", money(688.16 * pv_factor(r, n)))
show("w2  total paid 48 x 688.16", money(48 * 688.16))
show("w2  of which interest", money(48 * 688.16 - pv))
show("w2  trap: annual rate with monthly N (I/Y = 4.8, N = 48)", money(pv * 0.048 / (1 - 1.048 ** -48)))

print("\n=== Calculator settings check: $100 at 10% for 2 years ===")
fv_ok = 100 * 1.10 ** 2
fv_py12 = 100 * (1 + 0.10 / 12) ** 2
show("P/Y = 1: FV", "BA {}  TI {}".format(d4(fv_ok), t4(fv_ok)))
show("P/Y left at 12: FV (2 months at 10/12%)", "BA {}  TI {}".format(d4(fv_py12), t4(fv_py12)))

print("\n=== Check questions ===")
# c1 (Day 1): $8,000 at 5.5% for 15 years.
g = 1.055 ** 15
c1 = 8000 * g
show("c1  1.055^15", "{:.6f}".format(g))
show("c1  FV (answer)", "{!r} -> {}  (BA {} / TI {})".format(c1, money(c1), d4(c1), t4(c1)))
show("c1  simple interest would give 8,000 x (1 + 0.055 x 15)", money(8000 * (1 + 0.055 * 15)))
show("c1  interest on interest = FV - 14,600.00", money(c1 - 8000 * (1 + 0.055 * 15)))

# c2 (Day 2): $20,000 -> $35,000 in 9 years.
c2 = (35000 / 20000) ** (1 / 9) - 1
show("c2  35,000 / 20,000", "{:.4f}".format(35000 / 20000))
show("c2  1.75^(1/9)", "{:.6f}  (display {})".format(1.75 ** (1 / 9), d4(1.75 ** (1 / 9))))
show("c2  I/Y (answer, %)", "{!r} -> {}  (BA I/Y = {} / TI I%={})".format(100 * c2, pct(c2, 2), d4(100 * c2), t4(100 * c2)))
show("c2  check 20,000 x (1 + c2)^9", money(20000 * (1 + c2) ** 9))
show("c2  simple-interest trap: 75% / 9", pct(0.75 / 9, 2))

# c3 (Day 2): years to triple at 6%.
c3 = math.log(3) / math.log(1.06)
show("c3  ln 3 ; ln 1.06", "{:.6f} ; {:.6f}  (displays {} ; {} / TI {} ; {})".format(
    math.log(3), math.log(1.06), d4(math.log(3)), d4(math.log(1.06)), t4(math.log(3)), t4(math.log(1.06))))
show("c3  N (answer)", "{!r} -> {:.2f}  (BA N = {} / TI N={})".format(c3, c3, d4(c3), t4(c3)))
show("c3  Rule of 72: doubling years 72 / 6", "{:.0f}  (so tripling: between 12 and 24)".format(72 / 6))
show("c3  two doublings = x4 ; exact years to x4 at 6%", "{} ; {:.2f}".format(2 * 2, math.log(4) / math.log(1.06)))
show("c3  check 1.06^18.8542", "{:.4f}".format(1.06 ** 18.8542))

# c4 (Day 3): nominal 10%, inflation 6%, exact real.
c4 = 1.10 / 1.06 - 1
show("c4  C (right): 1.10 / 1.06 - 1", "{!r} -> {}  (BA {} then {} / TI {} then {})".format(
    100 * c4, pct(c4, 2), d4(c4), d4(100 * c4), t4(c4), t4(100 * c4)))
show("c4  1.10 / 1.06", "{:.6f}".format(1.10 / 1.06))
show("c4  A: shortcut 10% - 6%", pct(0.10 - 0.06, 2))
show("c4  B: multiplied 1.10 x 1.06 - 1", pct(1.10 * 1.06 - 1, 2))
show("c4  shortcut minus exact (points)", "{:.2f}".format(100 * (0.04 - c4)))
show("c4  same thing: 0.04 / 1.06", pct(0.04 / 1.06, 2))

# c5 (Day 3): conceptual, no numbers.

# c6 (Day 4): 11% stated, compounded daily (365).
c6 = (1 + 0.11 / 365) ** 365 - 1
cont11 = math.exp(0.11) - 1
show("c6  daily rate 0.11 / 365", "{:.8f}".format(0.11 / 365))
show("c6  EAR (answer)", "{!r} -> {}  (BA EFF = {} / TI {})".format(100 * c6, pct(c6, 2), d4(100 * c6), t4(100 * c6)))
show("c6  continuous ceiling e^0.11 - 1", "{} -> {}".format(pct(cont11), pct(cont11, 2)))
show("c6  gap ceiling - daily (points)", "{:.4f}".format(100 * (cont11 - c6)))
show("c6  monthly for comparison", pct((1 + 0.11 / 12) ** 12 - 1))

# c7 (Day 4): 7% continuous.
c7 = math.exp(0.07) - 1
show("c7  e^0.07", "{:.6f}  (BA {} / TI {})".format(math.exp(0.07), d4(math.exp(0.07)), t4(math.exp(0.07))))
show("c7  B (right): e^0.07 - 1", "{} -> {}  (BA {} then {} / TI {} then {})".format(
    pct(c7), pct(c7, 2), d4(c7), d4(100 * c7), t4(c7), t4(100 * c7)))
show("c7  A: stated taken as effective", pct(0.07, 2))
show("c7  C: monthly (1 + 0.07/12)^12 - 1", "{} -> {}".format(pct((1 + 0.07 / 12) ** 12 - 1), pct((1 + 0.07 / 12) ** 12 - 1, 2)))

# c8 (Day 5): $3,000 at the end of each quarter, 8 years, 8% stated quarterly.
a, r, n = 3000.0, 0.02, 32
c8 = a * pv_factor(r, n)
show("c8  N = 8 x 4 ; rate 8 / 4 (%)", "{} ; {}".format(8 * 4, 8 / 4))
show("c8  1.02^-32", "{:.6f}".format(1.02 ** -32))
show("c8  32-quarter factor at 2%", "{:.6f}".format(pv_factor(r, n)))
show("c8  PV (answer)", "{!r} -> {}  (BA PV = {} / TI PV={})".format(c8, money(c8), d4(-c8), t4(-c8)))
show("c8  trap: N = 8, I/Y = 8 with the quarterly payment", money(a * pv_factor(0.08, 8)))
show("c8  trap: N = 32, I/Y = 8 (annual rate, quarterly N)", money(a * pv_factor(0.08, 32)))
show("c8  sum of the 32 payments", money(a * 32))

# c9 (Day 6): lease $1,500 a month in advance, 3 years, 6% stated monthly.
a, r, n = 1500.0, 0.005, 36
f36 = pv_factor(r, n)
c9 = a * f36 * (1 + r)
c9_end = a * f36
show("c9  N = 3 x 12 ; rate 6 / 12 (%)", "{} ; {}".format(3 * 12, 6 / 12))
show("c9  36-month factor at 0.5%", "{:.6f}".format(f36))
show("c9  x 1.005", "{:.6f}".format(f36 * 1.005))
show("c9  PV due (answer)", "{!r} -> {}  (BA PV = {} / TI PV={})".format(c9, money(c9), d4(-c9), t4(-c9)))
show("c9  END-mode result (wrong)", "{}  (BA {})".format(money(c9_end), d4(-c9_end)))
show("c9  difference = one month of interest on the END value", "{}  ({})".format(money(c9 - c9_end), money(0.005 * c9_end)))
show("c9  split: 35-month factor", "{:.6f}".format(pv_factor(r, 35)))
show("c9  split: 1,500 + 35-payment ordinary", money(a + a * pv_factor(r, 35)))
show("c9  check split", same(a + a * pv_factor(r, 35), c9))
show("c9  sum of the 36 payments", money(a * 36))

# c10 (Days 2, 5, 6): at 5%, which is worth most today?
f10 = pv_factor(0.05, 10)
A = 10000 / 1.05 ** 10
B = 800 * f10
C = 780 * f10 * 1.05
C_end = 780 * f10
B_bgn = 800 * f10 * 1.05
show("c10 10-year factor at 5% ; x 1.05", "{:.6f} ; {:.6f}".format(f10, f10 * 1.05))
show("c10 1.05^10 ; 1.05^-10", "{:.6f} ; {:.6f}".format(1.05 ** 10, 1.05 ** -10))
show("c10 A: 10,000 / 1.05^10", "{}  (BA PV = {} / TI PV={})".format(money(A), d4(-A), t4(-A)))
show("c10 B: 800 x factor (END)", "{}  (BA PV = {} / TI PV={})".format(money(B), d4(-B), t4(-B)))
show("c10 C: 780 x factor x 1.05 (BGN, right)", "{}  (BA PV = {} / TI PV={})".format(money(C), d4(-C), t4(-C)))
show("c10 C in END (before x 1.05)", "{}  (BA {})".format(money(C_end), d4(-C_end)))
show("c10 trap: B with BGN left on", money(B_bgn))
show("c10 C - B", money(C - B))
show("c10 sums of payments: B ; C", "{} ; {}".format(money(8000), money(7800)))
assert C > B > A

show("c10 B's extra payment a year: 800 - 780", "{}".format(800 - 780))
show("decimal rates per period: c8 8%/4 ; c9 6%/12 ; w2 4.8%/12", "{:.3f} ; {:.3f} ; {:.3f}".format(0.08 / 4, 0.06 / 12, 0.048 / 12))

print("\n=== Traps block ===")
show("real-rate shortcut vs exact (c4 numbers)", "{} vs {}".format(pct(0.04, 2), pct(c4, 2)))
# Exact real = shortcut / (1 + inflation): lower when positive, a smaller loss
# when negative (Day 3's Turkiye story: 14% nominal, 36.08% inflation).
tr_exact = 1.14 / 1.3608 - 1
tr_short = 0.14 - 0.3608
show("Day 3 exact real 1.14 / 1.3608 - 1 ; shortcut 14 - 36.08", "{} ; {}".format(pct(tr_exact, 2), pct(tr_short, 2)))
show("check exact == shortcut / (1 + inflation), both cases", same(tr_exact, tr_short / 1.3608) + " " + same(c4, 0.04 / 1.06))
assert tr_exact > tr_short and c4 < 0.04
show("example: 6% stated, monthly, 5 years -> rate (%) ; N", "{} ; {}".format(6 / 12, 5 * 12))
