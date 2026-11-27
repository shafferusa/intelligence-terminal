"""Live cross-sectional evidence for ranking challengers (engine/livexs.py): the weekly statistic and gate G3-XS."""
import datetime as dt
import os
import random
import shutil
import tempfile
import unittest

from finsim2.data.store import Store
from finsim2.engine import livexs as X

VID = "alpha-learned-1w-hierarchy-exp"
V = {"id": VID, "kind": "shaffer-alpha", "family": "learned", "status": "challenger", "validation": {"1W": {"gates": {}}},
     "live_expectation": {"lab": "1W", "rank_ic": 0.067, "sd_ic": 0.20, "d": 0.03, "sd_d": 0.21, "weeks": 925}}


class LiveXS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "x.db"))

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _panels(self, weeks, edge_c, edge_p, assets=80, seed=1, collapse_after=None, d0=dt.date(2026, 10, 5)):
        rnd = random.Random(seed)
        panels = []
        for w in range(weeks):
            d = (d0 + dt.timedelta(days=7 * w)).isoformat()
            panels.append({"date": d, "week": X._week(d), "assets": assets})
            ec = 0.0 if collapse_after is not None and w >= collapse_after else edge_c
            for k in range(assets):
                f = rnd.gauss(0, 1)
                y = f + rnd.gauss(0, 2.5)
                for model, e in ((f"shaffer:{VID}", ec), ("shaffer", edge_p)):
                    s = e * f + rnd.gauss(0, 1) * (1 - e)
                    self.st.add_prediction({"asset_id": f"A{k}", "horizon": "1W", "model": model, "model_version": "t", "made_on": d,
                                            "target_date": d, "raw": s, "realized": y, "source": "panel"})
        self.st.kv_set(X.PANEL_KEY, panels)

    def test_weekly_cross_section_and_gate(self):
        self._panels(60, 0.5, 0.0)
        rows = X.weekly(self.st, VID, "1W")
        self.assertEqual(len(rows), 60)
        self.assertTrue(all(r["n"] == 80 for r in rows))
        g = X.live_gate(self.st, V)
        self.assertTrue(g["checks"]["weeks"] and g["checks"]["edge"] and g["checks"]["vs_production"])
        self.assertGreater(g["mean_ic"], 0.1)
        self.assertIsNotNone(g["weeks_needed_vs_production"])
        self.assertGreater(g["weeks_needed_vs_production"], 150)       # beating production at t ≥ 2 takes years

    def test_not_enough_weeks_or_small_cross_section(self):
        self._panels(20, 0.5, 0.0)
        g = X.live_gate(self.st, V)
        self.assertFalse(g["passed"])
        self.assertFalse(g["checks"]["weeks"])

    def test_small_cross_section_is_not_counted(self):
        self._panels(3, 0.5, 0.0, assets=20, seed=4, d0=dt.date(2027, 1, 4))    # < MIN_XS names: not a cross-section
        self.assertEqual(X.weekly(self.st, VID, "1W"), [])

    def test_decay_alarm_fires_when_the_edge_disappears(self):
        self._panels(80, 0.5, 0.3, collapse_after=20)
        g = X.live_gate(self.st, {**V, "live_expectation": {**V["live_expectation"], "d": 0.15, "sd_d": 0.12}})
        self.assertIsNotNone(g["cusum"]["fired_at_week"])
        self.assertFalse(g["passed"])

    def test_validated_is_not_superior(self):
        """A pass of G3-XS and live superiority are separate: a small positive live Δ passes the gate while the
        superiority label stays NOT RESOLVED; a large one earns DEMONSTRABLY SUPERIOR LIVE."""
        self.assertEqual(X.superiority([])["status"], X.NONE)
        self.assertEqual(X.superiority([0.01, -0.02, 0.03, 0.0, 0.02, -0.01])["status"], X.UNRESOLVED)
        self.assertEqual(X.superiority([0.2, 0.25, 0.18, 0.22, 0.3, 0.21])["status"], X.SUPERIOR)
        self.assertEqual(X.superiority([-0.2, -0.25, -0.18, -0.22])["status"], X.WORSE)
        s = X.superiority([0.1, 0.0, 0.2, -0.05, 0.12])
        self.assertLess(s["ci95"][0], s["mean"])
        self.assertGreater(s["prob_positive"], 0.5)
        self._panels(60, 0.35, 0.25, seed=7)
        g = X.live_gate(self.st, V)
        self.assertIn("superiority_vs_production", g)
        self.assertEqual(g["superiority_vs_production"]["reference"], "production")
        self.assertIn(g["label"], ("LIVE VALIDATED (G3-XS)", "ACCUMULATING"))

    def test_hierarchy_is_also_judged_against_its_global_sibling(self):
        self._panels(10, 0.5, 0.0)
        for p in self.st.predictions(horizon="1W"):          # the global sibling = production's scores here
            if p["model"] == "shaffer":
                self.st.add_prediction({**{k: p[k] for k in ("asset_id", "horizon", "made_on", "target_date", "raw", "realized")},
                                        "model": "shaffer:alpha-learned-1w-global-exp", "model_version": "t", "source": "panel"})
        g = X.live_gate(self.st, V)
        self.assertEqual(g["vs_sibling"]["reference"], "alpha-learned-1w-global-exp")
        self.assertEqual(g["vs_sibling"]["weeks"], 10)
        self.assertAlmostEqual(g["vs_sibling"]["mean"], g["superiority_vs_production"]["mean"])

    def test_rank_ic_and_ranking_detection(self):
        self.assertAlmostEqual(X.rank_ic([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(X.rank_ic([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertTrue(X.is_ranking(V))
        self.assertFalse(X.is_ranking({**V, "family": "directional"}))
        self.assertFalse(X.is_ranking({**V, "validation": {"3M": {}}}))


if __name__ == "__main__":
    unittest.main()
