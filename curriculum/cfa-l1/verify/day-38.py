#!/usr/bin/env python3
"""Day 38 of CFA Math: every number that appears on day-38.html.

Yield spreads and matrix pricing
(CFA Program 2027 Level I, Fixed Income, Yield and Yield Spread Measures for
Fixed-Rate Bonds; matrix pricing from Fixed-Income Bond Valuation: Prices and
Yields; the FRN margins from Yield and Yield Spread Measures for Floating-Rate
Instruments).
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
    print("  {:<70} {}".format(label, value))


def bond_pv(pmt, fv, r, n):
    """Price: n coupons of pmt, then fv with the last one; r per period.
    Summed term by term (the definition), not from the annuity formula."""
    return sum(pmt / (1 + r) ** t for t in range(1, n + 1)) + fv / (1 + r) ** n


def tvm_pv(n, iy, pmt, fv):
    """What CPT PV shows (END mode, P/Y = C/Y = 1): the negative of the price."""
    return -bond_pv(pmt, fv, iy / 100, n)


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


def interp(t, t_short, y_short, t_long, y_long):
    """Linear interpolation of a yield (percent in, percent out)."""
    return y_short + (t - t_short) / (t_long - t_short) * (y_long - y_short)


def chn(*tokens):
    """BA II Plus in Chn mode: every operator applied left to right as typed,
    except that a parenthesised group is worked out first."""
    acc = None
    op = None
    for tok in tokens:
        if isinstance(tok, str):
            op = tok
            continue
        if acc is None:
            acc = tok
        elif op == "+":
            acc += tok
        elif op == "-":
            acc -= tok
        elif op == "*":
            acc *= tok
        elif op == "/":
            acc /= tok
    return acc


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 37): Z_1 = 3.2%, Z_2 = 4.0%: the 1y1y forward rate
w1 = (1.04 ** 2 / 1.032 - 1) * 100
show("w1  1.04^2", "{:.6f}".format(1.04 ** 2))
show("w1  1.04^2 / 1.032", "{:.6f}".format(1.04 ** 2 / 1.032))
show("w1  1y1y = 1.04^2 / 1.032 - 1, as % (answer)", "{!r} -> BA {} / TI {} -> {:.2f}%".format(w1, ba(w1), ti(w1), w1))
show("w1  BA after = (before x 100)", "{} / TI {}".format(ba(w1 / 100), ti(w1 / 100)))
show("w1  trap: arithmetic shortcut 2 x 4.0 - 3.2", "{:.2f}".format(2 * 4.0 - 3.2))
show("w1  trap: simple average of the spots (3.2 + 4.0)/2", "{:.2f}".format((3.2 + 4.0) / 2))
show("w1  check: 1.032 x (1 + 1y1y) = 1.04^2", "{:.6f} vs {:.6f}".format(1.032 * (1 + w1 / 100), 1.04 ** 2))
show("w1  forward above both spots (upward-sloping curve)", "{:.4f} > 4.0 > 3.2: {}".format(w1, w1 > 4.0 > 3.2))

# w2 (Day 31): lognormal price can never be negative
for r in (-1.5, -3.0):
    show("w2  100 x e^({})".format(r), "{:.4f} -> {:.2f}".format(100 * math.exp(r), 100 * math.exp(r)))
show("w2  e^(-1.5) (BA: 1.5 +|- 2ND e^x)", "{} / TI {}".format(ba(math.exp(-1.5)), ti(math.exp(-1.5))))
show("w2  simple return of -150% would leave 100 x (1 - 1.5)", "{:.2f}".format(100 * (1 - 1.5)))

# ------------------------------------------------------------------ story
print("\n=== Story: LQD, March 2020 ===")
show("5.0% discount: cents per dollar of NAV", "{:.0f}".format(100 * (1 - 0.050)))
show("next day's discount (%)", "0.19")
show("2018-19: never more than (%) from NAV", "1")
show("19 March: discount back near (%)", "5")
show("25 March: premium of about (%), after the Fed's 23 March announcement", "5")
show("days from 12 March to 25 March", 25 - 12)

# ------------------------------------------------------------------ why it works
print("\n=== Why it works ===")
slope = (4.30 - 3.90) / (10 - 5)
show("5y 3.90% -> 10y 4.30%: climb (points)", "{:.2f}".format(4.30 - 3.90))
show("  per year: 0.40 / 5", "{:.2f}".format(slope))
show("  7 years is 2 years along: 3.90 + 2 x 0.08", "{:.2f}".format(3.90 + 2 * slope))
show("  formula: 3.90 + (7-5)/(10-5) x (4.30-3.90)", "{:.4f}".format(interp(7, 5, 3.90, 10, 4.30)))
# Z-spread mini-example: 2-year 5% annual-coupon bond, spots 3% and 4%, Z = 1%
zt1 = 5 / 1.04
zt2 = 105 / 1.05 ** 2
show("Z mini: 5 / (1 + 0.03 + 0.01)", "{:.4f}".format(zt1))
show("Z mini: 105 / (1 + 0.04 + 0.01)^2  (1.05^2 = {:.4f})".format(1.05 ** 2), "{:.4f}".format(zt2))
show("Z mini: price", "{!r} -> {:.4f} -> {:.2f}".format(zt1 + zt2, zt1 + zt2, zt1 + zt2))
# the same bond's YTM: a single rate that gives the same price
z_ytm = tvm_iy(2, -(zt1 + zt2), 5, 100)
show("Z mini: YTM of that bond at that price", "{:.4f}".format(z_ytm))
# discounting at the spots alone (no spread)
show("Z mini: at the spot rates alone 5/1.03 + 105/1.04^2", "{:.4f}".format(5 / 1.03 + 105 / 1.04 ** 2))
# FRN at par when DM = QM: MRR 4%, QM = DM = 0.50%, quarterly, 2 years
frn_par = -tvm_pv(8, (4 + 0.5) / 4, (4 + 0.5) / 4, 100)
show("FRN DM = QM: MRR 4%, margins 50 bp, quarterly, 2 years", "{:.4f}".format(frn_par))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: 7-year corporate at 5.35% ===")
frac = (7 - 5) / (10 - 5)
gap = 4.30 - 3.90
g7 = interp(7, 5, 3.90, 10, 4.30)
show("(7 - 5)/(10 - 5)", "{:.1f}".format(frac))
show("4.30 - 3.90", "{:.2f}".format(gap))
show("0.4 x 0.40", "{:.2f}".format(frac * gap))
show("interpolated 7-year government yield", "{:.2f}%".format(g7))
G = (5.35 - g7) * 100
I = (5.35 - 4.25) * 100
show("G-spread 5.35 - 4.06 (bp)", "{:.4f} -> {:.0f} bp".format(G, G))
show("I-spread 5.35 - 4.25 (bp)", "{:.4f} -> {:.0f} bp".format(I, I))
show("swap rate minus interpolated government (bp)", "{:.0f}".format((4.25 - g7) * 100))
show("trap: unmatched 5-year benchmark 5.35 - 3.90 (bp)", "{:.0f}".format((5.35 - 3.90) * 100))
show("trap: unmatched 10-year benchmark 5.35 - 4.30 (bp)", "{:.0f}".format((5.35 - 4.30) * 100))

# ------------------------------------------------------------------ walkthrough
print("\n=== Calculator walkthrough: illiquid 4-year 5% bond ===")
y4 = interp(4, 3, 4.10, 5, 4.70)
show("interpolated 4-year yield 4.10 + (4-3)/(5-3) x (4.70-4.10)", "{!r} -> BA {} / TI {}".format(y4, ba(y4), ti(y4)))
show("  pieces: (4-3)/(5-3), 4.70-4.10, product", "{:.1f}, {:.2f}, {:.2f}".format(1 / 2, 0.60, 0.5 * 0.60))
chn_wrong = chn(4.1, "+", (4 - 3), "/", (5 - 3), "*", (4.7 - 4.1))
show("  same keys in Chn mode (left to right): ((4.1+1)/2) x 0.6", "{!r} -> BA {}".format(chn_wrong, ba(chn_wrong)))
pv = tvm_pv(4, y4, 5, 100)
show("TVM N=4 I/Y=4.4 PMT=5 FV=100: CPT PV", "{!r} -> BA PV = {} / TI PV={}".format(pv, ba(pv), ti(pv)))
show("  price to the cent", "{:.2f}".format(-pv))
show("  term by term at 4.4%", "{:.4f}".format(bond_pv(5, 100, 0.044, 4)))
for t in range(1, 5):
    cf = 5 + (100 if t == 4 else 0)
    show("    CF {} = {} / 1.044^{}".format(t, cf, t), "{:.4f}".format(cf / 1.044 ** t))
gs = (y4 - 3.2) * 100
show("G-spread (4.4 - 3.2) x 100 (bp)", "{!r} -> BA {} / TI {}".format(gs, ba(gs), ti(gs)))
show("  4.4 - 3.2 (points)", "{:.4f}".format(y4 - 3.2))
show("  AOS trap: 4.4 - 3.2 x 100 without brackets", "{}".format(ba(4.4 - 3.2 * 100)))
# premium check
show("premium? coupon 5 > yield 4.40", "{}".format(5 > y4))
# un-interpolated comparables (range)
show("priced at the 3-year comparable 4.10% (no interpolation)", "{:.4f}".format(-tvm_pv(4, 4.10, 5, 100)))
show("priced at the 5-year comparable 4.70% (no interpolation)", "{:.4f}".format(-tvm_pv(4, 4.70, 5, 100)))
show("priced at the government yield 3.20% (no spread)", "{:.4f}".format(-tvm_pv(4, 3.20, 5, 100)))
# step 6 / story check: 10-year 4% bond at par, then 5% lower at 95
show("story: 10-year 4% bond at 4% (par check)", "{:.4f}".format(-tvm_pv(10, 4, 4, 100)))
iy95 = tvm_iy(10, -95, 4, 100)
show("story: N=10 PV=-95 PMT=4 FV=100: CPT I/Y", "{!r} -> BA I/Y = {} / TI I%={}".format(iy95, ba(iy95), ti(iy95)))
show("  yield rise over 4.00% (points, bp)", "{:.4f} -> {:.2f} bp -> about {:.0f} bp".format(iy95 - 4, (iy95 - 4) * 100, (iy95 - 4) * 100))
show("  check: price at 4.6361%", "{:.4f}".format(bond_pv(4, 100, iy95 / 100, 10)))
show("  0.19% gap the next day: price 100 x (1 - 0.0019)", "{:.2f}".format(100 * (1 - 0.0019)))
iy9981 = tvm_iy(10, -100 * (1 - 0.0019), 4, 100)
show("  0.19% gap as a yield: N=10 PV=-99.81 PMT=4 FV=100", "{:.4f} -> {:.1f} bp".format(iy9981, (iy9981 - 4) * 100))

# ------------------------------------------------------------------ traps
print("\n=== Exam traps ===")
show("unmatched benchmark (worked example): 5-year used", "{:.0f} bp instead of {:.0f} bp".format((5.35 - 3.90) * 100, G))
show("bp: 1.20 points", "{:.0f} bp".format(1.20 * 100))
show("bp: 1 bp as a percent", "{:.2f}%".format(1 / 100))
show("FRN at the quoted margin on a reset date (QM=60bp, MRR 3.6%, any N)", "{:.4f}".format(-tvm_pv(12, (3.6 + 0.6) / 4, (3.6 + 0.6) / 4, 100)))

# ------------------------------------------------------------------ check
print("\n=== Check questions ===")
# c1: comparables 3.80% (5y), 4.40% (8y); 6-year 4.5% annual coupon
c1y = interp(6, 5, 3.80, 8, 4.40)
show("c1  interpolated 6-year yield 3.80 + (6-5)/(8-5) x (4.40-3.80)", "{!r} -> BA {} / TI {}".format(c1y, ba(c1y), ti(c1y)))
show("c1  (6-5)/(8-5), 4.40-3.80, product", "{:.4f}, {:.2f}, {:.2f}".format(1 / 3, 0.60, 0.60 / 3))
c1 = -tvm_pv(6, c1y, 4.5, 100)
show("c1  N=6 I/Y=4 PMT=4.5 FV=100: CPT PV (answer)", "{!r} -> BA PV = {} / TI PV={} -> {:.2f}".format(c1, ba(-c1), ti(-c1), c1))
show("c1  using 3.80% un-interpolated", "{:.2f}".format(-tvm_pv(6, 3.80, 4.5, 100)))
show("c1  using 4.40% un-interpolated", "{:.2f}".format(-tvm_pv(6, 4.40, 4.5, 100)))
show("c1  using the simple average 4.10%", "{:.2f}".format(-tvm_pv(6, 4.10, 4.5, 100)))
show("c1  Chn mode trap on the interpolation", "{}".format(ba(chn(3.8, "+", 1, "/", 3, "*", 0.6))))

# c2: Z = 180, OAS = 130
show("c2  Z-spread - OAS = option value (bp)", "{}".format(180 - 130))

# c3: 3-year quarterly FRN, MRR 3.60%, QM 60 bp, DM 90 bp
c3pmt = (3.60 + 0.60) / 4
c3r = (3.60 + 0.90) / 4
show("c3  PMT = (3.60 + 0.60)/4", "{} / TI {}".format(ba(c3pmt), ti(c3pmt)))
show("c3  I/Y = (3.60 + 0.90)/4", "{} / TI {}".format(ba(c3r), ti(c3r)))
show("c3  N = 3 x 4", 3 * 4)
c3 = -tvm_pv(12, c3r, c3pmt, 100)
show("c3  N=12 I/Y=1.125 PMT=1.05 FV=100: CPT PV (answer)", "{!r} -> BA PV = {} / TI PV={} -> {:.2f}".format(c3, ba(-c3), ti(-c3), c3))
show("c3  trap: discounted at the quoted margin (I/Y = 1.05)", "{:.2f}".format(-tvm_pv(12, c3pmt, c3pmt, 100)))
show("c3  trap: annual inputs N=3 I/Y=4.5 PMT=4.2", "{:.2f}".format(-tvm_pv(3, 4.5, 4.2, 100)))
show("c3  trap: margins swapped (PMT=1.125, I/Y=1.05)", "{:.2f}".format(-tvm_pv(12, c3pmt, c3r, 100)))
show("c3  discount below par (points)", "{:.2f}".format(100 - c3))
show("c3  30 bp a year short = 0.075 per quarter x 12 quarters (undiscounted)", "{:.2f}".format((0.90 - 0.60) / 4 * 12))

# c4: 6-year corporate 5.10%; government 3.60% (5y), 4.10% (10y)
c4g = interp(6, 5, 3.60, 10, 4.10)
show("c4  interpolated 6-year government", "{:.2f}%".format(c4g))
show("c4  C: G-spread 5.10 - 3.70 (right, bp)", "{:.0f}".format((5.10 - c4g) * 100))
show("c4  A: unmatched 5-year 5.10 - 3.60 (bp)", "{:.0f}".format((5.10 - 3.60) * 100))
show("c4  B: simple average (3.60 + 4.10)/2 = {:.2f}, 5.10 - that (bp)".format((3.60 + 4.10) / 2), "{:.0f}".format((5.10 - (3.60 + 4.10) / 2) * 100))
show("c4  pieces: (6-5)/(10-5), 4.10-3.60, product", "{:.1f}, {:.2f}, {:.2f}".format(1 / 5, 0.50, 0.50 / 5))
