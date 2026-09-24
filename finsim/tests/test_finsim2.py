"""FinSim2: a portfolio-manager save from an amount alone, the analytics workspace, the Shaffer Score slot, the launcher."""
import os
import tempfile
import unittest
from unittest import mock

from finsim.api.service import Service
from finsim.store import EventStore
from finsim.world import CommandError
from finsim2 import shaffer_score
from finsim2.server import Router2


class FinSim2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Router2(Service(EventStore(":memory:")))
        out = cls.r.dispatch("POST", "/api/fs2/worlds", {}, {"name": "Test Fund", "amount": 25_000_000, "mode": "PRACTICE", "start_date": "2025-06-02"})
        cls.wid, cls.pid = out["world_id"], out["portfolio_id"]
        cls.r.dispatch("POST", f"/api/worlds/{cls.wid}/advance", {}, {"days": 2})

    def test_save_is_one_pm_book_with_the_amount(self):
        w = self.r.dispatch("GET", f"/api/worlds/{self.wid}", {}, {})
        self.assertEqual(len(w["portfolios"]), 1)
        self.assertEqual(w["portfolios"][0]["job"], "PORTFOLIO_MANAGER")
        d = self.r.dispatch("GET", f"/api/worlds/{self.wid}/portfolios/{self.pid}/career", {}, {})
        self.assertEqual(float(d["contributed_capital"]), 25_000_000)

    def test_amount_is_checked(self):
        for bad in (None, 0, -5, 2e12, "x"):
            with self.assertRaises(CommandError):
                self.r.dispatch("POST", "/api/fs2/worlds", {}, {"amount": bad, "mode": "PRACTICE"})
        with self.assertRaises(CommandError):
            self.r.dispatch("POST", "/api/fs2/worlds", {}, {"amount": 1e6, "mode": "SIDEWAYS"})

    def test_scoreboard(self):
        sb = self.r.dispatch("GET", f"/api/fs2/worlds/{self.wid}/scores", {}, {})
        ids = {row["id"] for row in sb["rows"]}
        for sid in ("AAPL", "SPY", "SAP-DE", "FX:EUR", "FX:JPY"):
            self.assertIn(sid, ids)
        self.assertFalse(any(row["asset_class"] == "OPTION" for row in sb["rows"]))
        aapl = next(row for row in sb["rows"] if row["id"] == "AAPL")
        for k in sb["metric_info"]:
            self.assertIn(k, aapl)
        self.assertIsNotNone(aapl["vol_ann"])
        self.assertIsNone(aapl["shaffer"])
        self.assertEqual(sb["shaffer"]["state"], "in development")

    def test_asset_analytics_and_encoded_ids(self):
        a = self.r.dispatch("GET", f"/api/fs2/worlds/{self.wid}/analytics/FX%3AEUR", {}, {})
        self.assertEqual(a["asset"]["asset_class"], "FX")
        self.assertTrue(a["series"]["closes"])
        b = self.r.dispatch("GET", f"/api/fs2/worlds/{self.wid}/analytics/AAPL", {}, {})
        self.assertTrue(any(x["id"] == "19" and x["value"] is not None for x in b["applied"]))     # Sharpe on a real series

    def test_equations_and_source(self):
        cat = self.r.dispatch("GET", "/api/fs2/equations", {}, {})
        ids = {e["id"] for e in cat["equations"]}
        self.assertTrue({str(i) for i in range(1, 119)} <= ids)
        src = self.r.dispatch("GET", "/api/fs2/equations/101/source", {}, {})
        self.assertIn("def ", src["source"])

    def test_shaffer_override_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "shaffer_score.py")
            with open(p, "w") as f:
                f.write("VERSION = 'test'\ndef score(metrics, asset):\n    return 42.0 if asset['asset_class'] != 'FX' else None\n")
            with mock.patch.dict(os.environ, {"FINSIM2_SHAFFER": p}):
                self.assertTrue(shaffer_score.status()["enabled"])
                sb = self.r.dispatch("GET", f"/api/fs2/worlds/{self.wid}/scores", {}, {})
                self.assertEqual(next(row for row in sb["rows"] if row["id"] == "AAPL")["shaffer"], 42.0)
                self.assertIsNone(next(row for row in sb["rows"] if row["id"] == "FX:EUR")["shaffer"])
        self.assertFalse(shaffer_score.status()["enabled"])

    def test_broken_override_does_not_break_scores(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "shaffer_score.py")
            with open(p, "w") as f:
                f.write("VERSION = 'bad'\ndef score(metrics, asset):\n    raise ValueError('boom')\n")
            with mock.patch.dict(os.environ, {"FINSIM2_SHAFFER": p}):
                sb = self.r.dispatch("GET", f"/api/fs2/worlds/{self.wid}/scores", {}, {})
                self.assertTrue(all(row["shaffer"] is None for row in sb["rows"]))

    def test_launcher_uses_its_own_home_and_module(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"FINSIM2_HOME": d}, clear=False):
            from finsim2.__main__ import configure
            from finsim import app
            saved = (app.serve_argv, app.APP_NAME, app.legacy_db_path)
            try:
                configure()
                self.assertEqual(app.home(), d)
                self.assertEqual(app.port(), 8865)
                self.assertTrue(app.db_path().endswith("finsim2.db"))
                argv = app.serve_argv()
                self.assertEqual(argv[1:4], ["-m", "finsim2", "serve"])
            finally:
                app.serve_argv, app.APP_NAME, app.legacy_db_path = saved
                for k in ("FINSIM_HOME", "FINSIM_PORT", "FINSIM_DB"):
                    os.environ.pop(k, None)

    def test_static_ui_present(self):
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim2", "static")
        with open(os.path.join(here, "index.html")) as f:
            self.assertIn("FinSim2", f.read())
        self.assertTrue(os.path.exists(os.path.join(here, "app.js")))


if __name__ == "__main__":
    unittest.main()
