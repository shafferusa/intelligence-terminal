#!/usr/bin/env python3
"""Day 42 of CFA Math: every number that appears on day-42.html.

Week 6 review (Days 36-41): spot rates and bootstrapping, implied forward and
par rates, yield spreads (G-spread, OAS) and interpolation, Macaulay duration
and the duration gap, approximate duration and convexity, portfolio duration
(CFA Program 2027 Level I, Fixed Income: The Term Structure of Interest Rates:
Spot, Par, and Forward Curves; Yield and Yield Spread Measures for Fixed-Rate
Bonds; Yield-Based Bond Duration Measures and Properties; Yield-Based Bond
Convexity and Portfolio Properties). Warm-up from Day 41 (convexity) and
Day 15 (pricing a semiannual bond).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime


def digits_fit(x, dec=4, width=10):
    """Decimals a 10-digit display can keep for x when set to `dec` decimals."""
    int_digits = len(str(int(abs(round(x, dec)))))
    return max(0, min(dec, width - int_digits))


def ba(x):
    """BA II Plus display: 4 decimals (fewer past 10 digits), thousands separators."""
    return "{:,.{}f}".format(x, digits_fit(x))


def ti(x):
    """TI-84 Plus display: FLOAT 4, no separators, no leading zero below 1."""
    s = "{:.{}f}".format(x, digits_fit(x))
    if s.startswith("0."):
        s = s[1:]
    if s.startswith("-0."):
        s = "-" + s[2:]
    return s


def both(x):
    return "BA {} / TI {}".format(ba(x), ti(x))


def show(label, value):
    print("  {:<72} {}".format(label, value))


def price(coupon, n, y_pct, face=100.0):
    """Price per 100 of a bond paying `coupon` per period for n periods, periodic yield y_pct %."""
    y = y_pct / 100.0
    return sum(coupon / (1 + y) ** t for t in range(1, n + 1)) + face / (1 + y) ** n


def approx(pm, p0, pp, dy):
    """ApproxModDur and ApproxCon from three prices; dy is the yield step as a decimal."""
    return (pm - pp) / (2 * dy * p0), (pm + pp - 2 * p0) / (dy ** 2 * p0)


# =================================================================== warm-up
print("=== w1 (Day 41): AnnModDur 11.2, AnnConvexity 160, yields FALL 75 bp ===")
md1, cv1, dy1 = 11.2, 160.0, -0.0075
w1_dur = -md1 * dy1
w1_con = 0.5 * cv1 * dy1 ** 2
w1 = (w1_dur + w1_con) * 100
show("duration part  -11.2 x (-0.0075)", "{:.4f} ({:.2f}%)".format(w1_dur, w1_dur * 100))
show("(-0.0075)^2", "{:.8f}".format(dy1 ** 2))
show("convexity part 0.5 x 160 x 0.0075^2", "{:.4f} ({:.2f}%)".format(w1_con, w1_con * 100))
show("w1 answer, %", "{!r} -> {:.2f}%".format(w1, w1))
show("w1 decimal display (BA / TI)", both(w1 / 100))
show("w1 trap: no 1/2", "{:.2f}%".format((w1_dur + 2 * w1_con) * 100))
show("w1 trap: convexity subtracted", "{:.2f}%".format((w1_dur - w1_con) * 100))
show("w1 trap: duration only", "{:.2f}%".format(w1_dur * 100))
show("w1 trap: minus sign kept on duration (yield fall treated as a rise)", "{:.2f}%".format((-w1_dur + w1_con) * 100))
show("w1 trap: dYield in percent: 0.5 x 160 x 0.75^2", "{:.2f}".format(0.5 * cv1 * 0.75 ** 2))

print("\n=== w2 (Day 15): 15-year 5.25% coupon, paid semiannually, yield 4.6% ===")
w2_right = price(2.625, 30, 2.3)
w2_annual = price(5.25, 15, 4.6)
w2_pmt_not_halved = price(5.25, 30, 2.3)
w2_yield_not_halved = price(2.625, 30, 4.6)
w2_n_not_doubled = price(2.625, 15, 2.3)
show("right: N=30, I/Y=2.3, PMT=2.625, FV=100", "{!r} -> PV = {} / PV={}".format(w2_right, ba(-w2_right), ti(-w2_right)))
show("annual inputs: N=15, I/Y=4.6, PMT=5.25", "{:.4f} -> {:.2f}".format(w2_annual, w2_annual))
show("PMT not halved: N=30, I/Y=2.3, PMT=5.25", "{:.4f} -> {:.2f}".format(w2_pmt_not_halved, w2_pmt_not_halved))
show("yield not halved: N=30, I/Y=4.6, PMT=2.625", "{:.4f} -> {:.2f}".format(w2_yield_not_halved, w2_yield_not_halved))
show("N not doubled: N=15, I/Y=2.3, PMT=2.625", "{:.4f} -> {:.2f}".format(w2_n_not_doubled, w2_n_not_doubled))

# =================================================================== worked example (Day 36)
print("\n=== Worked example: par yields 2.5% (1y), 3.2% (2y); price a 2-year 5% bond ===")
we_df1 = 1 / 1.025
we_df2 = (100 - 3.2 * we_df1) / 103.2
we_z2 = we_df2 ** -0.5 - 1
we_price = 5 * we_df1 + 105 * we_df2
show("DF1 = 1/1.025", "{!r} -> {:.6f}".format(we_df1, we_df1))
show("3.2 x DF1", "{:.6f}".format(3.2 * we_df1))
show("100 - 3.2 x DF1", "{:.6f}".format(100 - 3.2 * we_df1))
show("DF2 = (100 - 3.2 DF1)/103.2", "{!r} -> {:.6f}".format(we_df2, we_df2))
show("Z2 = DF2^(-1/2) - 1 (%)", "{!r} -> {:.4f}%".format(we_z2 * 100, we_z2 * 100))
show("1/DF2 = (1+Z2)^2", "{:.6f}".format(1 / we_df2))
show("price = 5 DF1 + 105 DF2", "{!r} -> {:.4f}".format(we_price, we_price))
show("  5 x DF1 / 105 x DF2", "{:.4f} / {:.4f}".format(5 * we_df1, 105 * we_df2))
show("  from 6-decimal DFs", "{:.6f}".format(5 * round(we_df1, 6) + 105 * round(we_df2, 6)))
we_ytm_px = price(5, 2, 3.2)
show("same bond discounted at the 3.2% par yield (both flows)", "{:.4f}".format(we_ytm_px))
show("check: par bond at spots 3.2/1.025 + 103.2/(1+Z2)^2", "{:.4f}".format(3.2 / 1.025 + 103.2 / (1 + we_z2) ** 2))
# YTM of the 5% bond at that price (bisection)
lo, hi = 0.0, 0.2
for _ in range(200):
    mid = (lo + hi) / 2
    if price(5, 2, mid * 100) > we_price:
        lo = mid
    else:
        hi = mid
show("YTM of the 5% bond at 103.4458 (%)", "{:.4f}".format(lo * 100))

# =================================================================== walkthrough (Days 40-41)
print("\n=== Walkthrough: 15-year 2% annual bond at 4%, +/-10 bp, yields FALL 100 bp ===")
N, CPN = 15, 2.0
p0 = price(CPN, N, 4.0)
pm = price(CPN, N, 3.9)
pp = price(CPN, N, 4.1)
p3 = price(CPN, N, 3.0)
p5 = price(CPN, N, 5.0)
D, C = approx(pm, p0, pp, 0.001)
show("PV0 at 4.0% (STO 1 / A)", "{!r} -> BA PV = {} / TI PV={} ; stored {} / {}".format(p0, ba(-p0), ti(-p0), ba(p0), ti(p0)))
show("PV- at 3.9% (STO 2 / B)", "{!r} -> BA PV = {} ; stored {} / {}".format(pm, ba(-pm), ba(pm), ti(pm)))
show("PV+ at 4.1% (STO 3 / C)", "{!r} -> BA PV = {} ; stored {} / {}".format(pp, ba(-pp), ba(pp), ti(pp)))
show("gain for -10 bp (PV- - PV0) / loss for +10 bp (PV0 - PV+)", "{:.4f} / {:.4f}".format(pm - p0, p0 - pp))
show("PV- - PV+", "{:.4f}".format(pm - pp))
show("PV- + PV+ - 2 PV0", "{:.6f}".format(pm + pp - 2 * p0))
show("ApproxModDur (STO 4 / D)", "{!r} -> {}".format(D, both(D)))
show("ApproxCon (STO 5 / E)", "{!r} -> {}".format(C, both(C)))
Dr, Cr = approx(round(pm, 4), round(p0, 4), round(pp, 4), 0.001)
show("from 4-decimal prices: ApproxModDur / ApproxCon", "{:.4f} / {:.4f}".format(Dr, Cr))
dur_part = -D * (-0.01)
con_part = 0.5 * C * 0.01 ** 2
est = dur_part + con_part
show("duration part: -ApproxModDur x (-0.01)", "{!r} -> {}".format(dur_part, both(dur_part)))
show("convexity part: 0.5 x ApproxCon x 0.01^2", "{!r} -> {}".format(con_part, both(con_part)))
show("estimate (decimal)", "{!r} -> {}".format(est, both(est)))
show("estimate x 100 (walkthrough answer, %)", "{!r} -> {}".format(est * 100, both(est * 100)))
show("PV at 3.0%", "{!r} -> BA PV = {} / TI {}".format(p3, ba(-p3), ti(p3)))
ex_dn = p3 / p0 - 1
show("exact: PV(3%)/PV0 - 1", "{!r} -> {}".format(ex_dn, both(ex_dn)))
show("exact x 100 (%)", "{!r} -> {}".format(ex_dn * 100, both(ex_dn * 100)))
show("duration-only miss vs exact (pp)", "{:.4f}".format(ex_dn * 100 - dur_part * 100))
show("duration+convexity miss vs exact (pp)", "{:.4f}".format(ex_dn * 100 - est * 100))
ex_up = p5 / p0 - 1
show("reverse (+100 bp): PV at 5% / exact % / dur only / dur + con",
     "{:.4f} / {:.4f} / {:.4f} / {:.4f}".format(p5, ex_up * 100, -dur_part * 100, (-dur_part + con_part) * 100))
show("ApproxMacDur = ApproxModDur x 1.04", "{:.4f}".format(D * 1.04))
# traps on the walkthrough numbers
show("trap: forgot the 2 in 2 x dYield", "{:.4f}".format((pm - pp) / (0.001 * p0)))
show("trap: dYield 0.1 (percent) instead of 0.001", "{:.4f}".format((pm - pp) / (2 * 0.1 * p0)))
show("trap: no 1/2 on convexity (estimate %)", "{:.4f}".format((dur_part + 2 * con_part) * 100))
show("trap: convexity subtracted because yields fell (estimate %)", "{:.4f}".format((dur_part - con_part) * 100))
show("trap: dYield 1 (percent) in the convexity term: 0.5 x C x 1^2", "{:.4f}".format(0.5 * C * 1 ** 2))

# =================================================================== traps block: 2y1y vs 1y2y (Day 37's curve)
print("\n=== Trap example: Day 37's curve Z1 2.5%, Z2 3.0%, Z3 3.4% ===")
f_2y1y_37 = 1.034 ** 3 / 1.03 ** 2 - 1
f_1y2y_37 = (1.034 ** 3 / 1.025) ** 0.5 - 1
show("2y1y = 1.034^3/1.03^2 - 1 (%)", "{:.4f}".format(f_2y1y_37 * 100))
show("1y2y = (1.034^3/1.025)^(1/2) - 1 (%)", "{:.4f}".format(f_1y2y_37 * 100))

# =================================================================== check questions
print("\n=== c1 (Day 36): par yields 3.0% (1y), 3.5% (2y): bootstrap Z2 ===")
c1_df1 = 1 / 1.03
c1_num = 100 - 3.5 * c1_df1
c1_df2 = c1_num / 103.5
c1_root = c1_df2 ** -0.5
c1 = (c1_root - 1) * 100
show("DF1 = 1/1.03 (STO 1 / A)", "{!r} -> {}".format(c1_df1, both(c1_df1)))
show("100 - 3.5 x DF1", "{!r} -> {}".format(c1_num, both(c1_num)))
show("DF2 = (100 - 3.5 DF1)/103.5 (STO 2 / B)", "{!r} -> {}".format(c1_df2, both(c1_df2)))
show("DF2^(-0.5)", "{!r} -> {}".format(c1_root, both(c1_root)))
show("DF2^(-0.5) - 1", both(c1_root - 1))
show("c1 answer Z2 (%)", "{!r} -> {} -> {:.3f}".format(c1, both(c1), c1))
show("c1 trap: Z2 = par yield 3.5 (miss)", "{:.4f}".format(c1 - 3.5))
show("c1 trap: forgot the square root: 1/DF2 - 1 (%)", "{:.4f}".format((1 / c1_df2 - 1) * 100))
show("1 + Z2 (6 dp)", "{:.6f}".format(c1_root))
show("check: 3.5/1.03 + 103.5/(1+Z2)^2","{:.4f}".format(3.5 / 1.03 + 103.5 / (1 + c1 / 100) ** 2))

print("\n=== c2 (Day 36): 3-year 4% bond, spots 3.0%, 3.6%, 4.1% ===")
Z = [0.030, 0.036, 0.041]
cf2 = [4, 4, 104]
pvs = [cf / (1 + z) ** t for t, (cf, z) in enumerate(zip(cf2, Z), start=1)]
c2 = sum(pvs)
show("PV of 4 at 3.0% / 4 at 3.6%^2 / 104 at 4.1%^3", "{:.4f} / {:.4f} / {:.4f}".format(*pvs))
show("c2 answer price", "{!r} -> {}".format(c2, both(c2)))
c2_all41 = price(4, 3, 4.1)
show("trap: everything at the 3-year spot 4.1%", "{:.4f}".format(c2_all41))
c2_all_avg = price(4, 3, sum(z * 100 for z in Z) / 3)
show("trap: everything at the average spot {:.4f}%".format(sum(z * 100 for z in Z) / 3), "{:.4f}".format(c2_all_avg))
# the bond's YTM at that price (bisection)
lo, hi = 0.0, 0.2
for _ in range(200):
    mid = (lo + hi) / 2
    if price(4, 3, mid * 100) > c2:
        lo = mid
    else:
        hi = mid
show("YTM of the bond at the spot price (%)", "{:.4f}".format(lo * 100))

print("\n=== c3 (Day 37): same curve: 2y1y ===")
c3_2y1y = 1.041 ** 3 / 1.036 ** 2 - 1
c3_1y2y = (1.041 ** 3 / 1.03) ** 0.5 - 1
c3_avg = (3.6 + 4.1) / 2
show("1.041^3", "{:.6f} -> {}".format(1.041 ** 3, both(1.041 ** 3)))
show("1.036^2", "{:.6f} -> {}".format(1.036 ** 2, both(1.036 ** 2)))
show("1.041^3/1.036^2", "{:.6f}".format(1.041 ** 3 / 1.036 ** 2))
show("A (right) 2y1y = 1.041^3/1.036^2 - 1 (%)", "{!r} -> {:.4f}".format(c3_2y1y * 100, c3_2y1y * 100))
show("2y1y decimal display", both(c3_2y1y))
show("B? 1y2y = (1.041^3/1.03)^(1/2) - 1 (%)", "{!r} -> {:.4f}".format(c3_1y2y * 100, c3_1y2y * 100))
show("  1.041^3/1.03 (two-year growth from year 1 to 3)", "{:.6f}".format(1.041 ** 3 / 1.03))
show("C? average of the 2- and 3-year spots (%)", "{:.4f}".format(c3_avg))
show("1y1y on this curve (%)", "{:.4f}".format((1.036 ** 2 / 1.03 - 1) * 100))
show("check: 1.03 x 1.1y1y x 1.2y1y = 1.041^3",
     "{:.6f} vs {:.6f}".format(1.03 * (1.036 ** 2 / 1.03) * (1 + c3_2y1y), 1.041 ** 3))
show("simple 'difference' 3 x 4.1 - 2 x 3.6 (approx, %)", "{:.4f}".format(3 * 4.1 - 2 * 3.6))

print("\n=== c4 (Day 37): 3-year par rate from the same spots ===")
df = [1 / (1 + z) ** t for t, z in enumerate(Z, start=1)]
sdf = sum(df)
c4 = (1 - df[2]) / sdf * 100
show("DF1 / DF2 / DF3", "{} / {} / {}".format(both(df[0]), both(df[1]), both(df[2])))
show("DF1 / DF2 / DF3 (6 dp)", "{:.6f} / {:.6f} / {:.6f}".format(*df))
show("1.036^2 / 1.041^3 displays", "{} / {}".format(both(1.036 ** 2), both(1.041 ** 3)))
show("sum DF", "{!r} -> {}".format(sdf, both(sdf)))
show("1 - DF3", "{} ({:.6f})".format(both(1 - df[2]), 1 - df[2]))
show("c4 answer par rate (%)", "{!r} -> {} -> {:.3f}".format(c4, both(c4), c4))
show("check: a 3-year bond with coupon c4 at the spots", "{:.4f}".format(sum((c4 + (100 if t == 3 else 0)) * d for t, d in enumerate(df, start=1))))
show("trap: the 3-year spot 4.1 (miss)", "{:.4f}".format(4.1 - c4))
show("trap: 1 - DF3 over DF3 only (%)", "{:.4f}".format((1 - df[2]) / df[2] * 100))

print("\n=== c5 (Day 38): 7-year corporate 5.40%; govts 3.80% (5y), 4.30% (10y) ===")
g7 = 3.80 + (7 - 5) / (10 - 5) * (4.30 - 3.80)
c5 = (5.40 - g7) * 100
show("interpolated 7-year government yield (%)", "{!r} -> {:.4f}".format(g7, g7))
show("c5 answer G-spread (bp)", "{!r} -> {:.4f}".format(c5, c5))
show("trap: 5-year benchmark (bp)", "{:.4f}".format((5.40 - 3.80) * 100))
show("trap: 10-year benchmark (bp)", "{:.4f}".format((5.40 - 4.30) * 100))
show("trap: simple average of 5y and 10y (bp)", "{:.4f}".format((5.40 - (3.80 + 4.30) / 2) * 100))
show("BA/TI chain: 5.4 - (3.8 + 2/5 x (4.3 - 3.8)) =", both(5.40 - g7))

print("\n=== c6 (Day 38): callable, G 140 bp, Z 150 bp, call worth 35 bp ===")
show("B (right) OAS = Z - option", "{}".format(150 - 35))
show("A: Z + option (putable logic)", "{}".format(150 + 35))
show("C: G - option", "{}".format(140 - 35))

print("\n=== c7 (Day 39): 4-year 3% annual-coupon bond at a 4.5% yield: MacDur ===")
cf7 = [3, 3, 3, 103]
tcf7 = [t * cf for t, cf in enumerate(cf7, start=1)]
pv7 = [cf / 1.045 ** t for t, cf in enumerate(cf7, start=1)]
c7_price = sum(pv7)
c7_tpv = sum(tc / 1.045 ** t for t, tc in enumerate(tcf7, start=1))
c7 = c7_tpv / c7_price
show("cash flows / t x CF", "{} / {}".format(cf7, tcf7))
show("PVs", " / ".join("{:.4f}".format(v) for v in pv7))
show("price: NPV {3,3,3,103} at 4.5% (STO 1 / A)", "{!r} -> {}".format(c7_price, both(c7_price)))
show("sum t x PV: NPV {3,6,9,412} at 4.5% (STO 2 / B)", "{!r} -> {}".format(c7_tpv, both(c7_tpv)))
show("c7 answer MacDur", "{!r} -> {} -> {:.3f}".format(c7, both(c7), c7))
show("weights", " / ".join("{:.4f}".format(v / c7_price) for v in pv7))
show("ModDur = MacDur/1.045", "{:.4f}".format(c7 / 1.045))
show("TVM check of the price: N=4, I/Y=4.5, PMT=3, FV=100", "PV = {}".format(ba(-price(3, 4, 4.5))))
show("trap: ModDur reported (MacDur/1.045)", "{!r} -> {:.3f}".format(c7 / 1.045, c7 / 1.045))
show("weight of the last payment (%)", "{:.1f}".format(pv7[3] / c7_price * 100))
show("trap: simple average time weighted by cash flows (not PV)", "{:.4f}".format(sum(tcf7) / sum(cf7)))
show("Day 39 c1 (4-year 6% at 5%) for comparison: different numbers",
     "{:.4f}".format(sum(t * cf / 1.05 ** t for t, cf in enumerate([6, 6, 6, 106], start=1)) / price(6, 4, 5)))

print("\n=== c8 (Day 39): MacDur 6.4, horizon 4 years ===")
show("duration gap = 6.4 - 4", "{:.1f}".format(6.4 - 4))

print("\n=== c9 (Days 40-41): 8-year 3.5% annual bond at 4.2%, +/-5 bp, +100 bp ===")
q0 = price(3.5, 8, 4.2)
qm = price(3.5, 8, 4.15)
qp = price(3.5, 8, 4.25)
q52 = price(3.5, 8, 5.2)
QD, QC = approx(qm, q0, qp, 0.0005)
show("PV0 at 4.20% (STO 1 / A)", "{!r} -> BA PV = {} / stored {} / TI {}".format(q0, ba(-q0), ba(q0), ti(q0)))
show("PV- at 4.15% (STO 2 / B)", "{!r} -> BA PV = {} / stored {} / TI {}".format(qm, ba(-qm), ba(qm), ti(qm)))
show("PV+ at 4.25% (STO 3 / C)", "{!r} -> BA PV = {} / stored {} / TI {}".format(qp, ba(-qp), ba(qp), ti(qp)))
show("PV- - PV+ / PV- + PV+ - 2 PV0", "{:.4f} / {:.6f}".format(qm - qp, qm + qp - 2 * q0))
show("0.0005^2", "{:.8f}".format(0.0005 ** 2))
show("ApproxModDur (STO 4 / D)", "{!r} -> {}".format(QD, both(QD)))
show("ApproxCon (STO 5 / E)", "{!r} -> {} -> {:.2f}".format(QC, both(QC), QC))
c9_dur = -QD * 0.01
c9_con = 0.5 * QC * 0.01 ** 2
c9 = (c9_dur + c9_con) * 100
show("duration part (decimal)", both(c9_dur))
show("convexity part (decimal)", both(c9_con))
show("estimate (decimal)", both(c9 / 100))
show("c9 answer: duration + convexity (%)", "{!r} -> {} -> {:.2f}".format(c9, both(c9), c9))
show("exact at 5.2%: price / %", "{:.4f} / {:.4f}".format(q52, (q52 / q0 - 1) * 100))
show("duration only (%)", "{:.4f}".format(c9_dur * 100))
QDr, QCr = approx(round(qm, 4), round(q0, 4), round(qp, 4), 0.0005)
c9r = (-QDr * 0.01 + 0.5 * QCr * 0.0001) * 100
show("from 4-decimal prices: Dur / Con / estimate (tolerance check)",
     "{:.4f} / {:.4f} / {:.4f} (diff {:.4f})".format(QDr, QCr, c9r, c9r - c9))
c9rr = (-round(QD, 4) * 0.01 + 0.5 * round(QC, 2) * 0.0001) * 100
show("from Dur 4 dp and Con 2 dp", "{:.4f} (diff {:.4f})".format(c9rr, c9rr - c9))
show("trap: forgot 1/2", "{:.4f}".format((c9_dur + 2 * c9_con) * 100))
show("trap: convexity subtracted", "{:.4f}".format((c9_dur - c9_con) * 100))
show("trap: forgot the 2 in 2 x dYield: ApproxModDur", "{:.4f}".format((qm - qp) / (0.0005 * q0)))

print("\n=== c10 (Day 40): GBP 6m at ModDur 4.0, GBP 4m at ModDur 9.5, +50 bp ===")
mv = [6.0, 4.0]
dd = [4.0, 9.5]
dp = sum(m * d for m, d in zip(mv, dd)) / sum(mv)
c10 = -dp * sum(mv) * 1e6 * 0.005
show("weights", "{:.2f} / {:.2f}".format(mv[0] / 10, mv[1] / 10))
show("portfolio duration", "{!r} -> {:.4f}".format(dp, dp))
show("money duration (GBP)", "{:,.0f}".format(dp * 10e6))
show("c10 answer: change in value (GBP)", "{!r} -> {:,.2f}".format(c10, c10))
show("bond by bond: 4.0 x 6m x 0.005 / 9.5 x 4m x 0.005",
     "{:,.0f} / {:,.0f}".format(4.0 * 6e6 * 0.005, 9.5 * 4e6 * 0.005))
show("trap: equal weights -> duration / loss", "{:.4f} / {:,.0f}".format(sum(dd) / 2, sum(dd) / 2 * 10e6 * 0.005))
show("trap: 50 bp typed as 0.05", "{:,.0f}".format(dp * 10e6 * 0.05))
show("BA display of 6.2 x 10000000 x .005", both(dp * 10e6 * 0.005))

# =================================================================== release date
print("\n=== Release date ===")
start = datetime.date(2026, 11, 16)
due = start + datetime.timedelta(days=41)
show("Day 1 due (start_date)", "{} ({})".format(start.isoformat(), start.strftime("%A")))
show("Day 42 due (start_date + 41)", "{} ({})".format(due.isoformat(), due.strftime("%A")))
