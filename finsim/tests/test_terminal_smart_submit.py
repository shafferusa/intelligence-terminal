"""1.0.7.2 (CHANGE_PLAN 3.2 / 3.4): what the Smart Ticket sends goes through the existing order route unchanged - a single leg is the
ticket's own order, a combination is a CUSTOM package (``strategies._custom_spec``) - with the page's request ids and its ``smart``
note.  Every expected number is worked by hand in the comment next to it; nothing touches the network or ~/.finsim.

The world: the Test Book T1 (USD 10,000,000.00 in T1:TREASURY, T1:US_EXCHANGE empty, funded by the automatic sweep at each fill)
built by ``server.build_runtime`` offline (every module bound as ``serve`` binds it, the Router, FakeClock, FakeProvider), Tue
2026-10-20 14:00 ET (US equities and equity options open).  ACME is a fictional listing (added to the master as Yahoo resolution
would add it), its print 100.00 observed 2 minutes ago on a completed 1-minute bar of 50,000 shares (10 % participation: 5,000 a
bar).  Its Cboe chain is invented (every price below), observed one minute ago and fetched now, 20 contracts displayed on every bid
and ask; the underlying shows no bid / ask, so shares fill at the print.  Option fees 0.65 a contract.

Request ids are the page's (CHANGE_PLAN 3.2): a single leg ``st:<ticket12>:L<n>:r<rev>``, a combination ``st:<ticket12>:C<n>:r<rev>``;
the server names an all-or-none package's legs ``<rid>:L<i>`` and a sequential package's waiting legs ``<rid>:H<i>``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import _Log, assert_integrity
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


def acme_chain(now: int, size: int = 20, fetched_at=None, quote_time=None) -> ChainSnapshot:
    qt = now - 60 if quote_time is None else quote_time
    rows = [ChainRow(acme(r, k, e), e, r, qprice(k), qprice(b), qprice(a), size, size, None, None, 0.30, None, None, None, None, 100, 10)
            for e, r, k, b, a in ROWS]
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

    # ---- the routes, as the page calls them -------------------------------------------------------------------------------------
    def call(self, method: str, path: str, body=None, rid=None):
        b = dict(body or {})
        if rid is not None:
            b["request_id"] = rid
        return self.rt.router.handle(method, path, None, b, "127.0.0.1", None)

    def send(self, body: dict, rid: str, book: str = "T"):
        return self.call("POST", f"/api/orders?book={book}", body, rid)

    def combo(self, n: int, legs: list, rev: int = 1, clause: str = "", **extra):
        body = dict({"account_id": ACCT, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": legs},
                     "smart": {"ticket": TICKET, "item": f"C{n}", "clause": clause}}, **extra)
        return self.send(body, f"st:{TICKET}:C{n}:r{rev}")

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

    def filled_aon(self, n: int, legs: list, prices: list, clause: str = ""):
        """Send combination ``n`` and check it filled at the submit, every leg at ``prices`` (its executable bid / ask), all from ONE
        observation; returns the package id."""
        st, out, h = self.combo(n, legs, clause=clause)
        self.assertEqual(200, st, out)
        rid = f"st:{TICKET}:C{n}:r1"
        pk = out["package"]
        self.assertEqual(("AON", "CUSTOM", "FILLED", "FILL"), (pk["fill_mode"], pk["strategy_key"], pk["status"], out["decision"]["action"]))
        oids = [o["order_id"] for o in out["orders"]]
        self.assertEqual([f"{rid}:L{i}" for i in range(1, len(legs) + 1)], [o["request_id"] for o in self.q(
            f"SELECT request_id FROM orders WHERE order_id IN ({','.join('?' for _ in oids)}) ORDER BY order_id", oids)])
        self.assertEqual(["FILLED"] * len(legs), [o["status"] for o in out["orders"]])
        got = {(o, s, n_, p) for o, s, n_, p in self.fills_of(oids)}
        want = {(o["order_id"], l["side"], int(l["qty"]), D(px)) for o, l, px in zip(out["orders"], legs, prices)}
        self.assertEqual(want, got)
        keys = {f["idem_key"].rsplit(":", 1)[0] for f in self.q(f"SELECT idem_key FROM fills WHERE order_id IN ({','.join('?' for _ in oids)})", oids)}
        self.assertEqual(1, len(keys), keys)                         # matchpkg:<package>:<quote_time>: one observation for every leg
        hp = self.q("SELECT strategy_key, candidate_id, fill_mode, status FROM hedge_packages WHERE package_id=?", (pk["package_id"],))[0]
        self.assertEqual(("CUSTOM", "AON", "FILLED"), (hp["strategy_key"], hp["fill_mode"], hp["status"]))
        assert_integrity(self, self.rt, "full")
        return pk["package_id"]


# ===== a single leg: the ticket's own order, with the page's id and note ===============================================================
class SingleLegTests(SmartSubmitBase):
    def test_a_leg_with_smart_is_the_tickets_order_and_ids_keep_their_rules(self):
        rt = self.rt
        ticket = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "90.00", "tif": "DAY"}
        st, a, _h = self.send(ticket, "ui:ticket-acme-000001")
        self.assertEqual(200, st, a)
        smart = dict(ticket, smart={"ticket": TICKET, "item": "L1", "clause": "buy 10 ACME limit 90"})
        rid = f"st:{TICKET}:L1:r1"
        st, b, h = self.send(smart, rid)
        self.assertEqual(200, st, b)
        self.assertNotIn("X-Replayed", h)
        # the same order for the same fields: a 90.00 limit below the 100.00 print rests; 10 x 90.00 + the 1.00 minimum fee reserved
        cols = ("account_id", "instrument_id", "side", "qty", "filled_qty", "order_type", "limit_px", "tif", "status", "expires_ts", "fill_mode", "package_id")
        oa, ob = (self.q("SELECT * FROM orders WHERE order_id=?", (x["order"]["order_id"],))[0] for x in (a, b))
        self.assertEqual({k: oa[k] for k in cols}, {k: ob[k] for k in cols})
        self.assertEqual(("WORKING", D("90.00"), rid), (ob["status"], D(ob["limit_px"]), ob["request_id"]))
        # the note is stored with the command (the audit trail of what was typed) and read by nothing else
        stored = json.loads(self.q("SELECT payload_json FROM commands WHERE request_id=?", (rid,))[0]["payload_json"])
        self.assertEqual({"ticket": TICKET, "item": "L1", "clause": "buy 10 ACME limit 90"}, stored["smart"])
        self.assertNotIn("smart", json.loads(self.q("SELECT payload_json FROM commands WHERE request_id=?", ("ui:ticket-acme-000001",))[0]["payload_json"]))
        n_orders = self.q("SELECT COUNT(*) n FROM orders")[0]["n"]
        # a resend of the unchanged item is a replay: the same answer, X-Replayed, no second order
        st, again, h = self.send(smart, rid)
        self.assertEqual((200, "1", b["order"]["order_id"]), (st, h.get("X-Replayed"), again["order"]["order_id"]))
        self.assertEqual(n_orders, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # ... also as the page resends it (1.0.7.2 review R1): a FRESH preview id, confirm_large false and the note of the words differ from
        # the stored command, the order's terms do not - a replay, never 409 REQUEST_ID_REUSED for the order that works
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(ticket, confirm_large=False))
        self.assertEqual(200, st, pv)
        st, again, h = self.send(dict(smart, preview_id=pv["preview_id"], confirm_large=False, smart=dict(smart["smart"], clause="buy 10 ACME limit 90.00")), rid)
        self.assertEqual((200, "1", b["order"]["order_id"]), (st, h.get("X-Replayed"), again["order"]["order_id"]))
        self.assertEqual(n_orders, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # the same id with another payload (an edited item must get a new rev) is refused, nothing written
        st, out, _h = self.send(dict(smart, qty=11), rid)
        self.assertEqual((409, "REQUEST_ID_REUSED"), (st, out["error"]["code"]))
        self.assertEqual(n_orders, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # a refusal leaves no row: limit 90.005 is off the 0.01 tick; the same id works once the price is fixed
        rid2 = f"st:{TICKET}:L2:r1"
        st, out, _h = self.send(dict(smart, limit_px="90.005", smart={"ticket": TICKET, "item": "L2", "clause": "buy 10 ACME limit 90.005"}), rid2)
        self.assertEqual((400, "PRICE_TICK"), (st, out["error"]["code"]))
        self.assertEqual([], self.q("SELECT id FROM commands WHERE request_id=?", (rid2,)))
        self.assertEqual([], self.q("SELECT order_id FROM orders WHERE request_id=?", (rid2,)))
        st, out, _h = self.send(dict(smart, limit_px="90.01", smart={"ticket": TICKET, "item": "L2", "clause": "buy 10 ACME limit 90.01"}), rid2)
        self.assertEqual((200, "WORKING", D("90.01")), (st, out["order"]["status"], D(out["order"]["limit_px"])))
        # ?book= is the leg's own book: the right one passes the guard (above); another book is refused before anything is written
        rid3 = f"st:{TICKET}:L3:r1"
        st, out, _h = self.send(dict(smart, qty=12), rid3, book="P")
        self.assertEqual((409, "BOOK_MISMATCH", "T", "P"), (st, out["error"]["code"], out["error"]["details"]["book"], out["error"]["details"]["selected"]))
        self.assertEqual([], self.q("SELECT id FROM commands WHERE request_id=?", (rid3,)))
        # every id the page makes fits the request-id rule (8-80 of [A-Za-z0-9:_.-]), the server's leg suffixes included
        from finsim.terminal.commands import RID_RE
        for x in (rid, f"st:{TICKET}:C8:r99:L8", f"st:{TICKET}:C8:r99:H7"):
            self.assertTrue(RID_RE.match(x), x)
        assert_integrity(self, rt, "full")


# ===== all-or-none combinations: every leg from one observation, or nothing ============================================================
class AonComboTests(SmartSubmitBase):
    def test_b_vertical_straddle_and_strangle_fill_all_or_none(self):
        # bull call spread: BTO C100 at the ask 5.00, STO C105 at the bid 2.60 (a debit of 2.40 x 100 = 240.00)
        self.filled_aon(1, [leg(acme("C", 100), "BTO", 1), leg(acme("C", 105), "STO", 1)], ["5.00", "2.60"], "buy the ACME Dec 100/105 call spread")
        # straddle: BTO C100 at 5.00 and BTO P100 at 4.30
        self.filled_aon(2, [leg(acme("C", 100), "BTO", 1), leg(acme("P", 100), "BTO", 1)], ["5.00", "4.30"])
        # strangle: BTO P95 at 2.25 and BTO C110 at 1.30
        self.filled_aon(3, [leg(acme("P", 95), "BTO", 1), leg(acme("C", 110), "BTO", 1)], ["2.25", "1.30"])
        # positions: C100 +2, C105 -1, P100 +1, P95 +1, C110 +1
        pos = {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}
        self.assertEqual({acme("C", 100): 2, acme("C", 105): -1, acme("P", 100): 1, acme("P", 95): 1, acme("C", 110): 1}, pos)

    def test_c_iron_condor_and_butterfly_fill_all_or_none(self):
        # iron condor: BTO P90 1.10, STO P95 2.10, STO C105 2.60, BTO C110 1.30 -> credit -1.10 + 2.10 + 2.60 - 1.30 = 2.30 (230.00)
        pid = self.filled_aon(1, [leg(acme("P", 90), "BTO", 1), leg(acme("P", 95), "STO", 1), leg(acme("C", 105), "STO", 1), leg(acme("C", 110), "BTO", 1)],
                              ["1.10", "2.10", "2.60", "1.30"])
        self.assertEqual(4, self.q("SELECT COUNT(*) n FROM orders WHERE package_id=?", (pid,))[0]["n"])
        # butterfly with a body of 2: BTO C110 1.30, STO 2 C115 0.50, BTO C120 0.25 -> debit 1.30 - 1.00 + 0.25 = 0.55 (55.00)
        self.filled_aon(2, [leg(acme("C", 110), "BTO", 1), leg(acme("C", 115), "STO", 2), leg(acme("C", 120), "BTO", 1)], ["1.30", "0.50", "0.25"])
        pos = {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}
        self.assertEqual({acme("P", 90): 1, acme("P", 95): -1, acme("C", 105): -1, acme("C", 110): 2, acme("C", 115): -2, acme("C", 120): 1}, pos)

    def test_d_calendar_and_a_roll_fill_all_or_none(self):
        # calendar (two expiries, one name): STO C100 Dec at the bid 4.80, BTO C100 Jan at the ask 6.40
        self.filled_aon(1, [leg(acme("C", 100), "STO", 1), leg(acme("C", 100, E2), "BTO", 1)], ["4.80", "6.40"])
        # a short Dec 105 call held (sold alone at the bid 2.60), then rolled out: BTC it at the ask 2.75 and STO the Jan 105 at the bid 3.90
        st, out, _h = self.send({"account_id": ACCT, "instrument_id": acme("C", 105), "side": "STO", "qty": 1, "order_type": "MARKET", "tif": "DAY",
                                 "smart": {"ticket": TICKET, "item": "L1", "clause": "sell 1 ACME Dec 105 call"}}, f"st:{TICKET}:L1:r1")
        self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)
        self.filled_aon(2, [leg(acme("C", 105), "BTC", 1), leg(acme("C", 105, E2), "STO", 1)], ["2.75", "3.90"], "roll the Dec 105 call to Jan")
        pos = {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}
        self.assertEqual({acme("C", 100): -1, acme("C", 100, E2): 1, acme("C", 105, E2): -1}, pos)     # the Dec 105 is closed

    def test_e_nothing_fills_on_too_small_a_size_or_a_stale_chain_then_one_fresh_observation_fills_all(self):
        rt = self.rt
        # 30 contracts against 20 displayed: AON_SIZE - the package works, no leg fills
        st, out, _h = self.combo(1, [leg(acme("C", 100), "BTO", 30), leg(acme("C", 105), "STO", 30)])
        self.assertEqual(200, st, out)
        self.assertEqual(("WORKING", "NONE", "AON_SIZE"), (out["package"]["status"], out["decision"]["action"], out["decision"]["reason"]))
        big = [o["order_id"] for o in out["orders"]]
        self.assertEqual((["WORKING", "WORKING"], []), ([o["status"] for o in out["orders"]], self.fills_of(big)))
        # a chain fetched 10 minutes ago (past the 5-minute fetch grace) is stale: priced, flagged, accepted - and nothing fills
        load_chain(rt, acme_chain(self.now, fetched_at=self.now - 600, quote_time=self.now - 660))
        legs = [leg(acme("C", 95), "BTO", 1), leg(acme("C", 110), "STO", 1)]
        st, pv, _h = self.call("POST", "/api/strategies/preview?book=T", {"account_id": ACCT, "key": "CUSTOM", "params": {"legs": legs}})
        self.assertEqual((200, "stale"), (st, pv["chain_quality"]), pv)
        self.assertTrue(any("stale" in w for w in pv["warnings"]))
        st, out, _h = self.combo(2, legs)
        self.assertEqual(200, st, out)
        self.assertEqual(("WORKING", "AON_STALE"), (out["package"]["status"], out["decision"]["reason"]))
        small = [o["order_id"] for o in out["orders"]]
        self.assertEqual([], self.fills_of(small))
        # one fresh observation: both legs of the small package fill together (BTO C95 at the ask 8.10, STO C110 at the bid 1.20); the big
        # one still finds 20 displayed and waits
        changed = load_chain(rt, acme_chain(self.now))
        self.assertIn(acme("C", 95), changed)
        self.assertGreaterEqual(rt.matcher.on_quotes(changed), 1)
        self.assertEqual({(small[0], "BTO", 1, D("8.10")), (small[1], "STO", 1, D("1.20"))}, set(self.fills_of(small)))
        self.assertEqual(["FILLED", "FILLED"], [o["status"] for o in self.q(f"SELECT status FROM orders WHERE order_id IN (?,?) ORDER BY order_id", small)])
        self.assertEqual([], self.fills_of(big))
        self.assertEqual(["WORKING", "WORKING"], [o["status"] for o in self.q("SELECT status FROM orders WHERE order_id IN (?,?) ORDER BY order_id", big)])
        # the Pending blotter names the package in words
        rows = [r for r in self.call("GET", "/api/blotter/pending?book=T&status=open")[1]["rows"] if r.get("package_id")]
        self.assertTrue(rows)
        self.assertEqual({"your own legs (Smart Ticket)"}, {r["strategy_words"] for r in rows})
        assert_integrity(self, rt, "full")

    def test_f_refusals_of_a_combination_leave_no_row(self):
        rt = self.rt
        n0 = rt.instruments.count()
        # a contract the chain does not list is never invented: 400 NOT_LISTED, nothing written, the id stays free
        st, out, _h = self.combo(1, [leg(acme("C", 125), "STO", 1), leg(acme("C", 105), "BTO", 1)])
        self.assertEqual((400, "NOT_LISTED"), (st, out["error"]["code"]))
        self.assertIsNone(rt.instruments.get(acme("C", 125)))
        self.assertEqual([], self.q("SELECT id FROM commands WHERE request_id=?", (f"st:{TICKET}:C1:r1",)))
        # a limit on an all-or-none leg (no net limit in this version); two names in one package
        st, out, _h = self.combo(1, [leg(acme("C", 100), "BTO", 1, order_type="LIMIT", limit_px="5.00"), leg(acme("C", 105), "STO", 1)])
        self.assertEqual((400, "LIMIT_IN_AON"), (st, out["error"]["code"]))
        st, out, _h = self.combo(1, [leg("WIDG", "BUY", 100), leg(acme("C", 105), "STO", 1)])
        self.assertEqual((400, "COMBO_INVALID"), (st, out["error"]["code"]))
        self.assertEqual([], self.q("SELECT order_id FROM orders"))
        # the fixed combination with the same id is accepted (a refusal never used the id up)
        self.filled_aon(1, [leg(acme("C", 100), "BTO", 1), leg(acme("C", 105), "STO", 1)], ["5.00", "2.60"])
        self.assertEqual(n0 + 2, rt.instruments.count())                     # exactly the two contracts the chain lists
        # ?book= of another book is refused before anything
        st, out, _h = self.send({"account_id": ACCT, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": [leg(acme("P", 100), "BTO", 1),
                                                                                                              leg(acme("P", 95), "STO", 1)]}},
                                f"st:{TICKET}:C2:r1", book="P")
        self.assertEqual((409, "BOOK_MISMATCH"), (st, out["error"]["code"]))
        self.assertEqual([], self.q("SELECT id FROM commands WHERE request_id=?", (f"st:{TICKET}:C2:r1",)))
        assert_integrity(self, rt, "full")


# ===== sequential: the shares first, the options wait and follow its fills ==============================================================
class SequentialComboTests(SmartSubmitBase):
    def test_g_children_wait_then_resize_to_the_primarys_fill(self):
        rt = self.rt
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 1000, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        legs = [leg(acme("C", 105), "STO", 10), leg("ACME", "BUY", 1000, order_type="LIMIT", limit_px="99.00")]      # named call first
        # a preview id the store does not know: the sequential package checks its primary's preview like a single order -> 409 PREVIEW_STALE
        st, out, _h = self.combo(1, legs, preview_id="pv:0000000000000000")
        self.assertEqual((409, "PREVIEW_STALE"), (st, out["error"]["code"]))
        self.assertEqual([], self.q("SELECT order_id FROM orders"))
        # the page's fresh preview of the primary leg, then the package with that preview id
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", primary)
        self.assertEqual(200, st, pv)
        st, out, h = self.combo(1, legs, rev=2, clause="buy 1000 ACME limit 99 and sell 10 Dec 105 calls", preview_id=pv["preview_id"])
        self.assertEqual(200, st, out)
        rid = f"st:{TICKET}:C1:r2"
        self.assertEqual(("SEQUENTIAL", "CUSTOM"), (out["package"]["fill_mode"], out["package"]["strategy_key"]))
        # the 99.00 limit is below the 100.00 print: the shares rest; the 10 calls wait for them (ratio 10 / 1,000 = 0.01)
        p = self.q("SELECT * FROM orders WHERE request_id=?", (rid,))[0]
        c = self.q("SELECT * FROM orders WHERE request_id=?", (rid + ":H1",))[0]
        self.assertEqual(("ACME", "BUY", 1000, "LIMIT", D("99.00"), "WORKING", 0), (p["instrument_id"], p["side"], p["qty"], p["order_type"], D(p["limit_px"]),
                                                                                    p["status"], p["filled_qty"]))
        self.assertEqual((acme("C", 105), "STO", 10, "MARKET", "WAITING_PARENT", p["order_id"], "0.01", p["package_id"]),
                         (c["instrument_id"], c["side"], c["qty"], c["order_type"], c["status"], c["parent_order_id"], c["ratio"], c["package_id"]))
        hp = self.q("SELECT strategy_key, fill_mode, primary_order_id FROM hedge_packages WHERE package_id=?", (p["package_id"],))[0]
        self.assertEqual(("CUSTOM", "SEQUENTIAL", p["order_id"]), (hp["strategy_key"], hp["fill_mode"], hp["primary_order_id"]))
        stored = json.loads(self.q("SELECT payload_json FROM commands WHERE request_id=?", (rid,))[0]["payload_json"])
        self.assertEqual("C1", stored["smart"]["item"])
        # a resend is a replay: no second package
        st, again, h2 = self.combo(1, legs, rev=2, clause="buy 1000 ACME limit 99 and sell 10 Dec 105 calls", preview_id=pv["preview_id"])
        self.assertEqual((200, "1"), (st, h2.get("X-Replayed")))
        self.assertEqual(2, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # the same id with an edited leg (11 calls) is another payload: 409 REQUEST_ID_REUSED, nothing written (an edit takes a new rev)
        st, out, _h = self.combo(1, [leg(acme("C", 105), "STO", 11), legs[1]], rev=2, clause="buy 1000 ACME limit 99 and sell 10 Dec 105 calls",
                                 preview_id=pv["preview_id"])
        self.assertEqual((409, "REQUEST_ID_REUSED"), (st, out["error"]["code"]))
        self.assertEqual(2, self.q("SELECT COUNT(*) n FROM orders")[0]["n"])
        # a print at 98.90 on the next completed bar (13:59, 4,000 shares): the resting 99.00 limit is crossed and fills at its limit, capped
        # at 10 % x 4,000 = 400 shares; the calls are resized to floor(400 x 0.01) = 4 and start working
        print_acme(rt, "98.90", 10, self.now - 60, 4000)
        self.assertEqual(1, rt.matcher.on_quotes(["ACME"]))
        p = self.q("SELECT * FROM orders WHERE order_id=?", (p["order_id"],))[0]
        c = self.q("SELECT * FROM orders WHERE order_id=?", (c["order_id"],))[0]
        self.assertEqual(("PARTIAL", 400), (p["status"], p["filled_qty"]))
        self.assertEqual([(p["order_id"], "BUY", 400, D("99.00"))], self.fills_of([p["order_id"]]))
        self.assertEqual(("WORKING", 4, 0), (c["status"], c["qty"], c["filled_qty"]))
        self.assertTrue(any("resized to 4 = floor(400" in e["note"] for e in self.q("SELECT note FROM order_events WHERE order_id=?", (c["order_id"],))))
        # the working calls fill at the bid 2.60 from the chain observation (20 displayed >= 4)
        self.assertEqual(1, rt.matcher.on_quotes([acme("C", 105)]))
        self.assertEqual([(c["order_id"], "STO", 4, D("2.60"))], self.fills_of([c["order_id"]]))
        pos = {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}
        self.assertEqual({"ACME": 400, acme("C", 105): -4}, pos)
        assert_integrity(self, rt, "full")


    def test_h_a_package_of_300_shares_and_one_call_releases_the_call(self):
        """1.0.7.2 review: the release is whole-number arithmetic on the leg's own quantity: 1 call for 300 shares (ratio 0.00333333 at 8
        decimals) is released once the shares have filled, never lost to floor(300 x 0.00333333) = 0."""
        legs = [leg("ACME", "BUY", 300), leg(acme("C", 105), "STO", 1)]
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 300,
                                                                       "order_type": "MARKET", "tif": "DAY"})
        self.assertEqual(200, st, pv)
        st, out, _h = self.combo(1, legs, clause="buy 300 ACME, sell 1 ACME Dec 18 2026 105 call as a package", preview_id=pv["preview_id"])
        self.assertEqual(200, st, out)
        rid = f"st:{TICKET}:C1:r1"
        p = self.q("SELECT * FROM orders WHERE request_id=?", (rid,))[0]
        c = self.q("SELECT * FROM orders WHERE request_id=?", (rid + ":H1",))[0]
        self.assertEqual(("FILLED", 300, "0.00333333"), (p["status"], p["filled_qty"], c["ratio"]))
        self.assertNotEqual("WAITING_PARENT", c["status"])
        self.assertEqual(1, int(c["qty"]))
        self.assertTrue(any(e["note"].startswith("resized to 1 = floor(300 ") and e["note"].endswith(" 1 / 300) after the primary's fill") for e in self.q("SELECT note FROM order_events WHERE order_id=?", (c["order_id"],))))
        assert_integrity(self, self.rt, "full")


class ReadAgainTests(SmartSubmitBase):
    def test_i_a_closing_leg_sent_then_read_again_is_the_order_that_went(self):
        """1.0.7.2 review R2: 'buy 2 ... calls' while short 2 reads buy to close; once that order is working (or filled), the same words
        read again are THAT order (ALREADY_SENT), never a new buy to open; a position wholly in working closing orders that other words
        name is ALREADY_CLOSING (a blocker), never silently an opening order."""
        c100 = acme("C", 100)
        st, out, _h = self.send({"account_id": ACCT, "instrument_id": c100, "side": "STO", "qty": 2, "order_type": "MARKET", "tif": "DAY"}, "ui:readagain-seed-0001")
        self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)
        text = "buy 2 ACME Dec 18 2026 100 calls limit 1.00"

        def parse(t):
            st_, ans, _h_ = self.call("POST", "/api/smart-ticket/parse?book=T", {"text": t, "book": "T"})
            self.assertEqual(200, st_, ans)
            return ans["legs"][0]
        first = parse(text)
        self.assertEqual(("BTC", 2), (first["side"], first["qty"]))
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(first["request"], confirm_large=False))
        self.assertEqual(200, st, pv)
        st, sent, _h = self.send(dict(first["request"], preview_id=pv["preview_id"], confirm_large=False, smart={"ticket": TICKET, "item": "L1", "clause": first["clause"]}),
                                 f"st:{TICKET}:L1:r1")
        self.assertEqual((200, "WORKING"), (st, sent["order"]["status"]), sent)
        again = parse(text)
        self.assertEqual(first["request"], again["request"])
        self.assertIn("ALREADY_SENT", [f["code"] for f in again["flags"]])
        other = parse("buy 2 ACME Dec 18 2026 100 calls limit 1.05")                     # other words: the short is already being closed
        self.assertEqual((None, None), (other["side"], other["request"]))
        f = next(f for f in other["flags"] if f["code"] == "ALREADY_CLOSING")
        self.assertEqual(("blocker", ["bto 2 ACME Dec 18 2026 100 calls limit 1.05"]), (f["severity"], [ch["clause"] for ch in f["choices"]]))
        assert_integrity(self, self.rt, "full")

if __name__ == "__main__":
    unittest.main()
