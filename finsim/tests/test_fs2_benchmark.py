"""Frozen benchmark (immutable, reproducible, production-only content) and Directional gate v2 (prior-only test)."""
import json
import os
import random
import shutil
import tempfile
import unittest

from finsim2.data.store import Store
from finsim2.engine import directional as D
from finsim2.engine import lab as L
from finsim2.engine import weights as W


def _research(store):
    """Minimal research outputs, with a rejected challenger whose results must NOT enter the benchmark."""
    store.kv_set(W.RESEARCH_KEY, {"started": "t1", "horizons": {"1M": {
        "production_all": {"acc": 0.505, "ic": 0.01}, "baselines_all": {"best": "always_bullish", "best_acc": 0.58},
        "challengers": {"signal@class": {"walkforward": {"challenger": {"acc": 0.9}}, "gates": {"status": "REJECT"}}}}}})
    store.kv_set(D.RESEARCH_KEY, {"started": "t2", "horizons": {"1M": {
        "production_alpha": {"rank_ic": 0.019}, "wf_base_rate": {"all": 0.58},
        "directional": {"production (read as p = (1 + raw/100)/2)": {"walkforward": {"accuracy": 0.505}},
                        "prior:product": {"walkforward": {"accuracy": 0.591, "brier": 0.2414}},
                        "alpha+prior@global": {"walkforward": {"accuracy": 0.591, "brier": 0.2415}},
                        "signal+prior@asset": {"walkforward": {"accuracy": 0.12345}, "gates": {"status": "REJECT"}}}}}})
    reg = L.registry(store)
    reg["versions"] += [{"id": "shaffer-alpha-2.1-production", "kind": "shaffer-alpha", "status": "production"},
                        {"id": "shaffer-2.1-sig-signal-class-exp", "kind": "shaffer", "status": "challenger"},
                        {"id": "shaffer-directional-2.1-signal-prior-asset-exp", "kind": "shaffer-directional", "status": "challenger"}]
    L._save_registry(store, reg)


class Benchmark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "b.db"))
        _research(self.st)

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_content_is_production_only(self):
        meta = L.freeze_benchmark(self.st, "bm-test")
        self.assertEqual(meta["production"], {"score": "shaffer-2.1", "alpha": "shaffer-alpha-2.1-production", "hedge": "hedge-2"})
        content = L.benchmark(self.st, "bm-test")["content"]
        text = json.dumps(content)
        self.assertNotIn("-exp", text)                         # no challenger version
        self.assertNotIn("0.12345", text)                      # no challenger result
        self.assertNotIn("0.9", json.dumps(content["metrics"]["1M"]["production_score"]))
        self.assertEqual(content["metrics"]["1M"]["prior_only"]["brier"], 0.2414)
        self.assertEqual(content["directional_research_definition"]["status"], "research (not a production version)")
        self.assertIsNotNone(content["sources"]["weights_research"]["hash"])

    def test_immutable_and_reproducible(self):
        before = {k: self.st.kv_get(k) for k in (W.RESEARCH_KEY, D.RESEARCH_KEY)}
        meta = L.freeze_benchmark(self.st, "bm-test")
        self.assertEqual(before, {k: self.st.kv_get(k) for k in (W.RESEARCH_KEY, D.RESEARCH_KEY)})   # research untouched
        self.assertTrue(L.verify_benchmark(self.st, "bm-test")["ok"])
        with self.assertRaises(ValueError):
            L.freeze_benchmark(self.st, "bm-test")
        # the same inputs give the same content hash (reproducible), independent of dict order
        self.assertEqual(L._hash(L.benchmark_content(self.st)), meta["hash"])
        self.assertEqual(L._hash({"b": 1, "a": [1, 2]}), L._hash({"a": [1, 2], "b": 1}))
        # later research does not change the frozen benchmark
        self.st.kv_set(D.RESEARCH_KEY, {"started": "t3", "horizons": {}})
        self.assertTrue(L.verify_benchmark(self.st, "bm-test")["ok"])
        self.assertEqual(L.benchmark(self.st)["content"]["metrics"]["1M"]["prior_only"]["brier"], 0.2414)
        # tampering with the frozen content is detected
        c = self.st.kv_get("benchmark:bm-test:content")
        c["metrics"]["1M"]["prior_only"]["brier"] = 0.1
        self.st.kv_set("benchmark:bm-test:content", c)
        self.assertFalse(L.verify_benchmark(self.st, "bm-test")["ok"])

    def test_registry_and_latest(self):
        L.freeze_benchmark(self.st, "bm-a")
        L.freeze_benchmark(self.st, "bm-b")
        self.assertEqual(L.benchmark(self.st)["id"], "bm-b")
        frozen = [v for v in L.registry(self.st)["versions"] if v["kind"] == "benchmark"]
        self.assertEqual([v["id"] for v in frozen], ["bm-a", "bm-b"])
        self.assertTrue(all(v["status"] == "frozen" and v["hash"] for v in frozen))

    def test_refuses_ambiguous_production(self):
        reg = L.registry(self.st)
        reg["versions"].append({"id": "hedge-3", "kind": "hedge", "status": "production"})
        L._save_registry(self.st, reg)
        with self.assertRaises(ValueError):
            L.freeze_benchmark(self.st, "bm-x")
        self.assertIsNone(self.st.kv_get("benchmark:bm-x"))


def _recs(n_weeks=300, assets=6, seed=1):
    rnd = random.Random(seed)
    out = []
    for w in range(n_weeks):
        for a in range(assets):
            r = W.Rec()
            r.asset, r.wk, r.yr = f"A{a}", w, (0.01 if rnd.random() < 0.55 else -0.01)
            out.append(r)
    return out


class GateV2(unittest.TestCase):
    def test_lower_brier_and_log_loss_count_as_better(self):
        recs = _recs()
        prior = {i: 0.55 for i in range(len(recs))}
        good = {i: (0.75 if r.yr > 0 else 0.35) for i, r in enumerate(recs)}         # informative
        bad = {i: (0.35 if r.yr > 0 else 0.75) for i, r in enumerate(recs)}          # wrong way round
        g = D.paired(recs, 5, {"new": good, "prior": prior}, "new", "prior")
        self.assertGreater(g["brier_gain"], 0)
        self.assertGreater(g["logloss_gain"], 0)
        self.assertGreater(g["delta_acc"], 0)
        self.assertTrue(D.prior_gate(g))
        b = D.paired(recs, 5, {"new": bad, "prior": prior}, "new", "prior")
        self.assertLess(b["brier_gain"], 0)
        self.assertLess(b["logloss_gain"], 0)
        self.assertFalse(D.prior_gate(b))

    def test_matching_the_prior_is_not_skill(self):
        recs = _recs()
        prior = {i: 0.55 for i in range(len(recs))}
        same = D.paired(recs, 5, {"new": dict(prior), "prior": prior}, "new", "prior")
        self.assertEqual(same["brier_gain"], 0)
        self.assertFalse(D.prior_gate(same))

    def test_every_condition_is_required(self):
        ok = {"brier_t": 3.0, "logloss_gain": 0.001, "delta_acc": 0.01}
        self.assertTrue(D.prior_gate(ok))
        for k, v in (("brier_t", 1.9), ("logloss_gain", -0.001), ("delta_acc", 0.0)):
            self.assertFalse(D.prior_gate({**ok, k: v}), k)
        self.assertFalse(D.prior_gate(None))

    def test_sector_pair_is_exploratory_and_gate_not_wired(self):
        self.assertTrue(D.PAIR_ROLE[("alpha+prior@sector", "prior:product")].startswith("exploratory"))
        self.assertEqual(D.GATES_VERSION, "v2")
        import inspect
        self.assertNotIn("prior_gate", inspect.getsource(L.record_shadow))
        self.assertNotIn("prior_gate", inspect.getsource(L.stage))


if __name__ == "__main__":
    unittest.main()
