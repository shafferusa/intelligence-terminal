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
        # fourth review: the pair is grouped with parentheses (a group word's run would reach back over an order before them)
        self.assertEqual(([0, len(text)], ["(" + text + ")"]), (f["span"], [c["clause"] for c in f["choices"]]))
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


class ChoicesKeepOtherOrders(_ResolverBase):
    """1.0.7.2 third review: every choice offered on an instruction of several orders, applied as the page applies it (its text in place
    of the flag's span, then a new parse of the whole instruction), leaves every OTHER order with the book, account and side the parser
    read, never newly sendable, and the order it is on in the book and account it was read in (unless the choice is that book or
    account).  The world is the corpus's, plus a WIDG chain and 100 WIDG short in the Fund book."""

    TEXTS = (("short 100 WIDG in the Fund book and 1 ACME Dec 18 2026 240/250 call spread", "P"),
             ("short 100 GIZM at pb ms and 1 ACME Dec 18 2026 240/250 call spread", "I"),
             ("sell 1 ACME Dec 18 2026 260 calls in the Institutional book at pb ms and 3 ACME Dec 18 2026 250 calls", "P"),
             ("short 100 WIDG in the Fund book and 2 ACME Dec 18 2026 260 calls", "P"),
             ("sell 10 ACME in the Fund book and 300 WIDG", "P"),
             ("sell 10 ACME in the Fund book and 1 ACME Dec 18 2026 240/250 call spread at 1.00", "P"),
             ("short 100 GIZM in the Institutional book and 1 ACME Dec 18 2026 240/250 call spread and 1 ACME Dec 18 2026 210/220 put spread", "P"),
             ("short 100 WIDG in the Fund book and 2 ACME Dec 18 2026 260 calls and 1 ACME Dec 18 2026 240/250 call spread", "P"),
             ("buy 3 WIDG Nov 20 2026 40 calls and buy 1 ACME Dec 18 2026 240/250 call spread at I1:PB_MS", "P"),
             ("buy 100 ACME; buy 50 GIZM at I1:PB_MS", "P"),
             ("buy 100 ACME, sto 2 ACME Dec 18 2026 250 calls in the Institutional book", "P"),
             ("sell 3 ACME Dec 18 2026 250 calls in the Institutional book and buy 10 WIDG", "P"),
             ("buy 100 ACME in F1:US_EXCHANGE and 50 WIDG", "P"),
             ("buy 1 ACME Dec 18 2026 245 call and 2 ACME Dec 18 2026 260 calls in the Fund book", "P"),
             ("buy 100 ACME and 50 WIDG and 15 LOTX", "P"),
             ("100 ACME and 50 WIDG", "P"))

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = cls._make()
        world = dict(corpus.WORLD)
        world["chains"] = list(corpus.WORLD["chains"]) + [{"underlying": "WIDG", "cboe": "WIDG", "spot": 40, "expiries": ("2026-11-20", corpus.DEC),
                                                           "strikes": (35, 40, 45), "skip": (), "age_s": 30, "trade_time": corpus.TRADE_TIME, "extra": ()}]
        world["positions"] = list(corpus.WORLD["positions"]) + [(F, "WIDG", "SHORT", 100, "40")]
        build_world(unittest.TestCase(), cls.rt, world)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def goes(out, l) -> bool:
        return l["request"] is not None and (l["combo"] is None or any(c["id"] == l["combo"] and c["request"] is not None for c in out["combos"]))

    def test_every_choice_changes_only_the_order_it_is_on(self):
        applied = widened = dropped = 0
        problems = []
        for text, book in self.TEXTS:
            out = self.parse(text, book)
            seen = set()
            for item in out["legs"] + out["combos"]:
                for f in item["flags"]:
                    if not f.get("choices"):
                        if f["code"] == "MISSING_SIDE" and len(out["legs"]) > 1:
                            dropped += 1
                        continue
                    a, b = f["span"]
                    for ch in f["choices"]:
                        if (f["code"], a, b, ch["clause"]) in seen:
                            continue
                        seen.add((f["code"], a, b, ch["clause"]))
                        applied += 1
                        widened += int(any(l["span"][0] >= a and l["span"][1] <= b and l is not item and l["span"] != item["span"] for l in out["legs"]))
                        out2 = self.parse(out["text"][:a] + ch["clause"] + out["text"][b:], book)
                        delta = len(ch["clause"]) - (b - a)
                        where = f"{text!r} [{f['code']} -> {ch['clause']!r}]"
                        before = {(l["book"], l["account_id"]) for l in out["legs"] if a <= l["span"][0] and l["span"][1] <= b}
                        known = all(x[1] is not None for x in before)        # an order not read yet (no contract) has no account to keep
                        for m in out2["legs"]:
                            if a <= m["span"][0] and m["span"][1] <= a + len(ch["clause"]):
                                moved = m["book"] not in {x[0] for x in before} or (known and (m["book"], m["account_id"]) not in before)
                                if f["code"] not in ("ACCOUNT_CHOICE", "ACCOUNT_NOT_IN_BOOK") and self.goes(out2, m) and moved:
                                    problems.append(f"{where}: leg {m['n']} moved to {m['book']} {m['account_id']}")
                        for l in out["legs"]:
                            if a <= l["span"][0] and l["span"][1] <= b:
                                continue
                            sp = l["span"] if l["span"][1] <= a else [l["span"][0] + delta, l["span"][1] + delta]
                            m = next((m for m in out2["legs"] if m["span"] == sp and m["instrument"].get("id") == l["instrument"].get("id")
                                      and m["book"] == l["book"]), None)
                            if m is None or (m["account_id"], m["side"]) != (l["account_id"], l["side"]) or (self.goes(out2, m) and not self.goes(out, l)):
                                problems.append(f"{where}: the other leg {l['n']} {(l['book'], l['account_id'], l['side'])} became "
                                                f"{None if m is None else (m['book'], m['account_id'], m['side'], self.goes(out2, m))}")
        self.assertEqual([], problems, "\n".join(problems))
        self.assertGreaterEqual(applied, 25)
        self.assertGreaterEqual(widened, 4)                     # choices that wrote out the orders around them
        self.assertGreaterEqual(dropped, 1)                     # "100 ACME and 50 WIDG": a side for the first would carry into the second

    def test_the_rewritten_choices_read_as_trader_words(self):
        f = flag(self.parse("short 100 WIDG in the Fund book and 1 ACME Dec 18 2026 240/250 call spread")["legs"][1], "MISSING_SIDE")
        self.assertEqual(["buy 1 ACME Dec 18 2026 240/250 call spread in the Fund book", "sell 1 ACME Dec 18 2026 240/250 call spread in the Fund book"],
                         [c["clause"] for c in f["choices"]])
        out = self.parse("short 100 GIZM in the Institutional book and 1 ACME Dec 18 2026 240/250 call spread and 1 ACME Dec 18 2026 210/220 put spread")
        f = flag(out["legs"][1], "MISSING_SIDE")
        self.assertEqual((45, len(out["text"])), tuple(f["span"]))     # widened over the put spread, which keeps its own (refused) side
        self.assertEqual("buy 1 ACME Dec 18 2026 240/250 call spread in the Institutional book and short 1 ACME Dec 18 2026 210/220 put spread in the"
                         " Institutional book", f["choices"][0]["clause"])
        out = self.parse("buy 3 WIDG Nov 20 2026 40 calls and buy 1 ACME Dec 18 2026 240/250 call spread at I1:PB_MS")
        f = flag(out["legs"][1], "ACCOUNT_NOT_IN_BOOK")
        self.assertEqual(([0, len(out["text"])], ["buy 3 WIDG Nov 20 2026 40 calls in the Personal book and buy 1 ACME Dec 18 2026 240/250 call spread at"
                                                  " I1:PB_MS in the Institutional book"]), (f["span"], [c["clause"] for c in f["choices"]]))
        # the opening part of a split keeps the account the leg was read in (the one that holds the calls), never R-ACCT-1's US Exchange
        f = flag(self.one("sell 3 ACME Dec 18 2026 250 calls in the Institutional book"), "SIDE_SPLIT")
        self.assertEqual("stc 2 ACME Dec 18 2026 250 calls in the Institutional book and sto 1 ACME Dec 18 2026 250 calls in the Institutional book at pb ms",
                         f["choices"][0]["clause"])
        self.assertEqual([PB, PB], [l["account_id"] for l in self.parse(f["choices"][0]["clause"])["legs"]])


class ChoicesFourthReview(_ResolverBase):
    """1.0.7.2 fourth review, applied as the page applies a choice (its text in place of the flag's span, then a new reading): a choice on
    one order never turns an order already sent into a new one (R2 reads the words without a leading "buy" / "sell"); "as separate
    orders" and the cover hint never merge another order into a package (the next group, or the pair, is written with parentheses); a
    choice keeps the order it is on in the account it was read in (the buy write, the side alternatives: "sto" on shares is a short
    sale); a covered call or collar quantity in contracts or lots is refused, never read as shares.  The world: the corpus's, a WIDG
    chain, 100 WIDG short in the Fund book, 200 WIDG long at I1:PB_MS, and the Personal book's Dec-18 240 / 250 calls closed today by two
    Smart Ticket orders whose commands keep the clause as the page stores it (fixture rows, fictional ids)."""

    SENT = (("O-ST4-BTC", "st:4f4f4f4f4f01:L2:r1", C240, "BTC", 2, "2 ACME Dec 18 2026 240 calls"),
            ("O-ST4-STC", "st:4f4f4f4f4f02:L2:r1", C250, "STC", 3, "3 ACME Dec 18 2026 250 calls"))

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = cls._make()
        world = dict(corpus.WORLD)
        world["chains"] = list(corpus.WORLD["chains"]) + [{"underlying": "WIDG", "cboe": "WIDG", "spot": 40, "expiries": ("2026-11-20", corpus.DEC),
                                                           "strikes": (35, 40, 45), "skip": (), "age_s": 30, "trade_time": corpus.TRADE_TIME, "extra": ()}]
        world["positions"] = ([x for x in corpus.WORLD["positions"] if not (x[0] == P and x[1] in (C240, C250))]
                              + [(F, "WIDG", "SHORT", 100, "40"), (PB, "WIDG", "LONG", 200, "40")])
        build_world(unittest.TestCase(), cls.rt, world)
        now = int(cls.rt.clock.epoch())
        with cls.rt.db.writer() as tx:                               # fixture only: two Smart Ticket orders sent and filled today
            for oid, rid, iid, side, qty, clause in cls.SENT:
                payload = json.dumps({"account_id": P, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY",
                                      "smart": {"ticket": rid.split(":")[1], "item": "L2", "clause": clause}}, sort_keys=True)
                tx.conn.execute("INSERT INTO commands(request_id, kind, actor, payload_json, payload_sha256, result_json, http_status, started_ts, finished_ts,"
                                " duration_ms, app_version, state_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (rid, "order.submit", "ui", payload, "0" * 64, "{}", 200, now, now, 1, "test", 0))
                tx.conn.execute("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, filled_qty, order_type, tif, status, session_group,"
                                " created_ts, updated_ts, expires_ts, preview_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'{}')",
                                (oid, rid, P, iid, side, qty, qty, "MARKET", "DAY", "FILLED", "US_EQUITY_OPTIONS", now, now, now + 7200))

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def goes(out, l) -> bool:
        return l["request"] is not None and (l["combo"] is None or any(c["id"] == l["combo"] and c["request"] is not None for c in out["combos"]))

    def apply(self, text, book, code, label):
        out = self.parse(text, book)
        for item in out["legs"] + out["combos"]:
            for f in item["flags"]:
                for ch in f.get("choices") or [] if f["code"] == code else []:
                    if label in ch["label"]:
                        a, b = f["span"]
                        return out, f, ch, self.parse(out["text"][:a] + ch["clause"] + out["text"][b:], book)
        self.fail(f"no {code} choice labelled {label!r} on {text!r}: {json.dumps([l['flags'] for l in out['legs']])[:2000]}")

    def test_r2_an_order_already_sent_reads_as_it_after_a_choice_on_another_order(self):
        for text, code, label, iid, side in (("buy 300 GIZM and 2 ACME Dec 18 2026 240 calls", "SIDE_SPLIT", "cover 200 only", C240, "BTC"),
                                             ("sell 400 WIDG and 3 ACME Dec 18 2026 250 calls", "WOULD_OVERSELL", "sell 200 + short 200", C250, "STC")):
            with self.subTest(text=text):
                out, f, ch, out2 = self.apply(text, "P", code, label)
                self.assertIn(("buy " if side == "BTC" else "sell ") + text.split(" and ")[1], ch["clause"])     # the guard wrote the carried side word
                for o in (out, out2):
                    leg = next(l for l in o["legs"] if l["instrument"].get("id") == iid)
                    self.assertEqual((side, True), (leg["side"], "ALREADY_SENT" in codes(leg["flags"])), o["text"])
        # the words with their own side word read as the order that went too (the side's direction is compared on its own)
        leg = self.one("buy 2 ACME Dec 18 2026 240 calls")
        self.assertEqual(("BTC", True), (leg["side"], "ALREADY_SENT" in codes(leg["flags"])))
        leg = self.one("sell 2 ACME Dec 18 2026 240 calls")                                     # the other direction is another order
        self.assertNotIn("ALREADY_SENT", codes(leg["flags"]))

    def test_grouping_choices_keep_each_package_its_own_orders(self):
        cases = (("buy 1 ACME Dec 18 2026 220 put as a package, buy 1 ACME Dec 18 2026 240/250 call spread as a package", "F", "COMBO_INVALID", "as separate orders",
                  "buy 1 ACME Dec 18 2026 220 put, (buy 1 ACME Dec 18 2026 240/250 call spread)"),
                 ("(bto 1 ACME Dec 18 2026 260 call); 1 ACME Dec 18 2026 210/220 put spread as a package", "P", "COMBO_INVALID", "as separate orders",
                  "bto 1 ACME Dec 18 2026 260 call; (1 ACME Dec 18 2026 210/220 put spread)"),
                 ("in the Fund book: sell 2 ACME Dec 18 2026 250 calls limit 1.10 as a package, 100 ACME as a package", "I", "COMBO_INVALID", "as separate orders",
                  "sell 2 ACME Dec 18 2026 250 calls limit 1.10, (100 ACME)"),
                 ("buy 1 ACME Dec 18 2026 240/250 call spread, buy 100 ACME, sell 1 ACME Dec 18 2026 260 call; buy 50 WIDG", "F", "COVER_HINT", "group them",
                  "(buy 100 ACME, sell 1 ACME Dec 18 2026 260 call)"))
        for text, book, code, label, want in cases:
            with self.subTest(text=text):
                out, f, ch, out2 = self.apply(text, book, code, label)
                self.assertTrue(ch["clause"].endswith(want), ch["clause"])
                before = {tuple(c["legs"]) for c in out["combos"] if c["request"] is not None}
                a, b = f["span"]
                inside = {l["n"] for l in out2["legs"] if a <= l["span"][0] and l["span"][1] <= a + len(ch["clause"])}
                merged = [c["legs"] for c in out2["combos"] if c["request"] is not None and set(c["legs"]) & inside and set(c["legs"]) - inside]
                self.assertEqual([], merged, out2["text"])
                for c in before:                              # every sendable package of the other orders is still there, with its legs
                    self.assertIn(len(c), [len(x["legs"]) for x in out2["combos"] if x["request"] is not None], out2["text"])
        # the pair alone: grouped with parentheses (a sequential package of the shares and the call)
        out = self.parse("buy 100 ACME, sell 1 ACME Dec 18 2026 260 call", "F")
        f = flag(out["legs"][1], "COVER_HINT")
        self.assertEqual(["(buy 100 ACME, sell 1 ACME Dec 18 2026 260 call)"], [c["clause"] for c in f["choices"]])
        self.assertEqual("SEQUENTIAL", self.parse(f["choices"][0]["clause"], "F")["combos"][0]["fill_mode"])

    def test_a_choice_keeps_the_order_it_is_on_in_its_account(self):
        out, f, ch, out2 = self.apply("covered call 300 WIDG Dec 18 2026 45 in the Institutional book", "P", "SIDE_SPLIT", "buy write")
        self.assertEqual(({PB}, "buy write 300 WIDG Dec 18 2026 45 in the Institutional book at pb ms"), ({l["account_id"] for l in out["legs"]}, ch["clause"]))
        self.assertEqual([(PB, "BUY", 300), (PB, "STO", 3)], [(l["account_id"], l["side"], l["qty"]) for l in out2["legs"]])
        # "sto" on shares is a short sale: the alternative is "short", read where the "sto" was read (an opening side)
        out, f, ch, out2 = self.apply("sto 50 WIDG in the Institutional book", "P", "MISSING_SIDE", "sell short")
        self.assertEqual(([I], "short 50 WIDG in the Institutional book"), ([l["account_id"] for l in out["legs"]], ch["clause"]))
        self.assertEqual([(I, "SELL_SHORT", 50)], [(l["account_id"], l["side"], l["qty"]) for l in out2["legs"]])
        # "bto" on shares: "buy", named where the "bto" was read, and said when it covers a short held there
        out, f, ch, out2 = self.apply("bto 50 WIDG in the Fund book", "P", "MISSING_SIDE", "buy")
        self.assertEqual(("buy (covers the 100 short in F1:US_EXCHANGE)", "buy 50 WIDG in the Fund book at us exchange"), (ch["label"], ch["clause"]))
        self.assertEqual([(F, "BUY_TO_COVER", 50)], [(l["account_id"], l["side"], l["qty"]) for l in out2["legs"]])

    TEXTS = (("buy 300 GIZM and 2 ACME Dec 18 2026 240 calls", "P"), ("sell 400 WIDG and 3 ACME Dec 18 2026 250 calls", "P"),
             ("buy 1 ACME Dec 18 2026 220 put as a package, buy 1 ACME Dec 18 2026 240/250 call spread as a package", "F"),
             ("(bto 1 ACME Dec 18 2026 260 call); 1 ACME Dec 18 2026 210/220 put spread as a package", "P"),
             ("in the Fund book: sell 2 ACME Dec 18 2026 250 calls limit 1.10 as a package, 100 ACME as a package", "I"),
             ("buy 1 ACME Dec 18 2026 240/250 call spread, buy 100 ACME, sell 1 ACME Dec 18 2026 260 call; buy 50 WIDG", "F"),
             ("covered call 300 WIDG Dec 18 2026 45 in the Institutional book", "P"), ("sto 50 WIDG in the Institutional book", "P"),
             ("bto 50 WIDG in the Fund book", "P"), ("covered call 100 contracts WIDG Dec 18 2026 45", "P"))

    def test_every_choice_keeps_the_other_orders_and_its_own_account(self):
        """Every choice offered on these texts, applied: every OTHER order keeps its book, account, side, quantity and reading as an
        order already sent, and is never newly sendable; the orders the choice writes stay in the book and account they were read in
        (unless the choice is about those) and join no package with an order outside them."""
        problems, applied = [], 0
        for text, book in self.TEXTS:
            out = self.parse(text, book)
            for item in out["legs"] + out["combos"]:
                for f in item["flags"]:
                    for ch in f.get("choices") or []:
                        applied += 1
                        a, b = f["span"]
                        out2 = self.parse(out["text"][:a] + ch["clause"] + out["text"][b:], book)
                        delta, end = len(ch["clause"]) - (b - a), a + len(ch["clause"])
                        where = f"{text!r} [{f['code']} -> {ch['clause']!r}]"
                        before = {(l["book"], l["account_id"]) for l in out["legs"] if a <= l["span"][0] and l["span"][1] <= b}
                        inside = {m["n"] for m in out2["legs"] if a <= m["span"][0] and m["span"][1] <= end}
                        for m in out2["legs"]:
                            if m["n"] in inside and f["code"] not in ("ACCOUNT_CHOICE", "ACCOUNT_NOT_IN_BOOK") and self.goes(out2, m) and before and                                     (m["book"], m["account_id"]) not in before:
                                problems.append(f"{where}: leg {m['n']} moved to {m['book']} {m['account_id']}")
                        for c in out2["combos"]:
                            if c["request"] is not None and set(c["legs"]) & inside and set(c["legs"]) - inside:
                                problems.append(f"{where}: package {c['legs']} joins the choice's orders with others")
                        for l in out["legs"]:
                            if a <= l["span"][0] and l["span"][1] <= b:
                                continue
                            words = st._clause_words(l["clause"])           # a parenthesis written next to it can move a comma into its clause
                            m = next((m for m in out2["legs"] if m["n"] not in inside and st._clause_words(m["clause"]) == words
                                      and m["instrument"].get("id") == l["instrument"].get("id")), None)
                            sent = ("ALREADY_SENT" in codes(l["flags"]), m is not None and "ALREADY_SENT" in codes(m["flags"]))
                            if m is None or (m["book"], m["account_id"], m["side"], m["qty"]) != (l["book"], l["account_id"], l["side"], l["qty"])                                     or sent[0] != sent[1] or (self.goes(out2, m) and not self.goes(out, l)):
                                problems.append(f"{where}: the other leg {l['n']} {(l['book'], l['account_id'], l['side'], l['qty'])} became "
                                                f"{None if m is None else (m['book'], m['account_id'], m['side'], m['qty'], sent[1], self.goes(out2, m))}")
        self.assertEqual([], problems, "\n".join(problems))
        self.assertGreaterEqual(applied, 12)

    def test_a_covered_call_or_collar_in_contracts_or_lots_is_refused(self):
        for text, book, n in (("covered call 100 contracts WIDG Dec 18 2026 45", "P", 100), ("covered call 200 lots WIDG Dec 18 2026 45", "P", 200),
                              ("collar 100 contracts ACME Dec 18 2026 220/260", "F", 100),
                              ("collar 100 contracts GIZM Dec 18 2026 45/55 in the Fund book", "P", 100)):
            with self.subTest(text=text):
                out = self.parse(text, book)
                self.assertEqual([], [l["n"] for l in out["legs"] if self.goes(out, l)], text)
                self.assertTrue(all("UNIT_KIND_MISMATCH" in codes(l["flags"]) for l in out["legs"]), text)
                f = next(x for l in out["legs"] for x in l["flags"] if x["code"] == "UNIT_KIND_MISMATCH")
                self.assertEqual(2, len(f["choices"]), f)
                self.assertIn(f"{n * 100:,} shares", f["choices"][0]["label"])
                self.assertNotIn(" contracts ", f["choices"][1]["clause"] + " ")
                self.assertNotIn(" lots ", f["choices"][1]["clause"] + " ")
        # shares (or no unit) read as before: 200 WIDG held free in the Personal book, a covered call of 2 calls
        out = self.parse("covered call 200 shares WIDG Dec 18 2026 45", "P")
        self.assertEqual([("STO", 2)], [(l["side"], l["qty"]) for l in out["legs"] if self.goes(out, l)])


# ===== 1.0.7.2 fifth review: the choice contract, the seeds of the review's property tests replayed ======================================
FIFTH_BOOKS = [tuple(b) for b in st.DEFAULT_BOOKS]                      # the books make_runtime(books="real") creates
FIFTH_PLACE = ("ACCOUNT_CHOICE", "ACCOUNT_NOT_IN_BOOK")
FIFTH_SPLIT = re.compile(r"^(btc|bto|stc|sto|cover|buy|sell|short) ([\d,]+)(?: \+ (btc|bto|stc|sto|cover|buy|sell|short) ([\d,]+))?(?: only)?$")
FIFTH_SIDE = {"btc": "BTC", "bto": "BTO", "stc": "STC", "sto": "STO", "cover": "BUY_TO_COVER", "short": "SELL_SHORT", "sell": "SELL", "buy": "BUY"}
FIFTH_YEAR = r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}(st|nd|rd|th)?,? "
#: the cases of the fifth review's property test of the Smart Ticket's choices (seeds 501-504) that broke the choice contract on 8df9869:
#: (seed, case, selected book, text) - the world of ``ChoicesFifthReview`` (B)
PROP5_SEED_TEXTS = (
    (501, 33, 'I', 'buy 2 ACME Dec 18 2026 240 call at us exchange limit 1.00 and (3 ACME Dec 18 2026 240 calls gtc) and sell 400 WIDG in the Personal and Fund books at us exchange'),
    (501, 39, 'F', 'short 5 WIDG in the Fund book at I1:PB_MS as a package and buy 1 ACME Dec 18 2026 220/230/240 put butterfly in the Fund book at us exchange limit 1.00 and (4 ACME Dec 18 2026 220 put in the Institutional book at I1:PB_MS at 2.10 as a package)'),
    (501, 45, 'F', 'at pb ms: buy 1 ACME Dec 18 2026 220/230/240 put butterfly limit 1.00 as a package; buy 200 LOTX in the Personal book at us exchange gtc as a package, sell 1 ACME Dec 18 2026 210/220 put spread in the Personal book at I1:PB_MS limit 1.00'),
    (501, 51, 'I', 'buy 2 WIDG in the Fund book at I1:PB_MS at 2.10'),
    (501, 54, 'P', 'sell 4 ACME Dec 18 2026 210 calls; cover 1 ACME Dec 18 2026 220 calls in the Fund book gtc as a package and 4 ACME Dec 18 2026 250 calls in the Fund and Institutional books limit 1.00'),
    (501, 60, 'F', 'stc 2 WIDG Dec 18 2026 40 put in the Personal book at I1:PB_MS'),
    (501, 77, 'P', 'sell 200 WIDG in the Personal and Fund books limit 41, stc 4 ACME Dec 18 2026 260 put in the Fund and Institutional books at I1:PB_MS, cover 2 ACME Dec 18 2026 240 calls in the Fund and Institutional books'),
    (502, 1, 'F', 'collar 200 ACME Dec 18 2026 35/45 in the Personal book at I1:PB_MS at 2.10; 5 GIZM'),
    (502, 2, 'F', 'in the Institutional book: short 5 ACME; 1 ACME Dec 18 2026 240 calls limit 41 as a package and (1 ACME Dec 18 2026 240 calls in the Personal book limit 1.00)'),
    (502, 30, 'I', 'sell 200 WIDG in the Fund book at I1:PB_MS gtc and (covered call 300 WIDG Dec 18 2026 45 in the Fund book at I1:PB_MS gtc); 3 ACME Dec 18 2026 260 calls in the Fund book limit 41'),
    (502, 31, 'F', 'buy write 1 contract WIDG Dec 18 2026 45 in the Personal book at us exchange at 2.10 and buy 400 WIDG'),
    (502, 36, 'F', '(cover 2 WIDG Dec 18 2026 40 calls) and 3 ACME Dec 18 2026 220 call in the Institutional book and sell 4 ACME Dec 18 2026 215 call in the Fund book gtc and covered call 100 WIDG Dec 18 2026 45 in the Fund book at I1:PB_MS limit 1.00'),
    (502, 54, 'P', 'stc 3 ACME Dec 18 2026 220 put in the Personal book limit 1.00, buy 3 ZORB at us exchange limit 41 and stc 1 ACME Dec 18 2026 260 put in the Personal book at I1:PB_MS and buy 2 ACME Dec 18 2026 240 put at pb ms'),
    (502, 66, 'P', 'buy 3 ACME Dec 18 2026 220 calls at us exchange limit 41 and stc 1 WIDG Dec 18 2026 40 calls in the Personal book at I1:PB_MS limit 1.00 as a package and sell 3 ACME Dec 18 2026 210 put limit 41'),
    (503, 1, 'P', '(short 3 ACME Dec 18 2026 260 calls gtc); 2 ACME Dec 18 2026 210 call in the Personal book at I1:PB_MS, 4 ACME Dec 18 2026 210 call'),
    (503, 40, 'P', 'sell 1 ACME Dec 18 2026 240/250 call spread in the Personal and Institutional books at I1:PB_MS at 2.10 as a package and 150 GIZM in the Fund book at I1:PB_MS as a package and bto 3 WIDG Dec 18 2026 40 put in the Personal book at us exchange'),
    (503, 53, 'I', 'short 2 ACME Dec 18 2026 210 call in the Fund book limit 41 as a package, collar 200 ACME Dec 18 2026 220/260 in the Personal and Fund books gtc and stc 1 ACME Dec 18 2026 220 call in the Personal book limit 41'),
    (503, 63, 'I', 'buy write 1 contract WIDG Dec 18 2026 45 in the Fund and Institutional books at 2.10 and sell 400 ZORB in the Fund book at I1:PB_MS limit 41'),
    (503, 65, 'I', 'buy 2 ACME Dec 18 2026 210 put in the Fund book at us exchange limit 41 and sell 4 ACME Dec 18 2026 260 put at I1:PB_MS gtc as a package and buy write 200 WIDG Dec 18 2026 45 in the Personal book at I1:PB_MS'),
    (503, 66, 'I', 'in the Fund book: sell 1 ACME Dec 18 2026 210 calls gtc; sell 1 ACME Dec 18 2026 220/230/240 put butterfly in the Personal book at I1:PB_MS at 2.10 and buy 1 ACME Dec 18 2026 240/250 call spread in the Fund book'),
    (503, 71, 'P', 'bto 2 ACME Dec 18 2026 260 put in the Institutional book at I1:PB_MS limit 41 as a package and short 3 WIDG Dec 18 2026 45 calls in the Personal and Institutional books at us exchange gtc; buy 1 ACME Dec 18 2026 210/220 put spread'),
    (503, 75, 'F', 'sto 4 ACME Dec 18 2026 220 put at I1:PB_MS and collar 100 ACME Dec 18 2026 220/260 in the Fund book at I1:PB_MS at 2.10 and btc 4 ACME Dec 18 2026 260 calls in the Personal and Institutional books at I1:PB_MS'),
    (503, 79, 'F', 'buy 1 ACME Dec 18 2026 250 calls in the Personal and Institutional books and bto 2 ACME Dec 18 2026 240 calls in the Fund book at I1:PB_MS; 300 GIZM in the Personal book at us exchange at 2.10'),
    (504, 4, 'P', 'buy 1 ACME Dec 18 2026 220/230/240 put butterfly at pb ms limit 1.00, buy 300 ZORB at pb ms limit 1.00 as a package and sto 4 ACME Dec 18 2026 260 call in the Personal book at I1:PB_MS'),
    (504, 23, 'P', 'short 150 GIZM in the Fund book at I1:PB_MS gtc and bto 150 LOTX at pb ms, sell 1 ACME Dec 18 2026 210/220 put spread in the Institutional book at I1:PB_MS as a package; 3 ACME Dec 18 2026 240 calls at us exchange'),
    (504, 27, 'P', 'in the Institutional book: sell 2 ACME Dec 18 2026 220 put in the Fund book at I1:PB_MS limit 1.00 and buy 1 ACME Dec 18 2026 210/220 put spread'),
    (504, 30, 'F', 'buy 2 ACME Dec 18 2026 240 calls at pb ms at 2.10; buy 1 LOTX in the Institutional book and bto 2 ACME Dec 18 2026 215 calls in the Fund book at I1:PB_MS as a package'),
    (504, 39, 'F', 'sell 3 ACME Dec 18 2026 250 calls in the Personal book at I1:PB_MS as a package, stc 400 GIZM as a package and 200 LOTX at us exchange and bto 2 ACME Dec 18 2026 220 call at us exchange limit 41'),
    (504, 48, 'P', 'buy 2 ACME Dec 18 2026 215 calls in the Fund book at I1:PB_MS limit 41'),
    (504, 62, 'I', 'short 3 WIDG Dec 18 2026 45 calls at I1:PB_MS gtc and 1 LOTX in the Personal book at I1:PB_MS limit 41'),
    (504, 63, 'I', 'sto 5 WIDG in the Institutional book at I1:PB_MS; sto 3 ACME Dec 18 2026 260 calls in the Fund book and 2 ACME Dec 18 2026 215 calls in the Fund book at I1:PB_MS'),
    (504, 69, 'I', 'buy 150 WIDG in the Personal book at pb ms; bto 1 ACME Dec 18 2026 250 calls in the Fund book at I1:PB_MS; (buy 200 ZORB at I1:PB_MS limit 1.00)'),
)
#: the seeds the fifth review's generative test of the parse and its choices reported (P8 words not understood, P4 / P5 an order moved,
#: P9 a typed year read as the limit), and the six it reported once a fix inside a package was offered: (seed, world, selected book, the
#: texts it walked - the instruction, then the text after each choice it applied on 8df9869)
PT5_SEED_TEXTS = (
    (525263, 'A', 'F', (                                       # found by this fix's own 12,000-case run: a closing phrase in a later order
        'cover 1,000 WIDG in the Fund book, and 2 WIDG 50p Nov 2026, close all GIZM in the Institutional book',
        'cover 1,000 WIDG in the Fund book, and 2 WIDG 45p Nov 2026, close all GIZM in the Institutional book',
    )),
    (520091, 'A', 'P', (
        'collar 100 GIZM Dec 18 2026 45/55; covered call 300 GIZM Dec 18 2026 55 limit 0.50 in the Institutional book',
        'collar 100 GIZM Dec 18 2026 45/55 at pb ms; covered call 300 GIZM Dec 18 2026 55 limit 0.50 in the Institutional book at us exchange',
        'collar 100 GIZM Dec 18 2026 45/55 at pb ms; covered call 300 GIZM Dec 18 2026 0.50 in the Institutional book at us exchange',
    )),
    (520130, 'B', 'P', (
        'cover 300 GIZM in the Institutional book, and buy write 400 GIZM Dec 2026 50 in the Personal book',
        'cover 300 GIZM in the Institutional book at us exchange, and buy write 400 GIZM Dec 2026 50 in the Personal book',
    )),
    (520300, 'B', 'P', (
        'sell 3 ACME Dec 18 2026 250 calls in the Institutional book and buy 1 GIZM Dec 18 2026 55 put',
        'sell 3 ACME Dec 18 2026 250 calls in the Institutional book at us exchange and buy 1 GIZM Dec 18 2026 55 put',
        'sell 3 ACME Dec 18 2026 250 calls in the Institutional book at at pb ms and buy 1 GIZM Dec 18 2026 55 put',
    )),
    (520370, 'B', 'P', (
        'bto 3 contracts ACME Jan 15 2027 240 puts limit in F day and covered call 300 WIDG Dec 18 2026 45 in the Institutional book, buy 2 ACME Dec 18 2026 240 calls; buy to close all ACME Dec 18 230 put',
        'bto 3 contracts ACME Jan 15 2027 240 puts limit in F day and covered call 200 WIDG Dec 18 2026 45 in the Institutional book, buy 2 ACME Dec 18 2026 240 calls; buy to close all ACME Dec 18 230 put',
        'bto 3 contracts ACME Jan 15 2027 240 puts limit in F day and covered call 200 WIDG Dec 18 2026 45 in the Institutional book at us exchange, buy 2 ACME Dec 18 2026 240 calls; buy to close all ACME Dec 18 230 put',
    )),
    (520901, 'A', 'F', (
        "sell 3 ACME Dec 18 2026 250 calls in the Institutional book then sell short halfx ACME Dec 18 '26 250 put",
        "sell 3 ACME Dec 18 2026 250 calls in the Institutional book at us exchange then sell short halfx ACME Dec 18 '26 250 put",
        "sell 3 ACME Dec 18 2026 250 calls in the Institutional book at at pb ms then sell short halfx ACME Dec 18 '26 250 put",
    )),
    (520929, 'A', 'P', (
        'bto 50 WIDG in the Fund book and collar 100 GIZM Dec 2026 45/50 in the Institutional book',
        'bto 50 WIDG in the Fund book and collar 100 GIZM Dec 2026 45/50 in the Institutional book at us exchange',
    )),
    (520974, 'B', 'P', (
        'sto 150 acme and close 100 GIZM; 100 lots WIDG day limit 3.10, and cover 300 GIZM in the Institutional book',
        'sto 150 acme and close 100 GIZM at pb ms; 100 lots WIDG day limit 3.10 at pb ms, and cover 300 GIZM in the Institutional book at us exchange',
        'sto 150 acme in the Institutional book at us exchange and close 100 GIZM at pb ms in the Institutional book; 100 lots WIDG day limit 3.10 at pb ms in the Institutional book, and cover 300 GIZM in the Institutional book at at pb ms',
    )),
    (521065, 'A', 'P', (
        "cover 300 GIZM in the Institutional book and collar 100 ACME Dec 18 '26 210/250 aon at market in the Personal book in the Fund book",
        "cover 300 GIZM in the Institutional book at us exchange and collar 100 ACME Dec 18 '26 210/250 aon at market in the Personal book in the Fund book",
    )),
    (521624, 'B', 'F', (
        'sell to open 2.5 lots ACME at 2.50, sell 2 ACME Dec 18 2026 250 calls in the Institutional book',
        'sell to open 2.5 lots ACME at 2.50, sell 2 ACME Dec 18 2026 250 calls in the Institutional book at us exchange',
        'short 2.5 lots ACME at 2.50, sell 2 ACME Dec 18 2026 250 calls in the Institutional book at us exchange',
    )),
    (521816, 'B', 'P', (
        'sell 2 ACME Dec 18 2026 250 calls in the Institutional book then roll 1 WIDG Dec 18 45 calls to Nov 20 2026 40 gtd Dec 18 2026 at pb',
        'sell 2 ACME Dec 18 2026 250 calls in the Institutional book at us exchange then roll 1 WIDG Dec 18 45 calls to Nov 20 2026 40 gtd Dec 18 2026 at pb',
    )),
    (521841, 'A', 'P', (
        'buy 50 GIZM in the Institutional book then roll 3 GIZM Dec 2026 45 puts to Dec 2026 55',
        'buy 50 GIZM in the Institutional book at us exchange then roll 3 GIZM Dec 2026 45 puts to Dec 2026 55',
    )),
    (521162, 'B', 'F', (
        '2 ACME Jan 15 2027/Dec 19 2026 200 put calendar at us exchange in the Personal book gtd Dec 18 2026 and bto 100 ACME in the Fund book',
        '2 ACME Jan 15 2027/2026-12-18 200 put calendar at us exchange in the Personal book gtd Dec 18 2026 and bto 100 ACME in the Fund book',
        'buy 2 ACME Jan 15 2027/2026-12-18 200 put calendar at us exchange in the Personal book gtd Dec 18 2026 and bto 100 ACME in the Fund book',
    )),
    (521996, 'B', 'P', (
        "roll ACME Dec 19 2026 230 calls to Dec 18 '26 250 2.50 limit, and 1 ACME Dec 18/Dec 19 2026 240 put calendar at P1:US_EXCHANGE limit 225",
        "roll ACME 2027-01-15 230 calls to Dec 18 '26 250 2.50 limit, and 1 ACME Dec 18/Dec 19 2026 240 put calendar at P1:US_EXCHANGE limit 225",
        "roll ACME 2027-01-15 230 calls to Dec 18 '26 250 2.50 limit, and sell 1 ACME Dec 18/Dec 19 2026 240 put calendar at P1:US_EXCHANGE limit 225",
    )),
    (521313, 'A', 'F', (
        '(200 WIDG), buy 100 ACME, sell 1 ACME Dec 18 2026 260 call',
    )),
    (520120, 'B', 'P', (
        'in the Fund book: buy to cover 2.5 lots WIDG at pb ms in the Fund book, and halfx ACME Dec 2026 240 call at us exchange gtd Dec 18 2026',
    )),
    (531068, 'B', 'P', (
        'roll ACME 12/18/26 245 calls to Dec 18 200 and roll 1 WIDG Dec 2026 45 calls to Dec 2026 35 aon limit 1.00, and sell 2x GIZM Dec 2026 50 put, sto 2 GIZM Dec 18 2026/Dec 18 2026 45 call calendar at 1.20',
        'roll ACME 12/18/26 245 calls to Dec 18 200 and roll 1 WIDG Dec 2026 45 calls to Dec 2026 35 limit 1.00, and sell 2x GIZM Dec 2026 50 put, sto 2 GIZM Dec 18 2026/Dec 18 2026 45 call calendar at 1.20',
    )),
    (531121, 'A', 'P', (
        '10 contracts WIDG 40p Dec 18 2026 together limit 0.50 in the Fund book',
        'sell 10 contracts WIDG 40p Dec 18 2026 together limit 0.50 in the Fund book',
    )),
    (525800, 'B', 'P', (
        'buy to cover 20 WIDG then (close 100 GIZM in the Fund book) then buy 100 ACME, sell 1 ACME Dec 18 2026 260 call good till cancelled, in the Institutional book at pb ms',
    )),
    (522146, 'B', 'P', (
        '(sell 1 ACME Jan 15 2027/Dec 19 2026 210 call calendar at 1.20 and sell short 300 lots LOTX limit 1.00 gtd Dec 18 2026 in F)',
        '(sell 1 ACME Jan 15 2027/2026-12-18 210 call calendar at 1.20 and sell short 300 lots LOTX limit 1.00 gtd Dec 18 2026 in F)',
    )),
    (522608, 'B', 'F', (
        'stc 1 ACME Dec 18 2026/Dec 19 2026 210 put calendar, and buy 1,5000 ACME limit 225',
    )),
    (523301, 'A', 'P', (
        'at pb ms: buy 2 ACME Dec 18/Dec 19 2026 215 put calendar and bto 150 ACME together at pb ms',
        'at pb ms: buy 2 ACME Dec 18/Dec 19 2026 215 put calendar and buy 150 ACME together at pb ms',
    )),
    (526009, 'A', 'P', (
        '(bto 3 ACME 2026-12-18 215 put at the mid in F and sell short 1 ACME Jan 2027/Dec 19 2026 215 call calendar limit 3.10 day)',
    )),
    (526287, 'A', 'F', (
        'buy to close 200 WIDG gtc in the Foo book; buy all ACME Dec 18 2026 250 calls, cover 2 ACME Dec 18/Dec 19 2026 260 call calendar then btc 3x GIZM Dec 2026 50 puts 2.50 limit in the Personal book',
        'buy to close 200 WIDG gtc in the Foo book; close all ACME Dec 18 2026 250 calls, cover 2 ACME Dec 18/Dec 19 2026 260 call calendar then btc 3x GIZM Dec 2026 50 puts 2.50 limit in the Personal book',
        'buy to close 200 WIDG gtc in the Foo book; close all ACME Dec 18 2026 250 calls, cover 2 ACME Dec 18/2027-01-15 260 call calendar then btc 3x GIZM Dec 2026 50 puts 2.50 limit in the Personal book',
    )),
    (527250, 'B', 'I', (
        'in the Institutional book at pb: covered call 100 ACME 12/18/26 250 and bto 2 ACME Dec 18 2026/Dec 19 2026 245 put calendar',
    )),
    (528342, 'B', 'P', (
        'buy half WIDG Dec 2026 45 put, cover 2 ACME Jan 2027/Dec 19 2026 240 put calendar limit in the Foo book',
        'buy half WIDG Dec 2026 45 put, cover 2 ACME Jan 2027/2027-01-15 240 put calendar limit in the Foo book',
    )),
    (528670, 'B', 'P', (
        'in the Institutional book: bto 2 ACME Dec 18/Dec 19 2026 215 put calendar and cover 3 GIZM Dec 2026 45/50/55 condor in the Foo book limit',
        'in the Institutional book: bto 2 ACME Dec 18/Dec 19 2026 215 put calendar and cover 3 GIZM Dec 2026 45/50/55 condor in the Institutional book limit',
        'in the Institutional book: bto 2 ACME Dec 18/2027-01-15 215 put calendar and cover 3 GIZM Dec 2026 45/50/55 condor in the Institutional book limit',
    )),
    (528789, 'A', 'P', (
        'sell 1 ACME Dec 18 2026/Dec 19 2026 240 put calendar; buy 1 ACME Dec 18 2026 215 call',
        'sell 1 ACME Dec 18 2026/2027-01-15 240 put calendar; buy 1 ACME Dec 18 2026 215 call',
    )),
    (522070, 'B', 'F', (
        'cover 300 GIZM in the Institutional book',
        'cover 300 GIZM in the Institutional book at us exchange',
    )),
    (520183, 'A', 'P', (
        'bto 1 GIZM Dec 2026 50/55 strangle day in the Personal book, buy 3 acme, and (short 3 WIDG Dec 2026 45 puts in F and short 3x WIDG Dec 2026 35 put at F1:US_EXCHANGE in the Fund book)',
        'bto 1 GIZM Dec 2026 50/55 strangle day in the Personal book, buy 3 acme, and (sto 3 WIDG Dec 2026 45 puts in F and short 3x WIDG Dec 2026 35 put at F1:US_EXCHANGE in the Fund book)',
    )),
    (520339, 'A', 'P', (
        'short 100 contracts LOTX in the Institutional book limit then sell to open 3 WIDG Dec 2026 35/50 call spread at 41, and sto 1 lots ACME 2.50 limit and stc 10 ACME Dec 2026 215 puts aon',
        'short 100 contracts LOTX in the Institutional book limit then sell to open 3 WIDG Dec 2026 35/50 call spread at 41, and short 1 lots ACME 2.50 limit and stc 10 ACME Dec 2026 215 puts aon',
        'short 100 LOTX in the Institutional book limit then sell to open 3 WIDG Dec 2026 35/50 call spread at 41, and short 1 lots ACME 2.50 limit and stc 10 ACME Dec 2026 215 puts aon',
    )),
    (520409, 'A', 'P', (
        '(sell 1 ACME Jan 2027/Dec 18 240 put calendar and btc 150 ACME together at P1:US_EXCHANGE in the Personal book)',
    )),
    (521405, 'A', 'F', (
        "(buy write 200 ACME Dec 18 260 day at 1.20, collar 100 ACME Dec 18 '26 215/245 together at 2.50)",
        "(buy write 200 ACME Dec 18 260 day at 1.20, collar 100 ACME Dec 18 '26 215/240 together at 2.50)",
        "(buy write 200 ACME Dec 18 260 day at 1.20, collar 100 ACME Dec 18 '26 215/240 together)",
    )),
    (521678, 'B', 'F', (
        '(buy write 100 contracts ACME 12/18/26 240 limit 1.00 and cover 1x ACME Dec 2026 200 call)',
        '(buy write 10000 ACME 12/18/26 240 limit 1.00 and cover 1x ACME Dec 2026 200 call)',
        '(buy write 10000 ACME 12/18/26 240 and cover 1x ACME Dec 2026 200 call)',
    )),
    (521971, 'A', 'P', (
        '(cover 3x ACME Dec 2026 210 calls and bto 5x ACME 12/18/26 240 call)',
    )),
)


def _fifth_goes(out, l) -> bool:
    return l["request"] is not None and (l["combo"] is None or any(c["id"] == l["combo"] and c["request"] is not None for c in out["combos"]))


def _fifth_body(l) -> tuple:
    return (l["book"], l["account_id"], l["side"], l["qty"], l["instrument"].get("id"), l["order_type"], l["limit_px"], l["stop_px"], l["trail_amt"],
            l["trail_pct"], l["tif"], l["gtd_date"], "ALREADY_SENT" in codes(l["flags"]))


def _fifth_unparsed(out) -> set:
    return {f["text"] for f in [f for l in out["legs"] for f in l["flags"]] + [f for c in out["combos"] for f in c["flags"]] + out["flags"]
            if f["code"] == "UNPARSED_TEXT"}


def choice_contract(out, item, f, ch, out2, books=FIFTH_BOOKS) -> list:
    """What a choice applied as the page applies it changed beyond the order it is on (1.0.7.2 fifth review; an empty list when nothing):
    C1 every order outside the clause(s) it is on, matched by position, keeps its order body and package and never becomes sendable (a
    leg whose own request stood and whose package with the choice's order the choice completes excepted); C2 the other books' orders of a
    clause repeated per book read as before, unless the same words are offered on them; C3 the order it is on stays in its account (an
    account or book choice excepted); C4 its words parse; C5 a split's label stages exactly those sides and quantities, "in the X Book"
    stages book X; C6 no limit staged is the year of a typed date."""
    probs = []
    new_words = _fifth_unparsed(out2) - _fifth_unparsed(out)
    if new_words:
        probs.append(f"C4 words not understood after the choice: {sorted(new_words)[:3]}")
    for l in out2["legs"]:
        px = str(l.get("limit_px") or "")
        if _fifth_goes(out2, l) and re.fullmatch(r"20\d\d", px) and re.search(FIFTH_YEAR + px + r"\b", out2["text"]):
            probs.append(f"C6 leg {l['n']} is sendable at LIMIT {px}, the year of its typed date")
    cl0, cl2 = st.parse(out["text"], books)["clauses"], st.parse(out2["text"], books)["clauses"]
    ix0 = {tuple(c["span"]): i for i, c in enumerate(cl0)}
    ix2 = {tuple(c["span"]): i for i, c in enumerate(cl2)}
    ci0 = {l["n"]: ix0.get(tuple(l["span"])) for l in out["legs"]}
    ci2 = {l["n"]: ix2.get(tuple(l["span"])) for l in out2["legs"]}
    if None in ci0.values() or None in ci2.values():
        return probs
    own = set(item["legs"]) if "fill_mode" in item else {item["n"]}
    if f["code"] == "COVER_HINT":
        m = re.search(r"legs (\d+) and (\d+)", f["text"])
        own |= {int(m.group(1)), int(m.group(2))} if m else set()
    own_ci = sorted({ci0[n] for n in own})
    lo, hi = own_ci[0], own_ci[-1]
    before, after = list(range(lo)), list(range(hi + 1, len(cl0)))
    if len(cl2) < len(before) + len(after) + 1:
        return probs + ["C1 the other orders cannot be matched one to one"]
    m02 = {i: i for i in before}
    for k, i in enumerate(reversed(after)):
        m02[i] = len(cl2) - 1 - k
    inv = {j: i for i, j in m02.items()}
    mid2 = [j for j in range(len(cl2)) if j not in inv]
    of0 = {n: cb for cb in out["combos"] for n in cb["legs"]}
    of2 = {n: cb for cb in out2["combos"] for n in cb["legs"]}

    def p0(x):
        return frozenset(ci0[m] if ci0[m] in m02 else "IN" for m in of0[x["n"]]["legs"] if m != x["n"]) if x["n"] in of0 else frozenset()

    def p2(y):
        return frozenset(inv.get(ci2[m], "IN") for m in of2[y["n"]]["legs"] if m != y["n"]) if y["n"] in of2 else frozenset()

    def offered_too(x):
        for it in (x, of0.get(x["n"])):
            for g in (it or {}).get("flags") or []:
                if any(c["clause"] == ch["clause"] for c in g.get("choices") or []):
                    return True
        return False
    by0, by2 = {}, {}
    for l in out["legs"]:
        by0.setdefault(ci0[l["n"]], []).append(l)
    for l in out2["legs"]:
        by2.setdefault(ci2[l["n"]], []).append(l)
    for i, j in m02.items():                                                              # C1
        x0, y0 = by0.get(i, []), by2.get(j, [])
        if len(x0) != len(y0):
            probs.append(f"C1 order {i + 1} has {len(x0)} -> {len(y0)} legs")
            continue
        for x, y in zip(x0, y0):
            if offered_too(x):
                continue
            if _fifth_body(x) != _fifth_body(y):
                probs.append(f"C1 leg {x['n']} {_fifth_body(x)} -> {_fifth_body(y)}")
            if _fifth_goes(out2, y) and not _fifth_goes(out, x) and not (x["request"] is not None and "IN" in p0(x)):
                probs.append(f"C1 leg {x['n']} {_fifth_body(x)} becomes sendable")
            if p0(x) != p2(y):
                probs.append(f"C1 leg {x['n']} package {sorted(map(str, p0(x)))} -> {sorted(map(str, p2(y)))}")
    own_books = {l["book"] for l in out["legs"] if l["n"] in own}
    if f["code"] not in FIFTH_PLACE and None not in own_books:                           # C2
        others = [l for i in own_ci for l in by0.get(i, []) if l["n"] not in own and l["book"] not in own_books]
        skip = {l["book"] for l in others if offered_too(l)}
        was = sorted(((_fifth_body(l), _fifth_goes(out, l)) for l in others if l["book"] not in skip), key=repr)
        now = sorted(((_fifth_body(l), _fifth_goes(out2, l)) for j in mid2 for l in by2.get(j, []) if l["book"] not in own_books and l["book"] not in skip), key=repr)
        if [x[0] for x in was] != [x[0] for x in now] or any(g and (fa, True) not in was for fa, g in now):
            probs.append(f"C2 another book's order {was} -> {now}")
    mine = [l for j in mid2 for l in by2.get(j, []) if l["book"] in own_books]
    where = {(l["book"], l["account_id"]) for l in out["legs"] if l["n"] in own}
    if f["code"] not in FIFTH_PLACE and where and all(w[0] and w[1] for w in where):       # C3
        for l in mine:
            if _fifth_goes(out2, l) and (l["book"], l["account_id"]) not in where:
                probs.append(f"C3 the order the choice is on moved to {l['book']} {l['account_id']} (read in {sorted(where)})")
    m = FIFTH_SPLIT.match(ch["label"].split(" (")[0])                                    # C5
    if m and f["code"] in ("SIDE_SPLIT", "WOULD_OVERSELL", "ALREADY_CLOSING") and len(own_books) == 1:
        want = [(FIFTH_SIDE[m.group(1)], int(m.group(2).replace(",", "")))] + ([(FIFTH_SIDE[m.group(3)], int(m.group(4).replace(",", "")))] if m.group(3) else [])
        got = [(l["side"], l["qty"]) for l in mine]
        if got != want and got != [({"BUY": "BTO", "SELL": "STO"}.get(s, s), q) for s, q in want]:
            probs.append(f"C5 {ch['label']!r} stages {got}")
    bm = re.match(r"in the (\w+) Book$", ch["label"])
    if f["code"] == "ACCOUNT_NOT_IN_BOOK" and bm and not any(l["book"] not in own_books for i in own_ci for l in by0.get(i, [])):
        code = next((c for c, name, _p in books if name.split()[0] == bm.group(1)), None)
        moved = [l for j in mid2 for l in by2.get(j, [])]
        bad = sorted({str(l["book"]) for l in moved} - {code}) if moved else ["(no leg)"]
        if bad:
            probs.append(f"C5 {ch['label']!r} stages the order in {bad}")
    return probs


class _FifthReviewBase(_ResolverBase):
    WORLD_KIND = "B"
    SENT = (("O-ST5-BTC", "st:5b5b5b5b5b01:L2:r1", C240, "BTC", 2, "2 ACME Dec 18 2026 240 calls"),
            ("O-ST5-STC", "st:5b5b5b5b5b02:L2:r1", C250, "STC", 3, "3 ACME Dec 18 2026 250 calls"))
    #: world A's two working closing orders (fictional ids): a buy to close of the Personal book's 2 short Dec-18 240 calls, a sell to close
    #: of 2 of the Dec-18 250 calls held at I1:PB_MS
    WORKING_A = [{"order_id": "O-ST5-W2", "request_id": "test:st5-working-2", "account_id": P, "instrument_id": C240, "side": "BTC", "qty": 2,
                  "order_type": "LIMIT", "limit_px": "1.00", "tif": "DAY", "status": "WORKING", "session_group": "US_EQUITY_OPTIONS"},
                 {"order_id": "O-ST5-W3", "request_id": "test:st5-working-3", "account_id": PB, "instrument_id": C250, "side": "STC", "qty": 2,
                  "order_type": "LIMIT", "limit_px": "9.00", "tif": "DAY", "status": "WORKING", "session_group": "US_EQUITY_OPTIONS"}]

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = cls._make()
        world = dict(corpus.WORLD)
        world["chains"] = list(corpus.WORLD["chains"]) + [{"underlying": "WIDG", "cboe": "WIDG", "spot": 40, "expiries": ("2026-11-20", corpus.DEC),
                                                           "strikes": (35, 40, 45), "skip": (), "age_s": 30, "trade_time": corpus.TRADE_TIME, "extra": ()}]
        if cls.WORLD_KIND == "A":
            world["positions"] = list(corpus.WORLD["positions"]) + [(F, "WIDG", "SHORT", 100, "40")]
            world["working"] = list(corpus.WORLD["working"]) + cls.WORKING_A
        else:
            world["positions"] = ([x for x in corpus.WORLD["positions"] if not (x[0] == P and x[1] in (C240, C250))]
                                  + [(F, "WIDG", "SHORT", 100, "40"), (PB, "WIDG", "LONG", 200, "40")])
        build_world(unittest.TestCase(), cls.rt, world)
        if cls.WORLD_KIND == "B":
            now = int(cls.rt.clock.epoch())
            with cls.rt.db.writer() as tx:                           # fixture only: two Smart Ticket orders sent and filled today
                for oid, rid, iid, side, qty, clause in cls.SENT:
                    payload = json.dumps({"account_id": P, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY",
                                          "smart": {"ticket": rid.split(":")[1], "item": "L2", "clause": clause}}, sort_keys=True)
                    tx.conn.execute("INSERT INTO commands(request_id, kind, actor, payload_json, payload_sha256, result_json, http_status, started_ts, finished_ts,"
                                    " duration_ms, app_version, state_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (rid, "order.submit", "ui", payload, "0" * 64, "{}", 200, now, now, 1, "test", 0))
                    tx.conn.execute("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, filled_qty, order_type, tif, status, session_group,"
                                    " created_ts, updated_ts, expires_ts, preview_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'{}')",
                                    (oid, rid, P, iid, side, qty, qty, "MARKET", "DAY", "FILLED", "US_EQUITY_OPTIONS", now, now, now + 7200))

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def find(self, out, code, label, book=None):
        for item in out["legs"] + out["combos"]:
            if book is not None and item.get("book") != book:
                continue
            for f in item["flags"]:
                for ch in (f.get("choices") or []) if f["code"] == code else []:
                    if label in ch["label"]:
                        return item, f, ch
        self.fail(f"no {code} choice labelled {label!r}" + (f" on the {book} order" if book else "") + f" on {out['text']!r}: "
                  + json.dumps([(l["n"], l["book"], [(x["code"], [c["label"] for c in x.get("choices") or []]) for x in l["flags"]]) for l in out["legs"]])[:3000])

    def apply(self, out, f, ch, book):
        a, b = f["span"]
        return self.parse(out["text"][:a] + ch["clause"] + out["text"][b:], book)

    def every_choice_keeps_the_contract(self, rows) -> tuple:
        """Every choice offered on each ``(label, book, text)``, applied as the page applies it, checked with ``choice_contract``."""
        problems, applied = [], 0
        for label, book, text in rows:
            out = self.parse(text, book)
            seen = set()
            for item in out["legs"] + out["combos"]:
                for f in item["flags"]:
                    for ch in f.get("choices") or []:
                        key = (f["code"], tuple(f["span"]), ch["clause"], item.get("n"), tuple(item["legs"]) if isinstance(item.get("legs"), list) else None)
                        if key in seen:
                            continue
                        seen.add(key)
                        try:
                            out2 = self.apply(out, f, ch, book)
                        except st.SmartTicketError:
                            continue
                        applied += 1
                        for p in choice_contract(out, item, f, ch, out2):
                            problems.append(f"{label} book {book}: {text!r}\n    {f['code']} {ch['label']!r} -> {ch['clause']!r}\n    {p}")
        return problems, applied


class ChoicesFifthReview(_FifthReviewBase):
    """1.0.7.2 fifth review (world B: the corpus's, a WIDG chain, 100 WIDG short in the Fund book, 200 WIDG long at I1:PB_MS, the Personal
    book's Dec-18 240 / 250 calls closed today by two Smart Ticket orders): an order already sent keeps its reading when a choice on
    another order writes out its book or account (R2 reads a book phrase naming its own book, and an account phrase naming the account it
    went to, as the same words); a choice on one book's order of a clause repeated per book is written for that book alone; the shares
    split offers the cover alone; a book choice replaces the order's own book phrase; a fix inside a package and every book an unknown
    book word may mean are offered; an account typed with "at" is replaced whole; the cover hint keeps the pair in the account it was
    read in; and every case the review's property tests reported keeps the choice contract."""

    def test_an_order_already_sent_keeps_its_reading_when_a_choice_writes_out_its_book_or_account(self):
        for text, code, label, iid, side, want in (
                ("buy 300 GIZM in the Personal book and 2 ACME Dec 18 2026 240 calls", "SIDE_SPLIT", "cover 200 only", C240, "BTC",
                 "cover 200 GIZM in the Personal book and buy 2 ACME Dec 18 2026 240 calls"),
                ("buy 300 GIZM at us exchange and 2 ACME Dec 18 2026 240 calls", "SIDE_SPLIT", "cover 200 only", C240, "BTC", None),
                ("sell 400 WIDG in the Personal book and 3 ACME Dec 18 2026 250 calls", "WOULD_OVERSELL", "sell 200 + short 200", C250, "STC", None),
                ("buy 2 ACME Dec 18 2026 240 calls and buy 1 ACME Dec 18 2026 240/250 call spread at I1:PB_MS", "ACCOUNT_NOT_IN_BOOK", "in the Institutional Book",
                 C240, "BTC", "buy 2 ACME Dec 18 2026 240 calls in the Personal book and buy 1 ACME Dec 18 2026 240/250 call spread at I1:PB_MS in the Institutional book")):
            with self.subTest(text=text):
                out = self.parse(text, "P")
                item, f, ch = self.find(out, code, label)
                if want is not None:
                    self.assertTrue(ch["clause"].startswith(want) or ch["clause"] == want, ch["clause"])
                out2 = self.apply(out, f, ch, "P")
                sent = [l for l in out2["legs"] if l["instrument"].get("id") == iid and l["combo"] is None]
                self.assertEqual([(side, True)], [(l["side"], "ALREADY_SENT" in codes(l["flags"])) for l in sent], out2["text"])
                self.assertEqual([], choice_contract(out, item, f, ch, out2))
        # R2 itself: the words with a book phrase naming their own book (one or several) or the account they went to read as the order sent
        for text in ("buy 2 ACME Dec 18 2026 240 calls in the Personal book", "buy 2 ACME Dec 18 2026 240 calls at us exchange",
                     "buy 2 ACME Dec 18 2026 240 calls in the Personal and Fund books", "buy 100 WIDG and 2 ACME Dec 18 2026 240 calls in the Personal book"):
            leg = next(l for l in self.parse(text, "P")["legs"] if l["book"] == "P" and l["instrument"].get("id") == C240)
            self.assertEqual(("BTC", True), (leg["side"], "ALREADY_SENT" in codes(leg["flags"])), text)
        leg = self.one("buy 2 ACME Dec 18 2026 240 calls at pb ms", "P")                   # another account: another order
        self.assertNotIn("ALREADY_SENT", codes(leg["flags"]))

    def test_a_choice_on_one_books_order_is_written_for_that_book_alone(self):
        for text, code, label, on, other, want in (
                ("sell 300 WIDG in the Personal and Fund books", "WOULD_OVERSELL", "short 300", "F", "P", "sell 300 WIDG in the Personal book; short 300 WIDG in the Fund book"),
                ("sell 300 WIDG in the Personal and Fund books", "WOULD_OVERSELL", "sell 200 + short 100", "P", "F",
                 "sell 200 WIDG in the Personal book and short 100 WIDG in the Personal book; sell 300 WIDG in the Fund book"),
                ("sell 3 ACME Dec 18 2026 250 calls in the Personal and Institutional books", "SIDE_SPLIT", "stc 2 only", "I", "P",
                 "sell 3 ACME Dec 18 2026 250 calls in the Personal book; stc 2 ACME Dec 18 2026 250 calls in the Institutional book")):
            with self.subTest(text=text, label=label):
                out = self.parse(text, "P")
                item, f, ch = self.find(out, code, label, on)
                self.assertEqual(want, ch["clause"])
                out2 = self.apply(out, f, ch, "P")
                was = sorted((l["side"] or "", l["qty"] or 0, l["account_id"] or "", _fifth_goes(out, l)) for l in out["legs"] if l["book"] == other)
                now = sorted((l["side"] or "", l["qty"] or 0, l["account_id"] or "", _fifth_goes(out2, l)) for l in out2["legs"] if l["book"] == other)
                self.assertEqual(was, now)
                self.assertEqual([], choice_contract(out, item, f, ch, out2))

    def test_the_shares_split_offers_the_cover_alone(self):
        for text, book, label, cover in (("buy 300 GIZM", "P", "cover 200 only (buy the other 100 once the cover has filled)", 200),
                                         ("buy 400 WIDG", "F", "cover 100 only (buy the other 300 once the cover has filled)", 100)):
            with self.subTest(text=text):
                leg = self.one(text, book)
                f = flag(leg, "SIDE_SPLIT")
                self.assertEqual([label], [c["label"] for c in f["choices"]])
                self.assertEqual([("BUY_TO_COVER", cover)], [(l["side"], l["qty"]) for l in self.parse(f["choices"][0]["clause"], book)["legs"]])
        # the option split names explicit sides and stages exactly them
        f = flag(self.one("sell 3 ACME Dec 18 2026 250 calls in the Institutional book", "P"), "SIDE_SPLIT")
        self.assertEqual([("STC", 2), ("STO", 1)], [(l["side"], l["qty"]) for l in self.parse(f["choices"][0]["clause"], "P")["legs"]])

    def test_a_book_choice_replaces_the_orders_own_book_phrase(self):
        for text, want in (("buy 4 ACME Dec 18 2026 220 put at I1:PB_MS", "buy 4 ACME Dec 18 2026 220 put at I1:PB_MS in the Institutional book"),
                           ("buy 4 ACME Dec 18 2026 220 put in the Personal book at I1:PB_MS", "buy 4 ACME Dec 18 2026 220 put in the Institutional book at I1:PB_MS"),
                           ("sell 2 ACME Dec 18 2026 260 call in the Fund book at I1:PB_MS gtc", "sell 2 ACME Dec 18 2026 260 call in the Institutional book at I1:PB_MS gtc")):
            with self.subTest(text=text):
                out = self.parse(text, "P")
                _item, f, ch = self.find(out, "ACCOUNT_NOT_IN_BOOK", "in the Institutional Book")
                self.assertEqual(want, ch["clause"])
                out2 = self.apply(out, f, ch, "P")
                self.assertEqual([("I", PB, False)], [(l["book"], l["account_id"], "DUPLICATE_FIELD" in codes(l["flags"])) for l in out2["legs"]])

    def test_a_fix_inside_a_package_and_every_book_an_unknown_word_may_mean_are_offered(self):
        for text in ("(buy 1 ACME Dec 18 2026 240 call, sell 1 ACME Dec 18 2026 215 call)", "buy 1 ACME Dec 18 2026 240 call, sell 1 ACME Dec 18 2026 215 call as a package"):
            with self.subTest(text=text):
                out = self.parse(text, "P")
                item, f, ch = self.find(out, "STRIKE_NOT_LISTED", "210 call")
                self.assertEqual(["210 call", "220 call"], [c["label"] for c in f["choices"]])
                out2 = self.apply(out, f, ch, "P")
                self.assertEqual(1, len([c for c in out2["combos"] if c["request"] is not None]), out2["text"])     # the package typed, completed
                self.assertEqual([], choice_contract(out, item, f, ch, out2))
        leg = self.one("buy 100 ACME in the Fnd book", "P")
        self.assertEqual((None, None), (leg["book"], leg["request"]))                     # unread: never the selected book
        f = flag(leg, "UNPARSED_TEXT")
        self.assertEqual(["the Personal Book", "the Fund Book", "the Institutional Book"], [c["label"] for c in f["choices"]])
        self.assertEqual(["F"], [l["book"] for l in self.parse(f["choices"][1]["clause"], "P")["legs"]])

    def test_an_account_typed_with_at_is_replaced_whole(self):
        for text, want in (("sell 2 ACME Dec 18 2026 250 calls in the Institutional book at us exchange", ("STC", PB)),
                           ("cover 50 GIZM in the Institutional book at us exchange", ("BUY_TO_COVER", PB))):
            with self.subTest(text=text):
                out = self.parse(text, "P")
                _item, f, ch = self.find(out, "ACCOUNT_CHOICE", "(I1:PB_MS)")
                self.assertNotIn("at at", ch["clause"])
                out2 = self.apply(out, f, ch, "P")
                self.assertEqual([want + (True,)], [(l["side"], l["account_id"], _fifth_goes(out2, l)) for l in out2["legs"]], out2["text"])
                self.assertEqual(set(), _fifth_unparsed(out2))
        # twice in a row: 'US Exchange' and then the prime-broker account
        out = self.parse("cover 300 GIZM in the Institutional book", "P")
        _item, f, ch = self.find(out, "ACCOUNT_CHOICE", "(I1:US_EXCHANGE)")
        out = self.apply(out, f, ch, "P")
        _item, f, ch = self.find(out, "ACCOUNT_CHOICE", "(I1:PB_MS)")
        self.assertEqual("cover 300 GIZM in the Institutional book at pb ms", ch["clause"])

    def test_the_cover_hint_keeps_the_pair_in_the_account_it_was_read_in(self):
        text = "buy 50 WIDG then (close 100 GIZM in the Fund book) then buy 100 ACME, sell 1 ACME Dec 18 2026 260 call, in the Institutional book at pb ms"
        out = self.parse(text, "P")
        self.assertEqual({PB}, {l["account_id"] for l in out["legs"] if (l["instrument"].get("id") or "").startswith("ACME")})
        item, f, ch = self.find(out, "COVER_HINT", "group them")
        out2 = self.apply(out, f, ch, "P")
        pair = [l for l in out2["legs"] if (l["instrument"].get("id") or "").startswith("ACME")]
        self.assertEqual(({PB}, set()), ({l["account_id"] for l in pair}, _fifth_unparsed(out2)), out2["text"])
        self.assertEqual(["SEQUENTIAL"], [c["fill_mode"] for c in out2["combos"] if c["request"] is not None])
        self.assertEqual([], choice_contract(out, item, f, ch, out2))

    def test_every_case_the_reviews_property_tests_reported_keeps_the_choice_contract(self):
        rows = [(f"seed {seed} case {case}", book, text) for seed, case, book, text in PROP5_SEED_TEXTS]
        rows += [(f"seed {seed} step {k}", book, t) for seed, world, book, texts in PT5_SEED_TEXTS if world == "B" for k, t in enumerate(texts)]
        problems, applied = self.every_choice_keeps_the_contract(rows)
        self.assertEqual([], problems, f"{len(problems)} of {applied} applied choices break the choice contract:\n" + "\n".join(problems[:20]))
        self.assertGreater(applied, 150)


class ChoicesFifthReviewWorldA(_FifthReviewBase):
    """1.0.7.2 fifth review (world A: the corpus's, a WIDG chain, 100 WIDG short in the Fund book, two working closing orders): a year or a
    roll's target strike typed before "limit" is never the price; a calendar's listed-expiry choices parse and never repeat the other
    leg's expiry; the cover hint after a parenthesis and a connector parses; and every world-A case the review's property test reported
    keeps the choice contract."""
    WORLD_KIND = "A"

    def test_a_year_or_a_roll_strike_typed_before_limit_is_never_the_price(self):
        for text, book, req in (("buy 2 ACME 240c Dec 18 2026 limit", "F", None), ("buy 2 ACME 240c Dec 18 2026 limit 2.50", "F", ("LIMIT", "2.50")),
                                ("buy 2 WIDG 45c Dec 18 2026 lmt", "F", None), ("sell 2 ACME 250c Dec 18 2026 limit", "P", None)):
            with self.subTest(text=text):
                out = self.parse(text, book)
                self.assertEqual(1, len(out["legs"]))
                l = out["legs"][0]
                self.assertNotIn("YEAR_INFERRED", codes(l["flags"]))
                self.assertEqual(req, (l["request"]["order_type"], l["request"]["limit_px"]) if l["request"] else None)
                if req is None:
                    self.assertIn("MISSING_PRICE", codes(l["flags"]))
        out = self.parse("roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 limit 1.20", "P")
        self.assertEqual([C250, "ACME270115C00260000"], [l["instrument"].get("id") for l in out["legs"]])
        f = flag(out["combos"][0], "LIMIT_IN_AON")
        drop = next(c for c in f["choices"] if c["label"].startswith("drop the limits"))
        self.assertEqual("roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260", drop["clause"])
        out = self.parse("roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 limit", "P")
        self.assertEqual([C250, "ACME270115C00260000"], [l["instrument"].get("id") for l in out["legs"]])

    def test_a_calendars_expiry_choices_parse(self):
        for text in ("buy 1 ACME Dec 18 2026/Dec 19 2026 240 call calendar", "sell 1 ACME Jan 15 2027/Dec 19 2026 200 put calendar"):
            with self.subTest(text=text):
                out = self.parse(text, "P")
                offered = [(f, c) for l in out["legs"] for f in l["flags"] if f["code"] == "EXPIRY_NOT_LISTED" for c in f.get("choices") or []]
                self.assertTrue(offered, out["legs"])
                for f, c in offered:
                    self.assertNotIn("-", c["clause"].split("calendar")[0].split(" ACME ")[1])      # the date in words, never ISO after "/"
                    out2 = self.apply(out, f, c, "P")
                    self.assertEqual(set(), _fifth_unparsed(out2), c["clause"])
                    self.assertNotIn("a calendar takes two different expiries", json.dumps(out2))

    def test_the_cover_hint_after_a_parenthesis_and_a_connector_parses(self):
        out = self.parse("(buy 50 WIDG and sell 1 WIDG Nov 20 2026 45 call) then buy 100 ACME, sell 1 ACME Dec 18 2026 260 call", "F")
        item, f, ch = self.find(out, "COVER_HINT", "group them")
        out2 = self.apply(out, f, ch, "F")
        self.assertEqual(set(), _fifth_unparsed(out2))
        self.assertEqual([], choice_contract(out, item, f, ch, out2))

    def test_every_case_the_reviews_property_test_reported_keeps_the_choice_contract(self):
        rows = [(f"seed {seed} step {k}", book, t) for seed, world, book, texts in PT5_SEED_TEXTS if world == "A" for k, t in enumerate(texts)]
        problems, applied = self.every_choice_keeps_the_contract(rows)
        self.assertEqual([], problems, f"{len(problems)} of {applied} applied choices break the choice contract:\n" + "\n".join(problems[:20]))
        self.assertGreater(applied, 40)


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
        # fifth review: the budget holds when the 20 legs offer choices (each one checked as re-read): a split offered on every order,
        # and the same with an order that carries its side after each one
        for text in ("; ".join(["sell 3 ACME Dec 18 2026 250 calls in the Institutional book"] * 20),
                     "; ".join(["sell 3 ACME Dec 18 2026 250 calls in the Institutional book and 3 ACME Dec 18 2026 250 calls"] * 10)):
            self.post({"text": text, "book": "P"})
            best = None
            for _ in range(3):
                t0 = time.perf_counter()
                status, out, _h = self.post({"text": text, "book": "P"}, "?book=P")
                ms = (time.perf_counter() - t0) * 1000.0
                best = ms if best is None else min(best, ms)
            n = sum(len(f.get("choices") or []) for it in out["legs"] + out["combos"] for f in it["flags"])
            self.assertEqual((200, 20), (status, len(out["legs"])))
            self.assertGreaterEqual(n, 40)
            sys.stderr.write(f"smart ticket: a 20-leg parse offering {n} choices took {best:.1f} ms (limit 300 ms)\n")
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
