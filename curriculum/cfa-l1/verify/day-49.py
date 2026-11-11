#!/usr/bin/env python3
"""Day 49 of CFA Math: every number that appears on day-49.html.

Week 7 review (Days 43-48): standard error and confidence intervals, the
t-test of a mean, Type I / Type II errors and the p-value, pooled and paired
t-tests, the chi-square test of a variance and the F-test of two variances,
the least-squares slope and intercept, and the regression ANOVA (R^2, SEE, F)
(CFA Program 2027 Level I, Quantitative Methods, LM7 Estimation and
Hypothesis Testing; LM10 Applications of Simple Linear Regression in
Finance). Warm-up from Day 48 (SEE) and Day 26 (total probability rule).
Plain Python 3, standard library only: the t, F and chi-square distributions
are computed here from the regularized incomplete beta and gamma functions,
and critical values by bisection. Run it and copy the printed numbers.
"""

import math


def show(label, value):
    print("  {:<72} {}".format(label, value))


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
    return "{!r} -> BA {} / TI {}".format(x, d4(x), ti4(x))


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


def t_upper(t, df):
    """P(T > t) for t >= 0: tcdf(t, 1E99, df)."""
    return 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))


def t_cdf(t, df):
    return 1.0 - t_upper(t, df) if t >= 0 else t_upper(-t, df)


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


def t_crit(area_below, df):
    """invT(area, df)."""
    return inverse(lambda v: t_cdf(v, df), area_below, -100.0, 100.0)


def chi2_crit(area_below, df):
    return inverse(lambda v: chi2_cdf(v, df), area_below, 0.0, 1000.0)


def f_crit(area_below, d1, d2):
    return inverse(lambda v: f_cdf(v, d1, d2), area_below, 0.0, 1000.0)


def one_var(xs):
    """What 1-V (BA II Plus) / 1-Var Stats (TI-84) show."""
    n = len(xs)
    m = sum(xs) / n
    ss = sum((x - m) ** 2 for x in xs)
    return {"n": n, "sum": sum(xs), "mean": m, "ss": ss,
            "Sx": math.sqrt(ss / (n - 1)), "sx": math.sqrt(ss / n)}


def two_var(xs, ys):
    """What LIN (BA II Plus) / LinReg(a+bx) (TI-84) show."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    r = sxy / math.sqrt(sxx * syy)
    b = sxy / sxx
    return {"n": n, "mx": mx, "my": my, "sxx": sxx, "syy": syy, "sxy": sxy,
            "Sx": math.sqrt(sxx / (n - 1)), "Sy": math.sqrt(syy / (n - 1)),
            "r": r, "b": b, "a": my - b * mx, "cov": sxy / (n - 1)}


# --- sanity checks against published table values ---------------------------
assert abs(t_crit(0.975, 7) - 2.365) < 0.0006
assert abs(t_crit(0.975, 15) - 2.131) < 0.0006
assert abs(t_crit(0.95, 24) - 1.711) < 0.0006
assert abs(chi2_crit(0.95, 20) - 31.410) < 0.0006
assert abs(f_crit(0.95, 20, 30) - 1.93) < 0.006
assert abs(t_upper(1.92, 35) - 0.0315) < 0.00005

print("Day 49 numbers")

# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 48): SEE from SST and SSR
n_w, SST_w, SSR_w = 27, 0.0900, 0.0360
SSE_w = SST_w - SSR_w
MSE_w = SSE_w / (n_w - 2)
SEE_w = math.sqrt(MSE_w)
show("w1 SSE = 0.0900 - 0.0360", "{:.4f}".format(SSE_w))
show("w1 n - 2", n_w - 2)
show("w1 MSE = SSE / 25 (BA shows 4 decimals)", both(MSE_w))
show("w1 SEE = sqrt(MSE) (answer)", both(SEE_w))
show("w1 R^2 = SSR / SST", "{:.4f}".format(SSR_w / SST_w))
show("w1 trap n - 1: sqrt(0.054 / 26)", "{:.4f}".format(math.sqrt(SSE_w / (n_w - 1))))
show("w1 trap SSR for SSE: sqrt(0.036 / 25)", "{:.4f}".format(math.sqrt(SSR_w / (n_w - 2))))

# w2 (Day 26): total probability rule for expected EPS
p_exp, p_rec = 0.60, 0.40
eps_hi, p_hi, eps_lo, p_lo = 3.20, 0.75, 2.40, 0.25
eps_rec = 1.50
e_exp = p_hi * eps_hi + p_lo * eps_lo
e_all = p_exp * e_exp + p_rec * eps_rec
show("w2 E(EPS | expansion) = 0.75 x 3.20 + 0.25 x 2.40", "{:.4f} ({:.2f} + {:.2f})".format(e_exp, p_hi * eps_hi, p_lo * eps_lo))
show("w2 E(EPS) = 0.60 x 3.00 + 0.40 x 1.50 (right, B)", "{:.4f} ({:.2f} + {:.2f})".format(e_all, p_exp * e_exp, p_rec * eps_rec))
trapA = p_hi * eps_hi + p_lo * eps_lo + p_rec * eps_rec
show("w2 A: 0.75 x 3.20 + 0.25 x 2.40 + 0.40 x 1.50 (weights sum 1.40)", "{:.4f}; weights {:.2f}".format(trapA, p_hi + p_lo + p_rec))
trapC = p_exp * (eps_hi + eps_lo) / 2 + p_rec * eps_rec
show("w2 C: expansion outcomes averaged equally: 0.60 x 2.80 + 0.40 x 1.50", "{:.4f}".format(trapC))
show("w2 joint probabilities 0.60 x 0.75 ; 0.60 x 0.25 ; 0.40", "{:.2f} ; {:.2f} ; {:.2f} (sum {:.2f})".format(
    p_exp * p_hi, p_exp * p_lo, p_rec, p_exp * p_hi + p_exp * p_lo + p_rec))
show("w2 check by joint: 0.45 x 3.20 + 0.15 x 2.40 + 0.40 x 1.50", "{:.4f}".format(
    p_exp * p_hi * eps_hi + p_exp * p_lo * eps_lo + p_rec * eps_rec))

# ---------------------------------------------------------------------------
print("=== Worked example (recap) ===")
n_e, m_e, s_e, t_e = 36, 0.7, 1.8, 2.030
se_e = s_e / math.sqrt(n_e)
half_e = t_e * se_e
show("SE = 1.8 / sqrt(36) = 1.8 / 6", "{:.4f}".format(se_e))
show("half-width 2.030 x 0.30", "{:.4f}".format(half_e))
show("interval", "{:.4f} to {:.4f}".format(m_e - half_e, m_e + half_e))
show("t = 0.7 / 0.30", "{:.4f}".format(m_e / se_e))
show("invT(.975, 35)", "{:.4f}".format(t_crit(0.975, 35)))
show("two-tailed p for t = 2.3333, 35 df", "{:.4f}".format(2 * t_upper(m_e / se_e, 35)))

# ---------------------------------------------------------------------------
print("=== Walkthrough ===")
R = [1.2, -0.4, 2.1, 0.8, -1.1, 1.5, 0.3, 0.9]
st = one_var(R)
show("n ; sum", "{} ; {:.4f}".format(st["n"], st["sum"]))
show("mean x-bar", both(st["mean"]))
show("Sx (n - 1)", both(st["Sx"]))
show("sigma x (n), the trap", both(st["sx"]))
show("sqrt(8)", d4(math.sqrt(8)))
se_w = st["Sx"] / math.sqrt(8)
show("SE = Sx / sqrt(8)", both(se_w))
t_w = st["mean"] / se_w
show("t = x-bar / SE (answer)", both(t_w))
show("t with rounded displays 0.6625 / (1.0350 / sqrt 8)", "{:.4f}".format(0.6625 / (1.0350 / math.sqrt(8))))
p_w = 2 * t_upper(t_w, 7)
show("two-tailed p (TI T-Test)", both(p_w))
tc7 = t_crit(0.975, 7)
show("invT(.975, 7)", both(tc7))
show("one-tailed 5% critical, 7 df: invT(.95, 7)", "{:.4f}".format(t_crit(0.95, 7)))
lo_tab, hi_tab = st["mean"] - 2.365 * se_w, st["mean"] + 2.365 * se_w
show("BA interval with table 2.365: half-width", "{:.6f}".format(2.365 * se_w))
show("BA interval lower ; upper", "{} ; {}".format(d4(lo_tab), d4(hi_tab)))
lo_ex, hi_ex = st["mean"] - tc7 * se_w, st["mean"] + tc7 * se_w
show("TI TInterval (exact t) lower ; upper", "{!r} ; {!r} -> TI ({},{})".format(lo_ex, hi_ex, ti4(lo_ex), ti4(hi_ex)))
show("trap sigma x: t = x-bar / (sigma x / sqrt 8)", "{:.4f}".format(st["mean"] / (st["sx"] / math.sqrt(8))))

# ---------------------------------------------------------------------------
print("=== Check questions ===")
# c1 (Day 43): upper limit of a 95% interval
n1, m1, s1, t1 = 20, 1.1, 3.2, 2.093
se1 = s1 / math.sqrt(n1)
show("c1 sqrt(20)", d4(math.sqrt(n1)))
show("c1 SE = 3.2 / sqrt(20)", both(se1))
show("c1 half-width 2.093 x SE", "{:.4f}".format(t1 * se1))
up1, lo1 = m1 + t1 * se1, m1 - t1 * se1
show("c1 upper limit (answer)", both(up1))
show("c1 lower limit", both(lo1))
show("c1 invT(.975, 19)", "{:.4f}".format(t_crit(0.975, 19)))
tc19 = t_crit(0.975, 19)
show("c1 TI TInterval (exact t)", "({}, {})".format(ti4(m1 - tc19 * se1), ti4(m1 + tc19 * se1)))
show("c1 trap z 1.96: upper", "{:.4f}".format(m1 + 1.96 * se1))
show("c1 trap SE = s / n: upper", "{:.4f}".format(m1 + t1 * s1 / n1))

# c2 (Day 43): reliability factor 90%, sigma unknown, n = 12
show("c2 t 5% per tail, 11 df (A, right)", "{:.4f}".format(t_crit(0.95, 11)))
show("c2 t 5% per tail, 12 df (C, df = n)", "{:.4f}".format(t_crit(0.95, 12)))
show("c2 z 5% per tail (B)", "{:.4f}".format(inverse(lambda v: 0.5 * (1 + math.erf(v / math.sqrt(2))), 0.95, -10, 10)))
show("c2 t 2.5% per tail, 11 df (a 95% factor, for reference)", "{:.4f}".format(t_crit(0.975, 11)))

# c3 (Day 44): t-statistic of a mean excess return
n3, m3, s3 = 64, 0.45, 1.6
se3 = s3 / math.sqrt(n3)
t3 = m3 / se3
show("c3 SE = 1.6 / 8", "{:.4f}".format(se3))
show("c3 t (answer)", both(t3))
show("c3 invT(.975, 63) ; invT(.995, 63)", "{:.4f} ; {:.4f}".format(t_crit(0.975, 63), t_crit(0.995, 63)))
show("c3 two-tailed p", "{:.4f}".format(2 * t_upper(t3, 63)))
show("c3 trap: s / n as SE: 0.45 / (1.6/64)", "{:.4f}".format(m3 / (s3 / n3)))

# c5 (Day 45): paired t on six differences
D = [0.8, -0.2, 1.1, 0.5, 0.9, 0.3]
sd = one_var(D)
show("c5 sum ; mean d-bar", "{:.4f} ; {}".format(sd["sum"], both(sd["mean"])))
show("c5 squared deviations sum", "{:.6f}".format(sd["ss"]))
show("c5 s_d (Sx)", both(sd["Sx"]))
show("c5 sigma x (trap)", both(sd["sx"]))
se5 = sd["Sx"] / math.sqrt(6)
show("c5 sqrt(6) ; SE = s_d / sqrt 6", "{} ; {}".format(d4(math.sqrt(6)), both(se5)))
t5 = sd["mean"] / se5
show("c5 paired t (answer)", both(t5))
show("c5 two-tailed p (TI T-Test)", both(2 * t_upper(t5, 5)))
show("c5 invT(.975, 5)", "{:.4f}".format(t_crit(0.975, 5)))
show("c5 trap sigma x: t", "{:.4f}".format(sd["mean"] / (sd["sx"] / math.sqrt(6))))
show("c5 t from rounded 0.5667 / (0.4719 / sqrt 6)", "{:.4f}".format(0.5667 / (0.4719 / math.sqrt(6))))

# c6 (Day 45): pooled t from summary statistics
na, ma, sa, nb, mb, sb = 15, 1.4, 2.2, 12, 0.6, 1.8
num_a, num_b = (na - 1) * sa ** 2, (nb - 1) * sb ** 2
sp2 = (num_a + num_b) / (na + nb - 2)
show("c6 14 x 2.2^2 ; 11 x 1.8^2", "{:.4f} ; {:.4f}".format(num_a, num_b))
show("c6 sum ; df", "{:.4f} ; {}".format(num_a + num_b, na + nb - 2))
show("c6 pooled variance s_p^2", both(sp2))
show("c6 s_p (TI Sxp)", both(math.sqrt(sp2)))
inside6 = sp2 / na + sp2 / nb
show("c6 s_p^2/15 + s_p^2/12", both(inside6))
se6 = math.sqrt(inside6)
show("c6 SE = sqrt(...)", both(se6))
t6 = (ma - mb) / se6
show("c6 pooled t (answer)", both(t6))
show("c6 two-tailed p, 25 df", both(2 * t_upper(t6, 25)))
show("c6 invT(.975, 25)", "{:.4f}".format(t_crit(0.975, 25)))
show("c6 trap: divide by n1 + n2 = 27 -> s_p^2 ; t", "{:.4f} ; {:.4f}".format(
    (num_a + num_b) / (na + nb), (ma - mb) / math.sqrt((num_a + num_b) / (na + nb) * (1 / na + 1 / nb))))

# c7 (Day 46): F-test of two variances
s71, n71, s72, n72 = 3.0, 21, 2.0, 16
F7 = s71 ** 2 / s72 ** 2
show("c7 F = 9 / 4 (C right: fail to reject)", "{:.4f}".format(F7))
show("c7 SD ratio (A)", "{:.4f}".format(s71 / s72))
show("c7 F crit 5% upper (20, 15)", "{:.4f}".format(f_crit(0.95, n71 - 1, n72 - 1)))
show("c7 F crit 5% upper (15, 20) (B, df reversed)", "{:.4f}".format(f_crit(0.95, n72 - 1, n71 - 1)))
show("c7 p = Fcdf(2.25, 1E99, 20, 15)", "{:.4f}".format(1 - f_cdf(F7, 20, 15)))

# c8 (Day 46): chi-square test of a variance
n8, s8, sig8 = 31, 2.4, 2.0
chi8 = (n8 - 1) * s8 ** 2 / sig8 ** 2
show("c8 30 x 2.4^2 ; / 2.0^2", "{:.4f} ; {}".format((n8 - 1) * s8 ** 2, both(chi8)))
show("c8 chi2 crit 5% upper, 30 df", "{:.4f}".format(chi2_crit(0.95, 30)))
show("c8 p = chi2cdf(43.2, 1E99, 30)", both(1 - chi2_cdf(chi8, 30)))
show("c8 trap SDs: 30 x 2.4 / 2.0", "{:.4f}".format((n8 - 1) * s8 / sig8))
show("c8 trap n: 31 x 5.76 / 4", "{:.4f}".format(n8 * s8 ** 2 / sig8 ** 2))
show("c8 s^2 / sigma0^2", "{:.4f}".format(s8 ** 2 / sig8 ** 2))

# c9 (Day 47): least-squares slope
X9 = [1.0, 2.5, -0.5, 3.0, 1.5]
Y9 = [2.0, 3.1, 0.2, 4.4, 2.3]
lr = two_var(X9, Y9)
show("c9 x-bar ; y-bar", "{} ; {}".format(d4(lr["mx"]), d4(lr["my"])))
show("c9 Sx ; Sy", "{} ; {}".format(d4(lr["Sx"]), d4(lr["Sy"])))
dx = [x - lr["mx"] for x in X9]
dy = [y - lr["my"] for y in Y9]
show("c9 X - X-bar", ", ".join("{:.2f}".format(v) for v in dx))
show("c9 Y - Y-bar", ", ".join("{:.2f}".format(v) for v in dy))
show("c9 products", ", ".join("{:.2f}".format(a * b) for a, b in zip(dx, dy)))
show("c9 squares of X deviations", ", ".join("{:.2f}".format(a * a) for a in dx))
show("c9 Sxx ; Sxy (sums of squares / cross products)", "{:.4f} ; {:.4f}".format(lr["sxx"], lr["sxy"]))
show("c9 slope b (answer)", both(lr["b"]))
show("c9 intercept a", both(lr["a"]))
show("c9 r ; r^2", "{} ; {}".format(both(lr["r"]), both(lr["r"] ** 2)))
show("c9 Cov ; Var(X)", "{:.4f} ; {:.4f}".format(lr["cov"], lr["Sx"] ** 2))
show("c9 check: r x Sy / Sx", "{:.4f}".format(lr["r"] * lr["Sy"] / lr["Sx"]))
swap = two_var(Y9, X9)
show("c9 trap X and Y swapped: slope", "{:.4f}".format(swap["b"]))

# c10 (Day 48): regression F-statistic
n10, SST10, SSE10 = 50, 0.0800, 0.0560
SSR10 = SST10 - SSE10
MSE10 = SSE10 / (n10 - 2)
F10 = SSR10 / MSE10
show("c10 SSR = SST - SSE", "{:.4f}".format(SSR10))
show("c10 MSE = 0.056 / 48", both(MSE10))
show("c10 F (answer)", both(F10))
show("c10 shortcut (SSR/SSE) x 48", "{:.4f}".format(SSR10 / SSE10 * 48))
show("c10 R^2 ; SEE", "{:.4f} ; {:.4f}".format(SSR10 / SST10, math.sqrt(MSE10)))
show("c10 F crit 5% (1, 48)", "{:.4f}".format(f_crit(0.95, 1, 48)))
p10 = 1 - f_cdf(F10, 1, 48)
show("c10 p = Fcdf(F, 1E99, 1, 48) (page says: below 0.001)", "{:.4e} (< 0.001: {})".format(p10, p10 < 0.001))
show("c10 trap n - 1: 0.024 / (0.056 / 49)", "{:.4f}".format(SSR10 / (SSE10 / 49)))
show("c10 trap SST on top: 0.08 / MSE", "{:.4f}".format(SST10 / MSE10))

# ---------------------------------------------------------------------------
print("=== Traps ===")
show("n - 1 vs n - 2: c10 F with 49 ; with 48", "{:.4f} ; {:.4f}".format(SSR10 / (SSE10 / 49), F10))
show("one-tailed vs two-tailed at 5%, 7 df (walkthrough t = 1.8104)",
     "{:.4f} vs {:.4f}".format(t_crit(0.95, 7), t_crit(0.975, 7)))
show("one-tailed vs two-tailed at 5%, 63 df (c3 t = 2.25)",
     "{:.4f} vs {:.4f}".format(t_crit(0.95, 63), t_crit(0.975, 63)))
show("c7 smaller variance on top: 4 / 9", "{:.4f}".format(s72 ** 2 / s71 ** 2))
