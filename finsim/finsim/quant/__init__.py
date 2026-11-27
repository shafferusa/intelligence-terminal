"""Quantitative library: one small, pure-Python function per equation in the analytics catalog.

Modules: stats, regression, timeseries, volatility, stochastic, ml, nn, portfolio,
fixed_income, derivatives, swaps, credit, linalg. `catalog` lists the equations and
`asset_metrics` evaluates the applicable ones on a single asset's price history.
"""
from .asset_metrics import METRIC_INFO, asset_metrics, score_table  # noqa: F401
from .catalog import EQUATIONS, SECTIONS, catalog  # noqa: F401
