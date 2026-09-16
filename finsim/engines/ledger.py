"""Double-entry general ledger.

One ledger per portfolio. Every business event that has an accounting effect
posts a balanced `JournalEntry` (sum of debits == sum of credits, enforced).
Lines can carry a `security_id` dimension so a position's drill-down can show
the entries that built it. Account balances are *derived* from entries.

Chart of accounts (institutional fund style, trade-date accounting):
  1xxx assets, 2xxx liabilities, 3xxx capital, 4xxx income, 5xxx expenses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from ..money import D, money, ZERO

CHART = {
    "1010": ("Cash", "ASSET"),                              # sub-account per currency: 1010:USD
    "1100": ("Investments - Equities (cost)", "ASSET"),
    "1110": ("Investments - Fixed Income (cost)", "ASSET"),
    "1150": ("Investment Valuation Adjustment (MTM)", "ASSET"),
    "1200": ("Receivable - Securities Sold", "ASSET"),
    "1210": ("Dividends Receivable", "ASSET"),
    "1220": ("Accrued Interest Receivable - Bonds", "ASSET"),
    "1230": ("Accrued Interest Receivable - Cash", "ASSET"),
    "1240": ("Accrued Interest Receivable - Reverse Repo", "ASSET"),
    "1250": ("FX Settlement Receivable", "ASSET"),
    "1300": ("Margin Deposits - Futures Clearing", "ASSET"),
    "1400": ("Cash Collateral Posted", "ASSET"),
    "1410": ("Accrued Rebate Receivable - Cash Collateral", "ASSET"),
    "1500": ("Reverse Repo Receivable", "ASSET"),
    "1600": ("Derivative Assets - FX Forwards", "ASSET"),
    "1700": ("Options Purchased (at cost)", "ASSET"),
    "1750": ("Option Valuation Adjustment - Long (MTM)", "ASSET"),
    "1800": ("Derivative Assets - OTC (PV)", "ASSET"),
    "2100": ("Payable - Securities Purchased", "LIABILITY"),
    "2300": ("Accrued Interest Payable - Cash", "LIABILITY"),
    "2310": ("Accrued Borrow Fees Payable", "LIABILITY"),
    "2320": ("Manufactured Dividends Payable", "LIABILITY"),
    "2330": ("Accrued Interest Payable - Repo", "LIABILITY"),
    "2340": ("Accrued Interest Payable - Margin Loan", "LIABILITY"),
    "2350": ("FX Settlement Payable", "LIABILITY"),
    "2500": ("Repo Borrowing", "LIABILITY"),
    "2600": ("Securities Sold Short (at proceeds)", "LIABILITY"),
    "2610": ("Short Position Valuation Adjustment (MTM)", "LIABILITY"),
    "2700": ("Prime Broker Margin Loan", "LIABILITY"),
    "2800": ("Derivative Liabilities - FX Forwards", "LIABILITY"),
    "2900": ("Options Written (at premium received)", "LIABILITY"),
    "2910": ("Option Valuation Adjustment - Written (MTM)", "LIABILITY"),
    "2800": ("Derivative Liabilities - OTC (PV)", "LIABILITY"),
    "2450": ("Cash Collateral Received (CSA variation margin)", "LIABILITY"),
    "3000": ("Contributed Capital", "EQUITY"),
    "4000": ("Realized Gain/Loss - Investments", "INCOME"),
    "4100": ("Unrealized Gain/Loss - Investments", "INCOME"),
    "4200": ("Dividend Income", "INCOME"),
    "4300": ("Interest Income", "INCOME"),
    "4400": ("Futures Trading Gain/Loss (variation margin)", "INCOME"),
    "4350": ("Rebate Income - Cash Collateral", "INCOME"),
    "4500": ("Reverse Repo Interest Income", "INCOME"),
    "4600": ("Unrealized FX Translation Gain/Loss", "INCOME"),
    "4650": ("Realized FX Gain/Loss", "INCOME"),
    "4700": ("FX Forward MTM Gain/Loss", "INCOME"),
    "4800": ("Option Settlement Gain/Loss (cash-settled, expiry)", "INCOME"),
    "4900": ("OTC Derivative Mark-to-Market", "INCOME"),
    "4910": ("OTC Derivative Settlements (realised)", "INCOME"),
    "5000": ("Commissions & Fees", "EXPENSE"),
    "5100": ("Interest Expense", "EXPENSE"),
    "5300": ("Securities Borrow Expense", "EXPENSE"),
    "5400": ("Dividend Expense - Payments in Lieu", "EXPENSE"),
    "5500": ("Repo Interest Expense", "EXPENSE"),
    "5600": ("Margin Loan Interest Expense", "EXPENSE"),
    "5700": ("Buy-in Penalties", "EXPENSE"),
}

DEBIT_NORMAL = {"ASSET", "EXPENSE"}


def cost_account(sec, short: bool) -> str:
    """Position cost account: long equities 1100, long bonds 1110, long options 1700; shorts 2600 (securities) / 2900 (written options)."""
    if getattr(sec, "is_option", False):
        return "2900" if short else "1700"
    if short:
        return "2600"
    return "1110" if sec.is_bond else "1100"


def adj_account(sec, short: bool) -> str:
    if getattr(sec, "is_option", False):
        return "2910" if short else "1750"
    return "2610" if short else "1150"


def account_name(code: str) -> str:
    base = code.split(":")[0]
    name, _ = CHART[base]
    if ":" in code:
        return f"{name} ({code.split(':')[1]})"
    return name


def account_type(code: str) -> str:
    return CHART[code.split(":")[0]][1]


@dataclass
class JournalLine:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    security_id: Optional[str] = None
    memo: str = ""


@dataclass
class JournalEntry:
    id: str
    portfolio_id: str
    date: str
    memo: str
    lines: List[JournalLine]
    event_id: str                 # the LEDGER_POSTED event id
    cause_id: Optional[str]       # the business event that caused the posting
    reference: Dict = field(default_factory=dict)

    def total_debits(self) -> Decimal:
        return sum((l.debit for l in self.lines), ZERO)

    def total_credits(self) -> Decimal:
        return sum((l.credit for l in self.lines), ZERO)


class LedgerError(Exception):
    pass


def dr(account: str, amount, security_id: Optional[str] = None, memo: str = "") -> Dict:
    """Debit helper; a negative amount flips to a credit so callers can pass signed values."""
    a = money(amount)
    if a < 0:
        return {"account": account, "debit": "0", "credit": str(-a), "security_id": security_id, "memo": memo}
    return {"account": account, "debit": str(a), "credit": "0", "security_id": security_id, "memo": memo}


def cr(account: str, amount, security_id: Optional[str] = None, memo: str = "") -> Dict:
    a = money(amount)
    if a < 0:
        return {"account": account, "debit": str(-a), "credit": "0", "security_id": security_id, "memo": memo}
    return {"account": account, "debit": "0", "credit": str(a), "security_id": security_id, "memo": memo}


class Ledger:
    def __init__(self, portfolio_id: str):
        self.portfolio_id = portfolio_id
        self.entries: List[JournalEntry] = []
        self.balances: Dict[str, Decimal] = {}         # account -> signed balance (debit positive)
        self.security_balances: Dict[str, Dict[str, Decimal]] = {}  # security -> account -> balance
        self.entries_by_id: Dict[str, JournalEntry] = {}

    def post(self, entry_id: str, date: str, memo: str, lines: List[Dict], event_id: str, cause_id: Optional[str], reference: Dict) -> JournalEntry:
        jl = [JournalLine(l["account"], D(l.get("debit", 0)), D(l.get("credit", 0)), l.get("security_id"), l.get("memo", "")) for l in lines]
        jl = [l for l in jl if l.debit != 0 or l.credit != 0]
        if not jl:
            raise LedgerError("empty journal entry")
        entry = JournalEntry(entry_id, self.portfolio_id, date, memo, jl, event_id, cause_id, reference)
        if entry.total_debits() != entry.total_credits():
            raise LedgerError(f"unbalanced entry {memo}: dr {entry.total_debits()} cr {entry.total_credits()}")
        for l in jl:
            if l.account.split(":")[0] not in CHART:
                raise LedgerError(f"unknown account {l.account}")
            delta = l.debit - l.credit
            self.balances[l.account] = self.balances.get(l.account, ZERO) + delta
            if l.security_id:
                sb = self.security_balances.setdefault(l.security_id, {})
                sb[l.account] = sb.get(l.account, ZERO) + delta
        self.entries.append(entry)
        self.entries_by_id[entry_id] = entry
        return entry

    def balance(self, account: str) -> Decimal:
        """Natural-sign balance: assets/expenses debit-positive, others credit-positive."""
        raw = self.balances.get(account, ZERO)
        return raw if account_type(account) in DEBIT_NORMAL else -raw

    def balance_prefix(self, prefix: str) -> Decimal:
        return sum((self.balance(a) for a in self.balances if a.startswith(prefix)), ZERO)

    def security_balance(self, security_id: str, account: str) -> Decimal:
        raw = self.security_balances.get(security_id, {}).get(account, ZERO)
        return raw if account_type(account) in DEBIT_NORMAL else -raw

    def trial_balance(self) -> Dict:
        rows = []
        td = tc = ZERO
        for acct in sorted(self.balances):
            raw = self.balances[acct]
            d = raw if raw > 0 else ZERO
            c = -raw if raw < 0 else ZERO
            td += d
            tc += c
            rows.append({"account": acct, "name": account_name(acct), "type": account_type(acct), "debit": d, "credit": c, "balance": self.balance(acct)})
        return {"rows": rows, "total_debits": td, "total_credits": tc, "balanced": td == tc}

    def nav(self) -> Decimal:
        assets = sum((self.balance(a) for a in self.balances if account_type(a) == "ASSET"), ZERO)
        liabilities = sum((self.balance(a) for a in self.balances if account_type(a) == "LIABILITY"), ZERO)
        return assets - liabilities

    def income_statement(self) -> Dict[str, Decimal]:
        out = {}
        for acct in self.balances:
            if account_type(acct) in ("INCOME", "EXPENSE"):
                out[acct] = self.balance(acct)
        return out

    def net_income(self) -> Decimal:
        inc = sum((self.balance(a) for a in self.balances if account_type(a) == "INCOME"), ZERO)
        exp = sum((self.balance(a) for a in self.balances if account_type(a) == "EXPENSE"), ZERO)
        return inc - exp
