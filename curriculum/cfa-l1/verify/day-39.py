#!/usr/bin/env python3
"""Day 39 of CFA Math: every number that appears on day-39.html.

Macaulay and modified duration
(CFA Program 2027 Level I, Fixed Income, Yield-Based Bond Duration Measures and Properties).
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
    print("  {:<66} {}".format(label, value))


def flows(coupon, n, face=100.0):
    """Cash flows per period of a plain bond: coupon each period, face with the last."""
    return [coupon + (face if t == n else 0.0) for t in range(1, n + 1)]


def npv0(rate_pct, cfs):
    """npv(rate, 0, {cfs}) / the CF worksheet with CFo = 0: sum of CF_t / (1 + r)^t."""
    r = rate_pct / 100.0
    return sum(cf / (1 + r) ** t for t, cf in enumerate(cfs, start=1))


def price(coupon, n, y):
    """Price per 100, y per period as a decimal (term by term, the definition)."""
    return sum(cf / (1 + y) ** t for t, cf in enumerate(flows(coupon, n), start=1))


def macdur(coupon, n, y):
    """PV-weighted average time, in periods."""
    cfs = flows(coupon, n)
    pv = [cf / (1 + y) ** t for t, cf in enumerate(cfs, start=1)]
    return sum(t * p for t, p in enumerate(pv, start=1)) / sum(pv)


def solve_yield(coupon, n, target):
    """Yield per period (decimal) that gives the target price, by bisection."""
    lo, hi = -0.005, 0.5
    for _ in range(300):
        mid = (lo + hi) / 2
        if price(coupon, n, mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 38): G-spread with an interpolated benchmark
corp8, g5, g10 = 5.45, 3.70, 4.20
g8 = g5 + (8 - 5) / (10 - 5) * (g10 - g5)
w1 = (corp8 - g8) * 100
show("w1  interpolated 8-year government yield 3.70 + 3/5 x 0.50 (%)", "{!r} -> {:.2f}".format(g8, g8))
show("w1  5.45 - (3.7 + 3/5 x .5) (BA after =)", "{} / TI {}".format(ba(corp8 - g8), ti(corp8 - g8)))
show("w1  G-spread in bp (answer)", "{!r} -> {} -> {:.0f}".format(w1, ba(w1), w1))
show("w1  trap: against the 10-year (bp)", "{:.0f}".format(round((corp8 - g10) * 100, 6)))
show("w1  trap: against the 5-year (bp)", "{:.0f}".format(round((corp8 - g5) * 100, 6)))

# w2 (Day 32): two-asset portfolio standard deviation, percent units
wa, wb, sa, sb, rho = 0.7, 0.3, 15.0, 5.0, -0.2
t1 = wa ** 2 * sa ** 2
t2 = wb ** 2 * sb ** 2
t3 = 2 * wa * wb * rho * sa * sb
var_p = t1 + t2 + t3
sig_p = math.sqrt(var_p)
show("w2  0.7^2 x 15^2", "{:.4f}".format(t1))
show("w2  0.3^2 x 5^2", "{:.4f}".format(t2))
show("w2  2 x 0.7 x 0.3 x (-0.2) x 15 x 5", "{:.4f}".format(t3))
show("w2  variance (%^2) (BA after =)", "{} / TI {}".format(ba(var_p), ti(var_p)))
show("w2  sigma_p (correct, C)", "{!r} -> {} -> {:.2f}%".format(sig_p, ba(sig_p), sig_p))
wavg = wa * sa + wb * sb
show("w2  weighted average of SDs (A, rho = +1)", "{:.2f}%".format(wavg))
sig_plus = math.sqrt(t1 + t2 - t3)
show("w2  sign of rho dropped: sqrt(110.25 + 2.25 + 6.3) (B)", "{:.4f} -> {:.2f}%".format(sig_plus, sig_plus))

# ------------------------------------------------------------------ story
print("\n=== Story: Austria's 2.1% bond due 20 September 2117 (annual coupons assumed) ===")
show("years to maturity at issue (2117 - 2017)", 2117 - 2017)
show("orders / size (11 / 3.5)", "{:.2f}x".format(11 / 3.5))
y_issue = 0.02112
p_issue = price(2.1, 100, y_issue)
md_issue = macdur(2.1, 100, y_issue)
mod_issue = md_issue / (1 + y_issue)
show("price at a 2.112% yield, 100 annual periods", "{:.4f}".format(p_issue))
show("MacDur at issue (years)", "{!r} -> {:.1f}".format(md_issue, md_issue))
show("ModDur at issue = 42.4091 / 1.02112", "{!r} -> {:.1f}".format(mod_issue, mod_issue))
show("ModDur using the rounded 42.4 / 1.02112", "{:.4f}".format(42.4 / 1.02112))
show("% price move per bp = ModDur x 0.01", "{:.4f}%".format(mod_issue * 0.01))
show("cents per 100 per bp at the issue price", "{:.4f}".format(mod_issue * 0.0001 * p_issue))
show("cents per 100 per bp at a price of 100", "{:.4f}".format(mod_issue * 0.0001 * 100))
show("Bloomberg's figure (cited, not computed)", "0.43 per 100, 0.43%")
# 2020: price 200 with about 97 years left
y200 = solve_yield(2.1, 97, 200.0)
md200 = macdur(2.1, 97, y200)
mod200 = md200 / (1 + y200)
show("2020: yield at a price of 200, 97 years left (%)", "{!r} -> {:.2f}".format(y200 * 100, y200 * 100))
show("2020: MacDur, ModDur at that yield", "{:.4f}, {:.4f} -> about {:.0f}".format(md200, mod200, mod200))
# April 2026: 58.75 with about 91 years left
y5875 = solve_yield(2.1, 91, 58.75)
show("Apr 2026: yield at 58.75, 91 years left (%)", "{!r} -> {:.2f}".format(y5875 * 100, y5875 * 100))
y5691 = solve_yield(2.1, 91, 56.91)
show("Jul 2026: yield at 56.91, 91 years left (%) (Borsa: about 3.8)", "{:.2f}".format(y5691 * 100))
dy = (y5875 - y200) * 100
show("yield rise, percentage points", "{:.4f}".format(dy))
pred = -mod200 * dy
show("duration-only estimate of the price change (%)", "{!r} -> {:.0f}%".format(pred, pred))
actual = (58.75 / 200 - 1) * 100
show("actual change 200 -> 58.75 (%)", "{!r} -> {:.0f}%".format(actual, actual))
show("change 200 -> 57 (%)", "{:.1f}%".format((57 / 200 - 1) * 100))
show("two-thirds as a percent", "{:.1f}%".format(200 / 3))
# Day 15's Disney century bond: 7.55% semiannual, priced at par, 200 half-years
md_dis_p = macdur(7.55 / 2, 200, 0.03775)
show("Disney (Day 15): price at 3.775% per half-year", "{:.4f}".format(price(7.55 / 2, 200, 0.03775)))
show("Disney: MacDur in half-years, in years", "{:.4f}, {:.4f} -> {:.1f}".format(md_dis_p, md_dis_p / 2, md_dis_p / 2))
show("Disney: perpetuity check (1 + y)/y half-years", "{:.4f}".format(1.03775 / 0.03775))

# ------------------------------------------------------------------ why it works
print("\n=== Why it works ===")
show("1/1.04: one year's extra bite per point of yield at r = 4% (%)", "{:.4f}".format(1 / 1.04))
show("exact: 1.04/1.05 - 1 (%)", "{:.4f}".format((1.04 / 1.05 - 1) * 100))
a = 105 / 1.04 ** 3
b = 105 / 1.05 ** 3
show("105 / 1.04^3", "{:.4f}".format(a))
show("105 / 1.05^3", "{:.4f}".format(b))
show("actual % change", "{:.4f} -> {:.2f}%".format((b / a - 1) * 100, (b / a - 1) * 100))
show("estimate 3 x 1/1.04 (%)", "{:.4f} -> {:.2f}%".format(3 / 1.04, 3 / 1.04))
s1 = 5 / 1.04
s2 = 5 / 1.04 ** 2
show("5/1.04 vs 5/1.05: % change (t = 1)", "{:.4f}%".format((5 / 1.05 / s1 - 1) * 100))
# horizon demo: walkthrough bond held for MacDur years, yield moves right after purchase
H = macdur(5, 3, 0.04)
for yy in (0.03, 0.04, 0.05):
    hv = price(5, 3, yy) * (1 + yy) ** H
    show("horizon value at t = {:.4f} if the yield is {:.0%}".format(H, yy), "{:.4f} -> {:.2f}".format(hv, hv))
for yy in (0.03, 0.04, 0.05):
    show("  price right after the move at {:.0%}".format(yy), "{:.4f}".format(price(5, 3, yy)))
# what raises duration (illustrations at 5%)
show("10-year 5% bond at 5%: MacDur", "{:.4f}".format(macdur(5, 10, 0.05)))
show("30-year 5% bond at 5%: MacDur", "{:.4f}".format(macdur(5, 30, 0.05)))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: 2-year 6% annual coupon, yield 5% ===")
pv1 = 6 / 1.05
pv2 = 106 / 1.05 ** 2
pw = pv1 + pv2
show("PV_1 = 6/1.05", "{:.4f}".format(pv1))
show("1.05^2", "{:.4f}".format(1.05 ** 2))
show("PV_2 = 106/1.05^2", "{:.4f}".format(pv2))
show("price", "{:.4f}".format(pw))
w_1, w_2 = pv1 / pw, pv2 / pw
show("weights", "{:.4f}, {:.4f} (sum {:.4f})".format(w_1, w_2, w_1 + w_2))
md_w = 1 * w_1 + 2 * w_2
show("MacDur = 1 x w1 + 2 x w2", "{!r} -> {:.4f}".format(md_w, md_w))
show("check with rounded weights 0.0561 + 2 x 0.9439", "{:.4f}".format(0.0561 + 2 * 0.9439))
mod_w = md_w / 1.05
show("ModDur = MacDur / 1.05", "{!r} -> {:.4f} -> {:.2f}%".format(mod_w, mod_w, mod_w))
show("check 1.9439 / 1.05", "{:.4f}".format(1.9439 / 1.05))

# ------------------------------------------------------------------ walkthrough
print("\n=== Walkthrough: 3-year 5% annual coupon, yield 4% ===")
cf = flows(5, 3)
tcf = [t * c for t, c in enumerate(cf, start=1)]
show("cash flows", cf)
show("t x CF", tcf)
for t, c in enumerate(cf, start=1):
    p = c / 1.04 ** t
    show("PV of {:g} at t = {}".format(c, t), "{} / TI {}".format(ba(p), ti(p)))
P = npv0(4, cf)
S = npv0(4, tcf)
show("price = NPV(4%, {5, 5, 105}) (STO 1)", "{!r} -> {} / TI {}".format(P, ba(P), ti(P)))
show("sum t x PV = NPV(4%, {5, 10, 315}) (STO 2)", "{!r} -> {} / TI {}".format(S, ba(S), ti(S)))
for t, c in enumerate(cf, start=1):
    p = c / 1.04 ** t
    show("  weight of t = {}".format(t), "{:.4f}".format(p / P))
MD = S / P
show("MacDur = RCL 2 / RCL 1", "{!r} -> {} / TI {}".format(MD, ba(MD), ti(MD)))
MOD = MD / 1.04
show("ModDur = MacDur / 1.04", "{!r} -> {} / TI {}".format(MOD, ba(MOD), ti(MOD)))
est = -MOD * 0.5
show("estimate for +50 bp: -ModDur x 0.5 (%)", "{!r} -> {} / TI {}".format(est, ba(est), ti(est)))
show("  as a decimal: -ModDur x 0.005", "{:.6f}".format(-MOD * 0.005))
P45 = npv0(4.5, cf)
show("reprice at 4.5%: PV (TVM CPT PV shows minus)", "{!r} -> {} / TI {}".format(P45, ba(-P45), ti(P45)))
ratio = P45 / P - 1
show("101.3745 / RCL 1 - 1", "{} / TI {}".format(ba(ratio), ti(ratio)))
exact = ratio * 100
show("exact % change", "{!r} -> {} / TI {}".format(exact, ba(exact), ti(exact)))
show("estimate in money per 100: -ModDur x 0.005 x price", "{:.4f}".format(-MOD * 0.005 * P))
show("story ratio: issue ModDur / walkthrough ModDur", "{:.1f}".format(mod_issue / MOD))
show("story ratio with rounded 41.5 / 2.75", "{:.1f}".format(41.5 / 2.75))

# ------------------------------------------------------------------ traps
print("\n=== Traps ===")
show("trap: MacDur x 0.5 used as the % change", "{:.4f}".format(MD * 0.5))
show("trap: units, ModDur x 50 (%)", "{:.4f}".format(MOD * 50))
show("trap: ModDur x 0.005 (decimal)", "{:.4f}".format(MOD * 0.005))
# semiannual illustration: MacDur 9 half-years, annual yield 5%
show("semiannual: right AnnModDur 9 / 1.025 / 2", "{:.4f}".format(9 / 1.025 / 2))
show("semiannual: wrong 4.5 / 1.05", "{:.4f}".format(4.5 / 1.05))
show("semiannual: not annualized 9 / 1.025", "{:.4f}".format(9 / 1.025))

# ------------------------------------------------------------------ check questions
print("\n=== Check questions ===")
# c1: 4-year 6% annual coupon at 5%
cf1 = flows(6, 4)
tcf1 = [t * c for t, c in enumerate(cf1, start=1)]
P1 = npv0(5, cf1)
S1 = npv0(5, tcf1)
c1 = S1 / P1
show("c1  cash flows, t x CF", "{} {}".format(cf1, tcf1))
show("c1  price NPV(5%)", "{!r} -> {} / TI {}".format(P1, ba(P1), ti(P1)))
show("c1  sum t x PV NPV(5%)", "{!r} -> {} / TI {}".format(S1, ba(S1), ti(S1)))
show("c1  MacDur (answer)", "{!r} -> {} / TI {}".format(c1, ba(c1), ti(c1)))
show("c1  ModDur (bonus)", "{:.4f}".format(c1 / 1.05))
show("c1  cross-check macdur()", "{:.10f}".format(macdur(6, 4, 0.05)))

# c2: three 10-year bonds at 5%
for cp in (8, 2, 0):
    show("c2  10-year {}% coupon at 5%: MacDur".format(cp), "{:.4f} -> {:.2f}".format(macdur(cp, 10, 0.05), macdur(cp, 10, 0.05)))

# c3: 2-year 4% semiannual at 6% (3% per half-year)
cf3 = flows(2, 4)
tcf3 = [t * c for t, c in enumerate(cf3, start=1)]
P3 = npv0(3, cf3)
S3 = npv0(3, tcf3)
md3 = S3 / P3
show("c3  cash flows, t x CF (half-years)", "{} {}".format(cf3, tcf3))
show("c3  price NPV(3%)", "{!r} -> {} / TI {}".format(P3, ba(P3), ti(P3)))
show("c3  sum t x PV NPV(3%)", "{!r} -> {} / TI {}".format(S3, ba(S3), ti(S3)))
show("c3  MacDur in half-years", "{!r} -> {} / TI {}".format(md3, ba(md3), ti(md3)))
show("c3  MacDur in years", "{:.4f}".format(md3 / 2))
c3 = md3 / 1.03 / 2
show("c3  AnnModDur = MacDur / 1.03 / 2 (answer)", "{!r} -> {} / TI {}".format(c3, ba(c3), ti(c3)))
show("c3  after / 1.03 only (not annualized)", "{} / TI {}".format(ba(md3 / 1.03), ti(md3 / 1.03)))
show("c3  trap: years / 1.06", "{:.4f}".format(md3 / 2 / 1.06))
show("c3  rounded path 3.8813 / 1.03 / 2", "{:.6f}".format(3.8813 / 1.03 / 2))
show("c3  rounded path 1.9407 / 1.03", "{:.6f}".format(1.9407 / 1.03))

# c4: MacDur 7.80, annual YTM 4%, +40 bp
mod4 = 7.80 / 1.04
show("c4  ModDur = 7.80 / 1.04", "{:.4f}".format(mod4))
show("c4  -ModDur x 0.40 (A)", "{:.4f}%".format(-mod4 * 0.40))
show("c4  -MacDur x 0.40 (B)", "{:.4f}%".format(-7.80 * 0.40))
show("c4  sign dropped (C)", "{:+.4f}%".format(mod4 * 0.40))

# c5: duration gap
show("c5  gap = 7 - 5", 7 - 5)
