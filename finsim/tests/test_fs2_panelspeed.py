"""Speed-ups of the weekly live panel must leave every production output bit-for-bit unchanged."""
import os
import random
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from finsim2.data.store import Store
from finsim2.engine import shaffer as SH


def _yearly(rnd, years=("2020", "2021", "2022"), hs=(5, 21), sigs=("a", "b", "c")):
    return {y: {h: {s: (rnd.randint(1, 500), rnd.gauss(0, 3), rnd.gauss(0, 3), abs(rnd.gauss(9, 3)), abs(rnd.gauss(9, 3)), rnd.gauss(0, 2))
                    for s in sigs} for h in hs} for y in years}


class PriorCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "p.db"))

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_in_place_cache_update_equals_a_full_reload(self):
        rnd = random.Random(3)
        r = SimpleNamespace(store=self.st)
        sigs = ["a", "b", "c"]
        for aid, cls in (("A", "EQUITY"), ("B", "EQUITY"), ("C", "ETF"), ("D", "EQUITY")):
            SH.save_checkpoints(r, aid, cls, _yearly(rnd), sigs)
        before = SH.load_priors(r, "A", "EQUITY")                  # builds the decoded cache
        SH.save_checkpoints(r, "B", "EQUITY", _yearly(rnd), sigs)  # B changes: cache updated in place
        self.assertIsNotNone(getattr(r, "_prior_cache", None))
        fast = SH.load_priors(r, "A", "EQUITY")
        fresh = SH.load_priors(SimpleNamespace(store=self.st), "A", "EQUITY")   # a new process: full decode
        self.assertEqual(repr(fast), repr(fresh))                  # bit-for-bit, dict order included
        self.assertNotEqual(repr(before), repr(fast))              # and B's new sums are in it

    def test_new_asset_invalidates_the_cache(self):
        rnd = random.Random(4)
        r = SimpleNamespace(store=self.st)
        SH.save_checkpoints(r, "A", "EQUITY", _yearly(rnd), ["a", "b", "c"])
        SH.load_priors(r, "B", "EQUITY")
        SH.save_checkpoints(r, "B", "EQUITY", _yearly(rnd), ["a", "b", "c"])
        self.assertEqual(repr(SH.load_priors(r, "C", "EQUITY")), repr(SH.load_priors(SimpleNamespace(store=self.st), "C", "EQUITY")))


if __name__ == "__main__":
    unittest.main()
