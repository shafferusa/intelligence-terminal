#!/usr/bin/env python3
"""Day 37 of CFA Math: every number that appears on day-37.html.

Forward rates and par rates
(CFA Program 2027 Level I, Fixed Income, The Term Structure of Interest Rates:
Spot, Par, and Forward Curves; building on Day 36's spot rates, discount
factors and bootstrapping).
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
    print("  {:<74} {}".format(label, value))


def fwd(z_a, a, z_b, b):
    """Implied forward rate IFR_(A, B-A), annual, decimal:
    (1 + Z_A)^A x (1 + IFR)^(B-A) = (1 + Z_B)^B."""
    return ((1 + z_b) ** b / (1 + z_a) ** a) ** (1 / (b - a)) - 1


def df(z, t):
    """Discount factor DF_t = 1 / (1 + Z_t)^t."""
    return 1 / (1 + z) ** t


def par_rate(dfs):
    """Par rate for N = len(dfs) years: (1 - DF_N) / (DF_1 + ... + DF_N)."""
    return (1 - dfs[-1]) / sum(dfs)


def pv_at(cfs, rates):
    """Cash flow t (t = 1, 2, ...) discounted at its own spot rate (decimal)."""
    return sum(cf / (1 + r) ** t for t, (cf, r) in enumerate(zip(cfs, rates), start=1))


def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 36): Z_1 = 3%, 2-year par yield 4% (annual coupons): bootstrap Z_2
w_df1 = 1 / 1.03
show("w1  DF_1 = 1/1.03", "{:.6f} -> BA {} / TI {}".format(w_df1, ba(w_df1), ti(w_df1)))
show("w1  4 x DF_1 = 4/1.03", "{:.6f} -> BA {}".format(4 * w_df1, ba(4 * w_df1)))
w_num = 100 - 4 * w_df1
show("w1  100 - 4/1.03", "{:.6f} -> BA {} / TI {}".format(w_num, ba(w_num), ti(w_num)))
w_df2 = w_num / 104
show("w1  DF_2 = (100 - 4/1.03)/104", "{:.6f} -> BA {} / TI {}".format(w_df2, ba(w_df2), ti(w_df2)))
show("w1  1/DF_2 = (1 + Z_2)^2", "{:.6f} -> BA {}".format(1 / w_df2, ba(1 / w_df2)))
show("w1  (1/DF_2)^0.5", "{:.6f} -> BA {}".format((1 / w_df2) ** 0.5, ba((1 / w_df2) ** 0.5)))
w_z2 = (1 / w_df2) ** 0.5 - 1
show("w1  Z_2 - before x 100", "BA {} / TI {}".format(ba(w_z2), ti(w_z2)))
show("w1  Z_2 (answer, %)", "{!r} -> BA {} / TI {} -> {:.2f}%".format(100 * w_z2, ba(100 * w_z2), ti(100 * w_z2), 100 * w_z2))
show("w1  check: 4/1.03 + 104/(1 + Z_2)^2", "{:.4f}".format(4 / 1.03 + 104 / (1 + w_z2) ** 2))
show("w1  trap: par yield taken as the spot rate", "4.00 (off by {:.2f} bp)".format((100 * w_z2 - 4) * 100))

# w2 (Day 30): mean 6%, SD 10%: P(return < -13.6%)
z_w2 = (-13.6 - 6) / 10
p_w2 = norm_cdf(z_w2)
show("w2  z = (-13.6 - 6)/10", "{:.4f}".format(z_w2))
show("w2  P(Z < -1.96) (B, right)", "{:.6f} -> {:.2f}%".format(p_w2, 100 * p_w2))
show("w2  A: both tails, 1 - 0.95", "{:.2f}%".format(100 * (1 - 0.95)))
show("w2  C: table area from 0 to 1.96", "{:.4f} -> {:.2f}%".format(norm_cdf(1.96) - 0.5, 100 * (norm_cdf(1.96) - 0.5)))
show("w2  95% band 6 +/- 1.96 x 10", "{:.2f} to {:.2f}".format(6 - 19.6, 6 + 19.6))

# ------------------------------------------------------------------ story
print("\n=== Story: 22 March 2019 ===")
show("3-month minus 10-year (points, bp): 2.46 - 2.44", "{:.2f} -> {:.0f} bp".format(2.46 - 2.44, (2.46 - 2.44) * 100))
story_fwd = (1.0231 ** 2 / 1.0245 - 1) * 100
show("1y1y from 1-year 2.45%, 2-year 2.31%: 1.0231^2/1.0245 - 1 (%)", "{!r} -> {:.4f} -> about {:.2f}%".format(story_fwd, story_fwd, story_fwd))
show("  1.0231^2", "{:.6f}".format(1.0231 ** 2))
show("  shortcut 2 x 2.31 - 2.45", "{:.2f}".format(2 * 2.31 - 2.45))
show("  Fed target range on 22 March 2019 (%)", "2.25 to 2.50")
show("  below the bottom of the range by (bp)", "{:.0f}".format((2.25 - story_fwd) * 100))
cuts = [2.50, 2.25, 2.00, 1.75]
show("  three 2019 cuts, upper bound 2.50 -> 2.25 -> 2.00 -> 1.75: total (bp)", "{:.0f}".format((cuts[0] - cuts[-1]) * 100))
show("  31 July range after the first cut", "2.00 to 2.25")
show("  30 October range after the third cut", "1.50 to 1.75")

# ------------------------------------------------------------------ the idea / why it works
print("\n=== Why it works (walkthrough curve Z_1 = 2.5%, Z_2 = 3.0%) ===")
d1, d2 = df(0.025, 1), df(0.03, 2)
show("Road 1: $1 at Z_2 for two years: 1.03^2", "{:.6f}".format(1.03 ** 2))
show("Road 2 first year: 1.025", "1.025")
show("replication: sell a 1-year zero, receive DF_1 today", "{:.6f} -> {:.4f}".format(d1, d1))
show("  buy 2-year zeros: DF_1 / DF_2 of them", "{:.6f} -> {:.4f}".format(d1 / d2, d1 / d2))
show("  at year 2 you collect DF_1/DF_2 = 1.03^2/1.025", "{:.6f}".format(1.03 ** 2 / 1.025))
show("  rate on that 1-year loan starting in one year (%)", "{:.4f}".format(100 * (d1 / d2 - 1)))
show("geometric average check: sqrt(1.025 x 1.035024)", "{:.6f}".format(math.sqrt(1.025 * (1.03 ** 2 / 1.025))))
show("shortcut 1y1y ~ 2 Z_2 - Z_1 = 2 x 3.0 - 2.5", "{:.2f}".format(2 * 3.0 - 2.5))
show("arithmetic mean of 2.5 and 3.5024", "{:.4f}".format((2.5 + 3.5024) / 2))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: inverted, Z_1 = 5.0%, Z_2 = 4.5% ===")
g2 = 1.045 ** 2
show("1.045^2", "{:.6f}".format(g2))
show("1.092025 / 1.05", "{:.6f}".format(g2 / 1.05))
we_f = fwd(0.05, 1, 0.045, 2)
show("1y1y (%)", "{!r} -> {:.4f}%".format(100 * we_f, 100 * we_f))
show("shortcut 2 x 4.5 - 5.0", "{:.2f}".format(2 * 4.5 - 5.0))
w_d1, w_d2 = df(0.05, 1), df(0.045, 2)
show("DF_1 = 1/1.05", "{:.6f}".format(w_d1))
show("DF_2 = 1/1.045^2", "{:.6f}".format(w_d2))
show("1 - DF_2", "{:.6f}".format(1 - w_d2))
show("DF_1 + DF_2", "{:.6f}".format(w_d1 + w_d2))
we_par = par_rate([w_d1, w_d2])
show("2-year par rate (%)", "{!r} -> {:.4f}%".format(100 * we_par, 100 * we_par))
show("check: 2-year bond with coupon = par rate at the spots", "{:.4f}".format(pv_at([100 * we_par, 100 + 100 * we_par], [0.05, 0.045])))
show("weight of the final payment in that price (%)", "{:.2f}".format(100 * (100 + 100 * we_par) * w_d2 / 100))

# ------------------------------------------------------------------ walkthrough
print("\n=== Calculator walkthrough: Z_1 = 2.5%, Z_2 = 3.0%, Z_3 = 3.4% ===")
Z1, Z2, Z3 = 0.025, 0.03, 0.034
f11 = fwd(Z1, 1, Z2, 2)
f21 = fwd(Z2, 2, Z3, 3)
f12 = fwd(Z1, 1, Z3, 3)
show("step 1  1.03^2", "BA {}".format(ba(1.03 ** 2)))
show("step 1  1.03^2 / 1.025 - 1 (before x 100)", "BA {} / TI {}".format(ba(f11), ti(f11)))
show("step 1  1y1y (%)", "{!r} -> BA {} / TI {}".format(100 * f11, ba(100 * f11), ti(100 * f11)))
show("step 2  1.034^3", "{:.6f} -> BA {}".format(1.034 ** 3, ba(1.034 ** 3)))
show("step 2  1.034^3 / 1.03^2 - 1 (before x 100)", "BA {} / TI {}".format(ba(f21), ti(f21)))
show("step 2  2y1y (%)", "{!r} -> BA {} / TI {}".format(100 * f21, ba(100 * f21), ti(100 * f21)))
show("step 3  1.034^3 / 1.025", "{:.6f} -> BA {}".format(1.034 ** 3 / 1.025, ba(1.034 ** 3 / 1.025)))
show("step 3  (1.034^3 / 1.025)^0.5 - 1 (before x 100)", "BA {} / TI {}".format(ba(f12), ti(f12)))
show("step 3  1y2y (%)", "{!r} -> BA {} / TI {}".format(100 * f12, ba(100 * f12), ti(100 * f12)))
show("step 3  check: sqrt(1.035024 x 1.042047) - 1 (%)", "{:.4f}".format(100 * (math.sqrt((1 + f11) * (1 + f21)) - 1)))
D1, D2, D3 = df(Z1, 1), df(Z2, 2), df(Z3, 3)
show("step 4  DF_1 = 1/1.025 (STO 1 / A)", "{!r} -> BA {} / TI {}".format(D1, ba(D1), ti(D1)))
show("step 4  1.03^2 then 1/x: DF_2 (STO 2 / B)", "{!r} -> BA {} / TI {}".format(D2, ba(D2), ti(D2)))
show("step 4  1.034^3 then 1/x: DF_3 (STO 3 / C)", "{!r} -> BA {} / TI {}".format(D3, ba(D3), ti(D3)))
S = D1 + D2 + D3
show("step 5  sum DF (STO 4 / D)", "{!r} -> BA {} / TI {}".format(S, ba(S), ti(S)))
show("step 6  1 - DF_3", "{:.6f} -> BA {}".format(1 - D3, ba(1 - D3)))
par3 = par_rate([D1, D2, D3])
show("step 6  3-year par rate (answer, %)", "{!r} -> BA {} / TI {}".format(100 * par3, ba(100 * par3), ti(100 * par3)))
chk = 100 * par3 * S + 100 * D3
show("step 7  check: par x sum DF + 100 x DF_3", "{!r} -> BA {} / TI {}".format(chk, ba(chk), ti(chk)))
show("step 7  pieces: coupons' PV, final 100's PV", "{:.4f} + {:.4f}".format(100 * par3 * S, 100 * D3))
show("        same at the spot rates", "{:.4f}".format(pv_at([100 * par3, 100 * par3, 100 + 100 * par3], [Z1, Z2, Z3])))
par1 = par_rate([D1])
par2 = par_rate([D1, D2])
show("        par curve 1y, 2y, 3y (%)", "{:.4f}, {:.4f}, {:.4f}".format(100 * par1, 100 * par2, 100 * par3))
show("        spot curve 1y, 2y, 3y (%)", "{:.4f}, {:.4f}, {:.4f}".format(100 * Z1, 100 * Z2, 100 * Z3))
show("        forward curve 0y1y, 1y1y, 2y1y (%)", "{:.4f}, {:.4f}, {:.4f}".format(100 * Z1, 100 * f11, 100 * f21))
show("        par below Z_3 by (bp)", "{:.2f}".format((100 * Z3 - 100 * par3) * 100))
show("        Z_3 as geometric average of 0y1y, 1y1y, 2y1y (%)",
     "{:.4f}".format(100 * (((1 + Z1) * (1 + f11) * (1 + f21)) ** (1 / 3) - 1)))

# ------------------------------------------------------------------ traps
print("\n=== Exam traps (walkthrough curve) ===")
show("trap 1: average of Z_2 and Z_3 (3.0 + 3.4)/2", "{:.2f}".format((3.0 + 3.4) / 2))
show("trap 1: shortcut 3 x 3.4 - 2 x 3.0 vs exact 2y1y", "{:.2f} vs {:.4f}".format(3 * 3.4 - 2 * 3.0, 100 * f21))
nr = 100 * (1.034 ** 3 / 1.025 - 1)
show("trap 2: 1y2y without the square root (%)", "{:.4f}".format(nr))
show("trap 2: 1y2y without the root, halved (%)", "{:.4f}".format(nr / 2))
show("trap 3: 2y1y vs 1y2y (%)", "{:.4f} vs {:.4f}".format(100 * f21, 100 * f12))
show("trap 4: par 3.3810 vs Z_3 3.4", "{:.4f} vs {:.4f}".format(100 * par3, 100 * Z3))

# ------------------------------------------------------------------ check
print("\n=== Check questions ===")
# c1: Z_2 = 3.5%, Z_3 = 3.9%: 2y1y
c1 = 100 * fwd(0.035, 2, 0.039, 3)
show("c1  1.039^3", "{:.6f} -> BA {}".format(1.039 ** 3, ba(1.039 ** 3)))
show("c1  1.035^2", "{:.6f}".format(1.035 ** 2))
show("c1  before x 100", "BA {} / TI {}".format(ba(c1 / 100), ti(c1 / 100)))
show("c1  2y1y (answer, %)", "{!r} -> BA {} / TI {} -> {:.3f}%".format(c1, ba(c1), ti(c1), c1))
show("c1  trap: average (3.5 + 3.9)/2", "{:.2f}".format((3.5 + 3.9) / 2))
show("c1  trap: shortcut 3 x 3.9 - 2 x 3.5", "{:.2f}".format(3 * 3.9 - 2 * 3.5))
show("c1  trap: 1.039^3/1.035 - 1 (wrong exponent on Z_2)", "{:.4f}".format(100 * (1.039 ** 3 / 1.035 - 1)))
show("c1  trap: 1.039^3/1.035^2 - 1 then halved (as if a 2-year leg)", "{:.4f}".format(c1 / 2))

# c2: why (right = C)
show("c2  walkthrough: Z_1 2.5 < Z_2 3.0 < 1y1y", "{:.4f}".format(100 * f11))

# c3: Z_1 = 3%, 1y1y = 4%, 2y1y = 5% -> Z_3
prod = 1.03 * 1.04 * 1.05
c3 = 100 * (prod ** (1 / 3) - 1)
show("c3  1.03 x 1.04 x 1.05", "{:.6f} -> BA {}".format(prod, ba(prod)))
show("c3  cube root", "{:.6f} -> BA {}".format(prod ** (1 / 3), ba(prod ** (1 / 3))))
show("c3  before x 100", "BA {} / TI {}".format(ba(c3 / 100), ti(c3 / 100)))
show("c3  Z_3 (answer, %)", "{!r} -> BA {} / TI {} -> {:.3f}%".format(c3, ba(c3), ti(c3), c3))
show("c3  trap: arithmetic average (3 + 4 + 5)/3", "{:.4f}".format((3 + 4 + 5) / 3))
show("c3  trap: forgot the cube root (%)", "{:.4f}".format(100 * (prod - 1)))
show("c3  check: Z_2 = sqrt(1.03 x 1.04) - 1 (%)", "{:.4f}".format(100 * (math.sqrt(1.03 * 1.04) - 1)))

# c4: Z_1 = 2.0%, Z_2 = 2.8%, Z_3 = 3.3%: 1y2y
c4B = 100 * fwd(0.02, 1, 0.033, 3)
c4A = 100 * fwd(0.028, 2, 0.033, 3)
c4C = 100 * (1.033 ** 3 / 1.02 - 1)
show("c4  1.033^3", "{:.6f}".format(1.033 ** 3))
show("c4  1.033^3 / 1.02", "{:.6f}".format(1.033 ** 3 / 1.02))
show("c4  B (right): 1y2y = (1.033^3/1.02)^(1/2) - 1", "{!r} -> {:.2f}%".format(c4B, c4B))
show("c4  A: 2y1y = 1.033^3/1.028^2 - 1", "{!r} -> {:.2f}%".format(c4A, c4A))
show("c4  C: no square root: 1.033^3/1.02 - 1", "{!r} -> {:.2f}%".format(c4C, c4C))
show("c4  1y1y on the way (for the solution)", "{:.4f}".format(100 * fwd(0.02, 1, 0.028, 2)))
show("c4  check: sqrt((1 + 1y1y)(1 + 2y1y)) - 1", "{:.4f}".format(100 * (math.sqrt((1 + fwd(0.02, 1, 0.028, 2)) * (1 + c4A / 100)) - 1)))

# ------------------------------------------------------------------ release date (for the hand-over note)
import datetime
start = datetime.date(2026, 11, 16)
due = start + datetime.timedelta(days=37 - 1)
show("release: start_date + 36 days", "{} ({})".format(due.isoformat(), due.strftime("%A")))
