"""Books, lifetimes, account ids, eligibility (TERMINAL_DESIGN.md §2.12 books.py, §13.10).

Conventions (sentences BOOKS, LIFETIME, ELIGIBILITY, QP)
- Three books on first run: Personal (P, INDIVIDUAL, USD 1,000,000), Fund (F, FUND, USD 25,000,000), Institutional
  (I, INSTITUTION, USD 1,000,000,000).  A lifetime is one run of a book (``P1``, ``P2`` …); every account id is
  ``{lifetime_id}:{role}`` so every ledger row belongs to exactly one lifetime through its account.  Books never
  transfer to each other (``treasury.transfer`` across lifetimes -> 400 CROSS_BOOK).
- Capital enters a lifetime once, through ``treasury.init_capital`` with request_id ``init:capital:{lifetime_id}``,
  skipped when that row already exists; the catalogue accounts open empty.
- Eligibility (design §13.10; BM-03 / SR-04 / PO-12, 2026-10-21): ``ecp = nav ≥ ECP_FLOOR_C`` (CEA 1a(18)); ``pb_otc``
  = the INSTITUTION profile, or the FUND profile when the lifetime STARTED at or above ``settings.pb_otc_threshold_c``
  (eligible from day one: a fee or a loss never takes that away; a Fund lifetime reset below the threshold is judged by
  NAV like the Personal book), or NAV ≥ the threshold, or relationships already recorded (``agreements``) — those
  survive a later NAV fall; a NEW relationship (``can_onboard``) needs the profile rule or the threshold; an OTC booking
  (``require_pb_otc``) needs ``pb_otc`` and the ECP floor.  ``private_markets`` = the QP threshold is 0 (INSTITUTION),
  or the FUND profile started at or above its QP threshold (qualified from day one), or NAV ≥
  ``settings.qp_threshold_c[profile]``, or the lifetime's PRIVATE account already exists ("the account then stays":
  a later NAV fall never locks it); ``unlock_private`` creates ``{lifetime}:PRIVATE`` the first time it is true.  The QP
  gate is for opening positions: ``require_private(..., side="SELL")`` never refuses a sale (SR-01).
- ``eligibility_notices(services)`` (the Supervisor's 30 s loop, SR-09): an INFO attention ELIGIBILITY_BELOW_THRESHOLD
  while a lifetime with relationships is below the threshold (new relationships refused), resolved when NAV recovers.
- Treasury reserve per book (BM-01): ``settings.treasury_reserve_by_book {book_code: cents}``; ``treasury_reserve_c(conn,
  lifetime_id)`` is what the accounting and the sweep read (the old global ``treasury_reserve_c`` only until the
  per-book map exists; ``migrate_treasury_reserve`` copies it into every book once, with a SETTINGS_CHANGED event).
- The tradeability "account kind" of §7.2 is the role for catalogue accounts (US_EXCHANGE, DEBT, CREDIT,
  FOREIGN_FX, PRIVATE), ``PB`` for prime-broker accounts and ``TREASURY`` for the Treasury (``account_kind_for``).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import config
from .commands import Command, RejectError, TxContext
from .db import get_setting
from .units import fmt_money

_ACCOUNT_ID_RE = re.compile(config.ACCOUNT_ID_RE)


@dataclass(frozen=True)
class Book:
    code: str
    name: str
    profile: str                 # INDIVIDUAL | FUND | INSTITUTION
    starting_capital_c: int


@dataclass(frozen=True)
class Eligibility:
    lifetime_id: str
    nav_c: int
    pb_otc: bool
    pb_otc_reason: str
    private_markets: bool
    private_reason: str
    ecp: bool
    threshold_c: int
    qp_threshold_c: int
    has_relationships: bool
    can_onboard: bool = False
    profile: str = ""
    by_profile: bool = False             # PB / OTC by the profile rule (INSTITUTION; FUND that started at the threshold)
    private_open: bool = False           # the lifetime's PRIVATE account exists (it stays open)
    starting_capital_c: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ---- ids ---------------------------------------------------------------------------------------------------------
def lifetime_id(code: str, seq: int) -> str:
    """``("P", 1) -> "P1"``."""
    return f"{code}{int(seq)}"


def account_id(lifetime_id: str, role: str) -> str:
    """``("P1", "US_EXCHANGE") -> "P1:US_EXCHANGE"``."""
    return f"{lifetime_id}:{role}"


def split_account_id(account_id: str) -> Tuple[str, str]:
    """``"P1:US_EXCHANGE" -> ("P1", "US_EXCHANGE")``; ValueError when malformed."""
    if not _ACCOUNT_ID_RE.match(account_id or ""):
        raise ValueError(f"malformed account id {account_id!r} (expected '{{lifetime}}:{{ROLE}}')")
    lid, role = account_id.split(":", 1)
    return lid, role


def book_code_of(lifetime_id: str) -> str:
    return re.match(r"^([A-Z]+)", lifetime_id).group(1)


def account_kind_for(row) -> str:
    """The §7.2 tradeability kind of an accounts row (dict or sqlite3.Row)."""
    kind = row["kind"]
    if kind == "TREASURY":
        return "TREASURY"
    if kind == "PB":
        return "PB"
    return row["role"]


# ---- reads ---------------------------------------------------------------------------------------------------------
def books(conn: sqlite3.Connection) -> List[Book]:
    rows = conn.execute("SELECT book_code, name, profile, starting_capital_c FROM books ORDER BY sort_order, book_code").fetchall()
    return [Book(r["book_code"], r["name"], r["profile"], int(r["starting_capital_c"])) for r in rows]


def book_of(conn: sqlite3.Connection, book_code: str) -> Optional[Book]:
    r = conn.execute("SELECT book_code, name, profile, starting_capital_c FROM books WHERE book_code=?", (book_code,)).fetchone()
    return Book(r["book_code"], r["name"], r["profile"], int(r["starting_capital_c"])) if r else None


def lifetime_row(conn: sqlite3.Connection, lifetime_id: str):
    return conn.execute("SELECT * FROM lifetimes WHERE lifetime_id=?", (lifetime_id,)).fetchone()


def open_lifetime(conn: sqlite3.Connection, book_code: str):
    return conn.execute("SELECT * FROM lifetimes WHERE book_code=? AND status='OPEN' ORDER BY seq DESC LIMIT 1", (book_code,)).fetchone()


def open_lifetimes(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute("SELECT l.lifetime_id FROM lifetimes l JOIN books b ON b.book_code=l.book_code WHERE l.status='OPEN' ORDER BY b.sort_order, l.seq").fetchall()
    return [r["lifetime_id"] for r in rows]


def next_seq(conn: sqlite3.Connection, book_code: str) -> int:
    r = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM lifetimes WHERE book_code=?", (book_code,)).fetchone()
    return int(r[0] or 0) + 1


def account_row(conn: sqlite3.Connection, account_id: str):
    return conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()


def profile_of(conn: sqlite3.Connection, lifetime_id: str) -> str:
    r = conn.execute("SELECT b.profile FROM lifetimes l JOIN books b ON b.book_code=l.book_code WHERE l.lifetime_id=?", (lifetime_id,)).fetchone()
    if r is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lifetime_id} does not exist")
    return r["profile"]


def qp_threshold_c(conn: sqlite3.Connection, profile: str) -> int:
    qp = get_setting(conn, "qp_threshold_c", None) or {}
    v = qp.get(profile) if isinstance(qp, dict) else None
    return int(v if v is not None else config.QP_THRESHOLD_C.get(profile, 0))


def pb_otc_threshold_c(conn: sqlite3.Connection) -> int:
    return int(get_setting(conn, "pb_otc_threshold_c", config.PB_OTC_THRESHOLD_C) or 0)


def treasury_reserve_c(conn: sqlite3.Connection, lifetime_id: str, currency: str = "USD") -> int:
    """BM-01: the Treasury reserve of the lifetime's book (USD only; other currencies 0): ``treasury_reserve_by_book[book]``,
    or, until that map exists (a database not yet migrated), the old global ``treasury_reserve_c``."""
    if str(currency or "USD").upper() != "USD":
        return 0
    by_book = get_setting(conn, "treasury_reserve_by_book", None)
    if isinstance(by_book, dict):
        try:
            return max(0, int(by_book.get(book_code_of(lifetime_id), 0) or 0))
        except (TypeError, ValueError):
            return 0
    try:
        return max(0, int(get_setting(conn, "treasury_reserve_c", 0) or 0))
    except (TypeError, ValueError):
        return 0


def migrate_treasury_reserve(services) -> bool:
    """BM-01, once per database: a non-zero old global ``treasury_reserve_c`` becomes every book's reserve in
    ``treasury_reserve_by_book`` through ``settings.set`` (SETTINGS_CHANGED with before / after; request_id
    ``migrate:treasury_reserve_by_book``, so a restart replays instead of repeating it).  Returns True when it migrated."""
    with services.db.reader() as conn:
        if get_setting(conn, "treasury_reserve_by_book", None) is not None:
            return False
        old = int(get_setting(conn, "treasury_reserve_c", 0) or 0)
        codes = [b.code for b in books(conn)]
    if old <= 0 or not codes or "settings.set" not in services.bus.kinds():
        return False
    from .units import from_c
    res = services.bus.execute(Command("settings.set", {"treasury_reserve_by_book": {c: str(from_c(old)) for c in codes}},   # USD strings, as the API takes amounts
                                       request_id="migrate:treasury_reserve_by_book", actor="init"))
    return bool(res.ok)


# ---- lifetime creation -------------------------------------------------------------------------------------------
def default_book_name(conn: sqlite3.Connection, book: Book) -> str:
    """The name a NEW lifetime of the book is called after (SR-07): ``settings.book_defaults[code].name`` (what Settings >
    Books saved), else the book's name."""
    defaults = get_setting(conn, "book_defaults", {}) or {}
    d = defaults.get(book.code) if isinstance(defaults, dict) else None
    nm = str(d.get("name") or "").strip() if isinstance(d, dict) else ""
    return nm or book.name


def _insert_lifetime_and_accounts(conn: sqlite3.Connection, ts: int, book: Book, seq: int, capital_c: int, name: Optional[str]) -> str:
    lid = lifetime_id(book.code, seq)
    conn.execute("INSERT INTO lifetimes(lifetime_id, book_code, seq, name, started_ts, ended_ts, starting_capital_c, status) VALUES (?,?,?,?,?,NULL,?,'OPEN')",
                 (lid, book.code, int(seq), name or f"{default_book_name(conn, book)} #{seq}", ts, int(capital_c)))
    private_now = qp_threshold_c(conn, book.profile) == 0
    for role, aname, kind, sort in config.ACCOUNT_CATALOGUE:
        if role == "PRIVATE" and not private_now:
            continue
        _insert_account(conn, ts, book.code, lid, role, aname, kind, sort)
    return lid


def _insert_account(conn: sqlite3.Connection, ts: int, book_code: str, lid: str, role: str, name: str, kind: str, sort: int, broker=None) -> str:
    aid = account_id(lid, role)
    conn.execute("INSERT OR IGNORE INTO accounts(account_id, book_code, lifetime_id, role, name, kind, broker, fee_schedule, status, sweep_out, sweep_buffer_c,"
                 " pb_financing, pb_limit_c, sort_order, opened_ts, closed_ts, settings_json) VALUES (?,?,?,?,?,?,?,?,'OPEN',1,?,0,0,?,?,NULL,'{}')",
                 (aid, book_code, lid, role, name, kind, broker, "DEFAULT" if kind != "PB" else (broker or "DEFAULT"), config.SWEEP_BUFFER_C, sort, ts))
    return aid


def create_lifetime(tx, bus_or_ctx, book: Book, seq: int, capital_c: int, name: Optional[str] = None) -> str:
    """Insert the lifetimes row and the catalogue accounts (PRIVATE only when the profile's QP threshold is 0), then
    post the initial capital through ``treasury.init_capital`` with request_id ``init:capital:{lifetime_id}``
    (skipped when that command already exists).

    Two modes: inside a command (``bus_or_ctx`` is a ``TxContext``: rows and the capital share the caller's
    transaction, ``tx`` may be None) or stand-alone (``bus_or_ctx`` is a ``CommandBus`` and ``tx`` is None: the rows
    commit in their own writer transaction, then the init command runs through the bus — idempotent on re-run)."""
    if isinstance(bus_or_ctx, TxContext):
        ctx = bus_or_ctx
        lid = _insert_lifetime_and_accounts(ctx.conn, ctx.ts, book, seq, capital_c, name)
        from . import treasury
        treasury.post_init_capital(ctx, lid, int(capital_c), "USD", "initial capital")
        ctx.event("LIFETIME_OPENED", account_id=account_id(lid, "TREASURY"), ref_type="lifetime", ref_id=lid, book=book.code, seq=seq, capital_c=int(capital_c))
        return lid
    bus = bus_or_ctx
    if tx is not None:
        raise ValueError("create_lifetime with a CommandBus manages its own transaction: pass tx=None")
    db = bus.db
    lid = lifetime_id(book.code, seq)
    with db.writer() as wtx:
        if lifetime_row(wtx.conn, lid) is None:
            _insert_lifetime_and_accounts(wtx.conn, wtx.ts, book, seq, capital_c, name)
    rid = f"init:capital:{lid}"
    with db.reader() as conn:
        exists = conn.execute("SELECT 1 FROM commands WHERE request_id=?", (rid,)).fetchone() is not None
    if not exists:
        res = bus.execute(Command("treasury.init_capital", {"lifetime_id": lid, "amount_c": int(capital_c), "currency": "USD", "source": "initial capital"},
                                  request_id=rid, actor="init"))
        if not res.ok:
            raise RuntimeError(f"initial capital for {lid} failed: {res.body}")
    return lid


def ensure_first_run(services) -> List[str]:
    """Create a lifetime for every book without an OPEN one (server start and ``make_runtime``).  Returns the new ids.

    Self-healing: the stand-alone ``create_lifetime`` commits the lifetime row and posts the capital in two
    transactions, so a crash between them leaves an OPEN lifetime with no capital.  Every start therefore also posts
    ``init:capital:{lid}`` for any OPEN lifetime whose init command is missing (idempotent on the request_id and the
    ``capital:initial:{lid}`` idem key; a lifetime that already has its capital is untouched)."""
    created: List[str] = []
    with services.db.reader() as conn:
        rows = conn.execute("SELECT b.book_code, b.name, b.profile, b.starting_capital_c FROM books b ORDER BY b.sort_order").fetchall()
        todo = []
        defaults = get_setting(conn, "book_defaults", {}) or {}
        for r in rows:
            if open_lifetime(conn, r["book_code"]) is None:
                d = defaults.get(r["book_code"]) if isinstance(defaults, dict) else None
                cap = int(d.get("starting_capital_c", r["starting_capital_c"])) if isinstance(d, dict) else int(r["starting_capital_c"])
                todo.append((Book(r["book_code"], r["name"], r["profile"], int(r["starting_capital_c"])), next_seq(conn, r["book_code"]), cap))
        heal = [(l["lifetime_id"], int(l["starting_capital_c"])) for l in conn.execute(
            "SELECT l.lifetime_id, l.starting_capital_c FROM lifetimes l WHERE l.status='OPEN'"
            " AND NOT EXISTS (SELECT 1 FROM commands c WHERE c.request_id = 'init:capital:' || l.lifetime_id)").fetchall()]
    for book, seq, cap in todo:
        created.append(create_lifetime(None, services.bus, book, seq, cap))
    for lid, cap in heal:
        res = services.bus.execute(Command("treasury.init_capital", {"lifetime_id": lid, "amount_c": cap, "currency": "USD", "source": "initial capital"},
                                           request_id=f"init:capital:{lid}", actor="init"))
        if not res.ok:
            raise RuntimeError(f"initial capital for {lid} failed: {res.body}")
    return created


# ---- eligibility -----------------------------------------------------------------------------------------------------
def eligibility(services, lifetime_id: str, nav_c: Optional[int] = None) -> Eligibility:
    with services.db.reader() as conn:
        lrow = lifetime_row(conn, lifetime_id)
        if lrow is None:
            raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lifetime_id} does not exist")
        profile = profile_of(conn, lifetime_id)
        threshold = pb_otc_threshold_c(conn)
        qp = qp_threshold_c(conn, profile)
        has_rel = conn.execute("SELECT 1 FROM agreements WHERE lifetime_id=? LIMIT 1", (lifetime_id,)).fetchone() is not None
        private_open = account_row(conn, account_id(lifetime_id, "PRIVATE")) is not None
        start_c = int(lrow["starting_capital_c"] or 0)
    if nav_c is None:
        nav_c = int(services.accounting.nav(f"{lifetime_id}:ALL").nav_c)
    ecp = nav_c >= config.ECP_FLOOR_C
    # design §13.10: the Institutional profile always; the Fund profile from day one when its lifetime started at or above the threshold
    by_profile = profile == "INSTITUTION" or (profile == "FUND" and start_c >= threshold)
    by_threshold = by_profile or nav_c >= threshold
    pb_otc = by_threshold or has_rel
    nav_s, thr_s, ecp_s = fmt_money(nav_c), fmt_money(threshold), fmt_money(config.ECP_FLOOR_C)
    if pb_otc and not ecp:
        pb_reason = f"NAV {nav_s} is below the ECP floor {ecp_s}: OTC bookings are refused until it recovers (relationships stay open)"
    elif by_profile:
        pb_reason = ("institutional profile" if profile == "INSTITUTION"
                     else f"Fund profile: eligible for prime brokers and OTC from day one (started with {fmt_money(start_c)}, threshold {thr_s})")
    elif by_threshold:
        pb_reason = f"NAV {nav_s} meets the PB/OTC threshold {thr_s}"
    elif has_rel:
        pb_reason = f"NAV {nav_s} is below the PB/OTC threshold {thr_s}; existing relationships stay open (new ones need the threshold)"
    elif not ecp:
        pb_reason = f"NAV {nav_s} is below the ECP floor {ecp_s} and the PB/OTC threshold {thr_s}"
    else:
        pb_reason = f"NAV {nav_s} is below the PB/OTC threshold {thr_s}"
    fund_qp = profile == "FUND" and qp > 0 and start_c >= qp
    by_nav_qp = nav_c >= qp
    private = qp == 0 or fund_qp or by_nav_qp or private_open
    if qp == 0:
        private_reason = "institutional profile: Private Markets open from the start"
    elif fund_qp:
        private_reason = f"Fund profile: qualified purchaser from day one (started with {fmt_money(start_c)}, threshold {fmt_money(qp)})"
    elif by_nav_qp:
        private_reason = f"NAV {nav_s} meets the qualified-purchaser threshold {fmt_money(qp)}"
    elif private_open:
        private_reason = f"Private Markets stays open once unlocked (NAV now {nav_s}; the {fmt_money(qp)} threshold only gates the first opening)"
    else:
        private_reason = f"Private Markets unlock at {fmt_money(qp)} NAV (now {nav_s})"
    return Eligibility(lifetime_id, nav_c, pb_otc, pb_reason, private, private_reason, ecp, threshold, qp, has_rel, by_threshold, profile,
                       by_profile, private_open, start_c)


def _lifetime_open(conn: sqlite3.Connection, lid: str) -> None:
    row = lifetime_row(conn, lid)
    if row is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid} does not exist")
    if row["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {lid} is closed (History is read-only)")


def require_pb_otc(services, account_id: str) -> None:
    """Raise ``RejectError NOT_ELIGIBLE`` (plain-English reason with the NAV, the threshold, the ECP floor and the
    ELIGIBILITY sentence) unless the account's lifetime may book OTC."""
    lid, _role = split_account_id(account_id)
    with services.db.reader() as conn:
        _lifetime_open(conn, lid)
    e = eligibility(services, lid)
    if not (e.pb_otc and e.ecp):
        raise RejectError("NOT_ELIGIBLE", f"{e.pb_otc_reason}. " + _sentence("ELIGIBILITY"),
                          {"lifetime_id": lid, "nav_c": e.nav_c, "threshold_c": e.threshold_c, "ecp_floor_c": config.ECP_FLOOR_C, "ecp": e.ecp})


def require_onboard(services, lifetime_id: str) -> None:
    """A NEW prime-broker relationship needs the threshold (or the INSTITUTION profile) — ``NOT_ELIGIBLE`` otherwise."""
    with services.db.reader() as conn:
        _lifetime_open(conn, lifetime_id)
    e = eligibility(services, lifetime_id)
    if not e.can_onboard:
        raise RejectError("NOT_ELIGIBLE", f"{e.pb_otc_reason}. " + _sentence("ELIGIBILITY"),
                          {"lifetime_id": lifetime_id, "nav_c": e.nav_c, "threshold_c": e.threshold_c, "ecp_floor_c": config.ECP_FLOOR_C})


def require_private(services, lifetime_id: str, side: Optional[str] = None) -> None:
    """Raise ``RejectError NOT_ELIGIBLE_QP`` (sentence QP) unless the lifetime passes its qualified-purchaser gate (SR-01:
    a lifetime whose Private Markets account exists stays unlocked; the gate is for buys / rounds, so ``side="SELL"``
    only checks that the lifetime is open)."""
    with services.db.reader() as conn:
        _lifetime_open(conn, lifetime_id)
    if str(side or "").upper() == "SELL":
        return
    e = eligibility(services, lifetime_id)
    if not e.private_markets:
        raise RejectError("NOT_ELIGIBLE_QP", f"{e.private_reason}. " + _sentence("QP"),
                          {"lifetime_id": lifetime_id, "nav_c": e.nav_c, "qp_threshold_c": e.qp_threshold_c})


def _sentence(key: str) -> str:
    from .tables import CONVENTIONS
    return CONVENTIONS.get(key, "")


def unlock_private(services, lifetime_id: Optional[str] = None) -> List[str]:
    """Create ``{lifetime}:PRIVATE`` for every OPEN lifetime (or the given one) that passes the QP gate and has no
    PRIVATE account yet (light command ``book.unlock_private``, request_id ``unlock:private:{lifetime}``, INFO
    attention).  Returns the account ids created."""
    with services.db.reader() as conn:
        lids = [lifetime_id] if lifetime_id else open_lifetimes(conn)
        todo = [lid for lid in lids if account_row(conn, account_id(lid, "PRIVATE")) is None
                and (lifetime_row(conn, lid) or {"status": "CLOSED"})["status"] == "OPEN"]
    created: List[str] = []
    for lid in todo:
        e = eligibility(services, lid)
        if not e.private_markets:
            continue
        res = services.bus.execute(Command("book.unlock_private", {"lifetime_id": lid, "nav_c": e.nav_c, "reason": e.private_reason},
                                           request_id=f"unlock:private:{lid}", actor="lifecycle"))
        if res.ok and not res.replayed:
            created.append(account_id(lid, "PRIVATE"))
    return created


ELIGIBILITY_NOTICE = "ELIGIBILITY_BELOW_THRESHOLD"


def eligibility_notices(services) -> dict:
    """SR-09 (design §13.10: "a NAV fall below the threshold produces an INFO attention ELIGIBILITY_BELOW_THRESHOLD, nothing
    closes"): for every OPEN lifetime with prime-broker relationships whose new relationships are refused (``can_onboard``
    false), an INFO attention row (ref lifetime, auto-resolve); resolved once ``can_onboard`` is true again.  Light: NAV is
    computed only for lifetimes with relationships that are judged by NAV (not by profile).  Run by the Supervisor's 30 s
    loop.  Returns ``{"opened": [...], "resolved": [...]}``."""
    out = {"opened": [], "resolved": []}
    with services.db.reader() as conn:
        threshold = pb_otc_threshold_c(conn)
        rel = {}
        for r in conn.execute("SELECT DISTINCT a.lifetime_id, b.profile, l.starting_capital_c FROM agreements a JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                              " JOIN books b ON b.book_code=l.book_code WHERE l.status='OPEN' ORDER BY a.lifetime_id").fetchall():
            # eligible by profile (design 13.10): new relationships are never refused, so no NAV is needed for them
            rel[r["lifetime_id"]] = r["profile"] == "INSTITUTION" or (r["profile"] == "FUND" and int(r["starting_capital_c"] or 0) >= threshold)
        open_rows = {}
        for r in conn.execute("SELECT id, ref_id FROM attention WHERE resolved_ts IS NULL AND code=?", (ELIGIBILITY_NOTICE,)).fetchall():
            open_rows.setdefault(r["ref_id"], []).append(int(r["id"]))
    kinds = services.bus.kinds()
    now = int(services.clock.epoch())
    for lid, by_profile in rel.items():
        e = None if by_profile else eligibility(services, lid)
        below = e is not None and e.has_relationships and not e.can_onboard
        if below and lid not in open_rows and "attention.open" in kinds:
            msg = (f"{lid}: NAV {fmt_money(e.nav_c)} is below the PB/OTC threshold {fmt_money(e.threshold_c)}: existing prime-broker relationships stay open, "
                   f"new ones are refused until NAV recovers" + ("" if e.ecp else f"; below the ECP floor {fmt_money(config.ECP_FLOOR_C)} OTC bookings are refused too"))
            res = services.bus.execute(Command("attention.open", {"severity": "INFO", "source": "books", "code": ELIGIBILITY_NOTICE, "message": msg,
                                                                  "details": {"ref_type": "lifetime", "ref_id": lid, "lifetime_id": lid, "nav_c": e.nav_c,
                                                                              "threshold_c": e.threshold_c, "ecp": e.ecp, "auto_resolve": 1}},
                                               request_id=f"elig:below:{lid}:{now}", actor="supervisor"))
            if res.ok:
                out["opened"].append(lid)
        elif not below and lid in open_rows and "attention.resolve" in kinds:
            for aid in open_rows[lid]:
                res = services.bus.execute(Command("attention.resolve", {"id": aid}, request_id=f"elig:recovered:{lid}:{aid}", actor="supervisor"))
                if res.ok:
                    out["resolved"].append(lid)
    # a lifetime that was closed (reset) or has no relationships any more: its open notice goes too
    for lid, ids in open_rows.items():
        if lid in rel or "attention.resolve" not in kinds:
            continue
        for aid in ids:
            res = services.bus.execute(Command("attention.resolve", {"id": aid}, request_id=f"elig:gone:{lid}:{aid}", actor="supervisor"))
            if res.ok:
                out["resolved"].append(lid)
    return out


# ---- handlers --------------------------------------------------------------------------------------------------------
def h_unlock_private(ctx: TxContext, payload: dict) -> dict:
    lid = payload.get("lifetime_id") or ""
    _lifetime_open(ctx.conn, lid)
    lrow = lifetime_row(ctx.conn, lid)
    aid = account_id(lid, "PRIVATE")
    if account_row(ctx.conn, aid) is not None:
        return {"account_id": aid, "created": False}
    _insert_account(ctx.conn, ctx.ts, lrow["book_code"], lid, "PRIVATE", "Private Markets", "TRADING", 50)
    ctx.event("ACCOUNT_OPENED", account_id=aid, ref_type="lifetime", ref_id=lid, role="PRIVATE", reason=payload.get("reason"))
    ctx.attention("INFO", "books", "PRIVATE_MARKETS_UNLOCKED", f"{lid}: Private Markets account opened ({payload.get('reason') or 'qualified-purchaser threshold met'})",
                  {"account_id": aid, "lifetime_id": lid, "nav_c": payload.get("nav_c"), "auto_resolve": 1})
    return {"account_id": aid, "created": True}


def register(bus) -> None:
    bus.register("book.unlock_private", h_unlock_private, light=True)
