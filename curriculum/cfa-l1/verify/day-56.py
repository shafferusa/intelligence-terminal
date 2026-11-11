#!/usr/bin/env python3
"""Day 56 of CFA Math: every number that appears on day-56.html.

Week 8 review (Days 50-55): testing a regression slope and beta by regression,
the correlation t-test, prediction intervals and log-lin forecasts, CAPM, the
SML and alpha, the APT, risk-adjusted performance (Sharpe, Treynor, M2,
Jensen), effective duration and convexity, credit risk (expected loss, LGD,
spread price impact). Plus a describe-only note on Quantitative Methods LM11
(no numbers). Warm-up from Day 55 (implied default probability) and Day 29
(binomial probability).
CFA Program 2027 Level I: Quantitative Methods LM10 Applications of Simple
Linear Regression in Finance; Portfolio Risk and Return: Part II; Curve-Based
and Empirical Fixed-Income Risk Measures; Credit Risk.
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


def two_var(xs, ys):
    """What LIN (BA II Plus) / LinReg(a+bx) and LinRegTTest (TI-84) show."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    r = sxy / math.sqrt(sxx * syy)
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    see = math.sqrt(sse / (n - 2))
    sb = see / math.sqrt(sxx)
    t0 = b / sb
    t_corr = r * math.sqrt(n - 2) / math.sqrt(1 - r * r)
    return {"n": n, "mx": mx, "my": my, "sxx": sxx, "syy": syy, "sxy": sxy,
            "Sx": math.sqrt(sxx / (n - 1)), "Sy": math.sqrt(syy / (n - 1)),
            "sigx": math.sqrt(sxx / n), "sigy": math.sqrt(syy / n),
            "a": a, "b": b, "r": r, "r2": r * r, "sse": sse, "see": see, "sb": sb,
            "t0": t0, "t_corr": t_corr}


def t3_two_tailed_p(t):
    """Two-tailed p-value of Student's t with 3 df (closed form)."""
    x = abs(t)
    cdf = 0.5 + (1 / math.pi) * (x / (math.sqrt(3) * (1 + x * x / 3)) + math.atan(x / math.sqrt(3)))
    return 2 * (1 - cdf)


def t_cdf(t, df, steps=200000):
    """Student t CDF by Simpson integration of the density (check only)."""
    c = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))
    f = lambda u: c * (1 + u * u / df) ** (-(df + 1) / 2)
    lo, hi = 0.0, abs(t)
    h = (hi - lo) / steps
    s = f(lo) + f(hi)
    for i in range(1, steps):
        s += (4 if i % 2 else 2) * f(lo + i * h)
    half = s * h / 3
    return 0.5 + half if t >= 0 else 0.5 - half


def t_crit(df, upper=0.975):
    lo, hi = 0.0, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if t_cdf(mid, df, 4000) < upper:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
print("=== Release date ===")
START = datetime.date(2026, 11, 16)
rel = START + datetime.timedelta(days=56 - 1)
show("Day 56 due on start_date + 55 days", "{} ({})".format(rel.isoformat(), rel.strftime("%A")))

# ---------------------------------------------------------------------------
print("\n=== w1 (Day 55): implied default probability from a spread ===")
SPR_W, RR_W = 1.80, 0.35
lgd_w = 1 - RR_W
pod_w = SPR_W / lgd_w
show("LGD share = 1 - 0.35", "{:.2f}".format(lgd_w))
show("POD = 1.80% / 0.65 (answer, %)", "{!r} -> {} -> {:.2f}%".format(pod_w, both(pod_w), pod_w))
show("check: POD x LGD = 2.7692% x 0.65", "{:.4f}%".format(pod_w * lgd_w))
show("trap: divided by the recovery rate 1.80 / 0.35", "{:.4f} -> {:.2f}%".format(SPR_W / RR_W, SPR_W / RR_W))
show("trap: spread read as the POD", "{:.2f}%".format(SPR_W))
show("100 bonds: defaults expected, loss each, total, % of 10,000",
     "{:.4f}, {:.0f}, {:.2f}, {:.2f}%".format(pod_w, 65, pod_w * 65, pod_w * 65 / 10000 * 100))

# ---------------------------------------------------------------------------
print("\n=== w2 (Day 29): P(exactly 4 beats in 5 years), p = 0.6 ===")
N_B, P_B, X_B = 5, 0.6, 4
ncr = math.comb(N_B, X_B)
one_path = P_B ** X_B * (1 - P_B) ** (N_B - X_B)
p_exact = ncr * one_path
p_five = P_B ** 5
p_atleast = p_exact + p_five
show("5C4", "{} -> {}".format(ncr, both(ncr)))
show("0.6^4", both(P_B ** 4))
show("one path 0.6^4 x 0.4 (A: 5C4 left out)", "{!r} -> {} -> {:.2f}%".format(one_path, both(one_path), one_path * 100))
show("P(exactly 4) = 5 x 0.05184 (C, right)", "{!r} -> {} -> {:.2f}%".format(p_exact, both(p_exact), p_exact * 100))
show("P(5 of 5) = 0.6^5", "{!r} -> {:.2f}%".format(p_five, p_five * 100))
show("P(at least 4) (B)", "{!r} -> {} -> {:.2f}%".format(p_atleast, both(p_atleast), p_atleast * 100))
show("the 5 paths with one miss", "MBBBB BMBBB BBMBB BBBMB BBBBM")

# ---------------------------------------------------------------------------
print("\n=== Worked example: beta 1.15, stock 13%, market 10%, Rf 3% ===")
BETA_E, RP_E, RM_E, RF_E = 1.15, 13.0, 10.0, 3.0
mrp_e = RM_E - RF_E
req_e = RF_E + BETA_E * mrp_e
alpha_e = RP_E - req_e
trey_e = (RP_E - RF_E) / BETA_E
trey_m = (RM_E - RF_E) / 1.0
show("market risk premium 10 - 3", "{:.2f}".format(mrp_e))
show("beta x premium 1.15 x 7", "{:.2f}".format(BETA_E * mrp_e))
show("CAPM required return 3 + 8.05", "{:.2f}%".format(req_e))
show("Jensen's alpha 13 - 11.05", "{:+.2f}%".format(alpha_e))
show("excess return 13 - 3", "{:.2f}".format(RP_E - RF_E))
show("Treynor stock (13 - 3)/1.15", "{!r} -> {:.4f}".format(trey_e, trey_e))
show("Treynor market (10 - 3)/1", "{:.2f}".format(trey_m))
show("Treynor gap", "{:.4f}".format(trey_e - trey_m))
show("alpha = beta x Treynor gap = 1.15 x 1.6957", "{:.4f}".format(BETA_E * (trey_e - trey_m)))
show("alpha = (Rp - Rf) - beta(Rm - Rf) = 10 - 8.05", "{:.2f}".format((RP_E - RF_E) - BETA_E * mrp_e))
show("trap: beta x market return 3 + 1.15 x 10", "{:.2f}%".format(RF_E + BETA_E * RM_E))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: beta by LIN, CAPM, slope tests (n = 5) ===")
XW = [2.0, -1.5, 3.2, 0.4, -2.1]
YW = [2.6, -2.4, 3.9, 0.1, -3.0]
RF_W, ERP_W = 3.0, 5.0
w = two_var(XW, YW)
for k in ("n", "mx", "my", "Sx", "sigx", "Sy", "sigy", "a", "b", "r", "r2"):
    show("LIN " + k, "{!r} -> {}".format(w[k], both(w[k])))
show("Y05 check", both(YW[-1]))
req_w = RF_W + w["b"] * ERP_W
show("CAPM 3 + b x 5 (walkthrough answer)", "{!r} -> {}".format(req_w, both(req_w)))
show("CAPM with b retyped as 1.3396", both(RF_W + 1.3396 * ERP_W))
show("trap: Y as X (stock as X): slope", "{:.4f}".format(w["sxy"] / w["syy"]))
show("SSE, SEE (TI s=)", "{:.6f}, {} ".format(w["sse"], both(w["see"])))
show("s_b = SEE / sqrt(Sxx)", "{!r} -> {}".format(w["sb"], both(w["sb"])))
show("t (beta = 0) = b / s_b", "{!r} -> {}".format(w["t0"], both(w["t0"])))
show("t (correlation) = r sqrt(3)/sqrt(1 - r^2)", "{!r} -> {}".format(w["t_corr"], both(w["t_corr"])))
show("sqrt(3)", both(math.sqrt(3)))
show("1 - r^2", "{!r} -> {}".format(1 - w["r2"], both(1 - w["r2"])))
show("sqrt(1 - r^2)", both(math.sqrt(1 - w["r2"])))
sb_from_t = w["b"] / w["t_corr"]
t1 = (w["b"] - 1) / sb_from_t
show("s_b = b / t", "{!r} -> {}".format(sb_from_t, both(sb_from_t)))
show("b - 1", both(w["b"] - 1))
show("t (beta = 1) = (b - 1)/(b/t)", "{!r} -> {}".format(t1, both(t1)))
show("t (beta = 1) with retyped 4-decimal b and s_b", "{:.4f}".format((1.3396 - 1) / round(sb_from_t, 4)))
p0 = t3_two_tailed_p(w["t_corr"])
p1 = t3_two_tailed_p(t1)
show("p-value (beta = 0), 3 df, two-tailed", "{!r} -> {:.4E}".format(p0, p0))
show("p-value (beta = 1), 3 df, two-tailed", "{!r} -> {:.4f}".format(p1, p1))
show("p-value check via numeric t CDF (beta = 1)", "{:.6f}".format(2 * (1 - t_cdf(t1, 3))))
tc3 = t_crit(3)
show("critical t, 3 df, 5% two-tailed (table 3.182)", "{:.4f}".format(tc3))
lo95, hi95 = w["b"] - tc3 * sb_from_t, w["b"] + tc3 * sb_from_t
show("95% CI for beta: b +/- 3.182 x s_b", "{:.4f} to {:.4f}".format(w["b"] - 3.182 * sb_from_t, w["b"] + 3.182 * sb_from_t))
show("  (exact t)", "{:.4f} to {:.4f}".format(lo95, hi95))
show("intercept a (alpha, % a month)", "{:.4f}".format(w["a"]))

# ---------------------------------------------------------------------------
print("\n=== c1 (Day 50): beta by LIN, six months ===")
X1 = [1.5, -2.0, 2.8, -0.7, 1.1, 3.0]
Y1 = [1.9, -2.9, 3.6, -1.4, 0.8, 4.1]
c1 = two_var(X1, Y1)
for k in ("n", "mx", "my", "a", "b", "r", "r2"):
    show("c1 LIN " + k, "{!r} -> {}".format(c1[k], both(c1[k])))
show("c1 answer to 3 decimals", "{:.3f}".format(c1["b"]))
show("c1 trap: stock as X (slope of X on Y)", "{:.4f}".format(c1["sxy"] / c1["syy"]))
show("c1 trap: r instead of b", "{:.4f}".format(c1["r"]))
show("c1 aside: CAPM at Rf 3%, ERP 5%", "{:.4f}".format(3 + c1["b"] * 5))
show("c1 Y06 check", both(Y1[-1]))

# ---------------------------------------------------------------------------
print("\n=== c2 (Day 50): correlation t-test, r = 0.40, n = 27 ===")
R2_, N2_ = 0.40, 27
t_c2 = R2_ * math.sqrt(N2_ - 2) / math.sqrt(1 - R2_ ** 2)
show("sqrt(25)", "{:.0f}".format(math.sqrt(N2_ - 2)))
show("1 - r^2", "{:.2f}".format(1 - R2_ ** 2))
show("sqrt(0.84)", "{:.4f}".format(math.sqrt(1 - R2_ ** 2)))
show("t = 0.40 x 5 / 0.9165 (A, right)", "{!r} -> {:.4f} -> {:.2f}".format(t_c2, t_c2, t_c2))
show("B: sqrt(1 - r^2) left out: 0.40 x 5", "{:.2f}".format(R2_ * math.sqrt(N2_ - 2)))
show("C: sqrt(n - 2) left out: 0.40 / 0.9165", "{:.4f} -> {:.2f}".format(R2_ / math.sqrt(1 - R2_ ** 2), R2_ / math.sqrt(1 - R2_ ** 2)))
show("critical t, 25 df, 5% two-tailed (table 2.060)", "{:.4f}".format(t_crit(25)))
show("trap: n - 1 = 26 df under the root", "{:.4f}".format(R2_ * math.sqrt(26) / math.sqrt(1 - R2_ ** 2)))

# ---------------------------------------------------------------------------
print("\n=== c3 (Day 51): half-width of a 95% prediction interval ===")
N3, SEE3, XBAR3, SX2_3, XF3, TC3 = 20, 2.0, 5.0, 4.0, 9.0, 2.101
dist = (XF3 - XBAR3) ** 2
den = (N3 - 1) * SX2_3
bracket = 1 + 1 / N3 + dist / den
sf = SEE3 * math.sqrt(bracket)
hw = TC3 * sf
show("(X_f - X-bar)^2 = (9 - 5)^2", "{:.0f}".format(dist))
show("(n - 1) s_X^2 = 19 x 4", "{:.0f}".format(den))
show("1/n", "{:.2f}".format(1 / N3))
show("16/76", "{!r} -> {:.4f}".format(dist / den, dist / den))
show("bracket 1 + 0.05 + 0.2105", "{!r} -> {}".format(bracket, both(bracket)))
show("sqrt(bracket)", both(math.sqrt(bracket)))
show("s_f = 2.0 x sqrt(bracket)", "{!r} -> {}".format(sf, both(sf)))
show("half-width = 2.101 x s_f (answer)", "{!r} -> {}".format(hw, both(hw)))
show("2.101 x 2", "{:.4f}".format(TC3 * SEE3))
show("trap: SEE alone 2.101 x 2.0", "{:.4f}".format(TC3 * SEE3))
show("trap: 1 dropped (mean of Y)", "{:.4f}".format(TC3 * SEE3 * math.sqrt(1 / N3 + dist / den)))
show("trap: n instead of (n - 1) in the distance term (16/80)", "{:.4f}".format(TC3 * SEE3 * math.sqrt(1 + 1 / N3 + dist / (N3 * SX2_3))))
show("at X_f = X-bar: 2.101 x 2 x sqrt(1.05)", "{:.4f}".format(TC3 * SEE3 * math.sqrt(1 + 1 / N3)))

# ---------------------------------------------------------------------------
print("\n=== c4 (Day 51): log-lin forecast ln Y = 1.8 + 0.12X at X = 12 ===")
B0_4, B1_4, X4 = 1.8, 0.12, 12
lnY = B0_4 + B1_4 * X4
Y4 = math.exp(lnY)
show("ln Y = 1.8 + 0.12 x 12", "{!r} -> {}".format(lnY, both(lnY)))
show("Y = e^3.24 (answer)", "{!r} -> {}".format(Y4, both(Y4)))
show("growth per unit X: e^0.12 - 1", "{:.4f} -> {:.2f}%".format(math.exp(B1_4) - 1, (math.exp(B1_4) - 1) * 100))
show("doubling: ln 2 / 0.12", "{:.4f}".format(math.log(2) / B1_4))
show("trap: not exponentiated", "{:.2f}".format(lnY))

# ---------------------------------------------------------------------------
print("\n=== c5 (Day 52): APT expected return ===")
RF5, L1, L2, BT1, BT2 = 3.0, 5.0, 2.0, 1.1, 0.6
er5 = RF5 + BT1 * L1 + BT2 * L2
show("1.1 x 5", "{:.2f}".format(BT1 * L1))
show("0.6 x 2", "{:.2f}".format(BT2 * L2))
show("E(R) = 3 + 5.5 + 1.2 (answer)", "{!r} -> {} -> {:.2f}%".format(er5, both(er5), er5))
show("trap: Rf left out", "{:.2f}%".format(BT1 * L1 + BT2 * L2))
show("trap: sensitivities added to premiums 3 + (1.1 + 0.6)(5 + 2)?", "{:.2f}%".format(RF5 + (BT1 + BT2) * (L1 + L2)))

# ---------------------------------------------------------------------------
print("\n=== c6 (Day 52): CAPM alpha, beta 1.4 ===")
BT6, RF6, RM6, FC6 = 1.4, 3.0, 8.0, 9.3
req6 = RF6 + BT6 * (RM6 - RF6)
show("required 3 + 1.4 x 5", "{:.2f}%".format(req6))
show("alpha 9.3 - 10.0 (B, right)", "{:+.2f}%".format(FC6 - req6))
show("A: forecast vs market return 9.3 - 8", "{:+.2f}%".format(FC6 - RM6))
show("C: beta x market return: required 3 + 1.4 x 8", "{:.2f}%".format(RF6 + BT6 * RM6))
show("C: alpha 9.3 - 14.2", "{:+.2f}%".format(FC6 - (RF6 + BT6 * RM6)))

# ---------------------------------------------------------------------------
print("\n=== c7 (Day 53): M-squared ===")
RP7, SP7, RM7, SM7, RF7 = 10.0, 18.0, 8.0, 15.0, 2.0
scaled = (RP7 - RF7) * SM7 / SP7
m2 = scaled - (RM7 - RF7)
show("excess fund 10 - 2, market 8 - 2", "{:.0f}, {:.0f}".format(RP7 - RF7, RM7 - RF7))
show("8 x 15 / 18", "{!r} -> {}".format(scaled, both(scaled)))
show("M2 = 6.6667 - 6 (answer, %)", "{!r} -> {}".format(m2, both(m2)))
show("risk-adjusted return Rf + 6.6667", "{:.4f}% vs market 8%".format(RF7 + scaled))
show("trap: (Rm - Rf) not subtracted", "{:.4f} -> {:.2f}%".format(scaled, scaled))
show("trap: whole market return subtracted 6.6667 - 8", "{:.4f}".format(scaled - RM7))
show("Sharpe fund 8/18, market 6/15", "{:.4f}, {:.4f}".format((RP7 - RF7) / SP7, (RM7 - RF7) / SM7))
show("M2 = (Sharpe_p - Sharpe_m) x sigma_m", "{:.4f}".format(((RP7 - RF7) / SP7 - (RM7 - RF7) / SM7) * SM7))

# ---------------------------------------------------------------------------
print("\n=== c8 (Day 54): effective duration, +/-25 bp ===")
P0_8, PM_8, PP_8, DC8 = 102.00, 103.10, 100.70, 0.0025
num8 = PM_8 - PP_8
den8 = 2 * DC8 * P0_8
ed8 = num8 / den8
ec8 = (PM_8 + PP_8 - 2 * P0_8) / (DC8 ** 2 * P0_8)
show("PV- - PV+", "{:.2f}".format(num8))
show("2 x 0.0025 x 102", "{:.4f}".format(den8))
show("EffDur (answer)", "{!r} -> {}".format(ed8, both(ed8)))
show("trap: 2 left out", "{:.4f}".format(num8 / (DC8 * P0_8)))
show("trap: bp as 25 not 0.0025 (x 10^-4)", "{:.6f}".format(num8 / (2 * 25 * P0_8)))
show("aside: EffCon", "{:.4f}".format(ec8))
show("gain on fall, loss on rise", "{:.2f}, {:.2f}".format(PM_8 - P0_8, P0_8 - PP_8))

# ---------------------------------------------------------------------------
print("\n=== c9 (Day 55): expected loss ===")
EXP9, RR9, POD9 = 20_000_000, 0.25, 0.015
lgd9 = EXP9 * (1 - RR9)
el9 = POD9 * lgd9
show("LGD = 20m x 0.75", "{:,.0f}".format(lgd9))
show("EL = 0.015 x 15m (answer)", "{!r} -> {:,.0f}".format(el9, el9))
show("EL as % of exposure", "{:.3f}%".format(el9 / EXP9 * 100))
show("trap: recovery rate used 0.015 x 0.25 x 20m", "{:,.0f}".format(POD9 * RR9 * EXP9))
show("trap: recovery ignored 0.015 x 20m", "{:,.0f}".format(POD9 * EXP9))
show("BA/TI display", both(el9))

# ---------------------------------------------------------------------------
print("\n=== c10 (Day 55): spread +200 bp, ModDur 5, convexity 40 ===")
MD10, CX10, DS10 = 5.0, 40.0, 0.02
dur10 = -MD10 * DS10 * 100
cvx10 = 0.5 * CX10 * DS10 ** 2 * 100
show("duration term -5 x 0.02", "{:+.2f}%".format(dur10))
show("0.02^2", "{:.4f}".format(DS10 ** 2))
show("convexity term 0.5 x 40 x 0.0004", "{:+.2f}%".format(cvx10))
show("C (right) total", "{:+.2f}%".format(dur10 + cvx10))
show("A: convexity subtracted", "{:+.2f}%".format(dur10 - cvx10))
show("B: convexity ignored", "{:+.2f}%".format(dur10))
show("aside: spread narrows 200 bp", "{:+.2f}%".format(-dur10 + cvx10))

# ---------------------------------------------------------------------------
print("\n=== Exam traps (numbers quoted from earlier days, recomputed) ===")
# M2 with Day 53's walkthrough numbers: fund 12%, sigma 4%; market 10%, sigma 15%; Rf 3%
sc53 = (12 - 3) * 15 / 4
show("Day 53: scaled excess 9 x 15/4 (stop here = the trap)", "{:.2f}%".format(sc53))
show("Day 53: M2 = 33.75 - 7", "{:.2f}%".format(sc53 - (10 - 3)))
show("Day 53: subtracting Rm itself 33.75 - 10", "{:.2f}%".format(sc53 - 10))
# Recovery vs LGD: POD 2%, recovery 40%
show("POD 2% x LGD 60%", "{:.2f}%".format(2 * 0.60))
show("POD 2% x recovery 40% (trap)", "{:.2f}%".format(2 * 0.40))
# Log-lin: ln Y = 3.0
show("e^3", "{!r} -> {:.2f}".format(math.exp(3), math.exp(3)))
# beta tests from the walkthrough, rounded for the trap text
show("walkthrough t vs 0, t vs 1 (2 dp)", "{:.2f}, {:.2f}".format(w["t_corr"], t1))
