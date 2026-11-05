#!/usr/bin/env python3
"""Day 17 of CFA Math: every number that appears on day-17.html.

Full price, flat price and accrued interest
(CFA Program 2027 Level I, Fixed Income, Fixed-Income Bond Valuation: Prices and Yields).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime as dt


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


def money(x):
    return "{:,.2f}".format(x)


def show(label, value):
    print("  {:<62} {}".format(label, value))


def actual(d1, d2):
    """Actual days between two dates (actual/actual)."""
    return (d2 - d1).days


def days_30_360(d1, d2):
    """30/360: every month 30 days, the year 360 (CFA formula)."""
    return 360 * (d2.year - d1.year) + 30 * (d2.month - d1.month) + (d2.day - d1.day)


def bond_pv(pmt, fv, r, n):
    """Price on a coupon date: n coupons of pmt left, then fv; r per period."""
    return sum(pmt / (1 + r) ** k for k in range(1, n + 1)) + fv / (1 + r) ** n


def full_flat(pv, r, t, T, pmt):
    full = pv * (1 + r) ** (t / T)
    ai = t / T * pmt
    return full, ai, full - ai


def irr_rate(price, pmt, fv, n):
    """Per-period yield by bisection (what CPT I/Y does)."""
    lo, hi = 1e-9, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if bond_pv(pmt, fv, mid, n) > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def npv(rate, cf0, flows):
    return cf0 + sum(cf / (1 + rate) ** t for t, cf in enumerate(flows, start=1))


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 16): 12-year 4.5% semiannual bond at 95.40 -> YTM, semiannual-bond basis
w1_n, w1_pmt, w1_price = 24, 2.25, 95.40
w1_r = irr_rate(w1_price, w1_pmt, 100, w1_n)
w1 = 200 * w1_r
show("w1  N, PMT, PV", "{}, {}, -{}".format(w1_n, w1_pmt, w1_price))
show("w1  CPT I/Y (semiannual %)", "{!r} -> I/Y = {:.4f}".format(100 * w1_r, 100 * w1_r))
show("w1  x 2 = YTM, semiannual-bond basis (answer, %)", "{!r} -> {:.4f} -> {:.2f}%".format(w1, w1, w1))
show("w1  check: price at that yield", "{:.4f}".format(bond_pv(w1_pmt, 100, w1_r, w1_n)))
show("w1  wrong: not doubled", "{:.2f}%".format(100 * w1_r))
show("w1  current yield 4.5/95.40 (for comparison)", "{:.4f}%".format(100 * 4.5 / 95.40))

# w2 (Day 10): CF0 = -1,200; 400, 400, 400, 300; NPV at 9%
w2_flows = [400, 400, 400, 300]
w2 = npv(0.09, -1200, w2_flows)
w2_noF = npv(0.09, -1200, [400, 300])          # F01 left at 1
w2_noCF0 = npv(0.09, 0, w2_flows)              # forgot CF0
show("w2  NPV at 9% (correct, B)", "{!r} -> {}".format(w2, money(w2)))
show("w2  F01 left at 1: 400 in yr 1, 300 in yr 2 (C)", money(w2_noF))
show("w2  forgot CF0: PV of the inflows (A)", money(w2_noCF0))
show("w2  BA display NPV", "NPV = {}".format(ba(w2)))
show("w2  TI npv(9,-1200,{400,300},{3,1})", ti(w2))
show("w1  TI I% display / BA I/Y display", "I%={} / I/Y = {}".format(ti(100 * w1_r), ba(100 * w1_r)))

# ------------------------------------------------------------------ story
print("\n=== Story: Greece's PSI, March 2012 ===")
new_greek, efsf = 31.50, 15.00
new_total = new_greek + efsf
show("per 100 of face: new Greek bonds, EFSF notes", "{:.2f}, {:.2f}".format(new_greek, efsf))
show("new Greek bonds + EFSF notes per 100 of face", "{:.2f}".format(new_total))
show("cut in face value", "{:.1f}%".format(100 - new_total))
show("participating face value (Lawyer Monthly), almost 200bn", "199.2 bn < 200: {}".format(199.2 < 200))
show("15% and 31.5% of 199.2 bn (for reference only)", "{:.1f} bn, {:.1f} bn".format(0.15 * 199.2, 0.315 * 199.2))

# Illustration: EUR 100,000 of a 4.5% annual-coupon bond, last coupon 20 Aug 2011
last, cut, nxt = dt.date(2011, 8, 20), dt.date(2012, 2, 24), dt.date(2012, 8, 20)
t_g, T_g = actual(last, cut), actual(last, nxt)
coupon_g = 100000 * 0.045
ai_g = coupon_g * t_g / T_g
show("t: 20 Aug 2011 -> 24 Feb 2012 (actual)", t_g)
show("T: 20 Aug 2011 -> 20 Aug 2012 (actual, includes 29 Feb)", T_g)
show("annual coupon on 100,000 at 4.5%", money(coupon_g))
show("AI = 4,500 x 188/366 (EUR)", "{!r} -> {}".format(ai_g, money(ai_g)))
show("share of the coupon earned, t/T", "{:.4f} -> {:.1f}%".format(t_g / T_g, 100 * t_g / T_g))

# ------------------------------------------------------------------ why it works
print("\n=== Why it works ===")
# Walkthrough bond used for the sawtooth: 10 coupons left at 2%, PMT 2.5
pv10 = bond_pv(2.5, 100, 0.02, 10)
pv9 = bond_pv(2.5, 100, 0.02, 9)
just_before = pv10 * 1.02
show("PV on 15 Feb 2027 (10 left)", ba(pv10))
show("full price the day before 15 Aug (t/T -> 1): PV x 1.02", ba(just_before))
show("AI just before = whole coupon", "2.5000")
show("flat just before = full - 2.5", ba(just_before - 2.5))
show("full price just after the coupon = PV with 9 left", ba(pv9))
show("flat just after (AI = 0)", ba(pv9))
show("drop in full price on coupon day", ba(just_before - pv9))
# check the step-3 identity on the walkthrough bond: discount each flow from settlement
tT = 71 / 181
direct = sum(2.5 / 1.02 ** (k - tT) for k in range(1, 11)) + 100 / 1.02 ** (10 - tT)
show("identity: sum CF/(1+r)^(k - t/T) at settlement", ba(direct))
show("          PV x (1+r)^(t/T)", ba(pv10 * 1.02 ** tT))
# par bond: 4% coupon, 4% yield, semiannual, halfway through the period
par_full = 100 * 1.02 ** 0.5
show("par bond halfway: full = 100 x 1.02^0.5", ba(par_full))
show("par bond halfway: AI = 0.5 x 2", "1.0000")
show("par bond halfway: flat", ba(par_full - 1))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: 6% corporate, 30/360, settles 2 Jun 2027, 5% yield ===")
we_last, we_set, we_next = dt.date(2027, 3, 15), dt.date(2027, 6, 2), dt.date(2027, 9, 15)
we_t = days_30_360(we_last, we_set)
we_T = days_30_360(we_last, we_next)
show("t (30/360): (6-3) x 30 + (2-15)", we_t)
show("T (30/360)", we_T)
show("actual days 15 Mar -> 2 Jun (for comparison)", actual(we_last, we_set))
c1_ = 3 / 1.025
c2_ = 103 / 1.025 ** 2
we_pv = c1_ + c2_
show("3/1.025", "{:.4f}".format(c1_))
show("103/1.025^2", "{:.4f}".format(c2_))
show("PV at last coupon", "{:.4f}  (TVM check {:.4f})".format(we_pv, bond_pv(3, 100, 0.025, 2)))
growth = 1.025 ** (we_t / we_T)
show("1.025^(77/180)", "{:.6f}".format(growth))
we_full, we_ai, we_flat = full_flat(we_pv, 0.025, we_t, we_T, 3)
show("full", "{!r} -> {:.4f}".format(we_full, we_full))
show("AI = 77/180 x 3", "{!r} -> {:.4f}".format(we_ai, we_ai))
show("flat", "{!r} -> {:.4f}".format(we_flat, we_flat))
show("flat from rounded: 102.0358 - 1.2833", "{:.4f}".format(102.0358 - 1.2833))
# with actual days, for the sidenote
wa_t = actual(we_last, we_set)
wa_T = actual(we_last, we_next)
wa_full, wa_ai, wa_flat = full_flat(we_pv, 0.025, wa_t, wa_T, 3)
show("if actual/actual were used: t, T", "{}, {}".format(wa_t, wa_T))
show("  full, AI, flat", "{:.4f}, {:.4f}, {:.4f}".format(wa_full, wa_ai, wa_flat))

# ------------------------------------------------------------------ walkthrough
print("\n=== Calculator walkthrough: 5% government bond, settles 27 Apr 2027, 4% yield ===")
wt_last, wt_set, wt_next, wt_mat = dt.date(2027, 2, 15), dt.date(2027, 4, 27), dt.date(2027, 8, 15), dt.date(2032, 2, 15)
wt_t = actual(wt_last, wt_set)
wt_T = actual(wt_last, wt_next)
show("DBD 2.1527 -> 4.2727 (t)", "{} -> BA DBD = {} / TI {}".format(wt_t, ba(wt_t), ti(wt_t)))
show("DBD 2.1527 -> 8.1527 (T)", "{} -> BA DBD = {} / TI {}".format(wt_T, ba(wt_T), ti(wt_T)))
# coupons left after 15 Feb 2027
n_left = (wt_mat.year - wt_last.year) * 2
show("coupons left after 15 Feb 2027 (N)", n_left)
wt_pv = bond_pv(2.5, 100, 0.02, n_left)
show("CPT PV", "{!r} -> BA PV = -{} / TI PV=-{}".format(wt_pv, ba(wt_pv), ti(wt_pv)))
gf = 1.02 ** (wt_t / wt_T)
show("71/181", "{:.6f}".format(wt_t / wt_T))
show("1.02^(71/181)", "{:.6f}".format(gf))
wt_full, wt_ai, wt_flat = full_flat(wt_pv, 0.02, wt_t, wt_T, 2.5)
show("full (exact PV)", "{!r} -> {}".format(wt_full, ba(wt_full)))
full_typed = 104.4913 * gf
show("full from typed 104.4913 (what the keystrokes give)", "{!r} -> {}".format(full_typed, ba(full_typed)))
show("AI = 71/181 x 2.5", "{!r} -> BA {} / TI {}".format(wt_ai, ba(wt_ai), ti(wt_ai)))
show("flat (exact)", "{!r} -> {}".format(wt_flat, ba(wt_flat)))
show("flat from typed PV (RCL 1 - RCL 2)", "{!r} -> {}".format(full_typed - wt_ai, ba(full_typed - wt_ai)))
show("BOND inputs: SDT, CPN, RDT, RV, YLD", "4.2727 (27 Apr 2027), 5, 2.1532 (15 Feb 2032), 100, 4")
# BA II Plus guidebook bond formula (more than one coupon period to redemption):
# PRI = RV/(1+Y/M)^(N-1+DSC/E) + sum_{K=1..N} (CPN/M)/(1+Y/M)^(K-1+DSC/E) - (CPN/M)(A/E)
dsc, E, A, M = actual(wt_set, wt_next), wt_T, wt_t, 2
pri = 100 / (1 + 0.04 / M) ** (n_left - 1 + dsc / E) + sum(
    (5 / M) / (1 + 0.04 / M) ** (k - 1 + dsc / E) for k in range(1, n_left + 1)) - (5 / M) * (A / E)
show("BA guidebook formula: DSC, E, A", "{}, {}, {}".format(dsc, E, A))
show("BA guidebook formula PRI", "{!r} -> PRI = {}".format(pri, ba(pri)))
show("BOND worksheet PRI (street formula, same thing)", ba(wt_flat))
show("BOND worksheet AI", ba(wt_ai))
show("PRI + AI (unrounded)", ba(wt_flat + wt_ai))
show("PRI + AI from the rounded displays", "{:.4f}".format(round(wt_flat, 4) + round(wt_ai, 4)))
show("full - PV at last coupon (value grown since 15 Feb)", ba(wt_full - wt_pv))

# ------------------------------------------------------------------ back to the story
print("\n=== Back to the story ===")
show("AI per 100 of face on the illustrative bond", "{:.4f}".format(4.5 * t_g / T_g))
show("AI if the coupon were paid in full (t/T = 1)", money(coupon_g))

# ------------------------------------------------------------------ check questions
print("\n=== c1: 6% semiannual government bond, 5% yield, 8 coupons left, 75 of 184 days ===")
c1_pv = bond_pv(3, 100, 0.025, 8)
c1_full, c1_ai, c1_flat = full_flat(c1_pv, 0.025, 75, 184, 3)
show("CPT PV", "{!r} -> BA PV = -{} / TI PV=-{}".format(c1_pv, ba(c1_pv), ti(c1_pv)))
show("1.025^(75/184)", "{:.6f}".format(1.025 ** (75 / 184)))
c1_full_typed = 103.5851 * 1.025 ** (75 / 184)
show("full (exact)", "{!r} -> {}".format(c1_full, ba(c1_full)))
show("full from typed 103.5851", "{!r} -> {}".format(c1_full_typed, ba(c1_full_typed)))
show("AI = 75/184 x 3", "{!r} -> {}".format(c1_ai, ba(c1_ai)))
show("flat (answer)", "{!r} -> {}".format(c1_flat, ba(c1_flat)))
show("flat from typed PV", ba(c1_full_typed - c1_ai))
show("mistake: answer the full price", ba(c1_full))
show("mistake: answer PV at last coupon", ba(c1_pv))
show("tolerance check |103.41 - flat|", "{:.4f}".format(abs(103.41 - c1_flat)))
show("rounded-steps check: 104.63 - 1.22", "{:.4f} -> diff {:.4f}".format(104.63 - 1.22, abs(104.63 - 1.22 - c1_flat)))
show("c1 displays: AI TI / flat TI", "{} / {}".format(ti(c1_ai), ti(c1_flat)))

print("\n=== c2: no numbers ===")

print("\n=== c3: 4% semiannual corporate, 97.25 flat, 1 May -> 19 Jul, 30/360 ===")
c3_t = days_30_360(dt.date(2027, 5, 1), dt.date(2027, 7, 19))
c3_ai = c3_t / 180 * 2
c3 = 97.25 + c3_ai
show("t (30/360): (7-5) x 30 + (19-1)", c3_t)
show("AI = 78/180 x 2", "{!r} -> {:.4f}".format(c3_ai, c3_ai))
show("full = 97.25 + AI (answer)", "{!r} -> {:.4f}".format(c3, c3))
show("from rounded AI 0.8667", "{:.4f}".format(97.25 + 0.8667))
C3_TOL = 0.0005   # asked to four decimals; covers t/T or AI rounded to 4 dp
show("data-tol", C3_TOL)
for label, v in (("typed 98.1167", 98.1167), ("t/T rounded 0.4333 -> 97.25 + 0.8666", 97.25 + 0.8666),
                 ("actual/actual 98.1087", 97.25 + 79 / 184 * 2), ("T = 181 by mistake", 97.25 + 78 / 181 * 2),
                 ("AI rounded to 0.87 (not 4 dp)", 97.25 + 0.87)):
    show("  tol check: " + label, "{:.4f} diff {:.4f} -> {}".format(
        v, abs(v - c3), "accepted" if abs(v - c3) <= C3_TOL else "rejected"))
c3_act_t = actual(dt.date(2027, 5, 1), dt.date(2027, 7, 19))
c3_act_T = actual(dt.date(2027, 5, 1), dt.date(2027, 11, 1))
c3_act = 97.25 + c3_act_t / c3_act_T * 2
show("mistake: actual/actual t, T", "{}, {}".format(c3_act_t, c3_act_T))
show("mistake: actual/actual full price", "{:.4f}  (off by {:.4f})".format(c3_act, c3 - c3_act))
show("mistake: flat - AI", "{:.4f}".format(97.25 - c3_ai))
show("mistake: annual coupon not halved, 78/180 x 4", "{:.4f}".format(97.25 + c3_t / 180 * 4))

print("\n=== c4: 4% semiannual government bond, 1 Mar -> 15 May, next coupon 1 Sep ===")
for yr in (2026, 2027, 2028):
    a = dt.date(yr, 3, 1)
    show("year {}: actual t, T".format(yr), "{}, {}".format(actual(a, dt.date(yr, 5, 15)), actual(a, dt.date(yr, 9, 1))))
c4_t = actual(dt.date(2027, 3, 1), dt.date(2027, 5, 15))
c4_T = actual(dt.date(2027, 3, 1), dt.date(2027, 9, 1))
c4_A = c4_t / c4_T * 2
c4_B = days_30_360(dt.date(2027, 3, 1), dt.date(2027, 5, 15)) / 180 * 2
c4_C = c4_t / c4_T * 4
show("A (correct) 75/184 x 2", "{!r} -> {:.4f}".format(c4_A, c4_A))
show("B 30/360: 74/180 x 2", "{} days -> {:.4f}".format(days_30_360(dt.date(2027, 3, 1), dt.date(2027, 5, 15)), c4_B))
show("C coupon not halved: 75/184 x 4", "{:.4f}".format(c4_C))
show("DATE 3.0127 -> 5.1527 / dbd(3.0127,5.1527)", "BA DBD = {} / TI {}".format(ba(c4_t), ti(c4_t)))
show("DATE 3.0127 -> 9.0127 / dbd(3.0127,9.0127)", "BA DBD = {} / TI {}".format(ba(c4_T), ti(c4_T)))
show("75/184 x 2 display", "BA {} / TI {}".format(ba(c4_A), ti(c4_A)))

print("\n=== Extra numbers in the prose ===")
show("Idea: walkthrough AI as a share of the coupon (71/181)", "{:.1f}%".format(100 * wt_t / wt_T))
show("full price vs PV at last coupon: growth factor - 1", "{:.4f}%".format(100 * (gf - 1)))
