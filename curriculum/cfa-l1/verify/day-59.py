#!/usr/bin/env python3
"""Day 59 of CFA Math: every number that appears on day-59.html.

Option payoffs, profits and value: call payoff c_T = max(0, S_T - X), put
payoff p_T = max(0, X - S_T); profit = payoff - premium (the short's is the
negative); breakevens X + c_0 (call) and X - p_0 (put); intrinsic value and
time value; the European lower bounds c_0 >= max[0, S_0 - X/(1 + r)^T] and
p_0 >= max[0, X/(1 + r)^T - S_0]
(CFA Program 2027 Level I, Derivatives: Pricing and Valuation of Options).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
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


def call_payoff(s_t, x):
    return max(0.0, s_t - x)


def put_payoff(s_t, x):
    return max(0.0, x - s_t)


def call_lower_bound(s0, x, r, t):
    return max(0.0, s0 - x / (1 + r) ** t)


def put_lower_bound(s0, x, r, t):
    return max(0.0, x / (1 + r) ** t - s0)


def short_straddle_profit(s_t, x, c0, p0):
    payoff = -call_payoff(s_t, x) - put_payoff(s_t, x)
    return payoff, payoff + c0 + p0


# ---------------------------------------------------------------------------
print("=== Release date ===")
START = datetime.date(2026, 11, 16)
rel = START + datetime.timedelta(days=59 - 1)
show("Day 59 due on start_date + 58 days", "{} ({})".format(rel.isoformat(), rel.strftime("%A")))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
# w1 (Day 58): 2-year annual par swap rate from spot rates 2.5% and 3.0%
Z1, Z2 = 0.025, 0.030
df1 = 1 / (1 + Z1)
df2 = 1 / (1 + Z2) ** 2
sum_df = df1 + df2
swap = (1 - df2) / sum_df * 100
show("w1 DF1 = 1/1.025", "{!r} -> {}".format(df1, both(df1)))
show("w1 1.03^2", both((1 + Z2) ** 2))
show("w1 DF2 = 1/1.03^2", "{!r} -> {}".format(df2, both(df2)))
show("w1 1 - DF2", "{:.6f}".format(1 - df2))
show("w1 DF1 + DF2", "{:.6f} -> {:.4f}".format(sum_df, sum_df))
show("w1 par swap rate % (answer)", "{!r} -> {} -> {:.2f}%".format(swap, both(swap), swap))
show("w1 trap: average of spot rates", "{:.2f}%".format((Z1 + Z2) / 2 * 100))
show("w1 trap: the 2-year spot rate", "{:.2f}%".format(Z2 * 100))
show("w1 check: PV of fixed leg at s on 1 notional + PV(1 at T)",
     "{:.6f}".format(swap / 100 * sum_df + df2))

# w2 (Day 52): CAPM, beta 0.8, Rf 3%, E(Rm) 10%, forecast 9.5%
BETA, RF, RM, FCST = 0.8, 3.0, 10.0, 9.5
req = RF + BETA * (RM - RF)
show("w2 market risk premium 10 - 3", "{:.1f}%".format(RM - RF))
show("w2 C (right): required 3 + 0.8 x 7", "{:.2f}% -> alpha {:+.2f}%".format(req, FCST - req))
req_trap = RF + BETA * RM
show("w2 B: 3 + 0.8 x 10 (beta x market return)", "{:.2f}% -> alpha {:+.2f}%".format(req_trap, FCST - req_trap))
show("w2 A: forecast vs market return 10%", "{:+.2f}%".format(FCST - RM))

# ---------------------------------------------------------------------------
print("\n=== Story ===")
show("Barings age in 1995 (founded 1762)", 1995 - 1762)
show("days from 17 Jan to Monday 23 Jan 1995", (datetime.date(1995, 1, 23) - datetime.date(1995, 1, 17)).days)
show("23 January 1995 weekday", datetime.date(1995, 1, 23).strftime("%A"))
show("17 January 1995 weekday", datetime.date(1995, 1, 17).strftime("%A"))
show("26 February 1995 weekday", datetime.date(1995, 2, 26).strftime("%A"))
CLOSE, FALL = 17785, 1055
show("fall 1,055 to 17,785: % of prior close", "{:.2f}%".format(FALL / (CLOSE + FALL) * 100))
show("alt fall 1,025 to 17,785: %", "{:.2f}%".format(1025 / (CLOSE + 1025) * 100))

# ---------------------------------------------------------------------------
print("\n=== Worked example: call X 50 for 3.00, put X 45 for 2.00, S_T 41 ===")
XC, C0, XP, P0, ST = 50.0, 3.00, 45.0, 2.00, 41.0
cpay = call_payoff(ST, XC)
ppay = put_payoff(ST, XP)
show("call: 41 - 50", "{:.2f}".format(ST - XC))
show("call payoff max(0, 41 - 50)", "{:.2f}".format(cpay))
show("call profit 0 - 3.00", "{:.2f}".format(cpay - C0))
show("put: 45 - 41", "{:.2f}".format(XP - ST))
show("put payoff max(0, 45 - 41)", "{:.2f}".format(ppay))
show("put profit 4.00 - 2.00", "{:+.2f}".format(ppay - P0))
show("both together", "{:+.2f}".format(cpay - C0 + ppay - P0))
show("call breakeven X + c0", "{:.2f}".format(XC + C0))
show("put breakeven X - p0", "{:.2f}".format(XP - P0))
S_EARLY, C_EARLY = 52.0, 3.40
iv = call_payoff(S_EARLY, XC)
show("at S = 52: call intrinsic value max(0, 52 - 50)", "{:.2f}".format(iv))
show("time value 3.40 - 2.00", "{:.2f}".format(C_EARLY - iv))
show("max loss long call = premium", "{:.2f}".format(C0))
show("max gain long put = X - p0", "{:.2f}".format(XP - P0))
show("short put max loss X - p0 (c2 illustration)", "{:.2f}".format(XP - P0))
# trap 3: intrinsic value never negative
show("trap: call X 50, S 48: S - X", "{:.2f}".format(48 - 50))
show("trap: intrinsic value max(0, 48 - 50)", "{:.2f}".format(call_payoff(48, 50)))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: short straddle X 19,000, call 300, put 280, S_T 17,950 ===")
X, CW, PW, STW = 19000.0, 300.0, 280.0, 17950.0
prem = CW + PW
show("step 2: 17950 - 19000 (call: S_T - X)", both(STW - X))
show("call payoff", "{:.2f}".format(call_payoff(STW, X)))
show("step 3: 19000 - 17950 (put: X - S_T)", both(X - STW))
show("put payoff", "{:.2f}".format(put_payoff(STW, X)))
pay, prof = short_straddle_profit(STW, X, CW, PW)
show("step 4: seller's payoff -max(..) - max(..)", "{!r} -> {}".format(pay, both(pay)))
show("step 5: profit = payoff + 300 + 280 (answer)", "{!r} -> {}".format(prof, both(prof)))
show("total premium 300 + 280", "{:.0f}".format(prem))
show("step 6: lower breakeven 19000 - 580", both(X - prem))
show("step 6: upper breakeven 19000 + 580", both(X + prem))
show("max gain (S_T = X)", "{:.0f}".format(short_straddle_profit(X, X, CW, PW)[1]))
S0, R, T = 19200.0, 0.02, 0.25
pvx = X / (1 + R) ** T
lb = call_lower_bound(S0, X, R, T)
show("1.02^0.25", "{:.6f}".format((1 + R) ** T))
show("PV of strike 19000/1.02^.25", "{:.4f}".format(pvx))
show("step 7: call lower bound 19200 - 19000/1.02^.25", "{!r} -> {} -> {:.2f}".format(lb, both(lb), lb))
show("intrinsic value at 19,200: max(0, 19200 - 19000)", "{:.2f}".format(call_payoff(S0, X)))
show("bound minus intrinsic value", "{:.2f}".format(lb - call_payoff(S0, X)))
show("put lower bound at the same inputs: 19000/1.02^.25 - 19200", "{:.4f} -> max(0, .) = {:.2f}".format(
    pvx - S0, put_lower_bound(S0, X, R, T)))
show("trap: AOS off / chain: (19200 - 19000)/1.02^.25", "{:.4f}".format((S0 - X) / (1 + R) ** T))

# ---------------------------------------------------------------------------
print("\n=== Back to the story: the same short straddle at other closes ===")
for s in (19000, 18420, 19580, 18000, 17950, 17949, 17785, 0):
    pay_s, prof_s = short_straddle_profit(float(s), X, CW, PW)
    show("S_T = {:,}: payoff, profit".format(s), "{:,.2f}, {:,.2f}".format(pay_s, prof_s))
show("worst case below: S_T = 0, loss = X - premium", "{:,.0f}".format(X - prem))
show("each point beyond a breakeven costs", "{:.0f}".format(
    short_straddle_profit(17949.0, X, CW, PW)[1] - short_straddle_profit(17950.0, X, CW, PW)[1]))

# ---------------------------------------------------------------------------
print("\n=== Check questions ===")
# c1: short straddle X 2,400, call 95, put 85, S_T 2,610
X1, C1, P1, S1 = 2400.0, 95.0, 85.0, 2610.0
pay1, prof1 = short_straddle_profit(S1, X1, C1, P1)
show("c1 2610 - 2400 (call in the money)", both(S1 - X1))
show("c1 2400 - 2610 (put out of the money)", both(X1 - S1))
show("c1 seller's payoff", both(pay1))
show("c1 premiums 95 + 85", "{:.0f}".format(C1 + P1))
show("c1 profit (answer)", "{!r} -> {} -> {:.2f}".format(prof1, both(prof1), prof1))
show("c1 BA: 95 + 85 - 210", both(C1 + P1 - (S1 - X1)))
show("c1 breakevens", "{:,.0f} and {:,.0f}".format(X1 - C1 - P1, X1 + C1 + P1))
show("c1 trap: forgot premium", "{:.2f}".format(pay1))
show("c1 trap: one premium only (95)", "{:.2f}".format(pay1 + C1))

# c2: conceptual; numbers used in the explanation
show("c2 short put X 45, p0 2: max loss 45 - 2", "{:.2f}".format(45 - 2.0))

# c3: European put lower bound X 60, S0 52, r 4%, T 0.75
X3, S3, R3, T3 = 60.0, 52.0, 0.04, 0.75
pv3 = X3 / (1 + R3) ** T3
lb3 = put_lower_bound(S3, X3, R3, T3)
show("c3 1.04^0.75", "{:.6f}".format((1 + R3) ** T3))
show("c3 PV of strike 60/1.04^.75", "{:.4f}".format(pv3))
show("c3 put lower bound (answer)", "{!r} -> {} -> {:.2f}".format(lb3, both(lb3), lb3))
show("c3 trap: strike not discounted (intrinsic value)", "{:.2f}".format(X3 - S3))
show("c3 trap: T typed as 9", "{:.4f}".format(X3 / (1 + R3) ** 9 - S3))
show("c3 trap: discounted with 4% x 0.75 simple: 60/1.03 - 52", "{:.4f}".format(X3 / (1 + R3 * T3) - S3))

# c4: long put X 40 for 3.50, S_T 34
X4, P4, S4 = 40.0, 3.50, 34.0
show("c4 A (right): max(0, 40 - 34) - 3.50", "{:.2f}".format(put_payoff(S4, X4) - P4))
show("c4 B: forgot premium", "{:.2f}".format(put_payoff(S4, X4)))
show("c4 C: call payoff max(0, 34 - 40) - 3.50", "{:.2f}".format(call_payoff(S4, X4) - P4))
show("c4 breakeven X - p0", "{:.2f}".format(X4 - P4))

# c5: reuses the walkthrough straddle
show("c5 reuse: breakevens", "{:,.0f} / {:,.0f}".format(X - prem, X + prem))
show("c5 reuse: max gain", "{:.0f}".format(prem))
