"""
ShafferFinEval -- synthetic ML validation.

These tests validate the SOFTWARE, not the investment model.

They generate data with a KNOWN relationship and check that the ML pipeline
recovers it: that labels line up, walk-forward respects chronology, models can
learn linear and nonlinear structure, regularization suppresses noise features,
leakage is caught, and the hedge/option research paths work.

A passing suite means the plumbing is correct. It says NOTHING about whether
the Shaffer Score predicts real markets -- only real, aged, point-in-time
observations can answer that.

Synthetic observations are never written into the live tables and never mixed
into production training.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

import mllib
import ml_lab

SYNTHETIC_LABEL = "SYNTHETIC TEST ONLY"


@dataclass
class SyntheticResult:
    name: str
    passed: bool = False
    detail: str = ""
    metrics: dict = field(default_factory=dict)
    note: str = SYNTHETIC_LABEL


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _split_chronological(X, y, dates, fraction: float = 0.7):
    """Train on the earlier portion, test on the later. Never random."""
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    cut = int(len(order) * fraction)
    train, test = order[:cut], order[cut:]
    return ([X[i] for i in train], [y[i] for i in train],
            [X[i] for i in test], [y[i] for i in test])


def _dates(n: int, per_period: int = 20) -> list[str]:
    out = []
    for i in range(n):
        period = i // per_period
        year = 2026 + period // 12
        month = period % 12 + 1
        out.append(f"{year}-{month:02d}-01")
    return out


# --------------------------------------------------------------------------
# 1. Linear recovery
# --------------------------------------------------------------------------

def test_linear_recovery(seed: int = 1) -> SyntheticResult:
    """Y = .35V + .25G + .15P + .10D + .10Sector + .05GPI + noise.

    Linear, Ridge and Elastic Net should recover the signs and roughly the
    ranking of the true coefficients.
    """
    result = SyntheticResult("Linear recovery")
    rng = _rng(seed)
    names = ["V", "G", "P", "D", "Sector", "GPI"]
    true = [0.35, 0.25, 0.15, 0.10, 0.10, 0.05]
    n = 600
    X = [[rng.uniform(-100, 100) for _ in names] for _ in range(n)]
    # Noise is set so the achievable R2 is ~0.89: high enough that a failure
    # means the pipeline is broken, not that the test out-ran its own SNR.
    y = [sum(t * v for t, v in zip(true, row)) / 100 * 0.3 + rng.gauss(0, 0.03)
         for row in X]
    dates = _dates(n)
    Xtr, ytr, Xte, yte = _split_chronological(X, y, dates)

    models = {
        "linear": mllib.LinearModel(0.0),
        "ridge": mllib.LinearModel(3.0),
        "elastic_net": mllib.ElasticNetModel(0.01, 0.5),
    }
    detail, ok = [], True
    for label, model in models.items():
        model.fit(Xtr, ytr, names)
        metrics = mllib.evaluate(yte, model.predict(Xte))
        result.metrics[label] = metrics
        signs_ok = all(c > 0 for c in model.coefficients)
        ranking = [n for n, _ in model.importance()]
        top_ok = ranking[0] == "V" and ranking[1] == "G"
        r2_ok = (metrics["r2"] or 0) > 0.80
        ok = ok and signs_ok and top_ok and r2_ok
        detail.append(f"{label}: R2={metrics['r2']:.3f} top={ranking[:3]}")
    result.passed = ok
    result.detail = " | ".join(detail)
    return result


# --------------------------------------------------------------------------
# 2. Nonlinear interaction
# --------------------------------------------------------------------------

def test_nonlinear_interaction(seed: int = 2) -> SyntheticResult:
    """A threshold interaction that a linear model structurally cannot see.

    Y gets a large bonus only when V > 50 AND G > 40. Gradient boosting should
    beat the linear model out of sample.
    """
    result = SyntheticResult("Nonlinear interaction")
    rng = _rng(seed)
    names = ["V", "G", "P"]
    n = 800
    X = [[rng.uniform(-100, 100) for _ in names] for _ in range(n)]
    y = []
    for v, g, p in X:
        base = (0.25 * v + 0.20 * g + 0.15 * p) / 100 * 0.3
        bonus = 0.25 if (v > 50 and g > 40) else 0.0
        y.append(base + bonus + rng.gauss(0, 0.04))
    dates = _dates(n)
    Xtr, ytr, Xte, yte = _split_chronological(X, y, dates)

    linear = mllib.LinearModel(0.0).fit(Xtr, ytr, names)
    gbm = mllib.GradientBoostingModel(80, 0.08, 3, seed=seed).fit(Xtr, ytr, names)
    lin_metrics = mllib.evaluate(yte, linear.predict(Xte))
    gbm_metrics = mllib.evaluate(yte, gbm.predict(Xte))
    result.metrics = {"linear": lin_metrics, "gradient_boosting": gbm_metrics}
    result.passed = gbm_metrics["mae"] < lin_metrics["mae"]
    result.detail = (f"linear MAE={lin_metrics['mae']:.4f} vs "
                     f"GBM MAE={gbm_metrics['mae']:.4f} "
                     f"({'GBM better' if result.passed else 'GBM did NOT improve'})")
    return result


# --------------------------------------------------------------------------
# 3. Useless factor
# --------------------------------------------------------------------------

def test_useless_factor(seed: int = 3) -> SyntheticResult:
    """A pure-noise feature must not be promoted.

    Lasso should zero it, and permutation importance should rank it last.
    """
    result = SyntheticResult("Useless factor rejected")
    rng = _rng(seed)
    names = ["V", "G", "NoiseFactor"]
    n = 600
    X = [[rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(-100, 100)]
         for _ in range(n)]
    y = [(0.4 * v + 0.3 * g) / 100 * 0.3 + rng.gauss(0, 0.04) for v, g, _ in X]

    lasso = mllib.ElasticNetModel(0.02, 1.0).fit(X, y, names)
    selected = lasso.selected_features()
    importance = dict(lasso.importance())
    gbm = mllib.GradientBoostingModel(60, 0.08, 3, seed=seed).fit(X, y, names)
    perm = dict(mllib.permutation_importance(gbm, X, y, names, seed=seed))

    dropped = "NoiseFactor" not in selected
    small = importance.get("NoiseFactor", 0) < 0.05
    last = min(perm, key=perm.get) == "NoiseFactor"
    result.passed = (dropped or small) and last
    result.metrics = {"lasso_importance": importance, "permutation": perm}
    result.detail = (f"lasso dropped={dropped} share={importance.get('NoiseFactor', 0):.3f} "
                     f"permutation-last={last}")
    return result


# --------------------------------------------------------------------------
# 4. Wrong production weight
# --------------------------------------------------------------------------

def test_wrong_weight(seed: int = 4) -> SyntheticResult:
    """Production over-weights Valuation; reality favours Growth.

    The challenger should learn a larger Growth coefficient than Valuation.
    """
    result = SyntheticResult("Wrong production weight detected")
    rng = _rng(seed)
    names = ["V", "G", "P", "D"]
    production = {"V": 0.40, "G": 0.25, "P": 0.20, "D": 0.15}
    reality = {"V": 0.15, "G": 0.45, "P": 0.25, "D": 0.15}
    n = 700
    X = [[rng.uniform(-100, 100) for _ in names] for _ in range(n)]
    y = [sum(reality[k] * v for k, v in zip(names, row)) / 100 * 0.3
         + rng.gauss(0, 0.04) for row in X]
    dates = _dates(n)
    Xtr, ytr, Xte, yte = _split_chronological(X, y, dates)

    model = mllib.LinearModel(1.0).fit(Xtr, ytr, names)
    learned = dict(model.importance())
    growth_beats_value = learned["G"] > learned["V"]
    production_says_otherwise = production["V"] > production["G"]
    result.passed = growth_beats_value and production_says_otherwise
    result.metrics = {"production": production, "learned_shares": learned,
                      "out_of_sample": mllib.evaluate(yte, model.predict(Xte))}
    result.detail = (f"production V={production['V']:.0%} > G={production['G']:.0%}, "
                     f"but learned G={learned['G']:.2f} > V={learned['V']:.2f}")
    return result


# --------------------------------------------------------------------------
# 5. Wrong sign
# --------------------------------------------------------------------------

def test_wrong_sign(seed: int = 5) -> SyntheticResult:
    """Production assumes a factor helps; reality says it hurts.

    The learned coefficient must come back NEGATIVE.
    """
    result = SyntheticResult("Wrong factor sign detected")
    rng = _rng(seed)
    names = ["V", "G", "SuspectFactor"]
    n = 600
    X = [[rng.uniform(-100, 100) for _ in names] for _ in range(n)]
    y = [(0.35 * v + 0.25 * g - 0.30 * s) / 100 * 0.3 + rng.gauss(0, 0.04)
         for v, g, s in X]

    model = mllib.LinearModel(0.0).fit(X, y, names)
    coefficient = model.coefficients[names.index("SuspectFactor")]
    result.passed = coefficient < 0
    result.metrics = {"coefficients": dict(zip(names, model.coefficients))}
    result.detail = (f"production assumed positive; learned "
                     f"{coefficient:+.5f} ({'sign flip found' if result.passed else 'not found'})")
    return result


# --------------------------------------------------------------------------
# 6. Political interaction
# --------------------------------------------------------------------------

def test_political_interaction(seed: int = 6) -> SyntheticResult:
    """Oil-style set where GPI only matters when inventories are tight.

        ShockEffect = GPI x InventoryTightness

    A tree model should capture this better than a linear one.
    """
    result = SyntheticResult("Political x inventory interaction")
    rng = _rng(seed)
    names = ["Inventory", "Supply", "Demand", "GPI"]
    n = 900
    X, y = [], []
    for _ in range(n):
        inventory = rng.uniform(-100, 100)      # negative = tight
        supply = rng.uniform(-100, 100)
        demand = rng.uniform(-100, 100)
        gpi = rng.uniform(0, 100)
        tightness = max(0.0, -inventory) / 100.0
        base = (0.25 * -inventory + 0.15 * supply + 0.15 * demand) / 100 * 0.3
        shock = 0.6 * (gpi / 100.0) * tightness
        X.append([inventory, supply, demand, gpi])
        y.append(base + shock + rng.gauss(0, 0.05))
    dates = _dates(n)
    Xtr, ytr, Xte, yte = _split_chronological(X, y, dates)

    linear = mllib.LinearModel(0.0).fit(Xtr, ytr, names)
    gbm = mllib.GradientBoostingModel(90, 0.08, 4, seed=seed).fit(Xtr, ytr, names)
    lin_metrics = mllib.evaluate(yte, linear.predict(Xte))
    gbm_metrics = mllib.evaluate(yte, gbm.predict(Xte))
    result.metrics = {"linear": lin_metrics, "gradient_boosting": gbm_metrics}
    result.passed = gbm_metrics["mae"] < lin_metrics["mae"]
    result.detail = (f"linear MAE={lin_metrics['mae']:.4f} vs GBM "
                     f"MAE={gbm_metrics['mae']:.4f}")
    return result


# --------------------------------------------------------------------------
# 7. Regime change
# --------------------------------------------------------------------------

def test_regime_change(seed: int = 7) -> SyntheticResult:
    """Value works in regime A, growth in regime B.

    Walk-forward must expose the instability; a random split hides it by
    letting regime-B rows leak into training for regime-B tests.
    """
    result = SyntheticResult("Regime change exposed by walk-forward")
    rng = _rng(seed)
    names = ["V", "G"]
    n = 800
    X, y, dates = [], [], []
    for i in range(n):
        v, g = rng.uniform(-100, 100), rng.uniform(-100, 100)
        regime_a = i < n // 2
        target = (0.5 * v if regime_a else 0.5 * g) / 100 * 0.3 + rng.gauss(0, 0.04)
        X.append([v, g]); y.append(target)
        dates.append(f"{2026 + i // 100}-{(i // 20) % 12 + 1:02d}-01")

    Xtr, ytr, Xte, yte = _split_chronological(X, y, dates, 0.5)
    walk = mllib.evaluate(yte, mllib.LinearModel(0.0).fit(Xtr, ytr, names).predict(Xte))

    order = list(range(n))
    rng.shuffle(order)
    cut = n // 2
    rnd_tr, rnd_te = order[:cut], order[cut:]
    random_model = mllib.LinearModel(0.0).fit(
        [X[i] for i in rnd_tr], [y[i] for i in rnd_tr], names)
    random_metrics = mllib.evaluate(
        [y[i] for i in rnd_te], random_model.predict([X[i] for i in rnd_te]))

    result.metrics = {"walk_forward": walk, "random_split": random_metrics}
    result.passed = walk["mae"] > random_metrics["mae"]
    result.detail = (f"walk-forward MAE={walk['mae']:.4f} vs random-split "
                     f"MAE={random_metrics['mae']:.4f} -- random split is "
                     f"{'falsely optimistic' if result.passed else 'NOT flattered'}")
    return result


# --------------------------------------------------------------------------
# 8. Leakage detection
# --------------------------------------------------------------------------

def test_leakage_detection(seed: int = 8) -> SyntheticResult:
    """A feature carrying the future must be caught, not silently learned.

    Two checks: a timestamp guard rejects a feature observed after the
    prediction date, and an unguarded leaked feature produces an implausibly
    perfect fit -- the signature a reviewer should look for.
    """
    result = SyntheticResult("Leakage prevention")
    rng = _rng(seed)
    names = ["V", "G", "LeakedFuture"]
    n = 400
    X, y = [], []
    for _ in range(n):
        v, g = rng.uniform(-100, 100), rng.uniform(-100, 100)
        target = (0.4 * v + 0.3 * g) / 100 * 0.3 + rng.gauss(0, 0.04)
        X.append([v, g, target])              # leaked: the answer itself
        y.append(target)

    leaked_model = mllib.LinearModel(0.0).fit(X, y, names)
    leaked_r2 = mllib.r_squared(y, leaked_model.predict(X))
    clean = [[row[0], row[1]] for row in X]
    clean_r2 = mllib.r_squared(y, mllib.LinearModel(0.0).fit(clean, y, names[:2]).predict(clean))

    guard_caught = not feature_is_point_in_time(
        feature_timestamp="2027-01-01", prediction_timestamp="2026-09-20")
    guard_allows_valid = feature_is_point_in_time(
        feature_timestamp="2026-09-19", prediction_timestamp="2026-09-20")
    suspicious = leaked_r2 is not None and leaked_r2 > 0.999

    result.passed = guard_caught and guard_allows_valid and suspicious
    result.metrics = {"leaked_r2": leaked_r2, "clean_r2": clean_r2}
    result.detail = (f"timestamp guard rejects future feature={guard_caught}, "
                     f"accepts past feature={guard_allows_valid}, "
                     f"leaked R2={leaked_r2:.5f} vs clean R2={clean_r2:.3f}")
    return result


def feature_is_point_in_time(feature_timestamp: str, prediction_timestamp: str) -> bool:
    """A feature is admissible only if observed AT OR BEFORE the prediction.

    Used as an explicit guard so a leaked feature is rejected structurally
    rather than being caught by eye.
    """
    if not feature_timestamp or not prediction_timestamp:
        return False
    return str(feature_timestamp)[:19] <= str(prediction_timestamp)[:19]


# --------------------------------------------------------------------------
# 9. Hedge selection
# --------------------------------------------------------------------------

def test_hedge_selection(seed: int = 9) -> SyntheticResult:
    """Simulated paths where one hedge is known to be most efficient.

    Large drawdowns with cheap puts: the put spread should beat no-hedge on
    downside, and the ranking by net outcome must recover the built-in truth.
    """
    result = SyntheticResult("Hedge selection")
    rng = _rng(seed)
    paths = []
    for _ in range(600):
        shock = rng.random() < 0.25
        ret = rng.gauss(-0.22, 0.10) if shock else rng.gauss(0.04, 0.08)
        paths.append(ret)

    spot = 100.0
    outcomes = {name: [] for name in
                ("no_hedge", "put", "put_spread", "collar", "trs", "futures", "short_stock")}
    for ret in paths:
        terminal = spot * (1 + ret)
        unhedged = terminal - spot
        put_k, lower_k, call_k = 93.0, 80.0, 110.0
        put_cost, spread_cost, collar_cost = 4.0, 2.0, 0.3
        financing = 0.8
        outcomes["no_hedge"].append(unhedged)
        outcomes["put"].append(unhedged + max(put_k - terminal, 0) - put_cost)
        outcomes["put_spread"].append(
            unhedged + max(put_k - terminal, 0) - max(lower_k - terminal, 0) - spread_cost)
        outcomes["collar"].append(
            unhedged + max(put_k - terminal, 0) - max(terminal - call_k, 0) - collar_cost)
        outcomes["trs"].append(unhedged - (terminal - spot) - financing)
        outcomes["futures"].append(unhedged - (terminal - spot) * 0.95 - 0.4)
        outcomes["short_stock"].append(unhedged - (terminal - spot) - 0.5)

    up_paths = [i for i, r in enumerate(paths) if r > 0]
    stats = {}
    for name, values in outcomes.items():
        downside = [v for v in values if v < 0]
        upside = [values[i] for i in up_paths]
        stats[name] = {
            "mean": sum(values) / len(values),
            "downside_mean": (sum(downside) / len(downside)) if downside else 0.0,
            "upside_mean": (sum(upside) / len(upside)) if upside else 0.0,
            "worst": min(values),
        }
    best_downside = max(stats, key=lambda k: stats[k]["downside_mean"])
    hedges_help = stats["put_spread"]["worst"] > stats["no_hedge"]["worst"]
    # Upside sacrifice must be measured where there IS upside: over the whole
    # sample a full hedge can beat a put simply because the sample is bearish.
    linear_removes_upside = stats["trs"]["upside_mean"] < stats["put"]["upside_mean"]
    convex_keeps_upside = stats["put"]["upside_mean"] < stats["no_hedge"]["upside_mean"]

    result.passed = (best_downside != "no_hedge") and hedges_help \
        and linear_removes_upside and convex_keeps_upside
    result.metrics = stats
    result.detail = (f"best downside={best_downside}; put-spread worst "
                     f"{stats['put_spread']['worst']:.2f} vs unhedged "
                     f"{stats['no_hedge']['worst']:.2f}; upside kept: "
                     f"unhedged {stats['no_hedge']['upside_mean']:.2f} > put "
                     f"{stats['put']['upside_mean']:.2f} > TRS "
                     f"{stats['trs']['upside_mean']:.2f}")
    return result


# --------------------------------------------------------------------------
# 10. Proxy hedge sizing
# --------------------------------------------------------------------------

def test_proxy_hedge(seed: int = 10) -> SyntheticResult:
    """Beta-adjusted sizing must beat naive dollar matching.

    Stock return = beta x index + idiosyncratic noise. Hedging notional x beta
    should leave less residual variance than hedging notional x 1.
    """
    result = SyntheticResult("Beta-adjusted proxy sizing")
    rng = _rng(seed)
    beta = 1.45
    residual_beta, residual_dollar = [], []
    for _ in range(1500):
        index = rng.gauss(0.0, 0.05)
        stock = beta * index + rng.gauss(0.0, 0.02)
        residual_beta.append(stock - beta * index)
        residual_dollar.append(stock - 1.0 * index)

    def variance(values):
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    var_beta, var_dollar = variance(residual_beta), variance(residual_dollar)
    result.passed = var_beta < var_dollar
    result.metrics = {"beta_adjusted_variance": var_beta,
                      "dollar_matched_variance": var_dollar,
                      "reduction": 1 - var_beta / var_dollar if var_dollar else None}
    result.detail = (f"beta-adjusted residual variance {var_beta:.6f} vs "
                     f"dollar-matched {var_dollar:.6f} "
                     f"({1 - var_beta / var_dollar:.0%} lower)")
    return result


# --------------------------------------------------------------------------
# 11. Option volatility value
# --------------------------------------------------------------------------

def test_option_value(seed: int = 11) -> SyntheticResult:
    """Options bought when expected realised vol exceeds implied should do better.

    Builds a set where IV-RV drives option P&L, then checks the model finds it.
    """
    result = SyntheticResult("Option volatility value")
    rng = _rng(seed)
    names = ["IV", "RV", "IV_minus_RV", "Delta", "Theta", "DTE"]
    n = 800
    X, y = [], []
    for _ in range(n):
        iv = rng.uniform(0.15, 0.80)
        rv = rng.uniform(0.15, 0.80)
        delta = rng.uniform(0.15, 0.85)
        theta = -rng.uniform(0.001, 0.02)
        dte = rng.uniform(20, 180)
        edge = rv - iv                        # positive = option is cheap
        pnl = 2.0 * edge + 0.3 * delta * rng.gauss(0, 0.2) + theta * dte / 30
        pnl += rng.gauss(0, 0.05)
        X.append([iv, rv, iv - rv, delta, theta, dte])
        y.append(pnl)

    dates = _dates(n)
    Xtr, ytr, Xte, yte = _split_chronological(X, y, dates)
    model = mllib.LinearModel(1.0).fit(Xtr, ytr, names)
    metrics = mllib.evaluate(yte, model.predict(Xte))
    importance = dict(model.importance())
    coefficients = dict(zip(names, model.coefficients))

    edge_matters = importance.get("IV_minus_RV", 0) > 0.15 or (
        importance.get("RV", 0) + importance.get("IV", 0) > 0.4)
    correct_sign = coefficients["IV_minus_RV"] < 0 or coefficients["RV"] > 0
    result.passed = edge_matters and correct_sign and (metrics["spearman"] or 0) > 0.5
    result.metrics = {"out_of_sample": metrics, "importance": importance}
    result.detail = (f"IV-RV share={importance.get('IV_minus_RV', 0):.2f}, "
                     f"spearman={metrics['spearman']:.3f}")
    return result


# --------------------------------------------------------------------------
# Suite
# --------------------------------------------------------------------------

TESTS: list[tuple[str, Callable]] = [
    ("Linear recovery", test_linear_recovery),
    ("Nonlinear interaction", test_nonlinear_interaction),
    ("Useless factor", test_useless_factor),
    ("Wrong weight", test_wrong_weight),
    ("Wrong sign", test_wrong_sign),
    ("Political interaction", test_political_interaction),
    ("Regime change", test_regime_change),
    ("Leakage prevention", test_leakage_detection),
    ("Hedge selection", test_hedge_selection),
    ("Proxy hedge sizing", test_proxy_hedge),
    ("Option value", test_option_value),
]


def run_all(seed_offset: int = 0) -> list[SyntheticResult]:
    """Run every synthetic validation test. Deterministic for a given offset."""
    results = []
    for index, (_name, fn) in enumerate(TESTS):
        try:
            results.append(fn(seed=index + 1 + seed_offset))
        except Exception as exc:
            failed = SyntheticResult(_name)
            failed.passed = False
            failed.detail = f"raised {type(exc).__name__}: {exc}"
            results.append(failed)
    return results


SYNTHETIC_DISCLAIMER = """\
**These tests validate the SOFTWARE, not the investment model.**

They generate data with a known built-in relationship and confirm the pipeline
recovers it: labels align, walk-forward respects chronology, linear and
nonlinear structure can be learned, regularization suppresses noise, leakage is
caught, and the hedge and option research paths work.

A green board means the machinery is correct. It says nothing about whether the
Shaffer Score predicts real markets — only real, aged, point-in-time
observations can answer that, and those accumulate one day at a time.

Synthetic observations are never written to the live tables and never mixed
into production training.
"""
