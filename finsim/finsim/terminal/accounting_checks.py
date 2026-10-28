"""The ledger-level integrity set L1–L25 (TERMINAL_DESIGN.md §6.8) — exact, no marks, failure = error.

Every check is a pure function of a connection and a filter.  ``ledger_checks`` runs them in order and never lets a
check's own exception hide: a crashing check becomes a Problem of its own id.  Filters: ``accounts`` (a set of account
ids, None = whole database), ``entries`` (entry ids touched by one command), ``lifetimes`` (resolved to their
accounts), ``closed_lifetimes`` (for L23).  The scoped run of the CommandBus passes the touched keys so the cost is
O(touched); the whole-DB run passes the OPEN lifetimes.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .books import split_account_id
from .ledger import CHART, COST_LONG_GLS, COST_SHORT_GLS, natural
from .units import to_c

POSTING_KINDS = ("treasury.init_capital", "treasury.deposit", "treasury.withdraw", "treasury.transfer")
EQUITY_KINDS = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")
LONG_GL_OF_KIND = {"EQUITY": "1100", "ETF": "1100", "ADR": "1100", "REIT": "1100", "PREFERRED": "1100", "BOND": "1110", "PRIVATE": "1120", "OPTION": "1700"}
SHORT_GL_OF_KIND = {"EQUITY": "2600", "ETF": "2600", "ADR": "2600", "REIT": "2600", "PREFERRED": "2600", "BOND": "2600", "OPTION": "2900"}


@dataclass
class Problem:
    id: str
    severity: str            # ERROR | WARN
    scope: str
    expected: object = None
    actual: object = None
    detail: str = ""
    lifetime_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "severity": self.severity, "scope": self.scope, "expected": _plain(self.expected), "actual": _plain(self.actual),
                "detail": self.detail, "lifetime_id": self.lifetime_id}


def _plain(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return v


@dataclass
class Filter:
    accounts: Optional[Set[str]] = None
    entries: Optional[List[int]] = None
    closed_lifetimes: Optional[List[str]] = None
    lifetimes: Optional[List[str]] = None
    max_problems: int = 50


def _lid(account_id: str) -> Optional[str]:
    try:
        return split_account_id(account_id)[0]
    except ValueError:
        return None


def _in(col: str, values: Optional[Iterable]) -> Tuple[str, list]:
    if values is None:
        return "", []
    vals = list(values)
    if not vals:
        return " AND 0", []
    return f" AND {col} IN ({','.join('?' for _ in vals)})", vals


def _entry_where(flt: Filter, col: str = "entry_id") -> Tuple[str, list]:
    if flt.entries is not None:
        if not flt.entries:
            return " AND 0", []
        return f" AND {col} IN ({','.join('?' for _ in flt.entries)})", list(flt.entries)
    if flt.accounts is not None:
        if not flt.accounts:
            return " AND 0", []
        q = ",".join("?" for _ in flt.accounts)
        return f" AND {col} IN (SELECT DISTINCT entry_id FROM journal_lines WHERE account_id IN ({q}))", list(flt.accounts)
    return "", []


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list:
    return conn.execute(sql, params).fetchall()


# ---- L1 ---------------------------------------------------------------------------------------------------------------
def l1_entries_balance(conn, today, cal, flt: Filter) -> List[Problem]:
    out: List[Problem] = []
    w, p = _entry_where(flt, "l.entry_id")
    for r in _rows(conn, f"SELECT l.entry_id, l.currency, SUM(l.amount_c) s FROM journal_lines l WHERE 1{w} GROUP BY l.entry_id, l.currency HAVING SUM(l.amount_c) <> 0", p):
        out.append(Problem("L1", "ERROR", f"entry {r['entry_id']}", 0, int(r["s"]), f"Σ amount_c in {r['currency']} is not zero"))
    for r in _rows(conn, f"SELECT l.entry_id, SUM(l.base_c) s FROM journal_lines l WHERE 1{w} GROUP BY l.entry_id HAVING SUM(l.base_c) <> 0", p):
        out.append(Problem("L1", "ERROR", f"entry {r['entry_id']}", 0, int(r["s"]), "Σ base_c is not zero"))
    return out


# ---- L2 ---------------------------------------------------------------------------------------------------------------
def l2_lines_valid(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _entry_where(flt, "l.entry_id")
    sql = ("SELECT l.line_id, l.entry_id, l.gl, l.account_id, l.currency, l.amount_c, l.instrument_id, c.gl AS cgl, c.per_instrument, a.account_id AS aid"
           " FROM journal_lines l LEFT JOIN chart c ON c.gl=l.gl LEFT JOIN accounts a ON a.account_id=l.account_id"
           " WHERE (c.gl IS NULL OR a.account_id IS NULL OR l.amount_c = 0 OR NOT (l.currency GLOB '[A-Z][A-Z][A-Z]')"
           f" OR (c.per_instrument = 1 AND (l.instrument_id IS NULL OR l.instrument_id = ''))){w}")
    out = []
    for r in _rows(conn, sql, p):
        why = []
        if r["cgl"] is None:
            why.append(f"gl {r['gl']} not in chart")
        if r["aid"] is None:
            why.append(f"account {r['account_id']} missing")
        if r["amount_c"] == 0:
            why.append("amount 0")
        if r["per_instrument"] == 1 and not r["instrument_id"]:
            why.append("per-instrument gl without instrument_id")
        out.append(Problem("L2", "ERROR", f"line {r['line_id']} (entry {r['entry_id']})", None, None, "; ".join(why) or "invalid line", _lid(r["account_id"])))
    return out


# ---- L3 ---------------------------------------------------------------------------------------------------------------
def l3_gl_balances_match(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("l.account_id", flt.accounts)
    lines = {}
    for r in _rows(conn, "SELECT l.account_id, l.gl, l.currency, CASE WHEN c.per_instrument=1 THEN COALESCE(l.instrument_id,'') ELSE '' END AS inst,"
                         f" SUM(l.amount_c) a, SUM(l.base_c) b FROM journal_lines l JOIN chart c ON c.gl=l.gl WHERE 1{w} GROUP BY 1,2,3,4", p):
        lines[(r["account_id"], r["gl"], r["currency"], r["inst"])] = (int(r["a"]), int(r["b"]))
    w2, p2 = _in("account_id", flt.accounts)
    bals = {}
    for r in _rows(conn, f"SELECT account_id, gl, currency, instrument_id, balance_c, base_c FROM gl_balances WHERE 1{w2}", p2):
        bals[(r["account_id"], r["gl"], r["currency"], r["instrument_id"])] = (int(r["balance_c"]), int(r["base_c"]))
    out = []
    for key in sorted(set(lines) | set(bals)):
        a = lines.get(key)
        b = bals.get(key)
        if b is None:
            if a != (0, 0):
                out.append(Problem("L3", "ERROR", f"{key[0]} {key[1]} {key[2]} {key[3]}", a, None, "lines exist but no gl_balances row", _lid(key[0])))
        elif a is None:
            out.append(Problem("L3", "ERROR", f"{key[0]} {key[1]} {key[2]} {key[3]}", None, b, "gl_balances row without journal lines", _lid(key[0])))
        elif a != b:
            out.append(Problem("L3", "ERROR", f"{key[0]} {key[1]} {key[2]} {key[3]}", a, b, "gl_balances differs from Σ journal_lines (amount, base)", _lid(key[0])))
    return out


# ---- L4 / L5 ----------------------------------------------------------------------------------------------------------
def _lifetime_filter(flt: Filter) -> Tuple[str, list]:
    if flt.lifetimes is not None:
        return _in("a.lifetime_id", flt.lifetimes)
    if flt.accounts is not None:
        lids = sorted({l for l in (_lid(a) for a in flt.accounts) if l})
        return _in("a.lifetime_id", lids)
    return "", []


def l4_ledger_identity(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _lifetime_filter(flt)
    out = []
    for r in _rows(conn, "SELECT a.lifetime_id, g.currency, SUM(g.balance_c) s FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id"
                         f" WHERE 1{w} GROUP BY 1,2 HAVING SUM(g.balance_c) <> 0", p):
        out.append(Problem("L4", "ERROR", f"{r['lifetime_id']} {r['currency']}", 0, int(r["s"]), "assets − liabilities − equity − income + expense ≠ 0", r["lifetime_id"]))
    return out


def l5_transfers_net_zero(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _lifetime_filter(flt)
    out = []
    for r in _rows(conn, "SELECT a.lifetime_id, g.currency, SUM(g.balance_c) s FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id"
                         f" WHERE g.gl='3100'{w} GROUP BY 1,2 HAVING SUM(g.balance_c) <> 0", p):
        out.append(Problem("L5", "ERROR", f"{r['lifetime_id']} 3100 {r['currency']}", 0, int(r["s"]), "Treasury advances do not net to zero", r["lifetime_id"]))
    for r in _rows(conn, "SELECT a.lifetime_id, SUM(g.base_c) s FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id"
                         f" WHERE g.gl='3900'{w} GROUP BY 1 HAVING SUM(g.base_c) <> 0", p):
        out.append(Problem("L5", "ERROR", f"{r['lifetime_id']} 3900 base", 0, int(r["s"]), "FX clearing does not net to zero in base", r["lifetime_id"]))
    return out


# ---- L6 / L25 -----------------------------------------------------------------------------------------------------------
def _capital_commands(conn) -> Dict[Tuple[str, str], int]:
    sums: Dict[Tuple[str, str], int] = {}
    for r in _rows(conn, "SELECT kind, payload_json, result_json FROM commands WHERE kind IN ('treasury.init_capital','treasury.deposit','treasury.withdraw') AND http_status < 400"):
        try:
            payload = json.loads(r["payload_json"] or "{}")
            result = json.loads(r["result_json"] or "{}")
        except ValueError:
            continue
        if result.get("skipped"):
            continue
        aid = result.get("account_id") or payload.get("account_id") or (f"{payload.get('lifetime_id')}:TREASURY" if payload.get("lifetime_id") else None)
        if not aid:
            continue
        ccy = str(result.get("currency") or payload.get("currency") or "USD").upper()
        amt = result.get("amount_c", payload.get("amount_c"))
        if amt is None and payload.get("amount") is not None:
            try:
                amt = to_c(str(payload["amount"]))
            except (ValueError, TypeError):
                continue
        if amt is None:
            continue
        sign = -1 if r["kind"] == "treasury.withdraw" else 1
        sums[(aid, ccy)] = sums.get((aid, ccy), 0) + sign * int(amt)
    return sums


def l6_capital_explained(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("account_id", flt.accounts)
    tre = [r["account_id"] for r in _rows(conn, f"SELECT account_id FROM accounts WHERE kind='TREASURY'{w}", p)]
    if not tre:
        return []
    sums = _capital_commands(conn)
    out = []
    bals: Dict[Tuple[str, str], int] = {}
    q = ",".join("?" for _ in tre)
    for r in _rows(conn, f"SELECT account_id, currency, balance_c FROM gl_balances WHERE gl='3000' AND account_id IN ({q})", tre):
        bals[(r["account_id"], r["currency"])] = natural("3000", r["balance_c"])
    keys = {k for k in bals if k[0] in tre} | {k for k in sums if k[0] in tre}
    for key in sorted(keys):
        if bals.get(key, 0) != sums.get(key, 0):
            out.append(Problem("L6", "ERROR", f"{key[0]} 3000 {key[1]}", sums.get(key, 0), bals.get(key, 0),
                               "contributed capital ≠ Σ committed init/deposit − withdraw commands", _lid(key[0])))
    # non-Treasury accounts never carry 3000
    for r in _rows(conn, f"SELECT g.account_id, g.currency, g.balance_c FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id WHERE g.gl='3000' AND a.kind<>'TREASURY' AND g.balance_c<>0{_in('g.account_id', flt.accounts)[0]}", _in('g.account_id', flt.accounts)[1]):
        out.append(Problem("L6", "ERROR", f"{r['account_id']} 3000 {r['currency']}", 0, int(r["balance_c"]), "capital posted outside Treasury", _lid(r["account_id"])))
    return out


# ---- L7 / L8 / L9 ---------------------------------------------------------------------------------------------------------
def l7_positions_match_fills(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("account_id", flt.accounts)
    fills = {(r["account_id"], r["instrument_id"]): int(r["q"]) for r in _rows(
        conn, f"SELECT account_id, instrument_id, SUM(CASE side WHEN 'BUY' THEN qty ELSE -qty END) q FROM fills WHERE 1{w} GROUP BY 1,2", p)}
    pos = {(r["account_id"], r["instrument_id"]): int(r["qty"]) for r in _rows(conn, f"SELECT account_id, instrument_id, qty FROM v_positions WHERE 1{w}", p)}
    out = []
    for key in sorted(set(fills) | set(pos)):
        if fills.get(key, 0) != pos.get(key, 0):
            out.append(Problem("L7", "ERROR", f"{key[0]} {key[1]}", fills.get(key, 0), pos.get(key, 0), "v_positions.qty ≠ Σ signed fills", _lid(key[0])))
    return out


def l8_lots_consistent(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("lo.account_id", flt.accounts)
    out = []
    for r in _rows(conn, "SELECT lo.lot_id, lo.account_id, lo.instrument_id, lo.qty_open, lo.qty_remaining, lo.unit_cost, lo.cost_c, i.kind, i.multiplier, i.price_scale,"
                         " COALESCE((SELECT SUM(qty) FROM lot_relief x WHERE x.lot_id=lo.lot_id),0) rq,"
                         " COALESCE((SELECT SUM(cost_relieved_c) FROM lot_relief x WHERE x.lot_id=lo.lot_id),0) rc"
                         f" FROM lots lo LEFT JOIN instruments i ON i.instrument_id=lo.instrument_id WHERE 1{w}", p):
        scope = f"{r['account_id']} {r['instrument_id']} lot {r['lot_id']}"
        if int(r["qty_open"]) - int(r["rq"]) != int(r["qty_remaining"]):
            out.append(Problem("L8", "ERROR", scope, int(r["qty_open"]) - int(r["rq"]), int(r["qty_remaining"]), "qty_open − Σ relief ≠ qty_remaining", _lid(r["account_id"])))
        if r["kind"] == "FUTURE":
            if int(r["cost_c"]) != 0:
                out.append(Problem("L8", "ERROR", scope, 0, int(r["cost_c"]), "futures lots carry no cost (margined daily)", _lid(r["account_id"])))
            continue
        try:
            mult = Decimal(r["multiplier"] or "1")
            scale = Decimal(r["price_scale"] or "1")
            opened = to_c(Decimal(int(r["qty_open"])) * Decimal(r["unit_cost"]) * mult * scale)
        except Exception:   # noqa: BLE001
            out.append(Problem("L8", "ERROR", scope, None, None, "unit cost / multiplier not parseable", _lid(r["account_id"])))
            continue
        if opened - int(r["rc"]) != int(r["cost_c"]):
            out.append(Problem("L8", "ERROR", scope, opened - int(r["rc"]), int(r["cost_c"]), "qty_open × unit_cost × mult × scale − Σ cost_relieved ≠ cost_c", _lid(r["account_id"])))
    return out


def l9_cost_accounts_match(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("lo.account_id", flt.accounts)
    lots: Dict[Tuple[str, str, str], int] = {}
    for r in _rows(conn, "SELECT lo.account_id, lo.instrument_id, lo.side, i.kind, SUM(lo.cost_c) c FROM lots lo LEFT JOIN instruments i ON i.instrument_id=lo.instrument_id"
                         f" WHERE lo.qty_remaining > 0{w} GROUP BY 1,2,3,4", p):
        kind = r["kind"] or "EQUITY"
        if kind == "FUTURE":
            continue
        gl = LONG_GL_OF_KIND.get(kind) if r["side"] == "LONG" else SHORT_GL_OF_KIND.get(kind)
        if gl is None:
            continue
        lots[(r["account_id"], gl, r["instrument_id"])] = lots.get((r["account_id"], gl, r["instrument_id"]), 0) + int(r["c"])
    w2, p2 = _in("account_id", flt.accounts)
    gls = COST_LONG_GLS + COST_SHORT_GLS
    bal: Dict[Tuple[str, str, str], int] = {}
    for r in _rows(conn, f"SELECT account_id, gl, instrument_id, balance_c FROM gl_balances WHERE gl IN ({','.join('?' for _ in gls)}){w2}", list(gls) + p2):
        bal[(r["account_id"], r["gl"], r["instrument_id"])] = natural(r["gl"], r["balance_c"])
    out = []
    for key in sorted(set(lots) | set(bal)):
        if lots.get(key, 0) != bal.get(key, 0):
            out.append(Problem("L9", "ERROR", f"{key[0]} {key[1]} {key[2]}", lots.get(key, 0), bal.get(key, 0), "cost account ≠ Σ open lots cost", _lid(key[0])))
    return out


# ---- L10 / L11 ----------------------------------------------------------------------------------------------------------
def l10_obligations_match(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("account_id", flt.accounts)
    pend: Dict[Tuple[str, str, str], int] = {}
    for r in _rows(conn, f"SELECT account_id, gl, currency, SUM(amount_c) s FROM settlements WHERE status='PENDING'{w} GROUP BY 1,2,3", p):
        pend[(r["account_id"], r["gl"], r["currency"])] = int(r["s"])
    bal: Dict[Tuple[str, str, str], int] = {}
    for r in _rows(conn, f"SELECT account_id, gl, currency, SUM(balance_c) balance_c FROM gl_balances WHERE gl IN ('1200','2100','1250','2350'){w} GROUP BY 1,2,3", p):
        bal[(r["account_id"], r["gl"], r["currency"])] = int(r["balance_c"])     # storage sign: receivable +, payable −
    out = []
    for key in sorted(set(pend) | set(bal)):
        if pend.get(key, 0) != bal.get(key, 0):
            out.append(Problem("L10", "ERROR", f"{key[0]} {key[1]} {key[2]}", pend.get(key, 0), bal.get(key, 0), "PENDING settlements ≠ receivable/payable balance", _lid(key[0])))
    wf, pf = _in("f.account_id", flt.accounts)
    for r in _rows(conn, "SELECT f.fill_id, f.account_id FROM fills f WHERE f.settle_date IS NOT NULL AND f.origin IN ('ORDER','EXERCISE','ASSIGNMENT','NEGOTIATED','MATURITY')"
                         f" AND NOT EXISTS (SELECT 1 FROM settlements s WHERE s.fill_id=f.fill_id){wf}", pf):
        out.append(Problem("L10", "ERROR", f"fill {r['fill_id']}", "settlement row", None, "a fill with a settle_date has no settlements row", _lid(r["account_id"])))
    for r in _rows(conn, f"SELECT settle_id, account_id FROM settlements WHERE status='SETTLED' AND entry_id IS NULL{w}", p):
        out.append(Problem("L10", "ERROR", f"settlement {r['settle_id']}", "entry_id", None, "SETTLED without a journal entry", _lid(r["account_id"])))
    return out


def l11_no_duplicate_keys(conn, today, cal, flt: Filter) -> List[Problem]:
    out = []
    checks = [("fills", "idem_key", "COUNT(*)", "COUNT(DISTINCT idem_key)"), ("journal_entries", "idem_key", "COUNT(*)", "COUNT(DISTINCT idem_key)"),
              ("transfers", "idem_key", "COUNT(*)", "COUNT(DISTINCT idem_key)"), ("jobs_done", "job||':'||key", "COUNT(*)", "COUNT(DISTINCT job||':'||key)"),
              ("commands", "request_id", "COUNT(*)", "COUNT(DISTINCT request_id)")]
    for table, key, c1, c2 in checks:
        r = conn.execute(f"SELECT {c1}, {c2} FROM {table}").fetchone()
        if int(r[0]) != int(r[1]):
            out.append(Problem("L11", "ERROR", f"{table}.{key}", int(r[1]), int(r[0]), "duplicate keys"))
    r = conn.execute("SELECT COUNT(*), COUNT(DISTINCT fill_id||':'||leg) FROM settlements WHERE fill_id IS NOT NULL").fetchone()
    if int(r[0]) != int(r[1]):
        out.append(Problem("L11", "ERROR", "settlements (fill_id, leg)", int(r[1]), int(r[0]), "duplicate settlement legs"))
    return out


# ---- L12 / L13 / L14 ------------------------------------------------------------------------------------------------------
def l12_shorts_borrowed(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("v.account_id", flt.accounts)
    out = []
    for r in _rows(conn, "SELECT v.account_id, v.instrument_id, v.qty, i.kind, COALESCE((SELECT SUM(b.qty - b.qty_returned) FROM borrows b WHERE b.status='OPEN' AND b.account_id=v.account_id AND b.instrument_id=v.instrument_id),0) bq"
                         f" FROM v_positions v LEFT JOIN instruments i ON i.instrument_id=v.instrument_id WHERE v.qty < 0{w}", p):
        if (r["kind"] or "EQUITY") in EQUITY_KINDS and int(r["bq"]) < -int(r["qty"]):
            out.append(Problem("L12", "ERROR", f"{r['account_id']} {r['instrument_id']}", -int(r["qty"]), int(r["bq"]), "short position without enough live borrow", _lid(r["account_id"])))
    wb, pb = _in("b.account_id", flt.accounts)
    for r in _rows(conn, "SELECT b.borrow_id, b.account_id, b.instrument_id, b.reason, COALESCE((SELECT qty FROM v_positions v WHERE v.account_id=b.account_id AND v.instrument_id=b.instrument_id),0) q"
                         f" FROM borrows b WHERE b.status='OPEN' AND b.qty - b.qty_returned > 0{wb}", pb):
        if int(r["q"]) >= 0 and not r["reason"]:
            out.append(Problem("L12", "ERROR", f"borrow {r['borrow_id']}", "a short position or a reason", None, "open borrow backs nothing and carries no reason", _lid(r["account_id"])))
    return out


def l13_collateral_match(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("account_id", flt.accounts)
    coll = {(r["account_id"], r["instrument_id"]): int(r["c"]) for r in _rows(
        conn, f"SELECT account_id, instrument_id, SUM(collateral_c) c FROM borrows WHERE status='OPEN'{w} GROUP BY 1,2", p)}
    bal = {(r["account_id"], r["instrument_id"]): natural("1400", r["balance_c"]) for r in _rows(
        conn, f"SELECT account_id, instrument_id, balance_c FROM gl_balances WHERE gl='1400'{w}", p)}
    out = []
    for key in sorted(set(coll) | set(bal)):
        if coll.get(key, 0) != bal.get(key, 0):
            out.append(Problem("L13", "ERROR", f"{key[0]} 1400 {key[1]}", coll.get(key, 0), bal.get(key, 0), "collateral posted ≠ Σ open borrows collateral_c", _lid(key[0])))
    return out


def l14_loans_match(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("account_id", flt.accounts)
    loans: Dict[Tuple[str, str, str], int] = {}
    for r in _rows(conn, f"SELECT account_id, kind, currency, SUM(principal_c) s FROM loans WHERE status='OPEN'{w} GROUP BY 1,2,3", p):
        gl = "2750" if r["kind"] == "CASH_LOAN" else "2700"
        loans[(r["account_id"], gl, r["currency"])] = int(r["s"])
    bal: Dict[Tuple[str, str, str], int] = {}
    for r in _rows(conn, f"SELECT account_id, gl, currency, balance_c FROM gl_balances WHERE gl IN ('2750','2700'){w}", p):
        bal[(r["account_id"], r["gl"], r["currency"])] = natural(r["gl"], r["balance_c"])
    out = []
    for key in sorted(set(loans) | set(bal)):
        if loans.get(key, 0) != bal.get(key, 0):
            out.append(Problem("L14", "ERROR", f"{key[0]} {key[1]} {key[2]}", loans.get(key, 0), bal.get(key, 0), "loan principal ≠ Σ OPEN loans", _lid(key[0])))
    for r in _rows(conn, "SELECT a.account_id, a.pb_limit_c, COALESCE((SELECT SUM(balance_c) FROM gl_balances g WHERE g.account_id=a.account_id AND g.gl='2700'),0) b"
                         f" FROM accounts a WHERE a.kind='PB' AND a.pb_limit_c > 0{_in('a.account_id', flt.accounts)[0]}", _in('a.account_id', flt.accounts)[1]):
        if natural("2700", r["b"]) > int(r["pb_limit_c"]):
            out.append(Problem("L14", "ERROR", f"{r['account_id']} 2700", f"≤ {r['pb_limit_c']}", natural("2700", r["b"]), "PB financing above pb_limit_c", _lid(r["account_id"])))
    return out


# ---- L15 / L16 / L17 / L18 -----------------------------------------------------------------------------------------------
def _im_rate_recorded(conn, account_id: str, instrument_id: str) -> Optional[int]:
    """SR-11: the IM per contract the position was last margined at - ``im_per_c`` of the latest ``futures_settle`` row that
    carries it (each fill records it); None for positions margined before the rate was recorded (the table applies)."""
    for r in conn.execute("SELECT quote_json FROM futures_settle WHERE account_id=? AND instrument_id=? ORDER BY settle_date DESC", (account_id, instrument_id)).fetchall():
        try:
            v = (json.loads(r["quote_json"] or "{}") or {}).get("im_per_c")
        except (ValueError, TypeError, AttributeError):
            v = None
        if v is not None:
            return int(v)
    return None


def l15_futures_margin(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("v.account_id", flt.accounts)
    need: Dict[Tuple[str, str], int] = {}
    for r in _rows(conn, "SELECT v.account_id, v.instrument_id, v.qty, i.spec_json FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id"
                         f" WHERE i.kind='FUTURE'{w}", p):
        im = _im_rate_recorded(conn, r["account_id"], r["instrument_id"])
        if im is None:
            try:
                im = int(json.loads(r["spec_json"] or "{}").get("im_c") or 0)
            except (ValueError, TypeError):
                im = 0
        need[(r["account_id"], r["instrument_id"])] = im * abs(int(r["qty"]))
    w2, p2 = _in("account_id", flt.accounts)
    bal = {(r["account_id"], r["instrument_id"]): natural("1300", r["balance_c"]) for r in _rows(conn, f"SELECT account_id, instrument_id, balance_c FROM gl_balances WHERE gl='1300'{w2}", p2)}
    out = []
    for key in sorted(set(need) | set(bal)):
        if need.get(key, 0) != bal.get(key, 0):
            out.append(Problem("L15", "ERROR", f"{key[0]} 1300 {key[1]}", need.get(key, 0), bal.get(key, 0), "futures initial margin ≠ the margined rate × |qty|", _lid(key[0])))
    return out


def l16_option_margin(conn, today, cal, flt: Filter) -> List[Problem]:
    """SR-13 (decision s7.6): option margin is a RESERVATION, so the check is on ``margin_state`` itself - the stored requirement
    is never negative and equals the requirement recomputed from its own stored inputs (the detail's per-group requirements,
    which the formula summed); a row without a detail (a hand-made one) is only checked for its sign."""
    w, p = _in("account_id", flt.accounts)
    out = []
    for r in _rows(conn, f"SELECT account_id, options_required_c, options_detail_json FROM margin_state WHERE 1{w}", p):
        aid, req = r["account_id"], int(r["options_required_c"])
        if req < 0:
            out.append(Problem("L16", "ERROR", f"{aid} margin_state", "≥ 0", req, "negative option margin requirement", _lid(aid)))
            continue
        try:
            detail = json.loads(r["options_detail_json"] or "{}") or {}
        except (ValueError, TypeError):
            detail = {}
        if not isinstance(detail, dict) or "requirement_c" not in detail:
            continue
        recomputed = sum(int(g.get("requirement_c") or 0) for g in (detail.get("groups") or []))
        if int(detail["requirement_c"]) != req or recomputed != req:
            out.append(Problem("L16", "ERROR", f"{aid} margin_state", req, recomputed, "option margin requirement ≠ its recomputed groups", _lid(aid)))
    return out


def l17_orders_consistent(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("o.account_id", flt.accounts)
    out = []
    for r in _rows(conn, "SELECT o.order_id, o.account_id, o.status, o.qty, o.filled_qty, COALESCE((SELECT SUM(qty) FROM fills f WHERE f.order_id=o.order_id),0) fq"
                         f" FROM orders o WHERE 1{w}", p):
        if int(r["filled_qty"]) != int(r["fq"]):
            out.append(Problem("L17", "ERROR", f"order {r['order_id']}", int(r["fq"]), int(r["filled_qty"]), "filled_qty ≠ Σ fills", _lid(r["account_id"])))
        if r["status"] == "FILLED" and int(r["filled_qty"]) != int(r["qty"]):
            out.append(Problem("L17", "ERROR", f"order {r['order_id']}", int(r["qty"]), int(r["filled_qty"]), "FILLED but filled_qty ≠ qty", _lid(r["account_id"])))
        if r["status"] in ("WORKING", "PARTIAL") and int(r["filled_qty"]) >= int(r["qty"]):
            out.append(Problem("L17", "ERROR", f"order {r['order_id']}", f"< {r['qty']}", int(r["filled_qty"]), f"{r['status']} but fully filled", _lid(r["account_id"])))
    return out


def l18_cash_floor(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("g.account_id", flt.accounts)
    out = []
    shortfalls: Set[Tuple[str, str]] = set()
    for r in _rows(conn, "SELECT account_id, details_json FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL"):
        try:
            ccy = str(json.loads(r["details_json"] or "{}").get("currency") or "USD")
        except ValueError:
            ccy = "USD"
        shortfalls.add((r["account_id"], ccy))
    for r in _rows(conn, "SELECT g.account_id, g.currency, g.balance_c, a.kind, a.pb_financing, a.pb_limit_c FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id"
                         f" WHERE g.gl='1010' AND g.balance_c < 0{w}", p):
        if r["kind"] == "PB" and int(r["pb_financing"]):
            continue
        if (r["account_id"], r["currency"]) in shortfalls:
            continue
        out.append(Problem("L18", "ERROR", f"{r['account_id']} 1010 {r['currency']}", "≥ 0", int(r["balance_c"]), "settled cash below zero without an open FUNDING_SHORTFALL item", _lid(r["account_id"])))
    return out


# ---- L19 / L20 / L21 ------------------------------------------------------------------------------------------------------
def l19_sweep_linkage(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("to_account", flt.accounts)
    out = []
    for r in _rows(conn, f"SELECT transfer_id, to_account, linked_type, linked_id, amount_c, shortfall_c FROM transfers WHERE kind='SWEEP_IN'{w}", p):
        scope = f"sweep {r['transfer_id']}"
        lt, li = r["linked_type"], r["linked_id"]
        if not lt or not li:
            out.append(Problem("L19", "ERROR", scope, "linked_type/linked_id", None, "SWEEP_IN without a link", _lid(r["to_account"])))
            continue
        if lt == "fill":
            ok = conn.execute("SELECT 1 FROM fills WHERE fill_id=?", (li,)).fetchone() is not None
        elif lt == "settlement":
            ok = conn.execute("SELECT 1 FROM settlements WHERE settle_id=?", (li,)).fetchone() is not None
        elif lt == "borrow":
            ok = conn.execute("SELECT 1 FROM borrows WHERE borrow_id=?", (li,)).fetchone() is not None
        else:
            ok = conn.execute("SELECT 1 FROM journal_entries WHERE idem_key=? OR ref_id=? OR CAST(entry_id AS TEXT)=?", (li, li, li)).fetchone() is not None
        if not ok:
            out.append(Problem("L19", "ERROR", scope, f"{lt} {li}", None, "sweep link points at nothing", _lid(r["to_account"])))
        if r["shortfall_c"] is None or int(r["shortfall_c"]) != int(r["amount_c"]):
            out.append(Problem("L19", "ERROR", scope, int(r["amount_c"]), r["shortfall_c"], "sweep amount ≠ recorded shortfall", _lid(r["to_account"])))
    return out


def l20_request_linkage(conn, today, cal, flt: Filter) -> List[Problem]:
    out = []
    w, p = _in("f.account_id", flt.accounts)
    for r in _rows(conn, f"SELECT f.fill_id, f.account_id FROM fills f LEFT JOIN journal_entries e ON e.entry_id=f.entry_id WHERE e.entry_id IS NULL{w}", p):
        out.append(Problem("L20", "ERROR", f"fill {r['fill_id']}", "entry", None, "fill without its journal entry", _lid(r["account_id"])))
    wt, pt = _in("COALESCE(t.to_account, t.from_account)", flt.accounts)
    for r in _rows(conn, f"SELECT t.transfer_id FROM transfers t LEFT JOIN journal_entries e ON e.entry_id=t.entry_id WHERE e.entry_id IS NULL{wt}", pt):
        out.append(Problem("L20", "ERROR", f"transfer {r['transfer_id']}", "entry", None, "transfer without its journal entry"))
    we, pe = _entry_where(flt, "e.entry_id")
    for r in _rows(conn, f"SELECT e.entry_id FROM journal_entries e LEFT JOIN commands c ON c.id=e.command_id WHERE c.id IS NULL{we}", pe):
        out.append(Problem("L20", "ERROR", f"entry {r['entry_id']}", "command", None, "entry without its command"))
    for r in _rows(conn, "SELECT c.id, c.request_id, c.kind, c.result_json FROM commands c WHERE c.kind IN (" + ",".join("?" for _ in POSTING_KINDS) + ")"
                         " AND c.http_status < 400 AND NOT EXISTS (SELECT 1 FROM journal_entries e WHERE e.command_id=c.id)", list(POSTING_KINDS)):
        try:
            if json.loads(r["result_json"] or "{}").get("skipped"):
                continue
        except ValueError:
            pass
        out.append(Problem("L20", "ERROR", f"command {r['request_id']}", "≥ 1 entry", 0, f"{r['kind']} committed without a posting"))
    return out


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return any(r[1] == column for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall())
    except sqlite3.Error:
        return False


def l21_settlement_health(conn, today, cal, flt: Filter) -> List[Problem]:
    """A PENDING settlement more than 5 business days overdue means the SETTLE_DUE job is stuck.  SR-02: settlements of a
    CLOSED (reset) lifetime are frozen with it - SETTLE_DUE settles OPEN lifetimes only - so they are not a stuck job and
    never raise the hold.  C9g (FINSIM1_PLAN): a settlement whose ``fail_state`` is FAILED (P6's column, present once the
    collateral module has bound) is a recorded settlement fail - shown as FAILED on the pages - not a stuck job, so it is not an
    integrity failure either."""
    w, p = _in("s.account_id", flt.accounts)
    limit = cal.add_business_days(today, -5).isoformat()
    failed = " AND COALESCE(s.fail_state, '') <> 'FAILED'" if _has_column(conn, "settlements", "fail_state") else ""
    out = []
    for r in _rows(conn, "SELECT s.settle_id, s.account_id, s.due_date FROM settlements s JOIN accounts a ON a.account_id=s.account_id"
                         f" JOIN lifetimes lt ON lt.lifetime_id=a.lifetime_id WHERE s.status='PENDING' AND s.due_date < ? AND lt.status='OPEN'{failed}{w}", [limit] + p):
        out.append(Problem("L21", "ERROR", f"settlement {r['settle_id']}", f"settled by {limit}", r["due_date"], "PENDING settlement more than 5 business days overdue (the job is stuck)", _lid(r["account_id"])))
    return out


# ---- L22 / L23 / L24 / L25 ---------------------------------------------------------------------------------------------
def l22_otc_margin_match(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _in("account_id", flt.accounts)
    out = []
    bal: Dict[Tuple[str, str, str], int] = {}
    for r in _rows(conn, f"SELECT account_id, gl, instrument_id, balance_c FROM gl_balances WHERE gl IN ('1320','2330','1330'){w}", p):
        bal[(r["account_id"], r["gl"], r["instrument_id"])] = natural(r["gl"], r["balance_c"])
    seen = set()
    for r in _rows(conn, f"SELECT contract_id, account_id, status, margin_mode, im_c, vm_balance_c FROM otc_contracts WHERE 1{w}", p):
        aid, cid = r["account_id"], r["contract_id"]
        seen.update({(aid, "1320", cid), (aid, "2330", cid), (aid, "1330", cid)})
        vm = int(r["vm_balance_c"])
        if r["status"] == "OPEN":
            exp = {"1320": max(0, -vm) if r["margin_mode"] == "CTM" else 0, "2330": max(0, vm) if r["margin_mode"] == "CTM" else 0, "1330": int(r["im_c"])}
        else:
            exp = {"1320": 0, "2330": 0, "1330": 0}
        for gl, e in exp.items():
            if bal.get((aid, gl, cid), 0) != e:
                out.append(Problem("L22", "ERROR", f"{aid} {gl} {cid}", e, bal.get((aid, gl, cid), 0), f"OTC margin ({r['status']}, {r['margin_mode']}) mismatch", _lid(aid)))
    for key, v in bal.items():
        if key not in seen and v != 0:
            out.append(Problem("L22", "ERROR", f"{key[0]} {key[1]} {key[2]}", 0, v, "OTC margin balance without a contract", _lid(key[0])))
    return out


def l23_lifetime_frozen(conn, today, cal, flt: Filter) -> List[Problem]:
    lids = flt.closed_lifetimes
    if lids is None:
        lids = [r["lifetime_id"] for r in _rows(conn, "SELECT lifetime_id FROM lifetimes WHERE status='CLOSED'")]
    out = []
    for lid in lids:
        row = conn.execute("SELECT ended_ts FROM lifetimes WHERE lifetime_id=? AND status='CLOSED'", (lid,)).fetchone()
        if row is None or row["ended_ts"] is None:
            continue
        ended = int(row["ended_ts"])
        n = conn.execute("SELECT COUNT(*) FROM journal_entries e WHERE e.ts > ? AND EXISTS (SELECT 1 FROM journal_lines l JOIN accounts a ON a.account_id=l.account_id WHERE l.entry_id=e.entry_id AND a.lifetime_id=?)", (ended, lid)).fetchone()[0]
        if n:
            out.append(Problem("L23", "ERROR", lid, 0, int(n), "journal entries after the lifetime ended", lid))
        n = conn.execute("SELECT COUNT(*) FROM fills f JOIN accounts a ON a.account_id=f.account_id WHERE a.lifetime_id=? AND f.ts > ?", (lid, ended)).fetchone()[0]
        if n:
            out.append(Problem("L23", "ERROR", lid, 0, int(n), "fills after the lifetime ended", lid))
        n = conn.execute("SELECT COUNT(*) FROM transfers t JOIN accounts a ON a.account_id=COALESCE(t.to_account, t.from_account) WHERE a.lifetime_id=? AND t.ts > ?", (lid, ended)).fetchone()[0]
        if n:
            out.append(Problem("L23", "ERROR", lid, 0, int(n), "transfers after the lifetime ended", lid))
        n = conn.execute("SELECT COUNT(*) FROM orders o JOIN accounts a ON a.account_id=o.account_id WHERE a.lifetime_id=? AND o.status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')", (lid,)).fetchone()[0]
        if n:
            out.append(Problem("L23", "ERROR", lid, 0, int(n), "open orders in a closed lifetime", lid))
        n = conn.execute("SELECT COUNT(*) FROM accounts WHERE lifetime_id=? AND status<>'CLOSED'", (lid,)).fetchone()[0]
        if n:
            out.append(Problem("L23", "ERROR", lid, 0, int(n), "accounts still OPEN in a closed lifetime", lid))
    return out


def l24_private_lots(conn, today, cal, flt: Filter) -> List[Problem]:
    out = []
    w, p = _in("f.account_id", flt.accounts)
    for r in _rows(conn, "SELECT f.fill_id, f.account_id, f.origin, (SELECT COUNT(*) FROM settlements s WHERE s.fill_id=f.fill_id) ns FROM fills f JOIN instruments i ON i.instrument_id=f.instrument_id"
                         f" WHERE i.kind='PRIVATE'{w}", p):
        if r["origin"] != "NEGOTIATED":
            out.append(Problem("L24", "ERROR", f"fill {r['fill_id']}", "NEGOTIATED", r["origin"], "private fill with a non-negotiated origin", _lid(r["account_id"])))
        if int(r["ns"]) == 0:
            out.append(Problem("L24", "ERROR", f"fill {r['fill_id']}", "settlement row", 0, "private fill without a settlement row", _lid(r["account_id"])))
    wl, pl = _in("l.account_id", flt.accounts)
    for r in _rows(conn, "SELECT l.loan_id, l.account_id, l.instrument_id, l.principal_c, l.issue_price,"
                         " COALESCE((SELECT cum_c FROM accrual_state s WHERE s.kind='pcoid' AND s.ref_id=l.loan_id),0) oid,"
                         " COALESCE((SELECT SUM(balance_c) FROM gl_balances g WHERE g.account_id=l.account_id AND g.gl='1130' AND g.instrument_id=l.instrument_id),0) b"
                         f" FROM private_loans l WHERE l.status='OPEN'{wl}", pl):
        try:
            cost = to_c(Decimal(int(r["principal_c"])) / Decimal(100) * Decimal(r["issue_price"]) / Decimal(100)) + int(r["oid"])
        except Exception:   # noqa: BLE001
            cost = None
        if cost is not None and natural("1130", r["b"]) != cost:
            out.append(Problem("L24", "ERROR", f"{r['account_id']} 1130 {r['instrument_id']}", cost, natural("1130", r["b"]), "amortised cost ≠ principal × issue price + OID accreted", _lid(r["account_id"])))
    return out


def l25_books_isolated(conn, today, cal, flt: Filter) -> List[Problem]:
    w, p = _entry_where(flt, "l.entry_id")
    out = []
    for r in _rows(conn, "SELECT l.entry_id, COUNT(DISTINCT a.lifetime_id) n FROM journal_lines l JOIN accounts a ON a.account_id=l.account_id"
                         f" WHERE 1{w} GROUP BY l.entry_id HAVING COUNT(DISTINCT a.lifetime_id) > 1", p):
        out.append(Problem("L25", "ERROR", f"entry {r['entry_id']}", 1, int(r["n"]), "a journal entry mixes accounts of two lifetimes"))
    return out


CHECKS = [l1_entries_balance, l2_lines_valid, l3_gl_balances_match, l4_ledger_identity, l5_transfers_net_zero, l6_capital_explained,
          l7_positions_match_fills, l8_lots_consistent, l9_cost_accounts_match, l10_obligations_match, l11_no_duplicate_keys, l12_shorts_borrowed,
          l13_collateral_match, l14_loans_match, l15_futures_margin, l16_option_margin, l17_orders_consistent, l18_cash_floor, l19_sweep_linkage,
          l20_request_linkage, l21_settlement_health, l22_otc_margin_match, l23_lifetime_frozen, l24_private_lots, l25_books_isolated]

#: BM-26: what each check proves, in trader wording (the Integrity tab lists one row per check run)
CHECK_NAMES = {
    "L1": "every journal entry balances", "L2": "every journal line is valid", "L3": "account balances equal the journal",
    "L4": "assets = liabilities + equity in every currency", "L5": "internal transfers net to zero", "L6": "capital is explained by deposits",
    "L7": "positions equal their fills", "L8": "tax lots are consistent", "L9": "cost accounts equal the open lots",
    "L10": "pending settlements equal receivables and payables", "L11": "no fill, settlement or accrual posted twice",
    "L12": "every short has a live borrow", "L13": "borrow collateral matches the ledger", "L14": "loans match their principal",
    "L15": "futures initial margin matches the positions", "L16": "the option margin reservation matches its requirement", "L17": "orders are consistent with their fills",
    "L18": "settled cash below zero only under an open funding shortfall", "L19": "every sweep-in is linked and exact",
    "L20": "every fill and transfer has its entry and command", "L21": "no settlement is stuck past its due date",
    "L22": "OTC margin matches the contracts", "L23": "closed lifetimes stay frozen", "L24": "private lots carry their terms",
    "L25": "books never post to each other",
}


def check_code(fn) -> str:
    return fn.__name__.split("_")[0].upper()


def ledger_checks(conn: sqlite3.Connection, today: date, cal, accounts: Optional[Set[str]] = None, entries: Optional[List[int]] = None,
                  lifetimes: Optional[List[str]] = None, closed_lifetimes: Optional[List[str]] = None, scoped: bool = False,
                  runs: Optional[List[dict]] = None) -> List[Problem]:
    """Run L1–L25.  ``scoped`` (inside a command) skips the whole-table checks that no touched key can change (L11,
    L21, L23) — they run in the ledger / full modes.  ``runs`` (BM-26), when given, receives one row per check run:
    ``{code, name, ok, count, errors, warnings, message}``."""
    if accounts is None and lifetimes is not None:
        q = ",".join("?" for _ in lifetimes)
        accounts = {r["account_id"] for r in conn.execute(f"SELECT account_id FROM accounts WHERE lifetime_id IN ({q})", list(lifetimes)).fetchall()} if lifetimes else set()
    flt = Filter(accounts=accounts, entries=entries, closed_lifetimes=closed_lifetimes if closed_lifetimes is not None else ([] if scoped else None), lifetimes=lifetimes)
    out: List[Problem] = []
    for fn in CHECKS:
        if scoped and fn in (l11_no_duplicate_keys, l21_settlement_health, l23_lifetime_frozen):
            continue
        code = check_code(fn)
        try:
            got = fn(conn, today, cal, flt)[:flt.max_problems]
        except Exception as e:   # noqa: BLE001 — a broken check is itself a finding, never a silent pass
            got = [Problem(code, "ERROR", "check", None, None, f"check raised {type(e).__name__}: {e}")]
        out.extend(got)
        if runs is not None:
            errs = sum(1 for p in got if p.severity == "ERROR")
            warns = sum(1 for p in got if p.severity == "WARN")
            runs.append({"code": code, "name": CHECK_NAMES.get(code, fn.__name__), "ok": errs == 0, "count": len(got), "errors": errs, "warnings": warns,
                         "message": "ok" if not got else "; ".join(f"{p.scope}: {p.detail}" for p in got[:3]) + (f" (+{len(got) - 3} more)" if len(got) > 3 else "")})
    return out
