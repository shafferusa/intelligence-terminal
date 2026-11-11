#!/usr/bin/env python3
"""Day 45 of CFA Math: every number that appears on day-45.html.

Comparing two means: the pooled-variance t-test for independent samples and the
paired-comparisons t-test for dependent samples (CFA Program 2027 Level I,
Quantitative Methods, LM7 Estimation and Hypothesis Testing). Plain Python 3,
standard library only. Run it and copy the printed numbers.

The t-distribution (critical values and p-values, what the TI-84's invT( and
tcdf( and its test screens show) is computed from the regularized incomplete
beta function, so no table or third-party package is needed.
"""

import datetime as dt
import math


# ------------------------------------------------------------ helpers
def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """A TI-84 display in FIX 4: no leading zero before the decimal point."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def mean(xs):
    return sum(xs) / len(xs)


def ss(xs):
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs)


def svar(xs):
    return ss(xs) / (len(xs) - 1)


def sd(xs):
    return math.sqrt(svar(xs))


def scov(xs, ys):
    mx, my = mean(xs), mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)


def show(label, value):
    print("  {:<66} {}".format(label, value))


# --- t-distribution via the regularized incomplete beta function -----------
def _betacf(a, b, x):
    """Continued fraction for the incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return h


def betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log(1.0 - x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def p_two_tail(t, df):
    """P(|T| > |t|) for Student's t with df degrees of freedom."""
    return betai(df / 2.0, 0.5, df / (df + t * t))


def t_cdf(t, df):
    p2 = p_two_tail(t, df)
    return 1.0 - 0.5 * p2 if t >= 0 else 0.5 * p2


def inv_t(p, df):
    """invT(p, df): the t with P(T <= t) = p (bisection)."""
    lo, hi = -100.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def pooled(n1, m1, s1, n2, m2, s2):
    """Pooled variance, standard error, t and df for H0: mu1 = mu2."""
    sp2 = ((n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2) / (n1 + n2 - 2)
    se = math.sqrt(sp2 / n1 + sp2 / n2)
    t = (m1 - m2) / se
    return sp2, se, t, n1 + n2 - 2


# sanity check of the t machinery against well-known table values
assert abs(inv_t(0.975, 10) - 2.228) < 0.001
assert abs(inv_t(0.975, 5) - 2.571) < 0.001
assert abs(inv_t(0.95, 35) - 1.690) < 0.001

# ---------------------------------------------------------------- story
print("=== Story ===")
launch = dt.date(2015, 3, 27)
landing = dt.date(2016, 3, 1)
show("days from 27 Mar 2015 to 1 Mar 2016 (US dates)", (landing - launch).days)
show("gene expression back to baseline within 6 months (NASA)", "91.3%")
show("Science paper published", "12 April 2019")

# ---------------------------------------------------------------- warm-up
print("\n=== Warm-up ===")
# w1 (Day 44): one-sample t-test of a mean
n, xbar, s = 40, 0.7, 2.2
se_w1 = s / math.sqrt(n)
t_w1 = xbar / se_w1
show("w1 SE = 2.2 / sqrt(40)", d4(se_w1))
show("w1 t = 0.7 / SE (answer)", "{!r} -> {}".format(t_w1, d4(t_w1)))
show("w1 df", n - 1)
show("w1 critical t, 39 df, two-tailed 5%  invT(.975,39)", d4(inv_t(0.975, 39)))
show("w1 critical t, 39 df, one-tailed 5%  invT(.95,39)", d4(inv_t(0.95, 39)))
show("w1 p-value two-tailed", d4(p_two_tail(t_w1, 39)))
show("w1 p-value one-tailed (upper)", d4(p_two_tail(t_w1, 39) / 2))

# w2 (Day 38): G-spread with an interpolated government yield
corp, g5, g10, swap6 = 5.10, 3.80, 4.30, 4.05
g6 = g5 + (6 - 5) / (10 - 5) * (g10 - g5)
show("w2 interpolated 6-year government yield", "{:.4f}".format(g6))
show("w2 G-spread (answer B) bp", "{:.1f}".format((corp - g6) * 100))
show("w2 distractor A: I-spread 5.10 - 4.05, bp", "{:.1f}".format((corp - swap6) * 100))
show("w2 distractor C: 5.10 - 3.80 (5-year, no interpolation), bp", "{:.1f}".format((corp - g5) * 100))

# ---------------------------------------------------------------- why it works / idea
print("\n=== Why it works (walkthrough data, used as the illustration) ===")
A = [2.4, -1.8, 3.1, 0.6, -2.9, 1.7]
B = [1.9, -2.1, 2.5, 0.4, -3.5, 1.0]
D = [a - b for a, b in zip(A, B)]
sA2, sB2 = svar(A), svar(B)
cAB = scov(A, B)
show("s_A^2", d4(sA2))
show("s_B^2", d4(sB2))
show("Cov(A, B)", d4(cAB))
show("r(A, B)", d4(cAB / math.sqrt(sA2 * sB2)))
show("s_A^2 + s_B^2 (variance of A - B if they were unrelated)", d4(sA2 + sB2))
show("s_A^2 + s_B^2 - 2 Cov = s_d^2", d4(sA2 + sB2 - 2 * cAB))
show("s_d^2 directly from the differences", d4(svar(D)))
show("sqrt(s_A^2 + s_B^2)", d4(math.sqrt(sA2 + sB2)))
show("s_d", d4(sd(D)))

# ---------------------------------------------------------------- worked example
print("\n=== Worked example ===")
sp2, se, t, df = pooled(24, 1.10, 2.0, 20, 0.60, 1.8)
show("(a) (24 - 1) x 2.0^2", "{:.4f}".format(23 * 2.0 ** 2))
show("(a) (20 - 1) x 1.8^2", "{:.4f}".format(19 * 1.8 ** 2))
show("(a) sum", "{:.4f}".format(23 * 2.0 ** 2 + 19 * 1.8 ** 2))
show("(a) s_p^2 = sum / 42", d4(sp2))
show("(a) s_p^2/24", d4(sp2 / 24))
show("(a) s_p^2/20", d4(sp2 / 20))
show("(a) s_p^2/24 + s_p^2/20", d4(sp2 / 24 + sp2 / 20))
show("(a) SE", d4(se))
show("(a) t", d4(t))
show("(a) df", df)
show("(a) critical +/- invT(.975, 42)", d4(inv_t(0.975, 42)))
show("(a) p-value", d4(p_two_tail(t, df)))
se_b = 0.80 / math.sqrt(20)
t_b = 0.50 / se_b
show("(b) sqrt(20)", d4(math.sqrt(20)))
show("(b) s_d-bar = 0.80 / sqrt(20)", d4(se_b))
show("(b) t = 0.50 / s_d-bar", d4(t_b))
show("(b) critical +/- invT(.975, 19)", d4(inv_t(0.975, 19)))
show("(b) p-value", d4(p_two_tail(t_b, 19)))

# ---------------------------------------------------------------- walkthrough
print("\n=== Calculator walkthrough ===")
show("A", A)
show("B", B)
show("sum A, sum B", "{:.4f}, {:.4f}".format(sum(A), sum(B)))
mA, mB = mean(A), mean(B)
sA, sB = sd(A), sd(B)
r = cAB / (sA * sB)
show("x-bar (A)", d4(mA))
show("Sx (A)", d4(sA))
show("sigma x (A)", d4(math.sqrt(ss(A) / 6)))
show("y-bar (B)", d4(mB))
show("Sy (B)", d4(sB))
show("sigma y (B)", d4(math.sqrt(ss(B) / 6)))
show("r", d4(r))
show("x-bar - y-bar", d4(mA - mB))
sp2w, sew, tw, dfw = pooled(6, mA, sA, 6, mB, sB)
show("s_p^2 = (5 Sx^2 + 5 Sy^2)/10", d4(sp2w))
show("s_p (TI-84 Sxp)", "{} / {}".format(d4(math.sqrt(sp2w)), ti4(math.sqrt(sp2w))))
show("2.3736^2 (squaring the rounded Sxp)", d4(round(math.sqrt(sp2w), 4) ** 2))
show("s_p^2/6 + s_p^2/6", d4(sp2w / 6 + sp2w / 6))
show("SE = sqrt(...)", d4(sew))
show("pooled t", "{} / {}".format(d4(tw), ti4(tw)))
show("pooled df", dfw)
pw = p_two_tail(tw, dfw)
show("pooled p-value (two-tailed)", "{} / {}".format(d4(pw), ti4(pw)))
show("critical invT(.975, 10)", d4(inv_t(0.975, 10)))
# with typed (rounded) displays instead of stored values
sp2_typed = (5 * 2.3945 ** 2 + 5 * 2.3526 ** 2) / 10
show("s_p^2 from typed rounded Sx, Sy", d4(sp2_typed))
show("t from typed rounded values 0.4833/sqrt(sp2/3)", d4(0.4833 / math.sqrt(sp2_typed / 3)))
show("TI home screen: (0.5167 - 0.0333)/sqrt(P/6 + P/6), P typed-rounded",
     d4((0.5167 - 0.0333) / math.sqrt(sp2_typed / 6 + sp2_typed / 6)))
show("x-bar - y-bar from the rounded displays 0.5167 - 0.0333", "{:.4f}".format(0.5167 - 0.0333))
show("TI home screen: .4833/(.1941/sqrt 6) with rounded displays (why we use T-Test)",
     d4(0.4833 / (0.1941 / math.sqrt(6))))

show("differences A - B", [round(x, 10) for x in D])
show("sum of differences", "{:.4f}".format(sum(D)))
md, sdd = mean(D), sd(D)
show("d-bar", "{} / {}".format(d4(md), ti4(md)))
show("s_d", "{} / {}".format(d4(sdd), ti4(sdd)))
show("sigma (population) of differences", d4(math.sqrt(ss(D) / 6)))
sed = sdd / math.sqrt(6)
show("s_d-bar = s_d / sqrt(6)", d4(sed))
tp = md / sed
show("paired t (answer)", "{!r} -> {} / {}".format(tp, d4(tp), ti4(tp)))
show("paired df", 5)
pp = p_two_tail(tp, 5)
show("paired p-value (two-tailed)", "{} / {}".format(d4(pp), ti4(pp)))
show("critical invT(.975, 5)", d4(inv_t(0.975, 5)))
show("ratio paired t / pooled t", d4(tp / tw))
show("ratio SE pooled / SE paired", d4(sew / sed))
# trap: forgetting sqrt(n)
show("trap: d-bar / s_d (forgot to divide by sqrt n)", d4(md / sdd))
# trap: paired data with the wrong df (2n - 2 = 10)
show("trap: paired t judged at 10 df critical", d4(inv_t(0.975, 10)))

# ---------------------------------------------------------------- check questions
print("\n=== Check questions ===")
# c1: paired t, new strategies, same six months
A1 = [1.2, -0.8, 2.6, -1.9, 0.4, 3.0]
B1 = [0.9, -1.0, 2.0, -2.4, 0.3, 2.3]
D1 = [a - b for a, b in zip(A1, B1)]
show("c1 differences", [round(x, 10) for x in D1])
md1, sd1 = mean(D1), sd(D1)
se1 = sd1 / math.sqrt(6)
t1 = md1 / se1
show("c1 d-bar", "{} / {}".format(d4(md1), ti4(md1)))
show("c1 s_d", "{} / {}".format(d4(sd1), ti4(sd1)))
show("c1 s_d-bar", d4(se1))
show("c1 paired t (answer)", "{!r} -> {} / {}".format(t1, d4(t1), ti4(t1)))
show("c1 p-value", "{} / {}".format(d4(p_two_tail(t1, 5)), ti4(p_two_tail(t1, 5))))
show("c1 t from rounded 0.4/(0.2366/sqrt 6)", d4(0.4 / (0.2366 / math.sqrt(6))))
sp2c1, sec1, tc1, _ = pooled(6, mean(A1), sd(A1), 6, mean(B1), sd(B1))
show("c1 (for contrast) pooled t on the same data", d4(tc1))
show("c1 Sx(A1), Sx(B1)", "{}, {}".format(d4(sd(A1)), d4(sd(B1))))
show("c1 r(A1, B1)", d4(scov(A1, B1) / (sd(A1) * sd(B1))))

# c2: conceptual; the walkthrough's numbers illustrate
show("c2 df pooled vs paired (walkthrough)", "10 vs 5")

# c3: pooled t from summary statistics
sp23, se3, t3, df3 = pooled(30, 0.95, 1.6, 25, 0.40, 1.9)
show("c3 29 x 1.6^2", "{:.4f}".format(29 * 1.6 ** 2))
show("c3 24 x 1.9^2", "{:.4f}".format(24 * 1.9 ** 2))
show("c3 sum", "{:.4f}".format(29 * 1.6 ** 2 + 24 * 1.9 ** 2))
show("c3 s_p^2", d4(sp23))
show("c3 s_p^2/30", d4(sp23 / 30))
show("c3 s_p^2/25", d4(sp23 / 25))
show("c3 SE", d4(se3))
show("c3 t (answer)", "{!r} -> {}".format(t3, d4(t3)))
show("c3 s_p (TI-84 Sxp)", "{} / {}".format(d4(math.sqrt(sp23)), ti4(math.sqrt(sp23))))
show("c3 difference in means 0.95 - 0.40", "{:.4f}".format(0.95 - 0.40))
show("c3 df", df3)
show("c3 critical invT(.975, 53)", d4(inv_t(0.975, 53)))
show("c3 p-value", d4(p_two_tail(t3, df3)))
show("c3 t with s_p^2 rounded to 3.0355", d4(0.55 / math.sqrt(3.0355 / 30 + 3.0355 / 25)))
show("c3 t with s_p^2 rounded to 3.04", d4(0.55 / math.sqrt(3.04 / 30 + 3.04 / 25)))
# common slip: unweighted average of the variances
sp_unw = (1.6 ** 2 + 1.9 ** 2) / 2
show("c3 slip: simple average of variances", d4(sp_unw))
show("c3 slip: t with the simple average", d4(0.55 / math.sqrt(sp_unw / 30 + sp_unw / 25)))

# c4: exam item, 36 months, same months
t4p = (1.12 - 0.70) / (1.2 / math.sqrt(36))
show("c4 d-bar = 1.12 - 0.70", "{:.4f}".format(1.12 - 0.70))
show("c4 s_d-bar = 1.2 / 6", d4(1.2 / 6))
show("c4 paired t (answer C)", d4(t4p))
show("c4 paired df", 35)
show("c4 critical invT(.975, 35)", d4(inv_t(0.975, 35)))
sp24, se4, t4, df4 = pooled(36, 1.12, 4.1, 36, 0.70, 3.9)
show("c4 distractor A: pooled s_p^2", d4(sp24))
show("c4 distractor A: pooled SE", d4(se4))
show("c4 distractor A: pooled t", d4(t4))
show("c4 distractor A: pooled df", df4)
show("c4 critical invT(.975, 70)", d4(inv_t(0.975, 70)))
# implied correlation of the two portfolios
cov4 = (4.1 ** 2 + 3.9 ** 2 - 1.2 ** 2) / 2
show("c4 implied Cov = (4.1^2 + 3.9^2 - 1.2^2)/2", d4(cov4))
show("c4 implied r", d4(cov4 / (4.1 * 3.9)))

print("\nDone.")
