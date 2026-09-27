"""The weekly live panel in worker processes (research.ShafferBatch) and the exactness of the Shaffer sweep's shortcuts.

The panel must record exactly what the one-asset-at-a-time loop records, and the sweep's prefix sums, lazily built
evidence and prior aggregation must reproduce the step-by-step computations bit for bit."""
import math
import os
import shutil
import sqlite3
import struct
import tempfile
import unittest

from finsim2 import shaffer_score as cfg
from finsim2.data.store import Store
from finsim2.engine import lab, learned, livexs
from finsim2.engine import shaffer as sh
from finsim2.engine.research import Research, ShafferBatch
from test_finsim2 import build_store


def bits(o):
    """A comparable form that tells 0.0 from -0.0 and keeps dict orders."""
    if isinstance(o, float):
        return ("f", struct.pack("<d", o))
    if isinstance(o, dict):
        return ("d", [(k, bits(v)) for k, v in o.items()])
    if isinstance(o, (list, tuple)):
        return (type(o).__name__, [bits(v) for v in o])
    return o


def written(path: str) -> dict:
    """Everything the panel writes that matters: ledger rows (all columns but the time written) and the cached
    results and checkpoints, as stored."""
    c = sqlite3.connect(path)
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(predictions)")]
        rows = [tuple(v for k, v in zip(cols, r) if k != "created_at") for r in c.execute("SELECT * FROM predictions ORDER BY id")]
        kv = c.execute("SELECT key, value FROM kv WHERE key LIKE 'shaffer2:%' OR key LIKE 'shaffer_cp:%' ORDER BY key").fetchall()
        return {"rows": rows, "kv": kv}
    finally:
        c.close()


FAILING = "SPY"           # its sweep raises after its (new) checkpoints are known: the loop stores nothing for it


def _score_at(orig):
    def score_at(self, *a, **k):
        if self.asset_id == FAILING:
            raise RuntimeError("boom")
        return orig(self, *a, **k)
    return score_at


class Panel(unittest.TestCase):
    """A universe whose stored checkpoints lag (the first panel of a year) or are missing (new assets), with an asset
    whose sweep fails: the parallel panel equals the serial loop row for row and byte for byte."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.base = os.path.join(cls.tmp, "base.db")
        st = build_store(cls.base, n=1400)
        r = Research(st)
        cls.assets = sorted(a["id"] for a in st.assets() if st.price_count(a["id"]))
        for a in cls.assets + ["NOPE"]:                     # the research universe (NOPE has no prices)
            st.put_lab_records(a, "1W", lab.SIG_VERSION, "t", [["2016-01-04", 0.0, 0.0, 0.0, {}]])
        for a in cls.assets:                               # last week's run: cached results and checkpoints
            r.shaffer_full(a)
        st._write(lambda c: c.execute("DELETE FROM kv WHERE key LIKE 'shaffer2:%'"))
        st._write(lambda c: c.execute("DELETE FROM predictions"))
        for k, a in enumerate(cls.assets):
            key = sh.checkpoint_key(a)
            if k == 3:
                st.kv_delete(key)                          # a new asset
            elif k % 2 == 0:                               # stored before the latest January
                cls_, years, hs, sigs, arr = sh.parse_checkpoints(st.kv_get(key))
                keep = len(years) - 1
                st.kv_set(key, {**st.kv_get(key), "years": list(years[:keep]), "data": sh._pack(arr.tolist()[:keep * len(hs) * len(sigs) * 6])})
        reg = lab.registry(st)
        vid = "alpha-learned-1w-hierarchy-exp"
        reg["versions"].append({"id": vid, "kind": "shaffer-alpha", "family": "learned", "status": "challenger",
                                "validation": {"1W": {"gates": {"G1_discovery": True, "G2_confirmation": True, "d_rank_ic": 0.03, "t": 2.4}}}})
        lab._save_registry(st, reg)
        names = sorted(cfg.FAMILY_OF)
        st.kv_set(learned.LIVE_KEY, {vid: {"lab": "1W", "names": names, "depth": "global", "scale": 0.8, "means": [0.0] * len(names),
                                           "coefs": {"global": [math.sin(i) / 5 for i in range(len(names))]}}})
        st.close()
        cls.universe = sorted(cls.assets + ["NOPE"])
        cls.serial_rec, cls.serial = cls._panel("serial.db", 1)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _panel(cls, name: str, workers: int) -> tuple:
        path = os.path.join(cls.tmp, name)
        shutil.copy(cls.base, path)
        st = Store(path)
        orig = sh.ShafferRun.score_at
        sh.ShafferRun.score_at = _score_at(orig)             # forked workers inherit it
        try:
            said = []
            rec = livexs.record_panel(Research(st), progress=said.append, force=True, workers=workers)
            rec["said"] = [m for m in said if "redone here" in m]
        finally:
            sh.ShafferRun.score_at = orig
            st.close()
        return rec, written(path)

    def test_parallel_panel_writes_what_the_serial_loop_writes(self):
        rec1, serial = self.serial_rec, self.serial
        rec3, parallel = self._panel("parallel.db", 3)
        self.assertEqual(rec1["assets"], len(self.universe) - 1)
        self.assertEqual(rec1["errors"], [f"{FAILING}: RuntimeError: boom"])
        self.assertGreater(rec1["rows"], 0)                          # the challenger was scored
        # the failing asset is redone here (raising again), and so is every asset after it: the workers assumed it
        # stores its new checkpoints
        self.assertEqual(rec3.pop("said"), [f"live panel: 11 assets swept on 3 workers (2 redone here)"])
        rec1 = dict(rec1)
        self.assertEqual(rec1.pop("said"), [])
        self.assertEqual({k: v for k, v in rec1.items() if k != "seconds"}, {k: v for k, v in rec3.items() if k != "seconds"})
        self.assertTrue(serial["rows"])
        self.assertEqual(serial["rows"], parallel["rows"])
        self.assertEqual(serial["kv"], parallel["kv"])
        self.assertEqual(len([k for k, _ in serial["kv"] if k.startswith("shaffer2:")]), len(self.universe) - 1)

    def test_disagreement_falls_back_to_the_serial_computation(self):
        """Once what the workers assumed about an earlier asset's checkpoints cannot be trusted (here from the third
        asset on), every later asset is swept in the calling process with the checkpoints the loop has stored."""
        from finsim2.engine.tracking import record_shaffer
        serial = self.serial
        path = os.path.join(self.tmp, "fallback.db")
        shutil.copy(self.base, path)
        st = Store(path)
        orig = sh.ShafferRun.score_at
        sh.ShafferRun.score_at = _score_at(orig)
        try:
            r = Research(st)
            batch = ShafferBatch(r, self.universe, workers=3)
            try:
                for k, a in enumerate(self.universe):
                    if k == 2:
                        batch.agree = False
                    try:
                        record_shaffer(st, r.panel(), a, batch.take(a))
                        lab.record_shadow(r, a)
                    except RuntimeError:
                        self.assertEqual(a, FAILING)
            finally:
                batch.close()
        finally:
            sh.ShafferRun.score_at = orig
            st.close()
        self.assertEqual(batch.here, len(self.universe) - 2)
        self.assertEqual(serial["rows"], written(path)["rows"])
        self.assertEqual(serial["kv"], written(path)["kv"])


class SweepExactness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = build_store(os.path.join(cls.tmp, "r.db"), n=1500)
        cls.r = Research(cls.store)
        for a in ("QQQ", "TLT", "GOLD"):
            cls.r.shaffer_full(a)                          # stored checkpoints, for the priors
        cls.srun = sh.ShafferRun(cls.r, "SPY")

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_prefix_sums_are_the_running_sums_of_the_step_by_step_sweep(self):
        """The evidence sums `_sums` reads at each refit equal a running `+=` over the observations as they join."""
        run = self.srun
        n, z = run.n, run.z
        refits = [t for t in run.refits if t < n]
        need = [True] * len(refits)
        dims = list(run.regimes)
        for lab_, h in run.horizons[:4]:
            sums = run._sums(h, n, refits, need, [], full=True)
            S = {s: [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0.0, 0, 0.0, {}] for s in run.signals}
            r = 0
            for tau in range(n):
                t = tau - h - 1
                if t >= 0 and t % sh._step(h) == 0 and run.y[h][t] is not None:
                    y = run.y[h][t]
                    states = [run.regimes[d][t] for d in dims] if h <= sh.REG_MAX_H else ()
                    for s in run.signals:
                        x = z[s][t]
                        if x is None:
                            continue
                        e = S[s]
                        e[0] += 1; e[1] += x; e[2] += y; e[3] += x * x; e[4] += y * y; e[5] += x * y
                        if abs(x) > 0.25:
                            e[6] += 1
                            if (x > 0) == (y > 0):
                                e[7] += 1
                        if x > 0.5:
                            e[8] += 1; e[9] += y
                        elif x < -0.5:
                            e[10] += 1; e[11] += y
                        for st in states:
                            if st is not None:
                                acc = e[12].setdefault(st, [0, 0.0, 0.0, 0.0, 0.0, 0.0])
                                acc[0] += 1; acc[1] += x; acc[2] += y; acc[3] += x * x; acc[4] += y * y; acc[5] += x * y
                while r < len(refits) and refits[r] == tau:
                    for i, (s, (cp, cpn, extra, reg)) in enumerate(sums.items()):
                        e = S[s]
                        self.assertEqual(bits(cp[r]), bits(tuple(e[:6])), (lab_, s, r))
                        self.assertEqual(cpn[r], e[0])
                        self.assertEqual(bits(extra[r]), bits((e[6], e[7], e[8], e[9], e[10], e[11])))
                        if reg is not None:
                            if i % 2:
                                reg.rows()                 # both ways of reading them: every refit's sums built, or one refit's
                            got = [(st, acc) for st, acc in reg.at(r) if acc[0]]
                            self.assertEqual(bits(got), bits([(st, tuple(acc)) for st, acc in e[12].items()]), (lab_, s, r))
                    r += 1

    def test_lazy_evidence_is_the_full_evidence_where_it_is_read(self):
        """Unless full, a muted signal's record is only its status and w = 0; every other record is the full one."""
        run = self.srun
        n = run.n
        refits = [t for t in run.refits if t < n]
        need = [True] * len(refits)
        pers_at, _ = run._comovement(n, refits, need)
        lab_, h = "1M", 21
        sums = run._sums(h, n, refits, need, [], full=True)
        muted = 0
        for r in range(len(refits) // 2, len(refits), 7):
            tau = refits[r]
            full = run._evidence(sums, h, tau, r, r, pers_at[r], run._prior_at(tau, h), full=True)
            lazy = run._evidence(sums, h, tau, r, r, pers_at[r], run._prior_at(tau, h), full=False)
            self.assertEqual(list(full), list(lazy))
            for s, e in full.items():
                f = {k: (v.full() if k == "_reg" else v) for k, v in e.items()}
                g = {k: (v.full() if k == "_reg" else v) for k, v in lazy[s].items()}
                if "ps" in e and not e["delta"]:
                    muted += 1
                    self.assertEqual(g, {"status": e["status"], "w": 0.0})
                    self.assertEqual(e["w"], 0.0)
                else:
                    self.assertEqual(bits(f), bits(g), s)
        self.assertGreater(muted, 0)

    def test_research_records_do_not_change_the_result(self):
        """shaffer_full sweeps without the ML Lab's research records: the summary is the same, bit for bit."""
        a = sh.ShafferRun(self.r, "IWM")
        b = sh.ShafferRun(self.r, "IWM")
        ra, rb = a.run(save=False), b.run(save=False, records=False)
        self.assertTrue(a.lab_records and a.sig_records and a.fam_records["1M"])
        self.assertEqual(b.lab_records, {})
        self.assertEqual(bits(sh.summarize(ra, a)), bits(sh.summarize(rb, b)))

    def test_priors_are_the_ordered_sums_over_the_other_assets(self):
        per = sh.prior_cache(self.r)["per"]

        def reference(asset_id, cls):                      # the sums, key by key, in the order of the stored assets
            agg = {}
            for aid, (acls, years, hs, sigs, arr) in per.items():
                if aid == asset_id:
                    continue
                flat, pos = arr.tolist(), 0
                for y in years:
                    for h in hs:
                        for sg in sigs:
                            slot = agg.setdefault((y, h, sg), [[0.0] * 6, [0.0] * 6])
                            for j in range(6):
                                slot[1][j] += flat[pos + j]
                                if acls == cls:
                                    slot[0][j] += flat[pos + j]
                            pos += 6
            out = {}
            for (y, h, sg), (c, g) in agg.items():
                out.setdefault(y, {}).setdefault(h, {})[sg] = (tuple(c), tuple(g))
            return out

        for a, cls in (("SPY", "ETF"), ("QQQ", "ETF"), ("ZZZ", "EQUITY")):
            ref = reference(a, cls)
            self.assertTrue(ref)
            self.assertEqual(bits(sh.aggregate_priors(per, a, cls)), bits(ref))
            sigs = ["ret_1d", "ret_3m", "vol_20"]
            filtered = sh.aggregate_priors(per, a, cls, sigs)
            self.assertEqual(bits(filtered), bits({y: {h: {s: v for s, v in d.items() if s in sigs} for h, d in yd.items()}
                                                   for y, yd in ref.items()}))


if __name__ == "__main__":
    unittest.main()
