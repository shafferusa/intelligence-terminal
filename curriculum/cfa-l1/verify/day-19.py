#!/usr/bin/env python3
"""Day 19 of CFA Math: every number that appears on day-19.html.

Constant-growth dividend discount model (CFA Program 2027 Level I, Equity
Investments: Equity Valuation: Concepts and Basic Tools; required return and
implied growth of equity from Quantitative Methods LM4).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def gordon(d0, g, r):
    """V_0 = D_1 / (r - g) with D_1 = D_0 (1 + g); rates as decimals."""
    return d0 * (1 + g) / (r - g)


def implied_r(d1, p0, g):
    """r = D_1 / P_0 + g."""
    return d1 / p0 + g


def implied_g_from_d0(r, p0, d0):
    """g = (r P_0 - D_0) / (P_0 + D_0), from P_0 = D_0 (1 + g) / (r - g)."""
    return (r * p0 - d0) / (p0 + d0)


def tbill_price(days, dr, year=360):
    return 100 * (1 - days / year * dr)


def show(label, value):
    print("  {:<64} {}".format(label, value))


print("=== Warm-up w1 (from Day 18): 182-day T-bill at a 4.60% discount rate ===")
show("182/360", "{:.6f}".format(182 / 360))
show("182/360 x 0.046", "{:.6f}".format(182 / 360 * 0.046))
w1 = tbill_price(182, 0.046)
show("price per 100 (answer)", "{!r} -> {:.4f} -> {:.2f}".format(w1, w1, w1))
w1_365 = tbill_price(182, 0.046, 365)
show("wrong: 365-day year", "{:.4f} -> {:.2f}".format(w1_365, w1_365))
show("gap 365 vs 360 (must exceed tol 0.01)", "{:.4f}".format(w1_365 - w1))
w1_addon = 100 / (1 + 182 / 360 * 0.046)
show("wrong: treated as add-on rate", "{:.4f} -> {:.2f}".format(w1_addon, w1_addon))

print("\n=== Warm-up w2 (from Day 3): nominal 9%, inflation 3%: exact real ===")
real = 1.09 / 1.03 - 1
show("1.09 / 1.03", "{:.6f}".format(1.09 / 1.03))
show("A (correct) exact real", "{!r} -> {:.4f} -> {:.2f}%".format(real, real, 100 * real))
show("B shortcut 9 - 3", "{:.2f}%".format(9 - 3))
prod = 1.09 * 1.03 - 1
show("C product 1.09 x 1.03 - 1", "{!r} -> {:.2f}%".format(100 * prod, 100 * prod))
show("display x100", "{:.4f}".format(100 * real))

print("\n=== Story: Shell, 30 April 2020 ===")
old_q, new_q = 0.47, 0.16
show("quarterly 0.47 -> 0.16: fraction kept", "{:.6f}".format(new_q / old_q))
show("cut as % of old", "{:.4f}% -> {:.2f}%".format(100 * (1 - new_q / old_q), 100 * (1 - new_q / old_q)))
show("two-thirds of 0.47 left", "{:.4f}".format(old_q / 3))
d0_old = 4 * old_q
d0_new = 4 * new_q
show("old annual D_0 = 4 x 0.47", "{:.2f}".format(d0_old))
show("new annual D_0 = 4 x 0.16", "{:.2f}".format(d0_new))
show("ADS dividend 0.94 -> 0.32 = 2 x quarterly", "{:.2f} -> {:.2f}".format(2 * old_q, 2 * new_q))

print("\n=== The idea: r = 8% required, g = 3% from growth, dividend covers the rest ===")
r, g = 0.08, 0.03
d1_old = d0_old * (1 + g)
show("r - g", "{:.2f} ({:.0f}%)".format(r - g, 100 * (r - g)))
show("D_1 = 1.88 x 1.03", "{!r} -> {:.4f}".format(d1_old, d1_old))
v_old = d1_old / (r - g)
show("V_0 = 1.9364 / 0.05", "{!r} -> {:.4f} -> {}".format(v_old, v_old, money(v_old)))
show("dividend yield D_1 / V_0", "{:.4f}%".format(100 * d1_old / v_old))
show("yield + growth", "{:.4f}%".format(100 * (d1_old / v_old + g)))

print("\n=== Why it works: series ratio, D_0 test, and a one-year check of V_0 = (D_1 + V_1)/(1 + r) ===")
print("  (the V_1 / D_1 + V_1 lines are a sanity check of steps 1-2; the verifier cut them from the page for length)")
v1 = v_old * (1 + g)
show("V_1 = 38.7280 x 1.03", "{!r} -> {:.4f}".format(v1, v1))
show("D_1 + V_1", "{:.4f}".format(d1_old + v1))
show("(D_1 + V_1) / 1.08", "{:.4f}".format((d1_old + v1) / (1 + r)))
show("ratio of successive PV terms (1+g)/(1+r) = 1.03/1.08", "{:.4f}".format((1 + g) / (1 + r)))
terms = [d1_old * (1 + g) ** (t - 1) / (1 + r) ** t for t in range(1, 6)]
show("first five PV terms", ", ".join("{:.4f}".format(x) for x in terms))
partial = sum(d1_old * (1 + g) ** (t - 1) / (1 + r) ** t for t in range(1, 2001))
show("sum of 2,000 PV terms (converges to V_0)", "{:.4f}".format(partial))
show("g = 0 -> D/r: 1.88 / 0.08 (level dividend)", "{:.2f}".format(d0_old / r))
g_back = implied_g_from_d0(r, v_old, d0_old)
show("g from D_0 formula at P_0 = 38.728", "{!r} -> {:.4f}%".format(g_back, 100 * g_back))
show("r x P_0 = 0.08 x 38.728", "{:.5f}".format(r * v_old))
show("P_0 + D_0 = 38.728 + 1.88", "{:.3f}".format(v_old + d0_old))
show("r from yield+growth at P_0 = 38.728", "{:.4f}%".format(100 * implied_r(d1_old, v_old, g)))

print("\n=== Worked example: D_0 = 2.00, g = 4%, r = 10%; P_0 = 40; preferred 3.00 at 7.5% ===")
d1_we = 2.00 * 1.04
show("(a) D_1", "{:.2f}".format(d1_we))
show("(a) r - g", "{:.2f}".format(0.10 - 0.04))
v_we = d1_we / (0.10 - 0.04)
show("(a) V_0", "{!r} -> {:.4f} -> {:.2f}".format(v_we, v_we, v_we))
show("(b) D_1 / P_0 = 2.08 / 40", "{:.4f}".format(d1_we / 40))
r_we = implied_r(d1_we, 40, 0.04)
show("(b) r", "{!r} -> {:.2f}%".format(r_we, 100 * r_we))
show("(c) 3.00 / 0.075", "{:.2f}".format(3.00 / 0.075))
show("(b) price 40 vs value 34.67: gap", "{:.2f}".format(40 - v_we))

print("\n=== Walkthrough: D_0 = 1.88, g = 3%, r = 8%; cut to 0.64; P_0 = 25; preferred 5 at 6% ===")
show("step 2: D_1 (display)", d4(d1_old))
show("step 3: V_0 (display)", d4(v_old))
show("step 3 slip: no brackets, 1.9364 / .08 - .03 (AOS)", d4(d1_old / 0.08 - 0.03))
d1_new = d0_new * (1 + g)
v_new = d1_new / (r - g)
show("step 4: D_1 after cut = 0.64 x 1.03", "{!r} -> {:.4f}".format(d1_new, d1_new))
show("step 4: V_0 after cut (display)", "{!r} -> {} -> {}".format(v_new, d4(v_new), money(v_new)))
show("step 4: V_new / V_old", "{:.6f}".format(v_new / v_old))
show("step 4: same as 0.64 / 1.88", "{:.6f}".format(d0_new / d0_old))
show("step 4: fall in value", "{:.4f}% -> {:.0f}%".format(100 * (1 - v_new / v_old), 100 * (1 - v_new / v_old)))
show("step 4: dollar fall", money(v_old - v_new))
num = r * 25 - d0_old
den = 25 + d0_old
show("step 5: r x P_0 - D_0 = 0.08 x 25 - 1.88", "{:.4f}".format(num))
show("step 5: P_0 + D_0 = 25 + 1.88", "{:.2f}".format(den))
g25 = implied_g_from_d0(r, 25, d0_old)
show("step 5: implied g", "{!r} -> {:.6f} -> {:.4f}% -> {:.2f}%".format(g25, g25, 100 * g25, 100 * g25))
show("step 5 display (BA / TI)", "{:.4f} / .{}".format(100 * g25, "{:.4f}".format(100 * g25).split(".")[1]))
d1_25 = d0_old * (1 + g25)
show("check: D_1 = 1.88 x (1 + g)", "{:.6f} -> {:.4f}".format(d1_25, d1_25))
show("check: D_1 / 25", "{:.6f} -> {:.4f}%".format(d1_25 / 25, 100 * d1_25 / 25))
show("check: r - D_1/P_0", "{:.4f}%".format(100 * (r - d1_25 / 25)))
show("check: Gordon value at g -> back to 25", "{:.4f}".format(gordon(d0_old, g25, r)))
show("wrong shortcut: r - D_0/P_0", "{:.4f}%".format(100 * (r - d0_old / 25)))
show("old dividend yield at 25: 1.88/25", "{:.4f}%".format(100 * d0_old / 25))
show("hypothetical 25 vs old-dividend value 38.73: % below", "{:.2f}%".format(100 * (1 - 25 / v_old)))
pref = 5 / 0.06
show("step 6: preferred 5 / 0.06", "{!r} -> {} -> {}".format(pref, d4(pref), money(pref)))

print("\n=== Back to the story ===")
show("value falls 38.73 -> 13.18 (%)", "{:.2f}%".format(100 * (1 - v_new / v_old)))
show("7% fall from 38.73 would leave", money(v_old * 0.93))
show("14% fall from 38.73 would leave", money(v_old * 0.86))

print("\n=== Exam traps ===")
trap1 = d0_old / (r - g)
show("D_0 in numerator: 1.88 / 0.05", "{:.4f} -> {:.2f}".format(trap1, trap1))
show("understatement vs 38.73", "{:.2f}".format(v_old - trap1))
g9 = 0.09
d1_g9 = d0_old * (1 + g9)
show("g = 9% > r = 8%: D_1 = 1.88 x 1.09", "{:.4f}".format(d1_g9))
show("g = 9%: D_1 / (0.08 - 0.09)", "{:.2f}".format(d1_g9 / (r - g9)))
show("preferred at common's 8%: 5 / 0.08", "{:.2f}".format(5 / 0.08))

print("\n=== Check c1: D_0 = 3.10, g = 2.5%, r = 7.5% ===")
d1_c1 = 3.10 * 1.025
show("D_1 = 3.10 x 1.025", "{!r} -> {:.4f}".format(d1_c1, d1_c1))
show("r - g", "{:.3f}".format(0.075 - 0.025))
c1 = d1_c1 / (0.075 - 0.025)
show("V_0 (answer)", "{!r} -> {} -> {}".format(c1, d4(c1), money(c1)))
show("wrong: D_0 used", money(3.10 / 0.05))
show("wrong: g left out of denominator", money(d1_c1 / 0.075))
show("D_1 rounded to 3.18 -> V_0 (tolerance check)", "{:.2f}".format(3.18 / 0.05))

print("\n=== Check c2: ex-dividend, D = 0.50 (no arithmetic) ===")
show("dividend", "{:.2f}".format(0.50))

print("\n=== Check c3: P_0 = 50, D_0 = 1.50, g = 6% -> implied r ===")
d1_c3 = 1.50 * 1.06
show("D_1 = 1.50 x 1.06", "{!r} -> {:.2f}".format(d1_c3, d1_c3))
show("D_1 / P_0", "{:.4f} ({:.2f}%)".format(d1_c3 / 50, 100 * d1_c3 / 50))
c3 = implied_r(d1_c3, 50, 0.06)
show("r (answer, percent)", "{!r} -> {:.4f} -> {:.2f}%".format(100 * c3, 100 * c3, 100 * c3))
show("display before x100 (BA / TI)", "{:.4f}".format(c3))
show("wrong: D_0 used: 1.50/50 + 6%", "{:.2f}%".format(100 * (1.50 / 50 + 0.06)))
show("wrong: growth left out: 1.59/50", "{:.2f}%".format(100 * d1_c3 / 50))
show("check: Gordon value at r = 9.18% -> 50", "{:.4f}".format(gordon(1.50, 0.06, c3)))

print("\n=== Check c4: D_0 = 1.20, g = 5%, r = 11% ===")
d1_c4 = 1.20 * 1.05
show("D_1 = 1.20 x 1.05", "{!r} -> {:.2f}".format(d1_c4, d1_c4))
show("r - g", "{:.2f}".format(0.11 - 0.05))
c4b = d1_c4 / (0.11 - 0.05)
show("B (correct) 1.26 / 0.06", "{!r} -> {:.4f} -> {:.2f}".format(c4b, c4b, c4b))
c4a = 1.20 / (0.11 - 0.05)
show("A D_0 used: 1.20 / 0.06", "{:.4f} -> {:.2f}".format(c4a, c4a))
c4c = d1_c4 / 0.11
show("C growth left out: 1.26 / 0.11", "{:.4f} -> {:.2f}".format(c4c, c4c))

print("\n=== Check c5 (explain): ratio example for r > g ===")
show("1.05/1.08 (r > g: shrink)", "{:.4f}".format(1.05 / 1.08))
show("1.08/1.08 (g = r: no shrink)", "{:.4f}".format(1.08 / 1.08))
