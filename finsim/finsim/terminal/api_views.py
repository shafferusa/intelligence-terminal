"""The account, portfolio, treasury, accounting, activity, books and history routes of the API (TERMINAL_DESIGN.md §15; WP-11).

Companion of ``api.py`` (same package rules): every read here takes ONE mark snapshot per request and a pooled reader,
never the writer lock and never the network; the mutating routes build one command each.  Shapes follow §15 and the
WP-12 fixtures (``tests/fixtures/terminal/api``): account rows carry ``nav`` / ``available_usd`` / the three counts;
transactions expand every journal line with ``debit`` / ``credit`` and the GL name; CSV exports use the fixture columns
(``entry_id,ts_ny,business_date,kind,memo,instrument_id,qty,price,gross,fee,net_<CCY>``; positions
``account_id,instrument_id,qty,cost,mark,quality,mv,unrealised``).  History routes serve the same read models under
the archived scope ``{lifetime}:ALL`` with ``read_only: true`` and the lifetime's final figures; marks come from the
stored final snapshot (``Accounting`` does that for CLOSED lifetimes).

Money and rate units on the wire (display-units fix, 2026-10-21)
- Every money field is a decimal string in MAJOR units ("1000000.00"), the convention of ``nav`` / ``capital`` /
  ``available``.  The codec (``api.encode``) converts ``*_c`` keys; a dict of cents keyed by currency / account / lifetime
  (``TreasuryView.cash_by_ccy``, ``NavView.by_account``, ``LiquidityView.detail``, ``ObligationsView.pending_in``, balance-
  sheet ``native``) has no ``_c`` key and is converted HERE (``money_map`` and the ``*_out`` builders); a minor-unit
  integer never reaches a response of this module, and no ``*_c`` duplicate of a converted field is emitted.
- Rates: ``rate`` / ``spread`` / ``floor`` are FRACTIONS (0.0337 = 3.37 %); an observation as published is a PERCENT named
  ``*_pct`` (``index_pct``); ``quotes.rates`` module docstring UNITS is the one place the convention is defined.
- ``available`` follows ``accounting.AVAILABLE_TEXT``: the account's own spendable cash floored at zero; what working
  orders and payables need beyond it is ``reserved`` with ``sweep_covers`` (moved from Treasury at the fill or the
  settlement), ``unfunded`` and ``overdrawn`` beside it — never a negative available.
- ``buying_power`` (per currency) follows ``accounting.BUYING_POWER_TEXT``: what a NEW order can draw = ``available`` +
  ``treasury_available`` + ``pb_headroom``, where those two are what Treasury / the PB facility still have after the
  account's ``committed`` (its working orders' and payables' needs beyond its own cash; ``treasury_gross`` /
  ``pb_headroom_gross`` before it) — the same three figures the ticket preview's fundability reads.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from . import books as _books, config, tables
from .accounting import AVAILABLE_TEXT, ScopeError
from .api import (ApiError, NotBuilt, NotFound, PACKAGES, Request, encode, mark_dict, need, ok_or_raise, quote_dict, run_command, OPEN_ORDER_STATUSES)
from .clock import accounting_day, ny_of
from .instruments import EQUITY_KINDS
from .ledger import CHART
from .attention import attention_lifetime
from .quotes.rates import UNIT_FRACTION, fraction_to_pct, treasury_cash_rate
from .units import fmt_money, from_c, fx_c, to_c

HISTORY_PATHS = ("positions", "portfolio", "accounting/balance-sheet", "accounting/pnl", "accounting/trial-balance", "accounting/journal", "accounting/integrity",
                 "accounting/transactions", "accounting/cash", "activity", "export/transactions.csv", "export/positions.csv")
_ACCOUNT_RE = re.compile(config.ACCOUNT_ID_RE)
_LIFETIME_RE = re.compile(r"^[A-Z]+[0-9]+$")


# ---- names, books and shares (P03: every row says whose it is in words) ---------------------------------------------------------
def name_maps(rt) -> Tuple[Dict[str, str], Dict[str, str]]:
    """``({account_id: account name}, {book_code: book name})`` - every account of every lifetime, every book."""
    with rt.db.reader() as conn:
        accts = {r[0]: r[1] for r in conn.execute("SELECT account_id, name FROM accounts").fetchall()}
        bks = {b.code: b.name for b in _books.books(conn)}
    return accts, bks


def lifetime_of(account_id: Optional[str]) -> Optional[str]:
    """``"P1:US_EXCHANGE" -> "P1"``; None for anything that is not an account id."""
    if not account_id or not _ACCOUNT_RE.match(str(account_id)):
        return None
    return str(account_id).split(":", 1)[0]


def book_fields(account_id: Optional[str], books: Dict[str, str]) -> dict:
    """``{lifetime_id, book_code, book_name}`` of an account id (all None for a non-account)."""
    lid = lifetime_of(account_id)
    code = _books.book_code_of(lid) if lid else None
    return {"lifetime_id": lid, "book_code": code, "book_name": books.get(code, code) if code else None}


def share(part_c, whole_c) -> Optional[Decimal]:
    """``part / whole`` as a 4-dp fraction (0.1294 = 12.94 %); None when either is missing or the whole is not positive (a share
    of a zero or negative NAV is not a weight)."""
    if part_c is None or whole_c is None or int(whole_c) <= 0:
        return None
    return (Decimal(int(part_c)) / Decimal(int(whole_c))).quantize(Decimal("0.0001"))


def scope_kind(scope: str) -> str:
    """ALL_BOOKS | LIFETIME (``ALL`` / ``{lifetime}:ALL``) | TREASURY (a Treasury account) | ACCOUNT."""
    s = str(scope or "")
    if s == "ALL_BOOKS":
        return "ALL_BOOKS"
    if s == "ALL" or s.endswith(":ALL"):
        return "LIFETIME"
    return "TREASURY" if s.endswith(":TREASURY") else "ACCOUNT"


WEIGHT_BASIS = {"ALL_BOOKS": "share of the NAV of all books", "LIFETIME": "share of the book's NAV", "ACCOUNT": "share of the account's NAV",
                "TREASURY": "share of the account's NAV"}


def fx_rate(rt, snap, ccy: str) -> Optional[Decimal]:
    """USD per unit of ``ccy`` from the mark snapshot (``Accounting._fx_rate``: 1 for USD, None when no usable rate)."""
    if ccy == "USD":
        return Decimal(1)
    return rt.accounting._fx_rate(snap, ccy)


# ---- money on the wire (module docstring) ---------------------------------------------------------------------------------
def money(c) -> Optional[str]:
    """Integer cents -> the API's decimal string in major units (``100000000`` -> ``"1000000.00"``); None stays None."""
    return None if c is None else str(from_c(int(c)))


def money_map(d: Optional[dict]) -> Dict[str, Optional[str]]:
    """``{key: cents}`` (a currency, account or lifetime key) -> ``{key: "major.minor"}``."""
    return {str(k): money(v) for k, v in (d or {}).items()}


def nav_out(nav) -> dict:
    """``NavView`` for JSON: the ``*_c`` fields go through the codec; ``by_account`` (cents per account) is converted here."""
    d = nav.to_dict()
    d["by_account"] = money_map(d.get("by_account"))
    return d


def liquidity_out(lv) -> dict:
    """``LiquidityView`` for JSON: ``detail`` (cents per account / lifetime / futures position) converted here."""
    d = lv.to_dict()
    d["detail"] = {k: money_map(v) if isinstance(v, dict) else v for k, v in (d.get("detail") or {}).items()}
    return d


def treasury_out(tv) -> dict:
    """``TreasuryView`` for JSON: ``cash_by_ccy`` / ``capital_by_ccy`` / ``advances_by_account`` (cents) converted here."""
    d = tv.to_dict()
    for k in ("cash_by_ccy", "capital_by_ccy", "advances_by_account"):
        d[k] = money_map(d.get(k))
    return d


def balance_sheet_out(bs, kind: Optional[str] = None) -> dict:
    """``BalanceSheet`` for JSON in the §15 / WP-12 row shape: one row per (line, currency) with ``gl`` ("1200/1250"),
    ``currency``, ``native`` and ``usd`` as decimal strings.  A computed line (unrealised, open trade equity, swaps at
    market) has no native amounts: it is a USD row.  A line held in several currencies: the USD part converts at 1 and a
    single foreign currency takes the rest of the line's base, both exact; two or more foreign currencies cannot be split
    exactly from the line total, so their ``usd`` is null and a "(total, USD)" sub-row carries the line's base.  The memo
    gains ``fx_clearing`` and ``ties`` (NAV = capital + advances + retained, to the cent)."""
    d = bs.to_dict()
    rows: List[dict] = []
    for r in d.get("rows") or []:
        native = {str(c): int(v) for c, v in (r.get("native") or {}).items() if v}
        base = int(r.get("base_c") or 0)
        key = str(r.get("key") or "")
        common = {"section": r.get("section"), "label": r.get("label"), "key": key, "gl": key.replace(",", "/") if key[:1].isdigit() else "", "sub": bool(r.get("sub"))}
        if not native:
            rows.append(dict(common, currency="USD", native=money(base), usd=money(base)))
            continue
        ccys = sorted(native, key=lambda c: (c != "USD", c))
        if len(ccys) == 1:
            rows.append(dict(common, currency=ccys[0], native=money(native[ccys[0]]), usd=money(base)))
            continue
        foreign = [c for c in ccys if c != "USD"]
        exact = len(foreign) == 1
        for c in ccys:
            usd = native[c] if c == "USD" else (base - native.get("USD", 0) if exact else None)
            rows.append(dict(common, currency=c, native=money(native[c]), usd=money(usd)))
        if not exact:
            rows.append(dict(common, label=f"{r.get('label')} (total, USD)", currency="USD", native=None, usd=money(base), sub=True))
    d["rows"] = rows
    memo = dict(d.get("memo") or {})
    memo["fx_clearing_c"] = int(memo.get("fx_clearing_base_c") or 0)
    memo["ties"] = int(bs.nav_c) == int(memo.get("capital_c") or 0) + int(memo.get("advances_c") or 0) + int(memo.get("retained_c") or 0)
    memo.update(advances_convention(kind or scope_kind(bs.scope), int(memo.get("advances_c") or 0)))
    d["memo"] = memo
    return d


def advances_convention(kind: str, advances_c: int) -> dict:
    """BM-29: ONE way of saying the Treasury's internal advances, on the Treasury page and the balance sheet.  The ledger
    keeps them in 3100 (``advances_c`` = the scope's natural 3100 balance in base: negative for the Treasury that advanced
    cash, positive for the account it funded, zero over a whole lifetime - L5).  Shown as a POSITIVE amount with the word
    that says its direction, and the identity written with the sign that makes it add up:
    Treasury scope  'advanced to accounts 100,000.00'   NAV = capital + retained - advanced to accounts
    account scope   'funded by Treasury 100,000.00'     NAV = capital + funded by Treasury + retained
    a lifetime / all books  'internal advances (net) 0.00'  NAV = capital + internal advances (net, zero across a lifetime) + retained."""
    if kind == "TREASURY":
        return {"advances_label": "advanced to accounts", "advances_shown_c": -advances_c,
                "identity_text": "NAV = capital + retained - advanced to accounts"}
    if kind == "ACCOUNT":
        return {"advances_label": "funded by Treasury", "advances_shown_c": advances_c,
                "identity_text": "NAV = capital + funded by Treasury + retained"}
    return {"advances_label": "internal advances (net)", "advances_shown_c": advances_c,
            "identity_text": "NAV = capital + internal advances (net, zero across a lifetime) + retained"}


def treasury_interest(rt, tv) -> dict:
    """The Treasury cash-interest block (sentence CASH_INT; ``quotes.rates`` UNITS): ``index_pct`` = the latest SOFR print
    as published (a percent), ``spread`` / ``floor`` / ``rate`` = FRACTIONS, ``rate = max(floor, SOFR / 100 + spread)``
    computed by ``rates.treasury_cash_rate`` — the arithmetic of the nightly accrual (SOFR 3.87 -> 0.0337; one night on
    1,000,000.00 ACT/360 = 93.61).  No SOFR print -> every rate is null and the reason is said (never defaulted)."""
    spec = tables.RATES["treasury_cash"]
    spread = Decimal(str(spec["spread"]))
    rates = getattr(rt, "rates", None)
    got = rates.sofr() if rates is not None and hasattr(rates, "sofr") else None
    formula = (f"rate = max({spec['floor']}, SOFR / 100 {'-' if spread < 0 else '+'} {abs(spread)}) on settled USD at the 17:00 ET close, "
               f"ACT/{int(spec['basis'])}, accrued nightly, paid monthly")
    out = {"index": "SOFR", "currency": "USD", "unit": UNIT_FRACTION, "spread": str(spread), "floor": str(spec["floor"]), "basis": int(spec["basis"]),
           "accrued_c": int(tv.accrued_c), "formula": formula,
           "units": "rate, spread and floor are fractions (0.0337 = 3.37 %); index_pct is SOFR as published, a percent",
           "note": tables.CONVENTIONS.get("CASH_INT", "")}
    if got is None:
        out.update(index_pct=None, as_of=None, rate=None, rate_pct=None,
                   status="SOFR has not been loaded from FRED yet: no rate is shown and no night accrues until it is (RATE_LAG; never defaulted)")
        return out
    rate = treasury_cash_rate(got[1])
    out.update(index_pct=str(got[1]), as_of=got[0].isoformat(), rate=str(rate), rate_pct=str(fraction_to_pct(rate)), status="ok")
    return out


# ---- shared builders --------------------------------------------------------------------------------------------------------
def account_rows(rt, lid: str, snap=None) -> List[dict]:
    """§15 ``GET /api/accounts`` rows for one lifetime: the accounts table + nav, the liquidity line in USD and the three
    counts.  ``available_usd`` is the account's own spendable cash floored at zero (``accounting.AVAILABLE_TEXT``);
    ``reserved_usd`` the working orders' reservation; ``sweep_covers_usd`` what the Treasury sweep moves at the fill /
    settlement because own cash does not cover it; ``unfunded_usd`` the part Treasury cannot cover; ``overdrawn_usd``
    settled cash below zero (an open funding shortfall)."""
    snap = snap if snap is not None else rt.marks.snapshot()
    with rt.db.reader() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (lid,)).fetchall()]
        q = ",".join("?" for _ in OPEN_ORDER_STATUSES)
        wo = {r[0]: int(r[1]) for r in conn.execute(f"SELECT account_id, COUNT(*) FROM orders WHERE status IN ({q}) GROUP BY account_id", OPEN_ORDER_STATUSES).fetchall()}
        ps = {r[0]: int(r[1]) for r in conn.execute("SELECT account_id, COUNT(*) FROM settlements WHERE status='PENDING' GROUP BY account_id").fetchall()}
        at = {r[0]: int(r[1]) for r in conn.execute("SELECT account_id, COUNT(*) FROM attention WHERE resolved_ts IS NULL AND account_id IS NOT NULL GROUP BY account_id").fetchall()}
        ccys: Dict[str, set] = {}
        for r in conn.execute("SELECT DISTINCT g.account_id, g.currency FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id WHERE a.lifetime_id=?", (lid,)).fetchall():
            ccys.setdefault(r[0], set()).add(r[1])
        bname = (lambda b: b.name if b else None)(_books.book_of(conn, _books.book_code_of(lid)))
    nav = rt.accounting.nav(f"{lid}:ALL", snapshot=snap)
    out = []
    for r in rows:
        aid = r["account_id"]
        try:
            r["settings"] = json.loads(r.pop("settings_json") or "{}")
        except ValueError:
            r["settings"] = {}
        r["nav_c"] = int(nav.by_account.get(aid, 0))
        av = rt.accounting.available_view(aid, "USD", snapshot=snap)
        r["available_usd_c"] = int(av.available_c)
        r["reserved_usd_c"] = int(av.reserved_c)
        r["needs_usd_c"] = int(av.needs_c)
        r["sweep_covers_usd_c"] = int(av.sweep_covers_c)
        r["unfunded_usd_c"] = int(av.unfunded_c)
        r["overdrawn_usd_c"] = int(av.overdrawn_c)
        r.update(available_by_currency(rt, aid, ccys.get(aid, set()), int(av.available_c), snap))
        r["working_orders"] = wo.get(aid, 0)
        r["pending_settlements"] = ps.get(aid, 0)
        r["attention_count"] = at.get(aid, 0)
        r["nav_quality"] = nav.quality
        r["book_name"] = bname
        out.append(r)
    return out


def available_by_currency(rt, account_id: str, currencies, usd_available_c: int, snap) -> dict:
    """C24 / BM-30: the account's own available cash in every currency it holds (``available_c`` floored at zero, the
    sidebar's 'avail USD x . EUR y') and the sum in USD at the snapshot's FX (``available_base``).  A currency whose USD
    rate is not loaded leaves the base figure null and names it (``available_base_missing``) - never a guessed rate."""
    by: Dict[str, int] = {"USD": int(usd_available_c)}
    for ccy in sorted(set(currencies or ()) - {"USD"}):
        by[ccy] = int(rt.accounting.available(account_id, ccy, snapshot=snap))
    base, missing = 0, []
    for ccy, v in by.items():
        rate = fx_rate(rt, snap, ccy)
        if rate is None:
            if v:
                missing.append(ccy)
            continue
        base += fx_c(v, rate)
    return {"available_by_ccy": money_map(by), "available_base_c": None if missing else base, "available_base_missing": missing}


def account_row(rt, account_id: str, snap=None) -> dict:
    with rt.db.reader() as conn:
        row = conn.execute("SELECT lifetime_id FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    if row is None:
        raise NotFound(f"account {account_id} does not exist")
    for r in account_rows(rt, row["lifetime_id"], snap):
        if r["account_id"] == account_id:
            return r
    raise NotFound(f"account {account_id} does not exist")


def _account_or_404(rt, account_id: str) -> dict:
    with rt.db.reader() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    if row is None:
        raise NotFound(f"account {account_id} does not exist")
    return dict(row)


def _symbol(rt, iid: Optional[str]) -> Optional[str]:
    inst = rt.instruments.get(iid) if iid else None
    return inst.symbol if inst is not None else iid


def order_full(rt, conn, order_id: str, with_events: bool = False) -> dict:
    """§15 ``OrderView``: the row, ``remaining``, fills, last event (+ events and the fills' journal entries on detail)."""
    from . import orders as _orders
    if conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone() is None:
        raise NotFound(f"order {order_id} does not exist")
    d = _orders.order_view(conn, order_id)
    d["symbol"] = _symbol(rt, d.get("instrument_id"))
    with_source(conn, "order", d)                              # 15.7 item 7: the as-of badge and label of a booked-as-of order
    for f in d.get("fills") or []:
        if isinstance(f, dict):
            with_source(conn, "fill", f)
    if with_events:
        d["events"] = [dict(e) for e in conn.execute("SELECT ts, from_status, to_status, note FROM order_events WHERE order_id=? ORDER BY id", (order_id,)).fetchall()]
        fids = [f["fill_id"] for f in d.get("fills", [])]
        d["entries"] = []
        for fid in fids:
            for e in conn.execute("SELECT entry_id, kind, memo, ts, business_date FROM journal_entries WHERE ref_type='fill' AND ref_id=?", (fid,)).fetchall():
                d["entries"].append(dict(e))
    return d


SIMULATED_SOURCE = {"source": "simulated", "label": "Simulated fill", "asof": False, "entered_ts": None, "batch_id": None, "booking": None, "badge": None}


def source_of(conn, kind: str, row, cache: Optional[dict] = None) -> dict:
    """15.7 item 7: ``{source, label, asof, entered_ts, batch_id, booking, badge}`` of an order / fill / OTC contract row from P2's
    ``blotters.source_label`` (the D1 as-of labels, "Historical model (not an independent execution)", "System (...)", "Simulated fill"),
    imported lazily; without that module (or on its failure) a row reads "Simulated fill" with its stored ``booking``."""
    try:
        from . import blotters as _bl                       # optional module: imported where needed, never at module level (15.2 step 3)
        fn = getattr(_bl, "source_label", None)
        if callable(fn):
            return dict(fn(conn, kind, row, cache))
    except Exception:   # noqa: BLE001 - the plain label below; a page never breaks on the label
        pass
    out = dict(SIMULATED_SOURCE)
    try:
        out["booking"] = row["booking"]
    except (KeyError, IndexError, TypeError):
        pass
    return out


def source_fields(src: dict) -> dict:
    """The flat fields P2's executions rows carry (``blotters``): ``source, source_label, asof, badge, batch_id`` (+ ``booking``)."""
    return {"source": src.get("source"), "source_label": src.get("label"), "asof": bool(src.get("asof")), "badge": src.get("badge"),
            "batch_id": src.get("batch_id"), "booking": src.get("booking")}


def with_source(conn, kind: str, d: dict, cache: Optional[dict] = None) -> dict:
    """``d`` with the source fields of P2's executions rows - ``booking``, ``source`` / ``source_label`` (trader wording: "Simulated fill",
    or the D1 as-of label), ``asof``, ``badge`` (AS-OF / MODEL / None), ``batch_id`` and the ``entered_ts`` of an as-of row - what
    orders.js / positions.js / activity.js show as the ``.badge-asof`` badge (15.7 item 7)."""
    src = source_of(conn, kind, d, cache)
    booking = d.get("booking")
    d.update(source_fields(src))
    d["booking"] = booking or src.get("booking")
    if d.get("entered_ts") is None:
        d["entered_ts"] = src.get("entered_ts")
    return d


def lender_text(name) -> str:
    """C16 / 15.7 item 8: a stored borrow lender as shown - ``lending.lender_label`` ("street lender (modelled)" -> "street lender (assumed
    terms)"); the stored rows are never rewritten."""
    try:
        from . import lending as _lending
        fn = getattr(_lending, "lender_label", None)
        if callable(fn):
            return fn(name)
    except Exception:   # noqa: BLE001 - the same rule, here
        pass
    s = "" if name is None else str(name)
    for old in ("modelled)", "modeled)"):
        if s.endswith(old):
            return s[: -len(old)] + "assumed terms)"
    return s


def borrows_shown(rows: List[dict]) -> List[dict]:
    """Borrow rows with the lender worded for the page (``lender_text``) and the stored text kept in ``lender_stored``."""
    for r in rows:
        if "lender" in r:
            r["lender_stored"] = r.get("lender")
            r["lender"] = lender_text(r.get("lender"))
    return rows


def fill_rows(rt, conn, where: str, params: list, now: int, limit: int = 500) -> List[dict]:
    out = []
    cache: dict = {}
    for r in conn.execute(f"SELECT * FROM fills WHERE {where} ORDER BY ts DESC, fill_id DESC LIMIT ?", params + [int(limit)]).fetchall():
        d = dict(r)
        raw = d.pop("quote_json", None)
        try:
            qd = json.loads(raw) if raw else None
        except ValueError:
            qd = None
        if isinstance(qd, dict) and qd:
            qt = qd.get("quote_time")
            qd["age_s"] = None if qt is None else max(0, now - int(qt))
        d["quote"] = qd or None
        d["symbol"] = _symbol(rt, d.get("instrument_id"))
        out.append(with_source(conn, "fill", d, cache))
    return out


_FUT_IM_RE = re.compile(r"initial margin [+-]?[0-9][0-9,]*\.[0-9]{2} \(([^)]*)\)")

# ---- V104R1-04 (1.0.4 fixer): trader wording for the link ids, account ids, codes and convention keys inside the texts a page shows ----
LINK_WORDS = {"fill": "fill", "otc": "OTC contract", "swapvm": "swap variation margin", "vm": "variation margin", "swappay": "swap payment",
              "cdxcpn": "CDX coupon", "trsreset": "TRS reset", "trsdiv": "TRS dividend", "xccy": "cross-currency swap", "xccypay": "cross-currency swap payment",
              "xccyfx": "cross-currency swap FX reset", "pay": "monthly charge", "pbmin": "prime-broker monthly minimum", "pb": "PB interest",
              "borrow": "stock borrow", "sec_loan": "securities loan", "repo": "repo", "settlement": "settlement", "loan": "loan", "private_loan": "private loan",
              "fx_deal": "FX deal", "coupon": "coupon", "div": "dividend", "divpay": "dividend payment", "expiry": "expiry", "final": "final settlement"}
LINK_STEPS = {"open": "at booking", "terminate": "termination", "start": "start", "end": "maturity", "close": "close"}
REF_WORDS = {"otc": "contract", "fx_deal": "deal", "sec_loan": "securities loan", "private_loan": "private loan", "borrow": "borrow"}
CODE_ACRONYMS = {"FX", "OTC", "CDX", "PB", "IM", "VM", "TRS", "IRS", "XCCY", "FIFO", "SOFR", "NAV", "US", "USD", "ECP", "QP", "ADV", "DV01", "CS01", "ETF",
                 "GC", "HTB", "CSA", "ISDA", "PBA", "SHO", "GTC", "AON", "MKT", "LMT", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "OCC", "CME", "PDT", "API"}
CODE_WORDS = {"ACCT_DAY": "accounting day", "FUT_MARGIN": "futures margin", "OPT_MARGIN": "option margin", "EXCESS_LIQ": "excess liquidity",
              "BUY_CCY": "currency bought", "SELL_CCY": "currency sold", "PB_ONBOARDED": "prime broker onboarded", "OTC_BOOKED": "OTC contract booked",
              "FX_DEALT": "FX deal", "FX_FORWARD_DEALT": "FX forward dealt", "SWEEP_IN": "sweep-in", "SWEEP_OUT": "sweep-out", "OTC_IM": "OTC initial margin",
              "OTC_VM": "OTC variation margin", "OTC_TERMINATE": "OTC termination"}
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
_ACCT_IN_TEXT = re.compile(r"\b[A-Z]{1,3}[0-9]+:[A-Z][A-Z0-9_]*[A-Z0-9]\b")
_LINK_TOKEN = re.compile(r"[A-Z]{1,3}[0-9]+:[A-Z][A-Z0-9_]*[A-Z0-9]|[^:]+")
_LINK_PHRASE = re.compile(r"\bfor ([a-z][a-z_]*) ([A-Za-z0-9][A-Za-z0-9_.:\-]*[A-Za-z0-9])")
_CONV_GROUP = re.compile(r"( ?)\(([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?:, ?[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)*)\)")
#: section names of a module's docstring some memos cite beside the conventions ('(EXECUTION, ACCRUED, COST_BASIS)' of a bond fill): not
#: shown on any page, so a page drops them and keeps the conventions it can show
DOC_KEYS = frozenset({"EXECUTION", "ACCRUED", "FILL"})


def code_words(code) -> str:
    """An internal code in words ('OTC_IM' -> 'OTC initial margin', 'PAY_FIXED' -> 'pay fixed'); any other text as given."""
    c = str(code if code is not None else "")
    if not _CODE_RE.match(c):
        return c
    if c in CODE_WORDS:
        return CODE_WORDS[c]
    return " ".join(w if w in CODE_ACRONYMS else w.lower() for w in c.split("_"))


def conv_title(key: str) -> str:
    """A convention key as a title ('SWAP_MARK' -> 'Swap mark', 'FX_SPOT' -> 'FX spot'): the same rule as T.convTitle."""
    w = code_words(key)
    return w[:1].upper() + w[1:]


def link_words(linked_type, linked_id, names: Optional[Dict[str, str]] = None) -> str:
    """What a link points at, in words: ('otc', 'im:SWP-000002:open') -> 'OTC initial margin SWP-000002 (at booking)',
    ('otc', 'otc:SWP-000002:terminate') -> 'OTC contract SWP-000002 (termination)', ('borrow', 'B-000001') -> 'stock borrow B-000001';
    an account id inside it by its name."""
    t = str(linked_type or "")
    word = LINK_WORDS.get(t, t.replace("_", " "))
    toks = _LINK_TOKEN.findall(str(linked_id or ""))
    if toks and toks[0].lower() == t.lower():
        toks = toks[1:]                                           # an idempotency key leads with its own kind
    if toks and toks[0].lower() in ("im", "vm") and t == "otc":
        word = "OTC initial margin" if toks[0].lower() == "im" else "OTC variation margin"
        toks = toks[1:]
    elif toks and toks[0] in LINK_WORDS and toks[0] != t:
        word = LINK_WORDS[toks[0]]                                # 'pay:pbmin:...' -> the prime-broker monthly minimum
        toks = toks[1:]
    ids, steps = [], []
    for x in toks:
        if _ACCT_IN_TEXT.fullmatch(x):
            ids.append((names or {}).get(x, x))
        elif x in LINK_STEPS:
            steps.append(LINK_STEPS[x])
        else:
            ids.append(x)
    return (word + (" " + " ".join(ids) if ids else "") + (f" ({', '.join(steps)})" if steps else "")).strip()


def readable(text, names: Optional[Dict[str, str]] = None) -> str:
    """A ledger / event text a page shows, in trader wording: 'for otc im:SWP-000002:open' -> 'for OTC initial margin SWP-000002
    (at booking)', account ids -> their names ('F1:TREASURY -> F1:DEBT' -> 'Treasury -> Debt Instruments'), a group of convention
    keys '(SWAP_MARK, SWAP_RESET)' -> '(see Swap mark, Swap reset)' (only keys the conventions know).  The stored text is unchanged."""
    s = str(text or "")
    if not s:
        return s
    names = names or {}

    def _link(m):
        if m.group(1) in LINK_WORDS or ":" in m.group(2):
            return "for " + link_words(m.group(1), m.group(2), names)
        return m.group(0)

    def _conv(m):
        keys = [k.strip() for k in m.group(2).split(",")]
        if not all(k in tables.CONVENTIONS or k in DOC_KEYS for k in keys):
            return m.group(0)
        shown = [conv_title(k) for k in keys if k in tables.CONVENTIONS]
        return m.group(1) + "(see " + ", ".join(shown) + ")" if shown else ""
    s = _LINK_PHRASE.sub(_link, s)
    s = _ACCT_IN_TEXT.sub(lambda m: names.get(m.group(0), m.group(0)), s)
    return _CONV_GROUP.sub(_conv, s)


TRANSFER_WORDS = {"INITIAL_CAPITAL": "initial capital", "DEPOSIT": "capital deposit", "WITHDRAWAL": "capital withdrawal", "TRANSFER": "internal transfer",
                  "SWEEP_IN": "sweep-in", "SWEEP_OUT": "sweep-out"}


def clean_memo(memo: Optional[str]) -> str:
    """BM-24: a default memo the handler appended after its own generated text ('capital deposit USD 1,000.00 to P1:TREASURY:
    capital deposit') is shown once; any other memo stays as stored."""
    m = re.match(r"^(.*): ([^:]+)$", memo or "")
    if m and m.group(1).strip().lower().startswith(m.group(2).strip().lower()):
        return m.group(1)
    return memo or ""


def _counterparty_name(rt, conn, cid: Optional[str]) -> Optional[str]:
    """A counterparty id of the directory (``BANK_JPM``, ``PD_GS``, ``PB_GS``) -> its name; the id itself when the directory
    does not know it (or is not built)."""
    if not cid:
        return None
    try:
        from . import counterparties as _cp
        e = _cp.get(rt, str(cid), conn)
        if e and e.get("name"):
            return str(e["name"])
    except Exception:   # noqa: BLE001 - the directory is optional (P08); the id is shown instead
        pass
    return str(cid)


def _ref_party(rt, conn, ref_type: Optional[str], ref_id: Optional[str]) -> Optional[dict]:
    """The other party of a ledger entry's reference (BM-24): a transfer's two accounts, a loan's lender, a borrow's lender,
    a repo's / securities loan's counterparty, an OTC contract's dealer, a private loan's borrower.  None when unknown."""
    if not ref_type or not ref_id:
        return None
    try:
        if ref_type == "transfer":
            r = conn.execute("SELECT kind, from_account, to_account FROM transfers WHERE transfer_id=?", (ref_id,)).fetchone()
            return {"transfer": dict(r)} if r else None
        if ref_type == "loan":
            r = conn.execute("SELECT * FROM loans WHERE loan_id=?", (ref_id,)).fetchone()
            return {"party": r["lender"], "role": "lender"} if r else None
        if ref_type == "borrow":
            r = conn.execute("SELECT lender FROM borrows WHERE borrow_id=?", (ref_id,)).fetchone()
            return {"party": lender_text(r["lender"]), "role": "lender"} if r else None
        if ref_type == "repo":
            r = conn.execute("SELECT * FROM repos WHERE repo_id=?", (ref_id,)).fetchone()
            cid = dict(r).get("counterparty") if r else None
            return {"party": _counterparty_name(rt, conn, cid), "role": "repo counterparty"} if cid else None
        if ref_type == "sec_loan":
            r = conn.execute("SELECT counterparty FROM sec_loans WHERE loan_id=?", (ref_id,)).fetchone()
            return {"party": _counterparty_name(rt, conn, r["counterparty"]), "role": "borrower of the shares"} if r else None
        if ref_type == "otc":
            r = conn.execute("SELECT broker FROM otc_contracts WHERE contract_id=?", (ref_id,)).fetchone()
            return {"party": r["broker"], "role": "dealer"} if r else None
        if ref_type == "private_loan":
            r = conn.execute("SELECT borrower FROM private_loans WHERE loan_id=?", (ref_id,)).fetchone()
            return {"party": r["borrower"], "role": "borrower"} if r else None
    except Exception:   # noqa: BLE001 - a table of a module that is not built (sec_loans, a column) leaves the party unknown, never a 500
        return None
    return None


def txn_rows(rt, scope: str, *, from_date=None, to_date=None, kinds=None, instrument_id=None, limit=500, offset=0) -> List[dict]:
    """``Accounting.transactions`` rows with every line expanded (debit / credit, GL name), plus (P03):
    - ``account_names`` of the entry's accounts, ``other_side`` = the other party in words (BM-24): for an account scope the
      entry's other accounts ('from Treasury' / 'to US Exchange'), else the reference's lender / counterparty / dealer;
    - ``memo`` without a repeated default memo (``clean_memo``; ``memo_stored`` keeps the ledger's text when it differs);
    - USX-20: the fill's quantity / price / gross / fee only on the fill's OWN entry (idem ``fill:{fill_id}``); another entry
      of the same fill (the futures initial-margin move ``im:{fill_id}``) carries none and says what it is ('initial margin
      +17,500.00 (0 -> 1 contracts x 17,500.00)')."""
    rows = rt.accounting.transactions(scope, from_date=from_date, to_date=to_date, kinds=kinds, instrument_id=instrument_id, limit=limit, offset=offset)
    names, _bk = name_maps(rt)
    own = scope if _ACCOUNT_RE.match(str(scope or "")) else None
    idem: Dict[int, str] = {}
    parties: Dict[tuple, Optional[dict]] = {}
    with rt.db.reader() as conn:
        ids = [int(t.entry_id) for t in rows]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute(f"SELECT entry_id, idem_key FROM journal_entries WHERE entry_id IN ({','.join('?' for _ in chunk)})", chunk).fetchall():
                idem[int(r[0])] = r[1]
        for t in rows:
            key = (t.ref_type, t.ref_id)
            if key not in parties:
                parties[key] = _ref_party(rt, conn, t.ref_type, t.ref_id)
    out = []
    for t in rows:
        d = t.to_dict()
        d["net_cash"] = money_map(t.net_cash)                                          # cents by currency (not a *_c key): converted here, no cents copy
        lines = []
        for l in d.get("lines", []):
            l = dict(l)
            amt = int(l.get("amount_c") or 0)
            l["debit_c"] = amt if amt > 0 else None
            l["credit_c"] = -amt if amt < 0 else None
            l.setdefault("gl_name", CHART[l["gl"]].name if l.get("gl") in CHART else l.get("gl"))
            l["account_name"] = names.get(l.get("account_id"), l.get("account_id"))
            lines.append(l)
        d["lines"] = lines
        entry_accounts = sorted({l.get("account_id") for l in lines if l.get("account_id")})
        d["entry_accounts"] = entry_accounts
        d["account_names"] = {a: names.get(a, a) for a in entry_accounts}
        memo = readable(clean_memo(t.memo), names)                                   # V104R1-04: names and words, never ids / keys
        if memo != (t.memo or ""):
            d["memo_stored"] = t.memo
        d["memo"] = memo
        d["kind_label"] = code_words(t.kind)
        if t.ref_type == "fill" and t.ref_id and idem.get(int(t.entry_id)) != f"fill:{t.ref_id}":
            # USX-20: not the fill's own entry (the futures initial margin of that fill): no second quantity / price / gross
            d["qty"] = d["price"] = None
            d["gross_c"] = d["fee_c"] = None
            im = sum(int(l.get("amount_c") or 0) for l in lines if l.get("gl") == "1300")
            if im or str(t.kind) == "FUT_MARGIN":
                m = _FUT_IM_RE.search(t.memo or "")
                d["memo"] = f"initial margin {'+' if im >= 0 else '-'}{fmt_money(abs(im))}" + (f" ({m.group(1)})" if m else "") + f" for fill {t.ref_id}"
        party = parties.get((t.ref_type, t.ref_id))
        other = [a for a in entry_accounts if a != own] if own else []
        if party and party.get("transfer"):
            tr = party["transfer"]
            src, dst = tr.get("from_account"), tr.get("to_account")
            if own and own == dst:
                d["other_side"] = f"from {names.get(src, src) if src else 'outside the book (capital)'}"
            elif own and own == src:
                d["other_side"] = f"to {names.get(dst, dst) if dst else 'outside the book (capital)'}"
            else:
                d["other_side"] = f"{names.get(src, src) if src else 'outside the book'} -> {names.get(dst, dst) if dst else 'outside the book'}"
        elif party and party.get("party"):
            d["other_side"] = f"{party['party']} ({party.get('role')})"
        elif other:
            d["other_side"] = ", ".join(names.get(a, a) for a in other)
        else:
            d["other_side"] = t.counterparty or None
        out.append(d)
    return out


def _csv(rows: List[List[object]], header: List[str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def transactions_csv(rows: List[dict]) -> str:
    ccys = sorted({c for r in rows for c in (r.get("net_cash") or {})}) or ["USD"]
    header = ["entry_id", "ts_ny", "business_date", "kind", "memo", "instrument_id", "qty", "price", "gross", "fee"] + [f"net_{c}" for c in ccys]
    body = []
    for r in rows:
        net = r.get("net_cash") or {}
        body.append([r.get("entry_id"), r.get("ts_ny"), r.get("business_date"), r.get("kind"), r.get("memo"), r.get("instrument_id"), r.get("qty"), r.get("price"),
                     None if r.get("gross_c") is None else str(from_c(r["gross_c"])), None if r.get("fee_c") is None else str(from_c(r["fee_c"]))]
                    + [net[c] if c in net else None for c in ccys])
    return _csv(body, header)


def positions_csv(positions) -> str:
    body = []
    for p in positions:
        m = p.mark
        body.append([p.account_id, p.instrument_id, p.qty, str(from_c(p.cost_c)), None if m is None or m.mark is None else str(m.mark), m.quality if m else "unavailable",
                     None if p.mv_c is None else str(from_c(p.mv_c)), None if p.unrealised_c is None else str(from_c(p.unrealised_c))])
    return _csv(body, ["account_id", "instrument_id", "qty", "cost", "mark", "quality", "mv", "unrealised"])


#: BM-14: the explain rows in the order of the identity explained = realised + income - financing - fees + unrealised + FX,
#: each with the sign of its EFFECT on P&L (financing and fees are booked as costs: a cost of 3.00 moves P&L by -3.00)
EXPLAIN_ROWS = (("realised", "realised", 1, ""), ("income", "income", 1, ""), ("financing", "financing", -1, "a financing cost reduces P&L"),
                ("fees", "fees", -1, "fees reduce P&L"), ("unrealised", "unrealised", 1, ""), ("fx_result", "FX result", 1, ""))


def pnl_out(view) -> dict:
    """``PnlView`` for JSON plus ``explain`` (BM-14): one row per component ``{key, label, booked, effect, note}`` where
    ``effect`` is the component's signed effect on P&L, so the rows add up to ``explained`` to the cent; and a
    ``baseline.label`` / ``note`` (BM-33): ITD measures from the lifetime's start ('inception (capital flows)': NAV0 = 0,
    capital is a flow), a period without a snapshot yet says so ('inception (no snapshot yet)'), else 'snapshot <date>'."""
    d = view.to_dict()
    comps = dict(view.components or {})
    rows = []
    for key, label, sign, note in EXPLAIN_ROWS:
        v = int(comps.get(key + "_c") or 0)
        rows.append({"key": key, "label": label, "booked_c": v, "effect_c": sign * v, "note": note})
    d["explain"] = rows
    d["explain_ties"] = sum(r["effect_c"] for r in rows) == int(view.explained_c)
    b = dict(d.get("baseline") or {})
    if b.get("inception"):
        if view.period == "ITD":
            b["label"], b["note"] = "inception (capital flows)", "ITD measures from the lifetime's start: NAV0 = 0 and every capital deposit or withdrawal is a flow"
        else:
            b["label"], b["note"] = "inception (no snapshot yet)", f"no daily snapshot before this {view.period} period yet: measured from the lifetime's start (never invented)"
    else:
        b["label"], b["note"] = f"snapshot {b.get('snap_date')}", "the last daily snapshot before the period"
        rs = b.get("restated")
        if isinstance(rs, dict) and rs.get("text"):
            # C9d: the baseline read from a restated version says so (the original stays stored beside it)
            b["label"] = f"snapshot {b.get('snap_date')} ({rs['text']})"
            b["note"] = f"the last daily snapshot before the period, {rs['text']}; the original snapshot is kept unchanged"
    d["baseline"] = b
    return d


def pnl_block(rt, scope: str, snap, periods=("TODAY", "MTD", "YTD", "ITD")) -> dict:
    out = {}
    for p in periods:
        try:
            out[p] = pnl_out(rt.accounting.pnl(scope, p, snapshot=snap))
        except ScopeError:
            raise
        except Exception as e:   # noqa: BLE001 — one period's failure must not hide the others; it is reported in place
            out[p] = {"period": p, "scope": scope, "error": f"{type(e).__name__}: {e}"}
            if rt.log:
                rt.log.exception("pnl %s %s failed", scope, p)
    return out


def position_row(p, names: Dict[str, str], books: Dict[str, str], nav_c: Optional[int] = None, basis: Optional[str] = None) -> dict:
    """A ``PositionView`` for JSON (USX-30 / C24): the mark with both spellings, the account's name, its lifetime / book, and
    ``weight`` = share of ``nav_c`` (the requested scope's NAV, BM-06) with ``weight_basis`` saying which NAV; without
    ``nav_c`` the weight stays the account's NAV share ``Accounting.positions`` gives (the account page labels it
    '% acct NAV').  ``notional`` (futures: contracts x multiplier x mark, in the instrument's currency) comes as computed."""
    d = p.to_dict()
    d["mark"] = mark_dict(p.mark)
    d["account_name"] = names.get(p.account_id, p.account_id)
    d.update(book_fields(p.account_id, books))
    if nav_c is not None:
        d["weight_account"] = d.get("weight")
        d["weight"] = share(p.mv_base_c, nav_c)
        d["weight_basis"] = basis or WEIGHT_BASIS["LIFETIME"]
    else:
        d["weight_basis"] = WEIGHT_BASIS["ACCOUNT"]
    return d


def positions_view(rt, scope: str, snap, kind: Optional[str] = None) -> List[dict]:
    names, books = name_maps(rt)
    out = []
    for p in rt.accounting.positions(scope, snapshot=snap):
        if kind and p.kind != kind:
            continue
        out.append(position_row(p, names, books))
    return out


def by_class(positions, nav_c: Optional[int] = None) -> List[dict]:
    """BM-04: the class subtotals as a LIST in the order the classes first appear (the Portfolio groups its rows the same
    way): ``{klass, label, count, mv, unrealised, day_pnl, cost, weight, missing, day_pnl_missing}``.  ``mv`` / ``unrealised``
    / ``day_pnl`` sum the rows that have the figure (``missing`` / ``day_pnl_missing`` count the ones that do not - an
    unpriced position is never valued at a guess); ``weight`` = ``mv`` as a share of ``nav_c`` (the scope's NAV)."""
    acc: Dict[str, dict] = {}
    order: List[str] = []
    for p in positions:
        k = p.klass or p.kind or ""
        b = acc.get(k)
        if b is None:
            b = acc[k] = {"klass": k, "label": k, "count": 0, "mv_c": 0, "unrealised_c": 0, "day_pnl_c": 0, "cost_c": 0, "missing": 0, "day_pnl_missing": 0}
            order.append(k)
        b["count"] += 1
        b["cost_c"] += int(p.cost_c or 0)
        if p.mv_base_c is None:
            b["missing"] += 1
        else:
            b["mv_c"] += int(p.mv_base_c)
        if p.unrealised_base_c is not None:
            b["unrealised_c"] += int(p.unrealised_base_c)
        if p.day_pnl_c is None:
            b["day_pnl_missing"] += 1
        else:
            b["day_pnl_c"] += int(p.day_pnl_c)
    for b in acc.values():
        b["weight"] = share(b["mv_c"], nav_c)
    return [acc[k] for k in order]


def portfolio_footer(rt, positions, snap) -> dict:
    """BM-05 footer: gross long / gross short = the market value of the long / short SECURITIES and option rows (base USD; a short
    as a positive amount) - the NAV block's own ``mv_long`` / ``mv_short`` definition (POSTCLOSE-07: the two figures tie); net =
    long - short.  OTC contracts and desk FX forwards are beside it at their market value (``otc_mv_positive`` /
    ``otc_mv_negative``, the contract value NAV carries, never a notional); futures carry no market value (their P&L is open
    trade equity), so their exposure is beside it as notional (contracts x multiplier x mark, converted at the snapshot's FX).
    Unpriced rows are counted, not valued."""
    long_c = short_c = fl_c = fs_c = otc_pos = otc_neg = 0
    unpriced = 0
    fx_missing: List[str] = []
    for p in positions:
        if p.kind == "FUTURE":
            if p.notional_c is None:
                unpriced += 1
                continue
            rate = fx_rate(rt, snap, p.currency)
            if rate is None:
                fx_missing.append(p.currency)
                continue
            n = fx_c(int(p.notional_c), rate)
            if n >= 0:
                fl_c += n
            else:
                fs_c += -n
            continue
        if p.mv_base_c is None:
            unpriced += 1
            continue
        if p.kind in ("SWAP", "FX_FORWARD"):
            if p.mv_base_c >= 0:
                otc_pos += int(p.mv_base_c)
            else:
                otc_neg += -int(p.mv_base_c)
            continue
        if p.mv_base_c >= 0:
            long_c += int(p.mv_base_c)
        else:
            short_c += -int(p.mv_base_c)
    return {"gross_long_c": long_c, "gross_short_c": short_c, "net_c": long_c - short_c, "futures_notional_long_c": fl_c, "futures_notional_short_c": fs_c,
            "otc_mv_positive_c": otc_pos, "otc_mv_negative_c": otc_neg, "unpriced": unpriced, "fx_missing": sorted(set(fx_missing)), "count": len(positions),
            "note": ("gross long / short = market value of the long / short securities and option rows in USD (the NAV block's long / short market value);"
                     " OTC contracts and FX forwards at their market value beside it; futures at notional (contracts x multiplier x mark), they carry no market value")}


def portfolio_view(rt, scope: str, snap, periods=("TODAY", "MTD", "YTD", "ITD"), with_day: bool = True) -> dict:
    """The consolidated Portfolio (B16) of ``scope`` (``{lifetime}:ALL`` / ``ALL_BOOKS`` / an account; the History pages pass
    an archived ``{lifetime}:ALL``): NAV, P&L tiles, positions with the account / book names and weights of THIS scope's NAV
    (BM-06), ``by_class`` subtotals (a list, BM-04), the footer (BM-05) and ``by_account`` rows ``{account_id, name,
    account_name, book_code, book_name, nav, weight, pnl_today}`` (``weight`` = share of the scope's NAV; ``pnl_today`` =
    ``Accounting.pnl(account, 'TODAY')``, omitted for an archived lifetime whose day is over)."""
    nav = rt.accounting.nav(scope, snapshot=snap)
    positions = rt.accounting.positions(scope, snapshot=snap)
    names, books = name_maps(rt)
    kind = "ALL_BOOKS" if scope == "ALL_BOOKS" else scope_kind(scope)
    basis = "share of the lifetime's final NAV" if nav.archived else WEIGHT_BASIS[kind]
    pv = [position_row(p, names, books, nav.nav_c, basis) for p in positions]
    counts: Dict[str, int] = {}
    for p in positions:
        counts[p.account_id] = counts.get(p.account_id, 0) + 1
    accounts = []
    for aid, v in nav.by_account.items():
        row = {"account_id": aid, "name": names.get(aid, aid), "account_name": names.get(aid, aid), "nav_c": int(v), "weight": share(v, nav.nav_c),
               "positions": counts.get(aid, 0)}
        row.update(book_fields(aid, books))
        if with_day and not nav.archived:
            try:
                pv_day = rt.accounting.pnl(aid, "TODAY", snapshot=snap)
                row["pnl_today_c"] = int(pv_day.pnl_c)
                row["pnl_today_since"] = "inception (no snapshot yet)" if pv_day.degraded else (pv_day.baseline or {}).get("snap_date")
            except Exception as e:   # noqa: BLE001 - one account's failure is said in its row, never a blank table
                row["pnl_today_c"] = None
                row["pnl_today_error"] = f"{type(e).__name__}: {e}"
        accounts.append(row)
    return {"scope": scope, "nav": nav_out(nav), "pnl": pnl_block(rt, scope, snap, periods), "by_account": accounts, "by_class": by_class(positions, nav.nav_c),
            "footer": portfolio_footer(rt, positions, snap), "positions": pv, "weight_basis": basis, "marks_partial": nav.partial, "missing_marks": nav.missing,
            "as_of": nav.as_of, "lifetime_ids": nav.lifetime_ids, "read_only": nav.archived}


def trial_balance(rt, scope: str) -> dict:
    """Per (gl, currency) debit / credit totals over ``gl_balances`` (storage is debit-positive); ``balanced`` means every
    currency's balances sum to zero AND the base amounts sum to zero (the §5.3 plug keeps them so)."""
    with rt.db.reader() as conn:
        sc = rt.accounting._scope(conn, scope)
        ids = [a["account_id"] for a in sc.accounts]
        if not ids:
            return {"scope": scope, "rows": [], "balanced": True, "totals": {"debit_c": 0, "credit_c": 0}, "by_currency": {}, "base": {"debit_c": 0, "credit_c": 0}}
        q = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT gl, currency, SUM(CASE WHEN balance_c > 0 THEN balance_c ELSE 0 END) AS dr, SUM(CASE WHEN balance_c < 0 THEN -balance_c ELSE 0 END) AS cr,"
                            f" SUM(balance_c) AS bal, SUM(base_c) AS base FROM gl_balances WHERE account_id IN ({q}) GROUP BY gl, currency ORDER BY gl, currency", ids).fetchall()
    out, by_ccy, tot_dr, tot_cr, base_dr, base_cr = [], {}, 0, 0, 0, 0
    for r in rows:
        dr, cr, bal, base = int(r["dr"] or 0), int(r["cr"] or 0), int(r["bal"] or 0), int(r["base"] or 0)
        out.append({"gl": r["gl"], "name": CHART[r["gl"]].name if r["gl"] in CHART else r["gl"], "currency": r["currency"], "debit_c": dr, "credit_c": cr, "balance_c": bal, "base_c": base})
        c = by_ccy.setdefault(r["currency"], {"debit_c": 0, "credit_c": 0, "net_c": 0, "base_c": 0})
        c["debit_c"] += dr
        c["credit_c"] += cr
        c["net_c"] += bal
        c["base_c"] += base
        tot_dr += dr
        tot_cr += cr
        base_dr += max(base, 0)
        base_cr += max(-base, 0)
    balanced = all(c["net_c"] == 0 for c in by_ccy.values()) and sum(c["base_c"] for c in by_ccy.values()) == 0
    return {"scope": scope, "rows": out, "balanced": balanced, "totals": {"debit_c": tot_dr, "credit_c": tot_cr}, "by_currency": by_ccy,
            "base": {"debit_c": base_dr, "credit_c": base_cr, "net_c": sum(c["base_c"] for c in by_ccy.values())}}


def _problem_message(got: List[dict]) -> str:
    if not got:
        return "ok"
    return "; ".join(f"{p.get('scope')}: {p.get('detail')}" for p in got[:3]) + (f" (+{len(got) - 3} more)" if len(got) > 3 else "")


def integrity_view(report, lifetime_id: Optional[str] = None) -> dict:
    """BM-26: the report with ONE ROW PER CHECK RUN ``{code, name, ok, severity, count, errors, warnings, message, scopes}``
    (``IntegrityReport.checks``, P04), so a clean run lists what it checked instead of 'no rows'.  With ``lifetime_id`` the
    rows, ``problems``, ``ok`` and ``hold`` are that lifetime's (its problems plus the database-wide ones, the rule the hold
    uses); the run itself covered the whole database (so it records every lifetime's hold correctly) and ``ok_all`` says
    how that went.  A report without per-check rows (an older stored one) falls back to one row per failing check."""
    d = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
    problems = list(d.get("problems") or [])
    if lifetime_id:
        problems = [p for p in problems if p.get("lifetime_id") in (lifetime_id, None)]
    runs = [dict(c) for c in (d.get("checks") or []) if isinstance(c, dict) and c.get("code")]
    rows: List[dict] = []
    if runs:
        for c in runs:
            got = [p for p in problems if p.get("id") == c["code"]]
            errs = sum(1 for p in got if p.get("severity") == "ERROR")
            warns = sum(1 for p in got if p.get("severity") == "WARN")
            rows.append({"code": c["code"], "name": c.get("name") or c["code"], "ok": errs == 0, "severity": "ERROR" if errs else ("WARN" if warns else ""),
                         "count": len(got), "errors": errs, "warnings": warns, "message": _problem_message(got),
                         "scopes": [p.get("scope") for p in got if p.get("scope")][:10]})
        known = {r["code"] for r in rows}
        extra = sorted({p.get("id") for p in problems if p.get("id") not in known})
    else:
        extra = sorted({p.get("id") for p in problems})
    for code in extra:                                    # a problem of a check the run list does not name (M0 'could not record', an older report)
        got = [p for p in problems if p.get("id") == code]
        errs = sum(1 for p in got if p.get("severity") == "ERROR")
        rows.append({"code": code, "name": code, "ok": errs == 0, "severity": "ERROR" if errs else "WARN", "count": len(got), "errors": errs,
                     "warnings": len(got) - errs, "message": _problem_message(got), "scopes": [p.get("scope") for p in got if p.get("scope")][:10]})
    d["ok_all"] = bool(d.get("ok"))
    if lifetime_id:
        d["ok"] = not any(p.get("severity") == "ERROR" for p in problems)
        d["hold"] = bool((d.get("holds") or {}).get(lifetime_id, False))
        d["problems"] = problems
        d["lifetime_id"] = lifetime_id
    d["checks"] = rows
    d["checks_run"] = len(rows)
    d["warnings"] = sum(r["warnings"] for r in rows)
    d["duration_ms"] = d.get("ms")
    d["scope_note"] = (f"one run over the whole database; rows show lifetime {lifetime_id} and the database-wide checks" if lifetime_id
                       else "one run over the whole database: every lifetime")
    return d


#: BM-07 / BM-25: what the Activity filter offers (``?type=``); a row's ``category`` is one of these; ``system`` rows (job runs,
#: housekeeping, data-feed and whole-database items no book owns) are shown only when asked for.  The raw row types of the first
#: API (event / fill / transfer / job / attention) still filter by ``type``.
ACTIVITY_CATEGORIES = ("orders", "settlements", "transfers", "financing", "otc", "attention", "other", "system")
ACTIVITY_ALIASES = {"order": "orders", "fills": "orders", "trades": "orders", "settlement": "settlements", "transfer": "transfers", "capital": "transfers",
                    "sweeps": "transfers", "loans": "financing", "swaps": "otc", "jobs": "system", "job": "system"}
ACTIVITY_RAW_TYPES = ("event", "fill", "transfer", "job", "attention")


def activity_tokens(raw: Optional[str]) -> set:
    return {t.strip().lower() for t in str(raw or "").split(",") if t.strip()}


def _activity_wants(tokens: set):
    """-> (categories, raw types, system wanted).  No token = every category but ``system``."""
    if not tokens:
        return set(ACTIVITY_CATEGORIES) - {"system"}, set(), False
    cats = {ACTIVITY_ALIASES.get(t, t) for t in tokens if ACTIVITY_ALIASES.get(t, t) in ACTIVITY_CATEGORIES}
    raw = {t for t in tokens if t in ACTIVITY_RAW_TYPES}
    return cats, raw, "system" in cats


def _event_category(etype: str, ref_type: Optional[str]) -> str:
    if etype in ("FX_DEALT", "FX_FORWARD_DEALT", "PRIVATE_TRADE") or ref_type in ("order", "fill", "package", "option"):
        return "orders"
    if ref_type in ("settlement", "pay", "dividend", "fx_deal") or etype in ("SETTLED", "COUPON_PAID", "PAID", "DIVIDEND_PAID", "DIVIDEND_ACCRUED"):
        return "settlements"
    if ref_type == "transfer":
        return "transfers"
    if ref_type in ("loan", "borrow", "repo", "sec_loan", "margin", "vm", "private_loan", "account"):
        return "financing"
    if ref_type == "otc":
        return "otc"
    return "other"


def _event_lifetime(account_id, ref_type, ref_id, pl: dict) -> Optional[str]:
    """The lifetime an event belongs to: its account's, a lifetime reference, or the payload's lifetime; None = system."""
    lid = lifetime_of(account_id)
    if lid:
        return lid
    if ref_type == "lifetime" and ref_id and _LIFETIME_RE.match(str(ref_id)):
        return str(ref_id)
    for k in ("lifetime_id", "closed", "opened"):
        v = pl.get(k) if isinstance(pl, dict) else None
        if isinstance(v, str) and _LIFETIME_RE.match(v):
            return v
    return None


def _detail_of(pl: dict) -> dict:
    """An event payload for the detail table: cents as money text ('amount' 1,000,000.00), never a minor-unit integer."""
    out = {}
    for k, v in (pl or {}).items():
        if k.endswith("_c") and isinstance(v, int) and not isinstance(v, bool):
            out[k[:-2]] = fmt_money(v)
        elif isinstance(v, (dict, list)):
            out[k] = json.dumps(v, default=str)[:300]
        elif k == "lender":
            out[k] = lender_text(v)                       # C16: "street lender (assumed terms)"
        else:
            out[k] = v
    return out


def link_reason(rt, conn, linked_type: Optional[str], linked_id: Optional[str]) -> Tuple[str, Optional[dict]]:
    """BM-23: what a sweep paid for, in words, and where it leads: 'for fill F-000003 (BUY 20 MSFT)' + ``{kind: 'fill',
    fill_id, order_id, account_id, instrument_id}`` (the account's Orders page lists the fill); an FX conversion, a settlement
    or another link says its own id."""
    if not linked_type or not linked_id:
        return "", None
    try:
        if linked_type == "fill":
            f = conn.execute("SELECT fill_id, order_id, account_id, instrument_id, side, qty, price FROM fills WHERE fill_id=?", (linked_id,)).fetchone()
            if f is not None:
                return (f"for fill {linked_id} ({f['side']} {int(f['qty']):,} {_symbol(rt, f['instrument_id'])})",
                        {"kind": "fill", "fill_id": linked_id, "order_id": f["order_id"], "account_id": f["account_id"], "instrument_id": f["instrument_id"]})
        if linked_type == "fx_deal":
            x = conn.execute("SELECT deal_id, account_id, kind, buy_ccy, sell_ccy, buy_c, sell_c FROM fx_deals WHERE deal_id=?", (linked_id,)).fetchone()
            if x is not None:
                return (f"for FX {str(x['kind']).lower()} {linked_id} (buy {x['buy_ccy']} {fmt_money(int(x['buy_c']))} / sell {x['sell_ccy']} {fmt_money(int(x['sell_c']))})",
                        {"kind": "fx_deal", "deal_id": linked_id, "account_id": x["account_id"]})
        if linked_type == "settlement":
            s = conn.execute("SELECT settle_id, account_id, leg, currency, amount_c, due_date FROM settlements WHERE settle_id=?", (linked_id,)).fetchone()
            if s is not None:
                return (f"for settlement {linked_id} ({s['leg']} {s['currency']} {fmt_money(abs(int(s['amount_c'])))} due {s['due_date']})",
                        {"kind": "settlement", "settle_id": linked_id, "account_id": s["account_id"]})
    except Exception:   # noqa: BLE001 - a table of a module that is not built: the plain link below
        pass
    names = {r[0]: r[1] for r in conn.execute("SELECT account_id, name FROM accounts").fetchall()} if ":" in str(linked_id) else None
    return f"for {link_words(linked_type, linked_id, names)}", {"kind": linked_type, "id": linked_id}


def _in(col: str, values) -> Tuple[str, list]:
    vals = list(values)
    return f"{col} IN ({','.join('?' for _ in vals)})", vals


def activity_rows(rt, *, limit: int = 100, account: Optional[str] = None, types: Optional[set] = None, since: Optional[int] = None,
                  lifetime_id: Optional[str] = None, lifetime_ids: Optional[List[str]] = None) -> List[dict]:
    """§15 ``GET /api/activity``: events, fills, transfers, job results and attention merged newest first (P03):
    - BOOK SEPARATION (BM-07): only the rows of ``lifetime_ids`` (the selected book's open lifetime; every open lifetime for
      ALL_BOOKS; the archived one on a History page) or of ``account``; the filtering runs in the SQL so other books never
      crowd this book's rows out of the limit;
    - ``category`` per row (orders / settlements / transfers / financing / otc / attention / other / system) and the
      ``types`` filter on it (aliases and the raw types event / fill / transfer / job / attention too); rows no book owns
      (job runs, housekeeping, feeds) are ``system`` and appear only when 'system' is asked for (BM-25);
    - an event and the transfer it describes are ONE row (the transfer, with ``events`` naming the event: BM-25);
    - names: ``account_name`` / ``book_name`` on every row and in the transfer text; ``detail`` (key / value, money as text)
      and ``entry_id`` (the journal entry of a fill / transfer) for the detail table."""
    lim = int(limit)
    tokens = set(types or ())
    cats, raw, want_system = _activity_wants(tokens)
    lids = [x for x in (lifetime_ids or ([lifetime_id] if lifetime_id else [])) if x]
    names, books = name_maps(rt)

    def wanted(row: dict) -> bool:
        return row["category"] in cats or row["type"] in raw

    rows: List[dict] = []
    transfers_by_id: Dict[str, dict] = {}
    with rt.db.reader() as conn:
        if account:
            accts: Optional[List[str]] = [account]
        elif lids:
            w, p = _in("lifetime_id", lids)
            accts = [r[0] for r in conn.execute(f"SELECT account_id FROM accounts WHERE {w}", p).fetchall()]
        else:
            accts = None                                    # every account (the consolidated, unfiltered feed)
        lid_set = set(lids)

        def acct_clause(col: str) -> Tuple[str, list]:
            if accts is None:
                return "1=1", []
            if not accts:
                return "1=0", []
            return _in(col, accts)

        def owned(lid: Optional[str]) -> bool:
            return (not lid_set and not account) or (lid in lid_set and not account)

        # ---- transfers first: the events that describe them merge into them
        w1, p1 = acct_clause("from_account")
        w2, p2 = acct_clause("to_account")
        for r in conn.execute(f"SELECT * FROM transfers WHERE ({w1} OR {w2}) AND (? IS NULL OR ts >= ?) ORDER BY ts DESC, transfer_id DESC LIMIT ?",
                              p1 + p2 + [since, since, lim]).fetchall():
            src, dst = r["from_account"], r["to_account"]
            reason, link = link_reason(rt, conn, r["linked_type"], r["linked_id"])
            words = TRANSFER_WORDS.get(r["kind"], str(r["kind"]).lower())
            aid = dst if (dst and (accts is None or dst in accts)) else src
            row = {"ts": int(r["ts"]), "type": "transfer", "category": "transfers", "severity": "INFO", "account_id": aid, "instrument_id": None,
                   "summary": f"{words} {r['currency']} {fmt_money(int(r['amount_c']))} {names.get(src, src) if src else 'from outside the book'} -> "
                              f"{names.get(dst, dst) if dst else 'outside the book'}" + (f" {reason}" if reason else ""),
                   "ref_type": "transfer", "ref_id": r["transfer_id"], "entry_id": r["entry_id"], "events": [], "link": link,
                   "detail": {"kind": words, "from": f"{names.get(src, src)} ({src})" if src else "outside the book",
                              "to": f"{names.get(dst, dst)} ({dst})" if dst else "outside the book", "currency": r["currency"], "amount": fmt_money(int(r["amount_c"])),
                              "paid for": reason or None, "shortfall": None if r["shortfall_c"] is None else fmt_money(int(r["shortfall_c"])),
                              "business date": r["business_date"]}}
            transfers_by_id[r["transfer_id"]] = row
            rows.append(row)
        # ---- events: the accounts' rows, then the account-less ones (a lifetime's own events, else system)
        we, pe = acct_clause("account_id")
        ev = conn.execute(f"SELECT id, ts, type, account_id, instrument_id, ref_type, ref_id, payload_json FROM events WHERE account_id IS NOT NULL AND {we}"
                          " AND (? IS NULL OR ts >= ?) ORDER BY ts DESC, id DESC LIMIT ?", pe + [since, since, lim]).fetchall()
        if not account:
            ev = list(ev) + list(conn.execute("SELECT id, ts, type, account_id, instrument_id, ref_type, ref_id, payload_json FROM events WHERE account_id IS NULL"
                                              " AND (? IS NULL OR ts >= ?) ORDER BY ts DESC, id DESC LIMIT ?", (since, since, lim)).fetchall())
        for r in ev:
            try:
                pl = json.loads(r["payload_json"] or "{}")
            except ValueError:
                pl = {}
            pl = pl if isinstance(pl, dict) else {}
            if r["ref_type"] == "transfer" and r["ref_id"] in transfers_by_id:
                transfers_by_id[r["ref_id"]]["events"].append(r["type"])          # BM-25: one row per transfer
                continue
            lid = _event_lifetime(r["account_id"], r["ref_type"], r["ref_id"], pl)
            if r["account_id"] is None and not owned(lid):
                if lid is not None or not want_system:
                    continue
            system = r["account_id"] is None and lid is None
            bits = [f"{k} {code_words(pl[k]) if k == 'side' else pl[k]}" for k in ("side", "qty", "price") if pl.get(k) is not None]
            amt = pl.get("amount_c")
            if isinstance(amt, int) and not isinstance(amt, bool):
                bits.append(f"amount {pl['currency'] + ' ' if pl.get('currency') else ''}{fmt_money(amt)}")     # cents -> money text, never "amount_c 100000000"
            elif pl.get("currency") is not None:
                bits.append(f"currency {pl['currency']}")
            if pl.get("reason") is not None:
                bits.append(f"reason {readable(pl['reason'], names)}")
            ref_txt = f"{REF_WORDS.get(r['ref_type'], str(r['ref_type'] or '').replace('_', ' '))} {r['ref_id'] or ''}".strip() if r["ref_id"] else ""
            rows.append({"ts": int(r["ts"]), "type": "event", "event": r["type"], "category": "system" if system else _event_category(r["type"], r["ref_type"]),
                         "system": system, "severity": "INFO", "account_id": r["account_id"], "instrument_id": r["instrument_id"], "lifetime_id": lid,
                         "summary": code_words(r["type"]) + (f" - {ref_txt}" if ref_txt else "") + (": " + ", ".join(bits) if bits else ""),
                         "ref_type": r["ref_type"], "ref_id": r["ref_id"], "id": int(r["id"]), "detail": _detail_of(pl)})
        # ---- fills (15.7 item 7: each with its booking and source label - "Simulated fill", or the as-of label with the AS-OF badge)
        wf, pf = acct_clause("account_id")
        src_cache: dict = {}
        for r in conn.execute(f"SELECT * FROM fills WHERE {wf} AND (? IS NULL OR ts >= ?) ORDER BY ts DESC, fill_id DESC LIMIT ?", pf + [since, since, lim]).fetchall():
            sym = _symbol(rt, r["instrument_id"])
            src = source_of(conn, "fill", r, src_cache)
            rows.append({"ts": int(r["ts"]), "type": "fill", "category": "orders", "severity": "INFO", "account_id": r["account_id"], "instrument_id": r["instrument_id"],
                         "summary": f"{r['fill_id']} {r['side']} {int(r['qty']):,} {sym} @ {r['price']} ({r['price_basis']}, {r['origin']})",
                         "ref_type": "fill", "ref_id": r["fill_id"], "entry_id": r["entry_id"], "order_id": r["order_id"],
                         **source_fields(src), "entered_ts": src.get("entered_ts"),
                         "detail": {"fill": r["fill_id"], "order": r["order_id"], "side": r["side"], "quantity": f"{int(r['qty']):,}", "instrument": sym,
                                    "price": r["price"], "price basis": r["price_basis"], "origin": r["origin"], "gross": f"{r['currency']} {fmt_money(int(r['gross_c']))}",
                                    "fee": f"{r['currency']} {fmt_money(int(r['fee_c'] or 0))}", "trade date": r["trade_date"], "settles": r["settle_date"],
                                    "source": src.get("label")}})
        # ---- job results: system rows (no book owns them)
        if want_system and not account:
            for r in conn.execute("SELECT job, key, run_ts, status, note FROM jobs_done WHERE (? IS NULL OR run_ts >= ?) ORDER BY run_ts DESC LIMIT ?", (since, since, lim)).fetchall():
                rows.append({"ts": int(r["run_ts"]), "type": "job", "category": "system", "system": True, "severity": "INFO" if r["status"] == "DONE" else "WARN",
                             "account_id": None, "instrument_id": None, "summary": f"{r['job']} {r['key']}: {r['status']}" + (f" - {r['note']}" if r["note"] else ""),
                             "ref_type": "job", "ref_id": f"{r['job']}:{r['key']}", "detail": {"job": r["job"], "key": r["key"], "status": r["status"], "note": r["note"]}})
        # ---- attention: the accounts' items and the lifetimes' own; the rows no book owns are system
        wa, pa = acct_clause("account_id")
        for r in conn.execute(f"SELECT id, ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts FROM attention"
                              f" WHERE (account_id IS NULL OR {wa}) AND (? IS NULL OR ts >= ?) ORDER BY ts DESC, id DESC LIMIT ?", pa + [since, since, lim]).fetchall():
            if r["account_id"] is None and account:
                continue
            lid = attention_lifetime(dict(r))
            if r["account_id"] is None and not owned(lid):
                if lid is not None or not want_system:
                    continue
            system = r["account_id"] is None and lid is None
            rows.append({"ts": int(r["ts"]), "type": "attention", "category": "system" if system else "attention", "system": system, "severity": r["severity"],
                         "account_id": r["account_id"], "instrument_id": None, "lifetime_id": lid, "summary": f"{code_words(r['code'])}: {readable(r['message'], names)}", "ref_type": "attention",
                         "ref_id": str(r["id"]), "resolved_ts": r["resolved_ts"], "source": r["source"],
                         "detail": {"code": r["code"], "source": r["source"], "severity": r["severity"], "message": r["message"],
                                    "resolved": ny_of(int(r["resolved_ts"])).strftime("%Y-%m-%d %H:%M:%S") if r["resolved_ts"] else "open"}})
    out = []
    for x in rows:
        x.setdefault("system", False)
        if not wanted(x):
            continue
        x["account_name"] = names.get(x["account_id"], x["account_id"]) if x.get("account_id") else None
        bf = book_fields(x.get("account_id"), books)
        if not bf["lifetime_id"] and x.get("lifetime_id"):
            code = _books.book_code_of(x["lifetime_id"])
            bf = {"lifetime_id": x["lifetime_id"], "book_code": code, "book_name": books.get(code, code)}
        x.update(bf)
        if x.get("instrument_id"):
            x["symbol"] = _symbol(rt, x["instrument_id"])
        out.append(x)
    out.sort(key=lambda x: (-int(x["ts"]), str(x.get("ref_id") or "")))
    for x in out:
        x["ts_ny"] = ny_of(int(x["ts"])).strftime("%Y-%m-%d %H:%M:%S")
    return out[:lim]


def activity_extras(rt, tokens: set, *, account: Optional[str] = None, lifetime_ids: Optional[List[str]] = None) -> dict:
    """SHELL-27: the Dashboard's attention tiles 'working orders' / 'pending settlements' open #/activity?type=orders|settlements; those views
    also list what is open NOW in the book (or the account): ``working_orders`` (WORKING / PARTIAL / WAITING_PARENT orders)
    and ``pending_settlements`` (the ladder), so the tile's count can be read row by row."""
    cats, _raw, _sys = _activity_wants(tokens)
    out: dict = {}
    if not ({"orders", "settlements"} & cats) or not tokens:
        return out
    names, _bk = name_maps(rt)
    with rt.db.reader() as conn:
        if account:
            accts = [account]
        elif lifetime_ids:
            w, p = _in("lifetime_id", lifetime_ids)
            accts = [r[0] for r in conn.execute(f"SELECT account_id FROM accounts WHERE {w}", p).fetchall()]
        else:
            accts = [r[0] for r in conn.execute("SELECT account_id FROM accounts").fetchall()]
        if not accts:
            return {"working_orders": [], "pending_settlements": []} if tokens else out
        wa, pa = _in("account_id", accts)
        if "orders" in cats:
            q = ",".join("?" for _ in OPEN_ORDER_STATUSES)
            orders = []
            for r in conn.execute(f"SELECT order_id, account_id, instrument_id, side, qty, filled_qty, order_type, limit_px, stop_px, tif, status, created_ts FROM orders"
                                  f" WHERE status IN ({q}) AND {wa} ORDER BY created_ts DESC, order_id DESC LIMIT 300", list(OPEN_ORDER_STATUSES) + pa).fetchall():
                d = dict(r)
                d["symbol"] = _symbol(rt, r["instrument_id"])
                d["account_name"] = names.get(r["account_id"], r["account_id"])
                d["remaining"] = int(r["qty"]) - int(r["filled_qty"] or 0)
                orders.append(d)
            out["working_orders"] = orders
        if "settlements" in cats:
            ladder = []
            for r in conn.execute(f"SELECT s.settle_id, s.account_id, s.fill_id, s.fx_deal_id, s.leg, s.currency, s.amount_c, s.due_date, f.instrument_id, f.side, f.qty"
                                  f" FROM settlements s LEFT JOIN fills f ON f.fill_id=s.fill_id WHERE s.status='PENDING' AND s.{wa}"
                                  " ORDER BY s.due_date, s.settle_id LIMIT 500", pa).fetchall():
                d = {k: r[k] for k in ("settle_id", "account_id", "fill_id", "fx_deal_id", "leg", "currency", "amount_c", "due_date")}
                d["account_name"] = names.get(r["account_id"], r["account_id"])
                d["direction"] = "receive" if int(r["amount_c"]) > 0 else "pay"
                d["what"] = (f"{r['side']} {int(r['qty']):,} {_symbol(rt, r['instrument_id'])} ({r['fill_id']})" if r["fill_id"] and r["instrument_id"]
                             else (f"FX deal {r['fx_deal_id']}" if r["fx_deal_id"] else r["leg"]))
                ladder.append(d)
            out["pending_settlements"] = ladder
    return out


def books_view(rt, snap=None, unlock: bool = True) -> List[dict]:
    """§15 ``GET /api/books`` rows (the sidebar switcher); ``unlock`` runs ``books.unlock_private`` first (a command only
    when a lifetime just crossed its qualified-purchaser threshold)."""
    if unlock:
        try:
            _books.unlock_private(rt)
        except Exception as e:   # noqa: BLE001 — the list still renders; the failure is logged and visible as an attention row by the bus
            if rt.log:
                rt.log.exception("unlock_private failed: %s", e)
    snap = snap if snap is not None else rt.marks.snapshot()
    with rt.db.reader() as conn:
        bks = _books.books(conn)
        info = []
        for b in bks:
            lrow = _books.open_lifetime(conn, b.code)
            accounts = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (lrow["lifetime_id"],)).fetchall()] if lrow else []
            past = int(conn.execute("SELECT COUNT(*) FROM lifetimes WHERE book_code=? AND status='CLOSED'", (b.code,)).fetchone()[0])
            info.append((b, dict(lrow) if lrow else None, accounts, past))
    out = []
    for b, lrow, accounts, past in info:
        row = {"book_code": b.code, "name": b.name, "profile": b.profile, "profile_label": rt_profile_label(b.profile), "starting_capital_c": b.starting_capital_c,
               "lifetime": None, "nav": None, "eligibility": None, "accounts": accounts, "past_lifetimes": past, "held": False}
        if lrow is not None:
            lid = lrow["lifetime_id"]
            nav = rt.accounting.nav(f"{lid}:ALL", snapshot=snap)
            elig = _books.eligibility(rt, lid, nav_c=nav.nav_c)
            row["lifetime"] = {"lifetime_id": lid, "seq": int(lrow["seq"]), "name": lrow["name"], "started": ny_of(int(lrow["started_ts"])).date().isoformat(),
                               "started_ts": int(lrow["started_ts"]), "starting_capital_c": int(lrow["starting_capital_c"]), "status": lrow["status"]}
            row["nav"] = {"value_c": nav.nav_c, "quality": nav.quality, "partial": nav.partial, "as_of": nav.as_of}
            row["eligibility"] = elig.to_dict()
            row["held"] = rt.accounting.is_held(lid)
        out.append(row)
    return out


def rt_profile_label(profile: str) -> str:
    from .tables import BOOK_PROFILES
    return (BOOK_PROFILES.get(profile) or {}).get("label", profile)


def lifetime_rows(rt, book_code: str) -> List[dict]:
    with rt.db.reader() as conn:
        if _books.book_of(conn, book_code) is None:
            raise NotFound(f"book {book_code} does not exist")
        rows = [dict(r) for r in conn.execute("SELECT * FROM lifetimes WHERE book_code=? ORDER BY seq DESC", (book_code,)).fetchall()]
    out = []
    for r in rows:
        ok = None
        if r.get("integrity_json"):
            try:
                ok = bool(json.loads(r["integrity_json"]).get("ok"))
            except (ValueError, AttributeError):
                ok = None
        elif r["status"] == "OPEN":
            ok = not rt.accounting.is_held(r["lifetime_id"])
        out.append({"lifetime_id": r["lifetime_id"], "seq": int(r["seq"]), "name": r["name"], "status": r["status"],
                    "started": ny_of(int(r["started_ts"])).date().isoformat(), "started_ts": int(r["started_ts"]),
                    "ended": ny_of(int(r["ended_ts"])).date().isoformat() if r.get("ended_ts") else None, "ended_ts": r.get("ended_ts"),
                    "starting_capital_c": int(r["starting_capital_c"]), "final_nav_c": r.get("final_nav_c"), "final_realised_c": r.get("final_realised_c"),
                    "final_pnl_c": r.get("final_pnl_c"), "final_quality": r.get("final_quality"), "reason": r.get("reason"), "integrity_ok": ok})
    return out


# ---- the routes --------------------------------------------------------------------------------------------------------------
def register_routes(r) -> None:
    def accounts(req: Request):
        return account_rows(req.rt, req.lifetime())

    def account_pb(req: Request):
        need(req.rt, "prime")
        broker = str(req.b("broker") or "").upper()
        if broker not in config.PRIME_BROKERS:
            raise ApiError(400, "UNKNOWN_BROKER", f"{broker!r} is not a known prime broker", {"brokers": sorted(config.PRIME_BROKERS)})
        lid = req.b("lifetime_id") or req.lifetime()
        res = run_command(req, "account.open_pb", {"lifetime_id": lid, "broker": broker})
        body = ok_or_raise(res)
        aid = body.get("account_id") or _books.account_id(lid, f"PB_{broker}")
        return 200, {"account": account_row(req.rt, aid), "schedule": body.get("schedule"), "result": body}, ({"X-Replayed": "1"} if res.replayed else {})

    def account_patch(req: Request):
        aid = req.params[0]
        _account_or_404(req.rt, aid)
        payload = {"account_id": aid}
        src = req.body or {}
        for k in ("sweep_out", "pb_financing"):
            if k in src:
                payload[k] = src[k]
        for k in ("sweep_buffer", "pb_limit"):
            if k + "_c" in src:
                payload[k + "_c"] = int(src[k + "_c"])
            elif k in src and src[k] is not None:
                payload[k + "_c"] = to_c(str(src[k]))
        res = run_command(req, "account.update_settings", payload)
        ok_or_raise(res)
        return 200, account_row(req.rt, aid), ({"X-Replayed": "1"} if res.replayed else {})

    def account_detail(req: Request):
        rt, aid = req.rt, req.params[0]
        _account_or_404(rt, aid)
        snap = rt.marks.snapshot()
        row = account_row(rt, aid, snap)
        cash = rt.accounting.cash(aid, snapshot=snap)
        bp = {ccy: rt.accounting.buying_power(aid, ccy, snapshot=snap) for ccy in cash}
        nav = rt.accounting.nav(aid, snapshot=snap)
        ob = rt.accounting.obligations(aid)
        today = accounting_day(rt.clock.now()).isoformat()
        with rt.db.reader() as conn:
            sweeps = [dict(x) for x in conn.execute("SELECT * FROM transfers WHERE kind IN ('SWEEP_IN','SWEEP_OUT') AND (from_account=? OR to_account=?) AND business_date=?"
                                                    " ORDER BY ts DESC", (aid, aid, today)).fetchall()]
            attention = [dict(x) for x in conn.execute("SELECT * FROM attention WHERE resolved_ts IS NULL AND account_id=? ORDER BY ts DESC", (aid,)).fetchall()]
            npos = int(conn.execute("SELECT COUNT(*) FROM v_positions WHERE account_id=? AND qty != 0", (aid,)).fetchone()[0])
            # POSTCLOSE-04: an open OTC contract and an open private loan are positions too (the PB header said 0 while holding 5 contracts)
            for sql in ("SELECT COUNT(*) FROM otc_contracts WHERE account_id=? AND status='OPEN'", "SELECT COUNT(*) FROM private_loans WHERE account_id=? AND status='OPEN'"):
                try:
                    npos += int(conn.execute(sql, (aid,)).fetchone()[0])
                except Exception:   # noqa: BLE001 - a table of a module that is not built: nothing of that kind is held
                    pass
        return {"account": row, "cash": cash, "buying_power": bp, "nav": nav_out(nav), "pnl": pnl_block(rt, aid, snap), "positions_count": npos,
                "working_orders": row["working_orders"], "pending_settlements": ob.settlements, "pending_in": money_map(ob.pending_in), "pending_out": money_map(ob.pending_out),
                "next_due": ob.next_due, "borrows": ob.borrows, "loans": ob.loans, "margin": ob.margin, "collateral": {"held_at_lender_c": ob.collateral_c}, "otc": ob.otc,
                "sweeps_today": sweeps, "attention": attention, "marks_partial": nav.partial, "missing_marks": nav.missing, "as_of": nav.as_of,
                "liquidity": liquidity_out(rt.accounting.liquidity(aid, snapshot=snap)), "available_definition": AVAILABLE_TEXT}

    def account_positions(req: Request):
        rt, aid = req.rt, req.params[0]
        _account_or_404(rt, aid)
        return positions_view(rt, aid, rt.marks.snapshot(), req.q("kind"))

    def position_detail(req: Request):
        rt, aid, iid = req.rt, req.params[0], req.params[1].upper()
        _account_or_404(rt, aid)
        snap = rt.marks.snapshot()
        pos = rt.accounting.position(aid, iid, snapshot=snap)
        inst = rt.instruments.get(iid)
        if pos is None and inst is None:
            raise NotFound(f"{aid} has no position in {iid}")
        with rt.db.reader() as conn:
            fills = fill_rows(rt, conn, "account_id=? AND instrument_id=?", [aid, iid], req.now, 200)
            entries = [dict(e) for e in conn.execute("SELECT DISTINCT e.entry_id, e.ts, e.kind, e.memo, e.ref_type, e.ref_id, e.business_date FROM journal_entries e"
                                                     " JOIN journal_lines l ON l.entry_id=e.entry_id WHERE l.account_id=? AND l.instrument_id=? ORDER BY e.entry_id DESC LIMIT 100",
                                                     (aid, iid)).fetchall()]
            borrows = borrows_shown([dict(b) for b in conn.execute("SELECT * FROM borrows WHERE account_id=? AND instrument_id=? ORDER BY opened_ts DESC", (aid, iid)).fetchall()])
            marks = [dict(m) for m in conn.execute("SELECT * FROM marks WHERE instrument_id=? ORDER BY mark_date DESC, kind LIMIT 60", (iid,)).fetchall()]
            # WP-18: the dividend rows of the name with their state and the DIVIDEND convention (ex-date income / in lieu, pay date)
            div = getattr(rt, "dividends", None)
            notes = (div.position_notes(conn, rt.cal, aid, iid, rt.clock.today())
                     if div is not None and hasattr(div, "position_notes") and inst is not None and inst.kind in EQUITY_KINDS else [])
        d = pos.to_dict() if pos is not None else None
        if d is not None:
            d["mark"] = mark_dict(pos.mark)
        names, _bk = name_maps(rt)
        for e in entries:                                                  # V104R1-04: the drawer's cash flows in words (the stored memo kept)
            e["memo_stored"], e["memo"] = e.get("memo"), readable(clean_memo(e.get("memo")), names)
            e["kind_label"] = code_words(e.get("kind"))
        return {"position": d, "instrument": inst.to_dict() if inst else None, "lots": rt.accounting.lots(aid, iid), "fills": fills, "entries": entries,
                "borrow": borrows[0] if borrows and borrows[0].get("status") == "OPEN" else None, "borrows": borrows, "marks_history": marks,
                "mark": mark_dict(snap.mark(iid)) if inst else None, "dividend_notes": notes}

    def account_orders(req: Request):
        rt, aid = req.rt, req.params[0]
        _account_or_404(rt, aid)
        status = (req.q("status") or "open").lower()
        limit, offset = req.q_int("limit", 200, 1, 2000), req.q_int("offset", 0, 0, 1_000_000)
        with rt.db.reader() as conn:
            if status == "open":
                q = ",".join("?" for _ in OPEN_ORDER_STATUSES)
                ids = conn.execute(f"SELECT order_id FROM orders WHERE account_id=? AND status IN ({q}) ORDER BY created_ts DESC, order_id DESC LIMIT ? OFFSET ?",
                                   (aid, *OPEN_ORDER_STATUSES, limit, offset)).fetchall()
            elif status == "all":
                ids = conn.execute("SELECT order_id FROM orders WHERE account_id=? ORDER BY created_ts DESC, order_id DESC LIMIT ? OFFSET ?", (aid, limit, offset)).fetchall()
            else:
                ids = conn.execute("SELECT order_id FROM orders WHERE account_id=? AND status=? ORDER BY created_ts DESC, order_id DESC LIMIT ? OFFSET ?",
                                   (aid, status.upper(), limit, offset)).fetchall()
            orders = [order_full(rt, conn, x["order_id"]) for x in ids]
            rej = [dict(x) for x in conn.execute("SELECT id, ts, request_id, account_id, instrument_id, side, qty, order_type, code, message FROM order_rejections"
                                                 " WHERE account_id=? ORDER BY ts DESC, id DESC LIMIT 50", (aid,)).fetchall()]
        return {"orders": orders, "rejections": rej, "status": status, "limit": limit, "offset": offset}

    def account_fills(req: Request):
        rt, aid = req.rt, req.params[0]
        _account_or_404(rt, aid)
        where, params = ["account_id=?"], [aid]
        if req.q("from"):
            where.append("trade_date >= ?")
            params.append(req.q("from"))
        if req.q("to"):
            where.append("trade_date <= ?")
            params.append(req.q("to"))
        if req.q("instrument"):
            where.append("instrument_id=?")
            params.append(req.q("instrument").upper())
        with rt.db.reader() as conn:
            return fill_rows(rt, conn, " AND ".join(where), params, req.now, req.q_int("limit", 500, 1, 5000))

    def _txn_args(req: Request):
        kinds = [k.strip().upper() for k in (req.q("kinds") or "").split(",") if k.strip()] or None
        return dict(from_date=req.q("from"), to_date=req.q("to"), kinds=kinds, instrument_id=(req.q("instrument") or None),
                    limit=req.q_int("limit", 500, 1, 5000), offset=req.q_int("offset", 0, 0, 1_000_000))

    def account_transactions(req: Request):
        rt, aid = req.rt, req.params[0]
        _account_or_404(rt, aid)
        rows = txn_rows(rt, aid, **_txn_args(req))
        if (req.q("format") or "json").lower() == "csv":
            return 200, transactions_csv(rows), {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'attachment; filename="{aid}-transactions.csv"'}
        return {"rows": rows, "account_id": aid, "count": len(rows)}

    def account_cash(req: Request):
        rt, aid = req.rt, req.params[0]
        _account_or_404(rt, aid)
        snap = rt.marks.snapshot()
        row = account_row(rt, aid, snap)
        cash = rt.accounting.cash(aid, snapshot=snap)
        ob = rt.accounting.obligations(aid)
        today = rt.clock.today()
        horizon = rt.cal.add_business_days(today, 5).isoformat()
        otc_refs: Dict[str, str] = {}
        with rt.db.reader() as conn:
            transfers = [dict(x) for x in conn.execute("SELECT * FROM transfers WHERE from_account=? OR to_account=? ORDER BY ts DESC, transfer_id DESC LIMIT 100", (aid, aid)).fetchall()]
            # OTC-R1-04: an OTC contract's own settlement (a CDX initial payment, an OTC option premium) names its contract in 'ref'
            try:
                for c in conn.execute("SELECT contract_id, terms_json FROM otc_contracts WHERE account_id=?", (aid,)).fetchall():
                    try:
                        terms = json.loads(c["terms_json"] or "{}")
                    except ValueError:
                        terms = {}
                    for k in ("settle_id", "premium_settle_id"):
                        if isinstance(terms, dict) and terms.get(k):
                            otc_refs[str(terms[k])] = c["contract_id"]
            except Exception:   # noqa: BLE001 - a database without the OTC table: no contract refs
                otc_refs = {}
        ladder = [dict(s, within_5bd=str(s["due_date"]) <= horizon, ref=s.get("fill_id") or s.get("fx_deal_id") or otc_refs.get(str(s.get("settle_id"))))
                  for s in ob.settlements]
        return {"account_id": aid, "account": row, "cash": cash, "buying_power": {ccy: rt.accounting.buying_power(aid, ccy, snapshot=snap) for ccy in cash},
                "transfers": transfers, "ladder": ladder, "ladder_horizon": horizon, "loans": ob.loans, "borrows": ob.borrows, "margin": ob.margin,
                "collateral": {"held_at_lender_c": ob.collateral_c}, "otc": ob.otc, "liquidity": liquidity_out(rt.accounting.liquidity(aid, snapshot=snap)),
                "available_definition": AVAILABLE_TEXT, "accounting_day": accounting_day(rt.clock.now()).isoformat()}

    def account_markets(req: Request):
        from .api_products import markets_view
        return markets_view(req, req.params[0], req.q("tab"))

    def portfolio(req: Request):
        rt = req.rt
        scope = req.scope("ALL")
        snap = rt.marks.snapshot()
        out = portfolio_view(rt, scope, snap)
        out["liquidity"] = liquidity_out(rt.accounting.liquidity(scope, snapshot=snap))
        return out

    def treasury(req: Request):
        rt = req.rt
        lid = req.lifetime()
        return treasury_view(rt, lid)

    def amount_fields(req: Request) -> dict:
        """BM-21: a typed amount reaches the command AS TYPED (``amount``), where ``treasury.amount_c`` reads it strictly
        (``units.parse_money``: digits with at most 2 decimals; '1e6', '100.005', '1,000', a sign and the typo guard are
        refused 400 AMOUNT, never rounded or expanded); integer cents in ``amount_c`` pass as before."""
        b = req.body or {}
        if b.get("amount_c") is None and b.get("amount") is not None:
            return {"amount": b.get("amount")}
        return {"amount_c": req.amount_c()}

    def treasury_move(req: Request, kind: str):
        rt = req.rt
        lid = req.b("lifetime_id") or req.lifetime()
        payload = dict({"lifetime_id": lid, "currency": str(req.b("currency") or "USD").upper(), "memo": req.b("memo")}, **amount_fields(req))
        payload = {k: v for k, v in payload.items() if v is not None}
        res = run_command(req, kind, payload)
        body = ok_or_raise(res)
        with rt.db.reader() as conn:
            tr = conn.execute("SELECT * FROM transfers WHERE transfer_id=?", (body.get("transfer_id"),)).fetchone()
        return 200, {"transfer": dict(tr) if tr else body, "treasury": treasury_out(rt.accounting.treasury(lid)), "result": body}, ({"X-Replayed": "1"} if res.replayed else {})

    def transfers(req: Request):
        rt = req.rt
        payload = dict({"from_account": req.b("from_account"), "to_account": req.b("to_account"), "currency": str(req.b("currency") or "USD").upper(),
                        "memo": req.b("memo")}, **amount_fields(req))
        payload = {k: v for k, v in payload.items() if v is not None}
        res = run_command(req, "treasury.transfer", payload)
        body = ok_or_raise(res)
        snap = rt.marks.snapshot()
        with rt.db.reader() as conn:
            tr = conn.execute("SELECT * FROM transfers WHERE transfer_id=?", (body.get("transfer_id"),)).fetchone()
        return 200, {"transfer": dict(tr) if tr else body, "from_cash": rt.accounting.cash(payload["from_account"], snapshot=snap),
                     "to_cash": rt.accounting.cash(payload["to_account"], snapshot=snap), "result": body}, ({"X-Replayed": "1"} if res.replayed else {})

    def balance_sheet(req: Request):
        scope = req.scope("ALL")
        return balance_sheet_out(req.rt.accounting.balance_sheet(scope, snapshot=req.rt.marks.snapshot()), scope_kind(scope))

    def pnl(req: Request):
        period = (req.q("period") or "TODAY").upper()
        if period not in ("TODAY", "MTD", "YTD", "ITD"):
            raise ApiError(400, "BAD_REQUEST", "period must be TODAY, MTD, YTD or ITD")
        return pnl_out(req.rt.accounting.pnl(req.scope("ALL"), period, snapshot=req.rt.marks.snapshot()))

    def trial(req: Request):
        return trial_balance(req.rt, req.scope("ALL"))

    def journal(req: Request):
        rows = txn_rows(req.rt, req.scope("ALL"), **_txn_args(req))
        gl = req.q("gl")
        if gl:
            rows = [x for x in rows if any(l.get("gl") == gl for l in x["lines"])]
        return {"entries": rows, "count": len(rows)}

    def accounting_transactions(req: Request):
        """BM-13: Accounting > Transactions - the transaction rows of a scope (the book's lifetime, ALL_BOOKS or an account),
        ``format=csv`` for the file (the export columns)."""
        scope = req.scope("ALL")
        rows = txn_rows(req.rt, scope, **_txn_args(req))
        if (req.q("format") or "json").lower() == "csv":
            name = scope.replace(":", "-")
            return 200, transactions_csv(rows), {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'attachment; filename="{name}-transactions.csv"'}
        return {"rows": rows, "count": len(rows), "scope": scope}

    def accounting_cash(req: Request):
        return cash_financing_view(req.rt, req.scope("ALL"), req.rt.marks.snapshot())

    def journal_entry(req: Request):
        return journal_entry_view(req.rt, req.params[0])

    def integrity(req: Request):
        """BM-26: one run over the whole database (so every lifetime's hold is recorded right) shown for the scope's lifetime:
        ``scope`` ALL / ``{lifetime}:ALL`` / an account -> that lifetime's rows; ``ALL_BOOKS`` -> every row; ``lifetime`` names
        one directly."""
        rt = req.rt
        mode = (req.q("mode") or "ledger").lower()
        if mode not in ("ledger", "full"):
            raise ApiError(400, "BAD_REQUEST", "mode must be ledger or full")
        scope = req.q("scope") or "ALL"
        lid = req.q("lifetime")
        if not lid and scope != "ALL_BOOKS":
            if _ACCOUNT_RE.match(scope):
                lid = lifetime_of(scope)
            elif scope.endswith(":ALL"):
                lid = scope[:-4]
            else:
                lid = req.lifetime()
        if lid:
            with rt.db.reader() as conn:
                if _books.lifetime_row(conn, lid) is None:
                    raise NotFound(f"lifetime {lid} does not exist")
        return integrity_view(rt.accounting.integrity(mode, snapshot=rt.marks.snapshot() if mode == "full" else None), lid)

    def activity(req: Request):
        """BM-07: the selected book's activity (its open lifetime) unless ``account`` names one account; ``scope=ALL_BOOKS``
        (or ``all=1``) = every open lifetime; ``scope={lifetime}:ALL`` one lifetime.  ``type`` = categories (orders,
        settlements, transfers, financing, otc, attention, other, system) or the raw types; 'orders' / 'settlements' add what is
        open now (SHELL-27)."""
        rt = req.rt
        tokens = activity_tokens(req.q("type"))
        since = req.q_int("since", 0, 0, 10**12) or None
        account = req.q("account")
        scope = req.q("scope") or ""
        lids: Optional[List[str]] = None
        if account:
            _account_or_404(rt, account)
        elif scope == "ALL_BOOKS" or req.q("all") in ("1", "true", "yes"):
            with rt.db.reader() as conn:
                lids = _books.open_lifetimes(conn)
            scope = "ALL_BOOKS"
        elif scope.endswith(":ALL") and _LIFETIME_RE.match(scope[:-4]):
            lids = [scope[:-4]]
        else:
            lids = [req.lifetime()]
            scope = f"{lids[0]}:ALL"
        out = {"rows": activity_rows(rt, limit=req.q_int("limit", 100, 1, 500), account=account, types=tokens, since=since, lifetime_ids=lids),
               "scope": account or scope, "lifetime_ids": lids or [], "types": sorted(tokens), "categories": list(ACTIVITY_CATEGORIES)}
        out.update(activity_extras(rt, tokens, account=account, lifetime_ids=lids))
        return out

    def books(req: Request):
        return books_view(req.rt)

    def book_lifetimes(req: Request):
        return {"book_code": req.params[0], "lifetimes": lifetime_rows(req.rt, req.params[0])}

    def book_reset(req: Request):
        rt, code = req.rt, req.params[0]
        with rt.db.reader() as conn:
            if _books.book_of(conn, code) is None:
                raise NotFound(f"book {code} does not exist")
        if str(req.b("confirm") or "") != "RESET":
            raise ApiError(400, "CONFIRM", "type RESET to confirm closing the lifetime", {"confirm": req.b("confirm")})
        cap = req.amount_c("starting_capital_c", "starting_capital")
        need(rt, "lifetimes")
        res = run_command(req, "book.reset_lifetime", {"book_code": code, "starting_capital_c": cap, "confirm": "RESET", "name": req.b("name")})
        body = ok_or_raise(res)
        return 200, body, ({"X-Replayed": "1"} if res.replayed else {})

    def book_onboard(req: Request):
        rt, code = req.rt, req.params[0]
        broker = str(req.b("broker") or "").upper()
        if broker not in config.PRIME_BROKERS:
            raise ApiError(400, "UNKNOWN_BROKER", f"{broker!r} is not a known prime broker", {"brokers": sorted(config.PRIME_BROKERS)})
        lid = lifetime_for_code(rt, code)
        need(rt, "lifetimes")
        res = run_command(req, "book.onboard_pb", {"lifetime_id": lid, "broker": broker})
        body = ok_or_raise(res)
        return 200, body, ({"X-Replayed": "1"} if res.replayed else {})

    def history(req: Request):
        return history_view(req, req.params[0], req.params[1])

    def export_transactions(req: Request):
        rows = txn_rows(req.rt, req.scope("ALL"), from_date=req.q("from"), to_date=req.q("to"), limit=req.q_int("limit", 5000, 1, 50000))
        return 200, transactions_csv(rows), {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="transactions.csv"'}

    def export_positions(req: Request):
        positions = req.rt.accounting.positions(req.scope("ALL"), snapshot=req.rt.marks.snapshot())
        return 200, positions_csv(positions), {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="positions.csv"'}

    def watchlists_get(req: Request):
        return watchlists_view(req)

    def watchlists_post(req: Request):
        res = run_command(req, "watchlist.add", req.payload("account_id", "name", "instrument_id"))
        ok_or_raise(res)
        return 200, watchlists_view(req), ({"X-Replayed": "1"} if res.replayed else {})

    def watchlists_delete(req: Request):
        res = run_command(req, "watchlist.remove", req.payload("account_id", "name", "instrument_id"))
        ok_or_raise(res)
        return 200, watchlists_view(req), ({"X-Replayed": "1"} if res.replayed else {})

    def dividends(req: Request):
        return dividends_view(req)

    r.add("GET", r"/api/accounts", accounts)
    r.add("POST", r"/api/accounts/pb", account_pb)
    r.add("PATCH", r"/api/accounts/([^/]+)", account_patch)
    r.add("GET", r"/api/accounts/([^/]+)", account_detail)
    r.add("GET", r"/api/accounts/([^/]+)/positions", account_positions)
    r.add("GET", r"/api/accounts/([^/]+)/positions/([^/]+)", position_detail)
    r.add("GET", r"/api/accounts/([^/]+)/orders", account_orders)
    r.add("GET", r"/api/accounts/([^/]+)/fills", account_fills)
    r.add("GET", r"/api/accounts/([^/]+)/transactions", account_transactions)
    r.add("GET", r"/api/accounts/([^/]+)/cash", account_cash)
    r.add("GET", r"/api/accounts/([^/]+)/markets", account_markets)
    r.add("GET", r"/api/portfolio", portfolio)
    r.add("GET", r"/api/treasury", treasury)
    r.add("POST", r"/api/treasury/deposit", lambda req: treasury_move(req, "treasury.deposit"))
    r.add("POST", r"/api/treasury/withdraw", lambda req: treasury_move(req, "treasury.withdraw"))
    r.add("POST", r"/api/transfers", transfers)
    r.add("GET", r"/api/accounting/balance-sheet", balance_sheet)
    r.add("GET", r"/api/accounting/pnl", pnl)
    r.add("GET", r"/api/accounting/trial-balance", trial)
    r.add("GET", r"/api/accounting/journal", journal)
    r.add("GET", r"/api/accounting/journal/([^/]+)", journal_entry)
    r.add("GET", r"/api/accounting/transactions", accounting_transactions)
    r.add("GET", r"/api/accounting/cash", accounting_cash)
    r.add("GET", r"/api/accounting/integrity", integrity)
    r.add("GET", r"/api/activity", activity)
    r.add("GET", r"/api/books", books)
    r.add("GET", r"/api/books/([^/]+)/lifetimes", book_lifetimes)
    r.add("POST", r"/api/books/([^/]+)/reset", book_reset)
    r.add("POST", r"/api/books/([^/]+)/onboard", book_onboard)
    r.add("GET", r"/api/history/([^/]+)/(.+)", history)
    r.add("GET", r"/api/export/transactions\.csv", export_transactions)
    r.add("GET", r"/api/export/positions\.csv", export_positions)
    r.add("GET", r"/api/watchlists", watchlists_get)
    r.add("POST", r"/api/watchlists", watchlists_post)
    r.add("DELETE", r"/api/watchlists", watchlists_delete)
    r.add("GET", r"/api/dividends", dividends)


# ---- views used by several routes -------------------------------------------------------------------------------------------
def lifetime_for_code(rt, code: str) -> str:
    with rt.db.reader() as conn:
        if _books.book_of(conn, code) is None:
            raise NotFound(f"book {code} does not exist")
        row = _books.open_lifetime(conn, code)
    if row is None:
        raise ApiError(404, "NO_OPEN_LIFETIME", f"book {code} has no open lifetime")
    return row["lifetime_id"]


def treasury_view(rt, lid: str) -> dict:
    """GET /api/treasury for one lifetime: the TreasuryView, advances by account, the capital history and the sweep log, the
    cash interest, the liquidity line; plus (P03) the shown figures the page needs without computing:
    - ``advances_view`` (BM-29): 'advanced to accounts' with 'NAV = capital + retained - advanced to accounts', the same words
      the balance sheet uses on the Treasury scope (``advances_convention``);
    - ``available_shown`` (BM-01 cross-item): Treasury available floored at zero with the signed figure beside it
      (``available_signed``; negative when the book's reserve is above the cash a sweep may move) and a sentence;
    - the capital history's ``memo`` (the text typed with the deposit / withdrawal, BM-24) and ``signed_amount`` (a withdrawal
      negative);
    - the sweep log's ``reason`` ('for fill F-000003 (BUY 20 MSFT)') and ``link`` (BM-23), ``account_name``."""
    tv = rt.accounting.treasury(lid)
    names, _bk = name_maps(rt)
    with rt.db.reader() as conn:
        hist = [dict(x) for x in conn.execute(
            "SELECT t.*, c.request_id, (SELECT l.memo FROM journal_lines l WHERE l.entry_id=t.entry_id AND l.gl='3000' ORDER BY l.line_no LIMIT 1) AS line_memo"
            " FROM transfers t LEFT JOIN commands c ON c.id=t.command_id WHERE t.kind IN ('INITIAL_CAPITAL','DEPOSIT','WITHDRAWAL')"
            " AND (t.to_account=? OR t.from_account=?) ORDER BY t.ts DESC, t.transfer_id DESC LIMIT 200", (tv.account_id, tv.account_id)).fetchall()]
        sweeps = [dict(x) for x in conn.execute(
            "SELECT t.*, (SELECT l.memo FROM journal_lines l WHERE l.entry_id=t.entry_id AND l.gl='1010' ORDER BY l.line_no LIMIT 1) AS line_memo FROM transfers t"
            " WHERE t.kind IN ('SWEEP_IN','SWEEP_OUT') AND (t.to_account=? OR t.from_account=?) ORDER BY t.ts DESC, t.transfer_id DESC LIMIT 200",
            (tv.account_id, tv.account_id)).fetchall()]
        for s in sweeps:
            s["direction"] = s["kind"]
            s["account_id"] = s["to_account"] if s["kind"] == "SWEEP_IN" else s["from_account"]
            s["account_name"] = names.get(s["account_id"], s["account_id"])
            if s["kind"] == "SWEEP_IN":
                s["reason"], s["link"] = link_reason(rt, conn, s.get("linked_type"), s.get("linked_id"))
                s["reason"] = s["reason"] or "funding shortfall"
            else:
                memo = str(s.get("line_memo") or "")
                s["reason"], s["link"] = (memo[len("sweep-out: "):] if memo.startswith("sweep-out: ") else memo) or "idle cash returned to Treasury", None
            s.pop("line_memo", None)
    for h in hist:
        h["kind_label"] = TRANSFER_WORDS.get(h["kind"], str(h["kind"]).lower())
        h["memo"] = h.pop("line_memo", None) or h["kind_label"]
        h["signed_amount_c"] = -int(h["amount_c"]) if h["kind"] == "WITHDRAWAL" else int(h["amount_c"])
    advances = [{"account_id": a, "name": names.get(a, a), "amount_c": v} for a, v in tv.advances_by_account.items()]
    avail = int(tv.available_c)
    av_note = ("Treasury cash a sweep may move now, after the book's reserve" if avail >= 0 else
               f"the reserve {fmt_money(int(tv.reserve_c))} is above the cash a sweep may move: nothing can be swept until cash arrives or the reserve is lowered")
    return {"treasury": treasury_out(tv), "advances": advances, "capital_history": hist, "sweep_log": sweeps, "interest": treasury_interest(rt, tv),
            "lifetime_id": lid, "liquidity": liquidity_out(rt.accounting.liquidity(f"{lid}:ALL")),
            "advances_view": advances_convention("TREASURY", -int(tv.advances_c)),
            "available_view": {"shown_c": max(0, avail), "signed_c": avail, "note": av_note}}


def _rows_or_none(conn, sql: str, params) -> List[dict]:
    """Rows of a product table that may not exist in this build (sec_loans before the lending module made it): [] then."""
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:   # noqa: BLE001 - sqlite3.OperationalError 'no such table / column': nothing of that kind exists
        return []


def cash_financing_view(rt, scope: str, snap) -> dict:
    """BM-13: Accounting > Cash & financing for a scope (the selected book's lifetime, ALL_BOOKS, one account, or an archived
    lifetime on History): every figure is a field the per-account views already serve, gathered across the scope's accounts -
    - ``cash``: one row per account and currency (``Accounting.cash``: settled, receivable, payable, reserved, available
      floored at zero, held at lender, margin, accrued) with the account / book names; ``totals`` per currency;
    - ``treasury``: per lifetime the Treasury's settled cash, the reserve and what a sweep may move (signed and floored);
    - ``ladder``: the pending settlements by due date; ``sweeps``: the last 100 sweep-ins / sweep-outs with what each paid for;
    - financing: open ``loans`` (lender, index, spread), stock ``borrows`` (lender, fee, collateral), ``repos``, securities
      ``lent`` and per-account ``margin`` (futures initial margin required / posted, option margin reserved, OTC IM / VM)."""
    names, books = name_maps(rt)
    with rt.db.reader() as conn:
        sc = rt.accounting._scope(conn, scope)
        accounts = [dict(a) for a in sc.accounts]
        aids = [a["account_id"] for a in accounts]
        wa, pa = _in("account_id", aids) if aids else ("1=0", [])
        loans = _rows_or_none(conn, f"SELECT * FROM loans WHERE status='OPEN' AND {wa} ORDER BY opened_date, loan_id", pa)
        borrows = borrows_shown(_rows_or_none(conn, f"SELECT * FROM borrows WHERE status='OPEN' AND {wa} ORDER BY opened_ts, borrow_id", pa))
        repos = _rows_or_none(conn, f"SELECT * FROM repos WHERE status='OPEN' AND {wa} ORDER BY opened_date, repo_id", pa)
        lent = _rows_or_none(conn, f"SELECT * FROM sec_loans WHERE status='OPEN' AND {wa} ORDER BY opened_ts, loan_id", pa)
        w1, p1 = _in("t.from_account", aids) if aids else ("1=0", [])
        w2, p2 = _in("t.to_account", aids) if aids else ("1=0", [])
        sweeps = [dict(r) for r in conn.execute(
            "SELECT t.*, (SELECT l.memo FROM journal_lines l WHERE l.entry_id=t.entry_id AND l.gl='1010' ORDER BY l.line_no LIMIT 1) AS line_memo FROM transfers t"
            f" WHERE t.kind IN ('SWEEP_IN','SWEEP_OUT') AND ({w1} OR {w2}) ORDER BY t.ts DESC, t.transfer_id DESC LIMIT 100", p1 + p2).fetchall()]
        ladder = []
        for r in conn.execute(f"SELECT s.settle_id, s.account_id, s.fill_id, s.fx_deal_id, s.leg, s.currency, s.amount_c, s.due_date, f.instrument_id, f.side, f.qty"
                              f" FROM settlements s LEFT JOIN fills f ON f.fill_id=s.fill_id WHERE s.status='PENDING' AND s.{wa} ORDER BY s.due_date, s.settle_id LIMIT 500",
                              pa).fetchall():
            d = {k: r[k] for k in ("settle_id", "account_id", "fill_id", "fx_deal_id", "leg", "currency", "amount_c", "due_date")}
            d["direction"] = "receive" if int(r["amount_c"]) > 0 else "pay"
            d["what"] = (f"{r['side']} {int(r['qty']):,} {_symbol(rt, r['instrument_id'])} ({r['fill_id']})" if r["fill_id"] and r["instrument_id"]
                         else (f"FX deal {r['fx_deal_id']}" if r["fx_deal_id"] else r["leg"]))
            ladder.append(d)
        for s in sweeps:
            s["direction"] = s["kind"]
            s["account_id"] = s["to_account"] if s["kind"] == "SWEEP_IN" else s["from_account"]
            if s["kind"] == "SWEEP_IN":
                s["reason"], s["link"] = link_reason(rt, conn, s.get("linked_type"), s.get("linked_id"))
            else:
                memo = str(s.get("line_memo") or "")
                s["reason"], s["link"] = (memo[len("sweep-out: "):] if memo.startswith("sweep-out: ") else memo) or "idle cash returned to Treasury", None
            s.pop("line_memo", None)
        for row in loans:
            row["counterparty_name"] = _counterparty_name(rt, conn, row.get("counterparty")) if row.get("counterparty") else row.get("lender")
        for row in repos:
            row["counterparty_name"] = _counterparty_name(rt, conn, row.get("counterparty")) if row.get("counterparty") else None
        for row in lent:
            row["counterparty_name"] = _counterparty_name(rt, conn, row.get("counterparty")) if row.get("counterparty") else None
    cash_rows: List[dict] = []
    totals: Dict[str, Dict[str, int]] = {}
    margin_rows: List[dict] = []
    for a in accounts:
        aid = a["account_id"]
        for ccy, cv in rt.accounting.cash(aid, snapshot=snap).items():
            figures = (cv.settled_c, cv.receivable_c, cv.payable_c, cv.reserved_c, cv.available_c, cv.held_at_lender_c, cv.margin_c, cv.otc_margin_c, cv.accrued_c)
            if ccy != "USD" and not any(figures):
                continue
            row = cv.to_dict()
            row.pop("definition", None)
            row["account_name"] = names.get(aid, aid)
            row["account_kind"] = a.get("kind")
            row.update(book_fields(aid, books))
            cash_rows.append(row)
            t = totals.setdefault(ccy, {"settled_c": 0, "receivable_c": 0, "payable_c": 0, "reserved_c": 0, "available_c": 0, "held_at_lender_c": 0, "margin_c": 0})
            for k in t:
                t[k] += int(getattr(cv, k) or 0)
        ob = rt.accounting.obligations(aid)
        m = dict(ob.margin or {})
        if any(int(v or 0) for k, v in m.items() if k.endswith("_c")) or int(ob.collateral_c or 0):
            mr = {k: v for k, v in m.items() if k.endswith("_c")}
            mr.update({"account_id": aid, "account_name": names.get(aid, aid), "collateral_held_at_lender_c": int(ob.collateral_c or 0),
                       "options_margin_mode": m.get("options_margin_mode")})
            mr.update(book_fields(aid, books))
            margin_rows.append(mr)
    tre_rows = []
    for lid in sc.lifetime_ids:
        try:
            tv = rt.accounting.treasury(lid, snapshot=snap)
        except Exception:   # noqa: BLE001 - a lifetime without a Treasury account (never in this build) is skipped, not invented
            continue
        code = _books.book_code_of(lid)
        tre_rows.append({"lifetime_id": lid, "book_code": code, "book_name": books.get(code, code), "account_id": tv.account_id,
                         "settled_by_ccy": money_map(tv.cash_by_ccy), "reserve_c": int(tv.reserve_c), "available_signed_c": int(tv.available_c),
                         "available_c": max(0, int(tv.available_c)), "advanced_c": int(tv.advances_c)})
    for rows in (loans, borrows, repos, lent, ladder, sweeps):
        for r in rows:
            if r.get("account_id"):
                r["account_name"] = names.get(r["account_id"], r["account_id"])
                r.update(book_fields(r["account_id"], books))
            if r.get("instrument_id"):
                r["symbol"] = _symbol(rt, r["instrument_id"])
    return {"scope": scope, "read_only": bool(sc.archived), "cash": cash_rows,
            "totals": [dict(v, currency=k) for k, v in sorted(totals.items(), key=lambda kv: (kv[0] != "USD", kv[0]))],
            "treasury": tre_rows, "ladder": ladder, "sweeps": sweeps, "loans": loans, "borrows": borrows, "repos": repos, "lent": lent, "margin": margin_rows,
            "available_definition": AVAILABLE_TEXT, "as_of": snap.as_of() if hasattr(snap, "as_of") else None}


def journal_entry_view(rt, entry_id: str) -> dict:
    try:
        eid = int(entry_id)
    except ValueError:
        raise NotFound(f"journal entry {entry_id!r} does not exist")
    with rt.db.reader() as conn:
        e = conn.execute("SELECT * FROM journal_entries WHERE entry_id=?", (eid,)).fetchone()
        if e is None:
            raise NotFound(f"journal entry {eid} does not exist")
        lines = []
        for l in conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (eid,)).fetchall():
            d = dict(l)
            amt = int(d["amount_c"])
            d["debit_c"], d["credit_c"] = (amt if amt > 0 else None), (-amt if amt < 0 else None)
            d["gl_name"] = CHART[d["gl"]].name if d["gl"] in CHART else d["gl"]
            lines.append(d)
        cmd = conn.execute("SELECT id, request_id, kind, actor, started_ts, finished_ts, duration_ms, http_status, app_version, state_version FROM commands WHERE id=?",
                           (e["command_id"],)).fetchone()
        linked = None
        if e["ref_type"] == "fill" and e["ref_id"]:
            linked = conn.execute("SELECT * FROM fills WHERE fill_id=?", (e["ref_id"],)).fetchone()
        elif e["ref_type"] == "transfer" and e["ref_id"]:
            linked = conn.execute("SELECT * FROM transfers WHERE transfer_id=?", (e["ref_id"],)).fetchone()
        linked = dict(linked) if linked is not None else None
        if linked and "quote_json" in linked:
            try:
                linked["quote"] = json.loads(linked.pop("quote_json") or "null")
            except ValueError:
                linked["quote"] = None
    return {"entry": dict(e), "lines": lines, "command": dict(cmd) if cmd else None, "linked": linked, "ref_type": e["ref_type"], "ref_id": e["ref_id"]}


def history_view(req: Request, lid: str, rest: str):
    """``GET /api/history/{lifetime}/...`` — the read routes under the archived scope with ``read_only: true``."""
    rt = req.rt
    with rt.db.reader() as conn:
        lrow = _books.lifetime_row(conn, lid)
    if lrow is None:
        raise NotFound(f"lifetime {lid} does not exist")
    lrow = dict(lrow)
    scope = f"{lid}:ALL"
    meta = {"read_only": True, "scope": scope, "frozen_at": ny_of(int(lrow["ended_ts"])).date().isoformat() if lrow.get("ended_ts") else None,
            "lifetime": {"lifetime_id": lid, "name": lrow["name"], "status": lrow["status"], "started": ny_of(int(lrow["started_ts"])).date().isoformat(),
                         "ended": ny_of(int(lrow["ended_ts"])).date().isoformat() if lrow.get("ended_ts") else None, "reason": lrow.get("reason"),
                         "starting_capital_c": int(lrow["starting_capital_c"]), "final_nav_c": lrow.get("final_nav_c"), "final_pnl_c": lrow.get("final_pnl_c"),
                         "final_realised_c": lrow.get("final_realised_c"), "final_quality": lrow.get("final_quality")}}
    rest = rest.strip("/")
    snap = rt.marks.snapshot()
    hscope = req.q("scope") or ""
    # an account of THIS lifetime may be the scope of the archived accounting tabs (the scope selector); anything else is the lifetime
    sub = hscope if (_ACCOUNT_RE.match(hscope) and lifetime_of(hscope) == lid) else scope
    if rest == "positions":
        return dict(meta, positions=positions_view(rt, scope, snap))
    if rest == "portfolio":
        # the same shape as the live Portfolio (BM-31: by_account rows with names; by_class a list; weights of the final NAV)
        return dict(meta, **{k: v for k, v in portfolio_view(rt, scope, snap, ("ITD",), with_day=False).items() if k not in ("scope", "read_only")})
    if rest == "accounting/balance-sheet":
        # BM-08 / SHELL-06: flat, like the live route and the trial balance (the page reads the same fields everywhere)
        return dict(meta, **{k: v for k, v in balance_sheet_out(rt.accounting.balance_sheet(sub, snapshot=snap), scope_kind(sub)).items() if k != "scope"})
    if rest == "accounting/pnl":
        period = (req.q("period") or "ITD").upper()
        if period not in ("TODAY", "MTD", "YTD", "ITD"):
            raise ApiError(400, "BAD_REQUEST", "period must be TODAY, MTD, YTD or ITD")
        return dict(meta, **{k: v for k, v in pnl_out(rt.accounting.pnl(sub, period, snapshot=snap)).items() if k != "scope"})
    if rest == "accounting/trial-balance":
        return dict(meta, **{k: v for k, v in trial_balance(rt, sub).items() if k != "scope"})
    if rest in ("accounting/journal", "accounting/transactions"):
        kinds = [k.strip().upper() for k in (req.q("kinds") or "").split(",") if k.strip()] or None
        rows = txn_rows(rt, sub, from_date=req.q("from"), to_date=req.q("to"), kinds=kinds, instrument_id=req.q("instrument"),
                        limit=req.q_int("limit", 500, 1, 5000), offset=req.q_int("offset", 0, 0, 1_000_000))
        gl = req.q("gl")
        if gl:
            rows = [x for x in rows if any(l.get("gl") == gl for l in x["lines"])]
        if rest == "accounting/transactions" and (req.q("format") or "json").lower() == "csv":
            return 200, transactions_csv(rows), {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'attachment; filename="{lid}-transactions.csv"'}
        return dict(meta, entries=rows, rows=rows, count=len(rows))
    if rest.startswith("accounting/journal/"):
        return dict(meta, **journal_entry_view(rt, rest.split("/", 2)[2]))
    if rest == "accounting/cash":
        return dict(meta, **{k: v for k, v in cash_financing_view(rt, sub, snap).items() if k not in ("scope", "read_only")})
    if rest == "accounting/integrity":
        # a check of the whole database shown for this lifetime (a run limited to one lifetime would re-record the holds of that
        # lifetime only); ``frozen`` = the verdict stored when the lifetime was closed
        view = integrity_view(rt.accounting.integrity("ledger"), lid)
        try:
            frozen = json.loads(lrow.get("integrity_json") or "null")
        except ValueError:
            frozen = None
        view["frozen"] = {"ok": frozen.get("ok"), "checked_at": frozen.get("checked_at"), "mode": frozen.get("mode")} if isinstance(frozen, dict) else None
        return dict(meta, **view)
    if rest == "activity":
        tokens = activity_tokens(req.q("type"))
        out = dict(meta, rows=activity_rows(rt, limit=req.q_int("limit", 200, 1, 500), lifetime_ids=[lid], types=tokens), types=sorted(tokens),
                   categories=list(ACTIVITY_CATEGORIES))
        out.update(activity_extras(rt, tokens, lifetime_ids=[lid]))
        return out
    if rest == "export/transactions.csv":
        rows = txn_rows(rt, scope, limit=50000)
        return 200, transactions_csv(rows), {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'attachment; filename="{lid}-transactions.csv"'}
    if rest == "export/positions.csv":
        return 200, positions_csv(rt.accounting.positions(scope, snapshot=snap)), {"Content-Type": "text/csv; charset=utf-8"}
    raise NotFound(f"no history route {rest!r}", {"routes": list(HISTORY_PATHS)})


def watchlists_view(req: Request) -> dict:
    rt = req.rt
    lid = None
    try:
        lid = req.lifetime()
    except (ScopeError, ApiError):
        lid = None
    with rt.db.reader() as conn:
        if req.q("account_id"):
            rows = conn.execute("SELECT * FROM watchlists WHERE account_id=? ORDER BY name, position, id", (req.q("account_id"),)).fetchall()
        elif lid:
            rows = conn.execute("SELECT w.* FROM watchlists w JOIN accounts a ON a.account_id=w.account_id WHERE a.lifetime_id=? ORDER BY w.account_id, w.name, w.position, w.id", (lid,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM watchlists ORDER BY account_id, name, position, id").fetchall()
        held = {(r[0], r[1]): int(r[2]) for r in conn.execute("SELECT account_id, instrument_id, qty FROM v_positions WHERE qty != 0").fetchall()}
    lists: Dict[tuple, dict] = {}
    ids = []
    for r in rows:
        key = (r["account_id"], r["name"])
        lst = lists.setdefault(key, {"name": r["name"], "account_id": r["account_id"], "items": []})
        inst = rt.instruments.get(r["instrument_id"])
        lst["items"].append({"instrument_id": r["instrument_id"], "symbol": inst.symbol if inst else r["instrument_id"], "name": inst.name if inst else None,
                             "kind": inst.kind if inst else None, "support": inst.support if inst else None, "position": held.get((r["account_id"], r["instrument_id"]), 0),
                             "rank": int(r["position"]), "quote": None, "account_kinds": list(inst.account_kinds) if inst else []})
        ids.append(r["instrument_id"])
    cache = getattr(rt, "cache", None)
    if cache is not None and ids:
        cache.subscribe(ids, 90, "WATCH")
        recs = cache.get_many(ids)
        for lst in lists.values():
            for it in lst["items"]:
                it["quote"] = quote_dict(recs.get(it["instrument_id"]), req.now)
    return {"lists": list(lists.values()), "count": sum(len(l["items"]) for l in lists.values())}


def dividends_view(req: Request) -> List[dict]:
    """Upcoming / recent ex-dates for held names from the ``dividends`` table (fed by the worker's ``dividends.upsert``):
    amount and source as recorded; ``pay_date`` = the recorded one or the convention ex-date + 15 business days (labelled)."""
    rt = req.rt
    today = rt.clock.today()
    lo, hi = (today - timedelta(days=60)).isoformat(), (today + timedelta(days=120)).isoformat()
    aid = req.q("account_id")
    # the account check and the lifetime lookup read on their own reader BEFORE this one (never a second pooled reader inside a held one)
    if aid:
        _account_or_404(rt, aid)
    lid = None if aid else req.lifetime()
    with rt.db.reader() as conn:
        if aid:
            rows = conn.execute("SELECT d.*, v.account_id AS account_id, v.qty AS qty FROM dividends d JOIN v_positions v ON v.instrument_id=d.instrument_id"
                                " WHERE v.account_id=? AND v.qty != 0 AND d.ex_date BETWEEN ? AND ? ORDER BY d.ex_date DESC, d.instrument_id", (aid, lo, hi)).fetchall()
        else:
            rows = conn.execute("SELECT d.*, v.account_id AS account_id, v.qty AS qty FROM dividends d JOIN v_positions v ON v.instrument_id=d.instrument_id"
                                " JOIN accounts a ON a.account_id=v.account_id WHERE a.lifetime_id=? AND v.qty != 0 AND d.ex_date BETWEEN ? AND ?"
                                " ORDER BY d.ex_date DESC, d.instrument_id", (lid, lo, hi)).fetchall()
    out = []
    for r in rows:
        amt = Decimal(str(r["amount"]))
        qty = int(r["qty"])
        ex = date.fromisoformat(r["ex_date"])
        pay = r["pay_date"] or rt.cal.add_business_days(ex, 15).isoformat()
        out.append({"instrument_id": r["instrument_id"], "symbol": _symbol(rt, r["instrument_id"]), "account_id": r["account_id"], "ex_date": r["ex_date"], "amount": str(amt),
                    "pay_date": pay, "pay_date_convention": r["pay_date"] is None, "source": r["source"], "fetched_ts": r["fetched_ts"], "position_qty": qty,
                    "expected_c": to_c(amt * qty), "kind": "dividend" if qty > 0 else "payment in lieu", "upcoming": r["ex_date"] >= today.isoformat(),
                    "convention": "cash on the recorded pay date, else ex-date + 15 business days (DIVIDEND)"})
    return out


__all__ = ["register_routes", "account_rows", "account_row", "order_full", "fill_rows", "txn_rows", "transactions_csv", "positions_csv", "trial_balance",
           "integrity_view", "activity_rows", "books_view", "lifetime_rows", "treasury_view", "journal_entry_view", "history_view", "watchlists_view", "dividends_view",
           "positions_view", "pnl_block", "by_class", "lifetime_for_code", "money", "money_map", "nav_out", "liquidity_out", "treasury_out", "balance_sheet_out",
           "treasury_interest", "name_maps", "position_row", "portfolio_view", "portfolio_footer", "pnl_out", "advances_convention", "available_by_currency",
           "activity_extras", "activity_tokens", "link_reason", "cash_financing_view", "clean_memo", "ACTIVITY_CATEGORIES"]
