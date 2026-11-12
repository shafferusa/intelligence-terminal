"""The λ-aware hedge grader (hedge/hedgelive.py): frozen cells and the λ-specific live statistic."""
import random
import unittest

from finsim2.hedge import hedgelive as HLV


def _cases(n_dates=30, seed=2):
    rnd = random.Random(seed)
    out = []
    for k in range(n_dates):
        u = [rnd.gauss(0, 3000) for _ in range(21)]
        hp = [-0.9 * x + rnd.gauss(0, 500) for x in u]
        out.append({"date": f"2027-{1 + k // 28:02d}-{1 + k % 28:02d}", "end": "x", "book": "MACRO", "btype": "mixed portfolio",
                    "objective": "target_vol", "u": u, "ideal": [-x for x in u], "primary": "CMD:OIL", "regime": {"volatility": "low_vol"},
                    "arms": {"A": {"hp": hp, "cost": 400.0, "skew": 0.0, "mult": 1.0,
                                   "legs": [{"id": "USO", "type": "SPOT", "product_type": "etf", "q": -100.0, "notional": 9e5, "beta_usd": 0.0, "option": False}]}}})
    return out


class HedgeLive(unittest.TestCase):
    def test_claimed_cells_parse(self):
        v = {"eligible_cells": {"1M Commodity/target_vol@5.0": {"objective": "target_vol", "lam": 5.0, "multiple": 0.54}}}
        c = HLV.claimed_cells(v)[0]
        self.assertEqual((c["horizon"], c["node"], c["lam"], c["multiple"]), ("1M", "Commodity/target_vol", 5.0, 0.54))

    def test_cell_statistic_on_research_format_cases(self):
        cell = {"key": "1M Commodity/target_vol@10.0", "horizon": "1M", "node": "Commodity/target_vol", "objective": "target_vol",
                "lam": 10.0, "multiple": 0.5, "backtest_d": 1000.0}
        r = HLV.cell_live(None, cell, _cases())
        self.assertEqual(r["dates"], 30)
        self.assertTrue(r["checks"]["dates"])
        self.assertAlmostEqual(r["b"]["cost"], 0.5 * r["a"]["cost"])   # half the hedge: half the cost …
        self.assertGreater(r["b"]["basis_error"], r["a"]["basis_error"])  # … but further from a full ideal hedge
        self.assertFalse(r["checks"]["H4"])                    # so the fixed basis guard (+5%) fails, as it should
        self.assertIsNotNone(r["d"])
        few = HLV.cell_live(None, cell, _cases(5))
        self.assertFalse(few["passed"])
        self.assertFalse(few["checks"]["dates"])

    def test_other_nodes_are_not_counted(self):
        cell = {"key": "1M Equity/target_vol@10.0", "horizon": "1M", "node": "Equity/target_vol", "objective": "target_vol", "lam": 10.0, "multiple": 0.5}
        self.assertEqual(HLV.cell_live(None, cell, _cases())["dates"], 0)


if __name__ == "__main__":
    unittest.main()
