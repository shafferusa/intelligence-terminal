"""Standing settlement-funding instruction (SFI) of OTC contracts: buy the currency an obligation needs, with USD, two business days
before it falls due, as an audited FX desk spot deal (FX_CCS_PLAN rev 2, D9, section 4.3, K8; release 1.0.6.1, package P-D).

Conventions stated here
- THE TERM: ``settlement_funding {ccy: "BUY_WITH_USD" | "NONE"}`` in a contract's ``terms_json`` (written at booking; the owner's
  booking program sets EUR BUY_WITH_USD on both of his FX contracts).  The light command ``otc.settlement_funding {contract_id,
  currency, mode}`` (actor ui allowed) switches it: it appends one row to ``otc_funding_instructions`` (the latest row per contract and
  currency wins over the term; no contract row is ever edited) and writes one SETTINGS_CHANGED event with the before and after
  values.  SFI deals already booked stay: they are trades.
- OBLIGATIONS: a contract's scheduled outflows in a non-USD currency (``fx.contract_flows``: the products' K5 / K6 hooks - a swap's
  coupons and final exchange, a forward's delivery).  Only a KNOWN amount is bought: a floating coupon is known once its index is
  published, at its period end, exactly two business days before its payment.
- THE JOB ``OTC_CCY_FUNDING`` (added before SNAPSHOT): 10:00-16:30 ET on an FX business day (the FX_SPOT session open, its trade
  date today), key ``{account}:{ccy}:{due}``, for each due date that is TODAY's spot date of the currency against USD
  (``fx.spot_date``: ccycal for EUR / GBP) and carries an outflow of a contract whose instruction for that currency is ON.  Quiet
  otherwise (nothing within the next spot date).  The need = the ON obligations due that day - what the account will hold then:
  ``holding = settled 1010 + PENDING settlement rows due by then (receivable - payable) + the account's known OTC flows due by then
  (in - out), the ON obligations of the day left out``.  Each SFI deal booked for the date is a receivable due by then, so a second
  run buys nothing (idempotent by state as well as by its key).
- THE DEAL: ``fx.book_linked_spot`` BUY ccy (the need) / SELL USD at the executable mid + the desk spread (0.2 pip against the Fund),
  value = the due date, ``link {type "otc_funding", contracts, due, obligations, text}``; ``settle_ref`` ("xccy_event", contract)
  when every obligation it funds is one cross-currency swap's (its PAY / END settles it PvP), otherwise none (SETTLE_DUE settles it at
  00:30 of the due date, before the forward's delivery).  ``check_funding`` off: a USD leg the account and Treasury cannot pay waits
  SETTLEMENT_UNFUNDED with an ERROR at settlement (D9); nothing converts or moves cash on its own.
- PENDING OUTCOMES: no executable quote (QUOTE_STALE, SESSION_CLOSED ...) -> SKIPPED NO_QUOTE, quiet, retried every 5 minutes;
  from 16:25 ET (the last retry before 16:30) the SKIPPED is surfaced as a WARN SFI_NOT_FUNDED "the obligation may overdraw".  A
  coupon not yet known -> SKIPPED INDEX_PENDING (quiet until 16:25, then the same WARN).
- ``plan(services, account_id)`` (pure, K8) lists the upcoming obligations (known and projected), the purchases the instruction
  will make (funding trade date, value date, amount, the USD at today's mid) and the USD the PB account and Treasury will hold before
  each purchase (settled USD now + the USD flows and settlement rows due before it - the earlier purchases), with the shortfall
  (``gap_c``) where the USD falls short (D9: the 2028-10-31 EUR purchase for the forward's delivery).  The previews, the drawers, the
  Risk ladder and the owner's report read this one function.  Indicative: today's mid, projected coupons, no VM or transfers.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .clock import epoch_of, ny_of
from .commands import ConflictError, RejectError, TxContext
from .units import fmt_money, from_c, to_c

MODULE = "ccyfunding"
JOB = "OTC_CCY_FUNDING"
JOB_KIND = "lifecycle.otc_ccy_funding"
#: the contract term's modes (FX_CCS_PLAN 4.3): ``settlement_funding {ccy: "BUY_WITH_USD" | "NONE"}``
MODES = ("BUY_WITH_USD", "NONE")
ON = "BUY_WITH_USD"
FROM_ET, TO_ET, WARN_ET = "10:00", "16:30", "16:25"
OTC_KINDS = ("XCCY", "FX_FORWARD")
TABLE = ("CREATE TABLE IF NOT EXISTS otc_funding_instructions (id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT NOT NULL, currency TEXT NOT NULL,"
         " mode TEXT NOT NULL, ts INTEGER NOT NULL, command_id INTEGER, before_mode TEXT, note TEXT)")
INDEX = "CREATE INDEX IF NOT EXISTS ix_otc_funding_instructions ON otc_funding_instructions(contract_id, currency, id)"
SCHEMA = [TABLE, INDEX]
SFI_TEXT = ("standing settlement-funding instruction: each EUR obligation is bought with USD two business days before it falls due,"
            " as an FX desk spot deal (mid + 0.2 pip), value the due date")


def _fx():
    from . import fx
    return fx


# ---- the instruction ------------------------------------------------------------------------------------------------------------------
def _terms(row) -> dict:
    try:
        t = json.loads(row["terms_json"] or "{}")
    except (TypeError, ValueError):
        t = {}
    return t if isinstance(t, dict) else {}


def _has_table(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='otc_funding_instructions'").fetchone() is not None


def term_mode(row, ccy: str) -> str:
    """The booking's own term for ``ccy`` (``terms_json.settlement_funding``), "NONE" when absent."""
    sf = _terms(row).get("settlement_funding") or {}
    m = str(sf.get(str(ccy).upper()) or "NONE").upper() if isinstance(sf, dict) else "NONE"
    return m if m in MODES else "NONE"


def instruction(conn, row, ccy: str) -> dict:
    """The instruction in force for (contract, ``ccy``): the latest ``otc.settlement_funding`` row, else the booking's term.
    ``{mode, source ("term" | "command"), ts}``."""
    ccy = str(ccy).upper()
    if _has_table(conn):
        r = conn.execute("SELECT mode, ts FROM otc_funding_instructions WHERE contract_id=? AND currency=? ORDER BY id DESC LIMIT 1",
                         (row["contract_id"], ccy)).fetchone()
        if r is not None:
            return {"mode": r["mode"], "source": "command", "ts": int(r["ts"])}
    return {"mode": term_mode(row, ccy), "source": "term", "ts": None}


def _currencies(row) -> List[str]:
    """The non-USD currencies a contract's instruction can name (its term's keys and its legs' currencies)."""
    t = _terms(row)
    out = []
    sf = t.get("settlement_funding") or {}
    for c in (list(sf) if isinstance(sf, dict) else []) + [((t.get("legs") or {}).get(s) or {}).get("currency") for s in ("pay", "receive")] + \
            [t.get("buy_ccy"), t.get("sell_ccy"), t.get("pay_ccy"), t.get("recv_ccy")]:
        c = str(c or "").upper()
        if len(c) == 3 and c != "USD" and c not in out:
            out.append(c)
    return out


# ---- obligations and holdings ------------------------------------------------------------------------------------------------------------
def _open_contracts(conn, account_id: Optional[str] = None) -> list:
    sql = (f"SELECT c.* FROM otc_contracts c JOIN accounts a ON a.account_id=c.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE c.status='OPEN'"
           f" AND a.status='OPEN' AND l.status='OPEN' AND c.kind IN ({','.join('?' for _ in OTC_KINDS)})")
    params: list = list(OTC_KINDS)
    if account_id:
        sql += " AND c.account_id=?"
        params.append(account_id)
    return conn.execute(sql + " ORDER BY c.contract_id", params).fetchall()


def obligations(conn, services, account_id: str, ccy: Optional[str] = None) -> List[dict]:
    """Every scheduled non-USD outflow of the account's OPEN XCCY / FX_FORWARD contracts (or of ``ccy``): ``{contract_id, kind, ccy,
    date, amount_c (positive: owed), known, what, event, mode, instruction_source}``, in date order."""
    fx = _fx()
    out = []
    for row in _open_contracts(conn, account_id):
        for f in fx.contract_flows(conn, row, services):
            c = str(f.get("ccy") or "").upper()
            amt = f.get("amount_c")
            if c == "USD" or (ccy and c != str(ccy).upper()) or amt is None or int(amt) >= 0:
                continue
            ins = instruction(conn, row, c)
            out.append({"contract_id": row["contract_id"], "kind": row["kind"], "ccy": c, "date": str(f.get("date")), "amount_c": -int(amt),
                        "known": bool(f.get("known", True)), "what": f.get("what") or "", "event": f.get("event"), "mode": ins["mode"],
                        "instruction_source": ins["source"]})
    out.sort(key=lambda o: (o["date"], o["contract_id"], o["ccy"]))
    return out


def holding(conn, services, account_id: str, ccy: str, due: str, leave_out: Optional[List[Tuple[str, str, int]]] = None) -> dict:
    """What the account will hold in ``ccy`` on ``due``: settled 1010 + PENDING settlement rows due by then (receivable - payable) + the
    known OTC flows of its OPEN contracts due by then (in - out), leaving out ``leave_out`` flows ``(contract_id, date, amount_c)`` -
    the obligations being funded.  ``{settled_c, settlements_c, otc_c, total_c}``."""
    from .ledger import balance
    fx = _fx()
    settled = int(balance(conn, account_id, "1010", ccy))
    srow = conn.execute("SELECT COALESCE(SUM(amount_c), 0) s FROM settlements WHERE account_id=? AND currency=? AND status='PENDING' AND due_date<=?",
                        (account_id, ccy, due)).fetchone()
    pend = int(srow["s"] or 0)
    skip = list(leave_out or [])
    otc = 0
    for row in _open_contracts(conn, account_id):
        for f in fx.contract_flows(conn, row, services):
            if str(f.get("ccy") or "").upper() != ccy or f.get("amount_c") is None or not f.get("known", True) or str(f.get("date")) > due:
                continue
            k = (row["contract_id"], str(f.get("date")), int(f["amount_c"]))
            if k in skip:
                skip.remove(k)
                continue
            otc += int(f["amount_c"])
    return {"settled_c": settled, "settlements_c": pend, "otc_c": otc, "total_c": settled + pend + otc}


def need_for(conn, services, account_id: str, ccy: str, due: str) -> dict:
    """The purchase the instruction makes for (account, ``ccy``, ``due``): ``{need_c, obligations_c, holding, obligations, pending
    (unknown amounts), contracts, settle_ref}``.  ``need_c`` = max(0, ON obligations due that day - holding), never more than the
    obligations."""
    obs = [o for o in obligations(conn, services, account_id, ccy) if o["date"] == due and o["mode"] == ON]
    pending = [o for o in obs if not o["known"]]
    known = [o for o in obs if o["known"]]
    owed = sum(o["amount_c"] for o in known)
    h = holding(conn, services, account_id, ccy, due, [(o["contract_id"], o["date"], -o["amount_c"]) for o in known])
    need = max(0, min(owed, owed - h["total_c"]))
    cids = sorted({o["contract_id"] for o in known})
    kinds = {o["kind"] for o in known}
    settle_ref = ("xccy_event", cids[0]) if len(cids) == 1 and kinds == {"XCCY"} else None
    return {"account_id": account_id, "ccy": ccy, "due": due, "need_c": need, "obligations_c": owed, "holding": h, "obligations": known,
            "pending": pending, "contracts": cids, "settle_ref": settle_ref}


# ---- dates -------------------------------------------------------------------------------------------------------------------------------
def _pair(ccy: str) -> str:
    from .instruments import fx_pair_id
    return fx_pair_id(ccy)


def funding_trade_date(services, ccy: str, due) -> Optional[date]:
    """The FX trade date whose spot date is ``due`` (the latest weekday T before it with ``fx.spot_date(pair, T) == due``), or None
    when no trade date settles on it (a due date that is not a value date of the pair)."""
    fx = _fx()
    d = due if isinstance(due, date) else date.fromisoformat(str(due))
    pair = _pair(ccy)
    for back in range(1, 15):
        t = d - timedelta(days=back)
        if t.weekday() >= 5:
            continue
        try:
            if fx.spot_date(services, pair, t) == d:
                return t
        except Exception:   # noqa: BLE001 - an unknown pair: no funding date
            return None
    return None


def _fx_day(services, sessions, now: int) -> Optional[date]:
    """Today's FX trade date when the FX session is open and it is today's calendar date (10:00-16:30 ET is inside one FX day)."""
    try:
        st = sessions.state("FX_SPOT", int(now))
    except Exception:   # noqa: BLE001 - no session table: not an FX business day
        return None
    if not getattr(st, "open", False) or not getattr(st, "trade_date", None):
        return None
    td = date.fromisoformat(st.trade_date)
    return td if td == ny_of(int(now)).date() else None


def keys_due(conn, services, now: int, sessions) -> List[str]:
    """The job's keys now (module docstring THE JOB); quiet outside 10:00-16:30 ET of an FX business day and when nothing falls due
    on today's spot date."""
    fx = _fx()
    et = ny_of(int(now))
    hm = et.strftime("%H:%M")
    if hm < FROM_ET or hm > TO_ET:
        return []
    td = _fx_day(services, sessions, now)
    if td is None:
        return []
    keys: List[str] = []
    spot_of: Dict[str, str] = {}
    for row in _open_contracts(conn):
        for c in _currencies(row):
            if instruction(conn, row, c)["mode"] != ON:
                continue
            if c not in spot_of:
                try:
                    spot_of[c] = fx.spot_date(services, _pair(c), td).isoformat()
                except Exception:   # noqa: BLE001 - a pair without a spot date: nothing to fund
                    spot_of[c] = ""
            sd = spot_of[c]
            if not sd:
                continue
            for f in fx.contract_flows(conn, row, services):
                if str(f.get("ccy") or "").upper() != c or str(f.get("date")) != sd:
                    continue
                amt = f.get("amount_c")
                if amt is not None and int(amt) >= 0:
                    continue
                key = f"{row['account_id']}:{c}:{sd}"
                if key not in keys:
                    keys.append(key)
    done = set()
    for k in keys:
        r = conn.execute("SELECT status FROM jobs_done WHERE job=? AND key=?", (JOB, k)).fetchone()
        if r is not None and r["status"] == "DONE":
            done.add(k)
    return [k for k in keys if k not in done]


def _split_key(key: str) -> Tuple[str, str, str]:
    acct, ccy, due = str(key).rsplit(":", 2)
    return acct, ccy.upper(), due


# ---- the job's work --------------------------------------------------------------------------------------------------------------------
def fund_due(ctx: TxContext, key: str, services=None) -> dict:
    """One key of OTC_CCY_FUNDING (module docstring THE DEAL / PENDING OUTCOMES)."""
    services = services if services is not None else ctx.services
    fx = _fx()
    aid, ccy, due = _split_key(key)
    late = ny_of(ctx.ts).strftime("%H:%M") >= WARN_ET
    n = need_for(ctx.conn, services, aid, ccy, due)
    if n["pending"]:
        what = "; ".join(f"{o['contract_id']} {o['what']}" for o in n["pending"])
        note = f"{ccy} due {due}: the amount is not known yet ({what}): bought once the index is published"
        if late:
            return {"status": "SKIPPED", "code": "SFI_NOT_FUNDED", "note": note + "; not bought today - the obligation may overdraw " + ccy,
                    "details": {"account_id": aid, "currency": ccy, "due": due, "pending": n["pending"]}}
        return {"status": "SKIPPED", "code": "INDEX_PENDING", "quiet": True, "note": note}
    if n["need_c"] <= 0:
        return {"status": "DONE", "posted": False, "need": n,
                "note": (f"{ccy} {fmt_money(n['obligations_c'])} due {due} is covered: the account will hold {ccy} {fmt_money(n['holding']['total_c'])}"
                         if n["obligations_c"] else f"nothing owed in {ccy} on {due} under the instruction")}
    link = {"type": "otc_funding", "contracts": n["contracts"], "due": due, "currency": ccy,
            "obligations": [{"contract_id": o["contract_id"], "amount_c": o["amount_c"], "what": o["what"], "event": o["event"]} for o in n["obligations"]],
            "text": (f"settlement funding of {', '.join(n['contracts'])}: {ccy} {fmt_money(n['need_c'])} for the obligations due {due} "
                     f"({'; '.join(o['what'] for o in n['obligations'])})")}
    try:
        out = fx.book_linked_spot(ctx, aid, ccy, "USD", n["need_c"], ccy, due, None, link, n["settle_ref"], check_funding=False)
    except ConflictError as e:
        note = f"{ccy} {fmt_money(n['need_c'])} for {due} not bought: {e.code} - {e.message}"
        if late:
            return {"status": "SKIPPED", "code": "SFI_NOT_FUNDED", "note": note + f"; the obligation may overdraw {ccy}",
                    "details": {"account_id": aid, "currency": ccy, "due": due, "need_c": n["need_c"], "reason": e.code}}
        return {"status": "SKIPPED", "code": "NO_QUOTE", "quiet": True, "note": note + "; retried every 5 minutes until 16:30 ET"}
    deal = out["deal"]
    chk = out.get("funding") or {}
    ctx.event("OTC_SETTLEMENT_FUNDED", account_id=aid, ref_type="fx_deal", ref_id=deal["deal_id"], currency=ccy, due=due, need_c=n["need_c"],
              contracts=n["contracts"], usd_c=int(deal["sell_c"]), usd_fundable=bool(chk.get("fundable", True)))
    note = (f"bought {ccy} {fmt_money(int(deal['buy_c']))} / sold USD {fmt_money(int(deal['sell_c']))} at {deal['rate_quoted']} ({deal['deal_id']}), value {due},"
            f" for {', '.join(n['contracts'])}")
    if not chk.get("fundable", True):
        note += (f"; USD {fmt_money(int(deal['sell_c']))} is not available by {due} (short {fmt_money(int(chk.get('shortfall_c') or 0))}): the USD leg will"
                 " wait SETTLEMENT_UNFUNDED until cash is moved")
    return {"status": "DONE", "posted": True, "deal": deal, "entry_id": out["entry_id"], "settlements": out["settlements"], "need": n, "funding": chk, "note": note}


def _job(job_base):
    class OtcCcyFunding(job_base):
        """OTC_CCY_FUNDING (module docstring THE JOB)."""
        name = JOB
        kind = JOB_KIND
        module = MODULE
        when = "10:00-16:30 ET on FX business days: the obligations due on today's spot date (two business days ahead)"

        def due(self, conn, clock, cal, sessions) -> List[str]:
            return keys_due(conn, self.services, int(clock.epoch()), sessions)

        def run(self, ctx, key: str, services) -> dict:
            return fund_due(ctx, key, services)
    return OtcCcyFunding()


# ---- the command ------------------------------------------------------------------------------------------------------------------------------
def h_settlement_funding(ctx: TxContext, payload: dict) -> dict:
    """``otc.settlement_funding {contract_id, currency, mode}`` (light; module docstring THE TERM)."""
    cid = str(payload.get("contract_id") or "")
    ccy = str(payload.get("currency") or "").upper()
    mode = str(payload.get("mode") or "").upper()
    if not cid:
        raise RejectError("CONTRACT_REQUIRED", "contract_id is required")
    if len(ccy) != 3 or not ccy.isalpha() or ccy == "USD":
        raise RejectError("CURRENCY", f"currency must be the non-USD currency the instruction buys, got {payload.get('currency')!r}")
    if mode not in MODES:
        raise RejectError("MODE", f"mode must be one of {', '.join(MODES)}, got {payload.get('mode')!r}")
    conn = ctx.conn
    row = conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
    if row is None:
        raise RejectError("CONTRACT_UNKNOWN", f"contract {cid} does not exist")
    if row["kind"] not in OTC_KINDS:
        raise RejectError("KIND", f"{cid} is {row['kind']}: the settlement-funding instruction is for cross-currency swaps and FX forwards")
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}")
    if not _has_table(conn):
        ctx.services.db.ensure_schema_ext(MODULE, SCHEMA)
    before = instruction(conn, row, ccy)
    conn.execute("INSERT INTO otc_funding_instructions(contract_id, currency, mode, ts, command_id, before_mode, note) VALUES (?,?,?,?,?,?,?)",
                 (cid, ccy, mode, ctx.ts, ctx.command_id, before["mode"], str(payload.get("note") or "")[:200] or None))
    ctx.event("SETTINGS_CHANGED", account_id=row["account_id"], ref_type="otc", ref_id=cid, setting="settlement_funding", currency=ccy,
              before={"mode": before["mode"], "source": before["source"]}, after={"mode": mode})
    ctx.touched_accounts.add(row["account_id"])
    return {"contract_id": cid, "currency": ccy, "before": before["mode"], "after": mode,
            "note": ("SFI deals already booked stay (they are trades); " + ("from now on each " + ccy + " obligation is bought with USD two business days before"
                                                                            if mode == ON else "no " + ccy + " is bought for this contract from now on"))}


# ---- the plan (K8) ----------------------------------------------------------------------------------------------------------------------------
def _usd_per_unit(services, ccy: str, snapshot=None) -> Optional[Decimal]:
    fx = _fx()
    try:
        snap = snapshot if snapshot is not None else services.marks.snapshot()
        now = int(services.clock.epoch())
        leg = fx.usd_leg(services, snap, ccy, fx._session(services, now), now)
        return None if leg["rate"] is None else Decimal(str(leg["rate"]))
    except Exception:   # noqa: BLE001 - no rate: the plan says so
        return None


def plan(services, account_id: str, snapshot=None, extra=None) -> dict:
    """K8 (pure): the account's upcoming non-USD obligations, the purchases the instruction will make and the USD the PB account and
    Treasury will hold before each one (module docstring ``plan``).  ``extra`` adds the flows of a trade being quoted (``[{date, ccy,
    amount_c (+ in / - out), what, known, mode (default BUY_WITH_USD)}]``), so a quote shows its own gap before it is booked (D9).
    ``{account_id, as_of, available, obligations, purchases, ladder, gaps, usd_start, notes, method}``."""
    from .books import account_row
    from .ledger import balance
    fx = _fx()
    now = int(services.clock.epoch())
    today = ny_of(now).date().isoformat()
    with services.db.reader() as conn:
        acct = account_row(conn, account_id)
        if acct is None:
            raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
        tre_id = f"{acct['lifetime_id']}:TREASURY"
        obs = [o for o in obligations(conn, services, account_id) if o["date"] >= today]
        usd_pb = int(balance(conn, account_id, "1010", "USD"))
        usd_tre = int(balance(conn, tre_id, "1010", "USD")) if account_row(conn, tre_id) is not None else 0
        usd_events: List[Tuple[str, int, str]] = []
        for s in conn.execute("SELECT due_date, amount_c, leg, fx_deal_id FROM settlements WHERE account_id IN (?,?) AND currency='USD' AND status='PENDING'"
                              " ORDER BY due_date", (account_id, tre_id)).fetchall():
            usd_events.append((str(s["due_date"]), int(s["amount_c"]), f"pending settlement {s['leg'] or ''} {s['fx_deal_id'] or ''}".strip()))
        for row in _open_contracts(conn, account_id):
            for f in fx.contract_flows(conn, row, services):
                if str(f.get("ccy") or "").upper() == "USD" and f.get("amount_c") is not None and str(f.get("date")) >= today:
                    usd_events.append((str(f["date"]), int(f["amount_c"]), f"{row['contract_id']} {f.get('what') or ''}".strip()
                                       + ("" if f.get("known", True) else " (projected)")))
    for f in extra or []:                              # the trade being quoted (not booked yet)
        c, amt = str(f.get("ccy") or "").upper(), f.get("amount_c")
        if amt is None or str(f.get("date") or "") < today:
            continue
        if c == "USD":
            usd_events.append((str(f["date"]), int(amt), f"this quote: {f.get('what') or ''}".strip()))
        elif int(amt) < 0:
            obs.append({"contract_id": str(f.get("contract_id") or "this quote"), "kind": str(f.get("kind") or "QUOTE"), "ccy": c, "date": str(f["date"]),
                        "amount_c": -int(amt), "known": bool(f.get("known", True)), "what": f.get("what") or "", "event": f.get("event"),
                        "mode": str(f.get("mode") or ON).upper(), "instruction_source": "quote"})
    obs.sort(key=lambda o: (o["date"], o["contract_id"], o["ccy"]))
    rates: Dict[str, Optional[Decimal]] = {}
    purchases: Dict[Tuple[str, str], dict] = {}
    for o in obs:
        if o["mode"] != ON:
            continue
        k = (o["ccy"], o["date"])
        p = purchases.get(k)
        if p is None:
            ftd = funding_trade_date(services, o["ccy"], o["date"])
            p = purchases[k] = {"ccy": o["ccy"], "value_date": o["date"], "funding_trade_date": ftd.isoformat() if ftd else None, "amount_c": 0, "known": True,
                                "contracts": [], "what": []}
        p["amount_c"] += o["amount_c"]
        p["known"] = p["known"] and o["known"]
        if o["contract_id"] not in p["contracts"]:
            p["contracts"].append(o["contract_id"])
        p["what"].append(f"{o['contract_id']}: {o['what']}" + ("" if o["known"] or "index decides" in str(o["what"]) else " (projected; the index decides)"))
    rows = sorted(purchases.values(), key=lambda p: (p["value_date"], p["ccy"]))
    for p in rows:
        if p["ccy"] not in rates:
            rates[p["ccy"]] = _usd_per_unit(services, p["ccy"], snapshot)
        r = rates[p["ccy"]]
        p["usd_at_mid_c"] = None if r is None else to_c(from_c(p["amount_c"]) * r)
        p["usd_rate"] = None if r is None else str(r)
    # the USD timeline: start, then every USD flow / settlement due on or before each purchase's value date (a same-day inflow such as
    # the swap's final exchange settles PvP with the purchase), then the purchase; a gap is reported and assumed covered for what follows
    usd = usd_pb + usd_tre
    events = sorted(usd_events)
    i = 0
    gaps = []
    for p in rows:
        while i < len(events) and events[i][0] <= p["value_date"]:
            usd += events[i][1]
            i += 1
        p["usd_before_c"] = usd
        need = p.get("usd_at_mid_c")
        if need is None:
            p["gap_c"] = None
            p["gap_note"] = f"no USD rate for {p['ccy']} now: the USD this purchase needs is unavailable"
            continue
        p["gap_c"] = max(0, need - max(0, usd))
        if p["gap_c"]:
            gaps.append({"value_date": p["value_date"], "funding_trade_date": p["funding_trade_date"], "ccy": p["ccy"], "amount_c": p["amount_c"],
                         "usd_needed_c": need, "usd_available_c": usd, "gap_c": p["gap_c"],
                         "text": (f"{p['funding_trade_date'] or p['value_date']}: buying {p['ccy']} {fmt_money(p['amount_c'])} for value {p['value_date']} needs about USD"
                                  f" {fmt_money(need)} at today's mid; the PB account and Treasury will hold about USD {fmt_money(usd)} - short about"
                                  f" USD {fmt_money(p['gap_c'])}")})
        usd = max(0, usd - need)
    ladder = [{"date": o["date"], "ccy": o["ccy"], "amount_c": -o["amount_c"], "what": f"{o['contract_id']}: {o['what']}",
               "funded_by": "SFI" if o["mode"] == ON else "the account (instruction off)", "known": o["known"]} for o in obs]
    ladder += [{"date": p["value_date"], "ccy": p["ccy"], "amount_c": p["amount_c"], "what": f"SFI purchase (dealt {p['funding_trade_date']})",
                "usd_c": -p["usd_at_mid_c"] if p.get("usd_at_mid_c") is not None else None, "known": p["known"]} for p in rows]
    ladder.sort(key=lambda x: (x["date"], x["ccy"]))
    return {"account_id": account_id, "as_of": now, "available": True, "obligations": obs, "purchases": rows, "ladder": ladder, "gaps": gaps,
            "usd_start": {"pb_c": usd_pb, "treasury_c": usd_tre, "total_c": usd_pb + usd_tre},
            "notes": ["indicative: today's mid, projected coupons (the published indices decide), no variation margin, transfers or other trades",
                      "the USD the instruction draws on is the PB account's cash, then Treasury's (the US exchange account is never swept)"],
            "method": SFI_TEXT}


# ---- routes and wiring --------------------------------------------------------------------------------------------------------------------------
def register_routes(router) -> None:
    """GET /api/otc/funding_plan?account_id= (``plan``); POST /api/otc/settlement_funding {contract_id, currency, mode}."""
    from .api import ApiError, run_command

    def get_plan(req):
        aid = req.q("account_id")
        if not aid:
            raise ApiError(400, "ACCOUNT_REQUIRED", "account_id is required")
        return plan(req.rt, str(aid))

    def post_mode(req):
        return run_command(req, "otc.settlement_funding", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    router.add("GET", r"/api/otc/funding_plan", get_plan)
    router.add("POST", r"/api/otc/settlement_funding", post_mode)


def register(bus, services=None) -> None:
    bus.register("otc.settlement_funding", h_settlement_funding, light=True)


def bind(rt) -> None:
    """The instruction table (additive) and the job before SNAPSHOT; exception-free (a failure is logged)."""
    db = getattr(rt, "db", None)
    if db is not None and callable(getattr(db, "ensure_schema_ext", None)):
        try:
            db.ensure_schema_ext(MODULE, SCHEMA)
        except Exception as e:   # noqa: BLE001
            log = getattr(rt, "log", None)
            if log:
                log.warning("ccyfunding.bind: the instruction table could not be created: %s", e)
    try:
        from .lifecycle_eod import add_product_job
        from .lifecycle_jobs import Job
    except ImportError:
        return
    add_product_job(_job(Job), before="SNAPSHOT")


__all__ = ["plan", "register", "register_routes", "bind", "instruction", "term_mode", "obligations", "holding", "need_for", "funding_trade_date", "keys_due",
           "fund_due", "h_settlement_funding", "MODULE", "JOB", "MODES", "ON", "SCHEMA", "SFI_TEXT"]
