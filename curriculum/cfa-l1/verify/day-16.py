#!/usr/bin/env python3
"""Day 16 of CFA Math: every number that appears on day-16.html.

Yield to maturity, call and worst
(CFA Program 2027 Level I, Fixed Income, Yield and Yield Spread Measures for Fixed-Rate Bonds).
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
    print("  {:<66} {}".format(label, value))


def annuity_factor(r, n):
    """Day 5: PV of 1 a period for n periods, first payment at t = 1."""
    return (1 - (1 + r) ** -n) / r


def bond_pv(pmt, fv, r, n):
    """Price: n coupons of pmt, then fv with the last one; r per period.
    Summed term by term (the definition), not from the annuity formula."""
    return sum(pmt / (1 + r) ** t for t in range(1, n + 1)) + fv / (1 + r) ** n


def tvm_pv(n, iy, pmt, fv):
    """What CPT PV shows (END mode, P/Y = C/Y = 1): the negative of the price."""
    r = iy / 100
    if r == 0:
        return -(pmt * n + fv)
    return -(pmt * annuity_factor(r, n) + fv / (1 + r) ** n)


def tvm_iy(n, pv, pmt, fv):
    """What CPT I/Y shows (END mode, P/Y = C/Y = 1), in percent per period.
    pv is negative (paid), pmt and fv positive (received). Bisection: the
    PV of the received cash falls as the rate rises, so one rate fits."""
    price = -pv
    lo, hi = -0.99, 1.0
    for _ in range(300):
        mid = (lo + hi) / 2
        if bond_pv(pmt, fv, mid, n) > price:
            lo = mid
        else:
            hi = mid
    return 100 * (lo + hi) / 2


def tvm_pmt(n, iy, pv, fv):
    """What CPT PMT shows (END mode): pv received (positive), so PMT is negative."""
    r = iy / 100
    return -(pv + fv / (1 + r) ** n) / annuity_factor(r, n)


def eff(nom, m):
    """ICONV EFF / TI-84 Eff(nom, m): percent in, percent out."""
    return 100 * ((1 + nom / 100 / m) ** m - 1)


def nom(eff_pct, m):
    """ICONV NOM / TI-84 Nom(eff, m)."""
    return 100 * m * ((1 + eff_pct / 100) ** (1 / m) - 1)


# ------------------------------------------------------------------ warm-up
print("=== Warm-up ===")
# w1 (Day 15): 5-year bond, 8% coupon paid semiannually, market yield 6%
w1 = tvm_pv(10, 3, 4, 100)
show("w1  N=10 I/Y=3 PMT=4 FV=100: CPT PV", "{!r} -> BA PV = {} / TI PV={}".format(w1, ba(w1), ti(w1)))
show("w1  term by term at 3% per half-year", "{:.6f}".format(bond_pv(4, 100, 0.03, 10)))
af = annuity_factor(0.03, 10)
show("w1  annuity factor (1 - 1.03^-10)/0.03", "{:.6f}".format(af))
show("w1  coupons 4 x factor", "{:.4f}".format(4 * af))
show("w1  par 100/1.03^10 (1.03^10 = {:.6f})".format(1.03 ** 10), "{:.4f}".format(100 / 1.03 ** 10))
show("w1  answer to two decimals", "{:.2f}".format(-w1))
show("w1  trap: annual inputs N=5 I/Y=6 PMT=8", "{:.4f}".format(-tvm_pv(5, 6, 8, 100)))
show("w1  trap: coupon not halved N=10 I/Y=3 PMT=8", "{:.4f}".format(-tvm_pv(10, 3, 8, 100)))

# w2 (Day 9): $150,000, 30 years, 6% stated annual, monthly payments
w2pmt = tvm_pmt(360, 0.5, 150000, 0)
w2int = 0.005 * 150000
show("w2  PMT (N=360, I/Y=0.5, PV=150000, FV=0)", "{!r} -> BA {} / TI {}".format(w2pmt, ba(w2pmt), ti(w2pmt)))
show("w2  interest in payment 1: 0.005 x 150,000 (A, right)", "{:,.2f}".format(w2int))
show("w2  principal in payment 1: PMT - interest (B)", "{:,.2f}".format(-w2pmt - w2int))
show("w2  annual rate x balance: 0.06 x 150,000 (C)", "{:,.2f}".format(0.06 * 150000))
show("w2  balance after payment 1", "{:,.2f}".format(150000 - (-w2pmt - w2int)))
show("w2  C's 'more than ten monthly payments': 10 x PMT vs 9,000", "{:,.2f} < 9,000.00: {}".format(-10 * w2pmt, -10 * w2pmt < 0.06 * 150000))

# ------------------------------------------------------------------ story
print("\n=== Story: Argentina's 100-year bond, 19 June 2017 ===")
show("years to maturity: 2117 - 2017", 2117 - 2017)
show("sold ($bn), orders ($bn), orders / sold", "2.75, 9.75, {:.2f}".format(9.75 / 2.75))
show("price per 100 of par; discount below par", "90, {}".format(100 - 90))
show("cash raised per $2.75bn of par at 90 ($bn)", "{:.3f}".format(2.75 * 0.90))
show("annual coupon per 100 (7.125% of 100)", "{:.3f}".format(7.125))
show("semiannual coupon per 100 (7.125 / 2)", "{:.4f}".format(7.125 / 2))
show("half-years to maturity (100 x 2)", 100 * 2)
show("coupons over the century per 100 (200 x 3.5625)", "{:.2f}".format(200 * 7.125 / 2))
show("coupons + the 100 repaid", "{:.2f}".format(200 * 7.125 / 2 + 100))
show("Bloomberg's reported yield (%)", "7.917")
show("ninth default in 2020: years after the sale", 2020 - 2017)
show("defaults: eight before the sale, then one more", 8 + 1)

# ------------------------------------------------------------------ why it works
print("\n=== Why it works: 4-year bond, 6% annual coupon, price 96.6128 ===")
P = bond_pv(6, 100, 0.07, 4)
show("price at 7% (what the market pays; = worked example price)", "{!r} -> {:.4f}".format(P, P))
for r in (0.06, 0.08, 0.07):
    show("try r = {:.0%}: PV of 6, 6, 6, 106".format(r), "{:.4f}".format(bond_pv(6, 100, r, 4)))
ytm_we = tvm_iy(4, -round(P, 4), 6, 100)
show("CPT I/Y with PV = -96.6128 (the solver's search)", "{!r} -> {}".format(ytm_we, ba(ytm_we)))
npv7 = -round(P, 4) + bond_pv(6, 100, 0.07, 4)
show("NPV at 7% of -96.6128, +6, +6, +6, +106", "{:.4f}".format(npv7))
cy_we = 6 / P * 100
show("coupon part: 6 / 96.6128 (current yield, %)", "{:.4f}".format(cy_we))
gain = 100 - P
show("climb to par: 100 - 96.6128", "{:.4f}".format(gain))
show("  per year straight-line: / 4", "{:.4f}".format(gain / 4))
show("YTM minus current yield (points)", "{:.4f}".format(7 - cy_we))
# periodicity, generic
show("8% on a semiannual-bond basis: 1.04^2", "{:.4f}".format(1.04 ** 2))
show("  = annual-pay yield (%)", "{:.4f}".format(eff(8, 2)))
show("  check general formula: (1 + 0.08/2)^2 vs (1 + 0.0816/1)^1", "{:.6f} vs {:.6f}".format(1.04 ** 2, 1 + eff(8, 2) / 100))

# ------------------------------------------------------------------ worked example
print("\n=== Worked example: 4-year 6% annual coupon at 96.6128 ===")
terms = [6 / 1.07, 6 / 1.07 ** 2, 6 / 1.07 ** 3, 106 / 1.07 ** 4]
for i, t in enumerate(terms, 1):
    show("term {}".format(i), "{:.4f}".format(t))
show("1.07^2, 1.07^3, 1.07^4", "{:.6f}, {:.6f}, {:.6f}".format(1.07 ** 2, 1.07 ** 3, 1.07 ** 4))
show("sum of rounded terms", "{:.4f}".format(sum(round(t, 4) for t in terms)))
show("(a) sum, unrounded", "{:.4f}".format(sum(terms)))
show("(b) current yield 6 / 96.6128 (%)", "{!r} -> {:.4f} -> {:.2f}".format(6 / 96.6128 * 100, 6 / 96.6128 * 100, 6 / 96.6128 * 100))
amort = (100 - 96.6128) / 4
show("(c) discount 100 - 96.6128", "{:.4f}".format(100 - 96.6128))
show("(c) per year: / 4", "{:.4f}".format(amort))
sy = (6 + amort) / 96.6128 * 100
show("(c) (6 + 0.8468) / 96.6128 (%)", "{:.4f} (rounded 0.8468: {:.4f}) -> {:.2f}".format(sy, (6 + 0.8468) / 96.6128 * 100, sy))
show("(d) 1.07^0.5", "{:.6f}".format(1.07 ** 0.5))
rs = 2 * (1.07 ** 0.5 - 1) * 100
show("(d) 2 x (1.07^0.5 - 1) (%)", "{:.4f} -> {:.2f}".format(rs, rs))
show("(d) check: (1 + 0.068816/2)^2", "{:.6f}".format((1 + rs / 200) ** 2))
show("(d) ICONV: EFF = 7, C/Y = 2 -> NOM", ba(nom(7, 2)))
show("order: coupon < current yield < YTM < simple yield", "6.00 < {:.2f} < 7.00 < {:.2f}".format(6 / 96.6128 * 100, sy))

# ------------------------------------------------------------------ walkthrough
print("\n=== Calculator walkthrough: Argentina 7.125% due 2117 at 90, semiannual ===")
N, PV, PMT, FV = 200, -90, 7.125 / 2, 100
iy = tvm_iy(N, PV, PMT, FV)
show("CPT I/Y (per half-year)", "{!r} -> BA I/Y = {} / TI I%={}".format(iy, ba(iy), ti(iy)))
show("check: price at that rate, term by term", "{:.6f}".format(bond_pv(PMT, FV, iy / 100, N)))
ytm = 2 * iy
show("x 2 = YTM, semiannual-bond basis", "{!r} -> {} -> {:.3f}%".format(ytm, ba(ytm), ytm))
show("x 2 using the displayed 3.9585", "{}".format(ba(3.9585 * 2)))
e_full = eff(ytm, 2)
e_typed = eff(7.917, 2)
show("ICONV / Eff: NOM = full YTM, C/Y = 2", "{!r} -> BA EFF = {} / TI {}".format(e_full, ba(e_full), ti(e_full)))
show("ICONV with NOM typed as 7.917", "{!r} -> {}".format(e_typed, ba(e_typed)))
show("  same as (1 + half-year rate)^2 - 1", "{:.4f}".format(100 * ((1 + iy / 100) ** 2 - 1)))
show("  annual-pay, to two decimals", "{:.2f}%".format(e_full))
cy = 7.125 / 90 * 100
show("current yield 7.125 / 90 x 100", "{!r} -> BA {} / TI {} -> {:.3f}%".format(cy, ba(cy), ti(cy), cy))
show("YTM - current yield (points)", "{:.4f}".format(ytm - cy))
show("story gap: reported 7.917 - coupon 7.125 (points)", "{:.3f} -> {:.2f}".format(7.917 - 7.125, 7.917 - 7.125))
show("  of which coupon-on-a-cheaper-price: current yield - coupon", "{:.4f}".format(cy - 7.125))
show("  of which the climb to par: YTM - current yield", "{:.4f}".format(ytm - cy))
show("perpetuity yield at 90 (Day 8): 7.125 / 90", "{:.4f}".format(cy))
fin = tvm_pv(N, iy, 0, FV)
show("PV of the final 100 alone at the YTM", "{!r} -> {} -> ${:.2f}".format(fin, ba(fin), -fin))
show("1.0395852^200", "{:,.1f}".format((1 + iy / 100) ** 200))
show("PV of the 10-point climb (90 -> 100) as a share", "PV(100) = {:.4f}".format(-fin))
# 10-year twin at the same price and coupon
iy10 = tvm_iy(20, PV, PMT, FV)
show("10-year twin: N = 20, CPT I/Y", "{!r} -> BA I/Y = {} / TI I%={}".format(iy10, ba(iy10), ti(iy10)))
show("10-year twin: x 2", "{!r} -> {} -> {:.2f}%".format(2 * iy10, ba(2 * iy10), 2 * iy10))
show("10-year twin: typing the displayed 4.3193 x 2 (why TI uses tvm_I%)", ba(round(iy10, 4) * 2))
show("10-year twin: current yield (same)", "{:.4f}".format(cy))
show("10-year twin: straight-line simple yield (7.125 + 10/10)/90", "{:.4f}".format((7.125 + 1) / 90 * 100))
show("100-year: simple yield (7.125 + 10/100)/90", "{:.4f}".format((7.125 + 0.1) / 90 * 100))

# ------------------------------------------------------------------ traps
print("\n=== Exam traps ===")
show("semiannual rate not doubled (story)", "{} vs {}".format(ba(iy), ba(ytm)))
show("story: semiannual basis vs annual-pay", "{:.3f} vs {:.2f}".format(ytm, e_full))
# call example: 5-year 8% annual-pay bond at 104, callable in 2 years at 102
ytc_t = tvm_iy(2, -104, 8, 102)
ytc_par = tvm_iy(2, -104, 8, 100)
ytm_t = tvm_iy(5, -104, 8, 100)
show("trap call: N=2 PV=-104 PMT=8 FV=102 (YTC)", "{!r} -> {} -> {:.2f}%".format(ytc_t, ba(ytc_t), ytc_t))
show("trap call: FV = 100 by mistake", "{!r} -> {} -> {:.2f}%".format(ytc_par, ba(ytc_par), ytc_par))
show("trap call: YTM N=5 PV=-104 PMT=8 FV=100", "{!r} -> {} -> {:.2f}%".format(ytm_t, ba(ytm_t), ytm_t))
show("trap call: yield-to-worst", "{:.2f}%".format(min(ytc_t, ytm_t)))

# ------------------------------------------------------------------ check
print("\n=== Check questions ===")
# c1: 20-year 5% semiannual bond at 88
c1_iy = tvm_iy(40, -88, 2.5, 100)
c1 = 2 * c1_iy
show("c1  N=40 PV=-88 PMT=2.5 FV=100: CPT I/Y", "{!r} -> BA I/Y = {} / TI I%={}".format(c1_iy, ba(c1_iy), ti(c1_iy)))
show("c1  x 2 = YTM (answer)", "{!r} -> {} -> {:.2f}%".format(c1, ba(c1), c1))
show("c1  x 2 from displayed 3.0209", ba(3.0209 * 2))
show("c1  not doubled (wrong)", "{:.2f}".format(c1_iy))
show("c1  current yield 5 / 88", "{:.4f} -> {:.2f}%".format(5 / 88 * 100, 5 / 88 * 100))
show("c1  annual-pay equivalent", "{:.4f}".format(eff(c1, 2)))
show("c1  annual inputs by mistake (N=20, PMT=5)", "{:.4f}".format(tvm_iy(20, -88, 5, 100)))

# c2: explanation numbers come from c1 (5% < 5.68% < 6.04%)
show("c2  coupon < current yield < YTM (c1 bond)", "5.00 < {:.2f} < {:.2f}".format(5 / 88 * 100, c1))

# c3: 10-year 6% semiannual at 108, callable in 3 years at 102
c3_ytc_iy = tvm_iy(6, -108, 3, 102)
c3_ytm_iy = tvm_iy(20, -108, 3, 100)
c3_par_iy = tvm_iy(6, -108, 3, 100)
show("c3  YTC: N=6 PV=-108 PMT=3 FV=102: CPT I/Y", "{!r} -> {} / TI {}".format(c3_ytc_iy, ba(c3_ytc_iy), ti(c3_ytc_iy)))
show("c3  YTC x 2 (answer = yield-to-worst)", "{!r} -> {} -> {:.2f}%".format(2 * c3_ytc_iy, ba(2 * c3_ytc_iy), 2 * c3_ytc_iy))
show("c3  YTC x 2 from displayed", ba(round(c3_ytc_iy, 4) * 2))
show("c3  YTM: N=20 PV=-108 PMT=3 FV=100: CPT I/Y", "{!r} -> {} / TI {}".format(c3_ytm_iy, ba(c3_ytm_iy), ti(c3_ytm_iy)))
show("c3  YTM x 2", "{!r} -> {} -> {:.2f}%".format(2 * c3_ytm_iy, ba(2 * c3_ytm_iy), 2 * c3_ytm_iy))
show("c3  trap: YTC with FV = 100", "{} -> x 2 = {} -> {:.2f}%".format(ba(c3_par_iy), ba(2 * c3_par_iy), 2 * c3_par_iy))
show("c3  current yield 6 / 108", "{:.4f}".format(6 / 108 * 100))
later = [2 * tvm_iy(n, -108, 3, 102) for n in range(7, 20)]
show("c3  if also callable later at 102 (N = 7..19): lowest YTC x 2", "{:.4f} (> 3.7895, so the first call stays the worst)".format(min(later)))

# c4: 6% on a semiannual-bond basis -> annual-pay
c4b = eff(6, 2)
c4c = nom(6, 2)
show("c4  B: 1.03^2 - 1 (right)", "{:.4f} -> {:.2f}%".format(c4b, c4b))
show("c4  A: no conversion", "6.00%")
show("c4  C: 2 x (1.06^0.5 - 1), the wrong way", "{:.4f} -> {:.2f}%".format(c4c, c4c))
show("c4  1.03^2", "{:.4f}".format(1.03 ** 2))
show("c4  ICONV NOM = 6, C/Y = 2 -> EFF", ba(c4b))

# ------------------------------------------------------------------ rounded forms quoted in the prose
print("\n=== Rounded forms used in the text ===")
show("w1 annual-input trap, 2 dp", "{:.2f}".format(-tvm_pv(5, 6, 8, 100)))
show("w2 payment, 2 dp; interest as a BA/TI display", "{:.2f}; {}".format(-w2pmt, ba(w2int)))
show("w2 PMT/12 check: 6% / 12 (% a month)", "{:.1f}".format(6 / 12))
show("worked price, 2 dp (96.61)", "{:.2f}".format(P))
show("1.07^0.5 - 1", "{:.6f}".format(1.07 ** 0.5 - 1))
show("8% semiannual -> annual-pay, 2 dp", "{:.2f}".format(eff(8, 2)))
show("1 + half-year rate (story), 6 dp", "{:.6f}".format(1 + iy / 100))
show("half-year rate quoted by mistake, 2 dp", "{:.2f}".format(iy))
show("c1 payment-free checks: half-year rate 2 dp", "{:.2f}".format(c1_iy))
