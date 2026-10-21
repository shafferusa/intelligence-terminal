"""Test fixture for the FinSim Live Terminal (TERMINAL_DESIGN.md §19, TERMINAL_WORKPLAN.md WP-00).

``make_runtime(tmpdir, at="2026-10-20T14:00:00-04:00", offline=True, books="test")`` wires a ``FakeClock``, the real
``TradingCalendar`` (the repo's ``data/nyse-holidays.json``), ``SessionTable``, ``Database`` (a temp file; ``FINSIM_HOME``
and ``FINSIM_TERMINAL_DB`` are pointed at ``tmpdir`` so nothing touches ``~/.finsim``), ``InstrumentMaster`` (seeded from
``universe.json``), ``FixedMarks`` as the mark provider, the real ``CommandBus`` with the core handlers, ``Accounting``
and the books model.  ``books="test"`` creates the single Test Book lifetime ``T1`` with USD 10,000,000 so the §19
scenarios keep their numbers; ``books="real"`` creates P1 / F1 / I1 (§19.14).  Nothing here touches the network.

The ``T1`` aliases (``TREASURY``, ``US_EXCHANGE``, ``DEBT``, ``CREDIT``, ``FOREIGN_FX``, ``PRIVATE``, ``pb("GS")``) let
scenario tests read as in §19.  A ``FakeProvider`` placeholder is bound when WP-01's module exists (import guarded).
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from finsim.terminal import accounting as _accounting, books as _books, config, treasury as _treasury   # noqa: E402
from finsim.terminal.calendar import TradingCalendar   # noqa: E402
from finsim.terminal.clock import FakeClock, NY   # noqa: E402
from finsim.terminal.commands import Command, CommandBus, Result   # noqa: E402
from finsim.terminal.db import Database   # noqa: E402
from finsim.terminal.instruments import InstrumentMaster   # noqa: E402
from finsim.terminal.ledger import Entry, balance as _balance, cr, dr, open_lot   # noqa: E402
from finsim.terminal.quotes import FixedMarks   # noqa: E402
from finsim.terminal.sessions import SessionTable   # noqa: E402
from finsim.terminal.units import to_c   # noqa: E402

# ---- the T1 aliases ----------------------------------------------------------------------------------------------------
T1 = "T1"
TREASURY = "T1:TREASURY"
US_EXCHANGE = "T1:US_EXCHANGE"
DEBT = "T1:DEBT"
CREDIT = "T1:CREDIT"
FOREIGN_FX = "T1:FOREIGN_FX"
PRIVATE = "T1:PRIVATE"
AT_DEFAULT = "2026-10-20T14:00:00-04:00"

DEFAULT_MARKS = {"EURUSD": ("1.1238", "delayed"), "GBPUSD": ("1.3394", "delayed"), "USDJPY": ("158.10", "delayed"),
                 "AAPL": ("339.50", "delayed"), "SPY": ("769.32", "delayed")}


def pb(broker: str, lifetime: str = T1) -> str:
    return f"{lifetime}:PB_{broker}"


class _Log:
    """A tiny logger that keeps messages in memory (no handlers, no files)."""

    def __init__(self):
        self.records = []

    def _add(self, level, msg, *args):
        try:
            self.records.append((level, msg % args if args else msg))
        except Exception:   # noqa: BLE001
            self.records.append((level, msg))

    def debug(self, msg, *a, **k):
        self._add("DEBUG", msg, *a)

    def info(self, msg, *a, **k):
        self._add("INFO", msg, *a)

    def warning(self, msg, *a, **k):
        self._add("WARNING", msg, *a)

    def error(self, msg, *a, **k):
        self._add("ERROR", msg, *a)

    def exception(self, msg, *a, **k):
        self._add("EXCEPTION", msg, *a)


class Runtime(types.SimpleNamespace):
    """The services bundle the handlers see (§2.12 ``services``): db, clock, cal, sessions, instruments, marks,
    accounting, bus, books, log, home, db_path (+ optional product modules bound by later packages)."""

    def close(self) -> None:
        self.db.close()


def make_runtime(tmpdir: Optional[str] = None, at: str = AT_DEFAULT, offline: bool = True, books: str = "test", marks: Optional[dict] = None,
                 seed_instruments: bool = True) -> Runtime:
    if not offline:
        raise ValueError("tests never use the network: offline must be True")
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="finsim-terminal-test-")
    os.makedirs(tmpdir, exist_ok=True)
    os.environ[config.HOME_ENV] = tmpdir
    db_path = os.path.join(tmpdir, "terminal.db")
    os.environ[config.DB_ENV] = db_path
    clock = FakeClock(at)
    log = _Log()
    cal = TradingCalendar(config.holidays_path())
    sessions = SessionTable(cal, clock)
    db = Database(db_path, clock, log)
    book_rows = [config.TEST_BOOK] if books == "test" else list(config.BOOKS)
    db.open(books=book_rows)
    fixed = FixedMarks(dict(DEFAULT_MARKS, **(marks or {})), as_of=clock.epoch())
    instruments = InstrumentMaster(db, log)
    if seed_instruments:
        with db.writer() as tx:
            instruments.seed(tx, cal)
    instruments.load()
    rt = Runtime(db=db, clock=clock, cal=cal, sessions=sessions, instruments=instruments, marks=fixed, log=log, home=tmpdir, db_path=db_path,
                 books=_books, offline=True, settings={})
    bus = CommandBus(db, clock, cal, sessions, fixed, log, services=rt)
    acc = _accounting.Accounting(db, instruments, fixed, clock, cal, sessions)
    acc.services = rt
    rt.bus = bus
    rt.accounting = acc
    _treasury.register(bus)
    _books.register(bus)
    bus.register("test.post", _h_test_post)
    bus.register("test.buy", _h_test_buy)
    bus.register("test.sell", _h_test_sell)
    bus.register("test.fail", _h_test_fail)
    try:   # WP-01 replaces this placeholder with the real FakeProvider
        from finsim.terminal.quotes.providers import FakeProvider   # type: ignore
        rt.fp = FakeProvider()
    except Exception:   # noqa: BLE001 — not merged yet
        rt.fp = None
    _books.ensure_first_run(rt)
    if books == "test":
        acc.selected_book = "T"
    return rt


# ---- helpers ----------------------------------------------------------------------------------------------------------
def rid(prefix: str = "ui") -> str:
    return f"{prefix}:{uuid.uuid4()}"


def execute(rt: Runtime, kind: str, payload: dict, request_id: Optional[str] = None, actor: str = "ui") -> Result:
    return rt.bus.execute(Command(kind, payload, request_id or rid(), actor))


def bal(rt: Runtime, account: str, gl: str, ccy: str = "USD", inst: Optional[str] = None) -> int:
    """Natural-sign balance in cents."""
    with rt.db.reader() as conn:
        return _balance(conn, account, gl, ccy, inst)


def fund(rt: Runtime, account: str, amount_c: int, ccy: str = "USD", lifetime: Optional[str] = None) -> Result:
    """Treasury -> account internal transfer."""
    lid = lifetime or account.split(":", 1)[0]
    return execute(rt, "treasury.transfer", {"from_account": f"{lid}:TREASURY", "to_account": account, "currency": ccy, "amount_c": int(amount_c)})


def deposit(rt: Runtime, amount_c: int, ccy: str = "USD", lifetime: str = T1, request_id: Optional[str] = None) -> Result:
    return execute(rt, "treasury.deposit", {"lifetime_id": lifetime, "amount_c": int(amount_c), "currency": ccy}, request_id)


def submit(rt: Runtime, **req) -> Result:
    """``order.submit`` (WP-02); raises when the orders package is not merged."""
    if "order.submit" not in rt.bus.kinds():
        raise RuntimeError("order.submit is not registered (WP-02 not merged)")
    req.setdefault("request_id", rid())
    return rt.bus.execute(Command("order.submit", req, req["request_id"]))


def tick(rt: Runtime, at: Optional[str] = None) -> dict:
    """Lifecycle tick (WP-08); moves the FakeClock to ``at`` first."""
    if at is not None:
        rt.clock.set(at)
    sched = getattr(rt, "lifecycle", None)
    if sched is None:
        raise RuntimeError("no lifecycle scheduler bound (WP-08 not merged)")
    return sched.tick()


def assert_integrity(tc, rt: Runtime, mode: str = "full", lifetime_id: Optional[str] = None) -> None:
    report = rt.accounting.integrity(mode, lifetime_id=lifetime_id)
    tc.assertTrue(report.ok, f"integrity {mode} failed: {[p.to_dict() for p in report.problems]}")
    tc.assertEqual([], [p.to_dict() for p in report.problems], "integrity reported problems")


def set_mark(rt: Runtime, instrument_id: str, price, quality: str = "delayed", quote_time: Optional[int] = None) -> None:
    rt.marks.set(instrument_id, price, quality, quote_time)


# ---- test-only handlers (positions without WP-02) -------------------------------------------------------------------------
def _h_test_post(ctx, payload):
    """Post an arbitrary entry: ``{"kind", "memo", "idem_key", "lines": [[account, gl, ccy, amount_c, instrument_id?, fx_rate?], ...]}``."""
    lines = []
    for ln in payload["lines"]:
        kw = {}
        if len(ln) > 4 and ln[4]:
            kw["instrument_id"] = ln[4]
        if len(ln) > 5 and ln[5] is not None:
            kw["fx_rate"] = Decimal(str(ln[5]))
        lines.append(dr(ln[0], ln[1], ln[2], int(ln[3]), **kw))
    entry_id = ctx.post(Entry(payload.get("kind", "ADJUST"), payload.get("memo", "test entry"), lines, payload["idem_key"],
                              payload.get("ref_type"), payload.get("ref_id")))
    if payload.get("corrupt"):
        ctx.conn.execute("UPDATE gl_balances SET balance_c = balance_c + 1 WHERE account_id=? AND gl=? AND currency=?", tuple(payload["corrupt"]))
    if payload.get("raise_after"):
        raise RuntimeError("test failure after posting")
    return {"entry_id": entry_id}


def _h_test_buy(ctx, payload):
    """A test-only equity BUY fill per §5.4 (no funding, no matcher): fills row, entry ``Dr 1100 gross / Dr 5000 fee /
    Cr 2100 gross+fee``, LONG lot, T+1 settlement row.  ``{"account_id", "instrument_id", "qty", "price", "fee_c"?}``."""
    from finsim.terminal.db import next_id
    from finsim.terminal.tables import fee_c as _fee
    aid, iid = payload["account_id"], payload["instrument_id"]
    qty = int(payload["qty"])
    px = Decimal(str(payload["price"]))
    inst = ctx.services.instruments.get(iid, ctx.conn)
    gross = inst.contract_value_c(px, qty)
    fee = int(payload["fee_c"]) if payload.get("fee_c") is not None else _fee(inst.kind, qty)[0]
    fill_id = next_id(ctx.conn, "fill")
    trade_date = ctx.clock.today()
    settle = ctx.cal.settlement_date(trade_date, inst.settle_days)
    lines = [dr(aid, "1100", inst.currency, gross, instrument_id=iid)]
    if fee:
        lines.append(dr(aid, "5000", inst.currency, fee, instrument_id=iid))
    lines.append(cr(aid, "2100", inst.currency, gross + fee))
    entry_id = ctx.post(Entry("FILL", f"BUY {qty:,} {iid} @ {px}, fee {fee / 100:.2f}", lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
    ctx.conn.execute("INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
                     " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fill_id, None, aid, iid, "BUY", qty, str(px), gross, fee, inst.currency, "1", ctx.ts, trade_date.isoformat(), settle.isoformat(), "ORDER", "last",
                      "{}", None, None, None, entry_id, f"test:{fill_id}"))
    lot_id = open_lot(ctx.tx, aid, iid, "LONG", fill_id, qty, px, gross, inst.currency)
    settle_id = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (settle_id, fill_id, None, aid, "CASH", inst.currency, -(gross + fee), "2100", settle.isoformat()))
    ctx.touched_instruments.add((aid, iid))
    ctx.event("ORDER_FILLED", account_id=aid, instrument_id=iid, ref_type="fill", ref_id=fill_id, qty=qty, price=str(px), gross_c=gross, fee_c=fee)
    return {"fill_id": fill_id, "entry_id": entry_id, "lot_id": lot_id, "settle_id": settle_id, "gross_c": gross, "fee_c": fee, "settle_date": settle.isoformat()}


def _h_test_sell(ctx, payload):
    """A test-only equity SELL fill per §5.4 (FIFO): ``Dr 1200 gross−fee / Dr 5000 fee / Cr 1100 cost relieved / Cr(Dr) 4000
    realised``; fills row; lot reliefs; T+1 receivable settlement row.  ``{"account_id", "instrument_id", "qty", "price", "fee_c"?}``."""
    from finsim.terminal.db import next_id
    from finsim.terminal.ledger import relieve_fifo
    from finsim.terminal.tables import fee_c as _fee
    aid, iid = payload["account_id"], payload["instrument_id"]
    qty = int(payload["qty"])
    px = Decimal(str(payload["price"]))
    inst = ctx.services.instruments.get(iid, ctx.conn)
    gross = inst.contract_value_c(px, qty)
    fee = int(payload["fee_c"]) if payload.get("fee_c") is not None else _fee(inst.kind, qty)[0]
    fill_id = next_id(ctx.conn, "fill")
    trade_date = ctx.clock.today()
    settle = ctx.cal.settlement_date(trade_date, inst.settle_days)
    # the fill row first (lot_relief references it); its entry comes right after, so entry_id is set in a second step
    placeholder_entry = None
    reliefs = None
    ctx.conn.execute("INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
                     " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fill_id, None, aid, iid, "SELL", qty, str(px), gross, fee, inst.currency, "1", ctx.ts, trade_date.isoformat(), settle.isoformat(), "ORDER", "last",
                      "{}", None, None, None, _first_entry_id(ctx), f"test:{fill_id}"))
    reliefs = relieve_fifo(ctx.tx, aid, iid, "LONG", qty, fill_id, gross)
    cost = sum(r.cost_relieved_c for r in reliefs)
    realised = sum(r.realised_c for r in reliefs)
    lines = [dr(aid, "1200", inst.currency, gross - fee)]
    if fee:
        lines.append(dr(aid, "5000", inst.currency, fee, instrument_id=iid))
    lines.append(cr(aid, "1100", inst.currency, cost, instrument_id=iid))
    if realised:
        lines.append(cr(aid, "4000", inst.currency, realised, instrument_id=iid))
    entry_id = ctx.post(Entry("FILL", f"SELL {qty:,} {iid} @ {px}, fee {fee / 100:.2f}", lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
    ctx.conn.execute("UPDATE fills SET entry_id=? WHERE fill_id=?", (entry_id, fill_id))
    settle_id = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (settle_id, fill_id, None, aid, "CASH", inst.currency, gross - fee, "1200", settle.isoformat()))
    ctx.touched_instruments.add((aid, iid))
    ctx.event("ORDER_FILLED", account_id=aid, instrument_id=iid, ref_type="fill", ref_id=fill_id, qty=-qty, price=str(px), gross_c=gross, fee_c=fee)
    return {"fill_id": fill_id, "entry_id": entry_id, "settle_id": settle_id, "gross_c": gross, "fee_c": fee, "cost_relieved_c": cost, "realised_c": realised,
            "reliefs": [{"lot_id": r.lot_id, "qty": r.qty, "cost_relieved_c": r.cost_relieved_c, "proceeds_c": r.proceeds_c, "realised_c": r.realised_c} for r in reliefs]}


def _first_entry_id(ctx) -> int:
    """fills.entry_id is NOT NULL with a foreign key: the SELL helper inserts the fill before its entry and points it at
    an existing entry (the lifetime's capital entry) for a moment, then updates it to the real one."""
    row = ctx.conn.execute("SELECT entry_id FROM journal_entries ORDER BY entry_id LIMIT 1").fetchone()
    return int(row["entry_id"])


def _h_test_fail(ctx, payload):
    raise RuntimeError(payload.get("message", "boom"))


def buy(rt: Runtime, account: str, instrument_id: str, qty: int, price, fee_c: Optional[int] = None) -> Result:
    return execute(rt, "test.buy", {"account_id": account, "instrument_id": instrument_id, "qty": int(qty), "price": str(price), "fee_c": fee_c})


def sell(rt: Runtime, account: str, instrument_id: str, qty: int, price, fee_c: Optional[int] = None) -> Result:
    return execute(rt, "test.sell", {"account_id": account, "instrument_id": instrument_id, "qty": int(qty), "price": str(price), "fee_c": fee_c})


def snapshot(rt: Runtime, scope: str, snap_date) -> dict:
    """Compute and store a ``nav_snapshots`` row for ``scope`` at the clock's current marks (what the SNAPSHOT job does)."""
    row = rt.accounting.snapshot_row(scope, snap_date)
    with rt.db.writer() as tx:
        rt.accounting.insert_snapshot(tx.conn, row)
    return row


def post_entry(rt: Runtime, lines, idem_key: str, kind: str = "ADJUST", memo: str = "test entry", **extra) -> Result:
    payload = {"kind": kind, "memo": memo, "idem_key": idem_key, "lines": lines}
    payload.update(extra)
    return execute(rt, "test.post", payload)


def ny(dt: str) -> datetime:
    return datetime.fromisoformat(dt).replace(tzinfo=NY) if datetime.fromisoformat(dt).tzinfo is None else datetime.fromisoformat(dt)


def cents(x) -> int:
    return to_c(str(x))
