#!/usr/bin/env python3
"""Day 26 of CFA Math: every number that appears on day-26.html.

Expected value, conditional expected value and the total probability rule
(plus joint probability, the addition rule and independence)
(CFA Program 2027 Level I, Quantitative Methods,
LM6 Statistical Distributions for Financial Asset Prices and Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math
from fractions import Fraction as F


def show(label, value):
    print("  {:<64} {}".format(label, value))


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def ev(outcomes, probs):
    assert abs(sum(probs) - 1) < 1e-12, "probabilities must sum to 1"
    return sum(p * x for p, x in zip(probs, outcomes))


def var_prob(outcomes, probs):
    m = ev(outcomes, probs)
    return sum(p * (x - m) ** 2 for p, x in zip(probs, outcomes))


def weighted_stats(xs, freqs):
    """What 1-V (BA II Plus) / 1-Var Stats L1,L2 (TI-84) show with frequencies."""
    n = sum(freqs)
    sx = sum(f * x for f, x in zip(freqs, xs))
    sx2 = sum(f * x * x for f, x in zip(freqs, xs))
    m = sx / n
    ss = sum(f * (x - m) ** 2 for f, x in zip(freqs, xs))
    return {"n": n, "mean": m, "Sx": math.sqrt(ss / (n - 1)), "sigma": math.sqrt(ss / n),
            "sum_x": sx, "sum_x2": sx2}


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
COV, SX, SY = -27.0, 15.0, 9.0
r_w1 = COV / (SX * SY)
show("w1 (Day 25) r = s_XY/(s_X s_Y) = -27/(15 x 9)", "{!r} -> {:.4f} -> {:.2f}".format(r_w1, r_w1, r_w1))
show("w1 15 x 9", SX * SY)

W2 = [0.15, -0.10, 0.06]
g = 1.0
for rr in W2:
    g *= 1 + rr
show("w2 (Day 11) growth factors 1.15 x 0.90 x 1.06", "{!r} -> {:.4f}".format(g, g))
show("w2 three-year HPR (correct)", "{:.4f}% -> {:.2f}%".format(100 * (g - 1), 100 * (g - 1)))
show("w2 distractor: added returns", "{:.2f}%".format(100 * sum(W2)))
ann = g ** (1 / 3) - 1
show("w2 distractor: annualized (per year), not the 3-year total", "{:.4f}% -> {:.2f}%".format(100 * ann, 100 * ann))

# ---------------------------------------------------------------------------
print("\n=== Story: Pascal to Fermat, 29 July 1654 ===")
STAKE = 32
POT = 2 * STAKE
show("pot = 2 x 32 pistoles", POT)
pac_21 = F(2, 3) * POT
show("Pacioli 2-1: (2/3) x 64", "{} = {:.4f} -> about {:.2f}".format(pac_21, float(pac_21), float(pac_21)))
show("Pacioli 2-1: other player (1/3) x 64", "{:.4f}".format(float(F(1, 3) * POT)))
show("Pacioli 1-0: 1/(1+0) x 64 (leader takes everything)", F(1, 1) * POT)


def share(a, b, target=3):
    """Leader's fair share (expected payoff) when the score is a-b, each round 50/50."""
    if a == target:
        return F(POT)
    if b == target:
        return F(0)
    return F(1, 2) * share(a + 1, b) + F(1, 2) * share(a, b + 1)


for a, b in [(2, 1), (2, 2), (2, 0), (1, 0), (1, 1), (0, 0)]:
    s = share(a, b)
    show("fair share of player A at {}-{}".format(a, b), "{} (other {}); P(A wins) = {} = {:.4f}".format(
        s, POT - s, s / POT, float(s / POT)))
show("2-1: 1/2 x 64 + 1/2 x 32", F(1, 2) * 64 + F(1, 2) * 32)
show("2-1:   1/2 x 64", F(1, 2) * 64)
show("2-1:   1/2 x 32", F(1, 2) * 32)
show("2-0: 1/2 x 64 + 1/2 x 48", F(1, 2) * 64 + F(1, 2) * 48)
show("1-0: 1/2 x 56 + 1/2 x 32", F(1, 2) * 56 + F(1, 2) * 32)
show("2-1 via win probability: 3/4 x 64", F(3, 4) * 64)
show("Pascal 48 vs Pacioli 42.67: difference", "{:.2f}".format(48 - float(pac_21)))

# ---------------------------------------------------------------------------
print("\n=== The idea: everyday pictures ===")
show("bet: 0.3 x $10", "{:.2f}".format(0.3 * 10))
show("commute: 0.3 x 40 + 0.7 x 25", "{} = {} + {}".format(0.3 * 40 + 0.7 * 25, 0.3 * 40, 0.7 * 25))

# ---------------------------------------------------------------------------
print("\n=== Why it works: the hat of 100 tickets (today's EPS numbers) ===")
EPS = [2.60, 2.45, 2.20, 2.00]
P_GOOD, P_POOR = 0.60, 0.40
COND = [0.75, 0.25, 0.25, 0.75]          # P(EPS | its scenario)
SCEN = [P_GOOD, P_GOOD, P_POOR, P_POOR]
JOINT = [c * s for c, s in zip(COND, SCEN)]
FREQ = [round(100 * j) for j in JOINT]
show("joint probabilities (multiply along the path)", ", ".join("{:.2f}".format(j) for j in JOINT))
show("  0.75 x 0.60, 0.25 x 0.60, 0.25 x 0.40, 0.75 x 0.40", "check sum = {:.4f}".format(sum(JOINT)))
show("frequencies (x 100)", FREQ)
for f, x in zip(FREQ, EPS):
    show("  {} x {:.2f}".format(f, x), "{:.2f}".format(f * x))
tot = sum(f * x for f, x in zip(FREQ, EPS))
show("sum of the 100 tickets", "{:.2f}".format(tot))
show("mean of the tickets = sum / 100", "{:.4f}".format(tot / 100))
E_good = 0.75 * 2.60 + 0.25 * 2.45
E_poor = 0.25 * 2.20 + 0.75 * 2.00
show("E(EPS | good) = 0.75 x 2.60 + 0.25 x 2.45", "{!r} -> {:.4f}".format(E_good, E_good))
show("   0.75 x 2.60, 0.25 x 2.45", "{:.4f}, {:.4f}".format(0.75 * 2.60, 0.25 * 2.45))
show("E(EPS | poor) = 0.25 x 2.20 + 0.75 x 2.00", "{!r} -> {:.4f}".format(E_poor, E_poor))
show("   0.25 x 2.20, 0.75 x 2.00", "{:.4f}, {:.4f}".format(0.25 * 2.20, 0.75 * 2.00))
E_tot = P_GOOD * E_good + P_POOR * E_poor
show("E(EPS) = 0.60 x E(good) + 0.40 x E(poor) (answer)", "{!r} -> {:.4f}".format(E_tot, E_tot))
show("   0.60 x 2.5625, 0.40 x 2.05", "{:.4f}, {:.4f}".format(P_GOOD * E_good, P_POOR * E_poor))
show("E(EPS) directly: sum of joint x EPS", "{:.4f}".format(ev(EPS, JOINT)))
VAR = var_prob(EPS, JOINT)
show("variance = sum P (X - E)^2", "{!r} -> {:.4f}".format(VAR, VAR))
for j, x in zip(JOINT, EPS):
    show("  {:.2f} x ({:.2f} - 2.3575)^2".format(j, x), "dev {:+.4f}, sq {:.6f}, weighted {:.6f}".format(
        x - E_tot, (x - E_tot) ** 2, j * (x - E_tot) ** 2))
SD = math.sqrt(VAR)
show("standard deviation = sqrt(variance)", "{!r} -> {:.4f}".format(SD, SD))

# ---------------------------------------------------------------------------
print("\n=== Worked example: bond payoff across recession / expansion ===")
P_R, P_E = 0.30, 0.70
PD_R, PD_E = 0.10, 0.02
FACE, REC = 100, 40
p_RD = PD_R * P_R
show("P(R and D) = 0.10 x 0.30", "{:.4f} -> {:.1f}%".format(p_RD, 100 * p_RD))
p_ED = PD_E * P_E
show("P(E and D) = 0.02 x 0.70", "{:.4f}".format(p_ED))
p_D = p_RD + p_ED
show("P(D) = 0.030 + 0.014", "{:.4f} -> {:.1f}%".format(p_D, 100 * p_D))
p_RorD = P_R + p_D - p_RD
show("P(R or D) = 0.30 + 0.044 - 0.03", "{:.4f} -> {:.1f}%".format(p_RorD, 100 * p_RorD))
show("  (without subtracting the overlap: 0.30 + 0.044)", "{:.4f}".format(P_R + p_D))
E_R = (1 - PD_R) * FACE + PD_R * REC
E_E = (1 - PD_E) * FACE + PD_E * REC
show("E(payoff | R) = 0.90 x 100 + 0.10 x 40", "{:.2f} = {:.2f} + {:.2f}".format(E_R, 0.9 * 100, 0.1 * 40))
show("E(payoff | E) = 0.98 x 100 + 0.02 x 40", "{:.2f} = {:.2f} + {:.2f}".format(E_E, 0.98 * 100, 0.02 * 40))
E_bond = P_R * E_R + P_E * E_E
show("E(payoff) = 0.30 x 94.0 + 0.70 x 98.8", "{:.2f} = {:.2f} + {:.2f}".format(E_bond, P_R * E_R, P_E * E_E))
E_bond_check = (1 - p_D) * FACE + p_D * REC
show("check: 0.956 x 100 + 0.044 x 40", "{:.2f} = {:.2f} + {:.2f}".format(E_bond_check, (1 - p_D) * FACE, p_D * REC))
show("trap: P(R) x P(D) as if independent", "{:.4f} -> {:.2f}%".format(P_R * p_D, 100 * P_R * p_D))
show("P(D | R) vs P(D): dependent?", "{:.3f} vs {:.3f}".format(PD_R, p_D))
show("P(R | D) = P(R and D)/P(D) (not on page; context)", "{:.4f}".format(p_RD / p_D))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough: bank EPS ===")
st = weighted_stats(EPS, FREQ)
show("step 1 display E(EPS | good)", d4(E_good))
show("step 2 display E(EPS | poor)", d4(E_poor))
show("step 3 display E(EPS)", d4(E_tot))
show("1-V n", "{} (BA {})".format(st["n"], d4(st["n"])))
show("1-V x-bar", d4(st["mean"]))
show("1-V Sx (the trap: divides by 99)", "{!r} -> {}".format(st["Sx"], d4(st["Sx"])))
show("1-V sigma_x (answer: probability-weighted SD)", "{!r} -> {}".format(st["sigma"], d4(st["sigma"])))
show("1-V sum x", d4(st["sum_x"]))
show("1-V sum x^2", d4(st["sum_x2"]))
show("variance = sigma_x^2", d4(st["sigma"] ** 2))
assert abs(st["mean"] - E_tot) < 1e-12 and abs(st["sigma"] - SD) < 1e-12

# ---------------------------------------------------------------------------
print("\n=== c1: stock return distribution ===")
C1X = [25, 10, -15]
C1P = [0.20, 0.50, 0.30]
C1F = [20, 50, 30]
e1 = ev(C1X, C1P)
show("E(R) = 0.2 x 25 + 0.5 x 10 + 0.3 x (-15)", "{!r} -> {:.2f}% ({:.2f} + {:.2f} + {:.2f})".format(
    e1, e1, 0.2 * 25, 0.5 * 10, 0.3 * -15))
for p, x in zip(C1P, C1X):
    show("  dev, sq, weighted for {} (p {})".format(x, p), "{:+.2f}, {:.4f}, {:.4f}".format(
        x - e1, (x - e1) ** 2, p * (x - e1) ** 2))
v1 = var_prob(C1X, C1P)
show("variance (%^2)", "{:.4f}".format(v1))
s1 = math.sqrt(v1)
show("sigma (answer)", "{!r} -> {:.4f} -> {:.2f}%".format(s1, s1, s1))
st1 = weighted_stats(C1X, C1F)
show("1-V: n, x-bar, Sx, sigma_x", "{}, {}, {}, {}".format(st1["n"], d4(st1["mean"]), d4(st1["Sx"]), d4(st1["sigma"])))
show("trap Sx to two decimals", "{:.2f}".format(st1["Sx"]))

# ---------------------------------------------------------------------------
print("\n=== c2: why sigma_x, not Sx ===")
show("walkthrough sigma_x vs Sx", "{} vs {}".format(d4(st["sigma"]), d4(st["Sx"])))
stP = weighted_stats([64, 32], [1, 1])
show("Pascal's 1/2-1/2 entered as frequencies 1, 1: x-bar", d4(stP["mean"]))
show("  sigma_x (right)", d4(stP["sigma"]))
show("  Sx (wrong formula)", d4(stP["Sx"]))
show("  true SD = sqrt(1/2 x 16^2 + 1/2 x 16^2)", d4(math.sqrt(0.5 * 16 ** 2 + 0.5 * 16 ** 2)))

# ---------------------------------------------------------------------------
print("\n=== c3: solve for E(EPS | poor) ===")
E_all, E_g, Pg = F("2.30"), F("2.50"), F("0.60")
E_p = (E_all - Pg * E_g) / (1 - Pg)
show("0.60 x 2.50", float(Pg * E_g))
show("2.30 - 1.50", float(E_all - Pg * E_g))
show("E(EPS | poor) = 0.80 / 0.40 (answer)", "{} -> {:.4f}".format(E_p, float(E_p)))
show("slip: divide by 0.60 instead of 0.40", "{:.4f}".format(float((E_all - Pg * E_g) / Pg)))
show("slip: (2.30 - 2.50 x 0.40)/0.60", "{:.4f}".format(float((E_all - (1 - Pg) * E_g) / Pg)))

# ---------------------------------------------------------------------------
print("\n=== c4: addition rule with a dependent joint probability ===")
PA, PB, PA_B = 0.40, 0.30, 0.50
PAB = PA_B * PB
show("P(AB) = P(A | B) x P(B) = 0.50 x 0.30", "{:.2f}".format(PAB))
show("A (correct): 0.40 + 0.30 - 0.15", "{:.2f}".format(PA + PB - PAB))
show("B (overlap not subtracted): 0.40 + 0.30", "{:.2f}".format(PA + PB))
show("C (independence assumed): P(AB) = 0.40 x 0.30", "{:.2f}".format(PA * PB))
show("C: 0.40 + 0.30 - 0.12", "{:.2f}".format(PA + PB - PA * PB))

# ---------------------------------------------------------------------------
print("\n=== Back to the story / c5 ===")
show("2-1 leader: 1/2 x 64 + 1/2 x 32", float(F(1, 2) * 64 + F(1, 2) * 32))
show("2-1 other: 64 - 48", 64 - 48)
show("1-0 other: 64 - 44", 64 - 44)
show("2-0 other: 64 - 56", 64 - 56)

# ---------------------------------------------------------------------------
print("\n=== Grading check (mirrors cfa.js numberOk: |typed - answer| <= tol) ===")


def graded(answer, tol, typed, pct=False):
    cands = [typed] + ([typed * 100] if pct else [])
    return any(abs(c - answer) <= tol + 1e-12 for c in cands)


CHECKS = [
    ("w1", r_w1, 0.01, False, [-0.20], [0.20]),
    ("c1", s1, 0.01, True, [14.57, 0.1457], [14.64, 5.50, 212.25]),
    ("c3", float(E_p), 0.01, False, [2.00], [1.33, 2.17]),
]
for name, ans, tol, pct, good, bad in CHECKS:
    ok = all(graded(ans, tol, t, pct) for t in good)
    rej = not any(graded(ans, tol, t, pct) for t in bad)
    show("{} answer {!r}, tol {}: accepts {}, rejects {}".format(name, ans, tol, good, bad),
         "OK" if ok and rej else "PROBLEM")
    assert ok and rej
