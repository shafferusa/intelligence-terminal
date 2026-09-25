"""The product registry: every product type FinSim2 knows, how it is priced, what risk it carries, the unit its
hedges are sized in, and whether Shaffer Hedge may recommend it.

A product may be recommended only when FinSim2 has (1) current market data, (2) a pricing model, (3) its contract or
notional conventions, (4) a risk model and (5) a sizing rule. Otherwise it is PRODUCT NOT ELIGIBLE FOR SHAFFER HEDGE,
with the reason. Status of a product type:

    SUPPORTED      priced from market quotes, risk-modelled, tradeable in the ledger
    MODELLED       priced by a stated model from observed inputs (futures fair value, Treasury-futures model, options
                   with a Cboe implied-volatility index, FX forwards by covered interest parity); tradeable
    PROXY          available only through a listed fund (e.g. MBS through MBB); the fund itself is SUPPORTED
    ANALYSIS_ONLY  risk and sizing can be shown but FinSim2 cannot price the traded contract faithfully
    NOT_SUPPORTED  no data or no model (reason given); never recommended, never guessed

Instrument ids: a universe asset id ("SPY"); "FUT:<root>:<code>" (ES:Z26); "OPT:<underlying>:<C|P>:<strike>:<expiry>";
"FWD:<pair>:<value date>"."""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from . import pricing as px
from .market import VOL_INDEX

NOT_ELIGIBLE = "PRODUCT NOT ELIGIBLE FOR SHAFFER HEDGE"
# stated cost assumptions (no broker or borrow data is available to FinSim2)
COMMISSION = {"FUTURE": 2.25, "OPTION": 0.65}          # $ per contract per side
GC_BORROW = 0.0030                                      # general-collateral borrow fee, per year
OPTION_SPREAD = {"index": 0.02, "etf": 0.03, "stock": 0.05}   # share of premium (full spread), floor $0.05
FX_FORWARD_SPREAD = 0.0001                             # 1bp of notional


@dataclass
class ProductRiskProfile:
    product_type: str
    underlying: Optional[str] = None
    currency: str = "USD"
    market_value: Optional[float] = None
    notional: Optional[float] = None
    equity_beta: Optional[float] = None
    factor_betas: Optional[Dict[str, float]] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    rho: Optional[float] = None
    duration: Optional[float] = None
    convexity: Optional[float] = None
    DV01: Optional[float] = None
    key_rate_DV01: Optional[Dict[str, float]] = None
    spread_duration: Optional[float] = None
    CS01: Optional[float] = None
    FX_exposure: Optional[Dict[str, float]] = None
    commodity_exposure: Optional[Dict[str, float]] = None
    crypto_exposure: Optional[float] = None
    inflation_exposure: Optional[float] = None
    volatility_exposure: Optional[float] = None
    liquidity: Optional[dict] = None
    financing: Optional[float] = None
    borrow_cost: Optional[float] = None
    carry: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ contract conventions
FUTURES = {
    # equity index: multiplier in $ per index point; the underlying index and the ETF whose dividends/volume stand in
    "ES": {"name": "E-mini S&P 500", "kind": "equity_index", "underlying": "SPX", "proxy": "SPY", "multiplier": 50, "tick": 0.25},
    "MES": {"name": "Micro E-mini S&P 500", "kind": "equity_index", "underlying": "SPX", "proxy": "SPY", "multiplier": 5, "tick": 0.25},
    "NQ": {"name": "E-mini Nasdaq-100", "kind": "equity_index", "underlying": "NDX", "proxy": "QQQ", "multiplier": 20, "tick": 0.25},
    "MNQ": {"name": "Micro E-mini Nasdaq-100", "kind": "equity_index", "underlying": "NDX", "proxy": "QQQ", "multiplier": 2, "tick": 0.25},
    "RTY": {"name": "E-mini Russell 2000", "kind": "equity_index", "underlying": "RUT", "proxy": "IWM", "multiplier": 50, "tick": 0.1},
    "M2K": {"name": "Micro E-mini Russell 2000", "kind": "equity_index", "underlying": "RUT", "proxy": "IWM", "multiplier": 5, "tick": 0.1},
    "YM": {"name": "E-mini Dow ($5)", "kind": "equity_index", "underlying": "DJI", "proxy": "DIA", "multiplier": 5, "tick": 1.0},
    "MYM": {"name": "Micro E-mini Dow", "kind": "equity_index", "underlying": "DJI", "proxy": "DIA", "multiplier": 0.5, "tick": 1.0},
    # Treasury: face value per contract and the approximate maturity of the cheapest-to-deliver note or bond
    "ZT": {"name": "2-Year T-Note", "kind": "treasury", "face": 200000, "ctd_years": 1.9, "tick": 1 / 256},
    "ZF": {"name": "5-Year T-Note", "kind": "treasury", "face": 100000, "ctd_years": 4.2, "tick": 1 / 128},
    "ZN": {"name": "10-Year T-Note", "kind": "treasury", "face": 100000, "ctd_years": 6.6, "tick": 1 / 64},
    "TN": {"name": "Ultra 10-Year T-Note", "kind": "treasury", "face": 100000, "ctd_years": 9.5, "tick": 1 / 64},
    "ZB": {"name": "U.S. Treasury Bond", "kind": "treasury", "face": 100000, "ctd_years": 15.5, "tick": 1 / 32},
    "UB": {"name": "Ultra U.S. Treasury Bond", "kind": "treasury", "face": 100000, "ctd_years": 25.0, "tick": 1 / 32},
    # currency futures: contract size in units of the foreign currency, priced in USD per unit
    "6E": {"name": "Euro FX", "kind": "fx", "ccy": "EUR", "pair": "EURUSD", "size": 125000, "tick": 0.00005},
    "6B": {"name": "British Pound", "kind": "fx", "ccy": "GBP", "pair": "GBPUSD", "size": 62500, "tick": 0.0001},
    "6J": {"name": "Japanese Yen", "kind": "fx", "ccy": "JPY", "pair": "USDJPY", "size": 12500000, "tick": 0.0000005},
    "6A": {"name": "Australian Dollar", "kind": "fx", "ccy": "AUD", "pair": "AUDUSD", "size": 100000, "tick": 0.00005},
    "6C": {"name": "Canadian Dollar", "kind": "fx", "ccy": "CAD", "pair": "USDCAD", "size": 100000, "tick": 0.00005},
    "6S": {"name": "Swiss Franc", "kind": "fx", "ccy": "CHF", "pair": "USDCHF", "size": 125000, "tick": 0.00005},
    # commodity futures: FinSim2 has only continuous front-month quotes (roll gaps), so these are analysis only
    "CL": {"name": "WTI Crude Oil", "kind": "commodity", "underlying": "WTI", "multiplier": 1000, "unit": "bbl"},
    "BZ": {"name": "Brent Crude", "kind": "commodity", "underlying": "BRENT", "multiplier": 1000, "unit": "bbl"},
    "NG": {"name": "Henry Hub Natural Gas", "kind": "commodity", "underlying": "NATGAS", "multiplier": 10000, "unit": "MMBtu"},
    "GC": {"name": "Gold", "kind": "commodity", "underlying": "GOLD", "multiplier": 100, "unit": "troy oz"},
    "SI": {"name": "Silver", "kind": "commodity", "underlying": "SILVER", "multiplier": 5000, "unit": "troy oz"},
    "HG": {"name": "Copper", "kind": "commodity", "underlying": "COPPER", "multiplier": 25000, "unit": "lb"},
    "BTC": {"name": "CME Bitcoin", "kind": "crypto", "underlying": "BTC", "multiplier": 5, "unit": "BTC"},
    "MBT": {"name": "CME Micro Bitcoin", "kind": "crypto", "underlying": "BTC", "multiplier": 0.1, "unit": "BTC"},
}
INDEX_OPTION_UNDERLYINGS = {"SPX", "NDX", "RUT", "DJI"}      # European, cash-settled


# ------------------------------------------------------------------ the registry of product types
def _pt(key, name, group, status, via, risk_units, sizing, families, costs, reason=""):
    return {"key": key, "name": name, "group": group, "status": status, "via": via, "risk_units": risk_units, "sizing": sizing,
            "families": families, "costs": costs, "reason": reason}


EQ_FAMILIES = "Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value"
PRODUCT_TYPES: List[dict] = [
    _pt("cash", "Cash (USD)", "Cash / short-term", "SUPPORTED", "ledger cash", "none (base currency)", "—", "Rates (opportunity cost)", "none",
        "Cash earns no interest in the ledger; foreign-currency cash is held through FX pairs."),
    _pt("money_market", "Money-market funds", "Cash / short-term", "PROXY", "SGOV", "DV01 (≈0.1y)", "DV01", "Rates", "spread",
        "No money-market fund NAV/yield data; SGOV (0–3 month T-bill ETF) stands in."),
    _pt("t_bill", "Treasury bills", "Cash / short-term", "PROXY", "BIL, SGOV; DGS1MO/DGS3MO for pricing", "DV01", "DV01", "Rates", "spread",
        "Individual bills are not traded in the ledger; T-bill ETFs are."),
    _pt("common_stock", "Common stock (long)", "Equities", "SUPPORTED", "45 US stocks + any Yahoo symbol", "beta-$ (market, sector, industry), idiosyncratic $",
        "shares = beta-$ ÷ (price × β)", EQ_FAMILIES, "spread (Corwin–Schultz), commission $0"),
    _pt("short_stock", "Short stock", "Equities", "SUPPORTED", "SHORT/COVER in the ledger", "negative beta-$", "shares = beta-$ ÷ (price × β)",
        EQ_FAMILIES + " (net of borrow and dividends)", "spread, borrow (assumed GC 0.30%/yr), dividends owed, 150% Reg-T collateral",
        "Borrow availability and hard-to-borrow fees are not available: general-collateral borrow is assumed."),
    _pt("preferred", "Preferred stock", "Equities", "PROXY", "PFF", "DV01, CS01", "DV01 / CS01", "Rates, Credit, Momentum", "spread",
        "Issue-level preferreds (call schedules, dividend coverage) are not modelled; PFF is."),
    _pt("reit", "REITs", "Equities", "PROXY", "VNQ, XLRE", "beta-$, rate sensitivity (empirical)", "beta-$", EQ_FAMILIES, "spread",
        "FFO/AFFO and NAV premium are not available from SEC companyfacts in a consistent form."),
    _pt("adr_foreign_equity", "ADRs / foreign equities", "Equities", "PARTIAL", "country ETFs (EWJ, EWG, …), foreign indices; add ADRs by symbol",
        "beta-$ + currency $ (look-through)", "beta-$; FX notional", EQ_FAMILIES + ", FX", "spread",
        "ADR/local-share basis and country risk premia are not modelled."),
    _pt("etf", "ETFs", "Funds", "SUPPORTED", "≈80 ETFs", "beta-$ / DV01 / CS01 / commodity $ by what it holds", "per risk unit", "by holdings", "spread",
        "Premium/discount to NAV and flows are not available."),
    _pt("leveraged_etf", "Leveraged ETFs", "Funds", "SUPPORTED", "SSO, QLD, TQQQ", "beta-$ (≈ leverage × index beta, daily)", "beta-$",
        EQ_FAMILIES + "; volatility drag L(L−1)σ²/2 per year", "spread, expense ratio (not modelled)"),
    _pt("inverse_etf", "Inverse ETFs", "Funds", "SUPPORTED", "SH, SDS, PSQ, SQQQ", "negative beta-$ (daily reset)", "beta-$",
        EQ_FAMILIES + "; volatility drag", "spread"),
    _pt("etn", "ETNs", "Funds", "PARTIAL", "VXX", "VIX-futures $ per VIX point (empirical)", "vega-equivalent $", "Volatility",
        "spread", "Issuer (Barclays) credit risk is not modelled."),
    _pt("closed_end_fund", "Closed-end funds", "Funds", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No NAV series: the discount/premium, the main CEF signal, cannot be computed."),
    _pt("government_bond", "Government bonds", "Bonds / credit", "MODELLED", "UST2Y/5Y/10Y/30Y constant-maturity indices; Treasury ETFs",
        "DV01 by key rate (2/5/10/30Y)", "DV01", "Rates, Momentum, Trend", "spread (ETFs); none (index)"),
    _pt("tips", "TIPS / inflation-linked", "Bonds / credit", "PROXY", "TIP, SCHP (DFII10 real yield)", "real-yield DV01", "real DV01",
        "Rates (real), Macro (inflation)", "spread", "Individual TIPS and index ratios are not modelled."),
    _pt("municipal", "Municipal bonds", "Bonds / credit", "PROXY", "MUB", "DV01", "DV01", "Rates", "spread",
        "Tax-equivalent yield and issuer credit are not modelled."),
    _pt("ig_corporate", "Investment-grade corporates", "Bonds / credit", "PROXY", "LQD, VCIT; CORP_BAA index", "DV01 + CS01 (IG OAS)", "DV01 / CS01",
        "Rates, Credit", "spread", "Issuer-level bonds are not available."),
    _pt("high_yield", "High-yield bonds", "Bonds / credit", "PROXY", "HYG, JNK", "DV01 + CS01 (HY OAS)", "CS01", "Credit, Rates, Macro", "spread",
        "Default probabilities and recoveries are not available."),
    _pt("frn", "Floating-rate notes", "Bonds / credit", "PROXY", "FLOT", "CS01 (spread duration ≈2y), DV01 ≈0", "CS01", "Credit", "spread"),
    _pt("leveraged_loan", "Bank / leveraged loans", "Bonds / credit", "PROXY", "BKLN", "CS01 (HY), DV01 ≈0", "CS01", "Credit, Macro", "spread"),
    _pt("mbs", "Agency MBS", "Bonds / credit", "PROXY", "MBB", "DV01 (negative convexity not modelled)", "DV01", "Rates", "spread",
        "No OAS, prepayment or pool data: negative convexity is only visible empirically."),
    _pt("abs", "ABS", "Bonds / credit", "NOT_SUPPORTED", "— (JAAA covers AAA CLOs only)", "—", "—", "—", "—",
        "No collateral, subordination or ABS spread data."),
    _pt("convertible", "Convertible bonds", "Bonds / credit", "PROXY", "CWB", "beta-$ (empirical)", "beta-$", EQ_FAMILIES, "spread",
        "The bond-floor + option decomposition needs issue terms, which are not available."),
    _pt("equity_index_future", "Equity index futures", "Futures", "MODELLED", "ES, MES, NQ, MNQ, RTY, M2K, YM, MYM",
        "beta-$ = price × multiplier × β", "contracts = beta-$ ÷ (F × multiplier × β)", "underlying index + carry (r − q)",
        "1 tick + $2.25/contract/side; roll at each quarter; margin (SPAN-like estimate) is collateral, not exposure",
        "Priced at fair value S·e^{(r−q)T} (index close, 3-month bill, trailing dividend yield of the tracking ETF), not a futures quote."),
    _pt("single_stock_future", "Single-stock futures", "Futures", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No US single-stock futures have traded since OneChicago closed (2020)."),
    _pt("treasury_future", "Treasury futures", "Futures", "MODELLED", "ZT, ZF, ZN, TN, ZB, UB", "DV01 per contract at the CTD key rate",
        "contracts = DV01 ÷ DV01 per contract", "Rates + carry", "1 tick + $2.25/contract/side",
        "Modelled as the forward price of the 6% notional coupon at the approximate cheapest-to-deliver maturity on the Treasury curve; "
        "conversion factors, delivery options and the actual CTD are not modelled."),
    _pt("commodity_future", "Commodity futures", "Futures", "ANALYSIS_ONLY", "CL, BZ, NG, GC, SI, HG (continuous front-month quotes)",
        "commodity $ = price × multiplier", "contracts = commodity $ ÷ (price × multiplier)", "Momentum, Trend, Macro", "—",
        "Only continuous front-month series are available: without per-contract prices a held position would book roll gaps as P&L. "
        "Commodity ETFs (USO, UNG, GLD, SLV, CPER, DBA, DBC) are used for hedging instead."),
    _pt("fx_future", "FX futures", "Futures", "MODELLED", "6E, 6B, 6J, 6A, 6C, 6S", "currency $ = contract size × price",
        "contracts = currency $ ÷ (size × price)", "FX + carry", "1 tick + $2.25/contract/side",
        "Priced by covered interest parity from spot and FRED short rates (CHF's rate series ended in 2024, so 6S is ineligible)."),
    _pt("crypto_future", "Crypto futures", "Futures", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No futures basis or term-structure data; BITO (futures ETF) and IBIT are the tradeable proxies."),
    _pt("fx_spot", "FX spot", "FX / forwards", "SUPPORTED", "10 pairs + DXY", "currency $", "notional = currency exposure", "Momentum, Trend, Rates, Macro",
        "spread"),
    _pt("fx_forward", "FX forwards", "FX / forwards", "MODELLED", "FWD:<pair>:<date>", "currency $", "notional = currency exposure to remove",
        "FX + forward carry (rate differential)", "1bp of notional",
        "Covered interest parity with FRED short rates (USD 3M bill, ECB deposit rate, SONIA, monthly JPY/CAD/AUD call rates)."),
    _pt("ndf", "NDFs", "FX / forwards", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No onshore/offshore rate or NDF-point data (INR, CNY)."),
    _pt("commodity_forward", "Commodity forwards", "FX / forwards", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No storage, convenience-yield or forward-curve data."),
    _pt("equity_call", "Equity calls", "Options", "MODELLED", "AAPL, AMZN, GOOGL, GS, IBM (Cboe equity VIX indices)", "delta-$, gamma, vega, theta",
        "contracts = delta-$ ÷ (S × 100 × |Δ| × β)", "underlying Shaffer + (forecast vol − implied vol) − theta", "premium, spread (5%), $0.65/contract",
        "Other single stocks (e.g. NVDA) have no implied-volatility data and are not eligible; flat volatility (no skew)."),
    _pt("equity_put", "Equity puts", "Options", "MODELLED", "same as calls", "delta-$, gamma, vega, theta", "contracts = delta-$ ÷ (S × 100 × |Δ| × β)",
        "as calls, bearish direction", "premium, spread, commission", "As calls; OTM put premiums are likely understated without skew."),
    _pt("index_option", "Index / ETF options", "Options", "MODELLED", "SPX, SPY, NDX, QQQ, RUT, IWM, DJI, DIA, EEM, EWZ (VIX, VXV, VXN, RVX, VXD, VXEEM, VXEWZ)",
        "delta-$, gamma, vega, theta", "contracts = delta-$ ÷ (S × 100 × |Δ| × β)", "direction + vol pricing + convexity", "premium, spread (2–3%), commission",
        "Implied volatility is the index's 30-day (and for the S&P 500 the 3-month) level, flat across strikes: no skew data."),
    _pt("fx_option", "FX options", "Options", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No FX implied-volatility data (EVZ was discontinued in 2025)."),
    _pt("commodity_option", "Commodity options", "Options", "MODELLED", "options on GLD and USO (GVZ, OVX)", "delta-$, vega", "delta-$",
        "direction + vol", "premium, spread (3%)", "ETF options only; options on futures are not modelled."),
    _pt("crypto_option", "Crypto options", "Options", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No crypto implied-volatility data."),
    _pt("warrant", "Warrants", "Options", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No warrant terms or dilution data."),
    _pt("irs", "Interest-rate swaps", "Rates derivatives", "ANALYSIS_ONLY", "DV01 per $ notional from the Treasury curve", "DV01",
        "notional = DV01 ÷ DV01 per $", "Rates", "—", "No SOFR swap curve: swap spreads are unknown, so a held swap cannot be valued faithfully."),
    _pt("ois", "OIS", "Rates derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No OIS curve."),
    _pt("fra", "FRAs", "Rates derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No forward-rate (term SOFR/futures) curve."),
    _pt("cap", "Caps", "Rates derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No interest-rate volatility data."),
    _pt("floor", "Floors", "Rates derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No interest-rate volatility data."),
    _pt("swaption", "Swaptions", "Rates derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No swaption volatility surface."),
    _pt("trs", "Total return swaps", "Other swaps", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No dealer financing spreads; the equity-index future carries the same exposure and is supported."),
    _pt("equity_swap", "Equity swaps", "Other swaps", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "As TRS."),
    _pt("commodity_swap", "Commodity swaps", "Other swaps", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No commodity forward curves."),
    _pt("fx_swap", "FX swaps", "Other swaps", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "Equivalent to spot + forward; the forward is supported."),
    _pt("cross_currency_swap", "Cross-currency swaps", "Other swaps", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No cross-currency basis data."),
    _pt("inflation_swap", "Inflation swaps", "Other swaps", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No zero-coupon inflation swap quotes (TIPS breakevens are available for analysis)."),
    _pt("cds_buy", "CDS — buy protection", "Credit derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No single-name CDS spreads."),
    _pt("cds_sell", "CDS — sell protection", "Credit derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No single-name CDS spreads."),
    _pt("cdx_itraxx", "CDX / iTraxx", "Credit derivatives", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No index quotes; credit risk is hedged with HYG/JNK/LQD sized by CS01."),
    _pt("variance_swap", "Variance swaps", "Volatility", "ANALYSIS_ONLY", "S&P 500 (fair strike ≈ VIX²)", "vega notional", "vega notional", "Volatility",
        "—", "The VIX approximates the 30-day fair strike, but there is no dealer market data to value a held swap."),
    _pt("vol_swap", "Volatility swaps", "Volatility", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No volatility-swap quotes."),
    _pt("vix_future", "VIX futures", "Volatility", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No VIX futures curve (the Cboe futures host is not reachable); VXX is the tradeable proxy."),
    _pt("vix_option", "VIX options", "Volatility", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "Needs the VIX futures curve."),
    _pt("crypto_spot", "Crypto spot", "Crypto", "SUPPORTED", "BTC, ETH, SOL (long only)", "crypto $", "notional", "Momentum, Trend, Volatility, Liquidity, Macro",
        "spread", "Shorting spot crypto is not supported (no borrow); IBIT/BITO can be shorted."),
    _pt("crypto_perp", "Crypto perpetuals", "Crypto", "NOT_SUPPORTED", "—", "—", "—", "—", "—", "No funding-rate or open-interest data."),
    _pt("structured_note", "Structured notes", "Structured", "NOT_SUPPORTED", "—", "—", "—", "—", "—",
        "No payoff model is registered for any note; FinSim2 refuses rather than guesses."),
]
PRODUCT_TYPE = {p["key"]: p for p in PRODUCT_TYPES}


def product_type_of(asset: dict) -> str:
    """Registry key for a universe asset."""
    meta = asset.get("meta") or {}
    if meta.get("product_type"):
        return meta["product_type"]
    cls = asset.get("asset_class")
    if cls == "EQUITY":
        return "common_stock"
    if cls == "TREASURY":
        return "government_bond"
    if cls == "CORP_BOND":
        return "ig_corporate"
    if cls == "FX":
        return "fx_spot"
    if cls == "CRYPTO":
        return "crypto_spot"
    if cls == "COMMODITY":
        return "commodity_future"
    if cls == "INDEX":
        return "index"
    aid = asset.get("id")
    return {"TIP": "tips", "MUB": "municipal", "LQD": "ig_corporate", "VCIT": "ig_corporate", "HYG": "high_yield", "JNK": "high_yield",
            "XLRE": "reit"}.get(aid, "etf")


# ------------------------------------------------------------------ instruments
@dataclass
class Instrument:
    id: str
    type: str                       # SPOT | FUTURE | OPTION | FORWARD
    underlying: str                 # asset id (SPOT: itself)
    multiplier: float = 1.0
    currency: str = "USD"
    expiry: Optional[str] = None
    roll: Optional[str] = None
    right: Optional[str] = None
    strike: Optional[float] = None
    style: str = "EUROPEAN"
    root: Optional[str] = None
    spec: dict = field(default_factory=dict)
    product_type: str = "etf"
    name: str = ""

    def view(self) -> dict:
        d = asdict(self)
        d["product"] = PRODUCT_TYPE.get(self.product_type, {}).get("name", self.product_type)
        return d


def future_id(root: str, code: str) -> str:
    return f"FUT:{root}:{code}"


def option_id(underlying: str, right: str, strike: float, expiry: str) -> str:
    k = f"{strike:g}"
    return f"OPT:{underlying}:{right.upper()[0]}:{k}:{expiry}"


def forward_id(pair: str, value_date: str) -> str:
    return f"FWD:{pair}:{value_date}"


def _code_expiry(kind: str, code: str) -> Tuple[str, str]:
    inv = {v: k for k, v in px.MONTH_CODE.items()}
    m, y = inv[code[0]], 2000 + int(code[1:])
    asof = _dt.date(y, m, 1) - _dt.timedelta(days=100)
    for c, ltd, roll in px.future_expiries(kind, asof.isoformat(), 4):
        if c == code:
            return ltd, roll
    raise ValueError(f"cannot resolve contract month {code}")


def parse(inst_id: str, store=None) -> Instrument:
    """An Instrument from its id. Raises ValueError for malformed ids or unknown contracts."""
    parts = inst_id.split(":")
    if parts[0] == "FUT":
        root, code = parts[1], parts[2]
        spec = FUTURES.get(root)
        if not spec:
            raise ValueError(f"unknown futures root {root}")
        ltd, roll = _code_expiry("treasury" if spec["kind"] == "treasury" else "equity", code)
        und = spec.get("underlying") or spec.get("pair") or root
        mult = spec.get("multiplier") or (spec["face"] / 100.0 if spec["kind"] == "treasury" else spec.get("size", 1.0))
        pt = {"equity_index": "equity_index_future", "treasury": "treasury_future", "fx": "fx_future", "commodity": "commodity_future",
              "crypto": "crypto_future"}[spec["kind"]]
        return Instrument(inst_id, "FUTURE", und, float(mult), "USD", ltd, roll, root=root, spec=spec, product_type=pt,
                          name=f"{spec['name']} {code}")
    if parts[0] == "OPT":
        und, right, strike, exp = parts[1], parts[2], float(parts[3]), parts[4]
        if right not in ("C", "P"):
            raise ValueError("option right must be C or P")
        style = "EUROPEAN" if und in INDEX_OPTION_UNDERLYINGS else "AMERICAN"
        a = store.asset(und) if store else None
        cls = (a or {}).get("asset_class")
        pt = "index_option" if (und in INDEX_OPTION_UNDERLYINGS or cls == "ETF") else "equity_put" if right == "P" else "equity_call"
        if und in ("GLD", "USO"):
            pt = "commodity_option"
        return Instrument(inst_id, "OPTION", und, float(px.OPTION_MULTIPLIER), "USD", exp, None, right, strike, style, spec={},
                          product_type=pt, name=f"{und} {exp} {strike:g} {'call' if right == 'C' else 'put'}")
    if parts[0] == "FWD":
        pair, vd = parts[1], parts[2]
        a = store.asset(pair) if store else None
        if store and (not a or a.get("asset_class") != "FX"):
            raise ValueError(f"{pair} is not an FX pair")
        quote = (a or {}).get("currency") or pair[3:]
        return Instrument(inst_id, "FORWARD", pair, 1.0, quote, vd, None, product_type="fx_forward", name=f"{pair} forward {vd}",
                          spec={"base": pair[:3], "quote": quote})
    a = store.asset(inst_id) if store else {"id": inst_id, "asset_class": "EQUITY"}
    if store and a is None:
        raise ValueError(f"unknown asset {inst_id}")
    return Instrument(inst_id, "SPOT", inst_id, 1.0, a.get("currency") or "USD", product_type=product_type_of(a), name=a.get("name", inst_id))


# ------------------------------------------------------------------ pricing an instrument at a market snapshot
FLAT_VOL_LABEL = "MODEL-PRICED — FLAT VOLATILITY ASSUMPTION"
FLAT_VOL_NOTE = ("No option-chain or skew data: every strike uses the at-the-money implied volatility, so out-of-the-money "
                 "puts are probably priced too cheaply and crash hedges look more attractive than they are.")


def pricing_label(inst) -> Optional[str]:
    """How the price of an instrument is obtained, when it is not a market quote."""
    if inst.type == "OPTION":
        return FLAT_VOL_LABEL
    if inst.type in ("FUTURE", "FORWARD"):
        return "MODEL-PRICED — FAIR VALUE, NOT A QUOTE"
    return None


class Priced:
    """An instrument evaluated at one Market snapshot: unit price, unit value, unit notional, exposures per unit,
    Greeks, costs, liquidity and eligibility. `exposures` are $ per unit factor move for ONE unit (share, contract,
    option contract, unit of base currency)."""

    def __init__(self, inst: Instrument, market, risk=None):
        self.inst, self.m, self.risk = inst, market, risk
        self.reasons: List[str] = []
        self.notes: List[str] = []
        self.greeks: Optional[dict] = None
        self.inputs: dict = {}
        self.price: Optional[float] = None
        self.pricing_label = pricing_label(inst)
        self._price()

    # -------------------------------------------------------------- price
    def _T(self) -> float:
        return px.year_frac(self.m.asof, self.inst.expiry) if self.inst.expiry else 0.0

    def _price(self):
        i, m = self.inst, self.m
        if i.type == "SPOT":
            p, d = m.close(i.id)
            self.inputs["price"] = {"value": p, "date": d, "source": "close"}
            if p is None:
                self.reasons.append(f"no current {i.id} price (last close older than 5 sessions)")
            self.price = p
            return
        if i.expiry and i.expiry <= m.asof:
            self.reasons.append(f"expired on {i.expiry}")
        T = self._T()
        r, rd, rsrc = m.short_rate("USD")
        self.inputs["rate"] = {"value": r, "date": rd, "source": rsrc}
        if r is None:
            self.reasons.append("no current USD short rate")
            return
        if i.type == "FUTURE":
            kind = i.spec["kind"]
            if kind == "equity_index":
                S, sd = m.close(i.underlying)
                q = m.div_yield(i.spec["proxy"])
                self.inputs.update({"spot": {"value": S, "date": sd, "source": f"{i.underlying} close"},
                                    "dividend_yield": {"value": q, "source": f"{i.spec['proxy']} trailing 12-month dividends"}})
                if S is None:
                    self.reasons.append(f"no current {i.underlying} level")
                    return
                self.price = px.equity_future(S, r, q, T)
                self.notes.append("fair value S·e^{(r−q)T}, not a futures quote")
            elif kind == "treasury":
                c = m.curve()
                if c is None:
                    self.reasons.append("no current Treasury curve")
                    return
                tf = px.treasury_future(c, i.spec["ctd_years"], T, i.spec["face"])
                self.price = tf["price"]
                self.inputs["treasury"] = tf
                self.notes.append("6% notional coupon at the approximate CTD maturity on the CMT curve; no conversion factor or delivery option")
            elif kind == "fx":
                S = self._fx_spot_usd_per(i.spec["ccy"])
                rf, fd, fsrc = m.short_rate(i.spec["ccy"])
                self.inputs.update({"spot": {"value": S, "source": i.spec["pair"]}, "foreign_rate": {"value": rf, "date": fd, "source": fsrc}})
                if S is None or rf is None:
                    self.reasons.append(f"no current {i.spec['ccy']} spot or short rate ({fsrc})")
                    return
                self.price = px.fx_forward(S, r, rf, T)
                self.notes.append("covered interest parity")
            else:
                self.reasons.append(PRODUCT_TYPE[i.product_type]["reason"])
                S, _ = m.close(i.underlying)
                self.price = S                  # for analysis only
            return
        if i.type == "OPTION":
            S, sd = m.close(i.underlying)
            q = m.div_yield(i.underlying)
            vol, vsrc, vd = m.implied_vol(i.underlying, T)
            self.inputs.update({"spot": {"value": S, "date": sd, "source": f"{i.underlying} close"},
                                "dividend_yield": {"value": q, "source": "trailing 12-month dividends"},
                                "implied_vol": {"value": vol, "date": vd, "source": vsrc}})
            if S is None:
                self.reasons.append(f"no current {i.underlying} price")
                return
            if vol is None:
                self.reasons.append(vsrc)
                return
            g = px.option(i.right, S, i.strike, T, r, q, vol, i.style)
            self.greeks = g
            self.price = g["price"]
            self.notes.append(f"{'Black-Scholes-Merton' if i.style == 'EUROPEAN' else 'CRR binomial (American)'}; implied vol {vol:.1%} from {vsrc}, flat across strikes (no skew data)")
            return
        if i.type == "FORWARD":
            base, quote = i.spec["base"], i.spec["quote"]
            S, sd = m.close(i.underlying)
            rb, _, bs = m.short_rate(base)
            rq, _, qs = m.short_rate(quote)
            self.inputs.update({"spot": {"value": S, "date": sd}, "base_rate": {"value": rb, "source": bs}, "quote_rate": {"value": rq, "source": qs}})
            if S is None or rb is None or rq is None:
                self.reasons.append(f"no current {i.underlying} spot or {base}/{quote} short rate")
                return
            self.price = px.fx_forward(S, rq, rb, T)
            self.notes.append("covered interest parity")

    def _fx_spot_usd_per(self, ccy: str) -> Optional[float]:
        return self.m.fx(ccy)

    # -------------------------------------------------------------- value and notional per unit (USD)
    def unit_value(self) -> Optional[float]:
        """Cash value of one unit today (0 for futures and forwards at inception)."""
        i = self.inst
        if self.price is None:
            return None
        if i.type == "SPOT":
            fx = self.m.fx(i.currency)
            a = self.m.store.asset(i.id) or {}
            if a.get("asset_class") == "FX" and (a.get("meta") or {}).get("base_currency", i.id[:3]) == "USD":
                return 1.0                       # one dollar of a dollar-first pair
            return self.price * (fx or 0.0) if fx else None
        if i.type == "OPTION":
            return self.price * i.multiplier
        return 0.0

    def unit_notional(self) -> Optional[float]:
        """Underlying exposure of one unit in USD: price × multiplier (futures), contract size × price (FX futures),
        S × 100 (options; delta scales it), base-currency units × USD rate (forwards)."""
        i = self.inst
        if self.price is None:
            return None
        if i.type == "SPOT":
            return self.unit_value()
        if i.type == "FUTURE":
            if i.spec["kind"] == "treasury":
                return self.price / 100.0 * i.spec["face"]
            return self.price * i.multiplier
        if i.type == "OPTION":
            S = self.inputs["spot"]["value"]
            return S * i.multiplier
        if i.type == "FORWARD":
            base = i.spec["base"]
            return self.m.fx(base) or None
        return None

    # -------------------------------------------------------------- risk per unit
    def exposures(self) -> Dict[str, float]:
        i, rk = self.inst, self.risk
        if rk is None or self.price is None:
            return {}
        if i.type == "SPOT":
            uv = self.unit_value()
            a = self.m.store.asset(i.id) or {}
            if a.get("asset_class") == "FX":
                meta = a.get("meta") or {}
                if (meta.get("base_currency") or i.id[:3]) == "USD":
                    return {f"FX:{i.currency}": -1.0}              # $1 long dollars = −$1 of the quote currency
            e = rk.unit_exposures(i.id)["exposures"]
            return {f: v * uv for f, v in e.items()} if uv else {}
        if i.type == "FUTURE":
            kind = i.spec["kind"]
            if kind == "equity_index":
                n = self.unit_notional()
                e = rk.unit_exposures(i.underlying)["exposures"]
                return {f: v * n for f, v in e.items() if not f.startswith("IDIO:")} | ({f"IDIO:{i.underlying}": n} if f"IDIO:{i.underlying}" in e else {})
            if kind == "treasury":
                from .risk import KRD
                dv = self.inputs["treasury"]["dv01"]
                yrs = self.inputs["treasury"]["ctd_years"]
                return {f: -dv * w for f, w in maturity_weights(yrs).items()}
            if kind == "fx":
                return {f"FX:{i.spec['ccy']}": self.unit_notional()}
            return {}
        if i.type == "OPTION":
            g = self.greeks or {}
            S = self.inputs["spot"]["value"]
            delta_usd = g.get("delta", 0.0) * S * i.multiplier
            e = rk.unit_exposures(i.underlying)["exposures"]
            out = {f: v * delta_usd for f, v in e.items()}
            vol = self.inputs["implied_vol"]["value"]
            vix, _ = self.m.macro("VIXCLS")
            beta_iv = (vol * 100.0 / vix) if vix else 1.0          # the option's implied vol moves with the VIX in proportion
            out["VOL"] = out.get("VOL", 0.0) + g.get("vega", 0.0) * i.multiplier * beta_iv
            return out
        if i.type == "FORWARD":
            base, quote = i.spec["base"], i.spec["quote"]
            if base == "USD":
                return {f"FX:{quote}": -1.0}
            return {f"FX:{base}": self.m.fx(base) or 0.0}
        return {}

    def greeks_per_unit(self) -> Optional[dict]:
        """Option Greeks per contract in dollars: delta-$ (per 100% move of the underlying), gamma-$ per (1% move)²,
        vega-$ per vol point, theta-$ per calendar day, rho-$ per bp."""
        if self.inst.type != "OPTION" or not self.greeks:
            return None
        g, mlt = self.greeks, self.inst.multiplier
        S = self.inputs["spot"]["value"]
        return {"delta": g["delta"], "delta_usd": g["delta"] * S * mlt, "gamma_usd_1pct": 0.5 * g["gamma"] * (0.01 * S) ** 2 * mlt,
                "gamma": g["gamma"], "vega_usd": g["vega"] * mlt, "theta_usd": g["theta"] * mlt, "rho_usd": g["rho"] * mlt,
                "premium": g["price"] * mlt, "iv": g.get("iv")}

    def profile(self) -> ProductRiskProfile:
        """The ProductRiskProfile of ONE unit (unsupported fields stay None)."""
        i = self.inst
        e = self.exposures()
        prof = ProductRiskProfile(i.product_type, i.underlying, i.currency, self.unit_value(), self.unit_notional())
        if not e:
            return prof
        n = self.unit_notional() or 0.0
        if "MKT" in e and n:
            prof.equity_beta = e["MKT"] / n if i.type != "OPTION" else e["MKT"] / (self.greeks_per_unit()["delta_usd"] or 1.0)
        prof.factor_betas = {f: (v / n if n else None) for f, v in e.items() if f.startswith(("SEC:", "IND:", "STY:"))} or None
        rates = {f: -v for f, v in e.items() if f.startswith("RATE:")}
        if rates:
            prof.key_rate_DV01 = rates
            prof.DV01 = sum(rates.values())
            if n and i.type == "SPOT":
                prof.duration = prof.DV01 / n * 1e4
        if "REAL:10Y" in e:
            prof.DV01 = (prof.DV01 or 0.0) - e["REAL:10Y"]
            prof.inflation_exposure = e["REAL:10Y"]      # gains when real yields fall relative to nominal
        cs = {f: -v for f, v in e.items() if f.startswith("CREDIT:")}
        if cs:
            prof.CS01 = sum(cs.values())
            if n:
                prof.spread_duration = prof.CS01 / n * 1e4
        fx = {f[3:]: v for f, v in e.items() if f.startswith("FX:")}
        prof.FX_exposure = fx or None
        cm = {f[4:]: v for f, v in e.items() if f.startswith("CMD:")}
        prof.commodity_exposure = cm or None
        prof.crypto_exposure = e.get("CRYPTO")
        prof.volatility_exposure = e.get("VOL")
        a = self.m.store.asset(i.underlying) or {}
        prof.convexity = a.get("convexity") if i.type == "SPOT" else None
        g = self.greeks_per_unit()
        if g:
            prof.delta, prof.gamma, prof.vega, prof.theta, prof.rho = g["delta_usd"], g["gamma_usd_1pct"], g["vega_usd"], g["theta_usd"], g["rho_usd"]
        prof.liquidity = self.liquidity()
        c = self.costs(21, "BUY")
        prof.carry = (c.get("info") or {}).get("carry_in_price") or (c.get("info") or {}).get("forward_points")
        prof.borrow_cost = (GC_BORROW * (self.unit_value() or 0.0)) if i.type == "SPOT" else None
        prof.financing = self.costs(21, "SELL").get("short_financing")
        return prof

    # -------------------------------------------------------------- costs, liquidity
    def costs(self, days: int, side: str) -> Dict[str, Optional[float]]:
        """Expected dollar cost of holding ONE unit for `days` sessions, opened on `side` (BUY, or SELL = short for spot),
        entry and exit included. Positive = cost. Only FRICTIONS count: the expected P&L relative to a frictionless,
        fair-value hedge with the same exposure, with idle cash earning the 3-month bill rate. So the financing a future's
        price embeds, the forward points of a forward and the dividends a short owes (offset by the ex-date price drop)
        are listed under `info` and are not costs; the risk premium given up is the same for every hedge of the risk.
        total = spread + commission + borrow + short financing + volatility premium + structural drift + volatility drag."""
        i, m = self.inst, self.m
        out: Dict[str, Optional[float]] = {}
        info: Dict[str, Optional[float]] = {}
        if self.price is None:
            return {"total": None}
        yrs = days / 252.0
        short = side.upper() in ("SELL", "SHORT")
        r = (self.inputs.get("rate") or {}).get("value")
        if r is None:
            r, _, _ = m.short_rate("USD")
        r = r or 0.0
        if i.type == "SPOT":
            uv = self.unit_value() or 0.0
            sp, _ = m.spread(i.id)
            out["spread"] = (sp or 0.0005) * uv                  # half the spread in and half out = one full spread
            out["commission"] = 0.0
            if short:
                a = m.store.asset(i.id) or {}
                if a.get("asset_class") == "CRYPTO":
                    out["borrow"] = None
                else:
                    out["borrow"] = GC_BORROW * yrs * uv
                    out["short_financing"] = r * yrs * uv          # retail short proceeds earn no rebate
                    info["dividends_owed"] = m.div_yield(i.id) * yrs * uv
            dr = self.structural_drift()
            if dr is not None:
                out["structural_drift"] = (-dr if not short else dr) * yrs * uv
            lev = ((m.store.asset(i.id) or {}).get("meta") or {}).get("leverage")
            if lev and lev != 1:
                v = m.realized_vol(i.id) or 0.0
                drag = lev * (lev - 1) / 2 * (v / abs(lev)) ** 2 * yrs * uv
                out["volatility_drag"] = drag if not short else -drag
        elif i.type == "FUTURE":
            tick = i.spec.get("tick", 0.0)
            unit = i.spec["face"] / 100.0 if i.spec["kind"] == "treasury" else (i.multiplier if i.spec["kind"] != "fx" else i.spec["size"])
            rolls = max(0, math.ceil((days / 252.0 * 365 - max(0.0, (_dt.date.fromisoformat(i.roll) - _dt.date.fromisoformat(m.asof)).days)) / 91)) if i.roll else 0
            out["spread"] = tick * unit * (1 + rolls)
            out["commission"] = COMMISSION["FUTURE"] * 2 * (1 + rolls)
            out["rolls"] = rolls
            if i.spec["kind"] == "equity_index":
                S = self.inputs["spot"]["value"]
                q = self.inputs["dividend_yield"]["value"]
                info["carry_in_price"] = (r - q) * yrs * S * i.multiplier * (1 if not short else -1)
            elif i.spec["kind"] == "treasury":
                cpp = self.inputs["treasury"]["carry_per_100"] / max(self._T(), 1 / 365) * yrs * i.spec["face"] / 100.0
                info["carry_in_price"] = cpp if short else -cpp
            elif i.spec["kind"] == "fx":
                rb = self.inputs["foreign_rate"]["value"]
                info["carry_in_price"] = (r - rb) * yrs * (self.unit_notional() or 0.0) * (1 if not short else -1)
        elif i.type == "OPTION":
            g = self.greeks or {}
            prem = g.get("price", 0.0) * i.multiplier
            kind = "index" if i.underlying in INDEX_OPTION_UNDERLYINGS else "etf" if (m.store.asset(i.underlying) or {}).get("asset_class") == "ETF" else "stock"
            info["premium"] = prem if not short else -prem
            out["spread"] = max(0.05 * i.multiplier, OPTION_SPREAD[kind] * prem)
            out["commission"] = COMMISSION["OPTION"] * 2
            info["theta_if_unchanged"] = -(g.get("theta", 0.0)) * i.multiplier * days * 365 / 252 * (1 if not short else -1)
            # the expected loss beyond fair value: premium at implied vol minus the value at the forecast (realised) vol
            fv, lab = m.vol_forecast(i.underlying)
            if fv:
                S = self.inputs["spot"]["value"]
                T = self._T()
                real = px.option_price(i.right, S, i.strike, T, r, self.inputs["dividend_yield"]["value"], fv, i.style) * i.multiplier
                gap = (prem - real) * min(1.0, (days / 252.0) / max(T, 1e-6))
                out["volatility_premium"] = gap if not short else -gap
                info["forecast_vol"] = fv
                info["forecast_vol_source"] = lab
        elif i.type == "FORWARD":
            n = self.unit_notional() or 0.0
            out["spread"] = FX_FORWARD_SPREAD * n
            rq, rb = self.inputs["quote_rate"]["value"], self.inputs["base_rate"]["value"]
            info["forward_points"] = (rq - rb) * yrs * n * (1 if not short else -1)
        out["total"] = sum(v for k, v in out.items() if k != "rolls" and isinstance(v, (int, float)))
        out["info"] = info
        return out

    DRIFT_TYPES = {"etn", "inverse_etf", "leveraged_etf", "commodity_etf", "crypto_futures_etf", "currency_fund"}

    def structural_drift(self) -> Optional[float]:
        """Annual return a fund loses (or gains) beyond its factor exposures — futures roll (contango), daily-reset
        decay, fees — measured as the mean residual return over the trailing 3 years; used only when |t| ≥ 2 and only
        for ETNs, leveraged/inverse and futures-based funds. Positive = the fund drifts up."""
        i = self.inst
        if i.type != "SPOT" or i.product_type not in self.DRIFT_TYPES and i.id not in ("USO", "UNG", "DBC", "DBA", "CPER", "VXX", "BITO"):
            return None
        if self.risk is None:
            return None
        self.risk.unit_exposures(i.id)
        res = self.risk._idio.get(i.id) or []
        xs = [x for x in res[max(1, self.m.i - 755):self.m.i + 1] if x is not None]
        if len(xs) < 250:
            return None
        mu = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))
        t = mu / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0
        if abs(t) < 2:
            return None
        # a fully funded fund earns the bill rate on (1 − β) of its value relative to the total-return factors
        # (an inverse fund holds cash AND receives financing on its short swap: 1 − (−1) = 2), so only the drift
        # beyond that is friction (fees, futures roll, daily-reset decay)
        beta = self.risk.unit_exposures(i.id)["exposures"].get("MKT", 0.0)
        r, _, _ = self.m.short_rate("USD")
        return mu * 252 - (1 - beta) * (r or 0.0)

    def liquidity(self) -> dict:
        i, m = self.inst, self.m
        proxy = i.id if i.type == "SPOT" else (i.spec.get("proxy") if i.type == "FUTURE" else i.underlying)
        if i.type == "FUTURE" and i.spec.get("kind") == "treasury":
            proxy = "IEF"
        adv = m.adv_usd(proxy) if proxy else None
        sp, src = m.spread(proxy) if proxy and m.store.asset(proxy) else (None, "")
        return {"adv_usd": adv, "adv_source": (f"{proxy} volume" + ("" if proxy == i.id else " (proxy: no futures/options volume or open-interest data)")) if adv else "no volume data",
                "spread": sp, "spread_source": src}

    # -------------------------------------------------------------- eligibility
    def eligibility(self) -> Tuple[bool, List[str]]:
        i = self.inst
        pt = PRODUCT_TYPE.get(i.product_type, {})
        reasons = list(self.reasons)
        status = pt.get("status")
        if status in ("NOT_SUPPORTED", "ANALYSIS_ONLY"):
            reasons.append(pt.get("reason") or f"{pt.get('name', i.product_type)} is {status.lower().replace('_', ' ')}")
        a = self.m.store.asset(i.underlying) if i.underlying else None
        if i.type == "SPOT" and a and a.get("asset_class") == "INDEX":
            reasons.append("an index level is not investable: use its futures, options or an ETF")
        if i.type == "SPOT" and a and (a.get("meta") or {}).get("synthetic"):
            reasons.append("a synthetic total-return index built from FRED yields: not a tradeable security")
        if i.type == "SPOT" and a and a.get("asset_class") == "COMMODITY":
            reasons.append(PRODUCT_TYPE["commodity_future"]["reason"])
        if self.risk is not None and self.price is not None and not self.exposures():
            reasons.append("no risk model for this product")
        # de-duplicate, keep order
        seen, out = set(), []
        for r in reasons:
            if r and r not in seen:
                seen.add(r); out.append(r)
        return (not out), out

    def sizing_rule(self) -> str:
        i = self.inst
        if i.type == "OPTION":
            return "contracts = target delta-$ ÷ (underlying × 100 × |Δ| × β)"
        if i.type == "FUTURE":
            return {"equity_index": "contracts = target beta-$ ÷ (futures price × multiplier × β)", "treasury": "contracts = target DV01 ÷ DV01 per contract",
                    "fx": "contracts = currency $ ÷ (contract size × USD price)"}.get(i.spec["kind"], "contracts = $ ÷ (price × multiplier)")
        if i.type == "FORWARD":
            return "notional = currency exposure to remove"
        return PRODUCT_TYPE.get(i.product_type, {}).get("sizing", "units = target exposure ÷ exposure per unit")

    def risk_unit(self) -> str:
        i = self.inst
        if i.type == "OPTION":
            return "delta-adjusted notional (+ gamma, vega, theta)"
        if i.type == "FUTURE":
            return {"equity_index": "contract notional × β", "treasury": "DV01", "fx": "currency notional"}.get(i.spec["kind"], "notional")
        if i.type == "FORWARD":
            return "currency notional"
        return PRODUCT_TYPE.get(i.product_type, {}).get("risk_units", "market value")


def maturity_weights(years: float) -> Dict[str, float]:
    """Split a cash flow at `years` between the 2/5/10/30-year key rates by maturity."""
    pts = [("RATE:2Y", 2.0), ("RATE:5Y", 5.0), ("RATE:10Y", 10.0), ("RATE:30Y", 30.0)]
    if years <= pts[0][1]:
        return {pts[0][0]: 1.0}
    for (f0, t0), (f1, t1) in zip(pts, pts[1:]):
        if years <= t1:
            w = (years - t0) / (t1 - t0)
            return {f0: 1 - w, f1: w}
    return {pts[-1][0]: 1.0}


def registry_table() -> List[dict]:
    return [dict(p) for p in PRODUCT_TYPES]
