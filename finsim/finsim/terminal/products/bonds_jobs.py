"""Bonds, part 2: the lifecycle handlers ``bonds.accrue`` / ``bonds.coupon`` / ``bonds.mature`` / ``bonds.list`` and the
ProductJob key helpers (``accrue_keys``, ``coupon_keys``, ``maturity_keys``, ``listing_keys``).  The conventions (ACCRUED,
ACCRUAL, COUPON, MATURITY, BENCHMARKS, JOBS) are stated in ``products/bonds.py``, which re-exports every name here, so
``services.bonds.accrue_keys`` and ``bonds.h_accrue`` keep their documented import path (WP-10; the ~900-line rule).

Handlers write their own ``jobs_done`` row (DONE, or SKIPPED with a note and one WARN attention per (code, key)); every
posting is idempotent through its idem key; nothing is ever posted in place of a missing coupon date, night or curve.
Build 2 P07 (2026-10-21): every posting is in the BOND'S currency (a EUR / JPY generic benchmark accrues, pays coupons and
redeems in that currency); coupon dates follow the month-end aware schedule of products/bonds.py; ``bonds.list`` (the
monthly BOND_LISTING key) no longer lists model benchmarks - the master is the real Treasury list of
products/treasuries.py - it expires matured bonds nobody holds (ACTIVE or DELISTED rows) and reports what it did.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Tuple

from .. import execution
from ..clock import epoch_of, ny_of
from ..commands import RejectError, TxContext
from ..db import dumps, next_id
from ..ledger import Entry, balance, cr, dr, position, relieve_fifo
from ..units import fmt_money
from .bonds import (COUPON_ET, JOB_ACCRUE, JOB_COUPON, JOB_LISTING, JOB_MATURITY, KIND, LISTING_ET, MATURITY_ET, BondTrade, _bond, _delete_state, _done_noop,
                    _insert_fill, _job_done, _job_row, _key3, _open_account, _resolve_attention, _services, _skip, _write_state, accrued_cents, accrued_per_100,
                    bond_terms, coupon_cents, coupon_dates, expire_matured, face_at, period_of, prev_coupon_date, state_row, through_night)


def _rate(ctx: TxContext, ccy: str) -> Decimal:
    """USD per 1 unit of ``ccy`` from the command's snapshot (1 for USD); the fill row records it."""
    if ccy == "USD":
        return Decimal(1)
    try:
        return ctx.fx.rate(ccy)
    except Exception as e:   # noqa: BLE001 - a foreign bond cannot be valued in base without its rate: refuse, never guess
        raise RejectError("FX_RATE_UNAVAILABLE", f"no USD rate for {ccy} in the mark snapshot") from e


# ---- bonds.accrue (nightly) ------------------------------------------------------------------------------------------------------------------
def h_accrue(ctx: TxContext, payload: dict) -> dict:
    """BOND_ACCRUE for one (account, instrument, night) — module docstring ACCRUAL.  Idempotent per key."""
    services = _services(ctx)
    conn = ctx.conn
    account, iid, ds = _key3(payload, "bonds.accrue")
    d = date.fromisoformat(ds)
    key = f"{account}:{iid}:{ds}"
    _open_account(conn, account)
    inst = _bond(services, conn, iid)
    terms = bond_terms(inst)
    if d > through_night(ctx.ts):
        return _skip(ctx, JOB_ACCRUE, key, "NIGHT_NOT_YET", f"{inst.symbol}: the night of {ds} is due from 00:30 ET of the next day", account, iid)
    st = state_row(conn, account, iid)
    face = face_at(conn, account, iid, d)
    if st is None:
        if face == 0:
            return _done_noop(ctx, JOB_ACCRUE, key, "NO_POSITION", f"no {inst.symbol} held at the close of {ds}")
        first = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE account_id=? AND instrument_id=?", (account, iid)).fetchone()
        through = date.fromisoformat(first["d"]) - timedelta(days=1) if first is not None and first["d"] else d - timedelta(days=1)
        cum = balance(conn, account, "1220", inst.currency, iid)
        st_through, cum_prev = through, cum
        note_state = "no accrual state (position not opened through bonds.trade): brought to the ACT/ACT figure from the 1220 balance"
    else:
        st_through, cum_prev = date.fromisoformat(st["accrued_through"]), int(st["cum_c"])
        note_state = ""
    if d <= st_through:
        return _done_noop(ctx, JOB_ACCRUE, key, "ALREADY_ACCRUED", f"{inst.symbol}: accrued through {st_through.isoformat()}")
    if d != st_through + timedelta(days=1):
        return _skip(ctx, JOB_ACCRUE, key, "ACCRUAL_GAP", f"{inst.symbol}: night {ds} requested but {(st_through + timedelta(days=1)).isoformat()} is the next unaccrued night"
                     " (accruals post in date order, never lumped)", account, iid, {"accrued_through": st_through.isoformat()})
    if d >= terms.maturity:
        return _done_noop(ctx, JOB_ACCRUE, key, "MATURED", f"{inst.symbol} matured on {terms.maturity.isoformat()}: nothing accrues from the maturity date")
    per = period_of(terms, d)
    if per is not None and not terms.zero_coupon and per[0] == d and face_at(conn, account, iid, d - timedelta(days=1)) > 0:
        row = _job_row(conn, JOB_COUPON, f"{account}:{iid}:{ds}")
        if row is None or row["status"] != "DONE":
            return _skip(ctx, JOB_ACCRUE, key, "COUPON_PENDING", f"{inst.symbol}: {ds} is a coupon date; its coupon is paid first (then the new period accrues)", account, iid,
                         {"coupon_key": f"{account}:{iid}:{ds}"})
    target = accrued_cents(face, terms, d)
    delta = target - cum_prev
    accrued4 = accrued_per_100(terms, d)
    per_txt = f"{per[0].isoformat()}..{per[1].isoformat()} ({(per[1] - per[0]).days} days)" if per is not None else "n/a"
    memo = (f"{inst.symbol} accrued interest night of {ds}: {terms.coupon_pct} % ACT/ACT, period {per_txt}, accrued {accrued4} per 100 on {face:,} face = {fmt_money(target)}"
            f" cumulative, carried {fmt_money(cum_prev)} -> posted {fmt_money(delta)} (ACCRUAL, ACCRUED)" + (f"; {note_state}" if note_state else ""))
    entry_id = None
    ccy = inst.currency
    if delta > 0:
        entry_id = ctx.post(Entry("ACCRUAL", memo, [dr(account, "1220", ccy, delta, instrument_id=iid, memo="accrued interest receivable"),
                                                   cr(account, "4300", ccy, delta, instrument_id=iid, memo=f"coupon interest night of {ds}")],
                                  f"accr:bond:{key}", ref_type=KIND, ref_id=key, business_date=ds))
    elif delta < 0:
        entry_id = ctx.post(Entry("ACCRUAL", memo, [dr(account, "4300", ccy, -delta, instrument_id=iid, memo="rounding reversal of accrued interest"),
                                                   cr(account, "1220", ccy, -delta, instrument_id=iid, memo="accrued interest receivable")],
                                  f"accr:bond:{key}", ref_type=KIND, ref_id=key, business_date=ds))
    if face > 0:
        _write_state(conn, account, iid, terms, face, d, target)
    else:
        _delete_state(conn, account, iid)
    _job_done(ctx, JOB_ACCRUE, key, "DONE", f"{fmt_money(delta)} on {face:,} face, accrued {accrued4} per 100 night of {ds}")
    _resolve_attention(ctx, ("ACCRUAL_GAP", "COUPON_PENDING", "NIGHT_NOT_YET"), key)
    ctx.touched_instruments.add((account, iid))
    ctx.touched_accounts.add(account)
    ctx.event("ACCRUED", account_id=account, instrument_id=iid, ref_type=KIND, ref_id=key, kind=KIND, night=ds, amount_c=delta, face=face, accrued_per_100=str(accrued4),
              cum_c=target, entry_id=entry_id)
    return {"status": "DONE", "posted": entry_id is not None, "skipped": entry_id is None, "key": key, "date": ds, "amount_c": delta, "posted_c": delta, "face": face,
            "accrued_per_100": str(accrued4), "cum_c": target, "cum_before_c": cum_prev, "entry_id": entry_id, "period": per_txt,
            "note": f"{fmt_money(delta)} on {face:,} face night of {ds}"}


# ---- bonds.coupon --------------------------------------------------------------------------------------------------------------------------
def h_coupon(ctx: TxContext, payload: dict) -> dict:
    """BOND_COUPON for one (account, instrument, coupon date) — module docstring COUPON.  Idempotent per key."""
    services = _services(ctx)
    conn = ctx.conn
    account, iid, cs = _key3(payload, "bonds.coupon")
    c = date.fromisoformat(cs)
    key = f"{account}:{iid}:{cs}"
    idem = f"coupon:{key}"
    _open_account(conn, account)
    inst = _bond(services, conn, iid)
    terms = bond_terms(inst)
    if terms.zero_coupon:
        return _done_noop(ctx, JOB_COUPON, key, "ZERO_COUPON", f"{inst.symbol} is a bill: no coupon")
    if c not in coupon_dates(terms, c - timedelta(days=1), c):
        raise RejectError("KEY_INVALID", f"{cs} is not a coupon date of {inst.symbol} (maturity {terms.maturity.isoformat()}, semi-annual)")
    if conn.execute("SELECT 1 FROM journal_entries WHERE idem_key=?", (idem,)).fetchone() is not None:
        _job_done(ctx, JOB_COUPON, key, "DONE", "already paid")
        return {"status": "DONE", "already": True, "skipped": True, "posted": False, "key": key}
    pay_date = services.cal.roll_following(c)
    today = ny_of(ctx.ts).date()
    if today < pay_date or ctx.ts < epoch_of(pay_date, COUPON_ET):
        return _skip(ctx, JOB_COUPON, key, "COUPON_NOT_YET", f"{inst.symbol}: the coupon of {cs} is paid on {pay_date.isoformat()} (rolled following) from {COUPON_ET} ET",
                     account, iid, {"pay_date": pay_date.isoformat()})
    face = face_at(conn, account, iid, c - timedelta(days=1))
    if face <= 0:
        return _done_noop(ctx, JOB_COUPON, key, "NO_POSITION", f"no {inst.symbol} held at the close of {(c - timedelta(days=1)).isoformat()}: no coupon due")
    st = state_row(conn, account, iid)
    through = date.fromisoformat(st["accrued_through"]) if st is not None and st["accrued_through"] else None
    if through is None or through < c - timedelta(days=1):
        return _skip(ctx, JOB_COUPON, key, "COUPON_PENDING_ACCRUAL", f"{inst.symbol}: the night of {(c - timedelta(days=1)).isoformat()} has not been accrued yet;"
                     " the coupon follows it", account, iid, {"accrued_through": None if through is None else through.isoformat()})
    cum = int(st["cum_c"]) if st is not None else 0
    coupon_c = coupon_cents(face, terms)
    income = coupon_c - cum
    prev = prev_coupon_date(terms, c)
    ccy = inst.currency
    lines = [dr(account, "1010", ccy, coupon_c, memo=f"coupon received {cs}")]
    if cum:
        lines.append(cr(account, "1220", ccy, cum, instrument_id=iid, memo="accrued interest receivable settled by the coupon"))
    if income:
        lines.append(cr(account, "4300", ccy, income, instrument_id=iid, memo="interest since the last night, earned at payment"))
    memo = (f"{inst.symbol} coupon {terms.coupon_pct} % on {face:,} face for {prev.isoformat()}..{cs} ({(c - prev).days} days ACT/ACT): {ccy} {fmt_money(coupon_c)} = carried accrued"
            f" {fmt_money(cum)} + {fmt_money(income)} earned since the last night; paid {pay_date.isoformat()}" + (" (rolled following)" if pay_date != c else "") + " (ACCRUAL)")
    entry_id = ctx.post(Entry("COUPON", memo, lines, idem, ref_type=KIND, ref_id=key))
    pos, _ = position(conn, account, iid)
    if pos > 0 and c < terms.maturity:
        _write_state(conn, account, iid, terms, pos, c, 0)
    elif st is not None:
        _write_state(conn, account, iid, terms, max(pos, 0), c, 0)
    _job_done(ctx, JOB_COUPON, key, "DONE", f"coupon {fmt_money(coupon_c)} on {face:,} face (carried {fmt_money(cum)} + {fmt_money(income)})")
    _resolve_attention(ctx, ("COUPON_NOT_YET", "COUPON_PENDING_ACCRUAL"), key)
    ctx.touched_instruments.add((account, iid))
    ctx.touched_accounts.add(account)
    ctx.event("COUPON_PAID", account_id=account, instrument_id=iid, ref_type=KIND, ref_id=key, coupon_date=cs, pay_date=pay_date.isoformat(), face=face, amount_c=coupon_c,
              carried_c=cum, income_c=income, entry_id=entry_id)
    return {"status": "DONE", "posted": True, "key": key, "coupon_date": cs, "pay_date": pay_date.isoformat(), "face": face, "amount_c": coupon_c, "carried_c": cum,
            "income_c": income, "entry_id": entry_id, "note": f"coupon {fmt_money(coupon_c)} on {face:,} face of {inst.symbol}"}


# ---- bonds.mature --------------------------------------------------------------------------------------------------------------------------
def _key2(payload: dict, what: str) -> Tuple[str, str]:
    account = str(payload.get("account_id") or "")
    iid = str(payload.get("instrument_id") or "")
    key = payload.get("key")
    if key and not (account and iid):
        parts = str(key).rsplit(":", 1)
        if len(parts) == 2:
            account, iid = parts
    if not account or not iid:
        raise RejectError("KEY_INVALID", f"{what} needs account_id and instrument_id")
    return account, iid


def _expire_if_unheld(ctx: TxContext, services, inst) -> bool:
    held = ctx.conn.execute("SELECT COUNT(*) FROM v_positions WHERE instrument_id=? AND qty <> 0", (inst.instrument_id,)).fetchone()[0]
    if int(held or 0):
        return False
    ctx.conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE instrument_id=? AND status='ACTIVE'", (ctx.ts, inst.instrument_id))
    services.instruments.refresh(inst.instrument_id, ctx.conn)
    return True


def h_mature(ctx: TxContext, payload: dict) -> dict:
    """BOND_MATURITY for one (account, instrument) — module docstring MATURITY.  Idempotent per key."""
    services = _services(ctx)
    conn = ctx.conn
    account, iid = _key2(payload, "bonds.mature")
    key = f"{account}:{iid}"
    idem_fill = f"maturity:{key}"
    _open_account(conn, account)
    inst = _bond(services, conn, iid)
    terms = bond_terms(inst)
    done = conn.execute("SELECT fill_id, entry_id FROM fills WHERE idem_key=?", (idem_fill,)).fetchone()
    if done is not None:
        _job_done(ctx, JOB_MATURITY, key, "DONE", "already redeemed")
        return {"status": "DONE", "already": True, "skipped": True, "posted": False, "key": key, "fill_id": done["fill_id"], "entry_id": done["entry_id"]}
    pay_date = services.cal.roll_following(terms.maturity)
    today = ny_of(ctx.ts).date()
    if today < pay_date or ctx.ts < epoch_of(pay_date, MATURITY_ET):
        return _skip(ctx, JOB_MATURITY, key, "MATURITY_NOT_YET", f"{inst.symbol} is redeemed on {pay_date.isoformat()} from {MATURITY_ET} ET", account, iid,
                     {"pay_date": pay_date.isoformat()})
    pos, _ = position(conn, account, iid)
    if pos <= 0:
        expired = _expire_if_unheld(ctx, services, inst)
        return _done_noop(ctx, JOB_MATURITY, key, "NO_POSITION", f"no {inst.symbol} held at maturity" + ("; instrument marked EXPIRED" if expired else ""))
    if not terms.zero_coupon and face_at(conn, account, iid, terms.maturity - timedelta(days=1)) > 0:
        row = _job_row(conn, JOB_COUPON, f"{key}:{terms.maturity.isoformat()}")
        if row is None or row["status"] != "DONE":
            return _skip(ctx, JOB_MATURITY, key, "MATURITY_PENDING_COUPON", f"{inst.symbol}: the final coupon of {terms.maturity.isoformat()} is paid before the redemption",
                         account, iid, {"coupon_key": f"{key}:{terms.maturity.isoformat()}"})
    par = Decimal(100)
    face_c = inst.contract_value_c(par, pos)
    fill_id = next_id(conn, "fill")
    conn.execute("PRAGMA defer_foreign_keys=ON")
    rl = relieve_fifo(ctx.tx, account, iid, "LONG", pos, fill_id, face_c)
    cost_relieved = sum(r.cost_relieved_c for r in rl)
    realised = sum(r.realised_c for r in rl)
    ccy = inst.currency
    lines = [dr(account, "1010", ccy, face_c, memo="redemption at par")]
    if cost_relieved:
        lines.append(cr(account, "1110", ccy, cost_relieved, instrument_id=iid, memo="cost relieved FIFO"))
    if realised:
        lines.append(cr(account, "4000", ccy, realised, instrument_id=iid, memo="realised at maturity (100 − cost per 100)"))
    memo = f"MATURITY {pos:,} {inst.symbol} at 100 on {pay_date.isoformat()}: cost {fmt_money(cost_relieved)}, realised {fmt_money(realised)}, no fee (FIFO)"
    entry_id = ctx.post(Entry("MATURITY", memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
    v = BondTrade(account, {}, "", inst, terms, "SELL", pos, pay_date, pay_date, par, Decimal("0.0000"), face_c, 0, 0, "no fee at maturity", None, pos, None,
                  currency=ccy, mark_price=par, fx_rate=_rate(ctx, ccy))
    _insert_fill(ctx, v, fill_id, "SELL", entry_id, idem_fill, origin="MATURITY", basis="par", settle=None, price=par, gross_c=face_c, fee_c=0,
                 quote_json=dumps({"basis": "par", "price": "100", "maturity": terms.maturity.isoformat(), "pay_date": pay_date.isoformat(), "terms": terms.to_dict()}),
                 trade_date=pay_date)
    _delete_state(conn, account, iid)
    expired = _expire_if_unheld(ctx, services, inst)
    execution.auto_resolve_shortfall(ctx, account, ccy)
    _job_done(ctx, JOB_MATURITY, key, "DONE", f"MATURITY {pos:,} @ 100: {fmt_money(face_c)} received, realised {fmt_money(realised)}")
    _resolve_attention(ctx, ("MATURITY_NOT_YET", "MATURITY_PENDING_COUPON"), key)
    ctx.touched_instruments.add((account, iid))
    ctx.touched_accounts.add(account)
    ctx.event("BOND_MATURED", account_id=account, instrument_id=iid, ref_type="fill", ref_id=fill_id, face=pos, amount_c=face_c, cost_c=cost_relieved, realised_c=realised,
              entry_id=entry_id, pay_date=pay_date.isoformat(), expired=expired)
    return {"status": "DONE", "posted": True, "key": key, "fill_id": fill_id, "entry_id": entry_id, "face": pos, "amount_c": face_c, "cost_relieved_c": cost_relieved,
            "realised_c": realised, "reliefs": [r.__dict__ for r in rl], "pay_date": pay_date.isoformat(), "instrument_status": "EXPIRED" if expired else "ACTIVE"}


# ---- bonds.list (light: instruments + jobs_done only) -------------------------------------------------------------------------------------------
def h_list(ctx: TxContext, payload: dict) -> dict:
    """BOND_LISTING for one month (key ``yyyy-mm``): expires the matured bonds nobody holds (ACTIVE or DELISTED rows).  The
    monthly model benchmarks of Build 1 are retired: the master is the real Treasury list (products/treasuries.py), and the
    generic corporate / foreign rows of the month are listed by the debt refresher, never here."""
    services = _services(ctx)
    conn = ctx.conn
    key = str(payload.get("month") or payload.get("key") or "")
    try:
        y, m = (int(x) for x in key.split("-")[:2])
        first = date(y, m, 1)
    except (TypeError, ValueError) as e:
        raise RejectError("KEY_INVALID", f"bonds.list needs a month yyyy-mm (got {key!r})") from e
    listing = services.cal.roll_following(first)
    today = ny_of(ctx.ts).date()
    if today < listing:
        return _skip(ctx, JOB_LISTING, key, "LISTING_NOT_YET", f"the housekeeping of {key} runs on {listing.isoformat()} (first business day)", None, None)
    expired = expire_matured(conn, services.instruments, today, ctx.ts)
    _job_done(ctx, JOB_LISTING, key, "DONE", f"expired {len(expired)} matured bond(s) nobody holds; the master itself comes from fiscaldata (debt refresher)")
    _resolve_attention(ctx, ("CURVE_MISSING", "LISTING_NOT_YET"), key)
    if expired:
        ctx.event("BOND_LISTINGS", ref_type="housekeeping", ref_id=key, listed=0, ids=[], expired=expired, listing_date=listing.isoformat())
    return {"status": "DONE", "key": key, "listing_date": listing.isoformat(), "listed": 0, "ids": [], "expired": expired, "reload_master": bool(expired), "posted": False,
            "note": "monthly model benchmarks are retired (the master is the real Treasury list)"}


# ---- lifecycle key helpers (ProductJob contracts: tuples in the key's field order) -------------------------------------------------------------------
def _pairs(conn, with_fills: bool = False) -> List[Tuple[str, str]]:
    """(account, instrument) pairs of OPEN accounts / lifetimes: bond accrual states and open BOND positions (plus every
    pair with BOND fills when ``with_fills``)."""
    pairs = set()
    for r in conn.execute("SELECT s.ref_id FROM accrual_state s WHERE s.kind=?", (KIND,)).fetchall():
        ref = str(r["ref_id"])
        account, iid = ref.rsplit(":", 1)
        pairs.add((account, iid))
    for r in conn.execute("SELECT v.account_id, v.instrument_id FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id WHERE i.kind='BOND' AND v.qty <> 0").fetchall():
        pairs.add((r["account_id"], r["instrument_id"]))
    if with_fills:
        for r in conn.execute("SELECT DISTINCT f.account_id, f.instrument_id FROM fills f JOIN instruments i ON i.instrument_id=f.instrument_id WHERE i.kind='BOND'").fetchall():
            pairs.add((r["account_id"], r["instrument_id"]))
    out = []
    for account, iid in sorted(pairs):
        row = conn.execute("SELECT a.status, l.status ls FROM accounts a JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE a.account_id=?", (account,)).fetchone()
        if row is not None and row["status"] == "OPEN" and row["ls"] == "OPEN":
            out.append((account, iid))
    return out


def accrue_keys(conn, now_ts: int, instruments=None) -> List[Tuple[str, str, str]]:
    """``(account_id, instrument_id, night)`` triples due: for every bond position, each night after ``accrued_through``
    (or after the first fill) up to the last night due, before maturity, not DONE."""
    through = through_night(now_ts)
    out: List[Tuple[str, str, str]] = []
    for account, iid in _pairs(conn):
        st = state_row(conn, account, iid)
        if st is not None and st["accrued_through"]:
            start = date.fromisoformat(st["accrued_through"]) + timedelta(days=1)
        else:
            first = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE account_id=? AND instrument_id=?", (account, iid)).fetchone()
            if first is None or not first["d"]:
                continue
            start = date.fromisoformat(first["d"])
        end = through
        if instruments is not None:
            inst = instruments.get(iid, conn)
            if inst is not None:
                try:
                    end = min(end, bond_terms(inst).maturity - timedelta(days=1))
                except RejectError:
                    continue
        d = start
        while d <= end:
            row = _job_row(conn, JOB_ACCRUE, f"{account}:{iid}:{d.isoformat()}")
            if row is None or row["status"] != "DONE":
                out.append((account, iid, d.isoformat()))
            d += timedelta(days=1)
    return out


def coupon_keys(conn, now_ts: int, cal, instruments) -> List[Tuple[str, str, str]]:
    """``(account_id, instrument_id, coupon_date)`` triples due: coupon dates after the first fill, with face held at
    the close of the day before, whose payment date (rolled following) has come (00:40 ET), not DONE."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str, str]] = []
    for account, iid in _pairs(conn, with_fills=True):
        inst = instruments.get(iid, conn)
        if inst is None:
            continue
        try:
            terms = bond_terms(inst)
        except RejectError:
            continue
        first = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE account_id=? AND instrument_id=?", (account, iid)).fetchone()
        if first is None or not first["d"]:
            continue
        for c in coupon_dates(terms, date.fromisoformat(first["d"]), today):
            pay = cal.roll_following(c)
            if now < epoch_of(pay, COUPON_ET):
                continue
            if face_at(conn, account, iid, c - timedelta(days=1)) <= 0:
                continue
            row = _job_row(conn, JOB_COUPON, f"{account}:{iid}:{c.isoformat()}")
            if row is None or row["status"] != "DONE":
                out.append((account, iid, c.isoformat()))
    return out


def maturity_keys(conn, now_ts: int, cal, instruments) -> List[Tuple[str, str]]:
    """``(account_id, instrument_id)`` pairs with a bond position whose redemption date (maturity rolled following,
    00:45 ET) has come, not DONE."""
    now = int(now_ts)
    out: List[Tuple[str, str]] = []
    for r in conn.execute("SELECT v.account_id, v.instrument_id FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id JOIN accounts a ON a.account_id=v.account_id"
                          " JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE i.kind='BOND' AND v.qty <> 0 AND a.status='OPEN' AND l.status='OPEN'"
                          " ORDER BY v.account_id, v.instrument_id").fetchall():
        inst = instruments.get(r["instrument_id"], conn)
        if inst is None:
            continue
        try:
            terms = bond_terms(inst)
        except RejectError:
            continue
        if now < epoch_of(cal.roll_following(terms.maturity), MATURITY_ET):
            continue
        row = _job_row(conn, JOB_MATURITY, f"{r['account_id']}:{r['instrument_id']}")
        if row is None or row["status"] != "DONE":
            out.append((r["account_id"], r["instrument_id"]))
    return out


def listing_keys(conn, now_ts: int, cal) -> List[Tuple[str]]:
    """``(yyyy-mm,)`` for the current month from 00:15 ET of its first business day, not DONE."""
    now = int(now_ts)
    today = ny_of(now).date()
    listing = cal.roll_following(today.replace(day=1))
    if now < epoch_of(listing, LISTING_ET):
        return []
    key = today.strftime("%Y-%m")
    row = _job_row(conn, JOB_LISTING, key)
    if row is not None and row["status"] == "DONE":
        return []
    return [(key,)]



__all__ = ["h_accrue", "h_coupon", "h_mature", "h_list", "accrue_keys", "coupon_keys", "maturity_keys", "listing_keys"]
