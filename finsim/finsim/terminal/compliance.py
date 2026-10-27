"""Compliance notices — inform only (TERMINAL_DESIGN.md §14, §12.5; TERMINAL_DECISIONS §4: "not a compliance system").

Conventions
- A ``Notice`` names its rule, the static threshold used (``tables.COMPLIANCE_LIMITS``, each with "verify the current
  rule text"), the value measured, the data it was measured from and a plain-English message.  ``status`` is one of
  ``ok`` (checked, below the threshold), ``notice`` (at or above a reporting / attention threshold), ``breach`` (at or
  above a hard limit such as a position limit), ``info`` (a statement that applies regardless of size) or ``n/a``
  (the rule does not touch this order).  Nothing here raises for a rule outcome and nothing blocks: ``orders.preview``
  shows the list, ``order.submit`` ignores it.
- Which rules run follows the book's profile (``tables.BOOK_PROFILES``, §12.5): INDIVIDUAL → Reg T / FINRA 4210,
  pattern-day-trader, cash-account good faith, 13D/G, Section 16, wash sale, self-trading, Reg SHO, Rule 201; FUND →
  13F at USD 100 M, 13h-1, 13D/G, Section 16, Form SHO / 13f-2, option and futures limits, wash sale, self-trading, Reg
  SHO, Rule 201; INSTITUTION → the FUND set with 13F from the start, plus the private-markets notices (accredited /
  QP status, Rule 144 lock-ups, Form D, "13F does not cover private shares").  Beneficial ownership, 13F, 13h-1 and
  Form SHO aggregate over the lifetime (the book); self-trading looks across every own account of every open book.
- Data: positions and NAV from ``accounting`` on one mark snapshot; shares outstanding from the instrument master
  (``spec.shares_outstanding`` "as of ``shares_asof``", from the universe file); day low / previous close from the
  cached quote record; fills, lot reliefs, orders and settlements from the ledger tables.  A missing input makes the
  notice ``info`` with the reason, never a guess.
- Rule 201 trigger: ``day_low ≤ 0.90 × prev_close`` (the 10 % decline from the prior close, static); 13D/G 5 % and
  Section 16 10 % of shares outstanding; 13h-1 2 M shares or USD 20 M in a day (20 M / 200 M in a month); 13F USD 100 M
  of 13F securities (long listed equities, ETFs and options); Form SHO USD 10 M gross short; PDT 4+ day trades in
  5 business days with NAV below USD 25,000; option position limits at the default 250,000-contract tier per side
  (bullish = long calls + short puts, bearish = short calls + long puts; ``notice`` from 80 %, ``breach`` at the limit);
  CFTC / CME spot-month limits applied to the net position of a root across all listed months (an approximation,
  said so); wash sale = a loss sale with a buy of the same instrument within 30 days before or after (IRS 1091);
  self-trading = an opposite-side working order, or a fill within 60 s, in another own account on the same instrument.
- EU / UK 0.1 % / 0.5 % short disclosure concerns foreign listings (Build 2): no notice is produced in this build.

Build 2 (2026-10-21):
- Relevance (OA-26): an FX deal gets no securities notice at all (13F / 13D/G / Reg SHO / Rule 201 / wash sale do not apply to
  currencies); an order on a FOREIGN listing skips the US-only rules (13F, 13D/G and Section 16, 13h-1, Form SHO, Reg SHO, Rule
  201, US option / futures limits) and says once that the venue's own rules are not modelled.
- Wash sale (USX-23): a sale is checked against REPLACEMENT purchases only - the purchases that opened the lots this sale closes
  (first-in, first-out) are not counted.
- Position limits (SR-11): ``settings.position_limit_overrides`` ``{key: contracts}`` replaces the static table - a futures root
  (``"ES"``) for its spot-month limit, ``"OPTIONS"`` for the default per-underlying option limit, ``"OPTIONS:<UNDERLYING>"`` for one
  underlying - labelled "your override".
- One reading connection for the whole check (``marks.shared_reader``, ORC-5): the order preview's own connection when it runs
  inside one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from . import tables
from .books import profile_of, split_account_id
from .db import get_setting
from .instruments import EQUITY_KINDS
from .marks import shared_reader
from .risk import jsonable, PCT4
from .units import fmt_money, from_c, fx_c, mul_c, to_c

LIMITS = tables.COMPLIANCE_LIMITS
VERIFY = LIMITS["verify"]
SINGLE_NAME_KINDS = ("EQUITY", "ADR", "REIT", "PREFERRED")
BUY_SIDES = ("BUY", "BTO", "BUY_TO_COVER", "BTC")
SELL_SIDES = ("SELL", "STC", "SELL_SHORT", "STO")
WORKING = ("WORKING", "PARTIAL", "WAITING_PARENT")
WASH_DAYS = 30
SELF_TRADE_WINDOW_S = 60
PDT_SESSIONS = 5
PDT_TRADES = 4
OPTION_NOTICE_FRACTION = Decimal("0.8")
#: rules that concern US-listed securities only: not run for an order on a FOREIGN listing (OA-26)
US_ONLY_RULES = ("13F", "13F_FROM_START", "13H1", "13DG", "SECTION_16", "FORM_SHO", "REG_SHO", "RULE_201", "OPTION_LIMITS", "FUTURES_LIMITS")


def _foreign(inst) -> bool:
    g = str(getattr(inst, "session_group", "") or "")
    return g == "FOREIGN" or g.startswith("FOREIGN:")


@dataclass
class Notice:
    rule: str
    status: str                       # ok | notice | breach | info | n/a
    threshold: Optional[str]
    value: Optional[str]
    message: str
    code: str = ""
    data_used: dict = field(default_factory=dict)
    account_id: Optional[str] = None
    instrument_id: Optional[str] = None
    lifetime_id: Optional[str] = None
    verify: str = VERIFY

    def to_dict(self) -> dict:
        return jsonable(dict(self.__dict__))


@dataclass
class _Order:
    account_id: str
    inst: Any
    side: str
    qty: int
    pv: dict

    @property
    def buy(self) -> bool:
        return self.side in BUY_SIDES

    @property
    def iid(self) -> str:
        return self.inst.instrument_id


class _Ctx:
    """Everything the rules read for one lifetime (the book): profile, positions on one snapshot, NAV, dates, accounts."""

    def __init__(self, services, lid: str, snap=None):
        self.services = services
        self.acc = services.accounting
        self.snap = snap if snap is not None else services.marks.snapshot()
        self.lid = lid
        self.now = int(services.clock.epoch())          # the wall clock (the self-trading window); the snapshot's as_of only labels marks
        self.today: date = services.clock.today()
        with shared_reader(services.db) as conn:
            self.profile = profile_of(conn, lid)
            self.accounts = [r["account_id"] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (lid,)).fetchall()]
            try:
                raw = get_setting(conn, "position_limit_overrides", {}) or {}
            except Exception:   # noqa: BLE001 - an unreadable setting is the static table
                raw = {}
        self.limit_overrides: Dict[str, int] = {}
        for k, v in (raw.items() if isinstance(raw, dict) else []):
            try:
                if int(v) > 0:
                    self.limit_overrides[str(k).upper()] = int(v)
            except (TypeError, ValueError):
                continue
        self.rules = tuple(tables.BOOK_PROFILES.get(self.profile, {}).get("rules", ()))
        self.positions = self.acc.positions(f"{lid}:ALL", snapshot=self.snap)
        self.nav_c = int(self.acc.nav(f"{lid}:ALL", snapshot=self.snap).nav_c)
        self._elig = None

    def has(self, rule: str) -> bool:
        return rule in self.rules

    def rows(self, sql: str, params=()):
        with shared_reader(self.services.db) as conn:
            return conn.execute(sql, params).fetchall()

    def placeholders(self) -> str:
        return ",".join("?" for _ in self.accounts)

    def eligibility(self):
        if self._elig is None:
            from . import books as _books
            self._elig = _books.eligibility(self.services, self.lid, self.nav_c)
        return self._elig

    def notice(self, rule: str, status: str, threshold: Optional[str], value: Optional[str], message: str, code: str = "", data: Optional[dict] = None,
               account_id: Optional[str] = None, instrument_id: Optional[str] = None) -> Notice:
        return Notice(rule, status, threshold, value, message, code, dict(data or {}), account_id, instrument_id, self.lid)

    def quote(self, iid: str):
        """The cached observation: the snapshot's record, else the QuoteCache, else the mark provider's; never a fetch."""
        for src in (self.snap, getattr(self.services, "cache", None), getattr(self.services, "marks", None)):
            if src is None:
                continue
            fn = getattr(src, "quote", None) or getattr(src, "get", None)
            if fn is None:
                continue
            try:
                rec = fn(iid)
            except Exception:   # noqa: BLE001 - a source without the record is the same as no record
                rec = None
            if rec is not None:
                return rec
        return None

    def rate(self, ccy: str) -> Optional[Decimal]:
        if ccy == "USD":
            return Decimal(1)
        v = self.snap.fx(ccy)
        return None if (v is None or v.mark is None or v.quality == "unavailable") else v.mark

    def mark(self, iid: str) -> Optional[Decimal]:
        v = self.snap.mark(iid)
        return None if (v is None or v.mark is None or v.quality == "unavailable") else v.mark


def _pct(num: Decimal, den: Decimal) -> Optional[Decimal]:
    if den <= 0:
        return None
    return (num / den).quantize(PCT4)


def _pct_text(x: Optional[Decimal]) -> str:
    return "n/a" if x is None else f"{(x * 100).quantize(Decimal('0.01'))} %"


def _order_notional_c(ctx: _Ctx, o: _Order) -> Optional[int]:
    econ = o.pv.get("economics") if isinstance(o.pv.get("economics"), dict) else None
    native = None
    if econ and econ.get("gross") not in (None, ""):
        try:
            native = to_c(Decimal(str(econ["gross"])))
        except Exception:   # noqa: BLE001
            native = None
    if native is None:
        mark = ctx.mark(o.iid)
        if mark is None:
            return None
        native = mul_c(abs(int(o.qty)), mark, o.inst.multiplier, o.inst.price_scale)
    rate = ctx.rate(o.inst.currency)
    return None if rate is None else fx_c(native, rate)


def _expected_price(o: _Order) -> Optional[Decimal]:
    ex = o.pv.get("execution") if isinstance(o.pv.get("execution"), dict) else None
    if ex and ex.get("expected_price") not in (None, ""):
        try:
            return Decimal(str(ex["expected_price"]))
        except Exception:   # noqa: BLE001
            return None
    return None


# ---- INDIVIDUAL ---------------------------------------------------------------------------------------------------------------
def rule_reg_t(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    rule = "Reg T / FINRA 4210 margin"
    if o is not None:
        if o.buy and o.inst.kind in EQUITY_KINDS + ("OPTION",):
            n = _order_notional_c(ctx, o)
            half = None if n is None else to_c(from_c(n) * Decimal("0.5"))
            return [ctx.notice(rule, "info", "50 % initial (Reg T); FINRA 4210 maintenance 25 %", None if half is None else str(from_c(half)),
                               ("Reg T initial margin would be " + fmt_money(half) + " on " + fmt_money(n) if half is not None else "Reg T initial margin: gross unavailable") +
                               "; this terminal funds every trade in full (no margin loan), so the requirement is informational", "REG_T",
                               {"gross_c": n, "basis": "50 % of the order's gross"}, o.account_id, o.iid)]
        return [ctx.notice(rule, "n/a", "50 % initial (Reg T)", None, "not an opening purchase of a margin security", "REG_T", {}, o.account_id, o.iid)]
    fut = opt = 0
    for aid in ctx.accounts:
        ob = ctx.acc.obligations(aid)
        fut += int(ob.margin["futures_posted_c"])
        opt += int(ob.margin["options_posted_c"])
    return [ctx.notice(rule, "info", "50 % initial (Reg T); FINRA 4210 maintenance 25 %", str(from_c(fut + opt)),
                       f"Personal book: trades are fully funded; futures initial margin {fmt_money(fut)} and short-option margin {fmt_money(opt)} are moved to margin deposits",
                       "REG_T", {"futures_posted_c": fut, "options_posted_c": opt})]


def _day_trades(ctx: _Ctx, since: date) -> int:
    q = ctx.placeholders()
    rows = ctx.rows(f"SELECT account_id, instrument_id, trade_date, SUM(CASE side WHEN 'BUY' THEN 1 ELSE 0 END) b, SUM(CASE side WHEN 'SELL' THEN 1 ELSE 0 END) s"
                    f" FROM fills WHERE account_id IN ({q}) AND trade_date >= ? AND origin='ORDER' GROUP BY account_id, instrument_id, trade_date", ctx.accounts + [since.isoformat()])
    return sum(min(int(r["b"]), int(r["s"])) for r in rows)


def rule_pdt(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    rule = "Pattern day trader (FINRA 4210(f)(8)(B))"
    floor = int(LIMITS["PDT_EQUITY_FLOOR_C"])
    since = ctx.services.cal.add_business_days(ctx.today, -(PDT_SESSIONS - 1))
    count = _day_trades(ctx, since)
    would = 0
    if o is not None and o.inst.kind in EQUITY_KINDS + ("OPTION",):
        opp = "SELL" if o.buy else "BUY"
        row = ctx.rows("SELECT COUNT(*) FROM fills WHERE account_id=? AND instrument_id=? AND trade_date=? AND side=? AND origin='ORDER'",
                       (o.account_id, o.iid, ctx.today.isoformat(), opp))
        would = 1 if int(row[0][0]) > 0 else 0
    total = count + would
    data = {"day_trades_5_sessions": count, "since": since.isoformat(), "nav_c": ctx.nav_c, "this_order_is_a_day_trade": bool(would)}
    thr = f"{PDT_TRADES}+ day trades in {PDT_SESSIONS} business days with equity below {fmt_money(floor)}"
    if ctx.nav_c < floor and total >= PDT_TRADES:
        return [ctx.notice(rule, "notice", thr, f"{total} day trades; NAV {fmt_money(ctx.nav_c)}",
                           f"{total} day trades in the rolling {PDT_SESSIONS} sessions with NAV {fmt_money(ctx.nav_c)} below {fmt_money(floor)}: the pattern-day-trader rule would apply"
                           + (" (this order counts as a day trade)" if would else ""), "PDT", data, o.account_id if o else None, o.iid if o else None)]
    return [ctx.notice(rule, "ok", thr, f"{total} day trades; NAV {fmt_money(ctx.nav_c)}",
                       f"{total} day trades in the rolling {PDT_SESSIONS} sessions" + (f"; NAV {fmt_money(ctx.nav_c)} is above the {fmt_money(floor)} floor" if ctx.nav_c >= floor else "")
                       + (" (this order would count as a day trade)" if would else ""), "PDT", data, o.account_id if o else None, o.iid if o else None)]


def rule_good_faith(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    if o is None or o.side not in ("SELL", "STC"):
        return []
    rows = ctx.rows("SELECT s.due_date FROM settlements s JOIN fills f ON f.fill_id=s.fill_id WHERE s.account_id=? AND f.instrument_id=? AND f.side='BUY'"
                    " AND s.status='PENDING' AND s.amount_c < 0 ORDER BY s.due_date", (o.account_id, o.iid))
    rule = "Cash account good faith / free riding"
    if not rows:
        return [ctx.notice(rule, "ok", "sell only after the purchase has settled", None, "no unsettled purchase of this instrument in the account", "CASH_ACCOUNT_GOOD_FAITH", {}, o.account_id, o.iid)]
    due = rows[0]["due_date"]
    return [ctx.notice(rule, "notice", "sell only after the purchase has settled", f"purchase settles {due}",
                       f"selling {o.inst.symbol} before its purchase settles ({due}) is a good-faith / free-riding violation in a cash account; here the sale proceeds settle T+1 as usual",
                       "CASH_ACCOUNT_GOOD_FAITH", {"settles": due, "pending_purchases": len(rows)}, o.account_id, o.iid)]


# ---- beneficial ownership ---------------------------------------------------------------------------------------------------------
def _long_qty_by_name(ctx: _Ctx) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in ctx.positions:
        if p.kind in SINGLE_NAME_KINDS and p.qty > 0:
            out[p.instrument_id] = out.get(p.instrument_id, 0) + int(p.qty)
    return out


def rule_13dg(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    out: List[Notice] = []
    pct5, pct10 = LIMITS["BENEFICIAL_OWNERSHIP_13DG"], LIMITS["SECTION_16"]
    held = _long_qty_by_name(ctx)
    names = dict(held)
    if o is not None:
        if o.inst.kind in SINGLE_NAME_KINDS:
            names = {o.iid: held.get(o.iid, 0) + (int(o.qty) if o.buy else -int(o.qty))}
        else:
            return [ctx.notice("Section 13(d)/(g) 5 %", "n/a", "5 % of a class", None, f"{o.inst.symbol} is not a single-name equity security", "13DG", {}, o.account_id, o.iid)]
    best: Optional[Decimal] = None
    for iid, qty in sorted(names.items()):
        inst = ctx.services.instruments.get(iid)
        spec = (inst.spec or {}) if inst else {}
        so = spec.get("shares_outstanding")
        asof = spec.get("shares_asof")
        sym = inst.symbol if inst else iid
        aid = o.account_id if o else None
        if not so:
            if o is not None:
                out.append(ctx.notice("Section 13(d)/(g) 5 %", "info", "5 % of a class", None, f"shares outstanding for {sym} are not in the master: 13D/G cannot be measured", "13DG",
                                      {"qty": qty}, aid, iid))
            continue
        pct = _pct(Decimal(max(0, qty)), Decimal(int(so)))
        if pct is None:
            continue
        data = {"qty": qty, "shares_outstanding": int(so), "shares_asof": asof, "basis": f"shares outstanding as of {asof} (instrument master)"}
        best = pct if best is None or pct > best else best
        if pct >= pct10 and ctx.has("SECTION_16"):
            out.append(ctx.notice("Section 16 10 %", "notice", "10 % of a class", _pct_text(pct),
                                  f"{sym}: {qty:,} shares {'would be ' if o else 'are '}{_pct_text(pct)} of {int(so):,} shares outstanding (as of {asof}): Section 16 insider reporting and short-swing profit rules",
                                  "SECTION_16", data, aid, iid))
        if pct >= pct5:
            out.append(ctx.notice("Section 13(d)/(g) 5 %", "notice", "5 % of a class", _pct_text(pct),
                                  f"{sym}: {qty:,} shares {'would be ' if o else 'are '}{_pct_text(pct)} of {int(so):,} shares outstanding (as of {asof}): Schedule 13D/13G beneficial-ownership filing",
                                  "13DG", data, aid, iid))
        elif o is not None:
            out.append(ctx.notice("Section 13(d)/(g) 5 %", "ok", "5 % of a class", _pct_text(pct),
                                  f"{sym}: {qty:,} shares would be {_pct_text(pct)} of {int(so):,} shares outstanding (as of {asof})", "13DG", data, aid, iid))
    if o is None and not any(n.code == "13DG" and n.status == "notice" for n in out):
        out.append(ctx.notice("Section 13(d)/(g) 5 %", "ok", "5 % of a class", None if best is None else _pct_text(best),
                              "no single-name holding at or above 5 % of shares outstanding" + (f" (largest {_pct_text(best)})" if best is not None else "")
                              + "; shares outstanding from the instrument master", "13DG", {"names_checked": len(names)}))
    return out


# ---- FUND / INSTITUTION size rules --------------------------------------------------------------------------------------------------
def rule_13h1(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    rule = "Large trader (Rule 13h-1)"
    lim = LIMITS["LARGE_TRADER_13H1"]
    q = ctx.placeholders()
    first = ctx.today.replace(day=1).isoformat()
    rows = ctx.rows(f"SELECT f.trade_date, f.qty, f.gross_c, f.fx_rate, i.kind FROM fills f JOIN instruments i ON i.instrument_id=f.instrument_id"
                    f" WHERE f.account_id IN ({q}) AND f.trade_date >= ? AND f.origin='ORDER'", ctx.accounts + [first])
    d_sh = d_usd = m_sh = m_usd = 0
    today = ctx.today.isoformat()
    for r in rows:
        if r["kind"] not in EQUITY_KINDS + ("OPTION",):
            continue
        sh = int(r["qty"]) * (100 if r["kind"] == "OPTION" else 1)
        usd = fx_c(int(r["gross_c"]), Decimal(r["fx_rate"] or "1"))
        m_sh += sh
        m_usd += usd
        if r["trade_date"] == today:
            d_sh += sh
            d_usd += usd
    if o is not None and o.inst.kind in EQUITY_KINDS + ("OPTION",):
        n = _order_notional_c(ctx, o) or 0
        sh = int(o.qty) * (100 if o.inst.kind == "OPTION" else 1)
        d_sh, d_usd, m_sh, m_usd = d_sh + sh, d_usd + n, m_sh + sh, m_usd + n
    over = d_sh >= lim["shares_day"] or d_usd >= lim["usd_day_c"] or m_sh >= lim["shares_month"] or m_usd >= lim["usd_month_c"]
    thr = f"{lim['shares_day']:,} shares or {fmt_money(lim['usd_day_c'])} in a day; {lim['shares_month']:,} shares or {fmt_money(lim['usd_month_c'])} in a month"
    val = f"today {d_sh:,} shares / {fmt_money(d_usd)}; month {m_sh:,} / {fmt_money(m_usd)}"
    data = {"day_shares": d_sh, "day_usd_c": d_usd, "month_shares": m_sh, "month_usd_c": m_usd, "basis": "fills of the lifetime (equities, ETFs, options x 100)"}
    msg = ("large-trader identification threshold reached: Form 13H" if over else "below the large-trader identification thresholds") + f" ({val}" + (", incl. this order)" if o else ")")
    return [ctx.notice(rule, "notice" if over else "ok", thr, val, msg, "13H1", data, o.account_id if o else None, o.iid if o else None)]


def _thirteen_f_c(ctx: _Ctx) -> int:
    return sum(int(p.mv_base_c) for p in ctx.positions if p.qty > 0 and p.kind in EQUITY_KINDS + ("OPTION",) and p.mv_base_c is not None)


def rule_13f(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    out: List[Notice] = []
    rule = "Form 13F"
    lim = int(LIMITS["FORM_13F_C"])
    v = _thirteen_f_c(ctx)
    if o is not None and o.buy and o.inst.kind in EQUITY_KINDS + ("OPTION",):
        v += _order_notional_c(ctx, o) or 0
    data = {"thirteen_f_securities_c": v, "basis": "long listed equities, ETFs and options at market (the lifetime)" + (", incl. this order" if o else "")}
    aid, iid = (o.account_id, o.iid) if o else (None, None)
    if ctx.has("13F_FROM_START"):
        out.append(ctx.notice(rule, "info", "applies from the start (institutional investment manager)", str(from_c(v)),
                              f"13F applies: file quarterly (13F securities {fmt_money(v)})", "13F", data, aid, iid))
    elif v >= lim:
        out.append(ctx.notice(rule, "notice", fmt_money(lim) + " of 13F securities", str(from_c(v)), f"13F securities {fmt_money(v)} exceed {fmt_money(lim)}: quarterly Form 13F", "13F", data, aid, iid))
    else:
        out.append(ctx.notice(rule, "ok", fmt_money(lim) + " of 13F securities", str(from_c(v)), f"13F securities {fmt_money(v)} are below {fmt_money(lim)}", "13F", data, aid, iid))
    if ctx.has("PRIVATE_QP") and (any(p.kind == "PRIVATE" for p in ctx.positions) or (o is not None and o.inst.kind == "PRIVATE")):
        out.append(ctx.notice(rule, "info", "13F covers listed securities only", None, "13F does not cover private shares", "13F_PRIVATE", {}, aid, iid))
    return out


def rule_form_sho(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    rule = "Form SHO / Rule 13f-2 short reporting"
    lim = int(LIMITS["FORM_SHO_GROSS_SHORT_C"])
    v = sum(abs(int(p.mv_base_c)) for p in ctx.positions if p.qty < 0 and p.kind in EQUITY_KINDS and p.mv_base_c is not None)
    if o is not None and o.side == "SELL_SHORT":
        v += _order_notional_c(ctx, o) or 0
    data = {"gross_short_c": v, "basis": "short listed equities at market (the lifetime)" + (", incl. this order" if o else "")}
    aid, iid = (o.account_id, o.iid) if o else (None, None)
    if v >= lim:
        return [ctx.notice(rule, "notice", fmt_money(lim) + " gross short", str(from_c(v)), f"gross short positions {fmt_money(v)} reach {fmt_money(lim)}: monthly Form SHO", "FORM_SHO", data, aid, iid)]
    return [ctx.notice(rule, "ok", fmt_money(lim) + " gross short", str(from_c(v)), f"gross short positions {fmt_money(v)} are below {fmt_money(lim)}", "FORM_SHO", data, aid, iid)]


def rule_option_limits(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    out: List[Notice] = []
    rule = "Listed option position limits"
    default_lim = int(ctx.limit_overrides.get("OPTIONS") or LIMITS["OPTION_POSITION_LIMITS"]["default"])
    sides: Dict[str, Dict[str, int]] = {}

    def add(inst, qty: int):
        if inst is None or inst.kind != "OPTION" or inst.underlying_id is None:
            return
        s = sides.setdefault(inst.underlying_id, {"bull": 0, "bear": 0})
        long_ = qty > 0
        bull = (inst.right == "C") == long_
        s["bull" if bull else "bear"] += abs(int(qty))
    for p in ctx.positions:
        if p.kind == "OPTION":
            add(ctx.services.instruments.get(p.instrument_id), int(p.qty))
    if o is not None:
        if o.inst.kind != "OPTION":
            return []
        add(o.inst, int(o.qty) if o.buy else -int(o.qty))
        sides = {k: v for k, v in sides.items() if k == o.inst.underlying_id}
    for uid, s in sorted(sides.items()):
        own = ctx.limit_overrides.get(f"OPTIONS:{str(uid).upper()}")
        lim = int(own or default_lim)
        thr = (f"{lim:,} contracts per side (your override)" if (own or "OPTIONS" in ctx.limit_overrides)
               else f"{lim:,} contracts per side (default tier; the class's own limit may be lower)")
        worst = max(s["bull"], s["bear"])
        status = "breach" if worst >= lim else ("notice" if Decimal(worst) >= OPTION_NOTICE_FRACTION * lim else "ok")
        msg = {"breach": "at or above the position limit", "notice": "within 20 % of the position limit", "ok": "below the position limit"}[status]
        out.append(ctx.notice(rule, status, thr, f"bullish {s['bull']:,} / bearish {s['bear']:,}", f"{uid} options: {msg} ({s['bull']:,} bullish, {s['bear']:,} bearish contracts"
                              + (", incl. this order)" if o else ")"), "OPTION_LIMITS", {"underlying": uid, "bull": s["bull"], "bear": s["bear"], "limit": lim},
                              o.account_id if o else None, o.iid if o else None))
    return out


def rule_futures_limits(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    out: List[Notice] = []
    rule = "CFTC / CME position limits (spot month)"
    table = LIMITS["FUTURES_SPOT_MONTH"]
    net: Dict[str, int] = {}
    for p in ctx.positions:
        if p.kind == "FUTURE":
            inst = ctx.services.instruments.get(p.instrument_id)
            root = (inst.spec or {}).get("root") if inst else None
            if root:
                net[root] = net.get(root, 0) + int(p.qty)
    if o is not None:
        if o.inst.kind != "FUTURE":
            return []
        root = (o.inst.spec or {}).get("root")
        if not root:
            return []
        net = {root: net.get(root, 0) + (int(o.qty) if o.buy else -int(o.qty))}
    for root, n in sorted(net.items()):
        own = ctx.limit_overrides.get(str(root).upper())
        lim = own or table.get(root)
        if lim is None:
            out.append(ctx.notice(rule, "info", "no limit in the static table", f"net {n:+,}", f"{root}: no spot-month limit in the table", "FUTURES_LIMITS", {"root": root, "net": n},
                                  o.account_id if o else None, o.iid if o else None))
            continue
        worst = abs(n)
        status = "breach" if worst >= lim else ("notice" if Decimal(worst) >= OPTION_NOTICE_FRACTION * lim else "ok")
        msg = {"breach": "at or above the spot-month limit", "notice": "within 20 % of the spot-month limit", "ok": "below the spot-month limit"}[status]
        out.append(ctx.notice(rule, status, f"{lim:,} contracts ({root}{', your override' if own else ''}); applied to the net position across all listed months"
                              " (approximation)", f"net {n:+,}",
                              f"{root}: {msg} (net {n:+,} contracts" + (", incl. this order)" if o else ")"), "FUTURES_LIMITS", {"root": root, "net": n, "limit": lim},
                              o.account_id if o else None, o.iid if o else None))
    return out


# ---- trading-pattern rules (every profile) -------------------------------------------------------------------------------------
def rule_wash_sale(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    out: List[Notice] = []
    rule = "Wash sale (IRS 1091)"
    q = ctx.placeholders()
    since = (ctx.today - timedelta(days=WASH_DAYS)).isoformat()
    thr = f"a loss sale with a purchase of the same instrument within {WASH_DAYS} days before or after"
    if o is not None:
        sym = o.inst.symbol
        if o.buy:
            losses = ctx.rows(f"SELECT f.fill_id, f.trade_date, SUM(lr.realised_c) l FROM lot_relief lr JOIN fills f ON f.fill_id=lr.close_fill_id"
                              f" WHERE f.account_id IN ({q}) AND f.instrument_id=? AND f.trade_date >= ? GROUP BY f.fill_id HAVING SUM(lr.realised_c) < 0 ORDER BY f.trade_date",
                              ctx.accounts + [o.iid, since])
            if losses:
                total = sum(int(r["l"]) for r in losses)
                return [ctx.notice(rule, "notice", thr, fmt_money(total), f"buying {sym} within {WASH_DAYS} days after a loss sale ({len(losses)} sale(s), {fmt_money(total)} realised"
                                   f" since {losses[0]['trade_date']}) makes the loss a wash sale: the loss is disallowed and added to the new basis (informational)",
                                   "WASH_SALE", {"loss_sales": [{"fill_id": r["fill_id"], "trade_date": r["trade_date"], "realised_c": int(r["l"])} for r in losses]}, o.account_id, o.iid)]
            return [ctx.notice(rule, "ok", thr, None, f"no loss sale of {sym} in the last {WASH_DAYS} days", "WASH_SALE", {}, o.account_id, o.iid)]
        if o.side in ("SELL", "STC"):
            pos = next((p for p in ctx.positions if p.account_id == o.account_id and p.instrument_id == o.iid), None)
            px = _expected_price(o)
            if pos is None or pos.avg_px is None or px is None:
                return [ctx.notice(rule, "info", thr, None, f"cannot tell whether this sale of {sym} is a loss (no position or no expected price)", "WASH_SALE", {}, o.account_id, o.iid)]
            if px >= pos.avg_px:
                return [ctx.notice(rule, "ok", thr, None, f"a sale of {sym} at {px} against an average cost of {pos.avg_px} is not a loss", "WASH_SALE",
                                   {"expected_price": str(px), "avg_px": str(pos.avg_px)}, o.account_id, o.iid)]
            closing = _closing_open_fills(ctx, o)        # USX-23: the purchases that opened the lots this sale closes are not replacements
            buys = [b for b in ctx.rows(f"SELECT fill_id, trade_date, qty FROM fills WHERE account_id IN ({q}) AND instrument_id=? AND side='BUY' AND trade_date >= ?"
                                        f" ORDER BY trade_date", ctx.accounts + [o.iid, since]) if b["fill_id"] not in closing]
            msg = (f"this sale of {sym} at {px} is below the average cost {pos.avg_px}; a replacement purchase within the {WASH_DAYS} days before"
                   f" ({len(buys)} found since {buys[0]['trade_date']}, not counting the lots this sale closes) or after it makes the loss a wash sale"
                   f" (informational)" if buys else
                   f"this sale of {sym} at {px} is below the average cost {pos.avg_px}: buying it back within {WASH_DAYS} days would make the loss a wash sale")
            return [ctx.notice(rule, "notice" if buys else "ok", thr, f"{px} vs {pos.avg_px}", msg, "WASH_SALE",
                               {"expected_price": str(px), "avg_px": str(pos.avg_px), "recent_buys": [{"fill_id": b["fill_id"], "trade_date": b["trade_date"], "qty": int(b["qty"])} for b in buys],
                                "lots_closed_from": sorted(closing)}, o.account_id, o.iid)]
        return [ctx.notice(rule, "n/a", thr, None, "not a purchase or a sale of a held position", "WASH_SALE", {}, o.account_id, o.iid)]
    losses = ctx.rows(f"SELECT lr.lot_id, f.fill_id, f.account_id, f.instrument_id, f.trade_date, lr.realised_c, (SELECT open_fill_id FROM lots WHERE lot_id=lr.lot_id) open_fill"
                      f" FROM lot_relief lr JOIN fills f ON f.fill_id=lr.close_fill_id WHERE f.account_id IN ({q}) AND f.trade_date >= ? AND lr.realised_c < 0 ORDER BY f.trade_date",
                      ctx.accounts + [since])
    hits: Dict[str, dict] = {}
    for r in losses:
        d = date.fromisoformat(r["trade_date"])
        lo, hi = (d - timedelta(days=WASH_DAYS)).isoformat(), (d + timedelta(days=WASH_DAYS)).isoformat()
        buys = ctx.rows(f"SELECT fill_id, trade_date, qty FROM fills WHERE account_id IN ({q}) AND instrument_id=? AND side='BUY' AND trade_date BETWEEN ? AND ? AND fill_id <> ?",
                        ctx.accounts + [r["instrument_id"], lo, hi, r["open_fill"] or ""])
        if buys:
            h = hits.setdefault(r["instrument_id"], {"realised_c": 0, "sales": [], "buys": []})
            h["realised_c"] += int(r["realised_c"])
            h["sales"].append({"fill_id": r["fill_id"], "trade_date": r["trade_date"], "realised_c": int(r["realised_c"])})
            for b in buys:
                bd = {"fill_id": b["fill_id"], "trade_date": b["trade_date"], "qty": int(b["qty"])}
                if bd not in h["buys"]:
                    h["buys"].append(bd)
    for iid, h in sorted(hits.items()):
        inst = ctx.services.instruments.get(iid)
        out.append(ctx.notice(rule, "notice", thr, fmt_money(h["realised_c"]),
                              f"{inst.symbol if inst else iid}: a loss sale ({fmt_money(h['realised_c'])}) with a purchase within {WASH_DAYS} days ({len(h['buys'])} buy(s)): the loss is disallowed"
                              f" and added to the replacement basis (informational)", "WASH_SALE", h, None, iid))
    if not out:
        out.append(ctx.notice(rule, "ok", thr, None, f"no loss sale with a repurchase within {WASH_DAYS} days", "WASH_SALE", {"loss_sales_checked": len(losses)}))
    return out


def _closing_open_fills(ctx: _Ctx, o: _Order) -> set:
    """The fills that opened the lots a SELL / STC of ``o.qty`` would relieve (first-in, first-out, as the ledger relieves them)."""
    left = int(o.qty)
    out = set()
    for r in ctx.rows("SELECT open_fill_id, qty_remaining FROM lots WHERE account_id=? AND instrument_id=? AND side='LONG' AND qty_remaining > 0"
                      " ORDER BY open_ts, lot_id", (o.account_id, o.iid)):
        if left <= 0:
            break
        out.add(r["open_fill_id"])
        left -= int(r["qty_remaining"])
    return out


def rule_self_trading(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    rule = "Self-trading across own accounts"
    thr = f"an opposite-side working order, or a fill within {SELF_TRADE_WINDOW_S} s, in another own account on the same instrument"
    if o is not None:
        opp_sides = SELL_SIDES if o.buy else BUY_SIDES
        q = ",".join("?" for _ in opp_sides)
        orders = ctx.rows(f"SELECT o.order_id, o.account_id, o.side, o.qty - o.filled_qty rem FROM orders o JOIN accounts a ON a.account_id=o.account_id"
                          f" WHERE o.instrument_id=? AND o.account_id <> ? AND a.status='OPEN' AND o.status IN ('WORKING','PARTIAL','WAITING_PARENT') AND o.side IN ({q})",
                          [o.iid, o.account_id] + list(opp_sides))
        fills = ctx.rows("SELECT fill_id, account_id, side, qty, ts FROM fills WHERE instrument_id=? AND account_id <> ? AND ts >= ? AND side=?",
                         (o.iid, o.account_id, ctx.now - SELF_TRADE_WINDOW_S, "SELL" if o.buy else "BUY"))
        if orders or fills:
            parts = [f"working {r['side']} {int(r['rem']):,} in {r['account_id']} ({r['order_id']})" for r in orders] + \
                    [f"{r['side']} {int(r['qty']):,} filled {ctx.now - int(r['ts'])} s ago in {r['account_id']} ({r['fill_id']})" for r in fills]
            return [ctx.notice(rule, "notice", thr, f"{len(orders)} order(s), {len(fills)} fill(s)",
                               f"{o.inst.symbol}: this {o.side} would trade against your own account: " + "; ".join(parts) + " (a wash / cross trade between own accounts)",
                               "SELF_TRADING", {"orders": [dict(r) for r in orders], "fills": [dict(r) for r in fills]}, o.account_id, o.iid)]
        return [ctx.notice(rule, "ok", thr, None, f"no opposite-side working order or recent fill in another own account for {o.inst.symbol}", "SELF_TRADING", {}, o.account_id, o.iid)]
    rows = ctx.rows("SELECT o.instrument_id, o.account_id, o.side, o.order_id FROM orders o JOIN accounts a ON a.account_id=o.account_id"
                    " WHERE a.status='OPEN' AND o.status IN ('WORKING','PARTIAL','WAITING_PARENT') ORDER BY o.instrument_id, o.order_id")
    by_inst: Dict[str, list] = {}
    for r in rows:
        by_inst.setdefault(r["instrument_id"], []).append(r)
    out: List[Notice] = []
    for iid, rs in by_inst.items():
        buys = [r for r in rs if r["side"] in BUY_SIDES]
        sells = [r for r in rs if r["side"] in SELL_SIDES]
        pairs = [(b, s) for b in buys for s in sells if b["account_id"] != s["account_id"]]
        if pairs:
            inst = ctx.services.instruments.get(iid)
            out.append(ctx.notice(rule, "notice", thr, f"{len(pairs)} pair(s)",
                                  f"{inst.symbol if inst else iid}: opposite-side working orders across own accounts: " + "; ".join(f"{b['order_id']} {b['account_id']} vs {s['order_id']} {s['account_id']}" for b, s in pairs),
                                  "SELF_TRADING", {"pairs": [[b["order_id"], s["order_id"]] for b, s in pairs]}, None, iid))
    if not out:
        out.append(ctx.notice(rule, "ok", thr, None, "no opposite-side working orders across own accounts", "SELF_TRADING", {"working_orders": len(rows)}))
    return out


def rule_reg_sho(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    rule = "Reg SHO 203(b) locate"
    thr = "locate before a short sale"
    if o is not None:
        if o.side != "SELL_SHORT":
            return [ctx.notice(rule, "n/a", thr, None, "not a short sale", "REG_SHO", {}, o.account_id, o.iid)]
        b = o.pv.get("borrow") if isinstance(o.pv.get("borrow"), dict) else None
        if b and b.get("needed") and b.get("available", True):
            return [ctx.notice(rule, "ok", thr, f"{int(b.get('qty_available') or 0):,} available",
                               f"locate satisfied by the borrow leg: {int(b.get('qty_available') or 0):,} available, tier {b.get('tier')}, lender {b.get('lender')}", "REG_SHO",
                               {"locate": {k: b.get(k) for k in ("qty_available", "tier", "lender", "fee_rate", "basis")}}, o.account_id, o.iid)]
        return [ctx.notice(rule, "notice", thr, None, f"no locate for {o.inst.symbol} in this preview: the borrow leg (not this notice) decides whether the short can be placed", "REG_SHO", {},
                           o.account_id, o.iid)]
    shorts = [p for p in ctx.positions if p.qty < 0 and p.kind in EQUITY_KINDS]
    if not shorts:
        return [ctx.notice(rule, "n/a", thr, None, "no short positions", "REG_SHO", {})]
    bare = [p for p in shorts if not p.borrow or int(p.borrow.get("qty") or 0) < -int(p.qty)]
    if bare:
        return [ctx.notice(rule, "notice", thr, f"{len(bare)} of {len(shorts)}", "short positions without a live borrow for the full quantity: " + ", ".join(f"{p.symbol} ({p.account_id})" for p in bare),
                           "REG_SHO", {"bare": [p.instrument_id for p in bare]})]
    return [ctx.notice(rule, "ok", thr, f"{len(shorts)} short(s)", "every short position is backed by a borrow: " + ", ".join(f"{p.symbol} ({', '.join(p.borrow['borrow_ids'])})" for p in shorts),
                       "REG_SHO", {"shorts": [p.instrument_id for p in shorts]})]


def _rule_201_for(ctx: _Ctx, inst, aid: Optional[str], prefix: str) -> Notice:
    rule = "Rule 201 short-sale price test"
    trig = LIMITS["RULE_201_TRIGGER"]
    thr = f"day low at or below {(1 - trig) * 100:.0f} % of the previous close"
    rec = ctx.quote(inst.instrument_id)
    low = getattr(rec, "day_low", None) if rec is not None else None
    prev = getattr(rec, "prev_close", None) if rec is not None else None
    if low is None or prev is None or prev <= 0:
        return ctx.notice(rule, "info", thr, None, f"{inst.symbol}: day low / previous close unavailable from the cached quote; the price test cannot be checked", "RULE_201",
                          {"day_low": None if low is None else str(low), "prev_close": None if prev is None else str(prev)}, aid, inst.instrument_id)
    level = (Decimal(prev) * (Decimal(1) - trig)).quantize(Decimal("0.0001"))
    data = {"day_low": str(low), "prev_close": str(prev), "trigger_level": str(level), "quote_source": getattr(rec, "source", None), "quote_time": getattr(rec, "quote_time", None)}
    if Decimal(low) <= level:
        return ctx.notice(rule, "notice", thr, f"low {low} vs {level}", f"{prefix}{inst.symbol} fell 10 % or more from the previous close {prev} (day low {low}, trigger {level}): the Rule 201"
                          f" circuit breaker allows short sales only at a price above the current national best bid (an uptick) for the rest of today and the next session", "RULE_201", data,
                          aid, inst.instrument_id)
    return ctx.notice(rule, "ok", thr, f"low {low} vs {level}", f"{inst.symbol}: day low {low} is above the trigger {level} (previous close {prev})", "RULE_201", data, aid, inst.instrument_id)


def rule_201(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    if o is not None:
        if o.side != "SELL_SHORT":
            return [ctx.notice("Rule 201 short-sale price test", "n/a", "day low at or below 90 % of the previous close", None, "not a short sale", "RULE_201", {}, o.account_id, o.iid)]
        return [_rule_201_for(ctx, o.inst, o.account_id, "short sale: ")]
    out: List[Notice] = []
    for p in ctx.positions:
        if p.qty < 0 and p.kind in EQUITY_KINDS:
            inst = ctx.services.instruments.get(p.instrument_id)
            if inst is not None:
                out.append(_rule_201_for(ctx, inst, p.account_id, "short position: "))
    return out


# ---- private markets (INSTITUTION) -------------------------------------------------------------------------------------------------
def rule_private(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    out: List[Notice] = []
    privs = [p for p in ctx.positions if p.kind == "PRIVATE"]
    if o is not None and o.inst.kind != "PRIVATE":
        return []
    if o is None and not privs:
        return []
    e = ctx.eligibility()
    aid, iid = (o.account_id, o.iid) if o else (None, None)
    out.append(ctx.notice("Accredited investor / qualified purchaser", "info" if e.private_markets else "notice", f"Private Markets unlock at {fmt_money(e.qp_threshold_c)} NAV (profile {e.profile})",
                          fmt_money(e.nav_c), e.private_reason + " (Rule 506 accredited / qualified-purchaser status is assumed by the profile, not verified)", "PRIVATE_QP",
                          {"nav_c": e.nav_c, "qp_threshold_c": e.qp_threshold_c}, aid, iid))
    today = ctx.today.isoformat()
    if ctx.has("RULE_144"):
        locked = []
        for p in privs:
            if o is not None and p.instrument_id != o.iid:
                continue
            for l in p.lots:
                if l.lock_until and l.lock_until > today:
                    locked.append((p, l))
        if locked:
            for p, l in locked:
                out.append(ctx.notice("Rule 144 holding period / lock-up", "notice", "lock-up until the stated date; Rule 144 transfer restrictions", l.lock_until,
                                      f"{p.name}: {l.qty_remaining:,} shares locked up until {l.lock_until} (lot {l.lot_id}); transfer restrictions apply (Rule 144)", "RULE_144",
                                      {"lot_id": l.lot_id, "lock_until": l.lock_until, "qty": l.qty_remaining}, p.account_id, p.instrument_id))
        elif o is not None and o.side in ("SELL", "STC"):
            out.append(ctx.notice("Rule 144 holding period / lock-up", "ok", "lock-up until the stated date; Rule 144 transfer restrictions", None, f"{o.inst.name}: no lot is inside its lock-up", "RULE_144", {}, aid, iid))
    if ctx.has("FORM_D"):
        out.append(ctx.notice("Form D", "info", "the issuer's filing (Rule 506)", None, "Form D is the issuer's filing for the private placement, not the investor's (informational)", "FORM_D", {}, aid, iid))
    return out


RULE_FNS = {"REG_T": rule_reg_t, "PDT": rule_pdt, "CASH_ACCOUNT_GOOD_FAITH": rule_good_faith, "13DG": rule_13dg, "13H1": rule_13h1, "13F": rule_13f,
            "13F_FROM_START": rule_13f, "FORM_SHO": rule_form_sho, "OPTION_LIMITS": rule_option_limits, "FUTURES_LIMITS": rule_futures_limits,
            "WASH_SALE": rule_wash_sale, "SELF_TRADING": rule_self_trading, "REG_SHO": rule_reg_sho, "RULE_201": rule_201, "PRIVATE_QP": rule_private}
# rules folded into another function: FINRA_4210 -> REG_T; SECTION_16 -> 13DG; RULE_144, FORM_D -> PRIVATE_QP


def _run(ctx: _Ctx, o: Optional[_Order]) -> List[Notice]:
    out: List[Notice] = []
    seen = set()
    foreign = o is not None and _foreign(o.inst)
    for key in ctx.rules:
        fn = RULE_FNS.get(key)
        if fn is None or fn in seen:
            continue
        if foreign and key in US_ONLY_RULES:
            continue
        seen.add(fn)
        out.extend(fn(ctx, o))
    if foreign:
        exch = (o.inst.spec or {}).get("exchange") if isinstance(getattr(o.inst, "spec", None), dict) else None
        out.append(ctx.notice("Foreign venue rules", "info", None, None, f"{o.inst.symbol} trades on a foreign venue{f' ({exch})' if exch else ''}: the US"
                              " securities-law notices (13F, 13D/G, Section 16, 13h-1, Form SHO, Reg SHO, Rule 201) do not apply to it, and the venue's own"
                              " disclosure and short-selling rules are not modelled in this terminal", "FOREIGN_VENUE", {}, o.account_id, o.iid))
    return out


def profile_rules(profile: str) -> List[str]:
    """The rule keys a profile runs (``tables.BOOK_PROFILES``)."""
    return list(tables.BOOK_PROFILES.get(profile, {}).get("rules", ()))


def pretrade(services, account_id: str, inst, side: str, qty: int, pv: Optional[dict] = None, snapshot=None) -> List[Notice]:
    """Notices for one order before it is placed (the ``orders.preview`` hook: ``pv["compliance"]``).  Never a block.  An FX deal
    gets none (OA-26: no securities rule applies to a currency)."""
    if getattr(inst, "kind", None) == "FX":
        return []
    lid, _role = split_account_id(account_id)
    ctx = _Ctx(services, lid, snapshot)
    return _run(ctx, _Order(account_id, inst, (side or "").upper(), int(qty), dict(pv or {})))


def holdings(services, scope: str = "ALL", snapshot=None) -> List[Notice]:
    """Notices on the current holdings of every lifetime in ``scope`` (an account, ``{lifetime}:ALL``, ``ALL`` or
    ``ALL_BOOKS``), each evaluated for its whole book and filtered by the book's profile."""
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    nav = services.accounting.nav(scope, snapshot=snap)
    out: List[Notice] = []
    for lid in nav.lifetime_ids:
        out.extend(_run(_Ctx(services, lid, snap), None))
    return out


__all__ = ["Notice", "pretrade", "holdings", "profile_rules", "RULE_FNS", "VERIFY"]
