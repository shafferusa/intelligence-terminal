"""FX desk: spot deals (USD pairs and crosses), outright forwards, their settlement, currency funding and conversion, foreign
venue taxes and currency positions (TERMINAL_DESIGN.md §2.12 fx.py, §5.4, §11.2 SETTLE_DUE, §13.5, §13.9, §19.6; WP-05;
Build 2 P06: OA-08 / OA-09 / OA-11 / OA-12 / OA-14 / OA-15 / OA-24, contracts C1 / C5 / C17 / C25).

Conventions stated here (the §1.3 sentences that bite in this module)
- FX_SPOT: a deal is done at the mid plus or minus 0.2 pip AGAINST the user (pip 0.0001; JPY-quoted pairs 0.01) and
  settles T+2 US business days (USD/CAD and USD/TRY T+1, ``instruments.fx_spot_days``).  A pair follows the market
  convention (``EURUSD`` = USD per EUR; ``USDJPY`` = JPY per USD, Yahoo ``JPY=X``; a cross ``EURJPY`` = JPY per EUR, the
  currency earlier in ``instruments.CCY_ORDER`` first).  Side rule in the quoted convention: buying the BASE pays
  ``mid + spread`` quote units per base unit, selling the base receives ``mid - spread``.
- USD pairs: the ledger carries ONE rate per foreign currency, USD per 1 unit, at most 8 decimals (``units.qprice``).  The deal
  stores ``rate`` = USD per unit of the foreign currency (for a USD-quoted pair the inverse of the dealt rate quantised to 8
  decimals; ``rate_quoted`` keeps the market convention) and the USD amount is ALWAYS ``fx_c(foreign_c, rate)``, so the two
  legs are consistent to the cent and the FX clearing account 3900 nets to zero in base exactly (integrity L5).  A typed USD
  amount gives ``foreign = to_c(USD / rate)`` and the USD leg is recomputed from it (``usd_adjusted_c``, at most a cent).
- CROSSES (OA-14; Build 2): dealt directly, one deal, one rate.  The mid is the cross's own Yahoo print (``EURJPY=X``) when it
  is executable, else DERIVED from the two USD pairs (``EURJPY = EURUSD x USDJPY``, both prints executable, said on the
  ticket).  Ledger: the leg with more minor units (the JPY leg of EURJPY) is booked at its USD mid (8 dp), U = fx_c(amount,
  rate); the other leg is booked at the deal-implied USD rate so its base is exactly U (``fit_base``: one 8-dp rate, or - for
  a leg so large that one 8-dp step moves it by more than a cent - the leg in two lines at two rates).  3900 therefore nets
  to zero in base exactly; the spread (and any cross / USD-pair basis) shows in ``fx_result`` when the second leg is
  revalued at its own mid.
- ``spread_cost_c`` = the USD value of the spread: for a USD pair the USD amount at the deal rate minus the USD amount of
  the same foreign amount at the mid; for a cross the computed leg's difference from its amount at the mid, valued at that
  currency's USD mid.  It appears inside ``fx_result`` on the deal date (§6.4).
- Entry (§5.4): buy leg ``Dr 1250:buy / Cr 3900:buy``, sell leg ``Dr 3900:sell / Cr 2350:sell``; foreign lines carry their
  ``fx_rate``; two ``settlements`` rows (``BUY_CCY`` +, ``SELL_CCY`` -) due on the value date; one ``fx_deals`` row.
- SETTLEMENT_UNFUNDABLE (OA-11): a spot sale of a currency the account cannot deliver by the value date - its own available
  then (settled + receivables due by then - payables - reserved) plus what Treasury (and PB financing for USD) can sweep -
  is refused 409 with the shortfall and the fix ("buy EUR first on the FX desk, value on or before ..." / "transfer EUR from
  Treasury").  A FORWARD sale (value date after the spot date) is allowed with that warning: the currency can still be
  bought spot before the value date; a leg still unfunded at the value date waits (Attention SETTLEMENT_UNFUNDED).
- FORWARDS (OA-09; §13.9): ``fx.forward`` deals an outright at the covered-interest-parity rate
  ``F = S x (1 + r_quote x d / B_quote) / (1 + r_base x d / B_base)`` (``forward_rate``; d = calendar days from the SPOT date
  to the value date; B = 360, GBP 365) +/- the disclosed forward spread (0.2 pip + 0.1 pip per month of tenor, assumed),
  labelled computed with every input (spot print, each currency's rate series, value and as-of).  Rates: USD = SOFR (FRED,
  or the NY Fed while FRED is in maintenance; the Treasury par-curve bill yield when no SOFR is observed), every other
  currency its ``tables.POLICY_INDEX`` series (JPY / CHF / CAD / AUD: monthly OECD proxies, labelled LIMITED with their
  month); a currency without a series, or a series never observed, gives NO forward price (never a default).  Tenors ON
  (spot-next), 1W, 1M, 2M, 3M, 6M, 9M, 1Y (months modified-following from the spot date, end-of-month rule) and any broken
  business day after spot up to the 1Y date.  No cash until the value date: the deal is an ``fx_deals`` row (kind FORWARD,
  ``entry_id`` 0); its mark-to-market ``(F_now - K) x amount x DF`` is computed with its inputs (``forward_mtm``, shown on
  the desk and in Currency positions; ``forward_overlays`` is the NAV overlay hook).  On the value date (from 00:30 ET)
  the job FX_FORWARD_DUE posts the deal entry at K and its two settlement rows; SETTLE_DUE settles them the same tick (the
  P&L is realised then, through ``fx_result``).
- CONVERSION (C17): ``conversion_plan`` (pure) prices the USD -> currency spot a non-USD need would take; ``convert_for``
  books it as a linked spot deal in the account (value date <= the need's settle date, else 409 FX_VALUE_DATE) - never a
  silent conversion: the order preview shows it and the client asks for it (``fx_convert``).  Its USD side is funded by the
  caller's cascade (``execution.fund``), measured before the deal's USD payable.
- VENUE TAXES (C25): ``venue_fee_c`` - UK stamp duty reserve tax 0.50 % on purchases, Hong Kong stamp duty 0.10 % on each
  side (rounded up to the whole HKD), French FTT 0.40 % on purchases of French companies, Italian FTT 0.10 % on purchases;
  ETFs exempt; others 0 - a table labelled "assumed - verify current rates".
- ``fx.settle`` (the SETTLE_DUE job, §11.2) settles both legs of a deal on or after the value date: the receivable leg
  ``Dr 1010 / Cr 1250``; the payable leg ``Dr 2350 / Cr 1010`` after ``execution.fund`` (the account's settled cash in that
  currency, else exactly the shortfall swept from Treasury's cash in that currency).  A payable that cannot be funded stays
  PENDING with one open Attention ERROR ``SETTLEMENT_UNFUNDED`` and is retried on the next tick; nothing is invented.
- ``fund_ccy`` (cross-currency swap notionals): the account's settled cash in the currency, else Treasury's cash in that
  currency as one linked sweep, else 409 ``INSUFFICIENT_FUNDS_CCY`` with the hint "buy <ccy> <amount> with Convert".
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Dict, List, Optional, Tuple

from . import tables
from .books import account_kind_for, account_row, lifetime_row
from .clock import accounting_day, epoch_of, ny_of
from .commands import ConflictError, RejectError, TxContext, require_amount_c
from .db import dumps, next_id
from .execution import FundingResult, auto_resolve_shortfall, fund
from .instruments import CCY_NAMES, CCY_ORDER, FX_CROSSES, fx_cross_id, fx_pair_id, fx_spot_days, venue_suffix
from .ledger import Entry, balance, cr, dr
from .marks import cip_forward
from .orders_preview import quote_of
from .quotes import EXECUTABLE_QUALITIES, QUALITY_RANK, is_executable
from .units import fmt_money, from_c, fx_c, qprice, to_c

SESSION_GROUP = "FX_SPOT"
SETTLE_DAYS = 2
_FEE = tables.FEES["DEFAULT"]["FX"]
STALE_CODES = {"STALE": "QUOTE_STALE", "NO_QUOTE": "QUOTE_UNAVAILABLE", "SESSION_CLOSED": "SESSION_CLOSED",
               "QUOTE_FROM_PREVIOUS_SESSION": "QUOTE_STALE", "PROVIDER_SESSION_ENDED": "QUOTE_STALE",
               "COMPUTED_NOT_EXECUTABLE": "QUOTE_NOT_EXECUTABLE", "NO_NBBO": "QUOTE_NOT_EXECUTABLE"}
STEP = Decimal("0.00000001")                 # one step of an 8-dp ledger rate
MIN_DEAL_C = 100                             # the smallest deal: 1.00 of the typed currency
TENORS = ("ON", "1W", "1M", "2M", "3M", "6M", "9M", "1Y")
TENOR_MONTHS = {"1M": 1, "2M": 2, "3M": 3, "6M": 6, "9M": 9, "1Y": 12}
TENOR_LABEL = {"ON": "ON (spot-next)", "1W": "1 week", "1M": "1 month", "2M": "2 months", "3M": "3 months", "6M": "6 months", "9M": "9 months", "1Y": "1 year"}
#: the forward deal spread (assumed, disclosed): the spot 0.2 pip plus 0.1 pip per month of tenor, against the user
FWD_SPREAD_PIPS_PER_MONTH = Decimal("0.1")
FWD_SPREAD_TEXT = "forward spread (assumed): 0.2 pip + 0.1 pip per month of tenor, against you"
FWD_DUE_ET = "00:30"                         # the value date's forward posting runs with SETTLE_DUE (from 00:30 ET)
MAJOR_PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD")
BUY_SIDES = ("BUY", "BUY_TO_COVER", "BTO", "BTC")
#: C25 foreign venue taxes (assumed - verify current rates): suffix -> rule
VENUE_TAX_NOTE = "assumed - verify current rates"
VENUE_TAXES = {
    "L": {"label": "UK stamp duty reserve tax (SDRT)", "rate": Decimal("0.005"), "purchases_only": True, "round": "cent",
          "text": "0.50 % of the consideration on purchases of UK shares (ETFs exempt), to the penny"},
    "HK": {"label": "Hong Kong stamp duty", "rate": Decimal("0.001"), "purchases_only": False, "round": "unit_up",
           "text": "0.10 % of the consideration on each side (buyer and seller; ETFs exempt), rounded up to the whole HKD"},
    "PA": {"label": "French financial transaction tax", "rate": Decimal("0.004"), "purchases_only": True, "round": "cent",
           "text": "0.40 % on purchases of shares of French companies with a market value above EUR 1 billion (ETFs exempt)"},
    "MI": {"label": "Italian financial transaction tax", "rate": Decimal("0.001"), "purchases_only": True, "round": "cent",
           "text": "0.10 % on purchases of shares of Italian companies on a regulated market (ETFs exempt)"},
}
#: listings on those venues that the tax does not cover (not incorporated there)
VENUE_TAX_EXEMPT = {"AIR.PA": "Airbus SE is incorporated in the Netherlands (not on the French FTT list)",
                    "STMPA.PA": "STMicroelectronics N.V. is incorporated in the Netherlands", "STLAP.PA": "Stellantis N.V. is incorporated in the Netherlands"}


# ---- currencies and pairs (pure) -----------------------------------------------------------------------------------------
def _ccy(x) -> str:
    c = str(x or "").upper()
    if len(c) != 3 or not c.isalpha():
        raise RejectError("CURRENCY", f"currency must be a 3-letter code, got {x!r}")
    return c


def pair_info(buy_ccy: str, sell_ccy: str) -> dict:
    """The pair of a deal between two currencies: ``{pair, base, quote, cross, foreign, usd_quoted, buying_base}``
    (``foreign`` / ``usd_quoted`` only for a USD pair).  400 SAME_CURRENCY when both are the same."""
    b, s = _ccy(buy_ccy), _ccy(sell_ccy)
    if b == s:
        raise RejectError("SAME_CURRENCY", "buy and sell currencies are the same")
    pair = fx_cross_id(b, s)
    base, quote = pair[:3], pair[3:]
    cross = "USD" not in (b, s)
    foreign = None if cross else (b if s == "USD" else s)
    return {"pair": pair, "base": base, "quote": quote, "cross": cross, "foreign": foreign, "usd_quoted": base == "USD", "buying_base": b == base,
            "buy_ccy": b, "sell_ccy": s}


def pair_for(buy_ccy: str, sell_ccy: str) -> Tuple[str, Optional[str], bool]:
    """``(pair_id, foreign_ccy, usd_quoted)``; a cross gives ``(pair, None, False)`` (dealt directly since Build 2)."""
    p = pair_info(buy_ccy, sell_ccy)
    return p["pair"], p["foreign"], p["usd_quoted"]


def pip_of(pair: str) -> Decimal:
    """0.0001, or 0.01 for JPY-quoted pairs (sentence FX_SPOT)."""
    return _FEE["pip_jpy"] if "JPY" in pair.upper() else _FEE["pip"]


def dealt_rate(mid_quoted: Decimal, pair: str, buying_base: bool, spread_pips: Optional[Decimal] = None) -> Decimal:
    """The dealt rate in the pair's own convention: mid + spread when the user buys the base, mid - spread when it sells it."""
    spread = pip_of(pair) * (spread_pips if spread_pips is not None else _FEE["spread_pips"])
    mid = qprice(mid_quoted)
    return qprice(mid + spread if buying_base else mid - spread)


def deal_rate(mid_quoted: Decimal, pair: str, buy_ccy: str, foreign: str, spread_pips: Optional[Decimal] = None) -> Tuple[Decimal, Decimal]:
    """USD pairs: ``(rate_quoted, rate_usd_per_unit)``: the dealt rate in the pair's own convention (mid -/+ the spread
    against the user) and the same rate as USD per 1 unit of the foreign currency, 8 decimals, which the ledger uses."""
    mid = qprice(mid_quoted)
    if mid <= 0:
        raise ConflictError("QUOTE_UNAVAILABLE", f"{pair}: the mid {mid} is not a usable rate")
    usd_quoted = pair.upper().startswith("USD")
    buying_base = (_ccy(buy_ccy) == "USD") if usd_quoted else (_ccy(buy_ccy) == foreign)
    quoted = dealt_rate(mid, pair, buying_base, spread_pips)
    rate = qprice(Decimal(1) / quoted) if usd_quoted else qprice(quoted)
    return qprice(quoted), rate


def deal_amounts(rate: Decimal, amount_c: int, amount_ccy: str, foreign: str) -> Tuple[int, int, int]:
    """``(foreign_c, usd_c, usd_adjusted_c)``: the USD amount is always ``fx_c(foreign_c, rate)`` (see the module
    docstring); a typed USD amount can therefore move by one cent (``usd_adjusted_c`` = deal USD - typed USD)."""
    amount_c = int(amount_c)
    if amount_c <= 0:
        raise RejectError("AMOUNT", "amount must be positive")
    if _ccy(amount_ccy) == foreign:
        foreign_c = amount_c
        return foreign_c, fx_c(foreign_c, rate), 0
    if _ccy(amount_ccy) != "USD":
        raise RejectError("AMOUNT_CCY", f"amount_ccy must be {foreign} or USD")
    foreign_c = to_c(from_c(amount_c) / rate)
    if foreign_c <= 0:
        raise RejectError("AMOUNT", f"USD {fmt_money(amount_c)} is less than one cent of {foreign} at {rate}")
    usd_c = fx_c(foreign_c, rate)
    return foreign_c, usd_c, usd_c - amount_c


def mid_usd_per_unit(mid_quoted: Decimal, usd_quoted: bool) -> Decimal:
    return qprice(Decimal(1) / qprice(mid_quoted)) if usd_quoted else qprice(mid_quoted)


def fit_base(amount_c: int, target_c: int) -> List[Tuple[int, Decimal]]:
    """The ledger pieces ``[(amount_c, rate)]`` of one leg whose bases ``fx_c(piece, rate)`` add up to EXACTLY ``target_c``
    (the other leg's USD base), with 8-dp rates near ``target / amount``.  One piece when an 8-dp rate hits the cent; else (a
    leg so large that one rate step moves its base by more than a cent) the leg in two pieces: the bulk at the 8-dp rate just
    below the target rate and at most 990,000.00 units at a rate searched so that its base takes exactly the rest (one rate
    step moves that piece by less than a cent, so the search always lands).  Worked: EUR 25,123,456.78 against U =
    27,203,668.51 USD: no 8-dp rate hits the cent (1.08279958 gives 27,203,668.45, the next 27,203,668.70: an 8-dp step moves
    it 0.25), so 24,133,456.78 @ 1.08279958 = 26,131,696.87 and 990,000.00 @ 1.08279964 = 1,071,971.64 -> 27,203,668.51."""
    amount_c, target_c = int(amount_c), int(target_c)
    if amount_c <= 0 or target_c <= 0:
        raise RejectError("AMOUNT", f"a deal leg must be positive (amount {amount_c}, base {target_c})")
    r0 = qprice(Decimal(target_c) / Decimal(amount_c))
    for k in (0, 1, -1, 2, -2, 3, -3):
        r = r0 + STEP * k
        if r > 0 and fx_c(amount_c, r) == target_c:
            return [(amount_c, r)]
    small = min(amount_c // 2, 99_000_000)
    if small <= 0:
        raise RejectError("AMOUNT", "the deal is too small to book at an exact USD value")
    big = amount_c - small
    r_t = Decimal(target_c) / Decimal(amount_c)
    r1 = ((Decimal(target_c) - Decimal(small) * r_t) / Decimal(big)).quantize(STEP, rounding=ROUND_FLOOR)
    rest = target_c - fx_c(big, r1)
    if r1 <= 0 or rest <= 0:
        raise RejectError("AMOUNT", "the deal cannot be booked at an exact USD value (rate rounding)")
    r2_0 = qprice(Decimal(rest) / Decimal(small))
    for k in range(0, 50):
        for r2 in ((r2_0 + STEP * k), (r2_0 - STEP * k)):
            if r2 > 0 and fx_c(small, r2) == rest:
                return [(big, r1), (small, r2)]
    raise RejectError("AMOUNT", "the deal cannot be booked at an exact USD value (rate rounding)")


# ---- dates -----------------------------------------------------------------------------------------------------------------
def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + int(months)
    y, m = d.year + m // 12, m % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def fx_trade_date(services, now: int) -> date:
    """The FX trade date: the FX_SPOT session's own (the FX day rolls at 17:00 ET), else the next session's."""
    try:
        st = services.sessions.state(SESSION_GROUP, int(now))
        if st.trade_date:
            return date.fromisoformat(st.trade_date)
    except Exception:   # noqa: BLE001 - a stand-in without sessions: the accounting day
        pass
    return accounting_day(datetime.fromtimestamp(int(now), timezone.utc))


def spot_date(services, pair: str, trade_date: date) -> date:
    return services.cal.settlement_date(trade_date, fx_spot_days(pair))


def tenor_date(services, pair: str, tenor: str, trade_date: date) -> date:
    """A tenor's value date from the spot date: ON = spot + 1 business day (spot-next); 1W = spot + 7 days, following;
    months = spot + n months, modified following, end-of-month rule (spot on its month's last business day -> the last
    business day of the target month).  US business days (NYSE calendar; the currencies' own holidays are not modelled)."""
    tenor = str(tenor or "").upper()
    cal = services.cal
    sd = spot_date(services, pair, trade_date)
    if tenor == "ON":
        return cal.add_business_days(sd, 1)
    if tenor == "1W":
        return cal.roll_following(sd + timedelta(days=7))
    months = TENOR_MONTHS.get(tenor)
    if months is None:
        raise RejectError("TENOR", f"tenor {tenor!r}: use one of {', '.join(TENORS)} or a value date")
    target = _add_months(sd, months)
    if sd == cal.month_end(sd):
        return cal.month_end(target)
    f = cal.roll_following(target)
    return f if f.month == target.month else cal.roll_back(target)


def tenor_months(tenor: Optional[str], days: int) -> Decimal:
    if tenor in TENOR_MONTHS:
        return Decimal(TENOR_MONTHS[tenor])
    if tenor == "1W":
        return Decimal("0.25")
    if tenor == "ON":
        return Decimal(0)
    return (Decimal(max(0, int(days))) / Decimal("30.4375")).quantize(Decimal("0.01"))


def forward_spread_pips(tenor: Optional[str], days: int) -> Decimal:
    return (_FEE["spread_pips"] + FWD_SPREAD_PIPS_PER_MONTH * tenor_months(tenor, days)).quantize(Decimal("0.01"))


# ---- quotes ------------------------------------------------------------------------------------------------------------------
def _session(services, now: int):
    return services.sessions.state(SESSION_GROUP, int(now))


def _exec_of(rec, view, session, now: int) -> Tuple[bool, str]:
    if rec is not None:
        return is_executable(rec, session, now)
    if session is None or not session.open:
        return False, "SESSION_CLOSED"
    if view is None or view.mark is None or view.quality == "unavailable":
        return False, "NO_QUOTE"
    if view.quality not in EXECUTABLE_QUALITIES or not view.executable:
        return False, ("STALE" if view.quality == "stale" else "COMPUTED_NOT_EXECUTABLE")
    return True, "OK"


def usd_leg(services, snapshot, ccy: str, session, now: int) -> dict:
    """USD per 1 unit of ``ccy`` (8 dp, the ledger rate of a currency) from its USD pair, with the pair print's quality and
    executability.  USD itself is 1."""
    ccy = _ccy(ccy)
    if ccy == "USD":
        return {"ccy": "USD", "pair": None, "rate": Decimal(1), "quoted": Decimal(1), "quality": "delayed", "executable": True, "reason": "OK",
                "quote_time": None, "source": "const"}
    pair = fx_pair_id(ccy)
    view = snapshot.fx(ccy)
    if view is None or view.mark is None or view.quality == "unavailable":
        return {"ccy": ccy, "pair": pair, "rate": None, "quoted": None, "quality": "unavailable", "executable": False, "reason": "NO_QUOTE",
                "quote_time": None, "source": None, "note": f"no USD rate for {ccy} ({pair})"}
    rec = quote_of(services, pair, snapshot)
    ok, reason = _exec_of(rec, view, session, now)
    inputs = view.inputs or {}
    quoted = qprice(inputs["inverted_from"]) if pair.startswith("USD") and inputs.get("inverted_from") else (
        qprice(Decimal(1) / qprice(view.mark)) if pair.startswith("USD") else qprice(view.mark))
    return {"ccy": ccy, "pair": pair, "rate": qprice(view.mark), "quoted": quoted, "quality": view.quality, "executable": ok, "reason": reason,
            "quote_time": view.quote_time, "source": view.source}


def _worst(*qs) -> str:
    return max((q or "unavailable" for q in qs), key=lambda q: QUALITY_RANK.get(q, 5))


def pair_mid(services, snapshot, pair: str, session, now: int) -> dict:
    """The mid of ``pair`` in its own convention: a USD pair from its print (``usd_leg``); a cross from its own Yahoo print
    when executable, else DERIVED from the two USD pairs (base USD rate / quote USD rate), executable only when both are.
    ``{pair, mid, basis, executable, reason, quality, quote_time, source, legs, direct}``."""
    pair = pair.upper()
    base, quote = pair[:3], pair[3:]
    if "USD" in (base, quote):
        foreign = quote if base == "USD" else base
        leg = usd_leg(services, snapshot, foreign, session, now)
        return {"pair": pair, "mid": leg["quoted"], "basis": "pair print", "executable": leg["executable"], "reason": leg["reason"], "quality": leg["quality"],
                "quote_time": leg["quote_time"], "source": leg["source"], "legs": [leg], "direct": True}
    direct = None
    rec = quote_of(services, pair, snapshot)
    view = snapshot.mark(pair) if hasattr(snapshot, "mark") else None
    if view is not None and view.mark is not None and view.quality != "unavailable":
        ok, reason = _exec_of(rec, view, session, now)
        direct = {"mid": qprice(view.mark), "executable": ok, "reason": reason, "quality": view.quality, "quote_time": view.quote_time, "source": view.source}
    lb, lq = usd_leg(services, snapshot, base, session, now), usd_leg(services, snapshot, quote, session, now)
    derived = None
    if lb["rate"] and lq["rate"]:
        dmid = qprice(lb["rate"] / lq["rate"])
        dok = lb["executable"] and lq["executable"]
        derived = {"mid": dmid, "executable": dok, "reason": "OK" if dok else (lb["reason"] if not lb["executable"] else lq["reason"]),
                   "quality": _worst(lb["quality"], lq["quality"]), "quote_time": min(t for t in (lb["quote_time"], lq["quote_time"]) if t) if (lb["quote_time"] or lq["quote_time"]) else None,
                   "source": f"derived from {lb['pair']} {lb['quoted']} and {lq['pair']} {lq['quoted']}"}
    pick = direct if direct is not None and direct["executable"] else (derived if derived is not None and derived["executable"] else (direct or derived))
    if pick is None:
        return {"pair": pair, "mid": None, "basis": None, "executable": False, "reason": "NO_QUOTE", "quality": "unavailable", "quote_time": None, "source": None,
                "legs": [lb, lq], "direct": False, "note": f"no {pair} print and no USD rate for {base if not lb['rate'] else quote}"}
    return {"pair": pair, "mid": pick["mid"], "basis": "pair print" if pick is direct else "derived from the USD pairs", "executable": pick["executable"],
            "reason": pick["reason"], "quality": pick["quality"], "quote_time": pick["quote_time"], "source": pick["source"], "legs": [lb, lq],
            "direct": pick is direct, "direct_quote": direct, "derived_quote": derived}


# ---- the account ---------------------------------------------------------------------------------------------------------------
def _account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    lrow = lifetime_row(conn, acct["lifetime_id"])
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {acct['lifetime_id']} is closed (History is read-only)")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _acct_of(services, account_id: str, conn=None):
    if conn is not None:
        return _account(conn, account_id)
    with services.db.reader() as rconn:
        return _account(rconn, account_id)


def _check_tradeable(services, acct, pi: dict, conn=None) -> None:
    akind = account_kind_for(acct)
    master = services.instruments
    inst = master.get(pi["pair"], conn)
    if inst is None:            # a cross not listed in the master (e.g. HKDSGD): the account must convert both currencies against USD
        legs = [master.get(fx_pair_id(c), conn) for c in (pi["base"], pi["quote"]) if c != "USD"]
        ok = bool(legs) and all(i is not None and master.tradeable_in(i, akind) for i in legs)
    else:
        ok = master.tradeable_in(inst, akind)
    if not ok:
        raise RejectError("NOT_TRADEABLE_HERE", f"{acct['account_id']} ({akind}) may not convert currencies; use the Credit, Foreign Markets & FX, PB or Treasury account",
                          {"account_kind": akind, "pair": pi["pair"]})


# ---- the quote (shared by the previews and the handlers) ------------------------------------------------------------------------
def quote(services, snapshot, account_id: str, buy_ccy: str, sell_ccy: str, amount_c: int, amount_ccy: str, conn=None, now=None, *,
          value_date: Optional[date] = None, outright: Optional[Decimal] = None, spread_pips: Optional[Decimal] = None) -> dict:
    """Everything a deal needs from one mark snapshot (pure: no writes).  Raises ``RejectError`` for a bad request; quote
    problems are returned in ``blockers`` (the handler turns them into 409).  ``now`` is the instant the session and value
    date are judged at.  A forward passes its ``value_date`` and its ``outright`` mid (the CIP rate) with its spread."""
    now = int(now if now is not None else snapshot.as_of())
    pi = pair_info(buy_ccy, sell_ccy)
    pair, b, s = pi["pair"], pi["buy_ccy"], pi["sell_ccy"]
    acct = _acct_of(services, account_id, conn)
    _check_tradeable(services, acct, pi, conn)
    amount_ccy = _ccy(amount_ccy)
    if amount_ccy not in (b, s):
        raise RejectError("AMOUNT_CCY", f"amount_ccy must be {b} or {s}")
    amount_c = int(amount_c)
    if amount_c < MIN_DEAL_C:
        raise RejectError("AMOUNT", f"the smallest deal is {amount_ccy} 1.00")
    session = _session(services, now)
    pm = pair_mid(services, snapshot, pair, session, now)
    blockers = []
    head = {"account_id": account_id, "pair": pair, "base": pi["base"], "quote_ccy": pi["quote"], "cross": pi["cross"], "foreign_ccy": pi["foreign"],
            "usd_quoted": pi["usd_quoted"], "buy_ccy": b, "sell_ccy": s, "amount_c": amount_c, "amount_ccy": amount_ccy}
    if pm["mid"] is None:
        blockers.append({"code": "QUOTE_UNAVAILABLE", "message": pm.get("note") or f"no {pair} rate received yet (Yahoo FX prints arrive while the FX session is open): nothing is dealt without one",
                         "details": {"pair": pair}})
        return dict(head, ok=False, blockers=blockers, quote=_quote_view(pm, session), executable=False, reason="NO_QUOTE")
    trade_date = fx_trade_date(services, now)
    sd = spot_date(services, pair, trade_date)
    vd = value_date or sd
    mid_quoted = qprice(pm["mid"]) if outright is None else qprice(outright)
    sp = _FEE["spread_pips"] if spread_pips is None else Decimal(spread_pips)
    rate_quoted = dealt_rate(mid_quoted, pair, pi["buying_base"], sp)
    if pi["cross"]:
        econ = _cross_economics(services, snapshot, pi, mid_quoted, rate_quoted, amount_c, amount_ccy, session, now)
    else:
        econ = _usd_pair_economics(pi, mid_quoted, rate_quoted, amount_c, amount_ccy)
    ok, reason = pm["executable"], pm["reason"]
    if not ok:
        blockers.append({"code": STALE_CODES.get(reason, "QUOTE_STALE"), "message": f"{pair}: {reason.lower().replace('_', ' ')} - the deal needs a fresh quote in the FX session",
                         "details": {"pair": pair, "reason": reason, "quality": pm["quality"], "quote_time": pm["quote_time"], "session": session.phase}})
    if econ.get("blocker"):
        blockers.append(econ.pop("blocker"))
    out = dict(head, **econ)
    out.update({"ok": not blockers, "blockers": blockers, "mid": str(mid_quoted), "spot_mid": str(qprice(pm["mid"])), "rate_quoted": str(rate_quoted),
                "pip": str(pip_of(pair)), "spread_pips": str(sp), "trade_date": trade_date.isoformat(), "spot_date": sd.isoformat(), "value_date": vd.isoformat(),
                "settle_days": fx_spot_days(pair), "quote": _quote_view(pm, session), "executable": ok, "reason": reason, "mid_basis": pm["basis"],
                "convention": tables.CONVENTIONS.get("FX_SPOT", "")})
    return out


def _quote_view(pm: dict, session) -> dict:
    q = {k: pm.get(k) for k in ("pair", "quality", "quote_time", "source", "executable", "reason", "basis")}
    q["mid"] = None if pm.get("mid") is None else str(pm["mid"])
    q["value"] = q["mid"]
    q["mark"] = q["mid"]
    q["session"] = getattr(session, "phase", None)
    q["legs"] = [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in l.items()} for l in (pm.get("legs") or [])]
    q["delay_label"] = "up to 15 min delayed" if pm.get("source") and "const" not in str(pm.get("source")) else ""
    return q


def _usd_pair_economics(pi: dict, mid_quoted: Decimal, rate_quoted: Decimal, amount_c: int, amount_ccy: str) -> dict:
    foreign, usd_quoted = pi["foreign"], pi["usd_quoted"]
    rate = qprice(Decimal(1) / rate_quoted) if usd_quoted else qprice(rate_quoted)
    foreign_c, usd_c, adjusted = deal_amounts(rate, amount_c, amount_ccy, foreign)
    usd_at_mid = fx_c(foreign_c, mid_usd_per_unit(mid_quoted, usd_quoted))
    spread_cost_c = abs(usd_c - usd_at_mid)
    b = pi["buy_ccy"]
    buy_c = foreign_c if b == foreign else usd_c
    sell_c = usd_c if b == foreign else foreign_c
    return {"buy_c": buy_c, "sell_c": sell_c, "buy": str(from_c(buy_c)), "sell": str(from_c(sell_c)), "usd_adjusted_c": adjusted, "rate": str(rate),
            "rate_basis": f"USD per 1 {foreign} (ledger rate, 8 dp)", "spread_cost_c": spread_cost_c, "spread_cost": str(from_c(spread_cost_c)),
            "ledger": {foreign: str(rate), "USD": "1"}}


def _cross_economics(services, snapshot, pi: dict, mid_quoted: Decimal, rate_quoted: Decimal, amount_c: int, amount_ccy: str, session, now: int) -> dict:
    """A cross: amounts from the dealt cross rate; the typed leg's USD mid and the computed leg's deal-implied USD rate for
    the ledger (module docstring CROSSES)."""
    base, quote, b = pi["base"], pi["quote"], pi["buy_ccy"]
    if amount_ccy == base:
        base_c = amount_c
        quote_c = to_c(from_c(base_c) * rate_quoted)
        quote_mid_c = to_c(from_c(base_c) * mid_quoted)
        typed, other, other_c, other_mid_c = base, quote, quote_c, quote_mid_c
    else:
        quote_c = amount_c
        base_c = to_c(from_c(quote_c) / rate_quoted)
        base_mid_c = to_c(from_c(quote_c) / mid_quoted)
        typed, other, other_c, other_mid_c = quote, base, base_c, base_mid_c
    if base_c < 1 or quote_c < 1:
        raise RejectError("AMOUNT", "the deal is too small: one leg rounds to zero")
    lt, lo = usd_leg(services, snapshot, typed, session, now), usd_leg(services, snapshot, other, session, now)
    out = {"buy_c": base_c if b == base else quote_c, "sell_c": quote_c if b == base else base_c, "usd_adjusted_c": 0}
    out["buy"], out["sell"] = str(from_c(out["buy_c"])), str(from_c(out["sell_c"]))
    out["rate"] = str(rate_quoted)
    out["rate_basis"] = f"{quote} per 1 {base} (the dealt cross rate)"
    if lt["rate"] is None or lo["rate"] is None:
        missing = typed if lt["rate"] is None else other
        out["blocker"] = {"code": "QUOTE_UNAVAILABLE", "message": f"no USD rate for {missing}: the deal cannot be valued in USD", "details": {"currency": missing}}
        out["spread_cost_c"] = None
        return out
    diff = abs(other_c - other_mid_c)
    spread_cost_c = fx_c(diff, lo["rate"])
    rates = {typed: lt["rate"], other: lo["rate"]}
    amounts = {base: base_c, quote: quote_c}
    lp = cross_ledger(amounts, rates)
    usd_c = lp["usd_c"]
    out.update({"usd_value_c": usd_c, "usd_value": str(from_c(usd_c)), "spread_cost_c": spread_cost_c, "spread_cost": str(from_c(spread_cost_c)),
                "ledger": {lp["anchor"]["ccy"]: lp["anchor"]["rate"], lp["fitted"]["ccy"]: ", ".join(p["rate"] for p in lp["fitted"]["pieces"])},
                "ledger_pieces": lp,
                "ledger_note": (f"{lp['anchor']['ccy']} booked at its USD mid {lp['anchor']['rate']} (USD {fmt_money(usd_c)}); {lp['fitted']['ccy']} at the deal-implied"
                                f" USD rate so both legs carry exactly USD {fmt_money(usd_c)} (3900 nets to zero); the spread {other} {fmt_money(diff)} shows in the FX result")})
    return out


def cross_ledger(amounts: Dict[str, int], usd_rates: Dict[str, Decimal]) -> dict:
    """The ledger of a cross between the two currencies of ``amounts`` (cents): the leg with more minor units is the ANCHOR at
    its USD rate, U = fx_c(amount, rate); the other leg is fitted to exactly U (``fit_base``).  ``{anchor {ccy, amount_c,
    rate}, fitted {ccy, pieces [{amount_c, rate}]}, usd_c}``."""
    (c1, a1), (c2, a2) = list(amounts.items())
    anchor, fitted = ((c1, a1), (c2, a2)) if a1 >= a2 else ((c2, a2), (c1, a1))
    r = qprice(usd_rates[anchor[0]])
    usd_c = fx_c(anchor[1], r)
    pieces = fit_base(fitted[1], usd_c)
    return {"anchor": {"ccy": anchor[0], "amount_c": int(anchor[1]), "rate": str(r)},
            "fitted": {"ccy": fitted[0], "pieces": [{"amount_c": int(a), "rate": str(x)} for a, x in pieces]}, "usd_c": usd_c}


# ---- funding of the sold currency (OA-11) -------------------------------------------------------------------------------------
def _pending_forward_flow(conn, account_id: str, ccy: str, by_date: date) -> Tuple[int, int]:
    """``(receivable_c, payable_c)`` of the account's not-yet-posted forwards in ``ccy`` due on or before ``by_date``."""
    recv = pay = 0
    for r in conn.execute("SELECT buy_ccy, sell_ccy, buy_c, sell_c FROM fx_deals WHERE account_id=? AND kind='FORWARD' AND status='PENDING' AND entry_id=0"
                          " AND value_date<=? AND (buy_ccy=? OR sell_ccy=?)", (account_id, by_date.isoformat(), ccy, ccy)).fetchall():
        if r["buy_ccy"] == ccy:
            recv += int(r["buy_c"])
        if r["sell_ccy"] == ccy:
            pay += int(r["sell_c"])
    return recv, pay


def funding_check(services, account_id: str, ccy: str, need_c: int, value_date: date, snapshot=None, conn=None) -> dict:
    """Can the account deliver ``need_c`` of ``ccy`` on ``value_date``?  Own available then (``accounting.buying_power``:
    settled + receivables due by then - payables - reserved, floored) + the account's unposted forwards due by then + what
    Treasury can sweep (and, for USD in a PB account with financing on, PB headroom).  ``{fundable, shortfall_c, ...}``."""
    acc = services.accounting
    need_c = int(need_c)
    own = tre = pb = 0
    try:
        bp = acc.buying_power(account_id, ccy, value_date, snapshot=snapshot)
        own, tre, pb = int(bp.available_c), max(0, int(bp.treasury_available_c)), max(0, int(bp.pb_headroom_c))
    except Exception:   # noqa: BLE001 - an older accounting: own available only, Treasury read below
        own = int(acc.available(account_id, ccy, value_date, snapshot=snapshot))
    acct = _acct_of(services, account_id, conn)
    if account_kind_for(acct) == "TREASURY":
        tre = pb = 0
    if conn is not None:
        recv, pay = _pending_forward_flow(conn, account_id, ccy, value_date)
    else:
        with services.db.reader() as rconn:
            recv, pay = _pending_forward_flow(rconn, account_id, ccy, value_date)
    fwd = recv - pay
    have = own + fwd
    from_tre = min(max(0, need_c - have), tre)
    from_pb = min(max(0, need_c - have - from_tre), pb)
    short = max(0, need_c - have - from_tre - from_pb)
    fixes = []
    if short:
        if ccy == "USD":
            fixes.append("add USD to this book's Treasury (Treasury > Add funds), then deal again")
        else:
            fixes.append(f"buy {ccy} first on this desk (value on or before {value_date.isoformat()})")
            fixes.append(f"or transfer {ccy} from Treasury (Treasury > Transfer) if Treasury holds it")
    return {"currency": ccy, "need_c": need_c, "own_available_c": own, "forwards_due_c": fwd, "treasury_available_c": tre, "pb_headroom_c": pb,
            "from_own_c": min(need_c, max(0, have)), "from_treasury_c": from_tre, "from_pb_c": from_pb, "shortfall_c": short, "fundable": short == 0,
            "value_date": value_date.isoformat(), "fixes": fixes,
            "text": (f"{ccy} {fmt_money(need_c)} due {value_date.isoformat()}: own {fmt_money(max(0, have))}"
                     + (f", Treasury sweep {fmt_money(from_tre)}" if from_tre else "") + (f", PB financing {fmt_money(from_pb)}" if from_pb else "")
                     + (f" - SHORT {fmt_money(short)}" if short else " - covered"))}


def _unfundable(acct_id: str, ccy: str, chk: dict, forward: bool) -> dict:
    return {"code": "SETTLEMENT_UNFUNDABLE",
            "message": (f"SELL {ccy} {fmt_money(chk['need_c'])} for value {chk['value_date']}: {acct_id} has {ccy} {fmt_money(max(0, chk['own_available_c'] + chk['forwards_due_c']))}"
                        f" available by then and Treasury {ccy} {fmt_money(chk['treasury_available_c'])} - short {ccy} {fmt_money(chk['shortfall_c'])}. "
                        + "; ".join(chk["fixes"])),
            "details": dict(chk, account_id=acct_id, forward=forward)}


def _legs(services, pv: dict, snapshot, conn=None) -> Dict[str, dict]:
    """Both legs' settled / pending before and after, the value date's availability and how the sold leg is funded."""
    cash = services.accounting.cash(pv["account_id"], snapshot=snapshot)
    vd = date.fromisoformat(pv["value_date"])
    legs = {}
    for ccy, amt, gl, sign in ((pv["buy_ccy"], pv["buy_c"], "1250", 1), (pv["sell_ccy"], pv["sell_c"], "2350", -1)):
        cv = cash.get(ccy)
        settled = cv.settled_c if cv else 0
        pending = cv.pending_c if cv else 0
        avail_vd = services.accounting.available(pv["account_id"], ccy, vd, snapshot=snapshot)
        leg = {"currency": ccy, "amount_c": amt, "amount": str(from_c(amt)), "gl": gl, "effect": "receivable" if sign > 0 else "payable",
               "settled_before_c": settled, "pending_before_c": pending, "pending_after_c": pending + sign * amt,
               "settled_after_value_date_c": settled + sign * amt, "available_at_value_date_before_c": avail_vd,
               "available_at_value_date_after_c": avail_vd + sign * amt}
        if sign < 0:
            chk = funding_check(services, pv["account_id"], ccy, amt, vd, snapshot=snapshot, conn=conn)
            short = max(0, amt - avail_vd)
            leg["settlement_shortfall_c"] = short
            leg["funding"] = chk
            if short:
                leg["note"] = (f"on {vd.isoformat()} the account lacks {ccy} {fmt_money(short)}: " + (
                    f"Treasury sweeps {fmt_money(chk['from_treasury_c'])}" + (f" and PB financing covers {fmt_money(chk['from_pb_c'])}" if chk["from_pb_c"] else "")
                    if chk["fundable"] else f"Treasury cannot cover it - short {fmt_money(chk['shortfall_c'])}: " + "; ".join(chk["fixes"])))
        legs[ccy] = leg
    return legs


# ---- previews (§13.5; pure) -------------------------------------------------------------------------------------------------
def spot_preview(services, account_id: str, buy_ccy: str, sell_ccy: str, amount_c: int, amount_ccy: str, *, subscribe: bool = False) -> dict:
    """§13.5: rate = mid -/+ 0.2 pip against the user, amount in either currency, value date T+2 (T+1 USD/CAD, USD/TRY),
    ``spread_cost_c``, both legs' settled / pending effect and how the sold leg is funded (``legs``); a sale the account
    and Treasury cannot fund by the value date is a SETTLEMENT_UNFUNDABLE blocker (OA-11).  Pure: writes nothing."""
    if subscribe:
        _subscribe_pairs(services, buy_ccy, sell_ccy)
    snap = services.marks.snapshot()
    pv = quote(services, snap, account_id, buy_ccy, sell_ccy, amount_c, amount_ccy, now=services.clock.epoch())
    pv["kind"] = "SPOT"
    if "buy_c" not in pv:
        return pv
    pv["legs"] = _legs(services, pv, snap)
    chk = pv["legs"][pv["sell_ccy"]].get("funding") or {}
    if chk and not chk.get("fundable"):
        pv["blockers"].append(_unfundable(account_id, pv["sell_ccy"], chk, False))
        pv["ok"] = False
    return pv


def forward_preview(services, account_id: str, buy_ccy: str, sell_ccy: str, amount_c: int, amount_ccy: str, tenor: Optional[str] = None,
                    value_date=None, *, subscribe: bool = False) -> dict:
    """An outright forward's preview: the CIP outright with its inputs (spot, both rates with series and as-of), the forward
    points, the dealt rate (outright -/+ the disclosed forward spread), the value date, both legs and the funding warning of
    the sold currency.  Pure."""
    if subscribe:
        _subscribe_pairs(services, buy_ccy, sell_ccy)
    snap = services.marks.snapshot()
    now = int(services.clock.epoch())
    pi = pair_info(buy_ccy, sell_ccy)
    vd, tenor = _forward_value_date(services, pi["pair"], tenor, value_date, now)
    F, fin = forward_rate(services, pi["pair"], vd, snap)
    if F is None:
        acct = _acct_of(services, account_id)
        _check_tradeable(services, acct, pi)
        return {"ok": False, "kind": "FORWARD", "account_id": account_id, "pair": pi["pair"], "buy_ccy": pi["buy_ccy"], "sell_ccy": pi["sell_ccy"], "tenor": tenor,
                "value_date": vd.isoformat(), "forward": fin,
                "blockers": [{"code": "FORWARD_UNAVAILABLE", "message": f"{pi['pair']} {tenor or vd.isoformat()}: no forward price - {fin.get('note')}", "details": fin}]}
    days = int(fin["days"])
    sp = forward_spread_pips(tenor, days)
    pv = quote(services, snap, account_id, buy_ccy, sell_ccy, amount_c, amount_ccy, now=now, value_date=vd, outright=F, spread_pips=sp)
    pv.update({"kind": "FORWARD", "tenor": tenor, "forward": fin, "outright_mid": str(F), "points": fin.get("points"), "spread_text": FWD_SPREAD_TEXT,
               "spot_rate_quoted": None})
    if "buy_c" not in pv:
        return pv
    pv["legs"] = _legs(services, pv, snap)
    chk = pv["legs"][pv["sell_ccy"]].get("funding") or {}
    if chk and not chk.get("fundable"):
        pv.setdefault("warnings", []).append(_unfundable(account_id, pv["sell_ccy"], chk, True)["message"]
                                             + " - a forward is allowed: buy the currency before the value date or close the forward with an opposite one")
    return pv


def _forward_value_date(services, pair: str, tenor, value_date, now: int) -> Tuple[date, Optional[str]]:
    td = fx_trade_date(services, now)
    sd = spot_date(services, pair, td)
    if value_date not in (None, ""):
        try:
            vd = value_date if isinstance(value_date, date) else date.fromisoformat(str(value_date))
        except ValueError as e:
            raise RejectError("VALUE_DATE", f"value_date must be YYYY-MM-DD, got {value_date!r}") from e
        if not services.cal.is_business_day(vd):
            raise RejectError("VALUE_DATE", f"{vd.isoformat()} is not a business day: pick another value date")
        if vd <= sd:
            raise RejectError("VALUE_DATE", f"a forward's value date must be after the spot date {sd.isoformat()} (deal spot for {sd.isoformat()})")
        last = tenor_date(services, pair, "1Y", td)
        if vd > last:
            raise RejectError("VALUE_DATE", f"the forward grid stops at 1 year ({last.isoformat()}): longer forwards need term rates this terminal has no free source for")
        matched = next((t for t in TENORS if tenor_date(services, pair, t, td) == vd), None)
        return vd, matched or "BROKEN"
    t = str(tenor or "").upper()
    if not t:
        raise RejectError("TENOR", f"a forward needs a tenor ({', '.join(TENORS)}) or a value date")
    return tenor_date(services, pair, t, td), t


# ---- forward rates (C17 forward_rate) ----------------------------------------------------------------------------------------
def rate_for(services, ccy: str, days: int = 90) -> Tuple[Optional[Decimal], dict]:
    """``(percent, info)`` of the rate a currency's forward leg uses (module docstring FORWARDS); ``(None, info)`` with the
    reason when no observation exists - never a default."""
    ccy = _ccy(ccy)
    rates = getattr(services, "rates", None)
    sid = tables.POLICY_INDEX.get(ccy)
    info = {"ccy": ccy, "series": sid, "basis": tables.daycount(ccy)}
    fred = {}
    try:
        fred = rates.fred_status() if rates is not None and hasattr(rates, "fred_status") else {}
    except Exception:   # noqa: BLE001
        fred = {}
    if sid is None:
        return None, dict(info, note=f"no free policy-rate series for {ccy} in this terminal: no forward price (spot only)")
    got = rates.latest(sid) if rates is not None else None
    if got is None and ccy == "USD":
        curve = getattr(services, "curve", None)
        y = None
        try:
            y = curve.par_yield(Decimal(max(1, int(days))) / Decimal(365)) if curve is not None else None
        except Exception:   # noqa: BLE001
            y = None
        if y is not None:
            as_of = curve.as_of()
            return qprice(y), dict(info, series="UST_PAR", value_pct=str(qprice(y)), as_of=as_of.isoformat() if as_of else None,
                                   source="US Treasury par curve (bill yield at the tenor; SOFR not observed)", limited=False,
                                   label=f"US Treasury bill yield {qprice(y)} % (par curve {as_of.isoformat() if as_of else 'n/a'})")
    if got is None:
        why = "FRED unavailable (maintenance)" if fred.get("maintenance") else "not observed yet"
        return None, dict(info, note=f"{sid} ({ccy}) {why}: no forward price")
    d, v = got
    monthly = sid in tables.POLICY_INDEX_MONTHLY
    src = (rates.source(sid) if hasattr(rates, "source") else None) or "FRED"
    return qprice(v), dict(info, value_pct=str(qprice(v)), as_of=d.isoformat(), source=src, limited=monthly,
                           label=f"{sid} {qprice(v)} % ({'monthly proxy, LIMITED' if monthly else 'daily'}, as of {d.isoformat()})")


def forward_rate(services, pair: str, value_date, snapshot=None) -> Tuple[Optional[Decimal], dict]:
    """C17: the canonical covered-interest-parity outright of ``pair`` for ``value_date`` (module docstring FORWARDS):
    ``(F, inputs)`` with F in the pair's own convention (6 dp) and every input; ``(None, {note, ...})`` when an input is
    missing.  Worked (§19.13): S 1.1238, SOFR 3.87, ECBDFR 2.00, 90 days: 1.1238 x 1.009675 / 1.005 = 1.129028."""
    pair = str(pair or "").upper()
    if len(pair) != 6:
        return None, {"note": f"not a currency pair: {pair!r}"}
    vd = value_date if isinstance(value_date, date) else date.fromisoformat(str(value_date))
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    now = int(services.clock.epoch())
    session = _session(services, now)
    pm = pair_mid(services, snap, pair, session, now)
    td = fx_trade_date(services, now)
    sd = spot_date(services, pair, td)
    spot = {"mid": None if pm["mid"] is None else str(qprice(pm["mid"])), "quality": pm["quality"], "quote_time": pm["quote_time"], "source": pm["source"],
            "basis": pm["basis"], "executable": pm["executable"]}
    if pm["mid"] is None:
        return None, {"note": pm.get("note") or f"no spot rate for {pair}", "spot": spot, "spot_date": sd.isoformat()}
    if vd < sd:
        return None, {"note": f"value date {vd.isoformat()} is before the spot date {sd.isoformat()}", "spot": spot, "spot_date": sd.isoformat()}
    days = (vd - sd).days
    base, quote = pair[:3], pair[3:]
    rb, ib = rate_for(services, base, days)
    rq, iq = rate_for(services, quote, days)
    if rb is None or rq is None:
        return None, {"note": (ib if rb is None else iq).get("note"), "spot": spot, "r_base": ib, "r_quote": iq, "days": days, "spot_date": sd.isoformat()}
    S = qprice(pm["mid"])
    F = cip_forward(S, rb / Decimal(100), rq / Decimal(100), days, ib["basis"], iq["basis"])
    pip = pip_of(pair)
    points = ((F - S) / pip).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    fq = (Decimal(1) + rq / Decimal(100) * Decimal(days) / Decimal(iq["basis"])).quantize(Decimal("0.00000001"))
    fb = (Decimal(1) + rb / Decimal(100) * Decimal(days) / Decimal(ib["basis"])).quantize(Decimal("0.00000001"))
    limited = bool(ib.get("limited") or iq.get("limited"))
    return F, {"pair": pair, "S": str(S), "spot": spot, "spot_date": sd.isoformat(), "value_date": vd.isoformat(), "days": days, "r_base": ib, "r_quote": iq,
               "outright": str(F), "points": str(points), "pip": str(pip), "limited": limited, "method": "cip", "quality": "computed",
               "arithmetic": (f"F = {S} x (1 + {rq} % x {days}/{iq['basis']}) / (1 + {rb} % x {days}/{ib['basis']}) = {S} x {fq} / {fb} = {F}"),
               "note": ("LIMITED: " + ", ".join(i["label"] for i in (ib, iq) if i.get("limited")) if limited else "")}


def forward_grid(services, pair: str, snapshot=None, value_date=None) -> dict:
    """GET /api/fx/forwards: the tenor grid (and an optional broken date) of ``pair``: value date, days, outright mid,
    points, the dealt rates on both sides and the inputs."""
    pair = str(pair or "").upper()
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    now = int(services.clock.epoch())
    td = fx_trade_date(services, now)
    rows = []
    first_inputs = None

    def row(tenor, vd):
        F, fin = forward_rate(services, pair, vd, snap)
        days = int(fin.get("days") or 0)
        r = {"tenor": tenor, "label": TENOR_LABEL.get(tenor, "broken date"), "value_date": vd.isoformat(), "days": days, "ok": F is not None,
             "note": fin.get("note") or ""}
        if F is not None:
            sp = forward_spread_pips(tenor if tenor != "BROKEN" else None, days)
            r.update({"outright": str(F), "points": fin["points"], "spread_pips": str(sp), "limited": fin.get("limited"),
                      "bid": str(dealt_rate(F, pair, False, sp)), "ask": str(dealt_rate(F, pair, True, sp)), "arithmetic": fin.get("arithmetic")})
        return r, fin

    for t in TENORS:
        r, fin = row(t, tenor_date(services, pair, t, td))
        rows.append(r)
        if first_inputs is None or (r["ok"] and not first_inputs.get("r_base")):
            first_inputs = fin
    broken = None
    if value_date not in (None, ""):
        try:
            vd, matched = _forward_value_date(services, pair, None, value_date, now)
            broken, _ = row(matched or "BROKEN", vd)
        except RejectError as e:
            broken = {"tenor": "BROKEN", "value_date": str(value_date), "ok": False, "note": e.message}
    fi = first_inputs or {}
    return {"pair": pair, "trade_date": td.isoformat(), "spot_date": spot_date(services, pair, td).isoformat(), "spot": fi.get("spot"), "r_base": fi.get("r_base"),
            "r_quote": fi.get("r_quote"), "tenors": rows, "broken": broken, "spread_text": FWD_SPREAD_TEXT,
            "method": "covered interest parity: F = S x (1 + r_quote x d/B) / (1 + r_base x d/B), d = days from the spot date; computed, never a print"}


# ---- handlers ------------------------------------------------------------------------------------------------------------------
def _payload_amount(payload: dict) -> Tuple[int, str]:
    amount_c = require_amount_c(payload)
    ccy = payload.get("amount_ccy")
    if not ccy:
        raise RejectError("AMOUNT_CCY", "amount_ccy is required (the currency the amount is typed in)")
    return amount_c, _ccy(ccy)


def _deal_lines(account_id: str, pv: dict) -> Tuple[List, Dict[str, str]]:
    """The deal entry's lines (§5.4) for a USD pair (foreign lines at the 8-dp ledger rate, USD = fx_c(foreign, rate)) or a
    cross (``ledger_pieces``: the anchor leg at its USD mid, the other fitted to the same USD base)."""
    buy, sell, buy_c, sell_c = pv["buy_ccy"], pv["sell_ccy"], int(pv["buy_c"]), int(pv["sell_c"])
    if not pv.get("cross"):
        rate = qprice(pv["rate"])
        foreign = pv["foreign_ccy"]
        fxr = {"fx_rate": rate}
        lines = [dr(account_id, "1250", buy, buy_c, memo=f"receive {buy} on {pv['value_date']}", **(fxr if buy == foreign else {})),
                 cr(account_id, "3900", buy, buy_c, memo="FX clearing", **(fxr if buy == foreign else {})),
                 dr(account_id, "3900", sell, sell_c, memo="FX clearing", **(fxr if sell == foreign else {})),
                 cr(account_id, "2350", sell, sell_c, memo=f"deliver {sell} on {pv['value_date']}", **(fxr if sell == foreign else {}))]
        return lines, {foreign: str(rate)}
    lp = pv["ledger_pieces"]
    anchor, fitted = lp["anchor"], lp["fitted"]
    pieces = {anchor["ccy"]: [(int(anchor["amount_c"]), qprice(anchor["rate"]))], fitted["ccy"]: [(int(p["amount_c"]), qprice(p["rate"])) for p in fitted["pieces"]]}
    lines = []
    for amt, r in pieces[buy]:
        lines.append(dr(account_id, "1250", buy, amt, memo=f"receive {buy} on {pv['value_date']}", fx_rate=r))
        lines.append(cr(account_id, "3900", buy, amt, memo="FX clearing", fx_rate=r))
    for amt, r in pieces[sell]:
        lines.append(dr(account_id, "3900", sell, amt, memo="FX clearing", fx_rate=r))
        lines.append(cr(account_id, "2350", sell, amt, memo=f"deliver {sell} on {pv['value_date']}", fx_rate=r))
    return lines, {c: ", ".join(str(r) for _a, r in ps) for c, ps in pieces.items()}


def _settlement_rows_for(ctx: TxContext, deal_id: str, account_id: str, pv: dict) -> List[dict]:
    out = []
    for leg, ccy, amt, gl in (("BUY_CCY", pv["buy_ccy"], int(pv["buy_c"]), "1250"), ("SELL_CCY", pv["sell_ccy"], -int(pv["sell_c"]), "2350")):
        sid = next_id(ctx.conn, "settlement")
        ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                         (sid, None, deal_id, account_id, leg, ccy, int(amt), gl, pv["value_date"]))
        out.append({"settle_id": sid, "leg": leg, "currency": ccy, "amount_c": int(amt), "gl": gl, "due_date": pv["value_date"], "status": "PENDING"})
    return out


def _book_spot(ctx: TxContext, pv: dict, *, link: Optional[dict] = None, title: str = "FX spot") -> dict:
    """Post one spot deal of the quote ``pv`` (entry + settlement rows + fx_deals row + event) inside ``ctx``."""
    conn = ctx.conn
    account_id = pv["account_id"]
    buy, sell, buy_c, sell_c = pv["buy_ccy"], pv["sell_ccy"], int(pv["buy_c"]), int(pv["sell_c"])
    deal_id = next_id(conn, "fx_deal")
    lines, ledger_rates = _deal_lines(account_id, pv)
    sign = "-" if Decimal(pv["rate_quoted"]) < Decimal(pv["mid"]) else "+"
    memo = (f"{title} {deal_id}: SELL {sell} {fmt_money(sell_c)} / BUY {buy} {fmt_money(buy_c)} @ {pv['rate_quoted']} ({pv['pair']} mid {pv['mid']}"
            f" {sign} {pv['spread_pips']} pip), spread cost USD {fmt_money(pv.get('spread_cost_c') or 0)}, value {pv['value_date']}"
            + (f" ({link.get('text')})" if link and link.get("text") else ""))
    entry_id = ctx.post(Entry("FX", memo, lines, f"fx:{deal_id}", ref_type="fx_deal", ref_id=deal_id))
    settlements = _settlement_rows_for(ctx, deal_id, account_id, pv)
    inputs = {k: pv.get(k) for k in ("pair", "foreign_ccy", "usd_quoted", "cross", "rate_quoted", "rate_basis", "pip", "spread_pips", "amount_c", "amount_ccy",
                                     "usd_adjusted_c", "trade_date", "spot_date", "settle_days", "reason", "mid_basis", "ledger_pieces", "usd_value_c")}
    inputs["ledger_rates"] = ledger_rates
    if link:
        inputs["link"] = link
    conn.execute("INSERT INTO fx_deals(deal_id, command_id, account_id, kind, buy_ccy, sell_ccy, buy_c, sell_c, rate, mid, spread_cost_c, quote_json, trade_ts,"
                 " trade_date, value_date, status, settled_ts, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',NULL,?,?)",
                 (deal_id, ctx.command_id, account_id, "SPOT", buy, sell, buy_c, sell_c, str(pv["rate"]), pv["mid"], int(pv.get("spread_cost_c") or 0), dumps(pv["quote"]),
                  ctx.ts, pv["trade_date"], pv["value_date"], entry_id, dumps(inputs)))
    ctx.event("FX_DEALT", account_id=account_id, instrument_id=pv["pair"], ref_type="fx_deal", ref_id=deal_id, buy_ccy=buy, sell_ccy=sell, buy_c=buy_c, sell_c=sell_c,
              rate=str(pv["rate"]), rate_quoted=pv["rate_quoted"], mid=pv["mid"], spread_cost_c=int(pv.get("spread_cost_c") or 0), value_date=pv["value_date"],
              kind="SPOT", link=link)
    ctx.touched_accounts.add(account_id)
    deal = {"deal_id": deal_id, "account_id": account_id, "kind": "SPOT", "buy_ccy": buy, "sell_ccy": sell, "buy_c": buy_c, "sell_c": sell_c, "rate": str(pv["rate"]),
            "rate_quoted": pv["rate_quoted"], "mid": pv["mid"], "pair": pv["pair"], "spread_cost_c": int(pv.get("spread_cost_c") or 0), "trade_date": pv["trade_date"],
            "value_date": pv["value_date"], "status": "PENDING", "entry_id": entry_id, "usd_adjusted_c": pv.get("usd_adjusted_c", 0), "cross": bool(pv.get("cross")),
            "ledger_rates": ledger_rates}
    return {"deal": deal, "entry_id": entry_id, "settlements": settlements, "memo": memo}


def _services_of(ctx: TxContext):
    if ctx.services is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return ctx.services


def h_spot(ctx: TxContext, payload: dict) -> dict:
    """``fx.spot {account_id, buy_ccy, sell_ccy, amount_c | amount, amount_ccy}``: the two-leg entry at the deal rate (a USD
    pair or a cross), two settlement rows due on the value date, one ``fx_deals`` row.  409 when the quote is not
    executable or the sold currency cannot be delivered on the value date (SETTLEMENT_UNFUNDABLE, OA-11)."""
    services = _services_of(ctx)
    account_id = str(payload.get("account_id") or "")
    if not account_id:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    amount_c, amount_ccy = _payload_amount(payload)
    pv = quote(services, ctx.marks, account_id, payload.get("buy_ccy"), payload.get("sell_ccy"), amount_c, amount_ccy, conn=ctx.conn, now=ctx.ts)
    if pv["blockers"]:
        b = pv["blockers"][0]
        raise ConflictError(b["code"], b["message"], dict(b.get("details") or {}, preview=pv))
    chk = funding_check(services, account_id, pv["sell_ccy"], int(pv["sell_c"]), date.fromisoformat(pv["value_date"]), snapshot=ctx.marks, conn=ctx.conn)
    if not chk["fundable"]:
        u = _unfundable(account_id, pv["sell_ccy"], chk, False)
        raise ConflictError(u["code"], u["message"], u["details"])
    out = _book_spot(ctx, pv)
    out["preview"] = pv
    out["funding"] = chk
    return out


def h_forward(ctx: TxContext, payload: dict) -> dict:
    """``fx.forward {account_id, buy_ccy, sell_ccy, amount_c | amount, amount_ccy, tenor | value_date}``: an outright at the
    CIP rate +/- the forward spread (module docstring FORWARDS): one ``fx_deals`` row (kind FORWARD, entry 0), no cash until
    the value date.  409 FORWARD_UNAVAILABLE when an input rate is missing, QUOTE_STALE when the spot is not executable."""
    services = _services_of(ctx)
    account_id = str(payload.get("account_id") or "")
    if not account_id:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    amount_c, amount_ccy = _payload_amount(payload)
    pi = pair_info(payload.get("buy_ccy"), payload.get("sell_ccy"))
    vd, tenor = _forward_value_date(services, pi["pair"], payload.get("tenor"), payload.get("value_date"), ctx.ts)
    F, fin = forward_rate(services, pi["pair"], vd, ctx.marks)
    if F is None:
        raise ConflictError("FORWARD_UNAVAILABLE", f"{pi['pair']} {tenor or vd.isoformat()}: no forward price - {fin.get('note')}", fin)
    days = int(fin["days"])
    sp = forward_spread_pips(tenor, days)
    pv = quote(services, ctx.marks, account_id, pi["buy_ccy"], pi["sell_ccy"], amount_c, amount_ccy, conn=ctx.conn, now=ctx.ts, value_date=vd, outright=F,
               spread_pips=sp)
    if pv["blockers"]:
        b = pv["blockers"][0]
        raise ConflictError(b["code"], b["message"], dict(b.get("details") or {}, preview=pv))
    conn = ctx.conn
    deal_id = next_id(conn, "fx_deal")
    buy, sell, buy_c, sell_c = pv["buy_ccy"], pv["sell_ccy"], int(pv["buy_c"]), int(pv["sell_c"])
    chk = funding_check(services, account_id, sell, sell_c, vd, snapshot=ctx.marks, conn=conn)
    usd_mids = {}
    session = _session(services, ctx.ts)
    for c in (buy, sell):
        leg = usd_leg(services, ctx.marks, c, session, ctx.ts)
        usd_mids[c] = None if leg["rate"] is None else str(leg["rate"])
    inputs = {k: pv.get(k) for k in ("pair", "foreign_ccy", "usd_quoted", "cross", "rate_quoted", "rate_basis", "pip", "spread_pips", "amount_c", "amount_ccy",
                                     "usd_adjusted_c", "trade_date", "spot_date", "reason", "mid_basis", "ledger_pieces", "usd_value_c", "spot_mid")}
    inputs.update({"tenor": tenor, "forward": fin, "outright_mid": str(F), "points": fin.get("points"), "spread_text": FWD_SPREAD_TEXT, "usd_mids_at_trade": usd_mids,
                   "funding_at_trade": chk})
    conn.execute("INSERT INTO fx_deals(deal_id, command_id, account_id, kind, buy_ccy, sell_ccy, buy_c, sell_c, rate, mid, spread_cost_c, quote_json, trade_ts,"
                 " trade_date, value_date, status, settled_ts, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',NULL,0,?)",
                 (deal_id, ctx.command_id, account_id, "FORWARD", buy, sell, buy_c, sell_c, str(pv["rate"]), str(F), int(pv.get("spread_cost_c") or 0), dumps(pv["quote"]),
                  ctx.ts, pv["trade_date"], vd.isoformat(), dumps(inputs)))
    ctx.event("FX_FORWARD_DEALT", account_id=account_id, instrument_id=pv["pair"], ref_type="fx_deal", ref_id=deal_id, buy_ccy=buy, sell_ccy=sell, buy_c=buy_c,
              sell_c=sell_c, rate_quoted=pv["rate_quoted"], outright_mid=str(F), points=fin.get("points"), value_date=vd.isoformat(), tenor=tenor)
    ctx.touched_accounts.add(account_id)
    memo = (f"FX forward {deal_id}: SELL {sell} {fmt_money(sell_c)} / BUY {buy} {fmt_money(buy_c)} @ {pv['rate_quoted']} ({pv['pair']} outright {F} = spot {fin['S']}"
            f" {'+' if Decimal(fin['points']) >= 0 else ''}{fin['points']} pips, {sp} pip spread), value {vd.isoformat()} ({tenor}); no cash until then")
    deal = {"deal_id": deal_id, "account_id": account_id, "kind": "FORWARD", "buy_ccy": buy, "sell_ccy": sell, "buy_c": buy_c, "sell_c": sell_c, "rate": str(pv["rate"]),
            "rate_quoted": pv["rate_quoted"], "mid": str(F), "outright_mid": str(F), "points": fin.get("points"), "pair": pv["pair"], "tenor": tenor,
            "spread_cost_c": int(pv.get("spread_cost_c") or 0), "trade_date": pv["trade_date"], "value_date": vd.isoformat(), "status": "PENDING", "entry_id": 0}
    out = {"deal": deal, "entry_id": None, "settlements": [], "preview": pv, "forward": fin, "memo": memo, "funding": chk}
    if not chk["fundable"]:
        out["warning"] = _unfundable(account_id, sell, chk, True)["message"]
    return out


# ---- the forward's value date: entry at K + settlement rows (job FX_FORWARD_DUE) ----------------------------------------------------
def post_forward(ctx: TxContext, deal_id: str, services=None, force: bool = False) -> dict:
    """On (or after) its value date a FORWARD gets its deal entry at the dealt rate K and its two settlement rows (due that
    day); SETTLE_DUE then settles both legs.  Idempotent: a forward already posted is skipped.  A cross is booked like a
    spot cross: the buy leg at its USD mid of the trade date, the sell leg fitted to the same USD base (3900 nets to zero)."""
    services = services if services is not None else ctx.services
    conn = ctx.conn
    row = conn.execute("SELECT * FROM fx_deals WHERE deal_id=?", (deal_id,)).fetchone()
    if row is None:
        raise RejectError("DEAL_UNKNOWN", f"FX deal {deal_id} does not exist")
    if row["kind"] != "FORWARD" or int(row["entry_id"] or 0) != 0:
        return {"status": "DONE", "note": f"{deal_id} already posted", "posted": False, "skipped": True}
    if row["value_date"] > ctx.today and not force:
        raise RejectError("NOT_DUE", f"forward {deal_id} is due {row['value_date']} (today {ctx.today})", {"value_date": row["value_date"]})
    account_id = row["account_id"]
    _account(conn, account_id)
    try:
        inputs = json.loads(row["inputs_json"] or "{}")
    except ValueError:
        inputs = {}
    buy, sell, buy_c, sell_c = row["buy_ccy"], row["sell_ccy"], int(row["buy_c"]), int(row["sell_c"])
    pv = {"account_id": account_id, "buy_ccy": buy, "sell_ccy": sell, "buy_c": buy_c, "sell_c": sell_c, "value_date": row["value_date"], "cross": bool(inputs.get("cross")),
          "rate": row["rate"], "foreign_ccy": inputs.get("foreign_ccy")}
    if pv["cross"]:
        mids = dict(inputs.get("usd_mids_at_trade") or {})
        for c in (buy, sell):
            if mids.get(c) is None:
                mids[c] = str(ctx.fx.rate(c))
        pv["ledger_pieces"] = cross_ledger({buy: buy_c, sell: sell_c}, {c: Decimal(str(mids[c])) for c in (buy, sell)})
    lines, ledger_rates = _deal_lines(account_id, pv)
    memo = (f"FX forward {deal_id} at value {row['value_date']}: SELL {sell} {fmt_money(sell_c)} / BUY {buy} {fmt_money(buy_c)} @ {inputs.get('rate_quoted') or row['rate']}"
            f" (outright dealt {row['trade_date']}; the P&L since then is in the FX result)")
    entry_id = ctx.post(Entry("FX", memo, lines, f"fx:{deal_id}", ref_type="fx_deal", ref_id=deal_id))
    settlements = _settlement_rows_for(ctx, deal_id, account_id, pv)
    inputs["ledger_rates_at_value"] = ledger_rates
    conn.execute("UPDATE fx_deals SET entry_id=?, inputs_json=? WHERE deal_id=?", (entry_id, dumps(inputs), deal_id))
    ctx.event("FX_FORWARD_POSTED", account_id=account_id, ref_type="fx_deal", ref_id=deal_id, entry_id=entry_id, value_date=row["value_date"])
    ctx.touched_accounts.add(account_id)
    return {"status": "DONE", "note": f"forward {deal_id} posted for value {row['value_date']}: {len(settlements)} settlement legs due", "entry_id": entry_id,
            "settlements": settlements, "posted": True}


def h_forward_due(ctx: TxContext, payload: dict) -> dict:
    """``fx.forward_due {deal_id, force?}``: ``post_forward`` (the job's work, also callable by the owner / tests)."""
    deal_id = str(payload.get("deal_id") or payload.get("key") or "")
    if not deal_id:
        raise RejectError("DEAL_REQUIRED", "deal_id is required")
    return post_forward(ctx, deal_id, force=bool(payload.get("force")))


def forward_due_keys(conn, now: int) -> List[str]:
    """Forwards whose value date has come (from 00:30 ET on that date) and whose entry is not posted yet."""
    today = ny_of(int(now)).date()
    cutoff = today if int(now) >= epoch_of(today, FWD_DUE_ET) else today - timedelta(days=1)
    rows = conn.execute("SELECT d.deal_id FROM fx_deals d JOIN accounts a ON a.account_id=d.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                        " WHERE d.kind='FORWARD' AND d.status='PENDING' AND d.entry_id=0 AND d.value_date<=? AND a.status='OPEN' AND l.status='OPEN'"
                        " ORDER BY d.value_date, d.deal_id", (cutoff.isoformat(),)).fetchall()
    return [r["deal_id"] for r in rows]


def _forward_job(job_base):
    class FxForwardDue(job_base):
        """FX_FORWARD_DUE: on the value date (from 00:30 ET) each unposted forward gets its entry at K and its settlement rows;
        it runs just before SETTLE_DUE, which settles both legs in the same pass."""
        name = "FX_FORWARD_DUE"
        kind = "lifecycle.fx_forward_due"
        module = "fx"
        when = "the value date from 00:30 ET (before SETTLE_DUE)"

        def due(self, conn, clock, cal, sessions) -> List[str]:
            return forward_due_keys(conn, int(clock.epoch()))

        def payload(self, key: str) -> dict:
            return {"key": key, "deal_id": key}

        def run(self, ctx: TxContext, key: str, services) -> dict:
            return post_forward(ctx, key, services)
    return FxForwardDue()


# ---- mark-to-market of forwards (OA-09, the NAV overlay hook) --------------------------------------------------------------------
def forward_mtm(services, row, snapshot=None) -> dict:
    """A forward's value now in USD: ``sign x A x (F_now - K)`` in the quote currency at the value date (A = the base amount,
    sign + when the deal buys the base), discounted to today at the quote currency's rate, at that currency's USD mid.
    ``{mtm_c, quality, inputs}``; ``mtm_c`` None when an input is missing (said in ``note``)."""
    r = dict(row)
    try:
        inputs = json.loads(r.get("inputs_json") or "{}")
    except ValueError:
        inputs = {}
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    pair = str(inputs.get("pair") or fx_cross_id(r["buy_ccy"], r["sell_ccy"]))
    base, quote_ccy = pair[:3], pair[3:]
    K = Decimal(str(inputs.get("rate_quoted") or r["rate"]))
    vd = date.fromisoformat(r["value_date"])
    buying_base = r["buy_ccy"] == base
    A = from_c(int(r["buy_c"] if buying_base else r["sell_c"]))
    now = int(services.clock.epoch())
    today = ny_of(now).date()
    F, fin = forward_rate(services, pair, vd, snap)
    note = ""
    if F is None:
        sd = date.fromisoformat(fin.get("spot_date")) if fin.get("spot_date") else None
        if sd is not None and vd <= sd and (fin.get("spot") or {}).get("mid"):
            F, note = qprice(fin["spot"]["mid"]), "value date within the spot period: valued at the spot mid"
        else:
            return {"deal_id": r["deal_id"], "mtm_c": None, "quality": "unavailable", "note": fin.get("note") or "no forward price", "inputs": fin}
    value_q = (A * (F - K)) if buying_base else (A * (K - F))
    rq, iq = rate_for(services, quote_ccy, max(1, (vd - today).days))
    days_left = max(0, (vd - today).days)
    df = Decimal(1)
    if rq is not None and days_left:
        df = (Decimal(1) / (Decimal(1) + rq / Decimal(100) * Decimal(days_left) / Decimal(iq["basis"]))).quantize(Decimal("0.0000000001"))
    session = _session(services, now)
    leg = usd_leg(services, snap, quote_ccy, session, now)
    if leg["rate"] is None:
        return {"deal_id": r["deal_id"], "mtm_c": None, "quality": "unavailable", "note": f"no USD rate for {quote_ccy}", "inputs": fin}
    mtm_c = to_c(value_q * df * leg["rate"])
    quality = "stale" if leg["quality"] == "stale" or (fin.get("spot") or {}).get("quality") == "stale" else "computed"
    return {"deal_id": r["deal_id"], "mtm_c": mtm_c, "mtm": str(from_c(mtm_c)), "quality": quality, "note": note or fin.get("note") or "",
            "inputs": {"pair": pair, "K": str(K), "F_now": str(F), "base_amount": str(A), "buying_base": buying_base, "value_in_quote_at_vd": str(qprice(value_q)),
                       "quote_ccy": quote_ccy, "days_to_value": days_left, "df": str(df), "r_quote": iq, "usd_per_quote": str(leg["rate"]), "forward": fin,
                       "formula": f"{'+' if buying_base else '-'}{A} x ({F} - {K}) {quote_ccy} x DF {df} x {leg['rate']} USD/{quote_ccy}"}}


def forward_overlays(services, conn, account_id: str, snapshot=None) -> List[dict]:
    """The NAV overlay of the account's open forwards (design §13.9: ``(F_now - K) x amount x DF``), one row per unposted
    forward: ``{deal_id, pair, value_date, buy_ccy, buy_c, sell_ccy, sell_c, rate, mtm_c (USD cents | None), quality, note,
    inputs}``.  Accounting adds ``mtm_c`` to USD unrealised when it carries the hook; until the value date nothing is in
    the ledger for a forward."""
    rows = conn.execute("SELECT * FROM fx_deals WHERE account_id=? AND kind='FORWARD' AND status='PENDING' AND entry_id=0 ORDER BY value_date, deal_id",
                        (account_id,)).fetchall()
    out = []
    for r in rows:
        m = forward_mtm(services, r, snapshot)
        out.append({"deal_id": r["deal_id"], "pair": json.loads(r["inputs_json"] or "{}").get("pair"), "value_date": r["value_date"], "buy_ccy": r["buy_ccy"],
                    "buy_c": int(r["buy_c"]), "sell_ccy": r["sell_ccy"], "sell_c": int(r["sell_c"]), "rate": r["rate"], "mtm_c": m.get("mtm_c"),
                    "quality": m.get("quality"), "note": m.get("note"), "inputs": m.get("inputs")})
    return out


# ---- settlement ------------------------------------------------------------------------------------------------------------------
def _settlement_rows(conn, deal_id: str):
    return conn.execute("SELECT * FROM settlements WHERE fx_deal_id=? ORDER BY CASE leg WHEN 'BUY_CCY' THEN 0 ELSE 1 END, settle_id", (deal_id,)).fetchall()


def _open_unfunded(conn, settle_id: str):
    return conn.execute("SELECT id FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND ref_type='settlement' AND ref_id=? AND resolved_ts IS NULL", (settle_id,)).fetchone()


def settle_deal(ctx: TxContext, deal_id: str, force: bool = False, services=None) -> dict:
    """Settle the PENDING legs of one deal (idempotent: SETTLED legs are skipped; each leg's idem key is
    ``settle:{settle_id}``).  A forward not yet posted is posted first (``post_forward``).  Returns
    ``{deal_id, settled: [...], unfunded: [...], skipped, status}``."""
    services = services if services is not None else ctx.services
    conn = ctx.conn
    deal = conn.execute("SELECT * FROM fx_deals WHERE deal_id=?", (deal_id,)).fetchone()
    if deal is None:
        raise RejectError("DEAL_UNKNOWN", f"FX deal {deal_id} does not exist")
    if deal["kind"] == "FORWARD" and int(deal["entry_id"] or 0) == 0:
        post_forward(ctx, deal_id, services, force=force)
        deal = conn.execute("SELECT * FROM fx_deals WHERE deal_id=?", (deal_id,)).fetchone()
    rows = _settlement_rows(conn, deal_id)
    if not rows:
        return {"deal_id": deal_id, "settled": [], "unfunded": [], "skipped": True, "status": deal["status"]}
    if deal["status"] == "SETTLED" and all(r["status"] == "SETTLED" for r in rows):
        return {"deal_id": deal_id, "settled": [], "unfunded": [], "skipped": True, "status": "SETTLED"}
    account_id = deal["account_id"]
    _account(conn, account_id)
    today = ctx.today
    settled, unfunded = [], []
    for r in rows:
        if r["status"] == "SETTLED":
            continue
        if r["due_date"] > today and not force:
            raise RejectError("NOT_DUE", f"settlement {r['settle_id']} is due {r['due_date']} (today {today}); pass force to settle early",
                              {"settle_id": r["settle_id"], "due_date": r["due_date"], "today": today})
        sid, ccy, amt = r["settle_id"], r["currency"], int(r["amount_c"])
        if amt > 0:                                                      # receivable: cash in
            lines = [dr(account_id, "1010", ccy, amt, memo=f"FX {deal_id} received"), cr(account_id, "1250", ccy, amt, memo="receivable settled")]
            memo = f"FX settlement {sid}: receive {ccy} {fmt_money(amt)} ({deal_id}, value {r['due_date']})"
            funding = None
        else:                                                            # payable: fund first, then cash out
            need = -amt
            try:
                funding = fund(ctx, account_id, ccy, need, "settlement", sid, None, immediate=True, services=services)
            except ConflictError as e:
                if e.code != "INSUFFICIENT_FUNDS":
                    raise
                if _open_unfunded(conn, sid) is None:
                    ctx.attention("ERROR", "fx", "SETTLEMENT_UNFUNDED",
                                  f"{account_id}: {ccy} {fmt_money(need)} due {r['due_date']} for FX deal {deal_id} cannot be funded ({e.message}); the leg stays pending and is retried",
                                  dict(e.details, account_id=account_id, ref_type="settlement", ref_id=sid, deal_id=deal_id, auto_resolve=1))
                unfunded.append({"settle_id": sid, "currency": ccy, "amount_c": need, "reason": e.code, "details": e.details})
                continue
            lines = [dr(account_id, "2350", ccy, need, memo="payable settled"), cr(account_id, "1010", ccy, need, memo=f"FX {deal_id} delivered")]
            memo = f"FX settlement {sid}: deliver {ccy} {fmt_money(need)} ({deal_id}, value {r['due_date']})"
        entry_id = ctx.post(Entry("SETTLE", memo, lines, f"settle:{sid}", ref_type="settlement", ref_id=sid))
        conn.execute("UPDATE settlements SET status='SETTLED', settled_ts=?, entry_id=? WHERE settle_id=?", (ctx.ts, entry_id, sid))
        conn.execute("UPDATE attention SET resolved_ts=? WHERE code='SETTLEMENT_UNFUNDED' AND ref_type='settlement' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, sid))
        auto_resolve_shortfall(ctx, account_id, ccy)
        ctx.event("SETTLED", account_id=account_id, ref_type="settlement", ref_id=sid, deal_id=deal_id, currency=ccy, amount_c=amt, entry_id=entry_id,
                  funding=funding.to_dict() if funding else None)
        settled.append({"settle_id": sid, "leg": r["leg"], "currency": ccy, "amount_c": amt, "entry_id": entry_id, "funding": funding.to_dict() if funding else None})
    status = "SETTLED" if all(x["status"] == "SETTLED" for x in _settlement_rows(conn, deal_id)) else "PENDING"
    if status == "SETTLED":
        conn.execute("UPDATE fx_deals SET status='SETTLED', settled_ts=? WHERE deal_id=?", (ctx.ts, deal_id))
        ctx.event("FX_SETTLED", account_id=account_id, ref_type="fx_deal", ref_id=deal_id, buy_ccy=deal["buy_ccy"], sell_ccy=deal["sell_ccy"],
                  buy_c=int(deal["buy_c"]), sell_c=int(deal["sell_c"]))
    ctx.touched_accounts.add(account_id)
    return {"deal_id": deal_id, "settled": settled, "unfunded": unfunded, "skipped": False, "status": status}


def h_settle(ctx: TxContext, payload: dict) -> dict:
    """``fx.settle {deal_id | settle_id, force?}`` (actor lifecycle: SETTLE_DUE, or the owner through the API)."""
    deal_id = payload.get("deal_id")
    if not deal_id and payload.get("settle_id"):
        row = ctx.conn.execute("SELECT fx_deal_id FROM settlements WHERE settle_id=?", (payload["settle_id"],)).fetchone()
        if row is None or not row["fx_deal_id"]:
            raise RejectError("SETTLEMENT_UNKNOWN", f"{payload['settle_id']} is not an FX settlement leg")
        deal_id = row["fx_deal_id"]
    if not deal_id:
        raise RejectError("DEAL_REQUIRED", "deal_id or settle_id is required")
    return settle_deal(ctx, str(deal_id), bool(payload.get("force")))


def due_deals(conn, today: str):
    """Deal ids with a PENDING leg due on or before ``today`` (for WP-08's SETTLE_DUE keys)."""
    rows = conn.execute("SELECT DISTINCT fx_deal_id FROM settlements WHERE fx_deal_id IS NOT NULL AND status='PENDING' AND due_date <= ? ORDER BY fx_deal_id", (today,)).fetchall()
    return [r["fx_deal_id"] for r in rows]


# ---- currency funding for WP-26 (XCCY notional exchanges) ----------------------------------------------------------------------
def fund_ccy(ctx: TxContext, account: str, ccy: str, need_c: int, link, *, services=None) -> FundingResult:
    """Make ``need_c`` of ``ccy`` SETTLED in ``account``: its own 1010 balance in that currency, else exactly the shortfall
    swept from Treasury's cash in that currency (one linked SWEEP_IN; L19), else 409 ``INSUFFICIENT_FUNDS_CCY`` with the
    hint "buy <ccy> <shortfall> with Convert".  ``link`` is ``(link_type, link_id)`` or ``"type:id"``."""
    from . import treasury
    ccy = _ccy(ccy)
    need_c = int(need_c)
    if isinstance(link, (tuple, list)):
        link_type, link_id = str(link[0]), str(link[1])
    else:
        link_type, _, link_id = str(link).partition(":")
    if need_c <= 0:
        return FundingResult(0, 0, 0, funded=True, source="none")
    _account(ctx.conn, account)
    own = balance(ctx.conn, account, "1010", ccy)
    shortfall = need_c - own
    if shortfall <= 0:
        return FundingResult(need_c, own, 0, funded=True, source="own")
    res = FundingResult(need_c, own, shortfall)
    try:
        tid = treasury.sweep_in(ctx, account, ccy, shortfall, link_type, link_id, shortfall)
    except ConflictError as e:
        if e.code != "INSUFFICIENT_FUNDS":
            raise
        t_avail = int(e.details.get("treasury_available_c", 0))
        raise ConflictError("INSUFFICIENT_FUNDS_CCY",
                            f"{account} needs {ccy} {fmt_money(need_c)} settled: it holds {fmt_money(own)}, Treasury has {ccy} {fmt_money(max(t_avail, 0))} available;"
                            f" buy {ccy} {fmt_money(shortfall)} with Convert",
                            {"need_c": need_c, "own_settled_c": own, "shortfall_c": shortfall, "treasury_available_c": t_avail, "currency": ccy, "account_id": account,
                             "link_type": link_type, "link_id": link_id, "hint": f"buy {ccy} {fmt_money(shortfall)} with Convert"}) from e
    res.sweep_id, res.sweep_c, res.source = tid, shortfall, "sweep"
    auto_resolve_shortfall(ctx, account, ccy)
    return res


# ---- C17: the explicit USD -> currency conversion of a non-USD need (orders, bonds) -----------------------------------------------
def conversion_plan(services, snapshot, account_id: str, ccy: str, need_c: int, settle_date, conn=None) -> Optional[dict]:
    """C17 (pure): the USD -> ``ccy`` spot that would cover ``need_c`` of ``ccy`` for a trade settling on ``settle_date``:
    ``{pair, buy_ccy, sell_ccy USD, buy_c, usd_c, rate, rate_quoted, mid, spread_cost_c, value_date, settle_date, executable,
    reason, text, quote}``.  None for USD or a need of 0.  Not executable (with the reason) when the pair has no fresh quote
    in the FX session or its spot value date falls after the settle date."""
    ccy = _ccy(ccy)
    need_c = int(need_c)
    if ccy == "USD" or need_c <= 0:
        return None
    sd = settle_date if isinstance(settle_date, date) else (date.fromisoformat(str(settle_date)) if settle_date else None)
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    try:
        pv = quote(services, snap, account_id, ccy, "USD", max(need_c, MIN_DEAL_C), ccy, conn=conn, now=int(services.clock.epoch()))
    except RejectError as e:
        return {"pair": fx_pair_id(ccy), "buy_ccy": ccy, "sell_ccy": "USD", "buy_c": need_c, "executable": False, "reason": f"{e.code}: {e.message}"}
    out = {"pair": pv["pair"], "buy_ccy": ccy, "sell_ccy": "USD", "buy_c": pv.get("buy_c", need_c), "usd_c": pv.get("sell_c"), "rate": pv.get("rate"),
           "rate_quoted": pv.get("rate_quoted"), "mid": pv.get("mid"), "spread_cost_c": pv.get("spread_cost_c"), "value_date": pv.get("value_date"),
           "settle_date": sd.isoformat() if sd else None, "executable": bool(pv.get("ok")), "reason": "" if pv.get("ok") else ((pv.get("blockers") or [{}])[0].get("message") or ""),
           "quote": pv.get("quote")}
    if sd is not None and pv.get("value_date") and pv["value_date"] > sd.isoformat():
        out["executable"] = False
        out["reason"] = (f"FX_VALUE_DATE: {pv['pair']} spot settles {pv['value_date']}, after this trade's settlement {sd.isoformat()}: buy {ccy} on the FX desk"
                         f" first (value on or before {sd.isoformat()})")
    if out.get("usd_c") is not None:
        out["text"] = (f"BUY {ccy} {fmt_money(out['buy_c'])} / SELL USD {fmt_money(int(out['usd_c']))} at {out['rate_quoted']} ({pv['pair']} mid {out['mid']},"
                       f" 0.2 pip against you, spread cost USD {fmt_money(int(out.get('spread_cost_c') or 0))}), value {out['value_date']}")
    return out


def convert_for(ctx: TxContext, account_id: str, ccy: str, need_c: int, link_type: str, link_id: str, settle_date, services=None) -> dict:
    """C17: book the linked USD -> ``ccy`` spot deal that delivers ``need_c`` of ``ccy`` into ``account_id`` by
    ``settle_date`` (409 FX_VALUE_DATE when the pair's spot value date is later; 409 QUOTE_STALE / SESSION_CLOSED when the pair
    is not executable).  Returns ``{deal_id, pair, buy_c, usd_c, rate, rate_quoted, mid, spread_cost_c, value_date, entry_id,
    settlements, memo}``; the USD side (``usd_c``, payable on the value date) is funded by the caller's cascade
    (``execution.fund``).  Never refuses for the USD side itself (SETTLEMENT_UNFUNDABLE is the desk's rule)."""
    services = services if services is not None else ctx.services
    ccy = _ccy(ccy)
    need_c = int(need_c)
    if ccy == "USD" or need_c <= 0:
        raise RejectError("AMOUNT", "a conversion needs a non-USD currency and a positive amount")
    pv = quote(services, ctx.marks, account_id, ccy, "USD", max(need_c, MIN_DEAL_C), ccy, conn=ctx.conn, now=ctx.ts)
    if pv["blockers"]:
        b = pv["blockers"][0]
        raise ConflictError(b["code"], f"the USD -> {ccy} conversion cannot be dealt: {b['message']}", dict(b.get("details") or {}, link_type=link_type, link_id=link_id))
    sd = settle_date if isinstance(settle_date, date) else (date.fromisoformat(str(settle_date)) if settle_date else None)
    if sd is not None and pv["value_date"] > sd.isoformat():
        raise ConflictError("FX_VALUE_DATE", f"{pv['pair']} spot settles {pv['value_date']}, after the trade's settlement {sd.isoformat()}: buy {ccy} on the FX desk first",
                            {"pair": pv["pair"], "value_date": pv["value_date"], "settle_date": sd.isoformat(), "currency": ccy})
    link = {"type": link_type, "id": link_id, "text": f"conversion for {link_type} {link_id}"}
    out = _book_spot(ctx, pv, link=link, title="FX conversion")
    d = out["deal"]
    return {"deal_id": d["deal_id"], "pair": d["pair"], "buy_ccy": ccy, "buy_c": int(d["buy_c"]), "usd_c": int(d["sell_c"]), "rate": d["rate"], "rate_quoted": d["rate_quoted"],
            "mid": d["mid"], "spread_cost_c": d["spread_cost_c"], "value_date": d["value_date"], "entry_id": out["entry_id"], "settlements": out["settlements"],
            "memo": out["memo"]}


# ---- C25: foreign venue taxes ---------------------------------------------------------------------------------------------------------
def venue_fee_c(inst, side, qty: int, gross_c: int) -> Tuple[int, str]:
    """C25: the venue tax of a foreign-listing fill in the instrument's currency cents, ``(cents, note)`` - ``VENUE_TAXES``
    (assumed - verify current rates).  0 for ETFs, other venues, exempt listings and sales where the tax is on purchases."""
    if inst is None or gross_c is None or int(gross_c) <= 0:
        return 0, ""
    ysym = getattr(inst, "yahoo_symbol", None)
    suffix = venue_suffix(ysym) or ((getattr(inst, "spec", None) or {}).get("venue_suffix") if isinstance(getattr(inst, "spec", None), dict) else None)
    rule = VENUE_TAXES.get(str(suffix or ""))
    if rule is None or getattr(inst, "kind", "") == "ETF":
        return 0, ""
    if str(ysym or "").upper() in VENUE_TAX_EXEMPT:
        return 0, ""
    purchase = str(side or "").upper() in BUY_SIDES
    if rule["purchases_only"] and not purchase:
        return 0, ""
    amount = from_c(int(gross_c)) * rule["rate"]
    if rule["round"] == "unit_up":
        cents = int(amount.quantize(Decimal(1), rounding=ROUND_CEILING)) * 100
    else:
        cents = to_c(amount)
    if cents <= 0:
        return 0, ""
    ccy = getattr(inst, "currency", "")
    return cents, f"{rule['label']} {rule['rate'] * 100:.2f} % {'on the purchase' if rule['purchases_only'] else 'on this side'}: {ccy} {fmt_money(cents)} ({VENUE_TAX_NOTE})"


def venue_fee_table() -> List[dict]:
    """The venue tax table for the UI (Foreign listings tab): venue, rate, rule, and the 'assumed' label."""
    from .instruments import FOREIGN_VENUES
    return [{"suffix": k, "venue": FOREIGN_VENUES.get(k, k), "tax": v["label"], "rate_pct": str((v["rate"] * 100).quantize(Decimal("0.01"))), "rule": v["text"],
             "note": VENUE_TAX_NOTE} for k, v in VENUE_TAXES.items()] + [{"suffix": "*", "venue": "every other venue", "tax": "none", "rate_pct": "0.00",
                                                                           "rule": "no venue tax charged in this terminal", "note": VENUE_TAX_NOTE}]


# ---- views: currency positions (OA-24), pairs (OA-14), deals --------------------------------------------------------------------------
def positions_view(services, account_id: str, snapshot=None) -> dict:
    """GET /api/fx/positions: per currency the settled cash, pending FX (spot receivable - payable), pending trade settlements,
    forwards by value date (with their computed mark-to-market), the net position and its USD equivalent at the mid."""
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    now = int(services.clock.epoch())
    session = _session(services, now)
    with services.db.reader() as conn:
        _account(conn, account_id)
        bal: Dict[Tuple[str, str], int] = {}
        for r in conn.execute("SELECT gl, currency, SUM(balance_c) b FROM gl_balances WHERE account_id=? AND gl IN ('1010','1250','2350','1200','2100') GROUP BY gl, currency",
                              (account_id,)).fetchall():
            bal[(r["gl"], r["currency"])] = int(r["b"] or 0)
        deals = [dict(r) for r in conn.execute("SELECT * FROM fx_deals WHERE account_id=? AND status='PENDING' ORDER BY value_date, deal_id", (account_id,)).fetchall()]
        overlays = {o["deal_id"]: o for o in forward_overlays(services, conn, account_id, snap)}
    ccys = sorted({c for (_g, c) in bal} | {d["buy_ccy"] for d in deals} | {d["sell_ccy"] for d in deals}, key=lambda c: (c != "USD", CCY_ORDER.index(c) if c in CCY_ORDER else 99, c))
    rows = []
    tot_usd = 0
    missing = []
    for c in ccys:
        settled = bal.get(("1010", c), 0)
        fx_in, fx_out = bal.get(("1250", c), 0), -bal.get(("2350", c), 0)
        tr_in, tr_out = bal.get(("1200", c), 0), -bal.get(("2100", c), 0)
        fwds = []
        for d in deals:
            if d["kind"] != "FORWARD" or int(d["entry_id"] or 0) != 0 or c not in (d["buy_ccy"], d["sell_ccy"]):
                continue
            amt = int(d["buy_c"]) if d["buy_ccy"] == c else -int(d["sell_c"])
            o = overlays.get(d["deal_id"]) or {}
            fwds.append({"deal_id": d["deal_id"], "value_date": d["value_date"], "amount_c": amt, "against": d["sell_ccy"] if d["buy_ccy"] == c else d["buy_ccy"],
                         "rate": d["rate"], "mtm_c": o.get("mtm_c"), "quality": o.get("quality")})
        fwd_net = sum(f["amount_c"] for f in fwds)
        net = settled + (fx_in - fx_out) + (tr_in - tr_out) + fwd_net
        leg = usd_leg(services, snap, c, session, now)
        usd_c = None if leg["rate"] is None else fx_c(net, leg["rate"])
        if usd_c is None:
            missing.append(c)
        else:
            tot_usd += usd_c
        rows.append({"currency": c, "name": CCY_NAMES.get(c, c), "settled_c": settled, "pending_fx_in_c": fx_in, "pending_fx_out_c": fx_out,
                     "pending_fx_c": fx_in - fx_out, "pending_trades_c": tr_in - tr_out, "forwards": fwds, "forwards_net_c": fwd_net, "net_c": net,
                     "usd_rate": None if leg["rate"] is None else str(leg["rate"]), "usd_rate_quality": leg["quality"], "usd_rate_pair": leg["pair"],
                     "usd_rate_quote_time": leg["quote_time"], "usd_equiv_c": usd_c})
    fwd_mtm = [o["mtm_c"] for o in overlays.values() if o.get("mtm_c") is not None]
    open_deals = []
    for d in deals:
        o = overlays.get(d["deal_id"]) or {}
        try:
            inp = json.loads(d.get("inputs_json") or "{}")
        except ValueError:
            inp = {}
        open_deals.append({"deal_id": d["deal_id"], "kind": d["kind"], "pair": inp.get("pair") or fx_cross_id(d["buy_ccy"], d["sell_ccy"]), "buy_ccy": d["buy_ccy"],
                           "buy_c": int(d["buy_c"]), "sell_ccy": d["sell_ccy"], "sell_c": int(d["sell_c"]), "rate_quoted": inp.get("rate_quoted") or d["rate"],
                           "trade_date": d["trade_date"], "value_date": d["value_date"], "tenor": inp.get("tenor"), "posted": int(d["entry_id"] or 0) != 0,
                           "mtm_c": o.get("mtm_c"), "mtm_quality": o.get("quality"), "mtm_note": o.get("note"), "link": inp.get("link")})
    return {"account_id": account_id, "as_of": now, "currencies": rows, "usd_equiv_total_c": tot_usd, "usd_missing": missing,
            "forwards_mtm_c": sum(fwd_mtm) if fwd_mtm else 0, "open_deals": open_deals,
            "note": ("net = settled + pending FX deals + pending trade settlements + forwards by value date; USD at the mid. A forward moves no cash before its"
                     " value date: its mark-to-market (computed, CIP) is shown per deal and realised at the value date")}


def _ordered_pairs(insts) -> list:
    def key(i):
        p = i.instrument_id
        cross = "USD" not in (p[:3], p[3:])
        return (cross, MAJOR_PAIRS.index(p) if p in MAJOR_PAIRS else 99, FX_CROSSES.index(p) if p in FX_CROSSES else 99, p)
    return sorted(insts, key=key)


def _subscribe(services, ids: List[str], ttl: int, priority: str) -> None:
    cache = getattr(services, "cache", None)
    if cache is None or not ids:
        return
    try:
        cache.subscribe(ids, ttl, priority)
    except Exception:   # noqa: BLE001 - an unknown class (an older cache): the default page class
        try:
            cache.subscribe(ids, ttl, "PAGE")
        except Exception:   # noqa: BLE001
            pass


def _subscribe_pairs(services, buy_ccy, sell_ccy) -> None:
    """A desk preview keeps the pair it prices fresh (its own print and the USD legs of a cross)."""
    try:
        pi = pair_info(buy_ccy, sell_ccy)
    except RejectError:
        return
    ids = [pi["pair"]] + [fx_pair_id(c) for c in (pi["base"], pi["quote"]) if c != "USD"]
    master = services.instruments
    _subscribe(services, [i for i in dict.fromkeys(ids) if master.get(i) is not None], 120, "PAGE")


def pairs_view(services, account_id: Optional[str] = None) -> dict:
    """GET /api/fx/pairs: every FX pair of the master (the USD pairs first, then the crosses) with its quote - a cross shows
    its own print when fresh, else the mid derived from its USD pairs, labelled - the currencies the desk can deal (each
    with its USD rate and whether forwards are priced), the FX session and the account's net position per pair."""
    master = services.instruments
    insts = _ordered_pairs([i for i in master.all() if i.kind == "FX" and i.status == "ACTIVE"])
    usd_ids = [i.instrument_id for i in insts if "USD" in (i.instrument_id[:3], i.instrument_id[3:])]
    cross_ids = [i.instrument_id for i in insts if i.instrument_id not in usd_ids]
    _subscribe(services, usd_ids, 90, "PAGE")
    _subscribe(services, cross_ids, 1800, "BROWSE")
    now = int(services.clock.epoch())
    snap = services.marks.snapshot()
    session = _session(services, now)
    pos = {}
    if account_id:
        try:
            pv = positions_view(services, account_id, snap)
            pos = {r["currency"]: r for r in pv["currencies"]}
        except RejectError:
            pos = {}
    cache = getattr(services, "cache", None)
    recs = cache.get_many([i.instrument_id for i in insts]) if cache is not None else {}
    td = fx_trade_date(services, now)
    rows = []
    for i in insts:
        p = i.instrument_id
        base, quote_ccy = p[:3], p[3:]
        pm = pair_mid(services, snap, p, session, now)
        rec = recs.get(p)
        q = None
        if rec is not None:
            q = {"last": None if rec.last is None else str(rec.last), "prev_close": None if rec.prev_close is None else str(rec.prev_close), "quality": rec.quality,
                 "quote_time": rec.quote_time, "fetched_at": rec.fetched_at, "source": rec.source, "delay_label": rec.delay_label, "error": rec.error,
                 "age_s": None if rec.quote_time is None else max(0, now - int(rec.quote_time))}
        pos_ccy = base if base != "USD" else quote_ccy
        prow = pos.get(pos_ccy) or {}
        rows.append({"instrument_id": p, "symbol": p, "name": i.name, "base": base, "quote": quote_ccy, "cross": "USD" not in (base, quote_ccy),
                     "pip": str(pip_of(p)), "quote_rec": q, "mid": None if pm["mid"] is None else str(pm["mid"]), "mid_basis": pm["basis"],
                     "mid_quality": pm["quality"], "mid_quote_time": pm["quote_time"], "mid_source": pm["source"], "executable": pm["executable"],
                     "reason": pm["reason"], "bid": None if pm["mid"] is None else str(dealt_rate(pm["mid"], p, False)),
                     "ask": None if pm["mid"] is None else str(dealt_rate(pm["mid"], p, True)), "spot_date": spot_date(services, p, td).isoformat(),
                     "position_ccy": pos_ccy, "position_c": prow.get("net_c"), "forwards": all(tables.POLICY_INDEX.get(c) for c in (base, quote_ccy))})
    ccys = ["USD"] + sorted({c for i in insts for c in (i.instrument_id[:3], i.instrument_id[3:]) if c != "USD"} | {c for c in CCY_NAMES if c != "USD"},
                            key=lambda c: (CCY_ORDER.index(c) if c in CCY_ORDER else 99, c))
    currencies = []
    for c in ccys:
        leg = usd_leg(services, snap, c, session, now)
        currencies.append({"ccy": c, "name": CCY_NAMES.get(c, c), "pair": leg["pair"], "usd_rate": None if leg["rate"] is None else str(leg["rate"]),
                           "quality": leg["quality"], "forwards": bool(tables.POLICY_INDEX.get(c)), "rate_series": tables.POLICY_INDEX.get(c),
                           "monthly_proxy": tables.POLICY_INDEX.get(c) in tables.POLICY_INDEX_MONTHLY})
    return {"pairs": rows, "currencies": currencies, "trade_date": td.isoformat(),
            "session": {"group": SESSION_GROUP, "open": session.open, "phase": session.phase, "next_open": session.next_open, "closes_at": session.closes_at,
                        "note": session.note},
            "spread_pips": str(_FEE["spread_pips"]), "conventions": tables.CONVENTIONS.get("FX_SPOT", ""),
            "note": ("USD pairs refresh every 30 s while this tab is open; a cross shows its own Yahoo print when fresh, else the mid derived from its two USD pairs"
                     " (labelled). Deal at the mid +/- 0.2 pip; spot settles T+2 (USD/CAD T+1).")}


def _compact(rec, now: int) -> Optional[dict]:
    if rec is None:
        return None
    return {"last": None if rec.last is None else str(rec.last), "prev_close": None if rec.prev_close is None else str(rec.prev_close), "quality": rec.quality,
            "quote_time": rec.quote_time, "fetched_at": rec.fetched_at, "source": rec.source, "delay_label": rec.delay_label, "error": rec.error,
            "age_s": None if rec.quote_time is None else max(0, int(now) - int(rec.quote_time)), "day_volume": rec.day_volume}


def listings_view(services, account_id: str) -> dict:
    """GET /api/fx/listings: the foreign listings this account trades (held / working / watched first, then by venue): the quote
    as Yahoo prints it with its unit (London in pence: ``quote_unit`` GBp, ``price_scale`` 0.01), the venue session from the
    record's Yahoo trading period, settlement, the venue tax rule, support (FULL, or LIMITED with the reason) and the
    position.  Held / working / watched rows are refreshed fast, the rest on the slow list budget."""
    from .instruments import EQUITY_KINDS, FOREIGN_GROUP
    master = services.instruments
    with services.db.reader() as conn:
        acct = _account(conn, account_id)
        held = {r[0]: int(r[1]) for r in conn.execute("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (account_id,)).fetchall()}
        working = {r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM orders WHERE account_id=? AND status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')",
                                              (account_id,)).fetchall()}
        watched = [r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM watchlists WHERE account_id=? ORDER BY position", (account_id,)).fetchall()]
    akind = account_kind_for(acct)
    listed = [i for i in master.all() if i.kind in EQUITY_KINDS and i.status == "ACTIVE" and i.session_group == FOREIGN_GROUP and master.tradeable_in(i, akind)]
    by_id = {i.instrument_id: i for i in listed}
    first = []
    for iid in list(held) + sorted(working) + watched:
        inst = by_id.get(iid) or master.get(iid)
        if inst is not None and inst.session_group == FOREIGN_GROUP and inst.instrument_id not in {x.instrument_id for x in first}:
            first.append(inst)
    rest = sorted((i for i in listed if i.instrument_id not in {x.instrument_id for x in first}),
                  key=lambda i: ((i.spec or {}).get("venue") or "zz", i.symbol))
    insts = first + rest
    fast = [i.instrument_id for i in first]
    slow = [i.instrument_id for i in rest]
    _subscribe(services, [i for i in fast if master.get(i) is not None and master.get(i).yahoo_symbol], 120, "PAGE")
    _subscribe(services, [i for i in slow if master.get(i) is not None and master.get(i).yahoo_symbol], 1800, "BROWSE")
    cache = getattr(services, "cache", None)
    now = int(services.clock.epoch())
    recs = cache.get_many([i.instrument_id for i in insts]) if cache is not None else {}
    rows = []
    for i in insts:
        spec = i.spec or {}
        rec = recs.get(i.instrument_id)
        ps = getattr(rec, "provider_session", None) if rec is not None else None
        try:
            st = services.sessions.state(FOREIGN_GROUP, now, foreign={"open_ts": int(ps[0]), "close_ts": int(ps[1])} if ps else None)
            sess = {"open": st.open, "phase": st.phase, "next_open": st.next_open, "closes_at": st.closes_at, "note": st.note}
        except Exception:   # noqa: BLE001
            sess = {"open": False, "phase": "UNKNOWN", "next_open": None, "closes_at": None, "note": "no session information yet"}
        suffix = spec.get("venue_suffix") or venue_suffix(i.yahoo_symbol)
        tax = VENUE_TAXES.get(str(suffix or ""))
        exempt = str(i.yahoo_symbol or "").upper() in VENUE_TAX_EXEMPT or i.kind == "ETF"
        rows.append({"instrument_id": i.instrument_id, "symbol": i.symbol, "name": i.name, "kind": i.kind, "currency": i.currency, "price_scale": str(i.price_scale),
                     "quote_unit": spec.get("quote_unit"), "yahoo_symbol": i.yahoo_symbol, "venue": spec.get("venue"), "venue_suffix": suffix,
                     "support": i.support, "limited_reason": spec.get("limited_reason"), "settle_days": i.settle_days, "quote": _compact(rec, now), "session": sess,
                     "venue_tax": None if tax is None else {"label": tax["label"], "rate_pct": str((tax["rate"] * 100).quantize(Decimal("0.01"))), "rule": tax["text"],
                                                             "exempt": VENUE_TAX_EXEMPT.get(str(i.yahoo_symbol or "").upper()) or ("ETFs are exempt" if i.kind == "ETF" else None) if exempt else None},
                     "position": held.get(i.instrument_id), "working": i.instrument_id in working, "watched": i.instrument_id in watched,
                     "fast": i.instrument_id in fast})
    return {"account_id": account_id, "rows": rows, "venue_fees": venue_fee_table(), "as_of": now,
            "note": ("orders trade in the venue's own session (Yahoo trading period) and settle T+2 in the listing's currency (T+1 in India and Canada);"
                     " when the account and Treasury lack that currency the order preview shows the USD conversion, or buy it first on the FX spot tab."
                     " Held, working and watched rows refresh fast, the others on the slow list budget.")}


def deals_view(services, account_id: str, status: Optional[str] = None, limit: int = 50) -> List[dict]:
    """GET /api/fx/deals: the account's FX deals newest first (``status`` open = PENDING), forwards with their mark-to-market."""
    with services.db.reader() as conn:
        _account(conn, account_id)
        sql = "SELECT * FROM fx_deals WHERE account_id=?"
        params: list = [account_id]
        if status in ("open", "PENDING"):
            sql += " AND status='PENDING'"
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY trade_ts DESC, deal_id DESC LIMIT ?", (*params, int(limit))).fetchall()]
    snap = services.marks.snapshot()
    out = []
    for d in rows:
        try:
            inp = json.loads(d.get("inputs_json") or "{}")
        except ValueError:
            inp = {}
        m = forward_mtm(services, d, snap) if d["kind"] == "FORWARD" and d["status"] == "PENDING" and int(d["entry_id"] or 0) == 0 else {}
        out.append({"deal_id": d["deal_id"], "kind": d["kind"], "pair": inp.get("pair") or fx_cross_id(d["buy_ccy"], d["sell_ccy"]), "buy_ccy": d["buy_ccy"],
                    "buy_c": int(d["buy_c"]), "sell_ccy": d["sell_ccy"], "sell_c": int(d["sell_c"]), "rate_quoted": inp.get("rate_quoted") or d["rate"],
                    "mid": d["mid"], "spread_cost_c": int(d["spread_cost_c"] or 0), "trade_date": d["trade_date"], "value_date": d["value_date"], "status": d["status"],
                    "tenor": inp.get("tenor"), "points": inp.get("points"), "link": inp.get("link"), "mtm_c": m.get("mtm_c"), "mtm_quality": m.get("quality"),
                    "mtm_inputs": m.get("inputs"), "trade_ts": d["trade_ts"]})
    return out


# ---- C5: the instrument page of an FX pair ---------------------------------------------------------------------------------------------
def instrument_extra(rt, inst, now: int) -> dict:
    snap = rt.marks.snapshot()
    session = _session(rt, now)
    pm = pair_mid(rt, snap, inst.instrument_id, session, now)
    td = fx_trade_date(rt, now)
    p = inst.instrument_id
    base, quote_ccy = p[:3], p[3:]
    rb, ib = rate_for(rt, base)
    rq, iq = rate_for(rt, quote_ccy)
    spec_view = [{"label": "pair", "value": f"{p}: {quote_ccy} per 1 {base}"}, {"label": "pip", "value": str(pip_of(p))},
                 {"label": "deal rate", "value": f"mid +/- {_FEE['spread_pips']} pip against you"},
                 {"label": "spot value", "value": f"T+{fx_spot_days(p)} US business days ({spot_date(rt, p, td).isoformat()} for trade date {td.isoformat()})"},
                 {"label": "mid source", "value": pm["source"] or "n/a"},
                 {"label": f"{base} rate", "value": ib.get("label") or ib.get("note") or "n/a"},
                 {"label": f"{quote_ccy} rate", "value": iq.get("label") or iq.get("note") or "n/a"},
                 {"label": "forwards", "value": "priced by covered interest parity (FX forwards tab)" if (rb is not None and rq is not None) else "no forward price: " + str((ib if rb is None else iq).get("note"))}]
    return {"headline": {"value": None if pm["mid"] is None else str(pm["mid"]), "quality": pm["quality"], "as_of": pm["quote_time"], "source": pm["source"],
                         "note": "" if pm["executable"] else str(pm["reason"]).lower().replace("_", " ")},
            "spec_view": spec_view, "actions": [{"label": "Deal on the FX desk", "ticket_kind": "FX", "instrument_id": p}]}


# ---- C1: routes ----------------------------------------------------------------------------------------------------------------------------
def register_routes(router) -> None:
    """GET /api/fx/pairs, /api/fx/forwards?pair=&value_date=, /api/fx/positions?account_id=, /api/fx/deals?account_id=&status=,
    /api/fx/listings?account_id=, /api/fx/venue_fees; POST /api/fx/preview {kind SPOT | FORWARD, tenor, value_date, ...},
    /api/fx/deal, /api/fx/spot, /api/fx/forward."""
    from .api import ApiError, run_command

    def _acct(req) -> str:
        aid = req.q("account_id") or req.b("account_id")
        if not aid:
            raise ApiError(400, "ACCOUNT_REQUIRED", "account_id is required")
        return str(aid)

    def pairs(req):
        return pairs_view(req.rt, req.q("account_id"))

    def forwards(req):
        pair = str(req.q("pair") or "").upper()
        if len(pair) != 6:
            raise ApiError(400, "PAIR_REQUIRED", "pair is required (e.g. EURUSD)")
        for c in (pair[:3], pair[3:]):
            if c != "USD":
                _subscribe(req.rt, [fx_pair_id(c)], 120, "PAGE")
        if req.rt.instruments.get(pair) is not None:
            _subscribe(req.rt, [pair], 120, "PAGE")
        return forward_grid(req.rt, pair, value_date=req.q("value_date"))

    def positions(req):
        return positions_view(req.rt, _acct(req))

    def deals(req):
        return deals_view(req.rt, _acct(req), req.q("status"), req.q_int("limit", 50, 1, 500))

    def fees(req):
        return {"rows": venue_fee_table(), "note": VENUE_TAX_NOTE}

    def listings(req):
        return listings_view(req.rt, _acct(req))

    def preview(req):
        rt = req.rt
        aid = _acct(req)
        kind = str(req.b("kind") or "SPOT").upper()
        buy, sell = str(req.b("buy_ccy") or "").upper(), str(req.b("sell_ccy") or "").upper()
        amount_ccy = str(req.b("amount_ccy") or buy).upper()
        if kind == "FORWARD":
            return forward_preview(rt, aid, buy, sell, req.amount_c(), amount_ccy, req.b("tenor"), req.b("value_date"), subscribe=True)
        if kind != "SPOT":
            raise ApiError(400, "KIND", f"kind {kind!r}: SPOT or FORWARD")
        return spot_preview(rt, aid, buy, sell, req.amount_c(), amount_ccy, subscribe=True)

    def _payload(req) -> dict:
        return {k: v for k, v in (req.body or {}).items() if k != "request_id"}

    def deal(req):
        kind = str(req.b("kind") or "SPOT").upper()
        return run_command(req, "fx.forward" if kind == "FORWARD" else "fx.spot", _payload(req))

    def spot(req):
        return run_command(req, "fx.spot", _payload(req))

    def forward(req):
        return run_command(req, "fx.forward", _payload(req))

    router.add("GET", r"/api/fx/pairs", pairs)
    router.add("GET", r"/api/fx/forwards", forwards)
    router.add("GET", r"/api/fx/positions", positions)
    router.add("GET", r"/api/fx/deals", deals)
    router.add("GET", r"/api/fx/venue_fees", fees)
    router.add("GET", r"/api/fx/listings", listings)
    router.add("POST", r"/api/fx/preview", preview, mutating=False)
    router.add("POST", r"/api/fx/deal", deal)
    router.add("POST", r"/api/fx/spot", spot)
    router.add("POST", r"/api/fx/forward", forward)


# ---- what is still not offered (Settings > Unsupported) --------------------------------------------------------------------------------
def unsupported() -> List[dict]:
    no_rate = sorted(c for c in CCY_NAMES if c != "USD" and c not in tables.POLICY_INDEX)
    return [{"item": "FX forwards in " + ", ".join(no_rate), "state": "LIMITED",
             "wording": "no free policy-rate series for these currencies: spot only, no forward price (never a defaulted rate)", "where": "FX forwards tab"},
            {"item": "FX swaps, non-deliverable forwards and listed FX options", "state": "NOT_OFFERED",
             "wording": "deal a spot and an opposite forward instead; FX options are OTC with a prime broker", "where": "FX desk"},
            {"item": "Foreign venue holidays and currency holidays", "state": "APPROXIMATED",
             "wording": "value dates and foreign settlement use the US business-day calendar; a venue's next open after its close is estimated from today's hours",
             "where": "FX desk, foreign listings"},
            {"item": "Foreign venue taxes", "state": "ASSUMED", "wording": "UK SDRT 0.50 %, Hong Kong stamp duty 0.10 %, French FTT 0.40 %, Italian FTT 0.10 %;"
             " others 0 - verify current rates", "where": "order preview fee lines"}]


# ---- wiring ---------------------------------------------------------------------------------------------------------------------------------
def register(bus) -> None:
    bus.register("fx.spot", h_spot)
    bus.register("fx.settle", h_settle)
    bus.register("fx.forward", h_forward)
    bus.register("fx.forward_due", h_forward_due)


def bind(rt) -> None:
    """Adds the FX_FORWARD_DUE lifecycle job just before SETTLE_DUE (C13) - a database without the hook keeps working, the
    forwards then post when ``fx.settle`` / ``fx.forward_due`` runs for them."""
    try:
        from .lifecycle_eod import add_product_job
        from .lifecycle_jobs import Job
    except ImportError:
        return
    add_product_job(_forward_job(Job), before="SETTLE_DUE")


__all__ = ["spot_preview", "forward_preview", "quote", "pair_for", "pair_info", "pip_of", "deal_rate", "dealt_rate", "deal_amounts", "mid_usd_per_unit",
           "fit_base", "cross_ledger", "settle_deal", "due_deals", "fund_ccy", "conversion_plan", "convert_for", "forward_rate", "forward_grid", "forward_mtm",
           "forward_overlays", "post_forward", "venue_fee_c", "venue_fee_table", "positions_view", "pairs_view", "deals_view", "listings_view", "funding_check",
           "instrument_extra", "register_routes", "unsupported", "h_spot", "h_settle", "h_forward", "h_forward_due", "register", "bind",
           "SESSION_GROUP", "SETTLE_DAYS", "TENORS", "tenor_date", "spot_date", "fx_trade_date", "rate_for", "usd_leg", "pair_mid"]
