#!/usr/bin/env python3
"""Day 34 of CFA Math: every number that appears on day-34.html.

Risk aversion, utility and the capital allocation line: mean-variance utility
U = E(r) - 1/2 A sigma^2 (decimals) as a certainty-equivalent return; risk
attitude from the sign of A; indifference curves; the capital allocation line
E(R_p) = R_f + [(E(R_i) - R_f)/sigma_i] sigma_p with sigma_p = w sigma_i; the
optimal risky weight w* = (E(R_i) - R_f)/(A sigma_i^2); the capital market line
with lending and borrowing portfolios; and Roy's safety-first ratio
SFRatio = (E(R_p) - R_L)/sigma_p (CFA Program 2027 Level I, Quantitative
Methods, LM8 The Return and Risk of a Financial Portfolio).
Story: the St Petersburg game (Nicolaus Bernoulli 1713, Daniel Bernoulli 1738)
and Daniel's logarithmic rule; the certainty equivalents are OUR computation
with his rule, not figures he printed.
Plain Python 3, standard library only. Run it and copy the printed numbers.
"""

import datetime
import math
from decimal import Decimal, ROUND_HALF_UP


def show(label, value):
    print("  {:<72} {}".format(label, value))


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


def both(x):
    return "BA {} / TI {}".format(d4(x), ti4(x))


def r2(x):
    """Round half up to 2 decimals, as a person would write it."""
    return str(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct(x):
    """A decimal return written as a percent to two decimals."""
    return r2(x * 100) + "%"


def utility(E, s, A):
    return E - 0.5 * A * s ** 2


def ncdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ---------------------------------------------------------------------------
print("=== Release date ===")
start = datetime.date(2026, 11, 16)
due = start + datetime.timedelta(days=34 - 1)
show("course start (plan start_date)", start.isoformat() + " " + start.strftime("%A"))
show("Day 34 due = start + 33 days", due.isoformat() + " " + due.strftime("%A"))

# ---------------------------------------------------------------------------
print("\n=== Warm-up ===")
s1, s2, rho = 16.0, 24.0, 0.3
cov = rho * s1 * s2
num = s2 ** 2 - cov
den = s1 ** 2 + s2 ** 2 - 2 * cov
w1 = num / den
var_p = w1 ** 2 * s1 ** 2 + (1 - w1) ** 2 * s2 ** 2 + 2 * w1 * (1 - w1) * cov
show("w1 Cov = 0.3 x 16 x 24 (%^2)", both(cov))
show("w1 16^2 ; 24^2", "{:.0f} ; {:.0f}".format(s1 ** 2, s2 ** 2))
show("w1 numerator 576 - 115.2", both(num))
show("w1 denominator 256 + 576 - 2 x 115.2 ; 2 x Cov", "{} ; {:.1f}".format(both(den), 2 * cov))
show("w1 w1* (data-answer, x 100)", "{!r} -> {} -> {}%".format(w1 * 100, both(w1), r2(w1 * 100)))
show("w1 weight on asset 2", "{:.4f} -> {}%".format(1 - w1, r2((1 - w1) * 100)))
show("w1 minimum sigma_p (%)", "{:.4f} -> {}%".format(math.sqrt(var_p), r2(math.sqrt(var_p))))
show("w1 short-sale check rho < s1/s2", "{:.4f}".format(s1 / s2))
show("w1 trap: ignore Cov  576/(256+576)", "{:.4f}".format(s2 ** 2 / (s1 ** 2 + s2 ** 2)))
show("w1 trap: rho=-1 shortcut 24/(16+24)", "{:.4f}".format(s2 / (s1 + s2)))

pw, pbw, pbn = 0.20, 0.60, 0.25
joint = pbw * pw
pb = joint + pbn * (1 - pw)
post = joint / pb
show("w2 joint P(buy and winner) = 0.60 x 0.20 (C)", "{:.4f} -> {}".format(joint, pct(joint)))
show("w2 P(buy and not) = 0.25 x 0.80", "{:.4f}".format(pbn * (1 - pw)))
show("w2 P(buy) = 0.12 + 0.20", "{:.4f}".format(pb))
show("w2 posterior 0.12 / 0.32 (B, right)", "{:.4f} -> {}".format(post, pct(post)))
show("w2 inverted conditional P(buy | winner) (A)", pct(pbw))

# ---------------------------------------------------------------------------
print("\n=== Story: the St Petersburg game ===")
show("years 1713 -> 1738", 1738 - 1713)
for k in (1, 2, 3, 4):
    p = 0.5 ** k
    pay = 2 ** (k - 1)
    show("toss {}: chance {} x payout {}".format(k, p, pay), "{:.4f}".format(p * pay))
show("expected value after 10, 20, 40 possible tosses", "{:.1f} ; {:.1f} ; {:.1f}".format(10 / 2, 20 / 2, 40 / 2))
show("P(payout = 1) = P(heads first)", "{:.2f}".format(0.5))
show("P(payout <= 2)", "{:.2f}".format(0.5 + 0.25))
show("2^10 ; P(payout >= 1,024) = P(first 10 tosses tails) = 1/2^10", "{} ; {:.6f} = 1 in {}".format(2 ** 10, 0.5 ** 10, 2 ** 10))
show("  payout on toss 11", 2 ** 10)


def log_ce(W, terms=400):
    """Certainty equivalent of a FREE ticket for a player with wealth W, log rule."""
    if W == 0:
        el = sum(0.5 ** k * (k - 1) * math.log(2) for k in range(1, terms))
        return math.exp(el)
    el = sum(0.5 ** k * math.log(W + 2 ** (k - 1)) for k in range(1, terms))
    return math.exp(el) - W


for W in (0, 10, 100, 1000):
    ce = log_ce(W)
    show("log-rule certainty equivalent, wealth {:>5}".format(W), "{:.6f} -> {}".format(ce, r2(ce)))
show("W = 0: ln 2 x sum (k-1)/2^k ; that sum", "{:.6f} ; {:.6f}".format(
    sum(0.5 ** k * (k - 1) for k in range(1, 400)) * math.log(2), sum(0.5 ** k * (k - 1) for k in range(1, 400))))
show("log step per doubling: ln 2", "{:.6f}".format(math.log(2)))

# ---------------------------------------------------------------------------
print("\n=== Why it works: Bernoulli's log = Day 12's geometric mean ===")
for x in (0.10, 0.20):
    g = math.sqrt((1 + x) * (1 - x)) - 1
    show("+/-{:.0%} coin: geometric mean sqrt({:.2f} x {:.2f}) - 1".format(x, 1 + x, 1 - x),
         "{:.6f} -> {:.4f}%".format(g, g * 100))
    show("   (1+x)(1-x) = 1 - x^2 ; 1/2 x^2", "{:.4f} ; {:.4f} -> {:.2f}%".format((1 + x) * (1 - x), 0.5 * x ** 2, 50 * x ** 2))
    show("   SD of the +/-x coin (population)", "{:.2f}".format(math.sqrt(0.5 * x ** 2 + 0.5 * x ** 2)))
show("log-utility CE of +/-20% equals avg log", "{:.6f} vs {:.6f}".format(
    math.log(1 + (math.sqrt(0.96) - 1)), 0.5 * math.log(1.2) + 0.5 * math.log(0.8)))

# ---------------------------------------------------------------------------
print("\n=== Walkthrough: A = 4, E = 10%, sigma = 20%, Rf = 3% ===")
A, E, s, Rf, s_t, RL = 4, 0.10, 0.20, 0.03, 0.12, 0.02
U_risky = utility(E, s, A)
U_bill = utility(Rf, 0.0, A)
prem = E - Rf
w_star = prem / (A * s ** 2)
E_star = Rf + w_star * prem
s_star = w_star * s
U_star = utility(E_star, s_star, A)
slope = prem / s
cal = Rf + slope * s_t
sf = (E - RL) / s
show("0.20^2 ; 0.5 x 4 x 0.04 (penalty)", "{:.4f} ; {:.4f}".format(s ** 2, 0.5 * A * s ** 2))
show("U all risky = 0.10 - 0.08", "{!r} -> {} -> {}".format(U_risky, both(U_risky), pct(U_risky)))
show("U all T-bills (sigma 0)", "{} -> {}".format(both(U_bill), pct(U_bill)))
show("risk premium 0.10 - 0.03", "{:.4f}".format(prem))
show("4 x 0.04", "{:.4f}".format(A * s ** 2))
show("w* = 0.07 / 0.16 (STO 1 / ->A) (walkthrough answer)", "{!r} -> {} -> {}%".format(w_star, both(w_star), r2(w_star * 100)))
show("T-bill share 1 - w*", "{:.4f} -> {}%".format(1 - w_star, r2((1 - w_star) * 100)))
show("w* x 0.07", "{:.6f}".format(w_star * prem))
show("E at w* = 0.03 + w* x 0.07 (STO 2 / ->B)", "{!r} -> {} -> {}".format(E_star, both(E_star), pct(E_star)))
show("sigma at w* = w* x 0.20 (STO 3 / ->C)", "{!r} -> {} -> {}".format(s_star, both(s_star), pct(s_star)))
show("sigma^2 at w* ; x 0.5 x 4", "{:.8f} ; {:.7f}".format(s_star ** 2, 0.5 * A * s_star ** 2))
show("U at w*", "{!r} -> {} -> {}".format(U_star, both(U_star), pct(U_star)))
show("U at w* by Rf + prem^2/(2 A s^2)", "{:.7f}".format(Rf + prem ** 2 / (2 * A * s ** 2)))
show("gain over T-bills U* - Rf", "{:.7f} -> {}".format(U_star - Rf, pct(U_star - Rf)))
show("CAL slope 0.07 / 0.20", both(slope))
show("CAL at sigma 12%: 0.03 + 0.35 x 0.12", "{!r} -> {} -> {}".format(cal, both(cal), pct(cal)))
show("  0.35 x 0.12 ; weight on the risky portfolio 0.12 / 0.20", "{:.4f} ; {:.4f}".format(slope * s_t, s_t / s))
show("SFRatio (0.10 - 0.02) / 0.20", "{!r} -> {}".format(sf, both(sf)))
show("SF: P(R < 2%) if normal = N(-0.40)", "{:.4f} -> {}".format(ncdf(-sf), pct(ncdf(-sf))))
show("trap: R_f instead of R_L (Sharpe) (0.10 - 0.03)/0.20", "{:.4f}".format((E - Rf) / s))

print("  -- the hill: U(w) = Rf + w x 0.07 - 2 x w^2 x 0.04 --")
for w in (0.0, 0.25, 0.4375, 0.5, 0.75, 0.875, 1.0):
    Uw = Rf + w * prem - 0.5 * A * w ** 2 * s ** 2
    show("  w = {:.4f}: U".format(w), "{:.6f} -> {}".format(Uw, pct(Uw)))
b = 0.5 * A * s ** 2
show("b = 1/2 x A x sigma^2 ; zero of the gain w = prem / b", "{:.4f} ; {:.4f}".format(b, prem / b))
show("top halfway: (prem / b) / 2", "{:.4f}".format(prem / b / 2))

print("  -- indifference curve through the optimum (A = 4, U = 4.53%) --")
for sg in (0.0, 0.0875, 0.20):
    need = U_star + 0.5 * A * sg ** 2
    show("  E needed at sigma {:.4f}".format(sg), "{:.6f} -> {}".format(need, pct(need)))

print("  -- other A for the same risky portfolio --")
for AA in (1, 2, 3, 4, 6, 8):
    ww = prem / (AA * s ** 2)
    show("  A = {}: w* ; E ; sigma ; U".format(AA), "{:.4f} ; {} ; {} ; {}".format(
        ww, pct(Rf + ww * prem), pct(ww * s), pct(utility(Rf + ww * prem, ww * s, AA))))

# ---------------------------------------------------------------------------
print("\n=== Back to the story ===")
show("risky alone vs T-bill vs best mix", "{} ; {} ; {}".format(pct(U_risky), pct(U_bill), pct(U_star)))
show("ceiling of the sum of the first n tosses' log steps (W = 0)", "{:.4f}".format(math.log(2)))

# ---------------------------------------------------------------------------
print("\n=== Worked example: A = 3, X (8%, 10%), Y (12%, 20%), R_L = 3% ===")
AX = 3
Ex, sx, Ey, sy, RLw = 0.08, 0.10, 0.12, 0.20, 0.03
Ux, Uy = utility(Ex, sx, AX), utility(Ey, sy, AX)
show("penalty X: 0.5 x 3 x 0.10^2", "{:.4f}".format(0.5 * AX * sx ** 2))
show("U_X", "{:.4f} -> {}".format(Ux, pct(Ux)))
show("penalty Y: 0.5 x 3 x 0.20^2", "{:.4f}".format(0.5 * AX * sy ** 2))
show("U_Y", "{:.4f} -> {}".format(Uy, pct(Uy)))
show("A = 0: U_X ; U_Y", "{} ; {}".format(pct(Ex), pct(Ey)))
sfx, sfy = (Ex - RLw) / sx, (Ey - RLw) / sy
show("SF_X = (8 - 3)/10 ; SF_Y = (12 - 3)/20", "{:.4f} ; {:.4f}".format(sfx, sfy))
show("P(R < 3%) if normal: N(-0.50) ; N(-0.45)", "{:.4f} -> {} ; {:.4f} -> {}".format(
    ncdf(-sfx), pct(ncdf(-sfx)), ncdf(-sfy), pct(ncdf(-sfy))))
show("A at which X and Y tie: (0.12-0.08)/(0.5 x (0.04-0.01))", "{:.4f}".format((Ey - Ex) / (0.5 * (sy ** 2 - sx ** 2))))
show("Y's extra return ; Y's extra penalty (points)", "{:.2f} ; {:.2f}".format(
    100 * (Ey - Ex), 100 * (0.5 * AX * sy ** 2 - 0.5 * AX * sx ** 2)))

print("\n=== The idea: sure $50 vs a coin flip for $0 or $100 ===")
show("expected value of the flip", "{:.0f}".format(0.5 * 0 + 0.5 * 100))

# ---------------------------------------------------------------------------
print("\n=== Exam traps ===")
show("percent typed: 10 - 0.5 x 4 x 20^2", "{:.0f}".format(10 - 0.5 * 4 * 20 ** 2))
show("decimals: 0.10 - 0.5 x 4 x 0.20^2", "{:.4f} -> {}".format(U_risky, pct(U_risky)))
show("percent with 0.005: 10 - 0.005 x 4 x 400", "{:.2f}".format(10 - 0.005 * 4 * 400))
show("SF with R_f instead of R_L", "{:.4f} vs {:.4f}".format((E - Rf) / s, sf))

# ---------------------------------------------------------------------------
print("\n=== c1: A = 3, E = 9%, sigma = 18%, R_f = 2.5% ===")
A1, E1, s1c, Rf1 = 3, 0.09, 0.18, 0.025
w1c = (E1 - Rf1) / (A1 * s1c ** 2)
show("premium 0.09 - 0.025", "{:.4f}".format(E1 - Rf1))
show("0.18^2 ; 3 x 0.0324", "{:.4f} ; {:.4f}".format(s1c ** 2, A1 * s1c ** 2))
show("w* (decimal) ; x 100 (data-answer)", "{!r} -> {} ; {!r} -> {}%".format(w1c, both(w1c), w1c * 100, r2(w1c * 100)))
show("  x 100 display", both(w1c * 100))
show("T-bill share 1 - w*", "{:.4f} -> {}%".format(1 - w1c, r2(100 - float(r2(w1c * 100)))))
E1s, s1s = Rf1 + w1c * (E1 - Rf1), w1c * s1c
show("side: E ; sigma ; U at w*", "{} ; {} ; {}".format(pct(E1s), pct(s1s), pct(utility(E1s, s1s, A1))))
show("trap: forgot to square sigma 0.065/(3 x 0.18)", "{:.4f}".format((E1 - Rf1) / (A1 * s1c)))
show("trap: E instead of premium 0.09/(3 x 0.0324)", "{:.4f}".format(E1 / (A1 * s1c ** 2)))

# ---------------------------------------------------------------------------
print("\n=== c2: walkthrough portfolio, A from 2 to 6 ===")
w_a2 = prem / (2 * s ** 2)
w_a6 = prem / (6 * s ** 2)
show("denominators A x 0.04 at A = 2 ; A = 6", "{:.2f} ; {:.2f}".format(2 * s ** 2, 6 * s ** 2))
show("A = 2: 0.07 / (2 x 0.04)", "{:.4f}".format(w_a2))
show("A = 6: 0.07 / (6 x 0.04)", "{:.6f} -> {:.4f}".format(w_a6, w_a6))
show("ratio", "{:.4f}".format(w_a6 / w_a2))
show("T-bill share at A = 6", "{:.4f}".format(1 - w_a6))

# ---------------------------------------------------------------------------
print("\n=== c3: target return 12% on the walkthrough CAL ===")
tgt = 0.12
w3 = (tgt - Rf) / (E - Rf)
show("w = (0.12 - 0.03)/(0.10 - 0.03) (data-answer)", "{!r} -> {}".format(w3, both(w3)))
show("borrowed per $1 of own money ; sigma_p = w x 20%", "{:.4f} ; {:.4f} -> {}".format(w3 - 1, w3 * s, pct(w3 * s)))
show("check: 1.2857 x 10% - 0.2857 x 3%", "{:.4f}".format(w3 * E - (w3 - 1) * Rf))
show("check via CAL: 0.03 + 0.35 x sigma_p", "{:.4f}".format(Rf + slope * w3 * s))
show("trap: 0.12 / 0.10", "{:.4f}".format(tgt / E))

# ---------------------------------------------------------------------------
print("\n=== c4: A = 2, E = 11%, sigma = 25% ===")
A4, E4, s4 = 2, 0.11, 0.25
show("C (right): 0.11 - 0.5 x 2 x 0.0625", "{:.4f} -> {}".format(utility(E4, s4, A4), pct(utility(E4, s4, A4))))
show("A: sigma not squared 0.11 - 0.5 x 2 x 0.25", "{:.4f} -> {}".format(E4 - 0.5 * A4 * s4, pct(E4 - 0.5 * A4 * s4)))
show("B: dropped the 1/2 0.11 - 2 x 0.0625", "{:.4f} -> {}".format(E4 - A4 * s4 ** 2, pct(E4 - A4 * s4 ** 2)))
show("  0.25^2", "{:.4f}".format(s4 ** 2))
