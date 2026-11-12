"""1.0.7.2 (simplified, SIMPLIFY_PLAN s9): the Smart Ticket's tickets through the normal order route; the covered-call invariant;
sequential packages.  Every expected number is worked by hand in the comment next to it; nothing touches the network or ~/.finsim.

The Smart Ticket sends nothing itself: each card opens the order ticket filled with its leg's ``request``, and the ticket sends it as it
sends any order (its own preview, then ``POST /api/orders`` under the ticket's request id: ``ticket``).  Packages are placed as the order
ticket places them: a shares order with its hedge (``seq_package``: the Hedges tab's pick ``{candidate_id, ratio, edits}``, or the
hedge's own ``legs``, which the order route takes as 1.0.7.1 does - the cases below that need a leg's own limit or an uneven ratio, such
as 1 call for 300 shares, use them) or a Strategies-tab template (``template_package``: ``{strategy_key, units, tenor_months,
strikes}``; tenor 1 reaches 2026-12-18 here and a strike override >= 60 is the strike itself).  The server names a sequential package's
waiting legs ``<rid>:H<i>`` and an all-or-none package's legs ``<rid>:L<i>``.

The world: the Test Book T1 (USD 10,000,000.00 in T1:TREASURY, T1:US_EXCHANGE empty, funded by the automatic sweep at each fill)
built by ``server.build_runtime`` offline (every module bound as ``serve`` binds it, the Router, FakeClock, FakeProvider), Tue
2026-10-20 14:00 ET (US equities and equity options open).  ACME is a fictional listing (added to the master as Yahoo resolution
would add it), its print 100.00 observed 2 minutes ago on a completed 1-minute bar of 50,000 shares (10 % participation: 5,000 a
bar).  Its Cboe chain is invented (every price below), observed one minute ago and fetched now, 20 contracts displayed on every bid
and ask; the underlying shows no bid / ask, so shares fill at the print.  Option fees 0.65 a contract.

The covered-call invariant (SIMPLIFY_PLAN s5: INV-1 to INV-5 and INV-9): a call is sold as covered (``covered: true``) only while shares
held free cover it, and those shares stay reserved for it until it is closed - a sale, a short sale, a loan or an early put exercise
of them is refused, and while calls sold as covered are open on a name a further call sold to open on it needs free shares of its own.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from typing import Optional

from tests.terminal_helpers import _Log, assert_integrity, execute
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.quotes import Bar1m, QuoteRecord
from finsim.terminal.quotes.cache import records_from_chain
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying, FakeProvider
from finsim.terminal.units import qprice

AT = "2026-10-20T14:00:00-04:00"      # Tuesday 14:00 ET: US_EQUITY and US_EQUITY_OPTIONS open
ACCT = "T1:US_EXCHANGE"
TICKET = "a1b2c3d4e5f6"               # the page's ticket id: 12 hex characters (T.uuid without dashes), never derived from the text
E1, E2 = "2026-12-18", "2027-01-15"
# the invented ACME chain: (expiry, right, strike, bid, ask)
ROWS = [(E1, "C", 95, "7.90", "8.10"), (E1, "C", 100, "4.80", "5.00"), (E1, "C", 105, "2.60", "2.75"), (E1, "C", 110, "1.20", "1.30"),
        (E1, "C", 115, "0.50", "0.60"), (E1, "C", 120, "0.20", "0.25"),
        (E1, "P", 90, "1.00", "1.10"), (E1, "P", 95, "2.10", "2.25"), (E1, "P", 100, "4.10", "4.30"),
        (E2, "C", 100, "6.20", "6.40"), (E2, "C", 105, "3.90", "4.05")]


def D(x) -> Decimal:
    return Decimal(str(x))


def acme(right: str, strike, expiry: str = E1) -> str:
    return occ_symbol("ACME", date.fromisoformat(expiry), right, D(strike))


def build(tmp: str):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                 books=[config.TEST_BOOK], fetch_threads=0, news_enabled=False)


def add_name(rt, sym: str) -> None:
    """A fictional US listing with listed options, stored as Yahoo resolution would store it."""
    with rt.db.writer() as tx:
        rt.instruments.upsert(tx, instrument_id=sym, kind="EQUITY", symbol=sym, name=f"{sym} test listing", currency="USD", session_group="US_EQUITY",
                              account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=sym, cboe_symbol=sym, spec={"exchange": "NYQ"})


def print_acme(rt, last: str, age: int, bar_t: int, vol: int) -> None:
    """A Yahoo print of ACME observed ``age`` seconds ago on the completed 1-minute bar starting at ``bar_t`` (``vol`` shares)."""
    now = rt.clock.epoch()
    px = qprice(last)
    bar = Bar1m(int(bar_t), px, px, px, px, int(vol))
    rt.cache.put(QuoteRecord(instrument_id="ACME", provider="yahoo", source="Yahoo Finance chart (ACME)", method="direct", last=px, quote_time=now - age,
                             fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group="US_EQUITY", bar=bar,
                             bars_tail=[bar]), now)


def acme_chain(now: int, size: int = 20, fetched_at=None, quote_time=None, extra=()) -> ChainSnapshot:
    """The invented chain (``ROWS``), plus ``extra`` rows of the same shape for a test that needs more contracts listed."""
    qt = now - 60 if quote_time is None else quote_time
    rows = [ChainRow(acme(r, k, e), e, r, qprice(k), qprice(b), qprice(a), size, size, None, None, 0.30, None, None, None, None, 100, 10)
            for e, r, k, b, a in list(ROWS) + list(extra)]
    return ChainSnapshot("ACME", "ACME", ChainUnderlying("ACME", qprice("100.00"), None, None, None, None, qt, None, 0.30), rows,
                         now if fetched_at is None else fetched_at, qt, len(rows))


def load_chain(rt, snap: ChainSnapshot) -> list:
    """What the quote worker does with a fetched chain: cache it, then record every contract of it the master lists.  The provider
    answers the same snapshot, so a fetch the worker might make returns it unchanged."""
    rt.fp.set_chain("ACME", snap)
    rt.chains.put(snap)
    now = rt.clock.epoch()
    changed = []
    for r in records_from_chain(snap, rt.instruments):
        changed += rt.cache.put(r, now)
    return changed


def leg(iid: str, side: str, qty: int, **kw) -> dict:
    return dict({"instrument_id": iid, "side": side, "qty": qty}, **kw)


class SmartSubmitBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-smartsub-")
        self.rt = build(self.tmp)
        add_name(self.rt, "ACME")
        add_name(self.rt, "WIDG")
        self.now = self.rt.clock.epoch()
        print_acme(self.rt, "100.00", 120, self.now - 180, 50000)       # observed 13:58:00 on the 13:57 bar
        load_chain(self.rt, acme_chain(self.now))

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the routes, as the page and the order ticket call them ---------------------------------------------------------------------
    def call(self, method: str, path: str, body=None, rid=None):
        b = dict(body or {})
        if rid is not None:
            b["request_id"] = rid
        return self.rt.router.handle(method, path, None, b, "127.0.0.1", None)

    def send(self, body: dict, rid: str, book: str = "T"):
        return self.call("POST", f"/api/orders?book={book}", body, rid)

    def parse(self, text: str, book: str = "T") -> dict:
        """The Smart Ticket's reading (its only request to the server)."""
        st, out, _h = self.call("POST", f"/api/smart-ticket/parse?book={book}", {"text": text, "book": book})
        self.assertEqual(200, st, out)
        return out

    def ticket(self, req: dict, rid: str, book: str = "T") -> tuple:
        """What the order ticket does with a card's request (or any order typed in it): its own preview, then the order under the
        ticket's request id with that preview's id.  Returns (http status, answer)."""
        st, pv, _h = self.call("POST", f"/api/orders/preview?book={book}", dict(req))
        body = dict(req, preview_id=pv["preview_id"]) if st == 200 and pv.get("preview_id") else dict(req)
        st, out, _h = self.send(body, rid, book)
        return st, out

    def seq_package(self, primary: dict, hedge, rid: str = "ui:seq-package-0001", preview: bool = True) -> tuple:
        """A sequential package as the order ticket places it: the shares order ``primary`` (LIMIT or MARKET) with its hedge - the Hedges
        tab's pick ``{candidate_id, ratio, edits}`` (a dict), or the hedge's own legs (a list: ``{"legs": [...]}``) - after the ticket's
        preview of it.  Returns (http status, answer, the shares order's row, its legs' rows ``<rid>:H<i>`` in order)."""
        body = dict(primary, hedge=hedge if isinstance(hedge, dict) else {"legs": list(hedge)})
        if preview:
            st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(body))
            self.assertEqual(200, st, pv)
            body["preview_id"] = pv["preview_id"]
        st, out, _h = self.send(body, rid)
        if st != 200:
            return st, out, None, []
        p = self.q("SELECT * FROM orders WHERE request_id=?", (rid,))[0]
        legs = sorted(self.q("SELECT * FROM orders WHERE request_id LIKE ?", (rid + ":H%",)), key=lambda r: int(r["request_id"].rsplit(":H", 1)[1]))
        return st, out, p, legs

    def template_package(self, key: str, units: int, strikes: Optional[dict] = None, tenor_months: int = 1, rid: str = "ui:tpl-package-0001",
                         **extra) -> tuple:
        """A Strategies-tab package on ACME: ``{strategy_key, underlying, units, tenor_months, strikes}`` (tenor 1 reaches 2026-12-18 here;
        a strike override >= 60 is the strike itself), plus any ``extra`` body keys.  Returns (http status, answer)."""
        body = dict({"account_id": ACCT, "tif": "DAY", "package": {"strategy_key": key, "underlying": "ACME", "units": units, "tenor_months": tenor_months,
                                                                   "strikes": {str(i): v for i, v in (strikes or {}).items()}}}, **extra)
        st, out, _h = self.send(body, rid)
        return st, out

    def q(self, sql: str, params=()):
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def fills_of(self, oids):
        """(order, order side, qty, price) of each fill (a fill row keeps the cash side: BUY for BTO / BTC, SELL for STO / STC)."""
        out = []
        for oid in oids:
            side = self.q("SELECT side FROM orders WHERE order_id=?", (oid,))[0]["side"]
            for f in self.q("SELECT * FROM fills WHERE order_id=? ORDER BY fill_id", (oid,)):
                self.assertEqual("BUY" if side in ("BUY", "BTO", "BTC", "BUY_TO_COVER") else "SELL", f["side"])
                out.append((f["order_id"], side, int(f["qty"]), D(f["price"])))
        return out


# ===== a single leg: the card's request is the ticket's own order =========================================================================
class SingleLegTests(SmartSubmitBase):
    def test_a_a_legs_request_sent_as_the_ticket_sends_it_places_exactly_the_read_order(self):
        """The card's request is a standard order ticket: sent as the ticket sends any order (no ``smart`` note, no ``st:`` request id), it
        places exactly the order a hand-typed ticket with the same fields places, and the request ids keep their rules."""
        rt = self.rt
        ticket = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "90.00", "tif": "DAY"}
        st, a, _h = self.send(ticket, "ui:ticket-acme-000001")
        self.assertEqual(200, st, a)
        (l,) = self.parse("buy 10 ACME limit 90")["legs"]
        req = l["request"]
        self.assertEqual(dict(ticket, limit_px="90"), {k: v for k, v in req.items() if v not in (None, False)})   # the ticket's fields as typed
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(req))
        self.assertEqual(200, st, pv)
        body = dict(req, preview_id=pv["preview_id"])
        rid = "ui:ticket-acme-000002"
        st, b, h = self.send(body, rid)
        self.assertEqual(200, st, b)
        self.assertNotIn("X-Replayed", h)
        # the same order for the same fields: a 90.00 limit below the 100.00 print rests; 10 x 90.00 + the 1.00 minimum fee reserved
        cols = ("account_id", "instrument_id", "side", "qty", "filled_qty", "order_type", "tif", "status", "expires_ts", "fill_mode", "package_id")
        oa, ob = (self.q("SELECT * FROM orders WHERE order_id=?", (x["order"]["order_id"],))[0] for x in (a, b))
        self.assertEqual(dict({k: oa[k] for k in cols}, limit_px=D(oa["limit_px"])), dict({k: ob[k] for k in cols}, limit_px=D(ob["limit_px"])))
        self.assertEqual(("WORKING", D("90.00"), rid), (ob["status"], D(ob["limit_px"]), ob["request_id"]))
        stored = json.loads(self.q("SELECT payload_json FROM commands WHERE request_id=?", (rid,))[0]["payload_json"])
        self.assertEqual(set(), {"smart", "aon_confirmed", "seq_confirmed"} & set(stored))
        self.assertEqual([], self.q("SELECT order_id FROM orders WHERE request_id LIKE 'st:%'"))
        n_orders = self.q("SELECT COUNT(*) n FROM orders")[0]["n"]
        # a resend of the unchanged order is a replay: the same answer, X-Replayed, no second order
        st, again, h = self.send(body, rid)
        self.assertEqual((200, "1", b["order"]["order_id"]), (st, h.get("X-Replayed"), again["order"]["order_id"]))
        # ... also with a FRESH preview id and confirm_large false (the ticket's PREVIEW_STALE / CONFIRM_REQUIRED resends, INV-7)
        st, pv2, _h = self.call("POST", "/api/orders/preview?book=T", dict(req, confirm_large=False))
        self.assertEqual(200, st, pv2)
        st, again, h = self.send(dict(req, preview_id=pv2["preview_id"], confirm_large=False), rid)
        self.assertEqual((200, "1", b["order"]["order_id"]), (st, h.get("X-Replayed"), again["order"]["order_id"]))
        self.assertEqual(n_orders, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # the same id with another payload is refused, nothing written
        st, out, _h = self.send(dict(body, qty=11), rid)
        self.assertEqual((409, "REQUEST_ID_REUSED"), (st, out["error"]["code"]))
        self.assertEqual(n_orders, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # a refusal leaves no row: limit 90.005 is off the 0.01 tick; the same id works once the price is fixed
        rid2 = "ui:ticket-acme-000003"
        st, out, _h = self.send(dict(req, limit_px="90.005"), rid2)
        self.assertEqual((400, "PRICE_TICK"), (st, out["error"]["code"]))
        self.assertEqual([], self.q("SELECT id FROM commands WHERE request_id=?", (rid2,)))
        self.assertEqual([], self.q("SELECT order_id FROM orders WHERE request_id=?", (rid2,)))
        st, out, _h = self.send(dict(req, limit_px="90.01"), rid2)
        self.assertEqual((200, "WORKING", D("90.01")), (st, out["order"]["status"], D(out["order"]["limit_px"])))
        # ?book= is the leg's own book: the right one passes the guard (above); another book is refused before anything is written
        rid3 = "ui:ticket-acme-000004"
        st, out, _h = self.send(dict(req, qty=12), rid3, book="P")
        self.assertEqual((409, "BOOK_MISMATCH", "T", "P"), (st, out["error"]["code"], out["error"]["details"]["book"], out["error"]["details"]["selected"]))
        self.assertEqual([], self.q("SELECT id FROM commands WHERE request_id=?", (rid3,)))
        # every id fits the request-id rule (8-80 of [A-Za-z0-9:_.-]), the server's leg suffixes included
        from finsim.terminal.commands import RID_RE
        for x in (rid, rid + ":L8", rid + ":H7"):
            self.assertTrue(RID_RE.match(x), x)
        assert_integrity(self, rt, "full")

    def test_o_the_moved_price_the_dialog_shows_is_the_one_accept_sends(self):
        """Third review: a 409 PREVIEW_STALE carries the order's fresh preview, and that preview is STORED (it has a preview_id), so
        "Accept & continue" sends the figures the dialog showed: a price that moved again while the dialog was open comes back stale and
        asks again; unmoved, it fills."""
        ticket = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "MARKET", "tif": "DAY"}
        rid = f"st:{TICKET}:L1:r1"
        smart = {"ticket": TICKET, "item": "L1", "clause": "buy 10 ACME"}
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(ticket, confirm_large=False))
        self.assertEqual((200, D("100")), (st, D(pv["execution"]["expected_price"])), pv)                # the confirm: 10 x 100.00
        print_acme(self.rt, "103.00", 30, self.now - 120, 50000)                                    # 3 % against the buy
        st, out, _h = self.send(dict(ticket, preview_id=pv["preview_id"], confirm_large=False, smart=smart), rid)
        self.assertEqual((409, "PREVIEW_STALE"), (st, out["error"]["code"]), out)
        shown = out["error"]["details"]["preview"]                                                   # what the dialog shows
        self.assertEqual(D("103"), D(shown["execution"]["expected_price"]))
        self.assertTrue(str(shown.get("preview_id") or "").startswith("pv:"), shown.get("preview_id"))
        self.assertIsNotNone(self.rt.previews.get(shown["preview_id"], self.rt.clock.epoch()))                # in the preview store
        print_acme(self.rt, "106.00", 20, self.now - 120, 50000)                                    # moves again while the dialog is open
        st, out, _h = self.send(dict(ticket, preview_id=shown["preview_id"], confirm_large=False, smart=smart), rid)   # Accept
        self.assertEqual((409, "PREVIEW_STALE"), (st, out["error"]["code"]), out)                    # asks again: nothing placed
        self.assertEqual([], self.q("SELECT order_id FROM orders"))
        again = out["error"]["details"]["preview"]
        st, out, _h = self.send(dict(ticket, preview_id=again["preview_id"], confirm_large=False, smart=smart), rid)   # Accept, unmoved
        self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)
        self.assertEqual([(10, D("106"))], [(int(f["qty"]), D(f["price"])) for f in self.q("SELECT qty, price FROM fills")])
        assert_integrity(self, self.rt, "full")


# ===== sequential packages: the shares first, the options wait and follow its fills ======================================================
class SequentialComboTests(SmartSubmitBase):
    def test_g_children_wait_then_resize_to_the_primarys_fill(self):
        """The Hedges tab's collar picked for BUY 1,000 ACME limit 99.  With two puts listed on the default expiry for this test (Jan 15 2027
        90 bid 2.40 / ask 2.55, 95 bid 3.80 / ask 4.00), COLLAR_7 is BTO 10 Jan 95 puts + STO 10 Jan 105 calls (K = 0.93 x 100.00 = 93 ->
        nearest listed 95; 1.07 x 100.00 = 107 -> 105; n = floor(1,000 / 100) = 10; ratio 10 / 1,000 = 0.01)."""
        rt = self.rt
        put, call = acme("P", 95, E2), acme("C", 105, E2)
        extra = [(E2, "P", 90, "2.40", "2.55"), (E2, "P", 95, "3.80", "4.00")]
        load_chain(rt, acme_chain(self.now, extra=extra))
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 1000, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        pick = {"candidate_id": "COLLAR_7", "ratio": None, "edits": {}}
        # a preview id the store does not know: the package checks its shares order's preview like a single order -> 409 PREVIEW_STALE
        st, out, _h = self.send(dict(primary, hedge=pick, preview_id="pv:0000000000000000"), "ui:seq-g-000001")
        self.assertEqual((409, "PREVIEW_STALE"), (st, out["error"]["code"]))
        self.assertEqual([], self.q("SELECT order_id FROM orders"))
        # the ticket's fresh preview, then the order with its hedge
        rid = "ui:seq-g-000002"
        st, out, p, (hp, hc) = self.seq_package(primary, pick, rid=rid)
        self.assertEqual(200, st, out)
        # the 99.00 limit is below the 100.00 print: the shares rest; the puts and calls wait for them
        self.assertEqual(("ACME", "BUY", 1000, "LIMIT", D("99.00"), "WORKING", 0), (p["instrument_id"], p["side"], p["qty"], p["order_type"], D(p["limit_px"]),
                                                                                    p["status"], p["filled_qty"]))
        for r, (iid, side) in zip((hp, hc), ((put, "BTO"), (call, "STO"))):
            self.assertEqual((iid, side, 10, "MARKET", "WAITING_PARENT", p["order_id"], "0.01", p["package_id"]),
                             (r["instrument_id"], r["side"], r["qty"], r["order_type"], r["status"], r["parent_order_id"], r["ratio"], r["package_id"]))
        pk = self.q("SELECT candidate_id, fill_mode, primary_order_id FROM hedge_packages WHERE package_id=?", (p["package_id"],))[0]
        self.assertEqual(("COLLAR_7", "SEQUENTIAL", p["order_id"]), (pk["candidate_id"], pk["fill_mode"], pk["primary_order_id"]))
        stored = json.loads(self.q("SELECT payload_json FROM commands WHERE request_id=?", (rid,))[0]["payload_json"])
        self.assertEqual(("COLLAR_7", None), (stored["hedge"]["candidate_id"], stored.get("smart")))
        # a resend is a replay: no second package
        st, again, h2 = self.send(dict(primary, hedge=pick, preview_id=stored["preview_id"]), rid)
        self.assertEqual((200, "1"), (st, h2.get("X-Replayed")))
        self.assertEqual(3, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # the same id with another hedge (a ratio) is another payload: 409 REQUEST_ID_REUSED, nothing written
        st, out, _h = self.send(dict(primary, hedge=dict(pick, ratio="0.011"), preview_id=stored["preview_id"]), rid)
        self.assertEqual((409, "REQUEST_ID_REUSED"), (st, out["error"]["code"]))
        self.assertEqual(3, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # a print at 98.90 on the next completed bar (13:59, 4,000 shares): the resting 99.00 limit is crossed and fills at its limit, capped
        # at 10 % x 4,000 = 400 shares; each leg is resized to floor(400 x 10 / 1,000) = 4 and starts working
        print_acme(rt, "98.90", 10, self.now - 60, 4000)
        self.assertEqual(1, rt.matcher.on_quotes(["ACME"]))
        p = self.q("SELECT * FROM orders WHERE order_id=?", (p["order_id"],))[0]
        self.assertEqual(("PARTIAL", 400), (p["status"], p["filled_qty"]))
        self.assertEqual([(p["order_id"], "BUY", 400, D("99.00"))], self.fills_of([p["order_id"]]))
        for oid in (hp["order_id"], hc["order_id"]):
            r = self.q("SELECT * FROM orders WHERE order_id=?", (oid,))[0]
            self.assertEqual(("WORKING", 4, 0), (r["status"], r["qty"], r["filled_qty"]))
            self.assertTrue(any("resized to 4 = floor(400" in e["note"] for e in self.q("SELECT note FROM order_events WHERE order_id=?", (oid,))))
        # the next chain observation (20 displayed >= 4): the puts are bought at the 4.00 ask, the calls sold at the 3.90 bid
        rt.clock.advance(30)
        load_chain(rt, acme_chain(rt.clock.epoch(), extra=extra))
        rt.matcher.on_quotes([put, call])
        self.assertEqual([(hp["order_id"], "BTO", 4, D("4.00")), (hc["order_id"], "STO", 4, D("3.90"))], self.fills_of([hp["order_id"], hc["order_id"]]))
        pos = {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}
        self.assertEqual({"ACME": 400, put: 4, call: -4}, pos)
        assert_integrity(self, rt, "full")

    def test_h_a_package_of_300_shares_and_one_call_releases_the_call(self):
        """1.0.7.2 review: the release is whole-number arithmetic on the leg's own quantity: 1 call for 300 shares (the hedge's own leg;
        ratio 0.00333333 at 8 decimals) is released once the shares have filled, never lost to floor(300 x 0.00333333) = 0."""
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 300, "order_type": "MARKET", "tif": "DAY"}
        st, out, p, (c,) = self.seq_package(primary, [leg(acme("C", 105), "STO", 1)], rid="ui:seq-h-000001")
        self.assertEqual(200, st, out)
        self.assertEqual(("FILLED", 300, "0.00333333"), (p["status"], p["filled_qty"], c["ratio"]))
        self.assertNotEqual("WAITING_PARENT", c["status"])
        self.assertEqual(1, int(c["qty"]))
        self.assertTrue(any(e["note"].startswith("resized to 1 = floor(300 ") and e["note"].endswith(" 1 / 300) after the primary's fill")
                            for e in self.q("SELECT note FROM order_events WHERE order_id=?", (c["order_id"],))))
        assert_integrity(self, self.rt, "full")


# ===== the release after the shares' quantity changed (1.0.7.2 second review) ========================================================
class ReleaseAfterAmendOrReplaceTests(SmartSubmitBase):
    """BUY 300 ACME limit 99 (below the 100.00 print: the shares rest) + STO 3 Dec 18 2026 105 calls (bid 2.60), ratio 3 / 300 = 0.01.
    Fourth review: the release is floor(shares filled x leg qty / shares qty) in whole numbers, with the two quantities as they were
    confirmed (their submit records), whatever quantity an amend or a replace gave the shares - never a half-up rounding of an amended
    quantity, never a tolerance that grows with the quantity; over a replace the shares are counted over the chain."""
    CALL = acme("C", 105)

    def package(self, shares: int, calls: int, n: int = 1):
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": shares, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        st, out, p, (c,) = self.seq_package(primary, [leg(self.CALL, "STO", calls)], rid=f"ui:seq-release-{n:04d}")
        self.assertEqual(200, st, out)
        self.assertEqual(("WORKING", "WAITING_PARENT", calls), (p["status"], c["status"], int(c["qty"])))
        return p["order_id"], c["order_id"]

    def bar(self, vol: int):
        """The next completed 1-minute bar printing 98.90 (crosses the 99.00 limit) with ``vol`` shares: 10 % of it fills."""
        self.rt.clock.advance(60)
        now = self.rt.clock.epoch()
        print_acme(self.rt, "98.90", 10, now - 60, vol)
        self.rt.matcher.on_quotes(["ACME"])

    def row(self, oid: str) -> dict:
        return self.q("SELECT * FROM orders WHERE order_id=?", (oid,))[0]

    def sell_calls(self):
        load_chain(self.rt, acme_chain(self.rt.clock.epoch()))
        self.rt.matcher.on_quotes([self.CALL])

    def positions(self) -> dict:
        return {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}

    def test_j_an_amended_or_replaced_quantity_releases_at_the_ratio_never_one_call_more(self):
        got = {}
        for case, qty in (("amended up", 350), ("amended down", 250), ("replaced", 350)):
            with self.subTest(case=case):
                if got:
                    self.tearDown()
                    self.setUp()
                p, c = self.package(300, 3)
                if case.startswith("amended"):
                    st, out, _h = self.call("PATCH", f"/api/orders/{p}?book=T", {"qty": qty}, f"ui:amend-{qty}-0001")
                else:
                    st, out, _h = self.call("POST", f"/api/orders/{p}/replace?book=T", {"qty": qty, "limit_px": "99.00"}, f"ui:replace-{qty}-0001")
                    p = out["order"]["order_id"]
                self.assertEqual(200, st, out)
                self.assertEqual(p, self.row(c)["parent_order_id"])
                self.bar(50000)                                                      # 5,000 shares available: the shares fill in full
                self.assertEqual(("FILLED", qty), (self.row(p)["status"], int(self.row(p)["filled_qty"])))
                self.sell_calls()
                ch = self.row(c)
                got[case] = (int(ch["qty"]), int(ch["filled_qty"]), self.positions())
                # 350 x 3 / 300 = 3.5 and 250 x 3 / 300 = 2.5: floor - 1.0.7.1's 3 and 2, never 4 and 3
                self.assertTrue(any(e["note"] == f"resized to {qty // 100:,} = floor({qty:,} \u00d7 3 / 300) after the primary's fill"
                                    for e in self.q("SELECT note FROM order_events WHERE order_id=?", (c,))), case)
                assert_integrity(self, self.rt, "full")
        self.assertEqual({"amended up": (3, 3, {"ACME": 350, self.CALL: -3}), "amended down": (2, 2, {"ACME": 250, self.CALL: -2}),
                          "replaced": (3, 3, {"ACME": 350, self.CALL: -3})}, got)

    def test_k_a_replace_after_a_partial_fill_releases_every_leg_on_the_whole_chain(self):
        # 300 shares + 1 call (ratio 0.00333333); 10 % x 2,000 = 200 shares fill: 200 x 1 // 300 = 0, the call waits; the rest (100) is
        # re-priced by a replace; once those 100 fill, the chain has 300 of 300: 300 x 1 // 300 = 1 call released and sold
        p, c = self.package(300, 1)
        self.bar(2000)
        self.assertEqual(("PARTIAL", 200, "WAITING_PARENT"), (self.row(p)["status"], int(self.row(p)["filled_qty"]), self.row(c)["status"]))
        st, out, _h = self.call("POST", f"/api/orders/{p}/replace?book=T", {"limit_px": "99.50"}, "ui:replace-rest-0001")
        self.assertEqual(200, st, out)
        new = out["order"]["order_id"]
        self.assertEqual((new, 100), (self.row(c)["parent_order_id"], int(self.row(new)["qty"])))
        self.bar(50000)
        self.assertEqual(("FILLED", 100), (self.row(new)["status"], int(self.row(new)["filled_qty"])))
        self.assertTrue(any(e["note"] == "resized to 1 = floor(300 \u00d7 1 / 300) after the primary's fill" for e in self.q("SELECT note FROM order_events WHERE order_id=?", (c,))))
        self.sell_calls()
        self.assertEqual(({"ACME": 300, self.CALL: -1}, "FILLED"), (self.positions(), self.row(c)["status"]))
        assert_integrity(self, self.rt, "full")
        # 300 shares + 3 calls; 150 fill: 150 x 3 // 300 = 1 call released (it stays with the replaced order); the rest (150) replaced and
        # filled: the chain's 300 of 300 resize that released call to 3
        self.tearDown()
        self.setUp()
        p, c = self.package(300, 3)
        self.bar(1500)
        self.assertEqual(("WORKING", 1), (self.row(c)["status"], int(self.row(c)["qty"])))
        st, out, _h = self.call("POST", f"/api/orders/{p}/replace?book=T", {"limit_px": "99.50"}, "ui:replace-rest-0002")
        self.assertEqual(200, st, out)
        new = out["order"]["order_id"]
        self.bar(50000)
        self.assertEqual(("FILLED", 150), (self.row(new)["status"], int(self.row(new)["filled_qty"])))
        self.assertEqual((p, 3), (self.row(c)["parent_order_id"], int(self.row(c)["qty"])))
        self.sell_calls()
        self.assertEqual({"ACME": 300, self.CALL: -3}, self.positions())
        assert_integrity(self, self.rt, "full")

    def test_p_millions_of_shares_never_release_a_call_the_shares_do_not_cover(self):
        """Fourth review: 3,000,000 WIDG + STO 30,000 calls (ratio 0.01) amended to 2,999,999 releases floor(2,999,999 x 30,000 / 3,000,000)
        = 29,999 calls (1.0.7.1's floor(2,999,999 x 0.01) too), never the 30,000 a tolerance growing with the quantity read as fitting."""
        call = occ_symbol("WIDG", date(2026, 12, 18), "C", Decimal("1.5"))

        def widg(last: str, age: int, vol: int):
            now = self.rt.clock.epoch()
            px = qprice(last)
            bar = Bar1m(int(now - 60), px, px, px, px, vol)
            self.rt.cache.put(QuoteRecord(instrument_id="WIDG", provider="yahoo", source="Yahoo Finance chart (WIDG)", method="direct", last=px,
                                          quote_time=now - age, fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD",
                                          session_group="US_EQUITY", bar=bar, bars_tail=[bar]), now)

        def chain():
            now = self.rt.clock.epoch()
            rows = [ChainRow(call, "2026-12-18", "C", qprice("1.5"), qprice("0.05"), qprice("0.06"), 100000, 100000, None, None, 0.30, None, None, None, None, 100, 10)]
            snap = ChainSnapshot("WIDG", "WIDG", ChainUnderlying("WIDG", qprice("1.00"), None, None, None, None, now - 60, None, 0.30), rows, now, now - 60, len(rows))
            self.rt.fp.set_chain("WIDG", snap)
            self.rt.chains.put(snap)
            for r in records_from_chain(snap, self.rt.instruments):
                self.rt.cache.put(r, now)
        widg("1.00", 120, 50_000_000)
        chain()
        body = {"account_id": ACCT, "instrument_id": "WIDG", "side": "BUY", "qty": 3_000_000, "order_type": "LIMIT", "limit_px": "0.99", "tif": "DAY",
                "confirm_large": True, "hedge": {"legs": [{"instrument_id": call, "side": "STO", "qty": 30_000}]}}
        st, out, _h = self.send(body, "ui:big-widg-0001")
        self.assertEqual(200, st, out)
        p = out["order"]["order_id"]
        c = self.q("SELECT order_id FROM orders WHERE parent_order_id=?", (p,))[0]["order_id"]
        self.assertEqual(("0.01", 30_000), (self.row(c)["ratio"], int(self.row(c)["qty"])))
        st, out, _h = self.call("PATCH", f"/api/orders/{p}?book=T", {"qty": 2_999_999, "confirm_large": True}, "ui:big-widg-amend-01")
        self.assertEqual(200, st, out)
        for _ in range(2):
            self.rt.clock.advance(60)
            widg("0.98", 10, 50_000_000)                                 # 5,000,000 may fill a bar
            self.rt.matcher.on_quotes(["WIDG"])
        chain()
        self.rt.matcher.on_quotes([call])
        self.assertEqual(("FILLED", 2_999_999), (self.row(p)["status"], int(self.row(p)["filled_qty"])))
        self.assertEqual(("FILLED", 29_999, 29_999), (self.row(c)["status"], int(self.row(c)["qty"]), int(self.row(c)["filled_qty"])))
        self.assertEqual({"WIDG": 2_999_999, call: -29_999}, self.positions())
        self.assertTrue(any(e["note"] == "resized to 29,999 = floor(2,999,999 \u00d7 30,000 / 3,000,000) after the primary's fill"
                            for e in self.q("SELECT note FROM order_events WHERE order_id=?", (c,))))
        assert_integrity(self, self.rt, "full")

    def test_l_a_primary_ended_after_a_partial_fill_ends_the_legs_still_waiting(self):
        # 300 shares + 1 call: 200 fill (200 x 1 // 300 = 0, the call waits), then the owner cancels the shares: nothing can release the
        # call any more, so it ends with them (CANCELLED, said) instead of waiting until it expires; the cancel answer reports it with
        # its primary.  A leg already released keeps working (test_terminal_orders test_d7, test_terminal_mass_cancel)
        p, c = self.package(300, 1)
        self.bar(2000)
        self.assertEqual(("PARTIAL", 200, "WAITING_PARENT"), (self.row(p)["status"], int(self.row(p)["filled_qty"]), self.row(c)["status"]))
        st, out, _h = self.call("POST", "/api/orders/cancel?book=T", {"order_ids": [p]}, "mc:release0001")
        self.assertEqual(200, st, out)
        self.assertEqual([(p, None), (c, "primary")], [(x["order_id"], x.get("with")) for x in out["cancelled"]])
        ch = self.row(c)
        self.assertEqual(("CANCELLED", "primary ended before it was due"), (ch["status"], ch["reason"]))
        self.assertEqual("primary " + p + " ended with 200 shares filled: floor(200 \u00d7 1 / 300) = 0, none of this leg was due",
                         self.q("SELECT note FROM order_events WHERE order_id=? ORDER BY rowid DESC LIMIT 1", (c,))[0]["note"])
        self.assertEqual({"ACME": 200}, self.positions())
        assert_integrity(self, self.rt, "full")

    def test_n_a_leg_that_ends_with_its_primary_is_said(self):
        """Third review: a confirmed leg that ends with its primary is never silent.  The x (DELETE) names it in its answer
        (``ended_with_it``, which the page's toast shows); a fill of an amended quantity the ratio does not fit, or the session close,
        raises ONE INFO attention item (HEDGE_LEG_ENDED) naming it."""
        def items():
            return [(r["severity"], r["code"], r["ref_id"]) for r in self.q("SELECT severity, code, ref_id FROM attention WHERE code='HEDGE_LEG_ENDED' ORDER BY id")]
        # the x: 300 shares + 1 call, 200 filled (200 x 1 // 300 = 0: the call waits)
        p, c = self.package(300, 1)
        self.bar(2000)
        st, out, _h = self.call("DELETE", f"/api/orders/{p}?book=T", {}, "ui:x-names-the-leg-01")
        self.assertEqual(200, st, out)
        self.assertEqual([(c, "CANCELLED", "primary ended before it was due", "STO", 1, self.CALL)],
                         [(x["order_id"], x["status"], x["reason"], x["side"], x["qty"], x["instrument_id"]) for x in out["ended_with_it"]])
        self.assertEqual([], items())                                    # the owner's own cancel: said in its answer, no attention item
        st, again, h = self.call("DELETE", f"/api/orders/{p}?book=T", {}, "ui:x-names-the-leg-01")
        self.assertEqual(("1", out["ended_with_it"]), (h.get("X-Replayed"), again["ended_with_it"]))
        # the session close ends the primary (DAY): one INFO item names the call
        self.tearDown()
        self.setUp()
        p, c = self.package(300, 1)
        self.bar(2000)
        self.assertTrue(self.rt.lifecycle.tick(at="2026-10-20T16:00:01-04:00", only="EXPIRE_ORDERS")["ran"])
        self.assertEqual(("EXPIRED", "CANCELLED"), (self.row(p)["status"], self.row(c)["status"]))
        self.assertEqual([("INFO", "HEDGE_LEG_ENDED", p)], items())
        msg = self.q("SELECT message FROM attention WHERE code='HEDGE_LEG_ENDED'")[0]["message"]
        self.assertIn(c, msg)
        self.assertIn("200 of 300 shares filled", msg)
        assert_integrity(self, self.rt, "full")
        # a fill ends the primary: 300 + 1 call amended to 250 shares (250 x 0.00333333 is not whole: floor(250 x 0.00333333) = 0)
        self.tearDown()
        self.setUp()
        p, c = self.package(300, 1)
        st, out, _h = self.call("PATCH", f"/api/orders/{p}?book=T", {"qty": 250}, "ui:amend-250-0002")
        self.assertEqual(200, st, out)
        self.bar(50000)
        self.assertEqual(("FILLED", "CANCELLED"), (self.row(p)["status"], self.row(c)["status"]))
        self.assertEqual([("INFO", "HEDGE_LEG_ENDED", p)], items())
        self.assertEqual({"ACME": 250}, self.positions())
        assert_integrity(self, self.rt, "full")


class HedgeLegLifetimeTests(SmartSubmitBase):
    """1.0.7.2 fifth review: a sequential package's hedge legs live exactly as long as their shares order.  BUY 300 ACME limit 99 (below the
    100.00 print: it rests) + BTO 3 Dec 18 2026 95 puts limit 1.00 (below the 2.10 bid: they rest once released), DAY, placed as the order
    ticket places a shares order with its hedge.  A shares order amended or re-priced to a longer time in force (GTC, next session) takes its legs with it - their time in
    force, expiry, good-till date and next-session start - so at the close the puts still wait, and the next session they are released
    as the shares fill.  A waiting leg is never expired on its own clock while its shares order works (a leg left with an older expiry is
    kept, then takes its shares order's lifetime when released); a leg whose own contract ends while its shares order still works ends,
    and ONE INFO item (HEDGE_LEG_ENDED) names it and the shares order."""
    PUT = acme("P", 95)
    bar = ReleaseAfterAmendOrReplaceTests.bar
    row = ReleaseAfterAmendOrReplaceTests.row
    positions = ReleaseAfterAmendOrReplaceTests.positions

    def package(self, tif: str = "DAY"):
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 300, "order_type": "LIMIT", "limit_px": "99.00", "tif": tif}
        st, out, p, (h,) = self.seq_package(primary, [leg(self.PUT, "BTO", 3, order_type="LIMIT", limit_px="1.00")], rid="ui:seq-life-0001")
        self.assertEqual(200, st, out)
        self.assertEqual(("WORKING", tif, "WAITING_PARENT", tif, int(p["expires_ts"])), (p["status"], p["tif"], h["status"], h["tif"], int(h["expires_ts"])))
        return p["order_id"], h["order_id"]

    def lifetime(self, oid: str) -> tuple:
        r = self.row(oid)
        return r["tif"], int(r["expires_ts"]), r.get("gtd_date"), r.get("activate_ts")

    def items(self) -> list:
        return [(r["ref_id"], r["message"]) for r in self.q("SELECT ref_id, message FROM attention WHERE code='HEDGE_LEG_ENDED' ORDER BY id")]

    def close_then_next_session(self) -> None:
        """The 16:00 close (EXPIRE_ORDERS), then Wed 2026-10-21 10:00 ET: a completed bar printing 98.90 (it crosses the 99.00 limit)."""
        self.assertTrue(self.rt.lifecycle.tick(at="2026-10-20T16:00:01-04:00", only="EXPIRE_ORDERS")["ran"] is not None)
        self.rt.clock.set("2026-10-21T10:00:00-04:00")
        now = self.rt.clock.epoch()
        print_acme(self.rt, "98.90", 10, now - 60, 50000)
        self.rt.matcher.on_quotes(["ACME"])

    def test_q_a_shares_order_kept_working_longer_keeps_its_waiting_legs(self):
        done = False
        for how, body in (("amend", {"tif": "GTC"}), ("replace", {"limit_px": "99.00", "tif": "GTC"}), ("replace", {"limit_px": "99.00", "tif": "NEXT_SESSION"})):
            with self.subTest(how=how, tif=body["tif"]):
                if done:
                    self.tearDown()
                    self.setUp()
                done = True
                p, h = self.package()
                if how == "amend":
                    st, out, _h = self.call("PATCH", f"/api/orders/{p}?book=T", body, "ui:st5-life-amend-01")
                else:
                    st, out, _h = self.call("POST", f"/api/orders/{p}/replace?book=T", body, f"ui:st5-life-repl-{body['tif'][:3].lower()}")
                self.assertEqual(200, st, out)
                p = out["order"]["order_id"]
                self.assertEqual([h], out["hedge_legs_follow"])
                self.assertEqual(self.lifetime(p), self.lifetime(h))          # the leg keeps its shares order's lifetime
                self.assertIn(f"keeps its time in force", self.q("SELECT note FROM order_events WHERE order_id=? ORDER BY id DESC LIMIT 1", (h,))[0]["note"])
                self.close_then_next_session()
                self.assertEqual(("FILLED", 300), (self.row(p)["status"], int(self.row(p)["filled_qty"])))
                self.assertEqual(("WORKING", 3, p), (self.row(h)["status"], int(self.row(h)["qty"]), self.row(h)["parent_order_id"]))
                self.assertEqual([], self.items())
                self.assertEqual({"ACME": 300}, self.positions())
                assert_integrity(self, self.rt, "full")

    def test_r_a_waiting_leg_is_never_expired_on_its_own_clock_while_its_shares_order_works(self):
        p, h = self.package()
        # a package whose shares order was given a longer time in force before this rule (fixture only: the leg keeps today's DAY expiry)
        with self.rt.db.writer() as tx:
            tx.conn.execute("UPDATE orders SET tif='GTC', expires_ts=expires_ts + 7776000 WHERE order_id=?", (p,))
        self.close_then_next_session()
        self.assertEqual(("FILLED", "WORKING", 3), (self.row(p)["status"], self.row(h)["status"], int(self.row(h)["qty"])))
        self.assertEqual(self.lifetime(p)[:2], self.lifetime(h)[:2])          # released: it took its shares order's lifetime
        self.assertEqual([], [r for r in self.q("SELECT key, status FROM jobs_done WHERE job='EXPIRE_ORDERS' AND key=?", (h,)) if r["status"] == "DONE"])
        self.assertTrue(self.rt.lifecycle.tick(at="2026-10-21T16:00:01-04:00", only="EXPIRE_ORDERS")["ran"] is not None)
        self.assertEqual("WORKING", self.row(h)["status"])                     # GTC now: the close of the day it was released ends nothing
        assert_integrity(self, self.rt, "full")

    def test_s_a_leg_whose_own_contract_ends_while_its_shares_order_works_is_said(self):
        p, h = self.package("GTC")
        self.assertTrue(self.rt.lifecycle.tick(at="2026-12-21T10:00:00-05:00", only="EXPIRE_ORDERS")["ran"] is not None)
        self.assertEqual(("WORKING", "EXPIRED"), (self.row(p)["status"], self.row(h)["status"]))
        (ref, msg), = self.items()
        self.assertEqual(p, ref)
        for s in (h, p, "expired", "still works", "not hedged by this leg"):
            self.assertIn(s, msg)
        assert_integrity(self, self.rt, "full")

    def test_t_a_year_typed_before_limit_is_never_sent_as_the_price(self):
        out = self.call("POST", "/api/smart-ticket/parse?book=T", {"text": "buy 2 ACME 105c Dec 18 2026 limit", "book": "T"})[1]
        leg_ = out["legs"][0]
        self.assertEqual((None, "MISSING_PRICE"), (leg_["request"], "MISSING_PRICE" if "MISSING_PRICE" in [f["code"] for f in leg_["flags"]] else None))
        self.assertNotIn("YEAR_INFERRED", [f["code"] for f in leg_["flags"]])
        out = self.call("POST", "/api/smart-ticket/parse?book=T", {"text": "buy 2 ACME 105c Dec 18 2026 limit 2.80", "book": "T"})[1]
        req = out["legs"][0]["request"]
        self.assertEqual(("LIMIT", "2.80", acme("C", 105)), (req["order_type"], req["limit_px"], req["instrument_id"]))
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(req))
        self.assertEqual(200, st, pv)
        st, res, _h = self.send(dict(req, preview_id=pv["preview_id"]), "ui:ticket-acme-c105-01")
        self.assertEqual((200, "FILLED"), (st, res["order"]["status"]), res)
        self.assertEqual([(2, "2.75")], [(int(f["qty"]), str(qprice(f["price"]))) for f in res["fills"]])   # at the 2.75 ask, within the 2.80 typed
        assert_integrity(self, self.rt, "full")


class ReleasedLegLifetimeTests(SmartSubmitBase):
    """1.0.7.2 sixth review (the money path; the property seeds W6 117, 544, 629 and W3 10, 14, 49, 75, 91, 92, 113 of the lifetime walk, L2
    640230, 640287, 640647, 640740, 640777, 640895, 640909, 641165, 641169, 641201, 641239, 641273, 641579, 641652, 641767, 641898, 641990 and
    L5 640165, 640249, 640264, 640559, 640611, 640909, 641201, 641652, 641823, 641898, 641990 of the money-path walk): a hedge leg already
    RELEASED for shares bought only ever lives longer when its shares order is amended or re-priced - never shortened (GTC -> DAY, an earlier
    GTD), never queued for a later session (next session: it keeps working, good till that session's close), never its own mod undone -
    and the answer names it (``hedge_legs_longer``, the ticket's status line says so).  Every later release (a working leg grown, a filled
    leg re-opened by the next session's fill) gives it its shares order's longer lifetime, so it can fill; a released leg that expires
    with part of it unfilled after its shares order ended is said (HEDGE_LEG_ENDED).  The world: BUY 300 ACME limit 99 + BTO 3 Dec 18 2026
    95 puts (limit 1.00 below the 2.10 bid: they rest; or MARKET: bought on the next chain observation), a 1,000-share bar fills 100."""
    PUT = acme("P", 95)
    CALL = acme("C", 105)
    bar = ReleaseAfterAmendOrReplaceTests.bar
    row = ReleaseAfterAmendOrReplaceTests.row
    positions = ReleaseAfterAmendOrReplaceTests.positions

    def package(self, tif: str = "DAY", put_limit: Optional[str] = "1.00", call: bool = False) -> tuple:
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 300, "order_type": "LIMIT", "limit_px": "99.00", "tif": tif}
        opt = leg(self.CALL, "STO", 3) if call else (leg(self.PUT, "BTO", 3, order_type="LIMIT", limit_px=put_limit) if put_limit else leg(self.PUT, "BTO", 3))
        st, out, p, (h,) = self.seq_package(primary, [opt], rid="ui:seq-released-0001")
        self.assertEqual(200, st, out)
        return p["order_id"], h["order_id"]

    def released(self, tif: str = "DAY", put_limit: Optional[str] = "1.00", call: bool = False) -> tuple:
        p, h = self.package(tif, put_limit, call)
        self.bar(1000)                                                   # 10 % x 1,000 = 100 shares: floor(100 x 3 / 300) = 1 released
        self.assertEqual((100, "WORKING", 1), (int(self.row(p)["filled_qty"]), self.row(h)["status"], int(self.row(h)["qty"])))
        return p, h

    def change(self, p: str, how: str, body: dict, rid: str) -> tuple:
        if how == "amend":
            st, out, _h = self.call("PATCH", f"/api/orders/{p}?book=T", body, rid)
        else:
            st, out, _h = self.call("POST", f"/api/orders/{p}/replace?book=T", dict({"limit_px": "99.00"}, **body), rid)
        self.assertEqual(200, st, out)
        return out["order"]["order_id"], out

    def lifetime(self, oid: str) -> tuple:
        r = self.row(oid)
        return r["tif"], int(r["expires_ts"]), r.get("gtd_date"), r.get("activate_ts")

    def close(self, day: str = "2026-10-20") -> None:
        self.assertTrue(self.rt.lifecycle.tick(at=f"{day}T16:00:01-04:00", only="EXPIRE_ORDERS")["ran"] is not None)

    def items(self) -> list:
        return [r["message"] for r in self.q("SELECT message FROM attention WHERE code='HEDGE_LEG_ENDED' ORDER BY id")]

    def options(self) -> None:
        self.rt.clock.advance(30)
        load_chain(self.rt, acme_chain(self.rt.clock.epoch()))
        self.rt.matcher.on_quotes([self.PUT, self.CALL])

    def test_u_a_shorter_time_in_force_of_the_shares_order_never_shortens_a_released_leg(self):
        """W6 / L5: GTC package, 1 put released and resting GTC; the shares order amended to DAY, re-priced by replace with DAY, or amended
        to an earlier GTD: the released put keeps its GTC lifetime, works past the close with 300 ACME held, and nothing is said."""
        done = False
        for how, body in (("amend", {"tif": "DAY"}), ("replace", {"tif": "DAY"}), ("amend", {"tif": "GTD", "gtd_date": "2026-10-21"})):
            with self.subTest(how=how, body=body):
                if done:
                    self.tearDown()
                    self.setUp()
                done = True
                p, h = self.released("GTC")
                before = self.lifetime(h)
                head, out = self.change(p, how, body, f"ui:rv6-short-{how[:2]}-{body['tif'][:3].lower()}")
                self.assertEqual(before, self.lifetime(h))                  # never shortened
                self.assertNotIn("hedge_legs_longer", out)
                self.bar(50000)                                             # the rest of the shares fill: the put is resized to 3
                self.assertEqual(("FILLED", "WORKING", 3), (self.row(head)["status"], self.row(h)["status"], int(self.row(h)["qty"])))
                self.close()
                self.assertEqual(("WORKING", "GTC"), (self.row(h)["status"], self.row(h)["tif"]))
                self.assertEqual(({"ACME": 300}, []), (self.positions(), self.items()))
                assert_integrity(self, self.rt, "full")

    def test_v_a_time_in_force_the_owner_gave_a_released_leg_is_kept(self):
        """W6: the owner's own mod of the released put (DAY -> GTC) survives a plain re-price of the shares (DAY); and a put the owner made
        DAY in a GTC package stays DAY when a later fill grows it (a release never undoes the owner's own choice while it can still work)."""
        p, h = self.released("DAY")
        st, out, _h = self.call("PATCH", f"/api/orders/{h}?book=T", {"tif": "GTC"}, "ui:rv6-own-gtc-01")
        self.assertEqual(200, st, out)
        own = self.lifetime(h)
        head, out = self.change(p, "replace", {"qty": 200, "order_type": "LIMIT", "tif": "DAY"}, "ui:rv6-own-repl-01")
        self.assertEqual((own, None), (self.lifetime(h), out.get("hedge_legs_longer")))
        self.bar(50000)
        self.close()
        self.assertEqual(("WORKING", "GTC", 3), (self.row(h)["status"], self.row(h)["tif"], int(self.row(h)["qty"])))
        assert_integrity(self, self.rt, "full")
        self.tearDown()
        self.setUp()
        p, h = self.released("GTC")
        st, out, _h = self.call("PATCH", f"/api/orders/{h}?book=T", {"tif": "DAY"}, "ui:rv6-own-day-01")
        self.assertEqual(200, st, out)
        self.bar(2000)                                                   # 200 more shares: the put grows to floor(300 x 3 / 300) = 3, still DAY
        self.assertEqual((3, "DAY"), (int(self.row(h)["qty"]), self.row(h)["tif"]))
        self.close()
        self.assertEqual(("EXPIRED", "FILLED"), (self.row(h)["status"], self.row(p)["status"]))
        assert_integrity(self, self.rt, "full")

    def test_w_a_shares_order_for_the_next_session_never_queues_a_released_leg(self):
        """L2: DAY package, a MARKET put released for the 100 shares bought (not bought yet); the shares order re-priced (replace) or amended
        for the next session: the put keeps working now - good till that session's close (GTD), never queued - and the next chain
        observation buys it; the answer names it."""
        done = False
        for how in ("replace", "amend"):
            with self.subTest(how=how):
                if done:
                    self.tearDown()
                    self.setUp()
                done = True
                p, h = self.released("DAY", put_limit=None)
                head, out = self.change(p, how, {"tif": "NEXT_SESSION"}, f"ui:rv6-next-{how[:2]}-01")
                self.assertIsNotNone(self.row(head).get("activate_ts"))     # the shares order is queued for the next session
                self.assertEqual([h], out.get("hedge_legs_longer"))
                r = self.row(h)
                self.assertEqual(("WORKING", None, "GTC", "2026-10-21"), (r["status"], r.get("activate_ts"), r["tif"], r.get("gtd_date")))
                self.assertEqual(int(self.row(head)["expires_ts"]), int(r["expires_ts"]))
                self.assertTrue(any("now works as long as it (GTD 2026-10-21)" in e["note"] for e in self.q("SELECT note FROM order_events WHERE order_id=?", (h,))))
                self.options()
                self.assertEqual(("FILLED", 1), (self.row(h)["status"], int(self.row(h)["filled_qty"])))
                self.assertEqual({"ACME": 100, self.PUT: 1}, self.positions())
                assert_integrity(self, self.rt, "full")

    def test_x_a_released_leg_lives_as_long_as_a_shares_order_kept_working_longer(self):
        """The fifth review's trigger, for a leg already released: DAY package, 1 put released and resting; the shares order amended to GTC:
        the put takes GTC (only ever longer; the answer and the ticket's status line name it), works past the close, and the next session's
        fill grows it to 3."""
        p, h = self.released("DAY")
        _head, out = self.change(p, "amend", {"tif": "GTC"}, "ui:rv6-longer-01")
        self.assertEqual([h], out.get("hedge_legs_longer"))
        self.assertEqual(self.lifetime(p)[:2], self.lifetime(h)[:2])
        self.close()
        self.assertEqual(("PARTIAL", "WORKING"), (self.row(p)["status"], self.row(h)["status"]))
        self.assertEqual([], self.items())
        self.rt.clock.set("2026-10-21T10:00:00-04:00")
        print_acme(self.rt, "98.90", 10, self.rt.clock.epoch() - 60, 50000)
        self.rt.matcher.on_quotes(["ACME"])
        self.assertEqual(("FILLED", "WORKING", 3), (self.row(p)["status"], self.row(h)["status"], int(self.row(h)["qty"])))
        assert_integrity(self, self.rt, "full")

    def test_y_a_leg_reopened_by_a_later_days_fill_takes_its_shares_orders_lifetime(self):
        """W3 (a put; seed 14: a sold call): DAY package of MARKET options, 100 shares and 1 option filled on Tuesday (the leg FILLED 1/1); the
        shares order kept working as GTC (amend, or a re-price by replace); Wednesday's fill of the other 200 re-opens the leg with its shares
        order's GTC lifetime - never Tuesday's passed expiry - and the 2 more are bought / sold on a fresh chain."""
        done = False
        for how, call in (("amend", False), ("replace", False), ("amend", True)):
            with self.subTest(how=how, call=call):
                if done:
                    self.tearDown()
                    self.setUp()
                done = True
                opt = self.CALL if call else self.PUT
                p, h = self.released("DAY", put_limit=None, call=call)
                self.options()
                self.assertEqual(("FILLED", 1), (self.row(h)["status"], int(self.row(h)["filled_qty"])))
                head, _out = self.change(p, how, {"tif": "GTC"}, f"ui:rv6-reopen-{how[:2]}-{int(call)}")
                self.close()
                self.rt.clock.set("2026-10-21T10:00:00-04:00")
                print_acme(self.rt, "98.90", 10, self.rt.clock.epoch() - 60, 50000)
                self.rt.matcher.on_quotes(["ACME"])
                r = self.row(h)
                self.assertEqual(("PARTIAL", 3, 1, "GTC"), (r["status"], int(r["qty"]), int(r["filled_qty"]), r["tif"]))
                self.assertGreater(int(r["expires_ts"]), self.rt.clock.epoch())
                self.assertTrue(any("it now lives as long as its shares order (GTC)" in e["note"] for e in self.q("SELECT note FROM order_events WHERE order_id=?", (h,))))
                self.options()
                self.assertEqual(("FILLED", 3), (self.row(h)["status"], int(self.row(h)["filled_qty"])))
                self.assertEqual({"ACME": 300, opt: -3 if call else 3}, self.positions())
                assert_integrity(self, self.rt, "full")

    def test_z_a_released_leg_that_expires_unfilled_after_its_shares_order_ended_is_said(self):
        """DAY package; the shares fill in full and release 3 puts resting at 1.00; at the close the puts expire unfilled: one INFO item
        names them and says the shares bought are not hedged by them (the shares order has ended: filled)."""
        p, h = self.package("DAY")
        self.bar(50000)
        self.assertEqual(("FILLED", "WORKING", 3), (self.row(p)["status"], self.row(h)["status"], int(self.row(h)["qty"])))
        self.close()
        self.assertEqual("EXPIRED", self.row(h)["status"])
        (msg,) = self.items()
        for s in (h, "expired", "3 of 3 not filled", p, "has ended (filled)"):
            self.assertIn(s, msg)
        assert_integrity(self, self.rt, "full")


class CoveredSharesPledgedTests(SmartSubmitBase):
    """1.0.7.2 (simplified; the sixth review's cases): a covered call or a collar's call is read against the shares held free at the reading
    (less what working sells, calls sold or being sold and lent shares take), and its card fills a standard ticket with ``covered:
    true``; the order route places it only while those shares are still free (409 NOT_COVERED, nothing placed) and keeps them for it
    until it is closed - so the same shares never cover two calls.  The book holds 300 ACME (bought first)."""
    C105, C110, P95 = acme("C", 105), acme("C", 110), acme("P", 95)
    row = ReleaseAfterAmendOrReplaceTests.row
    positions = ReleaseAfterAmendOrReplaceTests.positions

    def setUp(self):
        super().setUp()
        st, out, _h = self.send({"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 300, "order_type": "MARKET", "tif": "DAY"}, "ui:cover-hold-300-01")
        self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)

    def send_leg(self, req: dict, rid: str) -> str:
        st, out = self.ticket(req, rid)
        self.assertEqual(200, st, out)
        return out["order"]["order_id"]

    @staticmethod
    def flag_text(l: dict, code: str) -> str:
        return next((f["text"] for f in l["flags"] if f["code"] == code), "")

    @staticmethod
    def call_leg(out: dict, occ: str) -> dict:
        return next(l for l in out["legs"] if (l.get("instrument") or {}).get("id") == occ)

    def resting_call(self, n: int = 3) -> str:
        first = self.parse(f"covered call {n * 100} ACME Dec 18 2026 105 limit 2.70")          # above the 2.60 bid: it rests
        (req,) = [l["request"] for l in first["legs"] if l.get("request")]
        self.assertTrue(req.get("covered"), req)
        oid = self.send_leg(req, "ui:cover-rest-0001")
        self.assertEqual(("WORKING", "STO", n), (self.row(oid)["status"], self.row(oid)["side"], int(self.row(oid)["qty"])))
        return oid

    def test_ad_two_covered_calls_in_one_instruction_never_share_their_cover(self):
        """Each covered call is read against the 300 ACME held free at the reading - the reading counts no other order of the instruction,
        and both cards say another order sent first may take the shares; sent in the order typed, the first is placed and sold, the
        second is refused 409 NOT_COVERED: the 300 ACME cover one set of calls, never two."""
        out = self.parse("covered call 300 ACME Dec 18 2026 105, covered call 300 ACME Dec 18 2026 110")
        a, b = out["legs"]
        self.assertEqual([(self.C105, "STO", 3, True), (self.C110, "STO", 3, True)],
                         [(l["request"]["instrument_id"], l["request"]["side"], l["request"]["qty"], l["request"].get("covered")) for l in (a, b)])
        for l in (a, b):
            self.assertIn("the calls only, no shares bought", self.flag_text(l, "SIDE_INFERRED"))
            self.assertIn("another order you send first may take them, and FinSim then refuses", self.flag_text(l, "SIDE_INFERRED"))
        oid = self.send_leg(a["request"], "ui:cover-ad-0001")
        self.rt.clock.advance(30)
        load_chain(self.rt, acme_chain(self.rt.clock.epoch()))
        self.rt.matcher.on_quotes([self.C105])
        self.assertEqual(("FILLED", {"ACME": 300, self.C105: -3}), (self.row(oid)["status"], self.positions()))
        st, res = self.ticket(b["request"], "ui:cover-ad-0002")
        self.assertEqual((409, "NOT_COVERED"), (st, res["error"]["code"]), res)
        self.assertIn("the calls sold as covered need 300 ACME held free in T1:US_EXCHANGE as their cover and 0 are free now (held 300; 300 covering"
                      " calls already sold)", res["error"]["message"])
        self.assertEqual({"ACME": 300, self.C105: -3}, self.positions())
        assert_integrity(self, self.rt, "full")

    def test_ae_a_working_covered_call_holds_its_cover_for_the_next_ticket(self):
        cc = self.resting_call(3)
        out = self.parse("covered call 300 ACME Dec 18 2026 110")
        l = self.call_leg(out, self.C110)
        self.assertTrue(all(x["request"] is None for x in out["legs"]), out["legs"])
        self.assertIn("300 covering calls being sold (working orders)", self.flag_text(l, "SIDE_SPLIT"))
        out = self.parse("collar 300 ACME Dec 18 2026 95/110")                                  # the collar's sold call too
        self.assertTrue(all(l["request"] is None for l in out["legs"]), out["legs"])
        self.assertTrue(all("SIDE_SPLIT" in [f["code"] for f in l["flags"]] for l in out["legs"]))
        self.assertFalse(any("the put and the call only" in f["text"] for l in out["legs"] for f in l["flags"]))
        # a card filled before the call was placed (or the same ticket typed by hand) is refused at the route: the shares stay with it
        st, res = self.ticket({"account_id": ACCT, "instrument_id": self.C110, "side": "STO", "qty": 3, "order_type": "MARKET", "tif": "DAY", "covered": True},
                              "ui:cover-ae-0001")
        self.assertEqual((409, "NOT_COVERED"), (st, res["error"]["code"]), res)
        self.assertIn("(held 300; 300 covering calls being sold)", res["error"]["message"])
        self.assertEqual(("WORKING", {"ACME": 300}), (self.row(cc)["status"], self.positions()))
        assert_integrity(self, self.rt, "full")

    def test_af_what_a_working_call_leaves_free_is_offered_and_a_filled_one_is_reserved(self):
        self.resting_call(1)                                                                     # 100 of the 300 shares pledged
        out = self.parse("covered call 300 ACME Dec 18 2026 110")
        f = next(f for f in self.call_leg(out, self.C110)["flags"] if f["code"] == "SIDE_SPLIT")
        self.assertIn("200 of the 300 ACME held", f["text"])
        self.assertIn("covered call on the 200 held free", [c["label"] for c in f.get("choices") or []])
        out = self.parse("covered call 200 ACME Dec 18 2026 110")
        (l,) = out["legs"]
        self.assertEqual(("STO", 2), (l["request"]["side"], l["request"]["qty"]))
        # control: once the resting call has filled, the calls already sold reserve their shares
        self.rt.clock.advance(30)
        snap = acme_chain(self.rt.clock.epoch())
        for r in snap.contracts:
            if r.occ == self.C105:
                r.bid, r.ask = qprice("2.70"), qprice("2.80")
        load_chain(self.rt, snap)
        self.rt.matcher.on_quotes([self.C105])
        self.assertEqual({"ACME": 300, self.C105: -1}, self.positions())
        out = self.parse("covered call 300 ACME Dec 18 2026 110")
        self.assertTrue(all(x["request"] is None for x in out["legs"]), out["legs"])
        self.assertIn("200 of the 300 ACME held", self.flag_text(self.call_leg(out, self.C110), "SIDE_SPLIT"))
        assert_integrity(self, self.rt, "full")


class CoverCountedOnceTests(SmartSubmitBase):
    """1.0.7.2 (simplified; the seventh review's cases).  The book holds 300 ACME (bought first).  Each order of an instruction is read
    against the shares as they are at the reading, and every card's ticket is sent through the order route as the ticket sends it, in
    the order shown (a group's tickets in its order).  The route keeps the end state - never a call sold as covered without its shares:
    a call claimed after a sale of its shares, after plain calls on them or after another claimed call on them is 409 NOT_COVERED; a sale
    after a resting claimed call is 400 OVERSELL (SHARES_RESERVED); a plain call sold to open after a claimed one on the same shares is
    409 SHARES_RESERVED (INV-9), also with an earlier expiry or a lower strike, the calls the margin would otherwise cover first.  After
    each instruction the 105 call is bid 2.70 (a covered call resting at 2.70 fills)."""
    C105, C110, P95 = acme("C", 105), acme("C", 110), acme("P", 95)
    row = ReleaseAfterAmendOrReplaceTests.row
    positions = ReleaseAfterAmendOrReplaceTests.positions

    def setUp(self):
        super().setUp()
        self.k = 0
        self.hold(300)

    def hold(self, n: int) -> None:
        self.k += 1
        st, out, _h = self.send({"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": n, "order_type": "MARKET", "tif": "DAY"}, f"ui:cover7-hold-{self.k:03d}")
        self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)

    @staticmethod
    def shown(out: dict) -> list:
        """The cards' order: the legs as typed, a group's legs in its own order."""
        groups = {c["id"]: c for c in out["combos"]}
        seen, order = set(), []
        for l in out["legs"]:
            for n in (groups[l["combo"]]["order"] if l.get("combo") else [l["n"]]):
                if n not in seen:
                    seen.add(n)
                    order.append(n)
        return order

    def run_tickets(self, out: dict) -> list:
        """Every card with a request, in the order shown, sent as the ticket sends it: (leg n, http status, order status or refusal code)."""
        by_n = {l["n"]: l for l in out["legs"]}
        got = []
        for n in self.shown(out):
            req = by_n[n].get("request")
            if req is None:
                continue
            self.k += 1
            st, res = self.ticket(req, f"ui:cover7-ticket-{self.k:03d}")
            got.append((n, st, (res.get("order") or {}).get("status") if st == 200 else (res.get("error") or {}).get("code")))
        return got

    def options_move(self) -> None:
        """The next chain observation: the 105 call bid 2.70 / ask 2.80 (a covered call resting at its 2.70 limit fills)."""
        self.rt.clock.advance(30)
        snap = acme_chain(self.rt.clock.epoch())
        for r in snap.contracts:
            if r.occ == self.C105:
                r.bid, r.ask = qprice("2.70"), qprice("2.80")
        load_chain(self.rt, snap)
        self.rt.matcher.on_quotes([self.C105, self.C110, self.P95])

    def uncovered(self) -> int:
        """The ACME the book's short ACME calls need beyond the ACME it holds (0: every short call is covered)."""
        pos = self.positions()
        short_calls = -sum(q for iid, q in pos.items() if iid.startswith("ACME2") and iid[10] == "C" and q < 0)
        return max(0, 100 * short_calls - max(0, pos.get("ACME", 0)))

    @staticmethod
    def blockers(out: dict) -> list:
        return [(l["n"], f["code"]) for l in out["legs"] for f in l["flags"] if f["severity"] == "blocker"]

    def check(self, text: str, sent: list) -> dict:
        out = self.parse(text)
        self.assertEqual([], self.blockers(out), json.dumps([(l["n"], [f["text"] for f in l["flags"] if f["severity"] == "blocker"]) for l in out["legs"]]))
        got = self.run_tickets(out)
        self.assertEqual(sent, got, got)
        self.options_move()
        self.assertEqual(0, self.uncovered(), self.positions())
        assert_integrity(self, self.rt, "full")
        return out

    def margin_rules(self) -> dict:
        """The margin engine's reading of each short ACME call (covered_call / naked_call), from the stored margin detail."""
        rows = self.q("SELECT options_detail_json FROM margin_state WHERE account_id=?", (ACCT,))
        d = json.loads(rows[0]["options_detail_json"]) if rows else {}
        return {it["instrument_id"]: it["rule"] for g in d.get("groups") or [] for it in g.get("items") or [] if it["instrument_id"].startswith("ACME2")
                and it["instrument_id"][10] == "C"}

    def test_ag_a_sale_before_a_covered_call_or_collar_leaves_it_no_free_shares(self):
        self.check("sell 300 ACME, covered call 300 ACME Dec 18 2026 105", [(1, 200, "FILLED"), (2, 409, "NOT_COVERED")])
        self.assertEqual({}, self.positions())

    def test_ah_a_sale_before_a_collar(self):
        # the collar's put ticket is an ordinary purchase (sent, bought at the 2.25 ask); its call, sold as covered, finds no shares
        self.check("sell 300 ACME, collar 300 ACME Dec 18 2026 95/110", [(1, 200, "FILLED"), (2, 200, "FILLED"), (3, 409, "NOT_COVERED")])
        self.assertEqual({self.P95: 3}, self.positions())

    def test_ai_a_sale_after_a_covered_call_resting_at_its_limit_is_refused(self):
        self.check("covered call 300 ACME Dec 18 2026 105 limit 2.70, sell 300 ACME", [(1, 200, "WORKING"), (2, 400, "OVERSELL")])
        self.assertEqual({"ACME": 300, self.C105: -3}, self.positions())          # the calls filled at 2.70, covered

    def test_aj_calls_sold_to_open_before_a_covered_call_take_its_shares(self):
        self.check("sell 3 ACME Dec 18 2026 105 calls, covered call 300 ACME Dec 18 2026 110", [(1, 200, "FILLED"), (2, 409, "NOT_COVERED")])
        self.assertEqual({"ACME": 300, self.C105: -3}, self.positions())

    def test_ak_calls_sold_to_open_after_a_covered_call_on_the_same_shares_are_refused(self):
        self.check("covered call 300 ACME Dec 18 2026 105, sto 3 ACME Dec 18 2026 110 calls", [(1, 200, "FILLED"), (2, 409, "SHARES_RESERVED")])
        self.assertEqual({"ACME": 300, self.C105: -3}, self.positions())
        # a lower strike in the same expiry: the call the margin would cover first
        self.check("sto 1 ACME Dec 18 2026 100 call", [(1, 409, "SHARES_RESERVED")])
        # an earlier expiry than the claimed calls (a new world: the calls sold as covered expire on Jan 15 2027)
        self.tearDown()
        self.setUp()
        self.check("covered call 300 ACME Jan 15 2027 105, sto 3 ACME Dec 18 2026 110 calls", [(1, 200, "FILLED"), (2, 409, "SHARES_RESERVED")])
        # with free shares the same calls are accepted, and the margin then covers every call, the claimed ones included
        self.hold(300)
        self.check("sto 3 ACME Dec 18 2026 110 calls", [(1, 200, "FILLED")])
        jan = acme("C", 105, E2)
        self.assertEqual({"ACME": 600, jan: -3, self.C110: -3}, self.positions())
        self.assertEqual({jan: "covered_call", self.C110: "covered_call"}, self.margin_rules())

    def test_al_grouped_with_a_sale_of_its_shares_nothing_goes_uncovered(self):
        # a group's tickets go in its order: the call sold to open before the sale; the sale then finds its shares covering the calls
        self.check("(covered call 300 ACME Dec 18 2026 105, sell 300 ACME)", [(1, 200, "FILLED"), (2, 400, "OVERSELL")])
        self.assertEqual({"ACME": 300, self.C105: -3}, self.positions())
        # a partial sale after the covered call: what is left free only (0 here), never the shares the calls need
        self.tearDown()
        self.setUp()
        self.check("covered call 300 ACME Dec 18 2026 105 then sell 100 ACME", [(1, 200, "FILLED"), (2, 400, "OVERSELL")])
        self.assertEqual({"ACME": 300, self.C105: -3}, self.positions())

    def test_am_a_covered_call_or_a_collars_call_ticket_is_placed_only_while_its_shares_are_free(self):
        call = [l["request"] for l in self.parse("covered call 300 ACME Dec 18 2026 105")["legs"] if l.get("request")][0]
        put, call2 = [l["request"] for l in self.parse("collar 300 ACME Dec 18 2026 95/110")["legs"]]
        self.assertEqual((True, None, True), (call.get("covered"), put.get("covered"), call2.get("covered")))
        # before the owner sends them, the 300 ACME are sold (the docked ticket, another tab): an ordinary sale
        st, res, _h = self.send({"account_id": ACCT, "instrument_id": "ACME", "side": "SELL", "qty": 300, "order_type": "MARKET", "tif": "DAY"}, "ui:cover7-sell-300-01")
        self.assertEqual((200, "FILLED"), (st, res["order"]["status"]), res)
        st, res = self.ticket(call, "ui:cover7-am-call-01")
        self.assertEqual((409, "NOT_COVERED"), (st, res["error"]["code"]), res)
        self.assertIn("the calls sold as covered need 300 ACME held free in T1:US_EXCHANGE as their cover and 0 are free now", res["error"]["message"])
        st, res = self.ticket(call2, "ui:cover7-am-collar-01")
        self.assertEqual((409, "NOT_COVERED"), (st, res["error"]["code"]), res)
        self.assertEqual([], self.q("SELECT order_id FROM orders WHERE request_id LIKE 'ui:cover7-am-%'"))
        # the shares bought back: the same request id places it (the refusal stored nothing)
        self.hold(300)
        st, res = self.ticket(call, "ui:cover7-am-call-01")
        self.assertEqual((200, "FILLED"), (st, res["order"]["status"]), res)
        # ... and once those calls hold the shares, the collar's call ticket is refused (the calls already sold reserve them)
        st, res = self.ticket(call2, "ui:cover7-am-collar-02")
        self.assertEqual((409, "NOT_COVERED"), (st, res["error"]["code"]), res)
        self.assertEqual({"ACME": 300, self.C105: -3}, self.positions())
        # covered: true is for calls sold to open on shares held; a package that buys its shares brings its own cover
        st, res, _h = self.send({"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 1, "order_type": "MARKET", "tif": "DAY", "covered": True},
                                "ui:cover7-bad-0001")
        self.assertEqual((400, "COVERED_INVALID"), (st, res["error"]["code"]), res)
        st, res = self.template_package("COVERED_CALL", 100, {1: 110}, rid="ui:cover7-bad-0002", covered=True)
        self.assertEqual((400, "COVERED_INVALID"), (st, res["error"]["code"]), res)
        assert_integrity(self, self.rt, "full")


class RepricedLegBatchTests(SmartSubmitBase):
    """1.0.7.2 seventh review: the batch Pending now sends after a shares order was re-priced by a replace (the put released before the
    re-price stays with the replaced order: Pending reads its primary over the replace chain).  BUY 300 ACME limit 99 + BTO 3 Dec 18 2026
    95 puts limit 1.00; a 1,000-share bar fills 100 and releases 1 put (resting at 1.00); the rest of the shares is re-priced."""
    PUT = acme("P", 95)
    bar = ReleaseAfterAmendOrReplaceTests.bar
    row = ReleaseAfterAmendOrReplaceTests.row

    def package(self) -> tuple:
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 300, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        st, out, p, (h,) = self.seq_package(primary, [leg(self.PUT, "BTO", 3, order_type="LIMIT", limit_px="1.00")], rid="ui:seq-repriced-0001")
        self.assertEqual(200, st, out)
        p, h = p["order_id"], h["order_id"]
        self.bar(1000)
        self.assertEqual((100, "WORKING", 1), (int(self.row(p)["filled_qty"]), self.row(h)["status"], int(self.row(h)["qty"])))
        st, out, _h = self.call("POST", f"/api/orders/{p}/replace?book=T", {"limit_px": "99.00"}, "ui:repriced7-0001")
        self.assertEqual(200, st, out)
        return p, h, out["order"]["order_id"]

    def batch(self, ids: list, rid: str) -> dict:
        st, out, _h = self.call("POST", "/api/orders/cancel?book=T", {"order_ids": ids, "reason": "cancelled by the owner (Pending Trades)"}, rid)
        self.assertEqual(200, st, out)
        return out

    def test_aq_the_put_ticked_with_the_replacement_goes_before_it_and_is_cancelled(self):
        p, h, p2 = self.package()
        out = self.batch([h, p2], "mc:rp7tick00001")                        # what Pending sends: the ticked leg before the head of its chain
        self.assertEqual(("CANCELLED", "CANCELLED"), (self.row(h)["status"], self.row(p2)["status"]), out)
        self.assertEqual({h, p2}, {x["order_id"] for x in out["cancelled"]})
        assert_integrity(self, self.rt, "full")

    def test_ar_the_replacement_alone_keeps_the_put_and_names_the_order_that_bought_the_shares(self):
        p, h, p2 = self.package()
        out = self.batch([p2], "mc:rp7tick00002")
        (k,) = out["kept"]
        self.assertEqual((h, "RELEASED"), (k["order_id"], k["code"]))
        self.assertIn(f"released for the 100 shares bought by {p} (its primary, re-priced as {p2}) before {p2} was cancelled", k["message"])
        self.assertEqual("WORKING", self.row(h)["status"])
        assert_integrity(self, self.rt, "full")

    def test_as_the_put_ticked_alone_is_kept_while_the_replacement_works(self):
        p, h, p2 = self.package()
        out = self.batch([h], "mc:rp7tick00003")
        (k,) = out["kept"]
        self.assertEqual((h, "PRIMARY_WORKING", p2), (k["order_id"], k["code"], k["parent_order_id"]))
        self.assertIn(f"kept: its primary {p2} keeps working", k["message"])
        assert_integrity(self, self.rt, "full")


# ===== the sixth review's lifetime walk, replayed for the seeds it reported =================================================================
#: the walks of the sixth review's property test (sequential packages over several sessions: share fills, option fills, amends of the
#: quantity or the time in force, re-prices by replace, the owner's own mod of a released leg, the x on a shares order or on a leg, session
#: closes), seed for seed: W6 at 117, 544, 629 (a released leg shortened by its shares order's DAY, then expired at the close) and W3 at 10,
#: 14, 49, 75, 91, 92, 113 of the walk that keeps the shares working past the close (a leg re-opened with a passed expiry, never able to fill)
LIFE_SEEDS = ((False, (117, 544, 629)), (True, (10, 14, 49, 75, 91, 92, 113)))
_LIFE_LIVE = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
_LIFE_DAYS = ["2026-10-20", "2026-10-21", "2026-10-22"]           # Tue, Wed, Thu (NYSE sessions)


class _LifeWorld:
    CALL, PUT = acme("C", 105), acme("P", 95)

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-smartsub-life-")
        self.rt = build(self.tmp)
        add_name(self.rt, "ACME")
        now = self.rt.clock.epoch()
        print_acme(self.rt, "100.00", 120, now - 180, 50000)
        load_chain(self.rt, acme_chain(now))
        self.day = 0
        self.n = self.k = 0
        self.legs = {}               # leg id -> (L0, P0, side, instrument)
        self.life = {}               # leg id -> the expires_ts it had when released (or the owner's own mod gave it)
        self.own_cancel = set()      # legs the owner cancelled with their own x

    def close(self):
        try:
            self.rt.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None, rid=None):
        b = dict(body or {})
        if rid is not None:
            b["request_id"] = rid
        return self.rt.router.handle(method, path, None, b, "127.0.0.1", None)

    def q(self, sql, params=()):
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def row(self, oid):
        r = self.q("SELECT * FROM orders WHERE order_id=?", (oid,))
        return r[0] if r else None

    def rid(self, what):
        self.k += 1
        return f"ui:prop6-{what}-{self.k:06d}"

    def bar(self, vol):
        self.rt.clock.advance(60)
        print_acme(self.rt, "98.90", 10, self.rt.clock.epoch() - 60, vol)
        self.rt.matcher.on_quotes(["ACME"])

    def options(self):
        self.rt.clock.advance(30)
        load_chain(self.rt, acme_chain(self.rt.clock.epoch()))
        self.rt.matcher.on_quotes([self.CALL, self.PUT])

    def close_day(self):
        if self.day + 1 >= len(_LIFE_DAYS):
            return False
        self.rt.lifecycle.tick(at=f"{_LIFE_DAYS[self.day]}T16:00:01-04:00", only="EXPIRE_ORDERS")
        self.day += 1
        self.rt.clock.set(f"{_LIFE_DAYS[self.day]}T10:00:00-04:00")
        now = self.rt.clock.epoch()
        print_acme(self.rt, "100.00", 30, now - 90, 50000)     # the open: above the 99.00 limits, nothing fills on it
        load_chain(self.rt, acme_chain(now))
        return True

    def package(self, p0, legs, tif):
        """A shares order BUY ``p0`` ACME limit 99.00 with its hedge's own ``legs``, as the order ticket places it."""
        self.n += 1
        rid = f"ui:prop6-pkg-{self.n:06d}"
        body = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": p0, "order_type": "LIMIT", "limit_px": "99.00", "tif": tif,
                "hedge": {"legs": legs}}
        st, out, _h = self.call("POST", "/api/orders?book=T", body, rid)
        assert st == 200, (st, out)
        for i, lg in enumerate(legs, 1):
            oid = self.q("SELECT order_id FROM orders WHERE request_id=?", (f"{rid}:H{i}",))[0]["order_id"]
            self.legs[oid] = (int(lg["qty"]), p0, lg["side"], lg["instrument_id"])

    def primaries(self):
        return [r["order_id"] for r in self.q("SELECT order_id FROM orders WHERE parent_order_id IS NULL AND package_id IS NOT NULL AND instrument_id='ACME'"
                                                " AND status IN ('WORKING','PARTIAL','SUSPENDED') ORDER BY order_id")]

    def released(self):
        return [oid for oid in self.legs if (self.row(oid) or {}).get("status") in ("WORKING", "PARTIAL")]

    def head_of(self, leg_id):
        from finsim.terminal.api_products import replace_chain
        r = self.row(leg_id)
        with self.rt.db.reader() as conn:
            chain = replace_chain(conn, r["parent_order_id"], forward=True)
        rows = [self.row(x) for x in chain]
        return rows[0], sum(int(x["filled_qty"] or 0) for x in rows)

    def positions(self):
        return {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}


def _life_check(w, where, before):
    """W1 no orphan; W2 sizing floor(S x L0 / P0); W3 a released leg with quantity left is never past its own expiry while the session is
    open; W4 no short call beyond the shares / 100; W5 a waiting leg never expires while its shares order works; W6 a released leg never
    ends before the lifetime it had when released (or the owner's own mod gave it) unless the owner cancelled it."""
    bad = []
    now = w.rt.clock.epoch()
    for oid, (l0, p0, side, _inst) in w.legs.items():
        r = w.row(oid)
        st = r["status"]
        was = before.get(oid)
        head, s = w.head_of(oid)
        target = s * l0 // p0
        if st == "WAITING_PARENT":
            if head["status"] not in _LIFE_LIVE:
                bad.append(f"{where}: W1 {oid} waits for {head['order_id']} which is {head['status']}")
            if target > 0:
                bad.append(f"{where}: W2 {oid} still waits although floor({s} x {l0} / {p0}) = {target}")
        elif st in ("WORKING", "PARTIAL", "FILLED") and target > 0 and int(r["qty"]) != target:
            bad.append(f"{where}: W2 {oid} ({side}) sized {r['qty']} but floor({s} x {l0} / {p0}) = {target}")
        if st in ("WORKING", "PARTIAL") and int(r["qty"]) > int(r["filled_qty"]) and int(r["expires_ts"]) < now:
            bad.append(f"{where}: W3 {oid} ({side}) has {int(r['qty']) - int(r['filled_qty'])} left to fill but its expiry passed ({r['tif']})")
        if st in ("WORKING", "PARTIAL", "FILLED") and oid not in w.life:
            w.life[oid] = int(r["expires_ts"])
        if was in _LIFE_LIVE and st == "EXPIRED":
            if was == "WAITING_PARENT" and head["status"] in _LIFE_LIVE:
                bad.append(f"{where}: W5 the waiting leg {oid} expired while its shares order {head['order_id']} works ({head['status']})")
            if was in ("WORKING", "PARTIAL") and oid in w.life and now <= w.life[oid] and oid not in w.own_cancel:
                bad.append(f"{where}: W6 the released leg {oid} ({side}) expired before the lifetime it was released with; its tif now {r['tif']}")
        if was in ("WORKING", "PARTIAL") and st == "CANCELLED" and oid not in w.own_cancel:
            bad.append(f"{where}: W6 the released leg {oid} ({side}) was cancelled without the owner's own cancel: {r['reason']}")
    pos = w.positions()
    if -pos.get(w.CALL, 0) * 100 > pos.get("ACME", 0):
        bad.append(f"{where}: W4 {-pos.get(w.CALL, 0)} short calls on {pos.get('ACME', 0)} shares")
    return bad


def _life_scenario(seed: int, biased: bool = False):
    """One walk of the sixth review's lifetime property test, step for step as it drew them from ``random.Random(seed)``."""
    import random
    rng = random.Random(seed)
    w = _LifeWorld()
    bad, steps = [], []
    try:
        for _ in range(rng.choice([1, 1, 2])):
            p0 = rng.choice([100, 200, 300, 400])
            legs = []
            if rng.random() < 0.6:
                kind = "MARKET" if biased else rng.choice(["LIMIT", "MARKET"])
                legs.append(dict({"instrument_id": w.CALL, "side": "STO", "qty": rng.randint(1, p0 // 100), "order_type": kind},
                                 **({"limit_px": "9.00"} if kind == "LIMIT" else {})))
            if rng.random() < 0.7 or not legs:
                kind = "MARKET" if biased else rng.choice(["LIMIT", "MARKET"])
                legs.append(dict({"instrument_id": w.PUT, "side": "BTO", "qty": rng.randint(1, p0 // 100), "order_type": kind},
                                 **({"limit_px": "1.00"} if kind == "LIMIT" else {})))
            tif = "DAY" if biased else rng.choice(["DAY", "GTC"])
            w.package(p0, legs, tif)
            steps.append(f"package {p0} {tif} {[(l['side'], l['qty'], l['order_type']) for l in legs]}")
        if biased:
            plan = ["bar_part", "options", rng.choice(["amend_tif", "replace"]), "close", "bar", "options"]
            for _ in range(rng.randint(0, 3)):
                plan.insert(rng.randint(0, len(plan)), rng.choice(["bar_part", "options", "close", "amend_tif", "replace"]))
        else:
            plan = [None] * rng.randint(3, 12)
        for step, op in enumerate(plan):
            before = {oid: (w.row(oid) or {}).get("status") for oid in w.legs}
            live = w.primaries()
            if op is None:
                op = rng.choice(["bar", "bar", "bar", "options", "options", "amend_qty", "amend_tif", "replace", "leg_tif", "x_primary", "x_leg", "close", "close"])
            if op in ("bar", "bar_part"):
                vol = rng.choice([300, 600, 1000, 2000] if op == "bar_part" else [300, 600, 1000, 2000, 50000])
                op = "bar"
                w.bar(vol)
                op += f" {vol}"
            elif op == "options":
                w.options()
            elif op == "amend_qty" and live:
                oid = rng.choice(live)
                r = w.row(oid)
                qty = int(r["filled_qty"]) + rng.choice([1, 50, 100, 200])
                st, _out, _h = w.call("PATCH", f"/api/orders/{oid}?book=T", {"qty": qty}, w.rid("aq"))
                op += f" {oid} {qty} -> {st}"
            elif op == "amend_tif" and live:
                oid = rng.choice(live)
                tif = "GTC" if biased else rng.choice(["DAY", "GTC"])
                st, _out, _h = w.call("PATCH", f"/api/orders/{oid}?book=T", {"tif": tif}, w.rid("at"))
                op += f" {oid} {tif} -> {st}"
            elif op == "replace" and live:
                oid = rng.choice(live)
                r = w.row(oid)
                rem = int(r["qty"]) - int(r["filled_qty"])
                body = {"limit_px": rng.choice(["99.00", "99.05"]), "tif": "GTC" if biased else rng.choice([r["tif"], r["tif"], "DAY", "GTC"]),
                        "qty": rng.choice([rem, rem, max(1, rem - 50)])}
                st, _out, _h = w.call("POST", f"/api/orders/{oid}/replace?book=T", body, w.rid("rp"))
                op += f" {oid} {body} -> {st}"
            elif op == "leg_tif":
                rel = w.released()
                if rel:
                    oid = rng.choice(rel)
                    tif = rng.choice(["DAY", "GTC"])
                    st, _out, _h = w.call("PATCH", f"/api/orders/{oid}?book=T", {"tif": tif}, w.rid("lt"))
                    if st == 200:
                        w.life[oid] = int(w.row(oid)["expires_ts"])
                    op += f" {oid} {tif} -> {st}"
            elif op == "x_primary" and live:
                oid = rng.choice(live)
                st, _out, _h = w.call("DELETE", f"/api/orders/{oid}?book=T", {}, w.rid("xp"))
                op += f" {oid} -> {st}"
            elif op == "x_leg":
                opn = [oid for oid in w.legs if (w.row(oid) or {}).get("status") in _LIFE_LIVE]
                if opn:
                    oid = rng.choice(opn)
                    st, _out, _h = w.call("DELETE", f"/api/orders/{oid}?book=T", {}, w.rid("xl"))
                    if st == 200:
                        w.own_cancel.add(oid)
                    op += f" {oid} -> {st}"
            elif op == "close":
                if not w.close_day():
                    op += " (last day)"
            steps.append(op)
            got = _life_check(w, f"seed {seed} step {step} [{op}]", before)
            if got:
                bad += got
                break
        rep = w.rt.accounting.integrity("full")
        if not rep.ok:
            bad.append(f"seed {seed}: integrity {[p.to_dict() for p in rep.problems][:3]}")
    finally:
        w.close()
    return bad, steps


class LifetimeSeedTests(unittest.TestCase):
    def test_ac_the_lifetime_walks_the_sixth_review_reported_keep_every_leg_able_to_work(self):
        failures = {}
        for biased, seeds in LIFE_SEEDS:
            for seed in seeds:
                got, steps = _life_scenario(seed, biased)
                if got:
                    failures[(seed, biased)] = (got[:2], steps)
        self.assertEqual({}, failures)


# ===== the covered-call invariant at the order route (SIMPLIFY_PLAN s5: INV-1 to INV-5, INV-9; the round 8 scenarios) ===================
class CoverReservedTests(SmartSubmitBase):
    """1.0.7.2 (simplified): a call sold as covered keeps its shares until it is closed, whichever ticket sends the orders.  The cover of
    every call sold or being sold on a name is reserved in whole contracts as FinSim's margin assigns it (the calls already sold first,
    then the working sell-to-open orders, each by expiry and strike), read from the positions at the check - never from the last margin
    run.  A sale, an amend or a replace of a sale, a short sale, an early put exercise or a loan never takes it (400 OVERSELL naming
    SHARES_RESERVED, 400 SHORT_WHILE_LONG, 409 SHARES_RESERVED); a covered call raised beyond its shares by an amend or a replace is 409
    NOT_COVERED; while calls sold as covered are open on a name, every further call sold to open on it needs free shares of its own
    (INV-9: 409 SHARES_RESERVED).  The round 8 reviews' scenarios are here, each named where it runs (money path cover_kept a-d,
    stale_reservation a-b, exercise a; send loop e-f; property tester cover a-e, g, i).  ACME is borrowable in this class (an average
    daily volume of 5,000,000 in the master)."""
    C100, C105, C110, C120, P95 = acme("C", 100), acme("C", 105), acme("C", 110), acme("C", 120), acme("P", 95)
    C105J = acme("C", 105, E2)
    row = ReleaseAfterAmendOrReplaceTests.row
    positions = ReleaseAfterAmendOrReplaceTests.positions

    def setUp(self):
        super().setUp()
        with self.rt.db.writer() as tx:
            self.rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME test listing", currency="USD", session_group="US_EQUITY",
                                       account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME", spec={"exchange": "NYQ", "adv": 5000000})
        self.k = 0

    def rid(self) -> str:
        self.k += 1
        return f"ui:cover-res-{self.k:04d}"

    def order(self, iid: str, side: str, qty: int, **kw) -> tuple:
        """An ordinary order from another ticket (the docked ticket, the instrument page, another tab): (http status, order status or
        refusal code, refusal message, answer)."""
        st, out, _h = self.send(dict({"account_id": ACCT, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY"}, **kw), self.rid())
        return st, (out.get("order") or {}).get("status") if st == 200 else (out.get("error") or {}).get("code"), (out.get("error") or {}).get("message", ""), out

    def hold(self, n: int) -> None:
        st, status, _m, out = self.order("ACME", "BUY", n)
        self.assertEqual((200, "FILLED"), (st, status), out)

    def covered(self, occ: str, n: int, limit: Optional[str] = None) -> tuple:
        """Calls sold as covered as a Smart Ticket card fills the ticket (``covered: true``) and the ticket sends them: (http status, order
        status or refusal code, order id, answer)."""
        req = dict({"account_id": ACCT, "instrument_id": occ, "side": "STO", "qty": n, "order_type": "LIMIT" if limit else "MARKET", "tif": "DAY",
                    "covered": True}, **({"limit_px": limit} if limit else {}))
        st, out = self.ticket(req, self.rid())
        return st, (out.get("order") or {}).get("status") if st == 200 else (out.get("error") or {}).get("code"), (out.get("order") or {}).get("order_id"), out

    def bid(self, occ: str, bid: str, ask: str) -> None:
        """The next chain observation with ``occ`` at bid / ask (a call resting at its limit fills); every ACME option is matched."""
        self.rt.clock.advance(30)
        snap = acme_chain(self.rt.clock.epoch())
        for r in snap.contracts:
            if r.occ == occ:
                r.bid, r.ask = qprice(bid), qprice(ask)
        load_chain(self.rt, snap)
        self.rt.matcher.on_quotes([r.occ for r in snap.contracts])

    def uncovered(self) -> int:
        """The ACME the book's short ACME calls need beyond the ACME it holds (0: every short call is covered)."""
        pos = self.positions()
        short = -sum(q for iid, q in pos.items() if iid.startswith("ACME2") and iid[10] == "C" and q < 0)
        return max(0, 100 * short - max(0, pos.get("ACME", 0)))

    def stored_reservation(self) -> dict:
        rows = self.q("SELECT options_detail_json FROM margin_state WHERE account_id=?", (ACCT,))
        return (json.loads(rows[0]["options_detail_json"]).get("shares_reserved") or {}) if rows else {}

    margin_rules = CoverCountedOnceTests.margin_rules

    def test_a_sale_of_shares_a_working_call_covers_is_refused(self):
        """Round 8 cover_kept a, send loop f, property tester cover i: a covered call resting at its limit keeps its 300 ACME - an ordinary
        sale of them from another ticket is refused, whole or in part.  Round 8 cover_kept b, send loop e: the Smart Ticket's sale read
        while the shares were free, its card sent after the covered call was placed (the ticket's own fresh preview), is refused too."""
        self.hold(300)
        (sale,) = [l["request"] for l in self.parse("sell 300 ACME")["legs"]]             # read while the 300 are free: a plain sale
        self.assertEqual(("SELL", 300), (sale["side"], sale["qty"]))
        st, status, cc, out = self.covered(self.C105, 3, "2.70")                            # above the 2.60 bid: it rests
        self.assertEqual((200, "WORKING"), (st, status), out)
        for qty in (300, 1):
            st, code, msg, out = self.order("ACME", "SELL", qty)
            self.assertEqual((400, "OVERSELL"), (st, code), out)
            self.assertIn(f"300 ACME cover calls being sold ({cc}): cancel or close them first (SHARES_RESERVED)", msg)
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(sale))            # the ticket's fresh preview says so
        self.assertEqual(["OVERSELL"], [b["code"] for b in pv["blockers"]])
        self.assertIn("(SHARES_RESERVED)", pv["blockers"][0]["message"])
        st, out = self.ticket(sale, "ui:cover-res-sale-01")
        self.assertEqual((400, "OVERSELL"), (st, out["error"]["code"]), out)
        out = self.parse("sell 300 ACME")                                                   # a reading now refuses it as well
        self.assertEqual([None], [l["request"] for l in out["legs"]])
        self.assertIn("300 covering calls being sold (working orders)", next(f["text"] for f in out["legs"][0]["flags"] if f["code"] == "WOULD_OVERSELL"))
        self.bid(self.C105, "2.70", "2.80")                                                 # the calls fill at their limit, covered
        self.assertEqual(("FILLED", {"ACME": 300, self.C105: -3}, 0), (self.row(cc)["status"], self.positions(), self.uncovered()))
        st, code, msg, out = self.order("ACME", "SELL", 300)                               # sold: the calls already sold keep them
        self.assertEqual((400, "OVERSELL"), (st, code), out)
        self.assertIn("cover calls already sold", msg)
        assert_integrity(self, self.rt, "full")

    def test_a_working_sale_raised_by_amend_or_replace_over_the_cover_is_refused(self):
        """Round 8 cover_kept c: a sale of 100 works at 120.00; a covered call takes the 200 it leaves free; the sale raised to 300 by an
        amend or by a re-price (replace) is refused and stays as it was; a change of its price alone is unchanged."""
        self.hold(300)
        st, status, _m, out = self.order("ACME", "SELL", 100, order_type="LIMIT", limit_px="120.00")
        self.assertEqual((200, "WORKING"), (st, status), out)
        sale = out["order"]["order_id"]
        st, status, _cc, out = self.covered(self.C105, 2, "2.70")
        self.assertEqual((200, "WORKING"), (st, status), out)
        st, out, _h = self.call("PATCH", f"/api/orders/{sale}?book=T", {"qty": 300, "limit_px": "100.00"}, "ui:cover-res-amend-01")
        self.assertEqual((400, "OVERSELL"), (st, out["error"]["code"]), out)
        self.assertIn("(SHARES_RESERVED)", out["error"]["message"])
        st, out, _h = self.call("POST", f"/api/orders/{sale}/replace?book=T", {"qty": 300, "order_type": "LIMIT", "limit_px": "100.00"}, "ui:cover-res-repl-01")
        self.assertEqual((400, "OVERSELL"), (st, out["error"]["code"]), out)
        r = self.row(sale)
        self.assertEqual(("WORKING", 100, D("120.00")), (r["status"], int(r["qty"]), D(r["limit_px"])))
        st, out, _h = self.call("PATCH", f"/api/orders/{sale}?book=T", {"limit_px": "119.00"}, "ui:cover-res-amend-02")
        self.assertEqual(200, st, out)
        self.bid(self.C105, "2.70", "2.80")
        self.assertEqual(({"ACME": 300, self.C105: -2}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_a_covered_call_amended_or_replaced_beyond_its_shares_is_not_covered(self):
        """Round 8 cover_kept d: a covered call of 1 contract on the 100 ACME held, amended or replaced to 5 contracts, is refused 409
        NOT_COVERED and stays as it was; with 500 ACME held the same amend is accepted and the 5 calls are covered."""
        self.hold(100)
        st, status, cc, out = self.covered(self.C105, 1, "2.70")
        self.assertEqual((200, "WORKING"), (st, status), out)
        st, out, _h = self.call("PATCH", f"/api/orders/{cc}?book=T", {"qty": 5}, "ui:cover-res-cc-amend-01")
        self.assertEqual((409, "NOT_COVERED"), (st, out["error"]["code"]), out)
        st, out, _h = self.call("POST", f"/api/orders/{cc}/replace?book=T", {"qty": 5, "order_type": "LIMIT", "limit_px": "2.70"}, "ui:cover-res-cc-repl-01")
        self.assertEqual((409, "NOT_COVERED"), (st, out["error"]["code"]), out)
        self.assertEqual(("WORKING", 1), (self.row(cc)["status"], int(self.row(cc)["qty"])))
        self.hold(400)
        st, out, _h = self.call("PATCH", f"/api/orders/{cc}?book=T", {"qty": 5}, "ui:cover-res-cc-amend-02")
        self.assertEqual(200, st, out)
        self.bid(self.C105, "2.70", "2.80")
        self.assertEqual(({"ACME": 500, self.C105: -5}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_a_short_sale_while_the_shares_are_held_is_refused_cleanly(self):
        """Round 8 property tester cover a-e: with 300 ACME held and 3 calls sold as covered on them (resting, then sold), no reading offers a
        short sale ('sell 400 ACME': WOULD_OVERSELL with no short-sale choice; 'short 400 ACME': SHORT_WHILE_LONG), and the order route
        refuses a short sale from any ticket 400 SHORT_WHILE_LONG - also one no larger than the holding, which 1.0.7.1 rolled back 500
        INTEGRITY at its fill - with no ERROR attention item."""
        self.hold(300)
        st, status, cc, out = self.covered(self.C105, 3, "2.70")
        self.assertEqual((200, "WORKING"), (st, status), out)
        for when in ("resting", "sold"):
            with self.subTest(when=when):
                out = self.parse("sell 400 ACME")
                (f,) = [f for l in out["legs"] for f in l["flags"] if f["code"] == "WOULD_OVERSELL"]
                self.assertEqual([], [c["label"] for c in f.get("choices") or [] if "short" in c["label"].lower()])
                out = self.parse("short 400 ACME")
                self.assertEqual([None], [l["request"] for l in out["legs"]])
                self.assertIn("SHORT_WHILE_LONG", [f["code"] for l in out["legs"] for f in l["flags"] if f["severity"] == "blocker"])
                for qty in (400, 300, 100):
                    st, code, msg, out = self.order("ACME", "SELL_SHORT", qty, borrow=True)
                    self.assertEqual((400, "SHORT_WHILE_LONG"), (st, code), out)
                    self.assertIn("holds 300 ACME long - sell them first (SELL)", msg)
                self.bid(self.C105, "2.70", "2.80")
        self.assertEqual(("FILLED", {"ACME": 300, self.C105: -3}, 0), (self.row(cc)["status"], self.positions(), self.uncovered()))
        self.assertEqual([], self.q("SELECT code FROM attention WHERE severity='ERROR'"))
        assert_integrity(self, self.rt, "full")

    def test_the_reservation_is_read_from_the_positions_not_the_last_margin_run(self):
        """Round 8 stale_reservation a-b, property tester cover g: calls sold first, the shares bought after them.  The margin run of the
        calls' fill stored the reservation of that moment; the shares bought are those calls' cover at once: the reading offers no covered
        call on them, a covered call ticket is refused 409 NOT_COVERED, and a sale of them 400 OVERSELL (calls already sold)."""
        for held, bought in ((0, 300), (100, 200)):
            with self.subTest(held=held, bought=bought):
                if held:
                    self.tearDown()
                    self.setUp()
                    self.hold(held)
                st, status, _m, out = self.order(self.C105, "STO", 3)                    # 3 calls sold: naked, or on 100 ACME
                self.assertEqual((200, "FILLED"), (st, status), out)
                self.assertEqual({"ACME": held} if held else {}, self.stored_reservation())
                self.hold(bought)
                out = self.parse(f"covered call {bought} ACME Dec 18 2026 110")
                self.assertTrue(all(l["request"] is None for l in out["legs"]), out["legs"])
                self.assertIn("SIDE_SPLIT", [f["code"] for l in out["legs"] for f in l["flags"] if f["severity"] == "blocker"])
                st, status, _cc, out = self.covered(self.C110, bought // 100)
                self.assertEqual((409, "NOT_COVERED"), (st, status), out)
                st, code, msg, out = self.order("ACME", "SELL", 1)
                self.assertEqual((400, "OVERSELL"), (st, code), out)
                self.assertIn("cover calls already sold", msg)
                self.assertEqual(({"ACME": held + bought, self.C105: -3}, 0), (self.positions(), self.uncovered()))
                assert_integrity(self, self.rt, "full")

    def test_an_early_put_exercise_never_delivers_shares_that_cover_calls(self):
        """Round 8 exercise a: a collar on the 300 ACME held - its two tickets, the put bought and the call sold as covered - then ACME falls
        to 90.00: exercising the 3 puts early would deliver the 300 ACME the calls are covered by: 409 SHARES_RESERVED, nothing delivered;
        a sale of them is refused as well.  With 300 more ACME bought, the same exercise delivers those and the calls stay covered."""
        self.hold(300)
        put, call = [l["request"] for l in self.parse("collar 300 ACME Dec 18 2026 95/110")["legs"]]
        self.assertEqual((("BTO", None), ("STO", True)), ((put["side"], put.get("covered")), (call["side"], call.get("covered"))))
        for req in (put, call):
            st, out = self.ticket(req, self.rid())
            self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)
        self.assertEqual({"ACME": 300, self.P95: 3, self.C110: -3}, self.positions())
        self.rt.clock.advance(60)
        print_acme(self.rt, "90.00", 60, self.rt.clock.epoch() - 120, 50000)
        st, out, _h = self.call("POST", "/api/options/exercise?book=T", {"account_id": ACCT, "instrument_id": self.P95, "qty": 3}, "ui:cover-res-exercise-01")
        self.assertEqual((409, "SHARES_RESERVED"), (st, out["error"]["code"]), out)
        self.assertEqual({"ACME": 300, self.P95: 3, self.C110: -3}, self.positions())
        st, code, _m, out = self.order("ACME", "SELL", 300)
        self.assertEqual((400, "OVERSELL"), (st, code), out)
        self.hold(300)
        st, out, _h = self.call("POST", "/api/options/exercise?book=T", {"account_id": ACCT, "instrument_id": self.P95, "qty": 3}, "ui:cover-res-exercise-02")
        self.assertEqual(200, st, out)
        self.assertEqual(({"ACME": 300, self.C110: -3}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_lending_never_lends_shares_that_cover_calls(self):
        """INV-2: what the securities-lending program may lend leaves out the shares covering calls sold or being sold, in whole contracts
        (2 calls resting as covered: 200; then 1 plain call sold: 300 of the 300 held)."""
        from finsim.terminal.lending_accruals import free_to_lend

        def lend() -> dict:
            with self.rt.db.reader() as conn:
                return free_to_lend(self.rt, conn, ACCT, "ACME")
        self.hold(300)
        self.assertEqual((300, 0), (lend()["position"], lend()["reserved"]))
        st, status, _cc, out = self.covered(self.C105, 2, "2.70")
        self.assertEqual((200, "WORKING"), (st, status), out)
        self.assertEqual(200, lend()["reserved"])
        st, status, _m, out = self.order(self.C110, "STO", 1)                               # the 100 left free: allowed (INV-9)
        self.assertEqual((200, "FILLED"), (st, status), out)
        f = lend()
        self.assertEqual((300, 300, 0), (f["position"], f["reserved"], f["free"]))
        assert_integrity(self, self.rt, "full")

    def test_the_preview_shows_not_covered(self):
        """INV-1 / INV-9 at the ticket's preview: a covered call whose shares are not free shows NOT_COVERED (the refusal's own words), a
        further plain call on the name SHARES_RESERVED, a sale of the reserved shares OVERSELL naming the calls."""
        def blockers(req: dict) -> list:
            st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(req))
            self.assertEqual(200, st, pv)
            return [(b["code"], b["message"]) for b in pv.get("blockers") or []]
        cc = {"account_id": ACCT, "instrument_id": self.C105, "side": "STO", "qty": 3, "order_type": "MARKET", "tif": "DAY", "covered": True}
        ((code, msg),) = blockers(cc)
        self.assertEqual("NOT_COVERED", code)
        self.assertIn("need 300 ACME held free in T1:US_EXCHANGE as their cover and 0 are free now (held 0)", msg)
        st, out = self.ticket(cc, "ui:cover-res-pv-01")
        self.assertEqual((409, "NOT_COVERED", msg), (st, out["error"]["code"], out["error"]["message"]))
        self.hold(300)
        self.assertEqual([], blockers(cc))
        st, status, oid, out = self.covered(self.C105, 3, "2.70")
        self.assertEqual((200, "WORKING"), (st, status), out)
        ((code, msg),) = blockers(cc)
        self.assertEqual("NOT_COVERED", code)
        self.assertIn("(held 300; 300 covering calls being sold)", msg)
        plain = {k: v for k, v in cc.items() if k != "covered"}
        ((code, msg),) = blockers(dict(plain, instrument_id=self.C110, qty=1))
        self.assertEqual("SHARES_RESERVED", code)
        self.assertIn(f"covers calls sold as covered (3 contracts: {oid})", msg)
        ((code, msg),) = blockers({"account_id": ACCT, "instrument_id": "ACME", "side": "SELL", "qty": 100, "order_type": "MARKET", "tif": "DAY"})
        self.assertEqual("OVERSELL", code)
        self.assertIn(f"300 ACME cover calls being sold ({oid})", msg)
        assert_integrity(self, self.rt, "full")

    def test_controls_closed_calls_free_their_shares_and_a_plain_sale_is_unchanged(self):
        """Controls: a covered call cancelled, or bought back, frees its shares at once (a sale of them is accepted); with no call on the
        name a sale and an oversell read as in 1.0.7.1."""
        self.hold(300)
        st, status, cc, out = self.covered(self.C105, 3, "2.70")
        self.assertEqual((200, "WORKING"), (st, status), out)
        st, out, _h = self.call("DELETE", f"/api/orders/{cc}?book=T", {}, "ui:cover-res-x-01")
        self.assertEqual(200, st, out)
        st, status, _m, out = self.order("ACME", "SELL", 100)
        self.assertEqual((200, "FILLED"), (st, status), out)
        st, status, _cc, out = self.covered(self.C105, 2)                                  # sold at the 2.60 bid
        self.assertEqual((200, "FILLED"), (st, status), out)
        st, status, _m, out = self.order("ACME", "SELL", 100)
        self.assertEqual((400, "OVERSELL"), (st, status), out)
        st, status, _m, out = self.order(self.C105, "BTC", 2)                              # bought back at the 2.75 ask
        self.assertEqual((200, "FILLED"), (st, status), out)
        st, status, _m, out = self.order("ACME", "SELL", 200)
        self.assertEqual((200, "FILLED"), (st, status), out)
        self.assertEqual({}, self.positions())
        self.hold(100)
        st, status, msg, out = self.order("ACME", "SELL", 150)
        self.assertEqual((400, "OVERSELL"), (st, status), out)
        self.assertNotIn("SHARES_RESERVED", msg)
        st, status, _m, out = self.order("ACME", "SELL", 100)
        self.assertEqual((200, "FILLED"), (st, status), out)
        assert_integrity(self, self.rt, "full")

    def test_a_further_call_on_a_name_with_calls_sold_as_covered_needs_free_shares(self):
        """INV-9: FinSim's margin gives shares to calls by expiry, then strike - never by claim.  While 3 Jan 15 2027 105 calls are sold as
        covered on the 300 ACME held, a further call sold to open on ACME without free shares would take their cover: an earlier expiry, a
        lower strike, the short leg of an all-or-none spread (the Strategies tab) and a sequential package selling more calls than the
        shares it buys are refused 409 SHARES_RESERVED, nothing placed; a buy write through the Hedges tab (its call covered by the shares
        it buys, released as they fill) is accepted; with free shares a plain call is accepted, an amend raising it beyond them is refused,
        and the margin then covers every call, the claimed ones included."""
        self.hold(300)
        st, status, cc, out = self.covered(self.C105J, 3)                                   # sold at the 3.90 bid
        self.assertEqual((200, "FILLED"), (st, status), out)
        got = {}
        for what, occ in (("an earlier expiry", self.C110), ("a lower strike", acme("C", 100, E2))):
            st, code, msg, out = self.order(occ, "STO", 1)
            got[what] = (st, code)
            self.assertIn(f"covers calls sold as covered (3 contracts: {self.C105J})", msg)       # calls sold: named by their contract
        st, out = self.template_package("BULL_CALL_SPREAD", 100, {0: 100, 1: 105}, rid="ui:cover-res-tpl-01")    # BTO Dec 100, STO Dec 105
        got["an all-or-none spread's short leg"] = (st, (out.get("error") or {}).get("code"))
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        st, out, _p, _l = self.seq_package(primary, [leg(self.C120, "STO", 2)], rid="ui:cover-res-seq-01")      # 2 calls on 100 shares bought
        got["a package selling more calls than its shares"] = (st, (out.get("error") or {}).get("code"))
        self.assertEqual({k: (409, "SHARES_RESERVED") for k in got}, got)
        self.assertEqual([], self.q("SELECT order_id FROM orders WHERE request_id LIKE 'ui:cover-res-tpl-%' OR request_id LIKE 'ui:cover-res-seq-%'"))
        # a buy write: 100 ACME bought at the print, its 1 call released at the fill and sold at the 0.20 bid
        st, out, p, (h,) = self.seq_package(dict(primary, order_type="MARKET", limit_px=None), [leg(self.C120, "STO", 1)], rid="ui:cover-res-seq-02")
        self.assertEqual((200, "FILLED"), (st, p["status"]), out)
        self.bid(self.C120, "0.20", "0.25")
        self.assertEqual(("FILLED", 1), (self.row(h["order_id"])["status"], int(self.row(h["order_id"])["filled_qty"])))
        # with free shares (200 more bought): a plain Dec 110 call resting at 1.50 is accepted; raised beyond the free shares it is refused
        self.hold(200)
        st, status, _m, out = self.order(self.C110, "STO", 1, order_type="LIMIT", limit_px="1.50")
        self.assertEqual((200, "WORKING"), (st, status), out)
        plain = out["order"]["order_id"]
        st, out, _h = self.call("PATCH", f"/api/orders/{plain}?book=T", {"qty": 3}, "ui:cover-res-plain-amend-01")
        self.assertEqual((409, "SHARES_RESERVED"), (st, out["error"]["code"]), out)
        st, out, _h = self.call("PATCH", f"/api/orders/{plain}?book=T", {"qty": 2}, "ui:cover-res-plain-amend-02")
        self.assertEqual(200, st, out)
        self.bid(self.C110, "1.50", "1.60")
        self.assertEqual({"ACME": 600, self.C105J: -3, self.C120: -1, self.C110: -2}, self.positions())
        # the margin covers every call: Dec 110 (200) and Dec 120 (100) first, then the Jan 105 calls sold as covered (300)
        self.assertEqual({self.C105J: "covered_call", self.C110: "covered_call", self.C120: "covered_call"}, self.margin_rules())
        self.assertEqual(0, self.uncovered())
        assert_integrity(self, self.rt, "full")

    def test_a_resting_short_sale_counts_against_new_calls(self):
        """INV-4's known limit, counted: a short sale placed while the book was flat (300 ACME at 110.00, resting) still works when 300 ACME
        are bought: those are the shares it will sell, so the reading offers no covered call on them and a covered call ticket is refused
        409 NOT_COVERED (300 in working short sales); once the short sale is cancelled the same ticket is placed."""
        st, status, _m, out = self.order("ACME", "SELL_SHORT", 300, order_type="LIMIT", limit_px="110.00", borrow=True)
        self.assertEqual((200, "WORKING"), (st, status), out)
        short = out["order"]["order_id"]
        self.hold(300)
        out = self.parse("covered call 300 ACME Dec 18 2026 105")
        self.assertTrue(all(l["request"] is None for l in out["legs"]), out["legs"])
        self.assertTrue(any("300 taken by working short sales" in f["text"] for l in out["legs"] for f in l["flags"] if f["code"] == "SIDE_SPLIT"))
        st, status, _cc, out = self.covered(self.C105, 3)
        self.assertEqual((409, "NOT_COVERED"), (st, status), out)
        self.assertIn("(held 300; 300 in working short sales)", out["error"]["message"])
        st, out, _h = self.call("DELETE", f"/api/orders/{short}?book=T", {}, "ui:cover-res-x-short")
        self.assertEqual(200, st, out)
        st, status, _cc, out = self.covered(self.C105, 3)
        self.assertEqual((200, "FILLED"), (st, status), out)
        self.assertEqual(({"ACME": 300, self.C105: -3}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_reservations_are_whole_contracts_as_the_margin_assigns_them(self):
        """150 ACME held and 2 calls sold (or still working): the margin covers 1 call with 100 shares and charges the other as naked, so 100
        are reserved and 50 can be sold - SELL 51 is refused, SELL 50 accepted."""
        for sold in (True, False):
            with self.subTest(sold=sold):
                if not sold:
                    self.tearDown()
                    self.setUp()
                self.hold(150)
                st, status, _m, out = self.order(self.C105, "STO", 2, **({} if sold else {"order_type": "LIMIT", "limit_px": "2.70"}))
                self.assertEqual((200, "FILLED" if sold else "WORKING"), (st, status), out)
                st, code, msg, out = self.order("ACME", "SELL", 51)
                self.assertEqual((400, "OVERSELL"), (st, code), out)
                self.assertIn("shares covering calls is 50", msg)
                st, status, _m, out = self.order("ACME", "SELL", 50)
                self.assertEqual((200, "FILLED"), (st, status), out)
                self.assertEqual({"ACME": 100, self.C105: -2} if sold else {"ACME": 100}, self.positions())
                assert_integrity(self, self.rt, "full")

    def test_an_expiry_exercise_that_delivers_the_cover_is_said(self):
        """The known limit, said: a long Dec 18 2026 95 put exercised automatically at its expiry (ACME's close 90.00) delivers the 300 ACME
        that cover 3 Jan 15 2027 105 calls sold as covered; ONE WARN attention item (COVER_DELIVERED) names them - they are now naked."""
        self.hold(300)
        st, status, cc, out = self.covered(self.C105J, 3)
        self.assertEqual((200, "FILLED"), (st, status), out)
        st, status, _m, out = self.order(self.P95, "BTO", 3)
        self.assertEqual((200, "FILLED"), (st, status), out)
        # Fri 2026-12-18 after the 16:20 expiry time (the trades of Oct 20 settled); ACME's observed close of the day 90.00 (a marks CLOSE row:
        # fixture)
        self.rt.clock.set("2026-12-18T16:30:00-05:00")
        self.assertIsNotNone(self.rt.lifecycle.tick(at="2026-12-18T16:30:00-05:00", only="SETTLE_DUE")["ran"])
        ts = self.rt.clock.epoch()
        with self.rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                            " VALUES ('ACME', '2026-12-18', 'CLOSE', '90.00', 'USD', 'close', 'test', 'direct', ?, ?, 'fixture')", (ts - 1800, ts))
        r = execute(self.rt, "options.expire", {"account_id": ACCT, "instrument_id": self.P95}, "job:cover-res-expiry-01", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("EXERCISE", 3), (r.body["kind"], r.body["contracts"]))
        self.assertEqual({self.C105J: -3}, self.positions())
        items = self.q("SELECT severity, message FROM attention WHERE code='COVER_DELIVERED'")
        self.assertEqual(["WARN"], [i["severity"] for i in items])
        for words in ("the expiry exercise of", "delivered 300 ACME that covered the calls sold as covered", self.C105J, "they are now naked"):
            self.assertIn(words, items[0]["message"])
        assert_integrity(self, self.rt, "full")


# ===== rows the candidate 7b634e2 may have written (SIMPLIFY_PLAN s7) ======================================================================
class OldCandidateRowsTests(SmartSubmitBase):
    """Rows the 1.0.7.2 candidate could have written in a test home or a scratch copy - CUSTOM packages (all-or-none and sequential), orders
    under ``st:`` request ids, command payloads with ``smart`` and its confirmed figures - load read-only: Pending Trades and the order
    drawer read them in words, the generic machinery cancels and fills them as 1.0.7.1 does, a resend under such a request id is the
    replay of the order placed, the parse reads none of them, and a new CUSTOM package is refused 400 UNKNOWN_STRATEGY.  The rows are
    written as the candidate wrote them: packages placed with the candidate's request ids and notes, then given its CUSTOM key (the one
    fixture write)."""
    CALL = acme("C", 105)

    def test_custom_packages_and_st_orders_load_read_only_and_new_custom_is_refused(self):
        rt = self.rt
        # an all-or-none package: 30 x (BTO Dec 18 100 call, STO Dec 18 105 call) - 30 > 20 displayed: it works unfilled
        aon_body = {"account_id": ACCT, "tif": "DAY", "package": {"strategy_key": "BULL_CALL_SPREAD", "underlying": "ACME", "units": 3000, "tenor_months": 1,
                                                                  "strikes": {"0": 100, "1": 105}},
                    "smart": {"ticket": TICKET, "item": "C1", "clause": "buy 30 ACME Dec 18 2026 100/105 call spread"},
                    "aon_confirmed": {"net_cash": "-7200.00", "previewed_at": self.now}}
        st, aon, _h = self.send(aon_body, f"st:{TICKET}:C1:r1")
        self.assertEqual((200, "WORKING"), (st, aon["package"]["status"]), aon)
        # a sequential package: BUY 300 ACME limit 99 (resting) + STO 3 Dec 18 105 calls waiting for it
        seq_body = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 300, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY",
                    "hedge": {"legs": [leg(acme("C", 105), "STO", 3)]}, "smart": {"ticket": TICKET, "item": "C2", "clause": "buy 300 ACME limit 99 and sell 3"
                                                                                                                            " calls as a package"},
                    "seq_confirmed": {"options_cash": "780.00", "previewed_at": self.now}}
        st, seq, _h = self.send(seq_body, f"st:{TICKET}:C2:r1")
        self.assertEqual(200, st, seq)
        # a single order with the page's note
        st, one, _h = self.send({"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "90.00", "tif": "DAY",
                                 "smart": {"ticket": TICKET, "item": "L3", "clause": "buy 10 ACME limit 90"}}, f"st:{TICKET}:L3:r1")
        self.assertEqual((200, "WORKING"), (st, one["order"]["status"]), one)
        pids = (aon["package"]["package_id"], seq["hedge_package"])
        with rt.db.writer() as tx:                                         # the candidate's key (fixture)
            tx.conn.execute(f"UPDATE hedge_packages SET strategy_key='CUSTOM', candidate_id='CUSTOM' WHERE package_id IN ({','.join('?' for _ in pids)})", pids)
        legs = {r["request_id"]: r for r in self.q("SELECT * FROM orders WHERE request_id LIKE 'st:%'")}
        self.assertEqual({f"st:{TICKET}:C1:r1:L1", f"st:{TICKET}:C1:r1:L2", f"st:{TICKET}:C2:r1", f"st:{TICKET}:C2:r1:H1", f"st:{TICKET}:L3:r1"}, set(legs))
        # Pending Trades and the order drawer read the packages in words
        st, pend, _h = self.call("GET", "/api/blotter/pending?scope=T1:ALL")
        self.assertEqual(200, st, pend)
        words = {r["order_id"]: r.get("strategy_words") for r in pend["rows"] if r.get("package_id")}
        self.assertEqual({"your own legs (Smart Ticket)"}, set(words.values()), words)
        self.assertEqual(4, len(words))
        st, d, _h = self.call("GET", f"/api/orders/{legs[f'st:{TICKET}:C1:r1:L1']['order_id']}")
        self.assertEqual((200, "CUSTOM", "your own legs (Smart Ticket)"), (st, d["package"]["strategy_key"], d["package"]["strategy_words"]), d)
        # a resend under such a request id is the replay of the order placed
        n = self.q("SELECT COUNT(*) n FROM orders")[0]["n"]
        st, again, h = self.send(aon_body, f"st:{TICKET}:C1:r1")
        self.assertEqual((200, "1"), (st, h.get("X-Replayed")), again)
        self.assertEqual(n, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # the parse reads none of them: the same words read as a new order
        (l,) = self.parse("buy 10 ACME limit 90")["legs"]
        self.assertIsNotNone(l["request"])
        # the owner cancels the all-or-none package from Pending Trades; the sequential one fills as 1.0.7.1 fills it
        st, out, _h = self.call("POST", "/api/orders/cancel?book=T", {"order_ids": [legs[f"st:{TICKET}:C1:r1:L1"]["order_id"]], "reason": "cancelled by the owner"},
                                "mc:oldrows00001")
        self.assertEqual(200, st, out)
        self.assertEqual(["CANCELLED", "CANCELLED"], [r["status"] for r in self.q("SELECT status FROM orders WHERE package_id=? ORDER BY order_id", (pids[0],))])
        ReleaseAfterAmendOrReplaceTests.bar(self, 50000)                   # 98.90 crosses the 99.00 limit: 300 filled, the 3 calls released
        ReleaseAfterAmendOrReplaceTests.sell_calls(self)
        self.assertEqual({"ACME": 300, acme("C", 105): -3}, ReleaseAfterAmendOrReplaceTests.positions(self))
        # a new CUSTOM package is refused, nothing written - at the order route and at the Strategies preview
        st, out, _h = self.send({"account_id": ACCT, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": [leg(acme("C", 100), "BTO", 1),
                                                                                                               leg(acme("C", 105), "STO", 1)]}}, "ui:old-custom-01")
        self.assertEqual((400, "UNKNOWN_STRATEGY"), (st, out["error"]["code"]), out)
        self.assertEqual([], self.q("SELECT id FROM commands WHERE request_id='ui:old-custom-01'"))
        st, out, _h = self.call("POST", "/api/strategies/preview?book=T", {"account_id": ACCT, "key": "CUSTOM", "params": {"legs": [leg(acme("C", 100), "BTO", 1)]}})
        self.assertEqual((400, "UNKNOWN_STRATEGY"), (st, out["error"]["code"]), out)
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
