"""MarkProvider: one mark per instrument kind, immutable snapshots, the computed marks (TERMINAL_DESIGN.md §8.6, §13.8, §19.13).

Conventions
- A mark is the instrument's quoted price per unit AS QUOTED (the ledger's ``unit_cost`` convention): ``accounting``
  applies ``multiplier`` and ``price_scale`` itself, so nothing here scales (FX pairs keep the market convention —
  USDJPY 158.10 — and ``fx(ccy)`` inverts a ``USD…`` pair to USD per 1 unit of ``ccy``).
- Mark selection per kind: equity-like / INDEX / YIELD / FUTURE / FX → the cached Yahoo ``last`` with ``freshness()``;
  OPTION → the Cboe ``mid`` when ``bid > 0 and ask ≥ bid`` (from the option's chain record or the cached chain),
  else ``option_model`` (BSM European / CRR 60-step American from ``finsim.engines.options_pricing``: S = underlying
  last, K, T = days/365, r = ``^IRX``/100 else the par curve 3M, q = the master's ``dividend_yield``, σ = the IV
  interpolated between the two nearest quoted strikes of the same expiry, else the chain ``iv30``, else FRED
  ``VIXCLS``/100 for index / ETF underlyings) labelled ``computed`` and never executable; BOND → the par-curve price
  (``computed``, Build 2 execution); PRIVATE → ``services.private.private_mark`` (or ``services.private_credit.pc_mark``
  for a loan); SWAP → ``services.swaps.mark`` — an absent module answers ``unavailable`` with the note "not yet built".
- ``snapshot()`` is an immutable ``MarkSet`` (records, chains, curve, rates and FX frozen at one instant; marks are
  memoised; ``fx()`` answers for every currency: USD = 1, else the Yahoo pair, else the Frankfurter ECB reference rate
  as ``close``).  A missing rate or input yields ``unavailable`` with a note; nothing is defaulted to a number.
- ONE READER PER THREAD (the 2026-10-21 BUSY defect): the database hands out four pooled readers, so a mark that took a
  SECOND pooled reader inside the one its caller holds (accounting's positions / NAV, a view) starved the pool under
  ordinary polling (503 BUSY, "mark failed: BusyError").  The rows the PRIVATE / private-credit / SWAP marks need are
  read on ``MarkProvider.private_conn()``: the writer inside this thread's own transaction, else the connection this
  thread already reads on (``reading`` / ``shared_reader`` declare it; ``Accounting`` and the product modules do), else a
  short-lived read-only connection of its own - never a second pooled reader.  A snapshot reads the private companies
  and their latest rounds once (``private.mark_inputs``), so its private marks are consistent with each other and cost
  one read.  A loan's mark is ``private_credit.mark_view`` (quality computed, method pc_model, never executable).
- Pure Build 2 functions tested now (§19.13): ``price_bond`` (street convention: PV of the remaining semi-annual
  coupons and principal at y/2 compounding with the fractional first period; accrued ACT/ACT on the actual coupon
  period; clean = dirty − accrued) and ``cip_forward`` (``F = S × (1 + r_quote × d/B_quote) / (1 + r_base × d/B_base)``,
  ACT/360, GBP ACT/365).
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from finsim.engines import options_pricing as _op

from . import tables
from .clock import ny_of
from .instruments import EQUITY_KINDS, fx_pair_id
from .quotes import EXECUTABLE_QUALITIES, MarkView, QuoteRecord, freshness, is_executable, unavailable
from .quotes.cache import record_from_chain_row
from .quotes.rates import OAS_SERIES
from .units import from_c, qprice

getcontext().prec = 34
PX4 = Decimal("0.0001")
PX6 = Decimal("0.000001")
CORP_BASE_DATE = date(2026, 9, 18)        # §13.8: curated corporates mature on 2026-09-18 + years
CHAIN_STALE_S = 900


# ---- one reader per thread (module docstring ONE READER PER THREAD) ---------------------------------------------------------------
_READING = threading.local()          # .by_db: {Database: the connection this thread reads that database on}


def ambient_conn(db) -> Optional[sqlite3.Connection]:
    """The connection this thread is reading ``db`` on (declared by ``reading`` / ``shared_reader``), else None."""
    m = getattr(_READING, "by_db", None)
    try:
        return m.get(db) if m else None
    except TypeError:                     # an unhashable stand-in database: nothing is ever declared for it
        return None


@contextmanager
def reading(conn, db) -> Iterator:
    """Declare ``conn`` as this thread's reading connection of ``db`` for the block: marks and views computed inside reuse
    it instead of taking a second pooled reader.  The previous declaration comes back on exit (also on an exception)."""
    m = getattr(_READING, "by_db", None)
    if m is None:
        m = _READING.by_db = {}
    try:
        had, prev = db in m, m.get(db)
        m[db] = conn
    except TypeError:                     # unhashable stand-in: no declaration
        yield conn
        return
    try:
        yield conn
    finally:
        if had:
            m[db] = prev
        else:
            m.pop(db, None)


def _in_own_tx(db) -> bool:
    """True when this thread holds the writer inside a transaction (``Database.reader`` then hands out the writer itself)."""
    try:
        return bool(db.writer_owned_by_current_thread() and db.writer_conn().in_transaction)
    except Exception:   # noqa: BLE001 — a database that is not open / a stand-in without a writer: not in a transaction
        return False


@contextmanager
def shared_reader(db, conn=None) -> Iterator:
    """``conn`` when given; else the writer inside this thread's own transaction (what ``Database.reader`` returns there, so
    a handler sees its uncommitted rows); else the connection this thread already reads on; else ONE pooled reader,
    declared as this thread's reading connection for the block so that anything nested reuses it."""
    if conn is not None:
        yield conn
        return
    amb = ambient_conn(db)
    if amb is not None and not _in_own_tx(db):
        yield amb
        return
    with db.reader() as c, reading(c, db):
        yield c


def _side_conn(path: str) -> sqlite3.Connection:
    """A short-lived read-only connection of the mark provider's own (outside the pool; closed by the caller)."""
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA query_only=ON")
    except BaseException:
        conn.close()
        raise
    return conn


# ---- pure Build 2 pricing -----------------------------------------------------------------------------------------------------
def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    last = (date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def coupon_period(maturity: date, asof: date, freq: int = 2) -> Tuple[date, date, int]:
    """``(prev_coupon, next_coupon, n_remaining)`` for a bond paying ``freq`` times a year up to ``maturity``;
    ``prev ≤ asof < next``; ``n_remaining`` counts the coupons in (asof, maturity]."""
    if asof >= maturity:
        raise ValueError("the bond has matured")
    step = 12 // freq
    nxt = maturity
    n = 1
    while add_months(maturity, -step * n) > asof:
        nxt = add_months(maturity, -step * n)
        n += 1
    prev = add_months(maturity, -step * n)
    return prev, nxt, n


def accrued_interest(coupon_pct, prev_coupon: date, next_coupon: date, asof: date, freq: int = 2) -> Decimal:
    """ACT/ACT on the actual coupon period: ``coupon/freq × (asof − prev) / (next − prev)`` per 100, 4 dp."""
    period = (next_coupon - prev_coupon).days
    if period <= 0:
        return Decimal(0)
    elapsed = (asof - prev_coupon).days
    return (Decimal(str(coupon_pct)) / freq * Decimal(elapsed) / Decimal(period)).quantize(PX4, rounding=ROUND_HALF_EVEN)


def price_bond(coupon_pct, maturity: date, asof: date, y_pct, freq: int = 2) -> Tuple[Decimal, Decimal, Decimal, dict]:
    """``(clean, accrued, dirty, detail)`` per 100 of face (street convention; see the module docstring)."""
    c = Decimal(str(coupon_pct))
    y = Decimal(str(y_pct)) / Decimal(100)
    prev, nxt, n = coupon_period(maturity, asof, freq)
    period = Decimal((nxt - prev).days)
    w = Decimal((nxt - asof).days) / period if period else Decimal(1)
    per = Decimal(1) + y / freq
    cpn = c / freq
    dirty = Decimal(0)
    for i in range(1, n + 1):
        cf = cpn + (Decimal(100) if i == n else Decimal(0))
        dirty += cf / (per ** (Decimal(i - 1) + w))
    accrued = accrued_interest(c, prev, nxt, asof, freq)
    dirty_q = dirty.quantize(PX4, rounding=ROUND_HALF_EVEN)
    clean = (dirty - accrued).quantize(PX4, rounding=ROUND_HALF_EVEN)
    return clean, accrued, dirty_q, {"prev_coupon": prev.isoformat(), "next_coupon": nxt.isoformat(), "n_coupons": n, "w": str(w.quantize(PX6)),
                                     "coupon_pct": str(c), "yield_pct": str(Decimal(str(y_pct)))}


def cip_forward(spot, r_base, r_quote, days: int, basis_base: int = 360, basis_quote: int = 360) -> Decimal:
    """Covered interest parity: ``F = S × (1 + r_quote × d/B_quote) / (1 + r_base × d/B_base)`` (rates as decimals), 6 dp."""
    s = Decimal(str(spot))
    rb, rq = Decimal(str(r_base)), Decimal(str(r_quote))
    d = Decimal(int(days))
    f = s * (Decimal(1) + rq * d / Decimal(basis_quote)) / (Decimal(1) + rb * d / Decimal(basis_base))
    return f.quantize(PX6, rounding=ROUND_HALF_EVEN)


# ---- option model -------------------------------------------------------------------------------------------------------------------
def interpolate_iv(chain, expiry: str, right: str, strike: Decimal) -> Tuple[Optional[float], str]:
    """σ for (expiry, right, strike) from the chain's quoted contracts: the strike itself, else linear between the two
    nearest strikes bracketing it, else the nearest quoted strike, else the chain ``iv30``; ``(None, reason)`` otherwise."""
    if chain is None:
        return None, "no chain"
    rows = [c for c in chain.rows_for(expiry) if c.right == right and c.quoted and c.iv is not None and 0.01 < c.iv < 5.0]
    if rows:
        k = float(strike)
        exact = [c for c in rows if float(c.strike) == k]
        if exact:
            return exact[0].iv, f"Cboe iv at strike {strike}"
        below = [c for c in rows if float(c.strike) < k]
        above = [c for c in rows if float(c.strike) > k]
        if below and above:
            lo, hi = max(below, key=lambda c: c.strike), min(above, key=lambda c: c.strike)
            k0, k1 = float(lo.strike), float(hi.strike)
            iv = lo.iv + (hi.iv - lo.iv) * (k - k0) / (k1 - k0)
            return iv, f"Cboe iv interpolated between strikes {lo.strike} and {hi.strike}"
        near = min(rows, key=lambda c: abs(float(c.strike) - k))
        return near.iv, f"Cboe iv of the nearest quoted strike {near.strike}"
    if chain.iv30 is not None and 0.01 < chain.iv30 < 5.0:
        return chain.iv30, "Cboe iv30 of the underlying"
    return None, "no quoted implied volatility in the chain"


def price_option(right: str, style: str, S, K, T_years: float, r: float, q: float, sigma: float) -> Tuple[Decimal, str]:
    """``(price per share 4 dp, model label)`` from ``finsim.engines.options_pricing.price``."""
    model = "CRR 60-step American" if (style == "AMERICAN" and not (right == "C" and q <= 1e-9)) else "Black-Scholes-Merton (European)"
    px = _op.price(right, "AMERICAN" if style == "AMERICAN" else "EUROPEAN", float(S), float(K), float(T_years), float(r), float(q), float(sigma))
    return Decimal(repr(px)).quantize(PX4, rounding=ROUND_HALF_EVEN), model


# ---- the frozen snapshot -----------------------------------------------------------------------------------------------------------
class FrozenMarks:
    """An immutable ``MarkSet``: records (with their quality at ``now``), chains, curve, rates and FX captured at one
    instant.  ``mark()`` / ``fx()`` memoise; nothing here mutates the provider."""

    def __init__(self, provider: "MarkProvider", recs: Dict[str, QuoteRecord], chains: dict, now: int):
        self._p = provider
        self._recs = recs
        self._chains = chains
        self._now = int(now)
        self._today = ny_of(self._now).date()
        self._memo: Dict[str, MarkView] = {}
        self._fx: Dict[str, MarkView] = {}
        self._sessions: dict = {}
        self._computed: Dict[str, QuoteRecord] = {}
        self._lock = threading.Lock()
        self._private_inputs: Optional[dict] = None       # private.mark_inputs, read once per snapshot (ONE READER PER THREAD)
        self._inputs_lock = threading.Lock()

    # ---- MarkSet -----------------------------------------------------------------------------------------------------
    def as_of(self) -> int:
        return self._now

    def quote(self, instrument_id: str) -> Optional[QuoteRecord]:
        rec = self._recs.get(instrument_id)
        return rec if rec is not None else self._computed.get(instrument_id)

    def chain(self, underlying_id: str):
        return self._chains.get(underlying_id)

    def mark(self, instrument_id: str) -> MarkView:
        with self._lock:
            v = self._memo.get(instrument_id)
            if v is not None:
                return v
        try:
            v = self._compute(instrument_id)
        except Exception as e:   # noqa: BLE001 — a failing model is an unavailable mark with the reason, never a crash of a read
            v = unavailable(instrument_id, f"mark failed: {type(e).__name__}: {e}"[:200])
            if self._p.log:
                self._p.log.warning("mark %s failed: %s", instrument_id, e)
        with self._lock:
            self._memo[instrument_id] = v
        return v

    def fx(self, ccy: str) -> MarkView:
        ccy = (ccy or "USD").upper()
        with self._lock:
            v = self._fx.get(ccy)
            if v is not None:
                return v
        v = self._compute_fx(ccy)
        with self._lock:
            self._fx[ccy] = v
        return v

    # ---- sessions ------------------------------------------------------------------------------------------------------
    def session(self, rec: QuoteRecord):
        sessions = self._p.sessions
        if sessions is None:
            return None
        if rec.provider_session is not None and (rec.session_group.startswith("FOREIGN") or rec.session_group == "FOREIGN"):
            try:
                return sessions.state(rec.session_group, self._now, foreign={"open_ts": rec.provider_session[0], "close_ts": rec.provider_session[1]})
            except KeyError:
                return None
        key = rec.session_group
        if key not in self._sessions:
            try:
                self._sessions[key] = sessions.state(key, self._now)
            except KeyError:
                self._sessions[key] = None
        return self._sessions[key]

    # ---- per kind -------------------------------------------------------------------------------------------------------
    def _compute(self, iid: str) -> MarkView:
        inst = self._p.instrument(iid)
        if inst is None:
            return unavailable(iid, "unknown instrument")
        kind = inst.kind
        custom = self._p.custom.get(kind)
        if custom is not None:
            return self._normalise(iid, custom(self._p.services, inst), kind)
        if kind == "OPTION":
            return self._option(inst)
        if kind == "BOND":
            return self._bond(inst)
        if kind == "PRIVATE":
            if (inst.spec or {}).get("loan_id"):
                return self._dispatch(inst, "private_credit", "pc_mark", loan=True)
            return self._dispatch(inst, "private", "private_mark")
        if kind == "SWAP":
            return self._dispatch(inst, "swaps", "mark")
        rec = self._recs.get(iid)
        if rec is None or rec.price() is None:
            return unavailable(iid, (rec.error if rec is not None and rec.error else "no quote yet"))
        session = self.session(rec)
        if kind in ("INDEX", "YIELD"):
            exe, reason = False, "REFERENCE_ONLY"
        else:
            exe, reason = is_executable(rec, session, self._now)
        note = "" if exe else reason
        if rec.error:
            note = (note + "; " if note else "") + f"last fetch failed: {rec.error}"
        if rec.method == "front_month_continuous":
            note = (note + "; " if note else "") + "front-month continuous (reference)"
        return MarkView(iid, rec.price(), rec.quality, rec.source, rec.method, rec.quote_time, rec.fetched_at, rec.delay_label, rec.inputs,
                        exe and rec.quality in EXECUTABLE_QUALITIES, note)

    def _option(self, inst) -> MarkView:
        iid = inst.instrument_id
        rec = self._recs.get(iid)
        if (rec is None or rec.method != "chain") and inst.underlying_id:
            snap = self._chains.get(inst.underlying_id)
            row = snap.by_occ(inst.cboe_symbol or iid) if snap is not None else None
            if row is not None:
                rec = record_from_chain_row(inst, snap, row)
                rec.quality = freshness(rec, self.session(rec), self._now)
                self._computed[iid] = rec
        if rec is not None and rec.method == "chain" and rec.mid is not None and rec.bid is not None and rec.ask is not None and rec.bid > 0 and rec.ask >= rec.bid:
            session = self.session(rec)
            exe, reason = is_executable(rec, session, self._now)
            inputs = dict(rec.inputs or {})
            inputs.update(bid=str(rec.bid), ask=str(rec.ask), bid_size=rec.bid_size, ask_size=rec.ask_size, basis="Cboe mid")
            return MarkView(iid, rec.mid, rec.quality, rec.source, "chain", rec.quote_time, rec.fetched_at, rec.delay_label, inputs,
                            exe and rec.quality in EXECUTABLE_QUALITIES, "" if exe else reason)
        under_rec = self._recs.get(inst.underlying_id) if inst.underlying_id else None
        chain = self._chains.get(inst.underlying_id) if inst.underlying_id else None
        px, inputs = self._p.option_model(inst, under_rec, chain, now=self._now, snapshot=self)
        if px is None:
            return unavailable(iid, inputs.get("note") or "option model unavailable")
        model_rec = QuoteRecord(instrument_id=iid, provider="model", source="option model", method="model", last=px, fetched_at=self._now, delay_s=0, delay_label="model",
                                currency=inst.currency, session_group=inst.session_group, inputs=inputs, quote_time=inputs.get("as_of"))
        model_rec.quality = freshness(model_rec, self.session(model_rec), self._now)
        self._computed[iid] = model_rec
        note = "computed (option model, not executable)" + ("" if rec is None else "; no usable Cboe NBBO")
        return MarkView(iid, px, model_rec.quality, "option model", "model", inputs.get("as_of"), self._now, "model", inputs, False, note)

    def _bond(self, inst) -> MarkView:
        iid = inst.instrument_id
        px, inputs = self._p.bond_mark(inst, snapshot=self)
        if px is None:
            return unavailable(iid, inputs.get("note") or "no par curve")
        rec = QuoteRecord(instrument_id=iid, provider="model", source="par curve model", method="curve", last=px, fetched_at=self._now, delay_s=0, delay_label="model",
                          currency=inst.currency, session_group=inst.session_group, inputs=inputs, quote_time=inputs.get("as_of"))
        rec.quality = freshness(rec, self.session(rec), self._now)
        self._computed[iid] = rec
        return MarkView(iid, px, rec.quality, "par curve model", "curve", inputs.get("as_of"), self._now, "model", inputs, False, "model price, not executable in this build")

    def _dispatch(self, inst, module: str, fn_name: str, loan: bool = False) -> MarkView:
        """PRIVATE / private-credit / SWAP marks from their product module.  Every row they need is read on
        ``MarkProvider.private_conn()`` (never a second pooled reader), declared as this thread's reading connection while
        the module computes; the private names come from the snapshot's one ``mark_inputs`` read."""
        services = self._p.services
        mod = getattr(services, module, None) if services is not None else None
        fn = getattr(mod, fn_name, None) if mod is not None else None
        if fn is None:
            return unavailable(inst.instrument_id, "not yet built")
        iid = inst.instrument_id
        if module == "private" and callable(getattr(mod, "mark_inputs", None)):
            inputs = self._private_names(mod)
            if inputs is not None:
                return self._normalise(iid, fn(services, inst, now=self._now, inputs=inputs), inst.kind)
        with self._p.private_conn() as conn:
            if loan:
                row = self._p.loan_row(inst, conn) if conn is not None else None
                if row is None:
                    return unavailable(iid, "no private loan row")
                view = getattr(mod, "mark_view", None)
                if callable(view):
                    return view(services, row, conn=conn, now=self._now)
                return self._normalise(iid, fn(services, row), "PRIVATE_LOAN")
            if module == "swaps" and conn is not None:
                # the contract row itself: swaps.mark then reads nothing (an id or the instrument would make it open a reader)
                row = conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (iid,)).fetchone()
                return self._normalise(iid, fn(services, dict(row)) if row is not None else None, inst.kind)
            return self._normalise(iid, fn(services, inst), inst.kind)

    def _private_names(self, mod) -> Optional[dict]:
        """``mod.mark_inputs(conn)`` - every company row and latest round - read once per snapshot on ``private_conn``."""
        with self._inputs_lock:
            if self._private_inputs is None:
                with self._p.private_conn() as conn:
                    if conn is None:
                        return None
                    self._private_inputs = mod.mark_inputs(conn)
            return self._private_inputs

    def _normalise(self, iid: str, result, kind: str) -> MarkView:
        if result is None:
            return unavailable(iid, "no mark from the product module")
        if isinstance(result, MarkView):
            return result
        if isinstance(result, tuple) and len(result) == 2:
            val, inputs = result
            if val is None:
                return unavailable(iid, (inputs or {}).get("note") or "unavailable")
            mark = from_c(int(val)) if isinstance(val, int) and not isinstance(val, bool) else qprice(val)
            if kind == "PRIVATE":
                q, method, src = "private", "private_round", "last reported round"
            elif kind == "SWAP":
                q, method, src = "computed", "swap_model", "swap model"
            else:                                  # PRIVATE_LOAN: a loan's (cents, inputs) from pc_mark
                q, method, src = "computed", "pc_model", "private credit model"
            inputs = dict(inputs or {})
            return MarkView(iid, mark, q, src, method, inputs.get("as_of"), self._now, "model", inputs, False, inputs.get("note") or "")
        return unavailable(iid, "unrecognised mark shape from the product module")

    def _compute_fx(self, ccy: str) -> MarkView:
        if ccy == "USD":
            return MarkView("USD", Decimal(1), "delayed", "const", "const", self._now, self._now, "", None, True, "")
        pair = fx_pair_id(ccy)
        rec = self._recs.get(pair)
        if rec is not None and rec.price() is not None:
            px = rec.price()
            inputs = dict(rec.inputs or {})
            if pair.startswith("USD"):
                if px == 0:
                    return unavailable(pair, "zero rate")
                inputs["inverted_from"] = str(px)
                px = qprice(Decimal(1) / px)
            exe, _reason = is_executable(rec, self.session(rec), self._now)
            return MarkView(pair, px, rec.quality, rec.source, rec.method, rec.quote_time, rec.fetched_at, rec.delay_label, inputs or None,
                            exe and rec.quality in EXECUTABLE_QUALITIES, "")
        fxs = self._p.fxstore
        got = fxs.usd_per(ccy) if fxs is not None else None
        if got is not None:
            rate, as_of, source = got
            return MarkView(pair, rate, "close", source, "daily_close", int(ny_of(self._now).replace(hour=17, minute=0, second=0, microsecond=0).timestamp())
                            if as_of == self._today else None, self._now, "daily (ECB reference)", {"as_of": as_of.isoformat(), "basis": "ECB reference rate"}, False,
                            f"ECB reference rate {as_of.isoformat()} (fallback; the Yahoo pair is unavailable)")
        return unavailable(pair, f"no USD rate for {ccy}")


# ---- the provider --------------------------------------------------------------------------------------------------------------
class MarkProvider:
    def __init__(self, cache, chains, instruments, curve, rates, sessions, clock, services=None, fxstore=None, log=None):
        self.cache, self.chains, self.instruments, self.curve, self.rates = cache, chains, instruments, curve, rates
        self.sessions, self.clock, self.services, self.fxstore, self.log = sessions, clock, services, fxstore, log
        self.custom: Dict[str, Callable] = {}

    def register_kind(self, kind: str, fn: Callable) -> None:
        """A product module may take over a kind: ``fn(services, inst) -> MarkView | (cents | Decimal, inputs) | None``."""
        self.custom[kind] = fn

    def snapshot(self, ids: Optional[Iterable[str]] = None) -> FrozenMarks:
        now = int(self.clock.epoch())
        recs = self.cache.get_many(list(ids) if ids is not None else self.cache.ids(), now) if self.cache is not None else {}
        chains = {}
        if self.chains is not None:
            for uid in self.chains.stats().get("cached", []):
                snap = self.chains.peek(uid)
                if snap is not None:
                    chains[uid] = snap
        return FrozenMarks(self, recs, chains, now)

    def mark_for(self, inst, at: Optional[int] = None) -> MarkView:
        snap = self.snapshot()
        return snap.mark(getattr(inst, "instrument_id", inst))

    # ---- computed marks -----------------------------------------------------------------------------------------------
    def risk_free(self, snapshot: FrozenMarks) -> Tuple[Optional[Decimal], dict]:
        """``^IRX``/100 with its freshness, else the par curve's 3M yield (``close``), else None."""
        rec = snapshot.quote("^IRX")
        if rec is not None and rec.last is not None:
            return rec.last / Decimal(100), {"source": "^IRX (13-week bill yield)", "value_pct": str(rec.last), "quality": rec.quality, "quote_time": rec.quote_time}
        if self.curve is not None:
            y = self.curve.par_yield("0.25")
            if y is not None:
                as_of = self.curve.as_of()
                return y / Decimal(100), {"source": "par curve 3M", "value_pct": str(y), "quality": "close", "as_of": as_of.isoformat() if as_of else None}
        return None, {"source": "none", "quality": "unavailable", "note": "no risk-free rate: ^IRX unavailable and no par curve"}

    def option_model(self, inst, under_rec: Optional[QuoteRecord], chain, now: Optional[int] = None, snapshot: Optional[FrozenMarks] = None) -> Tuple[Optional[Decimal], dict]:
        """``(price, inputs)``; ``(None, {note})`` when an input is missing.  ``inputs.qualities`` carries every input's
        quality so ``freshness()`` of the model record turns stale when an input is."""
        now = int(now if now is not None else self.clock.epoch())
        snap = snapshot if snapshot is not None else self.snapshot()
        today = ny_of(now).date()
        inputs: dict = {"model": None, "as_of": now}
        if under_rec is None or under_rec.price() is None:
            return None, {"note": "underlying quote unavailable", "qualities": ["unavailable"]}
        if not inst.expiry or inst.strike is None or inst.right not in ("C", "P"):
            return None, {"note": "option terms incomplete", "qualities": ["unavailable"]}
        expiry = date.fromisoformat(inst.expiry)
        days = max(0, (expiry - today).days)
        r, r_in = self.risk_free(snap)
        if r is None:
            return None, {"note": r_in.get("note"), "qualities": ["unavailable"]}
        under_inst = self.instruments.get(inst.underlying_id) if (self.instruments is not None and inst.underlying_id) else None
        q_raw = (under_inst.spec or {}).get("dividend_yield") if under_inst is not None else None
        q = Decimal(str(q_raw)) if q_raw is not None else Decimal(0)
        sigma, sigma_src = interpolate_iv(chain, inst.expiry, inst.right, inst.strike)
        chain_quality = None
        if chain is not None:
            chain_quality = "delayed" if chain.fetched_at >= now - CHAIN_STALE_S else "stale"
        if sigma is None and under_inst is not None and under_inst.kind in ("INDEX", "ETF") and self.rates is not None:
            vix = self.rates.vix()
            if vix is not None:
                sigma, sigma_src = float(vix[1]) / 100.0, f"FRED VIXCLS {vix[0].isoformat()}"
                chain_quality = "close"
        if sigma is None:
            return None, {"note": f"no implied volatility ({sigma_src})", "qualities": ["unavailable"]}
        S = under_rec.price()
        px, model = price_option(inst.right, inst.style or "AMERICAN", S, inst.strike, days / 365.0, float(r), float(q), float(sigma))
        qualities = [under_rec.quality, r_in.get("quality") or "unavailable"] + ([chain_quality] if chain_quality else [])
        inputs.update(model=model, S=str(S), K=str(inst.strike), T_days=days, r=str(r.quantize(PX6)), q=str(q), sigma=str(Decimal(repr(sigma)).quantize(PX6)),
                      sigma_source=sigma_src, underlying={"instrument_id": inst.underlying_id, "quality": under_rec.quality, "quote_time": under_rec.quote_time,
                                                            "source": under_rec.source}, rate=r_in, qualities=qualities, style=inst.style, right=inst.right)
        return px, inputs

    def bond_price(self, inst, curve, shifts: Optional[Dict[Decimal, Decimal]], oas_bps, asof: date) -> Tuple[Optional[Decimal], dict]:
        """Build 2 (§13.8): clean price per 100 at ``y = par yield(T) [+ shift] + OAS``; ``(None, {note})`` without a curve."""
        spec = inst.spec or {}
        coupon = spec.get("coupon")
        if coupon is None:
            return None, {"note": "no coupon in the master"}
        c = Decimal(str(coupon))
        if c < 1:
            c = c * 100
        maturity = spec.get("maturity") or inst.expiry
        if maturity:
            mat = date.fromisoformat(str(maturity))
        elif spec.get("years") is not None:
            mat = CORP_BASE_DATE + timedelta(days=int(round(float(spec["years"]) * 365.25)))
        else:
            return None, {"note": "no maturity in the master"}
        if asof >= mat:
            return None, {"note": "matured"}
        t_years = Decimal((mat - asof).days) / Decimal(365)
        y = curve.par_yield(t_years, shifts) if curve is not None else None
        if y is None:
            return None, {"note": "no par curve"}
        oas = Decimal(str(oas_bps or 0)) / Decimal(100)
        y_all = (y + oas).quantize(PX6)
        clean, accrued, dirty, detail = price_bond(c, mat, asof, y_all)
        detail.update(maturity=mat.isoformat(), t_years=str(t_years.quantize(PX4)), par_yield_pct=str(y), oas_bps=str(oas_bps or 0), clean=str(clean),
                      accrued=str(accrued), dirty=str(dirty))
        return clean, detail

    def bond_mark(self, inst, snapshot: Optional[FrozenMarks] = None) -> Tuple[Optional[Decimal], dict]:
        snap = snapshot if snapshot is not None else self.snapshot()
        now = snap.as_of()
        asof = ny_of(now).date()
        curve = self.curve
        if curve is None or curve.latest() is None:
            return None, {"note": "no par curve", "qualities": ["unavailable"]}
        shifts, shift_inputs = curve.intraday_shifts(self.cache) if self.cache is not None else ({}, {})
        spec = inst.spec or {}
        oas_bps, oas_in = None, None
        rating = spec.get("rating")
        if rating and self.rates is not None:
            bucket = str(rating).upper().rstrip("+-")
            got = self.rates.oas(bucket) if bucket in OAS_SERIES else None
            if got is not None:
                oas_bps = got[1] * 100
                oas_in = {"series": OAS_SERIES[bucket], "value_pct": str(got[1]), "as_of": got[0].isoformat(), "quality": "close"}
            elif spec.get("spread_bps") is not None:
                oas_bps = Decimal(str(spec["spread_bps"]))
                oas_in = {"series": "static spread (universe)", "value_bps": str(oas_bps), "quality": "close"}
        px, detail = self.bond_price(inst, curve, shifts, oas_bps, asof)
        if px is None:
            detail["qualities"] = ["unavailable"]
            return None, detail
        as_of = curve.as_of()
        qualities = ["close"] + [v.get("quality", "stale") for v in shift_inputs.values()] + ([oas_in["quality"]] if oas_in else [])
        detail.update(curve={"as_of": as_of.isoformat() if as_of else None, "quality": "close", "source": curve.latest().source}, shifts=shift_inputs, oas=oas_in,
                      qualities=qualities, as_of=now, basis="clean price per 100, semi-annual, ACT/ACT accrued")
        return px, detail

    def fx_forward(self, pair: str, value_date: date, at: Optional[int] = None, snapshot: Optional[FrozenMarks] = None) -> Tuple[Optional[Decimal], dict]:
        """Build 2 (§8.6): the CIP forward from the Yahoo spot and the two policy indices; ``(None, {note})`` when an input is missing."""
        snap = snapshot if snapshot is not None else self.snapshot()
        now = int(at if at is not None else snap.as_of())
        pair = pair.upper()
        base, quote = pair[:3], pair[3:]
        rec = snap.quote(pair)
        if rec is None or rec.price() is None:
            return None, {"note": f"no spot for {pair}", "qualities": ["unavailable"]}
        if self.rates is None:
            return None, {"note": "no rate store", "qualities": ["unavailable"]}
        rb, rq = self.rates.policy_rate(base), self.rates.policy_rate(quote)
        if rb is None or rq is None:
            missing = base if rb is None else quote
            return None, {"note": f"{self.rates.policy_series(missing) or missing} unavailable", "qualities": ["unavailable"]}
        days = (value_date - ny_of(now).date()).days
        if days < 0:
            return None, {"note": "value date in the past", "qualities": ["unavailable"]}
        f = cip_forward(rec.price(), rb[1] / Decimal(100), rq[1] / Decimal(100), days, tables.daycount(base), tables.daycount(quote))
        inputs = {"S": str(rec.price()), "spot": {"quality": rec.quality, "quote_time": rec.quote_time, "source": rec.source}, "days": days,
                  "r_base": {"ccy": base, "series": self.rates.policy_series(base), "value_pct": str(rb[1]), "as_of": rb[0].isoformat(), "basis": tables.daycount(base)},
                  "r_quote": {"ccy": quote, "series": self.rates.policy_series(quote), "value_pct": str(rq[1]), "as_of": rq[0].isoformat(), "basis": tables.daycount(quote)},
                  "qualities": [rec.quality, "close", "close"], "as_of": now, "method": "cip"}
        return f, inputs

    # ---- the rows behind the PRIVATE / private-credit / SWAP marks (module docstring ONE READER PER THREAD) ---------------
    def _db(self):
        db = getattr(self.services, "db", None) if self.services is not None else None
        return db if db is not None else getattr(self.cache, "db", None)

    @contextmanager
    def private_conn(self) -> Iterator[Optional[sqlite3.Connection]]:
        """The connection those marks read on - never a second pooled reader: the writer inside this thread's own
        transaction (a handler sees its uncommitted rows), else the connection this thread already reads on (``reading``),
        else a short-lived read-only connection of the provider's own, closed on exit.  It is declared as the thread's
        reading connection for the block.  None without a database."""
        db = self._db()
        if db is None:
            yield None
            return
        if _in_own_tx(db):
            with db.reader() as c, reading(c, db):      # Database.reader hands out the writer itself here: no pool
                yield c
            return
        amb = ambient_conn(db)
        if amb is not None:
            yield amb
            return
        path = getattr(db, "path", None)
        if not path:
            yield None
            return
        conn = _side_conn(path)
        try:
            with reading(conn, db):
                yield conn
        finally:
            conn.close()

    def instrument(self, iid: str):
        """The instrument from the master (its cache answers without a read).  A cache miss - a row added after the last
        load, e.g. a just-committed loan - is read on this thread's connection (the writer in its own transaction, else its
        reading connection, else a side connection opened only for that miss): never a second pooled reader."""
        master = self.instruments
        if master is None:
            return None
        db = self._db()
        if db is None or _in_own_tx(db):
            return master.get(iid)
        amb = ambient_conn(db)
        if amb is not None:
            return master.get(iid, amb)
        path = getattr(db, "path", None)
        if not path:
            return master.get(iid)
        lazy = _LazySideConn(path)
        try:
            return master.get(iid, lazy)
        finally:
            lazy.close()

    def loan_row(self, inst, conn=None) -> Optional[dict]:
        """The ``private_loans`` row of a loan's PRIVATE instrument, read on ``conn`` (else on ``private_conn``)."""
        if conn is None:
            with self.private_conn() as c:
                return self.loan_row(inst, c) if c is not None else None
        row = conn.execute("SELECT * FROM private_loans WHERE instrument_id=?", (inst.instrument_id,)).fetchone()
        return dict(row) if row is not None else None


class _LazySideConn:
    """Opens ``_side_conn`` on the first ``execute`` only (an instrument-cache miss); ``close()`` closes it if it opened."""

    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[sqlite3.Connection] = None

    def execute(self, sql: str, params=()):
        if self.conn is None:
            self.conn = _side_conn(self.path)
        return self.conn.execute(sql, params)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


__all__ = ["MarkProvider", "FrozenMarks", "price_bond", "accrued_interest", "coupon_period", "cip_forward", "interpolate_iv", "price_option", "add_months",
           "CORP_BASE_DATE", "ambient_conn", "reading", "shared_reader"]
