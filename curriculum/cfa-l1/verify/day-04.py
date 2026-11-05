#!/usr/bin/env python3
"""Day 4 of CFA Math: every number that appears on day-04.html.

Stated, effective and continuous rates
(CFA Program 2027 Level I, Quantitative Methods, LM4 The Time Value of Money in Finance).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import math


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def ear(rs, m):
    """Effective annual rate from a stated annual rate rs compounded m times a year."""
    return (1 + rs / m) ** m - 1


def ear_cont(rs):
    return math.exp(rs) - 1


def nom(e, m):
    """Stated annual rate, compounded m times, that produces the effective rate e."""
    return m * ((1 + e) ** (1.0 / m) - 1)


def show(label, value):
    print("  {:<62} {}".format(label, value))


def pct(x, dp=4):
    return "{:.{}f}%".format(100 * x, dp)


print("=== Warm-up ===")
w1 = 1.08 / 1.05 - 1
show("w1  1.08 / 1.05", "{:.6f}".format(1.08 / 1.05))
show("w1  exact real return (answer, percent)", "{!r}  -> {}  ({})".format(100 * w1, pct(w1), pct(w1, 2)))
show("w1  BA/TI display of 1.08/1.05-1 (4 dp)", "{:.4f}  then x100 -> {:.4f}".format(w1, 100 * w1))
show("w1  shortcut 8 - 5", "3.00%")
w2 = 1.5 ** (1 / 5) - 1
show("w2  1.5^(1/5)", "{:.6f}".format(1.5 ** 0.2))
show("w2  implied annual rate (right)", "{!r}  -> {}  ({})".format(w2, pct(w2), pct(w2, 2)))
show("w2  distractor: simple 50% / 5", pct(0.5 / 5, 2))
show("w2  distractor: ln(1.5) / 5 (continuous rate)", "{}  ({})".format(pct(math.log(1.5) / 5), pct(math.log(1.5) / 5, 2)))
show("w2  ln(1.5)", "{:.6f}".format(math.log(1.5)))
show("w2  check: e^(ln1.5/5) - 1 equals the annual rate", pct(math.exp(math.log(1.5) / 5) - 1))

print("\n=== Story: Bernoulli, 1 lent at 100% a year ===")
for m in (1, 2, 4, 12, 365):
    g = (1 + 1.0 / m) ** m
    show("(1 + 1/{})^{}".format(m, m), "{:.6f}  -> {:.4f}".format(g, g))
show("1.5 x 1.5", "{:.4f}".format(1.5 * 1.5))
show("e (math.e)", "{!r}  -> {:.5f}".format(math.e, math.e))
show("e to 5 decimals, as the story prints it", "{:.5f}".format(math.e))
print("  Doubling the slices (each finer slice adds less):")
prev = None
for m in (1, 2, 4, 8, 16, 32, 64):
    g = (1 + 1.0 / m) ** m
    show("  m = {:<3} (1+1/m)^m".format(m), "{:.4f}{}".format(g, "" if prev is None else "   added {:.4f}".format(g - prev)))
    prev = g
print("  Interest by generations at 100% (continuous): 1/k!")
tot = 0.0
for k in range(0, 8):
    t = 1.0 / math.factorial(k)
    tot += t
    show("  generation {}: 1/{}! = {:.6f}".format(k, k, t), "running total {:.6f}".format(tot))
show("  bound 1 + 1 + 1/2 + 1/4 + 1/8 + ...", "3")
show("  1/6 < 1/4 ; 1/24 < 1/8 ; 1/120 < 1/16", "{} {} {}".format(1 / 6 < 1 / 4, 1 / 24 < 1 / 8, 1 / 120 < 1 / 16))

print("\n=== The idea: 12% stated at different frequencies ===")
for lab, m in (("annual", 1), ("semiannual", 2), ("quarterly", 4), ("monthly", 12), ("daily", 365)):
    show("12% {} -> EAR".format(lab), "{}  ({})".format(pct(ear(0.12, m)), pct(ear(0.12, m), 2)))
show("12% continuous -> EAR (also trap 4)", "{}  ({})".format(pct(ear_cont(0.12)), pct(ear_cont(0.12), 2)))
show("periodic rate 12%/12", pct(0.12 / 12, 2))
show("8% quarterly EAR vs 8.2% annual", "{} vs 8.2000%  ({} vs 8.20%)".format(pct(ear(0.08, 4)), pct(ear(0.08, 4), 2)))

print("\n=== Why it works ===")
show("1.01^12 (one year of 1% a month)", "{:.6f}".format(1.01 ** 12))
show("semiannual 12%: 100 x 1.06", "{:.2f}".format(100 * 1.06))
show("  second half interest: 6% of 106", "{:.2f}".format(0.06 * 106))
show("  year total 6 + 6.36; 100 x 1.06^2", "{:.2f}  {:.2f}".format(6 + 0.06 * 106, 100 * 1.06 ** 2))
show("  extra over 12: 6% of 6", "{:.2f}".format(0.06 * 6))
print("  8% by generations: r^k / k!")
tot = 0.0
for k in range(0, 5):
    t = 0.08 ** k / math.factorial(k)
    tot += t
    show("  generation {}: 0.08^{}/{}! = {:.7f}".format(k, k, k, t), "running total {:.7f}".format(tot))
show("  e^0.08", "{:.7f}".format(math.exp(0.08)))
print("  substitution check: (1 + 0.08/m)^m = [(1 + 1/n)^n]^0.08 with n = m/0.08")
for m in (4, 12, 365, 1000000):
    n = m / 0.08
    show("  m = {}".format(m), "{:.7f}  vs  {:.7f}".format((1 + 0.08 / m) ** m, ((1 + 1 / n) ** n) ** 0.08))

print("\n=== Worked example: 8% stated, $10,000, 3 years ===")
q = 0.08 / 4
show("periodic rate 8%/4", pct(q, 2))
show("1.02^4", "{:.6f}".format(1.02 ** 4))
show("(a) EAR quarterly", "{!r}  -> {}".format(ear(0.08, 4), pct(ear(0.08, 4))))
show("1.02^12", "{:.6f}".format(1.02 ** 12))
fvq = 10000 * 1.02 ** 12
show("(b) FV quarterly = 10,000 x 1.02^12", "{!r}  -> {}".format(fvq, money(fvq)))
show("    check via EAR: 10,000 x 1.082432^3", money(10000 * (1 + ear(0.08, 4)) ** 3))
show("    wrong way: 10,000 x 1.08^3 (annual)", money(10000 * 1.08 ** 3))
show("e^0.08", "{:.6f}".format(math.exp(0.08)))
show("(c) EAR continuous", "{!r}  -> {}".format(ear_cont(0.08), pct(ear_cont(0.08))))
show("e^0.24", "{:.6f}".format(math.exp(0.24)))
fvc = 10000 * math.exp(0.08 * 3)
show("(c) FV continuous = 10,000 x e^0.24", "{!r}  -> {}".format(fvc, money(fvc)))
show("continuous minus quarterly", money(fvc - fvq))
show("BA/TI 4-dp display, FV quarterly via TVM (N=12, I/Y=2)", d4(fvq))
show("BA/TI 4-dp display, FV continuous", d4(fvc))

print("\n=== Calculator walkthrough ===")
a12 = ear(1.0, 12)
show("100% compounded monthly: EFF (answer)", "{!r}  -> {}".format(100 * a12, d4(100 * a12)))
a365 = ear(1.0, 365)
show("100% compounded daily: EFF", "{!r}  -> {}".format(100 * a365, d4(100 * a365)))
show("1 2ND e^x", "{:.4f}".format(math.e))
show("e^1 - 1", "{:.4f}".format(math.e - 1))
show("(e^1 - 1) x 100", "{:.4f}".format(100 * (math.e - 1)))
b = ear(0.12, 12)
show("12% compounded monthly: EFF", "{!r}  -> {}".format(100 * b, d4(100 * b)))
show("back: NOM from EFF 12.6825..., C/Y 12", d4(100 * nom(b, 12)))

print("\n=== Back to the story: 100% stated as an EAR ===")
for lab, m in (("annually", 1), ("semiannually", 2), ("quarterly", 4), ("monthly", 12), ("daily", 365)):
    show("100% {}".format(lab), "{}  ({})".format(pct(ear(1.0, m)), pct(ear(1.0, m), 2)))
show("100% continuously (e - 1)", "{}  ({})".format(pct(ear_cont(1.0)), pct(ear_cont(1.0), 2)))
show("daily -> continuous adds (points)", "{:.4f}  ({:.2f})".format(100 * (ear_cont(1.0) - a365), 100 * (ear_cont(1.0) - a365)))
show("monthly -> daily adds (points)", "{:.4f}  ({:.2f})".format(100 * (a365 - a12), 100 * (a365 - a12)))
show("8% stated: continuous ceiling", "{}  ({})".format(pct(ear_cont(0.08)), pct(ear_cont(0.08), 2)))

print("\n=== Check questions ===")
c1 = ear(0.06, 12)
show("c1  periodic rate 6%/12", pct(0.06 / 12, 1))
show("c1  1.005^12", "{:.6f}".format(1.005 ** 12))
show("c1  6% monthly -> EAR (answer, percent)", "{!r}  -> {}  ({})".format(100 * c1, d4(100 * c1), pct(c1, 2)))
show("c2  10% monthly EAR", pct(ear(0.10, 12)))
show("c2  10% daily EAR", pct(ear(0.10, 365)))
show("c2  10% continuous EAR (e^0.10 - 1)", "{}  ({})".format(pct(ear_cont(0.10)), pct(ear_cont(0.10), 2)))
show("c2  e^0.10", "{:.4f}".format(math.exp(0.10)))
show("c2  generations 0.10, 0.10^2/2, 0.10^3/6", "{:.4f} {:.4f} {:.6f}".format(0.10, 0.01 / 2, 0.001 / 6))
c3 = nom(0.09, 4)
show("c3  1.09^(1/4)", "{:.6f}".format(1.09 ** 0.25))
show("c3  quarterly rate 1.09^(1/4) - 1", "{:.6f}".format(1.09 ** 0.25 - 1))
show("c3  stated quarterly for EAR 9% (answer, percent)", "{!r}  -> {}  ({})".format(100 * c3, d4(100 * c3), pct(c3, 2)))
show("c3  check: (1 + c3/4)^4 - 1", pct(ear(c3, 4)))
show("c3  continuous: ln(1.09)", "{!r}  -> {:.4f} -> {}".format(math.log(1.09), math.log(1.09), pct(math.log(1.09), 2)))
show("c3  monthly for EAR 9% (for contrast)", pct(nom(0.09, 12), 2))
c4 = 5000 * 1.0075 ** 48
show("c4  1.0075^48", "{:.6f}".format(1.0075 ** 48))
show("c4  B right: 5,000 x 1.0075^48", "{!r}  -> {}  (4dp {})".format(c4, money(c4), d4(c4)))
show("c4  A annual: 5,000 x 1.09^4", "{}  (1.09^4 = {:.6f})".format(money(5000 * 1.09 ** 4), 1.09 ** 4))
show("c4  C N=4: 5,000 x 1.0075^4", "{}  (1.0075^4 = {:.6f})".format(money(5000 * 1.0075 ** 4), 1.0075 ** 4))
show("c4  EAR of 9% monthly", "{}  ({})".format(pct(ear(0.09, 12)), pct(ear(0.09, 12), 2)))
show("c4  check via EAR: 5,000 x (1+EAR)^4", money(5000 * (1 + ear(0.09, 12)) ** 4))
show("c4  monthly rate 9%/12", pct(0.09 / 12, 2))

print("\n=== Rounded intermediates as written on the page (must reproduce the shown result) ===")
show("10,000 x 1.268242 (worked b)", money(10000 * 1.268242))
show("10,000 x 1.082432^3 (worked b, via EAR, rounded factor)", money(10000 * 1.082432 ** 3))
show("1.02^4 to 8 dp (exact)", "{:.8f}".format(1.02 ** 4))
show("10,000 x 1.08243216^3 (worked b, via exact EAR factor)", money(10000 * 1.08243216 ** 3))
show("10,000 x 1.271249 (worked c)", money(10000 * 1.271249))
show("1.25^4 (story: a quarter every three months)", "{:.4f}".format(1.25 ** 4))
show("daily vs monthly credits: 365 / 12", "{:.2f}".format(365 / 12))
show("w1  1.028571 - 1 -> percent", "{:.2f}%".format(100 * (1.028571 - 1)))
show("w2  1.084472 - 1 -> percent", "{:.2f}%".format(100 * (1.084472 - 1)))
show("c1  1.061678 - 1 -> percent", "{:.2f}%".format(100 * (1.061678 - 1)))
show("c3  4 x 0.021778", "{:.4f}  -> {:.2f}%".format(4 * 0.021778, 400 * 0.021778))
show("c4  1.0075^48 to 7 dp", "{:.7f}".format(1.0075 ** 48))
show("c4  5,000 x 1.4314053", money(5000 * 1.4314053))
show("c4  5,000 x 1.411582 (A)", money(5000 * 1.411582))
show("c4  5,000 x 1.030339 (C, 6 dp factor: one cent short)", money(5000 * 1.030339))
show("c4  1.0075^4 to 7 dp", "{:.7f}".format(1.0075 ** 4))
show("c4  5,000 x 1.0303392 (C)", money(5000 * 1.0303392))
show("c4  5,000 x 1.093807^4 (via EAR, rounded factor)", money(5000 * 1.093807 ** 4))
show("c2  0.10 + 0.0050 + 0.000167", "{:.6f}".format(0.10 + 0.005 + 0.000167))

print("\n=== Traps (own numbers, not the check questions') ===")
show("trap 1: 8% quarterly EAR vs 8.2% annual", "{} vs 8.20%".format(pct(ear(0.08, 4), 2)))
show("trap 2: 12% monthly, 3 years -> periods, periodic rate", "{} periods at {}".format(12 * 3, pct(0.12 / 12, 0)))
show("trap 3 right: N=36, I/Y=1, P/Y=C/Y=1: 1,000 x 1.01^36", money(1000 * 1.01 ** 36))
show("trap 3 wrong: P/Y=C/Y=12 too: 1,000 x (1 + 0.01/12)^36", money(1000 * (1 + 0.01 / 12) ** 36))
show("trap 4: continuous ceiling for 8% and 12%", "{}  {}".format(pct(ear_cont(0.08), 2), pct(ear_cont(0.12), 2)))

print("\n=== Grading check (cfa.js rule: |typed - answer| <= tol, data-pct also tries x100) ===")


def accepts(answer, tol, typed, is_pct=True):
    cands = [typed] + ([typed * 100] if is_pct else [])
    return any(abs(c - answer) <= tol + 1e-12 for c in cands)


for name, answer, ok, bad in (
        ("w1", 100 * w1, (2.86, 2.8571, 0.0286), (3.00,)),
        ("c1", 100 * c1, (6.17, 6.1678, 0.0617), (6.00, 6.09, 6.18)),
        ("c3", 100 * c3, (8.71, 8.7113, 0.0871), (8.62, 8.65, 9.00, 9.31))):
    show("{} accepts {} / rejects {}".format(name, ok, bad),
         all(accepts(answer, 0.01, t) for t in ok) and not any(accepts(answer, 0.01, t) for t in bad))
show("  (6.18 = 6% continuous, 6.09 = 6% semiannual; 8.62 = ln 1.09, 8.65 = monthly, 9.31 = 9% quarterly EAR)",
     "{} {} {} {} {}".format(pct(ear_cont(0.06), 2), pct(ear(0.06, 2), 2), pct(math.log(1.09), 2),
                             pct(nom(0.09, 12), 2), pct(ear(0.09, 4), 2)))
