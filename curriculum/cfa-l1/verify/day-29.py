#!/usr/bin/env python3
"""Day 29 of CFA Math: every number that appears on day-29.html.

Binomial probabilities and counting: Bernoulli trials, the binomial formula
P(X = x) = nCx p^x (1 - p)^(n - x), E(X) = np, Var(X) = np(1 - p), factorials,
combinations and permutations, binomial trees, and the discrete and continuous
uniform distributions (CFA Program 2027 Level I, Quantitative Methods,
LM6 Statistical Distributions for Financial Asset Prices and Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from fractions import Fraction as F


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


def pct(x, dp=2):
    return "{:.{}f}%".format(100 * x, dp)


def C(n, r):
    return math.comb(n, r)


def P(n, r):
    return math.perm(n, r)


def binom_pdf(n, p, x):
    return C(n, x) * p ** x * (1 - p) ** (n - x)


def binom_cdf(n, p, x):
    """P(X <= x), what the TI-84's binomcdf(n, p, x) returns."""
    return sum(binom_pdf(n, p, k) for k in range(0, x + 1))


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 26): expected return by the total probability rule
P_EXP, R_EXP, Q_EXP = 0.70, (14.0, 6.0), (0.6, 0.4)
P_REC, R_REC, Q_REC = 0.30, (-8.0, 2.0), (0.5, 0.5)
assert abs(P_EXP + P_REC - 1) < 1e-12
show("w1 scenario probabilities: expansion ; recession", "{:.2f} ; {:.2f}".format(P_EXP, P_REC))
e_exp = Q_EXP[0] * R_EXP[0] + Q_EXP[1] * R_EXP[1]
e_rec = Q_REC[0] * R_REC[0] + Q_REC[1] * R_REC[1]
e_all = P_EXP * e_exp + P_REC * e_rec
show("w1 .6 x 14 ; .4 x 6", "{:.2f} ; {:.2f}".format(Q_EXP[0] * R_EXP[0], Q_EXP[1] * R_EXP[1]))
show("w1 E(R | expansion) (STO 1 / ->A)", "{!r} -> BA {} / TI {}".format(e_exp, d4(e_exp), ti4(e_exp)))
show("w1 .5 x (-8) ; .5 x 2", "{:.2f} ; {:.2f}".format(Q_REC[0] * R_REC[0], Q_REC[1] * R_REC[1]))
show("w1 E(R | recession) (STO 2 / ->B)", "{!r} -> BA {} / TI {}".format(e_rec, d4(e_rec), ti4(e_rec)))
show("w1 .7 x 10.8 ; .3 x (-3)", "{:.2f} ; {:.2f}".format(P_EXP * e_exp, P_REC * e_rec))
show("w1 E(R) (answer, percent)", "{!r} -> BA {} / TI {} -> {:.2f}%".format(e_all, d4(e_all), ti4(e_all), e_all))
show("w1 trap: plain average of the four returns", "{:.2f}%".format(sum(R_EXP + R_REC) / 4))

# w2 (Day 22): float-adjusted market-cap weight
PX, QX, HX = 40.0, 300.0, 100.0   # price, shares outstanding (m), closely held (m)
PY, QY, HY = 25.0, 400.0, 0.0
fx, fy = QX - HX, QY - HY
capx, capy = PX * fx, PY * fy
wx = capx / (capx + capy)
show("w2 X float shares = 300 - 100 ; Y float = 400 - 0", "{:.0f} ; {:.0f}".format(fx, fy))
show("w2 float caps X = 40 x 200 ; Y = 25 x 400 ; total", "{:,.0f} ; {:,.0f} ; {:,.0f}".format(capx, capy, capx + capy))
show("w2 A (right): X float-adjusted weight", "{!r} -> {}".format(wx, pct(wx)))
fullx, fully = PX * QX, PY * QY
show("w2 full caps X = 40 x 300 ; Y = 25 x 400 ; total", "{:,.0f} ; {:,.0f} ; {:,.0f}".format(fullx, fully, fullx + fully))
show("w2 B (full shares, closely held not removed)", pct(fullx / (fullx + fully)))
show("w2 C (price weight 40/(40 + 25))", "{:.0f} -> {}".format(PX + PY, pct(PX / (PX + PY))))
show("w2 Y float-adjusted weight (for the solution)", pct(capy / (capx + capy)))

# ---------------------------------------------------------------------------
print("\n=== Story: Paul the octopus ===")
GERMANY_MATCHES = 7      # Australia, Serbia, Ghana, England, Argentina, Spain, Uruguay
FINAL = 1
N_PICKS = GERMANY_MATCHES + FINAL
show("picks: 7 Germany matches + the final", N_PICKS)
show("Germany wins he picked (Aus, Gha, Eng, Arg, Uru)", 5)
show("Germany defeats he picked (Serbia, Spain)", 2)
for label, dt in [("final", datetime.date(2010, 7, 11)), ("death", datetime.date(2010, 10, 26))]:
    show("{} {}".format(label, dt.isoformat()), dt.strftime("%A"))
p8 = F(1, 2) ** 8
show("P(8 of 8) = 0.5^8", "{} = {!r} -> {} ({})".format(p8, float(p8), pct(float(p8)), pct(float(p8), 4)))

# ---------------------------------------------------------------------------
print("\n=== Why it works ===")
PF = 0.7
one = PF * PF * (1 - PF)
show("fund: one order, beat beat miss = 0.7 x 0.7 x 0.3", "{:.4f}".format(one))
orders3 = ["".join(s) for s in __import__("itertools").product("BM", repeat=3) if s.count("B") == 2]
show("orders with exactly 2 beats in 3 years", "{} -> {}".format(orders3, len(orders3)))
assert len(orders3) == C(3, 2)
show("P(exactly 2 of 3) = 3 x 0.147", "{:.4f} (check binom {:.4f})".format(3 * one, binom_pdf(3, PF, 2)))
show("ordered choices of Paul's 2 misses: 8 x 7 = 8P2", "{} = {}".format(8 * 7, P(8, 2)))
show("8!/6!", math.factorial(8) // math.factorial(6))
show("2! ; 56/2 = 8C2 = 8C6", "{} ; {} = {} = {}".format(math.factorial(2), 56 // 2, C(8, 2), C(8, 6)))
for p in (0.5, 0.7, 0.6):
    m = 1 * p + 0 * (1 - p)
    v = p * (1 - m) ** 2 + (1 - p) * (0 - m) ** 2
    show("Bernoulli p = {}: mean, variance, p(1 - p)".format(p), "{:.4f}, {:.4f}, {:.4f}".format(m, v, p * (1 - p)))
show("0! (so 8C8 = 8!/(0! 8!) = 1)", "{} ; 8C8 = {}".format(math.factorial(0), C(8, 8)))
show("3C2 via n!/((n-r)! r!) = 6/(1 x 2)", C(3, 2))

# ---------------------------------------------------------------------------
print("\n=== Worked example ===")
n, p, x = 4, 0.6, 3
paths = ["".join(s) for s in __import__("itertools").product("UD", repeat=n) if s.count("U") == x]
show("(a) 4C3 and the paths", "{} {}".format(C(n, x), paths))
show("(a) 0.6^3", "{:.4f}".format(p ** 3))
show("(a) one path 0.6^3 x 0.4", "{:.4f}".format(p ** 3 * (1 - p)))
pa = binom_pdf(n, p, x)
show("(a) P(X = 3) = 4 x 0.0864 (answer)", "{!r} -> {:.4f}".format(pa, pa))
show("(a) E(X) = 4 x 0.6", "{:.2f}".format(n * p))
show("(a) Var(X) = 4 x 0.6 x 0.4", "{:.4f}".format(n * p * (1 - p)))
show("(a) sum of all five ending nodes (check)", "{:.4f}".format(sum(binom_pdf(n, p, k) for k in range(n + 1))))
show("(a) ending-node probabilities 0..4 ups", ["{:.4f}".format(binom_pdf(n, p, k)) for k in range(n + 1)])
show("(b) 8C3 = 8!/(5! 3!)", C(8, 3))
show("(b) 8P3 = 8!/5! = 8 x 7 x 6", "{} = {}".format(P(8, 3), 8 * 7 * 6))
show("(b) 56 x 3! = 56 x 6", 56 * math.factorial(3))
lo, hi, x1, x2 = -4.0, 10.0, 0.0, 5.0
pu = (x2 - x1) / (hi - lo)
show("(c) (5 - 0)/(10 - (-4)) = 5/14 (answer)", "{!r} -> {}".format(pu, pct(pu)))
show("(c) mean (a + b)/2", "{:.2f}%".format((lo + hi) / 2))
show("idea: fair die discrete uniform 1/6", "{:.4f}".format(1 / 6))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough: Paul's 8 picks, p = 0.5 ===")
n, p = 8, 0.5
one_order = p ** 8
show("step 2: .5 y^x 8", "{!r} -> BA {} / TI {}".format(one_order, d4(one_order), ti4(one_order)))
show("step 2: 1/x (1 in ...)", "BA {} / TI {}".format(d4(1 / one_order), ti4(1 / one_order)))
show("step 3: 8 nCr 6", "BA {} / TI {}".format(d4(C(8, 6)), ti4(C(8, 6))))
p6 = C(8, 6) * one_order
show("step 4: 28 x .5^8 = P(exactly 6) (= binompdf(8,.5,6))", "{!r} -> BA {} / TI {} -> {}".format(p6, d4(p6), ti4(p6), pct(p6)))
assert abs(p6 - binom_pdf(8, 0.5, 6)) < 1e-15
show("  as a fraction", F(C(8, 6), 256))
show("step 5: 8 nCr 7 ; 8 nCr 8", "BA {} ; {} / TI {} ; {}".format(d4(C(8, 7)), d4(C(8, 8)), ti4(C(8, 7)), ti4(C(8, 8))))
p7, p8f = binom_pdf(8, 0.5, 7), binom_pdf(8, 0.5, 8)
show("  P(exactly 7) ; P(exactly 8)", "{!r} -> {} ; {!r} -> {}".format(p7, pct(p7), p8f, pct(p8f)))
show("  28 + 8 + 1", 28 + 8 + 1)
p_ge6 = (C(8, 6) + C(8, 7) + C(8, 8)) * one_order
show("step 6: (28 + 8 + 1) x .5^8 = P(at least 6) (answer)", "{!r} -> BA {} / TI {} -> {}".format(p_ge6, d4(p_ge6), ti4(p_ge6), pct(p_ge6)))
show("  x 100", "BA {} / TI {}".format(d4(100 * p_ge6), ti4(100 * p_ge6)))
show("  as a fraction", F(37, 256))
cdf5 = binom_cdf(8, 0.5, 5)
show("step 6 TI: binomcdf(8,.5,5) = P(X <= 5)", "{!r} -> TI {} ({})".format(cdf5, ti4(cdf5), F(219, 256)))
show("step 6 TI: 1 - binomcdf(8,.5,5)", "{!r} -> TI {}".format(1 - cdf5, ti4(1 - cdf5)))
assert abs((1 - cdf5) - p_ge6) < 1e-15
show("check: all 9 outcomes add to 1", "{:.6f}".format(binom_cdf(8, 0.5, 8)))

# ---------------------------------------------------------------------------
print("\n=== Back to the story ===")
show("P(8 of 8) for one guesser", "{} = {}".format(F(1, 256), pct(1 / 256)))
for guessers in (100, 256):
    at_least_one = 1 - (255 / 256) ** guessers
    show("{} guessers: P(at least one 8 for 8) = 1 - (255/256)^{}".format(guessers, guessers),
         "{!r} -> {} -> {}".format(at_least_one, pct(at_least_one, 2), pct(at_least_one, 1)))
    show("{} guessers: expected perfect records n x p = {}/256".format(guessers, guessers), "{:.4f}".format(guessers / 256))
show("(255/256)^100 = P(all 100 fall short)", "{:.4f}".format((255 / 256) ** 100))
show("1 - 0.6761 (rounded first)", "{:.4f}".format(1 - 0.6761))
show("guesser: E(X) = 8 x 0.5", "{:.2f}".format(8 * 0.5))
show("guesser: Var(X) = 8 x 0.5 x 0.5", "{:.2f}".format(8 * 0.5 * 0.5))
show("guesser: SD = sqrt(2)", "{:.4f} -> {:.2f}".format(math.sqrt(2), math.sqrt(2)))
show("P(6 or more) for a guesser", pct(p_ge6))

# ---------------------------------------------------------------------------
print("\n=== Exam traps ===")
show("8P6 = 8!/2!", "{:,}".format(P(8, 6)))
show("6!", math.factorial(6))
show("8P6 / 6! = 8C6", "{:,} / {} = {}".format(P(8, 6), math.factorial(6), P(8, 6) // math.factorial(6)))
show("8! (for reference)", "{:,}".format(math.factorial(8)))
drop = C(4, 3) * 0.6 ** 3
show("TRAP dropped (1 - p)^(n - x): 4 x 0.6^3", "{:.4f} vs right {:.4f}".format(drop, binom_pdf(4, 0.6, 3)))
show("TRAP dropped factor on Paul: 28 x 0.5^6", "{:.4f}".format(28 * 0.5 ** 6))
show("TRAP exactly 6 vs at least 6", "{} vs {}".format(pct(p6), pct(p_ge6)))
cdf6 = binom_cdf(8, 0.5, 6)
show("TRAP 1 - binomcdf(8,.5,6) = P(at least 7)", "{!r} -> {} ({})".format(1 - cdf6, pct(1 - cdf6), F(9, 256)))
show("  check P(7) + P(8)", pct(p7 + p8f))

# ---------------------------------------------------------------------------
print("\n=== c1: 8 right out of 10 coin-flip calls ===")
c1 = binom_pdf(10, 0.5, 8)
show("10 nCr 8", "BA {} / TI {}".format(d4(C(10, 8)), ti4(C(10, 8))))
show("  choose the 2 misses: 10 x 9 / 2", 10 * 9 // 2)
show("0.5^10", "{!r} -> {}".format(0.5 ** 10, F(1, 1024)))
show("45 x .5^10 (= binompdf(10,.5,8))", "{!r} -> BA {} / TI {}".format(c1, d4(c1), ti4(c1)))
show("  x 100 (answer, percent)", "{!r} -> BA {} / TI {} -> {}".format(100 * c1, d4(100 * c1), ti4(100 * c1), pct(c1)))
show("  as a fraction", F(45, 1024))
show("  trap: left out 10C8 (0.5^10 alone)", pct(0.5 ** 10, 4))
show("  for contrast: P(at least 8 of 10) = (45 + 10 + 1)/1024", "{} -> {}".format(F(56, 1024), pct(56 / 1024)))

# ---------------------------------------------------------------------------
print("\n=== c3: at least one default in 10 bonds ===")
q0 = 0.95 ** 10
c3 = 1 - q0
show("P(no default) = 0.95^10 = binompdf(10,.05,0)", "{!r} -> BA {} / TI {}".format(q0, d4(q0), ti4(q0)))
assert abs(q0 - binom_pdf(10, 0.05, 0)) < 1e-15
show("1 - 0.95^10 (answer)", "{!r} -> BA {} / TI {} -> {}".format(c3, d4(c3), ti4(c3), pct(c3)))
show("  x 100 (data-answer, percent)", "{!r} -> BA {} / TI {}".format(100 * c3, d4(100 * c3), ti4(100 * c3)))
show("  trap: 10 x 5%", pct(10 * 0.05))
show("  P(exactly one) = 10 x 0.05 x 0.95^9", "{:.4f} -> {}".format(binom_pdf(10, 0.05, 1), pct(binom_pdf(10, 0.05, 1))))
show("  P(two or more) = 1 - P(0) - P(1)", pct(1 - q0 - binom_pdf(10, 0.05, 1)))
show("  E(defaults) = 10 x 0.05", "{:.2f}".format(10 * 0.05))
show("  10C0 ; 0.05^0", "{} ; {}".format(C(10, 0), 0.05 ** 0))

# ---------------------------------------------------------------------------
print("\n=== c4: exactly 3 rises in 5 periods, p = 0.55 ===")
n4, p4 = 5, 0.55
one4 = p4 ** 3 * (1 - p4) ** 2
show("5C3 (5 nCr 3 display)", "{} -> BA {} / TI {}".format(C(5, 3), d4(C(5, 3)), ti4(C(5, 3))))
show("0.55^3 ; 0.45^2", "{:.6f} ; {:.4f}".format(p4 ** 3, (1 - p4) ** 2))
show("A (5C3 left out): 0.55^3 x 0.45^2", "{!r} -> BA {} -> {}".format(one4, d4(one4), pct(one4)))
show("  one order to 5 decimals ; 10 x that (explanation)", "{:.5f} ; {:.4f}".format(one4, 10 * round(one4, 5)))
b4 = binom_pdf(n4, p4, 3)
show("B (right): 10 x 0.55^3 x 0.45^2", "{!r} -> BA {} / TI {} -> {}".format(b4, d4(b4), ti4(b4), pct(b4)))
p4_4, p4_5 = binom_pdf(n4, p4, 4), binom_pdf(n4, p4, 5)
show("  P(4) = 5 x 0.55^4 x 0.45 ; P(5) = 0.55^5", "{:.4f} ; {:.4f}".format(p4_4, p4_5))
c4c = b4 + p4_4 + p4_5
show("C (at least 3): P(3) + P(4) + P(5)", "{!r} -> {}".format(c4c, pct(c4c)))
cdf2 = binom_cdf(n4, p4, 2)
show("  TI: binomcdf(5,.55,2) ; 1 - that", "{} ; {}".format(ti4(cdf2), ti4(1 - cdf2)))
assert abs((1 - cdf2) - c4c) < 1e-15
show("  E(rises) = 5 x 0.55", "{:.2f}".format(n4 * p4))

# ---------------------------------------------------------------------------
print("\n=== c5 numbers (model answer) ===")
show("0.5^8 = 1/256", pct(1 / 256))
show("100 guessers", pct(1 - (255 / 256) ** 100, 1))
