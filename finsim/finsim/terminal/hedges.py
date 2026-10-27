"""Hedge candidates and hedge packages (TERMINAL_DESIGN.md §2.12 hedges.py, §10.9, §19.9 D7; sentences AON / SEQUENTIAL).

Conventions stated here
- Candidates are computed from the CACHED Cboe chain of the primary's underlying — never fetched on the request
  thread: without a cached chain the panel answers ``{"status": "fetching"}`` (HTTP 202) and the chain cache is asked
  for one; a chain older than 90 s is still used while a refresh is queued, and one older than the feed's delay plus
  grace (15 min) is labelled ``stale``.  Every candidate carries the chain's freshness (``chain_quality``), its
  observation time (``as_of``) and each leg's quote (bid / ask / sizes / greeks with the same freshness fields), so the
  panel shows nothing without its provenance.
- Strikes snap to LISTED strikes (nearest; ties to the lower); the expiry is the nearest listed one at least 90
  calendar days out (else the farthest listed, disclosed).  The ticket's edits (``hedge.edits``: ``strike`` for the
  protective leg, ``expiry``) override the rule: the strike snaps to the listed grid, the expiry must be listed (else
  the option candidates are unavailable with the reason).  Sizing: n = floor(shares / 100) contracts (a primary
  under 100 shares gets no option candidate, disclosed); INDEX_OVERLAY n = round-half-up(notional × beta /
  (front-month price × multiplier)) with beta from the master (1.0 assumed and labelled when the master has none; NQ
  for Nasdaq-listed names, else ES); DELTA_HEDGE shares = −delta × contracts × 100 rounded half-up to the nearest
  100 (delta from the Cboe row; none → the candidate says so).  Cost uses the chain ask for buys and bid for sells
  (× 100 × n); fees on the DEFAULT schedule per leg; protection level = K / S − 1; ``ratio`` = leg units / primary
  units (the children are sized to floor(primary filled × ratio)).
- A leg without a usable NBBO (``bid > 0 and ask ≥ bid``) makes the candidate ``available: false`` with the reason —
  nothing is priced from a model here.  Every candidate is SEQUENTIAL: the legs wait for the primary (WAITING_PARENT)
  and are resized on each partial fill; the row says so (``disclosure``).
- ``TRS_OVERLAY`` (receive / pay the primary's notional through the account's prime broker) appears only in a PB
  account of a PB/OTC-eligible lifetime when the swaps modules exist (WP-17 and WP-26's TRS); it is booked on the OTC desk, not as an order
  leg (``package()`` refuses it; the panel opens the OTC tab's TRS form prefilled from the candidate's ``prefill``, PO-10).
- ``FX_FORWARD`` (Build 2, OA-09): an FX position, or a foreign listing's currency exposure (qty x price x price scale in the
  listing's currency), is hedged by an outright 3-month forward priced by ``services.fx.forward_rate`` (covered interest parity,
  ``computed``, its inputs carried); it is dealt on the FX desk (``submit_via: fx``, ``prefill``), never an order leg.
- ``DV01_FUTURES`` (Build 2, OA-05): a bond is hedged with US Treasury futures sized by DV01: n = round(|position DV01| /
  contract DV01) with the position DV01 from ``services.bonds.dv01_c`` (full reprice for +1 bp) and the contract DV01 =
  tables.FUT_DV01 duration x price x multiplier / 10,000 (an APPROXIMATE duration table, labelled); the root by the bond's
  remaining life (<= 3.5 y ZT, <= 7 y ZF, <= 15 y ZN, else ZB); the futures trade in the lifetime's US Exchange account
  (``trade_account``).  Options on futures stay NOT IN THIS BUILD (a labelled row, never fabricated).
- Every candidate carries ``ratio_text`` ("1 contract per 100 shares", "60 shares per contract") and ``zero_size`` when it
  sizes to nothing (fewer than 100 shares, less than one futures contract): the panel hides those with one line saying why.
- POSITION MODE (USX-12 / ORC-4, ``position_candidates``; ``GET /api/hedges?mode=position``): the candidates for the position
  ALREADY HELD in the account (signed quantity from the ledger), sized to it, with NO primary order: a one-leg hedge is its
  own order (``submit_via: order``), a hedge of several option legs one all-or-none package (``submit_via: package``,
  strategies.POSITION_HEDGE), the TRS / FX forward on their own desks.  A flat position has nothing to hedge (said).
- ``package()`` turns a candidate (or the ticket's explicit ``legs``) into WAITING_PARENT child orders through
  ``orders.create_hedge_children``; option legs are materialised from the chain first (``options.ensure_option``) and
  an unlisted contract is refused (NOT_LISTED), never invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from . import tables
from .books import account_row, eligibility as _eligibility, split_account_id
from .clock import ny_of
from .commands import ConflictError, RejectError, TxContext
from .instruments import EQUITY_KINDS, fx_cross_id, parse_occ
from .ledger import position as _position
from .products import options as _options
from .units import from_c, qprice, to_c

MIN_DAYS = 90
PUT_PCT = Decimal("0.93")
CALL_PCT = Decimal("1.07")
PUT_SPREAD_PCT = Decimal("0.85")
CALL_SPREAD_PCT = Decimal("1.15")
CHAIN_STALE_S = 900
NASDAQ_EXCHANGES = ("NMS", "NGM", "NCM")
PCT4 = Decimal("0.0001")
CENT = Decimal("0.01")
NOT_BUILT = "NOT IN THIS BUILD"
SEQUENTIAL_NOTE = "sequential; a leg can rest (the legs are released after the primary fills, sized to its filled quantity x ratio)"
FX_FORWARD_TENOR = "3M"
# DV01_FUTURES: the Treasury future by the bond's remaining life in years (the last bucket has no limit)
BOND_FUTURE_BUCKETS = ((Decimal("3.5"), "ZT"), (Decimal(7), "ZF"), (Decimal(15), "ZN"), (None, "ZB"))
UNIT_NAMES = {"OPTION": ("contract", "contracts"), "FUTURE": ("contract", "contracts"), "EQUITY": ("share", "shares"), "TRS": ("unit", "units")}


@dataclass
class HedgeLeg:
    instrument_id: Optional[str]
    side: str                        # BUY SELL SELL_SHORT BTO STO | RECEIVE PAY (TRS)
    qty: int
    price_basis: str                 # ask | bid | last | otc.preview
    est_price: Optional[Decimal]
    quote: dict = field(default_factory=dict)
    kind: str = "OPTION"             # OPTION | EQUITY | FUTURE | TRS
    label: str = ""
    needs_borrow: bool = False
    listed: bool = True              # the instrument row exists in the master (options are materialised at submit)
    symbol: Optional[str] = None
    strike: Optional[str] = None
    expiry: Optional[str] = None
    fee: Optional[Decimal] = None
    margin: Optional[dict] = None    # futures legs: {"initial", "per_contract"} (FUT_MARGIN)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["est_price"] = _s(self.est_price)
        d["fee"] = _s(self.fee)
        return d


@dataclass
class HedgeCandidate:
    id: str
    label: str
    legs: List[HedgeLeg]
    cost: Optional[Decimal]          # cash paid (positive) / received (negative) for the legs, before fees
    protection_level: Optional[Decimal]
    net_cash: Optional[Decimal]      # −cost − fees
    greeks: dict
    chain_quality: str
    as_of: Optional[int]
    editable: dict
    available: bool = True
    reason: str = ""
    note: str = ""
    fees: Optional[Decimal] = None
    submit_via: str = "order"        # order | otc
    expiry: Optional[str] = None
    protection: Optional[str] = None
    ratio: Optional[Decimal] = None
    fill_mode: str = "SEQUENTIAL"
    disclosure: str = SEQUENTIAL_NOTE
    rule: str = ""
    not_in_this_build: bool = False
    mode: str = "order"              # order: the hedge legs of a NEW primary | position: hedges a position already held (no primary)
    ratio_text: Optional[str] = None
    extra: dict = field(default_factory=dict)     # zero_size, prefill (FX desk / OTC form), trade_account, position_qty

    def to_dict(self) -> dict:
        d = {"id": self.id, "label": self.label, "legs": [l.to_dict() for l in self.legs], "cost": _s(self.cost), "protection_level": _s(self.protection_level),
             "net_cash": _s(self.net_cash), "greeks": self.greeks, "chain_quality": self.chain_quality, "as_of": self.as_of, "editable": self.editable,
             "available": self.available, "reason": self.reason, "note": self.note, "fees": _s(self.fees), "submit_via": self.submit_via, "expiry": self.expiry,
             "protection": self.protection, "ratio": _s(self.ratio), "fill_mode": self.fill_mode, "disclosure": self.disclosure, "rule": self.rule,
             "not_in_this_build": self.not_in_this_build, "mode": self.mode, "ratio_text": self.ratio_text, "zero_size": False}
        d.update(self.extra or {})
        return d


def _s(x) -> Optional[str]:
    return None if x is None else str(x)


def round_half_up(x: Decimal) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def round_to_100(shares: Decimal) -> int:
    """Nearest 100 shares, half up, sign preserved: 550 -> 600, -593.2 -> -600, 206.8 -> 200."""
    sign = -1 if shares < 0 else 1
    return sign * round_half_up(abs(Decimal(shares)) / Decimal(100)) * 100


def ratio_of(leg_qty: int, primary_qty: int) -> Optional[Decimal]:
    """``leg units / primary units`` (8 dp HALF_EVEN, canonical like ``qprice``): 10 / 1,000 -> 0.01, 600 / 10 -> 60."""
    if not primary_qty or leg_qty is None:
        return None
    return qprice((Decimal(int(leg_qty)) / Decimal(int(primary_qty))).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN))


def _primary_units(inst) -> Tuple[str, str]:
    if inst.kind in ("OPTION", "FUTURE"):
        return "contract", "contracts"
    if inst.kind == "FX":
        base = str(inst.instrument_id)[:3]
        return base, base
    if inst.kind == "BOND":
        return "of face", "of face"
    return "share", "shares"


def ratio_text(ratio, leg_kind: str, inst, held: bool = False) -> Optional[str]:
    """USX-36: the ratio in a trader's words: 0.01 option per share -> "1 contract per 100 shares", 0.002 future per share ->
    "1 contract per 500 shares", 60 shares per option -> "60 shares per contract"; ``held`` adds "held" (position mode)."""
    if ratio is None:
        return None
    r = Decimal(str(ratio))
    if r <= 0:
        return None
    ls, lp = UNIT_NAMES.get(leg_kind, ("unit", "units"))
    ps, pp = _primary_units(inst)
    tail = " held" if held else ""
    if leg_kind == "OPTION" and ps == "share":
        return f"1 contract per 100 shares{tail}"        # the option sizing rule: n = floor(shares / 100) (one contract = 100 shares)
    if r < 1:
        inv = Decimal(1) / r
        if inv == inv.to_integral_value():
            n = int(inv)
            return f"1 {ls} per {n:,} {pp if n != 1 else ps}{tail}"
        return f"1 {ls} per about {round_half_up(inv):,} {pp}{tail}"
    if r == r.to_integral_value():
        n = int(r)
        return f"{n:,} {ls if n == 1 else lp} per {ps}{tail}"
    return f"{qprice(r)} {lp} per {ps}{tail}"


def _pct_text(level: Optional[Decimal]) -> str:
    if level is None:
        return ""
    pct = (level * 100).quantize(CENT, rounding=ROUND_HALF_EVEN)
    return f"{pct:+.2f} %"


# ---- chain access --------------------------------------------------------------------------------------------------------
def _chain_for(services, underlying_id: str, chain, now: int):
    """The chain to use, or a status dict: ``chain`` as given; else the cache (fresh, then stale + a queued refresh); else
    ``{"status": "fetching"}`` / ``{"status": "unavailable"}``."""
    if chain is not None:
        return chain, None
    chains = getattr(services, "chains", None)
    if chains is None:
        return None, {"status": "unavailable", "candidates": [], "note": "no chain source in this runtime (offline): hedge candidates need a Cboe chain"}
    snap = chains.get(underlying_id) if hasattr(chains, "get") else None
    if snap is not None:
        return snap, None
    stale = chains.peek(underlying_id) if hasattr(chains, "peek") else None
    if hasattr(chains, "keep_on_screen"):
        chains.keep_on_screen(underlying_id)
    err = chains.last_error(underlying_id) if hasattr(chains, "last_error") else None
    if stale is None:
        if err:
            return None, {"status": "unavailable", "candidates": [], "note": f"the {underlying_id} chain is unavailable: {err}", "retry_ms": 1500}
        chains.request(underlying_id)
        return None, {"status": "fetching", "candidates": [], "retry_ms": 1500, "underlying_id": underlying_id}
    chains.request(underlying_id)
    return stale, None


def chain_quality(services, chain, now: int, under=None) -> str:
    """``close`` outside the option session, ``delayed`` within 15 min of the fetch, else ``stale`` (options._chain_quality)."""
    return _options._chain_quality(services, chain, now, under)


def pick_expiry(chain, today: date, min_days: int = MIN_DAYS) -> Tuple[Optional[str], str]:
    """The nearest listed expiry at least ``min_days`` calendar days out, else the farthest listed (disclosed)."""
    exps = sorted(chain.expiries())
    if not exps:
        return None, "the chain lists no expiries"
    for e in exps:
        if (date.fromisoformat(e) - today).days >= min_days:
            return e, f"nearest listed expiry at least {min_days} days out"
    return exps[-1], f"no listed expiry at least {min_days} days out: the farthest listed ({exps[-1]}) is used"


def snap_strike(rows, target: Decimal) -> Optional[Decimal]:
    """The nearest listed strike (ties to the lower)."""
    listed = sorted({r.strike for r in rows})
    if not listed:
        return None
    return min(listed, key=lambda k: (abs(k - target), k))


def _row(chain, expiry: str, right: str, strike: Decimal):
    for r in chain.rows_for(expiry):
        if r.right == right and r.strike == strike:
            return r
    return None


def _leg_quote(row, chain, quality: str, now: int) -> dict:
    """The contract's chain quote with the chain's provenance (the same shape as ``options.leg_dict``)."""
    return _options.leg_dict(row, chain, quality, now)


def _mark_of(services, instrument_id: str) -> Tuple[Optional[Decimal], str, Optional[dict]]:
    """``(price, quality, quote dict)`` from the runtime's mark provider; ``(None, 'unavailable', None)`` when there is none."""
    marks = getattr(services, "marks", None)
    if marks is None:
        return None, "unavailable", None
    try:
        v = marks.mark_for(instrument_id) if hasattr(marks, "mark_for") else marks.mark(instrument_id)
    except Exception:   # noqa: BLE001 — a failing mark is an unavailable mark
        return None, "unavailable", None
    if v is None or v.mark is None or v.quality == "unavailable":
        return None, "unavailable", None
    px = qprice(v.mark)
    q = v.to_dict() if hasattr(v, "to_dict") else {}
    q.update({"last": str(px), "value": str(px), "quality": v.quality, "quote_time": v.quote_time})
    return px, v.quality, q


def _request_quote(services, instrument_id: str) -> str:
    """A futures leg without a mark: ask the quote cache for it (the worker fetches it off this thread) and say so - the panel
    asks again a few seconds later.  Returns the reason text."""
    cache = getattr(services, "cache", None)
    if cache is not None and hasattr(cache, "subscribe"):
        try:
            cache.subscribe([instrument_id], 120, "PAGE")
            return f"{instrument_id}: no quote yet - requested (the hedges are priced again in a few seconds)"
        except Exception:   # noqa: BLE001 - a cache that cannot take the request leaves the plain reason
            pass
    return f"{instrument_id} mark unavailable"


def _spot_of(services, chain, underlying_id: str) -> Tuple[Optional[Decimal], str]:
    if chain is not None and chain.spot is not None:
        return qprice(chain.spot), "chain underlying last"
    px, q, _ = _mark_of(services, underlying_id)
    return px, f"mark ({q})"


def _edit_decimal(edits: Optional[dict], key: str) -> Optional[Decimal]:
    v = (edits or {}).get(key)
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise RejectError("EDIT_INVALID", f"hedge edit {key} is not a number: {v!r}")
    if not d.is_finite() or d <= 0:
        raise RejectError("EDIT_INVALID", f"hedge edit {key} must be a positive number, got {v!r}")
    return d


# ---- option legs -----------------------------------------------------------------------------------------------------------
def _option_leg(chain, expiry: str, right: str, strike: Decimal, side: str, n: int, quality: str, master, now: int) -> Tuple[HedgeLeg, Optional[str]]:
    row = _row(chain, expiry, right, strike)
    if row is None:
        return HedgeLeg(None, side, n, "", None, {}, "OPTION", f"{right}{strike} {expiry}", strike=str(strike), expiry=expiry), \
            f"no {right}{strike} contract for {expiry} on the chain"
    buy = side in ("BTO", "BUY")
    leg = HedgeLeg(row.occ, side, n, "ask" if buy else "bid", None, _leg_quote(row, chain, quality, now), "OPTION",
                   f"{'buy' if buy else 'sell'} {n} {right}{strike} {expiry}", listed=master.get(row.occ) is not None, symbol=row.occ, strike=str(row.strike),
                   expiry=expiry, fee=from_c(tables.fee_c("OPTION", n)[0]))
    if not row.quoted:
        return leg, f"{row.occ}: no usable NBBO on the chain (bid {row.bid}, ask {row.ask})"
    leg.est_price = qprice(row.ask if buy else row.bid)
    return leg, None


def _finish(cand: HedgeCandidate, legs: List[HedgeLeg], problems: List[str], primary_qty: int, mult: Decimal = Decimal(100)) -> HedgeCandidate:
    """Cost / fees / net cash / greeks / ratio from the legs; unavailable when any leg has a problem."""
    cand.legs = legs
    if legs:
        cand.ratio = ratio_of(legs[0].qty, primary_qty)
    if problems:
        cand.available = False
        cand.reason = "; ".join(problems)
        return cand
    cost = Decimal(0)
    fees = Decimal(0)
    greeks: Dict[str, Decimal] = {"delta": Decimal(0), "gamma": Decimal(0), "theta": Decimal(0), "vega": Decimal(0)}
    partial = False
    for l in legs:
        if l.kind != "OPTION":
            continue
        buy = l.side in ("BTO", "BUY")
        cost += (l.est_price if buy else -l.est_price) * mult * l.qty
        fees += l.fee or Decimal(0)
        sign = Decimal(1) if buy else Decimal(-1)
        for g in ("delta", "gamma", "theta", "vega"):
            v = l.quote.get(g)
            if v is None:
                partial = True
                continue
            greeks[g] += sign * Decimal(str(v)) * mult * l.qty
    cand.cost = cost.quantize(CENT)
    cand.fees = fees.quantize(CENT)
    cand.net_cash = (-cand.cost - cand.fees).quantize(CENT)
    cand.greeks = {k: str(v.quantize(CENT)) for k, v in greeks.items()}
    cand.greeks["delta_shares"] = cand.greeks["delta"]
    cand.greeks["note"] = ("some Greeks missing on the chain (partial sums)" if partial else "from the Cboe chain record (delta x 100 x contracts, signed by side)")
    return cand


def _equity_candidates(services, account_id: str, inst, side: str, qty: int, chain, S: Decimal, s_src: str, quality: str, today: date, now: int,
                       edits: Optional[dict] = None) -> List[HedgeCandidate]:
    master = services.instruments
    out: List[HedgeCandidate] = []
    base_edit = {"strike": True, "expiry": True, "ratio": True}
    long_primary = side in ("BUY", "BUY_TO_COVER")
    option_ids = (("PUT_7PCT", "Protective put ~7 % below"), ("COLLAR_7", "Collar ~7 % / ~7 %"), ("PUT_SPREAD", "Put spread 7 % / 15 %")) if long_primary else \
                 (("CALL_7PCT", "Protective call ~7 % above"), ("CALL_SPREAD", "Call spread 7 % / 15 %"), ("COLLAR_SHORT", "Short collar"))
    n = int(qty) // 100
    exp_edit = (edits or {}).get("expiry")
    if exp_edit:
        exp_edit = str(exp_edit).strip()
        if exp_edit in chain.expiries():
            expiry, exp_note = exp_edit, "expiry set on the ticket"
        else:
            expiry, exp_note = None, f"expiry {exp_edit} is not listed on the chain (listed: {', '.join(chain.expiries())})"
    else:
        expiry, exp_note = pick_expiry(chain, today)
    k_edit = _edit_decimal(edits, "strike")
    note = f"{exp_note}; S {S} ({s_src}); n = floor({qty:,} / 100) = {n}"

    def unavailable(reason: str, zero: bool = False) -> None:
        for cid, label in option_ids:
            out.append(HedgeCandidate(cid, label, [], None, None, None, {}, quality, chain.quote_time, dict(base_edit), False, reason, note, None, expiry=expiry,
                                      ratio=ratio_of(n, qty) if n > 0 else None, extra={"zero_size": True} if zero else {}))

    if expiry is None:
        unavailable(exp_note)
    elif n <= 0:
        unavailable(f"fewer than 100 shares: no option hedge sizes to one contract (floor({qty:,} / 100) = 0)", zero=True)
    else:
        rows = chain.rows_for(expiry)
        puts = [r for r in rows if r.right == "P"]
        calls = [r for r in rows if r.right == "C"]
        days = (date.fromisoformat(expiry) - today).days
        kp, kc = snap_strike(puts, S * PUT_PCT), snap_strike(calls, S * CALL_PCT)
        kps, kcs = snap_strike(puts, S * PUT_SPREAD_PCT), snap_strike(calls, S * CALL_SPREAD_PCT)
        put_rule = f"K = {PUT_PCT} x {S} = {(S * PUT_PCT).quantize(PCT4)} -> nearest listed {kp}"
        call_rule = f"K = {CALL_PCT} x {S} = {(S * CALL_PCT).quantize(PCT4)} -> nearest listed {kc}"
        if k_edit is not None:
            if long_primary:
                kp = snap_strike(puts, k_edit)
                put_rule = f"K = {k_edit} (ticket) -> nearest listed {kp}"
            else:
                kc = snap_strike(calls, k_edit)
                call_rule = f"K = {k_edit} (ticket) -> nearest listed {kc}"
        size_rule = f"n = floor({qty:,} / 100) = {n}; expiry {expiry} ({days} days; {exp_note})"

        def cand(cid: str, label: str, specs: List[Tuple[str, Optional[Decimal], str]], prot_k: Optional[Decimal], rule: str) -> HedgeCandidate:
            legs, problems = [], []
            for right, k, lside in specs:
                if k is None:
                    problems.append(f"no listed {right} strike for {expiry}")
                    continue
                leg, prob = _option_leg(chain, expiry, right, k, lside, n, quality, master, now)
                legs.append(leg)
                if prob:
                    problems.append(prob)
            prot = None if (prot_k is None or S == 0) else (prot_k / S - Decimal(1)).quantize(PCT4)
            prot_text = None if prot is None else (f"floor {prot_k} ({_pct_text(prot)})" if long_primary else f"cap {prot_k} ({_pct_text(prot)})")
            c = HedgeCandidate(cid, label, [], None, prot, None, {}, quality, chain.quote_time, dict(base_edit), True, "", note, None, expiry=expiry,
                               protection=prot_text, rule=f"{rule}; {size_rule}")
            return _finish(c, legs, problems, qty)

        if long_primary:
            out.append(cand("PUT_7PCT", f"Protective put ~7 % below: buy {n} P{kp} {expiry}", [("P", kp, "BTO")], kp, put_rule))
            out.append(cand("COLLAR_7", f"Collar: buy {n} P{kp}, sell {n} C{kc} {expiry}", [("P", kp, "BTO"), ("C", kc, "STO")], kp, f"{put_rule}; {call_rule} (sold at the bid)"))
            out.append(cand("PUT_SPREAD", f"Put spread: buy {n} P{kp}, sell {n} P{kps} {expiry}", [("P", kp, "BTO"), ("P", kps, "STO")], kp,
                            f"{put_rule}; short K = {PUT_SPREAD_PCT} x {S} -> nearest listed {kps}"))
        else:
            out.append(cand("CALL_7PCT", f"Protective call ~7 % above: buy {n} C{kc} {expiry}", [("C", kc, "BTO")], kc, call_rule))
            out.append(cand("CALL_SPREAD", f"Call spread: buy {n} C{kc}, sell {n} C{kcs} {expiry}", [("C", kc, "BTO"), ("C", kcs, "STO")], kc,
                            f"{call_rule}; short K = {CALL_SPREAD_PCT} x {S} -> nearest listed {kcs}"))
            out.append(cand("COLLAR_SHORT", f"Short collar: buy {n} C{kc}, sell {n} P{kp} {expiry}", [("C", kc, "BTO"), ("P", kp, "STO")], kc, f"{call_rule}; {put_rule} (sold at the bid)"))
    out.append(_index_overlay(services, inst, qty, S, quality, chain.quote_time, "SELL" if long_primary else "BUY", today))
    trs = _trs_overlay(services, account_id, inst, side, qty, S, quality, chain.quote_time)
    if trs is not None:
        out.append(trs)
    return out


def front_month(services, root: str, today: date):
    """The earliest ACTIVE futures contract of ``root`` whose expiry is on or after ``today``."""
    best = None
    for i in services.instruments.all():
        if i.kind != "FUTURE" or i.status != "ACTIVE" or (i.spec or {}).get("root") != root or not i.expiry:
            continue
        if date.fromisoformat(i.expiry) < today:
            continue
        if best is None or i.expiry < best.expiry:
            best = i
    return best


def _futures_margin(fut, n: int) -> Optional[dict]:
    im = int((fut.spec or {}).get("im_c") or 0)
    if not im:
        return None
    return {"initial": str(from_c(im * n)), "per_contract": str(from_c(im)), "note": tables.CONVENTIONS.get("FUT_MARGIN", "")}


def _index_overlay(services, inst, qty: int, S: Decimal, quality: str, as_of: Optional[int], side: str, today: date) -> HedgeCandidate:
    spec = inst.spec or {}
    root = "NQ" if str(spec.get("exchange") or "").upper() in NASDAQ_EXCHANGES else "ES"
    beta_raw = spec.get("beta")
    beta = Decimal(str(beta_raw)) if beta_raw is not None else Decimal(1)
    beta_note = f"beta {beta} (static, from the master)" if beta_raw is not None else "beta unavailable in the master: 1.0 assumed (labelled)"
    editable = {"strike": False, "expiry": True, "ratio": True}
    greeks = {"delta_shares": None, "note": f"linear index exposure; {beta_note}"}
    fut = front_month(services, root, today)
    if fut is None:
        return HedgeCandidate("INDEX_OVERLAY", f"Index overlay ({root} futures)", [], None, None, None, greeks, quality, as_of, editable, False,
                              f"no listed {root} contract in the master", beta_note)
    px, q, quote = _mark_of(services, fut.instrument_id)
    if px is None:
        return HedgeCandidate("INDEX_OVERLAY", f"Index overlay ({fut.symbol})", [HedgeLeg(fut.instrument_id, side, 0, "last", None, {}, "FUTURE", fut.symbol, symbol=fut.symbol)],
                              None, None, None, greeks, quality, as_of, editable, False, _request_quote(services, fut.instrument_id), beta_note)
    notional = Decimal(qty) * S
    per_contract = px * fut.multiplier
    raw = notional * beta / per_contract
    n = round_half_up(raw)
    formula = f"n = round({qty:,} x {S} x {beta} / ({px} x {fut.multiplier})) = round({raw.quantize(PCT4)}) = {n}"
    fee = from_c(tables.fee_c("FUTURE", n)[0]) if n > 0 else Decimal(0)
    leg = HedgeLeg(fut.instrument_id, side, n, "last", px, quote or {}, "FUTURE", f"{side} {n} {fut.symbol}", symbol=fut.symbol, expiry=fut.expiry, fee=fee,
                   margin=_futures_margin(fut, n))
    hedged = (notional * beta).quantize(CENT)
    prot = (f"notional hedged {hedged:,} ({notional.quantize(CENT):,} x beta {beta}) with {n} {fut.symbol} = {(per_contract * n).quantize(CENT):,}"
            f" ({fut.multiplier} x {px} each); initial margin {leg.margin['initial'] if leg.margin else 'n/a'} (FUT_MARGIN)")
    c = HedgeCandidate("INDEX_OVERLAY", f"Index overlay: {side} {n} {fut.symbol} (beta-weighted)", [leg], Decimal(0), None, Decimal(0), greeks, quality, as_of, editable,
                       n > 0, "" if n > 0 else "notional too small for one contract", f"{beta_note}; {formula}; futures are margined (IM), no premium",
                       protection=prot if n > 0 else None, rule=formula, ratio=ratio_of(n, qty) if n > 0 else None)
    if n > 0:
        c.fees = fee
        c.net_cash = -fee
    else:
        c.extra["zero_size"] = True                   # USX-36: never listed as 'SELL 0 ESZ26'; the panel hides it and says why
    return c


def _trs_overlay(services, account_id: str, inst, side: str, qty: int, S: Decimal, quality: str, as_of: Optional[int]) -> Optional[HedgeCandidate]:
    swaps = getattr(services, "swaps", None)
    if swaps is None or getattr(services, "swaps_trs", None) is None:    # a TRS books through WP-17's gate AND WP-26's module (WP-15)
        return None
    with services.db.reader() as conn:
        acct = account_row(conn, account_id)
    if acct is None or acct["kind"] != "PB":
        return None
    lid = split_account_id(account_id)[0]
    try:
        el = _eligibility(services, lid)
    except RejectError:
        return None
    if not el.pb_otc:
        return None
    long_primary = side in ("BUY", "BUY_TO_COVER")
    leg_side = "PAY" if long_primary else "RECEIVE"
    leg = HedgeLeg(None, leg_side, int(qty), "underlying level", S, {"last": str(S), "value": str(S), "quality": quality, "quote_time": as_of,
                                                                    "source": "underlying level (the reset price); the PB deals its spread on the OTC tab",
                                                                    "delay_label": "Cboe delayed (chain underlying)"}, "TRS",
                   f"{leg_side} total return on {qty:,} {inst.symbol}", symbol=inst.symbol)
    # PO-10: the panel's button opens the OTC tab's TRS form with these terms (swapticket.prefill, C21); it is never an order leg
    prefill = {"kind": "TRS", "terms": {"underlying_id": inst.instrument_id, "units": int(qty), "receiver": not long_primary}}
    return HedgeCandidate("TRS_OVERLAY", f"TRS overlay: {leg_side.lower()} the total return on {qty:,} {inst.symbol} through {acct['broker'] or 'the PB'}", [leg], None, None, None,
                          {"delta_shares": str(-qty if long_primary else qty), "note": "the swap's return leg offsets the primary one for one"}, quality, as_of,
                          {"strike": False, "expiry": False, "ratio": True}, True, "", f"booked on the OTC desk (otc.book), not as an order leg; {el.pb_otc_reason}",
                          None, submit_via="otc", disclosure="booked on the OTC tab through the account's prime broker: opens the TRS form prefilled; not an order leg",
                          rule=f"notional = {qty:,} x {S}", ratio=Decimal(1), extra={"prefill": prefill, "trade_account": account_id})


# ---- option, futures, FX, bond primaries ---------------------------------------------------------------------------------------
def _option_candidates(services, account_id: str, inst, side: str, qty: int, chain, quality: str, now: int) -> List[HedgeCandidate]:
    editable = {"strike": False, "expiry": False, "ratio": True}
    if side not in ("BTO", "STO"):
        return []
    row = chain.by_occ(inst.cboe_symbol or inst.instrument_id) or chain.by_occ(inst.instrument_id)
    under_id = inst.underlying_id or ""
    if row is None:
        return [HedgeCandidate("DELTA_HEDGE", "Delta hedge (underlying shares)", [], None, None, None, {}, quality, chain.quote_time, editable, False,
                               f"{inst.symbol} is not on the cached {under_id} chain", "")]
    if row.delta is None:
        return [HedgeCandidate("DELTA_HEDGE", "Delta hedge (underlying shares)", [], None, None, None, {}, quality, chain.quote_time, editable, False,
                               f"{inst.symbol}: no delta on the chain row", "")]
    delta = Decimal(str(row.delta))
    position_delta = (Decimal(1) if side == "BTO" else Decimal(-1)) * delta * Decimal(qty) * inst.multiplier
    shares = round_to_100(-position_delta)
    S, s_src = _spot_of(services, chain, under_id)
    formula = f"shares = -({delta} x {qty} x {inst.multiplier}) = {(-position_delta).quantize(PCT4)} -> rounded to 100: {shares:+,}"
    if shares == 0:
        return [HedgeCandidate("DELTA_HEDGE", "Delta hedge (underlying shares)", [], None, None, None, {}, quality, chain.quote_time, editable, False,
                               "the position delta rounds to fewer than 50 shares", formula, rule=formula)]
    with services.db.reader() as conn:
        held, _ = _position(conn, account_id, under_id)
    if shares > 0:
        leg_side, needs = "BUY", False
    elif held >= -shares:
        leg_side, needs = "SELL", False
    else:
        leg_side, needs = "SELL_SHORT", True
    _px, _q, quote = _mark_of(services, under_id)
    quote = dict(quote or {})
    quote.update({"last": _s(S), "value": _s(S), "source": s_src, "quality": quality, "quote_time": chain.quote_time})
    fee = from_c(tables.fee_c("EQUITY", abs(shares))[0])
    leg = HedgeLeg(under_id, leg_side, abs(shares), "last", S, quote, "EQUITY", f"{leg_side} {abs(shares):,} {under_id}", needs, symbol=under_id, fee=fee)
    cost = None if S is None else (S * abs(shares) * (1 if leg_side == "BUY" else -1)).quantize(CENT)
    c = HedgeCandidate("DELTA_HEDGE", f"Delta hedge: {leg_side} {abs(shares):,} {under_id}", [leg], cost, None, None if cost is None else (-cost - fee).quantize(CENT),
                       {"delta": str(Decimal(shares)), "delta_shares": str(Decimal(shares)), "note": "delta from the Cboe chain row; the hedge offsets the position's delta"},
                       quality, chain.quote_time, editable, S is not None, "" if S is not None else f"{under_id} mark unavailable",
                       formula + ("; a short sale needs a borrow (WP-06)" if needs else ""), fee, rule=formula, ratio=ratio_of(abs(shares), qty))
    return [c]


def _futures_candidates(services, inst, side: str, qty: int, today: date, now: int) -> List[HedgeCandidate]:
    root = (inst.spec or {}).get("root")
    editable = {"strike": False, "expiry": True, "ratio": True}
    out: List[HedgeCandidate] = []
    nxt = None
    for i in sorted((i for i in services.instruments.all() if i.kind == "FUTURE" and i.status == "ACTIVE" and (i.spec or {}).get("root") == root and i.expiry),
                    key=lambda i: i.expiry):
        if inst.expiry and i.expiry > inst.expiry:
            nxt = i
            break
    opp = "SELL" if side == "BUY" else "BUY"
    if nxt is None:
        out.append(HedgeCandidate("CALENDAR", "Calendar spread (next listed month)", [], None, None, None, {}, "n/a", now, editable, False,
                                  f"no later {root} contract listed in the master", ""))
    else:
        px, q, quote = _mark_of(services, nxt.instrument_id)
        fee = from_c(tables.fee_c("FUTURE", int(qty))[0])
        leg = HedgeLeg(nxt.instrument_id, opp, int(qty), "last", px, quote or {}, "FUTURE", f"{opp} {qty} {nxt.symbol}", symbol=nxt.symbol, expiry=nxt.expiry, fee=fee,
                       margin=_futures_margin(nxt, int(qty)))
        out.append(HedgeCandidate("CALENDAR", f"Calendar: {opp} {qty} {nxt.symbol} against the {inst.symbol} position", [leg], Decimal(0), None, -fee,
                                  {"delta_shares": None, "note": "the next month offsets the front month one for one (calendar spread)"}, q, now, editable, px is not None,
                                  "" if px is not None else _request_quote(services, nxt.instrument_id), "futures are margined (IM); no premium", fee,
                                  rule=f"{opp} {qty} of the next listed month ({nxt.symbol}, expiry {nxt.expiry})", ratio=Decimal(1)))
    out.append(HedgeCandidate("OPTIONS_ON_FUTURES", f"Options on {root} futures", [], None, None, None, {}, "n/a", now, {}, False, NOT_BUILT, NOT_BUILT, not_in_this_build=True))
    return out


# ---- FX forwards (OA-09) and DV01 futures (OA-05) ------------------------------------------------------------------------------------
def _trade_account(services, account_id: str, role: str) -> str:
    """The account a hedge on another desk is dealt in: this one when it is a PB account (it trades both), else the lifetime's
    ``role`` account (futures trade in US Exchange, FX in Foreign Markets & FX)."""
    try:
        lid, own = split_account_id(account_id)
    except ValueError:
        return account_id
    return account_id if (own == role or own.startswith("PB_")) else f"{lid}:{role}"


def _fx_forward(services, account_id: str, inst, side: str, qty: int, now: int) -> HedgeCandidate:
    """OA-09: an outright forward against the currency exposure of an FX position or a foreign listing, priced by
    ``services.fx.forward_rate`` (CIP, computed) for the 3-month tenor; dealt on the FX desk (``prefill``), never an order leg."""
    cid = "FX_FORWARD"
    editable = {"strike": False, "expiry": False, "ratio": False}
    long_ = side in ("BUY", "BUY_TO_COVER")
    if inst.kind == "FX":
        pair = str(inst.instrument_id).upper()
        ccy, other = pair[:3], pair[3:]
        amount = Decimal(int(qty)).quantize(CENT)
        basis = f"{qty:,} {ccy} ({pair} base units)"
    else:
        ccy, other = str(inst.currency or "USD").upper(), "USD"
        pair = fx_cross_id(ccy, other)
        px, _q, _quote = _mark_of(services, inst.instrument_id)
        if px is None:
            return HedgeCandidate(cid, f"FX forward ({ccy})", [], None, None, None, {}, "n/a", now, editable, False,
                                  f"{inst.symbol}: no price, so the {ccy} amount to hedge is unknown", "")
        scale = Decimal(str(getattr(inst, "price_scale", 1) or 1))
        amount = (Decimal(int(qty)) * px * scale).quantize(CENT)
        basis = f"{qty:,} x {px}" + (f" x {scale}" if scale != 1 else "") + f" = {ccy} {amount:,}"
    sell_ccy, buy_ccy = (ccy, other) if long_ else (other, ccy)
    verb = "sell" if long_ else "buy"
    label = f"FX forward: {verb} {ccy} {amount:,} against {other} ({FX_FORWARD_TENOR})"
    fxm = getattr(services, "fx", None)
    if fxm is None or not hasattr(fxm, "forward_rate"):
        return HedgeCandidate(cid, label, [], None, None, None, {}, "n/a", now, editable, False, "the FX desk is not loaded in this runtime: no forward price", basis)
    try:
        td = fxm.fx_trade_date(services, now)
        vd = fxm.tenor_date(services, pair, FX_FORWARD_TENOR, td)
        F, inputs = fxm.forward_rate(services, pair, vd)
    except Exception as e:   # noqa: BLE001 - a missing input is an unavailable forward, never a guess
        return HedgeCandidate(cid, label, [], None, None, None, {}, "n/a", now, editable, False, f"no forward price: {e}", basis)
    if F is None:
        return HedgeCandidate(cid, label, [], None, None, None, {}, "n/a", now, editable, False, f"no forward price: {(inputs or {}).get('note') or 'an input is missing'}", basis)
    F = qprice(F)
    other_amt = (amount * F if pair.startswith(ccy) else amount / F).quantize(CENT)
    spot = (inputs or {}).get("spot") or {}
    quote = {"value": str(F), "last": str(F), "quality": "computed", "source": "covered interest parity (FX desk: spot and the two money-market rates)",
             "quote_time": spot.get("quote_time"), "method": "cip_forward", "inputs": {k: v for k, v in (inputs or {}).items() if k in ("spot", "r_base", "r_quote", "days")}}
    leg = HedgeLeg(None, "SELL" if long_ else "BUY", int(amount), "forward outright (CIP)", F, quote, "FX_FORWARD",
                   f"{verb} {ccy} {amount:,} / {'buy' if long_ else 'sell'} {other} {other_amt:,} at {F}, value {vd.isoformat()}", symbol=pair, expiry=vd.isoformat())
    prefill = {"kind": "FX", "mode": "FORWARD", "tenor": FX_FORWARD_TENOR, "buy_ccy": buy_ccy, "sell_ccy": sell_ccy, "amount": str(amount), "amount_ccy": ccy,
               "instrument_id": pair}
    rule = f"amount = {basis}; F = {pair} {FX_FORWARD_TENOR} outright {F} for value {vd.isoformat()} (covered interest parity, computed)"
    return HedgeCandidate(cid, label, [leg], Decimal(0), None, Decimal(0), {"note": "a forward carries no premium: both amounts change hands on the value date"},
                          "computed", spot.get("quote_time") or now, editable, True, "", rule, None, submit_via="fx", expiry=vd.isoformat(),
                          protection=f"locks {pair} at {F} for {ccy} {amount:,} until {vd.isoformat()}", fill_mode="FX DESK",
                          disclosure="dealt on the FX desk (a forward is not an order leg): opens the desk prefilled with this deal; the dealt rate adds the desk's disclosed spread",
                          rule=rule, extra={"prefill": prefill, "trade_account": _trade_account(services, account_id, "FOREIGN_FX")})


def _bond_years(services, inst, today: date) -> Optional[Decimal]:
    maturity = None
    bonds = getattr(services, "bonds", None)
    if bonds is not None and hasattr(bonds, "bond_terms"):
        try:
            maturity = bonds.bond_terms(inst).maturity
        except Exception:   # noqa: BLE001 - falls back to the master's expiry
            maturity = None
    if maturity is None and inst.expiry:
        maturity = date.fromisoformat(str(inst.expiry)[:10])
    if maturity is None:
        return None
    return (Decimal((maturity - today).days) / Decimal("365.25")).quantize(Decimal("0.01"))


def _dv01_futures(services, account_id: str, inst, side: str, qty: int, today: date, now: int) -> HedgeCandidate:
    """OA-05: Treasury futures against a bond's DV01 (the module docstring DV01_FUTURES); the futures trade in US Exchange."""
    cid, label = "DV01_FUTURES", "DV01 hedge with Treasury futures"
    editable = {"strike": False, "expiry": False, "ratio": False}

    def off(reason: str, note: str = "", zero: bool = False) -> HedgeCandidate:
        return HedgeCandidate(cid, label, [], None, None, None, {}, "n/a", now, editable, False, reason, note, extra={"zero_size": True} if zero else {})

    bonds = getattr(services, "bonds", None)
    if bonds is None or not hasattr(bonds, "dv01_c"):
        return off("the bond engine is not loaded in this runtime: no DV01")
    face = int(qty) if side in ("BUY", "BUY_TO_COVER") else -int(qty)
    try:
        dv, dv_in = bonds.dv01_c(services, inst, face)
    except Exception as e:   # noqa: BLE001 - no DV01 is an unavailable hedge, never a guess
        return off(f"DV01 unavailable: {e}")
    if dv is None:
        return off(f"DV01 unavailable: {(dv_in or {}).get('note') or (dv_in or {}).get('basis') or 'no yield'}")
    years = _bond_years(services, inst, today)
    root = next(r for lim, r in BOND_FUTURE_BUCKETS if lim is None or (years is not None and years <= lim))
    fut = front_month(services, root, today)
    if fut is None:
        return off(f"no listed {root} contract in the master")
    px, q, quote = _mark_of(services, fut.instrument_id)
    if px is None:
        return off(_request_quote(services, fut.instrument_id))
    dur = tables.FUT_DV01.get(root)
    per_c = to_c(dur * px * fut.multiplier / Decimal(10000))
    if per_c <= 0:
        return off(f"{fut.symbol}: no contract DV01")
    raw = Decimal(abs(int(dv))) / Decimal(per_c)
    n = round_half_up(raw)
    leg_side = "SELL" if dv < 0 else "BUY"                  # a long bond loses when yields rise: the short futures gain
    formula = (f"n = round(|position DV01| {from_c(abs(int(dv))):,} / contract DV01 {from_c(per_c):,}) = round({raw.quantize(PCT4)}) = {n}; "
               f"contract DV01 = {dur} x {px} x {fut.multiplier} / 10,000 = {from_c(per_c):,} USD per bp (approximate duration table)")
    if n <= 0:
        return off("the position's DV01 is less than half of one contract's", formula, zero=True)
    fee = from_c(tables.fee_c("FUTURE", n)[0])
    trade_acct = _trade_account(services, account_id, "US_EXCHANGE")
    leg = HedgeLeg(fut.instrument_id, leg_side, n, "last", px, quote or {}, "FUTURE", f"{leg_side} {n} {fut.symbol}", symbol=fut.symbol, expiry=fut.expiry, fee=fee,
                   margin=_futures_margin(fut, n))
    life = "unknown" if years is None else f"{years} years"
    cross = "" if str(inst.currency or "USD").upper() == "USD" else f"; a cross-market hedge of a {inst.currency} bond with US Treasury futures (basis risk)"
    note = (f"{root} for a bond with {life} left (<= 3.5 y ZT, <= 7 y ZF, <= 15 y ZN, else ZB){cross}; the futures trade in {trade_acct}, not in this account"
            f"; futures are margined (IM), no premium")
    prot = (f"position DV01 {from_c(int(dv)):,} USD per bp offset by {n} x {from_c(per_c):,} = {from_c(n * per_c):,} (approximate)")
    c = HedgeCandidate(cid, f"DV01 hedge: {leg_side} {n} {fut.symbol} (in {trade_acct})", [leg], Decimal(0), None, -fee,
                       {"delta_shares": None, "dv01_position": str(from_c(int(dv))), "dv01_contract": str(from_c(per_c)), "note": (dv_in or {}).get("basis") or ""},
                       q, now, editable, True, "", note, fee, rule=formula, protection=prot, fill_mode=None,
                       disclosure=f"its own futures order in {trade_acct} (the futures do not trade in this account)",
                       extra={"trade_account": trade_acct})
    c.ratio_text = "sized by DV01 (not by face)"
    return c


# ---- the entry points --------------------------------------------------------------------------------------------------------------
def candidates(services, account_id: str, inst, side: str, qty: int, chain=None, now: Optional[int] = None, edits: Optional[dict] = None):
    """§10.9: the candidate list for a primary, or a status dict (``fetching`` / ``unavailable``) when the chain is not cached.
    Closing trades (SELL / BUY_TO_COVER / STC / BTC of equities and options) get no candidates.  ``edits`` = the ticket's
    ``{strike, expiry}`` overrides for the protective option leg.  Every candidate gets its ``ratio_text``."""
    now = int(now if now is not None else services.clock.epoch())
    today = ny_of(now).date()
    side = str(side or "").upper()
    qty = int(qty)
    kind = inst.kind
    if side in ("SELL", "BUY_TO_COVER", "STC", "BTC") and kind not in ("FUTURE", "FX", "BOND"):
        return []
    out: List[HedgeCandidate]
    if kind in EQUITY_KINDS and str(inst.currency or "USD").upper() != "USD":
        out = [_fx_forward(services, account_id, inst, side, qty, now)]          # a foreign listing: its currency exposure
    elif kind in EQUITY_KINDS:
        snap, status = _chain_for(services, inst.instrument_id, chain, now)
        if status is not None:
            return status
        S, s_src = _spot_of(services, snap, inst.instrument_id)
        if S is None:
            return {"status": "unavailable", "candidates": [], "note": f"{inst.symbol}: no underlying price (chain and mark unavailable)"}
        out = _equity_candidates(services, account_id, inst, side, qty, snap, S, s_src, chain_quality(services, snap, now, inst), today, now, edits)
    elif kind == "OPTION":
        if not inst.underlying_id:
            return []
        snap, status = _chain_for(services, inst.underlying_id, chain, now)
        if status is not None:
            return status
        under = services.instruments.get(inst.underlying_id)
        out = _option_candidates(services, account_id, inst, side, qty, snap, chain_quality(services, snap, now, under), now)
    elif kind == "FUTURE":
        out = _futures_candidates(services, inst, side, qty, today, now)
    elif kind == "FX":
        out = [_fx_forward(services, account_id, inst, side, qty, now)]
    elif kind == "BOND":
        out = [_dv01_futures(services, account_id, inst, side, qty, today, now)]
    else:
        return []
    for c in out:
        if c.ratio_text is None and c.legs:
            c.ratio_text = ratio_text(c.ratio, c.legs[0].kind, inst)
    return out


def _position_side(kind: str, held: int) -> str:
    """The opening side of the position's direction, which the candidate rules are written for (no order is placed in it)."""
    if kind == "OPTION":
        return "BTO" if held > 0 else "STO"
    if kind in EQUITY_KINDS:
        return "BUY" if held > 0 else "SELL_SHORT"
    return "BUY" if held > 0 else "SELL"


def position_qty(services, account_id: str, instrument_id: str) -> int:
    """The signed quantity held (the ledger's position; 0 when flat)."""
    with services.db.reader() as conn:
        held, _ = _position(conn, account_id, instrument_id)
    return int(held or 0)


def position_candidates(services, account_id: str, inst, chain=None, now: Optional[int] = None, edits: Optional[dict] = None):
    """USX-12 / ORC-4: hedge the position ALREADY HELD in ``account_id`` (module docstring POSITION MODE).  Returns
    ``(held, result)``: ``result`` is the candidate list sized to |held| (each marked ``mode: position`` with how it trades:
    ``submit_via`` order | package | otc | fx, and never a primary order) or a status dict (``fetching`` / ``unavailable``, or
    ``flat`` when nothing is held)."""
    now = int(now if now is not None else services.clock.epoch())
    held = position_qty(services, account_id, inst.instrument_id)
    if held == 0:
        return 0, {"status": "flat", "candidates": [], "note": f"no {inst.symbol} position in {account_id}: there is nothing to hedge"}
    out = candidates(services, account_id, inst, _position_side(inst.kind, held), abs(held), chain=chain, now=now, edits=edits)
    if isinstance(out, dict):
        return held, out
    what = f"the {abs(held):,} {inst.symbol} you {'hold' if held > 0 else 'are short'}"
    for c in out:
        c.mode = "position"
        c.extra["position_qty"] = held
        if c.ratio_text and c.ratio is not None and c.legs:
            c.ratio_text = ratio_text(c.ratio, c.legs[0].kind, inst, held=True)
        if c.submit_via in ("otc", "fx"):
            continue
        legs = [l for l in c.legs if l.qty > 0]
        if len(legs) > 1 and all(l.kind == "OPTION" for l in legs):
            c.submit_via, c.fill_mode = "package", "AON"
            c.disclosure = f"hedges {what}: the {len(legs)} option legs trade together as one all-or-none package; no order in {inst.symbol}"
        else:
            c.submit_via, c.fill_mode = "order", None
            where = c.extra.get("trade_account")
            c.disclosure = (f"hedges {what}: the leg is its own order" + (f" in {where}" if where and where != account_id else "")
                            + f" (preview and submit it on the ticket); no order in {inst.symbol}")
    return held, out


def position_payload(inst, account_id: str, held: int, result) -> dict:
    """The JSON of ``GET /api/hedges?mode=position``: ``{mode, position, status, candidates, as_of, chain_quality, note}``."""
    pos = {"instrument_id": inst.instrument_id, "symbol": inst.symbol, "kind": inst.kind, "qty": held, "account_id": account_id,
           "direction": "long" if held > 0 else ("short" if held < 0 else "flat")}
    if isinstance(result, dict):
        return dict(result, mode="position", position=pos)
    first = next((c for c in result if c.chain_quality not in (None, "n/a")), None)
    note = f"hedges for the position held ({held:+,} {inst.symbol}): only the hedge legs trade - nothing is bought or sold in {inst.symbol}"
    return {"mode": "position", "position": pos, "status": "ready", "candidates": [c.to_dict() for c in result],
            "chain_quality": first.chain_quality if first else None, "as_of": first.as_of if first else None, "note": note}


def _resolve_legs(ctx: TxContext, services, praw, inst, candidate_id: Optional[str], ratio, edits: Optional[dict]) -> Tuple[List[dict], Any]:
    uid = inst.underlying_id if inst.kind == "OPTION" else inst.instrument_id
    chain = None
    if hasattr(ctx.marks, "chain"):
        chain = ctx.marks.chain(uid)
    if chain is None:
        chains = getattr(services, "chains", None)
        chain = chains.peek(uid) if chains is not None and hasattr(chains, "peek") else None
    cands = candidates(services, praw["account_id"], inst, praw["side"], int(praw["qty"]), chain=chain, now=ctx.ts, edits=edits)
    if isinstance(cands, dict):
        raise ConflictError("NO_CHAIN", f"{uid}: {cands.get('note') or 'the option chain is not cached yet'}; preview the hedge again", dict(cands, candidate_id=candidate_id))
    cand = next((c for c in cands if c.id == candidate_id), None)
    if cand is None:
        raise RejectError("NOT_AVAILABLE", f"hedge candidate {candidate_id!r} is not offered for {praw['side']} {praw['qty']:,} {inst.symbol}",
                          {"candidates": [c.id for c in cands]})
    if cand.submit_via != "order":
        raise RejectError("NOT_AVAILABLE", f"{cand.id} is booked on the OTC desk (otc.book), not as an order leg", {"candidate_id": cand.id})
    if not cand.available:
        raise RejectError("NOT_AVAILABLE", f"{cand.id}: {cand.reason}", {"candidate_id": cand.id})
    legs = [{"instrument_id": l.instrument_id, "side": l.side, "qty": int(l.qty)} for l in cand.legs]
    if ratio is not None and str(ratio).strip() != "":
        r = _edit_decimal({"ratio": ratio}, "ratio")
        pq = int(praw["qty"])
        for l in legs:
            l["qty"] = int((Decimal(pq) * r).to_integral_value(rounding="ROUND_FLOOR"))
            if l["qty"] <= 0:
                raise RejectError("QTY_INVALID", f"ratio {r} sizes the {l['instrument_id']} leg to 0 for a primary of {pq:,}")
    return legs, chain


def package(ctx: TxContext, services, primary_order_id: str, candidate_id: Optional[str], legs: Optional[List[dict]], ratio) -> str:
    """Attach a hedge package to a submitted primary (§10.9, §10.7 step 8): explicit ``legs`` or the candidate's legs
    (``ratio`` overrides each leg's size to floor(primary qty × ratio); the ticket's ``hedge.edits`` — read from the
    command payload — move the protective leg's strike / expiry); option legs are materialised from the chain; the
    children are created WAITING_PARENT by ``orders.create_hedge_children``.  Returns the package id."""
    from . import orders as _orders
    conn = ctx.conn
    praw = conn.execute("SELECT * FROM orders WHERE order_id=?", (primary_order_id,)).fetchone()
    if praw is None:
        raise RejectError("NOT_FOUND", f"order {primary_order_id} does not exist")
    inst = services.instruments.get(praw["instrument_id"], conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {praw['instrument_id']} is not in the master")
    hedge_req = (ctx.payload or {}).get("hedge") if isinstance((ctx.payload or {}).get("hedge"), dict) else {}
    edits = hedge_req.get("edits") if isinstance(hedge_req.get("edits"), dict) else None
    chain = None
    if legs:
        resolved = [{"instrument_id": l.get("instrument_id"), "side": str(l.get("side") or "").upper(), "qty": int(l.get("qty") or 0),
                     "order_type": l.get("order_type"), "limit_px": l.get("limit_px")} for l in legs]
        uid = inst.underlying_id if inst.kind == "OPTION" else inst.instrument_id
        chain = ctx.marks.chain(uid) if hasattr(ctx.marks, "chain") else None
    else:
        resolved, chain = _resolve_legs(ctx, services, praw, inst, candidate_id, ratio, edits)
    for l in resolved:
        iid = l.get("instrument_id")
        if not iid or l["qty"] <= 0:
            raise RejectError("QTY_INVALID", f"hedge leg {l} needs an instrument and a positive quantity")
        if services.instruments.get(iid, conn) is None and parse_occ(iid) is not None:
            _options.ensure_option(services, iid, chain=chain, tx=ctx.tx)
        if l["side"] == "SELL_SHORT" and getattr(services, "lending", None) is None:
            raise RejectError("NOT_IN_THIS_BUILD", f"hedge leg {iid}: a short sale needs the securities-lending package (WP-06)")
    return _orders.create_hedge_children(ctx, services, primary_order_id, resolved, candidate_id or "HEDGE")


def register_routes(router) -> None:
    """C1 (BUILD2_PLAN): ``GET /api/hedges`` is served here (ahead of api_products' route) so ``mode=position`` reaches
    ``position_candidates`` (USX-12 / ORC-4); every other call answers exactly as before: ``account_id``, ``instrument_id``,
    ``side``, ``qty`` -> the candidate list; 202 ``{status: fetching, retry_ms}`` while the chain loads; 400 QTY_INVALID;
    404 for an unknown account or instrument."""
    from .api import ApiError, NotFound, RETRY_MS

    def hedges_route(req):
        rt = req.rt
        aid = req.q("account_id") or ""
        with rt.db.reader() as conn:
            if account_row(conn, aid) is None:
                raise NotFound(f"account {aid} does not exist")
        iid = str(req.q("instrument_id") or "").strip().upper()
        inst = rt.instruments.get(iid)
        if inst is None:
            raise NotFound(f"instrument {iid} is not in the master")
        if str(req.q("mode") or "").lower() == "position":
            edits = {k: req.q(k) for k in ("strike", "expiry") if req.q(k)}
            held, out = position_candidates(rt, aid, inst, now=req.now, edits=edits or None)
            body = position_payload(inst, aid, held, out)
            if body.get("status") == "fetching":
                return 202, dict(body, retry_ms=body.get("retry_ms") or RETRY_MS)
            return body
        qty = req.q_int("qty", 0, 0, 10**9)
        if qty <= 0:
            raise ApiError(400, "QTY_INVALID", "qty must be a positive whole number")
        out = candidates(rt, aid, inst, (req.q("side") or "BUY").upper(), qty, now=req.now)
        if isinstance(out, dict):
            if out.get("status") == "fetching":
                return 202, dict(out, retry_ms=out.get("retry_ms") or RETRY_MS)
            return out
        return [c.to_dict() for c in out]

    router.add("GET", r"/api/hedges", hedges_route)


__all__ = ["HedgeCandidate", "HedgeLeg", "candidates", "package", "pick_expiry", "snap_strike", "round_to_100", "round_half_up", "ratio_of", "front_month",
           "chain_quality", "MIN_DAYS", "SEQUENTIAL_NOTE", "position_candidates", "position_payload", "position_qty", "ratio_text", "register_routes"]
