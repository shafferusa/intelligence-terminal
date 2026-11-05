#!/usr/bin/env python3
"""Day 12 of CFA Math: every number that appears on day-12.html.

Arithmetic, geometric and harmonic means
(CFA Program 2027 Level I, Quantitative Methods, LM2 Types of Financial Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math
from decimal import Decimal, ROUND_HALF_UP


def money(x):
    """Dollars to the cent, rounding half up on the shortest decimal form of x
    (so 6382.695, which a float stores as 6382.69499..., shows as 6,382.70)."""
    return "{:,}".format(Decimal(repr(round(x, 9))).quantize(Decimal("0.01"), ROUND_HALF_UP))


def d4(x):
    """A 4-decimal BA II Plus display (thousands separators, leading zero)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """A TI-84 FLOAT 4 display: no thousands separators, no leading zero below 1."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def pct(x, nd=2):
    return "{:.{}f}%".format(100 * x, nd)


def arith(rs):
    return sum(rs) / len(rs)


def product(factors):
    p = 1.0
    for f in factors:
        p *= f
    return p


def geo(rs):
    return product(1 + r for r in rs) ** (1.0 / len(rs)) - 1


def harmonic(xs):
    return len(xs) / sum(1.0 / x for x in xs)


def pop_var(xs):
    m = arith(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def sample_var(xs):
    m = arith(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def tvm_rate(n, pv, fv):
    """I/Y in percent with PMT = 0 (the calculator's CPT I/Y)."""
    return ((fv / -pv) ** (1.0 / n) - 1) * 100


def show(label, value):
    print("  {:<66} {}".format(label, value))


# ---------------------------------------------------------------- warm-up
print("=== Warm-up w1 (from Day 11): +18%, -25%, +12% chained ===")
w1_rs = [0.18, -0.25, 0.12]
w1_factor = product(1 + r for r in w1_rs)
w1 = w1_factor - 1
show("factor 1.18 x 0.75 x 1.12", "{:.4f} (BA {} / TI {})".format(w1_factor, d4(w1_factor), ti4(w1_factor)))
show("minus 1", "BA {} / TI {}".format(d4(w1), ti4(w1)))
show("times 100", "BA {} / TI {}".format(d4(w1 * 100), ti4(w1 * 100)))
show("3-year return (data-answer, percent)", repr(round(w1 * 100, 10)))
show("3-year return", pct(w1))
show("sum of the returns (the adding mistake)", pct(sum(w1_rs)))
show("1.18 x 0.75 (after two years)", "{:.4f}".format(1.18 * 0.75))

print("\n=== Warm-up w2 (from Day 2): $5,000 -> $8,000 in 6 years ===")
w2 = (8000 / 5000) ** (1 / 6) - 1
show("FV/PV", "{:.4f}".format(8000 / 5000))
show("implied rate (1.6)^(1/6) - 1  [C, right]", pct(w2) + "  ({:.6f})".format(w2))
show("1.6^(1/6)", "{:.4f}".format(1 + w2))
show("CPT I/Y display", "BA I/Y = {} / TI I%={}".format(d4(tvm_rate(6, -5000, 8000)), ti4(tvm_rate(6, -5000, 8000))))
show("simple: 60% / 6  [A]", pct(0.60 / 6))
show("continuous: ln(1.6)/6  [B]", pct(math.log(1.6) / 6))
show("check: 5000 x 1.081484^6", money(5000 * (1 + w2) ** 6))

# ---------------------------------------------------------------- story
print("\n=== Story: ARKK 2020-2022 (rounded yearly returns) ===")
ark = [1.525, -0.234, -0.670]
ark_factors = [1 + r for r in ark]
ark_sum = sum(ark)
ark_mean = arith(ark)
ark_prod = product(ark_factors)
ark_geo = geo(ark)
start = 10000.0
show("growth factors", ", ".join("{:.3f}".format(f) for f in ark_factors))
show("sum of returns (percent)", "{:.1f}".format(ark_sum * 100))
show("arithmetic mean", pct(ark_mean) + "  ({:.4f} as BA x-bar)".format(ark_mean * 100))
show("product of growth factors", "{:.7f}  (BA {} / TI {})".format(ark_prod, d4(ark_prod), ti4(ark_prod)))
show("cube root of the product", "{:.6f}  (BA {} / TI {})".format(ark_prod ** (1 / 3), d4(ark_prod ** (1 / 3)), ti4(ark_prod ** (1 / 3))))
show("geometric mean minus-1 step", "BA {} / TI {}".format(d4(ark_geo), ti4(ark_geo)))
show("geometric mean (percent, x100)", "BA {} / TI {}".format(d4(ark_geo * 100), ti4(ark_geo * 100)))
show("geometric mean", pct(ark_geo) + "  (full: {!r})".format(ark_geo * 100))
end = start * ark_prod
show("$10,000 ends at", "{}  (BA {} / TI {})".format(money(end), d4(end), "{:.4f}".format(end)))
show("ending value rounded to dollars", "{:,}".format(Decimal(repr(round(end, 9))).quantize(Decimal("1"), ROUND_HALF_UP)))
show("loss over three years", pct(end / start - 1))
path_actual = [start]
for f in ark_factors:
    path_actual.append(path_actual[-1] * f)
show("actual path", " -> ".join(money(v) for v in path_actual))
path_steady = [start]
for _ in ark:
    path_steady.append(path_steady[-1] * (1 + ark_geo))
show("steady path at the geometric mean", " -> ".join(money(v) for v in path_steady))
show("steady path, whole dollars", " -> ".join("{:,.0f}".format(v) for v in path_steady))
path_arith = start * (1 + ark_mean) ** 3
show("$10,000 at the arithmetic mean for 3 years", money(path_arith))
show("gap arithmetic - geometric (points)", "{:.2f}".format((ark_mean - ark_geo) * 100))
show("back to the story: +152.5% and -67.0% added (sounds like a gain)", "{:+.1f}".format(152.5 - 67.0))
show("back to the story: $1 x 2.525 x 0.33 (the two years compounded)", "{:.5f} -> ${:.2f}".format(2.525 * 0.33, 2.525 * 0.33))
show("data entry: last line X03", "BA {} / TI {:g}".format(d4(-67.0), -67.0))
show("1-V: n", "{} (BA {})".format(len(ark), d4(len(ark))))
show("1-V: x-bar", "BA {} / TI {}".format(d4(ark_mean * 100), ti4(ark_mean * 100)))
show("1-V: Sum x", d4(ark_sum * 100))
show("1-V: Sx (sample SD, percent)", d4(math.sqrt(sample_var([r * 100 for r in ark]))))
show("1-V: sigma x (population SD, percent)", d4(math.sqrt(pop_var([r * 100 for r in ark]))))
show("TVM check: N=3, PV=-10000, FV=6382.695, CPT I/Y", "BA {} / TI {}".format(d4(tvm_rate(3, -10000, 6382.695)), ti4(tvm_rate(3, -10000, 6382.695))))
show("trap: cube root of 152.5 x (-23.4) x (-67.0)", "{:,.1f} -> root {:.2f}".format(152.5 * -23.4 * -67.0, (152.5 * -23.4 * -67.0) ** (1 / 3)))

# ---------------------------------------------------------------- the idea
print("\n=== The idea: harmonic picture (60 miles out at 30 mph, back at 60 mph) ===")
show("time out, time back (hours)", "{:.0f}, {:.0f}".format(60 / 30, 60 / 60))
show("average speed = 120 / 3", "{:.0f}".format(120 / (60 / 30 + 60 / 60)))
show("harmonic mean of 30 and 60", "{:.4f}".format(harmonic([30, 60])))
show("arithmetic mean of 30 and 60", "{:.0f}".format(arith([30, 60])))

# ---------------------------------------------------------------- why it works
print("\n=== Why it works: +50% then -50%; the sigma^2/2 rule ===")
show("$100 -> after +50% -> after -50%", "100 -> {:.0f} -> {:.0f}".format(100 * 1.5, 100 * 1.5 * 0.5))
show("c2: the other order, $100 -> after -50% -> after +50%", "100 -> {:.0f} -> {:.0f}".format(100 * 0.5, 100 * 0.5 * 1.5))
show("c2: 1.5 x 0.5", "{:.2f}".format(1.5 * 0.5))
show("arithmetic of +50, -50", pct(arith([0.5, -0.5])))
show("geometric of +50, -50: sqrt(0.75) - 1", pct(geo([0.5, -0.5])) + "  ({:.6f})".format(geo([0.5, -0.5])))
show("approx: 0 - 0.50^2/2", pct(0 - 0.5 ** 2 / 2, 1))
show("arithmetic of +10, -10", pct(arith([0.1, -0.1])))
show("geometric of +10, -10: sqrt(0.99) - 1", pct(geo([0.1, -0.1]), 4))
show("approx: 0 - 0.10^2/2", pct(0 - 0.1 ** 2 / 2, 4))
show("ARKK: sum 62.1 / 3", "{:.1f}".format(62.1 / 3))

# ---------------------------------------------------------------- worked example
print("\n=== Worked example ===")
prices_a = [20.0, 25.0, 40.0]
spend = 1200.0
shares = [spend / p for p in prices_a]
show("(a) shares bought", ", ".join("{:g}".format(s) for s in shares))
show("(a) total shares, total spent", "{:g}, {}".format(sum(shares), money(spend * 3)))
show("(a) average cost = 3600/138", "{:.4f}".format(spend * 3 / sum(shares)))
show("(a) 1/20 + 1/25 + 1/40", "{:.4f}".format(sum(1 / p for p in prices_a)))
show("(a) harmonic mean 3/0.115", "{:.4f}".format(harmonic(prices_a)))
show("(a) arithmetic mean of prices", "{:.4f}".format(arith(prices_a)))
show("(a) geometric mean of prices (for the ordering)", "{:.4f}".format(product(prices_a) ** (1 / 3)))
show("(a) equal SHARES (10 at each price): cost per share", "{:.4f}".format(sum(10 * p for p in prices_a) / 30))
show("(b) arithmetic of +50, -50", pct(0.0))
show("(b) 1.50 x 0.50", "{:.2f}".format(1.5 * 0.5))
show("(b) sqrt(0.75)", "{:.4f}".format(0.75 ** 0.5))
show("(b) geometric", pct(geo([0.5, -0.5])))

# ---------------------------------------------------------------- check questions
print("\n=== c1: +25%, -10%, +30%, -20% ===")
c1 = [0.25, -0.10, 0.30, -0.20]
c1_prod = product(1 + r for r in c1)
c1_geo = geo(c1)
show("1-V n", "BA {} / TI {}".format(d4(len(c1)), len(c1)))
show("1-V x-bar (arithmetic, percent)", "BA {} / TI {}".format(d4(arith(c1) * 100), ti4(arith(c1) * 100)))
show("product 1.25 x 0.9 x 1.3 x 0.8", "BA {} / TI {}".format(d4(c1_prod), ti4(c1_prod)))
show("fourth root", "BA {} / TI {}".format(d4(c1_prod ** 0.25), ti4(c1_prod ** 0.25)))
show("minus 1", "BA {} / TI {}".format(d4(c1_geo), ti4(c1_geo)))
show("times 100", "BA {} / TI {}".format(d4(c1_geo * 100), ti4(c1_geo * 100)))
show("geometric mean (data-answer)", repr(c1_geo * 100))
show("geometric mean", pct(c1_geo))
show("$1 after four years", "{:.4f}".format(c1_prod))
show("1.040031^4 check", "{:.4f}".format((1 + c1_geo) ** 4))
show("TVM check N=4 PV=-1 FV=1.17 CPT I/Y", d4(tvm_rate(4, -1, 1.17)))
show("gap arithmetic - geometric (points)", "{:.2f}".format((arith(c1) - c1_geo) * 100))

print("\n=== c3: $500 a quarter at $10, $12.50, $8, $16 ===")
c3_prices = [10.0, 12.5, 8.0, 16.0]
c3_shares = [500 / p for p in c3_prices]
show("reciprocals", ", ".join("{:g}".format(1 / p) for p in c3_prices))
show("sum of reciprocals", "BA {} / {:.4f}".format(d4(sum(1 / p for p in c3_prices)), sum(1 / p for p in c3_prices)))
show("1/sum", d4(1 / sum(1 / p for p in c3_prices)))
show("harmonic mean (data-answer)", repr(harmonic(c3_prices)))
show("harmonic mean", "{:.4f} -> {}".format(harmonic(c3_prices), money(harmonic(c3_prices))))
show("shares bought", ", ".join("{:g}".format(s) for s in c3_shares) + " = {:g}".format(sum(c3_shares)))
show("2,000 / shares", "{:.4f}".format(2000 / sum(c3_shares)))
show("arithmetic mean of prices (trap)", "{:.4f}".format(arith(c3_prices)))

print("\n=== c4: 10%, -5%, 20% ===")
c4 = [0.10, -0.05, 0.20]
c4_prod = product(1 + r for r in c4)
show("product 1.10 x 0.95 x 1.20", "{:.4f}".format(c4_prod))
show("cube root", "{:.6f} (BA {} / TI {})".format(c4_prod ** (1 / 3), d4(c4_prod ** (1 / 3)), ti4(c4_prod ** (1 / 3))))
show("product display", "BA {} / TI {}".format(d4(c4_prod), ti4(c4_prod)))
show("minus 1 display", "BA {} / TI {}".format(d4(geo(c4)), ti4(geo(c4))))
show("A: geometric mean (right)", pct(geo(c4)) + "  ({:.6f})".format(geo(c4)))
show("B: arithmetic mean", pct(arith(c4)))
show("C: 3-year total, root not taken", pct(c4_prod - 1))
show("1.078364^3 check", "{:.4f}".format((1 + geo(c4)) ** 3))

print("\n=== Ordering check (every mean on the page) ===")
assert harmonic(prices_a) < product(prices_a) ** (1 / 3) < arith(prices_a)
assert ark_geo < ark_mean and c1_geo < arith(c1) and geo(c4) < arith(c4)
assert abs(tvm_rate(3, -10000, 6382.695) - ark_geo * 100) < 1e-9
assert abs(harmonic(c3_prices) - 2000 / sum(c3_shares)) < 1e-12
assert abs(harmonic(prices_a) - 3600 / 138) < 1e-12
show("harmonic < geometric < arithmetic for $20, $25, $40", "{:.4f} < {:.4f} < {:.4f}".format(
    harmonic(prices_a), product(prices_a) ** (1 / 3), arith(prices_a)))
print("  all assertions passed")
