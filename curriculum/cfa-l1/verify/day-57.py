#!/usr/bin/env python3
"""Day 57 of CFA Math: every number that appears on day-57.html.

Forward prices and the cost of carry: F0(T) = S0 (1 + r)^T with no carry,
F0(T) = [S0 - PV0(I) + PV0(C)] (1 + r)^T with income and costs,
F0(T) = S0 e^((r + c - i) T) with continuous compounding, the value of a long
forward at t, Vt(T) = [Ft(T) - F0(T)] / (1 + r)^(T - t), and the commodity
futures return = price return + roll return + collateral return
(CFA Program 2027 Level I, Derivatives: Pricing and Valuation of Forward
Contracts and for an Underlying with Varying Maturities).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from decimal import Decimal, ROUND_HALF_UP


def show(label, value):
    print("  {:<70} {}".format(label, value))


def calc_round(x, places=4):
    """Round the way the calculators display (decimal arithmetic, half up)."""
    d = Decimal(repr(round(x, 10)))
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def d4(x):
    """BA II Plus display at 4 decimals, with thousands separators."""
    return "{:,.4f}".format(calc_round(x))


def ti4(x):
    """TI-84 display at Float 4: no separators, no leading zero below 1."""
    s = "{:.4f}".format(calc_round(x))
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def both(x):
    return "BA {} / TI {}".format(d4(x), ti4(x))


def fwd(s0, r, t, pv_i=0.0, pv_c=0.0):
    """No-arbitrage forward price, discrete compounding, T in years."""
    return (s0 - pv_i + pv_c) * (1 + r) ** t


def fwd_cont(s0, r, c, i, t):
    """No-arbitrage forward price, continuous compounding."""
    return s0 * math.exp((r + c - i) * t)


def value_long(f_t, f_0, r, t_left):
    """Value at t of a long forward: PV of the change in the forward price."""
    return (f_t - f_0) / (1 + r) ** t_left


# ---------------------------------------------------------------------------
print("=== Release date ===")
START = datetime.date(2026, 11, 16)
rel = START + datetime.timedelta(days=57 - 1)
show("Day 57 due on start_date + 56 days", "{} ({})".format(rel.isoformat(), rel.strftime("%A")))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
# w1 (Day 54): CPR from SMM = 0.5%
SMM = 0.005
cpr = (1 - (1 - SMM) ** 12) * 100
show("w1 0.995^12", "{:.6f} -> {}".format((1 - SMM) ** 12, both((1 - SMM) ** 12)))
show("w1 1 - 0.995^12", "{:.6f}".format(1 - (1 - SMM) ** 12))
show("w1 CPR % (answer)", "{!r} -> {} -> {:.2f}%".format(cpr, both(cpr), cpr))
show("w1 trap: 12 x SMM", "{:.2f}%".format(12 * SMM * 100))
show("w1 trap: 1.005^12 - 1", "{:.4f}%".format(((1 + SMM) ** 12 - 1) * 100))

# w2 (Day 50): slope test of H0: beta = 1
B, SB, N_OBS, TCRIT = 0.85, 0.10, 30, 2.048
t_b1 = (B - 1) / SB
t_b0 = B / SB
se_wrong = SB / math.sqrt(N_OBS)
t_wrong = (B - 1) / se_wrong
show("w2 df = n - 2", N_OBS - 2)
show("w2 t(beta = 1) = (0.85 - 1)/0.10 (A, right)", "{:.4f} -> {:.2f}".format(t_b1, t_b1))
show("w2 |t| vs critical 2.048", "{:.2f} < {} -> fail to reject".format(abs(t_b1), TCRIT))
show("w2 B: t(beta = 0) = 0.85/0.10", "{:.4f} -> {:.2f}".format(t_b0, t_b0))
show("w2 C: s.e. divided by sqrt(30) again", "{:.6f}".format(se_wrong))
show("w2 C: t = -0.15 / (0.10/sqrt 30)", "{:.4f} -> {:.2f}".format(t_wrong, t_wrong))
show("w2 95% interval 0.85 -/+ 2.048 x 0.10", "{:.4f} to {:.4f} -> {:.2f} to {:.2f}".format(
    B - TCRIT * SB, B + TCRIT * SB, B - TCRIT * SB, B + TCRIT * SB))

# ---------------------------------------------------------------------------
print("\n=== Story: 20 April 2020 ===")
MAY, JUNE, SPOT, LOW = -37.63, 20.43, -36.98, -40.32
show("June - May = 20.43 - (-37.63)", "{:.2f}".format(JUNE - MAY))
show("June - spot = 20.43 - (-36.98)", "{:.2f}".format(JUNE - SPOT))
show("May - spot", "{:.2f}".format(MAY - SPOT))
show("session low minus settlement", "{:.2f}".format(LOW - MAY))
show("years since listing (1983 -> 2020)", 2020 - 1983)

# ---------------------------------------------------------------------------
print("\n=== Why it works: walkthrough oil, step by step ===")
S0, PVC, R, T = 20.0, 2.0, 0.01, 0.25
step1 = fwd(S0, R, T)
step2 = fwd(S0, R, T, pv_c=PVC)
show("1.01^0.25", "{:.6f}".format((1 + R) ** T))
show("step 1: 20 x 1.01^0.25 (no carry)", "{:.6f} -> {:.4f}".format(step1, step1))
show("interest on 20 for 3 months", "{:.4f}".format(step1 - S0))
show("step 2: (20 + 2) x 1.01^0.25", "{:.6f} -> {:.4f}".format(step2, step2))
show("cash-and-carry profit if F were 23", "{:.4f}".format(23 - step2))
show("reverse cash-and-carry profit if F were 21", "{:.4f}".format(step2 - 21))
show("check: at T, t = T: V = S_T - F0 (no discounting): (1+r)^0", (1 + R) ** 0)

# ---------------------------------------------------------------------------
print("\n=== Worked example: stock 100, dividend 2 in 3 months, r 4%, 6-month forward ===")
S_W, DIV, R_W, T_DIV, T_W = 100.0, 2.0, 0.04, 0.25, 0.5
pv_div = DIV / (1 + R_W) ** T_DIV
net = S_W - pv_div
f_w = net * (1 + R_W) ** T_W
f_nodiv = S_W * (1 + R_W) ** T_W
fv_div = DIV * (1 + R_W) ** (T_W - T_DIV)
show("1.04^0.25", "{:.6f}".format((1 + R_W) ** T_DIV))
show("PV of dividend 2/1.04^0.25", "{:.6f} -> {:.4f}".format(pv_div, pv_div))
show("S0 - PV(I)", "{:.6f} -> {:.4f}".format(net, net))
show("1.04^0.5", "{:.6f}".format((1 + R_W) ** T_W))
show("F0 = 98.0195 x 1.04^0.5 (answer)", "{!r} -> {:.4f} -> {:.2f}".format(f_w, f_w, f_w))
show("ignoring dividend 100 x 1.04^0.5", "{:.6f} -> {:.4f}".format(f_nodiv, f_nodiv))
show("dividend carried to T: 2 x 1.04^0.25", "{:.6f} -> {:.4f}".format(fv_div, fv_div))
show("check: 101.9804 - 2.0197", "{:.4f}".format(f_nodiv - fv_div))
show("overstatement if dividend ignored", "{:.4f}".format(f_nodiv - f_w))
PR, RR, CR = 5.0, -3.0, 4.0
show("commodity total return 5 - 3 + 4 (%)", "{:.0f}%".format(PR + RR + CR))
show("trap: roll return added as +3", "{:.0f}%".format(PR - RR + CR))
show("trap: collateral return left out", "{:.0f}%".format(PR + RR))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: oil S0 20, PV(C) 2, r 1%, T 0.25; one month later F_t 18 ===")
FT, T_LEFT = 18.0, 2 / 12
f0 = step2
change = FT - f0
disc = (1 + R) ** T_LEFT
v = value_long(FT, f0, R, T_LEFT)
show("step 2: 20 + 2", both(S0 + PVC))
show("step 3: F0 = 22 x 1.01^.25 (STO)", "{!r} -> {}".format(f0, both(f0)))
show("T - t = 2/12", "{:.6f}".format(T_LEFT))
show("step 4: F_t - F0 = 18 - F0", "{!r} -> {}".format(change, both(change)))
show("1.01^(2/12)", "{:.6f}".format(disc))
show("step 5: value = change / 1.01^(2/12) (answer)", "{!r} -> {}".format(v, both(v)))
show("value to the short", "{:.4f}".format(-v))
show("discounting removes", "{:.4f}".format(v - change))
show("trap: discounted over full T = .25", "{:.4f}".format(change / (1 + R) ** T))
show("trap: not discounted", "{:.4f}".format(change))
show("trap: T typed as 3 (months): 22 x 1.01^3", "{:.4f}".format(22 * (1 + R) ** 3))
show("trap: storage subtracted: (20 - 2) x 1.01^.25", "{:.4f}".format(fwd(S0, R, T, pv_i=PVC)))
show("value at inception: F0 - F0", "{:.4f}".format(value_long(f0, f0, R, T)))

# ---------------------------------------------------------------------------
print("\n=== Back to the story ===")
show("carry implied, r ~ 0: June - May", "{:.2f}".format(JUNE - MAY))
show("carry implied, r ~ 0: June - spot", "{:.2f}".format(JUNE - SPOT))

# ---------------------------------------------------------------------------
print("\n=== Check questions ===")
# c1: gold, S0 1900, PV(C) 10, r 5%, T 0.5
S1, C1, R1, T1 = 1900.0, 10.0, 0.05, 0.5
f1 = fwd(S1, R1, T1, pv_c=C1)
show("c1 1900 + 10", both(S1 + C1))
show("c1 1.05^0.5", "{:.6f}".format((1 + R1) ** T1))
show("c1 F0 = 1910 x 1.05^0.5 (answer)", "{!r} -> {} -> {:.2f}".format(f1, both(f1), f1))
show("c1 trap: storage subtracted (1890 x 1.05^.5)", "{:.4f} -> {:.2f}".format(fwd(S1, R1, T1, pv_i=C1), fwd(S1, R1, T1, pv_i=C1)))
show("c1 trap: T as 6", "{:.2f}".format(1910 * (1 + R1) ** 6))
show("c1 trap: storage ignored", "{:.2f}".format(fwd(S1, R1, T1)))

# c2: conceptual; illustrate with walkthrough numbers
show("c2 illustration: spot + storage financed (walkthrough F0)", "{:.4f}".format(f0))

# c3: continuous
S3, R3, C3, I3, T3 = 80.0, 0.03, 0.02, 0.01, 0.5
expo = (R3 + C3 - I3) * T3
f3 = fwd_cont(S3, R3, C3, I3, T3)
show("c3 r + c - i", "{:.4f}".format(R3 + C3 - I3))
show("c3 exponent (r + c - i) x T", "{!r} -> {}".format(expo, both(expo)))
show("c3 e^0.02", "{!r} -> {}".format(math.exp(expo), both(math.exp(expo))))
show("c3 F0 = 80 e^0.02 (answer)", "{!r} -> {} -> {:.2f}".format(f3, both(f3), f3))
show("c3 trap: income added, 80 e^((.03+.02+.01) x .5)", "{:.4f}".format(S3 * math.exp((R3 + C3 + I3) * T3)))
show("c3 trap: discrete 80 x 1.04^0.5", "{:.4f}".format(S3 * 1.04 ** 0.5))
show("c3 trap: T as 6 months, 80 e^(0.04 x 6)", "{:.4f}".format(S3 * math.exp(0.04 * 6)))

# c4: S0 50, PV(div) 1.20, r 4%, T 1
S4, I4, R4, T4 = 50.0, 1.20, 0.04, 1.0
fa = fwd(S4, R4, T4)
fb = fwd(S4, R4, T4, pv_c=I4)
fc = fwd(S4, R4, T4, pv_i=I4)
show("c4 A: dividend ignored 50 x 1.04", "{:.4f} -> {:.2f}".format(fa, fa))
show("c4 B: dividend added 51.20 x 1.04", "{:.4f} -> {:.2f}".format(fb, fb))
show("c4 C: right (50 - 1.20) x 1.04", "{:.4f} -> {:.2f} -> {}".format(fc, fc, both(fc)))
show("c4 50 - 1.20", "{:.2f}".format(S4 - I4))

# c5: reuses the story and walkthrough numbers
show("c5 reuse: June - May", "{:.2f}".format(JUNE - MAY))
show("c5 reuse: walkthrough value", "{:.4f}".format(v))
