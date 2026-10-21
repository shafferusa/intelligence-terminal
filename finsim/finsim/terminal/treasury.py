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
- A sweep-in moves exactly the shortfall, linked to what it paid for (L19).
"""
from __future__ import annotations

from typing import Optional

from .books import account_row, lifetime_row, split_account_id
from .commands import ConflictError, RejectError, TxContext, require_amount_c
from .db import dumps, next_id
from .ledger import Entry, cr, dr
from .units import fmt_money

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


def _transfer_row(ctx: TxContext, kind: str, from_account: Optional[str], to_account: Optional[str], ccy: str, amount_c: int, entry_id: int,
                  idem_key: str, linked_type: Optional[str] = None, linked_id: Optional[str] = None, shortfall_c: Optional[int] = None) -> str:
    tid = next_id(ctx.conn, "transfer")
    ctx.conn.execute(
        "INSERT INTO transfers(transfer_id, command_id, ts, business_date, kind, from_account, to_account, currency, amount_c, linked_type, linked_id,"
        " shortfall_c, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, ctx.command_id, ctx.ts, ctx.today, kind, from_account, to_account, ccy, int(amount_c), linked_type, linked_id, shortfall_c, entry_id, idem_key))
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
                              idem, ref_type="lifetime", ref_id=lifetime_id))
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
    amount_c = require_amount_c(payload)
    ccy = _ccy(payload)
    memo = str(payload.get("memo") or "capital deposit")
    idem = f"deposit:{ctx.request_id}"
    entry_id = ctx.post(Entry("CAPITAL", f"capital deposit {ccy} {fmt_money(amount_c)} to {aid}: {memo}",
                              [dr(aid, "1010", ccy, amount_c, memo=memo), cr(aid, "3000", ccy, amount_c, memo=memo)], idem,
                              ref_type="lifetime", ref_id=row["lifetime_id"]))
    tid = _transfer_row(ctx, "DEPOSIT", None, aid, ccy, amount_c, entry_id, f"xfer:{idem}")
    ctx.event("CAPITAL_DEPOSITED", account_id=aid, ref_type="transfer", ref_id=tid, amount_c=amount_c, currency=ccy, memo=memo)
    return {"transfer_id": tid, "account_id": aid, "amount_c": amount_c, "currency": ccy, "entry_id": entry_id, "settled_c": _settled(ctx, aid, ccy)}


def h_withdraw(ctx: TxContext, payload: dict) -> dict:
    row = _treasury_account(ctx, payload)
    aid = row["account_id"]
    amount_c = require_amount_c(payload)
    ccy = _ccy(payload)
    settled = _settled(ctx, aid, ccy)
    if settled < amount_c:
        raise ConflictError("INSUFFICIENT_FUNDS", f"Treasury settled {ccy} {fmt_money(settled)} is less than the withdrawal {fmt_money(amount_c)}",
                            {"settled_c": settled, "amount_c": amount_c, "currency": ccy})
    memo = str(payload.get("memo") or "capital withdrawal")
    idem = f"withdraw:{ctx.request_id}"
    entry_id = ctx.post(Entry("CAPITAL", f"capital withdrawal {ccy} {fmt_money(amount_c)} from {aid}: {memo}",
                              [dr(aid, "3000", ccy, amount_c, memo=memo), cr(aid, "1010", ccy, amount_c, memo=memo)], idem,
                              ref_type="lifetime", ref_id=row["lifetime_id"]))
    tid = _transfer_row(ctx, "WITHDRAWAL", aid, None, ccy, amount_c, entry_id, f"xfer:{idem}")
    ctx.event("CAPITAL_WITHDRAWN", account_id=aid, ref_type="transfer", ref_id=tid, amount_c=amount_c, currency=ccy, memo=memo)
    return {"transfer_id": tid, "account_id": aid, "amount_c": amount_c, "currency": ccy, "entry_id": entry_id, "settled_c": _settled(ctx, aid, ccy)}


# ---- transfers ----------------------------------------------------------------------------------------------------------
def _post_transfer(ctx: TxContext, kind: str, src: str, dst: str, ccy: str, amount_c: int, memo: str, linked_type=None, linked_id=None,
                   shortfall_c=None, event: str = "TRANSFER") -> dict:
    tid = next_id(ctx.conn, "transfer")
    idem = f"xfer:{tid}"
    entry_id = ctx.post(Entry("TRANSFER" if kind == "TRANSFER" else kind, f"{kind.lower().replace('_', '-')} {ccy} {fmt_money(amount_c)} {src} -> {dst}: {memo}",
                              [dr(src, "3100", ccy, amount_c, memo=memo), cr(src, "1010", ccy, amount_c, memo=memo),
                               dr(dst, "1010", ccy, amount_c, memo=memo), cr(dst, "3100", ccy, amount_c, memo=memo)],
                              idem, ref_type="transfer", ref_id=tid))
    ctx.conn.execute(
        "INSERT INTO transfers(transfer_id, command_id, ts, business_date, kind, from_account, to_account, currency, amount_c, linked_type, linked_id,"
        " shortfall_c, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, ctx.command_id, ctx.ts, ctx.today, kind, src, dst, ccy, int(amount_c), linked_type, linked_id, shortfall_c, entry_id, idem))
    ctx.event(event, account_id=dst, ref_type="transfer", ref_id=tid, from_account=src, to_account=dst, amount_c=int(amount_c), currency=ccy,
              linked_type=linked_type, linked_id=linked_id, shortfall_c=shortfall_c, memo=memo)
    return {"transfer_id": tid, "entry_id": entry_id, "from_account": src, "to_account": dst, "amount_c": int(amount_c), "currency": ccy}


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
    amount_c = require_amount_c(payload)
    ccy = _ccy(payload)
    avail = _available(ctx, src, ccy)
    if avail < amount_c:
        raise ConflictError("INSUFFICIENT_FUNDS", f"{src} has {ccy} {fmt_money(avail)} available, transfer needs {fmt_money(amount_c)}",
                            {"available_c": avail, "amount_c": amount_c, "currency": ccy, "account_id": src})
    memo = str(payload.get("memo") or "internal transfer")
    out = _post_transfer(ctx, "TRANSFER", src, dst, ccy, amount_c, memo)
    out["from_settled_c"] = _settled(ctx, src, ccy)
    out["to_settled_c"] = _settled(ctx, dst, ccy)
    return out


def sweep_in(ctx: TxContext, account: str, ccy: str, amount_c: int, linked_type: str, linked_id: str, shortfall_c: int) -> str:
    """Move exactly ``amount_c`` from the lifetime's Treasury to ``account`` as one linked SWEEP_IN transfer.  Raises
    ``ConflictError INSUFFICIENT_FUNDS`` (with the three amounts) when Treasury's available cash in that currency,
    net of ``settings.treasury_reserve_c``, cannot cover it.  Returns the transfer id."""
    amount_c = int(amount_c)
    if amount_c <= 0:
        raise RejectError("AMOUNT", "sweep amount must be positive")
    arow = account_row(ctx.conn, account)
    if arow is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account} does not exist")
    _open_account(ctx, arow)
    lid, _ = split_account_id(account)
    tre = f"{lid}:TREASURY"
    from .db import get_setting
    reserve = int(get_setting(ctx.conn, "treasury_reserve_c", 0) or 0) if ccy == "USD" else 0
    t_avail = _available(ctx, tre, ccy) - reserve
    if t_avail < amount_c:
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
