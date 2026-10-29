"""Single-name CDS wiring into the platform (CDS_SINGLE_NAME_PLAN rev 2, package B2, hooks H2-1..H2-12): the reference pin, the asset class,
the Risk bucket / label / exposure / single-name block, the Portfolio footer, the link words and the Completed Trades rows.

These tests need the hook edits (``hooks_b1.py``: the ``CDS`` kind in ``db.OTC_KINDS`` / the otc_contracts CHECK, ``swaps._module_for`` and the
bound modules in ``server.OPTIONAL_MODULES``; ``hooks_b2.py``: instruments / accounting / risk / api_views / blotters).  On the plain repo tree
they SKIP with that reason; they run on a rehearsal copy (``rehearse.py``).  A real offline runtime (``server.build_runtime``, FakeProvider,
FakeClock, a temp home); the CDS contract is written in the exact shape ``swaps_cds_names.book`` stores (terms, the SWAP instrument and its
spec, the OTC_BOOKED event), so the platform side is tested on its own.  Nothing touches the network or ~/.finsim.

Hand-worked numbers (the plan's Appendix C deal): buy protection on ORCL USD 5,000,000.00, 5Y to 2031-12-20, dealt 67.47 bp, coupon 100 bp;
upfront RECEIVED 74,282.20 -> the contract's cost basis -74,282.20 (GL 2345); its mark at mid mark0 -78,913.97 -> unrealised
-78,913.97 - (-74,282.20) = -4,631.77 (the cost of dealing).  A protection buyer is short credit: exposure -5,000,000.00.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import types
import unittest
from decimal import Decimal

from tests import terminal_helpers as th
from finsim.terminal import accounting as _acc, api_views as _av, blotters as _bl, config, db as _db, instruments as _ins, risk as _risk, \
    server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.products import swaps as _swaps
from finsim.terminal.quotes.providers import FakeProvider

D = Decimal
AT = "2026-10-29T14:00:00-04:00"
BUCKET = "Credit derivatives (single-name CDS notional)"
HOOKS = ("CDS" in getattr(_db, "OTC_KINDS", ()) and getattr(_swaps, "SINGLE_CDS_KIND", None) == "CDS"
         and "credit_entities" in getattr(_server, "OPTIONAL_MODULES", {}) and BUCKET in getattr(_risk, "BUCKET_ORDER", [])
         and "CDS" in getattr(_acc, "OTC_ASSET_CLASS", {}) and "CDS_CREDIT_EVENT" in getattr(_bl, "OTC_EVENTS", {}))
SKIP_WHY = ("the single-name CDS hook edits are not applied (hooks_b1.py: db.OTC_KINDS, swaps._module_for, server.OPTIONAL_MODULES; hooks_b2.py:"
            " instruments, accounting, risk, api_views, blotters): the wiring tests run on a rehearsal copy")
PB = "F1:PB_GS"
CID = "CDS-000001"


def build(tmp: str):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=th._Log(), books=None,
                                 fetch_threads=0)


def _h_book_cds(ctx, payload):
    """Writes a single-name CDS exactly as ``swaps_cds_names.book`` stores it (the rows the platform reads), without its postings."""
    cid, aid, side = payload["contract_id"], payload["account_id"], payload["side"]
    receiver = side == "SELL_PROTECTION"
    terms = {"kind": "CDS", "cik": 1341439, "ticker": "ORCL", "entity_name": "Oracle Corp", "underlying_id": "ORCL", "seniority": "SENIOR_UNSECURED",
             "doc_clause": "XR14", "tenor_years": 5, "side": side, "receiver": receiver, "notional_c": 500000000, "coupon_bp": "100", "recovery": "0.40",
             "trade_date": "2026-10-29", "maturity_date": "2031-12-20", "roll_date": "2026-09-21", "fixed_basis": "ACT/360", "level_kind": "MODEL",
             "basis_bp": "0.0000", "mid_spread_bp": "65.47", "tier": "A_OR_BETTER", "deal_spread_bp": "67.47" if not receiver else "63.47",
             "quoted": {"rfq_id": "RFQ-000001", "dealt_spread_bp": "67.47" if not receiver else "63.47", "coupon_bp": "100", "maturity_date": "2031-12-20",
                        "tier": "A_OR_BETTER"},
             "upfront_c": -7428220 if not receiver else 8355333, "mark0_c": -7891397 if not receiver else 7891397, "margin_mode": "CTM"}
    master = ctx.services.instruments
    insert = getattr(master, "insert", None) or getattr(master, "_insert")
    insert(ctx.conn, ctx.ts, instrument_id=cid, kind="SWAP", symbol=cid, name=f"Single-name CDS: ORCL 5Y ({side})", currency="USD",
           multiplier="1", tick="0.01", lot=1, price_scale="1", session_group=_swaps.SESSION_GROUP, settle_days=0, account_kinds=["PB"],
           underlying_id="ORCL", expiry="2031-12-20",
           spec={"contract_id": cid, "otc_kind": "CDS", "broker": "GS", "cik": 1341439, "ticker": "ORCL", "entity_name": "Oracle Corp",
                 "tenor_years": 5, "side": side, "receiver": receiver, "maturity_date": "2031-12-20", "coupon_bp": "100"},
           support="COMPUTED", status="ACTIVE")
    ctx.services.instruments.refresh(cid, ctx.conn)
    ctx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                     " margin_mode, im_c, status, mark_c, mark_ts, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,0)",
                     (cid, ctx.command_id, aid, "GS", "CDS", json.dumps(terms), 500000000, "USD", "2026-10-29", "2031-12-20", "CTM", 0, terms["mark0_c"], ctx.ts))
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind="CDS", broker="GS", side=side, notional_c=500000000, cik=1341439,
              ticker="ORCL", deal_spread_bp=terms["deal_spread_bp"], label="MODEL")
    return {"contract_id": cid}


def _h_cds_credit_event(ctx, payload):
    """The CDS_CREDIT_EVENT event as ``swaps_cds_names.h_credit_event`` records it (the payload keys the blotter reads)."""
    ctx.event("CDS_CREDIT_EVENT", account_id=payload["account_id"], instrument_id=payload["contract_id"], ref_type="otc", ref_id=payload["contract_id"],
              **payload["record"])
    return {}


@unittest.skipUnless(HOOKS, SKIP_WHY)
class CdsWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-cds-wiring-")
        self.rt = build(self.tmp)
        rt = self.rt
        rt.bus.register("test.post", th._h_test_post)
        rt.bus.register("test.cds_book", _h_book_cds)
        rt.bus.register("test.cds_event", _h_cds_credit_event)
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO accounts(account_id, book_code, lifetime_id, role, name, kind, broker, opened_ts, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
                            (PB, "F", "F1", "PB_GS", "Goldman (PB)", "PB", "GS", rt.clock.epoch(), 90))

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def book(self, side: str = "BUY_PROTECTION", cid: str = CID):
        r = th.execute(self.rt, "test.cds_book", {"contract_id": cid, "account_id": PB, "side": side})
        self.assertTrue(r.ok, r.body)
        # the upfront received 74,282.20 is the contract's cost basis (GL 2345, credit) - the plan's 2.3 step 2
        r = th.execute(self.rt, "test.post", {"kind": "CDS_UPFRONT", "memo": "upfront received", "idem_key": f"otc:{cid}:upfront",
                                              "lines": [[PB, "1010", "USD", 7428220], [PB, "2345", "USD", -7428220, cid]]})
        self.assertTrue(r.ok, r.body)

    def get(self, path, query=None):
        status, body, _h = self.rt.router.handle("GET", path, query, None, "127.0.0.1", None)
        self.assertEqual(200, status, f"GET {path} -> {status} {body}")
        from finsim.terminal.api import encode
        return json.loads(json.dumps(encode(body), default=str))

    def test_modules_are_bound(self):
        self.assertEqual("bound", self.rt.modules.get("credit_entities"))
        # the entities route of the bound module answers (offline: nothing loaded, said in words)
        out = self.get("/api/otc/cds/entities", {"q": "orcl"})
        self.assertEqual((0, "offline"), (out["total"], out["universe"]["state"]))

    def test_reference_pin(self):
        self.book()
        with self.rt.db.reader() as conn:
            self.assertEqual([PB], _ins.otc_reference_ids(conn).get("ORCL"))
            self.assertEqual("2026-10-29", _ins.otc_reference_since(conn)["ORCL"].isoformat())

    def test_portfolio_row_asset_class_and_footer(self):
        self.book()
        out = self.get("/api/portfolio", {"book": "F"})
        rows = {p["instrument_id"]: p for p in out["positions"]}
        row = rows[CID]
        # a protection buyer: qty -5,000,000 (short credit), asset class OTC CDS, the readable label, the PB as counterparty
        self.assertEqual((-5000000, "OTC CDS", "ORCL 5Y CDS buy protection", "Goldman Sachs (bilateral OTC, ISDA / CSA)"),
                         (row["qty"], row["asset_class"], row["extra"]["label"], row["counterparty"]))
        # cost -74,282.20 (upfront received), MV -78,913.97 (the mark at the last VM), unrealised -4,631.77
        self.assertEqual((D("-74282.20"), D("-78913.97"), D("-4631.77")), (D(row["cost"]), D(row["mv_base"]), D(row["unrealised_base"])))
        f = out["footer"]
        self.assertEqual((D("0.00"), D("5000000.00")), (D(f["otc_exposure"]["cds_long_credit"]), D(f["otc_exposure"]["cds_short_credit"])))
        self.assertEqual((D("0.00"), D("0.00")), (D(f["otc_exposure"]["cdx_long_credit"]), D(f["otc_exposure"]["cdx_short_credit"])))
        self.assertEqual(D("0.00"), D(f["otc_exposure"]["other_notional"]))
        self.assertTrue({"cds", "jtd", "cdx", "trs", "irs"} <= set(f["definitions"]))
        self.assertIn("OTC CDS", {c["klass"] for c in out["by_asset_class"]})

    def test_a_seller_is_long_credit(self):
        self.book("SELL_PROTECTION")
        out = self.get("/api/portfolio", {"book": "F"})
        row = {p["instrument_id"]: p for p in out["positions"]}[CID]
        self.assertEqual((5000000, "ORCL 5Y CDS sell protection"), (row["qty"], row["extra"]["label"]))
        self.assertEqual(D("5000000.00"), D(out["footer"]["otc_exposure"]["cds_long_credit"]))

    def test_risk_bucket_sign_label_and_block(self):
        self.book()
        rep = _risk.report(self.rt, "F1:ALL")
        r = next(p for p in rep.positions if p.instrument_id == CID)
        # outside the headline, signed like a credit exposure (bought protection = short credit), its own bucket and words
        self.assertEqual((BUCKET, False, -500000000, "ORCL 5Y CDS buy protection"), (r.bucket, r.headline, r.exposure_c, r.label))
        self.assertIn("single-name credit notional", r.exposure_basis)
        ex = {e["bucket"]: e for e in rep.exposures}
        self.assertEqual((500000000, False), (ex[BUCKET]["short_c"], ex[BUCKET]["headline"]))
        self.assertTrue({"cds", "jtd"} <= set(rep.definitions))
        self.assertEqual("single-name CDS", _risk.OTC_KIND_LABEL["CDS"])
        # the CDX bucket and block stay as they were: no CDX contract here
        self.assertNotIn("Credit derivatives (CDX notional)", ex)
        self.assertIsNone(rep.credit_derivatives)
        # the single-name block is the engine's (bound): present for a CDS row (its content is the engine's tests')
        self.assertIsNotNone(rep.single_name_cds)

    def test_block_warnings_join_the_page(self):
        self.book()
        seen = {}

        def fake_block(services, rows, base_c):
            seen["rows"] = [r.instrument_id for r in rows]
            return {"bucket": BUCKET, "rows": [], "warnings": [{"code": "CDS_JTD_CONCENTRATION", "message": "Oracle Corp jump to default would lose USD 3,078,913.97"
                                                                " (12.31 % of NAV, above 10 %)", "account_id": None, "instrument_id": None, "value": "0.1231",
                                                                "threshold": "0.10"}]}
        real = self.rt.swaps_cds_names
        self.rt.swaps_cds_names = types.SimpleNamespace(**{k: getattr(real, k) for k in dir(real) if not k.startswith("__")})
        self.rt.swaps_cds_names.risk_block = fake_block
        try:
            rep = _risk.report(self.rt, "F1:ALL")
        finally:
            self.rt.swaps_cds_names = real
        # only the CDS rows are handed to the block; its warning is on the page (WARN, with its value and threshold)
        self.assertEqual([CID], seen["rows"])
        w = [x for x in rep.warnings if x["code"] == "CDS_JTD_CONCENTRATION"]
        self.assertEqual([("WARN", "0.1231", "0.10")], [(x["severity"], x["value"], x["threshold"]) for x in w])
        self.assertEqual("CDS JTD concentration", _av.code_words("CDS_JTD_CONCENTRATION"))
        d = rep.to_dict()
        self.assertEqual(BUCKET, d["single_name_cds"]["bucket"])

    def test_block_failure_is_named(self):
        self.book()
        real = self.rt.swaps_cds_names

        def broken(services, rows, base_c):
            raise RuntimeError("no model")
        self.rt.swaps_cds_names = types.SimpleNamespace(**{k: getattr(real, k) for k in dir(real) if not k.startswith("__")})
        self.rt.swaps_cds_names.risk_block = broken
        try:
            rep = _risk.report(self.rt, "F1:ALL")
        finally:
            self.rt.swaps_cds_names = real
        self.assertEqual("RuntimeError: no model", rep.single_name_cds["error"])

    def test_no_cds_no_block(self):
        rep = _risk.report(self.rt, "F1:ALL")
        self.assertIsNone(rep.single_name_cds)

    def test_completed_trades_booking_and_credit_event_rows(self):
        self.book()
        rec = {"reference_entity": "Oracle Corp", "cik": 1341439, "ticker": "ORCL", "event_type": "BANKRUPTCY", "event_words": "bankruptcy",
               "event_date": "2026-11-02", "recovery_pct": "40", "defaulted_c": 500000000, "protection_c": 300000000, "accrued_c": 597222, "late_c": 13889,
               "net_c": 299402778, "settle_date": "2026-11-03", "settle_id": "STL-000009", "notional_before_c": 500000000, "notional_after_c": 0,
               "label": "simulated credit event - recovery entered by the owner; no ISDA Determinations Committee ruling or auction in the simulator",
               "contract_closed": True}
        self.assertTrue(th.execute(self.rt, "test.cds_event", {"account_id": PB, "contract_id": CID, "record": rec}).ok)
        with self.rt.db.reader() as conn:
            rows = [r for r in _bl.execution_rows(self.rt, conn, account_id=PB) if r["instrument_id"] == CID]
        book = next(r for r in rows if r["execution_id"] == CID)
        self.assertEqual(("ORCL 5Y CDS", "BUY PROTECTION", "67.47", "spread bp", 500000000),
                         (book["symbol"], book["side_words"], book["price"], book["price_label"], book["notional_c"]))
        ev = next(r for r in rows if r["status"] == "credit event")
        # one row: CREDIT EVENT (BUY PROTECTION), net 2,994,027.78 received = protection 3,000,000.00 - accrued 5,972.22, recovery 40 %
        self.assertEqual(("CREDIT EVENT", "CREDIT EVENT (BUY PROTECTION)", 299402778, "40", "2026-11-02", "2026-11-03"),
                         (ev["side"], ev["side_words"], ev["gross_c"], ev["price"], ev["trade_date"], ev["settle_date"]))
        self.assertEqual("Oracle Corp at 40 % recovery: defaulted 5000000.00 / protection 3000000.00 / accrued premium 5972.22; notional 5000000.00 -> 0.00",
                         ev["note"])
        self.assertEqual(2, len(rows))

    def test_link_words(self):
        self.assertEqual("CDS coupon CDS-000001 2026-12-21", _av.link_words("otc", "cdscpn:CDS-000001:2026-12-21"))
        self.assertEqual("CDS credit event", _av.code_words("CDS_CREDIT_EVENT"))
        # the CDX words are unchanged
        self.assertEqual("CDX coupon CDX-000001 2026-12-21", _av.link_words("otc", "cdxcpn:CDX-000001:2026-12-21"))
        self.assertEqual("CDX credit event", _av.code_words("CDX_CREDIT_EVENT"))

    def test_cdx_rows_unchanged_beside_a_cds(self):
        # a CDX contract beside the CDS: its own bucket, label and footer keys, never the single-name ones
        self.book()
        with self.rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                            " margin_mode, im_c, status, mark_c, mark_ts, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,0)",
                            ("CDX-000009", 1, PB, "GS", "CDX", json.dumps({"index": "IG", "side": "BUY_PROTECTION", "receiver": False}), 300000000, "USD",
                             "2026-10-29", "2031-12-20", "CTM", 0, -4917613, self.rt.clock.epoch()))
        out = self.get("/api/portfolio", {"book": "F"})
        f = out["footer"]["otc_exposure"]
        self.assertEqual((D("3000000.00"), D("5000000.00")), (D(f["cdx_short_credit"]), D(f["cds_short_credit"])))
        rep = _risk.report(self.rt, "F1:ALL")
        buckets = {p.instrument_id: p.bucket for p in rep.positions}
        self.assertEqual(("Credit derivatives (CDX notional)", BUCKET), (buckets["CDX-000009"], buckets[CID]))


if __name__ == "__main__":
    unittest.main()
