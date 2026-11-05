#!/usr/bin/env python3
"""Day 23 of CFA Math: every number that appears on day-23.html.

Median, mode, quantiles located with L_y = (n + 1) y / 100 and linear
interpolation, the interquartile range, and trimmed and winsorized means
(CFA Program 2027 Level I, Quantitative Methods, LM5 Statistical
Characteristics of Asset Returns). Plain Python 3, standard library only.
Run it and copy the printed numbers.
"""

import math


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def show(label, value):
    print("  {:<66} {}".format(label, value))


def mean(xs):
    return sum(xs) / len(xs)


def location(n, y):
    """CFA location of the y-th percentile, L_y = (n + 1) y / 100 (1-based)."""
    return (n + 1) * y / 100.0


def interp(sorted_xs, loc):
    """Value at a (possibly fractional) 1-based location, interpolating linearly."""
    whole = int(math.floor(loc))
    frac = loc - whole
    if whole < 1:
        return sorted_xs[0]
    if whole >= len(sorted_xs):
        return sorted_xs[-1]
    below, above = sorted_xs[whole - 1], sorted_xs[whole]
    return below + frac * (above - below)


def percentile(xs, y):
    s = sorted(xs)
    loc = location(len(s), y)
    return interp(s, loc), loc


def median_plain(s):
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def ti84_quartiles(xs):
    """TI-84 1-Var Stats: Q1/Q3 are the medians of the lower/upper halves,
    leaving out the middle value when n is odd."""
    s = sorted(xs)
    n = len(s)
    lower = s[: n // 2]
    upper = s[(n + 1) // 2:]
    return median_plain(lower), median_plain(s), median_plain(upper)


def trimmed(xs, k):
    s = sorted(xs)
    kept = s[k: len(s) - k]
    return sum(kept) / len(kept), kept


def winsorized(xs, k):
    s = sorted(xs)
    lo, hi = s[k], s[len(s) - 1 - k]
    w = [lo] * k + s[k: len(s) - k] + [hi] * k
    return sum(w) / len(w), w


def sample_sd(xs):
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def pop_sd(xs):
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def mode(xs):
    counts = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    top = max(counts.values())
    return [x for x, c in counts.items() if c == top], top


def bond_price(n, r, pmt, fv):
    return sum(pmt / (1 + r) ** t for t in range(1, n + 1)) + fv / (1 + r) ** n


def solve_rate(n, price, pmt, fv):
    """Per-period yield by bisection (decimal)."""
    lo, hi = -0.99, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if bond_price(n, mid, pmt, fv) > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------- warm-up
print("=== Warm-up w1 (from Day 22): new divisor after a 3-for-1 split ===")
prices = [45, 120, 80]
div0 = 1.75
idx = sum(prices) / div0
after = [45, 120 / 3.0, 80]
div1 = sum(after) / idx
show("sum of prices 45 + 120 + 80", sum(prices))
show("index before = 245 / 1.75", d4(idx))
show("120 / 3 after the split", d4(120 / 3.0))
show("new sum 45 + 40 + 80", sum(after))
show("new divisor = 165 / 140 (answer)", "{!r} -> {}".format(div1, d4(div1)))
show("check: 165 / new divisor", d4(sum(after) / div1))
show("check with the rounded divisor: 165 / 1.1786 (about 140)", d4(sum(after) / 1.1786))
show("trap: keep 1.75 -> index after split 165 / 1.75", d4(sum(after) / div0))
show("trap: divide old divisor by 3, 1.75 / 3", d4(div0 / 3))
show("trap: keeping 1.75, change in index 94.2857/140 - 1", "{:.2f}%".format(100 * (sum(after) / div0 / idx - 1)))

print("\n=== Warm-up w2 (from Day 16): yield-to-worst ===")
ytm = solve_rate(6, 104.0, 5.0, 100.0)
ytc = solve_rate(2, 104.0, 5.0, 101.0)
ytc_par = solve_rate(2, 104.0, 5.0, 100.0)
show("YTM: N = 6, PV = -104, PMT = 5, FV = 100 (A, not worst)", "{!r} -> {} -> {:.2f}%".format(100 * ytm, d4(100 * ytm), 100 * ytm))
show("YTC: N = 2, PV = -104, PMT = 5, FV = 101 (C, right)", "{!r} -> {} -> {:.2f}%".format(100 * ytc, d4(100 * ytc), 100 * ytc))
show("YTC with par as FV = 100 (B, trap)", "{!r} -> {} -> {:.2f}%".format(100 * ytc_par, d4(100 * ytc_par), 100 * ytc_par))
show("check price at YTC", d4(bond_price(2, ytc, 5, 101)))
show("check price at YTM", d4(bond_price(6, ytm, 5, 100)))
show("yield-to-worst = min(YTM, YTC)", "{:.2f}%".format(100 * min(ytm, ytc)))

# ---------------------------------------------------------------- the idea / walkthrough
print("\n=== Walkthrough: nine judges' marks (illustrative) ===")
J = [8.50, 8.25, 9.75, 8.75, 8.00, 8.50, 6.50, 8.75, 8.50]
n = len(J)
S = sorted(J)
show("marks as given", J)
show("sorted", S)
show("n", n)
show("sum x (Sigma x)", "{!r} -> {}".format(sum(J), d4(sum(J))))
show("mean x-bar", "{!r} -> {}".format(mean(J), d4(mean(J))))
show("Sx (sample SD, Day 24)", d4(sample_sd(J)))
show("sigma x (population SD, Day 24)", d4(pop_sd(J)))
show("min, max", "{} {}".format(d4(min(J)), d4(max(J))))
md, cnt = mode(J)
show("mode", "{} (appears {} times)".format(md, cnt))
for y in (25, 50, 75):
    p, loc = percentile(J, y)
    show("L_{} = 10 x {}/100 -> P_{}".format(y, y, y), "L = {}, P = {!r} -> {}".format(loc, p, d4(p)))
show("Q1 interpolation 8 + 0.5 x (8.25 - 8)", d4(8 + 0.5 * (8.25 - 8)))
q1 = percentile(J, 25)[0]
q3 = percentile(J, 75)[0]
show("IQR = Q3 - Q1", d4(q3 - q1))
show("TI-84 1-Var Stats Q1, Med, Q3", [d4(v) for v in ti84_quartiles(J)])
tm, kept = trimmed(J, 1)
show("trimmed (drop 6.50 and 9.75): kept", kept)
show("sum kept = 75.5 - 6.5 - 9.75", d4(sum(J) - 6.5 - 9.75))
show("trimmed mean = 59.25 / 7", "{!r} -> {}".format(tm, d4(tm)))
show("share dropped at each end 1/9", "{:.4f}% -> trimmed-mean name {:.1f}%".format(100 / 9.0, 200 / 9.0))
wm, w = winsorized(J, 1)
show("winsorized list", w)
show("winsorized sum 75.5 - 6.5 - 9.75 + 8 + 8.75", d4(sum(J) - 6.5 - 9.75 + 8 + 8.75))
show("winsorized mean = 76 / 9", "{!r} -> {}".format(wm, d4(wm)))
show("trap: winsorized sum / 7 instead of / 9", d4(76 / 7.0))
show("trap: trimmed sum / 9 instead of / 7", d4(59.25 / 9))
show("trap: Q1 with location n x y/100 = 2.25", d4(interp(S, 9 * 0.25)))
show("trap: Q3 with location n x y/100 = 6.75", d4(interp(S, 9 * 0.75)))

print("\n=== Back to the story: raise the top mark 9.75 -> 10.00 ===")
J2 = [10.00 if x == 9.75 else x for x in J]
show("plain mean after", "{!r} -> {}".format(mean(J2), d4(mean(J2))))
show("change in plain mean = 0.25 / 9", "{!r} -> {}".format(mean(J2) - mean(J), d4(mean(J2) - mean(J))))
show("trimmed mean after", d4(trimmed(J2, 1)[0]))
show("median after", d4(percentile(J2, 50)[0]))
show("winsorized mean after", d4(winsorized(J2, 1)[0]))
show("each mark's weight in the plain mean 1/9", "{:.4f} -> {:.1f}%".format(1 / 9.0, 100 / 9.0))
show("each kept mark's weight in the trimmed mean 1/7", "{:.4f} -> {:.1f}%".format(1 / 7.0, 100 / 7.0))
show("trimmed mean minus plain mean (8.4643 - 8.3889)", d4(tm - mean(J)))
show("9.75 above the trimmed mean / 6.50 below it", "{} / {}".format(d4(9.75 - tm), d4(tm - 6.5)))
# lean a little, inside the pack: the 8.75 judge gives 9.00 instead
J3 = list(J)
J3[3] = 9.00
show("one 8.75 becomes 9.00 (inside the pack): trimmed mean", d4(trimmed(J3, 1)[0]))
show("  change = 0.25 / 7", d4(trimmed(J3, 1)[0] - tm))

# ---------------------------------------------------------------- why it works
print("\n=== Why it works: locations ===")
for nn in (9, 12):
    show("median location (n + 1)/2, n = {}".format(nn), location(nn, 50))
show("n = 9: L_25, L_75 with (n + 1)", "{} {}".format(location(9, 25), location(9, 75)))
show("  distance in from each end", "{} {}".format(location(9, 25) - 1, 9 - location(9, 75)))
show("n = 9: with n instead, 9 x 0.25, 9 x 0.75", "{} {}".format(9 * 0.25, 9 * 0.75))
show("  distance in from each end", "{} {}".format(9 * 0.25 - 1, 9 - 9 * 0.75))
show("example: d = 2 points off moves the mean by 2/9", d4(2 / 9.0))

# ---------------------------------------------------------------- worked example
print("\n=== Worked example: twelve monthly returns ===")
R = [2.1, -0.8, 1.5, 3.4, -4.6, 0.9, 1.2, -1.1, 2.6, 0.4, 7.9, -0.3]
SR = sorted(R)
nr = len(R)
show("sorted", SR)
show("n", nr)
for y in (50, 25, 75, 10):
    p, loc = percentile(R, y)
    w = int(math.floor(loc))
    show("L_{} = 13 x {}/100".format(y, y), "L = {:.4f}: between {} and {} -> {!r} -> {:.4f}".format(
        loc, SR[w - 1], SR[w], p, p))
q1r = percentile(R, 25)[0]
q3r = percentile(R, 75)[0]
show("IQR = Q3 - Q1", "{!r} -> {:.4f}".format(q3r - q1r, q3r - q1r))
show("sum", "{!r} -> {:.4f}".format(sum(R), sum(R)))
show("mean", "{!r} -> {:.4f}".format(mean(R), mean(R)))
tr, keptr = trimmed(R, 1)
show("trimmed: kept sum", "{:.4f} over {}".format(sum(keptr), len(keptr)))
show("trimmed mean", "{!r} -> {:.4f}".format(tr, tr))
show("share dropped at each end 1/12", "{:.4f}% -> trimmed-mean name {:.1f}%".format(100 / 12.0, 200 / 12.0))
show("winsorized-mean name: share left untouched 100 - 2 x 8.333", "{:.1f}%".format(100 - 200 / 12.0))
wr, wl = winsorized(R, 1)
show("winsorized list", wl)
show("winsorized sum", "{:.4f}".format(sum(wl)))
show("winsorized mean", "{!r} -> {:.4f}".format(wr, wr))
show("TI-84 Q1, Med, Q3", ["{:.4f}".format(v) for v in ti84_quartiles(R)])
show("7.9 above the trimmed mean / -4.6 below it", "{:.4f} / {:.4f}".format(7.9 - tr, tr - (-4.6)))
show("trap: Q1 with n x y/100 = 3 -> 3rd value", "{:.4f}".format(interp(SR, nr * 0.25)))
show("trap: Q3 with n x y/100 = 9 -> 9th value", "{:.4f}".format(interp(SR, nr * 0.75)))
show("trap: unsorted list, L_25 = 3.25", "{:.4f}".format(interp(R, 3.25)))

# ---------------------------------------------------------------- check questions
print("\n=== c1: another panel of nine, trimmed mean ===")
C1 = [8.00, 7.75, 8.25, 9.50, 8.00, 8.50, 7.50, 8.25, 5.75]
show("sorted", sorted(C1))
show("sum", d4(sum(C1)))
show("mean", "{!r} -> {}".format(mean(C1), d4(mean(C1))))
show("Sx, sigma x", "{} {}".format(d4(sample_sd(C1)), d4(pop_sd(C1))))
t1, k1 = trimmed(C1, 1)
show("kept", k1)
show("kept sum = 71.5 - 5.75 - 9.5", d4(sum(C1) - 5.75 - 9.5))
show("trimmed mean (answer)", "{!r} -> {}".format(t1, d4(t1)))
show("median", d4(percentile(C1, 50)[0]))
w1v, wl1 = winsorized(C1, 1)
show("winsorized mean", "{!r} -> {}".format(w1v, d4(w1v)))
show("trap: kept sum / 9", d4((sum(C1) - 5.75 - 9.5) / 9))
show("TI-84 minX, maxX", "{} {}".format(d4(min(C1)), d4(max(C1))))

print("\n=== c3: 30th percentile, n = 10 ===")
C3 = [4.2, -2.5, 1.8, 6.1, -0.7, 3.3, 0.9, -5.4, 2.6, 7.4]
SC3 = sorted(C3)
show("sorted", SC3)
p30, l30 = percentile(C3, 30)
show("L_30 = 11 x 0.30", "{!r} -> {:.4f}".format(l30, l30))
show("3rd and 4th", "{} {}".format(SC3[2], SC3[3]))
show("gap 0.9 - (-0.7)", "{:.4f}".format(SC3[3] - SC3[2]))
show("0.3 x gap", "{:.4f}".format(0.3 * (SC3[3] - SC3[2])))
show("P30 (answer)", "{!r} -> {:.4f} -> {:.2f}%".format(p30, p30, p30))
show("trap: location n x y/100 = 3 -> 3rd value", "{:.4f}".format(interp(SC3, 10 * 0.30)))
show("trap: unsorted list at 3.3", "{:.4f}".format(interp(C3, 3.3)))
q1c3, l25c3 = percentile(C3, 25)
show("CFA Q1: L_25 = 2.75", "{} -> {:.4f}".format(l25c3, q1c3))
show("TI-84 Q1, Med, Q3", ["{:.4f}".format(v) for v in ti84_quartiles(C3)])
show("CFA Q3: L_75 = 8.25", "{} -> {:.4f}".format(percentile(C3, 75)[1], percentile(C3, 75)[0]))

print("\n=== c4: 75th percentile of eight returns ===")
C4 = [2.0, -1.5, 6.5, 0.5, 4.0, -3.0, 2.5, 1.0]
SC4 = sorted(C4)
show("sorted", SC4)
p75, l75 = percentile(C4, 75)
show("L_75 = 9 x 0.75", l75)
show("A (right): 2.5 + 0.75 x (4.0 - 2.5)", "{!r} -> {:.3f}%".format(p75, p75))
show("B: location n x y/100 = 8 x 0.75", 8 * 0.75)
show("B: location n x y/100 = 6 -> 6th sorted", "{:.3f}%".format(interp(SC4, 8 * 0.75)))
show("C: location 6.75 in the unsorted list", "{!r} -> {:.3f}%".format(interp(C4, 6.75), interp(C4, 6.75)))
show("  unsorted 6th and 7th", "{} {}".format(C4[5], C4[6]))
