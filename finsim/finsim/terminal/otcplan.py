"""``python -m finsim otcplan`` - the owner's OTC booking program, its read-only checker and the independent re-pricer (FX_CCS_PLAN rev 2
section 10, K9; release 1.0.6.1, package P-D).

    otcplan run <plan.json> --base-url <url> --report <dir> [--window any]
    otcplan snapshot --db <terminal.db> --out <before.json>
    otcplan check --db <terminal.db> --before <before.json> --run <report.json> [--second <report2.json>] [--after-restart <views.json>]
    otcplan reprice <record.json> [--kind FX_FORWARD | XCCY]

Conventions stated here
- INDEPENDENCE (K9, FX-C-05): this module imports only the standard library.  It never imports ``fxcurves``, ``ccycal``, ``fx`` or a
  product module (a test fails if it does), so a pricing bug shared by the quote and the contract cannot pass the checks.  Its own
  calendar rules (TARGET2, New York, London; ``CalRules``) re-derive the dates, its own Decimal arithmetic (precision 34, HALF_EVEN)
  re-derives every price from the pricing record alone.
- CURVES: a currency's zero curve is the record's nodes [(t, z %)] (t in years ACT/365 from the curve's ``as_of``), continuously
  compounded, linear in z between nodes, flat before the first node, no extrapolation beyond the last (CURVE_SHORT).  The first node
  (t = 1/365) is re-derived from the overnight print: z = 365 x ln(1 + r / B) (B = 360 ESTR / SOFR, 365 SONIA).  The EUR OIS spread
  (``spread_bp``) moves the EUR grid nodes (not the ESTR node), for projection and discounting; a basis level moves every node of the
  discount curve only.  DF(a, b) = exp(-z(t_b) t_b + z(t_a) t_a).
- THE FORWARD (FX_CCS_PLAN 3.2): S = the pair print (a USD pair) or base-USD / quote-USD quantised to 6 dp (a cross); DF*_base and
  DF*_quote from the spot date to the value date, quantised to 1e-10; F = S x DF*_base / DF*_quote at the pair's precision (6 dp; JPY
  quoted 4 dp); points = (F - S) / pip to 2 dp; hw_px = F x hw_bp x mult / 10,000 at the pair's precision; K = F - hw_px for a sale of
  the base, + for a purchase; the quote-currency amount to_c(A x K); USD notional to_c(A x base-USD); IM to_c(USD notional x pct);
  mark0 = to_c(sign x A x (F - K) x DF*_quote x quote-USD), sign -1 for a sale.
- THE SWAP (3.3): the full value for the spot date S at T: USD flows -N_USD at S, coupons N_USD (1 / DF_USD(s, e) - 1) at p, N_USD at
  p_n; EUR flows + N_EUR at S, coupons N_EUR (1 / DF_EUR(s, e) - 1) + N_EUR b d / 360 at p, N_EUR at p_n, discounted on DF*_EUR (the
  basis level) and converted at X0; every DF unquantised, the PV rounded to the cent at the end.  A* = sum (d / 360) DF*_EUR(S, p_k)
  at 1e-10 (the DFs unquantised, K9; the sum at 1e-10); s_par = PV(0) / (X0 N_EUR A*), unrounded; dealt = s_par + hw x mult rounded UP
  on the 0.1 bp grid for the payer of the EUR leg (down for its receiver); mark0 = the full PV at the dealt spread.  The conversion:
  X0 - 0.2 pip, USD to_c(N_EUR x rate), spread to_c(N_EUR x X0) - USD.
- FRESHNESS (10.2; V1061-01, 1.0.6.1 fix round 1): each FX print a quote is priced on is at most ``PRINT_MAX_AGE_S`` (300 s) older
  than the quote and was executable when quoted (quality delayed / live); the two USD legs of a cross are at most ``LEG_GAP_MAX_S``
  (120 s) apart.  The terminal's only FX source (Yahoo EURUSD=X / GBPUSD=X) stamps its prints on a 60 s grid and serves them 168-228 s
  after their own time, so the plan's first rule ("the quote within 120 s of the spot") could never pass on real data.  A failure of
  the data's timing (``Stale``) declines the quote and asks again on the next attempt (as 10.2 step 2 retries QUOTE_STALE); any other
  failure (a price that does not re-derive, a label, the plan's terms) ends the trade UNEXECUTED at once.
"""
from __future__ import annotations

import argparse
import calendar as _calendar
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Callable, Dict, List, Optional, Sequence, Tuple

PROG = "python -m finsim otcplan"
D = Decimal
CTX = Context(prec=34, rounding=ROUND_HALF_EVEN)
BP = D("0.0001")
Q10 = D("0.0000000001")
CENT = D("0.01")
PLAN_ID_RE = re.compile(r"^[A-Za-z0-9:_.-]{8,80}$")


class RepriceError(ValueError):
    """A pricing record that cannot be re-derived (a missing field, a date or node out of range)."""


# ---- decimal helpers -------------------------------------------------------------------------------------------------------------------
def dec(x, what: str = "value") -> Decimal:
    """A Decimal from a record field: a number string, an int, a Decimal, or a fraction "a/b" (precision 34)."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, bool) or x is None:
        raise RepriceError(f"{what}: missing")
    if isinstance(x, int):
        return D(x)
    if isinstance(x, float):
        return D(repr(x))
    s = str(x).strip()
    if "/" in s:
        a, b = s.split("/", 1)
        with localcontext(CTX):
            return D(a.strip()) / D(b.strip())
    try:
        return D(s)
    except Exception as e:   # noqa: BLE001 - InvalidOperation and friends
        raise RepriceError(f"{what}: not a number ({x!r})") from e


def qz(x: Decimal, exp: str) -> Decimal:
    return x.quantize(D(exp), rounding=ROUND_HALF_EVEN)


def to_cents(x: Decimal) -> int:
    """Units -> integer cents, HALF_EVEN (the terminal's ``units.to_c``)."""
    return int((x * 100).quantize(D(1), rounding=ROUND_HALF_EVEN))


def d_of(x, what: str = "date") -> date:
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError) as e:
        raise RepriceError(f"{what}: not a date ({x!r})") from e


def pip_of(pair: str) -> Decimal:
    return D("0.01") if "JPY" in str(pair).upper() else D("0.0001")


def px_exp(pair: str) -> str:
    """The pair's quantisation: 6 dp, JPY-quoted 4 dp."""
    return "0.0001" if "JPY" in str(pair).upper() else "0.000001"


# ---- calendars (independent of ccycal: FX_CCS_PLAN D10's rules, written again) ---------------------------------------------------------
def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d_, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d_ - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth(y: int, m: int, wd: int, n: int) -> date:
    d0 = date(y, m, 1)
    d0 += timedelta(days=(wd - d0.weekday()) % 7)
    return d0 + timedelta(weeks=n - 1)


def _last(y: int, m: int, wd: int) -> date:
    d0 = (date(y, m + 1, 1) - timedelta(days=1)) if m < 12 else date(y, 12, 31)
    while d0.weekday() != wd:
        d0 -= timedelta(days=1)
    return d0


def _target(y: int) -> set:
    e = _easter(y)
    return {date(y, 1, 1), e - timedelta(days=2), e + timedelta(days=1), date(y, 5, 1), date(y, 12, 25), date(y, 12, 26)}


def _fed(d0: date) -> Optional[date]:
    if d0.weekday() == 6:
        return d0 + timedelta(days=1)
    if d0.weekday() == 5:
        return None                      # a Saturday holiday is not observed (Federal Reserve)
    return d0


def _usny(y: int) -> set:
    s = {_fed(date(y, 1, 1)), _nth(y, 1, 0, 3), _nth(y, 2, 0, 3), _last(y, 5, 0), _fed(date(y, 6, 19)), _fed(date(y, 7, 4)), _nth(y, 9, 0, 1),
         _nth(y, 10, 0, 2), _fed(date(y, 11, 11)), _nth(y, 11, 3, 4), _fed(date(y, 12, 25))}
    s.discard(None)
    return s


def _gblo(y: int) -> set:
    e = _easter(y)
    s = set()
    ny = date(y, 1, 1)
    s.add(ny if ny.weekday() < 5 else ny + timedelta(days=7 - ny.weekday()))
    s |= {e - timedelta(days=2), e + timedelta(days=1), _nth(y, 5, 0, 1), _last(y, 5, 0), _last(y, 8, 0)}
    xm = date(y, 12, 25)
    if xm.weekday() == 5:
        s |= {date(y, 12, 27), date(y, 12, 28)}
    elif xm.weekday() == 6:
        s |= {date(y, 12, 26), date(y, 12, 27)}
    elif xm.weekday() == 4:
        s |= {xm, date(y, 12, 28)}
    else:
        s |= {xm, date(y, 12, 26)}
    return s


def _usgs(y: int) -> set:
    return _usny(y) | {_easter(y) - timedelta(days=2)}


RULES: Dict[str, Callable[[int], set]] = {"EUR": _target, "USD": _usny, "GBP": _gblo, "USGS": _usgs}


class CalRules:
    """A joint calendar of the named rule sets (EUR = TARGET2, USD = New York, GBP = London, USGS = SIFMA without its one-off list)."""

    def __init__(self, *names: str):
        self.names = tuple(names)
        self._cache: Dict[int, set] = {}

    def _hol(self, y: int) -> set:
        if y not in self._cache:
            s: set = set()
            for n in self.names:
                s |= RULES[n](y)
            self._cache[y] = s
        return self._cache[y]

    def good(self, d0: date) -> bool:
        return d0.weekday() < 5 and d0 not in self._hol(d0.year)

    def add_bd(self, d0: date, n: int) -> date:
        while n > 0:
            d0 += timedelta(days=1)
            if self.good(d0):
                n -= 1
        return d0

    def mf(self, d0: date) -> date:
        x = d0
        while not self.good(x):
            x += timedelta(days=1)
        if x.month != d0.month:
            x = d0
            while not self.good(x):
                x -= timedelta(days=1)
        return x

    def month_end(self, d0: date) -> date:
        x = date(d0.year, d0.month, _calendar.monthrange(d0.year, d0.month)[1])
        while not self.good(x):
            x -= timedelta(days=1)
        return x


def add_months(d0: date, m: int) -> date:
    y, mm = divmod(d0.month - 1 + m, 12)
    y += d0.year
    mm += 1
    return date(y, mm, min(d0.day, _calendar.monthrange(y, mm)[1]))


def spot_date(pair: str, trade_date: date) -> date:
    """D10: a USD pair - T+1 a business day of the non-USD currency, the spot date good in both; a cross - T+1 good in both, the
    spot date also in New York.  USD / EUR / GBP pairs only."""
    b, q = pair[:3].upper(), pair[3:].upper()
    if b not in RULES or q not in RULES:
        raise RepriceError(f"{pair}: no calendar here for {b if b not in RULES else q}")
    if "USD" in (b, q):
        other = q if b == "USD" else b
        d1 = CalRules(other).add_bd(trade_date, 1)
        return CalRules(other, "USD").add_bd(d1, 1)
    d1 = CalRules(b, q).add_bd(trade_date, 1)
    return CalRules(b, q, "USD").add_bd(d1, 1)


def value_cal(pair: str) -> CalRules:
    b, q = pair[:3].upper(), pair[3:].upper()
    return CalRules(b, q) if "USD" in (b, q) else CalRules(b, q, "USD")


TENOR_MONTHS = {"1M": 1, "2M": 2, "3M": 3, "6M": 6, "9M": 9, "1Y": 12, "18M": 18, "2Y": 24, "24M": 24}


def tenor_date(pair: str, tenor: str, trade_date: date) -> date:
    """Months from the spot date, modified following on the value calendar, end-of-month rule; 1W / 2W roll following."""
    t = str(tenor).upper()
    sd = spot_date(pair, trade_date)
    cal = value_cal(pair)
    if t in ("1W", "2W"):
        x = sd + timedelta(days=7 * int(t[0]))
        while not cal.good(x):
            x += timedelta(days=1)
        return x
    if t not in TENOR_MONTHS:
        raise RepriceError(f"tenor {tenor!r} is not re-derivable here")
    target = add_months(sd, TENOR_MONTHS[t])
    if sd == cal.month_end(sd):
        return cal.month_end(target)
    return cal.mf(target)


def ccs_schedule(start: date, months: int, step: int = 3, delay_bd: int = 2) -> List[Tuple[date, date, date]]:
    """The float / float swap's periods: e_k = MF(S + 3k months) on TARGET2 + New York, p_k = e_k + 2 BD, s_1 = S, s_k = e_(k-1)."""
    cal = CalRules("EUR", "USD")
    ends = [cal.mf(add_months(start, step * k)) for k in range(1, months // step + 1)]
    starts = [start] + ends[:-1]
    return [(s, e, cal.add_bd(e, delay_bd)) for s, e in zip(starts, ends)]


# ---- curves ----------------------------------------------------------------------------------------------------------------------------------
ON_BASIS = {"SOFR": 360, "ESTR": 360, "IUDSOIA": 365, "SONIA": 365}


class Curve:
    """A re-derived zero curve (module docstring CURVES).  ``nodes`` [(t, z fraction)] after the first node and the grid spread."""

    def __init__(self, ccy: str, as_of: date, nodes: List[Tuple[Decimal, Decimal]], first: Optional[dict] = None):
        self.ccy, self.as_of, self.nodes, self.first = ccy, as_of, nodes, first or {}

    def t(self, d0: date) -> Decimal:
        with localcontext(CTX):
            return D((d0 - self.as_of).days) / D(365)

    def z(self, t: Decimal, level: Decimal = D(0)) -> Decimal:
        """z(t) as a fraction, + ``level`` (a basis level / bump on every node)."""
        pts = self.nodes
        with localcontext(CTX):
            if t <= pts[0][0]:
                return pts[0][1] + level
            if t > pts[-1][0]:
                raise RepriceError(f"{self.ccy}: t = {qz(t, '0.000001')} is beyond the last node ({qz(pts[-1][0], '0.000001')}): CURVE_SHORT")
            for (t0, z0), (t1, z1) in zip(pts, pts[1:]):
                if t0 <= t <= t1:
                    return z0 + (z1 - z0) * (t - t0) / (t1 - t0) + level
        raise RepriceError(f"{self.ccy}: no bracket for t = {t}")

    def df(self, a: date, b: date, level: Decimal = D(0)) -> Decimal:
        with localcontext(CTX):
            ta, tb = self.t(a), self.t(b)
            return (-(self.z(tb, level) * tb) + self.z(ta, level) * ta).exp()


def on_node_z(value_pct, basis: int) -> Decimal:
    """The first node: z = 365 x ln(1 + r / B), as a fraction (r the overnight print in percent)."""
    with localcontext(CTX):
        return (D(1) + dec(value_pct, "overnight print") / D(100) / D(int(basis))).ln() * D(365)


def _node_t(n) -> Decimal:
    if isinstance(n, dict):
        if "months" in n:
            with localcontext(CTX):
                return D(int(n["months"])) / D(12)
        if "t" in n:
            return dec(n["t"], "node t")
        tenor = str(n.get("tenor") or "").upper()
        m = re.fullmatch(r"(\d+)([MY])", tenor)
        if m:
            with localcontext(CTX):
                return D(int(m.group(1)) * (12 if m.group(2) == "Y" else 1)) / D(12)
        raise RepriceError(f"node without t: {n!r}")
    return dec(n[0], "node t")


def _node_z(n) -> Decimal:
    return dec(n.get("z") if isinstance(n, dict) else n[1], "node z")


def curve_from_record(ccy: str, rec: dict, *, grid_spread_bp=None) -> Curve:
    """A ``Curve`` from one currency's block of a pricing record: ``as_of``, ``first_node {series, obs_date, value_pct, basis}`` (re-derived,
    t = 1/365) and ``nodes`` [(t, z %)] or [{tenor, t, z}] (the grid as priced: the EUR nodes already carry the EUR OIS spread, which the
    record also states as ``spread_bp`` for disclosure; a node at t = 1/365 is taken as the first node and replaced by the re-derived one).
    ``grid_spread_bp`` adds a spread to the grid nodes (a record whose nodes are the stored values without it)."""
    if not isinstance(rec, dict):
        raise RepriceError(f"curve {ccy}: missing")
    as_of = d_of(rec.get("as_of") or rec.get("d0"), f"curve {ccy} as_of")
    with localcontext(CTX):
        t_on = D(1) / D(365)
        spread = dec(grid_spread_bp, f"curve {ccy} grid spread") * BP if grid_spread_bp is not None else D(0)
        grid = []
        for n in rec.get("nodes") or []:
            t, zp = _node_t(n), _node_z(n)
            if abs(t - t_on) < D("1e-12"):
                continue
            grid.append((t, zp / D(100) + spread))
        grid.sort(key=lambda x: x[0])
        fn = rec.get("first_node") or {}
        series = str(fn.get("series") or "").upper()
        basis = int(fn.get("basis") or ON_BASIS.get(series, 365 if ccy == "GBP" else 360))
        if fn.get("value_pct") in (None, ""):
            raise RepriceError(f"curve {ccy}: the first node (overnight print) is missing")
        nodes = [(t_on, on_node_z(fn["value_pct"], basis))] + grid
    if len(nodes) < 2:
        raise RepriceError(f"curve {ccy}: no grid nodes")
    return Curve(ccy, as_of, nodes, dict(fn, basis=basis))


def _get(rec: dict, *path, default=None):
    cur = rec
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _first(rec: dict, *paths, what: str = "field"):
    for p in paths:
        v = _get(rec, *p) if isinstance(p, tuple) else rec.get(p)
        if v not in (None, ""):
            return v
    raise RepriceError(f"{what}: missing from the record")


def _usd_rate(rec: dict, ccy: str) -> Decimal:
    """USD per 1 ``ccy`` from the record's ``usd_rates`` (or the spot legs): EURUSD / GBPUSD mids; USDJPY inverted."""
    if ccy == "USD":
        return D(1)
    ur = rec.get("usd_rates")
    for pair in (f"{ccy}USD", f"USD{ccy}"):
        v = ur.get(pair) if isinstance(ur, dict) else None
        if v is None and isinstance(ur, list):
            v = next((x for x in ur if isinstance(x, dict) and str(x.get("pair") or "").upper() == pair), None)
        if v is None:
            for leg in _get(rec, "spot", "legs") or []:
                if str(leg.get("pair") or "").upper() == pair:
                    v = leg
        if v is not None:
            mid = dec(v.get("mid") if isinstance(v, dict) else v, f"{pair} mid")
            if pair.startswith("USD"):
                with localcontext(CTX):
                    return D(1) / mid
            return mid
    raise RepriceError(f"no USD rate for {ccy} in the record (usd_rates)")


# ---- the forward (K9, FX_CCS_PLAN 3.2) ---------------------------------------------------------------------------------------------------
def reprice_forward(record: dict) -> dict:
    """The PB OTC FX forward re-derived from its pricing record alone (module docstring THE FORWARD).  Returns the numbers as strings /
    cents: ``{pair, side, S, spot_date, value_date, days, t_S, t_V, z {ccy: {t_S, t_V}}, df_base, df_quote, F, points, hw_px, K,
    base_amount_c, quote_amount_c, usd_notional_c, im_c, mark0_c, arithmetic}``."""
    rec = record.get("pricing_record") if isinstance(record.get("pricing_record"), dict) else record
    pair = str(_first(rec, ("spot", "pair"), "pair", ("basis", "pair"), what="pair")).upper()
    base, quote = pair[:3], pair[3:]
    side = str(_first(rec, ("dealer", "side"), "side", what="side")).upper()
    if side not in ("SELL", "BUY"):
        raise RepriceError(f"side {side!r}: SELL or BUY (of the base currency)")
    sd = d_of(_first(rec, "spot_date", ("forward", "spot_date"), what="spot_date"), "spot_date")
    vd = d_of(_first(rec, "value_date", ("settlement", "value_date"), what="value_date"), "value_date")
    exp = px_exp(pair)
    pip = pip_of(pair)
    with localcontext(CTX):
        if "USD" in (base, quote):
            S = dec(_first(rec, ("spot", "mid"), what="spot mid"), "spot mid")
        else:
            S = qz(_usd_rate(rec, base) / _usd_rate(rec, quote), exp)
        curves = rec.get("curves") or {}
        cb = curve_from_record(base, curves.get(base))
        cq = curve_from_record(quote, curves.get(quote))
        lvl = {base: _level(rec, base), quote: _level(rec, quote)}
        dfb = qz(cb.df(sd, vd, lvl[base]), "0.0000000001")
        dfq = qz(cq.df(sd, vd, lvl[quote]), "0.0000000001")
        F = qz(S * dfb / dfq, exp)
        points = qz((F - S) / pip, "0.01")
        hw_bp = dec(_first(rec, ("dealer", "forward_hw_bp"), ("dealer", "hw_bp"), what="forward_hw_bp"), "forward_hw_bp")
        mult = dec(_first(rec, ("dealer", "otc_spread_mult"), what="otc_spread_mult"), "otc_spread_mult")
        hw_px = qz(F * hw_bp * mult / D(10000), exp)
        K = F - hw_px if side == "SELL" else F + hw_px
        a_c = cents_of(rec, "notional_base_c", "notional_c")
        if a_c is None:
            st = rec.get("settlement") or {}
            for blk in (st.get("deliver"), st.get("receive")):
                if isinstance(blk, dict) and str(blk.get("ccy") or blk.get("currency") or "").upper() == base:
                    a_c = cents_of(blk, "amount_c")
        if a_c is None:
            raise RepriceError("the forward's base notional (notional_base_c) is missing from the record")
        A = D(int(a_c)) / D(100)
        quote_c = to_cents(A * K)
        usd_notional_c = to_cents(A * _usd_rate(rec, base))
        im_pct = dec(_first(rec, ("im", "pct"), ("results", "im", "pct"), what="im pct"), "im pct")
        im_c = to_cents(D(usd_notional_c) / D(100) * im_pct)
        sign = D(-1) if side == "SELL" else D(1)
        mark0_c = to_cents(sign * A * (F - K) * dfq * _usd_rate(rec, quote))
        ts, tv = cb.t(sd), cb.t(vd)
        z = {c.ccy: {"t_S": str(qz(c.z(c.t(sd)) * 100, "0.000001")), "t_V": str(qz(c.z(c.t(vd)) * 100, "0.000001"))} for c in (cb, cq)}
        # the off-ledger PV deltas at the trade date (D14; B-T6): - A x DF*_base in the base currency, + A x K x DF*_quote in the quote (a sale)
        pv_delta = {base: to_cents(sign * A * dfb), quote: to_cents(-sign * A * K * dfq)}
    return {"kind": "FX_FORWARD", "pair": pair, "side": side, "S": str(S), "spot_date": sd.isoformat(), "value_date": vd.isoformat(), "days": (vd - sd).days,
            "t_S": str(qz(ts, "0.000001")), "t_V": str(qz(tv, "0.000001")), "z": z, "df_base": str(dfb), "df_quote": str(dfq), "F": str(F),
            "points": str(points), "hw_px": str(hw_px), "K": str(K), "base_amount_c": to_cents(A), "quote_amount_c": quote_c,
            "usd_notional_c": usd_notional_c, "im_c": im_c, "mark0_c": mark0_c, "levels_bp": {k: str(v / BP) for k, v in lvl.items()}, "pv_delta_c": pv_delta,
            "arithmetic": f"F = {S} x {dfb} / {dfq} = {F}; K = {F} {'-' if side == 'SELL' else '+'} {hw_px} = {K}"}


def _level(rec: dict, ccy: str) -> Decimal:
    """The basis level on ``ccy``'s discount curve under the USD CSA (USD none): ``basis.eur_usd_level_bp`` / ``gbp_usd_level_bp`` /
    ``level_bp`` (the swap's EUR/USD), as a fraction."""
    if ccy == "USD":
        return D(0)
    b = rec.get("basis") or {}
    v = b.get(f"{ccy.lower()}_usd_level_bp")
    if v in (None, "") and str(b.get("pair") or "").upper() in (f"{ccy}USD", f"USD{ccy}"):
        v = b.get("level_bp")
    if v in (None, ""):
        raise RepriceError(f"the {ccy}/USD basis level is missing from the record (basis)")
    return dec(v, f"{ccy}/USD basis level") * BP


# ---- the float / float swap (K9, FX_CCS_PLAN 3.3) -------------------------------------------------------------------------------------------
def _periods(rec: dict) -> List[Tuple[date, date, date]]:
    rows = rec.get("schedule") or rec.get("periods") or []
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append((d_of(r.get("s") or r.get("start")), d_of(r.get("e") or r.get("end")), d_of(r.get("p") or r.get("pay"))))
        else:
            out.append((d_of(r[0]), d_of(r[1]), d_of(r[2])))
    if not out:
        raise RepriceError("the swap's schedule is missing from the record")
    return out


def ccs_value(cu: Curve, ce: Curve, periods, start: date, t_spot: date, X: Decimal, n_usd: Decimal, n_eur: Decimal, spread: Decimal,
              level: Decimal = D(0), bump_usd: Decimal = D(0), bump_eur: Decimal = D(0), started: bool = False) -> Tuple[Decimal, Decimal, Decimal]:
    """The full value (USD) for value ``t_spot`` (module docstring THE SWAP): ``(pv, usd_leg, eur_leg_in_eur)``, unrounded."""
    with localcontext(CTX):
        usd = D(0)
        eur = D(0)
        if not started:
            usd += -n_usd * cu.df(t_spot, start, bump_usd)
            eur += n_eur * ce.df(t_spot, start, bump_eur + level)
        for s, e, p in periods:
            if started and p <= t_spot:
                continue
            d_k = D((e - s).days)
            c_usd = n_usd * (D(1) / cu.df(s, e, bump_usd) - 1)
            c_eur = n_eur * ((D(1) / ce.df(s, e, bump_eur) - 1) + spread * d_k / D(360))
            usd += c_usd * cu.df(t_spot, p, bump_usd)
            eur -= c_eur * ce.df(t_spot, p, bump_eur + level)
        pn = periods[-1][2]
        usd += n_usd * cu.df(t_spot, pn, bump_usd)
        eur -= n_eur * ce.df(t_spot, pn, bump_eur + level)
        return usd + X * eur, usd, eur


def reprice_ccs(record: dict) -> dict:
    """The EUR/USD float / float cross-currency swap and its inception conversion re-derived from the pricing record alone (module
    docstring THE SWAP).  Returns ``{X0, n_eur_c, n_usd_c, annuity_star, pv_zero_c, final_exchange_timing_c, coupon_delays_c, par_bp,
    dealt_bp, rounding_bp, mark0_c, im_c, basis01_c, rate01_c {USD, EUR}, conversion {rate, usd_c, spread_cost_c}, coupons1 {EUR, USD}}``."""
    rec = record.get("pricing_record") if isinstance(record.get("pricing_record"), dict) else record
    X0 = dec(_first(rec, ("deal_fx", "mid"), "deal_rate", what="deal FX (deal_fx.mid)"), "deal FX")
    periods = _periods(rec)
    start = periods[0][0]
    n_eur_c = cents_of(rec.get("notionals") or {}, "eur_c") or cents_of(rec, "pay_notional_c", "n_eur_c")
    if n_eur_c is None:
        for blk in (_get(rec, "exchanges", "initial", "we_receive"), _get(rec, "exchanges", "initial", "we_pay")):
            if isinstance(blk, dict) and str(blk.get("currency") or blk.get("ccy") or "").upper() == "EUR":
                n_eur_c = cents_of(blk, "amount_c")
    if n_eur_c is None:
        legs = rec.get("legs") or {}
        for leg in (legs.values() if isinstance(legs, dict) else legs):
            if isinstance(leg, dict) and str(leg.get("currency") or "").upper() == "EUR":
                n_eur_c = cents_of(leg, "notional_c")
    if n_eur_c is None:
        raise RepriceError("the EUR notional is missing from the record (notionals.eur_c)")
    n_eur = D(int(n_eur_c)) / D(100)
    side_v = _get(rec, "basis", "eur_side") or _get(rec, "basis", "payer") or rec.get("eur_side") or default_side(rec)
    pays_eur = str(side_v).upper() in ("PAY", "PAYER", "TRUE")
    curves = rec.get("curves") or {}
    cu = curve_from_record("USD", curves.get("USD"))
    ce = curve_from_record("EUR", curves.get("EUR"))
    level = dec(_first(rec, ("basis", "level_bp"), ("basis", "eur_usd_level_bp"), what="basis level_bp"), "basis level") * BP
    hw = dec(_first(rec, ("basis", "hw_bp"), ("dealer", "xccy_basis_hw_bp"), what="basis hw_bp"), "half-width")
    mult = dec(_first(rec, ("basis", "otc_spread_mult"), ("dealer", "otc_spread_mult"), what="otc_spread_mult"), "otc_spread_mult")
    im_pct = dec(_first(rec, ("im", "pct"), ("results", "im", "pct"), what="im pct"), "im pct")
    with localcontext(CTX):
        n_usd_c = to_cents(n_eur * X0)
        n_usd = D(n_usd_c) / D(100)
        astar = qz(sum((D((e - s).days) / D(360) * ce.df(start, p, level) for s, e, p in periods), D(0)), "0.0000000001")     # K9: DFs unquantised
        pv0, _u, _e = ccs_value(cu, ce, periods, start, start, X0, n_usd, n_eur, D(0), level)
        en, pn = periods[-1][1], periods[-1][2]
        fx_delay = n_usd * (cu.df(start, pn) - cu.df(start, en)) - X0 * n_eur * (ce.df(start, pn, level) - ce.df(start, en, level))
        par = pv0 / (X0 * n_eur * astar)
        par_bp = par / BP
        if pays_eur:
            dealt_bp = (par_bp + hw * mult).quantize(D("0.1"), rounding=ROUND_CEILING)
        else:
            dealt_bp = (par_bp - hw * mult).quantize(D("0.1"), rounding=ROUND_FLOOR)
        b = dealt_bp * BP
        pvb, _u, eur_leg = ccs_value(cu, ce, periods, start, start, X0, n_usd, n_eur, b, level)
        sign = D(1) if pays_eur else D(-1)
        # the Fund's side: paying EUR (the owner's trade) as written; receiving EUR mirrors every flow
        mark0 = pvb if pays_eur else -pvb
        basis01 = ccs_value(cu, ce, periods, start, start, X0, n_usd, n_eur, b, level + BP)[0] - pvb
        r01_usd = ccs_value(cu, ce, periods, start, start, X0, n_usd, n_eur, b, level, bump_usd=BP)[0] - pvb
        r01_eur = ccs_value(cu, ce, periods, start, start, X0, n_usd, n_eur, b, level, bump_eur=BP)[0] - pvb
        s1, e1, _p1 = periods[0]
        d1 = D((e1 - s1).days)
        c1_eur = to_cents(n_eur * ((D(1) / ce.df(s1, e1) - 1) + b * d1 / D(360)))
        c1_usd = to_cents(n_usd * (D(1) / cu.df(s1, e1) - 1))
        conv_rate = X0 - pip_of("EURUSD") * D("0.2")
        conv_usd_c = to_cents(n_eur * conv_rate)
    return {"kind": "XCCY", "X0": str(X0), "n_eur_c": to_cents(n_eur), "n_usd_c": n_usd_c, "start_date": start.isoformat(),
            "maturity_date": periods[-1][2].isoformat(), "termination_date": periods[-1][1].isoformat(), "annuity_star": str(astar),
            "pv_zero_c": to_cents(pv0), "final_exchange_timing_c": to_cents(fx_delay), "coupon_delays_c": to_cents(pv0) - to_cents(fx_delay),
            "par_bp": str(qz(par_bp, "0.0001")), "par_bp_exact": str(par_bp), "dealt_bp": str(dealt_bp),
            "rounding_bp": str(qz(dealt_bp - par_bp - sign * hw * mult, "0.0001")), "mark0_c": to_cents(mark0), "im_c": to_cents(n_usd * im_pct),
            "basis01_c": to_cents(sign * basis01), "rate01_c": {"USD": to_cents(sign * r01_usd), "EUR": to_cents(sign * r01_eur)},
            "coupons1": {"EUR": c1_eur, "USD": c1_usd}, "conversion": {"rate": str(conv_rate), "usd_c": conv_usd_c, "spread_cost_c": n_usd_c - conv_usd_c},
            # before the start the clean mark is the full value: its EURUSD delta in EUR is the EUR leg's value (D14's hook; the exchanges net to 0)
            "pv_delta_c": {"EUR": to_cents(sign * eur_leg)},
            "periods": [[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in periods]}


def default_side(rec: dict) -> str:
    """The Fund's side of the EUR leg: "PAY" when the record's legs say we pay EUR (the owner's trade), else "RECEIVE"."""
    for leg in rec.get("legs") or []:
        if isinstance(leg, dict) and str(leg.get("currency") or "").upper() == "EUR":
            return "PAY" if str(leg.get("side") or leg.get("we") or "pay").lower().startswith("pay") else "RECEIVE"
    legs = rec.get("legs")
    if isinstance(legs, dict):
        if str((legs.get("pay") or {}).get("currency") or "").upper() == "EUR":
            return "PAY"
        if str((legs.get("receive") or {}).get("currency") or "").upper() == "EUR":
            return "RECEIVE"
    return "PAY"


def reprice(record: dict, kind: Optional[str] = None) -> dict:
    """K9: dispatch on the record's kind (FX_FORWARD or XCCY)."""
    rec = record.get("pricing_record") if isinstance(record.get("pricing_record"), dict) else record
    k = str(kind or record.get("kind") or rec.get("kind") or "").upper()
    if not k:
        k = "XCCY" if (rec.get("deal_fx") or rec.get("schedule")) else "FX_FORWARD"
    if k == "FX_FORWARD":
        return reprice_forward(record)
    if k == "XCCY":
        return reprice_ccs(record)
    raise RepriceError(f"reprice: unknown kind {k!r} (FX_FORWARD or XCCY)")


# ---- record field readers (the database keeps ``*_c`` cents; the HTTP codec sends them as decimal strings without ``_c``) ------------------
def cents_of(d: dict, *names: str) -> Optional[int]:
    """The first of ``names`` present in ``d`` as cents: ``name_c`` (an int, as stored) or ``name`` (a decimal string, as served)."""
    if not isinstance(d, dict):
        return None
    for n in names:
        base = n[:-2] if n.endswith("_c") else n
        v = d.get(base + "_c")
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        v = d.get(base)
        if v not in (None, "") and not isinstance(v, (dict, list, bool)):
            try:
                return to_cents(dec(v, base))
            except RepriceError:
                continue
    return None


def _same_dec(a, b) -> bool:
    try:
        return dec(a) == dec(b)
    except RepriceError:
        return False


# ---- the plan file ------------------------------------------------------------------------------------------------------------------------------
PLAN_KEYS = ("plan_id", "format", "account_id", "window_et", "max_attempts_per_run", "retry_wait_s", "quote_wait_s", "accept_replays", "trades")
RETRYABLE = ("QUOTE_STALE", "QUOTE_UNAVAILABLE", "SESSION_CLOSED", "NO_QUOTE", "SPOT_INCONSISTENT", "RATE_UNAVAILABLE", "CURVE_UNAVAILABLE", "WRITER_BUSY",
             "BUSY", "RETRY", "FETCHING")
MIN_VERSION = (1, 0, 6, 1)
REQUIRED_MODULES = ("swaps", "swaps_trs", "otc_options", "fx", "ccyfunding")
REQUIRED_PROVIDERS = ("boe", "ecb", "nyfed")


def load_plan(path: str) -> Tuple[dict, str]:
    """The plan and the sha256 of its bytes; ValueError naming the first problem."""
    with open(path, "rb") as f:
        raw = f.read()
    plan = json.loads(raw.decode("utf-8"))
    missing = [k for k in PLAN_KEYS if k not in plan]
    if missing:
        raise ValueError(f"the plan lacks {', '.join(missing)}")
    if not PLAN_ID_RE.match(str(plan["plan_id"]) + ":x"):
        raise ValueError(f"plan_id {plan['plan_id']!r} has characters a request id cannot carry")
    keys = set()
    for t in plan["trades"]:
        for k in ("key", "client_ref", "kind", "terms", "expect"):
            if k not in t:
                raise ValueError(f"trade {t.get('key')!r} lacks {k}")
        if t["key"] in keys:
            raise ValueError(f"trade key {t['key']} appears twice")
        keys.add(t["key"])
        if str((t.get("terms") or {}).get("client_ref") or "") != t["client_ref"]:
            raise ValueError(f"trade {t['key']}: terms.client_ref must equal the trade's client_ref")
    return plan, hashlib.sha256(raw).hexdigest()


def request_id(plan: dict, run_id: str, key: str, n: int, what: str) -> str:
    rid = f"{plan['plan_id']}:{run_id}:{key}:{n}:{what}"
    if not PLAN_ID_RE.match(rid):
        raise ValueError(f"request id {rid!r} breaks the id rule (8-80 characters of A-Z a-z 0-9 : _ . -)")
    return rid


# ---- HTTP (stdlib urllib) ----------------------------------------------------------------------------------------------------------------------
class TransportError(Exception):
    """No HTTP answer (connection refused / reset, timeout)."""


class Http:
    """A small JSON client: ``get(path, params)`` and ``post(path, body)`` return ``(status, body)``; a transport failure raises
    ``TransportError``.  Mutating posts carry ``request_id`` in the body (the terminal replays a committed command for the same id
    and payload)."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _do(self, req: urllib.request.Request) -> Tuple[int, object]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
            e.close()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise TransportError(f"{req.get_method()} {req.full_url}: {e}") from e
        try:
            return status, json.loads(raw.decode("utf-8") or "null")
        except ValueError:
            return status, {"raw": raw[:500].decode("utf-8", "replace")}

    def get(self, path: str, params: Optional[dict] = None) -> Tuple[int, object]:
        from urllib.parse import urlencode
        url = self.base + path + (("?" + urlencode(params)) if params else "")
        return self._do(urllib.request.Request(url, method="GET", headers={"Accept": "application/json"}))

    def post(self, path: str, body: dict) -> Tuple[int, object]:
        data = json.dumps(body).encode("utf-8")
        return self._do(urllib.request.Request(self.base + path, data=data, method="POST", headers={"Content-Type": "application/json",
                                                                                                    "Accept": "application/json"}))


def _err(body) -> Tuple[str, str]:
    e = body.get("error") if isinstance(body, dict) else None
    if isinstance(e, dict):
        return str(e.get("code") or "ERROR"), str(e.get("message") or "")
    return "ERROR", str(body)[:300]


# ---- the server and window checks (10.2 steps 2-3) ---------------------------------------------------------------------------------------------
def _version_tuple(v: str) -> Tuple[int, ...]:
    out = []
    for p in str(v or "").split("."):
        m = re.match(r"\d+", p)
        out.append(int(m.group(0)) if m else 0)
    return tuple(out)


def server_checks(health: dict) -> Tuple[List[str], List[str]]:
    """``(failures, notes)`` of 10.2 step 2 on the ``/api/health`` body."""
    fails, notes = [], []
    v = str(health.get("version") or "")
    mods = health.get("modules") or {}
    if _version_tuple(v) < MIN_VERSION:
        if all(str(mods.get(m)) == "bound" for m in REQUIRED_MODULES):
            notes.append(f"VERSION {v} is not stamped 1.0.6.1 yet (the gate stamps it); the 1.0.6.1 modules are bound, so this is the 1.0.6.1 code")
        else:
            fails.append(f"version {v or '?'}: 1.0.6.1 or later is required")
    for m in REQUIRED_MODULES:
        if str(mods.get(m)) != "bound":
            fails.append(f"module {m} is {mods.get(m) or 'missing'}")
    integ = health.get("integrity") or {}
    if integ.get("hold"):
        fails.append("an integrity hold is on")
    lc = (health.get("workers") or {}).get("lifecycle") or {}
    if not lc.get("alive"):
        fails.append("the lifecycle scheduler is not running")
    elif lc.get("stalled"):
        fails.append("the lifecycle scheduler is stalled")
    prov = health.get("providers") or {}
    for p in REQUIRED_PROVIDERS:
        row = prov.get(p)
        if not isinstance(row, dict) or row.get("ok") is not True:
            fails.append(f"provider {p}: " + ("never answered" if not isinstance(row, dict) else (row.get("last_error") or "not ok")))
    if health.get("ready") is False:
        fails.append("the server is not ready")
    return fails, notes


def server_et(health: dict) -> datetime:
    """The server's New York time from ``/api/health`` (``clock.ny`` "YYYY-MM-DD HH:MM:SS ET")."""
    ny = str(((health.get("clock") or {}).get("ny")) or "")
    try:
        return datetime.strptime(ny[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        raise ValueError(f"the server's clock is unreadable ({ny!r})") from e


def window_ok(plan: dict, et: datetime) -> Tuple[bool, str]:
    w = plan.get("window_et") or {}
    lo, hi = str(w.get("from") or "08:00"), str(w.get("to") or "16:30")
    hm = et.strftime("%H:%M")
    if w.get("fx_business_day", True) and not CalRules("USD", "EUR", "GBP").good(et.date()):
        return False, f"{et.date().isoformat()} is not an FX business day of USD, EUR and GBP"
    if not (lo <= hm <= hi):
        return False, f"{hm} ET is outside the booking window {lo}-{hi} ET"
    return True, f"{hm} ET inside {lo}-{hi} ET"


# ---- quote validation (10.2, FX-C-05) -------------------------------------------------------------------------------------------------------------
#: V1061-01: a print may be at most this much older than the quote it prices.  Yahoo's FX prints (the only FX source) arrive 168-228 s after
#: their own time (60 s grid; 24 fetches 2026-10-29 04:46-04:52 ET: 167.7-200.8 s; acceptance V: 159-220 s); the terminal fetches a pinned
#: pair every 15 s (ORDER priority) and the dealer job quotes on its next tick.  300 s is also the terminal's own fetch / staleness grace.
PRINT_MAX_AGE_S = 300
#: the two USD legs of a derived cross: their quote times at most this far apart (fxcurves ``LEG_TIME_TOL_S``; 10.2)
LEG_GAP_MAX_S = 120
#: the qualities of a print the terminal deals on (``quotes.EXECUTABLE_QUALITIES``; "close" is a valuation price, never a dealing one)
DEALING_QUALITIES = ("delayed", "live")


class Stale(str):
    """A quote-check failure caused by the market data's timing (a print too old or not executable when quoted, the legs apart, the cross
    print away from the derived cross): the quote is declined and the next attempt asks again (10.2 step 2's retryable class).  Every
    other failure is a plain ``str`` and ends the trade UNEXECUTED."""
    retryable = True


def retryable(fails) -> bool:
    """True when every failure of a quote check is a market-data timing one (``Stale``): decline and ask again."""
    return bool(fails) and isinstance(fails, (list, tuple)) and all(getattr(f, "retryable", False) for f in fails)


def _labels_ok(rec: dict, want: List[str]) -> List[str]:
    labels = [str(x) for x in (rec.get("labels") or [])]
    if isinstance(rec.get("labels"), dict):
        labels = [str(v) for v in rec["labels"].values()]
    missing = []
    for w in want:
        if not any(l == w or l.startswith(w + ":") or l.startswith(w + " ") or f" {w}" in f" {l}" for l in labels):
            missing.append(w)
    return missing


def _bd_age(cal: CalRules, d0: date, today: date) -> int:
    n, x = 0, d0
    while x < today:
        x += timedelta(days=1)
        if cal.good(x):
            n += 1
    return n


CURVE_CAL = {"GBP": ("GBP",), "EUR": ("EUR",), "USD": ("USD",)}


def input_checks(rec: dict, trade_date: date) -> List[str]:
    """Every input of a pricing record has its source and time; overnight prints at most 10 days old, curves at most 3 BD of their own
    calendar (FX_CCS_PLAN D2); the spot legs within 120 s of each other; the cross within 5.0 pips of its print (those two: ``Stale``)."""
    out = []
    for ccy, c in (rec.get("curves") or {}).items():
        if not isinstance(c, dict):
            out.append(f"curve {ccy}: missing")
            continue
        if not c.get("source"):
            out.append(f"curve {ccy}: no source")
        try:
            as_of = d_of(c.get("as_of"), f"curve {ccy} as_of")
            age = _bd_age(CalRules(*CURVE_CAL.get(ccy, ("USD",))), as_of, trade_date)
            if age > 3:
                out.append(f"curve {ccy}: as of {as_of.isoformat()}, {age} business days before the trade date (at most 3)")
        except RepriceError as e:
            out.append(str(e))
        fn = c.get("first_node") or {}
        try:
            od = d_of(fn.get("obs_date"), f"curve {ccy} overnight print date")
            if (trade_date - od).days > 10:
                out.append(f"curve {ccy}: the overnight print {fn.get('series')} of {od.isoformat()} is {(trade_date - od).days} days old (at most 10)")
        except RepriceError as e:
            out.append(str(e))
        if not fn.get("series"):
            out.append(f"curve {ccy}: the overnight print has no series")
    spot = rec.get("spot") or {}
    legs = spot.get("legs") or []
    times = []
    for leg in legs:
        if not leg.get("quote_time"):
            out.append(f"spot leg {leg.get('pair')}: no quote time")
        else:
            times.append(int(leg["quote_time"]))
        if not leg.get("source"):
            out.append(f"spot leg {leg.get('pair')}: no source")
    if len(times) == 2 and abs(times[0] - times[1]) > LEG_GAP_MAX_S:
        out.append(Stale(f"the spot legs' quote times are {abs(times[0] - times[1])} s apart (at most {LEG_GAP_MAX_S})"))
    if spot.get("diff_pips") not in (None, ""):
        try:
            if abs(dec(spot["diff_pips"])) > dec(spot.get("tolerance_pips") or "5.0"):
                out.append(Stale(f"the cross print differs from the derived cross by {spot['diff_pips']} pips (at most {spot.get('tolerance_pips') or '5.0'})"))
        except RepriceError:
            out.append("the cross difference is unreadable")
    dfx = rec.get("deal_fx") or {}
    if dfx and (not dfx.get("quote_time") or not dfx.get("source")):
        out.append("the deal FX print has no source or quote time")
    for blk in ("limits", "funding"):
        if not rec.get(blk):
            out.append(f"the {blk} block is missing")
    return out


def compare_forward(rec: dict, mine: dict) -> List[str]:
    """Exact equality at the stated quantisation (10.2 B)."""
    out = []
    dfs = rec.get("dfs") or {}
    fwd = rec.get("forward") or {}
    dealer = rec.get("dealer") or {}
    pair = mine["pair"]
    if rec.get("kind") not in (None, "", "FX_FORWARD") or str(rec.get("model_version") or "").startswith("legacy"):
        out.append(f"the record is not a curve-priced forward ({rec.get('model_version') or rec.get('kind')})")

    def eq(name, have, want):
        if have in (None, ""):
            out.append(f"{name}: missing from the record (re-derived {want})")
        elif not _same_dec(have, want):
            out.append(f"{name}: the quote says {have}, re-derived {want}")
    eq("DF*_" + pair[:3], dfs.get(pair[:3]) or dfs.get("base") or dfs.get("df_base"), mine["df_base"])
    eq("DF*_" + pair[3:], dfs.get(pair[3:]) or dfs.get("quote") or dfs.get("df_quote"), mine["df_quote"])
    eq("F", fwd.get("outright") or fwd.get("F"), mine["F"])
    eq("points", fwd.get("points"), mine["points"])
    eq("hw_px", dealer.get("hw_px"), mine["hw_px"])
    eq("K", dealer.get("K") or dealer.get("strike"), mine["K"])
    st = rec.get("settlement") or {}
    got_q = None
    for blk in (st.get("receive"), st.get("deliver")):
        if isinstance(blk, dict) and str(blk.get("ccy") or blk.get("currency") or "").upper() == pair[3:]:
            got_q = cents_of(blk, "amount_c")
    if got_q is None:
        got_q = cents_of(st, f"{pair[3:].lower()}_c", "quote_amount_c")
    if got_q != mine["quote_amount_c"]:
        out.append(f"{pair[3:]} amount: the quote says {got_q}, re-derived {mine['quote_amount_c']} (cents)")
    im = rec.get("im") or {}
    if cents_of(im, "usd_notional_c") != mine["usd_notional_c"]:
        out.append(f"USD notional: the quote says {cents_of(im, 'usd_notional_c')}, re-derived {mine['usd_notional_c']} (cents)")
    if cents_of(im, "im_c") != mine["im_c"]:
        out.append(f"IM: the quote says {cents_of(im, 'im_c')}, re-derived {mine['im_c']} (cents)")
    return out


def compare_ccs(rec: dict, mine: dict) -> List[str]:
    out = []
    b = rec.get("basis") or {}
    res = rec.get("results") or rec
    for name, have, want in (("A*", res.get("annuity_star"), mine["annuity_star"]), ("par spread bp", b.get("par_bp"), mine["par_bp"]),
                             ("dealt spread bp", b.get("dealt_bp"), mine["dealt_bp"])):
        if have in (None, ""):
            out.append(f"{name}: missing from the record (re-derived {want})")
        elif not (_same_dec(have, want) or (name == "par spread bp" and _same_dec(qz(dec(have), "0.0001"), want))):
            out.append(f"{name}: the quote says {have}, re-derived {want}")
    for name, keys, want in (("PV(0)", ("pv_zero_c",), mine["pv_zero_c"]), ("final-exchange timing", ("final_exchange_timing_c",), mine["final_exchange_timing_c"]),
                             ("coupon delays", ("coupon_delays_c",), mine["coupon_delays_c"]), ("mark0", ("mark0_c",), mine["mark0_c"])):
        have = cents_of(res, *keys)
        if have is None and isinstance(res.get("pv_zero_parts"), dict):
            have = cents_of(res["pv_zero_parts"], *keys)
        if have != want:
            out.append(f"{name}: the quote says {have}, re-derived {want} (cents)")
    im = rec.get("im") or (rec.get("results") or {}).get("im") or {}
    if cents_of(im, "im_c") != mine["im_c"]:
        out.append(f"IM: the quote says {cents_of(im, 'im_c')}, re-derived {mine['im_c']} (cents)")
    conv = rec.get("conversion") or {}
    if conv and conv.get("on", True):
        if not _same_dec(conv.get("rate"), mine["conversion"]["rate"]):
            out.append(f"conversion rate: the quote says {conv.get('rate')}, re-derived {mine['conversion']['rate']}")
        got = amount_of(conv.get("buy")) if isinstance(conv.get("buy"), dict) else cents_of(conv, "buy_c", "usd_c", "buy")
        if got is not None and got != mine["conversion"]["usd_c"]:
            out.append(f"conversion USD: the quote says {got}, re-derived {mine['conversion']['usd_c']} (cents)")
        sc = cents_of(conv, "spread_cost_c")
        if sc is not None and sc != mine["conversion"]["spread_cost_c"]:
            out.append(f"conversion spread cost: the quote says {sc}, re-derived {mine['conversion']['spread_cost_c']} (cents)")
    ex = (rec.get("exchanges") or {}).get("initial") or {}
    for blk in (ex.get("we_pay"), ex.get("we_receive")):
        if isinstance(blk, dict) and str(blk.get("currency") or "").upper() == "USD" and amount_of(blk) != mine["n_usd_c"]:
            out.append(f"USD notional: the quote says {amount_of(blk)}, re-derived {mine['n_usd_c']} (cents)")
    nu = cents_of(rec.get("notionals") or {}, "usd_c")
    if nu is not None and nu != mine["n_usd_c"]:
        out.append(f"USD notional: the record's notionals say {nu}, re-derived {mine['n_usd_c']} (cents)")
    return out


def amount_of(blk) -> Optional[int]:
    """The cents of an amount block ``{currency, amount_c}`` (stored) or ``{currency, amount}`` (served)."""
    return cents_of(blk, "amount_c") if isinstance(blk, dict) else None


def print_checks(rec: dict, quoted_ts) -> List[str]:
    """V1061-01 (module docstring FRESHNESS): every FX print the quote is priced on (a forward's spot legs, a swap's deal FX) at most
    ``PRINT_MAX_AGE_S`` older than the quote, and executable when quoted (quality delayed / live; a leg the record marks not executable
    fails too).  Each failure is ``Stale``."""
    out: List[str] = []
    legs = (rec.get("spot") or {}).get("legs") or ([rec["deal_fx"]] if isinstance(rec.get("deal_fx"), dict) else [])
    for leg in legs:
        pair = leg.get("pair") or "FX"
        qt = leg.get("quote_time")
        if quoted_ts and qt:
            age = int(quoted_ts) - int(qt)
            if age > PRINT_MAX_AGE_S:
                out.append(Stale(f"the {pair} print is {age} s older than the quote (at most {PRINT_MAX_AGE_S})"))
        q = str(leg.get("quality") or "")
        if q not in DEALING_QUALITIES or leg.get("executable") is False:
            out.append(Stale(f"the {pair} print was not executable when quoted (quality {q or 'unknown'})"))
    return out


def validate_quote(trade: dict, rfq: dict, plan: dict) -> Tuple[List[str], Optional[dict], Optional[dict]]:
    """10.2's checks before an accept: ``(failures, record, re-derived numbers)``.  A quote with any failure is declined; when every
    failure is ``Stale`` (``retryable``) the run asks again on its next attempt."""
    fails: List[str] = []
    pv = rfq.get("quote_preview") or {}
    rec = pv.get("pricing_record") if isinstance(pv.get("pricing_record"), dict) else None
    if rec is None:
        return ["the quote carries no pricing record"], None, None
    exp = trade.get("expect") or {}
    miss = _labels_ok(rec, list(exp.get("labels") or []))
    if miss:
        fails.append("labels missing: " + ", ".join(miss))
    try:
        td = d_of(rec.get("trade_date") or pv.get("trade_date"), "trade_date")
    except RepriceError as e:
        return [str(e)], rec, None
    fails += input_checks(rec, td)
    fails += print_checks(rec, (rfq.get("quote") or {}).get("quoted_ts") or rfq.get("quoted_ts"))
    kind = str(trade["kind"]).upper()
    try:
        mine = reprice(dict(rec, kind=kind), kind)
    except RepriceError as e:
        return fails + [f"re-derivation failed: {e}"], rec, None
    if kind == "FX_FORWARD":
        fails += compare_forward(rec, mine)
        if exp.get("pair") and mine["pair"] != str(exp["pair"]).upper():
            fails.append(f"pair {mine['pair']}, the plan expects {exp['pair']}")
        if exp.get("side") and mine["side"] != str(exp["side"]).upper():
            fails.append(f"side {mine['side']}, the plan expects {exp['side']}")
        if exp.get("notional_c") is not None and mine["base_amount_c"] != int(exp["notional_c"]):
            fails.append(f"notional {mine['base_amount_c']}, the plan expects {exp['notional_c']} (cents)")
        tenor = str((trade.get("terms") or {}).get("tenor") or "").upper()
        if tenor:
            try:
                want_v = tenor_date(mine["pair"], tenor, td)
                if want_v.isoformat() != mine["value_date"]:
                    fails.append(f"value date {mine['value_date']}, {tenor} from {td.isoformat()} is {want_v.isoformat()}")
                if spot_date(mine["pair"], td).isoformat() != mine["spot_date"]:
                    fails.append(f"spot date {mine['spot_date']}, re-derived {spot_date(mine['pair'], td).isoformat()}")
            except RepriceError as e:
                fails.append(str(e))
    else:
        fails += compare_ccs(rec, mine)
        if exp.get("variant") and str(rec.get("variant") or pv.get("variant") or "").upper() not in ("", str(exp["variant"]).upper()):
            fails.append(f"variant {rec.get('variant') or pv.get('variant')}, the plan expects {exp['variant']}")
        if exp.get("pay_notional_c") is not None and mine["n_eur_c"] != int(exp["pay_notional_c"]):
            fails.append(f"EUR notional {mine['n_eur_c']}, the plan expects {exp['pay_notional_c']} (cents)")
        dr = pv.get("deal_rate")
        if dr not in (None, "") and not _same_dec(dr, mine["X0"]):
            fails.append(f"the deal FX of the record {mine['X0']} differs from the quote's deal rate {dr}")
        months = int((trade.get("terms") or {}).get("months") or 0)
        if months:
            try:
                want = ccs_schedule(spot_date("EURUSD", td), months)
                if [[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in want] != mine["periods"]:
                    fails.append("the schedule differs from the one re-derived from the trade date (TARGET2 + New York, MF, +2 BD)")
            except RepriceError as e:
                fails.append(str(e))
    return fails, rec, mine


# ---- run (10.2) ---------------------------------------------------------------------------------------------------------------------------------
#: 10.2 step 6 / 10.7 "Available Treasury cash and Goldman collateral after booking" (V1061R2-02): the routes the terminal serves -
#: Accounting > Cash & financing (every account of the scope per currency, and the Treasury's settled and available cash) and the Cash &
#: Collateral summary (IM and VM per counterparty).  ``/api/cash`` and ``/api/collateral`` never existed (404), so the 1.0.6.1 dry runs'
#: reports carried both views as "unavailable" and C9 compared them vacuously.
CASH_ROUTE = "/api/accounting/cash"
COLLATERAL_ROUTE = "/api/collateral/summary"


class Runner:
    """One run of a plan against a terminal (module docstring; FX_CCS_PLAN 10.2).  ``http`` and ``sleep`` are injectable (tests)."""

    def __init__(self, plan: dict, plan_sha: str, http: Http, report_dir: str, window: Optional[str] = None, sleep: Callable[[float], None] = time.sleep,
                 out: Callable[[str], None] = print, poll_s: float = 5.0):
        self.plan, self.plan_sha, self.http, self.report_dir, self.window = plan, plan_sha, http, report_dir, window
        self.sleep, self.out, self.poll_s = sleep, out, poll_s
        self.log: List[dict] = []
        self.run_id = ""

    def note(self, what: str, **kw) -> None:
        self.log.append(dict(kw, what=what, at=int(time.time())))

    # -- lookups --
    def contracts(self) -> List[dict]:
        st, body = self.http.get("/api/otc/contracts", {"account_id": self.plan["account_id"]})
        if st != 200:
            raise RuntimeError(f"GET /api/otc/contracts answered {st}: {_err(body)}")
        rows = body if isinstance(body, list) else (body.get("contracts") or body.get("rows") or []) if isinstance(body, dict) else []
        return [r for r in rows if isinstance(r, dict)]

    @staticmethod
    def ref_of(c: dict) -> Optional[str]:
        return c.get("client_ref") or (c.get("terms") or {}).get("client_ref")

    def booked(self, ref: str) -> Optional[dict]:
        for c in self.contracts():
            if self.ref_of(c) == ref:
                return c
        return None

    def open_rfqs(self, ref: str) -> List[dict]:
        st, body = self.http.get("/api/otc/rfqs", {"account_id": self.plan["account_id"], "status": "open"})
        rows = body if isinstance(body, list) else (body.get("rows") or []) if isinstance(body, dict) else []
        return [r for r in rows if isinstance(r, dict) and ((r.get("terms") or {}).get("client_ref") == ref)]

    # -- one trade --
    def trade(self, t: dict) -> dict:
        key, ref, kind = t["key"], t["client_ref"], str(t["kind"]).upper()
        res = {"key": key, "client_ref": ref, "kind": kind, "status": "UNEXECUTED", "attempts": [], "reason": None}
        c = self.booked(ref)
        if c is not None:
            res.update(status="ALREADY_BOOKED", contract_id=c.get("contract_id"), reason=f"{ref} is already booked as {c.get('contract_id')}; nothing was sent")
            return res
        n = 1                                                   # the request number: advances only when a request created a row
        maxa = int(self.plan.get("max_attempts_per_run") or 3)
        for attempt in range(1, maxa + 1):
            a = {"attempt": attempt, "n": n}
            res["attempts"].append(a)
            terms = dict(t["terms"])
            st, pv = self.http.post("/api/otc/preview", {"account_id": self.plan["account_id"], "kind": kind, "terms": terms})
            a["preview_http"] = st
            code, msg = None, ""
            if st == 202:
                code, msg = "FETCHING", "an input is being fetched for the preview"
            elif st != 200:
                code, msg = _err(pv)
                if code not in RETRYABLE:
                    res["reason"] = f"preview refused {code}: {msg}"
                    return res
            elif isinstance(pv, dict) and (pv.get("refused") or pv.get("blockers")):
                b = (pv.get("blockers") or [{}])[0]
                code = str(b.get("code") or pv.get("code") or "BLOCKED")
                msg = str(b.get("message") or pv.get("message") or "")
                if code not in RETRYABLE:
                    res["reason"] = f"{code}: {msg}".strip()
                    return res
            if code is not None:
                a["outcome"] = f"preview {code}, retried"
                res["reason"] = f"{code}: {msg}".strip()
                self.sleep(float(self.plan.get("retry_wait_s") or 45))
                continue
            rid_rfq = request_id(self.plan, self.run_id, key, n, "rfq")
            st, body = self.http.post("/api/otc/rfqs", {"request_id": rid_rfq, "account_id": self.plan["account_id"], "kind": kind, "terms": terms})
            a["request_http"], a["rfq_request_id"] = st, rid_rfq
            if st != 200:
                code, msg = _err(body)
                a["outcome"] = f"request {code}"
                if code == "DUPLICATE_CLIENT_REF":
                    c = self.booked(ref)
                    if c is not None:
                        res.update(status="ALREADY_BOOKED", contract_id=c.get("contract_id"), reason=f"{ref} is already booked as {c.get('contract_id')}")
                        return res
                    for o in self.open_rfqs(ref):
                        self.http.post(f"/api/otc/rfqs/{o['rfq_id']}/decline", {"request_id": request_id(self.plan, self.run_id, key, n, "dec")})
                        a["declined_earlier"] = o["rfq_id"]
                    continue
                if code in RETRYABLE:
                    res["reason"] = f"{code}: {msg}"
                    self.sleep(float(self.plan.get("retry_wait_s") or 45))
                    continue
                res["reason"] = f"quote request refused {code}: {msg}"
                return res
            rfq_id = (body or {}).get("rfq_id")
            a["rfq_id"] = rfq_id
            used_n = n
            n += 1                                              # a row exists: the next request takes the next number
            rfq = self.wait_quote(rfq_id)
            status = str((rfq or {}).get("status") or "")
            a["rfq_status"] = status
            if status == "EXPIRED":
                a["outcome"] = f"the dealer did not quote: {(rfq or {}).get('reason')}"
                res["reason"] = a["outcome"]
                continue
            if status != "QUOTED":
                self.http.post(f"/api/otc/rfqs/{rfq_id}/decline", {"request_id": request_id(self.plan, self.run_id, key, used_n, "dec")})
                a["outcome"] = f"no quote within {self.plan.get('quote_wait_s')} s ({status or 'unknown'}): declined"
                res["reason"] = a["outcome"]
                continue
            fails, rec, mine = validate_quote(t, rfq, self.plan)
            a["validation"] = fails or "passed"
            if fails:
                self.http.post(f"/api/otc/rfqs/{rfq_id}/decline", {"request_id": request_id(self.plan, self.run_id, key, used_n, "dec")})
                res.update(reason="quote check failed: " + "; ".join(fails), record=rec, reprice=mine)
                if retryable(fails):                            # V1061-01: the data's timing - declined; asked again on the next attempt
                    a["outcome"] = "quote declined; asked again (market data timing): " + "; ".join(fails)
                    if attempt < maxa:
                        self.sleep(float(self.plan.get("retry_wait_s") or 45))
                    continue
                return res
            out = self.accept(t, rfq_id, used_n, a)
            if out.get("status") == "EXECUTED":
                res.update(out, record=rec, reprice=mine)
                return res
            if out.get("final"):
                res["reason"] = out.get("reason")
                return res
            res["reason"] = out.get("reason")
        return res

    def wait_quote(self, rfq_id: str) -> Optional[dict]:
        waited = 0.0
        limit = float(self.plan.get("quote_wait_s") or 150)
        while True:
            st, body = self.http.get(f"/api/otc/rfqs/{rfq_id}")
            if st == 200 and isinstance(body, dict) and body.get("status") in ("QUOTED", "EXPIRED", "DECLINED", "ACCEPTED"):
                return body
            if waited >= limit:
                return body if isinstance(body, dict) else None
            self.sleep(self.poll_s)
            waited += self.poll_s

    def accept(self, t: dict, rfq_id: str, n: int, a: dict) -> dict:
        key, ref = t["key"], t["client_ref"]
        rid = request_id(self.plan, self.run_id, key, n, "acc")
        payload = {"request_id": rid}
        tries = 1 + int(self.plan.get("accept_replays") or 2)
        lost = False
        for i in range(tries):
            try:
                st, body = self.http.post(f"/api/otc/rfqs/{rfq_id}/accept", payload)
            except TransportError as e:
                a.setdefault("accept_errors", []).append(str(e))
                lost = True
                continue
            if st >= 500:
                a.setdefault("accept_errors", []).append(f"HTTP {st}: {_err(body)}")
                lost = True
                continue
            if st == 200:
                return dict(self._executed(body), status="EXECUTED", accept_request_id=rid,
                            note=("booked; the first response was lost and the replay returned the result" if lost else "booked"))
            code, msg = _err(body)
            a["accept"] = f"{code}: {msg}"
            if code in ("QUOTE_MOVED", "QUOTE_EXPIRED"):
                st2, cur = self.http.get(f"/api/otc/rfqs/{rfq_id}")
                if st2 == 200 and isinstance(cur, dict) and cur.get("status") == "QUOTED":
                    self.http.post(f"/api/otc/rfqs/{rfq_id}/decline", {"request_id": request_id(self.plan, self.run_id, key, n, "dec")})
                return {"status": "RETRY", "reason": f"{code}: {msg}"}
            return {"status": "UNEXECUTED", "final": True, "reason": f"accept refused {code}: {msg}"}
        c = self.booked(ref)
        if c is not None:
            return {"status": "EXECUTED", "contract_id": c.get("contract_id"), "accept_request_id": rid,
                    "note": "booked; the first response was lost (found by its reference)"}
        return {"status": "RETRY", "reason": "the accept got no answer and no contract carries the reference"}

    @staticmethod
    def _executed(body: dict) -> dict:
        c = body.get("contract") or {}
        conv = None
        for k in ("conversion", "start_conversion", "linked_deals"):
            v = body.get(k) or c.get(k)
            if isinstance(v, dict) and (v.get("deal_id") or (v.get("deal") or {}).get("deal_id")):
                conv = v.get("deal_id") or v["deal"]["deal_id"]
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, dict) and (x.get("deal_id")) and "xccy_start" in json.dumps(x):
                        conv = x["deal_id"]
        return {"contract_id": body.get("contract_id") or c.get("contract_id"), "conversion_deal_id": conv, "entries": body.get("entries"),
                "booked_ts": (body.get("rfq") or {}).get("closed_ts"), "booked_ny": (body.get("rfq") or {}).get("closed_ny"), "rfq_id": body.get("rfq_id")}

    # -- the whole run --
    def run(self) -> Tuple[int, dict]:
        report = {"plan_id": self.plan["plan_id"], "plan_sha256": self.plan_sha, "account_id": self.plan["account_id"], "window_flag": self.window,
                  "trades": [], "checks": {}}
        try:
            st, health = self.http.get("/api/health")
        except TransportError as e:
            report["checks"]["server"] = {"ok": False, "failures": [str(e)]}
            return 2, report
        if st != 200 or not isinstance(health, dict):
            report["checks"]["server"] = {"ok": False, "failures": [f"/api/health answered {st}"]}
            return 2, report
        et = server_et(health)
        self.run_id = et.strftime("%Y%m%dT%H%M%S")
        report.update(run_id=self.run_id, started_et=et.strftime("%Y-%m-%d %H:%M:%S ET"), server_version=health.get("version"))
        fails, notes = server_checks(health)
        report["checks"]["server"] = {"ok": not fails, "failures": fails, "notes": notes}
        if fails:
            return 2, report
        ok, why = window_ok(self.plan, et)
        report["checks"]["window"] = {"ok": ok, "text": why, "override": self.window}
        if not ok and self.window != "any":
            return 3, report
        for t in self.plan["trades"]:
            try:
                report["trades"].append(self.trade(t))
            except TransportError as e:
                report["trades"].append({"key": t["key"], "client_ref": t["client_ref"], "status": "UNEXECUTED", "reason": f"the server stopped answering: {e}"})
            except RuntimeError as e:                       # V1061-02: e.g. the contracts list refused - nothing was sent for this trade
                report["trades"].append({"key": t["key"], "client_ref": t["client_ref"], "status": "UNEXECUTED", "reason": str(e)})
        report["views"] = self.views()
        rc = 0 if all(x["status"] in ("EXECUTED", "ALREADY_BOOKED") for x in report["trades"]) else 1
        report["exit_code"] = rc
        return rc, report

    def views(self) -> dict:
        """10.2 step 6: the contracts, the Risk FX block, the GS schedule, cash and collateral (each 'unavailable: <reason>' on failure).
        V1061R2-02: cash is Accounting > Cash & financing (``/api/accounting/cash``: every account per currency and the Treasury) and
        collateral the Cash & Collateral summary (``/api/collateral/summary``: IM and VM per counterparty) - the routes the terminal serves."""
        out = {}
        aid = self.plan["account_id"]
        lid = aid.split(":", 1)[0]
        for name, path, params in (("contracts", "/api/otc/contracts", {"account_id": aid, "detail": "1"}), ("risk", "/api/risk", {"scope": f"{lid}:ALL"}),
                                   ("risk_pb", "/api/risk", {"scope": aid}), ("funding_plan", "/api/otc/funding_plan", {"account_id": aid}),
                                   ("cash", CASH_ROUTE, {"scope": f"{lid}:ALL"}), ("collateral", COLLATERAL_ROUTE, {"scope": f"{lid}:ALL"}),
                                   ("state", "/api/state", {"book": lid[:1]})):
            try:
                st, body = self.http.get(path, params)
                out[name] = body if st == 200 else {"unavailable": f"HTTP {st}: {_err(body)}"}
            except TransportError as e:
                out[name] = {"unavailable": str(e)}
        return out


def write_report(report: dict, report_dir: str, name: str = "report") -> Tuple[str, str]:
    os.makedirs(report_dir, exist_ok=True)
    jp = os.path.join(report_dir, f"{name}.json")
    tp = os.path.join(report_dir, f"{name}.txt")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
    with open(tp, "w", encoding="utf-8") as f:
        f.write(report_text(report))
    return jp, tp


def report_text(report: dict) -> str:
    """The owner-readable summary of a run (10.7's items that a run alone can state; the orchestrator adds the NAV bridge)."""
    lines = [f"Plan {report.get('plan_id')} run {report.get('run_id') or '-'} (started {report.get('started_et') or '-'}; plan sha256 {report.get('plan_sha256')})"]
    if report.get("window_flag") == "any":
        lines.append("Booked with --window any (a dry run outside the 08:00-16:30 ET window).")
    for k, v in (report.get("checks") or {}).items():
        if k == "window":                                   # the window check has a text, no failures; --window any books outside it (a dry run)
            state = "ok" if v.get("ok") else ("outside, overridden by --window any" if v.get("override") == "any" else "FAILED")
            lines.append(f"Check window: {state}" + (f" ({v.get('text')})" if v.get("text") else ""))
            continue
        lines.append(f"Check {k}: {'ok' if v.get('ok') else 'FAILED'}" + (" - " + "; ".join(v.get("failures") or []) if v.get("failures") else "")
                     + (" (" + "; ".join(v.get("notes") or []) + ")" if v.get("notes") else ""))
    for t in report.get("trades") or []:
        lines.append(f"Trade {t.get('key')} ({t.get('client_ref')}): {t.get('status')}" + (f" - contract {t.get('contract_id')}" if t.get("contract_id") else "")
                     + (f", conversion {t.get('conversion_deal_id')}" if t.get("conversion_deal_id") else "") + (f" - {t.get('reason')}" if t.get("reason") else "")
                     + (f" ({t.get('note')})" if t.get("note") else ""))
        m = t.get("reprice") or {}
        if m.get("kind") == "FX_FORWARD":
            lines.append(f"  {m['pair']} {m['side']}: spot {m['S']}, F {m['F']} ({m['points']} pips), K {m['K']}, value {m['value_date']};"
                         f" {m['pair'][3:]} {Decimal(m['quote_amount_c']) / 100:,.2f}; IM {Decimal(m['im_c']) / 100:,.2f}; mark0 {Decimal(m['mark0_c']) / 100:,.2f}")
        elif m.get("kind") == "XCCY":
            lines.append(f"  EUR/USD float / float: deal FX {m['X0']}, EUR {Decimal(m['n_eur_c']) / 100:,.2f} / USD {Decimal(m['n_usd_c']) / 100:,.2f}; par"
                         f" {m['par_bp']} bp, dealt {m['dealt_bp']} bp; mark0 {Decimal(m['mark0_c']) / 100:,.2f}; IM {Decimal(m['im_c']) / 100:,.2f};"
                         f" conversion {m['conversion']['rate']} = USD {Decimal(m['conversion']['usd_c']) / 100:,.2f}")
    lines += cash_collateral_lines(report.get("views") or {}, report.get("account_id"))
    lines.append("Notional limits: none configured in the Goldman Sachs schedule; compliance has no OTC limit.")
    lines.append(f"Exit code {report.get('exit_code')}.")
    return "\n".join(lines) + "\n"


def _amount_text(v) -> str:
    """A served decimal string as 1,234,567.89; "n/a" when absent or not a number."""
    try:
        return f"{dec(v):,.2f}"
    except (RepriceError, ArithmeticError, ValueError):
        return "n/a"


def _cents_text(d: dict, name: str) -> str:
    """``d``'s money field ``name`` - cents under ``name_c`` (as stored) or a decimal string under ``name`` (as served) - as 1,234.56."""
    c = cents_of(d, name)
    return "n/a" if c is None else f"{Decimal(c) / 100:,.2f}"


def cash_collateral_lines(views: dict, account_id: Optional[str] = None) -> List[str]:
    """10.7 "Available Treasury cash and Goldman collateral after booking" (V1061R2-02), from the run's cash and collateral views: the
    Treasury's settled cash per currency and its available USD; the plan account's prime broker - IM posted against the IM required, VM
    posted and VM received.  A view that could not be read is said unavailable with its reason, never left out."""
    out: List[str] = []
    cash = (views or {}).get("cash")
    if isinstance(cash, dict) and cash.get("unavailable") is None and isinstance(cash.get("treasury"), list):
        for t in cash["treasury"]:
            if not isinstance(t, dict):
                continue
            per = t.get("settled_by_ccy") or {}
            settled = ", ".join(f"{c} {_amount_text(v)}" for c, v in sorted(per.items(), key=lambda kv: (kv[0] != "USD", kv[0]))) or "none"
            out.append(f"Treasury {t.get('account_id')} after the run: settled {settled}"
                       + (f"; available USD {_cents_text(t, 'available_c')}" if cents_of(t, "available_c") is not None else "") + ".")
    elif cash is not None:
        out.append(f"Treasury cash after the run: unavailable ({cash.get('unavailable') if isinstance(cash, dict) else 'not read'}).")
    col = (views or {}).get("collateral")
    if isinstance(col, dict) and col.get("unavailable") is None and isinstance(col.get("by_counterparty"), list):
        mine = [g for g in col["by_counterparty"] if isinstance(g, dict) and g.get("type") == "CSA"
                and (account_id is None or g.get("account_id") == account_id or account_id in (g.get("accounts") or []))]
        for g in mine:
            out.append(f"{g.get('counterparty')} collateral after the run ({', '.join(g.get('accounts') or [g.get('account_id') or ''])}): IM posted"
                       f" {_cents_text(g, 'im_posted_c')} (required {_cents_text(g, 'im_required_c')}); VM posted {_cents_text(g, 'vm_posted_c')};"
                       f" VM received {_cents_text(g, 'vm_received_c')}.")
        if not mine:
            out.append(f"Prime-broker collateral after the run: no CSA counterparty line for {account_id or 'the plan account'}.")
    elif col is not None:
        out.append(f"Prime-broker collateral after the run: unavailable ({col.get('unavailable') if isinstance(col, dict) else 'not read'}).")
    return out


# ---- snapshot and check (10.3; read-only, sqlite3 mode=ro) --------------------------------------------------------------------------------------
SNAP_TABLES = ("otc_contracts", "otc_vm", "otc_resets", "fx_deals")
CONTRACT_JOB_COLS = ("mark_c", "mark_ts", "vm_balance_c", "inputs_json", "confirm_status", "confirmed_ts")


def ro_connect(path: str) -> sqlite3.Connection:
    p = os.path.abspath(path).replace("\\", "/")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql: str, params=()) -> List[dict]:
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql, params).fetchall()]


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(conn, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def entry_digest(conn, entry_id: int) -> str:
    e = conn.execute("SELECT * FROM journal_entries WHERE entry_id=?", (entry_id,)).fetchone()
    lines = conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,)).fetchall()
    h = hashlib.sha256(json.dumps([[e[k] for k in e.keys()]] + [[ln[k] for k in ln.keys()] for ln in lines], default=str).encode("utf-8"))
    return h.hexdigest()[:24]


def snapshot(db_path: str, lifetime: str = "F1", broker_account: str = "F1:PB_GS") -> dict:
    """10.3 ``snapshot``: the maximum ids, every row of the OTC and FX tables, the lifetime's settlements, the GS schedule, a digest per
    journal entry, the state_version and the time."""
    conn = ro_connect(db_path)
    try:
        snap = {"db": os.path.abspath(db_path), "taken_ts": int(time.time()), "lifetime": lifetime, "broker_account": broker_account,
                "max": {"commands": conn.execute("SELECT COALESCE(MAX(id),0) FROM commands").fetchone()[0],
                        "journal_entries": conn.execute("SELECT COALESCE(MAX(entry_id),0) FROM journal_entries").fetchone()[0],
                        "events": conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]},
                "tables": {t: (_rows(conn, f"SELECT * FROM {t}") if _has(conn, t) else []) for t in SNAP_TABLES},
                "settlements": _rows(conn, "SELECT * FROM settlements WHERE account_id LIKE ?", (lifetime + ":%",)),
                "schedule": _rows(conn, "SELECT * FROM pb_schedules WHERE account_id=?", (broker_account,)),
                "digests": {str(r[0]): entry_digest(conn, int(r[0])) for r in conn.execute("SELECT entry_id FROM journal_entries ORDER BY entry_id").fetchall()}}
        try:
            snap["state_version"] = conn.execute("SELECT value FROM meta WHERE key='state_version'").fetchone()[0]
        except Exception:   # noqa: BLE001 - an older database: no state_version row
            snap["state_version"] = None
        return snap
    finally:
        conn.close()


class Checker:
    """10.3 ``check``: C1-C11 on a database after a run (read-only).  ``result()`` -> ``{items: {C1: {ok, evidence}}, ok}``."""

    def __init__(self, db_path: str, before: dict, run: dict, second: Optional[dict] = None, after_restart: Optional[dict] = None):
        self.conn = ro_connect(db_path)
        self.before, self.run, self.second, self.after_restart = before, run, second, after_restart
        self.items: Dict[str, dict] = {}
        self.prefix = f"{run.get('plan_id')}:{run.get('run_id')}"
        self.untouched = set(((run.get("plan") or {}).get("untouched") or {}).get("contracts") or []) or {
            r["contract_id"] for r in before["tables"]["otc_contracts"]}
        self.refs = {t["client_ref"]: t for t in run.get("trades") or []}

    def close(self) -> None:
        self.conn.close()

    def item(self, cid: str, ok: bool, evidence) -> None:
        self.items[cid] = {"ok": bool(ok), "evidence": evidence}

    # -- the new rows of the run --
    def new_contracts(self) -> Dict[str, dict]:
        cols = _cols(self.conn, "otc_contracts")
        out = {}
        if "client_ref" not in cols:
            return out
        for r in _rows(self.conn, "SELECT * FROM otc_contracts WHERE client_ref IS NOT NULL"):
            if r["client_ref"] in self.refs:
                out[r["contract_id"]] = r
        return out

    def classify(self) -> Dict[int, int]:
        """Every command after the snapshot -> its class (module docstring; 10.3 C1)."""
        new = self.new_contracts()
        new_ids = set(new)
        deals = {r["deal_id"] for r in _rows(self.conn, "SELECT deal_id, inputs_json FROM fx_deals") if any(c in (r["inputs_json"] or "") for c in new_ids)}
        if _has(self.conn, "rfqs"):                # the dealer job's quotes and expiries of the run's requests are the run's lifecycle too
            deals |= {r["rfq_id"] for r in _rows(self.conn, "SELECT rfq_id, terms_json FROM rfqs") if any(ref in (r["terms_json"] or "") for ref in self.refs)}
        out = {}
        for c in _rows(self.conn, "SELECT id, request_id, kind, actor FROM commands WHERE id>? ORDER BY id", (self.before["max"]["commands"],)):
            rid = str(c["request_id"] or "")
            if rid.startswith(self.prefix + ":"):
                out[c["id"]] = 1
            elif c["actor"] == "lifecycle" and any(x and x in rid for x in new_ids | deals):
                out[c["id"]] = 2
            elif c["actor"] == "lifecycle" and any(x in rid for x in self.untouched):
                out[c["id"]] = 3
            else:
                out[c["id"]] = 4
        return out

    def c1(self, cls: Dict[int, int]) -> None:
        ev, bad = [], []
        before_c = {r["contract_id"]: r for r in self.before["tables"]["otc_contracts"]}
        now_c = {r["contract_id"]: r for r in _rows(self.conn, "SELECT * FROM otc_contracts")}
        for cid, b in before_c.items():
            a = now_c.get(cid)
            if a is None:
                bad.append(f"{cid} disappeared")
                continue
            changed = [k for k in b if k in a and str(b[k]) != str(a[k])]
            off = [k for k in changed if k not in CONTRACT_JOB_COLS]
            if off:
                bad.append(f"{cid}: columns {', '.join(off)} changed (only its own jobs may change {', '.join(CONTRACT_JOB_COLS)})")
            elif changed:
                own = [i for i, k in cls.items() if k == 3 and cid in str(self._rid(i))]
                if not own:
                    bad.append(f"{cid}: {', '.join(changed)} changed without a job of its own after the snapshot")
                else:
                    ev.append(f"{cid}: {', '.join(changed)} moved by its own lifecycle")
        for t in ("otc_vm", "otc_resets"):
            keyf = ("contract_id", "vm_date") if t == "otc_vm" else ("contract_id", "event_date", "kind")
            bmap = {tuple(r[k] for k in keyf): r for r in self.before["tables"][t]}
            for r in _rows(self.conn, f"SELECT * FROM {t}"):
                k = tuple(r[x] for x in keyf)
                if k in bmap:
                    if {x: str(v) for x, v in bmap[k].items()} != {x: str(v) for x, v in r.items()}:
                        bad.append(f"{t} {k}: an existing row changed")
                    continue
                if r["contract_id"] in before_c:
                    cmd = self._entry_cmd(r.get("entry_id"))
                    if cmd is None or cls.get(cmd) != 3:
                        bad.append(f"{t} {k}: a new row of {r['contract_id']} not written by its own lifecycle (command {cmd}, class {cls.get(cmd)})")
        maxe = int(self.before["max"]["journal_entries"])
        changed_e = [eid for eid, dg in self.before["digests"].items() if int(eid) <= maxe and (
            self.conn.execute("SELECT 1 FROM journal_entries WHERE entry_id=?", (int(eid),)).fetchone() is None or entry_digest(self.conn, int(eid)) != dg)]
        if changed_e:
            bad.append(f"journal entries altered after the snapshot: {', '.join(changed_e[:10])}")
        for i, k in cls.items():
            if k != 4:
                continue
            for e in _rows(self.conn, "SELECT entry_id, ref_type, ref_id FROM journal_entries WHERE command_id=?", (i,)):
                if e["ref_type"] == "otc" and e["ref_id"] in set(self.new_contracts()) | set(before_c):
                    bad.append(f"command {i} ({self._rid(i)}), not the run's nor a contract's own job, posted entry {e['entry_id']} on {e['ref_id']}")
        other = [f"{i} {self._rid(i)}" for i, k in cls.items() if k == 4]
        self.item("C1", not bad, {"problems": bad, "notes": ev[:20], "other_activity": other[:50],
                                  "classes": {str(n): sum(1 for v in cls.values() if v == n) for n in (1, 2, 3, 4)}})

    def _rid(self, cmd_id: int) -> str:
        r = self.conn.execute("SELECT request_id FROM commands WHERE id=?", (cmd_id,)).fetchone()
        return "" if r is None else str(r[0])

    def _entry_cmd(self, entry_id) -> Optional[int]:
        if not entry_id:
            return None
        r = self.conn.execute("SELECT command_id FROM journal_entries WHERE entry_id=?", (int(entry_id),)).fetchone()
        return None if r is None else int(r[0])

    def c2(self, cls: Dict[int, int]) -> None:
        bad, ev = [], []
        new = self.new_contracts()
        executed = [t for t in self.run.get("trades") or [] if t.get("status") in ("EXECUTED", "ALREADY_BOOKED")]
        by_ref: Dict[str, List[str]] = {}
        for cid, r in new.items():
            by_ref.setdefault(r["client_ref"], []).append(cid)
        for t in executed:
            got = by_ref.get(t["client_ref"], [])
            if len(got) != 1:
                bad.append(f"{t['client_ref']}: {len(got)} contracts carry it (exactly one expected)")
                continue
            cid = got[0]
            ev.append(f"{t['client_ref']} -> {cid}")
            row = new[cid]
            if row["kind"] == "XCCY":
                conv = [d for d in _rows(self.conn, "SELECT * FROM fx_deals WHERE kind='SPOT'") if _link(d).get("type") == "xccy_start" and _link(d).get("id") == cid]
                if len(conv) != 1:
                    bad.append(f"{cid}: {len(conv)} inception conversions linked (exactly one expected)")
                else:
                    d = conv[0]
                    srows = _rows(self.conn, "SELECT * FROM settlements WHERE fx_deal_id=?", (d["deal_id"],))
                    tagged = [s for s in srows if s.get("ref_type") == "xccy_start" and s.get("ref_id") == cid]
                    if len(srows) != 2 or (len(tagged) != 2 and row["status"] == "OPEN" and d["status"] == "PENDING"):
                        bad.append(f"{cid}: the conversion {d['deal_id']} has {len(srows)} settlement rows, {len(tagged)} tagged xccy_start")
                    if not d.get("entry_id"):
                        bad.append(f"{cid}: the conversion {d['deal_id']} has no FX entry")
                    ev.append(f"{cid}: conversion {d['deal_id']} SELL {d['sell_ccy']} {d['sell_c']} / BUY {d['buy_ccy']} {d['buy_c']} at {d['rate']}")
            im = self.conn.execute("SELECT COUNT(*) FROM journal_entries WHERE kind='OTC_IM' AND ref_type='otc' AND ref_id=?", (cid,)).fetchone()[0]
            if im < 1:
                bad.append(f"{cid}: no IM entry")
            if _has(self.conn, "rfqs"):
                rq = _rows(self.conn, "SELECT rfq_id, status, command_id FROM rfqs WHERE contract_id=?", (cid,))
                if len([x for x in rq if x["status"] == "ACCEPTED"]) != 1:
                    bad.append(f"{cid}: {len(rq)} accepted quote requests (exactly one expected)")
        # nothing else from classes 1 and 2: their contracts are the new ones, their deals linked to them
        for i, k in cls.items():
            if k not in (1, 2):
                continue
            for r in _rows(self.conn, "SELECT contract_id FROM otc_contracts WHERE command_id=?", (i,)):
                if r["contract_id"] not in new:
                    bad.append(f"command {i} ({self._rid(i)}) created {r['contract_id']}, which carries no reference of the plan")
            for d in _rows(self.conn, "SELECT deal_id, inputs_json FROM fx_deals WHERE command_id=?", (i,)):
                if not any(c in (d["inputs_json"] or "") for c in new):
                    bad.append(f"command {i} ({self._rid(i)}) dealt {d['deal_id']}, which serves none of the new contracts")
            sched = self.conn.execute("SELECT COUNT(*) FROM events WHERE command_id=? AND type='SETTINGS_CHANGED'", (i,)).fetchone()[0]
            if sched:
                bad.append(f"command {i} ({self._rid(i)}) of the run changed settings")
        self.item("C2", not bad, {"problems": bad, "found": ev})

    def c3_c4(self) -> None:
        bad3, bad4, ev = [], [], []
        new = self.new_contracts()
        for cid, row in new.items():
            terms = _jload(row.get("terms_json"))
            rec = terms.get("pricing_record") if isinstance(terms.get("pricing_record"), dict) else None
            if rec is None:
                bad3.append(f"{cid}: no pricing record in its terms")
                bad4.append(f"{cid}: no pricing record in its terms")
                continue
            t = next((x for x in self.run.get("plan", {}).get("trades", []) if x["client_ref"] == row["client_ref"]), None) or {
                "kind": row["kind"], "terms": {}, "expect": {}, "client_ref": row["client_ref"]}
            try:
                mine = reprice(dict(rec, kind=row["kind"]), row["kind"])
            except RepriceError as e:
                bad3.append(f"{cid}: re-derivation failed: {e}")
                continue
            bad3 += [f"{cid}: {x}" for x in (compare_forward(rec, mine) if row["kind"] == "FX_FORWARD" else compare_ccs(rec, mine))]
            if row["kind"] == "FX_FORWARD":
                k_terms = terms.get("strike")
                if k_terms is not None and not _same_dec(k_terms, mine["K"]):
                    bad3.append(f"{cid}: the contract's strike {k_terms} is not the quoted K {mine['K']}")
                if str(row["maturity_date"])[:10] != mine["value_date"]:
                    bad3.append(f"{cid}: maturity {row['maturity_date']} is not the value date {mine['value_date']}")
                try:
                    td = d_of(rec.get("trade_date"))
                    tenor = str((t.get("terms") or {}).get("tenor") or "")
                    if tenor and tenor_date(mine["pair"], tenor, td).isoformat() != mine["value_date"]:
                        bad3.append(f"{cid}: the value date {mine['value_date']} is not {tenor} from {td}")
                except RepriceError as e:
                    bad3.append(f"{cid}: {e}")
            else:
                for name, want in (("deal_rate", mine["X0"]),):
                    if terms.get(name) is not None and not _same_dec(terms[name], want):
                        bad3.append(f"{cid}: terms {name} {terms[name]} is not the quoted {want}")
                sp = terms.get("pay_spread_bp") if terms.get("pay_spread_bp") is not None else (terms.get("spread") or {}).get("dealt_bp")
                if sp is not None and not _same_dec(sp, mine["dealt_bp"]):
                    bad3.append(f"{cid}: the booked EUR spread {sp} bp is not the quoted {mine['dealt_bp']} bp")
                if str(row["maturity_date"])[:10] != mine["maturity_date"]:
                    bad3.append(f"{cid}: maturity {row['maturity_date']} is not the last payment {mine['maturity_date']}")
                try:
                    td = d_of(rec.get("trade_date"))
                    want = ccs_schedule(spot_date("EURUSD", td), int(terms.get("months") or (len(mine["periods"]) * 3)))
                    if [[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in want] != mine["periods"]:
                        bad3.append(f"{cid}: the schedule is not the one re-derived from {td}")
                except RepriceError as e:
                    bad3.append(f"{cid}: {e}")
            ev.append(f"{cid}: re-derived " + (f"F {mine['F']} K {mine['K']}" if row["kind"] == "FX_FORWARD" else f"par {mine['par_bp']} dealt {mine['dealt_bp']} mark0 {mine['mark0_c']}"))
            # C4: complete, labelled, and every input equal to its stored row
            try:
                bad4 += [f"{cid}: {x}" for x in input_checks(rec, d_of(rec.get("trade_date")))]
            except RepriceError as e:
                bad4.append(f"{cid}: {e}")
            if not rec.get("labels"):
                bad4.append(f"{cid}: no labels")
            bad4 += [f"{cid}: {x}" for x in self.stored_inputs(rec)]
            bad4 += [f"{cid}: {x}" for x in self.schedule_problems(rec, row["kind"])]
        self.item("C3", not bad3, {"problems": bad3, "found": ev})
        self.item("C4", not bad4, {"problems": bad4})

    def schedule_terms(self) -> Tuple[Dict[str, str], str]:
        """The GS schedule in force at the quote: the snapshot's stored ``pb_schedules`` row (a field an older row lacks takes the GS seed of
        FX_CCS_PLAN 1.2 / D5) - ``(terms, note)``; a SETTINGS_CHANGED after the snapshot is named (C11 lists it)."""
        rows = self.before.get("schedule") or []
        stored = _jload(rows[0].get("schedule_json")) if rows else {}
        terms = {k: str(stored[k]) if k in stored and stored[k] is not None else v for k, v in GS_SEEDS.items()}
        changed = self.conn.execute("SELECT COUNT(*) FROM events WHERE type='SETTINGS_CHANGED' AND id>? AND (ref_id=? OR account_id=?)",
                                    (self.before["max"]["events"], self.before.get("broker_account") or "F1:PB_GS",
                                     self.before.get("broker_account") or "F1:PB_GS")).fetchone()[0]
        note = "" if not changed else f"{changed} schedule change(s) after the snapshot: compared with the snapshot's terms (C11 lists the changes)"
        return terms, note

    def schedule_problems(self, rec: dict, kind: str) -> List[str]:
        terms, note = self.schedule_terms()
        out = []

        def eq(what, have, key):
            if have in (None, ""):
                out.append(f"{what}: missing from the record (the schedule says {terms[key]})")
            elif not _same_dec(have, terms[key]):
                out.append(f"{what} {have} differs from the Goldman Sachs schedule's {key} {terms[key]}" + (f" ({note})" if note else ""))
        b = rec.get("basis") or {}
        if kind == "FX_FORWARD":
            d = rec.get("dealer") or {}
            eq("the forward half-width", d.get("forward_hw_bp"), "forward_hw_bp")
            eq("the OTC spread multiplier", d.get("otc_spread_mult"), "otc_spread_mult")
            eq("the EUR/USD basis level", b.get("eur_usd_level_bp"), "xccy_basis_eur_bp")
            eq("the GBP/USD basis level", b.get("gbp_usd_level_bp"), "xccy_basis_gbp_bp")
            eq("the forward IM percentage", (rec.get("im") or {}).get("pct"), "forward_im_pct")
        else:
            eq("the EUR/USD basis level", b.get("level_bp"), "xccy_basis_eur_bp")
            eq("the cross-currency half-width", b.get("hw_bp"), "xccy_basis_hw_bp")
            eq("the OTC spread multiplier", b.get("otc_spread_mult"), "otc_spread_mult")
            eq("the cross-currency IM percentage", ((rec.get("results") or {}).get("im") or rec.get("im") or {}).get("pct"), "xccy_im_pct")
        eur = (rec.get("curves") or {}).get("EUR") or {}
        if eur:
            eq("the EUR OIS spread", eur.get("eur_ois_spread_bp") if eur.get("eur_ois_spread_bp") is not None else eur.get("spread_bp"), "eur_ois_spread_bp")
        return out

    def stored_inputs(self, rec: dict) -> List[str]:
        """C4: the overnight prints and the curve grid nodes equal the stored ``curve_points`` rows of their as-of dates."""
        out = []
        if not _has(self.conn, "curve_points"):
            return ["no curve_points table: the stored inputs cannot be compared"]
        for ccy, c in (rec.get("curves") or {}).items():
            fn = (c or {}).get("first_node") or {}
            if fn.get("series") and fn.get("obs_date"):
                rows = _rows(self.conn, "SELECT value FROM curve_points WHERE series=? AND as_of=?", (fn["series"], fn["obs_date"]))
                if not rows:
                    out.append(f"curve {ccy}: no stored {fn['series']} print for {fn['obs_date']}")
                elif not any(_same_dec(r["value"], fn.get("value_pct")) for r in rows):
                    out.append(f"curve {ccy}: {fn['series']} {fn['obs_date']} is {rows[0]['value']} stored, {fn.get('value_pct')} in the record")
            sid = (c or {}).get("series") or (c or {}).get("curve_id")
            grid = (c or {}).get("grid") or {}
            if sid and c.get("as_of") and isinstance(grid.get("dfs"), dict) and grid.get("dfs"):
                # a bootstrapped curve (USD: the Treasury par row -> semi-annual DFs -> zero rates): its row must be stored, and every node
                # must be the zero rate of its DF (z = -ln(DF) / t); the par yields themselves are not zero rates and are not compared
                if not _rows(self.conn, "SELECT 1 FROM curve_points WHERE series=? AND as_of=? LIMIT 1", (sid, c["as_of"])):
                    out.append(f"curve {ccy}: no stored {sid} row for {c['as_of']}")
                with localcontext(CTX):
                    for n in c.get("nodes") or []:
                        if not isinstance(n, dict) or str(n.get("tenor") or "").upper() == "ON":
                            continue
                        t = dec(n.get("t"), "node t")
                        df = next((v for k, v in grid["dfs"].items() if dec(k) == t), None)
                        if df is None:
                            out.append(f"curve {ccy} {sid}: no DF for the node at t = {n.get('t')}")
                            continue
                        z = -(dec(df).ln()) / t * 100
                        if abs(z - dec(n.get("z"))) > D("1e-20"):
                            out.append(f"curve {ccy} {sid} {n.get('tenor')}: the node {n.get('z')} is not -ln({df}) / {t} = {z}")
                continue
            if sid and c.get("as_of"):
                stored = {str(r["tenor"]).upper(): r["value"] for r in _rows(self.conn, "SELECT tenor, value FROM curve_points WHERE series=? AND as_of=?",
                                                                             (sid, c["as_of"]))}
                if not stored:
                    out.append(f"curve {ccy}: no stored {sid} row for {c['as_of']}")
                for n in c.get("nodes") or []:
                    ten = str(n.get("tenor") or "").upper() if isinstance(n, dict) else ""
                    if not ten and isinstance(n, dict) and n.get("months") is not None:
                        ten = f"{int(n['months'])}M"
                    if ten and ten in stored and not _same_dec(stored[ten], n.get("value_pct") if n.get("value_pct") is not None else n.get("z")):
                        out.append(f"curve {ccy} {sid} {ten}: stored {stored[ten]}, the record {n.get('value_pct') or n.get('z')}")
        return out

    def c5(self) -> None:
        bad, other = [], []
        lid = self.before.get("lifetime") or "F1"
        for aid in (f"{lid}:PB_GS", f"{lid}:TREASURY"):
            for ccy in ("USD", "EUR", "GBP"):
                v = self.conn.execute("SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1010' AND currency=?", (aid, ccy)).fetchone()[0]
                if int(v) < 0:
                    bad.append(f"{aid} settled {ccy} is {int(v) / 100:,.2f}")
        t0 = int(self.before["taken_ts"])
        for a in _rows(self.conn, "SELECT * FROM attention WHERE code IN ('FUNDING_SHORTFALL','SETTLEMENT_UNFUNDED') AND ts>=?", (t0,)):
            if a.get("account_id") in (f"{lid}:PB_GS", f"{lid}:TREASURY"):
                bad.append(f"{a['code']} on {a['account_id']}: {a['message'][:160]}")
            else:
                other.append(f"{a['code']} on {a.get('account_id')}")
        self.item("C5", not bad, {"problems": bad, "other_accounts": other})

    def c6(self) -> None:
        bad = []
        for r in _rows(self.conn, "SELECT currency, SUM(amount_c) s FROM journal_lines GROUP BY currency HAVING SUM(amount_c) <> 0"):
            bad.append(f"the trial balance does not balance in {r['currency']} ({r['s']})")
        s = self.conn.execute("SELECT COALESCE(SUM(base_c),0) FROM journal_lines").fetchone()[0]
        if int(s) != 0:
            bad.append(f"the trial balance does not balance in base ({s})")
        for c in _rows(self.conn, "SELECT contract_id, account_id, im_c, margin_mode FROM otc_contracts WHERE status='OPEN'"):
            held = self.conn.execute("SELECT COALESCE(SUM(amount_c),0) FROM journal_lines WHERE account_id=? AND gl='1330' AND instrument_id=?",
                                     (c["account_id"], c["contract_id"])).fetchone()[0]
            if int(held) != int(c["im_c"] or 0):
                bad.append(f"{c['contract_id']}: 1330 holds {held}, im_c {c['im_c']}")
        last = self.conn.execute("SELECT status, note FROM jobs_done WHERE job='INTEGRITY_FULL' ORDER BY run_ts DESC LIMIT 1").fetchone()
        note = None if last is None else f"{last['status']} {last['note']}"
        if last is not None and "ok" not in str(last["note"] or "").lower():
            bad.append(f"the last full integrity check: {note}")
        self.item("C6", not bad, {"problems": bad, "last_integrity_full": note})

    def c7(self) -> None:
        """The Risk FX block of the run report: EUR nominal -4,000,000.00 with both trades, GBP + to_c(A x K); each currency's PV delta =
        the lifetime's ledger balances + the re-derived off-ledger deltas of the new contracts (the swap only before its start), +/- 0.01."""
        bad, ev = [], []
        new = self.new_contracts()
        lid = self.before.get("lifetime") or "F1"
        risk = ((self.run.get("views") or {}).get("risk") or {})
        block = risk.get("fx_sensitivities") if isinstance(risk, dict) else None
        live = sorted(cid for cid, r in new.items() if r.get("status") == "OPEN" and r.get("kind") in ("XCCY", "FX_FORWARD"))
        if not live:                                        # V1061-02: nothing booked, nothing to measure (by_currency is legitimately [])
            self.item("C7", True, {"problems": [], "note": "not applicable: the run booked no FX contract (no open contract carries a plan reference)",
                                   "by_currency": block.get("by_currency") if isinstance(block, dict) else None})
            return
        if not isinstance(block, dict) or not block.get("by_currency"):
            self.item("C7", False, {"problems": ["the run report carries no Risk FX block (fx_sensitivities.by_currency) although the run booked "
                                                 + ", ".join(live)]})
            return
        rows = {str(r.get("ccy")): r for r in block["by_currency"] if isinstance(r, dict)}
        hooks: Dict[str, int] = {}
        exact = True
        k_gbp = None
        for cid, row in new.items():
            terms = _jload(row.get("terms_json"))
            rec = terms.get("pricing_record") if isinstance(terms.get("pricing_record"), dict) else None
            if rec is None or row["status"] != "OPEN":
                continue
            try:
                mine = reprice(dict(rec, kind=row["kind"]), row["kind"])
            except RepriceError as e:
                bad.append(f"{cid}: {e}")
                continue
            if row["kind"] == "XCCY" and self.conn.execute("SELECT 1 FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_START'", (cid,)).fetchone():
                exact = False                     # after the start the hook is the clean mark's delta at the valuation time: not re-derived here
                continue
            if row["kind"] == "FX_FORWARD":
                k_gbp = mine["quote_amount_c"]
            for c, v in (mine.get("pv_delta_c") or {}).items():
                hooks[c] = hooks.get(c, 0) + int(v)
        eur = rows.get("EUR") or {}
        n_eur = cents_of(eur, "nominal_c", "nominal")
        if {r["kind"] for r in new.values()} >= {"XCCY", "FX_FORWARD"} and n_eur != -400000000:
            bad.append(f"EUR nominal {n_eur} (cents), -4,000,000.00 expected (the swap and the forward)")
        if k_gbp is not None:
            g = cents_of(rows.get("GBP") or {}, "nominal_c", "nominal")
            if g != k_gbp:
                bad.append(f"GBP nominal {g} (cents), the forward's {k_gbp} expected")
        for ccy in sorted(hooks):
            ledger = int(self.conn.execute("SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id LIKE ? AND currency=? AND (gl LIKE '1%' OR gl LIKE '2%')",
                                           (lid + ":%", ccy)).fetchone()[0])
            want = ledger + hooks[ccy]
            got = cents_of(rows.get(ccy) or {}, "pv_delta_c", "pv_delta")
            ev.append(f"{ccy} PV delta: the Risk block {got}, ledger {ledger} + re-derived hooks {hooks[ccy]} = {want} (cents)")
            if exact and (got is None or abs(int(got) - want) > 1):
                bad.append(f"{ccy} PV delta {got} (cents) differs from the ledger {ledger} + the re-derived off-ledger deltas {hooks[ccy]} = {want} by more than 0.01")
        self.item("C7", not bad, {"problems": bad, "found": ev, "exact": exact})

    def c8(self) -> None:
        if self.second is None:
            self.item("C8", True, {"note": "no second run given (--second): not checked"})
            return
        bad = [f"{t['key']}: {t['status']} on the second run" for t in self.second.get("trades") or [] if t.get("status") != "ALREADY_BOOKED"]
        seen = {t.get("key") for t in self.second.get("trades") or []}
        for t in self.run.get("trades") or []:
            if t.get("status") in ("EXECUTED", "ALREADY_BOOKED") and t.get("key") not in seen:
                bad.append(f"{t.get('key')}: booked by the run, absent from the second run's report (exit {self.second.get('exit_code')})")
        pfx = f"{self.second.get('plan_id')}:{self.second.get('run_id')}:"
        n = self.conn.execute("SELECT COUNT(*) FROM commands WHERE request_id LIKE ?", (pfx + "%",)).fetchone()[0]
        if n:
            bad.append(f"{n} commands carry the second run's prefix {pfx}")
        self.item("C8", not bad, {"problems": bad})

    def c9(self) -> None:
        if self.after_restart is None:
            self.item("C9", True, {"note": "no restart views given (--after-restart): not checked"})
            return
        raw_b, raw_a = self.after_restart.get("before") or {}, self.after_restart.get("after") or {}
        before, after = stable_views(raw_b), stable_views(raw_a)
        bad, compared = [], []
        # V1061R2-02: the contracts, the positions, the cash and the collateral - each compared on its stored facts; a view that could not
        # be read on either side is a problem, never two equal "unavailable" answers (the dry runs' cash and collateral views were 404s)
        for k in RESTART_VIEWS:
            if k not in raw_b and k not in raw_a:
                continue
            gone = [f"{side} the restart ({v['unavailable']})" for side, v in (("before", before.get(k)), ("after", after.get(k)))
                    if isinstance(v, dict) and "unavailable" in v]
            if gone:
                bad.append(f"{k} could not be read {' and '.join(gone)}: not compared")
                continue
            compared.append(k)
            b, a = before.get(k), after.get(k)
            if json.dumps(b, sort_keys=True, default=str) != json.dumps(a, sort_keys=True, default=str):
                bad.append(restart_difference(k, b, a))
        self.item("C9", not bad, {"problems": bad, "views": compared, "not_compared": sorted((set(raw_b) | set(raw_a)) - set(RESTART_VIEWS)),
                                  "compared": "the stored facts of each contract and position, the ledger cash per account and currency, the Treasury's"
                                              " settled cash and each prime broker's IM and VM (not the live marks and the available cash, which are"
                                              " re-valued)"})

    def c10(self) -> None:
        bad = []
        new = self.new_contracts()
        for cid, row in new.items():
            if "dealer-confirmed" in str(row.get("terms_json") or "").lower():
                bad.append(f"{cid}: says dealer-confirmed")
        txt = json.dumps(self.run, default=str).lower()
        if "dealer-confirmed" in txt:
            bad.append("the run report says dealer-confirmed")
        self.item("C10", not bad, {"problems": bad})

    def c11(self, cls: Dict[int, int]) -> None:
        listed, bad = [], []
        for e in _rows(self.conn, "SELECT id, command_id, ref_type, ref_id, payload_json FROM events WHERE type='SETTINGS_CHANGED' AND id>?", (self.before["max"]["events"],)):
            k = cls.get(int(e["command_id"]))
            listed.append(f"event {e['id']} ({e['ref_type']} {e['ref_id']}) by command {e['command_id']} class {k}")
            if k == 1:
                bad.append(f"the run changed settings: event {e['id']} ({e['ref_type']} {e['ref_id']})")
        self.item("C11", not bad, {"problems": bad, "settings_changed": listed})

    def result(self) -> dict:
        cls = self.classify()
        for fn in (lambda: self.c1(cls), lambda: self.c2(cls), self.c3_c4, self.c5, self.c6, self.c7, self.c8, self.c9, self.c10, lambda: self.c11(cls)):
            try:
                fn()
            except Exception as e:   # noqa: BLE001 - a check that cannot run fails with its reason
                name = getattr(fn, "__name__", "check")
                self.items.setdefault(f"ERROR:{name}", {"ok": False, "evidence": f"{type(e).__name__}: {e}"})
        return {"items": self.items, "ok": all(v["ok"] for v in self.items.values())}


#: the Goldman Sachs seeds of the 1.0.6.1 terms (FX_CCS_PLAN 1.2 / D5): what an older stored schedule row that lacks a field is worth
GS_SEEDS = {"forward_hw_bp": "2.0", "otc_spread_mult": "1.0", "xccy_basis_eur_bp": "0.0", "xccy_basis_gbp_bp": "0.0", "xccy_basis_hw_bp": "1.0",
            "eur_ois_spread_bp": "0.0", "forward_im_pct": "0.06", "xccy_im_pct": "0.02"}
#: C9: what a restart must keep (the stored facts); live valuation fields (marks, qualities, ages) are re-computed and not compared
STABLE_KEYS = ("contract_id", "kind", "status", "client_ref", "account_id", "broker", "notional", "notional_c", "im", "im_c", "currency", "start_date",
               "maturity_date", "trade_date", "value_date", "margin_mode", "terms", "instrument_id", "qty", "quantity", "cost", "cost_c", "side")


#: C9 (V1061R2-02): the views a stop and start must serve the same (the other views - the funding plan, the state - are live)
RESTART_VIEWS = ("contracts", "risk", "risk_pb", "cash", "collateral")
#: C9 (V1061R2-02): the cash view's stored facts - the ledger balances per account and currency (Accounting.cash: settled 1010, receivable,
#: payable, held at a lender, margin, accrued, OTC margin) and the Treasury's settled cash per currency; available and reserved cash are
#: re-computed live (working orders, today's prices) and not compared
CASH_FACTS = ("settled", "receivable", "payable", "held_at_lender", "margin", "accrued", "otc_margin")
#: C9 (V1061R2-02): the collateral view's stored facts - each prime broker's (CSA) IM posted (1330) and required (its open contracts' IM)
#: and its VM posted and received
CSA_FACTS = ("im_posted", "im_required", "vm_posted", "vm_received")


def _cents_or_none(v) -> Optional[int]:
    try:
        return to_cents(dec(v))
    except (RepriceError, ArithmeticError, ValueError):
        return None


def _cash_facts(view: dict) -> List[dict]:
    rows = [dict({"part": "account", "account_id": r.get("account_id"), "currency": r.get("currency")}, **{k: cents_of(r, k + "_c") for k in CASH_FACTS})
            for r in view.get("cash") or [] if isinstance(r, dict)]
    rows += [{"part": "treasury", "account_id": t.get("account_id"),
              "settled_by_ccy": {str(c): _cents_or_none(v) for c, v in (t.get("settled_by_ccy") or {}).items()}}
             for t in view.get("treasury") or [] if isinstance(t, dict)]
    return sorted(rows, key=lambda r: (r["part"], str(r.get("account_id") or ""), str(r.get("currency") or "")))


def _csa_facts(view: dict) -> List[dict]:
    rows = [dict({"counterparty": g.get("counterparty"), "account_id": g.get("account_id")}, **{k: cents_of(g, k + "_c") for k in CSA_FACTS})
            for g in view.get("by_counterparty") or [] if isinstance(g, dict) and g.get("type") == "CSA"]
    return sorted(rows, key=lambda r: (str(r.get("counterparty") or ""), str(r.get("account_id") or "")))


def stable_views(views: dict) -> dict:
    """C9: what a restart must keep of each view - the stored facts of the contracts and positions (``STABLE_KEYS``), of the cash
    (``CASH_FACTS`` per account and currency, the Treasury's settled cash) and of the collateral (``CSA_FACTS`` per prime broker), in cents;
    a view that could not be read stays ``{"unavailable": reason}``."""
    out = {}
    for name, rows in (views or {}).items():
        if isinstance(rows, dict) and rows.get("unavailable") is not None:
            out[name] = {"unavailable": str(rows["unavailable"])}
            continue
        if name == "cash" and isinstance(rows, dict) and isinstance(rows.get("cash"), list):
            out[name] = _cash_facts(rows)
            continue
        if name == "collateral" and isinstance(rows, dict) and isinstance(rows.get("by_counterparty"), list):
            out[name] = _csa_facts(rows)
            continue
        rows = rows.get("contracts") or rows.get("rows") or rows.get("positions") if isinstance(rows, dict) else rows
        if not isinstance(rows, list):
            out[name] = rows
            continue
        out[name] = sorted(({k: r.get(k) for k in STABLE_KEYS if k in r} for r in rows if isinstance(r, dict)),
                           key=lambda r: (str(r.get("contract_id") or ""), str(r.get("instrument_id") or "")))
    return out


def _row_label(r) -> str:
    return " ".join(str(r[k]) for k in ("part", "contract_id", "instrument_id", "counterparty", "account_id", "currency") if isinstance(r, dict) and r.get(k))


def restart_difference(name: str, b, a) -> str:
    """C9's words for a view whose stored facts differ after the restart: the fields and the rows, or the row counts."""
    if isinstance(b, list) and isinstance(a, list) and len(b) == len(a):
        pairs = [(x, y) for x, y in zip(b, a) if isinstance(x, dict) and isinstance(y, dict) and x != y]
        fields = sorted({f for x, y in pairs for f in set(x) | set(y) if x.get(f) != y.get(f)})
        if fields:
            where = "; ".join(_row_label(x) for x, _y in pairs[:3])
            return f"{name} differs after the restart in {', '.join(fields[:8])}" + (f" ({where})" if where else "")
    nb = len(b) if isinstance(b, list) else 0
    na = len(a) if isinstance(a, list) else 0
    return f"{name} differs after the restart ({nb} rows before, {na} after)"


def _jload(s) -> dict:
    try:
        v = json.loads(s or "{}")
    except (TypeError, ValueError):
        v = {}
    return v if isinstance(v, dict) else {}


def _link(d: dict) -> dict:
    return _jload(d.get("inputs_json")).get("link") or {}



# ---- the command line -----------------------------------------------------------------------------------------------------------------------
class InputError(Exception):
    """V1061-02: an input a command cannot use - a missing or unreadable file, a file that is not JSON, a database that cannot be opened
    read-only.  ``main`` prints it as one line and exits 2 (never a traceback)."""


def read_json(path: str, what: str):
    """The JSON of an input file, or ``InputError`` naming the option (``what``), the path and the reason."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise InputError(f"cannot read {what} {path}: {e.strerror or e}") from e
    except ValueError as e:
        raise InputError(f"{what} {path} is not a JSON file: {e}") from e


def require_db(path: str, what: str = "--db") -> str:
    """A database file that exists and opens read-only (``mode=ro``), or ``InputError``."""
    if not os.path.isfile(path):
        raise InputError(f"cannot read {what} {path}: no such file")
    try:
        ro_connect(path).close()
    except sqlite3.Error as e:
        raise InputError(f"cannot open {what} {path} read-only: {e}") from e
    return path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG, description="The owner's OTC booking program (FX_CCS_PLAN section 10): run a plan file through the RFQ desk,"
                                 " snapshot and check a database read-only, re-derive a pricing record.")
    sub = ap.add_subparsers(dest="cmd", metavar="command")
    r = sub.add_parser("run", help="book the plan's trades through the RFQ desk of a running terminal (exit 0 all executed or already booked, 1 any"
                       " unexecuted, 2 server check failed, 3 outside the booking window)")
    r.add_argument("plan")
    r.add_argument("--base-url", required=True)
    r.add_argument("--report", required=True, help="folder for the run report (JSON and text)")
    r.add_argument("--window", default=None, choices=["any"], help="dry runs only: book outside 08:00-16:30 ET (the report says so)")
    s = sub.add_parser("snapshot", help="record a database's state before a run (read-only, sqlite3 mode=ro)")
    s.add_argument("--db", required=True)
    s.add_argument("--out", required=True)
    c = sub.add_parser("check", help="check a database after a run against its snapshot (read-only, sqlite3 mode=ro); exit 0 only when every item passes")
    c.add_argument("--db", required=True)
    c.add_argument("--before", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--second", default=None)
    c.add_argument("--after-restart", default=None)
    p = sub.add_parser("reprice", help="re-derive a pricing record (JSON file) with the independent pricer and print the numbers")
    p.add_argument("record")
    p.add_argument("--kind", default=None, choices=["FX_FORWARD", "XCCY"])
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.cmd:
        build_parser().print_help()
        return 2
    try:
        return _main(args)
    except InputError as e:
        print(f"otcplan {args.cmd}: {e}")
        return 2
    except sqlite3.Error as e:                              # a database that stops reading mid-way: said in one line
        print(f"otcplan {args.cmd}: the database could not be read ({e})")
        return 2


def _main(args) -> int:
    if args.cmd == "reprice":
        rec = read_json(args.record, "the record")
        try:
            out = reprice(rec, args.kind)
        except ValueError as e:                             # RepriceError: a record that cannot be re-derived
            print(f"otcplan reprice: {e}")
            return 1
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "run":
        try:
            plan, sha = load_plan(args.plan)
        except OSError as e:
            raise InputError(f"cannot read the plan {args.plan}: {e.strerror or e}") from e
        except ValueError as e:
            raise InputError(f"the plan {args.plan} cannot be used: {e}") from e
        runner = Runner(plan, sha, Http(args.base_url), args.report, args.window)
        rc, report = runner.run()
        report["plan"] = plan
        report["exit_code"] = rc
        jp, tp = write_report(report, args.report)
        print(report_text(report), end="")
        print(f"report: {jp}")
        return rc
    if args.cmd == "snapshot":
        snap = snapshot(require_db(args.db))
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, default=str)
        print(f"snapshot of {args.db}: commands <= {snap['max']['commands']}, entries <= {snap['max']['journal_entries']}, events <= {snap['max']['events']} -> {args.out}")
        return 0
    if args.cmd == "check":
        def _load(p, what):
            return read_json(p, what) if p else None
        inputs = (_load(args.before, "--before"), _load(args.run, "--run"), _load(args.second, "--second"), _load(args.after_restart, "--after-restart"))
        chk = Checker(require_db(args.db), *inputs)
        try:
            res = chk.result()
        finally:
            chk.close()
        for k in sorted(res["items"], key=lambda x: (len(x), x)):
            v = res["items"][k]
            print(f"{k}: {'PASS' if v['ok'] else 'FAIL'}")
            ev = v.get("evidence")
            if isinstance(ev, dict):
                for p in ev.get("problems") or []:
                    print(f"  - {p}")
        print("PASS" if res["ok"] else "FAIL")
        return 0 if res["ok"] else 1
    return 2


__all__ = ["main", "build_parser", "InputError", "read_json", "require_db", "print_checks", "retryable", "Stale", "PRINT_MAX_AGE_S", "LEG_GAP_MAX_S",
           "DEALING_QUALITIES", "reprice", "reprice_forward", "reprice_ccs", "RepriceError", "Curve", "curve_from_record", "CalRules", "spot_date",
           "tenor_date", "ccs_schedule", "ccs_value", "on_node_z", "load_plan", "request_id", "Http", "TransportError", "Runner", "server_checks", "window_ok",
           "validate_quote", "input_checks", "compare_forward", "compare_ccs", "snapshot", "Checker", "write_report", "report_text", "cents_of",
           "CASH_ROUTE", "COLLATERAL_ROUTE", "cash_collateral_lines", "stable_views", "RESTART_VIEWS", "restart_difference"]
