"""Writes the import tool's test fixtures (FINSIM1_PLAN P5 task 10): ``mini_batch.jsonl`` (the 9-line mini batch) and ``mini_pack.json``
(its small data pack, window 2026-10-21 .. 2026-10-26).  Run it from the ``finsim`` folder:
``py -3.12 tests/fixtures/terminal/import/make_fixtures.py``.  The values are the owner's database / reconciliation figures the P4
tests also use (Treasury par curves, FRED prints, ORCL closes, the ORCL Nov 20 140 call's bars); nothing here is fetched.

The nine lines: (1) an existing AAPL fill that matches (present_match, untouched), (2) an MSFT fill whose price differs from the
workbook (present_differs, reported, never corrected), (3) an as-of option BTO, (4) an as-of short TRS, (5) an as-of CDX IG,
(6) an as-of IRS, (7) an unresolved cash balance, (8) a declined T-bill line, (9) a historical-model listed BUY."""
from __future__ import annotations

import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = "IMP-TEST-MINI-A"
US, PB, DEBT = "F1:US_EXCHANGE", "F1:PB_GS", "F1:DEBT"
OTC_EXEC = "2026-10-21T19:45:00-04:00"
OPT_EXEC = "2026-10-22T16:00:00-04:00"
AS_OF_LABEL = "As-of booking - simulated fill at the 2026-10-22 closing print, entered <apply time> by the operator at the owner's instruction (batch IMP-TEST-MINI-A)"
OTC_LABEL = ("As-of booking - simulated OTC trade with Goldman Sachs, value date 2026-10-21, entered <apply time> by the operator at the owner's instruction"
             " (batch IMP-TEST-MINI-A)")
HM_LABEL = "Historical model (not an independent execution)"
CURVES = {
    "2026-10-21": {"1M": "4.09", "2M": "4.16", "3M": "4.25", "4M": "4.34", "6M": "4.35", "1Y": "4.52", "2Y": "4.81", "3Y": "4.89", "5Y": "4.95", "7Y": "5.03",
                   "10Y": "5.13", "20Y": "5.47", "30Y": "5.42"},
    "2026-10-22": {"1M": "4.11", "2M": "4.16", "3M": "4.22", "4M": "4.29", "6M": "4.29", "1Y": "4.41", "2Y": "4.65", "3Y": "4.72", "5Y": "4.78", "7Y": "4.86",
                   "10Y": "4.96", "20Y": "5.32", "30Y": "5.29"},
    "2026-10-23": {"1M": "4.11", "2M": "4.16", "3M": "4.22", "4M": "4.3", "6M": "4.3", "1Y": "4.44", "2Y": "4.69", "3Y": "4.75", "5Y": "4.82", "7Y": "4.89",
                   "10Y": "5", "20Y": "5.37", "30Y": "5.33"},
    "2026-10-26": {"1M": "4.11", "2M": "4.18", "3M": "4.22", "4M": "4.32", "6M": "4.35", "1Y": "4.47", "2Y": "4.68", "3Y": "4.79", "5Y": "4.84", "7Y": "4.91",
                   "10Y": "5", "20Y": "5.35", "30Y": "5.31"},
}
SOFR = [("2026-10-15", "3.89"), ("2026-10-16", "3.88"), ("2026-10-19", "3.87"), ("2026-10-20", "3.88"), ("2026-10-21", "3.88"), ("2026-10-22", "3.90"),
        ("2026-10-23", "3.92"), ("2026-10-26", "3.93")]
HY_OAS = [("2026-10-19", "3.29"), ("2026-10-20", "3.33"), ("2026-10-21", "3.27"), ("2026-10-22", "3.40"), ("2026-10-23", "3.38"), ("2026-10-26", "3.30")]
IG_OAS = [("2026-10-19", "0.90"), ("2026-10-20", "0.89"), ("2026-10-21", "0.88"), ("2026-10-22", "0.89"), ("2026-10-23", "0.91"), ("2026-10-26", "0.90")]
ORCL = {"2026-10-20": ("131.02", 9100000), "2026-10-21": ("129.13", 12000000), "2026-10-22": ("125.54", 15000000), "2026-10-23": ("126.16", 11000000),
        "2026-10-26": ("126.06", 9800000)}
ORCL_C140 = {"2026-10-21": ("4.55", 410), "2026-10-22": ("3.55", 1250), "2026-10-23": ("3.55", 980), "2026-10-26": ("3.15", 640)}
OCC = "ORCL261120C00140000"


def idem(canonical: str) -> str:
    return "imp:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def line(n, product, instrument, side, qty, price, account, canonical, decision, status, **extra):
    rec = {"record": "line", "batch_id": BATCH, "line_no": n, "idem_key": idem(canonical) if canonical else None, "idem_canonical": canonical,
           "source": {"workbook": "mini test workbook", "row": n}, "product": product, "instrument_id": instrument, "side": side, "qty": qty,
           "intended_price": price, "account_id": account, "match_status": status, "decision": decision, "historical_model": False}
    rec.update(extra)
    return rec


def build() -> list:
    lines = []
    lines.append(line(1, "EQUITY", "AAPL", "BUY", 100, "339.50", US, f"{US}|EQUITY|AAPL|BUY|100|2026-10-22", "preserve", "present_match",
                      intended_trade_date="2026-10-22", booking="EXISTING_SIMULATED_EXECUTION", product_label="Equity long",
                      source_label="Simulated fill (existing execution by the app's matcher; preserved untouched)"))
    lines.append(line(2, "EQUITY", "MSFT", "BUY", 50, "515.00", US, f"{US}|EQUITY|MSFT|BUY|50|2026-10-22", "preserve", "present_differs",
                      intended_trade_date="2026-10-22", booking="EXISTING_SIMULATED_EXECUTION", product_label="Equity long",
                      source_label="Simulated fill (existing execution by the app's matcher; preserved untouched)"))
    c3 = f"{US}|OPTION|{OCC}|BTO|10|2026-10-22"
    k3 = idem(c3)
    lines.append(line(3, "OPTION", OCC, "BTO", 10, "3.55", US, c3, "import", "missing_import", trade_date="2026-10-22", exec_ts=OPT_EXEC, booking="AS_OF",
                      settle_date="2026-10-23", product_label="Listed option long", source_label=AS_OF_LABEL,
                      command={"kind": "trade.book_as_of", "request_id": k3, "payload": {
                          "account_id": US, "instrument_id": OCC, "instrument_spec": {"occ": OCC, "underlying_id": "ORCL", "expiry": "2026-11-20", "strike": "140",
                                                                                       "right": "C", "style": "AMERICAN", "multiplier": 100,
                                                                                       "chain_row": "<injected at apply from the data pack's Cboe chain row>"},
                          "side": "BTO", "qty": 10, "price": "3.55", "order_type": "LIMIT", "tif": "DAY", "fee_rule": "SCHEDULE",
                          "as_of": {"trade_date": "2026-10-22", "exec_ts": OPT_EXEC, "batch_id": BATCH, "line_key": k3}}},
                      expected={"premium": "3550.00", "commission": "6.50"}))
    c4 = f"{PB}|OTC_TRS|ORCL|PAY_TOTAL_RETURN|1000|2026-10-21"
    k4 = idem(c4)
    lines.append(line(4, "OTC_TRS", "TRS ORCL (new contract id at booking)", "SHORT (pay total return)", 1000, "129.13", PB, c4, "import", "missing_import",
                      trade_date="2026-10-21", value_date="2026-10-21", exec_ts=OTC_EXEC, booking="AS_OF", product_label="OTC total return swap (short)",
                      source_label=OTC_LABEL, counterparty="Goldman Sachs",
                      command={"kind": "otc.book_as_of", "request_id": k4, "payload": {
                          "account_id": PB, "kind": "TRS", "terms": {"underlying_id": "ORCL", "units": 1000, "receiver": False, "reset_months": 1, "tenor_months": 12,
                                                                    "reset_px": "129.13"},
                          "as_of": {"trade_date": "2026-10-21", "value_date": "2026-10-21", "exec_ts": OTC_EXEC, "batch_id": BATCH, "line_key": k4}}},
                      expected={"im": "12913.00"}))
    c5 = f"{PB}|OTC_CDX|CDX.NA.IG.S47.5Y|BUY_PROTECTION|1000000|2026-10-21"
    k5 = idem(c5)
    lines.append(line(5, "OTC_CDX", "CDX.NA.IG S47 5Y (new contract id at booking)", "BUY_PROTECTION", "1000000 notional", "64 bp", PB, c5, "import",
                      "missing_import", trade_date="2026-10-21", value_date="2026-10-21", exec_ts=OTC_EXEC, booking="AS_OF",
                      product_label="OTC CDS index (buy protection)", source_label=OTC_LABEL, counterparty="Goldman Sachs",
                      command={"kind": "otc.book_as_of", "request_id": k5, "payload": {
                          "account_id": PB, "kind": "CDX", "terms": {"index": "IG", "series": 47, "tenor": "5Y", "side": "BUY_PROTECTION", "notional": "1000000.00",
                                                                    "dealt_spread_bp": "64"},
                          "as_of": {"trade_date": "2026-10-21", "value_date": "2026-10-21", "exec_ts": OTC_EXEC, "batch_id": BATCH, "line_key": k5}}},
                      expected={"im": "20000.00"}))
    c6 = f"{PB}|OTC_IRS|USD-SOFR|PAY_FIXED|1000000|2026-10-21|36M"
    k6 = idem(c6)
    lines.append(line(6, "OTC_IRS", "USD SOFR IRS 3Y (new contract id at booking)", "PAY_FIXED", "1000000 notional", "4.72 % fixed", PB, c6, "import",
                      "missing_import", trade_date="2026-10-21", value_date="2026-10-21", exec_ts=OTC_EXEC, booking="AS_OF",
                      product_label="OTC interest-rate swap (pay fixed)", source_label=OTC_LABEL, counterparty="Goldman Sachs",
                      command={"kind": "otc.book_as_of", "request_id": k6, "payload": {
                          "account_id": PB, "kind": "IRS", "terms": {"notional": "1000000.00", "pay_fixed": True, "tenor_months": 36, "fixed_rate": "0.04720",
                                                                    "fixed_months": 6, "fixed_basis": "ACT/360", "float_index": "SOFR", "float_basis": "ACT/360",
                                                                    "float_months": 12, "fixing": "COMPOUND_ARREARS", "calibrate_curve_spread_to_rate": True},
                          "as_of": {"trade_date": "2026-10-21", "value_date": "2026-10-21", "exec_ts": OTC_EXEC, "batch_id": BATCH, "line_key": k6}}}))
    lines.append(line(7, "CASH", "USD cash", None, "12345.67 (workbook residual)", None, None, None, "unresolved", "unresolved",
                      decision_reason="a balance, not a transaction: cash is the result of the ledger", product_label="Cash (USD)"))
    lines.append(line(8, "BOND", "UST bill reserve (never chosen)", "BUY", "1000000 face", "1", DEBT, f"{DEBT}|BOND|UST-BILL-RESERVE|BUY|1000000|2026-10-22",
                      "declined", "declined", intended_trade_date="2026-10-22", decision_reason="the owner decided not to book it",
                      product_label="Treasury bills (reserve) - declined"))
    c9 = f"{US}|ETF|SPY|BUY|10|2026-10-22"
    k9 = idem(c9)
    lines.append(line(9, "ETF", "SPY", "BUY", 10, "600.00", US, c9, "import", "missing_import", trade_date="2026-10-22", exec_ts=OPT_EXEC, booking="HISTORICAL_MODEL",
                      historical_model=True, settle_date="2026-10-23", product_label="Historical model listed buy", source_label=HM_LABEL,
                      command={"kind": "trade.book_as_of", "request_id": k9, "payload": {
                          "account_id": US, "instrument_id": "SPY", "side": "BUY", "qty": 10, "price": "600.00", "order_type": "LIMIT", "tif": "DAY",
                          "fee_rule": "SCHEDULE", "as_of": {"trade_date": "2026-10-22", "exec_ts": OPT_EXEC, "batch_id": BATCH, "line_key": k9,
                                                            "historical_model": True}}}))
    decisions = {"preserve": [1, 2], "import": [3, 4, 5, 6, 9], "declined": [8], "unresolved": [7]}
    header = {"record": "batch", "format_version": 2, "batch_id": BATCH, "status": "TEST FIXTURE", "book": "F (Fund Book)", "lifetime": "F1",
              "decisions": decisions, "decision_counts": {"preserve": 2, "import": 5, "declined": 1, "unresolved": 1, "total_lines": 9},
              "window": {"from": "2026-10-21", "through": "L = the last business day before the apply day (from the data pack)", "deadline": "2026-11-19"},
              "label_policy": {"as_of_option_rows": AS_OF_LABEL, "as_of_otc_rows": OTC_LABEL, "historical_model_rows": HM_LABEL}}
    setups = [{"record": "setup", "batch_id": BATCH, "step": s, "what": w} for s, w in
              (("S0", "preflight"), ("S1", "schema"), ("S2", "closes"), ("S2b", "FRED observations"), ("S3", "chain rows"), ("S4", "CDX basis"))]
    tail = [{"record": "catch_up", "batch_id": BATCH, "step": "C1-C6", "what": "the catch-up"},
            {"record": "summary", "batch_id": BATCH, "basis": "test fixture", "restated_nav_10_23": {"workbook": "25,000,000.00"}}]
    return [header] + setups + lines + tail


def pack() -> dict:
    return {"pack_format": 1, "batch_id": BATCH, "fetched_ts": 1793120400, "fetched_et": "2026-10-27 10:20:00 ET", "today": "2026-10-27",
            "window": {"from": "2026-10-21", "through": "2026-10-26"},
            "closes": {"ORCL": {"source": "Yahoo Finance daily bar (observed close)", "fetched_ts": 1793120400,
                                "bars": {d: {"close": c, "volume": v, "open": None, "high": None, "low": None} for d, (c, v) in ORCL.items()}}},
            "options": {OCC: {"trade_date": "2026-10-22", "underlying_id": "ORCL", "line_no": 3,
                              "daily": {"source": "Yahoo Finance option daily bar (last trade)", "fetched_ts": 1793120400,
                                        "bars": {d: {"close": c, "volume": v, "open": None, "high": None, "low": None} for d, (c, v) in ORCL_C140.items()}},
                              "chain_row": {"option": OCC, "bid": "4.30", "ask": "4.45", "last_trade_price": "4.375", "prev_day_close": "3.325", "volume": "512",
                                            "open_interest": "4120", "iv": "0.4123", "delta": "0.3011", "source": "Cboe delayed quotes", "chain_ts": 1793118960},
                              "official_close": {"date": "2026-10-26", "price": "3.325", "tied_to_L": True, "chain_ts": 1793118960,
                                                 "source": "Cboe delayed quotes prev_close (official close)"}}},
            "fred": {"BAMLC0A0CM": [list(x) for x in IG_OAS], "BAMLH0A0HYM2": [list(x) for x in HY_OAS], "SOFR": [list(x) for x in SOFR]},
            "fred_sources": {"BAMLC0A0CM": "FRED", "BAMLH0A0HYM2": "FRED", "SOFR": "FRED"},
            "curves": CURVES, "curve_sources": {d: "treasury.gov daily par yield curve (fixture)" for d in CURVES},
            "dividends": {"ORCL": [["2026-10-09", "0.50"]]}, "notes": [], "errors": []}


def main() -> None:
    with open(os.path.join(HERE, "mini_batch.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for rec in build():
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    with open(os.path.join(HERE, "mini_pack.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(pack(), f, indent=1, sort_keys=True)
    print("written", os.path.join(HERE, "mini_batch.jsonl"), os.path.join(HERE, "mini_pack.json"))


if __name__ == "__main__":
    main()
