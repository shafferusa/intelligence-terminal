#!/usr/bin/env python3
"""Day 58 of CFA Math: every number that appears on day-58.html.

FRAs and interest rate swaps. An FRA's fixed rate is the implied forward rate,
1 + FRA x d/360 = (1 + MRR_long x D_long/360) / (1 + MRR_short x D_short/360);
it settles at the START of the period, so the interest difference is discounted:
Notional x (MRR - FRA) x (d/360) / (1 + MRR x d/360). A swap is a bundle of
FRAs at one fixed rate; its par rate s_N = (1 - DF_N) / sum DF_i makes the fixed
and floating legs worth the same; later its value to the fixed receiver is
(s_old - s_new) x sum DF_new x notional
(CFA Program 2027 Level I, Derivatives: Pricing and Valuation of Interest Rate
and Other Swaps; FRAs from Pricing and Valuation of Forward Contracts and for
an Underlying with Varying Maturities).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from decimal import Decimal, ROUND_HALF_UP


def show(label, value):
    print("  {:<72} {}".format(label, value))


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


def dfs(spots):
    """Discount factors from annual spot rates (decimals): DF_i = 1/(1+Z_i)^i."""
    return [1 / (1 + z) ** (i + 1) for i, z in enumerate(spots)]


def par_swap(spots):
    d = dfs(spots)
    return (1 - d[-1]) / sum(d), d


def fra_rate(r_short, d_short, r_long, d_long):
    """Implied forward (FRA) rate, money-market simple rates, 360-day year."""
    g_long = 1 + r_long * d_long / 360
    g_short = 1 + r_short * d_short / 360
    d = d_long - d_short
    return (g_long / g_short - 1) * 360 / d, g_long, g_short


def fra_settle(notional, mrr, fra, days):
    """Paid at the start of the period: the end-of-period difference, discounted."""
    end = notional * (mrr - fra) * days / 360
    return end / (1 + mrr * days / 360), end


# ---------------------------------------------------------------------------
print("=== Release date ===")
START = datetime.date(2026, 11, 16)
rel = START + datetime.timedelta(days=58 - 1)
show("Day 58 due on start_date + 57 days", "{} ({})".format(rel.isoformat(), rel.strftime("%A")))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
# w1 (Day 57): value of a long forward. Agreed at 1,000 for delivery in 1 year;
# six months later the forward price for the same date is 1,030; r = 4%.
F0_W, FT_W, R_W, LEFT_W = 1000.0, 1030.0, 0.04, 0.5
chg_w = FT_W - F0_W
v_w = chg_w / (1 + R_W) ** LEFT_W
show("w1 F_t - F_0", both(chg_w))
show("w1 1.04^0.5", "{:.6f}".format((1 + R_W) ** LEFT_W))
show("w1 value = 30 / 1.04^0.5 (answer)", "{!r} -> {} -> {:.2f}".format(v_w, both(v_w), v_w))
show("w1 trap: not discounted", "{:.2f}".format(chg_w))
show("w1 trap: discounted over the full year", "{:.2f}".format(chg_w / (1 + R_W)))
show("w1 value to the short", "{:.2f}".format(-v_w))

# w2 (Day 51): log-lin slope b1 = 0.12 -> doubling time
B1 = 0.12
dbl = math.log(2) / B1
dbl_disc = math.log(2) / math.log(1 + B1)
show("w2 ln 2", "{:.6f}".format(math.log(2)))
show("w2 C (right): ln 2 / 0.12", "{:.4f} -> {:.2f}".format(dbl, dbl))
show("w2 B: ln 2 / ln 1.12 (treats b1 as annual compound rate)", "{:.4f} -> {:.2f}".format(dbl_disc, dbl_disc))
show("w2 A: 1 / 0.12 (forgot ln 2)", "{:.4f} -> {:.2f}".format(1 / B1, 1 / B1))
show("w2 annual growth e^0.12 - 1 (%)", "{:.4f} -> {:.2f}%".format((math.exp(B1) - 1) * 100, (math.exp(B1) - 1) * 100))
show("w2 check e^(0.12 x 5.7762)", "{:.4f}".format(math.exp(B1 * dbl)))

# ---------------------------------------------------------------------------
print("\n=== Story: Hammersmith and Fulham ===")
NOTIONAL_HF, DEBT_HF = 6052.0, 390.0
show("notional / borrowing = 6,052 / 390", "{:.4f} -> {:.1f}".format(NOTIONAL_HF / DEBT_HF, NOTIONAL_HF / DEBT_HF))
show("base rates 7.5% -> 15%: ratio", "{:.1f}".format(15 / 7.5))
show("base rates rise, percentage points", "{:.1f}".format(15 - 7.5))

# ---------------------------------------------------------------------------
print("\n=== Why it works: FRA from the worked example, forwards on the walkthrough curve ===")
# Worked example numbers reused in steps 1-2
f_we, g180, g90 = fra_rate(0.030, 90, 0.034, 180)
show("1 + 0.034 x 180/360", "{:.6f}".format(g180))
show("1 + 0.030 x 90/360", "{:.6f}".format(g90))
show("ratio 1.017/1.0075", "{:.6f}".format(g180 / g90))
show("FRA rate (%)", "{:.6f} -> {:.4f}% -> {:.2f}%".format(f_we * 100, f_we * 100, f_we * 100))
show("check: 1.0075 x (1 + 0.037717 x 90/360)", "{:.6f}".format(g90 * (1 + f_we * 90 / 360)))
# Walkthrough curve: forwards and the telescoping floating leg
SPOT_WT = [0.030, 0.035, 0.040]
s_wt, df_wt = par_swap(SPOT_WT)
fw = []
prev = 1.0
for i, d in enumerate(df_wt):
    f = prev / d - 1
    fw.append(f)
    show("forward f_{} = DF_{}/DF_{} - 1 (%)".format(i + 1, i, i + 1), "{:.6f} -> {:.4f}% -> {:.2f}%".format(f * 100, f * 100, f * 100))
    show("f_{} x DF_{} = DF_{} - DF_{}".format(i + 1, i + 1, i, i + 1), "{:.6f} vs {:.6f} -> {:.4f}".format(f * d, prev - d, f * d))
    prev = d
show("sum f_i x DF_i (floating leg per 1)", "{:.6f} -> {:.4f}".format(sum(f * d for f, d in zip(fw, df_wt)), sum(f * d for f, d in zip(fw, df_wt))))
show("1 - DF_3", "{:.6f} -> {:.4f}".format(1 - df_wt[-1], 1 - df_wt[-1]))
show("fixed leg at s: s x sum DF", "{:.6f}".format(s_wt * sum(df_wt)))
show("par bond check: s x sum DF + DF_3", "{:.6f}".format(s_wt * sum(df_wt) + df_wt[-1]))

# ---------------------------------------------------------------------------
print("\n=== Worked example: 3x6 FRA, MRR 90d 3.0%, 180d 3.4%; quote 3.77%; MRR sets at 4.5%; $20m ===")
NOT_WE, QUOTE, MRR_SET = 20_000_000.0, 0.0377, 0.045
pay, end = fra_settle(NOT_WE, MRR_SET, QUOTE, 90)
show("rate gap 0.045 - 0.0377", "{:.4f}".format(MRR_SET - QUOTE))
show("end-of-period difference 20m x 0.0073 x 90/360", "{:.2f}".format(end))
show("discount factor base 1 + 0.045 x 90/360", "{:.6f}".format(1 + MRR_SET * 90 / 360))
show("paid at start = 36,500 / 1.01125 (answer)", "{!r} -> {:,.2f}".format(pay, pay))
show("discounting reduces it by", "{:,.2f}".format(end - pay))
pay_low, end_low = fra_settle(NOT_WE, 0.030, QUOTE, 90)
show("if MRR had set at 3.0%: long receives (negative = pays)", "{:,.2f} (end {:,.2f})".format(pay_low, end_low))
pay_full, _ = fra_settle(NOT_WE, MRR_SET, f_we, 90)
show("with the unrounded FRA 3.7717%", "{:,.2f}".format(pay_full))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: spots 3.0, 3.5, 4.0 -> DFs and 3-year par swap rate ===")
for i, d in enumerate(df_wt):
    show("DF_{} = 1/(1 + Z_{})^{}".format(i + 1, i + 1, i + 1), "{!r} -> {}".format(d, both(d)))
show("1.035^2", "{:.6f}".format(1.035 ** 2))
show("1.04^3", "{:.6f}".format(1.04 ** 3))
sdf = sum(df_wt)
show("sum DF (STO 4 / D)", "{!r} -> {}".format(sdf, both(sdf)))
show("1 - DF3", "{!r} -> {}".format(1 - df_wt[-1], both(1 - df_wt[-1])))
show("swap rate % (answer)", "{!r} -> {}".format(s_wt * 100, both(s_wt * 100)))
show("trap: average of spot rates (%)", "{:.4f}".format(sum(SPOT_WT) / 3 * 100))
show("trap: 3-year spot (%)", "{:.4f}".format(SPOT_WT[-1] * 100))
# Using rounded displays instead of stored values
r4 = [float(calc_round(d)) for d in df_wt]
show("retyping rounded DFs: (1 - 0.8890)/(sum rounded) %", "{:.4f}".format((1 - r4[-1]) / sum(r4) * 100))

# ---------------------------------------------------------------------------
print("\n=== Back to the story: illustrative receive-fixed swap ===")
S_OLD, S_NEW, N_ILL, NOT_ILL = 0.09, 0.12, 5, 100.0  # GBP millions
spots_flat = [S_NEW] * N_ILL
s_flat, df_flat = par_swap(spots_flat)
af = sum(df_flat)
v_ill = (S_OLD - S_NEW) * af * NOT_ILL
show("flat 12% curve: par swap rate (%)", "{:.6f}".format(s_flat * 100))
show("sum DF at 12% for 5 years", "{:.6f} -> {:.4f}".format(af, af))
show("annuity check (1 - 1.12^-5)/0.12", "{:.6f}".format((1 - 1.12 ** -5) / 0.12))
show("value to receiver (GBP m)", "{:.6f} -> {:.2f}".format(v_ill, v_ill))
show("annual shortfall 3% x 100m (GBP m)", "{:.2f}".format((S_NEW - S_OLD) * NOT_ILL))
show("undiscounted 5 x 3m", "{:.2f}".format((S_NEW - S_OLD) * NOT_ILL * N_ILL))
show("value to payer (GBP m)", "{:.2f}".format(-v_ill))
# same loss scaled to the council's notional, for scale only (illustrative)
show("same per-100m loss as % of notional", "{:.4f}%".format(v_ill / NOT_ILL * 100))
# value at inception: 9% flat curve
s9, df9 = par_swap([S_OLD] * N_ILL)
show("at a 9% flat curve the par rate is (%)", "{:.6f}".format(s9 * 100))
show("value at inception (9% vs 9%)", "{:.6f}".format((S_OLD - s9) * sum(df9) * NOT_ILL))

# ---------------------------------------------------------------------------
print("\n=== Check questions ===")
# c1: 4-year par swap rate
SPOT_C1 = [0.020, 0.026, 0.031, 0.035]
s1, df1 = par_swap(SPOT_C1)
for i, d in enumerate(df1):
    show("c1 DF_{}".format(i + 1), "{!r} -> {}".format(d, both(d)))
show("c1 sum DF", "{!r} -> {}".format(sum(df1), both(sum(df1))))
show("c1 1 - DF4", "{!r} -> {}".format(1 - df1[-1], both(1 - df1[-1])))
show("c1 swap rate % (answer)", "{!r} -> {} -> {:.2f}%".format(s1 * 100, both(s1 * 100), s1 * 100))
show("c1 trap: average spot", "{:.4f}".format(sum(SPOT_C1) / 4 * 100))

# c2: conceptual; reuse walkthrough floating-leg value
show("c2 floating leg per 1 of notional (walkthrough)", "{:.4f}".format(1 - df_wt[-1]))

# c3: receive 4% fixed, 2 years left, spots 4.5%, 5.0%, notional 100
SPOT_C3 = [0.045, 0.050]
s3, df3 = par_swap(SPOT_C3)
v3 = (0.04 - s3) * sum(df3) * 100
show("c3 DF1, DF2", "{:.6f}, {:.6f} -> {} ; {}".format(df3[0], df3[1], both(df3[0]), both(df3[1])))
show("c3 sum DF", "{:.6f} -> {}".format(sum(df3), both(sum(df3))))
show("c3 new par rate %", "{!r} -> {} -> {:.4f}%".format(s3 * 100, both(s3 * 100), s3 * 100))
show("c3 value to fixed receiver (answer)", "{!r} -> {} -> {:.2f}".format(v3, both(v3), v3))
fixed_bond = 4 * df3[0] + 104 * df3[1]
show("c3 cross-check: 4% bond on new DFs", "{:.4f}; minus 100 = {:.4f}".format(fixed_bond, fixed_bond - 100))
show("c3 fixed leg 0.04 x sum DF x 100", "{:.4f}".format(0.04 * sum(df3) * 100))
show("c3 floating leg (1 - DF2) x 100", "{:.4f}".format((1 - df3[1]) * 100))
show("c3 trap: no discounting 2 x (0.04 - s) x 100", "{:.4f}".format(2 * (0.04 - s3) * 100))
show("c3 trap: payer's sign", "{:.4f}".format(-v3))
show("c3 trap: 2-year spot 5% as the new rate: (0.04-0.05) x sumDF x 100", "{:.4f}".format((0.04 - 0.05) * sum(df3) * 100))

# c4: long 3x6 FRA at 2.8% on $50m, MRR sets at 3.3%
pay4, end4 = fra_settle(50_000_000.0, 0.033, 0.028, 90)
no_dc = 50_000_000.0 * 0.005 / (1 + 0.033)
show("c4 A: not discounted", "{:,.2f}".format(end4))
show("c4 B: right, discounted (answer)", "{!r} -> {:,.2f}".format(pay4, pay4))
show("c4 C: 90/360 left out everywhere: 250,000/1.033", "{:,.2f}".format(no_dc))
show("c4 1 + 0.033 x 90/360", "{:.6f}".format(1 + 0.033 * 90 / 360))
show("c4 BA/TI display", both(pay4))

# c5: reuses the story numbers
show("c5 reuse: notional / borrowing", "{:.1f}".format(NOTIONAL_HF / DEBT_HF))
show("c5 reuse: illustrative value (GBP m)", "{:.2f}".format(v_ill))
