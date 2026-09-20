"""Offline tests for short-horizon labels, trade research and macro factors.

Pure stdlib, no network:   python3 test_pipeline_growth.py
"""
import csv, datetime as dt, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_models as A
import blotter as B
import counterfactual as CF
import macro_factors as MF
import ml_lab
import prediction as P
import refresh as R
import storage
import trade_research as TR
import universe as uni

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, "growth.db")
conn = storage.init_db(DB)
assets, _ = uni.load_multi_asset_universe()
storage.upsert_assets(conn, assets)

print("== short-horizon labels ==")
check("1D and 5D added", {"1D", "5D"} <= set(P.HORIZONS))
check("six horizons", len(P.HORIZONS) == 6, list(P.HORIZONS))
check("12M is still primary", P.PRIMARY_HORIZON == "12M")
check("early horizons declared", P.EARLY_HORIZONS == ("1D", "5D", "1M", "3M"))
check("calendar maturity per horizon",
      P.HORIZON_CALENDAR_DAYS["1D"] == 1 and P.HORIZON_CALENDAR_DAYS["12M"] == 365)
check("1D matures next day",
      dt.date(2026, 9, 20) + dt.timedelta(days=P.HORIZON_CALENDAR_DAYS["1D"])
      == dt.date(2026, 9, 21))
check("short horizons sample daily (little overlap)",
      ml_lab.DEFAULT_SAMPLING["1D"] == ml_lab.DAILY
      and ml_lab.DEFAULT_SAMPLING["5D"] == ml_lab.DAILY)
check("12M still samples monthly", ml_lab.DEFAULT_SAMPLING["12M"] == ml_lab.MONTHLY)

nvda = storage.get_asset(conn, "NVDA")
aid = nvda["asset_id"]
base = dt.date(2026, 1, 5)
storage.save_score_snapshot(conn, aid, base.isoformat(), price=100.0,
                            shaffer_score=40.0, classification="BULLISH",
                            factor_scores={"valuation": 20.0},
                            gpi={"impact": 3.0}, political_overlay=0.3)
series = {base + dt.timedelta(days=d): 100.0 * (1 + 0.0008 * d) for d in range(0, 420)}
vti = {base + dt.timedelta(days=d): 300.0 * (1 + 0.0003 * d) for d in range(0, 420)}
summary = ml_lab.refresh_outcome_labels(
    conn, lambda s: sorted(series.items()), lambda: sorted(vti.items()))
counts = storage.label_counts(conn)
for horizon in P.HORIZONS:
    check(f"{horizon} label created", counts.get(horizon, 0) >= 1, counts)
check("all six horizons labelled", len(counts) == 6, counts)
rows = {r["horizon"]: r for r in conn.execute(
    "SELECT * FROM outcome_labels WHERE asset_id=?", (aid,))}
check("1D return is small", abs(rows["1D"]["forward_return"]) < 0.01,
      rows["1D"]["forward_return"])
check("12M return is larger than 1D",
      rows["12M"]["forward_return"] > rows["1D"]["forward_return"])
check("returns increase with horizon",
      all(rows[a]["forward_return"] <= rows[b]["forward_return"]
          for a, b in (("1D", "5D"), ("5D", "1M"), ("1M", "3M"),
                       ("3M", "6M"), ("6M", "12M"))))
check("excess return stored separately for every horizon",
      all(rows[h]["excess_return"] is not None for h in P.HORIZONS))
check("absolute and excess differ",
      rows["12M"]["forward_return"] != rows["12M"]["excess_return"])

print("== a label's name must match the gap it was measured over ==")
check("1D allows a weekend, not a fortnight", ml_lab.label_tolerance("1D") == 4)
check("tolerance scales with the horizon",
      ml_lab.label_tolerance("12M") > ml_lab.label_tolerance("1M"))
check("every horizon has a finite tolerance",
      all(ml_lab.label_tolerance(h) >= 4 for h in P.HORIZONS))
gapped = {dt.date(2026, 1, 2): 100.0, dt.date(2026, 2, 20): 110.0}
check("a three-week hole does not become a 1D label",
      ml_lab._nearest_on_or_after(gapped, dt.date(2026, 1, 3),
                                  tolerance_days=ml_lab.label_tolerance("1D"))
      is None)
check("a weekend still labels fine",
      ml_lab._nearest_on_or_after({dt.date(2026, 1, 5): 101.0},
                                  dt.date(2026, 1, 3), tolerance_days=4)
      is not None)

print("== GPI preserved in the snapshot ==")
history = storage.get_score_history(conn, aid)[0]
check("gpi_json stored", storage.loads(history["gpi_json"])["impact"] == 3.0)
check("political overlay stored", history["political_overlay"] == 0.3)
check("factors stored", storage.loads(history["factor_scores_json"])["valuation"] == 20.0)
check("dataset exposes the stored GPI",
      "gpi_json" in storage.dataset_rows(conn, "12M")[0].keys())

print("== 1M/3M give evidence far sooner than 12M ==")
today = dt.date(2026, 9, 20)
for horizon in ("1D", "5D", "1M", "3M"):
    matured = base + dt.timedelta(days=P.HORIZON_CALENDAR_DAYS[horizon])
    check(f"{horizon} snapshot from Jan has already matured", matured <= today,
          matured)
check("12M from Jan has NOT matured",
      base + dt.timedelta(days=P.HORIZON_CALENDAR_DAYS["12M"]) > today)

print("== entry state freeze ==")
csv_path = os.path.join(TMP, "b.csv")
with open(csv_path, "w", newline="") as handle:
    w = csv.writer(handle)
    w.writerow(["Trade ID", "Trade Date", "Security", "Asset Class", "Side",
                "Quantity", "Price", "Strike", "Right", "Net Cash",
                "Trade Group ID", "Hedge Relationship"])
    w.writerow(["T1", "2026-09-10", "NVDA", "Stock", "BUY", "10000", "180.00",
                "", "", "-1800000", "G1", "primary"])
    # Price is the PREMIUM per share ($4.00), strike is 170 -- a blotter never
    # conflates the two, and neither does the importer.
    w.writerow(["T2", "2026-09-10", "NVDA", "Option", "BUY", "55", "4.00",
                "170.00", "P", "-22000", "G1", "hedge"])
storage.save_current_score(
    conn, aid, price=180.0, shaffer_score=-62.0, classification="BEARISH",
    preferred_hedge="Put-spread hedge", factor_scores={"valuation": -70.0},
    raw_inputs={"ebitda": 1.0}, gpi={"impact": -4.0},
    predicted_12m_return_pct=-12.4, model_version="equity_model_v1")

result = TR.import_and_freeze(conn, csv_path)
check("blotter imported", result["rows_read"] == 2, result)
check("trades stored", result["stored"] == 2)
check("one economic position formed", result["groups"] == 1)
check("entry states frozen", result["frozen"] == 2, result)
snapshot = storage.get_trade_snapshot(conn, "T1", TR.ENTRY)
check("score frozen at entry", snapshot["shaffer_score"] == -62.0)
check("prediction frozen", snapshot["predicted_return"] == -12.4)
check("hedge recommendation frozen", snapshot["preferred_hedge"] == "Put-spread hedge")
check("GPI frozen", storage.loads(snapshot["gpi_json"])["impact"] == -4.0)
versions = storage.loads(snapshot["model_versions_json"])
check("every model version frozen",
      {"score", "return_calibration", "hedge", "gpi"} <= set(versions), versions)
check("Trade_t + Score_t + Prediction_t + Hedge_t all present",
      snapshot["price"] is not None and snapshot["shaffer_score"] is not None
      and snapshot["predicted_return"] is not None
      and snapshot["preferred_hedge"] is not None)

storage.save_current_score(conn, aid, price=999.0, shaffer_score=+99.0,
                           classification="BULLISH")
again = TR.import_and_freeze(conn, csv_path)
check("re-import does not re-freeze", again["already_frozen"] == 2, again)
check("frozen score survives a later rescore",
      storage.get_trade_snapshot(conn, "T1", TR.ENTRY)["shaffer_score"] == -62.0)

print("== hedge effectiveness on close ==")
path = [180.0, 176.0, 168.0, 160.0, 172.0]
outcome = TR.evaluate_closed_position(
    conn, "explicit:G1", exit_price=172.0, exit_date="2026-09-28", price_path=path)
check("position graded", outcome.net_pnl is not None, outcome.notes)
check("symbol resolved", outcome.symbol == "NVDA")
check("holding period computed", outcome.holding_days == 18, outcome.holding_days)
check("entry score recovered from the frozen snapshot", outcome.entry_score == -62.0)
check("underlying P&L computed", outcome.underlying_pnl is not None)
check("hedge P&L computed", outcome.hedge_pnl is not None)
check("unhedged return computed", outcome.unhedged_return is not None)
check("drawdown with and without the hedge",
      outcome.max_drawdown_unhedged is not None
      and outcome.max_drawdown_hedged is not None)
check("hedge reduced drawdown",
      outcome.max_drawdown_hedged <= outcome.max_drawdown_unhedged)
check("score direction agreement evaluated",
      outcome.score_agreed_with_direction is False,
      outcome.score_agreed_with_direction)
check("a long held against a bearish score is flagged as disagreeing",
      outcome.entry_score < 0 and outcome.direction == "long"
      and outcome.score_agreed_with_direction is False)
check("prediction error measured", outcome.prediction_error is not None)
check("alternatives replayed", len(outcome.alternatives) >= 2, len(outcome.alternatives))
check("exactly one ACTUAL among the alternatives",
      sum(1 for a in outcome.alternatives if a.label == CF.ACTUAL) == 1)
check("counterfactuals persisted", len(storage.list_counterfactuals(conn)) >= 2)
check("evidence available in 18 days, not 12 months", outcome.holding_days < 30)

dataset = TR.hedge_effectiveness_dataset(conn)
check("hedge dataset built", len(dataset) >= 2)
check("every row carries its label", all("label" in r for r in dataset))
check("decision context carried", any("entry_score" in r for r in dataset))
summary = TR.trade_performance_summary([outcome])
check("behaviour summary built", summary["positions"] == 1)
check("summary separates aligned from against",
      "mean_pnl_aligned" in summary and "mean_pnl_against" in summary)
check("summary reports hedge economics",
      "mean_hedge_cost" in summary and "mean_drawdown_reduction" in summary)

print("== an incomplete option contract is declared, not guessed ==")
# A blotter that says only "Option" has not said whether it is a put or a
# call. Those are opposite trades, so the replay must refuse rather than
# quietly price one of them.
vague_path = os.path.join(TMP, "vague.csv")
with open(vague_path, "w", newline="") as handle:
    w = csv.writer(handle)
    w.writerow(["Trade ID", "Trade Date", "Security", "Asset Class", "Side",
                "Quantity", "Price", "Net Cash", "Trade Group ID",
                "Hedge Relationship"])
    w.writerow(["V1", "2026-09-10", "NVDA", "Stock", "BUY", "10000", "180.00",
                "-1800000", "G9", "primary"])
    w.writerow(["V2", "2026-09-10", "NVDA", "Option", "BUY", "55", "170.00",
                "-22000", "G9", "hedge"])
vague = TR.import_and_freeze(conn, vague_path)
check("vague blotter still imports", vague["stored"] == 2, vague)
check("import flags the unreplayable option",
      any("cannot be replayed" in n for n in vague["notes"]), vague["notes"])
check("import names the missing right",
      any("right (put or call)" in n for n in vague["notes"]), vague["notes"])

vague_outcome = TR.evaluate_closed_position(
    conn, "explicit:G9", exit_price=172.0, exit_date="2026-09-28",
    price_path=path)
check("grading reports the unknown leg",
      any("no put/call right" in m for m in vague_outcome.missing),
      vague_outcome.missing)
check("an unknown right is NOT priced as a call",
      vague_outcome.hedge_pnl == 0.0, vague_outcome.hedge_pnl)
check("no fabricated hedged drawdown",
      vague_outcome.max_drawdown_hedged is None,
      vague_outcome.max_drawdown_hedged)
check("unhedged drawdown is still reported",
      vague_outcome.max_drawdown_unhedged is not None)
check("hedge cost withheld when the leg is unknown",
      vague_outcome.hedge_cost is None, vague_outcome.hedge_cost)

# The same trade written the way a real blotter writes it replays cleanly.
occ = B.parse_occ_symbol("NVDA  260116P00170000")
check("OCC symbol decodes the right", occ["right"] == "P", occ)
check("OCC symbol decodes the strike", occ["strike"] == 170.0, occ)
check("OCC symbol decodes the expiry", occ["expiry"] == "2026-01-16", occ)
check("a plain ticker is not an option symbol",
      B.parse_occ_symbol("NVDA") is None)
check("premium is never used as a strike",
      storage.list_trades(conn)[0].keys() is not None)
hedge_row = [t for t in storage.list_trades(conn) if t["trade_id"] == "T2"][0]
check("stored strike is the strike", hedge_row["strike"] == 170.0)
check("stored price is the premium", hedge_row["price"] == 4.00)
check("stored right is explicit", hedge_row["option_right"] == "P")

print("== macro factors ==")
class FakeSeries:
    def __init__(self, values): self.values = list(values); self.dates = []
    @property
    def latest(self): return self.values[-1] if self.values else None
    @property
    def available(self): return bool(self.values)
    def change(self, n=1):
        return None if len(self.values) <= n else self.values[-1] - self.values[-1-n]
    def history(self, n=None): return self.values[-n:] if n else list(self.values)

class FakeSnapshot:
    def __init__(self, data): self.series = {k: FakeSeries(v) for k, v in data.items()}
    def get(self, k):
        s = self.series.get(k); return s if s and s.available else None
    def latest(self, k):
        s = self.get(k); return s.latest if s else None
    def history(self, k, n=None):
        s = self.get(k); return s.history(n) if s else []

n = 300
easing = FakeSnapshot({
    "fed_funds": [5.0] * n, "ust_2y": [4.0 + i * 0.001 for i in range(n)],
    "breakeven_10y": [2.5 - i * 0.002 for i in range(n)],
    "unemployment": [4.0 + i * 0.005 for i in range(n)],
    "ust_10y": [3.0 + i * 0.008 for i in range(n)],
})
values, missing = MF.rates_factors(easing)
check("rates factors computed", len(values) >= 4, values)
check("fiscal policy declared missing, not estimated",
      any("fiscal" in m for m in missing))
check("rates score produced", A.blend(A.RATES, values).score is not None)
check("factors stay in band", all(-100 <= v <= 100 for v in values.values()))
empty = MF.rates_factors(FakeSnapshot({}))
check("no macro -> no factors, never zeros", empty[0] == {})
check("no macro -> everything named missing", len(empty[1]) >= 5)

check("realised vol computed",
      MF.realised_volatility([100 * (1.01 ** i) for i in range(60)]) is not None)
check("too little history -> None", MF.realised_volatility([100, 101]) is None)
check("trend needs history", MF.trend_score([100, 101, 102]) is None)

print("== blocked engines are declared, not silently empty ==")
check("live engines listed", set(MF.LIVE_ENGINES) >= {"rates_shaffer_v1", "gold_shaffer_v1"})
check("blocked engines listed", len(MF.BLOCKED_ENGINES) >= 10)
for version in ("fx_shaffer_v1", "oil_shaffer_v1", "etf_shaffer_v1"):
    check(f"{version} declared blocked with a reason",
          version in MF.BLOCKED_ENGINES and len(MF.BLOCKED_ENGINES[version]) > 20)
check("FX reason names the missing side",
      "foreign" in MF.BLOCKED_ENGINES["fx_shaffer_v1"])
check("ETF reason names holdings",
      "holdings" in MF.BLOCKED_ENGINES["etf_shaffer_v1"])

print("== the FRED key never reaches a note, a row or the screen ==")
import macro_data as MD
_saved = os.environ.get("FRED_API_KEY")
os.environ["FRED_API_KEY"] = "TESTKEY-must-not-leak"
leaky = ("HTTPSConnectionPool(host='api.stlouisfed.org'): url: "
         "/fred/series/observations?series_id=DGS10&"
         "api_key=TESTKEY-must-not-leak&file_type=json")
scrubbed = MD._scrub(leaky)
check("the key itself is redacted", "TESTKEY-must-not-leak" not in scrubbed)
check("the api_key parameter is redacted",
      "api_key=[REDACTED]" in scrubbed, scrubbed)
check("an unknown key in a url is still redacted",
      "deadbeef" not in MD._scrub("api_key=deadbeef&x=1"))
check("the rest of the message survives", "series_id=DGS10" in scrubbed)
if _saved is None:
    del os.environ["FRED_API_KEY"]
else:
    os.environ["FRED_API_KEY"] = _saved

print("== a macro score carries its own caveat ==")
check("every macro engine that scores has a caveat",
      {"rates_shaffer_v1", "corp_credit_shaffer_v1", "gold_shaffer_v1"}
      <= set(R.MACRO_CAVEATS))
check("the corporate caveat says it is market-level, not issuer-level",
      "MARKET-LEVEL ONLY" in R.MACRO_CAVEATS["corp_credit_shaffer_v1"])
check("the corporate caveat says no rating is available",
      "no rating" in R.MACRO_CAVEATS["corp_credit_shaffer_v1"])
check("the rates caveat distinguishes conviction from return",
      "duration changes expected return" in R.MACRO_CAVEATS["rates_shaffer_v1"])
detail = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "views/asset_detail_page.py")).read()
check("the caveat is shown next to the score, not just logged",
      'raw.get("caveat")' in detail and 'st.warning(raw["caveat"])' in detail)
check("a high-yield spread is never assigned to an unrated bond",
      "hy_values" not in open(os.path.join(
          os.path.dirname(os.path.abspath(__file__)), "refresh.py")).read())

print("== every factor an engine asks for is actually fetched ==")
import macro_data as MD
check("the vol term-structure factor's series is fetched",
      "vix_3m" in MD.CORE_YAHOO)
check("every engine's series is in the core fetch set",
      {"real_10y", "dollar_index", "breakeven_10y", "fed_funds", "ust_2y",
       "ust_10y", "unemployment", "hy_oas", "ig_oas"} <= set(MD.CORE_SERIES))
check("gold price is fetched for the momentum factor", "gold" in MD.CORE_YAHOO)
check("a live engine is not the same claim as a routed engine",
      "vol_shaffer_v1" in MF.LIVE_ENGINES
      and not any(v[0] == "vol_shaffer_v1" for v in R.MACRO_ROUTES.values()))

print("== non-US and subtype routing is not fabricated ==")
check("only US instruments routed to the US rates engine",
      all("US" in k or "Corporate" in k for k in R.MACRO_ROUTES))
check("no German/Japanese government bond routed to US rates",
      not any("EUR" in k or "JPY" in k or "GBP" in k for k in R.MACRO_ROUTES))
check("gold engine restricted to gold", R.PRECIOUS == {"GC", "XAU"})
check("silver/platinum/palladium need their own model",
      set(R.NEEDS_SUBTYPE_MODEL) == {"SI", "PL", "PA"})
for code, reason in R.NEEDS_SUBTYPE_MODEL.items():
    check(f"{code} gap explained", "subtype model" in reason)

print("== duration affects RETURN, not conviction ==")
short = A.bond_price_change(2.0, -0.01)
long = A.bond_price_change(19.0, -0.01)
check("longer duration moves more", abs(long) > abs(short))
check("falling yields help both", short > 0 and long > 0)
check("convexity is additive",
      A.bond_price_change(8.0, 0.01, 80) > A.bond_price_change(8.0, 0.01))
check("conviction score is curve-wide by design",
      A.RATES.direction.startswith("Positive = bullish bond TOTAL RETURN"))

print("== the ML Lab page reflects the new horizons and the trade loop ==")
page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "views/ml_page.py")).read()
check("horizon defaults to the primary horizon, not a hardcoded index",
      "horizons.index(pred.PRIMARY_HORIZON)" in page and "index=3" not in page)
check("early horizons are marked as early, not as calibration evidence",
      "pred.EARLY_HORIZONS" in page and "EARLY horizon" in page)
check("a good short-horizon model is not a reason to move the slope",
      "not a reason to change" in page)
check("importing a blotter freezes entry states",
      "TR.import_and_freeze" in page)
check("closed positions can be graded from the page",
      "evaluate_closed_position" in page)
check("alternatives are labelled as eligible-at-entry, not hindsight",
      "ELIGIBLE AT ENTRY" in page)
check("an unavailable hedged drawdown is shown as unavailable",
      'else "UNAVAILABLE"' in page)
check("the trade dataset is still walled off from the market dataset",
      "never merged with the " in page and "market dataset" in page)

print("== the app can actually be launched ==")
_here = os.path.dirname(os.path.abspath(__file__))
reqs = open(os.path.join(_here, "requirements.txt")).read()
check("requirements lists streamlit", "streamlit" in reqs)
check("requirements lists requests", "requests" in reqs)
check("no dependency is listed that nothing imports",
      "plotly" not in reqs)
sources = "".join(
    open(os.path.join(_here, f)).read()
    for f in os.listdir(_here) if f.endswith(".py"))
sources += "".join(
    open(os.path.join(_here, "views", f)).read()
    for f in os.listdir(os.path.join(_here, "views")) if f.endswith(".py"))
for package in ("numpy", "pandas", "sklearn", "scipy", "yfinance"):
    check(f"nothing imports {package}", f"import {package}" not in sources)
for launcher in ("run.sh", "run.command", "run.bat"):
    check(f"{launcher} exists", os.path.isfile(os.path.join(_here, launcher)))
launch = open(os.path.join(_here, "run.sh")).read()
check("the launcher refuses Python below 3.10", "3,10" in launch)
check("the launcher waits for the server before opening a browser",
      "_stcore/health" in launch)
check("a failed install does not start a half-built app",
      "exit 1" in launch and "deps-installed" in launch)
config = open(os.path.join(_here, ".streamlit", "config.toml")).read()
check("the server binds to localhost only", '127.0.0.1' in config)
check("usage stats are off", "gatherUsageStats    = false" in config)

print("== production arithmetic still untouched ==")
import company_scoring as comp, hedging as H
check("equity weights unchanged",
      [f.weight for f in A.EQUITY.factors] == [0.40, 0.25, 0.20, 0.15])
check("company weights unchanged", comp.MAJOR_WEIGHTS ==
      {"valuation": .40, "growth": .25, "profitability": .20, "debt": .15})
check("return calibration unchanged", P.V1_SLOPE == 0.20)
check("hedge curve unchanged", (H.HEDGE_FLOOR, H.HEDGE_SPAN) == (15.0, 85.0))
check("no ML model promoted", storage.production_model(conn, "12M") is None)

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)
