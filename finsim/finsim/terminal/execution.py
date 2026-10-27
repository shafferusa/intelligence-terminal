"""Applying fills, funding, system fills, option-margin moves, hedge resizing (TERMINAL_DESIGN.md §2.12 execution.py,
§5.4, §10.7, §10.8, §11.6, §13.2, §13.3).

Conventions stated here (the §1.3 sentences that bite in this module)
- FILL_PRICE: the price comes from the caller's observed quote (the matcher's decision); nothing here invents one.
- COST_BASIS: cost basis is gross; commissions go to 5000 with the instrument.  FIFO: lots are relieved oldest first
  through ``ledger.relieve_fifo``; the realised amount is the sum of the reliefs' ``realised_c``.
- Settlement: a fill's cash leg is a settlements row (receivable 1200 / payable 2100) due ``cal.settlement_date(trade
  date, settle_days)``; origin NEGOTIATED (private secondaries) settles after ``settle_days`` CALENDAR days; an EXPIRY
  fill at price 0 moves no cash and has no settlement row.  An opening equity short's proceeds (its 1200 receivable)
  go to the lender as the borrow's collateral in the fill's own transaction (``lending.consume_for_short``,
  SHORT_COLLATERAL, hotfix 1.0.3.1), so its row carries only what they leave - nothing: the row is written SETTLED with
  amount 0 and the collateral entry, due T+1 as usual, and SETTLE_DUE never moves those proceeds a second time.
- SWEEP / funding (§10.8, C12): ONE cascade, the one the preview discloses (MONEY-4): own cash -> ONE linked Treasury
  SWEEP_IN of ``min(shortfall, Treasury available)`` (``accounting.treasury_available_c``: Treasury immediate available
  less the book's reserve, BM-01) -> the rest drawn on PB financing within the headroom (USD, a PB account with the
  switch on; ``services.credit.pb_draw_for_funding``) -> a non-USD rest converted from USD when the caller asks
  (``fx_convert=True``: ``services.fx.convert_for`` books a linked USD->ccy spot deal, value date <= the need's settle
  date; its USD side is then funded through this same cascade) -> else refuse with the amounts (``ConflictError
  INSUFFICIENT_FUNDS``) BEFORE anything moves, or the overdraft rule.  Nothing is swept when the whole cannot be met.
- What "own cash" is (one availability rule, shared with ``accounting.buying_power`` and the preview, USX-17):
  a fill's payable settles later -> ``available_c`` at the fill's settle date (settled + receivables due by then −
  payables − reserved, floored at 0); ``immediate=True`` (the posting leaves settled cash now: variation margin,
  collateral, a settlement, lifecycle charges) -> settled cash (1010); ``commitments=True`` (an immediate posting that
  must not spend cash already promised: futures initial margin at a fill, OTC IM at booking, PO-07) ->
  ``accounting.immediate_available_c`` = max(0, min(settled, available at the date)): the shortfall against the
  payables and working orders is swept at booking, never discovered later as 'beyond own cash'.
- The overdraft rule (§11.6): a lifecycle call that cannot be funded (``overdraft=True``) is still posted — after the
  cascade (Treasury, then PB) the rest overdraws the account's 1010 and an ERROR attention ``FUNDING_SHORTFALL`` for
  (account, currency) is opened in the same transaction; L18 accepts the negative balance only while that item is open;
  ``auto_resolve_shortfall`` closes it once 1010 is back at or above zero.
- FEES (C25): ``fee_breakdown(services, acct, inst, side, qty, gross)`` = the schedule's commission (``tables.fee_c``,
  the PB schedule through ``services.prime``) + a foreign listing's venue fee / transfer tax from
  ``services.fx.venue_fee_c(inst, side, qty, gross)`` as its OWN line (own 5000 posting with its memo); the preview and the
  fill read the same function, so the previewed fee is the posted one.  ``fee_for`` keeps its (total, basis) shape.
- OPT_MARGIN (decision s7.6, SR-13): short-option margin is a RESERVED requirement - no cash moves.  After an option
  fill the account's requirement is recomputed through ``services.options.margin_requirement_c`` (WP-03; absent ->
  NOT_IN_THIS_BUILD) and ``margin_state`` is upserted; ``accounting.reserved_c`` subtracts it from available and buying
  power.  An increase that own cash + Treasury + PB headroom cannot cover is refused at a fill (409 INSUFFICIENT_FUNDS);
  in a lifecycle run (``overdraft=True``) it opens ONE ERROR ``FUNDING_SHORTFALL`` (kind option_margin; no forced
  liquidation), resolved when the reservation is covered again.  AON packages recompute once for the package.
- Products: equities / ETFs / ADRs / REITs / preferreds and listed options post here; private shares use the same path
  with origin NEGOTIATED (WP-19); futures and FX dispatch to ``services.futures.apply_fill`` / ``services.fx.apply_fill``
  when those packages exist, else NOT_IN_THIS_BUILD; bonds are Build 2 (NOT_IN_THIS_BUILD).
- Shorts: an opening short consumes the order's borrow (``services.lending.consume_for_short``, which posts that fill's
  102 % collateral: the sale proceeds first, the 2 % excess + fee through ``fund``) and a cover returns the now-unneeded
  quantity (``return_borrow`` reason ``cover``); without the lending package (WP-06) those sides are NOT_IN_THIS_BUILD.
  Nothing is ever cash-settled or invented in their place.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from . import tables, treasury
from .accounting import available_c, immediate_available_c, spendable_c, treasury_available_c, view_settle_date
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
    fx_convert: bool = False                # C12 b / C15: a non-USD need beyond own + Treasury cash converts USD first
    trade_date: Optional[date] = None       # LC-1: a system fill of a past date (option expiry run late) pins trade / business date


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
    source: str = "own"              # own | sweep | pb | fx | overdraft | none, '+'-joined in cascade order ("sweep+pb")
    treasury_available_c: Optional[int] = None
    pb_headroom_c: Optional[int] = None
    fx_c: int = 0                    # the part converted from USD (C12 b)
    fx_conversion: Optional[dict] = None
    usd_funding: Optional[dict] = None
    mode: str = "fill"               # fill | immediate | commitments

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        if isinstance(d.get("usd_funding"), FundingResult):
            d["usd_funding"] = d["usd_funding"].to_dict()
        return d


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


def _foreign_listing(inst) -> bool:
    return inst.kind in EQUITY_KINDS and (str(inst.session_group or "").upper().startswith("FOREIGN") or str(inst.currency or "USD") != "USD")


def venue_fee(services, inst, side: Optional[str], qty: int, gross_c: Optional[int]) -> Tuple[int, str]:
    """C25: a foreign listing's venue fee / transfer tax in the instrument's currency, ``services.fx.venue_fee_c(inst, side,
    qty, gross_c) -> (cents, note)`` (P06's table, labelled 'assumed - verify current rates').  0 for US listings, without a
    side or a gross, or before the FX module provides it."""
    if side is None or gross_c is None or not _foreign_listing(inst):
        return 0, ""
    fx = getattr(services, "fx", None)
    fn = getattr(fx, "venue_fee_c", None) if fx is not None else None
    if fn is None:
        return 0, ""
    got = fn(inst, str(side), int(qty), int(gross_c))
    if isinstance(got, (tuple, list)):
        cents, note = int(got[0] or 0), str(got[1] or "") if len(got) > 1 else ""
    else:
        cents, note = int(got or 0), ""
    return max(0, cents), note


def _usd_rate_of(services, ccy: str) -> Optional[Decimal]:
    """USD per one unit of ``ccy`` from the current mark snapshot (the preview's rate), or None."""
    try:
        from .instruments import fx_pair_id
        v = services.marks.snapshot([fx_pair_id(ccy)]).fx(ccy)
        return Decimal(v.mark) if v is not None and v.mark is not None and Decimal(v.mark) > 0 else None
    except Exception:   # noqa: BLE001 - no rate: the caller says so
        return None


def _commission_in_ccy(services, inst, fee_c: int, basis: str, rate: Optional[Decimal]) -> Tuple[int, str]:
    """P06 (SHEL-L): the schedule's commission is a USD figure (per share, min 1.00); a listing quoted in another currency is
    charged its equivalent in that currency at the USD mid: ``to_c(from_c(usd_fee_c) / rate)`` with ``rate`` = USD per unit
    (the fill's rate; the preview reads the same snapshot rate), e.g. USD 1.00 = GBP 0.79 at 1.2700."""
    ccy = str(getattr(inst, "currency", None) or "USD").upper()       # strategies price a fee on a bare namespace(kind=...)
    if ccy == "USD" or not fee_c or not _foreign_listing(inst):
        return fee_c, basis
    r = rate if rate is not None else _usd_rate_of(services, ccy)
    if r is None or Decimal(r) <= 0:
        return fee_c, f"{basis} (no USD rate for {ccy} yet: shown unconverted; the fill converts it at its rate)"
    from .units import from_c, to_c
    conv = to_c(from_c(int(fee_c)) / Decimal(r))
    return conv, f"{basis}: USD {fmt_money(int(fee_c))} = {ccy} {fmt_money(conv)} at {Decimal(r)} USD per {ccy}"


def fee_breakdown(services, acct, inst, side: Optional[str], qty: int, gross_c: Optional[int] = None, rate: Optional[Decimal] = None) -> Tuple[int, str, List[dict]]:
    """``(total_c, basis, lines)`` of a fill (C25; the preview's ``_fees`` reads it): the commission on the account's
    schedule (§12.1) - converted from USD into a foreign listing's currency (``_commission_in_ccy``; ``rate`` = USD per unit,
    the fill passes its own) - plus a foreign venue fee as its own line ``{label, amount_c, basis}``; each line is posted to
    5000 with its own memo."""
    fee_c, basis = tables.fee_c(inst.kind, int(qty), _schedule_for(services, acct), gross_c=gross_c)
    fee_c, basis = _commission_in_ccy(services, inst, int(fee_c), basis, rate)
    lines: List[dict] = [{"label": "commission", "amount_c": int(fee_c), "basis": basis}]
    total = int(fee_c)
    vc, vnote = venue_fee(services, inst, side, qty, gross_c)
    if vc:
        lines.append({"label": vnote or "venue fee", "amount_c": vc, "basis": vnote or "venue fee (assumed)"})
        total += vc
        basis = f"{basis} + {vnote or 'venue fee'} {fmt_money(vc)}"
    return total, basis, lines


def fee_for(services, acct, inst, qty: int, gross_c: Optional[int] = None, side: Optional[str] = None):
    """``(fee_c, fee_basis)`` for a fill of ``qty`` on the account's schedule (§12.1), incl. a foreign venue fee when the
    side is given (``fee_breakdown``)."""
    total, basis, _lines = fee_breakdown(services, acct, inst, side, qty, gross_c)
    return total, basis


def _open_account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _venue_trade_date(inst, quote) -> Optional[date]:
    """A FOREIGN listing's trade date = its venue's local date of the session open (``sessions.venue_date`` of the quote's
    provider session, exact with the venue's zone); None for every other instrument or without a provider session."""
    if not str(getattr(inst, "session_group", "") or "").upper().startswith("FOREIGN"):
        return None
    ps = getattr(quote, "provider_session", None)
    if not ps:
        return None
    try:
        from .sessions import venue_date
        return venue_date(int(ps[0]), getattr(quote, "exchange_tz", None))
    except Exception:   # noqa: BLE001 - an unreadable session: the New York date as before
        return None


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
def own_funds_c(conn, account_id: str, currency: str, settle_date: Optional[date], snapshot, master, now, *, immediate: bool = False,
                commitments: bool = False) -> int:
    """The account's own cash a need is measured against (module docstring 'What "own cash" is'): ``commitments`` ->
    ``immediate_available_c`` (never above settled, net of payables / working orders); ``immediate`` -> settled cash
    (1010, may be negative while a shortfall is open); else ``available_c`` at ``settle_date`` (floored)."""
    if commitments:
        return immediate_available_c(conn, account_id, currency, settle_date, snapshot, master, now)
    if immediate:
        return balance(conn, account_id, "1010", currency)
    return available_c(conn, account_id, currency, settle_date, snapshot, master, now)


def _pb_room(ctx: TxContext, services, acct, currency: str) -> int:
    """PB headroom a funding need may draw (USD only; a PB account with ``pb_financing`` on and the credit hook bound)."""
    credit = getattr(services, "credit", None)
    if currency != "USD" or acct["kind"] != "PB" or not int(acct["pb_financing"] or 0) or credit is None or not hasattr(credit, "pb_draw_for_funding"):
        return 0
    acc = getattr(services, "accounting", None)
    if acc is not None and hasattr(acc, "pb_headroom_c"):
        return max(0, int(acc.pb_headroom_c(ctx.conn, dict(acct), ctx.marks)))
    if hasattr(credit, "headroom_c"):
        return max(0, int(credit.headroom_c(ctx, services, acct)))
    return 0


def _conversion_usd_c(conv: dict) -> Optional[int]:
    for k in ("usd_c", "usd_amount_c", "sell_c", "sell_amount_c"):
        v = conv.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            return int(v)
    return None


def fund(ctx: TxContext, account_id: str, currency: str, need_c: int, link_type: str, link_id: str, settle_date: Optional[date] = None, *,
         immediate: bool = False, overdraft: bool = False, services=None, fx_convert: bool = False, commitments: bool = False) -> FundingResult:
    """Make ``need_c`` of ``currency`` spendable in ``account_id`` (sentence SWEEP; §10.8; contract C12).

    The cascade (MONEY-4: exactly what the preview disclosed): own cash (``own_funds_c`` by mode) -> ONE linked SWEEP_IN
    of ``min(shortfall, Treasury available)`` -> the rest drawn on PB financing within the headroom -> with
    ``fx_convert`` a non-USD rest converted from USD (``services.fx.convert_for``; its USD side funded by this cascade,
    measured against the USD own cash BEFORE the deal) -> else 409 INSUFFICIENT_FUNDS before anything moved (a refusal
    never leaves a partial sweep), or with ``overdraft`` (lifecycle calls, §11.6) the rest is posted as an overdraft
    under an open FUNDING_SHORTFALL.  Worked (s9): own 0.00, need 250,001.50, Treasury 100,000.00, PB headroom
    169,750.00 -> SWEEP_IN 100,000.00 + PB draw 150,001.50 (source "sweep+pb")."""
    need_c = int(need_c)
    if need_c <= 0:
        return FundingResult(0, 0, 0, funded=True, source="none")
    services = _services(ctx, services)
    conn = ctx.conn
    acct = _open_account(conn, account_id)
    master = getattr(services, "instruments", None)
    mode = "commitments" if commitments else ("immediate" if immediate else "fill")
    own = own_funds_c(conn, account_id, currency, settle_date, ctx.marks, master, ctx.now, immediate=immediate, commitments=commitments)
    shortfall = need_c - own
    if shortfall <= 0:
        return FundingResult(need_c, own, 0, funded=True, source="own", mode=mode)
    res = FundingResult(need_c, own, shortfall, mode=mode)
    lid = acct["lifetime_id"]
    is_treasury = acct["kind"] == "TREASURY"
    t_avail = 0 if is_treasury else treasury_available_c(conn, lid, currency, ctx.marks, master, ctx.now)
    res.treasury_available_c = t_avail
    from_tre = min(shortfall, max(0, t_avail))
    rest = shortfall - from_tre
    pb_room = _pb_room(ctx, services, acct, currency) if rest > 0 else 0
    res.pb_headroom_c = pb_room if rest > 0 else None
    from_pb = min(rest, pb_room)
    rest -= from_pb
    fxm = getattr(services, "fx", None)
    convert = rest > 0 and fx_convert and currency != "USD" and fxm is not None and hasattr(fxm, "convert_for")
    details = {"need_c": need_c, "own_available_c": own, "treasury_available_c": t_avail, "pb_headroom_c": pb_room, "pb_draw_c": from_pb,
               "shortfall_c": rest, "currency": currency, "account_id": account_id, "link_type": link_type, "link_id": link_id, "mode": mode}
    if rest > 0 and not convert and not overdraft:
        hint = ""
        if currency != "USD" and fx_convert:
            hint = "; the automatic USD conversion is not available yet"
        elif currency != "USD":
            hint = f"; buy {currency} {fmt_money(rest)} with Convert, or ask for the automatic conversion"
        raise ConflictError("INSUFFICIENT_FUNDS",
                            f"{account_id} needs {currency} {fmt_money(need_c)}: own available {fmt_money(own)}, Treasury available "
                            f"{fmt_money(max(0, t_avail))}, PB financing {fmt_money(pb_room)}{hint}", details)
    sources = []
    if from_tre > 0:
        # the sweep moves exactly the part of the shortfall Treasury covers (L19: amount == recorded shortfall; PB / FX / an
        # overdraft carry the rest, each with its own linked line)
        res.sweep_id = treasury.sweep_in(ctx, account_id, currency, from_tre, link_type, link_id, from_tre)
        res.sweep_c = from_tre
        sources.append("sweep")
    if from_pb > 0:
        drawn = int(services.credit.pb_draw_for_funding(ctx, services, account_id, from_pb, link_type, link_id) or 0)
        if drawn != from_pb:          # the hook draws min(amount, headroom): the headroom read above is the one it uses
            raise ConflictError("PB_HEADROOM", f"{account_id}: PB financing drew {fmt_money(drawn)} of {fmt_money(from_pb)}", dict(details, drawn_c=drawn))
        res.pb_draw_c = drawn
        sources.append("pb")
    if convert:
        usd_own = own_funds_c(conn, account_id, "USD", settle_date, ctx.marks, master, ctx.now)
        conv = fxm.convert_for(ctx, account_id, currency, rest, link_type, link_id, settle_date, services=services)
        conv = dict(conv or {})
        res.fx_conversion, res.fx_c = conv, rest
        sources.append("fx")
        usd_c = _conversion_usd_c(conv)
        already = conv.get("usd_funded") or conv.get("usd_funding") or conv.get("funding")
        if usd_c is not None and usd_c > 0 and not already:
            # the USD side: measured against the USD own cash BEFORE the deal's USD payable, so it is counted once
            uf = _cascade_usd(ctx, services, acct, usd_c, usd_own, "fx_deal", str(conv.get("deal_id") or link_id), overdraft=False)
            res.usd_funding = uf.to_dict()
        rest = 0
    if rest > 0:                       # overdraft rule (§11.6): Treasury and PB took what they could, the rest overdraws 1010
        res.funded, res.unfunded_c = False, rest
        sources.append("overdraft")
        ctx.attention("ERROR", "execution", "FUNDING_SHORTFALL",
                      f"{account_id}: {currency} {fmt_money(rest)} for {link_type} {link_id} could not be funded (own available {fmt_money(own)},"
                      f" Treasury swept {fmt_money(from_tre)}, PB financing {fmt_money(from_pb)}); the obligation is posted and settled cash is overdrawn",
                      dict(details, amount_c=rest, ref_type=link_type, ref_id=link_id, auto_resolve=1, swept_c=from_tre))
        ctx.event("ATTENTION", account_id=account_id, ref_type=link_type, ref_id=link_id, code="FUNDING_SHORTFALL", amount_c=rest, currency=currency)
    else:
        auto_resolve_shortfall(ctx, account_id, currency)
    res.source = "+".join(sources) or "own"
    return res


def _cascade_usd(ctx: TxContext, services, acct, need_c: int, own: int, link_type: str, link_id: str, overdraft: bool = False) -> FundingResult:
    """The USD side of a conversion (C12 b): the same cascade with a precomputed own figure (taken before the deal posted
    its USD payable, so the need is counted once)."""
    account_id = acct["account_id"]
    res = FundingResult(int(need_c), int(own), max(0, int(need_c) - int(own)), mode="fill")
    if res.shortfall_c <= 0:
        res.source = "own"
        return res
    master = getattr(services, "instruments", None)
    t_avail = treasury_available_c(ctx.conn, acct["lifetime_id"], "USD", ctx.marks, master, ctx.now)
    res.treasury_available_c = t_avail
    from_tre = min(res.shortfall_c, max(0, t_avail))
    rest = res.shortfall_c - from_tre
    pb_room = _pb_room(ctx, services, acct, "USD") if rest > 0 else 0
    from_pb = min(rest, pb_room)
    rest -= from_pb
    if rest > 0 and not overdraft:
        raise ConflictError("INSUFFICIENT_FUNDS", f"{account_id} needs USD {fmt_money(need_c)} for the conversion: own available {fmt_money(own)}, Treasury available "
                            f"{fmt_money(max(0, t_avail))}, PB financing {fmt_money(pb_room)}",
                            {"need_c": int(need_c), "own_available_c": int(own), "treasury_available_c": t_avail, "pb_headroom_c": pb_room, "shortfall_c": rest,
                             "currency": "USD", "account_id": account_id, "link_type": link_type, "link_id": link_id})
    sources = []
    if from_tre > 0:
        res.sweep_id = treasury.sweep_in(ctx, account_id, "USD", from_tre, link_type, link_id, from_tre)
        res.sweep_c = from_tre
        sources.append("sweep")
    if from_pb > 0:
        res.pb_draw_c = int(services.credit.pb_draw_for_funding(ctx, services, account_id, from_pb, link_type, link_id) or 0)
        sources.append("pb")
    res.source = "+".join(sources) or "own"
    return res


def auto_resolve_shortfall(ctx: TxContext, account_id: str, currency: str) -> int:
    """Resolve the open FUNDING_SHORTFALL items of (account, currency) once settled cash is back at or above zero; an
    option-margin item (kind ``option_margin``, SR-13) resolves when the reservation is covered again
    (``option_margin_cover``).  Returns the number of items resolved (WP-08's housekeeping calls this after transfers and
    settlements; the Treasury transfer / deposit handlers call it for the lifetime's accounts)."""
    rows = ctx.conn.execute("SELECT id, details_json FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL", (account_id,)).fetchall()
    if not rows:
        return 0
    cash_ok = balance(ctx.conn, account_id, "1010", currency) >= 0
    margin_ok: Optional[bool] = None
    n = 0
    for r in rows:
        try:
            d = json.loads(r["details_json"] or "{}")
        except ValueError:
            d = {}
        ccy = str(d.get("currency") or "USD")
        if ccy != currency:
            continue
        if d.get("kind") == "option_margin":
            if margin_ok is None:
                margin_ok = option_margin_cover(ctx, ctx.services, account_id)[0] <= 0
            ok = margin_ok
        else:
            ok = cash_ok
        if ok:
            ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE id=?", (ctx.ts, int(r["id"])))
            n += 1
    return n


def option_margin_cover(ctx: TxContext, services, account_id: str) -> Tuple[int, dict]:
    """``(shortfall_c, parts)`` of the account's option margin RESERVATION (SR-13): what own cash (the signed spendable at the
    view's settlement date, the reservation included) + Treasury available + PB headroom leave uncovered; 0 when covered."""
    conn = ctx.conn
    acct = account_row(conn, account_id)
    if acct is None:
        return 0, {}
    master = getattr(services, "instruments", None) if services is not None else None
    d = view_settle_date(ctx.cal, ctx.now)
    spend = spendable_c(conn, account_id, "USD", d, ctx.marks, master, ctx.now)
    tre = 0 if acct["kind"] == "TREASURY" else treasury_available_c(conn, acct["lifetime_id"], "USD", ctx.marks, master, ctx.now)
    pb = _pb_room(ctx, services, acct, "USD") if services is not None else 0
    total = spend + max(0, tre) + max(0, pb)
    return max(0, -total), {"spendable_c": spend, "treasury_available_c": tre, "pb_headroom_c": pb, "settle_date": d.isoformat()}


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
    ccy = inst.currency
    rate = _fx_rate(ctx, ccy)
    if spec.fee_override_c is not None:
        fee_c, fee_basis = int(spec.fee_override_c), "override (system fill / negotiated)"
        fee_lines = [{"label": "commission", "amount_c": fee_c, "basis": fee_basis}]
    else:
        fee_c, fee_basis, fee_lines = fee_breakdown(services, acct, inst, side, qty, gross_c, rate=rate)
    # LC-1: a system fill processed after its date (an option expiry run on Monday) keeps the date it belongs to; a FOREIGN
    # listing trades on its venue's local date (P06 / EXEC-8: Tokyo / HK / Sydney fills in the ET evening are the next day there)
    tdate = spec.trade_date or _venue_trade_date(inst, spec.quote) or _trade_date(ctx.now, inst.session_group, ctx.cal)
    business_date = spec.trade_date.isoformat() if spec.trade_date is not None else None
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

    def fee_postings():
        # C25: one 5000 line per fee line (the commission; a foreign venue fee / transfer tax with its own memo)
        return [dr(spec.account_id, "5000", ccy, int(fl["amount_c"]), instrument_id=inst.instrument_id, memo=str(fl.get("basis") or fl.get("label") or fee_basis))
                for fl in fee_lines if int(fl.get("amount_c") or 0)]

    if opening and effect == "BUY":                     # BUY / BTO
        funding = fund(ctx, spec.account_id, ccy, gross_c + fee_c, "fill", fill_id, settle_date, services=services, fx_convert=bool(spec.fx_convert))
        lines.append(dr(spec.account_id, long_gl, ccy, gross_c, instrument_id=inst.instrument_id, memo="gross cost"))
        lines.extend(fee_postings())
        lines.append(cr(spec.account_id, "2100", ccy, gross_c + fee_c, memo="payable at settlement"))
        settle_amount, settle_gl = -(gross_c + fee_c), "2100"
    elif (not opening) and effect == "SELL":            # SELL / STC
        rl = relieve_fifo(ctx.tx, spec.account_id, inst.instrument_id, "LONG", qty, fill_id, gross_c)
        cost_relieved = sum(r.cost_relieved_c for r in rl)
        realised = sum(r.realised_c for r in rl)
        reliefs = [r.__dict__ for r in rl]
        if gross_c - fee_c:
            lines.append(dr(spec.account_id, "1200", ccy, gross_c - fee_c, memo="receivable at settlement"))
        lines.extend(fee_postings())
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
        lines.extend(fee_postings())
        lines.append(cr(spec.account_id, short_gl, ccy, gross_c, instrument_id=inst.instrument_id, memo="short proceeds"))
        settle_amount, settle_gl = gross_c - fee_c, "1200"
    else:                                               # BUY_TO_COVER / BTC
        if short_gl is None:
            raise RejectError("CLOSE_ONLY_MISMATCH", f"{inst.kind} has no short lots to cover")
        rl = relieve_fifo(ctx.tx, spec.account_id, inst.instrument_id, "SHORT", qty, fill_id, gross_c)
        cost_relieved = sum(r.cost_relieved_c for r in rl)
        realised = sum(r.realised_c for r in rl)
        reliefs = [r.__dict__ for r in rl]
        funding = fund(ctx, spec.account_id, ccy, gross_c + fee_c, "fill", fill_id, settle_date, services=services, fx_convert=bool(spec.fx_convert))
        if cost_relieved:
            lines.append(dr(spec.account_id, short_gl, ccy, cost_relieved, instrument_id=inst.instrument_id, memo="short proceeds relieved FIFO"))
        lines.extend(fee_postings())
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
    entry_id = ctx.post(Entry(entry_kind, memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id, business_date=business_date))
    conn.execute(
        "INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
        " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, spec.order_id, spec.account_id, inst.instrument_id, effect, qty, str(price), gross_c, fee_c, ccy, str(rate), ctx.ts, tdate.isoformat(),
         settle_date.isoformat() if settle_date else None, spec.origin, spec.price_basis, _quote_json(spec, ctx, ccy, rate), spec.bar_ts, spec.cap_qty,
         spec.cap_note, entry_id, spec.idem_key))
    if opening:
        lot_id = open_lot(ctx.tx, spec.account_id, inst.instrument_id, lot_side, fill_id, qty, price, gross_c, ccy, lock_until=spec.lock_until)
    applied_c = 0
    if inst.kind in EQUITY_KINDS and opening and effect == "SELL":
        # an opening short consumes its borrow BEFORE the settlement row: an order borrow's 102 % collateral is posted now,
        # funded first by this sale's own proceeds (the 1200 receivable above), so the row carries only what they leave
        # (SHORT_COLLATERAL, hotfix 1.0.3.1)
        applied_c = int(services.lending.consume_for_short(ctx, spec.borrow_id, qty, fill_id, price=price, proceeds_c=gross_c - fee_c, services=services) or 0)
        settle_amount -= applied_c
    settle_id = None
    if settle_date is not None and settle_amount:
        settle_id = next_id(conn, "settlement")
        conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (settle_id, fill_id, None, spec.account_id, "CASH", ccy, settle_amount, settle_gl, settle_date.isoformat()))
    elif settle_date is not None and applied_c:
        # the proceeds were delivered to the lender in this transaction: the cash leg is recorded settled at 0 by the
        # collateral entry (``collat:{borrow}:fill:{fill_id}``), so T+1 moves nothing a second time (L10: one row per fill)
        settle_id = next_id(conn, "settlement")
        coll = conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key=?", (f"collat:{spec.borrow_id}:fill:{fill_id}",)).fetchone()
        conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status, settled_ts, entry_id)"
                     " VALUES (?,?,?,?,?,?,0,?,?,'SETTLED',?,?)", (settle_id, fill_id, None, spec.account_id, "CASH", ccy, settle_gl, settle_date.isoformat(), ctx.ts,
                                                                   int(coll["entry_id"]) if coll is not None else entry_id))
    ctx.touched_instruments.add((spec.account_id, inst.instrument_id))
    ctx.touched_accounts.add(spec.account_id)
    # covers: return the borrowed quantity that now backs nothing (WP-06; an opening short consumed its borrow above)
    if inst.kind in EQUITY_KINDS:
        lending = getattr(services, "lending", None)
        if (not opening) and effect == "BUY" and lending is not None and hasattr(lending, "return_for_cover"):
            lending.return_for_cover(ctx, services, spec.account_id, inst.instrument_id, qty, fill_id)
    margin = None
    if inst.kind == "OPTION" and not spec.defer_margin:
        margin = recompute_option_margin(ctx, services, spec.account_id, f"fill:{fill_id}")
    new_qty, _ = position(conn, spec.account_id, inst.instrument_id)
    return FillResult(fill_id, entry_id, spec.account_id, inst.instrument_id, side, effect, qty, price, gross_c, fee_c, fee_basis, ccy, rate, tdate.isoformat(),
                      settle_date.isoformat() if settle_date else None, settle_id, lot_id, reliefs, cost_relieved, realised, funding, new_qty, memo, margin,
                      spec.price_basis)


def apply_system_fill(ctx: TxContext, account_id: str, inst, side: str, qty: int, price, origin: str, quote, idem_key: str, note: str, services=None,
                      settle_days: Optional[int] = None, lock_until: Optional[str] = None, fee_c: int = 0, trade_date: Optional[date] = None) -> FillResult:
    """A fill that no order produced (exercise / assignment / expiry / futures final / maturity / negotiated): commission-free
    unless ``fee_c`` says otherwise, price basis from the origin, ``note`` in the memo.  ``side`` is an order side
    (SELL / STC closes a long, BUY_TO_COVER / BTC closes a short, BUY / BTO opens a long)."""
    basis = PRICE_BASIS_OF_ORIGIN.get(origin, "system")
    spec = FillSpec(None, account_id, getattr(inst, "instrument_id", inst), side, int(qty), qprice(price), origin, quote, idem_key, None, basis,
                    fee_override_c=int(fee_c), settle_days=settle_days, lock_until=lock_until, note=note, trade_date=trade_date)
    return apply_fill(ctx, spec, services)


# ---- option margin (§10.7 step 6, §13.3) ----------------------------------------------------------------------------------
OPT_MARGIN_NOTE = ("short-option margin is a RESERVED requirement (decision s7.6): it reduces available cash and buying power; no cash moves "
                   "(Reg T-style approximation, OPT_MARGIN)")


def recompute_option_margin(ctx: TxContext, services, account_id: str, link_id: Optional[str] = None, overdraft: bool = False) -> dict:
    """Recompute the account's listed-option margin requirement (``services.options.margin_requirement_c``) and RESERVE it
    (SR-13, decision s7.6): ``margin_state`` is upserted (L16), nothing is posted - ``accounting.reserved_c`` subtracts the
    requirement from available and buying power.  An increase that own cash + Treasury + PB headroom cannot cover
    (``option_margin_cover``) is refused (409 INSUFFICIENT_FUNDS) unless ``overdraft`` (a lifecycle run), which opens ONE
    ERROR FUNDING_SHORTFALL (kind option_margin; no forced liquidation) instead; a covered reservation resolves it.
    Absent WP-03 -> NOT_IN_THIS_BUILD.  Worked (§19.7): STO 5 P740 -> requirement 67,172.00 reserved, 1010 unchanged."""
    opt = getattr(services, "options", None)
    if opt is None or not hasattr(opt, "margin_requirement_c"):
        raise RejectError("NOT_IN_THIS_BUILD", "listed options: the margin module is not yet built (WP-03)")
    positions = services.accounting.positions(account_id, snapshot=ctx.marks)
    required_c, detail = opt.margin_requirement_c(positions, ctx.marks)
    required_c = int(required_c)
    conn = ctx.conn
    row = conn.execute("SELECT options_required_c FROM margin_state WHERE account_id=?", (account_id,)).fetchone()
    previous = int(row["options_required_c"]) if row is not None else 0
    delta = required_c - previous
    idem = f"omargin:{account_id}:{ctx.ts}:{ctx.command_id}"
    conn.execute("INSERT INTO margin_state(account_id, as_of_ts, options_required_c, options_detail_json, note) VALUES (?,?,?,?,?)"
                 " ON CONFLICT(account_id) DO UPDATE SET as_of_ts=excluded.as_of_ts, options_required_c=excluded.options_required_c,"
                 " options_detail_json=excluded.options_detail_json, note=excluded.note",
                 (account_id, ctx.ts, required_c, dumps(detail), OPT_MARGIN_NOTE))
    ctx.touched_accounts.add(account_id)
    shortfall, parts = option_margin_cover(ctx, services, account_id) if required_c > 0 else (0, {})
    open_item = conn.execute("SELECT id FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL AND details_json LIKE ?",
                             (account_id, '%"kind":"option_margin"%')).fetchone()
    if shortfall > 0 and delta > 0 and not overdraft:
        raise ConflictError("INSUFFICIENT_FUNDS",
                            f"{account_id}: the option margin requirement rises to USD {fmt_money(required_c)} (+{fmt_money(delta)}); own cash, Treasury and PB financing "
                            f"leave {fmt_money(shortfall)} of it uncovered (it is reserved, never moved)",
                            dict(parts, need_c=delta, required_c=required_c, shortfall_c=shortfall, currency="USD", account_id=account_id, link_type="margin",
                                 link_id=link_id or idem))
    if shortfall > 0:
        message = (f"{account_id}: the option margin requirement USD {fmt_money(required_c)} exceeds own cash, Treasury and PB financing by "
                   f"{fmt_money(shortfall)}; new orders are held - add funds or reduce the written options (no forced liquidation)")
        details = dict(parts, kind="option_margin", currency="USD", amount_c=shortfall, required_c=required_c, account_id=account_id, ref_type="margin",
                       ref_id=account_id, auto_resolve=1)
        if open_item is None:
            ctx.attention("ERROR", "execution", "FUNDING_SHORTFALL", message, details)
            ctx.event("ATTENTION", account_id=account_id, ref_type="margin", ref_id=account_id, code="FUNDING_SHORTFALL", amount_c=shortfall, currency="USD")
        else:
            conn.execute("UPDATE attention SET ts=?, message=?, details_json=? WHERE id=?", (ctx.ts, message, dumps(details), int(open_item["id"])))
    elif open_item is not None:
        conn.execute("UPDATE attention SET resolved_ts=? WHERE id=?", (ctx.ts, int(open_item["id"])))
    if delta:
        ctx.event("MARGIN_RESERVED", account_id=account_id, ref_type="margin", ref_id=idem, required_c=required_c, delta_c=delta, link=link_id)
    return {"required_c": required_c, "previous_c": previous, "delta_c": delta, "entry_id": None, "idem_key": idem, "detail": detail, "funding": None,
            "reserved": True, "shortfall_c": shortfall, "cover": parts}


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
