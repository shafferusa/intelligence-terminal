"""Writes the SEC EDGAR fixtures of ``tests/test_terminal_credit_entities.py`` (CDS_SINGLE_NAME_PLAN package B2): small SYNTHETIC payloads in
the real API shapes - ``company_tickers_exchange.json``, XBRL frames (``frames/<taxonomy>/<tag>/<unit>/<period>.json``), ``submissions`` and
``companyfacts`` - plus ``filed.json`` (accession number -> filing date), which the test getter uses to serve a frame as it stood on a given
day (frames carry no filing date).  The values follow the plan's worked shapes (ORCL R6 at 2026-08-31, VZ R4b, KBH R6, PEP / ICE parts
rule, AAPL-like R2); every other number is invented for a rule.  Nothing here touches the network.

    py -3.12 tests\\fixtures\\terminal\\cds\\edgar\\gen_edgar_fixtures.py      (rewrites the files next to this script)
"""
import json
import os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

# (cik, name, ticker, exchange) in the file's order: the allowlist, the dropped strings, the JPMorgan / AMJB primary-ticker case
TICKERS = [
    (320193, "Apple Inc.", "AAPL", "Nasdaq"),
    (1341439, "ORACLE CORP", "ORCL", "NYSE"),
    (732712, "VERIZON COMMUNICATIONS INC", "VZ", "NYSE"),
    (795266, "KB HOME", "KBH", "NYSE"),
    (77476, "PEPSICO INC", "PEP", "Nasdaq"),
    (1571949, "Intercontinental Exchange, Inc.", "ICE", "NYSE"),
    (19617, "JPMORGAN CHASE & CO", "AMJB", "NYSE"),           # an ETN under JPMorgan's CIK, first in the file
    (19617, "JPMORGAN CHASE & CO", "JPM", "NYSE"),
    (19617, "JPMORGAN CHASE & CO", "JPM-PC", "NYSE"),         # a preferred series
    (37996, "FORD MOTOR CO", "F", "NYSE"),                    # custom debt tags only: NO_DEBT
    (86312, "TRAVELERS COMPANIES, INC.", "TRV", "NYSE"),      # insurer by its claims tag only
    (1045609, "Prologis, Inc.", "PLD", "NYSE"),               # REIT by the asset test
    (1046179, "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "TSM", "NYSE"),   # a 20-F filer
    (900001, "SMALLCO INC", "SMLC", "Nasdaq"),                # debt under USD 100M
    (900002, "OLDBOOKS CORP", "OLDB", "NYSE"),                # balance sheet 2025-10-31
    (900003, "BIGLIAB CORP", "BGLB", "CBOE"),                 # debt above liabilities
    (900004, "NOSHARE HOLDINGS", "NOSH", "cboe"),             # no share count; exchange in lower case
    (900005, "TEXAS LISTED CO", "TXLC", "TXSE"),              # the Texas Stock Exchange
    (900006, "PINK SHEETS CO", "PINK", "OTC"),                # dropped
    (900007, "NULL EXCHANGE CO", "NULX", None),               # dropped
    (900008, "AMEX STYLE CO", "AMXS", "NYSE American"),       # an unknown string: dropped and counted
    (900009, "WARRANT FIRST CO", "WFCOW", "Nasdaq"),          # a warrant first: the primary is WFCO
    (900009, "WARRANT FIRST CO", "WFCO", "Nasdaq"),
    (900010, "BANKCO", "BNKC", "NYSE"),                       # deposits never debt; financial by Deposits
    (900011, "CONVERTCO", "CVCO", "Nasdaq"),                  # NotesPayable with a smaller convertible
    (900012, "CPBIGCO", "CPBG", "NYSE"),                      # CP larger than its STB: added
    (900013, "MULTICLASS CO", "MLTC", "NYSE"),                # no cover share count: the balance-sheet count
    (900014, "WAONLY CO", "WAOC", "NYSE"),                    # only the diluted weighted average
    (900015, "PITCO", "PITC", "NYSE"),                        # point in time: a 10-Q filed on 10-27
    (900016, "AMENDCO", "AMND", "Nasdaq"),                    # an amendment of the same balance sheet
    (900017, "UTILITY PREFCO", "UTPF-PA", "NYSE"),            # only preferred series listed: no listed common stock
    (900017, "UTILITY PREFCO", "UTPF-PB", "NYSE"),
    (900018, "PARENTCO ENERGY CORP /DE/", "PRNT", "NYSE"),    # a joint filer: its 2026-06-30 10-Q is filed under its subsidiary's CIK
    (900019, "PARENTCO ELECTRIC LLC", "PRNE", "NYSE"),        # the listed subsidiary (a note with a common-looking symbol)
]

# accession number -> (filed, form)
ACCN = {
    "0000320193-26-000041": ("2026-05-01", "10-Q"), "0000320193-26-000077": ("2026-07-31", "10-Q"),
    "0001341439-26-000031": ("2026-06-20", "10-K"), "0001341439-26-000047": ("2026-09-10", "10-Q"),
    "0000732712-26-000088": ("2026-07-25", "10-Q"), "0000795266-26-000052": ("2026-10-02", "10-Q"),
    "0000077476-26-000061": ("2026-07-10", "10-Q"), "0001571949-26-000033": ("2026-08-01", "10-Q"),
    "0000019617-26-000140": ("2026-08-02", "10-Q"), "0000037996-26-000090": ("2026-07-30", "10-Q"),
    "0000086312-26-000071": ("2026-07-22", "10-Q"), "0001045609-26-000080": ("2026-07-29", "10-Q"),
    "0000900001-26-000010": ("2026-08-10", "10-Q"), "0000900002-25-000020": ("2025-12-05", "10-K"),
    "0000900003-26-000011": ("2026-08-11", "10-Q"), "0000900004-26-000012": ("2026-08-12", "10-Q"),
    "0000900005-26-000013": ("2026-10-21", "10-Q"), "0000900009-26-000014": ("2026-08-13", "10-Q"),
    "0000900010-26-000015": ("2026-08-14", "10-Q"), "0000900011-26-000016": ("2026-08-15", "10-Q"),
    "0000900012-26-000017": ("2026-08-16", "10-Q"), "0000900013-26-000018": ("2026-08-17", "10-Q"),
    "0000900014-26-000019": ("2026-08-18", "10-Q"), "0000900015-26-000020": ("2026-08-05", "10-Q"),
    "0000900015-26-000031": ("2026-10-27", "10-Q"), "0000900016-26-000021": ("2026-08-06", "10-Q"),
    "0000900016-26-000029": ("2026-10-25", "10-Q/A"), "0000900017-26-000022": ("2026-08-07", "10-Q"),
    "0000900018-26-000010": ("2026-05-01", "10-Q"), "0000900018-26-000033": ("2026-07-30", "10-Q"),
}

# the frames' entityName of a fact when it is not its CIK's own: the SEC files a joint 10-Q's consolidated (parent) facts under one
# co-registrant's CIK with the PARENT's name (Entergy Corp's 2026-06-30 10-Q under Entergy Arkansas, 2026-10-29)
ENTITY_NAMES = {"0000900018-26-000033": "PARENTCO ENERGY CORPORATION"}

# facts: cik -> [(taxonomy, tag, unit, end, value, accn, start?)]  (USD in dollars; shares in shares)
G, DEI = "us-gaap", "dei"
FACTS = {
    320193: [(G, "LongTermDebt", "USD", "2026-03-28", 85000000000, "0000320193-26-000041"),        # an older quarter: not the latest end
             (G, "LongTermDebt", "USD", "2026-06-27", 82300000000, "0000320193-26-000077"),
             (G, "CommercialPaper", "USD", "2026-06-27", 1997000000, "0000320193-26-000077"),
             (G, "Liabilities", "USD", "2026-06-27", 265665000000, "0000320193-26-000077"),
             (G, "Assets", "USD", "2026-06-27", 331495000000, "0000320193-26-000077"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-17", 14594180000, "0000320193-26-000077")],
    1341439: [(G, "DebtLongtermAndShorttermCombinedAmount", "USD", "2026-05-31", 129541000000, "0001341439-26-000031"),
              (G, "LongTermNotesAndLoans", "USD", "2026-05-31", 122342000000, "0001341439-26-000031"),
              (G, "LongTermNotesPayable", "USD", "2026-05-31", 122342000000, "0001341439-26-000031"),
              (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-06-16", 3010000000, "0001341439-26-000031"),
              (G, "LongTermNotesAndLoans", "USD", "2026-08-31", 117712000000, "0001341439-26-000047"),
              (G, "NotesPayableCurrent", "USD", "2026-08-31", 7625000000, "0001341439-26-000047"),
              (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-09-08", 3023736000, "0001341439-26-000047")],
    732712: [(G, "LongTermDebtAndCapitalLeaseObligations", "USD", "2026-06-30", 143448000000, "0000732712-26-000088"),
             (G, "LongTermDebtCurrent", "USD", "2026-06-30", 21783000000, "0000732712-26-000088"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-24", 4154775202, "0000732712-26-000088")],
    795266: [(G, "NotesAndLoansPayable", "USD", "2026-08-31", 2109145000, "0000795266-26-000052"),
             (G, "Liabilities", "USD", "2026-08-31", 3170000000, "0000795266-26-000052"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-09-25", 60762736, "0000795266-26-000052")],
    77476: [(G, "LongTermDebt", "USD", "2026-06-13", 37000000000, "0000077476-26-000061"),
            (G, "ShortTermBorrowings", "USD", "2026-06-13", 10602000000, "0000077476-26-000061"),
            (G, "CommercialPaper", "USD", "2026-06-13", 6100000000, "0000077476-26-000061"),
            (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-03", 1370000000, "0000077476-26-000061")],
    1571949: [(G, "LongTermDebt", "USD", "2026-06-30", 18000000000, "0001571949-26-000033"),
              (G, "ShortTermBorrowings", "USD", "2026-06-30", 1218000000, "0001571949-26-000033"),
              (G, "CommercialPaper", "USD", "2026-06-30", 1218000000, "0001571949-26-000033"),
              (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-28", 573000000, "0001571949-26-000033")],
    19617: [(G, "LongTermDebtNoncurrent", "USD", "2026-06-30", 420000000000, "0000019617-26-000140"),
            (G, "ShortTermBorrowings", "USD", "2026-06-30", 50000000000, "0000019617-26-000140"),
            (G, "Deposits", "USD", "2026-06-30", 2400000000000, "0000019617-26-000140"),
            (G, "Liabilities", "USD", "2026-06-30", 3900000000000, "0000019617-26-000140"),
            (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-25", 2750000000, "0000019617-26-000140")],
    37996: [(G, "Liabilities", "USD", "2026-06-30", 250000000000, "0000037996-26-000090"),
            (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-24", 3980000000, "0000037996-26-000090")],
    86312: [(G, "LongTermDebt", "USD", "2026-06-30", 8000000000, "0000086312-26-000071"),
            (G, "LiabilityForClaimsAndClaimsAdjustmentExpense", "USD", "2026-06-30", 60000000000, "0000086312-26-000071"),
            (G, "Liabilities", "USD", "2026-06-30", 100000000000, "0000086312-26-000071"),
            (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-17", 225000000, "0000086312-26-000071")],
    1045609: [(G, "SecuredDebt", "USD", "2026-06-30", 1000000000, "0001045609-26-000080"),
              (G, "UnsecuredDebt", "USD", "2026-06-30", 30000000000, "0001045609-26-000080"),
              (G, "RealEstateInvestmentPropertyNet", "USD", "2026-06-30", 80000000000, "0001045609-26-000080"),
              (G, "Assets", "USD", "2026-06-30", 95000000000, "0001045609-26-000080"),
              (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-24", 926000000, "0001045609-26-000080")],
    900001: [(G, "LongTermDebt", "USD", "2026-06-30", 30000000, "0000900001-26-000010"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-05", 50000000, "0000900001-26-000010")],
    900002: [(G, "LongTermDebt", "USD", "2025-10-31", 500000000, "0000900002-25-000020"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2025-12-01", 80000000, "0000900002-25-000020")],
    900003: [(G, "LongTermDebt", "USD", "2026-06-30", 900000000, "0000900003-26-000011"),
             (G, "Liabilities", "USD", "2026-06-30", 800000000, "0000900003-26-000011"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-06", 10000000, "0000900003-26-000011")],
    900004: [(G, "LongTermDebt", "USD", "2026-06-30", 400000000, "0000900004-26-000012")],
    900005: [(G, "LongTermDebt", "USD", "2026-09-30", 250000000, "0000900005-26-000013"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-10-20", 20000000, "0000900005-26-000013")],
    900009: [(G, "LongTermDebt", "USD", "2026-06-30", 150000000, "0000900009-26-000014"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-08", 30000000, "0000900009-26-000014")],
    900010: [(G, "LongTermDebt", "USD", "2026-06-30", 5000000000, "0000900010-26-000015"),
             (G, "Deposits", "USD", "2026-06-30", 90000000000, "0000900010-26-000015"),
             (G, "Liabilities", "USD", "2026-06-30", 100000000000, "0000900010-26-000015"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-09", 400000000, "0000900010-26-000015")],
    900011: [(G, "NotesPayable", "USD", "2026-06-30", 900000000, "0000900011-26-000016"),
             (G, "ConvertibleNotesPayable", "USD", "2026-06-30", 300000000, "0000900011-26-000016"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-10", 60000000, "0000900011-26-000016")],
    900012: [(G, "LongTermDebt", "USD", "2026-06-30", 2000000000, "0000900012-26-000017"),
             (G, "ShortTermBorrowings", "USD", "2026-06-30", 500000000, "0000900012-26-000017"),
             (G, "CommercialPaper", "USD", "2026-06-30", 800000000, "0000900012-26-000017"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-11", 70000000, "0000900012-26-000017")],
    900013: [(G, "LongTermDebt", "USD", "2026-06-30", 1000000000, "0000900013-26-000018"),
             (G, "CommonStockSharesOutstanding", "shares", "2026-06-30", 300000000, "0000900013-26-000018")],
    900014: [(G, "LongTermDebt", "USD", "2026-06-30", 700000000, "0000900014-26-000019"),
             (G, "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", "2026-06-30", 90000000, "0000900014-26-000019", "2026-04-01")],
    900015: [(G, "LongTermDebt", "USD", "2026-06-30", 600000000, "0000900015-26-000020"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-01", 40000000, "0000900015-26-000020"),
             (G, "LongTermDebt", "USD", "2026-09-30", 650000000, "0000900015-26-000031"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-10-23", 41000000, "0000900015-26-000031")],
    900016: [(G, "LongTermDebt", "USD", "2026-06-30", 1000000000, "0000900016-26-000021"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-03", 25000000, "0000900016-26-000021"),
             (G, "LongTermDebt", "USD", "2026-06-30", 1100000000, "0000900016-26-000029")],      # the 10-Q/A restates it
    900017: [(G, "LongTermDebt", "USD", "2026-06-30", 5000000000, "0000900017-26-000022"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-08-01", 100000000, "0000900017-26-000022")],
    900018: [(G, "LongTermDebt", "USD", "2026-03-31", 28000000000, "0000900018-26-000010"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-04-24", 300000000, "0000900018-26-000010")],
    # the parent's 2026-06-30 joint 10-Q, as the frames API lists it: under the subsidiary's CIK, named PARENTCO ENERGY CORPORATION
    900019: [(G, "LongTermDebt", "USD", "2026-06-30", 29500000000, "0000900018-26-000033"),
             (DEI, "EntityCommonStockSharesOutstanding", "shares", "2026-07-24", 301000000, "0000900018-26-000033")],
}

# submissions: cik -> (sic, sic description, [(form, filed, report, accn)] newest first)
SUBMISSIONS = {
    1341439: ("7372", "Services-Prepackaged Software", [("8-K", "2026-10-15", None, "0001193125-26-410001"),
                                                         ("10-Q", "2026-09-10", "2026-08-31", "0001341439-26-000047"),
                                                         ("10-K", "2026-06-20", "2026-05-31", "0001341439-26-000031")]),
    1046179: ("3674", "Semiconductors & Related Devices", [("6-K", "2026-10-16", None, "0001046179-26-000090"),
                                                           ("20-F", "2026-04-15", "2025-12-31", "0001046179-26-000040")]),
    86312: ("6331", "Fire, Marine & Casualty Insurance", [("10-Q", "2026-07-22", "2026-06-30", "0000086312-26-000071")]),
    1045609: ("6798", "Real Estate Investment Trusts", [("10-Q", "2026-07-29", "2026-06-30", "0001045609-26-000080")]),
    19617: ("6021", "National Commercial Banks", [("10-Q", "2026-10-28", "2026-09-30", "0000019617-26-000190"),
                                                  ("10-Q", "2026-08-02", "2026-06-30", "0000019617-26-000140")]),
    37996: ("3711", "Motor Vehicles & Passenger Car Bodies", [("10-Q", "2026-10-28", "2026-09-30", "0000037996-26-000150"),
                                                              ("10-Q", "2026-07-30", "2026-06-30", "0000037996-26-000090")]),
    320193: ("3571", "Electronic Computers", [("10-K", "2026-10-28", "2026-09-26", "0000320193-26-000105"),
                                              ("10-Q", "2026-07-31", "2026-06-27", "0000320193-26-000077")]),
}

# companyfacts: JPMorgan's file (the newest 10-Q is in it; a 2014 LongTermDebt and an IFRS-free us-gaap set) and TSM's IFRS-only file
COMPANYFACTS = {
    19617: {"dei": {"EntityCommonStockSharesOutstanding": {"shares": [
                {"end": "2026-07-25", "val": 2750000000, "accn": "0000019617-26-000140", "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-08-02"},
                {"end": "2026-10-24", "val": 2735000000, "accn": "0000019617-26-000190", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-10-28"}]}},
            "us-gaap": {
                "LongTermDebt": {"USD": [{"end": "2014-12-31", "val": 276836000000, "accn": "0000019617-15-000272", "fy": 2014, "fp": "FY", "form": "10-K",
                                          "filed": "2015-02-24"}]},
                "LongTermDebtNoncurrent": {"USD": [
                    {"end": "2026-06-30", "val": 420000000000, "accn": "0000019617-26-000140", "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-08-02"},
                    {"end": "2026-09-30", "val": 431000000000, "accn": "0000019617-26-000190", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-10-28"}]},
                "ShortTermBorrowings": {"USD": [
                    {"end": "2026-09-30", "val": 52000000000, "accn": "0000019617-26-000190", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-10-28"},
                    {"end": "2026-09-30", "val": 51000000000, "accn": "0000019617-26-000201", "fy": 2026, "fp": "Q3", "form": "8-K", "filed": "2026-10-29"}]},
                "Deposits": {"USD": [
                    {"end": "2026-09-30", "val": 2450000000000, "accn": "0000019617-26-000190", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-10-28"}]},
                "Liabilities": {"USD": [
                    {"end": "2026-09-30", "val": 3950000000000, "accn": "0000019617-26-000190", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-10-28"}]}}},
    # Ford: its debt is in its own tags since 2021; the newest standard debt tag is the 2020 10-K's LongTermDebtNoncurrent
    37996: {"dei": {"EntityCommonStockSharesOutstanding": {"shares": [
                {"end": "2026-10-20", "val": 3980000000, "accn": "0000037996-26-000150", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-10-28"}]}},
            "us-gaap": {"LongTermDebtNoncurrent": {"USD": [{"end": "2020-12-31", "val": 291000000, "accn": "0000037996-21-000012", "fy": 2020, "fp": "FY",
                                                            "form": "10-K", "filed": "2021-02-05"}]},
                        "Liabilities": {"USD": [{"end": "2026-09-30", "val": 255000000000, "accn": "0000037996-26-000150", "fy": 2026, "fp": "Q3",
                                                 "form": "10-Q", "filed": "2026-10-28"}]}}},
    1046179: {"ifrs-full": {"Borrowings": {"TWD": [{"end": "2025-12-31", "val": 900000000000, "accn": "0001046179-26-000040", "form": "20-F",
                                                    "filed": "2026-04-15"}]}}},
}


def _q(end: str, instant: bool = True):
    d = date.fromisoformat(end)
    return f"CY{d.year}Q{(d.month - 1) // 3 + 1}" + ("I" if instant else "")


def main() -> None:
    names = {c: n for c, n, _t, _e in TICKERS}
    with open(os.path.join(HERE, "company_tickers_exchange.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"fields": ["cik", "name", "ticker", "exchange"], "data": [list(r) for r in TICKERS]}, fh, indent=0)
    frames = {}
    for cik, rows in FACTS.items():
        for row in rows:
            tax, tag, unit, end, val, accn = row[:6]
            start = row[6] if len(row) > 6 else None
            period = _q(end, instant=start is None)
            ename = ENTITY_NAMES.get(accn, names[cik])
            rec = {"accn": accn, "cik": cik, "entityName": ename, "loc": "US-NY", "end": end, "val": val}
            if start is not None:
                rec = {"accn": accn, "cik": cik, "entityName": ename, "loc": "US-NY", "start": start, "end": end, "val": val}
            frames.setdefault((tax, tag, unit, period), []).append(rec)
    for (tax, tag, unit, period), data in sorted(frames.items()):
        folder = os.path.join(HERE, "frames", tax, tag, unit)
        os.makedirs(folder, exist_ok=True)
        doc = {"taxonomy": tax, "tag": tag, "ccp": period, "uom": unit, "label": tag, "description": f"synthetic fixture ({tag})", "pts": len(data), "data": data}
        with open(os.path.join(folder, f"{period}.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, indent=0)
    os.makedirs(os.path.join(HERE, "submissions"), exist_ok=True)
    for cik, (sic, desc, filings) in SUBMISSIONS.items():
        recent = {"accessionNumber": [f[3] for f in filings], "filingDate": [f[1] for f in filings], "reportDate": [f[2] or "" for f in filings],
                  "form": [f[0] for f in filings], "primaryDocument": ["doc.htm" for _f in filings]}
        doc = {"cik": str(cik), "entityType": "operating", "sic": sic, "sicDescription": desc, "name": names[cik], "tickers": [t for c, _n, t, _e in TICKERS if c == cik],
               "exchanges": [e for c, _n, _t, e in TICKERS if c == cik], "filings": {"recent": recent, "files": []}}
        with open(os.path.join(HERE, "submissions", f"CIK{cik:010d}.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, indent=0)
    os.makedirs(os.path.join(HERE, "companyfacts"), exist_ok=True)
    for cik, taxa in COMPANYFACTS.items():
        facts = {}
        for tax, tags in taxa.items():
            facts[tax] = {tag: {"label": tag, "description": "synthetic fixture", "units": units} for tag, units in tags.items()}
        with open(os.path.join(HERE, "companyfacts", f"CIK{cik:010d}.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"cik": cik, "entityName": names[cik], "facts": facts}, fh, indent=0)
    with open(os.path.join(HERE, "filed.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({a: {"filed": f, "form": fm} for a, (f, fm) in sorted(ACCN.items())}, fh, indent=0)


if __name__ == "__main__":
    main()
