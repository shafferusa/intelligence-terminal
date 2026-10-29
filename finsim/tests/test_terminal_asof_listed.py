"""FINSIM1_PLAN P2 Part A (release 1.0.5): as-of listed bookings (C8), as-of funding (C20) and the funding side of CSA netting (C21) -
P2 acceptance 1 (g), (h), (i), (j), (k), gap OMS-04.  Every expected number is worked by hand in the comment next to it.

The world: the three real books built by ``server.build_runtime`` offline (FakeProvider, FakeClock, a temp home, no network) on
Wed 2026-10-21 10:00 ET, so the Fund book's capital (F1:TREASURY 25,000,000.00) carries business date 2026-10-21.  The clock then
moves past the trade date (Tue 2026-10-27 10:00 ET) and the as-of commands run as the historical import runs them (actor
``import``; the order ticket's actor ``ui`` is refused).  The option contracts are created inside the booking command from the
payload's spec and chain row (none exists before), exactly as the owner's batch IMP-F1-20261027-A books lines 29-33.

Equity / option fee: the DEFAULT schedule (equities USD 0.005 per share, min 1.00; options USD 0.65 per contract).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date, datetime

from tests.test_terminal_display_units import build, quote
from finsim.terminal import accounting, execution
from finsim.terminal.blotters import HISTORICAL_MODEL_LABEL
from finsim.terminal.clock import NY
from finsim.terminal.commands import Command

BUILD_AT = "2026-10-21T10:00:00-04:00"          # Wed: the capital of every book is dated 2026-10-21
NOW = "2026-10-27T10:00:00-04:00"               # Tue: the day the batch is entered
D = "2026-10-22"                                 # the options' trade date (the owner's closing prints)
EXEC = "2026-10-22T16:00:00-04:00"
US = "F1:US_EXCHANGE"
PB = "F1:PB_GS"
TRE = "F1:TREASURY"
BATCH = "IMP-TEST-P2A"
ORCL_C = "ORCL261120C00140000"
QQQ_P700 = "QQQ261120P00700000"
QQQ_P675 = "QQQ261120P00675000"


def ep(iso_ny: str) -> int:
    """New York wall time 'YYYY-MM-DDTHH:MM' -> epoch seconds."""
    return int(datetime.fromisoformat(iso_ny).replace(tzinfo=NY).timestamp())


EXEC_TS = ep("2026-10-22T16:00")                 # 2026-10-22 16:00 EDT = 20:00 UTC
NOW_TS = ep("2026-10-27T10:00")


def spec(occ: str, underlying: str, strike: str, right: str, chain_row=True) -> dict:
    return {"occ": occ, "underlying_id": underlying, "expiry": "2026-11-20", "strike": strike, "right": right, "style": "AMERICAN", "multiplier": 100,
            "chain_row": {"option": occ, "bid": "3.40", "ask": "3.70", "last_trade_price": "3.55"} if chain_row else None}


def single(line_key: str, iid: str = ORCL_C, side: str = "BTO", qty: int = 387, price: str = "3.55", inst_spec=None, account: str = US, **as_of) -> dict:
    block = {"trade_date": D, "exec_ts": EXEC, "batch_id": BATCH, "line_key": line_key}
    block.update(as_of)
    return {"account_id": account, "instrument_id": iid, "instrument_spec": inst_spec if inst_spec is not None else spec(ORCL_C, "ORCL", "140", "C"),
            "side": side, "qty": qty, "price": price, "order_type": "LIMIT", "tif": "DAY", "fee_rule": "SCHEDULE", "as_of": block}


def package_payload(line700="imp:p2a-leg700", line675="imp:p2a-leg675") -> dict:
    return {"account_id": US, "package": {"strategy_key": "BEAR_PUT_SPREAD", "fill_mode": "AON", "label": "QQQ 2026-11-20 700/675 put spread"},
            "legs": [{"line_key": line700, "instrument_id": QQQ_P700, "instrument_spec": spec(QQQ_P700, "QQQ", "700", "P"), "side": "BTO", "qty": 150,
                      "price": "17.64"},
                     {"line_key": line675, "instrument_id": QQQ_P675, "instrument_spec": spec(QQQ_P675, "QQQ", "675", "P"), "side": "STO", "qty": 150,
                      "price": "10.25"}],
            "order_type": "LIMIT", "tif": "DAY", "fee_rule": "SCHEDULE", "as_of": {"trade_date": D, "exec_ts": EXEC, "batch_id": BATCH}}


def h_asof_fund(ctx, p: dict) -> dict:
    """Test-only command: ``execution.fund`` of ``need_c`` for ``account_id`` (fill mode settling T+1 from the as-of date, or
    ``immediate``), under ``ctx.asof_date`` when ``asof`` is given - exactly what an as-of booking or a past-date lifecycle step does."""
    if p.get("asof"):
        ctx.asof_date = p["asof"]
    settle = None if p.get("immediate") else ctx.cal.settlement_date(date.fromisoformat(p.get("asof") or ctx.today), 1)
    # the sweep links to an existing entry (L19): the Fund book's initial capital
    fr = execution.fund(ctx, p["account_id"], "USD", int(p["need_c"]), "test", "capital:initial:F1", settle, immediate=bool(p.get("immediate")),
                        csa_netting=bool(p.get("csa")), services=ctx.services)
    return fr.to_dict()


def h_csa_contracts(ctx, p: dict) -> dict:
    """Test-only command: two open CTM contracts with Goldman on F1:PB_GS and their variation margin as the swap jobs leave it (L22: a
    CTM contract's GL 2330 / 1320 balance is its positive / negative VM balance): SWP-TEST01 received 200,000.00 (Dr 1010 / Cr 2330),
    SWP-TEST02 posted 50,000.00 (Dr 1320 / Cr 1010)."""
    from finsim.terminal.ledger import Entry, cr, dr
    for cid, vm in (("SWP-TEST01", 20000000), ("SWP-TEST02", -5000000)):
        ctx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                         " margin_mode, im_c, status, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cid, ctx.command_id, PB, "GS", "IRS", '{"pay_fixed": true}', 100000000, "USD", "2026-10-21", "2029-10-21", "CTM", 0, "OPEN", vm))
    ctx.post(Entry("SWAP_VM", "test: VM received under the CSA", [dr(PB, "1010", "USD", 20000000), cr(PB, "2330", "USD", 20000000, instrument_id="SWP-TEST01")],
                   "p2a:csa-vm-in", ref_type="otc", ref_id="SWP-TEST01"))
    ctx.post(Entry("SWAP_VM", "test: VM posted under the CSA", [dr(PB, "1320", "USD", 5000000, instrument_id="SWP-TEST02"), cr(PB, "1010", "USD", 5000000)],
                   "p2a:csa-vm-out", ref_type="otc", ref_id="SWP-TEST02"))
    return {"contracts": ["SWP-TEST01", "SWP-TEST02"]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-asof-")
        self.rt = build(self.tmp, at=BUILD_AT)
        self.rt.bus.register("test.asof_fund", h_asof_fund)
        self.rt.bus.register("test.csa_contracts", h_csa_contracts)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ------------------------------------------------------------------------------------------------------------
    def cmd(self, kind: str, payload: dict, rid: str, actor: str = "import"):
        return self.rt.bus.execute(Command(kind, payload, rid, actor))

    def ok(self, kind: str, payload: dict, rid: str, actor: str = "import") -> dict:
        r = self.cmd(kind, payload, rid, actor)
        self.assertTrue(r.ok, f"{kind} {rid} -> {r.http} {r.body}")
        return r.body

    def q(self, sql: str, params=()):
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def n(self, table: str) -> int:
        return self.q(f"SELECT COUNT(*) n FROM {table}")[0]["n"]

    def counts(self) -> tuple:
        return tuple(self.n(t) for t in ("orders", "fills", "lots", "settlements", "journal_entries", "transfers", "hedge_packages", "instruments"))

    def at_close(self, account: str, d: str) -> int:
        with self.rt.db.reader() as conn:
            return accounting.cash_view_at(conn, account, "USD", date.fromisoformat(d))["settled_c"]

    def cash_now(self, account: str) -> int:
        return self.q("SELECT COALESCE(SUM(balance_c), 0) s FROM gl_balances WHERE account_id=? AND gl='1010' AND currency='USD'", (account,))[0]["s"]

    def xfer(self, at: str, src: str, dst: str, amount_c: int, rid: str) -> None:
        self.rt.clock.set(at)
        self.ok("treasury.transfer", {"from_account": src, "to_account": dst, "currency": "USD", "amount_c": amount_c}, rid, "ui")

    def onboard_gs(self) -> None:
        self.ok("book.onboard_pb", {"lifetime_id": "F1", "broker": "GS"}, "p2a:onboard-gs-0001", "ui")


# ===== the Activity page's fill row in words (the integrator, 1.0.6: a V104R2-BW-01 remnant) ======================================================
class ActivityFillWords(Base):
    def test_an_as_of_fill_reads_in_words_on_the_activity_page(self):
        """The owner's as-of ORCL 140C fill on the Activity page: "F-... BUY 387 ORCL261120C00140000 @ 3.55 (as-of booking price; fill of O-...)" -
        the stored price basis 'as_of' and origin 'ORDER' in words, in the row and in its dialog; a short sale's side, a final settlement's origin
        and a stop's trigger print in words too (fill_words)."""
        from finsim.terminal.api_views import activity_rows, fill_words
        self.rt.clock.set(NOW)
        out = self.ok("trade.book_as_of", single("imp:p2a-actw-0001"), "imp:p2a-actw-0001")
        f, o = out["fill"], out["order"]
        rows = [r for r in activity_rows(self.rt, types={"orders"}, lifetime_ids=["F1"]) if r.get("type") == "fill" and r["ref_id"] == f["fill_id"]]
        self.assertEqual(1, len(rows))
        r = rows[0]
        self.assertEqual(f"{f['fill_id']} BUY 387 {ORCL_C} @ 3.55 (as-of booking price; fill of {o['order_id']})", r["summary"])
        self.assertEqual(("BUY", "as-of booking price", f"fill of {o['order_id']}"), (r["detail"]["side"], r["detail"]["price basis"], r["detail"]["origin"]))
        text = " ".join([r["summary"]] + [str(v) for v in r["detail"].values()])
        self.assertNotIn("as_of", text)
        self.assertNotRegex(text.replace(ORCL_C, ""), r"\b[A-Z]+_[A-Z_]+\b|\bORDER\b")
        self.assertEqual(("sell short", "last trade", "fill of O-000003"), fill_words({"side": "SELL_SHORT", "price_basis": "last", "origin": "ORDER", "order_id": "O-000003"}))
        self.assertEqual(("SELL", "settle price", "final settlement"), fill_words({"side": "SELL", "price_basis": "settle", "origin": "FUTURES_FINAL", "order_id": None}))
        self.assertEqual(("buy to cover", "stop trigger print", "exercise"), fill_words({"side": "BUY_TO_COVER", "price_basis": "stop_print", "origin": "EXERCISE", "order_id": None}))


# ===== (g) BTO 387 ORCL 140C as of 2026-10-22 =================================================================================================
class AsOfOptionBooking(Base):
    def test_bto_orcl_books_the_owner_terms_on_the_trade_date(self):
        self.rt.clock.set(NOW)
        self.assertIsNone(self.rt.instruments.get(ORCL_C))                               # the contract is not in the master yet
        out = self.ok("trade.book_as_of", single("imp:p2a-orcl-0001"), "imp:p2a-orcl-0001")
        o, f = out["order"], out["fill"]
        self.assertTrue(out["instrument_created"])
        inst = self.rt.instruments.get(ORCL_C)
        self.assertEqual(("OPTION", "ORCL", "2026-11-20", "C", "AMERICAN"), (inst.kind, inst.underlying_id, inst.expiry, inst.right, inst.style))
        # the contract's creation is in the command row (audit trail): the payload with its spec and chain row
        cmd = self.q("SELECT kind, actor, payload_json FROM commands WHERE request_id='imp:p2a-orcl-0001'")[0]
        self.assertEqual(("trade.book_as_of", "import"), (cmd["kind"], cmd["actor"]))
        self.assertIn('"chain_row":{"ask":"3.70"', cmd["payload_json"])
        # premium 387 x 3.55 x 100 = 137,385.00; fee 387 x 0.65 = 251.55; the payable 137,385.00 + 251.55 = 137,636.55
        self.assertEqual((387, "3.55", 13738500, 25155, D, "2026-10-23"), (f["qty"], f["price"], f["gross_c"], f["fee_c"], f["trade_date"], f["settle_date"]))
        # the order: FILLED, LIMIT 3.55 DAY, created at the execution time, booking AS_OF, entered now, the line key
        row = self.q("SELECT * FROM orders WHERE order_id=?", (o["order_id"],))[0]
        self.assertEqual(("FILLED", "LIMIT", "3.55", "DAY", 387, "3.55", EXEC_TS, "AS_OF", NOW_TS, "imp:p2a-orcl-0001", 25155),
                         (row["status"], row["order_type"], row["limit_px"], row["tif"], row["filled_qty"], row["avg_px"], row["created_ts"], row["booking"],
                          row["entered_ts"], row["import_key"], row["fees_c"]))
        ev = self.q("SELECT ts, from_status, to_status, note FROM order_events WHERE order_id=? ORDER BY id", (o["order_id"],))
        self.assertEqual([(EXEC_TS, None, "WORKING"), (EXEC_TS, "WORKING", "FILLED")], [(e["ts"], e["from_status"], e["to_status"]) for e in ev])
        self.assertTrue(ev[0]["note"].startswith(f"as-of; entered 2026-10-27 10:00 ET by the operator at the owner's instruction (batch {BATCH})"))
        # fills.ts = lots.open_ts = 2026-10-22 16:00 ET; the fill carries booking / entered_ts / import key; origin stays ORDER
        fr = self.q("SELECT * FROM fills WHERE fill_id=?", (f["fill_id"],))[0]
        lot = self.q("SELECT * FROM lots WHERE open_fill_id=?", (f["fill_id"],))[0]
        self.assertEqual((EXEC_TS, EXEC_TS, "AS_OF", NOW_TS, "imp:p2a-orcl-0001", "ORDER", "as_of"),
                         (fr["ts"], lot["open_ts"], fr["booking"], fr["entered_ts"], fr["import_key"], fr["origin"], fr["price_basis"]))
        # Dr 1700 137,385.00 / Dr 5000 251.55 / Cr 2100 137,636.55, business date = the trade date
        entry = self.q("SELECT business_date, kind FROM journal_entries WHERE entry_id=?", (fr["entry_id"],))[0]
        lines = sorted((r["gl"], r["amount_c"]) for r in self.q("SELECT gl, amount_c FROM journal_lines WHERE entry_id=?", (fr["entry_id"],)))
        self.assertEqual((D, "FILL"), (entry["business_date"], entry["kind"]))
        self.assertEqual([("1700", 13738500), ("2100", -13763655), ("5000", 25155)], lines)
        # the settlement is due T+1 from the trade date: 2026-10-23
        st = self.q("SELECT due_date, amount_c, gl, status FROM settlements WHERE fill_id=?", (f["fill_id"],))[0]
        self.assertEqual(("2026-10-23", -13763655, "2100", "PENDING"), (st["due_date"], st["amount_c"], st["gl"], st["status"]))
        # funded as of the trade date (C20): US Exchange held 0.00 at the close of 10-22 -> ONE sweep of 137,636.55 dated 2026-10-22
        self.assertEqual(("sweep", 0, 13763655, D), (out["funding"]["source"], out["funding"]["own_c"], out["funding"]["sweep_c"], out["funding"]["asof"]))
        sw = self.q("SELECT business_date, amount_c, linked_type, linked_id, entry_id FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual([(D, 13763655, "fill", f["fill_id"])], [(s["business_date"], s["amount_c"], s["linked_type"], s["linked_id"]) for s in sw])
        self.assertEqual(D, self.q("SELECT business_date FROM journal_entries WHERE entry_id=?", (sw[0]["entry_id"],))[0]["business_date"])
        # Treasury at the close of 10-22: 25,000,000.00 - 137,636.55 = 24,862,363.45 (the same now)
        self.assertEqual((2486236345, 2486236345), (self.at_close(TRE, D), self.cash_now(TRE)))
        # the source label (D1), on the order and through Completed Trades' executions route
        label = (f"As-of booking - simulated fill at the 2026-10-22 closing print, entered 2026-10-27 10:00 ET by the operator at the owner's instruction"
                 f" (batch {BATCH})")
        self.assertEqual(("as_of", label, True, NOW_TS, BATCH, "AS-OF"), tuple(out["source"][k] for k in ("source", "label", "asof", "entered_ts", "batch_id", "badge")))
        st_, body, _h = self.rt.router.handle("GET", "/api/blotter/executions?book=F", None, None, "127.0.0.1", None)
        self.assertEqual(200, st_)
        ex = [r for r in body["rows"] if r["execution_id"] == f["fill_id"]][0]
        self.assertEqual((o["order_id"], "Option", "BTO", 387, "3.55", "137385.00", "251.55", label, "AS-OF", BATCH, "Settling"),
                         (ex["parent_id"], ex["asset_class"], ex["side_words"], ex["qty"], ex["price"], ex["gross"], ex["fee"], ex["source_label"], ex["badge"],
                          ex["batch_id"], ex["status_label"]))
        self.assertEqual("2026-10-27 10:00:00 ET", ex["entered_ny"])
        rep = self.rt.accounting.integrity("full")
        self.assertEqual([], [p.to_dict() for p in rep.problems if p.to_dict().get("severity") == "ERROR"])

    def test_replay_posts_nothing_and_a_reused_key_is_refused(self):
        self.rt.clock.set(NOW)
        first = self.cmd("trade.book_as_of", single("imp:p2a-orcl-0002"), "imp:p2a-orcl-0002")
        self.assertTrue(first.ok, first.body)
        before = self.counts()
        again = self.cmd("trade.book_as_of", single("imp:p2a-orcl-0002"), "imp:p2a-orcl-0002")
        self.assertEqual((200, True), (again.http, again.replayed))
        self.assertEqual(before, self.counts())                                          # a replay posts nothing
        other = self.cmd("trade.book_as_of", single("imp:p2a-orcl-0002", price="3.56"), "imp:p2a-orcl-0002")
        self.assertEqual((409, "REQUEST_ID_REUSED"), (other.http, other.code))           # a different payload with the same key
        dup = self.cmd("trade.book_as_of", single("imp:p2a-orcl-0002"), "imp:p2a-orcl-0002-again")
        self.assertEqual((409, "DUPLICATE_IMPORT"), (dup.http, dup.code))                # the same line under another command id
        self.assertEqual(before, self.counts())

    def test_the_order_ticket_cannot_book_as_of(self):
        self.rt.clock.set(NOW)
        before = self.counts()
        r = self.cmd("trade.book_as_of", single("imp:p2a-orcl-0003"), "imp:p2a-orcl-0003", actor="ui")
        self.assertEqual((403, "AS_OF_FORBIDDEN"), (r.http, r.code))
        r = self.cmd("trade.book_as_of_package", package_payload(), "asofpkg:p2a-ui-0001", actor="ui")
        self.assertEqual((403, "AS_OF_FORBIDDEN"), (r.http, r.code))
        self.assertEqual(before, self.counts())
        self.assertIsNone(self.rt.instruments.get(ORCL_C))

    def test_refusals_write_nothing(self):
        self.rt.clock.set(NOW)
        before = self.counts()
        bad = {
            "prose in the payload": (dict(single("imp:p2a-bad-0001"), note="the owner's call"), 400, "ASOF_PAYLOAD"),
            "not LIMIT / DAY": (dict(single("imp:p2a-bad-0002"), order_type="MARKET"), 400, "ASOF_PAYLOAD"),
            "unknown fee rule": (dict(single("imp:p2a-bad-0003"), fee_rule="HALF"), 400, "ASOF_PAYLOAD"),
            "execution on another day": (single("imp:p2a-bad-0004", exec_ts="2026-10-21T16:00:00-04:00"), 400, "ASOF_DATES"),
            "execution in the future": (single("imp:p2a-bad-0005", trade_date="2026-10-28", exec_ts="2026-10-28T16:00:00-04:00"), 400, "ASOF_DATES"),
            "a weekend trade date": (single("imp:p2a-bad-0006", trade_date="2026-10-24", exec_ts="2026-10-24T16:00:00-04:00"), 400, "ASOF_DATES"),
            "a price that cannot be stored exactly": (single("imp:p2a-bad-0007", price="3.550000001"), 409, "ASOF_TERMS_MISMATCH"),
            "a spec that is another contract": (single("imp:p2a-bad-0008", inst_spec=spec(ORCL_C, "ORCL", "145", "C")), 400, "ASOF_SPEC"),
            "no chain row for a new contract": (single("imp:p2a-bad-0009", inst_spec=spec(ORCL_C, "ORCL", "140", "C", chain_row=False)), 400, "ASOF_CHAIN_ROW"),
            "an as-of short sale": (single("imp:p2a-bad-0010", iid="AAPL", side="SELL_SHORT", qty=100, price="300.00", inst_spec={}), 400, "ASOF_SIDE"),
            "a future": (single("imp:p2a-bad-0011", iid="GCZ26", side="BUY", qty=1, price="4100.0", inst_spec={}), 400, "ASOF_KIND"),
        }
        for what, (payload, http, code) in bad.items():
            r = self.cmd("trade.book_as_of", payload, payload["as_of"]["line_key"])
            self.assertEqual((http, code), (r.http, r.code), f"{what}: {r.body}")
        self.assertEqual(before, self.counts())
        self.assertIsNone(self.rt.instruments.get(ORCL_C))                               # a refused booking leaves no contract behind


# ===== (h) the QQQ put spread as ONE package command ===============================================================================================
class AsOfPackage(Base):
    def test_qqq_bear_put_spread_is_one_command_with_one_package_row(self):
        self.rt.clock.set(NOW)
        quote(self.rt, "QQQ", "690.00")                                                  # the underlying is marked: the margin is computed now
        out = self.ok("trade.book_as_of_package", package_payload(), "asofpkg:p2a-0000000000000001")
        pk = self.q("SELECT * FROM hedge_packages")
        self.assertEqual([("BEAR_PUT_SPREAD", "AON", "FILLED", EXEC_TS)], [(p["strategy_key"], p["fill_mode"], p["status"], p["created_ts"]) for p in pk])
        legs = self.q("SELECT o.order_id, o.request_id, o.side, o.status, o.package_id, o.import_key, o.booking, f.qty, f.price, f.gross_c, f.fee_c, f.import_key fk,"
                      " f.trade_date, f.settle_date, f.ts FROM orders o JOIN fills f ON f.order_id=o.order_id ORDER BY o.order_id")
        # long before short: BTO 150 700P @ 17.64 = 150 x 17.64 x 100 = 264,600.00; STO 150 675P @ 10.25 = 153,750.00; fees 150 x 0.65 = 97.50 each
        self.assertEqual([("asofpkg:p2a-0000000000000001:L1", "BTO", "FILLED", "imp:p2a-leg700", "AS_OF", 150, "17.64", 26460000, 9750, "imp:p2a-leg700"),
                          ("asofpkg:p2a-0000000000000001:L2", "STO", "FILLED", "imp:p2a-leg675", "AS_OF", 150, "10.25", 15375000, 9750, "imp:p2a-leg675")],
                         [(r["request_id"], r["side"], r["status"], r["import_key"], r["booking"], r["qty"], r["price"], r["gross_c"], r["fee_c"], r["fk"]) for r in legs])
        self.assertEqual({pk[0]["package_id"]}, {r["package_id"] for r in legs})
        self.assertEqual({(D, "2026-10-23", EXEC_TS)}, {(r["trade_date"], r["settle_date"], r["ts"]) for r in legs})
        # the payable 264,600.00 + 97.50 = 264,697.50 and the receivable 153,750.00 - 97.50 = 153,652.50, both due 2026-10-23
        st = self.q("SELECT s.amount_c, s.due_date FROM settlements s JOIN fills f ON f.fill_id=s.fill_id ORDER BY f.fill_id")
        self.assertEqual([(-26469750, "2026-10-23"), (15365250, "2026-10-23")], [(s["amount_c"], s["due_date"]) for s in st])
        # ONE option-margin recomputation: the 675P is fully covered by the 700P (same expiry, higher strike) -> reserved 0.00
        self.assertEqual(0, out["margin"]["required_c"])
        ms = self.q("SELECT options_required_c, options_detail_json FROM margin_state WHERE account_id=?", (US,))[0]
        self.assertEqual(0, ms["options_required_c"])
        self.assertIn('"rule":"spread_covered"', ms["options_detail_json"])
        self.assertEqual(1, len([c for c in self.q("SELECT id FROM commands WHERE kind='trade.book_as_of_package'")]))
        # the long leg is funded as of 10-22 (264,697.50 swept, dated 10-22); the short leg needs no cash
        self.assertEqual([(D, 26469750)], [(s["business_date"], s["amount_c"]) for s in self.q("SELECT business_date, amount_c FROM transfers WHERE kind='SWEEP_IN'")])
        rep = self.rt.accounting.integrity("full")
        self.assertEqual([], [p.to_dict() for p in rep.problems if p.to_dict().get("severity") == "ERROR"])

    def test_without_an_underlying_mark_the_margin_waits_for_the_end_of_day_job(self):
        # the owner's database holds no QQQ quote and the import runs with the server stopped: nothing is assumed about QQQ
        self.rt.clock.set(NOW)
        out = self.ok("trade.book_as_of_package", package_payload("imp:p2a-leg700b", "imp:p2a-leg675b"), "asofpkg:p2a-0000000000000002")
        self.assertEqual((None, True, "MARK_UNAVAILABLE"), (out["margin"]["required_c"], out["margin"]["pending"], out["margin"]["code"]))
        self.assertEqual(2, self.n("fills"))
        self.assertEqual([], self.q("SELECT * FROM margin_state WHERE account_id=?", (US,)))


# ===== (i) as-of funding on business-date balances (C20) ==========================================================================================
class AsOfFunding(Base):
    """A PB account with no cash of its own and PB financing off (Goldman, onboarded 10-21); the need is measured as of D = 2026-10-22."""

    def setUp(self):
        super().setUp()
        self.onboard_gs()

    def test_the_business_date_figure_binds(self):
        # Treasury 25,000,000.00 - 24,000,000.00 moved out on D = 1,000,000.00 at the close of D; a deposit dated D+1 of 4,000,000.00
        self.xfer("2026-10-22T10:00:00-04:00", TRE, "F1:DEBT", 2400000000, "p2a:xfer-d-0001")
        self.rt.clock.set("2026-10-23T10:00:00-04:00")
        self.ok("treasury.deposit", {"lifetime_id": "F1", "amount_c": 400000000, "currency": "USD"}, "p2a:dep-d1-0001", "ui")
        self.rt.clock.set(NOW)
        self.assertEqual((100000000, 500000000), (self.at_close(TRE, D), self.cash_now(TRE)))  # 1,000,000.00 at the close of D; 5,000,000.00 now
        r = self.cmd("test.asof_fund", {"account_id": PB, "need_c": 150000000, "asof": D}, "p2a:fund-asof-0001")
        # an as-of need of 1,500,000.00: Treasury available at the close of D is 1,000,000.00 -> refused, nothing moved
        self.assertEqual((409, "INSUFFICIENT_FUNDS", 100000000), (r.http, r.code, r.body["error"]["details"]["treasury_available_c"]))
        self.assertIn("as of 2026-10-22", r.message)
        self.assertEqual(0, self.n("transfers WHERE kind='SWEEP_IN'"))
        # the same need without an as-of date sweeps 1,500,000.00 from the current balance (5,000,000.00 -> 3,500,000.00)
        out = self.ok("test.asof_fund", {"account_id": PB, "need_c": 150000000}, "p2a:fund-live-0001")
        self.assertEqual(("sweep", 150000000, None), (out["source"], out["sweep_c"], out["asof"]))
        self.assertEqual(350000000, self.cash_now(TRE))

    def test_the_sweep_is_dated_d(self):
        # Treasury 25,000,000.00 at the close of D; a transfer dated D+1 of 23,000,000.00 leaves 2,000,000.00 now
        self.xfer("2026-10-23T10:00:00-04:00", TRE, "F1:DEBT", 2300000000, "p2a:xfer-d1-0002")
        self.rt.clock.set(NOW)
        out = self.ok("test.asof_fund", {"account_id": PB, "need_c": 170191850, "asof": D}, "p2a:fund-asof-0002")
        # need 1,701,918.50, own 0.00 -> one sweep of 1,701,918.50 with business date D (sweep_in checks the business-date figure)
        self.assertEqual(("sweep", 0, 170191850, D), (out["source"], out["own_c"], out["sweep_c"], out["asof"]))
        sw = self.q("SELECT business_date, amount_c, entry_id FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual([(D, 170191850)], [(s["business_date"], s["amount_c"]) for s in sw])
        self.assertEqual(D, self.q("SELECT business_date FROM journal_entries WHERE entry_id=?", (sw[0]["entry_id"],))[0]["business_date"])
        # Treasury closes D at 25,000,000.00 - 1,701,918.50 = 23,298,081.50, D+1 at 23,298,081.50 - 23,000,000.00 = 298,081.50, holds 298,081.50 now
        self.assertEqual((2329808150, 29808150, 29808150), (self.at_close(TRE, D), self.at_close(TRE, "2026-10-23"), self.cash_now(TRE)))
        self.assertEqual(170191850, self.at_close(PB, D))

    def test_the_current_balance_binds(self):
        # a transfer dated D+1 of 23,532,419.20 leaves 25,000,000.00 - 23,532,419.20 = 1,467,580.80 now
        self.xfer("2026-10-23T10:00:00-04:00", TRE, "F1:DEBT", 2353241920, "p2a:xfer-d1-0003")
        self.rt.clock.set(NOW)
        before = self.counts()
        r = self.cmd("test.asof_fund", {"account_id": PB, "need_c": 170191850, "asof": D}, "p2a:fund-asof-0003")
        # 1,467,580.80 - 1,701,918.50 = -234,337.70 now: refused before anything is written, naming the amount to move first
        self.assertEqual((409, "ASOF_CURRENT_FUNDS"), (r.http, r.code))
        det = r.body["error"]["details"]
        self.assertEqual((146758080, 170191850, 23433770), (det["treasury_now_c"], det["sweep_c"], det["move_first_c"]))
        self.assertIn("would leave it at -234,337.70", r.message)
        self.assertEqual(before, self.counts())
        # an as-of need of 1,000,000.00 sweeps 1,000,000.00 dated D and leaves 1,467,580.80 - 1,000,000.00 = 467,580.80 now
        out = self.ok("test.asof_fund", {"account_id": PB, "need_c": 100000000, "asof": D}, "p2a:fund-asof-0004")
        self.assertEqual((100000000, D), (out["sweep_c"], out["asof"]))
        self.assertEqual([(D, 100000000)], [(s["business_date"], s["amount_c"]) for s in self.q("SELECT business_date, amount_c FROM transfers WHERE kind='SWEEP_IN'")])
        self.assertEqual(46758080, self.cash_now(TRE))


# ===== (j) CSA netting on the funding side (C21) =====================================================================================================
class CsaNetting(Base):
    def test_a_csa_payment_may_use_the_counterparty_collateral_and_nothing_else_may(self):
        self.onboard_gs()
        self.rt.clock.set(NOW)
        self.assertIsNotNone(getattr(self.rt, "collateral", None), "the collateral module (P6) is bound in this build")
        # settled 150,000.00 moved in + 200,000.00 VM received (GL 2330) - 50,000.00 VM posted (GL 1320) = 300,000.00
        self.ok("treasury.transfer", {"from_account": TRE, "to_account": PB, "currency": "USD", "amount_c": 15000000}, "p2a:csa-xfer-0001", "ui")
        self.ok("test.csa_contracts", {}, "p2a:csa-contracts-0001")
        self.assertEqual(30000000, self.cash_now(PB))
        with self.rt.db.reader() as conn:
            restricted = self.rt.collateral.restricted_c(conn, PB, "USD")
        self.assertEqual(15000000, restricted)                                           # max(0, 200,000.00 - 50,000.00) + 0 lending = 150,000.00
        # a VM payment of 250,000.00 to the CSA counterparty may use its collateral: own 300,000.00 covers it, no sweep
        out = self.ok("test.asof_fund", {"account_id": PB, "need_c": 25000000, "immediate": True, "csa": True}, "p2a:csa-fund-0001")
        self.assertEqual(("own", 30000000, 0), (out["source"], out["own_c"], out["sweep_c"]))
        # any other immediate need of 250,000.00 sees own 300,000.00 - 150,000.00 = 150,000.00 and sweeps 100,000.00 from Treasury
        out = self.ok("test.asof_fund", {"account_id": PB, "need_c": 25000000, "immediate": True}, "p2a:csa-fund-0002")
        self.assertEqual(("sweep", 15000000, 10000000), (out["source"], out["own_c"], out["sweep_c"]))


# ===== (k) a historical-model line ================================================================================================================
class HistoricalModel(Base):
    def test_a_historical_model_fill_is_labelled_as_such_and_never_as_of(self):
        self.rt.clock.set(NOW)
        payload = single("imp:p2a-hm-0001", iid="AAPL", side="BUY", qty=100, price="300.00", inst_spec={}, exec_ts="2026-10-22T15:30:00-04:00",
                         historical_model=True)
        payload["instrument_spec"] = None
        out = self.ok("trade.book_as_of", payload, "imp:p2a-hm-0001")
        # BUY 100 AAPL @ 300.00 = 30,000.00; fee 100 x 0.005 = 0.50 -> the 1.00 minimum
        self.assertEqual((3000000, 100, "HISTORICAL_MODEL"), (out["fill"]["gross_c"], out["fill"]["fee_c"], out["booking"]))
        self.assertEqual(("historical_model", HISTORICAL_MODEL_LABEL, False, "MODEL"),
                         tuple(out["source"][k] for k in ("source", "label", "asof", "badge")))
        st_, body, _h = self.rt.router.handle("GET", "/api/blotter/executions?book=F", None, None, "127.0.0.1", None)
        rows = body["rows"]
        self.assertEqual([("historical_model", "Historical model (not an independent execution)", "MODEL", False)],
                         [(r["source"], r["source_label"], r["badge"], r["asof"]) for r in rows])
        self.assertNotIn("As-of booking", repr(rows))
        o = self.q("SELECT booking FROM orders")[0]
        self.assertEqual("HISTORICAL_MODEL", o["booking"])
        st_, body, _h = self.rt.router.handle("GET", "/api/blotter/executions?book=F&source=as_of", None, None, "127.0.0.1", None)
        self.assertEqual(0, body["total"])


if __name__ == "__main__":
    unittest.main()
