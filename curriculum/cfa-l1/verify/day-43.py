#!/usr/bin/env python3
"""Day 43 of CFA Math: every number that appears on day-43.html.

Standard error and confidence intervals for a mean (CFA Program 2027 Level I,
Quantitative Methods, LM7 Estimation and Hypothesis Testing). Plain Python 3,
standard library only. Run it and copy the printed numbers.

The t-distribution (the critical values the TI-84's invT( shows, and the true
coverage of a band built with the wrong reliability factor) is computed from the
regularized incomplete beta function; z-values come from statistics.NormalDist.
"""

import math
from statistics import NormalDist

Z = NormalDist()


# ------------------------------------------------------------ helpers
def d4(x):
    """A 4-decimal calculator display (BA II Plus shows thousands separators)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """A TI-84 display in FIX 4: no leading zero before the decimal point."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def show(label, value):
    print("  {:<70} {}".format(label, value))


def head(title):
    print()
    print(title)
    print("-" * len(title))


# --- t-distribution via the regularized incomplete beta function -----------
def _betacf(a, b, x):
    """Continued fraction for the incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return h


def betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log(1.0 - x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    p2 = betai(df / 2.0, 0.5, df / (df + t * t))   # P(|T| > |t|)
    return 1.0 - 0.5 * p2 if t >= 0 else 0.5 * p2


def inv_t(p, df):
    """invT(p, df): the t with P(T <= t) = p (bisection)."""
    lo, hi = -100.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# Self-check against the closed form for 3 df.
def t3_cdf(t):
    u = t / math.sqrt(3)
    return 0.5 + (u / (1 + u * u) + math.atan(u)) / math.pi


assert abs(t_cdf(1.96, 3) - t3_cdf(1.96)) < 1e-12
assert abs(t_cdf(-0.7, 3) - t3_cdf(-0.7)) < 1e-12

# The t-table values the page quotes (3 decimals, as a printed table shows them).
T_TABLE = {
    (0.025, 3): 3.182, (0.025, 9): 2.262, (0.025, 15): 2.131,
    (0.025, 24): 2.064, (0.025, 29): 2.045, (0.05, 8): 1.860,
}
for (tail, df), v in T_TABLE.items():
    assert round(inv_t(1 - tail, df), 3) == v, (tail, df, inv_t(1 - tail, df))

print("Day 43 - Standard error and confidence intervals: every number on the page")

# ------------------------------------------------------------ warm-up
head("w1 (From Day 40): money duration")
mv, moddur, bp = 8_000_000, 6.2, 15
dy = bp / 10000
money_dur = moddur * mv
loss = money_dur * dy
pvbp = money_dur * 0.0001
show("money duration = 6.2 x 8,000,000", "{:,.4f}".format(money_dur))
show("estimated change = -money duration x 0.0015", "{:,.4f}".format(-loss))
show("w1 data-answer (signless loss, to the dollar)", loss)
show("BA display 6.2 x 8000000 x .0015 =", d4(loss))
show("TI display", "{:.4f}".format(loss))
show("PVBP of the position = money duration x 0.0001", "{:,.4f}".format(pvbp))
show("15 bp x PVBP", "{:,.4f}".format(15 * pvbp))
show("trap: dy typed as 0.15 (percent)", "{:,.4f}".format(money_dur * 0.15))

head("w2 (From Day 36): bootstrap the 2-year spot rate")
z1, par2 = 0.03, 4.0
df1 = 1 / (1 + z1)
cpn_pv = par2 * df1
df2 = (100 - cpn_pv) / (100 + par2)
z2 = df2 ** -0.5 - 1
z2_noroot = 1 / df2 - 1
show("DF_1 = 1/1.03", "{:.6f}".format(df1))
show("4 x DF_1 = 4/1.03", "{:.6f}".format(cpn_pv))
show("100 - 4/1.03", "{:.6f}".format(100 - cpn_pv))
show("DF_2 = (100 - 4/1.03)/104", "{:.6f}".format(df2))
show("BA display of DF_2", d4(df2))
show("Z_2 = DF_2^(-1/2) - 1 (decimal)", "{:.6f}".format(z2))
show("Z_2 in % (x 100), 4 dp display", d4(100 * z2))
show("distractor: forgot the square root, 1/DF_2 - 1 (%)", d4(100 * z2_noroot))
show("distractor: par yield taken as the spot rate (%)", "4.00")
show("check: 4/1.03 + 104/(1+Z_2)^2", "{:.6f}".format(4 / 1.03 + 104 / (1 + z2) ** 2))

# ------------------------------------------------------------ story / payoff
head("Story: t vs z for 95% two-sided")
z975 = Z.inv_cdf(0.975)
z95 = Z.inv_cdf(0.95)
z995 = Z.inv_cdf(0.995)
show("z 0.975 (95% two-sided)", "{:.6f}".format(z975))
show("z 0.95 (90% two-sided)", "{:.6f}".format(z95))
show("z 0.995 (99% two-sided)", "{:.6f}".format(z995))
for n in (4, 10, 16, 30):
    t = inv_t(0.975, n - 1)
    show("n = {:>2}: df = {:>2}, t_0.025 = ".format(n, n - 1), "{:.4f}  (table {:.3f})".format(t, t))
r3 = 3.182 / 1.96
show("3.182 / 1.96 (how much wider with n = 4)", "{:.4f} -> {:.0f}% wider".format(r3, 100 * (r3 - 1)))
cov3 = 2 * t_cdf(1.96, 3) - 1
show("true coverage of +-1.96 s/sqrt(n) with n = 4 (3 df)", "{:.4f} -> about {:.0f}%".format(cov3, 100 * cov3))
cov15 = 2 * t_cdf(1.96, 15) - 1
show("true coverage of +-1.96 s/sqrt(n) with n = 16 (15 df)", "{:.4f}".format(cov15))
r15 = 2.131 / 1.96
show("2.131 / 1.96 (walkthrough: how much wider)", "{:.4f} -> {:.1f}% wider".format(r15, 100 * (r15 - 1)))

head("The idea: bathroom scale picture (typical error 1 kg per reading)")
for n in (1, 4, 16):
    show("n = {:>2}: 1/sqrt(n)".format(n), "{:.2f} kg".format(1 / math.sqrt(n)))

# ------------------------------------------------------------ worked example
head("Worked example: n = 9, mean 2.0%, s = 4.5%, 90% interval")
n, xbar, s = 9, 2.0, 4.5
se = s / math.sqrt(n)
t8 = inv_t(0.95, 8)
half_t = 1.860 * se
half_z = 1.645 * se
show("sqrt(9)", math.sqrt(9))
show("SE = 4.5/3", "{:.4f}".format(se))
show("t_0.05, 8 df (exact)", "{:.4f}  (table 1.860)".format(t8))
show("half-width t: 1.860 x 1.50", "{:.4f}".format(half_t))
show("interval (t)", "{:.4f} to {:.4f}".format(xbar - half_t, xbar + half_t))
show("half-width z: 1.645 x 1.50", "{:.4f}".format(half_z))
show("interval (z, wrong)", "{:.4f} to {:.4f}".format(xbar - half_z, xbar + half_z))
show("z width / t width", "{:.4f} -> {:.1f}% narrower".format(1.645 / 1.860, 100 * (1 - 1.645 / 1.860)))
cov_z8 = 2 * t_cdf(1.645, 8) - 1
show("true coverage of the 'z 90%' band with 8 df", "{:.4f}".format(cov_z8))

# ------------------------------------------------------------ walkthrough
head("Walkthrough: n = 16, mean 1.2%, s = 4%, 95% interval, 15 df")
n, xbar, s = 16, 1.2, 4.0
se = s / math.sqrt(n)
t15 = inv_t(0.975, 15)
show("step 2: SE = 4 / sqrt(16)", "BA {}  TI {}".format(d4(se), ti4(se)))
show("step 3: table t (15 df, 0.025 per tail)", "2.131")
show("step 3: TI invT(.975,15)", "{}  (full {:.10f})".format(ti4(t15), t15))
show("step 4: half-width BA 2.131 x SE", d4(2.131 * se))
show("step 4: half-width TI T x S", ti4(t15 * se))
show("step 5: BA lower 1.2 - 2.131", d4(xbar - 2.131 * se))
show("step 5: BA upper 1.2 + 2.131", d4(xbar + 2.131 * se))
show("step 5: TI lower", ti4(xbar - t15 * se))
show("step 5: TI upper", ti4(xbar + t15 * se))
show("plan answer (upper, exact t)", "{:.6f}".format(xbar + t15 * se))
show("step 6: TInterval", "({},{})".format(ti4(xbar - t15 * se), ti4(xbar + t15 * se)))
show("with z 1.96 instead (for comparison)", "{:.4f} to {:.4f}".format(xbar - 1.96 * se, xbar + 1.96 * se))
show("width with t (table)", "{:.4f}".format(2 * 2.131 * se))
show("trap: SE = 4/16 (divided by n)", "{:.4f}".format(s / n))
show("trap: half-width 2.131 x 0.25", "{:.4f}".format(2.131 * s / n))

# ------------------------------------------------------------ check questions
head("c1: n = 25, mean 0.8%, s = 3.0%, 95% interval (24 df)")
n, xbar, s = 25, 0.8, 3.0
se = s / math.sqrt(n)
t24 = inv_t(0.975, 24)
show("SE = 3 / sqrt(25)", "BA {}  TI {}".format(d4(se), ti4(se)))
show("table t 24 df", "2.064")
show("TI invT(.975,24)", "{}  (full {:.10f})".format(ti4(t24), t24))
show("BA half-width 2.064 x 0.6", d4(2.064 * se))
show("BA upper", d4(xbar + 2.064 * se))
show("BA lower", d4(xbar - 2.064 * se))
show("TI half-width", ti4(t24 * se))
show("TI upper", ti4(xbar + t24 * se))
show("TI lower", ti4(xbar - t24 * se))
show("c1 data-answer (upper, exact t)", xbar + t24 * se)
show("c1 |table - exact| upper", "{:.6f}".format(abs(2.064 * se - t24 * se)))
show("c1 with z 1.96 (wrong)", "{:.4f}".format(xbar + 1.96 * se))
show("c1 with SE = s/n (wrong)", "{:.4f}".format(xbar + 2.064 * s / n))

head("c2: quadruple n, same s, large sample (z)")
show("SE ratio 1/sqrt(4)", 1 / math.sqrt(4))
show("wrong: 1/4", 1 / 4)

head("c3: sigma = 4% known, 95% half-width <= 0.5%: smallest n")
need = (1.96 * 4 / 0.5) ** 2
show("1.96 x 4 / 0.5", "{:.4f}".format(1.96 * 4 / 0.5))
show("n >= (1.96 x 4 / 0.5)^2", "{:.4f}".format(need))
n_ans = math.ceil(need)
show("c3 data-answer (round up)", n_ans)
show("half-width at n = 245", "{:.5f}".format(1.96 * 4 / math.sqrt(245)))
show("half-width at n = 246", "{:.5f}".format(1.96 * 4 / math.sqrt(246)))
show("wrong: forgot to square (n = 15.68 -> 16)", math.ceil(1.96 * 4 / 0.5))

head("c4: n = 64, mean 1.5%, sigma = 4% known, lower limit of 95% interval")
n, xbar, sig = 64, 1.5, 4.0
se = sig / math.sqrt(n)
show("SE = 4/8", "{:.4f}".format(se))
show("A (right): 1.5 - 1.96 x 0.5", "{:.4f}".format(xbar - 1.96 * se))
show("upper", "{:.4f}".format(xbar + 1.96 * se))
show("B: sigma/n = 4/64", "{:.4f}".format(sig / n))
show("B: 1.5 - 1.96 x 0.0625", "{:.4f}".format(xbar - 1.96 * sig / n))
show("C: 1.5 - 1.645 x 0.5", "{:.4f}".format(xbar - 1.645 * se))
show("aside: t_0.025 with 63 df (if sigma were unknown)", "{:.4f}".format(inv_t(0.975, 63)))
