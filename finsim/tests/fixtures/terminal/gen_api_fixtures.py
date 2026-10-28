"""Writes finsim/tests/fixtures/terminal/api/*.json for WP-12 (TERMINAL_DESIGN.md s15 shapes, hand-made).

Run from finsim/:  py -3.12 tests/fixtures/terminal/gen_api_fixtures.py   (rewrites tests/fixtures/terminal/api/*; kept for the arithmetic)
The fixtures describe ONE consistent paper world (the Personal Book, lifetime P2, USD 1,000,000) so the browser check
shows figures that tie.  Times are placeholders the fixture server substitutes at serve time ("$NOW-245" = now minus
245 s, "$TODAY" = the NY date) so freshness ages are realistic whenever the page is opened.

Hand-worked numbers (cents arithmetic, the API sends decimal strings):
  US_EXCHANGE advances 400,000.00
    AAPL  long 500 @ 339.50  cost 169,750.00  fee 2.50 (500 x 0.005)  T+1 payable 169,752.50   mark 341.10 delayed -> mv 170,550.00, unrealised 800.00, day P&L 797.50
    SPY   short 100 @ 769.32 proceeds 76,932.00 fee 1.00 (min)      T+1 receivable 76,931.00  mark 768.62 close -> |mv| 76,862.00, unrealised 70.00, day P&L 69.00
          borrow GC 0.30 %, collateral 1.02 x 76,932.00 = 78,470.64 at the lender
    ESZ26 long 1, settle 7734.50, mark 7754.25 delayed, x50 -> open trade equity (7754.25-7734.50) x 50 = 987.50; IM 15,000.00 posted; fee 2.25; day P&L 985.25
    AAPL 270115P00315000 long 5 @ 9.50 cost 4,750.00 fee 3.25 (5 x 0.65) payable 4,753.25; mark 9.75 (Cboe 9.70/9.80) -> mv 4,875.00, unrealised 125.00, day P&L 121.75
    TSLA  long 100 @ 250.00 cost 25,000.00 fee 1.00 (settled earlier); mark 255.10 STALE -> mv 25,510.00, unrealised 510.00; yesterday's snapshot mark 254.00 -> day P&L 110.00
    XYZ   long 10 @ 12.00 cost 120.00 fee 1.00 (settled earlier); mark UNAVAILABLE -> mv n/a, NAV partial
    settled = 400,000 - 25,001.00 - 121.00 - 15,000.00 - 78,470.64 - 2.25 = 281,405.11
    receivable 76,931.00; payables 169,752.50 + 4,753.25 = 174,505.75; pending = -97,574.75; available = 281,405.11 + 76,931.00 - 174,505.75 = 183,830.36
    NAV = 281,405.11 - 97,574.75 + 78,470.64 + 15,000.00 + (170,550 + 4,875 + 25,510) + 987.50 - 76,862.00 = 402,361.50
        = advances 400,000 - fees 11.00 + unrealised 2,492.50 - XYZ at cost 120.00 (excluded, unpriced)  = 402,361.50  (ties)
    P&L TODAY = 2,083.50 (NAV_0 = 400,278.00 with TSLA at 254.00; explained: -fees 9.00 + unrealised 2,092.50)
  DEBT advances 15,000.00: UST10Y 100 @ 97.50 cost 9,750.00 fee 5.00; mark 98.125 computed -> mv 9,812.50, unrealised 62.50; settled 5,245.00; NAV 15,057.50
  FOREIGN_FX advances 50,000.00: bought EUR 10,000 @ 1.1240 (mid 1.1238 + 0.2 pip) T+2: USD payable 11,240.00, EUR receivable 10,000 (= 11,238.00 at mid); NAV 49,998.00, fx_result -2.00
  PB_GS advances 100,000.00, no positions, NAV 100,000.00
  TREASURY capital 1,000,000.00, advances 565,000.00, retained 12.50 (cash interest), settled USD 435,000.00 + accrued 12.50 -> NAV 435,012.50 = 1,000,000 + 12.50 - 565,000
  Lifetime NAV = 435,012.50 + 402,361.50 + 15,057.50 + 0 + 49,998.00 + 100,000.00 = 1,002,429.50 (partial: XYZ unpriced)
"""
import json
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
FINSIM = r"C:\Users\logan\intelligence-terminal\finsim"
sys.path.insert(0, FINSIM)
from finsim.terminal import config, tables  # noqa: E402

OUT = os.path.join(FINSIM, "tests", "fixtures", "terminal", "api")
os.makedirs(OUT, exist_ok=True)
NOW = "$NOW"
TODAY = "$TODAY"
ACCT = "P2:US_EXCHANGE"


def jsonable(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    return o


def write(name, obj):
    with open(os.path.join(OUT, name + ".json"), "w", encoding="ascii") as f:
        json.dump(jsonable(obj), f, indent=1, ensure_ascii=True)
        f.write("\n")


def write_text(name, text):
    with open(os.path.join(OUT, name), "w", encoding="ascii", newline="") as f:
        f.write(text)


def quote(iid, last, quality="delayed", age=245, bid=None, ask=None, prev=None, hi=None, lo=None, vol=None, method="direct",
          source="Yahoo Finance (up to 15 min delayed)", delay_s=900, delay_label="up to 15 min delayed", ccy="USD", group="US_EQUITY",
          error=None, inputs=None, note=None, fetched=30):
    q = {"instrument_id": iid, "provider": source.split(" ")[0].lower(), "source": source, "method": method, "last": last,
         "bid": bid, "ask": ask, "bid_size": 100 if bid else None, "ask_size": 200 if ask else None,
         "mid": (str((Decimal(bid) + Decimal(ask)) / 2) if bid and ask else None), "prev_close": prev, "day_high": hi, "day_low": lo,
         "day_volume": vol, "quote_time": ("$NOW-%d" % age) if age is not None else None, "fetched_at": "$NOW-%d" % fetched,
         "delay_s": delay_s, "delay_label": delay_label, "currency": ccy, "session_group": group, "exchange_tz": "America/New_York",
         "provider_session": None, "error": error, "inputs": inputs, "quality": quality, "age_s": age}
    if note:
        q["note"] = note
    return q


def mark(iid, px, quality="delayed", age=245, method="direct", source="Yahoo Finance (up to 15 min delayed)", delay_label="up to 15 min delayed",
         inputs=None, executable=True, note=""):
    return {"instrument_id": iid, "mark": px, "quality": quality, "source": source, "method": method,
            "quote_time": ("$NOW-%d" % age) if age is not None else None, "fetched_at": "$NOW-30" if age is not None else None,
            "delay_label": delay_label, "inputs": inputs, "executable": executable and quality == "delayed", "note": note}


Q = {
    "AAPL": quote("AAPL", "341.10", prev="342.94", hi="343.20", lo="338.10", vol=30933066),
    "MSFT": quote("MSFT", "512.40", prev="509.90", hi="514.00", lo="508.25", vol=18200311, age=190),
    "NVDA": quote("NVDA", "188.20", prev="190.05", hi="191.10", lo="186.90", vol=150233100, age=130),
    "SPY": quote("SPY", "768.62", quality="close", age=3600, prev="766.48", hi="772.275", lo="764.26", vol=30979608, bid="768.61", ask="768.63",
                 source="Cboe delayed quotes", delay_label="~15 min delayed", group="US_EQUITY"),
    "TSLA": quote("TSLA", "255.10", quality="stale", age=1400, prev="252.00", hi="256.00", lo="250.10", vol=90120000, fetched=1300, error="HTTP 503 from query1"),
    "XYZ": quote("XYZ", None, quality="unavailable", age=None, fetched=600, error="Yahoo: No data found, symbol may be delisted"),
    "ESZ26.CME": quote("ESZ26.CME", "7754.25", prev="7734.50", hi="7760.00", lo="7701.75", vol=1531200, group="CME_EQUITY_INDEX", age=300),
    "EURUSD": quote("EURUSD", None, bid="1.1237", ask="1.1239", prev="1.1217", vol=0, group="FX_SPOT", age=120, source="Yahoo Finance (EURUSD=X, up to 15 min delayed)"),
    "UST10Y": quote("UST10Y", "98.125", quality="computed", method="curve", source="Treasury.gov par curve (model)", delay_s=0, delay_label="daily par curve",
                    age=25000, group="US_BONDS", inputs={"par_10y": {"value": "5.09", "as_of": "$NOW-25000", "quality": "close"}, "coupon": "4.25", "maturity": "2036-08-15"}),
    "AAPL 270115P00315000": quote("AAPL 270115P00315000", "9.75", bid="9.70", ask="9.80", prev="9.40", vol=1200, group="US_EQUITY_OPTIONS", method="chain",
                                  source="Cboe delayed quotes", delay_label="~15 min delayed", age=400),
}
Q["EURUSD"]["mid"] = "1.1238"
Q["AAPL 270115P00315000"]["mid"] = "9.75"
PRIVATE_MARK = mark("PRIV:ANTHROPIC", "61.50", quality="private", age=None, method="private_round", source="reported Series F (source: company press release 2026-09-02)",
                    delay_label="last reported round", executable=False, note="last round 2026-09-02 (Series F, post-money USD 183 bn)")

SESSIONS = {
    "US_EQUITY": {"group": "US_EQUITY", "open": True, "phase": "OPEN", "opened_at": "$NOW-7200", "closes_at": "$NOW+3600", "next_open": None, "trade_date": TODAY, "early_close": False, "note": ""},
    "US_EQUITY_OPTIONS": {"group": "US_EQUITY_OPTIONS", "open": True, "phase": "OPEN", "opened_at": "$NOW-7200", "closes_at": "$NOW+3600", "next_open": None, "trade_date": TODAY, "early_close": False, "note": ""},
    "US_INDEX_OPTIONS": {"group": "US_INDEX_OPTIONS", "open": True, "phase": "OPEN", "opened_at": "$NOW-7200", "closes_at": "$NOW+4500", "next_open": None, "trade_date": TODAY, "early_close": False, "note": ""},
    "US_BONDS": {"group": "US_BONDS", "open": True, "phase": "OPEN", "opened_at": "$NOW-12600", "closes_at": "$NOW+7200", "next_open": None, "trade_date": TODAY, "early_close": False, "note": "SIFMA approximated by the NYSE calendar"},
    "CME_EQUITY_INDEX": {"group": "CME_EQUITY_INDEX", "open": True, "phase": "OPEN", "opened_at": "$NOW-60000", "closes_at": "$NOW+7200", "next_open": "$NOW+10800", "trade_date": TODAY, "early_close": False, "note": "CME holidays are approximated from the NYSE calendar."},
    "FX_SPOT": {"group": "FX_SPOT", "open": True, "phase": "OPEN", "opened_at": "$NOW-60000", "closes_at": "$NOW+7200", "next_open": None, "trade_date": TODAY, "early_close": False, "note": ""},
}
PROVIDERS_OK = {p: {"ok": True, "last_ok_ts": "$NOW-%d" % s, "last_error": None, "consecutive_errors": 0, "down_until_ts": None, "requests_today": n}
                for p, s, n in [("yahoo", 20, 1412), ("cboe", 95, 38), ("treasury", 25000, 1), ("fred", 21000, 4), ("finra", 30000, 1), ("frankfurter", 3500, 6), ("fed", 1800, 3), ("newspaper", 240, 12)]}
WORKERS = {w: {"alive": True, "last_tick_ts": "$NOW-%d" % s, "stalled": False, "errors_1h": 0, "last_error": None} for w, s in [("quotes", 4), ("lifecycle", 12), ("matcher", 6), ("supervisor", 9)]}
INTEGRITY = {"ok": True, "hold": False, "checked_at": "$NOW-600", "problems": []}
CLOCK = {"utc": NOW, "ny": "$NY", "trading_day": TODAY, "accounting_day": TODAY}
HEALTH = {"app": "finsim-terminal", "version": "1.0.0", "schema": 1, "pid": 4242, "uptime_s": 5400, "started_ts": "$NOW-5400", "db_path": "C:\\Users\\logan\\.finsim\\terminal.db",
          "db_size": 3145728, "state_version": 42, "offline": False, "clock": CLOCK, "integrity": INTEGRITY, "providers": PROVIDERS_OK, "workers": WORKERS,
          "lifecycle": {"due": 0, "failing": []}, "attention_open": 2, "calendar": {"file": "data/nyse-holidays.json", "fallback_years": [2027, 2028]},
          "lock": {"path": "C:\\Users\\logan\\.finsim\\terminal.lock", "pid": 4242}, "news": {"injection_flags": 0, "fetched": {"yahoo": "$NOW-300", "fed": "$NOW-1800", "newspaper": "$NOW-240"}}}
write("health", HEALTH)
hd = json.loads(json.dumps(HEALTH))
hd["providers"]["yahoo"] = {"ok": False, "last_ok_ts": "$NOW-900", "last_err_ts": "$NOW-120", "last_error": "HTTP 503 Service Unavailable", "consecutive_errors": 5, "down_until_ts": "$NOW+120", "requests_today": 1412}
hd["providers"]["cboe"] = {"ok": False, "last_ok_ts": "$NOW-1000", "last_err_ts": "$NOW-60", "last_error": "timed out", "consecutive_errors": 2, "down_until_ts": "$NOW+60", "requests_today": 40}
hd["workers"]["quotes"]["stalled"] = True
hd["attention_open"] = 3
write("health_provider_down", hd)
ho = json.loads(json.dumps(HEALTH)); ho["offline"] = True
write("health_offline", ho)
hi = json.loads(json.dumps(HEALTH)); hi["integrity"] = {"ok": False, "hold": True, "checked_at": "$NOW-60", "problems": [{"check": "L3", "message": "gl_balances differs from the journal projection for P2:US_EXCHANGE/1010/USD by 0.01", "lifetime_id": "P2"}]}
write("health_integrity_hold", hi)
write("shutdown", {"shutting_down": True})

CONV = dict(tables.CONVENTIONS)
ACCOUNTS = [
    {"account_id": "P2:TREASURY", "name": "Treasury", "kind": "TREASURY", "role": "TREASURY", "broker": None, "status": "OPEN", "sweep_out": 1, "sweep_buffer": "0.00", "pb_financing": 0, "pb_limit": "0.00", "nav": "435012.50", "available_usd": "435000.00", "working_orders": 0, "pending_settlements": 0, "attention_count": 0, "sort_order": 0},
    {"account_id": ACCT, "name": "US Exchange", "kind": "TRADING", "role": "US_EXCHANGE", "broker": None, "status": "OPEN", "sweep_out": 1, "sweep_buffer": "10000.00", "pb_financing": 0, "pb_limit": "0.00", "nav": "402361.50", "available_usd": "183830.36", "working_orders": 2, "pending_settlements": 3, "attention_count": 1, "sort_order": 10},
    {"account_id": "P2:DEBT", "name": "Debt Instruments", "kind": "TRADING", "role": "DEBT", "broker": None, "status": "OPEN", "sweep_out": 1, "sweep_buffer": "10000.00", "pb_financing": 0, "pb_limit": "0.00", "nav": "15057.50", "available_usd": "5245.00", "working_orders": 0, "pending_settlements": 0, "attention_count": 0, "sort_order": 20},
    {"account_id": "P2:CREDIT", "name": "Credit Instruments", "kind": "TRADING", "role": "CREDIT", "broker": None, "status": "OPEN", "sweep_out": 1, "sweep_buffer": "10000.00", "pb_financing": 0, "pb_limit": "0.00", "nav": "0.00", "available_usd": "0.00", "working_orders": 0, "pending_settlements": 0, "attention_count": 0, "sort_order": 30},
    {"account_id": "P2:FOREIGN_FX", "name": "Foreign Markets & FX", "kind": "TRADING", "role": "FOREIGN_FX", "broker": None, "status": "OPEN", "sweep_out": 1, "sweep_buffer": "10000.00", "pb_financing": 0, "pb_limit": "0.00", "nav": "49998.00", "available_usd": "38760.00", "working_orders": 0, "pending_settlements": 2, "attention_count": 0, "sort_order": 40},
    {"account_id": "P2:PB_GS", "name": "Goldman Sachs (PB)", "kind": "PB", "role": "PB_GS", "broker": "GS", "status": "OPEN", "sweep_out": 1, "sweep_buffer": "10000.00", "pb_financing": 1, "pb_limit": "500000.00", "nav": "100000.00", "available_usd": "100000.00", "working_orders": 0, "pending_settlements": 0, "attention_count": 0, "sort_order": 100},
]
write("accounts", ACCOUNTS)
write("accounts_pb", {"account": ACCOUNTS[-1], "schedule": tables.PB_SCHEDULES["GS"]})
write("accounts_patch", ACCOUNTS[1])
write("accounts_schedule", {"account_id": "P2:PB_GS", "broker": "GS", "schedule": tables.PB_SCHEDULES["GS"], "updated_ts": "$NOW-86400", "fields_note": tables._PB_FIELDS_NOTE})

ELIG_P = {"lifetime_id": "P2", "nav": "1002429.50", "pb_otc": True, "pb_otc_reason": "prime brokers & OTC: relationship with GS opened 2026-10-18 (relationships stay open; NAV 1,002,429.50 is below the USD 25,000,000 threshold)",
          "private_markets": False, "private_reason": "Private Markets: unlock at USD 5,000,000 NAV - now 1,002,429.50", "ecp": False, "threshold": "25000000.00", "qp_threshold": "5000000.00",
          "has_relationships": True, "can_onboard": True, "profile": "INDIVIDUAL"}
ELIG_F = {"lifetime_id": "F1", "nav": "25000000.00", "pb_otc": True, "pb_otc_reason": "eligible from day one (Fund profile)", "private_markets": True, "private_reason": "Private Markets open (NAV at the USD 25,000,000 QP threshold)",
          "ecp": True, "threshold": "25000000.00", "qp_threshold": "25000000.00", "has_relationships": False, "can_onboard": True, "profile": "FUND"}
ELIG_I = {"lifetime_id": "I1", "nav": "1000000000.00", "pb_otc": True, "pb_otc_reason": "eligible from day one (Institutional profile)", "private_markets": True, "private_reason": "Private Markets open from the start (Institutional)",
          "ecp": True, "threshold": "25000000.00", "qp_threshold": "0.00", "has_relationships": False, "can_onboard": True, "profile": "INSTITUTION"}
BOOKS = [
    {"book_code": "P", "name": "Personal Book", "profile": "INDIVIDUAL", "lifetime": {"lifetime_id": "P2", "seq": 2, "started": "2026-10-15", "starting_capital": "1000000.00"}, "nav": {"value": "1002429.50", "quality": "delayed", "partial": True}, "eligibility": ELIG_P, "accounts": [a["account_id"] for a in ACCOUNTS], "past_lifetimes": 1},
    {"book_code": "F", "name": "Fund Book", "profile": "FUND", "lifetime": {"lifetime_id": "F1", "seq": 1, "started": "2026-10-15", "starting_capital": "25000000.00"}, "nav": {"value": "25000000.00", "quality": "delayed", "partial": False}, "eligibility": ELIG_F, "accounts": ["F1:TREASURY", "F1:US_EXCHANGE", "F1:DEBT", "F1:CREDIT", "F1:FOREIGN_FX", "F1:PRIVATE"], "past_lifetimes": 0},
    {"book_code": "I", "name": "Institutional Book", "profile": "INSTITUTION", "lifetime": {"lifetime_id": "I1", "seq": 1, "started": "2026-10-15", "starting_capital": "1000000000.00"}, "nav": {"value": "1000000000.00", "quality": "delayed", "partial": False}, "eligibility": ELIG_I, "accounts": ["I1:TREASURY", "I1:US_EXCHANGE", "I1:DEBT", "I1:CREDIT", "I1:FOREIGN_FX", "I1:PRIVATE"], "past_lifetimes": 0},
]
write("books", BOOKS)
write("books_lifetimes", {"book_code": "P", "lifetimes": [
    {"lifetime_id": "P2", "seq": 2, "name": "Personal Book", "started": "2026-10-15", "started_ts": "$NOW-432000", "ended": None, "ended_ts": None, "starting_capital": "1000000.00", "status": "OPEN", "final_nav": None, "final_realised": None, "final_pnl": None, "final_quality": None, "reason": None, "integrity_ok": True},
    {"lifetime_id": "P1", "seq": 1, "name": "Personal Book", "started": "2026-10-01", "started_ts": "$NOW-1641600", "ended": "2026-10-15", "ended_ts": "$NOW-432000", "starting_capital": "1000000.00", "status": "CLOSED", "final_nav": "812440.00", "final_realised": "-190120.00", "final_pnl": "-187560.00", "final_quality": "close", "reason": "owner reset (lifetime reset)", "integrity_ok": True}]})
write("books_reset", {"closed": {"lifetime_id": "P2", "final_nav": "1002429.50", "final_pnl": "2429.50", "ended": TODAY, "integrity_ok": True}, "opened": {"lifetime_id": "P3", "accounts": ["P3:TREASURY", "P3:US_EXCHANGE", "P3:DEBT", "P3:CREDIT", "P3:FOREIGN_FX"]}})
write("books_onboard", {"agreements": [{"kind": "ISDA_MASTER", "broker": "JPM", "signed_date": TODAY}, {"kind": "CSA", "broker": "JPM", "signed_date": TODAY}, {"kind": "PBA", "broker": "JPM", "signed_date": TODAY}],
                        "account": {"account_id": "P2:PB_JPM", "name": "JPMorgan (PB)", "kind": "PB", "broker": "JPM", "status": "OPEN", "nav": "0.00", "available_usd": "0.00"}})

SETTINGS = {"participation_rate": "0.10", "pb_otc_threshold": "25000000.00", "qp_threshold": {"INDIVIDUAL": "5000000.00", "FUND": "25000000.00", "INSTITUTION": "0.00"},
            "book_defaults": {"P": {"name": "Personal Book", "starting_capital": "1000000.00"}, "F": {"name": "Fund Book", "starting_capital": "25000000.00"}, "I": {"name": "Institutional Book", "starting_capital": "1000000000.00"}},
            "private_defaults": {"fee_pct": "0.03", "settle_days": 30, "lockup_months": 6}, "treasury_reserve": "0.00", "session_overrides": {}, "sweep_confirm_threshold": "2000000.00", "gtc_days": 90}
write("settings", SETTINGS)
ATTENTION = [
    {"id": 1, "ts": "$NOW-1500", "severity": "WARN", "source": "orders", "code": "FUNDING_SHORTFALL", "message": "P2:FOREIGN_FX: EUR 10,000 settlement on " + TODAY + " needs USD 11,240.00; Treasury sweep covers it at 17:30 ET", "account_id": "P2:FOREIGN_FX", "ref_type": "settlement", "ref_id": "st:fx:1:USD", "resolved_ts": None, "auto_resolve": 1},
    {"id": 2, "ts": "$NOW-4000", "severity": "INFO", "source": "books", "code": "ELIGIBILITY_BELOW_THRESHOLD", "message": "Personal Book NAV 1,002,429.50 is below the PB/OTC threshold of 25,000,000.00; the GS relationship stays open", "account_id": None, "ref_type": "lifetime", "ref_id": "P2", "resolved_ts": None, "auto_resolve": 0},
    {"id": 3, "ts": "$NOW-9000", "severity": "WARN", "source": "lifecycle", "code": "SPLIT_MANUAL_REVIEW", "message": "Yahoo reports a 4:1 split event on NVDA (ex 2026-10-19): splits are not applied to lots in this build - manual review", "account_id": ACCT, "ref_type": "instrument", "ref_id": "NVDA", "resolved_ts": None, "auto_resolve": 0},
]
write("attention", ATTENTION)
write("attention_resolve", dict(ATTENTION[1], resolved_ts=NOW))
# GET /api/attention/summary?book= (attention.attention_summary; the 1.0.5 Dashboard's tiles): the same counts the retired news page showed -
# working orders 2 (US Exchange), pending settlements 5 (US Exchange 3 + Foreign Markets & FX 2), the 3 open items above
write("attention_summary", {"scope": "P2:ALL", "scope_kind": "LIFETIME", "lifetimes": ["P2"], "accounts": [a["account_id"] for a in ACCOUNTS],
                            "as_of": NOW, "accounting_day": TODAY, "window_start": "$NOW-3600",
                            "integrity": {"ok": True, "checked_at": "$NOW-600", "mode": "ledger", "failing": [], "warnings": 0, "problems": []},
                            "hold": {"held": False, "lifetimes": [], "since": {}, "failing": {}},
                            "open": {"ERROR": 0, "WARN": 2, "INFO": 1}, "attention_open": 3,
                            "items": [{k: r[k] for k in ("id", "ts", "severity", "source", "code", "message", "account_id", "ref_type", "ref_id")} for r in ATTENTION],
                            "warnings": 3, "compliance": 4, "collateral_calls_today": 0, "collateral_marks_today": 0, "swap_vm_payments_today": 0, "recalls": 0,
                            "working_orders": 2, "pending_settlements": 5, "providers": {"down": [], "refused_ua": [], "paused": 0}, "notes": []})
write("state", {"accounts": ACCOUNTS, "settings": SETTINGS, "sessions": SESSIONS, "clock": CLOCK, "attention": ATTENTION, "integrity": INTEGRITY, "version": "1.0.0", "schema": 1,
                "conventions": CONV, "prime_brokers": config.PRIME_BROKERS, "pb_schedules": tables.PB_SCHEDULES, "books": BOOKS})
write("sessions", {"sessions": SESSIONS, "rules": {g: tables.SESSIONS[g] for g in SESSIONS}, "server_time": NOW})
write("calendar", {"year": 2026, "holidays": ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"], "early_closes": ["2026-11-27", "2026-12-24"], "source": "data/nyse-holidays.json", "fallback_years": [2027, 2028]})

CASH_US = {"USD": {"account_id": ACCT, "currency": "USD", "settled": "281405.11", "receivable": "76931.00", "payable": "174505.75", "pending": "-97574.75", "held_at_lender": "78470.64", "margin": "15000.00", "otc_margin": "0.00", "reserved": "0.00", "accrued": "0.00", "available": "183830.36"}}
BP_US = {"USD": {"account_id": ACCT, "currency": "USD", "available": "183830.36", "treasury_available": "435000.00", "pb_headroom": "0.00", "total": "618830.36", "settle_date": "$TODAY+1", "formula": "own available + Treasury available + PB headroom (each floored at 0)"}}
NAV_US = {"scope": ACCT, "nav": "402361.50", "assets": "576799.25", "liabilities": "174437.75", "quality": "stale", "partial": True, "missing": ["XYZ"], "as_of": NOW, "cash": "281405.11", "pending": "-97574.75",
          "mv_long": "200935.00", "mv_short": "-76862.00", "unrealised": "2492.50", "fx_result": "0.00", "capital": "0.00", "transfers": "400000.00", "realised": "0.00", "income": "0.00", "financing": "0.00", "fees": "11.00", "retained": "2481.50",
          "by_account": {ACCT: "402361.50"}, "lifetime_ids": ["P2"], "archived": False, "fx_missing": [], "note": "NAV excludes 1 unpriced position (XYZ)"}
PNL_US_TODAY = {"scope": ACCT, "period": "TODAY", "nav": "402361.50", "nav_0": "400278.00", "flows": "0.00", "pnl": "2083.50", "explained": "2083.50", "residual": "0.00",
                "components": {"realised": "0.00", "income": "0.00", "financing": "0.00", "fees": "9.00", "unrealised": "2092.50", "fx_result": "0.00"},
                "baseline": {"snap_date": "$TODAY-1", "ts": "$NOW-60000", "note": "yesterday 17:45 ET snapshot"}, "degraded": False, "quality": "stale", "partial": True}


def pnl(scope, period, nav, nav0, pnlv, fees, unreal, fx="0.00", realised="0.00", income="0.00"):
    return {"scope": scope, "period": period, "nav": nav, "nav_0": nav0, "flows": "0.00", "pnl": pnlv, "explained": pnlv, "residual": "0.00",
            "components": {"realised": realised, "income": income, "financing": "0.00", "fees": fees, "unrealised": unreal, "fx_result": fx},
            "baseline": {"snap_date": "$TODAY-1", "ts": "$NOW-60000", "note": "last snapshot before this accounting day"}, "degraded": False, "quality": "stale", "partial": True}


def lot(lid, side, date, qty, px, cost, fill, lock=None, ccy="USD"):
    return {"lot_id": lid, "side": side, "open_date": date, "open_ts": "$NOW-7000", "qty_open": qty, "qty_remaining": qty, "unit_cost": px, "cost": cost, "open_fill_id": fill, "lock_until": lock, "currency": ccy}


POS = [
    {"account_id": ACCT, "instrument_id": "AAPL", "symbol": "AAPL", "name": "Apple Inc.", "kind": "EQUITY", "klass": "securities", "currency": "USD", "qty": 500, "cost": "169750.00", "avg_px": "339.5000",
     "mark": mark("AAPL", "341.10"), "mv": "170550.00", "mv_base": "170550.00", "notional": None, "unrealised": "800.00", "unrealised_base": "800.00", "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "2.50", "income_pending": "0.00",
     "day_pnl": "797.50", "weight": "0.4239", "days_held": 0, "settle_px": None, "borrow": None, "lots": [lot(1, "LONG", TODAY, 500, "339.5000", "169750.00", "f:1")], "extra": {}},
    {"account_id": ACCT, "instrument_id": "SPY", "symbol": "SPY", "name": "SPDR S&P 500 ETF", "kind": "ETF", "klass": "securities", "currency": "USD", "qty": -100, "cost": "76932.00", "avg_px": "769.3200",
     "mark": mark("SPY", "768.62", quality="close", age=3600, source="Cboe delayed quotes", delay_label="~15 min delayed"), "mv": "-76862.00", "mv_base": "-76862.00", "notional": None, "unrealised": "70.00", "unrealised_base": "70.00", "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "1.00", "income_pending": "0.00",
     "day_pnl": "69.00", "weight": "-0.1910", "days_held": 0, "settle_px": None, "borrow": {"borrow_id": "b:1", "qty": 100, "fee_rate": "0.0030", "rebate_rate": "0.0337", "tier": "GC", "lender": "modelled lender", "collateral": "78470.64"}, "lots": [lot(2, "SHORT", TODAY, 100, "769.3200", "76932.00", "f:2")], "extra": {}},
    {"account_id": ACCT, "instrument_id": "ESZ26.CME", "symbol": "ESZ26", "name": "E-mini S&P 500 Dec 2026", "kind": "FUTURE", "klass": "futures", "currency": "USD", "qty": 1, "cost": "0.00", "avg_px": "7734.5000",
     "mark": mark("ESZ26.CME", "7754.25", age=300), "mv": "0.00", "mv_base": "0.00", "notional": "387712.50", "unrealised": "987.50", "unrealised_base": "987.50", "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "2.25", "income_pending": "0.00",
     "day_pnl": "985.25", "weight": "0.0000", "days_held": 0, "settle_px": "7734.50", "borrow": None, "lots": [lot(3, "LONG", TODAY, 1, "7734.5000", "0.00", "f:3")], "extra": {"initial_margin": "15000.00", "maintenance_margin": "13500.00"}},
    {"account_id": ACCT, "instrument_id": "AAPL 270115P00315000", "symbol": "AAPL 15JAN27 315 P", "name": "AAPL Jan-15-2027 315 put", "kind": "OPTION", "klass": "listed options", "currency": "USD", "qty": 5, "cost": "4750.00", "avg_px": "9.5000",
     "mark": mark("AAPL 270115P00315000", "9.75", age=400, method="chain", source="Cboe delayed quotes", delay_label="~15 min delayed"), "mv": "4875.00", "mv_base": "4875.00", "notional": None, "unrealised": "125.00", "unrealised_base": "125.00", "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "3.25", "income_pending": "0.00",
     "day_pnl": "121.75", "weight": "0.0121", "days_held": 0, "settle_px": None, "borrow": None, "lots": [lot(4, "LONG", TODAY, 5, "9.5000", "4750.00", "f:4")], "extra": {"multiplier": 100, "expiry": "2027-01-15"}},
    {"account_id": ACCT, "instrument_id": "TSLA", "symbol": "TSLA", "name": "Tesla, Inc.", "kind": "EQUITY", "klass": "securities", "currency": "USD", "qty": 100, "cost": "25000.00", "avg_px": "250.0000",
     "mark": mark("TSLA", "255.10", quality="stale", age=1400, executable=False, note="provider error: HTTP 503 from query1"), "mv": "25510.00", "mv_base": "25510.00", "notional": None, "unrealised": "510.00", "unrealised_base": "510.00", "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "1.00", "income_pending": "0.00",
     "day_pnl": "110.00", "weight": "0.0634", "days_held": 3, "settle_px": None, "borrow": None, "lots": [lot(5, "LONG", "$TODAY-3", 100, "250.0000", "25000.00", "f:0a")], "extra": {}},
    {"account_id": ACCT, "instrument_id": "XYZ", "symbol": "XYZ", "name": "XYZ Holdings (delisted)", "kind": "EQUITY", "klass": "securities", "currency": "USD", "qty": 10, "cost": "120.00", "avg_px": "12.0000",
     "mark": mark("XYZ", None, quality="unavailable", age=None, executable=False, note="Yahoo: No data found, symbol may be delisted"), "mv": None, "mv_base": None, "notional": None, "unrealised": None, "unrealised_base": None, "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "1.00", "income_pending": "0.00",
     "day_pnl": None, "weight": None, "days_held": 9, "settle_px": None, "borrow": None, "lots": [lot(6, "LONG", "$TODAY-9", 10, "12.0000", "120.00", "f:0b")], "extra": {}},
]
POS_DEBT = {"account_id": "P2:DEBT", "instrument_id": "UST10Y", "symbol": "T 4.25 08/36", "name": "US Treasury 4.25 % Aug-2036", "kind": "BOND", "klass": "securities", "currency": "USD", "qty": 100, "cost": "9750.00", "avg_px": "97.5000",
            "mark": mark("UST10Y", "98.125", quality="computed", age=25000, method="curve", source="Treasury.gov par curve (model)", delay_label="daily par curve", executable=False,
                         inputs={"par_10y": {"value": "5.09", "as_of": "$NOW-25000", "quality": "close"}, "coupon": "4.25"}, note="model price, not executable in this build"),
            "mv": "9812.50", "mv_base": "9812.50", "notional": None, "unrealised": "62.50", "unrealised_base": "62.50", "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "5.00", "income_pending": "0.00",
            "day_pnl": "0.00", "weight": "0.6517", "days_held": 4, "settle_px": None, "borrow": None, "lots": [lot(7, "LONG", "$TODAY-4", 100, "97.5000", "9750.00", "f:0c")], "extra": {"support": "LIMITED"}}
POS_PRIVATE = {"account_id": "I1:PRIVATE", "instrument_id": "PRIV:ANTHROPIC", "symbol": "ANTHROPIC", "name": "Anthropic PBC (private)", "kind": "PRIVATE", "klass": "private / illiquid", "currency": "USD", "qty": 20000, "cost": "1180000.00", "avg_px": "59.0000",
               "mark": PRIVATE_MARK, "mv": "1230000.00", "mv_base": "1230000.00", "notional": None, "unrealised": "50000.00", "unrealised_base": "50000.00", "realised_itd": "0.00", "income_itd": "0.00", "financing_itd": "0.00", "fees_itd": "35400.00", "income_pending": "0.00",
               "day_pnl": "0.00", "weight": "0.0012", "days_held": 12, "settle_px": None, "borrow": None, "lots": [lot(8, "LONG", "$TODAY-12", 20000, "59.0000", "1180000.00", "f:p1", lock="2027-04-08")], "extra": {"lock_until": "2027-04-08"}}
write("accounts_positions", POS)
write("accounts_positions_debt", [POS_DEBT])
write("accounts_positions_credit", [])
write("accounts_positions_foreign_fx", [])
write("accounts_positions_pb_gs", [])
write("accounts_positions_treasury", [])
FILLS = [
    {"fill_id": "f:1", "order_id": "o:1", "account_id": ACCT, "instrument_id": "AAPL", "side": "BUY", "qty": 500, "price": "339.50", "gross": "169750.00", "fee": "2.50", "currency": "USD", "fx_rate": "1", "ts": "$NOW-7000", "trade_date": TODAY, "settle_date": "$TODAY+1", "origin": "ORDER", "price_basis": "last (Yahoo, up to 15 min delayed)", "quote": quote("AAPL", "339.50", age=7245, fetched=7030), "bar_ts": "$NOW-7020", "cap_qty": 6190, "cap_note": "latest 1-minute bar volume 61,900; 10 % participation caps a pass at 6,190 shares"},
    {"fill_id": "f:2", "order_id": "o:2", "account_id": ACCT, "instrument_id": "SPY", "side": "SELL", "qty": 100, "price": "769.32", "gross": "76932.00", "fee": "1.00", "currency": "USD", "fx_rate": "1", "ts": "$NOW-6500", "trade_date": TODAY, "settle_date": "$TODAY+1", "origin": "ORDER", "price_basis": "Cboe bid (fresh chain cached)", "quote": quote("SPY", "769.32", bid="769.32", ask="769.34", age=6745, fetched=6520, source="Cboe delayed quotes", delay_label="~15 min delayed"), "bar_ts": "$NOW-6520", "cap_qty": 3100, "cap_note": "bar volume 31,000; cap 3,100"},
    {"fill_id": "f:3", "order_id": "o:3", "account_id": ACCT, "instrument_id": "ESZ26.CME", "side": "BUY", "qty": 1, "price": "7734.50", "gross": "0.00", "fee": "2.25", "currency": "USD", "fx_rate": "1", "ts": "$NOW-6000", "trade_date": TODAY, "settle_date": TODAY, "origin": "ORDER", "price_basis": "last (Yahoo, up to 15 min delayed)", "quote": quote("ESZ26.CME", "7734.50", age=6245, fetched=6020, group="CME_EQUITY_INDEX"), "bar_ts": "$NOW-6020", "cap_qty": 153, "cap_note": "bar volume 1,531; cap 153 contracts"},
    {"fill_id": "f:4", "order_id": "o:4", "account_id": ACCT, "instrument_id": "AAPL 270115P00315000", "side": "BUY", "qty": 5, "price": "9.50", "gross": "4750.00", "fee": "3.25", "currency": "USD", "fx_rate": "1", "ts": "$NOW-5500", "trade_date": TODAY, "settle_date": "$TODAY+1", "origin": "ORDER", "price_basis": "Cboe ask", "quote": quote("AAPL 270115P00315000", "9.50", bid="9.45", ask="9.50", age=5745, fetched=5520, method="chain", source="Cboe delayed quotes", delay_label="~15 min delayed", group="US_EQUITY_OPTIONS"), "bar_ts": None, "cap_qty": 40, "cap_note": "displayed ask size 40"},
]
write("accounts_fills", FILLS)


def order(oid, iid, sym, side, qty, filled, typ, status, created, limit=None, stop=None, reason=None, avg=None, tif="DAY", note=None, trail_amt=None, trail_pct=None):
    return {"order_id": oid, "request_id": "ui:" + oid.replace(":", "-") + "-aaaa", "account_id": ACCT, "instrument_id": iid, "symbol": sym, "side": side, "qty": qty, "filled_qty": filled, "avg_px": avg, "order_type": typ, "limit_px": limit, "stop_px": stop,
            "trail_amt": trail_amt, "trail_pct": trail_pct, "fill_mode": "SEQUENTIAL", "tif": tif, "status": status, "created_ts": "$NOW-%d" % created, "updated_ts": "$NOW-%d" % (created - 10), "expires_ts": "$NOW+3600" if tif == "DAY" else "$NOW+7776000",
            "reason": reason, "last_event": {"ts": "$NOW-%d" % (created - 10), "to_status": status, "note": note or status.lower()}, "fills": [], "fees": "0.00", "package_id": None, "parent_order_id": None, "participation": "0.10"}


OPEN_ORDERS = [order("o:5", "MSFT", "MSFT", "BUY", 200, 0, "LIMIT", "WORKING", 3000, limit="505.00", note="resting; crosses when last <= 505.00"),
               order("o:6", "AAPL", "AAPL", "SELL", 200, 0, "TRAILING_STOP", "WORKING", 2500, trail_amt="5.00", stop="336.10", tif="GTC", note="trail 5.00 below the highest observed last 341.10")]
HIST_ORDERS = [order("o:1", "AAPL", "AAPL", "BUY", 500, 500, "MARKET", "FILLED", 7100, avg="339.50", note="filled at the last observed trade 339.50"),
               order("o:2", "SPY", "SPY", "SELL_SHORT", 100, 100, "MARKET", "FILLED", 6600, avg="769.32", note="borrow b:1 (GC 0.30 %), filled at the Cboe bid"),
               order("o:3", "ESZ26.CME", "ESZ26", "BUY", 1, 1, "MARKET", "FILLED", 6100, avg="7734.50"),
               order("o:4", "AAPL 270115P00315000", "AAPL 15JAN27 315 P", "BTO", 5, 5, "LIMIT", "FILLED", 5600, limit="9.50", avg="9.50", note="marketable limit filled at the Cboe ask"),
               order("o:0", "NVDA", "NVDA", "BUY", 50, 0, "LIMIT", "CANCELLED", 80000, limit="180.00", reason="cancelled by user", tif="GTC")]
REJECTIONS = [{"id": 1, "ts": "$NOW-4200", "request_id": "ui:rej-1-aaaa", "account_id": ACCT, "instrument_id": "GME", "side": "SELL_SHORT", "qty": 1000, "order_type": "MARKET", "code": "NO_BORROW", "message": "no borrow available for GME (FINRA short ratio 0.71, tier NONE)"},
              {"id": 2, "ts": "$NOW-3900", "request_id": "ui:rej-2-aaaa", "account_id": ACCT, "instrument_id": "TSLA", "side": "BUY", "qty": 100, "order_type": "MARKET", "code": "QUOTE_STALE", "message": "TSLA quote is stale (23 min old during the session); executions blocked"}]
write("accounts_orders", {"orders": OPEN_ORDERS, "rejections": []})
write("accounts_orders_all", {"orders": OPEN_ORDERS + HIST_ORDERS, "rejections": REJECTIONS})
write("order", dict(HIST_ORDERS[0], events=[{"ts": "$NOW-7100", "from_status": None, "to_status": "WORKING", "note": "accepted"}, {"ts": "$NOW-7000", "from_status": "WORKING", "to_status": "FILLED", "note": "filled 500 @ 339.50"}], fills=[FILLS[0]], entries=[{"entry_id": 11, "kind": "fill", "memo": "BUY 500 AAPL @ 339.50"}]))
write("order_cancel", {"order": dict(OPEN_ORDERS[0], status="CANCELLED", reason="cancelled by user")})
write("order_resume", {"order": dict(OPEN_ORDERS[0], status="WORKING")})


def txn(eid, ts, kind, memo, iid=None, qty=None, price=None, gross=None, fee=None, net=None, ref=None, cp=None, lines=None, origin=None):
    return {"entry_id": eid, "ts": "$NOW-%d" % ts, "ts_ny": "$NYTS-%d" % ts, "business_date": TODAY, "kind": kind, "memo": memo, "ref_type": ref[0] if ref else None, "ref_id": ref[1] if ref else None, "command_id": eid, "request_id": "ui:cmd-%d-aaaa" % eid,
            "instrument_id": iid, "qty": qty, "price": price, "gross": gross, "fee": fee, "origin": origin, "net_cash": net or {}, "accounts": [ACCT] + ([cp] if cp else []), "counterparty": cp, "lines": lines or []}


TXNS = [
    txn(3, 86000, "transfer", "advance from Treasury", net={"USD": "400000.00"}, ref=("transfer", "t:1"), cp="P2:TREASURY", lines=[{"account_id": ACCT, "gl": "1010", "gl_name": "Cash settled", "currency": "USD", "debit": "400000.00", "credit": None, "instrument_id": None, "memo": "advance"}, {"account_id": ACCT, "gl": "3100", "gl_name": "Treasury advances", "currency": "USD", "debit": None, "credit": "400000.00", "instrument_id": None, "memo": "advance"}]),
    txn(5, 7000, "fill", "BUY 500 AAPL @ 339.50 (fee 2.50)", iid="AAPL", qty=500, price="339.50", gross="169750.00", fee="2.50", net={"USD": "-169752.50"}, ref=("fill", "f:1"), origin="ORDER", lines=[{"account_id": ACCT, "gl": "1100", "gl_name": "Equities at cost", "currency": "USD", "debit": "169750.00", "credit": None, "instrument_id": "AAPL", "memo": "lot 1"}, {"account_id": ACCT, "gl": "5000", "gl_name": "Fees", "currency": "USD", "debit": "2.50", "credit": None, "instrument_id": "AAPL", "memo": "USD 0.005/share min 1.00"}, {"account_id": ACCT, "gl": "2100", "gl_name": "Purchases payable", "currency": "USD", "debit": None, "credit": "169752.50", "instrument_id": "AAPL", "memo": "T+1"}]),
    txn(6, 6500, "fill", "SELL SHORT 100 SPY @ 769.32 (fee 1.00)", iid="SPY", qty=-100, price="769.32", gross="76932.00", fee="1.00", net={"USD": "76931.00"}, ref=("fill", "f:2"), origin="ORDER"),
    txn(7, 6480, "collateral", "borrow b:1 collateral 102 % posted to the lender", iid="SPY", net={"USD": "-78470.64"}, ref=("borrow", "b:1")),
    txn(8, 6000, "fill", "BUY 1 ESZ26.CME @ 7734.50 (fee 2.25)", iid="ESZ26.CME", qty=1, price="7734.50", gross="0.00", fee="2.25", net={"USD": "-2.25"}, ref=("fill", "f:3"), origin="ORDER"),
    txn(9, 5990, "margin", "futures initial margin 15,000.00 to the clearing deposit", iid="ESZ26.CME", net={"USD": "-15000.00"}, ref=("margin", "ESZ26.CME")),
    txn(10, 5500, "fill", "BTO 5 AAPL 270115P00315000 @ 9.50 (fee 3.25)", iid="AAPL 270115P00315000", qty=5, price="9.50", gross="4750.00", fee="3.25", net={"USD": "-4753.25"}, ref=("fill", "f:4"), origin="ORDER"),
]
write("accounts_transactions", {"rows": TXNS, "total": len(TXNS), "net_cash": {"USD": "208952.36"}})
write_text("accounts_transactions.csv", "entry_id,ts_ny,business_date,kind,memo,instrument_id,qty,price,gross,fee,net_USD\r\n" + "\r\n".join(
    "%d,%s,%s,%s,\"%s\",%s,%s,%s,%s,%s,%s" % (t["entry_id"], "2026-10-20 15:40", t["business_date"], t["kind"], t["memo"], t["instrument_id"] or "", t["qty"] or "", t["price"] or "", t["gross"] or "", t["fee"] or "", t["net_cash"].get("USD", "")) for t in TXNS) + "\r\n")
write_text("export_transactions.csv", open(os.path.join(OUT, "accounts_transactions.csv")).read())
write_text("history_export_transactions.csv", open(os.path.join(OUT, "accounts_transactions.csv")).read())
write_text("export_positions.csv", "account_id,instrument_id,qty,cost,mark,quality,mv,unrealised\r\n" + "\r\n".join("%s,%s,%s,%s,%s,%s,%s,%s" % (p["account_id"], p["instrument_id"], p["qty"], p["cost"], p["mark"]["mark"] or "", p["mark"]["quality"], p["mv"] or "", p["unrealised"] or "") for p in POS) + "\r\n")

ACCOUNT_DETAIL = {"account": ACCOUNTS[1], "cash": CASH_US, "buying_power": BP_US, "nav": NAV_US, "pnl": {"TODAY": PNL_US_TODAY, "MTD": pnl(ACCT, "MTD", "402361.50", "400278.00", "2083.50", "9.00", "2092.50"), "YTD": pnl(ACCT, "YTD", "402361.50", "0.00", "2361.50", "11.00", "2492.50", realised="-120.00"), "ITD": pnl(ACCT, "ITD", "402361.50", "0.00", "2361.50", "11.00", "2492.50", realised="-120.00")},
                  "positions_count": 6, "working_orders": 2, "pending_settlements": [{"settle_id": "st:f1:USD", "due_date": "$TODAY+1", "currency": "USD", "amount": "-169752.50", "leg": "cash", "fill_id": "f:1", "status": "PENDING"}, {"settle_id": "st:f2:USD", "due_date": "$TODAY+1", "currency": "USD", "amount": "76931.00", "leg": "cash", "fill_id": "f:2", "status": "PENDING"}, {"settle_id": "st:f4:USD", "due_date": "$TODAY+1", "currency": "USD", "amount": "-4753.25", "leg": "cash", "fill_id": "f:4", "status": "PENDING"}],
                  "borrows": [POS[1]["borrow"]], "loans": [], "margin": {"futures_required": "15000.00", "futures_posted": "15000.00", "options_required": "0.00", "options_posted": "0.00", "otc_im": "0.00", "otc_vm": "0.00"}, "collateral": {"held_at_lender": "78470.64"}, "sweeps_today": [], "attention": [ATTENTION[2]], "marks_partial": True, "as_of": NOW}
write("accounts_detail", ACCOUNT_DETAIL)
write("accounts_cash", {"account_id": ACCT, "account": ACCOUNTS[1], "cash": CASH_US, "buying_power": BP_US, "transfers": [{"transfer_id": "t:1", "ts": "$NOW-86000", "from_account": "P2:TREASURY", "to_account": ACCT, "currency": "USD", "amount": "400000.00", "memo": "advance", "kind": "ADVANCE"}],
                        "ladder": ACCOUNT_DETAIL["pending_settlements"], "loans": [], "borrows": ACCOUNT_DETAIL["borrows"], "margin": ACCOUNT_DETAIL["margin"], "collateral": ACCOUNT_DETAIL["collateral"], "sweeps_today": [], "as_of": NOW})
write("accounts_cash_foreign_fx", {"account_id": "P2:FOREIGN_FX", "account": ACCOUNTS[4], "cash": {"USD": {"account_id": "P2:FOREIGN_FX", "currency": "USD", "settled": "50000.00", "receivable": "0.00", "payable": "11240.00", "pending": "-11240.00", "held_at_lender": "0.00", "margin": "0.00", "otc_margin": "0.00", "reserved": "0.00", "accrued": "0.00", "available": "38760.00"},
                                                                                                  "EUR": {"account_id": "P2:FOREIGN_FX", "currency": "EUR", "settled": "0.00", "receivable": "10000.00", "payable": "0.00", "pending": "10000.00", "held_at_lender": "0.00", "margin": "0.00", "otc_margin": "0.00", "reserved": "0.00", "accrued": "0.00", "available": "10000.00"}},
                                   "ladder": [{"settle_id": "st:fx:1:USD", "due_date": "$TODAY+2", "currency": "USD", "amount": "-11240.00", "leg": "sell", "fx_deal_id": "fx:1", "status": "PENDING"}, {"settle_id": "st:fx:1:EUR", "due_date": "$TODAY+2", "currency": "EUR", "amount": "10000.00", "leg": "buy", "fx_deal_id": "fx:1", "status": "PENDING"}],
                                   "loans": [], "borrows": [], "margin": {"futures_required": "0.00", "futures_posted": "0.00", "options_required": "0.00", "options_posted": "0.00", "otc_im": "0.00", "otc_vm": "0.00"}, "collateral": {"held_at_lender": "0.00"}, "sweeps_today": [], "as_of": NOW})
write("accounts_position_detail", {"position": POS[0], "lots": POS[0]["lots"], "fills": [FILLS[0]], "entries": [TXNS[1]], "borrow": None, "hedge": None,
                                   "marks_history": [{"instrument_id": "AAPL", "mark_date": "$TODAY-1", "kind": "CLOSE", "price": "342.94", "currency": "USD", "quality": "close", "source": "Yahoo Finance daily close", "method": "daily_close", "quote_time": "$NOW-90000", "fetched_at": "$NOW-89000", "note": None},
                                                     {"instrument_id": "AAPL", "mark_date": "$TODAY-2", "kind": "CLOSE", "price": "340.10", "currency": "USD", "quality": "close", "source": "Yahoo Finance daily close", "method": "daily_close", "quote_time": "$NOW-176400", "fetched_at": "$NOW-175000", "note": None}]})


def mrow(iid, sym, name, kind, q, held=None, support="FULL", group=None, ccy="USD"):
    r = {"instrument_id": iid, "symbol": sym, "name": name, "kind": kind, "currency": ccy, "support": support, "quote": q, "position_qty": held, "held": bool(held)}
    if group:
        r["group"] = group
    return r


EQ_ROWS = [mrow("AAPL", "AAPL", "Apple Inc.", "EQUITY", Q["AAPL"], 500), mrow("MSFT", "MSFT", "Microsoft Corp.", "EQUITY", Q["MSFT"]), mrow("NVDA", "NVDA", "NVIDIA Corp.", "EQUITY", Q["NVDA"]),
           mrow("SPY", "SPY", "SPDR S&P 500 ETF", "ETF", Q["SPY"], -100), mrow("TSLA", "TSLA", "Tesla, Inc.", "EQUITY", Q["TSLA"], 100), mrow("XYZ", "XYZ", "XYZ Holdings (delisted)", "EQUITY", Q["XYZ"], 10),
           mrow("BTC-USD", "BTC-USD", "Bitcoin (crypto)", "CRYPTO", None, support="NOT_IN_THIS_BUILD")]
TABS_US = [{"key": "equities", "label": "Equities & ETFs"}, {"key": "options", "label": "Options underlyings"}, {"key": "futures", "label": "Futures"}]
write("accounts_markets", {"account_id": ACCT, "kind": "TRADING", "role": "US_EXCHANGE", "tab": "equities", "tabs": TABS_US, "rows": EQ_ROWS, "note": "watchlist + held names; Enter opens the workspace, b / s the ticket"})
write("accounts_markets_options", {"account_id": ACCT, "kind": "TRADING", "role": "US_EXCHANGE", "tab": "options", "tabs": TABS_US, "rows": [mrow("AAPL", "AAPL", "Apple Inc. (chain: 48 expiries)", "EQUITY", Q["AAPL"], 500), mrow("SPY", "SPY", "SPDR S&P 500 ETF (chain: 120 expiries)", "ETF", Q["SPY"], -100), mrow("^SPX", "SPX", "S&P 500 index options (Cboe _SPX)", "INDEX", quote("^SPX", "7712.16", prev="7690.10", source="Cboe delayed quotes", delay_label="~15 min delayed", group="US_INDEX_OPTIONS"))], "note": "underlyings with a Cboe chain; open the workspace for the chain"})
write("accounts_markets_futures", {"account_id": ACCT, "kind": "TRADING", "role": "US_EXCHANGE", "tab": "futures", "tabs": TABS_US, "groups": True, "rows": [mrow("ESZ26.CME", "ESZ26", "E-mini S&P 500 Dec 2026", "FUTURE", Q["ESZ26.CME"], 1, group="CME_EQUITY_INDEX"), mrow("NQZ26.CME", "NQZ26", "E-mini Nasdaq-100 Dec 2026", "FUTURE", quote("NQZ26.CME", "26510.75", prev="26480.00", group="CME_EQUITY_INDEX", age=280), group="CME_EQUITY_INDEX"), mrow("CLX26.NYM", "CLX26", "Crude Oil Nov 2026", "FUTURE", quote("CLX26.NYM", "101.09", prev="103.66", group="NYMEX_ENERGY", age=900), group="NYMEX_ENERGY"), mrow("ZNZ26.CBT", "ZNZ26", "10-Year T-Note Dec 2026", "FUTURE", quote("ZNZ26.CBT", "109.21875", prev="109.40625", group="CME_RATES", age=350), group="CME_RATES")], "note": "front months by group; margins are a static table (FUT_MARGIN)"})
write("accounts_markets_credit", {"account_id": "P2:CREDIT", "kind": "TRADING", "role": "CREDIT", "tab": "loans", "tabs": [{"key": "loans", "label": "Cash loans"}, {"key": "borrows", "label": "Securities lending"}, {"key": "rates", "label": "Rates"}], "rows": [],
                                  "sections": [{"key": "loans", "label": "Cash loans", "rows": [], "empty": "no cash loans in this account (draw one from Cash & financing)", "columns": ["loan_id", "currency", "principal", "index", "spread", "opened_date"]},
                                               {"key": "borrows", "label": "Securities lending (borrows)", "rows": [{"borrow_id": "b:1", "account_id": ACCT, "instrument_id": "SPY", "qty": 100, "fee_rate": "0.0030", "tier": "GC", "collateral": "78470.64", "lender": "modelled lender"}], "columns": ["borrow_id", "account_id", "instrument_id", "qty", "fee_rate", "tier", "collateral", "lender"]},
                                               {"key": "rates", "label": "Policy rates (FRED)", "rows": [{"currency": "USD", "index": "SOFR", "value": "3.87", "as_of": "2026-10-19"}, {"currency": "EUR", "index": "ECBDFR", "value": "2.00", "as_of": "2026-10-19"}, {"currency": "GBP", "index": "IUDSOIA", "value": "3.95", "as_of": "2026-10-19"}, {"currency": "JPY", "index": "IRSTCI01JPM156N (monthly proxy)", "value": "0.75", "as_of": "2026-09"}], "columns": ["currency", "index", "value", "as_of"], "note": "latest published observation on or before the accrual day (RATE_LAG)"}]})
write("accounts_markets_foreign_fx", {"account_id": "P2:FOREIGN_FX", "kind": "TRADING", "role": "FOREIGN_FX", "tab": "fx", "tabs": [{"key": "fx", "label": "FX spot"}, {"key": "listings", "label": "Foreign listings", "state": "NOT_IN_THIS_BUILD", "note": "resolve refuses non-US exchanges in this build"}],
                                      "rows": [mrow("EURUSD", "EUR/USD", "Euro / US dollar", "FX", Q["EURUSD"]), mrow("GBPUSD", "GBP/USD", "Pound / US dollar", "FX", quote("GBPUSD", None, bid="1.3393", ask="1.3395", prev="1.3370", group="FX_SPOT", age=100)), mrow("USDJPY", "USD/JPY", "US dollar / Yen", "FX", quote("USDJPY", None, bid="158.09", ask="158.11", prev="157.80", group="FX_SPOT", age=140))], "note": "mid from Yahoo (EURUSD=X ...); deals from Cash & financing at mid +/- 0.2 pip"})
write("accounts_markets_debt", {"account_id": "P2:DEBT", "kind": "TRADING", "role": "DEBT", "tab": "treasuries", "tabs": [{"key": "treasuries", "label": "Treasury benchmarks"}, {"key": "corporates", "label": "Curated corporates"}],
                                "curve": {"par": [{"tenor": "1M", "yield": "4.05"}, {"tenor": "3M", "yield": "4.02"}, {"tenor": "6M", "yield": "3.98"}, {"tenor": "1Y", "yield": "3.90"}, {"tenor": "2Y", "yield": "3.95"}, {"tenor": "5Y", "yield": "4.40"}, {"tenor": "10Y", "yield": "5.09"}, {"tenor": "30Y", "yield": "5.45"}], "as_of": "2026-10-19", "quality": "close", "source": "Treasury.gov daily par yield curve"},
                                "rows": [mrow("UST10Y", "T 4.25 08/36", "US Treasury 4.25 % Aug-2036", "BOND", Q["UST10Y"], 100, support="LIMITED"), mrow("UST2Y", "T 3.875 09/28", "US Treasury 3.875 % Sep-2028", "BOND", quote("UST2Y", "99.78", quality="computed", method="curve", source="Treasury.gov par curve (model)", delay_s=0, delay_label="daily par curve", age=25000, group="US_BONDS"), support="LIMITED")],
                                "note": "LIMITED: model price from the par curve, not executable in this build"})
write("accounts_markets_pb_gs", {"account_id": "P2:PB_GS", "kind": "PB", "role": "PB_GS", "tab": "equities", "tabs": TABS_US + [{"key": "otc", "label": "OTC (swaps)"}], "rows": EQ_ROWS[:4], "note": "DVP trading of the US set through Goldman Sachs; fees per the PB schedule"})
write("accounts_markets_pb_gs_otc", {"account_id": "P2:PB_GS", "kind": "PB", "role": "PB_GS", "tab": "otc", "tabs": TABS_US + [{"key": "otc", "label": "OTC (swaps)"}], "rows": [], "note": "IRS / TRS / XCCY dealt at the model par rate +/- the PB's half-width; daily VM and resets"})

INSTRUMENT = {"instrument": {"instrument_id": "AAPL", "symbol": "AAPL", "name": "Apple Inc.", "kind": "EQUITY", "currency": "USD", "multiplier": "1", "support": "FULL", "status": "ACTIVE", "optionable": True, "session_group": "US_EQUITY", "exchange": "NMS", "cik": 320193},
              "quote": Q["AAPL"], "mark": mark("AAPL", "341.10"), "session": SESSIONS["US_EQUITY"], "support": "FULL", "optionable": True, "spec": None,
              "positions": [{"account_id": ACCT, "qty": 500, "avg_px": "339.5000", "mv": "170550.00"}], "working_orders": [OPEN_ORDERS[1]]}
write("instrument", INSTRUMENT)
# deterministic 1-minute bars around 339-341 (fixture data for the chart; not a market simulation - the browser check needs a line to draw)
bars = []
px = Decimal("339.20")
for i in range(45):
    step = Decimal((i * 7) % 11 - 5) / 100
    px = px + step
    bars.append({"t": "$NOW-%d" % ((45 - i) * 60), "o": str(px - Decimal("0.05")), "h": str(px + Decimal("0.12")), "l": str(px - Decimal("0.11")), "c": str(px), "v": 20000 + ((i * 7919) % 50000)})
write("instrument_bars", {"instrument_id": "AAPL", "bars": bars, "prev_close": "342.94", "session": SESSIONS["US_EQUITY"], "source": "Yahoo Finance v8 chart", "delay_label": "up to 15 min delayed", "n": len(bars)})


def leg(iid, bid, ask, iv, delta, oi, vol, kind):
    return {"instrument_id": iid, "bid": bid, "ask": ask, "bid_size": 12, "ask_size": 40, "iv": iv, "delta": delta, "oi": oi, "volume": vol, "last": None, "quality": "delayed", "quote_time": "$NOW-400", "fetched_at": "$NOW-30", "source": "Cboe delayed quotes", "method": "chain", "delay_label": "~15 min delayed", "kind": kind}


rows = []
for k, strike in enumerate(["315", "320", "325", "330", "335", "340", "345", "350", "355", "360"]):
    s = int(strike)
    cb = Decimal(max(341 - s, 0)) + Decimal("4.20") - Decimal(k) * Decimal("0.35")
    pb = Decimal(max(s - 341, 0)) + Decimal("3.60") + Decimal(k) * Decimal("0.40")
    rows.append({"strike": strike + ".00", "call": leg("AAPL 270115C00%03d000" % s, str(cb), str(cb + Decimal("0.15")), "0.2650", "%.2f" % (0.95 - k * 0.07), 1200 + k * 310, 40 + k * 7, "call"),
                 "put": leg("AAPL 270115P00%03d000" % s, str(pb), str(pb + Decimal("0.10")), "0.2710", "%.2f" % (-0.05 - k * 0.07), 900 + k * 260, 30 + k * 5, "put")})
write("instrument_chain", {"underlying": Q["AAPL"], "expiries": ["2026-11-20", "2026-12-18", "2027-01-15", "2027-03-19", "2027-06-18"], "expiry": "2027-01-15", "rows": rows, "as_of": "$NOW-400", "delay_label": "Cboe delayed (~15 min)", "quality": "delayed", "reduced": "strikes within 35 % of spot, expiries <= 400 days"})
write("instrument_chain_fetching", {"$status": 202, "$body": {"status": "fetching", "retry_ms": 1500}})
write("instruments_search", [{"instrument_id": "AAPL", "symbol": "AAPL", "name": "Apple Inc.", "kind": "EQUITY", "currency": "USD", "support": "FULL", "quote": Q["AAPL"]}, {"instrument_id": "MSFT", "symbol": "MSFT", "name": "Microsoft Corp.", "kind": "EQUITY", "currency": "USD", "support": "FULL", "quote": Q["MSFT"]}, {"instrument_id": "BTC-USD", "symbol": "BTC-USD", "name": "Bitcoin (crypto) - not in this build", "kind": "CRYPTO", "currency": "USD", "support": "NOT_IN_THIS_BUILD", "quote": None}])
write("instruments_resolve", INSTRUMENT["instrument"])
write("instruments_resolve_resolving", {"$status": 202, "$body": {"status": "resolving", "retry_ms": 1500}})
write("instruments_resolve_unsupported", {"$status": 400, "$body": {"error": {"code": "UNSUPPORTED", "message": "foreign listings and crypto are not in this build", "details": {"symbol": "BTC-USD"}}, "request_id": None}})
QUOTES = {k: v for k, v in Q.items()}
write("quotes", {"quotes": QUOTES, "sessions": SESSIONS, "server_time": NOW})
qs = json.loads(json.dumps(QUOTES))
for k, v in qs.items():
    if v.get("quality") in ("delayed", "close"):
        v["quality"] = "stale"; v["quote_time"] = "$NOW-2000"; v["age_s"] = 2000; v["fetched_at"] = "$NOW-1900"; v["error"] = "HTTP 503 Service Unavailable"
write("quotes_provider_down", {"quotes": qs, "sessions": SESSIONS, "server_time": NOW})
write("curve", {"par": [{"tenor": t, "yield": y} for t, y in [("1M", "4.05"), ("3M", "4.02"), ("6M", "3.98"), ("1Y", "3.90"), ("2Y", "3.95"), ("3Y", "4.10"), ("5Y", "4.40"), ("7Y", "4.80"), ("10Y", "5.09"), ("20Y", "5.40"), ("30Y", "5.45")]], "as_of": "2026-10-19", "shifts": [-50, -25, 0, 25, 50], "quality": "close", "source": "Treasury.gov daily par yield curve XML", "fetched_at": "$NOW-25000"})
write("rates", {"SOFR": {"value": "3.87", "as_of": "2026-10-19", "source": "FRED SOFR"}, "policy": {"USD": {"index": "SOFR", "value": "3.87", "as_of": "2026-10-19"}, "EUR": {"index": "ECBDFR", "value": "2.00", "as_of": "2026-10-19"}, "GBP": {"index": "IUDSOIA", "value": "3.95", "as_of": "2026-10-19"}, "JPY": {"index": "IRSTCI01JPM156N", "value": "0.75", "as_of": "2026-09", "note": "monthly proxy"}}, "oas": {"BAMLC0A0CM": {"value": "0.92", "as_of": "2026-10-19"}, "BAMLH0A0HYM2": {"value": "3.10", "as_of": "2026-10-19"}}})
write("credit_locate", {"instrument_id": "SPY", "qty": 100, "qty_available": 100000, "fee_rate": "0.0030", "rebate_rate": "0.0337", "tier": "GC", "lender": "modelled lender", "collateral": "78470.64", "valid_until": "$NOW+300", "basis": tables.HTB["GC"]["rule"]})
write("credit_borrows", {"borrow": POS[1]["borrow"], "collateral_transfer": {"transfer_id": "t:9", "amount": "78470.64"}})
write("credit_borrow_return", {"borrow": dict(POS[1]["borrow"], qty_returned=100, status="CLOSED")})
write("credit_loans", {"loan": {"loan_id": "ln:1", "account_id": "P2:CREDIT", "kind": "CASH_LOAN", "currency": "EUR", "principal": "100000.00", "index_name": "ECBDFR", "spread": "0.0075", "day_basis": 360, "opened_date": TODAY, "maturity_date": None, "lender": "bank (modelled)", "status": "OPEN"}, "rate": {"index": "ECBDFR", "as_of": "2026-10-19", "value": "0.0200", "spread": "0.0075"}})
write("credit_loan_repay", {"loan": {"loan_id": "ln:1", "status": "CLOSED"}, "paid_interest": "15.28", "paid_principal": "100000.00"})
write("credit_pb", {"account": ACCOUNTS[-1], "cash": {"USD": {"settled": "150000.00", "available": "150000.00"}}})
write("fx_preview", {"mid": "1.1238", "rate": "1.1240", "buy": "10000.00", "sell": "11240.00", "spread_cost": "2.00", "value_date": "$TODAY+2", "quote": Q["EURUSD"], "legs": [{"currency": "EUR", "amount": "10000.00", "side": "BUY"}, {"currency": "USD", "amount": "-11240.00", "side": "SELL"}]})
write("fx_spot", {"deal": {"deal_id": "fx:2", "account_id": "P2:FOREIGN_FX", "buy_ccy": "EUR", "sell_ccy": "USD", "buy_amount": "10000.00", "sell_amount": "11240.00", "rate": "1.1240", "mid": "1.1238", "value_date": "$TODAY+2", "ts": NOW}, "settlements": [{"settle_id": "st:fx:2:EUR", "currency": "EUR", "amount": "10000.00", "due_date": "$TODAY+2"}, {"settle_id": "st:fx:2:USD", "currency": "USD", "amount": "-11240.00", "due_date": "$TODAY+2"}], "cash": {"USD": {"settled": "50000.00", "pending": "-22480.00"}}})
write("fx_forward", {"$status": 400, "$body": {"error": {"code": "NOT_IN_THIS_BUILD", "message": "FX forwards are Build 2 (covered interest parity from FRED rates)", "details": None}, "request_id": "ui:x"}})
write("options_exercise", {"fills": [{"fill_id": "f:ex1", "instrument_id": "AAPL", "side": "BUY", "qty": 500, "price": "315.00", "origin": "EXERCISE"}], "position": dict(POS[3], qty=0)})
write("accounting_pnl", pnl("ALL", "TODAY", "1002429.50", "1000348.00", "2081.50", "9.00", "2092.50", fx="-2.00"))
write("accounting_balance_sheet", {"scope": "ALL", "rows": [
    {"section": "assets", "label": "Cash settled", "gl": "1010", "currency": "USD", "native": "771650.11", "usd": "771650.11"},
    {"section": "assets", "label": "Receivables (sales, FX)", "gl": "1200/1250", "currency": "USD", "native": "76931.00", "usd": "76931.00"},
    {"section": "assets", "label": "Receivables (FX)", "gl": "1250", "currency": "EUR", "native": "10000.00", "usd": "11238.00", "quality": "delayed"},
    {"section": "assets", "label": "Accrued cash interest", "gl": "1230", "currency": "USD", "native": "12.50", "usd": "12.50"},
    {"section": "assets", "label": "Securities at market (long)", "gl": "1100/1110", "currency": "USD", "native": "210747.50", "usd": "210747.50", "quality": "stale"},
    {"section": "assets", "label": "at cost", "gl": "", "currency": "USD", "native": "204620.00", "usd": "204620.00", "sub": True},
    {"section": "assets", "label": "unrealised", "gl": "", "currency": "USD", "native": "1935.00", "usd": "1935.00", "sub": True},
    {"section": "assets", "label": "unpriced (excluded)", "gl": "", "currency": "USD", "native": "120.00", "usd": "120.00", "sub": True, "quality": "unavailable"},
    {"section": "assets", "label": "Options purchased at market", "gl": "1700", "currency": "USD", "native": "4875.00", "usd": "4875.00", "quality": "delayed"},
    {"section": "assets", "label": "Futures margin deposit", "gl": "1300", "currency": "USD", "native": "15000.00", "usd": "15000.00"},
    {"section": "assets", "label": "Futures open trade equity", "gl": "", "currency": "USD", "native": "987.50", "usd": "987.50", "quality": "delayed"},
    {"section": "assets", "label": "Collateral posted (held at lender)", "gl": "1400", "currency": "USD", "native": "78470.64", "usd": "78470.64"},
    {"section": "liabilities", "label": "Payables (purchases)", "gl": "2100", "currency": "USD", "native": "174505.75", "usd": "174505.75"},
    {"section": "liabilities", "label": "Payables (FX)", "gl": "2350", "currency": "USD", "native": "11240.00", "usd": "11240.00"},
    {"section": "liabilities", "label": "Securities sold short at market", "gl": "2600", "currency": "USD", "native": "76862.00", "usd": "76862.00", "quality": "close"},
    {"section": "nav", "label": "NAV", "gl": "", "currency": "USD", "native": "1002429.50", "usd": "1002429.50"}],
    "assets": "1265304.25", "liabilities": "262607.75", "nav": "1002429.50", "rounding": "0.00", "memo": {"capital": "1000000.00", "advances": "0.00", "fx_clearing": "0.00", "retained": "2429.50", "ties": True, "note": "advances net to zero across the lifetime"}, "quality": "stale", "partial": True, "as_of": NOW})
write("accounting_trial_balance", {"scope": "ALL", "rows": [{"gl": "1010", "name": "Cash settled", "currency": "USD", "debit": "771650.11", "credit": "0.00", "balance": "771650.11"}, {"gl": "1100", "name": "Equities at cost", "currency": "USD", "debit": "194870.00", "credit": "0.00", "balance": "194870.00"}, {"gl": "2600", "name": "Short securities at proceeds", "currency": "USD", "debit": "0.00", "credit": "76932.00", "balance": "-76932.00"}, {"gl": "3000", "name": "Contributed capital", "currency": "USD", "debit": "0.00", "credit": "1000000.00", "balance": "-1000000.00"}, {"gl": "5000", "name": "Fees", "currency": "USD", "debit": "16.00", "credit": "0.00", "balance": "16.00"}], "balanced": True, "totals": {"debit": "1316426.75", "credit": "1316426.75"}, "by_currency": {"USD": {"debit": "1305186.75", "credit": "1305186.75"}, "EUR": {"debit": "10000.00", "credit": "10000.00"}}})
write("accounting_journal", {"entries": TXNS, "total": len(TXNS), "limit": 300, "offset": 0})
write("accounting_journal_entry", dict(TXNS[1], command={"command_id": 5, "kind": "order.match", "request_id": "ui:cmd-5-aaaa", "actor": "matcher", "ts": "$NOW-7000"}, linked={"fill": FILLS[0], "order": HIST_ORDERS[0]}))
write("accounting_integrity", {"ok": True, "hold": False, "mode": "ledger", "checked_at": NOW, "lifetime_id": "P2", "duration_ms": 41,
                               "checks": [{"code": "L%d" % i, "name": n, "ok": True, "severity": "ERROR", "count": 0, "message": ""} for i, n in [(1, "every entry balances per currency"), (2, "every entry balances in base"), (3, "gl_balances equals the journal projection"), (4, "cash per account/currency equals settled + pending"), (5, "position quantity equals fills net of expiries/exercises"), (6, "command result stored"), (8, "Treasury = capital + retained - advances"), (12, "every short has a live borrow"), (20, "no duplicate idem keys")]] +
                                         [{"code": "M1", "name": "P&L residual is zero", "ok": True, "severity": "WARN", "count": 0, "message": "marks partial (XYZ): residual checked on priced positions"}, {"code": "M2", "name": "NAV identity to the cent", "ok": True, "severity": "WARN", "count": 0, "message": ""}], "problems": []})
write("risk", {"scope": "ALL", "as_of": NOW, "note": "warnings only - this page informs; funding, borrow and recall mechanics still apply", "gross": "1072167.50", "long": "995305.50", "short": "76862.00", "net": "918443.50", "dv01": "8.90", "excess_liquidity": "662835.36", "encumbrance": "93470.64",
               "greeks": {"delta": "-1575.00", "gamma": "12.40", "vega": "310.00", "theta": "-18.50"},
               "exposures": [{"bucket": "US equities", "long": "196060.00", "short": "76862.00", "net": "119198.00", "gross": "272922.00", "pct_nav": "0.1189"}, {"bucket": "Index futures (notional)", "long": "387712.50", "short": "0.00", "net": "387712.50", "gross": "387712.50", "pct_nav": "0.3867"}, {"bucket": "Listed options (delta USD)", "long": "0.00", "short": "1575.00", "net": "-1575.00", "gross": "1575.00", "pct_nav": "0.0016"}, {"bucket": "US Treasuries", "long": "9812.50", "short": "0.00", "net": "9812.50", "gross": "9812.50", "pct_nav": "0.0098"}, {"bucket": "FX (EUR)", "long": "11238.00", "short": "0.00", "net": "11238.00", "gross": "11238.00", "pct_nav": "0.0112"}],
               "scenarios": [{"name": "equities -10 %", "shock": "-10 %", "pnl": "-50629.25", "note": "futures notional included; private marks not shocked"}, {"name": "equities +10 %", "shock": "+10 %", "pnl": "50629.25"}, {"name": "rates +100 bp", "shock": "+100 bp", "pnl": "-890.00", "note": "DV01 x 100"}, {"name": "USD +5 % vs EUR", "shock": "+5 %", "pnl": "-561.90"}],
               "concentration": [{"instrument_id": "ESZ26.CME", "symbol": "ESZ26", "mv": "387712.50", "pct_nav": "0.3867", "note": "notional"}, {"instrument_id": "AAPL", "symbol": "AAPL", "mv": "170550.00", "pct_nav": "0.1701"}, {"instrument_id": "SPY", "symbol": "SPY", "mv": "-76862.00", "pct_nav": "0.0767"}],
               "margin": [{"account_id": ACCT, "required": "15000.00", "posted": "15000.00", "headroom": "183830.36", "note": "futures IM (static table) vs available"}, {"account_id": "P2:PB_GS", "required": "0.00", "posted": "0.00", "headroom": "100000.00", "note": "PB financing switched on; limit 500,000.00"}],
               "private": {"pct_nav": "0.0000", "positions": []},
               "warnings": [{"code": "CONCENTRATION", "severity": "WARN", "message": "ESZ26 notional is 38.7 % of consolidated NAV", "account_id": ACCT}, {"code": "STALE_MARK", "severity": "WARN", "message": "TSLA is marked on a stale quote (23 min); its P&L may be off", "account_id": ACCT}, {"code": "UNPRICED", "severity": "WARN", "message": "XYZ has no valid mark; NAV excludes it", "account_id": ACCT}]})
write("compliance", [{"rule": "Reg T / FINRA 4210 margin", "status": "info", "threshold": "50 % initial", "message": "Personal book: futures IM and option margin are reserved from available cash; verify the current rule text"}, {"rule": "Pattern day trader (FINRA 4210(f)(8)(B))", "status": "n/a", "threshold": "USD 25,000 equity", "message": "0 day trades in the rolling 5 sessions"}, {"rule": "Reg SHO 203(b) locate", "status": "ok", "threshold": "locate before a short sale", "message": "SPY short backed by borrow b:1"}, {"rule": "Section 13(d)/(g) 5 %", "status": "n/a", "threshold": "5 % of a class", "message": "no single-name holding near 5 % (shares outstanding from SEC when available)"}])
write("activity", {"rows": [
    {"ts": "$NOW-1500", "ts_ny": None, "type": "attention", "severity": "WARN", "account_id": "P2:FOREIGN_FX", "instrument_id": None, "summary": "FUNDING_SHORTFALL: EUR 10,000 settlement needs USD 11,240.00 - Treasury sweep at 17:30", "ref_type": "attention", "ref_id": "1"},
    {"ts": "$NOW-2500", "ts_ny": None, "type": "order", "severity": "INFO", "account_id": ACCT, "instrument_id": "AAPL", "summary": "o:6 TRAILING_STOP SELL 200 AAPL accepted (trail 5.00)", "ref_type": "order", "ref_id": "o:6"},
    {"ts": "$NOW-3000", "ts_ny": None, "type": "order", "severity": "INFO", "account_id": ACCT, "instrument_id": "MSFT", "summary": "o:5 LIMIT BUY 200 MSFT @ 505.00 resting", "ref_type": "order", "ref_id": "o:5"},
    {"ts": "$NOW-3900", "ts_ny": None, "type": "rejection", "severity": "WARN", "account_id": ACCT, "instrument_id": "TSLA", "summary": "QUOTE_STALE: BUY 100 TSLA refused (quote 23 min old)", "ref_type": "rejection", "ref_id": "2"},
    {"ts": "$NOW-5500", "ts_ny": None, "type": "fill", "severity": "INFO", "account_id": ACCT, "instrument_id": "AAPL 270115P00315000", "summary": "f:4 BTO 5 AAPL 15JAN27 315 P @ 9.50 (Cboe ask)", "ref_type": "fill", "ref_id": "f:4"},
    {"ts": "$NOW-6000", "ts_ny": None, "type": "fill", "severity": "INFO", "account_id": ACCT, "instrument_id": "ESZ26.CME", "summary": "f:3 BUY 1 ESZ26 @ 7734.50; IM 15,000.00 posted", "ref_type": "fill", "ref_id": "f:3"},
    {"ts": "$NOW-6480", "ts_ny": None, "type": "borrow", "severity": "INFO", "account_id": ACCT, "instrument_id": "SPY", "summary": "b:1 borrow 100 SPY (GC 0.30 %), collateral 78,470.64 posted", "ref_type": "borrow", "ref_id": "b:1"},
    {"ts": "$NOW-6500", "ts_ny": None, "type": "fill", "severity": "INFO", "account_id": ACCT, "instrument_id": "SPY", "summary": "f:2 SELL SHORT 100 SPY @ 769.32 (Cboe bid)", "ref_type": "fill", "ref_id": "f:2"},
    {"ts": "$NOW-7000", "ts_ny": None, "type": "fill", "severity": "INFO", "account_id": ACCT, "instrument_id": "AAPL", "summary": "f:1 BUY 500 AAPL @ 339.50 (last, Yahoo)", "ref_type": "fill", "ref_id": "f:1"},
    {"ts": "$NOW-60000", "ts_ny": None, "type": "job", "severity": "INFO", "account_id": None, "instrument_id": None, "summary": "SNAPSHOT 17:45 ET: 6 accounts + ALL written (quality close)", "ref_type": "job", "ref_id": "snap:" + "$TODAY-1"},
    {"ts": "$NOW-86000", "ts_ny": None, "type": "transfer", "severity": "INFO", "account_id": ACCT, "instrument_id": None, "summary": "t:1 advance 400,000.00 from Treasury", "ref_type": "transfer", "ref_id": "t:1"}], "next_since": "$NOW-86000"})
write("jobs", {"jobs": [{"name": n, "last_run_ts": "$NOW-%d" % s, "last_key": k, "next_due": "$NOW+%d" % d, "failing": False} for n, s, k, d in [("SETTLE_DUE", 300, TODAY, 600), ("ACCRUE_DAILY", 60000, "$TODAY-1", 26400), ("VM", 60000, "$TODAY-1", 23000), ("COLLATERAL_MARK", 60000, "$TODAY-1", 26000), ("SWEEP_OUT", 60000, "$TODAY-1", 27500), ("SNAPSHOT", 60000, "$TODAY-1", 28400), ("MARKS_RECORD", 60000, "$TODAY-1", 28400), ("HOUSEKEEPING", 3600, TODAY, 82800)]]})
write("jobs_run", {"ran": ["SETTLE_DUE"]})
write("treasury", {"treasury": {"lifetime_id": "P2", "account_id": "P2:TREASURY", "cash_by_ccy": {"USD": "435000.00"}, "accrued": "12.50", "capital": "1000000.00", "capital_by_ccy": {"USD": "1000000.00"}, "advances": "565000.00",
                                "advances_by_account": {ACCT: "400000.00", "P2:DEBT": "15000.00", "P2:CREDIT": "0.00", "P2:FOREIGN_FX": "50000.00", "P2:PB_GS": "100000.00"}, "retained": "12.50", "available": "435000.00", "reserve": "0.00", "nav": "435012.50", "encumbrance": "0.00", "identity_ok": True,
                                "formulas": {"nav": "settled + accrued (Treasury holds no positions)", "retained": "Treasury NAV - capital + advances", "available": "settled USD - reserve", "identity": "NAV = capital + retained - advances"}},
                   "advances": [{"account_id": ACCT, "name": "US Exchange", "amount": "400000.00"}, {"account_id": "P2:DEBT", "name": "Debt Instruments", "amount": "15000.00"}, {"account_id": "P2:FOREIGN_FX", "name": "Foreign Markets & FX", "amount": "50000.00"}, {"account_id": "P2:PB_GS", "name": "Goldman Sachs (PB)", "amount": "100000.00"}],
                   "capital_history": [{"ts": "$NOW-432000", "kind": "initial capital", "currency": "USD", "amount": "1000000.00", "memo": "init:capital:P2", "request_id": "init:capital:P2"}],
                   "sweep_log": [{"ts": "$NOW-86400", "account_id": "P2:DEBT", "direction": "SWEEP_OUT", "currency": "USD", "amount": "5000.00", "reason": "idle settled cash above needs + buffer"}],
                   "interest": {"index": "SOFR", "rate": "0.0337", "as_of": "2026-10-19", "accrued": "12.50", "paid_mtd": "0.00", "formula": "SOFR - 0.50 % (floor 0) on idle USD, ACT/360"}, "lifetime_id": "P2"})
write("treasury_deposit", {"transfer": {"transfer_id": "t:cap:2", "kind": "DEPOSIT", "currency": "USD", "amount": "100000.00", "memo": "owner deposit", "ts": NOW}, "treasury": {"capital": "1100000.00", "available": "535000.00", "nav": "535012.50"}})
write("treasury_withdraw", {"transfer": {"transfer_id": "t:cap:3", "kind": "WITHDRAWAL", "currency": "USD", "amount": "50000.00", "ts": NOW}, "treasury": {"capital": "950000.00", "available": "385000.00", "nav": "385012.50"}})
write("transfers", {"transfer": {"transfer_id": "t:2", "from_account": "P2:TREASURY", "to_account": "P2:CREDIT", "currency": "USD", "amount": "25000.00", "ts": NOW}, "from_cash": {"USD": {"settled": "410000.00"}}, "to_cash": {"USD": {"settled": "25000.00"}}})
PORTFOLIO = {"scope": "ALL", "nav": {"scope": "ALL", "nav": "1002429.50", "assets": "1265304.25", "liabilities": "262607.75", "quality": "stale", "partial": True, "missing": ["XYZ"], "as_of": NOW, "cash": "771650.11", "pending": "-108814.75", "mv_long": "210747.50", "mv_short": "-76862.00", "unrealised": "2555.00", "fx_result": "-2.00", "capital": "1000000.00", "transfers": "0.00", "realised": "0.00", "income": "0.00", "financing": "0.00", "fees": "16.00", "retained": "2429.50", "by_account": {"P2:TREASURY": "435012.50", ACCT: "402361.50", "P2:DEBT": "15057.50", "P2:CREDIT": "0.00", "P2:FOREIGN_FX": "49998.00", "P2:PB_GS": "100000.00"}, "lifetime_ids": ["P2"], "archived": False, "note": "NAV excludes 1 unpriced position (XYZ)"},
             "pnl": {"TODAY": pnl("ALL", "TODAY", "1002429.50", "1000348.00", "2081.50", "9.00", "2092.50", fx="-2.00"), "MTD": pnl("ALL", "MTD", "1002429.50", "1000348.00", "2081.50", "9.00", "2092.50", fx="-2.00"), "YTD": pnl("ALL", "YTD", "1002429.50", "1000000.00", "2429.50", "16.00", "2555.00", fx="-2.00", income="12.50", realised="-120.00"), "ITD": pnl("ALL", "ITD", "1002429.50", "1000000.00", "2429.50", "16.00", "2555.00", fx="-2.00", income="12.50", realised="-120.00")},
             "by_account": [{"account_id": "P2:TREASURY", "name": "Treasury", "nav": "435012.50", "pnl_today": "0.00", "weight": "0.4340"}, {"account_id": ACCT, "name": "US Exchange", "nav": "402361.50", "pnl_today": "2083.50", "weight": "0.4014"}, {"account_id": "P2:DEBT", "name": "Debt Instruments", "nav": "15057.50", "pnl_today": "0.00", "weight": "0.0150"}, {"account_id": "P2:CREDIT", "name": "Credit Instruments", "nav": "0.00", "pnl_today": "0.00", "weight": "0.0000"}, {"account_id": "P2:FOREIGN_FX", "name": "Foreign Markets & FX", "nav": "49998.00", "pnl_today": "-2.00", "weight": "0.0499"}, {"account_id": "P2:PB_GS", "name": "Goldman Sachs (PB)", "nav": "100000.00", "pnl_today": "0.00", "weight": "0.0998"}],
             "by_class": [{"klass": "securities", "count": 5, "mv": "129697.50", "unrealised": "1442.50", "day_pnl": "976.50", "weight": "0.1294"}, {"klass": "futures", "count": 1, "mv": "0.00", "unrealised": "987.50", "day_pnl": "985.25", "weight": "0.0000"}, {"klass": "listed options", "count": 1, "mv": "4875.00", "unrealised": "125.00", "day_pnl": "121.75", "weight": "0.0049"}],
             "positions": [dict(p, account_name="US Exchange") for p in POS] + [dict(POS_DEBT, account_name="Debt Instruments")], "footer": {"gross_long": "210747.50", "gross_short": "76862.00"}, "marks_partial": True, "as_of": NOW}
write("portfolio", PORTFOLIO)
PALL = json.loads(json.dumps(PORTFOLIO)); PALL["scope"] = "ALL_BOOKS"
PALL["positions"].append(dict(POS_PRIVATE, account_name="Private Markets (I1)"))
PALL["by_class"].append({"klass": "private / illiquid", "count": 1, "mv": "1230000.00", "unrealised": "50000.00", "day_pnl": "0.00", "weight": "0.0012"})
PALL["nav"].update({"scope": "ALL_BOOKS", "nav": "1026002429.50", "lifetime_ids": ["P2", "F1", "I1"], "note": "sum of the three open lifetimes; NAV excludes 1 unpriced position (XYZ)"})
PALL["by_account"] += [{"account_id": "F1:TREASURY", "name": "Treasury (F1)", "nav": "25000000.00", "pnl_today": "0.00", "weight": "0.0244"}, {"account_id": "I1:PRIVATE", "name": "Private Markets (I1)", "nav": "1230000.00", "pnl_today": "0.00", "weight": "0.0012"}]
write("portfolio_all_books", PALL)
write("portfolio_performance", {"status": "insufficient history", "needed": 2, "have": 1, "lifetime_id": "P2", "first_snapshot": "$TODAY-1", "last_snapshot": "$TODAY-1", "n": 1, "sofr": {"value": "3.87", "as_of": "2026-10-19"}, "note": "two daily snapshots are needed; the first was written yesterday 17:45 ET"})
HIST_NAV = {"scope": "P1:ALL", "nav": "812440.00", "assets": "812440.00", "liabilities": "0.00", "quality": "close", "partial": False, "missing": [], "as_of": "$NOW-432000", "cash": "812440.00", "pending": "0.00", "mv_long": "0.00", "mv_short": "0.00", "unrealised": "0.00", "fx_result": "0.00", "capital": "1000000.00", "transfers": "0.00", "realised": "-190120.00", "income": "2580.00", "financing": "0.00", "fees": "20.00", "retained": "-187560.00", "by_account": {"P1:TREASURY": "812440.00"}, "lifetime_ids": ["P1"], "archived": True, "note": "frozen at the reset; marks from the stored final snapshot"}
write("history_portfolio", {"scope": "P1:ALL", "read_only": True, "frozen_at": "2026-10-15", "lifetime": {"lifetime_id": "P1", "name": "Personal Book", "started": "2026-10-01", "ended": "2026-10-15", "reason": "owner reset (lifetime reset)", "starting_capital": "1000000.00", "final_nav": "812440.00", "final_pnl": "-187560.00"}, "nav": HIST_NAV,
                            "pnl": {"ITD": pnl("P1:ALL", "ITD", "812440.00", "1000000.00", "-187560.00", "20.00", "0.00", realised="-190120.00", income="2580.00")}, "by_account": [{"account_id": "P1:TREASURY", "name": "Treasury", "nav": "812440.00"}, {"account_id": "P1:US_EXCHANGE", "name": "US Exchange", "nav": "0.00"}], "by_class": [], "positions": [], "marks_partial": False, "as_of": "$NOW-432000"})
write("history_positions", [])
write("history_accounting_pnl", dict(pnl("P1:ALL", "ITD", "812440.00", "1000000.00", "-187560.00", "20.00", "0.00", realised="-190120.00", income="2580.00"), read_only=True, frozen_at="2026-10-15"))
write("history_accounting_balance_sheet", {"scope": "P1:ALL", "read_only": True, "frozen_at": "2026-10-15", "rows": [{"section": "assets", "label": "Cash settled", "gl": "1010", "currency": "USD", "native": "812440.00", "usd": "812440.00"}, {"section": "nav", "label": "NAV", "gl": "", "currency": "USD", "native": "812440.00", "usd": "812440.00"}], "assets": "812440.00", "liabilities": "0.00", "nav": "812440.00", "rounding": "0.00", "memo": {"capital": "1000000.00", "advances": "0.00", "fx_clearing": "0.00", "retained": "-187560.00", "ties": True}, "quality": "close", "partial": False, "as_of": "$NOW-432000"})
write("history_accounting_trial_balance", {"scope": "P1:ALL", "read_only": True, "rows": [{"gl": "1010", "name": "Cash settled", "currency": "USD", "debit": "812440.00", "credit": "0.00", "balance": "812440.00"}, {"gl": "3000", "name": "Contributed capital", "currency": "USD", "debit": "0.00", "credit": "1000000.00", "balance": "-1000000.00"}, {"gl": "4000", "name": "Realised P&L", "currency": "USD", "debit": "190120.00", "credit": "0.00", "balance": "190120.00"}, {"gl": "4200", "name": "Interest income", "currency": "USD", "debit": "0.00", "credit": "2580.00", "balance": "-2580.00"}, {"gl": "5000", "name": "Fees", "currency": "USD", "debit": "20.00", "credit": "0.00", "balance": "20.00"}], "balanced": True, "totals": {"debit": "1002580.00", "credit": "1002580.00"}})
write("history_accounting_journal", {"entries": [txn(1, 1641600, "capital", "initial capital 1,000,000.00", net={"USD": "1000000.00"}, ref=("capital", "init:capital:P1"))], "total": 1, "read_only": True})
write("history_accounting_journal_entry", dict(txn(1, 1641600, "capital", "initial capital 1,000,000.00", net={"USD": "1000000.00"}, ref=("capital", "init:capital:P1")), read_only=True))
write("history_accounting_integrity", {"ok": True, "hold": False, "mode": "full", "checked_at": "$NOW-432000", "lifetime_id": "P1", "checks": [{"code": "L1", "name": "every entry balances per currency", "ok": True, "severity": "ERROR", "count": 0, "message": ""}], "problems": [], "read_only": True})
write("history_activity", {"rows": [{"ts": "$NOW-432000", "ts_ny": None, "type": "lifetime", "severity": "INFO", "account_id": None, "instrument_id": None, "summary": "lifetime P1 closed: final NAV 812,440.00, P&L -187,560.00 (owner reset)", "ref_type": "lifetime", "ref_id": "P1"}, {"ts": "$NOW-500000", "ts_ny": None, "type": "fill", "severity": "INFO", "account_id": "P1:US_EXCHANGE", "instrument_id": "NVDA", "summary": "SELL 2,000 NVDA @ 95.06 (realised -190,120.00)", "ref_type": "fill", "ref_id": "f:p1:9"}], "read_only": True})
write("otc_preview", {"kind": "IRS", "par_rate": "0.0391", "dealt_rate": "0.0392", "half_width": "0.0001", "mark0": "-98.50", "dv01": "890.00", "im": "44500.00", "margin_mode": "CTM", "schedule": [{"date": "2027-01-20", "fixed": "97500.00", "float": "96750.00"}], "conventions": ["SWAP_MARK", "SWAP_VM", "SWAP_RESET"], "inputs": {"curve_as_of": "2026-10-19", "sofr": "3.87"}})
write("otc_contracts_post", {"contract": {"contract_id": "sw:1", "kind": "IRS", "notional": "10000000.00", "status": "OPEN"}, "im_transfer": {"amount": "44500.00"}, "entries": [21]})
write("otc_contracts", [])
write("otc_terminate", {"contract": {"contract_id": "sw:1", "status": "TERMINATED"}, "termination_value": "1250.00", "entries": [30]})
write("private_companies", [{"company_id": "PRIV:ANTHROPIC", "name": "Anthropic PBC", "sector": "AI", "country": "US", "status": "private", "ticker": None, "last_round": {"round_date": "2026-09-02", "round_type": "Series F", "post_money": "183000000000.00", "price_per_share": "61.50", "lead_investors": "ICONIQ, Fidelity, Lightspeed", "source_url": "https://www.anthropic.com/news/series-f", "as_of": "2026-09-02"}, "mark": PRIVATE_MARK, "source_url": "https://www.anthropic.com/news/series-f", "as_of": "2026-09-02"}])
write("private_companies_post", {"company_id": "PRIV:NEWCO", "name": "NewCo", "status": "private", "mark": None})
write("private_rounds_post", {"company_id": "PRIV:ANTHROPIC", "round": {"round_date": "2026-10-20", "round_type": "secondary", "price_per_share": "62.00", "source_url": "https://example.com/source"}, "mark": dict(PRIVATE_MARK, mark="62.00", note="last round 2026-10-20 (secondary)")})
write("private_preview", {"gross": "1230000.00", "fee": "36900.00", "net": "1266900.00", "settle_date": "$TODAY+30", "lock_until": "2027-04-20", "mark": PRIVATE_MARK, "warnings": [], "sentence": CONV["PRIVATE_TRADE"]})
write("private_trades", {"fill": {"fill_id": "f:p2", "qty": 20000, "price": "61.50", "origin": "NEGOTIATED"}, "settlement": {"due_date": "$TODAY+30", "amount": "-1266900.00"}, "lot": lot(9, "LONG", TODAY, 20000, "61.5000", "1230000.00", "f:p2", lock="2027-04-20")})
write("credit_private_post", {"loan": {"loan_id": "pl:1", "borrower": "NewCo", "currency": "USD", "principal": "5000000.00", "issue_price": "98.00", "index": "SOFR", "spread": "0.0650", "term_months": 36}, "mark": {"mark": "98.40", "quality": "computed", "source": "model: SOFR + FRED HY spread", "method": "pc_model", "inputs": {"sofr": "3.87", "hy_oas": "3.10"}}, "schedule": [{"date": "2027-01-20", "coupon": "131250.00"}]})
write("credit_private_action", {"loan": {"loan_id": "pl:1", "status": "OPEN"}, "entries": [40]})
write("credit_private", [])
write("dividends", [{"instrument_id": "AAPL", "ex_date": "2026-11-07", "amount": "0.26", "pay_date": "2026-11-14", "source": "Yahoo chart events", "convention": "cash on the pay date Yahoo gives", "position_qty": 500, "expected": "130.00"}])
write("unsupported", {"items": [
    {"item": "Private equity deal flow (buyouts), investment banking, clients / institutions / investors, careers / missions", "state": "NOT_IN_THIS_BUILD", "wording": "NOT IN THIS BUILD", "where": "nowhere in the terminal (TERMINAL_DECISIONS s5)"},
    {"item": "MBS / structured, exotic OTC (variance, vol, dividend, inflation swaps, barriers), commodity physicals, crypto", "state": "NOT_IN_THIS_BUILD", "wording": "grey rows wherever a list would show them; search says 'not in this build'", "where": "Markets lists, search"},
    {"item": "Bonds (Treasury benchmarks, curated corporates): execution", "state": "LIMITED", "wording": "model price, not executable in this build", "where": "Debt Instruments page (curve + list)"},
    {"item": "Foreign listings, foreign bonds", "state": "NOT_IN_THIS_BUILD", "wording": "resolve refuses non-US exchanges", "where": "Foreign Markets & FX page shows FX spot only"},
    {"item": "FX forwards, FX options", "state": "NOT_IN_THIS_BUILD", "wording": "fx.forward -> 400", "where": "hedge panel label for FX primaries"},
    {"item": "Repo / reverse repo, securities lending out of inventory", "state": "NOT_IN_THIS_BUILD", "wording": "Credit page sections present with the label", "where": "Credit Instruments page"},
    {"item": "PB OTC options and forwards (FX / equity options, FRAs, caps)", "state": "NOT_IN_THIS_BUILD", "wording": "/api/otc/preview -> 400 NOT_IN_THIS_BUILD; swaps are live", "where": "PB page OTC tab"},
    {"item": "Splits applied to lots", "state": "NOT_IN_THIS_BUILD", "wording": "a Yahoo split event raises an Attention WARN 'manual review'", "where": "positions row note"},
    {"item": "Recall notices from lender data", "state": "LIMITED", "wording": "INFO notice from the FINRA ratio heuristic only; no buy-ins ever", "where": "Attention INFO"},
    {"item": "Early assignment of short options", "state": "NOT_SIMULATED", "wording": "not simulated (OCC sentence)", "where": "ticket conventions"},
    {"item": "Options on futures", "state": "NOT_IN_THIS_BUILD", "wording": "hedge panel label for futures primaries", "where": "hedge panel"},
    {"item": "Live quotes", "state": "NONE", "wording": "everything is delayed, close or computed; 'live' is reserved", "where": "freshness on every price"},
    {"item": "VaR; shocks on private marks", "state": "NOT_IN_THIS_BUILD", "wording": "private marks are not shocked; no VaR", "where": "Risk table note"},
    {"item": "Phone / LAN access, access keys", "state": "NOT_IN_THIS_BUILD", "wording": "loopback only", "where": "-"}]})
write("watchlists", {"lists": [{"name": "core", "account_id": ACCT, "items": [{"instrument_id": "AAPL", "symbol": "AAPL", "name": "Apple Inc.", "position": 500, "quote": Q["AAPL"]}, {"instrument_id": "MSFT", "symbol": "MSFT", "name": "Microsoft Corp.", "position": 0, "quote": Q["MSFT"]}, {"instrument_id": "NVDA", "symbol": "NVDA", "name": "NVIDIA Corp.", "position": 0, "quote": Q["NVDA"]}, {"instrument_id": "SPY", "symbol": "SPY", "name": "SPDR S&P 500 ETF", "position": -100, "quote": Q["SPY"]}]},
                               {"name": "fx", "account_id": "P2:FOREIGN_FX", "items": [{"instrument_id": "EURUSD", "symbol": "EUR/USD", "name": "Euro / US dollar", "position": 0, "quote": Q["EURUSD"]}]}]})
print("wrote", len(os.listdir(OUT)), "files to", OUT)
