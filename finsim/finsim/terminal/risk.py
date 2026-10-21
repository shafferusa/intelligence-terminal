"""Risk view and risk warnings — inform only (TERMINAL_DESIGN.md §14, §2.12 ``risk.py``; TERMINAL_DECISIONS §4 "risk only warns").

Conventions
- Every figure ties to ``accounting.positions`` on ONE mark snapshot: ``report`` takes the snapshot once and passes it
  to every read model it calls (positions, NAV, liquidity, obligations, buying power), so the Risk page, Portfolio and
  Accounting show the same marks.  Nothing here writes, nothing here fetches, nothing here blocks.
- Exposure of a position (USD base, signed): securities, private shares and private loans at market value; futures at
  notional (qty × mark × multiplier); listed options at delta-USD (qty × multiplier × δ × S) when a delta is known,
  else at market value (noted on the row); TRS at ± notional (the underlying exposure).  IRS and cross-currency swaps
  are shown in their own bucket at notional but are NOT in the headline long / short / gross / net: their measures are
  DV01 and FX.  ``exposures`` (by bucket), ``exposure_by_class``, ``exposure_by_account`` and ``exposure_by_currency``
  all sum the same per-position rows (``positions`` in the view).
- Greeks per contract come from the Cboe chain row of the contract when a chain is cached (δ, γ, θ per calendar day,
  vega per vol point, as Cboe reports them), else from ``finsim.engines.options_pricing.greeks`` on the mark's model
  inputs, else they are unavailable (listed under ``greeks.unavailable``, never approximated).  Totals, each × contracts
  × multiplier and signed: delta in shares and in USD (δ × S), gamma in USD (the change of delta-USD for a 1 % move,
  γ × S² / 100), vega USD per vol point, theta USD per calendar day.
- DV01 is the P&L for +1 bp (signed, ``dv01_signed``): rates futures ZT / ZF / ZN / ZB = −qty × D × price × multiplier
  / 10 000 with D from ``tables.FUT_DV01`` (an approximate duration table, labelled); swaps from ``swaps.dv01_c`` as
  the accounting position carries it (``extra.dv01_c``, the PV change for +1 bp as booked); bonds are Build 2 (listed
  as unpriced).  The ``dv01`` tile is the magnitude.
- Scenarios re-price through the same functions as the marks: equities / ETFs / equity-index futures / TRS linear in
  the shock; options through ``marks.price_option`` on the mark's model inputs (S shocked, σ shifted; the base is the
  model price from the same inputs); rates through DV01 × bp (a full re-mark through ``swaps.mark`` with a shifted
  curve waits for that module); FX ±5 % on the USD value of every non-USD exposure.  Private marks and private loans
  are never shocked (every row says so).  A position that cannot be re-priced is listed under ``unpriced`` for that row.
- Warnings (ticket preview, Risk page, Attention strip) never block: concentration > 25 % of NAV, gross > 2 × NAV,
  option margin > 50 % of the account's NAV, stale or unpriced marks, currency loans > 50 % of Treasury capital, a
  futures maintenance shortfall, OTC VM posted > 10 % of the PB account's NAV, private / illiquid > 30 % of NAV.
- JSON (``to_dict``): cents fields become decimal strings under the name without ``_c``; fractions (``pct_nav``) are
  4-dp decimal strings; nothing is a float.  The preview hook is ``pretrade`` (``orders.preview`` calls
  ``services.risk.pretrade``); ``warnings_for_preview`` is the same function under the work plan's name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from finsim.engines import options_pricing as _op

from . import tables
from .books import split_account_id
from .instruments import EQUITY_KINDS
from .ledger import CHART, natural
from .marks import price_option
from .units import fmt_money, from_c, fx_c, mul_c, qprice, to_c

PCT4 = Decimal("0.0001")
ONE = Decimal(1)
EQUITY_SHOCKS = (Decimal("-0.20"), Decimal("-0.10"), Decimal("-0.05"), Decimal("0.05"), Decimal("0.10"), Decimal("0.20"))
RATE_SHOCKS_BP = (-100, -50, -25, 25, 50, 100)
VOL_SHOCKS = (Decimal("-0.05"), Decimal("0.05"))
FX_SHOCKS = (Decimal("-0.05"), Decimal("0.05"))
THRESHOLDS = {"CONCENTRATION": Decimal("0.25"), "LEVERAGE": Decimal("2"), "OPTION_MARGIN": Decimal("0.50"), "LOANS": Decimal("0.50"),
              "OTC_VM": Decimal("0.10"), "PRIVATE_ILLIQUID": Decimal("0.30")}
FUTURES_BUCKET = {"CME_EQUITY_INDEX": "Index futures (notional)", "CME_RATES": "Rates futures (notional)", "NYMEX_ENERGY": "Energy futures (notional)",
                  "COMEX_METALS": "Metals futures (notional)", "CBOT_GRAINS": "Grain futures (notional)", "CME_FX": "FX futures (notional)"}
BUCKET_ORDER = ["US equities", "Bonds", "Index futures (notional)", "Rates futures (notional)", "Energy futures (notional)", "Metals futures (notional)",
                "Grain futures (notional)", "FX futures (notional)", "Listed options (delta USD)", "TRS (underlying exposure)", "OTC swaps (notional)",
                "Private / illiquid", "Private credit"]
COST_GLS = ("1100", "1110", "1120", "1130", "1700", "2600", "2900")
NOTE = "warnings only - this page informs; funding, borrow and recall mechanics still apply"
PRIVATE_NOTE = "private marks and private loans are not shocked (last-round / model marks)"
SEVERITY_WARN = "WARN"
SEVERITY_INFO = "INFO"


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


def _pos_risk(services, snap, p, now: int) -> PosRisk:
    inst = services.instruments.get(p.instrument_id)
    rate = _rate_of(snap, p.currency)
    kind = p.kind
    mult = inst.multiplier if inst else ONE
    scale = inst.price_scale if inst else ONE
    group = inst.session_group if inst else ""
    mark = p.mark.mark if p.mark is not None else None
    extra = dict(p.extra or {})
    r = PosRisk(p.account_id, p.instrument_id, p.symbol, p.name, kind, p.klass, "", p.currency, int(p.qty), rate, p.mv_c, p.mv_base_c,
                None if p.notional_c is None or rate is None else fx_c(int(p.notional_c), rate), None, "", True, p.mark.quality if p.mark else "unavailable", mark,
                inst.underlying_id if inst else None, None, mult, scale, group, extra=extra,
                lots=[{"lot_id": l.lot_id, "qty_remaining": l.qty_remaining, "open_date": l.open_date, "lock_until": l.lock_until} for l in (p.lots or [])])
    r.lock_until = extra.get("lock_until") or (max((l.lock_until or "") for l in (p.lots or [])) or None if p.lots else None)
    if kind in EQUITY_KINDS:
        r.bucket, r.exposure_c, r.exposure_basis = "US equities", p.mv_base_c, "market value"
    elif kind == "BOND":
        r.bucket, r.exposure_c, r.exposure_basis = "Bonds", p.mv_base_c, "market value (computed)"
        r.dv01_basis = "bonds: DV01 is Build 2"
    elif kind == "FUTURE":
        r.bucket = FUTURES_BUCKET.get(group, "Futures (notional)")
        r.exposure_c, r.exposure_basis = r.notional_base_c, "notional"
        root = (inst.spec or {}).get("root") if inst else None
        dur = tables.FUT_DV01.get(root) if root else None
        if dur is not None and mark is not None and rate is not None:
            dv = to_c(Decimal(int(p.qty)) * dur * mark * mult / Decimal(10000))
            r.dv01_c, r.dv01_basis = -fx_c(dv, rate), f"approximate: {root} duration {dur} x price x multiplier / 10,000 per contract (tables.FUT_DV01)"
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
        if r.greeks is not None and S is not None and rate is not None:
            n = Decimal(int(p.qty)) * mult
            r.exposure_c = fx_c(to_c(n * r.greeks["delta"] * S), rate)
            r.exposure_basis = f"delta USD ({r.greeks_source})"
        else:
            r.exposure_c, r.exposure_basis = p.mv_base_c, "market value (no delta available)"
    elif kind == "SWAP":
        swap_kind = extra.get("kind")
        sign = 1 if int(p.qty) >= 0 else -1
        if swap_kind == "TRS":
            r.bucket, r.exposure_c, r.exposure_basis = "TRS (underlying exposure)", (None if r.notional_base_c is None else sign * abs(r.notional_base_c)), "notional (underlying)"
        else:
            r.bucket, r.headline = "OTC swaps (notional)", False
            r.exposure_c, r.exposure_basis = (None if r.notional_base_c is None else sign * abs(r.notional_base_c)), "notional (not in the headline exposure)"
        dv = extra.get("dv01_c")
        if dv is not None:
            r.dv01_c, r.dv01_basis = int(dv), "swaps.dv01_c (PV change for +1 bp, as booked)"
        elif swap_kind != "TRS":
            r.dv01_basis = "DV01 unavailable (swaps module absent or no curve)"
    elif kind == "PRIVATE":
        r.bucket, r.exposure_c, r.exposure_basis = "Private / illiquid", p.mv_base_c, "last-round mark (not shocked)"
    elif kind == "PRIVATE_LOAN":
        r.bucket, r.exposure_c, r.exposure_basis = "Private credit", p.mv_base_c, "model mark (not shocked)"
    else:
        r.bucket, r.exposure_c, r.exposure_basis = p.klass, p.mv_base_c, "market value"
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
        elif r.kind == "SWAP" and r.extra.get("kind") == "TRS":
            v = None if r.exposure_c is None else to_c(from_c(r.exposure_c) * e)
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


def _scenario_rates(rows: List[PosRisk], bp: int) -> dict:
    pnl = 0
    by_class: Dict[str, int] = {}
    unpriced: List[str] = []
    for r in rows:
        if r.dv01_c is not None:
            v = r.dv01_c * bp
            pnl += v
            by_class[r.klass] = by_class.get(r.klass, 0) + v
        elif r.kind == "BOND" or (r.kind == "SWAP" and r.extra.get("kind") != "TRS") or (r.kind == "FUTURE" and r.session_group == "CME_RATES"):
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

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["positions"] = [p.to_dict() for p in self.positions]
        return jsonable(d)


def _accounts(services, ids: List[str]) -> Dict[str, dict]:
    if not ids:
        return {}
    q = ",".join("?" for _ in ids)
    with services.db.reader() as conn:
        return {r["account_id"]: dict(r) for r in conn.execute(f"SELECT * FROM accounts WHERE account_id IN ({q})", list(ids)).fetchall()}


def _currency_balances(services, ids: List[str]) -> Dict[str, int]:
    """Σ (assets − liabilities) at cost per non-USD currency over the accounts, excluding the cost accounts that market
    values replace (storage sign is debit-positive, so a plain sum is assets − liabilities)."""
    if not ids:
        return {}
    q = ",".join("?" for _ in ids)
    out: Dict[str, int] = {}
    with services.db.reader() as conn:
        for r in conn.execute(f"SELECT gl, currency, SUM(balance_c) b FROM gl_balances WHERE account_id IN ({q}) AND currency <> 'USD' GROUP BY gl, currency", list(ids)).fetchall():
            spec = CHART.get(r["gl"])
            if spec is None or spec.kind not in ("ASSET", "LIABILITY") or r["gl"] in COST_GLS:
                continue
            out[r["currency"]] = out.get(r["currency"], 0) + int(r["b"] or 0)
    return out


def _gl_sum(services, ids: List[str], gl: str, base: bool = True) -> int:
    if not ids:
        return 0
    q = ",".join("?" for _ in ids)
    col = "base_c" if base else "balance_c"
    with services.db.reader() as conn:
        row = conn.execute(f"SELECT COALESCE(SUM({col}),0) FROM gl_balances WHERE account_id IN ({q}) AND gl=?", list(ids) + [gl]).fetchone()
    return natural(gl, int(row[0] or 0))


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
    rows = [_pos_risk(services, snap, p, as_of) for p in positions]
    nav_c = int(nav.nav_c)
    # exposures
    head = [r for r in rows if r.headline]
    by_bucket = _agg(rows, lambda r: r.bucket)
    by_class = _agg(rows, lambda r: r.klass)
    by_account = _agg(head, lambda r: r.account_id)
    for aid, b in by_account.items():
        b["nav_c"] = int(nav.by_account.get(aid, 0))
        b["pct_nav"] = _pct(b["gross_c"], nav_c)
    totals = _agg(head, lambda r: "ALL").get("ALL", {"long_c": 0, "short_c": 0, "net_c": 0, "gross_c": 0})
    # currency exposure: non-USD cash & receivables at cost + positions at market, in USD at the snapshot's rate
    native = _currency_balances(services, account_ids)
    for r in rows:
        if r.currency != "USD" and r.mv_c is not None:
            native[r.currency] = native.get(r.currency, 0) + int(r.mv_c)
    by_ccy: Dict[str, dict] = {}
    fx_rows: List[dict] = []
    for ccy in sorted(native):
        rate = _rate_of(snap, ccy)
        usd = None if rate is None else fx_c(native[ccy], rate)
        by_ccy[ccy] = {"native_c": native[ccy], "usd_c": usd, "rate": rate, "pct_nav": None if usd is None else _pct(usd, nav_c)}
        fx_rows.append(dict(by_ccy[ccy], currency=ccy))
        if usd is not None:
            b = by_bucket.setdefault(f"FX ({ccy})", {"long_c": 0, "short_c": 0, "net_c": 0, "gross_c": 0, "n": 0})
            b["long_c" if usd >= 0 else "short_c"] += abs(usd)
            b["net_c"] += usd
            b["gross_c"] += abs(usd)
    exposures = []
    for bucket in BUCKET_ORDER + sorted(k for k in by_bucket if k not in BUCKET_ORDER):
        b = by_bucket.get(bucket)
        if b is None:
            continue
        exposures.append({"bucket": bucket, "long_c": b["long_c"], "short_c": b["short_c"], "net_c": b["net_c"], "gross_c": b["gross_c"], "pct_nav": _pct(b["gross_c"], nav_c),
                          "headline": not bucket.startswith("OTC swaps")})
    # greeks and DV01
    gk = _greeks_total(rows, lambda c: _rate_of(snap, c))
    dv01_rows = [{"instrument_id": r.instrument_id, "account_id": r.account_id, "kind": r.kind, "qty": r.qty, "dv01_c": r.dv01_c, "basis": r.dv01_basis}
                 for r in rows if r.dv01_c is not None or r.dv01_basis]
    dv01_signed = sum(r.dv01_c for r in rows if r.dv01_c is not None)
    # scenarios
    scenarios: List[dict] = []
    for e in EQUITY_SHOCKS:
        s = _scenario_equity(rows, e)
        scenarios.append({"name": f"equities {_shock_text(e)}", "shock": _shock_text(e), "pnl_c": s["pnl_c"], "detail": {"by_class": s["by_class"]}, "unpriced": s["unpriced"],
                          "unchanged": s["unchanged"], "note": "equities / ETFs / index futures / TRS linear; options through the option model; " + PRIVATE_NOTE})
    for bp in RATE_SHOCKS_BP:
        s = _scenario_rates(rows, bp)
        scenarios.append({"name": f"rates {_shock_text(Decimal(bp), 'bp')}", "shock": _shock_text(Decimal(bp), "bp"), "pnl_c": s["pnl_c"], "detail": {"by_class": s["by_class"]},
                          "unpriced": s["unpriced"], "unchanged": [], "note": "DV01 x bp (rates futures from the duration table, swaps from swaps.dv01_c); bonds Build 2; option rho ignored"})
    for dv in VOL_SHOCKS:
        s = _scenario_vol(rows, dv)
        scenarios.append({"name": f"vol {_shock_text(dv, 'pts')}", "shock": _shock_text(dv, "pts"), "pnl_c": s["pnl_c"], "detail": {"by_class": s["by_class"]}, "unpriced": s["unpriced"],
                          "unchanged": [], "note": "options re-priced with sigma shifted; everything else unchanged"})
    fx_total = sum(v["usd_c"] for v in by_ccy.values() if v["usd_c"] is not None)
    for f in FX_SHOCKS:
        pnl = to_c(from_c(fx_total) * f)
        scenarios.append({"name": f"USD {_shock_text(-f)} vs foreign currencies", "shock": _shock_text(f) + " foreign", "pnl_c": pnl,
                          "detail": {"by_currency": {c: to_c(from_c(v["usd_c"]) * f) for c, v in by_ccy.items() if v["usd_c"] is not None}},
                          "unpriced": [c for c, v in by_ccy.items() if v["usd_c"] is None], "unchanged": [], "note": "every non-USD cash and position exposure at the shifted rate"})
    # concentration
    conc = sorted((r for r in rows if r.exposure_c is not None and r.headline), key=lambda r: -abs(r.exposure_c))[:10]
    concentration = [{"instrument_id": r.instrument_id, "symbol": r.symbol, "account_id": r.account_id, "mv_c": r.exposure_c, "pct_nav": _pct(r.exposure_c, nav_c),
                      "note": r.exposure_basis if r.exposure_basis != "market value" else ""} for r in conc]
    # financing, margin, OTC, private
    fin, margin, otc, warnings = _financing(services, acc, snap, rows, accounts, nav, liq)
    private = _private(rows, nav_c)
    # warnings
    lev = None if nav_c <= 0 else (Decimal(totals["gross_c"]) / Decimal(nav_c)).quantize(PCT4)
    for r in conc:
        pct = _pct(r.exposure_c, nav_c)
        if pct is not None and pct > THRESHOLDS["CONCENTRATION"]:
            warnings.append(_w("CONCENTRATION", f"{r.symbol} {r.exposure_basis if r.exposure_basis != 'market value' else ''} is {_pct_text(pct)} of NAV (limit 25 %)".replace("  ", " "),
                               r.account_id, r.instrument_id, str(pct), "0.25"))
    if lev is not None and lev > THRESHOLDS["LEVERAGE"]:
        warnings.append(_w("LEVERAGE", f"gross exposure is {lev}x NAV (limit 2x)", None, None, str(lev), "2"))
    for r in rows:
        if r.quality == "stale":
            warnings.append(_w("STALE_MARK", f"{r.symbol} is marked on a stale quote; its P&L may be off", r.account_id, r.instrument_id, "stale", "fresh"))
        elif r.quality == "unavailable":
            warnings.append(_w("UNPRICED", f"{r.symbol} has no valid mark; NAV excludes it", r.account_id, r.instrument_id, "unavailable", "priced"))
    for ccy in nav.fx_missing:
        warnings.append(_w("UNPRICED", f"no USD rate for {ccy}: balances in {ccy} are excluded from NAV", None, None, "unavailable", "rate"))
    if private["pct_nav"] is not None and private["pct_nav"] > THRESHOLDS["PRIVATE_ILLIQUID"]:
        warnings.append(_w("PRIVATE_ILLIQUID", f"private / illiquid positions are {_pct_text(private['pct_nav'])} of NAV (limit 30 %)", None, None, str(private["pct_nav"]), "0.30"))
    warnings.sort(key=lambda w: (0 if w["severity"] == SEVERITY_WARN else 1, w["code"]))
    return RiskView(scope, as_of, nav_c, nav.quality, nav.partial, list(nav.missing), totals["long_c"], totals["short_c"], totals["gross_c"], totals["net_c"], abs(dv01_signed), dv01_signed,
                    dv01_rows, exposures, {k: dict(v) for k, v in by_class.items()}, by_account, by_ccy, fx_rows, gk["total"], gk["rows"], scenarios, concentration, fin, margin, private, otc,
                    warnings, rows, int(liq.excess_liquidity_c), int(liq.encumbrance_c), lev)


def _w(code: str, message: str, account_id: Optional[str], instrument_id: Optional[str], value: Optional[str], threshold: Optional[str], severity: str = SEVERITY_WARN) -> dict:
    return {"code": code, "severity": severity, "message": message, "account_id": account_id, "instrument_id": instrument_id, "value": value, "threshold": threshold}


def _financing(services, acc, snap, rows: List[PosRisk], accounts: Dict[str, dict], nav, liq):
    """Financing & liquidity (§6.0, §13.11 margin headroom per PB incl. OTC IM / VM), the UI margin rows, the OTC block per PB and
    the financing warnings (option margin, maintenance shortfall, OTC VM, loans)."""
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
        for r in rows:
            if r.account_id == aid and r.kind == "FUTURE":
                inst = services.instruments.get(r.instrument_id)
                mm += int((inst.spec or {}).get("mm_c") or 0) * abs(r.qty) if inst else 0
        mm_total += mm
        fut_req += int(m["futures_required_c"])
        fut_posted += int(m["futures_posted_c"])
        opt_req += int(m["options_required_c"])
        opt_posted += int(m["options_posted_c"])
        coll += int(ob.collateral_c)
        otc_im += int(m["otc_im_c"])
        otc_vm_p += int(m["otc_vm_posted_c"])
        otc_vm_r += int(m["otc_vm_received_c"])
        avail = int(liq.detail.get("accounts", {}).get(aid, 0))
        per_account[aid] = {"available_c": avail, "nav_c": a_nav, "futures_required_c": int(m["futures_required_c"]), "futures_posted_c": int(m["futures_posted_c"]),
                            "maintenance_c": mm, "options_required_c": int(m["options_required_c"]), "options_posted_c": int(m["options_posted_c"]), "collateral_c": int(ob.collateral_c),
                            "otc_im_c": int(m["otc_im_c"]), "otc_vm_posted_c": int(m["otc_vm_posted_c"]), "otc_vm_received_c": int(m["otc_vm_received_c"])}
        required = int(m["futures_required_c"]) + int(m["options_required_c"]) + int(m["otc_im_c"])
        posted = int(m["futures_posted_c"]) + int(m["options_posted_c"]) + int(m["otc_im_c"]) + int(m["otc_vm_posted_c"])
        if a.get("kind") == "PB":
            bp = acc.buying_power(aid, "USD", snapshot=snap)
            financed = _gl_sum(services, [aid], "2700")
            long_mv = sum(r.mv_base_c for r in rows if r.account_id == aid and r.mv_base_c and r.mv_base_c > 0 and r.kind != "SWAP")
            on = bool(int(a.get("pb_financing") or 0))
            pb_rows.append({"account_id": aid, "broker": a.get("broker"), "pb_financing": on, "pb_limit_c": int(a.get("pb_limit_c") or 0), "headroom_c": int(bp.pb_headroom_c),
                            "financed_c": financed, "long_mv_c": long_mv, "otc_im_c": int(m["otc_im_c"]), "otc_vm_posted_c": int(m["otc_vm_posted_c"]),
                            "otc_vm_received_c": int(m["otc_vm_received_c"]), "nav_c": a_nav, "contracts": len(ob.otc),
                            "formula": "50 % of the PB account's long market value - PB financing drawn - OTC IM - OTC VM posted (capped by pb_limit)" if on else "PB financing is off: headroom 0"})
            note = (f"PB financing on; limit {fmt_money(int(a.get('pb_limit_c') or 0)) if int(a.get('pb_limit_c') or 0) else 'none'}; drawn {fmt_money(financed)}" if on
                    else "PB financing off") + "; OTC IM / VM included"
            margin_rows.append({"account_id": aid, "required_c": required, "posted_c": posted, "headroom_c": int(bp.pb_headroom_c), "note": note})
            otc_rows = [r for r in rows if r.account_id == aid and r.kind == "SWAP"]
            if otc_rows or ob.otc:
                dv_known = [r.dv01_c for r in otc_rows if r.dv01_c is not None]
                nxt = _next_events(services, ob.otc)
                pass_vm = int(m["otc_vm_posted_c"])
                pb_otc = {"account_id": aid, "broker": a.get("broker"), "contracts": len(ob.otc), "notional_c": sum(abs(int(c["notional_c"])) for c in ob.otc),
                          "dv01_c": sum(dv_known) if otc_rows and len(dv_known) == len(otc_rows) else None, "dv01_partial": len(dv_known) != len(otc_rows),
                          "mark_c": sum(int(c["mark_c"] or 0) for c in ob.otc), "vm_balance_c": sum(int(c["vm_balance_c"] or 0) for c in ob.otc), "im_c": int(m["otc_im_c"]),
                          "vm_posted_c": pass_vm, "vm_received_c": int(m["otc_vm_received_c"]), "maturities": sorted(c["maturity_date"] for c in ob.otc), "next_events": nxt,
                          "contracts_detail": [{"contract_id": c["contract_id"], "kind": c["kind"], "notional_c": int(c["notional_c"]), "currency": c["currency"], "mark_c": int(c["mark_c"] or 0),
                                                "vm_balance_c": int(c["vm_balance_c"] or 0), "im_c": int(c["im_c"] or 0), "maturity_date": c["maturity_date"], "margin_mode": c["margin_mode"],
                                                "last_reset_date": c["last_reset_date"]} for c in ob.otc],
                          "note": "DV01 and exposure from the current (post-reset) terms; VM is collateral, not P&L (CTM)"}
                if a_nav > 0 and Decimal(pass_vm) / Decimal(a_nav) > THRESHOLDS["OTC_VM"]:
                    pct = (Decimal(pass_vm) / Decimal(a_nav)).quantize(PCT4)
                    warnings.append(_w("OTC_VM", f"{aid}: OTC variation margin posted {fmt_money(pass_vm)} is {_pct_text(pct)} of the PB account's NAV (limit 10 %)", aid, None, str(pct), "0.10"))
                per_account[aid]["otc"] = pb_otc
        elif required or posted or int(ob.collateral_c):
            margin_rows.append({"account_id": aid, "required_c": required, "posted_c": posted, "headroom_c": avail, "note": "futures IM (static table) + option margin vs available cash"})
        if a_nav > 0 and int(m["options_required_c"]) and Decimal(int(m["options_required_c"])) / Decimal(a_nav) > THRESHOLDS["OPTION_MARGIN"]:
            pct = (Decimal(int(m["options_required_c"])) / Decimal(a_nav)).quantize(PCT4)
            warnings.append(_w("OPTION_MARGIN", f"{aid}: short-option margin {fmt_money(int(m['options_required_c']))} is {_pct_text(pct)} of the account's NAV (limit 50 %)", aid, None, str(pct), "0.50"))
    for key, s in sorted(liq.detail.get("maintenance", {}).items()):
        aid = key.split(" ")[0]
        warnings.append(_w("FUTURES_MAINTENANCE", f"{key}: maintenance margin shortfall {fmt_money(int(s))} (posted IM below maintenance; never liquidated here)", aid, key.split(" ")[-1], str(from_c(int(s))), "0"))
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
           "formulas": dict(liq.formulas, margin_excess="NAV - sum of futures maintenance margin (static table)",
                            pb_headroom="50 % of the PB account's long market value - PB financing drawn - OTC IM - OTC VM posted (Reg-T style, disclosed)",
                            loans="currency loan principal (2750) against Treasury contributed capital")}
    otc = [per_account[aid]["otc"] for aid in sorted(per_account) if "otc" in per_account[aid]]
    return fin, margin_rows, otc, warnings


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
    """The private / illiquid bucket: private shares and private loans at their (unshocked) marks, % of NAV, top names,
    the lock-up calendar from the lots' ``lock_until``."""
    pos = [r for r in rows if r.kind in ("PRIVATE", "PRIVATE_LOAN")]
    mv = sum(r.mv_base_c or 0 for r in pos)
    unpriced = [r.instrument_id for r in pos if r.mv_base_c is None]
    plist = sorted(pos, key=lambda r: -abs(r.mv_base_c or 0))
    positions = [{"instrument_id": r.instrument_id, "name": r.name, "symbol": r.symbol, "account_id": r.account_id, "kind": r.kind, "qty": r.qty, "mv_c": r.mv_base_c,
                  "pct_nav": None if r.mv_base_c is None else _pct(r.mv_base_c, nav_c), "lock_until": r.lock_until, "quality": r.quality} for r in plist]
    lockups = sorted(({"instrument_id": r.instrument_id, "name": r.name, "account_id": r.account_id, "lot_id": l["lot_id"], "qty": l["qty_remaining"], "lock_until": l["lock_until"]}
                      for r in pos for l in r.lots if l.get("lock_until")), key=lambda x: (x["lock_until"], x["instrument_id"], x["lot_id"]))
    return {"mv_c": mv, "pct_nav": _pct(mv, nav_c) if pos else (Decimal(0).quantize(PCT4) if nav_c > 0 else None), "positions": positions, "lockups": lockups,
            "top_names": [p["name"] for p in positions[:5]], "unpriced": unpriced, "note": PRIVATE_NOTE}


# ---- the preview hook ------------------------------------------------------------------------------------------------------------
def pretrade(services, account_id: str, inst, side: str, qty: int, pv: Optional[dict] = None) -> List[dict]:
    """Risk lines for the ticket (``orders.preview`` → ``pv["risk"]``): concentration after the trade (INFO below 25 %,
    WARN above), leverage after the trade (WARN above 2x NAV), a stale / unavailable quote, private / illiquid after a
    private buy (WARN above 30 %), futures maintenance after the trade (INFO), short-option margin already above 50 %
    (WARN).  Never raises for a missing figure: the line says what is unavailable."""
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
            priv = sum(r.mv_base_c or 0 for r in rows if r.kind in ("PRIVATE", "PRIVATE_LOAN")) + notional_c
            ppct = _pct(priv, nav_c)
            if ppct is not None and ppct > THRESHOLDS["PRIVATE_ILLIQUID"]:
                out.append(_w("PRIVATE_ILLIQUID", f"private / illiquid positions would be {_pct_text(ppct)} of NAV after this trade (limit 30 %)", account_id, inst.instrument_id, str(ppct), "0.30"))
    q = (pv.get("quote") or {}).get("quality") if isinstance(pv.get("quote"), dict) else None
    if q in ("stale", "unavailable"):
        out.append(_w("STALE_MARK", f"{inst.symbol}: the quote is {q}; the valuation keeps the last valid mark with its time", account_id, inst.instrument_id, q, "fresh", SEVERITY_INFO))
    if inst.kind == "FUTURE":
        mm = int((inst.spec or {}).get("mm_c") or 0)
        held = sum(abs(r.qty) for r in rows if r.account_id == account_id and r.instrument_id == inst.instrument_id)
        out.append(_w("FUTURES_MAINTENANCE", f"maintenance margin after the trade {fmt_money(mm * (held + int(qty)))} ({int(qty):,} new + {held:,} held contracts x {fmt_money(mm)}, static table)",
                      account_id, inst.instrument_id, str(from_c(mm * (held + int(qty)))), None, SEVERITY_INFO))
    if inst.kind == "OPTION":
        ob = acc.obligations(account_id)
        req = int(ob.margin["options_required_c"])
        a_nav = int(acc.nav(account_id, snapshot=snap).nav_c)
        if req and a_nav > 0 and Decimal(req) / Decimal(a_nav) > THRESHOLDS["OPTION_MARGIN"]:
            pct = (Decimal(req) / Decimal(a_nav)).quantize(PCT4)
            out.append(_w("OPTION_MARGIN", f"short-option margin {fmt_money(req)} is already {_pct_text(pct)} of the account's NAV (limit 50 %); it is recomputed after the fill",
                          account_id, inst.instrument_id, str(pct), "0.50"))
    return out


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


__all__ = ["RiskView", "PosRisk", "report", "pretrade", "warnings_for_preview", "jsonable", "THRESHOLDS", "EQUITY_SHOCKS", "RATE_SHOCKS_BP", "VOL_SHOCKS", "FX_SHOCKS"]
