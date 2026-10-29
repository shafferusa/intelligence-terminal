"""Instrument master, id rules, OCC / futures parsing, FUTURES_SPECS, seeding, resolve (TERMINAL_DESIGN.md §2.10, §7, §12.3).

Conventions
- Identifiers (§7.1): US listings use the Yahoo ticker as spelled (``AAPL``, ``BRK-B``); options the OCC form
  ``AAPL261120C00330000``; futures ``<root><M><yy>`` (``ESZ26``) with Yahoo ``ESZ26.CME``; FX the market convention
  (``EURUSD``, ``USDJPY``; Yahoo ``JPY=X`` with ``yahoo_invert=1`` because Yahoo quotes JPY per USD); indices
  ``SPX NDX RUT VIX DJX DXY``; yields ``^IRX ^FVX ^TNX ^TYX``; private companies ``PRV-<slug>``; swaps ``SWP-000001``.
- ``account_kinds`` is the tradeability list of §7.2 (``US_EXCHANGE``, ``DEBT``, ``CREDIT``, ``FOREIGN_FX``,
  ``PRIVATE``, ``PB``, ``TREASURY``); ``tradeable_in`` is the pure check; the eligibility hooks live in ``books``.
- Futures margins are a static table (sentence FUT_MARGIN); last-trade dates follow the exchange rule per root on the
  NYSE-approximated calendar (sentence CME_CAL); grains are quoted in cents per bushel with multiplier 50 USD/point.
- Bonds are seeded LIMITED with no account kinds (Build 2); crypto rows of the universe are skipped (unsupported).
- Foreign listings (§7.1, §7.3, §13.9): ``foreign_listing_fields`` is the ONE classification used by the seed (a universe
  equity whose currency is not USD, e.g. ``0700-HK`` HKD, Yahoo ``0700.HK``) and by ``upsert_from_yahoo_meta`` (a non-US
  venue): ISO currency (Yahoo's minor units GBp / ZAc / ILA -> GBP / ZAR / ILS with ``price_scale`` 0.01: London is quoted
  in pence, marks stay as quoted and ``accounting`` applies the scale), session group FOREIGN (the venue session comes
  from Yahoo's currentTradingPeriod), T+2 (T+1 venues by suffix), account kinds FOREIGN_FX + PB, no Cboe symbol.
  Build 2 (P06, OA-12): a listing with a Yahoo symbol on a venue of ``FOREIGN_VENUES`` whose currency has a USD pair in the
  FX master trades (support FULL: its venue session from the quote record, settlement in its currency, the USD conversion
  shown in the preview and booked with the fill, a venue tax from ``fx.venue_fee_c``); any other foreign row is LIMITED
  (quotes and marks only, ``spec.limited_reason`` says why).  ``repair_foreign_listings`` corrects, in place, databases
  seeded before 2026-10-21 (every universe equity was seeded USD / US_EQUITY with its ticker as Yahoo symbol), lifts the
  foreign rows of the old UNSUPPORTED classification to FULL / LIMITED, adds the FX crosses and the T+1 spot pairs.
- FX master: the 25 USD pairs of the universe (market convention, ``fx_pair_id``) plus the major crosses ``FX_CROSSES``
  (``EURJPY`` = JPY per EUR, Yahoo ``EURJPY=X``); a cross's id puts the currency earlier in ``CCY_ORDER`` first
  (``fx_cross_id``).  Spot settles T+2 US business days, USD/CAD and USD/TRY T+1 (``FX_SPOT_T1``, market convention).
- Cache (TX-2): ``get`` / ``all`` never keep a row read on a connection inside an open transaction (a rolled-back command
  must not leave a phantom row); ``invalidate(ids)`` drops ids from memory and the next ``all()`` re-reads the table.
- ADV of names resolved on demand (hotfix 1.0.3.1, 2026-10-21, BORROW_AVAIL): a ticker resolved through Yahoo has no ``adv`` in
  its spec (the seed's universe carries it), so the borrow tier was NONE and every short was refused.  The quote worker computes
  it from Yahoo daily bars (``range=3mo&interval=1d``) when the name is resolved and when ``lending.locate`` finds it missing:
  ``adv_from_daily`` = the mean volume of the COMPLETED sessions that report one (never zero-filled, at least
  ``ADV_MIN_SESSIONS``), stored in the spec (``instruments.set_adv``) with its basis "ADV from Yahoo daily bars, 3 months
  (N sessions FIRST to LAST)"; a master ADV is never overwritten; no data -> no ADV -> no borrow.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, Iterable, List, Optional, Tuple

from . import config
from .clock import NY
from .units import mul_c, qprice

MONTH_CODES = "FGHJKMNQUVXZ"
MONTH_OF_CODE = {c: i + 1 for i, c in enumerate(MONTH_CODES)}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
EQUITY_KINDS = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")
INDEX_OPTION_ROOTS = ("SPX", "SPXW", "NDX", "RUT", "VIX")
US_EXCHANGES = ("NMS", "NYQ", "PCX", "NGM", "ASE", "BTS", "NCM")
_OCC_RE = re.compile(r"^([A-Z][A-Z0-9.\-]{0,5})(\d{6})([CP])(\d{8})$")
_FUT_RE = re.compile(r"^([A-Z0-9]{1,3})([FGHJKMNQUVXZ])(\d{2})$")


@dataclass(frozen=True)
class FutSpec:
    root: str
    name: str
    group: str
    suffix: str
    multiplier: Decimal
    tick: Decimal
    im_c: int
    mm_c: int
    months: str              # month codes, or "monthly"
    rule: str
    settle_time_et: str
    note: str = ""


def _fs(root, name, group, suffix, mult, tick, im, mm, months, rule, settle, note=""):
    return root, FutSpec(root, name, group, suffix, Decimal(mult), Decimal(tick), int(im) * 100, int(mm) * 100, months, rule, settle, note)


FUTURES_SPECS: Dict[str, FutSpec] = dict([
    _fs("ES", "E-mini S&P 500", "CME_EQUITY_INDEX", "CME", "50", "0.25", 17500, 15900, "HMUZ", "third_friday", "16:00"),
    _fs("NQ", "E-mini Nasdaq-100", "CME_EQUITY_INDEX", "CME", "20", "0.25", 26000, 23600, "HMUZ", "third_friday", "16:00"),
    _fs("YM", "E-mini Dow", "CME_EQUITY_INDEX", "CBT", "5", "1.00", 11000, 10000, "HMUZ", "third_friday", "16:00"),
    _fs("RTY", "E-mini Russell 2000", "CME_EQUITY_INDEX", "CME", "50", "0.10", 9000, 8200, "HMUZ", "third_friday", "16:00"),
    _fs("CL", "WTI crude", "NYMEX_ENERGY", "NYM", "1000", "0.01", 6500, 5900, "monthly", "cl", "14:30"),
    _fs("BZ", "Brent", "NYMEX_ENERGY", "NYM", "1000", "0.01", 6500, 5900, "monthly", "bz", "14:30"),
    _fs("NG", "Henry Hub", "NYMEX_ENERGY", "NYM", "10000", "0.001", 4500, 4100, "monthly", "ng", "14:30"),
    _fs("GC", "Gold", "COMEX_METALS", "CMX", "100", "0.10", 12000, 10900, "GJMQVZ", "third_last_bd", "14:30"),
    _fs("SI", "Silver", "COMEX_METALS", "CMX", "5000", "0.005", 11000, 10000, "HKNUZ", "third_last_bd", "14:30"),
    _fs("HG", "Copper", "COMEX_METALS", "CMX", "25000", "0.0005", 6000, 5500, "HKNUZ", "third_last_bd", "14:30"),
    _fs("ZB", "30-year T-bond", "CME_RATES", "CBT", "1000", "0.03125", 5000, 4500, "HMUZ", "seventh_bd_before_last", "16:00"),
    _fs("ZN", "10-year note", "CME_RATES", "CBT", "1000", "0.015625", 2600, 2400, "HMUZ", "seventh_bd_before_last", "16:00"),
    _fs("ZF", "5-year note", "CME_RATES", "CBT", "1000", "0.0078125", 1500, 1400, "HMUZ", "last_bd", "16:00"),
    _fs("ZT", "2-year note", "CME_RATES", "CBT", "2000", "0.0078125", 1100, 1000, "HMUZ", "last_bd", "16:00"),
    _fs("6E", "Euro FX", "CME_FX", "CME", "125000", "0.00005", 2800, 2500, "HMUZ", "fx_cme", "16:00"),
    _fs("6J", "Japanese yen", "CME_FX", "CME", "12500000", "0.0000005", 3000, 2700, "HMUZ", "fx_cme", "16:00"),
    _fs("6B", "British pound", "CME_FX", "CME", "62500", "0.0001", 2400, 2200, "HMUZ", "fx_cme", "16:00"),
    _fs("ZC", "Corn (cents/bu)", "CBOT_GRAINS", "CBT", "50", "0.25", 1500, 1400, "HKNUZ", "grains", "14:20", "quoted in cents per bushel; multiplier 50 USD per point"),
    _fs("ZW", "Wheat (cents/bu)", "CBOT_GRAINS", "CBT", "50", "0.25", 2200, 2000, "HKNUZ", "grains", "14:20", "quoted in cents per bushel; multiplier 50 USD per point"),
    _fs("ZS", "Soybeans (cents/bu)", "CBOT_GRAINS", "CBT", "50", "0.25", 2800, 2500, "FHKNQUX", "grains", "14:20", "quoted in cents per bushel; multiplier 50 USD per point"),
])

INDICES = [("SPX", "S&P 500", "^GSPC", "_SPX"), ("NDX", "Nasdaq-100", "^NDX", "_NDX"), ("RUT", "Russell 2000", "^RUT", "_RUT"),
           ("VIX", "Cboe Volatility Index", "^VIX", "_VIX"), ("DJX", "Dow Jones Industrial Average", "^DJI", "_DJX"),
           ("DXY", "US Dollar Index", "DX-Y.NYB", None)]
YIELDS = [("^IRX", "13-week T-bill yield"), ("^FVX", "5-year Treasury yield"), ("^TNX", "10-year Treasury yield"), ("^TYX", "30-year Treasury yield")]
USD_FIRST = ("JPY", "CHF", "CAD", "SEK", "NOK", "MXN", "BRL", "CNH", "HKD", "SGD", "KRW", "INR", "ZAR", "PLN", "TRY", "TWD", "IDR",
             "THB", "CZK", "HUF", "SAR")
CCY_NAMES = {"EUR": "Euro", "GBP": "British pound", "JPY": "Japanese yen", "CHF": "Swiss franc", "CAD": "Canadian dollar", "AUD": "Australian dollar",
             "NZD": "New Zealand dollar", "SEK": "Swedish krona", "NOK": "Norwegian krone", "MXN": "Mexican peso", "BRL": "Brazilian real",
             "CNH": "Offshore yuan", "HKD": "Hong Kong dollar", "SGD": "Singapore dollar", "KRW": "Korean won", "INR": "Indian rupee",
             "ZAR": "South African rand", "PLN": "Polish zloty", "TRY": "Turkish lira", "TWD": "Taiwan dollar", "IDR": "Indonesian rupiah",
             "THB": "Thai baht", "CZK": "Czech koruna", "HUF": "Hungarian forint", "SAR": "Saudi riyal", "USD": "US dollar"}
UNIVERSE_SPEC_KEYS = ("adv", "beta", "shares_outstanding", "shares_asof", "cik", "dividend_yield", "sector", "country")
# foreign listings: the classification shared by seed() and upsert_from_yahoo_meta() (foreign_listing_fields)
FOREIGN_GROUP = "FOREIGN"                 # sessions.state("FOREIGN", foreign=<the quote's Yahoo currentTradingPeriod>)
FOREIGN_ACCOUNT_KINDS = ("FOREIGN_FX", "PB")
FOREIGN_SUPPORT = "FULL"                  # Build 2 (OA-12): a listing on a known venue in a currency with a USD pair trades
FOREIGN_LIMITED = "LIMITED"               # any other foreign row: quotes and marks only (spec.limited_reason)
FOREIGN_SETTLE_DAYS = 2
FOREIGN_SETTLE_BY_SUFFIX = {"NS": 1, "BO": 1, "TO": 1, "V": 1}   # venues on T+1: India NSE/BSE (since 2023-01-27), Canada TSX/TSXV (2024-05-27)
MINOR_UNITS = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ZAC": "ZAR", "ILA": "ILS"}   # Yahoo quotes London in pence, JSE in cents, TASE in agorot
MINOR_UNIT_LABEL = {"GBP": "GBp", "ZAR": "ZAc", "ILS": "ILA"}
#: the venues a foreign listing trades on in this terminal (Yahoo symbol suffix -> venue); mainland China A-shares (.SS / .SZ)
#: settle in onshore CNY through Stock Connect and stay LIMITED
FOREIGN_VENUES = {"L": "London Stock Exchange", "HK": "Hong Kong Exchanges", "T": "Tokyo Stock Exchange", "KS": "Korea Exchange (KOSPI)",
                  "KQ": "Korea Exchange (KOSDAQ)", "TW": "Taiwan Stock Exchange", "TWO": "Taipei Exchange", "SR": "Saudi Exchange (Tadawul)",
                  "PA": "Euronext Paris", "DE": "Deutsche Boerse Xetra", "F": "Frankfurt Stock Exchange", "AS": "Euronext Amsterdam",
                  "BR": "Euronext Brussels", "LS": "Euronext Lisbon", "MI": "Borsa Italiana (Milan)", "MC": "Bolsa de Madrid",
                  "SW": "SIX Swiss Exchange", "ST": "Nasdaq Stockholm", "OL": "Oslo Bors", "HE": "Nasdaq Helsinki", "VI": "Wiener Boerse",
                  "IR": "Euronext Dublin", "NS": "National Stock Exchange of India", "BO": "BSE (Bombay)", "AX": "ASX (Australia)",
                  "NZ": "NZX (New Zealand)", "TO": "Toronto Stock Exchange", "V": "TSX Venture Exchange", "SI": "Singapore Exchange",
                  "JO": "Johannesburg Stock Exchange", "SA": "B3 (Sao Paulo)", "MX": "Bolsa Mexicana de Valores", "WA": "Warsaw Stock Exchange",
                  "BK": "Stock Exchange of Thailand", "JK": "Indonesia Stock Exchange", "IS": "Borsa Istanbul", "PR": "Prague Stock Exchange",
                  "BD": "Budapest Stock Exchange"}
FOREIGN_NOTE = ("foreign listing: trades in its venue's session (Yahoo currentTradingPeriod) and settles T+2 in its currency (T+1 venues"
                " by suffix); when the account and Treasury lack the currency, the USD conversion is shown in the preview and booked with the fill")
#: the FX master: crosses listed next to the 25 USD pairs (Yahoo ``EURJPY=X``); a cross's id = fx_cross_id(a, b)
FX_CROSSES = ("EURJPY", "EURGBP", "EURCHF", "GBPJPY", "AUDJPY", "EURAUD", "EURCAD", "CHFJPY", "GBPCHF", "CADJPY", "NZDJPY", "AUDNZD",
              "GBPAUD", "EURNZD", "AUDCAD", "EURSEK", "EURNOK")
#: market order of currencies in a pair name: the earlier one is the base (EUR > GBP > AUD > NZD > USD > CAD > CHF ... > JPY)
CCY_ORDER = ("EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "NOK", "SEK", "DKK", "PLN", "CZK", "HUF", "TRY", "ZAR", "MXN", "BRL", "ILS",
             "SGD", "HKD", "CNH", "TWD", "INR", "KRW", "IDR", "THB", "SAR", "JPY")
#: spot value T+1 by market convention (every other pair T+2)
FX_SPOT_T1 = ("USDCAD", "USDTRY")
OPEN_ORDER_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
REPAIR_CODE = "INSTRUMENT_NOT_REPAIRED"
_VENUE_RE = re.compile(r"\.([A-Z]{1,3})$")
ADV_DAYS = 63                    # ~3 months of sessions: YahooChart.daily(days > 22) asks range=3mo&interval=1d
ADV_MIN_SESSIONS = 5             # fewer completed sessions with a volume -> no ADV (no borrow)
ADV_BASIS = "ADV from Yahoo daily bars, 3 months"


def adv_from_daily(bars, today: date) -> Optional[dict]:
    """The average daily volume of Yahoo daily bars (BORROW_AVAIL): the mean volume of the sessions dated before ``today``
    (today's bar may still be forming) that report a volume - a missing volume is skipped, never zero-filled - rounded
    HALF_EVEN to whole shares.  ``{adv, sessions, first, last, basis}``; None with fewer than ``ADV_MIN_SESSIONS`` such
    sessions or an average of 0 shares (no data -> no ADV -> no borrow; nothing is invented)."""
    vols = sorted((b.date, int(b.v)) for b in (bars or []) if getattr(b, "v", None) is not None and b.date < today)
    if len(vols) < ADV_MIN_SESSIONS:
        return None
    adv = int((Decimal(sum(v for _, v in vols)) / Decimal(len(vols))).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    if adv <= 0:
        return None
    first, last = vols[0][0].isoformat(), vols[-1][0].isoformat()
    return {"adv": adv, "sessions": len(vols), "first": first, "last": last, "basis": f"{ADV_BASIS} ({len(vols)} sessions {first} to {last})"}


def needs_adv(inst) -> bool:
    """An equity-like, ACTIVE, tradeable, Yahoo-quoted row with no ADV in its spec (a name resolved on demand):
    ``lending.locate`` gives it NO borrow until the quote worker has stored one from Yahoo daily bars."""
    if inst is None or inst.kind not in EQUITY_KINDS or inst.status != "ACTIVE" or not inst.yahoo_symbol or inst.support in ("UNSUPPORTED", "COMPUTED"):
        return False
    return (inst.spec or {}).get("adv") in (None, "", 0, "0")


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    kind: str
    symbol: str
    name: str
    currency: str
    multiplier: Decimal
    tick: Decimal
    lot: int
    price_scale: Decimal
    session_group: str
    settle_days: int
    account_kinds: Tuple[str, ...]
    yahoo_symbol: Optional[str]
    cboe_symbol: Optional[str]
    underlying_id: Optional[str]
    expiry: Optional[str]
    strike: Optional[Decimal]
    right: Optional[str]
    style: Optional[str]
    exercise: Optional[str]
    spec: dict
    support: str
    status: str
    yahoo_invert: bool = False

    def is_option(self) -> bool:
        return self.kind == "OPTION"

    def is_future(self) -> bool:
        return self.kind == "FUTURE"

    def is_bond(self) -> bool:
        return self.kind == "BOND"

    def is_fx(self) -> bool:
        return self.kind == "FX"

    def is_private(self) -> bool:
        return self.kind == "PRIVATE"

    def is_swap(self) -> bool:
        return self.kind == "SWAP"

    def is_equity_like(self) -> bool:
        return self.kind in EQUITY_KINDS

    def contract_value_c(self, px: Decimal, qty: int) -> int:
        """``qty × px × multiplier × price_scale`` in cents (bonds: face × px / 100 through multiplier 0.01)."""
        return mul_c(int(qty), px, self.multiplier, self.price_scale)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("multiplier", "tick", "price_scale", "strike"):
            d[k] = None if d[k] is None else str(d[k])
        d["account_kinds"] = list(self.account_kinds)
        return d


def row_to_instrument(r: sqlite3.Row) -> Instrument:
    try:
        spec = json.loads(r["spec_json"] or "{}")
    except (TypeError, ValueError):
        spec = {}
    try:
        kinds = tuple(json.loads(r["account_kinds"] or "[]"))
    except (TypeError, ValueError):
        kinds = ()
    return Instrument(r["instrument_id"], r["kind"], r["symbol"], r["name"], r["currency"], Decimal(r["multiplier"]), Decimal(r["tick"]),
                      int(r["lot"]), Decimal(r["price_scale"]), r["session_group"], int(r["settle_days"]), kinds, r["yahoo_symbol"],
                      r["cboe_symbol"], r["underlying_id"], r["expiry"], None if r["strike"] is None else Decimal(r["strike"]), r["right"],
                      r["style"], r["exercise"], spec, r["support"], r["status"], bool(r["yahoo_invert"]))


# ---- id rules ---------------------------------------------------------------------------------------------------------
def parse_occ(s: str) -> Optional[Tuple[str, date, str, Decimal]]:
    """``AAPL261120C00330000`` -> ``("AAPL", date(2026, 11, 20), "C", Decimal("330"))``; None when not OCC."""
    m = _OCC_RE.match((s or "").strip().upper())
    if not m:
        return None
    root, ymd, right, strike = m.groups()
    try:
        d = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return root, d, right, qprice(Decimal(int(strike)) / Decimal(1000))


def occ_symbol(root: str, expiry: date, right: str, strike: Decimal) -> str:
    return f"{root}{expiry.strftime('%y%m%d')}{right}{int(qprice(strike) * 1000):08d}"


def futures_id(code: str, year: int, month: int) -> str:
    return f"{code}{MONTH_CODES[month - 1]}{year % 100:02d}"


def futures_yahoo(code: str, year: int, month: int) -> str:
    return f"{futures_id(code, year, month)}.{FUTURES_SPECS[code].suffix}"


def parse_futures_id(s: str) -> Optional[Tuple[str, int, int]]:
    m = _FUT_RE.match((s or "").strip().upper())
    if not m or m.group(1) not in FUTURES_SPECS:
        return None
    return m.group(1), 2000 + int(m.group(3)), MONTH_OF_CODE[m.group(2)]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def _month_before(year: int, month: int, n: int = 1) -> Tuple[int, int]:
    m = month - n
    y = year
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def last_trade_date(code: str, year: int, month: int, cal) -> date:
    """Last trade date per the root's exchange rule (§12.3) on the NYSE-approximated calendar."""
    spec = FUTURES_SPECS[code]
    rule = spec.rule
    if rule == "third_friday":
        return cal.roll_back(_nth_weekday(year, month, 4, 3))
    if rule == "cl":
        py, pm = _month_before(year, month)
        d = cal.roll_back(date(py, pm, 25))
        return cal.add_business_days(d, -3)
    if rule == "bz":
        py, pm = _month_before(year, month, 2)
        return cal.month_end(date(py, pm, 1))
    if rule == "ng":
        return cal.add_business_days(date(year, month, 1), -3)
    if rule == "third_last_bd":
        return cal.add_business_days(cal.month_end(date(year, month, 1)), -2)
    if rule == "seventh_bd_before_last":
        return cal.add_business_days(cal.month_end(date(year, month, 1)), -7)
    if rule == "last_bd":
        return cal.month_end(date(year, month, 1))
    if rule == "fx_cme":
        return cal.add_business_days(_nth_weekday(year, month, 2, 3), -2)
    if rule == "grains":
        return cal.prev_business_day(date(year, month, 15))
    raise KeyError(rule)


def listed_months(code: str, today: date, cal, n: int = 4) -> List[Tuple[int, int]]:
    """The next ``n`` contract months of a root whose last trade date is on or after ``today``."""
    spec = FUTURES_SPECS[code]
    codes = MONTH_CODES if spec.months == "monthly" else spec.months
    out: List[Tuple[int, int]] = []
    y, m = today.year, today.month
    for _ in range(40):
        if MONTH_CODES[m - 1] in codes and last_trade_date(code, y, m, cal) >= today:
            out.append((y, m))
            if len(out) == n:
                break
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def fx_yahoo(pair: str) -> Tuple[str, bool]:
    """``"EURUSD" -> ("EURUSD=X", False)``; ``"USDJPY" -> ("JPY=X", True)`` (Yahoo quotes JPY per USD)."""
    pair = pair.upper()
    if pair.startswith("USD") and len(pair) == 6:
        return f"{pair[3:]}=X", True
    return f"{pair}=X", False


def fx_pair_id(ccy: str) -> str:
    """The market-convention pair for a currency against USD."""
    ccy = ccy.upper()
    return f"USD{ccy}" if ccy in USD_FIRST else f"{ccy}USD"


def _ccy_rank(ccy: str) -> float:
    """Position in ``CCY_ORDER``; an unlisted currency sorts just before JPY (JPY is always the quote currency)."""
    try:
        return float(CCY_ORDER.index(ccy))
    except ValueError:
        return CCY_ORDER.index("JPY") - 0.5


def fx_cross_id(a: str, b: str) -> str:
    """The market-convention name of a pair of two currencies (``("JPY", "EUR") -> "EURJPY"``; with USD it is
    ``fx_pair_id``): the currency earlier in ``CCY_ORDER`` is the base."""
    a, b = a.upper(), b.upper()
    if a == "USD":
        return fx_pair_id(b)
    if b == "USD":
        return fx_pair_id(a)
    return f"{a}{b}" if _ccy_rank(a) <= _ccy_rank(b) else f"{b}{a}"


def fx_spot_days(pair: str) -> int:
    """Spot value lag in business days: T+1 for USD/CAD and USD/TRY (market convention), T+2 for every other pair."""
    return 1 if str(pair or "").upper() in FX_SPOT_T1 else 2


def fx_pip(pair: str) -> Decimal:
    """0.01 for a JPY-quoted pair, else 0.0001 (sentence FX_SPOT)."""
    return Decimal("0.01") if "JPY" in str(pair or "").upper() else Decimal("0.0001")


def fx_pair_fields(pair: str) -> dict:
    """The ``_insert`` fields of one FX master row (a USD pair or a cross): kind FX, quote currency, pip tick, FX_SPOT session,
    spot lag, the accounts that convert currencies, the Yahoo symbol (``JPY=X`` inverted for USDJPY, ``EURJPY=X`` for a cross)."""
    pair = pair.upper()
    base, quote = pair[:3], pair[3:]
    ysym, invert = fx_yahoo(pair)
    cross = "USD" not in (base, quote)
    spec = {"base": base, "quote": quote, "pip": str(fx_pip(pair))}
    if cross:
        spec["cross"] = True
    else:
        spec["ccy"] = quote if base == "USD" else base
    return {"instrument_id": pair, "kind": "FX", "symbol": pair, "name": f"{CCY_NAMES.get(base, base)} / {CCY_NAMES.get(quote, quote)}", "currency": quote,
            "tick": str(fx_pip(pair)), "session_group": "FX_SPOT", "settle_days": fx_spot_days(pair), "account_kinds": ["FOREIGN_FX", "CREDIT", "TREASURY", "PB"],
            "yahoo_symbol": ysym, "yahoo_invert": 1 if invert else 0, "spec": spec, "support": "FULL"}


def otc_reference_ids(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """``{instrument_id: [account_id, ...]}`` of the instruments that OPEN OTC contracts of OPEN lifetimes observe without
    holding them: the underlying of a TRS (its mark, its reset CLOSE, its dividends) and the USD pair of a cross-currency
    swap's foreign currency (its mark and the 17:00 FX close of an MTM reset).  The quote worker pins these at HELD priority
    and MARKS_RECORD records their closes, so a TRS / XCCY never waits on a name nobody holds (WP-15 integration)."""
    out: Dict[str, List[str]] = {}
    for r in _open_reference_rows(conn):
        for iid in _reference_ids(r["kind"], r["terms_json"]):
            accts = out.setdefault(iid, [])
            if r["account_id"] not in accts:
                accts.append(r["account_id"])
    return out


def _open_reference_rows(conn: sqlite3.Connection) -> list:
    """The OPEN OTC contracts of OPEN accounts and lifetimes whose kind observes an instrument (``otc_reference_ids``)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(otc_contracts)").fetchall()}
    td = "c.trade_date" if "trade_date" in cols else "NULL"
    return conn.execute(f"SELECT c.contract_id, c.account_id, c.kind, c.terms_json, c.start_date, {td} AS trade_date FROM otc_contracts c"
                        " JOIN accounts a ON a.account_id=c.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE c.status='OPEN'"
                        " AND a.status='OPEN' AND l.status='OPEN' AND c.kind IN ('TRS','XCCY','EQ_OPTION','EQ_FORWARD','FX_OPTION','FX_FORWARD')"
                        " ORDER BY c.contract_id").fetchall()


def _reference_ids(kind: str, terms_json) -> List[str]:
    """The instruments one OTC contract observes: a TRS / equity option / equity forward its underlying, an XCCY the USD pair of its
    foreign currency, an FX option / forward its pair and, for a cross, each leg's USD pair."""
    try:
        terms = json.loads(terms_json or "{}")
    except ValueError:
        terms = {}
    if not isinstance(terms, dict):
        return []
    ids: List[str] = []
    if kind in ("TRS", "EQ_OPTION", "EQ_FORWARD") and terms.get("underlying_id"):
        ids.append(str(terms["underlying_id"]).upper())
    elif kind == "XCCY" and terms.get("foreign_ccy"):
        ids.append(fx_pair_id(str(terms["foreign_ccy"])))
    elif kind in ("FX_OPTION", "FX_FORWARD") and terms.get("pair"):
        pair = str(terms["pair"]).upper().replace("/", "")
        ids.append(pair)
        for c in (pair[:3], pair[3:6]):                      # a cross (EURJPY) also needs each leg's USD pair
            if len(c) == 3 and c != "USD":
                ids.append(fx_pair_id(c))
    return ids


def otc_reference_since(conn: sqlite3.Connection) -> Dict[str, date]:
    """V1061R1-02: ``{instrument_id: the first date an OPEN OTC contract observes it}`` - the earliest trade date of the contracts that
    reference it (``trade_date``, else ``start_date``: a contract booked before 1.0.6.1 stored no trade date).  MARKS_RECORD back-fills an
    FX pair's closes from this date only: a pair that enters the recorded set with today's booking needs no close before it."""
    out: Dict[str, date] = {}
    for r in _open_reference_rows(conn):
        raw = r["trade_date"] or r["start_date"]
        try:
            d = date.fromisoformat(str(raw)[:10]) if raw else None
        except ValueError:
            d = None
        if d is None:
            continue
        for iid in _reference_ids(r["kind"], r["terms_json"]):
            if iid not in out or d < out[iid]:
                out[iid] = d
    return out


# ---- foreign listings -------------------------------------------------------------------------------------------------
def venue_suffix(yahoo_symbol: Optional[str]) -> Optional[str]:
    """``"0700.HK" -> "HK"``, ``"SHEL.L" -> "L"``; None when the Yahoo symbol has no venue suffix (US listings, FX, futures)."""
    m = _VENUE_RE.search(str(yahoo_symbol or "").strip().upper())
    return m.group(1) if m else None


def foreign_listing_fields(yahoo_symbol: Optional[str], currency: Optional[str], scale=None) -> dict:
    """The classification of a foreign listing, shared by ``seed`` (universe rows) and ``upsert_from_yahoo_meta`` (Yahoo
    meta) so both paths write the same row: the ISO currency (Yahoo's minor units ``GBp`` / ``ZAc`` / ``ILA`` become
    ``GBP`` / ``ZAR`` / ``ILS`` with ``price_scale`` 0.01; ``scale`` — the universe's ``yahoo_scale`` — states the quote
    scale when the currency is given as the major unit, e.g. ``GBP`` + 0.01 for London's pence), session group FOREIGN,
    settlement T+2 (T+1 venues by suffix), account kinds FOREIGN_FX + PB, no Cboe symbol, support UNSUPPORTED.  Returns
    the ``_insert`` keyword fields plus ``spec`` (only the foreign keys; the caller merges its own).  ValueError on a
    currency that is not an ISO code or a scale outside (0, 1]."""
    raw = str(currency or "").strip()
    major = MINOR_UNITS.get(raw)
    if major is not None:
        ccy, price_scale = major, Decimal("0.01")
    else:
        ccy = raw.upper()
        if not re.fullmatch(r"[A-Z]{3}", ccy):
            raise ValueError(f"foreign listing {yahoo_symbol}: currency {currency!r} is not an ISO code")
        try:
            price_scale = Decimal(str(scale)) if scale not in (None, "") else Decimal(1)
        except ArithmeticError as e:
            raise ValueError(f"foreign listing {yahoo_symbol}: quote scale {scale!r} is not a number") from e
    if not price_scale.is_finite() or not Decimal(0) < price_scale <= Decimal(1):
        raise ValueError(f"foreign listing {yahoo_symbol}: quote scale {scale!r} must be in (0, 1]")
    suffix = venue_suffix(yahoo_symbol)
    support, why = foreign_support(yahoo_symbol, suffix, ccy)
    spec = {"foreign": True, "venue_suffix": suffix, "venue": FOREIGN_VENUES.get(suffix or ""), "note": FOREIGN_NOTE if support == FOREIGN_SUPPORT else why}
    if why:
        spec["limited_reason"] = why
    if price_scale != 1:
        label = MINOR_UNIT_LABEL.get(ccy) if price_scale == Decimal("0.01") else None
        spec["quote_unit"] = label or f"{price_scale} {ccy}"
        spec["quote_unit_note"] = f"Yahoo quotes this listing in {spec['quote_unit']}; values are {ccy} = quoted price x {price_scale}"
    return {"currency": ccy, "price_scale": str(price_scale), "session_group": FOREIGN_GROUP,
            "settle_days": FOREIGN_SETTLE_BY_SUFFIX.get(suffix, FOREIGN_SETTLE_DAYS), "account_kinds": list(FOREIGN_ACCOUNT_KINDS),
            "yahoo_symbol": str(yahoo_symbol).strip() if yahoo_symbol else None, "cboe_symbol": None, "support": support, "spec": spec}


def foreign_support(yahoo_symbol: Optional[str], suffix: Optional[str], ccy: str) -> Tuple[str, str]:
    """``(support, reason)`` of a foreign listing (OA-12): FULL when it has a Yahoo symbol (the venue session and the prints come
    from it), its venue is in ``FOREIGN_VENUES`` and its currency has a USD pair in the FX master (``CCY_NAMES``: the account
    can hold, value and convert it); else LIMITED with the reason (quotes and marks only)."""
    if not yahoo_symbol:
        return FOREIGN_LIMITED, "no Yahoo symbol: no venue session and no prints, so orders cannot be judged (quotes and marks only)"
    if (suffix or "") not in FOREIGN_VENUES:
        return FOREIGN_LIMITED, (f"venue .{suffix} is not in this terminal's venue table: quotes and marks only" if suffix
                                 else "no venue suffix on the Yahoo symbol: quotes and marks only")
    if ccy not in CCY_NAMES:
        return FOREIGN_LIMITED, f"{ccy} has no USD pair in the FX master: the listing cannot be valued or funded here (quotes only)"
    return FOREIGN_SUPPORT, ""


def universe_is_foreign(e: dict) -> bool:
    """A universe equity row is a foreign listing when its currency is not USD or its explicit Yahoo symbol carries a venue
    suffix (``0700.HK``); every other equity row is a US listing quoted by its own ticker (§7.4)."""
    return str(e.get("currency") or "USD").strip().upper() != "USD" or venue_suffix(e.get("yahoo")) is not None


def universe_yahoo(e: dict) -> Optional[str]:
    """A foreign universe row's Yahoo symbol: its ``yahoo`` field (every shipped row has one); else the ticker with the
    venue hyphen turned back into Yahoo's dot (the universe spells ``0700.HK`` as ``0700-HK``)."""
    y = str(e.get("yahoo") or "").strip()
    if y:
        return y
    t = str(e.get("ticker") or "").strip()
    head, sep, tail = t.rpartition("-")
    return f"{head}.{tail}" if sep and head and tail else (t or None)


def _blocking_reference(conn: sqlite3.Connection, instrument_id: str) -> Optional[str]:
    """What stops an in-place repair of ``instrument_id``: an OPEN order, or any lot / fill / borrow / ledger line (booked
    history).  A cancelled or expired order without fills is history that keeps its own session group: not blocking."""
    q = ",".join("?" for _ in OPEN_ORDER_STATUSES)
    r = conn.execute(f"SELECT order_id, status FROM orders WHERE instrument_id=? AND status IN ({q}) ORDER BY order_id LIMIT 1",
                     (instrument_id, *OPEN_ORDER_STATUSES)).fetchone()
    if r is not None:
        return f"open order {r[0]} ({r[1]}) references it"
    for table, what in (("lots", "a position lot"), ("fills", "a fill"), ("borrows", "a stock borrow"), ("journal_lines", "a ledger line")):
        if conn.execute(f"SELECT 1 FROM {table} WHERE instrument_id=? LIMIT 1", (instrument_id,)).fetchone() is not None:
            return f"{what} references it"
    return None


def _spec_of(row) -> dict:
    try:
        spec = json.loads(row["spec_json"] or "{}")
    except (TypeError, ValueError):
        spec = {}
    return spec if isinstance(spec, dict) else {}


# ---- the master -------------------------------------------------------------------------------------------------------
class InstrumentMaster:
    def __init__(self, db, log=None):
        self.db = db
        self.log = log
        self._cache: Dict[str, Instrument] = {}
        self._uncached: Optional[Dict[str, Instrument]] = None
        self._loaded = False

    # ---- read -----------------------------------------------------------------------------------------------------
    @staticmethod
    def _in_tx(conn) -> bool:
        """True when ``conn`` is inside an open transaction: what it reads may still roll back (TX-2), so it is never cached."""
        try:
            return bool(conn.in_transaction)
        except Exception:   # noqa: BLE001 - a stand-in connection without the flag: treat as committed
            return False

    def load(self) -> None:
        """Read the whole table into memory.  Inside an open transaction (``db.reader()`` hands this thread's writer while it
        holds one) the rows are used for that call only and the master stays unloaded, so a rollback leaves no phantom."""
        with self.db.reader() as conn:
            rows = conn.execute("SELECT * FROM instruments").fetchall()
            in_tx = self._in_tx(conn)
        cache = {r["instrument_id"]: row_to_instrument(r) for r in rows}
        if in_tx:
            self._uncached = cache
            self._loaded = False
            return
        self._cache = cache
        self._uncached = None
        self._loaded = True

    def get(self, instrument_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Instrument]:
        inst = self._cache.get(instrument_id)
        if inst is not None:
            return inst
        if conn is not None:
            row = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (instrument_id,)).fetchone()
            in_tx = self._in_tx(conn)
        else:
            with self.db.reader() as c:
                row = c.execute("SELECT * FROM instruments WHERE instrument_id=?", (instrument_id,)).fetchone()
                in_tx = self._in_tx(c)
        if row is None:
            return None
        inst = row_to_instrument(row)
        if not in_tx:                              # TX-2: a row read inside an open transaction may still roll back
            self._cache[instrument_id] = inst
        return inst

    def all(self) -> List[Instrument]:
        if not self._loaded:
            self.load()
            if not self._loaded:                   # read inside an open transaction: this call's rows, nothing cached
                rows = getattr(self, "_uncached", None) or {}
                self._uncached = None
                return list(rows.values())
        return list(self._cache.values())

    def invalidate(self, ids: Iterable[str]) -> None:
        """TX-2: forget ``ids`` (a command that touched them rolled back); the next ``all()`` re-reads the committed table, so a
        committed row stays listed and a rolled-back one is gone."""
        for i in ids or ():
            self._cache.pop(str(i), None)
        self._loaded = False

    def count(self, kind: Optional[str] = None) -> int:
        return sum(1 for i in self.all() if kind is None or i.kind == kind)

    def search(self, q: str, kinds: Iterable[str] = (), limit: int = 20) -> List[Instrument]:
        """Exact id / symbol / Yahoo symbol / alias first, then symbol prefix, then name or alias contains (SR-14: a listed
        company found by its former private name through ``spec.aliases``)."""
        q = (q or "").strip().upper()
        kinds = tuple(kinds)
        if not q:
            return []
        hits: List[Tuple[int, Instrument]] = []
        for inst in self.all():
            if kinds and inst.kind not in kinds:
                continue
            sym = inst.symbol.upper()
            raw = (inst.spec or {}).get("aliases") if isinstance(inst.spec, dict) else None
            aliases = [str(a).upper() for a in (raw if isinstance(raw, (list, tuple)) else ([raw] if raw else []))]
            if sym == q or inst.instrument_id.upper() == q or (inst.yahoo_symbol or "").upper() == q or q in aliases:
                hits.append((0, inst))
            elif sym.startswith(q) or any(a.startswith(q) for a in aliases):
                hits.append((1, inst))
            elif q in inst.name.upper() or any(q in a for a in aliases):
                hits.append((2, inst))
        hits.sort(key=lambda t: (t[0], t[1].symbol))
        return [i for _, i in hits[:limit]]

    def by_underlying(self, underlying_id: str, expiry: Optional[str] = None) -> List[Instrument]:
        return [i for i in self.all() if i.underlying_id == underlying_id and (expiry is None or i.expiry == expiry)]

    @staticmethod
    def _yahoo_holder(conn: sqlite3.Connection, yahoo_symbol: Optional[str], exclude: Optional[str] = None) -> Optional[str]:
        """The id of the row that owns ``yahoo_symbol`` under the unique index ``ux_instruments_yahoo`` (options excluded)."""
        if not yahoo_symbol:
            return None
        row = conn.execute("SELECT instrument_id FROM instruments WHERE yahoo_symbol=? AND kind<>'OPTION' AND instrument_id<>? LIMIT 1",
                           (yahoo_symbol, exclude or "")).fetchone()
        return None if row is None else row[0]

    def by_yahoo_symbol(self, yahoo_symbol: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Instrument]:
        """The instrument quoted under ``yahoo_symbol`` (``0700.HK`` -> ``0700-HK``, ``JPY=X`` -> ``USDJPY``), or None."""
        sym = str(yahoo_symbol or "").strip()
        if not sym:
            return None
        if conn is not None:
            iid = self._yahoo_holder(conn, sym)
        else:
            with self.db.reader() as c:
                iid = self._yahoo_holder(c, sym)
        return None if iid is None else self.get(iid, conn)

    def refresh(self, instrument_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Instrument]:
        self._cache.pop(instrument_id, None)
        return self.get(instrument_id, conn)

    # ---- write ----------------------------------------------------------------------------------------------------
    def _insert(self, conn: sqlite3.Connection, ts: int, **f) -> bool:
        row = {"multiplier": "1", "tick": "0.01", "lot": 1, "price_scale": "1", "settle_days": 1, "account_kinds": [], "yahoo_symbol": None,
               "yahoo_invert": 0, "cboe_symbol": None, "underlying_id": None, "expiry": None, "strike": None, "right": None, "style": None,
               "exercise": None, "spec": {}, "support": "FULL", "status": "ACTIVE"}
        row.update(f)
        cur = conn.execute(
            "INSERT OR IGNORE INTO instruments(instrument_id, kind, symbol, name, currency, multiplier, tick, lot, price_scale, session_group,"
            " settle_days, account_kinds, yahoo_symbol, yahoo_invert, cboe_symbol, underlying_id, expiry, strike, right, style, exercise, spec_json,"
            " support, status, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["instrument_id"], row["kind"], row["symbol"], row["name"], row["currency"], str(row["multiplier"]), str(row["tick"]),
             int(row["lot"]), str(row["price_scale"]), row["session_group"], int(row["settle_days"]), json.dumps(list(row["account_kinds"])),
             row["yahoo_symbol"], int(row["yahoo_invert"]), row["cboe_symbol"], row["underlying_id"], row["expiry"],
             None if row["strike"] is None else str(row["strike"]), row["right"], row["style"], row["exercise"], json.dumps(row["spec"]),
             row["support"], row["status"], ts, ts))
        inserted = cur.rowcount == 1
        if inserted:
            self._cache.pop(row["instrument_id"], None)
            self._loaded = False
        return inserted

    UPSERT_COLUMNS = ("kind", "symbol", "name", "currency", "multiplier", "tick", "lot", "price_scale", "session_group", "settle_days", "account_kinds",
                      "yahoo_symbol", "yahoo_invert", "cboe_symbol", "underlying_id", "expiry", "strike", "right", "style", "exercise", "spec", "support",
                      "status")

    def upsert(self, tx, **fields) -> Optional[Instrument]:
        """Public insert-or-update of one master row inside the caller's writer transaction (P07's Treasury master, any product
        list refreshed by a job).  ``instrument_id`` is required; a NEW row also needs kind, symbol, name, currency and
        session_group (defaults as ``_insert``: multiplier 1, tick 0.01, lot 1, scale 1, T+1, FULL, ACTIVE); an EXISTING row has
        exactly the given columns replaced (``spec`` replaces the whole spec, ``account_kinds`` the list) and ``updated_ts`` set.
        Unknown keys are refused (ValueError).  The row is read back on ``tx.conn`` and returned (not cached inside the open
        transaction, TX-2); the master's memory forgets the id so the next read after the commit is the stored row."""
        iid = str(fields.pop("instrument_id", "") or "").strip()
        if not iid:
            raise ValueError("upsert needs instrument_id")
        bad = [k for k in fields if k not in self.UPSERT_COLUMNS]
        if bad:
            raise ValueError(f"upsert: unknown column(s) {', '.join(sorted(bad))}")
        conn = tx.conn
        ts = int(tx.now.timestamp())
        exists = conn.execute("SELECT 1 FROM instruments WHERE instrument_id=?", (iid,)).fetchone() is not None
        if not exists:
            missing = [k for k in ("kind", "symbol", "name", "currency", "session_group") if not fields.get(k)]
            if missing:
                raise ValueError(f"upsert of the new row {iid}: {', '.join(missing)} required")
            self._insert(conn, ts, instrument_id=iid, **fields)
        elif fields:
            cols, vals = [], []
            for k, v in fields.items():
                col = "spec_json" if k == "spec" else k
                if k == "spec":
                    v = json.dumps(v if isinstance(v, dict) else {})
                elif k == "account_kinds":
                    v = json.dumps(list(v or []))
                elif k in ("multiplier", "tick", "price_scale", "strike") and v is not None:
                    v = str(v)
                elif k in ("lot", "settle_days", "yahoo_invert"):
                    v = int(v)
                cols.append(f"{col}=?")
                vals.append(v)
            conn.execute(f"UPDATE instruments SET {', '.join(cols)}, updated_ts=? WHERE instrument_id=?", (*vals, ts, iid))
        self._cache.pop(iid, None)
        self._loaded = False
        row = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
        return None if row is None else row_to_instrument(row)

    def seed(self, tx, cal=None) -> int:
        """First run (§7.4): equities / ETFs / ADRs / REITs / preferreds from ``universe.json`` (crypto skipped; the 38
        non-USD rows are foreign listings through ``foreign_listing_fields``, every other row a US listing), 25 FX
        pairs vs USD (the crosses come from ``ensure_fx_master`` at start), indices, yields, the next 4 months of every futures root, bonds LIMITED, the private-company master
        when its file exists.  Idempotent (INSERT OR IGNORE); returns the number of rows inserted.  ``cal`` (optional)
        is the TradingCalendar used for the futures last-trade dates."""
        conn = tx.conn
        ts = int(tx.now.timestamp())
        n = 0
        uni = self._universe()
        for e in uni.get("equities") or []:
            kind = e.get("asset_class")
            ticker = e.get("ticker")
            if not ticker or kind not in EQUITY_KINDS:
                continue
            spec = {k: e.get(k) for k in UNIVERSE_SPEC_KEYS}
            if universe_is_foreign(e):          # 0700-HK HKD / Yahoo 0700.HK, SHEL-L GBP pence, ... (foreign_listing_fields)
                fields = foreign_listing_fields(universe_yahoo(e), e.get("currency"), e.get("yahoo_scale"))
                spec.update(fields.pop("spec"))
                n += self._insert(conn, ts, instrument_id=ticker, kind=kind, symbol=ticker, name=e.get("name") or ticker, spec=spec, **fields)
                continue
            n += self._insert(conn, ts, instrument_id=ticker, kind=kind, symbol=ticker, name=e.get("name") or ticker, currency="USD",
                              session_group="US_EQUITY", settle_days=1, account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=ticker,
                              cboe_symbol=ticker.replace("-", "."), spec=spec, support="FULL")
        for ccy in uni.get("fx") or {}:
            n += self._insert(conn, ts, **fx_pair_fields(fx_pair_id(ccy)))
        # the major crosses (Build 2, OA-14: EURJPY=X ...) are added by ensure_fx_master, which the server runs on every start right
        # after the seed (repair_foreign_listings), so a fresh and an old database get them the same way
        for iid, name, ysym, csym in INDICES:
            n += self._insert(conn, ts, instrument_id=iid, kind="INDEX", symbol=iid, name=name, currency="USD", session_group="US_EQUITY",
                              settle_days=0, account_kinds=[], yahoo_symbol=ysym, cboe_symbol=csym, spec={"reference": True}, support="LIMITED")
        for sym, name in YIELDS:
            n += self._insert(conn, ts, instrument_id=sym, kind="YIELD", symbol=sym, name=name, currency="USD", session_group="US_BONDS",
                              settle_days=0, account_kinds=[], yahoo_symbol=sym, spec={"reference": True, "unit": "percent"}, support="LIMITED")
        for b in uni.get("bonds") or []:
            bid = b.get("id")
            if not bid:
                continue
            n += self._insert(conn, ts, instrument_id=bid, kind="BOND", symbol=bid, name=b.get("name") or bid, currency="USD", multiplier="0.01",
                              tick="0.001", lot=1000, session_group="US_BONDS", settle_days=1, account_kinds=[],
                              spec={k: b.get(k) for k in ("issuer", "issuer_ticker", "coupon", "years", "rating", "spread_bps", "adv", "sector")},
                              support="LIMITED")
        n += self.ensure_futures_listings(tx, tx.now.astimezone(NY).date(), cal or self._default_cal())
        n += self._seed_private(conn, ts)
        self._loaded = False
        return n

    @staticmethod
    def _default_cal():
        from .calendar import TradingCalendar
        return TradingCalendar(config.holidays_path())

    @staticmethod
    def _universe() -> dict:
        path = config.universe_path()
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def repair_foreign_listings(self, tx) -> dict:
        """Idempotent startup repair for databases seeded before the foreign-listing fix (2026-10-21; the owner's live
        ``terminal.db`` was): the old seed stored EVERY universe equity as USD / US_EQUITY / US_EXCHANGE+PB with its ticker
        as the Yahoo symbol, so the 38 foreign rows sat in the US Exchange account with quotes unavailable forever.  Each
        foreign universe row (``universe_is_foreign``) still stored as ``currency='USD'`` and ``session_group='US_EQUITY'``
        is rewritten IN PLACE with exactly the fields the seed now writes (``foreign_listing_fields``: currency, price
        scale, session group, settlement days, account kinds, Yahoo symbol, no Cboe symbol, support, spec), unless
        something live or booked references it (``_blocking_reference``: an open order, a lot, a fill, a borrow or a
        ledger line) — such a row is left exactly as recorded, named in the log line and in ONE open WARN attention item
        (``INSTRUMENT_NOT_REPAIRED``; the run that later repairs the row resolves it).  When another row already holds the
        listing's Yahoo symbol (the unique index allows one owner): a duplicate the pre-fix resolve made (typing
        ``0700.HK``: an UNSUPPORTED equity row nothing references) releases it to the universe row
        (``_release_resolved_duplicate``); any other holder keeps it, the row is reclassified without a Yahoo symbol and
        ``spec.yahoo_symbol_held_by`` names the holder.  A row already foreign (fresh seed, earlier repair) is never
        touched, so a second run changes nothing.  Call site: ``server.build_runtime``, right after the first-run seed, in
        its own writer transaction, on every start.  Logs ONE INFO line with the counts; returns ``{"repaired": [ids],
        "skipped": {id: reason}, "moved": {duplicate id: universe id}}``."""
        conn = tx.conn
        ts = int(tx.now.timestamp())
        repaired: List[str] = []
        skipped: Dict[str, str] = {}
        moved: Dict[str, str] = {}
        for e in self._universe().get("equities") or []:
            iid = e.get("ticker")
            if not iid or e.get("asset_class") not in EQUITY_KINDS or not universe_is_foreign(e):
                continue
            row = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
            if row is None or row["session_group"] != "US_EQUITY" or row["currency"] != "USD":
                continue                                            # not seeded, or already a foreign listing
            reason = _blocking_reference(conn, iid)
            fields: dict = {}
            if reason is None:
                try:
                    fields = foreign_listing_fields(universe_yahoo(e), e.get("currency"), e.get("yahoo_scale"))
                except ValueError as err:
                    reason = f"its universe row is invalid ({err})"
            if reason is not None:
                skipped[iid] = reason
                self._attention_not_repaired(conn, ts, iid, e.get("name") or iid, reason)
                continue
            spec = _spec_of(row)
            spec.update(fields.pop("spec"))
            holder = self._yahoo_holder(conn, fields["yahoo_symbol"], exclude=iid)
            if holder is not None:
                if self._release_resolved_duplicate(conn, ts, holder, iid):
                    moved[holder] = iid
                else:
                    spec["yahoo_symbol_held_by"] = holder
                    fields["yahoo_symbol"] = None
            conn.execute("UPDATE instruments SET currency=?, price_scale=?, session_group=?, settle_days=?, account_kinds=?, yahoo_symbol=?, cboe_symbol=?,"
                         " support=?, spec_json=?, updated_ts=? WHERE instrument_id=? AND session_group='US_EQUITY' AND currency='USD'",
                         (fields["currency"], fields["price_scale"], fields["session_group"], int(fields["settle_days"]), json.dumps(list(fields["account_kinds"])),
                          fields["yahoo_symbol"], fields["cboe_symbol"], fields["support"], json.dumps(spec), ts, iid))
            conn.execute("UPDATE attention SET resolved_ts=? WHERE code=? AND ref_type='instrument' AND ref_id=? AND resolved_ts IS NULL", (ts, REPAIR_CODE, iid))
            self._cache.pop(iid, None)
            repaired.append(iid)
        repaired.sort()
        if repaired:
            self._loaded = False
        support = self._upgrade_foreign_support(conn, ts)
        fx_master = self.ensure_fx_master(tx)
        if self.log:
            self.log.info("instrument repair (foreign universe listings): %d corrected [%s]; %d left as recorded [%s]%s; foreign rows re-classified %d"
                          " (FULL %d, LIMITED %d); FX master: %d pair(s) added, %d spot lag(s) corrected", len(repaired),
                          ", ".join(repaired) or "none", len(skipped), "; ".join(f"{k}: {v}" for k, v in sorted(skipped.items())) or "none",
                          "".join(f"; Yahoo symbol of the resolved duplicate {k} moved to {v}" for k, v in sorted(moved.items())),
                          len(support["changed"]), support["full"], support["limited"], len(fx_master["added"]), len(fx_master["spot_lag"]))
        return {"repaired": repaired, "skipped": skipped, "moved": moved, "support": support["changed"], "fx_added": fx_master["added"],
                "fx_spot_lag": fx_master["spot_lag"]}

    def _upgrade_foreign_support(self, conn: sqlite3.Connection, ts: int) -> dict:
        """Build 2 (OA-12): every foreign listing row (``spec.foreign``) gets the support ``foreign_support`` gives it now - FULL on a
        known venue in a currency with a USD pair, else LIMITED with the reason - in place of the old UNSUPPORTED (quotes only).
        Only ``support`` and the spec's note / limited reason / venue change; booked history is untouched (an UNSUPPORTED row was
        never traded).  Idempotent: a row already classified this way is skipped."""
        changed: List[str] = []
        full = limited = 0
        rows = conn.execute("SELECT instrument_id, kind, currency, yahoo_symbol, support, spec_json FROM instruments WHERE session_group=?"
                            " AND kind IN ('EQUITY','ETF','ADR','REIT','PREFERRED') AND status='ACTIVE'", (FOREIGN_GROUP,)).fetchall()
        for r in rows:
            spec = _spec_of(r)
            if not spec.get("foreign"):
                continue                                           # a crypto / fund row resolved as FOREIGN: not a listing
            suffix = venue_suffix(r["yahoo_symbol"]) or spec.get("venue_suffix")
            support, why = foreign_support(r["yahoo_symbol"], suffix, str(r["currency"] or ""))
            full += support == FOREIGN_SUPPORT
            limited += support != FOREIGN_SUPPORT
            new = dict(spec, venue=FOREIGN_VENUES.get(suffix or ""), note=FOREIGN_NOTE if support == FOREIGN_SUPPORT else why)
            if why:
                new["limited_reason"] = why
            else:
                new.pop("limited_reason", None)
            if r["support"] == support and new == spec:
                continue
            conn.execute("UPDATE instruments SET support=?, spec_json=?, updated_ts=? WHERE instrument_id=?", (support, json.dumps(new), ts, r["instrument_id"]))
            self._cache.pop(r["instrument_id"], None)
            changed.append(r["instrument_id"])
        if changed:
            self._loaded = False
        return {"changed": sorted(changed), "full": full, "limited": limited}

    def ensure_fx_master(self, tx) -> dict:
        """Idempotent: the FX crosses of ``FX_CROSSES`` exist (a database seeded before Build 2 has the 25 USD pairs only), and
        the T+1 spot pairs (``FX_SPOT_T1``) carry settle_days 1 (a deal's own value date is stored on the deal, so nothing booked
        moves).  Returns ``{"added": [ids], "spot_lag": [ids]}``."""
        conn = tx.conn
        ts = int(tx.now.timestamp())
        added = [p for p in FX_CROSSES if self._insert(conn, ts, **fx_pair_fields(p))]
        lag = []
        for pair in FX_SPOT_T1:
            cur = conn.execute("UPDATE instruments SET settle_days=?, updated_ts=? WHERE instrument_id=? AND kind='FX' AND settle_days<>?",
                               (fx_spot_days(pair), ts, pair, fx_spot_days(pair)))
            if cur.rowcount:
                lag.append(pair)
                self._cache.pop(pair, None)
        if added or lag:
            self._loaded = False
        return {"added": added, "spot_lag": lag}

    def _release_resolved_duplicate(self, conn: sqlite3.Connection, ts: int, holder: str, iid: str) -> bool:
        """The row holding a foreign listing's Yahoo symbol is a duplicate the pre-fix resolve made (typing ``0700.HK`` while
        the seeded ``0700-HK`` carried the wrong symbol): an equity-kind UNSUPPORTED row nothing references.  Its Yahoo
        symbol is released (the row stays, without a quote source, its spec pointing at ``iid``) so the universe row can
        take it.  Anything else (another kind, a tradeable row, a referenced row) is left alone: False."""
        r = conn.execute("SELECT kind, support, spec_json FROM instruments WHERE instrument_id=?", (holder,)).fetchone()
        if r is None or r["kind"] not in EQUITY_KINDS or r["support"] != "UNSUPPORTED" or _blocking_reference(conn, holder) is not None:
            return False
        spec = _spec_of(r)
        spec.update(yahoo_symbol_moved_to=iid, note=f"duplicate of the universe listing {iid}: its Yahoo symbol moved there (foreign-listing repair)")
        conn.execute("UPDATE instruments SET yahoo_symbol=NULL, spec_json=?, updated_ts=? WHERE instrument_id=?", (json.dumps(spec), ts, holder))
        self._cache.pop(holder, None)
        return True

    @staticmethod
    def _attention_not_repaired(conn: sqlite3.Connection, ts: int, iid: str, name: str, reason: str) -> None:
        """ONE open WARN attention item per row the repair had to leave as recorded (deduplicated across restarts)."""
        if conn.execute("SELECT 1 FROM attention WHERE code=? AND ref_type='instrument' AND ref_id=? AND resolved_ts IS NULL", (REPAIR_CODE, iid)).fetchone():
            return
        fix = ("cancel that order (or let it expire) and restart the terminal to correct it" if reason.startswith("open order")
               else "it is not changed automatically")
        msg = f"{iid} ({name}) is a foreign listing still recorded as a US equity (USD, US Exchange, Yahoo symbol {iid}) because {reason}: {fix}"
        details = {"instrument_id": iid, "reason": reason, "ref_type": "instrument", "ref_id": iid, "auto_resolve": 1}
        conn.execute("INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
                     " VALUES (?, 'WARN', 'instruments', ?, ?, ?, NULL, 'instrument', ?, NULL, 1)", (ts, REPAIR_CODE, msg, json.dumps(details, sort_keys=True), iid))

    def ensure_futures_listings(self, tx, today: date, cal) -> int:
        """List the next 4 months of every root (§12.3); mark contracts past their last trade date EXPIRED.  Returns new rows."""
        conn = tx.conn
        ts = int(tx.now.timestamp())
        n = 0
        for code, spec in FUTURES_SPECS.items():
            for y, m in listed_months(code, today, cal):
                iid = futures_id(code, y, m)
                lt = last_trade_date(code, y, m, cal)
                n += self._insert(conn, ts, instrument_id=iid, kind="FUTURE", symbol=iid, name=f"{spec.name} {MONTH_NAMES[m - 1]} {y}", currency="USD",
                                  multiplier=str(spec.multiplier), tick=str(spec.tick), session_group=spec.group, settle_days=0,
                                  account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=futures_yahoo(code, y, m), expiry=lt.isoformat(),
                                  spec={"root": code, "month": m, "year": y, "im_c": spec.im_c, "mm_c": spec.mm_c, "settle_time_et": spec.settle_time_et,
                                        "last_trade": lt.isoformat(), "trade_date_rule": "next_business_day_after_18:00", "note": spec.note},
                                  support="FULL")
        conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE kind='FUTURE' AND status='ACTIVE' AND expiry < ?", (ts, today.isoformat()))
        return n

    def _seed_private(self, conn: sqlite3.Connection, ts: int) -> int:
        path = config.private_companies_path()
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as e:
            if self.log:
                self.log.warning("private_companies.json unreadable: %s", e)
            return 0
        rows = raw.get("companies") if isinstance(raw, dict) else raw
        n = 0
        for c in rows or []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            slug = re.sub(r"[^A-Z0-9]+", "", str(c.get("company_id") or c["name"]).upper().replace("PRV-", ""))
            cid = c.get("company_id") or f"PRV-{slug}"
            iid = c.get("instrument_id") or cid
            n += self._insert(conn, ts, instrument_id=iid, kind="PRIVATE", symbol=iid, name=c["name"], currency="USD", session_group="PRIVATE_MARKETS",
                              settle_days=int(c.get("settle_days") or config.PRIVATE_SETTLE_DAYS), account_kinds=["PRIVATE"],
                              spec={"sector": c.get("sector"), "country": c.get("country"), "status": c.get("status")}, support="COMPUTED")
            conn.execute("INSERT OR IGNORE INTO private_companies(company_id, name, sector, country, status, ticker, instrument_id, notes, updated_ts)"
                         " VALUES (?,?,?,?,?,?,?,?,?)",
                         (cid, c["name"], c.get("sector"), c.get("country"), (c.get("status") or "PRIVATE").upper(), c.get("ticker"), iid, c.get("notes"), ts))
            lr = c.get("last_round")
            if isinstance(lr, dict) and lr.get("round_date") and lr.get("source_url"):
                pm = lr.get("post_money_usd")
                conn.execute("INSERT OR IGNORE INTO private_rounds(company_id, round_date, round_type, post_money_c, price_per_share, lead_investors,"
                             " source_url, as_of, recorded_by, command_id) VALUES (?,?,?,?,?,?,?,?,'seed',NULL)",
                             (cid, lr["round_date"], lr.get("round_type") or "round", None if pm is None else int(Decimal(str(pm)) * 100),
                              None if lr.get("price_per_share") is None else str(qprice(str(lr["price_per_share"]))),
                              ", ".join(lr["lead_investors"]) if isinstance(lr.get("lead_investors"), list) else lr.get("lead_investors"),
                              lr["source_url"], lr.get("as_of") or lr["round_date"]))
        return n

    def ensure_option(self, tx, occ: str, underlying: Instrument, chain_row) -> Instrument:
        """Materialise an OPTION row from a Cboe chain row on first sight."""
        parsed = parse_occ(occ)
        if parsed is None:
            raise ValueError(f"not an OCC symbol: {occ}")
        root, expiry, right, strike = parsed
        index = underlying.kind == "INDEX" or root in INDEX_OPTION_ROOTS
        cboe = getattr(chain_row, "occ", None) or (chain_row.get("option") if isinstance(chain_row, dict) else None) or occ
        self._insert(tx.conn, int(tx.now.timestamp()), instrument_id=occ, kind="OPTION", symbol=occ,
                     name=f"{underlying.symbol} {expiry.isoformat()} {'C' if right == 'C' else 'P'}{strike}", currency="USD", multiplier="100", tick="0.01",
                     session_group="US_INDEX_OPTIONS" if index else "US_EQUITY_OPTIONS", settle_days=1, account_kinds=["US_EXCHANGE", "PB"],
                     cboe_symbol=cboe, underlying_id=underlying.instrument_id, expiry=expiry.isoformat(), strike=str(strike), right=right,
                     style="EUROPEAN" if index else "AMERICAN", exercise="CASH" if index else "PHYSICAL",
                     spec={"root": root, "index": index}, support="FULL")
        return self.get(occ, tx.conn)

    def upsert_from_yahoo_meta(self, tx, symbol: str, meta: dict) -> Instrument:
        """Resolve an unknown ticker from Yahoo chart meta (§7.3): US exchanges -> FULL; a non-US venue or a non-USD quote
        -> the foreign-listing classification the seed also writes (``foreign_listing_fields``: GBp -> GBP x 0.01,
        FOREIGN, T+2, FOREIGN_FX + PB, FULL on a known venue else LIMITED); crypto / mutual funds UNSUPPORTED.  A Yahoo symbol another row
        already owns (``0700.HK`` is the seeded ``0700-HK``, ``JPY=X`` is ``USDJPY``) is refused with that row's id
        (RejectError ``ALREADY_IN_MASTER``) instead of an insert the unique Yahoo-symbol index would silently drop."""
        from .commands import RejectError
        holder = self._yahoo_holder(tx.conn, symbol)
        if holder is not None and holder != symbol:
            raise RejectError("ALREADY_IN_MASTER", f"{symbol} is already in the instrument master as {holder}: use {holder}",
                              {"symbol": symbol, "instrument_id": holder})
        itype = str(meta.get("instrumentType") or "").upper()
        if itype == "OPTION":
            raise RejectError("OPTION_SYMBOL", f"{symbol} is an option contract: open it from its underlying's option chain "
                              "(the chain lists it in the master)", {"symbol": symbol})
        exch = str(meta.get("exchangeName") or "")
        raw_ccy = str(meta.get("currency") or "USD").strip()
        ccy = raw_ccy.upper()
        kind = "ETF" if itype == "ETF" else "EQUITY"
        if itype == "FUTURE":
            # Yahoo's continuous ``<root>=F`` symbol (decisions s7.1): a LIMITED reference row, never an order; the listed
            # contract (``ESZ26``) is the tradeable instrument and is seeded four months ahead by ensure_futures_listings
            root = symbol[:-2].upper() if symbol.upper().endswith("=F") else symbol.upper()
            fs = FUTURES_SPECS.get(root)
            group = getattr(fs, "group", None) or "US_EQUITY"
            self._insert(tx.conn, int(tx.now.timestamp()), instrument_id=symbol, kind="INDEX", symbol=symbol,
                         name=meta.get("longName") or meta.get("shortName") or symbol, currency=ccy, session_group=group, settle_days=0, account_kinds=[],
                         yahoo_symbol=symbol, cboe_symbol=None,
                         spec={"reference": True, "continuous": True, "root": root, "exchange": exch, "full_exchange": meta.get("fullExchangeName"),
                               "tz": meta.get("exchangeTimezoneName"), "instrument_type": itype,
                               "note": f"continuous Yahoo symbol (reference only): trade the listed {root} contract, e.g. {root}Z26" if fs is not None
                               else "continuous Yahoo symbol (reference only): this root has no contract spec in this build"},
                         support="LIMITED")
            return self.get(symbol, tx.conn)
        ts = int(tx.now.timestamp())
        name = meta.get("longName") or meta.get("shortName") or symbol
        spec = {"exchange": exch, "full_exchange": meta.get("fullExchangeName"), "tz": meta.get("exchangeTimezoneName"), "instrument_type": itype}
        if itype in ("CRYPTOCURRENCY", "MUTUALFUND"):
            self._insert(tx.conn, ts, instrument_id=symbol, kind="EQUITY", symbol=symbol, name=name, currency=ccy, session_group=FOREIGN_GROUP, settle_days=2,
                         account_kinds=[], yahoo_symbol=symbol, cboe_symbol=None, spec=spec, support="UNSUPPORTED")
        elif exch not in US_EXCHANGES or ccy != "USD":
            try:
                fields = foreign_listing_fields(symbol, raw_ccy)
            except ValueError as e:
                raise RejectError("UNSUPPORTED", f"{symbol}: {e}", {"symbol": symbol, "currency": raw_ccy}) from e
            spec.update(fields.pop("spec"))
            self._insert(tx.conn, ts, instrument_id=symbol, kind=kind, symbol=symbol, name=name, spec=spec, **fields)
        else:
            self._insert(tx.conn, ts, instrument_id=symbol, kind=kind, symbol=symbol, name=name, currency=ccy, session_group="US_EQUITY", settle_days=1,
                         account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=symbol, cboe_symbol=symbol.replace("-", "."), spec=spec, support="FULL")
        return self.get(symbol, tx.conn)

    def ensure_foreign_listing(self, tx, yahoo_symbol: str, meta: dict) -> Instrument:
        """A foreign listing from Yahoo meta: the shared classification (``foreign_listing_fields``): FULL on a known venue in a
        currency with a USD pair, else LIMITED with the reason."""
        return self.upsert_from_yahoo_meta(tx, yahoo_symbol, dict(meta, exchangeName=meta.get("exchangeName") or "FOREIGN"))

    # ---- rules ----------------------------------------------------------------------------------------------------
    def tradeable_in(self, inst: Instrument, account_kind: str) -> bool:
        """§7.2: pure tradeability by account kind (US_EXCHANGE, DEBT, CREDIT, FOREIGN_FX, PRIVATE, PB, TREASURY).
        PRIVATE instruments trade only in PRIVATE accounts; SWAP rows only in PB accounts; Treasury only converts FX."""
        if account_kind == "TREASURY":
            return inst.kind == "FX"
        if inst.kind == "PRIVATE":
            return account_kind == "PRIVATE"
        if inst.kind == "SWAP":
            return account_kind == "PB"
        return account_kind in inst.account_kinds
