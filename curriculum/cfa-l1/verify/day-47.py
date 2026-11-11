#!/usr/bin/env python3
"""Day 47 of CFA Math: every number that appears on day-47.html.

Least squares: fitting the line. Y-hat = b0 + b1 X with
b1 = Cov(X, Y) / Var(X) = sum(x y) / sum(x^2) = r x s_Y / s_X (x, y are
deviations from the means), b0 = Y-bar - b1 X-bar, residual e = Y - Y-hat,
and least squares minimizes sum(e^2) (CFA Program 2027 Level I, Quantitative
Methods, LM10 Applications of Simple Linear Regression in Finance).
Plain Python 3, standard library only; the chi-square distribution for the
Day 46 warm-up is computed from the regularized incomplete gamma function and
its critical value by bisection. Run it and copy the printed numbers.
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


def mean(v):
    return sum(v) / len(v)


def lin(X, Y):
    """What the BA II Plus LIN screen and TI-84 LinReg(a+bx) report."""
    n = len(X)
    xb, yb = mean(X), mean(Y)
    dx = [x - xb for x in X]
    dy = [y - yb for y in Y]
    sxy = sum(a * b for a, b in zip(dx, dy))
    sxx = sum(a * a for a in dx)
    syy = sum(b * b for b in dy)
    out = {
        "n": n, "xbar": xb, "ybar": yb, "dx": dx, "dy": dy,
        "Sxy": sxy, "Sxx": sxx, "Syy": syy,
        "Sx": math.sqrt(sxx / (n - 1)), "sigx": math.sqrt(sxx / n),
        "Sy": math.sqrt(syy / (n - 1)), "sigy": math.sqrt(syy / n),
        "cov": sxy / (n - 1), "varx": sxx / (n - 1), "vary": syy / (n - 1),
    }
    out["b"] = sxy / sxx
    out["a"] = yb - out["b"] * xb
    out["r"] = sxy / math.sqrt(sxx * syy)
    out["r2"] = out["r"] ** 2
    return out


# --- chi-square (Day 46 warm-up) --------------------------------------------

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
    return 1.0 - math.exp(-x + s * math.log(x) - math.lgamma(s)) * h


def chi2_cdf(x, df):
    return gammainc_lower(df / 2.0, x / 2.0)


def chi2_crit(area_below, df):
    lo, hi = 0.0, 1000.0
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if chi2_cdf(mid, df) < area_below:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


assert abs(chi2_crit(0.95, 20) - 31.410) < 0.0006
assert abs(chi2_crit(0.95, 15) - 24.996) < 0.0006

print("Day 47 numbers")

# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 46): chi-square test of a single variance
n1, s1, sig1 = 16, 2.5, 2.0
chi = (n1 - 1) * s1 ** 2 / sig1 ** 2
show("w1 s^2 = 2.5^2 ; sigma0^2 = 2.0^2", "{:.4f} ; {:.4f}".format(s1 ** 2, sig1 ** 2))
show("w1 (n-1) s^2 = 15 x 6.25", "{:.4f}".format((n1 - 1) * s1 ** 2))
show("w1 chi2 = 93.75 / 4 (answer)", "{!r} -> BA {} / TI {}".format(chi, d4(chi), ti4(chi)))
show("w1 chi2 crit 5% upper, 15 df", "{:.3f}".format(chi2_crit(0.95, 15)))
p1 = 1 - chi2_cdf(chi, 15)
show("w1 p = chi2cdf(23.4375, 1E99, 15)", "{!r} -> TI {}".format(p1, ti4(p1)))
show("w1 s^2 / sigma0^2", "{:.4f}".format(s1 ** 2 / sig1 ** 2))
show("w1 trap: n instead of n - 1: 16 x 6.25 / 4", "{:.4f}".format(n1 * s1 ** 2 / sig1 ** 2))
show("w1 trap: SDs: 15 x 2.5 / 2", "{:.4f}".format((n1 - 1) * s1 / sig1))

# w2 (Day 25): correlation from covariance
cov2, sx2, sy2 = 12.0, 4.0, 6.0
r2w = cov2 / (sx2 * sy2)
show("w2 r = 12 / (4 x 6) (right, A)", "{:.4f}".format(r2w))
show("w2 B: 12 / (16 x 36) (variances)", "{:.4f}".format(cov2 / (sx2 ** 2 * sy2 ** 2)))
show("w2 C: 12 / 16 = Cov / Var(X) = today's slope", "{:.4f}".format(cov2 / sx2 ** 2))

# ---------------------------------------------------------------------------
print("=== Story ===")
show("Galton: children 930 in prose, 928 in Table I; parentages", "930 ; 928 ; 205")
show("female factor 1.08 (e.g. 64 in -> 64 x 1.08)", "{:.2f}".format(64 * 1.08))
show("two-thirds slope: 3 inches above -> 3 x 2/3", "{:.4f}".format(3 * 2 / 3))

# ---------------------------------------------------------------------------
print("=== Why it works (worked-example data) ===")
Xw = [1, 2, 3, 4, 5]
Yw = [2, 3, 5, 4, 6]
W = lin(Xw, Yw)
show("sum x^2 ; sum x y ; sum y^2 (deviations)", "{:.4f} ; {:.4f} ; {:.4f}".format(W["Sxx"], W["Sxy"], W["Syy"]))


def sse(b, L):
    return L["Syy"] - 2 * b * L["Sxy"] + b * b * L["Sxx"]


for b in (0.8, 0.9, 1.0):
    show("SSE(b1 = {}) = 10 - 18 b + 10 b^2".format(b), "{:.4f}".format(sse(b, W)))
show("vertex 18 / (2 x 10)", "{:.4f}".format(W["Sxy"] / W["Sxx"]))
show("constant: sum y^2 - (sum x y)^2 / sum x^2", "{:.4f}".format(W["Syy"] - W["Sxy"] ** 2 / W["Sxx"]))
# weighted-average-of-slopes view
pts = [(x, y) for x, y in zip(W["dx"], W["dy"]) if x != 0]
num = sum(x * x * (y / x) for x, y in pts)
show("weights x^2 ; own slopes y/x (points with x != 0)",
     "{} ; {}".format([x * x for x, _ in pts], ["{:.4f}".format(y / x) for x, y in pts]))
show("weighted average of own slopes", "{:.4f}".format(num / W["Sxx"]))

# ---------------------------------------------------------------------------
print("=== Worked example ===")
show("sum X ; sum Y ; X-bar ; Y-bar", "{} ; {} ; {:.4f} ; {:.4f}".format(sum(Xw), sum(Yw), W["xbar"], W["ybar"]))
show("deviations x", W["dx"])
show("deviations y", W["dy"])
show("cross-products", [a * b for a, b in zip(W["dx"], W["dy"])])
show("sum x y ; sum x^2", "{:.4f} ; {:.4f}".format(W["Sxy"], W["Sxx"]))
show("b1 = 9 / 10", "{:.4f}".format(W["b"]))
show("Cov = 9/4 ; Var(X) = 10/4 ; Cov/Var", "{:.4f} ; {:.4f} ; {:.4f}".format(W["cov"], W["varx"], W["cov"] / W["varx"]))
show("b0 = 4 - 0.9 x 3", "{:.4f}".format(W["a"]))
pred6 = W["a"] + W["b"] * 6
show("prediction at X = 6", "{:.4f}".format(pred6))
fit = [W["a"] + W["b"] * x for x in Xw]
res = [y - f for y, f in zip(Yw, fit)]
show("fitted Y-hat", ["{:.1f}".format(f) for f in fit])
show("residuals e", ["{:+.1f}".format(e) for e in res])
show("sum of residuals ; SSE", "{:.4f} ; {:.4f}".format(sum(res), sum(e * e for e in res)))
show("residual of country 3", "{:.4f}".format(res[2]))
show("r (worked example)", "{:.4f}".format(W["r"]))

# ---------------------------------------------------------------------------
print("=== Walkthrough: five families ===")
X = [64, 66, 68, 70, 72]
Y = [66.0, 66.4, 68.6, 68.2, 70.3]
L = lin(X, Y)
show("n ; x-bar ; Sx ; sigma x", "{} ; {} ; {} ; {}".format(L["n"], d4(L["xbar"]), d4(L["Sx"]), d4(L["sigx"])))
show("y-bar ; Sy ; sigma y", "{} ; {} ; {}".format(d4(L["ybar"]), d4(L["Sy"]), d4(L["sigy"])))
show("a ; b ; r (BA)", "{} ; {} ; {}".format(d4(L["a"]), d4(L["b"]), d4(L["r"])))
show("a ; b ; r^2 ; r (TI)", "a={} b={} r2={} r={}".format(ti4(L["a"]), ti4(L["b"]), ti4(L["r2"]), ti4(L["r"])))
show("deviations x ; y", "{} ; {}".format(L["dx"], ["{:+.1f}".format(v) for v in L["dy"]]))
show("sum x y ; sum x^2 ; sum y^2", "{:.4f} ; {:.4f} ; {:.4f}".format(L["Sxy"], L["Sxx"], L["Syy"]))
show("Cov = 20.8/4 ; Var(X) = 40/4 ; Var(Y) = 12.2/4", "{:.4f} ; {:.4f} ; {:.4f}".format(L["cov"], L["varx"], L["vary"]))
show("b = Cov/Var(X) ; r x Sy/Sx", "{:.4f} ; {:.4f}".format(L["cov"] / L["varx"], L["r"] * L["Sy"] / L["Sx"]))
show("b0 = 67.9 - 0.52 x 68", "{:.4f}".format(L["ybar"] - L["b"] * L["xbar"]))
pred = L["a"] + L["b"] * 75
show("Y' at X' = 75 (answer)", "{!r} -> BA {} / TI {}".format(pred, d4(pred), ti4(pred)))
show("Y' at X' = 68 (through the means)", "{}".format(d4(L["a"] + L["b"] * 68)))
show("SSE for the five families: sum y^2 - (sum xy)^2/sum x^2", "{:.4f}".format(L["Syy"] - L["Sxy"] ** 2 / L["Sxx"]))

# ---------------------------------------------------------------------------
print("=== Back to the story ===")
show("75 - 68 ; prediction - children's mean", "{} ; {:.4f}".format(75 - 68, pred - L["ybar"]))
show("0.52 x 7", "{:.4f}".format(L["b"] * 7))
zx = 7 / L["Sx"]
zy = L["r"] * zx
show("parent z = 7 / Sx ; predicted z = r x that", "{:.4f} ; {:.4f}".format(zx, zy))
show("predicted z x Sy (check = 3.64)", "{:.4f}".format(zy * L["Sy"]))
show("as printed: 0.9416 x 2.21", "{:.4f}".format(0.9416 * 2.21))
show("Galton: 3 in above x 2/3", "{:.4f}".format(3 * 2 / 3))

# ---------------------------------------------------------------------------
print("=== Traps ===")
show("swap X and Y: slope = Cov / Var(Y) = 5.2 / 3.05", "{:.4f}".format(L["cov"] / L["vary"]))
show("  and r x Sx / Sy", "{:.4f}".format(L["r"] * L["Sx"] / L["Sy"]))
show("  1 / 0.52 (not the swapped slope either)", "{:.4f}".format(1 / L["b"]))
show("read a as the slope: 0.52 + 32.54 x 75", "{:,.2f}".format(L["b"] + L["a"] * 75))
show("mixing: r x Sy / sigma x", "{:.4f}".format(L["r"] * L["Sy"] / L["sigx"]))
show("both population: r x sigma y / sigma x", "{:.4f}".format(L["r"] * L["sigy"] / L["sigx"]))
show("intercept = prediction at X = 0", "{:.4f}".format(L["a"]))

# ---------------------------------------------------------------------------
print("=== Check questions ===")
# c1
X1 = [62, 65, 67, 70, 71]
Y1 = [64.8, 66.0, 67.9, 68.4, 69.5]
C1 = lin(X1, Y1)
show("c1 x-bar ; y-bar", "{:.4f} ; {:.4f}".format(C1["xbar"], C1["ybar"]))
show("c1 sum x y ; sum x^2", "{:.4f} ; {:.4f}".format(C1["Sxy"], C1["Sxx"]))
show("c1 slope b (answer)", "{!r} -> BA {} / TI {}".format(C1["b"], d4(C1["b"]), ti4(C1["b"])))
show("c1 a ; r ; r^2", "{} ; {} ; {}".format(d4(C1["a"]), d4(C1["r"]), ti4(C1["r2"])))
show("c1 Sx ; Sy", "{} ; {}".format(d4(C1["Sx"]), d4(C1["Sy"])))
show("c1 swapped slope (trap) Cov/Var(Y)", "{:.4f}".format(C1["cov"] / C1["vary"]))

# c3
r3, sY3, sX3, xb3, yb3, x3 = 0.6, 8.0, 5.0, 2.0, 3.0, 4.0
b3 = r3 * sY3 / sX3
a3 = yb3 - b3 * xb3
p3 = a3 + b3 * x3
show("c3 b1 = 0.6 x 8 / 5", "{:.4f}".format(b3))
show("c3 b0 = 3 - 0.96 x 2", "{:.4f}".format(a3))
show("c3 prediction at 4 (answer, %)", "{!r} -> {:.4f}".format(p3, p3))
show("c3 via means: 3 + 0.96 x (4 - 2)", "{:.4f}".format(yb3 + b3 * (x3 - xb3)))
show("c3 trap: s_X / s_Y: 0.6 x 5/8 -> slope ; prediction", "{:.4f} ; {:.4f}".format(r3 * sX3 / sY3, yb3 + r3 * sX3 / sY3 * (x3 - xb3)))
show("c3 trap: slope = r: 3 + 0.6 x 2", "{:.4f}".format(yb3 + r3 * (x3 - xb3)))

# c4
a4, b4, x4 = 1.20, 0.45, 4
show("c4 right B: 1.20 + 0.45 x 4", "{:.4f}".format(a4 + b4 * x4))
show("c4 A: a as slope 0.45 + 1.20 x 4", "{:.4f}".format(b4 + a4 * x4))
show("c4 C: solved for X: (4 - 1.20) / 0.45", "{:.4f}".format((x4 - a4) / b4))
