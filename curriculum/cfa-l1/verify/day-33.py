#!/usr/bin/env python3
"""Day 33 of CFA Math: every number that appears on day-33.html.

The minimum-variance portfolio and the efficient frontier: the two-asset
weight that minimizes variance, w1* = (s2^2 - Cov)/(s1^2 + s2^2 - 2 Cov),
derived as the bottom of the U-shaped curve sigma_p^2(w); the zero-risk mix
w1 = s2/(s1 + s2) when rho = -1; why the benefit shrinks as rho rises; the
minimum-variance frontier, the global minimum-variance portfolio and the
efficient frontier (CFA Program 2027 Level I, Quantitative Methods, LM8 The
Return and Risk of a Financial Portfolio).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from decimal import Decimal, ROUND_HALF_UP


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
    return "BA {} / TI {}".format(d4(x), ti4(x))


def r2(x):
    """Round half up to 2 decimals, as a person would write it."""
    return str(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def port(w1, s1, s2, rho):
    """Two-asset portfolio variance (same units as s1^2) and SD."""
    w2 = 1 - w1
    var = w1 ** 2 * s1 ** 2 + w2 ** 2 * s2 ** 2 + 2 * w1 * w2 * rho * s1 * s2
    return var, math.sqrt(max(var, 0.0))


def wmin(s1, s2, rho):
    """Minimum-variance weight on asset 1 (no limits on the weight)."""
    cov = rho * s1 * s2
    return (s2 ** 2 - cov) / (s1 ** 2 + s2 ** 2 - 2 * cov)


def wmin_long(s1, s2, rho):
    """Minimum-variance weight on asset 1 when weights must stay in [0, 1]."""
    return min(max(wmin(s1, s2, rho), 0.0), 1.0)


# ---------------------------------------------------------------------------
print("=== Release date ===")
start = datetime.date(2026, 11, 16)
due = start + datetime.timedelta(days=33 - 1)
show("course start (plan start_date)", start.isoformat() + " " + start.strftime("%A"))
show("Day 33 due = start + 32 days", due.isoformat() + " " + due.strftime("%A"))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
ww, sa, sb, rr = 0.3, 25.0, 10.0, 0.3
wt1 = ww ** 2 * sa ** 2
wt2 = (1 - ww) ** 2 * sb ** 2
wt3 = 2 * ww * (1 - ww) * rr * sa * sb
wv = wt1 + wt2 + wt3
show("w1: 0.3^2 x 25^2 (STO 1 / ->A)", both(wt1))
show("w1: 0.7^2 x 10^2 (STO 2 / ->B)", both(wt2))
show("w1: Cov = 0.3 x 25 x 10", "{:.2f}".format(rr * sa * sb))
show("w1: 2 x 0.3 x 0.7 x 0.3 x 25 x 10 (STO 3 / ->C)", both(wt3))
show("w1: variance", both(wv))
show("w1: sigma_p (data-answer)", "{!r} -> {} -> {}%".format(math.sqrt(wv), both(math.sqrt(wv)), r2(math.sqrt(wv))))
show("w1 trap: weighted-average SD 0.3 x 25 + 0.7 x 10", "{:.2f}%".format(ww * sa + (1 - ww) * sb))
show("w1 trap: forgot the 2", "{:.4f} -> {:.2f}%".format(math.sqrt(wt1 + wt2 + wt3 / 2), math.sqrt(wt1 + wt2 + wt3 / 2)))

raw = [-2, 18, 4, -6, 5, 1, 3]
srt = sorted(raw)
n = len(raw)
L = (n + 1) * 75 / 100
show("w2 list as given ; sorted", "{} ; {}".format(raw, srt))
show("w2 L_75 = (7 + 1) x 0.75", "{:.2f}".format(L))
show("w2 B (right): 6th sorted value", "{:.2f}%".format(srt[int(L) - 1]))
Lw = n * 75 / 100
lo = srt[int(Lw) - 1]
hi = srt[int(Lw)]
show("w2 A: n x y/100 = 5.25 -> 5th + 0.25 x (6th - 5th)", "{:.2f} -> {} + 0.25 x ({} - {}) = {:.2f}%".format(
    Lw, lo, hi, lo, lo + (Lw - int(Lw)) * (hi - lo)))
show("w2 C: 6th value in the unsorted list", "{:.2f}%".format(raw[int(L) - 1]))

# ---------------------------------------------------------------------------
print("\n=== Story ===")
d17 = datetime.date(1998, 8, 17)
d21 = datetime.date(1998, 8, 21)
d23s = datetime.date(1998, 9, 23)
show("17 Aug 1998 weekday ; 21 Aug 1998 weekday", d17.strftime("%A") + " ; " + d21.strftime("%A"))
show("days from default to the $553m day", (d21 - d17).days)
show("days from default to the rescue (23 Sep)", (d23s - d17).days)
show("553 / 35 (worst day vs the models' bound)", "{:.1f}".format(553 / 35))
show("capital implied by 553 = 15% (not printed)", "{:.2f} bn".format(0.553 / 0.15))

# ---------------------------------------------------------------------------
print("\n=== The 10% / 12% pair: the idea, the walkthrough, back to the story ===")
s1, s2 = 10.0, 12.0
print("  -- sigma_p along the weights, rho = 0.1 and 0.7 --")
for w in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
    show("  w1 = {:.1f}: sigma_p at rho 0.1 ; at 0.7".format(w), "{:.2f}% ; {:.2f}%".format(
        port(w, s1, s2, 0.1)[1], port(w, s1, s2, 0.7)[1]))
print("  -- the lowest sigma_p as rho changes (weights in [0, 1]) --")
for rho in (-1.0, -0.5, 0.0, 0.1, 0.3, 0.5, 0.7, s1 / s2, 0.9, 1.0):
    w = wmin_long(s1, s2, rho)
    show("  rho = {:+.4f}: w1* (formula) ; w1 used ; min sigma_p".format(rho), "{:.4f} ; {:.4f} ; {:.4f} -> {:.2f}%".format(
        wmin(s1, s2, rho), w, port(w, s1, s2, rho)[1], port(w, s1, s2, rho)[1]))
show("rho above which the formula gives w1 > 1: s1/s2 = 10/12", "{:.4f}".format(s1 / s2))
show("rho = 0.9 formula weight (needs a short sale)", "{:.4f}".format(wmin(s1, s2, 0.9)))
show("rho = +1 formula weight s2/(s2 - s1)", "{:.4f}".format(s2 / (s2 - s1)))
show("rho = -1 weights: 12/22 ; 10/22", "{:.4f} ; {:.4f}".format(s2 / (s1 + s2), s1 / (s1 + s2)))

print("  -- why it works, with the walkthrough's numbers (rho = 0.1) --")
cov = 0.1 * s1 * s2
a = s1 ** 2 + s2 ** 2 - 2 * cov
b = s2 ** 2 - cov
show("Cov = 0.1 x 10 x 12", "{:.4f}".format(cov))
show("a = 100 + 144 - 2 x 12", "{:.4f}".format(a))
show("b = 144 - 12 ; 2b", "{:.4f} ; {:.4f}".format(b, 2 * b))
show("curve: sigma_p^2 = a w^2 - 2b w + 144", "{:.0f} w^2 - {:.0f} w + {:.0f}".format(a, 2 * b, s2 ** 2))
show("second point at height 144: w = 2b/a", "{:.4f}".format(2 * b / a))
show("check: curve at w = 1.2", "{:.4f}".format(a * 1.2 ** 2 - 2 * b * 1.2 + s2 ** 2))
show("bottom: w = b/a", "{:.4f}".format(b / a))
show("check: expanded vs Day 32 form at w = 0.37", "{:.6f} vs {:.6f}".format(
    a * 0.37 ** 2 - 2 * b * 0.37 + s2 ** 2, port(0.37, s1, s2, 0.1)[0]))

print("  -- walkthrough: rho = 0.1 --")
cov1 = 0.1 * s1 * s2
w1 = (s2 ** 2 - cov1) / (s1 ** 2 + s2 ** 2 - 2 * cov1)
w2 = 1 - w1
v1 = w1 ** 2 * s1 ** 2 + w2 ** 2 * s2 ** 2 + 2 * w1 * w2 * cov1
show("Cov = .1 x 10 x 12 (STO 1 / ->A)", both(cov1))
show("numerator 144 - 12 ; denominator 100 + 144 - 24", "{:.0f} ; {:.0f}".format(s2 ** 2 - cov1, s1 ** 2 + s2 ** 2 - 2 * cov1))
show("w1* (STO 2 / ->B)", both(w1))
show("w2 = 1 - w1* (STO 3 / ->C)", both(w2))
show("terms: w1^2 x 100 ; w2^2 x 144 ; 2 w1 w2 Cov", "{:.4f} ; {:.4f} ; {:.4f}".format(
    w1 ** 2 * 100, w2 ** 2 * 144, 2 * w1 * w2 * cov1))
show("variance (%^2)", both(v1))
show("sigma_p (walkthrough answer)", "{!r} -> {} -> {}%".format(math.sqrt(v1), both(math.sqrt(v1)), r2(math.sqrt(v1))))
show("gain vs the safer strategy alone (10%)", "{:.4f} -> {:.2f} points".format(10 - math.sqrt(v1), 10 - math.sqrt(v1)))

print("  -- walkthrough: rho = 0.7 --")
cov7 = 0.7 * s1 * s2
w17 = (s2 ** 2 - cov7) / (s1 ** 2 + s2 ** 2 - 2 * cov7)
w27 = 1 - w17
v7 = w17 ** 2 * s1 ** 2 + w27 ** 2 * s2 ** 2 + 2 * w17 * w27 * cov7
show("Cov = .7 x 10 x 12 (STO 1 / ->A)", both(cov7))
show("numerator 144 - 84 ; denominator 244 - 168", "{:.0f} ; {:.0f}".format(s2 ** 2 - cov7, s1 ** 2 + s2 ** 2 - 2 * cov7))
show("w1* (STO 2 / ->B)", "{!r} -> {}".format(w17, both(w17)))
show("w2 = 1 - w1* (STO 3 / ->C)", both(w27))
show("variance (%^2)", both(v7))
show("sigma_p", "{!r} -> {} -> {}%".format(math.sqrt(v7), both(math.sqrt(v7)), r2(math.sqrt(v7))))
show("gain vs the safer strategy alone (10%)", "{:.4f} -> {:.2f} points".format(10 - math.sqrt(v7), 10 - math.sqrt(v7)))
show("check: closed form (s1^2 s2^2 - Cov^2)/(denominator) at 0.1 ; 0.7", "{:.4f} ; {:.4f}".format(
    (s1 ** 2 * s2 ** 2 - cov1 ** 2) / (s1 ** 2 + s2 ** 2 - 2 * cov1),
    (s1 ** 2 * s2 ** 2 - cov7 ** 2) / (s1 ** 2 + s2 ** 2 - 2 * cov7)))
show("rho 0.3 (the models' ceiling): w1* ; sigma_p", "{:.4f} ; {:.4f} -> {:.2f}%".format(
    wmin(s1, s2, 0.3), port(wmin(s1, s2, 0.3), s1, s2, 0.3)[1], port(wmin(s1, s2, 0.3), s1, s2, 0.3)[1]))

print("  -- back to the story: 100 equal bets, sigma 10% each --")
nb, sg = 100, 10.0
res = {}
for rho in (0.1, 0.3, 0.7):
    s = sg * math.sqrt(1 / nb + (1 - 1 / nb) * rho)
    res[rho] = s
    show("  rho = {:.1f}: sigma sqrt(1/n + (1 - 1/n) rho)".format(rho), "{:.4f} -> {:.2f}%".format(s, s))
show("ratio sigma(0.7)/sigma(0.1)", "{:.4f}".format(res[0.7] / res[0.1]))
for rho in (0.1, 0.3, 0.7):
    show("  floor as n -> infinity: sigma sqrt(rho), rho = {:.1f}".format(rho), "{:.4f} -> {:.2f}%".format(
        sg * math.sqrt(rho), sg * math.sqrt(rho)))
show("check the n-bet formula by brute force (n = 100, rho = 0.3)", "{:.6f}".format(math.sqrt(
    nb * (1 / nb) ** 2 * sg ** 2 + nb * (nb - 1) * (1 / nb) ** 2 * 0.3 * sg ** 2)))
show("own terms n x (1/n)^2 ; cross terms n(n - 1) ; pairs n(n - 1)/2", "{:.2f} ; {:,} ; {:,}".format(
    nb * (1 / nb) ** 2, nb * (nb - 1), nb * (nb - 1) // 2))
show("cross-term share n(n - 1) x (1/n)^2 = 1 - 1/n", "{:.2f}".format(nb * (nb - 1) * (1 / nb) ** 2))
for rho in (0.1, 0.3, 0.7):
    show("  0.01 + 0.99 x {:.1f} ; its root ; x 10%".format(rho), "{:.3f} ; {:.4f} ; {:.2f}%".format(
        0.01 + 0.99 * rho, math.sqrt(0.01 + 0.99 * rho), 10 * math.sqrt(0.01 + 0.99 * rho)))

# ---------------------------------------------------------------------------
print("\n=== Worked example: sigma 20% and 30% ===")
ws1, ws2 = 0.20, 0.30
wa = ws2 ** 2 / (ws1 ** 2 + ws2 ** 2)
show("(a) rho = 0: 0.09 / (0.04 + 0.09) = 0.09 / 0.13", "{:.6f} -> {:.4f}".format(wa, wa))
show("    w2 = 1 - w1*", "{:.6f} -> {:.4f}".format(1 - wa, 1 - wa))
show("    w1^2 ; w2^2", "{:.6f} ; {:.6f}".format(wa ** 2, (1 - wa) ** 2))
show("    w1^2 x 0.04 ; w2^2 x 0.09", "{:.6f} ; {:.6f}".format(wa ** 2 * 0.04, (1 - wa) ** 2 * 0.09))
va = port(wa, ws1, ws2, 0.0)[0]
show("    sigma_p^2", "{:.6f}".format(va))
show("    pieces to 5 dp ; their sum ; root of that sum", "{:.5f} + {:.5f} ; {:.5f} ; {:.6f}".format(
    wa ** 2 * 0.04, (1 - wa) ** 2 * 0.09, round(wa ** 2 * 0.04, 5) + round((1 - wa) ** 2 * 0.09, 5),
    math.sqrt(round(wa ** 2 * 0.04, 5) + round((1 - wa) ** 2 * 0.09, 5))))
show("    same with the rounded weights 0.6923 and 0.3077", "{:.5f} + {:.5f} = {:.5f}".format(
    0.6923 ** 2 * 0.04, 0.3077 ** 2 * 0.09, 0.6923 ** 2 * 0.04 + 0.3077 ** 2 * 0.09))
show("    sigma_p", "{:.6f} -> {:.2f}%".format(math.sqrt(va), 100 * math.sqrt(va)))
show("    check: s1^2 s2^2 / (s1^2 + s2^2) = 0.0036 / 0.13", "{:.6f}".format(ws1 ** 2 * ws2 ** 2 / (ws1 ** 2 + ws2 ** 2)))
wb = ws2 / (ws1 + ws2)
show("(b) rho = -1: 0.30 / 0.50", "{:.4f}".format(wb))
show("    0.60 x 20 ; 0.40 x 30 ; |difference|", "{:.1f} ; {:.1f} ; {:.1f}".format(wb * 20, (1 - wb) * 30, abs(wb * 20 - (1 - wb) * 30)))
show("    general formula at rho = -1 gives", "{:.4f}".format(wmin(ws1, ws2, -1.0)))
show("    sigma_p at that weight", "{:.6f}".format(port(wb, ws1, ws2, -1.0)[1]))

# ---------------------------------------------------------------------------
print("\n=== Exam traps ===")
wrong = (s2 ** 2 - 0.1) / (s1 ** 2 + s2 ** 2 - 2 * 0.1)
show("rho 0.1 typed where Cov (12) belongs: (144 - 0.1)/(244 - 0.2)", "{:.4f}".format(wrong))
show("  sigma_p at that weight", "{:.4f}".format(port(wrong, s1, s2, 0.1)[1]))
wrong_dec = (s2 ** 2 - 0.0012) / (s1 ** 2 + s2 ** 2 - 2 * 0.0012)
show("Cov in decimals 0.0012 typed into a percent formula", "{:.4f}".format(wrong_dec))
show("rho 0.1 -> 0.7 (correlations jump): min sigma_p", "{:.2f}% -> {:.2f}%".format(math.sqrt(v1), math.sqrt(v7)))

# ---------------------------------------------------------------------------
print("\n=== c1: sigma 15% and 25%, rho = 0.2 ===")
c1s1, c1s2, c1r = 15.0, 25.0, 0.2
c1cov = c1r * c1s1 * c1s2
c1w = (c1s2 ** 2 - c1cov) / (c1s1 ** 2 + c1s2 ** 2 - 2 * c1cov)
c1v = c1w ** 2 * c1s1 ** 2 + (1 - c1w) ** 2 * c1s2 ** 2 + 2 * c1w * (1 - c1w) * c1cov
show("Cov = .2 x 15 x 25 (STO 1 / ->A)", both(c1cov))
show("numerator 625 - 75 ; denominator 225 + 625 - 150", "{:.0f} ; {:.0f}".format(c1s2 ** 2 - c1cov, c1s1 ** 2 + c1s2 ** 2 - 2 * c1cov))
show("w1* (STO 2 / ->B)", "{!r} -> {}".format(c1w, both(c1w)))
show("w2 (STO 3 / ->C)", both(1 - c1w))
show("terms", "{:.4f} ; {:.4f} ; {:.4f}".format(c1w ** 2 * 225, (1 - c1w) ** 2 * 625, 2 * c1w * (1 - c1w) * c1cov))
show("variance", both(c1v))
show("sigma_p (data-answer)", "{!r} -> {} -> {}%".format(math.sqrt(c1v), both(math.sqrt(c1v)), r2(math.sqrt(c1v))))
for wr in (0.7857, 0.786, 0.79):
    show("  tolerance check: sigma_p with w rounded to {}".format(wr), "{:.4f}".format(port(wr, c1s1, c1s2, c1r)[1]))
show("  trap: rho typed for Cov: weight ; sigma_p", "{:.4f} ; {:.4f}".format(
    (625 - 0.2) / (850 - 0.4), port((625 - 0.2) / (850 - 0.4), c1s1, c1s2, c1r)[1]))
show("  trap: Cov ignored (rho = 0 weights) 625/850 ; sigma_p", "{:.4f} ; {:.4f}".format(
    625 / 850, port(625 / 850, c1s1, c1s2, c1r)[1]))

# ---------------------------------------------------------------------------
print("\n=== c2: lowest sigma_p as rho falls (10% / 12% pair, weights in [0, 1]) ===")
for rho in (1.0, 0.5, 0.0, -0.5, -1.0):
    w = wmin_long(s1, s2, rho)
    show("  rho = {:+.1f}: w1 ; lowest sigma_p".format(rho), "{:.4f} ; {:.2f}%".format(w, port(w, s1, s2, rho)[1]))

# ---------------------------------------------------------------------------
print("\n=== c3: sigma 12% (E 6%) and 18% (E 10%), rho = -1 ===")
c3w = 18 / (12 + 18)
c3E = c3w * 6 + (1 - c3w) * 10
show("w1 = 18 / 30", both(c3w))
show("E = 0.6 x 6 + 0.4 x 10 (data-answer)", "{!r} -> {} -> {}%".format(c3E, both(c3E), r2(c3E)))
show("check sigma_p = |0.6 x 12 - 0.4 x 18|", "{:.1f} - {:.1f} = {:.4f}".format(c3w * 12, (1 - c3w) * 18, abs(c3w * 12 - (1 - c3w) * 18)))
show("general formula: Cov = -216 ; (324 + 216)/(144 + 324 + 432)", "{:.0f} ; {:.4f}".format(-12 * 18, (324 + 216) / (144 + 324 + 432)))
wtrap = 12 / 30
show("trap: weights swapped (12/30 on asset 1): E ; sigma_p", "{:.2f}% ; {:.2f}%".format(
    wtrap * 6 + (1 - wtrap) * 10, port(wtrap, 12, 18, -1.0)[1]))
w0 = 18 ** 2 / (12 ** 2 + 18 ** 2)
show("trap: rho = 0 formula 324/468: weight ; E", "{:.4f} ; {:.2f}%".format(w0, w0 * 6 + (1 - w0) * 10))

# ---------------------------------------------------------------------------
print("\n=== c4: sigma 20% and 15%, rho = 0.25 ===")
c4cov = 0.25 * 0.20 * 0.15
c4A = (0.15 ** 2 - c4cov) / (0.20 ** 2 + 0.15 ** 2 - 2 * c4cov)
show("Cov = 0.25 x 0.20 x 0.15", "{:.4f}".format(c4cov))
show("numerator 0.0225 - 0.0075 ; denominator 0.04 + 0.0225 - 0.015", "{:.4f} ; {:.4f}".format(
    0.15 ** 2 - c4cov, 0.20 ** 2 + 0.15 ** 2 - 2 * c4cov))
show("A (right)", "{:.4f}".format(c4A))
show("A in percent units: (225 - 75)/(400 + 225 - 150)", "{:.4f}".format((225 - 75) / (400 + 225 - 150)))
show("B: rho = -1 shortcut 15/(20 + 15)", "{:.4f}".format(15 / 35))
show("C: covariance ignored 0.0225/0.0625", "{:.4f}".format(0.0225 / 0.0625))
show("min sigma_p at A", "{:.4f}".format(port(c4A, 20, 15, 0.25)[1]))

# ---------------------------------------------------------------------------
print("\n=== Why it works: algebra checks ===")
for (x1, x2, rho) in ((10.0, 12.0, 0.1), (15.0, 25.0, 0.2), (20.0, 15.0, 0.25), (12.0, 18.0, -1.0)):
    cv = rho * x1 * x2
    aa = x1 ** 2 + x2 ** 2 - 2 * cv
    top1, top2 = x2 ** 2 - cv, x1 ** 2 - cv
    grid = [i / 10000 for i in range(-5000, 15001)]
    best = min(grid, key=lambda w: port(w, x1, x2, rho)[0])
    show("s1 {:.0f}, s2 {:.0f}, rho {:+.2f}: b/a ; grid search ; tops add to a".format(x1, x2, rho),
         "{:.4f} ; {:.4f} ; {}".format(top1 / aa, best, abs(top1 + top2 - aa) < 1e-9))
show("rho = -1, s 12 and 18: w1 s1 ; w2 s2 ; s1 s2/(s1 + s2)", "{:.4f} ; {:.4f} ; {:.4f}".format(
    0.6 * 12, 0.4 * 18, 12 * 18 / 30))
