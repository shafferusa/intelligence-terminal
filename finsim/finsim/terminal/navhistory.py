"""NAV history: the as-of NAV engine, restated snapshot versions with their split, ``nav.restate`` (FINSIM1_PLAN C9d / C9i,
PORTFOLIO-01, PORTFOLIO-14; package P3).

An optional module (``server.OPTIONAL_MODULES["navhistory"]``): ``bind`` creates ``nav_snapshot_versions`` (additive DDL through
``Database.ensure_schema_ext``, D2), ``register`` adds the command ``nav.restate``.  It imports nothing of another new module, so
its import, ``register`` and ``bind`` never fail with the build half done (section 15.2 step 3).

Conventions
- BUSINESS DATE, NEVER THE LEDGER AS IT STANDS.  The NAV of a scope "as of business date d" (``row_as_of``,
  ``accounting.nav_as_of``) takes cash, every GL balance and every position from the journal entries whose ``business_date``
  <= d: a fill entered at 19:58 ET on D carries business date D+1 and counts from D+1; an as-of booking counts from its own
  business date.  Position quantities come from the fills whose own entry is in that set; their cost from the cost GLs
  (1100 / 1110 / 1120 / 1700 long, 2600 / 2900 short, OTC 1335 / 2345); a future's open-trade-equity reference from the latest
  ``futures_settle`` row of the set; an OTC contract exists from its booking entry and is carried at the variation-margin mark of
  its latest ``otc_vm`` row on or before d (before the first VM: the booking mark ``mark0_c``).
- DATED MARKS (PORTFOLIO-14).  Equities / ETFs at the recorded CLOSE of d, futures at the SETTLE of d, options at the recorded
  CLOSE (the Cboe official close where it was recorded, else the last trade; its stored quality says so), the OTC VM mark of d.
  A weekend or holiday carries the previous trading day's stored mark (no re-mark); a mark older than the instrument's last
  trading day is labelled stale.  ``detail_json`` keeps each position's mark kind, source and date.
- VERSIONS (C9d).  ``nav.restate`` writes ``nav_snapshot_versions`` rows beside the originals (``nav_snapshots`` is never
  replaced), for every account row and the ``{lifetime}:ALL`` row of every date in its range.  Readers take per (account, date)
  the latest version, else the original (``accounting.snapshot_at`` / ``snapshot_chain`` / ``_last_snapshot``) and label it
  "restated on <date> (batch X)".  A date with no original row but non-zero restated figures gets a version with
  ``original_nav_c`` 0, "added by restatement" (F1:PB_GS on 10-21: the account opened at 19:44 ET, after that day's 17:45
  snapshot).  The ``pnl_day`` / ``flows_day`` chain is recomputed across the range against the previous date's latest row.
- THE SPLIT (a waterfall at exact cents; every row of a USD book):
      original (stored)       N0
      reproduction            N1 = NAV of the ledger the original saw (the entries of the commands before the snapshot's own
                                   command) with the original's own position values - must equal N0
      dating      = NAV(P, original marks) - N1      P = the entries of business date <= d the batch did not post
      mark policy = NAV(P, dated marks) - NAV(P, original marks), one row per position (dated mark minus the original's)
      interest    = the C17 corrections (idem ``reaccrue:<batch>:``) dated <= d
      import      = each group in the payload's order, at dated marks (``funding`` nets to 0 at the lifetime level only)
      residual    = restated - (N0 + dating + mark policy + interest + import) = (N1 - N0) + what the batch posted that no group
                    names; it must be 0 for every account and ``{lifetime}:ALL`` on every date, else the command refuses (409
                    RESTATE_RESIDUAL) and writes nothing.
  The dating line lists the entries behind it: the ones the original counted that carry a later business date (a late snapshot
  "from the ledger as it stood"; the 17:00-17:45 ET postings of an evening, which carry the next business date) and the ones
  of business date <= d posted after the original was taken (a dividend recorded a day late).  An entry belongs to the batch
  when a group names it (its contract / order / fill / settlement / transfer, or the sweeps linked to them) or when its
  command ran at or after the batch's first command (the first command whose payload names the batch id) or under actor
  ``import``.
- IDEMPOTENT: a date whose latest version already carries the same batch id and the same figures gets no new version, so a re-run
  (or a second dry run) writes nothing.
- Limits (said, never hidden): the reproduction is exact in USD; a scope holding foreign currencies may show an FX-rounding
  residual, which refuses.  Desk FX forwards are not restated (their overlay is a live model): a scope holding one refuses.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .accounting import (OTC_COST_GLS, VERSIONS_TABLE, _instrument, _iso, _last_snapshot, dated_mark_view, mark_labels, pl_amount, pl_bucket, snapshot_at)
from .clock import accounting_day, ny_of
from .commands import ConflictError, RejectError
from .db import dumps
from .ledger import CHART, natural
from .quotes import MarkView, QUALITY_RANK, unavailable
from .units import fx_c, mul_c

__all__ = ["SCHEMA", "bind", "register", "AsOfBook", "row_as_of", "h_restate", "restate_plan", "SNAPSHOT_FIELDS", "Forbidden"]

LISTED_COST_GLS = ("1100", "1110", "1120", "1130", "1700", "2600", "2900")
COST_GLS = LISTED_COST_GLS + tuple(OTC_COST_GLS)
SNAPSHOT_FIELDS = ("nav_c", "settled_c", "pending_c", "mv_long_c", "mv_short_c", "unrealised_c", "fx_result_c", "realised_cum_c", "income_cum_c",
                   "financing_cum_c", "fees_cum_c", "capital_cum_c", "transfers_cum_c", "flows_day_c", "pnl_day_c")
SPLIT_FIELDS = ("original_nav_c", "added", "import_effect_json", "interest_effect_c", "mark_policy_c", "mark_policy_json", "dating_c", "dating_json", "residual_c")
SOURCE = "navhistory"
_RANK_NAME = {v: k for k, v in QUALITY_RANK.items() if k != "live"}

SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS {VERSIONS_TABLE} (
  account_id TEXT NOT NULL, snap_date TEXT NOT NULL, version INTEGER NOT NULL, ts INTEGER NOT NULL, command_id INTEGER,
  reason TEXT NOT NULL, batch_id TEXT,
  nav_c INTEGER NOT NULL, settled_c INTEGER NOT NULL, pending_c INTEGER NOT NULL, mv_long_c INTEGER NOT NULL, mv_short_c INTEGER NOT NULL,
  unrealised_c INTEGER NOT NULL, fx_result_c INTEGER NOT NULL, realised_cum_c INTEGER NOT NULL, income_cum_c INTEGER NOT NULL,
  financing_cum_c INTEGER NOT NULL, fees_cum_c INTEGER NOT NULL, capital_cum_c INTEGER NOT NULL, transfers_cum_c INTEGER NOT NULL,
  flows_day_c INTEGER NOT NULL, pnl_day_c INTEGER NOT NULL, quality TEXT NOT NULL, partial INTEGER NOT NULL DEFAULT 0,
  original_nav_c INTEGER NOT NULL, added INTEGER NOT NULL DEFAULT 0, import_effect_json TEXT NOT NULL, interest_effect_c INTEGER NOT NULL,
  mark_policy_c INTEGER NOT NULL, mark_policy_json TEXT NOT NULL, dating_c INTEGER NOT NULL, dating_json TEXT NOT NULL, residual_c INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  PRIMARY KEY (account_id, snap_date, version))""",
]


class Forbidden(RejectError):
    """A command the UI may not send -> HTTP 403 (nothing stored)."""
    http = 403


# ---- the in-memory ledger of a scope -----------------------------------------------------------------------------------------
class AsOfBook:
    """Everything the as-of NAV of a set of accounts needs, read ONCE on the caller's connection: entries (with their command and
    actor), lines, fills, futures references, OTC contracts with their VM / reset history, private loans.  Valuation filters it in
    memory by an entry predicate, so the restatement's several states cost one read."""

    def __init__(self, conn: sqlite3.Connection, master, account_ids: Iterable[str], sessions=None):
        self.conn, self.master, self.sessions = conn, master, sessions
        self.accounts = sorted(set(account_ids))
        self._icache: dict = {}
        self.entries: Dict[int, dict] = {}
        self.lines: Dict[str, List[tuple]] = {a: [] for a in self.accounts}
        self.fills: Dict[str, List[dict]] = {a: [] for a in self.accounts}
        self.fut: Dict[Tuple[str, str], List[tuple]] = {}
        self.lot_cost: Dict[str, Decimal] = {}
        self.otc: Dict[str, List[dict]] = {a: [] for a in self.accounts}
        self.otc_vm: Dict[str, List[tuple]] = {}
        self.otc_reset: Dict[str, List[tuple]] = {}
        self.otc_book: Dict[str, int] = {}
        self.loans: Dict[str, List[dict]] = {a: [] for a in self.accounts}
        self.entry_accounts: Dict[int, Set[str]] = {}
        if not self.accounts:
            return
        q = ",".join("?" for _ in self.accounts)
        A = list(self.accounts)
        for r in conn.execute("SELECT e.entry_id, e.command_id, e.ts, e.business_date, e.kind, e.memo, e.ref_type, e.ref_id, e.idem_key, c.actor"
                              " FROM journal_entries e LEFT JOIN commands c ON c.id=e.command_id"
                              f" WHERE e.entry_id IN (SELECT DISTINCT entry_id FROM journal_lines WHERE account_id IN ({q}))", A).fetchall():
            self.entries[int(r["entry_id"])] = dict(r)
        for r in conn.execute(f"SELECT entry_id, account_id, gl, currency, amount_c, base_c, instrument_id FROM journal_lines WHERE account_id IN ({q})"
                              " ORDER BY entry_id, line_no", A).fetchall():
            self.lines[r["account_id"]].append((int(r["entry_id"]), r["gl"], r["currency"], int(r["amount_c"]), int(r["base_c"]), r["instrument_id"] or ""))
            self.entry_accounts.setdefault(int(r["entry_id"]), set()).add(r["account_id"])
        for r in conn.execute(f"SELECT fill_id, order_id, account_id, instrument_id, side, qty, entry_id, ts FROM fills WHERE account_id IN ({q})"
                              " ORDER BY ts, fill_id", A).fetchall():
            self.fills[r["account_id"]].append(dict(r))
        for r in conn.execute(f"SELECT open_fill_id, unit_cost FROM lots WHERE account_id IN ({q})", A).fetchall():
            try:
                self.lot_cost[r["open_fill_id"]] = Decimal(str(r["unit_cost"]))
            except Exception:   # noqa: BLE001 - a lot without a readable unit cost gives no reference (the row says so)
                pass
        for r in conn.execute(f"SELECT account_id, instrument_id, settle_date, settle_px, entry_id FROM futures_settle WHERE account_id IN ({q})"
                              " ORDER BY settle_date", A).fetchall():
            self.fut.setdefault((r["account_id"], r["instrument_id"]), []).append((r["settle_date"], Decimal(str(r["settle_px"])),
                                                                                    None if r["entry_id"] is None else int(r["entry_id"])))
        try:
            for r in conn.execute(f"SELECT * FROM otc_contracts WHERE account_id IN ({q}) ORDER BY contract_id", A).fetchall():
                self.otc[r["account_id"]].append(dict(r))
            for r in conn.execute(f"SELECT v.contract_id, v.vm_date, v.mark_c, v.mode, v.entry_id FROM otc_vm v JOIN otc_contracts c ON c.contract_id=v.contract_id"
                                  f" WHERE c.account_id IN ({q}) ORDER BY v.vm_date", A).fetchall():
                self.otc_vm.setdefault(r["contract_id"], []).append((r["vm_date"], int(r["mark_c"]), r["mode"], None if r["entry_id"] is None else int(r["entry_id"])))
            for r in conn.execute(f"SELECT r.contract_id, r.event_date, r.detail_json, r.entry_id FROM otc_resets r JOIN otc_contracts c ON c.contract_id=r.contract_id"
                                  f" WHERE c.account_id IN ({q}) AND r.kind='RESET' ORDER BY r.event_date", A).fetchall():
                self.otc_reset.setdefault(r["contract_id"], []).append((r["event_date"], r["detail_json"], None if r["entry_id"] is None else int(r["entry_id"])))
        except sqlite3.Error:
            pass
        for eid in sorted(self.entries):
            e = self.entries[eid]
            if e["ref_type"] == "otc" and e["ref_id"] and e["ref_id"] not in self.otc_book:
                self.otc_book[e["ref_id"]] = eid
        try:
            for r in conn.execute(f"SELECT * FROM private_loans WHERE account_id IN ({q})", A).fetchall():
                self.loans[r["account_id"]].append(dict(r))
        except sqlite3.Error:
            pass

    def inst(self, iid: str):
        return _instrument(self.conn, self.master, iid, self._icache)

    def bd(self, eid: Optional[int]) -> Optional[str]:
        e = self.entries.get(eid) if eid is not None else None
        return e["business_date"] if e else None


@dataclass
class State:
    """Which entries a valuation counts: ``include(entry_id)``; ``on_date`` decides the rows that carry no entry (a futures
    settle with a zero VM, a VM row without an entry): counted when dated on or before it."""
    include: Callable[[int], bool]
    on_date: Optional[str] = None

    def row_in(self, eid: Optional[int], row_date: Optional[str]) -> bool:
        if eid is not None:
            return bool(self.include(eid))
        return self.on_date is not None and row_date is not None and row_date <= self.on_date


@dataclass
class Fig:
    """One account's (or a scope's summed) figures in one state - the ``nav_snapshots`` columns and the position detail."""
    S: Dict[str, int] = field(default_factory=dict)
    B: Dict[str, int] = field(default_factory=dict)
    U: Dict[str, int] = field(default_factory=dict)
    gl: Dict[Tuple[str, str], int] = field(default_factory=dict)
    capital_c: int = 0
    transfers_c: int = 0
    realised_c: int = 0
    income_c: int = 0
    financing_c: int = 0
    fees_c: int = 0
    nav_c: int = 0
    unrealised_c: int = 0
    fx_result_c: int = 0
    settled_c: int = 0
    pending_c: int = 0
    mv_long_c: int = 0
    mv_short_c: int = 0
    worst: int = 0
    missing: List[str] = field(default_factory=list)
    fx_missing: List[str] = field(default_factory=list)
    positions: Dict[str, dict] = field(default_factory=dict)
    pos_acct: Dict[Tuple[str, str], dict] = field(default_factory=dict)        # (account, instrument) -> the position detail
    by_account: Dict[str, int] = field(default_factory=dict)
    rates: Dict[str, Optional[Decimal]] = field(default_factory=dict)
    has_positions: bool = False

    def add(self, o: "Fig") -> None:
        for k in ("capital_c", "transfers_c", "realised_c", "income_c", "financing_c", "fees_c", "nav_c", "unrealised_c", "fx_result_c", "settled_c",
                  "pending_c", "mv_long_c", "mv_short_c"):
            setattr(self, k, getattr(self, k) + getattr(o, k))
        for d_self, d_o in ((self.S, o.S), (self.B, o.B), (self.U, o.U)):
            for ccy, v in d_o.items():
                d_self[ccy] = d_self.get(ccy, 0) + v
        self.worst = max(self.worst, o.worst)
        self.missing.extend(o.missing)
        self.fx_missing = sorted(set(self.fx_missing) | set(o.fx_missing))
        self.positions.update(o.positions)
        self.pos_acct.update(o.pos_acct)
        self.by_account.update(o.by_account)
        self.rates.update(o.rates)
        self.has_positions = self.has_positions or o.has_positions

    @property
    def quality(self) -> str:
        if self.missing or self.fx_missing:
            return "unavailable"
        return _RANK_NAME.get(self.worst, "delayed") if self.has_positions else "delayed"

    @property
    def partial(self) -> bool:
        return bool(self.missing or self.fx_missing)


#: a chooser answers the mark of a listed position: (mark or None, MarkView)
Chooser = Callable[[str, str, object], Tuple[Optional[Decimal], MarkView]]
#: an OTC valuer answers a contract's value in its currency: (mv_c, MarkView)
OtcValuer = Callable[[str, dict, "State"], Tuple[int, MarkView]]


def fut_reference(book: AsOfBook, aid: str, iid: str, state: State) -> Optional[Decimal]:
    """A future's open-trade-equity reference in ``state``: the latest ``futures_settle`` row of the state (its weighted reference
    after fills, the settle print after a VM), else the unit cost of the earliest lot whose opening fill is in the state."""
    ref = None
    for sd, px, eid in book.fut.get((aid, iid), []):
        if state.row_in(eid, sd):
            ref = px
    if ref is not None:
        return ref
    for f in book.fills.get(aid, []):
        if f["instrument_id"] == iid and state.include(int(f["entry_id"])) and f["fill_id"] in book.lot_cost:
            return book.lot_cost[f["fill_id"]]
    return None


def otc_exists(book: AsOfBook, c: dict, state: State) -> bool:
    """A contract exists in ``state`` from its booking entry (the first entry referring to it) until the entry that closed it."""
    cid = c["contract_id"]
    eid = book.otc_book.get(cid)
    if eid is not None:
        if not state.include(eid):
            return False
    elif not (state.on_date is not None and str(c.get("start_date") or "") <= state.on_date):
        return False
    if c.get("status") in ("TERMINATED", "MATURED") and c.get("closed_ts"):
        closed_bd = accounting_day(ny_of(int(c["closed_ts"]))).isoformat()
        if state.on_date is not None and closed_bd <= state.on_date:
            return False
    return True


def otc_overlay(book: AsOfBook, c: dict, state: State) -> Tuple[int, str]:
    """``(mark_c, how)``: the contract's VM mark in ``state`` - the latest ``otc_vm`` row of the state (0 after an STM settlement),
    a later RESET re-basing it (TRS: 0), else the booking mark ``mark0_c`` (0 for a TRS)."""
    cid = c["contract_id"]
    best = None
    for vd, mark, mode, eid in book.otc_vm.get(cid, []):
        if state.row_in(eid, vd):
            best = (vd, 0 if mode == "STM" else int(mark), f"VM of {vd}")
    for ed, detail_json, eid in book.otc_reset.get(cid, []):
        if state.row_in(eid, ed) and (best is None or ed >= best[0]):
            try:
                det = json.loads(detail_json or "{}")
            except ValueError:
                det = {}
            best = (ed, int(det.get("post_mark_c") or 0), f"reset of {ed}")
    if best is not None:
        return best[1], best[2]
    try:
        terms = json.loads(c.get("terms_json") or "{}")
    except ValueError:
        terms = {}
    return int(terms.get("mark0_c") or 0), "booking mark (before the first VM)"


def _overlay_view(cid: str, mark_c: int, how: str, quality: str = "computed") -> MarkView:
    from .units import from_c
    return MarkView(cid, from_c(int(mark_c)), quality, f"OTC variation-margin mark ({how})", "swap_overlay", None, None, "model", {"mark_kind": "OVERLAY", "how": how},
                    False, f"the contract's mark at its {how}")


def account_fig(book: AsOfBook, aid: str, state: State, chooser: Chooser, otc_value: Optional[OtcValuer] = None,
                loan_value: Optional[Callable[[str, str], Optional[int]]] = None, rate_of: Optional[Callable[[str], Optional[Decimal]]] = None) -> Fig:
    """One account's figures in ``state`` (``accounting._account_calc`` arithmetic on the state's entries)."""
    f = Fig()
    cost: Dict[str, int] = {}
    pl_inst: Dict[str, int] = {}
    for eid, gl, ccy, amt, base, iid in book.lines.get(aid, []):
        if not state.include(eid):
            continue
        spec = CHART.get(gl)
        if spec is None:
            continue
        f.gl[(gl, ccy)] = f.gl.get((gl, ccy), 0) + natural(gl, amt)
        if spec.kind in ("ASSET", "LIABILITY"):
            f.S[ccy] = f.S.get(ccy, 0) + amt
            f.B[ccy] = f.B.get(ccy, 0) + base
            if gl in COST_GLS and iid:
                cost[iid] = cost.get(iid, 0) + amt
        elif gl == "3000":
            f.capital_c += natural(gl, base)
        elif gl == "3100":
            f.transfers_c += natural(gl, base)
        else:
            bucket = pl_bucket(gl)
            if bucket is None:
                continue
            v = pl_amount(gl, natural(gl, base))
            setattr(f, bucket + "_c", getattr(f, bucket + "_c") + v)
            if iid:
                pl_inst[iid] = pl_inst.get(iid, 0) + (v if bucket in ("realised", "income") else -v)
    rates: Dict[str, Optional[Decimal]] = {}

    def rate(ccy: str) -> Optional[Decimal]:
        if ccy not in rates:
            rates[ccy] = Decimal(1) if ccy == "USD" else (rate_of(ccy) if rate_of is not None else None)
        return rates[ccy]

    qty: Dict[str, int] = {}
    for fl in book.fills.get(aid, []):
        if state.include(int(fl["entry_id"])):
            qty[fl["instrument_id"]] = qty.get(fl["instrument_id"], 0) + (int(fl["qty"]) if fl["side"] == "BUY" else -int(fl["qty"]))
    otc_ids = {c["contract_id"] for c in book.otc.get(aid, [])}
    loan_ids = {ln["instrument_id"] for ln in book.loans.get(aid, [])}

    def record(iid, inst, kind, ccy, q, cost_c, view, mv_c, unreal_c, extra=None):
        r = rate(ccy)
        mv_base = None if (mv_c is None or r is None) else fx_c(int(mv_c), r)
        unreal_base = None if (unreal_c is None or r is None) else fx_c(int(unreal_c), r)
        if unreal_c is None:
            f.missing.append(iid)
            f.worst = max(f.worst, QUALITY_RANK["unavailable"])
        else:
            f.worst = max(f.worst, QUALITY_RANK.get(view.quality, 5))
            f.U[ccy] = f.U.get(ccy, 0) + int(unreal_c)
            if kind not in ("FUTURE", "SWAP") and mv_base is not None:
                if mv_base >= 0:
                    f.mv_long_c += mv_base
                else:
                    f.mv_short_c += -mv_base
        f.has_positions = True
        d = {"account_id": aid, "qty": int(q), "cost_c": int(cost_c), "mv_c": mv_c, "mv_base_c": mv_base, "unrealised_c": unreal_base or 0,
             "pl_cum_c": pl_inst.get(iid, 0), "mark": None if view.mark is None else str(view.mark), "quality": view.quality if unreal_c is not None else "unavailable",
             "as_of": view.quote_time, **mark_labels(kind, view)}
        if extra:
            d.update(extra)
        f.positions[iid] = d
        f.pos_acct[(aid, iid)] = d

    for iid in sorted(set(qty) | {i for i in cost if i not in otc_ids}):
        q = qty.get(iid, 0)
        c = cost.get(iid, 0)
        if q == 0 and c == 0:
            continue
        inst = book.inst(iid)
        kind = inst.kind if inst is not None else "EQUITY"
        ccy = inst.currency if inst is not None else "USD"
        if iid in loan_ids or kind == "PRIVATE_LOAN":
            mv = loan_value(aid, iid) if loan_value is not None else None
            view = _overlay_view(iid, mv, "stored snapshot") if mv is not None else unavailable(iid, "private credit: carried at amortised cost")
            record(iid, inst, "PRIVATE_LOAN", ccy, q, c, view, mv, None if mv is None else int(mv) - c)
            continue
        mark, view = chooser(aid, iid, inst)
        mult = inst.multiplier if inst is not None else Decimal(1)
        scale = inst.price_scale if inst is not None else Decimal(1)
        if kind == "FUTURE":
            ref = fut_reference(book, aid, iid, state)
            ote = None if (mark is None or ref is None) else mul_c(q, mark - ref, mult, scale)
            record(iid, inst, kind, ccy, q, c, view, 0, ote, {"reference_px": None if ref is None else str(ref)})
            continue
        mv = None if mark is None else mul_c(q, mark, mult, scale)
        record(iid, inst, kind, ccy, q, c, view, mv, None if mv is None else mv - c)
    for crow in book.otc.get(aid, []):
        if not otc_exists(book, crow, state):
            continue
        cid = crow["contract_id"]
        if otc_value is not None:
            mv_c, view = otc_value(aid, crow, state)
        else:
            m, how = otc_overlay(book, crow, state)
            mv_c, view = m, _overlay_view(cid, m, how)
        c = cost.get(cid, 0)
        try:
            terms = json.loads(crow.get("terms_json") or "{}")
        except ValueError:
            terms = {}
        sign = -1 if terms.get("pay_fixed") or terms.get("receiver") is False else 1
        record(cid, None, "SWAP", crow["currency"], sign * (int(crow["notional_c"]) // 100), c, view, int(mv_c), int(mv_c) - c, {"kind": crow["kind"]})
    # NAV = sum over currencies fx(S + U); unrealised = fx(S + U) - fx(S); FX result = fx(S) - B (accounting module docstring)
    for ccy in sorted(set(f.S) | set(f.U)):
        S, U, B = f.S.get(ccy, 0), f.U.get(ccy, 0), f.B.get(ccy, 0)
        r = rate(ccy)
        if r is None:
            if S or U:
                f.fx_missing.append(ccy)
            continue
        total, at_cost = fx_c(S + U, r), fx_c(S, r)
        f.nav_c += total
        f.unrealised_c += total - at_cost
        if ccy != "USD":
            f.fx_result_c += at_cost - B
    for (gl, ccy), v in f.gl.items():
        r = rate(ccy)
        if r is None:
            continue
        if gl == "1010":
            f.settled_c += fx_c(v, r)
        elif gl in ("1200", "1250"):
            f.pending_c += fx_c(v, r)
        elif gl in ("2100", "2350"):
            f.pending_c -= fx_c(v, r)
    f.rates = rates
    f.by_account[aid] = f.nav_c
    return f


def scope_fig(book: AsOfBook, accounts: Iterable[str], state: State, chooser: Chooser, otc_value: Optional[OtcValuer] = None,
              loan_value=None, rate_of=None) -> Fig:
    out = Fig()
    for aid in accounts:
        out.add(account_fig(book, aid, state, chooser, otc_value, loan_value, rate_of))
    return out


# ---- mark choosers --------------------------------------------------------------------------------------------------------------
def dated_chooser(book: AsOfBook, d: str, fallback: Optional[Chooser] = None) -> Chooser:
    """The DATED mark of ``d`` (``accounting.dated_mark_view``), else ``fallback``'s answer, else unavailable (carried at cost)."""
    cache: Dict[str, Tuple[Optional[Decimal], MarkView]] = {}

    def choose(aid: str, iid: str, inst) -> Tuple[Optional[Decimal], MarkView]:
        if iid not in cache:
            v = dated_mark_view(book.conn, inst, d, book.sessions) if inst is not None else None
            if v is not None and v.mark is not None:
                cache[iid] = (v.mark, v)
            elif fallback is not None:
                cache[iid] = fallback(aid, iid, inst)
            else:
                cache[iid] = (None, unavailable(iid, f"no recorded close on or before {d}"))
        return cache[iid]
    return choose


def original_marks(row: Optional[dict]) -> Dict[str, dict]:
    """The positions of an original snapshot row's ``detail_json`` (``{instrument_id: {mark, quality, mv_c, unrealised_c, ...}}``)."""
    if not row:
        return {}
    try:
        det = json.loads(row.get("detail_json") or "{}")
    except (TypeError, ValueError):
        return {}
    pos = det.get("positions") if isinstance(det, dict) else None
    return pos if isinstance(pos, dict) else {}


def original_chooser(orig: Dict[str, dict], then: Chooser) -> Chooser:
    """The ORIGINAL snapshot's mark of a position it held (a None mark stays None: the original carried it at cost), else ``then``."""
    def choose(aid: str, iid: str, inst) -> Tuple[Optional[Decimal], MarkView]:
        p = orig.get(iid)
        if p is None or (p.get("account_id") and p.get("account_id") != aid):
            return then(aid, iid, inst)
        m = p.get("mark")
        if m is None:
            return None, unavailable(iid, "the original snapshot had no price: carried at cost")
        return Decimal(str(m)), MarkView(iid, Decimal(str(m)), str(p.get("quality") or "delayed"), "original snapshot", "snapshot", p.get("as_of"), None, "original",
                                         {"mark_kind": p.get("mark_kind") or "LIVE"}, False, "the mark the original snapshot used")
    return choose


def fallback_original(orig: Dict[str, dict]) -> Chooser:
    """For a position without a dated mark: the original snapshot's mark when it held the position, else unavailable."""
    return original_chooser(orig, lambda aid, iid, inst: (None, unavailable(iid, "no recorded close and no original mark")))


# ---- one business-date row (late snapshots, accounting.nav_as_of) ------------------------------------------------------------------
def row_as_of(conn: sqlite3.Connection, accounting, scope_key: str, accounts: List[str], d, live=None, book: Optional[AsOfBook] = None) -> dict:
    """The ``nav_snapshots`` row of ``scope_key`` as of business date ``d`` (module docstring BUSINESS DATE, DATED MARKS): every
    figure from the entries dated <= d, listed positions at their dated marks (a name with no stored mark falls back to the live
    snapshot ``live``, labelled stale), ``flows_day`` / ``pnl_day`` against the latest row before ``d``."""
    ds = _iso(d)
    book = book or AsOfBook(conn, accounting.instruments, accounts, accounting.sessions)
    fallback = None
    if live is not None:
        def fallback(aid, iid, inst):   # noqa: E306 - a live mark for a past date is labelled, never passed off as that day's close
            v = live.mark(iid)
            if v is None or v.mark is None or v.quality == "unavailable":
                return None, unavailable(iid, f"no recorded close on or before {ds} and no live mark")
            return v.mark, MarkView(iid, v.mark, "stale", v.source, v.method, v.quote_time, v.fetched_at, v.delay_label, v.inputs, False,
                                    f"no close recorded on or before {ds}: the live mark of the snapshot time")
    rate_of = None
    if live is not None:
        rate_of = lambda ccy: accounting._fx_rate(live, ccy)   # noqa: E731
    state = State(lambda eid: (book.bd(eid) or "9999-12-31") <= ds, ds)
    fig = scope_fig(book, accounts, state, dated_chooser(book, ds, fallback), rate_of=rate_of)
    return _row_from_fig(conn, scope_key, ds, fig, accounting.clock.epoch(), {"business_date": True})


def _row_from_fig(conn: sqlite3.Connection, key: str, ds: str, fig: Fig, ts: int, extra_detail: Optional[dict] = None) -> dict:
    prev = _last_snapshot(conn, key, ds)
    if prev is not None:
        flows_day = (fig.capital_c - int(prev["capital_cum_c"])) + (fig.transfers_c - int(prev["transfers_cum_c"]))
        pnl_day = fig.nav_c - int(prev["nav_c"]) - flows_day
    else:
        flows_day = fig.capital_c + fig.transfers_c
        pnl_day = fig.nav_c - flows_day
    detail = {"positions": fig.positions, "fx": {k: str(v) for k, v in fig.rates.items() if v is not None and k != "USD"}, "missing": fig.missing,
              "by_account": fig.by_account}
    detail.update(extra_detail or {})
    return {"account_id": key, "snap_date": ds, "ts": int(ts), "nav_c": fig.nav_c, "settled_c": fig.settled_c, "pending_c": fig.pending_c,
            "mv_long_c": fig.mv_long_c, "mv_short_c": fig.mv_short_c, "unrealised_c": fig.unrealised_c, "fx_result_c": fig.fx_result_c,
            "realised_cum_c": fig.realised_c, "income_cum_c": fig.income_c, "financing_cum_c": fig.financing_c, "fees_cum_c": fig.fees_c,
            "capital_cum_c": fig.capital_c, "transfers_cum_c": fig.transfers_c, "flows_day_c": flows_day, "pnl_day_c": pnl_day, "quality": fig.quality,
            "partial": 1 if fig.partial else 0, "detail_json": dumps(detail)}


# ---- groups: which entries the batch posted -------------------------------------------------------------------------------------
def _tokens(s: Optional[str]) -> Set[str]:
    return {t for t in str(s or "").split(":") if t}


def assign_groups(conn: sqlite3.Connection, book: AsOfBook, groups: List[dict]) -> Tuple[Dict[int, str], Dict[str, List[int]]]:
    """``({entry_id: group key}, {group key: [entry ids]})`` - every entry a group names: its ``entry_ids``; the entries of its
    fills (and of its orders' fills), their settlements and the sweeps linked to them; its settlements; its contracts' entries (a
    line on the contract or a reference to it), their settlements and linked sweeps; its transfers.  An entry two groups claim is
    refused (409 GROUP_OVERLAP)."""
    owner: Dict[int, str] = {}
    by_group: Dict[str, List[int]] = {}
    settle_cols = set()
    try:
        settle_cols = {r[1] for r in conn.execute('PRAGMA table_info("settlements")').fetchall()}
    except sqlite3.Error:
        pass
    all_fills = {f["fill_id"]: f for fl in book.fills.values() for f in fl}
    for g in groups:
        key = str(g.get("key"))
        refs = g.get("refs") or {}
        want: Set[int] = {int(x) for x in (refs.get("entry_ids") or [])}
        contracts = {str(x) for x in (refs.get("contract_ids") or [])}
        orders = {str(x) for x in (refs.get("order_ids") or [])}
        fills = {str(x) for x in (refs.get("fill_ids") or [])} | {fid for fid, f in all_fills.items() if f.get("order_id") in orders}
        settles = {str(x) for x in (refs.get("settle_ids") or [])}
        transfers = {str(x) for x in (refs.get("transfer_ids") or [])}
        for fid in fills:
            f = all_fills.get(fid)
            if f is not None:
                want.add(int(f["entry_id"]))
        if fills:
            q = ",".join("?" for _ in fills)
            settles |= {r[0] for r in conn.execute(f"SELECT settle_id FROM settlements WHERE fill_id IN ({q})", list(fills)).fetchall()}
        if contracts and {"ref_type", "ref_id"} <= settle_cols:
            q = ",".join("?" for _ in contracts)
            settles |= {r[0] for r in conn.execute(f"SELECT settle_id FROM settlements WHERE ref_type='otc' AND ref_id IN ({q})", list(contracts)).fetchall()}
        for crows in book.otc.values():                 # a contract's own settlements named in its terms (the CDX initial payment, an OTC premium)
            for c in crows:
                if c["contract_id"] in contracts:
                    try:
                        terms = json.loads(c.get("terms_json") or "{}")
                    except ValueError:
                        terms = {}
                    for k in ("settle_id", "premium_settle_id"):
                        if isinstance(terms, dict) and terms.get(k):
                            settles.add(str(terms[k]))
        for eid, e in book.entries.items():
            rt, rid = e["ref_type"], e["ref_id"]
            if (rt == "fill" and rid in fills) or (rt == "order" and rid in orders) or (rt == "settlement" and rid in settles) or (rt == "otc" and rid in contracts) \
                    or (rt == "transfer" and rid in transfers):
                want.add(eid)
        if settles:
            q = ",".join("?" for _ in settles)
            want |= {int(r[0]) for r in conn.execute(f"SELECT entry_id FROM settlements WHERE settle_id IN ({q}) AND entry_id IS NOT NULL", list(settles)).fetchall()}
        if contracts:
            for lines in book.lines.values():
                for eid, gl, ccy, amt, base, iid in lines:
                    if iid in contracts:
                        want.add(eid)
        linked = fills | orders | settles | contracts
        for r in conn.execute("SELECT transfer_id, entry_id, linked_type, linked_id FROM transfers").fetchall():
            if r["transfer_id"] in transfers or (r["linked_id"] and (r["linked_id"] in linked or (_tokens(r["linked_id"]) & linked))):
                want.add(int(r["entry_id"]))
        for eid in sorted(want):
            if eid not in book.entries:
                continue                       # an entry of another lifetime's accounts: not this restatement's
            if eid in owner and owner[eid] != key:
                raise ConflictError("GROUP_OVERLAP", f"journal entry {eid} belongs to two groups ({owner[eid]} and {key}); every entry belongs to exactly one",
                                    {"entry_id": eid, "groups": [owner[eid], key]})
            owner[eid] = key
            by_group.setdefault(key, []).append(eid)
    return owner, by_group


def batch_window(conn: sqlite3.Connection, batch_id: str, before_command: int) -> Optional[int]:
    """The id of the batch's first command: the first command (before this one) whose payload names ``batch_id``; None when none
    does.  Every entry posted at or after it belongs to the batch (module docstring THE SPLIT)."""
    r = conn.execute("SELECT MIN(id) FROM commands WHERE id < ? AND kind NOT LIKE 'quotes.%' AND instr(payload_json, ?) > 0",
                     (int(before_command), f'"{batch_id}"')).fetchone()
    return int(r[0]) if r is not None and r[0] is not None else None


# ---- the restatement ---------------------------------------------------------------------------------------------------------
@dataclass
class Restated:
    key: str
    snap_date: str
    accounts: List[str]
    original: Optional[dict]
    fig: Fig
    original_nav_c: int
    reproduced_c: int
    dating_c: int
    dating: List[dict]
    mark_policy: List[dict]
    mark_policy_c: int
    interest_c: int
    import_effect: List[dict]
    unassigned_c: int
    unassigned: List[int]
    residual_c: int


def _snapshot_command(conn: sqlite3.Connection, key: str, ds: str) -> Optional[int]:
    """The command that wrote the original snapshot ``key`` / ``ds`` (``jobs_done`` SNAPSHOT), when recorded."""
    r = conn.execute("SELECT command_id FROM jobs_done WHERE job='SNAPSHOT' AND key=? AND command_id IS NOT NULL", (f"{key}:{ds}",)).fetchone()
    return int(r[0]) if r is not None and r[0] is not None else None


def _reproduce(book: AsOfBook, accounts: List[str], orig: dict, cmd: Optional[int]) -> Tuple[int, Set[int]]:
    """``(N1, O)``: the original's NAV recomputed from the ledger it saw (the entries of the commands before its own; without a
    recorded command, the entries posted at or before its timestamp) and its own position values; O = those entries."""
    ts = int(orig.get("ts") or 0)
    if cmd is not None:
        seen = {eid for eid, e in book.entries.items() if e["command_id"] is not None and int(e["command_id"]) < cmd}
    else:
        seen = {eid for eid, e in book.entries.items() if int(e["ts"]) <= ts}
    acct = set(accounts)
    S: Dict[str, int] = {}
    for aid in acct:
        for eid, gl, ccy, amt, base, iid in book.lines.get(aid, []):
            spec = CHART.get(gl)
            if eid in seen and spec is not None and spec.kind in ("ASSET", "LIABILITY"):
                S[ccy] = S.get(ccy, 0) + amt
    try:
        det = json.loads(orig.get("detail_json") or "{}")
    except (TypeError, ValueError):
        det = {}
    rates = {k: Decimal(str(v)) for k, v in (det.get("fx") or {}).items()}
    nav = 0
    for ccy, v in S.items():
        r = Decimal(1) if ccy == "USD" else rates.get(ccy)
        nav += v if r is None else fx_c(v, r)
    for p in (det.get("positions") or {}).values():
        if isinstance(p, dict):
            nav += int(p.get("unrealised_c") or 0)
    return nav, seen


def restate_plan(conn: sqlite3.Connection, accounting, lifetime_id: str, dates: List[str], groups: List[dict], batch_id: str, command_id: int,
                 sessions=None) -> List[Restated]:
    """Every (scope, date) of the restatement with its split (module docstring THE SPLIT) - computed, nothing written."""
    accounts = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (lifetime_id,)).fetchall()]
    book = AsOfBook(conn, accounting.instruments, accounts, sessions or accounting.sessions)
    owner, by_group = assign_groups(conn, book, groups)
    interest = {eid for eid, e in book.entries.items() if str(e.get("idem_key") or "").startswith(f"reaccrue:{batch_id}:")}
    window = batch_window(conn, batch_id, command_id)
    batch_entries = {eid for eid, e in book.entries.items()
                     if e.get("actor") == "import" or (window is not None and e["command_id"] is not None and int(e["command_id"]) >= window)}
    unassigned_all = {eid for eid in batch_entries if eid not in owner and eid not in interest}
    excluded = set(owner) | interest | unassigned_all
    order = [str(g.get("key")) for g in groups]
    labels = {str(g.get("key")): str(g.get("label") or g.get("key")) for g in groups}
    out: List[Restated] = []
    scopes = [(a, [a]) for a in accounts] + [(f"{lifetime_id}:ALL", accounts)]
    for ds in dates:
        bd_le = lambda eid, ds=ds: (book.bd(eid) or "9999-12-31") <= ds   # noqa: E731
        for key, accts in scopes:
            orig_row = conn.execute("SELECT * FROM nav_snapshots WHERE account_id=? AND snap_date=?", (key, ds)).fetchone()
            orig = dict(orig_row) if orig_row is not None else None
            orig_pos = original_marks(orig)
            dated = dated_chooser(book, ds, fallback_original(orig_pos))
            if orig is not None:
                n1, seen = _reproduce(book, accts, orig, _snapshot_command(conn, key, ds))
                n0 = int(orig["nav_c"])
            else:
                n1, seen, n0 = 0, set(), 0
            p_state = State(lambda eid, f=bd_le: f(eid) and eid not in excluded, ds)
            # N2: the preserved business-date state at the original's marks (a position it did not hold: the dated mark)
            orig_otc = {iid: p for iid, p in orig_pos.items() if isinstance(p, dict) and p.get("mv_c") is not None}

            def otc_orig(aid, crow, st, orig_otc=orig_otc):
                p = orig_otc.get(crow["contract_id"])
                if p is not None and (not p.get("account_id") or p.get("account_id") == aid):
                    return int(p["mv_c"]), _overlay_view(crow["contract_id"], int(p["mv_c"]), "original snapshot")
                m, how = otc_overlay(book, crow, st)
                return m, _overlay_view(crow["contract_id"], m, how)
            n2 = scope_fig(book, accts, p_state, original_chooser(orig_pos, dated), otc_orig)
            n3 = scope_fig(book, accts, p_state, dated)
            # mark policy per position: (dated mark - original mark) on the same holding
            mp: List[dict] = []
            for (aid3, iid), p3 in sorted(n3.pos_acct.items()):
                p2 = n2.pos_acct.get((aid3, iid))
                if p2 is None:
                    continue
                a3 = int(p3.get("unrealised_c") or 0)
                a2 = int(p2.get("unrealised_c") or 0)
                if a3 != a2:
                    mp.append({"instrument_id": iid, "account_id": p3.get("account_id"), "qty": p3.get("qty"), "original_mark": p2.get("mark"),
                               "dated_mark": p3.get("mark"), "mark_kind": p3.get("mark_kind"), "mark_date": p3.get("mark_date"), "amount_c": a3 - a2})
            # interest corrections, then each group, then whatever the batch posted that no group names
            r_state = State(lambda eid, f=bd_le: f(eid) and (eid not in excluded or eid in interest), ds)
            n4 = scope_fig(book, accts, r_state, dated)
            running: Set[int] = set(interest)
            prev_nav = n4.nav_c
            effects: List[dict] = []
            for gk in order:
                running |= set(by_group.get(gk, []))
                st = State(lambda eid, f=bd_le, run=frozenset(running): f(eid) and (eid not in excluded or eid in run), ds)
                nav_g = scope_fig(book, accts, st, dated).nav_c
                effects.append({"key": gk, "label": labels.get(gk, gk), "amount_c": nav_g - prev_nav})
                prev_nav = nav_g
            all_state = State(bd_le, ds)
            fig = scope_fig(book, accts, all_state, dated)
            unassigned = sorted(eid for eid in unassigned_all if bd_le(eid))
            unassigned_c = fig.nav_c - prev_nav
            # dating: the entries behind N2 - N1
            dating_entries: List[dict] = []
            acct_set = set(accts)
            for eid in sorted(seen ^ {eid for eid in book.entries if p_state.include(eid)}):
                e = book.entries.get(eid)
                if e is None or eid in excluded or not (book.entry_accounts.get(eid, set()) & acct_set):
                    continue
                counted = eid in seen
                dating_entries.append({"entry_id": eid, "business_date": e["business_date"], "posted": ny_of(int(e["ts"])).strftime("%Y-%m-%d %H:%M"),
                                       "kind": e["kind"], "memo": str(e.get("memo") or "")[:140],
                                       "why": ("counted by the original but dated " + str(e["business_date"])) if counted
                                       else ("dated " + str(e["business_date"]) + ", posted after the original was taken")})
            dating_c = n2.nav_c - n1
            mark_policy_c = n3.nav_c - n2.nav_c
            interest_c = n4.nav_c - n3.nav_c
            residual = fig.nav_c - (n0 + dating_c + mark_policy_c + interest_c + sum(x["amount_c"] for x in effects))
            out.append(Restated(key, ds, list(accts), orig, fig, n0, n1, dating_c, dating_entries, mp, mark_policy_c, interest_c, effects, unassigned_c, unassigned,
                                residual))
    return out


def _figures(row: dict) -> dict:
    """The comparable figures of a version row (idempotency): every money column, quality, partial and the split."""
    keys = SNAPSHOT_FIELDS + ("quality", "partial") + SPLIT_FIELDS
    return {k: row.get(k) for k in keys}


def _dates(from_d: date, through_d: date) -> List[str]:
    out, d = [], from_d
    while d <= through_d:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _parse_date(payload: dict, key: str) -> date:
    try:
        return date.fromisoformat(str(payload.get(key) or ""))
    except ValueError as e:
        raise RejectError("BAD_DATE", f"{key} must be an ISO date (YYYY-MM-DD)", {key: payload.get(key)}) from e


def h_restate(ctx, payload: dict) -> dict:
    """``nav.restate {lifetime_id, from_date, through_date, reason, batch_id, groups}`` (C9d): writes a restated version of every
    account row and the ``{lifetime}:ALL`` row of every date in the range, each with its split; refuses (writing nothing) when a
    residual is not 0; idempotent per (batch, date).  ``groups`` = ``[{key, label, refs: {contract_ids, order_ids, fill_ids,
    settle_ids, transfer_ids, entry_ids}}]``.  Never from the UI."""
    if ctx.actor == "ui":
        raise Forbidden("RESTATE_FORBIDDEN", "a NAV restatement is written by the historical import, never from a page")
    conn = ctx.conn
    services = ctx.services
    accounting = getattr(services, "accounting", None)
    if accounting is None:
        raise RejectError("NOT_IN_THIS_BUILD", "the accounting service is not bound")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VERSIONS_TABLE,)).fetchone() is None:
        raise RejectError("NOT_IN_THIS_BUILD", "the NAV history table is missing (the navhistory module did not bind)")
    lid = str(payload.get("lifetime_id") or "")
    lrow = conn.execute("SELECT status FROM lifetimes WHERE lifetime_id=?", (lid,)).fetchone()
    if lrow is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid!r} does not exist")
    if lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {lid} is closed (History is read-only)")
    from_d, through_d = _parse_date(payload, "from_date"), _parse_date(payload, "through_date")
    if from_d > through_d:
        raise RejectError("BAD_RANGE", f"from_date {from_d} is after through_date {through_d}")
    today = accounting_day(ctx.now)
    if through_d >= today:
        raise ConflictError("RESTATE_RANGE", f"{through_d} is not a closed business date yet (the accounting day is {today}); restate dates before it",
                            {"accounting_day": today.isoformat()})
    if (through_d - from_d).days > 400:
        raise RejectError("BAD_RANGE", "a restatement covers at most 400 days")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise RejectError("REASON", "say why the NAV is restated")
    batch_id = str(payload.get("batch_id") or "").strip()
    if not batch_id:
        raise RejectError("BATCH_ID", "a restatement names its batch id")
    groups = payload.get("groups") or []
    if not isinstance(groups, list) or any(not isinstance(g, dict) or not g.get("key") for g in groups):
        raise RejectError("GROUPS", "groups is a list of {key, label, refs}")
    if len({str(g["key"]) for g in groups}) != len(groups):
        raise RejectError("GROUPS", "group keys must be unique")
    plan = restate_plan(conn, accounting, lid, _dates(from_d, through_d), groups, batch_id, ctx.command_id, ctx.sessions)
    bad = [r for r in plan if r.residual_c != 0]
    if bad:
        detail = [{"scope": r.key, "date": r.snap_date, "residual_c": r.residual_c, "reproduction_c": r.reproduced_c - r.original_nav_c,
                   "unassigned_c": r.unassigned_c, "unassigned_entries": r.unassigned[:20]} for r in bad[:20]]
        raise ConflictError("RESTATE_RESIDUAL", f"{len(bad)} restated row(s) do not explain to the cent (first: {bad[0].key} {bad[0].snap_date}, residual "
                            f"{bad[0].residual_c / 100:.2f}); nothing was written", {"rows": detail})
    written = skipped = 0
    rows_out: List[dict] = []
    for r in plan:
        original = r.original
        if original is None and r.fig.nav_c == 0 and not r.fig.has_positions and not any(r.fig.S.values()) and r.fig.capital_c == 0 and r.fig.transfers_c == 0:
            continue                                                       # nothing existed and nothing is restated: no row
        row = _row_from_fig(conn, r.key, r.snap_date, r.fig, ctx.ts, {"business_date": True, "restated": True})
        row.update({"original_nav_c": r.original_nav_c, "added": 0 if original is not None else 1,
                    "import_effect_json": dumps(r.import_effect), "interest_effect_c": r.interest_c, "mark_policy_c": r.mark_policy_c,
                    "mark_policy_json": dumps(r.mark_policy), "dating_c": r.dating_c, "dating_json": dumps(r.dating), "residual_c": r.residual_c})
        latest = snapshot_at(conn, r.key, r.snap_date)
        if latest is not None and latest.get("restated") and latest.get("batch_id") == batch_id and _figures(latest) == _figures(row):
            skipped += 1
            continue
        version = int(conn.execute(f"SELECT COALESCE(MAX(version), 0) FROM {VERSIONS_TABLE} WHERE account_id=? AND snap_date=?", (r.key, r.snap_date)).fetchone()[0]) + 1
        cols = ("account_id", "snap_date", "version", "ts", "command_id", "reason", "batch_id") + SNAPSHOT_FIELDS + ("quality", "partial") + SPLIT_FIELDS + ("detail_json",)
        vals = dict(row, version=version, ts=ctx.ts, command_id=ctx.command_id, reason=reason, batch_id=batch_id)
        conn.execute(f"INSERT INTO {VERSIONS_TABLE}({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [vals[c] for c in cols])
        written += 1
        rows_out.append({"scope": r.key, "date": r.snap_date, "version": version, "nav_c": row["nav_c"], "original_nav_c": r.original_nav_c,
                         "added": original is None, "dating_c": r.dating_c, "mark_policy_c": r.mark_policy_c, "interest_effect_c": r.interest_c,
                         "import_effect": r.import_effect, "residual_c": r.residual_c})
    ctx.event("NAV_RESTATED", ref_type="lifetime", ref_id=lid, lifetime_id=lid, from_date=from_d.isoformat(), through_date=through_d.isoformat(), batch_id=batch_id,
              reason=reason, written=written, unchanged=skipped)
    return {"status": "DONE", "lifetime_id": lid, "from_date": from_d.isoformat(), "through_date": through_d.isoformat(), "batch_id": batch_id,
            "written": written, "unchanged": skipped, "rows": rows_out,
            "split": [{"scope": r.key, "date": r.snap_date, "original_nav_c": r.original_nav_c, "restated_nav_c": r.fig.nav_c, "dating_c": r.dating_c,
                       "mark_policy_c": r.mark_policy_c, "interest_effect_c": r.interest_c, "import_effect": r.import_effect, "residual_c": r.residual_c}
                      for r in plan]}


# ---- module wiring ------------------------------------------------------------------------------------------------------------------
def bind(services) -> None:
    """``nav_snapshot_versions`` (additive, idempotent) - logged once by ``ensure_schema_ext``."""
    db = getattr(services, "db", None)
    if db is not None and callable(getattr(db, "ensure_schema_ext", None)):
        db.ensure_schema_ext("navhistory", SCHEMA)


def register(bus, services=None) -> None:
    bus.register("nav.restate", h_restate)
