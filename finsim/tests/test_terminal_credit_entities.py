"""Single-name CDS reference entities (CDS_SINGLE_NAME_PLAN rev 2, package B2: ``finsim/terminal/credit_entities.py``, contract C-ENT).

Every test runs on the plain repo tree (the module edits no existing file and needs no hook): a temp database (``terminal_helpers.make_runtime``,
FakeClock), the module's two light commands registered on the real CommandBus, and a getter that serves the SYNTHETIC SEC payloads in the real
API shapes from ``tests/fixtures/terminal/cds/edgar`` (``gen_edgar_fixtures.py`` writes them).  Nothing touches the network: ``sec_get`` is
wrapped so a call without a getter fails the test, and the 4-requests-a-second pacer runs on an injected clock.

Hand-worked numbers used below (cents in the code; whole dollars here)
- R2 (AAPL-like): LongTermDebt 82,300,000,000 + CommercialPaper 1,997,000,000 (no ShortTermBorrowings: CP is its own whole) = 84,297,000,000.
- R6 (ORCL at 2026-08-31): LongTermNotesAndLoans 117,712,000,000 + NotesPayableCurrent 7,625,000,000 = 125,337,000,000; the 2026-05-31
  10-K's R1 129,541,000,000 is an older end and is not used.
- R4b (VZ): LongTermDebtAndCapitalLeaseObligations 143,448,000,000 + LongTermDebtCurrent 21,783,000,000 = 165,231,000,000.
- R6 (KBH): NotesAndLoansPayable 2,109,145,000 alone.
- Parts rule: PEP STB 10,602,000,000 with CP 6,100,000,000 <= STB -> 10,602,000,000 (+ LongTermDebt 37,000,000,000 = 47,602,000,000);
  ICE STB = CP = 1,218,000,000 -> 1,218,000,000 (+ 18,000,000,000 = 19,218,000,000); CPBG CP 800,000,000 > STB 500,000,000 -> both,
  1,300,000,000 (+ 2,000,000,000 = 3,300,000,000); CVCO NotesPayable 900,000,000 with a smaller convertible 300,000,000 -> 900,000,000.
- R3 (JPM-like): LongTermDebtNoncurrent 420,000,000,000 + ShortTermBorrowings 50,000,000,000 = 470,000,000,000; Deposits 2.4T never count.
- Remainder (2026-10-29 live check): UTPF lists only preferred series (UTPF-PA, UTPF-PB) -> NO_LISTED_COMMON although its LongTermDebt
  5,000,000,000 and 100,000,000 shares would qualify; PARENTCO's 2026-06-30 joint 10-Q (LongTermDebt 29,500,000,000) is listed under its
  subsidiary PRNE's CIK with the parent's name -> left out of PRNE (NO_DEBT) and PRNT keeps its own 2026-03-31 LongTermDebt 28,000,000,000
  (212 days old on 2026-10-29: 212 / 30.4375 = 6.97 -> "7 months old", amber).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import types
import unittest
from datetime import date

from tests.terminal_helpers import make_runtime
from finsim.terminal import api as _api, config, credit_entities as ce
from finsim.terminal.clock import epoch_of
from finsim.terminal.quotes.http import FetchError, ProviderHealth

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "cds", "edgar")
B = 100 * 1_000_000_000        # one billion dollars in cents
M = 100 * 1_000_000            # one million dollars in cents


def usd(dollars: int) -> int:
    return int(dollars) * 100


class FakeTime:
    """The pacer's clock: ``sleep`` moves it forward (no real waiting)."""

    def __init__(self):
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.t += float(d)


class FixtureSEC:
    """The test getter (``get_with_policy(getter=...)`` hands it ``(url, ua, timeout, accept)``): the fixture file for the URL, as SEC served it
    on ``as_of`` - a frame keeps only the facts filed by then and, per CIK, the last filed one (the frames API's rule); 404 otherwise."""

    def __init__(self, as_of: str = "2026-10-29", ft: FakeTime = None, too_large=()):
        self.as_of = as_of
        self.ft = ft
        self.too_large = set(too_large)
        self.calls = []
        with open(os.path.join(FIX, "filed.json"), encoding="utf-8") as fh:
            self.filed = json.load(fh)

    def path_of(self, url: str):
        if url == ce.UNIVERSE_URL:
            return os.path.join(FIX, "company_tickers_exchange.json"), "tickers"
        pre = "https://data.sec.gov/api/xbrl/frames/"
        if url.startswith(pre):
            return os.path.join(FIX, "frames", *url[len(pre):].split("/")), "frame"
        pre = "https://data.sec.gov/submissions/"
        if url.startswith(pre):
            return os.path.join(FIX, "submissions", url[len(pre):]), "submissions"
        pre = "https://data.sec.gov/api/xbrl/companyfacts/"
        if url.startswith(pre):
            return os.path.join(FIX, "companyfacts", url[len(pre):]), "companyfacts"
        return None, None

    def __call__(self, url, ua, timeout, accept):
        self.calls.append((url, ua, None if self.ft is None else self.ft.now()))
        path, kind = self.path_of(url)
        if kind == "companyfacts" and any(url.endswith(f"CIK{c:010d}.json") for c in self.too_large):
            raise FetchError("response too large", retryable=False)
        if path is None or not os.path.isfile(path):
            raise FetchError("HTTP 404", status=404)
        with open(path, "rb") as fh:
            raw = fh.read()
        if kind != "frame":
            return raw
        doc = json.loads(raw)
        best = {}
        for r in doc["data"]:
            f = self.filed.get(r["accn"], {}).get("filed", "9999-12-31")
            if f > self.as_of:
                continue
            key = (f, r["accn"])
            if r["cik"] not in best or key > best[r["cik"]][0]:
                best[r["cik"]] = (key, r)
        doc["data"] = [v[1] for v in best.values()]
        doc["pts"] = len(doc["data"])
        if not doc["data"]:
            raise FetchError("HTTP 404", status=404)
        return json.dumps(doc).encode("utf-8")


class Base(unittest.TestCase):
    AT = "2026-10-29T08:00:00-04:00"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-credit-ent-")
        self.rt = make_runtime(self.tmp, at=self.AT)
        ce.register(self.rt.bus)
        self.rt.db.ensure_schema_ext(ce.SOURCE, ce.SCHEMA)
        ce._CLOCK[0] = self.rt.clock
        self.ft = FakeTime()
        self._pacer = ce.PACER
        ce.PACER = ce.Pacer(ce.SEC_RPS, sleep=self.ft.sleep, now=self.ft.now)
        self._gwp = ce.get_with_policy

        def guarded(url, timeout=20.0, accept="application/json", getter=None, max_bytes=config.MAX_BYTES):
            if getter is None:
                raise AssertionError(f"a test reached the network: {url}")
            return self._gwp(url, timeout=timeout, accept=accept, getter=getter, max_bytes=max_bytes)
        ce.get_with_policy = guarded

    def tearDown(self):
        ce.PACER = self._pacer
        ce.get_with_policy = self._gwp
        ce._CLOCK[0] = None
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def online(self):
        """A provider set that is not offline, with a real health table (the SEC row)."""
        self.rt.providers = types.SimpleNamespace(offline=False, health=ProviderHealth(lambda: int(self.rt.clock.epoch())))
        return self.rt.providers

    def full_run(self, as_of: str = "2026-10-29", at: str = None) -> dict:
        if at is not None:
            self.rt.clock.set(at)
        g = FixtureSEC(as_of, self.ft)
        res = ce.run_refresh(self.rt, int(self.rt.clock.epoch()), "full", getter=g)
        res["getter"] = g
        return res

    def fund(self, cik: int):
        with self.rt.db.reader() as conn:
            return ce.fundamentals_now(conn, cik, self.rt.clock.today())

    def ent(self, key):
        with self.rt.db.reader() as conn:
            return ce.entity(conn, key, self.rt.clock.today())


# ---- the universe (B2 task 2) ------------------------------------------------------------------------------------------------------------
class UniverseTests(Base):
    def test_exchange_allowlist_and_primary_ticker(self):
        with open(os.path.join(FIX, "company_tickers_exchange.json"), "rb") as fh:
            raw = fh.read()
        u = ce.parse_universe(raw, master={"JPM", "AAPL"})
        ents = u["entities"]
        # kept: NYSE, Nasdaq, CBOE, "cboe" (no case), TXSE; dropped: OTC, null and the unknown "NYSE American" (counted once)
        self.assertEqual(("CBOE", "CBOE", "TXSE"), (ents[900003]["exchange"], ents[900004]["exchange"], ents[900005]["exchange"]))
        for cik in (900006, 900007, 900008):
            self.assertNotIn(cik, ents)
        self.assertEqual({"NYSE American": 1}, u["unknown_exchanges"])
        self.assertEqual(1, u["rows_by_exchange"]["OTC"])
        self.assertEqual(1, u["rows_by_exchange"]["null"])
        # JPMorgan: AMJB (an ETN) is first in the file, JPM is in the master -> JPM; the others are kept as its other tickers
        self.assertEqual(("JPM", ["AMJB", "JPM-PC"]), (ents[19617]["ticker"], ents[19617]["tickers"]))
        # without JPM in the master the first common-looking ticker wins (the ETN cannot be told from a stock by its symbol: the master rule)
        self.assertEqual("AMJB", ce.parse_universe(raw, master=set())["entities"][19617]["ticker"])
        # a warrant (Nasdaq fifth letter W) first in the file: the primary is the common WFCO
        self.assertEqual(("WFCO", ["WFCOW"]), (ents[900009]["ticker"], ents[900009]["tickers"]))
        # only preferred series: the first symbol is shown (the entity stays searchable); it has no listed common stock
        self.assertEqual(("UTPF-PA", ["UTPF-PB"]), (ents[900017]["ticker"], ents[900017]["tickers"]))
        self.assertFalse(ce.has_listed_common(ents[900017]))
        self.assertTrue(ce.has_listed_common(ents[900017], stock=True))            # a master stock with that id would be its equity
        self.assertTrue(ce.has_listed_common(ents[900009]))
        # the pattern rule: preferred / warrant / unit / right suffixes are not common; class shares are
        self.assertEqual([False, False, False, True, True, False],
                         [ce._common_looking(t, e) for t, e in (("JPM-PC", "NYSE"), ("ACAB-WT", "NYSE"), ("SPAC-U", "NYSE"), ("BRK-B", "NYSE"),
                                                                 ("GOOGL", "Nasdaq"), ("ABCDW", "Nasdaq"))])

    def test_nasdaq_row_count(self):
        with open(os.path.join(FIX, "company_tickers_exchange.json"), "rb") as fh:
            u = ce.parse_universe(fh.read(), master=set())
        # Nasdaq rows: AAPL, PEP, SMLC, WFCOW, WFCO, CVCO, AMND = 7; NYSE 21 (ORCL VZ KBH ICE AMJB JPM JPM-PC F TRV PLD TSM OLDB BNKC CPBG MLTC
        # WAOC PITC UTPF-PA UTPF-PB PRNT PRNE); CBOE 1; cboe 1; TXSE 1; OTC 1; null 1; NYSE American 1 = 34 rows
        self.assertEqual({"Nasdaq": 7, "NYSE": 21, "CBOE": 1, "cboe": 1, "TXSE": 1, "OTC": 1, "null": 1, "NYSE American": 1}, u["rows_by_exchange"])


# ---- the recipes (plan 3.2) ----------------------------------------------------------------------------------------------------------------
class RecipeTests(unittest.TestCase):
    def d(self, **f):
        return ce.compute_debt(f)

    def test_each_recipe_with_a_hand_sum(self):
        # R1: the combined amount alone, even when R2's tag is present too (R1 first)
        r = self.d(DebtLongtermAndShorttermCombinedAmount=usd(129_541_000_000), LongTermDebt=usd(100_000_000_000))
        self.assertEqual(("R1", usd(129_541_000_000)), (r["recipe"], r["debt_c"]))
        # R2 AAPL-like: 82,300,000,000 + 1,997,000,000 = 84,297,000,000
        r = self.d(LongTermDebt=usd(82_300_000_000), CommercialPaper=usd(1_997_000_000))
        self.assertEqual(("R2", usd(84_297_000_000)), (r["recipe"], r["debt_c"]))
        # R3: 420,000,000,000 + (no DebtCurrent) LongTermDebtCurrent 0 + STB 50,000,000,000 = 470,000,000,000
        r = self.d(LongTermDebtNoncurrent=usd(420_000_000_000), ShortTermBorrowings=usd(50_000_000_000))
        self.assertEqual(("R3", usd(470_000_000_000)), (r["recipe"], r["debt_c"]))
        #     with DebtCurrent 30,000,000,000 the current part is DebtCurrent alone: 420 + 30 = 450 (STB not added again)
        r = self.d(LongTermDebtNoncurrent=usd(420_000_000_000), DebtCurrent=usd(30_000_000_000), ShortTermBorrowings=usd(50_000_000_000))
        self.assertEqual(("R3", usd(450_000_000_000)), (r["recipe"], r["debt_c"]))
        # R4a: 10,000,000,000 + STB 1,000,000,000 = 11,000,000,000
        r = self.d(LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities=usd(10_000_000_000), ShortTermBorrowings=usd(1_000_000_000))
        self.assertEqual(("R4a", usd(11_000_000_000)), (r["recipe"], r["debt_c"]))
        # R4b VZ: 143,448,000,000 + LongTermDebtCurrent 21,783,000,000 = 165,231,000,000 (the first draft's 143,448,000,000 dropped the current part)
        r = self.d(LongTermDebtAndCapitalLeaseObligations=usd(143_448_000_000), LongTermDebtCurrent=usd(21_783_000_000))
        self.assertEqual(("R4b", usd(165_231_000_000)), (r["recipe"], r["debt_c"]))
        #     its own current tag first: 143,448,000,000 + 5,000,000,000 = 148,448,000,000 (LongTermDebtCurrent then not used)
        r = self.d(LongTermDebtAndCapitalLeaseObligations=usd(143_448_000_000), LongTermDebtAndCapitalLeaseObligationsCurrent=usd(5_000_000_000),
                   LongTermDebtCurrent=usd(21_783_000_000))
        self.assertEqual(usd(148_448_000_000), r["debt_c"])
        # R5a: 1,000,000,000 + 30,000,000,000 = 31,000,000,000
        r = self.d(SecuredDebt=usd(1_000_000_000), UnsecuredDebt=usd(30_000_000_000))
        self.assertEqual(("R5a", usd(31_000_000_000)), (r["recipe"], r["debt_c"]))
        # R5b: NotesPayable 900,000,000 with a smaller convertible 300,000,000 counts once, + lines of credit 50,000,000 = 950,000,000
        r = self.d(NotesPayable=usd(900_000_000), ConvertibleNotesPayable=usd(300_000_000), LinesOfCreditCurrent=usd(50_000_000))
        self.assertEqual(("R5b", usd(950_000_000)), (r["recipe"], r["debt_c"]))
        self.assertEqual([("ConvertibleNotesPayable", usd(300_000_000), "NotesPayable")], [(x["tag"], x["value_c"], x["whole"]) for x in r["left_out"]])
        #     both note tags summed when NotesPayable is absent: 400,000,000 + 300,000,000 = 700,000,000
        r = self.d(SeniorNotes=usd(400_000_000), ConvertibleNotesPayable=usd(300_000_000))
        self.assertEqual(("R5b", usd(700_000_000)), (r["recipe"], r["debt_c"]))
        # R6 ORCL 2026-08-31: 117,712,000,000 + 7,625,000,000 = 125,337,000,000
        r = self.d(LongTermNotesAndLoans=usd(117_712_000_000), NotesPayableCurrent=usd(7_625_000_000))
        self.assertEqual(("R6", usd(125_337_000_000)), (r["recipe"], r["debt_c"]))
        #     alternative names of one total count once: LongTermNotesAndLoans = LongTermNotesPayable = 122,342,000,000 -> 122,342,000,000
        r = self.d(LongTermNotesAndLoans=usd(122_342_000_000), LongTermNotesPayable=usd(122_342_000_000))
        self.assertEqual(("R6", usd(122_342_000_000)), (r["recipe"], r["debt_c"]))
        # R6 KBH: NotesAndLoansPayable 2,109,145,000 alone
        r = self.d(NotesAndLoansPayable=usd(2_109_145_000))
        self.assertEqual(("R6", usd(2_109_145_000)), (r["recipe"], r["debt_c"]))
        #     the larger of the totals and NC + CUR: NotesAndLoansPayable 1,000 < 800 + 300 = 1,100 -> 1,100 (whole dollars)
        r = self.d(NotesAndLoansPayable=usd(1_000), LongTermLoansPayable=usd(800), NotesPayableCurrent=usd(300))
        self.assertEqual(usd(1_100), r["debt_c"])

    def test_parts_rule(self):
        # PEP: CP 6,100,000,000 <= STB 10,602,000,000 -> the whole alone; with LongTermDebt 37,000,000,000: 47,602,000,000
        r = self.d(LongTermDebt=usd(37_000_000_000), ShortTermBorrowings=usd(10_602_000_000), CommercialPaper=usd(6_100_000_000))
        self.assertEqual(usd(47_602_000_000), r["debt_c"])
        self.assertEqual({"LongTermDebt": usd(37_000_000_000), "ShortTermBorrowings": usd(10_602_000_000)}, r["parts"])
        self.assertEqual([("CommercialPaper", usd(6_100_000_000), "ShortTermBorrowings", usd(10_602_000_000))],
                         [(x["tag"], x["value_c"], x["whole"], x["whole_c"]) for x in r["left_out"]])
        # ICE: both 1,218,000,000 -> 1,218,000,000 once; + 18,000,000,000 = 19,218,000,000
        r = self.d(LongTermDebt=usd(18_000_000_000), ShortTermBorrowings=usd(1_218_000_000), CommercialPaper=usd(1_218_000_000))
        self.assertEqual(usd(19_218_000_000), r["debt_c"])
        # a CP larger than its STB cannot be inside it: 500,000,000 + 800,000,000 = 1,300,000,000; + 2,000,000,000 = 3,300,000,000
        r = self.d(LongTermDebt=usd(2_000_000_000), ShortTermBorrowings=usd(500_000_000), CommercialPaper=usd(800_000_000))
        self.assertEqual(usd(3_300_000_000), r["debt_c"])
        self.assertEqual([], r["left_out"])

    def test_no_debt_and_order(self):
        self.assertEqual((None, 0), (self.d()["recipe"], self.d()["debt_c"]))
        # a zero line of credit alone gives no debt (R5b sums 0, R6 has no tag)
        r = self.d(LinesOfCreditCurrent=0)
        self.assertEqual((None, 0), (r["recipe"], r["debt_c"]))
        # R5a summing 0 is skipped: R6 then gives 2,000 (whole dollars)
        r = self.d(SecuredDebt=0, UnsecuredDebt=0, LoansPayable=usd(2_000))
        self.assertEqual(("R6", usd(2_000)), (r["recipe"], r["debt_c"]))
        # an R2 first tag of 0 does not apply: R6 from NotesAndLoansPayable 5,000
        r = self.d(LongTermDebt=0, NotesAndLoansPayable=usd(5_000))
        self.assertEqual(("R6", usd(5_000)), (r["recipe"], r["debt_c"]))

    def test_flags_from_tags(self):
        self.assertEqual(ce.FINANCIAL, ce.tags_flag({"Deposits": usd(1)})[0])
        # an insurer with only the claims tag is financial (the critic's CDS-C-12: TRV, ALL, PGR ... were missed by the first tag alone)
        self.assertEqual((ce.FINANCIAL, "LiabilityForClaimsAndClaimsAdjustmentExpense 60,000,000,000"),
                         ce.tags_flag({"LiabilityForClaimsAndClaimsAdjustmentExpense": usd(60_000_000_000)}))
        # REIT: investment property 80,000,000,000 of assets 95,000,000,000 = 84 % >= 50 %
        self.assertEqual((ce.REIT, "investment property 84 % of assets"), ce.tags_flag({"RealEstateInvestmentPropertyNet": usd(80_000_000_000),
                                                                                         "Assets": usd(95_000_000_000)}))
        self.assertEqual("", ce.tags_flag({"RealEstateInvestmentPropertyNet": usd(40), "Assets": usd(100)})[0])

    def test_split_adjusted_shares(self):
        # a 10:1 split after the share count's date multiplies by 10: 1,000,000 -> 10,000,000
        n, words, applied = ce.split_adjusted_shares(1_000_000, "2026-06-30", [("2026-08-01", 10, 1)])
        self.assertEqual(10_000_000, n)
        self.assertEqual("1,000,000 shares at 2026-06-30 x 10/1 for the split of 2026-08-01 = 10,000,000", words)
        # a split on or before the share date is already in the count; one after ``through`` is not yet effective
        self.assertEqual(1_000_000, ce.split_adjusted_shares(1_000_000, "2026-06-30", [("2026-06-30", 10, 1), ("2026-09-01", 2, 1)], date(2026, 8, 31))[0])
        # a 1-for-8 reverse split: 8,000,000 -> 1,000,000; dict rows (the cds_splits shape) work too
        self.assertEqual(1_000_000, ce.split_adjusted_shares(8_000_000, "2026-06-30", [{"split_date": "2026-07-15", "numerator": "1", "denominator": "8"}])[0])

    def test_b2r3_the_largest_of_r5a_r5b_r6_wins(self):
        # B2-R3 (integration): Realty Income-like - SecuredDebt 37,085,000 alone (R5a) beside NotesPayable 25,000,000,000 (R5b):
        # the first-above-0 rule took 37,085,000 (below the USD 100M minimum); the largest is R5b 25,000,000,000
        r = self.d(SecuredDebt=usd(37_085_000), NotesPayable=usd(25_000_000_000))
        self.assertEqual(("R5b", usd(25_000_000_000), {"NotesPayable": usd(25_000_000_000)}), (r["recipe"], r["debt_c"], r["parts"]))
        # Deere-like: R5a SecuredDebt 6,095,000,000 beside R6 LongTermNotesPayable 17,115,000,000 (no current tag: CUR 0) -> R6 17,115,000,000
        r = self.d(SecuredDebt=usd(6_095_000_000), LongTermNotesPayable=usd(17_115_000_000))
        self.assertEqual(("R6", usd(17_115_000_000)), (r["recipe"], r["debt_c"]))
        # R5a the largest: 1,000,000,000 + 30,000,000,000 = 31,000,000,000 > LoansPayable 2,000,000,000 -> R5a
        r = self.d(SecuredDebt=usd(1_000_000_000), UnsecuredDebt=usd(30_000_000_000), LoansPayable=usd(2_000_000_000))
        self.assertEqual(("R5a", usd(31_000_000_000)), (r["recipe"], r["debt_c"]))
        # a tie keeps the earlier recipe and only its parts: R5a 500 = R5b 500 (whole dollars) -> R5a {SecuredDebt 500}
        r = self.d(SecuredDebt=usd(500), NotesPayable=usd(500))
        self.assertEqual(("R5a", usd(500), {"SecuredDebt": usd(500)}), (r["recipe"], r["debt_c"], r["parts"]))
        # R1-R4 still apply first when their first tag is above 0: LongTermDebt 10 + (R5b NotesPayable 1,000 ignored) -> R2 10
        r = self.d(LongTermDebt=usd(10), NotesPayable=usd(1_000))
        self.assertEqual(("R2", usd(10)), (r["recipe"], r["debt_c"]))

    def test_b3r3_a_cover_share_count_dated_after_its_own_filing_is_ignored(self):
        # B3-R3 (integration): AAL's 10-Q filed 2026-07-23 carries a cover share count dated 2027-07-17 (an issuer typo) beside the real
        # 2026-07-17 one: the future-dated fact is ignored, so the share date (split rule, 465-day age rule) is 2026-07-17
        F = ce.Fact
        acc = "0000006201-26-000020"
        facts = {"LongTermDebt": [F("2026-06-30", usd(29_000_000_000), acc, "2026-07-23", "10-Q", "companyfacts")],
                 ce.DEI_SHARES: [F("2027-07-17", 660_000_000, acc, "2026-07-23", "10-Q", "companyfacts"),
                                 F("2026-07-17", 656_000_000, acc, "2026-07-23", "10-Q", "companyfacts")]}
        row = ce.build_fundamentals(6201, facts, "2026-10-29", 1, "test")
        self.assertEqual((656_000_000, "2026-07-17", ce.DEI_SHARES), (row["shares"], row["shares_end"], row["shares_tag"]))
        # a frames row has no filed date: the latest cover date is used as before
        facts[ce.DEI_SHARES] = [F("2026-07-17", 656_000_000, acc, None, None, "CY2026Q3I"), F("2026-04-17", 650_000_000, acc, None, None, "CY2026Q2I")]
        row = ce.build_fundamentals(6201, facts, "2026-10-29", 1, "test")
        self.assertEqual((656_000_000, "2026-07-17"), (row["shares"], row["shares_end"]))


# ---- the full refresh from the fixtures (B2 tasks 1-3, 5) --------------------------------------------------------------------------------
class RefreshTests(Base):
    def test_full_universe_build(self):
        res = self.full_run()
        g = res["getter"]
        # the tickers file + 40 frames (37 USD tags + 3 share tags) x 5 quarters = 201 requests, every one with the GOV user agent
        self.assertEqual(201, len(g.calls))
        self.assertEqual(201, res["requests"])
        self.assertEqual({config.GOV_UA}, {ua for _u, ua, _t in g.calls})
        self.assertEqual("CY2026Q4I,CY2026Q3I,CY2026Q2I,CY2026Q1I,CY2025Q4I", res["quarters"])
        self.assertEqual({"NYSE American": 1}, res["unknown_exchanges"])
        # the unknown exchange string is logged once for the refresh (and kept in its run record)
        logged = [m for lvl, m in self.rt.log.records if lvl == "WARNING" and "NYSE American" in m]
        self.assertEqual(1, len(logged), self.rt.log.records)
        with self.rt.db.reader() as conn:
            self.assertEqual({"NYSE American": 1}, json.loads(conn.execute("SELECT unknown_exchanges_json FROM credit_refresh_runs").fetchone()[0]))
        # ORCL: the 2026-08-31 10-Q (R6) wins over the 2026-05-31 10-K's R1 129,541,000,000 (an older end)
        f = self.fund(1341439)
        self.assertEqual(("2026-08-31", "R6", usd(125_337_000_000)), (f["end_date"], f["debt_recipe"], f["debt_c"]))
        self.assertEqual({"LongTermNotesAndLoans": usd(117_712_000_000), "NotesPayableCurrent": usd(7_625_000_000)}, f["debt_parts"])
        self.assertEqual((3_023_736_000, "2026-09-08", ce.DEI_SHARES), (f["shares"], f["shares_end"], f["shares_tag"]))
        self.assertEqual("LongTermNotesAndLoans 117,712,000,000 + NotesPayableCurrent 7,625,000,000 (balance sheet 2026-08-31; SEC XBRL frames CY2026Q3I)",
                         f["debt_text"])
        self.assertEqual(("OFFERED", None, "ORCL"), (self.ent(1341439)["status"], self.ent(1341439)["status_reason"], self.ent(1341439)["instrument_id"]))
        # VZ R4b 165,231,000,000; KBH R6 2,109,145,000; AAPL R2 84,297,000,000 at 2026-06-27 (not its 2026-03-28 quarter)
        self.assertEqual(("R4b", usd(165_231_000_000)), (self.fund(732712)["debt_recipe"], self.fund(732712)["debt_c"]))
        self.assertEqual(("R6", usd(2_109_145_000)), (self.fund(795266)["debt_recipe"], self.fund(795266)["debt_c"]))
        self.assertEqual(("2026-06-27", "R2", usd(84_297_000_000)), (self.fund(320193)["end_date"], self.fund(320193)["debt_recipe"], self.fund(320193)["debt_c"]))
        # the parts rule on stored rows, with the part left out named
        pep = self.fund(77476)
        self.assertEqual(usd(47_602_000_000), pep["debt_c"])
        self.assertEqual("CommercialPaper 6,100,000,000 taken as part of ShortTermBorrowings 10,602,000,000", pep["debt_note"])
        self.assertEqual((usd(19_218_000_000), usd(3_300_000_000), usd(900_000_000)),
                         (self.fund(1571949)["debt_c"], self.fund(900012)["debt_c"], self.fund(900011)["debt_c"]))
        # JPM: deposits 2,400,000,000,000 are never debt (R3 470,000,000,000); financial from its filing tags until the SIC is checked
        jpm = self.ent("JPM")
        self.assertEqual((19617, "JPM", usd(470_000_000_000)), (jpm["cik"], jpm["ticker"], jpm["fundamentals"]["debt_c"]))
        self.assertEqual((ce.FINANCIAL, "tags", "OFFERED"), (jpm["flag"], jpm["flag_source"], jpm["status"]))
        self.assertIn("SIC not checked yet", jpm["flag_note"])
        self.assertTrue(jpm["limited"])
        self.assertEqual((ce.FINANCIAL, "tags"), (self.ent("TRV")["flag"], self.ent("TRV")["flag_source"]))
        self.assertEqual((ce.REIT, "tags"), (self.ent("PLD")["flag"], self.ent("PLD")["flag_source"]))
        # the refusals, each with its reason in words
        self.assertEqual("NO_DEBT", self.ent("F")["status_reason"])
        self.assertEqual("FOREIGN_FILER", self.ent("TSM")["status_reason"])                  # an ADR in the master
        small = self.ent("SMLC")
        self.assertEqual(("BELOW_MIN_DEBT", "not offered: reported debt USD 30,000,000 is below the USD 100,000,000 minimum (assumed)"),
                         (small["status_reason"], small["status_words"]))
        self.assertEqual("DEBT_GT_LIABILITIES", self.ent("BGLB")["status_reason"])
        self.assertEqual(("NO_SHARES", "CBOE"), (self.ent("NOSH")["status_reason"], self.ent("NOSH")["exchange"]))
        # a balance sheet 6-15 months old is offered with an amber age note: 2025-10-31 -> 2026-10-29 = 363 days = 12 months
        old = self.ent("OLDB")
        self.assertEqual(("OFFERED", True, "balance sheet 2025-10-31 is 12 months old"), (old["status"], old["fundamentals"]["amber"], old["fundamentals"]["age_note"]))
        self.assertEqual("offered (balance sheet 2025-10-31 is 12 months old)", old["status_words"])
        # the share chain: the balance-sheet count when the cover has none; the diluted weighted average last
        self.assertEqual((300_000_000, ce.CS_SHARES), (self.fund(900013)["shares"], self.fund(900013)["shares_tag"]))
        self.assertEqual((90_000_000, ce.WA_SHARES, "diluted weighted average"), (self.fund(900014)["shares"], self.fund(900014)["shares_tag"],
                                                                                 self.fund(900014)["shares_label"]))
        self.assertEqual(("OFFERED", "TXSE"), (self.ent("TXLC")["status"], self.ent("TXLC")["exchange"]))
        for t in ("AMXS", "PINK", "NULX"):
            self.assertIsNone(self.ent(t))
        # the universe line: 27 listed CIKs (34 file rows less 3 dropped = 31; JPM's 3 rows, WFCO's 2 and UTPF's 2 are one CIK each:
        # 31 - 2 - 1 - 1 = 27)
        with self.rt.db.reader() as conn:
            st = ce.universe_status(conn, self.rt.clock.today())
        self.assertEqual(27, st["listed"])
        offered = {"AAPL", "ORCL", "VZ", "KBH", "PEP", "ICE", "JPM", "TRV", "PLD", "OLDB", "TXLC", "WFCO", "BNKC", "CVCO", "CPBG", "MLTC", "WAOC", "PITC",
                   "AMND", "PRNT"}
        self.assertEqual(len(offered), st["offered"])
        self.assertEqual({"OFFERED": 20, "NO_DEBT": 2, "FOREIGN_FILER": 1, "BELOW_MIN_DEBT": 1, "DEBT_GT_LIABILITIES": 1, "NO_SHARES": 1, "NO_LISTED_COMMON": 1},
                         st["by_status"])
        self.assertEqual({ce.FINANCIAL: {"tags": 3}, ce.REIT: {"tags": 1}}, st["by_flag"])          # JPM, TRV, BNKC; PLD
        self.assertEqual(("ready", 201), (st["state"], st["last_run"]["requests"]))

    def test_a_registrant_with_only_preferred_series_listed_is_not_offered(self):
        # UTPF: LongTermDebt 5,000,000,000 and 100,000,000 cover shares would qualify, but the SEC file lists only its preferred series:
        # the model has no common share price -> NOT_OFFERED with the reason in words; the fundamentals stay stored (shown in the row)
        self.full_run()
        e = self.ent("UTPF-PA")
        self.assertEqual((900017, "UTPF-PA", ["UTPF-PB"], "NOT_OFFERED", "NO_LISTED_COMMON"), (e["cik"], e["ticker"], e["tickers"], e["status"], e["status_reason"]))
        self.assertEqual("not offered: no common stock listed - the SEC's exchange file lists only UTPF-PA, UTPF-PB (preferred shares, warrants, units or"
                         " rights); the model needs the share price of listed common stock", e["status_words"])
        self.assertEqual(usd(5_000_000_000), e["fundamentals"]["debt_c"])
        self.assertEqual(900017, self.ent("UTPF-PB")["cik"])                     # any of its symbols finds it
        with self.rt.db.reader() as conn:
            row = ce.search(conn, q="utility prefco", today=self.rt.clock.today())["rows"][0]
        self.assertEqual(("UTPF-PA", False, "NO_LISTED_COMMON"), (row["ticker"], row["offered"], row["status_reason"]))
        # the stored status column agrees, and a master STOCK with that id would make it the reference equity (derive's ``stock``)
        with self.rt.db.reader() as conn:
            stored = dict(conn.execute("SELECT * FROM credit_entities WHERE cik=900017").fetchone())
            fund = ce.fundamentals_now(conn, 900017, self.rt.clock.today())
        self.assertEqual(("NOT_OFFERED", "NO_LISTED_COMMON"), (stored["status"], stored["status_reason"]))
        self.assertEqual("OFFERED", ce.derive(stored, fund, self.rt.clock.today(), stock=True)["status"])
        # the order of the reasons: no longer listed comes first
        self.assertEqual("NOT_LISTED", ce.derive(dict(stored, exchange=None), fund, self.rt.clock.today())["status_reason"])

    def test_a_joint_filing_s_figures_stay_with_the_parent(self):
        # the SEC frames API lists PARENTCO's 2026-06-30 joint 10-Q (accession 0000900018-26-000033, LongTermDebt 29,500,000,000, entityName
        # PARENTCO ENERGY CORPORATION) under its listed subsidiary PRNE's CIK: those are the parent's consolidated figures, never PRNE's
        res = self.full_run()
        self.assertEqual({900019}, set(res["others"]))
        o = res["others"][900019]
        self.assertEqual((900018, 2), (o["by"], o["facts"]))                       # LongTermDebt and the cover share count
        self.assertIn("PARENTCO ENERGY CORPORATION", o["example"])
        prne = self.ent("PRNE")
        self.assertEqual(("NO_DEBT", None), (prne["status_reason"], prne["fundamentals"]))
        # the parent keeps its OWN latest balance sheet (2026-03-31, 28,000,000,000; 212 days old -> 7 months, amber) - plan 12.5
        prnt = self.ent("PRNT")
        f = prnt["fundamentals"]
        self.assertEqual(("OFFERED", "2026-03-31", usd(28_000_000_000), 300_000_000), (prnt["status"], f["end_date"], f["debt_c"], f["shares"]))
        self.assertEqual("offered (balance sheet 2026-03-31 is 7 months old)", prnt["status_words"])
        with self.rt.db.reader() as conn:
            note = conn.execute("SELECT note FROM credit_refresh_runs").fetchone()[0]
        self.assertIn("2 facts filed under 1 other CIK(s) were another listed registrant's joint-filing figures and were left out", note)

    def test_the_joint_filing_rule(self):
        names = {65984: "ENTERGY CORP /DE/", 7323: "ENTERGY ARKANSAS, LLC", 1610520: "UBS Group AG", 1114446: "UBS AG"}
        # the live shape of 2026-10-29: Entergy Corp's 10-Q facts under Entergy Arkansas (CIK 7323), named ENTERGY CORPORATION
        self.assertEqual(65984, ce.other_registrant(7323, "0000065984-26-000283", "ENTERGY CORPORATION", names))
        # UBS AG's own facts in a filing submitted by UBS Group: its own name -> its own fact
        self.assertIsNone(ce.other_registrant(1114446, "0001610520-26-000001", "UBS AG", names))
        # a filing agent's accession (not a listed registrant), the CIK's own accession, a submitter not in the list: own facts
        self.assertIsNone(ce.other_registrant(7323, "0001193125-26-000283", "ENTERGY CORPORATION", names))
        self.assertIsNone(ce.other_registrant(65984, "0000065984-26-000283", "ENTERGY CORPORATION", names))
        self.assertIsNone(ce.other_registrant(7323, "0000099999-26-000001", "ENTERGY CORPORATION", names))
        # names compared without case, punctuation, a state tag or the legal form; GROUP / HOLDINGS are kept
        self.assertEqual(("ENTERGY",), ce.name_key("ENTERGY CORP /DE/"))
        self.assertEqual(ce.name_key("SOUTHERN CO"), ce.name_key("The Southern Company"))
        self.assertEqual(ce.name_key("KOHLS Corp"), ce.name_key("KOHL\u2019S CORP"))
        self.assertNotEqual(ce.name_key("UBS Group AG"), ce.name_key("UBS AG"))
        self.assertEqual((65984, None), (ce.submitter_cik("0000065984-26-000283"), ce.submitter_cik("65984")))

    def test_a_second_identical_run_writes_only_its_record(self):
        self.full_run()
        with self.rt.db.reader() as conn:
            n_fund = conn.execute("SELECT COUNT(*) FROM commands WHERE kind='credit.fundamentals_upsert'").fetchone()[0]
            n_ent = conn.execute("SELECT COUNT(*) FROM commands WHERE kind='credit.entities_upsert'").fetchone()[0]
        res = self.full_run(at="2026-10-29T09:00:00-04:00")
        self.assertEqual((0, 0), (res["fundamentals_changed"], res["entities_changed"]))
        with self.rt.db.reader() as conn:
            self.assertEqual(n_fund, conn.execute("SELECT COUNT(*) FROM commands WHERE kind='credit.fundamentals_upsert'").fetchone()[0])
            # one more entities command: the run record alone (no rows)
            self.assertEqual(n_ent + 1, conn.execute("SELECT COUNT(*) FROM commands WHERE kind='credit.entities_upsert'").fetchone()[0])
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM credit_refresh_runs").fetchone()[0])

    def test_point_in_time_and_the_amendment(self):
        # 10-20: PITC's 2026-06-30 balance sheet (600,000,000); AMND's 10-Q (1,000,000,000)
        self.full_run(as_of="2026-10-20", at="2026-10-20T08:00:00-04:00")
        # 10-29: PITC's 2026-09-30 10-Q (filed 10-27, 650,000,000) and AMND's 10-Q/A of the same balance sheet (filed 10-25, 1,100,000,000)
        self.full_run(as_of="2026-10-29", at="2026-10-29T08:00:00-04:00")
        with self.rt.db.reader() as conn:
            on25 = ce.fundamentals_on(conn, 900015, date(2026, 10, 25))
            on29 = ce.fundamentals_on(conn, 900015, date(2026, 10, 29))
            now = ce.fundamentals_now(conn, 900015)
            a25 = ce.fundamentals_on(conn, 900016, date(2026, 10, 25))
            a_now = ce.fundamentals_now(conn, 900016)
            rows = conn.execute("SELECT end_date, accn, known_from, debt_c FROM credit_fundamentals WHERE cik=900016 ORDER BY known_from").fetchall()
        # a VM of 10-25 uses what the app knew on 10-25: the 06-30 row; from 10-29 the 09-30 row
        self.assertEqual(("2026-06-30", "2026-10-20", usd(600_000_000)), (on25["end_date"], on25["known_from"], on25["debt_c"]))
        self.assertEqual(("2026-09-30", "2026-10-29", usd(650_000_000)), (on29["end_date"], on29["known_from"], on29["debt_c"]))
        self.assertEqual("2026-09-30", now["end_date"])
        # two rows of one end: the amendment (known later) wins from 10-29 on; on 10-25 the original
        self.assertEqual([("2026-06-30", "0000900016-26-000021", "2026-10-20", usd(1_000_000_000)),
                          ("2026-06-30", "0000900016-26-000029", "2026-10-29", usd(1_100_000_000))], [tuple(r) for r in rows])
        self.assertEqual((usd(1_000_000_000), usd(1_100_000_000)), (a25["debt_c"], a_now["debt_c"]))
        # nothing known on 10-19
        with self.rt.db.reader() as conn:
            self.assertIsNone(ce.fundamentals_on(conn, 900015, date(2026, 10, 19)))

    def test_same_end_tie_break(self):
        a = {"end_date": "2026-06-30", "accn": "0000000001-26-000001", "known_from": "2026-10-20", "filed": "2026-08-06"}
        b = {"end_date": "2026-06-30", "accn": "0000000001-26-000002", "known_from": "2026-10-20", "filed": "2026-10-25"}
        self.assertIs(b, ce._choose([a, b]))                                   # the later filed wins
        self.assertIs(b, ce._choose([b, a]))
        c = dict(b, filed="2026-08-06", known_from="2026-10-29")
        self.assertIs(c, ce._choose([a, c]))                                   # equal filed: the later known_from
        d = dict(a, accn="0000000001-26-000009")
        self.assertIs(d, ce._choose([a, d]))                                   # equal filed and known_from: the higher accn
        e = dict(a, filed=None, known_from="2026-10-29")
        self.assertIs(e, ce._choose([a, e]))                                   # one filed unknown: the later known_from
        newer = dict(a, end_date="2026-09-30", known_from="2026-10-01", filed=None)
        self.assertIs(newer, ce._choose([b, newer]))                           # a later end always wins

    def test_stale_rules_and_the_minimum(self):
        self.full_run()
        with self.rt.db.reader() as conn:
            # 2027-02-15: OLDB's 2025-10-31 balance sheet is 472 days old (> 465): 472 / 30.4375 = 15.5 -> 16 months
            e = ce.entity(conn, "OLDB", date(2027, 2, 15))
        self.assertEqual(("STALE_FUNDAMENTALS", "not offered: balance sheet 2025-10-31 is 16 months old"), (e["status_reason"], e["status_words"]))
        # 465 days exactly is still offered (2025-10-31 + 465 days = 2027-02-08); 466 is not
        with self.rt.db.reader() as conn:
            self.assertEqual("OFFERED", ce.entity(conn, "OLDB", date(2027, 2, 8))["status"])
            self.assertEqual("STALE_FUNDAMENTALS", ce.entity(conn, "OLDB", date(2027, 2, 9))["status_reason"])
        # a share count older than 465 days refuses too
        ent = {"cik": 1, "ticker": "X", "name": "X", "exchange": "NYSE", "check_json": "{}"}
        f = {"debt_c": 500 * M, "liabilities_c": None, "shares": 10, "end_date": "2026-06-30", "shares_end": "2025-01-01"}
        d = ce.derive(ent, f, date(2026, 10, 29))
        self.assertEqual("STALE_FUNDAMENTALS", d["status_reason"])
        self.assertEqual("not offered: the share count of 2025-01-01 is 22 months old", ce.status_words("STALE_FUNDAMENTALS", f, ent, date(2026, 10, 29)))
        # the USD 100,000,000 minimum: exactly 100,000,000.00 is offered, one cent less is not
        f2 = dict(f, shares_end="2026-08-01", debt_c=ce.MIN_DEBT_C)
        self.assertEqual("OFFERED", ce.derive(ent, f2, date(2026, 10, 29))["status"])
        self.assertEqual("BELOW_MIN_DEBT", ce.derive(ent, dict(f2, debt_c=ce.MIN_DEBT_C - 1), date(2026, 10, 29))["status_reason"])

    def test_a_no_longer_listed_name(self):
        self.full_run()
        # the SEC file drops SMLC: the next full run marks it NOT_LISTED (kept in the table, never deleted)
        path = os.path.join(FIX, "company_tickers_exchange.json")
        with open(path, "rb") as fh:
            doc = json.loads(fh.read())
        doc["data"] = [r for r in doc["data"] if r[2] != "SMLC"]
        g = FixtureSEC("2026-10-30", self.ft)
        orig = g.__call__

        def getter(url, ua, timeout, accept):
            if url == ce.UNIVERSE_URL:
                g.calls.append((url, ua, None))
                return json.dumps(doc).encode("utf-8")
            return orig(url, ua, timeout, accept)
        self.rt.clock.set("2026-10-30T08:00:00-04:00")
        ce.run_refresh(self.rt, int(self.rt.clock.epoch()), "full", getter=getter)
        e = self.ent("SMLC")
        self.assertEqual(("NOT_LISTED", None), (e["status_reason"], e["exchange"]))
        self.assertIn("no longer listed", e["status_words"])


# ---- the refresher's schedule and the offline rule (B2 task 5) -----------------------------------------------------------------------------
class RefresherTests(Base):
    def run_at(self, at: str, as_of: str):
        self.rt.clock.set(at)
        g = FixtureSEC(as_of, self.ft)
        out = ce.refresh(self.rt, int(self.rt.clock.epoch()), getter=g)
        return out, len(g.calls)

    def test_modes_first_run_daily_and_monday(self):
        self.online()
        out, n = self.run_at("2026-10-29T08:00:00-04:00", "2026-10-29")           # Thursday, no universe yet: full
        self.assertTrue(out.startswith("full:"), out)
        self.assertEqual(201, n)
        out, n = self.run_at("2026-10-29T15:00:00-04:00", "2026-10-29")           # a restart the same day: nothing fetched
        self.assertEqual(("already refreshed today", 0), (out, n))
        out, n = self.run_at("2026-10-30T06:31:00-04:00", "2026-10-30")           # Friday: the daily run, 2 quarters x 40 frames = 80
        self.assertTrue(out.startswith("daily:"), out)
        self.assertEqual(80, n)
        out, n = self.run_at("2026-11-02T06:31:00-05:00", "2026-11-02")           # Monday: the weekly full run
        self.assertTrue(out.startswith("full:"), out)
        self.assertEqual(201, n)
        out, n = self.run_at("2026-11-02T12:00:00-05:00", "2026-11-02")
        self.assertEqual(("already refreshed today", 0), (out, n))
        # provider health has the SEC row: ok, every request counted
        row = self.rt.providers.health.get("sec")
        self.assertTrue(row["ok"])

    def test_offline_makes_no_call(self):
        def boom(*a, **k):
            raise AssertionError("offline must not call SEC")
        # no provider set (a test runtime) and the FakeProvider set (--offline): no request, the desk says why
        self.assertEqual("offline: SEC EDGAR is not contacted", ce.refresh(self.rt, int(self.rt.clock.epoch()), getter=boom))
        self.rt.providers = types.SimpleNamespace(offline=True, health=None)
        self.assertEqual("offline: SEC EDGAR is not contacted", ce.refresh(self.rt, int(self.rt.clock.epoch()), getter=boom))
        r = ce.refresh_entity(self.rt, 1341439)
        self.assertEqual(("OFFLINE", 0), (r["code"], r["requests"]))
        with self.rt.db.reader() as conn:
            st = ce.universe_status(conn)
        self.assertEqual("offline", st["state"])
        self.assertIn("offline", st["message"])

    def test_sec_user_agent_and_four_requests_a_second(self):
        self.online()
        g = FixtureSEC("2026-10-29", self.ft)
        ce.run_refresh(self.rt, int(self.rt.clock.epoch()), "full", getter=g)
        self.assertEqual({config.GOV_UA}, {ua for _u, ua, _t in g.calls})
        self.assertEqual("LoganTerminal/1.0 (loganshaffer87@gmail.com)", config.GOV_UA)
        # the injected clock at each request: one every 0.25 s at most -> never more than 4 inside any one second
        times = [t for _u, _ua, t in g.calls]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 0.25 - 1e-9)
        for i, t in enumerate(times):
            self.assertLessEqual(sum(1 for x in times[i:] if x < t + 1.0), 4)
        # 201 requests at 4 a second take at least 50 s of pacing
        self.assertGreaterEqual(times[-1] - times[0], 50.0)
        self.assertEqual(201, self.rt.providers.health.get("sec")["requests_today"])

    def test_the_pacer_spaces_actual_starts(self):
        # the OS timer may wake a sleeper late: every second wait here wakes 0.02 s late.  Each start must still be at least 0.25 s after the
        # previous ACTUAL start (the 14:49 ET live check measured gaps of 0.232 s and 5 starts inside one second 23 times before this rule)
        ft = FakeTime()
        late = [False]

        def sleep(d):
            late[0] = not late[0]
            ft.sleep(d + (0.02 if late[0] else 0.0))
        p = ce.Pacer(ce.SEC_RPS, sleep=sleep, now=ft.now)
        starts = []
        for _ in range(40):
            p.wait()
            starts.append(ft.now())
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertGreaterEqual(min(gaps), 0.25 - 1e-9)
        for i, t in enumerate(starts):
            self.assertLessEqual(sum(1 for x in starts[i:] if x < t + 1.0), 4)
        # the default clock is the high-resolution one (time.monotonic ticks in 15.6 ms steps on Windows)
        import time as _t
        self.assertIs(_t.perf_counter, ce.Pacer()._now)

    def test_health_pauses_sec_after_errors(self):
        hp = self.online().health

        def failing(url, ua, timeout, accept):
            raise FetchError("HTTP 503", status=503)
        for _ in range(5):
            with self.assertRaises(FetchError):
                ce.sec_get(self.rt, ce.UNIVERSE_URL, failing)
        # five failures open the breaker: the next call is refused before any request
        calls = []
        with self.assertRaises(FetchError) as cm:
            ce.sec_get(self.rt, ce.UNIVERSE_URL, lambda *a: calls.append(a) or b"{}")
        self.assertEqual([], calls)
        self.assertIn("paused", str(cm.exception))
        self.assertFalse(hp.get("sec")["ok"])

    def test_bind_creates_the_tables_and_registers_the_refresher(self):
        from finsim.terminal.quotes import worker as _worker
        tmp = tempfile.mkdtemp(prefix="fst-credit-bind-")
        rt = make_runtime(tmp, at=self.AT)
        try:
            ce.bind(rt)
            with rt.db.reader() as conn:
                names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertTrue({"credit_entities", "credit_fundamentals", "credit_refresh_runs"} <= names)
            self.assertIn("cds_fundamentals", _worker.registered_refreshers())
        finally:
            _worker.unregister_refresher("cds_fundamentals")
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)


# ---- one name on demand (B2 task 4) --------------------------------------------------------------------------------------------------------
class RefreshEntityTests(Base):
    def setUp(self):
        super().setUp()
        self.full_run()

    def check(self, cik: int, too_large=()):
        g = FixtureSEC("2026-10-29", self.ft, too_large=too_large)
        return ce.refresh_entity(self.rt, cik, getter=g), g

    def test_sic_check_replaces_the_tags_flag(self):
        r, g = self.check(86312)                                         # TRV: SIC 6331 Fire, Marine & Casualty Insurance
        self.assertEqual((ce.FINANCIAL, "sic", 1), (r["flag"], r["flag_source"], len(g.calls)))
        e = self.ent("TRV")
        self.assertEqual(("6331", "10-Q", "2026-07-22"), (e["sic"], e["latest_form"], e["latest_filed"]))
        self.assertEqual("financial (SIC 6331 Fire, Marine & Casualty Insurance)", e["flag_note"])
        r, _g = self.check(1045609)                                      # PLD: SIC 6798
        self.assertEqual((ce.REIT, "sic"), (r["flag"], r["flag_source"]))
        r, g = self.check(1341439)                                       # ORCL: SIC 7372, no flag; frames already have the 08-31 10-Q
        self.assertEqual(("", "sic", "OFFERED", 1), (r["flag"], r["flag_source"], r["status"], len(g.calls)))
        self.assertIsNotNone(self.ent("ORCL")["checked_ts"])

    def test_a_20f_filer_is_not_offered(self):
        r, g = self.check(1046179)                                       # TSM: 20-F -> foreign private issuer; no companyfacts request
        self.assertEqual(("FOREIGN_FILER", 1), (r["status_reason"], len(g.calls)))
        self.assertEqual("not offered: foreign private issuer (ADR ratios and IFRS reports are not modelled in this release)", r["status_words"])
        with open(os.path.join(FIX, "companyfacts", "CIK0001046179.json"), "rb") as fh:
            facts, ifrs_only = ce.facts_from_companyfacts(json.loads(fh.read()))
        self.assertTrue(ifrs_only)                                       # IFRS-only facts make a name FOREIGN_FILER too

    def test_companyfacts_brings_the_newer_10q(self):
        # JPM: its 10-Q for 2026-09-30 (filed 10-28) is newer than the frames row (06-30): companyfacts is read
        r, g = self.check(19617)
        self.assertEqual(2, len(g.calls))
        self.assertTrue(r["changed"])
        f = self.fund(19617)
        # the 2014 LongTermDebt (276,836,000,000) is an old end and ignored; the 8-K fact is not a periodic form:
        # R3 = LongTermDebtNoncurrent 431,000,000,000 + ShortTermBorrowings 52,000,000,000 = 483,000,000,000
        self.assertEqual(("2026-09-30", "R3", usd(483_000_000_000), "2026-10-28", "10-Q"), (f["end_date"], f["debt_recipe"], f["debt_c"], f["filed"], f["form"]))
        self.assertEqual((2_735_000_000, "2026-10-24"), (f["shares"], f["shares_end"]))
        self.assertTrue(f["source"].startswith("SEC companyfacts"))
        e = self.ent("JPM")
        self.assertEqual((ce.FINANCIAL, "sic", "OFFERED"), (e["flag"], e["flag_source"], e["status"]))
        # the frames row of 2026-06-30 stays beside it (rows are never deleted: a VM of an earlier date reads what was known then)
        with self.rt.db.reader() as conn:
            ends = [r[0] for r in conn.execute("SELECT end_date FROM credit_fundamentals WHERE cik=19617 ORDER BY end_date").fetchall()]
        self.assertEqual(["2026-06-30", "2026-09-30"], ends)

    def test_companyfacts_over_the_cap_keeps_the_frames_row(self):
        r, g = self.check(320193, too_large={320193})                   # AAPL: a newer 10-K exists but its companyfacts file is too large
        self.assertFalse(r["changed"])
        self.assertEqual("the newest 10-K (period 2026-09-26) is in a company-facts file over the 16 MB limit; using balance sheet 2026-06-27", r["note"])
        self.assertEqual(r["note"], self.ent("AAPL")["check_note"])
        self.assertEqual("2026-06-27", self.fund(320193)["end_date"])

    def test_debt_only_in_the_company_s_own_tags_stays_no_debt(self):
        # Ford files current 10-Qs, but its newest balance sheet with a standard debt tag is the 2020 10-K (R3 291,000,000): that row is past
        # the 15-month limit while the company reports, so it is not taken as its balance sheet - "no debt reported", the old date named
        r, g = self.check(37996)
        self.assertEqual(2, len(g.calls))                                # submissions, then companyfacts (no frames row to compare with)
        self.assertEqual(("NO_DEBT", False), (r["status_reason"], r["changed"]))
        self.assertEqual("its newest balance sheet with a standard debt tag is 2020-12-31 (R3, USD 291,000,000); its later reports (the newest"
                         " 10-Q, period 2026-09-30) give its debt in the company's own tags", r["note"])
        self.assertIsNone(self.fund(37996))
        self.assertEqual(r["note"], self.ent("F")["check_note"])

    def test_a_decompressed_companyfacts_over_the_cap_is_not_parsed(self):
        # the network layer caps the compressed bytes; the module also refuses to parse a body larger than the cap (memory on this machine)
        saved = config.MAX_BYTES
        config.MAX_BYTES = 1000                                          # JPM's fixture file is larger than 1,000 bytes
        try:
            r, g = self.check(19617)
        finally:
            config.MAX_BYTES = saved
        self.assertEqual(2, len(g.calls))
        self.assertFalse(r["changed"])
        self.assertEqual("the newest 10-Q (period 2026-09-30) is in a company-facts file over the 16 MB limit; using balance sheet 2026-06-30", r["note"])
        self.assertEqual("2026-06-30", self.fund(19617)["end_date"])

    def test_a_joint_filing_from_companyfacts_stays_with_the_parent(self):
        # the on-demand twin of the frames rule: PRNE's submissions name the joint 10-Q (period 2026-06-30) and its companyfacts carry the
        # parent's consolidated LongTermDebt 29,500,000,000 under the joint accession 0000900018-26-000033, submitted by PARENTCO (PRNT, a
        # listed registrant): not taken as PRNE's balance sheet; the name stays NO_DEBT and says why
        sub = {"cik": "900019", "sic": "4911", "sicDescription": "Electric Services", "name": "PARENTCO ELECTRIC LLC",
               "filings": {"recent": {"accessionNumber": ["0000900018-26-000033"], "filingDate": ["2026-07-30"], "reportDate": ["2026-06-30"],
                                      "form": ["10-Q"], "primaryDocument": ["doc.htm"]}, "files": []}}
        cf = {"cik": 900019, "entityName": "PARENTCO ELECTRIC LLC", "facts": {
            "us-gaap": {"LongTermDebt": {"units": {"USD": [{"end": "2026-06-30", "val": 29500000000, "accn": "0000900018-26-000033", "fy": 2026, "fp": "Q2",
                                                            "form": "10-Q", "filed": "2026-07-30"}]}}},
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [{"end": "2026-07-24", "val": 301000000, "accn": "0000900018-26-000033",
                                                                                 "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-07-30"}]}}}}}
        base = FixtureSEC("2026-10-29", self.ft)
        calls = []

        def getter(url, ua, timeout, accept):
            calls.append(url)
            if url.endswith("submissions/CIK0000900019.json"):
                return json.dumps(sub).encode("utf-8")
            if url.endswith("companyfacts/CIK0000900019.json"):
                return json.dumps(cf).encode("utf-8")
            return base(url, ua, timeout, accept)
        r = ce.refresh_entity(self.rt, 900019, getter=getter)
        self.assertEqual(2, len(calls))                                  # submissions, then companyfacts (no balance sheet to compare with)
        self.assertEqual(("NO_DEBT", False), (r["status_reason"], r["changed"]))
        self.assertEqual("its newest balance sheet in the SEC's XBRL API (2026-06-30, accession 0000900018-26-000033) is a joint filing submitted by"
                         " PARENTCO ENERGY CORP (PRNT): those are PARENTCO ENERGY CORP's consolidated figures, not this company's", r["note"])
        self.assertIsNone(self.fund(900019))
        self.assertEqual(r["note"], self.ent("PRNE")["check_note"])
        # its own filing (the accession of its own CIK) is taken as before
        own = json.loads(json.dumps(cf).replace("0000900018-26-000033", "0000900019-26-000040"))
        sub2 = json.loads(json.dumps(sub).replace("0000900018-26-000033", "0000900019-26-000040"))

        def getter2(url, ua, timeout, accept):
            if url.endswith("submissions/CIK0000900019.json"):
                return json.dumps(sub2).encode("utf-8")
            if url.endswith("companyfacts/CIK0000900019.json"):
                return json.dumps(own).encode("utf-8")
            return base(url, ua, timeout, accept)
        r = ce.refresh_entity(self.rt, 900019, getter=getter2)
        self.assertTrue(r["changed"])
        self.assertEqual(("2026-06-30", usd(29_500_000_000)), (self.fund(900019)["end_date"], self.fund(900019)["debt_c"]))

    def test_unknown_cik(self):
        r, g = self.check(999999)
        self.assertEqual(("ENTITY_UNKNOWN", 0), (r["code"], len(g.calls)))
        self.assertFalse(ce.needs_check({"checked_ts": int(self.rt.clock.epoch()) - 3600}, int(self.rt.clock.epoch())))
        self.assertTrue(ce.needs_check({"checked_ts": int(self.rt.clock.epoch()) - 8 * 86400}, int(self.rt.clock.epoch())))
        self.assertTrue(ce.needs_check({"checked_ts": None}, int(self.rt.clock.epoch())))


# ---- the reads: entity, search, instrument, the route (B2 task 6) ----------------------------------------------------------------------------
class ReadTests(Base):
    def setUp(self):
        super().setUp()
        self.full_run()

    def search(self, **kw):
        with self.rt.db.reader() as conn:
            return ce.search(conn, today=self.rt.clock.today(), **kw)

    def test_entity_lookup(self):
        self.assertEqual(1341439, self.ent(1341439)["cik"])
        self.assertEqual(1341439, self.ent("1341439")["cik"])
        self.assertEqual(1341439, self.ent("orcl")["cik"])
        self.assertEqual(19617, self.ent("AMJB")["cik"])                 # any of the CIK's tickers
        self.assertEqual(19617, self.ent("JPM-PC")["cik"])
        self.assertIsNone(self.ent("NOPE"))
        e = self.ent("KBH")
        self.assertEqual(("KB HOME", "NYSE", "KBH", "OFFERED"), (e["name"], e["exchange"], e["instrument_id"], e["status"]))
        self.assertEqual(usd(2_109_145_000), e["fundamentals"]["debt_c"])

    def test_search_and_filters(self):
        self.assertEqual("ORCL", self.search(q="orc")["rows"][0]["ticker"])
        self.assertEqual("JPM", self.search(q="AMJB")["rows"][0]["ticker"])
        self.assertEqual("KBH", self.search(q="kb home")["rows"][0]["ticker"])
        self.assertEqual("ORCL", self.search(q="1341439")["rows"][0]["ticker"])
        fin = self.search(flt="financial")
        self.assertEqual(["BNKC", "JPM", "TRV"], [r["ticker"] for r in fin["rows"]])
        self.assertEqual({"tags"}, {r["flag_source"] for r in fin["rows"]})
        self.assertIn("known so far", fin["caption"])
        self.assertEqual(["PLD"], [r["ticker"] for r in self.search(flt="reit")["rows"]])
        nonfin = {r["ticker"] for r in self.search(flt="non-financial", limit=100)["rows"]}
        self.assertFalse(nonfin & {"BNKC", "JPM", "TRV", "PLD"})
        self.assertEqual(["ORCL"], [r["ticker"] for r in self.search(flt="mine", held={1341439})["rows"]])
        # sorted by debt, largest first: JPM 470B, VZ 165.2B, ORCL 125.3B
        self.assertEqual(["JPM", "VZ", "ORCL"], [r["ticker"] for r in self.search(sort="debt", desc=True, limit=3)["rows"]])
        # paged: 27 names, 50 a page by default; offset 20 shows the last 7
        p = self.search(offset=20)
        self.assertEqual((27, 7), (p["total"], len(p["rows"])))
        with self.assertRaises(ce.RejectError):
            self.search(flt="banks")

    def test_route_answer(self):
        routes = {}
        ce.register_routes(types.SimpleNamespace(add=lambda m, p, fn, **k: routes.setdefault((m, p), fn)))
        fn = routes[("GET", r"/api/otc/cds/entities")]
        self.rt.cds_model = types.SimpleNamespace(last_models=lambda ciks: {1341439: {"ok": True, "spread_bp": "112.54", "bucket": "BBB", "quality": "computed"}})
        req = _api.Request(self.rt, "GET", "/api/otc/cds/entities", (), {"q": "orcl", "account_id": "T1:PB_GS"}, None, "127.0.0.1", {})
        out = json.loads(json.dumps(_api.encode(fn(req))))
        row = out["rows"][0]
        self.assertEqual(("ORCL", "125337000000.00", "OFFERED", "112.54"), (row["ticker"], row["debt"], row["status"], row["model_5y"]["spread_bp"]))
        self.assertEqual("ready", out["universe"]["state"])
        self.assertIn("refreshed", out["universe"]["message"])
        req = _api.Request(self.rt, "GET", "/api/otc/cds/entities", (), {"filter": "nope"}, None, "127.0.0.1", {})
        with self.assertRaises(_api.ApiError) as cm:
            fn(req)
        self.assertEqual(400, cm.exception.http)

    def test_instrument_for(self):
        rt = self.rt
        self.assertEqual(("ORCL", "ok"), ce.instrument_for(rt, self.ent("ORCL")))
        asked = []
        rt.worker = types.SimpleNamespace(resolve=lambda t: asked.append(t) or {"status": "resolving"})
        self.assertEqual((None, "resolving"), ce.instrument_for(rt, self.ent("TXLC")))
        self.assertEqual(["TXLC"], asked)
        rt.worker = types.SimpleNamespace(resolve=lambda t: {"status": "error", "message": "Yahoo has no such symbol"})
        self.assertEqual((None, "error: Yahoo has no such symbol"), ce.instrument_for(rt, self.ent("TXLC")))
        rt.worker = None
        self.assertEqual((None, "unavailable: no quote worker to look the ticker up"), ce.instrument_for(rt, self.ent("TXLC")))

    def test_light_command_replay_is_a_no_op(self):
        from finsim.terminal.commands import Command
        payload = {"rows": [{"cik": 900005, "name": "TEXAS LISTED CO", "ticker": "TXLC", "exchange": "TXSE", "tickers": []}]}
        rid = ce._rid("credit:ent", payload)
        a = self.rt.bus.execute(Command("credit.entities_upsert", payload, rid, "worker"))
        b = self.rt.bus.execute(Command("credit.entities_upsert", payload, rid, "worker"))
        self.assertTrue(a.ok and b.ok)
        self.assertTrue(b.replayed)
        self.assertEqual((0, 0), (a.body["new"], a.body["updated"]))        # nothing changed: the row is as stored


if __name__ == "__main__":
    unittest.main()
