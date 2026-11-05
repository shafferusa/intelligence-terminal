#!/usr/bin/env python3
"""Day 22 of CFA Math: every number that appears on day-22.html.

Index weighting: price, market-cap (value), float-adjusted, equal; the
divisor after a split or a substitution; index price return and total
return (CFA Program 2027 Level I, Quantitative Methods, LM3 Benchmarking
Returns). Plain Python 3, standard library only. Run it and copy the
printed numbers.
"""


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def pc(x, n=2):
    """A decimal as a percent string."""
    return "{:.{}f}%".format(100 * x, n)


def show(label, value):
    print("  {:<70} {}".format(label, value))


def bond_price(n, r, pmt, fv=100.0):
    """Price of a level-coupon bond: n periods, r per period (decimal)."""
    return sum(pmt / (1 + r) ** t for t in range(1, n + 1)) + fv / (1 + r) ** n


# ---------------------------------------------------------------- warm-up
print("=== Warm-up w1 (from Day 19): implied growth g = r - D1/P0 ===")
p0_w1, d1_w1, r_w1 = 60.00, 2.10, 0.08
dy_w1 = d1_w1 / p0_w1
g_w1 = r_w1 - dy_w1
show("dividend yield D1/P0 = 2.10/60", "{!r} -> {}".format(dy_w1, pc(dy_w1)))
show("g = 0.08 - 0.035 (answer, percent)", "{!r} -> {}".format(100 * g_w1, pc(g_w1)))
show("calculator display .08 - 2.1/60", d4(g_w1))
show("wrong: added the yield, 0.08 + 0.035", pc(r_w1 + dy_w1))
show("check: D1/(r - g) = 2.10/0.045", d4(d1_w1 / (r_w1 - g_w1)))

print("\n=== Warm-up w2 (from Day 15): 9-year 5% semiannual bond at a 6.5% yield ===")
right = bond_price(18, 0.0325, 2.5)
no_half = bond_price(18, 0.0325, 5.0)
at_coupon = bond_price(18, 0.025, 2.5)
annual = bond_price(9, 0.065, 5.0)
show("B (right): N = 18, I/Y = 3.25, PMT = 2.5, FV = 100 -> PV", "{!r} -> {} -> {:.2f}".format(right, d4(-right), right))
show("C: coupon not halved, PMT = 5, N = 18, I/Y = 3.25", "{} -> {:.2f}".format(d4(no_half), no_half))
show("A: discounted at the coupon rate (I/Y = 2.5)", "{} -> {:.2f}".format(d4(at_coupon), at_coupon))
show("(not used) annual treatment N = 9, I/Y = 6.5, PMT = 5", d4(annual))

# ---------------------------------------------------------------- story
print("\n=== Story: Apple's 4-for-1 split, 28-31 Aug 2020 ===")
close = 499.23
show("499.23 / 4 (split-adjusted Friday close)", "{:.4f} -> {:.2f}".format(close / 4, close / 4))
show("share of the old price left after a 4-for-1 split", "{:.2%}".format(1 / 4))
show("cut in price (three-quarters)", "{:.2%}".format(1 - 1 / 4))
show("a quarter of 12%", "{:.1%}".format(0.12 / 4))
w_after = 0.12 / 4 / (1 - 0.12 + 0.12 / 4)
show("12% weight, price quartered, other prices fixed: 3/91", "{:.2%}".format(w_after))
div = 0.15
show("Dow points per $1 move, divisor 0.15 (1/0.15)", "{:.2f} -> about {:.1f}".format(1 / div, 1 / div))
one_pct_before = 0.01 * close
one_pct_after = 0.01 * close / 4
show("1% of 499.23 ($)", "{:.2f}".format(one_pct_before))
show("Dow points for that move, /0.15", "{:.1f}".format(one_pct_before / div))
show("1% of 124.81 ($)", "{:.2f}".format(0.01 * 124.81))
show("Dow points for that move, /0.15", "{:.1f}".format(0.01 * 124.81 / div))

# ---------------------------------------------------------------- why it works
print("\n=== Why it works: equal weights drift (worked-example returns) ===")
growth = [1.08, 0.95, 1.075]
tot = sum(growth)
show("1/3 each grows to 1.08, 0.95, 1.075 (sum)", "{:.4f}".format(tot))
show("new weights X, Y, Z", ", ".join("{:.2%}".format(g / tot) for g in growth))

# ---------------------------------------------------------------- worked example
print("\n=== Worked example: X, Y, Z over one quarter ===")
P0 = {"X": 50.0, "Y": 20.0, "Z": 80.0}
P1 = {"X": 54.0, "Y": 19.0, "Z": 86.0}
OUT = {"X": 200.0, "Y": 500.0, "Z": 100.0}   # shares outstanding, millions
HELD = {"X": 40.0, "Y": 0.0, "Z": 50.0}      # closely held, millions
DIVS = {"X": 0.0, "Y": 0.40, "Z": 0.0}
names = ["X", "Y", "Z"]
R = {k: P1[k] / P0[k] - 1 for k in names}
for k in names:
    show("price return {} = {:.0f}/{:.0f} - 1".format(k, P1[k], P0[k]), pc(R[k]))
FLOAT = {k: OUT[k] - HELD[k] for k in names}
show("free float X, Y, Z (m)", ", ".join("{:.0f}".format(FLOAT[k]) for k in names))
fcap = {k: P0[k] * FLOAT[k] for k in names}
show("float caps X, Y, Z ($m)", ", ".join("{:,.0f}".format(fcap[k]) for k in names))
ftot = sum(fcap.values())
show("total float cap ($m)", "{:,.0f}".format(ftot))
wf = {k: fcap[k] / ftot for k in names}
show("float weights X, Y, Z", ", ".join(pc(wf[k]) for k in names))
show("float weights, 4 decimals", ", ".join("{:.4f}".format(wf[k]) for k in names))
terms = {k: wf[k] * 100 * R[k] for k in names}
show("terms w x R, exact weights (percent points)", ", ".join("{:.4f}".format(terms[k]) for k in names))
w4 = {k: round(wf[k], 4) for k in names}
terms4 = {k: w4[k] * 100 * R[k] for k in names}
show("terms with the page's 4-decimal weights (0.3636 x 8 ...)", ", ".join("{:.4f}".format(terms4[k]) for k in names))
show("  ... as printed on the page, 2 decimals; their sum",
     "{} -> {:.2f}".format(", ".join("{:.2f}".format(terms4[k]) for k in names),
                           sum(round(terms4[k], 2) for k in names)))
r_float = sum(wf[k] * R[k] for k in names)
show("(a) float-adjusted price return", "{!r} -> {}".format(100 * r_float, pc(r_float)))
show("    check in $m: gains 640 - 500 + 300 over 22,000",
     "{:.0f}, {:.0f}, {:.0f} -> {}".format(fcap['X'] * R['X'], fcap['Y'] * R['Y'], fcap['Z'] * R['Z'],
                                           pc(sum(fcap[k] * R[k] for k in names) / ftot)))
cap = {k: P0[k] * OUT[k] for k in names}
ctot = sum(cap.values())
show("full caps X, Y, Z ($m)", ", ".join("{:,.0f}".format(cap[k]) for k in names))
show("total full cap ($m)", "{:,.0f}".format(ctot))
wc = {k: cap[k] / ctot for k in names}
show("full-cap weights X, Y, Z", ", ".join(pc(wc[k]) for k in names))
r_cap = sum(wc[k] * R[k] for k in names)
show("(b) full-cap price return", "{!r} -> {}".format(100 * r_cap, pc(r_cap)))
show("    with 4-decimal weights", "{:.4f}".format(sum(round(wc[k], 4) * 100 * R[k] for k in names)))
r_eq = sum(R.values()) / 3
show("(b) equal-weighted price return (8 - 5 + 7.5)/3", "{!r} -> {}".format(100 * r_eq, pc(r_eq)))
TR = {k: (P1[k] - P0[k] + DIVS[k]) / P0[k] for k in names}
show("(c) Y total return (19 + 0.40)/20 - 1", pc(TR["Y"]))
show("    Y income return 0.40/20", pc(DIVS["Y"] / P0["Y"]))
tr_float = sum(wf[k] * TR[k] for k in names)
show("(c) float-adjusted total return", "{!r} -> {}".format(100 * tr_float, pc(tr_float)))
show("    with the page's 4-decimal weights", "{:.4f}".format(sum(w4[k] * 100 * TR[k] for k in names)))
inc = wf["Y"] * DIVS["Y"] / P0["Y"]
show("    income return 0.4545 x 2%", "{!r} -> {}".format(100 * inc, pc(inc)))
show("    price + income", pc(r_float + inc))
pw = sum(P1.values()) / sum(P0.values()) - 1
show("(not on page) price-weighted return 154/150 - 1", pc(pw))

# ---------------------------------------------------------------- walkthrough
print("\n=== Walkthrough: price-weighted index A, B, C; A splits 4-for-1 ===")
pa, pb, pc_ = 400.0, 80.0, 120.0
d_old = 3.0
idx0 = (pa + pb + pc_) / d_old
show("sum of prices before the split", "{:.0f}".format(pa + pb + pc_))
show("index before = 600 / 3", d4(idx0))
show("weight of A before (price-weighted) 400/600", pc(pa / (pa + pb + pc_)))
pa_s = pa / 4
sum_s = pa_s + pb + pc_
show("A after split 400/4; new sum", "{:.0f}; {:.0f}".format(pa_s, sum_s))
show("wrong: old divisor kept, 300/3 (false fall)", "{} ({})".format(d4(sum_s / d_old), pc(sum_s / d_old / idx0 - 1)))
d_new = sum_s / idx0
show("new divisor = 300 / 200", d4(d_new))
show("check: 300 / 1.5", d4(sum_s / d_new))
show("weight of A after (price-weighted) 100/300", pc(pa_s / sum_s))
pa1, pb1, pc1 = 110.0, 76.0, 126.0
sum1 = pa1 + pb1 + pc1
idx1 = sum1 / d_new
show("sum of end prices", "{:.0f}".format(sum1))
show("index at the end = 312 / 1.5", d4(idx1))
pr = idx1 / idx0 - 1
show("price return (Delta% %CH)", "{!r} -> {}".format(100 * pr, d4(100 * pr)))
show("TI: 208/200", d4(idx1 / idx0))
show("TI: 208/200 - 1", d4(idx1 / idx0 - 1))
divb, divc = 2.0, 1.0
idx1_tr = (sum1 + divb + divc) / d_new
show("total-return level = (312 + 2 + 1) / 1.5", d4(idx1_tr))
trr = idx1_tr / idx0 - 1
show("total return (Delta% %CH)", "{!r} -> {}".format(100 * trr, d4(100 * trr)))
show("income return = 3 / 300", pc((divb + divc) / sum_s))
show("TI: 210/200", d4(idx1_tr / idx0))
show("TI: 210/200 - 1", d4(idx1_tr / idx0 - 1))
# member returns and price weights after the split
ra, rb, rc = pa1 / pa_s - 1, pb1 / pb - 1, pc1 / pc_ - 1
show("member returns A, B, C", ", ".join(pc(x) for x in (ra, rb, rc)))
wpa, wpb, wpc = pa_s / sum_s, pb / sum_s, pc_ / sum_s
show("price weights after split A, B, C", ", ".join(pc(x) for x in (wpa, wpb, wpc)))
show("sum w x R (price-weighted)", pc(wpa * ra + wpb * rb + wpc * rc))
# cap-weighted, shares after the split (millions)
qa, qb, qc = 40.0, 50.0, 30.0
mc0 = pa_s * qa + pb * qb + pc_ * qc
mc1 = pa1 * qa + pb1 * qb + pc1 * qc
show("cap at start 100x40 + 80x50 + 120x30 ($m)", "{:,.0f} + {:,.0f} + {:,.0f} = {:,.0f}".format(
    pa_s * qa, pb * qb, pc_ * qc, mc0))
show("BA displays of the two cap sums", "{} and {}".format(d4(mc0), d4(mc1)))
show("cap at end 110x40 + 76x50 + 126x30 ($m)", "{:,.0f} + {:,.0f} + {:,.0f} = {:,.0f}".format(
    pa1 * qa, pb1 * qb, pc1 * qc, mc1))
show("A's cap before split 400 x 10 = after 100 x 40", "{:,.0f} = {:,.0f}".format(pa * qa / 4, pa_s * qa))
show("BA: 11,980 / 11,600 - 1 (display)", d4(mc1 / mc0 - 1))
cr = mc1 / mc0 - 1
show("cap-weighted price return x 100", "{!r} -> {}".format(100 * cr, d4(100 * cr)))
show("TI intermediate: 11980/11600", d4(mc1 / mc0))
wca, wcb, wcc = pa_s * qa / mc0, pb * qb / mc0, pc_ * qc / mc0
show("cap weights A, B, C", ", ".join(pc(x) for x in (wca, wcb, wcc)))
show("sum w x R (cap-weighted)", pc(wca * ra + wcb * rb + wcc * rc))
show("gap PW - cap (points)", "{:.4f}".format(100 * (pr - cr)))

# ---------------------------------------------------------------- traps
print("\n=== Traps ===")
show("divisor not reset: index 300/3 vs 200", "{} ({})".format(d4(sum_s / d_old), pc(sum_s / d_old / idx0 - 1)))
show("divisor not reset, end: 312/3 vs 200", "{} ({})".format(d4(sum1 / d_old), pc(sum1 / d_old / idx0 - 1)))
show("A's cap weight before = after (4,000 / 11,600)", pc(4000 / mc0))
show("float vs full-cap (worked example)", "{} vs {}".format(pc(r_float), pc(r_cap)))

# ---------------------------------------------------------------- check questions
print("\n=== c1: price-weighted 90, 45, 165; divisor 2.5; 165 splits 3-for-1 ===")
s0 = 90 + 45 + 165
i0 = s0 / 2.5
show("sum 300; index = 300 / 2.5", d4(i0))
s_split = 90 + 45 + 165 / 3
dn = s_split / i0
show("after split: 90 + 45 + 55", "{:.0f}".format(s_split))
show("new divisor 190 / 120", "{!r} -> {}".format(dn, d4(dn)))
s_end = 93 + 44 + 58
i_end = s_end / dn
show("end prices sum 93 + 44 + 58", "{:.0f}".format(s_end))
show("index at the end 195 / 1.5833", "{!r} -> {}".format(i_end, d4(i_end)))
c1 = i_end / i0 - 1
show("TI: 123.1579/120", d4(i_end / i0))
show("BA/TI: /120 - 1", d4(i_end / i0 - 1))
show("price return (answer, percent)", "{!r} -> {} -> {}".format(100 * c1, d4(100 * c1), pc(c1)))
show("shortcut 195/190 - 1", d4(100 * (s_end / s_split - 1)))
show("typed rounded 123.1579 in Delta%", d4(100 * (123.1579 / 120 - 1)))
show("wrong: old divisor kept, 195/2.5 = ", "{} -> {}".format(d4(s_end / 2.5), pc(s_end / 2.5 / i0 - 1)))
show("wrong: 195/300 - 1 (split ignored)", pc(s_end / s0 - 1))

print("\n=== c2: cap-weighted 2-for-1 split: conceptual, no numbers ===")

print("\n=== c3: 4-stock PW index, sum 260, divisor 2.0; 30 replaced by 110 ===")
i_c3 = 260 / 2.0
s_new = 260 - 30 + 110
dn_c3 = s_new / i_c3
show("index before 260 / 2", d4(i_c3))
show("new sum 260 - 30 + 110", "{:.0f}".format(s_new))
show("new divisor 340 / 130 (answer)", "{!r} -> {}".format(dn_c3, d4(dn_c3)))
show("wrong: divisor kept: 340 / 2 (false jump)", "{} ({})".format(d4(s_new / 2.0), pc(s_new / 2.0 / i_c3 - 1)))
show("check: 340 / 2.6154", d4(s_new / dn_c3))

print("\n=== c4: PW index A 25->30, B 50->45, C 100->110 (C pays 2.00) ===")
s_a, s_b = 25 + 50 + 100, 30 + 45 + 110
c4_right = s_b / s_a - 1
c4_eq = (30 / 25 - 1 + 45 / 50 - 1 + 110 / 100 - 1) / 3
c4_tr = (s_b + 2) / s_a - 1
show("A (right): 185/175 - 1", "{!r} -> {}".format(100 * c4_right, pc(c4_right)))
show("member returns", ", ".join(pc(x) for x in (30 / 25 - 1, 45 / 50 - 1, 110 / 100 - 1)))
show("B: average of the three returns (equal weighting)", pc(c4_eq))
show("C: dividend added (total return) 187/175 - 1", pc(c4_tr))
show("calculator: 185/175 - 1 (display), x 100", "{} -> {}".format(d4(c4_right), d4(100 * c4_right)))
show("weights 25/175, 50/175, 100/175", ", ".join(pc(x / 175) for x in (25, 50, 100)))
show("weighted: (25 x 20% + 50 x -10% + 100 x 10%)/175", "(5 - 5 + 10)/175 = {}".format(
    pc((25 * 0.2 + 50 * -0.1 + 100 * 0.1) / 175)))

print("\n=== headline length ===")
h1 = ("Apple split its stock 4-for-1 and lost three-quarters of its pull on the Dow. "
      "Nothing about Apple changed.")
show("characters", len(h1))
