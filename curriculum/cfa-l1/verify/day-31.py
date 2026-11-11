#!/usr/bin/env python3
"""Day 31 of CFA Math: every number that appears on day-31.html.

Lognormal prices and Monte Carlo simulation: continuously compounded (log)
returns r = ln(P1/P0) = ln(1 + HPR), their additivity across periods,
P_T = P_0 e^(r_0,T), why such prices are lognormal (never negative, skewed
right, mean above median), Monte Carlo simulation with normal draws made from
uniform random numbers (inverse transform), and historical simulation and
bootstrap resampling (CFA Program 2027 Level I, Quantitative Methods, LM9
Simulation of Financial Asset Prices and Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math
import random
from decimal import Decimal, ROUND_HALF_UP
from statistics import NormalDist

Z = NormalDist()            # the standard normal: mean 0, SD 1


def show(label, value):
    print("  {:<66} {}".format(label, value))


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


def r2(x):
    """Round half up to 2 decimals, as a person would write it."""
    return str(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct(x, dp=2):
    return "{:.{}f}%".format(100 * x, dp)


def phi(z):
    return 0.5 * math.erfc(-z / math.sqrt(2))


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 30): the 5% left-tail cutoff X = mu - 1.645 sigma
mu_w, sd_w = 1.0, 6.0
w1_ba = mu_w - 1.645 * sd_w
w1_ti = NormalDist(mu_w, sd_w).inv_cdf(0.05)
show("w1 1.645 x 6", "{:.2f}".format(1.645 * sd_w))
show("w1 BA: 1 - 1.645 x 6 (data-answer)", "{!r} -> BA {} -> {}%".format(w1_ba, d4(w1_ba), r2(w1_ba)))
show("w1 TI: invNorm(.05,1,6)", "{!r} -> TI {} -> {}%".format(w1_ti, ti4(w1_ti), r2(w1_ti)))
show("w1 gap BA vs TI", "{:.4f}".format(w1_ti - w1_ba))
show("w1 trap 1.96: 1 - 1.96 x 6", "{}%".format(r2(mu_w - 1.96 * sd_w)))
show("w1 trap 2.33: 1 - 2.33 x 6", "{}%".format(r2(mu_w - 2.33 * sd_w)))
show("w1 trap mean left out: -1.645 x 6", "{}%".format(r2(-1.645 * sd_w)))
show("w1 check: P(R < -8.87) with exact z", pct(NormalDist(mu_w, sd_w).cdf(w1_ba)))

# w2 (Day 24): coefficient of variation CV = s / mean
f1m, f1s = 8.0, 12.0
f2m, f2s = 5.0, 9.0
show("w2 CV fund 1 = 12/8", "{:.2f}".format(f1s / f1m))
show("w2 CV fund 2 = 9/5", "{:.2f}".format(f2s / f2m))
show("w2 trap inverted mean/SD fund 1 ; fund 2", "{:.2f} ; {:.2f}".format(f1m / f1s, f2m / f2s))

# ---------------------------------------------------------------------------
print("\n=== Story: Ulam's solitaire, 1946 ===")
f52 = math.factorial(52)
show("52!", "{} ({} digits)".format(f52, len(str(f52))))
show("52! in scientific notation", "{:.4e} -> about {:.0e}".format(f52, f52))
show("years from 1946 to the 1949 paper", 1949 - 1946)

# ---------------------------------------------------------------------------
print("\n=== The idea / Why it works ===")
show("+2% day: growth factor", "{:.2f}".format(1.02))
show("100 x e^0.20 ; gain", "{:.2f} ; {:.2f}".format(100 * math.exp(0.20), 100 * math.exp(0.20) - 100))
show("100 x e^-0.20 ; loss", "{:.2f} ; {:.2f}".format(100 * math.exp(-0.20), 100 - 100 * math.exp(-0.20)))
show("100 x e^-1 (a log return of -100%)", "{:.2f}".format(100 * math.exp(-1)))
show("100 x e^-3 (a log return of -300%)", "{:.2f}".format(100 * math.exp(-3)))
MU_Y, SD_Y, S0_Y = 0.05, 0.20, 100.0
med = S0_Y * math.exp(MU_Y)
mean = S0_Y * math.exp(MU_Y + SD_Y ** 2 / 2)
show("sigma^2 / 2 = 0.04 / 2", "{:.2f}".format(SD_Y ** 2 / 2))
show("lognormal median 100 e^0.05", "{:.2f}".format(med))
show("lognormal mean 100 e^(0.05 + 0.02) = 100 e^0.07", "{:.2f}".format(mean))
for u in (0.90, 0.50, 0.05, 0.10, 0.95):
    show("inverse transform: invNorm({:.2f})".format(u), "{:.6f} -> {}".format(Z.inv_cdf(u), ti4(Z.inv_cdf(u))))
show("check P(Z < 1.2816)", "{:.4f}".format(phi(1.2816)))
show("check P(Z < -1.6449)", "{:.4f}".format(phi(-1.6449)))

# ---------------------------------------------------------------------------
print("\n=== Worked example: 50.00 -> 55.00 -> 49.50 ===")
p = [50.00, 55.00, 49.50]
s1 = p[1] / p[0] - 1
s2 = p[2] / p[1] - 1
show("simple returns", "{} ; {}".format(pct(s1), pct(s2)))
show("simple returns added", pct(s1 + s2))
show("true 2-day simple return 49.50/50 - 1", pct(p[2] / p[0] - 1))
l1 = math.log(p[1] / p[0])
l2 = math.log(p[2] / p[1])
show("ratios 55/50 ; 49.50/55", "{:.4f} ; {:.4f}".format(p[1] / p[0], p[2] / p[1]))
show("r1 = ln 1.1", "{!r} -> {:.4f}".format(l1, l1))
show("r2 = ln 0.9", "{!r} -> {:.4f}".format(l2, l2))
show("r1 + r2", "{!r} -> {:.4f} ({:.6f})".format(l1 + l2, l1 + l2, l1 + l2))
show("ln(49.50/50) = ln 0.99", "{!r} -> {:.4f}".format(math.log(0.99), math.log(0.99)))
show("rounded parts added: 0.0953 + (-0.1054)", "{:.4f}".format(0.0953 - 0.1054))
show("50 x e^(-0.010050)", "{:.4f} -> {:.2f}".format(50 * math.exp(l1 + l2), 50 * math.exp(l1 + l2)))
show("as a percent", "{:.2f}%".format(100 * (l1 + l2)))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough ===")
P = [80.00, 82.40, 79.93, 81.50]
lr = [math.log(P[i + 1] / P[i]) for i in range(3)]
for i in range(3):
    ratio = P[i + 1] / P[i]
    show("ratio {:.2f}/{:.2f}".format(P[i + 1], P[i]), "{!r} -> BA {} / TI {}".format(ratio, d4(ratio), ti4(ratio)))
    show("  ln -> STO {}".format(i + 1), "{!r} -> BA {} / TI {}".format(lr[i], d4(lr[i]), ti4(lr[i])))
tot = sum(lr)
direct = math.log(P[3] / P[0])
show("RCL 1 + RCL 2 + RCL 3", "{!r} -> BA {} / TI {}".format(tot, d4(tot), ti4(tot)))
show("check ln(81.50/80.00)", "{!r} -> BA {} / TI {}".format(direct, d4(direct), ti4(direct)))
show("  ratio 81.5/80", "BA {} / TI {}".format(d4(P[3] / P[0]), ti4(P[3] / P[0])))
show("  difference (should be ~0)", "{:.2e}".format(tot - direct))
show("  rounded parts added 0.0296 - 0.0304 + 0.0195", "{:.4f}".format(0.0296 - 0.0304 + 0.0195))
show("  as a percent", "{:.4f}% -> {:.2f}%".format(100 * tot, 100 * tot))
simple = [P[i + 1] / P[i] - 1 for i in range(3)]
show("  simple returns", " ; ".join(pct(x, 4) for x in simple))
show("  simple returns added (wrong)", pct(sum(simple), 4))
show("  true 3-day HPR 81.50/80 - 1", pct(P[3] / P[0] - 1, 4))
show("  e^0.018577 - 1 (back from the log return)", pct(math.exp(tot) - 1, 4))

S0, MU, SIG, ZD = 100.0, 0.0005, 0.02, 1.2816
expo = MU + SIG * ZD
show("sigma x z = 0.02 x 1.2816", "{:.6f}".format(SIG * ZD))
show("exponent 0.0005 + 0.02 x 1.2816", "{!r} -> BA {} / TI {}".format(expo, d4(expo), ti4(expo)))
g = math.exp(expo)
show("e^exponent", "{!r} -> BA {} / TI {}".format(g, d4(g), ti4(g)))
P1 = S0 * g
show("P1 = 100 x e^exponent (walkthrough answer)", "{!r} -> BA {} / TI {} -> {}".format(P1, d4(P1), ti4(P1), r2(P1)))
zx = Z.inv_cdf(0.90)
P1x = S0 * math.exp(MU + SIG * zx)
show("TI invNorm(.9)", "{!r} -> TI {}".format(zx, ti4(zx)))
show("P1 with the full invNorm(.9)", "{!r} -> TI {}".format(P1x, ti4(P1x)))
show("  as a % move", "{:.4f}%".format(100 * (P1 / S0 - 1)))
# a 'typical' range for one simulated day, for the page's description
lo = S0 * math.exp(MU + SIG * Z.inv_cdf(0.025))
hi = S0 * math.exp(MU + SIG * Z.inv_cdf(0.975))
show("95% of simulated days land between", "{:.2f} and {:.2f}".format(lo, hi))
med1 = S0 * math.exp(MU + SIG * 0)
show("median day (z = 0)", "{:.2f}".format(med1))
# trap: rates typed in percent into e^x
bad_expo = 100 * expo
show("trap: exponent typed in percent", "{:.4f}".format(bad_expo))
show("trap: 100 x e^2.6132", "{:.2f}".format(S0 * math.exp(bad_expo)))
show("trap: 100 x e^(2.6132) with display value", "{:.2f}".format(S0 * math.exp(2.6132)))

# ---------------------------------------------------------------------------
print("\n=== Back to the story: P(year-end price < 80), mu 5%, sigma 20% ===")
cut = math.log(0.8)
zc = (cut - MU_Y) / SD_Y
pex = phi(zc)
show("ln 0.8", "{!r} -> {:.4f}".format(cut, cut))
show("z = (ln 0.8 - 0.05) / 0.20", "{!r} -> {:.4f}".format(zc, zc))
show("exact P = N(z)", "{!r} -> {}".format(pex, pct(pex)))
show("  NormalDist check", pct(NormalDist(MU_Y, SD_Y).cdf(cut)))
# seeded simulation (as in the plan): random.Random(1946), gauss draws,
# 100 runs, then 10,000 more runs from the same generator
rng = random.Random(1946)
minp = float("inf")
maxp = 0.0


def run(n):
    global minp, maxp
    hits = 0
    for _ in range(n):
        r = rng.gauss(MU_Y, SD_Y)
        price = S0_Y * math.exp(r)
        minp = min(minp, price)
        maxp = max(maxp, price)
        if price < 80:
            hits += 1
    return hits


h100 = run(100)
h10k = run(10000)
show("100 simulated years: hits ; share", "{} ; {}".format(h100, pct(h100 / 100)))
show("10,000 simulated years: hits ; share", "{} ; {}".format(h10k, pct(h10k / 10000)))
show("lowest ; highest simulated price (10,100 runs)", "{:.2f} ; {:.2f}".format(minp, maxp))
show("runs in total", 100 + 10000)
se100 = math.sqrt(pex * (1 - pex) / 100)
se10k = math.sqrt(pex * (1 - pex) / 10000)
show("standard error with 100 runs", "{:.2f} points".format(100 * se100))
show("standard error with 10,000 runs", "{:.2f} points".format(100 * se10k))
show("ratio of errors ; ratio of runs", "{:.0f} ; {:.0f}".format(se100 / se10k, 10000 / 100))
show("100-run miss ; 10,000-run miss", "{:.2f} ; {:.2f} points".format(100 * (h100 / 100 - pex), 100 * (h10k / 10000 - pex)))

# ---------------------------------------------------------------------------
print("\n=== c1: S0 60, mu 0.04%, sigma 1.5%, z = -1.6449 ===")
S1, M1, G1, Z1 = 60.0, 0.0004, 0.015, -1.6449
e1 = M1 + G1 * Z1
show("sigma x z = 0.015 x -1.6449", "{:.7f}".format(G1 * Z1))
show("exponent", "{!r} -> BA {} / TI {}".format(e1, d4(e1), ti4(e1)))
show("e^exponent", "BA {} / TI {}".format(d4(math.exp(e1)), ti4(math.exp(e1))))
c1 = S1 * math.exp(e1)
show("P1 = 60 e^exponent (data-answer)", "{!r} -> BA {} / TI {} -> {}".format(c1, d4(c1), ti4(c1), r2(c1)))
c1m = S1 * math.exp(M1 + G1 * -1.645)
show("  with z = -1.645", "{:.4f}".format(c1m))
c1x = S1 * math.exp(M1 + G1 * Z.inv_cdf(0.05))
show("  with invNorm(.05) exact", "{!r} -> {:.4f} (invNorm(.05) = {})".format(c1x, c1x, ti4(Z.inv_cdf(0.05))))
show("  trap: percent in e^x: 60 e^(-2.42735)", "{:.2f}".format(S1 * math.exp(100 * e1)))
show("  trap: simple return: 60 x (1 + exponent)", "{:.4f}".format(S1 * (1 + e1)))
show("  trap: forgot the minus on z", "{:.2f}".format(S1 * math.exp(M1 + G1 * 1.6449)))

# ---------------------------------------------------------------------------
print("\n=== c3: S0 40, five daily log returns ===")
R3 = [0.012, -0.008, 0.015, -0.020, 0.006]
s3 = sum(R3)
show("sum of log returns", "{!r} -> BA {} / TI {}".format(s3, d4(s3), ti4(s3)))
show("e^0.005", "BA {} / TI {}".format(d4(math.exp(s3)), ti4(math.exp(s3))))
c3 = 40 * math.exp(s3)
show("P = 40 e^0.005 (data-answer)", "{!r} -> BA {} / TI {} -> {}".format(c3, d4(c3), ti4(c3), r2(c3)))
show("week's HPR e^0.005 - 1", "{} ({!r})".format(pct(math.exp(s3) - 1, 4), math.exp(s3) - 1))
prod = 40
for r in R3:
    prod *= 1 + r
show("trap: treated as simple returns, chain-linked", "{:.4f} -> {}".format(prod, r2(prod)))
show("trap: 40 x (1 + 0.005)", "{:.4f}".format(40 * 1.005))
show("trap: percent in e^x: 40 e^0.5", "{:.2f}".format(40 * math.exp(0.5)))

# ---------------------------------------------------------------------------
print("\n=== c4: 50 -> 60 -> 45, 2-period continuously compounded return ===")
a, b, c = 50.0, 60.0, 45.0
ra = b / a - 1
rb = c / b - 1
show("simple returns", "{} ; {}".format(pct(ra), pct(rb)))
show("A: simple returns added", pct(ra + rb))
show("B: ln(45/50) (right)", "{!r} -> {}".format(math.log(c / a), pct(math.log(c / a))))
show("  ln(60/50) + ln(45/60)", "{:.4f} + {:.4f} = {:.4f}".format(math.log(b / a), math.log(c / b), math.log(b / a) + math.log(c / b)))
show("C: HPR 45/50 - 1", pct(c / a - 1))
show("  back: 50 x e^(-0.1054) ; with full ln 0.9", "{:.4f} ; {:.4f}".format(50 * math.exp(-0.1054), 50 * math.exp(math.log(0.9))))
show("  ln(1 + R) < R: ln 0.9 vs -0.10", "{:.4f} < {:.4f}".format(math.log(0.9), -0.10))

# ---------------------------------------------------------------------------
print("\n=== Extra checks for figures quoted on the page ===")
show("story z with ln 0.8 to 5 dp: (-0.22314 - 0.05)/0.20", "{:.4f}".format((-0.22314 - 0.05) / 0.20))
show("  (with ln 0.8 to 4 dp it would read)", "{:.4f}".format((-0.2231 - 0.05) / 0.20))
show("walkthrough: e^0.018576 - 1", pct(math.exp(0.018576) - 1, 4))
show("walkthrough: 81.50/80 - 1", pct(81.5 / 80 - 1, 4))
show("c1: 60 x 0.9760", "{:.4f}".format(60 * 0.9760))
show("c1: 0.0004 - 0.0246735", "{:.7f}".format(0.0004 - 0.0246735))
show("c3: 40 x 1.0050", "{:.4f}".format(40 * 1.0050))
H1 = "In 1946 Stanisław Ulam couldn’t work out his odds at solitaire. Why not deal it 100 times and count?"
show("h1 length (characters, aim under ~110)", len(H1))

# ---------------------------------------------------------------------------
print("\n=== c2 numbers (why log returns add) ===")
show("ln(1.1 x 0.9) vs ln 1.1 + ln 0.9", "{:.6f} vs {:.6f}".format(math.log(1.1 * 0.9), math.log(1.1) + math.log(0.9)))
show("big moves: ln(2) + ln(0.5) = ln(1)", "{:.6f} + {:.6f} = {:.6f}".format(math.log(2), math.log(0.5), math.log(2) + math.log(0.5)))
