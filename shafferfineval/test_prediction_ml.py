"""Offline tests for prediction.py, mllib.py and ml_lab.py.

Pure stdlib, no network:   python3 test_prediction_ml.py
"""
import datetime as dt, math, os, random, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mllib
import ml_lab
import prediction as P
import storage
import universe as uni

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

print("== 1-5. V1 score-to-return mapping ==")
for score, expected in [(100, 20.0), (50, 10.0), (25, 5.0), (0, 0.0),
                        (-25, -5.0), (-50, -10.0), (-100, -20.0)]:
    got = P.predicted_return_pct(score)
    check(f"score {score:+4} -> {expected:+.1f}%", abs(got - expected) < 1e-9, got)
check("slope is exactly 0.20", P.V1_SLOPE == 0.20)
check("intercept is zero", P.V1_INTERCEPT == 0.0)
check("no score -> None, not zero", P.predicted_return_pct(None) is None)
check("nan -> None", P.predicted_return_pct(float('nan')) is None)
check("linear throughout",
      all(abs(P.predicted_return_pct(s) - 0.20 * s) < 1e-12 for s in range(-100, 101)))

print("== 6-7. predicted price ==")
check("spec example: $180 @ +60 -> $201.60",
      abs(P.predicted_price(180.0, 12.0) - 201.60) < 1e-9)
check("negative return lowers the price",
      abs(P.predicted_price(100.0, -20.0) - 80.0) < 1e-9)
check("zero return keeps the price", P.predicted_price(100.0, 0.0) == 100.0)
check("no price -> None", P.predicted_price(None, 10.0) is None)
check("no return -> None", P.predicted_price(100.0, None) is None)
check("zero price -> None", P.predicted_price(0.0, 10.0) is None)
full = P.build_prediction(60, 180.0)
check("prediction record complete",
      full.predicted_return_pct == 12.0 and abs(full.predicted_price - 201.6) < 1e-9)
check("model tagged", full.model == "shaffer_score_linear")
check("version tagged", full.model_version == "v1_0.20")
check("uncertainty NOT fabricated",
      full.lower_return_pct is None and full.upper_return_pct is None)
check("uncalibrated label shown", "UNCERTAINTY NOT YET CALIBRATED" in full.uncertainty_note)
banded = P.build_prediction(60, 180.0, interval=(-7.0, 26.0))
check("interval used when supplied", banded.lower_return_pct == -7.0)
check("label cleared once calibrated", banded.uncertainty_note == "")

print("== 11. absolute return kept separate from VTI excess ==")
check("forward return", abs(P.forward_return(100.0, 112.0) - 0.12) < 1e-12)
check("excess = stock - benchmark",
      abs(P.excess_return(0.12, 0.05) - 0.07) < 1e-12)
check("excess needs both", P.excess_return(0.12, None) is None)
check("zero base -> None", P.forward_return(0.0, 10.0) is None)
check("prediction module never mixes them",
      P.predicted_return_pct(50) == 10.0)

DB = os.path.join(tempfile.mkdtemp(), "ml.db")
conn = storage.init_db(DB)

print("== 8 & 30. historical predictions are immutable ==")
assets, _ = uni.load_multi_asset_universe()
storage.upsert_assets(conn, assets[:60])
nvda = storage.get_asset(conn, "NVDA") or storage.list_assets(conn)[0]
aid = nvda["asset_id"]
day = "2026-09-20"
storage.save_score_snapshot(conn, aid, day, price=180.0, shaffer_score=60.0,
                            classification="BULLISH", factor_scores={"valuation": 10.0})
first = storage.save_prediction_fields(
    conn, aid, day, predicted_return_pct=12.0, predicted_price=201.6,
    model="shaffer_score_linear", model_version="v1_0.20")
check("prediction written", first == "written")
second = storage.save_prediction_fields(
    conn, aid, day, predicted_return_pct=7.4, predicted_price=193.3,
    model="ml_linear", model_version="v2_learned")
check("a newer calibration does NOT rewrite it", second == "exists")
row = storage.get_score_history(conn, aid)[0]
check("original return preserved", row["predicted_12m_return_pct"] == 12.0)
check("original model preserved", row["prediction_model_version"] == "v1_0.20")

print("== 9-10. outcome labels at every horizon ==")
base = dt.date(2026, 9, 20)
series = {base + dt.timedelta(days=d): 180.0 * (1 + 0.0004 * d) for d in range(0, 500)}
vti = {base + dt.timedelta(days=d): 300.0 * (1 + 0.0002 * d) for d in range(0, 500)}
summary = ml_lab.refresh_outcome_labels(
    conn, price_history_fn=lambda s: sorted(series.items()),
    vti_history_fn=lambda: sorted(vti.items()))
counts = storage.label_counts(conn)
for horizon in ("1M", "3M", "6M", "12M"):
    check(f"{horizon} label created", counts.get(horizon, 0) >= 1, counts)
label = conn.execute(
    "SELECT * FROM outcome_labels WHERE asset_id=? AND horizon='12M'", (aid,)).fetchone()
check("forward return computed", label["forward_return"] is not None)
check("forward return is absolute price return",
      abs(label["forward_return"] - (label["future_price"] / 180.0 - 1)) < 1e-9)
check("VTI return stored separately", label["vti_forward_return"] is not None)
check("excess = stock - vti",
      abs(label["excess_return"] -
          (label["forward_return"] - label["vti_forward_return"])) < 1e-9)
check("absolute != excess", label["forward_return"] != label["excess_return"])

print("== 12. no future-data leakage in features ==")
rows = storage.dataset_rows(conn, "12M")
check("dataset reads score_history only", len(rows) >= 1)
check("features come from the stored snapshot",
      rows[0]["factor_scores_json"] is not None)
check("label is separate from features",
      "forward_return" in rows[0].keys() and rows[0]["forward_return"] is not None)
check("snapshot date precedes the outcome date",
      rows[0]["snapshot_date"] < rows[0]["future_date"])

print("== 13-14. walk-forward splits ==")
dates = [f"2026-{m:02d}-01" for m in range(1, 13)] * 5
dates.sort()
splits = ml_lab.walk_forward_splits(dates, n_folds=3, min_train=5)
check("splits produced", len(splits) >= 2, len(splits))
for train_idx, test_idx in splits:
    latest_train = max(dates[i] for i in train_idx)
    earliest_test = min(dates[i] for i in test_idx)
    if latest_train >= earliest_test:
        check("every train period precedes its test period", False,
              f"{latest_train} >= {earliest_test}")
        break
else:
    check("every train period strictly precedes its test period", True)
check("training window expands",
      all(len(splits[i][0]) < len(splits[i+1][0]) for i in range(len(splits)-1)))
check("no index appears in both halves",
      all(not (set(a) & set(b)) for a, b in splits))
check("too few dates -> no split", ml_lab.walk_forward_splits(["2026-01-01"]) == [])
check("random split is never used", "shuffle" not in
      open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "ml_lab.py")).read())

print("== 16-19. model training ==")
random.seed(3)
X = [[random.gauss(0, 1) for _ in range(4)] for _ in range(300)]
y = [1.5*r[0] - 0.8*r[1] + 0.3*r[2] + random.gauss(0, 0.25) for r in X]
names = ["a", "b", "c", "d"]
linear = mllib.LinearModel(0.0).fit(X, y, names)
check("17. linear trains", linear.fitted)
check("linear recovers coefficients",
      abs(linear.coefficients[0] - 1.5) < 0.1 and abs(linear.coefficients[1] + 0.8) < 0.1,
      linear.coefficients)
ridge = mllib.LinearModel(10.0).fit(X, y, names)
check("18. ridge trains", ridge.fitted)
check("ridge shrinks coefficients",
      abs(ridge.std_coefficients[0]) < abs(linear.std_coefficients[0]))
lasso = mllib.ElasticNetModel(0.25, 1.0).fit(X, y, names)
check("lasso trains", lasso.fitted)
check("lasso drops the noise feature", "d" not in lasso.selected_features(),
      lasso.selected_features())
enet = mllib.ElasticNetModel(0.05, 0.5).fit(X, y, names)
check("elastic net trains", enet.fitted)
gb = mllib.GradientBoostingModel(30, 0.1, 3).fit(X, y, names)
check("19. gradient boosting trains", gb.fitted and len(gb.trees) == 30)
check("gradient boosting predicts", mllib.r_squared(y, gb.predict(X)) > 0.8)
rf = mllib.RandomForestModel(15, 5).fit(X, y, names)
check("random forest trains", rf.fitted)
check("models are deterministic",
      mllib.GradientBoostingModel(30, 0.1, 3).fit(X, y, names).predict(X[:3])
      == gb.predict(X[:3]))

print("== 16. baseline model ==")
baseline = ml_lab.ShafferBaseline(score_index=0)
preds = baseline.predict([[50.0], [-50.0], [0.0]])
check("baseline is 0.20 x score as a fraction",
      abs(preds[0] - 0.10) < 1e-12 and abs(preds[1] + 0.10) < 1e-12
      and preds[2] == 0.0, preds)
check("baseline does not learn",
      baseline.fit([[1.0]], [99.0]).predict([[50.0]])[0] == 0.10)

print("== 21-24. metrics ==")
truth = [0.10, -0.05, 0.20, -0.15, 0.02]
guess = [0.08, -0.02, 0.25, -0.10, 0.01]
check("MAE", abs(mllib.mean_absolute_error(truth, guess) - 0.032) < 1e-9,
      mllib.mean_absolute_error(truth, guess))
check("RMSE positive", mllib.root_mean_squared_error(truth, guess) > 0)
check("R2 high for a good fit", mllib.r_squared(truth, guess) > 0.9)
check("24. spearman perfect for monotone",
      abs(mllib.spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-12)
check("spearman inverted", abs(mllib.spearman([1, 2, 3], [3, 2, 1]) + 1.0) < 1e-12)
check("spearman handles ties",
      mllib.spearman([1, 2, 2, 3], [1, 2, 2, 3]) is not None)
check("pearson matches for linear", abs(mllib.pearson([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-12)
check("23. direction accuracy",
      mllib.direction_accuracy([1, -1, 1, -1], [1, -1, -1, -1]) == 0.75)
check("direction ignores zero truth",
      mllib.direction_accuracy([0, 1], [1, 1]) == 1.0)
check("too few points -> None", mllib.spearman([1], [1]) is None)

print("== 25. quantile / decile analysis ==")
truth = [i / 100 for i in range(100)]
guess = list(truth)
buckets = mllib.quantile_buckets(truth, guess, 10)
check("ten buckets", len(buckets) == 10)
check("bucket 1 is the highest predicted",
      buckets[0]["mean_predicted"] > buckets[-1]["mean_predicted"])
check("perfect model is monotonic in actual",
      all(buckets[i]["mean_actual"] >= buckets[i+1]["mean_actual"]
          for i in range(len(buckets)-1)))
check("bucket sizes sum to n", sum(b["n"] for b in buckets) == 100)
check("empty input -> empty", mllib.quantile_buckets([], []) == [])
cal = mllib.calibration_buckets([0.03, 0.08, -0.02], [0.02, 0.07, -0.03])
check("calibration buckets built", len(cal) >= 1)
check("calibration reports both sides",
      all("mean_predicted" in c and "mean_actual" in c for c in cal))

print("== 21. factor importance ==")
imp = linear.importance()
check("importance returned", len(imp) == 4)
check("importance sums to 1", abs(sum(v for _, v in imp) - 1.0) < 1e-9)
check("strongest feature ranks first", imp[0][0] == "a", imp)
perm = mllib.permutation_importance(gb, X, y, names)
check("permutation importance returned", len(perm) == 4)
check("permutation importance normalised", abs(sum(v for _, v in perm) - 1.0) < 1e-9)

print("== 20 & 26. insufficient data and overlap handling ==")
empty = ml_lab.build_dataset(conn, "12M")
check("dataset builds without crashing", isinstance(empty, ml_lab.Dataset))
check("insufficient data reported honestly",
      empty.effective_observations < ml_lab.MIN_OBS_EXPERIMENTAL)
report = ml_lab.ModelReport(name="x", family="y", horizon="12M",
                            target=ml_lab.TARGET_ABSOLUTE, sampling="monthly")
check("status is INSUFFICIENT with no data",
      ml_lab.classify_status(empty, report) == ml_lab.INSUFFICIENT)
check("12M defaults to monthly sampling",
      ml_lab.DEFAULT_SAMPLING["12M"] == ml_lab.MONTHLY)
check("1M does not default to monthly", ml_lab.DEFAULT_SAMPLING["1M"] != ml_lab.MONTHLY)
daily_dates = [f"2026-{m:02d}-{d:02d}" for m in (1, 2) for d in (1, 8, 15, 22)]
check("monthly sampling collapses a month to one date",
      len(ml_lab._sample_dates(daily_dates, ml_lab.MONTHLY)) == 2)
check("weekly sampling keeps more than monthly",
      len(ml_lab._sample_dates(daily_dates, ml_lab.WEEKLY)) > 2)
check("daily sampling keeps everything",
      len(ml_lab._sample_dates(daily_dates, ml_lab.DAILY)) == len(set(daily_dates)))
_d, reports, calibration = ml_lab.train_all(conn, "12M", register=False)
check("training with no data returns reports, not a fake backtest",
      all(r.metrics.get("n", 0) == 0 or not r.folds for r in reports))
check("calibration refuses to invent a mapping with one date",
      calibration.beta is None or calibration.status == ml_lab.INSUFFICIENT)

print("== 22. score calibration regression ==")
class FakeDataset(ml_lab.Dataset):
    pass
synthetic = ml_lab.Dataset(horizon="12M")
synthetic.shaffer_scores = [float(s) for s in range(-100, 101, 5)]
# realised returns follow 0.14 x score (a different slope from production 0.20)
synthetic.y = [0.14 * s / 100.0 for s in synthetic.shaffer_scores]
synthetic.dates = [f"2026-{(i % 12) + 1:02d}-01" for i in range(len(synthetic.y))]
synthetic.X = [[s] for s in synthetic.shaffer_scores]
calib = ml_lab.estimate_calibration(synthetic)
check("beta recovered", abs(calib.beta - 0.14) < 1e-6, calib.beta)
check("alpha near zero", abs(calib.alpha) < 1e-6)
check("production slope retained for comparison", calib.production_slope == 0.20)
rows = calib.implied_at
check("comparison table built", len(rows) == 5)
check("production column uses V1",
      all(abs(p - 0.20 * s) < 1e-9 for s, p, _ in rows))
check("learned column differs from production",
      any(abs(l - p) > 1e-6 for _, p, l in rows))

print("== 15 & 27. model registry and no auto-promotion ==")
model_id = storage.register_model(
    conn, model_name="Gradient boosting", model_family="gradient_boosting",
    target=ml_lab.TARGET_ABSOLUTE, horizon="12M", features=["shaffer_score"],
    hyperparameters={"n_estimators": 60}, metrics={"mae": 0.1},
    model_version="gb_v1")
check("model registered", model_id > 0)
stored = storage.get_model(conn, model_id)
check("defaults to RESEARCH, never PRODUCTION", stored["status"] == storage.RESEARCH)
check("features persisted",
      storage.loads(stored["feature_list_json"]) == ["shaffer_score"])
check("hyperparameters persisted",
      storage.loads(stored["hyperparameters_json"])["n_estimators"] == 60)
check("metrics persisted", storage.loads(stored["metrics_json"])["mae"] == 0.1)
check("version persisted", stored["model_version"] == "gb_v1")
check("no production model exists", storage.production_model(conn, "12M") is None)
ml_lab.train_all(conn, "12M", register=True)
check("training NEVER promotes to production",
      storage.production_model(conn, "12M") is None)
check("every registered model is RESEARCH",
      all(m["status"] == storage.RESEARCH for m in storage.list_models(conn)))
storage.set_model_status(conn, model_id, storage.PRODUCTION)
check("explicit promotion works",
      storage.production_model(conn, "12M")["model_id"] == model_id)
storage.set_model_status(conn, model_id, storage.RESEARCH)

print("== 28-29. production output is the Shaffer prediction ==")
check("production calibration unchanged by ML", P.V1_SLOPE == 0.20)
check("production model name", P.PREDICTION_MODEL == "shaffer_score_linear")
check("calibration version tagged", P.RETURN_CALIBRATION_VERSION.endswith("0.20"))
page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "views/ml_page.py")).read()
check("29. challenger columns labelled as challenger",
      "ML challenger return" in page and "ML challenger price" in page)
check("challenger clearly not production",
      "would not replace it" in page or "not replace" in page)
check("ML Lab states it cannot change production",
      "never changed from this page" in page.replace("\n", " ").replace("  ", " ")
      or "production Shaffer model is never" in page.replace("\n", " ")
      or "changed from this page" in page.replace("\n", " "))
current = storage.get_current_score(conn, aid)
check("28. terminal stores only the official prediction",
      current is None or current["prediction_model"] in (None, "shaffer_score_linear"))

print("== effective observation count discounts TIME overlap ==")
# Daily snapshots over one year: heavy 12M label overlap.
daily = ml_lab.Dataset(horizon="12M")
daily.dates = [(dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()
               for i in range(365) for _ in range(5)]
daily.y = [0.0] * len(daily.dates)
check("1,825 daily rows over one year are far fewer effective observations",
      daily.effective_observations < daily.n / 10,
      f"{daily.effective_observations} of {daily.n}")
# A single date cannot be validated at all, however many names it carries.
single = ml_lab.Dataset(horizon="12M")
single.dates = ["2026-01-01"] * 500
single.y = [0.0] * 500
single_report = ml_lab.ModelReport(name="x", family="y", horizon="12M",
                                   target=ml_lab.TARGET_ABSOLUTE, sampling="monthly")
check("one snapshot date yields no walk-forward folds",
      ml_lab.walk_forward_splits(single.dates) == [])
check("one snapshot date is always INSUFFICIENT regardless of row count",
      ml_lab.classify_status(single, single_report) == ml_lab.INSUFFICIENT)

print("== status thresholds are deterministic ==")
big = ml_lab.Dataset(horizon="12M")
# 120 monthly dates over 10 years x 100 names per date. Effective observations
# discount the overlap: ~10 non-overlapping 12M windows x 100 names = ~1,000.
month_dates = [f"20{y:02d}-{m:02d}-01" for y in range(26, 36) for m in range(1, 13)]
big.dates = [d for d in month_dates for _ in range(100)]
big.y = [0.0] * len(big.dates)
rep = ml_lab.ModelReport(name="x", family="y", horizon="12M",
                         target=ml_lab.TARGET_ABSOLUTE, sampling="monthly")
rep.folds = [ml_lab.FoldResult("", "", "", "", 1, 1) for _ in range(4)]
rep.metrics = {"spearman": 0.30}
check("large sample + good spearman -> VALIDATED",
      ml_lab.classify_status(big, rep) == ml_lab.VALIDATED)
rep.metrics = {"spearman": -0.1}
check("negative spearman -> EXPERIMENTAL, not validated",
      ml_lab.classify_status(big, rep) == ml_lab.EXPERIMENTAL)
rep.metrics = {"spearman": 0.30}
baseline_rep = ml_lab.ModelReport(name="b", family="baseline", horizon="12M",
                                  target=ml_lab.TARGET_ABSOLUTE, sampling="monthly")
baseline_rep.metrics = {"spearman": 0.295}
check("must beat the baseline by the documented margin",
      ml_lab.classify_status(big, rep, baseline_rep) == ml_lab.EXPERIMENTAL)
baseline_rep.metrics = {"spearman": 0.10}
check("clear edge over the baseline -> VALIDATED",
      ml_lab.classify_status(big, rep, baseline_rep) == ml_lab.VALIDATED)
rep.folds = [ml_lab.FoldResult("", "", "", "", 1, 1)]
check("too few folds -> INSUFFICIENT",
      ml_lab.classify_status(big, rep) == ml_lab.INSUFFICIENT)

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)
