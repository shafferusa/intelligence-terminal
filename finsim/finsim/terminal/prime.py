"""Prime brokers: accounts, editable fee schedules, financing headroom, nightly financing and credit-interest lines,
the monthly minimum, the OTC gate (TERMINAL_DESIGN.md §2.12 prime.py, §4.1, §5.4, §6.1, §11.2, §11.3, §12.4, §13.7,
§13.10, §19.23; TERMINAL_WORKPLAN.md WP-07).

Conventions stated here (the §1.3 sentences and the §12.4 / §13.7 rules that bite in this module)
- One account per prime broker and lifetime: ``account.open_pb {lifetime_id | book_code, broker}`` creates
  ``{lifetime}:PB_{broker}`` (kind PB, ``fee_schedule`` = the broker key) for one of the NINE brokers of
  ``tables.PB_SCHEDULES`` (GS, JPM, MS, DB, BARC, CITI, UBS, BNP, BOFA).  Idempotent per broker: a replay of the same
  request id returns the stored result; a second open answers 409 ``EXISTS``; an unknown key 400 ``UNKNOWN_BROKER``.  A
  NEW relationship needs the eligibility threshold or the INSTITUTION profile (``books.require_onboard`` -> 400
  ``NOT_ELIGIBLE`` with the plain-English reason), which is what WP-23's ``book.onboard_pb`` checks before calling
  ``open_pb(..., check_eligibility=False)`` in the same transaction.  The account trades the US set DVP with the PB's
  fees: "the PB delivers or receives the shares" = the ordinary settlement rows of that account (§5.4, WP-02).
- Editable schedules (B4): the open copies the broker's seed row into ``pb_schedules.schedule_json``; ``tables.
  PB_SCHEDULES`` is only the seed.  ``schedule(account)`` reads the live row (seed when a row is missing, labelled
  ``source "seed"``); ``account.update_schedule`` (light) validates each field (decimal strings or cents, never
  floats; ``swap_margin_mode`` CTM | STM; nothing below zero) and writes ONE event ``SETTINGS_CHANGED`` with
  ``before`` / ``after`` per changed field.  The two money fields are accepted under the names the API serves them
  (``equity_min`` / ``monthly_minimum``: decimal USD, at most 2 decimals, converted to the stored integer cents with
  ``units.to_c``) or as stored (``equity_min_c`` / ``monthly_minimum_c``: integer cents); a missing, empty or
  unparsable value is 400 ``SCHEDULE_VALUE`` naming the field (``details.field``) -- never stored as 0.  Past postings are never restated: a fee already in the ledger keeps the
  schedule it was charged on; only later fills use the new numbers (§19.23: 0.0030 -> 3.00, then 0.0025 -> 2.50).
- Fees: WP-02's ``execution.fee_for`` asks ``services.prime.schedule(acct)`` for PB accounts and ``tables.fee_c``
  applies ``equity_per_share`` / ``equity_min_c`` / ``option_per_contract`` / ``futures_per_side`` (HALF_EVEN to the
  cent after the multiplication, floored by the minimum).
- PB financing headroom (§6.1, §12.4): ``50 % x long securities market value - balance(2700) - OTC IM (1330) - OTC VM
  posted (1320)``, capped by ``accounts.pb_limit_c`` (``limit - financed``; 0 = no cap), floored at 0, and 0 when the
  ``pb_financing`` switch is off.  Reg-T style initial margin, disclosed: equities / ETFs / ADRs / REITs / preferreds
  (bonds in Build 2) carry loan value; listed options, futures and swaps do not (Reg T gives long options no loan
  value; futures and swaps are margined, not financed).  Margin is per PB: a long at one PB never offsets a short at
  another; the IM / VM a PB account booked stays in that account.  ``bind(services)`` installs ``pb_headroom_c`` as
  ``accounting.pb_headroom_fn`` so ``accounting.buying_power`` and WP-09's Risk page show the same number.
- Financing interest (§11.3, kind ``pb``): base = the natural balance of 2700 open at 17:00 ET of night D (summed from
  the journal, so a draw after the cut-off belongs to the next night); rate = SOFR (latest published observation on or
  before D, RATE_LAG, through ``credit.index_observation``; none -> 409 ``RATE_UNAVAILABLE``, never a defaulted number)
  + the schedule's ``financing_spread``; ACT/360; cumulative rounding through WP-08's ``lifecycle.accrue_segment`` (the
  segment restarts when the base or the rate changes); ``Dr 5100 / Cr 2300`` (idem ``accr:pb:{account}:{D}``); paid
  monthly by WP-08's PAY_MONTHLY (2300 -> 1010).  Memo: "SOFR 3.87 % (2026-10-19) + 1.25 % = 5.12 %, ACT/360, night
  of 2026-10-20".
- PB credit interest (§12.4, kind ``pbcredit``): on POSITIVE settled USD (1010 at 17:00 ET of D) at ``max(0, SOFR -
  credit_interest_spread)`` ACT/360: ``Dr 1230 / Cr 4300`` (idem ``accr:pbcredit:{account}:{D}``); a floored rate or a
  non-positive balance posts nothing (the night is recorded as accrued with 0 so it is never retried).  4300 is a
  per-instrument code and the ledger refuses an empty instrument id (``ledger.validate`` INSTRUMENT_REQUIRED, L2), so
  the line carries the currency code ``USD`` as its instrument id (``CASH_INSTRUMENT``); no position is ever created
  from it (positions come from lots, swaps and private loans only).
- Monthly minimum (§12.4): ``max(0, monthly_minimum_c - fees charged in the month)`` where "fees charged" = the 5000
  debits of the account in that calendar month (business dates), excluding earlier minimum charges; posted ``Dr 5000
  / Cr 1010`` (instrument id ``PB:{broker}``, idem ``pay:pbmin:{account}:{yyyy-mm}``) after ``execution.fund``
  (immediate; the §11.6 overdraft rule: an unfundable charge is still posted with an ERROR ``FUNDING_SHORTFALL``).
  Nothing to charge -> SKIPPED with the figures.  Default 0 = no minimum.
- OTC via PB: IRS / TRS / cross-currency swaps are live (WP-17 / WP-26, eligible lifetimes only); OTC options, FRAs,
  caps / floors, swaptions and forwards answer 400 ``NOT_IN_THIS_BUILD`` (``require_otc_kind``) and are listed for
  ``/api/unsupported`` (``otc_unsupported``).
- ``account.close`` (§4.1) closes a FLAT prime-broker account only (no balance, order, pending settlement, loan,
  borrow or contract; 409 ``NOT_FLAT`` otherwise); the role id stays taken (a reopen is 409 ``EXISTS``), the history
  stays readable.  Catalogue accounts are never closed here.
Every number here is a disclosed, modelled assumption; nothing is simulated or invented.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from . import config, credit, execution, tables
from .books import _insert_account, account_row, lifetime_row, open_lifetime, require_onboard, split_account_id
from .clock import epoch_of, ny_of
from .commands import ConflictError, RejectError, TxContext
from .ledger import Entry, Line, balance, cr, dr, natural
from .units import fmt_money, from_c, qprice, to_c

D = Decimal
ACCRUAL_KIND_PB = "pb"
ACCRUAL_KIND_CREDIT = "pbcredit"
CASH_INSTRUMENT = "USD"                      # the instrument id of a cash-interest line on the per-instrument code 4300
FIN_BASIS = 360
LOAN_VALUE_KINDS = tuple(tables.EQUITY_KINDS) + ("BOND",)
BROKERS: Tuple[str, ...] = tuple(tables.PB_SCHEDULES)       # nine, incl. BOFA
PB_SORT_BASE = 60
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PRICE_8 = Decimal("0.00000001")

# §12.4 fields: name -> (kind, minimum, maximum, label).  Decimals are fractions (0.0125 = 1.25 %) or USD per unit;
# the ``*_c`` fields are integer cents; ``swap_margin_mode`` is an enum.
FIELD_SPECS: Dict[str, tuple] = {
    "equity_per_share": ("decimal", D("0"), D("100"), "USD per share (min equity_min_c per fill)"),
    "equity_min_c": ("cents", 0, None, "minimum equity fee per fill, cents"),
    "option_per_contract": ("decimal", D("0"), D("100"), "USD per option contract"),
    "futures_per_side": ("decimal", D("0"), D("100"), "USD per futures contract per side"),
    "financing_spread": ("decimal", D("0"), D("1"), "SOFR + spread on debit balances (fraction)"),
    "credit_interest_spread": ("decimal", D("0"), D("1"), "interest on positive settled USD = max(0, SOFR - spread) (fraction)"),
    "gc_rate": ("decimal", D("0"), D("1"), "general-collateral borrow fee (fraction)"),
    "htb_add": ("decimal", D("0"), D("1"), "borrow add-on over GC (fraction)"),
    "otc_spread_mult": ("decimal", D("0.01"), D("100"), "multiplies the IRS half-width and the TRS spread"),
    "swap_margin_mode": ("enum", ("CTM", "STM"), None, "swap variation margin: collateralised-to-market or settled-to-market"),
    "monthly_minimum_c": ("cents", 0, None, "monthly minimum commission, cents (0 = none)"),
    "irs_half_width_bp": ("decimal", D("0"), D("1000"), "IRS dealing half-width, basis points"),
    "trs_spread": ("decimal", D("0"), D("1"), "TRS financing spread over SOFR (fraction)"),
}
EDITABLE_FIELDS: Tuple[str, ...] = tuple(FIELD_SPECS)
# The money fields under the names the API serves them (the codec strips ``_c`` and sends decimal USD: ``equity_min``
# "1.00") -> the stored integer-cents field.  ``account.update_schedule`` accepts either name (never both at once).
MONEY_ALIASES: Dict[str, str] = {k[:-2]: k for k, spec in FIELD_SPECS.items() if spec[0] == "cents" and k.endswith("_c")}
_CENTS_RE = re.compile(r"^[0-9]+$")                          # integer cents, ASCII digits only
_MONEY_RE = re.compile(r"^[0-9]+(\.[0-9]{1,2})?$")           # decimal USD, at most 2 decimals, no sign, no separators
FIELDS_NOTE = tables._PB_FIELDS_NOTE

OTC_LIVE = ("IRS", "TRS", "XCCY")
OTC_NOT_BUILT = {
    "OPTION": "OTC equity options", "FX_OPTION": "OTC FX options", "SWAPTION": "swaptions", "FRA": "forward rate agreements",
    "CAP": "interest-rate caps", "FLOOR": "interest-rate floors", "FORWARD": "OTC forwards (equity / FX)",
}


class NotFound(RejectError):
    """Unknown account / not a prime-broker account -> HTTP 404 (nothing stored)."""
    http = 404


# ---- the schedule ------------------------------------------------------------------------------------------------------------
@dataclass
class PbSchedule:
    """The live schedule of one PB account (§12.4).  Decimal fields are ``Decimal``; ``*_c`` fields are integer cents;
    ``to_dict()`` is JSON-safe (decimals as strings) and is what ``execution.fee_for`` / ``credit.pb_draw`` read."""
    account_id: str
    broker: str
    name: str
    equity_per_share: Decimal
    equity_min_c: int
    option_per_contract: Decimal
    futures_per_side: Decimal
    financing_spread: Decimal
    credit_interest_spread: Decimal
    gc_rate: Decimal
    htb_add: Decimal
    otc_spread_mult: Decimal
    swap_margin_mode: str
    monthly_minimum_c: int
    irs_half_width_bp: Decimal
    trs_spread: Decimal
    note: str = ""
    updated_ts: int = 0
    source: str = "pb_schedules"

    def to_dict(self) -> dict:
        out = {"name": self.name}
        for k in EDITABLE_FIELDS:
            v = getattr(self, k)
            out[k] = str(v) if isinstance(v, Decimal) else v
        out["note"] = self.note
        return out

    def get(self, key, default=None):
        d = self.to_dict()
        return d.get(key, default)

    def __getitem__(self, key):
        return self.to_dict()[key]


def brokers() -> List[dict]:
    """The nine prime brokers with their seed schedules (the opener modal; §12.4)."""
    out = []
    for i, key in enumerate(BROKERS):
        out.append({"broker": key, "name": tables.PB_SCHEDULES[key]["name"], "role": f"PB_{key}", "sort_order": PB_SORT_BASE + i,
                    "schedule": seed_schedule(key), "agreements": ["ISDA_MASTER", "CSA", "PBA"]})
    return out


def seed_schedule(broker: str) -> dict:
    """A JSON-safe copy of ``tables.PB_SCHEDULES[broker]`` (decimals as their seed strings, e.g. "0.0030")."""
    seed = tables.PB_SCHEDULES.get(str(broker or "").upper())
    if seed is None:
        raise RejectError("UNKNOWN_BROKER", f"unknown prime broker {broker!r}; one of {', '.join(BROKERS)}", {"brokers": list(BROKERS)})
    out = {}
    for k, v in seed.items():
        out[k] = str(v) if isinstance(v, Decimal) else v
    return out


def _bad_value(name: str, message: str) -> RejectError:
    """400 ``SCHEDULE_VALUE`` naming the field in the message and in ``details.field``."""
    return RejectError("SCHEDULE_VALUE", f"{name}: {message}", {"field": name})


def _require_value(name: str, value, label: str) -> None:
    """Refuse a missing value (None, an empty or blank string), a bool and a float before any parsing, so nothing
    missing or unparsable can ever be stored as 0."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _bad_value(name, f"a value is required ({label}); a missing value is refused, never stored as 0")
    if isinstance(value, bool):
        raise _bad_value(name, f"{value!r} is not a value ({label})")
    if isinstance(value, float):
        raise _bad_value(name, f"send a decimal string, not a float ({value!r})")


def parse_field(name: str, value) -> object:
    """Validate one editable field; returns the stored form (decimal string / int / enum string).
    400 ``SCHEDULE_FIELD`` for an unknown name, 400 ``SCHEDULE_VALUE`` (``details.field`` = the name) for a missing or
    bad value (floats are refused on purpose: money and rates travel as decimal strings).  The money fields under the
    names the API serves them (``equity_min`` / ``monthly_minimum``) go through ``parse_money``."""
    if name in MONEY_ALIASES:
        return parse_money(name, value)
    spec = FIELD_SPECS.get(name)
    if spec is None:
        raise RejectError("SCHEDULE_FIELD", f"{name!r} is not a schedule field; editable: {', '.join(EDITABLE_FIELDS)} "
                          f"(or {', '.join(MONEY_ALIASES)} in USD)", {"field": name, "fields": list(EDITABLE_FIELDS), "money_fields": dict(MONEY_ALIASES)})
    kind, lo, hi, label = spec
    _require_value(name, value, label)
    if kind == "enum":
        v = str(value).strip().upper()
        if v not in lo:
            raise _bad_value(name, f"{value!r} is not one of {', '.join(lo)}")
        return v
    if kind == "cents":
        s = str(value).strip()
        if isinstance(value, int):
            n = int(value)
        elif _CENTS_RE.fullmatch(s):
            n = int(s)
        else:
            raise _bad_value(name, f"{value!r} is not an integer number of cents ({label})")
        if n < lo or (hi is not None and n > hi):
            raise _bad_value(name, f"{n} is outside [{lo}, {hi if hi is not None else 'inf'}] ({label})")
        return n
    try:
        d = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError) as e:
        raise _bad_value(name, f"{value!r} is not a decimal number ({label})") from e
    if not d.is_finite():
        raise _bad_value(name, f"{value!r} is not a finite number")
    if d != d.quantize(PRICE_8):
        raise _bad_value(name, f"at most 8 decimal places ({value!r})")
    if d < lo or d > hi:
        raise _bad_value(name, f"{d} is outside [{lo}, {hi}] ({label})")
    return str(d)


def parse_money(name: str, value) -> int:
    """A money field in MAJOR units as the API serves it -> the integer cents of its stored field: ``equity_min`` "1.50"
    -> 150 (``equity_min_c``), ``monthly_minimum`` "500.00" -> 50000 (``monthly_minimum_c``).  A plain decimal string
    (or an int: whole USD) at or above 0 with at most 2 decimals, converted with ``units.to_c`` (exact at 2 decimals).
    Missing (None, "", blank), a bool, a float, a sign, a thousands separator or a third decimal -> 400
    ``SCHEDULE_VALUE`` naming the field: a value that cannot be read is refused, never stored as 0.00."""
    field = MONEY_ALIASES.get(name)
    if field is None:
        raise RejectError("SCHEDULE_FIELD", f"{name!r} is not a money field of the schedule; one of {', '.join(MONEY_ALIASES)}",
                          {"field": name, "money_fields": dict(MONEY_ALIASES)})
    _kind, lo, hi, label = FIELD_SPECS[field]
    label = label.replace("cents", "USD")
    _require_value(name, value, label)
    s = str(int(value)) if isinstance(value, int) else str(value).strip()
    if not _MONEY_RE.fullmatch(s):
        raise _bad_value(name, f"{value!r} is not a USD amount: a plain decimal at or above 0 with at most 2 decimals, e.g. 1.00 ({label})")
    n = to_c(s)
    if n < lo or (hi is not None and n > hi):
        raise _bad_value(name, f"{s} is outside [{from_c(lo)}, {from_c(hi) if hi is not None else 'inf'}] ({label})")
    return n


def schedule_from_json(text: str, account_id: str, broker: str, updated_ts: int = 0, source: str = "pb_schedules") -> PbSchedule:
    """Build the dataclass from a stored ``schedule_json`` (a field missing from an older row takes the seed value)."""
    seed = seed_schedule(broker)
    try:
        data = json.loads(text or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    vals = {}
    for k in EDITABLE_FIELDS:
        raw = data.get(k, seed[k])
        kind = FIELD_SPECS[k][0]
        if kind == "decimal":
            vals[k] = Decimal(str(raw))
        elif kind == "cents":
            vals[k] = int(raw)
        else:
            vals[k] = str(raw).upper()
    return PbSchedule(account_id, broker, str(data.get("name") or seed["name"]), note=str(data.get("note") or seed.get("note") or ""),
                      updated_ts=int(updated_ts or 0), source=source, **vals)


def _acct(conn, account) -> dict:
    """An accounts row as a dict from a row / dict / account id; 404 ``ACCOUNT_UNKNOWN``."""
    if isinstance(account, str):
        row = account_row(conn, account)
        if row is None:
            raise NotFound("ACCOUNT_UNKNOWN", f"account {account} does not exist", {"account_id": account})
        return dict(row)
    return dict(account)


def broker_of(acct) -> str:
    b = acct["broker"] if "broker" in acct.keys() else None
    if not b and str(acct["role"]).startswith("PB_"):
        b = str(acct["role"])[3:]
    return str(b or "").upper()


def read_schedule(conn, account) -> PbSchedule:
    """The live schedule of a PB account (404 ``NOT_PB`` for any other account; the seed row, labelled ``source
    "seed"``, when ``pb_schedules`` has no row — an account inserted without ``account.open_pb``)."""
    acct = _acct(conn, account)
    if acct["kind"] != "PB":
        raise NotFound("NOT_PB", f"{acct['account_id']} is not a prime-broker account (kind {acct['kind']}); it has the DEFAULT fee schedule",
                       {"account_id": acct["account_id"], "kind": acct["kind"]})
    broker = broker_of(acct)
    if broker not in tables.PB_SCHEDULES:
        raise RejectError("UNKNOWN_BROKER", f"{acct['account_id']}: broker {broker!r} has no schedule", {"brokers": list(BROKERS)})
    row = conn.execute("SELECT schedule_json, updated_ts FROM pb_schedules WHERE account_id=?", (acct["account_id"],)).fetchone()
    if row is None:
        return schedule_from_json(json.dumps(seed_schedule(broker)), acct["account_id"], broker, int(acct.get("opened_ts") or 0), "seed")
    return schedule_from_json(row["schedule_json"], acct["account_id"], broker, int(row["updated_ts"]))


def schedule_view(conn, account) -> dict:
    """``GET /api/accounts/{id}/schedule`` (WP-11): the live schedule with its provenance and the seed for comparison."""
    s = read_schedule(conn, account)
    return {"account_id": s.account_id, "broker": s.broker, "name": s.name, "schedule": s.to_dict(), "updated_ts": s.updated_ts, "source": s.source,
            "seed": seed_schedule(s.broker), "fields_note": FIELDS_NOTE, "editable": list(EDITABLE_FIELDS),
            "labels": {k: FIELD_SPECS[k][3] for k in EDITABLE_FIELDS}}


# ---- the services-bound facade -----------------------------------------------------------------------------------------------
class Prime:
    """``services.prime``: what WP-02 (``fee_for``), WP-05 (``pb_schedule``), WP-17 (half-width / spread / margin mode)
    and WP-11 read.  Reads on a reader connection unless the caller passes its own."""

    def __init__(self, services):
        self.services = services

    def schedule(self, account, conn=None) -> PbSchedule:
        if conn is not None:
            return read_schedule(conn, account)
        with self.services.db.reader() as c:
            return read_schedule(c, account)

    def schedule_view(self, account, conn=None) -> dict:
        if conn is not None:
            return schedule_view(conn, account)
        with self.services.db.reader() as c:
            return schedule_view(c, account)

    def headroom(self, account_id: str, snapshot=None) -> dict:
        """The headroom detail of one PB account at the current (or given) mark snapshot."""
        acc = self.services.accounting
        snap = snapshot if snapshot is not None else acc.marks.snapshot()
        with self.services.db.reader() as c:
            return pb_headroom_detail(c, _acct(c, account_id), snap, acc)

    def brokers(self) -> List[dict]:
        return brokers()


def bind(services) -> Prime:
    """Install ``services.prime`` and the accounting headroom hook (the integrator / ``make_runtime`` call this)."""
    p = Prime(services)
    services.prime = p
    acc = getattr(services, "accounting", None)
    if acc is not None:
        acc.pb_headroom_fn = pb_headroom_c
    return p


# ---- headroom (§6.1, §12.4) ------------------------------------------------------------------------------------------------
HEADROOM_FORMULA = ("50 % of the PB account's long securities market value (equities / ETFs / ADRs / REITs / preferreds; listed options, futures and swaps"
                    " carry no loan value) - PB financing drawn (2700) - OTC IM (1330) - OTC VM posted (1320), capped by pb_limit (limit - drawn); Reg-T style, disclosed")


def pb_headroom_detail(conn, acct: dict, snap, accounting) -> dict:
    """Every component of the headroom of one PB account; ``headroom_c`` is 0 when ``pb_financing`` is off."""
    aid = acct["account_id"]
    on = acct.get("kind") == "PB" and bool(int(acct.get("pb_financing") or 0))
    calc = accounting._account_calc(conn, dict(acct), snap)
    long_mv = 0
    excluded = []
    for p in calc.positions:
        if p.mv_base_c is None or p.mv_base_c <= 0:
            continue
        if p.kind in LOAN_VALUE_KINDS:
            long_mv += int(p.mv_base_c)
        else:
            excluded.append(p.instrument_id)
    financed = balance(conn, aid, "2700", "USD")
    otc_im = balance(conn, aid, "1330", "USD")
    otc_vm = balance(conn, aid, "1320", "USD")
    gross = to_c(from_c(long_mv) * tables.PB_HEADROOM_PCT)
    uncapped = gross - financed - otc_im - otc_vm
    limit = int(acct.get("pb_limit_c") or 0)
    by_limit = (limit - financed) if limit > 0 else None
    head = uncapped if by_limit is None else min(uncapped, by_limit)
    head = max(0, head) if on else 0
    return {"account_id": aid, "pb_financing": on, "headroom_c": head, "long_mv_c": long_mv, "loan_value_c": gross, "financed_c": financed, "otc_im_c": otc_im,
            "otc_vm_posted_c": otc_vm, "pb_limit_c": limit, "headroom_by_limit_c": by_limit, "uncapped_c": uncapped, "no_loan_value": excluded,
            "pct": str(tables.PB_HEADROOM_PCT), "formula": HEADROOM_FORMULA, "marks_partial": bool(calc.missing)}


def pb_headroom_c(conn, acct: dict, snap, accounting) -> int:
    """The ``accounting.pb_headroom_fn`` hook (§6.1): cents of PB financing still available."""
    return int(pb_headroom_detail(conn, acct, snap, accounting)["headroom_c"])


# ---- accruals (§11.3) ---------------------------------------------------------------------------------------------------------
def _pct(x: Decimal) -> str:
    """'3.87' / '5.12' / '4.6250' — a label; the exact rate lives in accrual_state.rate."""
    q2 = x.quantize(Decimal("0.01"))
    return f"{q2:.2f}" if q2 == x else f"{x.quantize(Decimal('0.0001')):.4f}"


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d))


def _services(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _accrue_fn(services):
    lc = getattr(services, "lifecycle", None)
    fn = getattr(lc, "accrue_segment", None)
    if fn is None:
        raise RejectError("NOT_IN_THIS_BUILD", "PB accruals need the lifecycle accrual helper (WP-08 lifecycle.accrue_segment), which is not loaded")
    return fn


def night_cutoff_ts(night) -> int:
    """17:00 ET of calendar day ``night`` — the balance open then is the night's base (§11.2)."""
    return epoch_of(_as_date(night), config.ACCOUNTING_DAY_CLOSE_ET)


def balance_at(conn, account_id: str, gl: str, ccy: str, cutoff_ts: int) -> int:
    """Natural-sign balance of (account, gl, ccy) from the journal lines of entries posted at or before ``cutoff_ts``."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE l.account_id=? AND l.gl=? AND l.currency=? AND e.ts <= ?", (account_id, gl, ccy, int(cutoff_ts))).fetchone()
    return natural(gl, int(row[0] or 0))


def financing_rate(sofr_pct: Decimal, spread) -> Decimal:
    """Decimal fraction: ``SOFR % / 100 + financing_spread`` (3.87 % + 0.0125 -> 0.0512)."""
    return qprice(Decimal(str(sofr_pct)) / Decimal(100) + Decimal(str(spread)))


def credit_rate(sofr_pct: Decimal, spread) -> Decimal:
    """Decimal fraction: ``max(0, SOFR % / 100 - credit_interest_spread)`` (3.87 % - 0.0075 -> 0.0312)."""
    r = Decimal(str(sofr_pct)) / Decimal(100) - Decimal(str(spread))
    return qprice(r) if r > 0 else Decimal(0)


def _index(index) -> Tuple[Optional[date], Decimal]:
    obs_date, pct = (index if isinstance(index, (tuple, list)) else (None, index))
    return (obs_date if obs_date is None else _as_date(obs_date)), qprice(Decimal(str(pct)))


def _label(kind: str, obs_date, pct: Decimal, spread: Decimal, rate: Decimal, night: date) -> str:
    sign = "+" if kind == ACCRUAL_KIND_PB else "-"
    obs = f" ({obs_date.isoformat()})" if obs_date else ""
    return f"SOFR {_pct(pct)} %{obs} {sign} {_pct(spread * 100)} % = {_pct(rate * 100)} %, ACT/{FIN_BASIS}, night of {night.isoformat()}"


def accrual_lines_pb(ctx: TxContext, acct, d, sofr, fx=None) -> List[Line]:
    """Night-``d`` PB financing interest lines ``Dr 5100 / Cr 2300`` for the amount the cumulative-rounding segment
    yields (empty when nothing is drawn or it rounds to zero).  ``sofr`` = ``(obs_date, percent)`` or a bare percent;
    ``fx`` unused (USD).  Mutates ``accrual_state`` (kind ``pb``, ref = account id); the caller posts the entry."""
    acct = _acct(ctx.conn, acct)
    aid = acct["account_id"]
    accrue = _accrue_fn(_services(ctx))
    night = _as_date(d)
    obs_date, pct = _index(sofr)
    sched = read_schedule(ctx.conn, acct)
    rate = financing_rate(pct, sched.financing_spread)
    base = max(0, balance_at(ctx.conn, aid, "2700", "USD", night_cutoff_ts(night)))
    amount = int(accrue(ctx, ACCRUAL_KIND_PB, aid, base, rate, FIN_BASIS, night))
    if amount == 0:
        return []
    label = _label(ACCRUAL_KIND_PB, obs_date, pct, sched.financing_spread, rate, night)
    return [dr(aid, "5100", "USD", amount, memo=label), cr(aid, "2300", "USD", amount, memo=f"accrued to {sched.name}")]


def accrual_lines_pb_credit(ctx: TxContext, acct, d, sofr, fx=None) -> List[Line]:
    """Night-``d`` PB credit interest lines ``Dr 1230 / Cr 4300`` (instrument ``USD``) on positive settled USD at
    ``max(0, SOFR - credit_interest_spread)``; empty when the balance is not positive, the rate floors at 0 or the
    segment rounds to zero.  Mutates ``accrual_state`` (kind ``pbcredit``)."""
    acct = _acct(ctx.conn, acct)
    aid = acct["account_id"]
    accrue = _accrue_fn(_services(ctx))
    night = _as_date(d)
    obs_date, pct = _index(sofr)
    sched = read_schedule(ctx.conn, acct)
    rate = credit_rate(pct, sched.credit_interest_spread)
    base = max(0, balance_at(ctx.conn, aid, "1010", "USD", night_cutoff_ts(night)))
    amount = int(accrue(ctx, ACCRUAL_KIND_CREDIT, aid, base, rate, FIN_BASIS, night))
    if amount == 0:
        return []
    label = _label(ACCRUAL_KIND_CREDIT, obs_date, pct, sched.credit_interest_spread, rate, night)
    return [dr(aid, "1230", "USD", amount, memo=label), cr(aid, "4300", "USD", amount, instrument_id=CASH_INSTRUMENT, memo=f"credit interest from {sched.name}")]


def accrual_state(conn, kind: str, account_id: str):
    return conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (kind, account_id)).fetchone()


def nights_due(conn, acct, today, kind: str = ACCRUAL_KIND_PB) -> List[date]:
    """Calendar nights D with ``opened ≤ D < today`` not yet recorded in ``accrual_state`` for (kind, account) —
    WP-08's ACCRUE_DAILY keys ``{kind}:{account}:{D}``."""
    acct = _acct(conn, acct)
    today = _as_date(today)
    start = ny_of(int(acct["opened_ts"])).date()
    st = accrual_state(conn, kind, acct["account_id"])
    if st is not None and st["accrued_through"]:
        start = max(start, _as_date(st["accrued_through"]) + timedelta(days=1))
    out = []
    d = start
    while d < today:
        out.append(d)
        d += timedelta(days=1)
    return out


def _open_pb_account(conn, account_id: str) -> dict:
    acct = _acct(conn, account_id)
    if acct["kind"] != "PB":
        raise NotFound("NOT_PB", f"{account_id} is not a prime-broker account", {"account_id": account_id, "kind": acct["kind"]})
    lrow = lifetime_row(conn, acct["lifetime_id"])
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {acct['lifetime_id']} is closed (History is read-only)")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def post_pb_accrual(ctx: TxContext, services, acct, night, kind: str = ACCRUAL_KIND_PB, force: bool = False) -> dict:
    """One night of one PB account as an entry (idempotent on ``accr:{kind}:{account}:{D}``; a night with nothing to
    post is recorded as accrued with 0 and reported ``skipped`` with the reason).  Returns ``{account_id, night, kind,
    amount_c, entry_id, rate, index, base_c, cum_c, segment_start, skipped, note}``."""
    if kind not in (ACCRUAL_KIND_PB, ACCRUAL_KIND_CREDIT):
        raise RejectError("ACCRUAL_KIND", f"kind must be {ACCRUAL_KIND_PB} or {ACCRUAL_KIND_CREDIT}, got {kind!r}")
    night = _as_date(night)
    acct = _acct(ctx.conn, acct)
    aid = acct["account_id"]
    idem = f"accr:{kind}:{aid}:{night.isoformat()}"
    existing = ctx.conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key=?", (idem,)).fetchone()
    if existing is not None:
        return {"account_id": aid, "night": night.isoformat(), "kind": kind, "amount_c": 0, "entry_id": int(existing["entry_id"]), "skipped": True,
                "note": "already posted"}
    _open_pb_account(ctx.conn, aid)
    opened = ny_of(int(acct["opened_ts"])).date()
    if night < opened:
        raise RejectError("NIGHT_BEFORE_OPEN", f"night {night.isoformat()} is before the account's opening day {opened.isoformat()}")
    if night.isoformat() >= ctx.today and not force:
        raise RejectError("NIGHT_NOT_ENDED", f"the night of {night.isoformat()} has not ended (accounting day {ctx.today}); accruals post from 00:30 the next day")
    sid, obs_date, pct = credit.index_observation(services, "USD", night)
    sched = read_schedule(ctx.conn, acct)
    if kind == ACCRUAL_KIND_PB:
        gl, spread = "2700", sched.financing_spread
        rate = financing_rate(pct, spread)
        lines = accrual_lines_pb(ctx, acct, night, (obs_date, pct))
    else:
        gl, spread = "1010", sched.credit_interest_spread
        rate = credit_rate(pct, spread)
        lines = accrual_lines_pb_credit(ctx, acct, night, (obs_date, pct))
    base = max(0, balance_at(ctx.conn, aid, gl, "USD", night_cutoff_ts(night)))
    st = accrual_state(ctx.conn, kind, aid)
    out = {"account_id": aid, "night": night.isoformat(), "kind": kind, "amount_c": 0, "entry_id": None, "rate": str(rate), "spread": str(spread),
           "index": {"series": sid, "value": str(pct), "obs_date": obs_date.isoformat()}, "base_c": base, "cum_c": int(st["cum_c"]) if st else 0,
           "segment_start": st["segment_start"] if st else None, "skipped": False, "note": ""}
    if not lines:
        if base <= 0:
            out["note"] = "no PB financing drawn at 17:00 ET" if kind == ACCRUAL_KIND_PB else "no positive settled USD at 17:00 ET"
        elif rate == 0:
            out["note"] = f"rate floored at 0 % (SOFR {_pct(pct)} % - {_pct(spread * 100)} %)"
        else:
            out["note"] = "rounds to zero this night (cumulative rounding)"
        out["skipped"] = True
        return out
    amount = lines[0].amount_c
    what = "PB financing interest" if kind == ACCRUAL_KIND_PB else "PB credit interest"
    entry_id = ctx.post(Entry("ACCRUAL", f"{what} {aid}: USD {fmt_money(amount)} - {lines[0].memo}, base {fmt_money(base)}", lines, idem, ref_type="account", ref_id=aid))
    ctx.event("ACCRUED", account_id=aid, ref_type="account", ref_id=aid, kind=kind, night=night.isoformat(), amount_c=amount, currency="USD", rate=str(rate),
              index=out["index"], base_c=base, entry_id=entry_id, broker=sched.broker)
    out.update(amount_c=amount, entry_id=entry_id)
    return out


# ---- monthly minimum (§12.4) --------------------------------------------------------------------------------------------------
def _month(month) -> str:
    m = str(month or "").strip()
    if not MONTH_RE.match(m):
        raise RejectError("MONTH", f"month must be YYYY-MM, got {month!r}")
    return m


def fees_charged_c(conn, account_id: str, month: str) -> int:
    """Natural USD cents on 5000 of the account for entries with a business date in ``month``, excluding earlier
    monthly-minimum charges (``pay:pbmin:*``)."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE l.account_id=? AND l.gl='5000' AND l.currency='USD' AND e.business_date >= ? AND e.business_date < ?"
                       " AND e.idem_key NOT LIKE 'pay:pbmin:%'", (account_id, month + "-01", _next_month(month) + "-01")).fetchone()
    return natural("5000", int(row[0] or 0))


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def monthly_minimum_line(ctx: TxContext, acct, month) -> Tuple[List[Line], dict]:
    """``(lines, detail)`` of the PB_MONTHLY_MIN charge for ``month``: ``Dr 5000 / Cr 1010`` of ``max(0, minimum -
    fees charged)``; no lines when nothing is due.  Pure apart from the reads."""
    month = _month(month)
    acct = _acct(ctx.conn, acct)
    aid = acct["account_id"]
    sched = read_schedule(ctx.conn, acct)
    fees = fees_charged_c(ctx.conn, aid, month)
    minimum = int(sched.monthly_minimum_c)
    shortfall = max(0, minimum - fees)
    idem = f"pay:pbmin:{aid}:{month}"
    detail = {"account_id": aid, "broker": sched.broker, "month": month, "minimum_c": minimum, "fees_charged_c": fees, "shortfall_c": shortfall, "idem_key": idem,
              "formula": "max(0, monthly_minimum_c - commissions charged in the month)"}
    if shortfall <= 0:
        return [], detail
    memo = f"PB monthly minimum {month}: minimum {fmt_money(minimum)} - fees {fmt_money(fees)} = {fmt_money(shortfall)}"
    return [dr(aid, "5000", "USD", shortfall, instrument_id=f"PB:{sched.broker}", memo=memo), cr(aid, "1010", "USD", shortfall, memo=f"paid to {sched.name}")], detail


def post_monthly_minimum(ctx: TxContext, services, acct, month, force: bool = False) -> dict:
    """The PB_MONTHLY_MIN unit for one account and month: funds the shortfall (``execution.fund``, immediate, §11.6
    overdraft rule) and posts it; idempotent on ``pay:pbmin:{account}:{yyyy-mm}``; SKIPPED with the figures when
    nothing is due or the minimum is 0."""
    month = _month(month)
    acct = _acct(ctx.conn, acct)
    aid = acct["account_id"]
    idem = f"pay:pbmin:{aid}:{month}"
    existing = ctx.conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key=?", (idem,)).fetchone()
    if existing is not None:
        return {"account_id": aid, "month": month, "amount_c": 0, "entry_id": int(existing["entry_id"]), "skipped": True, "note": "already charged"}
    _open_pb_account(ctx.conn, aid)
    if month >= ctx.today[:7] and not force:
        raise RejectError("MONTH_NOT_ENDED", f"month {month} has not ended (accounting day {ctx.today}); the minimum is charged with PAY_MONTHLY on the 1st")
    lines, detail = monthly_minimum_line(ctx, acct, month)
    out = dict(detail, amount_c=0, entry_id=None, skipped=False, note="", funding=None)
    if not lines:
        out["skipped"] = True
        out["note"] = "no monthly minimum" if detail["minimum_c"] <= 0 else f"fees {fmt_money(detail['fees_charged_c'])} >= minimum {fmt_money(detail['minimum_c'])}"
        return out
    shortfall = int(detail["shortfall_c"])
    funding = execution.fund(ctx, aid, "USD", shortfall, "pbmin", idem, None, immediate=True, overdraft=True, services=services)
    entry_id = ctx.post(Entry("FEE", lines[0].memo, lines, idem, ref_type="account", ref_id=aid))
    ctx.event("PAID", account_id=aid, ref_type="account", ref_id=aid, kind="pbmin", month=month, amount_c=shortfall, currency="USD", minimum_c=detail["minimum_c"],
              fees_charged_c=detail["fees_charged_c"], entry_id=entry_id, funded=funding.funded, sweep_c=funding.sweep_c, pb_draw_c=funding.pb_draw_c)
    out.update(amount_c=shortfall, entry_id=entry_id, funding=funding.to_dict() if hasattr(funding, "to_dict") else None, settled_c=balance(ctx.conn, aid, "1010", "USD"))
    return out


# ---- OTC gate (§13.7, §20) ----------------------------------------------------------------------------------------------------
def require_otc_kind(kind) -> str:
    """IRS / TRS / XCCY pass (upper-cased); the Build-2 OTC products raise 400 ``NOT_IN_THIS_BUILD``; anything else
    400 ``OTC_KIND``."""
    k = str(kind or "").strip().upper()
    if k in OTC_LIVE:
        return k
    if k in OTC_NOT_BUILT:
        raise RejectError("NOT_IN_THIS_BUILD", f"{OTC_NOT_BUILT[k]} via a prime broker are not in this build (Build 2); swaps (IRS / TRS / XCCY) are live",
                          {"kind": k, "live": list(OTC_LIVE), "not_built": sorted(OTC_NOT_BUILT)})
    raise RejectError("OTC_KIND", f"unknown OTC kind {kind!r}; live: {', '.join(OTC_LIVE)}; not in this build: {', '.join(sorted(OTC_NOT_BUILT))}",
                      {"live": list(OTC_LIVE), "not_built": sorted(OTC_NOT_BUILT)})


def otc_unsupported() -> List[dict]:
    """Rows for ``/api/unsupported`` (§20): every OTC product that answers NOT_IN_THIS_BUILD."""
    return [{"item": f"PB OTC: {label}", "kind": k, "state": "NOT_IN_THIS_BUILD", "wording": "NOT IN THIS BUILD (Build 2); never priced with invented data",
             "where": "PB page OTC tab, /api/otc/preview"} for k, label in sorted(OTC_NOT_BUILT.items())]


# ---- opening and closing accounts ----------------------------------------------------------------------------------------------
def account_view(conn, account_id: str) -> dict:
    row = account_row(conn, account_id)
    if row is None:
        raise NotFound("ACCOUNT_UNKNOWN", f"account {account_id} does not exist", {"account_id": account_id})
    return dict(row)


def _resolve_lifetime(conn, payload: dict) -> str:
    lid = str(payload.get("lifetime_id") or "")
    if not lid:
        code = str(payload.get("book_code") or "")
        if not code:
            raise RejectError("LIFETIME_REQUIRED", "lifetime_id (or book_code) is required")
        row = open_lifetime(conn, code)
        if row is None:
            raise RejectError("LIFETIME_UNKNOWN", f"book {code} has no open lifetime")
        lid = row["lifetime_id"]
    lrow = lifetime_row(conn, lid)
    if lrow is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid} does not exist")
    if lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {lid} is closed (History is read-only)")
    return lid


def open_pb(ctx: TxContext, lifetime_id: str, broker: str, pb_financing: int = 0, pb_limit_c: int = 0, check_eligibility: bool = True, services=None) -> dict:
    """Create ``{lifetime}:PB_{broker}`` with its schedule copy inside the caller's command (WP-23's ``book.onboard_pb``
    calls this with ``check_eligibility=False`` after recording the agreements).  409 ``EXISTS`` when the role is
    already taken (OPEN or CLOSED)."""
    broker = str(broker or "").strip().upper()
    if broker not in tables.PB_SCHEDULES:
        raise RejectError("UNKNOWN_BROKER", f"unknown prime broker {broker!r}; one of {', '.join(BROKERS)}", {"broker": broker, "brokers": list(BROKERS)})
    lrow = lifetime_row(ctx.conn, lifetime_id)
    if lrow is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lifetime_id} does not exist")
    if lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {lifetime_id} is closed (History is read-only)")
    if check_eligibility:
        require_onboard(_services(ctx, services), lifetime_id)
    role = f"PB_{broker}"
    aid = f"{lifetime_id}:{role}"
    row = account_row(ctx.conn, aid)
    if row is not None:
        raise ConflictError("EXISTS", f"{aid} already exists ({row['status']}); one account per prime broker and lifetime",
                            {"account_id": aid, "status": row["status"], "broker": broker})
    fin = 1 if pb_financing in (1, True, "1", "true", "True") else 0
    try:
        limit = int(pb_limit_c or 0)
    except (TypeError, ValueError) as e:
        raise RejectError("SETTING_VALUE", f"pb_limit_c must be an integer number of cents, got {pb_limit_c!r}") from e
    if limit < 0:
        raise RejectError("SETTING_VALUE", "pb_limit_c must be >= 0 (0 = no cap beyond the headroom)")
    name = tables.PB_SCHEDULES[broker]["name"]
    sort = PB_SORT_BASE + BROKERS.index(broker)
    _insert_account(ctx.conn, ctx.ts, lrow["book_code"], lifetime_id, role, f"{name} (PB)", "PB", sort, broker=broker)
    ctx.conn.execute("UPDATE accounts SET pb_financing=?, pb_limit_c=? WHERE account_id=?", (fin, limit, aid))
    seed = seed_schedule(broker)
    ctx.conn.execute("INSERT INTO pb_schedules(account_id, schedule_json, updated_ts) VALUES (?,?,?)", (aid, json.dumps(seed, sort_keys=True), ctx.ts))
    ctx.touched_accounts.add(aid)
    ctx.event("ACCOUNT_OPENED", account_id=aid, ref_type="lifetime", ref_id=lifetime_id, role=role, broker=broker, name=name, pb_financing=fin, pb_limit_c=limit,
              schedule=seed, agreements=["ISDA_MASTER", "CSA", "PBA"])
    return {"account": account_view(ctx.conn, aid), "schedule": read_schedule(ctx.conn, aid).to_dict(), "account_id": aid, "broker": broker, "created": True,
            "schedule_view": schedule_view(ctx.conn, aid)}


def h_open_pb(ctx: TxContext, payload: dict) -> dict:
    """``account.open_pb {lifetime_id | book_code, broker, pb_financing?, pb_limit_c?}`` (actor ui)."""
    lid = _resolve_lifetime(ctx.conn, payload)
    return open_pb(ctx, lid, payload.get("broker"), payload.get("pb_financing", 0), payload.get("pb_limit_c", 0), True)


def h_close(ctx: TxContext, payload: dict) -> dict:
    """``account.close {account_id}``: a FLAT prime-broker account only (409 ``NOT_FLAT`` with the reasons)."""
    aid = str(payload.get("account_id") or "")
    acct = _open_pb_account(ctx.conn, aid)
    conn = ctx.conn
    reasons = []
    for r in conn.execute("SELECT gl, currency, instrument_id, balance_c FROM gl_balances WHERE account_id=? AND balance_c <> 0", (aid,)).fetchall():
        reasons.append(f"balance {r['gl']} {r['currency']}{(' ' + r['instrument_id']) if r['instrument_id'] else ''} {fmt_money(natural(r['gl'], int(r['balance_c'])))}")
    n = conn.execute("SELECT COUNT(*) FROM orders WHERE account_id=? AND status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')", (aid,)).fetchone()[0]
    if n:
        reasons.append(f"{n} open order(s)")
    n = conn.execute("SELECT COUNT(*) FROM settlements WHERE account_id=? AND status='PENDING'", (aid,)).fetchone()[0]
    if n:
        reasons.append(f"{n} pending settlement(s)")
    for table, what in (("loans", "open loan(s)"), ("borrows", "open borrow(s)"), ("otc_contracts", "open OTC contract(s)")):
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id=? AND status='OPEN'", (aid,)).fetchone()[0]
        if n:
            reasons.append(f"{n} {what}")
    if reasons:
        raise ConflictError("NOT_FLAT", f"{aid} is not flat: " + "; ".join(reasons) + " (close positions, settle, repay and transfer the cash to Treasury first)",
                            {"account_id": aid, "reasons": reasons})
    conn.execute("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE account_id=?", (ctx.ts, aid))
    ctx.event("ACCOUNT_CLOSED", account_id=aid, ref_type="lifetime", ref_id=acct["lifetime_id"], role=acct["role"], broker=acct["broker"])
    return {"account_id": aid, "status": "CLOSED", "closed_ts": ctx.ts, "account": account_view(conn, aid)}


def h_update_schedule(ctx: TxContext, payload: dict) -> dict:
    """``account.update_schedule {account_id, <field>: value, ...}`` (light; also accepts ``fields`` / ``schedule``
    sub-dicts).  Validates every field, writes ``pb_schedules`` and ONE ``SETTINGS_CHANGED`` event with before / after
    for the fields that changed; 400 ``NOTHING_TO_UPDATE`` when nothing differs.  Only the fields given are touched:
    every other field keeps its stored value.  The money fields may be sent as the API serves them (``equity_min`` /
    ``monthly_minimum``, decimal USD; ``parse_money``) or as stored (``*_c``, integer cents), one name per field (both
    -> 400 ``SCHEDULE_FIELD``); before / after name the stored field.  A missing or unparsable value is 400
    ``SCHEDULE_VALUE`` with ``details.field`` and nothing is written (never a silent 0)."""
    aid = str(payload.get("account_id") or "")
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    acct = _open_pb_account(ctx.conn, aid)
    current = read_schedule(ctx.conn, acct)
    stored = current.to_dict()
    given: Dict[str, object] = {}
    for sub in ("fields", "schedule"):
        d = payload.get(sub)
        if d is not None:
            if not isinstance(d, dict):
                raise RejectError("SCHEDULE_FIELD", f"{sub} must be an object of field: value")
            given.update(d)
    for k, v in payload.items():
        if k in ("account_id", "fields", "schedule", "request_id", "note"):
            continue
        given[k] = v
    if not given:
        raise RejectError("NOTHING_TO_UPDATE", f"no schedule field given; editable: {', '.join(EDITABLE_FIELDS)}")
    parsed: Dict[str, object] = {}                                  # stored field -> stored form; every value validated first
    for k, v in given.items():
        field = MONEY_ALIASES.get(k, k)
        if field != k and field in given:
            raise RejectError("SCHEDULE_FIELD", f"{k} (USD) and {field} (cents) name the same field: send one of them",
                              {"field": k, "fields": [k, field]})
        parsed[field] = parse_field(k, v)
    before, after = {}, {}
    new = dict(stored)
    for k, val in parsed.items():
        old = stored[k]
        changed = (Decimal(str(old)) != Decimal(str(val))) if FIELD_SPECS[k][0] == "decimal" else (old != val)
        if changed:
            before[k] = old
            after[k] = val
            new[k] = val
    if not after:
        raise RejectError("NOTHING_TO_UPDATE", "every given field already has that value", {"given": {k: str(v) for k, v in given.items()}})
    ctx.conn.execute("INSERT INTO pb_schedules(account_id, schedule_json, updated_ts) VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET"
                     " schedule_json=excluded.schedule_json, updated_ts=excluded.updated_ts", (aid, json.dumps(new, sort_keys=True), ctx.ts))
    note = str(payload.get("note") or "")
    event_id = ctx.event("SETTINGS_CHANGED", account_id=aid, ref_type="schedule", ref_id=aid, before=before, after=after, fields=sorted(after), broker=current.broker,
                         note=note, restated=False)
    view = schedule_view(ctx.conn, aid)
    return {"account_id": aid, "broker": current.broker, "before": before, "after": after, "schedule": view["schedule"], "updated_ts": view["updated_ts"],
            "event_id": event_id, "note": "past postings are never restated; later fills use the new schedule"}


def h_pb_accrue(ctx: TxContext, payload: dict) -> dict:
    """``prime.pb_accrue {account_id, night, kind: pb | pbcredit, force?}`` (actor lifecycle; the ACCRUE_DAILY unit)."""
    aid = str(payload.get("account_id") or "")
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    if not payload.get("night"):
        raise RejectError("NIGHT_REQUIRED", "night (YYYY-MM-DD) is required")
    try:
        night = date.fromisoformat(str(payload["night"]))
    except ValueError as e:
        raise RejectError("NIGHT", f"night must be YYYY-MM-DD, got {payload['night']!r}") from e
    return post_pb_accrual(ctx, _services(ctx), aid, night, str(payload.get("kind") or ACCRUAL_KIND_PB), bool(payload.get("force")))


def h_monthly_minimum(ctx: TxContext, payload: dict) -> dict:
    """``prime.monthly_minimum {account_id, month, force?}`` (actor lifecycle; the PB_MONTHLY_MIN unit)."""
    aid = str(payload.get("account_id") or "")
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    return post_monthly_minimum(ctx, _services(ctx), aid, payload.get("month"), bool(payload.get("force")))


def pb_accounts(conn, lifetime_id: Optional[str] = None, status: Optional[str] = "OPEN") -> List[dict]:
    """The PB accounts (of one lifetime), oldest first — WP-08's per-account job keys."""
    sql, params = "SELECT * FROM accounts WHERE kind='PB'", []
    if lifetime_id:
        sql += " AND lifetime_id=?"
        params.append(lifetime_id)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY lifetime_id, sort_order, account_id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def register(bus) -> None:
    bus.register("account.open_pb", h_open_pb)
    bus.register("account.close", h_close)
    bus.register("account.update_schedule", h_update_schedule, light=True)
    bus.register("prime.pb_accrue", h_pb_accrue)
    bus.register("prime.monthly_minimum", h_monthly_minimum)


__all__ = ["ACCRUAL_KIND_PB", "ACCRUAL_KIND_CREDIT", "CASH_INSTRUMENT", "BROKERS", "EDITABLE_FIELDS", "FIELD_SPECS", "FIELDS_NOTE", "OTC_LIVE", "OTC_NOT_BUILT",
           "NotFound", "PbSchedule", "Prime", "bind", "brokers", "seed_schedule", "parse_field", "parse_money", "MONEY_ALIASES", "schedule_from_json", "read_schedule", "schedule_view",
           "pb_headroom_detail", "pb_headroom_c", "HEADROOM_FORMULA", "night_cutoff_ts", "balance_at", "financing_rate", "credit_rate", "accrual_lines_pb",
           "accrual_lines_pb_credit", "accrual_state", "nights_due", "post_pb_accrual", "fees_charged_c", "monthly_minimum_line", "post_monthly_minimum",
           "require_otc_kind", "otc_unsupported", "account_view", "open_pb", "pb_accounts", "h_open_pb", "h_close", "h_update_schedule", "h_pb_accrue",
           "h_monthly_minimum", "register"]
