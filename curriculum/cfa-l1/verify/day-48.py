#!/usr/bin/env python3
"""Day 48 of CFA Math: every number that appears on day-48.html.

ANOVA, R^2 and the standard error of the estimate: total variation in Y splits
into an explained and an unexplained part, SST = SSR + SSE; R^2 = SSR/SST
(= r^2 with one independent variable); MSE = SSE/(n - 2); SEE = sqrt(MSE);
F = MSR/MSE with 1 and n - 2 df (CFA Program 2027 Level I, Quantitative
Methods, LM10 Applications of Simple Linear Regression in Finance).
Story: Anscombe's quartet (1973), recomputed from the published data.
Plain Python 3, standard library only: the F distribution comes from the
regularized incomplete beta function, critical values by bisection.
Run it and copy the printed numbers.
"""

import math


def show(label, value):
    print("  {:<72} {}".format(label, value))


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


# --- the F distribution (standard library only) ----------------------------

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


def f_cdf(f, d1, d2):
    if f <= 0:
        return 0.0
    return betainc(d1 / 2.0, d2 / 2.0, d1 * f / (d1 * f + d2))


def f_upper(f, d1, d2):
    """Area above f: what Fcdf(f, 1E99, d1, d2) returns on the TI-84."""
    return 1.0 - f_cdf(f, d1, d2)


def f_crit(area_below, d1, d2):
    lo, hi = 0.0, 1000.0
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if f_cdf(mid, d1, d2) < area_below:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# sanity checks against published F-table values
assert abs(f_crit(0.95, 1, 9) - 5.117) < 0.0006
assert abs(f_crit(0.95, 1, 3) - 10.128) < 0.0006
assert abs(f_crit(0.95, 1, 28) - 4.196) < 0.0006
assert abs(f_crit(0.95, 20, 20) - 2.124) < 0.0006


# --- regression helpers -----------------------------------------------------

def mean(v):
    return sum(v) / len(v)


def svar(v):
    m = mean(v)
    return sum((x - m) ** 2 for x in v) / (len(v) - 1)


def fit(x, y):
    """Least squares: returns b0, b1, r and the ANOVA pieces."""
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
    return dict(n=n, mx=mx, my=my, sxx=sxx, sxy=sxy, sst=syy, ssr=ssr, sse=sse,
                b0=b0, b1=b1, r=r, r2=ssr / syy, mse=mse, see=math.sqrt(mse),
                F=ssr / mse, yhat=yhat)


print("Day 48 numbers")

# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 47): least-squares prediction from Cov and Var
cov, var_m, mean_m, mean_s, x_new = 18.0, 15.0, 1.0, 1.4, 3.0
b1w = cov / var_m
b0w = mean_s - b1w * mean_m
yw = b0w + b1w * x_new
show("w1 slope b1 = Cov/Var = 18/15", "{:.4f}".format(b1w))
show("w1 intercept b0 = 1.4 - 1.2 x 1.0", "{:.4f}".format(b0w))
show("w1 1.2 x 3.0", "{:.4f}".format(b1w * x_new))
show("w1 prediction b0 + b1 x 3.0 (answer, %)", "{!r} -> {:.2f}".format(yw, yw))
show("w1 trap: Var/Cov slope 15/18, prediction", "{:.4f} ; {:.4f}".format(var_m / cov, (mean_s - var_m / cov * mean_m) + var_m / cov * x_new))
show("w1 trap: forgot the intercept 1.2 x 3", "{:.4f}".format(b1w * x_new))

# w2 (Day 41): duration + convexity
md, cx, dy = 8.0, 90.0, -0.01
dur_term = -md * dy
cx_term = 0.5 * cx * dy ** 2
w2 = dur_term + cx_term
show("w2 duration term -8.0 x (-0.01)", "{:+.4%}".format(dur_term))
show("w2 (dYield)^2", "{:.4f}".format(dy ** 2))
show("w2 convexity term 1/2 x 90 x 0.0001", "{:+.4%}".format(cx_term))
show("w2 estimate (right, C)", "{:+.4%}".format(w2))
show("w2 forgot the 1/2 (B)", "{:+.4%}".format(dur_term + cx * dy ** 2))
show("w2 minus sign on convexity (A)", "{:+.4%}".format(dur_term - cx_term))

# ---------------------------------------------------------------------------
print("=== Story: Anscombe's quartet (published data) ===")
x123 = [10, 8, 13, 9, 11, 14, 6, 4, 12, 7, 5]
sets = {
    "I": (x123, [8.04, 6.95, 7.58, 8.81, 8.33, 9.96, 7.24, 4.26, 10.84, 4.82, 5.68]),
    "II": (x123, [9.14, 8.14, 8.74, 8.77, 9.26, 8.10, 6.13, 3.10, 9.13, 7.26, 4.74]),
    "III": (x123, [7.46, 6.77, 12.74, 7.11, 7.81, 8.84, 6.08, 5.39, 8.15, 6.42, 5.73]),
    "IV": ([8, 8, 8, 8, 8, 8, 8, 19, 8, 8, 8],
           [6.58, 5.76, 7.71, 8.84, 8.47, 7.04, 5.25, 12.50, 5.56, 7.91, 6.89]),
}
fc9 = f_crit(0.95, 1, 9)
for name, (x, y) in sets.items():
    f = fit(x, y)
    show("set {}: n ; mean X ; mean Y".format(name), "{} ; {:.4f} ; {:.4f}".format(f["n"], f["mx"], f["my"]))
    show("set {}: var X ; var Y (sample)".format(name), "{:.4f} ; {:.4f}".format(svar(x), svar(y)))
    show("set {}: r ; b0 ; b1".format(name), "{:.4f} ; {:.4f} ; {:.4f}".format(f["r"], f["b0"], f["b1"]))
    show("set {}: SST ; SSR ; SSE".format(name), "{:.4f} ; {:.4f} ; {:.4f}".format(f["sst"], f["ssr"], f["sse"]))
    show("set {}: R2 ; r^2".format(name), "{:.4f} ; {:.4f}".format(f["r2"], f["r"] ** 2))
    show("set {}: MSE ; SEE ; F".format(name), "{:.4f} ; {:.4f} ; {:.4f}".format(f["mse"], f["see"], f["F"]))
    show("set {}: SST = 10 x var Y check".format(name), "{:.4f}".format(10 * svar(y)))
show("F crit 5%, (1, 9)", "{:.4f}".format(fc9))
fI = fit(*sets["I"])
show("as on the page, set I: SST ; SSR ; SSE ; var Y",
     "{:.2f} ; {:.2f} ; {:.2f} ; {:.3f}".format(fI["sst"], fI["ssr"], fI["sse"], svar(sets["I"][1])))
show("as on the page, set I: R2 ; SEE ; F ; F crit",
     "{:.3f} ; {:.3f} ; {:.2f} ; {:.2f}".format(fI["r2"], fI["see"], fI["F"], fc9))
for name in ("II", "III", "IV"):
    f = fit(*sets[name])
    show("as on the page, set {}: R2 ; SEE ; F".format(name),
         "{:.2f} ; {:.2f} ; {:.0f}".format(f["r2"], f["see"], f["F"]))
show("set I rounded: 27.51 / 41.27 ; sqrt(13.76/9) ; 27.51/(13.76/9)",
     "{:.4f} ; {:.4f} ; {:.4f}".format(27.51 / 41.27, math.sqrt(13.76 / 9), 27.51 / (13.76 / 9)))
# set III without its outlier (X = 13): the other ten lie on a straight line
x3 = [a for a, b in zip(*sets["III"]) if a != 13]
y3 = [b for a, b in zip(*sets["III"]) if a != 13]
f3 = fit(x3, y3)
show("set III without the X = 13 point: r ; b0 ; b1", "{:.6f} ; {:.4f} ; {:.4f}".format(f3["r"], f3["b0"], f3["b1"]))
show("set IV: points with X = 8 ; X = 19", "{} ; {}".format(sets["IV"][0].count(8), sets["IV"][0].count(19)))

# ---------------------------------------------------------------------------
print("=== Worked example (Day 47 money-supply data) ===")
xw = [1, 2, 3, 4, 5]
yw5 = [2, 3, 5, 4, 6]
fw = fit(xw, yw5)
show("means X ; Y", "{} ; {}".format(fw["mx"], fw["my"]))
show("line b0 ; b1", "{:.4f} ; {:.4f}".format(fw["b0"], fw["b1"]))
show("(Y - Ybar)^2 terms", [round((b - fw["my"]) ** 2, 4) for b in yw5])
show("SST", "{:.4f}".format(fw["sst"]))
show("fitted values", [round(h, 4) for h in fw["yhat"]])
res = [round(b - h, 4) for b, h in zip(yw5, fw["yhat"])]
show("residuals Y - Yhat", res)
show("squared residuals", [round(e ** 2, 4) for e in res])
show("SSE", "{:.4f}".format(fw["sse"]))
dev = [round(h - fw["my"], 4) for h in fw["yhat"]]
show("Yhat - Ybar", dev)
show("(Yhat - Ybar)^2 terms", [round(d ** 2, 4) for d in dev])
show("SSR ; check b1^2 x Sxx = 0.81 x 10", "{:.4f} ; {:.4f}".format(fw["ssr"], fw["b1"] ** 2 * fw["sxx"]))
cross = [round(d * e, 4) for d, e in zip(dev, res)]
show("cross products (Yhat - Ybar) x e ; sum", "{} ; {:.4f}".format(cross, sum(cross)))
show("sum of residuals ; sum of (X - Xbar) e", "{:.4f} ; {:.4f}".format(sum(res), sum((a - fw['mx']) * e for a, e in zip(xw, res))))
show("SSR + SSE", "{:.4f}".format(fw["ssr"] + fw["sse"]))
show("R2 = 8.10 / 10 ; r ; r^2", "{:.4f} ; {:.4f} ; {:.4f}".format(fw["r2"], fw["r"], fw["r"] ** 2))
show("s_X ; s_Y", "{:.4f} ; {:.4f}".format(math.sqrt(svar(xw)), math.sqrt(svar(yw5))))
show("MSE = 1.90 / 3", "{!r} -> {:.4f}".format(fw["mse"], fw["mse"]))
show("SEE = sqrt(MSE)", "{!r} -> {:.4f}".format(fw["see"], fw["see"]))
show("F = 8.10 / 0.6333", "{!r} -> {:.4f}".format(fw["F"], fw["F"]))
show("F crit 5%, (1, 3)", "{:.4f}".format(f_crit(0.95, 1, 3)))
show("p-value Fcdf(F, 1E99, 1, 3)", "{:.4f}".format(f_upper(fw["F"], 1, 3)))
show("ANOVA df: regression 1 ; error n - 2 ; total n - 1", "1 ; {} ; {}".format(fw["n"] - 2, fw["n"] - 1))
sb1 = fw["see"] / math.sqrt(fw["sxx"])
show("trap: SE of slope = SEE / sqrt(Sxx) = 0.7958 / sqrt(10)", "{:.4f}".format(sb1))
show("trap: SSR / SSE (not R2)", "{:.4f}".format(fw["ssr"] / fw["sse"]))
show("t of slope = b1 / s_b1 ; t^2 (= F)", "{:.4f} ; {:.4f}".format(fw["b1"] / sb1, (fw["b1"] / sb1) ** 2))

# ---------------------------------------------------------------------------
print("=== Walkthrough ===")
n, ssr, sse = 30, 0.0040, 0.0060
sst = ssr + sse
r2 = ssr / sst
mse = sse / (n - 2)
see = math.sqrt(mse)
F = (ssr / 1) / mse
show("SST = SSR + SSE", "{:.4f}".format(sst))
show("R2 = .004 / (.004 + .006)", "{!r} -> BA {} / TI {}".format(r2, d4(r2), ti4(r2)))
show("MSE = .006 / 28", "{!r} -> BA {} / TI {}".format(mse, d4(mse), ti4(mse)))
show("SEE = sqrt(MSE)", "{!r} -> BA {} / TI {}".format(see, d4(see), ti4(see)))
show("SEE in percent", "{:.2f}%".format(100 * see))
sy = math.sqrt(sst / (n - 1))
show("Y's own SD = sqrt(SST / 29)", "{:.4f} ({:.2f}%)".format(sy, 100 * sy))
show("F = .004 / MSE", "{!r} -> BA {} / TI {}".format(F, d4(F), ti4(F)))
show("trap: F with MSE retyped as 0.0002", "{:.4f}".format(ssr / 0.0002))
show("shortcut F = (SSR/SSE) x (n - 2) ; R2/(1 - R2) x (n - 2)", "{:.4f} ; {:.4f}".format(ssr / sse * (n - 2), r2 / (1 - r2) * (n - 2)))
fc28 = f_crit(0.95, 1, 28)
show("F crit 5%, (1, 28)", "{:.4f}".format(fc28))
pF = f_upper(F, 1, 28)
show("p = Fcdf(Ans, 1E99, 1, 28)", "{!r} -> TI {} ; rounded {:.4f}".format(pF, ti4(pF), pF))
show("trap n - 1: MSE = .006/29 ; SEE ; F", "{:.6f} ; {:.4f} ; {:.4f}".format(sse / 29, math.sqrt(sse / 29), ssr / (sse / 29)))
show("as on the page: R2 ; MSE ; SEE ; F ; F crit ; p",
     "{:.2f} ; {:.6f} ; {:.4f} ; {:.2f} ; {:.2f} ; {:.4f}".format(r2, mse, see, F, fc28, pF))

# Day 47's five families: R2 = r^2
xp = [64, 66, 68, 70, 72]
yc = [66.0, 66.4, 68.6, 68.2, 70.3]
fp = fit(xp, yc)
show("families: a ; b ; r", "{} ; {} ; {}".format(d4(fp["b0"]), d4(fp["b1"]), d4(fp["r"])))
show("families: r^2 (stored r squared)", "{!r} -> BA {} / TI {}".format(fp["r"] ** 2, d4(fp["r"] ** 2), ti4(fp["r"] ** 2)))
show("families: SST ; SSR ; SSE ; SSR/SST", "{:.4f} ; {:.4f} ; {:.4f} ; {:.4f}".format(fp["sst"], fp["ssr"], fp["sse"], fp["r2"]))
show("families: 0.9416^2 (rounded r squared)", "{:.4f}".format(0.9416 ** 2))
show("families: Y05", d4(yc[-1]))

# ---------------------------------------------------------------------------
print("=== Check questions ===")
# c1
n1, ssr1, sse1 = 40, 0.0125, 0.0375
mse1 = sse1 / (n1 - 2)
F1 = ssr1 / mse1
show("c1 MSE = .0375 / 38", "{!r} -> BA {} / TI {}".format(mse1, d4(mse1), ti4(mse1)))
show("c1 F (answer)", "{!r} -> BA {} / TI {}".format(F1, d4(F1), ti4(F1)))
show("c1 shortcut (SSR/SSE) x 38", "{:.4f}".format(ssr1 / sse1 * (n1 - 2)))
show("c1 R2 ; SEE", "{:.4f} ; {:.4f}".format(ssr1 / (ssr1 + sse1), math.sqrt(mse1)))
show("c1 F crit 5% (1, 38)", "{:.4f}".format(f_crit(0.95, 1, 38)))
p1 = f_upper(F1, 1, 38)
show("c1 p = Fcdf(Ans, 1E99, 1, 38)", "{!r} -> TI {} ; {:.4f}".format(p1, ti4(p1), p1))
show("c1 trap n - 1: F with /39", "{:.4f}".format(ssr1 / (sse1 / 39)))
show("c1 as on the page: MSE ; F ; F crit (1, 38)",
     "{:.6f} ; {:.2f} ; {:.2f}".format(mse1, F1, f_crit(0.95, 1, 38)))

# c3
n3, sst3, r23 = 20, 0.0500, 0.64
sse3 = (1 - r23) * sst3
ssr3 = r23 * sst3
mse3 = sse3 / (n3 - 2)
see3 = math.sqrt(mse3)
show("c3 SSE = (1 - 0.64) x 0.05 ; SSR", "{:.4f} ; {:.4f}".format(sse3, ssr3))
show("c3 MSE = SSE / 18", "{!r} -> BA {}".format(mse3, d4(mse3)))
show("c3 SEE (answer)", "{!r} -> BA {} / TI {}".format(see3, d4(see3), ti4(see3)))
show("c3 F = SSR / MSE", "{:.4f}".format(ssr3 / mse3))
show("c3 F crit 5% (1, 18)", "{:.4f}".format(f_crit(0.95, 1, 18)))
show("c3 trap n - 1: sqrt(.018/19)", "{:.4f}".format(math.sqrt(sse3 / 19)))
show("c3 trap R2 as unexplained: sqrt(.032/18)", "{:.4f}".format(math.sqrt(ssr3 / 18)))
show("c3 trap no root: MSE", "{:.4f}".format(mse3))

# c4
n4, sse4 = 25, 0.0460
right4 = math.sqrt(sse4 / (n4 - 2))
show("c4 MSE = .046 / 23", "{:.6f}".format(sse4 / (n4 - 2)))
show("c4 SEE (right, A)", "{!r} -> {:.4f}".format(right4, right4))
show("c4 n - 1 (B): sqrt(.046/24)", "{:.4f}".format(math.sqrt(sse4 / (n4 - 1))))
show("c4 no square root (C): .046/23", "{:.4f}".format(sse4 / (n4 - 2)))
show("c4 n (not offered): sqrt(.046/25)", "{:.4f}".format(math.sqrt(sse4 / n4)))
