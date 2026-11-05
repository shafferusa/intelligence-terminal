#!/usr/bin/env python3
"""Day 2 of CFA Math: every number that appears on day-02.html.

Discounting: present value, implied rate and number of periods
(CFA Program 2027 Level I, Quantitative Methods, LM4 The Time Value of Money in Finance).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def pv(fv, r, n):
    return fv / (1 + r) ** n


def fv(p, r, n):
    return p * (1 + r) ** n


def rate(p, f, n):
    return (f / p) ** (1.0 / n) - 1


def periods(p, f, r):
    return math.log(f / p) / math.log(1 + r)


def show(label, value):
    print("  {:<58} {}".format(label, value))


print("=== Warm-up (from Day 1) ===")
w1 = fv(2500, 0.07, 15)
show("w1  1.07^15", "{:.6f}".format(1.07 ** 15))
show("w1  FV of 2,500 at 7% for 15 years (answer)", "{!r}  -> {}".format(w1, money(w1)))
w2 = fv(2000, 0.06, 10)
show("w2  1.06^10", "{:.6f}".format(1.06 ** 10))
show("w2  FV of 2,000 at 6% for 10 years (PV entered +)", "{!r}  -> BA shows FV = -{} / TI FV=-{:.4f}".format(w2, d4(w2), w2))

print("\n=== Story: Manhattan, 1626 -> 2014 ===")
N = 2014 - 1626
show("years 1626 -> 2014", N)
show("60 guilders x $0.40", "{:.2f}".format(60 * 0.40))
P0 = 24.0
FV_PUB = 1.74e12          # Barr, Smith & Kulkarni (2018), published estimate for 2014
FV_LO, FV_HI = 1.54e12, 1.95e12
FV_2016 = 1.4e12          # 2016 working-paper figure, widely reported
growth = FV_PUB / P0
show("growth factor FV/PV = 1.74 trillion / 24", "{:,.0f}  ({:.4e})".format(growth, growth))
r_pub = rate(P0, FV_PUB, N)
show("implied rate, $1.74 trillion (decimal)", repr(r_pub))
show("implied rate, $1.74 trillion (percent, 4 dp)", "{:.4f}%".format(100 * r_pub))
show("implied rate, $1.74 trillion (percent, 2 dp)", "{:.2f}%".format(100 * r_pub))
show("1 + r", "{:.6f}".format(1 + r_pub))
for lab, f in (("low end $1.54 trillion", FV_LO), ("high end $1.95 trillion", FV_HI), ("2016 figure $1.4 trillion", FV_2016)):
    show("implied rate, " + lab, "{:.4f}%  ({:.2f}%)".format(100 * rate(P0, f, N), 100 * rate(P0, f, N)))
show("check: 24 x (1 + r)^388", "{:,.0f}".format(fv(P0, r_pub, N)))
print("  Discount $1.74 trillion back 388 years:")
for r in (0.06, 0.07, 0.08):
    p = pv(FV_PUB, r, N)
    show("  PV at {:.0f}%".format(100 * r), "{!r}  -> ${}".format(p, money(p)))
    show("  (1 + r)^388 at {:.0f}%".format(100 * r), "{:.4e}".format((1 + r) ** N))
print("  $24 compounded 388 years:")
for r in (0.06, 0.07):
    f = fv(P0, r, N)
    show("  FV of $24 at {:.0f}%".format(100 * r), "{:,.0f}  (${:.2f} trillion / ${:.0f} billion)".format(f, f / 1e12, f / 1e9))

print("\n=== Calculator walkthrough ($ thousands) ===")
PVk, FVk = -P0 / 1000, FV_PUB / 1000
show("PV in $ thousands", PVk)
show("FV in $ thousands", "{:,.0f}  (digits: {})".format(FVk, len(str(int(FVk)))))
show("digits in 1,740,000,000,000", len(str(int(FV_PUB))))
r_k = rate(-PVk, FVk, N)
show("CPT I/Y (percent)", "{!r} -> I/Y = {:.4f}".format(100 * r_k, 100 * r_k))
show("same as in dollars?", abs(r_k - r_pub) < 1e-15)
n2 = periods(1, 2, 0.07)
show("part 2: CPT N, double at 7%", "{!r} -> N = {:.4f}".format(n2, n2))
show("ln 2", "{:.4f}".format(math.log(2)))
show("ln 1.07", "{:.4f}".format(math.log(1.07)))
show("ln 2 / ln 1.07", "{:.4f}".format(math.log(2) / math.log(1.07)))
show("Rule of 72: 72 / 7", "{:.4f}".format(72 / 7))
show("check: 1.07^10.2448", "{:.4f}".format(1.07 ** n2))
show("why 72: ln 2 x 100", "{:.2f}".format(100 * math.log(2)))

print("\n=== Why it works: one year back at 5% ===")
show("100 x 1.05", "{:.2f}".format(100 * 1.05))
show("105 / 1.05", "{:.2f}".format(105 / 1.05))
show("110.25 / 1.05^2", "{:.2f}".format(110.25 / 1.05 ** 2))
show("1.05^2", "{:.4f}".format(1.05 ** 2))

print("\n=== Worked example: $5,000 in 6 years ===")
f6 = 1.05 ** 6
show("1.05^6", "{:.6f}".format(f6))
DF = 1 / f6
show("DF = 1/1.05^6", "{:.6f}".format(DF))
pva = 5000 * DF
show("(a) PV = 5,000 x DF", "{!r} -> {}".format(pva, money(pva)))
ratio = 5000 / 3500
show("5,000 / 3,500", "{:.6f}".format(ratio))
show("ratio^(1/6)", "{:.6f}".format(ratio ** (1 / 6)))
rb = rate(3500, 5000, 6)
show("(b) r", "{:.6f} -> {:.2f}%".format(rb, 100 * rb))
show("ln(5,000/3,500)", "{:.6f}".format(math.log(ratio)))
show("ln 1.05", "{:.6f}".format(math.log(1.05)))
nc = periods(3500, 5000, 0.05)
show("(c) N", "{:.6f} -> {:.2f} years".format(nc, nc))
show("check: 3,731.08 x 1.05^6", money(3731.08 * f6))

print("\n=== Check question 1: painting, 1995 -> 2025 ===")
n_c1 = 2025 - 1995
c1 = 100 * rate(12000, 85000, n_c1)
show("N", n_c1)
show("85,000 / 12,000", "{:.6f}".format(85000 / 12000))
show("c1 CPT I/Y (answer, percent)", "{!r} -> I/Y = {:.4f} -> {:.2f}%".format(c1, c1, c1))

print("\n=== Check question 2: $1,000 at 5 and 30 years, 4% -> 5% ===")
for n in (5, 30):
    a, b = pv(1000, 0.04, n), pv(1000, 0.05, n)
    show("N={}: PV at 4%, PV at 5%".format(n), "{} , {}".format(money(a), money(b)))
    show("N={}: percent change".format(n), "{:.2f}%".format(100 * (b / a - 1)))

print("\n=== Check question 3: $40,000 -> $100,000 at 6.5% ===")
c3 = periods(40000, 100000, 0.065)
show("ln 2.5", "{:.6f}".format(math.log(2.5)))
show("ln 1.065", "{:.6f}".format(math.log(1.065)))
show("c3 N (answer)", "{!r} -> {:.4f} -> {:.2f}".format(c3, c3, c3))
show("check: 40,000 x 1.065^N", money(fv(40000, 0.065, c3)))

print("\n=== Check question 4: $250,000 in 12 years at 7% ===")
c4 = pv(250000, 0.07, 12)
show("1.07^12", "{:.6f}".format(1.07 ** 12))
show("C (correct) 250,000 / 1.07^12", "{!r} -> {} (BA: PV = -{})".format(c4, money(c4), d4(c4)))
c4a = 250000 / (1 + 0.07 * 12)
show("A (simple) 250,000 / (1 + 0.07 x 12)", money(c4a))
c4b = 250000 / (1 + 0.07 / 12) ** 12
show("B (P/Y = 12) 250,000 / (1 + 0.07/12)^12", money(c4b))

print("\n=== Extra numbers used in the prose ===")
show("w1 4-decimal display", "BA FV = {} / TI FV={:.4f}".format(d4(w1), w1))
trip = pv(1000, 0.05, 2)
show("The idea: $1,000 needed in 2 years at 5%", "{!r} -> {}".format(trip, money(trip)))
show("DF at 5% for 6 years (4 dp)", "{:.4f}".format(1 / 1.05 ** 6))
show("PV(6%)/PV(7%) for 388 years", "{:.1f}".format(pv(FV_PUB, 0.06, N) / pv(FV_PUB, 0.07, N)))
show("PV(7%)/PV(8%) for 388 years", "{:.1f}".format(pv(FV_PUB, 0.07, N) / pv(FV_PUB, 0.08, N)))
show("PV at 8% in cents", "{:.1f} cents".format(100 * pv(FV_PUB, 0.08, N)))
show("change in land value, 1.4 -> 1.74 trillion", "{:.1f}%".format(100 * (FV_PUB / FV_2016 - 1)))
show("rate change, 1.4 -> 1.74 trillion (points)", "{:.4f}".format(100 * (r_pub - rate(P0, FV_2016, N))))
print("  Rule of 72: exact doubling time vs 72/r vs 69.3/r, and the exact constant r% x N")
for rp in (2, 4, 6, 7, 8, 10, 12):
    ex = math.log(2) / math.log(1 + rp / 100)
    show("  r = {}%".format(rp), "exact {:.4f}  72/r {:.4f}  69.3/r {:.4f}  constant {:.2f}".format(ex, 72 / rp, 69.3 / rp, rp * ex))
show("c2: 1.05/1.04", "{:.6f}".format(1.05 / 1.04))
show("c2: (1.04/1.05)^5", "{:.4f}".format((1.04 / 1.05) ** 5))
show("c2: (1.04/1.05)^30", "{:.4f}".format((1.04 / 1.05) ** 30))
show("c3 TVM display", "N = {:.4f}".format(c3))
show("c4 TI display", "PV=-{:.4f}".format(c4))
show("c4 B as the BA computes it: N=12, I/Y=7, P/Y=12", money(pv(250000, 0.07 / 12, 12)))
show("Rule of 72 at 7%, 2 dp", "{:.2f}".format(72 / 7))
show("PV at 8%, nearest cent", "{:.0f} cents".format(100 * pv(FV_PUB, 0.08, N)))
show("DF at 5%/6y in cents (about)", "{:.0f} cents".format(100 / 1.05 ** 6))
show("smallest ratio of PVs one point apart (> 35?)", "{:.1f} -> {}".format(
    min(pv(FV_PUB, 0.06, N) / pv(FV_PUB, 0.07, N), pv(FV_PUB, 0.07, N) / pv(FV_PUB, 0.08, N)),
    min(pv(FV_PUB, 0.06, N) / pv(FV_PUB, 0.07, N), pv(FV_PUB, 0.07, N) / pv(FV_PUB, 0.08, N)) > 35))
show("Rule of 72 at 7% minus exact (years)", "{:.4f} -> {:.2f} of a year".format(72 / 7 - n2, 72 / 7 - n2))
show("  ... in days (about two weeks?)", "{:.1f} days".format(365 * (72 / 7 - n2)))
show("72 divides evenly by", [k for k in range(2, 13) if 72 % k == 0])
show("tolerances: |6.74 - c1|, |14.55 - c3|, |6897.58 - w1|", "{:.4f} {:.4f} {:.4f}".format(abs(6.74 - c1), abs(14.55 - c3), abs(6897.58 - w1)))
