"""The instrument, quote, order, credit, FX, options, hedge, strategy, news, performance and OTC / private routes of the API
(TERMINAL_DESIGN.md §15; WP-11).  Companion of ``api.py``.

Conventions
- Fetch-triggering reads never fetch on the request thread: ``/resolve`` queues the symbol on the quote worker and
  answers 202 ``resolving`` until ``instruments.upsert`` has landed (an error recorded by the worker is answered 404 /
  400 and NOT re-queued, so the reason is never hidden by a fresh attempt); ``/chain`` and ``/hedges`` answer 202
  ``fetching`` while the single-flight chain request is pending; ``/bars`` subscribes an unknown symbol and answers 202.
- Previews (``/api/orders/preview``, ``/api/fx/preview``, ``/api/otc/preview``, ``/api/private/preview``,
  ``/api/strategies/preview``) are POSTs that write nothing and need no ``request_id``; every other POST is a command.
- A 4xx from ``order.submit`` is recorded as an ``order_rejections`` row (``orders.record_rejection``), except the
  "come back with more" answers (CONFIRM_REQUIRED, PREVIEW_STALE, REQUEST_ID_REUSED) which are not rejections.
- Routes of a desk whose module is not merged (prime WP-07, swaps WP-17 / WP-26, private WP-19, private credit WP-21,
  lifetimes WP-23) answer 501 NOT_BUILT with the package id; the handlers below are written against the documented
  module names so they come alive when those packages land (``getattr`` guarded, never a fake number meanwhile).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from . import books as _books, config
from .accounting import ScopeError
from .api import (ApiError, MAX_QUOTE_IDS, NotBuilt, NotFound, PACKAGES, RETRY_MS, Request, mark_dict, need, ok_or_raise, quote_dict, run_command,
                  OPEN_ORDER_STATUSES)
from .api_views import _account_or_404, account_row, order_full
from .commands import Result
from .instruments import EQUITY_KINDS, parse_futures_id

NOT_REJECTIONS = ("CONFIRM_REQUIRED", "PREVIEW_STALE", "REQUEST_ID_REUSED", "BAD_REQUEST_ID")
MARKET_ROWS = 60


def _inst_or_404(rt, iid: str):
    inst = rt.instruments.get(str(iid or "").strip().upper())
    if inst is None:
        raise NotFound(f"instrument {iid} is not in the master")
    return inst


def _inst_row(rt, inst, rec=None, now: int = 0, position: Optional[int] = None) -> dict:
    return {"instrument_id": inst.instrument_id, "symbol": inst.symbol, "name": inst.name, "kind": inst.kind, "currency": inst.currency, "support": inst.support,
            "status": inst.status, "session_group": inst.session_group, "quote": quote_dict(rec, now), "position": position,
            "optionable": bool(inst.cboe_symbol) and inst.kind in EQUITY_KINDS}


def _replay(res: Result) -> dict:
    return {"X-Replayed": "1"} if res.replayed else {}


def _quotes_for(rt, ids: List[str], now: int, priority: str = "PAGE") -> Dict[str, dict]:
    cache = getattr(rt, "cache", None)
    if cache is None or not ids:
        return {}
    cache.subscribe(ids, 90, priority)
    return {k: quote_dict(v, now) for k, v in cache.get_many(ids).items()}


# ---- markets (the account page lists) ---------------------------------------------------------------------------------------
def _materialise_option(rt, iid) -> None:
    """An OCC contract picked on a chain (or typed) is listed in the master on first use through ``options.ensure_option``:
    the underlying's cached Cboe chain must list it (nothing is listed from thin air; otherwise NOT_LISTED says to load the
    chain).  Called by resolve / preview / submit BEFORE any command, so the short writer it opens never nests inside a command
    transaction.  2026-10-21 market-hours check: a chain-picked contract failed INSTRUMENT_UNKNOWN "resolve it first", and
    resolving it through Yahoo registered it as a stock."""
    iid = str(iid or "").strip().upper()
    if not iid:
        return
    inst = rt.instruments.get(iid)
    if inst is None:
        opts = getattr(rt, "options", None)
        parse = getattr(opts, "parse_occ", None)
        if opts is None or parse is None or not hasattr(opts, "ensure_option") or parse(iid) is None:
            return
        inst = opts.ensure_option(rt, iid)
    if inst is None or inst.kind != "OPTION":
        return
    # its quote comes from the chain it is listed on: record it now (records_from_chain only runs at the next chain refresh)
    cache, chains = getattr(rt, "cache", None), getattr(rt, "chains", None)
    if cache is None or chains is None or not hasattr(chains, "peek") or cache.get(iid) is not None or not inst.underlying_id:
        return
    snap = chains.peek(inst.underlying_id)
    row = (snap.by_occ(inst.cboe_symbol or iid) or snap.by_occ(iid)) if snap is not None else None
    if row is not None:
        from .quotes.cache import record_from_chain_row
        cache.put(record_from_chain_row(inst, snap, row))


def markets_view(req: Request, aid: str, tab: Optional[str]) -> dict:
    rt = req.rt
    acct = _account_or_404(rt, aid)
    kind = _books.account_kind_for(acct)
    now = req.now
    with rt.db.reader() as conn:
        held = {r[0]: int(r[1]) for r in conn.execute("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (aid,)).fetchall()}
        q = ",".join("?" for _ in OPEN_ORDER_STATUSES)
        working = [r[0] for r in conn.execute(f"SELECT DISTINCT instrument_id FROM orders WHERE account_id=? AND status IN ({q})", (aid, *OPEN_ORDER_STATUSES)).fetchall()]
        watched = [r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM watchlists WHERE account_id=? ORDER BY position", (aid,)).fetchall()]
        loans = [dict(r) for r in conn.execute("SELECT * FROM loans WHERE account_id=? AND status='OPEN' ORDER BY opened_date", (aid,)).fetchall()]
        borrows = [dict(r) for r in conn.execute("SELECT * FROM borrows WHERE account_id=? AND status='OPEN' ORDER BY opened_ts", (aid,)).fetchall()]
    master = rt.instruments
    tabs: List[dict] = []
    rows: List[dict] = []
    extra: dict = {}

    def pick(ids: List[str]) -> List[str]:
        out, seen = [], set()
        for i in ids:
            if i and i not in seen and master.get(i) is not None:
                out.append(i)
                seen.add(i)
        return out

    def fill(kinds, n: int, pred=None) -> List[str]:
        return [i.instrument_id for i in sorted((x for x in master.all() if x.kind in kinds and x.status == "ACTIVE" and (pred is None or pred(x))), key=lambda x: x.symbol)[:n]]

    if kind in ("US_EXCHANGE", "PB"):
        tabs = [{"key": "equities", "label": "Equities & ETFs"}, {"key": "options", "label": "Options underlyings"}, {"key": "futures", "label": "Futures"}]
        if kind == "PB":
            tabs.append({"key": "otc", "label": "OTC"})
        tab = tab or "equities"
        if tab == "equities":
            ids = pick(list(held) + working + watched)
            ids += [i for i in fill(EQUITY_KINDS, MARKET_ROWS, lambda x: x.support == "FULL") if i not in ids][:max(0, MARKET_ROWS - len(ids))]
        elif tab == "options":
            unders = []
            for i in list(held) + working + watched:
                inst = master.get(i)
                if inst is None:
                    continue
                if inst.kind == "OPTION" and inst.underlying_id:
                    unders.append(inst.underlying_id)
                elif inst.cboe_symbol:
                    unders.append(i)
            ids = pick(unders)
            ids += [i for i in fill(EQUITY_KINDS + ("INDEX",), 30, lambda x: bool(x.cboe_symbol)) if i not in ids][:max(0, 30 - len(ids))]
        elif tab == "futures":
            front: Dict[str, object] = {}
            for inst in master.all():
                if inst.kind != "FUTURE" or inst.status != "ACTIVE":
                    continue
                parsed = parse_futures_id(inst.instrument_id)
                root = parsed[0] if parsed else inst.symbol
                cur = front.get(root)
                if cur is None or (inst.expiry or "") < (cur.expiry or ""):
                    front[root] = inst
            ids = pick(list(held) + working) + [i.instrument_id for i in sorted(front.values(), key=lambda x: (x.session_group, x.symbol)) if i.instrument_id not in held]
            extra["groups"] = sorted({master.get(i).session_group for i in ids if master.get(i)})
        elif tab == "otc":
            ids = []
            swaps = getattr(rt, "swaps", None)
            extra["otc"] = {"state": "available" if swaps is not None else f"NOT_BUILT {PACKAGES['swaps']}", "kinds": ["IRS", "TRS", "XCCY"],
                            "not_in_this_build": ["OTC options", "FX options", "FRAs", "caps"]}
        else:
            raise ApiError(400, "BAD_REQUEST", f"unknown tab {tab!r}", {"tabs": [t["key"] for t in tabs]})
    elif kind == "CREDIT":
        tabs = [{"key": "loans", "label": "Currency loans"}, {"key": "borrows", "label": "Stock borrows"}, {"key": "rates", "label": "Rates"}]
        tab = tab or "loans"
        ids = pick(list(held) + working + watched)
        rates = getattr(rt, "rates", None)
        extra = {"loans": loans, "borrows": borrows, "rates": rates.view() if rates is not None and hasattr(rates, "view") else None,
                 "repo": {"state": "NOT_IN_THIS_BUILD", "note": "repo / reverse repo and lending out of inventory are not in this build"}}
    elif kind == "FOREIGN_FX":
        tabs = [{"key": "fx", "label": "FX spot"}, {"key": "listings", "label": "Foreign listings"}]
        tab = tab or "fx"
        if tab == "listings":
            # held / working / watched first, then every foreign listing of the master (account kind FOREIGN_FX)
            ids = pick(list(held) + working + watched
                       + fill(("EQUITY", "ETF", "ADR", "REIT", "PREFERRED"), 400, lambda x: "FOREIGN_FX" in x.account_kinds))
            extra["note"] = ("foreign listings show live (delayed) quotes and marks; trading them is not in this build "
                             "(a foreign session and FX settlement are needed)")
        else:
            ids = fill(("FX",), 40)
    elif kind == "DEBT":
        tabs = [{"key": "curve", "label": "Treasury curve"}, {"key": "bonds", "label": "Bonds (LIMITED)"}]
        tab = tab or "curve"
        ids = fill(("BOND",), MARKET_ROWS) if tab == "bonds" else []
        extra["curve"] = curve_view(rt, now)
        extra["note"] = "model price, not executable in this build"
    elif kind == "PRIVATE":
        tabs = [{"key": "companies", "label": "Private companies"}]
        tab = tab or "companies"
        ids = fill(("PRIVATE",), 200)
        extra["note"] = "negotiated secondaries; last-round marks, valuation only without a sourced price"
    else:   # TREASURY
        tabs = [{"key": "cash", "label": "Cash"}]
        tab = tab or "cash"
        ids = []
        extra["cash"] = rt.accounting.cash(aid)
    recs = _quotes_for(rt, ids, now)
    snap = rt.marks.snapshot(ids) if ids else None
    for i in ids:
        inst = master.get(i)
        row = _inst_row(rt, inst, None, now, held.get(i))
        row["quote"] = recs.get(i)
        row["mark"] = mark_dict(snap.mark(i)) if snap is not None else None
        row["group"] = inst.session_group
        rows.append(row)
    out = {"account_id": aid, "kind": acct["kind"], "role": acct["role"], "tab": tab, "tabs": tabs, "rows": rows, "sessions": rt.sessions.states(), "server_time": now}
    out.update(extra)
    return out


def curve_view(rt, now: int) -> dict:
    curve = getattr(rt, "curve", None)
    if curve is None:
        raise NotBuilt(PACKAGES["curve"], "curve")
    pc = curve.latest()
    if pc is None:
        return {"par": [], "as_of": None, "shifts": {}, "shift_inputs": {}, "quality": "unavailable", "source": None, "fetched_at": None,
                "note": "no par curve has been fetched yet (Treasury.gov daily par yield curve)"}
    shifts, inputs = curve.intraday_shifts(getattr(rt, "cache", None))
    today = rt.clock.today()
    fresh = pc.as_of >= rt.cal.prev_business_day(today)
    return {"par": [{"tenor": k, "yield": str(v)} for k, v in pc.labels().items()], "points": {str(k): str(v) for k, v in sorted(pc.points.items())},
            "as_of": pc.as_of.isoformat(), "shifts": {str(k): str(v) for k, v in shifts.items()}, "shift_inputs": inputs, "quality": "close" if fresh else "stale",
            "source": pc.source, "fetched_at": pc.fetched_at, "shift_grid_bp": [-50, -25, 0, 25, 50]}


# ---- the routes --------------------------------------------------------------------------------------------------------------
def register_routes(r) -> None:
    def instruments(req: Request):
        rt = req.rt
        q = req.q("q") or ""
        kinds = tuple(k.strip().upper() for k in (req.q("kinds") or "").split(",") if k.strip())
        limit = req.q_int("limit", 20, 1, 200)
        hits = rt.instruments.search(q, kinds, limit)
        if req.q("account"):
            acct = _account_or_404(rt, req.q("account"))
            ak = _books.account_kind_for(acct)
            hits = [i for i in hits if rt.instruments.tradeable_in(i, ak)]
        cache = getattr(rt, "cache", None)
        recs = cache.get_many([i.instrument_id for i in hits]) if cache is not None and hits else {}
        return [_inst_row(rt, i, recs.get(i.instrument_id), req.now) for i in hits]

    def resolve(req: Request):
        rt = req.rt
        sym = (req.q("symbol") or "").strip().upper()
        if not sym:
            raise ApiError(400, "BAD_REQUEST", "symbol is required")
        _materialise_option(rt, sym)
        inst = rt.instruments.get(sym)
        if inst is None:
            exact = [i for i in rt.instruments.search(sym, (), 5) if i.symbol.upper() == sym]
            inst = exact[0] if exact else None
        if inst is not None:
            if inst.support == "UNSUPPORTED":
                raise ApiError(400, "UNSUPPORTED", "foreign listings and crypto are not in this build", {"symbol": sym, "instrument_id": inst.instrument_id,
                                                                                                        "exchange": (inst.spec or {}).get("exchange")})
            d = inst.to_dict()
            d["optionable"] = bool(inst.cboe_symbol) and inst.kind in EQUITY_KINDS
            d["exchange"] = (inst.spec or {}).get("exchange")
            d["cik"] = (inst.spec or {}).get("cik")
            return d
        worker = getattr(rt, "worker", None)
        if worker is None:
            raise NotFound(f"{sym} is not in the master and this runtime has no resolver (quote worker)", {"symbol": sym})
        st = worker.resolve_status(sym)
        if st.get("status") == "error":
            http = st.get("http_status")
            raise ApiError(404 if http in (404, 400, None) else 503, "NOT_FOUND" if http in (404, 400, None) else "PROVIDER_DOWN",
                           f"{sym}: {st.get('message')}", {"symbol": sym, "provider_status": http})
        if st.get("status") != "resolving":
            worker.resolve(sym)
        return 202, {"status": "resolving", "retry_ms": RETRY_MS, "symbol": sym}

    def instrument(req: Request):
        rt = req.rt
        inst = _inst_or_404(rt, req.params[0])
        iid = inst.instrument_id
        recs = _quotes_for(rt, [iid], req.now)
        snap = rt.marks.snapshot([iid])
        with rt.db.reader() as conn:
            positions = [dict(p) for p in conn.execute("SELECT v.account_id, v.qty, v.cost_c FROM v_positions v JOIN accounts a ON a.account_id=v.account_id"
                                                       " JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE v.instrument_id=? AND v.qty != 0 AND l.status='OPEN'", (iid,)).fetchall()]
            q = ",".join("?" for _ in OPEN_ORDER_STATUSES)
            oids = [x[0] for x in conn.execute(f"SELECT order_id FROM orders WHERE instrument_id=? AND status IN ({q}) ORDER BY created_ts DESC", (iid, *OPEN_ORDER_STATUSES)).fetchall()]
            working = [order_full(rt, conn, o) for o in oids]
        for p in positions:
            p["mark_c"] = None
            try:
                pv = rt.accounting.position(p["account_id"], iid, snapshot=snap)
                if pv is not None:
                    p.update(avg_px=pv.avg_px, mv_c=pv.mv_c, unrealised_c=pv.unrealised_c)
            except ScopeError:
                pass
        d = inst.to_dict()
        d["optionable"] = bool(inst.cboe_symbol) and inst.kind in EQUITY_KINDS
        d["exchange"] = (inst.spec or {}).get("exchange")
        d["cik"] = (inst.spec or {}).get("cik")
        return {"instrument": d, "quote": recs.get(iid), "mark": mark_dict(snap.mark(iid)), "session": rt.sessions.state(inst.session_group, req.now),
                "support": inst.support, "optionable": d["optionable"], "spec": inst.spec or None, "positions": positions, "working_orders": working,
                "chain_cached": bool(getattr(rt, "chains", None) is not None and rt.chains.peek(iid) is not None)}

    def bars(req: Request):
        rt = req.rt
        inst = _inst_or_404(rt, req.params[0])
        iid = inst.instrument_id
        cache = getattr(rt, "cache", None)
        rec = cache.get(iid) if cache is not None else None
        if rec is None or (not rec.bars_tail and rec.last is None):
            if cache is not None and getattr(rt, "worker", None) is not None:
                cache.subscribe([iid], 90, "PAGE")
                return 202, {"status": "fetching", "retry_ms": RETRY_MS, "instrument_id": iid}
            raise NotFound(f"no bars for {iid} (no quote in the cache)")
        cache.subscribe([iid], 90, "PAGE")
        d = rec.to_dict()
        return {"instrument_id": iid, "range": req.q("range") or "1d", "bars": d.get("bars_tail") or [], "bar": d.get("bar"), "prev_close": d.get("prev_close"),
                "session": rt.sessions.state(inst.session_group, req.now), "quote_time": rec.quote_time, "fetched_at": rec.fetched_at, "source": rec.source,
                "quality": rec.quality, "delay_label": rec.delay_label}

    def chain(req: Request):
        rt = req.rt
        inst = _inst_or_404(rt, req.params[0])
        options = need(rt, "options")
        out = options.chain_view(rt, inst.instrument_id, req.q("expiry"), now=req.now)
        if isinstance(out, dict) and out.get("status") == "fetching":
            return 202, dict(out, retry_ms=out.get("retry_ms") or RETRY_MS)
        return out

    def quotes(req: Request):
        rt = req.rt
        ids = [i.strip().upper() for i in (req.q("ids") or "").split(",") if i.strip()]
        if len(ids) > MAX_QUOTE_IDS:
            raise ApiError(400, "TOO_MANY", f"at most {MAX_QUOTE_IDS} ids per request, got {len(ids)}", {"max": MAX_QUOTE_IDS, "n": len(ids)})
        known = [i for i in ids if rt.instruments.get(i) is not None]
        recs = _quotes_for(rt, known, req.now, "WATCH")
        groups = {rt.instruments.get(i).session_group for i in known}
        return {"quotes": recs, "unknown": [i for i in ids if i not in known], "sessions": {g: rt.sessions.state(g, req.now) for g in sorted(groups)}, "server_time": req.now}

    def curve(req: Request):
        return curve_view(req.rt, req.now)

    def rates(req: Request):
        store = need(req.rt, "rates")
        out = store.view()
        out["source"] = "FRED (observation dates shown); monthly proxies labelled"
        return out

    def order_preview(req: Request):
        orders = need(req.rt, "orders")
        body = dict(req.body or {})
        _materialise_option(req.rt, body.get("instrument_id"))
        body.pop("request_id", None)
        return orders.preview(req.rt, body)

    def order_submit(req: Request):
        rt = req.rt
        orders = need(rt, "orders")
        payload = dict(req.body or {})
        payload.pop("request_id", None)
        _materialise_option(rt, payload.get("instrument_id"))
        res = run_command(req, "order.submit", payload)
        if res.ok:
            body = res.body if isinstance(res.body, dict) else {}
            order = body.get("order")
            if isinstance(order, dict):
                inst = rt.instruments.get(order.get("instrument_id") or "")
                order["symbol"] = inst.symbol if inst else order.get("instrument_id")
            if not res.replayed:
                m = getattr(rt, "matcher", None)
                if m is not None and hasattr(m, "wake"):
                    m.wake()
            return res
        if 400 <= res.http < 500 and not res.replayed and res.code not in NOT_REJECTIONS and hasattr(orders, "record_rejection"):
            try:
                orders.record_rejection(rt, req.request_id or "", payload, res.code or "REJECTED", res.message or "")
            except Exception:   # noqa: BLE001 — the audit row is best effort; the refusal itself is the answer
                if rt.log:
                    rt.log.exception("record_rejection failed")
        return res

    def order_get(req: Request):
        with req.rt.db.reader() as conn:
            return order_full(req.rt, conn, req.params[0], with_events=True)

    def order_cancel(req: Request):
        rt, oid = req.rt, req.params[0]
        with rt.db.reader() as conn:
            if conn.execute("SELECT 1 FROM orders WHERE order_id=?", (oid,)).fetchone() is None:
                raise NotFound(f"order {oid} does not exist")
        res = run_command(req, "order.cancel", {"order_id": oid, "reason": req.b("reason") or "cancelled by user"})
        body = ok_or_raise(res)
        with rt.db.reader() as conn:
            return 200, {"order": order_full(rt, conn, oid, with_events=True), "result": body}, _replay(res)

    def order_resume(req: Request):
        rt, oid = req.rt, req.params[0]
        with rt.db.reader() as conn:
            if conn.execute("SELECT 1 FROM orders WHERE order_id=?", (oid,)).fetchone() is None:
                raise NotFound(f"order {oid} does not exist")
        res = run_command(req, "order.resume", {"order_id": oid})
        body = ok_or_raise(res)
        with rt.db.reader() as conn:
            return 200, {"order": order_full(rt, conn, oid, with_events=True), "result": body}, _replay(res)

    def credit_locate(req: Request):
        rt = req.rt
        lending = need(rt, "lending")
        aid = req.q("account_id") or ""
        _account_or_404(rt, aid)
        inst = _inst_or_404(rt, req.q("instrument_id") or "")
        qty = req.q_int("qty", 0, 0, 10**9)
        if qty <= 0:
            raise ApiError(400, "QTY_INVALID", "qty must be a positive whole number")
        return lending.locate(rt, aid, inst, qty)

    def credit_borrow(req: Request):
        res = run_command(req, "credit.borrow_stock", req.payload("account_id", "instrument_id", "qty"))
        return res

    def credit_borrow_return(req: Request):
        payload = {"borrow_id": req.params[0]}
        if req.b("qty") is not None:
            payload["qty"] = req.b("qty")
        return run_command(req, "credit.return_stock", payload)

    def credit_loan(req: Request):
        payload = {"account_id": req.b("account_id"), "currency": str(req.b("currency") or "").upper(), "amount_c": req.amount_c(), "term_days": req.b("term_days"), "memo": req.b("memo")}
        payload = {k: v for k, v in payload.items() if v is not None}
        res = run_command(req, "credit.loan_draw", payload)
        body = ok_or_raise(res)
        loan = body.get("loan") or {}
        idx = loan.get("index") or {}
        return 200, {"loan": loan, "rate": {"index": loan.get("index_name"), "as_of": idx.get("obs_date"), "value": idx.get("value"), "spread": loan.get("spread"),
                                            "all_in": loan.get("rate"), "monthly_proxy": loan.get("monthly_proxy")}, "result": body}, _replay(res)

    def credit_loan_repay(req: Request):
        return run_command(req, "credit.loan_repay", {"loan_id": req.params[0], "amount_c": req.amount_c()})

    def credit_pb(req: Request):
        rt, aid, action = req.rt, req.params[0], req.params[1]
        _account_or_404(rt, aid)
        res = run_command(req, "credit.pb_draw" if action == "draw" else "credit.pb_repay", {"account_id": aid, "amount_c": req.amount_c()})
        body = ok_or_raise(res)
        snap = rt.marks.snapshot()
        return 200, {"account": account_row(rt, aid, snap), "cash": rt.accounting.cash(aid, snapshot=snap), "result": body}, _replay(res)

    def fx_preview(req: Request):
        rt = req.rt
        fx = need(rt, "fx")
        aid = str(req.b("account_id") or "")
        _account_or_404(rt, aid)
        pv = fx.spot_preview(rt, aid, str(req.b("buy_ccy") or "").upper(), str(req.b("sell_ccy") or "").upper(), req.amount_c(), str(req.b("amount_ccy") or req.b("buy_ccy") or "").upper())
        if not pv.get("ok", True) and pv.get("blockers"):
            b0 = pv["blockers"][0]
            raise ApiError(409, b0.get("code") or "NO_QUOTE", b0.get("message") or "no executable FX quote", {"preview": pv})
        return pv

    def fx_spot(req: Request):
        payload = {"account_id": req.b("account_id"), "buy_ccy": str(req.b("buy_ccy") or "").upper(), "sell_ccy": str(req.b("sell_ccy") or "").upper(),
                   "amount_c": req.amount_c(), "amount_ccy": str(req.b("amount_ccy") or req.b("buy_ccy") or "").upper()}
        return run_command(req, "fx.spot", payload)

    def fx_forward(req: Request):
        if "fx.forward" not in req.rt.bus.kinds():
            raise ApiError(400, "NOT_IN_THIS_BUILD", "FX forwards are not in this build")
        return run_command(req, "fx.forward", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    def options_exercise(req: Request):
        return run_command(req, "options.exercise", req.payload("account_id", "instrument_id", "qty"))

    def hedges(req: Request):
        rt = req.rt
        hmod = need(rt, "hedges")
        aid = req.q("account_id") or ""
        _account_or_404(rt, aid)
        inst = _inst_or_404(rt, req.q("instrument_id") or "")
        qty = req.q_int("qty", 0, 0, 10**9)
        if qty <= 0:
            raise ApiError(400, "QTY_INVALID", "qty must be a positive whole number")
        out = hmod.candidates(rt, aid, inst, (req.q("side") or "BUY").upper(), qty, now=req.now)
        if isinstance(out, dict):
            if out.get("status") == "fetching":
                return 202, dict(out, retry_ms=out.get("retry_ms") or RETRY_MS)
            return out
        return [c.to_dict() if hasattr(c, "to_dict") else c for c in out]

    def strategies(req: Request):
        mod = need(req.rt, "strategies")
        return mod.catalogue_view(req.rt, req.q("account_id"), req.q("instrument_id"))

    def strategies_preview(req: Request):
        mod = need(req.rt, "strategies")
        status, body = mod.preview_view(req.rt, dict(req.body or {}))
        return int(status), body

    def news(req: Request):
        rt = req.rt
        store = need(rt, "news")
        source = req.q("source")
        try:
            items = store.items(req.q_int("limit", 60, 1, 200), source)
        except ValueError as e:
            raise ApiError(400, "BAD_REQUEST", str(e))
        try:
            scope = req.scope("ALL")
        except ScopeError:
            scope = "ALL_BOOKS"
        from . import news as _news
        attention = _news.attention_summary(rt, scope) if hasattr(_news, "attention_summary") else None
        return {"items": [i.to_dict() if hasattr(i, "to_dict") else i for i in items], "attention": attention, "fetched": store.fetched(), "editions": store.editions(),
                "health": store.health(), "scope": scope, "sentence": "News is shown as fetched; nothing is interpreted, scored or summarised (NEWS)"}

    def risk(req: Request):
        mod = need(req.rt, "risk")
        return mod.report(req.rt, req.scope("ALL"))

    def compliance(req: Request):
        mod = need(req.rt, "compliance")
        return list(mod.holdings(req.rt, req.scope("ALL")))

    def performance(req: Request):
        mod = need(req.rt, "performance")
        scope = req.q("scope") or None
        if scope in (None, "ALL"):
            try:
                scope = req.lifetime()
            except ScopeError:
                scope = None
        try:
            view = mod.perf_view(req.rt, scope)
        except ScopeError as e:
            msg = str(e.args[0]) if e.args else "bad scope"
            if "per lifetime" in msg:
                raise ApiError(400, "SCOPE", msg)
            raise
        return view

    def schedule_get(req: Request):
        rt, aid = req.rt, req.params[0]
        acct = _account_or_404(rt, aid)
        if acct["kind"] != "PB":
            raise NotFound(f"{aid} is not a prime-broker account")
        with rt.db.reader() as conn:
            row = conn.execute("SELECT * FROM pb_schedules WHERE account_id=?", (aid,)).fetchone()
        from . import tables
        if row is None:
            need(rt, "prime")
            raise NotFound(f"{aid} has no schedule row yet")
        try:
            sched = json.loads(row["schedule_json"])
        except ValueError:
            sched = None
        return {"account_id": aid, "broker": acct["broker"], "schedule": sched, "updated_ts": row["updated_ts"], "fields_note": getattr(tables, "_PB_FIELDS_NOTE", "")}

    def schedule_patch(req: Request):
        rt, aid = req.rt, req.params[0]
        acct = _account_or_404(rt, aid)
        if acct["kind"] != "PB":
            raise NotFound(f"{aid} is not a prime-broker account")
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        payload["account_id"] = aid
        res = run_command(req, "account.update_schedule", payload)
        ok_or_raise(res)
        return 200, schedule_get(req), _replay(res)

    # ---- OTC swaps (WP-17 / WP-26), private markets (WP-19), private credit (WP-21): alive when their modules are bound -------
    def otc_preview(req: Request):
        swaps = need(req.rt, "swaps")
        kind = str(req.b("kind") or "").upper()
        if kind not in ("IRS", "TRS", "XCCY"):
            raise ApiError(400, "NOT_IN_THIS_BUILD" if kind else "BAD_REQUEST", f"OTC kind {kind!r}: only IRS, TRS and XCCY swaps are in this build")
        aid = str(req.b("account_id") or "")
        _account_or_404(req.rt, aid)
        return swaps.preview(req.rt, aid, kind, dict(req.b("terms") or {}))

    def otc_book(req: Request):
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        return run_command(req, "otc.book", payload)

    def otc_contracts(req: Request):
        rt = req.rt
        swaps = need(rt, "swaps")
        if hasattr(swaps, "contracts_view"):
            return swaps.contracts_view(rt, account_id=req.q("account_id"), scope=req.q("scope"), detail=req.q("detail") in ("1", "true"))
        with rt.db.reader() as conn:
            if req.q("account_id"):
                rows = conn.execute("SELECT * FROM otc_contracts WHERE account_id=? ORDER BY contract_id", (req.q("account_id"),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM otc_contracts ORDER BY contract_id").fetchall()
        return [dict(x) for x in rows]

    def otc_terminate(req: Request):
        return run_command(req, "otc.terminate", {"contract_id": req.params[0]})

    def private_companies(req: Request):
        rt = req.rt
        private = need(rt, "private")
        if hasattr(private, "companies_view"):
            return private.companies_view(rt, req.q("q"), req.q("account_id"))
        with rt.db.reader() as conn:
            return [dict(x) for x in conn.execute("SELECT * FROM private_companies ORDER BY name").fetchall()]

    def private_company_post(req: Request):
        return run_command(req, "private.add_company", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    def private_round_post(req: Request):
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        payload["company_id"] = req.params[0]
        return run_command(req, "private.record_round", payload)

    def private_preview(req: Request):
        private = need(req.rt, "private")
        return private.preview(req.rt, dict(req.body or {}))

    def private_trade(req: Request):
        return run_command(req, "private.trade", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    def private_credit_post(req: Request):
        return run_command(req, "pc.commit", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    def private_credit_action(req: Request):
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        payload["loan_id"] = req.params[0]
        return run_command(req, "pc.amend_fee" if req.params[1] == "fee" else "pc.repay", payload)

    def private_credit_get(req: Request):
        rt = req.rt
        pc = need(rt, "private_credit")
        if hasattr(pc, "loans_view"):
            return pc.loans_view(rt, req.scope("ALL"))
        with rt.db.reader() as conn:
            return [dict(x) for x in conn.execute("SELECT * FROM private_loans ORDER BY loan_id").fetchall()]

    r.add("GET", r"/api/instruments", instruments)
    r.add("GET", r"/api/instruments/resolve", resolve)
    r.add("GET", r"/api/instruments/([^/]+)", instrument)
    r.add("GET", r"/api/instruments/([^/]+)/bars", bars)
    r.add("GET", r"/api/instruments/([^/]+)/chain", chain)
    r.add("GET", r"/api/quotes", quotes)
    r.add("GET", r"/api/curve", curve)
    r.add("GET", r"/api/rates", rates)
    r.add("POST", r"/api/orders/preview", order_preview, mutating=False)
    r.add("POST", r"/api/orders", order_submit)
    r.add("GET", r"/api/orders/([^/]+)", order_get)
    r.add("DELETE", r"/api/orders/([^/]+)", order_cancel)
    r.add("POST", r"/api/orders/([^/]+)/resume", order_resume)
    r.add("GET", r"/api/credit/locate", credit_locate)
    r.add("POST", r"/api/credit/borrows", credit_borrow)
    r.add("POST", r"/api/credit/borrows/([^/]+)/return", credit_borrow_return)
    r.add("POST", r"/api/credit/loans", credit_loan)
    r.add("POST", r"/api/credit/loans/([^/]+)/repay", credit_loan_repay)
    r.add("POST", r"/api/credit/pb/([^/]+)/(draw|repay)", credit_pb)
    r.add("POST", r"/api/credit/private", private_credit_post)
    r.add("POST", r"/api/credit/private/([^/]+)/(fee|repay)", private_credit_action)
    r.add("GET", r"/api/credit/private", private_credit_get)
    r.add("POST", r"/api/fx/preview", fx_preview, mutating=False)
    r.add("POST", r"/api/fx/spot", fx_spot)
    r.add("POST", r"/api/fx/forward", fx_forward)
    r.add("POST", r"/api/options/exercise", options_exercise)
    r.add("GET", r"/api/hedges", hedges)
    r.add("GET", r"/api/strategies", strategies)
    r.add("POST", r"/api/strategies/preview", strategies_preview, mutating=False)
    r.add("GET", r"/api/news", news)
    r.add("GET", r"/api/risk", risk)
    r.add("GET", r"/api/compliance", compliance)
    r.add("GET", r"/api/portfolio/performance", performance)
    r.add("GET", r"/api/accounts/([^/]+)/schedule", schedule_get)
    r.add("PATCH", r"/api/accounts/([^/]+)/schedule", schedule_patch)
    r.add("POST", r"/api/otc/preview", otc_preview, mutating=False)
    r.add("POST", r"/api/otc/contracts", otc_book)
    r.add("GET", r"/api/otc/contracts", otc_contracts)
    r.add("POST", r"/api/otc/contracts/([^/]+)/terminate", otc_terminate)
    r.add("GET", r"/api/private/companies", private_companies)
    r.add("POST", r"/api/private/companies", private_company_post)
    r.add("POST", r"/api/private/companies/([^/]+)/rounds", private_round_post)
    r.add("POST", r"/api/private/preview", private_preview, mutating=False)
    r.add("POST", r"/api/private/trades", private_trade)


__all__ = ["register_routes", "markets_view", "curve_view"]
