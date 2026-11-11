#!/usr/bin/env python3
"""Day 46 of CFA Math: every number that appears on day-46.html.

Chi-square and F tests: the chi-square test of a single variance,
chi2 = (n - 1) s^2 / sigma0^2 with n - 1 df; the F-test of two variances,
F = s1^2 / s2^2 (larger on top) with n1 - 1 and n2 - 1 df; parametric vs
non-parametric tests; and the chi-square test of independence on a
contingency table, E_ij = row total x column total / overall total
(CFA Program 2027 Level I, Quantitative Methods, LM7 Estimation and
Hypothesis Testing). Plain Python 3, standard library only: the F and
chi-square distributions are computed here from the regularized incomplete
beta and gamma functions, and critical values by bisection.
Run it and copy the printed numbers.
"""

import math


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


# --- distributions (standard library only) --------------------------------

def betacf(a, b, x):
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 10000):
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


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log(1.0 - x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * betacf(a, b, x) / a
    return 1.0 - bt * betacf(b, a, 1.0 - x) / b


def gammainc_lower(s, x):
    """Regularized lower incomplete gamma P(s, x)."""
    if x <= 0.0:
        return 0.0
    if x < s + 1.0:
        term = 1.0 / s
        total = term
        n = 0
        while True:
            n += 1
            term *= x / (s + n)
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    # continued fraction for Q(s, x)
    tiny = 1e-300
    b = x + 1.0 - s
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - s)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    q = math.exp(-x + s * math.log(x) - math.lgamma(s)) * h
    return 1.0 - q


def chi2_cdf(x, df):
    return gammainc_lower(df / 2.0, x / 2.0)


def f_cdf(f, d1, d2):
    if f <= 0:
        return 0.0
    return betainc(d1 / 2.0, d2 / 2.0, d1 * f / (d1 * f + d2))


def inverse(cdf, p, lo, hi):
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def chi2_crit(area_below, df):
    return inverse(lambda v: chi2_cdf(v, df), area_below, 0.0, 1000.0)


def f_crit(area_below, d1, d2):
    return inverse(lambda v: f_cdf(v, d1, d2), area_below, 0.0, 1000.0)


# --- sanity checks against published table values ---------------------------
assert abs(chi2_crit(0.95, 1) - 3.841) < 0.0006
assert abs(chi2_crit(0.95, 20) - 31.410) < 0.0006
assert abs(chi2_crit(0.05, 20) - 10.851) < 0.0006
assert abs(f_crit(0.95, 20, 20) - 2.12) < 0.006
assert abs(f_crit(0.975, 24, 24) - 2.27) < 0.006
# Day 44's t p-value as a cross-check of betainc: tcdf(1.92, inf, 35) = .0315
t, df = 1.92, 35
p_t = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
assert abs(p_t - 0.0315) < 0.00005, p_t

print("Day 46 numbers")

# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 45): pooled variance
n1, s1, n2, s2 = 16, 3.0, 21, 2.0
num1 = (n1 - 1) * s1 ** 2
num2 = (n2 - 1) * s2 ** 2
den = n1 + n2 - 2
sp2 = (num1 + num2) / den
show("w1 (n1-1) s1^2 = 15 x 9 ; (n2-1) s2^2 = 20 x 4", "{:.0f} ; {:.0f}".format(num1, num2))
show("w1 sum ; denominator n1 + n2 - 2", "{:.0f} ; {}".format(num1 + num2, den))
show("w1 pooled variance s_p^2 (answer, %^2)", "{!r} -> BA {} / TI {}".format(sp2, d4(sp2), ti4(sp2)))
show("w1 trap: plain average of variances (9 + 4)/2", "{:.4f}".format((s1 ** 2 + s2 ** 2) / 2))
show("w1 trap: divided by n1 + n2 = 37", "{:.4f}".format((num1 + num2) / (n1 + n2)))

# w2 (Day 39): modified duration of a zero
T, y = 6, 0.05
macdur = T
moddur = macdur / (1 + y)
show("w2 MacDur of a 6-year zero", macdur)
show("w2 ModDur = 6 / 1.05 (answer C)", "{!r} -> {}".format(moddur, d4(moddur)))
show("w2 trap: 6 x 1.05", "{:.2f}".format(macdur * (1 + y)))

# ---------------------------------------------------------------------------
print("=== Story ===")
v0, v1 = 17.31, 37.32
chg = v1 / v0 - 1
show("VIX change 17.31 -> 37.32", "{:.4%}".format(chg))
r252 = math.sqrt(252)
show("sqrt(252)", "{:.4f}".format(r252))
sd0, sd1 = v0 / r252, v1 / r252
show("implied daily SD before = 17.31 / sqrt(252) (%)", "{:.4f}".format(sd0))
show("implied daily SD after  = 37.32 / sqrt(252) (%)", "{:.4f}".format(sd1))
show("variances before ; after (%^2)", "{:.4f} ; {:.4f}".format(sd0 ** 2, sd1 ** 2))
F_story = (v1 / v0) ** 2
show("F = (37.32/17.31)^2 = sd1^2/sd0^2", "{:.4f} (check {:.4f})".format(F_story, sd1 ** 2 / sd0 ** 2))
show("SD ratio 37.32/17.31", "{:.4f}".format(v1 / v0))
fc_story = f_crit(0.95, 20, 20)
show("F crit 5% one tail, (20, 20)", "{:.4f}".format(fc_story))
p_story = 1 - f_cdf(F_story, 20, 20)
show("p-value Fcdf(4.6482, 1E99, 20, 20)", "{:.3e} -> TI {}".format(p_story, ti4(p_story)))
chi_story = 20 * F_story
show("hypothetical chi2 = 20 x 4.6482 (sigma0 = pre-shock daily SD)", "{:.4f}".format(chi_story))
show("chi2 crit 5% upper, 20 df", "{:.4f}".format(chi2_crit(0.95, 20)))

# ---------------------------------------------------------------------------
print("=== The idea ===")
show("same gap of 3: 4 - 1 ; 104 - 101", "{} ; {}".format(4 - 1, 104 - 101))
show("ratios 4/1 ; 104/101", "{:.4f} ; {:.4f}".format(4 / 1, 104 / 101))

print("=== Why it works ===")
show("chi2 when s^2 = sigma0^2, n = 21: (n-1) x 1", 20)
show("chi2(20) lower 5% ; upper 5%", "{:.3f} ; {:.3f}".format(chi2_crit(0.05, 20), chi2_crit(0.95, 20)))
show("  distances from the mean 20", "{:.3f} below ; {:.3f} above".format(20 - chi2_crit(0.05, 20), chi2_crit(0.95, 20) - 20))
show("F(20,30) lower 5% = 1 / F(30,20) upper 5%",
     "{:.4f} = 1/{:.4f} = {:.4f}".format(f_crit(0.05, 20, 30), f_crit(0.95, 30, 20), 1 / f_crit(0.95, 30, 20)))

# ---------------------------------------------------------------------------
print("=== Worked example ===")
sA, sB, nA, nB = 5.2, 3.9, 25, 25
vA, vB = sA ** 2, sB ** 2
Fw = vA / vB
show("(a) 5.2^2 ; 3.9^2", "{:.2f} ; {:.2f}".format(vA, vB))
show("(a) F = 27.04 / 15.21", "{!r} -> {:.4f}".format(Fw, Fw))
fcw = f_crit(0.975, 24, 24)
show("(a) F crit 2.5% upper (24, 24)", "{:.4f}".format(fcw))
show("(a) two-tailed p-value", "{:.4f}".format(2 * (1 - f_cdf(Fw, 24, 24))))
show("(a) SD ratio 5.2/3.9 (trap)", "{:.4f}".format(sA / sB))

O = [[30, 20], [28, 42]]  # rows: value, growth; cols: beat, missed
rows = [sum(r) for r in O]
cols = [sum(O[i][j] for i in range(2)) for j in range(2)]
N = sum(rows)
show("(b) row totals ; column totals ; overall", "{} ; {} ; {}".format(rows, cols, N))
E = [[rows[i] * cols[j] / N for j in range(2)] for i in range(2)]
comps = []
for i, rn in enumerate(["value", "growth"]):
    for j, cn in enumerate(["beat", "missed"]):
        c = (O[i][j] - E[i][j]) ** 2 / E[i][j]
        comps.append(c)
        show("(b) {}-{}: O = {}, E = {} x {}/{}".format(rn, cn, O[i][j], rows[i], cols[j], N),
             "E = {:.4f}; O - E = {:+.4f}; (O-E)^2 = {:.4f}; /E = {:.4f}".format(
                 E[i][j], O[i][j] - E[i][j], (O[i][j] - E[i][j]) ** 2, c))
chi_w = sum(comps)
show("(b) chi2 = sum", "{!r} -> {:.4f}".format(chi_w, chi_w))
show("(b) sum of the four rounded components", "{:.4f}".format(sum(round(c, 4) for c in comps)))
show("(b) df = (2-1)(2-1)", 1)
show("(b) chi2 crit 5%, 1 df", "{:.4f}".format(chi2_crit(0.95, 1)))
show("(b) p-value", "{:.4f}".format(1 - chi2_cdf(chi_w, 1)))
show("(b) beat rates: value 30/50 ; growth 28/70 ; all 58/120",
     "{:.1%} ; {:.1%} ; {:.2%}".format(30 / 50, 28 / 70, 58 / 120))

# ---------------------------------------------------------------------------
print("=== Walkthrough ===")
s_aft, n_aft, s_bef, n_bef, sig0 = 2.0, 21, 1.1, 31, 1.5
va, vb = s_aft ** 2, s_bef ** 2
Fk = va / vb
show("variances: 2.0^2 ; 1.1^2", "{:.4f} ; {:.4f}".format(va, vb))
show("F = 4 / 1.21", "{!r} -> BA {} / TI {}".format(Fk, d4(Fk), ti4(Fk)))
show("df (20, 30)", (n_aft - 1, n_bef - 1))
fck = f_crit(0.95, 20, 30)
show("F crit 5% one tail (20, 30)", "{:.4f}".format(fck))
pF = 1 - f_cdf(Fk, 20, 30)
show("p = Fcdf(F, 1E99, 20, 30)", "{!r} -> TI {}".format(pF, ti4(pF)))
pF_r = 1 - f_cdf(round(Fk, 4), 20, 30)
show("p with F typed as 3.3058", "{:.6f} -> TI {}".format(pF_r, ti4(pF_r)))
show("trap: SD ratio 2/1.1", "{:.4f}".format(s_aft / s_bef))
show("trap: smaller on top 1.21/4", "{:.4f}".format(vb / va))
chik = (n_aft - 1) * va / sig0 ** 2
show("1.5^2", "{:.4f}".format(sig0 ** 2))
show("20 x 4 = 80 ; chi2 = 80 / 2.25", "{:.0f} ; {!r} -> BA {} / TI {}".format((n_aft - 1) * va, chik, d4(chik), ti4(chik)))
cck = chi2_crit(0.95, 20)
show("chi2 crit 5% upper, 20 df", "{:.4f}".format(cck))
pC = 1 - chi2_cdf(chik, 20)
show("p = chi2cdf(chi2, 1E99, 20)", "{!r} -> TI {}".format(pC, ti4(pC)))
pC_r = 1 - chi2_cdf(round(chik, 4), 20)
show("p with chi2 typed as 35.5556", "{:.6f} -> TI {}".format(pC_r, ti4(pC_r)))
show("s^2 / sigma0^2 = 4 / 2.25 ; times 20", "{:.4f} ; {:.4f}".format(va / sig0 ** 2, 20 * va / sig0 ** 2))

# ---------------------------------------------------------------------------
print("=== Check questions ===")
# c1
a1, m1, b1, k1 = 2.4, 16, 1.5, 21
F1 = a1 ** 2 / b1 ** 2
show("c1 2.4^2 ; 1.5^2", "{:.4f} ; {:.4f}".format(a1 ** 2, b1 ** 2))
show("c1 F (answer)", "{!r} -> BA {} / TI {}".format(F1, d4(F1), ti4(F1)))
show("c1 df", (m1 - 1, k1 - 1))
show("c1 F crit 5% one tail (15, 20)", "{:.4f}".format(f_crit(0.95, 15, 20)))
p1 = 1 - f_cdf(F1, 15, 20)
show("c1 p = Fcdf(2.56, 1E99, 15, 20)", "{!r} -> TI {}".format(p1, ti4(p1)))
show("c1 trap: SD ratio 2.4/1.5", "{:.4f}".format(a1 / b1))

# c3
n3, s3, sig3 = 24, 1.2, 1.5
chi3 = (n3 - 1) * s3 ** 2 / sig3 ** 2
show("c3 23 x 1.44 = ; / 2.25", "{:.4f} ; {!r} -> BA {} / TI {}".format((n3 - 1) * s3 ** 2, chi3, d4(chi3), ti4(chi3)))
show("c3 chi2(23) lower 5% ; upper 5%", "{:.4f} ; {:.4f}".format(chi2_crit(0.05, 23), chi2_crit(0.95, 23)))
p3 = chi2_cdf(chi3, 23)
show("c3 p = chi2cdf(0, 14.72, 23)", "{!r} -> TI {}".format(p3, ti4(p3)))
show("c3 s^2/sigma0^2 = 1.44/2.25", "{:.4f}".format(s3 ** 2 / sig3 ** 2))
show("c3 trap: n instead of n - 1: 24 x 1.44/2.25", "{:.4f}".format(n3 * s3 ** 2 / sig3 ** 2))
show("c3 trap: SDs: 23 x 1.2/1.5", "{:.4f}".format((n3 - 1) * s3 / sig3))

# c4
sa4, na4, sb4, nb4 = 4.2, 25, 3.0, 31
F4 = sa4 ** 2 / sb4 ** 2
show("c4 4.2^2 ; 3.0^2", "{:.2f} ; {:.2f}".format(sa4 ** 2, sb4 ** 2))
show("c4 F (right, A)", "{!r} -> {:.4f}".format(F4, F4))
show("c4 SD ratio (B)", "{:.4f}".format(sa4 / sb4))
show("c4 smaller on top (C)", "{:.4f}".format(sb4 ** 2 / sa4 ** 2))
show("c4 F crit 2.5% upper (24, 30)", "{:.4f}".format(f_crit(0.975, 24, 30)))
show("c4 two-tailed p", "{:.4f}".format(2 * (1 - f_cdf(F4, 24, 30))))

# c2 support: the lower critical value via the reciprocal rule, walkthrough df
lo = f_crit(0.025, 20, 30)
show("c2 F(20,30) lower 2.5% ; 1 / F(30,20) upper 2.5%",
     "{:.4f} ; {:.4f}".format(lo, 1 / f_crit(0.975, 30, 20)))
show("c2 F(20,30) upper 2.5%", "{:.4f}".format(f_crit(0.975, 20, 30)))
show("c2 distances from 1: below ; above", "{:.4f} ; {:.4f}".format(1 - lo, f_crit(0.975, 20, 30) - 1))
show("c3 chi2 / df = 14.72 / 23", "{:.4f}".format(chi3 / 23))

print("=== Traps ===")
show("trap n instead of n - 1 (walkthrough): 21 x 4 / 2.25", "{:.4f}".format(21 * 4 / 2.25))
show("trap SDs in chi2 (walkthrough): 20 x 2 / 1.5", "{:.4f}".format(20 * 2 / 1.5))
