"""The Shaffer Score: one quantitative score per tradeable asset, from an algorithm being developed separately.

This module is its slot. When a score is live, FinSim2 shows it on every researched asset's page and as a column in
the Markets table, and the Quant Lab's Shaffer Score tab says which version is running. Until the algorithm lands
`score` returns None and the pages say it is in development.

To plug the algorithm in, either edit `score` below (and set VERSION), or drop a file with the same two names
(`VERSION` and `score`) at ~/.finsim2/shaffer_score.py (or the path in FINSIM2_SHAFFER); that file wins when present.

Inputs, per asset (from its research bundle, point in time as of the latest close):
  metrics: every feature's latest value by name (see finsim2.engine.features.FEATURES: returns, momentum, volatility
           incl. GARCH, drawdown, beta, skew, RSI, Hurst, ADF, half-life, valuation, fundamentals, rates, credit,
           macro), plus "z" (each feature's capped expanding z-score), "quant_score", "ml_score" and "confidence"
           (each by horizon, 1D .. 10Y) and "regime" (the current regime labels). A value is None when unavailable.
  asset:   id, name, asset class, sector, currency, price and as-of date.
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
