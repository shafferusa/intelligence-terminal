#!/usr/bin/env python3
"""Day 10 of CFA Math: every number that appears on day-10.html.

Uneven cash flows: net present value (cash-flow additivity) and the internal
rate of return (CFA Program 2027 Level I, Quantitative Methods,
LM4 The Time Value of Money in Finance).
Plain Python 3, standard library only. Run it and copy the printed numbers.

Calculator displays are shown at the 4 decimals both calculators are set to.
The BA II Plus shows thousands separators (NPV = 1,199.6448), the TI-84 not.
"""


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus style, with separators)."""
    return "{:,.4f}".format(x)


def expand(cfs, freqs=None):
    """Turn a list of distinct amounts plus frequencies into the year-by-year stream."""
    if freqs is None:
        freqs = [1] * len(cfs)
    out = []
    for c, f in zip(cfs, freqs):
        out += [c] * f
    return out


def npv(rate_pct, cf0, cfs, freqs=None):
    """Same arguments as the TI-84's npv(: rate in percent, CF0, list, frequencies."""
    r = rate_pct / 100.0
    stream = expand(cfs, freqs)
    return cf0 + sum(c / (1 + r) ** t for t, c in enumerate(stream, start=1))


def irr(cf0, cfs, freqs=None, lo=-99.0, hi=1000.0):
    """Same arguments as the TI-84's irr(: returns a percent. Bisection on NPV."""
    f_lo = npv(lo, cf0, cfs, freqs)
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid, cf0, cfs, freqs)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def show(label, value):
    print("  {:<60} {}".format(label, value))


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 9): $24,000 loan, 5 years, 6% stated annual rate, monthly payments.
P, years, stated = 24000, 5, 0.06
n_w1, i_w1 = years * 12, stated / 12
w1 = P * i_w1 / (1 - (1 + i_w1) ** -n_w1)
show("w1  N = 5 x 12", n_w1)
show("w1  I/Y = 6 / 12 (percent per month)", "{:.4f}".format(100 * i_w1))
show("w1  1.005^-60", "{:.6f}".format(1.005 ** -60))
show("w1  1 - 1.005^-60", "{:.6f}".format(1 - 1.005 ** -60))
show("w1  24,000 x 0.005", "{:.2f}".format(P * i_w1))
show("w1  monthly payment (answer)", "{!r} -> {} (BA PMT = -{}, TI PMT=-{:.4f})".format(w1, money(w1), d4(w1), w1))
w1_wrong = P * stated / (1 - (1 + stated) ** -years) / 12
show("w1  (trap) annual payment / 12", money(w1_wrong))

# w2 (Day 2): discount factor for year 3 at 8%.
w2 = 1 / 1.08 ** 3
show("w2  A (correct) DF = 1/1.08^3", "{:.6f} -> {:.4f}".format(w2, w2))
show("w2  B (simple) 1/(1 + 3 x 0.08)", "{:.4f}".format(1 / (1 + 3 * 0.08)))
show("w2  C (growth factor) 1.08^3", "{:.4f}".format(1.08 ** 3))
show("w2  link: 150 x DF", money(150 * w2))

# ---------------------------------------------------------------------------
print("\n=== Story numbers (from the plan; no arithmetic beyond these) ===")
show("MSA signed", "23 November 1998")
show("parties", "4 manufacturers; 46 states + D.C. + 5 territories")
show("states + D.C. + territories", 46 + 1 + 5)
show("first 25 years: 1998 + 25", 1998 + 25)
show("about $206 billion / 25 years (average a year, $ billions)", "{:.2f}".format(206 / 25))
show("Ohio Buckeye bonds, Oct 2007", "${:,} -> ${:.2f} billion".format(5531594541, 5531594541 / 1e9))
show("refinancing, Feb 2020", "about $5.35 billion")

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: pay 470 for 120, 120, 115, 110, 105 ===")
CF0 = -470
CFS, FREQ = [120, 115, 110, 105], [2, 1, 1, 1]
STREAM = expand(CFS, FREQ)
show("stream, years 1-5", STREAM)
print("  BA II Plus worksheet echoes (4 decimals):")
show("  CFo", d4(CF0))
for k, (c, f) in enumerate(zip(CFS, FREQ), start=1):
    show("  C{:02d} / F{:02d}".format(k, k), "{} / {}".format(d4(c), d4(f)))
show("  TI-84 line", "npv(6,{},{{{}}},{{{}}})".format(CF0, ",".join(map(str, CFS)), ",".join(map(str, FREQ))))
show("sum of the payments (undiscounted)", sum(STREAM))
pv_sum = 0.0
for t, c in enumerate(STREAM, start=1):
    p = c / 1.06 ** t
    pv_sum += p
    show("  1.06^{}".format(t), "{:.6f}".format(1.06 ** t))
    show("  PV of year {}: {} / 1.06^{}".format(t, c, t), "{:.4f} -> {}".format(p, money(p)))
rounded_parts = [round(c / 1.06 ** t, 2) for t, c in enumerate(STREAM, start=1)]
show("sum of the five PVs (exact)", "{:.4f} -> {}".format(pv_sum, money(pv_sum)))
show("sum of the rounded PVs", "{:.2f}".format(sum(rounded_parts)))
NPV6 = npv(6, CF0, CFS, FREQ)
show("NPV at 6% (answer)", "{!r} -> BA NPV = {} / TI {:.4f} -> {}".format(NPV6, d4(NPV6), NPV6, money(NPV6)))
show("check: -470 + PV sum", money(-470 + pv_sum))
IRR = irr(CF0, CFS, FREQ)
show("IRR (percent)", "{!r} -> BA IRR = {:.4f} / TI {:.4f} -> {:.2f}%".format(IRR, IRR, IRR, IRR))
show("NPV at the exact IRR", "{:.10f}".format(npv(IRR, CF0, CFS, FREQ)))
for rp in (0, 4, 6, 7, 8, 10):
    show("  NPV at {}%".format(rp), "{:.4f} -> {}".format(npv(rp, CF0, CFS, FREQ), money(npv(rp, CF0, CFS, FREQ))))
show("  NPV at 6.97%", "{:.4f}".format(npv(6.97, CF0, CFS, FREQ)))

print("  Bank-account check at the IRR: deposit 470, withdraw the payments")
bal = 470.0
g = 1 + IRR / 100
for t, c in enumerate(STREAM, start=1):
    grown = bal * g
    bal = grown - c
    show("  year {}: grows to, minus {}, leaves".format(t, c), "{:.4f} -> {:.4f}  ({} -> {})".format(grown, bal, money(grown), money(bal)))
show("  ending balance (should be 0)", "{:.10f}".format(bal))
show("  growth factor 1 + IRR (4 dp)", "{:.6f}".format(g))
bal_r = 470.0
for c in STREAM:
    bal_r = bal_r * 1.069689 - c
show("  same check at the displayed 6.9689%: ending balance", "{:.4f} -> {}".format(bal_r, money(abs(bal_r))))

print("  Bank account at 6%: what it takes to fund the same withdrawals")
show("  deposit needed at 6% = PV of payments", money(pv_sum))
show("  extra the bank would need over 470", money(pv_sum - 470))

print("  Story payoff: every payment 10% short")
SHORT = [0.9 * c for c in CFS]
show("  short amounts", ["{:g}".format(c) for c in SHORT])
IRR_S = irr(CF0, SHORT, FREQ)
show("  IRR if 10% short", "{!r} -> {:.4f} -> {:.2f}%".format(IRR_S, IRR_S, IRR_S))
NPV6_S = npv(6, CF0, SHORT, FREQ)
show("  NPV at 6% if 10% short", "{:.4f} -> {}".format(NPV6_S, money(NPV6_S)))
show("  same as paying 470/0.9 for the full stream", "{:.4f} (IRR {:.4f})".format(470 / 0.9, irr(-470 / 0.9, CFS, FREQ)))
show("  short PV at 6% = 0.9 x 482.16", money(0.9 * pv_sum))
show("  shortfall in PV at 6%", money(pv_sum - 0.9 * pv_sum))

print("  Rate typed as a decimal: npv(0.06, ...) means 0.06%")
show("  npv(.06,-470,{120,115,110,105},{2,1,1,1})", "{:.4f} -> {}".format(npv(0.06, CF0, CFS, FREQ), money(npv(0.06, CF0, CFS, FREQ))))

# ---------------------------------------------------------------------------
print("\n=== Worked example: 500 today; 200, 250, 150 at 8% ===")
WE0, WE = -500, [200, 250, 150]
parts8 = []
for t, c in enumerate(WE, start=1):
    p = c / 1.08 ** t
    parts8.append(p)
    show("  1.08^{}".format(t), "{:.6f}".format(1.08 ** t))
    show("  PV year {}: {} / 1.08^{}".format(t, c, t), "{:.4f} -> {}".format(p, money(p)))
we8 = npv(8, WE0, WE)
show("NPV at 8% (exact)", "{!r} -> {}".format(we8, money(we8)))
show("NPV at 8% from rounded PVs", "{:.2f}".format(-500 + sum(round(p, 2) for p in parts8)))
for rp in (10, 11):
    parts = [c / (1 + rp / 100) ** t for t, c in enumerate(WE, start=1)]
    show("  PVs at {}%".format(rp), ", ".join(money(p) for p in parts))
    show("  NPV at {}%".format(rp), "{:.4f} -> {}".format(npv(rp, WE0, WE), money(npv(rp, WE0, WE))))
    show("  NPV at {}% from rounded PVs".format(rp), "{:.2f}".format(-500 + sum(round(p, 2) for p in parts)))
we_irr = irr(WE0, WE)
show("IRR (percent)", "{!r} -> {:.4f} -> {:.2f}%".format(we_irr, we_irr, we_irr))

# ---------------------------------------------------------------------------
print("\n=== c1: CF0 = -2,000; 500, 700, 700, 900 at 9% ===")
c1 = npv(9, -2000, [500, 700, 900], [1, 2, 1])
show("c1 stream", expand([500, 700, 900], [1, 2, 1]))
show("c1 NPV at 9% (answer)", "{!r} -> BA NPV = {} / TI {:.4f} -> {}".format(c1, d4(c1), c1, money(c1)))
for t, c in enumerate(expand([500, 700, 900], [1, 2, 1]), start=1):
    show("  PV year {}".format(t), money(c / 1.09 ** t))
c1_irr = irr(-2000, [500, 700, 900], [1, 2, 1])
show("c1 IRR", "{!r} -> {:.4f} -> {:.2f}%".format(c1_irr, c1_irr, c1_irr))
show("c1 (trap) F02 left at 1: 500, 700, 900", money(npv(9, -2000, [500, 700, 900])))

print("\n=== c2: NPV at 8% > 0, so IRR > 8% (illustrated with the worked example) ===")
show("worked example NPV 8%, 10%, 11%", "{} {} {}".format(money(npv(8, WE0, WE)), money(npv(10, WE0, WE)), money(npv(11, WE0, WE))))

print("\n=== c3: most to pay for 50, 50, 50, 60, 70 at 7% ===")
c3 = npv(7, 0, [50, 60, 70], [3, 1, 1])
show("c3 stream", expand([50, 60, 70], [3, 1, 1]))
show("c3 PV of inflows (answer)", "{!r} -> BA NPV = {} / TI {:.4f} -> {}".format(c3, d4(c3), c3, money(c3)))
show("c3 check: IRR at that price", "{:.6f}".format(irr(-c3, [50, 60, 70], [3, 1, 1])))
show("c3 (trap) F01 left at 1: 50, 60, 70", money(npv(7, 0, [50, 60, 70])))
show("c3 undiscounted sum", sum(expand([50, 60, 70], [3, 1, 1])))

print("\n=== c4: CF0 = -1,000; 400 x 3 then 300, at 10% ===")
c4 = npv(10, -1000, [400, 300], [3, 1])
show("c4 B (correct)", "{!r} -> BA NPV = {} / TI {:.4f} -> {}".format(c4, d4(c4), c4, money(c4)))
c4a = npv(10, -1000, [400, 300], [1, 1])
show("c4 A (F01 left at 1: 400 in year 1, 300 in year 2)", "{!r} -> {}".format(c4a, money(c4a)))
c4c = npv(10, 0, [400, 300], [3, 1])
show("c4 C (CF0 forgotten)", "{!r} -> BA NPV = {} -> {}".format(c4c, d4(c4c), money(c4c)))
show("c4 PV of 400 a year for 3 years at 10%", money(400 * (1 - 1.1 ** -3) / 0.1))
show("c4 PV of 300 in year 4 at 10%", money(300 / 1.1 ** 4))
show("c4 IRR", "{:.4f}%".format(irr(-1000, [400, 300], [3, 1])))

# ---------------------------------------------------------------------------
print("\n=== Tolerances ===")
show("|226.00 - c1|", "{:.4f}".format(abs(226.00 - c1)))
show("|226.90 - c3|", "{:.4f}".format(abs(226.90 - c3)))
show("|463.99 - w1|", "{:.4f}".format(abs(463.99 - w1)))
# By hand with each PV rounded to the cent first: still inside data-tol 0.01.
c1_hand = -2000 + sum(round(c / 1.09 ** t, 2) for t, c in enumerate(expand([500, 700, 900], [1, 2, 1]), start=1))
c3_hand = sum(round(c / 1.07 ** t, 2) for t, c in enumerate(expand([50, 60, 70], [3, 1, 1]), start=1))
show("c1 from cent-rounded PVs, |diff|", "{:.2f}, {:.4f}".format(c1_hand, abs(c1_hand - c1)))
show("c3 from cent-rounded PVs, |diff|", "{:.2f}, {:.4f}".format(c3_hand, abs(c3_hand - c3)))
show("c1 distractor (F02 left at 1) outside tol", abs(npv(9, -2000, [500, 700, 900]) - c1) > 0.01)
show("w1 distractor (annual / 12) outside tol", abs(w1_wrong - w1) > 0.01)
