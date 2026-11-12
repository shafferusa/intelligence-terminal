"""The Smart Ticket corpus (FinSim 1.0.7.2, CHANGE_PLAN r1072 2.6 and deviation D-a): data only, no test logic.

``WORLD`` describes one fictional world - names and numbers are invented, nothing comes from the owner's books - that
``tests/test_terminal_smart_ticket.py`` builds in a temp home: the fictional equities ACME, WIDG, GIZM, ZORB (two rows
sharing one symbol), LOTX (a lot of 10) and Q (a one-letter ticker), a fictional bond and a fictional private name, three option
rows in the master, the cached chains of ACME (expiries 2026-12-18 and 2027-01-15, no 215 call on 2026-12-18, an adjusted root
ACME1 on the 2026-12-18 200 put), GIZM (stale: fetched 15 minutes ago) and SPX (SPX and SPXW on one 2026-12-18 contract), a prime
broker account in the Institutional book, a few positions, one working order and one quote.  The clock is Tuesday 2026-10-20 14:00
ET (``AT``): "Dec 18" is then 2026-12-18 and "Jan 15" 2027-01-15.  The futures roots and FX pairs are those of the test universe.

``chain_payload(...)`` builds a Cboe-shaped delayed-quotes payload (``{"data": {..., "options": [...]}}``) that
``quotes.parsers.parse_chain`` reads like the live file: every option's mid is its intrinsic value + 2.00 with a 0.05 half spread.

``CASES``: ``{id, text, book, setup, expect}``.  ``book`` is the selected book (body and ``?book=``); ``setup`` names the world and
may carry the body's default ``account_id``; ``expect`` is matched by the test: ``status`` (200 unless said), ``error`` (the code of
a 4xx), ``ok``, ``legs`` (a count or a list of leg checks: side, qty, order_type, limit_px, stop_px, trail_amt, trail_pct, tif,
gtd_date, book, account_id, combo, id = the instrument id, codes = flag codes the leg carries, request = True / False / the exact
body), ``combos`` (a count or a list: kind, legs, strategy, order, request, codes; since the 1.0.7.2 simplification a group is separate
tickets: ``kind`` "SEPARATE" and no request of its own), ``codes`` (flag codes somewhere in the answer),
``absent`` (codes nowhere in it) and ``top`` (instruction-level codes).  At least as many cases are refusals or ambiguities (``ok``
false or a 4xx) as clean ones.
"""
from __future__ import annotations

AT = "2026-10-20T14:00:00-04:00"
TRADE_TIME = "2026-10-20T13:59:00"          # the Cboe last-trade time (naive Eastern) of a fresh chain
STALE_TRADE_TIME = "2026-10-20T13:44:00"    # ... of the stale GIZM chain (fetched 15 minutes before AT)
DEC, JAN = "2026-12-18", "2027-01-15"
P, F, I = "P1:US_EXCHANGE", "F1:US_EXCHANGE", "I1:US_EXCHANGE"
PB = "I1:PB_MS"


def chain_payload(symbol, spot, expiries, strikes, trade_time, skip=(), extra=(), cboe_symbol=None):
    """A Cboe-shaped chain payload: every (expiry, right, strike) not in ``skip`` (``(expiry, "C" | "P", strike)``), each with
    bid / ask = intrinsic + 2.00 -/+ 0.05, sizes 10, then the ``extra`` raw option entries.  Deterministic (no clock)."""
    root = cboe_symbol or symbol
    spot_c = int(round(float(spot) * 100))
    options = []
    for e in expiries:
        yy, mm, dd = e[2:4], e[5:7], e[8:10]
        for k in strikes:
            k_c = int(round(float(k) * 100))
            for right in ("C", "P"):
                if (e, right, k) in skip:
                    continue
                intr = max(0, spot_c - k_c) if right == "C" else max(0, k_c - spot_c)
                mid = intr + 200
                options.append({"option": f"{root}{yy}{mm}{dd}{right}{k_c * 10:08d}", "bid": (mid - 5) / 100.0, "ask": (mid + 5) / 100.0,
                                "bid_size": 10, "ask_size": 10, "iv": 0.3, "delta": 0.5 if right == "C" else -0.5, "gamma": 0.01, "theta": -0.02,
                                "vega": 0.1, "open_interest": 100, "volume": 5, "last_trade_price": mid / 100.0, "last_trade_time": trade_time})
    options.extend(dict(o) for o in extra)
    return {"data": {"symbol": root, "current_price": float(spot), "close": float(spot), "iv30": 30.0, "bid": float(spot) - 0.01, "ask": float(spot) + 0.01,
                     "bid_size": 5, "ask_size": 5, "last_trade_time": trade_time, "prev_day_close": float(spot), "options": options}}


def _eq(iid, name, **kw):
    row = {"instrument_id": iid, "kind": "EQUITY", "symbol": iid, "name": name, "currency": "USD", "session_group": "US_EQUITY", "settle_days": 1,
           "account_kinds": ["US_EXCHANGE", "PB"], "cboe_symbol": iid, "support": "FULL"}
    row.update(kw)
    return row


def _opt(underlying, occ):
    root, ymd, right, k8 = occ[:-15], occ[-15:-9], occ[-9], occ[-8:]
    expiry = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}"
    strike = str(int(k8) // 1000) if int(k8) % 1000 == 0 else str(int(k8) / 1000)
    return {"instrument_id": occ, "kind": "OPTION", "symbol": occ, "name": f"{underlying} {expiry} {right}{strike}", "currency": "USD", "multiplier": "100",
            "tick": "0.01", "session_group": "US_EQUITY_OPTIONS", "settle_days": 1, "account_kinds": ["US_EXCHANGE", "PB"], "cboe_symbol": occ,
            "underlying_id": underlying, "expiry": expiry, "strike": strike, "right": right, "style": "AMERICAN", "exercise": "PHYSICAL",
            "spec": {"root": root, "index": False}, "support": "FULL"}


ACME_STRIKES = (200, 210, 215, 220, 230, 240, 250, 260)
WORLD = {
    "instruments": [
        _eq("ACME", "Acme Corp"), _eq("WIDG", "Widget Works"), _eq("GIZM", "Gizmo Labs"), _eq("Q", "Quux Corp"), _eq("LOTX", "Lotx Holdings", lot=10),
        _eq("ZORB", "Zorb Inc", cboe_symbol=None), _eq("ZORB-W", "Zorb Inc warrants", symbol="ZORB", cboe_symbol=None),
        {"instrument_id": "ACME-31", "kind": "BOND", "symbol": "ACME-31", "name": "Acme Corp 5% 2031", "currency": "USD", "multiplier": "0.01", "tick": "0.001",
         "lot": 1000, "session_group": "US_BONDS", "settle_days": 1, "account_kinds": [], "support": "LIMITED"},
        {"instrument_id": "PRV-WIDGLABS", "kind": "PRIVATE", "symbol": "PRV-WIDGLABS", "name": "Widget Labs", "currency": "USD", "session_group": "PRIVATE_MARKETS",
         "settle_days": 30, "account_kinds": ["PRIVATE"], "support": "COMPUTED"},
        _opt("ACME", "ACME261218C00240000"), _opt("ACME", "ACME261218C00250000"), _opt("WIDG", "WIDG261218P00040000"),
    ],
    "chains": [
        {"underlying": "ACME", "cboe": "ACME", "spot": 230, "expiries": (DEC, JAN), "strikes": ACME_STRIKES, "skip": ((DEC, "C", 215),), "age_s": 30,
         "trade_time": TRADE_TIME,
         "extra": ({"option": "ACME1261218P00200000", "bid": 0.5, "ask": 0.6, "bid_size": 5, "ask_size": 5, "last_trade_time": TRADE_TIME},)},
        {"underlying": "GIZM", "cboe": "GIZM", "spot": 50, "expiries": (DEC,), "strikes": (45, 50, 55), "skip": (), "age_s": 900, "trade_time": STALE_TRADE_TIME,
         "extra": ()},
        {"underlying": "SPX", "cboe": "_SPX", "root": "SPX", "spot": 6000, "expiries": (DEC,), "strikes": (5900, 6000, 6100), "skip": (), "age_s": 30,
         "trade_time": TRADE_TIME,
         "extra": ({"option": "SPXW261218C06000000", "bid": 80.0, "ask": 81.0, "bid_size": 5, "ask_size": 5, "last_trade_time": TRADE_TIME},
                   {"option": "SPXW261211C06000000", "bid": 70.0, "ask": 71.0, "bid_size": 5, "ask_size": 5, "last_trade_time": TRADE_TIME})},
    ],
    # a prime-broker account in the Institutional book only (the other books have one equity account: no ACCOUNT_CHOICE there)
    "accounts": [{"book": "I", "lifetime": "I1", "role": "PB_MS", "name": "Morgan Stanley PB", "kind": "PB", "broker": "MS", "sort": 60}],
    # (account, instrument, LONG | SHORT, qty, price): opening fills of the fixture
    "positions": [(P, "WIDG", "LONG", 300, "40"), (P, "GIZM", "SHORT", 200, "50"), (P, "ACME261218C00240000", "SHORT", 2, "3.10"),
                  (P, "ACME261218C00250000", "LONG", 3, "1.20"), (F, "ACME", "LONG", 100, "225"),
                  # the Institutional book holds these in its prime-broker account, not in US Exchange (the account follows the holding)
                  (PB, "GIZM", "SHORT", 50, "50"), (PB, "ACME261218C00250000", "LONG", 2, "1.20")],
    # a working sell of 100 WIDG in the Personal book: its free quantity is 200
    "working": [{"order_id": "O-SMT-1", "request_id": "test:smart-working-1", "account_id": P, "instrument_id": "WIDG", "side": "SELL", "qty": 100,
                 "order_type": "LIMIT", "limit_px": "45", "tif": "DAY", "status": "WORKING", "session_group": "US_EQUITY"}],
    "quotes": [{"instrument_id": "WIDG", "last": "40.10", "bid": "40.05", "ask": "40.15", "mid": "40.10", "age_s": 120}],
}


def _case(cid, text, expect, book="P", setup=None):
    return {"id": cid, "text": text, "book": book, "setup": dict({"world": "standard"}, **(setup or {})), "expect": expect}


def _req(account, iid, side, qty, order_type="MARKET", tif="DAY", **extra):
    out = {"account_id": account, "instrument_id": iid, "side": side, "qty": qty, "order_type": order_type, "tif": tif}
    out.update(extra)
    return out


C240, C250, C260, C230, C220, C210 = "ACME261218C00240000", "ACME261218C00250000", "ACME261218C00260000", "ACME261218C00230000", "ACME261218C00220000", "ACME261218C00210000"
P240, P250, P230, P220, P210 = "ACME261218P00240000", "ACME261218P00250000", "ACME261218P00230000", "ACME261218P00220000", "ACME261218P00210000"
J240C, J250C, J240P = "ACME270115C00240000", "ACME270115C00250000", "ACME270115P00240000"
LONG_TEXT = "buy 1 ACME limit 1.00 " + "gtc " * 495            # 2,002 characters

CASES = [
    # ---- clean: single orders --------------------------------------------------------------------------------------------------
    _case("clean-limit", "buy 200 ACME limit 225", {"ok": True, "legs": [
        {"side": "BUY", "qty": 200, "order_type": "LIMIT", "limit_px": "225", "tif": "DAY", "book": "P", "account_id": P, "id": "ACME",
         "codes": ["TIF_DEFAULT", "BOOK_SELECTED"], "request": _req(P, "ACME", "BUY", 200, "LIMIT", limit_px="225")}], "absent": ["TYPE_DEFAULT", "OTHER_BOOK"]}),
    _case("clean-at-gtc", "buy 200 ACME at 225 gtc", {"ok": True, "legs": [{"order_type": "LIMIT", "limit_px": "225", "tif": "GTC", "codes": ["TYPE_FROM_AT"]}],
                                                      "absent": ["TIF_DEFAULT"]}),
    _case("clean-market-default", "buy 100 ACME", {"ok": True, "legs": [{"side": "BUY", "qty": 100, "order_type": "MARKET", "codes": ["TYPE_DEFAULT"]}]}),
    # 1.0.7.2 simplified (INV-4): a short sale while the account holds the shares is refused (SHORT_WHILE_LONG, below), so the clean short
    # sales are typed in the Institutional book, which holds no WIDG
    _case("clean-short-borrow", "short 300 WIDG limit 41.20", {"ok": True, "legs": [
        {"side": "SELL_SHORT", "qty": 300, "limit_px": "41.20", "request": _req(I, "WIDG", "SELL_SHORT", 300, "LIMIT", limit_px="41.20", borrow=True)}]}, book="I"),
    _case("clean-sell-short-at", "sell short 100 WIDG at 41", {"ok": True, "legs": [{"side": "SELL_SHORT", "limit_px": "41", "codes": ["TYPE_FROM_AT"]}]}, book="I"),
    _case("clean-sell-free", "sell 200 WIDG limit 41.20", {"ok": True, "legs": [{"side": "SELL", "qty": 200, "limit_px": "41.20"}]}),
    _case("clean-caps", "BUY 100 ACME LIMIT 225 GTC", {"ok": True, "legs": [{"side": "BUY", "limit_px": "225", "tif": "GTC"}]}),
    _case("clean-mixed-case", "Buy 100 Acme limit 225", {"ok": True, "legs": [{"id": "ACME", "limit_px": "225"}]}),
    _case("clean-lower-ticker", "buy 100 acme", {"ok": True, "legs": [{"id": "ACME"}]}),
    _case("clean-forced-ticker", "buy 100 $acme", {"ok": True, "legs": [{"id": "ACME"}]}),
    _case("clean-at-sign", "buy 100 ACME @ 225.50", {"ok": True, "legs": [{"limit_px": "225.50", "codes": ["TYPE_FROM_AT"]}]}),
    _case("clean-dollar-commas", "buy 100 ACME limit $1,225.50", {"ok": True, "legs": [{"limit_px": "1225.50"}]}),
    _case("clean-k-qty", "buy 1.5k ACME", {"ok": True, "legs": [{"qty": 1500}]}),
    _case("clean-shares-after", "buy 100 ACME shares", {"ok": True, "legs": [{"qty": 100, "id": "ACME"}]}),
    _case("clean-shares-before", "buy 100 shares ACME", {"ok": True, "legs": [{"qty": 100, "id": "ACME"}]}),
    _case("clean-lot-multiple", "buy 20 LOTX", {"ok": True, "legs": [{"qty": 20, "id": "LOTX"}]}),
    _case("clean-one-letter-caps", "buy 100 Q", {"ok": True, "legs": [{"id": "Q"}]}),
    _case("clean-next-session", "buy 100 ACME next session", {"ok": True, "legs": [{"tif": "NEXT_SESSION"}]}),
    _case("clean-gtc-words", "buy 100 ACME good till cancelled", {"ok": True, "legs": [{"tif": "GTC"}]}),
    _case("clean-gtd-words", "buy 100 ACME good till Dec 18 2026", {"ok": True, "legs": [{"tif": "GTD", "gtd_date": DEC,
                                                                                            "request": _req(P, "ACME", "BUY", 100, tif="GTD", gtd_date=DEC)}]}),
    _case("clean-gtd-iso", "sell 100 WIDG gtd 2026-12-18", {"ok": True, "legs": [{"side": "SELL", "tif": "GTD", "gtd_date": DEC}]}),
    _case("clean-until-year-inferred", "sell 100 WIDG until Dec 18", {"ok": True, "legs": [{"tif": "GTD", "gtd_date": DEC, "codes": ["YEAR_INFERRED"]}]}),
    _case("clean-trail-pct", "buy 100 ACME trail 2%", {"ok": True, "legs": [{"order_type": "TRAILING_STOP", "trail_pct": "2", "trail_amt": None,
                                                                            "request": _req(P, "ACME", "BUY", 100, "TRAILING_STOP", trail_pct="2")}]}),
    _case("clean-trail-amt", "buy 100 ACME trailing stop 1.50", {"ok": True, "legs": [{"order_type": "TRAILING_STOP", "trail_amt": "1.50"}]}),
    _case("clean-stop-limit", "buy 100 ACME stop 240 limit 241", {"ok": True, "legs": [{"order_type": "STOP_LIMIT", "stop_px": "240", "limit_px": "241"}]}),
    _case("clean-two-orders-dots", "buy 100 ACME. sell 50 WIDG.", {"ok": True, "legs": [{"id": "ACME"}, {"id": "WIDG", "side": "SELL"}]}),
    _case("clean-empty-separators", "buy 100 ACME; ; ; sell 50 WIDG", {"ok": True, "legs": 2}),
    _case("clean-side-carried", "buy 100 ACME and 50 WIDG", {"ok": True, "legs": [{"side": "BUY", "id": "ACME"}, {"side": "BUY", "id": "WIDG", "codes": ["SIDE_CARRIED"]}]}),
    _case("clean-ampersand", "buy 100 ACME & 50 WIDG", {"ok": True, "legs": [{"id": "ACME"}, {"id": "WIDG", "codes": ["SIDE_CARRIED"]}]}),
    _case("clean-then", "buy 100 ACME then sell 50 WIDG", {"ok": True, "legs": [{"side": "BUY"}, {"side": "SELL"}]}),
    _case("clean-plus", "buy 100 ACME plus 50 WIDG", {"ok": True, "legs": [{"id": "ACME"}, {"id": "WIDG"}]}),
    _case("clean-duplicate-leg-check", "buy 100 ACME and buy 100 ACME", {"ok": True, "legs": [{"codes": ["DUPLICATE_LEG"]}, {"codes": ["DUPLICATE_LEG"]}]}),
    _case("clean-cover", "cover 100 GIZM", {"ok": True, "legs": [{"side": "BUY_TO_COVER", "qty": 100}], "absent": ["SIDE_INFERRED"]}),
    _case("clean-buy-to-cover", "buy to cover 100 GIZM", {"ok": True, "legs": [{"side": "BUY_TO_COVER"}]}),
    _case("clean-buy-covers-short", "buy 100 GIZM", {"ok": True, "legs": [{"side": "BUY_TO_COVER", "qty": 100, "codes": ["SIDE_INFERRED"]}]}),
    _case("clean-sell-half", "sell half WIDG", {"ok": True, "legs": [{"side": "SELL", "qty": 150}]}),
    _case("clean-close-short-all", "close all GIZM", {"ok": True, "legs": [{"side": "BUY_TO_COVER", "qty": 200, "codes": ["SIDE_INFERRED"]}]}),
    _case("clean-close-free", "close WIDG", {"ok": True, "legs": [{"side": "SELL", "qty": 200, "codes": ["SIDE_INFERRED"]}]}),
    # ---- clean: options -------------------------------------------------------------------------------------------------------------
    _case("clean-opt-btc-at-ask", "buy 2 ACME Dec 18 2026 240 calls at the ask", {"ok": True, "legs": [
        {"side": "BTC", "qty": 2, "order_type": "LIMIT", "limit_px": "2.05", "id": C240, "codes": ["PRICE_FROM_QUOTE", "SIDE_INFERRED"],
         "request": _req(P, C240, "BTC", 2, "LIMIT", limit_px="2.05")}]}),
    _case("clean-opt-btc", "buy 2 ACME Dec 18 2026 240 calls", {"ok": True, "legs": [{"side": "BTC", "qty": 2, "codes": ["SIDE_INFERRED", "TYPE_DEFAULT"]}]}),
    _case("clean-opt-stc", "sell 2 ACME 250c 12/18/26", {"ok": True, "legs": [{"side": "STC", "qty": 2, "id": C250, "codes": ["SIDE_INFERRED"]}]}),
    _case("clean-opt-sto", "sell 2 ACME 260c 12/18/26", {"ok": True, "legs": [{"side": "STO", "qty": 2, "id": C260, "codes": ["SIDE_INFERRED"]}]}),
    _case("clean-opt-bto", "buy 2x ACME Dec 18 2026 260 call", {"ok": True, "legs": [{"side": "BTO", "qty": 2, "id": C260}], "absent": ["SIDE_INFERRED"]}),
    _case("clean-opt-occ-typed", "buy 1 ACME261218C00240000", {"ok": True, "legs": [{"side": "BTC", "id": C240}]}),
    _case("clean-opt-master-row", "sell 1 WIDG261218P00040000", {"ok": True, "legs": [{"side": "STO", "id": "WIDG261218P00040000"}], "absent": ["OPTION_UNVERIFIED"]}),
    _case("clean-opt-master-terms", "buy 1 WIDG Dec 18 2026 40 put", {"ok": True, "legs": [{"side": "BTO", "id": "WIDG261218P00040000"}], "absent": ["OPTION_UNVERIFIED"]}),
    _case("clean-opt-strike-right", "buy 1 ACME 240C 2026-12-18", {"ok": True, "legs": [{"id": C240}]}),
    _case("clean-opt-ordinal", "buy 1 ACME Dec 18th 2026 240 call", {"ok": True, "legs": [{"id": C240}]}),
    _case("clean-opt-day-month", "buy 1 ACME 18 Dec 2026 240 call", {"ok": True, "legs": [{"id": C240}]}),
    _case("clean-opt-quote-year", "buy 1 ACME Dec 18 '26 240 call", {"ok": True, "legs": [{"id": C240}]}),
    _case("clean-opt-comma-year", "buy 1 ACME Dec 18, 2026 240 call", {"ok": True, "legs": [{"id": C240}]}),
    _case("clean-opt-comma-short-year", "buy 1 ACME Dec 18, 26 240 call", {"ok": True, "legs": [{"id": C240}]}),
    _case("clean-opt-year-inferred", "buy 1 ACME Dec 18 240 call", {"ok": True, "legs": [{"id": C240, "codes": ["YEAR_INFERRED"]}]}),
    _case("clean-opt-at-mid", "buy 1 ACME 2026-12-18 240 call at the mid", {"ok": True, "legs": [{"limit_px": "2", "codes": ["PRICE_FROM_QUOTE"]}]}),
    _case("clean-opt-explicit-stc", "stc 1 ACME Dec 18 2026 250 call", {"ok": True, "legs": [{"side": "STC"}], "absent": ["SIDE_INFERRED"]}),
    _case("clean-opt-explicit-btc-limit", "btc 1 ACME Dec 18 2026 240 call limit 2.5", {"ok": True, "legs": [{"side": "BTC", "limit_px": "2.5"}]}),
    _case("clean-opt-half", "sell half ACME Dec 18 2026 250 calls", {"ok": True, "legs": [{"side": "STC", "qty": 1, "codes": ["QTY_ROUNDED"]}]}),
    _case("clean-opt-all", "buy all ACME Dec 18 2026 240 calls", {"ok": True, "legs": [{"side": "BTC", "qty": 2}]}),
    _case("clean-opt-contracts", "buy 2 contracts ACME Dec 18 2026 240 call", {"ok": True, "legs": [{"side": "BTC", "qty": 2}]}),
    _case("clean-opt-month-only", "buy 1 ACME Dec 2026 240 call", {"ok": True, "legs": [{"id": C240, "codes": ["EXPIRY_FROM_MONTH"]}]}),
    _case("clean-opt-stale-chain", "buy 1 GIZM Dec 18 2026 50 call", {"ok": True, "legs": [{"id": "GIZM261218C00050000", "codes": ["CHAIN_STALE"]}]}),
    _case("clean-opt-unverified", "buy 1 WIDG Dec 18 2026 45 put", {"ok": True, "legs": [{"id": "WIDG261218P00045000", "codes": ["OPTION_UNVERIFIED"]}]}),
    _case("clean-opt-weekly-root", "buy 1 SPXW Dec 18 2026 6000 call", {"ok": True, "legs": [{"id": "SPXW261218C06000000"}], "absent": ["ROOT_CHOICE"]}),
    _case("clean-opt-one-root-date", "buy 1 SPX Dec 11 2026 6000 call", {"ok": True, "legs": [{"id": "SPXW261211C06000000"}], "absent": ["ROOT_CHOICE"]}),
    _case("clean-close-calls", "close ACME calls", {"ok": True, "legs": [{"side": "BTC", "qty": 2, "id": C240}, {"side": "STC", "qty": 3, "id": C250}]}),
    _case("clean-close-options", "close ACME options", {"ok": True, "legs": 2}),
    _case("clean-close-one", "close 1 ACME Dec 18 2026 250 call", {"ok": True, "legs": [{"side": "STC", "qty": 1}]}),
    # ---- clean: futures ----------------------------------------------------------------------------------------------------------------
    _case("clean-fut-month", "buy 2 ES Mar", {"ok": True, "legs": [{"side": "BUY", "qty": 2, "id": "ESH27", "codes": ["YEAR_INFERRED"]}]}),
    _case("clean-fut-month-year", "buy 2 ES Mar 2027", {"ok": True, "legs": [{"id": "ESH27"}], "absent": ["YEAR_INFERRED"]}),
    _case("clean-fut-front", "sell 1 front ES stop 6500", {"ok": True, "legs": [{"side": "SELL", "id": "ESZ26", "order_type": "STOP", "stop_px": "6500",
                                                                                 "codes": ["EXPIRY_FROM_MONTH"]}]}),
    _case("clean-fut-id", "buy 10 ESZ26 limit 6500.25", {"ok": True, "legs": [{"id": "ESZ26", "limit_px": "6500.25"}]}),
    _case("clean-fut-digit-root", "buy 1 6E Dec", {"ok": True, "legs": [{"id": "6EZ26", "codes": ["YEAR_INFERRED"]}]}),
    _case("clean-fut-lots", "buy 2 lots ESZ26", {"ok": True, "legs": [{"qty": 2, "id": "ESZ26"}]}),
    # ---- clean: combinations (1.0.7.2 simplified: groups of separate tickets, each order with its own request, sent in ``order``) ------
    _case("clean-call-spread", "buy 1 ACME Dec 18 2026 240/250 call spread", {"ok": True, "legs": [
        {"side": "BTO", "qty": 1, "id": C240, "combo": "C1", "codes": ["STRATEGY_EXPANDED"], "request": _req(P, C240, "BTO", 1)},
        {"side": "STO", "qty": 1, "id": C250, "combo": "C1", "request": _req(P, C250, "STO", 1)}],
        "combos": [{"kind": "SEPARATE", "legs": [1, 2], "strategy": "call spread", "codes": ["SEPARATE_TICKETS"], "request": False, "order": [1, 2]}]}),
    _case("clean-sell-call-spread", "sell 1 ACME Dec 18 2026 240/250 call spread", {"ok": True, "legs": [{"side": "STO", "id": C240}, {"side": "BTO", "id": C250}]}),
    _case("clean-put-spread", "buy 1 ACME Dec 18 2026 240/250 put spread", {"ok": True, "legs": [{"side": "STO", "id": P240}, {"side": "BTO", "id": P250}]}),
    _case("clean-call-vertical", "buy 1 ACME Dec 18 2026 240/250 call vertical", {"ok": True, "legs": [{"id": C240}, {"id": C250}]}),
    _case("clean-spread-exp-after", "buy 1 ACME 240/250 call spread Dec 18 2026", {"ok": True, "legs": [{"id": C240}, {"id": C250}]}),
    _case("clean-iron-condor-sell", "sell 1 ACME Dec 18 2026 210/220/250/260 iron condor", {"ok": True, "codes": ["STRATEGY_SIDE_CONVENTION"], "legs": [
        {"side": "BTO", "id": P210}, {"side": "STO", "id": P220}, {"side": "STO", "id": C250}, {"side": "BTO", "id": C260}],
        "combos": [{"kind": "SEPARATE", "legs": [1, 2, 3, 4], "order": [1, 4, 2, 3], "request": False}]}),
    _case("clean-iron-condor-buy", "buy 1 ACME Dec 18 2026 220/230/250/260 iron condor", {"ok": True, "legs": [
        {"side": "STO", "id": P220}, {"side": "BTO", "id": P230}, {"side": "BTO", "id": C250}, {"side": "STO", "id": C260}]}),
    _case("clean-iron-butterfly", "sell 1 ACME Dec 18 2026 220/230/240 iron butterfly", {"ok": True, "legs": [
        {"side": "BTO", "id": P220}, {"side": "STO", "id": P230}, {"side": "STO", "id": C230}, {"side": "BTO", "id": C240}]}),
    _case("clean-straddle", "buy 1 ACME Dec 18 2026 230 straddle", {"ok": True, "legs": [{"side": "BTO", "id": C230}, {"side": "BTO", "id": P230}]}),
    _case("clean-strangle", "buy 1 ACME Dec 18 2026 220/240 strangle", {"ok": True, "legs": [{"side": "BTO", "id": P220}, {"side": "BTO", "id": C240}]}),
    _case("clean-butterfly", "buy 1 ACME Dec 18 2026 220/230/240 butterfly", {"ok": True, "codes": ["RIGHT_DEFAULT"], "legs": [
        {"side": "BTO", "qty": 1, "id": C220}, {"side": "STO", "qty": 2, "id": C230}, {"side": "BTO", "qty": 1, "id": C240}]}),
    _case("clean-put-butterfly", "buy 1 ACME Dec 18 2026 220/230/240 put butterfly", {"ok": True, "legs": [{"id": P220}, {"qty": 2, "id": P230}, {"id": P240}]}),
    _case("clean-calendar", "buy 1 ACME Dec 18 2026/Jan 15 2027 240 call calendar", {"ok": True, "legs": [{"side": "STO", "id": C240}, {"side": "BTO", "id": J240C}]}),
    _case("clean-calendar-reversed", "buy 1 ACME Jan 15 2027/Dec 18 2026 240 put calendar", {"ok": True, "legs": [{"side": "STO", "id": P240}, {"side": "BTO", "id": J240P}]}),
    _case("clean-spread-gtc", "buy 1 ACME Dec 18 2026 240/250 call spread gtc", {"ok": True, "combos": [{"request": False, "codes": ["SEPARATE_TICKETS"]}],
                                                                                    "legs": [{"tif": "GTC", "request": True}, {"tif": "GTC", "request": True}]}),
    _case("clean-roll-short", "roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250", {"ok": True, "legs": [
        {"side": "BTC", "qty": 2, "id": C240, "request": True}, {"side": "STO", "qty": 2, "id": J250C, "request": True}],
        "combos": [{"kind": "SEPARATE", "strategy": "roll", "request": False, "order": [1, 2]}]}),
    _case("clean-roll-long-held", "roll ACME Dec 18 2026 250 calls to Jan 15 2027", {"ok": True, "legs": [{"side": "STC", "qty": 3, "id": C250}, {"side": "BTO", "qty": 3, "id": J250C}]}),
    # words asking for one order are refused on the group and each of its orders (ONE_ORDER_ASKED), never split: the ids keep 7b634e2's
    _case("clean-package-buy-write", "buy 200 ACME limit 225 and sell 2 ACME Dec 18 2026 240 calls as a package", {"ok": False, "legs": [
        {"side": "BUY", "limit_px": "225", "combo": "C1", "codes": ["ONE_ORDER_ASKED"], "request": False},
        {"side": "STO", "qty": 2, "combo": "C1", "codes": ["ONE_ORDER_ASKED"], "request": False}],
        "combos": [{"kind": "SEPARATE", "codes": ["ONE_ORDER_ASKED"], "request": False, "order": [1, 2]}]}),
    _case("clean-group-aon-word", "buy 1 ACME Dec 18 2026 240 call; buy 1 ACME Dec 18 2026 250 call aon", {"ok": False, "legs": [
        {"side": "BTC", "combo": "C1", "request": False}, {"side": "BTO", "combo": "C1", "request": False}],
        "combos": [{"kind": "SEPARATE", "legs": [1, 2], "codes": ["ONE_ORDER_ASKED"]}]}),
    _case("clean-paren-package-fund", "(buy 100 ACME and buy 1 ACME Dec 18 2026 240 call) in the Fund book", {"ok": True, "legs": [
        {"book": "F", "account_id": F, "codes": ["BOOK_FROM_SCOPE", "OTHER_BOOK"], "request": True}, {"book": "F", "side": "BTO", "request": True}],
        "combos": [{"kind": "SEPARATE", "order": [1, 2], "request": False}]}),
    _case("clean-straddle-with-shares", "buy 1 ACME Dec 18 2026 240 straddle and buy 100 ACME as a package", {"ok": False, "legs": 3,
        "combos": [{"kind": "SEPARATE", "legs": [1, 2, 3], "order": [3, 1, 2], "codes": ["ONE_ORDER_ASKED"]}]}),
    _case("clean-covered-call", "covered call 100 ACME Dec 18 2026 240", {"ok": True, "legs": [
        {"side": "BUY", "qty": 100, "request": _req(P, "ACME", "BUY", 100)}, {"side": "STO", "qty": 1, "id": C240, "request": _req(P, C240, "STO", 1, covered=True)}],
        "combos": [{"kind": "SEPARATE", "strategy": "covered call", "order": [1, 2], "request": False}]}),
    _case("clean-buy-write", "buy write 200 ACME Dec 18 2026 250", {"ok": True, "legs": [{"side": "BUY", "qty": 200}, {"side": "STO", "qty": 2, "id": C250}]}),
    _case("clean-collar-held", "collar 100 ACME Dec 18 2026 220/260 in the Fund book", {"ok": True, "legs": [
        {"side": "BTO", "id": P220, "book": "F", "codes": ["SIDE_INFERRED"], "request": _req(F, P220, "BTO", 1)},
        {"side": "STO", "id": C260, "request": _req(F, C260, "STO", 1, covered=True)}], "combos": [{"kind": "SEPARATE", "strategy": "collar", "order": [1, 2]}]}),
    _case("clean-collar-not-held", "collar 100 ACME Dec 18 2026 220/260", {"ok": True, "legs": [{"side": "BUY", "qty": 100}, {"side": "BTO", "id": P220}, {"side": "STO", "id": C260}],
                                                                          "combos": [{"kind": "SEPARATE", "order": [1, 2, 3], "request": False}]}),
    # ---- clean: books and accounts ----------------------------------------------------------------------------------------------------
    _case("clean-g-example", "buy 200 ACME limit 225, sell 2 ACME Dec 18 2026 240 calls, and buy 50 WIDG at market in the Fund book", {"ok": True, "legs": [
        {"book": "F", "side": "BUY", "codes": ["BOOK_FROM_SCOPE", "OTHER_BOOK"]}, {"book": "F", "side": "STO", "codes": ["BOOK_FROM_SCOPE", "COVER_HINT", "SIDE_INFERRED"]},
        {"book": "F", "id": "WIDG", "codes": ["BOOK_FROM_SCOPE"]}]}),
    _case("clean-prefix-scope", "in the Fund book: buy 100 ACME; buy 100 WIDG gtc", {"ok": True, "legs": [
        {"book": "F", "account_id": F, "codes": ["BOOK_FROM_SCOPE", "OTHER_BOOK"]}, {"book": "F", "tif": "GTC", "codes": ["BOOK_FROM_SCOPE"]}]}),
    _case("clean-prefix-for", "for the Fund book: buy 100 WIDG", {"ok": True, "legs": [{"book": "F"}]}),
    _case("clean-book-code", "buy 100 ACME in F", {"ok": True, "legs": [{"book": "F", "account_id": F}]}),
    _case("clean-book-for-name", "buy 100 ACME for the Fund", {"ok": True, "legs": [{"book": "F"}]}),
    _case("clean-multi-book", "buy 100 ACME in the Personal and Fund books", {"ok": True, "top": ["MULTI_BOOK"], "legs": [
        {"book": "P", "codes": ["MULTI_BOOK"]}, {"book": "F", "codes": ["MULTI_BOOK", "OTHER_BOOK"]}]}),
    _case("clean-multi-book-prefix", "in the Personal and Fund books: buy 100 ACME; buy 50 WIDG", {"ok": True, "legs": [
        {"book": "P", "id": "ACME"}, {"book": "F", "id": "ACME"}, {"book": "P", "id": "WIDG"}, {"book": "F", "id": "WIDG"}]}),
    _case("clean-account-choice", "buy 100 ACME in the Institutional book", {"ok": True, "legs": [{"book": "I", "account_id": I, "codes": ["ACCOUNT_CHOICE"]}]}),
    _case("clean-account-named-pb", "buy 100 ACME in the Institutional book at pb ms", {"ok": True, "legs": [{"account_id": PB}], "absent": ["ACCOUNT_CHOICE"]}),
    _case("clean-account-named-exchange", "buy 100 ACME at us exchange in the Institutional book", {"ok": True, "legs": [{"account_id": I}], "absent": ["ACCOUNT_CHOICE"]}),
    _case("clean-prefix-account", "in the Institutional book at pb: buy 100 ACME; buy 50 WIDG", {"ok": True, "legs": [{"account_id": PB}, {"account_id": PB}]}),
    _case("clean-selected-fund", "sell 100 ACME", {"ok": True, "legs": [{"book": "F", "side": "SELL", "qty": 100, "codes": ["BOOK_SELECTED"]}], "absent": ["OTHER_BOOK"]}, book="F"),
    _case("clean-default-account", "buy 100 ACME", {"ok": True, "legs": [{"book": "I", "account_id": PB}], "absent": ["ACCOUNT_CHOICE"]}, book="I",
          setup={"account_id": PB}),
    _case("clean-own-clause-scope", "buy 100 ACME, buy 50 WIDG in the Fund book and sell 1 ACME Dec 18 2026 260 call", {"ok": True, "legs": [
        {"book": "P"}, {"book": "F"}, {"book": "P", "codes": ["COVER_HINT"]}]}),
    # ---- refusals: positions -----------------------------------------------------------------------------------------------------------
    _case("ref-oversell-all", "sell all WIDG at market", {"ok": False, "legs": [{"qty": 300, "side": None, "codes": ["WOULD_OVERSELL"], "request": False}]}),
    _case("ref-oversell", "sell 250 WIDG", {"ok": False, "legs": [{"codes": ["WOULD_OVERSELL"], "request": False}]}),
    _case("ref-sell-unheld", "sell 100 ACME", {"ok": False, "legs": [{"codes": ["WOULD_OVERSELL"]}]}),
    _case("ref-split-btc", "buy 5 ACME Dec 18 2026 240 calls", {"ok": False, "legs": [{"codes": ["SIDE_SPLIT"], "request": False}]}),
    _case("ref-split-stc", "sell 5 ACME 250c 12/18/26", {"ok": False, "legs": [{"codes": ["SIDE_SPLIT"]}]}),
    _case("ref-split-cover", "buy 300 GIZM", {"ok": False, "legs": [{"codes": ["SIDE_SPLIT"]}]}),
    _case("ref-nothing-held-all", "sell all ACME", {"ok": False, "legs": [{"codes": ["NOTHING_HELD"]}]}),
    _case("ref-close-too-many", "close 300 GIZM", {"ok": False, "legs": [{"codes": ["SIDE_SPLIT"]}]}),
    _case("ref-close-unheld-option", "close ACME Dec 18 2026 260 call", {"ok": False, "legs": [{"codes": ["NOTHING_HELD"]}]}),
    _case("ref-close-unheld-class", "close ACME puts", {"ok": False, "legs": [{"codes": ["NOTHING_HELD"]}]}),
    _case("ref-close-unheld-shares", "close ACME", {"ok": False, "legs": [{"codes": ["NOTHING_HELD"]}]}),
    _case("ref-roll-nothing", "roll 1 ACME Jan 15 2027 240 calls to 250", {"ok": False, "legs": [{"codes": ["NOTHING_TO_ROLL"]}, {"codes": ["NOTHING_TO_ROLL"]}],
                                                                           "combos": [{"request": False}]}),
    _case("ref-roll-too-many", "roll 5 ACME Dec 18 2026 240 calls to Jan 15 2027 250", {"ok": False, "codes": ["SIDE_SPLIT"]}),
    _case("ref-roll-no-target", "roll 2 ACME Dec 18 2026 240 calls", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-roll-same", "roll 2 ACME Dec 18 2026 240 calls to Dec 18 2026 240", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-suffix-oversell", "buy 100 ACME; sell 50 WIDG; in the Fund book", {"ok": False, "legs": [{"book": "F", "codes": ["BOOK_FROM_SCOPE"]},
                                                                                                         {"book": "F", "codes": ["WOULD_OVERSELL"]}]}),
    _case("ref-collar-lot", "collar 150 ACME Dec 18 2026 220/260", {"ok": False, "codes": ["QTY_LOT"]}),
    _case("ref-buy-write-lot", "buy write 150 ACME Dec 18 2026 240", {"ok": False, "codes": ["QTY_LOT"], "combos": [{"request": False}]}),
    _case("ref-qty-lot", "buy 15 LOTX", {"ok": False, "legs": [{"codes": ["QTY_LOT"]}]}),
    # ---- refusals: instruments and listings -----------------------------------------------------------------------------------------
    _case("ref-symbol-unknown", "buy 100 NOSUCH", {"ok": False, "legs": [{"codes": ["SYMBOL_UNKNOWN"], "request": False}]}),
    _case("ref-symbol-unknown-option", "buy 1 NOSUCH Dec 18 2026 240 call", {"ok": False, "codes": ["SYMBOL_UNKNOWN"]}),
    _case("ref-symbol-ambiguous", "buy 100 ZORB", {"ok": False, "legs": [{"codes": ["SYMBOL_AMBIGUOUS"]}]}),
    _case("ref-root-choice", "buy 1 ACME Dec 18 2026 200 put", {"ok": False, "legs": [{"codes": ["ROOT_CHOICE"], "request": False}]}),
    _case("ref-root-choice-index", "buy 1 SPX Dec 18 2026 6000 call", {"ok": False, "legs": [{"codes": ["ROOT_CHOICE"]}]}),
    _case("ref-expiry-not-listed", "buy 1 ACME Dec 19 2026 240 call", {"ok": False, "legs": [{"codes": ["EXPIRY_NOT_LISTED"], "request": False}]}),
    _case("ref-expiry-saturday", "buy 1 WIDG Dec 19 2026 45 put", {"ok": False, "legs": [{"codes": ["EXPIRY_NOT_LISTED"]}]}),
    _case("ref-expiry-passed", "buy 1 ACME Dec 18 2025 240 call", {"ok": False, "codes": ["EXPIRY_NOT_LISTED"]}),
    _case("ref-expiry-not-a-date", "buy 1 ACME Feb 30 2027 240 call", {"ok": False, "codes": ["EXPIRY_NOT_LISTED"]}),
    _case("ref-strike-not-listed", "buy 1 ACME Dec 18 2026 215 call", {"ok": False, "legs": [{"codes": ["STRIKE_NOT_LISTED"]}]}),
    _case("ref-strike-between", "buy 1 ACME Dec 18 2026 245 call", {"ok": False, "legs": [{"codes": ["STRIKE_NOT_LISTED"]}]}),
    _case("ref-occ-not-on-chain", "buy 1 ACME261218C00245000", {"ok": False, "codes": ["STRIKE_NOT_LISTED"]}),
    _case("ref-month-none-listed", "buy 1 ACME Mar 2027 240 call", {"ok": False, "legs": [{"codes": ["EXPIRY_MONTH_ONLY"]}]}),
    _case("ref-month-no-chain", "buy 1 WIDG Dec 2026 40 put", {"ok": False, "legs": [{"codes": ["EXPIRY_MONTH_ONLY"]}]}),
    _case("ref-fut-month-passed", "buy 2 ES Mar 2026", {"ok": False, "codes": ["EXPIRY_NOT_LISTED"]}),
    _case("ref-fut-id-unlisted", "buy 10 ESZ29", {"ok": False, "codes": ["EXPIRY_NOT_LISTED"]}),
    _case("ref-own-ticket-fx", "buy 1m EURUSD", {"ok": False, "legs": [{"codes": ["KIND_OWN_TICKET"], "request": False}]}),
    _case("ref-own-ticket-fx-slash", "sell 1m EUR/USD", {"ok": False, "codes": ["KIND_OWN_TICKET"]}),
    _case("ref-own-ticket-fx-k", "buy 500k USDJPY", {"ok": False, "codes": ["KIND_OWN_TICKET"]}),
    _case("ref-own-ticket-bond", "buy 10 ACME-31", {"ok": False, "codes": ["KIND_OWN_TICKET"]}),
    _case("ref-own-ticket-private", "buy 10 PRV-WIDGLABS", {"ok": False, "codes": ["KIND_OWN_TICKET"]}),
    _case("ref-index", "buy 100 SPX", {"ok": False, "codes": ["NOT_TRADEABLE_HERE"]}),
    _case("ref-fx-options", "buy 1 EURUSD Dec 18 2026 1.2 call", {"ok": False, "codes": ["NOT_TRADEABLE_HERE"]}),
    # ---- refusals: words and fields --------------------------------------------------------------------------------------------------
    _case("ref-one-letter-lower", "buy 100 q", {"ok": False, "legs": [{"codes": ["MISSING_INSTRUMENT"]}]}),
    _case("amb-unit-or-ticker", "buy 100 LOT TOGETHER", {"ok": False, "legs": [{"codes": ["TOKEN_AMBIGUOUS"], "request": False}]}),
    _case("ref-bare-number", "buy 100 ACME 225", {"ok": False, "legs": [{"codes": ["UNPARSED_TEXT"]}]}),
    _case("ref-two-strikes", "buy 1 ACME Dec 18 26 240 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-strike-pair-not-date", "buy 1 ACME 12/18 240 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-month-alone", "buy 1 ACME Dec 240 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-no-strike", "buy 1 ACME Dec 20 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-day-month-no-strike", "buy 1 ACME 20 Dec 2026 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-year-not-strike", "buy 1 ACME Dec 18 2050 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-two-prices", "buy 100 ACME limit 225 at market", {"ok": False, "legs": [{"codes": ["DUPLICATE_FIELD"]}]}),
    _case("ref-two-tifs", "buy 100 ACME day gtc", {"ok": False, "codes": ["DUPLICATE_FIELD"]}),
    _case("ref-two-books", "buy 100 ACME in the Fund book in the Personal book", {"ok": False, "codes": ["DUPLICATE_FIELD"]}),
    _case("ref-two-ats", "sell 100 ACME at 225 at 226", {"ok": False, "codes": ["DUPLICATE_FIELD"]}),
    _case("ref-qty-not-whole", "buy 1.5 ACME", {"ok": False, "legs": [{"codes": ["QTY_NOT_WHOLE"]}]}),
    _case("ref-qty-zero", "buy 0 ACME", {"ok": False, "codes": ["MISSING_QTY"]}),
    _case("ref-qty-missing", "buy ACME", {"ok": False, "codes": ["MISSING_QTY"]}),
    _case("ref-qty-dollars", "buy $225 ACME", {"ok": False, "codes": ["MISSING_QTY"]}),
    _case("ref-side-missing", "100 ACME", {"ok": False, "legs": [{"codes": ["MISSING_SIDE"]}]}),
    _case("ref-instrument-missing", "buy 100", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-unit-option", "buy 2 shares ACME Dec 18 2026 240 call", {"ok": False, "codes": ["UNIT_KIND_MISMATCH"]}),
    _case("ref-unit-equity", "buy 100 ACME contracts", {"ok": False, "codes": ["UNIT_KIND_MISMATCH"]}),
    _case("ref-unit-future", "buy 2 shares ESZ26", {"ok": False, "codes": ["UNIT_KIND_MISMATCH"]}),
    _case("ref-side-future", "short 1 ESZ26", {"ok": False, "codes": ["MISSING_SIDE"]}),
    _case("ref-side-equity", "bto 100 ACME", {"ok": False, "codes": ["MISSING_SIDE"]}),
    _case("ref-side-option", "short 2 ACME Dec 18 2026 260 calls", {"ok": False, "codes": ["MISSING_SIDE"]}),
    _case("ref-gtd-no-date", "buy 100 ACME gtd", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-trail-pct", "buy 100 ACME trail 150%", {"ok": False, "codes": ["MISSING_PRICE"]}),
    _case("ref-trail-none", "buy 100 ACME trail", {"ok": False, "codes": ["MISSING_PRICE"]}),
    _case("ref-stop-none", "buy 100 ACME stop", {"ok": False, "codes": ["MISSING_PRICE"]}),
    _case("ref-limit-none", "buy 100 ACME limit", {"ok": False, "codes": ["MISSING_PRICE"]}),
    _case("ref-stop-limit-words", "buy 100 ACME stop limit 240", {"ok": False, "codes": ["MISSING_PRICE"]}),
    _case("ref-limit-zero", "sell 2 ACME Dec 18 2026 240 calls limit 0", {"ok": False, "codes": ["MISSING_PRICE"]}),
    _case("ref-no-quote", "buy 100 ACME at the bid", {"ok": False, "legs": [{"codes": ["MISSING_PRICE"], "request": False}]}),
    _case("ref-unknown-book", "buy 100 ACME in the Foo book", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-account-pb-not-in-book", "buy 100 ACME at pb ms", {"ok": False, "codes": ["ACCOUNT_NOT_IN_BOOK"]}),
    _case("ref-account-id-other-book", "buy 100 ACME in F1:US_EXCHANGE", {"ok": False, "codes": ["ACCOUNT_NOT_IN_BOOK"]}),
    _case("ref-not-understood", "hello world", {"ok": False, "legs": 0, "top": ["UNPARSED_TEXT"]}),
    _case("ref-empty", "", {"ok": False, "legs": 0, "top": ["MISSING_INSTRUMENT"]}),
    _case("ref-blank", "   \n  ", {"ok": False, "legs": 0, "top": ["MISSING_INSTRUMENT"]}),
    _case("ref-trailing-words", "buy 100 ACME (and more)", {"ok": False, "top": ["UNPARSED_TEXT"]}),
    _case("ref-stray-paren", "buy 100 ACME)", {"ok": False, "top": ["UNPARSED_TEXT"]}),
    _case("ref-junk-char", "buy 100 ACME #now", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-non-ascii", "buy 100 ACME \u2605", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    # ---- refusals: combinations (1.0.7.2 simplified: a leg's own limit is its ticket's; a price on the whole is NET_PRICE; words asking for
    # one order are ONE_ORDER_ASKED; two names, two equities, a future, GTD are separate tickets - the ids keep 7b634e2's) -----------------
    _case("ref-limit-in-aon-group", "(buy 1 ACME Dec 18 2026 240 call limit 3.20 and sell 1 ACME Dec 18 2026 250 call limit 1.10) together", {
        "ok": False, "legs": [{"order_type": "LIMIT", "limit_px": "3.20", "request": False}, {"order_type": "LIMIT", "limit_px": "1.10", "request": False}],
        "combos": [{"kind": "SEPARATE", "codes": ["ONE_ORDER_ASKED"], "request": False}], "absent": ["NET_PRICE"]}),
    _case("ref-limit-in-aon-strategy", "buy 1 ACME Dec 18 2026 240/250 call spread limit 2.10", {"ok": False, "combos": [{"codes": ["NET_PRICE"], "request": False}],
                                                                                                "legs": [{"request": False}, {"request": False}]}),
    _case("ref-combo-two-names", "(buy 1 ACME Dec 18 2026 240 call and sell 1 WIDG Dec 18 2026 40 put) together", {"ok": False, "combos": [{"codes": ["ONE_ORDER_ASKED"]}],
                                                                                                                    "legs": [{"request": False}, {"request": False}]}),
    # simplify round 1: words asking for one order hold every order of the run they end, whatever its names (never only the last name's)
    _case("ref-combo-one-leg", "buy 100 WIDG and sell 1 ACME Dec 18 2026 260 call as a package", {"ok": False, "legs": [
        {"combo": "C1", "codes": ["ONE_ORDER_ASKED"], "request": False}, {"combo": "C1", "codes": ["ONE_ORDER_ASKED"], "request": False}],
        "combos": [{"legs": [1, 2], "codes": ["ONE_ORDER_ASKED"]}]}),
    _case("ref-combo-two-equities", "(buy 100 ACME and buy 100 WIDG)", {"ok": True, "legs": [{"combo": "C1", "request": True}, {"combo": "C1", "request": True}],
                                                                       "combos": [{"kind": "SEPARATE", "codes": ["SEPARATE_TICKETS"], "request": False}]}),
    _case("ref-combo-future", "(buy 1 ESZ26 and buy 1 ACME Dec 18 2026 240 call)", {"ok": True, "legs": [{"id": "ESZ26", "request": True}, {"request": True}],
                                                                                   "combos": [{"codes": ["SEPARATE_TICKETS"], "request": False}]}),
    _case("ref-combo-gtd", "buy 1 ACME Dec 18 2026 240/250 call spread gtd 2026-12-01", {"ok": True, "legs": [
        {"tif": "GTD", "gtd_date": "2026-12-01", "request": True}, {"tif": "GTD", "gtd_date": "2026-12-01", "request": True}], "combos": [{"codes": ["SEPARATE_TICKETS"]}]}),
    _case("ref-covered-price", "covered call 100 ACME Dec 18 2026 240 limit 2", {"ok": False, "codes": ["NET_PRICE"], "legs": [{"request": False}, {"request": False}]}),
    _case("ref-strike-order", "buy 1 ACME Dec 18 2026 240/220 strangle", {"ok": False, "codes": ["STRIKE_ORDER"]}),
    _case("ref-vertical-no-right", "buy 1 ACME Dec 18 2026 240/250 vertical", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-spread-one-strike", "buy 1 ACME Dec 18 2026 240 call spread", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-calendar-no-right", "buy 1 ACME Dec 18 2026/Jan 15 2027 240 calendar", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-spread-three-strikes", "buy 1 ACME Dec 18 2026 240/250/260 call spread", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-fly-four-strikes", "buy 1 ACME Dec 18 2026 220/230/240/250 butterfly", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-fly-order", "buy 1 ACME Dec 18 2026 240/230/220 butterfly", {"ok": False, "codes": ["STRIKE_ORDER"]}),
    _case("ref-condor-order", "sell 1 ACME Dec 18 2026 260/250/230/220 iron condor", {"ok": False, "codes": ["STRIKE_ORDER"]}),
    _case("ref-straddle-right", "buy 1 ACME Dec 18 2026 240 call straddle", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-strategy-short", "short 1 ACME Dec 18 2026 240/250 call spread", {"ok": False, "codes": ["MISSING_SIDE"], "combos": [{"request": False}]}),
    _case("ref-roll-unheld-stale", "roll 2 GIZM Dec 18 2026 50 calls to 55", {"ok": False, "codes": ["NOTHING_TO_ROLL", "CHAIN_STALE"]}),
    _case("ref-close-qty-two-positions", "close 1 ACME calls", {"ok": False, "codes": ["ACCOUNT_CHOICE"]}),
    _case("ref-pb-not-in-fund", "buy 1 ACME Dec 18 2026 240 call in the Fund book at pb ms", {"ok": False, "codes": ["ACCOUNT_NOT_IN_BOOK"]}),
    _case("ref-pb-other-broker", "buy 100 ACME in the Institutional book at pb gs", {"ok": False, "codes": ["ACCOUNT_NOT_IN_BOOK"]}),
    _case("ref-two-quote-prices", "buy 1 ACME Dec 18 2026 240 call at the bid at the ask", {"ok": False, "codes": ["DUPLICATE_FIELD"]}),
    _case("ref-two-tif-words", "buy 100 ACME gtc next session", {"ok": False, "codes": ["DUPLICATE_FIELD"]}),
    _case("ref-until-not-a-date", "sell 100 WIDG until Feb 30", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-two-rights", "buy 1 ACME Dec 18 2026 240 put call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-two-expiries", "buy 2 ACME 2026-12-18 2027-01-15 240 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-no-qty-second-name", "buy 100 ACME, WIDG", {"ok": False, "legs": 1, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-unclosed-paren", "buy 1 ACME Dec 18 2026 240 call (and sell 1 ACME Dec 18 2026 250 call", {"ok": False, "legs": 2, "top": ["UNPARSED_TEXT"]}),
    _case("ref-nested-paren", "((buy 100 ACME))", {"ok": False, "top": ["UNPARSED_TEXT"], "combos": [{"legs": [1], "request": False}]}),
    _case("ref-future-with-terms", "buy 1 ESZ26 Dec 18 2026 240 call", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-occ-with-terms", "buy 1 ACME261218C00240000 240 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-occ-expired", "buy 1 ACME251219C00240000", {"ok": False, "codes": ["EXPIRY_NOT_LISTED"]}),
    _case("ref-occ-unknown-underlying", "buy 1 ZZZZ261218C00240000", {"ok": False, "codes": ["SYMBOL_UNKNOWN"]}),
    _case("ref-half-unheld", "sell half Q", {"ok": False, "codes": ["NOTHING_HELD"]}),
    _case("ref-all-unheld-option", "buy all WIDG Dec 18 2026 45 put", {"ok": False, "codes": ["NOTHING_HELD"]}),
    _case("ref-cover-future", "cover 100 ESZ26", {"ok": False, "codes": ["MISSING_SIDE"]}),
    _case("ref-stc-equity", "stc 100 ACME", {"ok": False, "codes": ["MISSING_SIDE"]}),
    _case("ref-stop-limit-no-limit", "buy 100 ACME stop 240 limit", {"ok": False, "codes": ["MISSING_PRICE"]}),
    _case("ref-limit-not-number", "buy 100 ACME limit abc", {"ok": False, "codes": ["MISSING_PRICE", "UNPARSED_TEXT"]}),
    _case("ref-books-one-unknown", "buy 100 ACME in the Personal and Foo books", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-covered-put", "covered call 100 ACME Dec 18 2026 240 put", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-collar-one-strike", "collar 100 ACME Dec 18 2026 260", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-buy-write-no-qty", "buy write ACME Dec 18 2026 240", {"ok": False, "codes": ["MISSING_QTY"]}),
    _case("ref-close-alone", "close", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-roll-alone", "roll", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-prefix-unknown-book", "in the Foo book: buy 100 ACME", {"ok": False, "top": ["UNPARSED_TEXT"]}),
    _case("ref-index-strike", "buy 1 SPX Dec 18 2026 6050 call", {"ok": False, "codes": ["STRIKE_NOT_LISTED"]}),
    _case("ref-weekly-root-strike", "buy 1 SPXW Dec 18 2026 5900 call", {"ok": False, "codes": ["STRIKE_NOT_LISTED"]}),
    _case("ref-stale-chain-strike", "buy 1 GIZM Dec 18 2026 47 put", {"ok": False, "codes": ["STRIKE_NOT_LISTED", "CHAIN_STALE"]}),
    _case("amb-x-unit-or-ticker", "buy 100 X TOGETHER", {"ok": False, "codes": ["TOKEN_AMBIGUOUS"]}),
    _case("amb-futures-root-alone", "buy 2 NQ", {"ok": False, "legs": [{"codes": ["TOKEN_AMBIGUOUS"], "request": False}]}),
    _case("ref-english-word-not-ticker", "buy 100 now", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-qty-after-ticker", "buy ACME 100", {"ok": False, "codes": ["MISSING_QTY", "UNPARSED_TEXT"]}),
    _case("ref-no-separator", "buy 100 ACME sell 50 WIDG", {"ok": False, "legs": 1, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-second-strike-alone", "buy 1 ACME Dec 18 2026 240 call and 250 call", {"ok": False, "legs": 1, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-words-after", "buy 100 ACME on monday", {"ok": False, "codes": ["UNPARSED_TEXT"]}),
    _case("ref-option-no-expiry", "buy 1 ACME 240 call", {"ok": False, "codes": ["MISSING_INSTRUMENT"]}),
    _case("ref-negative-qty", "sell -100 ACME", {"ok": False, "codes": ["MISSING_QTY"]}),
    _case("ref-exponent-qty", "buy 1e3 ACME", {"ok": False, "codes": ["MISSING_QTY"]}),
    # ---- 1.0.7.2 review: the misparses found and fixed (each was a sendable order the words did not say) -----------------------------
    _case("clean-put-spread-reversed", "buy 1 ACME Dec 18 2026 250/240 put spread", {"ok": True, "codes": ["STRATEGY_SIDE_CONVENTION"], "legs": [
        {"side": "STO", "id": P240}, {"side": "BTO", "id": P250}]}),
    _case("clean-call-condor", "buy 1 ACME Dec 18 2026 210/220/250/260 call condor", {"ok": True, "legs": [
        {"side": "BTO", "id": C210}, {"side": "STO", "id": C220}, {"side": "STO", "id": C250}, {"side": "BTO", "id": C260}],
        "combos": [{"kind": "SEPARATE", "order": [1, 4, 2, 3]}]}),
    _case("clean-calendar-year-once", "buy 1 ACME Dec 18/Jan 15 2027 240 call calendar", {"ok": True, "legs": [{"side": "STO", "id": C240}, {"side": "BTO", "id": J240C}]}),
    _case("clean-suffix-then-modifier", "buy 200 ACME limit 225, buy 50 WIDG in the Fund book at market", {"ok": True, "legs": [
        {"book": "F", "codes": ["BOOK_FROM_SCOPE"]}, {"book": "F"}]}),
    _case("clean-carried-book", "buy 100 ACME in the Fund book and 50 WIDG", {"ok": True, "legs": [{"book": "F"}, {"book": "F", "codes": ["SIDE_CARRIED", "BOOK_FROM_SCOPE"]}]}),
    _case("clean-prefix-overridden", "in the Fund book: buy 100 ACME; buy 50 WIDG in the Personal book", {"ok": True, "legs": [
        {"book": "F"}, {"book": "P", "codes": ["BOOK_CONFLICT"]}]}),
    _case("clean-covered-call-held", "covered call 100 ACME Dec 18 2026 240", {"ok": True, "combos": 0, "legs": [
        {"side": "STO", "qty": 1, "id": C240, "book": "F", "codes": ["SIDE_INFERRED"]}]}, book="F"),
    _case("amb-bare-condor", "buy 1 ACME Dec 18 2026 210/220/250/260 condor", {"ok": False, "codes": ["TOKEN_AMBIGUOUS"], "combos": [{"request": False}]}),
    _case("ref-roll-price", "roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 at 1.20", {"ok": False, "combos": [{"codes": ["NET_PRICE"], "request": False}],
                                                                                          "absent": ["TYPE_FROM_AT"]}),
    _case("ref-collar-price", "collar 100 ACME Dec 18 2026 220/260 limit 0.50", {"ok": False, "combos": [{"codes": ["NET_PRICE"], "request": False}]}),
    _case("ref-sell-all-short-option", "sell all ACME Dec 18 2026 240 calls", {"ok": False, "legs": [{"codes": ["ADDS_TO_POSITION"], "request": False}]}),
    _case("ref-buy-all-long", "buy all WIDG limit 40", {"ok": False, "legs": [{"codes": ["ADDS_TO_POSITION", "QTY_FROM_HELD"], "request": False}]}),
    _case("ref-qty-comma", "buy 1,5k ACME limit 225", {"ok": False, "legs": [{"codes": ["QTY_NOT_WHOLE"], "request": False}]}),
    _case("amb-qty-thousands-point", "buy 1.000 ACME limit 225", {"ok": False, "legs": [{"codes": ["TOKEN_AMBIGUOUS"], "request": False}]}),
    _case("ref-close-class-price", "close ACME calls limit 2", {"ok": False, "legs": [{"codes": ["PRICE_AMBIGUOUS"], "request": False},
                                                                                     {"codes": ["PRICE_AMBIGUOUS"], "request": False}]}),
    _case("clean-held-in-pb-stc", "sell 2 ACME Dec 18 2026 250 calls in the Institutional book", {"ok": True, "legs": [
        {"account_id": PB, "side": "STC", "qty": 2, "codes": ["SIDE_INFERRED", "ACCOUNT_CHOICE"]}]}),
    _case("clean-held-in-pb-cover", "buy 50 GIZM in the Institutional book", {"ok": True, "legs": [{"account_id": PB, "side": "BUY_TO_COVER", "qty": 50}]}),
    _case("ref-named-account-not-holding", "sell 2 ACME Dec 18 2026 250 calls in the Institutional book at us exchange", {"ok": False, "legs": [
        {"codes": ["ACCOUNT_CHOICE"], "request": False}]}),
    _case("ref-prefix-account-not-in-book", "at pb ms: buy 100 ACME", {"ok": False, "legs": [{"codes": ["ACCOUNT_NOT_IN_BOOK"], "request": False}]}),
    _case("ref-suffix-account-not-in-book", "buy 100 ACME; at pb ms", {"ok": False, "legs": [{"codes": ["ACCOUNT_NOT_IN_BOOK"], "request": False}]}),
    # ---- 1.0.7.2 second review: the gaps left by the first fixes -------------------------------------------------------------------------
    # a calendar with a month alone: both dates read (the month through the cached chain's one date), near sold on a buy, either typed order
    _case("clean-calendar-month-only", "buy 1 ACME Dec 2026/Jan 15 240 call calendar", {"ok": True, "codes": ["EXPIRY_FROM_MONTH", "YEAR_INFERRED"], "legs": [
        {"side": "STO", "id": C240}, {"side": "BTO", "id": J240C}]}),
    _case("clean-calendar-month-only-reversed", "buy 1 ACME Jan 15/Dec 2026 240 call calendar", {"ok": True, "legs": [{"side": "STO", "id": C240}, {"side": "BTO", "id": J240C}]}),
    _case("clean-calendar-month-only-sold", "sell 1 ACME Dec 2026/Jan 15 240 put calendar", {"ok": True, "legs": [{"side": "BTO", "id": P240}, {"side": "STO", "id": J240P}]}),
    _case("clean-calendar-month-then-day", "buy 1 ACME Jan 2027/Dec 18 240 call calendar", {"ok": True, "legs": [{"side": "STO", "id": C240}, {"side": "BTO", "id": J240C}]}),
    _case("ref-calendar-month-no-chain", "buy 1 WIDG Nov 2026/Dec 18 40 put calendar", {"ok": False, "codes": ["EXPIRY_MONTH_ONLY"], "combos": [{"request": False}]}),
    # a price typed on a strategy, a roll or a collar inside a group reaches the group (NET_PRICE, refused), never dropped
    _case("ref-grouped-spread-price", "buy 1 ACME Dec 18 2026 240/250 call spread at 1.00 as a package", {"ok": False, "combos": [
        {"codes": ["NET_PRICE", "ONE_ORDER_ASKED"], "request": False}]}),
    _case("ref-paren-spread-price", "(buy 1 ACME Dec 18 2026 240/250 call spread limit 1.00)", {"ok": False, "combos": [{"codes": ["NET_PRICE"], "request": False}]}),
    _case("ref-grouped-spread-price-and-call", "buy 1 ACME Dec 18 2026 240/250 call spread at 1.00 and sell 1 ACME Dec 18 2026 260 call as a package",
          {"ok": False, "combos": [{"codes": ["NET_PRICE", "ONE_ORDER_ASKED"], "request": False}]}),
    _case("ref-paren-roll-price", "(roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 at 1.20)", {"ok": False, "combos": [{"codes": ["NET_PRICE"], "request": False}]}),
    _case("ref-grouped-collar-price", "collar 100 ACME Dec 18 2026 220/260 limit 0.50 as a package", {"ok": False, "combos": [
        {"codes": ["NET_PRICE", "ONE_ORDER_ASKED"], "request": False}]}, book="F"),
    # a side carried into a strategy: a strategy is bought or sold, never a carried short / stc read as a buy
    _case("ref-carried-short-strategy", "short 100 WIDG and 1 ACME Dec 18 2026 240/250 call spread", {"ok": False, "codes": ["MISSING_SIDE"], "combos": [{"request": False}],
                                                                                                    "absent": ["STRATEGY_SIDE_CONVENTION"]}),
    _case("ref-carried-stc-strategy", "stc 1 ACME Dec 18 2026 250 calls and 1 ACME Dec 18 2026 240/250 call spread", {"ok": False, "codes": ["MISSING_SIDE"],
                                                                                                                   "combos": [{"request": False}]}),
    # a covered call / collar on a book that is short the shares: refused, never a share purchase beside the short
    _case("ref-covered-call-short", "covered call 200 GIZM Dec 18 2026 55", {"ok": False, "codes": ["NOTHING_HELD"], "combos": [{"request": False}]}),
    _case("ref-collar-short", "collar 200 GIZM Dec 18 2026 45/55", {"ok": False, "codes": ["NOTHING_HELD"], "combos": [{"request": False}]}),
    _case("ref-covered-call-short-pb", "covered call 100 GIZM Dec 18 2026 55 in the Institutional book", {"ok": False, "codes": ["NOTHING_HELD"]}),
    # an explicit opening side keeps R-ACCT-1 (US Exchange first); the account holding the contract is offered, never filled in silently
    _case("clean-opening-side-not-in-holding-account", "sto 2 ACME Dec 18 2026 250 calls in the Institutional book", {"ok": True, "legs": [
        {"account_id": I, "side": "STO", "codes": ["ACCOUNT_CHOICE"]}]}, book="I"),
    _case("clean-bto-not-in-holding-account", "bto 2 ACME Dec 18 2026 250 calls in the Institutional book", {"ok": True, "legs": [
        {"account_id": I, "side": "BTO", "codes": ["ACCOUNT_CHOICE"]}]}, book="I"),
    _case("clean-short-not-in-holding-account", "short 20 GIZM in the Institutional book", {"ok": True, "legs": [
        {"account_id": I, "side": "SELL_SHORT", "codes": ["ACCOUNT_CHOICE"]}]}, book="I"),
    # the account travels with the carried side, and with the closing book phrase it stands beside
    _case("clean-carried-account", "buy 100 ACME at pb ms and 50 WIDG", {"ok": True, "legs": [{"account_id": PB}, {"account_id": PB, "codes": ["SIDE_CARRIED"]}]},
          book="I"),
    _case("ref-carried-account-not-in-book", "buy 100 ACME at pb ms and 50 WIDG", {"ok": False, "legs": [{"codes": ["ACCOUNT_NOT_IN_BOOK"], "request": False},
                                                                                         {"codes": ["ACCOUNT_NOT_IN_BOOK"], "request": False}]}),
    _case("clean-account-before-closing-book", "buy 100 ACME, buy 50 WIDG at pb ms in the Institutional book", {"ok": True, "legs": [
        {"book": "I", "account_id": PB}, {"book": "I", "account_id": PB}]}, book="I"),
    _case("clean-account-after-closing-book", "buy 100 ACME, buy 50 WIDG in the Institutional book, at pb ms", {"ok": True, "legs": [
        {"book": "I", "account_id": PB}, {"book": "I", "account_id": PB}]}, book="I"),
    # a comma before four or more digits is one malformed number too, never two orders
    _case("ref-qty-comma-4-digits", "buy 1,5000 ACME limit 225", {"ok": False, "legs": [{"codes": ["QTY_NOT_WHOLE"], "request": False}]}),
    _case("ref-qty-comma-5-digits", "buy 12,34567 ACME limit 225", {"ok": False, "legs": [{"codes": ["QTY_NOT_WHOLE"], "request": False}]}),
    # ---- 1.0.7.2 simplified: the cover read on the account as it is, words asking for one order, net prices ----------------------------
    _case("ref-short-while-long", "short 300 WIDG limit 41.20", {"ok": False, "legs": [{"side": "SELL_SHORT", "codes": ["SHORT_WHILE_LONG"], "request": False}]}),
    _case("ref-oversell-no-short", "sell 400 WIDG limit 41", {"ok": False, "legs": [{"codes": ["WOULD_OVERSELL"], "request": False}]}),
    _case("ref-collar-inverted", "collar 100 ACME Dec 18 2026 260/220", {"ok": False, "codes": ["STRIKE_ORDER"], "combos": [{"strategy": "collar", "request": False}]},
          book="F"),
    _case("ref-buy-write-short", "buy write 200 GIZM Dec 18 2026 55", {"ok": False, "codes": ["NOTHING_HELD"], "legs": [{"request": False}, {"request": False}]}),
    _case("ref-buy-write-price", "buy write 200 ACME Dec 18 2026 250 limit 2.10", {"ok": False, "combos": [{"codes": ["NET_PRICE"], "request": False}],
                                                                                  "legs": [{"request": False}, {"request": False}]}),
] + [
    _case(f"ref-one-order-{where}-{w.replace(' ', '-')}", text + " " + w, {"ok": False, "combos": [{"kind": "SEPARATE", "codes": ["ONE_ORDER_ASKED"], "request": False}],
                                                                       "legs": [{"codes": ["ONE_ORDER_ASKED"], "request": False}] * 2})
    for w in ("as a package", "as one order", "as one", "as a combo", "all or none", "aon", "together")
    for where, text in (("strategy", "buy 1 ACME Dec 18 2026 240/250 call spread"), ("group", "(buy 100 ACME and buy 1 ACME Dec 18 2026 240 call)"))
] + [
    # ---- the route's 400s -------------------------------------------------------------------------------------------------------------
    _case("ref-400-too-long", LONG_TEXT, {"status": 400, "error": "TEXT_TOO_LONG"}),
    _case("ref-400-too-many-legs", "; ".join(["sell 1 ACME Dec 18 2026 210/220/250/260 iron condor"] * 5) + "; buy 1 ACME", {"status": 400, "error": "TOO_MANY_LEGS"}),
    _case("ref-400-multi-book-legs", "buy 1 ACME; buy 1 WIDG; buy 1 GIZM; buy 1 Q; buy 1 LOTX; buy 1 ACME; buy 1 WIDG; buy 1 GIZM; buy 1 Q; buy 1 LOTX; buy 1 ACME"
                                     " in the Personal and Fund and Institutional books", {"status": 400, "error": "TOO_MANY_LEGS"}),
]
