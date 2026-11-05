#!/usr/bin/env python3
"""Day 20 of CFA Math: every number that appears on day-20.html.

Two-stage dividend discount model (CFA Program 2027 Level I, Equity
Investments: Equity Valuation: Concepts and Basic Tools).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""


from decimal import Decimal, ROUND_HALF_UP


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators).

    Both calculators work in decimal and round half up, so 1.82505 shows as
    1.8251 even though binary floating point stores 1.8250499...; round to 12
    significant digits first, then half up, as the calculators do.
    """
    q = Decimal(repr(float("{:.12g}".format(x)))).quantize(Decimal("0.0001"), ROUND_HALF_UP)
    return "{:,.4f}".format(q)


def show(label, value):
    print("  {:<66} {}".format(label, value))


def pv(cf, r, t):
    return cf / (1 + r) ** t


def npv(rate, cf0, cfs):
    """npv(rate as decimal, CF0, [CF1, CF2, ...])."""
    return cf0 + sum(cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cfs))


def two_stage(d0, gs, n, gl, r):
    """Return (dividends D_1..D_n, D_(n+1), V_n, PV of dividends, PV of V_n, V_0)."""
    divs = [d0 * (1 + gs) ** t for t in range(1, n + 1)]
    d_next = divs[-1] * (1 + gl)
    vn = d_next / (r - gl)
    pv_divs = sum(pv(d, r, t) for t, d in enumerate(divs, start=1))
    pv_vn = pv(vn, r, n)
    return divs, d_next, vn, pv_divs, pv_vn, pv_divs + pv_vn


# ---------------------------------------------------------------- warm-up
print("=== Warm-up w1 (from Day 19): Gordon growth ===")
d1_w1 = 1.60 * 1.04
v_w1 = d1_w1 / (0.09 - 0.04)
show("D_1 = 1.60 x 1.04", d4(d1_w1))
show("r - g = 0.09 - 0.04", "{:.2f}".format(0.09 - 0.04))
show("V_0 = D_1/(r - g) (answer)", "{!r} -> {} -> {:.2f}".format(v_w1, d4(v_w1), v_w1))
show("wrong: D_0 on top 1.60/0.05", d4(1.60 / 0.05))
show("wrong: growth left out 1.664/0.09", d4(d1_w1 / 0.09))

print("\n=== Warm-up w2 (from Day 13): MWR vs TWR, conceptual, no numbers ===")

# ---------------------------------------------------------------- story
print("\n=== Story: Microsoft ===")
show("years from March 1986 to January 2003 (months)", "{} months = {:.2f} years".format(
    (2003 - 1986) * 12 + (1 - 3), ((2003 - 1986) * 12 + (1 - 3)) / 12))
show("annual dividend before the split / after (2-for-1)", "{:.2f} / {:.2f}".format(0.16, 0.16 / 2))
show("8 cents x 10.8 billion shares ($ millions)", "{:,.0f}".format(0.08 * 10.8e9 / 1e6))
show("special dividend $3 vs 8-cent annual starter (years' worth)", "{:.1f}".format(3 / 0.08))
show("91 cents a quarter x 4", "{:.2f}".format(0.91 * 4))
ratio = 3.64 / 0.08
show("3.64 / 0.08 (times as much)", "{:.1f}".format(ratio))
show("growth a year over 22.5 years", "{:.2%}".format(ratio ** (1 / 22.5) - 1))
yrs = ((2025 - 2003) * 12 + (9 - 1)) / 12
show("Jan 2003 to Sep 2025 (years)", "{:.3f}".format(yrs))
show("growth a year over Jan 2003 - Sep 2025", "{:.2%}".format(ratio ** (1 / yrs) - 1))
show("Gordon with g = 18%, r = 9%: r - g", "{:.2%}".format(0.09 - 0.18))

# ---------------------------------------------------------------- worked example
print("\n=== Worked example: D_0 = 1.00, 10% for 2 years, then 4%; r = 8% ===")
divs, d3, v2, pvd, pvv, v0 = two_stage(1.00, 0.10, 2, 0.04, 0.08)
show("D_1, D_2", ", ".join(d4(d) for d in divs))
show("D_3 = D_2 x 1.04", d4(d3))
show("r - g_L", "{:.2f}".format(0.08 - 0.04))
show("V_2 = D_3 / 0.04", d4(v2))
show("PV of D_1 = 1.10 / 1.08", d4(pv(divs[0], 0.08, 1)))
show("1.08^2", d4(1.08 ** 2))
show("D_2 + V_2", d4(divs[1] + v2))
show("PV of (D_2 + V_2) = 32.67 / 1.1664", d4(pv(divs[1] + v2, 0.08, 2)))
show("V_0 (answer)", "{!r} -> {} -> {:.2f}".format(v0, d4(v0), v0))
show("share of V_0 from the terminal value", "{:.1%}".format(pvv / v0))

# ---------------------------------------------------------------- walkthrough
print("\n=== Walkthrough: D_0 = 0.50, 20% for 3 years, then 5%; r = 9% ===")
divs, d4_, v3, pvd, pvv, v0 = two_stage(0.50, 0.20, 3, 0.05, 0.09)
show("D_1, D_2, D_3 (calculator displays)", ", ".join(d4(d) for d in divs))
show("D_4 = D_3 x 1.05", d4(d4_))
show("r - g_L = 0.09 - 0.05", "{:.2f}".format(0.09 - 0.05))
show("V_3 = D_4 / 0.04", d4(v3))
show("C03 = D_3 + V_3", d4(divs[2] + v3))
cfs = [divs[0], divs[1], divs[2] + v3]
show("npv(9, 0, {.6, .72, 23.544}) (answer)", "{!r} -> {} -> {:.2f}".format(
    npv(0.09, 0, cfs), d4(npv(0.09, 0, cfs)), npv(0.09, 0, cfs)))
show("V_0 from two_stage()", d4(v0))
show("1.09^3", d4(1.09 ** 3))
show("PV of the three dividends (C03 = .864 alone)", "{} -> {:.2f}".format(d4(pvd), pvd))
show("npv(9, 0, {.6, .72, .864})", d4(npv(0.09, 0, divs)))
show("PV of V_3 = 22.68 / 1.09^3", "{} -> {:.2f}".format(d4(pvv), pvv))
show("share of V_0 from the terminal value", "{:.2%}".format(pvv / v0))
show("share of V_0 from the dividends", "{:.2%}".format(pvd / v0))
show("V_0 to cents", "{:.2f}".format(v0))
show("if 'bought' at 18.00: NPV with CF0 = -18", d4(npv(0.09, -18, cfs)))

print("\n--- traps, on the walkthrough numbers ---")
t1 = pvd + pv(v3, 0.09, 4)
show("TV discounted 4 periods: 22.68/1.09^4", d4(pv(v3, 0.09, 4)))
show("  -> V_0", d4(t1))
v3_dn = divs[2] / 0.04
show("D_3 instead of D_4 on top: V_3 = .864/.04", d4(v3_dn))
show("  -> V_0", d4(pvd + pv(v3_dn, 0.09, 3)))
show("forgot V_3 in C03: NPV", d4(pvd))
d4_gs = divs[2] * 1.20
v3_gs = d4_gs / 0.04
show("D_4 grown at 20% (g_S) instead of 5%: D_4, V_3", "{}, {}".format(d4(d4_gs), d4(v3_gs)))
show("  -> V_0", d4(pvd + pv(v3_gs, 0.09, 3)))

# ---------------------------------------------------------------- check questions
print("\n=== c1: D_0 = 1.20, 15% for 4 years, then 4%; r = 10% ===")
divs, d5, v4, pvd, pvv, v0 = two_stage(1.20, 0.15, 4, 0.04, 0.10)
show("D_1..D_4 (full)", ", ".join(repr(round(d, 10)) for d in divs))
show("D_1..D_4 (4-decimal displays)", ", ".join(d4(d) for d in divs))
show("D_5 = D_4 x 1.04", "{!r} -> {}".format(round(d5, 10), d4(d5)))
show("r - g_L", "{:.2f}".format(0.10 - 0.04))
show("V_4 = D_5 / 0.06", d4(v4))
c04 = divs[3] + v4
show("C04 = D_4 + V_4", "{!r} -> {}".format(c04, d4(c04)))
cfs = divs[:3] + [c04]
c1 = npv(0.10, 0, cfs)
show("V_0 (answer)", "{!r} -> {} -> {:.2f}".format(c1, d4(c1), c1))
show("V_0 with C04 typed 38.4781 and D_3 typed 1.82505", d4(npv(0.10, 0, [1.38, 1.587, 1.82505, 38.4781])))
r2 = [round(d, 2) for d in divs]
d5r = round(r2[3] * 1.04, 2)
v4r = round(d5r / 0.06, 2)
alt = npv(0.10, 0, r2[:3] + [r2[3] + v4r])
show("everything rounded to cents along the way -> V_0", "{} (off by {:.4f})".format(d4(alt), alt - c1))
show("PV of dividends / PV of V_4", "{} / {}".format(d4(pvd), d4(pvv)))
show("wrong: TV discounted 5 periods", d4(pvd + pv(v4, 0.10, 5)))
show("wrong: forgot V_4 in C04", d4(pvd))

print("\n=== c3: nothing for 3 years, D_4 = 2.00, then 5% forever; r = 11% ===")
v3 = 2.00 / (0.11 - 0.05)
c3 = v3 / 1.11 ** 3
show("V_3 = 2.00 / 0.06", d4(v3))
show("1.11^3 (4 decimals / exact)", "{} / {}".format(d4(1.11 ** 3), round(1.11 ** 3, 6)))
show("V_0 = V_3 / 1.11^3 (answer)", "{!r} -> {} -> {:.2f}".format(c3, d4(c3), c3))
show("npv(11, 0, {0, 0, 33.3333})", d4(npv(0.11, 0, [0, 0, 33.3333])))
show("wrong: discounted 4 periods", d4(v3 / 1.11 ** 4))
show("wrong: D_5 = 2.10 on top -> V_3, V_0", "{}, {}".format(d4(2.10 / 0.06), d4(2.10 / 0.06 / 1.11 ** 3)))

print("\n=== c4: D_0 = 2.00, 12% for 2 years, then 6%; r = 10% ===")
divs, d3, v2, pvd, pvv, v0 = two_stage(2.00, 0.12, 2, 0.06, 0.10)
show("D_1, D_2", ", ".join(d4(d) for d in divs))
show("D_3 = D_2 x 1.06", "{!r} -> {}".format(round(d3, 10), d4(d3)))
show("V_2 = D_3 / 0.04", d4(v2))
show("PV of D_1", d4(pv(divs[0], 0.10, 1)))
show("PV of (D_2 + V_2)", d4(pv(divs[1] + v2, 0.10, 2)))
show("C (correct) V_0", "{} -> {:.2f}".format(d4(v0), v0))
a = pvd + pv(v2, 0.10, 3)
show("A: PV of V_2 over 3 periods = 66.4832/1.10^3", d4(pv(v2, 0.10, 3)))
show("A: PV of D_2 alone = 2.5088/1.10^2", d4(pv(divs[1], 0.10, 2)))
show("A: TV discounted 3 periods", "{} -> {:.2f}".format(d4(a), a))
v2b = divs[1] / 0.04
b = pvd + pv(v2b, 0.10, 2)
show("B: D_2 on top, V_2 = 2.5088/0.04", d4(v2b))
show("B: PV of (D_2 + 62.72) = 65.2288/1.10^2", d4(pv(divs[1] + v2b, 0.10, 2)))
show("B: V_0", "{} -> {:.2f}".format(d4(b), b))
show("npv(10, 0, {2.24, 68.992})", d4(npv(0.10, 0, [2.24, divs[1] + v2])))
show("C02 = D_2 + V_2", d4(divs[1] + v2))
