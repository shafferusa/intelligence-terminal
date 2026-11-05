#!/usr/bin/env python3
"""Day 1 - Compounding: how money grows (CFA Math, Level I, LM4 The Time Value of Money in Finance).

Computes every number that appears on curriculum/cfa-l1/day-01.html and prints it.
Plain Python 3, standard library only.
"""


def ba(x):
    """BA II Plus display, 4 decimals, thousands separators."""
    return "{:,.4f}".format(x)


def ti(x):
    """TI-84 Plus display, FLOAT 4, no separators."""
    return "{:.4f}".format(x)


def money(x):
    return "{:,.2f}".format(x)


def fv(pv, r, n):
    return pv * (1 + r) ** n


def show(label, value, fmt=None):
    print("  %-58s %s" % (label, fmt(value) if fmt else value))


# ---------------------------------------------------------------- story
print("STORY: Franklin's 1,000 pounds at 5% a year")
PV, R, N = 1000.0, 0.05, 100
simple_year = PV * R
simple_100 = PV * R * N
simple_total = PV + simple_100
franklin_100 = 131000.0
franklin_200 = 4061000.0
gap = franklin_100 - PV - simple_100
show("5% of 1,000 (one year's simple interest)", simple_year, money)
show("simple interest over 100 years", simple_100, money)
show("simple-interest total after 100 years", simple_total, money)
show("Franklin's 131,000 minus 1,000 minus 5,000 (the puzzle gap)", gap, money)
show("public works + relent (100,000 + 31,000)", 100000 + 31000, money)

g100 = (1 + R) ** N
fv100 = PV * g100
show("growth factor 1.05^100 (full)", repr(g100))
show("growth factor 1.05^100 (4 dp)", g100, lambda v: "%.4f" % v)
show("growth factor 1.05^100 (2 dp)", g100, lambda v: "%.2f" % v)
show("FV = 1,000 x 1.05^100 (full)", repr(fv100))
show("FV (cents)", fv100, money)
show("FV minus Franklin's 131,000", fv100 - franklin_100, money)
show("31,000 x 131 (Franklin's second century)", 31000 * 131, money)
show("31,000 x 1.05^100 (unrounded factor)", 31000 * g100, money)
show("31,000 x 1.05^100, nearest pound", round(31000 * g100), lambda v: "{:,}".format(v))
show("interest on interest over the century (FV - 6,000)", fv100 - simple_total, money)
show("compound interest total over the century (FV - 1,000)", fv100 - PV, money)

# year by year
bal = PV
print("  year-by-year balances at 5%:")
for y in range(1, 4):
    interest = bal * R
    bal = bal + interest
    print("    year %d: interest %s, balance %s (exact %r)" % (y, money(interest), money(bal), bal))
year100_interest = PV * (1 + R) ** 99 * R
show("interest earned in year 100 alone = 1,000 x 1.05^99 x 0.05", year100_interest, money)
show("1.05^14 (about one doubling)", (1 + R) ** 14, lambda v: "%.4f" % v)
show("1.05^15", (1 + R) ** 15, lambda v: "%.4f" % v)
show("2^7 (seven doublings)", 2 ** 7)
show("doubling sequence 2^0 .. 2^7", ", ".join(str(2 ** k) for k in range(8)))
show("years per doubling at 5% (ln 2 / ln 1.05)", __import__("math").log(2) / __import__("math").log(1 + R),
     lambda v: "%.4f" % v)

# (1 + r)^2 = 1 + 2r + r^2 for Franklin
print("  two years, multiplied out: 1,000 x (1 + 2r + r^2)")
show("1,000 x 1", PV * 1, money)
show("1,000 x 2r (two years of simple interest)", PV * 2 * R, money)
show("1,000 x r^2 (interest on the first year's interest)", PV * R ** 2, money)
show("total = 1,000 x 1.05^2", PV * (1 + R) ** 2, money)
show("1.05^2", (1 + R) ** 2, lambda v: "%.4f" % v)

# a lost point over 100 years
g4 = 1.04 ** 100
g3 = 1.03 ** 100
show("1.04^100", g4, lambda v: "%.4f (%.2f)" % (v, v))
show("1.03^100", g3, lambda v: "%.4f (%.2f)" % (v, v))
show("1.04^100 / 1.05^100", g4 / g100, lambda v: "%.4f" % v)
show("1.03^100 / 1.05^100", g3 / g100, lambda v: "%.4f" % v)
show("one point less leaves (percent of the 5% result)", 100 * g4 / g100, lambda v: "%.2f%% (about %d%%)" % (v, round(v)))
show("two points less leaves (percent; page says 'under 15%')", 100 * g3 / g100, lambda v: "%.2f%%" % v)
show("1,000 x 1.04^100", PV * g4, money)
show("1,000 x 1.03^100", PV * g3, money)

# ---------------------------------------------------------------- calculator walkthrough
print("\nSETUP DISPLAYS")
show("BA after 2ND FORMAT 4 ENTER", 4, lambda v: "DEC = " + ba(v))
show("BA after 2ND P/Y 1 ENTER", 1, lambda v: "P/Y = " + ba(v))

print("\nWALKTHROUGH: N=100, I/Y=5, PV=-1000, PMT=0, P/Y=C/Y=1, END")
pv_wt = -1000.0
fv_wt = -pv_wt * g100           # sign rule: pay out -1,000, receive +FV
show("BA display after 1000 +|- PV", pv_wt, lambda v: "PV = " + ba(v))
show("BA display CPT FV", fv_wt, lambda v: "FV = " + ba(v))
show("TI display FV", fv_wt, lambda v: "FV=" + ti(v))
show("BA power key 1.05 y^x 100 =", g100, ba)
show("BA then x 1000 =", g100 * 1000, ba)
show("TI home 1.05^100", g100, ti)
show("TI home Ans*1000", g100 * 1000, ti)
show("answer to the cent", fv_wt, money)
show("if PV entered as +1000, CPT FV shows", -fv_wt, lambda v: "FV = " + ba(v))

# ---------------------------------------------------------------- traps (Franklin's numbers)
print("\nTRAPS on Franklin's problem")
py12 = PV * (1 + R / 12) ** 100
show("P/Y left at 12: 1,000 x (1 + 0.05/12)^100", py12, money)
show("  BA display", py12, lambda v: "FV = " + ba(v))
typed005 = PV * (1 + 0.0005) ** 100
show("typed 0.05 in I/Y: 1,000 x 1.0005^100", typed005, money)
show("simple interest instead", simple_total, money)

# ---------------------------------------------------------------- worked example
print("\nWORKED EXAMPLE: $2,000 at 6% for 3 years")
pv, r, n = 2000.0, 0.06, 3
b = pv
prev_interest = []
for y in range(1, n + 1):
    i = b * r
    print("  year %d: interest = %s x 0.06 = %s (exact %r); balance = %s (exact %r)"
          % (y, money(b), money(i), i, money(b + i), b + i))
    b += i
show("1.06^3", (1 + r) ** n, lambda v: "%.6f" % v)
show("FV = 2,000 x 1.06^3", fv(pv, r, n), money)
simple = pv + pv * r * n
show("simple interest total 2,000 + 2,000 x 0.06 x 3", simple, money)
show("simple interest earned", pv * r * n, money)
show("interest on interest = FV - simple total", fv(pv, r, n) - simple, money)
ioi_y2 = (pv * r) * r                       # year 1's interest earning in year 2
ioi_y3 = (fv(pv, r, 2) - pv) * r            # years 1-2 interest earning in year 3
show("year 2: first year's 120.00 earns 6%", ioi_y2, money)
show("year 3: earlier interest (247.20) earns 6%", ioi_y3, money)
show("earlier interest after 2 years", fv(pv, r, 2) - pv, money)
show("sum of the two", ioi_y2 + ioi_y3, money)

# ---------------------------------------------------------------- check questions
print("\nCHECK QUESTIONS")
c1 = fv(2500, 0.045, 40)
print("c1: EUR 2,500 at 4.5% for 40 years")
show("FV (full)", repr(c1))
show("FV (cents)", c1, money)
show("BA display", c1, lambda v: "FV = " + ba(v))
show("TI display", c1, lambda v: "FV=" + ti(v))
show("1.045^40", 1.045 ** 40, lambda v: "%.4f" % v)
show("1.045^40 (6 dp)", 1.045 ** 40, lambda v: "%.6f" % v)
show("2,500 x 5.816365 (6-dp factor)", 2500 * 5.816365, money)
show("2,500 x 5.8164 (4-dp factor, the rounding trap)", 2500 * 5.8164, money)
show("cents lost by rounding the factor", 2500 * 5.8164 - c1, money)

print("c2: $1,000 at 6%, 10 years vs 20 years (interest earned)")
f10 = fv(1000, 0.06, 10)
f20 = fv(1000, 0.06, 20)
show("1.06^10", 1.06 ** 10, lambda v: "%.4f" % v)
show("1.06^20", 1.06 ** 20, lambda v: "%.4f" % v)
show("(1.06^10)^2", (1.06 ** 10) ** 2, lambda v: "%.4f" % v)
show("1.06^10 (6 dp)", 1.06 ** 10, lambda v: "%.6f" % v)
show("1.06^20 = (1.06^10)^2 (6 dp)", (1.06 ** 10) ** 2, lambda v: "%.6f" % v)
show("1,000 x 1.790848 / 1,000 x 3.207135 (6-dp factors)", (1000 * 1.790848, 1000 * 3.207135),
     lambda v: "%s / %s" % (money(v[0]), money(v[1])))
show("FV after 10 years", f10, money)
show("FV after 20 years", f20, money)
show("2 x FV after 10 years (FV itself does NOT more than double)", 2 * f10, money)
show("interest earned, 10 years", f10 - 1000, money)
show("interest earned, 20 years", f20 - 1000, money)
show("ratio interest 20y / 10y", (f20 - 1000) / (f10 - 1000), lambda v: "%.4f" % v)
show("interest earned in the second decade", f20 - f10, money)
show("simple interest, 10 years", 1000 * 0.06 * 10, money)
show("simple interest, 20 years", 1000 * 0.06 * 20, money)
show("from the rounded figures: 3,207.14 - 1,790.85", 3207.14 - 1790.85, money)

print("c3: $10,000 at 8% for 10 years, interest on interest")
c3_fv = fv(10000, 0.08, 10)
c3_simple = 10000 * 0.08 * 10
c3 = c3_fv - 10000 - c3_simple
show("FV (full)", repr(c3_fv))
show("FV (cents)", c3_fv, money)
show("BA display", c3_fv, lambda v: "FV = " + ba(v))
show("TI display", c3_fv, lambda v: "FV=" + ti(v))
show("total interest FV - PV", c3_fv - 10000, money)
show("simple interest 10,000 x 0.08 x 10", c3_simple, money)
show("interest on interest (full)", repr(c3))
show("interest on interest (cents)", c3, money)
show("from the rounded figures: 11,589.25 - 8,000.00", 11589.25 - 8000.00, money)
show("share of all interest that is interest on interest", c3 / (c3_fv - 10000), lambda v: "%.4f" % v)

print("c4: $15,000 at 6% compounded annually for 8 years")
c4 = fv(15000, 0.06, 8)
c4_simple = 15000 * (1 + 0.06 * 8)
c4_py12 = 15000 * (1 + 0.06 / 12) ** 8
show("correct FV", c4, money)
show("  BA display", c4, lambda v: "FV = " + ba(v))
show("  TI display", c4, lambda v: "FV=" + ti(v))
show("distractor: simple interest 15,000 x (1 + 0.06 x 8)", c4_simple, money)
show("distractor: P/Y = 12 -> 8 periods at 0.5%", c4_py12, money)
show("1.06^8", 1.06 ** 8, lambda v: "%.6f" % v)
show("15,000 x 1.593848 (rounded factor)", 15000 * 1.593848, money)

# ---------------------------------------------------------------- grading self-test (mirrors cfa.js numberOk)
print("\nGRADING SELF-TEST (data-answer / data-tol as on the page)")


def graded_ok(answer, tol, typed, signless=False, pct=False):
    cands = [typed]
    if signless:
        cands, answer = [abs(typed)], abs(answer)
    if pct:
        cands.append(cands[0] * 100)
    return any(abs(c - answer) <= tol + 1e-12 for c in cands)


c1_wrong = {
    "rounded factor 2,500 x 5.8164": 2500 * 5.8164,
    "simple interest 2,500 x (1 + 0.045 x 40)": 2500 * (1 + 0.045 * 40),
    "P/Y = 12: 2,500 x (1 + 0.045/12)^40": 2500 * (1 + 0.045 / 12) ** 40,
    "interest only (FV - PV)": c1 - 2500,
}
c3_wrong = {
    "all the interest (FV - PV)": c3_fv - 10000,
    "the FV itself": c3_fv,
    "simple interest only": c3_simple,
}
for label, ans, typed, want in (
    [("c1 typed 14540.91", c1, 14540.91, True), ("c1 typed -14540.91 (signless)", c1, -14540.91, True)]
    + [("c1 wrong: %s = %s" % (k, money(v)), c1, round(v, 2), False) for k, v in c1_wrong.items()]
    + [("c3 typed 3589.25", c3, 3589.25, True)]
    + [("c3 wrong: %s = %s" % (k, money(v)), c3, round(v, 2), False) for k, v in c3_wrong.items()]
):
    got = graded_ok(ans, 0.01, typed, signless=True)
    print("  %-66s %s%s" % (label, "accepted" if got else "rejected", "" if got == want else "   <-- UNEXPECTED"))

# ---------------------------------------------------------------- checks on hand arithmetic quoted on the page
print("\nHAND ARITHMETIC QUOTED ON THE PAGE")
show("1,000 x 131.5013 (rounded factor)", 1000 * 131.5013, money)
show("2,000 x 1.191016 (rounded factor)", 2000 * 1.191016, money)
show("2,382.03 - 2,360.00", 2382.03 - 2360.00, money)
show("7.20 + 14.83", 7.20 + 14.83, money)
show("131,501.26 - 6,000.00", 131501.26 - 6000.00, money)
show("2,500 x 5.8164 (rounded factor, sanity check only)", 2500 * 5.8164, money)
