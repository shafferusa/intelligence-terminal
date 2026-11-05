#!/usr/bin/env python3
"""Day 18 of CFA Math: every number that appears on day-18.html.

Money-market yields and repos (CFA Program 2027 Level I, Fixed Income:
Yield and Yield Spread Measures for Floating-Rate Instruments; repos from
Fixed-Income Markets for Corporate Issuers).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime


def money(x):
    return "{:,.2f}".format(x)


def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def price_discount(fv, days, dr, year=360):
    """Discount basis: PV = FV x [1 - (Days/Year) x DR], DR as a decimal."""
    return fv * (1 - days / year * dr)


def price_addon(fv, days, aor, year=360):
    """Add-on basis: PV = FV / [1 + (Days/Year) x AOR], AOR as a decimal."""
    return fv / (1 + days / year * aor)


def addon_rate(pv, fv, days, year):
    """AOR = (Year/Days) x (FV - PV)/PV, as a decimal."""
    return year / days * (fv - pv) / pv


def discount_rate(pv, fv, days, year=360):
    """DR = (Year/Days) x (FV - PV)/FV, as a decimal."""
    return year / days * (fv - pv) / fv


def bey(pv, fv, days):
    return addon_rate(pv, fv, days, 365)


def show(label, value):
    print("  {:<62} {}".format(label, value))


print("=== Warm-up w1 (from Day 17): flat price from full price ===")
ai_w1 = 60 / 182 * (4.5 / 2)
flat_w1 = 102.3 - ai_w1
show("coupon per half-year (4.5% / 2)", 4.5 / 2)
show("t/T = 60/182", "{:.6f}".format(60 / 182))
show("AI = 60/182 x 2.25", "{!r} -> {:.4f}".format(ai_w1, ai_w1))
show("flat = 102.3000 - AI (answer)", "{!r} -> {:.4f} -> {:.2f}".format(flat_w1, flat_w1, flat_w1))
show("wrong: whole coupon 4.5 -> AI, flat", "{:.4f}, {:.4f}".format(60 / 182 * 4.5, 102.3 - 60 / 182 * 4.5))
show("wrong: full + AI", "{:.4f}".format(102.3 + ai_w1))
show("tolerance check |101.56 - answer|", "{:.4f}".format(abs(101.56 - flat_w1)))
show("AI rounded to 0.74 -> flat", "{:.4f}".format(102.3 - 0.74))

print("\n=== Warm-up w2 (from Day 11): annualize a 1.5% quarter ===")
show("B (correct) 1.015^4 - 1", "{:.4f}% -> {:.2f}%".format(100 * (1.015 ** 4 - 1), 100 * (1.015 ** 4 - 1)))
show("A (x 4)", "{:.4f}% -> {:.2f}%".format(4 * 1.5, 4 * 1.5))
show("C (fourth root) 1.015^(1/4) - 1", "{:.4f}% -> {:.2f}%".format(100 * (1.015 ** 0.25 - 1), 100 * (1.015 ** 0.25 - 1)))

print("\n=== Inputs typed as decimals on the page ===")
show("4.80% repo rate as a decimal; 5.20% as a decimal", "{}; {}".format(4.80 / 100, 5.20 / 100))
show("5.25% and 4.20% discount rates as decimals", "{}; {}".format(5.25 / 100, 4.20 / 100))

print("\n=== Story: 9 Dec 2008 ===")
show("days from Lehman filing (15 Sep 2008) to 9 Dec 2008",
     (datetime.date(2008, 12, 9) - datetime.date(2008, 9, 15)).days)
show("4-week bills began 2001 -> 2008 (years)", 2008 - 2001)
show("bill auctions since 1929 -> 2008 (years)", 2008 - 1929)
show("four weeks in days", 4 * 7)
p0 = price_discount(100, 28, 0.0)
show("DR 0.000%: price per 100", "{:.4f}".format(p0))
show("DR 0.000%: interest on $30 billion", money(30e9 - price_discount(30e9, 28, 0.0)))
p_wk = price_discount(100000, 28, 0.0004)
# The page runs the week-earlier 0.04% through the formula for 28 days as a
# computation (that bill's exact term and whether 0.04% was its discount or
# investment rate are not in the sources; if it was issued Thursday 4 Dec 2008,
# 28 days later is 1 Jan 2009, a holiday, so it may have run 29 days).
show("4 Dec 2008 + 28 days", datetime.date(2008, 12, 4) + datetime.timedelta(28))
show("DR 0.04%: price of a $100,000 bill (28 days)", "{!r} -> {}".format(p_wk, money(p_wk)))
show("DR 0.04%: interest per $100,000", money(100000 - p_wk))
show("DR 0.04%: interest per $100,000 (unrounded)", repr(100000 - p_wk))
show("DR 0.04%: price per 100", "{:.6f}".format(price_discount(100, 28, 0.0004)))
b_wk = bey(p_wk, 100000, 28)
show("DR 0.04%: BEY", "{!r} -> {:.4f}%".format(100 * b_wk, 100 * b_wk))
int30 = 30e9 * 28 / 360 * 0.0004
show("DR 0.04%: discount on $30 billion", "{} (about ${:,.0f})".format(money(int30), int30))
show("3-month bill at 0.005%: price per 100 for 91 days", "{:.6f}".format(price_discount(100, 91, 0.00005)))

print("\n=== The idea: the jacket ===")
show("20% off $100 -> pay", 100 * (1 - 0.20))
show("$20 saved as % of $80 paid", "{:.2f}%".format(100 * 20 / 80))

print("\n=== Why it works: $100 for 90 days at 4% ===")
show("period rate 90/360 x 4%", "{:.4f}%".format(100 * 90 / 360 * 0.04))
show("add-on: deposit 100 -> back", "{:.4f}".format(100 * (1 + 90 / 360 * 0.04)))
show("add-on: PV of 101 at 4% add-on", "{:.4f}".format(price_addon(101, 90, 0.04)))
p99 = price_discount(100, 90, 0.04)
show("discount: discount on 100 face", "{:.4f}".format(100 * 90 / 360 * 0.04))
show("discount: price", "{:.4f}".format(p99))
show("DR back from price (360)", "{:.4f}%".format(100 * discount_rate(p99, 100, 90)))
show("AOR from price (360)", "{:.4f}%".format(100 * addon_rate(p99, 100, 90, 360)))
show("BEY from price (365)", "{:.4f}%".format(100 * bey(p99, 100, 90)))
show("1/99", "{:.6f}".format(1 / 99))
show("1/100", "{:.6f}".format(1 / 100))
show("Year/Days: 360/90, 365/90", "{:.4f}, {:.4f}".format(360 / 90, 365 / 90))

print("\n=== Worked example: 90-day bill at 4.90% DR vs 90-day CD at 4.95% add-on (360) ===")
disc_we = 100 * 90 / 360 * 0.049
show("discount per 100 = 100 x 90/360 x 0.049", "{:.4f}".format(disc_we))
show("(90/360) x 0.049", "{:.5f}".format(90 / 360 * 0.049))
p_we = price_discount(100, 90, 0.049)
show("bill price", "{!r} -> {:.4f}".format(p_we, p_we))
hpr_we = (100 - p_we) / p_we
show("90-day return on money paid 1.2250/98.7750", "{:.7f} ({:.4f}%)".format(hpr_we, 100 * hpr_we))
show("365/90", "{:.4f}".format(365 / 90))
show("(365/90) x 0.0124019 (page's rounded step)", "{:.4f}%".format(100 * 365 / 90 * 0.0124019))
show("4.0556 x 0.012402 (too-rounded: drifts to)", "{:.4f}%".format(100 * 4.0556 * 0.012402))
b_we = bey(p_we, 100, 90)
show("bill BEY", "{!r} -> {:.4f}% -> {:.2f}%".format(100 * b_we, 100 * b_we, 100 * b_we))
show("bill 360-day add-on", "{:.4f}%".format(100 * addon_rate(p_we, 100, 90, 360)))
cd_period = 90 / 360 * 0.0495
show("CD 90-day return 90/360 x 4.95%", "{:.4f}%".format(100 * cd_period))
cd_bey = 0.0495 * 365 / 360
show("CD BEY = 4.95% x 365/360", "{!r} -> {:.5f}% -> {:.4f}% -> {:.2f}%".format(100 * cd_bey, 100 * cd_bey, 100 * cd_bey, 100 * cd_bey))
show("CD BEY via period return x 365/90", "{:.5f}%".format(100 * cd_period * 365 / 90))
show("bill BEY - CD BEY (points)", "{:.4f}".format(100 * (b_we - cd_bey)))
show("bill BEY - CD BEY (bp)", "{:.2f}".format(10000 * (b_we - cd_bey)))
show("on $1,000,000 for 90 days: bill gain per $ invested vs CD", "{:.2f} vs {:.2f}".format(1e6 * hpr_we, 1e6 * cd_period))

print("\n=== Walkthrough: 28-day bill, DR 0 then 5.25%; 7-day repo ===")
show("case 1: 100 x (1 - 28/360 x 0)", "{:.4f}".format(p0))
show("28/360", "{:.6f}".format(28 / 360))
show("28/360 x 0.0525 (discount per $1 of face)", "{:.6f}".format(28 / 360 * 0.0525))
p_wt = price_discount(100, 28, 0.0525)
show("case 2: price per 100", "{!r} -> {}".format(p_wt, d4(p_wt)))
show("discount per 100 = 100 - price", "{:.6f} -> {:.4f}".format(100 - p_wt, 100 - p_wt))
show("28-day return on money paid", "{:.6f} ({:.4f}%)".format((100 - p_wt) / p_wt, 100 * (100 - p_wt) / p_wt))
a360 = 100 * addon_rate(p_wt, 100, 28, 360)
show("360-day add-on rate (display)", "{!r} -> {}".format(a360, d4(a360)))
b_wt = 100 * bey(p_wt, 100, 28)
show("BEY (answer)", "{!r} -> {}".format(b_wt, d4(b_wt)))
show("BEY as a360 x 365/360", "{!r} -> {}".format(a360 * 365 / 360, d4(a360 * 365 / 360)))
show("gap from face vs price (a360 - 5.25)", "{:.4f}".format(a360 - 5.25))
show("gap from 360 vs 365 (BEY - a360)", "{:.4f}".format(b_wt - a360))
show("BEY - quoted DR", "{:.4f}".format(b_wt - 5.25))
show("365/28", "{:.6f}".format(365 / 28))
repo = 20000000 * 0.052 * 7 / 360
show("repo interest 20,000,000 x 0.052 x 7/360", "{!r} -> {} (BA {} / TI {:.4f})".format(repo, money(repo), d4(repo), repo))
show("repurchase price", money(20000000 + repo))
show("digits in 20020222.22", len("2002022222"))
show("trap: 28-day bill at 5.25% on a 365-day year (wrong price)", "{:.4f}".format(price_discount(100, 28, 0.0525, 365)))
show("effective annual (compounded) for comparison", "{:.4f}%".format(100 * ((100 / p_wt) ** (365 / 28) - 1)))

print("\n=== Check c1: 91-day bill at 4.20% DR ===")
p_c1 = price_discount(100, 91, 0.042)
show("91/360 x 0.042", "{:.6f}".format(91 / 360 * 0.042))
show("price", "{!r} -> {}".format(p_c1, d4(p_c1)))
show("100 - price", "{:.4f}".format(100 - p_c1))
c1 = 100 * bey(p_c1, 100, 91)
show("BEY (answer)", "{!r} -> {} -> {:.2f}%".format(c1, d4(c1), c1))
show("365/91", "{:.6f}".format(365 / 91))
show("tolerance: price rounded to 98.94 -> BEY", "{:.4f}".format(100 * bey(98.94, 100, 91)))
show("tolerance: |4.30 - c1|", "{:.4f}".format(abs(4.30 - c1)))
show("wrong: 360-day add-on", "{:.4f}".format(100 * addon_rate(p_c1, 100, 91, 360)))
show("wrong: price on a 365-day year -> BEY", "{:.4f}".format(100 * bey(price_discount(100, 91, 0.042, 365), 100, 91)))

print("\n=== Check c2: the 5.25% bill split into its two gaps (explanation) ===")
show("5.25 -> 360 add-on -> BEY", "5.25 -> {:.4f} -> {:.4f}".format(a360, b_wt))

print("\n=== Check c3: repo with a 3% haircut ===")
loan = 10000000 * (1 - 0.03)
show("loan = 10,000,000 x 0.97", money(loan))
c3 = loan * 0.048 * 14 / 360
show("interest (answer)", "{!r} -> {} (BA {} / TI {:.4f})".format(c3, money(c3), d4(c3), c3))
show("repurchase price", money(loan + c3))
show("wrong: no haircut", money(10000000 * 0.048 * 14 / 360))
show("wrong: 365-day year", money(loan * 0.048 * 14 / 365))
show("haircut in dollars", money(10000000 - loan))

print("\n=== Check c4: 90-day bill at 98.80 ===")
gain = 100 - 98.80
show("gain per 100", "{:.2f}".format(gain))
c4a = 100 * discount_rate(98.80, 100, 90)
c4b = 100 * addon_rate(98.80, 100, 90, 360)
c4c = 100 * bey(98.80, 100, 90)
show("A discount rate (on face, 360)", "{!r} -> {:.4f} -> {:.2f}%".format(c4a, c4a, c4a))
show("B add-on, 360", "{!r} -> {:.4f} -> {:.2f}%".format(c4b, c4b, c4b))
show("C BEY (correct)", "{!r} -> {:.4f} -> {:.2f}%".format(c4c, c4c, c4c))
show("1.20/98.80", "{:.6f}".format(gain / 98.80))

print("\n=== Formula plate check: discount and add-on prices agree when rates match ===")
dr_x = discount_rate(p_wt, 100, 28)
show("DR recovered from 99.5917 price", "{:.6f}%".format(100 * dr_x))
show("price from 360 add-on rate", "{:.6f}".format(price_addon(100, 28, a360 / 100)))
show("price from BEY on 365", "{:.6f}".format(price_addon(100, 28, b_wt / 100, 365)))
