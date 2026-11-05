#!/usr/bin/env python3
"""Day 13 of CFA Math: every number that appears on day-13.html.

Money-weighted vs time-weighted returns
(CFA Program 2027 Level I, Quantitative Methods, LM3 Benchmarking Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.

Calculator displays are shown at the 4 decimals both calculators are set to.
The BA II Plus shows thousands separators (NPV = 1,199.6448), the TI-84 not,
and the TI-84 drops the leading zero below 1 (.2200).
"""

import math


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


def chain(*rs):
    g = 1.0
    for r in rs:
        g *= 1 + r
    return g - 1


def annualize(total, years):
    return (1 + total) ** (1 / years) - 1


def show(label, value):
    print("  {:<64} {}".format(label, value))


# ---------------------------------------------------------------------------
print("=== Warm-up w1 (from Day 12): returns +18%, -12%, +9%: geometric mean ===")
w1_prod = 1.18 * 0.88 * 1.09
w1 = 100 * (w1_prod ** (1 / 3) - 1)
show("product of growth factors 1.18 x 0.88 x 1.09", "{!r} -> {} (TI {})".format(w1_prod, d4(w1_prod), ti4(w1_prod)))
show("cube root of the product", "{} (TI {})".format(d4(w1_prod ** (1 / 3)), ti4(w1_prod ** (1 / 3))))
show("root - 1", "{} (TI {})".format(d4(w1_prod ** (1 / 3) - 1), ti4(w1_prod ** (1 / 3) - 1)))
show("w1 geometric mean (answer, percent)", "{!r} -> {} -> {:.2f}%".format(w1, d4(w1), w1))
show("w1 trap: arithmetic mean (18 - 12 + 9)/3", "{:.2f}%".format((18 - 12 + 9) / 3))
show("w1 check: 1.0421^3 vs product", "{:.4f} vs {:.4f}".format((1 + w1 / 100) ** 3, w1_prod))
show("w1 $10,000 after the three years", money(10000 * w1_prod))
show("w1 10,000 x (1 + 4.2150%)^3 (steady rate, same end)", money(10000 * (1 + w1 / 100) ** 3))
show("w1 tolerance |4.21 - w1|","{:.4f}".format(abs(4.21 - w1)))

print("\n=== Warm-up w2 (from Day 4): which account pays the most? ===")
ear_a = (1 + 0.06 / 12) ** 12 - 1
ear_b = 0.061
ear_c = (1 + 0.0595 / 365) ** 365 - 1
show("A 6.00% compounded monthly: EAR (right)", "{} -> {}".format(d4(100 * ear_a), pct(ear_a)))
show("B 6.10% compounded annually: EAR", "{} -> {}".format(d4(100 * ear_b), pct(ear_b)))
show("C 5.95% compounded daily (365): EAR", "{} -> {}".format(d4(100 * ear_c), pct(ear_c)))
show("1.005^12", "{:.6f}".format(1.005 ** 12))
show("periodic rate 6/12 (percent per month)", "{:.2f}".format(6 / 12))

# ---------------------------------------------------------------------------
print("\n=== Story: CGM Focus (plan numbers; arithmetic only) ===")
show("1.18^10 (about 18% a year for ten years)", "{:.4f}".format(1.18 ** 10))
show("1.179^10 (17.9%, CBS/Morningstar)", "{:.4f}".format(1.179 ** 10))
show("0.89^10 (about -11% a year)", "{:.4f}".format(0.89 ** 10))
show("0.892^10 (-10.8%, CBS/Morningstar)", "{:.4f}".format(0.892 ** 10))
show("$2.6 billion inflow, 2008; fund fell about 48%", "(plan figures)")

print("\n=== Back to the story: a hypothetical investor (plan payoff) ===")
s_dep0, s_dep1, s_r1, s_r2 = 10000.0, 20000.0, 0.80, -0.48
s_v1 = s_dep0 * (1 + s_r1)
s_v1b = s_v1 + s_dep1
s_v2 = s_v1b * (1 + s_r2)
show("start 2007: 10,000 x 1.80", money(s_v1))
show("plus 20,000 at the start of 2008", money(s_v1b))
show("end 2008: 38,000 x 0.52", money(s_v2))
show("total put in", money(s_dep0 + s_dep1))
show("share of her money added before 2008", "{:.4f} (two-thirds)".format(s_dep1 / (s_dep0 + s_dep1)))
show("dollars lost: 30,000 - 19,760", money(s_dep0 + s_dep1 - s_v2))
s_twr2 = chain(s_r1, s_r2)
s_twr = annualize(s_twr2, 2)
show("TWR two years: 1.80 x 0.52 - 1", "{:.4f} -> {}".format(1.8 * 0.52, pct(s_twr2)))
show("TWR annualized: sqrt(0.936) - 1", "{!r} -> {}".format(s_twr, pct(s_twr)))
s_cfs = [-s_dep0, -s_dep1, s_v2]
s_mwr = irr(s_cfs)
show("MWR = IRR(-10,000, -20,000, +19,760)", "{!r} -> {} (BA IRR = {})".format(s_mwr, pct(s_mwr), d4(100 * s_mwr)))
show("  check NPV at the MWR", "{:.8f}".format(npv(s_mwr, s_cfs)))
show("all 30,000 in at the start of 2007, untouched", money(30000 * 1.8 * 0.52))
show("Mind the Gap: 7.7% - 6.0%", "{:.1f} percentage points".format(7.7 - 6.0))

# ---------------------------------------------------------------------------
print("\n=== Why it works: the 100 / 100 / 231 example ===")
y_v1 = 100 * 1.20
y_v1b = y_v1 + 100
y_v2 = 231.0
y_h1 = y_v1 / 100 - 1
y_h2 = y_v2 / y_v1b - 1
show("year 1: 100 x 1.20", "{:.2f}".format(y_v1))
show("after the second 100", "{:.2f}".format(y_v1b))
show("year 2 HPR: 231 / 220 - 1", "{:.4f} -> {}".format(y_h2, pct(y_h2)))
show("chain 1.20 x 1.05", "{:.4f}".format((1 + y_h1) * (1 + y_h2)))
y_twr = annualize(chain(y_h1, y_h2), 2)
show("TWR annualized sqrt(1.26) - 1", "{:.6f} -> {}".format(y_twr, pct(y_twr)))
show("1.10^2", "{:.2f}".format(1.10 ** 2))
show("MWR check: 100 x 1.1^2 + 100 x 1.1","{:.2f} + {:.2f} = {:.2f}".format(100 * 1.1 ** 2, 100 * 1.1, 100 * 1.1 ** 2 + 100 * 1.1))
y_mwr = irr([-100, -100, 231])
show("MWR = IRR(-100, -100, +231)", "{!r} -> {}".format(y_mwr, pct(y_mwr)))
show("NPV at 10%: -100 - 100/1.1 + 231/1.21", "{:.10f}".format(npv(0.10, [-100, -100, 231])))
show("single deposit 100, no flows, ends 126: IRR", pct(irr([-100, 0, 126])))
show("  equals TWR sqrt(1.26) - 1", pct(1.26 ** 0.5 - 1))

# ---------------------------------------------------------------------------
print("\n=== Worked example: 1,000,000; 1,150,000; withdraw 400,000; 690,000 ===")
we_h1 = 1150000 / 1000000 - 1
we_start2 = 1150000 - 400000
we_h2 = 690000 / we_start2 - 1
we_twr2 = chain(we_h1, we_h2)
we_twr = annualize(we_twr2, 2)
show("HPR_1", pct(we_h1))
show("start of sub-period 2", money(we_start2))
show("HPR_2 = 690,000/750,000 - 1", "{:.4f} -> {}".format(we_h2, pct(we_h2)))
show("1.15 x 0.92", "{:.4f}".format(1.15 * 0.92))
show("TWR over two years", pct(we_twr2))
show("TWR annualized sqrt(1.058) - 1", "{!r} -> {} ({})".format(we_twr, pct(we_twr), pct(we_twr, 4)))
show("  sqrt(1.058)", "{:.6f}".format(1.058 ** 0.5))
show("trap: 5.80% / 2", "{:.2f}%".format(100 * we_twr2 / 2))
a, b, c = 690000.0, 400000.0, -1000000.0
disc = b * b - 4 * a * c
show("400,000^2", "{:,.0f}".format(b * b))
show("4 x 690,000 x 1,000,000", "{:,.0f}".format(4 * a * -c))
show("discriminant", "{:,.0f}".format(disc))
show("sqrt(discriminant)", "{:,.1f}".format(math.sqrt(disc)))
show("numerator -400,000 + sqrt", "{:,.1f}".format(-b + math.sqrt(disc)))
show("2 x 690,000", "{:,.0f}".format(2 * a))
v = (-b + math.sqrt(disc)) / (2 * a)
show("v", "{:.6f}".format(v))
show("1/v", "{:.6f}".format(1 / v))
we_mwr = 1 / v - 1
show("MWR = 1/v - 1", "{!r} -> {} ({})".format(we_mwr, pct(we_mwr), pct(we_mwr, 4)))
we_irr = irr([-1000000, 400000, 690000])
show("MWR by IRR (BA IRR / TI irr)", "{} / {}".format(d4(100 * we_irr), ti4(100 * we_irr)))
show("check: 1,000,000 x 1.0544 - 400,000, then x 1.0544",
     money((1000000 * (1 + we_mwr) - 400000) * (1 + we_mwr)))
show("  balance after the withdrawal at the MWR", money(1000000 * (1 + we_mwr) - 400000))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: one share at 100, a second at 120, sell both at 130 ===")
p0, p1, p2, dv = 100.0, 120.0, 130.0, 2.0
cf0 = -p0
cf1 = -p1 + dv
cf2 = 2 * dv + 2 * p2
show("CF0 = -100", "{:.2f} (BA CFo = {})".format(cf0, d4(cf0)))
show("CF1 = -120 + 2", "{:.2f} (BA C01 = {})".format(cf1, d4(cf1)))
show("CF2 = 2 x 2 + 2 x 130", "{:.2f} (BA C02 = {})".format(cf2, d4(cf2)))
show("  year-2 dividends 2 x 2.00 / shares 2 x 130", "{:.2f} / {:.2f}".format(2 * dv, 2 * p2))
wt_cfs = [cf0, cf1, cf2]
wt_mwr = irr(wt_cfs)
show("MWR = IRR (BA IRR = / TI irr)", "{!r} -> IRR = {} / {} -> {}".format(100 * wt_mwr, d4(100 * wt_mwr), ti4(100 * wt_mwr), pct(wt_mwr)))
show("  check NPV at the MWR", "{:.8f}".format(npv(wt_mwr, wt_cfs)))
wt_h1 = (p1 - p0 + dv) / p0
wt_h2 = (p2 - p1 + dv) / p1
show("HPR_1 = (120 - 100 + 2)/100", "{} (TI {}) -> {}".format(d4(wt_h1), ti4(wt_h1), pct(wt_h1)))
show("  BA: 120 - 100 + 2 =", d4(p1 - p0 + dv))
show("HPR_2 = (130 - 120 + 2)/120", "{} (TI {}) -> {}".format(d4(wt_h2), ti4(wt_h2), pct(wt_h2)))
show("  BA: 130 - 120 + 2 =", d4(p2 - p1 + dv))
show("portfolio view, year 2: start 2 x 120", "{:.2f}".format(2 * p1))
show("portfolio view, year 2: 264/240 - 1", pct(cf2 / (2 * p1) - 1))
show("portfolio view, year 1: (120 + 2)/100 - 1", pct((p1 + dv) / p0 - 1))
wt_prod = (1 + wt_h1) * (1 + wt_h2)
wt_root = wt_prod ** 0.5
wt_twr = wt_root - 1
show("BA 1.22 x 1.1 =", d4(wt_prod))
show("BA sqrt -> (TI sqrt(1.22*1.1))", "{} (TI {})".format(d4(wt_root), ti4(wt_root)))
show("- 1 =", "{} (TI {})".format(d4(wt_twr), ti4(wt_twr)))
show("x 100 = (TWR, percent)", "{!r} -> {} (TI {}) -> {}".format(100 * wt_twr, d4(100 * wt_twr), ti4(100 * wt_twr), pct(wt_twr)))
show("two-year TWR 1.342 - 1", pct(wt_prod - 1))
show("money in: year 1 vs year 2", "{:.0f} vs {:.0f}".format(p0, 2 * p1))
show("MWR account check (not on the page): 100 x (1 + MWR)", "{:.4f}".format(100 * (1 + wt_mwr)))
show("  + 118", "{:.4f}".format(100 * (1 + wt_mwr) + 118))
show("  x (1 + MWR)", "{:.4f}".format((100 * (1 + wt_mwr) + 118) * (1 + wt_mwr)))
show("gap TWR - MWR (points)", "{:.2f}".format(100 * (wt_twr - wt_mwr)))

# ---------------------------------------------------------------------------
print("\n=== c1: 1 share at 50; +1.00 div and 2 more at 60; 1.00/share and sell 3 at 57 ===")
c1_cfs = [-50.0, -2 * 60.0 + 1.0, 3 * 1.0 + 3 * 57.0]
show("cash flows", c1_cfs)
c1 = 100 * irr(c1_cfs)
show("c1 MWR (answer, percent)", "{!r} -> IRR = {} / TI {} -> {:.2f}%".format(c1, d4(c1), ti4(c1), c1))
show("  check NPV at the MWR", "{:.8f}".format(npv(c1 / 100, c1_cfs)))
c1_h1 = (60 - 50 + 1) / 50
c1_h2 = (57 - 60 + 1) / 60
show("year-2 dividends 3 x 1.00 / shares 3 x 57", "{:.2f} / {:.2f}".format(3 * 1.0, 3 * 57.0))
show("HPR_1 (60 - 50 + 1)/50", "{} -> {}".format(d4(c1_h1), pct(c1_h1)))
show("1 + HPR_2 (6 decimals)", "{:.6f}".format(1 + (57 - 60 + 1) / 60))
show("HPR_2 (57 - 60 + 1)/60", "{} (TI {}) -> {}".format(d4(c1_h2), ti4(c1_h2), pct(c1_h2)))
c1_prod = (1 + c1_h1) * (1 + c1_h2)
show("product 1.22 x (1 + HPR_2)", "{!r} -> {}".format(c1_prod, d4(c1_prod)))
c1_twr = c1_prod ** 0.5 - 1
show("TWR annualized", "{} -> {}".format(d4(100 * c1_twr), pct(c1_twr)))
show("shares held: year 1 vs year 2", "1 vs 3")
show("money in: year 1 vs year 2", "{:.0f} vs {:.0f}".format(50, 3 * 60))
show("c1 trap: signs flipped on C01 (+119)", "IRR of -50, +119, +174 -> {}".format(pct(irr([-50, 119, 174]))))
show("c1 trap: dividends left out (-120, +171)", "{}".format(pct(irr([-50, -120, 171]))))
show("c1 tolerance |2.27 - c1|", "{:.4f}".format(abs(2.27 - c1)))

print("\n=== c3: TWR with deposits/withdrawals during the year ===")
c3_v0, c3_v1, c3_dep = 200000.0, 208000.0, 50000.0
c3_s2 = c3_v1 + c3_dep
c3_v2 = c3_s2 * 0.98
c3_wd = 30000.0
c3_s3 = c3_v2 - c3_wd
c3_v3 = c3_s3 * 1.06
show("31 Mar value 200,000 x 1.04", money(c3_v0 * 1.04))
show("start of sub-period 2: 208,000 + 50,000", money(c3_s2))
show("31 Aug value 258,000 x 0.98", money(c3_v2))
show("start of sub-period 3: 252,840 - 30,000", money(c3_s3))
show("31 Dec value 222,840 x 1.06", money(c3_v3))
c3_h = [c3_v1 / c3_v0 - 1, c3_v2 / c3_s2 - 1, c3_v3 / c3_s3 - 1]
show("HPRs", " / ".join(pct(h) for h in c3_h))
c3 = 100 * chain(*c3_h)
show("c3 TWR (answer, percent)", "{!r} -> {} -> {:.2f}%".format(c3, d4(c3), c3))
show("  1.04 x 0.98 x 1.06", "{:.6f}".format(1.04 * 0.98 * 1.06))
show("  BA chain: 208000/200000 x 252840/258000 x 236210.4/222840 - 1 =",
     "{} (TI {})".format(d4(c3 / 100), ti4(c3 / 100)))
show("c3 trap: end/start - 1 (deposits counted as return)", pct(c3_v3 / c3_v0 - 1))
show("c3 trap: (end - start - net deposits)/start", pct((c3_v3 - c3_v0 - (c3_dep - c3_wd)) / c3_v0))
show("c3 trap: adding 4 - 2 + 6", "{:.2f}%".format(4 - 2 + 6))
show("net deposits 50,000 - 30,000", money(c3_dep - c3_wd))
show("c3 MWR for comparison (monthly IRR, annualized)", "(not shown on the page)")

print("\n=== c4: 100 in; +15%; add 100; 215 earns 6%; ends 227.90 ===")
c4_v1 = 100 * 1.15
c4_s2 = c4_v1 + 100
c4_v2 = c4_s2 * 1.06
show("end of year 1", "{:.2f}".format(c4_v1))
show("start of year 2", "{:.2f}".format(c4_s2))
show("end of year 2: 215 x 1.06", "{:.2f}".format(c4_v2))
c4_two = 1.15 * 1.06 - 1
show("two-year TWR 1.15 x 1.06 - 1", "{:.4f} -> {}".format(1.15 * 1.06, pct(c4_two)))
c4_B = annualize(c4_two, 2)
show("B (right) sqrt(1.219) - 1", "{!r} -> {}".format(c4_B, pct(c4_B)))
c4_A = c4_two / 2
show("A (arithmetic annualizing) 21.90% / 2", pct(c4_A))
c4_C = irr([-100, -100, c4_v2])
show("C (MWR) IRR(-100, -100, +227.90)", "{!r} -> {}".format(c4_C, pct(c4_C)))
show("  quadratic: x^2 + x - 2.279 = 0, x = 1 + r", "{:.6f}".format((-1 + math.sqrt(1 + 4 * 2.279)) / 2))
show("  wrong: 227.90 / 200 - 1 (deposit lumped in)", pct(c4_v2 / 200 - 1))
c4_prod = 1.15 * 1.06
show("BA 1.15 x 1.06 = | sqrt | - 1 =", "{} | {} | {}".format(d4(c4_prod), d4(c4_prod ** 0.5), d4(c4_prod ** 0.5 - 1)))
show("TI sqrt(1.15*1.06)-1", ti4(c4_prod ** 0.5 - 1))
show("C as BA IRR / TI irr(-100,{-100,227.9})", "IRR = {} / {}".format(d4(100 * c4_C), ti4(100 * c4_C)))

# ---------------------------------------------------------------------------
print("\n=== Exam traps ===")
show("worked example: 690,000 / 1,000,000 - 1 (withdrawal counted as loss)", pct(690000 / 1000000 - 1))
show("walkthrough with C01 entered as +118 (sign error)", pct(irr([-100, 118, 264])))
show("walkthrough with every sign flipped (100, 118, -264)", pct(irr([100, 118, -264])))
show("annualizing: sqrt(1.058) - 1 vs 5.80 / 2", "{} vs {:.2f}%".format(pct(we_twr), 100 * we_twr2 / 2))

# ---------------------------------------------------------------------------
print("\n=== Rounded forms used in the prose ===")
show("walkthrough MWR / TWR (2 dp)", "{} / {}".format(pct(wt_mwr), pct(wt_twr)))
show("worked MWR / TWR (2 dp)", "{} / {}".format(pct(we_mwr), pct(we_twr)))
show("story hypothetical MWR / TWR (2 dp)", "{} / {}".format(pct(s_mwr), pct(s_twr)))
show("why-it-works MWR / TWR (2 dp); money at work year 1 vs 2: 100 vs 220", "{} / {}".format(pct(y_mwr), pct(y_twr)))
show("c1 MWR / TWR (2 dp)", "{} / {}".format(pct(c1 / 100), pct(c1_twr)))
show("c4 options A / B / C", "{} / {} / {}".format(pct(c4_A), pct(c4_B), pct(c4_C)))

# ---------------------------------------------------------------------------
print("\n=== Grading check: data-tol accepts the asked precision, rejects distractors ===")


def accepts(ans, tol, typed, is_pct=True):
    cands = [typed] + ([typed * 100] if is_pct else [])
    return any(abs(c - ans) <= tol + 1e-12 for c in cands)


for name, ans, tol, good, bad in [
    ("w1", w1, 0.01, [4.22, 0.0422], [5.00]),
    ("c1", c1, 0.01, [2.27, 0.0227], [100 * c1_twr, 100 * irr([-50, -120, 171])]),
    ("c3", c3, 0.01, [8.04, 0.0804], [100 * (c3_v3 / c3_v0 - 1),
                                      100 * (c3_v3 - c3_v0 - (c3_dep - c3_wd)) / c3_v0, 8.00]),
]:
    show("{} tol {}: accepts {}".format(name, tol, good),
         "{} | rejects {}: {}".format([accepts(ans, tol, g) for g in good],
                                       ["{:.2f}".format(b) for b in bad],
                                       [not accepts(ans, tol, b) for b in bad]))
