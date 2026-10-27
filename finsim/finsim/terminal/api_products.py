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
from decimal import Decimal
from typing import Dict, List, Optional

from datetime import date, timedelta

from . import books as _books, config
from .accounting import ScopeError
from .api import (ApiError, MAX_QUOTE_IDS, NotBuilt, NotFound, PACKAGES, RETRY_MS, Request, mark_dict, need, ok_or_raise, quote_dict, run_command,
                  OPEN_ORDER_STATUSES)
from .api_views import _account_or_404, account_row, order_full
from .commands import ConflictError, Result
from .instruments import EQUITY_KINDS, parse_futures_id
from .quotes.cache import PRIORITY_RANK

NOT_REJECTIONS = ("CONFIRM_REQUIRED", "PREVIEW_STALE", "REQUEST_ID_REUSED", "BAD_REQUEST_ID")
#: C5 (BUILD2_PLAN): instrument kinds that have no Yahoo quote: never subscribed (quote null, no 'up to 15 min delayed' record);
#: their page shows the product module's ``instrument_extra`` instead
NO_YAHOO_KINDS = ("BOND", "PRIVATE", "SWAP", "IRS", "TRS", "XCCY", "EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD")
#: USX-26: the futures tab's group headers in trader words
FUTURES_GROUP_LABELS = {"CME_EQUITY_INDEX": "CME equity index", "CME_RATES": "CME rates", "CME_FX": "CME FX", "NYMEX_ENERGY": "NYMEX energy",
                        "COMEX_METALS": "COMEX metals", "CBOT_GRAINS": "CBOT grains"}
EXPIRY_WEEK_D = 7               # USX-26: a cash-settled / energy month inside its last 7 calendar days before expiry is not the active month
#: USX-26: groups whose contracts deliver from the first notice day (the last business day of the month BEFORE the contract month): the
#: market rolls out of a month a few days before that, so such a month stops being the active one ``NOTICE_ROLL_D`` days before its
#: contract month begins (GCV26 is not the active gold contract in October: GCZ26 is)
NOTICE_GROUPS = ("CME_RATES", "COMEX_METALS", "CBOT_GRAINS")
NOTICE_ROLL_D = 3
STALE_PRINT_SESSIONS = 10       # USX-27: a FULL row with no print for more than this many sessions is flagged 'possibly delisted'
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
BARS_RETRY_S = 60               # C8: after a failed full-session fetch, the chart asks the worker again at most once a minute
#: C5: which runtime module answers ``instrument_extra(rt, inst, now)`` for a kind (the first bound one that defines it)
EXTRA_MODULES = {"BOND": ("bonds", "treasuries"), "PRIVATE": ("private",), "FX": ("fx",), "SWAP": ("swaps", "otc_options"),
                 "IRS": ("swaps", "otc_options"), "TRS": ("swaps", "otc_options"), "XCCY": ("swaps", "otc_options"),
                 "EQ_OPTION": ("otc_options", "swaps"), "FX_OPTION": ("otc_options", "swaps"), "EQ_FORWARD": ("otc_options", "swaps"),
                 "FX_FORWARD": ("otc_options", "swaps")}
MARKET_ROWS = 60                # the cap of the DEBT bonds list; the US Exchange / PB equity and option-underlying lists have none
BROWSE = "BROWSE"               # the cache's slow list class (quotes/cache.py): at most config.BROWSE_PER_MIN refreshes a minute
BROWSE_TTL_S = 1800             # a listed row's BROWSE subscription lives 30 min after the page's last load (the page reloads every 30 s)
#: what a list row's quote carries (the grid's price, chg %, bid / ask, volume and freshness chip): no bars_tail / bar / inputs / sizes
COMPACT_QUOTE_FIELDS = ("last", "bid", "ask", "mid", "prev_close", "day_volume", "quote_time", "fetched_at", "delay_s", "delay_label", "quality",
                        "source", "provider", "method", "error")


def _inst_or_404(rt, iid: str):
    inst = rt.instruments.get(str(iid or "").strip().upper())
    if inst is None:
        raise NotFound(f"instrument {iid} is not in the master")
    return inst


def _inst_row(rt, inst, rec=None, now: int = 0, position: Optional[int] = None) -> dict:
    return {"instrument_id": inst.instrument_id, "symbol": inst.symbol, "name": inst.name, "kind": inst.kind, "currency": inst.currency, "support": inst.support,
            "status": inst.status, "session_group": inst.session_group, "quote": quote_dict(rec, now), "position": position,
            "optionable": bool(inst.cboe_symbol) and inst.kind in EQUITY_KINDS,
            "price_scale": str(inst.price_scale), "quote_unit": (inst.spec or {}).get("quote_unit") if isinstance(inst.spec, dict) else None}


def _replay(res: Result) -> dict:
    return {"X-Replayed": "1"} if res.replayed else {}


def book_guard(req: Request, account_id) -> None:
    """C7 (SHELL-02): a request whose ``?book=`` names a book that does not own ``account_id`` is refused 409 BOOK_MISMATCH before
    anything is previewed or booked (the ticket would otherwise book into the other book while the switcher says otherwise)."""
    sel = req.q("book")
    aid = str(account_id or "")
    if not sel or not aid:
        return
    try:
        lid, _role = _books.split_account_id(aid)
    except ValueError:
        return
    owner = _books.book_code_of(lid)
    if owner == str(sel).upper():
        return
    with req.rt.db.reader() as conn:
        names = {b.code: b.name for b in _books.books(conn)}
    raise ApiError(409, "BOOK_MISMATCH", f"account {aid} belongs to the {names.get(owner, owner)}, not the selected {names.get(str(sel).upper(), sel)}: "
                   f"switch books or pick an account of this book", {"account_id": aid, "book": owner, "selected": str(sel).upper()})


def _month_label(inst) -> str:
    """The contract month in words ("Dec 2026") from the futures id (root, year, month), else from the expiry date."""
    parsed = parse_futures_id(inst.instrument_id)
    if parsed:
        try:
            return f"{MONTH_NAMES[int(parsed[2]) - 1]} {int(parsed[1])}"
        except (ValueError, IndexError, TypeError):
            pass
    try:
        d = date.fromisoformat(str(inst.expiry))
    except (TypeError, ValueError):
        return inst.symbol
    return f"{MONTH_NAMES[d.month - 1]} {d.year}"


def roll_date(inst) -> Optional[date]:
    """USX-26: the day a futures month stops being the active one: for the physically delivered groups (``NOTICE_GROUPS``)
    ``NOTICE_ROLL_D`` days before its contract month begins (ahead of the first notice day), for the others ``EXPIRY_WEEK_D`` days
    before its expiry (the roll week of the cash-settled index, FX and energy contracts)."""
    parsed = parse_futures_id(inst.instrument_id)
    if inst.session_group in NOTICE_GROUPS and parsed:
        try:
            return date(int(parsed[1]), int(parsed[2]), 1) - timedelta(days=NOTICE_ROLL_D)
        except (TypeError, ValueError):
            pass
    try:
        return date.fromisoformat(str(inst.expiry)) - timedelta(days=EXPIRY_WEEK_D)
    except (TypeError, ValueError):
        return None


def active_months(rt, today: date) -> Dict[str, dict]:
    """USX-26: per futures root, the ACTIVE listed months (by expiry) and the active one: the month with the highest observed volume
    (the cached quotes' day volume; open interest is not in the free feed) when it beats the baseline, else the baseline = the first
    month before its roll date (``roll_date``: GCZ26, not GCV26 in October), else the front."""
    by_root: Dict[str, list] = {}
    for inst in rt.instruments.all():
        if inst.kind != "FUTURE" or inst.status != "ACTIVE":
            continue
        parsed = parse_futures_id(inst.instrument_id)
        root = parsed[0] if parsed else inst.symbol
        by_root.setdefault(root, []).append(inst)
    cache = getattr(rt, "cache", None)
    out: Dict[str, dict] = {}
    for root, insts in by_root.items():
        insts.sort(key=lambda x: (x.expiry or "9999-12-31", x.instrument_id))
        recs = cache.get_many([i.instrument_id for i in insts]) if cache is not None else {}
        base = None
        for i in insts:
            rd = roll_date(i)
            if rd is None or today < rd:
                base = i
                break
        base = base or insts[-1]

        def vol(i):
            r = recs.get(i.instrument_id)
            v = getattr(r, "day_volume", None) if r is not None else None
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0
        best = max(insts, key=lambda i: (vol(i), -insts.index(i)))
        pick, why = (best, "highest observed volume") if vol(best) > vol(base) and best is not base else \
                    (base, "the next month: the front one is in its delivery / expiry roll" if base is not insts[0] else "front month")
        out[root] = {"active": pick, "why": why, "months": insts}
    return out


def print_age(rt, quote: Optional[dict], now: int) -> tuple:
    """USX-27: ``(last_print_age_d, sessions since the print (counted up to STALE_PRINT_SESSIONS + 1), print date)`` of a list row's
    compact quote (None when it has no print)."""
    qt = (quote or {}).get("quote_time")
    if not qt:
        return None, None, None
    from .clock import ny_of
    d0 = ny_of(int(qt)).date()
    d1 = ny_of(int(now)).date()
    age_d = max(0, (d1 - d0).days)
    if age_d <= STALE_PRINT_SESSIONS:
        return age_d, None, d0
    n, d = 0, d0 + timedelta(days=1)
    while d <= d1 and n <= STALE_PRINT_SESSIONS:
        if rt.cal.is_business_day(d):
            n += 1
        d += timedelta(days=1)
    return age_d, n, d0


def instrument_extra(rt, inst, now: int) -> Optional[dict]:
    """C5: the product module's ``instrument_extra(rt, inst, now) -> {headline {value, quality, as_of, source, note}, spec_view
    [{label, value}], actions}`` for this kind (``EXTRA_MODULES``), else None.  A failure is logged and answered as
    ``{"error": ...}`` on the page (never hidden, never a 500 for the whole instrument page)."""
    for attr in EXTRA_MODULES.get(getattr(inst, "kind", ""), ()):
        mod = getattr(rt, attr, None)
        fn = getattr(mod, "instrument_extra", None) if mod is not None else None
        if not callable(fn):
            continue
        try:
            out = fn(rt, inst, now)
        except Exception as e:   # noqa: BLE001 - surfaced on the page and in the log
            if getattr(rt, "log", None):
                rt.log.exception("instrument_extra %s for %s failed", attr, inst.instrument_id)
            return {"error": f"{type(e).__name__}: {e}", "source": attr}
        return out if isinstance(out, dict) else None
    return None


def compact_quote(rec, now: int) -> Optional[dict]:
    """A list row's quote: ``COMPACT_QUOTE_FIELDS`` + ``age_s`` (what ``quote_dict`` gives, without the bars, sizes and inputs)."""
    if rec is None:
        return None
    d = {}
    for k in COMPACT_QUOTE_FIELDS:
        v = getattr(rec, k, None)
        d[k] = str(v) if isinstance(v, Decimal) else v
    qt = d.get("quote_time")
    d["age_s"] = None if qt is None else max(0, int(now) - int(qt))
    return d


def _quotes_for(rt, ids: List[str], now: int, priority: Optional[str] = "PAGE", ttl_s: int = 90, compact: bool = False) -> Dict[str, dict]:
    """The cached quotes of ``ids``, subscribing them first with ``priority`` for ``ttl_s`` seconds (None: read only, no
    subscription); ``compact`` gives ``compact_quote`` rows instead of the full ``quote_dict``."""
    cache = getattr(rt, "cache", None)
    if cache is None or not ids:
        return {}
    if priority:
        # C5: bonds, private names, swaps and OTC contracts have no Yahoo quote: never subscribed (their own modules price them)
        master = getattr(rt, "instruments", None)
        sub = [i for i in ids if master is None or getattr(master.get(i), "kind", None) not in NO_YAHOO_KINDS]
        if sub:
            cache.subscribe(sub, ttl_s, priority)
    shape = compact_quote if compact else quote_dict
    return {k: shape(v, now) for k, v in cache.get_many(ids).items()}


def browse_available() -> bool:
    """True when the quote cache knows the BROWSE class (its interval and per-minute budget are in config)."""
    return BROWSE in PRIORITY_RANK and BROWSE in config.QUOTE_INTERVALS


def _list_quotes(rt, ids: List[str], fast: set, now: int):
    """The quotes of a long list page (owner 2026-10-21: "why is there only 60 rows of equities on market place???? it needs all";
    "it doesnt need to ask every minute ... it can pull 50 prices per minute and stagger it within 15/30 min"): the rows in ``fast``
    (a position or a working order in this account) keep the PAGE subscription (the worker's HELD / ORDER pins refresh them every
    15-30 s anyway), every other row is subscribed BROWSE for BROWSE_TTL_S, and every row gets the compact quote.  Without the
    BROWSE class (an older cache) only the first MARKET_ROWS other rows are subscribed, as before, and the rest read the cache.
    Returns ``(quotes by id, the ids subscribed)``."""
    quick = [i for i in ids if i in fast]
    slow = [i for i in ids if i not in fast]
    recs = _quotes_for(rt, quick, now, compact=True)
    if browse_available():
        recs.update(_quotes_for(rt, slow, now, BROWSE, BROWSE_TTL_S, compact=True))
        subscribed = set(ids)
    else:
        recs.update(_quotes_for(rt, slow[:MARKET_ROWS], now, compact=True))
        recs.update(_quotes_for(rt, slow[MARKET_ROWS:], now, None, compact=True))
        subscribed = set(quick) | set(slow[:MARKET_ROWS])
    return recs, (subscribed if getattr(rt, "cache", None) is not None else set())


def _list_note(rt, n_rows: int, what: str, waiting: int) -> dict:
    """The page's refresh rule for a BROWSE list, built from the config and the backlog the cache reports (nothing promised that
    the cache does not do).  ``waiting`` = listed rows subscribed but never fetched yet (``queued`` on the row)."""
    iv = config.QUOTE_INTERVALS
    fast = f"Held / working names refresh every {iv.get('ORDER', 15)}-{iv.get('HELD', 30)} s while their session is open."
    grace = max(1, int(round(int(getattr(config, "FETCH_GRACE_S", 300)) / 60)))
    if browse_available():
        per_min = int(getattr(config, "BROWSE_PER_MIN", 0) or 0) or None
        stats: dict = {}
        cache = getattr(rt, "cache", None)
        if cache is not None and hasattr(cache, "browse_stats"):
            try:
                stats = cache.browse_stats() or {}
            except Exception:   # noqa: BLE001 - the note is informative; a failing counter must not fail the page
                stats = {}
        backlog, pinned = stats.get("due_backlog"), stats.get("browse_only_pinned")
        mins = max(1, int(round(int(iv[BROWSE]) / 60)))
        if per_min and pinned:
            mins = max(mins, -(-int(pinned) // per_min))       # more BROWSE names than one interval's budget: the cycle is longer
        eta = -(-int(backlog) // per_min) if (per_min and backlog) else None
        parts = [f"{n_rows} {what}. Prices refresh in the background" + (f", {per_min} a minute (oldest first)" if per_min else "")
                 + f", each row about every {mins} min, so in market hours a row shows STALE once its last fetch is over {grace} min old;"
                 " the ticket fetches its own fresh quote before any order."]
        if waiting:
            parts.append(f"{waiting} still waiting for a first price" + (f" (about {eta} min at that rate)." if eta else "."))
        parts += [fast, "Each price shows its own age."]
        return {"rows": n_rows, "class": BROWSE, "interval_s": int(iv[BROWSE]), "per_min": per_min, "ttl_s": BROWSE_TTL_S, "waiting": waiting,
                "backlog": backlog, "eta_min": eta, "cycle_min": mins, "note": " ".join(parts)}
    return {"rows": n_rows, "class": "PAGE", "interval_s": None, "per_min": None, "ttl_s": 90, "waiting": waiting, "backlog": None, "eta_min": None,
            "cycle_min": None,
            "note": (f"{n_rows} {what}. The background refresh of the full list is not in this build: the first {MARKET_ROWS} rows are kept fresh "
                     f"and the others show the last price fetched. {fast} Each price shows its own age.")}


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
    fast: Optional[set] = None      # set by the equities / options tabs: the rows quoted fast; every other row is a BROWSE row
    fut_meta: Dict[str, dict] = {}   # the futures tab: per active month its root, why it is the active one and the listed months (USX-26)
    work_rows: Optional[set] = None  # the options tab: underlyings with an open order on themselves OR on one of their options
    what = ""

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
            # EVERY active equity / ETF / ADR / REIT / preferred this account trades (no cap): held / working / watched first, then
            # the rest by symbol; their quotes come from the BROWSE class (_list_quotes)
            ids = pick([i for i in list(held) + working + watched if getattr(master.get(i), "kind", None) in EQUITY_KINDS])
            listed = set(ids)
            ids += [i for i in fill(EQUITY_KINDS, None, lambda x: x.support == "FULL" and master.tradeable_in(x, kind)) if i not in listed]
            fast = set(held) | set(working)
            what = "listed equities and ETFs"
        elif tab == "options":
            # EVERY optionable underlying (a Cboe symbol: the account's equities / ETFs and the index option roots), no cap
            unders, quick, work = [], set(), set()
            for i in list(held) + working + watched:
                inst = master.get(i)
                if inst is None:
                    continue
                u = inst.underlying_id if inst.kind == "OPTION" and inst.underlying_id else (i if inst.cboe_symbol else None)
                if u is None:
                    continue
                unders.append(u)
                if i in held or i in working:
                    quick.add(u)
                if i in working:
                    work.add(u)          # an order on an option shows 'working' on its underlying's row (working holds OCC ids)
            ids = pick(unders)
            listed = set(ids)
            ids += [i for i in fill(EQUITY_KINDS + ("INDEX",), None, lambda x: bool(x.cboe_symbol) and (x.kind == "INDEX" or master.tradeable_in(x, kind)))
                    if i not in listed]
            fast, work_rows = quick, work
            what = "optionable underlyings"
        elif tab == "futures":
            # USX-26: one row per root = its ACTIVE month (observed volume, else the first month outside its expiry week), the other listed
            # months in `months` for a picker; held / working contracts are listed as they are
            act = active_months(rt, rt.clock.today())
            for root, a in act.items():
                inst = a["active"]
                fut_meta[inst.instrument_id] = {"root": root, "why": a["why"],
                                                "months": [{"instrument_id": m.instrument_id, "symbol": m.symbol, "expiry": m.expiry, "label": _month_label(m),
                                                            "active": m is inst} for m in a["months"]]}
            own = [i for i in pick(list(held) + working) if getattr(master.get(i), "kind", None) == "FUTURE"]
            ids = own + [a["active"].instrument_id for a in sorted(act.values(), key=lambda a: (a["active"].session_group, a["active"].symbol))
                         if a["active"].instrument_id not in own]
            extra["groups"] = sorted({master.get(i).session_group for i in ids if master.get(i)})
            extra["group_labels"] = {g: FUTURES_GROUP_LABELS.get(g, g.replace("_", " ")) for g in extra["groups"]}
        elif tab == "otc":
            ids = []
            swaps = getattr(rt, "swaps", None)
            otc_opt = getattr(rt, "otc_options", None)
            kinds = ["IRS", "TRS", "XCCY"] + (["EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD"] if otc_opt is not None else [])
            extra["otc"] = {"state": "available" if swaps is not None else "not available in this version", "kinds": kinds,
                            "not_in_this_build": ["swaptions", "caps / floors", "FRAs"] + ([] if otc_opt is not None else ["OTC equity / FX options", "OTC forwards"])}
        else:
            raise ApiError(400, "BAD_REQUEST", f"unknown tab {tab!r}", {"tabs": [t["key"] for t in tabs]})
    elif kind == "CREDIT":
        tabs = [{"key": "loans", "label": "Currency loans"}, {"key": "borrows", "label": "Stock borrows"}, {"key": "rates", "label": "Rates"}]
        tab = tab or "loans"
        ids = pick(list(held) + working + watched)
        rates = getattr(rt, "rates", None)
        extra = {"loans": loans, "borrows": borrows, "rates": rates.view() if rates is not None and hasattr(rates, "view") else None,
                 "repo": ({"state": "available", "note": "repo / reverse repo and securities lending: Credit Instruments desk"} if getattr(rt, "counterparties", None) is not None
                          else {"state": "NOT_YET", "note": "repo / reverse repo and lending out of inventory are not in this version yet"})}
    elif kind == "FOREIGN_FX":
        tabs = [{"key": "fx", "label": "FX spot"}, {"key": "listings", "label": "Foreign listings"}]
        tab = tab or "fx"
        if tab == "listings":
            # held / working / watched first, then every foreign listing of the master (account kind FOREIGN_FX)
            ids = pick(list(held) + working + watched
                       + fill(("EQUITY", "ETF", "ADR", "REIT", "PREFERRED"), 400, lambda x: "FOREIGN_FX" in x.account_kinds))
            extra["note"] = ("foreign listings: delayed quotes and marks; orders settle in the listing's currency" if callable(getattr(getattr(rt, "fx", None), "venue_fee_c", None))
                             else "foreign listings show delayed quotes and marks; trading them is not in this version yet")
        else:
            ids = fill(("FX",), 40)
    elif kind == "DEBT" and callable(getattr(getattr(rt, "treasuries", None), "debt_markets", None)):
        return rt.treasuries.debt_markets(rt, aid, tab, now)          # Build 2 P07: the same answer as GET /api/debt/markets
    elif kind == "DEBT":
        tabs = [{"key": "curve", "label": "Treasury curve"}, {"key": "bonds", "label": "Bonds (LIMITED)"}]
        tab = tab or "curve"
        ids = fill(("BOND",), MARKET_ROWS) if tab == "bonds" else []
        extra["curve"] = curve_view(rt, now)
        extra["note"] = ("bonds trade at the computed price with its inputs shown" if callable(getattr(getattr(rt, "bonds", None), "dv01_c", None))
                         else "model price, not executable in this version")
    elif kind == "PRIVATE":
        tabs = [{"key": "companies", "label": "Private companies"}]
        tab = tab or "companies"
        ids = fill(("PRIVATE",), 200, lambda x: not (x.spec or {}).get("loan_id"))          # private-credit loans live in CREDIT
        priv = getattr(rt, "private", None)
        if priv is not None and hasattr(priv, "listed_companies"):
            # a company that LISTED or was ACQUIRED is no longer a private name (owner 2026-10-21: "spacex is in private markets for some
            # reason"): it leaves this list unless the account still holds pre-listing shares (closing sales only)
            gone = {c["instrument_id"]: c.get("listing") for c in priv.listed_companies(rt, account_id=aid)}
            ids = [i for i in ids if i not in gone or i in held]
            extra["listed"] = [dict(gone[i] or {}, instrument_id=i) for i in sorted(gone)]
        extra["note"] = "negotiated secondaries; last-round marks, valuation only without a sourced price"
    else:   # TREASURY
        tabs = [{"key": "cash", "label": "Cash"}]
        tab = tab or "cash"
        ids = []
        extra["cash"] = rt.accounting.cash(aid)
    compact = fast is not None
    queued: set = set()
    if compact:
        recs, subscribed = _list_quotes(rt, ids, fast, now)
        queued = {i for i in subscribed if recs.get(i) is None}           # subscribed, never fetched yet: in the refresh queue
        extra["refresh"] = _list_note(rt, len(ids), what, len(queued))
        extra.setdefault("note", extra["refresh"]["note"])
        working_set = set(working) if work_rows is None else work_rows
        marked = [i for i in ids if recs.get(i) is None]      # a list row's mark only says why there is no quote (it IS the quote otherwise)
    else:
        recs = _quotes_for(rt, ids, now)
        marked = ids
    snap = rt.marks.snapshot(marked) if marked else None
    for i in ids:
        inst = master.get(i)
        row = _inst_row(rt, inst, None, now, held.get(i))
        row["quote"] = recs.get(i)
        row["mark"] = mark_dict(snap.mark(i)) if snap is not None and (not compact or row["quote"] is None) else None
        row["group"] = inst.session_group
        if inst.kind == "FUTURE":
            row["group_label"] = FUTURES_GROUP_LABELS.get(inst.session_group, inst.session_group.replace("_", " "))
            meta = fut_meta.get(i)
            if meta is not None:
                row.update(root=meta["root"], active_why=meta["why"], months=meta["months"], month_label=_month_label(inst))
        if compact:
            row["working"] = i in working_set
            row["fast"] = i in fast                    # quoted fast (PAGE): a position / working order here, or on its options
            row["queued"] = i in queued
            age_d, sessions, d0 = print_age(rt, row["quote"], now)
            row["last_print_age_d"] = age_d
            if sessions is not None and sessions > STALE_PRINT_SESSIONS and inst.support == "FULL" and i not in held and i not in working_set:
                # USX-27: no print for more than 10 sessions: shown (searchable) but not offered for b / s; a held / working row keeps its actions
                row["stale_note"] = f"no print since {d0.isoformat()} - possibly delisted"
                row["tradeable"] = False
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


def schedule_reference(rt, broker) -> dict:
    """C9 (PO-19): the dealer's seed schedule (``prime.seed_schedule``) and the field specs (``prime.FIELD_SPECS``: label, unit / kind,
    min, max) so the editor shows the seed next to each field and checks ranges before the confirm dialog."""
    try:
        from . import prime as _prime
    except Exception:   # noqa: BLE001 - no prime module: nothing to add
        return {}
    out: dict = {}
    seed_fn = getattr(_prime, "seed_schedule", None)
    if callable(seed_fn) and broker:
        try:
            out["seed"] = seed_fn(broker)
        except Exception as e:   # noqa: BLE001 - an unknown broker: said so, the schedule itself still answers
            out["seed"] = None
            out["seed_error"] = str(e)[:200]
    specs = getattr(_prime, "FIELD_SPECS", None)
    if isinstance(specs, dict):
        fields = []
        for name, spec in specs.items():
            if isinstance(spec, dict):
                f = dict(spec)
            elif isinstance(spec, (tuple, list)) and len(spec) >= 4 and isinstance(spec[0], str) and spec[0] in ("decimal", "cents", "enum", "int"):
                kind, lo, hi, label = spec[0], spec[1], spec[2], spec[3]          # the 1.0.x shape (kind, min, max, label)
                f = {"kind": kind, "label": label, "min": lo if kind != "enum" else None, "max": hi, "choices": list(lo) if kind == "enum" else None}
            elif isinstance(spec, (tuple, list)) and len(spec) >= 4:
                label, unit, lo, hi = spec[0], spec[1], spec[2], spec[3]          # the C22 shape (label, unit, min, max)
                f = {"label": label, "unit": unit, "min": lo, "max": hi}
            else:
                continue
            f["field"] = name
            for k in ("min", "max"):
                if isinstance(f.get(k), Decimal):
                    f[k] = str(f[k])
            if f.get("kind") == "cents" and str(name).endswith("_c"):
                f["field_usd"] = str(name)[:-2]                              # the API serves cents fields as USD without the suffix
                for k in ("min", "max"):
                    if isinstance(f.get(k), int) and not isinstance(f.get(k), bool):
                        f[k + "_usd"] = str(Decimal(f[k]) / 100)
            fields.append(f)
        out["fields"] = fields
    return out


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
            tp = getattr(getattr(rt, "private", None), "tradeable_private", None)
            if ak == "PRIVATE" and callable(tp):
                # C6: a listed / acquired company (or anything the private module refuses) is not offered in the Private Markets search
                keep = []
                for i in hits:
                    try:
                        ok = tp(rt, i.instrument_id)
                        ok = ok[0] if isinstance(ok, tuple) else bool(ok)
                    except Exception:   # noqa: BLE001 - a failing check keeps the row (the ticket's own validation still runs)
                        ok = True
                    if ok:
                        keep.append(i)
                hits = keep
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
                what = ("foreign listing: quotes and marks only in this version" if inst.session_group == "FOREIGN" else "not offered in this version")
                raise ApiError(400, "UNSUPPORTED", f"{inst.symbol}: {what}", {"symbol": sym, "instrument_id": inst.instrument_id,
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
        if rt.instruments.get(str(req.params[0] or "").strip().upper()) is None:
            try:
                _materialise_option(rt, req.params[0])        # a contract the cached chain lists (never one from thin air)
            except Exception:   # noqa: BLE001 - not listed / no chain: the 404 below says it is not in the master
                pass
        inst = _inst_or_404(rt, req.params[0])
        iid = inst.instrument_id
        no_yahoo = inst.kind in NO_YAHOO_KINDS
        recs = {} if no_yahoo else _quotes_for(rt, [iid], req.now)        # C5: bonds / private / swaps / OTC are never subscribed
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
        out = {"instrument": d, "quote": None if no_yahoo else recs.get(iid), "mark": mark_dict(snap.mark(iid)), "session": rt.sessions.state(inst.session_group, req.now),
               "support": inst.support, "optionable": d["optionable"], "spec": inst.spec or None, "positions": positions, "working_orders": working,
               "chain_cached": bool(getattr(rt, "chains", None) is not None and rt.chains.peek(iid) is not None)}
        extra = instrument_extra(rt, inst, req.now)
        if extra is not None:
            out["extra"] = extra
        return out

    def bars(req: Request):
        rt = req.rt
        inst = _inst_or_404(rt, req.params[0])
        iid = inst.instrument_id
        cache = getattr(rt, "cache", None)
        rec = cache.get(iid) if cache is not None else None
        if inst.kind in NO_YAHOO_KINDS:
            raise NotFound(f"{iid}: no exchange 1-minute bars (its price is computed or negotiated)", {"instrument_id": iid, "kind": inst.kind})
        worker = getattr(rt, "worker", None)
        if worker is not None and callable(getattr(worker, "request_bars", None)) and inst.kind != "OPTION" and inst.yahoo_symbol:
            # C8 (USX-06): every 1-minute bar of the session through the quote worker (one shared fetch, never this thread)
            err = worker.bars_error(iid) if hasattr(worker, "bars_error") else None
            kept = worker.bars(iid) if hasattr(worker, "bars") else None
            last_req = getattr(rt, "_bars_requested", None)
            if last_req is None:
                last_req = rt._bars_requested = {}
            fut = None
            if err is None or req.now - int(last_req.get(iid, 0)) >= BARS_RETRY_S:
                last_req[iid] = req.now
                fut = worker.request_bars(iid)
            if fut is not None and fut.done() and fut.exception() is None:
                kept, err = fut.result(), None
            elif fut is not None and not fut.done() and kept is None:
                if cache is not None:
                    cache.subscribe([iid], 90, "PAGE")
                return 202, {"status": "fetching", "retry_ms": RETRY_MS, "instrument_id": iid}
            elif fut is not None and fut.done() and fut.exception() is not None:
                err = str(fut.exception())
            if cache is not None:
                cache.subscribe([iid], 90, "PAGE")
            if kept is not None:
                d = rec.to_dict() if rec is not None else {}
                return {"instrument_id": iid, "range": req.q("range") or "1d", "bars": kept.get("bars") or [], "count": kept.get("count"),
                        "full_session": bool(kept.get("full_session")), "bar": d.get("bar"), "prev_close": d.get("prev_close"),
                        "session": rt.sessions.state(inst.session_group, req.now), "quote_time": kept.get("quote_time"), "fetched_at": kept.get("fetched_at"),
                        "source": kept.get("source"), "quality": getattr(rec, "quality", None), "delay_label": kept.get("delay_label"),
                        "note": kept.get("note") or "", "error": err}
            if rec is not None and (rec.bars_tail or rec.last is not None):
                d = rec.to_dict()
                return {"instrument_id": iid, "range": req.q("range") or "1d", "bars": d.get("bars_tail") or [], "full_session": False, "bar": d.get("bar"),
                        "prev_close": d.get("prev_close"), "session": rt.sessions.state(inst.session_group, req.now), "quote_time": rec.quote_time,
                        "fetched_at": rec.fetched_at, "source": rec.source, "quality": rec.quality, "delay_label": rec.delay_label,
                        "note": "the full session could not be fetched; the last bars of the quote are shown", "error": err}
            return {"instrument_id": iid, "range": req.q("range") or "1d", "bars": [], "count": 0, "full_session": False, "bar": None, "prev_close": None,
                    "session": rt.sessions.state(inst.session_group, req.now), "quote_time": None, "fetched_at": None, "source": "Yahoo Finance chart",
                    "quality": "unavailable", "delay_label": None, "available": False, "error": err or "the provider answered nothing",
                    "note": f"no 1-minute bars for {inst.symbol}: {err or 'the provider answered nothing'}"}
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
        out["source"] = "per series: FRED, or a labelled free fallback while FRED is down (observation dates shown; monthly proxies labelled)"
        return out

    def order_preview(req: Request):
        orders = need(req.rt, "orders")
        body = dict(req.body or {})
        book_guard(req, body.get("account_id"))
        _materialise_option(req.rt, body.get("instrument_id"))
        body.pop("request_id", None)
        return orders.preview(req.rt, body)

    def order_submit(req: Request):
        rt = req.rt
        orders = need(rt, "orders")
        payload = dict(req.body or {})
        payload.pop("request_id", None)
        book_guard(req, payload.get("account_id"))
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
                "paper": store.paper_meta() if hasattr(store, "paper_meta") else None, "health": store.health(), "scope": scope,
                "sentence": "News is shown as fetched; nothing is interpreted, scored or summarised (NEWS)"}

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
        try:
            from . import prime as _prime
        except Exception:   # noqa: BLE001 - no prime module: the stored row as it is
            _prime = None
        eff = None
        with rt.db.reader() as conn:
            row = conn.execute("SELECT * FROM pb_schedules WHERE account_id=?", (aid,)).fetchone()
            if row is not None and _prime is not None and hasattr(_prime, "read_schedule"):
                try:
                    eff = _prime.read_schedule(conn, aid).to_dict()      # missing terms run on the broker's seed (SR-10)
                except Exception:   # noqa: BLE001 - an unreadable row: the stored JSON below
                    eff = None
        from . import tables
        if row is None:
            need(rt, "prime")
            raise NotFound(f"{aid} has no schedule row yet")
        sched = eff
        if sched is None:
            try:
                sched = json.loads(row["schedule_json"])
            except ValueError:
                sched = None
        note = getattr(_prime, "FIELDS_NOTE", None) if _prime is not None else None
        out = {"account_id": aid, "broker": acct["broker"], "schedule": sched, "updated_ts": row["updated_ts"], "fields_note": note or getattr(tables, "_PB_FIELDS_NOTE", "")}
        out.update(schedule_reference(rt, acct["broker"]))
        return out

    def schedule_patch(req: Request):
        rt, aid = req.rt, req.params[0]
        acct = _account_or_404(rt, aid)
        if acct["kind"] != "PB":
            raise NotFound(f"{aid} is not a prime-broker account")
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        payload["account_id"] = aid
        res = run_command(req, "account.update_schedule", payload)
        body = ok_or_raise(res)
        out = schedule_get(req)
        out.update(before=body.get("before"), after=body.get("after"), note=body.get("note"))         # C9: the change, before / after
        return 200, out, _replay(res)

    # ---- OTC swaps (WP-17 / WP-26), private markets (WP-19), private credit (WP-21): alive when their modules are bound -------
    def otc_preview(req: Request):
        swaps = need(req.rt, "swaps")
        kind = str(req.b("kind") or "").upper()
        allowed = ("IRS", "TRS", "XCCY") + (("EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD") if getattr(req.rt, "otc_options", None) is not None else ())
        if kind not in allowed:
            raise ApiError(400, "NOT_IN_THIS_BUILD" if kind else "BAD_REQUEST", f"OTC kind {kind!r}: the prime brokers here deal {', '.join(allowed)}")
        aid = str(req.b("account_id") or "")
        _account_or_404(req.rt, aid)
        try:
            return swaps.preview(req.rt, aid, kind, dict(req.b("terms") or {}))
        except ConflictError as e:
            # V104R1-06: an input being fetched for this preview (a first quote): 202 + retry_ms, the ticket asks again by itself
            retry = (e.details or {}).get("retry_ms")
            if not retry:
                raise
            return 202, dict(e.details or {}, status="fetching", pending=True, code=e.code, message=e.message, retry_ms=int(retry)), {}

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

    def bonds_trade(req: Request):
        """WP-10 ``bonds.trade`` (Build 2): 400 UNSUPPORTED "model price, not executable in this build" while the build2_bonds setting is off."""
        need(req.rt, "bonds")
        return run_command(req, "bonds.trade", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

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
    r.add("POST", r"/api/bonds/trade", bonds_trade)


__all__ = ["register_routes", "markets_view", "curve_view", "instrument_extra", "NO_YAHOO_KINDS", "EXTRA_MODULES"]
