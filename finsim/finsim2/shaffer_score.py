"""The Shaffer Score: one quantitative score per tradeable asset, from an algorithm being developed separately.

This module is its slot. FinSim2 calls `score(metrics, asset)` for every asset on the Analytics scoreboard and shows
the result in the Shaffer Score column, ranks by it on the Shaffer Score page, and hands it the same inputs the rest of
the scoreboard uses. Until the algorithm lands `score` returns None and the pages say it is in development.

To plug the algorithm in, either edit `score` below (and set VERSION), or drop a file with the same two names
(`VERSION` and `score`) at ~/.finsim2/shaffer_score.py (or the path in FINSIM2_SHAFFER); that file wins when present.

Inputs, per asset:
  metrics: the scoreboard's statistics (see finsim.quant.asset_metrics.METRIC_INFO for every key): returns over
           several horizons, annualised return and volatility, EWMA and GARCH volatility, Sharpe, Sortino, beta,
           Jensen's alpha, skew, kurtosis, AR(1) coefficient and half-life, the ADF statistic, z-score, momentum,
           drawdown, VaR and expected shortfall. A value is None when the history is too short to compute it.
  asset:   id, name, asset class, sector, country, currency, last price, and for bonds yield, duration and rating.
Output: a float (higher = better), or None to leave the asset unscored.
"""
from __future__ import annotations

import importlib.util
import os
from typing import Dict, Optional

NAME = "Shaffer Score"
VERSION: Optional[str] = None          # set when the algorithm is in; None = in development
RANGE = (0.0, 100.0)                   # the scale the page draws its bars on


def score(metrics: Dict, asset: Dict) -> Optional[float]:
    """The score for one asset. In development: returns None until the algorithm replaces this body."""
    return None


# ------------------------------------------------------------------ an override file, so the algorithm can arrive without a code change
def _override_path() -> str:
    return os.environ.get("FINSIM2_SHAFFER") or os.path.join(os.environ.get("FINSIM2_HOME") or os.path.join(os.path.expanduser("~"), ".finsim2"), "shaffer_score.py")


def load():
    """(version, score function, source): the override file when it exists and loads, else this module."""
    path = _override_path()
    if os.path.isfile(path):
        try:
            spec = importlib.util.spec_from_file_location("finsim2_shaffer_override", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if callable(getattr(mod, "score", None)):
                return getattr(mod, "VERSION", "custom"), mod.score, path
        except Exception as e:          # a broken override must not take the scoreboard down
            return None, score, f"{path} (failed to load: {e!r})"
    return VERSION, score, "finsim2/shaffer_score.py"


def status() -> Dict:
    version, _, source = load()
    return {"name": NAME, "version": version, "enabled": version is not None, "source": source, "range": list(RANGE),
            "state": "live" if version is not None else "in development",
            "override_path": _override_path()}


def safe_score(fn, metrics: Dict, asset: Dict) -> Optional[float]:
    try:
        v = fn(metrics, asset)
        return None if v is None else float(v)
    except Exception:
        return None
