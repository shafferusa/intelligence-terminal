"""Listed options: margin, expiry, exercise, assignment, the chain view (TERMINAL_DESIGN.md §2.12 products/options.py, §5.4,
§11.2 OPTION_EXPIRY / OPTION_MARGIN_MARK, §13.3, §19.7; conventions EXERCISE, OCC, OPT_MARGIN).

Conventions stated here (the §1.3 sentences that bite in this module)
- Contracts come from the Cboe chain (``ensure_option``): equity / ETF options AMERICAN, PHYSICAL, multiplier 100; index
  options (SPX, SPXW, NDX, RUT, VIX) EUROPEAN, CASH.  Executions happen only at the Cboe NBBO (WP-02's matcher); a Cboe
  outage makes options ``unavailable`` and nothing here invents a fill or a mark.
- OPT_MARGIN (``margin_requirement_c``), per short contract, per share then × multiplier: naked call
  ``max(premium + 0.20 S − max(0, K − S), premium + 0.10 S)``; naked put ``max(premium + 0.20 S − max(0, S − K),
  premium + 0.10 K)``; index options 0.15 / 0.10; covered call (long shares ≥ 100 per contract in the same account)
  0 with the shares reserved (SHARES_RESERVED); vertical spread (same underlying, expiry and right with a long
  contract covering) = width × 100 × n net of the opening credit, 0 when the long strike covers fully; a
  cash-secured put is the naked formula.  ``premium`` = the contract's current mark (Cboe mid, else model); when no
  mark exists the contract's own cost basis stands in, labelled; ``S`` = the underlying's mark — unavailable raises
  MARK_UNAVAILABLE, never a number.  One (underlying, expiry) with written calls AND puts: both sides fully spread
  (iron condor) → the larger side; otherwise the sides add (a conservative, disclosed approximation of Reg T).
  The requirement is RESERVED (decision s7.6, SR-13): ``execution.recompute_option_margin`` upserts ``margin_state``
  and ``accounting.reserved_c`` subtracts it from available and buying power - no cash moves, nothing is posted to 1310
  (L16 checks the stored requirement against its own groups).  An increase own cash + Treasury + PB cannot cover is
  refused at a fill; in a lifecycle run it opens ONE ERROR FUNDING_SHORTFALL (no forced liquidation).  A database that
  still holds 1310 balances from an earlier version is migrated ONCE at start (``options.migrate_margin``: every OPEN
  account's 1310 released to 1010, idem ``migrate:omargin:{account}``, memo "decision 6: option margin is a
  reservation").
- OCC (``options.expire``, key ``{account}:{option_id}``): the underlying's OBSERVED close of the expiry date decides —
  the ``marks`` CLOSE row (MARKS_RECORD), else a cached record / mark whose quote_time is on that date at or after
  the session end − 60 s (then recorded as the CLOSE row); intrinsic ≥ 0.01 → EXERCISE (long) / ASSIGNMENT (short)
  at the strike, commission-free, premium rolled into the stock's basis or proceeds (§5.4); < 0.01 → EXPIRY fill at
  0 (long realises −cost, short +premium).  Index options settle the intrinsic in cash.  No observed close → SKIPPED
  ``EXPIRY_PENDING_CLOSE`` with an attention item, retried; a put exercise / call assignment that would leave the
  account short without a borrow SKIPS (``EXERCISE_NEEDS_BORROW`` / ``ASSIGNMENT_NEEDS_BORROW``); nothing is
  cash-settled or invented in their place.  Early assignment of short options is not simulated (limitation).
- EXERCISE (``options.exercise``): the owner may exercise a long AMERICAN option early, during its session, at the
  strike (the same bookings, idem ``exercise:{account}:{inst}:{request_id}``); out-of-the-money exercises are allowed
  and warned.
- Fill rows: an exercise / assignment writes two fills on ONE journal entry — the option leg at price 0 (its premium
  is rolled, not realised) and the stock leg at the strike with a T+1 settlement row; the lot reliefs of the option
  carry the rolled premium as proceeds so they net to zero realised (per-lot rounding may leave ±1 cent per lot that
  nets to zero across the reliefs; the ledger's 4000 line is the authority).
- Margin recomputes inside the lifecycle commands follow the overdraft rule (§11.6): ``options.expire`` and
  ``options.margin_mark`` recompute the account ONCE per command through ``execution.recompute_option_margin(...,
  overdraft=True)`` — the EXPIRY fill is applied with ``defer_margin`` so the fill path does not recompute first
  without the rule — and a reservation that cannot be covered opens an ERROR ``FUNDING_SHORTFALL``.  The owner's early
  exercise is a user action: an uncovered increase refuses it.
- ``expiry_keys`` lists written contracts (negative positions) before long ones, so a spread's long leg still covers
  the short while the short is assigned or lapses: no transient naked requirement is posted between two keys.
- Position cost in the margin formula is read as ``|cost_c|``: ``v_positions`` carries −cost for shorts, while the
  strategy preview's synthetic legs (WP-24) carry a positive cost — both mean the gross premium received / paid.
- The chain view (``chain_view``, ``leg_dict``, ``underlying_dict``) hands the UI the §15 shape: each contract is the
  ChainRow plus its OCC ``instrument_id``, ``kind``, the chain's observation time and provenance (``quote_time``,
  ``fetched_at``, ``age_s``, ``source``, ``method`` chain, ``delay_label``) and a ``quality`` that is ``unavailable``
  for a contract without a usable NBBO; the underlying is a QuoteRecord-shaped dict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple

from .. import config, execution, tables
from ..books import account_row
from ..clock import epoch_of, ny_of, trade_date as _trade_date
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..instruments import INDEX_OPTION_ROOTS, Instrument, parse_occ
from ..ledger import Entry, LedgerError, cr, dr, open_lot, open_lots, position, relieve_fifo
from ..units import fmt_money, from_c, mul_c, qprice, to_c

EQUITY_KINDS = execution.EQUITY_KINDS
JOB_EXPIRY = "OPTION_EXPIRY"
JOB_MARGIN = "OPTION_MARGIN_MARK"
EXPIRY_TIME_ET = {"US_EQUITY_OPTIONS": "16:20", "US_INDEX_OPTIONS": "16:35"}     # §11.2: from 16:20 (index 16:35)
NAKED_PCT = {"equity": Decimal("0.20"), "index": Decimal("0.15")}
FLOOR_PCT = Decimal("0.10")
CHAIN_STALE_S = 900
PX8 = Decimal("0.00000001")
PX4 = Decimal("0.0001")

_services = None       # bound by register(bus): the fallback instrument master for margin_requirement_c


class ServiceUnavailable(RejectError):
    """The chain provider refused the honest User-Agent -> HTTP 503."""
    http = 503


# ---- helpers ----------------------------------------------------------------------------------------------------------
def is_index_option(inst: Instrument) -> bool:
    """Cash-settled index option (SPX, SPXW, NDX, RUT, VIX): EUROPEAN / CASH, session US_INDEX_OPTIONS."""
    return (inst.exercise or "").upper() == "CASH" or bool((inst.spec or {}).get("index")) or inst.session_group == "US_INDEX_OPTIONS"


def underlying_id_of_root(root: str) -> str:
    """OCC root -> underlying instrument id (``SPXW`` -> ``SPX``; other roots are the ticker as spelled)."""
    root = (root or "").upper()
    if root == "SPXW":
        return "SPX"
    return root


def _services_of(ctx: TxContext):
    s = ctx.services if ctx.services is not None else _services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _master(marks, instruments=None):
    if instruments is not None:
        return instruments
    prov = getattr(marks, "_p", None)
    m = getattr(prov, "instruments", None) if prov is not None else None
    if m is not None:
        return m
    if _services is not None and getattr(_services, "instruments", None) is not None:
        return _services.instruments
    raise RejectError("NO_SERVICES", "margin_requirement_c needs an instrument master: pass instruments= or call options.register(bus)")


def _mark(marks, instrument_id: str) -> Tuple[Optional[Decimal], str, Optional[int]]:
    """``(price, quality, quote_time)`` from a MarkSet; ``(None, 'unavailable', None)`` when there is none."""
    if marks is None or not hasattr(marks, "mark"):
        return None, "unavailable", None
    try:
        v = marks.mark(instrument_id)
    except Exception:   # noqa: BLE001 — a failing mark is an unavailable mark
        return None, "unavailable", None
    if v is None or v.mark is None or v.quality == "unavailable":
        return None, "unavailable", None
    return qprice(v.mark), v.quality, v.quote_time


def _round_half_up(x: Decimal) -> int:
    from decimal import ROUND_HALF_UP
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _s(x) -> Optional[str]:
    return None if x is None else str(x)


# ---- §13.3 margin ------------------------------------------------------------------------------------------------------
@dataclass
class _Short:
    inst: Instrument
    n: int                 # contracts written (positive)
    proceeds_c: int        # gross premium received for the n contracts (the SHORT lots' cost_c, positive)
    remaining: int


@dataclass
class _Long:
    inst: Instrument
    n: int
    cost_c: int            # gross premium paid for the n contracts
    used: int = 0


def _naked_per_share(right: str, mark: Decimal, S: Decimal, K: Decimal, index: bool) -> Tuple[Decimal, Decimal, Decimal, str]:
    """``(per_share, a, b, formula)`` of the naked rule; ``a`` = premium + pct S − OTM, ``b`` = premium + 0.10 × (S | K)."""
    pct = NAKED_PCT["index" if index else "equity"]
    if right == "C":
        otm = max(Decimal(0), K - S)
        a = mark + pct * S - otm
        b = mark + FLOOR_PCT * S
        formula = f"max(premium + {pct} × S − max(0, K − S), premium + 0.10 × S)"
    else:
        otm = max(Decimal(0), S - K)
        a = mark + pct * S - otm
        b = mark + FLOOR_PCT * K
        formula = f"max(premium + {pct} × S − max(0, S − K), premium + 0.10 × K)"
    return max(a, b), a, b, formula


def _share(total_c: int, take: int, n: int) -> int:
    """The pro-rata share (cents) of ``take`` contracts out of ``n`` — the whole amount when take == n."""
    if take >= n:
        return int(total_c)
    return to_c(Decimal(int(total_c)) / Decimal(100) * Decimal(take) / Decimal(n))


def margin_requirement_c(positions, marks, instruments=None) -> Tuple[int, dict]:
    """§13.3 for one account: ``(requirement_c, detail)``.  ``positions`` are the account's ``PositionView`` rows (every
    kind: the equity rows cover calls), ``marks`` the command's MarkSet; ``instruments`` (optional) the master, else
    the one behind the snapshot / the bound runtime.  The detail names every rule, input and reserved share so the
    preview and the Cash page can show the arithmetic; it is JSON-serialisable (Decimals as strings)."""
    master = _master(marks, instruments)
    shares: Dict[str, int] = {}
    opts: List[Tuple[Any, Instrument]] = []
    for p in positions:
        kind = getattr(p, "kind", None)
        if kind == "OPTION":
            inst = master.get(p.instrument_id)
            if inst is None:
                raise RejectError("INSTRUMENT_UNKNOWN", f"option {p.instrument_id} is not in the master")
            opts.append((p, inst))
        elif kind in EQUITY_KINDS and int(p.qty) > 0:
            shares[p.instrument_id] = shares.get(p.instrument_id, 0) + int(p.qty)
    detail: dict = {"requirement_c": 0, "rule": tables.CONVENTIONS.get("OPT_MARGIN", ""), "groups": [], "shares_reserved": {}, "qualities": []}
    if not any(int(p.qty) < 0 for p, _ in opts):
        detail["note"] = "no written options: no margin requirement"
        return 0, detail
    groups: Dict[Tuple[str, str], Dict[str, Dict[str, list]]] = {}
    for p, inst in opts:
        key = (inst.underlying_id or "", inst.expiry or "")
        g = groups.setdefault(key, {"C": {"short": [], "long": []}, "P": {"short": [], "long": []}})
        right = inst.right or "C"
        qty = int(p.qty)
        if qty < 0:
            g[right]["short"].append(_Short(inst, -qty, abs(int(p.cost_c)), -qty))     # |cost|: proceeds received (see the docstring)
        elif qty > 0:
            g[right]["long"].append(_Long(inst, qty, abs(int(p.cost_c))))
    reserved: Dict[str, int] = {}
    total = 0
    qualities: List[str] = []
    for (uid, expiry), sides in sorted(groups.items()):
        if not sides["C"]["short"] and not sides["P"]["short"]:
            continue
        S, s_quality, s_time = _mark(marks, uid)
        if S is None:
            raise RejectError("MARK_UNAVAILABLE", f"no mark for the underlying {uid}: the option margin cannot be computed (nothing is assumed)",
                              {"underlying_id": uid, "expiry": expiry})
        qualities.append(s_quality)
        gd: dict = {"underlying_id": uid, "expiry": expiry, "S": str(S), "S_quality": s_quality, "S_quote_time": s_time, "sides": {}, "items": []}
        side_req: Dict[str, int] = {}
        all_spread = True
        for right in ("C", "P"):
            shorts: List[_Short] = sorted(sides[right]["short"], key=lambda s: s.inst.strike or 0)
            if not shorts:
                side_req[right] = 0
                continue
            longs: List[_Long] = sorted(sides[right]["long"], key=lambda l: (l.inst.strike or 0) * (1 if right == "C" else -1))
            req_side = 0
            for sh in shorts:
                K = sh.inst.strike if sh.inst.strike is not None else Decimal(0)
                mult = sh.inst.multiplier
                index = is_index_option(sh.inst)
                n_short = sh.n
                # 1. covered calls: long shares of the underlying in the same account (reserved while the call is open)
                if right == "C" and not index:
                    free = shares.get(uid, 0) - reserved.get(uid, 0)
                    per = int(mult) if mult == mult.to_integral_value() else 0
                    cover = min(sh.remaining, free // per) if per > 0 else 0
                    if cover > 0:
                        reserved[uid] = reserved.get(uid, 0) + cover * per
                        sh.remaining -= cover
                        gd["items"].append({"instrument_id": sh.inst.instrument_id, "right": right, "strike": str(K), "n": cover, "rule": "covered_call",
                                            "requirement_c": 0, "covered_by": f"{cover * per:,} long shares of {uid} (SHARES_RESERVED)"})
                # 2. vertical spreads: a long contract of the same expiry and right covers (fully when its strike is at or inside)
                for lg in longs:
                    if sh.remaining == 0:
                        break
                    avail = lg.n - lg.used
                    if avail <= 0:
                        continue
                    take = min(sh.remaining, avail)
                    KL = lg.inst.strike if lg.inst.strike is not None else Decimal(0)
                    fully = (KL <= K) if right == "C" else (KL >= K)
                    credit_c = _share(sh.proceeds_c, take, n_short) - _share(lg.cost_c, take, lg.n)
                    if fully:
                        req = 0
                        rule = "spread_covered"
                        width = Decimal(0)
                    else:
                        width = abs(KL - K)
                        req = max(0, mul_c(take, width, mult) - credit_c)
                        rule = "vertical_spread"
                    gd["items"].append({"instrument_id": sh.inst.instrument_id, "right": right, "strike": str(K), "n": take, "rule": rule, "requirement_c": req,
                                        "covered_by": lg.inst.instrument_id, "long_strike": str(KL), "width": str(width), "credit_c": credit_c,
                                        "formula": "width × multiplier × n − net credit (0 when the long strike covers fully)"})
                    req_side += req
                    lg.used += take
                    sh.remaining -= take
                # 3. naked (cash-secured puts show the same formula)
                if sh.remaining > 0:
                    all_spread = False
                    mark, m_quality, _ = _mark(marks, sh.inst.instrument_id)
                    if mark is None:
                        per_share_basis = Decimal(sh.proceeds_c) / Decimal(100) / Decimal(n_short) / mult
                        mark = qprice(per_share_basis)
                        m_quality, premium_source = "unavailable", "cost basis (no mark for the contract)"
                    else:
                        premium_source = f"mark ({m_quality})"
                    per_share, a, b, formula = _naked_per_share(right, mark, S, K, index)
                    req = to_c(per_share * mult * Decimal(sh.remaining))
                    qualities.append(m_quality)
                    gd["items"].append({"instrument_id": sh.inst.instrument_id, "right": right, "strike": str(K), "n": sh.remaining,
                                        "rule": ("index_" if index else "naked_") + ("call" if right == "C" else "put"), "requirement_c": req,
                                        "premium": str(mark), "premium_source": premium_source, "S": str(S), "K": str(K), "a": str(a.quantize(PX4)),
                                        "b": str(b.quantize(PX4)), "per_share": str(per_share.quantize(PX4)), "multiplier": str(mult), "formula": formula})
                    req_side += req
                    sh.remaining = 0
            side_req[right] = req_side
        both = bool(sides["C"]["short"]) and bool(sides["P"]["short"])
        combine = "max" if (both and all_spread) else "sum"
        g_req = max(side_req["C"], side_req["P"]) if combine == "max" else side_req["C"] + side_req["P"]
        gd["sides"] = {"call_c": side_req["C"], "put_c": side_req["P"], "combine": combine,
                       "note": "both sides are spreads: the larger side applies (an iron condor loses on one side only)" if combine == "max"
                       else "sides add (naked legs present or one side only)"}
        gd["requirement_c"] = g_req
        detail["groups"].append(gd)
        total += g_req
    detail["requirement_c"] = total
    detail["shares_reserved"] = reserved
    detail["qualities"] = sorted(set(qualities))
    return total, detail


def reserved_shares(services, account_id: str, instrument_id: Optional[str] = None):
    """Shares reserved by covered calls per the last ``margin_state`` (``{instrument_id: qty}`` or one instrument's qty).
    WP-02's ``orders.validate`` may subtract these from the sellable position (SHARES_RESERVED)."""
    with services.db.reader() as conn:
        row = conn.execute("SELECT options_detail_json FROM margin_state WHERE account_id=?", (account_id,)).fetchone()
    try:
        res = dict((json.loads(row["options_detail_json"]) if row else {}).get("shares_reserved") or {})
    except (TypeError, ValueError):
        res = {}
    if instrument_id is None:
        return {k: int(v) for k, v in res.items()}
    return int(res.get(instrument_id, 0))


# ---- jobs_done / attention bookkeeping ---------------------------------------------------------------------------------------
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


def _skip(ctx: TxContext, job: str, key: str, code: str, message: str, account_id: str, ref_id: str, details: Optional[dict] = None) -> dict:
    """SKIPPED with a note, one open WARN attention per (code, key) and an event; retried by the scheduler."""
    _job_done(ctx, job, key, "SKIPPED", f"{code}: {message}")
    existing = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
    attention_id = int(existing["id"]) if existing else ctx.attention("WARN", "options", code, message,
                                                                          dict(details or {}, account_id=account_id, ref_type="option", ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=account_id, instrument_id=ref_id, ref_type="job", ref_id=key, job=job, code=code, message=message)
    return {"status": "SKIPPED", "code": code, "message": message, "key": key, "attention_id": attention_id}


# ---- the observed close (§11.4) ---------------------------------------------------------------------------------------------
def observed_close(ctx: TxContext, services, under: Instrument, expiry: date) -> Optional[dict]:
    """The underlying's CLOSE of ``expiry``: the ``marks`` CLOSE row, else a cached record / mark with ``quote_time`` on
    that date at or after the session end − 60 s (recorded as the CLOSE row); None when nothing was observed."""
    conn = ctx.conn
    row = conn.execute("SELECT price, quality, source, method, quote_time, fetched_at, note FROM marks WHERE instrument_id=? AND mark_date=? AND kind='CLOSE'",
                       (under.instrument_id, expiry.isoformat())).fetchone()
    if row is not None:
        return {"underlying_id": under.instrument_id, "close": str(qprice(row["price"])), "mark_date": expiry.isoformat(), "source": row["source"],
                "method": row["method"], "quote_time": row["quote_time"], "quality": "close", "basis": "marks CLOSE row", "note": row["note"]}
    close_ts = ctx.sessions.session_close(under.session_group, expiry)
    if close_ts is None:
        return None
    candidates: List[Tuple[Decimal, str, str, Optional[int], Optional[int]]] = []
    rec = ctx.marks.quote(under.instrument_id) if hasattr(ctx.marks, "quote") else None
    if rec is not None and rec.last is not None and rec.quote_time is not None and rec.method in ("direct", "daily_close"):
        candidates.append((qprice(rec.last), rec.source or "", rec.method, int(rec.quote_time), int(rec.fetched_at or ctx.ts)))
    px, quality, qt = _mark(ctx.marks, under.instrument_id)
    if px is not None and qt is not None and quality in ("delayed", "close", "stale", "live"):
        mv = ctx.marks.mark(under.instrument_id)
        if mv.method in ("direct", "daily_close", "snapshot"):
            candidates.append((px, mv.source or "", mv.method, int(qt), int(mv.fetched_at or ctx.ts)))
    for price, source, method, quote_time, fetched_at in candidates:
        if ny_of(quote_time).date() == expiry and quote_time >= int(close_ts) - 60:
            conn.execute("INSERT OR IGNORE INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (under.instrument_id, expiry.isoformat(), "CLOSE", str(price), under.currency, "close", source, method, quote_time, fetched_at,
                          "recorded by options.expire from the cached observation"))
            return {"underlying_id": under.instrument_id, "close": str(price), "mark_date": expiry.isoformat(), "source": source, "method": method,
                    "quote_time": quote_time, "quality": "close", "basis": "cached observation on the expiry date at/after the session end − 60 s", "note": None}
    return None


def intrinsic_of(right: str, S: Decimal, K: Decimal) -> Decimal:
    """``max(0, S − K)`` for a call, ``max(0, K − S)`` for a put (per share)."""
    return max(Decimal(0), S - K) if right == "C" else max(Decimal(0), K - S)


# ---- postings ----------------------------------------------------------------------------------------------------------------
def _fifo_cost(conn, account_id: str, instrument_id: str, side: str, qty: int) -> int:
    """The cost ``relieve_fifo`` will relieve for ``qty`` (a dry run of the same allocation)."""
    lots = open_lots(conn, account_id, instrument_id, side)
    left, total = int(qty), 0
    for r in lots:
        if left == 0:
            break
        remaining = int(r["qty_remaining"])
        take = min(left, remaining)
        total += int(r["cost_c"]) if take == remaining else to_c(Decimal(int(r["cost_c"])) / Decimal(100) * Decimal(take) / Decimal(remaining))
        left -= take
    if left:
        raise LedgerError("INSUFFICIENT_LOTS", f"{account_id} {instrument_id}: {side} lots hold less than {qty}")
    return total


def _insert_fill(ctx: TxContext, fill_id: str, account_id: str, inst: Instrument, effect: str, qty: int, price: Decimal, gross_c: int, tdate: date,
                 settle_date: Optional[date], origin: str, basis: str, quote: dict, entry_id: int, idem: str, note: str) -> None:
    ctx.conn.execute(
        "INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
        " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,NULL,?,?,?,?,?,?,0,?,'1',?,?,?,?,?,?,NULL,NULL,?,?,?)",
        (fill_id, account_id, inst.instrument_id, effect, int(qty), str(qprice(price)), int(gross_c), inst.currency, ctx.ts, tdate.isoformat(),
         settle_date.isoformat() if settle_date else None, origin, basis, dumps(quote), note, entry_id, idem))
    ctx.touched_instruments.add((account_id, inst.instrument_id))
    ctx.touched_accounts.add(account_id)


def _settlement(ctx: TxContext, fill_id: str, account_id: str, ccy: str, amount_c: int, gl: str, due: date) -> str:
    sid = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (sid, fill_id, None, account_id, "CASH", ccy, int(amount_c), gl, due.isoformat()))
    return sid


def _unit_cost(cost_c: int, qty: int) -> Decimal:
    unit = (Decimal(int(cost_c)) / Decimal(100) / Decimal(int(qty))).quantize(PX8, rounding=ROUND_HALF_EVEN)
    if mul_c(int(qty), unit) != int(cost_c):
        raise LedgerError("LOT_ROUNDING", f"a lot of {qty:,} at {unit} does not reproduce the cost {fmt_money(cost_c)} to the cent")
    return unit


def _trim_working_sells(ctx: TxContext, account_id: str, instrument_id: str, sides: Tuple[str, ...], holding: int, why: str) -> List[dict]:
    """TX-3: after an exercise / assignment delivered shares (or closed option contracts), the account's working ``sides`` orders
    on ``instrument_id`` may sell more than it still holds (``holding``); the newest are reduced or CANCELLED first, each with an
    order event (``why``) and an event, so no order is left resting on shares that are gone (the matcher would otherwise
    retry an OVERSELL on every pass).  Returns what changed."""
    conn = ctx.conn
    q = ",".join("?" for _ in sides)
    rows = conn.execute(f"SELECT order_id, qty, filled_qty, status, side FROM orders WHERE account_id=? AND instrument_id=? AND side IN ({q})"
                        " AND status IN ('WORKING','PARTIAL','WAITING_PARENT','SUSPENDED') ORDER BY created_ts DESC, order_id DESC",
                        [account_id, instrument_id] + list(sides)).fetchall()
    excess = sum(int(r["qty"]) - int(r["filled_qty"]) for r in rows) - max(0, int(holding))
    out: List[dict] = []
    for r in rows:
        if excess <= 0:
            break
        rem = int(r["qty"]) - int(r["filled_qty"])
        cut = min(rem, excess)
        excess -= cut
        oid = r["order_id"]
        if cut >= rem:                 # nothing of it can sell any more: the remainder is cancelled (a partial fill keeps its fills)
            conn.execute("UPDATE orders SET status='CANCELLED', reason=?, updated_ts=? WHERE order_id=?", (why, ctx.ts, oid))
            conn.execute("INSERT INTO order_events(order_id, ts, from_status, to_status, note) VALUES (?,?,?,?,?)",
                         (oid, ctx.ts, r["status"], "CANCELLED", f"{why}: the {rem:,} still to sell are no longer held"))
            ctx.event("ORDER_CANCELLED", account_id=account_id, instrument_id=instrument_id, ref_type="order", ref_id=oid, reason=why)
            out.append({"order_id": oid, "status": "CANCELLED", "cut": cut})
        else:
            new_qty = int(r["qty"]) - cut
            conn.execute("UPDATE orders SET qty=?, updated_ts=? WHERE order_id=?", (new_qty, ctx.ts, oid))
            conn.execute("INSERT INTO order_events(order_id, ts, from_status, to_status, note) VALUES (?,?,?,?,?)",
                         (oid, ctx.ts, r["status"], r["status"], f"{why}: reduced by {cut:,} to {new_qty:,}"))
            ctx.event("ORDER_RESIZED", account_id=account_id, instrument_id=instrument_id, ref_type="order", ref_id=oid, qty=new_qty, reason=why)
            out.append({"order_id": oid, "status": r["status"], "qty": new_qty, "cut": cut})
    return out


def _physical(ctx: TxContext, services, account_id: str, opt: Instrument, under: Instrument, signed_qty: int, obs: dict, idem_base: str,
              origin: str, note: str, overdraft: bool = True, trade_date: Optional[date] = None) -> dict:
    """EXERCISE / ASSIGNMENT with physical delivery (§5.4): one entry, two fill rows (option at 0, stock at the strike), the
    stock lot or FIFO relief, the T+1 settlement row, funding for a purchase, a borrow for a short delivery.  Returns the
    result dict or a ``{"skip": (code, message)}`` when a borrow is needed and none can be opened.  ``trade_date`` (LC-1,
    OPTION_EXPIRY): the expiry date is the trade date and the business date whenever the job runs, and settlement is
    ``cal.settlement_date(expiry, settle_days)`` - an expiry processed on Monday still trades Friday and settles Monday."""
    conn = ctx.conn
    n = abs(int(signed_qty))
    long = signed_qty > 0
    right = opt.right or "C"
    K = opt.strike
    mult = opt.multiplier
    Q = int((Decimal(n) * mult).to_integral_value())
    ccy = under.currency
    KQ = mul_c(Q, K, Decimal(1), under.price_scale)
    stock_buy = (long and right == "C") or ((not long) and right == "P")
    pos_qty, _ = position(conn, account_id, under.instrument_id)
    tdate = trade_date or _trade_date(ctx.now, under.session_group, ctx.cal)
    bdate = trade_date.isoformat() if trade_date is not None else None
    settle = ctx.cal.settlement_date(tdate, under.settle_days)
    opt_side = "LONG" if long else "SHORT"
    P = _fifo_cost(conn, account_id, opt.instrument_id, opt_side, n)        # the premium rolled: long cost / short proceeds
    lending = getattr(services, "lending", None)
    borrow_id = None
    short_qty = 0
    if not stock_buy:
        short_qty = max(0, Q - max(0, pos_qty))
        if short_qty > 0:
            code = "EXERCISE_NEEDS_BORROW" if long else "ASSIGNMENT_NEEDS_BORROW"
            if lending is None or not (hasattr(lending, "locate") and hasattr(lending, "open_borrow")):
                return {"skip": (code, f"{'exercising' if long else 'assignment of'} {n} {opt.symbol} would deliver {Q:,} {under.symbol} but the account holds "
                                       f"{max(0, pos_qty):,} and the securities-lending module is not available: buy the shares first or wait for the borrow")}
            loc = lending.locate(services, account_id, under, short_qty)
            if not getattr(loc, "available", False) or int(getattr(loc, "qty_available", 0)) < short_qty:
                return {"skip": (code, f"no borrow for {short_qty:,} {under.symbol} to deliver against {n} {opt.symbol}: {getattr(loc, 'basis', '')}")}
            borrow_id = lending.open_borrow(ctx, services, account_id, under, short_qty, loc, None)
    conn.execute("PRAGMA defer_foreign_keys=ON")     # lot_relief / lots rows precede their fill rows inside this transaction
    opt_fill = next_id(conn, "fill")
    stock_fill = next_id(conn, "fill")
    reliefs = relieve_fifo(ctx.tx, account_id, opt.instrument_id, opt_side, n, opt_fill, P)
    opt_gl = "1700" if long else "2900"
    close_px = Decimal(obs["close"])
    tag = f"{origin} {n} {opt.symbol} ({'long' if long else 'short'} {right}{K})"
    if stock_buy:
        cost = KQ + P if long else KQ - P
        if cost <= 0:
            raise LedgerError("BASIS_NEGATIVE", f"{tag}: the premium {fmt_money(P)} exceeds the strike value {fmt_money(KQ)}")
        funding = execution.fund(ctx, account_id, ccy, KQ, "fill", stock_fill, settle, overdraft=overdraft, services=services)
        lines = [dr(account_id, "1100", ccy, cost, instrument_id=under.instrument_id, memo=f"{Q:,} shares at the strike {K}, premium {fmt_money(P)} rolled into the basis")]
        lines.append(cr(account_id, opt_gl, ccy, P, instrument_id=opt.instrument_id, memo="option premium rolled") if long
                     else dr(account_id, opt_gl, ccy, P, instrument_id=opt.instrument_id, memo="premium received rolled into the basis"))
        lines.append(cr(account_id, "2100", ccy, KQ, memo="payable at settlement"))
        memo = f"{tag} -> BUY {Q:,} {under.symbol} @ {K}, basis {fmt_money(cost)}; {under.symbol} close {close_px} ({obs['mark_date']}); {note}"
        entry_id = ctx.post(Entry("EXERCISE" if long else "ASSIGN", memo, lines, f"fill:{stock_fill}", ref_type="fill", ref_id=stock_fill, business_date=bdate))
        _insert_fill(ctx, opt_fill, account_id, opt, "SELL" if long else "BUY", n, Decimal(0), 0, tdate, None, origin, "exercise: premium rolled into the stock basis",
                     obs, entry_id, f"{idem_base}:OPT", note)
        _insert_fill(ctx, stock_fill, account_id, under, "BUY", Q, K, KQ, tdate, settle, origin, "strike", obs, entry_id, f"{idem_base}:UND", note)
        lot_id = open_lot(ctx.tx, account_id, under.instrument_id, "LONG", stock_fill, Q, _unit_cost(cost, Q), cost, ccy)
        settle_id = _settlement(ctx, stock_fill, account_id, ccy, -KQ, "2100", settle)
        out = {"stock_side": "BUY", "stock_fill_id": stock_fill, "lot_id": lot_id, "cost_c": cost, "funding": funding.to_dict() if funding else None,
               "cost_relieved_c": 0, "realised_c": 0}
    else:
        proceeds = KQ - P if long else KQ + P
        if proceeds <= 0:
            raise LedgerError("PROCEEDS_NEGATIVE", f"{tag}: the premium {fmt_money(P)} exceeds the strike value {fmt_money(KQ)}")
        deliver = Q - short_qty
        cost_rel = realised = 0
        lines = [dr(account_id, "1200", ccy, KQ, memo="receivable at settlement")]
        lines.append(cr(account_id, opt_gl, ccy, P, instrument_id=opt.instrument_id, memo="option premium rolled into the proceeds") if long
                     else dr(account_id, opt_gl, ccy, P, instrument_id=opt.instrument_id, memo="premium received added to the proceeds"))
        funding = None
        lot_id = None
        if deliver > 0:
            share = _share(proceeds, deliver, Q)
            rl = relieve_fifo(ctx.tx, account_id, under.instrument_id, "LONG", deliver, stock_fill, share)
            cost_rel = sum(r.cost_relieved_c for r in rl)
            realised = sum(r.realised_c for r in rl)
            if cost_rel:
                lines.append(cr(account_id, "1100", ccy, cost_rel, instrument_id=under.instrument_id, memo="cost relieved FIFO"))
            if realised:
                lines.append(cr(account_id, "4000", ccy, realised, instrument_id=under.instrument_id, memo="realised"))
        if short_qty > 0:
            short_proceeds = proceeds - _share(proceeds, deliver, Q) if deliver > 0 else proceeds
            lines.append(cr(account_id, "2600", ccy, short_proceeds, instrument_id=under.instrument_id, memo="short proceeds (delivery without shares)"))
        memo = f"{tag} -> SELL {Q:,} {under.symbol} @ {K}, proceeds {fmt_money(proceeds)}" + (f", FIFO cost {fmt_money(cost_rel)}, realised {fmt_money(realised)}" if deliver else "") \
            + (f", {short_qty:,} delivered short (borrow {borrow_id})" if short_qty else "") + f"; {under.symbol} close {close_px} ({obs['mark_date']}); {note}"
        entry_id = ctx.post(Entry("EXERCISE" if long else "ASSIGN", memo, lines, f"fill:{stock_fill}", ref_type="fill", ref_id=stock_fill, business_date=bdate))
        _insert_fill(ctx, opt_fill, account_id, opt, "SELL" if long else "BUY", n, Decimal(0), 0, tdate, None, origin, "exercise: premium rolled into the proceeds",
                     obs, entry_id, f"{idem_base}:OPT", note)
        _insert_fill(ctx, stock_fill, account_id, under, "SELL", Q, K, KQ, tdate, settle, origin, "strike", obs, entry_id, f"{idem_base}:UND", note)
        if short_qty > 0:
            short_proceeds = proceeds - _share(proceeds, deliver, Q) if deliver > 0 else proceeds
            lot_id = open_lot(ctx.tx, account_id, under.instrument_id, "SHORT", stock_fill, short_qty, _unit_cost(short_proceeds, short_qty), short_proceeds, ccy)
            if lending is not None and hasattr(lending, "consume_for_short"):
                lending.consume_for_short(ctx, borrow_id, short_qty, stock_fill)
        settle_id = _settlement(ctx, stock_fill, account_id, ccy, KQ, "1200", settle)
        out = {"stock_side": "SELL", "stock_fill_id": stock_fill, "lot_id": lot_id, "proceeds_c": proceeds, "cost_relieved_c": cost_rel, "realised_c": realised,
               "short_qty": short_qty, "borrow_id": borrow_id, "funding": None}
        # TX-3: the delivered shares can no longer be sold by a working SELL; trim / cancel what exceeds the long left
        held_after, _ = position(conn, account_id, under.instrument_id)
        trimmed = _trim_working_sells(ctx, account_id, under.instrument_id, ("SELL",), max(0, held_after), "shares delivered by exercise" if long else "shares delivered by assignment")
        if trimmed:
            out["orders_trimmed"] = trimmed
    # TX-3: closing option contracts by exercise leaves fewer to sell: working STC orders beyond the long contracts left are trimmed
    if long:
        left, _ = position(conn, account_id, opt.instrument_id)
        trimmed_opt = _trim_working_sells(ctx, account_id, opt.instrument_id, ("STC",), max(0, left), "contracts exercised")
        if trimmed_opt:
            out["option_orders_trimmed"] = trimmed_opt
    out.update({"option_fill_id": opt_fill, "entry_id": entry_id, "settle_id": settle_id, "settle_date": settle.isoformat(), "premium_c": P, "strike": str(K),
                "qty": Q, "contracts": n, "reliefs": [r.__dict__ for r in reliefs], "memo": memo, "trade_date": tdate.isoformat()})
    return out


def _lapse(ctx: TxContext, services, account_id: str, opt: Instrument, signed_qty: int, obs: dict, idem: str, note: str, trade_date: Optional[date] = None) -> dict:
    """EXPIRY fill at 0 (§5.4): long ``Dr 4000 / Cr 1700 cost``; short ``Dr 2900 premium / Cr 4000``; no cash, no settlement.
    Applied with ``defer_margin`` so the caller's single recompute (overdraft rule, §11.6) adjusts the margin — not the fill
    path; dated the expiry date (LC-1)."""
    n = abs(int(signed_qty))
    side = "STC" if signed_qty > 0 else "BTC"
    spec = execution.FillSpec(None, account_id, opt.instrument_id, side, n, Decimal(0), "EXPIRY", obs, idem, None,
                              execution.PRICE_BASIS_OF_ORIGIN["EXPIRY"], fee_override_c=0, defer_margin=True, note=note, trade_date=trade_date)
    fr = execution.apply_fill(ctx, spec, services)
    return {"fill_id": fr.fill_id, "entry_id": fr.entry_id, "realised_c": fr.realised_c, "cost_relieved_c": fr.cost_relieved_c, "memo": fr.memo}


def _cash_settle(ctx: TxContext, services, account_id: str, opt: Instrument, signed_qty: int, intrinsic: Decimal, obs: dict, idem: str, origin: str, note: str,
                 trade_date: Optional[date] = None) -> dict:
    """Index option cash settlement (§5.4): long ``Dr 1200 intrinsic×100×n / Cr 1700 cost / 4000 diff``; short ``Dr 2900 premium /
    Cr 2100 intrinsic×100×n / 4000 diff`` — a fill at the intrinsic with a T+1 settlement row; a short's payable is funded
    with the overdraft rule."""
    conn = ctx.conn
    n = abs(int(signed_qty))
    long = signed_qty > 0
    ccy = opt.currency
    gross = opt.contract_value_c(intrinsic, n)
    tdate = trade_date or _trade_date(ctx.now, opt.session_group, ctx.cal)          # LC-1: the expiry date when the job runs late
    settle = ctx.cal.settlement_date(tdate, opt.settle_days)
    conn.execute("PRAGMA defer_foreign_keys=ON")
    fill_id = next_id(conn, "fill")
    rl = relieve_fifo(ctx.tx, account_id, opt.instrument_id, "LONG" if long else "SHORT", n, fill_id, gross)
    cost_rel = sum(r.cost_relieved_c for r in rl)
    realised = sum(r.realised_c for r in rl)
    funding = None
    if long:
        lines = [dr(account_id, "1200", ccy, gross, memo="cash settlement receivable")]
        if cost_rel:
            lines.append(cr(account_id, "1700", ccy, cost_rel, instrument_id=opt.instrument_id, memo="cost relieved FIFO"))
    else:
        funding = execution.fund(ctx, account_id, ccy, gross, "fill", fill_id, settle, overdraft=True, services=services)
        lines = [dr(account_id, "2900", ccy, cost_rel, instrument_id=opt.instrument_id, memo="premium received relieved FIFO")]
        if gross:
            lines.append(cr(account_id, "2100", ccy, gross, memo="cash settlement payable"))
    if realised:
        lines.append(cr(account_id, "4000", ccy, realised, instrument_id=opt.instrument_id, memo="realised"))
    memo = f"{origin} {n} {opt.symbol} cash-settled at the intrinsic {intrinsic} ({obs['underlying_id']} close {obs['close']} {obs['mark_date']}), realised {fmt_money(realised)}; {note}"
    entry_id = ctx.post(Entry("EXERCISE" if long else "ASSIGN", memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id,
                              business_date=trade_date.isoformat() if trade_date is not None else None))
    _insert_fill(ctx, fill_id, account_id, opt, "SELL" if long else "BUY", n, intrinsic, gross, tdate, settle if gross else None, origin, "intrinsic (cash settlement)",
                 obs, entry_id, idem, note)
    settle_id = _settlement(ctx, fill_id, account_id, ccy, gross if long else -gross, "1200" if long else "2100", settle) if gross else None
    return {"fill_id": fill_id, "entry_id": entry_id, "gross_c": gross, "cost_relieved_c": cost_rel, "realised_c": realised, "settle_id": settle_id,
            "settle_date": settle.isoformat(), "funding": funding.to_dict() if funding else None, "memo": memo}


def _expire_instrument(ctx: TxContext, services, inst: Instrument) -> None:
    ctx.conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE instrument_id=? AND status='ACTIVE'", (ctx.ts, inst.instrument_id))
    try:
        services.instruments.refresh(inst.instrument_id, ctx.conn)
    except Exception:   # noqa: BLE001 — the master reloads at the next start; the row is what counts
        pass


# ---- handlers ---------------------------------------------------------------------------------------------------------------------
def h_expire(ctx: TxContext, payload: dict) -> dict:
    """OPTION_EXPIRY for one (account, option) — see the module docstring (OCC).  Idempotent: a DONE key, a flat position
    or an existing fill for the key posts nothing."""
    services = _services_of(ctx)
    conn = ctx.conn
    account_id = str(payload.get("account_id") or "")
    iid = str(payload.get("instrument_id") or payload.get("option_id") or "")
    key = f"{account_id}:{iid}"
    done = _job_row(conn, JOB_EXPIRY, key)
    if done is not None and done["status"] == "DONE":
        return {"status": "DONE", "key": key, "note": "already done: " + (done["note"] or ""), "posted": False}
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    inst = services.instruments.get(iid, conn)
    if inst is None or inst.kind != "OPTION":
        raise RejectError("INSTRUMENT_UNKNOWN", f"{iid} is not a listed option")
    expiry = date.fromisoformat(inst.expiry)
    today = ny_of(ctx.ts).date()
    if expiry > today:
        raise ConflictError("NOT_EXPIRED", f"{inst.symbol} expires on {inst.expiry}", {"expiry": inst.expiry})
    close_ts = ctx.sessions.session_close(inst.session_group, expiry)
    if expiry == today and close_ts is not None and ctx.ts < int(close_ts):
        raise ConflictError("SESSION_OPEN", f"{inst.symbol}'s last session closes at {ny_of(int(close_ts)).strftime('%H:%M ET')}; expiry runs after the close")
    pos_qty, _cost = position(conn, account_id, iid)
    if pos_qty == 0:
        _expire_instrument(ctx, services, inst)
        _job_done(ctx, JOB_EXPIRY, key, "DONE", "flat: no position to settle")
        return {"status": "DONE", "key": key, "note": "flat", "posted": False}
    if conn.execute("SELECT 1 FROM fills WHERE idem_key IN (?, ?, ?)", (f"expiry:{key}", f"expiry:{key}:OPT", f"expiry:{key}:UND")).fetchone() is not None:
        _job_done(ctx, JOB_EXPIRY, key, "DONE", "fills already recorded")
        return {"status": "DONE", "key": key, "note": "already posted", "posted": False}
    under = services.instruments.get(inst.underlying_id, conn) if inst.underlying_id else None
    if under is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"{inst.symbol}: underlying {inst.underlying_id!r} is not in the master")
    obs = observed_close(ctx, services, under, expiry)
    if obs is None:
        return _skip(ctx, JOB_EXPIRY, key, "EXPIRY_PENDING_CLOSE",
                     f"{inst.symbol}: no observed close of {under.symbol} for {inst.expiry} yet (MARKS_RECORD or the next daily bar will supply it); the position stays open",
                     account_id, iid, {"expiry": inst.expiry, "underlying_id": under.instrument_id})
    S = Decimal(obs["close"])
    K = inst.strike
    intr = intrinsic_of(inst.right or "C", S, K)
    n = abs(pos_qty)
    idem = f"expiry:{key}"
    note = f"OCC: intrinsic {intr} vs threshold {config.OCC_THRESHOLD} at the observed close {S}"
    index = is_index_option(inst)
    outcome: dict
    # LC-1: every expiry fill is dated the expiry date (trade date, business date, settlement from it), whenever the job runs
    if intr < config.OCC_THRESHOLD:
        kind = "EXPIRY"
        outcome = _lapse(ctx, services, account_id, inst, pos_qty, obs, idem, note, trade_date=expiry)
    elif index:
        kind = "EXERCISE" if pos_qty > 0 else "ASSIGNMENT"
        outcome = _cash_settle(ctx, services, account_id, inst, pos_qty, intr, obs, idem, kind, note, trade_date=expiry)
    else:
        kind = "EXERCISE" if pos_qty > 0 else "ASSIGNMENT"
        outcome = _physical(ctx, services, account_id, inst, under, pos_qty, obs, idem, kind, note, trade_date=expiry)
        if "skip" in outcome:
            code, message = outcome["skip"]
            return _skip(ctx, JOB_EXPIRY, key, code, message, account_id, iid, {"expiry": inst.expiry, "underlying_id": under.instrument_id})
    _expire_instrument(ctx, services, inst)
    margin = execution.recompute_option_margin(ctx, services, account_id, f"expiry:{key}", overdraft=True)
    _job_done(ctx, JOB_EXPIRY, key, "DONE", f"{kind} {n} at close {S}, intrinsic {intr}")
    _resolve_attention(ctx, ("EXPIRY_PENDING_CLOSE", "EXERCISE_NEEDS_BORROW", "ASSIGNMENT_NEEDS_BORROW"), key)
    ctx.event({"EXPIRY": "OPTION_EXPIRED", "EXERCISE": "OPTION_EXERCISED", "ASSIGNMENT": "OPTION_ASSIGNED"}[kind], account_id=account_id, instrument_id=iid,
              ref_type="option", ref_id=key, contracts=n, close=str(S), intrinsic=str(intr), strike=str(K), margin_required_c=margin.get("required_c"))
    return {"status": "DONE", "key": key, "kind": kind, "posted": True, "contracts": n, "close": obs, "intrinsic": str(intr), "strike": str(K),
            "index_cash_settled": index and kind != "EXPIRY", "result": outcome, "margin": margin}


def h_margin_mark(ctx: TxContext, payload: dict) -> dict:
    """OPTION_MARGIN_MARK (17:10 and after every option fill through WP-02): recompute §13.3 and reserve it (``margin_state``;
    an uncovered reservation opens the FUNDING_SHORTFALL item, SR-13); key ``{account}:{date}``."""
    services = _services_of(ctx)
    account_id = str(payload.get("account_id") or "")
    d = str(payload.get("date") or ny_of(ctx.ts).date().isoformat())
    key = f"{account_id}:{d}"
    done = _job_row(ctx.conn, JOB_MARGIN, key)
    if done is not None and done["status"] == "DONE" and not payload.get("force"):
        return {"status": "DONE", "key": key, "note": "already done", "posted": False}
    acct = account_row(ctx.conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    try:
        res = execution.recompute_option_margin(ctx, services, account_id, f"omargin:{key}", overdraft=True)
    except RejectError as e:
        if e.code != "MARK_UNAVAILABLE":
            raise
        return _skip(ctx, JOB_MARGIN, key, "MARGIN_PENDING_MARK", e.message, account_id, account_id, e.details)
    _job_done(ctx, JOB_MARGIN, key, "DONE", f"requirement {fmt_money(res['required_c'])} reserved (delta {fmt_money(res['delta_c'])})"
              + (f"; {fmt_money(res['shortfall_c'])} uncovered" if res.get("shortfall_c") else ""))
    _resolve_attention(ctx, ("MARGIN_PENDING_MARK",), key)
    return {"status": "DONE", "key": key, "posted": bool(res.get("entry_id")), "margin": res}


def h_exercise(ctx: TxContext, payload: dict) -> dict:
    """Early exercise by the owner: a long AMERICAN option, during its session, at the strike; idem
    ``exercise:{account}:{inst}:{request_id}``; 409 NOT_LONG / SESSION_CLOSED, 400 NOT_EXERCISABLE for index options."""
    services = _services_of(ctx)
    conn = ctx.conn
    account_id = str(payload.get("account_id") or "")
    iid = str(payload.get("instrument_id") or "")
    try:
        qty = int(payload.get("qty"))
    except (TypeError, ValueError) as e:
        raise RejectError("QTY_INVALID", "qty must be a whole number of contracts") from e
    if qty <= 0:
        raise RejectError("QTY_INVALID", "qty must be positive")
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    inst = services.instruments.get(iid, conn)
    if inst is None or inst.kind != "OPTION":
        raise RejectError("INSTRUMENT_UNKNOWN", f"{iid} is not a listed option")
    if inst.status != "ACTIVE" or date.fromisoformat(inst.expiry) < ny_of(ctx.ts).date():
        raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} is expired; the OPTION_EXPIRY job settles it")
    if (inst.style or "AMERICAN").upper() != "AMERICAN" or is_index_option(inst):
        raise RejectError("NOT_EXERCISABLE", f"{inst.symbol} is European / cash-settled: it settles at expiry from the observed close (OCC)")
    session = ctx.sessions.state(inst.session_group, ctx.ts)
    if not session.open:
        raise ConflictError("SESSION_CLOSED", f"{inst.symbol}: exercise during the option session only ({session.phase.lower()})", {"next_open": session.next_open})
    pos_qty, _ = position(conn, account_id, iid)
    if pos_qty < qty:
        raise ConflictError("NOT_LONG", f"{account_id} holds {max(pos_qty, 0):,} {inst.symbol}, asked to exercise {qty:,}", {"position": pos_qty, "qty": qty})
    under = services.instruments.get(inst.underlying_id, conn)
    if under is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"{inst.symbol}: underlying {inst.underlying_id!r} is not in the master")
    S, quality, qt = _mark(ctx.marks, under.instrument_id)
    warnings: List[str] = []
    if S is None:
        warnings.append(f"{under.symbol}: no current mark to judge moneyness (the exercise is at the strike regardless)")
        S_text = "unavailable"
    else:
        S_text = str(S)
        if intrinsic_of(inst.right or "C", S, inst.strike) <= 0:
            warnings.append(f"{inst.symbol} is out of the money at the {quality} mark {S}: exercising pays the strike for less than the market price")
    obs = {"underlying_id": under.instrument_id, "close": S_text if S is not None else "0", "mark_date": ny_of(ctx.ts).date().isoformat(), "source": "current mark",
           "method": "early exercise", "quote_time": qt, "quality": quality, "basis": "owner's early exercise at the strike"}
    if S is None:
        obs["close"] = str(inst.strike)
    idem = f"exercise:{account_id}:{iid}:{ctx.request_id}"
    res = _physical(ctx, services, account_id, inst, under, qty, obs, idem, "EXERCISE", f"early exercise by the owner ({ctx.request_id})", overdraft=False)
    if "skip" in res:
        code, message = res["skip"]
        raise ConflictError(code, message, {"account_id": account_id, "instrument_id": iid})
    margin = execution.recompute_option_margin(ctx, services, account_id, idem)
    new_qty, _ = position(conn, account_id, iid)
    ctx.event("OPTION_EXERCISED", account_id=account_id, instrument_id=iid, ref_type="option", ref_id=idem, contracts=qty, strike=str(inst.strike), early=True,
              margin_required_c=margin.get("required_c"))
    fills = [f for f in (res.get("option_fill_id"), res.get("stock_fill_id")) if f]
    return {"fills": fills, "position": {"instrument_id": iid, "qty": new_qty, "underlying_id": under.instrument_id,
                                         "underlying_qty": position(conn, account_id, under.instrument_id)[0]},
            "result": res, "margin": margin, "warnings": warnings, "idem_key": idem}


def h_migrate_margin(ctx: TxContext, payload: dict) -> dict:
    """SR-13 one-time migration (decision s7.6): release every OPEN account's 1310 balance to 1010 - option margin is now a
    reservation (``margin_state`` keeps the requirement).  Idem ``migrate:omargin:{account}`` (``:{n}`` for a later
    balance an older binary may have posted); accounts of CLOSED lifetimes keep their history untouched."""
    from ..ledger import natural
    conn = ctx.conn
    rows = conn.execute("SELECT g.account_id, SUM(g.balance_c) b FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id JOIN lifetimes l ON"
                        " l.lifetime_id=a.lifetime_id WHERE g.gl='1310' AND g.currency='USD' AND a.status='OPEN' AND l.status='OPEN' GROUP BY g.account_id"
                        " HAVING SUM(g.balance_c) <> 0 ORDER BY g.account_id").fetchall()
    released = []
    for r in rows:
        aid = r["account_id"]
        amount = natural("1310", int(r["b"]))
        idem = f"migrate:omargin:{aid}"
        n = 1
        while conn.execute("SELECT 1 FROM journal_entries WHERE idem_key=?", (idem,)).fetchone() is not None:
            n += 1
            idem = f"migrate:omargin:{aid}:{n}"
        lines = ([dr(aid, "1010", "USD", amount, memo="option margin released to cash"), cr(aid, "1310", "USD", amount, memo="decision 6: option margin is a reservation")]
                 if amount > 0 else [dr(aid, "1310", "USD", -amount, memo="decision 6: option margin is a reservation"), cr(aid, "1010", "USD", -amount)])
        entry_id = ctx.post(Entry("OPT_MARGIN", f"decision 6: option margin is a reservation - {aid} 1310 {fmt_money(amount)} released to settled cash", lines, idem,
                                  ref_type="margin", ref_id=aid))
        ctx.touched_accounts.add(aid)
        execution.auto_resolve_shortfall(ctx, aid, "USD")
        ctx.event("MARGIN_MIGRATED", account_id=aid, ref_type="margin", ref_id=aid, amount_c=amount, entry_id=entry_id, idem_key=idem)
        released.append({"account_id": aid, "amount_c": amount, "entry_id": entry_id, "idem_key": idem})
    return {"released": released, "note": "decision 6: option margin is a reservation" if released else "nothing to release"}


def bind(services) -> None:
    """At start: the one-time SR-13 migration when an OPEN account still holds a 1310 balance (a request id per start, the
    entries idempotent per account), so the reservation rule and the ledger agree before anything else runs."""
    bus = getattr(services, "bus", None)
    db = getattr(services, "db", None)
    if bus is None or db is None or "options.migrate_margin" not in bus.kinds():
        return
    with db.reader() as conn:
        n = conn.execute("SELECT COUNT(*) FROM (SELECT g.account_id FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id JOIN lifetimes l ON"
                         " l.lifetime_id=a.lifetime_id WHERE g.gl='1310' AND a.status='OPEN' AND l.status='OPEN' GROUP BY g.account_id HAVING SUM(g.balance_c) <> 0)").fetchone()[0]
    if not int(n or 0):
        return
    from ..commands import Command
    clock = getattr(services, "clock", None)
    res = bus.execute(Command("options.migrate_margin", {}, f"migrate:omargin:{int(clock.epoch()) if clock is not None else 0}", "init"))
    log = getattr(services, "log", None)
    if log is not None:
        if res.ok:
            log.info("SR-13 option margin migration: %s", (res.body or {}).get("released"))
        else:
            log.error("SR-13 option margin migration failed: %s %s", res.code, res.message)


def register(bus, services=None) -> None:
    """Register the handlers; remember the runtime (``bus.services``) as the fallback master for ``margin_requirement_c``."""
    global _services
    s = services if services is not None else getattr(bus, "services", None)
    if s is not None:
        _services = s
    bus.register("options.margin_mark", h_margin_mark)
    bus.register("options.expire", h_expire)
    bus.register("options.exercise", h_exercise)
    bus.register("options.migrate_margin", h_migrate_margin)


# ---- due keys for the lifecycle (WP-08 may use these in its due()) ----------------------------------------------------------------
def expiry_keys(conn, now_ts: int, sessions=None) -> List[Tuple[str, str]]:
    """``(account_id, option_id)`` pairs whose OPTION_EXPIRY is due: open option positions of OPEN lifetimes / accounts whose
    expiry date has passed, or is today and the clock is past 16:20 ET (index 16:35); keys already DONE are skipped.
    Per account the WRITTEN contracts come first (``ORDER BY v.qty``: negative positions, then long ones), so a spread's
    long leg still covers its short while the short is settled — see the module docstring."""
    today = ny_of(int(now_ts)).date()
    out: List[Tuple[str, str]] = []
    rows = conn.execute("SELECT v.account_id, v.instrument_id, i.expiry, i.session_group FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id"
                        " JOIN accounts a ON a.account_id=v.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                        " WHERE i.kind='OPTION' AND v.qty <> 0 AND a.status='OPEN' AND l.status='OPEN' AND i.expiry <= ?"
                        " ORDER BY v.account_id, v.qty, v.instrument_id",
                        (today.isoformat(),)).fetchall()
    for r in rows:
        exp = date.fromisoformat(r["expiry"])
        if exp == today and int(now_ts) < epoch_of(today, EXPIRY_TIME_ET.get(r["session_group"], "16:20")):
            continue
        key = f"{r['account_id']}:{r['instrument_id']}"
        d = _job_row(conn, JOB_EXPIRY, key)
        if d is not None and d["status"] == "DONE":
            continue
        out.append((r["account_id"], r["instrument_id"]))
    return out


def margin_mark_keys(conn, today: str) -> List[Tuple[str, str]]:
    """``(account_id, date)`` pairs for OPTION_MARGIN_MARK: OPEN accounts (of OPEN lifetimes) with an option position, a
    non-zero ``margin_state`` requirement or a 1310 balance; keys DONE for the date are skipped."""
    accounts = set()
    for r in conn.execute("SELECT DISTINCT v.account_id FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id WHERE i.kind='OPTION' AND v.qty <> 0").fetchall():
        accounts.add(r["account_id"])
    for r in conn.execute("SELECT account_id FROM margin_state WHERE options_required_c <> 0").fetchall():
        accounts.add(r["account_id"])
    for r in conn.execute("SELECT DISTINCT account_id FROM gl_balances WHERE gl='1310' AND balance_c <> 0").fetchall():
        accounts.add(r["account_id"])
    out: List[Tuple[str, str]] = []
    for aid in sorted(accounts):
        row = conn.execute("SELECT a.status, l.status AS lstatus FROM accounts a JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE a.account_id=?", (aid,)).fetchone()
        if row is None or row["status"] != "OPEN" or row["lstatus"] != "OPEN":
            continue
        d = _job_row(conn, JOB_MARGIN, f"{aid}:{today}")
        if d is not None and d["status"] == "DONE":
            continue
        out.append((aid, today))
    return out


# ---- chain view and materialisation (§7.4 ensure_option, §15 /api/instruments/{id}/chain) ----------------------------------------------
def chain_freshness(services, chain, now: int, under: Optional[Instrument] = None) -> Tuple[str, Optional[int], Optional[str]]:
    """USX-09: ``(quality, age_s, reason)`` of a chain at ``now`` by the rule the ticket enforces on its contracts
    (``quotes.freshness`` on the option record, whose quote_time / fetched_at / delay are the chain's): ``close`` outside the
    option session (index options to 16:15); ``stale`` when the PROVIDER time is older than the feed's delay + 5 min grace
    (``config.STALE_GRACE_S``) or the chain was fetched more than 5 min ago (``config.FETCH_GRACE_S``) or carries no time;
    else ``delayed``.  ``age_s`` = now - the provider time; ``reason`` says why it is stale."""
    group = "US_INDEX_OPTIONS" if under is not None and under.kind == "INDEX" else "US_EQUITY_OPTIONS"
    qt = getattr(chain, "quote_time", None)
    age = None if qt is None else max(0, int(now) - int(qt))
    try:
        session = services.sessions.state(group, now)
        if not session.open:
            return "close", age, None
    except Exception:   # noqa: BLE001 — no session table (offline tooling): judge by age alone
        pass
    delay = int(getattr(chain, "delay_s", 0) or 0)
    if qt is None:
        return "stale", None, "the chain carries no provider time"
    if age > delay + config.STALE_GRACE_S:
        return "stale", age, (f"provider time {ny_of(int(qt)).strftime('%H:%M:%S')} ET is {age // 60} min old (feed delay {delay // 60} min + "
                              f"{config.STALE_GRACE_S // 60} min grace): orders on it are refused until a fresh chain")
    fetched_age = int(now) - int(chain.fetched_at)
    if fetched_age > config.FETCH_GRACE_S:
        return "stale", age, f"fetched {fetched_age // 60} min ago: a refresh is needed before an order can use it"
    return "delayed", age, None


def _chain_quality(services, chain, now: int, under: Optional[Instrument] = None) -> str:
    """The chain's freshness at ``now`` (``chain_freshness``)."""
    return chain_freshness(services, chain, now, under)[0]


def leg_dict(row, snap, quality: str, now: int, listed: Optional[bool] = None) -> dict:
    """One contract for the API / UI (``components/chain.js``, ``components/hedge.js``): the ChainRow fields plus the OCC
    ``instrument_id``, ``kind`` (call / put), the chain's observation time and provenance (``quote_time`` / ``as_of``,
    ``fetched_at``, ``age_s``, ``source``, ``method`` chain, ``provider`` cboe, ``delay_label``) and ``quality``
    (``unavailable`` for a contract without a usable NBBO); ``listed`` says whether the master already has the row."""
    d = row.to_dict()
    qt = snap.quote_time
    d.update({"instrument_id": row.occ, "kind": "call" if row.right == "C" else "put", "quality": quality if row.quoted else "unavailable",
              "quote_time": qt, "as_of": qt, "fetched_at": snap.fetched_at, "age_s": None if qt is None else max(0, int(now) - int(qt)),
              "source": snap.source, "method": "chain", "provider": "cboe", "delay_s": snap.delay_s, "delay_label": snap.delay_label, "mid": _s(row.mid)})
    if listed is not None:
        d["listed"] = listed
    return d


def underlying_dict(snap, quality: str, now: int, underlying_id: str) -> dict:
    """The chain's underlying as a QuoteRecord-shaped dict (``last``, NBBO, ``prev_close``, ``iv30`` and the freshness
    fields the UI's price chip reads); ``quality`` is ``unavailable`` when the chain carries no underlying price."""
    u = snap.underlying
    qt = u.last_trade_time
    nbbo = u.bid is not None and u.ask is not None and u.bid > 0 and u.ask >= u.bid
    return {"instrument_id": underlying_id, "symbol": u.symbol, "provider": "cboe", "source": snap.source, "method": "direct", "last": _s(u.last),
            "bid": _s(u.bid), "ask": _s(u.ask), "bid_size": u.bid_size, "ask_size": u.ask_size, "mid": _s(qprice((u.bid + u.ask) / 2)) if nbbo else None,
            "prev_close": _s(u.prev_close), "iv30": u.iv30, "quote_time": qt, "as_of": qt, "fetched_at": snap.fetched_at,
            "age_s": None if qt is None else max(0, int(now) - int(qt)), "delay_s": snap.delay_s, "delay_label": snap.delay_label, "currency": "USD",
            "quality": quality if u.last is not None else "unavailable"}


def underlying_of(services, occ: str, conn=None) -> Optional[Instrument]:
    parsed = parse_occ(occ)
    if parsed is None:
        return None
    root = parsed[0]
    master = services.instruments
    for cand in (underlying_id_of_root(root), root.replace(".", "-"), root):
        inst = master.get(cand, conn)
        if inst is not None:
            return inst
    return None


def ensure_option(services, occ: str, chain=None, tx=None, underlying: Optional[Instrument] = None) -> Instrument:
    """Materialise ``occ`` from a Cboe chain row on first sight (the master's ``ensure_option``): the cached chain of the
    underlying (or ``chain``) must list it — nothing is listed from thin air.  Inside a command pass ``tx=ctx.tx``;
    otherwise a short writer transaction is used."""
    master = services.instruments
    inst = master.get(occ, tx.conn if tx is not None else None)
    if inst is not None:
        return inst
    if parse_occ(occ) is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"{occ!r} is not an OCC option symbol")
    under = underlying or underlying_of(services, occ, tx.conn if tx is not None else None)
    if under is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"{occ}: its underlying is not in the master")
    snap = chain
    if snap is None:
        chains = getattr(services, "chains", None)
        snap = chains.peek(under.instrument_id) if chains is not None and hasattr(chains, "peek") else None
    row = snap.by_occ(occ) if snap is not None else None
    if row is None:
        raise RejectError("NOT_LISTED", f"{occ} is not on the cached {under.symbol} chain: load the chain first", {"underlying_id": under.instrument_id})
    if tx is not None:
        return master.ensure_option(tx, occ, under, row)
    with services.db.writer() as tx2:
        return master.ensure_option(tx2, occ, under, row)


def chain_view(services, underlying_id: str, expiry: Optional[str] = None, chain=None, now: Optional[int] = None) -> dict:
    """§15 ``GET /api/instruments/{id}/chain``: ``{underlying, underlying_id, expiries, expiry, rows: [{strike, call, put}], as_of,
    fetched_at, delay_label, quality, source}``; ``{"status": "fetching", "retry_ms": 1500}`` while the cache loads (202);
    ConflictError NO_CHAIN (409) when the underlying has no listed options; ServiceUnavailable CBOE_REFUSED_UA (503).
    Each contract row is the ChainRow plus ``instrument_id`` (the OCC id), ``listed`` (already in the master) and ``quality``."""
    now = int(now if now is not None else services.clock.epoch())
    master = services.instruments
    under = master.get(underlying_id)
    if under is None:
        raise RejectError("NOT_FOUND", f"instrument {underlying_id} is not in the master")
    snap = chain
    stale_note = None
    if snap is None:
        chains = getattr(services, "chains", None)
        if chains is None:
            return {"status": "unavailable", "underlying_id": underlying_id, "note": "no chain source in this runtime (offline)"}
        snap = chains.get(underlying_id) if hasattr(chains, "get") else None
        if snap is None:
            err = chains.last_error(underlying_id) if hasattr(chains, "last_error") else None
            if err and ("403" in err or "refused" in err.lower()):
                raise ServiceUnavailable("CBOE_REFUSED_UA", f"Cboe refused the honest User-Agent for {underlying_id}: options are computed-only (no execution)", {"error": err})
            snap = chains.peek(underlying_id) if hasattr(chains, "peek") else None
            if hasattr(chains, "keep_on_screen"):
                chains.keep_on_screen(underlying_id)
            if snap is None:
                if err and ("404" in err or "no cboe symbol" in err.lower()):
                    raise ConflictError("NO_CHAIN", f"{underlying_id} has no Cboe option chain", {"error": err})
                chains.request(underlying_id)
                return {"status": "fetching", "retry_ms": 1500, "underlying_id": underlying_id}
            stale_note = f"chain fetched {ny_of(int(snap.fetched_at)).strftime('%H:%M:%S ET')}; a refresh is queued"
            chains.request(underlying_id)
    expiries = snap.expiries()
    if not expiries:
        raise ConflictError("NO_CHAIN", f"{underlying_id}: the chain lists no contracts within {config.CHAIN_MAX_DAYS} days / ±{config.CHAIN_STRIKE_BAND * 100:.0f} %")
    exp = expiry or expiries[0]
    if exp not in expiries:
        raise RejectError("NOT_FOUND", f"{underlying_id}: expiry {exp} is not listed", {"expiries": expiries})
    quality, age_s, stale_reason = chain_freshness(services, snap, now, under)
    by_strike: Dict[Decimal, dict] = {}
    for row in snap.rows_for(exp):
        d = leg_dict(row, snap, quality, now, listed=master.get(row.occ) is not None)
        by_strike.setdefault(row.strike, {"strike": str(row.strike), "call": None, "put": None})["call" if row.right == "C" else "put"] = d
    return {"underlying_id": underlying_id, "underlying": underlying_dict(snap, quality, now, underlying_id),
            "expiries": expiries, "expiry": exp, "rows": [by_strike[k] for k in sorted(by_strike)], "as_of": snap.quote_time, "fetched_at": snap.fetched_at,
            "delay_label": snap.delay_label, "quality": quality, "age_s": age_s, "stale_reason": stale_reason, "source": snap.source, "n_kept": snap.n_kept(),
            "note": stale_note or stale_reason}


__all__ = ["margin_requirement_c", "reserved_shares", "observed_close", "intrinsic_of", "is_index_option", "h_expire", "h_margin_mark", "h_exercise", "register",
           "expiry_keys", "margin_mark_keys", "ensure_option", "underlying_of", "chain_view", "leg_dict", "underlying_dict", "ServiceUnavailable", "JOB_EXPIRY",
           "JOB_MARGIN", "EXPIRY_TIME_ET"]
