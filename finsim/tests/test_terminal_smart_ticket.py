"""Smart Ticket: the resolver and the pure route (FinSim 1.0.7.2, CHANGE_PLAN r1072 2.6; design FINSIM_TERMINAL_GAPS 3.4-3.6).

Two runtimes, both in temp homes on the fictional world of ``tests/terminal_smart_corpus.py`` (ACME, WIDG, GIZM, ... invented names
and numbers; the clock is Tuesday 2026-10-20 14:00 ET):

- ``make_runtime(books="real")`` plus a ``QuoteCache`` / ``ChainCache`` (as ``tests/test_terminal_strategies.py``): the resolver's
  rules one by one - books (R-BOOK-1..5, a renamed book, an ambiguous book word), accounts (R-ACCT-1 / 2, ACCOUNT_CHOICE, the page's
  default account), options from the cached chain / the master's rows / built and flagged OPTION_UNVERIFIED, never snapped,
  month-only expiries, index roots and ROOT_CHOICE, futures by month, year and active month, every row of G 3.5.3 with positions
  seeded through ``terminal_helpers.buy`` / ``sell`` (and a test-only opening fill for shorts and options), the products with their
  own ticket, the request bodies and the combinations.  This bundle has no options / lending module: the guarded lookups fall back.
- ``server.build_runtime(..., offline=True, fetch_threads=0)`` (as ``tests/test_terminal_api.py``): the route - registered NOT
  mutating, no request id needed, the 400s - and THE PURITY PROOF over the whole corpus through ``rt.router.handle``: the state
  version, ``PRAGMA data_version`` on a separately held connection, the instrument count, the chain cache's LRU order and pending list,
  the quote cache's pins and every corpus symbol's priorities, and the preview store are equal before and after, and instance-level
  spies on every function of CHANGE_PLAN 2.2's never-called list record zero calls.  Every corpus case's expectation is checked on
  the same pass; a 20-leg instruction parses in under 300 ms (deviation D-f; the measured time is printed).
Nothing touches the network or ``~/.finsim``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date
from decimal import Decimal

from tests import terminal_smart_corpus as corpus
from tests.terminal_helpers import _Log, _h_test_buy, buy, execute, make_runtime, sell
from finsim.terminal import api as _api, api_products as _api_products, books as _books, config, orders_preview as _orders_preview, server as _server
from finsim.terminal import smart_ticket as st, tables
from finsim.terminal.clock import FakeClock
from finsim.terminal.db import next_id
from finsim.terminal.execution import LONG_GL, SHORT_GL
from finsim.terminal.ledger import Entry, cr, dr, open_lot
from finsim.terminal.quotes import QuoteRecord
from finsim.terminal.quotes.cache import ChainCache, QuoteCache
from finsim.terminal.quotes.parsers import parse_chain
from finsim.terminal.quotes.providers import FakeProvider

TODAY = date(2026, 10, 20)
P, F, I, PB = corpus.P, corpus.F, corpus.I, corpus.PB
C240, C250 = corpus.C240, corpus.C250


# ---- the fixture world --------------------------------------------------------------------------------------------------------------
def _h_open(ctx, payload):
    """A test-only opening fill (no order, no matcher) of a LONG or SHORT lot at ``price``: the position GLs of ``execution.LONG_GL`` /
    ``SHORT_GL`` against the T+1 settlement GL, the settlement row, and for a short equity a live borrow without collateral, so the
    command's scoped integrity checks hold.  ``{"account_id", "instrument_id", "side": "LONG" | "SHORT", "qty", "price"}``."""
    aid, iid, side = payload["account_id"], payload["instrument_id"], payload["side"]
    qty = int(payload["qty"])
    px = Decimal(str(payload["price"]))
    inst = ctx.services.instruments.get(iid, ctx.conn)
    gross = inst.contract_value_c(px, qty)
    fill_id = next_id(ctx.conn, "fill")
    td = ctx.clock.today()
    if side == "LONG":
        lines = [dr(aid, LONG_GL[inst.kind], "USD", gross, instrument_id=iid), cr(aid, "2100", "USD", gross)]
    else:
        lines = [dr(aid, "1200", "USD", gross), cr(aid, SHORT_GL[inst.kind], "USD", gross, instrument_id=iid)]
    entry_id = ctx.post(Entry("FILL", f"test opening fill {side} {qty} {iid}", lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
    ctx.conn.execute("INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
                     " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fill_id, None, aid, iid, "BUY" if side == "LONG" else "SELL", qty, str(px), gross, 0, "USD", "1", ctx.ts, td.isoformat(), td.isoformat(),
                      "ORDER", "last", "{}", None, None, None, entry_id, f"test:{fill_id}"))
    open_lot(ctx.tx, aid, iid, side, fill_id, qty, px, gross, "USD")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (next_id(ctx.conn, "settlement"), fill_id, None, aid, "CASH", "USD", -gross if side == "LONG" else gross, "2100" if side == "LONG" else "1200",
                      ctx.cal.settlement_date(td, inst.settle_days).isoformat()))
    if side == "SHORT" and inst.kind != "OPTION":
        ctx.conn.execute("INSERT INTO borrows(borrow_id, command_id, account_id, instrument_id, qty, fee_rate, rebate_rate, tier, lender, collateral_c, currency,"
                         " opened_ts, opened_date) VALUES (?,?,?,?,?,'0.0025','0','GC','test lender',0,'USD',?,?)",
                         (f"B-{fill_id}", ctx.command_id, aid, iid, qty, ctx.ts, td.isoformat()))
    ctx.touched_instruments.add((aid, iid))
    return {"fill_id": fill_id}


def build_world(tc, rt, world=corpus.WORLD):
    """``corpus.WORLD`` into ``rt``: master rows and the prime-broker account in one writer, the chains (Cboe-shaped payloads through
    ``parse_chain``), the positions (long equities through ``terminal_helpers.buy``, the rest through the opening fill above), the
    working order and the quote."""
    if "test.buy" not in rt.bus.kinds():
        rt.bus.register("test.buy", _h_test_buy)
    if "test.smart.open" not in rt.bus.kinds():
        rt.bus.register("test.smart.open", _h_open)
    now = int(rt.clock.epoch())
    with rt.db.writer() as tx:
        for row in world["instruments"]:
            rt.instruments.upsert(tx, **row)
        for a in world["accounts"]:
            _books._insert_account(tx.conn, int(tx.now.timestamp()), a["book"], a["lifetime"], a["role"], a["name"], a["kind"], a["sort"], broker=a["broker"])
    rt.instruments.load()
    for ch in world["chains"]:
        payload = corpus.chain_payload(ch["underlying"] if "root" not in ch else ch["root"], ch["spot"], ch["expiries"], ch["strikes"], ch["trade_time"],
                                       skip=ch["skip"], extra=ch["extra"])
        rt.chains.put(parse_chain(payload, ch["cboe"], ch["underlying"], TODAY, now - int(ch["age_s"])))
    for aid, iid, side, qty, px in world["positions"]:
        inst = rt.instruments.get(iid)
        if side == "LONG" and inst.kind != "OPTION":
            res = buy(rt, aid, iid, qty, px)
        else:
            res = execute(rt, "test.smart.open", {"account_id": aid, "instrument_id": iid, "side": side, "qty": qty, "price": px})
        tc.assertTrue(res.ok, f"fixture position {aid} {iid}: {res.body}")
    with rt.db.writer() as tx:
        for o in world["working"]:
            tx.conn.execute("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, limit_px, tif, status, session_group,"
                            " created_ts, updated_ts, expires_ts, preview_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'{}')",
                            (o["order_id"], o["request_id"], o["account_id"], o["instrument_id"], o["side"], o["qty"], o["order_type"], o["limit_px"], o["tif"],
                             o["status"], o["session_group"], now, now, now + 7200))
    for q in world["quotes"]:
        rt.cache.put(QuoteRecord(q["instrument_id"], "yahoo", f"Yahoo Finance chart ({q['instrument_id']})", "direct", last=Decimal(q["last"]), bid=Decimal(q["bid"]),
                                 ask=Decimal(q["ask"]), mid=Decimal(q["mid"]), quote_time=now - int(q["age_s"]), fetched_at=now - 10, session_group="US_EQUITY"), now)


def codes(flags):
    return {f["code"] for f in flags}


def all_codes(out):
    return codes([f for l in out["legs"] for f in l["flags"]] + [f for c in out["combos"] for f in c["flags"]] + out["flags"])


def flag(leg_or_combo, code):
    for f in leg_or_combo["flags"]:
        if f["code"] == code:
            return f
    raise AssertionError(f"no {code} flag in {[f['code'] for f in leg_or_combo['flags']]}")


def case_problems(case, status, body):
    """What does not match a corpus case's ``expect`` (an empty list when everything does)."""
    exp = case["expect"]
    out = []
    want_status = exp.get("status", 200)
    if status != want_status:
        return [f"status {status} (want {want_status}): {json.dumps(body)[:300]}"]
    if status != 200:
        if body.get("error", {}).get("code") != exp.get("error"):
            out.append(f"error {body.get('error')} (want {exp.get('error')})")
        return out
    for key in ("grammar", "selected_book", "text", "ok", "counts", "legs", "combos", "unparsed", "flags", "as_of"):
        if key not in body:
            out.append(f"answer lacks {key}")
    if out:
        return out
    if "ok" in exp and body["ok"] != exp["ok"]:
        out.append(f"ok {body['ok']} (want {exp['ok']}); codes {sorted(all_codes(body))}")
    for kind in ("legs", "combos"):
        want = exp.get(kind)
        got = body[kind]
        if want is None:
            continue
        if isinstance(want, int):
            if len(got) != want:
                out.append(f"{len(got)} {kind} (want {want})")
            continue
        if len(got) != len(want):
            out.append(f"{len(got)} {kind} (want {len(want)}): {[(l.get('side'), (l.get('instrument') or {}).get('id')) for l in got] if kind == 'legs' else got}")
            continue
        for n, (w, g) in enumerate(zip(want, got), 1):
            for k, v in w.items():
                if k == "codes":
                    miss = set(v) - codes(g["flags"])
                    if miss:
                        out.append(f"{kind[:-1]} {n} lacks {sorted(miss)} (has {sorted(codes(g['flags']))})")
                elif k == "id":
                    if g["instrument"]["id"] != v:
                        out.append(f"leg {n} instrument {g['instrument']['id']} (want {v})")
                elif k == "request":
                    if v is True and g["request"] is None:
                        out.append(f"{kind[:-1]} {n} has no request ({sorted(codes(g['flags']))})")
                    elif v is False and g["request"] is not None:
                        out.append(f"{kind[:-1]} {n} has a request {g['request']}")
                    elif isinstance(v, dict) and g["request"] != v:
                        out.append(f"{kind[:-1]} {n} request {g['request']} (want {v})")
                elif g.get(k) != v:
                    out.append(f"{kind[:-1]} {n} {k} {g.get(k)!r} (want {v!r})")
    have = all_codes(body)
    miss = set(exp.get("codes", ())) - have
    if miss:
        out.append(f"lacks {sorted(miss)} (has {sorted(have)})")
    extra = set(exp.get("absent", ())) & have
    if extra:
        out.append(f"carries {sorted(extra)}")
    miss = set(exp.get("top", ())) - codes(body["flags"])
    if miss:
        out.append(f"top lacks {sorted(miss)} (has {sorted(codes(body['flags']))})")
    return out


# ===== the resolver on make_runtime(books="real") =========================================================================================
class _ResolverBase(unittest.TestCase):
    rt = None

    @classmethod
    def _make(cls):
        tmp = tempfile.mkdtemp(prefix="fst-smart-res-")
        rt = make_runtime(tmp, books="real")
        rt.cache = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, rt.bus, rt.log)
        rt.chains = ChainCache(rt.clock, max_entries=12)
        return tmp, rt

    def parse(self, text, book="P", **body):
        return st.parse_view(self.rt, dict({"text": text, "book": book}, **body))

    def one(self, text, book="P", **body):
        out = self.parse(text, book, **body)
        self.assertEqual(1, len(out["legs"]), out)
        return out["legs"][0]


class ResolverRules(_ResolverBase):
    """The read-only rules on a shared world (no test here writes)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = cls._make()
        tc = unittest.TestCase()
        rt = cls.rt
        build_world(tc, rt, dict(corpus.WORLD, positions=[p for p in corpus.WORLD["positions"] if p[1] != "WIDG"]))
        assert buy(rt, P, "WIDG", 400, "40").ok                      # the helpers' long fill and FIFO sale: 400 - 100 = 300 WIDG held
        assert sell(rt, P, "WIDG", 100, "41").ok

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- the answer's shape ------------------------------------------------------------------------------------------------------------
    def test_answer_shape_and_request_body(self):
        out = self.parse("buy 200 ACME limit 225")
        self.assertEqual(("smart-ticket/1", "P", "buy 200 ACME limit 225", True), (out["grammar"], out["selected_book"], out["text"], out["ok"]))
        self.assertEqual(int(self.rt.clock.epoch()), out["as_of"])
        self.assertEqual({"legs": 1, "combos": 0, "blockers": 0, "checks": 0, "infos": 2}, out["counts"])
        leg = out["legs"][0]
        for k in ("n", "span", "clause", "combo", "book", "book_name", "lifetime_id", "account_id", "account_name", "instrument", "position", "side", "qty",
                  "order_type", "limit_px", "stop_px", "trail_amt", "trail_pct", "tif", "gtd_date", "flags", "inferred", "request"):
            self.assertIn(k, leg)
        self.assertEqual((1, [0, 22], "buy 200 ACME limit 225", None, "P", "Personal Book", "P1", P, "US Exchange"),
                         (leg["n"], leg["span"], leg["clause"], leg["combo"], leg["book"], leg["book_name"], leg["lifetime_id"], leg["account_id"], leg["account_name"]))
        self.assertEqual({"id": "ACME", "symbol": "ACME", "kind": "EQUITY", "lot": 1, "tick": "0.01", "underlying_id": None, "expiry": None, "strike": None, "right": None,
                          "listed": True, "words": "ACME equity (Acme Corp)", "chain": None}, leg["instrument"])
        self.assertEqual({"qty": 0, "working_sells": 0, "working_covers": 0, "reserved": 0, "lent": 0, "free": 0}, leg["position"])
        self.assertEqual({"account_id": P, "instrument_id": "ACME", "side": "BUY", "qty": 200, "order_type": "LIMIT", "limit_px": "225", "tif": "DAY"}, leg["request"])
        self.assertIn("R-TIF: DAY", leg["inferred"])
        self.assertEqual([{"code": "BOOK_SELECTED", "severity": "info", "text": "no book named: the selected Personal Book"},
                          {"code": "TIF_DEFAULT", "severity": "info", "text": "time in force DAY (none given)"}], leg["flags"])
        self.assertEqual(([], [], []), (out["combos"], out["unparsed"], out["flags"]))

    def test_flags_sorted_and_answer_deterministic(self):
        text = "buy 200 ACME limit 225, sell 2 ACME Dec 18 2026 240 calls, and buy 50 WIDG at market in the Fund book"
        a, b = self.parse(text), self.parse(text)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        for leg in a["legs"]:
            keys = [(st.SEVERITY_RANK[f["severity"]], f["code"], (f.get("span") or [-1, -1])) for f in leg["flags"]]
            self.assertEqual(sorted(keys), keys)
        self.assertEqual([1, 2, 3], [l["n"] for l in a["legs"]])                       # text order

    def test_selected_book_defaults_to_the_first_open_book(self):
        out = st.parse_view(self.rt, {"text": "buy 1 ACME"})
        self.assertEqual("P", out["selected_book"])
        out = st.parse_view(self.rt, {"text": "buy 1 ACME"}, "F")                       # the ?book= query when the body names none
        self.assertEqual(("F", F), (out["selected_book"], out["legs"][0]["account_id"]))
        with self.assertRaises(st.RejectError) as cm:
            st.parse_view(self.rt, {"text": "buy 1 ACME", "book": "Z"})
        self.assertEqual(("UNKNOWN_BOOK", 404), (cm.exception.code, cm.exception.http))

    # ---- books (R-BOOK-1..5) -------------------------------------------------------------------------------------------------------------
    def test_book_rules(self):
        leg = self.one("buy 100 ACME in the Fund book")                                 # R-BOOK-1: the clause's own phrase, no inference flag
        self.assertEqual(("F", F), (leg["book"], leg["account_id"]))
        self.assertNotIn("BOOK_FROM_SCOPE", codes(leg["flags"]))
        self.assertIn("OTHER_BOOK", codes(leg["flags"]))                                 # SD-4: allowed, said
        out = self.parse("in the Fund book: buy 100 ACME; buy 50 WIDG")                 # R-BOOK-2: information
        self.assertEqual([("F", "info")] * 2, [(l["book"], flag(l, "BOOK_FROM_SCOPE")["severity"]) for l in out["legs"]])
        out = self.parse("buy 100 ACME; sell 2 ACME Dec 18 2026 260 calls; buy 50 WIDG in the Fund book")      # R-BOOK-3 / SD-2
        self.assertEqual(["F", "F", "F"], [l["book"] for l in out["legs"]])
        self.assertEqual(["check", "check", "info"], [flag(l, "BOOK_FROM_SCOPE")["severity"] for l in out["legs"]])
        out = self.parse("buy 100 ACME in the Personal book; buy 50 WIDG in the Fund book")                    # each its own: no inference
        self.assertEqual(["P", "F"], [l["book"] for l in out["legs"]])
        self.assertFalse(any("BOOK_FROM_SCOPE" in codes(l["flags"]) for l in out["legs"]))
        out = self.parse("buy 100 ACME in the Personal book; buy 50 WIDG; sell 10 GIZM in the Fund book")      # SD-2: every leg naming none
        self.assertEqual(["P", "F", "F"], [l["book"] for l in out["legs"]])
        leg = self.one("buy 100 ACME")                                                   # R-BOOK-4
        self.assertEqual(("P", "BOOK_SELECTED"), (leg["book"], flag(leg, "BOOK_SELECTED")["code"]))
        out = self.parse("buy 100 ACME in the Personal and Fund books")                  # R-BOOK-5: repeated at the same quantity
        self.assertEqual([("P", 100), ("F", 100)], [(l["book"], l["qty"]) for l in out["legs"]])
        self.assertTrue(all("MULTI_BOOK" in codes(l["flags"]) for l in out["legs"]))
        self.assertIn("MULTI_BOOK", codes(out["flags"]))
        for text in ("buy 100 ACME for the Fund", "buy 100 ACME in F", "buy 100 ACME in the fund books", "Fund book: buy 100 ACME"):
            self.assertEqual("F", self.parse(text)["legs"][0]["book"], text)

    def test_account_rules(self):
        leg = self.one("buy 100 ACME in the Institutional book")                        # R-ACCT-1: US Exchange first, the PB listed
        self.assertEqual(I, leg["account_id"])
        f = flag(leg, "ACCOUNT_CHOICE")
        self.assertEqual(("check", [0, 38]), (f["severity"], f["span"]))
        self.assertEqual([{"label": "Morgan Stanley PB (I1:PB_MS)", "clause": "buy 100 ACME in the Institutional book at pb ms"}], f["choices"])
        self.assertEqual(PB, self.parse(f["choices"][0]["clause"])["legs"][0]["account_id"])         # the choice re-parses to that account
        self.assertNotIn("ACCOUNT_CHOICE", codes(self.one("buy 100 ACME")["flags"]))                 # one equity account in P
        self.assertEqual(PB, self.one("buy 100 ACME in the Institutional book at prime broker ms")["account_id"])
        self.assertEqual(PB, self.one("buy 100 ACME", book="I", account_id=PB)["account_id"])       # the page's default account
        self.assertEqual(I, self.one("buy 100 ACME", book="I", account_id=P)["account_id"])         # ... only in its own book
        leg = self.one("buy 100 ACME at pb ms")                                          # R-ACCT-2
        self.assertEqual(("ACCOUNT_NOT_IN_BOOK", None), (flag(leg, "ACCOUNT_NOT_IN_BOOK")["code"], leg["request"]))
        leg = self.one("buy 100 ACME in F1:US_EXCHANGE")
        f = flag(leg, "ACCOUNT_NOT_IN_BOOK")
        self.assertEqual(["buy 100 ACME in F1:US_EXCHANGE in the Fund book"], [c["clause"] for c in f["choices"]])
        self.assertEqual(F, self.parse(f["choices"][0]["clause"])["legs"][0]["account_id"])
        self.assertIn("NOT_TRADEABLE_HERE", codes(self.one("buy 100 ACME in the Fund book at F1:DEBT")["flags"]))

    # ---- instruments ---------------------------------------------------------------------------------------------------------------------
    def test_options_from_the_chain_the_master_or_unverified(self):
        leg = self.one("buy 1 ACME Dec 18 2026 260 call")                               # the cached chain
        ins = leg["instrument"]
        self.assertEqual(("ACME261218C00260000", "OPTION", "ACME", "2026-12-18", "260", "C", True, "ACME 2026-12-18 260 call"),
                         (ins["id"], ins["kind"], ins["underlying_id"], ins["expiry"], ins["strike"], ins["right"], ins["listed"], ins["words"]))
        self.assertEqual("delayed", ins["chain"]["quality"])
        self.assertEqual({"quality", "quote_time", "fetched_at", "reason"}, set(ins["chain"]))
        leg = self.one("buy 1 WIDG Dec 18 2026 40 put")                                  # a master row (no chain cached)
        self.assertEqual(("WIDG261218P00040000", True, None), (leg["instrument"]["id"], leg["instrument"]["listed"], leg["instrument"]["chain"]))
        leg = self.one("buy 1 WIDG Dec 18 2026 45 put")                                  # neither: built, flagged, never invented as listed
        f = flag(leg, "OPTION_UNVERIFIED")
        self.assertEqual(("WIDG261218P00045000", False, "check"), (leg["instrument"]["id"], leg["instrument"]["listed"], f["severity"]))
        self.assertEqual({"kind": "load_chain", "label": "Load the chain", "instrument_id": "WIDG"}, f["action"])
        self.assertIsNotNone(leg["request"])                                             # a check: the preview refuses NOT_LISTED until the chain lists it
        self.assertIsNone(self.rt.instruments.get("WIDG261218P00045000"))
        leg = self.one("buy 1 GIZM Dec 18 2026 50 call")                                 # a stale chain
        self.assertEqual(("stale", "load_chain"), (leg["instrument"]["chain"]["quality"], flag(leg, "CHAIN_STALE")["action"]["kind"]))

    def test_never_snapped(self):
        leg = self.one("buy 1 ACME Dec 18 2026 245 call")
        f = flag(leg, "STRIKE_NOT_LISTED")
        self.assertEqual((None, None, "blocker"), (leg["instrument"]["id"], leg["request"], f["severity"]))
        self.assertEqual(["buy 1 ACME Dec 18 2026 240 call", "buy 1 ACME Dec 18 2026 250 call"], [c["clause"] for c in f["choices"]])
        leg = self.one("buy 1 ACME Dec 18 2026 215 call")                                # a one-sided strike: the 215 put is listed, the call is not
        self.assertEqual(["buy 1 ACME Dec 18 2026 210 call", "buy 1 ACME Dec 18 2026 220 call"], [c["clause"] for c in flag(leg, "STRIKE_NOT_LISTED")["choices"]])
        self.assertEqual("ACME261218P00215000", self.one("buy 1 ACME Dec 18 2026 215 put")["instrument"]["id"])
        leg = self.one("buy 1 ACME Dec 19 2026 240 call")                                # D-i: Dec 19 is not listed; Dec 18 offered
        self.assertEqual(["buy 1 ACME 2026-12-18 240 call", "buy 1 ACME 2027-01-15 240 call"], [c["clause"] for c in flag(leg, "EXPIRY_NOT_LISTED")["choices"]])
        leg = self.one("sell 1 ACME 245c 12/18/26")
        self.assertEqual(["sell 1 ACME 240c 12/18/26", "sell 1 ACME 250c 12/18/26"], [c["clause"] for c in flag(leg, "STRIKE_NOT_LISTED")["choices"]])

    def test_month_only_expiries(self):
        leg = self.one("buy 1 ACME Jan 2027 240 call")
        self.assertEqual(("ACME270115C00240000", "check"), (leg["instrument"]["id"], flag(leg, "EXPIRY_FROM_MONTH")["severity"]))
        leg = self.one("buy 1 ACME Feb 2027 240 call")                                   # none listed that month: the nearest offered
        self.assertEqual(["buy 1 ACME 2027-01-15 240 call"], [c["clause"] for c in flag(leg, "EXPIRY_MONTH_ONLY")["choices"]])
        leg = self.one("buy 1 WIDG Dec 2026 40 put")                                     # no chain: the dates need it
        self.assertEqual("load_chain", flag(leg, "EXPIRY_MONTH_ONLY")["action"]["kind"])

    def test_index_roots_and_root_choice(self):
        leg = self.one("buy 1 SPX Dec 18 2026 6000 call")
        f = flag(leg, "ROOT_CHOICE")
        self.assertEqual((None, "blocker"), (leg["request"], f["severity"]))
        self.assertEqual(["buy 1 SPX261218C06000000", "buy 1 SPXW261218C06000000"], [c["clause"] for c in f["choices"]])       # the standard root first (D-h)
        self.assertEqual(("SPXW261218C06000000", "SPX"), (self.one(f["choices"][1]["clause"])["instrument"]["id"], leg["instrument"]["underlying_id"]))
        leg = self.one("buy 1 SPXW Dec 18 2026 6000 call")                               # the weekly root typed: no choice to make
        self.assertEqual(("SPXW261218C06000000", "SPX 2026-12-18 6000 call (root SPXW)"), (leg["instrument"]["id"], leg["instrument"]["words"]))
        self.assertEqual("SPX261218C05900000", self.one("buy 1 SPX Dec 18 2026 5900 call")["instrument"]["id"])
        leg = self.one("buy 1 ACME Dec 18 2026 200 put")                                 # an adjusted root on one contract
        self.assertEqual(["buy 1 ACME261218P00200000", "buy 1 ACME1261218P00200000"], [c["clause"] for c in flag(leg, "ROOT_CHOICE")["choices"]])

    def test_futures_month_year_and_active(self):
        leg = self.one("buy 2 ES Mar")
        self.assertEqual(("ESH27", "FUTURE", "BUY"), (leg["instrument"]["id"], leg["instrument"]["kind"], leg["side"]))
        self.assertIn("YEAR_INFERRED", codes(leg["flags"]))
        self.assertEqual("ESH27", self.one("buy 2 ES Mar 2027")["instrument"]["id"])
        self.assertEqual("ESM27", self.one("buy 2 ES Jun '27")["instrument"]["id"])
        leg = self.one("sell 1 active ES stop 6500")
        self.assertEqual(("ESZ26", "SELL", "STOP", "6500"), (leg["instrument"]["id"], leg["side"], leg["order_type"], leg["stop_px"]))
        self.assertIn("front month", flag(leg, "EXPIRY_FROM_MONTH")["text"])
        leg = self.one("buy 1 ESZ29")
        self.assertEqual(["buy 1 ESZ26", "buy 1 ESH27", "buy 1 ESM27", "buy 1 ESU27"], [c["clause"] for c in flag(leg, "EXPIRY_NOT_LISTED")["choices"]])
        self.assertEqual(("SELL", 0), (self.one("sell 1 ESZ26")["side"], self.one("sell 1 ESZ26")["position"]["qty"]))

    def test_position_aware_sides_every_row_of_g_3_5_3(self):
        rows = [
            ("buy 2 ACME Dec 18 2026 240 calls", "BTC", "SIDE_INFERRED"),          # buy option, short >= qty
            ("buy 3 ACME Dec 18 2026 240 calls", None, "SIDE_SPLIT"),              # buy option, short < qty
            ("buy 1 ACME Dec 18 2026 260 call", "BTO", None),                      # buy option, flat
            ("buy 1 ACME Dec 18 2026 250 call", "BTO", None),                      # buy option, long
            ("sell 3 ACME Dec 18 2026 250 calls", "STC", "SIDE_INFERRED"),         # sell option, long >= qty
            ("sell 4 ACME Dec 18 2026 250 calls", None, "SIDE_SPLIT"),             # sell option, long < qty
            ("sell 1 ACME Dec 18 2026 260 call", "STO", "SIDE_INFERRED"),          # sell option, flat (SD-3)
            ("sell 1 ACME Dec 18 2026 240 call", "STO", "SIDE_INFERRED"),          # sell option, short
            ("buy 200 GIZM", "BUY_TO_COVER", "SIDE_INFERRED"),                     # buy shares, short
            ("buy 201 GIZM", None, "SIDE_SPLIT"),                                  # buy shares, short: the rest needs a choice
            ("buy 10 ACME", "BUY", None),                                          # buy shares, flat
            ("buy 10 WIDG", "BUY", None),                                          # buy shares, long
            ("sell 200 WIDG", "SELL", None),                                       # sell shares, free >= qty (300 held - 100 working)
            ("sell 201 WIDG", None, "WOULD_OVERSELL"),                             # sell shares, less free
            ("buy 1 ESZ26", "BUY", None), ("sell 1 ESZ26", "SELL", None),          # futures net
            ("close WIDG", "SELL", "SIDE_INFERRED"), ("close GIZM", "BUY_TO_COVER", "SIDE_INFERRED"), ("close ACME", None, "NOTHING_HELD"),
        ]
        for text, side, code in rows:
            leg = self.one(text)
            self.assertEqual(side, leg["side"], text)
            if code:
                self.assertIn(code, codes(leg["flags"]), text)
            else:
                self.assertNotIn("SIDE_INFERRED", codes(leg["flags"]), text)
            self.assertEqual(side is None, leg["request"] is None, text)
        f = flag(self.one("buy 3 ACME Dec 18 2026 240 calls"), "SIDE_SPLIT")
        self.assertEqual(["btc 2 ACME Dec 18 2026 240 calls and bto 1 ACME Dec 18 2026 240 calls", "btc 2 ACME Dec 18 2026 240 calls"], [c["clause"] for c in f["choices"]])
        out = self.parse(f["choices"][0]["clause"])
        self.assertEqual([("BTC", 2), ("BTO", 1)], [(l["side"], l["qty"]) for l in out["legs"]])
        f = flag(self.one("sell 201 WIDG"), "WOULD_OVERSELL")
        self.assertEqual(["sell 200 WIDG", "sell 200 WIDG and short 1 WIDG"], [c["clause"] for c in f["choices"]])
        self.assertIn("100 in working sells", f["text"])
        self.assertEqual(["short 10 ACME"], [c["clause"] for c in flag(self.one("sell 10 ACME"), "WOULD_OVERSELL")["choices"]])
        for text, side in (("short 10 ACME", "SELL_SHORT"), ("cover 10 GIZM", "BUY_TO_COVER"), ("bto 1 ACME Dec 18 2026 240 call", "BTO"),
                           ("sto 1 ACME Dec 18 2026 250 call", "STO"), ("btc 1 ACME Dec 18 2026 260 call", "BTC")):
            leg = self.one(text)                                                         # explicit sides are never changed (the preview judges them)
            self.assertEqual(side, leg["side"], text)
            self.assertNotIn("SIDE_INFERRED", codes(leg["flags"]), text)

    def test_quantities_all_half_k_m_and_lots(self):
        self.assertEqual(("BTC", 2), (self.one("buy all ACME Dec 18 2026 240 calls")["side"], self.one("buy all ACME Dec 18 2026 240 calls")["qty"]))
        leg = self.one("sell half ACME Dec 18 2026 250 calls")                          # floor(3 / 2) = 1: rounded
        self.assertEqual((1, "check"), (leg["qty"], flag(leg, "QTY_ROUNDED")["severity"]))
        leg = self.one("sell half WIDG")                                                 # 300 / 2 = 150 exactly: no rounding flag
        self.assertEqual(150, leg["qty"])
        self.assertNotIn("QTY_ROUNDED", codes(leg["flags"]))
        self.assertIn("NOTHING_HELD", codes(self.one("sell all ACME")["flags"]))
        self.assertEqual(2500, self.one("buy 2.5k ACME")["qty"])
        self.assertEqual(1000000, self.one("buy 1m ACME")["qty"])
        self.assertIn("QTY_NOT_WHOLE", codes(self.one("buy 1.2345k ACME")["flags"]))
        leg = self.one("buy 15 LOTX")
        f = flag(leg, "QTY_LOT")
        self.assertEqual((None, ["buy 10 LOTX", "buy 20 LOTX"]), (leg["request"], [c["clause"] for c in f["choices"]]))

    def test_prices_from_quotes(self):
        leg = self.one("buy 2 ACME Dec 18 2026 260 calls at the ask")
        self.assertEqual(("LIMIT", "2.05"), (leg["order_type"], leg["limit_px"]))     # the chain's ask: intrinsic 0 + 2.00 + 0.05
        self.assertIn("13:59:00 ET", flag(leg, "PRICE_FROM_QUOTE")["text"])
        self.assertEqual("40.05", self.one("buy 100 WIDG at the bid")["limit_px"])
        self.assertEqual("40.10", self.one("buy 100 WIDG at the mid")["limit_px"])
        leg = self.one("buy 100 ACME at the bid")                                        # no quote cached: never a guessed price
        self.assertEqual((None, None), (leg["limit_px"], leg["request"]))
        self.assertIn("MISSING_PRICE", codes(leg["flags"]))
        self.assertEqual("1225.50", self.one("buy 100 ACME limit $1,225.50")["limit_px"])         # copied as typed, never rounded
        self.assertEqual("41.200", self.one("sell 100 WIDG limit 41.200")["limit_px"])

    def test_own_ticket_products(self):
        leg = self.one("buy 1m EURUSD")
        f = flag(leg, "KIND_OWN_TICKET")
        self.assertEqual((None, "blocker"), (leg["request"], f["severity"]))
        self.assertEqual({"kind": "open_ticket", "label": "Open the FX desk", "ticket": {"kind": "FX", "account_id": "P1:FOREIGN_FX", "instrument_id": "EURUSD",
                                                                                        "buy_ccy": "EUR", "sell_ccy": "USD", "amount": "1000000"}}, f["action"])
        f = flag(self.one("sell 2m EUR/USD"), "KIND_OWN_TICKET")
        self.assertEqual(("USD", "EUR", "2000000"), (f["action"]["ticket"]["buy_ccy"], f["action"]["ticket"]["sell_ccy"], f["action"]["ticket"]["amount"]))
        f = flag(self.one("buy 10 ACME-31"), "KIND_OWN_TICKET")
        self.assertEqual(("Open the bond ticket", "BOND", "P1:DEBT"), (f["action"]["label"], f["action"]["ticket"]["kind"], f["action"]["ticket"]["account_id"]))
        f = flag(self.one("buy 10 PRV-WIDGLABS", book="I"), "KIND_OWN_TICKET")
        self.assertEqual(("PRIVATE", "I1:PRIVATE"), (f["action"]["ticket"]["kind"], f["action"]["ticket"]["account_id"]))

    def test_symbols_unknown_ambiguous_and_the_slot_rule(self):
        leg = self.one("buy 100 NOSUCH")
        self.assertEqual({"kind": "resolve", "label": "Look it up", "symbol": "NOSUCH"}, flag(leg, "SYMBOL_UNKNOWN")["action"])
        f = flag(self.one("buy 100 ZORB"), "SYMBOL_AMBIGUOUS")
        self.assertEqual(["buy 100 $ZORB", "buy 100 $ZORB-W"], [c["clause"] for c in f["choices"]])
        self.assertEqual("ZORB-W", self.one(f["choices"][1]["clause"])["instrument"]["id"])
        f = flag(self.one("buy 100 q"), "MISSING_INSTRUMENT")                           # a one-letter ticker needs capitals or $
        self.assertEqual(["buy 100 $Q"], [c["clause"] for c in f["choices"]])
        self.assertEqual("Q", self.one("buy 100 $Q")["instrument"]["id"])
        self.assertEqual("Q", self.one("buy 100 Q")["instrument"]["id"])

    # ---- combinations ----------------------------------------------------------------------------------------------------------------------
    def test_combinations_and_their_requests(self):
        out = self.parse("buy 2 ACME Dec 18 2026 220/240 strangle")
        cb = out["combos"][0]
        self.assertEqual(("C1", [1, 2], "AON", "CUSTOM", "ACME", "strangle", "P", P), (cb["id"], cb["legs"], cb["fill_mode"], cb["kind"], cb["underlying_id"], cb["strategy"],
                                                                                      cb["book"], cb["account_id"]))
        self.assertEqual({"account_id": P, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": [
            {"instrument_id": "ACME261218P00220000", "side": "BTO", "qty": 2}, {"instrument_id": "ACME261218C00240000", "side": "BTO", "qty": 2}]}}, cb["request"])
        self.assertTrue(all(l["combo"] == "C1" and l["request"] is not None for l in out["legs"]))      # each leg keeps its own body (per-leg view)
        self.assertNotIn("TYPE_DEFAULT", all_codes(out))                                 # AON legs are market by design: the combination says so
        out = self.parse("buy 100 ACME limit 225 and sell 1 ACME Dec 18 2026 260 call as a package")
        self.assertEqual({"account_id": P, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": [
            {"instrument_id": "ACME", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "225"},
            {"instrument_id": "ACME261218C00260000", "side": "STO", "qty": 1, "order_type": "MARKET"}]}}, out["combos"][0]["request"])
        out = self.parse("sell 1 ACME Dec 18 2026 260 call and buy 100 ACME together")  # the shares go first in a sequential package
        self.assertEqual(["ACME", "ACME261218C00260000"], [l["instrument_id"] for l in out["combos"][0]["request"]["package"]["legs"]])
        out = self.parse("(buy 1 ACME Dec 18 2026 240 call limit 3.20 and sell 1 ACME Dec 18 2026 250 call limit 1.10) together")
        f = flag(out["combos"][0], "LIMIT_IN_AON")
        self.assertEqual(["(buy 1 ACME Dec 18 2026 240 call and sell 1 ACME Dec 18 2026 250 call) together",
                          "buy 1 ACME Dec 18 2026 240 call limit 3.20 and sell 1 ACME Dec 18 2026 250 call limit 1.10"], [c["clause"] for c in f["choices"]])
        self.assertEqual(f["span"], [0, len(out["text"])])
        first = self.parse(f["choices"][0]["clause"])
        self.assertEqual((True, "AON"), (first["ok"], first["combos"][0]["fill_mode"]))
        second = self.parse(f["choices"][1]["clause"])
        self.assertEqual((True, 0, ["LIMIT", "LIMIT"]), (second["ok"], len(second["combos"]), [l["order_type"] for l in second["legs"]]))
        out = self.parse("buy 1 ACME Dec 18 2026 240/250 call spread limit 2.10")
        self.assertEqual(["buy 1 ACME Dec 18 2026 240/250 call spread", "bto 1 ACME261218C00240000 and sto 1 ACME261218C00250000"],
                         [c["clause"] for c in flag(out["combos"][0], "LIMIT_IN_AON")["choices"]])
        for text in ("(buy 1 ACME Dec 18 2026 240 call and sell 1 WIDG Dec 18 2026 40 put)", "(buy 100 ACME and buy 100 WIDG)",
                     "(buy 1 ESZ26 and buy 1 ACME Dec 18 2026 240 call)", "(buy 100 ACME and buy 1 ACME Dec 18 2026 240 call in the Fund book)"):
            cb = self.parse(text)["combos"][0]                                           # two names, two share legs, a futures leg, two books
            self.assertIsNone(cb["request"], text)
            self.assertIn("COMBO_INVALID", codes(cb["flags"]), text)
        cb = self.parse("(buy 1 ACME Dec 18 2026 240 call and sell 1 WIDG Dec 18 2026 40 put)")["combos"][0]
        self.assertEqual(["buy 1 ACME Dec 18 2026 240 call and sell 1 WIDG Dec 18 2026 40 put"], [c["clause"] for c in flag(cb, "COMBO_INVALID")["choices"]])

    def test_covered_calls_collars_and_rolls(self):
        out = self.parse("covered call 300 ACME Dec 18 2026 250")
        self.assertEqual([("BUY", 300, "ACME"), ("STO", 3, C250)], [(l["side"], l["qty"], l["instrument"]["id"]) for l in out["legs"]])
        self.assertEqual(("SEQUENTIAL", "covered call"), (out["combos"][0]["fill_mode"], out["combos"][0]["strategy"]))
        out = self.parse("collar 100 ACME Dec 18 2026 220/260", book="F")               # 100 held in F: the options only
        self.assertEqual(["BTO", "STO"], [l["side"] for l in out["legs"]])
        out = self.parse("collar 100 ACME Dec 18 2026 220/260")                          # none in P: the shares first
        self.assertEqual(["BUY", "BTO", "STO"], [l["side"] for l in out["legs"]])
        out = self.parse("roll ACME Dec 18 2026 240 calls to 250")                       # the 2 held short: buy them back, sell the new strike
        self.assertEqual([("BTC", 2, C240), ("STO", 2, C250)], [(l["side"], l["qty"], l["instrument"]["id"]) for l in out["legs"]])
        self.assertEqual("AON", out["combos"][0]["fill_mode"])

    def test_cover_hint_and_duplicate_leg(self):
        text = "buy 100 ACME and sell 1 ACME Dec 18 2026 260 call"
        out = self.parse(text)
        f = flag(out["legs"][1], "COVER_HINT")
        self.assertEqual(([0, len(text)], [text + " as a package"]), (f["span"], [c["clause"] for c in f["choices"]]))
        self.assertEqual("SEQUENTIAL", self.parse(f["choices"][0]["clause"])["combos"][0]["fill_mode"])
        out = self.parse("buy 100 ACME; buy 100 ACME")
        self.assertTrue(all("DUPLICATE_LEG" in codes(l["flags"]) for l in out["legs"]))
        self.assertTrue(out["ok"])                                                       # a check, not a blocker
        # ... but inside ONE combination a contract named twice is a blocker: the CUSTOM package refuses it (400 LEGS), so it is never sent
        for text in ("(buy 1 ACME Dec 18 2026 260 call and buy 1 ACME Dec 18 2026 260 call)",
                     "(buy 1 ACME Dec 18 2026 260 call and sell 1 ACME Dec 18 2026 260 call) together"):
            out = self.parse(text)
            cb = out["combos"][0]
            f = flag(cb, "COMBO_INVALID")
            self.assertEqual((False, None, [1, 2]), (out["ok"], cb["request"], cb["legs"]), text)
            self.assertIn("same contract", f["text"], text)
            self.assertEqual([[0, len(out["text"])]], [f["span"]], text)
            self.assertEqual(1, len(f["choices"]), text)                             # as separate orders: no package, so no 400
            self.assertNotIn("COMBO_INVALID", all_codes(self.parse(f["choices"][0]["clause"])), text)
        self.assertNotIn("COMBO_INVALID", all_codes(self.parse("sell 1 ACME Dec 18 2026 220/230/240 iron butterfly")))   # P230 and C230: two contracts


class ResolverRenamedBooks(_ResolverBase):
    """A renamed book is found by its new first word; a word two books share is TOKEN_AMBIGUOUS (each test has its own runtime)."""

    def setUp(self):
        self.tmp, self.rt = self._make()
        with self.rt.db.writer() as tx:
            self.rt.instruments.upsert(tx, **corpus.WORLD["instruments"][0])
        self.rt.instruments.load()

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rename(self, code, name):
        with self.rt.db.writer() as tx:
            tx.conn.execute("UPDATE books SET name=? WHERE book_code=?", (name, code))

    def test_renamed_book(self):
        self._rename("F", "Alpha Fund")
        self.assertEqual("F", self.one("buy 100 ACME in the Alpha book")["book"])
        self.assertEqual("F", self.one("buy 100 ACME in the Fund book")["book"])        # the profile word still names it
        self.assertEqual("Alpha Fund", self.one("buy 100 ACME in the Alpha book")["book_name"])

    def test_a_word_two_books_share(self):
        self._rename("I", "Fund Partners")                                               # "fund": F's profile word and I's first word
        leg = self.one("buy 100 ACME in the Fund book")
        f = flag(leg, "TOKEN_AMBIGUOUS")
        self.assertEqual(["buy 100 ACME in the F book", "buy 100 ACME in the I book"], [c["clause"] for c in f["choices"]])
        self.assertIsNone(leg["request"])
        self.assertEqual("I", self.one(f["choices"][1]["clause"])["book"])


# ===== the route on a full runtime: registration, 400s, the corpus and the purity proof ================================================
def _build_full(tmp):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(corpus.AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                 fetch_threads=0, news_enabled=False)


NEVER_CALLED = (("options", "ensure_option"), ("api_products", "_materialise_option"), ("instruments", "upsert"), ("instruments", "ensure_option"),
                ("worker", "resolve"), ("worker", "request_bars"), ("worker", "request_chain"), ("cache", "subscribe"), ("cache", "pin"),
                ("orders_preview", "_ask_for_quote"), ("chains", "get"), ("chains", "request"), ("chains", "keep_on_screen"), ("orders", "preview"),
                ("orders_preview", "preview"), ("previews", "put"), ("marks", "snapshot"), ("bus", "execute"), ("db", "writer"))


class Route(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fst-smart-route-")
        cls.rt = _build_full(cls.tmp)
        build_world(unittest.TestCase(), cls.rt)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, body, query="", headers=None):
        return self.rt.router.handle("POST", "/api/smart-ticket/parse" + query, None, body, "127.0.0.1", headers)

    def test_registered_not_mutating_and_bound(self):
        rt = self.rt
        self.assertEqual("smart_ticket", list(_server.OPTIONAL_MODULES)[-1])
        self.assertEqual("smart_ticket", _server.OPTIONAL_MODULES["smart_ticket"])
        self.assertEqual("bound", rt.modules.get("smart_ticket"))
        self.assertIs(st, rt.smart_ticket)
        routes = [(m, rx.pattern, mut) for m, rx, _fn, mut, _loop in rt.router.routes if "smart-ticket" in rx.pattern]
        self.assertEqual([("POST", "^/api/smart-ticket/parse$", False)], routes)
        self.assertEqual(1, rt.router.product_routes.get("smart_ticket"))
        self.assertFalse(hasattr(st, "register"))                                        # no command
        self.assertIn("SMART_TICKET", tables.CONVENTIONS)
        rows = [r for r in _api.unsupported_rows(rt) if r["where"] == "Smart Ticket"]
        self.assertEqual(2, len(rows))
        self.assertIn("all-or-none combinations have no net limit in this version (each leg at its executable bid / ask)", [r["wording"] for r in rows])

    def test_no_request_id_needed_and_one_sent_is_ignored(self):
        s1, b1, _h = self.post({"text": "buy 1 ACME limit 1.00", "book": "P"}, "?book=P")
        s2, b2, _h = self.post({"text": "buy 1 ACME limit 1.00", "book": "P", "request_id": "st:abcdef123456:L1:r1"}, "?book=P", {"X-Request-Id": "st:abcdef123456:L1:r2"})
        self.assertEqual((200, 200, "smart-ticket/1"), (s1, s2, b1["grammar"]))
        self.assertEqual(json.dumps(b1, sort_keys=True), json.dumps(b2, sort_keys=True))

    def test_the_400s(self):
        cases = [("[1, 2]", "BAD_REQUEST"), ({}, "BAD_REQUEST"), ({"text": 5}, "BAD_REQUEST"), ({"text": None}, "BAD_REQUEST"), ({"text": "buy 1 ACME", "account_id": 3}, "BAD_REQUEST"),
                 ({"text": "x" * 2001}, "TEXT_TOO_LONG"), ({"text": "; ".join(["buy 1 ACME"] * 21)}, "TOO_MANY_LEGS")]
        for body, code in cases:
            status, out, headers = self.post(body)
            self.assertEqual((400, code), (status, out["error"]["code"]), body if not isinstance(body, dict) or len(str(body)) < 80 else code)
            self.assertEqual("no-store", headers["Cache-Control"])
        status, out, _h = self.post({"text": "x" * 2000})
        self.assertEqual(200, status)                                                   # exactly the limit is accepted
        status, out, _h = self.post({"text": "; ".join(["buy 1 ACME"] * 20)})
        self.assertEqual((200, 20), (status, len(out["legs"])))
        status, out, _h = self.post({"text": "buy 1 ACME"}, "?book=Z")
        self.assertEqual((404, "UNKNOWN_BOOK"), (status, out["error"]["code"]))

    def test_twenty_legs_under_300_ms(self):
        text = "; ".join(["sell 1 ACME Dec 18 2026 210/220/250/260 iron condor"] * 5)
        self.post({"text": text, "book": "P"})                                           # warm the master index once
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            status, out, _h = self.post({"text": text, "book": "P"}, "?book=P")
            ms = (time.perf_counter() - t0) * 1000.0
            best = ms if best is None else min(best, ms)
            self.assertEqual((200, 20, 5), (status, len(out["legs"]), len(out["combos"])))
        sys.stderr.write(f"\nsmart ticket: a 20-leg parse took {best:.1f} ms (limit 300 ms)\n")
        self.assertLess(best, 300.0)

    def test_corpus_through_the_route_is_pure(self):
        """THE PURITY PROOF (CHANGE_PLAN 2.6) over the whole corpus, and every case's expectation on the same pass."""
        rt = self.rt
        import finsim.terminal.api_products as m_api_products
        import finsim.terminal.orders_preview as m_orders_preview
        owners = {"api_products": m_api_products, "orders_preview": m_orders_preview}
        symbols = sorted({w.upper().lstrip("$") for c in corpus.CASES for w in re.findall(r"\$?[A-Za-z][A-Za-z0-9.\-]*", c["text"])
                          if rt.instruments.get(w.upper().lstrip("$")) is not None} | {C240, C250, "WIDG261218P00040000"})
        self.assertIn("ACME", symbols)
        held = sqlite3.connect(rt.db_path)
        calls = []
        patched = []
        try:
            def state():
                with rt.db.reader() as conn:
                    n_inst = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
                    n_cmd = conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
                return {"state_version": rt.db.state_version(), "data_version": held.execute("PRAGMA data_version").fetchone()[0], "instruments": n_inst,
                        "commands": n_cmd, "snaps": list(rt.chains._snaps), "pending": list(rt.chains.pending()), "pinned": list(rt.cache.pinned()),
                        "priorities": {s: list(rt.cache.priorities(s)) for s in symbols}, "previews": (len(rt.previews), list(rt.previews._ring))}
            before = state()
            for owner, attr in NEVER_CALLED:
                obj = owners.get(owner) or getattr(rt, owner)
                self.assertTrue(callable(getattr(obj, attr, None)), f"{owner}.{attr} is not there to spy on")
                had = attr in vars(obj)
                orig = getattr(obj, attr)

                def spy(*a, _orig=orig, _name=f"{owner}.{attr}", **k):
                    calls.append(_name)
                    return _orig(*a, **k)
                setattr(obj, attr, spy)
                patched.append((obj, attr, had, orig))
            problems = []
            n_clean = n_refused = 0
            for case in corpus.CASES:
                body = {"text": case["text"], "book": case["book"]}
                if case["setup"].get("account_id"):
                    body["account_id"] = case["setup"]["account_id"]
                status, out, _h = self.post(body, f"?book={case['book']}")
                for p in case_problems(case, status, out):
                    problems.append(f"{case['id']}: {p}")
                if status == 200 and out.get("ok"):
                    n_clean += 1
                else:
                    n_refused += 1
                self.assertEqual(before["state_version"], rt.db.state_version(), case["id"])
            after = state()
        finally:
            for obj, attr, had, orig in reversed(patched):
                if had:
                    setattr(obj, attr, orig)
                else:
                    delattr(obj, attr)
            held.close()
        self.assertEqual([], calls)
        self.assertEqual(before, after)
        self.assertEqual([], problems, "\n" + "\n".join(problems))
        self.assertGreaterEqual(len(corpus.CASES), 150)
        self.assertGreaterEqual(n_refused, n_clean)
        for obj, attr, _had, orig in patched:                                            # the spies are gone (a bound method compares equal, not identical)
            self.assertEqual(orig, getattr(obj, attr))

    def test_word_salad_through_the_route_answers_and_writes_nothing(self):
        """A seeded word salad (the grammar module's vocabulary): every answer is 200 or a stated 4xx, never a 500, and nothing is written."""
        import random
        from tests.test_terminal_smart_grammar import FUZZ_WORDS
        rt = self.rt
        rng = random.Random(1105)
        v0 = rt.db.state_version()
        statuses = set()
        for _ in range(250):
            text = " ".join(rng.choice(FUZZ_WORDS) for _ in range(rng.randint(1, 12)))
            status, out, _h = self.post({"text": text, "book": rng.choice("PFI")})
            statuses.add(status)
            self.assertIn(status, (200, 400), (text, out))
            if status == 200:
                self.assertEqual(out["ok"], out["counts"]["blockers"] == 0 and bool(out["legs"]), text)
                for leg in out["legs"]:                                                  # a body only while the leg carries no blocker
                    if leg["request"] is not None:
                        self.assertFalse(any(f["severity"] == "blocker" for f in leg["flags"]), text)
        self.assertIn(200, statuses)
        self.assertEqual(v0, rt.db.state_version())

    def test_a_parser_fault_is_a_500_without_an_incident_row(self):
        """The route's own failure path: a fault inside the parser is logged and answered 500 PARSE_FAILED; the router's incident path (an
        attention row written through a command) is never taken, so even a failing parse writes nothing."""
        rt = self.rt
        v0 = rt.db.state_version()
        orig = st.Resolver.run

        def boom(self_, tree):
            raise ZeroDivisionError("test fault")
        st.Resolver.run = boom
        try:
            status, out, _h = self.post({"text": "buy 1 ACME", "book": "P"})
        finally:
            st.Resolver.run = orig
        self.assertEqual((500, "PARSE_FAILED"), (status, out["error"]["code"]))
        self.assertNotIn("incident_id", out["error"]["details"])
        self.assertEqual(v0, rt.db.state_version())
        self.assertEqual(200, self.post({"text": "buy 1 ACME", "book": "P"})[0])

    def test_corpus_ids_unique_and_balanced(self):
        ids = [c["id"] for c in corpus.CASES]
        self.assertEqual(len(ids), len(set(ids)))
        clean = [c for c in corpus.CASES if c["expect"].get("status", 200) == 200 and c["expect"].get("ok") is True]
        refused = [c for c in corpus.CASES if c not in clean]
        self.assertGreaterEqual(len(refused), len(clean))
        self.assertTrue(all(set(c) == {"id", "text", "book", "setup", "expect"} for c in corpus.CASES))
        with open(corpus.__file__, "rb") as fh:
            self.assertTrue(fh.read().isascii())                                         # the data module itself is ASCII (an escape for the non-ASCII case)


if __name__ == "__main__":
    unittest.main()
