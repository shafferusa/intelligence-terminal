"""Private Markets: the sourced company master, last-round marks, negotiated secondary trades with lock-ups, the QP gate
(TERMINAL_DESIGN.md §2.12 products/private.py, §5.4, §7.2, §8.6, §13.13, §19.17; TERMINAL_DECISIONS.md owner amendment
17:48; TERMINAL_WORKPLAN.md WP-19).

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- The master (``terminal/data/private_companies.json``) was researched at build time with web search and every
  ``post_money_usd`` / ``price_per_share`` carries a ``source_url`` and an ``as_of`` or is null — nothing is estimated
  (``test_terminal_private.py`` guards the file).  ``InstrumentMaster._seed_private`` seeds it once (INSERT OR IGNORE)
  into ``private_companies`` / ``private_rounds`` (``recorded_by = seed``) / ``instruments`` (kind PRIVATE, support
  COMPUTED, session group PRIVATE_MARKETS); ``reseed()`` adds new file rows later and never overwrites owner rounds.
- PRIVATE_MARK: ``private_mark()`` is the LATEST reported round's ``price_per_share`` (latest by ``round_date``, then by
  the order recorded), quality ``private``, method ``private_round``, note "last round <date> (<round_type>)", never
  executable and never aged into stale (``quotes.freshness`` treats ``private_round`` as ``private``).  When the latest
  round has only a valuation the mark is UNAVAILABLE with the note "valuation only: no price per share disclosed" — a
  valuation × owned-fraction mark would need a share count the sources rarely give, so it is left blank, never guessed.
  A company set PUBLIC with a ticker takes the LISTED instrument's mark (through ``services.marks.snapshot()``) from its
  listing date (``spec.listed_on``, else the IPO round's date), labelled "listed <date> (<ticker>)"; the PRIVATE lots
  stay and are sold "secondary" at that mark until Build 2 (disclosed in the note).
- PRIVATE_TRADE: ``private.trade`` executes at the price the owner enters — there are no counterparties, fills or prices
  invented.  ``gross = qty × price``; ``fee = to_c(qty × price × fee_pct)`` (default ``settings.private_defaults.fee_pct``
  3 % for SECONDARY, 0 for ROUND, editable); BUY needs ``gross + fee`` funded at the fill (own available at the settle
  date, else one Treasury SWEEP_IN of exactly the shortfall; else 409 INSUFFICIENT_FUNDS); the fill has origin
  NEGOTIATED and price basis ``negotiated`` (``execution.apply_system_fill``), posts ``Dr 1120 gross / Dr 5000 fee /
  Cr 2100 gross + fee`` with a settlement row due ``trade_date + settle_days`` CALENDAR days (SETTLE_DUE pays it, the
  preview says "pending until <date>") and opens a LONG lot with ``lock_until = trade_date + lockup_months`` calendar
  months.  SELL relieves FIFO lots: ``Dr 1200 gross − fee / Dr 5000 fee / Cr 1120 cost relieved / Cr(Dr) 4000 realised``;
  a sale inside a lot's lock-up carries the WARNING ``LOCKUP`` ("lock-up until <date>; transfer restrictions apply (Rule
  144)") — never a block.  Sides SELL_SHORT / BUY_TO_COVER (and STO / BTC) are NOT_TRADEABLE_HERE; a missing price is
  PRICE_REQUIRED; shares trade only in the lifetime's PRIVATE account (NOT_TRADEABLE_HERE elsewhere).
- QP: every validation starts with ``books.require_private(lifetime)`` → 400 NOT_ELIGIBLE_QP with the plain-English
  reason (a Personal book below the threshold, which has no PRIVATE account, gets this code, not ACCOUNT_UNKNOWN).
- Owner records: ``private.record_round`` (light) needs ``round_date``, ``round_type``, at least one of ``post_money`` /
  ``price_per_share`` and a non-empty ``source_url`` (else 400 SOURCE_REQUIRED); ``private.add_company`` (light) needs a
  name and, for a PUBLIC status, a ticker; an optional ``last_round`` follows the same rules.  ``private.set_status``
  (light, an addition to the §2.12 list) switches a company to FILED_IPO / PUBLIC (+ ticker, listing date).  Rows carry
  ``recorded_by = owner`` and the command id; the seed rows are never touched.
- Money: integer cents and Decimal only; HALF_EVEN through ``units.to_c``.  Dates are ISO strings; the trade date is the
  New York calendar date (PRIVATE_MARKETS has no session and no futures-style evening roll).
- LISTED (owner 2026-10-21: "spacex is in private markets for some reason"): a company whose status is PUBLIC with a ticker
  is LISTED from its listing date (``spec.listed_on``, else the master file's ``listed_on``, else the IPO round's date); a
  company the master file records as ``acquired`` (a completed acquisition by a listed company, with its source) is
  ACQUIRED from the effective date.  From that date it is not a private name: ``companies()`` leaves it out and
  ``listed_companies()`` (``companies_view()["listed"]``) carries it with one line - "Listed <date>: <ticker> on <venue> -
  trade it in US Exchange" or "Acquired <date> by <acquirer> (<ticker>) - no longer privately traded; trade <ticker> in US
  Exchange" - and ``trade_in`` (the lifetime's US_EXCHANGE account + the listed instrument).  ``preview`` / ``private.trade``
  refuse a BUY (either kind), and a SELL with nothing held, with 400 COMPANY_LISTED / COMPANY_ACQUIRED and that line; lots
  bought before the date may still be SOLD (closing only, at the negotiated price, WARNING LISTED / ACQUIRED), so nothing is
  ever trapped.  A LISTED company's mark is the listed instrument's from that date (PRIVATE_MARK above); an ACQUIRED company
  keeps its last reported round with the acquisition in the note (no exchange ratio is applied in this build).
- MASTER SYNC: the master is seeded once (INSERT OR IGNORE), so ``private.sync_master`` (light) carries later research into
  an existing database; ``bind()`` runs it at every start under ``private:sync:<SYNC_VERSION>:<file sha256[:16]>`` (a replay
  while the file is unchanged; a failure is the bus's 500 + ERROR attention and the start goes on).  Statuses only move
  FORWARD (PRIVATE -> FILED_IPO -> PUBLIC; never back, never an owner-added company); missing listing fields and the
  acquisition fill the PRIVATE instrument's spec (never overwriting); seed notes follow the file; a switch to PUBLIC is an
  event plus one INFO attention.  Every PUBLIC company on a venue this build classifies as US (``instruments.US_EXCHANGES``)
  gets its ticker into the instrument master through ``InstrumentMaster.upsert_from_yahoo_meta`` with the sourced venue
  (INSERT OR IGNORE: an existing row is never changed), so it resolves as a US Exchange instrument without a network call;
  any other venue resolves through Yahoo as usual.  The tickers of LISTED companies whose pre-listing lots are held are
  pinned on the quote cache (priority ``PRIVATE_LISTED``, which the worker's HELD / ORDER scan never touches) so those
  marks stay live.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from .. import books as _books
from .. import config, execution, tables
from ..accounting import available_c
from ..clock import epoch_of, ny_of
from ..commands import Command, ConflictError, RejectError, TxContext
from ..db import get_setting
from ..instruments import US_EXCHANGES
from ..ledger import open_lots, position
from ..marks import add_months
from ..quotes import MarkView, unavailable
from ..units import fmt_money, from_c, qprice, to_c

SIDES = ("BUY", "SELL")
SHORT_SIDES = ("SELL_SHORT", "BUY_TO_COVER", "STO", "BTC")
KINDS = ("SECONDARY", "ROUND")
STATUSES = ("PRIVATE", "FILED_IPO", "PUBLIC")
COMPANY_ID_RE = re.compile(r"^PRV-[A-Z0-9]{1,40}$")
TICKER_RE = re.compile(r"^[A-Z0-9.\-^=]{1,16}$")
LOCKUP_TEXT = "lock-up until {date}; transfer restrictions apply (Rule 144)"
PCT2 = Decimal("0.01")
MAX_SETTLE_DAYS = 365
MAX_LOCKUP_MONTHS = 120
# no longer private: LISTED / ACQUIRED and the master sync (module docstring)
LISTED_CODE = "COMPANY_LISTED"
ACQUIRED_CODE = "COMPANY_ACQUIRED"
LISTED_PIN = "PRIVATE_LISTED"
SYNC_KIND = "private.sync_master"
SYNC_VERSION = "v1"
STATUS_RANK = {"PRIVATE": 0, "FILED_IPO": 1, "PUBLIC": 2}
# a listing venue as the issuer / the exchange names it (upper case) -> (Yahoo chart ``exchangeName``, short name for the one line)
VENUES = {"NASDAQ GLOBAL SELECT MARKET": ("NMS", "Nasdaq"), "NASDAQ GLOBAL MARKET": ("NGM", "Nasdaq"), "NASDAQ CAPITAL MARKET": ("NCM", "Nasdaq"),
          "NEW YORK STOCK EXCHANGE": ("NYQ", "NYSE"), "NYSE": ("NYQ", "NYSE"), "NYSE ARCA": ("PCX", "NYSE Arca"), "NYSE AMERICAN": ("ASE", "NYSE American"),
          "CBOE BZX": ("BTS", "Cboe BZX"), "CBOE BZX EXCHANGE": ("BTS", "Cboe BZX")}
CODE_SHORT = {"NMS": "Nasdaq", "NGM": "Nasdaq", "NCM": "Nasdaq", "NYQ": "NYSE", "PCX": "NYSE Arca", "ASE": "NYSE American", "BTS": "Cboe BZX"}


# ---- helpers ----------------------------------------------------------------------------------------------------------
@contextmanager
def _reader(services, conn: Optional[sqlite3.Connection] = None):
    """The caller's connection (a handler's transaction) or a fresh reader."""
    if conn is not None:
        yield conn
    else:
        with services.db.reader() as c:
            yield c


def _services(ctx: TxContext):
    if ctx.services is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return ctx.services


def slug_id(name_or_id: str) -> str:
    """``"Anysphere (Cursor)" -> "PRV-ANYSPHERECURSOR"``; an existing ``PRV-`` id is normalised, not re-prefixed."""
    s = re.sub(r"[^A-Z0-9]+", "", str(name_or_id or "").upper().replace("PRV-", "", 1))
    return f"PRV-{s}"


def _iso_date(s: Any, code: str, what: str) -> str:
    try:
        return date.fromisoformat(str(s).strip()).isoformat()
    except (TypeError, ValueError, AttributeError) as e:
        raise RejectError(code, f"{what} must be an ISO date (YYYY-MM-DD), got {s!r}") from e


def _dec(x: Any, code: str, what: str) -> Decimal:
    if isinstance(x, bool) or isinstance(x, float):
        raise RejectError(code, f"{what} must be a decimal string or integer, not {type(x).__name__}")
    try:
        d = Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError) as e:
        raise RejectError(code, f"{what} is not a number: {x!r}") from e
    if not d.is_finite():
        raise RejectError(code, f"{what} is not a finite number: {x!r}")
    return d


def _int(x: Any, code: str, what: str) -> int:
    if isinstance(x, bool):
        raise RejectError(code, f"{what} must be an integer")
    try:
        d = Decimal(str(x).strip())
    except (InvalidOperation, ValueError, TypeError) as e:
        raise RejectError(code, f"{what} is not an integer: {x!r}") from e
    if d != d.to_integral_value():
        raise RejectError(code, f"{what} must be a whole number, got {x!r}")
    return int(d)


def defaults(services, conn: Optional[sqlite3.Connection] = None) -> dict:
    """``settings.private_defaults`` (seeded from ``config``): ``{fee_pct: Decimal, settle_days: int, lockup_months: int}``."""
    with _reader(services, conn) as c:
        raw = get_setting(c, "private_defaults", None) or {}
    try:
        fee = Decimal(str(raw.get("fee_pct", config.PRIVATE_FEE_PCT)))
    except (InvalidOperation, ValueError, TypeError):
        fee = config.PRIVATE_FEE_PCT
    try:
        settle = int(raw.get("settle_days", config.PRIVATE_SETTLE_DAYS))
    except (ValueError, TypeError):
        settle = config.PRIVATE_SETTLE_DAYS
    try:
        lock = int(raw.get("lockup_months", config.PRIVATE_LOCKUP_MONTHS))
    except (ValueError, TypeError):
        lock = config.PRIVATE_LOCKUP_MONTHS
    return {"fee_pct": fee, "settle_days": settle, "lockup_months": lock}


# ---- company master reads ----------------------------------------------------------------------------------------------
def company_row(conn: sqlite3.Connection, key: str) -> Optional[sqlite3.Row]:
    """The company by ``company_id`` or by its instrument id."""
    return conn.execute("SELECT * FROM private_companies WHERE company_id=? OR instrument_id=?", (key, key)).fetchone()


def rounds(conn: sqlite3.Connection, company_id: str) -> List[dict]:
    """Every reported round, latest first (by ``round_date``, then by the order recorded)."""
    rows = conn.execute("SELECT * FROM private_rounds WHERE company_id=? ORDER BY round_date DESC, id DESC", (company_id,)).fetchall()
    return [round_view(r) for r in rows]


def latest_round(conn: sqlite3.Connection, company_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM private_rounds WHERE company_id=? ORDER BY round_date DESC, id DESC LIMIT 1", (company_id,)).fetchone()
    return round_view(row) if row is not None else None


def round_view(r) -> dict:
    pm = r["post_money_c"]
    return {"id": int(r["id"]), "company_id": r["company_id"], "round_date": r["round_date"], "round_type": r["round_type"],
            "post_money": None if pm is None else str(from_c(int(pm))), "post_money_c": None if pm is None else int(pm),
            "price_per_share": r["price_per_share"], "lead_investors": r["lead_investors"], "source_url": r["source_url"],
            "as_of": r["as_of"], "recorded_by": r["recorded_by"], "command_id": r["command_id"]}


def _spec_of(services, inst_or_id, conn: Optional[sqlite3.Connection]) -> dict:
    inst = inst_or_id if not isinstance(inst_or_id, str) else services.instruments.get(inst_or_id, conn)
    spec = getattr(inst, "spec", None) if inst is not None else None
    return dict(spec or {})


def _spec_json(conn: sqlite3.Connection, instrument_id: str) -> dict:
    """The stored spec of an instrument row (read through the caller's transaction, never the cache)."""
    r = conn.execute("SELECT spec_json FROM instruments WHERE instrument_id=?", (instrument_id,)).fetchone()
    try:
        return dict(json.loads(r["spec_json"] or "{}")) if r is not None else {}
    except (TypeError, ValueError):
        return {}


# ---- the master file and listings (module docstring LISTED / MASTER SYNC) --------------------------------------------------
_MASTER_LOCK = threading.Lock()
_MASTER: Dict[str, Any] = {"key": None, "rows": {}, "sha": None}


def master_file(strict: bool = False) -> Tuple[Dict[str, dict], Optional[str]]:
    """``(rows of private_companies.json by company_id, sha256 of the file)``, cached on path + mtime + size; ``({}, None)``
    without a file.  An unreadable file raises with ``strict`` (the sync must fail loudly), else it reads as empty."""
    path = config.private_companies_path()
    try:
        st = os.stat(path)
    except OSError:
        return {}, None
    key = (path, st.st_mtime_ns, st.st_size)
    with _MASTER_LOCK:
        if _MASTER["key"] == key:
            return _MASTER["rows"], _MASTER["sha"]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as e:
        if strict:
            raise RuntimeError(f"private_companies.json unreadable: {e}") from e
        return {}, None
    rows = data.get("companies") if isinstance(data, dict) else data
    by_id: Dict[str, dict] = {}
    for c in rows or []:
        if isinstance(c, dict) and c.get("name"):
            by_id[str(c.get("company_id") or slug_id(c["name"]))] = c
    sha = hashlib.sha256(raw).hexdigest()
    with _MASTER_LOCK:
        _MASTER.update(key=key, rows=by_id, sha=sha)
    return by_id, sha


def venue(name_or_code: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``"Nasdaq Global Select Market" -> ("NMS", "Nasdaq", "Nasdaq Global Select Market")``; a Yahoo code works too
    (``"NMS" -> ("NMS", "Nasdaq", "Nasdaq")``); any other venue keeps its own name and has no code (not classified as US)."""
    s = " ".join(str(name_or_code or "").split())
    if not s:
        return None, None, None
    up = s.upper()
    if up in CODE_SHORT:
        return up, CODE_SHORT[up], CODE_SHORT[up]
    if up in VENUES:
        code, short = VENUES[up]
        return code, short, s
    return None, s, s


def _on_or_before(on: Optional[str], today: date) -> bool:
    if not on:
        return True
    try:
        return date.fromisoformat(str(on)) <= today
    except ValueError:
        return True


@dataclass
class Listing:
    """Why a company is, or from ``on`` will be, no longer a private name: LISTED (status PUBLIC + ticker) or ACQUIRED (a
    completed acquisition by a listed company recorded in the master)."""
    kind: str
    company_id: str
    name: str
    ticker: Optional[str]
    on: Optional[str]
    effective: bool
    exchange: Optional[str] = None
    exchange_full: Optional[str] = None
    exchange_code: Optional[str] = None
    source_url: Optional[str] = None
    acquirer: Optional[str] = None

    @property
    def code(self) -> str:
        return LISTED_CODE if self.kind == "LISTED" else ACQUIRED_CODE

    @property
    def us(self) -> Optional[bool]:
        """True on a venue this build trades (``US_EXCHANGES``), False on another known code, None when the venue is unknown."""
        return None if not self.exchange_code else self.exchange_code in US_EXCHANGES

    def message(self) -> str:
        on = f" {self.on}" if self.on else ""
        if self.kind == "LISTED":
            where = f"{self.ticker} on {self.exchange}" if self.exchange else f"{self.ticker}"
            if not self.effective:
                return f"Lists{on}: {where} - private trading until then"
            if self.us is False:
                return f"Listed{on}: {where} - foreign listings are not traded in this build"
            return f"Listed{on}: {where} - trade it in US Exchange"
        by = self.acquirer or "a listed company"
        tick = f" ({self.ticker})" if self.ticker else ""
        if not self.effective:
            return f"Acquisition by {by}{tick} effective{on}"
        tail = f"; trade {self.ticker} in US Exchange" if self.ticker else ""
        return f"Acquired{on} by {by}{tick} - no longer privately traded{tail}"

    def trade_in(self, account_id: Optional[str] = None) -> Optional[dict]:
        """Where the listed shares trade: the US_EXCHANGE account of the lifetime of ``account_id`` and the listed instrument."""
        if not self.ticker or self.us is False:
            return None
        acct = None
        if account_id:
            try:
                acct = _books.account_id(_books.split_account_id(account_id)[0], "US_EXCHANGE")
            except ValueError:
                acct = None
        return {"account_id": acct, "instrument_id": self.ticker}

    def to_dict(self, account_id: Optional[str] = None) -> dict:
        return {"kind": self.kind, "code": self.code, "company_id": self.company_id, "name": self.name, "ticker": self.ticker, "on": self.on,
                "listed_on": self.on if self.kind == "LISTED" else None, "effective": self.effective, "exchange": self.exchange,
                "exchange_full": self.exchange_full, "exchange_code": self.exchange_code, "us": self.us, "source_url": self.source_url,
                "acquirer": self.acquirer, "message": self.message(), "trade_in": self.trade_in(account_id)}


def listing_of(row, spec: Optional[dict], lr: Optional[dict], today: date, master_row: Optional[dict] = None) -> Optional[Listing]:
    """The company's listing or acquisition, None while it is a private name.  The database's status + ticker decide a
    listing; the PRIVATE instrument's spec, then the master file row (same ticker only), then the IPO round give its date,
    venue and source.  An acquisition comes from the spec, else from the master file row."""
    spec = dict(spec or {})
    fr = master_row if isinstance(master_row, dict) else {}
    cid, name = row["company_id"], row["name"]
    status = str(row["status"] or "PRIVATE").upper()
    ticker = row["ticker"]
    if status == "PUBLIC" and ticker:
        same = str(fr.get("ticker") or "").strip().upper() == str(ticker).strip().upper()
        ipo = lr["round_date"] if lr and "IPO" in str(lr.get("round_type") or "").upper() else None
        on = spec.get("listed_on") or (fr.get("listed_on") if same else None) or ipo
        code, short, full = venue(spec.get("exchange_full") or spec.get("exchange") or (fr.get("exchange") if same else None))
        if spec.get("exchange_code"):
            code = str(spec["exchange_code"]).strip().upper()
            short = short or CODE_SHORT.get(code)
        src = spec.get("listing_source_url") or (fr.get("listing_source_url") if same else None)
        return Listing("LISTED", cid, name, ticker, on, _on_or_before(on, today), short, full, code, src)
    acq = spec.get("acquired") if isinstance(spec.get("acquired"), dict) else fr.get("acquired")
    if isinstance(acq, dict) and acq.get("on"):
        return Listing("ACQUIRED", cid, name, acq.get("acquirer_ticker") or None, str(acq["on"]), _on_or_before(acq["on"], today),
                       source_url=acq.get("source_url"), acquirer=acq.get("by"))
    return None


def _listing_fields(payload: dict) -> dict:
    """Owner-entered venue (a name or a Yahoo code) and source of a listing, as instrument spec fields."""
    out: Dict[str, Any] = {}
    ex = payload.get("exchange") or payload.get("exchange_code")
    if ex not in (None, ""):
        code, short, full = venue(ex)
        out.update(exchange=short, exchange_full=full, exchange_code=code)
    src = str(payload.get("listing_source_url") or payload.get("source_url") or "").strip()
    if src:
        out["listing_source_url"] = src
    return out


# ---- marks (§8.6, PRIVATE_MARK) ------------------------------------------------------------------------------------------
def private_mark(services, inst, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None) -> MarkView:
    """The mark of a PRIVATE instrument (``MarkProvider`` dispatches ``fn(services, inst)`` here; ``inst`` may also be an
    instrument / company id).  See the module docstring for the rules; ``conn`` lets a handler see its own transaction."""
    iid = getattr(inst, "instrument_id", inst)
    with _reader(services, conn) as c:
        row = company_row(c, iid)
        if row is None:
            return unavailable(iid, "no private company row")
        lr = latest_round(c, row["company_id"])
    now_ts = int(now if now is not None else services.clock.epoch())
    lst = listing_of(row, _spec_of(services, inst, conn), lr, ny_of(now_ts).date(), master_file()[0].get(row["company_id"]))
    return _mark_for(services, iid, lr, lst, now_ts)


def _mark_for(services, iid: str, lr: Optional[dict], lst: Optional[Listing], now_ts: int) -> MarkView:
    """LISTED from its date: the listed instrument's mark; otherwise the last reported round, with the coming listing or the
    completed acquisition in the note."""
    if lst is not None and lst.kind == "LISTED" and lst.effective:
        return _listed_mark(services, iid, lst.ticker, lst.on, now_ts)
    view = _round_mark(iid, lr, now_ts)
    if lst is not None and lst.kind == "LISTED" and lst.on:
        view.note = (view.note + "; " if view.note else "") + f"lists {lst.on} ({lst.ticker})"
    elif lst is not None and lst.kind == "ACQUIRED" and lst.effective:
        by = lst.acquirer or "a listed company"
        view.note = (view.note + "; " if view.note else "") + f"acquired {lst.on} by {by}: the last private mark is kept (no exchange ratio is applied in this build)"
    return view


def _round_mark(iid: str, lr: Optional[dict], now_ts: int) -> MarkView:
    if lr is None:
        return unavailable(iid, "no reported round")
    label = f"last round {lr['round_date']} ({lr['round_type']})"
    if lr["price_per_share"] is None:
        if lr["post_money_c"] is not None:
            return unavailable(iid, f"valuation only: no price per share disclosed ({label}, post-money {fmt_money(int(lr['post_money_c']))})")
        return unavailable(iid, f"no price per share or valuation reported ({label})")
    inputs = {"round_date": lr["round_date"], "round_type": lr["round_type"], "price_per_share": lr["price_per_share"], "post_money": lr["post_money"],
              "lead_investors": lr["lead_investors"], "source_url": lr["source_url"], "as_of": lr["as_of"], "recorded_by": lr["recorded_by"]}
    return MarkView(iid, qprice(lr["price_per_share"]), "private", "last reported round", "private_round",
                    epoch_of(date.fromisoformat(lr["round_date"]), "17:00"), now_ts, "last reported round", inputs, False, label)


def _listed_mark(services, iid: str, ticker: str, listed_on: Optional[str], now_ts: int) -> MarkView:
    tag = f"listed {listed_on} ({ticker})" if listed_on else f"listed ({ticker})"
    marks = getattr(services, "marks", None)
    if marks is None or ticker == iid:
        return unavailable(iid, f"{tag}: no mark provider for the listed instrument")
    try:
        try:
            snap = marks.snapshot([ticker])
        except TypeError:                      # a mark set whose snapshot() takes no ids
            snap = marks.snapshot()
        lv = snap.mark(ticker)
    except Exception as e:   # noqa: BLE001 — a failing listed lookup is an unavailable mark with the reason, never a crash
        return unavailable(iid, f"{tag}: listed mark failed: {type(e).__name__}: {e}"[:200])
    if lv is None or lv.mark is None or lv.quality == "unavailable":
        return unavailable(iid, f"{tag}: {(lv.note if lv is not None and lv.note else 'listed mark unavailable')}")
    inputs = dict(lv.inputs or {})
    inputs.update(ticker=ticker, listed_on=listed_on, listed_quality=lv.quality, listed_source=lv.source)
    return MarkView(iid, lv.mark, lv.quality, lv.source, lv.method, lv.quote_time, lv.fetched_at, lv.delay_label, inputs, False,
                    f"{tag}: mark of the listed instrument; pre-listing lots held here can only be sold (closing), new trades go through US Exchange")


# ---- views ---------------------------------------------------------------------------------------------------------------
def company_view(services, row, conn: Optional[sqlite3.Connection] = None, account_id: Optional[str] = None, now: Optional[int] = None) -> dict:
    """One company with its latest round, its mark, its listing (None while it is a private name; LISTED / ACQUIRED with
    the one-line message and ``trade_in``) and (optionally) the account's position in it."""
    now_ts = int(now if now is not None else services.clock.epoch())
    with _reader(services, conn) as c:
        lr = latest_round(c, row["company_id"])
        n = int(c.execute("SELECT COUNT(*) FROM private_rounds WHERE company_id=?", (row["company_id"],)).fetchone()[0])
        pos = None
        if account_id:
            q, cost = position(c, account_id, row["instrument_id"])
            lots = [dict(l) for l in open_lots(c, account_id, row["instrument_id"], "LONG")]
            pos = {"qty": q, "cost_c": cost, "cost": str(from_c(cost)), "lock_until": max((l.get("lock_until") or "" for l in lots), default="") or None}
        spec = _spec_of(services, row["instrument_id"], c)
    lst = listing_of(row, spec, lr, ny_of(now_ts).date(), master_file()[0].get(row["company_id"]))
    mark = _mark_for(services, row["instrument_id"], lr, lst, now_ts)
    out_now = lst is not None and lst.effective
    return {"company_id": row["company_id"], "name": row["name"], "sector": row["sector"], "country": row["country"], "status": row["status"],
            "ticker": row["ticker"], "instrument_id": row["instrument_id"], "notes": row["notes"], "updated_ts": int(row["updated_ts"] or 0),
            "listed_on": lst.on if lst is not None and lst.kind == "LISTED" else spec.get("listed_on"), "last_round": lr, "rounds_count": n,
            "mark": mark.to_dict(), "source_url": lr["source_url"] if lr else None, "as_of": lr["as_of"] if lr else None, "position": pos,
            "listing": lst.to_dict(account_id) if lst is not None else None, "privately_tradable": not out_now}


def _views(services, q: Optional[str], account_id: Optional[str], conn: Optional[sqlite3.Connection]) -> Tuple[List[dict], List[dict]]:
    """``(private names, no longer private)`` filtered by ``q`` on id / name / sector / ticker, each sorted by name."""
    names: List[dict] = []
    gone: List[dict] = []
    with _reader(services, conn) as c:
        rows = c.execute("SELECT * FROM private_companies ORDER BY name").fetchall()
        needle = (q or "").strip().upper()
        for r in rows:
            if needle and needle not in (r["company_id"] + " " + r["name"] + " " + (r["sector"] or "") + " " + (r["ticker"] or "")).upper():
                continue
            v = company_view(services, r, conn=c, account_id=account_id)
            (names if v["privately_tradable"] else gone).append(v)
    return names, gone


def companies(services, q: Optional[str] = None, account_id: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    """The PRIVATE names: every company that is not (yet) LISTED / ACQUIRED, filtered by ``q``, sorted by name."""
    return _views(services, q, account_id, conn)[0]


def listed_companies(services, q: Optional[str] = None, account_id: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    """The companies that are no longer private names (LISTED / ACQUIRED from their date), with the account's pre-listing position."""
    return _views(services, q, account_id, conn)[1]


def companies_view(services, q: Optional[str] = None, account_id: Optional[str] = None) -> dict:
    """``GET /api/private/companies`` (``api_products.private_companies``): ``{companies: [the private names], listed: [the
    companies no longer private, each with listing.message and listing.trade_in], account_id}``."""
    names, gone = _views(services, q, account_id, None)
    return {"companies": names, "listed": gone, "account_id": account_id}


def company(services, company_id: str, conn: Optional[sqlite3.Connection] = None, account_id: Optional[str] = None) -> dict:
    with _reader(services, conn) as c:
        row = company_row(c, company_id)
        if row is None:
            raise RejectError("COMPANY_UNKNOWN", f"no private company {company_id!r}", {"company_id": company_id})
        view = company_view(services, row, conn=c, account_id=account_id)
        view["rounds"] = rounds(c, row["company_id"])
    return view


# ---- validation (shared by preview and the trade handler) ----------------------------------------------------------------
@dataclass
class Validated:
    account_id: str
    lifetime_id: str
    company: dict
    inst: Any
    side: str
    kind: str
    qty: int
    price: Decimal
    fee_pct: Decimal
    settle_days: int
    lockup_months: int
    trade_date: date
    settle_date: date
    lock_until: Optional[str]
    gross_c: int
    fee_c: int
    net_c: int
    mark: MarkView
    discount_pct: Optional[Decimal]
    position_qty: int
    cost_relieved_c: int = 0
    realised_c: int = 0
    lots_used: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)


def fifo_preview(lots: List[dict], qty: int, gross_c: int) -> Tuple[int, List[dict]]:
    """The cost that ``ledger.relieve_fifo`` would relieve for ``qty`` from ``lots`` (oldest first): a lot's whole remaining
    cost when it is used up, else the pro-rata share HALF_EVEN; realised = gross − cost.  Pure."""
    left = int(qty)
    cost = 0
    used: List[dict] = []
    for l in lots:
        if left == 0:
            break
        rem = int(l["qty_remaining"])
        if rem <= 0:
            continue
        take = min(left, rem)
        c = int(l["cost_c"]) if take == rem else to_c(Decimal(int(l["cost_c"])) / Decimal(100) * Decimal(take) / Decimal(rem))
        used.append({"lot_id": int(l["lot_id"]), "qty": take, "cost_relieved_c": c, "lock_until": l["lock_until"], "unit_cost": str(l["unit_cost"])})
        cost += c
        left -= take
    if left:
        raise RejectError("OVERSELL", f"the open lots hold {int(qty) - left:,} shares, asked to sell {int(qty):,}", {"qty": int(qty)})
    return cost, used


def validate(services, req: dict, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None) -> Validated:
    """§13.13 validation in the order that gives the most useful code: QP gate → account → company → side → listing (a LISTED /
    ACQUIRED company refuses a BUY and a SELL with nothing held: COMPANY_LISTED / COMPANY_ACQUIRED) → kind / qty / price →
    economics → SELL position and lock-ups.  Raises ``RejectError`` (400) / ``ConflictError`` (409)."""
    req = dict(req or {})
    account_id = str(req.get("account_id") or "").strip()
    try:
        lid, role = _books.split_account_id(account_id)
    except ValueError as e:
        raise RejectError("ACCOUNT_INVALID", str(e)) from e
    _books.require_private(services, lid)                       # NOT_ELIGIBLE_QP / LIFETIME_CLOSED / LIFETIME_UNKNOWN
    with _reader(services, conn) as c:
        acct = _books.account_row(c, account_id)
        if acct is None:
            raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist (Private Markets is {lid}:PRIVATE)")
        if acct["status"] != "OPEN":
            raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
        akind = _books.account_kind_for(acct)                 # the §7.2 kind (accounts.kind is TREASURY / TRADING / PB)
        if akind != "PRIVATE":
            raise RejectError("NOT_TRADEABLE_HERE", f"private company shares trade only in the Private Markets account ({lid}:PRIVATE), not in {account_id}",
                              {"account_kind": akind})
        cid = str(req.get("company_id") or req.get("instrument_id") or "").strip()
        row = company_row(c, cid) if cid else None
        if row is None:
            raise RejectError("COMPANY_UNKNOWN", f"no private company {cid!r}: add it first (Add a private company)", {"company_id": cid})
        inst = services.instruments.get(row["instrument_id"], c)
        if inst is None or inst.kind != "PRIVATE":
            raise RejectError("INSTRUMENT_UNKNOWN", f"{row['instrument_id']} is not a PRIVATE instrument")
        if not services.instruments.tradeable_in(inst, akind):
            raise RejectError("NOT_TRADEABLE_HERE", f"{inst.symbol} does not trade in a {akind} account")
        side = str(req.get("side") or "").strip().upper()
        if side in SHORT_SIDES:
            raise RejectError("NOT_TRADEABLE_HERE", "private company shares cannot be sold short or bought to cover: sides are BUY and SELL only", {"side": side})
        if side not in SIDES:
            raise RejectError("SIDE_INVALID", f"side {side!r} is not valid for private shares: use BUY or SELL")
        now_ts = int(now if now is not None else services.clock.epoch())
        trade_date = ny_of(now_ts).date()
        lr = latest_round(c, row["company_id"])
        listing = listing_of(row, dict(getattr(inst, "spec", None) or {}), lr, trade_date, master_file()[0].get(row["company_id"]))
        listed_warning = None
        if listing is not None and listing.effective:              # LISTED / ACQUIRED: no new private purchase; held pre-listing lots may be sold
            held, _held_cost = position(c, account_id, inst.instrument_id)
            if side == "BUY" or held <= 0:
                raise RejectError(listing.code, listing.message(), {"company_id": row["company_id"], "side": side, "position": held,
                                                                     "listing": listing.to_dict(account_id), "trade_in": listing.trade_in(account_id)})
            listed_warning = {"code": listing.kind, "message": f"{listing.message()}: this SELL closes pre-listing shares held in {account_id} at the negotiated price",
                              "trade_in": listing.trade_in(account_id)}
        kind = str(req.get("kind") or "SECONDARY").strip().upper()
        if kind not in KINDS:
            raise RejectError("KIND_INVALID", f"kind {kind!r} must be SECONDARY (negotiated secondary) or ROUND (participate in a round)")
        if req.get("qty") in (None, ""):
            raise RejectError("QTY_INVALID", "quantity is required")
        qty = _int(req.get("qty"), "QTY_INVALID", "quantity")
        if qty <= 0:
            raise RejectError("QTY_INVALID", "quantity must be a positive whole number of shares")
        if req.get("price") in (None, ""):
            raise RejectError("PRICE_REQUIRED", "a private secondary needs the negotiated price per share (sentence PRIVATE_TRADE): enter the price you agreed")
        price = qprice(_dec(req.get("price"), "PRICE_INVALID", "price"))
        if price <= 0:
            raise RejectError("PRICE_INVALID", "the negotiated price must be positive")
        d = defaults(services, c)
        fee_pct = d["fee_pct"] if kind == "SECONDARY" else Decimal(0)
        if req.get("fee_pct") not in (None, ""):
            fee_pct = _dec(req.get("fee_pct"), "FEE_INVALID", "fee_pct")
        if fee_pct < 0 or fee_pct > 1:
            raise RejectError("FEE_INVALID", "fee_pct is a fraction of the gross amount between 0 and 1 (0.03 = 3 %)")
        settle_days = d["settle_days"] if req.get("settle_days") in (None, "") else _int(req.get("settle_days"), "SETTLE_DAYS_INVALID", "settle_days")
        if settle_days < 0 or settle_days > MAX_SETTLE_DAYS:
            raise RejectError("SETTLE_DAYS_INVALID", f"settle_days must be between 0 and {MAX_SETTLE_DAYS} calendar days")
        lockup = d["lockup_months"] if req.get("lockup_months") in (None, "") else _int(req.get("lockup_months"), "LOCKUP_INVALID", "lockup_months")
        if lockup < 0 or lockup > MAX_LOCKUP_MONTHS:
            raise RejectError("LOCKUP_INVALID", f"lockup_months must be between 0 and {MAX_LOCKUP_MONTHS}")
        settle_date = trade_date + timedelta(days=settle_days)
        lock_until = add_months(trade_date, lockup).isoformat() if (side == "BUY" and lockup > 0) else None
        gross_c = inst.contract_value_c(price, qty)
        fee_c = to_c(Decimal(qty) * price * fee_pct)
        net_c = gross_c + fee_c if side == "BUY" else gross_c - fee_c
        mark = _mark_for(services, inst.instrument_id, lr, listing, now_ts)
        disc = None
        if mark.mark is not None and mark.mark > 0:
            disc = ((price / mark.mark - Decimal(1)) * Decimal(100)).quantize(PCT2)
        pos_qty, _cost = position(c, account_id, inst.instrument_id)
        v = Validated(account_id, lid, {"company_id": row["company_id"], "name": row["name"], "status": row["status"], "ticker": row["ticker"],
                                        "instrument_id": row["instrument_id"], "listing": listing.to_dict(account_id) if listing is not None else None},
                      inst, side, kind, qty, price, fee_pct, settle_days, lockup, trade_date,
                      settle_date, lock_until, gross_c, fee_c, net_c, mark, disc, pos_qty)
        if side == "SELL":
            if pos_qty < qty:
                raise RejectError("OVERSELL", f"SELL {qty:,} {inst.symbol} but the position is {max(pos_qty, 0):,}", {"position": pos_qty, "qty": qty})
            lots = [dict(l) for l in open_lots(c, account_id, inst.instrument_id, "LONG")]
            v.cost_relieved_c, v.lots_used = fifo_preview(lots, qty, gross_c)
            v.realised_c = gross_c - v.cost_relieved_c
            locked = [u["lock_until"] for u in v.lots_used if u.get("lock_until") and u["lock_until"] > trade_date.isoformat()]
            if locked:
                v.warnings.append({"code": "LOCKUP", "message": LOCKUP_TEXT.format(date=max(locked)), "lock_until": max(locked),
                                   "lots": [u["lot_id"] for u in v.lots_used if u.get("lock_until") in locked]})
        if listed_warning is not None:
            v.warnings.append(listed_warning)
    return v


# ---- preview (§13.13, §16 POST /api/private/preview) --------------------------------------------------------------------
def preview(services, req: dict, now: Optional[int] = None, conn: Optional[sqlite3.Connection] = None) -> dict:
    """Economics, settlement, lock-up, mark, funding plan (BUY), FIFO cost and realised (SELL), warnings, blockers and the
    PRIVATE_TRADE sentence for a trade request; raises the validation codes (400 / 409) exactly like the handler."""
    v = validate(services, req, conn=conn, now=now)
    blockers: List[dict] = []
    funding = None
    if v.side == "BUY":
        funding = _funding_plan(services, v, conn=conn)
        if funding["unfunded_c"] > 0:
            blockers.append({"code": "INSUFFICIENT_FUNDS", "message": f"{v.account_id} needs USD {fmt_money(v.net_c)} at {v.settle_date.isoformat()}: own available "
                                                                       f"{fmt_money(funding['own_available_c'])}, Treasury available {fmt_money(funding['treasury_available_c'])}"})
    note = (f"pending until {v.settle_date.isoformat()} (T+{v.settle_days} calendar days)" + (f"; lock-up until {v.lock_until}" if v.lock_until else ""))
    out = {"account_id": v.account_id, "lifetime_id": v.lifetime_id, "company": v.company, "instrument_id": v.inst.instrument_id, "side": v.side, "kind": v.kind,
           "qty": v.qty, "price": str(v.price), "fee_pct": str(v.fee_pct), "settle_days": v.settle_days, "lockup_months": v.lockup_months,
           "trade_date": v.trade_date.isoformat(), "settle_date": v.settle_date.isoformat(), "lock_until": v.lock_until,
           "gross": str(from_c(v.gross_c)), "gross_c": v.gross_c, "fee": str(from_c(v.fee_c)), "fee_c": v.fee_c, "net": str(from_c(v.net_c)), "net_c": v.net_c,
           "mark": v.mark.to_dict(), "discount_pct": None if v.discount_pct is None else str(v.discount_pct), "position_qty": v.position_qty,
           "cost_relieved": str(from_c(v.cost_relieved_c)), "cost_relieved_c": v.cost_relieved_c, "realised": str(from_c(v.realised_c)), "realised_c": v.realised_c,
           "lots": v.lots_used, "funding": funding, "warnings": list(v.warnings), "blockers": blockers, "note": note,
           "sentence": tables.CONVENTIONS.get("PRIVATE_TRADE", ""), "conventions": ["PRIVATE_TRADE", "PRIVATE_MARK", "QP", "FIFO"],
           "executable": not blockers, "as_of": int(now if now is not None else services.clock.epoch())}
    return out


def _funding_plan(services, v: Validated, conn: Optional[sqlite3.Connection] = None) -> dict:
    """What ``execution.fund`` will do at the fill: own available at the settle date, else a Treasury sweep of the shortfall."""
    snap = services.marks.snapshot()
    with _reader(services, conn) as c:
        own = available_c(c, v.account_id, "USD", v.settle_date, snap, services.instruments, services.clock.now())
    treas = int(services.accounting.treasury(v.lifetime_id, snapshot=snap).available_c)
    shortfall = max(0, v.net_c - own)
    sweep = min(shortfall, max(treas, 0)) if shortfall else 0
    unfunded = shortfall - sweep
    source = "own" if shortfall == 0 else ("sweep" if unfunded == 0 else "none")
    return {"need_c": v.net_c, "need": str(from_c(v.net_c)), "own_available_c": own, "own_available": str(from_c(own)), "treasury_available_c": treas,
            "treasury_available": str(from_c(treas)), "shortfall_c": shortfall, "sweep_in_c": sweep, "sweep_in": str(from_c(sweep)), "unfunded_c": unfunded,
            "source": source, "settle_date": v.settle_date.isoformat(),
            "note": ("paid from the account's own available cash" if source == "own" else
                     (f"sweep-in of {fmt_money(sweep)} from {v.lifetime_id}:TREASURY at the fill (sentence SWEEP)" if source == "sweep" else
                      f"short by {fmt_money(unfunded)}: the trade will be refused (INSUFFICIENT_FUNDS)"))}


# ---- handlers --------------------------------------------------------------------------------------------------------------
def h_trade(ctx: TxContext, payload: dict) -> dict:
    """``private.trade {account_id, company_id, side: BUY | SELL, qty, price, kind: SECONDARY | ROUND, fee_pct?, settle_days?,
    lockup_months?}``: validate, then one NEGOTIATED fill through ``execution.apply_system_fill`` (funding, postings, lot with
    lock-up, settlement row).  Returns ``{fill, settlement, lot, reliefs, economics, mark, warnings, ...}``."""
    services = _services(ctx)
    v = validate(services, payload, conn=ctx.conn, now=ctx.ts)
    inst = v.inst
    quote = {"mark": None if v.mark.mark is None else str(v.mark.mark), "quality": v.mark.quality, "method": v.mark.method, "source": v.mark.source,
             "source_url": (v.mark.inputs or {}).get("source_url"), "as_of": (v.mark.inputs or {}).get("as_of"), "note": v.mark.note,
             "negotiated_price": str(v.price), "discount_pct": None if v.discount_pct is None else str(v.discount_pct), "kind": v.kind,
             "fee_pct": str(v.fee_pct), "settle_days": v.settle_days, "lockup_months": v.lockup_months, "basis": "negotiated (PRIVATE_TRADE)"}
    if v.side == "BUY":
        note = f"negotiated {v.kind.lower()} purchase, settles T+{v.settle_days} calendar days on {v.settle_date.isoformat()}" + (f", lock-up until {v.lock_until}" if v.lock_until else "")
    else:
        lock = next((w for w in v.warnings if w.get("code") == "LOCKUP"), None)
        closing = any(w.get("code") in ("LISTED", "ACQUIRED") for w in v.warnings)
        note = f"negotiated {v.kind.lower()} sale, settles T+{v.settle_days} calendar days on {v.settle_date.isoformat()}" + \
               (f", inside the lock-up until {lock['lock_until']}" if lock else "") + ("; closes pre-listing shares of a company no longer private" if closing else "")
    fr = execution.apply_system_fill(ctx, v.account_id, inst, v.side, v.qty, v.price, "NEGOTIATED", quote, f"private:{ctx.request_id}", note, services,
                                     settle_days=v.settle_days, lock_until=v.lock_until, fee_c=v.fee_c)
    conn = ctx.conn
    settlement = None
    if fr.settle_id:
        s = conn.execute("SELECT * FROM settlements WHERE settle_id=?", (fr.settle_id,)).fetchone()
        if s is not None:
            settlement = {"settle_id": s["settle_id"], "due_date": s["due_date"], "amount_c": int(s["amount_c"]), "amount": str(from_c(int(s["amount_c"]))),
                          "gl": s["gl"], "status": s["status"], "currency": s["currency"], "note": f"pending until {s['due_date']} (T+{v.settle_days} calendar days)"}
    lot = None
    if fr.lot_id is not None:
        l = conn.execute("SELECT * FROM lots WHERE lot_id=?", (int(fr.lot_id),)).fetchone()
        if l is not None:
            lot = {"lot_id": int(l["lot_id"]), "side": l["side"], "open_fill_id": l["open_fill_id"], "open_ts": int(l["open_ts"]), "open_date": ny_of(int(l["open_ts"])).date().isoformat(),
                   "qty_open": int(l["qty_open"]), "qty_remaining": int(l["qty_remaining"]), "unit_cost": l["unit_cost"], "cost_c": int(l["cost_c"]),
                   "cost": str(from_c(int(l["cost_c"]))), "lock_until": l["lock_until"], "currency": l["currency"]}
    ctx.event("PRIVATE_TRADE", account_id=v.account_id, instrument_id=inst.instrument_id, ref_type="fill", ref_id=fr.fill_id, side=v.side, kind=v.kind, qty=v.qty,
              price=str(v.price), gross_c=v.gross_c, fee_c=v.fee_c, settle_date=v.settle_date.isoformat(), lock_until=v.lock_until,
              realised_c=fr.realised_c, company_id=v.company["company_id"], warnings=[w["code"] for w in v.warnings])
    fill = fr.to_dict()
    fill.update(origin="NEGOTIATED", kind=v.kind, company_id=v.company["company_id"])
    return {"fill": fill, "settlement": settlement, "lot": lot, "reliefs": fr.reliefs,
            "economics": {"gross_c": v.gross_c, "gross": str(from_c(v.gross_c)), "fee_c": v.fee_c, "fee": str(from_c(v.fee_c)), "net_c": v.net_c, "net": str(from_c(v.net_c)),
                          "cost_relieved_c": fr.cost_relieved_c, "cost_relieved": str(from_c(fr.cost_relieved_c)), "realised_c": fr.realised_c,
                          "realised": str(from_c(fr.realised_c)), "fee_pct": str(v.fee_pct), "discount_pct": None if v.discount_pct is None else str(v.discount_pct)},
            "mark": v.mark.to_dict(), "warnings": list(v.warnings), "company": v.company, "account_id": v.account_id, "side": v.side, "kind": v.kind, "qty": v.qty,
            "price": str(v.price), "trade_date": v.trade_date.isoformat(), "settle_date": v.settle_date.isoformat(), "lock_until": v.lock_until,
            "position_qty": fr.position_qty, "funding": fr.funding.to_dict() if fr.funding else None, "sentence": tables.CONVENTIONS.get("PRIVATE_TRADE", "")}


def _round_payload(payload: dict) -> dict:
    """Normalise an owner-entered round (``private.record_round`` / ``add_company.last_round``): ISO dates, a round type,
    at least one of post-money / price per share, and the mandatory source URL."""
    p = dict(payload or {})
    round_date = _iso_date(p.get("round_date"), "DATE_INVALID", "round_date") if p.get("round_date") not in (None, "") else None
    if round_date is None:
        raise RejectError("DATE_INVALID", "round_date is required (the date of the reported round)")
    round_type = str(p.get("round_type") or "").strip()
    if not round_type:
        raise RejectError("ROUND_TYPE_REQUIRED", "round_type is required (Series X, tender offer, secondary, SAFE, secondary indication ...)")
    post_c: Optional[int] = None
    if p.get("post_money_c") not in (None, ""):
        post_c = _int(p.get("post_money_c"), "AMOUNT_INVALID", "post_money_c")
    elif p.get("post_money") not in (None, ""):
        post_c = to_c(_dec(p.get("post_money"), "AMOUNT_INVALID", "post_money"))
    elif p.get("post_money_usd") not in (None, ""):
        post_c = to_c(_dec(p.get("post_money_usd"), "AMOUNT_INVALID", "post_money_usd"))
    if post_c is not None and post_c <= 0:
        raise RejectError("AMOUNT_INVALID", "post_money must be positive")
    price: Optional[str] = None
    if p.get("price_per_share") not in (None, ""):
        d = qprice(_dec(p.get("price_per_share"), "PRICE_INVALID", "price_per_share"))
        if d <= 0:
            raise RejectError("PRICE_INVALID", "price_per_share must be positive")
        price = str(d)
    if post_c is None and price is None:
        raise RejectError("NUMBER_REQUIRED", "a reported round needs a post-money valuation and/or a price per share")
    src = str(p.get("source_url") or "").strip()
    if not src:
        raise RejectError("SOURCE_REQUIRED", "a source URL is mandatory for every reported number (nothing is recorded without one)")
    as_of = _iso_date(p.get("as_of"), "DATE_INVALID", "as_of") if p.get("as_of") not in (None, "") else round_date
    leads = p.get("lead_investors")
    if isinstance(leads, (list, tuple)):
        leads = ", ".join(str(x).strip() for x in leads if str(x).strip())
    leads = (str(leads).strip() or None) if leads is not None else None
    return {"round_date": round_date, "round_type": round_type, "post_money_c": post_c, "price_per_share": price, "lead_investors": leads, "source_url": src, "as_of": as_of}


def _insert_round(conn: sqlite3.Connection, company_id: str, r: dict, command_id: Optional[int]) -> int:
    if conn.execute("SELECT 1 FROM private_rounds WHERE company_id=? AND round_date=? AND round_type=?", (company_id, r["round_date"], r["round_type"])).fetchone():
        raise ConflictError("ROUND_EXISTS", f"{company_id} already has a {r['round_type']} round dated {r['round_date']}", {"company_id": company_id})
    cur = conn.execute("INSERT INTO private_rounds(company_id, round_date, round_type, post_money_c, price_per_share, lead_investors, source_url, as_of, recorded_by, command_id)"
                       " VALUES (?,?,?,?,?,?,?,?,'owner',?)",
                       (company_id, r["round_date"], r["round_type"], r["post_money_c"], r["price_per_share"], r["lead_investors"], r["source_url"], r["as_of"], command_id))
    return int(cur.lastrowid)


def h_record_round(ctx: TxContext, payload: dict) -> dict:
    """``private.record_round {company_id, round_date, round_type, post_money? | price_per_share?, lead_investors?, source_url, as_of?}``
    (light): appends an owner-recorded round; the mark changes only through this (sentence PRIVATE_MARK)."""
    services = _services(ctx)
    conn = ctx.conn
    cid = str(payload.get("company_id") or "").strip()
    row = company_row(conn, cid) if cid else None
    if row is None:
        raise RejectError("COMPANY_UNKNOWN", f"no private company {cid!r}", {"company_id": cid})
    r = _round_payload(payload)
    rid = _insert_round(conn, row["company_id"], r, ctx.command_id)
    conn.execute("UPDATE private_companies SET updated_ts=? WHERE company_id=?", (ctx.ts, row["company_id"]))
    ctx.event("PRIVATE_ROUND_RECORDED", instrument_id=row["instrument_id"], ref_type="company", ref_id=row["company_id"], round_id=rid,
              round_date=r["round_date"], round_type=r["round_type"], post_money_c=r["post_money_c"], price_per_share=r["price_per_share"], source_url=r["source_url"])
    row = company_row(conn, row["company_id"])
    view = company_view(services, row, conn=conn, now=ctx.ts)
    new = conn.execute("SELECT * FROM private_rounds WHERE id=?", (rid,)).fetchone()
    return {"company": view, "round": round_view(new), "mark": view["mark"]}


def h_add_company(ctx: TxContext, payload: dict) -> dict:
    """``private.add_company {name, sector?, country?, status?, ticker?, listed_on?, exchange?, source_url?, notes?, company_id?, settle_days?, last_round?}`` (light):
    a new PRIVATE instrument (``PRV-<slug>``, support COMPUTED) plus its company row and, when given, an owner-recorded
    round (a source URL is required for any number).  409 EXISTS when the id is taken."""
    services = _services(ctx)
    conn = ctx.conn
    name = str(payload.get("name") or "").strip()
    if not name:
        raise RejectError("NAME_REQUIRED", "the company name is required")
    status = str(payload.get("status") or "PRIVATE").strip().upper()
    if status not in STATUSES:
        raise RejectError("STATUS_INVALID", f"status must be one of {', '.join(STATUSES)}")
    ticker = (str(payload.get("ticker") or "").strip().upper() or None)
    if ticker is not None and not TICKER_RE.match(ticker):
        raise RejectError("TICKER_INVALID", f"ticker {ticker!r} is not a valid symbol")
    if status == "PUBLIC" and not ticker:
        raise RejectError("TICKER_REQUIRED", "a PUBLIC company needs its listed ticker (the mark switches to that instrument)")
    cid = slug_id(payload.get("company_id") or name)
    if not COMPANY_ID_RE.match(cid):
        raise RejectError("COMPANY_ID_INVALID", f"company id {cid!r} must be PRV-<letters/digits>")
    if company_row(conn, cid) is not None or services.instruments.get(cid, conn) is not None:
        raise ConflictError("EXISTS", f"{cid} already exists", {"company_id": cid})
    lr = payload.get("last_round")
    r = _round_payload(lr) if isinstance(lr, dict) and lr else None
    listed_on = _iso_date(payload.get("listed_on"), "DATE_INVALID", "listed_on") if payload.get("listed_on") not in (None, "") else None
    settle_days = defaults(services, conn)["settle_days"]
    if payload.get("settle_days") not in (None, ""):
        settle_days = _int(payload.get("settle_days"), "SETTLE_DAYS_INVALID", "settle_days")
    spec = {"sector": payload.get("sector"), "country": payload.get("country"), "status": status, "ticker": ticker, "listed_on": listed_on, "recorded_by": "owner"}
    if status == "PUBLIC":
        spec.update(_listing_fields(payload))
    inserted = services.instruments._insert(conn, ctx.ts, instrument_id=cid, kind="PRIVATE", symbol=cid, name=name, currency="USD", session_group="PRIVATE_MARKETS",
                                            settle_days=int(settle_days), account_kinds=["PRIVATE"], spec=spec, support="COMPUTED")
    if not inserted:
        raise ConflictError("EXISTS", f"instrument {cid} already exists", {"company_id": cid})
    conn.execute("INSERT INTO private_companies(company_id, name, sector, country, status, ticker, instrument_id, notes, updated_ts) VALUES (?,?,?,?,?,?,?,?,?)",
                 (cid, name, payload.get("sector"), payload.get("country"), status, ticker, cid, payload.get("notes"), ctx.ts))
    rid = _insert_round(conn, cid, r, ctx.command_id) if r else None
    services.instruments.refresh(cid, conn)
    listed = ensure_listed_instrument(ctx.tx, services, ticker, name, spec.get("exchange_code"), spec.get("exchange_full")) if status == "PUBLIC" else None
    ctx.event("PRIVATE_COMPANY_ADDED", instrument_id=cid, ref_type="company", ref_id=cid, name=name, status=status, ticker=ticker, round_id=rid, listed_instrument=listed)
    row = company_row(conn, cid)
    view = company_view(services, row, conn=conn, now=ctx.ts)
    view["rounds"] = rounds(conn, cid)
    view["listed_instrument"] = listed
    return view


def h_set_status(ctx: TxContext, payload: dict) -> dict:
    """``private.set_status {company_id, status, ticker?, listed_on?, exchange?, source_url?}`` (light): PRIVATE → FILED_IPO →
    PUBLIC (+ ticker); a PUBLIC company's mark switches to the listed instrument from ``listed_on`` (default: the command's
    date) and from that date it is no longer a private name (LISTED).  ``exchange`` (a venue name or a Yahoo code) and
    ``source_url`` describe the listing; a US venue registers the ticker as a US Exchange instrument
    (``ensure_listed_instrument``).  An owner-set status is never moved again by the master file (MASTER SYNC)."""
    services = _services(ctx)
    conn = ctx.conn
    cid = str(payload.get("company_id") or "").strip()
    row = company_row(conn, cid) if cid else None
    if row is None:
        raise RejectError("COMPANY_UNKNOWN", f"no private company {cid!r}", {"company_id": cid})
    status = str(payload.get("status") or "").strip().upper()
    if status not in STATUSES:
        raise RejectError("STATUS_INVALID", f"status must be one of {', '.join(STATUSES)}")
    ticker = (str(payload.get("ticker") or row["ticker"] or "").strip().upper() or None)
    if ticker is not None and not TICKER_RE.match(ticker):
        raise RejectError("TICKER_INVALID", f"ticker {ticker!r} is not a valid symbol")
    if status == "PUBLIC" and not ticker:
        raise RejectError("TICKER_REQUIRED", "a PUBLIC company needs its listed ticker (the mark switches to that instrument)")
    listed_on = None
    spec = _spec_json(conn, row["instrument_id"])
    if status == "PUBLIC":
        listed_on = _iso_date(payload.get("listed_on"), "DATE_INVALID", "listed_on") if payload.get("listed_on") not in (None, "") else ny_of(ctx.ts).date().isoformat()
        spec.update(_listing_fields(payload))
    conn.execute("UPDATE private_companies SET status=?, ticker=?, updated_ts=? WHERE company_id=?", (status, ticker, ctx.ts, row["company_id"]))
    spec.update(status=status, ticker=ticker, listed_on=listed_on)
    conn.execute("UPDATE instruments SET spec_json=?, updated_ts=? WHERE instrument_id=?", (json.dumps(spec), ctx.ts, row["instrument_id"]))
    services.instruments.refresh(row["instrument_id"], conn)
    listed = ensure_listed_instrument(ctx.tx, services, ticker, row["name"], spec.get("exchange_code"), spec.get("exchange_full")) if status == "PUBLIC" else None
    ctx.event("PRIVATE_STATUS_CHANGED", instrument_id=row["instrument_id"], ref_type="company", ref_id=row["company_id"], status=status, ticker=ticker,
              listed_on=listed_on, exchange=spec.get("exchange") if status == "PUBLIC" else None, listed_instrument=listed)
    refresh_pins(services, conn)
    row = company_row(conn, row["company_id"])
    view = company_view(services, row, conn=conn, now=ctx.ts)
    view["listed_instrument"] = listed
    return view


def ensure_listed_instrument(tx, services, ticker: Optional[str], name: str, exchange_code: Optional[str], exchange_full: Optional[str]) -> dict:
    """Make a listed company's ticker resolvable as a US Exchange instrument through the master's public
    ``upsert_from_yahoo_meta`` (its chart-meta shape, filled from the sourced venue - no quote is fetched; INSERT OR IGNORE,
    so an existing row, a seeded universe name or an earlier Yahoo resolve, is never changed).  Only a venue this build
    classifies as US (``US_EXCHANGES``) is registered this way; any other venue is left to the normal Yahoo resolve.  Returns
    what the master holds now, with ``problem`` when a US listing's row there does not trade in US Exchange."""
    master = services.instruments
    if not ticker:
        return {"ticker": None, "in_master": False, "created": False, "note": "no ticker"}
    inst = master.get(ticker, tx.conn)
    if inst is None and callable(getattr(master, "by_yahoo_symbol", None)):
        inst = master.by_yahoo_symbol(ticker, tx.conn)       # a row that already quotes this Yahoo symbol under another id
    created = False
    if inst is None:
        if exchange_code not in US_EXCHANGES:
            where = exchange_full or exchange_code or "not recorded"
            return {"ticker": ticker, "in_master": False, "created": False,
                    "note": f"venue {where}: {ticker} resolves through Yahoo when it is opened in US Exchange"}
        meta = {"symbol": ticker, "instrumentType": "EQUITY", "exchangeName": exchange_code, "fullExchangeName": exchange_full or exchange_code,
                "currency": "USD", "longName": name, "exchangeTimezoneName": "America/New_York"}
        try:
            inst = master.upsert_from_yahoo_meta(tx, ticker, meta)
        except RejectError as e:                             # e.g. ALREADY_IN_MASTER: reported as a problem, never a failed sync
            return {"ticker": ticker, "in_master": False, "created": False, "problem": f"{ticker} could not be registered: {e.code}: {e.message}"}
        if inst is None:
            return {"ticker": ticker, "in_master": False, "created": False, "problem": f"{ticker} could not be registered in the instrument master"}
        created = True
    out = {"ticker": ticker, "instrument_id": inst.instrument_id, "in_master": True, "created": created, "kind": inst.kind, "support": inst.support,
           "us_exchange": bool(master.tradeable_in(inst, "US_EXCHANGE"))}
    if not out["us_exchange"] and exchange_code in US_EXCHANGES:
        out["problem"] = f"{ticker} is in the instrument master as {inst.kind} / {inst.support} / {list(inst.account_kinds)}: it does not trade in US Exchange"
    return out


def listed_held_tickers(services, conn: Optional[sqlite3.Connection] = None) -> List[str]:
    """Tickers of LISTED companies whose pre-listing lots are held in an OPEN lifetime (their marks need the listed quote)."""
    with _reader(services, conn) as c:
        rows = c.execute("SELECT DISTINCT pc.ticker FROM private_companies pc JOIN v_positions p ON p.instrument_id = pc.instrument_id"
                         " JOIN accounts a ON a.account_id = p.account_id JOIN lifetimes l ON l.lifetime_id = a.lifetime_id"
                         " WHERE l.status = 'OPEN' AND p.qty != 0 AND pc.status = 'PUBLIC' AND pc.ticker IS NOT NULL").fetchall()
    return sorted({str(r[0]) for r in rows})


def refresh_pins(services, conn: Optional[sqlite3.Connection] = None) -> List[str]:
    """Pin those tickers on the quote cache under ``PRIVATE_LISTED`` (``QuoteCache.set_pins`` replaces that priority's whole
    set; the worker's HELD / ORDER scan never touches it).  A runtime without a quote cache pins nothing; a failure is
    logged (a pin is a refresh-schedule hint, never a reason to fail a command)."""
    cache = getattr(services, "cache", None)
    if cache is None or not callable(getattr(cache, "set_pins", None)):
        return []
    try:
        master = services.instruments
        ids = [t for t in listed_held_tickers(services, conn) if master.get(t, conn) is not None]
        cache.set_pins(LISTED_PIN, ids)
        return ids
    except Exception as e:   # noqa: BLE001 — logged, the caller goes on
        log = getattr(services, "log", None)
        if log is not None:
            log.warning("private: pinning the listed tickers failed: %s: %s", type(e).__name__, e)
        return []


def _owner_set_status(conn: sqlite3.Connection, company_id: str) -> bool:
    """True when the owner ever set this company's status (``private.set_status``): the master file then never moves it."""
    return conn.execute("SELECT 1 FROM events e JOIN commands c ON c.id = e.command_id WHERE e.type = 'PRIVATE_STATUS_CHANGED' AND e.ref_id = ?"
                        " AND c.kind = 'private.set_status' LIMIT 1", (company_id,)).fetchone() is not None


def sync_master(tx, services, ctx: Optional[TxContext] = None) -> dict:
    """MASTER SYNC (module docstring) on ``tx``: new file rows (``InstrumentMaster._seed_private``, INSERT OR IGNORE), forward
    status moves, missing listing / acquisition fields, seed notes, then the listed tickers in the instrument master.  With
    a command context a status move is an event (a move to PUBLIC also one INFO attention) and a problem is a WARN attention
    item; without one (the housekeeping hook) they are only returned."""
    conn = tx.conn
    ts = int(tx.now.timestamp())
    today = ny_of(ts).date()
    rows, sha = master_file(strict=True)
    master = services.instruments
    out: Dict[str, Any] = {"file_sha256": sha, "new_rows": 0, "status_changes": [], "filled": [], "notes": [], "listed_instruments": [], "problems": []}
    if rows:
        out["new_rows"] = int(master._seed_private(conn, ts))
    for cid in sorted(rows):
        fr = rows[cid]
        row = company_row(conn, cid)
        if row is None:
            continue
        spec = _spec_json(conn, row["instrument_id"])
        if spec.get("recorded_by") == "owner":
            continue                                                     # an owner-added company: the file never touches it
        dstat = str(row["status"] or "PRIVATE").upper()
        fstat = str(fr.get("status") or "PRIVATE").upper()
        fticker = str(fr.get("ticker") or "").strip().upper() or None
        upd: Dict[str, Any] = {}
        moved = None
        if STATUS_RANK.get(fstat, -1) > STATUS_RANK.get(dstat, 0) and not _owner_set_status(conn, cid):
            ticker = row["ticker"] or fticker
            if fstat == "PUBLIC" and not ticker:
                out["problems"].append({"company_id": cid, "problem": f"PUBLIC in the master file without a ticker: the status stays {dstat}"})
            else:
                conn.execute("UPDATE private_companies SET status=?, ticker=?, updated_ts=? WHERE company_id=?", (fstat, ticker, ts, cid))
                upd["status"] = fstat
                if ticker != row["ticker"]:
                    upd["ticker"] = ticker
                moved = {"company_id": cid, "from": dstat, "to": fstat, "ticker": ticker,
                         "source_url": fr.get("listing_source_url") if fstat == "PUBLIC" else fr.get("status_source_url")}
                out["status_changes"].append(moved)
                row = company_row(conn, cid)
        if str(row["status"]).upper() == "PUBLIC" and row["ticker"] and fticker == str(row["ticker"]).upper():
            code, short, full = venue(fr.get("exchange"))
            for k, val in (("ticker", row["ticker"]), ("listed_on", fr.get("listed_on")), ("exchange", short), ("exchange_full", full),
                           ("exchange_code", code), ("listing_source_url", fr.get("listing_source_url"))):
                if val not in (None, "") and spec.get(k) in (None, ""):
                    upd[k] = val
        acq = fr.get("acquired")
        if isinstance(acq, dict) and acq.get("on") and not isinstance(spec.get("acquired"), dict):
            upd["acquired"] = {k: acq[k] for k in ("by", "acquirer_ticker", "on", "source_url") if acq.get(k) not in (None, "")}
        if upd:
            spec.update(upd)
            conn.execute("UPDATE instruments SET spec_json=?, updated_ts=? WHERE instrument_id=?", (json.dumps(spec), ts, row["instrument_id"]))
            master.refresh(row["instrument_id"], conn)
            out["filled"].append({"company_id": cid, "fields": sorted(upd)})
        notes = fr.get("notes")
        if isinstance(notes, str) and notes and row["notes"] != notes:
            conn.execute("UPDATE private_companies SET notes=? WHERE company_id=?", (notes, cid))
            out["notes"].append(cid)
        if moved is not None and ctx is not None:
            ctx.event("PRIVATE_STATUS_CHANGED", instrument_id=row["instrument_id"], ref_type="company", ref_id=cid, status=moved["to"], ticker=moved["ticker"],
                      listed_on=spec.get("listed_on"), source="private_companies.json", source_url=moved["source_url"])
            if moved["to"] == "PUBLIC":
                lst = listing_of(row, spec, latest_round(conn, cid), today, fr)
                src = moved["source_url"] or "not recorded"
                ctx.attention("INFO", "private", "PRIVATE_COMPANY_LISTED", f"{row['name']}: {lst.message() if lst is not None else 'listed'} (researched master; source {src})",
                              {"ref_type": "company", "ref_id": cid, "ticker": moved["ticker"], "source_url": moved["source_url"]})
    for row in conn.execute("SELECT * FROM private_companies WHERE status='PUBLIC' AND ticker IS NOT NULL ORDER BY company_id").fetchall():
        lst = listing_of(row, _spec_json(conn, row["instrument_id"]), latest_round(conn, row["company_id"]), today, rows.get(row["company_id"]))
        res = ensure_listed_instrument(tx, services, lst.ticker, row["name"], lst.exchange_code, lst.exchange_full)
        res["company_id"] = row["company_id"]
        out["listed_instruments"].append(res)
        if res.get("problem"):
            out["problems"].append({"company_id": row["company_id"], "problem": res["problem"]})
    if ctx is not None:
        for p in out["problems"]:
            ctx.attention("WARN", "private", "PRIVATE_MASTER_SYNC", f"{p['company_id']}: {p['problem']}", {"ref_type": "company", "ref_id": p["company_id"]})
    master._loaded = False
    return out


def h_sync_master(ctx: TxContext, payload: dict) -> dict:
    """``private.sync_master {file_sha256?}`` (light): the MASTER SYNC inside a command (events + attention)."""
    return sync_master(ctx.tx, _services(ctx), ctx)


def reseed(tx, services) -> int:
    """HOUSEKEEPING hook (WP-08): the MASTER SYNC on the job's transaction (outside a command nothing is evented; status moves
    and problems are logged) - INSERT OR IGNORE, so owner-recorded rounds and owner edits are never overwritten.  Returns the
    number of new instrument rows the file added."""
    out = sync_master(tx, services, None)
    log = getattr(services, "log", None)
    if log is not None and (out["status_changes"] or out["problems"]):
        log.info("private.reseed: status changes %s; problems %s", out["status_changes"], out["problems"])
    refresh_pins(services, tx.conn)
    return int(out["new_rows"])


def bind(services) -> dict:
    """Startup hook (``server._bind_optional`` calls ``bind(rt)`` after ``register``): run ``private.sync_master`` once per
    master-file version (request id ``private:sync:<SYNC_VERSION>:<sha256[:16]>``; a replay while the file is unchanged),
    reload the instrument cache, then pin the listed tickers of held pre-listing lots.  A failed sync is the bus's loud
    failure (500, ERROR attention COMMAND_FAILED, logged) and is logged here again; the start goes on."""
    out: Dict[str, Any] = {"sync": None, "pins": []}
    bus = getattr(services, "bus", None)
    if bus is None or getattr(services, "db", None) is None or getattr(services, "instruments", None) is None:
        return out
    if SYNC_KIND not in bus.kinds():
        register(bus)
    sha = master_file()[1]
    rid = f"private:sync:{SYNC_VERSION}:{(sha or 'nofile')[:16]}"
    res = bus.execute(Command(SYNC_KIND, {"file_sha256": sha}, rid, "init"))
    out["sync"] = {"ok": res.ok, "replayed": res.replayed, "http": res.http, "code": res.code, "message": res.message, "request_id": rid,
                   "result": res.body if res.ok else None}
    log = getattr(services, "log", None)
    if not res.ok and log is not None:
        log.error("private.sync_master %s failed: %s %s", rid, res.code, res.message)
    services.instruments.load()
    out["pins"] = refresh_pins(services)
    return out


def register(bus) -> None:
    bus.register("private.add_company", h_add_company, light=True)
    bus.register("private.record_round", h_record_round, light=True)
    bus.register("private.set_status", h_set_status, light=True)
    bus.register(SYNC_KIND, h_sync_master, light=True)
    bus.register("private.trade", h_trade)


__all__ = ["SIDES", "KINDS", "STATUSES", "LOCKUP_TEXT", "LISTED_CODE", "ACQUIRED_CODE", "LISTED_PIN", "SYNC_KIND", "SYNC_VERSION", "Listing", "slug_id",
           "defaults", "master_file", "venue", "listing_of", "company_row", "rounds", "latest_round", "round_view", "private_mark", "company_view",
           "companies", "listed_companies", "companies_view", "company", "Validated", "fifo_preview", "validate", "preview", "h_trade", "h_record_round",
           "h_add_company", "h_set_status", "h_sync_master", "ensure_listed_instrument", "listed_held_tickers", "refresh_pins", "sync_master", "reseed",
           "bind", "register"]
