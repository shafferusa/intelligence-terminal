#!/usr/bin/env python3
"""Day 40 of CFA Math: every number that appears on day-40.html.

Approximate duration, money duration and PVBP
(CFA Program 2027 Level I, Fixed Income, Yield-Based Bond Duration Measures and Properties;
portfolio duration from Yield-Based Bond Convexity and Portfolio Properties).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime


def digits_fit(x, dec=4, width=10):
    """Decimals a 10-digit display can keep for x when set to `dec` decimals."""
    int_digits = len(str(int(abs(round(x, dec)))))
    return max(0, min(dec, width - int_digits))


def ba(x):
    """BA II Plus display: 4 decimals (fewer if the 10-digit display runs out), separators."""
    d = digits_fit(x)
    return "{:,.{}f}".format(x, d)


def ti(x):
    """TI-84 Plus display: FLOAT 4 (fewer decimals past 10 digits), no separators, no leading zero."""
    d = digits_fit(x)
    s = "{:.{}f}".format(x, d)
    if s.startswith("0."):
        s = s[1:]
    if s.startswith("-0."):
        s = "-" + s[2:]
    return s


def show(label, value):
    print("  {:<70} {}".format(label, value))


def price(coupon, n, y_pct, face=100.0):
    """Price per 100 of an annual-pay bond (TVM: N, I/Y, PMT, FV; CPT PV, sign flipped)."""
    y = y_pct / 100.0
    return sum(coupon / (1 + y) ** t for t in range(1, n + 1)) + face / (1 + y) ** n


def macdur(coupon, n, y_pct, face=100.0):
    """Exact Macaulay duration (Day 39's PV-weighted average time)."""
    y = y_pct / 100.0
    cfs = [coupon + (face if t == n else 0.0) for t in range(1, n + 1)]
    pv = [cf / (1 + y) ** t for t, cf in enumerate(cfs, start=1)]
    return sum(t * p for t, p in enumerate(pv, start=1)) / sum(pv)


def approx_moddur(pm, pp, p0, dy):
    return (pm - pp) / (2 * dy * p0)


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 39): MacDur 8.40, annual-pay yield 5%, yields fall 20 bp
md_w1 = 8.40 / 1.05
w1 = -md_w1 * (-0.20)
show("w1  ModDur = 8.40 / 1.05", "{!r} -> {}".format(md_w1, ba(md_w1)))
show("w1  %change = -ModDur x (-0.20) (answer, %)", "{!r} -> {} / TI {}".format(w1, ba(w1), ti(w1)))
show("w1  trap: MacDur x 0.20", "{:.2f}".format(8.40 * 0.20))
show("w1  trap: sign wrong", "{:.2f}".format(-w1))

# w2 (Day 10): CF0 = -800; 250 for 3 years; 300 in year 4; at 8%
cfs_w2 = [250, 250, 250, 300]
pv_in = sum(c / 1.08 ** t for t, c in enumerate(cfs_w2, start=1))
npv_w2 = -800 + pv_in
show("w2  PV of inflows at 8%", "{!r} -> {}".format(pv_in, ba(pv_in)))
show("w2  NPV (right)", "{!r} -> {} / TI {} -> {:.2f}".format(npv_w2, ba(npv_w2), ti(npv_w2), npv_w2))
show("w2  forgot CF0 (inflows only)", "{:.2f}".format(pv_in))
f1 = -800 + 250 / 1.08 + 300 / 1.08 ** 2
show("w2  F01 left at 1: 250 once, 300 lands in year 2", "{:.2f}".format(f1))
ann3 = (1 - 1.08 ** -3) / 0.08
show("w2  annuity factor 3 years 8%; 250 x it; 300/1.08^4",
     "{:.6f}; {:.4f}; {:.4f}".format(ann3, 250 * ann3, 300 / 1.08 ** 4))

# ------------------------------------------------------------------ story
print("\n=== Story: Silicon Valley Bank ===")
d9 = datetime.date(2023, 3, 9)
d10 = datetime.date(2023, 3, 10)
show("9 March 2023 weekday", d9.strftime("%A"))
show("10 March 2023 weekday", d10.strftime("%A"))
htm_cost, htm_fv, equity = 91.3, 76.2, 16.0
loss = htm_cost - htm_fv
show("HTM unrealized loss 91.3 - 76.2 ($bn)", "{:.1f}".format(loss))
show("from the unrounded 91.321 - 76.2 ($bn)", "{:.3f}".format(91.321 - 76.2))
show("loss / equity", "{:.1%}".format(loss / equity))
pct_loss = loss / htm_cost * 100
show("loss as % of cost 15.1 / 91.3", "{!r} -> {:.2f}%".format(pct_loss, pct_loss))
implied = pct_loss / 6.2
show("implied yield rise 16.54 / 6.2 (points)", "{!r} -> {:.2f}".format(implied, implied))
show("check with the rounded 16.54 / 6.2", "{:.4f}".format(16.54 / 6.2))
t5 = 3.99 - 1.37
show("5-year Treasury 1.37% -> 3.99%: rise (points)", "{:.2f}".format(t5))
loss_t5 = 6.2 * t5 / 100 * htm_cost
show("loss implied by 6.2 x 0.0262 x 91.3 ($bn)", "{!r} -> {:.1f}".format(loss_t5, loss_t5))
money_dur = 6.2 * htm_fv
show("money duration 6.2 x 76.2 ($bn)", "{!r} -> {:.2f}".format(money_dur, money_dur))
show("per 1-point rise (x 0.01) ($bn)", "{:.4f} -> {:.2f}".format(money_dur * 0.01, money_dur * 0.01))
show("per basis point (x 0.0001) ($ million)", "{:.4f} -> {:.1f}".format(money_dur * 0.0001 * 1000, money_dur * 0.0001 * 1000))
show("deposits tried to pull ($bn) (cited)", 42)

# ------------------------------------------------------------------ walkthrough
print("\n=== Walkthrough: 10-year 3% annual coupon, yield 4%, +/- 1 bp ===")
N, CPN, Y0, DY = 10, 3.0, 4.0, 0.0001
p0 = price(CPN, N, Y0)
pm = price(CPN, N, Y0 - 0.01)
pp = price(CPN, N, Y0 + 0.01)
show("PV_0 at 4.00% (STO 1 / A)", "{!r} -> BA PV = {} / TI {}".format(p0, ba(-p0), ti(p0)))
show("PV_- at 3.99% (STO 2 / B)", "{!r} -> BA PV = {} / TI {}".format(pm, ba(-pm), ti(pm)))
show("PV_+ at 4.01% (STO 3 / C)", "{!r} -> BA PV = {} / TI {}".format(pp, ba(-pp), ti(pp)))
show("PV_- - PV_+", "{!r} -> {:.4f}".format(pm - pp, pm - pp))
show("PV_- - PV_0 (gain for -1 bp)", "{:.6f} -> {:.4f}".format(pm - p0, pm - p0))
show("PV_0 - PV_+ (loss for +1 bp)", "{:.6f} -> {:.4f}".format(p0 - pp, p0 - pp))
one_down = (pm - p0) / (DY * p0)
one_up = (p0 - pp) / (DY * p0)
show("one-sided, yield down: (PV_- - PV_0)/(0.0001 x PV_0)", "{:.4f}".format(one_down))
show("one-sided, yield up:   (PV_0 - PV_+)/(0.0001 x PV_0)", "{:.4f}".format(one_up))
show("average of the two", "{:.4f}".format((one_down + one_up) / 2))
# Why it works: a big step (+/- 1 point) so the curve shows
p3 = price(CPN, N, 3.0)
p5 = price(CPN, N, 5.0)
show("why: price at 3% (coupon = yield, par)", "{!r} -> {:.4f}".format(p3, p3))
show("why: price at 5%", "{!r} -> {:.4f}".format(p5, p5))
show("why: gain 3%: PV - PV_0; loss 5%: PV_0 - PV", "{:.4f}; {:.4f}".format(p3 - p0, p0 - p5))
show("why: 0.01 x PV_0", "{:.6f}".format(0.01 * p0))
big_down = (p3 - p0) / (0.01 * p0)
big_up = (p0 - p5) / (0.01 * p0)
show("why: one-sided slope, yield down 1 point", "{!r} -> {:.4f} -> {:.2f}".format(big_down, big_down, big_down))
show("why: one-sided slope, yield up 1 point", "{!r} -> {:.4f} -> {:.2f}".format(big_up, big_up, big_up))
big_c = approx_moddur(p3, p5, p0, 0.01)
show("why: two-sided with +/- 1 point = average", "{!r} -> {:.4f} -> {:.2f}".format(big_c, big_c, big_c))
show("why: from the rounded prices (100 - 84.5565)/(0.02 x 91.8891)",
     "{:.4f}".format((100 - 84.5565) / (0.02 * 91.8891)))
show("why: rounded 8.1109 / 0.918891, 7.3326 / 0.918891",
     "{:.4f}, {:.4f}".format(8.1109 / 0.918891, 7.3326 / 0.918891))
amd = approx_moddur(pm, pp, p0, DY)
show("2 x 0.0001 x PV_0", "{:.8f}".format(2 * DY * p0))
show("0.1541 / 0.01837782 (rounded numerator)", "{:.4f}".format(0.1541 / 0.01837782))
show("ApproxModDur (STO 4 / D)", "{!r} -> {} / TI {}".format(amd, ba(amd), ti(amd)))
amd_r = approx_moddur(round(pm, 4), round(pp, 4), round(p0, 4), DY)
show("ApproxModDur from 4-decimal prices (retyped)", "{!r} -> {:.4f}".format(amd_r, amd_r))
exact_md = macdur(CPN, N, Y0)
show("exact MacDur (Day 39 method)", "{!r} -> {:.4f}".format(exact_md, exact_md))
show("exact ModDur = MacDur / 1.04", "{:.4f}".format(exact_md / 1.04))
amac = amd * 1.04
show("ApproxMacDur = ApproxModDur x 1.04", "{!r} -> {} / TI {}".format(amac, ba(amac), ti(amac)))
pvbp100 = (pm - pp) / 2
show("PVBP per 100 = (PV_- - PV_+)/2", "{!r} -> {} / TI {}".format(pvbp100, ba(pvbp100), ti(pvbp100)))
show("  check: ApproxModDur x 0.0001 x PV_0", "{:.6f}".format(amd * DY * p0))
show("  PVBP per 100 in cents", "{:.2f}".format(pvbp100 * 100))
par = 10_000_000
pos = par * p0 / 100
show("position value 10,000,000 x RCL 1 / 100", "{!r} -> {} / TI {}".format(pos, ba(pos), ti(pos)))
mdur = amd * pos
show("money duration = ApproxModDur x position", "{!r} -> {} / TI {}".format(mdur, ba(mdur), ti(mdur)))
chg = -mdur * 0.02
show("estimated change for +200 bp (answer)", "{!r} -> {} / TI {}".format(chg, ba(chg), ti(chg)))
show("  rounded to the dollar", "{:,.0f}".format(chg))
show("  as % of position", "{:.4f}%".format(chg / pos * 100))
show("  -ApproxModDur x 2 (%)", "{:.4f}%".format(-amd * 2))
show("position PVBP = money duration x 0.0001", "{:.4f} -> {:,.0f}".format(mdur * 0.0001, mdur * 0.0001))
show("position PVBP = (PV_- - PV_+)/2 x 100,000", "{:.4f}".format(pvbp100 * par / 100))
p6 = price(CPN, N, 6.0)
full = (p6 - p0) / 100 * par
show("full repricing at 6%: price per 100", "{:.4f}".format(p6))
show("full repricing: change on the position", "{!r} -> {:,.0f}".format(full, full))
show("  as % of position", "{:.4f}%".format((p6 / p0 - 1) * 100))
show("duration overstates the loss by", "{:,.0f}".format(-chg + full))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: PV_0 98.50, PV_- 100.80, PV_+ 96.25, +/- 25 bp ===")
w_num = 100.80 - 96.25
w_den = 2 * 0.0025 * 98.50
w_amd = w_num / w_den
show("PV_- - PV_+", "{:.4f}".format(w_num))
show("2 x 0.0025 x 98.50", "{:.4f}".format(w_den))
show("ApproxModDur", "{!r} -> {:.4f}".format(w_amd, w_amd))
w_md = w_amd * 5_000_000
show("money duration x $5,000,000", "{!r} -> {:,.0f}".format(w_md, w_md))
w_chg = -w_md * 0.0010
show("change for +10 bp", "{!r} -> {:,.0f}".format(w_chg, w_chg))
show("holding PVBP = money duration x 0.0001", "{:,.2f}".format(w_md * 0.0001))
show("  one-sided down (100.80 - 98.50)/(0.0025 x 98.50)", "{:.4f}".format((100.80 - 98.50) / (0.0025 * 98.50)))
show("  one-sided up (98.50 - 96.25)/(0.0025 x 98.50)", "{:.4f}".format((98.50 - 96.25) / (0.0025 * 98.50)))

# ------------------------------------------------------------------ traps
print("\n=== Traps (walkthrough bond) ===")
show("forgot the 2: (PV_- - PV_+)/(0.0001 x PV_0)", "{:.4f}".format((pm - pp) / (DY * p0)))
show("1 bp typed as 0.01: (PV_- - PV_+)/(2 x 0.01 x PV_0)", "{:.4f}".format((pm - pp) / (2 * 0.01 * p0)))
mdur_par = amd * par
show("money duration on par instead of market value", "{:,.0f}".format(mdur_par))
show("  -> loss for +200 bp", "{:,.0f}".format(mdur_par * 0.02))

# ------------------------------------------------------------------ check questions
print("\n=== Check questions ===")
# c1: 8-year 2.5% annual coupon at 3.5%, +/- 1 bp
c_p0 = price(2.5, 8, 3.5)
c_pm = price(2.5, 8, 3.49)
c_pp = price(2.5, 8, 3.51)
c1 = approx_moddur(c_pm, c_pp, c_p0, DY)
show("c1  PV_0 at 3.50%", "{!r} -> BA PV = {} / TI {}".format(c_p0, ba(-c_p0), ti(c_p0)))
show("c1  PV_- at 3.49%", "{!r} -> BA PV = {} / TI {}".format(c_pm, ba(-c_pm), ti(c_pm)))
show("c1  PV_+ at 3.51%", "{!r} -> BA PV = {} / TI {}".format(c_pp, ba(-c_pp), ti(c_pp)))
show("c1  ApproxModDur (answer)", "{!r} -> {} / TI {} -> {:.2f}".format(c1, ba(c1), ti(c1), c1))
c1r = approx_moddur(round(c_pm, 4), round(c_pp, 4), round(c_p0, 4), DY)
show("c1  from retyped 4-decimal prices", "{:.4f} (diff {:.4f})".format(c1r, c1r - c1))
show("c1  PVBP per 100", "{} / TI {}".format(ba((c_pm - c_pp) / 2), ti((c_pm - c_pp) / 2)))
show("c1  exact MacDur, ModDur", "{:.4f}, {:.4f}".format(macdur(2.5, 8, 3.5), macdur(2.5, 8, 3.5) / 1.035))
show("c1  ApproxMacDur x 1.035", "{:.4f}".format(c1 * 1.035))
show("c1  forgot the 2", "{:.4f}".format((c_pm - c_pp) / (DY * c_p0)))

# c2: conceptual (numbers from the walkthrough used in the explanation)
show("c2  one-sided down / up / average (walkthrough)", "{:.4f} / {:.4f} / {:.4f}".format(one_down, one_up, amd))

# c3: portfolio duration, market-value weights
mv = [6_000_000, 4_000_000]
dd = [4.0, 9.0]
parv = [6_500_000, 3_500_000]
tot = sum(mv)
dp = sum(m * d for m, d in zip(mv, dd)) / tot
show("c3  weights 6/10, 4/10", "{:.2f}, {:.2f}".format(mv[0] / tot, mv[1] / tot))
show("c3  portfolio duration (market-value weights)", "{:.4f}".format(dp))
c3 = -dp * tot * 0.0025
show("c3  change for +25 bp (answer)", "{!r} -> {:,.0f}".format(c3, c3))
show("c3  bond by bond: -4.0 x 6m x 0.0025, -9.0 x 4m x 0.0025",
     "{:,.0f}, {:,.0f}".format(-4.0 * 6e6 * 0.0025, -9.0 * 4e6 * 0.0025))
dp_par = sum(p * d for p, d in zip(parv, dd)) / sum(parv)
show("c3  trap: par weights 6.5/10, 3.5/10 -> duration", "{:.4f}".format(dp_par))
show("c3  trap: loss with par-weighted duration", "{:,.0f}".format(dp_par * tot * 0.0025))
show("c3  simple average (4 + 9)/2", "{:.2f}".format(sum(dd) / 2))
show("c3  bond prices implied (MV/par x 100)", "{:.4f}, {:.4f}".format(mv[0] / parv[0] * 100, mv[1] / parv[1] * 100))

# c4: PV_0 101.50, PV_- 104.10, PV_+ 99.00, +/- 50 bp
c4 = (104.10 - 99.00) / (2 * 0.005 * 101.50)
show("c4  ApproxModDur (B)", "{!r} -> {:.2f}".format(c4, c4))
show("c4  forgot the 2 (A)", "{:.4f} -> {:.2f}".format((104.10 - 99.00) / (0.005 * 101.50), (104.10 - 99.00) / (0.005 * 101.50)))
show("c4  divided by par 100 (C)", "{:.4f} -> {:.2f}".format((104.10 - 99.00) / (2 * 0.005 * 100), (104.10 - 99.00) / (2 * 0.005 * 100)))
show("c4  2 x 0.005 x 101.50", "{:.4f}".format(2 * 0.005 * 101.50))

# c5: treasurer, $50bn at ModDur 5
md5 = 5 * 50
show("c5  money duration 5 x $50bn ($bn)", md5)
show("c5  per 1-point rise ($bn)", "{:.2f}".format(md5 * 0.01))
show("c5  PVBP ($ million)", "{:.0f}".format(md5 * 0.0001 * 1000))
