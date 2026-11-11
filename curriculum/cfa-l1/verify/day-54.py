#!/usr/bin/env python3
"""Day 54 of CFA Math: every number that appears on day-54.html.

Effective duration and effective convexity measured by shifting the benchmark
curve, negative convexity of callable bonds and mortgage-backed securities,
key rate durations, and the SMM / CPR prepayment conversion
(CFA Program 2027 Level I, Fixed Income: Curve-Based and Empirical
Fixed-Income Risk Measures; Mortgage-Backed Security (MBS) Instrument and
Market Features).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime


def show(label, value):
    print("  {:<70} {}".format(label, value))


def d4(x):
    """A 4-decimal BA II Plus display (thousands separators)."""
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


def eff_dur(pv_minus, pv0, pv_plus, dcurve):
    return (pv_minus - pv_plus) / (2 * dcurve * pv0)


def eff_con(pv_minus, pv0, pv_plus, dcurve):
    return (pv_minus + pv_plus - 2 * pv0) / (dcurve ** 2 * pv0)


def pct_change(dur, con, dy):
    """%dPV (as a decimal) ~ -Dur x dy + 1/2 x Con x dy^2."""
    return -dur * dy + 0.5 * con * dy ** 2


def cpr_from_smm(smm):
    return 1 - (1 - smm) ** 12


def smm_from_cpr(cpr):
    return 1 - (1 - cpr) ** (1 / 12)


# ---------------------------------------------------------------------------
print("=== Release date ===")
START = datetime.date(2026, 11, 16)
rel = START + datetime.timedelta(days=54 - 1)
show("Day 54 due on start_date + 53 days", "{} ({})".format(rel.isoformat(), rel.strftime("%A")))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
# w1 (Day 53): Jensen's alpha
RP_W, B_W, RF_W, RM_W = 9.8, 0.8, 2.6, 8.5
prem_w = RM_W - RF_W
req_w = RF_W + B_W * prem_w
alpha_w = RP_W - req_w
show("w1 market risk premium 8.5 - 2.6", "{:.4f}".format(prem_w))
show("w1 beta x premium 0.8 x 5.9", "{:.4f}".format(B_W * prem_w))
show("w1 CAPM required return 2.6 + 4.72", "{:.4f}".format(req_w))
show("w1 alpha = 9.8 - 7.32 (answer, %)", "{!r} -> {}".format(alpha_w, both(alpha_w)))
show("w1 trap: beta x market return, 9.8 - (2.6 + 0.8 x 8.5)", "{:.4f}".format(RP_W - (RF_W + B_W * RM_W)))
show("w1 trap: raw 9.8 - 8.5", "{:.4f}".format(RP_W - RM_W))

# w2 (Day 39): modified duration estimate for a yield fall
MAC_W, Y_W, DY_W = 8.40, 0.05, -0.0030
mod_w = MAC_W / (1 + Y_W)
est_w = -mod_w * DY_W * 100
show("w2 ModDur = 8.40/1.05", "{:.4f}".format(mod_w))
show("w2 estimate -ModDur x (-0.30) (answer A, %)", "{:+.4f}".format(est_w))
show("w2 distractor B: MacDur x 0.30", "{:+.4f}".format(MAC_W * 0.30))
show("w2 distractor C: sign flipped", "{:+.4f}".format(-est_w))

# ---------------------------------------------------------------------------
print("\n=== Story ===")
show("Fed funds target 3% -> 3.25%: change in bp", "{:.0f}".format((3.25 - 3.00) * 100))
show("4 February 1994 was a", datetime.date(1994, 2, 4).strftime("%A"))

# ---------------------------------------------------------------------------
print("\n=== Worked example: mortgage pass-through, +/-50 bp ===")
P0, PM, PP, DC = 98.50, 100.10, 96.60, 0.005
num_d = PM - PP
den_d = 2 * DC * P0
ed = eff_dur(PM, P0, PP, DC)
num_c = PM + PP - 2 * P0
den_c = DC ** 2 * P0
ec = eff_con(PM, P0, PP, DC)
show("gain on the fall 100.10 - 98.50", "{:.2f}".format(PM - P0))
show("loss on the rise 98.50 - 96.60", "{:.2f}".format(P0 - PP))
show("EffDur numerator 100.10 - 96.60", "{:.2f}".format(num_d))
show("EffDur denominator 2 x 0.005 x 98.50", "{:.4f}".format(den_d))
show("EffDur", "{!r} -> {:.4f}".format(ed, ed))
show("EffCon numerator 100.10 + 96.60 - 197.00", "{:.2f}".format(num_c))
show("PV- + PV+", "{:.2f}".format(PM + PP))
show("2 x PV0", "{:.2f}".format(2 * P0))
show("0.005^2", "{:.6f}".format(DC ** 2))
show("EffCon denominator 0.005^2 x 98.50", "{:.7f}".format(den_c))
show("EffCon", "{!r} -> {:.2f}".format(ec, ec))
conv_term = 0.5 * ec * 0.01 ** 2
show("convexity term for 100 bp: 1/2 x EffCon x 0.01^2 (%)", "{:.4f} -> {:.2f}%".format(conv_term * 100, conv_term * 100))
show("duration term for 100 bp (%)", "{:.4f}".format(ed * 0.01 * 100))
up = pct_change(ed, ec, 0.01) * 100
dn = pct_change(ed, ec, -0.01) * 100
show("+100 bp estimate (%)", "{:.4f} -> {:.2f}%".format(up, up))
show("-100 bp estimate (%)", "{:+.4f} -> {:+.2f}%".format(dn, dn))
smm_we = smm_from_cpr(0.06)
show("0.94^(1/12)", "{:.6f}".format(0.94 ** (1 / 12)))
show("SMM from CPR 6% = 1 - 0.94^(1/12) (%)", "{!r} -> {:.4f}%".format(smm_we * 100, smm_we * 100))
show("trap: 6%/12 (%)", "{:.4f}".format(6 / 12))
show("check: CPR from that SMM (%)", "{:.4f}".format(cpr_from_smm(smm_we) * 100))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: callable bond, +/-25 bp; SMM 0.8% ===")
Q0, QM, QP, DQ = 100.10, 100.94, 99.24, 0.0025
gain = QM - Q0
loss = Q0 - QP
show("gain on the fall 100.94 - 100.10", "{:.2f}".format(gain))
show("loss on the rise 100.10 - 99.24", "{:.2f}".format(loss))
show("EffDur numerator 100.94 - 99.24", "{:.2f}".format(QM - QP))
show("EffDur denominator 2 x .0025 x 100.1", "{:.4f}".format(2 * DQ * Q0))
ED = eff_dur(QM, Q0, QP, DQ)
show("EffDur (STO 1 / ->D)", "{!r} -> {}".format(ED, both(ED)))
show("EffCon numerator 100.94 + 99.24 - 2 x 100.1", "{:.4f}".format(QM + QP - 2 * Q0))
show("PV- + PV+ = 100.94 + 99.24", "{:.2f}".format(QM + QP))
show(".0025 x^2 (BA shows 0.0000 at 4 decimals)", "{:.8f} -> display {}".format(DQ ** 2, d4(DQ ** 2)))
show("EffCon denominator .0025^2 x 100.1", "{:.9f} -> display {}".format(DQ ** 2 * Q0, d4(DQ ** 2 * Q0)))
EC = eff_con(QM, Q0, QP, DQ)
show("EffCon (STO 2 / ->C)", "{!r} -> {}".format(EC, both(EC)))
show("rounded-price check: EffCon with numerator -0.02 exactly", "{:.4f}".format(-0.02 / (DQ ** 2 * Q0)))
# estimates for +/-100 bp
dur_term = ED * 0.01
con_term = 0.5 * EC * 0.01 ** 2
show("duration term 3.3966 x 0.01", "{:.6f}".format(dur_term))
show("convexity term 1/2 x EffCon x 0.01^2", "{:.7f} -> {:.4f}%".format(con_term, con_term * 100))
UP = pct_change(ED, EC, 0.01)
DN = pct_change(ED, EC, -0.01)
show("+100 bp, decimal", "{!r} -> {}".format(UP, both(UP)))
show("+100 bp, percent (x 100)", "{} -> {:.2f}%".format(both(UP * 100), UP * 100))
show("-100 bp, decimal", "{!r} -> {}".format(DN, both(DN)))
show("-100 bp, percent (x 100)", "{} -> {:.2f}%".format(both(DN * 100), DN * 100))
show("duration alone, either way (%)", "{:.4f}".format(ED))
show("asymmetry: loss minus gain at 100 bp (points)", "{:.4f}".format(-UP * 100 - DN * 100))
# what +/-25 bp predicts vs the model prices
show("check +25 bp estimate x PV0 (price change)", "{:.4f}".format(pct_change(ED, EC, DQ) * Q0))
show("check -25 bp estimate x PV0 (price change)", "{:.4f}".format(pct_change(ED, EC, -DQ) * Q0))
# leverage illustration (illustrative only)
LEV = 5
show("illustration: 5x assets/equity, -3.5564% on assets -> equity loss %", "{:.4f} -> {:.1f}%".format(LEV * UP * 100, LEV * UP * 100))
show("illustration: 5x assets/equity, +3.2368% on assets -> equity gain %", "{:.4f} -> {:.1f}%".format(LEV * DN * 100, LEV * DN * 100))
# CPR from SMM
SMM = 0.008
surv = (1 - SMM) ** 12
CPR = cpr_from_smm(SMM)
show("survives each month 1 - 0.008", "{:.3f}".format(1 - SMM))
show(".992^12 (share surviving the year)", "{!r} -> {}".format(surv, both(surv)))
show("CPR = 1 - .992^12, decimal", "{!r} -> {}".format(CPR, both(CPR)))
show("CPR (%)", "{} -> {:.2f}%".format(both(CPR * 100), CPR * 100))
show("trap: 12 x 0.8%", "{:.2f}%".format(12 * 0.8))
show("trap overstates by (points)", "{:.4f}".format(12 * 0.8 - CPR * 100))

# ---------------------------------------------------------------------------
print("\n=== Why it works: key rate durations (illustration) ===")
KRD = {"2-year": 0.35, "5-year": 1.60, "10-year": 1.45}
show("sum of key rate durations 0.35 + 1.60 + 1.45", "{:.2f}".format(sum(KRD.values())))
show("only the 10-year par rate +50 bp: -1.45 x 0.005 (%)", "{:.4f}".format(-1.45 * 0.005 * 100))
show("whole curve +50 bp: -3.40 x 0.005 (%)", "{:.4f}".format(-sum(KRD.values()) * 0.005 * 100))
# PSA benchmark
show("100 PSA CPR, month 1 / month 10 / month 30+ (%)", "{:.1f} / {:.1f} / {:.1f}".format(0.2, 0.2 * 10, min(0.2 * 30, 6.0)))
show("150 PSA CPR from month 30 (%)", "{:.1f}".format(1.5 * 6.0))
show("months to reach 6% at +0.2%/month", "{:.0f}".format(6.0 / 0.2))

# ---------------------------------------------------------------------------
print("\n=== Back to the story ===")
show("callable: gain on -25 bp / loss on +25 bp", "{:.2f} / {:.2f}".format(gain, loss))
show("EffDur / EffCon", "{:.4f} / {:.2f}".format(ED, EC))
show("+100 bp / -100 bp (%)", "{:.2f} / {:+.2f}".format(UP * 100, DN * 100))

# ---------------------------------------------------------------------------
print("\n=== Check questions ===")
# c1: EffDur, +/-20 bp
C0, CM, CP, DC1 = 101.50, 102.38, 100.55, 0.002
c1 = eff_dur(CM, C0, CP, DC1)
show("c1 numerator 102.38 - 100.55", "{:.2f}".format(CM - CP))
show("c1 denominator 2 x .002 x 101.5", "{:.4f}".format(2 * DC1 * C0))
show("c1 EffDur (answer)", "{!r} -> {} -> {:.2f}".format(c1, both(c1), c1))
c1c = eff_con(CM, C0, CP, DC1)
show("c1 bonus: EffCon numerator 102.38 + 100.55 - 203", "{:.2f}".format(CM + CP - 2 * C0))
show("c1 bonus: EffCon denominator .002^2 x 101.5", "{:.6f}".format(DC1 ** 2 * C0))
show("c1 bonus: EffCon", "{!r} -> {}".format(c1c, both(c1c)))
show("c1 gain / loss for 20 bp", "{:.2f} / {:.2f}".format(CM - C0, C0 - CP))
show("c1 trap: forgot the 2 -> 1.83/(.002 x 101.5)", "{:.4f}".format((CM - CP) / (DC1 * C0)))
show("c1 trap: dC typed as 20 bp = .02 -> ", "{:.4f}".format((CM - CP) / (2 * 0.02 * C0)))

# c2: conceptual (no numbers)

# c3: EffDur 5.20, EffCon -80, +100 bp
D3, K3 = 5.20, -80.0
c3_up = pct_change(D3, K3, 0.01) * 100
c3_dn = pct_change(D3, K3, -0.01) * 100
show("c3 duration term -5.20 x 0.01 (%)", "{:.4f}".format(-D3 * 0.01 * 100))
show("c3 convexity term 1/2 x -80 x 0.01^2 (%)", "{:.4f}".format(0.5 * K3 * 0.01 ** 2 * 100))
show("c3 +100 bp (answer, %)", "{!r} -> {}".format(c3_up, both(c3_up)))
show("c3 -100 bp (%)", "{!r} -> {}".format(c3_dn, both(c3_dn)))
show("c3 trap: convexity sign flipped for a rise (%)", "{:.4f}".format((-D3 * 0.01 - 0.5 * K3 * 0.01 ** 2) * 100))
show("c3 trap: forgot the 1/2 (%)", "{:.4f}".format((-D3 * 0.01 + K3 * 0.01 ** 2) * 100))
show("c3 BA: 5.2 +|- x .01 + .5 x 80 +|- x .01 x^2 = (decimal)", "{}".format(both(c3_up / 100)))

# c4: SMM 0.6% -> CPR
S4 = 0.006
c4 = cpr_from_smm(S4) * 100
show("c4 .994^12", "{:.6f}".format((1 - S4) ** 12))
show("c4 CPR = 1 - .994^12 (answer B, %)", "{!r} -> {} -> {:.2f}%".format(c4, both(c4), c4))
show("c4 distractor A: 12 x 0.6", "{:.2f}%".format(12 * 0.6))
show("c4 distractor C: 1.006^12 - 1", "{:.4f} -> {:.2f}%".format(((1 + S4) ** 12 - 1) * 100, ((1 + S4) ** 12 - 1) * 100))
