"""Repo and reverse repo against US Treasuries with the New York Fed primary dealers (Build 2 P08; TERMINAL_DESIGN.md §13.9,
§5.4 repo rows; contracts C4 / C13 / C20 / C27; owner 2026-10-21: "why isnt there counterparties for credit instruments?").

Conventions stated here (each is a sentence the screen shows or a rule the tests pin)
- WHAT: a REPO is cash BORROWED from a dealer against Treasuries the book holds in its Debt Instruments account (the face
  stays the book's position and is PLEDGED: a sale of pledged face is refused "release the repo first", C27
  ``pledged_face``); a REVERSE repo is cash LENT to a dealer against a Treasury the dealer delivers (the book holds it as
  collateral; it is not the book's position).  Cash, interest and the financing balance live in the Credit Instruments
  account.  US Treasury bills, notes and bonds only (TIPS and FRNs are refused: no nominal model price for them here).
  Dealers and their terms come from the counterparty directory (ASSUMED, editable terms; a primary-dealer name is never a
  quote from that dealer).  Opening, rolling and closing need the US bond session (08:00 - 17:00 ET).
- COLLATERAL VALUE: face x (clean mark + accrued) / 100 - the BOND mark of the command's snapshot (FedInvest, or the
  computed price with its inputs; a stale or missing mark refuses: nothing is invented) plus ACT/ACT accrued interest of
  the New York date.  CASH = to_c(collateral value x (1 - haircut)); haircuts are the dealer's ASSUMED table (bills 1 %,
  notes / bonds 2 %, TIPS 3 % by default).  A REPO needs settled face: face bought and not yet settled cannot be pledged.
- RATE: SOFR (the latest print within 10 days - FRED, or the NY Fed while FRED is in maintenance, labelled) + the dealer's
  repo spread when the book borrows cash, SOFR - its reverse-repo spread (floored at 0) when the book lends cash.
  Overnight (ON) and term repos (1W, 2W, 1M, 3M or a number of days) FIX the rate at booking and at each roll; an OPEN repo
  floats: each night uses that night's SOFR (RATE_LAG) + the spread.  Interest is ACT/360, simple, on the cash principal
  standing at each night's 17:00 ET close, posted nightly with cumulative rounding per segment (§11.3).
- POSTINGS (idem keys): open REPO ``Dr 1010 / Cr 2500`` (``repo:{id}:open``); open REVERSE ``Dr 1500 / Cr 1010`` after
  ``execution.fund`` (one Treasury sweep-in linked to the repo when the Credit account lacks the cash); night D interest
  REPO ``Dr 5500 / Cr 2500``, REVERSE ``Dr 1500 / Cr 4500`` (``accr:repo:{id}:{D}``); collateral mark (17:15 ET each US
  bond trading day, both ways: the cash principal is re-sized to collateral value x (1 - haircut), GMRA-style repricing,
  no minimum transfer amount in this model) REPO ``Dr 1010 / Cr 2500`` (excess back) or ``Dr 2500 / Cr 1010`` (margin
  call, funded by the sweep), REVERSE ``Dr 1500 / Cr 1010`` (the dealer's excess, funded) or ``Dr 1010 / Cr 1500``
  (``rmark:{id}:{date}``); roll pays the interest accrued (``repo:{id}:roll:{n}:interest``) and re-fixes the rate; close
  repays principal (``repo:{id}:close``) and the interest (``repo:{id}:close:interest``).  Before a mark, a roll or a
  close every night owed is posted first (never lumped, never skipped).
- JOBS (C13 ``add_product_job``): REPO_ACCRUE (from 00:30 ET, one key per repo and night), REPO_MATURITY (the maturity date
  from 15:00 ET: a term / overnight repo is repaid; if the Credit account and the Treasury cannot pay a REPO it rolls one
  business day with a WARN - no invented default), REPO_COLLATERAL_MARK (17:15 ET on US bond trading days; a day missed by
  downtime is not re-marked later with a later price).
"""
from __future__ import annotations

import json
import weakref
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from . import execution, tables
from .books import account_kind_for, account_row, lifetime_row, split_account_id
from .clock import accounting_day, epoch_of, ny_of
from .commands import ConflictError, RejectError, TxContext
from .db import dumps, next_id
from .ledger import Entry, balance, cr, dr, position
from .lifecycle_jobs import Job, accrual_state_row, accrue_segment, job_row, nights_between, write_job_done
from .units import fmt_money, from_c, qprice, to_c

SOURCE = "repo"
GROUP = "US_BONDS"
BASIS = 360
RATE_LAG_MAX_DAYS = 10
ACCRUE_FROM_ET = "00:30"
MATURITY_ET = "15:00"
MARK_ET = "17:15"
ACCRUAL_KIND = "repo"
SIDES = ("REPO", "REVERSE")
COLLATERAL_TYPES = ("BILL", "NOTE", "BOND")
SCHEMA_EXT = [
    "ALTER TABLE repos ADD COLUMN counterparty TEXT",
    "ALTER TABLE repos ADD COLUMN terms_json TEXT",
    "ALTER TABLE repos ADD COLUMN closed_ts INTEGER",
    "ALTER TABLE repos ADD COLUMN collateral_account TEXT",
    "ALTER TABLE repos ADD COLUMN last_mark_date TEXT",
    "CREATE INDEX IF NOT EXISTS ix_repos_collateral ON repos(collateral_account, instrument_id, status)",
]
_SCHEMA_DONE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
SIDE_TEXT = {"REPO": "repo (cash borrowed against your Treasuries)", "REVERSE": "reverse repo (cash lent against the dealer's Treasury)"}


# ---- schema / helpers -------------------------------------------------------------------------------------------------------------
def ensure_schema(db, conn=None) -> None:
    """C4: the repos columns this module adds (idempotent).  From ``bind`` (no ``conn``): once per Database object.  From a
    handler (``conn`` = its transaction, a runtime built without ``bind``): only when a column is missing, inside that
    transaction (a rollback takes the DDL with it, so nothing is cached then)."""
    if db is None:
        return
    fn = getattr(db, "ensure_schema_ext", None)
    if conn is not None:
        if not _has_col(conn, "last_mark_date") and callable(fn):
            fn("repo", SCHEMA_EXT)
        return
    if _SCHEMA_DONE.get(db):
        return
    if callable(fn):
        fn("repo", SCHEMA_EXT)
    _SCHEMA_DONE[db] = True


def _services(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _bonds(services):
    b = getattr(services, "bonds", None)
    if b is None or not hasattr(b, "bond_terms"):
        try:
            from .products import bonds as b
        except Exception as e:   # noqa: BLE001
            raise RejectError("NOT_AVAILABLE", "repo needs the bond engine (Debt Instruments), which is not loaded") from e
    return b


def _cps(services):
    c = getattr(services, "counterparties", None)
    if c is None or not hasattr(c, "repo_terms"):
        from . import counterparties as c
    return c


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _pct(fraction) -> str:
    q = (Decimal(str(fraction)) * 100).quantize(Decimal("0.0001"))
    s = f"{q:f}".rstrip("0")
    if s.endswith("."):
        s += "00"
    elif len(s.split(".")[1]) < 2:
        s += "0"
    return f"{s} %"


def _terms(row) -> dict:
    try:
        return json.loads(row["terms_json"] or "{}") if "terms_json" in row.keys() else {}
    except ValueError:
        return {}


def repo_row(conn, repo_id: str):
    return conn.execute("SELECT * FROM repos WHERE repo_id=?", (str(repo_id),)).fetchone()


def _require_row(conn, repo_id: str):
    r = repo_row(conn, repo_id)
    if r is None:
        raise RejectError("NOT_FOUND", f"repo {repo_id} does not exist")
    return r


def bond_type(inst) -> str:
    return str((inst.spec or {}).get("type") or "").upper()


def _has_col(conn, col: str) -> bool:
    return any(r[1] == col for r in conn.execute("PRAGMA table_info(repos)").fetchall())


# ---- C27: pledged face ---------------------------------------------------------------------------------------------------------------
def pledged_face(conn, account_id: str, instrument_id: str) -> int:
    """C27: face of ``instrument_id`` held in ``account_id`` that OPEN repos have pledged (P07's bond SELL refuses it)."""
    if not _has_col(conn, "collateral_account"):
        return 0
    row = conn.execute("SELECT COALESCE(SUM(face), 0) FROM repos WHERE side='REPO' AND status='OPEN' AND collateral_account=? AND instrument_id=?",
                       (account_id, instrument_id)).fetchone()
    return int(row[0] or 0)


def settled_face(conn, account_id: str, instrument_id: str) -> Tuple[int, int, Optional[str]]:
    """``(position face, face bought and not yet settled, its due date)`` of a bond in an account."""
    pos, _cost = position(conn, account_id, instrument_id)
    r = conn.execute("SELECT COALESCE(SUM(f.qty), 0) q, MAX(s.due_date) d FROM fills f JOIN settlements s ON s.fill_id=f.fill_id WHERE f.account_id=?"
                     " AND f.instrument_id=? AND f.side='BUY' AND s.status='PENDING'", (account_id, instrument_id)).fetchone()
    return int(pos), int(r["q"] or 0), r["d"]


# ---- valuation and rates -----------------------------------------------------------------------------------------------------------
@dataclass
class Collateral:
    instrument_id: str
    symbol: str
    name: str
    type: str
    face: int
    clean: Decimal
    accrued: Decimal
    dirty: Decimal
    value_c: int
    quality: str
    source: str
    as_of: Any
    note: str

    def to_dict(self) -> dict:
        return {"instrument_id": self.instrument_id, "symbol": self.symbol, "name": self.name, "type": self.type, "face": self.face, "clean": str(self.clean),
                "accrued": str(self.accrued), "dirty": str(self.dirty), "value_c": self.value_c, "quality": self.quality, "source": self.source, "as_of": self.as_of,
                "note": self.note}


def collateral(services, inst, face: int, snapshot, on: date, allow_stale: bool = False) -> Collateral:
    """The collateral value of ``face`` of a Treasury (module docstring COLLATERAL VALUE); RejectError when it cannot be valued.
    ``allow_stale`` (views only: the page and the exposure, never a booking or a margin movement) values a ``stale`` mark too,
    labelled with its quality; an unavailable mark is never valued."""
    B = _bonds(services)
    if inst is None or inst.kind != "BOND":
        raise RejectError("COLLATERAL_KIND", "repo collateral must be a US Treasury bill, note or bond")
    t = bond_type(inst)
    if t not in COLLATERAL_TYPES or inst.currency != "USD":
        why = {"TIPS": "TIPS are priced as a real price per 100 of indexed principal here (LIMITED)", "FRN": "FRNs have no model price here (LIMITED)"}.get(
            t, f"{inst.symbol} is not a US Treasury bill, note or bond")
        raise RejectError("COLLATERAL_KIND", f"{inst.symbol}: {why}; repo takes US Treasury bills, notes and bonds", {"type": t})
    view = snapshot.mark(inst.instrument_id) if snapshot is not None else None
    ok, why = B.executable_mark(view, on)
    if not ok and allow_stale and view is not None and view.mark is not None and view.quality == "stale":
        ok = True
    if not ok:
        raise RejectError("MARK_UNAVAILABLE", f"{inst.symbol}: {why} - the collateral cannot be valued, so nothing is booked (never an invented price)",
                          {"instrument_id": inst.instrument_id})
    terms = B.bond_terms(inst)
    if on >= terms.maturity:
        raise RejectError("COLLATERAL_MATURED", f"{inst.symbol} matures {terms.maturity.isoformat()}: it cannot be repo collateral")
    accrued = B.accrued_per_100(terms, on)
    clean = qprice(view.mark)
    dirty = qprice(clean + accrued)
    value_c = to_c(Decimal(int(face)) * dirty / Decimal(100))
    return Collateral(inst.instrument_id, inst.symbol, inst.name, t, int(face), clean, accrued, dirty, value_c, view.quality, view.source or "", view.quote_time, view.note or "")


def sofr(services, on: date) -> Tuple[date, Decimal, str]:
    """``(observation date, percent, source)``: the latest SOFR print on or before ``on`` within 10 days (RATE_LAG)."""
    rates = getattr(services, "rates", None)
    got = rates.as_of_le("SOFR", on) if rates is not None and hasattr(rates, "as_of_le") else None
    if got is None or (on - got[0]).days > RATE_LAG_MAX_DAYS:
        raise ConflictError("RATE_UNAVAILABLE", f"no SOFR print within {RATE_LAG_MAX_DAYS} days on or before {on.isoformat()}: the repo rate is unknown and never defaulted",
                            {"series": "SOFR", "on": on.isoformat()})
    src = rates.source("SOFR") if hasattr(rates, "source") else None
    return got[0], qprice(got[1]), src or "FRED"


def repo_rate(side: str, sofr_pct: Decimal, spread: Decimal) -> Decimal:
    """REPO: SOFR + spread; REVERSE: max(0, SOFR - spread) - decimal fractions."""
    base = Decimal(str(sofr_pct)) / Decimal(100)
    r = base + Decimal(str(spread)) if side == "REPO" else max(Decimal(0), base - Decimal(str(spread)))
    return qprice(r)


def term_spec(payload: dict) -> Tuple[str, Optional[int]]:
    """``(label, days)``: ON / 1W / 2W / 1M / 3M / OPEN or ``term_days`` (custom, 1 .. 365)."""
    from .counterparties import tenor_days
    t = str(payload.get("term") or "").upper().strip()
    if t:
        return t, tenor_days(t, "repo_terms") if t != "OPEN" else None
    days = payload.get("term_days")
    try:
        days = int(days)
    except (TypeError, ValueError) as e:
        raise RejectError("TERM", "term is required: ON, 1W, 2W, 1M, 3M, OPEN or term_days") from e
    if days < 1 or days > 365:
        raise RejectError("TERM", "term_days must be 1 .. 365")
    return f"{days}D", days


def maturity_for(cal, start: date, days: Optional[int]) -> str:
    """ON -> the next business day; N days -> start + N rolled following; OPEN -> '' (terminable any business day)."""
    if days is None:
        return ""
    if days == 1:
        return cal.next_business_day(start).isoformat()
    return cal.roll_following(start + timedelta(days=days)).isoformat()


# ---- ledger reads per repo ------------------------------------------------------------------------------------------------------------
def _gl_of(side: str) -> str:
    return "2500" if side == "REPO" else "1500"


def principal_at_close_c(conn, rid: str, side: str, d) -> int:
    """The cash principal of a repo standing at the 17:00 ET close of accounting day ``d``: its REPO-kind entries (open, marks,
    close) on 2500 / 1500 whose ``business_date`` <= d (natural sign)."""
    gl = _gl_of(side)
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.ref_type='repo' AND e.ref_id=?"
                       " AND e.kind='REPO' AND l.gl=? AND e.business_date<=?", (rid, gl, _d(d).isoformat())).fetchone()
    v = int(row[0] or 0)
    return -v if side == "REPO" else v


def accrued_c(conn, rid: str, side: str) -> int:
    """Interest accrued and not yet paid (natural sign, cents): the ACCRUAL entries minus the REPO_INTEREST payments."""
    gl = _gl_of(side)
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.ref_type='repo' AND e.ref_id=?"
                       " AND e.kind IN ('ACCRUAL', 'REPO_INTEREST') AND l.gl=?", (rid, gl)).fetchone()
    v = int(row[0] or 0)
    return -v if side == "REPO" else v


def interest_paid_c(conn, rid: str, side: str) -> int:
    gl = _gl_of(side)
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.ref_type='repo' AND e.ref_id=?"
                       " AND e.kind='REPO_INTEREST' AND l.gl=?", (rid, gl)).fetchone()
    v = int(row[0] or 0)
    return v if side == "REPO" else -v


def nights_due(conn, row, through: date) -> List[date]:
    """Calendar nights ``opened_date <= D <= through`` (and before the maturity / close day) not yet accrued."""
    first = _d(row["opened_date"])
    last = through
    ln = None
    if row["status"] != "OPEN" and "closed_ts" in row.keys() and row["closed_ts"]:
        ln = accounting_day(int(row["closed_ts"])) - timedelta(days=1)
    elif row["maturity_date"]:
        ln = _d(row["maturity_date"]) - timedelta(days=1)
    if ln is not None:
        last = min(last, ln)
    st = accrual_state_row(conn, ACCRUAL_KIND, row["repo_id"])
    start = first
    if st is not None and st["accrued_through"]:
        start = max(first, _d(st["accrued_through"]) + timedelta(days=1))
    return nights_between(start, last)


def night_rate(services, row, night: date) -> Tuple[Decimal, str]:
    """The rate of night D (module docstring RATE) and its memo label."""
    t = _terms(row)
    if t.get("rate_type") == "FLOATING":
        obs, pct, src = sofr(services, night)
        spread = Decimal(str(t.get("spread") or "0"))
        rate = repo_rate(row["side"], pct, spread)
        sign = "+" if row["side"] == "REPO" else "-"
        return rate, f"SOFR {pct} % ({obs.isoformat()}, {src}) {sign} {_pct(spread)} = {_pct(rate)} (open repo, floating)"
    rate = qprice(Decimal(str(row["rate"])))
    return rate, f"{_pct(rate)} fixed {t.get('fixed_on') or row['opened_date']} (SOFR {t.get('sofr_pct')} % of {t.get('sofr_as_of')} {'+' if row['side'] == 'REPO' else '-'} {_pct(t.get('spread') or 0)})"


def accrue_night(ctx: TxContext, services, row, night: date) -> dict:
    """Post night D of one repo (``accr:repo:{id}:{D}``); idempotent per night."""
    rid, side, aid = row["repo_id"], row["side"], row["account_id"]
    d = _d(night)
    idem = f"accr:repo:{rid}:{d.isoformat()}"
    got = ctx.conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key=?", (idem,)).fetchone()
    if got is not None:
        return {"repo_id": rid, "night": d.isoformat(), "amount_c": 0, "entry_id": int(got["entry_id"]), "skipped": True}
    if d.isoformat() >= ctx.today:
        raise RejectError("NIGHT_NOT_ENDED", f"the night of {d.isoformat()} has not ended (accounting day {ctx.today})")
    base = principal_at_close_c(ctx.conn, rid, side, d)
    rate, label = night_rate(services, row, d)
    amount = int(accrue_segment(ctx, ACCRUAL_KIND, rid, base, rate, BASIS, d))
    entry_id = None
    memo = f"repo interest {rid} ({SIDE_TEXT[side].split(' (')[0]}): {label}, ACT/360 on {fmt_money(base)}, night of {d.isoformat()} (ACCRUAL)"
    if amount > 0:
        lines = ([dr(aid, "5500", "USD", amount, memo=f"night of {d.isoformat()}"), cr(aid, "2500", "USD", amount, memo="interest accrued to the dealer")] if side == "REPO" else
                 [dr(aid, "1500", "USD", amount, memo="interest accrued from the dealer"), cr(aid, "4500", "USD", amount, memo=f"night of {d.isoformat()}")])
        entry_id = ctx.post(Entry("ACCRUAL", memo, lines, idem, ref_type="repo", ref_id=rid))
    ctx.touched_accounts.add(aid)
    ctx.event("ACCRUED", account_id=aid, ref_type="repo", ref_id=rid, kind=ACCRUAL_KIND, night=d.isoformat(), amount_c=amount, base_c=base, rate=str(rate), entry_id=entry_id)
    return {"repo_id": rid, "night": d.isoformat(), "amount_c": amount, "base_c": base, "rate": str(rate), "entry_id": entry_id, "memo": memo}


def catch_up(ctx: TxContext, services, row) -> List[dict]:
    """Every night owed before the command's accounting day, posted first (a missing SOFR refuses: 409 ACCRUAL_PENDING)."""
    through = _d(ctx.today) - timedelta(days=1)
    out = []
    for night in nights_due(ctx.conn, row, through):
        try:
            out.append(accrue_night(ctx, services, row, night))
        except RejectError as e:
            raise ConflictError("ACCRUAL_PENDING", f"repo {row['repo_id']} owes the night of {night.isoformat()} but its interest cannot be computed ({e.code}: {e.message})",
                                dict(e.details or {}, repo_id=row["repo_id"], night=night.isoformat())) from e
    return out


# ---- the handlers ---------------------------------------------------------------------------------------------------------------------
def _credit_account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    lrow = lifetime_row(conn, acct["lifetime_id"])
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {acct['lifetime_id']} is closed (History is read-only)")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    if account_kind_for(acct) != "CREDIT":
        raise RejectError("NOT_CREDIT_ACCOUNT", f"{account_id} is not the Credit Instruments account: repo and reverse repo are booked there")
    return acct


def _session_open(services, now: int) -> None:
    st = services.sessions.state(GROUP, now)
    if not st.open:
        nxt = ""
        if st.next_open:
            ny = ny_of(int(st.next_open))
            nxt = f" ({ny.strftime('%a %d-%b %H:%M')} ET)"
        raise ConflictError("SESSION_CLOSED", f"the US bond market is closed: repo dealing reopens 08:00 ET{nxt}", {"group": GROUP, "next_open": st.next_open})


def preview(services, payload: dict, conn=None, now: Optional[int] = None, snapshot=None) -> dict:
    """What ``repo.open`` would book (pure): collateral, haircut, cash, rate, the interest for the term, the warnings."""
    from .marks import shared_reader
    now_ts = int(now if now is not None else services.clock.epoch())
    on = ny_of(now_ts).date()
    snap = snapshot if snapshot is not None else services.marks.snapshot([str(payload.get("instrument_id") or "")])
    with shared_reader(services.db, conn) as c:
        ensure_cols = _has_col(c, "collateral_account")
        aid = str(payload.get("account_id") or "")
        acct = _credit_account(c, aid)
        side = str(payload.get("side") or "").upper()
        if side not in SIDES:
            raise RejectError("SIDE", "side must be REPO (borrow cash against your Treasuries) or REVERSE (lend cash against the dealer's Treasury)")
        dealer = _cps(services).repo_terms(services, str(payload.get("counterparty_id") or ""), c)
        iid = str(payload.get("instrument_id") or "").strip().upper()
        inst = services.instruments.get(iid, c) if iid else None
        if inst is None:
            raise RejectError("INSTRUMENT_UNKNOWN", f"{iid or 'the collateral'} is not a Treasury in the master (pick one from the list or type its CUSIP)")
        try:
            face = int(Decimal(str(payload.get("face"))))
        except Exception as e:   # noqa: BLE001
            raise RejectError("FACE", f"face must be a whole amount, got {payload.get('face')!r}") from e
        lot = int(inst.lot or 1000)
        if face <= 0 or face % lot:
            raise RejectError("FACE", f"face must be a positive multiple of {lot:,}")
        label, days = term_spec(payload)
        coll_acct = None
        held = pending = pledged = None
        if side == "REPO":
            lid, _role = split_account_id(aid)
            coll_acct = str(payload.get("collateral_account") or f"{lid}:DEBT")
            ca = account_row(c, coll_acct)
            if ca is None or ca["lifetime_id"] != lid or account_kind_for(ca) not in ("DEBT",):
                raise RejectError("COLLATERAL_ACCOUNT", f"{coll_acct} is not this book's Debt Instruments account: repo collateral is a Treasury held there")
            held, pending, due = settled_face(c, coll_acct, iid)
            pledged = pledged_face(c, coll_acct, iid) if ensure_cols else 0
            free = held - pending - pledged
            if face > free:
                # V104R1-05: a purchase still settling cannot be pledged; said without implying more is held than the position
                why = f"{ca['name'] or coll_acct} holds {held:,} face of {inst.symbol}"
                if pending:
                    why += (f"; purchases of {pending:,} face settle {due}" + (" - none of the position has settled yet" if pending >= held
                                                                                  else f" - {held - pending:,} of it has settled"))
                if pledged:
                    why += f"; {pledged:,} already pledged in open repos"
                raise ConflictError("COLLATERAL_SHORT", f"{why}: at most {max(free, 0):,} can be pledged now", {"held": held, "pending": pending, "pledged": pledged, "free": max(free, 0)})
        col = collateral(services, inst, face, snap, on)
        haircut = dealer["haircuts"].get(col.type)
        if haircut is None:
            raise RejectError("HAIRCUT", f"{dealer['label']} has no haircut for {col.type}")
        cash_c = to_c(from_c(col.value_c) * (Decimal(1) - haircut))
        if cash_c <= 0:
            raise RejectError("FACE", "the cash leg rounds to zero")
        obs, pct, src = sofr(services, on)
        spread = dealer["repo_spread"] if side == "REPO" else dealer["reverse_spread"]
        rate = repo_rate(side, pct, spread)
        start = accounting_day(now_ts)
        maturity = maturity_for(services.cal, start, days)
        nights = ((_d(maturity) - start).days if maturity else 1)
        interest_c = to_c(from_c(cash_c) * rate * Decimal(nights) / Decimal(BASIS))
        lid, _role = split_account_id(aid)
        used_c = 0
        if ensure_cols:
            for r in c.execute("SELECT r.principal_c FROM repos r JOIN accounts a ON a.account_id=r.account_id WHERE r.status='OPEN' AND r.counterparty=? AND a.lifetime_id=?",
                               (dealer["counterparty_id"], lid)).fetchall():
                used_c += int(r["principal_c"])
        cap_c = to_c(dealer["max_usd"]) if dealer["max_usd"] else None
        warnings = []
        if cap_c is not None and used_c + cash_c > cap_c:
            raise ConflictError("FACILITY_LIMIT", f"{dealer['label']}: the assumed maximum is USD {fmt_money(cap_c)}; open repos use {fmt_money(used_c)} and this one is"
                                f" {fmt_money(cash_c)}", {"max_usd_c": cap_c, "used_c": used_c, "cash_c": cash_c})
        if col.quality != "close":
            warnings.append(f"collateral valued at a {col.quality} price ({col.note or col.source})")
        sign = "+" if side == "REPO" else "-"
        return {"ok": True, "account_id": aid, "side": side, "side_text": SIDE_TEXT[side], "counterparty_id": dealer["counterparty_id"], "dealer": dealer["label"],
                "agreement": dealer.get("agreement"), "collateral": col.to_dict(), "collateral_account": coll_acct, "held_face": held, "pending_face": pending,
                "pledged_face": pledged, "haircut": str(haircut), "haircut_pct": _pct(haircut), "cash_c": cash_c, "term": label, "term_days": days,
                "start_date": start.isoformat(), "maturity_date": maturity or None, "nights": nights if maturity else None,
                "rate": str(rate), "rate_pct": _pct(rate), "rate_type": "FLOATING" if label == "OPEN" else "FIXED", "spread": str(spread),
                "sofr": {"value_pct": str(pct), "as_of": obs.isoformat(), "source": src}, "rate_text": f"SOFR {pct} % ({obs.isoformat()}) {sign} {_pct(spread)} = {_pct(rate)}",
                "interest_c": interest_c if maturity else None, "interest_note": (f"{nights} night(s) ACT/360 at {_pct(rate)}" if maturity else
                                                                                  f"one night {fmt_money(interest_c)}; an open repo floats with SOFR until closed"),
                "repay_c": cash_c + interest_c if maturity else None, "used_c": used_c, "max_usd_c": cap_c, "warnings": warnings,
                "conventions": "cash = collateral value x (1 - haircut); ACT/360 simple interest on the cash; marked daily both ways at 17:15 ET; dealer terms are ASSUMED"}


def h_open(ctx: TxContext, payload: dict) -> dict:
    """``repo.open {account_id, side, counterparty_id, instrument_id, face, term | term_days, collateral_account?}``."""
    services = _services(ctx)
    ensure_schema(getattr(services, "db", None), ctx.conn)
    _session_open(services, ctx.ts)
    pv = preview(services, payload, conn=ctx.conn, now=ctx.ts, snapshot=ctx.marks)
    conn = ctx.conn
    aid, side, cash_c = pv["account_id"], pv["side"], int(pv["cash_c"])
    rid = next_id(conn, "repo")
    col = pv["collateral"]
    funding = None
    what = (f"{SIDE_TEXT[side]} {rid} with {pv['dealer']}: {col['symbol']} face {int(col['face']):,} at {col['dirty']} dirty = {fmt_money(int(col['value_c']))},"
            f" haircut {pv['haircut_pct']} -> cash {fmt_money(cash_c)} at {pv['rate_text']}, {pv['term']}" + (f" to {pv['maturity_date']}" if pv["maturity_date"] else " (open)"))
    if side == "REPO":
        lines = [dr(aid, "1010", "USD", cash_c, memo=f"cash borrowed from {pv['dealer']}"), cr(aid, "2500", "USD", cash_c, memo=f"repo {rid} principal")]
    else:
        funding = execution.fund(ctx, aid, "USD", cash_c, "repo", rid, None, immediate=True, services=services)
        lines = [dr(aid, "1500", "USD", cash_c, memo=f"reverse repo {rid} principal"), cr(aid, "1010", "USD", cash_c, memo=f"cash lent to {pv['dealer']}")]
    entry_id = ctx.post(Entry("REPO", what, lines, f"repo:{rid}:open", ref_type="repo", ref_id=rid))
    terms = {"term": pv["term"], "term_days": pv["term_days"], "rate_type": pv["rate_type"], "spread": pv["spread"], "sofr_pct": pv["sofr"]["value_pct"],
             "sofr_as_of": pv["sofr"]["as_of"], "sofr_source": pv["sofr"]["source"], "fixed_on": ctx.today, "dealer": pv["dealer"], "agreement": pv.get("agreement"),
             "collateral_type": col["type"], "collateral_value_c": int(col["value_c"]), "clean": col["clean"], "accrued_per_100": col["accrued"], "rolls": 0,
             "opened_ts": ctx.ts}
    conn.execute("INSERT INTO repos(repo_id, command_id, account_id, side, instrument_id, face, price, haircut, principal_c, rate, opened_date, maturity_date, status,"
                 " counterparty, terms_json, closed_ts, collateral_account, last_mark_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,NULL,?,NULL)",
                 (rid, ctx.command_id, aid, side, col["instrument_id"], int(col["face"]), col["dirty"], pv["haircut"], cash_c, pv["rate"], ctx.today,
                  pv["maturity_date"] or "", pv["counterparty_id"], dumps(terms), pv.get("collateral_account")))
    ctx.touched_accounts.add(aid)
    if pv.get("collateral_account"):
        ctx.touched_instruments.add((pv["collateral_account"], col["instrument_id"]))
    ctx.event("REPO_OPENED", account_id=aid, instrument_id=col["instrument_id"], ref_type="repo", ref_id=rid, side=side, counterparty_id=pv["counterparty_id"],
              face=int(col["face"]), cash_c=cash_c, rate=pv["rate"], maturity_date=pv["maturity_date"], entry_id=entry_id,
              sweep_id=None if funding is None else funding.sweep_id)
    return {"repo_id": rid, "entry_id": entry_id, "preview": pv, "repo": repo_view(services, conn, repo_row(conn, rid), ctx.ts),
            "funding": None if funding is None else funding.to_dict(), "memo": what}


def _settle_interest(ctx: TxContext, services, row, suffix: str) -> Tuple[Optional[int], int]:
    """Pay the accrued interest: REPO ``Dr 2500 / Cr 1010`` (funded first), REVERSE ``Dr 1010 / Cr 1500`` (kind REPO_INTEREST)."""
    rid, side, aid = row["repo_id"], row["side"], row["account_id"]
    acc = accrued_c(ctx.conn, rid, side)
    if acc <= 0:
        return None, 0
    if side == "REPO":
        execution.fund(ctx, aid, "USD", acc, "repo", rid, None, immediate=True, services=services)
        lines = [dr(aid, "2500", "USD", acc, memo="repo interest paid"), cr(aid, "1010", "USD", acc, memo=f"interest to the dealer for {rid}")]
    else:
        lines = [dr(aid, "1010", "USD", acc, memo=f"interest from the dealer for {rid}"), cr(aid, "1500", "USD", acc, memo="reverse repo interest received")]
    eid = ctx.post(Entry("REPO_INTEREST", f"{'repo' if side == 'REPO' else 'reverse repo'} interest {rid}: {fmt_money(acc)} ({suffix})", lines,
                         f"repo:{rid}:{suffix}:interest", ref_type="repo", ref_id=rid))
    return eid, acc


def close_repo(ctx: TxContext, services, row, reason: str, idem_suffix: str = "close") -> dict:
    """Catch up every night owed, then repay / receive the principal and the interest; the repo CLOSES (the pledge ends)."""
    rid, side, aid = row["repo_id"], row["side"], row["account_id"]
    caught = catch_up(ctx, services, row)
    principal = principal_at_close_c(ctx.conn, rid, side, "9999-12-31")
    acc = accrued_c(ctx.conn, rid, side)
    funding = None
    if side == "REPO":
        funding = execution.fund(ctx, aid, "USD", principal + acc, "repo", rid, None, immediate=True, services=services)
        lines = [dr(aid, "2500", "USD", principal, memo=f"repo {rid} principal repaid"), cr(aid, "1010", "USD", principal, memo=f"cash back to {(_terms(row).get('dealer') or 'the dealer')}")]
    else:
        lines = [dr(aid, "1010", "USD", principal, memo=f"cash back from {(_terms(row).get('dealer') or 'the dealer')}"), cr(aid, "1500", "USD", principal, memo=f"reverse repo {rid} principal received")]
    eid = ctx.post(Entry("REPO", f"{'repo' if side == 'REPO' else 'reverse repo'} {rid} closed ({reason}): principal {fmt_money(principal)}", lines, f"repo:{rid}:{idem_suffix}",
                         ref_type="repo", ref_id=rid))
    ieid, paid = _settle_interest(ctx, services, row, idem_suffix)
    ctx.conn.execute("UPDATE repos SET status='CLOSED', closed_ts=?, principal_c=0 WHERE repo_id=?", (ctx.ts, rid))
    ctx.touched_accounts.add(aid)
    if row["collateral_account"] if "collateral_account" in row.keys() else None:
        ctx.touched_instruments.add((row["collateral_account"], row["instrument_id"]))
    ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE source=? AND ref_type='repo' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, SOURCE, rid))
    ctx.event("REPO_CLOSED", account_id=aid, instrument_id=row["instrument_id"], ref_type="repo", ref_id=rid, reason=reason, principal_c=principal, interest_c=paid,
              entry_id=eid, interest_entry_id=ieid, nights=[x["night"] for x in caught])
    return {"repo_id": rid, "closed": True, "reason": reason, "principal_c": principal, "interest_c": paid, "entry_id": eid, "interest_entry_id": ieid,
            "accrued_nights": [{"night": x["night"], "amount_c": x["amount_c"]} for x in caught], "funding": None if funding is None else funding.to_dict()}


def h_close(ctx: TxContext, payload: dict) -> dict:
    """``repo.close {repo_id}`` (the owner terminates a repo early, or closes an open repo)."""
    services = _services(ctx)
    row = _require_row(ctx.conn, str(payload.get("repo_id") or ""))
    if row["status"] != "OPEN":
        raise ConflictError("REPO_CLOSED", f"repo {row['repo_id']} is already closed")
    _credit_account(ctx.conn, row["account_id"])
    _session_open(services, ctx.ts)
    out = close_repo(ctx, services, row, "closed by you")
    out["repo"] = repo_view(services, ctx.conn, repo_row(ctx.conn, row["repo_id"]), ctx.ts)
    return out


def h_roll(ctx: TxContext, payload: dict) -> dict:
    """``repo.roll {repo_id, term?}``: catch up, pay the accrued interest, re-fix the rate at today's SOFR + the dealer spread and
    extend to a new maturity (default: the same term from today)."""
    services = _services(ctx)
    conn = ctx.conn
    row = _require_row(conn, str(payload.get("repo_id") or ""))
    if row["status"] != "OPEN":
        raise ConflictError("REPO_CLOSED", f"repo {row['repo_id']} is closed")
    _credit_account(conn, row["account_id"])
    _session_open(services, ctx.ts)
    t = _terms(row)
    if t.get("rate_type") == "FLOATING":
        raise RejectError("OPEN_REPO", f"repo {row['repo_id']} is open (it floats daily and has no maturity): close it when you are done")
    if payload.get("term") not in (None, "") or payload.get("term_days") not in (None, ""):
        label, days = term_spec(payload)
    elif t.get("term") and not str(t.get("term")).endswith("D"):
        label, days = term_spec({"term": t.get("term")})
    else:
        label, days = term_spec({"term_days": t.get("term_days")})
    if days is None:
        raise RejectError("TERM", "roll into a term (ON, 1W, 2W, 1M, 3M or days); to keep it open-ended close it and book an OPEN repo")
    caught = catch_up(ctx, services, row)
    n = int(t.get("rolls") or 0) + 1
    ieid, paid = _settle_interest(ctx, services, row, f"roll:{n}")
    on = ny_of(ctx.ts).date()
    obs, pct, src = sofr(services, on)
    dealer = _cps(services).repo_terms(services, row["counterparty"], conn)
    spread = dealer["repo_spread"] if row["side"] == "REPO" else dealer["reverse_spread"]
    rate = repo_rate(row["side"], pct, spread)
    start = _d(ctx.today)
    maturity = maturity_for(ctx.cal, start, days)
    t.update(term=label, term_days=days, spread=str(spread), sofr_pct=str(pct), sofr_as_of=obs.isoformat(), sofr_source=src, fixed_on=ctx.today, rolls=n)
    conn.execute("UPDATE repos SET rate=?, maturity_date=?, terms_json=? WHERE repo_id=?", (str(rate), maturity, dumps(t), row["repo_id"]))
    ctx.event("REPO_ROLLED", account_id=row["account_id"], ref_type="repo", ref_id=row["repo_id"], rate=str(rate), maturity_date=maturity, interest_c=paid, interest_entry_id=ieid,
              nights=[x["night"] for x in caught])
    return {"repo_id": row["repo_id"], "rolled": True, "interest_paid_c": paid, "interest_entry_id": ieid, "rate": str(rate), "rate_pct": _pct(rate), "maturity_date": maturity,
            "accrued_nights": [{"night": x["night"], "amount_c": x["amount_c"]} for x in caught], "repo": repo_view(services, conn, repo_row(conn, row["repo_id"]), ctx.ts)}


def mark_repo(ctx: TxContext, services, row, mark_date: str) -> dict:
    """The daily collateral mark (module docstring POSTINGS): re-size the cash to collateral value x (1 - haircut)."""
    rid, side, aid = row["repo_id"], row["side"], row["account_id"]
    if row["status"] != "OPEN":
        return {"repo_id": rid, "skipped": "closed", "delta_c": 0}
    if (row["last_mark_date"] or "") >= mark_date:
        return {"repo_id": rid, "skipped": f"already marked for {mark_date}", "delta_c": 0}
    catch_up(ctx, services, row)
    inst = services.instruments.get(row["instrument_id"], ctx.conn)
    on = _d(mark_date)
    col = collateral(services, inst, int(row["face"]), ctx.marks, on)
    haircut = Decimal(str(row["haircut"]))
    required = to_c(from_c(col.value_c) * (Decimal(1) - haircut))
    principal = principal_at_close_c(ctx.conn, rid, side, "9999-12-31")
    delta = required - principal
    t = _terms(row)
    entry_id = None
    funding = None
    if delta != 0:
        if side == "REPO":
            if delta > 0:
                what, lines = "excess collateral: the dealer pays cash back", [dr(aid, "1010", "USD", delta, memo=f"repo {rid} excess"), cr(aid, "2500", "USD", delta, memo="principal up")]
            else:
                funding = execution.fund(ctx, aid, "USD", -delta, "repo", rid, None, immediate=True, overdraft=True, services=services)
                what, lines = "margin call: cash paid to the dealer", [dr(aid, "2500", "USD", -delta, memo="principal down"), cr(aid, "1010", "USD", -delta, memo=f"repo {rid} margin")]
        else:
            if delta > 0:
                funding = execution.fund(ctx, aid, "USD", delta, "repo", rid, None, immediate=True, overdraft=True, services=services)
                what, lines = "the dealer calls its excess: more cash lent", [dr(aid, "1500", "USD", delta, memo="principal up"), cr(aid, "1010", "USD", delta, memo=f"reverse repo {rid} margin")]
            else:
                what, lines = "margin call on the dealer: cash returned", [dr(aid, "1010", "USD", -delta, memo=f"reverse repo {rid} margin"), cr(aid, "1500", "USD", -delta, memo="principal down")]
        memo = (f"collateral mark {mark_date} on {rid}: {col.symbol} {int(row['face']):,} face at {col.dirty} dirty = {fmt_money(col.value_c)} ({col.quality}, {col.source});"
                f" cash {fmt_money(principal)} -> {fmt_money(required)} at haircut {_pct(haircut)}: {what} {fmt_money(abs(delta))}")
        entry_id = ctx.post(Entry("REPO", memo, lines, f"rmark:{rid}:{mark_date}", ref_type="repo", ref_id=rid))
    t.update(collateral_value_c=col.value_c, clean=str(col.clean), accrued_per_100=str(col.accrued), last_mark={"date": mark_date, "delta_c": delta, "required_c": required})
    ctx.conn.execute("UPDATE repos SET principal_c=?, last_mark_date=?, terms_json=? WHERE repo_id=?", (required, mark_date, dumps(t), rid))
    ctx.touched_accounts.add(aid)
    ctx.event("REPO_MARKED", account_id=aid, instrument_id=row["instrument_id"], ref_type="repo", ref_id=rid, mark_date=mark_date, collateral_value_c=col.value_c,
              required_c=required, delta_c=delta, entry_id=entry_id)
    return {"repo_id": rid, "mark_date": mark_date, "collateral_value_c": col.value_c, "required_c": required, "principal_before_c": principal, "delta_c": delta,
            "entry_id": entry_id, "funding": None if funding is None else funding.to_dict()}


def h_mark(ctx: TxContext, payload: dict) -> dict:
    """``repo.mark {repo_id, mark_date}`` (the REPO_COLLATERAL_MARK unit; the owner may run it from the page too)."""
    services = _services(ctx)
    row = _require_row(ctx.conn, str(payload.get("repo_id") or ""))
    md = str(payload.get("mark_date") or ny_of(ctx.ts).date().isoformat())
    return mark_repo(ctx, services, row, md)


# ---- jobs (C13) ---------------------------------------------------------------------------------------------------------------------------
class _RepoJob(Job):
    """The repo jobs live in the process-wide REGISTRY once any runtime bound this module (C13); in a runtime without
    ``services.repo`` they are quietly idle (no keys) instead of a 'module not bound' note."""
    module = "repo"

    def available(self, services):
        return True, ""

    def _bound(self) -> bool:
        return getattr(self.services, "repo", None) is not None


def _open_rows(conn) -> list:
    if not _has_col(conn, "collateral_account"):
        return []
    return conn.execute("SELECT r.* FROM repos r JOIN accounts a ON a.account_id=r.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                        " WHERE r.status='OPEN' AND a.status='OPEN' AND l.status='OPEN' ORDER BY r.repo_id").fetchall()


class RepoAccrue(_RepoJob):
    name = "REPO_ACCRUE"
    kind = "lifecycle.repo_accrue"
    when = "from 00:30 ET for every calendar night a repo was open at 17:00 ET"
    module = "repo"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        if not self._bound():
            return []
        today = clock.today()
        through = today - timedelta(days=1) if clock.epoch() >= epoch_of(today, ACCRUE_FROM_ET) else today - timedelta(days=2)
        keys = []
        for r in _open_rows(conn):
            for d in nights_due(conn, r, through):
                keys.append(f"{r['repo_id']}:{d.isoformat()}")
        return self.filter_done(conn, keys)

    def series_of(self, key: str) -> Optional[str]:
        return key.rsplit(":", 1)[0]

    def run(self, ctx: TxContext, key: str, services) -> dict:
        rid, d = key.rsplit(":", 1)
        row = _require_row(ctx.conn, rid)
        if row["status"] != "OPEN":
            return {"status": "DONE", "note": f"{rid} is closed: its nights were posted at the close", "posted": False}
        out = accrue_night(ctx, services, row, _d(d))
        return dict(out, status="DONE", posted_c=int(out.get("amount_c") or 0), note=f"{fmt_money(int(out.get('amount_c') or 0))} night of {d}")


class RepoMaturity(_RepoJob):
    name = "REPO_MATURITY"
    kind = "lifecycle.repo_maturity"
    when = "the maturity date from 15:00 ET: term and overnight repos are repaid (a REPO the book cannot pay rolls one business day with a warning)"
    module = "repo"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        if not self._bound():
            return []
        today = clock.today()
        now = clock.epoch()
        keys = []
        for r in _open_rows(conn):
            if not r["maturity_date"]:
                continue
            m = _d(r["maturity_date"])
            if m < today or (m == today and now >= epoch_of(m, MATURITY_ET)):
                keys.append(f"{r['repo_id']}:{m.isoformat()}")
        return self.filter_done(conn, keys)

    def run(self, ctx: TxContext, key: str, services) -> dict:
        rid, d = key.rsplit(":", 1)
        row = _require_row(ctx.conn, rid)
        if row["status"] != "OPEN" or row["maturity_date"] != d:
            return {"status": "DONE", "note": f"{rid} is no longer due {d} (closed or rolled)", "posted": False}
        try:
            out = close_repo(ctx, services, row, f"matured {d}")
            return dict(out, status="DONE", posted=True, note=f"repaid principal {fmt_money(out['principal_c'])} + interest {fmt_money(out['interest_c'])}")
        except ConflictError as e:
            if e.code != "INSUFFICIENT_FUNDS" or row["side"] != "REPO":
                raise
        new = ctx.cal.next_business_day(_d(d)).isoformat()
        ctx.conn.execute("UPDATE repos SET maturity_date=? WHERE repo_id=?", (new, rid))
        ctx.attention("WARN", SOURCE, "REPO_ROLLED", f"repo {rid} matured {d} but the Credit account and the Treasury cannot repay it: it rolls to {new} at its rate;"
                      " add funds or close other positions (no default is invented)", {"account_id": row["account_id"], "ref_type": "repo", "ref_id": rid, "auto_resolve": 1})
        return {"status": "DONE", "note": f"rolled to {new}: cash short", "posted": False, "rolled_to": new}


class RepoCollateralMark(_RepoJob):
    name = "REPO_COLLATERAL_MARK"
    kind = "lifecycle.repo_mark"
    when = "17:15 ET on US bond trading days: each repo's cash re-sized to its collateral value x (1 - haircut), both ways"
    module = "repo"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        if not self._bound():
            return []
        today = clock.today()
        if not sessions.is_trading_day(GROUP, today) or clock.epoch() < epoch_of(today, MARK_ET):
            return []
        keys = []
        for r in _open_rows(conn):
            if _d(r["opened_date"]) > today or (r["last_mark_date"] or "") >= today.isoformat():
                continue
            keys.append(f"{r['repo_id']}:{today.isoformat()}")
        return self.filter_done(conn, keys)

    def run(self, ctx: TxContext, key: str, services) -> dict:
        rid, d = key.rsplit(":", 1)
        row = _require_row(ctx.conn, rid)
        out = mark_repo(ctx, services, row, d)
        if out.get("skipped"):
            return dict(out, status="DONE", note=str(out["skipped"]), posted=False)
        return dict(out, status="DONE", posted=bool(out.get("delta_c")), note=f"required {fmt_money(out['required_c'])}, movement {fmt_money(out['delta_c'])}")


JOBS = (RepoAccrue(), RepoMaturity(), RepoCollateralMark())


# ---- views --------------------------------------------------------------------------------------------------------------------------------
def repo_view(services, conn, row, now: Optional[int] = None) -> dict:
    """One repo for the page: terms, cash, interest posted and owed now, the payoff, the collateral now (with the margin it
    implies) - every figure from the ledger or the mark, nothing invented."""
    from .lending_accruals import segment_amount
    now_ts = int(now if now is not None else services.clock.epoch())
    rid, side = row["repo_id"], row["side"]
    t = _terms(row)
    principal = principal_at_close_c(conn, rid, side, "9999-12-31") if row["status"] == "OPEN" else 0
    posted = accrued_c(conn, rid, side)
    pending, problem = [], None
    if row["status"] == "OPEN":
        today = accounting_day(now_ts)
        st = accrual_state_row(conn, ACCRUAL_KIND, rid)
        srow = None if st is None else dict(st)
        for night in nights_due(conn, row, today - timedelta(days=1)):
            try:
                rate, _label = night_rate(services, row, night)
            except RejectError as e:
                problem = f"night of {night.isoformat()}: {e.message}"
                break
            base = principal_at_close_c(conn, rid, side, night)
            amt, start, cum = segment_amount(srow, base, rate, BASIS, night.isoformat())
            srow = {"segment_start": start, "base_c": base, "rate": str(qprice(rate)), "day_basis": BASIS, "accrued_through": night.isoformat(), "cum_c": cum}
            pending.append({"night": night.isoformat(), "amount_c": int(amt)})
    pend_c = sum(p["amount_c"] for p in pending)
    col_now = None
    margin = None
    inst = services.instruments.get(row["instrument_id"], conn) if getattr(services, "instruments", None) is not None else None
    if row["status"] == "OPEN" and inst is not None:
        try:
            snap = services.marks.snapshot([row["instrument_id"]])
            col = collateral(services, inst, int(row["face"]), snap, ny_of(now_ts).date(), allow_stale=True)
            col_now = col.to_dict()
            req = to_c(from_c(col.value_c) * (Decimal(1) - Decimal(str(row["haircut"]))))
            margin = {"required_c": req, "delta_c": req - principal, "text": ("excess due to you" if (req - principal) > 0 else "margin due from you") if side == "REPO" else
                      ("the dealer may call more cash" if (req - principal) > 0 else "margin due from the dealer")}
        except RejectError as e:
            col_now = {"error": e.message}
    rate_pct = _pct(Decimal(str(row["rate"])))
    out = {"repo_id": rid, "account_id": row["account_id"], "side": side, "side_text": SIDE_TEXT.get(side, side), "counterparty_id": row["counterparty"] if "counterparty" in row.keys() else None,
           "dealer": t.get("dealer") or row["counterparty"], "agreement": t.get("agreement"), "instrument_id": row["instrument_id"], "symbol": inst.symbol if inst else row["instrument_id"],
           "name": inst.name if inst else "", "face": int(row["face"]), "collateral_account": row["collateral_account"] if "collateral_account" in row.keys() else None,
           "haircut": str(row["haircut"]), "haircut_pct": _pct(Decimal(str(row["haircut"]))), "opening_dirty": str(row["price"]), "principal_c": principal,
           "rate": str(row["rate"]), "rate_pct": rate_pct if t.get("rate_type") != "FLOATING" else f"SOFR {'+' if side == 'REPO' else '-'} {_pct(t.get('spread') or 0)} (floating)",
           "rate_type": t.get("rate_type"), "term": t.get("term"), "spread": t.get("spread"), "sofr": {"value_pct": t.get("sofr_pct"), "as_of": t.get("sofr_as_of"), "source": t.get("sofr_source")},
           "opened_date": row["opened_date"], "maturity_date": row["maturity_date"] or None, "status": row["status"], "closed_ts": row["closed_ts"] if "closed_ts" in row.keys() else None,
           "interest_posted_c": posted, "pending_nights": pending, "interest_owed_c": posted + pend_c, "interest_paid_c": interest_paid_c(conn, rid, side),
           "payoff_c": None if problem else (principal + posted + pend_c if row["status"] == "OPEN" else 0), "payoff_note": problem or "principal + interest to date",
           "collateral_now": col_now, "margin_now": margin, "last_mark_date": row["last_mark_date"] if "last_mark_date" in row.keys() else None,
           "last_mark": t.get("last_mark"), "rolls": int(t.get("rolls") or 0), "assumed": True}
    return out


def repos_view(services, lifetime_id: Optional[str] = None, account_id: Optional[str] = None, status: str = "OPEN") -> dict:
    from .marks import shared_reader
    now = int(services.clock.epoch())
    with shared_reader(services.db) as conn:
        if not _has_col(conn, "collateral_account"):
            return {"rows": [], "collateral": [], "as_of": now}
        sql, params = "SELECT r.* FROM repos r JOIN accounts a ON a.account_id=r.account_id WHERE 1=1", []
        if lifetime_id:
            sql += " AND a.lifetime_id=?"
            params.append(lifetime_id)
        if account_id:
            sql += " AND r.account_id=?"
            params.append(account_id)
        if status and status.upper() != "ALL":
            sql += " AND r.status=?"
            params.append(status.upper())
        rows = [repo_view(services, conn, r, now) for r in conn.execute(sql + " ORDER BY r.status DESC, r.repo_id DESC LIMIT 200", params).fetchall()]
        cand = collateral_candidates(services, conn, lifetime_id) if lifetime_id else []
    sess = services.sessions.state(GROUP, now)
    return {"rows": rows, "collateral": cand, "as_of": now, "session": {"open": sess.open, "next_open": sess.next_open, "phase": sess.phase},
            "note": "repo: cash borrowed against your Treasuries (the face is pledged); reverse repo: cash lent against the dealer's Treasury. Dealer terms are ASSUMED."}


def collateral_candidates(services, conn, lifetime_id: str) -> List[dict]:
    """The Treasuries held in the book's Debt Instruments account with the face free to pledge (settled, not yet pledged)."""
    debt = f"{lifetime_id}:DEBT"
    out = []
    rows = conn.execute("SELECT v.instrument_id, v.qty FROM v_positions v WHERE v.account_id=? AND v.qty > 0 ORDER BY v.instrument_id", (debt,)).fetchall()
    for r in rows:
        inst = services.instruments.get(r["instrument_id"], conn)
        if inst is None or inst.kind != "BOND":
            continue
        held, pending, due = settled_face(conn, debt, r["instrument_id"])
        pledged = pledged_face(conn, debt, r["instrument_id"])
        out.append({"instrument_id": inst.instrument_id, "symbol": inst.symbol, "name": inst.name, "type": bond_type(inst), "account_id": debt, "held": held,
                    "pending": pending, "pending_due": due, "pledged": pledged, "free": max(0, held - pending - pledged),
                    "eligible": bond_type(inst) in COLLATERAL_TYPES and inst.currency == "USD"})
    return out


def exposure_items(services, conn, account_ids: List[str], snap) -> List[dict]:
    """C20 rows: REPO (the dealer holds the book's bonds at market vs the cash it lent) and REVERSE (cash lent + interest vs the
    dealer's bonds at market)."""
    if not account_ids or not _has_col(conn, "collateral_account"):
        return []
    q = ",".join("?" for _ in account_ids)
    out = []
    today = ny_of(int(services.clock.epoch())).date()
    rows = conn.execute(f"SELECT * FROM repos WHERE status='OPEN' AND account_id IN ({q}) ORDER BY repo_id", account_ids).fetchall()
    if rows and getattr(services, "marks", None) is not None and hasattr(services.marks, "snapshot"):
        snap = services.marks.snapshot(sorted({r["instrument_id"] for r in rows}))
    for r in rows:
        t = _terms(r)
        principal = principal_at_close_c(conn, r["repo_id"], r["side"], "9999-12-31")
        acc = accrued_c(conn, r["repo_id"], r["side"])
        inst = services.instruments.get(r["instrument_id"], conn)
        try:
            col_c = collateral(services, inst, int(r["face"]), snap, today, allow_stale=True).value_c
        except RejectError:
            col_c = None
        what = f"{r['repo_id']} {inst.symbol if inst else r['instrument_id']} {int(r['face']):,} face"
        if r["side"] == "REPO":
            out.append({"counterparty_id": r["counterparty"], "name": t.get("dealer") or r["counterparty"], "product": "repo", "exposure_c": col_c,
                        "collateral_c": principal + acc, "what": what})
        else:
            out.append({"counterparty_id": r["counterparty"], "name": t.get("dealer") or r["counterparty"], "product": "reverse repo", "exposure_c": principal + acc,
                        "collateral_c": col_c, "what": what})
    return out


# ---- wiring --------------------------------------------------------------------------------------------------------------------------------
def unsupported() -> List[dict]:
    return [{"item": "Repo on corporate bonds, TIPS, FRNs or equities; tri-party and cleared (FICC) repo", "state": "NOT_OFFERED",
             "wording": "bilateral repo against US Treasury bills, notes and bonds only", "where": "Credit Instruments > Repo"},
            {"item": "Repo margin thresholds and minimum transfer amounts", "state": "SIMPLIFIED",
             "wording": "the cash is re-sized to the collateral value every US bond trading day at 17:15 ET, with no threshold", "where": "Credit Instruments > Repo"}]


def register(bus, services=None) -> None:
    bus.register("repo.open", h_open)
    bus.register("repo.close", h_close)
    bus.register("repo.roll", h_roll)
    bus.register("repo.mark", h_mark)


def bind(rt) -> None:
    """C4 columns, then the three lifecycle jobs through C13 ``add_product_job`` (before SNAPSHOT)."""
    ensure_schema(getattr(rt, "db", None))
    try:
        from .lifecycle_eod import add_product_job
    except ImportError:                                     # pragma: no cover - a build without the C13 hook: no jobs (said in health)
        return
    for job in JOBS:
        add_product_job(job, before="SNAPSHOT")


def register_routes(r) -> None:
    from .api import ApiError, NotFound, ok_or_raise, run_command

    def repos_get(req):
        aid = req.q("account_id")
        lid = req.lifetime() if not aid else split_account_id(aid)[0]
        return repos_view(req.rt, lifetime_id=lid, account_id=aid, status=req.q("status") or "OPEN")

    def repo_preview(req):
        body = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        try:
            return preview(req.rt, body)
        except RejectError as e:
            raise ApiError(int(getattr(e, "http", 400) or 400), e.code, e.message, e.details)

    def repo_post(req):
        res = run_command(req, "repo.open", {k: v for k, v in (req.body or {}).items() if k != "request_id"})
        return res

    def repo_action(req):
        rid, action = req.params[0], req.params[1]
        payload = {"repo_id": rid}
        if action == "roll":
            for k in ("term", "term_days"):
                if req.b(k) not in (None, ""):
                    payload[k] = req.b(k)
        return run_command(req, {"close": "repo.close", "roll": "repo.roll", "mark": "repo.mark"}[action], payload)

    r.add("GET", r"/api/credit/repos", repos_get)
    r.add("POST", r"/api/credit/repos/preview", repo_preview, mutating=False)
    r.add("POST", r"/api/credit/repos", repo_post)
    r.add("POST", r"/api/credit/repos/([^/]+)/(close|roll|mark)", repo_action)


__all__ = ["pledged_face", "settled_face", "collateral", "sofr", "repo_rate", "term_spec", "maturity_for", "principal_at_close_c", "accrued_c", "nights_due",
           "accrue_night", "catch_up", "preview", "h_open", "h_close", "h_roll", "h_mark", "close_repo", "mark_repo", "repo_view", "repos_view",
           "collateral_candidates", "exposure_items", "register", "bind", "register_routes", "JOBS", "RepoAccrue", "RepoMaturity", "RepoCollateralMark", "unsupported"]
