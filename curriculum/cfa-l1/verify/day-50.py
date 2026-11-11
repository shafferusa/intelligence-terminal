#!/usr/bin/env python3
"""Day 50 of CFA Math: every number that appears on day-50.html.

Testing the slope; beta by regression: t = (b1 - B1) / s_b1 with n - 2 df,
s_b1 = SEE / sqrt(sum (X - Xbar)^2); in simple regression the slope test of
B1 = 0 equals the correlation test t = r sqrt(n - 2) / sqrt(1 - r^2), and
F = t^2; regressing a stock's returns on the market's gives its beta, the
intercept is alpha (CFA Program 2027 Level I, Quantitative Methods, LM10
Applications of Simple Linear Regression in Finance).
Story: Fama and French (1992), Black (1993); the six months are illustrative.
Plain Python 3, standard library only: Student's t comes from the regularized
incomplete beta function, critical values by bisection.
Run it and copy the printed numbers.
"""

import math


def show(label, value):
    print("  {:<74} {}".format(label, value))


def d4(x):
    """A 4-decimal BA II Plus display (thousands separators, leading zero)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """TI-84 in Fix 4, Normal mode: below .001 in size it shows scientific
    notation with a 4-decimal mantissa; otherwise 4 decimals, no leading 0."""
    if x != 0 and abs(x) < 0.001:
        e = int(math.floor(math.log10(abs(x))))
        m = x / 10 ** e
        if abs(round(m, 4)) >= 10:
            m /= 10
            e += 1
        return "{:.4f}E{}".format(m, e)
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


# --- Student's t (standard library only) ------------------------------------

def betacf(a, b, x):
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


def t_upper(t, df):
    """Area above t: what tcdf(t, 1E99, df) returns on the TI-84."""
    x = df / (df + t * t)
    half = 0.5 * betainc(df / 2.0, 0.5, x)
    return half if t >= 0 else 1.0 - half


def t_two(t, df):
    return 2.0 * t_upper(abs(t), df)


def t_crit(area_below, df):
    """invT(area_below, df)."""
    lo, hi = -100.0, 100.0
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if 1.0 - t_upper(mid, df) < area_below:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# sanity checks against published t-table values
assert abs(t_crit(0.975, 4) - 2.776) < 0.0006
assert abs(t_crit(0.975, 5) - 2.571) < 0.0006
assert abs(t_crit(0.975, 30) - 2.042) < 0.0006
assert abs(t_crit(0.975, 60) - 2.000) < 0.0006
assert abs(t_crit(0.95, 35) - 1.690) < 0.0006


# --- regression helpers -----------------------------------------------------

def mean(v):
    return sum(v) / len(v)


def svar(v):
    m = mean(v)
    return sum((x - m) ** 2 for x in v) / (len(v) - 1)


def fit(x, y):
    """Least squares with the slope test: b0, b1, r, SEE, s_b1, t's."""
    n = len(x)
    mx, my = mean(x), mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    syy = sum((b - my) ** 2 for b in y)
    b1 = sxy / sxx
    b0 = my - b1 * mx
    yhat = [b0 + b1 * a for a in x]
    sse = sum((b - h) ** 2 for b, h in zip(y, yhat))
    ssr = sum((h - my) ** 2 for h in yhat)
    r = sxy / math.sqrt(sxx * syy)
    mse = sse / (n - 2)
    see = math.sqrt(mse)
    sb1 = see / math.sqrt(sxx)
    sb0 = see * math.sqrt(1.0 / n + mx ** 2 / sxx)
    return dict(n=n, mx=mx, my=my, sxx=sxx, sxy=sxy, sst=syy, ssr=ssr, sse=sse,
                b0=b0, b1=b1, r=r, r2=r * r, mse=mse, see=see, sb1=sb1, sb0=sb0,
                t0=b1 / sb1, t1=(b1 - 1) / sb1, ta=b0 / sb0, F=ssr / mse,
                sx=math.sqrt(svar(x)), sy=math.sqrt(svar(y)))


print("Day 50 numbers")

# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 47): slope from r and the SDs, intercept through the means
r_w, sy_w, sx_w, my_w, mx_w = 0.6, 5.0, 4.0, 1.4, 0.8
b1_w = r_w * sy_w / sx_w
b0_w = my_w - b1_w * mx_w
show("w1 slope = r x sY / sX = 0.6 x 5.0 / 4.0", "{:.4f}".format(b1_w))
show("w1 0.75 x 0.8", "{:.4f}".format(b1_w * mx_w))
show("w1 intercept = 1.4 - 0.75 x 0.8 (answer, %)", "{!r} -> {:.2f}".format(b0_w, b0_w))
b1_wt = r_w * sx_w / sy_w
show("w1 trap sX/sY: slope ; intercept", "{:.4f} ; {:.4f}".format(b1_wt, my_w - b1_wt * mx_w))
show("w1 trap no r: slope sY/sX ; intercept", "{:.4f} ; {:.4f}".format(sy_w / sx_w, my_w - sy_w / sx_w * mx_w))

# w2 (Day 43): 95% CI lower limit, t with 35 df
n_w2, xbar_w2, s_w2 = 36, 1.0, 4.2
se_w2 = s_w2 / math.sqrt(n_w2)
tc35 = t_crit(0.975, n_w2 - 1)
show("w2 SE = 4.2 / sqrt(36)", "{:.4f}".format(se_w2))
show("w2 invT(.975, 35) ; table value used 2.030", "{:.4f}".format(tc35))
show("w2 half-width 2.030 x 0.7", "{:.4f}".format(2.030 * se_w2))
show("w2 lower limit (right, B) ; upper limit", "{:.4f} ; {:.4f}".format(xbar_w2 - 2.030 * se_w2, xbar_w2 + 2.030 * se_w2))
show("w2 divided by n (A): 4.2/36 ; lower", "{:.4f} ; {:.4f}".format(s_w2 / n_w2, xbar_w2 - 2.030 * s_w2 / n_w2))
show("w2 one-tailed z 1.645 (C): lower", "{:.4f}".format(xbar_w2 - 1.645 * se_w2))
show("w2 z 1.96 (not offered): lower", "{:.4f}".format(xbar_w2 - 1.96 * se_w2))

# ---------------------------------------------------------------------------
print("=== Walkthrough (and the story's numbers) ===")
X = [1.8, -2.4, 3.1, -0.6, 0.9, 2.6]      # market, %
Y = [2.9, -2.0, 4.6, -1.9, 0.2, 3.1]      # stock, %
f = fit(X, Y)
n = f["n"]
df = n - 2
show("n ; df = n - 2", "{} ; {}".format(n, df))
show("means X ; Y", "{:.4f} ; {:.4f}".format(f["mx"], f["my"]))
show("Sx ; Sy", "{:.4f} ; {:.4f}".format(f["sx"], f["sy"]))
show("Sum (X - Xbar)^2 ; Sum (X - Xbar)(Y - Ybar) ; SST", "{:.4f} ; {:.4f} ; {:.4f}".format(f["sxx"], f["sxy"], f["sst"]))
show("LIN a (intercept, alpha)", "{!r} -> BA {} / TI {}".format(f["b0"], d4(f["b0"]), ti4(f["b0"])))
show("LIN b (slope, beta)", "{!r} -> BA {} / TI {}".format(f["b1"], d4(f["b1"]), ti4(f["b1"])))
show("LIN r", "{!r} -> BA {} / TI {}".format(f["r"], d4(f["r"]), ti4(f["r"])))
show("r^2", "{!r} -> TI {}".format(f["r2"], ti4(f["r2"])))
show("SSE ; MSE ; SEE (TI s=)", "{:.4f} ; {:.4f} ; {!r} -> TI {}".format(f["sse"], f["mse"], f["see"], ti4(f["see"])))
show("Y05 check ; X06 ; Y06", "{} ; {} ; {}".format(d4(Y[4]), d4(X[5]), d4(Y[5])))
one_minus = 1 - f["r"] ** 2
show("1 - r^2 ; sqrt(1 - r^2) ; sqrt(4)", "{:.4f} ; {:.4f} ; {:.4f}".format(one_minus, math.sqrt(one_minus), math.sqrt(df)))
t_corr = f["r"] * math.sqrt(df) / math.sqrt(one_minus)
show("t = r x sqrt(4) / sqrt(1 - r^2) (correlation t)", "{!r} -> BA {} / TI {}".format(t_corr, d4(t_corr), ti4(t_corr)))
show("slope t = b / s_b (direct) -- must equal", "{!r}".format(f["t0"]))
assert abs(t_corr - f["t0"]) < 1e-9
p0 = t_two(t_corr, df)
show("two-tailed p for beta = 0 (TI p=)", "{!r} -> TI {}".format(p0, ti4(p0)))
tc4 = t_crit(0.975, df)
show("invT(.975, 4) ; table 2.776", "{!r} -> TI {}".format(tc4, ti4(tc4)))
sb = f["b1"] / t_corr
show("s_b = b / t", "{!r} -> BA {} / TI {}".format(sb, d4(sb), ti4(sb)))
show("s_b = SEE / sqrt(Sum (X - Xbar)^2) -- must equal", "{!r}".format(f["sb1"]))
show("sqrt(Sum (X - Xbar)^2)", "{:.4f}".format(math.sqrt(f["sxx"])))
assert abs(sb - f["sb1"]) < 1e-12
t1 = (f["b1"] - 1) / sb
show("t(beta = 1) = (b - 1) / s_b (walkthrough answer)", "{!r} -> BA {} / TI {}".format(t1, d4(t1), ti4(t1)))
show("b - 1", "{:.4f}".format(f["b1"] - 1))
p1 = t_two(t1, df)
show("two-tailed p for beta = 1 = 2 x tcdf(t, 1E99, 4)", "{!r} -> TI {} ; {:.2f}".format(p1, ti4(p1), p1))
show("trap: retyped 4-dp values (1.2661 - 1) / 0.2185", "{:.4f}".format((1.2661 - 1) / 0.2185))
show("trap: 0 in the numerator for beta = 1 (that is t vs 0)", "{:.4f}".format(f["b1"] / sb))
lo95 = f["b1"] - 2.776 * sb
hi95 = f["b1"] + 2.776 * sb
show("95% CI with table 2.776: b - 2.776 s_b ; b + 2.776 s_b", "{!r} ; {!r}".format(lo95, hi95))
show("95% CI displays", "BA {} ; {} / TI {} ; {}".format(d4(lo95), d4(hi95), ti4(lo95), ti4(hi95)))
show("95% CI with invT 2.7764", "{:.4f} ; {:.4f}".format(f["b1"] - tc4 * sb, f["b1"] + tc4 * sb))
show("half-width 2.776 x s_b", "{:.4f}".format(2.776 * sb))
show("F = t^2 (for beta = 0) ; F = MSR/MSE", "{:.4f} ; {:.4f}".format(t_corr ** 2, f["F"]))
show("alpha test: s_b0 ; t = a / s_b0", "{:.4f} ; {:.4f}".format(f["sb0"], f["ta"]))
tc5 = t_crit(0.975, 5)
show("trap df n - 1 = 5: critical t", "{:.4f}".format(tc5))
show("trap df: t = 2.65 rejects with 5 df? ; with 4 df?", "{} ; {}".format(2.65 > tc5, 2.65 > tc4))
show("SEE / sqrt(Sxx) = 1.0173 / 4.6562 (rounded inputs)", "{:.4f}".format(1.0173 / 4.6562))
# stock entered as X, market as Y: slope of market on stock
rev = fit(Y, X)
show("trap: stock as X, market as Y: slope ; = r^2 / b", "{:.4f} ; {:.4f}".format(rev["b1"], f["r"] ** 2 / f["b1"]))
show("trap: reversed regression t vs 0 (same) ; t vs 1", "{:.4f} ; {:.4f}".format(rev["t0"], rev["t1"]))
show("as on the page (story): beta ; s_b ; t0 ; t1 ; CI",
     "{:.2f} ; {:.2f} ; {:.2f} ; {:.2f} ; {:.2f} to {:.2f}".format(f["b1"], sb, t_corr, t1, lo95, hi95))

# ---------------------------------------------------------------------------
print("=== Worked example (60 months) ===")
nw, bw, sbw, aw, saw = 60, 1.25, 0.10, 0.20, 0.15
dfw = nw - 2
tcw = t_crit(0.975, dfw)
show("df ; critical t(0.975, 58) ; table 2.002", "{} ; {:.4f}".format(dfw, tcw))
tw1 = (bw - 1) / sbw
tw0 = bw / sbw
twa = aw / saw
show("beta = 1: t = (1.25 - 1) / 0.10", "{!r} -> {:.2f}".format(tw1, tw1))
show("beta = 0: t = 1.25 / 0.10", "{!r} -> {:.1f}".format(tw0, tw0))
show("alpha: t = 0.20 / 0.15", "{!r} -> {:.4f}".format(twa, twa))
Fw = tw0 ** 2
show("F = 12.5^2", "{:.2f}".format(Fw))
rw = tw0 / math.sqrt(tw0 ** 2 + dfw)
show("r = t / sqrt(t^2 + n - 2) = 12.5 / sqrt(156.25 + 58)", "{!r} -> {:.4f}".format(rw, rw))
show("156.25 + 58 ; sqrt", "{:.2f} ; {:.4f}".format(Fw + dfw, math.sqrt(Fw + dfw)))
show("check r sqrt(58) / sqrt(1 - r^2)", "{:.4f}".format(rw * math.sqrt(dfw) / math.sqrt(1 - rw ** 2)))
show("check with r rounded 0.8540", "{:.4f}".format(0.8540 * math.sqrt(dfw) / math.sqrt(1 - 0.8540 ** 2)))
show("R^2 = r^2", "{:.4f}".format(rw ** 2))
show("95% CI for beta: 1.25 -/+ 2.002 x 0.10", "{:.4f} ; {:.4f}".format(bw - 2.002 * sbw, bw + 2.002 * sbw))
show("p-values (two-tailed): beta=1 ; alpha", "{:.4f} ; {:.4f}".format(t_two(tw1, dfw), t_two(twa, dfw)))

# ---------------------------------------------------------------------------
print("=== Check questions ===")
# c1: six new months
Xc = [2.2, -1.4, 0.8, -2.9, 3.5, 1.1]
Yc = [2.6, -2.3, 0.1, -3.6, 4.9, 1.9]
fc = fit(Xc, Yc)
tc_corr = fc["r"] * math.sqrt(fc["n"] - 2) / math.sqrt(1 - fc["r"] ** 2)
sbc = fc["b1"] / tc_corr
t1c = (fc["b1"] - 1) / sbc
show("c1 a ; b ; r", "BA {} ; {} ; {}".format(d4(fc["b0"]), d4(fc["b1"]), d4(fc["r"])))
show("c1 r^2 ; s (SEE)", "TI {} ; {}".format(ti4(fc["r2"]), ti4(fc["see"])))
show("c1 correlation t = slope t vs 0", "{!r} -> {}".format(tc_corr, d4(tc_corr)))
show("c1 p for beta = 0", "{:.4f} -> TI {}".format(t_two(tc_corr, 4), ti4(t_two(tc_corr, 4))))
show("c1 s_b = b / t", "{!r} -> {}".format(sbc, d4(sbc)))
show("c1 t(beta = 1) (answer)", "{!r} -> {}".format(t1c, d4(t1c)))
show("c1 two-tailed p for beta = 1", "{:.4f}".format(t_two(t1c, 4)))
show("c1 retyped 4-dp: (1.3355 - 1) / 0.1144", "{:.4f}".format((1.3355 - 1) / 0.1144))
show("c1 trap 0 in numerator", "{:.4f}".format(fc["b1"] / sbc))
show("c1 95% CI (2.776)", "{:.4f} ; {:.4f}".format(fc["b1"] - 2.776 * sbc, fc["b1"] + 2.776 * sbc))
show("c1 1 - r^2 ; sqrt", "{:.4f} ; {:.4f}".format(1 - fc["r"] ** 2, math.sqrt(1 - fc["r"] ** 2)))

# c3: correlation test from r and n
r3, n3 = 0.35, 40
t3 = r3 * math.sqrt(n3 - 2) / math.sqrt(1 - r3 ** 2)
show("c3 sqrt(38) ; 1 - r^2 ; sqrt(1 - r^2)", "{:.4f} ; {:.4f} ; {:.4f}".format(math.sqrt(n3 - 2), 1 - r3 ** 2, math.sqrt(1 - r3 ** 2)))
show("c3 t (answer)", "{!r} -> {:.4f}".format(t3, t3))
show("c3 critical t(0.975, 38)", "{:.4f}".format(t_crit(0.975, n3 - 2)))
show("c3 p (two-tailed)", "{:.4f}".format(t_two(t3, n3 - 2)))
show("c3 trap sqrt(n - 1) = sqrt(39)", "{:.4f}".format(r3 * math.sqrt(n3 - 1) / math.sqrt(1 - r3 ** 2)))
show("c3 trap no square root on 1 - r^2", "{:.4f}".format(r3 * math.sqrt(n3 - 2) / (1 - r3 ** 2)))
show("c3 F = t^2", "{:.4f}".format(t3 ** 2))

# c4: exam style
n4, b4, s4 = 36, 1.18, 0.08
right4 = (b4 - 1) / s4
show("c4 t(beta = 1) (right, C)", "{!r} -> {:.4f}".format(right4, right4))
show("c4 0 in numerator (A)", "{:.4f}".format(b4 / s4))
show("c4 SE x sqrt(36) (B)", "{:.4f} ; denominator {:.4f}".format((b4 - 1) / (s4 * math.sqrt(n4)), s4 * math.sqrt(n4)))
show("c4 critical t(0.975, 34)", "{:.4f}".format(t_crit(0.975, n4 - 2)))
show("c4 p (two-tailed)", "{:.4f}".format(t_two(right4, n4 - 2)))
show("c4 95% CI 1.18 -/+ 2.032 x 0.08", "{:.4f} ; {:.4f}".format(b4 - 2.032 * s4, b4 + 2.032 * s4))
