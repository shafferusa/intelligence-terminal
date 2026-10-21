"""Bonds and rates (Build 2; LIMITED in Build 1): model benchmarks, curated corporates, computed marks for the DEBT page,
the ``bonds.trade`` fill at the model price, nightly ACT/ACT accrual, coupons on schedule and redemption at par
(TERMINAL_DESIGN.md §2.12 products/bonds.py, §5.4, §8.6, §11.2, §11.3, §12.1, §13.8, §19.13; TERMINAL_WORKPLAN.md WP-10).

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- COMPUTED: a bond's mark is the par-curve model price of ``marks.MarkProvider.bond_mark`` (clean price per 100,
  quality ``computed``, never executable through the order ticket).  Bond rows are seeded / listed with
  ``support = LIMITED`` and ``account_kinds = []``, so the ticket refuses them (NOT_TRADEABLE_HERE) and the Debt
  Instruments page shows the curve and the list with "model price, not executable in this build".
- BUILD2_BONDS: DEBT execution exists only behind the ``build2_bonds`` setting (``settings`` table, default off).
  ``bonds.trade {account_id, instrument_id, side: BUY | SELL, qty (face), …}`` answers 400 ``UNSUPPORTED`` "model
  price, not executable in this build" while the flag is off; with it on the fill executes at once at the command
  snapshot's computed clean price plus the accrued interest of the trade date, disclosed as "model price".  Only a
  DEBT account, only a BOND of the master, only while the US_BONDS session (08:00–17:00 ET) is open, only ACTIVE and
  not yet matured, face a multiple of the 1,000 lot.  SELL_SHORT / BUY_TO_COVER are NOT_IN_THIS_BUILD here (bond
  shorts need a borrow; a later slice).
- FACE: ``qty`` on fills / lots is the face in units of USD 1 (``lot`` 1,000); ``contract_value_c(px, face)`` =
  face × px / 100 through the master's multiplier 0.01, so a lot's ``unit_cost`` is the clean price per 100 and its
  ``cost_c`` the clean principal (L8 / L9 hold exactly: 1,000,000 × 96.1027 × 0.01 = 961,027.00).
- ACCRUED (the §19.13 convention): accrued interest per 100 on date D is ``coupon / 2 × (D − prev_coupon) /
  (next_coupon − prev_coupon)`` quantised to 4 decimals (``marks.accrued_interest``; 2.25 × 45 / 184 = 0.5503 after
  45 days of a 184-day period), and the cents follow from it: ``to_c(face × accrued_4dp / 100)`` (5,503.00 on
  1,000,000).  On a coupon date the new period starts (accrued 0): the coupon belongs to the holder before that day.
- FILL (§5.4): BUY ``Dr 1110 clean / Dr 1220 accrued / Dr 5000 fee / Cr 2100 clean + accrued + fee``, settlement row
  T+1, lot at the clean price, funded first (``execution.fund``: own available cash, else one Treasury sweep-in of
  exactly the shortfall).  SELL ``Dr 1200 clean + accrued − fee / Dr 5000 / Cr 1110 cost FIFO / Cr 1220 the carried
  accrued relieved pro rata / Cr 4300 the rest of the accrued received (the days since the last night) / Cr 4000
  realised = clean − cost`` (realised excludes accrued; COST_BASIS, FIFO).  Fee USD 0.02 per 100 face, minimum
  USD 5.00 (§12.1; 200.00 on 1,000,000).
- ACCRUAL (§11.3, cumulative, never drifting): one ``accrual_state`` row per (account, instrument), kind ``bond``,
  ``ref_id = "{account}:{instrument}"``: ``base_c`` carries the FACE held (units, not cents), ``rate`` the coupon
  fraction per period (0.0225 for 4.500 %), ``day_basis`` the days of the current coupon period, ``segment_start`` the
  day after the last coupon date, ``accrued_through`` the last night posted, ``cum_c`` the accrued interest carried
  in 1220.  Night D (due from 00:30 ET of D + 1, calendar days, weekends included) posts ``target(D) − cum_c`` where
  ``target(D) = to_c(face(D) × accrued_4dp(D) / 100)`` and ``face(D)`` is the face held at the close of D (Σ signed
  fills with trade date ≤ D): ``Dr 1220 / Cr 4300``, idem ``accr:bond:{account}:{instrument}:{D}``, business date D.
  A purchase adds its accrued to ``cum_c``; a sale relieves the carried accrued pro rata; nights post in date order
  (ACCRUAL_GAP otherwise); the night of a coupon date waits for that coupon (COUPON_PENDING) — nothing is lumped.
- COUPON (``bonds.coupon``, key ``{account}:{instrument}:{coupon_date}``, due from 00:40 ET of the payment date =
  coupon date rolled following): ``Dr 1010 face × coupon / 2 / Cr 1220 cum_c / Cr 4300 coupon − cum_c`` (the last
  day's interest is recognised at payment, so the sum of the nights and the payment equals the coupon exactly), idem
  ``coupon:{account}:{instrument}:{C}``; the segment restarts at C + 1 with ``cum_c`` 0.  The holder is whoever held
  face at the close of C − 1 (a buy on C carries no accrued and gets no coupon).  Bills (zero coupon) pay nothing.
- MATURITY (``bonds.mature``, key ``{account}:{instrument}``, due from 00:45 ET of the maturity date rolled following,
  after the final coupon): a commission-free MATURITY fill at 100 — ``Dr 1010 face / Cr 1110 cost FIFO / Cr 4000
  realised`` (realised = 100 − cost per 100: 38,973.00 on 1,000,000 bought at 96.1027), cash the same day (no
  settlement row), idem ``fill:{fill_id}`` with ``fills.idem_key = maturity:{account}:{instrument}``; the accrual
  state row is deleted and the instrument is marked EXPIRED once no account holds it.
- BENCHMARKS (§13.8): on the first business day of each month ``bonds.list`` (key ``yyyy-mm``) lists one model
  benchmark per tenor — 1M 3M 6M 1Y bills (zero coupon) and 2 3 5 7 10 20 30Y notes / bonds — issue = the listing
  date, maturity = issue + tenor, coupon = the par yield of the latest curve at that tenor rounded to the nearest
  1/8 % (ties to even), semi-annual ACT/ACT, named "US Treasury 4.250 % 02-Nov-2036 (model benchmark)" / "US Treasury
  bill 02-Feb-2027 (model benchmark)", id ``UST-{tenor}-{yyyymm}``; without a curve the month is SKIPPED
  (CURVE_MISSING, retried) — a coupon is never invented.  Curated corporates come from ``universe.bonds`` (seeded by
  the core: coupon as a fraction, ``years`` → maturity = 2026-09-18 + years, rating → OAS bucket) and are labelled
  "corporate, computed price".  Matured bonds nobody holds are marked EXPIRED by the same job.
- JOBS: the handlers write their own ``jobs_done`` row (DONE, or SKIPPED with a note and one WARN attention per
  (code, key)); the key helpers (``accrue_keys``, ``coupon_keys``, ``maturity_keys``, ``listing_keys``) derive the
  due keys from state so a catch-up after downtime posts every night once, in order, before the coupon it feeds.
  Money is integer cents / Decimal; nothing here is a float except the universe's ``years`` → days conversion, which
  mirrors ``marks.bond_price`` exactly so the terms and the mark agree.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple

from .. import execution
from ..books import account_kind_for, account_row, lifetime_row
from ..clock import epoch_of, ny_of, trade_date as _trade_date
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, get_setting, next_id
from ..ledger import Entry, cr, dr, open_lot, position, relieve_fifo
from ..lifecycle_jobs import ACCRUE_FROM_ET
from ..marks import CORP_BASE_DATE, accrued_interest, add_months, coupon_period
from ..units import fmt_money, from_c, qprice, to_c

SOURCE = "bonds"
KIND = "bond"                      # accrual_state.kind
GROUP = "US_BONDS"
LOT = 1000
FREQ = 2
JOB_ACCRUE = "BOND_ACCRUE"
JOB_COUPON = "BOND_COUPON"
JOB_MATURITY = "BOND_MATURITY"
JOB_LISTING = "BOND_LISTING"
COUPON_ET = "00:40"
MATURITY_ET = "00:45"
LISTING_ET = "00:15"
SETTING_FLAG = "build2_bonds"
UNSUPPORTED_TEXT = "model price, not executable in this build"
EIGHTH = Decimal("0.125")
PX4 = Decimal("0.0001")
HUNDRED = Decimal(100)
# (tenor label, months to maturity, zero coupon?)
BENCHMARK_TENORS: List[Tuple[str, int, bool]] = [("1M", 1, True), ("3M", 3, True), ("6M", 6, True), ("1Y", 12, True), ("2Y", 24, False), ("3Y", 36, False),
                                                  ("5Y", 60, False), ("7Y", 84, False), ("10Y", 120, False), ("20Y", 240, False), ("30Y", 360, False)]
TENOR_YEARS: Dict[str, Decimal] = {"1M": Decimal(1) / Decimal(12), "3M": Decimal("0.25"), "6M": Decimal("0.5"), "1Y": Decimal(1), "2Y": Decimal(2), "3Y": Decimal(3),
                                   "5Y": Decimal(5), "7Y": Decimal(7), "10Y": Decimal(10), "20Y": Decimal(20), "30Y": Decimal(30)}


# ---- terms and the pure schedule ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class BondTerms:
    coupon_pct: Decimal            # annual coupon in percent (4.5 = 4.500 %)
    maturity: date
    freq: int = FREQ
    issue: Optional[date] = None
    zero_coupon: bool = False
    benchmark: bool = False

    @property
    def coupon_per_period(self) -> Decimal:
        """The coupon fraction per period: 0.0225 for 4.500 % semi-annual."""
        return self.coupon_pct / HUNDRED / Decimal(self.freq)

    def to_dict(self) -> dict:
        return {"coupon_pct": str(self.coupon_pct), "maturity": self.maturity.isoformat(), "freq": self.freq, "issue": None if self.issue is None else self.issue.isoformat(),
                "zero_coupon": self.zero_coupon, "benchmark": self.benchmark, "daycount": "ACT/ACT"}


def bond_terms(inst) -> BondTerms:
    """Coupon (percent) and maturity of a BOND row: ``spec.coupon_pct`` (benchmarks), else ``spec.coupon`` with the
    master's convention (a value below 1 is a fraction: 0.0485 → 4.85 %); maturity ``spec.maturity`` / ``expiry``, else
    ``years`` from the curated universe (2026-09-18 + round(years × 365.25) days, exactly as ``marks.bond_price``)."""
    spec = inst.spec or {}
    if spec.get("coupon_pct") is not None:
        c = Decimal(str(spec["coupon_pct"]))
    elif spec.get("coupon") is not None:
        c = Decimal(str(spec["coupon"]))
        if c < 1:
            c = c * HUNDRED
    else:
        raise RejectError("BOND_TERMS_MISSING", f"{inst.instrument_id}: the master has no coupon for this bond")
    maturity = spec.get("maturity") or inst.expiry
    if maturity:
        mat = date.fromisoformat(str(maturity))
    elif spec.get("years") is not None:
        mat = CORP_BASE_DATE + timedelta(days=int(round(float(spec["years"]) * 365.25)))
    else:
        raise RejectError("BOND_TERMS_MISSING", f"{inst.instrument_id}: the master has no maturity for this bond")
    issue = date.fromisoformat(str(spec["issue"])) if spec.get("issue") else None
    return BondTerms(c, mat, int(spec.get("freq") or FREQ), issue, bool(spec.get("zero_coupon")) or c == 0, bool(spec.get("benchmark")))


def period_of(terms: BondTerms, d: date) -> Optional[Tuple[date, date]]:
    """``(prev_coupon, next_coupon)`` with ``prev ≤ d < next`` (None on or after maturity)."""
    if d >= terms.maturity:
        return None
    prev, nxt, _n = coupon_period(terms.maturity, d, terms.freq)
    return prev, nxt


def accrued_per_100(terms: BondTerms, d: date) -> Decimal:
    """Accrued interest per 100 on ``d`` (4 dp, convention ACCRUED); 0 for bills and from maturity on."""
    if terms.zero_coupon or terms.coupon_pct == 0:
        return Decimal("0.0000")
    per = period_of(terms, d)
    if per is None:
        return Decimal("0.0000")
    return accrued_interest(terms.coupon_pct, per[0], per[1], d, terms.freq)


def accrued_cents(face: int, terms: BondTerms, d: date) -> int:
    """``to_c(face × accrued_4dp / 100)`` — 5,503.00 for 1,000,000 face at 0.5503."""
    return to_c(Decimal(int(face)) * accrued_per_100(terms, d) / HUNDRED)


def coupon_cents(face: int, terms: BondTerms) -> int:
    """One coupon payment on ``face``: ``to_c(face × coupon / freq / 100)`` — 22,500.00 for 4.500 % on 1,000,000."""
    if terms.zero_coupon:
        return 0
    return to_c(Decimal(int(face)) * terms.coupon_per_period)


def coupon_dates(terms: BondTerms, after: date, through: date) -> List[date]:
    """Coupon dates ``C`` with ``after < C ≤ through`` (the maturity date is the last coupon date), ascending."""
    if terms.zero_coupon:
        return []
    step = 12 // terms.freq
    out: List[date] = []
    k = 0
    while k < 2000:
        c = add_months(terms.maturity, -step * k)
        if c <= after:
            break
        if c <= through:
            out.append(c)
        k += 1
    return sorted(out)


def round_to_eighth(y_pct) -> Decimal:
    """The nearest 1/8 % (ties to even): 4.26 → 4.25, 4.4375 → 4.5, 4.3125 → 4.25."""
    return ((Decimal(str(y_pct)) / EIGHTH).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * EIGHTH).quantize(Decimal("0.001"))


def benchmark_id(tenor: str, listing: date) -> str:
    return f"UST-{tenor}-{listing.strftime('%Y%m')}"


def benchmark_name(coupon_pct: Optional[Decimal], maturity: date, zero_coupon: bool) -> str:
    mat = maturity.strftime("%d-%b-%Y")
    if zero_coupon:
        return f"US Treasury bill {mat} (model benchmark)"
    return f"US Treasury {Decimal(coupon_pct).quantize(Decimal('0.001'))} % {mat} (model benchmark)"


def through_night(now_ts: int) -> date:
    """The last night due at ``now``: yesterday from 00:30 ET, else the day before (§11.1 ACCRUE_DAILY)."""
    today = ny_of(int(now_ts)).date()
    return today - timedelta(days=1) if int(now_ts) >= epoch_of(today, ACCRUE_FROM_ET) else today - timedelta(days=2)


def flag_on(conn) -> bool:
    """``settings.build2_bonds`` (default off); accepts true / 1 / "true" / "on" / "yes"."""
    v = get_setting(conn, SETTING_FLAG, False)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "on", "yes")
    return bool(v)


# ---- listings ---------------------------------------------------------------------------------------------------------------------
def ensure_bond(tx, master, instrument_id: str, name: str, coupon_pct, maturity: date, issue: Optional[date] = None, spec: Optional[dict] = None,
                support: str = "LIMITED") -> bool:
    """Insert one BOND row (INSERT OR IGNORE; the master's conventions: multiplier 0.01, tick 0.001, lot 1,000, US_BONDS,
    T+1, no account kinds — execution is ``bonds.trade`` behind the flag).  Returns True when inserted."""
    c = Decimal(str(coupon_pct)).quantize(Decimal("0.001"))
    row_spec = {"coupon": str(c / HUNDRED), "coupon_pct": str(c), "maturity": maturity.isoformat(), "issue": None if issue is None else issue.isoformat(),
                "freq": FREQ, "daycount": "ACT/ACT", "zero_coupon": c == 0}
    row_spec.update(spec or {})
    inserted = master._insert(tx.conn, int(tx.now.timestamp()), instrument_id=instrument_id, kind="BOND", symbol=instrument_id, name=name, currency="USD",
                              multiplier="0.01", tick="0.001", lot=LOT, session_group=GROUP, settle_days=1, account_kinds=[], expiry=maturity.isoformat(),
                              spec=row_spec, support=support)
    if inserted:
        master.refresh(instrument_id, tx.conn)
    return inserted


def ensure_benchmarks(tx, master, curve, listing: date, cal) -> dict:
    """List the month's model benchmarks (module docstring BENCHMARKS) from the curve's latest par yields.  Pure of
    the clock: ``listing`` is the listing date.  Returns ``{listed, ids, skipped, curve_as_of}``; without a curve
    nothing is listed and ``skipped`` says why."""
    pc = curve.latest() if curve is not None else None
    if pc is None or not pc.points:
        return {"listed": 0, "ids": [], "skipped": "no par curve: the benchmark coupons cannot be set", "curve_as_of": None}
    ids: List[str] = []
    n = 0
    for tenor, months, zero in BENCHMARK_TENORS:
        y = curve.par_yield(TENOR_YEARS[tenor])
        if y is None:
            continue
        maturity = add_months(listing, months)
        coupon = Decimal("0.000") if zero else round_to_eighth(y)
        iid = benchmark_id(tenor, listing)
        spec = {"benchmark": True, "tenor": tenor, "par_yield_pct": str(y), "curve_as_of": pc.as_of.isoformat(), "curve_source": pc.source,
                "note": "model benchmark: coupon = par yield at listing rounded to 1/8 %; priced from the live par curve (computed)"}
        if ensure_bond(tx, master, iid, benchmark_name(coupon, maturity, zero), coupon, maturity, listing, spec):
            n += 1
            ids.append(iid)
    return {"listed": n, "ids": ids, "skipped": None, "curve_as_of": pc.as_of.isoformat()}


def expire_matured(conn, master, today: date, ts: int) -> List[str]:
    """Mark matured BOND rows nobody holds EXPIRED (held ones expire through ``bonds.mature``)."""
    rows = conn.execute("SELECT i.instrument_id FROM instruments i WHERE i.kind='BOND' AND i.status='ACTIVE' AND i.expiry IS NOT NULL AND i.expiry <= ?"
                        " AND NOT EXISTS (SELECT 1 FROM v_positions v WHERE v.instrument_id=i.instrument_id AND v.qty <> 0)", (today.isoformat(),)).fetchall()
    out = []
    for r in rows:
        conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE instrument_id=? AND status='ACTIVE'", (ts, r["instrument_id"]))
        master.refresh(r["instrument_id"], conn)
        out.append(r["instrument_id"])
    return out


# ---- services / rows ----------------------------------------------------------------------------------------------------------------
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


def _bond(services, conn, iid: str):
    inst = services.instruments.get(iid, conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid} is not in the master")
    if inst.kind != "BOND":
        raise RejectError("NOT_A_BOND", f"{iid} is a {inst.kind}, not a bond")
    return inst


def face_at(conn, account: str, iid: str, d: date) -> int:
    """Face held at the close of ``d``: Σ signed fills (BUY +, SELL −) with trade date ≤ d."""
    row = conn.execute("SELECT COALESCE(SUM(CASE side WHEN 'BUY' THEN qty ELSE -qty END), 0) FROM fills WHERE account_id=? AND instrument_id=? AND trade_date<=?",
                       (account, iid, d.isoformat())).fetchone()
    return int(row[0] or 0)


def _ref(account: str, iid: str) -> str:
    return f"{account}:{iid}"


def state_row(conn, account: str, iid: str):
    return conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (KIND, _ref(account, iid))).fetchone()


def _write_state(conn, account: str, iid: str, terms: BondTerms, face: int, through: date, cum_c: int) -> None:
    per = period_of(terms, through + timedelta(days=1)) or period_of(terms, through)
    if per is None:
        start, basis = terms.maturity, 1
    else:
        start, basis = per[0] + timedelta(days=1), (per[1] - per[0]).days
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,?,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate, day_basis=excluded.day_basis,"
                 " accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (KIND, _ref(account, iid), start.isoformat(), int(face), str(qprice(terms.coupon_per_period)), int(basis), through.isoformat(), int(cum_c)))


def _delete_state(conn, account: str, iid: str) -> None:
    conn.execute("DELETE FROM accrual_state WHERE kind=? AND ref_id=?", (KIND, _ref(account, iid)))


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


def _skip(ctx: TxContext, job: str, key: str, code: str, message: str, account_id: Optional[str], iid: Optional[str], details: Optional[dict] = None) -> dict:
    """A PENDING outcome: jobs_done SKIPPED (the key stays due), one open WARN attention per (code, key), an event."""
    _job_done(ctx, job, key, "SKIPPED", f"{code}: {message}")
    existing = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
    attention_id = int(existing["id"]) if existing else ctx.attention("WARN", SOURCE, code, message,
                                                                          dict(details or {}, account_id=account_id, ref_type=KIND, ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=account_id, instrument_id=iid, ref_type="job", ref_id=key, job=job, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "code": code, "message": message, "key": key, "attention_id": attention_id}


def _done_noop(ctx: TxContext, job: str, key: str, code: str, note: str) -> dict:
    """A TERMINAL no-op: DONE with the reason, nothing posted."""
    _job_done(ctx, job, key, "DONE", f"{code}: {note}")
    return {"status": "DONE", "skipped": True, "posted": False, "code": code, "note": note, "key": key}


def _key3(payload: dict, what: str) -> Tuple[str, str, str]:
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
        raise RejectError("KEY_INVALID", f"{what} needs account_id, instrument_id and an ISO date (got {ds!r})") from e
    if not account or not iid:
        raise RejectError("KEY_INVALID", f"{what} needs account_id and instrument_id")
    return account, iid, d.isoformat()


# ---- the computed quote for the page / ticket --------------------------------------------------------------------------------------------
def quote_view(services, inst, now_ts: Optional[int] = None, snapshot=None, conn=None) -> dict:
    """The DEBT page / ticket figures: the computed clean mark (with its provenance), the accrued of today, the dirty
    price, the terms and whether DEBT execution is enabled (the flag), never a fabricated number."""
    snap = snapshot if snapshot is not None else services.marks.snapshot([inst.instrument_id])
    now = int(now_ts if now_ts is not None else services.clock.epoch())
    today = ny_of(now).date()
    view = snap.mark(inst.instrument_id)
    terms = bond_terms(inst)
    accrued = accrued_per_100(terms, today)
    clean = None if view.mark is None else qprice(view.mark)
    if conn is not None:
        enabled = flag_on(conn)
    else:
        with services.db.reader() as c:
            enabled = flag_on(c)
    return {"instrument_id": inst.instrument_id, "mark": view.to_dict(), "clean": None if clean is None else str(clean), "accrued": str(accrued),
            "dirty": None if clean is None else str((clean + accrued).quantize(PX4)), "as_of_date": today.isoformat(), "terms": terms.to_dict(),
            "label": "model benchmark" if terms.benchmark else "corporate, computed price", "executable_here": enabled and view.quality == "computed",
            "note": "model price" if enabled else UNSUPPORTED_TEXT, "fee_basis": "USD 0.02/100 face, min 5.00", "settlement": "T+1"}


# ---- bonds.trade ---------------------------------------------------------------------------------------------------------------------------
@dataclass
class BondTrade:
    account_id: str
    acct: dict
    lifetime_id: str
    inst: Any
    terms: BondTerms
    side: str
    face: int
    trade_date: date
    settle_date: date
    clean: Decimal
    accrued: Decimal              # per 100, 4 dp
    clean_c: int
    accrued_c: int
    fee_c: int
    fee_basis: str
    mark: Any                     # MarkView
    position: int
    state: Optional[dict]


def validate_trade(services, payload: dict, conn, now_ts: int, snapshot) -> BondTrade:
    """§10.2-style validation of a ``bonds.trade`` request (module docstring BUILD2_BONDS); raises ``RejectError`` 400 /
    ``ConflictError`` 409 with the code; returns the priced trade."""
    if not flag_on(conn):
        raise RejectError("UNSUPPORTED", UNSUPPORTED_TEXT, {"setting": SETTING_FLAG, "hint": "DEBT execution is a Build 2 feature behind settings.build2_bonds"})
    account_id = str(payload.get("account_id") or "")
    if not account_id:
        raise RejectError("ACCOUNT_UNKNOWN", "account_id is required")
    acct = _open_account(conn, account_id)
    lid = acct["lifetime_id"]
    lrow = lifetime_row(conn, lid)
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {lid} is closed (History is read-only)", {"lifetime_id": lid})
    acc = getattr(services, "accounting", None)
    if acc is not None and hasattr(acc, "is_held") and acc.is_held(lid):
        raise ConflictError("INTEGRITY_HOLD", f"lifetime {lid} is on an integrity hold: the whole-ledger check failed and is unresolved", {"lifetime_id": lid})
    if account_kind_for(acct) != "DEBT":
        raise RejectError("NOT_TRADEABLE_HERE", f"bonds trade in a Debt Instruments account, not in {account_id} ({acct['kind']})")
    iid = str(payload.get("instrument_id") or "")
    inst = _bond(services, conn, iid)
    if inst.support in ("UNSUPPORTED", "COMPUTED"):
        raise RejectError("UNSUPPORTED", f"{inst.symbol} is {inst.support.lower()} in this build")
    if inst.status != "ACTIVE":
        raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} is {inst.status}")
    terms = bond_terms(inst)
    now = int(now_ts)
    tdate = _trade_date(ny_of(now), GROUP, services.cal)
    if terms.maturity <= tdate:
        raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} matures on {terms.maturity.isoformat()}: no trades on or after the maturity date")
    side = str(payload.get("side") or "").upper()
    if side in ("SELL_SHORT", "BUY_TO_COVER", "STO", "BTC"):
        raise RejectError("NOT_IN_THIS_BUILD", "bond shorts need a borrow (coupons in lieu to 5400): not in this build")
    if side not in ("BUY", "SELL"):
        raise RejectError("SIDE_INVALID", f"side {side!r}: use BUY or SELL")
    raw = payload.get("qty") if payload.get("qty") is not None else payload.get("face")
    try:
        face = int(raw)
    except (TypeError, ValueError) as e:
        raise RejectError("QTY_INVALID", f"qty (face) must be a whole number, got {raw!r}") from e
    lot = max(1, int(inst.lot or LOT))
    if face <= 0 or face % lot:
        raise RejectError("QTY_INVALID", f"face must be a positive multiple of {lot:,}", {"lot": lot})
    session = services.sessions.state(GROUP, now)
    if not session.open:
        raise RejectError("SESSION_CLOSED", f"the {GROUP} session is {session.phase.lower()}: model-price execution only while the bond market is open (08:00–17:00 ET)",
                          {"phase": session.phase, "next_open": session.next_open})
    pos, _cost = position(conn, account_id, iid)
    st = state_row(conn, account_id, iid)
    if st is not None and st["accrued_through"] and date.fromisoformat(st["accrued_through"]) < through_night(now):
        raise ConflictError("ACCRUAL_PENDING", f"{inst.symbol}: the overnight accruals of this position (after {st['accrued_through']}) have not been posted yet;"
                            " the lifecycle catches up within a tick — retry in a moment", {"accrued_through": st["accrued_through"]})
    if side == "SELL":
        if pos < face:
            raise RejectError("OVERSELL", f"SELL {face:,} {inst.symbol} but the position is {max(pos, 0):,} face", {"position": pos, "qty": face})
        first = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE account_id=? AND instrument_id=?", (account_id, iid)).fetchone()
        if first is not None and first["d"]:
            for c in coupon_dates(terms, date.fromisoformat(first["d"]), tdate):
                row = _job_row(conn, JOB_COUPON, f"{account_id}:{iid}:{c.isoformat()}")
                if face_at(conn, account_id, iid, c - timedelta(days=1)) > 0 and (row is None or row["status"] != "DONE"):
                    raise ConflictError("COUPON_PENDING", f"{inst.symbol}: the coupon of {c.isoformat()} has not been paid yet; retry after the lifecycle pays it",
                                        {"coupon_date": c.isoformat()})
    view = snapshot.mark(iid)
    if view is None or view.mark is None or view.quality == "unavailable":
        raise ConflictError("MARK_UNAVAILABLE", f"{inst.symbol}: no computed mark ({(view.note if view is not None else '') or 'no par curve'}); nothing is invented",
                            {"quality": None if view is None else view.quality})
    if view.quality != "computed":
        raise ConflictError("QUOTE_STALE", f"{inst.symbol}: the model mark is {view.quality} ({view.note or 'inputs not fresh'}); model-price execution needs a computed mark",
                            {"quality": view.quality, "note": view.note})
    clean = qprice(view.mark)
    if clean <= 0:
        raise ConflictError("MARK_UNAVAILABLE", f"{inst.symbol}: the model mark {clean} is not a price")
    accrued = accrued_per_100(terms, tdate)
    clean_c = inst.contract_value_c(clean, face)
    accrued_c = to_c(Decimal(face) * accrued / HUNDRED)
    fee_c, fee_basis = execution.fee_for(services, acct, inst, face, clean_c)
    settle = services.cal.settlement_date(tdate, inst.settle_days)
    return BondTrade(account_id, dict(acct), lid, inst, terms, side, face, tdate, settle, clean, accrued, clean_c, accrued_c, int(fee_c), fee_basis, view, pos,
                     None if st is None else dict(st))


def _quote_json(v: BondTrade) -> str:
    m = v.mark
    return dumps({"mark": m.to_dict() if hasattr(m, "to_dict") else None, "clean": str(v.clean), "accrued": str(v.accrued), "dirty": str((v.clean + v.accrued).quantize(PX4)),
                  "basis": "model price: computed clean price of the command's mark snapshot + ACT/ACT accrued of the trade date (COMPUTED, ACCRUED)",
                  "terms": v.terms.to_dict(), "trade_date": v.trade_date.isoformat()})


def _insert_fill(ctx: TxContext, v: BondTrade, fill_id: str, effect: str, entry_id: int, idem: str, origin: str = "ORDER", basis: str = "model",
                 settle: Optional[date] = None, price: Optional[Decimal] = None, gross_c: Optional[int] = None, fee_c: Optional[int] = None, quote_json: Optional[str] = None,
                 trade_date: Optional[date] = None) -> None:
    ctx.conn.execute(
        "INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
        " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, None, v.account_id, v.inst.instrument_id, effect, int(v.face), str(price if price is not None else v.clean), int(gross_c if gross_c is not None else v.clean_c),
         int(fee_c if fee_c is not None else v.fee_c), "USD", "1", ctx.ts, (trade_date or v.trade_date).isoformat(), settle.isoformat() if settle else None, origin, basis,
         quote_json if quote_json is not None else _quote_json(v), None, None, None, entry_id, idem))


def _settlement(ctx: TxContext, v: BondTrade, fill_id: str, amount_c: int, gl: str) -> str:
    settle_id = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (settle_id, fill_id, None, v.account_id, "CASH", "USD", int(amount_c), gl, v.settle_date.isoformat()))
    return settle_id


def h_trade(ctx: TxContext, payload: dict) -> dict:
    """``bonds.trade``: module docstring BUILD2_BONDS / FILL.  One immediate fill at the model price inside the
    command's transaction; returns the fill, the entry lines' amounts, the settlement row, the lot and the accrual state."""
    services = _services(ctx)
    conn = ctx.conn
    v = validate_trade(services, payload, conn, ctx.ts, ctx.marks)
    aid, inst, iid = v.account_id, v.inst, v.inst.instrument_id
    fill_id = next_id(conn, "fill")
    idem_fill = f"bonds:{ctx.request_id}"
    funding = None
    st = v.state
    cum_before = int(st["cum_c"]) if st is not None else 0
    if v.side == "BUY":
        total = v.clean_c + v.accrued_c + v.fee_c
        funding = execution.fund(ctx, aid, "USD", total, "fill", fill_id, v.settle_date, services=services)
        lines = [dr(aid, "1110", "USD", v.clean_c, instrument_id=iid, memo=f"clean price {v.clean} per 100 (model price)")]
        if v.accrued_c:
            lines.append(dr(aid, "1220", "USD", v.accrued_c, instrument_id=iid, memo=f"accrued interest purchased {v.accrued} per 100 (ACT/ACT)"))
        if v.fee_c:
            lines.append(dr(aid, "5000", "USD", v.fee_c, instrument_id=iid, memo=v.fee_basis))
        lines.append(cr(aid, "2100", "USD", total, memo="payable at settlement"))
        memo = (f"BUY {v.face:,} {inst.symbol} @ {v.clean} clean + {v.accrued} accrued (model price), fee {fmt_money(v.fee_c)}; "
                f"settles {v.settle_date.isoformat()} (COMPUTED, ACCRUED, COST_BASIS)")
        entry_id = ctx.post(Entry("FILL", memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
        _insert_fill(ctx, v, fill_id, "BUY", entry_id, idem_fill, settle=v.settle_date)
        lot_id = open_lot(ctx.tx, aid, iid, "LONG", fill_id, v.face, v.clean, v.clean_c, "USD")
        settle_id = _settlement(ctx, v, fill_id, -total, "2100")
        reliefs: List[dict] = []
        cost_relieved = realised = relieved_accr = income = 0
        new_face = v.position + v.face
        through = date.fromisoformat(st["accrued_through"]) if st is not None and st["accrued_through"] else v.trade_date
        cum_after = cum_before + v.accrued_c
        net_cash = -total
    else:
        conn.execute("PRAGMA defer_foreign_keys=ON")     # lot_relief rows precede their fill row inside this transaction
        rl = relieve_fifo(ctx.tx, aid, iid, "LONG", v.face, fill_id, v.clean_c)
        cost_relieved = sum(r.cost_relieved_c for r in rl)
        realised = sum(r.realised_c for r in rl)
        reliefs = [r.__dict__ for r in rl]
        relieved_accr = cum_before if v.face == v.position else to_c(Decimal(cum_before) / HUNDRED * Decimal(v.face) / Decimal(v.position))
        income = v.accrued_c - relieved_accr
        receivable = v.clean_c + v.accrued_c - v.fee_c
        lines = [dr(aid, "1200", "USD", receivable, memo="receivable at settlement (clean + accrued − fee)")]
        if v.fee_c:
            lines.append(dr(aid, "5000", "USD", v.fee_c, instrument_id=iid, memo=v.fee_basis))
        if cost_relieved:
            lines.append(cr(aid, "1110", "USD", cost_relieved, instrument_id=iid, memo="cost relieved FIFO"))
        if relieved_accr:
            lines.append(cr(aid, "1220", "USD", relieved_accr, instrument_id=iid, memo="accrued interest carried, relieved pro rata"))
        if income:
            lines.append(cr(aid, "4300", "USD", income, instrument_id=iid, memo="accrued received beyond the carried accrual (interest since the last night)"))
        if realised:
            lines.append(cr(aid, "4000", "USD", realised, instrument_id=iid, memo="realised (clean − cost; accrued is income)"))
        memo = (f"SELL {v.face:,} {inst.symbol} @ {v.clean} clean + {v.accrued} accrued (model price), fee {fmt_money(v.fee_c)}, FIFO cost {fmt_money(cost_relieved)},"
                f" realised {fmt_money(realised)}; settles {v.settle_date.isoformat()} (COMPUTED, ACCRUED, FIFO)")
        entry_id = ctx.post(Entry("FILL", memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
        _insert_fill(ctx, v, fill_id, "SELL", entry_id, idem_fill, settle=v.settle_date)
        lot_id = None
        settle_id = _settlement(ctx, v, fill_id, receivable, "1200")
        new_face = v.position - v.face
        through = date.fromisoformat(st["accrued_through"]) if st is not None and st["accrued_through"] else v.trade_date
        cum_after = cum_before - relieved_accr
        net_cash = receivable
    if new_face > 0:
        _write_state(conn, aid, iid, v.terms, new_face, through, cum_after)
    else:
        _delete_state(conn, aid, iid)
    ctx.touched_instruments.add((aid, iid))
    ctx.touched_accounts.add(aid)
    ctx.event("BOND_TRADE", account_id=aid, instrument_id=iid, ref_type="fill", ref_id=fill_id, side=v.side, face=v.face, clean=str(v.clean), accrued=str(v.accrued),
              clean_c=v.clean_c, accrued_c=v.accrued_c, fee_c=v.fee_c, realised_c=realised, settle_date=v.settle_date.isoformat(), entry_id=entry_id,
              funding=None if funding is None else funding.to_dict())
    new_qty, _ = position(conn, aid, iid)
    return {"fill": {"fill_id": fill_id, "entry_id": entry_id, "account_id": aid, "instrument_id": iid, "side": v.side, "qty": v.face, "face": v.face, "price": str(v.clean),
                     "price_basis": "model", "origin": "ORDER", "gross_c": v.clean_c, "accrued_c": v.accrued_c, "fee_c": v.fee_c, "fee_basis": v.fee_basis,
                     "trade_date": v.trade_date.isoformat(), "settle_date": v.settle_date.isoformat(), "idem_key": idem_fill, "memo": memo},
            "economics": {"clean": str(v.clean), "accrued": str(v.accrued), "dirty": str((v.clean + v.accrued).quantize(PX4)), "clean_c": v.clean_c, "accrued_c": v.accrued_c,
                          "fee_c": v.fee_c, "net_cash_c": net_cash, "net_cash": str(from_c(net_cash)), "cost_relieved_c": cost_relieved, "realised_c": realised,
                          "accrued_relieved_c": relieved_accr, "accrued_income_c": income},
            "settlement": {"settle_id": settle_id, "due_date": v.settle_date.isoformat(), "amount_c": net_cash, "gl": "2100" if v.side == "BUY" else "1200", "status": "PENDING"},
            "lot_id": lot_id, "reliefs": reliefs, "position_qty": new_qty, "funding": None if funding is None else funding.to_dict(),
            "accrual_state": None if new_face <= 0 else {"face": new_face, "cum_c": cum_after, "accrued_through": through.isoformat()},
            "mark": v.mark.to_dict() if hasattr(v.mark, "to_dict") else None, "note": "model price (COMPUTED): the computed clean price plus ACT/ACT accrued; no quote was observed",
            "terms": v.terms.to_dict()}


# ---- the lifecycle handlers and key helpers live in bonds_jobs.py (the ~900-line rule); they resolve lazily here ----------------------------------
_JOB_EXPORTS = ("h_accrue", "h_coupon", "h_mature", "h_list", "accrue_keys", "coupon_keys", "maturity_keys", "listing_keys")


def __getattr__(name: str):
    """``bonds.h_accrue`` / ``bonds.accrue_keys`` … keep their documented import path: the names are resolved from
    ``bonds_jobs`` on first access, which works in either import order (PEP 562)."""
    if name in _JOB_EXPORTS:
        from . import bonds_jobs
        return getattr(bonds_jobs, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register(bus, services=None) -> None:
    """Register ``bonds.trade`` (ui), ``bonds.accrue`` / ``bonds.coupon`` / ``bonds.mature`` (lifecycle) and the light
    ``bonds.list``; the runtime binds this module as ``services.bonds``."""
    from . import bonds_jobs as jobs
    bus.register("bonds.trade", h_trade)
    bus.register("bonds.accrue", jobs.h_accrue)
    bus.register("bonds.coupon", jobs.h_coupon)
    bus.register("bonds.mature", jobs.h_mature)
    bus.register("bonds.list", jobs.h_list, light=True)


__all__ = ["BondTerms", "bond_terms", "period_of", "accrued_per_100", "accrued_cents", "coupon_cents", "coupon_dates", "round_to_eighth", "benchmark_id",
           "benchmark_name", "through_night", "flag_on", "ensure_bond", "ensure_benchmarks", "expire_matured", "face_at", "state_row", "quote_view", "validate_trade",
           "h_trade", "h_accrue", "h_coupon", "h_mature", "h_list", "accrue_keys", "coupon_keys", "maturity_keys", "listing_keys", "register", "JOB_ACCRUE",
           "JOB_COUPON", "JOB_MATURITY", "JOB_LISTING", "SETTING_FLAG", "UNSUPPORTED_TEXT", "BENCHMARK_TENORS"]
