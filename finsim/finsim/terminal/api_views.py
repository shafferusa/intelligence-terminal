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
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from . import books as _books, config, tables
from .accounting import AVAILABLE_TEXT, ScopeError
from .api import (ApiError, NotBuilt, NotFound, PACKAGES, Request, encode, mark_dict, need, ok_or_raise, quote_dict, run_command, OPEN_ORDER_STATUSES)
from .clock import accounting_day, ny_of
from .ledger import CHART
from .quotes.rates import UNIT_FRACTION, fraction_to_pct, treasury_cash_rate
from .units import fmt_money, from_c, to_c

HISTORY_PATHS = ("positions", "portfolio", "accounting/balance-sheet", "accounting/pnl", "accounting/trial-balance", "accounting/journal", "accounting/integrity",
                 "activity", "export/transactions.csv", "export/positions.csv")


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


def balance_sheet_out(bs) -> dict:
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
    d["memo"] = memo
    return d


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
        r["working_orders"] = wo.get(aid, 0)
        r["pending_settlements"] = ps.get(aid, 0)
        r["attention_count"] = at.get(aid, 0)
        r["nav_quality"] = nav.quality
        out.append(r)
    return out


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
    if with_events:
        d["events"] = [dict(e) for e in conn.execute("SELECT ts, from_status, to_status, note FROM order_events WHERE order_id=? ORDER BY id", (order_id,)).fetchall()]
        fids = [f["fill_id"] for f in d.get("fills", [])]
        d["entries"] = []
        for fid in fids:
            for e in conn.execute("SELECT entry_id, kind, memo, ts, business_date FROM journal_entries WHERE ref_type='fill' AND ref_id=?", (fid,)).fetchall():
                d["entries"].append(dict(e))
    return d


def fill_rows(rt, conn, where: str, params: list, now: int, limit: int = 500) -> List[dict]:
    out = []
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
        out.append(d)
    return out


def txn_rows(rt, scope: str, *, from_date=None, to_date=None, kinds=None, instrument_id=None, limit=500, offset=0) -> List[dict]:
    """``Accounting.transactions`` rows with every line expanded (debit / credit, GL name)."""
    rows = rt.accounting.transactions(scope, from_date=from_date, to_date=to_date, kinds=kinds, instrument_id=instrument_id, limit=limit, offset=offset)
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
            lines.append(l)
        d["lines"] = lines
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


def pnl_block(rt, scope: str, snap, periods=("TODAY", "MTD", "YTD", "ITD")) -> dict:
    out = {}
    for p in periods:
        try:
            out[p] = rt.accounting.pnl(scope, p, snapshot=snap)
        except ScopeError:
            raise
        except Exception as e:   # noqa: BLE001 — one period's failure must not hide the others; it is reported in place
            out[p] = {"period": p, "scope": scope, "error": f"{type(e).__name__}: {e}"}
            if rt.log:
                rt.log.exception("pnl %s %s failed", scope, p)
    return out


def positions_view(rt, scope: str, snap, kind: Optional[str] = None) -> List[dict]:
    out = []
    for p in rt.accounting.positions(scope, snapshot=snap):
        if kind and p.kind != kind:
            continue
        d = p.to_dict()
        d["mark"] = mark_dict(p.mark)
        out.append(d)
    return out


def by_class(positions) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for p in positions:
        b = out.setdefault(p.klass, {"n": 0, "mv_base_c": 0, "unrealised_base_c": 0, "cost_c": 0, "missing": 0})
        b["n"] += 1
        b["cost_c"] += int(p.cost_c)
        if p.mv_base_c is None:
            b["missing"] += 1
        else:
            b["mv_base_c"] += int(p.mv_base_c)
            b["unrealised_base_c"] += int(p.unrealised_base_c or 0)
    return out


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


def integrity_view(report) -> dict:
    d = report.to_dict()
    checks: Dict[str, dict] = {}
    for p in d.get("problems", []):
        c = checks.setdefault(p["id"], {"code": p["id"], "ok": False, "severity": p["severity"], "count": 0, "message": p.get("detail", ""), "scopes": []})
        c["count"] += 1
        if p.get("scope") and len(c["scopes"]) < 10:
            c["scopes"].append(p["scope"])
    d["checks"] = [checks[k] for k in sorted(checks)]
    d["duration_ms"] = d.get("ms")
    return d


def activity_rows(rt, *, limit: int = 100, account: Optional[str] = None, types: Optional[set] = None, since: Optional[int] = None,
                  lifetime_id: Optional[str] = None) -> List[dict]:
    """§15 ``GET /api/activity``: events, fills, transfers, job results and attention merged newest first."""
    rows: List[dict] = []
    lim = int(limit)
    with rt.db.reader() as conn:
        acct_ids = None
        if lifetime_id:
            acct_ids = {r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=?", (lifetime_id,)).fetchall()}

        def keep(aid: Optional[str]) -> bool:
            if account and aid != account:
                return False
            if acct_ids is not None and aid not in acct_ids:
                return False
            return True

        def want(t: str) -> bool:
            return not types or t in types

        if want("event"):
            for r in conn.execute("SELECT id, ts, type, account_id, instrument_id, ref_type, ref_id, payload_json FROM events WHERE (? IS NULL OR ts >= ?) ORDER BY ts DESC, id DESC LIMIT ?",
                                  (since, since, lim)).fetchall():
                if not keep(r["account_id"]):
                    continue
                try:
                    pl = json.loads(r["payload_json"] or "{}")
                except ValueError:
                    pl = {}
                bits = [f"{k} {pl[k]}" for k in ("side", "qty", "price") if pl.get(k) is not None]
                amt = pl.get("amount_c")
                if isinstance(amt, int) and not isinstance(amt, bool):
                    bits.append(f"amount {pl['currency'] + ' ' if pl.get('currency') else ''}{fmt_money(amt)}")     # cents -> money text, never "amount_c 100000000"
                elif pl.get("currency") is not None:
                    bits.append(f"currency {pl['currency']}")
                if pl.get("reason") is not None:
                    bits.append(f"reason {pl['reason']}")
                rows.append({"ts": int(r["ts"]), "type": "event", "event": r["type"], "severity": "INFO", "account_id": r["account_id"], "instrument_id": r["instrument_id"],
                             "summary": f"{r['type']} {r['ref_type'] or ''} {r['ref_id'] or ''}".strip() + (": " + ", ".join(bits) if bits else ""),
                             "ref_type": r["ref_type"], "ref_id": r["ref_id"], "id": int(r["id"])})
        if want("fill"):
            for r in conn.execute("SELECT fill_id, ts, account_id, instrument_id, side, qty, price, price_basis, origin FROM fills WHERE (? IS NULL OR ts >= ?) ORDER BY ts DESC LIMIT ?",
                                  (since, since, lim)).fetchall():
                if not keep(r["account_id"]):
                    continue
                rows.append({"ts": int(r["ts"]), "type": "fill", "severity": "INFO", "account_id": r["account_id"], "instrument_id": r["instrument_id"],
                             "summary": f"{r['fill_id']} {r['side']} {int(r['qty']):,} {_symbol(rt, r['instrument_id'])} @ {r['price']} ({r['price_basis']}, {r['origin']})",
                             "ref_type": "fill", "ref_id": r["fill_id"]})
        if want("transfer"):
            for r in conn.execute("SELECT transfer_id, ts, kind, from_account, to_account, currency, amount_c, linked_type, linked_id FROM transfers WHERE (? IS NULL OR ts >= ?)"
                                  " ORDER BY ts DESC LIMIT ?", (since, since, lim)).fetchall():
                aid = r["to_account"] or r["from_account"]
                if not (keep(r["to_account"]) or keep(r["from_account"])):
                    continue
                link = f" for {r['linked_type']} {r['linked_id']}" if r["linked_type"] else ""
                rows.append({"ts": int(r["ts"]), "type": "transfer", "severity": "INFO", "account_id": aid, "instrument_id": None,
                             "summary": f"{r['transfer_id']} {r['kind']} {r['currency']} {fmt_money(int(r['amount_c']))} {r['from_account'] or 'external'} -> {r['to_account'] or 'external'}{link}",
                             "ref_type": "transfer", "ref_id": r["transfer_id"]})
        if want("job") and acct_ids is None and not account:
            for r in conn.execute("SELECT job, key, run_ts, status, note FROM jobs_done WHERE (? IS NULL OR run_ts >= ?) ORDER BY run_ts DESC LIMIT ?", (since, since, lim)).fetchall():
                rows.append({"ts": int(r["run_ts"]), "type": "job", "severity": "INFO" if r["status"] == "DONE" else "WARN", "account_id": None, "instrument_id": None,
                             "summary": f"{r['job']} {r['key']}: {r['status']}" + (f" - {r['note']}" if r["note"] else ""), "ref_type": "job", "ref_id": f"{r['job']}:{r['key']}"})
        if want("attention"):
            for r in conn.execute("SELECT id, ts, severity, source, code, message, account_id, ref_type, ref_id, resolved_ts FROM attention WHERE (? IS NULL OR ts >= ?)"
                                  " ORDER BY ts DESC, id DESC LIMIT ?", (since, since, lim)).fetchall():
                if not keep(r["account_id"]) and r["account_id"] is not None:
                    continue
                if (account or acct_ids is not None) and r["account_id"] is None:
                    continue
                rows.append({"ts": int(r["ts"]), "type": "attention", "severity": r["severity"], "account_id": r["account_id"], "instrument_id": None,
                             "summary": f"{r['code']}: {r['message']}", "ref_type": "attention", "ref_id": str(r["id"]), "resolved_ts": r["resolved_ts"], "source": r["source"]})
    rows.sort(key=lambda x: (-int(x["ts"]), str(x.get("ref_id") or "")))
    for x in rows:
        x["ts_ny"] = ny_of(int(x["ts"])).strftime("%Y-%m-%d %H:%M:%S")
    return rows[:lim]


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
            borrows = [dict(b) for b in conn.execute("SELECT * FROM borrows WHERE account_id=? AND instrument_id=? ORDER BY opened_ts DESC", (aid, iid)).fetchall()]
            marks = [dict(m) for m in conn.execute("SELECT * FROM marks WHERE instrument_id=? ORDER BY mark_date DESC, kind LIMIT 60", (iid,)).fetchall()]
        d = pos.to_dict() if pos is not None else None
        if d is not None:
            d["mark"] = mark_dict(pos.mark)
        return {"position": d, "instrument": inst.to_dict() if inst else None, "lots": rt.accounting.lots(aid, iid), "fills": fills, "entries": entries,
                "borrow": borrows[0] if borrows and borrows[0].get("status") == "OPEN" else None, "borrows": borrows, "marks_history": marks,
                "mark": mark_dict(snap.mark(iid)) if inst else None}

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
        with rt.db.reader() as conn:
            transfers = [dict(x) for x in conn.execute("SELECT * FROM transfers WHERE from_account=? OR to_account=? ORDER BY ts DESC, transfer_id DESC LIMIT 100", (aid, aid)).fetchall()]
        ladder = [dict(s, within_5bd=str(s["due_date"]) <= horizon) for s in ob.settlements]
        return {"account_id": aid, "account": row, "cash": cash, "buying_power": {ccy: rt.accounting.buying_power(aid, ccy, snapshot=snap) for ccy in cash},
                "transfers": transfers, "ladder": ladder, "ladder_horizon": horizon, "loans": ob.loans, "borrows": ob.borrows, "margin": ob.margin,
                "collateral": {"held_at_lender_c": ob.collateral_c}, "otc": ob.otc, "liquidity": liquidity_out(rt.accounting.liquidity(aid, snapshot=snap)),
                "available_definition": AVAILABLE_TEXT}

    def account_markets(req: Request):
        from .api_products import markets_view
        return markets_view(req, req.params[0], req.q("tab"))

    def portfolio(req: Request):
        rt = req.rt
        scope = req.scope("ALL")
        snap = rt.marks.snapshot()
        nav = rt.accounting.nav(scope, snapshot=snap)
        positions = rt.accounting.positions(scope, snapshot=snap)
        pv = []
        for p in positions:
            d = p.to_dict()
            d["mark"] = mark_dict(p.mark)
            pv.append(d)
        with rt.db.reader() as conn:
            names = {r[0]: r[1] for r in conn.execute("SELECT account_id, name FROM accounts").fetchall()}
        return {"scope": scope, "nav": nav_out(nav), "pnl": pnl_block(rt, scope, snap), "by_account": [{"account_id": a, "name": names.get(a, a), "nav_c": v} for a, v in nav.by_account.items()],
                "by_class": by_class(positions), "positions": pv, "marks_partial": nav.partial, "missing_marks": nav.missing, "as_of": nav.as_of,
                "liquidity": liquidity_out(rt.accounting.liquidity(scope, snapshot=snap)), "lifetime_ids": nav.lifetime_ids, "read_only": nav.archived}

    def treasury(req: Request):
        rt = req.rt
        lid = req.lifetime()
        return treasury_view(rt, lid)

    def treasury_move(req: Request, kind: str):
        rt = req.rt
        lid = req.b("lifetime_id") or req.lifetime()
        payload = {"lifetime_id": lid, "amount_c": req.amount_c(), "currency": str(req.b("currency") or "USD").upper(), "memo": req.b("memo")}
        payload = {k: v for k, v in payload.items() if v is not None}
        res = run_command(req, kind, payload)
        body = ok_or_raise(res)
        with rt.db.reader() as conn:
            tr = conn.execute("SELECT * FROM transfers WHERE transfer_id=?", (body.get("transfer_id"),)).fetchone()
        return 200, {"transfer": dict(tr) if tr else body, "treasury": treasury_out(rt.accounting.treasury(lid)), "result": body}, ({"X-Replayed": "1"} if res.replayed else {})

    def transfers(req: Request):
        rt = req.rt
        payload = {"from_account": req.b("from_account"), "to_account": req.b("to_account"), "currency": str(req.b("currency") or "USD").upper(),
                   "amount_c": req.amount_c(), "memo": req.b("memo")}
        payload = {k: v for k, v in payload.items() if v is not None}
        res = run_command(req, "treasury.transfer", payload)
        body = ok_or_raise(res)
        snap = rt.marks.snapshot()
        with rt.db.reader() as conn:
            tr = conn.execute("SELECT * FROM transfers WHERE transfer_id=?", (body.get("transfer_id"),)).fetchone()
        return 200, {"transfer": dict(tr) if tr else body, "from_cash": rt.accounting.cash(payload["from_account"], snapshot=snap),
                     "to_cash": rt.accounting.cash(payload["to_account"], snapshot=snap), "result": body}, ({"X-Replayed": "1"} if res.replayed else {})

    def balance_sheet(req: Request):
        return balance_sheet_out(req.rt.accounting.balance_sheet(req.scope("ALL"), snapshot=req.rt.marks.snapshot()))

    def pnl(req: Request):
        period = (req.q("period") or "TODAY").upper()
        if period not in ("TODAY", "MTD", "YTD", "ITD"):
            raise ApiError(400, "BAD_REQUEST", "period must be TODAY, MTD, YTD or ITD")
        return req.rt.accounting.pnl(req.scope("ALL"), period, snapshot=req.rt.marks.snapshot())

    def trial(req: Request):
        return trial_balance(req.rt, req.scope("ALL"))

    def journal(req: Request):
        rows = txn_rows(req.rt, req.scope("ALL"), **_txn_args(req))
        gl = req.q("gl")
        if gl:
            rows = [x for x in rows if any(l.get("gl") == gl for l in x["lines"])]
        return {"entries": rows, "count": len(rows)}

    def journal_entry(req: Request):
        return journal_entry_view(req.rt, req.params[0])

    def integrity(req: Request):
        mode = (req.q("mode") or "ledger").lower()
        if mode not in ("ledger", "full"):
            raise ApiError(400, "BAD_REQUEST", "mode must be ledger or full")
        lid = req.q("lifetime")
        return integrity_view(req.rt.accounting.integrity(mode, lifetime_id=lid, snapshot=req.rt.marks.snapshot() if mode == "full" else None))

    def activity(req: Request):
        types = {t.strip().lower() for t in (req.q("type") or "").split(",") if t.strip()} or None
        since = req.q_int("since", 0, 0, 10**12) or None
        return {"rows": activity_rows(req.rt, limit=req.q_int("limit", 100, 1, 500), account=req.q("account"), types=types, since=since)}

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
    tv = rt.accounting.treasury(lid)
    with rt.db.reader() as conn:
        names = {r[0]: r[1] for r in conn.execute("SELECT account_id, name FROM accounts WHERE lifetime_id=?", (lid,)).fetchall()}
        hist = [dict(x) for x in conn.execute("SELECT t.*, c.request_id FROM transfers t LEFT JOIN commands c ON c.id=t.command_id WHERE t.kind IN ('INITIAL_CAPITAL','DEPOSIT','WITHDRAWAL')"
                                              " AND (t.to_account=? OR t.from_account=?) ORDER BY t.ts DESC, t.transfer_id DESC LIMIT 200", (tv.account_id, tv.account_id)).fetchall()]
        sweeps = [dict(x) for x in conn.execute("SELECT * FROM transfers WHERE kind IN ('SWEEP_IN','SWEEP_OUT') AND (to_account=? OR from_account=?) ORDER BY ts DESC, transfer_id DESC LIMIT 200",
                                                (tv.account_id, tv.account_id)).fetchall()]
    advances = [{"account_id": a, "name": names.get(a, a), "amount_c": v} for a, v in tv.advances_by_account.items()]
    for s in sweeps:
        s["direction"] = s["kind"]
        s["account_id"] = s["to_account"] if s["kind"] == "SWEEP_IN" else s["from_account"]
    return {"treasury": treasury_out(tv), "advances": advances, "capital_history": hist, "sweep_log": sweeps, "interest": treasury_interest(rt, tv),
            "lifetime_id": lid, "liquidity": liquidity_out(rt.accounting.liquidity(f"{lid}:ALL"))}


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
    if rest == "positions":
        return dict(meta, positions=positions_view(rt, scope, snap))
    if rest == "portfolio":
        nav = rt.accounting.nav(scope, snapshot=snap)
        positions = rt.accounting.positions(scope, snapshot=snap)
        pv = []
        for p in positions:
            d = p.to_dict()
            d["mark"] = mark_dict(p.mark)
            pv.append(d)
        return dict(meta, nav=nav_out(nav), pnl=pnl_block(rt, scope, snap, ("ITD",)), by_account=[{"account_id": a, "nav_c": v} for a, v in nav.by_account.items()],
                    by_class=by_class(positions), positions=pv, marks_partial=nav.partial)
    if rest == "accounting/balance-sheet":
        return dict(meta, balance_sheet=balance_sheet_out(rt.accounting.balance_sheet(scope, snapshot=snap)))
    if rest == "accounting/pnl":
        period = (req.q("period") or "ITD").upper()
        if period not in ("TODAY", "MTD", "YTD", "ITD"):
            raise ApiError(400, "BAD_REQUEST", "period must be TODAY, MTD, YTD or ITD")
        return dict(meta, pnl=rt.accounting.pnl(scope, period, snapshot=snap))
    if rest == "accounting/trial-balance":
        return dict(meta, **trial_balance(rt, scope))
    if rest == "accounting/journal":
        kinds = [k.strip().upper() for k in (req.q("kinds") or "").split(",") if k.strip()] or None
        rows = txn_rows(rt, scope, from_date=req.q("from"), to_date=req.q("to"), kinds=kinds, instrument_id=req.q("instrument"),
                        limit=req.q_int("limit", 500, 1, 5000), offset=req.q_int("offset", 0, 0, 1_000_000))
        return dict(meta, entries=rows, count=len(rows))
    if rest.startswith("accounting/journal/"):
        return dict(meta, **journal_entry_view(rt, rest.split("/", 2)[2]))
    if rest == "accounting/integrity":
        return dict(meta, **integrity_view(rt.accounting.integrity("ledger", lifetime_id=lid)))
    if rest == "activity":
        return dict(meta, rows=activity_rows(rt, limit=req.q_int("limit", 200, 1, 500), lifetime_id=lid))
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
                             "rank": int(r["position"]), "quote": None})
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
    with rt.db.reader() as conn:
        if aid:
            _account_or_404(rt, aid)
            rows = conn.execute("SELECT d.*, v.account_id AS account_id, v.qty AS qty FROM dividends d JOIN v_positions v ON v.instrument_id=d.instrument_id"
                                " WHERE v.account_id=? AND v.qty != 0 AND d.ex_date BETWEEN ? AND ? ORDER BY d.ex_date DESC, d.instrument_id", (aid, lo, hi)).fetchall()
        else:
            lid = req.lifetime()
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
           "treasury_interest"]
