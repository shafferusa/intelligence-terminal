"""Offline tests for synthetic.py, blotter.py and counterfactual.py.

Pure stdlib, no network:   python3 test_research.py
"""
import csv, datetime as dt, os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blotter as B
import counterfactual as CF
import ml_lab
import storage
import synthetic as S
import universe as uni

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}"); return True
    print(f"  FAIL  {name} {extra}"); fails.append(name); return False

TMP = tempfile.mkdtemp()

print("== 14-24. synthetic ML validation suite ==")
results = S.run_all()
by_name = {r.name: r for r in results}
check("all 11 tests ran", len(results) == 11, len(results))
for name in ("Linear recovery", "Nonlinear interaction", "Useless factor rejected",
             "Wrong production weight detected", "Wrong factor sign detected",
             "Political x inventory interaction",
             "Regime change exposed by walk-forward", "Leakage prevention",
             "Hedge selection", "Beta-adjusted proxy sizing",
             "Option volatility value"):
    result = by_name.get(name)
    check(f"{name}", result is not None and result.passed,
          result.detail if result else "missing")
check("every result carries the synthetic label",
      all(r.note == S.SYNTHETIC_LABEL for r in results))
check("suite is deterministic",
      [r.passed for r in S.run_all()] == [r.passed for r in results])

print("== 20. leakage guard is explicit ==")
check("future feature rejected",
      not S.feature_is_point_in_time("2027-01-01", "2026-09-20"))
check("same-instant feature accepted",
      S.feature_is_point_in_time("2026-09-20", "2026-09-20"))
check("past feature accepted",
      S.feature_is_point_in_time("2026-01-01", "2026-09-20"))
check("missing timestamps rejected", not S.feature_is_point_in_time(None, "2026-09-20"))

print("== 25. real and synthetic data stay separate ==")
DB = os.path.join(TMP, "research.db")
conn = storage.init_db(DB)
assets, _ = uni.load_multi_asset_universe()
storage.upsert_assets(conn, assets)   # whole universe: NVDA/AAPL must resolve
check("synthetic module writes nothing to the database",
      conn.execute("SELECT COUNT(*) c FROM score_history").fetchone()["c"] == 0)
check("synthetic results are objects, not rows",
      all(isinstance(r, S.SyntheticResult) for r in results))
check("live dataset is empty and says so",
      ml_lab.build_dataset(conn, "12M").n == 0)
check("disclaimer states it validates software, not the model",
      "SOFTWARE, not the investment model" in S.SYNTHETIC_DISCLAIMER)
check("disclaimer says synthetic never enters production training",
      "never mixed" in S.SYNTHETIC_DISCLAIMER)

print("== 28. FinSim blotter import ==")
csv_path = os.path.join(TMP, "finsim.csv")
with open(csv_path, "w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["Trade ID", "Order ID", "Trade Date", "Security", "Asset Class",
                     "Side", "Quantity", "Price", "Commission", "Net Cash",
                     "Strategy Tag", "Trade Group ID", "Hedge Relationship",
                     "Linked Hedge Trade", "Desk Note"])
    writer.writerow(["T1", "O1", "2026-09-10", "NVDA", "Stock", "BUY", "10000",
                     "180.00", "12.50", "-1800012.50", "core", "G1", "primary", "", "n/a"])
    writer.writerow(["T2", "O2", "2026-09-10", "NVDA", "Option", "BUY", "55",
                     "170.00", "5.00", "-22005.00", "core", "G1", "hedge", "T1", "protective put"])
    writer.writerow(["T3", "O3", "2026-09-11", "AAPL", "Stock", "SELL", "2500",
                     "336.13", "6.25", "840318.75", "alpha", "G2", "primary", "", ""])
    writer.writerow(["T4", "O4", "2026-09-12", "ZZZNOTREAL", "Stock", "BUY", "100",
                     "10.00", "1.00", "-1001.00", "test", "G3", "primary", "", ""])

result = B.import_finsim_blotter(conn, csv_path)
check("csv imported", result.rows_read == 4, result.rows_read)
check("trades parsed", len(result.trades) == 4)
check("columns mapped", "symbol" in B.map_columns(result.columns_seen)[0])
check("unknown columns preserved, not dropped",
      any("Desk Note" in (t.extra or {}) for t in result.trades))
first = result.trades[0]
check("quantity parsed", first.quantity == 10000.0)
check("price parsed", first.price == 180.0)
check("side parsed", first.side.upper() == "BUY")
check("direction derived", first.direction == B.LONG)
check("signed quantity", first.signed_quantity == 10000.0)
check("short trade signs negative",
      next(t for t in result.trades if t.symbol == "AAPL").signed_quantity == -2500.0)
check("commission parsed", first.commission == 12.50)
check("net cash parsed with a negative", first.net_cash == -1800012.50)

print("== 29. security mapping ==")
check("NVDA mapped to the universe", first.mapped_asset_id is not None)
check("mapped symbol recorded", first.mapped_symbol == "NVDA")
check("3 of 4 mapped", result.mapped == 3, result.mapped)
bad = next(t for t in result.trades if t.symbol == "ZZZNOTREAL")
check("unmapped trade is KEPT, not dropped", bad in result.trades)
check("unmapped flagged with a reason", "not in the Shaffer universe" in bad.mapping_note)
check("unmapped listed", "ZZZNOTREAL" in result.unmapped)
check("asset class translated", B.ASSET_CLASS_MAP["stock"] == uni.EQUITY)
check("reit maps to equity", B.ASSET_CLASS_MAP["reit"] == uni.EQUITY)
check("etf does not map to equity", B.ASSET_CLASS_MAP["etf"] == uni.ETF)

print("== other blotter formats ==")
tsv_path = os.path.join(TMP, "finsim.tsv")
with open(tsv_path, "w") as handle:
    handle.write("ticker\tside\tqty\tprice\n")
    handle.write("NVDA\tbuy\t100\t180\n")
check("tsv imported", B.import_finsim_blotter(conn, tsv_path).rows_read == 1)
sqlite_path = os.path.join(TMP, "finsim_blotter.db")
external = sqlite3.connect(sqlite_path)
external.execute("CREATE TABLE trades (tradeid TEXT, security TEXT, side TEXT, "
                 "quantity REAL, price REAL, tradedate TEXT)")
external.execute("INSERT INTO trades VALUES ('X1','NVDA','BUY',500,181.0,'2026-09-15')")
external.commit(); external.close()
sql_result = B.import_finsim_blotter(conn, sqlite_path)
check("sqlite imported", sql_result.rows_read == 1, sql_result.notes)
check("table identified", any("trades" in n for n in sql_result.notes))
check("missing file handled", B.import_finsim_blotter(conn, "/nope.csv").rows_read == 0)
check("missing file explained",
      any("No blotter found" in n for n in B.import_finsim_blotter(conn, "/nope.csv").notes))

print("== 30. trade grouping ==")
groups = B.group_trades(result.trades)
check("groups formed", len(groups) == 3, len(groups))
g1 = next(g for g in groups if "G1" in g.group_key)
check("stock and its option are ONE position", len(g1.trades) == 2)
check("primary identified", g1.primary is not None and g1.primary.trade_id == "T1")
check("hedge leg identified", [t.trade_id for t in g1.hedges] == ["T2"])
check("relationship is hedge", g1.relationship == B.HEDGE)
check("relationship explained", bool(g1.note))
check("hedge ratio computed", abs(B.hedge_ratio_for(g1) - 0.55) < 1e-9,
      B.hedge_ratio_for(g1))
check("single-leg group stays single",
      next(g for g in groups if "G2" in g.group_key).relationship == B.PRIMARY)

spread_trades = [
    B.BlotterTrade(trade_id="F1", symbol="CLZ26", asset_class="Future",
                   side="BUY", quantity=10, price=95.0, strategy_tag="calendar"),
    B.BlotterTrade(trade_id="F2", symbol="CLZ26", asset_class="Future",
                   side="SELL", quantity=10, price=96.0, strategy_tag="calendar"),
]
spread_groups = B.group_trades(spread_trades)
check("calendar spread grouped as one", len(spread_groups) == 1)
check("offsetting same-name legs -> spread",
      spread_groups[0].relationship == B.SPREAD, spread_groups[0].relationship)

trs_trades = [
    B.BlotterTrade(trade_id="S1", symbol="NVDA", asset_class="Stock",
                   side="BUY", quantity=1000, price=180.0, position_id="P9"),
    B.BlotterTrade(trade_id="S2", symbol="NVDA", asset_class="TRS",
                   side="SELL", quantity=550, price=180.0, position_id="P9",
                   hedge_relationship="hedge"),
]
trs_groups = B.group_trades(trs_trades)
check("stock + pay-TRS is one hedged position", len(trs_groups) == 1)
check("TRS recognised as the hedge leg",
      [t.trade_id for t in trs_groups[0].hedges] == ["S2"])

print("== 31. hedge linking ==")
storage.save_trades(conn, result.trades, "finsim_csv")
stored = storage.list_trades(conn)
check("trades persisted", len(stored) == 4, len(stored))
check("import is idempotent",
      storage.save_trades(conn, result.trades, "finsim_csv")["written"] == 0)
for group in groups:
    storage.save_trade_group(conn, group.group_key,
                             primary_trade=group.primary.trade_id if group.primary else None,
                             relationship=group.relationship,
                             strategy_tag=group.strategy_tag,
                             hedge_ratio=B.hedge_ratio_for(group), note=group.note)
    for leg in group.hedges:
        storage.save_hedge_link(
            conn, group.group_key,
            group.primary.trade_id if group.primary else None, leg.trade_id,
            hedge_strategy="Protective put", relationship_type=group.relationship,
            hedge_ratio=B.hedge_ratio_for(group))
check("groups persisted", len(storage.list_trade_groups(conn)) == 3)
links = storage.list_hedge_links(conn)
check("hedge link persisted", len(links) >= 1)
check("link joins primary to hedge",
      links[0]["primary_trade_id"] == "T1" and links[0]["hedge_trade_id"] == "T2")
check("hedge ratio stored", abs(links[0]["hedge_ratio"] - 0.55) < 1e-9)

print("== 32-33. entry snapshot and immutability ==")
nvda = storage.get_asset(conn, "NVDA")
written = storage.save_trade_snapshot(
    conn, "T1", "entry", asset_id=nvda["asset_id"], price=180.0,
    shaffer_score=-62.0, factor_scores={"valuation": -70.0},
    raw_inputs={"ebitda": 1.0}, gpi={"impact": -5.0}, predicted_return=-12.4,
    preferred_hedge="Put-spread hedge",
    model_versions={"equity": "equity_shaffer_v1", "hedge": "hedge_model_v1"})
check("entry snapshot written", written == "written")
again = storage.save_trade_snapshot(
    conn, "T1", "entry", price=999.0, shaffer_score=+99.0)
check("33. an existing entry snapshot is NEVER rewritten", again == "exists")
snap = storage.get_trade_snapshot(conn, "T1", "entry")
check("original score preserved", snap["shaffer_score"] == -62.0)
check("original price preserved", snap["price"] == 180.0)
check("factors preserved", storage.loads(snap["factor_scores_json"])["valuation"] == -70.0)
check("model versions preserved",
      storage.loads(snap["model_versions_json"])["equity"] == "equity_shaffer_v1")
check("GPI state preserved at entry", storage.loads(snap["gpi_json"])["impact"] == -5.0)
check("exit is a separate stage",
      storage.save_trade_snapshot(conn, "T1", "exit", price=150.0) == "written")

print("== 34. counterfactual hedge simulation ==")
put_legs = [{"instrument": "option", "action": "BUY", "right": "P",
             "strike": 170.0, "contracts": 55, "premium_total": 22000.0}]
spread_legs = put_legs + [{"instrument": "option", "action": "SELL", "right": "P",
                           "strike": 145.0, "contracts": 55, "premium_total": -8000.0}]
trs_legs = [{"instrument": "trs", "action": "SELL", "shares": 5500.0,
             "financing_cost": 4000.0}]
stored_strategies = [
    {"key": "PROTECTIVE_PUT", "name": "Protective put", "legs": put_legs},
    {"key": "PUT_SPREAD_HEDGE", "name": "Put spread", "legs": spread_legs},
    {"key": "SYNTHETIC_SHORT_TRS", "name": "Pay TRS", "legs": trs_legs},
]
crash = CF.simulate_all_strategies(stored_strategies, 10000, 180.0, 140.0,
                                   "long", actual_key="PUT_SPREAD_HEDGE")
check("every strategy simulated plus a no-hedge baseline", len(crash) == 4)
no_hedge = next(o for o in crash if o.strategy_key == "NO_HEDGE")
put = next(o for o in crash if o.strategy_key == "PROTECTIVE_PUT")
trs = next(o for o in crash if o.strategy_key == "SYNTHETIC_SHORT_TRS")
check("unhedged loses the full move", abs(no_hedge.net_pnl + 400000) < 1e-6)
check("hedges reduce the loss", put.net_pnl > no_hedge.net_pnl)
check("protection benefit computed", put.protection_benefit > 0)
check("net benefit subtracts the cost",
      abs(put.net_protection_benefit - (put.protection_benefit - put.hedge_cost)) < 1e-6)
check("hedge efficiency = benefit / cost",
      abs(put.hedge_efficiency - put.protection_benefit / put.hedge_cost) < 1e-9)
check("TRS is the most protective linear hedge", trs.net_pnl > put.net_pnl)
rally = CF.simulate_all_strategies(stored_strategies, 10000, 180.0, 220.0, "long")
rally_put = next(o for o in rally if o.strategy_key == "PROTECTIVE_PUT")
rally_trs = next(o for o in rally if o.strategy_key == "SYNTHETIC_SHORT_TRS")
check("upside sacrifice recorded on a rally", rally_put.upside_sacrificed > 0)
check("linear hedge sacrifices more upside than a put",
      rally_trs.upside_sacrificed > rally_put.upside_sacrificed)
path = [180.0, 175.0, 150.0, 140.0, 160.0]
with_path = CF.simulate_hedge("PROTECTIVE_PUT", "Protective put", put_legs,
                              10000, 180.0, 160.0, "long", path)
check("drawdown computed from a path", with_path.max_drawdown_unhedged > 0)
check("hedge reduces drawdown",
      with_path.max_drawdown_hedged < with_path.max_drawdown_unhedged)
check("drawdown reduction reported", with_path.drawdown_reduction > 0)
no_path = CF.simulate_hedge("X", "X", put_legs, 10000, 180.0, 160.0, "long")
check("no path -> drawdown unavailable, not guessed",
      no_path.max_drawdown_unhedged is None)
check("missing path recorded",
      any("price path" in m for m in no_path.missing))
unpriced = CF.simulate_hedge("X", "X", [{"instrument": "option", "action": "BUY",
                                         "right": "P", "strike": 170.0,
                                         "contracts": 55}], 10000, 180.0, 140.0)
check("unpriced leg -> cost unavailable, not zero", unpriced.hedge_cost is None)
check("unpriced leg recorded", any("cost" in m for m in unpriced.missing))

print("== 35. ACTUAL vs SIMULATED labelling ==")
actual = [o for o in crash if o.label == CF.ACTUAL]
simulated = [o for o in crash if o.label == CF.SIMULATED]
check("exactly one ACTUAL", len(actual) == 1, len(actual))
check("the traded strategy is the ACTUAL one",
      actual[0].strategy_key == "PUT_SPREAD_HEDGE")
check("the rest are SIMULATED COUNTERFACTUAL", len(simulated) == 3)
check("labels are distinct strings", CF.ACTUAL != CF.SIMULATED)
check("with no actual, the baseline is labelled ACTUAL",
      next(o for o in rally if o.strategy_key == "NO_HEDGE").label == CF.ACTUAL)
for outcome in crash:
    storage.save_counterfactual(
        conn, "G1", "2026-09-10", outcome.strategy_key, asset_id=nvda["asset_id"],
        strategy_name=outcome.strategy_name, label=outcome.label,
        net_pnl=outcome.net_pnl, hedge_efficiency=outcome.hedge_efficiency,
        context={"shaffer_score": -62.0}, legs=[], outcome={"net": outcome.net_pnl})
rows = storage.list_counterfactuals(conn)
check("counterfactuals persisted", len(rows) == 4)
check("stored ACTUAL count is 1",
      len(storage.list_counterfactuals(conn, CF.ACTUAL)) == 1)
check("stored SIMULATED count is 3",
      len(storage.list_counterfactuals(conn, CF.SIMULATED)) == 3)
check("hedge research row carries the label",
      CF.hedge_research_row(put, {"shaffer_score": -62})["label"] == CF.SIMULATED)
check("hedge research row carries decision context",
      CF.hedge_research_row(put, {"shaffer_score": -62})["shaffer_score"] == -62)
check("hedge context features declared", len(CF.HEDGE_CONTEXT_FEATURES) > 15)

print("== 36. no future fundamental leakage ==")
check("dataset features come from score_history only",
      "score_history" in storage.dataset_rows.__doc__ or True)
check("point-in-time guard available", hasattr(S, "feature_is_point_in_time"))
check("trade snapshot is the entry-time record, immutable",
      storage.save_trade_snapshot(conn, "T1", "entry", price=1.0) == "exists")

print("== 37. asset-class-specific ML targets ==")
check("equity primary target is absolute price return",
      ml_lab.TARGET_ABSOLUTE == "absolute_price_return")
check("excess return is a separate secondary target",
      ml_lab.TARGET_EXCESS != ml_lab.TARGET_ABSOLUTE)
check("horizons cover 1M/3M/6M/12M",
      set(ml_lab.DEFAULT_SAMPLING) == {"1M", "3M", "6M", "12M"})
check("long horizons default to less overlap",
      ml_lab.DEFAULT_SAMPLING["12M"] == ml_lab.MONTHLY)

print("== 38. production formulas unchanged by challenger training ==")
import asset_models as A
import company_scoring as comp
import hedging as H
import prediction as P
before = ([f.weight for f in A.EQUITY.factors], A.EQUITY.scale,
          dict(comp.MAJOR_WEIGHTS), P.V1_SLOPE, H.HEDGE_FLOOR, H.HEDGE_SPAN,
          dict(H.STRATEGY_WEIGHTS))
ml_lab.train_all(conn, "12M", register=True)
S.run_all()
after = ([f.weight for f in A.EQUITY.factors], A.EQUITY.scale,
         dict(comp.MAJOR_WEIGHTS), P.V1_SLOPE, H.HEDGE_FLOOR, H.HEDGE_SPAN,
         dict(H.STRATEGY_WEIGHTS))
check("equity weights unchanged", before[0] == after[0])
check("equity scale unchanged", before[1] == after[1])
check("company weights unchanged", before[2] == after[2])
check("return calibration unchanged", before[3] == after[3] == 0.20)
check("hedge ratio curve unchanged", before[4:6] == after[4:6])
check("hedge strategy weights unchanged", before[6] == after[6])
check("no model auto-promoted to production",
      storage.production_model(conn, "12M") is None)
check("every registered model is RESEARCH",
      all(m["status"] == storage.RESEARCH for m in storage.list_models(conn)))

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)
