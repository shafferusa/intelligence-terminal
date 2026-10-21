"""Lifetimes: reset with history, onboarding agreements, book settings, History read model (TERMINAL_DESIGN.md §13.10,
§2.12 ``lifetimes.py``, §6.0, §15 ``/api/books/*`` + ``/api/history/*``, §19.14; TERMINAL_WORKPLAN.md WP-23).

Handlers (registered by ``register(bus)``; the API routes are WP-11's ``api_views``):
- ``book.reset_lifetime {book_code, starting_capital_c | starting_capital, confirm: "RESET", name?}`` — ONE command,
  one transaction, request_id from the UI (a replay returns the stored result and opens nothing twice):
  (1) every WORKING / PARTIAL / SUSPENDED / WAITING_PARENT order of the lifetime -> CANCELLED, reason ``lifetime reset``
      (``orders._close_order`` / ``_close_package``: order_events + events, hedge children, the borrow behind an unfilled
      short order goes back through ``lending.release_for_cancel``); then ``lending.release_unused`` returns every other
      borrowed quantity that backs only cancelled orders (§13.1 step 6) so L12 holds at the freeze;
  (2) the final figures at the command's mark snapshot while the lifetime is still OPEN: ``final_nav_c`` =
      ``nav({lifetime}:ALL)`` with its ``final_quality``, ``final_realised_c`` = Σ natural 4000 + 4400 + 4450 (base) of
      its accounts, ``final_pnl_c = final_nav_c − starting_capital_c − Σ deposits + Σ withdrawals`` (the 3000 lines of
      its Treasury: idem ``deposit:*`` / ``withdraw:*``), which equals NAV − contributed capital;
  (3) ``accounting.integrity("full", lifetime_id)`` on the closing lifetime AFTER its last postings; the report is stored
      on ``lifetimes.integrity_json``; a failure does NOT block the reset — it is recorded there and as an ERROR
      attention ``LIFETIME_RESET_INTEGRITY`` (nothing is deleted, ever);
  (4) a last ``nav_snapshots`` row for today (New York date) for every account and for ``{lifetime}:ALL`` (detail
      ``final: true``; an earlier row for the same day written by the SNAPSHOT job is replaced by this final one), so
      the archived read models serve the frozen marks and History shows the final day;
  (5) the lifetime row gets ``ended_ts`` = now, the final figures, ``reason``, ``status CLOSED``; every account of the
      lifetime -> ``status CLOSED`` (``ledger.post`` refuses CLOSED accounts, so the archive is read-only by
      construction; L23).  Open borrows / loans / swaps / private loans keep their rows and status (frozen);
  (6) the new lifetime ``{code}{seq+1}`` with the typed capital (or ``settings.book_defaults[code]``, else the book's
      seed capital) and the catalogue accounts (PRIVATE at once only for the INSTITUTION profile), no PB accounts,
      sweeps on; its name defaults to ``{book name} #{seq}``.
  The scoped check (§4 step 7) and the archived accounts: the bus re-runs the ledger-level set on the keys a command
  touched.  The closing lifetime's accounts are REMOVED from the touched keys at the end of this handler (its entries
  stay, so L1/L2 still run on what the reset posted): the reset's own check is the FULL report of step (3), taken after
  the last postings and stored on the row, and §13.10 requires that a pre-existing failure never blocks the reset
  (the scoped subset would otherwise roll it back with 500).  Nothing of the closing lifetime is hidden: the report
  and the ERROR attention stay, and the whole-DB runs keep reporting it.
  The capital of the new lifetime is NOT posted inside this command: L6 (``capital_explained``) sums the COMMITTED
  commands of kind ``treasury.init_capital`` / ``deposit`` / ``withdraw``, so the capital must be its own command.
  An on-commit listener (installed by ``register``) runs ``books.create_lifetime(None, bus, ...)`` right after the
  reset commits (or replays), which posts ``treasury.init_capital`` with request_id ``init:capital:{new}`` exactly
  once; a crash in between leaves an OPEN lifetime without capital that ``books.ensure_first_run`` heals at the next
  start and that any replay of the reset heals at once.  The two steps are the first-run sequence of §3.1.
- ``book.onboard_pb {lifetime_id | book_code, broker, pb_financing?, pb_limit_c?}``: a NEW relationship needs
  ``books.require_onboard`` (the threshold or the INSTITUTION profile -> 400 ``NOT_ELIGIBLE`` otherwise); records three
  ``agreements`` rows (ISDA_MASTER, CSA, PBA, ``signed_date`` = today in New York) and calls ``prime.open_pb(...,
  check_eligibility=False)`` in the same transaction.  Idempotent per (lifetime, broker): a later request with any
  request_id finds the rows and the account and answers ``created: false`` with the same rows (no 409).  Unknown
  broker -> 400 ``UNKNOWN_BROKER`` before anything is written.
- ``book.update_settings`` (light): ``pb_otc_threshold_c`` (or ``pb_otc_threshold`` decimal string), ``qp_threshold_c``
  (per profile), ``book_defaults {code: {name?, starting_capital_c? | starting_capital?}}`` — names and starting
  capitals apply to NEW lifetimes only; a name also changes ``books.name`` at once (display only); the live
  lifetime's history is never rewritten.  Event ``SETTINGS_CHANGED`` with before / after.
- ``history(services, lifetime_id) -> LifetimeView``: the row, the final figures, deposits / withdrawals, the stored
  integrity verdict, accounts, agreements, the count of orders cancelled at the reset, and the links of the read-only
  History pages (``#/h/{lifetime}/…``) and routes (``/api/history/{lifetime}/…``, ``read_only: true``).

Conventions (sentence LIFETIME): "Reset lifetime closes the current lifetime read-only (nothing is deleted) and opens
a new one with the capital you type; past lifetimes stay browsable under History."  Money is integer cents; every
mark-dependent figure carries the snapshot's quality.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import weakref
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import books as _books, config, orders as _orders
from .books import Book, account_row, book_of, lifetime_row, next_seq, open_lifetime
from .clock import ny_of
from .commands import ConflictError, RejectError, TxContext, require_amount_c
from .db import dumps, get_setting, set_setting
from .ledger import natural
from .units import fmt_money, to_c

RESET_REASON = "lifetime reset"
LIVE_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
REALISED_GLS = ("4000", "4400", "4450")
AGREEMENT_KINDS = ("ISDA_MASTER", "CSA", "PBA")
AGREEMENT_MEANING = {
    "ISDA_MASTER": "ISDA Master Agreement: the framework contract under which every OTC derivative with this dealer is traded (netting, events of default).",
    "CSA": "Credit Support Annex: the collateral terms of the ISDA (what is posted as initial and variation margin, thresholds, interest on collateral).",
    "PBA": "Prime Brokerage Agreement: custody, financing, DVP settlement of listed trades and securities lending with this prime broker.",
    "GMRA": "Global Master Repurchase Agreement: the repo framework (Build 2, NOT IN THIS BUILD).",
    "MSLA": "Master Securities Lending Agreement / GMSLA: the stock-lending framework the borrow tiers model (named, not signed here).",
}
PROFILES = ("INDIVIDUAL", "FUND", "INSTITUTION")
_LISTENING: "weakref.WeakSet" = weakref.WeakSet()


class NotBuilt(RejectError):
    """A step that needs a module this build does not have -> HTTP 501."""
    http = 501


@dataclass
class LifetimeView:
    lifetime_id: str
    book_code: str
    seq: int
    name: str
    status: str
    started_ts: int
    started: str
    ended_ts: Optional[int]
    ended: Optional[str]
    starting_capital_c: int
    final_nav_c: Optional[int]
    final_realised_c: Optional[int]
    final_pnl_c: Optional[int]
    final_quality: Optional[str]
    reason: Optional[str]
    integrity_ok: Optional[bool]
    integrity_failing: List[str]
    deposits_c: int
    withdrawals_c: int
    accounts: List[dict]
    agreements: List[dict]
    orders_cancelled_at_reset: int
    snapshot_date: Optional[str]
    read_only: bool
    links: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ---- small helpers ----------------------------------------------------------------------------------------------------
def _services(ctx: TxContext):
    if ctx.services is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return ctx.services


def _resolve_lifetime(conn: sqlite3.Connection, payload: dict) -> str:
    """``lifetime_id`` as given, else the OPEN lifetime of ``book_code``."""
    lid = payload.get("lifetime_id")
    if lid:
        return str(lid)
    code = str(payload.get("book_code") or "").upper()
    if not code:
        raise RejectError("LIFETIME_REQUIRED", "lifetime_id or book_code is required")
    if book_of(conn, code) is None:
        raise RejectError("BOOK_UNKNOWN", f"book {code!r} does not exist")
    row = open_lifetime(conn, code)
    if row is None:
        raise ConflictError("NO_OPEN_LIFETIME", f"book {code} has no open lifetime")
    return row["lifetime_id"]


def _accounts_of(conn: sqlite3.Connection, lid: str) -> List[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (lid,)).fetchall()]


def realised_itd_c(conn: sqlite3.Connection, account_ids: List[str]) -> int:
    """Σ natural(4000 + 4400 + 4450) in base over the accounts (realised P&L, dividends, lending income to date)."""
    if not account_ids:
        return 0
    q = ",".join("?" for _ in account_ids)
    total = 0
    for r in conn.execute(f"SELECT gl, SUM(base_c) b FROM gl_balances WHERE account_id IN ({q}) AND gl IN ('4000','4400','4450') GROUP BY gl", account_ids).fetchall():
        total += natural(r["gl"], int(r["b"] or 0))
    return total


def capital_flows_c(conn: sqlite3.Connection, lid: str):
    """``(deposits_c, withdrawals_c)`` of the lifetime's Treasury in base: the 3000 lines of the entries whose idem key
    is ``deposit:*`` / ``withdraw:*`` (the initial capital, ``capital:initial:*``, is the row's starting capital)."""
    tre = f"{lid}:TREASURY"
    out = []
    for prefix in ("deposit:", "withdraw:"):
        r = conn.execute("SELECT SUM(l.base_c) b FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                         " WHERE l.account_id=? AND l.gl='3000' AND e.idem_key LIKE ?", (tre, prefix + "%")).fetchone()
        out.append(natural("3000", int(r["b"] or 0)))
    return out[0], -out[1]


def _starting_capital_c(conn: sqlite3.Connection, book: Book, payload: dict) -> int:
    """The typed capital, else ``settings.book_defaults[code].starting_capital_c``, else the book's seed capital."""
    if payload.get("starting_capital_c") is not None or payload.get("starting_capital") is not None:
        return require_amount_c(payload, "starting_capital_c", "starting_capital")
    defaults = get_setting(conn, "book_defaults", {}) or {}
    d = defaults.get(book.code) if isinstance(defaults, dict) else None
    cap = d.get("starting_capital_c") if isinstance(d, dict) else None
    try:
        cap = int(cap) if cap is not None else int(book.starting_capital_c)
    except (TypeError, ValueError) as e:
        raise RejectError("AMOUNT", f"book_defaults[{book.code}].starting_capital_c is not an integer number of cents: {cap!r}") from e
    if cap <= 0:
        raise RejectError("AMOUNT", "starting capital must be positive")
    return cap


def _integrity_ok(raw: Optional[str]):
    """``(ok, failing ids)`` from a stored ``integrity_json`` (None, [] when nothing is stored / unreadable)."""
    if not raw:
        return None, []
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return None, []
    failing = sorted({str(p.get("id")) for p in d.get("problems", []) if p.get("severity") == "ERROR"})
    return bool(d.get("ok")), failing


# ---- the reset ----------------------------------------------------------------------------------------------------------
def cancel_open_orders(ctx: TxContext, services, lid: str) -> List[dict]:
    """Every live order of the lifetime -> CANCELLED "lifetime reset" through the orders module's close path (an AON
    package closes as a unit; a primary's waiting children close with it); rows are re-read so a leg already closed by
    its package is not closed twice."""
    conn = ctx.conn
    rows = conn.execute("SELECT o.order_id FROM orders o JOIN accounts a ON a.account_id=o.account_id WHERE a.lifetime_id=?"
                        " AND o.status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT') ORDER BY o.order_id", (lid,)).fetchall()
    out: List[dict] = []
    for r in rows:
        cur = conn.execute("SELECT * FROM orders WHERE order_id=?", (r["order_id"],)).fetchone()
        if cur is None or cur["status"] not in LIVE_STATUSES:
            continue
        if cur["package_id"] and cur["fill_mode"] == "AON":
            out.extend(_orders._close_package(ctx, services, cur["package_id"], "CANCELLED", RESET_REASON, "ORDER_CANCELLED"))
        else:
            out.append(_orders._close_order(ctx, services, cur, "CANCELLED", RESET_REASON, "ORDER_CANCELLED"))
    return out


def release_unused_borrows(ctx: TxContext, services, lid: str) -> List[dict]:
    """§13.1 step 6 at the freeze: every borrowed quantity of the lifetime that backs only cancelled orders goes back
    (``lending.release_unused``); when the lending module is not bound nothing is returned and the result says so."""
    lending = getattr(services, "lending", None)
    if lending is None or not hasattr(lending, "release_unused"):
        return []
    return list(lending.release_unused(ctx, services, lid, ctx.today) or [])


def write_final_snapshots(ctx: TxContext, services, lid: str, account_ids: List[str], snap_date) -> Dict[str, int]:
    """The last ``nav_snapshots`` rows (every account + ``{lifetime}:ALL``) for ``snap_date`` at the command's marks."""
    out: Dict[str, int] = {}
    for scope in list(account_ids) + [f"{lid}:ALL"]:
        row = services.accounting.snapshot_row(scope, snap_date, snapshot=ctx.marks)
        try:
            detail = json.loads(row.get("detail_json") or "{}")
        except (TypeError, ValueError):
            detail = {}
        detail.update({"final": True, "reset": True, "computed_ts": ctx.ts})
        row["detail_json"] = dumps(detail)
        services.accounting.insert_snapshot(ctx.conn, row)
        out[scope] = int(row["nav_c"])
    return out


def _exclude_from_scoped(ctx: TxContext, account_ids: List[str]) -> None:
    """Remove the archived accounts from the keys of the bus's scoped check (see the module docstring); the entries the
    reset posted stay in ``touched_entries`` so L1 / L2 still run on them."""
    drop = set(account_ids)
    ctx.touched_accounts = {a for a in ctx.touched_accounts if a not in drop}
    ctx.touched_instruments = {t for t in ctx.touched_instruments if t[0] not in drop}


def h_reset_lifetime(ctx: TxContext, payload: dict) -> dict:
    """``book.reset_lifetime {book_code, starting_capital_c | starting_capital, confirm: "RESET", name?}`` (§13.10)."""
    services = _services(ctx)
    conn = ctx.conn
    if str(payload.get("confirm") or "") != "RESET":
        raise RejectError("CONFIRM", "type RESET (upper case) to confirm closing the lifetime; nothing was changed", {"confirm": payload.get("confirm")})
    code = str(payload.get("book_code") or "").upper()
    book = book_of(conn, code) if code else None
    if book is None:
        raise RejectError("BOOK_UNKNOWN", f"book {code!r} does not exist")
    lrow = open_lifetime(conn, code)
    if lrow is None:
        raise ConflictError("NO_OPEN_LIFETIME", f"book {code} has no open lifetime to reset")
    lid = lrow["lifetime_id"]
    cap_c = _starting_capital_c(conn, book, payload)
    name = str(payload["name"]).strip() if payload.get("name") not in (None, "") else None
    accounts = _accounts_of(conn, lid)
    acct_ids = [a["account_id"] for a in accounts]
    # (1) cancellations and the borrow clean-up
    cancelled = cancel_open_orders(ctx, services, lid)
    returned = release_unused_borrows(ctx, services, lid)
    # (2) final figures at the command's marks, the lifetime still OPEN
    nav = services.accounting.nav(f"{lid}:ALL", snapshot=ctx.marks)
    realised_c = realised_itd_c(conn, acct_ids)
    deposits_c, withdrawals_c = capital_flows_c(conn, lid)
    starting_c = int(lrow["starting_capital_c"])
    final_pnl_c = nav.nav_c - starting_c - deposits_c + withdrawals_c
    # (3) the full integrity report of the closing lifetime: stored, never blocking
    report = services.accounting.integrity("full", lifetime_id=lid, snapshot=ctx.marks)
    rep = report.to_dict()
    failing = sorted({str(p.get("id")) for p in rep.get("problems", []) if p.get("severity") == "ERROR"})
    if not report.ok:
        ctx.attention("ERROR", "lifetimes", "LIFETIME_RESET_INTEGRITY",
                      f"{lid}: the final integrity check failed ({', '.join(failing)}); the lifetime was closed and archived as it stands — nothing was deleted; "
                      f"the report is stored on the lifetime row",
                      {"ref_type": "lifetime", "ref_id": lid, "lifetime_id": lid, "failing": failing, "problems": rep.get("problems", [])[:20]})
    # (4) the last snapshots (today in New York)
    snap_date = ny_of(ctx.ts).date()
    snaps = write_final_snapshots(ctx, services, lid, acct_ids, snap_date)
    # (5) close the lifetime and its accounts
    conn.execute("UPDATE lifetimes SET ended_ts=?, status='CLOSED', final_nav_c=?, final_realised_c=?, final_pnl_c=?, final_quality=?, reason=?, integrity_json=?"
                 " WHERE lifetime_id=?", (ctx.ts, int(nav.nav_c), int(realised_c), int(final_pnl_c), nav.quality, RESET_REASON, dumps(rep), lid))
    conn.execute("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE lifetime_id=? AND status<>'CLOSED'", (ctx.ts, lid))
    for a in accounts:
        ctx.event("ACCOUNT_CLOSED", account_id=a["account_id"], ref_type="lifetime", ref_id=lid, role=a["role"], reason=RESET_REASON)
    ctx.event("LIFETIME_CLOSED", ref_type="lifetime", ref_id=lid, book=code, reason=RESET_REASON, final_nav_c=int(nav.nav_c), final_pnl_c=int(final_pnl_c),
              final_realised_c=int(realised_c), final_quality=nav.quality, integrity_ok=bool(report.ok), cancelled=len(cancelled), borrows_returned=len(returned))
    # (6) the new lifetime (its capital is posted by the on-commit follow-up: init:capital:{new})
    seq = next_seq(conn, code)
    new_lid = _books._insert_lifetime_and_accounts(conn, ctx.ts, book, seq, cap_c, name)
    new_accounts = [r["account_id"] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (new_lid,)).fetchall()]
    new_row = lifetime_row(conn, new_lid)
    ctx.event("LIFETIME_OPENED", ref_type="lifetime", ref_id=new_lid, book=code, seq=seq, capital_c=cap_c, capital_request_id=f"init:capital:{new_lid}",
              name=new_row["name"], after=lid)
    ctx.attention("INFO", "lifetimes", "LIFETIME_RESET",
                  f"{lid} closed (final NAV {fmt_money(int(nav.nav_c))}, P&L {fmt_money(int(final_pnl_c))}, {len(cancelled)} order(s) cancelled); "
                  f"{new_lid} opened with USD {fmt_money(cap_c)}",
                  {"ref_type": "lifetime", "ref_id": new_lid, "closed": lid, "auto_resolve": 1})
    # the archived accounts leave the scoped check (module docstring)
    _exclude_from_scoped(ctx, acct_ids)
    closed = history_row(conn, lid)
    return {"closed": closed.to_dict(),
            "opened": {"lifetime_id": new_lid, "book_code": code, "seq": seq, "name": new_row["name"], "accounts": new_accounts, "starting_capital_c": cap_c,
                       "capital_request_id": f"init:capital:{new_lid}", "status": "OPEN"},
            "cancelled_orders": cancelled, "borrows_returned": returned, "integrity_ok": bool(report.ok), "integrity_failing": failing,
            "snapshot": {"snap_date": snap_date.isoformat(), "nav_c": int(nav.nav_c), "quality": nav.quality, "partial": bool(nav.partial), "scopes": snaps},
            "reason": RESET_REASON}


def after_commit(bus, cmd, result) -> None:
    """The on-commit follow-up of ``book.reset_lifetime``: post the new lifetime's capital as its own command
    (``init:capital:{new}``) through ``books.create_lifetime`` — idempotent, also on a replay."""
    if result is None or getattr(cmd, "kind", None) != "book.reset_lifetime" or not result.ok:
        return
    opened = (result.body or {}).get("opened") if isinstance(result.body, dict) else None
    lid = (opened or {}).get("lifetime_id")
    if not lid:
        return
    with bus.db.reader() as conn:
        lrow = lifetime_row(conn, lid)
        book = book_of(conn, lrow["book_code"]) if lrow is not None else None
    if lrow is None or book is None or lrow["status"] != "OPEN":
        return
    try:
        _books.create_lifetime(None, bus, book, int(lrow["seq"]), int(lrow["starting_capital_c"]), lrow["name"])
    except Exception as e:   # noqa: BLE001 — loud: an attention row now, the heal at the next start / replay
        _attention_direct(bus, "ERROR", "LIFETIME_CAPITAL_FAILED", f"{lid}: the initial capital could not be posted ({e}); it is retried at the next start or a replay of the reset",
                          {"lifetime_id": lid, "request_id": f"init:capital:{lid}", "error": str(e)})
        raise


def _attention_direct(bus, severity: str, code: str, message: str, details: dict) -> None:
    try:
        with bus.db.writer(timeout=5.0) as tx:
            tx.x("INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve) VALUES (?,?,?,?,?,?,?,?,?,NULL,0)",
                 (tx.ts, severity, "lifetimes", code, message, dumps(details), None, "lifetime", details.get("lifetime_id")))
    except Exception:   # noqa: BLE001 — never raise over the original error
        if getattr(bus, "log", None):
            bus.log.exception("could not record attention %s", code)


# ---- onboarding -----------------------------------------------------------------------------------------------------------
def agreements_of(conn: sqlite3.Connection, lid: str, broker: Optional[str] = None) -> List[dict]:
    sql = "SELECT id, lifetime_id, broker, kind, signed_date, command_id, note FROM agreements WHERE lifetime_id=?"
    params: list = [lid]
    if broker:
        sql += " AND broker=?"
        params.append(broker)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY broker, id", params).fetchall()]
    for r in rows:
        r["meaning"] = AGREEMENT_MEANING.get(r["kind"], "")
    return rows


def h_onboard_pb(ctx: TxContext, payload: dict) -> dict:
    """``book.onboard_pb {lifetime_id | book_code, broker, pb_financing?, pb_limit_c?}`` (§13.10)."""
    services = _services(ctx)
    conn = ctx.conn
    broker = str(payload.get("broker") or "").strip().upper()
    if broker not in config.PRIME_BROKERS:
        raise RejectError("UNKNOWN_BROKER", f"{broker!r} is not a known prime broker", {"broker": broker, "brokers": sorted(config.PRIME_BROKERS)})
    lid = _resolve_lifetime(conn, payload)
    lrow = lifetime_row(conn, lid)
    if lrow is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid} does not exist")
    if lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {lid} is closed (History is read-only)")
    aid = f"{lid}:PB_{broker}"
    existing = agreements_of(conn, lid, broker)
    acct = account_row(conn, aid)
    if existing and acct is not None:
        return {"lifetime_id": lid, "broker": broker, "agreements": existing, "account_id": aid, "account": _account_view(conn, aid), "created": False,
                "note": "already onboarded: the agreements and the account exist (idempotent per lifetime and broker)"}
    if not existing:
        _books.require_onboard(services, lid)
    signed = ny_of(ctx.ts).date().isoformat()
    have = {r["kind"] for r in existing}
    for kind in AGREEMENT_KINDS:
        if kind in have:
            continue
        conn.execute("INSERT INTO agreements(lifetime_id, broker, kind, signed_date, command_id, note) VALUES (?,?,?,?,?,?)",
                     (lid, broker, kind, signed, ctx.command_id, f"recorded by book.onboard_pb ({config.PRIME_BROKERS[broker]})"))
    opened = None
    if acct is None:
        try:
            from . import prime as _prime
        except ImportError as e:   # pragma: no cover - the module exists in this build
            raise NotBuilt("NOT_BUILT", "prime.py (WP-07) is not in this build: the agreements were not recorded", {"package": "WP-07"}) from e
        opened = _prime.open_pb(ctx, lid, broker, payload.get("pb_financing", 0), payload.get("pb_limit_c", 0), check_eligibility=False, services=services)
    rows = agreements_of(conn, lid, broker)
    ctx.event("PB_ONBOARDED", account_id=aid, ref_type="lifetime", ref_id=lid, broker=broker, agreements=[r["kind"] for r in rows], signed_date=signed,
              account_created=opened is not None)
    out = {"lifetime_id": lid, "broker": broker, "agreements": rows, "account_id": aid, "account": _account_view(conn, aid), "created": True, "signed_date": signed,
           "meaning": {k: AGREEMENT_MEANING[k] for k in AGREEMENT_KINDS}}
    if opened is not None:
        out["schedule"] = opened.get("schedule")
        out["schedule_view"] = opened.get("schedule_view")
    return out


def _account_view(conn: sqlite3.Connection, aid: str) -> Optional[dict]:
    r = account_row(conn, aid)
    return dict(r) if r is not None else None


# ---- settings ----------------------------------------------------------------------------------------------------------------
def _cents(key: str, value) -> int:
    if isinstance(value, bool) or value is None:
        raise RejectError("SETTING_VALUE", f"{key}: {value!r} is not an amount")
    try:
        v = int(value) if isinstance(value, int) else to_c(str(value))
    except (ValueError, ArithmeticError, TypeError) as e:
        raise RejectError("SETTING_VALUE", f"{key}: {value!r} is not an amount") from e
    if v < 0:
        raise RejectError("SETTING_VALUE", f"{key} must be >= 0")
    return v


def h_update_settings(ctx: TxContext, payload: dict) -> dict:
    """``book.update_settings {pb_otc_threshold_c | pb_otc_threshold?, qp_threshold_c | qp_threshold?, book_defaults?}`` (light)."""
    conn = ctx.conn
    before: Dict[str, object] = {}
    after: Dict[str, object] = {}
    if payload.get("pb_otc_threshold_c") is not None or payload.get("pb_otc_threshold") is not None:
        v = _cents("pb_otc_threshold_c", payload.get("pb_otc_threshold_c", payload.get("pb_otc_threshold")))
        before["pb_otc_threshold_c"] = get_setting(conn, "pb_otc_threshold_c", config.PB_OTC_THRESHOLD_C)
        set_setting(conn, "pb_otc_threshold_c", v)
        after["pb_otc_threshold_c"] = v
    qp = payload.get("qp_threshold_c", payload.get("qp_threshold"))
    if qp is not None:
        if not isinstance(qp, dict):
            raise RejectError("SETTING_VALUE", "qp_threshold_c must be a map {profile: amount}")
        cur = dict(get_setting(conn, "qp_threshold_c", config.QP_THRESHOLD_C) or config.QP_THRESHOLD_C)
        before["qp_threshold_c"] = dict(cur)
        for profile, value in qp.items():
            p = str(profile).upper()
            if p not in PROFILES:
                raise RejectError("SETTING_VALUE", f"qp_threshold_c: unknown profile {profile!r}", {"profiles": list(PROFILES)})
            cur[p] = _cents(f"qp_threshold_c[{p}]", value)
        set_setting(conn, "qp_threshold_c", cur)
        after["qp_threshold_c"] = cur
    bd = payload.get("book_defaults")
    if bd is not None:
        if not isinstance(bd, dict):
            raise RejectError("SETTING_VALUE", "book_defaults must be a map {book_code: {name?, starting_capital_c?}}")
        cur = dict(get_setting(conn, "book_defaults", {}) or {})
        before["book_defaults"] = {k: dict(v) for k, v in cur.items() if isinstance(v, dict)}
        names_before, names_after = {}, {}
        for code, d in bd.items():
            code = str(code).upper()
            book = book_of(conn, code)
            if book is None:
                raise RejectError("BOOK_UNKNOWN", f"book {code!r} does not exist")
            if not isinstance(d, dict):
                raise RejectError("SETTING_VALUE", f"book_defaults[{code}] must be a map")
            row = dict(cur.get(code) or {})
            if d.get("name") is not None:
                nm = str(d["name"]).strip()
                if not nm:
                    raise RejectError("SETTING_VALUE", f"book_defaults[{code}].name is empty")
                row["name"] = nm
                if nm != book.name:
                    names_before[code] = book.name
                    names_after[code] = nm
                    conn.execute("UPDATE books SET name=? WHERE book_code=?", (nm, code))
            cap = d.get("starting_capital_c", d.get("starting_capital"))
            if cap is not None:
                v = _cents(f"book_defaults[{code}].starting_capital_c", cap)
                if v <= 0:
                    raise RejectError("AMOUNT", f"book_defaults[{code}].starting_capital_c must be positive")
                row["starting_capital_c"] = v
            cur[code] = row
        set_setting(conn, "book_defaults", cur)
        after["book_defaults"] = cur
        if names_after:
            before["book_names"] = names_before
            after["book_names"] = names_after
    if not after:
        raise RejectError("NOTHING_TO_UPDATE", "no setting given (pb_otc_threshold_c, qp_threshold_c, book_defaults)")
    ctx.event("SETTINGS_CHANGED", ref_type="settings", ref_id=",".join(sorted(k for k in after if k != "book_names")), before=before, after=after)
    return {"before": before, "after": after, "settings": settings_view(conn)}


def settings_view(conn: sqlite3.Connection) -> dict:
    return {"pb_otc_threshold_c": int(get_setting(conn, "pb_otc_threshold_c", config.PB_OTC_THRESHOLD_C) or 0),
            "qp_threshold_c": get_setting(conn, "qp_threshold_c", config.QP_THRESHOLD_C) or dict(config.QP_THRESHOLD_C),
            "book_defaults": get_setting(conn, "book_defaults", {}) or {},
            "books": [{"book_code": b.code, "name": b.name, "profile": b.profile, "starting_capital_c": b.starting_capital_c} for b in _books.books(conn)]}


# ---- history read model ---------------------------------------------------------------------------------------------------
def history_row(conn: sqlite3.Connection, lid: str) -> LifetimeView:
    """The ``LifetimeView`` of a lifetime from an open connection (raises ``RejectError LIFETIME_UNKNOWN``)."""
    r = lifetime_row(conn, lid)
    if r is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid} does not exist")
    r = dict(r)
    ok, failing = _integrity_ok(r.get("integrity_json"))
    deposits_c, withdrawals_c = capital_flows_c(conn, lid)
    accounts = [{"account_id": a["account_id"], "role": a["role"], "name": a["name"], "kind": a["kind"], "broker": a["broker"], "status": a["status"],
                 "opened_ts": int(a["opened_ts"]), "closed_ts": a["closed_ts"]} for a in _accounts_of(conn, lid)]
    n_cancel = int(conn.execute("SELECT COUNT(*) FROM orders o JOIN accounts a ON a.account_id=o.account_id WHERE a.lifetime_id=? AND o.status='CANCELLED' AND o.reason=?",
                                (lid, RESET_REASON)).fetchone()[0])
    snap = conn.execute("SELECT MAX(snap_date) d FROM nav_snapshots WHERE account_id=?", (f"{lid}:ALL",)).fetchone()
    ended_ts = int(r["ended_ts"]) if r.get("ended_ts") is not None else None
    links = {"portfolio": f"#/h/{lid}/portfolio", "accounting": f"#/h/{lid}/accounting", "activity": f"#/h/{lid}/activity",
             "api": {p.replace("/", "_").replace(".csv", "").replace("-", "_"): f"/api/history/{lid}/{p}" for p in
                     ("portfolio", "positions", "accounting/balance-sheet", "accounting/pnl", "accounting/trial-balance", "accounting/journal", "accounting/integrity",
                      "activity", "export/transactions.csv", "export/positions.csv")}}
    return LifetimeView(lid, r["book_code"], int(r["seq"]), r["name"], r["status"], int(r["started_ts"]), ny_of(int(r["started_ts"])).date().isoformat(),
                        ended_ts, ny_of(ended_ts).date().isoformat() if ended_ts is not None else None, int(r["starting_capital_c"]),
                        r.get("final_nav_c"), r.get("final_realised_c"), r.get("final_pnl_c"), r.get("final_quality"), r.get("reason"), ok, failing,
                        deposits_c, withdrawals_c, accounts, agreements_of(conn, lid), n_cancel, snap["d"] if snap and snap["d"] else None,
                        r["status"] == "CLOSED", links)


def history(services, lifetime_id: str) -> LifetimeView:
    """§2.12: the row + final figures + links to the read-only pages of ``lifetime_id``."""
    with services.db.reader() as conn:
        return history_row(conn, lifetime_id)


def lifetimes_of(services, book_code: str) -> List[LifetimeView]:
    """Every lifetime of the book, newest first (the OPEN one first when present)."""
    with services.db.reader() as conn:
        if book_of(conn, book_code) is None:
            raise RejectError("BOOK_UNKNOWN", f"book {book_code!r} does not exist")
        ids = [r["lifetime_id"] for r in conn.execute("SELECT lifetime_id FROM lifetimes WHERE book_code=? ORDER BY seq DESC", (book_code,)).fetchall()]
        return [history_row(conn, lid) for lid in ids]


# ---- registration -----------------------------------------------------------------------------------------------------------
def register(bus, services=None) -> None:
    bus.register("book.reset_lifetime", h_reset_lifetime)
    bus.register("book.onboard_pb", h_onboard_pb)
    bus.register("book.update_settings", h_update_settings, light=True)
    if bus not in _LISTENING:
        bus.on_commit(lambda cmd, res: after_commit(bus, cmd, res))
        _LISTENING.add(bus)
    if services is not None and getattr(services, "lifetimes", None) is None:
        services.lifetimes = sys.modules[__name__]


def bind(services) -> None:
    services.lifetimes = sys.modules[__name__]


__all__ = ["LifetimeView", "RESET_REASON", "AGREEMENT_KINDS", "AGREEMENT_MEANING", "h_reset_lifetime", "h_onboard_pb", "h_update_settings", "after_commit",
           "history", "history_row", "lifetimes_of", "agreements_of", "realised_itd_c", "capital_flows_c", "cancel_open_orders", "release_unused_borrows",
           "write_final_snapshots", "settings_view", "register", "bind"]
