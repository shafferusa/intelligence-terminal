#!/usr/bin/env python3
"""Day 11 of CFA Math: every number that appears on day-11.html.

Holding-period returns and chain-linking
(CFA Program 2027 Level I, Quantitative Methods, LM1 Returns of Financial Assets
and Instruments; annualizing from LM2 Types of Financial Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
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


def hpr(p0, p1, d1=0.0):
    return (p1 - p0 + d1) / p0


def chain(*rs):
    g = 1.0
    for r in rs:
        g *= 1 + r
    return g - 1


def annualize(r_period, c):
    return (1 + r_period) ** c - 1


def needed(loss):
    return loss / (1 - loss)


def npv(rate, cfs):
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cfs))


def irr(cfs, lo=-0.99, hi=1.0):
    f_lo = npv(lo, cfs)
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid, cfs)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def show(label, value):
    print("  {:<62} {}".format(label, value))


print("=== Warm-up w1 (from Day 10): CF0 -2,500; 600 x 4; 900 in year 5; at 7% ===")
w1_cfs = [-2500, 600, 600, 600, 600, 900]
w1 = npv(0.07, w1_cfs)
for t, cf in enumerate(w1_cfs):
    if t:
        show("  PV of year {} ({})".format(t, cf), money(cf / 1.07 ** t))
show("w1 NPV at 7% (answer)", "{!r} -> {} (display NPV = {} / TI {})".format(w1, money(w1), d4(w1), ti4(w1)))
show("w1 IRR (solution extra)", "{:.4f}%  -> {:.2f}%".format(100 * irr(w1_cfs), 100 * irr(w1_cfs)))
w1_f1 = npv(0.07, [-2500, 600, 900])
show("w1 mistake: F01 left at 1 (600 once, 900 in year 2)", money(w1_f1))

print("\n=== Warm-up w2 (from Day 1): 6% a year for 3 years, total growth ===")
g3 = 1.06 ** 3
show("1.06^3", "{:.6f}".format(g3))
show("B (right) total return", pct(g3 - 1))
show("A (adding) 3 x 6%", pct(3 * 0.06))
show("C (factor as a percent)", "{:.2f}%".format(100 * g3))

print("\n=== Story: Nasdaq Composite ===")
PEAK, LOW, REC = 5048.62, 1114.11, 5056.06
d_peak, d_low, d_rec = datetime.date(2000, 3, 10), datetime.date(2002, 10, 9), datetime.date(2015, 4, 23)
show("days peak -> low", (d_low - d_peak).days)
show("months peak -> low (calendar)", (d_low.year - d_peak.year) * 12 + d_low.month - d_peak.month)
days_lr = (d_rec - d_low).days
show("days low -> 2015 record", days_lr)
show("years low -> record (days / 365.25)", "{:.4f}".format(days_lr / 365.25))
show("years low -> record (days / 365)", "{:.4f}".format(days_lr / 365))
T = 12.54
show("years used (plan)", T)
# 15 years, 1 month, 13 days: 10 Mar 2000 + 15y = 10 Mar 2015, +1m = 10 Apr 2015, +13d
chk = datetime.date(2015, 4, 10) + datetime.timedelta(days=13)
show("10 Mar 2015 + 1 month + 13 days", chk.isoformat())
show("days peak -> 2015 record", (d_rec - d_peak).days)

frac = LOW / PEAK
r_fall = hpr(PEAK, LOW)
show("growth factor of the fall LOW/PEAK", "{:.6f} -> {:.4f} (TI {})".format(frac, frac, ti4(frac)))
show("HPR of the fall", "{!r} -> {:.4f}% -> {}".format(r_fall, 100 * r_fall, pct(r_fall)))
show("HPR as a decimal (TI Ans-1)", "{:.6f} -> TI {}".format(r_fall, ti4(r_fall)))
show("loss L", "{:.6f} -> {:.4f}".format(-r_fall, -r_fall))
show("$10,000 at the peak -> at the low", money(10000 * frac))
up78 = LOW * 1.78
show("78% rise from the low: 1,114.11 x 1.78", "{:.4f} -> {}".format(up78, money(up78)))
show("  as a fraction of the peak", "{:.6f} -> {:.4f} (TI {}) -> {}".format(up78 / PEAK, up78 / PEAK, ti4(up78 / PEAK), pct(up78 / PEAK)))
show("  HPR peak -> after a 78% rise", pct(up78 / PEAK - 1))
back = PEAK / LOW
r_back = back - 1
show("factor needed PEAK/LOW", "{:.6f} -> {:.4f}".format(back, back))
show("gain needed", "{!r} -> {:.4f}% -> {}".format(r_back, 100 * r_back, pct(r_back)))
show("check L/(1-L)", "{:.6f}".format(needed(-r_fall)))
show("check 0.7793/0.2207 (rounded inputs)", "{:.4f}".format(0.7793 / 0.2207))
show("check 1/0.2207 (rounded)", "{:.4f}".format(1 / 0.2207))
g_rec = REC / LOW
show("factor low -> record REC/LOW", "{:.6f} -> {:.4f}".format(g_rec, g_rec))
show("HPR low -> record", "{:.4f}% -> {}".format(100 * (g_rec - 1), pct(g_rec - 1)))
ann = g_rec ** (1 / T) - 1
show("annualized low -> record, T = 12.54", "{!r} -> {:.4f}% -> {}".format(100 * ann, 100 * ann, pct(ann)))
show("  1 + r (TI display)", "{:.6f} -> TI {}".format(1 + ann, ti4(1 + ann)))
show("  r (TI display)", ti4(ann))
show("annualized with exact days/365.25", pct(g_rec ** (365.25 / days_lr) - 1, 4))
total = REC / PEAK - 1
show("peak -> 2015 record, total HPR", "{:.4f}% -> {}".format(100 * total, pct(total)))
show("chain: frac x g_rec", "{:.6f} -> {:.4f}".format(frac * g_rec, frac * g_rec))
show("chain rounded: 0.2207 x 4.5382", "{:.4f}".format(0.2207 * 4.5382))
show("wrong: adding -77.93% + 353.82%", "{:.2f}%".format(100 * r_fall + 100 * (g_rec - 1)))
show("wrong (rounded): -77.93 + 353.82", "{:.2f}%".format(-77.93 + 353.82))
ann_fall = frac ** (12 / 31) - 1
show("(extra) fall annualized over 31 months", pct(ann_fall))
# Back to the story: how long each climb takes at the 12.82% a year the index managed
yrs_78 = math.log(1.78) / math.log(1 + ann)
yrs_back = math.log(back) / math.log(1 + ann)
show("years for a 78% rise at 12.8192% a year ('under five')", "{:.4f}".format(yrs_78))
show("years for the 353.15% rise at 12.8192% a year ('twelve and a half')", "{:.4f}".format(yrs_back))

print("\n=== The idea / Why it works ===")
show("jacket $100, -50%, then +50%", money(100 * 0.5 * 1.5))
show("+50% then -50%: 1.50 x 0.50", "{:.2f} -> {}".format(1.5 * 0.5, pct(1.5 * 0.5 - 1)))
for L in (0.10, 0.25, 0.50, 0.75, 0.90):
    show("loss {:.0f}% needs".format(100 * L), "{:.4f} -> {}".format(needed(L), pct(needed(L))))
show("+10% then -10%: 1.10 x 0.90", "{:.2f} -> {}".format(1.1 * 0.9, pct(1.1 * 0.9 - 1)))
show("50% loss: 0.5 x 2", 0.5 * 2)

print("\n=== Worked example ===")
wa = hpr(40.00, 43.00, 1.20)
show("(a) gain 43 - 40 + 1.20", "{:.2f}".format(43.00 - 40.00 + 1.20))
show("(a) HPR", "{!r} -> {:.4f} -> {}".format(wa, wa, pct(wa)))
show("    income yield 1.20/40", pct(1.20 / 40.00))
show("    price return 3.00/40", pct(3.00 / 40.00))
show("    sum", pct(1.20 / 40.00 + 3.00 / 40.00))
f1 = 1.105 * 0.80
show("(b) 1.105 x 0.80", "{:.4f}".format(f1))
gb = 1.105 * 0.80 * 1.15
show("(b) 1.105 x 0.80 x 1.15", "{!r} -> {:.4f}".format(gb, gb))
show("(b) 3-year return", pct(chain(0.105, -0.20, 0.15)))
show("(b) adding", "{:.1f}%".format(10.5 - 20 + 15))
show("(b) adding / compounding", "{:.2f}x".format(5.5 / (100 * (gb - 1))))
gc = 1.025 ** 4
show("(c) 1.025^4", "{:.6f}".format(gc))
show("(c) annualized", "{:.4f}% -> {}".format(100 * (gc - 1), pct(gc - 1)))
show("(c) wrong 4 x 2.5%", "{:.2f}%".format(4 * 2.5))

print("\n=== Calculator walkthrough (displays) ===")
show("step 2  BA %CH (OLD 5048.62, NEW 1114.11, #PD 1)", "%CH = {}".format(d4(100 * r_fall)))
show("step 2  TI 1114.11/5048.62 | Ans-1 | Ans*100", "{} | {} | {}".format(ti4(frac), ti4(r_fall), ti4(100 * r_fall)))
show("step 3  BA NEW (OLD 1114.11, %CH 78, #PD 1)", "NEW = {}".format(d4(LOW * (1 + 78 / 100) ** 1)))
show("step 3  TI 1114.11*1.78 | Ans/5048.62", "{} | {}".format(ti4(up78), ti4(up78 / PEAK)))
show("step 4  BA %CH (OLD 1114.11, NEW 5048.62, #PD 1)", "%CH = {}".format(d4(100 * r_back)))
show("step 4  TI 5048.62/1114.11 | Ans-1 | Ans*100", "{} | {} | {}".format(ti4(back), ti4(r_back), ti4(100 * r_back)))
show("step 5  BA %CH (OLD 1114.11, NEW 5056.06, #PD 12.54)", "%CH = {}".format(d4(100 * ann)))
show("step 5  TI 5056.06/1114.11 | Ans^(1/12.54) | Ans-1 | Ans*100",
     "{} | {} | {} | {}".format(ti4(g_rec), ti4(1 + ann), ti4(ann), ti4(100 * ann)))
show("step 5  1/12.54", "{:.6f}".format(1 / 12.54))
# TVM cross-check: N = 12.54, PV = -1114.11, PMT = 0, FV = 5056.06 -> I/Y
show("step 6  TVM I/Y", "I/Y = {} / I%={}".format(d4(100 * ann), ti4(100 * ann)))
show("  check: 1114.11 x (1 + I/Y)^12.54", "{:.4f}".format(LOW * (1 + ann) ** T))

print("\n=== c1: index 3,200 -> 2,000, back to 3,200 in 4.5 years ===")
c1_fall = hpr(3200, 2000)
c1_need = 3200 / 2000 - 1
c1 = 100 * ((3200 / 2000) ** (1 / 4.5) - 1)
show("c1 fall HPR", "{:.4f}% (BA %CH = {})".format(100 * c1_fall, d4(100 * c1_fall)))
show("c1 gain needed", "{:.4f}% (BA %CH = {})".format(100 * c1_need, d4(100 * c1_need)))
show("c1 check L/(1-L) = 0.375/0.625", "{:.4f}".format(0.375 / 0.625))
show("c1 annualized (answer, percent)", "{!r} -> {:.4f} -> {:.2f}%".format(c1, c1, c1))
show("c1 TI: 1.6^(1/4.5) | Ans-1", "{} | {}".format(ti4(1.6 ** (1 / 4.5)), ti4(1.6 ** (1 / 4.5) - 1)))
show("c1 wrong: 60% / 4.5", "{:.2f}%".format(60 / 4.5))
show("c1 tolerance |11.01 - c1|", "{:.4f}".format(abs(11.01 - c1)))

print("\n=== c2: 50% loss needs 100% ===")
show("100 -> 50 -> back to 100: gain 50 on base 50", "{:.0f}%".format(100 * 50 / 50))
show("0.5 x 2", 0.5 * 2)

print("\n=== c3: +12% in year 1, +5% total over two years ===")
c3 = 100 * (1.05 / 1.12 - 1)
show("1.05 / 1.12", "{!r} -> {:.4f} (TI {})".format(1.05 / 1.12, 1.05 / 1.12, ti4(1.05 / 1.12)))
show("c3 year-2 return (answer, percent)", "{!r} -> {:.2f}%".format(c3, c3))
show("c3 BA: 1.05 / 1.12 - 1 =", d4(1.05 / 1.12 - 1))
show("c3 TI: 1.05/1.12-1", ti4(1.05 / 1.12 - 1))
show("c3 Δ%: OLD 112, NEW 105", "%CH = {}".format(d4(100 * (105 / 112 - 1))))
show("c3 wrong: 5 - 12", "{:.2f}%".format(5 - 12))
show("c3 check: 1.12 x 0.9375", "{:.4f}".format(1.12 * 0.9375))

print("\n=== c4: bought 50, dividend 1.50, sold 47 after six months ===")
c4_h = hpr(50, 47, 1.50)
show("HPR (6 months)", "{:.4f} -> {}".format(c4_h, pct(c4_h)))
c4 = annualize(c4_h, 2)
show("C (right) 0.97^2 - 1", "{!r} -> {:.4f} -> {}".format(c4, c4, pct(c4)))
show("    0.97^2", "{:.4f}".format(0.97 ** 2))
show("B (x 2) ", pct(2 * c4_h))
c4_nod = hpr(50, 47)
show("A (dividend left out) HPR", pct(c4_nod))
show("A 0.94^2 - 1", "{:.4f} -> {}".format(annualize(c4_nod, 2), pct(annualize(c4_nod, 2))))
show("    0.94^2", "{:.4f}".format(0.94 ** 2))
show("BA: .97 x² - 1 = / TI", "{} / {}".format(d4(0.97 ** 2 - 1), ti4(0.97 ** 2 - 1)))

print("\n=== Solution displays (keystroke lines) ===")
show("w1 BA NPV / TI npv(7,-2500,{600,900},{4,1})", "NPV = {} / {}".format(d4(w1), ti4(w1)))
show("w1 BA IRR / TI irr(-2500,{600,900},{4,1})", "IRR = {} / {}".format(d4(100 * irr(w1_cfs)), ti4(100 * irr(w1_cfs))))
show("c1 TI 2000/3200 | Ans-1 | Ans*100", "{} | {} | {}".format(ti4(2000 / 3200), ti4(2000 / 3200 - 1), ti4(100 * (2000 / 3200 - 1))))
show("c1 TI 3200/2000 | Ans-1 | Ans*100", "{} | {} | {}".format(ti4(3200 / 2000), ti4(3200 / 2000 - 1), ti4(100 * (3200 / 2000 - 1))))
show("c1 TI (3200/2000)^(1/4.5)-1 | Ans*100", "{} | {}".format(ti4(1.6 ** (1 / 4.5) - 1), ti4(c1)))
show("c1 BA %CH with #PD = 4.5", "%CH = {}".format(d4(c1)))
show("c3 BA x 100", d4(c3))
show("c4 BA 47 - 50 + 1.5 =", d4(47 - 50 + 1.5))
show("c4 BA / 50 = | + 1 = | x² | - 1 =", "{} | {} | {} | {}".format(d4((47 - 50 + 1.5) / 50), d4(c4_h + 1), d4((c4_h + 1) ** 2), d4((c4_h + 1) ** 2 - 1)))
show("c4 TI same chain", "{} | {} | {} | {}".format(ti4(c4_h), ti4(c4_h + 1), ti4((c4_h + 1) ** 2), ti4((c4_h + 1) ** 2 - 1)))
show("telescoping: (LOW/PEAK) x (REC/LOW) = REC/PEAK", "{:.6f} = {:.6f}".format(frac * g_rec, REC / PEAK))
show("L/(1-L) with 6-decimal inputs 0.779324/0.220676", "{:.4f}".format(0.779324 / 0.220676))
show("1/0.220676", "{:.4f}".format(1 / 0.220676))

print("\n=== Rounded forms used in the prose ===")
show("w2 growth factor 1.06^3 (4 dp)", "{:.4f}".format(g3))
show("headline: gain needed, whole percent", "{:.0f}%".format(100 * r_back))
show("headline: fall, whole percent", "{:.0f}%".format(-100 * r_fall))
show("story: years low -> record (about)", "{:.1f}".format(days_lr / 365.25))
show("78% rise as a share of the peak, whole percent", "{:.0f}%".format(100 * up78 / PEAK))
show("c1 fall / gain needed (2 dp)", "{:.2f}% / {:.2f}%".format(100 * c1_fall, 100 * c1_need))
show("c1 check L/(1-L) (2 dp)", "{:.2f}".format(0.375 / 0.625))
show("c2 factor to recover 50%: 1/(1 - 0.50)", "{:.2f}".format(1 / (1 - 0.50)))
show("why: 75% loss needs (whole percent)", "{:.0f}%".format(100 * needed(0.75)))
show("BA Δ% after 2ND CLR WORK", "OLD = {} , #PD = {}".format(d4(0), d4(1)))
