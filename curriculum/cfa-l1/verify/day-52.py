#!/usr/bin/env python3
"""Day 52 of CFA Math: every number that appears on day-52.html.

Beta, the capital asset pricing model and the security market line
(CFA Program 2027 Level I, Portfolio Construction,
Portfolio Risk and Return: Part II).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math


def show(label, value):
    print("  {:<64} {}".format(label, value))


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def beta_from_corr(rho, s_i, s_m):
    return rho * s_i / s_m


def capm(rf, beta, erp):
    """Required return in %, with the market risk premium erp in %."""
    return rf + beta * erp


# ---------------------------------------------------------------------------
print("=== Story (8 August 2011) ===")
spx_close, spx_drop = 1119.46, 79.92
spx_prev = spx_close + spx_drop
show("S&P 500 previous close (1,119.46 + 79.92)", "{:,.2f}".format(spx_prev))
show("S&P 500 fall, %", "{:.4f}  -> 6.66%".format(100 * spx_drop / spx_prev))
gold_settle, gold_up = 1713.20, 61.40
gold_prev = gold_settle - gold_up
show("Dec gold previous settlement (1,713.20 - 61.40)", "{:,.2f}".format(gold_prev))
show("Gold rise, %", "{:.4f}  -> 3.7%".format(100 * gold_up / gold_prev))
sig_gold, sig_spx = 15.44, 14.32
show("SSGA 30-yr volatility: gold, S&P 500 (%)", "{}, {}".format(sig_gold, sig_spx))
show("Gold vol / S&P vol", "{:.4f}".format(sig_gold / sig_spx))
show("Siegel 1802-2012 real: stocks 6.6, gold 0.7; gap (points)", "{:.1f}  -> roughly 6".format(6.6 - 0.7))

# ---------------------------------------------------------------------------
print("\n=== Back to the story (illustrative correlations; R_f = 4%, ERP = 5.5%) ===")
RF_S, ERP_S = 4.0, 5.5
for rho in (0.1, 0.9, -0.1):
    b = beta_from_corr(rho, sig_gold, sig_spx)
    show("rho = {:+.1f}: beta = rho x 15.44/14.32".format(rho), "{:.6f}  -> {:.3f}".format(b, b))
    show("           CAPM required return, %", "{:.6f}  -> {:.2f}%".format(capm(RF_S, b, ERP_S), capm(RF_S, b, ERP_S)))
    show("           share of variance that is systematic (rho^2)", "{:.4f}  -> {:.0f}%".format(rho ** 2, 100 * rho ** 2))
gap = capm(RF_S, beta_from_corr(0.9, sig_gold, sig_spx), ERP_S) - capm(RF_S, beta_from_corr(0.1, sig_gold, sig_spx), ERP_S)
show("Gap between the two required returns (points)", "{:.4f}  -> {:.2f}".format(gap, gap))
show("Market itself: R_f + ERP", "{:.1f}%".format(RF_S + ERP_S))

# ---------------------------------------------------------------------------
print("\n=== The idea ===")
show("beta 1.2: market up 10% -> stock up (%)", "{:.1f}".format(1.2 * 10))

# ---------------------------------------------------------------------------
print("\n=== Why it works: diversification leaves only covariance ===")
sig, rho_pair = 0.36, 0.30
var_avg = sig ** 2
cov_avg = rho_pair * sig * sig
show("average variance (36%)^2", "{:.4f}".format(var_avg))
show("average covariance 0.3 x 0.36 x 0.36", "{:.5f}".format(cov_avg))
for n in (1, 30, 1000):
    vp = var_avg / n + (1 - 1 / n) * cov_avg
    show("N = {:>4}: sigma_p = sqrt(var/N + (1 - 1/N) cov), %".format(n), "{:.4f}  -> {:.1f}%".format(100 * math.sqrt(vp), 100 * math.sqrt(vp)))
show("N -> infinity: sigma_p = sqrt(cov), %", "{:.4f}  -> {:.1f}%".format(100 * math.sqrt(cov_avg), 100 * math.sqrt(cov_avg)))
# Mix of the market and T-bills with weight beta in the market
bmix = 0.5
show("beta 0.5 mix: 0.5 x 0 + 0.5 x 1", "{}".format(bmix * 1 + (1 - bmix) * 0))

# ---------------------------------------------------------------------------
print("\n=== Worked example ===")
cov_im, s_m, s_i = 0.0216, 0.15, 0.24
rf_w, erm_w, fc_w = 3.0, 9.0, 8.0
b_w = cov_im / s_m ** 2
show("sigma_m^2 = 0.15^2", "{:.4f}".format(s_m ** 2))
show("beta = 0.0216 / 0.0225", "{:.4f}".format(b_w))
rho_w = cov_im / (s_i * s_m)
show("0.24 x 0.15", "{:.4f}".format(s_i * s_m))
show("rho = 0.0216 / (0.24 x 0.15)", "{:.4f}".format(rho_w))
show("beta = rho x 24/15", "{:.4f}".format(rho_w * 24 / 15))
show("market risk premium 9 - 3", "{:.1f}".format(erm_w - rf_w))
req_w = capm(rf_w, b_w, erm_w - rf_w)
show("0.96 x 6", "{:.2f}".format(b_w * (erm_w - rf_w)))
show("E(R) = 3 + 0.96 x 6, %", "{:.4f}".format(req_w))
show("alpha = 8 - 8.76, %", "{:.4f}".format(fc_w - req_w))
show("TRAP beta x market return: 3 + 0.96 x 9", "{:.4f}".format(rf_w + b_w * erm_w))
show("TRAP percent/decimal mix: 0.0216 / 15^2", "{:.6f}".format(cov_im / 15 ** 2))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough (rho 0.6, sigma_i 36%, sigma_m 18%, R_f 4%, ERP 5.5%, forecast 14%) ===")
rho_k, si_k, sm_k, rf_k, erp_k, fc_k = 0.6, 36.0, 18.0, 4.0, 5.5, 14.0
b_k = beta_from_corr(rho_k, si_k, sm_k)
show("beta = 0.6 x 36 / 18  (display)", d4(b_k))
req_k = capm(rf_k, b_k, erp_k)
show("required = 4 + 1.2 x 5.5  (display)", d4(req_k))
show("1.2 x 5.5", d4(b_k * erp_k))
show("Chn-mode error: (4 + 1.2) x 5.5", d4((rf_k + b_k) * erp_k))
a_k = fc_k - req_k
show("alpha = 14 - 10.6  (display)", d4(a_k))
show("E(R_m) = 4 + 5.5", d4(rf_k + erp_k))
show("systematic sd = beta x sigma_m = rho x sigma_i (%)", "{:.4f} / {:.4f}".format(b_k * sm_k, rho_k * si_k))
show("total variance 36^2", "{:.2f}".format(si_k ** 2))
show("systematic variance (1.2 x 18)^2", "{:.2f}".format((b_k * sm_k) ** 2))
show("unsystematic variance", "{:.2f}".format(si_k ** 2 - (b_k * sm_k) ** 2))
show("unsystematic sd", "{:.4f}".format(math.sqrt(si_k ** 2 - (b_k * sm_k) ** 2)))
show("systematic share rho^2", "{:.4f}".format(rho_k ** 2))
cml_k = rf_k + erp_k / sm_k * si_k
show("TRAP CML pricing: 4 + (5.5/18) x 36", d4(cml_k))
show("TRAP CML alpha: 14 - 15", d4(fc_k - cml_k))
show("CML premium for diversifiable risk: 15 - 10.6", d4(cml_k - req_k))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
b0, b1, t = 3.50, 0.08, 10
lnf = b0 + b1 * t
show("w1 (Day 51) ln forecast 3.50 + 0.08 x 10", d4(lnf))
show("w1 forecast e^4.3 ($ millions)", "{!r}  -> {}".format(math.exp(lnf), d4(math.exp(lnf))))
dbar, sd, n = 0.60, 1.50, 25
show("w2 (Day 45) s_d / sqrt(n) = 1.5 / 5", "{:.2f}".format(sd / math.sqrt(n)))
show("w2 right: t = 0.60 / 0.30", "{:.2f}".format(dbar / (sd / math.sqrt(n))))
show("w2 distractor: forgot sqrt(n) 0.60 / 1.50", "{:.2f}".format(dbar / sd))
show("w2 distractor: divided by n, 0.60 / (1.50/25)", "{:.2f}".format(dbar / (sd / n)))
show("w2 df", "{}".format(n - 1))

# ---------------------------------------------------------------------------
print("\n=== Check questions ===")
rho1, si1, sm1, rf1, erp1, fc1 = 0.45, 40.0, 20.0, 3.5, 6.0, 7.5
b1c = beta_from_corr(rho1, si1, sm1)
req1 = capm(rf1, b1c, erp1)
a1 = fc1 - req1
show("c1 beta = 0.45 x 40/20", d4(b1c))
show("c1 0.9 x 6", d4(b1c * erp1))
show("c1 required = 3.5 + 0.9 x 6", d4(req1))
show("c1 alpha = 7.5 - 8.9 (answer)", "{!r}  -> {}".format(a1, d4(a1)))
show("c1 Chn-mode error (3.5 + 0.9) x 6", d4((rf1 + b1c) * erp1))

req3, rf3, erm3 = 12.4, 4.0, 10.0
b3 = (req3 - rf3) / (erm3 - rf3)
show("c3 beta = (12.4 - 4)/(10 - 4)", "{!r}  -> {}".format(b3, d4(b3)))
show("c3 12.4 - 4 ; 10 - 4", "{:.1f} ; {:.1f}".format(req3 - rf3, erm3 - rf3))
show("c3 check: 4 + 1.4 x 6", "{:.1f}".format(capm(rf3, b3, erm3 - rf3)))
show("c3 wrong: 12.4 / 10 (no R_f)", "{:.2f}".format(req3 / erm3))
show("c3 wrong: (12.4 - 4) / 10", "{:.2f}".format((req3 - rf3) / erm3))

b4, rf4, erm4 = 1.3, 3.0, 9.0
show("c4 right C: 3 + 1.3 x 6", "{:.2f}".format(capm(rf4, b4, erm4 - rf4)))
show("c4 A: 3 + 1.3 x 9 (beta x market return)", "{:.2f}".format(rf4 + b4 * erm4))
show("c4 B: 1.3 x 6 (left out R_f)", "{:.2f}".format(b4 * (erm4 - rf4)))

# ---------------------------------------------------------------------------
print("\n=== Text checks ===")
h1 = "8 August 2011: stocks fell 6.66% and gold settled at a record. Why markets pay for beta, not volatility"
show("h1 length (under ~110)", len(h1))
