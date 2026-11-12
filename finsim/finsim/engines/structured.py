"""Structured credit: CLO, CMBS, non-agency RMBS and consumer ABS tranches as tradeable bonds.

Each tranche is a bond in the universe (asset class STRUCTURED) with the deal's payment terms: CLO debt and card ABS
float over the 3-month rate and reset every day; conduit CMBS, prime-jumbo RMBS and auto ABS pay fixed coupons. Every
tranche has a market spread (a discount margin for floaters) that the macro model moves with the credit indices and the
rating, exactly as it moves corporate spreads, but around the tranche's own norm (a AAA CLO trades 130bp wide, not the
40bp of a AAA corporate). Ratings migrate; a tranche that defaults is written down to its recovery. Deal structure
(overcollateralisation tests, reinvestment, sequential paydown) is summarised in the expected maturity and the
recovery, not modelled cash flow by cash flow.

Listed proxies for the desk: JAAA and CLOA (AAA CLOs), CMBS (agency and non-agency CMBS).
"""
from __future__ import annotations

from typing import List, Tuple

# id, name, deal type, tranche, rating, coupon (fixed) or spread over 3M (floater), floating, years, market spread bps, recovery, ADV face, sector
STRUCTURED_SEED: List[Tuple] = [
    # broadly syndicated loan CLOs: floating, five-year reinvestment, a two-year non-call, expected maturity ~7 years
    ("CLO-2026-1A", "Broadly Syndicated Loan CLO 2026-1 Class A", "CLO", "A (senior)", "AAA", 0.0135, True, 7.0, 135.0, 0.90, 30_000_000, "Structured Credit"),
    ("CLO-2026-1B", "Broadly Syndicated Loan CLO 2026-1 Class B", "CLO", "B", "AA", 0.0190, True, 8.0, 190.0, 0.80, 12_000_000, "Structured Credit"),
    ("CLO-2026-1C", "Broadly Syndicated Loan CLO 2026-1 Class C", "CLO", "C (mezzanine)", "A", 0.0240, True, 8.0, 240.0, 0.65, 8_000_000, "Structured Credit"),
    ("CLO-2026-1D", "Broadly Syndicated Loan CLO 2026-1 Class D", "CLO", "D (mezzanine)", "BBB-", 0.0380, True, 8.5, 380.0, 0.45, 6_000_000, "Structured Credit"),
    ("CLO-2026-1E", "Broadly Syndicated Loan CLO 2026-1 Class E", "CLO", "E (junior)", "BB-", 0.0700, True, 9.0, 700.0, 0.20, 4_000_000, "Structured Credit"),
    ("CLO-2025-2A", "Broadly Syndicated Loan CLO 2025-2 Class A (seasoned)", "CLO", "A (senior)", "AAA", 0.0145, True, 6.0, 128.0, 0.90, 25_000_000, "Structured Credit"),
    ("CLO-2025-2D", "Broadly Syndicated Loan CLO 2025-2 Class D (seasoned)", "CLO", "D (mezzanine)", "BBB", 0.0400, True, 7.5, 360.0, 0.45, 5_000_000, "Structured Credit"),
    ("MMCLO-2026-1A", "Middle-Market CLO 2026-1 Class A", "CLO", "A (senior)", "AAA", 0.0175, True, 7.0, 175.0, 0.88, 12_000_000, "Structured Credit"),
    # conduit CMBS: fixed, ten-year, super-senior down to the BBB- piece
    ("CMBS-2026-C1-A4", "Conduit CMBS 2026-C1 Class A-4 (last cash flow)", "CMBS", "A-4 (super senior, 30% credit support)", "AAA", 0.0565, False, 9.7, 100.0, 0.85, 20_000_000, "Structured Credit"),
    ("CMBS-2026-C1-AS", "Conduit CMBS 2026-C1 Class A-S", "CMBS", "A-S (senior)", "AAA", 0.0590, False, 9.8, 130.0, 0.80, 8_000_000, "Structured Credit"),
    ("CMBS-2026-C1-B", "Conduit CMBS 2026-C1 Class B", "CMBS", "B", "AA-", 0.0625, False, 9.8, 190.0, 0.65, 5_000_000, "Structured Credit"),
    ("CMBS-2026-C1-C", "Conduit CMBS 2026-C1 Class C", "CMBS", "C", "A-", 0.0665, False, 9.9, 240.0, 0.50, 4_000_000, "Structured Credit"),
    ("CMBS-2026-C1-D", "Conduit CMBS 2026-C1 Class D", "CMBS", "D", "BBB-", 0.0750, False, 9.9, 450.0, 0.30, 3_000_000, "Structured Credit"),
    ("CMBS-2024-C3-A4", "Conduit CMBS 2024-C3 Class A-4 (seasoned)", "CMBS", "A-4 (super senior)", "AAA", 0.0540, False, 7.8, 110.0, 0.85, 15_000_000, "Structured Credit"),
    # non-agency RMBS: prime jumbo, fixed
    ("RMBS-2026-1-A1", "Prime Jumbo RMBS 2026-1 Class A-1", "RMBS", "A-1 (senior)", "AAA", 0.0575, False, 6.5, 140.0, 0.85, 12_000_000, "Structured Credit"),
    ("RMBS-2026-1-M1", "Prime Jumbo RMBS 2026-1 Class M-1", "RMBS", "M-1 (mezzanine)", "A", 0.0640, False, 8.0, 250.0, 0.55, 4_000_000, "Structured Credit"),
    ("RMBS-2025-3-B1", "Non-QM RMBS 2025-3 Class B-1", "RMBS", "B-1 (subordinate)", "BB", 0.0800, False, 8.5, 480.0, 0.30, 2_500_000, "Structured Credit"),
    # consumer ABS: prime and subprime auto (fixed), credit cards (floating)
    ("AUTO-2026-1-A3", "Prime Auto Loan ABS 2026-1 Class A-3", "ABS", "A-3 (senior)", "AAA", 0.0480, False, 2.6, 60.0, 0.92, 20_000_000, "Structured Credit"),
    ("AUTO-2026-1-B", "Prime Auto Loan ABS 2026-1 Class B", "ABS", "B", "AA", 0.0520, False, 3.5, 100.0, 0.80, 5_000_000, "Structured Credit"),
    ("AUTO-2026-1-C", "Prime Auto Loan ABS 2026-1 Class C", "ABS", "C", "A", 0.0555, False, 3.8, 140.0, 0.65, 4_000_000, "Structured Credit"),
    ("SUBAUTO-2026-1-A", "Subprime Auto Loan ABS 2026-1 Class A", "ABS", "A (senior)", "AAA", 0.0505, False, 1.8, 90.0, 0.92, 10_000_000, "Structured Credit"),
    ("SUBAUTO-2026-1-D", "Subprime Auto Loan ABS 2026-1 Class D", "ABS", "D (subordinate)", "BB", 0.0790, False, 4.2, 400.0, 0.40, 2_500_000, "Structured Credit"),
    ("CARD-2026-A1", "Credit Card Master Trust 2026-A Class A", "ABS", "A (senior)", "AAA", 0.0055, True, 3.0, 55.0, 0.95, 25_000_000, "Structured Credit"),
    ("CARD-2026-C1", "Credit Card Master Trust 2026-A Class C", "ABS", "C (subordinate)", "BBB", 0.0180, True, 3.2, 180.0, 0.60, 5_000_000, "Structured Credit"),
    ("EQUIP-2026-1-A2", "Equipment Lease ABS 2026-1 Class A-2", "ABS", "A-2 (senior)", "AAA", 0.0495, False, 2.2, 75.0, 0.92, 8_000_000, "Structured Credit"),
]

DEAL_TYPES = {
    "CLO": ("Collateralised loan obligation", "a portfolio of broadly syndicated (or middle-market) leveraged loans; the debt tranches float over the 3-month rate and are paid sequentially; the equity takes what is left"),
    "CMBS": ("Commercial mortgage-backed securities", "a conduit pool of commercial mortgages; fixed-rate, ten-year; losses hit the bottom tranche first and the last-cash-flow A-4 last"),
    "RMBS": ("Non-agency residential MBS", "prime-jumbo or non-QM home loans without an agency guarantee; the senior tranche is protected by subordination"),
    "ABS": ("Asset-backed securities", "auto loans, credit-card receivables, equipment leases; short, amortising, senior tranches with hard credit enhancement"),
}
