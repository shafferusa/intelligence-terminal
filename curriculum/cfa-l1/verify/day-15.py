#!/usr/bin/env python3
"""Day 15 of CFA Math: every number that appears on day-15.html.

Pricing a bond from its yield
(CFA Program 2027 Level I, Fixed Income, Fixed-Income Bond Valuation: Prices and Yields).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""


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
    print("  {:<64} {}".format(label, value))


def annuity_factor(r, n):
    """Day 5: PV of 1 a period for n periods, first payment at t = 1."""
    return (1 - (1 + r) ** -n) / r


def bond_pv(pmt, fv, r, n):
    """Price: n coupons of pmt, then fv with the last one; r per period.
    Summed term by term (the definition), not from the annuity formula."""
    return sum(pmt / (1 + r) ** t for t in range(1, n + 1)) + fv / (1 + r) ** n


def tvm_pv(n, iy, pmt, fv):
    """What CPT PV shows (END mode, P/Y = C/Y = 1): the negative of the price."""
    r = iy / 100
    if r == 0:
        return -(pmt * n + fv)
    return -(pmt * annuity_factor(r, n) + fv / (1 + r) ** n)


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 12): $800 invested at prices 16, 20, 25 -> average cost per share (harmonic mean)
prices = [16, 20, 25]
amount = 800
shares = [amount / p for p in prices]
recips = sum(1 / p for p in prices)
w1 = len(prices) / recips
show("w1  shares bought: 800/16, 800/20, 800/25", ", ".join("{:g}".format(s) for s in shares))
show("w1  total shares, total money", "{:g}, {}".format(sum(shares), amount * len(prices)))
show("w1  money / shares", "{!r} -> {:.4f}".format(amount * 3 / sum(shares), amount * 3 / sum(shares)))
show("w1  1/16 + 1/20 + 1/25 (BA after =)", "{!r} -> {} / TI {}".format(recips, ba(recips), ti(recips)))
show("w1  1/x of that", ba(1 / recips))
show("w1  x 3 = harmonic mean (answer)", "{!r} -> {} -> {:.2f}".format(w1, ba(w1), w1))
show("w1  arithmetic mean of the prices (the trap)", "{:.4f} -> {:.2f}".format(sum(prices) / 3, sum(prices) / 3))

# w2 (Day 8): perpetual bond, 4.00 a year per 100, first payment in a year, r = 5%
w2 = 4 / 0.05
show("w2  4 / 0.05 (correct, C)", "{!r} -> {} / TI {}".format(w2, ba(w2), ti(w2)))
show("w2  discounted one extra period: 80 / 1.05 (B)", ba(w2 / 1.05))
show("w2  at the coupon rate: 4 / 0.04 (A)", ba(4 / 0.04))

# ------------------------------------------------------------------ story
print("\n=== Story: Disney's 100-year bond, July 1993 ===")
show("issue size ($ million), coupon %, years", "300, 7.55, {}".format(2093 - 1993))
show("annual coupon per 100 of par", "{:.2f}".format(7.55))
show("coupons over a full century per 100 (100 x 7.55)", "{:.2f}".format(100 * 7.55))
show("coupons + the 100 repaid", "{:.2f}".format(100 * 7.55 + 100))
show("semiannual coupon per 100 (7.55 / 2)", "{:.4f}".format(7.55 / 2))
show("semiannual periods (100 x 2)", 100 * 2)
show("redeemed 15 Jul 2023: years before 2093", 2093 - 2023)
show("8-K: outstanding redeemed", "201,169,000 -> about $201 million")
show("years since the last US century bond (1954) in 1993", 1993 - 1954)

# ------------------------------------------------------------------ why it works
print("\n=== Why it works: 2-year bond, 8% annual coupon, par 100 ===")
r = 0.10
a, b = 8 / 1.10, 108 / 1.10 ** 2
show("8 / 1.10", "{:.4f}".format(a))
show("108 / 1.10^2 (= 108 / 1.21)", "{:.4f}".format(b))
show("price at 10% (term by term)", "{!r} -> {:.4f}".format(a + b, a + b))
af = annuity_factor(0.10, 2)
show("annuity factor (1 - 1.10^-2) / 0.10", "{:.6f}".format(af))
show("coupons: 8 x factor", "{:.4f}".format(8 * af))
show("par: 100 / 1.21", "{:.4f}".format(100 / 1.21))
show("annuity + single sum", "{:.4f}".format(8 * af + 100 / 1.21))
p8a, p8b = 8 / 1.08, 108 / 1.08 ** 2
show("at 8%: 8/1.08, 108/1.08^2 (=108/1.1664)", "{:.4f}, {:.4f}".format(p8a, p8b))
show("at 8%: price", "{!r} -> {:.4f}".format(p8a + p8b, p8a + p8b))
show("1.08^2", "{:.4f}".format(1.08 ** 2))
p6a, p6b = 8 / 1.06, 108 / 1.06 ** 2
show("at 6%: 8/1.06, 108/1.06^2 (=108/1.1236)", "{:.4f}, {:.4f}".format(p6a, p6b))
show("at 6%: price (premium)", "{:.4f}".format(p6a + p6b))
show("one year left, 10%: 108 / 1.10", "{:.4f}".format(108 / 1.10))
show("price change over the year (pull to par)", "{:.4f}".format(108 / 1.10 - (a + b)))
show("check: 96.5289 grows at 10% for a year", "{:.4f}".format((a + b) * 1.10))
show("  minus the 8 coupon = price with one year left", "{:.4f}".format((a + b) * 1.10 - 8))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: 3-year 5% annual coupon at 6%; 3-year zero at 6% ===")
we1, we2, we3 = 5 / 1.06, 5 / 1.06 ** 2, 105 / 1.06 ** 3
show("5/1.06", "{:.4f}".format(we1))
show("5/1.06^2", "{:.4f}".format(we2))
show("1.06^2, 1.06^3", "{:.6f}, {:.6f}".format(1.06 ** 2, 1.06 ** 3))
show("105/1.06^3", "{:.4f}".format(we3))
show("sum of the rounded pieces", "{:.4f}".format(round(we1, 4) + round(we2, 4) + round(we3, 4)))
we_a = bond_pv(5, 100, 0.06, 3)
show("(a) price, unrounded", "{!r} -> {:.4f}".format(we_a, we_a))
af3 = annuity_factor(0.06, 3)
show("annuity factor (1 - 1.06^-3)/0.06", "{:.6f}".format(af3))
show("coupons as an annuity: 5 x factor", "{:.4f}".format(5 * af3))
we_b = 100 / 1.06 ** 3
show("(b) zero: 100/1.06^3", "{!r} -> {:.4f}".format(we_b, we_b))
show("annuity + zero", "{:.4f}".format(5 * af3 + we_b))
show("at 4%: same coupon bond (premium)", "{:.4f}".format(bond_pv(5, 100, 0.04, 3)))
show("one year on, 2 years left, still 6%", "{:.4f}".format(bond_pv(5, 100, 0.06, 2)))
show("two years on, 1 year left: 105/1.06", "{:.4f}".format(bond_pv(5, 100, 0.06, 1)))
show("share of (a) that is principal: 83.9619/97.3269", "{:.2%}".format(we_b / we_a))
show("TVM check (a): N=3 I/Y=6 PMT=5 FV=100", "PV = {}".format(ba(tvm_pv(3, 6, 5, 100))))
show("TVM check (b): N=3 I/Y=6 PMT=0 FV=100", "PV = {}".format(ba(tvm_pv(3, 6, 0, 100))))

# ------------------------------------------------------------------ walkthrough
print("\n=== Calculator walkthrough: Disney 7.55% due 2093, per 100, semiannual ===")
N, PMT, FV = 200, 7.55 / 2, 100
for iy, label in [(3.775, "I/Y = 3.775 (7.55%, at issue)"), (2, "I/Y = 2 (4%)"), (4.5, "I/Y = 4.5 (9%)")]:
    pv = tvm_pv(N, iy, PMT, FV)
    direct = bond_pv(PMT, FV, iy / 100, N)
    show(label, "{!r} -> BA PV = {} / TI PV={}  (term-by-term {:.6f})".format(pv, ba(pv), ti(pv), direct))
final9 = tvm_pv(N, 4.5, 0, FV)
final755 = tvm_pv(N, 3.775, 0, FV)
show("final 100 only, I/Y = 4.5, PMT = 0", "{!r} -> BA PV = {} / TI PV={}".format(final9, ba(final9), ti(final9)))
show("final 100 only, I/Y = 3.775, PMT = 0", "{!r} -> BA PV = {} / TI PV={}".format(final755, ba(final755), ti(final755)))
show("  in dollars per $100: at 7.55%, at 9%", "${:.2f}, ${:.3f}".format(-final755, -final9))
show("1.03775^200, 1.045^200", "{:,.1f}, {:,.1f}".format(1.03775 ** 200, 1.045 ** 200))
p755 = -tvm_pv(N, 3.775, PMT, FV)
show("coupons' share of the 100.00 at 7.55%: 100 - 0.0605", "{:.4f} -> {:.2f}".format(p755 + final755, p755 + final755))
p9 = -tvm_pv(N, 4.5, PMT, FV)
show("coupons' share at 9%: (83.8913 - 0.0150)/83.8913", "{:.2%}".format((p9 + final9) / p9))
perp = 3.775 / 0.045
show("perpetuity (Day 8): 3.775 / 0.045 = 7.55 / 0.09", "{!r} -> {:.4f} / {:.4f}".format(perp, perp, 7.55 / 0.09))
show("bond minus perpetuity at 9%", "{:.4f}".format(p9 - perp))
show("perpetuity tail beyond period 200: 83.8889/1.045^200", "{:.4f}".format(perp / 1.045 ** 200))
show("perp - tail + final 100 = bond", "{:.4f}".format(perp - perp / 1.045 ** 200 - final9))
show("perpetuity at 4%: 3.775/0.02 (vs bond 187.0590)", "{:.4f}".format(3.775 / 0.02))
show("perpetuity at 7.55%: 3.775/0.03775", "{:.4f}".format(3.775 / 0.03775))
show("annual-pay version at 9% (N=100, PMT=7.55, I/Y=9)", ba(-tvm_pv(100, 9, 7.55, 100)))

# ------------------------------------------------------------------ traps
print("\n=== Exam traps ===")
show("coupon not halved: N=200, I/Y=4.5, PMT=7.55", ba(-tvm_pv(200, 4.5, 7.55, 100)))
show("discounting at the coupon rate always gives par (any N)", "{:.4f}, {:.4f}".format(
    -tvm_pv(3, 5, 5, 100), -tvm_pv(200, 3.775, 3.775, 100)))
show("worked example with PMT = -5, FV = +100 (signs mixed)", "PV = {}".format(ba(tvm_pv(3, 6, -5, 100))))

# ------------------------------------------------------------------ check questions
print("\n=== Check questions ===")
c1 = -tvm_pv(60, 2.5, 3, 100)
show("c1  30-yr 6% semiannual at 5%: N=60 I/Y=2.5 PMT=3 FV=100", "{!r} -> BA PV = -{} / TI PV=-{} -> {:.2f}".format(c1, ba(c1), ti(c1), c1))
show("c1  term-by-term check", "{:.6f}".format(bond_pv(3, 100, 0.025, 60)))
show("c1  wrong: annual inputs N=30 I/Y=5 PMT=6", ba(-tvm_pv(30, 5, 6, 100)))
show("c1  PV of the 100 alone: 100/1.025^60", ba(100 / 1.025 ** 60))

# c2: conceptual, no numbers beyond the illustration
c2_old = -tvm_pv(10, 5, 5, 100)
c2_new = -tvm_pv(10, 6, 5, 100)
show("c2  illustration: 10-yr 5% annual bond at 5% and at 6%", "{:.4f}, {:.4f}".format(c2_old, c2_new))

c3 = 100 / 1.025 ** 20
show("c3  10-yr zero, 5% semiannual: 100/1.025^20", "{!r} -> BA {} / TI {} -> {:.2f}".format(c3, ba(c3), ti(c3), c3))
show("c3  TVM: N=20 I/Y=2.5 PMT=0 FV=100", "PV = {}".format(ba(tvm_pv(20, 2.5, 0, 100))))
show("c3  wrong: annual compounding 100/1.05^10", "{:.4f}".format(100 / 1.05 ** 10))
show("c3  1.025^20", "{:.6f}".format(1.025 ** 20))

c4 = -tvm_pv(40, 3, 2, 100)
c4_b = -tvm_pv(40, 3, 4, 100)
c4_c = -tvm_pv(40, 2, 2, 100)
show("c4  20-yr 4% semiannual at 6%: N=40 I/Y=3 PMT=2 (A)", "{!r} -> BA PV = -{} / TI PV=-{} -> {:.2f}".format(c4, ba(c4), ti(c4), c4))
show("c4  coupon not halved: PMT=4 (B)", "{:.4f} -> {:.2f}".format(c4_b, c4_b))
show("c4  discounted at the coupon rate: I/Y=2 (C)", "{:.4f} -> {:.2f}".format(c4_c, c4_c))
show("c4  annual inputs N=20 I/Y=6 PMT=4 (for reference)", "{:.4f}".format(-tvm_pv(20, 6, 4, 100)))

# ------------------------------------------------------------------ rounded figures in the text
print("\n=== Rounded figures quoted in the text (from the values above) ===")
show("w2 coupon per 100; w2 options", "{:.2f}; {:.2f}, {:.2f}, {:.2f}".format(4, 4 / 0.04, w2 / 1.05, w2))
show("story: coupons, coupons + par (whole dollars)", "{:.0f}, {:.0f}".format(100 * 7.55, 100 * 7.55 + 100))
show("BA display after 200 N, 3.775 PMT, 100 FV", "N = {}, PMT = {}, FV = {}".format(ba(200), ba(3.775), ba(100)))
show("walkthrough prices, 2 decimals: 7.55%, 4%, 9%", "{:.2f}, {:.2f}, {:.2f}".format(
    -tvm_pv(N, 3.775, PMT, FV), -tvm_pv(N, 2, PMT, FV), p9))
show("final 100 alone: 7.55%, 9%", "{:.2f}, {:.3f}".format(-final755, -final9))
show("worked example (a), (b), 2 decimals", "{:.2f}, {:.2f}".format(we_a, we_b))
show("c1 wrong (annual inputs), 2 decimals", "{:.2f}".format(-tvm_pv(30, 5, 6, 100)))
show("c3 wrong (annual compounding), 2 decimals", "{:.2f}".format(100 / 1.05 ** 10))

# ------------------------------------------------------------------ headline
print("\n=== Headline length ===")
h1 = "Disney borrowed for 100 years, and the $100 it promised to repay in 2093 was worth six cents"
show("h1 characters", len(h1))
