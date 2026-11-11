#!/usr/bin/env python3
"""Day 36 of CFA Math: every number that appears on day-36.html.

Spot rates and bootstrapping
(CFA Program 2027 Level I, Fixed Income, The Term Structure of Interest Rates:
Spot, Par, and Forward Curves; building on Fixed-Income Bond Valuation: Prices
and Yields and Yield and Yield Spread Measures for Fixed-Rate Bonds).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math


def ba(x):
    """BA II Plus display: 4 decimals, thousands separators."""
    return "{:,.4f}".format(x)


def ti(x):
    """TI-84 Plus display: FLOAT 4, no separators, no leading zero."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    if s.startswith("-0."):
        s = "-" + s[2:]
    return s


def show(label, value):
    print("  {:<72} {}".format(label, value))


def pv_at(cfs, rates):
    """Price: cash flow t (t = 1, 2, ...) discounted at its own rate (decimal)."""
    return sum(cf / (1 + r) ** t for t, (cf, r) in enumerate(zip(cfs, rates), start=1))


def bond_pv(pmt, fv, r, n):
    """Price at one rate r per period: n coupons of pmt, fv with the last one.
    Summed term by term (the definition), not from the annuity formula."""
    return sum(pmt / (1 + r) ** t for t in range(1, n + 1)) + fv / (1 + r) ** n


def tvm_iy(n, pv, pmt, fv):
    """What CPT I/Y shows (END mode, P/Y = C/Y = 1), in percent per period.
    pv negative (paid), pmt and fv positive. Bisection: the PV of the cash
    received falls as the rate rises, so one rate fits."""
    price = -pv
    lo, hi = -0.99, 1.0
    for _ in range(300):
        mid = (lo + hi) / 2
        if bond_pv(pmt, fv, mid, n) > price:
            lo = mid
        else:
            hi = mid
    return 100 * (lo + hi) / 2


def bootstrap(par_pct):
    """Discount factors from annual-pay par yields (percent), shortest first.
    Each N-year par bond: 100 = c x (DF_1 + ... + DF_(N-1)) + (100 + c) x DF_N."""
    dfs = []
    for c in par_pct:
        dfs.append((100 - c * sum(dfs)) / (100 + c))
    return dfs


def spot(df, t):
    """Z_t = (1/DF_t)^(1/t) - 1, as a decimal."""
    return (1 / df) ** (1 / t) - 1


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 33): sigma_1 = 8%, sigma_2 = 6%, rho = 0 -> minimum-variance portfolio's sigma
s1, s2 = 8.0, 6.0
cov = 0.0 * s1 * s2
w1 = (s2 ** 2 - cov) / (s1 ** 2 + s2 ** 2 - 2 * cov)
w2 = 1 - w1
var_p = w1 ** 2 * s1 ** 2 + w2 ** 2 * s2 ** 2 + 2 * w1 * w2 * cov
sig_p = math.sqrt(var_p)
show("w1  Cov = 0 x 8 x 6", "{:.4f}".format(cov))
show("w1  w_1* = 6^2 / (8^2 + 6^2) = 36 / 100", "{!r} -> BA {} / TI {}".format(w1, ba(w1), ti(w1)))
show("w1  w_2 = 1 - w_1*", "{:.4f}".format(w2))
show("w1  0.36^2 x 64", "{:.4f}".format(w1 ** 2 * s1 ** 2))
show("w1  0.64^2 x 36", "{:.4f}".format(w2 ** 2 * s2 ** 2))
show("w1  variance (%^2)", "{!r} -> BA {} / TI {}".format(var_p, ba(var_p), ti(var_p)))
show("w1  sigma_p (answer, %)", "{!r} -> BA {} / TI {} -> {:.2f}%".format(sig_p, ba(sig_p), ti(sig_p), sig_p))
show("w1  shortcut sigma1 sigma2 / sqrt(sigma1^2 + sigma2^2) when rho = 0", "{:.4f}".format(s1 * s2 / math.sqrt(s1 ** 2 + s2 ** 2)))
half = math.sqrt(0.25 * s1 ** 2 + 0.25 * s2 ** 2)
show("w1  50/50 mix for comparison", "{:.4f}".format(half))
show("w1  weighted-average SD at 0.36/0.64 (right only if rho = +1)", "{:.4f}".format(w1 * s1 + w2 * s2))

# w2 (Day 29): 6 bonds, each 10% default probability, independent: P(exactly 1)
n, p = 6, 0.10
exact1 = math.comb(n, 1) * p * (1 - p) ** (n - 1)
no_comb = p * (1 - p) ** (n - 1)
at_least1 = 1 - (1 - p) ** n
show("w2  6C1", "{}".format(math.comb(6, 1)))
show("w2  0.9^5", "{:.6f}".format(0.9 ** 5))
show("w2  C (right): 6 x 0.1 x 0.9^5", "{!r} -> {:.2f}%".format(exact1, 100 * exact1))
show("w2  A: left out 6C1: 0.1 x 0.9^5", "{!r} -> {:.2f}%".format(no_comb, 100 * no_comb))
show("w2  B: P(at least one) = 1 - 0.9^6", "{!r} -> {:.2f}%".format(at_least1, 100 * at_least1))
show("w2  BA: 6 x .1 x .9 y^x 5 = ", "{} / TI {}".format(ba(exact1), ti(exact1)))
show("w2  P(no default) 0.9^6", "{:.4f}".format(0.9 ** 6))
show("w2  E(X) = np", "{:.1f}".format(n * p))

# ------------------------------------------------------------------ story
print("\n=== Story ===")
show("years from Merrill Lynch's TIGRs (1982) to STRIPS (1985)", 1985 - 1982)

# ------------------------------------------------------------------ the idea
print("\n=== The idea ===")
p2 = 100 / 1.05 ** 2
show("2-year strip paying 100 at a 5% spot rate: 100 / 1.05^2", "{:.4f} -> {:.2f}".format(p2, p2))
z_from = (100 / 90.70) ** 0.5 - 1
show("spot rate from a price of 90.70: (100/90.70)^(1/2) - 1", "{:.6f} -> {:.2f}%".format(z_from, 100 * z_from))
show("DF_2 = 90.70 / 100", "{:.4f}".format(90.70 / 100))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: Z_1 = 4%, 2-year par yield 5% ===")
df1w = 1 / 1.04
show("DF_1 = 1/1.04", "{:.6f}".format(df1w))
show("5 x DF_1", "{:.6f}".format(5 * df1w))
show("100 - 5 x DF_1", "{:.6f}".format(100 - 5 * df1w))
df2w = (100 - 5 * df1w) / 105
show("DF_2 = (100 - 5 x DF_1)/105", "{:.6f}".format(df2w))
show("1/DF_2", "{:.6f}".format(1 / df2w))
show("(1/DF_2)^(1/2)", "{:.6f}".format((1 / df2w) ** 0.5))
z2w = spot(df2w, 2)
show("Z_2 = (1/DF_2)^(1/2) - 1 (answer)", "{!r} -> {:.4f}%".format(z2w, 100 * z2w))
show("same via DF_2^(-1/2) - 1", "{:.6f}".format(df2w ** -0.5 - 1))
chk = 5 / 1.04 + 105 / (1 + z2w) ** 2
show("check: 5/1.04 + 105/(1 + Z_2)^2", "{:.4f}".format(chk))
show("check with Z_2 rounded to 5.0252%: 5/1.04 + 105/1.050252^2", "{:.4f}".format(5 / 1.04 + 105 / 1.050252 ** 2))
show("trap: par yield 5% used as the spot rate: 5/1.04 + 105/1.05^2", "{:.4f}".format(5 / 1.04 + 105 / 1.05 ** 2))
show("Z_2 above the par yield by (bp)", "{:.2f}".format((100 * z2w - 5) * 100))

# ------------------------------------------------------------------ walkthrough
print("\n=== Calculator walkthrough: par yields 2.0 / 2.6 / 3.0 ===")
par = [2.0, 2.6, 3.0]
df = bootstrap(par)
D1, D2, D3 = df
show("step 1  DF_1 = 1 / 1.02 (STO 1 / A)", "{!r} -> BA {} / TI {}".format(D1, ba(D1), ti(D1)))
show("        same as 100/102", "{:.6f}".format(100 / 102))
show("step 2  2.6 x DF_1", "{:.6f}".format(2.6 * D1))
show("step 2  DF_2 = (100 - 2.6 x DF_1) / 102.6 (STO 2 / B)", "{!r} -> BA {} / TI {}".format(D2, ba(D2), ti(D2)))
Z2 = spot(D2, 2)
show("step 3  1/DF_2", "{:.4f}".format(1 / D2))
show("step 3  (1/DF_2)^(1/2) - 1", "{!r} -> BA {} / TI {}".format(Z2, ba(Z2), ti(Z2)))
show("step 3  x 100: Z_2 (%)", "BA {} / TI {}".format(ba(100 * Z2), ti(100 * Z2)))
show("step 4  DF_1 + DF_2", "{:.6f}".format(D1 + D2))
show("step 4  DF_3 = (100 - 3 x (DF_1 + DF_2)) / 103 (STO 3 / C)", "{!r} -> BA {} / TI {}".format(D3, ba(D3), ti(D3)))
Z3 = spot(D3, 3)
show("step 5  1/DF_3", "{:.4f}".format(1 / D3))
show("step 5  (1/DF_3)^(1/3) - 1", "{!r} -> BA {} / TI {}".format(Z3, ba(Z3), ti(Z3)))
show("step 5  x 100: Z_3 (%)", "BA {} / TI {}".format(ba(100 * Z3), ti(100 * Z3)))
show("        Z_1 (%)", "{:.4f}".format(100 * spot(D1, 1)))
strips = [6 * D1, 6 * D2, 106 * D3]
price = sum(strips)
show("step 6  strip 1: 6 x DF_1", "{:.4f}".format(strips[0]))
show("step 6  strip 2: 6 x DF_2", "{:.4f}".format(strips[1]))
show("step 6  strip 3: 106 x DF_3", "{:.4f}".format(strips[2]))
show("step 6  sum of rounded strips", "{:.4f}".format(sum(round(s, 4) for s in strips)))
show("step 6  price = 6 x (DF_1 + DF_2) + 106 x DF_3 (STO 4 / D)", "{!r} -> BA {} / TI {}".format(price, ba(price), ti(price)))
show("step 6  same at the spot rates", "{:.4f}".format(pv_at([6, 6, 106], [spot(D1, 1), Z2, Z3])))
ytm = tvm_iy(3, -price, 6, 100)
show("step 7  TVM N=3 PV=-price PMT=6 FV=100: CPT I/Y (answer)", "{!r} -> BA I/Y = {} / TI I%={}".format(ytm, ba(ytm), ti(ytm)))
show("        check: price at the YTM", "{:.4f}".format(bond_pv(6, 100, ytm / 100, 3)))
show("        premium? coupon 6 > every spot rate", "{}".format(6 > 100 * Z3))
show("        par bonds reprice to 100 at the spots (1y, 2y, 3y)", "{:.4f}, {:.4f}, {:.4f}".format(
    pv_at([102.0], [spot(D1, 1)]), pv_at([2.6, 102.6], [spot(D1, 1), Z2]), pv_at([3, 3, 103], [spot(D1, 1), Z2, Z3])))
show("        YTM below Z_3 by (bp)", "{:.2f}".format((100 * Z3 - ytm) * 100))
show("        cents per dollar due in 1, 2, 3 years", "{:.2f}, {:.2f}, {:.2f}".format(100 * D1, 100 * D2, 100 * D3))
# share of the price in the last payment
show("        share of price in the final payment (%)", "{:.2f}".format(100 * strips[2] / price))

# ------------------------------------------------------------------ traps
print("\n=== Exam traps (walkthrough bond: 3-year 6%) ===")
one_z3 = bond_pv(6, 100, Z3, 3)
show("trap 1: every cash flow at Z_3 = 3.0183%", "{:.4f}".format(one_z3))
show("trap 1: every cash flow at the 3-year par yield 3%", "{:.4f}".format(bond_pv(6, 100, 0.03, 3)))
show("trap 1: difference from 108.5346 (at Z_3)", "{:.4f}".format(price - one_z3))
par_as_spot = pv_at([6, 6, 106], [0.02, 0.026, 0.03])
show("trap 2: par yields used as spot rates (2%, 2.6%, 3%)", "{:.4f}".format(par_as_spot))
show("trap 2: difference from 108.5346", "{:.4f}".format(price - par_as_spot))
show("trap 2: worked example Z_2 vs par (5.0252 vs 5)", "{:.4f}".format(100 * z2w))
# rounding too early: DFs rounded to two decimals
r1, r2 = round(D1, 2), round(D2, 2)
d3r = (100 - 3 * (r1 + r2)) / 103
z3r = spot(d3r, 3)
show("trap 3: DF_1, DF_2 rounded to 0.98, 0.95: DF_3", "{:.6f}".format(d3r))
show("trap 3: -> Z_3 (%)", "{:.4f}".format(100 * z3r))
show("trap 3: rounded DFs: r1, r2", "{}, {}".format(r1, r2))
# rounding Z_2 to 2.6% (i.e. treating it as the par yield) before Z_3
d2_round = 1 / 1.026 ** 2
d3_round = (100 - 3 * (D1 + d2_round)) / 103
show("trap 3: Z_2 rounded to 2.6% then DF_3 -> Z_3 (%)", "{:.4f}".format(100 * spot(d3_round, 3)))
d2_r1 = 1 / 1.0261 ** 2
show("trap 3: Z_2 rounded to 2.61% then -> Z_3 (%)", "{:.4f}".format(100 * spot((100 - 3 * (D1 + d2_r1)) / 103, 3)))
# rounding the displayed DF_3 (0.9147) and retyping it before the root
show("trap 3: retyped display 0.9147 -> Z_3 (%)", "{:.4f}".format(100 * spot(round(D3, 4), 3)))
show("trap 3: retyped displays 0.9804, 0.9498 -> DF_3", "{:.6f}".format((100 - 3 * (round(D1, 4) + round(D2, 4))) / 103))
show("trap 3: DFs rounded to 2 decimals -> price 6 x (0.98 + 0.95) + 106 x 0.91",
     "{:.4f}".format(6 * (0.98 + 0.95) + 106 * 0.91))
show("trap 3: c1 with DF_3 retyped as 0.9309 -> Z_3 (%)", "{:.4f}".format(100 * spot(0.9309, 3)))
show("trap 4: forgot the root: 1/DF_3 - 1 (%)", "{:.4f}".format(100 * (1 / D3 - 1)))
show("trap 4: forgot the root, divided by 3 (%)", "{:.4f}".format(100 * (1 / D3 - 1) / 3))

# ------------------------------------------------------------------ check
print("\n=== Check questions ===")
# c1: par yields 1.5, 2.0, 2.4
c1df = bootstrap([1.5, 2.0, 2.4])
a1, a2, a3 = c1df
show("c1  DF_1 = 1/1.015", "{!r} -> BA {} / TI {}".format(a1, ba(a1), ti(a1)))
show("c1  DF_2 = (100 - 2 x DF_1)/102", "{!r} -> BA {} / TI {}".format(a2, ba(a2), ti(a2)))
c1z2 = spot(a2, 2)
show("c1  Z_2 (%)", "{!r} -> BA {} / TI {}".format(100 * c1z2, ba(100 * c1z2), ti(100 * c1z2)))
show("c1  Z_2 before x 100", "BA {} / TI {}".format(ba(c1z2), ti(c1z2)))
show("c1  DF_1 + DF_2", "{:.6f}".format(a1 + a2))
show("c1  DF_3 = (100 - 2.4 x (DF_1 + DF_2))/102.4", "{!r} -> BA {} / TI {} -> {:.6f}".format(a3, ba(a3), ti(a3), a3))
c1z3 = spot(a3, 3)
show("c1  Z_3 before x 100", "BA {} / TI {}".format(ba(c1z3), ti(c1z3)))
show("c1  Z_3 (answer, %)", "{!r} -> BA {} / TI {} -> {:.3f}%".format(100 * c1z3, ba(100 * c1z3), ti(100 * c1z3), 100 * c1z3))
show("c1  trap: par yield 2.4 taken as Z_3", "2.400 (off by {:.3f})".format(100 * c1z3 - 2.4))
show("c1  check: 3-year par bond at the spots", "{:.4f}".format(pv_at([2.4, 2.4, 102.4], [spot(a1, 1), c1z2, c1z3])))

# c2: concept (right = B); numbers to back the explanation
show("c2  worked example: 5 x DF_1 at 4% vs at 5%", "{:.4f} vs {:.4f}".format(5 / 1.04, 5 / 1.05))
show("c2  so 105 x DF_2 must be (100 - 4.8077) vs (100 - 4.7619)", "{:.4f} vs {:.4f}".format(100 - 5 / 1.04, 100 - 5 / 1.05))

# c3: Z_1 = 2%; 2-year 4% bond at 101.50
c3d1 = 1 / 1.02
c3d2 = (101.50 - 4 * c3d1) / 104
c3z2 = spot(c3d2, 2)
show("c3  4 x DF_1 = 4/1.02", "{:.6f} -> BA {}".format(4 * c3d1, ba(4 * c3d1)))
show("c3  101.50 - 4/1.02", "{:.6f}".format(101.50 - 4 * c3d1))
show("c3  DF_2 = (101.50 - 4/1.02)/104", "{!r} -> BA {} / TI {} -> {:.6f}".format(c3d2, ba(c3d2), ti(c3d2), c3d2))
show("c3  Z_2 before x 100", "BA {} / TI {}".format(ba(c3z2), ti(c3z2)))
show("c3  Z_2 (answer, %)", "{!r} -> BA {} / TI {} -> {:.3f}%".format(100 * c3z2, ba(100 * c3z2), ti(100 * c3z2), 100 * c3z2))
show("c3  check: 4/1.02 + 104/(1 + Z_2)^2", "{:.4f}".format(4 / 1.02 + 104 / (1 + c3z2) ** 2))
show("c3  trap: price treated as 100 (par): DF_2 -> Z_2 (%)", "{:.4f}".format(100 * spot((100 - 4 * c3d1) / 104, 2)))
show("c3  bond's own YTM (N=2, PV=-101.5, PMT=4, FV=100)", "{:.4f}".format(tvm_iy(2, -101.5, 4, 100)))
show("c3  with DF_2 retyped as 0.9383 -> Z_2 (%)", "{:.4f}".format(100 * spot(0.9383, 2)))
show("c3  1/DF_2", "{:.4f}".format(1 / c3d2))

# c4: spots 2, 3, 4; 3-year 5% bond
c4 = pv_at([5, 5, 105], [0.02, 0.03, 0.04])
c4b = bond_pv(5, 100, 0.04, 3)
c4c = bond_pv(5, 100, 0.03, 3)
show("c4  pieces 5/1.02, 5/1.03^2, 105/1.04^3", "{:.4f}, {:.4f}, {:.4f}".format(5 / 1.02, 5 / 1.03 ** 2, 105 / 1.04 ** 3))
show("c4  A (right): each at its own spot rate", "{!r} -> {:.2f}".format(c4, c4))
show("c4  B: every cash flow at the 3-year spot 4%", "{!r} -> {:.2f}".format(c4b, c4b))
show("c4  C: every cash flow at the average spot 3%", "{!r} -> {:.2f}".format(c4c, c4c))
show("c4  average spot (2 + 3 + 4)/3", "{:.2f}".format((2 + 3 + 4) / 3))
show("c4  YTM of the bond at price A", "{:.4f}".format(tvm_iy(3, -c4, 5, 100)))
