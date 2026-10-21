"""Fee, rate, session, contract, compliance and convention tables (TERMINAL_DESIGN.md §1.3, §9, §12, §12.5).

Every number here is a disclosed, modelled assumption.  Nothing in this module is a market observation: rates
indices come from FRED at run time (``quotes/rates.py``), fees are the terminal's own schedule, margins are a
static table ("verify against the exchange"), compliance thresholds carry "verify the current rule text".
``CONVENTIONS`` is the §1.3 map served as ``/api/state.conventions`` and shown inline where each sentence bites.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional, Tuple

from .units import to_c

D = Decimal

# ---- 12.1 fees ----------------------------------------------------------------------------------------------------
EQUITY_KINDS = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")

FEES = {
    "DEFAULT": {
        "EQUITY": {"per_share": D("0.005"), "min_c": 100, "basis": "USD 0.005/share, min 1.00"},
        "OPTION": {"per_contract": D("0.65"), "min_c": 0, "basis": "USD 0.65/contract"},
        "FUTURE": {"per_contract": D("2.25"), "min_c": 0, "basis": "USD 2.25/contract/side"},
        "FX": {"pip": D("0.0001"), "pip_jpy": D("0.01"), "spread_pips": D("0.2"), "basis": "FX_SPOT"},
        "BOND": {"per_100_face": D("0.02"), "min_c": 500, "basis": "USD 0.02/100 face, min 5.00"},
        "PRIVATE": {"pct": D("0.03"), "min_c": 0, "basis": "placement fee 3 % of gross (editable on the ticket)"},
        "SWAP": {"per_contract": D("0"), "min_c": 0, "basis": "no fee beyond the dealer's half-width (disclosed)"},
    }
}


def fee_kind(kind: str) -> str:
    """Map an instrument kind to a FEES row key."""
    if kind in EQUITY_KINDS:
        return "EQUITY"
    return kind if kind in FEES["DEFAULT"] else "EQUITY"


def fee_c(kind: str, qty: int, schedule: Optional[dict] = None, gross_c: Optional[int] = None,
          face: Optional[int] = None) -> Tuple[int, str]:
    """Commission in cents for a fill of ``qty`` units on the DEFAULT schedule (or a PB schedule dict of §12.4).
    Fees are quantised HALF_EVEN after the multiplication and floored by the minimum."""
    k = fee_kind(kind)
    row = FEES["DEFAULT"][k]
    q = abs(int(qty))
    if k == "EQUITY":
        per = D(str(schedule.get("equity_per_share"))) if schedule and schedule.get("equity_per_share") is not None else row["per_share"]
        min_c = int(schedule.get("equity_min_c")) if schedule and schedule.get("equity_min_c") is not None else row["min_c"]
        basis = f"USD {per}/share, min {min_c / 100:.2f}"
        return max(to_c(per * q), min_c) if q else 0, basis
    if k == "OPTION":
        per = D(str(schedule.get("option_per_contract"))) if schedule and schedule.get("option_per_contract") is not None else row["per_contract"]
        return to_c(per * q), f"USD {per}/contract"
    if k == "FUTURE":
        per = D(str(schedule.get("futures_per_side"))) if schedule and schedule.get("futures_per_side") is not None else row["per_contract"]
        return to_c(per * q), f"USD {per}/contract/side"
    if k == "BOND":
        f = int(face if face is not None else q)
        return max(to_c(row["per_100_face"] * D(f) / D(100)), row["min_c"]), row["basis"]
    if k == "PRIVATE":
        pct = row["pct"]
        return (to_c(D(int(gross_c or 0)) / D(100) * pct), f"placement fee {pct * 100:.2f} % of gross")
    return 0, row["basis"]


# ---- 12.2 rates ---------------------------------------------------------------------------------------------------
RATES = {
    "treasury_cash": {"index": "SOFR", "spread": D("-0.0050"), "floor": D("0"), "basis": 360,
                      "text": "Idle Treasury USD earns SOFR - 0.50 % (floor 0); foreign Treasury cash earns nothing in this build."},
    "pb_financing": {"index": "SOFR", "spread": "per PB schedule", "basis": 360, "text": "SOFR + PB spread, ACT/360, nightly on 2700, paid monthly"},
    "pb_credit": {"index": "SOFR", "spread": D("-0.0075"), "floor": D("0"), "basis": 360,
                  "text": "PB credit interest on positive settled USD: max(0, SOFR - 0.75 %) nightly (schedule field credit_interest_spread)"},
    "borrow_fee": {"GC": D("0.0030"), "T1": D("0.0100"), "T2": D("0.0500"), "T3": D("0.2500"), "basis": 360,
                   "text": "stock borrow fee by tier: GC 0.30 %, T1 1.00 %, T2 5.00 %, T3 25.00 %, ACT/360 on qty × mark"},
    "rebate": {"index": "SOFR", "spread": D("-0.0050"), "floor": D("0"), "basis": 360, "text": "cash collateral rebate max(0, SOFR - 0.50 %) (0 for T3)"},
    "currency_loan": {"spread": D("0.0075"), "text": "policy index + 0.75 %, ACT/360 (GBP ACT/365), nightly on principal, paid at repayment"},
    "repo": {"ust": D("0.0005"), "ig": D("0.0035"), "lending": D("-0.0005"), "haircuts": {"UST": D("0.02"), "IG": D("0.05"), "EQUITY": D("0.15")},
             "text": "Build 2: SOFR + 0.05 % (UST) / + 0.35 % (IG corp); SOFR - 0.05 % lending; haircuts UST 2 %, IG 5 %, equities 15 %"},
}
POLICY_INDEX = {"USD": "SOFR", "EUR": "ECBDFR", "GBP": "IUDSOIA", "JPY": "IRSTCI01JPM156N", "CHF": "IRSTCI01CHM156N",
                "CAD": "IRSTCI01CAM156N", "AUD": "IRSTCI01AUM156N"}
POLICY_INDEX_MONTHLY = ("IRSTCI01JPM156N", "IRSTCI01CHM156N", "IRSTCI01CAM156N", "IRSTCI01AUM156N")   # labelled "monthly proxy"
DAYCOUNT_CASH = {"GBP": 365, "*": 360}
LOAN_SPREAD = D("0.0075")


def daycount(ccy: str) -> int:
    return DAYCOUNT_CASH.get(ccy, DAYCOUNT_CASH["*"])


# Borrow availability tiers (heuristic from observable data; convention BORROW_AVAIL)
HTB = {
    "GC": {"fee": D("0.0030"), "availability": "full", "rule": "in the master, FINRA short ratio < 0.60 and ADV >= 1 M shares"},
    "T1": {"fee": D("0.0100"), "availability": "full", "rule": "ratio >= 0.60, or ADV in [250 k, 1 M)"},
    "T2": {"fee": D("0.0500"), "availability": "min(qty, 0.25 × ADV)", "rule": "ADV < 250 k"},
    "T3": {"fee": D("0.2500"), "availability": "min(qty, 0.10 × ADV)", "rule": "no FINRA row for the last 3 sessions, or preferred / ADR with ADV < 100 k"},
    "NONE": {"fee": None, "availability": "unavailable", "rule": "not in the master, no ADV, or not shortable -> NO_BORROW"},
}
HTB_RECALL_RATIO = D("0.80")   # three consecutive sessions >= 0.80 -> INFO "hard-to-borrow tightening" (never a recall)


def borrow_tier(in_master: bool, adv: Optional[int], ratio: Optional[Decimal], finra_rows_last3: int,
                kind: str = "EQUITY", shortable: bool = True) -> Tuple[str, Optional[int]]:
    """Pure tier rule of §12.2.  Returns (tier, qty_cap or None for 'full'); tier NONE means no borrow."""
    if not in_master or not adv or not shortable:
        return "NONE", 0
    adv = int(adv)
    if finra_rows_last3 <= 0 or (kind in ("PREFERRED", "ADR") and adv < 100_000):
        return "T3", int(D(adv) * D("0.10"))
    if adv < 250_000:
        return "T2", int(D(adv) * D("0.25"))
    if (ratio is not None and ratio >= D("0.60")) or adv < 1_000_000:
        return "T1", None
    return "GC", None


# ---- 12.3 futures settle times (the contract table itself is instruments.FUTURES_SPECS) ----------------------------
SETTLE_TIME_ET = {"CME_EQUITY_INDEX": "16:00", "CME_RATES": "16:00", "NYMEX_ENERGY": "14:30", "COMEX_METALS": "14:30",
                  "CBOT_GRAINS": "14:20", "CME_FX": "16:00"}
FUT_DV01 = {"ZT": D("1.9"), "ZF": D("4.2"), "ZN": D("6.5"), "ZB": D("15")}   # × price × 10 bp per contract, labelled approximate

# ---- 9 sessions (New York time; sessions.py evaluates these rules) ----------------------------------------------------
SESSIONS = {
    "US_EQUITY": {"kind": "US", "open": "09:30", "close": "16:00", "early": "13:00", "holidays": "NYSE file", "note": ""},
    "US_EQUITY_OPTIONS": {"kind": "US", "open": "09:30", "close": "16:00", "early": "13:00", "holidays": "NYSE file", "note": ""},
    "US_INDEX_OPTIONS": {"kind": "US", "open": "09:30", "close": "16:15", "early": "13:15", "holidays": "NYSE file", "note": ""},
    "US_BONDS": {"kind": "US", "open": "08:00", "close": "17:00", "early": "14:00", "holidays": "NYSE (SIFMA approximated)",
                 "note": "SIFMA holidays approximated by the NYSE calendar (Build 2 product)"},
    "CME_EQUITY_INDEX": {"kind": "CME", "open": "18:00", "close": "17:00", "early": "13:00", "holidays": "NYSE approximation", "note": "CME holidays are approximated from the NYSE calendar."},
    "CME_RATES": {"kind": "CME", "open": "18:00", "close": "17:00", "early": "13:00", "holidays": "NYSE approximation", "note": "CME holidays are approximated from the NYSE calendar."},
    "NYMEX_ENERGY": {"kind": "CME", "open": "18:00", "close": "17:00", "early": "13:00", "holidays": "NYSE approximation", "note": "CME holidays are approximated from the NYSE calendar."},
    "COMEX_METALS": {"kind": "CME", "open": "18:00", "close": "17:00", "early": "13:00", "holidays": "NYSE approximation", "note": "CME holidays are approximated from the NYSE calendar."},
    "CBOT_GRAINS": {"kind": "GRAINS", "night_open": "20:00", "night_close": "08:45", "open": "09:30", "close": "14:20", "early": "13:05",
                    "holidays": "NYSE approximation", "note": "CME holidays are approximated from the NYSE calendar."},
    "CME_FX": {"kind": "CME", "open": "18:00", "close": "17:00", "early": "13:00", "holidays": "NYSE approximation", "note": "CME holidays are approximated from the NYSE calendar."},
    "FX_SPOT": {"kind": "FX", "open": "17:00", "close": "17:00", "holidays": "1 Jan and 25 Dec closed (assumption)",
                "note": "FX spot trades Sun 17:00 - Fri 17:00 ET; value dates skip weekends and US holidays; other centres' holidays are not modelled."},
    "FOREIGN": {"kind": "FOREIGN", "holidays": "implied by Yahoo currentTradingPeriod", "note": "Build 2: foreign listings take their session from Yahoo's currentTradingPeriod."},
    "PRIVATE_MARKETS": {"kind": "NONE", "note": "Private secondaries are negotiated; there is no session."},
}

# ---- 12.4 prime-broker schedules (seed; pb_schedules.schedule_json is the live copy, editable in Settings) ---------
_PB_FIELDS_NOTE = ("Fields: equity_per_share, equity_min_c, option_per_contract, futures_per_side, financing_spread (SOFR + spread on "
                   "debit balances), credit_interest_spread (interest on positive settled USD = max(0, SOFR - spread)), gc_rate, htb_add, "
                   "otc_spread_mult, swap_margin_mode (CTM | STM), monthly_minimum_c, irs_half_width_bp, trs_spread.")


def _pb(name, eq, opt, fut, fin, gc_add, irs_bp, trs):
    return {"name": name, "equity_per_share": D(eq), "equity_min_c": 100, "option_per_contract": D(opt), "futures_per_side": D(fut),
            "financing_spread": D(fin), "credit_interest_spread": D("0.0075"), "gc_rate": D("0.0030") + D(gc_add), "htb_add": D(gc_add),
            "otc_spread_mult": D("1.0"), "swap_margin_mode": "CTM", "monthly_minimum_c": 0, "irs_half_width_bp": D(irs_bp),
            "trs_spread": D(trs), "note": "modelled assumptions; real PB schedules vary"}


PB_SCHEDULES = {
    "GS": _pb("Goldman Sachs", "0.0030", "0.55", "2.00", "0.0125", "0.0010", "1.0", "0.0060"),
    "JPM": _pb("JPMorgan", "0.0030", "0.55", "2.00", "0.0120", "0.0010", "1.0", "0.0055"),
    "MS": _pb("Morgan Stanley", "0.0035", "0.60", "2.10", "0.0130", "0.0012", "1.2", "0.0060"),
    "DB": _pb("Deutsche Bank", "0.0035", "0.60", "2.10", "0.0140", "0.0015", "1.5", "0.0070"),
    "BARC": _pb("Barclays", "0.0035", "0.60", "2.10", "0.0135", "0.0012", "1.2", "0.0065"),
    "CITI": _pb("Citi", "0.0030", "0.55", "2.00", "0.0130", "0.0010", "1.0", "0.0060"),
    "UBS": _pb("UBS", "0.0035", "0.60", "2.10", "0.0135", "0.0012", "1.2", "0.0065"),
    "BNP": _pb("BNP Paribas", "0.0040", "0.65", "2.25", "0.0145", "0.0015", "1.5", "0.0070"),
    "BOFA": _pb("BofA Securities", "0.0030", "0.55", "2.00", "0.0125", "0.0010", "1.0", "0.0060"),
}
PB_HEADROOM_PCT = D("0.50")   # Reg-T style initial: financing headroom = 50 % of the PB account's long market value - balance(2700)

# ---- 12.5 compliance limits (static, inform-only; each carries "verify the current rule text") -----------------------
COMPLIANCE_LIMITS = {
    "OPTION_POSITION_LIMITS": {"tiers": [25_000, 50_000, 75_000, 200_000, 250_000], "default": 250_000, "unit": "contracts"},
    "FUTURES_SPOT_MONTH": {"ES": 20_000, "NQ": 20_000, "YM": 20_000, "RTY": 20_000, "CL": 6_000, "BZ": 6_000, "NG": 2_000, "GC": 6_000,
                           "SI": 3_000, "HG": 1_500, "ZB": 100_000, "ZN": 100_000, "ZF": 100_000, "ZT": 100_000, "6E": 10_000, "6J": 10_000,
                           "6B": 10_000, "ZC": 1_200, "ZW": 1_200, "ZS": 1_200},
    "BENEFICIAL_OWNERSHIP_13DG": D("0.05"),
    "SECTION_16": D("0.10"),
    "LARGE_TRADER_13H1": {"shares_day": 2_000_000, "usd_day_c": 20_000_000_00, "shares_month": 20_000_000, "usd_month_c": 200_000_000_00},
    "FORM_13F_C": 100_000_000_00,
    "FORM_SHO_GROSS_SHORT_C": 10_000_000_00,
    "RULE_201_TRIGGER": D("0.10"),
    "EU_UK_SHORT_DISCLOSURE": {"notify": D("0.001"), "public": D("0.005")},
    "PDT_EQUITY_FLOOR_C": 25_000_00,
    "ECP_FLOOR_C": 10_000_000_00,
    "QP_THRESHOLD_C": {"INDIVIDUAL": 5_000_000_00, "FUND": 25_000_000_00, "INSTITUTION": 0},
    "verify": "verify the current rule text",
}

BOOK_PROFILES = {
    "INDIVIDUAL": {"label": "Personal (individual)", "rules": ["REG_T", "FINRA_4210", "PDT", "CASH_ACCOUNT_GOOD_FAITH", "13DG", "SECTION_16", "WASH_SALE", "SELF_TRADING", "REG_SHO", "RULE_201"],
                   "note": "Reg T 50 % initial / FINRA 4210 maintenance, pattern-day-trader (4+ day trades in 5 business days with NAV < 25,000), cash-account good-faith / free-riding shown pre-trade."},
    "FUND": {"label": "Fund", "rules": ["13F", "13H1", "13DG", "SECTION_16", "FORM_SHO", "OPTION_LIMITS", "FUTURES_LIMITS", "WASH_SALE", "SELF_TRADING", "REG_SHO", "RULE_201"],
             "note": "13F notice when 13F securities exceed USD 100 M; 13h-1 at the daily/monthly thresholds; 13D/G, Section 16, Form SHO / 13f-2, option and futures limits."},
    "INSTITUTION": {"label": "Institutional", "rules": ["13F_FROM_START", "13H1", "13DG", "SECTION_16", "FORM_SHO", "OPTION_LIMITS", "FUTURES_LIMITS", "PRIVATE_QP", "RULE_144", "FORM_D", "WASH_SALE", "SELF_TRADING", "REG_SHO", "RULE_201"],
                    "note": "13F applies from the start (file quarterly); 13h-1; 13D/G; Section 16; position limits / accountability levels; Form SHO / 13f-2; private markets: accredited / QP status, Rule 144, Form D (issuer's filing), 13F does not cover private shares."},
}

# ---- 1.3 conventions stated on screen (one sentence each, verbatim) -------------------------------------------------
CONVENTIONS: Dict[str, str] = {
    "FILL_PRICE": "Fills happen at the last observed trade (Yahoo, up to 15 min delayed) or, for options, at the Cboe delayed bid/ask; no price is invented.",
    "LIMIT_RULE": "A marketable limit fills at the observed price on its first look; afterwards a crossed limit fills at the limit price.",
    "STOP_RULE": "Stops trigger on the observed last trade and fill at that print; a gap through the stop fills at the gap price.",
    "PARTICIPATION": "Each new 1-minute bar lets at most 10 % of its volume fill; the remainder keeps working.",
    "NO_VOLUME": "With no volume information the order fills in full.",
    "SESSION": "Orders entered outside the session rest until it opens; DAY orders expire at that session's close; GTC after 90 days.",
    "STALE": "A quote older than its stated delay plus 5 minutes is stale and blocks executions; valuation keeps the last valid mark with its time.",
    "COST_BASIS": "Cost basis is gross; commissions are in Fees.",
    "FIFO": "Lots are relieved first-in, first-out.",
    "EXERCISE": "Exercise and assignment are commission-free; the option premium is rolled into the stock's cost basis or proceeds.",
    "OCC": "At expiry an option in the money by USD 0.01 or more is exercised or assigned from the observed closing price; early assignment is not simulated.",
    "FUT_SETTLE": "Futures settle daily at the observed price at or before the group's settlement time — not the exchange's official settlement.",
    "FUT_MARGIN": "Initial margins are a static table; verify against the exchange.",
    "OPT_MARGIN": "Short-option margin is a Reg T-style approximation and is moved to a margin deposit so buying power is honest.",
    "SHORT_COLLATERAL": "A borrow posts 102 % cash collateral from the account, marked daily both ways; sale proceeds settle as ordinary cash; the collateral is held at the lender until the borrow is returned.",
    "BORROW_AVAIL": "Borrow availability and fee tiers are estimated from FINRA daily short volume and average volume; a real locate would come from a lender.",
    "ACCRUAL": "Interest, borrow fees and rebates accrue for each calendar day the balance is open at 17:00 ET, ACT/360 (GBP ACT/365, bonds ACT/ACT), rounded cumulatively so daily postings never drift from the total.",
    "RATE_LAG": "Floating rates use the latest published observation on or before the accrual day (SOFR is published the next morning).",
    "FX_SPOT": "FX spot deals at the Yahoo mid plus or minus 0.2 pip against you (pip 0.0001, JPY pairs 0.01) and settles T+2.",
    "FX_RESULT": "FX result = foreign balances at today's rate minus the dollars they were worth when booked (no realised/unrealised split).",
    "ACCT_DAY": "The accounting day closes at 17:00 ET; 'today' compares with yesterday's 17:45 ET snapshot.",
    "SWEEP": "When an account needs more cash than it holds, exactly the shortfall moves from Treasury as one linked transfer; idle cash above the account's needs returns to Treasury after 17:30 ET.",
    "CME_CAL": "CME holidays are approximated from the NYSE calendar.",
    "CASH_INT": "Idle Treasury USD earns SOFR − 0.50 % (floor 0); foreign Treasury cash earns nothing in this build.",
    "COMPUTED": "A computed mark is a model from observed inputs (shown on hover) and is never executable.",
    "PAPER": "PAPER TRADING — fictional capital; free delayed market data.",
    "BOOKS": "Three books, each its own lifetime with its own Treasury, accounts and ledger: Personal (USD 1,000,000), Fund (USD 25,000,000), Institutional (USD 1,000,000,000); books never transfer to each other.",
    "LIFETIME": "Reset lifetime closes the current lifetime read-only (nothing is deleted) and opens a new one with the capital you type; past lifetimes stay browsable under History.",
    "ELIGIBILITY": "OTC swaps need Eligible Contract Participant status (CEA 1a(18): more than USD 10M invested) and a dealer's ISDA Master + CSA + Prime Brokerage Agreement; this terminal unlocks prime brokers and OTC when a book's NAV reaches the editable threshold (default USD 25,000,000) — dealers' real minimums vary; relationships already open stay open if NAV later falls.",
    "QP": "Private markets need accredited-investor / qualified-purchaser status (Rule 506, Rule 144 transfer restrictions); here the Private Markets account unlocks at USD 5,000,000 of NAV for the Personal book, USD 25,000,000 for the Fund book and from the start for the Institutional book (editable).",
    "SWAP_MARK": "A swap's mark is the clean present value from the live par curve (IRS) or the underlying's observed price since the last reset (TRS), labelled computed; the current period's coupons accrue separately and are paid on the payment date.",
    "SWAP_VM": "Swaps carry daily variation margin with the prime broker: the day's change in the computed mark moves in cash — collateralised-to-market (collateral, not P&L) unless the PB schedule says settled-to-market (realised each day, like futures).",
    "SWAP_RESET": "IRS floating legs fix at each reset from the latest published reference rate; TRS resets settle the return and financing legs in cash and reset the notional to the underlying's current value; cross-currency swaps exchange notional at start and maturity and, when booked mark-to-market, reset the USD notional at each reset with the difference settled in cash.",
    "TRAILING": "A trailing stop ratchets with each observed print in your favour (sell: highest last minus the trail; buy: lowest last plus the trail) and never moves back; it triggers on the observed last and fills at that print.",
    "AON": "An option-only package fills all-or-none: every leg must have a fresh Cboe quote with displayed size for the whole quantity in the same observation, or nothing fills; packages with a stock or futures leg fill sequentially (the hedge legs after the primary, resized on partial fills).",
    "PRIVATE_MARK": "A private company's mark is the last reported round's implied price per share with its date (never dimmed as stale; it changes only when a new round or a sourced secondary indication is recorded); the P&L is mark minus cost, realised on sale.",
    "PRIVATE_TRADE": "Private secondaries are negotiated: the trade executes at the price you enter (default the last round's price ± your discount or premium), pays the placement fee you set (default 3 %), settles after the lag you set (default T+30 calendar days) and carries a lock-up (default 6 months) that the Sell ticket warns about.",
    "PRIVATE_CREDIT": "A private loan pays the policy rate plus its spread, ACT/360, quarterly; the original-issue discount accretes straight-line to maturity; amendment fees are recorded when you receive them; the mark is a model price from the live rate and the FRED high-yield spread, labelled computed.",
    "DIVIDEND": "Dividends come from Yahoo's chart events: income on the ex-date for long positions, a payment in lieu for shorts; cash on the pay date when Yahoo gives one, else 15 business days after the ex-date (convention).",
    "EXCESS_LIQ": "Excess liquidity = Treasury available + every account's available cash − futures maintenance shortfalls; it is what the book could still deploy today.",
    "ENCUMBRANCE": "Encumbrance = collateral and margin the book has posted and cannot spend: stock-borrow collateral at the lender, futures and option margin, OTC initial and variation margin.",
    "AVAILABLE": "Available = max(0, settled cash + receivables due by the order's settlement date − payables − cash reserved by working orders), per account and per currency: the account's own spendable cash, never shown below zero; what working orders and payables need beyond it is shown apart as 'reserved (Treasury sweep covers N)' — exactly that shortfall moves from Treasury when the order fills or the payable settles, and a part Treasury cannot cover is flagged unfunded.",
    "TREASURY": "Treasury is the lifetime's own funds: contributed capital plus retained results (its cash interest) minus what it has advanced to the accounts; advances are internal transfers that net to zero across the lifetime.",
    "PERF": "Performance statistics use daily NAV-to-NAV returns with capital flows removed, annualised by √252, Sharpe and Sortino against SOFR, beta and alpha from a daily regression on SPY's observed closes; they need at least two daily snapshots and are per lifetime.",
    "NEWS": "Headlines are real and unedited: Yahoo Finance news search, the Federal Reserve press feed and this repository's own newspaper editions; they are shown as text and never interpreted.",
}
