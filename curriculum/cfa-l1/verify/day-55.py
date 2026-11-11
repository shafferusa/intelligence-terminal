#!/usr/bin/env python3
"""Day 55 of CFA Math: every number that appears on day-55.html.

Credit risk: expected loss = probability of default x loss given default,
LGD = exposure x (1 - recovery rate), the yield spread as roughly POD x LGD a
year, and the price impact of a spread change,
%dPV ~ -ModDur x dSpread + 1/2 x Convexity x (dSpread)^2
(CFA Program 2027 Level I, Fixed Income: Credit Risk).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from decimal import Decimal, ROUND_HALF_UP


def show(label, value):
    print("  {:<68} {}".format(label, value))


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


def lgd(exposure, rr):
    return exposure * (1 - rr)


def pct_dpv(moddur, conv, ds):
    """Duration term, convexity term and total, each in percent."""
    dur = -moddur * ds * 100
    cvx = 0.5 * conv * ds ** 2 * 100
    return dur, cvx, dur + cvx


# ---------------------------------------------------------------------------
print("=== Release date ===")
START = datetime.date(2026, 11, 16)
rel = START + datetime.timedelta(days=55 - 1)
show("Day 55 due on start_date + 54 days", "{} ({})".format(rel.isoformat(), rel.strftime("%A")))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
# w1 (Day 54): effective duration of a callable bond
P0, PM, PP, DC = 99.50, 100.30, 98.60, 0.0030
effdur = (PM - PP) / (2 * DC * P0)
effcon = (PM + PP - 2 * P0) / (DC ** 2 * P0)
show("w1 numerator PV- - PV+ = 100.30 - 98.60", "{:.2f}".format(PM - PP))
show("w1 denominator 2 x 0.003 x 99.50", "{:.4f}".format(2 * DC * P0))
show("w1 EffDur (answer)", "{!r} -> {}".format(effdur, both(effdur)))
show("w1 gain if curve falls 30 bp (100.30 - 99.50)", "{:.2f}".format(PM - P0))
show("w1 loss if curve rises 30 bp (99.50 - 98.60)", "{:.2f}".format(P0 - PP))
show("w1 EffCon (aside: negative)", "{:.2f}".format(effcon))
show("w1 trap: forgot the 2 (1.70/(0.003 x 99.50))", "{:.4f}".format((PM - PP) / (DC * P0)))

# w2 (Day 48): R^2 and SEE from SST and SSE
N2, SST, SSE = 32, 0.0800, 0.0300
SSR = SST - SSE
R2 = SSR / SST
SEE = math.sqrt(SSE / (N2 - 2))
SEE_wrong = math.sqrt(SSE / (N2 - 1))
show("w2 SSR = SST - SSE", "{:.4f}".format(SSR))
show("w2 R^2 = SSR/SST (right)", "{:.4f}".format(R2))
show("w2 SSE/SST (trap: the unexplained share)", "{:.4f}".format(SSE / SST))
show("w2 MSE = SSE/(n - 2) = 0.03/30", "{:.6f}".format(SSE / (N2 - 2)))
show("w2 SEE = sqrt(MSE) (right)", "{:.6f} -> {:.4f}".format(SEE, SEE))
show("w2 trap: sqrt(SSE/(n - 1)) = sqrt(0.03/31)", "{:.6f} -> {:.4f}".format(SEE_wrong, SEE_wrong))

# ---------------------------------------------------------------------------
print("\n=== Story: Lehman, 10 October 2008 ===")
REC = 8.625
LGD_L = 100 - REC
show("auction final price (recovery) per 100", REC)
show("LGD per 100 = 100 - 8.625 (paid by protection sellers)", "{:.3f}".format(LGD_L))
show("recovery rate as a decimal", "{:.5f}".format(REC / 100))
show("LGD as a share of exposure", "{:.5f}".format(LGD_L / 100))
show("check: 100 x (1 - 0.08625)", "{:.3f}".format(lgd(100, REC / 100)))

# ---------------------------------------------------------------------------
print("\n=== Why it works: 100 bonds of 100, POD 2%, RR 40% ===")
NB, FACE, POD_W, RR_W = 100, 100, 0.02, 0.40
defaults = NB * POD_W
loss_each = lgd(FACE, RR_W)
total = defaults * loss_each
lent = NB * FACE
show("expected defaults out of 100", "{:.0f}".format(defaults))
show("loss on each default = 100 x 0.60", "{:.0f}".format(loss_each))
show("total expected loss", "{:.0f}".format(total))
show("total lent", "{:,}".format(lent))
show("loss as % of lent = spread needed", "{:.2f}%".format(total / lent * 100))
show("POD x LGD% = 2% x 60%", "{:.2f}%".format(POD_W * (1 - RR_W) * 100))

# ---------------------------------------------------------------------------
print("\n=== Worked example ===")
EXP, POD_E, RR_E = 10_000_000, 0.02, 0.40
MD_E, CX_E, DS_E = 4.2, 22, -0.0050
LGD_E = lgd(EXP, RR_E)
EL_E = POD_E * LGD_E
spread_E = POD_E * (1 - RR_E) * 100
dur_e, cvx_e, tot_e = pct_dpv(MD_E, CX_E, DS_E)
show("LGD = 10,000,000 x (1 - 0.40)", "{:,.0f}".format(LGD_E))
show("EL = 0.02 x 6,000,000", "{:,.0f}".format(EL_E))
show("EL as % of exposure", "{:.2f}%".format(EL_E / EXP * 100))
show("spread ~ 2% x 60% (%)", "{:.2f}%  = {:.0f} bp".format(spread_E, spread_E * 100))
show("(-0.005)^2", "{:.6f}".format(DS_E ** 2))
show("1/2 x 22", "{:.0f}".format(0.5 * CX_E))
show("convexity term as a decimal 11 x 0.000025", "{:.6f}".format(0.5 * CX_E * DS_E ** 2))
show("duration term -4.2 x (-0.005), %", "{:+.4f}%".format(dur_e))
show("convexity term 1/2 x 22 x 0.000025, %", "{:+.4f}%".format(cvx_e))
show("total %dPV", "{:+.4f}%  -> {:+.2f}%".format(tot_e, tot_e))
show("trap: convexity subtracted", "{:+.4f}%".format(dur_e - cvx_e))
show("trap: EL with the recovery rate 0.02 x 0.40 x 10m", "{:,.0f}".format(POD_E * RR_E * EXP))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: Lehman LGD, 3% POD, ModDur 6.5, Cvx 55, +300 bp ===")
POD_K, MD_K, CX_K, DS_K = 0.03, 6.5, 55, 0.03
el_k = POD_K * LGD_L
dur_k, cvx_k, tot_k = pct_dpv(MD_K, CX_K, DS_K)
show("step 2: 100 - 8.625 (STO 1 / ->L)", "{!r} -> {}".format(LGD_L, both(LGD_L)))
show("step 3: EL per 100 = 91.375 x 0.03 (= spread %)", "{!r} -> {}".format(el_k, both(el_k)))
show("step 3: spread in bp", "{:.3f} bp -> about {:.0f} bp".format(el_k * 100, el_k * 100))
show("step 4: duration term -6.5 x 0.03 x 100", "{!r} -> {}".format(dur_k, both(dur_k)))
show("step 5: 0.03^2 (BA shows after x^2)", "{} -> {}".format(DS_K ** 2, both(DS_K ** 2)))
show("step 5: convexity term 0.5 x 55 x 0.0009 x 100", "{!r} -> {}".format(cvx_k, both(cvx_k)))
show("step 6: total = 2.475 + (-19.5)", "{!r} -> {}".format(tot_k, both(tot_k)))
show("trap: convexity with wrong sign", "{:.4f}".format(dur_k - cvx_k))
show("trap: squaring the change in percent, 1/2 x 55 x 3^2", "{:.4f}".format(0.5 * CX_K * 3 ** 2))
show("trap: total with that, -6.5 x 3 + 247.5", "{:+.4f}".format(-MD_K * 3 + 0.5 * CX_K * 3 ** 2))
show("each 1% of POD needs (bp of spread)", "{:.3f} bp".format(0.01 * LGD_L / 100 * 10000))

# ---------------------------------------------------------------------------
print("\n=== Back to the story ===")
SPREAD1 = 1.0  # percent
pod_40 = SPREAD1 / 0.60
pod_leh = SPREAD1 / (LGD_L / 100)
show("1% spread, RR 40% -> POD = 1/0.60 (%)", "{:.4f}% -> {:.2f}%".format(pod_40, pod_40))
show("1% spread, Lehman LGD -> POD = 1/0.91375 (%)", "{:.4f}% -> {:.2f}%".format(pod_leh, pod_leh))
show("3% POD x 91.375% (%)", "{:.5f}% -> {:.2f}%".format(POD_K * LGD_L, POD_K * LGD_L))
show("3% POD x 60% (what a 40% recovery assumption would charge)", "{:.2f}%".format(POD_K * 60))
show("+300 bp: duration, convexity, total (%)", "{:.2f}, {:+.3f}, {:.3f}".format(dur_k, cvx_k, tot_k))
show("on 100 of price, the hit is about", "{:.3f}".format(-tot_k))

# ---------------------------------------------------------------------------
print("\n=== Check questions ===")
# c1: ModDur 7.2, convexity 70, +150 bp
MD1, CX1, DS1 = 7.2, 70, 0.015
dur1, cvx1, tot1 = pct_dpv(MD1, CX1, DS1)
show("c1 duration term -7.2 x 0.015 x 100", "{!r} -> {}".format(dur1, both(dur1)))
show("c1 0.015^2", "{:.6f}".format(DS1 ** 2))
show("c1 1/2 x 70", "{:.0f}".format(0.5 * CX1))
show("c1 convexity term as a decimal 35 x 0.000225", "{:.6f}".format(0.5 * CX1 * DS1 ** 2))
show("c1 duration term as a decimal -7.2 x 0.015", "{:.3f}".format(-MD1 * DS1))
show("c1 convexity term 0.5 x 70 x 0.000225 x 100", "{!r} -> {}".format(cvx1, both(cvx1)))
show("c1 total (answer, %)", "{!r} -> {}".format(tot1, both(tot1)))
show("c1 answer to 2 decimals", "{:.2f}".format(tot1))
show("c1 trap: wrong convexity sign", "{:.4f} -> {:.2f}".format(dur1 - cvx1, dur1 - cvx1))
show("c1 trap: duration only", "{:.2f}".format(dur1))

# c2: conceptual, no numbers beyond the 40% / 60% illustration
show("c2 illustration: POD 2%, RR 40% -> POD x LGD", "{:.2f}%".format(2 * 0.60))
show("c2 option A (POD alone)", "{:.2f}%".format(2.0))
show("c2 option C (POD x RR)", "{:.2f}%".format(2 * 0.40))

# c3: implied POD from a 240 bp spread and 40% recovery
SPR3, RR3 = 2.40, 0.40
pod3 = SPR3 / (1 - RR3)
pod3_wrong = SPR3 / RR3
show("c3 implied POD = 2.40% / 0.60 (answer, %)", "{!r} -> {:.2f}%".format(pod3, pod3))
show("c3 trap: 2.40% / 0.40", "{:.2f}%".format(pod3_wrong))
show("c3 check: 4% x 60%", "{:.2f}%".format(0.04 * 0.60 * 100))
show("c3 BA/TI display 2.4 / 0.6", both(SPR3 / 0.6))
show("c3 BA/TI display 2.4 / (1 - .4)", both(SPR3 / (1 - RR3)))

# c4: EL on $5m, RR 30%, POD 2.5%
EXP4, RR4, POD4 = 5_000_000, 0.30, 0.025
lgd4 = lgd(EXP4, RR4)
el4 = POD4 * lgd4
show("c4 LGD = 5,000,000 x 0.70", "{:,.0f}".format(lgd4))
show("c4 EL = 0.025 x 3,500,000 (A, right)", "{:,.0f}".format(el4))
show("c4 B: recovery rate used, 0.025 x 0.30 x 5m", "{:,.0f}".format(POD4 * RR4 * EXP4))
show("c4 C: recovery ignored, 0.025 x 5m", "{:,.0f}".format(POD4 * EXP4))
show("c4 EL as % of exposure (spread ~)", "{:.3f}%".format(el4 / EXP4 * 100))

# c5: Lehman summary numbers reused (no new ones)
show("c5 reuse: LGD 91.375, 3% POD -> spread", "{:.2f}%".format(POD_K * LGD_L))
