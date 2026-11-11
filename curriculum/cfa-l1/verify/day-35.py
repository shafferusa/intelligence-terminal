#!/usr/bin/env python3
"""Day 35 of CFA Math: every number that appears on day-35.html.

Week 5 review (Days 29-34): binomial probabilities and counting, the normal
curve and its bands, skewness and kurtosis, log returns and simulation,
two-asset portfolio risk, covariance from a joint probability table, the
minimum-variance weight, utility and the capital allocation line (CFA Program
2027 Level I, Quantitative Methods, LM5, LM6, LM8 and LM9).
Warm-up from Day 34 (optimal weight on the CAL) and Day 24 (mean absolute
deviation).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math


def show(label, value):
    print("  {:<70} {}".format(label, value))


def d4(x):
    """A 4-decimal BA II Plus display (with thousands separators)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """TI-84 display in fixed 4 decimals: no leading zero below 1."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def both(x):
    return "BA {} / TI {}".format(d4(x), ti4(x))


def pct(x, dp=2):
    return "{:.{}f}%".format(100 * x, dp)


def Phi(z):
    """Standard normal cumulative probability (what a z-table prints)."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def ncr(n, r):
    return math.comb(n, r)


def binom(n, p, x):
    return ncr(n, x) * p ** x * (1 - p) ** (n - x)


# ---------------------------------------------------------------------------
print("=== w1 (Day 34): optimal weight in the risky portfolio ===")
A1, RF1, E1, S1 = 5, 0.04, 0.11, 0.16
prem1 = E1 - RF1
den1 = A1 * S1 ** 2
w1 = prem1 / den1
show("premium 0.11 - 0.04", "{:.4f}".format(prem1))
show("sigma^2 = 0.16^2; A x sigma^2", "{:.4f}; {:.4f}".format(S1 ** 2, den1))
show("w* = 0.07 / 0.128 (display)", both(w1))
show("w* (answer, percent)", "{!r} -> {}".format(100 * w1, pct(w1)))
show("  x 100 display", both(100 * w1))
show("  in T-bills 1 - w*", pct(1 - w1))
ep1 = RF1 + w1 * prem1
sp1 = w1 * S1
u1 = ep1 - 0.5 * A1 * sp1 ** 2
show("  mix E(Rp) = 0.04 + w* x 0.07", "{} ({})".format(both(ep1), pct(ep1)))
show("  mix sigma_p = w* x 0.16", "{} ({})".format(both(sp1), pct(sp1)))
show("  mix U", "{} ({})".format(both(u1), pct(u1)))
show("  U of all T-bills; U of all risky",
     "{}; {}".format(pct(RF1), pct(E1 - 0.5 * A1 * S1 ** 2)))
show("trap: sigma not squared 0.07 / (5 x 0.16)", "{:.4f} -> {}".format(prem1 / (A1 * S1), pct(prem1 / (A1 * S1))))
show("trap: percent units 7 / (5 x 16^2)", "{:.6f}".format(7 / (5 * 16 ** 2)))
show("trap: E instead of the premium 0.11 / 0.128", pct(E1 / den1))

print("\n=== w2 (Day 24): mean absolute deviation ===")
R2 = [9, -3, 5, 12, -8]
m2 = sum(R2) / len(R2)
ad = [abs(x - m2) for x in R2]
mad = sum(ad) / len(R2)
show("sum, mean", "{}, {:.2f}".format(sum(R2), m2))
show("deviations", ", ".join("{:+.0f}".format(x - m2) for x in R2))
show("  signed deviations sum", "{:.0f}".format(sum(x - m2 for x in R2)))
show("absolute deviations; sum", "{}; {:.0f}".format(", ".join("{:.0f}".format(a) for a in ad), sum(ad)))
show("MAD = 34 / 5 (C, right)", "{!r} -> {:.2f}%".format(mad, mad))
show("  BA display", both(mad))
show("B (trap): divide by n - 1 = 4", "{:.4f} -> {:.2f}%".format(sum(ad) / 4, sum(ad) / 4))
ss2 = sum((x - m2) ** 2 for x in R2)
show("A (trap): sample SD (sum of squares; / 4; root)",
     "{:.0f}; {:.2f}; {:.4f} -> {:.2f}%".format(ss2, ss2 / 4, math.sqrt(ss2 / 4), math.sqrt(ss2 / 4)))
show("  population SD (not offered)", "{:.4f}".format(math.sqrt(ss2 / 5)))

# ---------------------------------------------------------------------------
print("\n=== c1 (Day 29): exactly 3 beats in 5 years, p = 0.4 ===")
n1, p1, x1 = 5, 0.4, 3
c53 = ncr(n1, x1)
one_order = p1 ** 3 * (1 - p1) ** 2
pb = binom(n1, p1, x1)
show("5C3", "{} -> {}".format(c53, both(c53)))
show("0.4^3; 0.6^2", "{:.4f}; {:.4f}".format(p1 ** 3, (1 - p1) ** 2))
show("one order 0.4^3 x 0.6^2", "{:.5f}".format(one_order))
show("P(X = 3) (answer)", "{!r} -> {} -> {}".format(pb, both(pb), pct(pb)))
show("  data-answer (percent)", repr(100 * pb))
show("  x 100 display", both(100 * pb))
show("trap: 5C3 forgotten (one order)", "{:.5f} -> {}".format(one_order, pct(one_order)))
show("trap: (1 - p)^(n - x) dropped 10 x 0.4^3", pct(c53 * p1 ** 3))
atl3 = sum(binom(n1, p1, k) for k in (3, 4, 5))
show("trap: at least 3 = P3 + P4 + P5", "{:.5f} + {:.5f} + {:.5f} = {:.5f} -> {}".format(
    binom(n1, p1, 3), binom(n1, p1, 4), binom(n1, p1, 5), atl3, pct(atl3)))
show("trap: p and 1 - p swapped 10 x 0.6^3 x 0.4^2", pct(c53 * 0.6 ** 3 * 0.4 ** 2))
show("expected beats np; variance np(1-p)", "{:.1f}; {:.2f}".format(n1 * p1, n1 * p1 * (1 - p1)))
show("binomcdf(5,.4,2) and 1 - it", "{:.4f}; {:.4f}".format(sum(binom(5, .4, k) for k in range(3)),
                                                         1 - sum(binom(5, .4, k) for k in range(3))))

print("\n=== c2 (Day 30): P(return < -5%), mu = 8, sigma = 10 ===")
MU2, SD2, X2 = 8.0, 10.0, -5.0
z2 = (X2 - MU2) / SD2
show("z = (-5 - 8) / 10", both(z2))
for zz in (1.25, 1.30, 1.35):
    show("  table P(Z <= {:.2f})".format(zz), "{:.4f}".format(Phi(zz)))
tab = round(Phi(1.30), 4)
show("table route 1 - 0.9032", "{:.4f}".format(1 - tab))
p2 = Phi(z2)
show("normalcdf(-1E99,-5,8,10) exact", "{!r} -> {}".format(p2, both(p2)))
show("answer (percent)", "{!r} -> {}".format(100 * p2, pct(p2)))
show("  x 100 display", both(100 * p2))
show("trap: mean left out, z = -5/10 = -0.50", pct(Phi(-0.5)))
show("trap: divided by the variance, z = -13/100", pct(Phi(-13 / 100)))
show("trap: right tail read, 0.9032", pct(1 - p2))

print("\n=== c3 (Day 30): 95% band, mu = 9, sigma = 12 ===")
MU3, SD3 = 9.0, 12.0
for zc, lab in ((1.645, "90% (A, trap)"), (1.96, "95% (B, right)"), (2.58, "99% (C, trap)")):
    h = zc * SD3
    show("{}: 9 +/- {} x 12 = 9 +/- {:.2f}".format(lab, zc, h), "{:.2f}% to {:.2f}%".format(MU3 - h, MU3 + h))
show("check with exact z 1.959964", "{:.2f}% to {:.2f}%".format(MU3 - 1.959964 * SD3, MU3 + 1.959964 * SD3))
show("two-sided coverage at 1.645, 1.96, 2.58", "{}, {}, {}".format(
    pct(2 * Phi(1.645) - 1), pct(2 * Phi(1.96) - 1), pct(2 * Phi(2.58) - 1)))

print("\n=== c4 (Day 30): skew and kurtosis ===")
MEAN4, MED4, XK4 = 0.8, 1.2, 1.8
show("mean - median", "{:+.1f} (mean below median -> negative skew)".format(MEAN4 - MED4))
show("excess kurtosis; total kurtosis = excess + 3", "{:.1f}; {:.1f}".format(XK4, XK4 + 3))

# ---------------------------------------------------------------------------
print("\n=== c5 (Day 31): two-period continuously compounded return, 64 -> 72 -> 60 ===")
P0, P1_, P2_ = 64.0, 72.0, 60.0
r1 = math.log(P1_ / P0)
r2 = math.log(P2_ / P1_)
rt = math.log(P2_ / P0)
show("60 / 64 (growth factor)", both(P2_ / P0))
show("ln(60/64) (answer)", "{!r} -> {} -> {}".format(rt, both(rt), pct(rt)))
show("  data-answer (percent)", repr(100 * rt))
show("  x 100 display", both(100 * rt))
show("ln(72/64); ln(60/72); sum", "{:.6f}; {:.6f}; {:.6f}".format(r1, r2, r1 + r2))
show("  4-dec displays", "{} ; {}".format(both(r1), both(r2)))
s1, s2 = P1_ / P0 - 1, P2_ / P1_ - 1
show("simple returns", "{} and {}".format(pct(s1), pct(s2)))
show("trap: simple returns added", "{:.4f} -> {}".format(s1 + s2, pct(s1 + s2)))
show("trap: holding-period return 60/64 - 1", pct(P2_ / P0 - 1))
show("check: 64 x e^(r)", "{:.4f}".format(P0 * math.exp(rt)))

print("\n=== c6 (Day 31): Monte Carlo identification (no numbers beyond the stem) ===")
show("stem numbers", "mean 0.7%, SD 4.5%, 10,000 paths of 12 months")

# ---------------------------------------------------------------------------
print("\n=== c7 (Day 32): 50/50, sigma 22 and 9, rho = -0.2 ===")
W7, S7a, S7b, RHO7 = 0.5, 22.0, 9.0, -0.2
own_a = W7 ** 2 * S7a ** 2
own_b = W7 ** 2 * S7b ** 2
cross = 2 * W7 * W7 * RHO7 * S7a * S7b
var7 = own_a + own_b + cross
sd7 = math.sqrt(var7)
show("own term 0.5^2 x 22^2", both(own_a))
show("own term 0.5^2 x 9^2", both(own_b))
show("cross term 2 x .5 x .5 x (-.2) x 22 x 9", both(cross))
show("  covariance rho x 22 x 9", "{:.2f}".format(RHO7 * S7a * S7b))
show("variance", both(var7))
show("sigma_p (answer)", "{!r} -> {} -> {:.2f}%".format(sd7, both(sd7), sd7))
wavg = W7 * S7a + W7 * S7b
show("trap: weighted-average SD", "{:.2f}%".format(wavg))
show("trap: 2 forgotten", "{:.2f} -> {:.2f}%".format(own_a + own_b + cross / 2, math.sqrt(own_a + own_b + cross / 2)))
show("trap: sign of rho dropped", "{:.2f} -> {:.2f}%".format(own_a + own_b - cross, math.sqrt(own_a + own_b - cross)))
show("trap: weights not squared .5x484 + .5x81 + cross", "{:.4f}%".format(math.sqrt(W7 * S7a ** 2 + W7 * S7b ** 2 + cross)))

print("\n=== c8 (Day 32): covariance from a joint probability table ===")
PR = [0.25, 0.50, 0.25]
X8 = [18.0, 8.0, -6.0]
Y8 = [2.0, 5.0, 11.0]
ex = sum(p * x for p, x in zip(PR, X8))
ey = sum(p * y for p, y in zip(PR, Y8))
show("E(X) pieces", ", ".join("{:.2f}".format(p * x) for p, x in zip(PR, X8)))
show("E(X)", both(ex))
show("E(Y) pieces", ", ".join("{:.2f}".format(p * y) for p, y in zip(PR, Y8)))
show("E(Y)", both(ey))
dxs = [x - ex for x in X8]
dys = [y - ey for y in Y8]
show("X deviations", ", ".join("{:+.2f}".format(v) for v in dxs))
show("Y deviations", ", ".join("{:+.2f}".format(v) for v in dys))
terms = [p * a * b for p, a, b in zip(PR, dxs, dys)]
show("P x dX x dY per state", ", ".join("{:.4f}".format(t) for t in terms))
cov8 = sum(terms)
show("Cov (answer, %^2)", "{!r} -> {}".format(cov8, both(cov8)))
show("  in decimals", "{:.6f}".format(cov8 / 100 ** 2))
varx = sum(p * a * a for p, a in zip(PR, dxs))
vary = sum(p * b * b for p, b in zip(PR, dys))
sx8, sy8 = math.sqrt(varx), math.sqrt(vary)
show("Var X, sigma X", "{:.4f}, {:.4f}".format(varx, sx8))
show("Var Y, sigma Y", "{:.4f}, {:.4f}".format(vary, sy8))
show("rho = Cov / (sX sY)", "{:.4f}".format(cov8 / (sx8 * sy8)))
show("trap: probabilities left out (sum of products)", "{:.2f}".format(sum(a * b for a, b in zip(dxs, dys))))
mx, my = sum(X8) / 3, sum(Y8) / 3
samp = sum((x - mx) * (y - my) for x, y in zip(X8, Y8)) / 2
show("trap: states treated as a sample, unweighted means, / (n - 1)",
     "means {:.4f}, {:.4f}; cov {:.4f}".format(mx, my, samp))
show("trap: probability-weighted, then / (n - 1) = 2 more", "{:.4f}".format(cov8 * 3 / 2))

print("\n=== c9 (Day 33): minimum-variance weight, sigma 18 and 10, rho 0.3 ===")
S9a, S9b, RHO9 = 18.0, 10.0, 0.3
cov9 = RHO9 * S9a * S9b
top9 = S9b ** 2 - cov9
bot9 = S9a ** 2 + S9b ** 2 - 2 * cov9
w9 = top9 / bot9
show("Cov = 0.3 x 18 x 10", both(cov9))
show("top 100 - 54; bottom 324 + 100 - 108", "{:.0f}; {:.0f}".format(top9, bot9))
show("w1* (display)", both(w9))
show("w1* (answer, percent)", "{!r} -> {}".format(100 * w9, pct(w9)))
show("  x 100 display", both(100 * w9))
show("  in decimals: (0.01 - 0.0054) / (0.0324 + 0.01 - 0.0108)",
     "{:.4f} / {:.4f} = {:.4f}".format(0.01 - 0.0054, 0.0324 + 0.01 - 0.0108, (0.01 - 0.0054) / (0.0324 + 0.01 - 0.0108)))
w9b = 1 - w9
var9 = w9 ** 2 * S9a ** 2 + w9b ** 2 * S9b ** 2 + 2 * w9 * w9b * cov9
show("w2 = 1 - w1*", both(w9b))
show("variance at the mix", both(var9))
show("minimum sigma_p", "{} -> {:.2f}%".format(both(math.sqrt(var9)), math.sqrt(var9)))
show("trap: rho typed for Cov (100 - .3)/(324 + 100 - .6)", "{:.4f} -> {}".format(
    (S9b ** 2 - RHO9) / (S9a ** 2 + S9b ** 2 - 2 * RHO9), pct((S9b ** 2 - RHO9) / (S9a ** 2 + S9b ** 2 - 2 * RHO9))))
show("trap: rho = -1 shortcut 10/28", pct(S9b / (S9a + S9b)))
show("trap: Cov ignored 100/424", pct(S9b ** 2 / (S9a ** 2 + S9b ** 2)))
show("trap: sigma1^2 on top (324 - 54)/316", pct((S9a ** 2 - cov9) / bot9))
show("trap: 2 dropped in the bottom (100 - 54)/(324 + 100 - 54)", pct(top9 / (S9a ** 2 + S9b ** 2 - cov9)))

print("\n=== c10 (Day 34): utility, A = 4 ===")
A10 = 4
PORTS = [("P1", 0.05, 0.05), ("P2", 0.09, 0.12), ("P3", 0.14, 0.24)]
for name, e, s in PORTS:
    u = e - 0.5 * A10 * s ** 2
    u_sig = e - 0.5 * A10 * s
    u_pctunits = 100 * e - 0.5 * A10 * (100 * s) ** 2
    u_nohalf = e - A10 * s ** 2
    show("{}: penalty 0.5 x 4 x s^2; U".format(name), "{:.4f}; {:.4f} -> {}".format(
        0.5 * A10 * s ** 2, u, pct(u)))
    show("   with sigma not squared; percent units; no 1/2", "{}; {:.0f}; {}".format(
        pct(u_sig), u_pctunits, pct(u_nohalf)))
best = max(PORTS, key=lambda t: t[1] - 0.5 * A10 * t[2] ** 2)[0]
show("highest U (B, right)", best)
show("risk-neutral pick (highest E) (C)", max(PORTS, key=lambda t: t[1])[0])
show("A = 0 tie-break: none; A at which P2 and P3 tie",
     "{:.4f}".format((0.14 - 0.09) / (0.5 * (0.24 ** 2 - 0.12 ** 2))))
show("A at which P1 and P2 tie", "{:.4f}".format((0.09 - 0.05) / (0.5 * (0.12 ** 2 - 0.05 ** 2))))

# ---------------------------------------------------------------------------
print("\n=== traps box (numbers from Days 32 and 34, already seen) ===")
v_ok = 0.6 ** 2 * 400 + 0.4 ** 2 * 64 + 2 * 0.6 * 0.4 * 0.2 * 20 * 8
v_no2 = 0.6 ** 2 * 400 + 0.4 ** 2 * 64 + 0.6 * 0.4 * 0.2 * 20 * 8
show("60/40, 20 & 8, rho .2: variance with / without the 2", "{:.2f} / {:.2f}".format(v_ok, v_no2))
show("  sigma_p with / without the 2", "{:.2f}% / {:.2f}%".format(math.sqrt(v_ok), math.sqrt(v_no2)))
show("utility in decimals 0.10 - .5 x 4 x 0.20^2", "{:.4f}".format(0.10 - 0.5 * 4 * 0.2 ** 2))
show("utility in percent 10 - .5 x 4 x 20^2", "{:.0f}".format(10 - 0.5 * 4 * 20 ** 2))
show("one-tailed 5%: 1.645 leaves in one tail", pct(1 - Phi(1.645)))
show("1.96 leaves in one tail", pct(1 - Phi(1.96)))

print("\n=== count of calculator questions ===")
show("c1 c2 c5 c7 c8 c9", 6)
