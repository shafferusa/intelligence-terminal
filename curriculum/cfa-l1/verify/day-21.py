#!/usr/bin/env python3
"""Day 21 (review of Days 15-20): every number that appears on the page.

Review day: no story, no worked example. Two warm-up questions (Days 20 and 8)
and ten check questions (c1-c10) on bond prices, yields, periodicity, full/flat
price, money-market yields, repos, and the Gordon and two-stage DDMs.
Standard library only. Displays are shown to 4 decimals, as both calculators
are set up on Day 1.
"""


def pv_annuity_bond(n, r, pmt, fv):
    """Price (positive) of a bond: PMT each period for n periods plus FV at n, at periodic rate r."""
    return sum(pmt / (1 + r) ** t for t in range(1, n + 1)) + fv / (1 + r) ** n


def solve_rate(n, price, pmt, fv, lo=1e-9, hi=1.0):
    """Periodic rate r with pv_annuity_bond(n, r, pmt, fv) == price (bisection)."""
    for _ in range(300):
        mid = (lo + hi) / 2
        if pv_annuity_bond(n, mid, pmt, fv) > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def npv(rate, cfs):
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cfs))


def show(label, x, d=4):
    print("%-58s %s" % (label, format(x, ",.%df" % d)))


print("=== Warm-up ===")
# w1 (Day 20): terminal value. Last high-growth dividend D_4 = 3.00, then 5% forever, r = 10%.
D4w, gL, r = 3.00, 0.05, 0.10
D5w = D4w * (1 + gL)
V4 = D5w / (r - gL)
show("w1 D_5 = 3.00 x 1.05", D5w)
show("w1 V_4 = D_5/(r - g) = ANSWER", V4)
show("w1 slip: D_4/(r - g)", D4w / (r - gL))
show("w1 slip: D_5/r (forgot g)", D5w / r)

# w2 (Day 8): perpetuity of 4 a year, first payment end of year 3, r = 5%.
A, rp = 4.0, 0.05
val_t2 = A / rp
show("w2 A/r = value at t = 2", val_t2)
show("w2 1.05^2", 1.05 ** 2)
show("w2 PV0 = 80/1.05^2 = ANSWER (A)", val_t2 / 1.05 ** 2)
show("w2 B: discounted 3 periods 80/1.05^3", val_t2 / 1.05 ** 3)
show("w2 C: ignored deferral", val_t2)

print("\n=== Settings check: 2-year 10% semiannual bond at a 10% yield (N = 4, I/Y = 5, PMT = 5, FV = 100) ===")
show("check PV, END, P/Y = 1 (must be par)", pv_annuity_bond(4, 0.05, 5, 100))
r12 = 0.05 / 12
show("check PV if P/Y = C/Y = 12 (rate 5/12% a period)", pv_annuity_bond(4, r12, 5, 100))
pv_bgn = sum(5 / 1.05 ** t for t in range(0, 4)) + 100 / 1.05 ** 4
show("check PV if BGN is on", pv_bgn)

print("\n=== c1 (Day 15): 12-year 4.5% semiannual bond at a 5.2% yield ===")
c1 = pv_annuity_bond(24, 0.026, 2.25, 100)
show("c1 N = 24, I/Y = 2.6, PMT = 2.25, FV = 100 -> PV = ANSWER", c1)
show("c1 slip: annual inputs N = 12, I/Y = 5.2, PMT = 4.5", pv_annuity_bond(12, 0.052, 4.5, 100))
show("c1 slip: halved coupon only (N = 12, I/Y = 5.2, PMT = 2.25)", pv_annuity_bond(12, 0.052, 2.25, 100))
show("c1 slip: discounted at the coupon (I/Y = 2.25)", pv_annuity_bond(24, 0.0225, 2.25, 100))

print("\n=== c2 (Day 15): pull to par, 10-year 6% annual-pay at 5% ===")
p10 = pv_annuity_bond(10, 0.05, 6, 100)
p9 = pv_annuity_bond(9, 0.05, 6, 100)
show("c2 price now (N = 10)", p10)
show("c2 price in a year (N = 9) = ANSWER (B)", p9)
show("c2 check: 107.7217 x 1.05", p10 * 1.05)
show("c2 check: 107.7217 x 1.05 - 6", p10 * 1.05 - 6)
show("c2 premium now (price - 100)", p10 - 100)
show("c2 premium in a year", p9 - 100)
show("c2 premium shrinks by", p10 - p9)
show("c2 A: premium grows instead, p10 + (p10 - p9)", p10 + (p10 - p9))
show("c2 C: unchanged", p10)
show("c2 yield earned on the price: 5% x 107.7217", 0.05 * p10)
show("c2 coupon 6 minus yield earned", 6 - 0.05 * p10)
show("c2 next year it is larger: 6 - 5% x 107.1078", 6 - 0.05 * p9)
show("c2 extra: 11 years left (a year added)", pv_annuity_bond(11, 0.05, 6, 100))

print("\n=== c3 (Day 16): YTM of an 8-year 5% semiannual bond at 96.50 ===")
r3 = solve_rate(16, 96.50, 2.5, 100)
show("c3 CPT I/Y (semiannual %)", r3 * 100)
show("c3 YTM semiannual-bond basis = ANSWER (x 2)", r3 * 200)
show("c3 TI: displayed 2.7738 x 2", 2.7738 * 2)
show("c3 slip: not doubled", r3 * 100)
show("c3 1 + r (6 decimals, as on the page)", 1 + r3, 6)
show("c3 slip: (1 + r)^2 - 1 (effective annual)", ((1 + r3) ** 2 - 1) * 100)
show("c3 current yield 5/96.50 (not asked)", 5 / 96.50 * 100)

print("\n=== c4 (Day 16): 10-year 6.5% semiannual at 104.25, callable in 4 years at 101 ===")
ytm = solve_rate(20, 104.25, 3.25, 100)
ytc = solve_rate(8, 104.25, 3.25, 101)
show("c4 YTM: CPT I/Y (N = 20, FV = 100)", ytm * 100)
show("c4 YTM x 2", ytm * 200)
show("c4 YTC: CPT I/Y (N = 8, FV = 101)", ytc * 100)
show("c4 YTC x 2", ytc * 200)
show("c4 yield-to-worst = ANSWER", min(ytm, ytc) * 200)
ytc_par = solve_rate(8, 104.25, 3.25, 100)
show("c4 slip: YTC with FV = 100 (par, not call price)", ytc_par * 200)
show("c4 premium lost if called: 104.25 - 101", 104.25 - 101)
show("c4 premium lost at maturity: 104.25 - 100", 104.25 - 100)
show("c4 TI: displayed 2.9653 x 2", 2.9653 * 2)
show("c4 TI: displayed 2.7640 x 2", 2.7640 * 2)

print("\n=== c5 (Day 16): 5.80% annual-pay restated on a semiannual-bond basis ===")
c5 = 2 * (1.058 ** 0.5 - 1) * 100
show("c5 1.058^0.5", 1.058 ** 0.5)
show("c5 2 x (1.058^0.5 - 1) = ANSWER (C)", c5)
show("c5 B: wrong way (1.029^2 - 1)", (1.029 ** 2 - 1) * 100)
show("c5 A: no conversion", 5.80)
show("c5 1.058^0.5 - 1 (half-year rate, %)", (1.058 ** 0.5 - 1) * 100)
show("c5 B: 5.80/2 = 2.9 per half-year", 5.80 / 2)
show("c5 check: (1 + 5.7183%/2)^2 - 1", ((1 + c5 / 200) ** 2 - 1) * 100)

print("\n=== c6 (Day 17): 4% semiannual government bond at 4.6%, 6 coupons left, 62 of 182 days ===")
pv_last = pv_annuity_bond(6, 0.023, 2, 100)
tT = 62 / 182
full = pv_last * 1.023 ** tT
ai = tT * 2
flat = full - ai
show("c6 PV at last coupon (N = 6, I/Y = 2.3, PMT = 2)", pv_last)
show("c6 t/T = 62/182", tT)
show("c6 1.023^(62/182)", 1.023 ** tT)
full_typed = 98.3365 * 1.023 ** tT
show("c6 full price (exact PV)", full)
show("c6 full price from typed 98.3365", full_typed)
show("c6 AI = 62/182 x 2", ai)
show("c6 flat = full - AI = ANSWER", flat)
show("c6 flat from typed values", full_typed - ai)
show("c6 slip: grown at the annual yield 1.046^(62/182)", pv_last * 1.046 ** tT - ai)
show("c6 slip: full price reported", full)

print("\n=== c7 (Day 18): 120-day T-bill at a 3.90% discount rate ===")
price7 = 100 * (1 - 120 / 360 * 0.039)
bey = 365 / 120 * (100 - price7) / price7 * 100
aor360 = 360 / 120 * (100 - price7) / price7 * 100
show("c7 price per 100", price7)
show("c7 discount 100 - price", 100 - price7)
show("c7 BEY = 365/120 x 1.30/98.70 = ANSWER", bey)
show("c7 slip: 360-day add-on rate", aor360)
show("c7 slip: discount rate restated on 365 days", 3.90 * 365 / 360)

print("\n=== c8 (Day 18): 30-day repo of $25,000,000 at 4.40% ===")
show("c8 A: 25,000,000 x .044 x 30/360 = ANSWER (A)", 25_000_000 * 0.044 * 30 / 360, 2)
show("c8 display (4 decimals)", 25_000_000 * 0.044 * 30 / 360, 4)
show("c8 B: 365-day year", 25_000_000 * 0.044 * 30 / 365, 2)
show("c8 C: a full year", 25_000_000 * 0.044, 2)

print("\n=== c9 (Day 19): implied required return, P0 = 52.00, D0 = 1.95, g = 4% ===")
D1 = 1.95 * 1.04
show("c9 D1 = 1.95 x 1.04", D1)
show("c9 D1/P0", D1 / 52 * 100)
show("c9 r = D1/P0 + g = ANSWER (%)", (D1 / 52 + 0.04) * 100)
show("c9 slip: D0/P0 + g (%)", (1.95 / 52 + 0.04) * 100)
show("c9 check: D1/(r - g)", D1 / ((D1 / 52 + 0.04) - 0.04))

print("\n=== c10 (Day 20): D0 = 1.50, 18% for 3 years, then 5%; r = 11% ===")
d = [1.50]
for _ in range(3):
    d.append(d[-1] * 1.18)
D1_, D2_, D3_ = d[1], d[2], d[3]
D4_ = D3_ * 1.05
V3 = D4_ / (0.11 - 0.05)
show("c10 D1", D1_)
show("c10 D2", D2_)
show("c10 D3", D3_)
show("c10 D4 = D3 x 1.05", D4_)
show("c10 V3 = D4/0.06", V3)
show("c10 C03 = D3 + V3", D3_ + V3)
v0 = npv(0.11, [0, D1_, D2_, D3_ + V3])
show("c10 V0 = NPV at 11% = ANSWER (C)", v0)
show("c10 V0 from typed CFs 1.77, 2.0886, 45.5941", npv(0.11, [0, 1.77, 2.0886, 45.5941]))
pv_divs = npv(0.11, [0, D1_, D2_, D3_])
show("c10 PV of D1-D3 alone", pv_divs)
show("c10 PV of V3 alone (3 periods)", V3 / 1.11 ** 3)
show("c10 A: V3 discounted 4 periods", pv_divs + V3 / 1.11 ** 4)
V3_bad = D3_ / 0.06
show("c10 B: D3 on top -> V3", V3_bad)
show("c10 B: V0 with D3/(r - g)", pv_divs + V3_bad / 1.11 ** 3)
show("c10 terminal value share of V0 (%)", V3 / 1.11 ** 3 / v0 * 100, 0)
show("c6 typed path: 99.1012 - 0.6813", 99.1012 - 0.6813)
show("c9 check with r = 7.90%: 2.028/(0.079 - 0.04)", 2.028 / (0.079 - 0.04))

print("\n=== data-answer values (full precision) ===")
for k, v in [("w1", V4), ("c1 (signless)", c1), ("c3 (pct)", r3 * 200), ("c4 (pct)", min(ytm, ytc) * 200),
             ("c6", flat), ("c7 (pct)", bey), ("c9 (pct)", (D1 / 52 + 0.04) * 100)]:
    print("%-16s %r" % (k, v))
print("mc: w2 = A (72.56), c2 = B (107.11), c5 = C (5.72%), c8 = A ($91,666.67), c10 = C (36.63)")
