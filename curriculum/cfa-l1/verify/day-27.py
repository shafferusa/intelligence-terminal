#!/usr/bin/env python3
"""Day 27 of CFA Math: every number that appears on day-27.html.

Bayes' formula: updating a prior on new information, with P(Information)
from the total probability rule (CFA Program 2027 Level I, Quantitative
Methods, LM6 Statistical Distributions for Financial Asset Prices and Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime


def show(label, value):
    print("  {:<64} {}".format(label, value))


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """TI-84 display in fixed 4 decimals: no leading zero below 1."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def bayes(prior, p_i_given_e, p_i_given_not_e):
    """Return (top branch, bottom branch, P(I), posterior P(E | I))."""
    top = p_i_given_e * prior
    bottom = p_i_given_not_e * (1 - prior)
    p_i = top + bottom
    return top, bottom, p_i, top / p_i


def pct(x, dp=2):
    return "{:.{}f}%".format(100 * x, dp)


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 26): expected bond payoff by the total probability rule
P_R, PD_R, PD_X, FACE, REC = 0.25, 0.12, 0.04, 100.0, 45.0
e_r = (1 - PD_R) * FACE + PD_R * REC
e_x = (1 - PD_X) * FACE + PD_X * REC
e_all = P_R * e_r + (1 - P_R) * e_x
show("w1 E(payoff | recession) = 0.88 x 100 + 0.12 x 45", "{} -> BA {} / TI {}".format(e_r, d4(e_r), ti4(e_r)))
show("w1   0.88 x 100 ; 0.12 x 45", "{:.2f} ; {:.2f}".format((1 - PD_R) * FACE, PD_R * REC))
show("w1 E(payoff | expansion) = 0.96 x 100 + 0.04 x 45", "{} -> {}".format(e_x, d4(e_x)))
show("w1   0.96 x 100 ; 0.04 x 45", "{:.2f} ; {:.2f}".format((1 - PD_X) * FACE, PD_X * REC))
show("w1   0.25 x 93.40 ; 0.75 x 97.80", "{:.4f} ; {:.4f}".format(P_R * e_r, (1 - P_R) * e_x))
show("w1 E(payoff) (answer)", "{!r} -> {} -> {:.2f}".format(e_all, d4(e_all), e_all))
p_d = P_R * PD_R + (1 - P_R) * PD_X
show("w1 shortcut: P(D) = 0.25 x 0.12 + 0.75 x 0.04", "{:.4f} ({:.4f} + {:.4f})".format(p_d, P_R * PD_R, (1 - P_R) * PD_X))
show("w1 shortcut: 100 - P(D) x (100 - 45)", "{:.4f}".format(FACE - p_d * (FACE - REC)))
show("w1 loss given default = 100 - 45", FACE - REC)

# w2 (Day 20): terminal value of a two-stage DDM
D0, GS, GL, R, N = 1.50, 0.08, 0.03, 0.09, 2
d1 = D0 * (1 + GS)
d2 = d1 * (1 + GS)
d3 = d2 * (1 + GL)
v2 = d3 / (R - GL)
show("w2 D1, D2", "{:.4f}, {:.4f}".format(d1, d2))
show("w2 D3 = D2 x 1.03", "{!r} -> {:.4f}".format(d3, d3))
show("w2 V2 = D3/(r - gL) (answer)", "{!r} -> {:.4f} -> {:.2f}".format(v2, v2, v2))
show("w2 distractor: D2/(r - gL)", "{:.4f} -> {:.2f}".format(d2 / (R - GL), d2 / (R - GL)))
show("w2 distractor: V2 discounted to today V2/1.09^2", "{:.4f} -> {:.2f}".format(v2 / (1 + R) ** 2, v2 / (1 + R) ** 2))
show("w2 1.09^2", "{:.4f}".format((1 + R) ** 2))

# ---------------------------------------------------------------------------
print("\n=== Story: Sally Clark ===")
for label, dt in [("conviction", datetime.date(1999, 11, 9)), ("freed", datetime.date(2003, 1, 29))]:
    show("{} {}".format(label, dt.isoformat()), dt.strftime("%A"))
yrs = (datetime.date(2003, 1, 29) - datetime.date(1999, 11, 9)).days / 365.25
show("years from conviction to release (about three)", "{:.2f}".format(yrs))
ONE = 8543
sq = ONE * ONE
show("8,543 squared", "{:,}".format(sq))
show("  as 'about 1 in N million'", "{:.1f} million".format(sq / 1e6))
show("1/8,543 as a probability", "{:.8f}".format(1 / ONE))
for ratio in (9, 4.5):
    post = 1 / (1 + ratio)
    show("double SIDS {} x as likely: P(guilty | deaths) = 1/(1 + {})".format(ratio, ratio),
         "{!r} -> {}".format(post, pct(post, 2)))
show("  same, as P(innocent | deaths)", "{} to {}".format(pct(9 / 10, 2), pct(4.5 / 5.5, 2)))

# ---------------------------------------------------------------------------
print("\n=== Why it works: counting 1,000 firms (worked-example rates) ===")
FIRMS, PRIOR, PW_D, PW_N = 1000, 0.05, 0.70, 0.10
dflt = FIRMS * PRIOR
ok = FIRMS - dflt
w_d = dflt * PW_D
w_n = ok * PW_N
show("defaulters, non-defaulters", "{:.0f}, {:.0f}".format(dflt, ok))
show("warned defaulters (70% of 50)", "{:.0f}".format(w_d))
show("unwarned defaulters", "{:.0f}".format(dflt - w_d))
show("warned non-defaulters (10% of 950)", "{:.0f}".format(w_n))
show("unwarned non-defaulters", "{:.0f}".format(ok - w_n))
show("all warned", "{:.0f}".format(w_d + w_n))
show("share of warned that default 35/130", "{!r} -> {} -> {}".format(w_d / (w_d + w_n), pct(w_d / (w_d + w_n), 2), pct(w_d / (w_d + w_n), 1)))
show("warned firms that do NOT default 95/130", pct(w_n / (w_d + w_n), 2))

# ---------------------------------------------------------------------------
print("\n=== Worked example: default and a credit-watch warning ===")
top, bot, p_w, post_w = bayes(PRIOR, PW_D, PW_N)
show("joint P(W and D) = 0.70 x 0.05", "{:.4f}".format(top))
show("P(W and not D) = 0.10 x 0.95", "{:.4f}".format(bot))
show("P(W) = 0.035 + 0.095", "{:.4f}".format(p_w))
show("update factor P(W | D)/P(W) = 0.70/0.13", "{:.4f}".format(PW_D / p_w))
show("P(D | W) = 0.035/0.13 (answer)", "{!r} -> {}".format(post_w, pct(post_w)))
show("  check: factor x prior", "{:.6f}".format(PW_D / p_w * PRIOR))
show("P(not D | W) = 1 - P(D | W)", pct(1 - post_w))
p_nw = 1 - p_w
top_n = (1 - PW_D) * PRIOR
bot_n = (1 - PW_N) * (1 - PRIOR)
show("P(no W) = 1 - 0.13", "{:.4f}".format(p_nw))
show("  P(no W and D) = 0.30 x 0.05", "{:.4f}".format(top_n))
show("  P(no W and not D) = 0.90 x 0.95", "{:.4f}".format(bot_n))
show("  check: 0.015 + 0.855", "{:.4f}".format(top_n + bot_n))
post_nw = top_n / p_nw
show("P(D | no W) = 0.015/0.87 (answer)", "{!r} -> {}".format(post_nw, pct(post_nw)))
show("update factor P(no W | D)/P(no W) = 0.30/0.87", "{:.4f}".format((1 - PW_D) / p_nw))
show("check: weighted posteriors give back the prior", "{:.4f}".format(post_w * p_w + post_nw * p_nw))
show("trap: inverted conditional P(W | D)", pct(PW_D))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough: earnings beat and a signal ===")
PB, PS_B, PS_N = 0.40, 0.80, 0.30
top, bot, p_s, post_s = bayes(PB, PS_B, PS_N)
show("P(no beat) = 1 - 0.40", "{:.4f}".format(1 - PB))
show("top branch .8 x .4 (STO 1 / ->A)", "BA {} / TI {}".format(d4(top), ti4(top)))
show("bottom branch .3 x .6 (STO 2 / ->B)", "BA {} / TI {}".format(d4(bot), ti4(bot)))
show("P(signal) = RCL1 + RCL2 (STO 3 / ->C)", "BA {} / TI {}".format(d4(p_s), ti4(p_s)))
show("P(beat | signal) = RCL1 / RCL3 (answer)", "{!r} -> BA {} / TI {}".format(post_s, d4(post_s), ti4(post_s)))
f = PS_B / p_s
show("update factor .8 / P(signal)", "BA {} / TI {}".format(d4(f), ti4(f)))
show("factor x .4", "BA {} / TI {}".format(d4(f * PB), ti4(f * PB)))
show("P(beat | no signal) (not on page as an answer)", pct((1 - PS_B) * PB / (1 - p_s)))
# Traps on the walkthrough's numbers
wrong_bot = (1 - PS_B) * (1 - PB)
show("TRAP P(I | not E) taken as 1 - P(I | E) = 0.2: P(I)", "{:.4f}".format(top + wrong_bot))
show("TRAP posterior with that P(I)", "{!r} -> {}".format(top / (top + wrong_bot), pct(top / (top + wrong_bot))))
show("TRAP bottom branch left out: 0.32/0.32", pct(top / top))
show("TRAP inverted conditional P(signal | beat)", pct(PS_B))

# ---------------------------------------------------------------------------
print("\n=== c1: dividend cut and a warning sign ===")
top, bot, p_i, post = bayes(0.25, 0.70, 0.20)
show("P(no cut) = 0.75", "{:.4f}".format(0.75))
show("top .7 x .25", "BA {} / TI {}".format(d4(top), ti4(top)))
show("bottom .2 x .75", "BA {} / TI {}".format(d4(bot), ti4(bot)))
show("P(signal)", "BA {} / TI {}".format(d4(p_i), ti4(p_i)))
show("P(cut | signal) (answer)", "{!r} -> BA {} / TI {} -> {}".format(post, d4(post), ti4(post), pct(post)))
show("  x 100", "BA {} / TI {}".format(d4(100 * post), ti4(100 * post)))
show("  data-answer (percent)", repr(100 * post))
show("  inverted conditional (the trap)", pct(0.70))

# ---------------------------------------------------------------------------
print("\n=== c3: the other branch (no signal) ===")
PB3, PS_B3, PS_N3 = 0.30, 0.75, 0.20
pn_b = 1 - PS_B3
pn_n = 1 - PS_N3
top = pn_b * PB3
bot = pn_n * (1 - PB3)
p_n = top + bot
post3 = top / p_n
show("P(no signal | beat) = 1 - 0.75", "{:.2f}".format(pn_b))
show("P(no signal | no beat) = 1 - 0.20", "{:.2f}".format(pn_n))
show("top .25 x .3", "BA {} / TI {}".format(d4(top), ti4(top)))
show("bottom .8 x .7", "BA {} / TI {}".format(d4(bot), ti4(bot)))
show("P(no signal)", "BA {} / TI {}".format(d4(p_n), ti4(p_n)))
show("P(beat | no signal) (answer)", "{!r} -> BA {} / TI {} -> {}".format(post3, d4(post3), ti4(post3), pct(post3)))
show("  x 100", "BA {} / TI {}".format(d4(100 * post3), ti4(100 * post3)))
show("  data-answer (percent)", repr(100 * post3))
_, _, p_s3, post_s3 = bayes(PB3, PS_B3, PS_N3)
show("  for contrast: P(signal), P(beat | signal)", "{:.4f}, {}".format(p_s3, pct(post_s3)))
show("  check: P(signal) + P(no signal)", "{:.4f}".format(p_s3 + p_n))
show("  trap: used the signal branch instead", pct(post_s3))

# ---------------------------------------------------------------------------
print("\n=== c4: exam style, default screen ===")
P4, F_D, F_N = 0.02, 0.90, 0.05
top, bot, p_f, post4 = bayes(P4, F_D, F_N)
show("top 0.90 x 0.02", "{:.4f}".format(top))
show("bottom 0.05 x 0.98", "{:.4f}".format(bot))
show("P(flagged)", "{:.4f}".format(p_f))
show("B (right): P(default | flagged)", "{!r} -> {}".format(post4, pct(post4)))
show("A (inverted): P(flagged | default)", pct(F_D))
show("C (prior ignored): 0.90/(0.90 + 0.05)", "{!r} -> {}".format(F_D / (F_D + F_N), pct(F_D / (F_D + F_N))))
show("  of 1,000 firms: defaulters, flagged defaulters", "{:.0f}, {:.0f}".format(1000 * P4, 1000 * top))
show("  of 1,000 firms: survivors", "{:.0f}".format(1000 * (1 - P4)))
show("  of 1,000 firms: flagged non-defaulters, all flagged", "{:.0f}, {:.0f}".format(1000 * bot, 1000 * p_f))
show("  flagged firms that do NOT default", pct(1 - post4))
show("  survivors per defaulter (0.98/0.02)", "{:.0f}".format((1 - P4) / P4))
show("  formula form: (0.90/0.067) x 0.02", "{:.4f} x 0.02 = {}".format(F_D / p_f, pct(F_D / p_f * P4)))
