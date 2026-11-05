#!/usr/bin/env python3
"""Day 9 of CFA Math: every number that appears on day-09.html.

Amortizing loans: where each payment goes
(CFA Program 2027 Level I, Quantitative Methods, LM4 The Time Value of Money in Finance).
Plain Python 3, standard library only. Run it and copy the printed numbers.

Calculator displays are shown at the 4 decimals both calculators are set to.
The BA II Plus shows thousands separators (PMT = -1,558.5828), the TI-84 not.
The BA II Plus AMORT worksheet (and the TI-84's bal(, SumPrn(, SumInt( when no
round value is given) round each period to the 4-decimal setting, so multi-
period amortization figures are shown both unrounded and with that rounding
simulated; the page writes them as "about" to the cent.
"""

import math


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


def pct(x, dp=2):
    return "{:.{}f}%".format(100 * x, dp)


def show(label, value):
    print("  {:<66} {}".format(label, value))


def same(a, b, tol=1e-9):
    assert abs(a - b) <= tol, (a, b)
    return "OK"


def annuity_factor(r, n):
    """PV of 1 a period for n periods, paid at the END of each period (Day 5)."""
    return (1 - (1 + r) ** -n) / r


def payment(pv, r, n):
    """Level payment on an amortizing loan: A = PV x r / [1 - (1 + r)^-N]."""
    return pv * r / (1 - (1 + r) ** -n)


def schedule(pv, r, a, n, dp=None):
    """Rows (t, interest, principal, balance after t). dp rounds each period."""
    rnd = (lambda x: round(x, dp)) if dp is not None else (lambda x: x)
    a = rnd(a)
    bal = pv
    rows = []
    for t in range(1, n + 1):
        i = rnd(bal * r)
        p = rnd(a - i)
        bal = rnd(bal - p)
        rows.append((t, i, p, bal))
    return rows


def amort(rows, p1, p2):
    """What AMORT shows for payments p1..p2: BAL after p2, sum PRN, sum INT."""
    sel = [row for row in rows if p1 <= row[0] <= p2]
    return sel[-1][3], sum(row[2] for row in sel), sum(row[1] for row in sel)


def balance_pv(a, r, n, k):
    """Balance after k payments = PV of the N - k remaining payments."""
    return a * annuity_factor(r, n - k)


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 8): $12 a year forever, first payment at the end of year 5, r = 6%.
w1_at4 = 12 / 0.06
w1 = w1_at4 / 1.06 ** 4
show("w1  A/r = 12 / 0.06 (value at t = 4)", "{}  (display {})".format(money(w1_at4), "{:.4f}".format(w1_at4)))
show("w1  1.06^4", "{:.6f}".format(1.06 ** 4))
show("w1  200 / 1.06^4 (answer)", "{!r} -> {}  (BA PV = {} / TI PV={})".format(w1, money(w1), d4(-w1), t4(-w1)))
show("w1  trap: discounted 5 periods", money(w1_at4 / 1.06 ** 5))
show("w1  trap: no deferral", money(w1_at4))

# w2 (Day 2): $8,000 grows to $11,000 in 5 years. Annual rate?
w2 = (11000 / 8000) ** (1 / 5) - 1
show("w2  11,000 / 8,000", "{:.6f}".format(11000 / 8000))
show("w2  C (right) (1.375)^(1/5) - 1", "{!r} -> {}  (BA I/Y = {} / TI I%={})".format(w2, pct(w2), d4(100 * w2), t4(100 * w2)))
show("w2  exponent 1/5", "{:.1f}".format(1 / 5))
show("w2  A (total, not per year) 1.375 - 1", pct(11000 / 8000 - 1))
show("w2  B (simple) 37.5% / 5", pct((11000 / 8000 - 1) / 5))
show("w2  check: 8,000 x 1.0658^5", money(8000 * (1 + w2) ** 5))
show("w2  B check: 8,000 x 1.075^5 (overshoots)", money(8000 * 1.075 ** 5))

# ---------------------------------------------------------------------------
print("\n=== Story (from the plan; arithmetic only) ===")
show("survey week", "9 October 1981; survey began 1971; years running", )
show("years from 1971 to 1981", 1981 - 1971)
show("record rate", "18.63%; prime 21.5%; Fed funds ~20%")
PV, RATE_A, YEARS = 100000.0, 0.1863, 30
N = YEARS * 12
r = RATE_A / 12
A = payment(PV, r, N)
show("N = 30 x 12", N)
show("monthly rate 18.63 / 12 (percent)", "{:.4f}".format(100 * r))
show("PMT (exact)", "{!r} -> {}".format(A, money(A)))

# ---------------------------------------------------------------------------
print("\n=== The idea: credit-card picture ===")
cc_bal, cc_r, cc_pay = 1000.0, 0.015, 20.0
show("1,000 x 1.5% interest", money(cc_bal * cc_r))
show("20 - 15 principal", money(cc_pay - cc_bal * cc_r))
show("new balance", money(cc_bal - (cc_pay - cc_bal * cc_r)))

# ---------------------------------------------------------------------------
print("\n=== Why it works ===")
exact = schedule(PV, r, A, N)
i1, p1, b1 = exact[0][1], exact[0][2], exact[0][3]
i2, p2, b2 = exact[1][1], exact[1][2], exact[1][3]
show("month 1: interest = 0.015525 x 100,000", "{:.4f} -> {}".format(i1, money(i1)))
show("month 1: principal = A - interest", "{:.4f} -> {}".format(p1, money(p1)))
show("month 1: balance", "{:.4f} -> {}".format(b1, money(b1)))
show("month 2: interest = 0.015525 x balance", "{:.4f} -> {}".format(i2, money(i2)))
show("month 2: principal", "{:.4f} -> {}".format(p2, money(p2)))
show("month 2: principal = month-1 principal x 1.015525", "{:.4f}  {}".format(p1 * (1 + r), same(p1 * (1 + r), p2)))
show("month 2 as written on the page: 6.0828 x 1.015525", "{:.4f}".format(6.0828 * 1.015525))
show("interest saved in month 2 = r x principal_1", "{:.4f}".format(r * p1))
for t in (1, 2, 12, 120, 240, 316, 317, 360):
    row = exact[t - 1]
    show("  payment {:>3}: interest / principal / balance".format(t),
         "{} / {} / {}".format(money(row[1]), money(row[2]), money(row[3])))
    assert abs(row[2] - p1 * (1 + r) ** (t - 1)) < 1e-6
# Balance follows B_t = B_(t-1)(1 + r) - A, and so does the PV of the payments left.
for t in (1, 12, 60, 317):
    show("  B_{} vs PV of the {} payments left".format(t, N - t),
         "{:.6f} vs {:.6f}  {}".format(exact[t - 1][3], balance_pv(A, r, N, t), same(exact[t - 1][3], balance_pv(A, r, N, t), 1e-6)))

# ---------------------------------------------------------------------------
print("\n=== Worked example: $10,000 at 6%, 3 annual payments ===")
we_pv, we_r, we_n = 10000.0, 0.06, 3
show("1.06^-3", "{:.6f}".format(1.06 ** -3))
show("1 - 1.06^-3", "{:.6f}".format(1 - 1.06 ** -3))
show("10,000 x 0.06", money(we_pv * we_r))
we_a = payment(we_pv, we_r, we_n)
show("A = 600 / 0.160381", "{!r} -> {}".format(we_a, money(we_a)))
show("A from the rounded divisor 600 / 0.160381 (too few digits)", "{:.4f}".format(600 / 0.160381))
show("1 - 1.06^-3 to 7 digits", "{:.7f}".format(1 - 1.06 ** -3))
show("A = 600 / 0.1603807 (page)", "{:.4f} -> {}".format(600 / 0.1603807, money(600 / 0.1603807)))
# Schedule the way it is done by hand: payment and every line rounded to cents.
a_c = round(we_a, 2)
bal = we_pv
for t in range(1, we_n + 1):
    i = round(we_r * bal, 2)
    p = round(a_c - i, 2)
    show("year {}: interest = 0.06 x {}".format(t, money(bal)), money(i))
    show("year {}: principal = {} - {}".format(t, money(a_c), money(i)), money(p))
    if t == we_n:
        show("year {}: principal needed (balance owed)".format(t), money(bal))
        show("year {}: overshoot (rounding)".format(t), money(p - bal))
        show("year {}: last payment trimmed to".format(t), money(bal + i))
        p = bal
    bal = round(bal - p, 2)
    show("year {}: balance".format(t), money(bal))
show("total interest 600.00 + 411.53 + 211.76", money(600.00 + 411.53 + 211.76))
show("total principal 3,141.10 + 3,329.57 + 3,529.33", money(3141.10 + 3329.57 + 3529.33))
show("principal ratio 3,329.57 / 3,141.10 (grows by 1 + r)", "{:.4f}".format(3329.57 / 3141.10))
show("check: balance after year 1 = 3,741.10 x [1 - 1.06^-2]/0.06",
     "{:.6f} -> {}".format(annuity_factor(0.06, 2), money(a_c * annuity_factor(0.06, 2))))
show("check (exact A)", money(balance_pv(we_a, we_r, we_n, 1)))
show("check with A = 3,741.098: 3,741.098 x 1.833393", money(3741.098 * 1.833393))
show("principal grows by 1.06: 3,141.10 x 1.06 ; 3,329.57 x 1.06", "{} ; {}".format(money(3141.10 * 1.06), money(3329.57 * 1.06)))
show("1 - 1.06^-2", "{:.6f}".format(1 - 1.06 ** -2))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough: $100,000, 30 years, 18.63% monthly ===")
show("PMT", "BA PMT = {}  TI PMT={}".format(d4(-A), t4(-A)))
show("payment 1: BAL / PRN / INT", "BA {} / {} / {}   TI {} / {} / {}".format(
    d4(b1), d4(-p1), d4(-i1), t4(b1), t4(-p1), t4(-i1)))
rounded = schedule(PV, r, A, N, dp=4)
for label, rows in (("unrounded", exact), ("4-dp per month (BA/TI rounding)", rounded)):
    bal12, prn12, int12 = amort(rows, 1, 12)
    show("P1=1, P2=12 ({}): BAL / PRN / INT".format(label),
         "{} / {} / {}".format(d4(bal12), d4(-prn12), d4(-int12)))
bal12, prn12, int12 = amort(exact, 1, 12)
show("year 1 to the cent: BAL / PRN / INT", "{} / {} / {}".format(money(bal12), money(prn12), money(int12)))
show("12 payments: 12 x PMT", "{:.4f} -> {}".format(12 * A, money(12 * A)))
show("check: 12 x PMT - principal = interest", "{} {}".format(money(12 * A - prn12), same(12 * A - prn12, int12, 1e-6)))
show("check: 100,000 - BAL = principal", "{} {}".format(money(PV - bal12), same(PV - bal12, prn12, 1e-6)))
show("share of year-1 payments that was interest", pct(int12 / (12 * A), 2))
show("share of payment 1 that was interest", pct(i1 / A, 2))
for t in (316, 317):
    for label, rows in (("unrounded", exact), ("4-dp", rounded)):
        bal_t, prn_t, int_t = amort(rows, t, t)
        show("P1=P2={} ({}): BAL / PRN / INT".format(t, label),
             "{} / {} / {}  (one decimal: PRN {:.1f}, INT {:.1f})".format(d4(bal_t), d4(-prn_t), d4(-int_t), -prn_t, -int_t))
first_cross = next(t for t, i, p, b in exact if p > i)
show("first payment where principal > interest", first_cross)
show("  in years + months", "{} years {} months".format(first_cross // 12, first_cross % 12))
show("  closed form: 1 + ln((A/2)/p1)/ln(1 + r)", "{:.4f}".format(1 + math.log((A / 2) / p1) / math.log(1 + r)))
show("  half the payment", money(A / 2))

# ---------------------------------------------------------------------------
print("\n=== Back to the story ===")
total_paid = N * A
show("total paid: 360 x PMT", "{:.4f} -> {}".format(total_paid, money(total_paid)))
show("total interest: total paid - 100,000", "{:.4f} -> {}".format(total_paid - PV, money(total_paid - PV)))
show("total interest, rounded to $10", "{:,.0f}".format(round(total_paid - PV, -1)))
show("interest / amount borrowed", "{:.2f}x".format((total_paid - PV) / PV))

# ---------------------------------------------------------------------------
print("\n=== Exam traps ===")
trap_annual = payment(PV, RATE_A, N)
show("I/Y = 18.63 with N = 360 (annual rate used monthly): PMT", "{} -> {}".format(d4(-trap_annual), money(trap_annual)))
show("  = interest only: 100,000 x 0.1863", money(PV * RATE_A))
for label, rows in (("unrounded", exact), ("4-dp", rounded)):
    b, p_, i_ = amort(rows, 12, 12)
    show("P1=P2=12 ({}): PRN / INT".format(label), "{} / {}".format(d4(-p_), d4(-i_)))
b12_only, p12_only, i12_only = amort(exact, 12, 12)
show("payment 12 alone, to the cent: interest / principal", "{} / {}".format(money(i12_only), money(p12_only)))
show("even principal: 100,000 / 360", money(PV / N))
show("100,000 - 12 x PMT (payments subtracted, interest ignored)", money(PV - 12 * A))
show("  as written on the page: 100,000 - 18,702.99", money(PV - 18702.99))
show("  (not 12 x the rounded 1,558.58: 100,000 - 18,702.96)", money(PV - 12 * 1558.58))

# ---------------------------------------------------------------------------
print("\n=== Check questions ===")
# c1: $300,000, 30 years, 7.2% stated, monthly. Balance after 60 payments.
c1_pv, c1_r, c1_n = 300000.0, 0.072 / 12, 360
c1_a = payment(c1_pv, c1_r, c1_n)
show("c1  I/Y = 7.2 / 12", "{:.4f}".format(100 * c1_r))
show("c1  monthly rate as a decimal", "{:.3f}".format(c1_r))
show("c1  PMT", "{!r}  BA PMT = {}  TI PMT={} -> {}".format(c1_a, d4(-c1_a), t4(-c1_a), money(c1_a)))
c1_ex = schedule(c1_pv, c1_r, c1_a, c1_n)
c1_rd = schedule(c1_pv, c1_r, c1_a, c1_n, dp=4)
show("c1  payment 1: interest / principal", "{} / {}  (BA INT = {}, PRN = {})".format(
    money(c1_ex[0][1]), money(c1_ex[0][2]), d4(-c1_ex[0][1]), d4(-c1_ex[0][2])))
c1_bal = c1_ex[59][3]
show("c1  balance after 60 (answer, unrounded)", "{!r} -> {}".format(c1_bal, money(c1_bal)))
show("c1  balance after 60 (4-dp per month)", d4(c1_rd[59][3]))
show("c1  same as PV of 300 remaining", "{:.4f} {}".format(balance_pv(c1_a, c1_r, c1_n, 60), same(balance_pv(c1_a, c1_r, c1_n, 60), c1_bal, 1e-6)))
b60, prn60, int60 = amort(c1_ex, 1, 60)
show("c1  P1=1, P2=60: PRN / INT (unrounded)", "{} / {}".format(d4(-prn60), d4(-int60)))
show("c1  principal repaid in 5 years", money(prn60))
show("c1  60 payments total", money(60 * c1_a))
show("c1  share of 5 years' payments that was interest", pct(int60 / (60 * c1_a), 1))
show("c1  trap: 300,000 - 60 x PMT", money(c1_pv - 60 * c1_a))
show("c1  tolerance check |4dp - exact| (data-tol 0.05)", "{:.4f}".format(abs(c1_rd[59][3] - c1_bal)))

show("c1  300-month factor at 0.6%", "{:.6f}".format(annuity_factor(c1_r, 300)))
show("c1  2,036.3646 x factor", money(2036.3646 * annuity_factor(c1_r, 300)))
# c2: conceptual, uses c1's first payment as the illustration.
show("c2  payment 1 interest share (c1 loan)", pct(c1_ex[0][1] / c1_a, 1))
show("c2  even-slice principal 300,000 / 360", money(c1_pv / c1_n))
show("c2  even slices + month-1 interest: 833.33 + 1,800.00", money(round(c1_pv / c1_n, 2) + 1800.00))
show("c2  month-2 principal = 236.3646 x 1.006", "{:.4f} -> {}  (exact {})".format(236.3646 * 1.006, money(236.3646 * 1.006), money(c1_ex[1][2])))

# c3: $200,000, 15 years, 6% stated, monthly. Balance after 5 years = PV of 120 left.
c3_pv, c3_r, c3_n = 200000.0, 0.005, 180
c3_a = payment(c3_pv, c3_r, c3_n)
show("c3  PMT", "{!r}  BA PMT = {}  TI PMT={} -> {}".format(c3_a, d4(-c3_a), t4(-c3_a), money(c3_a)))
c3 = balance_pv(c3_a, c3_r, c3_n, 60)
show("c3  N = 180 - 60", c3_n - 60)
show("c3  PV of 120 remaining (answer)", "{!r}  BA PV = {}  TI PV={} -> {}".format(c3, d4(c3), t4(c3), money(c3)))
c3_rounded_pmt = round(c3_a, 2) * annuity_factor(c3_r, 120)
show("c3  with the rounded 1,687.71 typed in", "{:.4f} -> {}".format(c3_rounded_pmt, money(c3_rounded_pmt)))
c3_rd = schedule(c3_pv, c3_r, c3_a, c3_n, dp=4)
show("c3  AMORT P1=1, P2=60 BAL (4-dp per month)", d4(c3_rd[59][3]))
show("c3  AMORT BAL (unrounded)", "{:.4f} {}".format(schedule(c3_pv, c3_r, c3_a, c3_n)[59][3], same(schedule(c3_pv, c3_r, c3_a, c3_n)[59][3], c3, 1e-6)))
show("c3  tolerance needed |rounded-PMT - exact|", "{:.4f}".format(abs(c3_rounded_pmt - c3)))
show("c3  |AMORT 4-dp BAL - PV| (page: within a cent or so)", "{:.4f}".format(abs(c3_rd[59][3] - c3)))
show("c3  data-tol 0.35 covers it", "{}".format(abs(c3_rounded_pmt - c3) <= 0.35 and abs(c3_rd[59][3] - c3) <= 0.35))
show("c3  120-month factor at 0.5%", "{:.6f}".format(annuity_factor(c3_r, 120)))
show("c3  principal repaid in 5 years", money(c3_pv - c3))
show("c3  share of the loan repaid after 1/3 of the term", pct((c3_pv - c3) / c3_pv, 1))
show("c3  trap: PV of the 60 payments made", money(c3_a * annuity_factor(c3_r, 60)))
show("c3  trap: 200,000 - 60 x PMT", money(c3_pv - 60 * c3_a))

# c4: $250,000, 25 years, 6% stated, monthly payment.
c4_pv = 250000.0
c4 = payment(c4_pv, 0.005, 300)
show("c4  B (right) N = 300, I/Y = 0.5", "{!r}  BA PMT = {}  TI PMT={} -> {}".format(c4, d4(-c4), t4(-c4), money(c4)))
c4_annual = payment(c4_pv, 0.06, 25)
show("c4  annual payment N = 25, I/Y = 6", "{} -> {}".format(d4(-c4_annual), money(c4_annual)))
show("c4  A (annual / 12)", money(c4_annual / 12))
show("c4  C: 250,000 / 300 + 250,000 x 0.005", "{} + {} = {}".format(money(c4_pv / 300), money(c4_pv * 0.005), money(c4_pv / 300 + c4_pv * 0.005)))
show("c4  first payment's interest", money(c4_pv * 0.005))
show("c4  first payment's principal (right)", money(c4 - c4_pv * 0.005))

print("\n=== c5: no numbers ===")
