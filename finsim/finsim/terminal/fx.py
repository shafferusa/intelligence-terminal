"""FX spot deals, their T+2 settlement and currency funding (TERMINAL_DESIGN.md §2.12 fx.py, §5.4, §11.2 SETTLE_DUE,
§13.5, §19.6; WP-05).

Conventions stated here (the §1.3 sentences that bite in this module)
- FX_SPOT: a deal is done at the Yahoo mid plus or minus 0.2 pip AGAINST the user (pip 0.0001; JPY pairs 0.01) and
  settles T+2 US business days.  The pair follows the market convention (``EURUSD`` = USD per EUR; ``USDJPY`` = JPY per
  USD, which Yahoo quotes as ``JPY=X`` with ``yahoo_invert``).  The side rule in the quoted convention:
  * direct pair ``XXXUSD``: buying XXX pays ``mid + 0.2 pip`` USD per XXX, selling XXX receives ``mid - 0.2 pip``;
  * USD-quoted pair ``USDXXX``: buying XXX (selling USD) receives ``mid - 0.2 pip`` XXX per USD, selling XXX pays
    ``mid + 0.2 pip`` XXX per USD.
- The ledger carries ONE rate per foreign currency: USD per 1 unit, at most 8 decimals (``units.qprice``).  The deal
  therefore stores ``rate`` = USD per unit of the foreign currency (for a USD-quoted pair the inverse of the dealt
  rate quantised to 8 decimals; ``rate_quoted`` keeps the market convention) and the USD amount is ALWAYS
  ``fx_c(foreign_c, rate)``.  The two legs are then consistent to the cent by construction and the FX clearing account
  3900 nets to zero in base exactly (integrity L5), also for JPY-style pairs.  When the user types the USD amount the
  foreign amount is ``to_c(USD / rate)`` and the USD leg is recomputed from it: the deal's USD amount can differ from
  the typed amount by one cent, disclosed as ``usd_adjusted_c`` in the preview and the deal.
- ``spread_cost_c`` = USD amount at the deal rate minus the USD amount of the same foreign amount at the mid
  (sentence "0.2 pip equivalent spread cost shown"); it appears inside ``fx_result`` on the deal date (§6.4).
- The deal entry (§5.4): buy leg ``Dr 1250:buy / Cr 3900:buy``, sell leg ``Dr 3900:sell / Cr 2350:sell``; the foreign
  lines carry ``fx_rate`` = the deal rate, so each leg's base is the USD leg's amount.  Two ``settlements`` rows
  (``BUY_CCY`` +, ``SELL_CCY`` −) due on the value date; one ``fx_deals`` row with the whole quote.
- ``fx.spot`` requires the pair to be executable (fresh record, FX_SPOT session open; ``quotes.is_executable``);
  a stale / closed / missing quote is 409 (``QUOTE_STALE`` / ``SESSION_CLOSED`` / ``QUOTE_UNAVAILABLE``).
- Crosses (neither currency USD) are refused ``NOT_IN_THIS_BUILD`` with the instruction to deal twice through USD;
  forwards are Build 2 (``fx.forward`` answers ``NOT_IN_THIS_BUILD``).
- ``fx.settle`` (the SETTLE_DUE job, §11.2) settles both legs of a deal on or after the value date: the receivable leg
  ``Dr 1010 / Cr 1250``; the payable leg ``Dr 2350 / Cr 1010`` after ``execution.fund`` (the account's settled cash in
  that currency, else exactly the shortfall swept from Treasury's cash in that currency, linked to the settlement).
  A payable that cannot be funded stays PENDING with one open Attention ERROR ``SETTLEMENT_UNFUNDED`` and is retried
  on the next tick (the §11.2 row); nothing is invented and the receivable leg still settles.  Settlement lines are
  valued at the command's rate (the FX effect since the deal shows in ``fx_result``).
- ``fund_ccy`` (for WP-26's cross-currency notional exchanges): the account's settled cash in the currency, else
  Treasury's cash in that currency as one linked sweep, else 409 ``INSUFFICIENT_FUNDS_CCY`` with the hint
  "buy <ccy> <amount> with Convert" — never an automatic conversion.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Tuple

from . import tables
from .books import account_kind_for, account_row, lifetime_row
from .clock import accounting_day
from .commands import ConflictError, RejectError, TxContext, require_amount_c
from .db import dumps, next_id
from .execution import FundingResult, auto_resolve_shortfall, fund
from .instruments import fx_pair_id
from .ledger import Entry, balance, cr, dr
from .orders_preview import quote_of
from .quotes import EXECUTABLE_QUALITIES, is_executable
from .units import fmt_money, from_c, fx_c, qprice, to_c

SESSION_GROUP = "FX_SPOT"
SETTLE_DAYS = 2
_FEE = tables.FEES["DEFAULT"]["FX"]
STALE_CODES = {"STALE": "QUOTE_STALE", "NO_QUOTE": "QUOTE_UNAVAILABLE", "SESSION_CLOSED": "SESSION_CLOSED",
               "QUOTE_FROM_PREVIOUS_SESSION": "QUOTE_STALE", "PROVIDER_SESSION_ENDED": "QUOTE_STALE",
               "COMPUTED_NOT_EXECUTABLE": "QUOTE_NOT_EXECUTABLE", "NO_NBBO": "QUOTE_NOT_EXECUTABLE"}


# ---- pure quoting ---------------------------------------------------------------------------------------------------------
def _ccy(x) -> str:
    c = str(x or "").upper()
    if len(c) != 3 or not c.isalpha():
        raise RejectError("CURRENCY", f"currency must be a 3-letter code, got {x!r}")
    return c


def pair_for(buy_ccy: str, sell_ccy: str) -> Tuple[str, str, bool]:
    """``(pair_id, foreign_ccy, usd_quoted)`` for a conversion between USD and one foreign currency.  Crosses are
    refused: they are two deals through USD (disclosed on the ticket)."""
    b, s = _ccy(buy_ccy), _ccy(sell_ccy)
    if b == s:
        raise RejectError("SAME_CURRENCY", "buy and sell currencies are the same")
    if "USD" not in (b, s):
        raise RejectError("NOT_IN_THIS_BUILD", f"{s}->{b} is a cross: convert through USD in two deals (sell {s} for USD, then buy {b})",
                          {"buy_ccy": b, "sell_ccy": s})
    foreign = b if s == "USD" else s
    pair = fx_pair_id(foreign)
    return pair, foreign, pair.startswith("USD")


def pip_of(pair: str) -> Decimal:
    """0.0001, or 0.01 for JPY pairs (sentence FX_SPOT)."""
    return _FEE["pip_jpy"] if "JPY" in pair.upper() else _FEE["pip"]


def deal_rate(mid_quoted: Decimal, pair: str, buy_ccy: str, foreign: str) -> Tuple[Decimal, Decimal]:
    """``(rate_quoted, rate_usd_per_unit)``: the dealt rate in the pair's own convention (mid ∓ 0.2 pip against the
    user) and the same rate as USD per 1 unit of the foreign currency, 8 decimals, which the ledger uses."""
    mid = qprice(mid_quoted)
    if mid <= 0:
        raise ConflictError("QUOTE_UNAVAILABLE", f"{pair}: the mid {mid} is not a usable rate")
    spread = pip_of(pair) * _FEE["spread_pips"]
    buying_foreign = _ccy(buy_ccy) == foreign
    usd_quoted = pair.upper().startswith("USD")
    if usd_quoted:
        quoted = mid - spread if buying_foreign else mid + spread        # XXX per USD: fewer XXX when buying them, more when selling
        rate = qprice(Decimal(1) / quoted)
    else:
        quoted = mid + spread if buying_foreign else mid - spread        # USD per XXX: pay more when buying, receive less when selling
        rate = qprice(quoted)
    return qprice(quoted), rate


def deal_amounts(rate: Decimal, amount_c: int, amount_ccy: str, foreign: str) -> Tuple[int, int, int]:
    """``(foreign_c, usd_c, usd_adjusted_c)``: the USD amount is always ``fx_c(foreign_c, rate)`` (see the module
    docstring); a typed USD amount can therefore move by one cent (``usd_adjusted_c`` = deal USD − typed USD)."""
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


# ---- the quote (shared by the preview and the handler) ----------------------------------------------------------------------
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


def _quoted_mid(view, usd_quoted: bool) -> Tuple[Decimal, str]:
    """The pair's mid in its own convention from the snapshot's ``fx(ccy)`` view (USD per unit, inverted for USD-quoted
    pairs with ``inputs.inverted_from`` carrying the print)."""
    inputs = view.inputs or {}
    if usd_quoted:
        if inputs.get("inverted_from"):
            return qprice(inputs["inverted_from"]), "pair print"
        return qprice(Decimal(1) / qprice(view.mark)), "inverted from the USD-per-unit mark"
    return qprice(view.mark), "pair print"


def quote(services, snapshot, account_id: str, buy_ccy: str, sell_ccy: str, amount_c: int, amount_ccy: str, conn=None, now=None) -> dict:
    """Everything a deal needs from one mark snapshot (pure: no writes).  Raises ``RejectError`` for a bad request;
    quote problems are returned in ``blockers`` (the handler turns them into 409).  ``now`` is the instant the session
    and value date are judged at (the command's ``ctx.ts`` / the clock), not the snapshot's capture time."""
    now = int(now if now is not None else snapshot.as_of())
    pair, foreign, usd_quoted = pair_for(buy_ccy, sell_ccy)
    b, s = _ccy(buy_ccy), _ccy(sell_ccy)
    inst = services.instruments.get(pair, conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"FX pair {pair} is not in the master")
    acct = _account(conn, account_id) if conn is not None else None
    if acct is None:
        with services.db.reader() as rconn:
            acct = _account(rconn, account_id)
    akind = account_kind_for(acct)
    if not services.instruments.tradeable_in(inst, akind):
        raise RejectError("NOT_TRADEABLE_HERE", f"{account_id} ({akind}) may not convert currencies; use the Credit, Foreign Markets & FX, PB or Treasury account",
                          {"account_kind": akind, "pair": pair})
    blockers = []
    view = snapshot.fx(foreign)
    if view is None or view.mark is None or view.quality == "unavailable":
        blockers.append({"code": "QUOTE_UNAVAILABLE", "message": f"no USD rate for {foreign} ({pair}) in the quote cache", "details": {"pair": pair}})
        return {"ok": False, "blockers": blockers, "pair": pair, "foreign_ccy": foreign, "buy_ccy": b, "sell_ccy": s, "quote": view.to_dict() if view else None}
    session = services.sessions.state(SESSION_GROUP, now)
    rec = quote_of(services, pair, snapshot)
    if rec is not None:
        ok, reason = is_executable(rec, session, now)
    else:
        if not session.open:
            ok, reason = False, "SESSION_CLOSED"
        elif view.quality not in EXECUTABLE_QUALITIES or not view.executable:
            ok, reason = False, ("STALE" if view.quality == "stale" else "COMPUTED_NOT_EXECUTABLE")
        else:
            ok, reason = True, "OK"
    mid_quoted, mid_basis = _quoted_mid(view, usd_quoted)
    rate_quoted, rate = deal_rate(mid_quoted, pair, b, foreign)
    foreign_c, usd_c, adjusted = deal_amounts(rate, amount_c, amount_ccy, foreign)
    usd_at_mid = fx_c(foreign_c, mid_usd_per_unit(mid_quoted, usd_quoted))
    spread_cost_c = abs(usd_c - usd_at_mid)
    buy_c = foreign_c if b == foreign else usd_c
    sell_c = usd_c if b == foreign else foreign_c
    trade_date = date.fromisoformat(session.trade_date) if session.trade_date else accounting_day(datetime.fromtimestamp(now, timezone.utc))
    value_date = services.cal.settlement_date(trade_date, inst.settle_days or SETTLE_DAYS)
    if not ok:
        blockers.append({"code": STALE_CODES.get(reason, "QUOTE_STALE"), "message": f"{pair}: {reason.lower().replace('_', ' ')} — the deal needs a fresh quote in the FX session",
                         "details": {"pair": pair, "reason": reason, "quality": view.quality, "quote_time": view.quote_time, "session": session.phase}})
    q = view.to_dict()
    q.update({"pair": pair, "executable": ok, "reason": reason, "mid_quoted": str(mid_quoted), "mid_basis": mid_basis, "session": session.phase})
    return {"ok": not blockers, "blockers": blockers, "account_id": account_id, "pair": pair, "foreign_ccy": foreign, "usd_quoted": usd_quoted,
            "buy_ccy": b, "sell_ccy": s, "buy_c": buy_c, "sell_c": sell_c, "buy": str(from_c(buy_c)), "sell": str(from_c(sell_c)),
            "amount_c": int(amount_c), "amount_ccy": _ccy(amount_ccy), "usd_adjusted_c": adjusted,
            "mid": str(mid_quoted), "rate_quoted": str(rate_quoted), "rate": str(rate), "rate_basis": f"USD per 1 {foreign} (ledger rate, 8 dp)",
            "pip": str(pip_of(pair)), "spread_pips": str(_FEE["spread_pips"]), "spread_cost_c": spread_cost_c, "spread_cost": str(from_c(spread_cost_c)),
            "trade_date": trade_date.isoformat(), "value_date": value_date.isoformat(), "settle_days": inst.settle_days or SETTLE_DAYS,
            "quote": q, "executable": ok, "reason": reason, "convention": tables.CONVENTIONS["FX_SPOT"]}


# ---- preview (§13.5; pure) --------------------------------------------------------------------------------------------------
def spot_preview(services, account_id: str, buy_ccy: str, sell_ccy: str, amount_c: int, amount_ccy: str) -> dict:
    """§13.5: rate = mid ∓ 0.2 pip against the user, amount in either currency, value date T+2, ``spread_cost_c``, and
    both legs' settled / pending effect (``legs``).  Pure: reads one mark snapshot and the ledger; writes nothing."""
    snap = services.marks.snapshot()
    pv = quote(services, snap, account_id, buy_ccy, sell_ccy, amount_c, amount_ccy, now=services.clock.epoch())
    if "buy_c" not in pv:
        return pv
    cash = services.accounting.cash(account_id, snapshot=snap)
    value_date = date.fromisoformat(pv["value_date"])
    legs = {}
    for ccy, amt, gl, sign in ((pv["buy_ccy"], pv["buy_c"], "1250", 1), (pv["sell_ccy"], pv["sell_c"], "2350", -1)):
        cv = cash.get(ccy)
        settled = cv.settled_c if cv else 0
        pending = cv.pending_c if cv else 0
        avail_vd = services.accounting.available(account_id, ccy, value_date, snapshot=snap)
        leg = {"currency": ccy, "amount_c": amt, "amount": str(from_c(amt)), "gl": gl, "effect": "receivable" if sign > 0 else "payable",
               "settled_before_c": settled, "pending_before_c": pending, "pending_after_c": pending + sign * amt,
               "settled_after_value_date_c": settled + sign * amt, "available_at_value_date_before_c": avail_vd,
               "available_at_value_date_after_c": avail_vd + sign * amt}
        if sign < 0:
            short = max(0, amt - avail_vd)
            leg["settlement_shortfall_c"] = short
            if short:
                leg["note"] = (f"at settlement the account lacks {ccy} {fmt_money(short)}: exactly that amount is swept from Treasury's {ccy} cash if it has it,"
                               f" otherwise the leg waits (Attention SETTLEMENT_UNFUNDED) — no automatic conversion")
        legs[ccy] = leg
    pv["legs"] = legs
    return pv


# ---- handlers ------------------------------------------------------------------------------------------------------------------
def _payload_amount(payload: dict) -> Tuple[int, str]:
    amount_c = require_amount_c(payload)
    ccy = payload.get("amount_ccy")
    if not ccy:
        raise RejectError("AMOUNT_CCY", "amount_ccy is required (the currency the amount is typed in)")
    return amount_c, _ccy(ccy)


def h_spot(ctx: TxContext, payload: dict) -> dict:
    """``fx.spot {account_id, buy_ccy, sell_ccy, amount_c | amount, amount_ccy}``: the two-leg entry at the deal rate,
    two settlement rows due on the value date, one ``fx_deals`` row.  409 when the quote is not executable."""
    services = ctx.services
    if services is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    account_id = str(payload.get("account_id") or "")
    if not account_id:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    amount_c, amount_ccy = _payload_amount(payload)
    pv = quote(services, ctx.marks, account_id, payload.get("buy_ccy"), payload.get("sell_ccy"), amount_c, amount_ccy, conn=ctx.conn, now=ctx.ts)
    if pv["blockers"]:
        b = pv["blockers"][0]
        raise ConflictError(b["code"], b["message"], dict(b.get("details") or {}, preview=pv))
    conn = ctx.conn
    buy, sell, buy_c, sell_c = pv["buy_ccy"], pv["sell_ccy"], int(pv["buy_c"]), int(pv["sell_c"])
    rate = qprice(pv["rate"])
    foreign = pv["foreign_ccy"]
    deal_id = next_id(conn, "fx_deal")
    fxr = {"fx_rate": rate}
    lines = [dr(account_id, "1250", buy, buy_c, memo=f"receive {buy} on {pv['value_date']}", **(fxr if buy == foreign else {})),
             cr(account_id, "3900", buy, buy_c, memo="FX clearing", **(fxr if buy == foreign else {})),
             dr(account_id, "3900", sell, sell_c, memo="FX clearing", **(fxr if sell == foreign else {})),
             cr(account_id, "2350", sell, sell_c, memo=f"deliver {sell} on {pv['value_date']}", **(fxr if sell == foreign else {}))]
    sign = "-" if Decimal(pv["rate_quoted"]) < Decimal(pv["mid"]) else "+"
    memo = (f"FX spot {deal_id}: SELL {sell} {fmt_money(sell_c)} / BUY {buy} {fmt_money(buy_c)} @ {pv['rate_quoted']} ({pv['pair']} mid {pv['mid']}"
            f" {sign} {pv['spread_pips']} pip), spread cost USD {fmt_money(pv['spread_cost_c'])}, value {pv['value_date']}")
    entry_id = ctx.post(Entry("FX", memo, lines, f"fx:{deal_id}", ref_type="fx_deal", ref_id=deal_id))
    settlements = []
    for leg, ccy, amt, gl in (("BUY_CCY", buy, buy_c, "1250"), ("SELL_CCY", sell, -sell_c, "2350")):
        sid = next_id(conn, "settlement")
        conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (sid, None, deal_id, account_id, leg, ccy, int(amt), gl, pv["value_date"]))
        settlements.append({"settle_id": sid, "leg": leg, "currency": ccy, "amount_c": int(amt), "gl": gl, "due_date": pv["value_date"], "status": "PENDING"})
    inputs = {k: pv[k] for k in ("pair", "foreign_ccy", "usd_quoted", "rate_quoted", "rate_basis", "pip", "spread_pips", "amount_c", "amount_ccy", "usd_adjusted_c",
                                 "trade_date", "settle_days", "reason")}
    inputs["fx_rate_lines"] = f"{foreign} lines at the deal rate {rate} USD per {foreign}; USD lines const"
    conn.execute("INSERT INTO fx_deals(deal_id, command_id, account_id, kind, buy_ccy, sell_ccy, buy_c, sell_c, rate, mid, spread_cost_c, quote_json, trade_ts,"
                 " trade_date, value_date, status, settled_ts, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',NULL,?,?)",
                 (deal_id, ctx.command_id, account_id, "SPOT", buy, sell, buy_c, sell_c, str(rate), pv["mid"], int(pv["spread_cost_c"]), dumps(pv["quote"]), ctx.ts,
                  pv["trade_date"], pv["value_date"], entry_id, dumps(inputs)))
    ctx.event("FX_DEALT", account_id=account_id, instrument_id=pv["pair"], ref_type="fx_deal", ref_id=deal_id, buy_ccy=buy, sell_ccy=sell, buy_c=buy_c, sell_c=sell_c,
              rate=str(rate), rate_quoted=pv["rate_quoted"], mid=pv["mid"], spread_cost_c=int(pv["spread_cost_c"]), value_date=pv["value_date"])
    deal = {"deal_id": deal_id, "account_id": account_id, "kind": "SPOT", "buy_ccy": buy, "sell_ccy": sell, "buy_c": buy_c, "sell_c": sell_c, "rate": str(rate),
            "rate_quoted": pv["rate_quoted"], "mid": pv["mid"], "pair": pv["pair"], "spread_cost_c": int(pv["spread_cost_c"]), "trade_date": pv["trade_date"],
            "value_date": pv["value_date"], "status": "PENDING", "entry_id": entry_id, "usd_adjusted_c": pv["usd_adjusted_c"]}
    return {"deal": deal, "entry_id": entry_id, "settlements": settlements, "preview": pv, "memo": memo}


def _settlement_rows(conn, deal_id: str):
    return conn.execute("SELECT * FROM settlements WHERE fx_deal_id=? ORDER BY CASE leg WHEN 'BUY_CCY' THEN 0 ELSE 1 END, settle_id", (deal_id,)).fetchall()


def _open_unfunded(conn, settle_id: str):
    return conn.execute("SELECT id FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND ref_type='settlement' AND ref_id=? AND resolved_ts IS NULL", (settle_id,)).fetchone()


def settle_deal(ctx: TxContext, deal_id: str, force: bool = False, services=None) -> dict:
    """Settle the PENDING legs of one deal (idempotent: SETTLED legs are skipped; each leg's idem key is
    ``settle:{settle_id}``).  Returns ``{deal_id, settled: [...], unfunded: [...], skipped, status}``."""
    services = services if services is not None else ctx.services
    conn = ctx.conn
    deal = conn.execute("SELECT * FROM fx_deals WHERE deal_id=?", (deal_id,)).fetchone()
    if deal is None:
        raise RejectError("DEAL_UNKNOWN", f"FX deal {deal_id} does not exist")
    rows = _settlement_rows(conn, deal_id)
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


def h_forward(ctx: TxContext, payload: dict) -> dict:
    raise RejectError("NOT_IN_THIS_BUILD", "FX forwards are Build 2 (covered-interest-parity pricing from FRED rates); spot is available with fx.spot")


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


def register(bus) -> None:
    bus.register("fx.spot", h_spot)
    bus.register("fx.settle", h_settle)
    bus.register("fx.forward", h_forward)


__all__ = ["spot_preview", "quote", "pair_for", "pip_of", "deal_rate", "deal_amounts", "mid_usd_per_unit", "settle_deal", "due_deals", "fund_ccy",
           "h_spot", "h_settle", "h_forward", "register", "SESSION_GROUP", "SETTLE_DAYS"]
