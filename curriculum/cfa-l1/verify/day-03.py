#!/usr/bin/env python3
"""Day 3 of CFA Math: every number that appears on day-03.html.

Required returns: real, nominal and after-tax
(CFA Program 2027 Level I, Quantitative Methods, LM1 Returns of Financial Assets and
Instruments; return types from LM2 Types of Financial Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime


def money(x):
    return "{:,.2f}".format(x)


def ba(x):
    """BA II Plus display: 4 decimals, thousands separators."""
    return "{:,.4f}".format(x)


def ti(x):
    """TI-84 Plus display, FLOAT 4: no separators, no leading zero before the point."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def real(nominal, infl):
    """Exact real return from the Fisher relation: (1 + nominal)/(1 + inflation) - 1."""
    return (1 + nominal) / (1 + infl) - 1


def after_tax(pre, t):
    return pre * (1 - t)


def pv(fv, r, n):
    return fv / (1 + r) ** n


def fv(p, r, n):
    return p * (1 + r) ** n


def show(label, value):
    print("  {:<62} {}".format(label, value))


print("=== Warm-up ===")
w1 = pv(12000, 0.055, 9)
show("w1 (Day 2) 1.055^9", "{:.6f}".format(1.055 ** 9))
show("w1 PV of 12,000 due in 9 years at 5.5% (answer)", "{!r} -> {}".format(w1, money(w1)))
show("w1 displays", "BA PV = -{} / TI PV=-{}".format(ba(w1), ti(w1)))
show("w1 tolerance check |nearest cent - exact|", "{:.4f}".format(abs(round(w1, 2) - w1)))
w2_right = fv(1000, 0.0005, 10)      # 0.05 typed in I/Y = 0.05% a year
w2_meant = fv(1000, 0.05, 10)
show("w2 (Day 1) FV, 1,000 at 0.05% for 10 years (typed 0.05)", "{!r} -> BA FV = {}".format(w2_right, ba(w2_right)))
show("w2 FV, 1,000 at 5% for 10 years (what was meant)", "BA FV = {}".format(ba(w2_meant)))
show("w2 interest earned when 0.05 is typed", money(w2_right - 1000))

print("\n=== Story: Türkiye, 2021 ===")
show("policy rate cut 15% -> 14% (points)", "{:.0f}".format(15 - 14))
show("500 basis points in percentage points", "{:.0f}".format(500 / 100))
show("days from 16 Dec 2021 to 3 Jan 2022",
     (datetime.date(2022, 1, 3) - datetime.date(2021, 12, 16)).days)
NOM, INF = 0.14, 0.3608
short = NOM - INF
show("shortcut 14% - 36.08%", "{:.2f}%".format(100 * short))

print("\n=== Why it works ===")
show("Day 1: 1.05 x 1.05", "{:.4f}".format(1.05 * 1.05))
show("Day 1: 1 + 0.05 + 0.05", "{:.4f} -> {:.2f}".format(1 + 0.05 + 0.05, 1 + 0.05 + 0.05))
show("cross term, real 2% x inflation 3%", "{:.4f} -> {:.2f}%".format(0.02 * 0.03, 100 * 0.02 * 0.03))
show("exact nominal for real 2%, inflation 3%: 1.02 x 1.03 - 1", "{:.4f} -> {:.2f}%".format(1.02 * 1.03 - 1, 100 * (1.02 * 1.03 - 1)))

print("\n=== Worked example: 5-year corporate bond ===")
parts = [1.5, 2.5, 1.2, 0.3, 0.5]
req = sum(parts)
show("(a) required return 1.5 + 2.5 + 1.2 + 0.3 + 0.5", "{:.1f}%".format(req))
show("    nominal risk-free part 1.5 + 2.5", "{:.1f}%".format(1.5 + 2.5))
g = 1.06 / 1.025
show("(b) real growth factor 1.06 / 1.025", "{:.6f}".format(g))
rb = real(0.06, 0.025)
show("(b) exact real return", "{:.6f} -> {:.2f}%".format(rb, 100 * rb))
show("(b) shortcut 6% - 2.5%", "{:.2f}%".format(6 - 2.5))
show("(b) shortcut minus exact (points)", "{:.2f}".format(3.5 - 100 * rb))
atn = after_tax(0.06, 0.30)
show("(c) after-tax nominal 6% x (1 - 0.30)", "{:.4f} -> {:.2f}%".format(atn, 100 * atn))
g2 = (1 + atn) / 1.025
show("(c) 1.042 / 1.025", "{:.6f}".format(g2))
rc = real(atn, 0.025)
show("(c) after-tax real", "{:.6f} -> {:.2f}%".format(rc, 100 * rc))

print("\n=== Calculator walkthrough: 14% nominal, 36.08% inflation ===")
r_real = real(NOM, INF)
show("1.14 / 1.3608 (real growth factor)", "{:.4f}".format(1.14 / 1.3608))
show("exact real return (decimal)", "{!r} -> BA {} / TI {}".format(r_real, ba(r_real), ti(r_real)))
show("x 100", "{!r} -> BA {} / TI {}".format(100 * r_real, ba(100 * r_real), ti(100 * r_real)))
show("exact real, 2 dp", "{:.2f}%".format(100 * r_real))
show("shortcut 14 - 36.08", "BA {} / TI {}".format(ba(14 - 36.08), ti(14 - 36.08)))
gap = 100 * r_real - 100 * short
show("gap exact - shortcut (points)", "{:.4f} -> {:.2f}".format(gap, gap))
T = 0.15                                  # hypothetical tax rate on interest
atn_t = after_tax(NOM, T)
show("after-tax nominal 0.14 x (1 - 0.15)", "{!r} -> BA {} / TI {}".format(atn_t, ba(atn_t), ti(atn_t)))
show("after-tax nominal, percent", "{:.2f}%".format(100 * atn_t))
r_at = real(atn_t, INF)
show("after-tax real (decimal)", "{!r} -> BA {} / TI {}".format(r_at, ba(r_at), ti(r_at)))
show("after-tax real x 100", "{!r} -> BA {} / TI {}".format(100 * r_at, ba(100 * r_at), ti(100 * r_at)))
show("after-tax real, 2 dp", "{:.2f}%".format(100 * r_at))

print("\n=== Back to the story ===")
show("10,000 lira at 14%", money(10000 * 1.14))
show("a 10,000-lira basket after 36.08% inflation", money(10000 * 1.3608))
show("baskets bought: 11,400 / 13,608", "{:.4f}".format(11400 / 13608))
show("purchasing power lost", "{:.2f}%".format(100 * (1 - 11400 / 13608)))
cross = r_real * INF
show("cross term real x inflation", "{:.4f} -> {:.2f} points".format(cross, 100 * cross))
show("check: real + inflation + cross = nominal", "{:.4f}".format(r_real + INF + cross))
show("check: shortfall / (1 + inflation) = real", "{:.4f} / {:.4f} = {:.4f}".format(short, 1 + INF, short / (1 + INF)))
show("check: (1 + real) x 1.3608", "{:.4f}".format((1 + r_real) * 1.3608))
need0 = (1 + 0.0) * (1 + INF) - 1
need2 = 1.02 * 1.3608 - 1
show("nominal needed for 0% real", "{:.2f}%".format(100 * need0))
show("nominal needed for 2% real: 1.02 x 1.3608 - 1", "{!r} -> {:.2f}%".format(need2, 100 * need2))
show("2% real by the shortcut: 2 + 36.08", "{:.2f}%".format(2 + 36.08))

print("\n=== Check question 1: nominal 9%, inflation 4.2% ===")
c1 = 100 * real(0.09, 0.042)
show("1.09 / 1.042", "{:.6f}".format(1.09 / 1.042))
show("c1 exact real (answer, percent)", "{!r} -> {:.2f}%".format(c1, c1))
show("c1 displays (decimal, then x 100)", "BA {} then {} / TI {} then {}".format(
    ba(c1 / 100), ba(c1), ti(c1 / 100), ti(c1)))
show("c1 shortcut 9 - 4.2", "{:.2f}%".format(9 - 4.2))
show("c1 shortcut minus exact (points)", "{:.4f} -> {:.2f}".format(9 - 4.2 - c1, 9 - 4.2 - c1))
show("c1 tolerance check |4.61 - exact|, |4.80 - exact|", "{:.4f} {:.4f}".format(abs(4.61 - c1), abs(4.80 - c1)))

print("\n=== Check question 2: why the exact loss is smaller ===")
show("shortfall -0.2208 / 1.3608", "{:.4f} -> {:.2f}%".format(short / 1.3608, 100 * short / 1.3608))
show("with a 15% tax (for option B)", "{:.2f}%".format(100 * r_at))

print("\n=== Check question 3: 7% bond, 25% tax, 2% inflation ===")
atn3 = after_tax(0.07, 0.25)
show("after-tax nominal 0.07 x 0.75", "{:.4f} -> BA {} / TI {}".format(atn3, ba(atn3), ti(atn3)))
c3 = 100 * real(atn3, 0.02)
show("1 + after-tax nominal", "{:.4f}".format(1 + atn3))
show("c3 decimal: 1.0525 / 1.02 - 1", "{:.6f}".format(c3 / 100))
show("c3 after-tax real (answer, percent)", "{!r} -> {:.2f}%".format(c3, c3))
show("c3 displays", "BA {} then {} / TI {} then {}".format(ba(c3 / 100), ba(c3), ti(c3 / 100), ti(c3)))
wrong_taxreal = 100 * real(0.07, 0.02) * 0.75
show("wrong: tax the real return (1.07/1.02 - 1) x 0.75", "{:.4f} -> {:.2f}%".format(wrong_taxreal, wrong_taxreal))
show("    real before tax 1.07/1.02 - 1", "{:.2f}%".format(100 * real(0.07, 0.02)))
show("wrong: shortcut 5.25 - 2", "{:.2f}%".format(5.25 - 2))
show("c3 tolerance check |3.19 - exact|, |3.25 - exact|, |3.68 - exact|",
     "{:.4f} {:.4f} {:.4f}".format(abs(3.19 - c3), abs(3.25 - c3), abs(round(wrong_taxreal, 2) - c3)))

print("\n=== Check question 4: required return on a 3-month T-bill ===")
# (plan: 1.2, 2.8, 1.5, 0.6, 0.9, but that gives 4.0%, the worked example's nominal
#  risk-free rate; new inputs so the answer cannot be recalled from the example)
rf_real, ip, dp, lp, mp = 1.0, 2.7, 1.6, 0.4, 0.9
show("A (correct) real rf + inflation premium", "{:.1f}%".format(rf_real + ip))
show("B adds the default premium", "{:.1f}%".format(rf_real + ip + dp))
show("C adds every premium", "{:.1f}%".format(rf_real + ip + dp + lp + mp))
