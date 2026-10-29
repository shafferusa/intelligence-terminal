"""Risk view and risk warnings — inform only (TERMINAL_DESIGN.md §14, §2.12 ``risk.py``; TERMINAL_DECISIONS §4 "risk only warns").

Conventions
- Every figure ties to ``accounting.positions`` on ONE mark snapshot: ``report`` takes the snapshot once and passes it
  to every read model it calls (positions, NAV, liquidity, obligations, buying power, counterparties), so the Risk page,
  Portfolio and Accounting show the same marks.  Nothing here writes, nothing here fetches, nothing here blocks.
- Exposure of a position (USD base, signed): securities and bonds at market value; private shares and private loans at
  their mark, or AT COST when they have none ("at cost (no mark)": NAV carries an unpriced position at its cost, review
  MONEY-5 / SR-03); futures at notional (qty × mark × multiplier); listed options at delta-USD (qty × multiplier × δ × S)
  when a delta is known, else at market value (noted on the row); TRS at ± notional (the underlying exposure).  IRS,
  cross-currency swaps, the PB OTC options / forwards and the FX-desk forwards (the USD amount exchanged; their two legs
  in the exposure by currency) are shown in their own bucket at notional but are NOT in the headline long / short /
  gross / net: their measures are DV01, FX and the OTC block.  ``exposures`` (by bucket),
  ``exposure_by_class``, ``exposure_by_account`` and ``exposure_by_currency`` all sum the same per-position rows.
- % of NAV: against the scope's NAV; for a single-account scope the bucket, concentration and private percentages are
  against the BOOK's NAV (BM-06: a position's concentration is judged at book level, never against one account), while
  the leverage multiples of that scope use the account's own NAV (``pct_basis`` says which).
- Leverage (owner 2026-10-21: "calculate gross notional against nav too ... prob going to leverage w TRS"):
  ``gross_leverage`` = gross exposure / NAV (the headline gross above); ``gross_notional_leverage`` = gross NOTIONAL / NAV
  where every position counts at its full notional: securities, bonds and private positions at |market value| (or cost
  without a mark), futures at |qty × multiplier × price|, listed options at the FULL underlying notional |qty × multiplier
  × S|, TRS at the underlying notional, IRS / cross-currency swaps / OTC options and forwards at their notional;
  ``long_pct_nav`` / ``short_pct_nav`` / ``net_pct_nav`` = long, short and net exposure / NAV; the same per account
  (``leverage_by_account``, against the account's NAV), per prime-broker account (``financing_liquidity.pb``) and per book
  (``by_book``).  ``trs_notional_c`` / ``trs_pct_nav`` = the TRS underlying notional / NAV.  Warnings stay warnings.
- Greeks per contract come from the Cboe chain row of the contract when a chain is cached (δ, γ, θ per calendar day,
  vega per vol point, as Cboe reports them), else from ``finsim.engines.options_pricing.greeks`` on the mark's model
  inputs, else they are unavailable (listed under ``greeks.unavailable``, never approximated).  Totals, each × contracts
  × multiplier and signed: delta in shares and in USD (δ × S), gamma in USD (the change of delta-USD for a 1 % move,
  γ × S² / 100), vega USD per vol point, theta USD per calendar day.  ``delta`` is the book's EQUITY delta in USD
  (stocks / ETFs at market value + equity-index futures at notional + TRS at the underlying notional + listed options at
  δ × S; BM-27), with the other futures (rates, energy, metals, grains, FX) shown apart; ``delta_by_underlying`` splits it.
- DV01 is the P&L for +1 bp (signed, ``dv01_signed``): bonds by a full reprice of the dirty price at +1 bp of the
  bond's own yield (``services.bonds.dv01_c``, C18); rates futures ZT / ZF / ZN / ZB = −qty × D × price × multiplier
  / 10 000 with D from an assumed duration table (``tables.FUT_DV01``, labelled); swaps as the accounting position
  carries it (``extra.dv01_c``, the PV change for +1 bp as booked).  The ``dv01`` tile is the magnitude; ``dv01_rows``
  carry the tenor and ``dv01_by_tenor`` the buckets.
- Scenarios re-price through the same functions as the marks: equities / ETFs / equity-index futures / TRS linear in
  the shock; options through ``marks.price_option`` on the mark's model inputs (S shocked, σ shifted; the base is the
  model price from the same inputs); rates: bonds fully re-priced at the shifted yield (``services.bonds.reprice_c``),
  futures and swaps DV01 × bp; FX ±5 % on the USD value of every non-USD exposure.  Private marks and private loans
  are never shocked.  One plain sentence per scenario group (``scenario_notes``); a position that cannot be re-priced
  is listed under ``unpriced`` for that row.
- Warnings (ticket preview, Risk page, Attention strip) never block: concentration > 25 % of NAV, gross > 2 × NAV,
  option margin > 50 % of the account's NAV, stale or unpriced marks, currency loans > 50 % of Treasury capital, a
  futures maintenance shortfall, OTC VM posted > 10 % of the PB account's NAV, private / illiquid > 30 % of NAV.
- PORTFOLIO-17 (FINSIM1_PLAN P3 task 11): ``otc_exposure`` is THE definition of an OTC contract's exposure, shared with the Portfolio
  footer: a TRS at its reference units x the underlying price now (signed: paying the total return is short; at the contract
  notional when no underlying price is observed, said so), a CDX at its notional with its credit sign (protection bought = short
  credit), an IRS / cross-currency swap by its DV01 (its notional is never an exposure).  ``DEFINITIONS`` words market value vs
  exposure for both pages.
- PORTFOLIO-19: ``by_sector`` = the headline exposure per sector (the owner's classification, else the instrument master's, else
  "unclassified"; a TRS carries its underlying's sector); its gross sums to the headline gross.  The owner classifies a name with
  the light command ``instrument.classify {instrument_id, sector}`` (table ``instrument_sectors``, made by ``bind``; one
  SETTINGS_CHANGED event with before / after; an empty sector removes the owner's classification).
- Reg T (FINSIM1_PLAN P3 task 13): ``reg_t`` per account from P6's ``collateral.summary`` (C16) when that function is in the build -
  requirement, posted / eligible collateral, excess or deficit, open calls and the assumed terms as P6 states them - else
  ``available: false`` with the reason; nothing here computes a requirement of its own.
- V104R2-BW-08: an exposure bucket's ``headline`` is True exactly when its rows are in the headline long / short / gross (the FX
  currency buckets, IRS / cross-currency swaps, CDX and FX forwards are not).  P8B-08: that includes the prime broker's OTC FX
  forwards: their USD notional sits in 'FX forwards (notional)' with the FX desk's, and their two legs count in the currency
  exposure and the FX scenarios until delivery; OTC equity forwards stay in the headline (equity delta).
- FX_CCS_PLAN D14 / K7 (1.0.6.1): the currency exposure has ONE ledger part and ONE off-ledger part.  The ledger part comes only from
  ``_currency_balances`` (every non-USD asset and liability GL - 2350, 2360, 2340 ... - plus the FX desk's pending forwards) and the
  non-USD positions at market; the off-ledger part comes only from the product hooks ``risk_sensitivities`` of the OPEN OTC FX contracts
  (FX_FORWARD: both legs until delivery; XCCY: the exchanges not yet on the ledger and the clean mark's FX delta), so nothing is counted
  twice: by currency, nominal = ledger + hook nominal and PV delta = ledger balances + hook PV delta.  ``fx_sensitivities`` carries
  per currency the nominal and the PV delta with their two parts and the USD effect of a 1 % and a 5 % move, the pair scenarios (each
  USD pair with the other held; each traded cross with either leg held - re-priced linearly on the PV deltas, a cross's held leg moving
  its other currency by 1 / (1 + shock)), rate01 per currency, basis01 per pair, points01, the prime brokers' counterparty lines, the
  liquidity ladder (the contracts' scheduled flows and, when that module is in the build, the settlement-funding purchases and gaps)
  and the notional limits ("none configured").  OTC FX contracts (FX_FORWARD, XCCY) leave the USD DV01 rows and the rates scenario:
  their rate risk is per currency (OTC-09).  Every figure of the block is MODEL with its inputs' labels (PROXY / ASSUMED) as the hooks
  give them; a contract that cannot be re-priced now is named in ``notes`` and its currencies' PV deltas read as unavailable.
- JSON (``to_dict``): cents fields become decimal strings under the name without ``_c``; fractions (``pct_nav``,
  the leverage multiples) are 4-dp decimal strings; nothing is a float.  The preview hook is ``pretrade``
  (``orders.preview`` and ``private.preview`` call ``services.risk.pretrade``); ``warnings_for_preview`` is the same
  function under the work plan's name.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from finsim.engines import options_pricing as _op

from . import tables
from .books import split_account_id
from .clock import ny_of
from .instruments import EQUITY_KINDS
from .ledger import CHART, natural
from .marks import price_option, shared_reader
from .units import fmt_money, from_c, fx_c, mul_c, qprice, to_c

PCT4 = Decimal("0.0001")
ONE = Decimal(1)
EQUITY_SHOCKS = (Decimal("-0.20"), Decimal("-0.10"), Decimal("-0.05"), Decimal("0.05"), Decimal("0.10"), Decimal("0.20"))
RATE_SHOCKS_BP = (-100, -50, -25, 25, 50, 100)
VOL_SHOCKS = (Decimal("-0.05"), Decimal("0.05"))
FX_SHOCKS = (Decimal("-0.05"), Decimal("0.05"))
#: FX_CCS_PLAN K7: the pair moves of the FX block (signed fractions of the pair's price)
FX_PAIR_SHOCKS = (Decimal("0.01"), Decimal("-0.01"), Decimal("0.05"), Decimal("-0.05"))
#: the OTC FX contract kinds whose off-ledger risk comes from a product hook (D14), and the service that carries the hook
FX_HOOK_MODULES = {"FX_FORWARD": "otc_options", "XCCY": "swaps_trs"}
FX_OTC_KINDS = tuple(FX_HOOK_MODULES)
CAPS_TEXT = "notional limits: none configured in the prime-broker schedules; compliance has no OTC limit"
FX_RATE_RISK = "rate risk per currency in the FX block (rate01), not a USD DV01"
THRESHOLDS = {"CONCENTRATION": Decimal("0.25"), "LEVERAGE": Decimal("2"), "OPTION_MARGIN": Decimal("0.50"), "LOANS": Decimal("0.50"),
              "OTC_VM": Decimal("0.10"), "PRIVATE_ILLIQUID": Decimal("0.30")}
FUTURES_BUCKET = {"CME_EQUITY_INDEX": "Index futures (notional)", "CME_RATES": "Rates futures (notional)", "NYMEX_ENERGY": "Energy futures (notional)",
                  "COMEX_METALS": "Metals futures (notional)", "CBOT_GRAINS": "Grain futures (notional)", "CME_FX": "FX futures (notional)"}
BUCKET_ORDER = ["US equities", "Bonds", "Index futures (notional)", "Rates futures (notional)", "Energy futures (notional)", "Metals futures (notional)",
                "Grain futures (notional)", "FX futures (notional)", "Listed options (delta USD)", "TRS (underlying exposure)", "Credit derivatives (CDX notional)", "OTC swaps (notional)",
                "OTC options and forwards", "FX forwards (notional)", "Private / illiquid", "Private credit"]
COST_GLS = ("1100", "1110", "1120", "1130", "1700", "2600", "2900")
NOTE = "warnings only - this page informs; funding, borrow and recall mechanics still apply"
PRIVATE_NOTE = "private marks and private loans are not shocked (last-round / model marks)"
AT_COST = "at cost (no mark)"
SEVERITY_WARN = "WARN"
SEVERITY_INFO = "INFO"
#: the nominal tenor of a rates future (years): the 2-, 5-, 10- and 30-year Treasury contracts
FUT_TENOR = {"ZT": Decimal(2), "ZF": Decimal(5), "ZN": Decimal(10), "TN": Decimal(10), "ZB": Decimal(30), "UB": Decimal(30)}
TENOR_BUCKETS = (("0-2y", Decimal(2)), ("2-5y", Decimal(5)), ("5-10y", Decimal(10)), ("10-30y", Decimal(30)), ("30y+", None))
OTC_KIND_LABEL = {"IRS": "IRS", "TRS": "TRS", "XCCY": "XCCY", "EQ_OPTION": "OTC equity option", "FX_OPTION": "OTC FX option", "EQ_FORWARD": "Equity forward",
                  "FX_FORWARD": "FX forward", "CDX": "CDX"}
#: one plain sentence per scenario group (BM-28): shown once under the scenarios table, never repeated per row
SCENARIO_NOTES = [
    {"group": "equities", "title": "Equity moves",
     "text": "Stocks, ETFs, equity-index futures and TRS move one for one with the shock; listed options are re-priced through the option model at the "
             "shocked spot; " + PRIVATE_NOTE + "."},
    {"group": "rates", "title": "Rate moves",
     "text": "A parallel shift of yields: bonds are fully re-priced at the shifted yield (convexity included); rates futures move by their DV01 from an "
             "assumed duration table and swaps by their DV01 as booked; option rho is not included."},
    {"group": "vol", "title": "Volatility moves", "text": "Listed options are re-priced with their implied volatility moved by the points shown; nothing else changes."},
    {"group": "fx", "title": "Currency moves", "text": "Every non-USD cash balance and position is revalued at the shifted USD rate; USD positions do not move."}]
NOTE_OF = {n["group"]: n["text"] for n in SCENARIO_NOTES}
DEFINITIONS = {
    "gross_exposure": "Gross exposure / NAV: long + short headline exposure (stocks, ETFs, bonds and private positions at market value or cost without a "
                      "mark, futures at notional, listed options at delta x underlying price, TRS at the underlying notional; IRS, cross-currency swaps, "
                      "OTC options / forwards and FX-desk forwards are not in it) divided by NAV.",
    "gross_notional": "Gross notional / NAV: every position at its full notional - securities, bonds and private positions at market value (cost without a "
                      "mark), futures quantity x multiplier x price, listed options quantity x multiplier x underlying price, TRS at the underlying notional, "
                      "IRS / cross-currency swaps / OTC options and forwards / FX-desk forwards at their notional - divided by NAV.",
    "long": "Long / NAV: the long headline exposure divided by NAV.",
    "short": "Short / NAV: the short headline exposure (a positive amount) divided by NAV.",
    "net": "Net / NAV: long minus short headline exposure divided by NAV (signed).",
    "trs": "TRS notional / NAV: the underlying notional of every total return swap divided by NAV (signed: receiving the total return is long).",
    "delta": "Equity delta in USD: stocks and ETFs at market value, equity-index futures at notional, TRS at the underlying notional and listed options at "
             "delta x underlying price; other futures (rates, energy, metals, grains, FX) are shown apart at notional.",
    "dv01": "DV01: the P&L for a 1 bp rise in yields (signed: a long bond loses) - bonds by a full reprice, rates futures from an assumed duration table, "
            "swaps as booked.",
    # PORTFOLIO-17: market value vs exposure, the same words on the Portfolio footer and here
    "market_value": "Market value: what a position is worth in NAV - securities, bonds and options at price x quantity, an OTC contract at its contract "
                    "value (the model mark), a future at 0 (its gains and losses are paid daily as variation margin).",
    "exposure": "Exposure: the size of the market risk, never a value - futures at notional (contracts x multiplier x price), a TRS at its reference units "
                "x the underlying price, a CDX at its notional with its credit sign, an IRS by its DV01.",
    "trs_exposure": "TRS exposure: reference units x the underlying price now, signed (receiving the total return is long, paying it short); at the "
                    "contract notional (units x reset price) only when no underlying price is observed, said so.",
    "cdx": "CDX exposure: the credit notional with its sign - protection bought is short credit (negative), protection sold long credit; its risk "
           "measure is CS01 (the credit block).",
    "irs": "IRS / cross-currency swaps: measured by DV01 (the P&L for +1 bp of the swap curve), never by notional.",
    "futures": "Futures: notional exposure = contracts x multiplier x price; their market value is 0 (daily variation margin)."}


# ---- serialisation --------------------------------------------------------------------------------------------------------
def jsonable(x):
    """Cents (``*_c`` ints) -> decimal strings without the suffix; Decimal -> str; dataclasses / to_dict objects recurse."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if isinstance(k, str) and k.endswith("_c") and isinstance(v, int) and not isinstance(v, bool):
                out[k[:-2]] = str(from_c(v))
            elif isinstance(k, str) and k.endswith("_c") and v is None:
                out[k[:-2]] = None
            else:
                out[k] = jsonable(v)
        return out
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if hasattr(x, "to_dict"):
        return x.to_dict()
    return x


def _d(x) -> Decimal:
    """A model float (greek, iv) -> Decimal through ``repr`` (never arithmetic in float)."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x))


def _pct(part_c: int, nav_c: int) -> Optional[Decimal]:
    if not nav_c or nav_c <= 0:
        return None
    return (Decimal(abs(int(part_c))) / Decimal(nav_c)).quantize(PCT4)


def _pct_signed(part_c: int, nav_c: int) -> Optional[Decimal]:
    if not nav_c or nav_c <= 0:
        return None
    return (Decimal(int(part_c)) / Decimal(nav_c)).quantize(PCT4)


def _pct_text(x: Optional[Decimal]) -> str:
    return "n/a" if x is None else f"{(x * 100).quantize(Decimal('0.1'))} %"


def _ratios(long_c: int, short_c: int, gross_c: int, net_c: int, notional_c: int, nav_c: int) -> dict:
    """The leverage multiples of one scope / account / book against ``nav_c`` (None when that NAV is not positive)."""
    return {"gross_leverage": _pct(gross_c, nav_c), "gross_notional_leverage": _pct(notional_c, nav_c), "long_pct_nav": _pct(long_c, nav_c),
            "short_pct_nav": _pct(short_c, nav_c), "net_pct_nav": _pct_signed(net_c, nav_c)}


# ---- per-position risk row --------------------------------------------------------------------------------------------------
@dataclass
class PosRisk:
    """One position's risk inputs (every figure from ``accounting.positions``; base USD cents, signed)."""
    account_id: str
    instrument_id: str
    symbol: str
    name: str
    kind: str
    klass: str
    bucket: str
    currency: str
    qty: int
    rate: Optional[Decimal]
    mv_c: Optional[int]
    mv_base_c: Optional[int]
    notional_base_c: Optional[int]
    exposure_c: Optional[int]
    exposure_basis: str
    headline: bool
    quality: str
    mark: Optional[Decimal]
    underlying_id: Optional[str] = None
    underlying_mark: Optional[Decimal] = None
    multiplier: Decimal = ONE
    price_scale: Decimal = ONE
    session_group: str = ""
    greeks: Optional[dict] = None
    greeks_source: Optional[str] = None
    greeks_note: str = ""
    dv01_c: Optional[int] = None
    dv01_basis: str = ""
    inputs: Optional[dict] = None
    right: Optional[str] = None
    style: Optional[str] = None
    extra: dict = field(default_factory=dict)
    lock_until: Optional[str] = None
    lots: List[dict] = field(default_factory=list)
    label: str = ""
    tenor_years: Optional[Decimal] = None
    gross_notional_c: Optional[int] = None
    delta_c: Optional[int] = None
    cost_base_c: Optional[int] = None
    at_cost: bool = False
    sector: Optional[str] = None            # PORTFOLIO-19 (accounting.PositionView.sector; a TRS its underlying's)
    _p0: Optional[Decimal] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        return jsonable(d)


def _rate_of(snap, ccy: str) -> Optional[Decimal]:
    if ccy == "USD":
        return ONE
    v = snap.fx(ccy)
    if v is None or v.mark is None or v.quality == "unavailable":
        return None
    return v.mark


def _mark_of(snap, iid: Optional[str]) -> Optional[Decimal]:
    if not iid:
        return None
    v = snap.mark(iid)
    if v is None or v.mark is None or v.quality == "unavailable":
        return None
    return v.mark


def _chain_for(services, snap, uid: Optional[str]):
    """The cached chain of an underlying: the snapshot's (FrozenMarks) first, else ``services.chains.peek``; never a fetch."""
    if not uid:
        return None
    chain = None
    if hasattr(snap, "chain"):
        try:
            chain = snap.chain(uid)
        except Exception:   # noqa: BLE001 - a snapshot without chains is the same as no chain
            chain = None
    if chain is None:
        chains = getattr(services, "chains", None)
        if chains is not None and hasattr(chains, "peek"):
            try:
                chain = chains.peek(uid)
            except Exception:   # noqa: BLE001
                chain = None
    return chain


def _model_inputs(services, snap, inst, p, chain, now: int) -> Optional[dict]:
    """The option model inputs (S, K, T_days, r, q, sigma): the mark's own when it is a model mark, else from
    ``services.marks.option_model`` (the real MarkProvider) on the cached underlying record; None when unavailable."""
    inp = p.mark.inputs if p.mark is not None else None
    if isinstance(inp, dict) and all(k in inp for k in ("S", "K", "T_days", "r", "q", "sigma")):
        return inp
    marks = getattr(services, "marks", None)
    if marks is None or not hasattr(marks, "option_model") or inst is None:
        return None
    under_rec = snap.quote(inst.underlying_id) if hasattr(snap, "quote") and inst.underlying_id else None
    try:
        px, inp = marks.option_model(inst, under_rec, chain, now=now, snapshot=snap)
    except Exception:   # noqa: BLE001 - a failing model is "unpriced", disclosed on the scenario row
        return None
    if px is None or not isinstance(inp, dict) or not all(k in inp for k in ("S", "K", "T_days", "r", "q", "sigma")):
        return None
    return inp


def _option_price(r: PosRisk, s_mult: Decimal = ONE, dvol: Decimal = Decimal(0)) -> Decimal:
    """Price per share from the same model as the marks (``marks.price_option``) at S × s_mult and σ + dvol."""
    inp = r.inputs
    S = qprice(Decimal(str(inp["S"])) * s_mult)
    K = Decimal(str(inp["K"]))
    t_years = int(inp["T_days"]) / 365.0
    sigma = float(Decimal(str(inp["sigma"])) + dvol)
    sigma = max(1e-4, sigma)
    px, _model = price_option(r.right, r.style or "AMERICAN", S, K, t_years, float(Decimal(str(inp["r"]))), float(Decimal(str(inp["q"]))), sigma)
    return px


def _option_base_price(r: PosRisk) -> Decimal:
    if r._p0 is None:
        r._p0 = _option_price(r)
    return r._p0


def _greeks_from(row) -> Optional[dict]:
    if row is None or getattr(row, "delta", None) is None:
        return None
    return {"delta": _d(row.delta), "gamma": _d(row.gamma or 0.0), "theta": _d(row.theta or 0.0), "vega": _d(row.vega or 0.0), "iv": None if row.iv is None else _d(row.iv)}


def _greeks_model(r: PosRisk) -> Optional[dict]:
    inp = r.inputs
    if inp is None or r.right not in ("C", "P"):
        return None
    try:
        g = _op.greeks(r.right, "EUROPEAN" if r.style == "EUROPEAN" else "AMERICAN", float(Decimal(str(inp["S"]))), float(Decimal(str(inp["K"]))),
                       int(inp["T_days"]) / 365.0, float(Decimal(str(inp["r"]))), float(Decimal(str(inp["q"]))), float(Decimal(str(inp["sigma"]))))
    except Exception:   # noqa: BLE001 - no greeks rather than wrong greeks
        return None
    return {"delta": _d(g["delta"]), "gamma": _d(g["gamma"]), "theta": _d(g["theta"]), "vega": _d(g["vega"]), "iv": _d(g["iv"])}


def _years_to(iso: Optional[str], today: date) -> Optional[Decimal]:
    """Years from ``today`` to an ISO date (ACT/365.25, 2 dp); None without a date."""
    if not iso:
        return None
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return (Decimal(max(0, (d - today).days)) / Decimal("365.25")).quantize(Decimal("0.01"))


def _pct_str(x: Any) -> str:
    """A fraction (0.0401) as a short percent text ("4.01 %"); the input as given when it is not a number."""
    try:
        return f"{(Decimal(str(x)) * 100).quantize(Decimal('0.01'))} %"
    except Exception:   # noqa: BLE001
        return str(x)


def contract_label(inst, extra: Optional[dict] = None) -> str:
    """A readable label of an OTC contract (PO-18): "TRS AAPL", "IRS pay fixed 4.01 % to 2031-10-21", "XCCY receive EUR / pay USD",
    "OTC equity option AAPL" - from the contract row's kind and the instrument's spec; the instrument name when nothing better is known."""
    extra = dict(extra or {})
    spec = dict(getattr(inst, "spec", None) or {}) if inst is not None else {}
    kind = str(extra.get("kind") or spec.get("otc_kind") or "")
    name = str(getattr(inst, "name", "") or "")
    und = (getattr(inst, "underlying_id", None) if inst is not None else None) or spec.get("underlying_id") or spec.get("underlying")
    if kind == "TRS":
        side = "" if spec.get("receiver") is None else (" (receive)" if spec.get("receiver") else " (pay)")
        return f"TRS {und}{side}" if und else (name.split(" @ ")[0] if name else "TRS")
    if kind == "IRS":
        pay = spec.get("pay_fixed")
        side = "" if pay is None else (" pay fixed" if pay else " receive fixed")
        rate = f" {_pct_str(spec['fixed_rate'])}" if spec.get("fixed_rate") not in (None, "") else ""
        mat = spec.get("maturity_date") or extra.get("maturity_date")
        return f"IRS{side}{rate}" + (f" to {mat}" if mat else "")
    if kind == "XCCY":
        return name.split(",")[0] if name.startswith("XCCY") else "XCCY"
    if kind == "CDX":
        return f"{spec.get('index') or 'CDX'} S{spec.get('series') or '?'} 5Y {'sell' if spec.get('receiver') else 'buy'} protection"
    label = OTC_KIND_LABEL.get(kind, kind or "OTC")
    pair = spec.get("pair")
    what = und or pair
    return f"{label} {what}" if what else (name[:60] if name else label)


def otc_exposure(p, rate: Optional[Decimal] = None) -> dict:
    """PORTFOLIO-17: ``{kind, exposure_c, dv01_c, basis}`` of an OTC position row (``accounting.PositionView`` of kind SWAP) in base cents
    (``rate`` = USD per unit of the contract's currency; USD needs none): a TRS at its reference units x the underlying price now,
    signed by the row (a negative quantity = paying the total return = short), else at the contract notional when no underlying price is
    observed (``basis`` says which); a CDX at its notional with its credit sign (protection bought = short credit, negative); an IRS /
    cross-currency swap by its DV01 (``exposure_c`` None); an OTC option / forward at its signed notional.  Worked: TRS pay ORCL
    38,700 reference units with ORCL at 126.06 -> -4,878,522.00 (38,700 x 126.06)."""
    ex = dict(p.extra or {})
    kind = str(ex.get("kind") or "")
    if rate is None:
        rate = Decimal(1) if p.currency == "USD" else None
    sign = -1 if int(p.qty or 0) < 0 else 1

    def base(c):
        return None if c is None or rate is None else fx_c(int(c), rate)
    if kind == "TRS":
        units, s = ex.get("units"), ex.get("current")
        if units not in (None, "") and s not in (None, ""):
            try:
                v = base(to_c(Decimal(abs(int(units))) * Decimal(str(s))))
                if v is not None:
                    return {"kind": "TRS", "exposure_c": sign * abs(v), "dv01_c": None,
                            "basis": f"{abs(int(units)):,} reference units x {s} (the underlying price now)"}
            except Exception:   # noqa: BLE001 - an unreadable input falls back to the contract notional, said so
                pass
        v = base(p.notional_c)
        return {"kind": "TRS", "exposure_c": None if v is None else sign * abs(v), "dv01_c": None,
                "basis": "the contract notional (reference units x the reset price): no underlying price observed now"}
    if kind == "CDX":
        v = base(p.notional_c)
        return {"kind": "CDX", "exposure_c": None if v is None else sign * abs(v), "dv01_c": None,
                "basis": "credit notional: protection bought = short credit (negative), sold = long credit"}
    if kind in ("IRS", "XCCY"):
        dv = ex.get("dv01_c")
        if dv is None:
            dv = (ex.get("position_fields") or {}).get("extra", {}).get("dv01_c") if isinstance(ex.get("position_fields"), dict) else None
        return {"kind": kind, "exposure_c": None, "dv01_c": None if dv is None else int(dv),
                "basis": "DV01: the P&L for +1 bp of the swap curve (its notional is not an exposure)"}
    v = base(p.notional_c)
    return {"kind": kind or "OTC", "exposure_c": None if v is None else sign * abs(v), "dv01_c": None, "basis": "the contract's signed notional"}


def _pos_risk(services, snap, p, now: int) -> PosRisk:
    inst = services.instruments.get(p.instrument_id)
    rate = _rate_of(snap, p.currency)
    kind = p.kind
    mult = inst.multiplier if inst else ONE
    scale = inst.price_scale if inst else ONE
    group = inst.session_group if inst else ""
    mark = p.mark.mark if p.mark is not None else None
    extra = dict(p.extra or {})
    today = ny_of(int(now)).date()
    r = PosRisk(p.account_id, p.instrument_id, p.symbol, p.name, kind, p.klass, "", p.currency, int(p.qty), rate, p.mv_c, p.mv_base_c,
                None if p.notional_c is None or rate is None else fx_c(int(p.notional_c), rate), None, "", True, p.mark.quality if p.mark else "unavailable", mark,
                inst.underlying_id if inst else None, None, mult, scale, group, extra=extra,
                lots=[{"lot_id": l.lot_id, "qty_remaining": l.qty_remaining, "open_date": l.open_date, "lock_until": l.lock_until} for l in (p.lots or [])])
    r.label = p.symbol
    r.sector = getattr(p, "sector", None)
    r.lock_until = extra.get("lock_until") or (max((l.lock_until or "") for l in (p.lots or [])) or None if p.lots else None)
    if rate is not None and p.cost_c is not None:
        r.cost_base_c = fx_c(int(p.cost_c), rate)
    if kind in EQUITY_KINDS:
        r.bucket, r.exposure_c, r.exposure_basis = "US equities", p.mv_base_c, "market value"
        r.delta_c = p.mv_base_c
        r.gross_notional_c = None if p.mv_base_c is None else abs(p.mv_base_c)
    elif kind == "BOND":
        r.label = p.name or p.symbol                         # "US Treasury 4.625 % 15-Aug-2036 (91282CRF0)" rather than the bare CUSIP
        r.bucket, r.exposure_c, r.exposure_basis = "Bonds", p.mv_base_c, "market value"
        r.gross_notional_c = None if p.mv_base_c is None else abs(p.mv_base_c)
        r.tenor_years = _years_to(inst.expiry if inst else None, today)
        bonds = getattr(services, "bonds", None)
        fn = getattr(bonds, "dv01_c", None) if bonds is not None else None
        if callable(fn) and inst is not None:
            try:
                dv, inp = fn(services, inst, r.qty, snap)
            except Exception as e:   # noqa: BLE001 - an unpriced bond is named, never a guessed DV01
                dv, inp = None, {"note": f"DV01 unavailable: {type(e).__name__}: {e}"[:160]}
            inp = dict(inp or {})
            r.dv01_c = None if dv is None else int(dv)
            r.dv01_basis = ("full reprice of the dirty price for +1 bp of the bond's yield" if dv is not None
                            else str(inp.get("note") or inp.get("basis") or "DV01 unavailable"))
        else:
            r.dv01_basis = "DV01 unavailable: the bond engine is not loaded"
    elif kind == "FUTURE":
        r.bucket = FUTURES_BUCKET.get(group, "Futures (notional)")
        r.exposure_c, r.exposure_basis = r.notional_base_c, "notional"
        r.delta_c = r.notional_base_c
        r.gross_notional_c = None if r.notional_base_c is None else abs(r.notional_base_c)
        root = (inst.spec or {}).get("root") if inst else None
        dur = tables.FUT_DV01.get(root) if root else None
        if dur is not None:
            r.tenor_years = FUT_TENOR.get(root)
        if dur is not None and mark is not None and rate is not None:
            dv = to_c(Decimal(int(p.qty)) * dur * mark * mult / Decimal(10000))
            r.dv01_c, r.dv01_basis = -fx_c(dv, rate), f"approximate: {root} duration {dur} x price x multiplier / 10,000 per contract (assumed duration table)"
        elif dur is not None:
            r.dv01_basis = "no mark: DV01 unavailable"
    elif kind == "OPTION":
        r.bucket = "Listed options (delta USD)"
        r.right, r.style = (inst.right, inst.style) if inst else (None, None)
        r.underlying_mark = _mark_of(snap, r.underlying_id)
        chain = _chain_for(services, snap, r.underlying_id)
        row = chain.by_occ((inst.cboe_symbol if inst else None) or p.instrument_id) if chain is not None else None
        r.inputs = _model_inputs(services, snap, inst, p, chain, now)
        g = _greeks_from(row)
        if g is not None:
            r.greeks, r.greeks_source = g, "Cboe"
        else:
            g = _greeks_model(r)
            if g is not None:
                r.greeks, r.greeks_source = g, "model"
            else:
                r.greeks_note = "no chain row and no model inputs: greeks unavailable"
        S = r.underlying_mark if r.underlying_mark is not None else (Decimal(str(r.inputs["S"])) if r.inputs else None)
        n = Decimal(int(p.qty)) * mult
        if S is not None and rate is not None:
            r.gross_notional_c = abs(fx_c(to_c(n * S), rate))
        if r.greeks is not None and S is not None and rate is not None:
            r.exposure_c = fx_c(to_c(n * r.greeks["delta"] * S), rate)
            r.exposure_basis = f"delta USD ({r.greeks_source})"
            r.delta_c = r.exposure_c
        else:
            r.exposure_c, r.exposure_basis = p.mv_base_c, "market value (no delta available)"
    elif kind == "SWAP":
        swap_kind = extra.get("kind")
        sign = 1 if int(p.qty) >= 0 else -1
        r.label = contract_label(inst, extra)
        r.tenor_years = _years_to(extra.get("maturity_date"), today)
        r.gross_notional_c = None if r.notional_base_c is None else abs(r.notional_base_c)
        if swap_kind == "TRS":
            # PORTFOLIO-17: the same definition as the Portfolio footer (reference units x the underlying price now; else the notional)
            x = otc_exposure(p, rate)
            r.bucket, r.exposure_c, r.exposure_basis = "TRS (underlying exposure)", x["exposure_c"], x["basis"]
            r.delta_c = r.exposure_c
            r.gross_notional_c = None if r.exposure_c is None else abs(r.exposure_c)
        elif swap_kind == "CDX":
            r.bucket, r.headline = "Credit derivatives (CDX notional)", False
            r.exposure_c = None if r.notional_base_c is None else sign * abs(r.notional_base_c)
            r.exposure_basis = "credit notional: bought protection = short credit (not in the headline exposure; CS01 in the credit block)"
        else:
            r.bucket, r.headline = "OTC swaps (notional)", False
            r.exposure_c, r.exposure_basis = (None if r.notional_base_c is None else sign * abs(r.notional_base_c)), "notional (not in the headline exposure)"
        if swap_kind == "EQ_FORWARD":                           # an equity forward: its signed notional, in the headline (P12): equity delta
            r.bucket, r.headline = "OTC options and forwards", True
            r.exposure_c, r.exposure_basis = (None if r.notional_base_c is None else sign * abs(r.notional_base_c)), "forward notional"
        elif swap_kind == "FX_FORWARD":
            # P8B-08 / OTC-02: a PB OTC FX forward is treated like an FX-desk forward (V104R1-02): its USD notional in 'FX forwards (notional)',
            # outside the headline long / short; its risk is the currency exposure, where _currency_balances carries its two legs until delivery
            r.bucket, r.headline = "FX forwards (notional)", False
            r.exposure_c = None if r.notional_base_c is None else sign * abs(r.notional_base_c)
            r.exposure_basis = "forward notional in USD (not in the headline exposure: its risk is the currency exposure of its two legs)"
        elif swap_kind in ("EQ_OPTION", "FX_OPTION"):           # an OTC option: its value (no delta from a chain)
            r.bucket, r.headline = "OTC options and forwards", True
            r.exposure_c, r.exposure_basis = p.mv_base_c, "OTC option value"
        dv = extra.get("dv01_c")
        if swap_kind in FX_OTC_KINDS:
            # FX_CCS_PLAN D14 / OTC-09: an OTC FX contract's rate risk is two-currency - its rate01 per currency sits in the FX block, never in
            # the USD DV01 rows or the rates scenario (one USD DV01 had one currency and the wrong sign)
            r.dv01_c, r.dv01_basis = None, ""
            r.extra["rate_risk"] = FX_RATE_RISK
        elif dv is not None:
            r.dv01_c, r.dv01_basis = int(dv), "PV change for +1 bp of the swap curve, as booked"
        elif swap_kind == "IRS" or swap_kind == "XCCY":
            r.dv01_basis = "DV01 unavailable (no swap curve loaded)"
    elif kind == "FX_FORWARD":
        # V104R1-02: a forward dealt on the FX desk counts at its notional (the USD amount it exchanges; + = the pair's base bought),
        # never at its MTM: in gross notional and in its own bucket, outside the headline long / short like IRS / XCCY (its risk is
        # the currency exposure: _currency_balances already carries its two legs until the value date)
        r.label = p.symbol
        r.bucket, r.headline = "FX forwards (notional)", False
        r.exposure_c = r.notional_base_c
        r.exposure_basis = "forward notional in USD (" + str(extra.get("notional_basis") or "USD leg") + "; not in the headline exposure: its risk is the currency exposure)"
        r.gross_notional_c = None if r.notional_base_c is None else abs(r.notional_base_c)
    elif kind == "PRIVATE":
        r.bucket = "Private / illiquid"
        if p.mv_base_c is not None:
            r.exposure_c, r.exposure_basis = p.mv_base_c, "last-round mark (not shocked)"
        elif r.cost_base_c is not None:
            r.exposure_c, r.exposure_basis, r.at_cost = r.cost_base_c, AT_COST, True        # SR-03: NAV carries it at cost
        r.gross_notional_c = None if r.exposure_c is None else abs(r.exposure_c)
    elif kind == "PRIVATE_LOAN":
        r.bucket = "Private credit"
        if p.mv_base_c is not None:
            r.exposure_c, r.exposure_basis = p.mv_base_c, "model mark (not shocked)"
        elif r.cost_base_c is not None:
            r.exposure_c, r.exposure_basis, r.at_cost = r.cost_base_c, AT_COST, True
        r.gross_notional_c = None if r.exposure_c is None else abs(r.exposure_c)
        r.tenor_years = _years_to(extra.get("maturity_date"), today)
    else:
        r.bucket, r.exposure_c, r.exposure_basis = p.klass, p.mv_base_c, "market value"
        base = r.notional_base_c if r.notional_base_c is not None else p.mv_base_c
        r.gross_notional_c = None if base is None else abs(base)
    return r


# ---- aggregation helpers --------------------------------------------------------------------------------------------------
def _agg(rows: List[PosRisk], key) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows:
        if r.exposure_c is None:
            continue
        b = out.setdefault(key(r), {"long_c": 0, "short_c": 0, "net_c": 0, "gross_c": 0, "n": 0})
        if r.exposure_c >= 0:
            b["long_c"] += r.exposure_c
        else:
            b["short_c"] += -r.exposure_c
        b["net_c"] += r.exposure_c
        b["gross_c"] += abs(r.exposure_c)
        b["n"] += 1
    return out


def _greeks_total(rows: List[PosRisk], rate_fn) -> dict:
    """Totals per underlying and overall (see the module conventions for the units)."""
    by_u: Dict[str, dict] = {}
    unavailable: List[dict] = []
    for r in rows:
        if r.kind != "OPTION":
            continue
        S = r.underlying_mark if r.underlying_mark is not None else (Decimal(str(r.inputs["S"])) if r.inputs else None)
        if r.greeks is None or S is None or r.rate is None:
            unavailable.append({"instrument_id": r.instrument_id, "account_id": r.account_id, "reason": r.greeks_note or ("no underlying mark" if S is None else "no FX rate")})
            continue
        n = Decimal(r.qty) * r.multiplier
        u = by_u.setdefault(r.underlying_id or r.instrument_id, {"underlying": r.underlying_id or r.instrument_id, "contracts": 0, "delta_shares": Decimal(0),
                                                                   "delta_c": 0, "gamma_shares": Decimal(0), "gamma_c": 0, "vega_c": 0, "theta_c": 0, "sources": [],
                                                                   "underlying_mark": str(S)})
        u["contracts"] += r.qty
        d_sh = (n * r.greeks["delta"]).quantize(PCT4)
        g_sh = (n * r.greeks["gamma"]).quantize(PCT4)
        u["delta_shares"] += d_sh
        u["gamma_shares"] += g_sh
        u["delta_c"] += fx_c(to_c(n * r.greeks["delta"] * S), r.rate)
        u["gamma_c"] += fx_c(to_c(n * r.greeks["gamma"] * S * S / Decimal(100)), r.rate)
        u["vega_c"] += fx_c(to_c(n * r.greeks["vega"]), r.rate)
        u["theta_c"] += fx_c(to_c(n * r.greeks["theta"]), r.rate)
        if r.greeks_source not in u["sources"]:
            u["sources"].append(r.greeks_source)
    rows_out = sorted(by_u.values(), key=lambda u: -abs(u["delta_c"]))
    total = {"delta_shares": sum((u["delta_shares"] for u in rows_out), Decimal(0)), "delta_c": sum(u["delta_c"] for u in rows_out),
             "gamma_c": sum(u["gamma_c"] for u in rows_out), "vega_c": sum(u["vega_c"] for u in rows_out), "theta_c": sum(u["theta_c"] for u in rows_out),
             "unavailable": unavailable, "basis": "delta USD = contracts x multiplier x delta x S; gamma USD per 1 % move; vega per vol point; theta per calendar day"}
    return {"total": total, "rows": rows_out}


def _delta(rows: List[PosRisk], greek_rows: List[dict]) -> tuple:
    """BM-27: the book's equity delta in USD and its parts, plus the split by underlying (the option greeks merged in)."""
    parts = {"equities_c": 0, "index_futures_c": 0, "trs_c": 0, "options_c": 0, "other_futures_c": 0}
    missing: List[str] = []
    by_u: Dict[str, dict] = {}

    def slot(key: str) -> dict:
        return by_u.setdefault(key, {"underlying": key, "stock_c": 0, "options_c": 0, "futures_c": 0, "trs_c": 0, "total_c": 0, "contracts": 0,
                                     "delta_shares": Decimal(0), "gamma_c": 0, "vega_c": 0, "theta_c": 0})
    for r in rows:
        if r.kind in EQUITY_KINDS:
            part, key, col = "equities_c", r.instrument_id, "stock_c"
        elif r.kind == "FUTURE":
            if r.session_group != "CME_EQUITY_INDEX":
                if r.delta_c is not None:
                    parts["other_futures_c"] += r.delta_c
                continue
            part, key, col = "index_futures_c", r.underlying_id or r.instrument_id, "futures_c"
        elif r.kind == "SWAP" and r.extra.get("kind") == "TRS":
            part, key, col = "trs_c", r.underlying_id or r.instrument_id, "trs_c"
        elif r.kind == "OPTION":
            if r.delta_c is None:
                missing.append(r.instrument_id)
            continue                                     # the option greeks rows below carry the options' share
        else:
            continue
        if r.delta_c is None:
            missing.append(r.instrument_id)
            continue
        parts[part] += r.delta_c
        u = slot(key)
        u[col] += r.delta_c
        u["total_c"] += r.delta_c
    for g in greek_rows:
        u = slot(g["underlying"])
        u["options_c"] += int(g["delta_c"])
        u["total_c"] += int(g["delta_c"])
        u["contracts"] += int(g["contracts"])
        u["delta_shares"] += g["delta_shares"]
        for k in ("gamma_c", "vega_c", "theta_c"):
            u[k] += int(g[k])
        parts["options_c"] += int(g["delta_c"])
    total = parts["equities_c"] + parts["index_futures_c"] + parts["trs_c"] + parts["options_c"]
    out = dict(parts, total_c=total, missing=missing, basis=DEFINITIONS["delta"])
    return out, sorted(by_u.values(), key=lambda u: (-abs(u["total_c"]), u["underlying"]))


def _tenor_bucket(t: Optional[Decimal]) -> str:
    if t is None:
        return "n/a"
    for name, hi in TENOR_BUCKETS:
        if hi is None or t <= hi:
            return name
    return "n/a"


def _scenario_equity(rows: List[PosRisk], e: Decimal) -> dict:
    pnl = 0
    by_class: Dict[str, int] = {}
    unpriced: List[str] = []
    unchanged: List[str] = []
    for r in rows:
        v: Optional[int] = None
        if r.kind in EQUITY_KINDS:
            v = None if r.mv_base_c is None else to_c(from_c(r.mv_base_c) * e)
        elif r.kind == "FUTURE" and r.session_group == "CME_EQUITY_INDEX":
            v = None if r.notional_base_c is None else to_c(from_c(r.notional_base_c) * e)
        elif r.kind == "SWAP" and r.extra.get("kind") in ("TRS", "EQ_FORWARD"):
            v = None if r.exposure_c is None else to_c(from_c(r.exposure_c) * e)
        elif r.kind == "SWAP" and r.extra.get("kind") == "EQ_OPTION":
            v = None                                     # an OTC equity option is not re-priced by this shock: listed as unpriced
        elif r.kind == "OPTION":
            if r.inputs is not None and r.rate is not None and r.right in ("C", "P"):
                try:
                    v = fx_c(mul_c(r.qty, _option_price(r, s_mult=ONE + e) - _option_base_price(r), r.multiplier, r.price_scale), r.rate)
                except Exception:   # noqa: BLE001 - unpriced, listed
                    v = None
        else:
            unchanged.append(r.instrument_id)
            continue
        if v is None:
            unpriced.append(r.instrument_id)
            continue
        pnl += v
        by_class[r.klass] = by_class.get(r.klass, 0) + v
    return {"pnl_c": pnl, "by_class": by_class, "unpriced": unpriced, "unchanged": unchanged}


def _bond_reprice(services, snap, r: PosRisk, bp: int) -> Optional[int]:
    """C18: the P&L in USD cents of the bond position for a parallel move of ``bp`` (full reprice, convexity included)."""
    bonds = getattr(services, "bonds", None)
    fn = getattr(bonds, "reprice_c", None) if bonds is not None else None
    inst = services.instruments.get(r.instrument_id)
    if not callable(fn) or inst is None:
        return None
    try:
        v = fn(services, inst, r.qty, bp, snap)
    except Exception:   # noqa: BLE001 - unpriced, listed
        return None
    return None if v is None else int(v)


def _scenario_rates(rows: List[PosRisk], bp: int, services=None, snap=None) -> dict:
    pnl = 0
    by_class: Dict[str, int] = {}
    unpriced: List[str] = []
    for r in rows:
        if r.kind == "SWAP" and r.extra.get("kind") in FX_OTC_KINDS:
            continue                                                         # D14: per-currency rate01 in the FX block, not this USD shift
        if r.kind == "BOND":
            v = _bond_reprice(services, snap, r, bp) if services is not None else None
            if v is None and r.dv01_c is not None:
                v = r.dv01_c * bp                                            # DV01 x bp when the full reprice is not available
            if v is None:
                unpriced.append(r.instrument_id)
                continue
            pnl += v
            by_class[r.klass] = by_class.get(r.klass, 0) + v
        elif r.dv01_c is not None:
            v = r.dv01_c * bp
            pnl += v
            by_class[r.klass] = by_class.get(r.klass, 0) + v
        elif (r.kind == "SWAP" and r.extra.get("kind") in ("IRS", "XCCY")) or (r.kind == "FUTURE" and r.session_group == "CME_RATES"):
            unpriced.append(r.instrument_id)
    return {"pnl_c": pnl, "by_class": by_class, "unpriced": unpriced}


def _scenario_vol(rows: List[PosRisk], dvol: Decimal) -> dict:
    pnl = 0
    by_class: Dict[str, int] = {}
    unpriced: List[str] = []
    for r in rows:
        if r.kind != "OPTION":
            continue
        if r.inputs is None or r.rate is None or r.right not in ("C", "P"):
            unpriced.append(r.instrument_id)
            continue
        try:
            v = fx_c(mul_c(r.qty, _option_price(r, dvol=dvol) - _option_base_price(r), r.multiplier, r.price_scale), r.rate)
        except Exception:   # noqa: BLE001
            unpriced.append(r.instrument_id)
            continue
        pnl += v
        by_class[r.klass] = by_class.get(r.klass, 0) + v
    return {"pnl_c": pnl, "by_class": by_class, "unpriced": unpriced}


def _shock_text(x: Decimal, unit: str = "%") -> str:
    if unit == "bp":
        return f"{'+' if x > 0 else ''}{int(x)} bp"
    v = (x * 100).quantize(Decimal("1"))
    return f"{'+' if v > 0 else ''}{v} {unit}"


# ---- the report ---------------------------------------------------------------------------------------------------------------
@dataclass
class RiskView:
    scope: str
    as_of: int
    nav_c: int
    quality: str
    partial: bool
    missing: List[str]
    long_c: int
    short_c: int
    gross_c: int
    net_c: int
    dv01_c: int
    dv01_signed_c: int
    dv01_rows: List[dict]
    exposures: List[dict]
    exposure_by_class: Dict[str, dict]
    exposure_by_account: Dict[str, dict]
    exposure_by_currency: Dict[str, dict]
    fx_exposure: List[dict]
    greeks: dict
    greeks_by_underlying: List[dict]
    scenarios: List[dict]
    concentration: List[dict]
    financing_liquidity: dict
    margin: List[dict]
    private: dict
    otc: List[dict]
    warnings: List[dict]
    positions: List[PosRisk]
    excess_liquidity_c: int
    encumbrance_c: int
    leverage: Optional[Decimal]
    note: str = NOTE
    # Build 2 (P11): leverage against NAV (owner 2026-10-21), the equity delta, DV01 by tenor, scenario notes, counterparties, names
    book_nav_c: Optional[int] = None
    pct_basis: str = ""
    gross_leverage: Optional[Decimal] = None
    gross_notional_c: int = 0
    gross_notional_leverage: Optional[Decimal] = None
    long_pct_nav: Optional[Decimal] = None
    short_pct_nav: Optional[Decimal] = None
    net_pct_nav: Optional[Decimal] = None
    trs_notional_c: int = 0
    trs_pct_nav: Optional[Decimal] = None
    notional_missing: List[str] = field(default_factory=list)
    leverage_by_account: List[dict] = field(default_factory=list)
    by_book: List[dict] = field(default_factory=list)
    delta: dict = field(default_factory=dict)
    delta_by_underlying: List[dict] = field(default_factory=list)
    dv01_by_tenor: List[dict] = field(default_factory=list)
    scenario_notes: List[dict] = field(default_factory=list)
    counterparties: dict = field(default_factory=dict)
    account_names: Dict[str, str] = field(default_factory=dict)
    definitions: Dict[str, str] = field(default_factory=dict)
    credit_derivatives: Optional[dict] = None
    by_sector: List[dict] = field(default_factory=list)          # PORTFOLIO-19: the headline exposure per sector (sums to the headline gross)
    reg_t: dict = field(default_factory=dict)                    # task 13: per account from collateral.summary (C16), or why it is unavailable
    headline_note: str = ""                                      # V104R2-BW-08: which buckets add up to the gross tile
    fx_sensitivities: dict = field(default_factory=dict)         # FX_CCS_PLAN K7: the FX block (ledger part + the hooks' off-ledger part)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["positions"] = [p.to_dict() for p in self.positions]
        return jsonable(d)


def _accounts(services, ids: List[str]) -> Dict[str, dict]:
    if not ids:
        return {}
    q = ",".join("?" for _ in ids)
    with shared_reader(services.db) as conn:
        return {r["account_id"]: dict(r) for r in conn.execute(f"SELECT * FROM accounts WHERE account_id IN ({q})", list(ids)).fetchall()}


def _book_names(services, lifetime_ids: List[str]) -> Dict[str, dict]:
    if not lifetime_ids:
        return {}
    q = ",".join("?" for _ in lifetime_ids)
    with shared_reader(services.db) as conn:
        return {r["lifetime_id"]: {"book_code": r["book_code"], "name": r["bname"] or r["lname"]}
                for r in conn.execute(f"SELECT l.lifetime_id, l.book_code, l.name AS lname, b.name AS bname FROM lifetimes l LEFT JOIN books b ON b.book_code = l.book_code"
                                      f" WHERE l.lifetime_id IN ({q})", list(lifetime_ids)).fetchall()}


def _currency_balances(services, ids: List[str]) -> Dict[str, int]:
    """THE ledger part of the currency exposure (FX_CCS_PLAN D14): Σ (assets − liabilities) at cost per non-USD currency over the
    accounts, excluding the cost accounts that market values replace (storage sign is debit-positive, so a plain sum is assets −
    liabilities), plus the FX desk's pending forwards.  An OPEN PB OTC FX forward is not here: its two legs are off the ledger until
    delivery and come from its product hook (``_fx_hooks``), exactly once."""
    if not ids:
        return {}
    q = ",".join("?" for _ in ids)
    out: Dict[str, int] = {}
    with shared_reader(services.db) as conn:
        for r in conn.execute(f"SELECT gl, currency, SUM(balance_c) b FROM gl_balances WHERE account_id IN ({q}) AND currency <> 'USD' GROUP BY gl, currency", list(ids)).fetchall():
            spec = CHART.get(r["gl"])
            if spec is None or spec.kind not in ("ASSET", "LIABILITY") or r["gl"] in COST_GLS:
                continue
            out[r["currency"]] = out.get(r["currency"], 0) + int(r["b"] or 0)
        try:
            for f in conn.execute(f"SELECT buy_ccy, buy_c, sell_ccy, sell_c FROM fx_deals WHERE kind='FORWARD' AND status='PENDING' AND COALESCE(entry_id, 0)=0"
                                  f" AND account_id IN ({q})", list(ids)).fetchall():
                if f["buy_ccy"] and f["buy_ccy"] != "USD":
                    out[f["buy_ccy"]] = out.get(f["buy_ccy"], 0) + int(f["buy_c"] or 0)
                if f["sell_ccy"] and f["sell_ccy"] != "USD":
                    out[f["sell_ccy"]] = out.get(f["sell_ccy"], 0) - int(f["sell_c"] or 0)
        except sqlite3.Error:      # a database without the FX deals table: cash and positions only
            pass
    return out


def _fx_hooks(services, ids: List[str], snap) -> dict:
    """FX_CCS_PLAN D14 / K5-K6: the off-ledger part of the OPEN OTC FX contracts (FX_FORWARD, XCCY) of the accounts, from each kind's
    product hook ``risk_sensitivities(services, row, snapshot)``: ``{rows [the hooks' dicts with account_id / broker / kind], nominal
    {ccy: c}, delta {ccy: c}, partial {ccy: [contract ids whose PV delta is unknown now]}, errors [text], contracts [rows]}``.  A kind
    whose module or hook is not in this build, or a hook that fails, is named in ``errors`` - never estimated here."""
    out: dict = {"rows": [], "nominal": {}, "delta": {}, "partial": {}, "errors": [], "contracts": []}
    if not ids:
        return out
    q = ",".join("?" for _ in ids)
    kq = ",".join("?" for _ in FX_OTC_KINDS)
    try:
        with shared_reader(services.db) as conn:
            contracts = [{k: r[k] for k in r.keys()} for r in conn.execute(
                f"SELECT * FROM otc_contracts WHERE status='OPEN' AND kind IN ({kq}) AND account_id IN ({q}) ORDER BY contract_id",
                list(FX_OTC_KINDS) + list(ids)).fetchall()]
    except sqlite3.Error:          # a database without the OTC contracts table
        return out
    out["contracts"] = contracts
    for c in contracts:
        mod = getattr(services, FX_HOOK_MODULES.get(c["kind"], ""), None)
        fn = getattr(mod, "risk_sensitivities", None) if mod is not None else None
        if not callable(fn):
            out["errors"].append(f"{c['contract_id']} ({c['kind']}): its off-ledger currency risk is not available in this build")
            continue
        try:
            s = fn(services, c, snapshot=snap)
        except Exception as e:   # noqa: BLE001 - a hook never breaks the Risk page; named
            out["errors"].append(f"{c['contract_id']}: {type(e).__name__}: {e}"[:240])
            continue
        if not s:
            continue
        s = dict(s, contract_id=c["contract_id"], kind=c["kind"], account_id=c["account_id"], broker=c.get("broker"))
        out["rows"].append(s)
        for ccy, v in (s.get("fx_nominal") or {}).items():
            out["nominal"][ccy] = out["nominal"].get(ccy, 0) + int(v)
        deltas = s.get("fx_delta") or {}
        for ccy, v in deltas.items():
            out["delta"][ccy] = out["delta"].get(ccy, 0) + int(v)
        for ccy in (s.get("fx_nominal") or {}):
            if ccy not in deltas:
                out["partial"].setdefault(ccy, []).append(c["contract_id"])
        for n in s.get("notes") or []:
            out["errors"].append(str(n))
    return out


def _gl_sum(services, ids: List[str], gl: str, base: bool = True) -> int:
    if not ids:
        return 0
    q = ",".join("?" for _ in ids)
    col = "base_c" if base else "balance_c"
    with shared_reader(services.db) as conn:
        row = conn.execute(f"SELECT COALESCE(SUM({col}),0) FROM gl_balances WHERE account_id IN ({q}) AND gl=?", list(ids) + [gl]).fetchone()
    return natural(gl, int(row[0] or 0))


def _is_account_scope(scope: str) -> bool:
    s = str(scope or "")
    return bool(s) and s not in ("ALL", "ALL_BOOKS") and not s.endswith(":ALL")


def _sum_rows(rows: List[PosRisk]) -> dict:
    """long / short / gross / net of the headline rows and the gross notional / TRS notional of every row."""
    head = _agg([r for r in rows if r.headline], lambda r: "ALL").get("ALL", {"long_c": 0, "short_c": 0, "net_c": 0, "gross_c": 0, "n": 0})
    notional = sum(r.gross_notional_c for r in rows if r.gross_notional_c is not None)
    trs = sum(r.exposure_c for r in rows if r.kind == "SWAP" and r.extra.get("kind") == "TRS" and r.exposure_c is not None)
    return {"long_c": head["long_c"], "short_c": head["short_c"], "gross_c": head["gross_c"], "net_c": head["net_c"], "gross_notional_c": notional, "trs_notional_c": trs}


#: the keys of ``exposure_summary`` (and of the RiskView attributes ``report`` takes from it)
EXPOSURE_KEYS = ("long_c", "short_c", "gross_c", "net_c", "gross_leverage", "gross_notional_c", "gross_notional_leverage", "long_pct_nav", "short_pct_nav",
                 "net_pct_nav", "trs_notional_c", "trs_pct_nav", "dv01_c", "excess_liquidity_c", "encumbrance_c", "quality", "partial", "notional_missing")


def _exposure_fields(rows: List[PosRisk], nav, liq) -> dict:
    """The headline exposure of a scope from its per-position risk rows (``_pos_risk``), its NAV view and its liquidity view: long /
    short / gross / net of the headline rows (``_sum_rows``), the leverage multiples against the scope's NAV (``_ratios``), the TRS
    notional (signed, % NAV), the DV01 sum (``dv01_c`` its size, ``dv01_signed_c`` its sign), excess liquidity, encumbrance, the NAV's
    quality and the positions without a notional.  ``report`` and ``exposure_summary`` both build their figures here, so the Risk page
    and the Dashboard can never differ."""
    nav_c = int(nav.nav_c)
    totals = _sum_rows(rows)
    ratios = _ratios(totals["long_c"], totals["short_c"], totals["gross_c"], totals["net_c"], totals["gross_notional_c"], nav_c)
    dv01_signed = sum(int(r.dv01_c) for r in rows if r.dv01_c is not None)
    return {"long_c": totals["long_c"], "short_c": totals["short_c"], "gross_c": totals["gross_c"], "net_c": totals["net_c"],
            "gross_leverage": ratios["gross_leverage"], "gross_notional_c": totals["gross_notional_c"], "gross_notional_leverage": ratios["gross_notional_leverage"],
            "long_pct_nav": ratios["long_pct_nav"], "short_pct_nav": ratios["short_pct_nav"], "net_pct_nav": ratios["net_pct_nav"],
            "trs_notional_c": totals["trs_notional_c"], "trs_pct_nav": _pct_signed(totals["trs_notional_c"], nav_c), "dv01_c": abs(dv01_signed),
            "dv01_signed_c": dv01_signed, "excess_liquidity_c": int(liq.excess_liquidity_c), "encumbrance_c": int(liq.encumbrance_c), "quality": nav.quality,
            "partial": bool(nav.partial), "notional_missing": [r.instrument_id for r in rows if r.gross_notional_c is None]}


def exposure_summary(services, scope: str, snapshot=None, now: Optional[int] = None) -> dict:
    """The Risk page's headline exposure of ``scope`` without the rest of its report (FINSIM1 P1b-R2; the Dashboard's Exposure section):
    ``long_c``, ``short_c``, ``gross_c``, ``net_c``, ``gross_leverage``, ``gross_notional_c``, ``gross_notional_leverage``,
    ``long_pct_nav``, ``short_pct_nav``, ``net_pct_nav``, ``trs_notional_c``, ``trs_pct_nav``, ``dv01_c`` (the size of the signed DV01
    sum), ``excess_liquidity_c``, ``encumbrance_c``, ``quality``, ``partial``, ``notional_missing`` and ``definitions`` (DEFINITIONS) -
    computed exactly as ``report`` computes them (``Accounting.positions`` -> ``_pos_risk`` rows -> ``_exposure_fields``, plus
    ``Accounting.liquidity`` and the NAV's quality) on ONE mark snapshot, without the scenarios, financing, Reg T or warnings."""
    acc = services.accounting
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    as_of = int(now if now is not None else snap.as_of())
    nav = acc.nav(scope, snapshot=snap)
    liq = acc.liquidity(scope, snapshot=snap)
    rows = [_pos_risk(services, snap, p, as_of) for p in acc.positions(scope, snapshot=snap)]
    out = _exposure_fields(rows, nav, liq)
    out["definitions"] = dict(DEFINITIONS)
    return out


def report(services, scope: str = "ALL", snapshot=None, now: Optional[int] = None) -> RiskView:
    """§14 ``RiskView`` for a scope (an account, ``{lifetime}:ALL``, ``ALL`` or ``ALL_BOOKS``) on one mark snapshot."""
    acc = services.accounting
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    as_of = int(now if now is not None else snap.as_of())
    nav = acc.nav(scope, snapshot=snap)
    positions = acc.positions(scope, snapshot=snap)
    liq = acc.liquidity(scope, snapshot=snap)
    account_ids = list(nav.by_account)
    accounts = _accounts(services, account_ids)
    names = {aid: (a.get("name") or aid) for aid, a in accounts.items()}
    rows = [_pos_risk(services, snap, p, as_of) for p in positions]
    nav_c = int(nav.nav_c)
    # BM-06: a single account's positions are weighed against its BOOK's NAV (concentration, buckets, private), never the account's
    book_nav_c = nav_c
    pct_basis = "% of NAV of the scope"
    if _is_account_scope(scope):
        try:
            book_nav_c = int(acc.nav(f"{split_account_id(scope)[0]}:ALL", snapshot=snap).nav_c)
            pct_basis = f"% of the book's NAV ({fmt_money(book_nav_c)}); the leverage multiples use this account's own NAV ({fmt_money(nav_c)})"
        except Exception:   # noqa: BLE001 - an unknown lifetime keeps the scope's own NAV, said so
            book_nav_c = nav_c
    base_c = book_nav_c
    # exposures
    head = [r for r in rows if r.headline]
    by_bucket = _agg(rows, lambda r: r.bucket)
    by_class = _agg(rows, lambda r: r.klass)
    for b in by_class.values():
        b["pct_nav"] = _pct(b["gross_c"], base_c)
    by_account = _agg(head, lambda r: r.account_id)
    for aid, b in by_account.items():
        b["nav_c"] = int(nav.by_account.get(aid, 0))
        b["pct_nav"] = _pct(b["gross_c"], base_c)
        b["name"] = names.get(aid, aid)
    ex = _exposure_fields(rows, nav, liq)                         # P1b-R2: the figures exposure_summary serves (the Dashboard), computed once
    # currency exposure: non-USD cash & receivables at cost + positions at market, in USD at the snapshot's rate
    native = _currency_balances(services, account_ids)
    for r in rows:
        if r.currency != "USD" and r.mv_c is not None:
            native[r.currency] = native.get(r.currency, 0) + int(r.mv_c)
    # D14: the ledger part above, plus the OTC FX contracts' off-ledger part from their hooks - each counted once
    ledger_native = dict(native)
    hooks = _fx_hooks(services, account_ids, snap)
    for ccy, c in hooks["nominal"].items():
        if ccy != "USD":
            native[ccy] = native.get(ccy, 0) + int(c)
    by_ccy: Dict[str, dict] = {}
    fx_rows: List[dict] = []
    for ccy in sorted(native):
        rate = _rate_of(snap, ccy)
        usd = None if rate is None else fx_c(native[ccy], rate)
        by_ccy[ccy] = {"native_c": native[ccy], "usd_c": usd, "rate": rate, "pct_nav": None if usd is None else _pct(usd, base_c)}
        fx_rows.append(dict(by_ccy[ccy], currency=ccy))
        if usd is not None:
            b = by_bucket.setdefault(f"FX ({ccy})", {"long_c": 0, "short_c": 0, "net_c": 0, "gross_c": 0, "n": 0})
            b["long_c" if usd >= 0 else "short_c"] += abs(usd)
            b["net_c"] += usd
            b["gross_c"] += abs(usd)
    exposures = []
    head_buckets = {r.bucket for r in rows if r.headline and r.exposure_c is not None}
    for bucket in BUCKET_ORDER + sorted(k for k in by_bucket if k not in BUCKET_ORDER):
        b = by_bucket.get(bucket)
        if b is None:
            continue
        # V104R2-BW-08: a bucket is in the headline gross exactly when its rows are (the FX currency buckets, IRS / XCCY, CDX and FX forwards are not)
        exposures.append({"bucket": bucket, "long_c": b["long_c"], "short_c": b["short_c"], "net_c": b["net_c"], "gross_c": b["gross_c"], "pct_nav": _pct(b["gross_c"], base_c),
                          "headline": bucket in head_buckets})
    # greeks, delta and DV01
    gk = _greeks_total(rows, lambda c: _rate_of(snap, c))
    delta, delta_rows = _delta(rows, gk["rows"])
    dv01_rows = [{"instrument_id": r.instrument_id, "label": r.label, "account_id": r.account_id, "account_name": names.get(r.account_id, r.account_id), "kind": r.kind,
                  "qty": r.qty, "dv01_c": r.dv01_c, "basis": r.dv01_basis, "tenor_years": r.tenor_years, "tenor": _tenor_bucket(r.tenor_years)}
                 for r in rows if r.dv01_c is not None or r.dv01_basis]
    tenors: Dict[str, dict] = {}
    for d in dv01_rows:
        if d["dv01_c"] is None:
            continue
        t = tenors.setdefault(d["tenor"], {"tenor": d["tenor"], "dv01_c": 0, "n": 0})
        t["dv01_c"] += int(d["dv01_c"])
        t["n"] += 1
    order = [n for n, _hi in TENOR_BUCKETS] + ["n/a"]
    dv01_by_tenor = [tenors[k] for k in order if k in tenors]
    # scenarios (one plain sentence per group, BM-28)
    scenarios: List[dict] = []
    for e in EQUITY_SHOCKS:
        s = _scenario_equity(rows, e)
        scenarios.append({"name": f"equities {_shock_text(e)}", "group": "equities", "shock": _shock_text(e), "pnl_c": s["pnl_c"], "detail": {"by_class": s["by_class"]},
                          "unpriced": s["unpriced"], "unchanged": s["unchanged"], "note": NOTE_OF["equities"]})
    for bp in RATE_SHOCKS_BP:
        s = _scenario_rates(rows, bp, services, snap)
        scenarios.append({"name": f"rates {_shock_text(Decimal(bp), 'bp')}", "group": "rates", "shock": _shock_text(Decimal(bp), "bp"), "pnl_c": s["pnl_c"],
                          "detail": {"by_class": s["by_class"]}, "unpriced": s["unpriced"], "unchanged": [], "note": NOTE_OF["rates"]})
    for dv in VOL_SHOCKS:
        s = _scenario_vol(rows, dv)
        scenarios.append({"name": f"vol {_shock_text(dv, 'pts')}", "group": "vol", "shock": _shock_text(dv, "pts"), "pnl_c": s["pnl_c"], "detail": {"by_class": s["by_class"]},
                          "unpriced": s["unpriced"], "unchanged": [], "note": NOTE_OF["vol"]})
    fx_total = sum(v["usd_c"] for v in by_ccy.values() if v["usd_c"] is not None)
    for f in FX_SHOCKS:
        pnl = to_c(from_c(fx_total) * f)
        scenarios.append({"name": f"USD {_shock_text(-f)} vs foreign currencies", "group": "fx", "shock": _shock_text(f) + " foreign", "pnl_c": pnl,
                          "detail": {"by_currency": {c: to_c(from_c(v["usd_c"]) * f) for c, v in by_ccy.items() if v["usd_c"] is not None}},
                          "unpriced": [c for c, v in by_ccy.items() if v["usd_c"] is None], "unchanged": [], "note": NOTE_OF["fx"]})
    # concentration (book level for an account scope, BM-06)
    conc = sorted((r for r in rows if r.exposure_c is not None and r.headline), key=lambda r: -abs(r.exposure_c))[:10]
    concentration = [{"instrument_id": r.instrument_id, "symbol": r.label or r.symbol, "name": r.name, "account_id": r.account_id, "account_name": names.get(r.account_id, r.account_id),
                      "mv_c": r.exposure_c, "pct_nav": _pct(r.exposure_c, base_c), "note": r.exposure_basis if r.exposure_basis != "market value" else ""} for r in conc]
    # financing, margin, OTC, private
    fin, margin, otc, warnings = _financing(services, acc, snap, rows, accounts, nav, liq, names)
    private = _private(rows, base_c)
    # leverage against NAV (owner 2026-10-21 18:29): the scope, every account, every book
    lev = None if nav_c <= 0 else (Decimal(ex["gross_c"]) / Decimal(nav_c)).quantize(PCT4)
    lev_accounts = _leverage_by_account(rows, accounts, nav, names)
    for row in lev_accounts:
        b = by_account.get(row["account_id"])
        if b is not None:
            b.update({k: row[k] for k in ("gross_notional_c", "gross_leverage", "gross_notional_leverage", "long_pct_nav", "short_pct_nav", "net_pct_nav")})
    by_book = _by_book(services, rows, accounts, nav)
    counterparties = _counterparties(services, list(nav.lifetime_ids), snap)
    credit = _credit_derivatives(services, rows, base_c)
    # warnings
    for r in conc:
        pct = _pct(r.exposure_c, base_c)
        if pct is not None and pct > THRESHOLDS["CONCENTRATION"]:
            basis = r.exposure_basis if r.exposure_basis != "market value" else ""
            warnings.append(_w("CONCENTRATION", f"{r.label or r.symbol} {basis} is {_pct_text(pct)} of NAV (limit 25 %)".replace("  ", " "),
                               r.account_id, r.instrument_id, str(pct), "0.25"))
    if lev is not None and lev > THRESHOLDS["LEVERAGE"]:
        warnings.append(_w("LEVERAGE", f"gross exposure is {lev}x NAV (limit 2x)", None, None, str(lev), "2"))
    for r in rows:
        if r.quality == "stale":
            warnings.append(_w("STALE_MARK", f"{r.label or r.symbol} is marked on a stale quote; its P&L may be off", r.account_id, r.instrument_id, "stale", "fresh"))
        elif r.quality == "unavailable":
            warnings.append(_unpriced_warning(r))
    for ccy in nav.fx_missing:
        warnings.append(_w("UNPRICED", f"no USD rate for {ccy}: balances in {ccy} are excluded from NAV", None, None, "unavailable", "rate"))
    if private["pct_nav"] is not None and private["pct_nav"] > THRESHOLDS["PRIVATE_ILLIQUID"]:
        warnings.append(_w("PRIVATE_ILLIQUID", f"private / illiquid positions are {_pct_text(private['pct_nav'])} of NAV (limit 30 %)", None, None, str(private["pct_nav"]), "0.30"))
    warnings.sort(key=lambda w: (0 if w["severity"] == SEVERITY_WARN else 1, w["code"]))
    view = RiskView(scope, as_of, nav_c, nav.quality, nav.partial, list(nav.missing), ex["long_c"], ex["short_c"], ex["gross_c"], ex["net_c"], ex["dv01_c"],
                    ex["dv01_signed_c"], dv01_rows, exposures, {k: dict(v) for k, v in by_class.items()}, by_account, by_ccy, fx_rows, gk["total"], gk["rows"], scenarios,
                    concentration, fin, margin, private, otc, warnings, rows, ex["excess_liquidity_c"], ex["encumbrance_c"], lev)
    view.book_nav_c, view.pct_basis = book_nav_c, pct_basis
    view.gross_leverage, view.gross_notional_c, view.gross_notional_leverage = ex["gross_leverage"], ex["gross_notional_c"], ex["gross_notional_leverage"]
    view.long_pct_nav, view.short_pct_nav, view.net_pct_nav = ex["long_pct_nav"], ex["short_pct_nav"], ex["net_pct_nav"]
    view.trs_notional_c, view.trs_pct_nav = ex["trs_notional_c"], ex["trs_pct_nav"]
    view.notional_missing = ex["notional_missing"]
    view.leverage_by_account, view.by_book = lev_accounts, by_book
    view.delta, view.delta_by_underlying, view.dv01_by_tenor = delta, delta_rows, dv01_by_tenor
    view.scenario_notes = [dict(n) for n in SCENARIO_NOTES]
    view.counterparties, view.account_names, view.definitions = counterparties, names, dict(DEFINITIONS)
    view.credit_derivatives = credit
    view.by_sector = _by_sector(head, base_c)
    view.reg_t = reg_t_block(services, scope, accounts, names, snapshot=snap)     # P1b-R2: the Reg T panel on this report's marks
    view.headline_note = ("the buckets marked 'in gross' add up to the gross exposure tile (long + short headline exposure); the others - IRS / "
                          "cross-currency swaps and CDX at notional, FX forwards and the currency (FX) rows - are shown for their size but are not in it: "
                          "their measures are DV01, CS01 and the currency exposure")
    try:
        view.fx_sensitivities = fx_block(services, snap, account_ids, ledger_native, hooks, ex, accounts, names, fin)
    except Exception as e:   # noqa: BLE001 - the block informs; its failure is named, the rest of the report stands
        view.fx_sensitivities = dict(_empty_fx_block(), notes=[f"the FX block is unavailable: {type(e).__name__}: {e}"[:240]])
    return view


# ---- FX_CCS_PLAN K7: the FX block --------------------------------------------------------------------------------------------------
def _empty_fx_block() -> dict:
    return {"by_currency": [], "pairs": [], "rates01": [], "basis01": [], "spread01": [], "points01": [], "counterparty": [], "ladder": [],
            "limits": {"headline_gross_nav": None, "gross_notional_nav": None, "warning_limit": "2x NAV", "caps": "none configured", "text": CAPS_TEXT},
            "notes": [], "labels": [], "funding_plans": []}


def _pair_move(pair: str, shock: Decimal, held: Optional[str]) -> Dict[str, Decimal]:
    """The factor each currency's USD rate moves by when ``pair`` moves by ``shock`` (a fraction): a USD pair moves its foreign currency
    (base: x (1 + shock); quote: x 1 / (1 + shock)); a cross moves the currency whose USD pair is NOT ``held`` (the base's pair held: the
    quote currency x 1 / (1 + shock); the quote's pair held: the base x (1 + shock))."""
    from .instruments import fx_pair_id
    base, quote = pair[:3], pair[3:]
    f = ONE + shock
    if quote == "USD":
        return {base: f}
    if base == "USD":
        return {quote: ONE / f}
    if held == fx_pair_id(base):
        return {quote: ONE / f}
    return {base: f}


def fx_block(services, snap, account_ids: List[str], ledger_native: Dict[str, int], hooks: dict, ex: dict, accounts: Dict[str, dict],
             names: Dict[str, str], fin: dict) -> dict:
    """FX_CCS_PLAN K7 / section 6: the Risk page's FX block for the scope (see the module conventions).  ``ledger_native`` is the ledger
    part per currency (``_currency_balances`` + non-USD positions at market), ``hooks`` the off-ledger part (``_fx_hooks``)."""
    from .instruments import fx_cross_id, fx_pair_id
    out = _empty_fx_block()
    notes: List[str] = list(hooks.get("errors") or [])
    labels: List[str] = []
    for h in hooks.get("rows") or []:
        for lab in h.get("labels") or []:
            if lab not in labels:
                labels.append(lab)
    ccys = sorted({c for c in ledger_native if c != "USD"} | {c for c in hooks["nominal"] if c != "USD"} | {c for c in hooks["delta"] if c != "USD"})
    rates: Dict[str, Optional[Decimal]] = {c: _rate_of(snap, c) for c in ccys}
    pvd: Dict[str, Optional[int]] = {}
    for ccy in ccys:
        nl, nh = int(ledger_native.get(ccy, 0)), int(hooks["nominal"].get(ccy, 0))
        dh = int(hooks["delta"].get(ccy, 0))
        partial = list(hooks["partial"].get(ccy) or [])
        pv = None if partial else nl + dh
        pvd[ccy] = pv
        rate = rates[ccy]
        rows = []
        if nl:
            rows.append({"source": "ledger", "what": f"{ccy} balances, receivables / payables and positions on the ledger", "nominal_c": nl, "pv_delta_c": nl})
        for h in hooks.get("rows") or []:
            n, d = (h.get("fx_nominal") or {}).get(ccy), (h.get("fx_delta") or {}).get(ccy)
            if n is None and d is None:
                continue
            rows.append({"source": h["contract_id"], "kind": h["kind"], "what": f"{h['contract_id']} ({OTC_KIND_LABEL.get(h['kind'], h['kind'])}): off the ledger",
                         "nominal_c": None if n is None else int(n), "pv_delta_c": None if d is None else int(d)})
        usd1 = usd5 = None
        if pv is not None and rate is not None:
            usd1 = to_c(from_c(pv) * rate * Decimal("0.01"))
            usd5 = to_c(from_c(pv) * rate * Decimal("0.05"))
        if partial:
            notes.append(f"{ccy}: the PV delta is unavailable now ({', '.join(partial)} could not be re-priced); the nominal stands")
        out["by_currency"].append({"ccy": ccy, "rate": rate, "nominal_c": nl + nh, "nominal_ledger_c": nl, "nominal_hooks_c": nh, "pv_delta_c": pv,
                                   "pv_delta_ledger_c": nl, "pv_delta_hooks_c": None if partial else dh, "usd_per_1pct_c": usd1, "usd_per_5pct_c": usd5,
                                   "partial": partial, "rows": rows})
    usd_hooks = int(hooks["nominal"].get("USD", 0))
    if usd_hooks:
        out["usd_nominal_hooks_c"] = usd_hooks
    # the pair scenarios: each USD pair with the other USD pairs held; each cross an open OTC FX forward trades, with either leg held
    def pnl(pair: str, shock: Decimal, held: Optional[str]):
        total, missing = 0, []
        for c, fac in _pair_move(pair, shock, held).items():
            if c == "USD":
                continue
            if c not in pvd:
                continue                                                  # no exposure in that currency: it does not move the book
            if pvd[c] is None or rates.get(c) is None:
                missing.append(c)
                continue
            total += to_c(from_c(pvd[c]) * rates[c] * (fac - ONE))
        return (None if missing else total), missing
    usd_pairs = [fx_pair_id(c) for c in ccys]
    crosses: List[str] = []
    for h in hooks.get("rows") or []:
        p = str(h.get("pair") or "")
        if len(p) == 6 and "USD" not in (p[:3], p[3:]):
            p = fx_cross_id(p[:3], p[3:])
            if p not in crosses:
                crosses.append(p)
    for pair in usd_pairs:
        others = [p for p in usd_pairs if p != pair]
        held = (" and ".join(others) + " held") if others else "no other currency held"
        for s in FX_PAIR_SHOCKS:
            v, missing = pnl(pair, s, None)
            out["pairs"].append({"pair": pair, "shock": _shock_text(s), "shock_frac": s, "held": held, "pnl_c": v,
                                 "note": (f"unavailable: no PV delta for {', '.join(missing)} now" if missing else "")})
    for pair in crosses:
        for leg in (fx_pair_id(pair[:3]), fx_pair_id(pair[3:])):
            for s in FX_PAIR_SHOCKS:
                v, missing = pnl(pair, s, leg)
                out["pairs"].append({"pair": pair, "shock": _shock_text(s), "shock_frac": s, "held": f"{leg} held", "pnl_c": v,
                                     "note": (f"unavailable: no PV delta for {', '.join(missing)} now" if missing else "")})
    # rate01 per currency, basis01 per pair, points01 per pair (the hooks' re-priced figures, summed)
    def agg(field_name: str, key_name: str) -> List[dict]:
        acc_: Dict[str, dict] = {}
        for h in hooks.get("rows") or []:
            for k0, v in (h.get(field_name) or {}).items():
                if v is None:
                    continue
                k = str(k0).replace("/", "").upper()                      # "EUR/USD" and "EURUSD" are one pair
                a = acc_.setdefault(k, {key_name: k, "usd_per_bp_c": 0, "rows": []})
                a["usd_per_bp_c"] += int(v)
                a["rows"].append({"contract_id": h["contract_id"], "kind": h["kind"], "usd_per_bp_c": int(v)})
        return [acc_[k] for k in sorted(acc_)]
    out["rates01"] = agg("rate01_c", "ccy")
    out["basis01"] = agg("basis01_c", "pair")
    out["spread01"] = agg("spread01_c", "ccy")                           # D5: the EUR OIS spread over the ECB AAA curve, +1 bp on its grid nodes
    pts: Dict[str, dict] = {}
    for h in hooks.get("rows") or []:
        v = h.get("points01_c")
        if v is None:
            continue
        p = str(h.get("pair") or "")
        a = pts.setdefault(p, {"pair": p, "usd_per_pip_c": 0, "rows": []})
        a["usd_per_pip_c"] += int(v)
        a["rows"].append({"contract_id": h["contract_id"], "kind": h["kind"], "usd_per_pip_c": int(v)})
    out["points01"] = [pts[k] for k in sorted(pts)]
    out["counterparty"] = _fx_counterparty(services, accounts, names, fin)
    ladder, plans, lnotes = _fx_ladder(services, hooks, accounts, names)
    out["ladder"], out["funding_plans"] = ladder, plans
    notes += lnotes
    out["limits"] = {"headline_gross_nav": ex.get("gross_leverage"), "gross_notional_nav": ex.get("gross_notional_leverage"), "warning_limit": "2x NAV",
                     "caps": "none configured", "text": CAPS_TEXT,
                     "basis": "OTC FX forwards and cross-currency swaps are outside the headline gross; their notional is in the gross notional"}
    if hooks.get("rows"):
        labels = ["MODEL"] + [x for x in labels if x != "MODEL"]
    out["labels"] = labels
    out["notes"] = notes
    out["method"] = ("nominal and PV delta per currency = the ledger part (balances, receivables / payables and positions) + the OTC FX contracts'"
                     " off-ledger part from their own re-pricing; a pair move re-prices the PV deltas linearly at today's USD rates; rate01, basis01"
                     " and points01 are each a full re-price of the contract for +1 bp / +1 pip")
    return out


def _fx_counterparty(services, accounts: Dict[str, dict], names: Dict[str, str], fin: dict) -> List[dict]:
    """K7 ``counterparty``: per prime-broker account with OTC contracts - the contracts' value to you (the CSA exposure where the contract
    states one, else its mark), the IM posted, VM posted / received, and the exposure to the dealer's default = the IM posted (assumed
    not segregated, ASSUMED) + whatever the VM posted exceeds what you owe on the contracts."""
    out = []
    pb_ids = [aid for aid, a in sorted(accounts.items()) if a.get("kind") == "PB"]
    if not pb_ids:
        return out
    with shared_reader(services.db) as conn:
        for aid in pb_ids:
            try:
                rows = conn.execute("SELECT contract_id, kind, mark_c, inputs_json FROM otc_contracts WHERE status='OPEN' AND account_id=?", (aid,)).fetchall()
            except sqlite3.Error:
                rows = []
            if not rows:
                continue
            mtm = csa = 0
            for r in rows:
                m = int(r["mark_c"] or 0)
                mtm += m
                try:
                    inp = json.loads(r["inputs_json"] or "{}") or {}
                except ValueError:
                    inp = {}
                x = inp.get("csa_exposure_c") if isinstance(inp, dict) else None
                csa += int(x) if isinstance(x, int) and not isinstance(x, bool) else m
            per = (fin.get("accounts") or {}).get(aid) or {}
            im, vp, vr = int(per.get("otc_im_c") or 0), int(per.get("otc_vm_posted_c") or 0), int(per.get("otc_vm_received_c") or 0)
            exposure = im + max(0, csa + vp - vr)
            a = accounts[aid]
            out.append({"account_id": aid, "name": names.get(aid, aid), "broker": a.get("broker"), "contracts": len(rows), "mtm_c": mtm, "csa_exposure_c": csa,
                        "im_posted_c": im, "vm_posted_c": vp, "vm_received_c": vr, "exposure_c": exposure,
                        "basis": ("exposure to the dealer's default = IM posted (assumed not segregated, ASSUMED) + max(0, the contracts' CSA value to you + VM"
                                  " posted - VM received)")})
    return out


def _fx_ladder(services, hooks: dict, accounts: Dict[str, dict], names: Dict[str, str]):
    """K7 ``ladder``: the OPEN OTC FX contracts' scheduled flows (each product's ``scheduled_flows``) and, when the settlement-funding module
    is in the build, its planned purchases and gaps (``ccyfunding.plan``, read lazily) - ``(rows, plans, notes)``."""
    rows: List[dict] = []
    notes: List[str] = []
    plans: List[dict] = []
    contracts = hooks.get("contracts") or []
    if contracts:
        with shared_reader(services.db) as conn:
            for c in contracts:
                mod = getattr(services, FX_HOOK_MODULES.get(c["kind"], ""), None)
                fn = getattr(mod, "scheduled_flows", None) if mod is not None else None
                if not callable(fn):
                    continue
                try:
                    from .fx import hook_takes_services          # lazy: the hook reads the published indices through this runtime (P-C-R4)
                    flows = (fn(conn, c, services) if hook_takes_services(fn) else fn(conn, c)) or []
                except Exception as e:   # noqa: BLE001 - named
                    notes.append(f"{c['contract_id']}: scheduled flows unavailable: {type(e).__name__}: {e}"[:200])
                    continue
                for f in flows:
                    if not isinstance(f, dict):
                        continue
                    rows.append({"date": f.get("date"), "ccy": f.get("ccy"), "amount_c": f.get("amount_c"), "what": f.get("what"), "kind": "flow",
                                 "event": f.get("event"), "known": f.get("known"), "contract_id": f.get("contract_id") or c["contract_id"],
                                 "account_id": c["account_id"]})
    fund_mod = getattr(services, "ccyfunding", None)
    plan_fn = getattr(fund_mod, "plan", None) if fund_mod is not None else None
    acct_ids = sorted({c["account_id"] for c in contracts})
    if acct_ids and not callable(plan_fn):
        notes.append("the settlement-funding plan (EUR purchases before each obligation) is not in this build: the ladder shows the contracts' flows only")
    elif callable(plan_fn):
        for aid in acct_ids:
            try:
                p = plan_fn(services, aid)
            except Exception as e:   # noqa: BLE001 - named, never a guessed gap
                notes.append(f"{names.get(aid, aid)}: settlement-funding plan unavailable: {type(e).__name__}: {e}"[:200])
                continue
            if not isinstance(p, dict):
                continue
            plans.append({k: p.get(k) for k in ("account_id", "available", "reason", "gaps", "usd_start", "notes", "method") if k in p})
            # the plan's own ladder repeats the obligations the contracts' flows already list: only its purchases and its gaps are added here
            for x in p.get("purchases") or []:
                if not isinstance(x, dict):
                    continue
                what = x.get("what")
                what = "; ".join(str(w) for w in what) if isinstance(what, (list, tuple)) else str(what or "")
                usd = x.get("usd_at_mid_c")
                ftd = x.get("funding_trade_date") or x.get("trade_date")
                rows.append({"date": x.get("value_date") or x.get("date"), "ccy": x.get("ccy"), "amount_c": x.get("amount_c"), "kind": "purchase",
                             "what": (f"settlement-funding purchase{' dealt ' + str(ftd) if ftd else ''}: buy {x.get('ccy')}"
                                      + (f" with about USD {fmt_money(int(usd))} at today's mid" if isinstance(usd, int) else "") + (f" - for {what}" if what else "")),
                             "trade_date": ftd, "usd_c": None if not isinstance(usd, int) else -usd, "account_id": aid})
            for g in p.get("gaps") or []:
                if isinstance(g, dict):
                    gap = g.get("gap_c")
                    rows.append({"date": g.get("funding_trade_date") or g.get("value_date") or g.get("date"), "ccy": "USD",
                                 "amount_c": None if not isinstance(gap, int) else -gap, "what": g.get("text") or "settlement-funding gap", "kind": "gap",
                                 "account_id": aid})
    rows.sort(key=lambda r: (str(r.get("date") or ""), {"purchase": 0, "flow": 1, "gap": 2}.get(r.get("kind"), 3), str(r.get("ccy") or "")))
    return rows, plans, notes


def _by_sector(head: List[PosRisk], base_c: int) -> List[dict]:
    """PORTFOLIO-19: long / short / net / gross headline exposure per sector, % of NAV; "unclassified" without a sector.  The gross
    sums to the headline gross (the same rows)."""
    agg = _agg(head, lambda r: r.sector or "unclassified")
    names: Dict[str, List[str]] = {}
    for r in head:
        if r.exposure_c is not None:
            names.setdefault(r.sector or "unclassified", [])
            if (r.label or r.symbol) not in names[r.sector or "unclassified"]:
                names[r.sector or "unclassified"].append(r.label or r.symbol)
    out = [dict(b, sector=k, pct_nav=_pct(b["gross_c"], base_c), instruments=names.get(k, [])) for k, b in agg.items()]
    out.sort(key=lambda b: (b["sector"] == "unclassified", -b["gross_c"], b["sector"]))
    return out


REG_T_KEYS = ("margin_required_c", "margin_posted_c", "margin_excess_c", "collateral_posted_c", "collateral_received_c", "restricted_c", "encumbered_c")


def _takes_kw(fn, name: str) -> bool:
    """True when ``fn`` accepts the keyword ``name`` (a parameter of that name, or ``**kwargs``)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def reg_t_block(services, scope: str, accounts: Dict[str, dict], names: Dict[str, str], snapshot=None) -> dict:
    """FINSIM1_PLAN P3 task 13: the Risk page's Reg T panel - per account (Treasury excluded) P6's ``collateral.summary(services,
    account_id)`` (C16): requirement, posted / eligible collateral, excess (negative = deficit), restricted, open calls and the
    assumed terms as P6 states them; plus the scope's total.  ``available: false`` with the reason while that function is not in the
    build; one account's failure is listed in ``errors``, never hidden and never replaced by a figure of our own.  ``snapshot`` (P1b-R2):
    every account's summary and the total are priced on these marks (the report's own) when ``collateral.summary`` takes one, instead
    of each call taking a snapshot of its own."""
    mod = getattr(services, "collateral", None)
    summ = getattr(mod, "summary", None) if mod is not None else None
    if not callable(summ):
        return {"available": False, "reason": "margin requirements by account (Reg T) are not in this build", "rows": []}
    kw = {"snapshot": snapshot} if snapshot is not None and _takes_kw(summ, "snapshot") else {}
    rows, errors = [], []
    for aid in sorted(accounts):
        a = accounts[aid]
        if a.get("kind") == "TREASURY":
            continue
        try:
            s = summ(services, aid, **kw)
            s = dict(s) if isinstance(s, dict) else {}
        except Exception as e:   # noqa: BLE001 - named in the panel
            errors.append(f"{names.get(aid, aid)}: {type(e).__name__}: {e}"[:200])
            continue
        row = {"account_id": aid, "name": names.get(aid, aid), "kind": a.get("kind")}
        for k in REG_T_KEYS + ("open_calls", "unmet_calls", "failed_settlements"):
            if k in s:
                row[k] = s.get(k)
        if s.get("open_calls_c") is not None:
            row["open_calls_amount_c"] = s.get("open_calls_c")
        regt = s.get("regt") if isinstance(s.get("regt"), list) else []
        r0 = regt[0] if regt and isinstance(regt[0], dict) else None
        if r0 is None or not r0.get("applies", True):
            continue                                       # Reg T applies to the accounts holding US equities (US Exchange, the PBs)
        for k in ("requirement_c", "maintenance_c", "eligible_c", "excess_c", "deficit_c", "margin_cash_c", "short_mv_c", "long_mv_c", "long_loan_value_c",
                  "below_maintenance", "terms", "notes", "definition"):
            if k in r0:
                row[k] = r0.get(k)
        rows.append(row)
    total = None
    try:
        total = summ(services, scope, **kw)
        total = dict(total) if isinstance(total, dict) else None
    except Exception as e:   # noqa: BLE001
        errors.append(f"scope total: {type(e).__name__}: {e}"[:200])
    return {"available": True, "rows": rows, "total": total, "errors": errors,
            "note": "Reg T on assumed, editable terms (P6's margin terms): requirement against the eligible collateral; a deficit is reserved on cash "
                    "and raises a margin call - positions are never sold for you"}


# ---- PORTFOLIO-19: the owner's sector classification (light command) -----------------------------------------------------------------
SECTOR_SCHEMA = ["CREATE TABLE IF NOT EXISTS instrument_sectors (instrument_id TEXT PRIMARY KEY, sector TEXT NOT NULL, source TEXT NOT NULL,"
                 " ts INTEGER NOT NULL, command_id INTEGER)"]
SECTOR_MAX = 60


def h_classify(ctx, payload: dict) -> dict:
    """``instrument.classify {instrument_id, sector}`` (light; source "owner"): stores the owner's sector of an instrument (it overrides the
    instrument master's), one SETTINGS_CHANGED event with before / after; an empty sector (or "unclassified") removes the owner's
    classification, so the master's (or "unclassified") shows again.  Refused: an unknown instrument, a sector over 60 characters or
    with control characters."""
    from .commands import RejectError
    iid = str(payload.get("instrument_id") or "").strip().upper()
    if not iid or ctx.conn.execute("SELECT 1 FROM instruments WHERE instrument_id=?", (iid,)).fetchone() is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid or '(none)'} is not in the instrument master")
    sector = " ".join(str(payload.get("sector") or "").split())
    if len(sector) > SECTOR_MAX or any(ord(ch) < 32 for ch in sector):
        raise RejectError("BAD_SECTOR", f"a sector is plain text of at most {SECTOR_MAX} characters", {"sector": payload.get("sector")})
    if ctx.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='instrument_sectors'").fetchone() is None:
        raise RejectError("NOT_IN_THIS_BUILD", "the sector table is missing (the risk module did not bind)")
    row = ctx.conn.execute("SELECT sector FROM instrument_sectors WHERE instrument_id=?", (iid,)).fetchone()
    before = row[0] if row else None
    if sector.lower() in ("", "unclassified"):
        ctx.conn.execute("DELETE FROM instrument_sectors WHERE instrument_id=?", (iid,))
        after = None
    else:
        ctx.conn.execute("INSERT OR REPLACE INTO instrument_sectors(instrument_id, sector, source, ts, command_id) VALUES (?,?,?,?,?)",
                         (iid, sector, "owner", ctx.ts, ctx.command_id))
        after = sector
    ctx.event("SETTINGS_CHANGED", instrument_id=iid, ref_type="instrument", ref_id=iid, before={"sector": before}, after={"sector": after, "source": "owner"})
    return {"instrument_id": iid, "sector": after, "source": "owner" if after else None, "before": before}


def bind(services) -> None:
    """``instrument_sectors`` (additive, idempotent)."""
    db = getattr(services, "db", None)
    if db is not None and callable(getattr(db, "ensure_schema_ext", None)):
        db.ensure_schema_ext("risk", SECTOR_SCHEMA)


def register(bus, services=None) -> None:
    bus.register("instrument.classify", h_classify, light=True)


def _credit_derivatives(services, rows: List[PosRisk], base_c: int) -> Optional[dict]:
    """The PB credit-derivatives block (CDX index protection) when its module is bound (``services.swaps_cds.risk_block``: CS01,
    notional by side, spread scenarios re-priced); None without such contracts or without the module; a failure is named."""
    mod = getattr(services, "swaps_cds", None)
    fn = getattr(mod, "risk_block", None) if mod is not None else None
    if not callable(fn):
        return None
    try:
        return fn(services, rows, base_c)
    except Exception as e:   # noqa: BLE001 - named on the page, never a guessed number
        return {"error": f"{type(e).__name__}: {e}"[:200], "rows": []}


def _unpriced_warning(r: PosRisk) -> dict:
    """UNPRICED: what NAV does with a position without a valid mark (MONEY-5: a security / private position stays at its cost; a
    future's open P&L since its last settle waits for a mark).  A private name without a price per share is expected (valuation-
    only rounds), so its line is INFO; a listed instrument without a mark is a data problem (WARN)."""
    name = r.label or r.symbol
    cost = "" if r.cost_base_c is None else f" ({fmt_money(r.cost_base_c)})"
    if r.kind in ("PRIVATE", "PRIVATE_LOAN"):
        return _w("UNPRICED", f"{name} has no valid mark; NAV carries it at cost{cost}", r.account_id, r.instrument_id, "unavailable", "priced", SEVERITY_INFO)
    if r.kind == "FUTURE":
        return _w("UNPRICED", f"{name} has no valid mark; its open P&L since the last settle stays out of NAV until a mark arrives", r.account_id, r.instrument_id,
                  "unavailable", "priced")
    if r.kind == "SWAP":
        return _w("UNPRICED", f"{name} has no valid model mark; NAV keeps the mark of its last variation margin", r.account_id, r.instrument_id, "unavailable", "priced")
    return _w("UNPRICED", f"{name} has no valid mark; NAV carries it at cost{cost}", r.account_id, r.instrument_id, "unavailable", "priced")


def _w(code: str, message: str, account_id: Optional[str], instrument_id: Optional[str], value: Optional[str], threshold: Optional[str], severity: str = SEVERITY_WARN) -> dict:
    return {"code": code, "severity": severity, "message": message, "account_id": account_id, "instrument_id": instrument_id, "value": value, "threshold": threshold}


def _leverage_by_account(rows: List[PosRisk], accounts: Dict[str, dict], nav, names: Dict[str, str]) -> List[dict]:
    """Every non-Treasury account with a position: long / short / gross / net, gross notional and TRS notional, and the multiples
    against the ACCOUNT's own NAV (a PB account's leverage at the dealer)."""
    out = []
    for aid in sorted(accounts):
        a = accounts[aid]
        if a.get("kind") == "TREASURY":
            continue
        mine = [r for r in rows if r.account_id == aid]
        if not mine:
            continue
        s = _sum_rows(mine)
        a_nav = int(nav.by_account.get(aid, 0))
        row = {"account_id": aid, "name": names.get(aid, aid), "kind": a.get("kind"), "role": a.get("role"), "broker": a.get("broker"), "lifetime_id": a.get("lifetime_id"),
               "nav_c": a_nav, "positions": len(mine)}
        row.update(s)
        row.update(_ratios(s["long_c"], s["short_c"], s["gross_c"], s["net_c"], s["gross_notional_c"], a_nav))
        row["trs_pct_nav"] = _pct_signed(s["trs_notional_c"], a_nav)
        out.append(row)
    return out


def _by_book(services, rows: List[PosRisk], accounts: Dict[str, dict], nav) -> List[dict]:
    """The same multiples per book (lifetime) in scope: the book's NAV = the sum of its accounts' NAV in this view."""
    lids = list(nav.lifetime_ids) or sorted({a.get("lifetime_id") for a in accounts.values() if a.get("lifetime_id")})
    books = _book_names(services, lids)
    out = []
    for lid in lids:
        aids = [aid for aid, a in accounts.items() if a.get("lifetime_id") == lid]
        mine = [r for r in rows if r.account_id in aids]
        s = _sum_rows(mine)
        b_nav = sum(int(nav.by_account.get(aid, 0)) for aid in aids)
        meta = books.get(lid, {})
        row = {"lifetime_id": lid, "book_code": meta.get("book_code"), "name": meta.get("name") or lid, "nav_c": b_nav, "positions": len(mine)}
        row.update(s)
        row.update(_ratios(s["long_c"], s["short_c"], s["gross_c"], s["net_c"], s["gross_notional_c"], b_nav))
        row["trs_pct_nav"] = _pct_signed(s["trs_notional_c"], b_nav)
        out.append(row)
    return out


def _counterparties(services, lifetime_ids: List[str], snap) -> dict:
    """C20: exposure by counterparty (banks, repo dealers, agent lenders, prime brokers, private-credit borrowers) for the books in
    scope; measured per book (a single account's scope shows its book's counterparties)."""
    cp = getattr(services, "counterparties", None)
    fn = getattr(cp, "exposure", None) if cp is not None else None
    if not callable(fn):
        return {"available": False, "rows": [], "totals": None, "errors": [], "note": "the counterparty directory is not loaded"}
    rows: List[dict] = []
    errors: List[str] = []
    for lid in lifetime_ids:
        try:
            for x in fn(services, lid, snap) or []:
                rows.append(dict(x, lifetime_id=lid))
        except Exception as e:   # noqa: BLE001 - named, never a guessed exposure
            errors.append(f"{lid}: {type(e).__name__}: {e}"[:200])
    totals = {"exposure_c": sum(int(r.get("exposure_c") or 0) for r in rows), "collateral_c": sum(int(r.get("collateral_c") or 0) for r in rows),
              "net_c": sum(int(r.get("net_c") or 0) for r in rows)}
    return {"available": True, "rows": rows, "totals": totals, "errors": errors,
            "note": "exposure = what the counterparty owes the book or holds of it; collateral = what the book holds of theirs; net = exposure - collateral "
                    "(positive: unsecured exposure to them). Measured per book; the terms of every institution are assumed and editable."}


def _financing(services, acc, snap, rows: List[PosRisk], accounts: Dict[str, dict], nav, liq, names: Optional[Dict[str, str]] = None):
    """Financing & liquidity (§6.0, §13.11 margin headroom per PB incl. OTC IM / VM), the UI margin rows, the OTC block per PB and
    the financing warnings (option margin, maintenance shortfall, OTC VM, loans)."""
    from .accounting import futures_rates_c
    names = names or {}
    warnings: List[dict] = []
    margin_rows: List[dict] = []
    pb_rows: List[dict] = []
    per_account: Dict[str, dict] = {}
    fut_req = fut_posted = opt_req = opt_posted = coll = otc_im = otc_vm_p = otc_vm_r = mm_total = 0
    for aid, a in sorted(accounts.items()):
        if a.get("kind") == "TREASURY":
            continue
        ob = acc.obligations(aid)
        m = ob.margin
        a_nav = int(nav.by_account.get(aid, 0))
        mm = 0
        futs = [r for r in rows if r.account_id == aid and r.kind == "FUTURE"]
        if futs:
            with shared_reader(services.db) as c:
                for r in futs:
                    inst = services.instruments.get(r.instrument_id)
                    mm += futures_rates_c(c, inst)[1] * abs(r.qty) if inst is not None else 0
        mm_total += mm
        fut_req += int(m["futures_required_c"])
        fut_posted += int(m["futures_posted_c"])
        opt_cov = max(int(m["options_posted_c"]), int(m.get("options_reserved_c", 0) or 0))   # SR-13: reserved = covered
        opt_req += int(m["options_required_c"])
        opt_posted += opt_cov
        coll += int(ob.collateral_c)
        otc_im += int(m["otc_im_c"])
        otc_vm_p += int(m["otc_vm_posted_c"])
        otc_vm_r += int(m["otc_vm_received_c"])
        avail = int(liq.detail.get("accounts", {}).get(aid, 0))
        per_account[aid] = {"name": names.get(aid, aid), "available_c": avail, "nav_c": a_nav, "futures_required_c": int(m["futures_required_c"]),
                            "futures_posted_c": int(m["futures_posted_c"]), "maintenance_c": mm, "options_required_c": int(m["options_required_c"]),
                            "options_posted_c": int(m["options_posted_c"]), "options_reserved_c": int(m.get("options_reserved_c", 0) or 0),
                            "collateral_c": int(ob.collateral_c), "otc_im_c": int(m["otc_im_c"]),
                            "otc_vm_posted_c": int(m["otc_vm_posted_c"]), "otc_vm_received_c": int(m["otc_vm_received_c"])}
        required = int(m["futures_required_c"]) + int(m["options_required_c"]) + int(m["otc_im_c"])
        posted = int(m["futures_posted_c"]) + opt_cov + int(m["otc_im_c"]) + int(m["otc_vm_posted_c"])
        if a.get("kind") == "PB":
            bp = acc.buying_power(aid, "USD", snapshot=snap)
            financed = _gl_sum(services, [aid], "2700")
            long_mv = sum(r.mv_base_c for r in rows if r.account_id == aid and r.mv_base_c and r.mv_base_c > 0 and r.kind != "SWAP")
            on = bool(int(a.get("pb_financing") or 0))
            limit_c = int(a.get("pb_limit_c") or 0)
            head_gross = int(getattr(bp, "pb_headroom_gross_c", bp.pb_headroom_c))      # the §12.4 headroom the formula below describes
            s = _sum_rows([r for r in rows if r.account_id == aid])
            pb = {"account_id": aid, "name": names.get(aid, aid), "broker": a.get("broker"), "pb_financing": on, "pb_limit_c": limit_c, "headroom_c": head_gross,
                  "headroom_after_committed_c": int(bp.pb_headroom_c), "financed_c": financed, "long_mv_c": long_mv, "otc_im_c": int(m["otc_im_c"]),
                  "otc_vm_posted_c": int(m["otc_vm_posted_c"]), "otc_vm_received_c": int(m["otc_vm_received_c"]), "nav_c": a_nav, "contracts": len(ob.otc),
                  "limit_text": (fmt_money(limit_c) if limit_c else "no cap (0 = no cap)"),
                  "formula": "50 % of the PB account's long market value - PB financing drawn - OTC IM - OTC VM posted (capped by the PB limit)" if on
                  else "PB financing is off: headroom 0 (switch it on in Cash & financing or Settings > Accounts)"}
            pb.update(s)
            pb.update(_ratios(s["long_c"], s["short_c"], s["gross_c"], s["net_c"], s["gross_notional_c"], a_nav))
            pb["trs_pct_nav"] = _pct_signed(s["trs_notional_c"], a_nav)
            pb_rows.append(pb)
            note = (f"PB financing on; limit {fmt_money(limit_c) if limit_c else 'none (no cap)'}; drawn {fmt_money(financed)}" if on
                    else "PB financing off") + "; OTC IM / VM included"
            margin_rows.append({"account_id": aid, "name": names.get(aid, aid), "required_c": required, "posted_c": posted, "headroom_c": head_gross, "note": note})
            otc_rows = [r for r in rows if r.account_id == aid and r.kind == "SWAP"]
            if otc_rows or ob.otc:
                dv_known = [r.dv01_c for r in otc_rows if r.dv01_c is not None]
                nxt = _next_events(services, ob.otc)
                pass_vm = int(m["otc_vm_posted_c"])
                by_cid = {r.instrument_id: r for r in otc_rows}
                nxt_by = {e.get("contract_id"): e.get("events") for e in (nxt or [])}
                pb_otc = {"account_id": aid, "name": names.get(aid, aid), "broker": a.get("broker"), "contracts": len(ob.otc),
                          "notional_c": sum(abs(int(c["notional_c"])) for c in ob.otc),
                          "dv01_c": sum(dv_known) if otc_rows and len(dv_known) == len(otc_rows) else None, "dv01_partial": len(dv_known) != len(otc_rows),
                          "mark_c": sum(int(c["mark_c"] or 0) for c in ob.otc), "vm_balance_c": sum(int(c["vm_balance_c"] or 0) for c in ob.otc), "im_c": int(m["otc_im_c"]),
                          "vm_posted_c": pass_vm, "vm_received_c": int(m["otc_vm_received_c"]), "maturities": sorted(c["maturity_date"] for c in ob.otc), "next_events": nxt,
                          "contracts_detail": [_otc_detail(services, c, by_cid.get(c["contract_id"]), nxt_by.get(c["contract_id"])) for c in ob.otc],
                          "note": "DV01 and exposure from the current (post-reset) terms; VM is collateral, not P&L"}
                if a_nav > 0 and Decimal(pass_vm) / Decimal(a_nav) > THRESHOLDS["OTC_VM"]:
                    pct = (Decimal(pass_vm) / Decimal(a_nav)).quantize(PCT4)
                    warnings.append(_w("OTC_VM", f"{names.get(aid, aid)}: OTC variation margin posted {fmt_money(pass_vm)} is {_pct_text(pct)} of the PB account's NAV (limit 10 %)",
                                       aid, None, str(pct), "0.10"))
                per_account[aid]["otc"] = pb_otc
        elif required or posted or int(ob.collateral_c):
            margin_rows.append({"account_id": aid, "name": names.get(aid, aid), "required_c": required, "posted_c": posted, "headroom_c": avail,
                                "note": "futures initial margin (static table) + option margin vs available cash"})
        if a_nav > 0 and int(m["options_required_c"]) and Decimal(int(m["options_required_c"])) / Decimal(a_nav) > THRESHOLDS["OPTION_MARGIN"]:
            pct = (Decimal(int(m["options_required_c"])) / Decimal(a_nav)).quantize(PCT4)
            warnings.append(_w("OPTION_MARGIN", f"{names.get(aid, aid)}: short-option margin {fmt_money(int(m['options_required_c']))} is {_pct_text(pct)} of the account's NAV "
                                                f"(limit 50 %)", aid, None, str(pct), "0.50"))
    for key, s in sorted(liq.detail.get("maintenance", {}).items()):
        aid = key.split(" ")[0]
        warnings.append(_w("FUTURES_MAINTENANCE", f"{names.get(aid, aid)} {key.split(' ')[-1]}: maintenance margin shortfall {fmt_money(int(s))} (posted initial margin below "
                                                  f"maintenance; never liquidated here)", aid, key.split(" ")[-1], str(from_c(int(s))), "0"))
    loans = _gl_sum(services, [aid for aid, a in accounts.items() if a.get("kind") != "TREASURY"], "2750")
    capital = 0
    for lid in nav.lifetime_ids:
        try:
            capital += int(acc.treasury(lid, snapshot=snap).capital_c)
        except Exception:   # noqa: BLE001 - an archived lifetime without a Treasury row contributes nothing
            pass
    if capital > 0 and loans and Decimal(loans) / Decimal(capital) > THRESHOLDS["LOANS"]:
        pct = (Decimal(loans) / Decimal(capital)).quantize(PCT4)
        warnings.append(_w("LOANS", f"currency loans {fmt_money(loans)} are {_pct_text(pct)} of Treasury capital (limit 50 %)", None, None, str(pct), "0.50"))
    fin = {"excess_liquidity_c": int(liq.excess_liquidity_c), "encumbrance_c": int(liq.encumbrance_c), "treasury_available_c": int(liq.treasury_available_c),
           "accounts_available_c": int(liq.accounts_available_c), "maintenance_shortfall_c": int(liq.maintenance_shortfall_c), "maintenance_required_c": mm_total,
           "margin_excess_c": int(nav.nav_c) - mm_total, "futures_im_required_c": fut_req, "futures_im_posted_c": fut_posted, "options_margin_required_c": opt_req,
           "options_margin_posted_c": opt_posted, "collateral_posted_c": coll, "otc_im_c": otc_im, "otc_vm_posted_c": otc_vm_p, "otc_vm_received_c": otc_vm_r, "loans_c": loans,
           "treasury_capital_c": capital, "pb": pb_rows, "accounts": per_account,
           "formulas": dict(liq.formulas, margin_excess="NAV - sum of futures maintenance margin (static table, or your override)",
                            pb_headroom="50 % of the PB account's long market value - PB financing drawn - OTC IM - OTC VM posted (Reg-T style, disclosed)",
                            loans="currency loan principal against the Treasury's contributed capital")}
    otc = [per_account[aid]["otc"] for aid in sorted(per_account) if "otc" in per_account[aid]]
    return fin, margin_rows, otc, warnings


def _otc_detail(services, c: dict, r: Optional[PosRisk], events: Optional[list]) -> dict:
    """One OTC contract of the per-PB block: its readable label, notional, signed DV01, mark, VM balance, IM, maturity and next event."""
    inst = services.instruments.get(c["contract_id"])
    label = r.label if r is not None and r.label else contract_label(inst, {"kind": c["kind"], "maturity_date": c["maturity_date"]})
    nxt = None
    for e in events or []:
        if isinstance(e, dict):
            nxt = {"date": e.get("date") or e.get("event_date"), "kind": e.get("kind") or e.get("event"), "text": e.get("text") or e.get("label")}
            break
    return {"contract_id": c["contract_id"], "label": label, "kind": c["kind"], "notional_c": int(c["notional_c"]), "currency": c["currency"], "mark_c": int(c["mark_c"] or 0),
            "vm_balance_c": int(c["vm_balance_c"] or 0), "im_c": int(c["im_c"] or 0), "maturity_date": c["maturity_date"], "margin_mode": c["margin_mode"],
            "last_reset_date": c["last_reset_date"], "dv01_c": None if r is None else r.dv01_c, "next_event": nxt}


def _next_events(services, contracts: List[dict]) -> Optional[list]:
    swaps = getattr(services, "swaps", None)
    if swaps is None or not hasattr(swaps, "next_events"):
        return None
    out = []
    for c in contracts:
        try:
            out.append({"contract_id": c["contract_id"], "events": swaps.next_events(services, c)})
        except Exception as e:   # noqa: BLE001 - disclosed per contract
            out.append({"contract_id": c["contract_id"], "events": None, "error": f"{type(e).__name__}: {e}"})
    return out


def _private(rows: List[PosRisk], nav_c: int) -> dict:
    """The private / illiquid bucket: private shares and private loans at their (unshocked) marks, or AT COST when they have no
    mark (NAV carries them at cost, SR-03), % of NAV, top names, the lock-up calendar from the lots' ``lock_until``."""
    pos = [r for r in rows if r.kind in ("PRIVATE", "PRIVATE_LOAN")]
    mv = sum(r.exposure_c or 0 for r in pos)
    unpriced = [r.instrument_id for r in pos if r.mv_base_c is None]
    plist = sorted(pos, key=lambda r: -abs(r.exposure_c or 0))
    positions = [{"instrument_id": r.instrument_id, "name": r.name, "symbol": r.symbol, "account_id": r.account_id, "kind": r.kind, "qty": r.qty, "mv_c": r.exposure_c,
                  "pct_nav": None if r.exposure_c is None else _pct(r.exposure_c, nav_c), "lock_until": r.lock_until, "quality": r.quality,
                  "basis": r.exposure_basis, "at_cost": r.at_cost} for r in plist]
    lockups = sorted(({"instrument_id": r.instrument_id, "name": r.name, "account_id": r.account_id, "lot_id": l["lot_id"], "qty": l["qty_remaining"], "lock_until": l["lock_until"]}
                      for r in pos for l in r.lots if l.get("lock_until")), key=lambda x: (x["lock_until"], x["instrument_id"], x["lot_id"]))
    at_cost = sum(r.exposure_c or 0 for r in pos if r.at_cost)
    return {"mv_c": mv, "pct_nav": _pct(mv, nav_c) if pos else (Decimal(0).quantize(PCT4) if nav_c > 0 else None), "positions": positions, "lockups": lockups,
            "top_names": [p["name"] for p in positions[:5]], "unpriced": unpriced, "at_cost_c": at_cost,
            "note": PRIVATE_NOTE + ("; positions without a price per share are carried at cost, as NAV carries them" if unpriced else "")}


# ---- the preview hook ------------------------------------------------------------------------------------------------------------
def pretrade(services, account_id: str, inst, side: str, qty: int, pv: Optional[dict] = None) -> List[dict]:
    """Risk lines for the ticket (``orders.preview`` / ``private.preview`` → ``pv["risk"]``): concentration after the trade (INFO
    below 25 %, WARN above), leverage after the trade (WARN above 2x NAV), a stale / unavailable quote, private / illiquid after a
    private buy (WARN above 30 %; unpriced private positions at cost), futures maintenance after the trade (INFO: the signed
    position after the trade x the maintenance margin per contract, ORC-1), short-option margin already above 50 % (WARN).  Never
    raises for a missing figure: the line says what is unavailable."""
    pv = pv or {}
    acc = services.accounting
    lid, _role = split_account_id(account_id)
    snap = services.marks.snapshot()
    scope = f"{lid}:ALL"
    nav_c = int(acc.nav(scope, snapshot=snap).nav_c)
    positions = acc.positions(scope, snapshot=snap)
    rows = [_pos_risk(services, snap, p, snap.as_of()) for p in positions]
    out: List[dict] = []
    side = (side or "").upper()
    buy = side in ("BUY", "BTO", "BUY_TO_COVER", "BTC")
    notional_c = _order_notional_c(inst, qty, pv, snap)
    existing = sum(r.exposure_c for r in rows if r.instrument_id == inst.instrument_id and r.exposure_c is not None and r.headline)
    gross_now = sum(abs(r.exposure_c) for r in rows if r.exposure_c is not None and r.headline)
    if notional_c is None:
        out.append(_w("CONCENTRATION", f"{inst.symbol}: no price to size the order's exposure (quote unavailable)", account_id, inst.instrument_id, None, "0.25", SEVERITY_INFO))
    else:
        after = existing + (notional_c if buy else -notional_c)
        pct = _pct(after, nav_c)
        if pct is None:
            out.append(_w("CONCENTRATION", f"{inst.symbol}: NAV is not positive; concentration cannot be measured", account_id, inst.instrument_id, None, "0.25", SEVERITY_INFO))
        else:
            over = pct > THRESHOLDS["CONCENTRATION"]
            out.append(_w("CONCENTRATION", f"{inst.symbol} would be {_pct_text(pct)} of the book's NAV {fmt_money(nav_c)}" + (" (above the 25 % limit)" if over else ""),
                          account_id, inst.instrument_id, str(pct), "0.25", SEVERITY_WARN if over else SEVERITY_INFO))
        gross_after = gross_now - abs(existing) + abs(after)
        lev = None if nav_c <= 0 else (Decimal(gross_after) / Decimal(nav_c)).quantize(PCT4)
        if lev is not None and lev > THRESHOLDS["LEVERAGE"]:
            out.append(_w("LEVERAGE", f"gross exposure after the trade would be {lev}x NAV (limit 2x)", account_id, inst.instrument_id, str(lev), "2"))
        if inst.kind == "PRIVATE" and buy:
            priv = sum(r.exposure_c or 0 for r in rows if r.kind in ("PRIVATE", "PRIVATE_LOAN")) + notional_c       # unpriced names at cost (SR-03)
            ppct = _pct(priv, nav_c)
            if ppct is not None and ppct > THRESHOLDS["PRIVATE_ILLIQUID"]:
                out.append(_w("PRIVATE_ILLIQUID", f"private / illiquid positions would be {_pct_text(ppct)} of NAV after this trade (limit 30 %)", account_id, inst.instrument_id,
                              str(ppct), "0.30"))
    q = (pv.get("quote") or {}).get("quality") if isinstance(pv.get("quote"), dict) else None
    if q in ("stale", "unavailable"):
        out.append(_w("STALE_MARK", f"{inst.symbol}: the quote is {q}; the valuation keeps the last valid mark with its time", account_id, inst.instrument_id, q, "fresh", SEVERITY_INFO))
    if inst.kind == "FUTURE":
        out.append(_maintenance_line(services, rows, account_id, inst, buy, int(qty)))
    if inst.kind == "OPTION":
        ob = acc.obligations(account_id)
        req = int(ob.margin["options_required_c"])
        a_nav = int(acc.nav(account_id, snapshot=snap).nav_c)
        if req and a_nav > 0 and Decimal(req) / Decimal(a_nav) > THRESHOLDS["OPTION_MARGIN"]:
            pct = (Decimal(req) / Decimal(a_nav)).quantize(PCT4)
            out.append(_w("OPTION_MARGIN", f"short-option margin {fmt_money(req)} is already {_pct_text(pct)} of the account's NAV (limit 50 %); it is recomputed after the fill",
                          account_id, inst.instrument_id, str(pct), "0.50"))
    return out


def _maintenance_line(services, rows: List[PosRisk], account_id: str, inst, buy: bool, qty: int) -> dict:
    """ORC-1: maintenance margin after the trade = maintenance per contract (your Settings override, else the static table) x
    |held + signed order quantity|; a closing order shows the margin left after the close (0.00 when flat).  Informational."""
    from .accounting import futures_rates_c
    with shared_reader(services.db) as c:
        _im, mm = futures_rates_c(c, inst)
    held = sum(r.qty for r in rows if r.account_id == account_id and r.instrument_id == inst.instrument_id)
    signed = qty if buy else -qty
    after = held + signed
    req = int(mm) * abs(after)
    move = f"{held:+,} held, {abs(signed):,} {'bought' if buy else 'sold'}"
    if after == 0:
        text = f"maintenance margin after the trade {fmt_money(0)}: flat after the trade ({move})"
    else:
        where = "long" if after > 0 else "short"
        text = (f"maintenance margin after the trade {fmt_money(req)}: {abs(after):,} contract{'s' if abs(after) != 1 else ''} {where} after the trade ({move}) "
                f"x {fmt_money(int(mm))} per contract (static table or your override)")
    return _w("FUTURES_MAINTENANCE", text, account_id, inst.instrument_id, str(from_c(req)), None, SEVERITY_INFO)


warnings_for_preview = pretrade


def _order_notional_c(inst, qty: int, pv: dict, snap) -> Optional[int]:
    """The order's exposure in USD: the preview's gross when it has one, else qty × mark × multiplier at the snapshot."""
    econ = pv.get("economics") if isinstance(pv.get("economics"), dict) else None
    if econ and econ.get("gross") not in (None, ""):
        try:
            native = to_c(Decimal(str(econ["gross"])))
        except Exception:   # noqa: BLE001
            native = None
        if native is not None:
            rate = _rate_of(snap, inst.currency)
            return None if rate is None else fx_c(native, rate)
    mark = _mark_of(snap, inst.instrument_id)
    rate = _rate_of(snap, inst.currency)
    if mark is None or rate is None:
        return None
    return fx_c(mul_c(abs(int(qty)), mark, inst.multiplier, inst.price_scale), rate)


__all__ = ["RiskView", "PosRisk", "report", "pretrade", "warnings_for_preview", "jsonable", "contract_label", "THRESHOLDS", "EQUITY_SHOCKS", "RATE_SHOCKS_BP",
           "VOL_SHOCKS", "FX_SHOCKS", "SCENARIO_NOTES", "DEFINITIONS", "AT_COST"]
