#!/usr/bin/env python3
"""Day 5 of CFA Math: every number that appears on day-05.html.

Ordinary annuities: the present and future value of a level stream of payments
(CFA Program 2027 Level I, Quantitative Methods, LM4 The Time Value of Money in Finance).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def t4(x):
    """A 4-decimal TI-84 display (no thousands separators)."""
    return "{:.4f}".format(x)


def pv_single(fv, r, n):
    return fv / (1 + r) ** n


def fv_single(p, r, n):
    return p * (1 + r) ** n


def af(r, n):
    """Annuity factor: PV of 1 per period for n periods at r (ordinary annuity)."""
    return (1 - (1 + r) ** -n) / r


def fvf(r, n):
    """Future-value annuity factor: FV of 1 per period for n periods at r."""
    return ((1 + r) ** n - 1) / r


def pv_ann(a, r, n):
    return a * af(r, n)


def fv_ann(a, r, n):
    return a * fvf(r, n)


def pmt_for_fv(fv, r, n):
    return fv / fvf(r, n)


def show(label, value):
    print("  {:<64} {}".format(label, value))


def pct(x, dp=4):
    return "{:.{}f}%".format(100 * x, dp)


print("=== Warm-up ===")
w1 = (1 + 0.18 / 12) ** 12 - 1
show("w1  periodic rate 18% / 12", pct(0.18 / 12, 2))
show("w1  1.015^12", "{:.6f}".format(1.015 ** 12))
show("w1  EAR of 18% compounded monthly (answer, percent)", "{!r}  -> EFF = {}  ({})".format(100 * w1, d4(100 * w1), pct(w1, 2)))
w2 = 1 + 0.08 * (1 - 0.25)
show("w2  after-tax nominal 8% x (1 - 0.25)", pct(0.08 * 0.75, 2))
w2c = (1 + 0.08 * 0.75) / 1.03 - 1
show("w2  C (right) 1.06 / 1.03 - 1", "{:.6f} -> {}".format(w2c, pct(w2c, 2)))
show("w2  1.06 / 1.03", "{:.6f}".format(1.06 / 1.03))
w2a = (1.08 / 1.03 - 1) * 0.75
show("w2  A (taxed the real return) (1.08/1.03 - 1) x 0.75", "{:.6f} -> {}".format(w2a, pct(w2a, 2)))
show("w2  1.08/1.03 - 1", pct(1.08 / 1.03 - 1, 2))
show("w2  B (shortcut) 6% - 3%", pct(0.06 - 0.03, 2))

print("\n=== Story: Irene Triplett ===")
show("Civil War ended 1865 -> 2020", 2020 - 1865)
show("Irene born 1930, died 2020: age", "90 (died 31 May 2020; born 1930 -> {} or {})".format(2020 - 1930 - 1, 2020 - 1930))
show("Mose died 1938 aged 92 -> born about", "{} or {}".format(1938 - 92, 1938 - 93))
A = 73.13
show("120 cheques x $73.13 (total paid in ten years)", money(120 * A))

print("\n=== Why it works: A = $100 a year, r = 10%, N = 2 ===")
s1 = pv_single(100, 0.10, 1)
s2 = pv_single(100, 0.10, 2)
show("100 / 1.10", money(s1))
show("100 / 1.10^2 = 100 / 1.21", money(s2))
show("1.10^2", "{:.2f}".format(1.10 ** 2))
show("sum of single sums", "{!r} -> {}".format(s1 + s2, money(s1 + s2)))
show("rounded parts added: 90.91 + 82.64", money(90.91 + 82.64))
dep = 100 / 0.10
show("bank deposit A/r = 100 / 0.10", money(dep))
show("interest on deposit: 0.10 x 1,000", money(0.10 * dep))
left = pv_single(dep, 0.10, 2)
show("leftover 1,000 at t=2, today: 1,000 / 1.21", "{!r} -> {}".format(left, money(left)))
show("PV = 1,000 - 826.45 (bank trick)", "{!r} -> {}".format(dep - left, money(dep - left)))
show("formula 100 x (1 - 1.1^-2) / 0.1", "{!r} -> {}".format(pv_ann(100, 0.10, 2), money(pv_ann(100, 0.10, 2))))
p = pv_ann(100, 0.10, 2)
y1 = p * 1.10
show("account check: 173.55 x 1.10 after a year", money(y1))
show("  minus the first 100", money(y1 - 100))
y2 = (y1 - 100) * 1.10
show("  x 1.10 after the second year", money(y2))
show("  minus the second 100", money(y2 - 100))
show("  (rounded path: 173.55 x 1.1, -100, x1.1)", "{} {} {}".format(money(173.55 * 1.1), money(173.55 * 1.1 - 100), money((round(173.55 * 1.1, 2) - 100) * 1.1)))
f = fv_ann(100, 0.10, 2)
show("FV = 100 x (1.21 - 1) / 0.1", "{!r} -> {}".format(f, money(f)))
show("FV = PV x 1.21", money(p * 1.21))
show("FV by single sums: 100 x 1.1 + 100", money(100 * 1.1 + 100))
show("annuity factor 10%, 2 periods", "{:.4f}".format(af(0.10, 2)))

print("\n=== Worked example: $1,000 a year for 4 years at 6% ===")
parts = [pv_single(1000, 0.06, t) for t in (1, 2, 3, 4)]
for t, v in zip((1, 2, 3, 4), parts):
    show("1,000 / 1.06^{}".format(t), "{!r} -> {}".format(v, money(v)))
show("sum (exact)", money(sum(parts)))
show("sum of rounded parts", money(sum(round(v, 2) for v in parts)))
g4 = 1.06 ** 4
show("1.06^4", "{:.6f}".format(g4))
show("1.06^2", "{:.4f}".format(1.06 ** 2))
show("1.06^-4 = 1 / 1.06^4", "{:.6f}".format(1 / g4))
show("1 - 1.06^-4", "{:.6f}".format(1 - 1 / g4))
show("annuity factor (1 - 1.06^-4) / 0.06", "{:.6f}".format(af(0.06, 4)))
we_pv = pv_ann(1000, 0.06, 4)
show("PV by formula", "{!r} -> {}".format(we_pv, money(we_pv)))
show("1.06^4 - 1", "{:.6f}".format(g4 - 1))
show("FV factor (1.06^4 - 1) / 0.06", "{:.6f}".format(fvf(0.06, 4)))
we_fv = fv_ann(1000, 0.06, 4)
show("FV by formula", "{!r} -> {}".format(we_fv, money(we_fv)))
show("check 3,465.11 x 1.06^4", money(3465.11 * g4))
show("check exact PV x 1.06^4", money(we_pv * g4))
fparts = [fv_single(1000, 0.06, 4 - t) for t in (1, 2, 3, 4)]
for t, v in zip((1, 2, 3, 4), fparts):
    show("FV of year-{} payment: 1,000 x 1.06^{}".format(t, 4 - t), money(v))
show("sum of FV parts", money(sum(fparts)))

print("\n=== Calculator walkthrough: 120 x $73.13 at 4%/12 ===")
r = 0.04 / 12
show("I/Y = 4 / 12 (percent per month)", "{!r} -> I/Y = {}".format(4 / 12, d4(4 / 12)))
pv_w = pv_ann(A, r, 120)
show("CPT PV (answer; calculator shows negative)", "{!r} -> PV = -{} / PV=-{}".format(-pv_w, d4(pv_w), t4(pv_w)))
show("PV to the cent", money(pv_w))
fv_w = fv_ann(A, r, 120)
show("CPT FV with PV = 0 (calculator shows negative)", "{!r} -> FV = -{} / FV=-{}".format(-fv_w, d4(fv_w), t4(fv_w)))
show("FV to the cent", money(fv_w))
g120 = (1 + r) ** 120
show("(1 + 0.04/12)^120", "{:.6f}".format(g120))
show("check: PV x (1 + r)^120", money(pv_w * g120))
show("check: 7,223.0629 x 1.490833", money(7223.0629 * round(g120, 6)))
pv8 = pv_ann(A, 0.08 / 12, 120)
show("I/Y = 8 / 12", d4(8 / 12))
show("CPT PV at 8% stated", "{!r} -> PV = -{} / PV=-{}".format(-pv8, d4(pv8), t4(pv8)))
show("PV at 8% to the cent", money(pv8))

print("\n=== Back to the story ===")
show("PV 10 years at 4% / total paid", "{} / {} = {:.1f}%".format(money(pv_w), money(120 * A), 100 * pv_w / (120 * A)))
show("discount factor of the last cheque (t = 120)", "{:.4f} -> {:.0f} cents".format(1 / g120, 100 / g120))
show("discount factor of the first cheque (t = 1)", "{:.4f}".format(1 / (1 + r)))
perp = A / r
show("forever: 73.13 / (0.04/12) = 73.13 x 300", "{!r} -> {}".format(perp, money(perp)))
show("interest on 21,939 for a month at 4%/12", money(21939 * r))
left_w = perp / g120
show("leftover 21,939 at month 120, today", money(left_w))
show("21,939 - 14,715.94 (bank trick)", money(perp - left_w))
show("1 - DF(120)", "{:.6f}".format(1 - 1 / g120))
pv30 = pv_ann(A, r, 360)
show("30 years (360 months) at 4%", money(pv30))
show("3 x the 10-year PV (wrong guess)", money(3 * pv_w))
show("30-year PV as a multiple of the 10-year PV", "{:.2f}".format(pv30 / pv_w))
show("8% PV vs 4% PV, percent lower", "{:.1f}%".format(100 * (1 - pv8 / pv_w)))
show("total paid in 30 years", money(360 * A))

print("\n=== Check question 1: $450 a month, 5 years, 6% stated ===")
c1 = pv_ann(450, 0.06 / 12, 60)
show("I/Y = 6 / 12", d4(6 / 12))
show("annuity factor 0.5%, 60", "{:.6f}".format(af(0.005, 60)))
show("1.005^-60", "{:.6f}".format(1.005 ** -60))
show("c1 PV (answer)", "{!r} -> {}  BA PV = -{} / TI PV=-{}".format(c1, money(c1), d4(c1), t4(c1)))
show("c1 total paid 60 x 450", money(60 * 450))

print("\n=== Check question 2: $1,000 a year at 6%, 20 vs 40 years ===")
a20, a40 = af(0.06, 20), af(0.06, 40)
show("annuity factor 20 years", "{:.4f}".format(a20))
show("annuity factor 40 years", "{:.4f}".format(a40))
show("PV 20 years / PV 40 years", "{} / {}".format(money(1000 * a20), money(1000 * a40)))
show("rise", "{:.2f}%".format(100 * (a40 / a20 - 1)))
show("value added by years 21-40", money(1000 * (a40 - a20)))
show("1.06^-20 (discount factor at 20)", "{:.4f}".format(1.06 ** -20))
show("check: PV20 x 1.06^-20", money(1000 * a20 * 1.06 ** -20))
show("$1,000 in year 40 today", money(pv_single(1000, 0.06, 40)))
show("$1,000 in year 21 today", money(pv_single(1000, 0.06, 21)))

print("\n=== Check question 3: $500,000 in 30 years at 5%, annual deposits ===")
c3 = pmt_for_fv(500000, 0.05, 30)
show("1.05^30", "{:.6f}".format(1.05 ** 30))
show("1.05^30 - 1", "{:.6f}".format(1.05 ** 30 - 1))
show("FV factor (1.05^30 - 1) / 0.05", "{:.6f}".format(fvf(0.05, 30)))
show("500,000 x 0.05", money(500000 * 0.05))
show("c3 PMT (answer)", "{!r} -> {}  BA PMT = -{} / TI PMT=-{}".format(c3, money(c3), d4(c3), t4(c3)))
show("c3 total deposited 30 x PMT", money(30 * c3))
show("c3 interest earned", money(500000 - 30 * c3))
show("c3 wrong: 500,000 / 30 (no interest)", money(500000 / 30))

print("\n=== Check question 4: $2,000 a month, 20 years, 6% stated ===")
c4 = pv_ann(2000, 0.005, 240)
show("annuity factor 0.5%, 240", "{:.6f}".format(af(0.005, 240)))
show("A (right) N = 240, I/Y = 0.5", "{!r} -> {}  BA PV = -{}".format(c4, money(c4), d4(c4)))
show("B (24,000 at each year-end, N = 20, 6%)", money(pv_ann(24000, 0.06, 20)))
show("C (2,000 with N = 20, I/Y = 6)", money(pv_ann(2000, 0.06, 20)))
show("annuity factor 6%, 20", "{:.6f}".format(af(0.06, 20)))

print("\n=== Extra numbers used in the prose ===")
show("c2 1.06^-20 (6 dp)", "{:.6f}".format(1.06 ** -20))
show("c2 11,469.92 x 0.311805", money(11469.92 * 0.311805))
show("c2 31.18% equals the discount factor 1.06^-20", "{:.2f}% vs {:.2f}%".format(100 * (a40 / a20 - 1), 100 * 1.06 ** -20))
show("c3 deposits, nearest dollar", "{:,.0f}".format(30 * c3))
show("c3 interest, nearest dollar", "{:,.0f}".format(500000 - 30 * c3))
show("story: PV as % of total paid, whole percent", "{:.0f}%".format(100 * pv_w / (120 * A)))
show("story: 30-year PV / 10-year PV, 2 dp", "{:.2f} times".format(pv30 / pv_w))
show("story: monthly rate as displayed (percent)", "{:.4f}%".format(4 / 12))
show("why: 0.21 / 0.10 x 100", "{:.2f}".format(100 * 0.21 / 0.10))
show("why: 110 + 100", "{:.2f}".format(110 + 100))
show("walkthrough: 10 x 12", 10 * 12)
show("c1: 5 x 12 ; c4: 20 x 12", "{} ; {}".format(5 * 12, 20 * 12))

print("\n=== Tolerances ===")
show("|19.56 - w1|", "{:.4f}".format(abs(19.56 - 100 * w1)))
show("|23276.50 - c1|", "{:.4f}".format(abs(23276.50 - c1)))
show("|7525.72 - c3|", "{:.4f}".format(abs(7525.72 - c3)))

print("\n=== Common mistakes (each must miss its tolerance of 0.01) ===")
mistakes = [
    ("w1 stated rate typed as the EAR", 18.0, 100 * w1),
    ("w1 continuous by mistake: e^0.18 - 1", 100 * (2.718281828459045 ** 0.18 - 1), 100 * w1),
    ("c1 annual N = 5, I/Y = 6", pv_ann(450, 0.06, 5), c1),
    ("c1 BGN left on", pv_ann(450, 0.005, 60) * 1.005, c1),
    ("c1 FV instead of PV", fv_ann(450, 0.005, 60), c1),
    ("c3 BGN left on", pmt_for_fv(500000, 0.05, 30) / 1.05, c3),
    ("c3 no interest: 500,000 / 30", 500000 / 30, c3),
]
for label, wrong, right in mistakes:
    show(label, "{} (off by {:,.2f}; rejected: {})".format(money(wrong), abs(wrong - right), abs(wrong - right) > 0.01))
