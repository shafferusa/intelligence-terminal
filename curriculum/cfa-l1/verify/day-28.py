#!/usr/bin/env python3
"""Day 28 of CFA Math: every number that appears on day-28.html.

Week 4 review (Days 22-27): index weighting, quantiles and trimmed /
winsorized means, dispersion and target downside deviation, covariance,
correlation and Spearman's rank correlation, expected value and the total
probability rule, Bayes' formula (CFA Program 2027 Level I, Quantitative
Methods, LM3 Benchmarking Returns, LM5 Statistical Characteristics of Asset
Returns, LM6 Statistical Distributions for Financial Asset Prices and Returns).
Warm-up from Day 27 (Bayes) and Day 12 (geometric mean).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math


def show(label, value):
    print("  {:<66} {}".format(label, value))


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


def one_var(xs, freqs=None):
    """What 1-V (BA II Plus) / 1-Var Stats (TI-84) show."""
    if freqs is None:
        freqs = [1] * len(xs)
    n = sum(freqs)
    m = sum(f * x for f, x in zip(freqs, xs)) / n
    ss = sum(f * (x - m) ** 2 for f, x in zip(freqs, xs))
    return {"n": n, "mean": m, "ss": ss, "Sx": math.sqrt(ss / (n - 1)), "sx": math.sqrt(ss / n)}


def two_var(xs, ys):
    """What LIN (BA II Plus) / 2-Var Stats + LinReg(a+bx) (TI-84) show."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    Sx, Sy = math.sqrt(sxx / (n - 1)), math.sqrt(syy / (n - 1))
    r = sxy / math.sqrt(sxx * syy)
    b = sxy / sxx
    a = my - b * mx
    return {"n": n, "mx": mx, "my": my, "sxx": sxx, "syy": syy, "sxy": sxy, "Sx": Sx, "Sy": Sy,
            "sigx": math.sqrt(sxx / n), "sigy": math.sqrt(syy / n), "r": r, "a": a, "b": b,
            "cov": sxy / (n - 1)}


def bayes(prior, p_i_e, p_i_not_e):
    top = p_i_e * prior
    bottom = p_i_not_e * (1 - prior)
    return top, bottom, top + bottom, top / (top + bottom)


# ---------------------------------------------------------------------------
print("=== w1 (Day 27): downgrade and a spread jump ===")
PRIOR, PJ_D, PJ_N = 0.20, 0.75, 0.15
top, bot, p_j, post = bayes(PRIOR, PJ_D, PJ_N)
show("P(not downgraded) = 1 - 0.20", "{:.2f}".format(1 - PRIOR))
show("top branch 0.75 x 0.20 (STO 1 / ->A)", both(top))
show("bottom branch 0.15 x 0.80", both(bot))
show("P(jump) = 0.15 + 0.12", both(p_j))
show("P(downgrade | jump) (answer)", "{!r} -> {} -> {}".format(post, both(post), pct(post)))
show("  data-answer (percent)", repr(100 * post))
show("  of 100 bonds: downgraded, jumps among them", "{:.0f}, {:.0f}".format(100 * PRIOR, 100 * top))
show("  of 100 bonds: jumps among the 80 others, all jumps", "{:.0f}, {:.0f}".format(100 * bot, 100 * p_j))
show("  trap: inverted conditional P(jump | downgrade)", pct(PJ_D))
show("  trap: prior ignored 0.75/(0.75 + 0.15)", pct(PJ_D / (PJ_D + PJ_N)))

print("\n=== w2 (Day 12): compound annual return of -20%, +35%, +8% ===")
W2 = [-0.20, 0.35, 0.08]
prod = 1.0
for r_ in W2:
    prod *= 1 + r_
geo = prod ** (1 / 3) - 1
show("growth factors 0.80 x 1.35 x 1.08", both(prod))
show("cube root of the product", both(prod ** (1 / 3)))
show("geometric mean (A, right)", "{!r} -> {}".format(geo, pct(geo)))
show("  display after - 1 =", both(geo))
show("  unrounded rate, 4 decimals (as a percent)", "{:.4f}%".format(100 * geo))
show("  three years at the unrounded rate: 100 x (1 + g)^3", "{:.2f}".format(100 * (1 + geo) ** 3))
show("  three years at the rounded 5.26%: 100 x 1.0526^3 (not 116.64)",
     "{:.2f}".format(100 * round(prod ** (1 / 3), 4) ** 3))
show("arithmetic mean (B)", "{:.4f}% -> {}".format(100 * sum(W2) / 3, pct(sum(W2) / 3)))
show("three-year total, root not taken (C)", pct(prod - 1))
show("$100 path: after year 1, 2, 3", ", ".join("{:.2f}".format(100 * v) for v in
                                                   (0.80, 0.80 * 1.35, prod)))

# ---------------------------------------------------------------------------
print("\n=== c1 (Day 22): price-weighted divisor after a 5-for-1 split ===")
P = [40, 60, 25, 155]
D_OLD, SPLIT = 2.8, 5
s_old = sum(P)
idx = s_old / D_OLD
post_price = 155 / SPLIT
s_new = 40 + 60 + 25 + post_price
d_new = s_new / idx
false_idx = s_new / D_OLD
show("sum of prices before", s_old)
show("index before = 280 / 2.8", both(idx))
show("post-split price 155 / 5", both(post_price))
show("sum after", both(s_new))
show("new divisor = 156 / 100 (answer)", "{!r} -> {}".format(d_new, both(d_new)))
show("  check: 156 / 1.56", "{:.4f}".format(s_new / d_new))
show("trap: old divisor kept: 156 / 2.8", both(false_idx))
show("  false change in index points (= % since base 100)", both(false_idx - idx))
show("  false %CH via Delta% with OLD 100, NEW 55.7143", "{:.4f}".format(100 * (55.7143 - 100) / 100))
show("  false %CH exact", "{:.4f}".format(100 * (false_idx / idx - 1)))
show("trap: divisor divided by the split ratio 2.8 / 5", "{:.4f}".format(D_OLD / SPLIT))
show("weight of the big stock before / after (price weight)",
     "{} / {}".format(pct(155 / s_old), pct(post_price / s_new)))

print("\n=== c2 (Day 22): float-adjusted weight ===")
stocks = {"A": (50, 300, 120), "B": (20, 1000, 0), "C": (80, 200, 50)}
flt = {k: (p, q - h) for k, (p, q, h) in stocks.items()}
fcap = {k: p * f for k, (p, f) in flt.items()}
tot_f = sum(fcap.values())
for k in "ABC":
    show("float {} = shares - closely held; float cap".format(k), "{} m; {:,} m".format(flt[k][1], fcap[k]))
show("total float cap", "{:,}".format(tot_f))
for k in "ABC":
    show("float weight {}".format(k), "{!r} -> {}".format(fcap[k] / tot_f, pct(fcap[k] / tot_f)))
ocap = {k: p * q for k, (p, q, h) in stocks.items()}
tot_o = sum(ocap.values())
show("shares-outstanding caps A, B, C; total", "{}, {}, {}; {:,}".format(ocap["A"], ocap["B"], ocap["C"], tot_o))
show("C (trap): A's cap weight on shares outstanding", "{!r} -> {}".format(ocap["A"] / tot_o, pct(ocap["A"] / tot_o)))
psum = sum(p for p, q, h in stocks.values())
show("A (trap): price weight 50 / 150", "{!r} -> {}".format(50 / psum, pct(50 / psum)))
show("equal weight (not offered)", pct(1 / 3))

# ---------------------------------------------------------------------------
print("\n=== c3 (Day 23): 60th percentile of 11 returns ===")
R3 = [5.2, -1.4, 3.8, 9.1, 0.6, -3.7, 2.9, 7.5, 1.1, 4.4, -0.2]
srt = sorted(R3)
n3 = len(R3)
show("sorted", ", ".join("{:g}".format(x) for x in srt))
L = (n3 + 1) * 60 / 100
w = int(L)
frac = L - w
lo, hi = srt[w - 1], srt[w]
p60 = lo + frac * (hi - lo)
show("L = (11 + 1) x 60/100", "{:.4f}".format(L))
show("7th, 8th values", "{}, {}".format(lo, hi))
show("gap 4.4 - 3.8; 0.2 x gap", "{:.4f}; {:.4f}".format(hi - lo, frac * (hi - lo)))
show("P60 (answer)", "{!r} -> {}".format(p60, both(p60)))
Lw = n3 * 60 / 100
lw = srt[int(Lw) - 1] + (Lw - int(Lw)) * (srt[int(Lw)] - srt[int(Lw) - 1])
show("trap: L = n x y/100 = 6.6 -> 6th + 0.6 x (7th - 6th)", "{:.4f} -> {:.4f}".format(Lw, lw))
show("trap: unsorted 7th and 8th: 2.9 + 0.2 x (7.5 - 2.9)", "{:.4f}".format(R3[6] + 0.2 * (R3[7] - R3[6])))
show("median (6th of 11)", srt[5])

print("\n=== c4 (Day 23): mean, trimmed and winsorized ===")
R4 = [-12.0, -2.0, 0.5, 1.5, 2.0, 3.0, 3.5, 4.5, 6.0, 18.0]
s4 = sum(R4)
mean4 = s4 / len(R4)
trim = R4[1:-1]
win = [R4[1]] + R4[1:-1] + [R4[-2]]
show("sum, plain mean (A)", "{:.1f}, {:.4f} -> {:.2f}%".format(s4, mean4, mean4))
show("trimmed: sum of 8, mean (B)", "{:.1f}, {:.4f} -> {:.2f}%".format(sum(trim), sum(trim) / 8, sum(trim) / 8))
show("winsorized: list", ", ".join("{:g}".format(x) for x in win))
show("winsorized: sum, mean (C, right)", "{:.1f}, {!r} -> {:.2f}%".format(sum(win), sum(win) / 10, sum(win) / 10))
show("  sum change: +10 at the bottom, -12 at the top", "{:.1f}".format((R4[1] - R4[0]) + (R4[-2] - R4[-1])))
show("  share reset at each end (1 of 10)", pct(1 / 10, 0))
show("  name: winsorized keeps 100 - 2 x 10 = ...%; trimmed drops 2 x 10 = ...%",
     "{}; {}".format(pct(1 - 2 / 10, 0), pct(2 / 10, 0)))

# ---------------------------------------------------------------------------
print("\n=== c5 (Day 24): sample SD of six returns ===")
R5 = [9.0, -3.5, 4.0, 12.5, -1.0, 6.0]
st5 = one_var(R5)
show("sum, mean", "{:.1f}, {}".format(sum(R5), both(st5["mean"])))
devs = [x - st5["mean"] for x in R5]
show("deviations", ", ".join("{:.1f}".format(d) for d in devs))
show("squared deviations", ", ".join("{:.2f}".format(d * d) for d in devs))
show("sum of squares", "{:.2f}".format(st5["ss"]))
show("sample variance 181 / 5", "{:.4f}".format(st5["ss"] / 5))
show("Sx (answer)", "{!r} -> {}".format(st5["Sx"], both(st5["Sx"])))
show("population variance 181 / 6", "{:.4f}".format(st5["ss"] / 6))
show("sigma x (trap)", "{!r} -> {}".format(st5["sx"], both(st5["sx"])))
show("n display", both(st5["n"]))
show("CV = Sx / mean (not asked)", "{:.4f}".format(st5["Sx"] / st5["mean"]))

print("\n=== c6 (Day 24): target downside deviation, B = 2% ===")
B = 2.0
below = [x for x in R5 if x <= B]
sh = [x - B for x in below]
ssq = sum(s * s for s in sh)
tdd = math.sqrt(ssq / (len(R5) - 1))
show("returns at or below 2", ", ".join("{:g}".format(x) for x in below))
show("shortfalls x - 2", ", ".join("{:.1f}".format(s) for s in sh))
show("squares", ", ".join("{:.2f}".format(s * s) for s in sh))
show("sum of squares (display after =)", both(ssq))
show("divide by n - 1 = 5", both(ssq / 5))
show("target downside deviation (answer)", "{!r} -> {}".format(tdd, both(tdd)))
show("trap: divide by the 2 below-target years", "{:.4f}".format(math.sqrt(ssq / len(below))))
show("trap: divide by n = 6", "{:.4f}".format(math.sqrt(ssq / len(R5))))
z = [x for x in R5 if x <= 0]
show("trap: shortfalls from 0 instead of B", "{:.4f}".format(math.sqrt(sum(x * x for x in z) / 5)))
show("  for comparison: Sx", "{:.4f}".format(st5["Sx"]))

# ---------------------------------------------------------------------------
print("\n=== c7 (Day 25): covariance via r x Sx x Sy ===")
X7 = [15, -8, 4, 22, 9]
Y7 = [3, 5, -1, 6, 2]
t7 = two_var(X7, Y7)
show("x-bar, y-bar", "{} ; {}".format(both(t7["mx"]), both(t7["my"])))
dx = [x - t7["mx"] for x in X7]
dy = [y - t7["my"] for y in Y7]
show("x deviations", ", ".join("{:.1f}".format(v) for v in dx))
show("y deviations", ", ".join("{:.0f}".format(v) for v in dy))
show("products", ", ".join("{:.1f}".format(a * b) for a, b in zip(dx, dy)))
show("sum of products; / 4", "{:.4f}; {:.4f}".format(t7["sxy"], t7["sxy"] / 4))
show("Sx", "{!r} -> {}".format(t7["Sx"], both(t7["Sx"])))
show("Sy", "{!r} -> {}".format(t7["Sy"], both(t7["Sy"])))
show("sigma x, sigma y", "{} ; {}".format(both(t7["sigx"]), both(t7["sigy"])))
show("r", "{!r} -> {}".format(t7["r"], both(t7["r"])))
show("a, b (LinReg / LIN)", "{} ; {}".format(both(t7["a"]), both(t7["b"])))
show("r squared", both(t7["r"] ** 2))
cov = t7["r"] * t7["Sx"] * t7["Sy"]
show("r x Sx x Sy (answer)", "{!r} -> {}".format(cov, both(cov)))
retyped = 0.2007 * 11.3710 * 2.7386
show("retyped rounded: 0.2007 x 11.3710 x 2.7386", "{:.4f}".format(retyped))
show("trap: r x sigma x x sigma y (population covariance)", "{:.4f}".format(t7["r"] * t7["sigx"] * t7["sigy"]))
show("  Sxx/4, Syy/4", "{:.2f}, {:.2f}".format(t7["sxx"] / 4, t7["syy"] / 4))

print("\n=== c8 (Day 25): Spearman from two analysts' ranks ===")
R1 = [1, 2, 3, 4, 5, 6]
R2 = [1, 3, 2, 6, 4, 5]
d = [a - b for a, b in zip(R1, R2)]
sd2 = sum(v * v for v in d)
n8 = 6
rs = 1 - 6 * sd2 / (n8 * (n8 ** 2 - 1))
show("d = rank1 - rank2", ", ".join("{:+d}".format(v) if v else "0" for v in d))
show("d squared; sum", "{}; {}".format(", ".join(str(v * v) for v in d), sd2))
show("6 x sum d^2; n(n^2 - 1)", "{}; {}".format(6 * sd2, n8 * (n8 ** 2 - 1)))
show("r_s = 1 - 48/210 (C, right)", "{!r} -> {:.4f}".format(rs, rs))
show("48/210", "{:.4f}".format(48 / 210))
show("A (trap): the 6 left out 1 - 8/210", "{:.4f}".format(1 - sd2 / (n8 * (n8 ** 2 - 1))))
show("B (trap): n(n - 1) = 30: 1 - 48/30", "{:.4f}".format(1 - 6 * sd2 / (n8 * (n8 - 1))))
show("check: Pearson r of the ranks", "{:.4f}".format(two_var(R1, R2)["r"]))

# ---------------------------------------------------------------------------
print("\n=== c9 (Day 26): probability-weighted SD over two scenarios ===")
PS, PW = 0.55, 0.45
outs = [18.0, 8.0, 2.0, -10.0]
probs = [PS * 0.6, PS * 0.4, PW * 0.5, PW * 0.5]
show("path probabilities", ", ".join("{:.3f}".format(p) for p in probs))
show("  sum", "{:.4f}".format(sum(probs)))
freqs = [round(200 * p) for p in probs]
show("weights x 100 (not all whole)", ", ".join("{:g}".format(100 * p) for p in probs))
show("whole-number weights x 200", ", ".join(str(f) for f in freqs))
e_s = 0.6 * 18 + 0.4 * 8
e_w = 0.5 * 2 + 0.5 * (-10)
show("E(R | strong) = 0.6 x 18 + 0.4 x 8", "{:.2f} + {:.2f} = {:.4f}".format(0.6 * 18, 0.4 * 8, e_s))
show("E(R | weak) = 0.5 x 2 + 0.5 x (-10)", "{:.2f} + {:.2f} = {:.4f}".format(0.5 * 2, 0.5 * -10, e_w))
e = PS * e_s + PW * e_w
show("E(R) = 0.55 x 14 + 0.45 x (-4)", "{:.2f} + {:.2f} = {!r}".format(PS * e_s, PW * e_w, e))
show("  check: sum p x", "{:.4f}".format(sum(p * x for p, x in zip(probs, outs))))
dv = [x - e for x in outs]
show("deviations from 5.90", ", ".join("{:.1f}".format(v) for v in dv))
show("squared", ", ".join("{:.2f}".format(v * v) for v in dv))
show("weighted p x dev^2", ", ".join("{:.5f}".format(p * v * v) for p, v in zip(probs, dv)))
var9 = sum(p * v * v for p, v in zip(probs, dv))
sd9 = math.sqrt(var9)
show("variance", "{:.4f}".format(var9))
show("SD (answer)", "{!r} -> {}".format(sd9, both(sd9)))
st9 = one_var(outs, freqs)
show("1-V with weights: n, mean", "{} ; {}".format(both(st9["n"]), both(st9["mean"])))
show("1-V with weights: Sx (trap), sigma x (right)", "{} ; {}".format(both(st9["Sx"]), both(st9["sx"])))
show("trap: plain SD of the four outcomes, unweighted (Sx)", "{:.4f}".format(one_var(outs)["Sx"]))
show("trap: plain mean of four outcomes", "{:.4f}".format(sum(outs) / 4))

print("\n=== c10 (Day 27): skilled manager after three beats ===")
top, bot, p3, post10 = bayes(0.10, 0.60, 0.5 ** 3)
show("0.5^3", "{:.4f}".format(0.5 ** 3))
show("top 0.60 x 0.10; bottom 0.125 x 0.90", "{:.4f}; {:.4f}".format(top, bot))
show("P(three beats)", "{:.4f}".format(p3))
show("  displays: 0.6 x 0.1 (STO 1); RCL1 / (RCL1 + .125 x .9)", "{} ; {}".format(both(top), both(post10)))
show("A (right): P(skilled | three beats)", "{!r} -> {}".format(post10, pct(post10)))
show("B (trap): P(three beats | skilled)", pct(0.60))
show("C (trap): prior ignored 0.60/(0.60 + 0.125)", "{!r} -> {}".format(0.6 / 0.725, pct(0.6 / 0.725)))
show("  of 1,000 managers: skilled, skilled with 3 beats", "{:.0f}, {:.0f}".format(100, 1000 * top))
show("  of 1,000 managers: unskilled with 3 beats, all with 3 beats", "{:.1f}, {:.1f}".format(1000 * bot, 1000 * p3))
show("  lucky share of the three-beat managers", pct(bot / p3))
