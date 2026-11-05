#!/usr/bin/env python3
"""Day 6 of CFA Math: every number that appears on day-06.html.

Annuity due: payments in advance
(CFA Program 2027 Level I, Quantitative Methods, LM4 The Time Value of Money in Finance).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def t4(x):
    """A 4-decimal TI-84 display (no separators)."""
    return "{:.4f}".format(x)


def pct(x, dp=4):
    return "{:.{}f}%".format(100 * x, dp)


def show(label, value):
    print("  {:<66} {}".format(label, value))


def pv_factor(r, n):
    """PV of 1 a period for n periods, paid at the END of each period."""
    return (1 - (1 + r) ** -n) / r


def fv_factor(r, n):
    """FV at t = n of 1 a period for n periods, paid at the END of each period."""
    return ((1 + r) ** n - 1) / r


def pv_ord(a, r, n):
    return a * pv_factor(r, n)


def pv_due(a, r, n):
    return a * pv_factor(r, n) * (1 + r)


def fv_ord(a, r, n):
    return a * fv_factor(r, n)


def fv_due(a, r, n):
    return a * fv_factor(r, n) * (1 + r)


def solve_rate(f, target, lo=1e-9, hi=1.0):
    """Bisection: the rate r with f(r) == target, f decreasing in r."""
    for _ in range(300):
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def same(a, b, tol=1e-9):
    assert abs(a - b) <= tol, (a, b)
    return "OK"


print("=== Warm-up ===")
w1 = pv_ord(2000, 0.08, 5)
show("w1  1.08^-5", "{:.6f}".format(1.08 ** -5))
show("w1  factor (1 - 1.08^-5) / 0.08", "{:.6f}".format(pv_factor(0.08, 5)))
show("w1  PV, $2,000 at year-ends, 5 years, 8% (answer)", "{!r}  -> {}  (BA {} / TI {})".format(w1, money(w1), d4(-w1), t4(-w1)))
show("w1  rounded check 2,000 x 3.992710", money(2000 * 3.992710))
show("w1  teaser: same stream paid at the START (x 1.08)", "{}  ({})".format(money(w1 * 1.08), money(pv_due(2000, 0.08, 5))))
w2x = (1 + 0.05 / 4) ** 4 - 1
show("w2  X: 5% compounded quarterly -> EAR", "{}  ({})  1.0125^4 = {:.6f}".format(pct(w2x), pct(w2x, 2), 1.0125 ** 4))
show("w2  ICONV/►Eff display", "{}".format(t4(100 * w2x)))
show("w2  Y: 5.1% compounded annually -> EAR", pct(0.051))
show("w2  Y beats X by (percentage points)", "{:.4f}".format(100 * (0.051 - w2x)))
show("w2  Y beats X on $10,000 over a year ($)", money(10000 * (0.051 - w2x)))

print("\n=== Story ===")
show("30 x 68 = 2,040 ($ millions)", "{}".format(30 * 68))
show("cash / headline = 997.6 / 2,040", "{:.4f}  -> {}".format(997.6 / 2040, pct(997.6 / 2040, 1)))
show("annuity: 1 payment now + 29 more", "{}".format(1 + 29))

print("\n=== The idea: the warm-up's annuity, paid in advance ===")
show("7,985.42 x 1.08", money(7985.42 * 1.08))
show("exact: PV_due(2,000, 8%, 5)", money(pv_due(2000, 0.08, 5)))

print("\n=== Why it works: $1,000 a year, 3 years, 10% ===")
r, a = 0.10, 1000.0
ord_terms = [a / (1 + r) ** t for t in (1, 2, 3)]
due_terms = [a / (1 + r) ** t for t in (0, 1, 2)]
show("ordinary terms (t = 1, 2, 3)", "  ".join(money(x) for x in ord_terms))
show("ordinary sum", money(sum(ord_terms)))
show("sum of the ROUNDED ordinary terms", money(sum(round(x, 2) for x in ord_terms)))
show("due terms (t = 0, 1, 2)", "  ".join(money(x) for x in due_terms))
show("due sum", money(sum(due_terms)))
show("sum of the ROUNDED due terms", money(sum(round(x, 2) for x in due_terms)))
show("909.09 x 1.1 ; 826.45 x 1.1 ; 751.31 x 1.1", "{} ; {} ; {}".format(money(909.09 * 1.1), money(826.45 * 1.1), money(751.31 * 1.1)))
show("ordinary sum x 1.1 (exact)", money(sum(ord_terms) * 1.1))
show("2,486.85 x 1.1 (rounded)", "{:.4f}".format(2486.85 * 1.1))
show("check due == ordinary x (1 + r)", same(sum(due_terms), sum(ord_terms) * 1.1))
show("formula: 1,000 x (1 - 1.1^-3)/0.1", "{}  factor {:.6f}".format(money(pv_ord(a, r, 3)), pv_factor(r, 3)))
show("split: 1 + 2-payment factor", "{:.6f}".format(pv_factor(r, 2)))
show("split: 1,000 + 1,000 x 1.735537", money(1000 + 1000 * 1.735537))
show("check split == due", same(a + pv_ord(a, r, 2), sum(due_terms)))
show("FV ordinary: 1,000 x (1.1^3 - 1)/0.1", "{}  factor {:.4f}".format(money(fv_ord(a, r, 3)), fv_factor(r, 3)))
fv_terms_due = [a * (1 + r) ** t for t in (3, 2, 1)]
fv_terms_ord = [a * (1 + r) ** t for t in (2, 1, 0)]
show("FV ordinary terms (grow 2, 1, 0 years)", "  ".join(money(x) for x in fv_terms_ord))
show("FV due terms (grow 3, 2, 1 years)", "  ".join(money(x) for x in fv_terms_due) + "  sum " + money(sum(fv_terms_due)))
show("FV due = 3,310 x 1.1", money(fv_due(a, r, 3)))
show("check FV due == sum of terms", same(fv_due(a, r, 3), sum(fv_terms_due)))

print("\n=== Worked example: rent $1,200 at the start of each year, 3 years, 5% ===")
r, a = 0.05, 1200.0
terms = [a / (1 + r) ** t for t in (0, 1, 2)]
show("terms 1,200 / 1.05^t, t = 0, 1, 2 (4 dp)", "  ".join(d4(x) for x in terms))
show("sum (4 dp)", d4(sum(terms)))
show("sum of 4-dp terms", d4(sum(round(x, 4) for x in terms)))
show("PV_due (answer)", money(pv_due(a, r, 3)))
show("1.05^-3 ; 3-year factor", "{:.6f} ; {:.6f}".format(1.05 ** -3, pv_factor(r, 3)))
show("PV_ordinary = 1,200 x 2.723248", "{}  (rounded factor {})".format(money(pv_ord(a, r, 3)), money(1200 * 2.723248)))
show("ratio PV_due / PV_ordinary", "{:.4f}".format(pv_due(a, r, 3) / pv_ord(a, r, 3)))
show("rounded ratio 3,431.29 / 3,267.90", "{:.4f}".format(3431.29 / 3267.90))
show("3,267.90 x 1.05", "{:.4f}".format(3267.90 * 1.05))
show("2-year factor", "{:.6f}".format(pv_factor(r, 2)))
show("split: 1,200 + 1,200 x 1.859410", money(1200 + 1200 * 1.859410))
show("check split", same(a + pv_ord(a, r, 2), pv_due(a, r, 3)))
show("FV_due at end of year 3 = 3,431.29 x 1.05^3", "{}  exact {}".format(money(3431.29 * 1.05 ** 3), money(pv_due(a, r, 3) * 1.05 ** 3)))
show("1.05^3", "{:.6f}".format(1.05 ** 3))
fvt = [a * 1.05 ** t for t in (3, 2, 1)]
show("FV_due terms 1,200 x 1.05^3, ^2, ^1", "  ".join(money(x) for x in fvt) + "  sum " + money(sum(fvt)))
show("FV_due by formula", money(fv_due(a, r, 3)))
show("trap: rent as ordinary (too low)", money(pv_ord(a, r, 3)))
show("trap: (1 + r) twice: 3,431.29 x 1.05", "{}  exact {}".format(money(3431.29 * 1.05), money(pv_due(a, r, 3) * 1.05)))

print("\n=== Calculator walkthrough: Powerball simplified, $ millions ===")
a, r, n = 68.0, 0.04, 30
due = pv_due(a, r, n)
ordv = pv_ord(a, r, n)
show("30-year factor at 4% (END)", "{:.6f}".format(pv_factor(r, n)))
show("x 1.04 (BGN factor)", "{:.6f}".format(pv_factor(r, n) * 1.04))
show("BGN: PV (walkthrough answer)", "{!r}  -> BA PV = {}  TI PV={}".format(-due, d4(-due), t4(-due)))
show("END: PV", "{!r}  -> BA PV = {}  TI PV={}".format(-ordv, d4(-ordv), t4(-ordv)))
show("difference BGN - END", "{}  ({:.1f})".format(d4(due - ordv), due - ordv))
show("ratio BGN / END", "{:.4f}".format(due / ordv))
show("ratio from 4-dp displays 1,222.8926 / 1,175.8583", "{:.4f}".format(1222.8926 / 1175.8583))
show("0.04 x END value (= the difference)", d4(0.04 * ordv))
rate_bgn = solve_rate(lambda x: pv_due(a, x, n), 997.6)
rate_end = solve_rate(lambda x: pv_ord(a, x, n), 997.6)
show("BGN: I/Y that makes PV = 997.6", "{!r}  -> {}".format(100 * rate_bgn, t4(100 * rate_bgn)))
show("check PV_due at that rate", d4(pv_due(a, rate_bgn, n)))
show("END: I/Y that makes PV = 997.6 (for contrast)", "{} ({})".format(t4(100 * rate_end), pct(rate_end, 2)))
f29 = pv_factor(r, 29)
show("split: 29-year factor at 4%", "{:.6f}".format(f29))
show("split: END, N = 29: PV", "BA {}  TI {}".format(d4(-a * f29), t4(-a * f29)))
show("split: 68 + 1,154.8926 (typed display)", d4(68 + 1154.8926))
show("check split == BGN", same(a + a * f29, due))
show("rounded: 1,222.9 / 1,175.9 / 47.0", "{:.1f} / {:.1f} / {:.1f}".format(due, ordv, due - ordv))

print("\n=== Back to the story: the real schedule (first payment today, +5% a year) ===")
g = 0.05
growth_sum = sum((1 + g) ** k for k in range(30))
first = 2040 / growth_sum
last = first * (1 + g) ** 29
show("sum of 1.05^k, k = 0..29", "{:.6f}".format(growth_sum))
show("first payment ($ millions)", "{:.4f}  ({:.1f})".format(first, first))
show("30th payment ($ millions)", "{:.4f}  ({:.1f})".format(last, last))
show("check: payments add to 2,040", "{:.6f}".format(sum(first * (1 + g) ** k for k in range(30))))


def pv_real(x):
    return sum(first * (1 + g) ** k / (1 + x) ** k for k in range(30))


rate_real = solve_rate(pv_real, 997.6)
show("rate that makes the real schedule worth 997.6", "{:.4f}%  ({:.1f}%)".format(100 * rate_real, 100 * rate_real))
show("real schedule PV at 4% (first today)", "{:.1f}".format(pv_real(0.04)))
show("real schedule PV at 5.94% (first today)", "{:.1f}".format(pv_real(rate_bgn)))
show("payments in the first 10 years (k = 0..9)", "{:.1f}".format(sum(first * (1 + g) ** k for k in range(10))))
show("payments in the last 10 years (k = 20..29)", "{:.1f}".format(sum(first * (1 + g) ** k for k in range(20, 30))))
show("level: payments in the first 10 years", "{:.0f}".format(68 * 10))

print("\n=== Check questions ===")
c1 = pv_due(2400, 0.05, 10)
show("c1  10-year factor at 5%", "{:.6f}".format(pv_factor(0.05, 10)))
show("c1  x 1.05", "{:.6f}".format(pv_factor(0.05, 10) * 1.05))
show("c1  PV_due $2,400, 10 years, 5% (answer)", "{!r}  -> {}  (BA {} / TI {})".format(c1, money(c1), d4(-c1), t4(-c1)))
show("c1  rounded 2,400 x 8.107822", money(2400 * 8.107822))
show("c1  END-mode result (wrong)", money(pv_ord(2400, 0.05, 10)))
show("c1  split: 2,400 + 2,400 x 9-year factor", "{:.6f}  -> {}".format(pv_factor(0.05, 9), money(2400 + pv_ord(2400, 0.05, 9))))
c3 = fv_due(5000, 0.07, 20)
show("c3  1.07^20", "{:.6f}".format(1.07 ** 20))
show("c3  FV factor (1.07^20 - 1)/0.07", "{:.6f}".format(fv_factor(0.07, 20)))
show("c3  FV_ordinary", money(fv_ord(5000, 0.07, 20)))
show("c3  FV_due (answer)", "{!r}  -> {}  (BA {} / TI {})".format(c3, money(c3), d4(c3), t4(c3)))
show("c3  rounded 204,977.46 x 1.07", money(204977.46 * 1.07))
show("c3  rounded 5,000 x 40.995492 x 1.07", money(5000 * 40.995492 * 1.07))
show("c3  deposits in total", money(5000 * 20))
c4a = pv_due(50000, 0.06, 20)
c4b = pv_ord(50000, 0.06, 20)
c4c = c4a * 1.06
show("c4  20-year factor at 6%", "{:.6f}".format(pv_factor(0.06, 20)))
show("c4  A right (BGN)", "{}  (BA {} / TI {})".format(money(c4a), d4(-c4a), t4(-c4a)))
show("c4  B END mode", money(c4b))
show("c4  C BGN and x 1.06 again", money(c4c))
show("c4  rounded 573,496.06 x 1.06", money(573496.06 * 1.06))
show("c4  rounded 607,905.82 x 1.06", money(607905.82 * 1.06))
show("c4  split: 50,000 + 19-year ordinary", "{:.6f}  -> {}".format(pv_factor(0.06, 19), money(50000 + pv_ord(50000, 0.06, 19))))

print("\n=== Traps ===")
show("BGN left on: warm-up annuity would show", money(pv_due(2000, 0.08, 5)))
show("rent in advance valued as ordinary (too low) / right", "{} / {}".format(money(pv_ord(1200, 0.05, 3)), money(pv_due(1200, 0.05, 3))))
show("(1 + r) twice on the rent: 3,431.29 x 1.05", money(3431.29 * 1.05))
show("N for 30 payments, first today / split's ordinary part", "{} / {}".format(30, 30 - 1))

print("\n=== Rounded figures as written in the text ===")
show("w2  Y's EAR to two decimals", pct(0.051, 2))
show("w2  X's EAR to two decimals", pct(w2x, 2))
show("story/back: first 10 years of the real schedule, about", "{:.0f}".format(sum(first * (1 + g) ** k for k in range(10))))
show("back: level stream break-even rate, two decimals", pct(rate_bgn, 2))
show("back: real schedule break-even rate, one decimal", pct(rate_real, 1))
show("c3  last deposit grows (years) / first grows (years)", "1 / 20")

print("\n=== Rounded intermediates as written on the page (must reproduce the shown result) ===")
show("1.08^-5 (7 dp) ; 1.05^-3 (7 dp) ; 1.07^20 (8 dp)", "{:.7f} ; {:.7f} ; {:.8f}".format(1.08 ** -5, 1.05 ** -3, 1.07 ** 20))
show("w1  (1 - 0.6805832)/0.08", "{:.6f}".format((1 - 0.6805832) / 0.08))
show("w1  2,000 x 3.992710", money(2000 * 3.992710))
show("w2  1.0125^4 - 1 (percent, 4 dp)", "{:.4f}".format(100 * (1.050945 - 1)))
show("idea 7,985.42 x 1.08", money(7985.42 * 1.08))
show("why 909.09 + 826.45 + 751.31", money(909.09 + 826.45 + 751.31))
show("why 1,000.00 + 909.09 + 826.45", money(1000.00 + 909.09 + 826.45))
show("why 2,486.85 x 1.1 (to the cent)", "{:.4f} -> {}".format(2486.85 * 1.1, money(2486.85 * 1.1 + 1e-9)))
show("why 1,000 + 1,000 x 1.735537", money(1000 + 1000 * 1.735537))
show("why 1,331 + 1,210 + 1,100 ; 3,310 x 1.1", "{} ; {}".format(money(1331 + 1210 + 1100), money(3310 * 1.1)))
show("worked 1,200.0000 + 1,142.8571 + 1,088.4354", d4(1200.0 + 1142.8571 + 1088.4354))
show("worked (1 - 0.8638376)/0.05", "{:.6f}".format((1 - 0.8638376) / 0.05))
show("worked 1,200 x 2.723248", money(1200 * 2.723248))
show("worked 3,431.29 / 3,267.90", "{:.4f}".format(3431.29 / 3267.90))
show("worked 1,200 + 1,200 x 1.859410", money(1200 + 1200 * 1.859410))
show("worked 1,389.15 + 1,323.00 + 1,260.00", money(1389.15 + 1323.00 + 1260.00))
show("worked 3,431.29 x 1.05^3", money(3431.29 * 1.05 ** 3))
show("calc 1,222.8926 / 1,175.8583", "{:.4f}".format(1222.8926 / 1175.8583))
show("calc 1.04 x 1,175.8583", d4(1.04 * 1175.8583))
show("calc 68 + 1,154.8926", d4(68 + 1154.8926))
show("back 1,222.9 - 1,175.9 ; 1,222.9 / 1,175.9", "{:.1f} ; {:.4f}".format(1222.9 - 1175.9, 1222.9 / 1175.9))
show("c1  7.721735 x 1.05 ; x 2,400", "{:.6f} ; {}".format(7.721735 * 1.05, money(2400 * 8.107822)))
show("c1  18,532.16 x 1.05", money(18532.16 * 1.05))
show("c3  (3.86968446 - 1)/0.07", "{:.6f}".format((3.86968446 - 1) / 0.07))
show("c3  5,000 x 40.995492 ; x 1.07", "{} ; {}".format(money(5000 * 40.995492), money(204977.46 * 1.07)))
show("c4  573,496.06 x 1.06", money(573496.06 * 1.06))
show("c4  19 ordinary payments (END, N = 19)", "{}  -> 50,000 + that = {}".format(money(pv_ord(50000, 0.06, 19)), money(50000 + 557905.82)))
show("c4  607,905.82 x 1.06 (C)", money(607905.82 * 1.06))


print("\n=== Grading check (mirrors cfa.js numberOk: |typed| vs |answer| within data-tol) ===")


def graded(answer, tol, typed, signless=True):
    a, v = (abs(answer), abs(typed)) if signless else (answer, typed)
    return abs(v - a) <= tol + 1e-12


for name, ans, tol, right, wrongs in (
    ("w1", w1, 0.01, 7985.42, [pv_due(2000, 0.08, 5)]),
    ("c1", c1, 0.01, 19458.77, [pv_ord(2400, 0.05, 10), pv_due(2400, 0.05, 10) * 1.05]),
    ("c3", c3, 0.01, 219325.88, [fv_ord(5000, 0.07, 20), fv_due(5000, 0.07, 20) * 1.07, fv_ord(5000, 0.07, 21)]),
):
    assert graded(ans, tol, right) and graded(ans, tol, -right), name
    assert not any(graded(ans, tol, round(x, 2)) for x in wrongs), name
    show("{}  accepts {} (either sign), rejects".format(name, money(right)), ", ".join(money(x) for x in wrongs))
