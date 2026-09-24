"""Shaffer Hedge: risk first, product second.

    position(s) -> risk vector (financial units) -> hedge objective -> eligible products -> per-product sizing unit
    -> optimiser (risk mismatch, cost, basis, liquidity, turnover) -> Raw Shaffer Hedge -> capped ML adjustment

Modules: `pricing` (valuation and Greeks), `products` (the product registry, contract conventions, eligibility),
`risk` (the universal risk vector), `engine` (objectives, candidates, sizing, optimiser, score, scenarios),
`history` (walk-forward hedge evaluation and the ML adjustment)."""
