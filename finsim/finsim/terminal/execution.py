"""Applying fills, funding, system fills, option-margin moves, hedge resizing (TERMINAL_DESIGN.md §2.12 execution.py,
§5.4, §10.7, §10.8, §11.6, §13.2, §13.3).

Conventions stated here (the §1.3 sentences that bite in this module)
- FILL_PRICE: the price comes from the caller's observed quote (the matcher's decision); nothing here invents one.
- COST_BASIS: cost basis is gross; commissions go to 5000 with the instrument.  FIFO: lots are relieved oldest first
  through ``ledger.relieve_fifo``; the realised amount is the sum of the reliefs' ``realised_c``.
- Settlement: a fill's cash leg is a settlements row (receivable 1200 / payable 2100) due ``cal.settlement_date(trade
  date, settle_days)``; origin NEGOTIATED (private secondaries) settles after ``settle_days`` CALENDAR days; an EXPIRY
  fill at price 0 moves no cash and has no settlement row.
- SWEEP / funding (§10.8): own available cash (at the fill's settle date) -> one Treasury SWEEP_IN of exactly the
  shortfall, linked to what it paid for -> PB financing (WP-05/07 hook ``services.credit.pb_draw_for_funding``) ->
  refuse with the three amounts (``ConflictError INSUFFICIENT_FUNDS``).  ``immediate=True`` measures the need against
  SETTLED cash because the posting leaves 1010 at once (margin, collateral, variation margin); a fill's payable settles
  later, so receivables due by then count.
- The overdraft rule (§11.6): a lifecycle call that cannot be funded (``overdraft=True``) is still posted — the
  account's 1010 goes negative and an ERROR attention ``FUNDING_SHORTFALL`` for (account, currency) is opened in the
  same transaction; L18 accepts the negative balance only while that item is open; ``auto_resolve_shortfall`` closes
  it once 1010 is back at or above zero.  A Treasury that cannot cover the WHOLE shortfall sweeps nothing (one linked
  transfer of exactly the shortfall, or none).
- OPT_MARGIN: after an option fill the account's requirement is recomputed through ``services.options.
  margin_requirement_c`` (WP-03; absent -> NOT_IN_THIS_BUILD) and 1310 is moved to it (``omargin:{account}:{ts}:
  {command}``), funded first; ``margin_state`` is upserted so L16 ties.  AON packages recompute once for the package.
- Products: equities / ETFs / ADRs / REITs / preferreds and listed options post here; private shares use the same path
  with origin NEGOTIATED (WP-19); futures and FX dispatch to ``services.futures.apply_fill`` / ``services.fx.apply_fill``
  when those packages exist, else NOT_IN_THIS_BUILD; bonds are Build 2 (NOT_IN_THIS_BUILD).
- Shorts: an opening short consumes the order's borrow (``services.lending.consume_for_short``) and a cover returns the
  now-unneeded quantity (``return_borrow`` reason ``cover``); without the lending package (WP-06) those sides are
  NOT_IN_THIS_BUILD.  Nothing is ever cash-settled or invented in their place.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, List, Optional

from . import tables, treasury
from .accounting import available_c
from .books import account_row
from .clock import trade_date as _trade_date
from .commands import ConflictError, RejectError, TxContext
from .db import dumps, next_id
from .ledger import Entry, LedgerError, balance, cr, dr, open_lot, position, relieve_fifo
from .units import fmt_money, qprice

BUY_SIDES = ("BUY", "BUY_TO_COVER", "BTO", "BTC")
SELL_SIDES = ("SELL", "SELL_SHORT", "STO", "STC")
OPENING_SIDES = ("BUY", "SELL_SHORT", "BTO", "STO")
CLOSING_SIDES = ("SELL", "BUY_TO_COVER", "STC", "BTC")
EQUITY_KINDS = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")
LONG_GL = {"EQUITY": "1100", "ETF": "1100", "ADR": "1100", "REIT": "1100", "PREFERRED": "1100", "OPTION": "1700", "PRIVATE": "1120", "BOND": "1110"}
SHORT_GL = {"EQUITY": "2600", "ETF": "2600", "ADR": "2600", "REIT": "2600", "PREFERRED": "2600", "OPTION": "2900"}
PRICE_BASIS_OF_ORIGIN = {"EXERCISE": "strike", "ASSIGNMENT": "strike", "EXPIRY": "zero", "FUTURES_FINAL": "settle", "MATURITY": "par", "NEGOTIATED": "negotiated"}


def position_effect(side: str) -> str:
    """The fills.side of an order side: BUY for BUY / BUY_TO_COVER / BTO / BTC, SELL for the rest."""
    return "BUY" if side in BUY_SIDES else "SELL"


@dataclass
class FillSpec:
    order_id: Optional[str]
    account_id: str
    instrument_id: str
    side: str                       # the ORDER side: BUY SELL SELL_SHORT BUY_TO_COVER BTO STO BTC STC
    qty: int
    price: Decimal
    origin: str                     # ORDER | EXERCISE | ASSIGNMENT | EXPIRY | FUTURES_FINAL | MATURITY | NEGOTIATED
    quote: Any                      # QuoteRecord | None (provenance; stored whole in fills.quote_json)
    idem_key: str
    bar_ts: Optional[int]
    price_basis: str
    fee_override_c: Optional[int] = None
    cap_qty: Optional[int] = None
    cap_note: Optional[str] = None
    settle_days: Optional[int] = None       # NEGOTIATED: calendar days (default the instrument's settle_days)
    lock_until: Optional[str] = None        # private secondaries: the lot's lock-up end date
    borrow_id: Optional[str] = None
    defer_margin: bool = False              # package legs: the caller recomputes the option margin once
    note: str = ""


@dataclass
class FundingResult:
    need_c: int
    own_c: int
    shortfall_c: int
    sweep_id: Optional[str] = None
    sweep_c: int = 0
    pb_draw_c: int = 0
    funded: bool = True
    unfunded_c: int = 0
    source: str = "own"              # own | sweep | pb | overdraft | none
    treasury_available_c: Optional[int] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FillResult:
    fill_id: str
    entry_id: int
    account_id: str
    instrument_id: str
    side: str                        # order side
    effect: str                      # BUY | SELL (fills.side)
    qty: int
    price: Decimal
    gross_c: int
    fee_c: int
    fee_basis: str
    currency: str
    fx_rate: Decimal
    trade_date: str
    settle_date: Optional[str]
    settle_id: Optional[str]
    lot_id: Optional[int]
    reliefs: List[dict] = field(default_factory=list)
    cost_relieved_c: int = 0
    realised_c: int = 0
    funding: Optional[FundingResult] = None
    position_qty: int = 0
    memo: str = ""
    margin: Optional[dict] = None
    price_basis: str = ""           # last | ask | bid | limit | stop_print | strike | zero | settle | par | negotiated

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
            elif isinstance(v, FundingResult):
                d[k] = v.to_dict()
            else:
                d[k] = v
        return d


# ---- helpers ----------------------------------------------------------------------------------------------------------
def _services(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _schedule_for(services, acct) -> Optional[dict]:
    """A PB account's fee schedule through ``services.prime.schedule`` (WP-07); absent -> DEFAULT fees."""
    if acct is None or acct["kind"] != "PB":
        return None
    prime = getattr(services, "prime", None)
    if prime is None or not hasattr(prime, "schedule"):
        return None
    sched = prime.schedule(acct)
    if sched is None:
        return None
    if isinstance(sched, dict):
        return sched
    if hasattr(sched, "to_dict"):
        return sched.to_dict()
    return dict(getattr(sched, "__dict__", {}))


def fee_for(services, acct, inst, qty: int, gross_c: Optional[int] = None):
    """``(fee_c, fee_basis)`` for a fill of ``qty`` on the account's schedule (§12.1)."""
    return tables.fee_c(inst.kind, int(qty), _schedule_for(services, acct), gross_c=gross_c)


def _open_account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _fx_rate(ctx: TxContext, ccy: str) -> Decimal:
    try:
        return ctx.fx.rate(ccy)
    except LedgerError as e:
        raise ConflictError("FX_RATE_UNAVAILABLE", f"no USD rate for {ccy} in the mark snapshot: the fill cannot be valued in base", {"currency": ccy}) from e


def _quote_json(spec: FillSpec, ctx: TxContext, ccy: str, rate: Decimal) -> str:
    rec = spec.quote
    d = rec.to_dict() if rec is not None and hasattr(rec, "to_dict") else (dict(rec) if isinstance(rec, dict) else {})
    if ccy != "USD":
        d["fx"] = {"currency": ccy, "rate_to_usd": str(rate), "source": ctx.fx.source(ccy), "as_of": ctx.fx.as_of(ccy)}
    return dumps(d)


# ---- funding (§10.8, §11.6) ------------------------------------------------------------------------------------------
def fund(ctx: TxContext, account_id: str, currency: str, need_c: int, link_type: str, link_id: str, settle_date: Optional[date] = None, *,
         immediate: bool = False, overdraft: bool = False, services=None) -> FundingResult:
    """Make ``need_c`` of ``currency`` spendable in ``account_id`` (sentence SWEEP; order of funding §10.8).

    ``immediate``: the posting leaves settled cash now (margin, collateral, VM) -> the need is measured against 1010;
    otherwise against ``available`` at ``settle_date`` (receivables due by then count).  ``overdraft``: a lifecycle call
    that cannot be covered returns ``funded=False`` with an open FUNDING_SHORTFALL attention instead of raising (§11.6).
    """
    need_c = int(need_c)
    if need_c <= 0:
        return FundingResult(0, 0, 0, funded=True, source="none")
    services = _services(ctx, services)
    conn = ctx.conn
    acct = _open_account(conn, account_id)
    master = getattr(services, "instruments", None)
    if immediate:
        own = balance(conn, account_id, "1010", currency)
    else:
        own = available_c(conn, account_id, currency, settle_date, ctx.marks, master, ctx.now)
    shortfall = need_c - own
    if shortfall <= 0:
        return FundingResult(need_c, own, 0, funded=True, source="own")
    res = FundingResult(need_c, own, shortfall)
    try:
        tid = treasury.sweep_in(ctx, account_id, currency, shortfall, link_type, link_id, shortfall)
        res.sweep_id, res.sweep_c, res.source = tid, shortfall, "sweep"
        auto_resolve_shortfall(ctx, account_id, currency)
        return res
    except ConflictError as e:
        if e.code != "INSUFFICIENT_FUNDS":
            raise
        res.treasury_available_c = int(e.details.get("treasury_available_c", 0))
    # PB financing (WP-05 / WP-07 hook): draw up to headroom when the switch is on
    credit = getattr(services, "credit", None)
    if acct["kind"] == "PB" and int(acct["pb_financing"] or 0) and credit is not None and hasattr(credit, "pb_draw_for_funding"):
        drawn = int(credit.pb_draw_for_funding(ctx, services, account_id, shortfall, link_type, link_id) or 0)
        if drawn >= shortfall:
            res.pb_draw_c, res.source = drawn, "pb"
            return res
        res.pb_draw_c = drawn
    unfunded = shortfall - res.pb_draw_c
    details = {"need_c": need_c, "own_available_c": own, "treasury_available_c": res.treasury_available_c, "pb_draw_c": res.pb_draw_c,
               "shortfall_c": unfunded, "currency": currency, "account_id": account_id, "link_type": link_type, "link_id": link_id}
    if overdraft:
        res.funded, res.unfunded_c, res.source = False, unfunded, "overdraft"
        ctx.attention("ERROR", "execution", "FUNDING_SHORTFALL",
                      f"{account_id}: {currency} {fmt_money(unfunded)} for {link_type} {link_id} could not be funded (own available {fmt_money(own)},"
                      f" Treasury available {fmt_money(res.treasury_available_c or 0)}); the obligation is posted and settled cash is overdrawn",
                      dict(details, amount_c=unfunded, ref_type=link_type, ref_id=link_id, auto_resolve=1))
        ctx.event("ATTENTION", account_id=account_id, ref_type=link_type, ref_id=link_id, code="FUNDING_SHORTFALL", amount_c=unfunded, currency=currency)
        return res
    raise ConflictError("INSUFFICIENT_FUNDS",
                        f"{account_id} needs {currency} {fmt_money(need_c)}: own available {fmt_money(own)}, Treasury available "
                        f"{fmt_money(res.treasury_available_c or 0)}, PB financing {fmt_money(res.pb_draw_c)}", details)


def auto_resolve_shortfall(ctx: TxContext, account_id: str, currency: str) -> int:
    """Resolve the open FUNDING_SHORTFALL items of (account, currency) once settled cash is back at or above zero.
    Returns the number of items resolved (WP-08's housekeeping calls this after transfers and settlements)."""
    if balance(ctx.conn, account_id, "1010", currency) < 0:
        return 0
    rows = ctx.conn.execute("SELECT id, details_json FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL", (account_id,)).fetchall()
    n = 0
    for r in rows:
        try:
            ccy = str(json.loads(r["details_json"] or "{}").get("currency") or "USD")
        except ValueError:
            ccy = "USD"
        if ccy == currency:
            ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE id=?", (ctx.ts, int(r["id"])))
            n += 1
    return n


# ---- the fill (§10.7) ----------------------------------------------------------------------------------------------------
def apply_fill(ctx: TxContext, spec: FillSpec, services=None) -> FillResult:
    """Apply one fill inside the caller's transaction: funding -> postings (§5.4) -> lots -> fill row -> settlement row
    -> borrow consumption / return -> option margin.  Raises ``RejectError`` / ``ConflictError`` (the caller decides
    whether a funding refusal suspends a resting order or refuses a new one)."""
    services = _services(ctx, services)
    conn = ctx.conn
    acct = _open_account(conn, spec.account_id)
    inst = services.instruments.get(spec.instrument_id, conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {spec.instrument_id} is not in the master")
    if inst.kind == "FUTURE":
        fut = getattr(services, "futures", None)
        if fut is None or not hasattr(fut, "apply_fill"):
            raise RejectError("NOT_IN_THIS_BUILD", "futures execution is not yet built (WP-04)")
        return fut.apply_fill(ctx, spec, services)
    if inst.kind == "FX":
        fx = getattr(services, "fx", None)
        if fx is None or not hasattr(fx, "apply_fill"):
            raise RejectError("NOT_IN_THIS_BUILD", "FX spot deals go through fx.spot (WP-05); FX orders are not yet built")
        return fx.apply_fill(ctx, spec, services)
    if inst.kind not in LONG_GL:
        raise RejectError("NOT_IN_THIS_BUILD", f"{inst.kind} execution is not in this build")
    if inst.kind == "BOND":
        raise RejectError("NOT_IN_THIS_BUILD", "bonds are Build 2: model price, not executable in this build")
    side = spec.side
    if side not in BUY_SIDES + SELL_SIDES:
        raise RejectError("SIDE_INVALID", f"unknown order side {side!r}")
    qty = int(spec.qty)
    if qty <= 0:
        raise RejectError("QTY_INVALID", "fill quantity must be positive")
    price = qprice(spec.price)
    effect = position_effect(side)
    opening = side in OPENING_SIDES
    lot_side = "LONG" if side in ("BUY", "BTO", "SELL", "STC") else "SHORT"
    gross_c = inst.contract_value_c(price, qty)
    if spec.fee_override_c is not None:
        fee_c, fee_basis = int(spec.fee_override_c), "override (system fill / negotiated)"
    else:
        fee_c, fee_basis = fee_for(services, acct, inst, qty, gross_c)
    ccy = inst.currency
    rate = _fx_rate(ctx, ccy)
    tdate = _trade_date(ctx.now, inst.session_group, ctx.cal)
    if spec.origin == "EXPIRY" or inst.settle_days is None:
        settle_date: Optional[date] = None
    elif spec.origin == "NEGOTIATED":
        settle_date = tdate + timedelta(days=int(spec.settle_days if spec.settle_days is not None else inst.settle_days))
    else:
        settle_date = ctx.cal.settlement_date(tdate, inst.settle_days)
    # closing sides need the position
    pos_qty, _cost = position(conn, spec.account_id, spec.instrument_id)
    if not opening:
        have = pos_qty if lot_side == "LONG" else -pos_qty
        if have < qty:
            code = "OVERSELL" if side == "SELL" else "CLOSE_ONLY_MISMATCH"
            raise RejectError(code, f"{side} {qty:,} {inst.symbol} but the {lot_side.lower()} position is {max(have, 0):,}",
                              {"position": pos_qty, "qty": qty})
    fill_id = next_id(conn, "fill")
    conn.execute("PRAGMA defer_foreign_keys=ON")     # lot_relief rows may precede their fill row inside this transaction
    long_gl, short_gl = LONG_GL[inst.kind], SHORT_GL.get(inst.kind)
    lines = []
    reliefs: List[dict] = []
    cost_relieved = realised = 0
    settle_amount = 0
    settle_gl = None
    funding: Optional[FundingResult] = None
    lot_id: Optional[int] = None
    memo_tail = ""
    if opening and effect == "BUY":                     # BUY / BTO
        funding = fund(ctx, spec.account_id, ccy, gross_c + fee_c, "fill", fill_id, settle_date, services=services)
        lines.append(dr(spec.account_id, long_gl, ccy, gross_c, instrument_id=inst.instrument_id, memo="gross cost"))
        if fee_c:
            lines.append(dr(spec.account_id, "5000", ccy, fee_c, instrument_id=inst.instrument_id, memo=fee_basis))
        lines.append(cr(spec.account_id, "2100", ccy, gross_c + fee_c, memo="payable at settlement"))
        settle_amount, settle_gl = -(gross_c + fee_c), "2100"
    elif (not opening) and effect == "SELL":            # SELL / STC
        rl = relieve_fifo(ctx.tx, spec.account_id, inst.instrument_id, "LONG", qty, fill_id, gross_c)
        cost_relieved = sum(r.cost_relieved_c for r in rl)
        realised = sum(r.realised_c for r in rl)
        reliefs = [r.__dict__ for r in rl]
        if gross_c - fee_c:
            lines.append(dr(spec.account_id, "1200", ccy, gross_c - fee_c, memo="receivable at settlement"))
        if fee_c:
            lines.append(dr(spec.account_id, "5000", ccy, fee_c, instrument_id=inst.instrument_id, memo=fee_basis))
        if cost_relieved:
            lines.append(cr(spec.account_id, long_gl, ccy, cost_relieved, instrument_id=inst.instrument_id, memo="cost relieved FIFO"))
        if realised:
            lines.append(cr(spec.account_id, "4000", ccy, realised, instrument_id=inst.instrument_id, memo="realised"))
        settle_amount, settle_gl = gross_c - fee_c, "1200"
        memo_tail = f", FIFO cost {fmt_money(cost_relieved)}, realised {fmt_money(realised)}"
    elif opening and effect == "SELL":                  # SELL_SHORT / STO
        if short_gl is None:
            raise RejectError("NOT_IN_THIS_BUILD", f"{inst.kind} cannot be sold short in this build")
        if inst.kind in EQUITY_KINDS and (spec.borrow_id is None or getattr(services, "lending", None) is None):
            raise RejectError("NOT_IN_THIS_BUILD", "short sales need the securities-lending package (WP-06): no borrow backs this fill")
        if gross_c - fee_c:
            lines.append(dr(spec.account_id, "1200", ccy, gross_c - fee_c, memo="receivable at settlement"))
        if fee_c:
            lines.append(dr(spec.account_id, "5000", ccy, fee_c, instrument_id=inst.instrument_id, memo=fee_basis))
        lines.append(cr(spec.account_id, short_gl, ccy, gross_c, instrument_id=inst.instrument_id, memo="short proceeds"))
        settle_amount, settle_gl = gross_c - fee_c, "1200"
    else:                                               # BUY_TO_COVER / BTC
        if short_gl is None:
            raise RejectError("CLOSE_ONLY_MISMATCH", f"{inst.kind} has no short lots to cover")
        rl = relieve_fifo(ctx.tx, spec.account_id, inst.instrument_id, "SHORT", qty, fill_id, gross_c)
        cost_relieved = sum(r.cost_relieved_c for r in rl)
        realised = sum(r.realised_c for r in rl)
        reliefs = [r.__dict__ for r in rl]
        funding = fund(ctx, spec.account_id, ccy, gross_c + fee_c, "fill", fill_id, settle_date, services=services)
        if cost_relieved:
            lines.append(dr(spec.account_id, short_gl, ccy, cost_relieved, instrument_id=inst.instrument_id, memo="short proceeds relieved FIFO"))
        if fee_c:
            lines.append(dr(spec.account_id, "5000", ccy, fee_c, instrument_id=inst.instrument_id, memo=fee_basis))
        if gross_c + fee_c:
            lines.append(cr(spec.account_id, "2100", ccy, gross_c + fee_c, memo="payable at settlement"))
        if realised:
            lines.append(cr(spec.account_id, "4000", ccy, realised, instrument_id=inst.instrument_id, memo="realised"))
        settle_amount, settle_gl = -(gross_c + fee_c), "2100"
        memo_tail = f", FIFO proceeds {fmt_money(cost_relieved)}, realised {fmt_money(realised)}"
    memo = f"{side} {qty:,} {inst.symbol} @ {price}, fee {fmt_money(fee_c)}{memo_tail}" + (f" ({spec.note})" if spec.note else "")
    entry_kind = {"EXERCISE": "EXERCISE", "ASSIGNMENT": "ASSIGN", "EXPIRY": "EXPIRY", "FUTURES_FINAL": "FINAL"}.get(spec.origin, "FILL")
    if not lines:
        raise RejectError("ZERO_FILL", f"a fill of {qty:,} {inst.symbol} at {price} moves nothing")
    entry_id = ctx.post(Entry(entry_kind, memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
    conn.execute(
        "INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
        " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, spec.order_id, spec.account_id, inst.instrument_id, effect, qty, str(price), gross_c, fee_c, ccy, str(rate), ctx.ts, tdate.isoformat(),
         settle_date.isoformat() if settle_date else None, spec.origin, spec.price_basis, _quote_json(spec, ctx, ccy, rate), spec.bar_ts, spec.cap_qty,
         spec.cap_note, entry_id, spec.idem_key))
    if opening:
        lot_id = open_lot(ctx.tx, spec.account_id, inst.instrument_id, lot_side, fill_id, qty, price, gross_c, ccy, lock_until=spec.lock_until)
    settle_id = None
    if settle_date is not None and settle_amount:
        settle_id = next_id(conn, "settlement")
        conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (settle_id, fill_id, None, spec.account_id, "CASH", ccy, settle_amount, settle_gl, settle_date.isoformat()))
    ctx.touched_instruments.add((spec.account_id, inst.instrument_id))
    ctx.touched_accounts.add(spec.account_id)
    # shorts: consume / return the borrow (WP-06)
    if inst.kind in EQUITY_KINDS:
        lending = getattr(services, "lending", None)
        if opening and effect == "SELL":
            lending.consume_for_short(ctx, spec.borrow_id, qty, fill_id)
        elif (not opening) and effect == "BUY" and lending is not None and hasattr(lending, "return_for_cover"):
            lending.return_for_cover(ctx, services, spec.account_id, inst.instrument_id, qty, fill_id)
    margin = None
    if inst.kind == "OPTION" and not spec.defer_margin:
        margin = recompute_option_margin(ctx, services, spec.account_id, f"fill:{fill_id}")
    new_qty, _ = position(conn, spec.account_id, inst.instrument_id)
    return FillResult(fill_id, entry_id, spec.account_id, inst.instrument_id, side, effect, qty, price, gross_c, fee_c, fee_basis, ccy, rate, tdate.isoformat(),
                      settle_date.isoformat() if settle_date else None, settle_id, lot_id, reliefs, cost_relieved, realised, funding, new_qty, memo, margin,
                      spec.price_basis)


def apply_system_fill(ctx: TxContext, account_id: str, inst, side: str, qty: int, price, origin: str, quote, idem_key: str, note: str, services=None,
                      settle_days: Optional[int] = None, lock_until: Optional[str] = None, fee_c: int = 0) -> FillResult:
    """A fill that no order produced (exercise / assignment / expiry / futures final / maturity / negotiated): commission-free
    unless ``fee_c`` says otherwise, price basis from the origin, ``note`` in the memo.  ``side`` is an order side
    (SELL / STC closes a long, BUY_TO_COVER / BTC closes a short, BUY / BTO opens a long)."""
    basis = PRICE_BASIS_OF_ORIGIN.get(origin, "system")
    spec = FillSpec(None, account_id, getattr(inst, "instrument_id", inst), side, int(qty), qprice(price), origin, quote, idem_key, None, basis,
                    fee_override_c=int(fee_c), settle_days=settle_days, lock_until=lock_until, note=note)
    return apply_fill(ctx, spec, services)


# ---- option margin (§10.7 step 6, §13.3) ----------------------------------------------------------------------------------
def recompute_option_margin(ctx: TxContext, services, account_id: str, link_id: Optional[str] = None, overdraft: bool = False) -> dict:
    """Recompute the account's listed-option margin requirement (``services.options.margin_requirement_c``) and move
    1310 to it: an increase is funded first (``fund(immediate=True)``) and posted ``Dr 1310 / Cr 1010``; a decrease
    ``Dr 1010 / Cr 1310``; ``margin_state`` is upserted (L16).  Absent WP-03 -> NOT_IN_THIS_BUILD."""
    opt = getattr(services, "options", None)
    if opt is None or not hasattr(opt, "margin_requirement_c"):
        raise RejectError("NOT_IN_THIS_BUILD", "listed options: the margin module is not yet built (WP-03)")
    positions = services.accounting.positions(account_id, snapshot=ctx.marks)
    required_c, detail = opt.margin_requirement_c(positions, ctx.marks)
    required_c = int(required_c)
    conn = ctx.conn
    current = balance(conn, account_id, "1310", "USD")
    delta = required_c - current
    idem = f"omargin:{account_id}:{ctx.ts}:{ctx.command_id}"
    entry_id = None
    funding = None
    if delta > 0:
        funding = fund(ctx, account_id, "USD", delta, "margin", idem, None, immediate=True, overdraft=overdraft, services=services)
        entry_id = ctx.post(Entry("OPT_MARGIN", f"option margin to {fmt_money(required_c)} (+{fmt_money(delta)})",
                                  [dr(account_id, "1310", "USD", delta, memo="Reg T-style approximation (OPT_MARGIN)"), cr(account_id, "1010", "USD", delta)], idem,
                                  ref_type="margin", ref_id=link_id or idem))
    elif delta < 0:
        entry_id = ctx.post(Entry("OPT_MARGIN", f"option margin to {fmt_money(required_c)} ({fmt_money(delta)})",
                                  [dr(account_id, "1010", "USD", -delta), cr(account_id, "1310", "USD", -delta, memo="margin released")], idem,
                                  ref_type="margin", ref_id=link_id or idem))
    conn.execute("INSERT INTO margin_state(account_id, as_of_ts, options_required_c, options_detail_json, note) VALUES (?,?,?,?,?)"
                 " ON CONFLICT(account_id) DO UPDATE SET as_of_ts=excluded.as_of_ts, options_required_c=excluded.options_required_c,"
                 " options_detail_json=excluded.options_detail_json, note=excluded.note",
                 (account_id, ctx.ts, required_c, dumps(detail), tables.CONVENTIONS["OPT_MARGIN"]))
    ctx.touched_accounts.add(account_id)
    if delta:
        ctx.event("MARGIN_MOVED", account_id=account_id, ref_type="margin", ref_id=idem, required_c=required_c, delta_c=delta, link=link_id)
    return {"required_c": required_c, "previous_c": current, "delta_c": delta, "entry_id": entry_id, "idem_key": idem, "detail": detail,
            "funding": funding.to_dict() if funding else None}


# ---- hedge children (§10.7 step 8) ------------------------------------------------------------------------------------
def resize_hedge_children(ctx: TxContext, primary_order_id: str, primary_filled_qty: int, primary_ended: bool = False) -> List[dict]:
    """Size the WAITING_PARENT / working children of a primary to ``floor(primary filled × ratio)``; a child whose target
    is still 0 keeps waiting; when the primary ended with no fill the children are CANCELLED."""
    conn = ctx.conn
    out: List[dict] = []
    rows = conn.execute("SELECT * FROM orders WHERE parent_order_id=? AND status IN ('WAITING_PARENT','WORKING','PARTIAL','FILLED')", (primary_order_id,)).fetchall()
    for r in rows:
        if primary_ended and int(primary_filled_qty) == 0:
            if r["status"] in ("WAITING_PARENT", "WORKING", "PARTIAL"):
                conn.execute("UPDATE orders SET status='CANCELLED', reason=?, updated_ts=? WHERE order_id=?", ("primary ended unfilled", ctx.ts, r["order_id"]))
                conn.execute("INSERT INTO order_events(order_id, ts, from_status, to_status, note) VALUES (?,?,?,?,?)",
                             (r["order_id"], ctx.ts, r["status"], "CANCELLED", f"primary {primary_order_id} ended with no fill"))
                ctx.event("ORDER_CANCELLED", account_id=r["account_id"], instrument_id=r["instrument_id"], ref_type="order", ref_id=r["order_id"], reason="primary ended unfilled")
                out.append({"order_id": r["order_id"], "status": "CANCELLED"})
            continue
        ratio = Decimal(r["ratio"] or "0")
        target = int((Decimal(int(primary_filled_qty)) * ratio).to_integral_value(rounding="ROUND_FLOOR"))
        filled = int(r["filled_qty"])
        if target <= 0 or target == int(r["qty"]) and r["status"] != "WAITING_PARENT":
            continue
        if target <= filled:
            continue
        status = "WORKING" if filled == 0 else "PARTIAL"
        conn.execute("UPDATE orders SET qty=?, status=?, updated_ts=? WHERE order_id=?", (target, status, ctx.ts, r["order_id"]))
        conn.execute("INSERT INTO order_events(order_id, ts, from_status, to_status, note) VALUES (?,?,?,?,?)",
                     (r["order_id"], ctx.ts, r["status"], status, f"resized to {target:,} = floor({primary_filled_qty:,} × {ratio}) after the primary's fill"))
        ctx.event("ORDER_RESUMED" if r["status"] == "WAITING_PARENT" else "ORDER_RESIZED", account_id=r["account_id"], instrument_id=r["instrument_id"],
                  ref_type="order", ref_id=r["order_id"], qty=target, primary_filled=int(primary_filled_qty), ratio=str(ratio))
        out.append({"order_id": r["order_id"], "status": status, "qty": target})
    return out
