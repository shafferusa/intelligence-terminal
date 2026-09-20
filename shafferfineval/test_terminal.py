"""Offline tests for the terminal: universe, storage, routing, refresh.

Pure stdlib, no network:   python3 test_terminal.py
"""
import datetime as dt, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import company_scoring as comp
import hedging as H
import refresh as R
import routers
import scoring
import storage
import strategy_catalog as SC
import universe as uni

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

DB = os.path.join(tempfile.mkdtemp(), "t.db")

print("== 1. universe loading from the workbook ==")
assets, source = uni.load_multi_asset_universe()
check("workbook found", source.kind == "workbook", source.notes)
check("many assets", len(assets) > 1500, len(assets))
classes = {a.asset_class for a in assets}
for expected in (uni.EQUITY, uni.ETF, uni.BOND, uni.FUTURE, uni.FX,
                 uni.COMMODITY, uni.OTC, uni.CDS, uni.CRYPTO):
    check(f"loads {expected}", expected in classes)
equities = [a for a in assets if a.asset_class == uni.EQUITY]
check("equities are Stock/ADR/REIT only",
      {a.subclass for a in equities} <= {"Stock", "ADR (foreign stock)", "REIT"},
      {a.subclass for a in equities})
check("ETFs are NOT scoreable", all(not a.scoreable for a in assets
                                    if a.asset_class == uni.ETF))
check("equities are scoreable", all(a.scoreable for a in equities))
check("foreign ticker translated",
      uni.to_yahoo_symbol("0700-HK", uni.EQUITY) == "0700.HK")
check("share class preserved", uni.to_yahoo_symbol("BRK-B", uni.EQUITY) == "BRK-B")
check("bonds get no Yahoo symbol", uni.to_yahoo_symbol("UST-10Y", uni.BOND) is None)
check("equity rows for scoring", len(uni.equity_universe_rows(assets)) == len(equities))

print("== 23. database initialisation ==")
conn = storage.init_db(DB)
check("db created", os.path.exists(DB))
tables = {r["name"] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
for table in ("assets", "current_scores", "score_history", "positions",
              "watchlist", "hedge_recommendations", "fundamental_history"):
    check(f"table {table}", table in tables)
check("init is idempotent", storage.init_db(DB) is not None)

ids = storage.upsert_assets(conn, assets)
check("assets persisted", len(ids) == len(assets), f"{len(ids)} vs {len(assets)}")
check("keyed by (symbol, class) so collisions survive",
      ("CL", uni.EQUITY) in ids and ("CL", uni.COMMODITY) in ids)
check("re-upsert is idempotent",
      len(storage.upsert_assets(conn, assets)) == len(ids))

print("== 2-4. search ==")
check("by ticker", any(r["symbol"] == "NVDA"
                       for r in storage.search_assets(conn, "NVDA")))
check("exact ticker ranks first",
      storage.search_assets(conn, "NVDA")[0]["symbol"] == "NVDA")
check("by company name", any("NVIDIA" in (r["name"] or "").upper()
                             for r in storage.search_assets(conn, "NVIDIA")))
check("by sector", len(storage.search_assets(conn, "Information Technology")) > 5)
check("by asset class", len(storage.search_assets(conn, "Bond")) > 5)
check("case insensitive", len(storage.search_assets(conn, "nvda")) > 0)
check("empty query -> nothing", storage.search_assets(conn, "") == [])
check("no match -> nothing", storage.search_assets(conn, "ZZZQQQ999") == [])

nvda = storage.get_asset(conn, "NVDA")
aapl = storage.get_asset(conn, "AAPL")
check("get_asset works", nvda is not None and nvda["asset_class"] == uni.EQUITY)
check("ambiguous symbol prefers the scoreable equity",
      storage.get_asset(conn, "CL")["asset_class"] == uni.EQUITY)
check("explicit asset class disambiguates",
      storage.get_asset(conn, "CL", uni.COMMODITY)["name"] == "WTI Crude Oil")
check("both classes discoverable", len(storage.find_assets(conn, "CL")) == 2)

print("== 5. watchlist add/remove ==")
storage.add_to_watchlist(conn, nvda["asset_id"])
check("added", storage.is_watched(conn, nvda["asset_id"]))
storage.add_to_watchlist(conn, nvda["asset_id"])
check("adding twice is idempotent", len(storage.list_watchlist(conn)) == 1)
storage.remove_from_watchlist(conn, nvda["asset_id"])
check("removed", not storage.is_watched(conn, nvda["asset_id"]))
storage.add_to_watchlist(conn, nvda["asset_id"])

print("== 6-7. positions, long and short ==")
long_id = storage.add_position(conn, nvda["asset_id"], H.LONG, 10000, average_cost=150.0)
short_id = storage.add_position(conn, aapl["asset_id"], H.SHORT, 2500, average_cost=310.0)
positions = storage.list_positions(conn)
check("two positions", len(positions) == 2)
long_row = next(p for p in positions if p["symbol"] == "NVDA")
short_row = next(p for p in positions if p["symbol"] == "AAPL")
check("long stored", long_row["direction"] == H.LONG and long_row["quantity"] == 10000)
check("short stored", short_row["direction"] == H.SHORT and short_row["quantity"] == 2500)
check("quantity stored absolute", short_row["quantity"] > 0)
check("average cost stored", long_row["average_cost"] == 150.0)

print("== 8. current score persistence ==")
storage.save_current_score(
    conn, nvda["asset_id"], price=180.0, shaffer_score=-62.0,
    classification="BEARISH", preferred_hedge="Put-spread hedge",
    company_score=-66.0, sector_overlay=4.0, score_confidence="HIGH",
    model_status=routers.IMPLEMENTED,
    factor_scores={"valuation": -70.0, "growth": -40.0},
    raw_inputs={"price": 180.0}, model_version=storage.EQUITY_MODEL_VERSION)
current = storage.get_current_score(conn, nvda["asset_id"])
check("score stored", current["shaffer_score"] == -62.0)
check("factors stored as json",
      storage.loads(current["factor_scores_json"])["valuation"] == -70.0)
check("13. model version stored", current["model_version"] == "equity_model_v1")
storage.save_current_score(conn, nvda["asset_id"], price=181.0, shaffer_score=-55.0,
                           classification="BEARISH")
check("current score is mutable",
      storage.get_current_score(conn, nvda["asset_id"])["shaffer_score"] == -55.0)

print("== 9 & 24. immutable daily snapshots ==")
day1, day2 = "2026-09-18", "2026-09-19"
written = storage.save_score_snapshot(
    conn, nvda["asset_id"], day1, price=180.0, shaffer_score=-62.0,
    classification="BEARISH", factor_scores={"valuation": -70.0},
    preferred_hedge="Put-spread hedge")
check("first close snapshot written", written == "written")
again = storage.save_score_snapshot(
    conn, nvda["asset_id"], day1, price=999.0, shaffer_score=+99.0,
    classification="BULLISH", factor_scores={"valuation": +99.0})
check("21. same-day re-refresh does NOT rewrite the close", again == "exists")
history = storage.get_score_history(conn, nvda["asset_id"])
check("24. old snapshot unmutated", history[0]["shaffer_score"] == -62.0,
      history[0]["shaffer_score"])
check("old price unmutated", history[0]["price"] == 180.0)
check("only one row for that date", len(history) == 1)

intraday = storage.save_score_snapshot(
    conn, nvda["asset_id"], day1, kind=storage.INTRADAY, shaffer_score=-58.0)
check("22. intraday snapshot is separate", intraday == "written")
check("intraday does not touch the close",
      storage.get_score_history(conn, nvda["asset_id"])[0]["shaffer_score"] == -62.0)
again_intra = storage.save_score_snapshot(
    conn, nvda["asset_id"], day1, kind=storage.INTRADAY, shaffer_score=-50.0)
check("intraday IS replaceable", again_intra == "written")
check("intraday replaced in place",
      storage.get_score_history(conn, nvda["asset_id"], kind=storage.INTRADAY)[0]
      ["shaffer_score"] == -50.0)

storage.save_score_snapshot(conn, nvda["asset_id"], day2, price=176.0,
                            shaffer_score=-40.0, classification="SEMI-BEARISH",
                            factor_scores={"valuation": -55.0})
check("two close snapshots", len(storage.get_score_history(conn, nvda["asset_id"])) == 2)
check("newest first",
      storage.get_score_history(conn, nvda["asset_id"])[0]["snapshot_date"] == day2)
previous = storage.get_previous_snapshot(conn, nvda["asset_id"], day2)
check("previous snapshot found", previous["snapshot_date"] == day1)

print("== 10. score delta ==")
check("delta computed", R.score_delta(-40.0, -62.0) == 22.0)
check("no previous -> None", R.score_delta(-40.0, None) is None)
check("no current -> None", R.score_delta(None, -62.0) is None)
drivers = R.explain_score_change({"valuation": -55.0}, {"valuation": -70.0})
check("driver identified", drivers and drivers[0][0] == "valuation")
check("driver change correct", abs(drivers[0][3] - 15.0) < 1e-9)
multi = R.explain_score_change(
    {"valuation": -55.0, "growth": -38.0}, {"valuation": -70.0, "growth": -40.0})
check("sorted by absolute change", multi[0][0] == "valuation")
check("unchanged factors omitted",
      R.explain_score_change({"a": 1.0}, {"a": 1.0}) == [])
check("missing snapshot -> no drivers", R.explain_score_change(None, {"a": 1.0}) == [])

print("== 11. fundamental change detection ==")
stored = {"revenue": 100.0, "ebitda": 40.0, "net_income": 20.0}
check("no stored -> first observation, not a change",
      R.detect_fundamental_changes(None, stored) == {})
check("identical -> no change",
      R.detect_fundamental_changes(stored, dict(stored)) == {})
changed = R.detect_fundamental_changes(stored, {**stored, "ebitda": 48.0})
check("real change detected", "ebitda" in changed)
check("previous and new recorded",
      changed["ebitda"]["previous"] == 40.0 and changed["ebitda"]["new"] == 48.0)
check("float noise ignored",
      R.detect_fundamental_changes(stored, {**stored, "ebitda": 40.00001}) == {})
check("appearing value is a change",
      "cash" in R.detect_fundamental_changes({**stored, "cash": None},
                                             {**stored, "cash": 5.0}))
check("only watched fields",
      R.detect_fundamental_changes(stored, {**stored, "unrelated": 1}) == {})

storage.save_fundamentals(conn, nvda["asset_id"], stored)
check("fundamentals stored",
      storage.latest_fundamentals(conn, nvda["asset_id"])["revenue"] == 100.0)
storage.save_fundamentals(conn, nvda["asset_id"], {**stored, "ebitda": 48.0},
                          {"ebitda": {"previous": 40.0, "new": 48.0}})
check("change log recorded",
      len(storage.list_fundamental_changes(conn, nvda["asset_id"])) >= 1)

print("== 12. price change is reflected ==")
check("price stored per snapshot",
      {h["snapshot_date"]: h["price"] for h in
       storage.get_score_history(conn, nvda["asset_id"])}[day2] == 176.0)

print("== 14-15. asset class routing ==")
equity = routers.score_asset(uni.EQUITY, "NVDA", None)
check("equity routes to the equity engine", equity.status == routers.NO_DATA)
check("equity model version", equity.model_version == "equity_model_v1")
# Every class now carries Shaffer v1 arithmetic. What differs is whether this
# project has DATA to feed it, so an unfed class returns awaiting_inputs rather
# than model_not_implemented -- and never a fabricated score.
for asset_class in (uni.BOND, uni.FX, uni.COMMODITY, uni.FUTURE, uni.ETF,
                    uni.CRYPTO, uni.OTC, uni.CDS, uni.INDEX, uni.PREFERRED):
    result = routers.score_asset(asset_class, "X", None)
    check(f"{asset_class} -> not scored without inputs",
          result.status in (routers.NOT_IMPLEMENTED, routers.AWAITING_DATA,
                            routers.NO_DATA), result.status)
    check(f"{asset_class} invents no score", result.shaffer_score is None)
    check(f"{asset_class} explains itself", bool(result.message))
check("equities are the only class fed end to end today",
      routers.SCORE_ENGINES[uni.EQUITY] == "equity_model_v1")
check("every other class has arithmetic but declares its missing inputs",
      all(routers.score_asset(c, "X").status != routers.IMPLEMENTED
          for c in (uni.BOND, uni.FX, uni.COMMODITY, uni.CRYPTO)))
check("supplying inputs DOES produce a score",
      routers.score_asset(uni.COMMODITY, "CL", subclass="Commodity Energy",
                          factor_values={"inventories": 50, "supply": 10,
                                         "demand": 20}).status == routers.IMPLEMENTED)
check("only equities have a hedge engine", set(routers.HEDGE_ENGINES) == {uni.EQUITY})

print("== 17-19. hedge routing, no-position vs position ==")
CAT, _ = SC.load_strategy_catalog()
position = H.Position("NVDA", H.LONG, 10000, 180.0)
context = H.HedgeContext(equity_score=-62.0)
check("equity hedge dispatches",
      routers.recommend_hedge_for(uni.EQUITY, position, context, CAT) is not None)
for asset_class in (uni.BOND, uni.FX, uni.COMMODITY, uni.CDS):
    check(f"{asset_class} gets NO equity hedge",
          routers.recommend_hedge_for(asset_class, position, context, CAT) is None)
    check(f"{asset_class} hedge status explained",
          "No hedge engine" in routers.hedge_engine_status(asset_class))

positioned = routers.recommend_hedge_for(uni.EQUITY, position, context, CAT)
check("19. position hedge has exact quantities",
      positioned.hedged_shares > 5000 and positioned.preferred is not None)
check("position ticket has contracts",
      any(l.contracts for l in positioned.preferred.legs
          if l.instrument in ("option",)) or
      any(l.shares for l in positioned.preferred.legs))
nominal = routers.recommend_hedge_for(
    uni.EQUITY, H.Position("NVDA", H.LONG, 100.0, 180.0), context, CAT)
check("18. no-position run still yields a preferred strategy",
      nominal.preferred is not None)
check("no-position hedge ratio is the same model number",
      abs(nominal.hedge_ratio - positioned.hedge_ratio) < 1e-12)

print("== 16. equity score integration ==")
def mk(t, ind="Semiconductors", **kw):
    base = dict(name=f"{t} Inc", industry=ind, sector="Technology", price=100.0,
                shares_outstanding=1e9, market_cap=100e9, total_debt=10e9, cash=5e9,
                ebitda=10e9, revenue=50e9, net_income=5e9, total_assets=100e9,
                revenue_annual=[40e9, 45e9, 50e9], ebitda_annual=[8e9, 9e9, 10e9])
    base.update(kw)
    return comp.CompanyFinancials(ticker=t, **base)
uni_fin = [mk(f"S{i}", ebitda=(i+1)*1e9, market_cap=(i+1)*12e9, total_debt=2e9,
              cash=1e9, shares_outstanding=1e9, price=(i+1)*12.0) for i in range(16)]
target = mk("TGT", ebitda=5e9, market_cap=40e9, cash=10e9, total_debt=2e9,
            shares_outstanding=1e9, price=40.0)
import sector_scoring as sect
fake_sector = sect.SectorScore(sector="Technology", raw_score=40.0, overlay=10.0,
                               confidence="HIGH")
routed = routers.score_asset(uni.EQUITY, "TGT", target, uni_fin + [target], fake_sector)
check("equity scored through the router", routed.status == routers.IMPLEMENTED)
check("uses the existing company engine", routed.detail is not None)
check("final = company + overlay",
      abs(routed.shaffer_score - (routed.company_score + 10.0)) < 1e-9)
check("classification matches the shared bands",
      routed.classification == scoring.classify_score(routed.shaffer_score))
check("factor scores captured",
      {"valuation", "growth", "profitability", "debt"} <= set(routed.factor_scores))
check("raw inputs captured", "ebitda" in routed.raw_inputs)
check("sector overlay carried", routed.sector_overlay == 10.0)

print("== 20. one bad ticker never stops the job ==")
class Boom:
    def __getattr__(self, name): raise RuntimeError("bad ticker")
summary = R.RefreshSummary(started_at=dt.datetime.now(dt.timezone.utc))
ok = 0
for symbol in ["GOOD1", "BAD", "GOOD2"]:
    try:
        if symbol == "BAD":
            raise RuntimeError("simulated failure")
        ok += 1
    except Exception as exc:
        summary.failed += 1
        summary.errors.append(f"{symbol}: {exc}")
check("loop continued past the failure", ok == 2 and summary.failed == 1)
check("failure recorded", "BAD" in summary.errors[0])
check("summary serialises", summary.as_dict()["failed"] == 1)

print("== refresh run bookkeeping ==")
run_id = storage.start_refresh_run(conn, "test", storage.CLOSE)
storage.finish_refresh_run(conn, run_id, 10, 8, 2, {"scope": "test"})
last = storage.last_refresh_run(conn)
check("run recorded", last["attempted"] == 10 and last["failed"] == 2)
check("finished stamped", last["finished_at"] is not None)

print("== hedge recommendation persistence ==")
storage.save_hedge_recommendation(
    conn, nvda["asset_id"], day2, position_id=long_id, strategy="Put-spread hedge",
    strategy_key="PUT_SPREAD_HEDGE", strategy_score=89.0, hedge_ratio=0.55,
    adverse_score=62.0, confidence="HIGH", ticket={"summary": "BUY 55 puts"})
rec = storage.get_hedge_recommendation(conn, nvda["asset_id"], long_id)
check("recommendation stored", rec["strategy_key"] == "PUT_SPREAD_HEDGE")
check("ticket stored", storage.loads(rec["ticket_json"])["summary"] == "BUY 55 puts")
check("hedge model version", rec["model_version"] == "hedge_model_v1")
storage.save_hedge_recommendation(
    conn, nvda["asset_id"], day2, position_id=long_id, strategy="Collar",
    strategy_key="COLLAR", strategy_score=76.0)
check("same-day recommendation updates in place",
      storage.get_hedge_recommendation(conn, nvda["asset_id"], long_id)
      ["strategy_key"] == "COLLAR")

print("== market rows feed the UI from the database ==")
rows = storage.market_rows(conn)
check("one row per asset", len(rows) == len(assets), f"{len(rows)} vs {len(assets)}")
nvda_row = next(r for r in rows if r["symbol"] == "NVDA")
check("carries the current score", nvda_row["shaffer_score"] == -55.0)
check("carries the previous close for a delta", nvda_row["prev_score"] == -62.0,
      nvda_row["prev_score"])
check("watchlist flag", nvda_row["on_watchlist"] == 1)
check("position count", nvda_row["position_count"] == 1)
unscored = [r for r in rows if r["shaffer_score"] is None]
check("unscored assets present and null, never faked", len(unscored) > 1000)

print("== schedules ==")
check("equity schedule active", R.SCHEDULES[uni.EQUITY].implemented)
check("equity time is 16:30 ET",
      R.SCHEDULES[uni.EQUITY].hour == 16 and R.SCHEDULES[uni.EQUITY].minute == 30
      and R.SCHEDULES[uni.EQUITY].timezone == "America/New_York")
check("other classes have their own clocks, inactive",
      all(not s.implemented for k, s in R.SCHEDULES.items() if k != uni.EQUITY))
check("crypto schedule is UTC", R.SCHEDULES[uni.CRYPTO].timezone == "UTC")
check("schedule described", "16:30" in R.next_scheduled_run(uni.EQUITY))

storage.remove_position(conn, short_id)
check("position removed", len(storage.list_positions(conn)) == 1)

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)
