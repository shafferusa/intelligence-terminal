#!/usr/bin/env python3
"""Day 41 of CFA Math: every number that appears on day-41.html.

Convexity: correcting the duration estimate
(CFA Program 2027 Level I, Fixed Income, Yield-Based Bond Convexity and Portfolio Properties).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""


def ba(x):
    """BA II Plus display: 4 decimals, thousands separators."""
    return "{:,.4f}".format(x)


def ti(x):
    """TI-84 Plus display: FLOAT 4, no separators, no leading zero."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    if s.startswith("-0."):
        s = "-" + s[2:]
    return s


def show(label, value):
    print("  {:<70} {}".format(label, value))


def price(n, coupon, y_pct, face=100.0):
    """Annual-pay bond price per 100 (the TVM PV with the sign flipped), y in percent."""
    r = y_pct / 100.0
    return sum(coupon / (1 + r) ** t for t in range(1, n + 1)) + face / (1 + r) ** n


def analytic(n, coupon, y_pct, face=100.0):
    """Exact Macaulay/modified duration and convexity (annual pay), for reference."""
    r = y_pct / 100.0
    cfs = [coupon + (face if t == n else 0.0) for t in range(1, n + 1)]
    pv = [cf / (1 + r) ** t for t, cf in enumerate(cfs, start=1)]
    p = sum(pv)
    mac = sum(t * v for t, v in enumerate(pv, start=1)) / p
    con = sum(t * (t + 1) * v for t, v in enumerate(pv, start=1)) / p / (1 + r) ** 2
    return mac, mac / (1 + r), con


def approx(pm, p0, pp, dy):
    """ApproxModDur and ApproxCon from three prices, dy as a decimal."""
    d = (pm - pp) / (2 * dy * p0)
    c = (pm + pp - 2 * p0) / (dy ** 2 * p0)
    return d, c


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 40): ApproxModDur from three given prices, +/-30 bp
w_p0, w_pm, w_pp, w_dy = 97.40, 99.95, 94.93, 0.003
w1 = (w_pm - w_pp) / (2 * w_dy * w_p0)
show("w1  PV- - PV+ = 99.95 - 94.93", "{:.4f}".format(w_pm - w_pp))
show("w1  2 x 0.003 x 97.40", "{:.4f}".format(2 * w_dy * w_p0))
show("w1  ApproxModDur (answer)", "{!r} -> BA {} / TI {} -> {:.2f}".format(w1, ba(w1), ti(w1), w1))
show("w1  trap: forgot the 2", "{:.4f}".format((w_pm - w_pp) / (w_dy * w_p0)))
show("w1  trap: divided by par 100 instead of PV0", "{:.4f}".format((w_pm - w_pp) / (2 * w_dy * 100)))
show("w1  (a convex bond: gain PV- - PV0, loss PV0 - PV+)", "{:.2f} / {:.2f}".format(w_pm - w_p0, w_p0 - w_pp))

# w2 (Day 34): mean-variance utility U = E - 0.5 A sigma^2
A, E, sd = 3, 0.10, 0.18
u = E - 0.5 * A * sd ** 2
u_noh = E - A * sd ** 2
u_sd = E - 0.5 * A * sd
show("w2  0.5 x 3 x 0.18^2", "{:.4f}".format(0.5 * A * sd ** 2))
show("w2  U (answer, %)", "{!r} -> {:.2f}%".format(u, u * 100))
show("w2  trap: forgot the 1/2 (%)", "{:.2f}%".format(u_noh * 100))
show("w2  trap: sigma not squared (%)", "{:.2f}%".format(u_sd * 100))
show("w2  BA: .1 - .5 x 3 x .18 x2 =", "{} / TI {}".format(ba(u), ti(u)))

# ------------------------------------------------------------------ story / walkthrough bond
print("\n=== The stylized gilt: 30 years, 0.5% annual coupon (story + walkthrough) ===")
N, CPN = 30, 0.5
p0 = price(N, CPN, 3.7)
pm = price(N, CPN, 3.6)
pp = price(N, CPN, 3.8)
p51 = price(N, CPN, 5.1)
p23 = price(N, CPN, 2.3)
dy, move = 0.001, 0.014
D, C = approx(pm, p0, pp, dy)
mac, mod, con = analytic(N, CPN, 3.7)
show("PV0 at 3.7% (BA PV = ...)", "{!r} -> BA {} / TI {}".format(p0, ba(-p0), ti(-p0)))
show("PV- at 3.6%", "{!r} -> BA {} / TI {}".format(pm, ba(-pm), ti(pm)))
show("PV+ at 3.8%", "{!r} -> BA {} / TI {}".format(pp, ba(-pp), ti(pp)))
show("gain for a 10 bp fall: PV- - PV0", "{:.4f}".format(pm - p0))
show("loss for a 10 bp rise: PV0 - PV+", "{:.4f}".format(p0 - pp))
show("gain - loss = PV- + PV+ - 2 PV0 (full precision)", "{!r} -> {:.4f}".format(pm + pp - 2 * p0, pm + pp - 2 * p0))
show("gain - loss from 4-decimal prices", "{:.4f}".format(round(pm, 4) + round(pp, 4) - 2 * round(p0, 4)))
show("ApproxModDur (STO 4 / D)", "{!r} -> {} / {}".format(D, ba(D), ti(D)))
show("ApproxCon (STO 5 / E)", "{!r} -> {} / {}".format(C, ba(C), ti(C)))
Dr, Cr = approx(round(pm, 4), round(p0, 4), round(pp, 4), dy)
show("ApproxModDur from 4-decimal prices", "{:.4f}".format(Dr))
show("ApproxCon from 4-decimal prices (rounding trap)", "{:.4f} -> {:.1f}".format(Cr, Cr))
show("analytic MacDur / ModDur / convexity at 3.7%", "{:.4f} / {:.4f} / {:.4f}".format(mac, mod, con))
show("story: 25.4 x 1.4", "{:.2f}".format(25.4 * 1.4))
dur_only = -D * move * 100
conv_term = 0.5 * C * move ** 2 * 100
est = dur_only + conv_term
exact = (p51 / p0 - 1) * 100
show("duration only, % (RCL 4 +/- x .014 x 100)", "{!r} -> {} / {}".format(dur_only, ba(dur_only), ti(dur_only)))
show("convexity term, % (.5 x RCL 5 x .014^2 x 100)", "{!r} -> {} / {:.2f}".format(conv_term, ba(conv_term), conv_term))
show("duration + convexity, % (walkthrough answer)", "{!r} -> {} / {}".format(est, ba(est), ti(est)))
show("PV at 5.1%", "{!r} -> BA {} / TI {}".format(p51, ba(-p51), ti(p51)))
show("exact: PV(5.1)/PV0 - 1 (decimal)", "{} / {}".format(ba(p51 / p0 - 1), ti(p51 / p0 - 1)))
show("exact %", "{!r} -> {} / {:.2f}".format(exact, ba(exact), exact))
show("duration overstated the loss by (pp)", "{:.4f} -> {:.1f}".format(exact - dur_only, exact - dur_only))
show("duration + convexity misses by (pp)", "{:.4f} -> {:.1f}".format(est - exact, est - exact))
show("duration's straight-line loss as a share of the true loss", "{:.4f}".format(dur_only / exact))
# reverse move
exact_dn = (p23 / p0 - 1) * 100
show("reverse: PV at 2.3%", "{:.4f}".format(p23))
show("reverse: exact % gain", "{:.4f} -> {:.2f}".format(exact_dn, exact_dn))
show("reverse: duration only", "{:.4f} -> {:.2f}".format(-dur_only, -dur_only))
show("reverse: duration + convexity", "{:.4f} -> {:.2f}".format(-dur_only + conv_term, -dur_only + conv_term))
# traps on the walkthrough numbers
show("trap: no 1/2 -> convexity term", "{:.4f} -> estimate {:.2f}".format(2 * conv_term, dur_only + 2 * conv_term))
show("trap: minus sign on convexity when yields rise", "{:.2f}".format(dur_only - conv_term))
show("trap: dYield in percent, 0.5 x C x 1.4^2", "{:.2f}".format(0.5 * C * 1.4 ** 2))
show("trap: ratio 1.4^2 / 0.014^2", "{:.0f}".format(1.4 ** 2 / 0.014 ** 2))
# convexity grows with the square: double the move
for mv in (0.007, 0.014, 0.028):
    show("  move {:.3f}: duration-only error vs exact? convexity term %".format(mv),
         "{:.4f}".format(0.5 * C * mv ** 2 * 100))
small = 0.001
small_exact = (pp / p0 - 1) * 100
show("10 bp rise: duration only / with convexity / exact (%)",
     "{:.4f} / {:.4f} / {:.4f}".format(-D * small * 100, -D * small * 100 + 0.5 * C * small ** 2 * 100, small_exact))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: 10-year 4% annual coupon at 5% ===")
we0, wem, wep, we65 = 92.2783, 94.1377, 90.4639, 82.0279
show("check prices: 5%, 4.75%, 5.25%, 6.5%", "{:.4f} {:.4f} {:.4f} {:.4f}".format(
    price(10, 4, 5), price(10, 4, 4.75), price(10, 4, 5.25), price(10, 4, 6.5)))
wd = 0.0025
show("PV- - PV+", "{:.4f}".format(wem - wep))
show("2 x 0.0025 x 92.2783", "{:.7f}".format(2 * wd * we0))
WD = (wem - wep) / (2 * wd * we0)
show("ApproxModDur", "{!r} -> {:.4f}".format(WD, WD))
show("PV- + PV+", "{:.4f}".format(wem + wep))
show("2 x PV0", "{:.4f}".format(2 * we0))
show("numerator PV- + PV+ - 2 PV0", "{:.4f}".format(wem + wep - 2 * we0))
show("0.0025^2", "{:.8f}".format(wd ** 2))
show("0.0025^2 x 92.2783", "{:.9f}".format(wd ** 2 * we0))
WC = (wem + wep - 2 * we0) / (wd ** 2 * we0)
show("ApproxCon (from the given 4-decimal prices)", "{!r} -> {:.4f} -> {:.2f}".format(WC, WC, WC))
show("page: 0.0450 / 0.00057674 (denominator as printed)", "{:.4f}".format(0.0450 / 0.00057674))
show("page: 3.6738 / 0.4613915", "{:.4f}".format(3.6738 / 0.4613915))
show("page: 1/2 x 78.0248 x 0.000225 (decimal)", "{:.8f}".format(0.5 * 78.0248 * 0.000225))
_, WCfull = approx(price(10, 4, 4.75), price(10, 4, 5), price(10, 4, 5.25), wd)
show("ApproxCon from full-precision prices (reference)", "{:.4f}".format(WCfull))
wdt = -WD * 0.015 * 100
wct = 0.5 * WC * 0.015 ** 2 * 100
show("duration term %", "{:.4f}".format(wdt))
show("0.015^2", "{:.6f}".format(0.015 ** 2))
show("convexity term %", "{:.4f}".format(wct))
show("estimate %", "{:.4f}".format(wdt + wct))
wex = (we65 / we0 - 1) * 100
show("exact %", "{:.4f}".format(wex))
show("duration alone missed by / with convexity missed by", "{:.2f} / {:.2f}".format(wdt - wex, wdt + wct - wex))
show("gilt ApproxCon / this bond's", "{:.2f}".format(C / WC))

# ------------------------------------------------------------------ check questions
print("\n=== c1: 20-year 1% annual bond at 3%, +120 bp ===")
q0 = price(20, 1, 3.0)
qm = price(20, 1, 2.9)
qp = price(20, 1, 3.1)
q42 = price(20, 1, 4.2)
QD, QC = approx(qm, q0, qp, 0.001)
show("PV at 3.0 / 2.9 / 3.1 (BA PV =)", "{} / {} / {}".format(ba(-q0), ba(-qm), ba(-qp)))
show("TI (-)tvm_PV values", "{} / {} / {}".format(ti(q0), ti(qm), ti(qp)))
show("ApproxModDur", "{!r} -> {}".format(QD, ba(QD)))
show("ApproxCon", "{!r} -> {}".format(QC, ba(QC)))
c1_dur = -QD * 0.012 * 100
c1_con = 0.5 * QC * 0.012 ** 2 * 100
c1 = c1_dur + c1_con
show("duration only %", "{} / {}".format(ba(c1_dur), ti(c1_dur)))
show("convexity term %", "{}".format(ba(c1_con)))
show("c1 answer: duration + convexity %", "{!r} -> {} / {}".format(c1, ba(c1), ti(c1)))
show("exact at 4.2%: price / %", "{:.4f} / {:.4f}".format(q42, (q42 / q0 - 1) * 100))
QDr, QCr = approx(round(qm, 4), round(q0, 4), round(qp, 4), 0.001)
c1r = -QDr * 1.2 + 0.5 * QCr * 0.012 ** 2 * 100
show("c1 from 4-decimal prices (tolerance check)", "Dur {:.4f} Con {:.4f} -> {:.4f} (diff {:.4f})".format(QDr, QCr, c1r, c1r - c1))
c1rr = -round(QD, 2) * 1.2 + 0.5 * round(QC, 1) * 0.012 ** 2 * 100
show("c1 from Dur to 2 dp, Con to 1 dp", "{:.4f} (diff {:.4f})".format(c1rr, c1rr - c1))
show("c1 trap: no 1/2", "{:.4f} -> {:.2f}".format(c1_dur + 2 * c1_con, c1_dur + 2 * c1_con))
show("c1 trap: minus on convexity", "{!r} -> {:.2f}".format(c1_dur - c1_con, c1_dur - c1_con))
show("c1 terms to 2 dp (solution line)", "{:.2f} + {:.2f} = {:.2f}".format(c1_dur, c1_con, c1))
show("c1 exact to 2 dp", "{:.2f}".format((q42 / q0 - 1) * 100))

print("\n=== c2: walkthrough bond, +/-140 bp (explanation numbers) ===")
show("yields up: duration / exact / dur+con", "{:.2f} / {:.2f} / {:.2f}".format(dur_only, exact, est))
show("yields down: duration / exact / dur+con", "{:.2f} / {:.2f} / {:.2f}".format(-dur_only, exact_dn, -dur_only + conv_term))

print("\n=== c3: GBP 50 million gilt position, AnnModDur 18.5, AnnConvexity 420, +150 bp ===")
pos, md, cv, sh = 50.0, 18.5, 420.0, 0.015
money_dur = md * pos
money_con = cv * pos
c3_d = -money_dur * sh
c3_c = 0.5 * money_con * sh ** 2
c3 = c3_d + c3_c
show("money duration (GBP m)", "{:.4f}".format(money_dur))
show("money convexity (GBP m)", "{:.4f}".format(money_con))
show("duration part (GBP m)", "{:.4f}".format(c3_d))
show("0.015^2", "{:.6f}".format(sh ** 2))
show("convexity part (GBP m)", "{:.4f}".format(c3_c))
show("c3 answer (GBP m)", "{!r} -> {:.4f}".format(c3, c3))
show("c3 as a % of the position", "{:.4f}".format(c3 / pos * 100))
show("c3 trap: no 1/2", "{:.4f}".format(c3_d + 2 * c3_c))
show("BA/TI chain display (millions)", "{} / {}".format(ba(c3), ti(c3)))

print("\n=== c4: ModDur 7.2, convexity 65, +75 bp ===")
m4, k4, d4 = 7.2, 65.0, 0.0075
c4_right = (-m4 * d4 + 0.5 * k4 * d4 ** 2) * 100
c4_noh = (-m4 * d4 + k4 * d4 ** 2) * 100
c4_neg = (-m4 * d4 - 0.5 * k4 * d4 ** 2) * 100
show("duration term %", "{:.4f}".format(-m4 * d4 * 100))
show("0.0075^2", "{:.8f}".format(d4 ** 2))
show("convexity term %", "{:.4f}".format(0.5 * k4 * d4 ** 2 * 100))
show("C (right)", "{:.4f}".format(c4_right))
show("forgot the 1/2", "{:.4f}".format(c4_noh))
show("minus sign on convexity", "{:.4f}".format(c4_neg))
show("BA / TI decimal display of the right answer", "{} / {}".format(ba(c4_right / 100), ti(c4_right / 100)))
show("options to 2 dp: right / no 1/2 / minus sign", "{:.2f} / {:.2f} / {:.2f}".format(c4_right, c4_noh, c4_neg))
show("convexity points with 1/2 / without (2 dp)", "{:.2f} / {:.2f}".format(0.5 * k4 * d4 ** 2 * 100, k4 * d4 ** 2 * 100))
show("decimal terms: -7.2 x 0.0075 / 1/2 x 65 x 0.0075^2", "{:.4f} / {:.4f}".format(-m4 * d4, 0.5 * k4 * d4 ** 2))

print("\n=== Release date ===")
import datetime
start = datetime.date(2026, 11, 16)
show("Day 41 due (start_date + 40)", (start + datetime.timedelta(days=40)).isoformat())
