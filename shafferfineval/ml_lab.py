"""
ShafferFinEval -- ML Lab.

A RESEARCH and CHALLENGER system. It tests whether Shaffer Scores and their
factors actually predict future returns, learns alternative mappings, and
compares challengers against the human-designed model.

It never changes production. The Shaffer Score, its factor weights, the
classification bands, the hedge formula and the V1 score-to-return calibration
are untouched by anything here. A model reaches PRODUCTION only through an
explicit `storage.set_model_status` call that no training path invokes.

    Collect -> Observe Outcomes -> Train -> Backtest -> Validate
      -> Compare -> Propose        (never: Train -> Silently Rewrite)
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional, Sequence

import mllib
import prediction as pred
import storage
from statlib import is_finite

ASSET_CLASS = "Equity"
TARGET_ABSOLUTE = "absolute_price_return"
TARGET_EXCESS = "vti_excess_return"

#: Sampling cadences. Daily snapshots create heavily overlapping 12-month
#: labels, so the default for long horizons is monthly.
DAILY, WEEKLY, MONTHLY = "daily", "weekly", "monthly"
DEFAULT_SAMPLING = {
    # Short horizons barely overlap, so daily rows are genuinely independent
    # observations and are kept. Long horizons overlap almost completely.
    "1D": DAILY, "5D": DAILY, "1M": WEEKLY,
    "3M": MONTHLY, "6M": MONTHLY, "12M": MONTHLY,
}

#: Status thresholds, deterministic and documented.
MIN_OBS_EXPERIMENTAL = 200
MIN_OBS_VALIDATED = 750
MIN_FOLDS_VALIDATED = 3
#: A challenger must beat the Shaffer baseline's out-of-sample rank correlation
#: by at least this margin to be called a validated challenger.
MIN_SPEARMAN_EDGE = 0.02

INSUFFICIENT = "INSUFFICIENT DATA"
EXPERIMENTAL = "EXPERIMENTAL"
VALIDATED = "VALIDATED CHALLENGER"

#: Features drawn from the immutable stored snapshot. Every one of these was
#: genuinely known at the snapshot date.
CORE_FEATURES = [
    "shaffer_score", "company_score", "sector_overlay",
    "valuation", "growth", "profitability", "debt",
    "revenue_growth", "revenue_acceleration", "ebitda_growth",
    "ebitda_margin", "roa", "net_debt_ebitda", "debt_market_cap",
]
RAW_FEATURES = ["valuation_gap", "benchmark_ev_ebitda"]


@dataclass
class Dataset:
    """A point-in-time training set."""

    horizon: str
    target: str = TARGET_ABSOLUTE
    sampling: str = MONTHLY
    feature_names: list[str] = field(default_factory=list)
    X: list[list[float]] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    shaffer_scores: list[float] = field(default_factory=list)
    total_rows: int = 0
    dropped_incomplete: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.y)

    @property
    def unique_dates(self) -> int:
        return len(set(self.dates))

    @property
    def effective_observations(self) -> int:
        """Non-overlapping equivalent: independent time windows x names per date.

        Daily rows carry almost identical 12-month labels, so the raw count
        flatters the sample. This discounts that TIME overlap.

        It does NOT discount cross-sectional correlation: 500 names on a single
        date share one market period and are far from 500 independent draws.
        Guarding that is the walk-forward fold requirement, which a single date
        can never satisfy, so such a sample is reported INSUFFICIENT however
        many rows it holds.
        """
        if not self.dates:
            return 0
        span_days = pred.HORIZONS.get(self.horizon, 252)
        unique = sorted(set(self.dates))
        if len(unique) < 2:
            return self.unique_dates and self.n // max(self.unique_dates, 1)
        first = _dt.date.fromisoformat(unique[0])
        last = _dt.date.fromisoformat(unique[-1])
        calendar_span = max((last - first).days, 1)
        windows = max(calendar_span / (span_days * 365 / 252), 1.0)
        per_date = self.n / len(unique)
        return int(round(windows * per_date))


@dataclass
class FoldResult:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    metrics: dict = field(default_factory=dict)


@dataclass
class ModelReport:
    name: str
    family: str
    horizon: str
    target: str
    sampling: str
    features: list[str] = field(default_factory=list)
    hyperparameters: dict = field(default_factory=dict)
    folds: list[FoldResult] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    importance: list = field(default_factory=list)
    coefficients: dict = field(default_factory=dict)
    n_train: int = 0
    n_test: int = 0
    training_start: str = ""
    training_end: str = ""
    status: str = INSUFFICIENT
    note: str = ""


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

def refresh_outcome_labels(conn, price_history_fn, vti_history_fn=None) -> dict:
    """Fill in realised forward returns for every stored snapshot.

    `price_history_fn(symbol) -> [(date, adjusted_close), ...]` supplies actual
    prices. Labels are observations of the FUTURE relative to a snapshot, so
    using today's price history for them is not leakage -- the point-in-time
    rule binds the FEATURES, which come only from the stored snapshot.
    """
    summary = {"snapshots": 0, "labels_written": 0, "pending": 0, "errors": 0}
    rows = conn.execute(
        """SELECT h.asset_id, a.symbol, a.yahoo_symbol, h.snapshot_date, h.price
           FROM score_history h JOIN assets a ON a.asset_id = h.asset_id
           WHERE h.snapshot_kind='close' AND h.price IS NOT NULL
           ORDER BY a.symbol, h.snapshot_date"""
    ).fetchall()
    if not rows:
        return summary

    vti_series = {}
    if vti_history_fn is not None:
        try:
            vti_series = dict(vti_history_fn() or [])
        except Exception:
            vti_series = {}

    cache: dict[str, dict] = {}
    for row in rows:
        summary["snapshots"] += 1
        symbol = row["yahoo_symbol"] or row["symbol"]
        if symbol not in cache:
            try:
                cache[symbol] = dict(price_history_fn(symbol) or [])
            except Exception:
                cache[symbol] = {}
                summary["errors"] += 1
        series = cache[symbol]
        if not series:
            continue

        base_date = _dt.date.fromisoformat(row["snapshot_date"])
        for horizon in pred.HORIZONS:
            target_date = base_date + _dt.timedelta(
                days=pred.HORIZON_CALENDAR_DAYS.get(horizon, 365))
            future = _nearest_on_or_after(
                series, target_date, tolerance_days=label_tolerance(horizon))
            if future is None:
                summary["pending"] += 1
                continue
            future_date, future_price = future
            forward = pred.forward_return(row["price"], future_price)
            vti_forward = None
            if vti_series:
                vti_base = _nearest_on_or_before(vti_series, base_date)
                vti_future = _nearest_on_or_after(
                    vti_series, target_date,
                    tolerance_days=label_tolerance(horizon))
                if vti_base and vti_future:
                    vti_forward = pred.forward_return(vti_base[1], vti_future[1])
            storage.save_outcome_label(
                conn, row["asset_id"], row["snapshot_date"], horizon,
                base_price=row["price"], future_date=future_date.isoformat(),
                future_price=future_price, forward_return=forward,
                vti_forward_return=vti_forward,
                excess_return=pred.excess_return(forward, vti_forward),
            )
            summary["labels_written"] += 1
    return summary


def label_tolerance(horizon: str) -> int:
    """How far past the target date a price may sit and still be that label.

    A weekend or a holiday always pushes the match a few days out, so some
    slack is required. But without a ceiling a gap in the series would let a
    price three weeks late be stored as a "1D" outcome. The allowance is 10%
    of the horizon with a four-day floor, so it scales with what the label
    actually means.
    """
    span = pred.HORIZON_CALENDAR_DAYS.get(horizon, 365)
    return max(4, int(round(span * 0.10)))


def _nearest_on_or_after(series: dict, target: _dt.date,
                         tolerance_days: Optional[int] = None):
    candidates = [d for d in series if d >= target]
    if not candidates:
        return None
    day = min(candidates)
    if tolerance_days is not None and (day - target).days > tolerance_days:
        # The series has a hole here. Report no label rather than a label
        # whose name does not match the gap it was measured over.
        return None
    return day, series[day]


def _nearest_on_or_before(series: dict, target: _dt.date):
    candidates = [d for d in series if d <= target]
    if not candidates:
        return None
    day = max(candidates)
    return day, series[day]


# --------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------

def _sample_dates(dates: Sequence[str], sampling: str) -> set:
    """Thin the snapshot dates to reduce label overlap."""
    unique = sorted(set(dates))
    if sampling == DAILY or not unique:
        return set(unique)
    keep, seen = [], set()
    for value in unique:
        day = _dt.date.fromisoformat(value)
        key = (day.year, day.month) if sampling == MONTHLY else day.isocalendar()[:2]
        if key not in seen:
            seen.add(key)
            keep.append(value)
    return set(keep)


def build_dataset(conn, horizon: str = "12M", target: str = TARGET_ABSOLUTE,
                  sampling: Optional[str] = None) -> Dataset:
    """Assemble features and labels strictly from immutable stored snapshots."""
    sampling = sampling or DEFAULT_SAMPLING.get(horizon, MONTHLY)
    dataset = Dataset(horizon=horizon, target=target, sampling=sampling)

    rows = storage.dataset_rows(conn, horizon)
    dataset.total_rows = len(rows)
    if not rows:
        dataset.notes.append(
            f"No labelled {horizon} observations yet. Snapshots need to age "
            f"{horizon} before an outcome exists."
        )
        return dataset

    keep_dates = _sample_dates([r["snapshot_date"] for r in rows], sampling)
    feature_names = CORE_FEATURES + RAW_FEATURES
    dataset.feature_names = feature_names

    for row in rows:
        if row["snapshot_date"] not in keep_dates:
            continue
        label = row["excess_return"] if target == TARGET_EXCESS else row["forward_return"]
        if not is_finite(label):
            continue

        factors = storage.loads(row["factor_scores_json"]) or {}
        raw = storage.loads(row["raw_inputs_json"]) or {}
        values, complete = [], True
        for name in feature_names:
            if name == "shaffer_score":
                value = row["shaffer_score"]
            elif name == "company_score":
                value = row["company_score"]
            elif name == "sector_overlay":
                value = row["sector_overlay"]
            elif name in RAW_FEATURES:
                value = raw.get(name)
            else:
                value = factors.get(name)
            if not is_finite(value):
                complete = False
                break
            values.append(float(value))
        if not complete:
            dataset.dropped_incomplete += 1
            continue

        dataset.X.append(values)
        dataset.y.append(float(label))
        dataset.dates.append(row["snapshot_date"])
        dataset.symbols.append(row["symbol"])
        dataset.shaffer_scores.append(float(row["shaffer_score"]))

    dataset.notes.append(
        f"{dataset.n} rows from {dataset.unique_dates} snapshot dates "
        f"({sampling} sampling), {dataset.dropped_incomplete} dropped for "
        f"incomplete factors."
    )
    return dataset


# --------------------------------------------------------------------------
# Walk-forward validation
# --------------------------------------------------------------------------

def walk_forward_splits(dates: Sequence[str], n_folds: int = 4,
                        min_train: int = 40) -> list[tuple[list[int], list[int]]]:
    """Expanding-window chronological splits. Past trains, future tests.

    Never a random split: an observation may never be trained on information
    that occurs after its own prediction target.
    """
    unique = sorted(set(dates))
    if len(unique) < 2:
        return []
    by_date: dict[str, list[int]] = {}
    for index, value in enumerate(dates):
        by_date.setdefault(value, []).append(index)

    splits = []
    n_dates = len(unique)
    fold_size = max(1, n_dates // (n_folds + 1))
    for fold in range(1, n_folds + 1):
        cut = fold_size * fold
        test_end = min(cut + fold_size, n_dates)
        if cut >= n_dates or cut == test_end:
            break
        train_dates = unique[:cut]
        test_dates = unique[cut:test_end]
        train_idx = [i for d in train_dates for i in by_date[d]]
        test_idx = [i for d in test_dates for i in by_date[d]]
        if len(train_idx) < min_train or not test_idx:
            continue
        splits.append((train_idx, test_idx))
    return splits


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

class ShafferBaseline:
    """The production V1 calibration, as a model. Every challenger must beat it.

    It does not learn: it is the human mapping 0.20 x ShafferScore, expressed
    as a fraction so it is comparable with realised returns.
    """

    name = "Shaffer V1 baseline"
    family = "baseline"

    def __init__(self, score_index: int = 0):
        self.score_index = score_index
        self.fitted = True

    def fit(self, X, y, feature_names=None):
        return self

    def predict(self, X):
        return [pred.predicted_return_pct(row[self.score_index]) / 100.0 for row in X]


def build_models(seed: int = 0) -> list:
    """The challenger line-up. Deterministic, pure stdlib."""
    return [
        ("Linear regression", "linear", mllib.LinearModel(0.0, "linear"), {}),
        ("Ridge", "ridge", mllib.LinearModel(5.0, "ridge"), {"alpha": 5.0}),
        ("Lasso", "lasso", mllib.ElasticNetModel(0.02, 1.0),
         {"alpha": 0.02, "l1_ratio": 1.0}),
        ("Elastic Net", "elastic_net", mllib.ElasticNetModel(0.02, 0.5),
         {"alpha": 0.02, "l1_ratio": 0.5}),
        ("Gradient boosting", "gradient_boosting",
         mllib.GradientBoostingModel(60, 0.08, 3, seed=seed),
         {"n_estimators": 60, "learning_rate": 0.08, "max_depth": 3}),
        ("Random forest", "random_forest",
         mllib.RandomForestModel(40, 6, seed=seed),
         {"n_estimators": 40, "max_depth": 6}),
    ]


def evaluate_model(dataset: Dataset, model, name: str, family: str,
                   hyperparameters: Optional[dict] = None,
                   n_folds: int = 4) -> ModelReport:
    """Walk-forward evaluation of one model over one dataset."""
    report = ModelReport(
        name=name, family=family, horizon=dataset.horizon, target=dataset.target,
        sampling=dataset.sampling, features=list(dataset.feature_names),
        hyperparameters=hyperparameters or {},
    )
    if dataset.n == 0:
        report.note = "No labelled observations."
        return report

    report.training_start = min(dataset.dates)
    report.training_end = max(dataset.dates)

    splits = walk_forward_splits(dataset.dates, n_folds)
    if not splits:
        report.note = (
            "Not enough distinct snapshot dates for a walk-forward split. "
            "At least two well-separated periods are needed."
        )
        return report

    all_true, all_pred = [], []
    for train_idx, test_idx in splits:
        X_train = [dataset.X[i] for i in train_idx]
        y_train = [dataset.y[i] for i in train_idx]
        X_test = [dataset.X[i] for i in test_idx]
        y_test = [dataset.y[i] for i in test_idx]
        try:
            model.fit(X_train, y_train, dataset.feature_names)
            predictions = model.predict(X_test)
        except Exception as exc:
            report.note = f"Training failed: {exc}"
            return report
        all_true.extend(y_test)
        all_pred.extend(predictions)
        report.folds.append(FoldResult(
            train_start=min(dataset.dates[i] for i in train_idx),
            train_end=max(dataset.dates[i] for i in train_idx),
            test_start=min(dataset.dates[i] for i in test_idx),
            test_end=max(dataset.dates[i] for i in test_idx),
            n_train=len(train_idx), n_test=len(test_idx),
            metrics=mllib.evaluate(y_test, predictions),
        ))
        report.n_train = max(report.n_train, len(train_idx))
        report.n_test += len(test_idx)

    report.metrics = mllib.evaluate(all_true, all_pred)
    report.metrics["quantiles"] = mllib.quantile_buckets(all_true, all_pred)
    report.metrics["calibration"] = mllib.calibration_buckets(all_true, all_pred)

    # Fit once on everything for interpretable coefficients / importance.
    try:
        model.fit(dataset.X, dataset.y, dataset.feature_names)
        if hasattr(model, "importance"):
            report.importance = model.importance()
        else:
            report.importance = mllib.permutation_importance(
                model, dataset.X, dataset.y, dataset.feature_names)
        if getattr(model, "coefficients", None):
            report.coefficients = dict(zip(dataset.feature_names, model.coefficients))
    except Exception:
        pass

    report.status = classify_status(dataset, report)
    return report


def classify_status(dataset: Dataset, report: ModelReport,
                    baseline: Optional[ModelReport] = None) -> str:
    """Deterministic, documented status thresholds.

        INSUFFICIENT DATA    fewer than 200 effective observations, or fewer
                             than 3 walk-forward folds
        EXPERIMENTAL         enough to train, not yet enough to trust
        VALIDATED CHALLENGER >= 750 effective observations, >= 3 folds, and a
                             positive out-of-sample Spearman that beats the
                             Shaffer baseline by at least 0.02
    """
    effective = dataset.effective_observations
    if effective < MIN_OBS_EXPERIMENTAL or len(report.folds) < MIN_FOLDS_VALIDATED:
        return INSUFFICIENT
    if effective < MIN_OBS_VALIDATED:
        return EXPERIMENTAL
    spearman = report.metrics.get("spearman")
    if spearman is None or spearman <= 0:
        return EXPERIMENTAL
    if baseline is not None:
        base_spearman = baseline.metrics.get("spearman")
        if base_spearman is not None and spearman < base_spearman + MIN_SPEARMAN_EDGE:
            return EXPERIMENTAL
    return VALIDATED


# --------------------------------------------------------------------------
# Score calibration
# --------------------------------------------------------------------------

@dataclass
class Calibration:
    """Historically estimated score-to-return mapping."""

    alpha: Optional[float] = None
    beta: Optional[float] = None
    n: int = 0
    r2: Optional[float] = None
    spearman: Optional[float] = None
    mae: Optional[float] = None
    training_start: str = ""
    training_end: str = ""
    status: str = INSUFFICIENT
    production_slope: float = pred.V1_SLOPE
    note: str = ""

    @property
    def implied_at(self):
        """Predicted return at a few reference scores, learned vs production."""
        if self.beta is None:
            return []
        out = []
        for score in (-100, -50, 0, 50, 100):
            learned = (self.alpha or 0) + self.beta * score
            production = pred.predicted_return_pct(score)
            out.append((score, production, learned))
        return out


def estimate_calibration(dataset: Dataset) -> Calibration:
    """Fit ActualReturn = alpha + beta x ShafferScore on realised outcomes.

    Reported in percentage points so it is directly comparable with the
    production 0.20 slope. It is a FINDING, never an automatic replacement.
    """
    calibration = Calibration()
    if dataset.n == 0:
        calibration.note = (
            f"No realised {dataset.horizon} outcomes yet, so the score-to-return "
            "mapping cannot be estimated. V1 remains in production."
        )
        return calibration

    calibration.n = dataset.n
    calibration.training_start = min(dataset.dates)
    calibration.training_end = max(dataset.dates)

    X = [[score] for score in dataset.shaffer_scores]
    y_pct = [value * 100.0 for value in dataset.y]      # percentage points
    model = mllib.LinearModel(0.0, "calibration").fit(X, y_pct, ["shaffer_score"])
    if not model.fitted:
        calibration.note = "Calibration fit failed (degenerate score distribution)."
        return calibration

    calibration.alpha = model.intercept
    calibration.beta = model.coefficients[0]
    fitted = model.predict(X)
    calibration.r2 = mllib.r_squared(y_pct, fitted)
    calibration.spearman = mllib.spearman(dataset.shaffer_scores, dataset.y)
    calibration.mae = mllib.mean_absolute_error(y_pct, fitted)

    effective = dataset.effective_observations
    if effective < MIN_OBS_EXPERIMENTAL:
        calibration.status = INSUFFICIENT
    elif effective < MIN_OBS_VALIDATED:
        calibration.status = EXPERIMENTAL
    else:
        calibration.status = VALIDATED
    return calibration


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def train_all(conn, horizon: str = "12M", target: str = TARGET_ABSOLUTE,
              sampling: Optional[str] = None, n_folds: int = 4,
              register: bool = True) -> tuple[Dataset, list[ModelReport], Calibration]:
    """Build the dataset, evaluate every model walk-forward, register results.

    Registration writes to the model registry with status RESEARCH or the
    computed research status. It never touches production.
    """
    dataset = build_dataset(conn, horizon, target, sampling)
    calibration = estimate_calibration(dataset)

    baseline_report = evaluate_model(
        dataset, ShafferBaseline(dataset.feature_names.index("shaffer_score")
                                 if "shaffer_score" in dataset.feature_names else 0),
        "Shaffer V1 baseline", "baseline",
        {"slope": pred.V1_SLOPE}, n_folds)

    reports = [baseline_report]
    for name, family, model, hyperparameters in build_models():
        report = evaluate_model(dataset, model, name, family, hyperparameters, n_folds)
        report.status = classify_status(dataset, report, baseline_report)
        reports.append(report)

    if register and dataset.n:
        for report in reports:
            storage.register_model(
                conn, model_name=report.name, model_family=report.family,
                asset_class=ASSET_CLASS, target=report.target,
                horizon=report.horizon, features=report.features,
                hyperparameters=report.hyperparameters,
                training_start=report.training_start,
                training_end=report.training_end,
                validation_method=f"walk-forward ({len(report.folds)} folds)",
                training_observations=report.n_train,
                test_observations=report.n_test,
                metrics={k: v for k, v in report.metrics.items()
                         if k not in ("quantiles", "calibration")},
                importance=report.importance, coefficients=report.coefficients,
                sampling=report.sampling, status=storage.RESEARCH,
                model_version=f"{report.family}_v1",
            )
    return dataset, reports, calibration


def data_status(conn) -> dict:
    """What the ML Lab has to work with today."""
    stats = storage.snapshot_stats(conn)
    counts = storage.label_counts(conn)
    status = dict(stats)
    status["labels"] = counts
    status["models_registered"] = len(storage.list_models(conn))
    status["production_ml_model"] = None
    for horizon in pred.HORIZONS:
        row = storage.production_model(conn, horizon)
        if row is not None:
            status["production_ml_model"] = row["model_name"]
            break
    return status


ML_LAB_DETAILS = """\
**The ML Lab is a challenger system, not production.**

The Shaffer Score, its factor weights, the classification bands, the hedge
formula and the V1 score-to-return calibration are the production model. The
Lab measures them, proposes alternatives and reports what it finds. Nothing
here can change production: a model reaches PRODUCTION only via an explicit
status change that no training path calls.

**Point-in-time rule**

Features come only from immutable stored snapshots, so every value is what was
genuinely known that day. Revised fundamentals never leak backwards. Outcome
labels are future prices relative to a snapshot, which is what a label is.

**Walk-forward validation**

Expanding chronological windows -- train on the past, test on the next period,
repeat. Never a random split.

**Overlapping labels**

Daily snapshots produce almost-identical 12-month labels, which inflates the
apparent sample. Long horizons default to monthly sampling and the effective
(non-overlapping) observation count is always shown alongside the raw count.

**Status thresholds**

    INSUFFICIENT DATA     < 200 effective observations, or < 3 folds
    EXPERIMENTAL          trainable, not yet trustworthy
    VALIDATED CHALLENGER  >= 750 effective observations, >= 3 folds, positive
                          out-of-sample Spearman beating the Shaffer baseline
                          by at least 0.02

**Importance is predictive, not causal.** It says a feature helped a model
predict, not that it caused a return.
"""
