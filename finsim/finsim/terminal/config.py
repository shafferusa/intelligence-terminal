"""Paths, environment and constants (TERMINAL_DESIGN.md §2.1).

Every number here is a disclosed, modelled assumption; the constants that the owner may edit at run time
(book names, starting capitals, the PB/OTC and qualified-purchaser thresholds, private-trade defaults) are only
the SEED of the ``settings`` table — the live values are read from the database (§3.1).
"""
from __future__ import annotations

import os
from decimal import Decimal

HOME_ENV = "FINSIM_HOME"
DB_ENV = "FINSIM_TERMINAL_DB"
PORT_ENV = "FINSIM_PORT"
DEFAULT_PORT = 8765
LEGACY_PORT = 8766
HOST = "127.0.0.1"
BASE_CCY = "USD"

# Books (§13.10): code, name, profile, starting capital in cents (editable in Settings; changes apply to NEW
# lifetimes only).  A lifetime is one run of a book: P1, P2, ...; every account id is "{lifetime_id}:{role}".
BOOKS = [("P", "Personal Book", "INDIVIDUAL", 1_000_000_00),
         ("F", "Fund Book", "FUND", 25_000_000_00),
         ("I", "Institutional Book", "INSTITUTION", 1_000_000_000_00)]
TEST_BOOK = ("T", "Test Book", "FUND", 10_000_000_00)      # terminal_helpers.make_runtime only; keeps §19's 10,000,000 numbers
ECP_FLOOR_C = 10_000_000_00                                 # CEA 1a(18) — below it a book cannot trade OTC at all
PB_OTC_THRESHOLD_C = 25_000_000_00                          # settings.pb_otc_threshold_c default; eligible when NAV >= threshold
QP_THRESHOLD_C = {"INDIVIDUAL": 5_000_000_00, "FUND": 25_000_000_00, "INSTITUTION": 0}   # Private Markets unlock, per profile

# Account roles (§13.10): role, name, kind, sort; account_id = f"{lifetime_id}:{role}" e.g. "P1:TREASURY", "I1:PB_GS".
ACCOUNT_CATALOGUE = [("TREASURY", "Treasury", "TREASURY", 0),
                     ("US_EXCHANGE", "US Exchange", "TRADING", 10),
                     ("DEBT", "Debt Instruments", "TRADING", 20),
                     ("CREDIT", "Credit Instruments", "TRADING", 30),
                     ("FOREIGN_FX", "Foreign Markets & FX", "TRADING", 40),
                     ("PRIVATE", "Private Markets", "TRADING", 50)]
PRIME_BROKERS = {"GS": "Goldman Sachs", "JPM": "JPMorgan", "MS": "Morgan Stanley", "DB": "Deutsche Bank",
                 "BOFA": "BofA Securities", "BARC": "Barclays", "CITI": "Citi", "UBS": "UBS", "BNP": "BNP Paribas"}   # role = "PB_" + key
ACCOUNT_ID_RE = r"^[A-Z][0-9]+:[A-Z_]+$"
PRIVATE_FEE_PCT = Decimal("0.03")
PRIVATE_SETTLE_DAYS = 30
PRIVATE_LOCKUP_MONTHS = 6                                   # defaults, editable on the ticket
SWAP_IM_BP = 50                                             # OTC initial margin = DV01 × 50 bp (static, disclosed)
NEWS_YAHOO = "https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=0&newsCount={n}"   # BROWSER_UA (Yahoo only)
NEWS_FED_RSS = "https://www.federalreserve.gov/feeds/press_all.xml"                                  # GOV_UA
NEWS_PAPER_URL = "https://shafferusa.github.io/intelligence-terminal/"
NEWS_INTERVALS = {"YAHOO": 900, "FED": 3600, "PAPER": 300}
PARTICIPATION_RATE = Decimal("0.10")
GTC_DAYS = 90
STALE_GRACE_S = 300
FETCH_GRACE_S = 300
ERROR_AGE_S = 60
SWEEP_BUFFER_C = 10_000_00
COLLATERAL_PCT = Decimal("1.02")
OCC_THRESHOLD = Decimal("0.01")
SWEEP_CONFIRM_THRESHOLD_C = 2_000_000_00                    # a sweep-in above this needs confirm_large (fat-finger guard)
PREVIEW_TTL_S = 120
PRICE_MOVED_PCT = Decimal("0.005")                          # 409 PREVIEW_STALE rules
QUOTE_INTERVALS = {"ORDER": 15, "HELD": 30, "WATCH": 60, "PAGE": 30, "CLOSED": 300, "CHAIN": 90, "CURVE": 86400, "FRED": 21600}
YAHOO_RPS = 2.0
CBOE_RPS = 1.0
FRED_RPS = 1.0
GOV_UA = "LoganTerminal/1.0 (loganshaffer87@gmail.com)"                       # .gov and cdn.finra.org
TERMINAL_UA = "FinSimTerminal/1.0 (paper trading; python stdlib)"             # Cboe, Frankfurter (honest, non-browser)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"  # Yahoo ONLY
MAX_BYTES = 16 << 20
CHAIN_MAX_DAYS = 400
CHAIN_STRIKE_BAND = Decimal("0.35")

# Accounting-day boundary (§6.6) and the lifecycle times used by the core (sessions / snapshots).
ACCOUNTING_DAY_CLOSE_ET = "17:00"
SNAPSHOT_TIME_ET = "17:45"
SWEEP_OUT_TIME_ET = "17:30"


def home() -> str:
    """The home folder: ``FINSIM_HOME`` or ``%USERPROFILE%\\.finsim``."""
    return os.environ.get(HOME_ENV) or os.path.join(os.path.expanduser("~"), ".finsim")


def db_path() -> str:
    """``FINSIM_TERMINAL_DB`` or ``<home>/terminal.db``.  ``finsim.db`` (the old simulation) is never this file."""
    return os.environ.get(DB_ENV) or os.path.join(home(), "terminal.db")


def log_path() -> str:
    return os.path.join(home(), "terminal.log")


def lock_path() -> str:
    return os.path.join(home(), "terminal.lock")


def port() -> int:
    return int(os.environ.get(PORT_ENV) or DEFAULT_PORT)


def package_dir() -> str:
    """``.../finsim/finsim/terminal``."""
    return os.path.dirname(os.path.abspath(__file__))


def repo_root() -> str:
    """The folder holding ``data/nyse-holidays.json`` and ``site/reports/index.json`` (three levels above the package)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(package_dir())))


def newspaper_index() -> str:
    return os.path.join(repo_root(), "site", "reports", "index.json")


def holidays_path() -> str:
    return os.path.join(repo_root(), "data", "nyse-holidays.json")


def universe_path() -> str:
    """``finsim/finsim/data/universe.json`` — the instrument seed (§7.4)."""
    return os.path.join(os.path.dirname(package_dir()), "data", "universe.json")


def private_companies_path() -> str:
    """``finsim/finsim/terminal/data/private_companies.json`` — the sourced private-company master (§13.13, WP-19)."""
    return os.path.join(package_dir(), "data", "private_companies.json")
