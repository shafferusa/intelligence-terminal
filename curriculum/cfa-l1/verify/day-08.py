#!/usr/bin/env python3
"""Day 8 of CFA Math: every number that appears on day-08.html.

Perpetuities: payments forever
(CFA Program 2027 Level I, Quantitative Methods, LM4 The Time Value of Money in Finance).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal BA II Plus display (thousands separators)."""
    return "{:,.4f}".format(x)


def t4(x):
    """A 4-decimal TI-84 display (no separators, no leading zero)."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def pct(x, dp=4):
    return "{:.{}f}%".format(100 * x, dp)


def show(label, value):
    print("  {:<66} {}".format(label, value))


def pv_factor(r, n):
    """PV of 1 a period for n periods, paid at the END of each period."""
    return (1 - (1 + r) ** -n) / r


def pv_ord(a, r, n):
    return a * pv_factor(r, n)


def pv_due(a, r, n):
    return a * pv_factor(r, n) * (1 + r)


def perp(a, r):
    """Level perpetuity, first payment one period from now."""
    return a / r


def deferred(a, r, t):
    """Level perpetuity whose first payment is at time t: A/r sits at t - 1."""
    return perp(a, r) / (1 + r) ** (t - 1)


def same(a, b, tol=1e-9):
    assert abs(a - b) <= tol, (a, b)
    return "OK"


print("=== Warm-up ===")
w1 = pv_due(4000, 0.06, 4)
show("w1  4-year factor at 6% (END)", "{:.6f}".format(pv_factor(0.06, 4)))
show("w1  x 1.06 (BGN factor)", "{:.6f}".format(pv_factor(0.06, 4) * 1.06))
show("w1  PV_due $4,000 at year-starts, 4 years, 6% (answer)", "{!r}  -> {}  (BA {} / TI {})".format(w1, money(w1), d4(-w1), t4(-w1)))
show("w1  rounded: 4,000 x 3.673012", money(4000 * 3.673012))
show("w1  ordinary (END) for contrast", money(pv_ord(4000, 0.06, 4)))
show("w1  split: 3-year factor at 6%", "{:.6f}".format(pv_factor(0.06, 3)))
show("w1  split: 4,000 + 4,000 x 2.673012", money(4000 + 4000 * 2.673012))
show("w1  check split", same(4000 + pv_ord(4000, 0.06, 3), w1))

f4 = 1.04 ** 20
f8 = 1.08 ** 20
show("w2  1.04^20 ; 1.08^20", "{:.6f} ; {:.6f}".format(f4, f8))
show("w2  $1,000 at 4% for 20 years (given)", money(1000 * f4))
show("w2  $1,000 at 8% for 20 years (B, right)", money(1000 * f8))
show("w2  interest at 4% ; at 8%", "{} ; {}".format(money(1000 * f4 - 1000), money(1000 * f8 - 1000)))
show("w2  A: 1,000 + twice the 4% interest", money(1000 + 2 * (1000 * f4 - 1000)))
show("w2  C: twice the 4% FV", money(2 * 1000 * f4))
show("w2  interest ratio 8% / 4%", "{:.2f}".format((f8 - 1) / (f4 - 1)))
show("w2  1.04^2 (one year at 4%, twice)", "{:.4f}".format(1.04 ** 2))
show("w2  calculator: 1.08^20 x 1000", "BA {}  TI {}".format(d4(1000 * f8), t4(1000 * f8)))

print("\n=== Story ===")
show("years 1917 -> 2015 ; 1932 -> 2015", "{} ; {}".format(2015 - 1917, 2015 - 1932))
show("coupon on 1,938.6 million at 3.5% (£ millions a year)", "{:.3f}  ({:.1f})".format(1938.6 * 0.035, 1938.6 * 0.035))
show("3.50 / 0.035 (coupon rate = market rate)", money(perp(3.5, 0.035)))
show("3.50 / 0.02639 (Dec 2014 record-low 30-year yield)", "{!r}  -> {}  ({})".format(perp(3.5, 0.02639), d4(perp(3.5, 0.02639)), money(perp(3.5, 0.02639))))
show("3.50 / 0.04", money(perp(3.5, 0.04)))
show("value above par at 2.639% (per £100)", money(perp(3.5, 0.02639) - 100))

print("\n=== The idea ===")
show("3.50 due in 100 years, at 2%", "{:.4f}".format(3.5 / 1.02 ** 100))
show("3.50 due in 300 years, at 2%", "{:.4f}".format(3.5 / 1.02 ** 300))
show("bank picture: 175 x 0.02", money(175 * 0.02))
show("implied rate: 3.50 / 100", pct(3.5 / 100, 2))

print("\n=== Why it works ===")
for n in (10, 100, 500):
    show("1.02^-%d" % n, "{:.6f}  ({:.7f})".format(1.02 ** -n, 1.02 ** -n))
for n in (30, 100, 500):
    show("£3.50 a year for %d years at 2%% (annuity PV)" % n, "{}  ({:.4f})".format(money(pv_ord(3.5, 0.02, n)), pv_ord(3.5, 0.02, n)))
show("forever: 3.50 / 0.02", money(perp(3.5, 0.02)))
show("one-line proof check: (A + P)/(1 + r) with P = 175", money((3.5 + 175) / 1.02))
show("4-year factor at 2%", "{:.6f}".format(pv_factor(0.02, 4)))
show("4 missing payments: 3.50 x 3.807729", "{:.4f}".format(3.5 * 3.807729))
show("175 - 13.3271", "{:.4f}".format(175 - 13.3271))
show("175 / 1.02^4", "{:.4f}".format(175 / 1.02 ** 4))
show("1.02^4", "{:.8f}".format(1.02 ** 4))
show("check: subtraction == discounting", same(175 - pv_ord(3.5, 0.02, 4), deferred(3.5, 0.02, 5)))
show("cap rate: 500,000 / 0.05", money(500000 / 0.05))

print("\n=== Worked example: $6 a year forever ===")
a = 6.0
show("(a) 6 / 0.08", money(perp(a, 0.08)))
show("(b) 6 / 80", "{:.4f}  -> {}".format(6 / 80, pct(6 / 80, 2)))
show("(c) 1.08^3", "{:.6f}".format(1.08 ** 3))
we = deferred(a, 0.08, 4)
show("(c) 75 / 1.259712 (answer)", "{!r}  -> {}".format(we, money(we)))
show("(c) rounded: 75 / 1.259712", "{:.4f}".format(75 / 1.259712))
show("(c) 3-year factor at 8%", "{:.6f}".format(pv_factor(0.08, 3)))
show("(c) 3 missing payments: 6 x 2.577097", "{:.4f}  ({})".format(6 * 2.577097, money(6 * 2.577097)))
show("(c) 75.00 - 15.46", money(75 - 15.46))
show("(c) check subtraction", same(75 - pv_ord(a, 0.08, 3), we))
show("trap: discounted 4 periods instead of 3", money(75 / 1.08 ** 4))
show("trap: perpetuity due 6 + 75 ; 75 x 1.08", "{} ; {}".format(money(6 + 75), money(75 * 1.08)))
show("trap: rate typed as a percent: 3.5 / 2", "{:.4f}".format(3.5 / 2))

print("\n=== Calculator walkthrough ===")
p2 = perp(3.5, 0.02)
show("step 2: 3.5 / 0.02", "BA {}  TI {}".format(d4(p2), t4(p2)))
p3 = perp(3.5, 0.02639)
show("step 3: 3.5 / 0.02639", "BA {}  TI {}".format(d4(p3), t4(p3)))
p500 = pv_ord(3.5, 0.02, 500)
show("step 4: N=500, I/Y=2, PMT=3.5, FV=0 -> PV", "{!r}  BA PV = {}  TI PV={}".format(-p500, d4(-p500), t4(-p500)))
show("step 4: gap to 175", "{:.4f}".format(175 - p500))
p5 = deferred(3.5, 0.02, 5)
show("step 5: N=4, I/Y=2, PMT=0, FV=175 -> PV (answer)", "{!r}  BA PV = {}  TI PV={}".format(-p5, d4(-p5), t4(-p5)))
a4 = pv_ord(3.5, 0.02, 4)
show("step 6: N=4, I/Y=2, PMT=3.5, FV=0 -> PV", "{!r}  BA PV = {}  TI PV={}".format(-a4, d4(-a4), t4(-a4)))
show("step 6: 175 - 13.3271 (typed display)", "BA {}  TI {}".format(d4(175 - 13.3271), t4(175 - 13.3271)))
show("lost by starting late: 175 - 161.6729", "{:.4f}  ({})".format(175 - p5, money(175 - p5)))

print("\n=== Back to the story ===")
show("at 2.639%: 132.63 ; at 3.5%: 100 ; at 4%: 87.50", "{} ; {} ; {}".format(money(p3), money(perp(3.5, 0.035)), money(perp(3.5, 0.04))))
show("whole issue at 2.639% (£ millions, for reference only)", "{:.1f}".format(1938.6 * p3 / 100))

print("\n=== Check questions ===")
c1 = deferred(5, 0.04, 7)
show("c1  5 / 0.04", "{}  (BA {} / TI {})".format(money(perp(5, 0.04)), d4(perp(5, 0.04)), t4(perp(5, 0.04))))
show("c1  1.04^6", "{:.6f}".format(1.04 ** 6))
show("c1  125 / 1.04^6 (answer)", "{!r}  -> {}  (BA PV = {} / TI PV={})".format(c1, money(c1), d4(-c1), t4(-c1)))
show("c1  wrong: discounted 7 periods", money(125 / 1.04 ** 7))
show("c1  6-year factor at 4%", "{:.6f}".format(pv_factor(0.04, 6)))
show("c1  check: 125 - 5 x 5.242137", "{:.4f}".format(125 - 5 * 5.242137))
show("c1  check subtraction", same(125 - pv_ord(5, 0.04, 6), c1))

show("c2  payments from year 101 on: (A/r)/1.05^100, share of A/r", "first one at t = {}".format(100 + 1))
show("c2  1/1.05^100 (share of a perpetuity's value after year 100)", "{:.6f}  -> {}".format(1.05 ** -100, pct(1.05 ** -100, 2)))
show("c2  each term = the one before x 1/1.05", "{:.6f}".format(1 / 1.05))

a3 = 80 * 0.05
c3 = perp(a3, 0.04)
show("c3  A = 80 x 0.05", "{}  (BA {} / TI {})".format(money(a3), d4(a3), t4(a3)))
show("c3  price at 4% = 4 / 0.04 (answer)", "{!r}  -> {}  (BA {} / TI {})".format(c3, money(c3), d4(c3), t4(c3)))
show("c3  price change", pct(c3 / 80 - 1, 2))
show("c3  rate change", pct(0.04 / 0.05 - 1, 2))

c4 = deferred(10, 0.05, 6)
show("c4  10 / 0.05", "{}  (BA {} / TI {})".format(money(perp(10, 0.05)), d4(perp(10, 0.05)), t4(perp(10, 0.05))))
show("c4  1.05^5 ; 1.05^6", "{:.6f} ; {:.6f}".format(1.05 ** 5, 1.05 ** 6))
show("c4  C (right): 200 / 1.05^5", "{!r}  -> {}  (BA PV = {} / TI PV={})".format(c4, money(c4), d4(-c4), t4(-c4)))
show("c4  A (wrong): 200 / 1.05^6", money(200 / 1.05 ** 6))
show("c4  B (wrong): no deferral", money(200))
show("c4  check: 200 - 10 x 5-year factor", "{:.6f}  -> {}".format(pv_factor(0.05, 5), money(200 - pv_ord(10, 0.05, 5))))
show("c4  check subtraction", same(200 - pv_ord(10, 0.05, 5), c4))

print("\n=== c5: no numbers ===")


def graded(answer, tol, typed, signless=True):
    """What cfa.js does: |typed - answer| <= tol (sizes only when signless)."""
    if signless:
        typed, answer = abs(typed), abs(answer)
    return abs(typed - answer) <= tol + 1e-12


print("\n=== Tolerances (typed at the precision asked; distractors must fail) ===")
for name, ans, tol, right, wrong in (
        ("w1", w1, 0.01, [14692.05, -14692.05, 14692.0478], [pv_ord(4000, 0.06, 4), 4000 * 4 * 1.06]),
        ("c1", c1, 0.01, [98.79, -98.79, 98.7893], [125 / 1.04 ** 7, 125, 5 / 0.04 / 1.04 ** 5]),
        ("c3", c3, 0.01, [100, 100.00, -100], [80 * 0.04 / 0.05, 80, 80 * 1.25 * 1.25])):
    assert all(graded(ans, tol, x) for x in right), name
    assert not any(graded(ans, tol, x) for x in wrong), name
    show("%s  accepts %s ; rejects %s" % (name, right, ["%.2f" % x for x in wrong]), "OK")
