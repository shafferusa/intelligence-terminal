"""Futures: fills (fee + initial margin), daily variation margin, final settlement, the listing roll and the lifecycle
key helpers (TERMINAL_DESIGN.md §2.12 products/futures.py, §5.4, §10.7, §11.2 FUTURES_VM / FUTURES_FINAL, §11.4, §11.6,
§12.3, §13.4, §19.8; TERMINAL_WORKPLAN.md WP-04).

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- FUT_MARGIN: initial margin is the static per-contract table of ``instruments.FUTURES_SPECS`` carried on the row's
  ``spec.im_c``; ``balance(1300, instrument) == im_c × |position|`` at all times (L15).  A fill moves
  ``im_c × (|position after| − |position before|)`` between 1010 and 1300 (``im:{fill_id}``): an increase is funded
  first (``execution.fund(immediate=True)`` — the deposit leaves settled cash at once; the fee and the margin move are
  netted, so a closing fill never sweeps for its fee), a decrease is released.  Maintenance (``spec.mm_c``) is
  reported by ``accounting`` and Risk, never liquidated.
- No cost posting: a futures lot carries ``cost_c = 0`` (L8 waives the cost formula for FUTURE lots) and the fill
  price on ``lots.unit_cost`` for display; closing reliefs carry proceeds 0 and realise nothing in 4000.  Every cent
  of futures P&L reaches the ledger through 4400 (variation margin) and through nothing else.
- Fees (USD 2.25 per contract per side, or the PB schedule) are cash the same day (``fill:{fill_id}``: ``Dr 5000 /
  Cr 1010``); there is no settlement row and ``fills.settle_date`` is NULL because nothing settles later.
- FUT_SETTLE (``futures.vm``, key ``{account}:{instrument}:{date}``, idem ``vm:{key}``):
  ``VM_d = Σ_legs q_j × (S_d − r_j) × mult + unposted_c + Σ_{fills on d} q_i × (S_d − p_i) × mult``, where the legs
  are the carried position's reference (the previous ``futures_settle`` row — one leg at the previous settle print
  after a settle, the fill-price legs after fills), ``qty_prev`` = Σ signed fills with trade date < d must equal
  Σ legs (else FUT_REFERENCE_MISMATCH surfaces instead of a guess), and ``S_d`` is the OBSERVED print at or before
  the group's settlement time (``tables.SETTLE_TIME_ET``, capped at an early close) — never the exchange's official
  settlement.  Gain ``Dr 1010 / Cr 4400``; loss ``Dr 4400 / Cr 1010`` after ``fund()`` with the §11.6 overdraft rule
  (posted even when unfunded, under an open ERROR ``FUNDING_SHORTFALL``).  A zero VM posts nothing and records the
  settle.  ``business_date`` is the settle date even when the job catches up later.
- The settle print, in this order and from nothing else: (1) the ``marks`` SETTLE row already recorded for the date
  (the same print for every account); (2) the command's immutable snapshot record (``ctx.marks.quote``) when it was
  fetched at or after the settle time and its ``quote_time`` lies within the trade date's session at or before the
  settle time — else the latest completed 1-minute bar of its tail ending at or before the settle time; (3) the
  daily close the CALLER fetched outside the transaction with ``daily_fallback()`` (handlers never do network I/O)
  for exactly that date, labelled "observed close (daily bars), recorded late".  None of them → the job is SKIPPED
  ``SETTLE_PRINT_MISSING`` (jobs_done SKIPPED + one WARN attention, retried by the scheduler); nothing is invented.
- ``futures_settle`` is the per-(account, instrument) reference history, one row per trade date touched.  A row
  written by a fill (``quote_json.kind = "seed"``) or updated by a fill after the day's settle carries the reference
  legs (``legs = [[signed qty, price]]`` and ``unposted_c``, the P&L of contracts closed since the reference) and
  ``settle_px`` = the single weighted reference R with ``qty × (M − R) × mult = Σ q_j (M − r_j) × mult + unposted_c``,
  which ``accounting`` uses as the open-trade-equity reference; a row written by the VM job (``kind = "settle"``)
  carries the print (``price``) and one leg at it.  Because the carried term comes from legs, a day whose print was
  never found is caught up EXACTLY by the next settle, and that day's key then completes as DONE "superseded" —
  never posted twice.
- FUTURES_FINAL (``futures.final``, key ``{account}:{instrument}``, idem ``final:{key}``) on the last trade date
  (``instruments.expiry``), after that day's VM (else SKIPPED ``FINAL_PENDING_VM``, retried): a commission-free
  FUTURES_FINAL fill at the day's settle print closes the position — its trade date is the last trade date whenever
  the job runs — the IM is released and the instrument is marked EXPIRED.
- Jobs: handlers write ``jobs_done`` in their own transaction — DONE for a settled key and for terminal no-ops
  (not a trading day, nothing carried and no fills, superseded, flat at expiry), SKIPPED for a pending outcome; the
  key helpers return every key that is not DONE, so a SKIPPED key is retried.  A retry needs a new request id (the
  bus replays an id with its stored result).
- Money is integer cents / Decimal; the multiplier applies to the price as quoted (grains: cents per bushel × 50 USD
  per point, 525.25 → 526.00 on one ZC contract is 0.75 × 50 = USD 37.50).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .. import execution
from ..books import account_row
from ..clock import epoch_of, ny_of, ny_str, trade_date as _trade_date
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..instruments import FUTURES_SPECS
from ..ledger import Entry, balance, cr, dr, open_lot, position, relieve_fifo
from ..units import fmt_money, mul_c, qprice

JOB_VM = "FUTURES_VM"
JOB_FINAL = "FUTURES_FINAL"
SETTLE_DELAY_S = 300            # the VM job is due at settle time + 5 min (§11.2)
BAR_S = 60
SOURCE = "futures"

Leg = Tuple[int, Decimal]       # (signed contracts, reference price)


# ---- pure helpers ---------------------------------------------------------------------------------------------------------
def _spec_c(inst, key: str) -> int:
    v = (inst.spec or {}).get(key)
    if v is None:
        fs = FUTURES_SPECS.get(str((inst.spec or {}).get("root") or ""))
        v = getattr(fs, key, None) if fs is not None else None
    if v is None:
        raise RejectError("FUT_SPEC_MISSING", f"{inst.instrument_id}: the contract table has no {key} (FUT_MARGIN)")
    return int(v)


def im_required_c(inst, qty: int) -> int:
    """Initial margin in cents for ``|qty|`` contracts of ``inst`` (static table, sentence FUT_MARGIN)."""
    return _spec_c(inst, "im_c") * abs(int(qty))


def maintenance_c(inst, qty: int) -> int:
    """Maintenance margin in cents for ``|qty|`` contracts (reported, never liquidated)."""
    return _spec_c(inst, "mm_c") * abs(int(qty))


def signed_qty(side: str, qty: int) -> int:
    """Order side -> signed contracts: BUY-like +, SELL-like −."""
    return int(qty) if execution.position_effect(side) == "BUY" else -int(qty)


def merge_fill(legs: List[Leg], unposted_c: int, signed: int, price: Decimal, mult, scale) -> Tuple[List[Leg], int]:
    """Apply one fill to the reference legs: an opposite-signed fill closes legs FIFO (the closed part's P&L joins
    ``unposted_c``: ``closed_signed × (price − r_j) × mult``), the remainder opens a new leg at the fill price."""
    remaining = int(signed)
    out: List[Leg] = []
    for q, r in legs:
        if remaining != 0 and (q > 0) != (remaining > 0):
            closed = min(abs(q), abs(remaining))
            closed_signed = closed if q > 0 else -closed
            unposted_c += mul_c(closed_signed, qprice(price) - r, mult, scale)
            remaining += closed_signed
            left = q - closed_signed
            if left:
                out.append((left, r))
        else:
            out.append((q, r))
    if remaining:
        out.append((remaining, qprice(price)))
    return out, int(unposted_c)


def weighted_reference(legs: List[Leg], unposted_c: int, mult, scale) -> Optional[Decimal]:
    """The single reference R with ``qty × (M − R) × mult × scale = Σ q_j (M − r_j) × mult × scale + unposted_c``
    (8 decimals); None when the legs are flat."""
    qty = sum(q for q, _ in legs)
    if qty == 0:
        return None
    num = sum(Decimal(q) * r for q, r in legs) - Decimal(int(unposted_c)) / (Decimal(100) * Decimal(mult) * Decimal(scale))
    return qprice(num / Decimal(qty))


def carried_term_c(legs: List[Leg], unposted_c: int, settle_px: Decimal, mult, scale) -> int:
    """``Σ q_j × (S − r_j) × mult × scale + unposted_c`` in cents (exact: every leg price is a print)."""
    return sum(mul_c(q, settle_px - r, mult, scale) for q, r in legs) + int(unposted_c)


@dataclass
class Observed:
    price: Decimal
    method: str
    source: str
    provider: str
    quote_time: Optional[int]
    fetched_at: Optional[int]
    quality: str
    note: str

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["price"] = str(self.price)
        return d


def select_print(rec, d: date, settle_ts: int, session_open_ts: Optional[int] = None) -> Optional[Observed]:
    """The observed print at or before ``settle_ts`` from ONE quote record, or None (pure).  The record must have been
    fetched at or after the settle time (an earlier fetch cannot know the last print before it); its ``quote_time``
    must lie within the trade date's session (from ``session_open_ts``, else midnight New York of ``d``) at or before
    the settle time, else the latest completed 1-minute bar of its tail ending at or before the settle time is used."""
    if rec is None or getattr(rec, "error", None):
        return None
    fetched = int(getattr(rec, "fetched_at", 0) or 0)
    if fetched < int(settle_ts):
        return None
    lo = int(session_open_ts) if session_open_ts is not None else epoch_of(d, "00:00")
    qt = getattr(rec, "quote_time", None)
    last = getattr(rec, "last", None)
    if last is not None and qt is not None and lo <= int(qt) <= int(settle_ts):
        return Observed(qprice(last), "observed print at/before the settle time", str(rec.source or ""), str(rec.provider or ""), int(qt), fetched, "close",
                        f"last trade {ny_str(int(qt))} at or before the settle time {ny_str(int(settle_ts))} (FUT_SETTLE)")
    bars = list(getattr(rec, "bars_tail", None) or [])
    bar = getattr(rec, "bar", None)
    if bar is not None and all(int(b.t) != int(bar.t) for b in bars):
        bars.append(bar)
    cands = [b for b in bars if b.c is not None and lo <= int(b.t) and int(b.t) + BAR_S <= int(settle_ts)]
    if cands:
        b = max(cands, key=lambda x: int(x.t))
        return Observed(qprice(b.c), "1-minute bar close at/before the settle time", str(rec.source or ""), str(rec.provider or ""), int(b.t) + BAR_S, fetched, "close",
                        f"close of the 1-minute bar ending {ny_str(int(b.t) + BAR_S)}; the record's last trade ({ny_str(int(qt)) if qt else 'unknown'}) was after the settle time")
    return None


def daily_from_payload(payload: Optional[dict], d: date, now_ts: int) -> Optional[Observed]:
    """The ``daily`` block a caller fetched with ``daily_fallback()``; honoured only for exactly ``d``."""
    daily = (payload or {}).get("daily")
    if not isinstance(daily, dict) or daily.get("close") in (None, ""):
        return None
    if str(daily.get("date") or "") != d.isoformat():
        return None
    qt = daily.get("quote_time")
    return Observed(qprice(str(daily["close"])), "daily_close", str(daily.get("source") or "Yahoo daily bars (observed close)"), str(daily.get("provider") or "yahoo"),
                    None if qt is None else int(qt), int(daily.get("fetched_at") or now_ts), "close", "observed close (daily bars), recorded late")


# ---- services / rows --------------------------------------------------------------------------------------------------------
def _services(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _open_account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _future(services, conn, iid: str):
    inst = services.instruments.get(iid, conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid} is not in the master")
    if inst.kind != "FUTURE":
        raise RejectError("NOT_A_FUTURE", f"{iid} is a {inst.kind}, not a futures contract")
    return inst


def _last_trade(inst) -> Optional[date]:
    try:
        return date.fromisoformat(inst.expiry) if inst.expiry else None
    except ValueError:
        return None


def _yahoo(services):
    ps = getattr(services, "providers", None)
    if ps is not None and hasattr(getattr(ps, "yahoo", None), "daily"):
        return ps.yahoo
    if ps is not None and hasattr(ps, "daily"):
        return ps
    fp = getattr(services, "fp", None)
    return fp if fp is not None and hasattr(fp, "daily") else None


def daily_fallback(services, inst, d: date, now_ts: Optional[int] = None) -> Optional[dict]:
    """For the job runner, OUTSIDE any transaction: the daily close of ``inst`` on ``d`` from the Yahoo daily bars
    (§11.4 "recorded late"), as the ``daily`` block of a ``futures.vm`` payload; None when no bar of that date exists
    or no provider is bound.  A provider failure (``FetchError``) is reported as None too — the job then stays SKIPPED
    with its attention item, which is the honest outcome."""
    provider = _yahoo(services)
    sym = getattr(inst, "yahoo_symbol", None)
    if provider is None or not sym:
        return None
    now = int(now_ts if now_ts is not None else services.clock.epoch())
    days = max(10, (ny_of(now).date() - d).days + 5)
    try:
        bars = provider.daily(sym, days)
    except Exception as e:   # noqa: BLE001 — FetchError or a provider fault: no bar, no number
        log = getattr(services, "log", None)
        if log is not None:
            log.warning("daily_fallback %s %s: %s", sym, d, e)
        return None
    bar = next((b for b in bars if b.date == d), None)
    if bar is None or bar.c is None:
        return None
    close_ts = services.sessions.session_close(inst.session_group, d) if getattr(services, "sessions", None) is not None else None
    return {"close": str(qprice(bar.c)), "date": d.isoformat(), "source": "Yahoo daily bars (observed close)", "provider": str(getattr(provider, "name", "yahoo")),
            "quote_time": close_ts, "fetched_at": now, "method": "daily_close", "symbol": sym}


def _row_info(row) -> dict:
    try:
        info = json.loads(row["quote_json"] or "{}")
    except (ValueError, TypeError):
        info = {}
    return info if isinstance(info, dict) else {}


def _is_settle(row) -> bool:
    return row is not None and _row_info(row).get("kind") == "settle"


def _legs_of(row) -> Tuple[List[Leg], int]:
    if row is None:
        return [], 0
    info = _row_info(row)
    legs = [(int(q), qprice(str(p))) for q, p in (info.get("legs") or [])]
    if not legs and "legs" not in info and int(row["qty_after"] or 0):
        legs = [(int(row["qty_after"]), qprice(row["settle_px"]))]
    return legs, int(info.get("unposted_c") or 0)


def _row(conn, account: str, iid: str, ds: str):
    return conn.execute("SELECT * FROM futures_settle WHERE account_id=? AND instrument_id=? AND settle_date=?", (account, iid, ds)).fetchone()


def _latest_before(conn, account: str, iid: str, ds: str):
    return conn.execute("SELECT * FROM futures_settle WHERE account_id=? AND instrument_id=? AND settle_date<? ORDER BY settle_date DESC LIMIT 1",
                        (account, iid, ds)).fetchone()


def _settles_after(conn, account: str, iid: str, ds: str) -> List[str]:
    rows = conn.execute("SELECT * FROM futures_settle WHERE account_id=? AND instrument_id=? AND settle_date>? ORDER BY settle_date", (account, iid, ds)).fetchall()
    return [r["settle_date"] for r in rows if _is_settle(r)]


def _write_row(ctx: TxContext, account: str, iid: str, ds: str, settle_px: Decimal, qty_after: int, vm_c: int, info: dict, entry_id: Optional[int]) -> None:
    ctx.conn.execute(
        "INSERT INTO futures_settle(account_id, instrument_id, settle_date, settle_px, qty_after, vm_c, quote_json, entry_id) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(account_id, instrument_id, settle_date) DO UPDATE SET settle_px=excluded.settle_px, qty_after=excluded.qty_after, vm_c=excluded.vm_c,"
        " quote_json=excluded.quote_json, entry_id=excluded.entry_id",
        (account, iid, ds, str(qprice(settle_px)), int(qty_after), int(vm_c), dumps(info), entry_id))


def _fills_state(conn, account: str, iid: str, ds: str) -> Tuple[int, List[Tuple[int, Decimal, str]]]:
    """``(qty_prev, [(signed qty, price, fill_id)] of trade date ds)`` from the fills table (BUY +, SELL −, every origin)."""
    rows = conn.execute("SELECT fill_id, side, qty, price, trade_date FROM fills WHERE account_id=? AND instrument_id=? AND trade_date<=? ORDER BY ts, fill_id",
                        (account, iid, ds)).fetchall()
    qty_prev = 0
    today: List[Tuple[int, Decimal, str]] = []
    for r in rows:
        s = int(r["qty"]) if r["side"] == "BUY" else -int(r["qty"])
        if r["trade_date"] < ds:
            qty_prev += s
        else:
            today.append((s, qprice(r["price"]), r["fill_id"]))
    return qty_prev, today


def _job_row(conn, job: str, key: str):
    return conn.execute("SELECT status, note, run_ts FROM jobs_done WHERE job=? AND key=?", (job, key)).fetchone()


def _job_done(ctx: TxContext, job: str, key: str, status: str, note: str) -> None:
    ctx.conn.execute("INSERT INTO jobs_done(job, key, run_ts, command_id, status, note) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(job, key) DO UPDATE SET run_ts=excluded.run_ts, command_id=excluded.command_id, status=excluded.status, note=excluded.note",
                     (job, key, ctx.ts, ctx.command_id, status, note[:500]))


def _resolve_attention(ctx: TxContext, codes: Tuple[str, ...], ref_id: str) -> int:
    n = 0
    for code in codes:
        cur = ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, code, ref_id))
        n += cur.rowcount or 0
    return n


def _skip(ctx: TxContext, job: str, key: str, code: str, message: str, account_id: str, iid: str, details: Optional[dict] = None) -> dict:
    """A PENDING outcome: jobs_done SKIPPED (the key stays due), one open WARN attention per (code, key), an event."""
    _job_done(ctx, job, key, "SKIPPED", f"{code}: {message}")
    existing = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
    attention_id = int(existing["id"]) if existing else ctx.attention("WARN", SOURCE, code, message,
                                                                          dict(details or {}, account_id=account_id, ref_type="futures", ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=account_id, instrument_id=iid, ref_type="job", ref_id=key, job=job, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "code": code, "message": message, "key": key, "attention_id": attention_id}


def _done_noop(ctx: TxContext, job: str, key: str, code: str, note: str) -> dict:
    """A TERMINAL no-op: DONE with the reason, nothing posted."""
    _job_done(ctx, job, key, "DONE", f"{code}: {note}")
    return {"status": "DONE", "skipped": True, "posted": False, "code": code, "note": note, "key": key}


# ---- the settle print (§11.2 / §11.4) -----------------------------------------------------------------------------------------
def settle_print(ctx: TxContext, services, inst, d: date, settle_ts: int, payload: Optional[dict] = None) -> Optional[Observed]:
    """The settle print of ``inst`` for trade date ``d`` (module docstring, FUT_SETTLE): the recorded SETTLE mark, else
    the snapshot record, else the caller's daily block; None when nothing was observed."""
    conn = ctx.conn
    ds = d.isoformat()
    row = conn.execute("SELECT * FROM marks WHERE instrument_id=? AND mark_date=? AND kind='SETTLE'", (inst.instrument_id, ds)).fetchone()
    if row is not None:
        return Observed(qprice(row["price"]), str(row["method"]), str(row["source"]), "marks", row["quote_time"], int(row["fetched_at"]), str(row["quality"]),
                        f"recorded SETTLE mark of {ds}" + (f": {row['note']}" if row["note"] else ""))
    rec = ctx.marks.quote(inst.instrument_id) if ctx.marks is not None and hasattr(ctx.marks, "quote") else None
    lo = None
    try:
        sessions = services.sessions.sessions_for(inst.session_group, d)
        lo = sessions[0].open_ts if sessions else None
    except KeyError:
        lo = None
    obs = select_print(rec, d, settle_ts, lo)
    if obs is not None:
        return obs
    return daily_from_payload(payload, d, ctx.ts)


def _record_mark(ctx: TxContext, inst, ds: str, obs: Observed) -> None:
    ctx.conn.execute("INSERT OR IGNORE INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                     " VALUES (?,?,'SETTLE',?,?,?,?,?,?,?,?)",
                     (inst.instrument_id, ds, str(obs.price), inst.currency, obs.quality, obs.source, obs.method, obs.quote_time,
                      int(obs.fetched_at if obs.fetched_at is not None else ctx.ts), obs.note[:300]))


# ---- the fill (§10.7 for futures; called by execution.apply_fill through services.futures) --------------------------------
def _reference_after_fill(ctx: TxContext, inst, account: str, d: date, pos_before: int, signed: int, price: Decimal, fill_id: str) -> dict:
    """Upsert the trade date's ``futures_settle`` row after a fill (module docstring: seed / after-settle reference)."""
    conn = ctx.conn
    iid = inst.instrument_id
    ds = d.isoformat()
    same_day = _row(conn, account, iid, ds)
    base = same_day if same_day is not None else _latest_before(conn, account, iid, ds)
    legs, unposted = _legs_of(base)
    held = sum(q for q, _ in legs)
    if held != pos_before:
        raise RejectError("FUT_REFERENCE_MISMATCH",
                          f"{account} {iid}: the reference legs hold {held} contracts but the position is {pos_before}; the futures_settle history needs repair "
                          f"before trading (nothing is guessed)", {"legs": [[q, str(r)] for q, r in legs], "position": pos_before})
    legs, unposted = merge_fill(legs, unposted, signed, price, inst.multiplier, inst.price_scale)
    ref = weighted_reference(legs, unposted, inst.multiplier, inst.price_scale)
    qty_after = pos_before + signed
    fills = list(_row_info(same_day).get("fills") or []) + [fill_id] if same_day is not None else [fill_id]
    if same_day is not None and _is_settle(same_day):
        info = dict(_row_info(same_day))
        info.update({"legs": [[q, str(r)] for q, r in legs], "unposted_c": unposted, "fills": fills, "ref_after_fill": None if ref is None else str(ref),
                     "note": "settle print kept in 'price'; settle_px is the weighted reference of the position after the fills since the settle"})
        settle_px = ref if ref is not None else qprice(str(info.get("price") or same_day["settle_px"]))
        _write_row(ctx, account, iid, ds, settle_px, qty_after, int(same_day["vm_c"]), info, same_day["entry_id"])
    else:
        info = {"kind": "seed", "legs": [[q, str(r)] for q, r in legs], "unposted_c": unposted, "fills": fills,
                "note": "reference until the day's settle: weighted legs of the carried position and the day's fills; nothing posted (FUT_SETTLE)"}
        if ref is not None:
            settle_px = ref
        elif base is not None:
            settle_px = qprice(base["settle_px"])
        else:
            settle_px = qprice(price)
        _write_row(ctx, account, iid, ds, settle_px, qty_after, 0, info, None)
    return {"reference_px": str(settle_px), "legs": [[q, str(r)] for q, r in legs], "unposted_c": unposted, "qty_after": qty_after}


def apply_fill(ctx: TxContext, spec, services=None):
    """A futures fill inside the caller's transaction (``execution.apply_fill`` dispatches FUTURE here): position effect
    from the order side (BUY-like +, SELL-like −; an opposite-signed fill closes FIFO, the rest opens), fee entry
    ``fill:{fill_id}``, IM move ``im:{fill_id}`` (funded first, netted with the fee), fill row (no settlement), lots
    (cost 0), the day's reference row.  Returns ``execution.FillResult`` (``margin`` carries the IM detail)."""
    services = _services(ctx, services)
    conn = ctx.conn
    acct = _open_account(conn, spec.account_id)
    inst = _future(services, conn, spec.instrument_id)
    qty = int(spec.qty)
    if qty <= 0:
        raise RejectError("QTY_INVALID", "fill quantity must be positive")
    if spec.side not in execution.BUY_SIDES + execution.SELL_SIDES:
        raise RejectError("SIDE_INVALID", f"unknown order side {spec.side!r}")
    signed = signed_qty(spec.side, qty)
    effect = execution.position_effect(spec.side)
    price = qprice(spec.price)
    ccy = inst.currency
    rate = ctx.fx.rate(ccy)
    last = _last_trade(inst)
    final = spec.origin == "FUTURES_FINAL"
    if final and last is not None:
        tdate = last
    else:
        tdate = _trade_date(ctx.now, inst.session_group, ctx.cal)
    if not final:
        if inst.status != "ACTIVE":
            raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} is {inst.status}: no new fills")
        if last is not None and tdate > last:
            raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} last traded on {last}: trade date {tdate} is past it (LAST_SESSION)")
    pos_before, _cost = position(conn, spec.account_id, inst.instrument_id)
    pos_after = pos_before + signed
    closing = min(qty, abs(pos_before)) if pos_before and (pos_before > 0) != (signed > 0) else 0
    opening = qty - closing
    im_per = _spec_c(inst, "im_c")
    im_delta = im_per * (abs(pos_after) - abs(pos_before))
    gross_c = inst.contract_value_c(price, qty)
    if spec.fee_override_c is not None:
        fee_c, fee_basis = int(spec.fee_override_c), "override (system fill)"
    else:
        fee_c, fee_basis = execution.fee_for(services, acct, inst, qty, gross_c)
    fill_id = next_id(conn, "fill")
    funding = None
    need = fee_c + im_delta
    if need > 0:
        funding = execution.fund(ctx, spec.account_id, ccy, need, "fill", fill_id, None, immediate=True, services=services)
    memo = (f"{spec.side} {qty:,} {inst.symbol} @ {price}, fee {fmt_money(fee_c)}; initial margin {'+' if im_delta >= 0 else ''}{fmt_money(im_delta)} "
            f"({abs(pos_before):,} -> {abs(pos_after):,} contracts x {fmt_money(im_per)}; FUT_MARGIN)" + (f" ({spec.note})" if spec.note else ""))
    entry_kind = "FINAL" if final else "FILL"
    fee_entry = im_entry = None
    if im_delta > 0:
        im_entry = ctx.post(Entry("FUT_MARGIN", memo, [dr(spec.account_id, "1300", ccy, im_delta, instrument_id=inst.instrument_id, memo="initial margin posted (static table; FUT_MARGIN)"),
                                                      cr(spec.account_id, "1010", ccy, im_delta)], f"im:{fill_id}", ref_type="fill", ref_id=fill_id))
    elif im_delta < 0:
        im_entry = ctx.post(Entry("FUT_MARGIN", memo, [dr(spec.account_id, "1010", ccy, -im_delta),
                                                      cr(spec.account_id, "1300", ccy, -im_delta, instrument_id=inst.instrument_id, memo="initial margin released")],
                                  f"im:{fill_id}", ref_type="fill", ref_id=fill_id))
    if fee_c:
        fee_entry = ctx.post(Entry(entry_kind, memo, [dr(spec.account_id, "5000", ccy, fee_c, instrument_id=inst.instrument_id, memo=fee_basis),
                                                      cr(spec.account_id, "1010", ccy, fee_c)], f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
    entry_id = fee_entry if fee_entry is not None else im_entry
    if entry_id is None:
        raise RejectError("ZERO_FILL", f"a fill of {qty:,} {inst.symbol} at {price} moves no cash and no margin")
    rec = spec.quote
    qd = rec.to_dict() if rec is not None and hasattr(rec, "to_dict") else (dict(rec) if isinstance(rec, dict) else {})
    conn.execute(
        "INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
        " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, spec.order_id, spec.account_id, inst.instrument_id, effect, qty, str(price), gross_c, fee_c, ccy, str(rate), ctx.ts, tdate.isoformat(), None,
         spec.origin, spec.price_basis, dumps(qd), spec.bar_ts, spec.cap_qty, spec.cap_note, entry_id, spec.idem_key))
    reliefs = []
    if closing:
        rl = relieve_fifo(ctx.tx, spec.account_id, inst.instrument_id, "LONG" if pos_before > 0 else "SHORT", closing, fill_id, 0)
        reliefs = [r.__dict__ for r in rl]
    lot_id = None
    if opening:
        lot_id = open_lot(ctx.tx, spec.account_id, inst.instrument_id, "LONG" if signed > 0 else "SHORT", fill_id, opening, price, 0, ccy)
    reference = _reference_after_fill(ctx, inst, spec.account_id, tdate, pos_before, signed, price, fill_id)
    ctx.touched_instruments.add((spec.account_id, inst.instrument_id))
    ctx.touched_accounts.add(spec.account_id)
    if im_delta < 0:
        execution.auto_resolve_shortfall(ctx, spec.account_id, ccy)
    new_qty, _ = position(conn, spec.account_id, inst.instrument_id)
    margin = {"im_per_contract_c": im_per, "im_delta_c": im_delta, "im_posted_c": balance(conn, spec.account_id, "1300", ccy, inst.instrument_id),
              "maintenance_c": _spec_c(inst, "mm_c") * abs(new_qty), "im_entry_id": im_entry, "fee_entry_id": fee_entry, "closing": closing, "opening": opening,
              "reference": reference}
    return execution.FillResult(fill_id, entry_id, spec.account_id, inst.instrument_id, spec.side, effect, qty, price, gross_c, fee_c, fee_basis, ccy, rate,
                                tdate.isoformat(), None, None, lot_id, reliefs, 0, 0, funding, new_qty, memo, margin, spec.price_basis)


# ---- futures.vm (§11.2 FUTURES_VM) ----------------------------------------------------------------------------------------------
def _vm_key(payload: dict) -> Tuple[str, str, str]:
    account = str(payload.get("account_id") or "")
    iid = str(payload.get("instrument_id") or "")
    ds = str(payload.get("date") or "")
    key = payload.get("key")
    if key and not (account and iid and ds):
        parts = str(key).rsplit(":", 2)
        if len(parts) == 3:
            account, iid, ds = parts
    try:
        d = date.fromisoformat(ds)
    except ValueError as e:
        raise RejectError("KEY_INVALID", f"futures.vm needs account_id, instrument_id and an ISO date (got {ds!r})") from e
    if not account or not iid:
        raise RejectError("KEY_INVALID", "futures.vm needs account_id and instrument_id")
    return account, iid, d.isoformat()


def h_vm(ctx: TxContext, payload: dict) -> dict:
    """FUTURES_VM for one (account, instrument, date) — module docstring FUT_SETTLE.  Idempotent per key."""
    services = _services(ctx)
    conn = ctx.conn
    account, iid, ds = _vm_key(payload)
    d = date.fromisoformat(ds)
    key = f"{account}:{iid}:{ds}"
    idem = f"vm:{key}"
    _open_account(conn, account)
    inst = _future(services, conn, iid)
    ccy, mult, scale = inst.currency, inst.multiplier, inst.price_scale
    existing = _row(conn, account, iid, ds)
    if _is_settle(existing):
        _job_done(ctx, JOB_VM, key, "DONE", "already settled")
        return {"status": "DONE", "already": True, "skipped": True, "posted": False, "key": key, "settle_px": existing["settle_px"],
                "vm_c": int(existing["vm_c"]), "entry_id": existing["entry_id"]}
    later = _settles_after(conn, account, iid, ds)
    if later:
        return _done_noop(ctx, JOB_VM, key, "VM_SUPERSEDED",
                          f"the settle of {later[0]} measured the position from the reference that stood before it, so the P&L of {ds} is already in 4400")
    settle_ts = services.sessions.settle_time(inst.session_group, d)
    if settle_ts is None:
        return _done_noop(ctx, JOB_VM, key, "NOT_A_TRADING_DAY", f"{ds} is not a trading day of {inst.session_group}")
    if ctx.ts < int(settle_ts):
        return _skip(ctx, JOB_VM, key, "SETTLE_NOT_YET", f"{inst.symbol}: the settlement time of {ds} is {ny_str(int(settle_ts))}; the VM runs after it",
                     account, iid, {"settle_ts": int(settle_ts)})
    qty_prev, fills_today = _fills_state(conn, account, iid, ds)
    if qty_prev == 0 and not fills_today:
        return _done_noop(ctx, JOB_VM, key, "NOTHING_TO_SETTLE", f"no position carried into {ds} and no fills on it")
    obs = settle_print(ctx, services, inst, d, int(settle_ts), payload)
    if obs is None:
        rec = ctx.marks.quote(iid) if ctx.marks is not None and hasattr(ctx.marks, "quote") else None
        have = "no record in the snapshot" if rec is None else (f"snapshot record last {rec.last} at {ny_str(int(rec.quote_time)) if rec.quote_time else '?'},"
                                                                f" fetched {ny_str(int(rec.fetched_at)) if rec.fetched_at else '?'}")
        return _skip(ctx, JOB_VM, key, "SETTLE_PRINT_MISSING",
                     f"{inst.symbol}: no observed print at or before the settle time {ny_str(int(settle_ts))} of {ds} ({have}); nothing is invented — "
                     f"retried; after downtime the runner supplies the daily close (daily_fallback)", account, iid, {"settle_ts": int(settle_ts), "date": ds})
    S = obs.price
    prev = _latest_before(conn, account, iid, ds)
    legs, unposted = _legs_of(prev)
    held = sum(q for q, _ in legs)
    if held != qty_prev:
        raise RejectError("FUT_REFERENCE_MISMATCH",
                          f"{account} {iid}: the reference legs before {ds} hold {held} contracts but the fills say {qty_prev}; the futures_settle history needs "
                          f"repair (nothing is guessed)", {"legs": [[q, str(r)] for q, r in legs], "qty_prev": qty_prev})
    carried = carried_term_c(legs, unposted, S, mult, scale)
    fills_c = sum(mul_c(q, S - p, mult, scale) for q, p, _ in fills_today)
    vm = carried + fills_c
    qty_after = qty_prev + sum(q for q, _, _ in fills_today)
    ref_txt = ", ".join(f"{q:+d} @ {r}" for q, r in legs) or "none"
    memo = (f"{inst.symbol} variation margin {ds}: settle {S} ({obs.method}); carried {ref_txt}" + (f" + unposted {fmt_money(unposted)}" if unposted else "") +
            f" -> {fmt_money(carried)}; fills of the day {fmt_money(fills_c)}; VM {fmt_money(vm)} (FUT_SETTLE)")
    funding = None
    entry_id = None
    if vm < 0:
        funding = execution.fund(ctx, account, ccy, -vm, "vm", idem, None, immediate=True, overdraft=True, services=services)
        entry_id = ctx.post(Entry("VM", memo, [dr(account, "4400", ccy, -vm, instrument_id=iid, memo="variation margin paid"), cr(account, "1010", ccy, -vm)],
                                  idem, ref_type="vm", ref_id=key, business_date=ds))
    elif vm > 0:
        entry_id = ctx.post(Entry("VM", memo, [dr(account, "1010", ccy, vm), cr(account, "4400", ccy, vm, instrument_id=iid, memo="variation margin received")],
                                  idem, ref_type="vm", ref_id=key, business_date=ds))
        execution.auto_resolve_shortfall(ctx, account, ccy)
    _record_mark(ctx, inst, ds, obs)
    info = {"kind": "settle", "price": str(S), "method": obs.method, "source": obs.source, "provider": obs.provider, "quote_time": obs.quote_time,
            "fetched_at": obs.fetched_at, "quality": obs.quality, "note": obs.note, "legs": [[qty_after, str(S)]] if qty_after else [], "unposted_c": 0,
            "qty_prev": qty_prev, "carried_c": carried, "fills_c": fills_c, "fills": [f for _, _, f in fills_today], "reference": [[q, str(r)] for q, r in legs],
            "reference_unposted_c": unposted}
    _write_row(ctx, account, iid, ds, S, qty_after, vm, info, entry_id)
    _job_done(ctx, JOB_VM, key, "DONE", f"VM {fmt_money(vm)} at {S} ({obs.method})" + ("" if funding is None or funding.funded else "; UNFUNDED (overdraft rule)"))
    _resolve_attention(ctx, ("SETTLE_PRINT_MISSING", "SETTLE_NOT_YET"), key)
    ctx.touched_instruments.add((account, iid))
    ctx.touched_accounts.add(account)
    ctx.event("FUTURES_VM", account_id=account, instrument_id=iid, ref_type="vm", ref_id=key, vm_c=vm, settle_px=str(S), method=obs.method, qty_prev=qty_prev,
              qty_after=qty_after, entry_id=entry_id, funded=True if funding is None else funding.funded, date=ds)
    return {"status": "DONE", "posted": entry_id is not None, "skipped": entry_id is None, "key": key, "date": ds, "settle_px": str(S), "print": obs.to_dict(),
            "qty_prev": qty_prev, "qty_after": qty_after, "carried_c": carried, "fills_c": fills_c, "vm_c": vm, "entry_id": entry_id,
            "funding": None if funding is None else funding.to_dict(), "settled_c": balance(conn, account, "1010", ccy),
            "reference": [[q, str(r)] for q, r in legs], "reference_unposted_c": unposted}


# ---- futures.final (§11.2 FUTURES_FINAL) ------------------------------------------------------------------------------------------
def _final_key(payload: dict) -> Tuple[str, str]:
    account = str(payload.get("account_id") or "")
    iid = str(payload.get("instrument_id") or "")
    key = payload.get("key")
    if key and not (account and iid):
        parts = str(key).rsplit(":", 1)
        if len(parts) == 2:
            account, iid = parts
    if not account or not iid:
        raise RejectError("KEY_INVALID", "futures.final needs account_id and instrument_id")
    return account, iid


def _expire(ctx: TxContext, services, inst) -> None:
    ctx.conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE instrument_id=? AND status='ACTIVE'", (ctx.ts, inst.instrument_id))
    services.instruments.refresh(inst.instrument_id, ctx.conn)


def h_final(ctx: TxContext, payload: dict) -> dict:
    """FUTURES_FINAL for one (account, instrument): after the last trade date's VM, a FUTURES_FINAL fill at that
    settle print closes the position, the IM is released, the instrument is EXPIRED.  Idempotent per key."""
    services = _services(ctx)
    conn = ctx.conn
    account, iid = _final_key(payload)
    key = f"{account}:{iid}"
    idem = f"final:{key}"
    _open_account(conn, account)
    inst = _future(services, conn, iid)
    last = _last_trade(inst)
    if last is None:
        raise RejectError("FUT_SPEC_MISSING", f"{iid}: no last trade date on the master row")
    done = conn.execute("SELECT fill_id, entry_id FROM fills WHERE idem_key=?", (idem,)).fetchone()
    if done is not None:
        _job_done(ctx, JOB_FINAL, key, "DONE", "already settled")
        return {"status": "DONE", "already": True, "skipped": True, "posted": False, "key": key, "fill_id": done["fill_id"], "entry_id": done["entry_id"]}
    today = ny_of(ctx.ts).date()
    if today < last:
        raise ConflictError("FINAL_NOT_DUE", f"{inst.symbol} trades until {last}; the final settlement runs on that date after the settle", {"last_trade_date": last.isoformat()})
    qty, _ = position(conn, account, iid)
    if qty == 0:
        _expire(ctx, services, inst)
        return _done_noop(ctx, JOB_FINAL, key, "NO_POSITION", f"no position in {inst.symbol} at expiry; instrument marked EXPIRED")
    row = _row(conn, account, iid, last.isoformat())
    if not _is_settle(row):
        return _skip(ctx, JOB_FINAL, key, "FINAL_PENDING_VM",
                     f"{inst.symbol}: the variation margin of the last trade date {last} has not been settled yet; the final settlement follows it",
                     account, iid, {"vm_key": f"{account}:{iid}:{last.isoformat()}"})
    info = _row_info(row)
    S = qprice(str(info.get("price") or row["settle_px"]))
    side = "SELL" if qty > 0 else "BUY"
    quote = {"source": info.get("source"), "method": "settle", "price": str(S), "mark_date": last.isoformat(), "quote_time": info.get("quote_time"),
             "note": "final settlement at the last trade date's observed settle print (FUT_SETTLE)"}
    fr = execution.apply_system_fill(ctx, account, inst, side, abs(qty), S, "FUTURES_FINAL", quote, idem, f"final settlement of {inst.symbol} on {last}", services=services)
    _expire(ctx, services, inst)
    _job_done(ctx, JOB_FINAL, key, "DONE", f"FUTURES_FINAL {side} {abs(qty)} @ {S}; IM released {fmt_money(-(fr.margin or {}).get('im_delta_c', 0))}")
    _resolve_attention(ctx, ("FINAL_PENDING_VM",), key)
    ctx.event("FUTURES_FINAL", account_id=account, instrument_id=iid, ref_type="fill", ref_id=fr.fill_id, qty=abs(qty), side=side, price=str(S),
              im_released_c=-(fr.margin or {}).get("im_delta_c", 0), entry_id=fr.entry_id, last_trade_date=last.isoformat())
    return {"status": "DONE", "posted": True, "key": key, "fill": fr.to_dict(), "settle_px": str(S), "last_trade_date": last.isoformat(), "instrument_status": "EXPIRED"}


# ---- lifecycle helpers (WP-08: due keys from state, the HOUSEKEEPING roll) -----------------------------------------------------
def vm_keys(conn, now_ts: int, sessions, instruments) -> List[Tuple[str, str, str]]:
    """``(account_id, instrument_id, date)`` triples whose FUTURES_VM is due (§11.1: derived from state): for every
    (account, instrument) of an OPEN account / lifetime with a ``futures_settle`` history or an open position, each
    trading day of the group after the last settle (from the first reference row) up to today and the last trade
    date, whose settle time + 5 min has passed, with a position carried into it or fills on it; keys DONE are skipped
    (SKIPPED keys come back).  Dates stop at the first day whose settle has not come yet."""
    now = int(now_ts)
    today = ny_of(now).date()
    pairs = set()
    for r in conn.execute("SELECT DISTINCT f.account_id, f.instrument_id FROM futures_settle f JOIN accounts a ON a.account_id=f.account_id"
                          " JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE a.status='OPEN' AND l.status='OPEN'").fetchall():
        pairs.add((r["account_id"], r["instrument_id"]))
    for r in conn.execute("SELECT v.account_id, v.instrument_id FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id JOIN accounts a ON a.account_id=v.account_id"
                          " JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE i.kind='FUTURE' AND v.qty <> 0 AND a.status='OPEN' AND l.status='OPEN'").fetchall():
        pairs.add((r["account_id"], r["instrument_id"]))
    out: List[Tuple[str, str, str]] = []
    for account, iid in sorted(pairs):
        inst = instruments.get(iid, conn)
        if inst is None or inst.kind != "FUTURE":
            continue
        rows = conn.execute("SELECT settle_date, quote_json FROM futures_settle WHERE account_id=? AND instrument_id=? ORDER BY settle_date", (account, iid)).fetchall()
        settled = [r["settle_date"] for r in rows if _is_settle(r)]
        first_fill = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE account_id=? AND instrument_id=?", (account, iid)).fetchone()
        starts = [x for x in ([rows[0]["settle_date"]] if rows else []) + ([first_fill["d"]] if first_fill and first_fill["d"] else []) if x]
        if settled:
            start = date.fromisoformat(settled[-1]) + timedelta(days=1)
        elif starts:
            start = date.fromisoformat(min(starts))
        else:
            continue
        end = today
        last = _last_trade(inst)
        if last is not None and last < end:
            end = last
        by_date: Dict[str, int] = {}
        qty = 0
        for f in conn.execute("SELECT side, qty, trade_date FROM fills WHERE account_id=? AND instrument_id=?", (account, iid)).fetchall():
            s = int(f["qty"]) if f["side"] == "BUY" else -int(f["qty"])
            if f["trade_date"] < start.isoformat():
                qty += s
            else:
                by_date[f["trade_date"]] = by_date.get(f["trade_date"], 0) + s
        d = start
        while d <= end:
            ds = d.isoformat()
            day_fills = by_date.get(ds, 0)
            if sessions.is_trading_day(inst.session_group, d):
                st = sessions.settle_time(inst.session_group, d)
                if st is None or now < int(st) + SETTLE_DELAY_S:
                    break
                if qty != 0 or day_fills != 0:
                    row = _job_row(conn, JOB_VM, f"{account}:{iid}:{ds}")
                    if row is None or row["status"] != "DONE":
                        out.append((account, iid, ds))
            qty += day_fills
            d += timedelta(days=1)
    return out


def final_keys(conn, now_ts: int, instruments) -> List[Tuple[str, str]]:
    """``(account_id, instrument_id)`` pairs whose FUTURES_FINAL is due: open futures positions of OPEN accounts /
    lifetimes whose last trade date is today or past (the handler itself waits for that day's VM); DONE keys skipped."""
    today = ny_of(int(now_ts)).date().isoformat()
    out: List[Tuple[str, str]] = []
    for r in conn.execute("SELECT v.account_id, v.instrument_id, i.expiry FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id"
                          " JOIN accounts a ON a.account_id=v.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                          " WHERE i.kind='FUTURE' AND v.qty <> 0 AND a.status='OPEN' AND l.status='OPEN' AND i.expiry IS NOT NULL AND i.expiry <= ?"
                          " ORDER BY v.account_id, v.instrument_id", (today,)).fetchall():
        row = _job_row(conn, JOB_FINAL, f"{r['account_id']}:{r['instrument_id']}")
        if row is not None and row["status"] == "DONE":
            continue
        out.append((r["account_id"], r["instrument_id"]))
    return out


def roll_listings(ctx: TxContext, services, today: Optional[date] = None) -> dict:
    """HOUSEKEEPING (§11.2): list the next 4 months of every root and mark contracts past their last trade date
    EXPIRED (``InstrumentMaster.ensure_futures_listings``).  New rows are visible to ``get()`` at once; the caller
    reloads the master (``instruments.load()``) AFTER the commit so ``all()`` / search see them."""
    d = today if today is not None else ny_of(ctx.ts).date()
    n = int(services.instruments.ensure_futures_listings(ctx.tx, d, ctx.cal))
    expired = [r["instrument_id"] for r in ctx.conn.execute("SELECT instrument_id FROM instruments WHERE kind='FUTURE' AND status='EXPIRED' AND updated_ts=?", (ctx.ts,)).fetchall()]
    for iid in expired:
        services.instruments.refresh(iid, ctx.conn)
    if n or expired:
        ctx.event("FUTURES_LISTINGS", ref_type="housekeeping", ref_id=d.isoformat(), listed=n, expired=expired)
    return {"date": d.isoformat(), "listed": n, "expired": expired, "reload_master": bool(n)}


def register(bus, services=None) -> None:
    """Register ``futures.vm`` and ``futures.final``; the runtime binds this module as ``services.futures`` so
    ``execution.apply_fill`` and the preview can reach ``apply_fill`` / ``im_required_c``."""
    bus.register("futures.vm", h_vm)
    bus.register("futures.final", h_final)
