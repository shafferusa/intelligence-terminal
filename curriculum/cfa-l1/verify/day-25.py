#!/usr/bin/env python3
"""Day 25 of CFA Math: every number that appears on day-25.html.

Covariance and correlation (and Spearman rank correlation, as insurance)
(CFA Program 2027 Level I, Quantitative Methods,
LM5 Statistical Characteristics of Asset Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math


def show(label, value):
    print("  {:<62} {}".format(label, value))


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def mean(xs):
    return sum(xs) / len(xs)


def devs(xs):
    m = mean(xs)
    return [x - m for x in xs]


def s_sample(xs):
    return math.sqrt(sum(d * d for d in devs(xs)) / (len(xs) - 1))


def s_pop(xs):
    return math.sqrt(sum(d * d for d in devs(xs)) / len(xs))


def cov_sample(xs, ys):
    return sum(a * b for a, b in zip(devs(xs), devs(ys))) / (len(xs) - 1)


def cov_pop(xs, ys):
    return sum(a * b for a, b in zip(devs(xs), devs(ys))) / len(xs)


def corr(xs, ys):
    return cov_sample(xs, ys) / (s_sample(xs) * s_sample(ys))


def linreg(xs, ys):
    b = cov_sample(xs, ys) / s_sample(xs) ** 2
    a = mean(ys) - b * mean(xs)
    return a, b


def ranks_high_first(xs):
    """Rank 1 = highest value (no ties in today's data)."""
    order = sorted(xs, reverse=True)
    assert len(set(xs)) == len(xs), "ties present"
    return [order.index(x) + 1 for x in xs]


def spearman(xs, ys):
    rx, ry = ranks_high_first(xs), ranks_high_first(ys)
    d = [a - b for a, b in zip(rx, ry)]
    n = len(xs)
    sd2 = sum(v * v for v in d)
    return rx, ry, d, sd2, 1 - 6 * sd2 / (n * (n * n - 1))


def fmt_list(xs, f="{:g}"):
    return ", ".join(f.format(x) for x in xs)


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
W1 = [7, -4, 12, 1, 9]
show("w1 (Day 24) returns", fmt_list(W1))
show("w1 mean", mean(W1))
show("w1 deviations", fmt_list(devs(W1)))
show("w1 squared deviations", fmt_list([d * d for d in devs(W1)]))
ss = sum(d * d for d in devs(W1))
show("w1 sum of squared deviations", ss)
show("w1 sample variance = SS/(n-1)", "{:.4f}".format(ss / (len(W1) - 1)))
show("w1 Sx (answer, sample SD)", "{!r} -> {:.4f} -> {:.2f}%".format(s_sample(W1), s_sample(W1), s_sample(W1)))
show("w1 sigma_x (population, the trap)", "{:.4f}".format(s_pop(W1)))
show("w1 population variance SS/n", "{:.4f}".format(ss / len(W1)))

DAYS, DR, FACE = 180, 0.04, 100.0
price = FACE * (1 - DAYS / 360 * DR)
bey = 365 / DAYS * (FACE - price) / price
show("w2 (Day 18) 180-day bill at 4.00% discount: price per 100", "{:.4f}".format(price))
show("w2 bond-equivalent yield (correct)", "{!r} -> {:.4f}% -> {:.2f}%".format(100 * bey, 100 * bey, 100 * bey))
show("w2 distractor: add-on on price but 360-day year", "{:.4f}% -> {:.2f}%".format(100 * 360 / DAYS * (FACE - price) / price, 100 * 360 / DAYS * (FACE - price) / price))
show("w2 distractor: discount rate x 365/360 (still on face)", "{:.4f}% -> {:.2f}%".format(100 * DR * 365 / 360, 100 * DR * 365 / 360))
show("w2 discount (100 - price)", "{:.4f}".format(FACE - price))
show("w2 discount rate as a decimal", DR)

# ---------------------------------------------------------------------------
print("\n=== Story: 13 September 2022 and the year 2022 ===")
day = datetime.date(2022, 9, 13)
show("13 Sep 2022 weekday", day.strftime("%A"))
close, drop = 3932.69, 177.72
prev = close + drop
show("S&P 500 previous close = 3,932.69 + 177.72", "{:,.2f}".format(prev))
show("S&P 500 % change on the day", "{:.4f}%".format(-100 * drop / prev))
y_from, y_to = 3.571, 3.7434
show("two-year yield change (percentage points)", "{:.4f}".format(y_to - y_from))
show("two-year yield, as reported (from -> to)", "{}% -> {}%".format(y_from, y_to))
show("two-year yield, rounded", "{:.2f}% -> {:.2f}%".format(y_from, y_to))
SP22, AGG22 = -18.11, -13.01
mix = 0.6 * SP22 + 0.4 * AGG22
show("60/40 mix in 2022: 0.6 x 18.11 + 0.4 x 13.01 (loss)", "{:.4f} -> {:.2f}%".format(mix, mix))
show("  0.6 x 18.11", "{:.4f}".format(0.6 * 18.11))
show("  0.4 x 13.01", "{:.4f}".format(0.4 * 13.01))

# ---------------------------------------------------------------------------
print("\n=== Worked example (by hand) ===")
X = [10, -5, 15, 0]
Y = [2, 6, 1, 3]
show("X", fmt_list(X))
show("Y", fmt_list(Y))
show("sum X, mean X", "{} , {}".format(sum(X), mean(X)))
show("sum Y, mean Y", "{} , {}".format(sum(Y), mean(Y)))
dx, dy = devs(X), devs(Y)
show("X deviations", fmt_list(dx))
show("Y deviations", fmt_list(dy))
prods = [a * b for a, b in zip(dx, dy)]
show("cross-products", fmt_list(prods))
show("sum of cross-products", sum(prods))
sxy = cov_sample(X, Y)
show("s_XY = sum/(n-1)", "{!r} -> {:.4f}".format(sxy, sxy))
show("X squared deviations, sum", "{} , {}".format(fmt_list([d * d for d in dx]), sum(d * d for d in dx)))
show("Y squared deviations, sum", "{} , {}".format(fmt_list([d * d for d in dy]), sum(d * d for d in dy)))
show("s_X^2", "{:.4f}".format(s_sample(X) ** 2))
show("s_X", "{!r} -> {:.4f}".format(s_sample(X), s_sample(X)))
show("s_Y^2", "{:.4f}".format(s_sample(Y) ** 2))
show("s_Y", "{!r} -> {:.4f}".format(s_sample(Y), s_sample(Y)))
show("s_X x s_Y", "{:.4f}".format(s_sample(X) * s_sample(Y)))
r_we = corr(X, Y)
show("r = s_XY/(s_X s_Y)", "{!r} -> {:.4f}".format(r_we, r_we))
show("r via rounded inputs -18.3333/(9.1287 x 2.1602)", "{:.4f}".format(-18.3333 / (9.1287 * 2.1602)))
show("r via sums: -55 / sqrt(250 x 14)", "{:.4f}".format(-55 / math.sqrt(250 * 14)))
rx, ry, d, sd2, rs = spearman(X, Y)
show("Spearman ranks X (1 = highest)", fmt_list(rx))
show("Spearman ranks Y (1 = highest)", fmt_list(ry))
show("d = rank X - rank Y", fmt_list(d))
show("sum d^2", sd2)
show("n(n^2-1)", 4 * (16 - 1))
show("r_s", "{!r} -> {:.4f}".format(rs, rs))
show("check: Pearson r of the ranks", "{:.4f}".format(corr(rx, ry)))
# Doubling X: covariance doubles, correlation does not move
X2 = [2 * x for x in X]
show("double X: s_XY", "{:.4f}".format(cov_sample(X2, Y)))
show("double X: s_X", "{:.4f}".format(s_sample(X2)))
show("double X: r", "{:.4f}".format(corr(X2, Y)))
show("double X: s_XY, 2 dp; r, 2 dp", "{:.2f} ; {:.2f}".format(cov_sample(X2, Y), corr(X2, Y)))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough: S&P 500 (X) vs Bloomberg US Aggregate (Y), 2018-2022 ===")
YEARS = [2018, 2019, 2020, 2021, 2022]
SP = [-4.38, 31.49, 18.40, 28.71, -18.11]
AGG = [0.01, 8.72, 7.51, -1.54, -13.01]
n = len(SP)
show("inputs X (S&P 500, %)", ", ".join("{:.2f}".format(v) for v in SP) + "  (typed: " + fmt_list(SP) + ")")
show("inputs Y (Aggregate, %)", ", ".join("{:.2f}".format(v) for v in AGG))
show("BA display of the last entry, Y05", d4(AGG[-1]))
show("n", "{} (BA shows n = {:.4f})".format(n, n))
show("x-bar", "{!r} -> {}".format(mean(SP), d4(mean(SP))))
show("Sx", "{!r} -> {}".format(s_sample(SP), d4(s_sample(SP))))
show("sigma_x", d4(s_pop(SP)))
show("y-bar", "{!r} -> {}".format(mean(AGG), d4(mean(AGG))))
show("Sy", "{!r} -> {}".format(s_sample(AGG), d4(s_sample(AGG))))
show("sigma_y", d4(s_pop(AGG)))
a, b = linreg(SP, AGG)
show("a (intercept)", d4(a))
show("b (slope)", d4(b))
r_wt = corr(SP, AGG)
show("r", "{!r} -> {}".format(r_wt, d4(r_wt)))
show("r^2", d4(r_wt ** 2))
show("r, 2 dp (story)", "{:.2f}".format(r_wt))
cov_wt = r_wt * s_sample(SP) * s_sample(AGG)
show("s_XY = r x Sx x Sy (answer)", "{!r} -> {} -> {:.2f}".format(cov_wt, d4(cov_wt), cov_wt))
show("s_XY directly from the definition", "{!r}".format(cov_sample(SP, AGG)))
show("product from 4-dp displays .7763 x 21.6329 x 8.7106", "{:.4f}".format(0.7763 * 21.6329 * 8.7106))
show("TRAP: r x sigma_x x sigma_y (population covariance)", "{:.4f} -> {:.2f}".format(r_wt * s_pop(SP) * s_pop(AGG), r_wt * s_pop(SP) * s_pop(AGG)))
show("sums: Sx^2", d4(sum(v * v for v in SP)))
show("sums: Sxy", d4(sum(p * q for p, q in zip(SP, AGG))))
print("  Year by year (deviation from mean, sign of the cross-product):")
ddx, ddy = devs(SP), devs(AGG)
tot = 0
for yr, u, v in zip(YEARS, ddx, ddy):
    tot += u * v
    show("  {}  dX = {:+.4f}  dY = {:+.4f}".format(yr, u, v), "product {:+.4f}".format(u * v))
show("  sum of cross-products", "{:.4f}".format(tot))
show("  sum / (n - 1)", "{:.4f}".format(tot / (n - 1)))
share22 = ddx[4] * ddy[4] / tot
show("  |walkthrough cov| / |worked-example cov| (c2 explanation)",
     "{:.4f} (about eight times)".format(abs(cov_wt) / abs(sxy)))
show("  2022's share of the sum of cross-products", "{:.4f} (about two-thirds)".format(share22))
show("  years on the same side of both means (product > 0)",
     ", ".join(str(yr) for yr, u, v in zip(YEARS, ddx, ddy) if u * v > 0))
show("  years split (product < 0)",
     ", ".join(str(yr) for yr, u, v in zip(YEARS, ddx, ddy) if u * v < 0))
# Outlier sensitivity: drop 2022
r_ex22 = corr(SP[:4], AGG[:4])
show("r for 2018-2021 only (drop 2022)", "{!r} -> {:.4f} -> {:.2f}".format(r_ex22, r_ex22, r_ex22))
show("cov for 2018-2021 only", "{:.4f}".format(cov_sample(SP[:4], AGG[:4])))
rx, ry, d, sd2, rs = spearman(SP, AGG)
show("Spearman on 2018-2022: ranks X", fmt_list(rx))
show("Spearman on 2018-2022: ranks Y", fmt_list(ry))
show("Spearman on 2018-2022: sum d^2, r_s", "{} , {:.4f}".format(sd2, rs))

# ---------------------------------------------------------------------------
print("\n=== Nonlinear example: Y = X^2 ===")
NX = [-2, -1, 0, 1, 2]
NY = [x * x for x in NX]
show("X", fmt_list(NX))
show("Y = X^2", fmt_list(NY))
show("covariance", "{:.4f}".format(cov_sample(NX, NY)))
show("r", "{:.4f}".format(corr(NX, NY)))

# ---------------------------------------------------------------------------
print("\n=== c1: your turn (LIN statistics) ===")
C1X = [12, -3, 7, 20, -6]
C1Y = [4, 6, 3, -2, 7]
show("X", fmt_list(C1X))
show("Y", fmt_list(C1Y))
show("x-bar", d4(mean(C1X)))
show("Sx", "{!r} -> {}".format(s_sample(C1X), d4(s_sample(C1X))))
show("sigma_x", d4(s_pop(C1X)))
show("y-bar", d4(mean(C1Y)))
show("Sy", "{!r} -> {}".format(s_sample(C1Y), d4(s_sample(C1Y))))
show("sigma_y", d4(s_pop(C1Y)))
r1 = corr(C1X, C1Y)
show("r", "{!r} -> {}".format(r1, d4(r1)))
c1 = r1 * s_sample(C1X) * s_sample(C1Y)
show("covariance r x Sx x Sy (answer)", "{!r} -> {} -> {:.2f}".format(c1, d4(c1), c1))
show("covariance from definition", "{!r}".format(cov_sample(C1X, C1Y)))
show("sum of cross-products", "{:.4f}".format(sum(p * q for p, q in zip(devs(C1X), devs(C1Y)))))
show("cross-products", fmt_list(["{:.1f}".format(p * q) for p, q in zip(devs(C1X), devs(C1Y))], "{}"))
show("X deviations", fmt_list(devs(C1X)))
show("Y deviations", fmt_list(devs(C1Y)))
show("product from 4-dp displays -.9260 x 10.7005 x 3.5071", "{:.4f}".format(-0.9260 * 10.7005 * 3.5071))
show("TRAP: r x sigma_x x sigma_y", "{:.4f} -> {:.2f}".format(r1 * s_pop(C1X) * s_pop(C1Y), r1 * s_pop(C1X) * s_pop(C1Y)))

# ---------------------------------------------------------------------------
print("\n=== c3: Spearman rank correlation (twist) ===")
Y1 = [12.5, 8.1, 15.2, 3.4, 9.9, 6.0]
Y2 = [10.1, 9.0, 11.8, 2.2, 6.5, 7.7]
rx, ry, d, sd2, rs = spearman(Y1, Y2)
show("Year 1", fmt_list(Y1))
show("Year 2", fmt_list(Y2))
show("ranks Year 1 (1 = highest)", fmt_list(rx))
show("ranks Year 2 (1 = highest)", fmt_list(ry))
show("d", fmt_list(d))
show("d^2", fmt_list([v * v for v in d]))
show("sum d^2", sd2)
show("6 x sum d^2", 6 * sd2)
show("n(n^2 - 1) with n = 6", 6 * (36 - 1))
show("6 sum d^2 / n(n^2-1)", "{:.4f}".format(6 * sd2 / 210))
show("r_s (answer)", "{!r} -> {:.4f}".format(rs, rs))
show("check: Pearson r of the ranks", "{!r} -> {:.4f}".format(corr(rx, ry), corr(rx, ry)))
show("Pearson r of the raw returns", "{:.4f}".format(corr(Y1, Y2)))
show("ranks 1..6 sum of squared deviations n(n^2-1)/12", "{} vs {}".format(sum((k - 3.5) ** 2 for k in range(1, 7)), 6 * 35 / 12))
show("TRAP: forgetting the 1 - (just 6 sum d^2 / n(n^2-1))", "{:.4f}".format(6 * sd2 / 210))
show("TRAP: n(n-1) instead of n(n^2-1): 1 - 36/30", "{:.4f}".format(1 - 36 / 30))

# ---------------------------------------------------------------------------
print("\n=== c4: exam style (three years) ===")
C4X = [10, 20, 0]
C4Y = [4, 2, 9]
show("means", "{} , {}".format(mean(C4X), mean(C4Y)))
show("X deviations", fmt_list(devs(C4X)))
show("Y deviations", fmt_list(devs(C4Y)))
cp = [p * q for p, q in zip(devs(C4X), devs(C4Y))]
show("cross-products", fmt_list(cp))
show("sum", sum(cp))
show("correct: sum/(n-1)", "{!r} -> {:.2f}".format(sum(cp) / 2, sum(cp) / 2))
show("divided by n", "{!r} -> {:.2f}".format(sum(cp) / 3, sum(cp) / 3))
show("not divided", "{:.2f}".format(sum(cp)))
show("check: cov_sample", "{!r}".format(cov_sample(C4X, C4Y)))
show("calculator display r x Sx x Sy", d4(corr(C4X, C4Y) * s_sample(C4X) * s_sample(C4Y)))
show("r for c4 data", "{:.4f}".format(corr(C4X, C4Y)))

# ---------------------------------------------------------------------------
print("\n=== 'Why +-1 is the limit' identity check (walkthrough data) ===")
zx = [u / s_sample(SP) for u in devs(SP)]
zy = [v / s_sample(AGG) for v in devs(AGG)]
show("sum zX^2 and sum zY^2 (both n - 1 = 4)", "{:.4f} , {:.4f}".format(sum(z * z for z in zx), sum(z * z for z in zy)))
show("sum zX zY / (n - 1) = r", "{:.4f}".format(sum(p * q for p, q in zip(zx, zy)) / (n - 1)))
show("sum (zX - zY)^2 vs 2(n-1)(1-r)", "{:.4f} vs {:.4f}".format(sum((p - q) ** 2 for p, q in zip(zx, zy)), 2 * (n - 1) * (1 - r_wt)))
show("sum (zX + zY)^2 vs 2(n-1)(1+r)", "{:.4f} vs {:.4f}".format(sum((p + q) ** 2 for p, q in zip(zx, zy)), 2 * (n - 1) * (1 + r_wt)))

# ---------------------------------------------------------------------------
print("\n=== Spearman shortcut identity check (n = 4, 5, 6) ===")
for k in (4, 5, 6):
    show("n = {}: sum of (rank - mean rank)^2 vs n(n^2-1)/12".format(k),
         "{} vs {}".format(sum((j - (k + 1) / 2) ** 2 for j in range(1, k + 1)), k * (k * k - 1) / 12))
