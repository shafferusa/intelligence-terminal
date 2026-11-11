#!/usr/bin/env python3
"""Day 53 of CFA Math: every number that appears on day-53.html.

Risk-adjusted performance measures: the Sharpe ratio, the Treynor ratio, M²
and Jensen's alpha, with the Sortino and Calmar ratios for downside risk
(CFA Program 2027 Level I, Portfolio Construction: Portfolio Risk and Return:
Part II; Sortino and Calmar from Alternative Investments).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math


def show(label, value):
    print("  {:<66} {}".format(label, value))


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


def both(x):
    return "BA {} / TI {}".format(d4(x), ti4(x))


def sharpe(rp, rf, sd):
    return (rp - rf) / sd


def treynor(rp, rf, beta):
    return (rp - rf) / beta


def m2(rp, rf, sdp, rm, sdm):
    return (rp - rf) * sdm / sdp - (rm - rf)


def jensen(rp, rf, beta, rm):
    return rp - (rf + beta * (rm - rf))


# ---------------------------------------------------------------------------
print("=== Release date ===")
START = datetime.date(2026, 11, 16)
rel = START + datetime.timedelta(days=53 - 1)
show("Day 53 due on start_date + 52 days", "{} ({})".format(rel.isoformat(), rel.strftime("%A")))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
# w1 (Day 52): beta from covariance, then the CAPM required return
COV, SDM_W, RF_W, RM_W = 0.0240, 0.20, 3.0, 9.0
var_m = SDM_W ** 2
beta_w = COV / var_m
erp_w = RM_W - RF_W
req_w = RF_W + beta_w * erp_w
show("w1 sigma_m^2 = 0.20^2", "{:.4f}".format(var_m))
show("w1 beta = 0.0240 / 0.04 (STO 1 / ->B)", "{!r} -> {}".format(beta_w, both(beta_w)))
show("w1 ERP = 9 - 3", erp_w)
show("w1 beta x ERP = 0.6 x 6", "{:.4f}".format(beta_w * erp_w))
show("w1 required return = 3 + 0.6 x 6 (answer, %)", "{!r} -> {}".format(req_w, both(req_w)))
show("w1 trap: 3 + beta x market return 9", "{:.2f}".format(RF_W + beta_w * RM_W))
show("w1 trap: beta from SD not variance 0.024/0.20", "{:.4f}".format(COV / SDM_W))

# w2 (Day 46): F-statistic for equal variances
SA, NA, SB, NB = 6.0, 21, 4.0, 25
F = SA ** 2 / SB ** 2
show("w2 variances 6^2, 4^2", "{:.0f}, {:.0f}".format(SA ** 2, SB ** 2))
show("w2 F = 36/16 (answer B)", "{:.4f} -> {:.2f}, df ({}, {})".format(F, F, NA - 1, NB - 1))
show("w2 distractor A: ratio of SDs 6/4", "{:.2f}".format(SA / SB))
show("w2 distractor C: df taken as n", "({}, {})".format(NA, NB))
show("w2 smaller on top 16/36", "{:.4f}".format(SB ** 2 / SA ** 2))

# ---------------------------------------------------------------------------
print("\n=== Story: Fairfield Sentry's August 2007 sheet ===")
for label, dt in [("Markopolos e-mail", datetime.date(2005, 11, 7)),
                  ("examiner's e-mail", datetime.date(2005, 11, 10)),
                  ("Madoff arrested", datetime.date(2008, 12, 11))]:
    show("{} {}".format(label, dt.isoformat()), dt.strftime("%A"))
R_S, SD_S, SH_S = 10.85, 2.50, 2.75          # Sentry: CAR, annual SD, printed Sharpe
R_I, SD_I, SH_I = 11.21, 14.11, 0.51         # S&P 100
R_L, SD_L, SH_L = 6.96, 3.73, 0.80           # Lehman Aggregate
rf_imp = R_S - SH_S * SD_S
show("implied R_f = 10.85 - 2.75 x 2.50", "{!r} -> {:.3f}%".format(rf_imp, rf_imp))
ex_s = R_S - rf_imp
ex_i = R_I - rf_imp
ex_l = R_L - rf_imp
show("Sentry excess return 10.85 - 3.975", "{:.3f} -> {:.2f}".format(ex_s, ex_s))
show("S&P 100 excess return 11.21 - 3.975", "{:.3f} -> {:.2f}".format(ex_i, ex_i))
show("Lehman Agg excess return 6.96 - 3.975", "{:.3f}".format(ex_l))
show("Sharpe check Sentry 6.875/2.50", "{:.4f}".format(ex_s / SD_S))
show("Sharpe check S&P 100 7.235/14.11 (printed 0.51)", "{:.4f} -> {:.2f}".format(ex_i / SD_I, ex_i / SD_I))
show("Sharpe check Lehman 2.985/3.73 (printed 0.80)", "{:.4f} -> {:.2f}".format(ex_l / SD_L, ex_l / SD_L))
show("Sentry Sharpe / S&P 100 Sharpe (computed)", "{:.4f} -> {:.1f}x".format(SH_S / (ex_i / SD_I), SH_S / (ex_i / SD_I)))
show("Sentry Sharpe / S&P 100 Sharpe (printed 2.75/0.51)", "{:.4f} -> {:.1f}x".format(SH_S / SH_I, SH_S / SH_I))
show("SD ratio 14.11/2.50 (leverage to the index's risk)", "{:.4f}".format(SD_I / SD_S))
lev_ret = rf_imp + ex_s * SD_I / SD_S
m2_s = lev_ret - R_I
show("6.875 x 14.11/2.50", "{:.4f}".format(ex_s * SD_I / SD_S))
show("risk-adjusted return 3.975 + 6.875 x 14.11/2.50", "{!r} -> {:.2f}%".format(lev_ret, lev_ret))
show("M^2 = that - 11.21", "{!r} -> {:.2f} points".format(m2_s, m2_s))
show("M^2 by formula", "{:.4f}".format(m2(R_S, rf_imp, SD_S, R_I, SD_I)))
show("M^2 = (S_p - S_m) x sigma_m check", "{:.4f}".format((SH_S - ex_i / SD_I) * SD_I))
show("Calmar-style 10.85 / 0.64 (worst drawdown -0.64%)", "{:.4f} -> about {:.0f}".format(R_S / 0.64, R_S / 0.64))
show("S&P 100 raw return minus Sentry's", "{:.2f}".format(R_I - R_S))

# ---------------------------------------------------------------------------
print("\n=== Worked example: funds A and B ===")
RF, RM, SDM = 2.0, 9.0, 16.0
A = dict(r=14.0, sd=20.0, b=1.2)
B = dict(r=10.0, sd=12.0, b=0.7)
show("excess A, B, market", "{}, {}, {}".format(A["r"] - RF, B["r"] - RF, RM - RF))
for name, f in (("A", A), ("B", B)):
    rho = f["b"] * SDM / f["sd"]
    show("rho {} = beta x sigma_m/sigma_p".format(name), "{:.4f} -> {:.2f}".format(rho, rho))
show("Sharpe A 12/20", "{:.4f}".format(sharpe(A["r"], RF, A["sd"])))
show("Sharpe B 8/12", "{:.4f}".format(sharpe(B["r"], RF, B["sd"])))
show("Sharpe market 7/16", "{:.4f}".format(sharpe(RM, RF, SDM)))
show("Treynor A 12/1.2", "{:.4f}".format(treynor(A["r"], RF, A["b"])))
show("Treynor B 8/0.7", "{:.4f}".format(treynor(B["r"], RF, B["b"])))
show("Treynor market 7/1", "{:.4f}".format(treynor(RM, RF, 1.0)))
for name, f in (("A", A), ("B", B)):
    part = (f["r"] - RF) * SDM / f["sd"]
    show("M2 {}: (excess) x 16/sd = ".format(name), "{:.4f}".format(part))
    show("M2 {}: risk-adjusted return 2 + that".format(name), "{:.4f}".format(RF + part))
    show("M2 {} (answer)".format(name), "{:.4f}".format(m2(f["r"], RF, f["sd"], RM, SDM)))
for name, f in (("A", A), ("B", B)):
    req = RF + f["b"] * (RM - RF)
    show("CAPM required {} = 2 + {} x 7".format(name, f["b"]), "{:.4f} (beta x 7 = {:.4f})".format(req, f["b"] * (RM - RF)))
    show("Jensen {} (answer)".format(name), "{:.4f}".format(jensen(f["r"], RF, f["b"], RM)))
    gap = treynor(f["r"], RF, f["b"]) - (RM - RF)
    show("  check alpha = beta x (T_p - T_m) {}".format(name), "{} x {:.4f} = {:.4f}".format(f["b"], gap, f["b"] * gap))
show("Calmar A = 14/25", "{:.4f}".format(14 / 25))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough ===")
RP, SDP, BP, RMW, SDMW, RFW = 12.0, 4.0, 0.2, 10.0, 15.0, 3.0
ex = RP - RFW
exm = RMW - RFW
show("fund excess 12 - 3 (STO 1 / ->E)", both(ex))
show("market excess 10 - 3", exm)
show("Sharpe fund RCL1/4", both(ex / SDP))
show("Sharpe market 7/15", both(exm / SDMW))
show("Sharpe fund / market", "{:.4f} -> {:.1f}x".format((ex / SDP) / (exm / SDMW), (ex / SDP) / (exm / SDMW)))
show("Treynor fund RCL1/.2", both(ex / BP))
show("Treynor market 7/1", both(exm / 1))
adj = ex * SDMW / SDP + RFW
show("9 x 15/4", both(ex * SDMW / SDP))
show("risk-adjusted return RCL1 x 15/4 + 3", both(adj))
show("M2 = that - 10 (answer)", "{!r} -> {}".format(adj - RMW, both(adj - RMW)))
show("M2 by formula 9 x 15/4 - 7", "{:.4f}".format(m2(RP, RFW, SDP, RMW, SDMW)))
show("leverage factor 15/4", "{:.4f}".format(SDMW / SDP))
show("beta x premium .2 x 7", "{:.4f}".format(BP * exm))
show("CAPM required 3 + .2 x 7", both(RFW + BP * exm))
show("Jensen 12 - (3 + .2 x 7)", both(jensen(RP, RFW, BP, RMW)))
show("  check alpha = beta x (T_p - T_m) = .2 x (45 - 7)", "{:.4f}".format(BP * (ex / BP - exm)))
show("rho implied = beta x 15/4", "{:.4f}".format(BP * SDMW / SDP))
show("max possible beta with sigma_p 4 = 4/15", "{:.4f}".format(SDP / SDMW))
show("TRAP M2 without subtracting (R_m - R_f): 9 x 15/4", "{:.4f}".format(ex * SDMW / SDP))
show("TRAP M2 = risk-adjusted return itself", "{:.4f}".format(adj))

# ---------------------------------------------------------------------------
print("\n=== c1: M^2 on the calculator ===")
r1, s1, m1, sm1, f1 = 9.5, 12.0, 8.0, 14.0, 2.5
ex1 = r1 - f1
adj1 = f1 + ex1 * sm1 / s1
ans1 = m2(r1, f1, s1, m1, sm1)
show("excess 9.5 - 2.5 (STO 1)", both(ex1))
show("market excess 8 - 2.5", m1 - f1)
show("7 x 14/12", both(ex1 * sm1 / s1))
show("risk-adjusted return 2.5 + 7 x 14/12", both(adj1))
show("M2 = that - 8 (answer)", "{!r} -> {} -> {:.2f}".format(ans1, both(ans1), ans1))
show("trap: forgot to subtract (R_m - R_f)", "{:.4f} -> {:.2f}".format(ex1 * sm1 / s1, ex1 * sm1 / s1))
show("Sharpe fund, market", "{:.4f}, {:.4f}".format(ex1 / s1, (m1 - f1) / sm1))
show("check (S_p - S_m) x 14", "{:.4f}".format((ex1 / s1 - (m1 - f1) / sm1) * sm1))

# ---------------------------------------------------------------------------
print("\n=== c3: Sortino ratio ===")
rets = [12.0, -4.0, 9.0, -8.0, 15.0]
MAR = 0.0
n = len(rets)
mean = sum(rets) / n
short = [x for x in rets if x <= MAR]
ss = sum((x - MAR) ** 2 for x in short)
dd = math.sqrt(ss / (n - 1))
sortino = (mean - MAR) / dd
show("sum of returns", sum(rets))
show("mean", both(mean))
show("shortfalls", short)
show("sum of squared shortfalls 16 + 64", ss)
show("/ (n - 1) = 80/4", ss / (n - 1))
show("downside deviation sqrt(20) (STO 1)", both(dd))
show("Sortino = 4.8 / 4.4721 (answer)", "{!r} -> {} -> {:.2f}".format(sortino, both(sortino), sortino))
dd_wrong = math.sqrt(ss / len(short))
show("trap: divide by 2 losing years: dd", "{:.4f}".format(dd_wrong))
show("trap: Sortino with that", "{:.4f}".format(mean / dd_wrong))
sd_all = math.sqrt(sum((x - mean) ** 2 for x in rets) / (n - 1))
show("sample SD of all five", "{:.4f}".format(sd_all))
show("mean / full SD (a Sharpe-style ratio, R_f = 0)", "{:.4f} -> {:.2f}".format(mean / sd_all, mean / sd_all))
show("trap: divide by n = 5", "{:.4f}".format(mean / math.sqrt(ss / n)))

# ---------------------------------------------------------------------------
print("\n=== c4: Jensen's alpha, exam style ===")
r4, b4, m4, f4 = 11.0, 1.3, 9.0, 3.0
show("premium 9 - 3", m4 - f4)
show("1.3 x 6", "{:.4f}".format(b4 * (m4 - f4)))
show("required 3 + 7.8", "{:.4f}".format(f4 + b4 * (m4 - f4)))
show("A (right): 11 - 10.8", "{:.4f}".format(jensen(r4, f4, b4, m4)))
show("B: beta x market return: 11 - (3 + 1.3 x 9)", "{:.4f} (1.3 x 9 = {:.4f}; 3 + that = {:.4f})".format(
    r4 - (f4 + b4 * m4), b4 * m4, f4 + b4 * m4))
show("C: raw 11 - 9", "{:.4f}".format(r4 - m4))
show("Treynor of the fund (8/1.3)", "{:.4f}".format(treynor(r4, f4, b4)))
show("check alpha = 1.3 x (T_p - 6)", "{:.4f}".format(b4 * (treynor(r4, f4, b4) - (m4 - f4))))
