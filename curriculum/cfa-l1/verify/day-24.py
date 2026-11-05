#!/usr/bin/env python3
"""Day 24 of CFA Math: every number that appears on day-24.html.

Dispersion: range, mean absolute deviation, sample and population variance and
standard deviation, the coefficient of variation and target downside deviation
(CFA Program 2027 Level I, Quantitative Methods, LM5 Statistical Characteristics
of Asset Returns). Plain Python 3, standard library only. Run it and copy the
printed numbers.
"""

import math


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def mean(xs):
    return sum(xs) / len(xs)


def ss(xs, c=None):
    """Sum of squared deviations from c (default: the sample mean)."""
    if c is None:
        c = mean(xs)
    return sum((x - c) ** 2 for x in xs)


def sample_var(xs):
    return ss(xs) / (len(xs) - 1)


def pop_var(xs):
    return ss(xs) / len(xs)


def mad(xs):
    m = mean(xs)
    return sum(abs(x - m) for x in xs) / len(xs)


def downside(xs, b, divisor=None):
    """Target downside deviation: shortfalls at or below b, divided by n - 1 (all n)."""
    s = sum((x - b) ** 2 for x in xs if x <= b)
    d = (len(xs) - 1) if divisor is None else divisor
    return math.sqrt(s / d), s


def percentile(xs, y):
    """CFA location L_y = (n + 1) y / 100 with linear interpolation (1-based)."""
    s = sorted(xs)
    loc = (len(s) + 1) * y / 100.0
    whole = int(math.floor(loc))
    frac = loc - whole
    if whole < 1:
        return s[0], loc
    if whole >= len(s):
        return s[-1], loc
    return s[whole - 1] + frac * (s[whole] - s[whole - 1]), loc


def show(label, value):
    print("  {:<62} {}".format(label, value))


# ---------------------------------------------------------------- warm-up
print("=== Warm-up ===")
W1 = [3.1, -2.4, 5.6, 0.8, -0.5, 4.2, 1.9, 7.3]
show("w1 data (as reported)", W1)
show("w1 sorted", sorted(W1))
p40, loc40 = percentile(W1, 40)
show("w1 location L_40 = (8 + 1) x 0.40", "{:.4f}".format(loc40))
show("w1 3rd and 4th sorted values", "{} and {}".format(sorted(W1)[2], sorted(W1)[3]))
show("w1 gap 4th - 3rd", "{:.4f}".format(sorted(W1)[3] - sorted(W1)[2]))
show("w1 0.6 x gap", "{:.4f}".format(0.6 * (sorted(W1)[3] - sorted(W1)[2])))
show("w1 40th percentile (answer)", "{!r} -> {:.2f}%".format(p40, p40))
# trap: location n x y / 100 = 3.2
s = sorted(W1)
trap_loc = len(W1) * 40 / 100.0
trap = s[2] + (trap_loc - 3) * (s[3] - s[2])
show("w1 trap: location n x y/100", "{:.4f} -> {:.4f}%".format(trap_loc, trap))

# w2: 5% semiannual corporate bond, 60 of 180 days (30/360), flat 101.20
cpn_half = 5 / 2.0
ai = 60 / 180.0 * cpn_half
full = 101.20 + ai
show("w2 half-year coupon per 100", "{:.4f}".format(cpn_half))
show("w2 AI = 60/180 x 2.5", "{:.4f}".format(ai))
show("w2 full = 101.20 + AI (A, right)", "{:.4f}".format(full))
ai_wrong = 60 / 180.0 * 5
show("w2 B: AI with annual coupon 60/180 x 5", "{:.4f} -> full {:.4f}".format(ai_wrong, 101.20 + ai_wrong))
show("w2 C: AI subtracted 101.20 - AI", "{:.4f}".format(101.20 - ai))

# ---------------------------------------------------------------- story
print("\n=== Story: Daniels, 4,063 pilots ===")
TOTAL = 4063
left = [("stature (height)", 1055), ("chest circumference", 302), ("sleeve length", 143),
        ("crotch height", 73), ("torso circumference", 28)]
prev = TOTAL
for name, k in left:
    show("after " + name, "{:>5}  = {:.2f}% of 4,063; kept {:.1f}% of previous {}".format(
        k, 100.0 * k / TOTAL, 100.0 * k / prev, prev))
    prev = k
show("share average on height alone", "{:.4f}%  -> about {:.0f}%".format(100.0 * 1055 / TOTAL, 100.0 * 1055 / TOTAL))
show("outside the band on height alone", "{} pilots ({:.0f}%)".format(TOTAL - 1055, 100.0 * (TOTAL - 1055) / TOTAL))

# ---------------------------------------------------------------- the idea
print("\n=== The idea: two funds averaging 5% ===")
A = [5, 5]
B = [30, -20]
show("fund A mean", mean(A))
show("fund B mean (arithmetic)", mean(B))
gB = (1.30 * 0.80) ** 0.5 - 1
show("fund B growth over two years 1.30 x 0.80", "{:.4f}".format(1.30 * 0.80))
show("fund B geometric mean per year", "{!r} -> {:.2f}%".format(100 * gB, 100 * gB))

# ---------------------------------------------------------------- worked example
print("\n=== Worked example: 6, -2, 10, 3, 8 ===")
X = [6, -2, 10, 3, 8]
n = len(X)
m = mean(X)
show("sum", sum(X))
show("mean", m)
show("range = max - min", max(X) - min(X))
dev = [x - m for x in X]
show("deviations", dev)
show("sum of deviations", sum(dev))
show("sum of |deviations|", sum(abs(d) for d in dev))
show("MAD", mad(X))
sq = [d ** 2 for d in dev]
show("squared deviations", sq)
show("sum of squares about the mean", ss(X))
for c in (4, 6):
    show("sum of squares about {} (Why it works)".format(c), "{}  = 88 + 5 x 1^2 = {}".format(ss(X, c), 88 + 5 * (m - c) ** 2))
s2 = sample_var(X)
s_ = math.sqrt(s2)
p2 = pop_var(X)
p_ = math.sqrt(p2)
show("sample variance 88/4", s2)
show("sample SD sqrt(22)", "{!r} -> {:.4f}%".format(s_, s_))
show("population variance 88/5", p2)
show("population SD sqrt(17.6)", "{!r} -> {:.4f}%".format(p_, p_))
cv = s_ / m
show("CV = s / mean", "{!r} -> {:.4f}".format(cv, cv))
dd, dsum = downside(X, 4)
show("downside B = 4: returns <= 4", [x for x in X if x <= 4])
show("  shortfalls (x - 4)", [x - 4 for x in X if x <= 4])
show("  squared shortfalls", [(x - 4) ** 2 for x in X if x <= 4])
show("  sum of squared shortfalls", dsum)
show("  / (5 - 1)", dsum / 4.0)
show("  s_Target(4%)", "{!r} -> {:.4f}%".format(dd, dd))
show("free deviations: last = -(1 - 7 + 5 - 2)", -(1 - 7 + 5 - 2))

# ---------------------------------------------------------------- walkthrough
print("\n=== Calculator walkthrough: 14, -8, 22, 3, -12, 11; B = 0 ===")
F = [14, -8, 22, 3, -12, 11]
nF = len(F)
mF = mean(F)
show("n", "{} -> BA n = {}".format(nF, d4(nF)))
show("x-bar", "{!r} -> {}".format(mF, d4(mF)))
show("sum x", "{} -> {}".format(sum(F), d4(sum(F))))
show("sum x^2", "{} -> {}".format(sum(x * x for x in F), d4(sum(x * x for x in F))))
show("sum of squares about mean", ss(F))
sF2 = sample_var(F)
sF = math.sqrt(sF2)
pF2 = pop_var(F)
pF = math.sqrt(pF2)
show("sample variance (%^2)", "{!r} -> {:.4f}".format(sF2, sF2))
show("Sx (sample SD)", "{!r} -> {}".format(sF, d4(sF)))
show("population variance", "{!r} -> {:.4f}".format(pF2, pF2))
show("sigma x (population SD)", "{!r} -> {}".format(pF, d4(pF)))
cvF = sF / mF
show("CV = Sx / 5", "{!r} -> {}".format(cvF, d4(cvF)))
show("CV from typed 13.1757 / 5", d4(13.1757 / 5))
ddF, dsF = downside(F, 0)
show("returns <= 0", [x for x in F if x <= 0])
show("8^2, 12^2", "{} {}".format(8 ** 2, 12 ** 2))
show("sum of squared shortfalls", dsF)
show("/ (6 - 1)", "{!r} -> {}".format(dsF / 5.0, d4(dsF / 5.0)))
show("s_Target(0%)", "{!r} -> {}".format(ddF, d4(ddF)))
ddF_wrong = math.sqrt(dsF / 2.0)
show("trap: divide by the 2 shortfalls", d4(ddF_wrong))
show("MAD of the fund (Back to the story)", "{!r} -> {:.2f}".format(mad(F), mad(F)))
show("closest any year came to the mean", min(abs(x - mF) for x in F))
show("|x - mean| per year", [abs(x - mF) for x in F])
show("TI-84 trap: (-)8 x^2 is -(8^2)", -(8 ** 2))
show("answer line (2 dp): mean, s, sigma, CV, s_Target", "{:.2f}% {:.2f}% {:.2f}% {:.2f} {:.2f}%".format(mF, sF, pF, cvF, ddF))
show("trap line: s_Target / 2 shortfalls (2 dp)", "{:.2f}%".format(ddF_wrong))
show("Sx^2 from the display 13.1757^2", "{:.4f}".format(13.1757 ** 2))

# ---------------------------------------------------------------- check questions
print("\n=== c1: 11, -6, 15, 2, -4 ===")
C1 = [11, -6, 15, 2, -4]
mC = mean(C1)
show("mean", "{!r} -> {}".format(mC, d4(mC)))
show("deviations", [round(x - mC, 10) for x in C1])
show("sum of squares", "{:.4f}".format(ss(C1)))
c1s = math.sqrt(sample_var(C1))
c1p = math.sqrt(pop_var(C1))
show("Sx (answer)", "{!r} -> {} -> {:.2f}%".format(c1s, d4(c1s), c1s))
show("sigma x (trap)", "{!r} -> {} -> {:.2f}%".format(c1p, d4(c1p), c1p))
show("sum x, sum x^2", "{} {}".format(sum(C1), sum(x * x for x in C1)))
show("squared deviations", [round((x - mC) ** 2, 4) for x in C1])
show("sum of squares / 4 = sample variance", "{:.4f}".format(ss(C1) / 4))
show("BA display n", d4(len(C1)))

print("\n=== c2: why n - 1 (uses the worked example) ===")
show("SS about mean 5 / about 4 / about 6", "{} / {} / {}".format(ss(X), ss(X, 4), ss(X, 6)))

print("\n=== c3: downside B = 3% on c1's returns ===")
c3, c3sum = downside(C1, 3)
show("returns <= 3", [x for x in C1 if x <= 3])
show("shortfalls", [x - 3 for x in C1 if x <= 3])
show("squares", [(x - 3) ** 2 for x in C1 if x <= 3])
show("sum", c3sum)
show("/ (5 - 1)", c3sum / 4.0)
show("s_Target(3%) (answer)", "{!r} -> {} -> {:.2f}%".format(c3, d4(c3), c3))
c3trap = math.sqrt(c3sum / 3.0)
show("trap: / 3 shortfalls", "{:.4f} -> {} -> {:.2f}%".format(c3sum / 3.0, d4(c3trap), c3trap))

print("\n=== c4: 8, -4, 12, 0 ===")
C4 = [8, -4, 12, 0]
show("mean", mean(C4))
show("deviations", [x - mean(C4) for x in C4])
show("sum of squares", ss(C4))
c4s = math.sqrt(sample_var(C4))
c4p = math.sqrt(pop_var(C4))
show("B sample SD sqrt(160/3) (right)", "{!r} -> {:.2f}%".format(c4s, c4s))
show("A population SD sqrt(160/4)", "{!r} -> {:.2f}%".format(c4p, c4p))
show("C sample variance 160/3 (%^2)", "{!r} -> {:.2f}".format(sample_var(C4), sample_var(C4)))
show("BA/TI displays Sx, sigma x", "{} {}".format(d4(c4s), d4(c4p)))
show("sum 16/4; squares 4^2 + 8^2 + 8^2 + 4^2", "{} {}".format(sum(C4) / 4.0, 4 ** 2 + 8 ** 2 + 8 ** 2 + 4 ** 2))
show("by hand: sqrt((16 + 64 + 64 + 16)/3), /4", "{} {}".format(d4(math.sqrt(160 / 3.0)), d4(math.sqrt(160 / 4.0))))
