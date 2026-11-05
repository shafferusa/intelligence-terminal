#!/usr/bin/env python3
"""Day 30 of CFA Math: every number that appears on day-30.html.

The normal curve and fat tails: standardizing z = (X - mu)/sigma, the
68/90/95/99% confidence bands, left-tail probabilities and cutoffs
(normalcdf / invNorm on the TI-84, a z-table or memorized critical values on
the BA II Plus), skewness and the mean-median-mode order, excess kurtosis,
and value at risk as a left-tail percentile (CFA Program 2027 Level I,
Quantitative Methods, LM6 Statistical Distributions for Financial Asset Prices
and Returns; skewness and kurtosis from LM5 Statistical Characteristics of
Asset Returns).
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from decimal import Decimal, ROUND_HALF_UP
from statistics import NormalDist

Z = NormalDist()            # the standard normal: mean 0, SD 1


def show(label, value):
    print("  {:<66} {}".format(label, value))


def d4(x):
    """A 4-decimal BA II Plus display (thousands separators, leading zero)."""
    return "{:,.4f}".format(x)


def ti4(x):
    """TI-84 display in fixed 4 decimals: no leading zero below 1."""
    s = "{:.4f}".format(x)
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def r2(x):
    """Round half up to 2 decimals, as a person would write it."""
    return str(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct(x, dp=2):
    return "{:.{}f}%".format(100 * x, dp)


def phi(z):
    """Standard normal CDF P(Z <= z); erfc keeps far-tail values exact."""
    return 0.5 * math.erfc(-z / math.sqrt(2))


def table(z):
    """A z-table entry: P(Z <= z) to four decimals."""
    return "{:.4f}".format(phi(z))


def normalcdf(lo, hi, mu=0.0, sd=1.0):
    return phi((hi - mu) / sd) - phi((lo - mu) / sd)


def comb(n, r):
    return math.comb(n, r)


# ---------------------------------------------------------------------------
print("=== Warm-up ===")
# w1 (Day 29): binomial, exactly 4 beats in 6 years with p = 0.6
n, p, x = 6, 0.6, 4
c = comb(n, x)
w1 = c * p ** x * (1 - p) ** (n - x)
show("w1 6C4", "{} -> BA {} / TI {}".format(c, d4(c), ti4(c)))
show("w1 0.6^4 ; 0.4^2", "{:.4f} ; {:.4f}".format(p ** x, (1 - p) ** (n - x)))
show("w1 one order 0.6^4 x 0.4^2", "{!r} -> {:.6f}".format(p ** x * (1 - p) ** (n - x), p ** x * (1 - p) ** (n - x)))
show("w1 15 x 0.020736 (answer)", "{!r} -> BA {} / TI {} -> {}".format(w1, d4(w1), ti4(w1), pct(w1)))
show("w1   x 100", "BA {} / TI {}".format(d4(100 * w1), ti4(100 * w1)))
show("w1 trap: 6C4 left out", pct(p ** x * (1 - p) ** (n - x)))
show("w1 E(X) = np (for the solution)", "{:.1f}".format(n * p))

# w2 (Day 23): 10th percentile of eleven daily returns, L = (n + 1)y/100
W2 = [0.6, -2.1, 1.3, -0.4, 0.9, -3.6, 0.2, 1.8, -0.9, 0.5, -1.2]
srt = sorted(W2)
show("w2 n", len(W2))
show("w2 sorted", srt)


def pctile(data, loc):
    """Value at a 1-based (possibly fractional) location, interpolated."""
    k = int(math.floor(loc))
    frac = loc - k
    lo = data[k - 1]
    hi = data[k] if k < len(data) else data[k - 1]
    return lo + frac * (hi - lo)


L_right = (len(W2) + 1) * 10 / 100
L_wrong = len(W2) * 10 / 100
w2_right = pctile(srt, L_right)
w2_wrong_n = pctile(srt, L_wrong)
w2_unsorted = pctile(W2, L_right)
show("w2 L_10 = 12 x 0.10", "{:.2f}".format(L_right))
show("w2 1st and 2nd sorted ; gap", "{} ; {} ; {:.2f}".format(srt[0], srt[1], srt[1] - srt[0]))
show("w2 right: -3.6 + 0.2 x 1.5", "{!r} -> {:.2f}%".format(w2_right, w2_right))
show("w2 trap n x y/100 = 1.1: -3.6 + 0.1 x 1.5", "{!r} -> {:.2f}%".format(w2_wrong_n, w2_wrong_n))
show("w2 trap unsorted at 1.2: 0.6 + 0.2 x (-2.1 - 0.6)", "{!r} -> {:.2f}%".format(w2_unsorted, w2_unsorted))
show("w2   0.2 x (-2.7)", "{:.2f}".format(0.2 * (W2[1] - W2[0])))
show("w2 median (6th sorted) for reference", srt[5])
show("w2 mean (for reference)", "{:.4f}".format(sum(W2) / len(W2)))

# ---------------------------------------------------------------------------
print("\n=== Story: Black Monday, 19 October 1987 ===")
for label, dt in [("Friday", datetime.date(1987, 10, 16)), ("Monday", datetime.date(1987, 10, 19))]:
    show("{} {}".format(label, dt.isoformat()), dt.strftime("%A"))
FRI_CLOSE, FRI_DROP = 2246.74, 108.35
MON_CLOSE = 1738.74
thu_close = FRI_CLOSE + FRI_DROP
show("Thursday close implied = 2,246.74 + 108.35", "{:,.2f}".format(thu_close))
show("Friday % fall = 108.35 / 2,355.09", "{:.4f}% -> {:.1f}%".format(100 * FRI_DROP / thu_close, 100 * FRI_DROP / thu_close))
pts = FRI_CLOSE - MON_CLOSE
mon_pct = 100 * pts / FRI_CLOSE
show("Monday points = 2,246.74 - 1,738.74", "{:.2f}".format(pts))
show("Monday % fall = 508 / 2,246.74", "{:.4f}% -> {:.1f}%".format(mon_pct, mon_pct))
two_day = 100 * (thu_close - MON_CLOSE) / thu_close
show("Friday + Monday together: 2,355.09 -> 1,738.74", "{:.2f} points, {:.2f}% -> about {:.0f}%".format(thu_close - MON_CLOSE, two_day, two_day))
show("NYSE volume about 604 million, about 3x normal -> normal day", "about {:.0f} million".format(604 / 3))
MU_D, SD_D = 0.05, 1.0
X_CRASH = -22.6
z_crash = (X_CRASH - MU_D) / SD_D
show("puzzle z = (-22.6 - 0.05)/1", "{!r} -> BA {} / TI {}".format(z_crash, d4(z_crash), ti4(z_crash)))

# ---------------------------------------------------------------------------
print("\n=== The idea ===")
for k in (1, 2, 3):
    pk = phi(-k)
    show("P(Z < -{}) -> 1 in".format(k), "{} -> 1 in {:.1f}".format(pct(pk), 1 / pk))
for k in (1.0, 1.645, 1.96, 2.58, 2.576, 2.33, 2.326):
    inside = phi(k) - phi(-k)
    show("P(-{} < Z < {}) ; one tail".format(k, k), "{} ; {}".format(pct(inside, 2), pct(phi(-k), 2)))
show("one-tail 5% z (exact)", "{:.6f}".format(Z.inv_cdf(0.95)))
show("one-tail 2.5% z (exact)", "{:.6f}".format(Z.inv_cdf(0.975)))
show("one-tail 1% z (exact)", "{:.6f}".format(Z.inv_cdf(0.99)))
show("one-tail 0.5% z (exact)", "{:.6f}".format(Z.inv_cdf(0.995)))

# ---------------------------------------------------------------------------
print("\n=== Why it works ===")
X2 = -2.0
dist = X2 - MU_D
show("distance from the mean: -2 - 0.05", "{:.2f}".format(dist))
show("in daily SDs: -2.05 / 1", "{:.2f}".format(dist / SD_D))
show("same 2.05 points against a monthly SD of 4.5%", "{:.4f} -> {:.2f}".format(dist / 4.5, dist / 4.5))
# two different normals, same z, same probability
zd = (-1.95 - 0.05) / 1.0
zm = (-9.0 - 1.0) / 5.0
show("daily: (-1.95 - 0.05)/1", "{:.2f}".format(zd))
show("monthly: (-9 - 1)/5", "{:.2f}".format(zm))
show("P below each", "{} ; {} (NormalDist daily {} / monthly {})".format(
    pct(phi(zd)), pct(phi(zm)), pct(NormalDist(0.05, 1).cdf(-1.95)), pct(NormalDist(1, 5).cdf(-9))))
show("mean of X - mu ; SD of (X - mu)/sigma", "{} ; {}".format(0.05 - 0.05, 1.0 / 1.0))
show("E[Z^3] (skew of a normal) ; E[Z^4] (kurtosis of a normal)", "0 ; 3  (moments of N(0,1))")
# numerical check of E[Z^4] = 3 by integration
h = 0.001
m4 = sum(((i * h) ** 4) * math.exp(-((i * h) ** 2) / 2) / math.sqrt(2 * math.pi) * h for i in range(-12000, 12001))
m3 = sum(((i * h) ** 3) * math.exp(-((i * h) ** 2) / 2) / math.sqrt(2 * math.pi) * h for i in range(-12000, 12001))
show("  numeric check E[Z^4] ; E[Z^3]", "{:.6f} ; {:.6f}".format(m4, m3))
show("  z^4 for z = 1, 2, 3, 4", [k ** 4 for k in (1, 2, 3, 4)])

# ---------------------------------------------------------------------------
print("\n=== Worked example: mean 8%, SD 16% ===")
mu, sd = 8.0, 16.0
za = (0 - mu) / sd
show("(a) z = (0 - 8)/16", "{:.2f}".format(za))
show("(a) table P(Z <= 0.50)", table(0.50))
show("(a) 1 - 0.6915", "{:.4f}".format(1 - 0.6915))
pa = phi(za)
show("(a) exact P(Z < -0.50) = normalcdf(-1E99,0,8,16)", "{!r} -> TI {} -> {}".format(pa, ti4(pa), pct(pa)))
half = 1.96 * sd
show("(b) 1.96 x 16", "{:.2f}".format(half))
show("(b) 8 - 31.36 ; 8 + 31.36", "{:.2f} ; {:.2f}".format(mu - half, mu + half))
show("(b) check: share inside with 1.96", pct(normalcdf(mu - half, mu + half, mu, sd)))
show("(c) mean 0.04 < median 0.07 -> negative skew", 0.04 < 0.07)
show("(c) excess kurtosis 5.1 - 3", "{:.1f}".format(5.1 - 3))

# ---------------------------------------------------------------------------
print("\n=== Calculator walkthrough: daily mean 0.05%, SD 1% ===")
show("step 2: (-22.6 - .05) / 1", "BA {} / TI {}".format(d4(z_crash), ti4(z_crash)))
z2 = (X2 - MU_D) / SD_D
show("step 3: (-2 - .05) / 1", "BA {} / TI {}".format(d4(z2), ti4(z2)))
show("step 4: table P(Z <= 2.05)", table(2.05))
t205 = 1 - float(table(2.05))
show("step 4 BA: 1 - .9798", "BA {}".format(d4(t205)))
pb = normalcdf(-1e99, X2, MU_D, SD_D)
show("step 4 TI: normalcdf(-1E99,-2,.05,1)", "{!r} -> TI {} -> {}".format(pb, ti4(pb), pct(pb)))
show("  1 in ... days", "{:.1f}".format(1 / pb))
show("  days a year at 252 trading days", "{:.2f}".format(252 * pb))
cut_ba = MU_D - 1.645 * SD_D
show("step 5 BA: .05 - 1.645 x 1", "{!r} -> BA {}".format(cut_ba, d4(cut_ba)))
cut_ti = NormalDist(MU_D, SD_D).inv_cdf(0.05)
show("step 5 TI: invNorm(.05,.05,1)", "{!r} -> TI {}".format(cut_ti, ti4(cut_ti)))
show("  check P(R < cutoff) with exact z", pct(phi((cut_ti - MU_D) / SD_D)))
show("  check P(R < -1.595) with 1.645", "{}".format(pct(phi(-1.645), 4)))
show("  5% one-day VaR, 2 dp half up (BA 1.595 is a tie)", "{}% (BA) / {}% (TI) -> about {:.1f}% either way".format(r2(-cut_ba), r2(-cut_ti), -cut_ti))
show("  BA and TI differ in the 4th decimal", "{:.4f}".format(cut_ti - cut_ba))
show("  1 day in 20", "{:.0f}".format(1 / 0.05))

# ---------------------------------------------------------------------------
print("\n=== Back to the story ===")
p_crash = phi(z_crash)
log10p = math.log10(p_crash)
show("P(Z < -22.65)", "{:.4e}".format(p_crash))
show("log10 P", "{:.4f} -> {:.1f}".format(log10p, log10p))
digits = "{:.120f}".format(p_crash)[2:]
show("zeros after the decimal point before the first digit", "{} zeros, then {}".format(len(digits) - len(digits.lstrip("0")), digits.lstrip("0")[0]))
wait_days = 1 / p_crash
wait_years = wait_days / 252
show("expected wait in trading days 1/p", "{:.4e}".format(wait_days))
show("expected wait in years (252 days)", "{:.4e}".format(wait_years))
UNIVERSE = 1.38e10
show("universe age (about 13.8 billion years = 1.4 x 10^10)", "{:.2e}".format(UNIVERSE))
show("wait / universe age", "{:.2e}".format(wait_years / UNIVERSE))
z_fri = (-4.6 - MU_D) / SD_D
p_fri = phi(z_fri)
show("Friday: z = (-4.6 - 0.05)/1", "{:.2f}".format(z_fri))
show("Friday: P(Z < -4.65)", "{:.4e}".format(p_fri))
show("Friday: once in ... trading days ; years", "{:,.0f} ; {:,.0f}".format(1 / p_fri, 1 / p_fri / 252))
show("Friday using exact 4.6007%", "z {:.4f} ; years {:,.0f}".format((-100 * FRI_DROP / thu_close - MU_D), 1 / phi(-100 * FRI_DROP / thu_close - MU_D) / 252))
show("crash in 'Friday units' (-22.6 / -4.6)", "{:.1f}".format(22.6 / 4.6))

# ---------------------------------------------------------------------------
print("\n=== Exam traps ===")
show("1.96 instead of 1.645: .05 - 1.96", "{:.2f}%  (P below = {})".format(MU_D - 1.96, pct(phi(-1.96))))
show("1.645 one tail: P below", pct(phi(-1.645)))
z_var = (0 - mu) / (sd ** 2)
show("dividing by variance: (0 - 8)/256", "{:.5f} (about {:.3f}) -> P = {} (near 49%; table at -0.03: {})".format(z_var, z_var, pct(phi(z_var)), pct(1 - float(table(0.03)))))
show("  vs right z -0.50 -> P", pct(phi(-0.5)))
show("kurtosis 3 -> excess", 3 - 3)

# ---------------------------------------------------------------------------
print("\n=== c1: monthly mean 0.8%, SD 4.5%, P(month < -10%) ===")
mu1, sd1, x1 = 0.8, 4.5, -10.0
z1 = (x1 - mu1) / sd1
show("z = (-10 - 0.8)/4.5", "{!r} -> BA {} / TI {}".format(z1, d4(z1), ti4(z1)))
show("  -10 - 0.8", "{:.1f}".format(x1 - mu1))
for zz in (2.30, 2.35, 2.40, 2.45):
    show("  table P(Z <= {:.2f})".format(zz), table(zz))
show("  BA: 1 - .9918", d4(1 - 0.9918))
p1 = phi(z1)
assert abs(p1 - NormalDist(mu1, sd1).cdf(x1)) < 1e-15
show("  TI normalcdf(-1E99,-10,.8,4.5)", "{!r} -> TI {}".format(p1, ti4(p1)))
show("  answer x 100 (data-answer)", "{!r} -> {:.2f}%".format(100 * p1, 100 * p1))
show("  trap: forgot to subtract the mean: z = -10/4.5", "{:.4f} -> {} (table at -2.22: {}) -> about 1.3%".format(-10 / 4.5, pct(phi(-10 / 4.5)), pct(1 - float(table(2.22)))))
show("  trap: divided by variance 20.25", "{:.4f} -> {}".format(z1 * sd1 / sd1 ** 2, pct(phi(z1 * sd1 / sd1 ** 2))))
show("  1 month in ...", "{:.0f}".format(1 / p1))

# ---------------------------------------------------------------------------
print("\n=== c3: 5% left-tail cutoff -10%, mean 1% -> sigma ===")
mu3, cut3 = 1.0, -10.0
s3 = (mu3 - cut3) / 1.645
s3x = (mu3 - cut3) / Z.inv_cdf(0.95)
show("  1 - (-10)", "{:.0f}".format(mu3 - cut3))
show("  BA: (1 + 10) / 1.645 (data-answer)", "{!r} -> BA {} -> {:.2f}%".format(s3, d4(s3), s3))
show("  TI: invNorm(.05)", ti4(Z.inv_cdf(0.05)))
show("  TI: (-10 - 1) / Ans  (exact z)", "{!r} -> TI {}".format((cut3 - mu3) / Z.inv_cdf(0.05), ti4((cut3 - mu3) / Z.inv_cdf(0.05))))
show("  gap between the two", "{:.4f}".format(s3x - s3))
show("  trap 1.96: 11/1.96", "{:.4f}".format(11 / 1.96))
show("  trap 2.33: 11/2.33", "{:.4f}".format(11 / 2.33))
show("  check: 1 - 1.645 x 6.6869", "{:.4f}".format(mu3 - 1.645 * s3))

# ---------------------------------------------------------------------------
print("\n=== c4: mean 7%, SD 15%, 95% interval ===")
mu4, sd4 = 7.0, 15.0
for k, lab in ((1.645, "A (1.645, the 90% band)"), (2.58, "B (2.58, the 99% band)"), (1.96, "C (right, 1.96)")):
    show("{}: {} x 15 = {:.3f}".format(lab, k, k * sd4), "{}% to {}%".format(r2(mu4 - k * sd4), r2(mu4 + k * sd4)))
show("  share inside the right band", pct(normalcdf(mu4 - 1.96 * sd4, mu4 + 1.96 * sd4, mu4, sd4)))

# ---------------------------------------------------------------------------
print("\n=== c5 numbers (model answer) ===")
show("z", "{:.2f}".format(z_crash))
show("P", "about {:.0f} x 10^{}".format(p_crash / 10 ** math.floor(log10p), math.floor(log10p)))
