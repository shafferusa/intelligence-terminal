#!/usr/bin/env python3
"""Day 32 of CFA Math: every number that appears on day-32.html.

Two-asset portfolio return and risk: the expected return of a portfolio as the
weighted average E(R_p) = sum w_i E(R_i); the two-asset portfolio variance
sigma_p^2 = w1^2 s1^2 + w2^2 s2^2 + 2 w1 w2 Cov_12 with Cov_12 = rho s1 s2;
covariance and correlation from a joint probability table; and why sigma_p is
below the weighted-average SD whenever rho < +1 (CFA Program 2027 Level I,
Quantitative Methods, LM8 The Return and Risk of a Financial Portfolio).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from decimal import Decimal, ROUND_HALF_UP


def show(label, value):
    print("  {:<70} {}".format(label, value))


def d4(x):
    """A 4-decimal BA II Plus display (thousands separators, leading zero)."""
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


def r2(x):
    """Round half up to 2 decimals, as a person would write it."""
    return str(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def port(w1, s1, s2, rho, two=2.0):
    """Two-asset portfolio variance (same units as s1^2) and SD."""
    w2 = 1 - w1
    var = w1 ** 2 * s1 ** 2 + w2 ** 2 * s2 ** 2 + two * w1 * w2 * rho * s1 * s2
    return var, math.sqrt(var)


# ---------------------------------------------------------------------------
print("=== Release date ===")
start = datetime.date(2026, 11, 16)
due = start + datetime.timedelta(days=32 - 1)
show("course start (plan start_date)", start.isoformat() + " " + start.strftime("%A"))
show("Day 32 due = start + 31 days", due.isoformat() + " " + due.strftime("%A"))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
p0, p1, p2 = 25.00, 27.50, 26.40
r1 = math.log(p1 / p0)
r2_ = math.log(p2 / p1)
tot = math.log(p2 / p0)
show("w1 day 1: ln(27.50/25.00) = ln 1.10", "{:.6f} -> {}".format(r1, both(r1)))
show("w1 day 2: ln(26.40/27.50) = ln 0.96", "{:.6f} -> {}".format(r2_, both(r2_)))
show("w1 sum of the two", "{:.6f}".format(r1 + r2_))
show("w1 one step: 26.40/25.00 ; ln of it (answer)", "{:.4f} ; {:.6f} -> {}".format(p2 / p0, tot, both(tot)))
show("w1   x 100 (data-answer)", "{!r} -> {}".format(tot * 100, both(tot * 100)))
show("w1   as written", r2(tot * 100) + "%")
show("w1 trap: simple returns added  +10% and -4%", "{:.2f}% + {:.2f}% = {:.2f}%".format(
    100 * (p1 / p0 - 1), 100 * (p2 / p1 - 1), 100 * (p1 / p0 - 1) + 100 * (p2 / p1 - 1)))
show("w1 trap: holding-period return 26.40/25 - 1", "{:.2f}%".format(100 * (p2 / p0 - 1)))

r, sx, sy, n = 0.60, 15.00, 6.00, 5
cov_right = r * sx * sy
sigx = sx * math.sqrt((n - 1) / n)
sigy = sy * math.sqrt((n - 1) / n)
show("w2 sample cov r x Sx x Sy = 0.60 x 15 x 6 (A, right)", "{:.2f}".format(cov_right))
show("w2 sigma x = Sx sqrt(4/5) ; sigma y", "{:.4f} -> {:.2f} ; {:.4f} -> {:.2f}".format(sigx, sigx, sigy, sigy))
show("w2 B: r x sigma x x sigma y (population SDs)", "{:.4f} -> {:.2f}".format(r * sigx * sigy, r * sigx * sigy))
show("w2 B from the rounded 13.42 and 5.37", "{:.4f}".format(r * 13.42 * 5.37))
show("w2 C: divided by n - 1 again: 54 / 4", "{:.2f}".format(cov_right / (n - 1)))

# ---------------------------------------------------------------------------
print("\n=== Story ===")
born = datetime.date(1927, 8, 24)
pub = datetime.date(1952, 3, 1)
age = pub.year - born.year - ((pub.month, pub.day) < (born.month, born.day))
show("age in March 1952 (born 24 Aug 1927)", age)
show("pages 77-91 -> number of pages", 91 - 77 + 1)
show("Williams 1938 -> years before 1952", 1952 - 1938)
show("Nobel 1990 -> years after the paper", 1990 - 1952)

# ---------------------------------------------------------------------------
print("\n=== The 60/40 portfolio (idea, calculator, back to the story) ===")
w1, w2 = 0.6, 0.4
E1, E2 = 9.0, 4.0
s1, s2 = 20.0, 8.0
rho = 0.2
ER = w1 * E1 + w2 * E2
t1 = w1 ** 2 * s1 ** 2
t2 = w2 ** 2 * s2 ** 2
t3 = 2 * w1 * w2 * rho * s1 * s2
var = t1 + t2 + t3
sd = math.sqrt(var)
show("E(R_p) = 0.6 x 9 + 0.4 x 4", "{:.4f} -> {}".format(ER, both(ER)))
show("  0.6 x 9 ; 0.4 x 4", "{:.1f} ; {:.1f}".format(w1 * E1, w2 * E2))
show("0.6^2 ; 20^2", "{:.4f} ; {:.0f}".format(w1 ** 2, s1 ** 2))
show("term 1: 0.6^2 x 20^2 (STO 1 / ->A)", both(t1))
show("0.4^2 ; 8^2", "{:.4f} ; {:.0f}".format(w2 ** 2, s2 ** 2))
show("term 2: 0.4^2 x 8^2 (STO 2 / ->B)", both(t2))
show("Cov = 0.2 x 20 x 8 (%^2) ; in decimals", "{:.4f} ; {:.4f}".format(rho * s1 * s2, rho * 0.20 * 0.08))
show("term 3: 2 x 0.6 x 0.4 x 0.2 x 20 x 8 (STO 3 / ->C)", both(t3))
show("  2 x 0.6 x 0.4 = 0.48 ; 0.48 x 32", "{:.2f} ; {:.2f}".format(2 * w1 * w2, 2 * w1 * w2 * 32))
show("variance (%^2) = RCL1 + RCL2 + RCL3", both(var))
show("sigma_p = sqrt(var) (walkthrough answer)", "{!r} -> {}".format(sd, both(sd)))
show("  as written", r2(sd) + "%")
show("variance in decimals ; its root", "{:.5f} ; {:.6f}".format(var / 10000, math.sqrt(var / 10000)))
show("same in decimals: .6^2x.2^2 + .4^2x.08^2 + 2x.6x.4x.2x.2x.08", "{:.5f}".format(
    0.36 * 0.04 + 0.16 * 0.0064 + 2 * 0.6 * 0.4 * 0.2 * 0.2 * 0.08))
wavg = w1 * s1 + w2 * s2
show("weighted-average SD 0.6 x 20 + 0.4 x 8", both(wavg))
show("gap: 15.2 - 13.0231", "{:.4f} -> {:.2f} points".format(wavg - sd, wavg - sd))
show("one-line TI: sqrt(.6^2*20^2+.4^2*8^2+2*.6*.4*.2*20*8)", ti4(math.sqrt(0.36 * 400 + 0.16 * 64 + 2 * 0.6 * 0.4 * 0.2 * 20 * 8)))

print("  -- the rho ladder for the 60/40 mix --")
for rr in (1.0, 0.5, 0.2, 0.0, -0.5, -1.0):
    v, s = port(w1, s1, s2, rr)
    show("  rho = {:+.1f}: variance ; sigma_p".format(rr), "{:.4f} ; {:.4f} -> {:.2f}%".format(v, s, s))
show("rho = +1 check: (0.6 x 20 + 0.4 x 8)^2", "{:.4f} = 15.2^2".format((w1 * s1 + w2 * s2) ** 2))
show("rho = -1 check: |0.6 x 20 - 0.4 x 8|", "{:.4f}".format(abs(w1 * s1 - w2 * s2)))
show("  0.6 x 20 ; 0.4 x 8", "{:.1f} ; {:.1f}".format(w1 * s1, w2 * s2))

print("  -- back to the story: 90% bonds, 10% stocks --")
a = 0.10
ERa = a * E1 + (1 - a) * E2
ta1 = a ** 2 * s1 ** 2
ta2 = (1 - a) ** 2 * s2 ** 2
ta3 = 2 * a * (1 - a) * rho * s1 * s2
va = ta1 + ta2 + ta3
show("E(R_p) = 0.1 x 9 + 0.9 x 4", "{:.4f} -> {:.1f}%".format(ERa, ERa))
show("0.1^2 x 20^2 ; 0.9^2 x 8^2 ; 0.9^2", "{:.4f} ; {:.4f} ; {:.2f}".format(ta1, ta2, (1 - a) ** 2))
show("2 x 0.1 x 0.9 x 0.2 x 20 x 8 ; 2 x 0.1 x 0.9", "{:.4f} ; {:.2f}".format(ta3, 2 * a * (1 - a)))
show("variance ; sigma_p", "{:.4f} ; {:.4f} -> {:.2f}%".format(va, math.sqrt(va), math.sqrt(va)))
show("all bonds: variance ; sigma", "{:.0f} ; {:.0f}%".format(s2 ** 2, s2))
show("bond term falls by 64 - 51.84", "{:.2f}".format(s2 ** 2 - ta2))
show("min-variance stock weight (Day 33 preview) (64-32)/(400+64-64)", "{:.4f}".format(
    (s2 ** 2 - rho * s1 * s2) / (s1 ** 2 + s2 ** 2 - 2 * rho * s1 * s2)))
wm = (s2 ** 2 - rho * s1 * s2) / (s1 ** 2 + s2 ** 2 - 2 * rho * s1 * s2)
show("  sigma_p at that weight", "{:.4f}".format(port(wm, s1, s2, rho)[1]))

# ---------------------------------------------------------------------------
print("\n=== Worked example: joint probability table ===")
P = [0.3, 0.5, 0.2]
A = [25.0, 10.0, -15.0]
B = [4.0, 8.0, 5.0]
EA = sum(p * x for p, x in zip(P, A))
EB = sum(p * y for p, y in zip(P, B))
show("sum of probabilities", "{:.1f}".format(sum(P)))
show("E(R_A) = 0.3x25 + 0.5x10 + 0.2x(-15)", "{:.1f} + {:.1f} + {:.1f} = {:.4f}".format(
    P[0] * A[0], P[1] * A[1], P[2] * A[2], EA))
show("E(R_B) = 0.3x4 + 0.5x8 + 0.2x5", "{:.1f} + {:.1f} + {:.1f} = {:.4f}".format(
    P[0] * B[0], P[1] * B[1], P[2] * B[2], EB))
cov = 0.0
names = ["boom", "normal", "recession"]
for nm, p, x, y in zip(names, P, A, B):
    dA, dB = x - EA, y - EB
    c = p * dA * dB
    cov += c
    show("{}: {} x ({:+.1f}) x ({:+.1f})".format(nm, p, dA, dB), "{:.4f} -> {:.2f}".format(c, c))
show("Cov (%^2)", "{:.4f} -> {:.2f}".format(cov, cov))
show("Cov in decimals (/10,000)", "{:.6f} -> {:.5f}".format(cov / 10000, cov / 10000))
vA = sum(p * (x - EA) ** 2 for p, x in zip(P, A))
vB = sum(p * (y - EB) ** 2 for p, y in zip(P, B))
show("var A pieces 0.3x15.5^2 ; 0.5x0.5^2 ; 0.2x24.5^2", "{:.4f} ; {:.4f} ; {:.4f}".format(
    0.3 * 15.5 ** 2, 0.5 * 0.5 ** 2, 0.2 * 24.5 ** 2))
show("var A ; sigma A", "{:.4f} ; {:.4f}".format(vA, math.sqrt(vA)))
show("var B pieces 0.3x2.2^2 ; 0.5x1.8^2 ; 0.2x1.2^2", "{:.4f} ; {:.4f} ; {:.4f}".format(
    0.3 * 2.2 ** 2, 0.5 * 1.8 ** 2, 0.2 * 1.2 ** 2))
show("var B ; sigma B", "{:.4f} ; {:.4f}".format(vB, math.sqrt(vB)))
sA, sB = math.sqrt(vA), math.sqrt(vB)
show("sigma A x sigma B", "{:.4f}".format(sA * sB))
show("rho = Cov / (sA sB)", "{:.6f} -> {:.4f}".format(cov / (sA * sB), cov / (sA * sB)))
show("rho from rounded 13.8654 x 1.8330", "{:.4f}".format(-3.90 / (13.8654 * 1.8330)))
plain = [(x - EA) * (y - EB) for x, y in zip(A, B)]
show("trap: plain products (no probabilities) ; their sum", "{} ; {:.2f}".format(
    ", ".join("{:.2f}".format(v) for v in plain), sum(plain)))
show("trap: divided by n - 1 = 2 as if a sample", "{:.4f} -> {:.2f}".format(sum(plain) / 2, sum(plain) / 2))

# ---------------------------------------------------------------------------
print("\n=== Exam traps (walkthrough numbers) ===")
show("averaging the SDs (rho = +1)", "{:.2f}%".format(wavg))
v_no2 = t1 + t2 + t3 / 2
show("forgot the 2: 144 + 10.24 + 7.68", "{:.2f} -> sqrt {:.4f} -> {:.2f}%".format(v_no2, math.sqrt(v_no2), math.sqrt(v_no2)))
show("  half the cross term", "{:.2f}".format(t3 / 2))
cov_dec = rho * 0.20 * 0.08
v_mix = t1 + t2 + 2 * w1 * w2 * cov_dec
show("mixed: Cov 0.0032 typed into a percent sum", "{:.4f} -> sqrt {:.4f} -> {:.2f}%".format(v_mix, math.sqrt(v_mix), math.sqrt(v_mix)))
show("  2 x 0.6 x 0.4 x 0.0032", "{:.6f}".format(2 * w1 * w2 * cov_dec))
show("Cov 0.0032 in %^2 (x 10,000)", "{:.0f}".format(cov_dec * 10000))
show("variance 0.01696 misread as 1.696%: sqrt(1.696)", "{:.4f}".format(math.sqrt(1.696)))

# ---------------------------------------------------------------------------
print("\n=== c1: 70% equities (8%, 18%), 30% bonds (3.5%, 6%), rho = -0.1 ===")
c1 = dict(w1=0.7, E1=8.0, E2=3.5, s1=18.0, s2=6.0, rho=-0.1)
ER1 = c1["w1"] * c1["E1"] + (1 - c1["w1"]) * c1["E2"]
u1 = c1["w1"] ** 2 * c1["s1"] ** 2
u2 = (1 - c1["w1"]) ** 2 * c1["s2"] ** 2
u3 = 2 * c1["w1"] * (1 - c1["w1"]) * c1["rho"] * c1["s1"] * c1["s2"]
vc1 = u1 + u2 + u3
show("E(R_p) = 0.7 x 8 + 0.3 x 3.5", "{:.4f} -> {:.2f}%".format(ER1, ER1))
show("0.7^2 x 18^2", both(u1))
show("0.3^2 x 6^2", both(u2))
show("2 x 0.7 x 0.3 x (-0.1) x 18 x 6", both(u3))
show("variance", both(vc1))
show("sigma_p (data-answer)", "{!r} -> {} -> {}%".format(math.sqrt(vc1), both(math.sqrt(vc1)), r2(math.sqrt(vc1))))
show("weighted-average SD 0.7 x 18 + 0.3 x 6", "{:.2f}%".format(0.7 * 18 + 0.3 * 6))
show("trap: rho sign dropped (+0.1)", "{:.2f}%".format(port(0.7, 18, 6, 0.1)[1]))

# ---------------------------------------------------------------------------
print("\n=== c2: two assets, sigma 15% each, 50/50 ===")
for rr in (1.0, 0.0, -1.0):
    v, s = port(0.5, 15, 15, rr)
    show("rho = {:+.0f}: variance ; sigma_p".format(rr), "{:.4f} ; {:.4f} -> {:.2f}%".format(v, s, s))
show("rho = 0: 0.25 x 225 + 0.25 x 225", "{:.2f}".format(0.25 * 225 * 2))

# ---------------------------------------------------------------------------
print("\n=== c3: 50/50, sigma 20% and 10%, sigma_p 12% -> rho ===")
lhs = 12.0 ** 2
own = 0.25 * 400 + 0.25 * 100
k = 2 * 0.5 * 0.5 * 20 * 10
rho3 = (lhs - own) / k
show("12^2 ; 0.25 x 400 ; 0.25 x 100", "{:.0f} ; {:.0f} ; {:.0f}".format(lhs, 0.25 * 400, 0.25 * 100))
show("144 - 100 - 25", both(lhs - own))
show("2 x 0.5 x 0.5 x 20 x 10", "{:.0f}".format(k))
show("rho = 19 / 100 (data-answer)", "{!r} -> {}".format(rho3, both(rho3)))
show("decimals: (0.0144 - 0.01 - 0.0025) / (2 x .25 x .2 x .1)", "{:.4f}".format(
    (0.0144 - 0.25 * 0.04 - 0.25 * 0.01) / (2 * 0.25 * 0.2 * 0.1)))
show("check: sigma_p at rho = 0.19", "{:.4f}".format(port(0.5, 20, 10, rho3)[1]))
show("trap: forgot the 2 -> rho", "{:.2f}".format((lhs - own) / (k / 2)))

# ---------------------------------------------------------------------------
print("\n=== c4: 40% sigma 25%, 60% sigma 10%, rho 0.3 ===")
q1 = 0.4 ** 2 * 25 ** 2
q2 = 0.6 ** 2 * 10 ** 2
q3 = 2 * 0.4 * 0.6 * 0.3 * 25 * 10
show("0.16 x 625 ; 0.36 x 100 ; 2 x .4 x .6 x .3 x 25 x 10", "{:.2f} ; {:.2f} ; {:.2f}".format(q1, q2, q3))
show("C (right): sqrt(172)", "{:.4f} -> {:.2f}%".format(math.sqrt(q1 + q2 + q3), math.sqrt(q1 + q2 + q3)))
show("B (forgot the 2): sqrt(100 + 36 + 18)", "{:.4f} -> {:.2f}%".format(math.sqrt(q1 + q2 + q3 / 2), math.sqrt(q1 + q2 + q3 / 2)))
show("A (weighted-average SD): 0.4 x 25 + 0.6 x 10", "{:.2f}%".format(0.4 * 25 + 0.6 * 10))
show("  sum 100 + 36 + 36", "{:.0f}".format(q1 + q2 + q3))

# ---------------------------------------------------------------------------
print("\n=== c5 numbers (model answer) ===")
show("60/40: weighted-average SD vs sigma_p", "{:.1f}% vs {:.2f}%".format(wavg, sd))
