"""Treasury handlers and sweeps (TERMINAL_DESIGN.md §2.12 treasury.py, §5.4, §10.8, §13.10).

Handlers: ``treasury.init_capital`` {lifetime_id | account_id, amount_c, currency, source} (idempotent: skipped when
the lifetime's capital entry exists), ``treasury.deposit`` (any currency; never P&L), ``treasury.withdraw`` (refused
unless Treasury's settled cash in that currency covers it), ``treasury.transfer`` (internal, one entry four lines
through 3100; refused across lifetimes with CROSS_BOOK), ``account.update_settings`` (light).
``sweep_in()`` / ``sweep_out()`` are the funding primitives used by ``execution.fund`` and the SWEEP_OUT job.

Conventions (sentences TREASURY, SWEEP)
- Initial capital / deposit: T: Dr 1010 / Cr 3000 (idem ``capital:initial:{lifetime}`` / ``deposit:{request_id}``).
- Withdrawal: T: Dr 3000 / Cr 1010 (idem ``withdraw:{request_id}``).
- Transfer T -> A (manual or SWEEP_IN): T: Dr 3100 / Cr 1010 ; A: Dr 1010 / Cr 3100 (idem ``xfer:{transfer_id}``);
  A -> T mirrored.  3100 nets to zero across the lifetime (L5).
- A sweep-in moves exactly the shortfall, linked to what it paid for (L19); ``execution.fund`` sweeps
  ``min(shortfall, Treasury available)`` and draws the rest on PB financing (C12 a), so the Treasury figure it reads,
  ``accounting.treasury_available_c`` (Treasury immediate available - the reserve of the lifetime's book, BM-01), is
  the one ``sweep_in`` checks and the preview shows.
- An internal transfer moves SETTLED cash at once: it is checked against ``accounting.immediate_available_c`` (settled
  cash net of payables and working-order reservations, receivables due today counted; never above settled) and refused
  409 INSUFFICIENT_FUNDS naming the settled amount and what settles later (MONEY-6) - the L18 integrity check is never the
  refusal path.
- A foreign-currency deposit / withdrawal / transfer needs that currency's USD rate in the mark snapshot (every line
  carries its base): without it the command is refused 409 FX_RATE_UNAVAILABLE "no EUR/USD rate yet; try again when FX
  rates load" before anything is posted (BM-20), never a 500.
- Typed amounts: a decimal string in ``amount`` is read STRICTLY (``units.parse_money``: at most 2 decimals, no
  exponent, sign or separators, a typo guard above 1,000,000,000,000.00; BM-21); ``amount_c`` (integer cents) as before.
- AS-OF DATING (FINSIM1_PLAN C15 / D7 / D8, POSTCLOSE-06): when the command context carries ``ctx.asof_date`` = D (an
  as-of booking of the historical import, an as-of settlement, a lifecycle job of business date D that runs after the
  17:00 ET accounting-day cut-off), every transfer row this module writes carries ``transfers.business_date`` = D and its
  journal entry ``business_date`` = D (``business_date(ctx)``); without it, the accounting day as before.
- AS-OF SWEEP-IN (C15 / C20): under ``ctx.asof_date`` = D, ``sweep_in`` measures Treasury on the business-date figure
  ``asof_sweepable_c`` = Treasury's settled cash at the close of D (journal lines with business_date <= D;
  ``accounting.cash_view_at`` when present) less the book's reserve - never today's ``sweepable_c``.  The current-balance
  floor stays with ``execution.fund`` (409 ASOF_CURRENT_FUNDS) and the L18 check at commit.  Worked (P6 test j): Treasury
  900,000.00 at the close of D and 5,000,000.00 now -> a 950,000.00 as-of sweep is refused 409 INSUFFICIENT_FUNDS;
  25,000,000.00 at the close of D and 2,000,000.00 now -> a 1,701,918.50 as-of sweep posts, dated D.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .books import account_row, lifetime_row, split_account_id, treasury_reserve_c
from .commands import ConflictError, RejectError, TxContext, require_amount_c
from .db import dumps, next_id
from .ledger import Entry, LedgerError, cr, dr
from .units import fmt_money, parse_money

_CCY_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _ccy(payload: dict) -> str:
    c = str(payload.get("currency") or "USD").upper()
    if len(c) != 3 or any(ch not in _CCY_OK for ch in c):
        raise RejectError("CURRENCY", f"currency must be a 3-letter code, got {c!r}")
    return c


def _treasury_account(ctx: TxContext, payload: dict):
    aid = payload.get("account_id")
    if not aid and payload.get("lifetime_id"):
        aid = f"{payload['lifetime_id']}:TREASURY"
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id or lifetime_id is required")
    row = account_row(ctx.conn, aid)
    if row is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
    if row["kind"] != "TREASURY":
        raise RejectError("NOT_TREASURY", f"{aid} is not a Treasury account: capital enters only through Treasury")
    _open_account(ctx, row)
    return row


def _open_account(ctx: TxContext, row) -> None:
    lrow = lifetime_row(ctx.conn, row["lifetime_id"])
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {row['lifetime_id']} is closed (History is read-only)")
    if row["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {row['account_id']} is closed")


def _settled(ctx: TxContext, account_id: str, ccy: str) -> int:
    from .ledger import balance
    return balance(ctx.conn, account_id, "1010", ccy)


def _available(ctx: TxContext, account_id: str, ccy: str) -> int:
    from .accounting import available_c
    return available_c(ctx.conn, account_id, ccy, None, ctx.marks, getattr(ctx.services, "instruments", None) if ctx.services else None, ctx.now)


def _master(ctx: TxContext):
    return getattr(ctx.services, "instruments", None) if ctx.services else None


def sweepable_c(ctx: TxContext, lifetime_id: str, ccy: str) -> int:
    """What the lifetime's Treasury can sweep now in ``ccy`` (signed; C12 a, BM-01): ``accounting.treasury_available_c``."""
    from .accounting import treasury_available_c
    return treasury_available_c(ctx.conn, lifetime_id, ccy, ctx.marks, _master(ctx), ctx.now)


# ---- as-of dating (C15) -------------------------------------------------------------------------------------------------
def asof_date(ctx) -> Optional[str]:
    """C15: the business date an as-of command posts on (``ctx.asof_date``: a date or an ISO string), else None."""
    d = getattr(ctx, "asof_date", None)
    if d is None or d == "":
        return None
    return d.isoformat() if isinstance(d, date) else str(d)[:10]


def business_date(ctx) -> str:
    """The business date of the rows this module writes: ``ctx.asof_date`` when set (C15), else the accounting day."""
    return asof_date(ctx) or ctx.today


def settled_at_close_c(conn, account_id: str, ccy: str, d) -> int:
    """Settled cash (1010, natural cents) of ``account_id`` at the close of business date ``d``: the journal lines whose entry
    has ``business_date <= d`` (C9h ``accounting.cash_view_at(...)["settled_c"]`` when that hook is in the build, else the same
    sum read here)."""
    ds = d.isoformat() if isinstance(d, date) else str(d)[:10]
    from . import accounting as _acc
    fn = getattr(_acc, "cash_view_at", None)
    if callable(fn):
        v = fn(conn, account_id, ccy, date.fromisoformat(ds))
        got = v.get("settled_c") if isinstance(v, dict) else getattr(v, "settled_c", None)
        if got is not None:
            return int(got)
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE l.account_id=? AND l.gl='1010' AND l.currency=? AND e.business_date<=?", (account_id, ccy, ds)).fetchone()
    return int(row[0] or 0)


def asof_sweepable_c(ctx_or_conn, lifetime_id: str, ccy: str, d) -> int:
    """C15 / C20: what the lifetime's Treasury could sweep AS OF business date ``d`` (signed): its settled cash at the close
    of ``d`` less the book's reserve.  ``execution.fund`` (under ``ctx.asof_date``) sizes the sweep on this figure and
    ``sweep_in`` checks the same one, so the previewed as-of sweep is the one that posts."""
    conn = getattr(ctx_or_conn, "conn", ctx_or_conn)
    return settled_at_close_c(conn, f"{lifetime_id}:TREASURY", ccy, d) - treasury_reserve_c(conn, lifetime_id, ccy)


def require_rate(ctx: TxContext, ccy: str) -> None:
    """BM-20: a non-USD posting needs the currency's USD rate in the command's mark snapshot; refuse plainly (409, no
    ERROR attention, the message not prefixed with its own code) before anything is posted."""
    if ccy == "USD":
        return
    try:
        ctx.fx.rate(ccy)
    except LedgerError as e:
        raise ConflictError("FX_RATE_UNAVAILABLE", f"no {ccy}/USD rate yet; try again when FX rates load", {"currency": ccy}) from e


def amount_c(payload: dict) -> int:
    """The command's amount in cents: a typed decimal string in ``amount`` read strictly (``units.parse_money``, BM-21),
    else ``amount_c`` (integer cents) as ``require_amount_c`` reads it."""
    raw = payload.get("amount")
    if raw is not None and payload.get("amount_c") is None:
        try:
            return parse_money(raw, "amount")
        except ValueError as e:
            raise RejectError("AMOUNT", str(e), {"amount": str(raw)}) from e
    return require_amount_c(payload)


def _transfer_row(ctx: TxContext, kind: str, from_account: Optional[str], to_account: Optional[str], ccy: str, amount_c: int, entry_id: int,
                  idem_key: str, linked_type: Optional[str] = None, linked_id: Optional[str] = None, shortfall_c: Optional[int] = None) -> str:
    tid = next_id(ctx.conn, "transfer")
    ctx.conn.execute(
        "INSERT INTO transfers(transfer_id, command_id, ts, business_date, kind, from_account, to_account, currency, amount_c, linked_type, linked_id,"
        " shortfall_c, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, ctx.command_id, ctx.ts, business_date(ctx), kind, from_account, to_account, ccy, int(amount_c), linked_type, linked_id, shortfall_c, entry_id, idem_key))
    return tid


# ---- capital ----------------------------------------------------------------------------------------------------------
def post_init_capital(ctx: TxContext, lifetime_id: str, amount_c: int, ccy: str = "USD", source: str = "initial capital") -> dict:
    """Post the lifetime's initial capital once (idem ``capital:initial:{lifetime}``); a second call is a no-op."""
    aid = f"{lifetime_id}:TREASURY"
    idem = f"capital:initial:{lifetime_id}"
    existing = ctx.conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key=?", (idem,)).fetchone()
    if existing is not None:
        return {"account_id": aid, "amount_c": int(amount_c), "currency": ccy, "skipped": True, "entry_id": int(existing["entry_id"])}
    row = account_row(ctx.conn, aid)
    if row is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
    entry_id = ctx.post(Entry("CAPITAL", f"{source}: {ccy} {fmt_money(amount_c)} to {aid}",
                              [dr(aid, "1010", ccy, amount_c, memo=source), cr(aid, "3000", ccy, amount_c, memo=source)],
                              idem, ref_type="lifetime", ref_id=lifetime_id, business_date=asof_date(ctx)))
    tid = _transfer_row(ctx, "INITIAL_CAPITAL", None, aid, ccy, amount_c, entry_id, f"xfer:{idem}")
    ctx.event("CAPITAL_DEPOSITED", account_id=aid, ref_type="transfer", ref_id=tid, amount_c=int(amount_c), currency=ccy, source=source, initial=True)
    return {"account_id": aid, "amount_c": int(amount_c), "currency": ccy, "skipped": False, "entry_id": entry_id, "transfer_id": tid}


def h_init_capital(ctx: TxContext, payload: dict) -> dict:
    row = _treasury_account(ctx, payload)
    amount_c = require_amount_c(payload)
    ccy = _ccy(payload)
    return post_init_capital(ctx, row["lifetime_id"], amount_c, ccy, str(payload.get("source") or "initial capital"))


def h_deposit(ctx: TxContext, payload: dict) -> dict:
    row = _treasury_account(ctx, payload)
    aid = row["account_id"]
    amount = amount_c(payload)
    ccy = _ccy(payload)
    require_rate(ctx, ccy)
    return _deposit(ctx, row, aid, amount, ccy, payload)


def _deposit(ctx: TxContext, row, aid: str, amount_c: int, ccy: str, payload: dict) -> dict:
    typed = str(payload.get("memo") or "").strip()
    memo = typed or "capital deposit"
    idem = f"deposit:{ctx.request_id}"
    entry_id = ctx.post(Entry("CAPITAL", f"capital deposit {ccy} {fmt_money(amount_c)} to {aid}" + (f": {typed}" if typed else ""),
                              [dr(aid, "1010", ccy, amount_c, memo=memo), cr(aid, "3000", ccy, amount_c, memo=memo)], idem,
                              ref_type="lifetime", ref_id=row["lifetime_id"], business_date=asof_date(ctx)))
    tid = _transfer_row(ctx, "DEPOSIT", None, aid, ccy, amount_c, entry_id, f"xfer:{idem}")
    ctx.event("CAPITAL_DEPOSITED", account_id=aid, ref_type="transfer", ref_id=tid, amount_c=amount_c, currency=ccy, memo=memo)
    return {"transfer_id": tid, "account_id": aid, "amount_c": amount_c, "currency": ccy, "entry_id": entry_id, "settled_c": _settled(ctx, aid, ccy),
            "shortfalls_resolved": _resolve_shortfalls(ctx, row["lifetime_id"], ccy)}


def h_withdraw(ctx: TxContext, payload: dict) -> dict:
    row = _treasury_account(ctx, payload)
    aid = row["account_id"]
    amount = amount_c(payload)
    ccy = _ccy(payload)
    require_rate(ctx, ccy)
    return _withdraw(ctx, row, aid, amount, ccy, payload)


def _withdraw(ctx: TxContext, row, aid: str, amount_c: int, ccy: str, payload: dict) -> dict:
    settled = _settled(ctx, aid, ccy)
    if settled < amount_c:
        raise ConflictError("INSUFFICIENT_FUNDS", f"Treasury settled {ccy} {fmt_money(settled)} is less than the withdrawal {fmt_money(amount_c)}",
                            {"settled_c": settled, "amount_c": amount_c, "currency": ccy})
    typed = str(payload.get("memo") or "").strip()
    memo = typed or "capital withdrawal"
    idem = f"withdraw:{ctx.request_id}"
    entry_id = ctx.post(Entry("CAPITAL", f"capital withdrawal {ccy} {fmt_money(amount_c)} from {aid}" + (f": {typed}" if typed else ""),
                              [dr(aid, "3000", ccy, amount_c, memo=memo), cr(aid, "1010", ccy, amount_c, memo=memo)], idem,
                              ref_type="lifetime", ref_id=row["lifetime_id"], business_date=asof_date(ctx)))
    tid = _transfer_row(ctx, "WITHDRAWAL", aid, None, ccy, amount_c, entry_id, f"xfer:{idem}")
    ctx.event("CAPITAL_WITHDRAWN", account_id=aid, ref_type="transfer", ref_id=tid, amount_c=amount_c, currency=ccy, memo=memo)
    return {"transfer_id": tid, "account_id": aid, "amount_c": amount_c, "currency": ccy, "entry_id": entry_id, "settled_c": _settled(ctx, aid, ccy)}


# ---- transfers ----------------------------------------------------------------------------------------------------------
def _post_transfer(ctx: TxContext, kind: str, src: str, dst: str, ccy: str, amount_c: int, memo: str, linked_type=None, linked_id=None,
                   shortfall_c=None, event: str = "TRANSFER", idem_key: Optional[str] = None, entry_kind: Optional[str] = None,
                   entry_memo: Optional[str] = None) -> dict:
    """One internal transfer: entry ``Dr 3100 / Cr 1010`` (source) + ``Dr 1010 / Cr 3100`` (destination) and its ``transfers`` row,
    both dated ``business_date(ctx)`` (C15).  ``idem_key`` (journal entry and transfer row; default ``xfer:{transfer_id}``),
    ``entry_kind`` (default the transfer kind) and ``entry_memo`` serve the as-of funding commands (``collateral``: idem
    ``asofsweep:...``, entry kind ASOF_FUNDING, memo "as-of funding")."""
    tid = next_id(ctx.conn, "transfer")
    idem = idem_key or f"xfer:{tid}"
    bd = business_date(ctx)
    text = entry_memo if entry_memo is not None else f"{kind.lower().replace('_', '-')} {ccy} {fmt_money(amount_c)} {src} -> {dst}: {memo}"
    entry_id = ctx.post(Entry(entry_kind or ("TRANSFER" if kind == "TRANSFER" else kind), text,
                              [dr(src, "3100", ccy, amount_c, memo=memo), cr(src, "1010", ccy, amount_c, memo=memo),
                               dr(dst, "1010", ccy, amount_c, memo=memo), cr(dst, "3100", ccy, amount_c, memo=memo)],
                              idem, ref_type="transfer", ref_id=tid, business_date=asof_date(ctx)))
    ctx.conn.execute(
        "INSERT INTO transfers(transfer_id, command_id, ts, business_date, kind, from_account, to_account, currency, amount_c, linked_type, linked_id,"
        " shortfall_c, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, ctx.command_id, ctx.ts, bd, kind, src, dst, ccy, int(amount_c), linked_type, linked_id, shortfall_c, entry_id, idem))
    ctx.event(event, account_id=dst, ref_type="transfer", ref_id=tid, from_account=src, to_account=dst, amount_c=int(amount_c), currency=ccy,
              linked_type=linked_type, linked_id=linked_id, shortfall_c=shortfall_c, memo=memo, business_date=bd)
    return {"transfer_id": tid, "entry_id": entry_id, "from_account": src, "to_account": dst, "amount_c": int(amount_c), "currency": ccy, "business_date": bd}


def h_transfer(ctx: TxContext, payload: dict) -> dict:
    src = str(payload.get("from_account") or "")
    dst = str(payload.get("to_account") or "")
    if not src or not dst:
        raise RejectError("ACCOUNT_REQUIRED", "from_account and to_account are required")
    if src == dst:
        raise RejectError("SAME_ACCOUNT", "from_account and to_account are the same")
    srow, drow = account_row(ctx.conn, src), account_row(ctx.conn, dst)
    if srow is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {src} does not exist")
    if drow is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {dst} does not exist")
    if srow["lifetime_id"] != drow["lifetime_id"]:
        raise RejectError("CROSS_BOOK", f"books never transfer to each other: {src} and {dst} belong to different lifetimes",
                          {"from_lifetime": srow["lifetime_id"], "to_lifetime": drow["lifetime_id"]})
    _open_account(ctx, srow)
    _open_account(ctx, drow)
    amount = amount_c(payload)
    ccy = _ccy(payload)
    require_rate(ctx, ccy)
    check_transfer(ctx, src, ccy, amount)
    return _transfer(ctx, src, dst, ccy, amount, payload)


def check_transfer(ctx: TxContext, src: str, ccy: str, amount_c: int) -> int:
    """MONEY-6: a transfer leaves the source's SETTLED cash at once, so it is measured against the immediate available
    (``accounting.immediate_available_c``: settled cash net of payables and working-order reservations, receivables due
    today counted, never above settled) and refused 409 INSUFFICIENT_FUNDS naming the settled amount and what settles
    later.  Worked (s7, Wed 18:00): settled 0.00, a sale's 344,995.00 settling Thu -> 'can move USD 0.00 now (settled cash
    0.00; 344,995.00 settles 2026-10-22)' instead of a 500 from the L18 integrity check.  Returns the immediate figure."""
    from .accounting import _available_parts, immediate_available_c
    master = _master(ctx)
    imm = immediate_available_c(ctx.conn, src, ccy, None, ctx.marks, master, ctx.now)
    if imm >= amount_c:
        return imm
    parts = _available_parts(ctx.conn, src, ccy, None, ctx.marks, master, ctx.now)
    settled, recv, pay, reserved = parts[1], parts[2], parts[3], parts[4]
    # every pending receivable is cash that has not settled yet (a sale due tomorrow counts in 'available' after 17:00, never in settled cash)
    later = ctx.conn.execute("SELECT COALESCE(SUM(amount_c),0) AS n, MIN(due_date) AS d FROM settlements WHERE account_id=? AND currency=? AND status='PENDING'"
                             " AND amount_c > 0", (src, ccy)).fetchone()
    # FINSIM1 P6 (C9b): ``settled`` above is net of the cash the accounting adjusters hold back (the counterparties' collateral, the Reg T
    # margin cash): the sentence names the ledger's settled cash and each of them, so the refusal adds up
    from .accounting import cash_adjustments
    held = [a for a in cash_adjustments(ctx.conn, src, ccy, ctx.marks, master) if int(a.get("amount_c") or 0) > 0]
    parts = [f"settled cash {fmt_money(settled + sum(int(a['amount_c']) for a in held))}"]
    for a in held:
        parts.append(f"less {a['label']} {fmt_money(int(a['amount_c']))}")
    if pay:
        parts.append(f"less payables {fmt_money(pay)}")
    if reserved:
        parts.append(f"less working orders {fmt_money(reserved)}")
    incoming = int(later["n"] or 0) if later is not None else 0
    tail = f"; {fmt_money(incoming)} of sale / FX proceeds settles {later['d']} and can move after it" if incoming > 0 and later["d"] else ""
    raise ConflictError("INSUFFICIENT_FUNDS", f"{src} can move {ccy} {fmt_money(imm)} now ({', '.join(parts)}{tail}); the transfer needs {fmt_money(amount_c)}",
                        {"available_c": imm, "settled_c": settled, "payables_c": pay, "reserved_c": reserved, "receivable_later_c": max(0, incoming),
                         "amount_c": amount_c, "currency": ccy, "account_id": src})


def _transfer(ctx: TxContext, src: str, dst: str, ccy: str, amount_c: int, payload: dict) -> dict:
    memo = str(payload.get("memo") or "internal transfer")
    out = _post_transfer(ctx, "TRANSFER", src, dst, ccy, amount_c, memo)
    out["from_settled_c"] = _settled(ctx, src, ccy)
    out["to_settled_c"] = _settled(ctx, dst, ccy)
    out["shortfalls_resolved"] = _resolve_shortfalls(ctx, account_row(ctx.conn, dst)["lifetime_id"], ccy)
    return out


def _resolve_shortfalls(ctx: TxContext, lifetime_id: str, ccy: str) -> int:
    """After cash arrives in a lifetime (a transfer, a deposit): resolve its open FUNDING_SHORTFALL items now covered
    (``execution.auto_resolve_shortfall``: settled cash back at zero, or an option-margin reservation covered, SR-13)."""
    from .execution import auto_resolve_shortfall
    n = 0
    for r in ctx.conn.execute("SELECT DISTINCT t.account_id FROM attention t JOIN accounts a ON a.account_id=t.account_id WHERE t.code='FUNDING_SHORTFALL'"
                              " AND t.resolved_ts IS NULL AND a.lifetime_id=?", (lifetime_id,)).fetchall():
        n += auto_resolve_shortfall(ctx, r["account_id"], ccy)
    return n


def sweep_in(ctx: TxContext, account: str, ccy: str, amount_c: int, linked_type: str, linked_id: str, shortfall_c: int) -> str:
    """Move exactly ``amount_c`` from the lifetime's Treasury to ``account`` as one linked SWEEP_IN transfer.  Raises
    ``ConflictError INSUFFICIENT_FUNDS`` (with the three amounts) when Treasury's available cash in that currency,
    net of the book's reserve (``sweepable_c``), cannot cover it.  Under ``ctx.asof_date`` = D (C15) the figure is the
    business-date one, ``asof_sweepable_c`` (Treasury settled at the close of D less the reserve), and the sweep is dated D;
    today's balance is not consulted here (``execution.fund`` and L18 keep the current-balance floor).  Returns the
    transfer id."""
    amount_c = int(amount_c)
    if amount_c <= 0:
        raise RejectError("AMOUNT", "sweep amount must be positive")
    arow = account_row(ctx.conn, account)
    if arow is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account} does not exist")
    _open_account(ctx, arow)
    lid, _ = split_account_id(account)
    tre = f"{lid}:TREASURY"
    if account == tre:
        raise RejectError("SAME_ACCOUNT", "Treasury cannot sweep into itself")
    d = asof_date(ctx)
    t_avail = asof_sweepable_c(ctx, lid, ccy, d) if d else sweepable_c(ctx, lid, ccy)
    if t_avail < amount_c:
        if d:
            raise ConflictError("INSUFFICIENT_FUNDS", f"Treasury {tre} had {ccy} {fmt_money(max(t_avail, 0))} available for {account} at the close of {d}"
                                f" (business date); the as-of sweep needs {fmt_money(amount_c)}",
                                {"need_c": amount_c, "treasury_available_c": t_avail, "currency": ccy, "account_id": account, "linked_type": linked_type,
                                 "linked_id": linked_id, "as_of": d})
        raise ConflictError("INSUFFICIENT_FUNDS", f"Treasury {tre} has {ccy} {fmt_money(max(t_avail, 0))} available for {account}; the sweep needs {fmt_money(amount_c)}",
                            {"need_c": amount_c, "treasury_available_c": t_avail, "currency": ccy, "account_id": account, "linked_type": linked_type,
                             "linked_id": linked_id})
    out = _post_transfer(ctx, "SWEEP_IN", tre, account, ccy, amount_c, f"sweep-in for {linked_type} {linked_id}", linked_type, linked_id,
                         int(shortfall_c), event="SWEEP_IN")
    return out["transfer_id"]


def sweep_out(ctx: TxContext, account: str, ccy: str, amount_c: int, note: str = "idle cash") -> str:
    """Return ``amount_c`` of idle settled cash from ``account`` to its Treasury (SWEEP_OUT; the job computes the excess)."""
    arow = account_row(ctx.conn, account)
    if arow is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account} does not exist")
    _open_account(ctx, arow)
    lid, _ = split_account_id(account)
    out = _post_transfer(ctx, "SWEEP_OUT", account, f"{lid}:TREASURY", ccy, int(amount_c), f"sweep-out: {note}", event="SWEEP_OUT")
    return out["transfer_id"]


# ---- settings --------------------------------------------------------------------------------------------------------
_SETTING_FIELDS = {"sweep_out": lambda v: 1 if v else 0, "sweep_buffer_c": int, "pb_financing": lambda v: 1 if v else 0, "pb_limit_c": int}


def h_update_settings(ctx: TxContext, payload: dict) -> dict:
    aid = str(payload.get("account_id") or "")
    row = account_row(ctx.conn, aid)
    if row is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
    _open_account(ctx, row)
    before, after = {}, {}
    for k, conv in _SETTING_FIELDS.items():
        if k in payload and payload[k] is not None:
            try:
                v = conv(payload[k])
            except (TypeError, ValueError) as e:
                raise RejectError("SETTING_VALUE", f"{k}: {payload[k]!r} is not valid") from e
            if k in ("sweep_buffer_c", "pb_limit_c") and v < 0:
                raise RejectError("SETTING_VALUE", f"{k} must be >= 0")
            if k == "pb_financing" and v and row["kind"] != "PB":
                raise RejectError("NOT_PB", f"{aid} is not a prime-broker account: pb_financing applies to PB accounts only")
            before[k] = row[k]
            after[k] = v
    if not after:
        raise RejectError("NOTHING_TO_UPDATE", "no settable field given (sweep_out, sweep_buffer_c, pb_financing, pb_limit_c)")
    sets = ", ".join(f"{k}=?" for k in after)
    ctx.conn.execute(f"UPDATE accounts SET {sets} WHERE account_id=?", (*after.values(), aid))
    ctx.event("SETTINGS_CHANGED", account_id=aid, ref_type="account", ref_id=aid, before=before, after=after)
    row = account_row(ctx.conn, aid)
    return {"account_id": aid, "before": before, "after": after, "settings": {k: row[k] for k in _SETTING_FIELDS}}


def register(bus) -> None:
    bus.register("treasury.init_capital", h_init_capital)
    bus.register("treasury.deposit", h_deposit)
    bus.register("treasury.withdraw", h_withdraw)
    bus.register("treasury.transfer", h_transfer)
    bus.register("account.update_settings", h_update_settings, light=True)
