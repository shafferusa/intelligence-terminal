"""PB OTC single-name credit default swaps (release 1.0.7, CDS_SINGLE_NAME_PLAN rev 2, package B1) - the ``CDS`` delegate kind of
``products/swaps.py`` (the owner 2026-10-28 17:05 ET: "CDS needs added to prime brokers, i see CDX, but I want to CDS on everything you can").

Bound as ``services.swaps_cds_names`` (server.OPTIONAL_MODULES, after ``swaps_cds``, ``credit_entities`` and ``cds_model``); the contracts are
``otc_contracts`` rows of kind ``CDS`` (``CDS-000001`` ...) booked, terminated and margined through ``otc.book`` / ``otc.rfq.*`` /
``otc.terminate`` / SWAP_VM: ``swaps.py`` runs the common gate first (the kind through ``prime.require_otc_kind``, a prime-broker account,
``books.require_pb_otc``) and dispatches CDS here (``_module_for``).  The ISDA-style pricing, the IMM dates, the premium leg, the coupons, the
VM and the close-out are the CDX machinery (``swaps_cds``'s public pure functions, ``swaps.close_contract``), imported, not rewritten; the
owner's CDX contracts never touch this module.

What a contract is (plan section 2)
- One SEC registrant (CIK) with a US listing, its senior unsecured level (no reference-obligation ISIN: the ISDA standard reference
  obligation list is not freely available), XR14 credit events (bankruptcy and failure to pay, no restructuring), simulated only.
- BUY_PROTECTION (pay the coupon; short credit) or SELL_PROTECTION (receive it; long credit); USD 100,000.00 .. 1,000,000,000.00.
- Tenor 1, 2, 3, 4, 5, 7 or 10 years (5 by default), the semi-annual single-name roll (ISDA 2015): the roll date is the latest
  20 March / 20 September (rolled following) on or before the trade date, and the maturity is 20 December (a September roll) or 20 June
  (a March roll) n years after the roll's year.
- Coupon 100 or 500 bp running, the owner's choice; by default 100 bp unless the MODEL spread's bucket before any negotiated basis is
  BB-like or worse (then 500 bp).  Recovery 40 % for pricing.  Dates and accrual exactly as CDX (quarterly IMM dates rolled following,
  ACT/360, full first coupon with the accrued rebate, the last period includes the maturity).
- The upfront U = (S - C) x RPV01(S) x N (the flat-hazard approximation of ``swaps_cds``, labelled MODEL) and the accrued rebate settle net
  three business days after the trade (the bilateral standard; the PB is the counterparty of record).

The spread is a MODEL (``services.cds_model``): equity-implied CreditGrades plus the ICE BofA AAA OAS floor, labelled with its inputs, NOT
validated against traded levels.  So no new deal runs on the bare model: a deal on a name in a PB account needs the owner's dealing level
there - his NEGOTIATED mid (stored as a basis to the model, so marks keep moving with the model's inputs) or his confirmation to deal at the
MODEL (``cds.basis``).  Without one the preview shows every number but is not bookable (DEALING_LEVEL_UNCONFIRMED) and ``otc.rfq.request``
/ ``otc.book`` answer 409 with the same words.  A distressed name needs a negotiated mid (DISTRESSED_NEEDS_NEGOTIATED_MID).  Marks, VM,
termination and credit events never need a level.  mid = MODEL spread + the account's basis; the dealer deals at mid +/- the tier's
half-spread x the PB schedule's OTC spread multiplier, against the owner; the tier (and so the IM % and the half-spread) is the
rating-like bucket of the MID on the same day's FRED ladder.  An accepted quote is firm on four terms - the dealt spread, the coupon,
the maturity and the tier - so a coupon left to its default can never flip between the quote and the booking.

Postings (plan 2.3, 2.4, 5.3; no new GL): IM Dr 1330 / Cr 1010 (``im:{cid}:open``); the initial payment ``CDS_UPFRONT`` (``otc:{cid}:upfront``):
the upfront as the contract's cost basis (Dr 1335 paid / Cr 2345 received), the rebate (Cr 2340 buyer / Dr 1225 seller), the net Dr 1200 /
Cr 2100 with a settlement row due T+3; premium nights through ``swaps.accrue_night`` (Dr 4453 / Cr 2340 buyer, Dr 1225 / Cr 4453 seller);
CDS_COUPON (``cdscpn:{cid}:{date}``, 00:45 ET of each payment date; the last one matures the contract); SWAP_VM on the model value of the
VM date's inputs; termination at mid -/+ the half-spread (``otc:{cid}:terminate``); the owner's simulated credit event (``otc:{cid}:credit``
then ``otc:{cid}:credit-close``) closes the contract.

Risk (plan 5.2): CS01 (+1 bp of the spread, hazard re-solved), jump to default = sign x (1 - 40 %) x N - the contract's value now, per
name the net JTD and its share of NAV (a WARN above 10 %), spread scenarios and equity scenarios through the model (``cds_model.spread_at``).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from .. import prime, tables
from .. import marks as _marks
from ..books import account_row, eligibility as _eligibility, split_account_id
from ..clock import epoch_of, ny_of
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..ledger import Entry, balance, cr, dr, natural
from ..quotes import MarkView
from ..units import fmt_money, from_c, qprice, to_c
from . import swaps
from . import swaps_cds as _cdx                 # only names in its __all__ (plan 6.1): dates, the ISDA-style pure pricing functions

KIND = "CDS"
SOURCE = "swaps_cds_names"
JOB_COUPON = "CDS_COUPON"
COUPON_ET = "00:45"
TENORS = (1, 2, 3, 4, 5, 7, 10)
DEFAULT_TENOR = 5
COUPONS_BP = (100, 500)
RECOVERY = Decimal("0.40")
UPFRONT_SETTLE_BD = 3                           # the initial payment: T+3 business days (the bilateral standard, Q6)
CREDIT_SETTLE_BD = 1                            # the protection settlement, net of the accrued premium: T+1 business day after the event date
MIN_NOTIONAL_C = 100_000_00
MAX_NOTIONAL_C = 1_000_000_000_00
MODEL_VERSION = "CG2002-AAA-FLOOR-1"
SENIORITY = "SENIOR_UNSECURED"
DOC_CLAUSE = "XR14"
SIDES = ("BUY_PROTECTION", "SELL_PROTECTION")
EVENT_TYPES = {"BANKRUPTCY": "bankruptcy", "FAILURE_TO_PAY": "failure to pay"}
LEVEL_KINDS = ("NEGOTIATED", "MODEL")
COVERED = ("COVERED", "OFFERED")                # the C-ENT status of an offered reference entity
ZERO, ONE = Decimal(0), Decimal(1)
PCT = Decimal(100)
BP = Decimal("0.0001")
BP_Q = Decimal("0.0001")                         # spreads in bp to 0.0001 bp, as CDX
BP2 = Decimal("0.01")
RPV_Q = Decimal("0.0000000001")
PX4 = Decimal("0.0001")
D365 = Decimal(365)
MID_FLOOR_BP = Decimal(1)                        # a scenario's shocked mid is floored at 1 bp (plan 5.2)
JTD_WARN = Decimal("0.10")                       # CDS_JTD_CONCENTRATION: one name's net jump-to-default loss above 10 % of NAV (Q9)
SPREAD_SHOCKS_BP = (-25, 25, 50, 100)
LABEL = "MODEL"
LABEL_WORDS = "MODEL - equity-implied CreditGrades spread plus the ICE BofA AAA OAS floor; not a dealer quote"
OBLIGATION_WORDS = "senior unsecured, XR14 (bankruptcy, failure to pay; no restructuring), no reference-obligation ISIN"
CREDIT_EVENT_LABEL = "simulated credit event - recovery entered by the owner; no ISDA Determinations Committee ruling or auction in the simulator"
CONVENTIONS = ("CDS_MODEL", "CDS_DEAL", "CDS_COUPON", "CDS_CREDIT", "SWAP_VM", "ELIGIBILITY")
PRICING = ("ISDA-style approximation: flat hazard lambda = S / (1 - R), Q(t) = exp(-lambda t); RPV01 = sum accrual x DF x (Q(start) + Q(end)) / 2"
           " over the remaining coupon periods from the step-in date; upfront = (S - C) x RPV01; DF from the USD curve the swaps use")

#: the rating-like buckets of the FRED ladder -> the dealing tier (plan 5.1); the tier sets the IM % and the dealer half-spread
TIERS = ("A_OR_BETTER", "BBB", "BB", "B", "CCC")
TIER_OF_BUCKET = {"AAA": "A_OR_BETTER", "AA": "A_OR_BETTER", "A": "A_OR_BETTER", "BBB": "BBB", "BB": "BB", "B": "B", "CCC": "CCC"}
TIER_BUCKETS = {"A_OR_BETTER": ["AAA", "AA", "A"], "BBB": ["BBB"], "BB": ["BB"], "B": ["B"], "CCC": ["CCC"]}
TIER_WORDS = {"A_OR_BETTER": "A-like or better", "BBB": "BBB-like", "BB": "BB-like", "B": "B-like", "CCC": "CCC-like"}
HIGH_YIELD_BUCKETS = ("BB", "B", "CCC")          # the MODEL bucket that defaults the coupon to 500 bp (else 100 bp)
#: the IM seeds by broker group (ASSUMED, editable per account): GS / JPM / CITI / BOFA keep the lowest terms, as for the swaps (plan 5.1)
BROKER_GROUP = {"GS": 0, "JPM": 0, "CITI": 0, "BOFA": 0, "MS": 1, "BARC": 1, "UBS": 1, "DB": 2, "BNP": 2}
IM_SEEDS = {"A_OR_BETTER": ("0.04", "0.05", "0.06"), "BBB": ("0.05", "0.06", "0.075"), "BB": ("0.08", "0.09", "0.10"),
            "B": ("0.12", "0.135", "0.15"), "CCC": ("0.25", "0.275", "0.30")}
HALF_SPREAD_SEEDS = {"A_OR_BETTER": "2.0", "BBB": "3.0", "BB": "7.5", "B": "15.0", "CCC": "40.0"}
TERM_SPECS = {"im_pct": {"label": "initial margin", "unit": "pct", "min": Decimal(0), "max": Decimal("0.99"),
                         "hint": "a fraction of the notional posted to the PB for the contract's life, by the tier the quote fixed (assumed)"},
              "half_spread_bp": {"label": "dealer half-spread", "unit": "bp", "min": Decimal(0), "max": Decimal(500),
                                 "hint": "you deal at the mid spread +/- this (x the PB's OTC spread multiplier), against you (assumed)"}}
REASON_WORDS = {"BELOW_MIN_DEBT": "not offered: reported debt is below the USD 100,000,000 minimum (assumed)",
                "NO_DEBT": "not offered: no debt reported in the latest 10-Q / 10-K XBRL (or it uses the company's own tags)",
                "NO_SHARES": "not offered: share count not reported (multi-class issuer?)",
                "STALE_FUNDAMENTALS": "not offered: the latest balance sheet is more than 15 months old",
                "FOREIGN_FILER": "not offered: foreign private issuer (ADR ratios and IFRS reports are not modelled in this release)",
                "DEBT_GT_LIABILITIES": "not offered: the reported debt exceeds the reported liabilities (the filing's tags cannot be read reliably)"}

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS cds_terms (account_id TEXT PRIMARY KEY, terms_json TEXT NOT NULL, updated_ts INTEGER NOT NULL, command_id INTEGER)",
    "CREATE TABLE IF NOT EXISTS cds_basis (account_id TEXT NOT NULL, cik INTEGER NOT NULL, level_kind TEXT NOT NULL, basis_bp TEXT NOT NULL,"
    " negotiated_mid_bp TEXT, model_mid_bp TEXT NOT NULL, model_json TEXT NOT NULL, entered_ts INTEGER NOT NULL, command_id INTEGER,"
    " PRIMARY KEY (account_id, cik))",
    "CREATE TABLE IF NOT EXISTS cds_credit_events (contract_id TEXT PRIMARY KEY, cik INTEGER, entity_name TEXT NOT NULL, event_type TEXT NOT NULL,"
    " event_date TEXT NOT NULL, recovery_pct TEXT NOT NULL, notional_c INTEGER NOT NULL, protection_c INTEGER NOT NULL, accrued_c INTEGER NOT NULL,"
    " late_c INTEGER NOT NULL, net_c INTEGER NOT NULL, settle_date TEXT NOT NULL, settle_id TEXT, entry_id INTEGER, close_entry_id INTEGER,"
    " command_id INTEGER, ts INTEGER NOT NULL)",
]

tables.add_conventions({
    "CDS_MODEL": ("Single-name CDS spreads are a MODEL: the equity-implied CreditGrades spread (share price, 1000-day realised volatility,"
                  " SEC-reported debt per share) plus the ICE BofA AAA corporate OAS from FRED; not a dealer quote, and not validated against"
                  " traded levels. A new deal on a name needs your negotiated mid or your confirmation to deal at the MODEL; a negotiated mid"
                  " moves with the model from then on."),
    "CDS_DEAL": ("Single-name CDS deal at the mid plus or minus the dealer's half-spread for the spread bucket, against you; the upfront ="
                 " (spread - coupon) x the risky annuity, with the accrued rebate, settles three business days later; full first coupon."),
    "CDS_COUPON": ("A protection buyer pays the fixed coupon (100 or 500 bp) quarterly on the 20th of March, June, September and December (next"
                   " business day), ACT/360; daily variation margin on the model value under the CSA."),
    "CDS_CREDIT": ("Credit events are simulated only when you enter one (bankruptcy or failure to pay, at the recovery you enter): the"
                   " protection, less the accrued premium, settles the next business day and the contract closes."),
})


class NotFound(RejectError):
    http = 404


# ---- small helpers -----------------------------------------------------------------------------------------------------------------
def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _terms(row: dict) -> dict:
    try:
        return json.loads(row.get("terms_json") or "{}")
    except ValueError:
        return {}


def _dec(x, default=None) -> Optional[Decimal]:
    if x is None or isinstance(x, bool) or (isinstance(x, str) and not x.strip()):
        return default
    try:
        d = Decimal(str(x).strip())
    except (InvalidOperation, ValueError, TypeError):
        return default
    return d if d.is_finite() else default


def _js(x):
    """JSON-safe copy (Decimals and dates as strings) of what goes into a stored payload or an error's details."""
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): _js(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_js(v) for v in x]
    return x


def _sign(side: str) -> int:
    """+1 for a protection BUYER (short credit: gains when spreads widen), -1 for a seller."""
    return 1 if side == "BUY_PROTECTION" else -1


def _bp_words(x) -> str:
    """A spread in words: 2 decimals, thousands separated ("2,300.22 bp")."""
    d = _dec(x)
    return "unavailable" if d is None else f"{d.quantize(BP2):,} bp"


def _signed_bp(x) -> str:
    d = _dec(x, ZERO).quantize(BP2)
    return f"{'+' if d >= 0 else '-'}{abs(d)}"


def _pct_words(fraction, places: str = "0.01") -> str:
    return f"{(Decimal(fraction) * PCT).quantize(Decimal(places))} %"


def _ts_words(ts) -> str:
    return ny_of(int(ts)).strftime("%Y-%m-%d %H:%M ET") if ts not in (None, "") else "an unrecorded time"


def _blocker(code: str, message: str, **details) -> dict:
    return {"code": code, "message": message, "details": _js(details)}


def maturity_for(roll: date, tenor_years: int) -> date:
    """The single-name maturity (plan 2.1): 20 December (a September roll) or 20 June (a March roll) ``tenor_years`` after the roll's year."""
    return date(roll.year + int(tenor_years), 12 if roll.month == 9 else 6, 20)


def roll_of(cal, d: date) -> dict:
    """The on-the-run roll on ``d``: the latest 20 March / 20 September rolled following on or before it, the next one, and every
    tenor's maturity (``swaps_cds.on_the_run`` gives the roll; its series number is the CDX's and is not used here)."""
    otr = _cdx.on_the_run(cal, d)
    roll = otr["roll_date"]
    return {"roll_date": roll, "next_roll": otr["next_roll"], "maturities": {str(n): maturity_for(roll, n) for n in TENORS}}


def entity_label(ticker, tenor_years, name=None) -> str:
    """"ORCL 5Y CDS (Oracle Corp)"."""
    head = f"{ticker or 'CDS'} {tenor_years or '?'}Y CDS"
    return f"{head} ({name})" if name else head


def _reason_words(code, reason=None) -> str:
    r = str(reason or "").strip()
    if r and not (r.replace("_", "").isalnum() and r.upper() == r):
        return r
    return REASON_WORDS.get(str(code or r or "").upper(), r.replace("_", " ").lower() or "not offered")


# ---- the other CDS modules (C-ENT, C-MODEL), reached through services at call time only (plan 6.1) ------------------------------------------
def _entities(services):
    return getattr(services, "credit_entities", None)


def _model_mod(services):
    return getattr(services, "cds_model", None)


def _resolve(services, conn, t: dict) -> dict:
    """The covered reference entity of the terms (``cik`` or ``ticker``) through C-ENT; 404 ENTITY_UNKNOWN, 409 NOT_COVERED with the reason
    in words, 409 ENTITIES_UNAVAILABLE without the module."""
    ce = _entities(services)
    fn = getattr(ce, "entity", None) if ce is not None else None
    if not callable(fn):
        raise ConflictError("ENTITIES_UNAVAILABLE", "the single-name CDS reference entities (SEC EDGAR) are not available in this build: no single-name"
                            " CDS can be priced", {})
    key = t.get("cik") if t.get("cik") is not None else t.get("ticker")
    ent = fn(conn, key)
    if not ent:
        raise NotFound("ENTITY_UNKNOWN", f"no reference entity {key}: no US-listed SEC registrant has that {'CIK' if t.get('cik') is not None else 'ticker'}",
                       {"field": "cik" if t.get("cik") is not None else "ticker", "key": key})
    ent = dict(ent)
    if t.get("cik") is not None and t.get("ticker"):
        have = {str(x).upper() for x in ([ent.get("ticker")] + list(ent.get("tickers") or []))}
        if str(t["ticker"]).upper() not in have:
            raise RejectError("TERMS", f"ticker {t['ticker']} is not a ticker of CIK {t['cik']} ({ent.get('name')}: {ent.get('ticker')})", {"field": "ticker"})
    status = str(ent.get("status") or "").upper()
    if status not in COVERED:
        words = ent.get("status_words") or _reason_words(ent.get("status_reason") or status, ent.get("status_reason"))
        raise ConflictError("NOT_COVERED", f"{ent.get('name')} ({ent.get('ticker')}): {words}",
                            {"cik": ent.get("cik"), "ticker": ent.get("ticker"), "status": status, "reason": ent.get("status_reason")})
    return ent


def model_now(services, cik: int, maturity: date, conn=None, now: Optional[int] = None, valuation_date: Optional[date] = None, marks=None) -> dict:
    """``services.cds_model.model_spread`` (C-MODEL) as a dict, never an exception: ``{ok: False, code, reason}`` when the module is missing or
    fails (named, never a guessed spread)."""
    cm = _model_mod(services)
    fn = getattr(cm, "model_spread", None) if cm is not None else None
    if not callable(fn):
        return {"ok": False, "code": "MODEL_UNAVAILABLE", "reason": "the single-name CDS spread model is not available in this build"}
    try:
        out = swaps._call(fn, services, int(cik), maturity, valuation_date=valuation_date, conn=conn, now=now, marks=marks)
    except RejectError as e:
        return dict(e.details or {}, ok=False, code=e.code, reason=e.message)
    except Exception as e:   # noqa: BLE001 - a model failure is named, never a crash and never a default
        return {"ok": False, "code": "MODEL_ERROR", "reason": f"the spread model failed: {type(e).__name__}: {str(e)[:160]}"}
    if not isinstance(out, dict):
        return {"ok": False, "code": "MODEL_ERROR", "reason": "the spread model gave no result"}
    return out


def _raise_model(m: dict) -> None:
    code = str(m.get("code") or "MODEL_UNAVAILABLE")
    reason = str(m.get("reason") or m.get("note") or code)
    details = _js({k: v for k, v in m.items() if k not in ("ok", "code", "reason")})
    if code == "ENTITY_UNKNOWN":
        raise NotFound(code, reason, details)
    raise ConflictError(code, reason, details)


def _ladder_bp(model: dict) -> Dict[str, Decimal]:
    out = {}
    for b, r in (((model or {}).get("ladder") or {}).get("rows") or {}).items():
        v = _dec((r or {}).get("spread_bp") if isinstance(r, dict) else r)
        if v is not None and v > 0:
            out[str(b)] = v
    return out


def bucket_of_mid(services, mid, model: dict) -> Optional[str]:
    """The rating-like bucket of a mid on the model's own (same day's) FRED ladder (``cds_model.bucket_of``); None without a ladder."""
    ladder = _ladder_bp(model)
    cm = _model_mod(services)
    fn = getattr(cm, "bucket_of", None) if cm is not None else None
    if not ladder or not callable(fn):
        return None
    try:
        b = fn(Decimal(str(mid)), ladder)
    except Exception:   # noqa: BLE001 - no bucket: the caller refuses with the reason
        return None
    return str(b) if b in TIER_OF_BUCKET else None


def default_coupon_bp(model: dict) -> int:
    """100 bp unless the MODEL spread's bucket (before any negotiated basis) is BB-like or worse, then 500 bp (plan 2.1, Q11)."""
    return 500 if str((model or {}).get("bucket") or "").upper() in HIGH_YIELD_BUCKETS else 100


def _model_text(services) -> str:
    cm = _model_mod(services)
    return str(getattr(cm, "MODEL_TEXT", "") or "equity-implied CreditGrades plus the ICE BofA AAA OAS floor - a MODEL spread, not a dealer quote")


def _model_summary(model: dict) -> dict:
    """What a contract keeps of the model (``terms.model``, the stored inputs): the spread, its parts and inputs - not the ladder."""
    m = model or {}
    fl = m.get("floor") or {}
    return _js({"spread_bp": m.get("spread_bp"), "raw_bp": m.get("raw_bp"), "floor_bp": m.get("floor_bp"), "floor_as_of": fl.get("as_of"),
                "bucket": m.get("bucket"), "quality": m.get("quality"), "method": m.get("method"), "inputs": m.get("inputs") or {},
                "label": m.get("label") or LABEL, "valuation_date": m.get("valuation_date"), "maturity_date": m.get("maturity_date")})


# ---- the editable tier terms (cds.terms) -----------------------------------------------------------------------------------------------------
def seed_terms(broker: str) -> Dict[str, Dict[str, Decimal]]:
    """The seed IM % and half-spread of every tier for a broker (plan 5.1; ASSUMED)."""
    g = BROKER_GROUP.get(str(broker or "").upper(), 0)
    return {t: {"im_pct": Decimal(IM_SEEDS[t][g]), "half_spread_bp": Decimal(HALF_SPREAD_SEEDS[t])} for t in TIERS}


def _stored_terms(conn, account_id: str) -> Tuple[dict, Optional[int]]:
    try:
        r = conn.execute("SELECT terms_json, updated_ts FROM cds_terms WHERE account_id=?", (account_id,)).fetchone()
    except sqlite3.OperationalError:            # the table is created at bind; a runtime without it uses the seeds
        r = None
    if r is None:
        return {}, None
    try:
        data = json.loads(r["terms_json"] or "{}")
    except ValueError:
        data = {}
    return (data if isinstance(data, dict) else {}), int(r["updated_ts"] or 0)


def tiers_for(conn, account_id: str, broker: str) -> Dict[str, dict]:
    """Every tier's ``{im_pct, half_spread_bp, source, updated_ts, seed}`` for the account: the stored values, else the broker's seeds."""
    seeds = seed_terms(broker)
    data, ts = _stored_terms(conn, account_id)
    out = {}
    for t in TIERS:
        mine = data.get(t) if isinstance(data.get(t), dict) else {}
        row = {}
        for k, seed in seeds[t].items():
            v = _dec(mine.get(k))
            row[k] = v if v is not None else seed
        row["source"] = "as edited (assumed)" if mine else "seed (assumed)"
        row["updated_ts"] = ts if mine else None
        row["seed"] = dict(seeds[t])
        out[t] = row
    return out


# ---- the dealing level (cds_basis) -------------------------------------------------------------------------------------------------------------
def level_of(conn, account_id: str, cik) -> Optional[dict]:
    """The account's dealing level for a reference entity (``cds_basis``): ``{level_kind, basis_bp, negotiated_mid_bp, model_mid_bp, entered_ts}``;
    None when the owner has set none (the guard applies)."""
    if cik in (None, ""):
        return None
    try:
        r = conn.execute("SELECT * FROM cds_basis WHERE account_id=? AND cik=?", (account_id, int(cik))).fetchone()
    except (sqlite3.OperationalError, ValueError, TypeError):
        return None
    return None if r is None else {k: r[k] for k in r.keys()}


def _basis(level: Optional[dict]) -> Decimal:
    return ZERO if not level else (_dec(level.get("basis_bp"), ZERO) or ZERO)


def level_words(level: Optional[dict], model_bp, mid) -> str:
    """"mid 84.00 bp = model 65.47 bp + your negotiated basis +18.53 bp (set 2026-10-29 10:14 ET)" / "mid 65.47 bp = MODEL (you confirmed ...)"."""
    if not level:
        return f"mid {_bp_words(mid)} = MODEL {_bp_words(model_bp)}; no dealing level set - not bookable until you set your negotiated mid or confirm dealing at the MODEL"
    if level.get("level_kind") == "NEGOTIATED":
        return (f"mid {_bp_words(mid)} = model {_bp_words(model_bp)} + your negotiated basis {_signed_bp(level.get('basis_bp'))} bp"
                f" (set {_ts_words(level.get('entered_ts'))})")
    return f"mid {_bp_words(mid)} = MODEL (you confirmed dealing at the model on {_ts_words(level.get('entered_ts'))})"


def _no_level_words(ent: dict, model: dict) -> str:
    return (f"No market level is known for {ent.get('name')} ({ent.get('ticker')}): its spread is a MODEL ({_bp_words(model.get('spread_bp'))},"
            f" {model.get('bucket') or '?'}-like) and has not been validated against traded levels. Set your negotiated mid, or confirm dealing at"
            " the MODEL, before asking for a quote.")


def _distressed_words(ent: dict, model: dict) -> str:
    p0 = ((model.get("intermediate") or {}).get("P0"))
    surv = f" (model survival now {Decimal(str(p0)).quantize(PX4)})" if _dec(p0) is not None else ""
    return (f"{ent.get('name')} ({ent.get('ticker')}) is distressed in the MODEL: {_bp_words(model.get('spread_bp'))}{surv}, outside the model's"
            " calibrated range. A new deal needs your negotiated mid; a confirmation to deal at the MODEL is not enough.")


# ---- terms -----------------------------------------------------------------------------------------------------------------------------------
def parse_terms(terms: dict) -> dict:
    """``{cik | ticker, tenor_years, side, notional | notional_c[, coupon_bp]}`` -> validated terms (400 TERMS naming the field).  The
    reference entity itself is resolved in ``_deal`` through C-ENT."""
    terms = dict(terms or {})
    cik = terms.get("cik")
    if cik not in (None, ""):
        if isinstance(cik, (bool, float)):
            raise RejectError("TERMS", "cik must be an integer (the SEC Central Index Key)", {"field": "cik"})
        try:
            cik = int(str(cik).strip())
        except ValueError as e:
            raise RejectError("TERMS", f"cik must be an integer (the SEC Central Index Key), got {terms.get('cik')!r}", {"field": "cik"}) from e
        if cik <= 0:
            raise RejectError("TERMS", "cik must be a positive integer", {"field": "cik"})
    else:
        cik = None
    ticker = str(terms.get("ticker") or "").strip().upper() or None
    if cik is None and ticker is None:
        raise RejectError("TERMS", "the reference entity is required: cik (SEC Central Index Key) or ticker", {"field": "cik"})
    raw_t = terms.get("tenor_years", terms.get("tenor"))
    if raw_t in (None, ""):
        tenor = DEFAULT_TENOR
    else:
        try:
            tenor = int(str(raw_t).strip().upper().rstrip("Y"))
        except ValueError as e:
            raise RejectError("TERMS", f"tenor_years must be one of {', '.join(str(x) for x in TENORS)}, got {raw_t!r}", {"field": "tenor_years"}) from e
        if isinstance(raw_t, float) or tenor not in TENORS:
            raise RejectError("TERMS", f"tenor_years must be one of {', '.join(str(x) for x in TENORS)} (years), got {raw_t!r}", {"field": "tenor_years"})
    side = str(terms.get("side") or "").strip().upper().replace(" ", "_")
    side = {"BUY": "BUY_PROTECTION", "SELL": "SELL_PROTECTION", "BUY_PROT": "BUY_PROTECTION", "SELL_PROT": "SELL_PROTECTION"}.get(side, side)
    if side not in SIDES:
        raise RejectError("TERMS", "side must be BUY_PROTECTION (pay the coupon, short credit) or SELL_PROTECTION (receive it, long credit)", {"field": "side"})
    raw_c, raw = terms.get("notional_c"), terms.get("notional")
    if isinstance(raw_c, float) or isinstance(raw, float):
        raise RejectError("TERMS", "notional must be a decimal string or integer cents, not a float", {"field": "notional"})
    try:
        if raw_c not in (None, "") and not isinstance(raw_c, bool):
            n = int(Decimal(str(raw_c)))
        elif raw not in (None, ""):
            d = Decimal(str(raw).strip().replace(",", ""))
            if d != d.quantize(Decimal("0.01")):
                raise RejectError("TERMS", f"notional {raw!r} has more than 2 decimals", {"field": "notional"})
            n = to_c(d)
        else:
            raise RejectError("TERMS", "notional is required (USD, e.g. 25000000.00)", {"field": "notional"})
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"notional is not a number: {raw if raw is not None else raw_c!r}", {"field": "notional"}) from e
    if n < MIN_NOTIONAL_C or n > MAX_NOTIONAL_C:
        raise RejectError("TERMS", f"notional must be between {fmt_money(MIN_NOTIONAL_C)} and {fmt_money(MAX_NOTIONAL_C)} USD", {"field": "notional"})
    c = terms.get("coupon_bp")
    if c in (None, ""):
        coupon = None
    else:
        d = _dec(c)
        if isinstance(c, (bool, float)) or d is None or d not in (Decimal(100), Decimal(500)):
            raise RejectError("TERMS", f"coupon_bp must be 100 or 500 (the standard running coupons), got {c!r}", {"field": "coupon_bp"})
        coupon = int(d)
    return {"cik": cik, "ticker": ticker, "tenor_years": tenor, "side": side, "notional_c": n, "coupon_bp": coupon}


# ---- the curve -------------------------------------------------------------------------------------------------------------------------------
def _curve(services, periods, valuation: date, on: Optional[date] = None):
    """The swaps' USD discount factors far enough for the last payment (``swaps.dfs_for``; 409 NO_CURVE without a curve); ``on``: the curve
    OF that date (a VM date), never today's."""
    last = max((p for _s, _e, p in periods), default=valuation)
    years = Decimal(max(1, (last - valuation).days)) / D365
    n = int((years * 2).to_integral_value(rounding="ROUND_CEILING")) + 1
    return swaps.dfs_for(services, max(2, n), on=on)


# ---- the deal (preview and booking share it) ---------------------------------------------------------------------------------------------
def _deal(services, conn, acct: dict, t: dict, now: int, firm: Optional[dict] = None, marks=None) -> dict:
    """Everything a single-name CDS preview shows and a booking posts (plan B1 task 3; no network): the covered entity, the maturity, the
    MODEL (C-MODEL; not ok -> its code), the account's dealing level and the mid, the tier, the coupon, the dealer's spread, the curve, the
    values, the rebate, the initial payment and the IM.  ``firm`` (C14): the accepted quote's dealt spread, coupon, maturity and tier."""
    firm = dict(firm or {})
    cal = services.cal
    ts = int(now)
    today = ny_of(ts).date()
    ent = _resolve(services, conn, t)
    cik = int(ent["cik"])
    roll = roll_of(cal, today)
    maturity = maturity_for(roll["roll_date"], t["tenor_years"])
    if firm.get("maturity_date"):
        maturity = _date(firm["maturity_date"])                    # C14: the maturity the dealer quoted
    if maturity <= today:
        raise ConflictError("TERMS", f"the maturity {maturity.isoformat()} is not after the trade date {today.isoformat()}", {"field": "tenor_years"})
    periods = _cdx.coupon_periods(cal, today, maturity)
    model = model_now(services, cik, maturity, conn=conn, now=ts, marks=marks)
    if not model.get("ok"):
        _raise_model(model)
    if model.get("dealable") is False:                             # C-MODEL: the name is not offered as of the model's date (a new deal only)
        raise ConflictError("NOT_COVERED", f"{ent.get('name')} ({ent.get('ticker')}): {model.get('dealable_reason') or 'not offered'}",
                            {"cik": cik, "ticker": ent.get("ticker"), "reason": model.get("status_reason")})
    level = level_of(conn, acct["account_id"], cik)
    distressed = str(model.get("quality") or "") == "distressed"
    if distressed and (level is None or level.get("level_kind") != "NEGOTIATED"):
        raise ConflictError("DISTRESSED_NEEDS_NEGOTIATED_MID", _distressed_words(ent, model),
                            {"cik": cik, "ticker": ent.get("ticker"), "model_spread_bp": model.get("spread_bp"), "level_kind": (level or {}).get("level_kind")})
    model_bp = Decimal(str(model["spread_bp"]))
    basis = _basis(level)
    mid = (model_bp + basis).quantize(BP_Q)
    if mid <= 0:
        raise ConflictError("MID_NOT_POSITIVE", f"the mid {_bp_words(mid)} (MODEL {_bp_words(model_bp)} + your basis {_signed_bp(basis)} bp) is not positive:"
                            " set a new negotiated mid", {"model_spread_bp": str(model_bp), "basis_bp": str(basis)})
    blockers = []
    if level is None:
        blockers.append(_blocker("DEALING_LEVEL_UNCONFIRMED", _no_level_words(ent, model), cik=cik, ticker=ent.get("ticker"),
                                 model_spread_bp=str(model_bp), bucket=model.get("bucket")))
    mid_bucket = bucket_of_mid(services, mid, model)
    tier = firm.get("tier") if firm.get("tier") in TIERS else (TIER_OF_BUCKET.get(mid_bucket) if mid_bucket else None)
    if tier is None:
        raise ConflictError("FLOOR_UNAVAILABLE", "no FRED rating ladder of the model's date: the dealing tier (IM and half-spread) cannot be set", {})
    coupon_default = default_coupon_bp(model)
    if firm.get("coupon_bp") not in (None, ""):
        coupon_bp = int(Decimal(str(firm["coupon_bp"])))
    else:
        coupon_bp = t["coupon_bp"] if t.get("coupon_bp") else coupon_default
    coupon_rule = "owner" if t.get("coupon_bp") else "default from the MODEL bucket"
    broker = prime.broker_of(acct)
    tiers = tiers_for(conn, acct["account_id"], broker)
    mine = tiers[tier]
    sched = prime.read_schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    hs = (mine["half_spread_bp"] * mult).quantize(BP_Q)
    side, sign = t["side"], _sign(t["side"])
    deal_bp = (mid + hs) if sign > 0 else (mid - hs)
    if firm.get("dealt_spread_bp") not in (None, ""):
        deal_bp = Decimal(str(firm["dealt_spread_bp"])).quantize(BP_Q)     # C14: the accepted quote, as quoted
    if deal_bp <= 0:
        raise ConflictError("DEAL_SPREAD", f"the dealt spread {_bp_words(deal_bp)} is not positive (mid {_bp_words(mid)} - half-spread {_bp_words(hs)})", {})
    dfs, curve_inputs = _curve(services, periods, today)
    df = _cdx.df_function(dfs)
    n = int(t["notional_c"])
    c = Decimal(coupon_bp)
    mark0, pmid = _cdx.value_c(side, n, mid, c, RECOVERY, periods, today, df)
    deal_val, pdeal = _cdx.value_c(side, n, deal_bp, c, RECOVERY, periods, today, df)
    cs01 = _cdx.cs01_from(side, n, mid, c, RECOVERY, periods, today, df)
    dv_up, _ = _cdx.value_c(side, n, mid, c, RECOVERY, periods, today, _cdx.df_function(dfs, 1))
    _x0, s0 = _cdx.accrual_start(cal, today)
    days = (today + timedelta(days=1) - s0).days
    reb = _cdx.rebate_c(n, c, days)
    up_buyer = _cdx.upfront_c(n, deal_bp, c, pdeal["rpv01"])
    pays = deal_val                                                     # what THIS holder pays: buyer up_buyer, seller -up_buyer
    cash_in = -pays + (reb if sign > 0 else -reb)                       # net initial payment to the holder (+ received / - paid)
    settle = cal.add_business_days(today, UPFRONT_SETTLE_BD)
    im_pct = mine["im_pct"]
    im = to_c(from_c(n) * im_pct)
    protection = to_c(from_c(n) * (ONE - RECOVERY))
    jtd = sign * protection - mark0
    first = periods[0]
    sched_rows = [{"start": s.isoformat(), "end": (e - timedelta(days=1)).isoformat(), "pay_date": p.isoformat(), "days": (e - s).days,
                   "amount_c": _cdx.coupon_amount_c(n, c, s, e), "direction": "you pay" if sign > 0 else "you receive"} for s, e, p in periods]
    underlying_id = (model.get("underlying") or {}).get("instrument_id") or ent.get("instrument_id")
    return {"entity": ent, "cik": cik, "acct": acct, "broker": broker, "roll": roll, "maturity": maturity, "model": model, "model_bp": model_bp,
            "level": level, "basis": basis, "mid": mid, "mid_bucket": mid_bucket, "tier": tier, "tiers": tiers, "terms_used": mine, "coupon_bp": coupon_bp,
            "coupon_default": coupon_default, "coupon_rule": coupon_rule, "sched": sched, "mult": mult, "hs": hs, "deal_bp": deal_bp, "side": side,
            "sign": sign, "notional_c": n, "tenor_years": t["tenor_years"], "periods": periods, "dfs": dfs, "curve": curve_inputs, "mark0_c": mark0,
            "deal_value_c": deal_val, "pmid": pmid, "pdeal": pdeal, "cs01_c": cs01, "dv01_c": dv_up - mark0, "accrual_start": s0, "rebate_days": days,
            "rebate_c": reb, "upfront_buyer_c": up_buyer, "pays_c": pays, "cash_in_c": cash_in, "settle_date": settle, "im_pct": im_pct, "im_c": im,
            "protection_c": protection, "jtd_c": jtd, "trade_date": today, "step_in": today + timedelta(days=1), "first": first, "schedule": sched_rows,
            "blockers": blockers, "margin_mode": str(sched.swap_margin_mode).upper(), "underlying_id": underlying_id, "firm": firm}


def _side_text(ticker, tenor, side: str) -> str:
    what = f"{ticker or 'CDS'} {tenor}Y"
    return f"buy protection on {what} (short credit)" if side == "BUY_PROTECTION" else f"sell protection on {what} (long credit)"


def _plain(ent: dict, tenor, side: str, n: int, coupon_bp) -> str:
    name = f"{ent.get('name')} ({ent.get('ticker')})"
    if side == "BUY_PROTECTION":
        return (f"Buying protection on USD {fmt_money(n)} of {name} senior unsecured debt for {tenor} years: you pay {coupon_bp} bp a year quarterly"
                f" and are paid (1 - recovery) x the notional if {ent.get('name')} defaults - a SHORT credit position that gains when its credit"
                " spread widens and loses (plus the coupon) when it tightens.")
    return (f"Selling protection on USD {fmt_money(n)} of {name} senior unsecured debt for {tenor} years: you receive {coupon_bp} bp a year quarterly"
            f" and pay (1 - recovery) x the notional if {ent.get('name')} defaults - a LONG credit position like owning its bonds.")


def _entity_view(ent: dict) -> dict:
    keep = ("cik", "ticker", "name", "exchange", "tickers", "status", "status_reason", "status_words", "offered", "flag", "flag_source", "flag_note",
            "limited", "limited_reason", "sic", "sic_desc", "latest_form", "latest_filed", "checked_ts", "instrument_id")
    out = {k: ent.get(k) for k in keep}
    out["fundamentals"] = _js(ent.get("fundamentals"))
    return out


def _model_view(services, model: dict) -> dict:
    m = _js({k: v for k, v in (model or {}).items() if k not in ("ok",)})
    m["text"] = _model_text(services)
    m["label"] = m.get("label") or LABEL
    return m


def _level_view(level: Optional[dict], model_bp, mid) -> dict:
    if not level:
        return {"kind": None, "set": False, "words": "not set - not bookable", "rule": level_words(None, model_bp, mid)}
    return {"kind": level.get("level_kind"), "set": True, "basis_bp": level.get("basis_bp"), "negotiated_mid_bp": level.get("negotiated_mid_bp"),
            "model_mid_bp_at_entry": level.get("model_mid_bp"), "entered_ts": level.get("entered_ts"),
            "words": ("your negotiated mid" if level.get("level_kind") == "NEGOTIATED" else "your confirmation to deal at the MODEL"),
            "rule": level_words(level, model_bp, mid)}


def _preview_dict(services, conn, deal: dict, now: int) -> dict:
    ent, model, sign, n = deal["entity"], deal["model"], deal["sign"], deal["notional_c"]
    acct = deal["acct"]
    tenor = deal["tenor_years"]
    ticker = ent.get("ticker")
    side_text = _side_text(ticker, tenor, deal["side"])
    up_pct = deal["pdeal"]["upfront"] * PCT
    mid_pct = deal["pmid"]["upfront"] * PCT
    buyer_pays = deal["upfront_buyer_c"]
    you = "pay" if deal["pays_c"] > 0 else "receive"
    reb_dir = "received by you (the protection buyer)" if sign > 0 else "paid by you (the protection seller)"
    net = deal["cash_in_c"]
    mine = deal["terms_used"]
    label = entity_label(ticker, tenor, ent.get("name"))
    fl = model.get("floor") or {}
    out = {"kind": KIND, "kind_label": "single-name CDS", "label": LABEL, "label_words": LABEL_WORDS, "account_id": acct["account_id"],
           "broker": prime.broker_of(acct), "entity": _entity_view(ent), "entity_label": label, "cik": deal["cik"], "ticker": ticker,
           "entity_name": ent.get("name"), "seniority": SENIORITY, "doc_clause": DOC_CLAUSE, "obligation": OBLIGATION_WORDS,
           "tenor_years": tenor, "roll_date": deal["roll"]["roll_date"].isoformat(), "next_roll": deal["roll"]["next_roll"].isoformat(),
           "trade_date": deal["trade_date"].isoformat(), "step_in_date": deal["step_in"].isoformat(), "accrual_start": deal["accrual_start"].isoformat(),
           "maturity_date": deal["maturity"].isoformat(), "last_payment_date": deal["periods"][-1][2].isoformat(),
           "side": deal["side"], "side_text": side_text, "plain": _plain(ent, tenor, deal["side"], n, deal["coupon_bp"]), "notional_c": n, "currency": "USD",
           "exposure_c": -n if sign > 0 else n, "exposure_note": "bought protection = short credit (negative); sold protection = long credit",
           "model": _model_view(services, model), "model_spread_bp": str(deal["model_bp"]), "raw_bp": _js(model.get("raw_bp")),
           "floor_bp": _js(model.get("floor_bp")), "floor_as_of": fl.get("as_of"), "bucket": model.get("bucket"),
           "bucket_label": model.get("bucket_label") or f"{model.get('bucket')}-like (model spread bucket - not a credit rating)",
           "quality": model.get("quality") or "computed",
           "level": _level_view(deal["level"], deal["model_bp"], deal["mid"]), "basis_bp": str(deal["basis"].quantize(BP_Q)),
           "mid_spread_bp": str(deal["mid"]), "mid_bucket": deal["mid_bucket"], "tier": deal["tier"], "tier_words": TIER_WORDS.get(deal["tier"]),
           "half_spread_bp": str(mine["half_spread_bp"]), "otc_spread_mult": str(deal["mult"]), "half_spread_eff_bp": str(deal["hs"]),
           "deal_spread_bp": str(deal["deal_bp"]), "firm": _js(deal["firm"]) or None,
           "spread_rule": (f"{level_words(deal['level'], deal['model_bp'], deal['mid'])}; dealt {_bp_words(deal['deal_bp'])} = mid"
                           f" {'+' if sign > 0 else '-'} half-spread {mine['half_spread_bp']} bp x {deal['mult']} ({TIER_WORDS.get(deal['tier'])} tier,"
                           " against you)" + (" - as quoted (firm)" if deal["firm"].get("dealt_spread_bp") not in (None, "") else "")),
           "coupon_bp": str(deal["coupon_bp"]), "coupon_rule": deal["coupon_rule"], "coupon_default_bp": str(deal["coupon_default"]),
           "coupon_pct": _pct_words(Decimal(deal["coupon_bp"]) * BP), "recovery": str(RECOVERY), "recovery_pct": _pct_words(RECOVERY, "1"),
           "recovery_note": "40 % - the senior unsecured market convention",
           "hazard_mid": str(deal["pmid"]["lambda"].quantize(RPV_Q)), "hazard_deal": str(deal["pdeal"]["lambda"].quantize(RPV_Q)),
           "rpv01_mid": str(deal["pmid"]["rpv01"].quantize(RPV_Q)), "rpv01_deal": str(deal["pdeal"]["rpv01"].quantize(RPV_Q)),
           "survival_to_maturity": str(_cdx.survival(deal["pmid"]["lambda"], Decimal((deal["maturity"] - deal["trade_date"]).days) / D365).quantize(PX4)),
           "upfront_c": deal["pays_c"], "upfront_buyer_c": buyer_pays, "upfront_pct": str(up_pct.quantize(PX4)), "price": str((PCT - up_pct).quantize(PX4)),
           "mid_upfront_pct": str(mid_pct.quantize(PX4)), "mid_price": str((PCT - mid_pct).quantize(PX4)),
           "upfront_rule": (f"upfront = (dealt spread {_bp_words(deal['deal_bp'])} - coupon {deal['coupon_bp']} bp) x RPV01"
                            f" {deal['pdeal']['rpv01'].quantize(PX4)} x notional = {up_pct.quantize(PX4)} % = USD {fmt_money(abs(buyer_pays))}"
                            f" {'paid' if buyer_pays > 0 else 'received'} by the protection buyer; price {(PCT - up_pct).quantize(PX4)} (= 100 - upfront %)"),
           "upfront_direction": f"you {you} USD {fmt_money(abs(deal['pays_c']))}",
           "accrued_rebate": {"days": deal["rebate_days"], "amount_c": deal["rebate_c"], "from": deal["accrual_start"].isoformat(),
                              "through": deal["trade_date"].isoformat(), "direction": reb_dir,
                              "rule": (f"{fmt_money(n)} x {deal['coupon_bp']} bp x {deal['rebate_days']} days ({deal['accrual_start'].isoformat()} to the trade"
                                       f" date inclusive) / 360 = {fmt_money(deal['rebate_c'])}: the full first coupon convention")},
           "initial_payment": {"amount_c": net, "direction": "received" if net > 0 else ("paid" if net < 0 else "none"), "settle_date": deal["settle_date"].isoformat(),
                               "rule": (f"upfront and accrued rebate net, settled T+{UPFRONT_SETTLE_BD} business days on {deal['settle_date'].isoformat()}"
                                        " (the bilateral standard; the prime broker is the counterparty of record)")},
           "coupon": {"first_date": deal["first"][2].isoformat(), "first_amount_c": _cdx.coupon_amount_c(n, Decimal(deal["coupon_bp"]), deal["first"][0], deal["first"][1]),
                      "first_rule": "the FULL first coupon (from the accrual start); the rebate above refunds the part before the trade",
                      "basis": "ACT/360", "dates": "20 Mar / Jun / Sep / Dec, next business day", "count": len(deal["periods"])},
           "schedule": deal["schedule"], "mark0_c": deal["mark0_c"], "value_at_mid_c": deal["mark0_c"], "cost_of_dealing_c": deal["mark0_c"] - deal["deal_value_c"],
           "value_at_deal_c": deal["deal_value_c"], "cs01_c": deal["cs01_c"], "dv01_c": deal["dv01_c"],
           "cs01_note": "PV change for +1 bp of the single-name spread (a protection buyer gains when spreads widen)",
           "dv01_note": "PV change for a +1 bp parallel shift of the USD zero curve",
           "jtd_c": deal["jtd_c"], "protection_c": deal["protection_c"],
           "jtd_rule": (f"jump to default = {'+' if sign > 0 else '-'}(1 - 40 %) x {fmt_money(n)} = {'+' if sign > 0 else '-'}{fmt_money(deal['protection_c'])}"
                        f" less the contract's value now {fmt_money(deal['mark0_c'])} = {fmt_money(deal['jtd_c'])}"),
           "im_c": deal["im_c"], "im_pct": str(deal["im_pct"]),
           "im_rule": (f"IM = {_pct_words(deal['im_pct'])} of the notional = {fmt_money(deal['im_c'])} ({TIER_WORDS.get(deal['tier'])} tier,"
                       f" {mine['source']})"),
           "margin_mode": deal["margin_mode"], "fees": "none beyond the dealer's half-spread (disclosed)",
           "inputs": dict(_js(deal["curve"]), valuation_date=deal["trade_date"].isoformat(), rows_mid=deal["pmid"]["rows"], pricing=PRICING),
           "pricing": PRICING, "model_version": MODEL_VERSION, "blockers": list(deal["blockers"]), "bookable": not deal["blockers"],
           "conventions": list(CONVENTIONS), "underlying_id": deal["underlying_id"],
           "assumed": ["the spread is a MODEL (not validated against traded levels)", "the tier IM % and dealer half-spread", "recovery 40 %",
                       "the senior unsecured level (no reference-obligation ISIN)", "NY business days = the NYSE calendar", "initial payment T+3"]}
    return out


def _funding(services, conn, deal: dict, snap=None) -> dict:
    """The preview's funding block: the IM now and, when the initial payment is a payable, its amount - checked together (PO-07)."""
    aid = deal["acct"]["account_id"]
    im = int(deal["im_c"])
    pay = max(0, -int(deal["cash_in_c"]))
    what = "IM + initial payment" if (im and pay) else ("initial payment" if pay else "IM")
    f = swaps.funding_preview(services, conn, aid, "USD", im + pay, snap, what=what)
    f["im_c"], f["initial_payment_c"] = im, pay
    if pay:
        f["payment_note"] = f"the initial payment USD {fmt_money(pay)} is paid on {deal['settle_date'].isoformat()} (checked now together with the IM)"
    return f


def preview(services, account_id: str, kind: str, terms: dict, conn=None, now: Optional[int] = None) -> dict:
    """C21 ``otc.preview`` for a single-name CDS (swaps.preview ran the gate): the MODEL with its inputs, the dealing level and the mid, the
    dealer's spread, the upfront in USD and %, the rebate, the initial payment, the coupon schedule, CS01, DV01, JTD, IM and the funding.  Pure
    (no writes).  A missing dealing level keeps every number with ``bookable`` false and the blocker DEALING_LEVEL_UNCONFIRMED first."""
    k = prime.require_otc_kind(kind)
    if k != KIND:
        raise RejectError("OTC_KIND", f"{k} is not a single-name CDS")
    with _marks.shared_reader(services.db, conn) as c:
        acct = swaps._pb_account(c, account_id)
        t = parse_terms(terms)
        ts = int(now if now is not None else services.clock.epoch())
        deal = _deal(services, c, acct, t, ts)
        out = _preview_dict(services, c, deal, ts)
        out["funding"] = _funding(services, c, deal)
        if not out["funding"].get("ok", True):
            out["blockers"].append(_blocker("INSUFFICIENT_FUNDS", out["funding"]["note"]))
            out["bookable"] = False
        try:
            out["eligibility"] = _eligibility(services, split_account_id(account_id)[0]).to_dict()
        except Exception:   # noqa: BLE001 - informational; the gate already passed
            out["eligibility"] = None
        try:
            out["relationship"] = prime.relationship(c, account_id).get("agreements_line")
        except Exception:   # noqa: BLE001
            out["relationship"] = None
        out["terms"] = {"cik": deal["cik"], "ticker": deal["entity"].get("ticker"), "tenor_years": t["tenor_years"], "side": t["side"],
                        "notional_c": t["notional_c"], "coupon_bp": t["coupon_bp"]}
        out["as_of"] = ts
        return out


# ---- booking ----------------------------------------------------------------------------------------------------------------------------
def _insert_instrument(services, ctx: TxContext, cid: str, name: str, maturity: date, underlying_id, spec: dict) -> None:
    master = services.instruments
    insert = getattr(master, "insert", None) or getattr(master, "_insert")
    insert(ctx.conn, ctx.ts, instrument_id=cid, kind="SWAP", symbol=cid, name=name, currency="USD", multiplier="1", tick="0.01", lot=1, price_scale="1",
           session_group=swaps.SESSION_GROUP, settle_days=0, account_kinds=["PB"], underlying_id=underlying_id, expiry=maturity.isoformat(), spec=spec,
           support="COMPUTED", status="ACTIVE")
    master.refresh(cid, ctx.conn)


def book(ctx: TxContext, payload: dict) -> dict:
    """C21 ``otc.book`` for a single-name CDS (swaps.h_book checked the kind, the PB account and the eligibility first): re-prices inside the
    transaction (an accepted quote's four firm terms from ``ctx.rfq_firm``), re-checks the dealing level (409 DEALING_LEVEL_UNCONFIRMED: a
    level cleared between the quote and the accept), refuses a deal it cannot fund, then posts plan 2.3."""
    services = ctx.services
    conn = ctx.conn
    acct = swaps._pb_account(conn, str(payload.get("account_id") or ""))
    t = parse_terms(payload.get("terms") or {})
    firm = getattr(ctx, "rfq_firm", None) or {}
    deal = _deal(services, conn, acct, t, ctx.ts, firm=firm, marks=ctx.marks)
    for b in deal["blockers"]:
        raise ConflictError(b["code"], b["message"], dict(b.get("details") or {}, blockers=deal["blockers"]))
    return _book(ctx, services, acct, t, deal)


def _book(ctx: TxContext, services, acct: dict, t: dict, deal: dict) -> dict:
    conn = ctx.conn
    aid = acct["account_id"]
    fund = _funding(services, conn, deal, ctx.marks)
    if not fund.get("ok", True):
        raise ConflictError("INSUFFICIENT_FUNDS", fund["note"], {"funding": fund})
    ent, sign, n = deal["entity"], deal["sign"], deal["notional_c"]
    tenor, ticker = deal["tenor_years"], ent.get("ticker")
    broker = prime.broker_of(acct)
    cid = next_id(conn, "cds")
    side_text = _side_text(ticker, tenor, deal["side"])
    entries: List[int] = []
    funding = None
    im_c = int(deal["im_c"])
    model = deal["model"]
    if im_c > 0:
        idem = f"im:{cid}:open"
        funding = swaps.fund_commitment(ctx, services, aid, "USD", im_c, "otc", idem)
        memo = (f"OTC IM {cid}: {side_text} USD {fmt_money(n)} with {broker}; IM = {_pct_words(deal['im_pct'])} x notional = {fmt_money(im_c)}"
                f" ({TIER_WORDS.get(deal['tier'])} tier, {deal['terms_used']['source']})")
        entries.append(ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", "USD", im_c, instrument_id=cid, memo="OTC initial margin"),
                                                       cr(aid, "1010", "USD", im_c, memo="IM paid")], idem, ref_type="otc", ref_id=cid)))
    pays, reb, cash_in = int(deal["pays_c"]), int(deal["rebate_c"]), int(deal["cash_in_c"])
    lines = []
    if pays > 0:                                     # C11 / PORTFOLIO-08: the upfront is the contract's cost basis, released when the contract ends
        lines.append(dr(aid, "1335", "USD", pays, instrument_id=cid, memo="CDS upfront paid (contract cost)"))
    elif pays < 0:
        lines.append(cr(aid, "2345", "USD", -pays, instrument_id=cid, memo="CDS upfront received (contract cost)"))
    if reb > 0:
        if sign > 0:
            lines.append(cr(aid, "2340", "USD", reb, instrument_id=cid, memo="accrued premium before the trade (part of the full first coupon)"))
        else:
            lines.append(dr(aid, "1225", "USD", reb, instrument_id=cid, memo="accrued premium before the trade (part of the full first coupon)"))
    settle_id = None
    sd = deal["settle_date"].isoformat()
    if cash_in > 0:
        lines.append(dr(aid, "1200", "USD", cash_in, memo=f"CDS initial payment receivable {sd}"))
    elif cash_in < 0:
        lines.append(cr(aid, "2100", "USD", -cash_in, memo=f"CDS initial payment payable {sd}"))
    if lines:
        memo = (f"CDS initial payment {cid}: {side_text} USD {fmt_money(n)} ({ent.get('name')}); upfront {(deal['pdeal']['upfront'] * PCT).quantize(PX4)} % ="
                f" {fmt_money(abs(pays))} {'paid' if pays > 0 else 'received'} (dealt {deal['deal_bp']} bp vs coupon {deal['coupon_bp']} bp, RPV01"
                f" {deal['pdeal']['rpv01'].quantize(PX4)}); accrued rebate {deal['rebate_days']} days = {fmt_money(reb)} {'received' if sign > 0 else 'paid'};"
                f" net {fmt_money(abs(cash_in))} {'receivable' if cash_in > 0 else 'payable'} {sd} (T+{UPFRONT_SETTLE_BD}); {level_words(deal['level'], deal['model_bp'], deal['mid'])};"
                " the upfront is the contract's cost, released to the swap result when the contract ends (CDS_DEAL)")
        entries.append(ctx.post(Entry("CDS_UPFRONT", memo, lines, f"otc:{cid}:upfront", ref_type="otc", ref_id=cid)))
    if cash_in:
        settle_id = next_id(conn, "settlement")
        if swaps.has_columns(conn, "settlements", "ref_type", "ref_id"):         # C10 / C15: the settlement names its contract
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status, ref_type, ref_id)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'PENDING','otc',?)", (settle_id, None, None, aid, "UPFRONT", "USD", cash_in, "1200" if cash_in > 0 else "2100", sd, cid))
        else:
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                         (settle_id, None, None, aid, "UPFRONT", "USD", cash_in, "1200" if cash_in > 0 else "2100", sd))
    periods = deal["periods"]
    firm = deal["firm"] or {}
    quoted = None
    if firm.get("dealt_spread_bp") not in (None, ""):
        quoted = {"rfq_id": firm.get("rfq_id"), "dealt_spread_bp": str(firm.get("dealt_spread_bp")), "coupon_bp": None if firm.get("coupon_bp") is None else str(firm.get("coupon_bp")),
                  "maturity_date": firm.get("maturity_date"), "tier": firm.get("tier")}
    level = deal["level"] or {}
    stored = {"kind": KIND, "cik": deal["cik"], "ticker": ticker, "entity_name": ent.get("name"), "underlying_id": deal["underlying_id"],
              "seniority": SENIORITY, "doc_clause": DOC_CLAUSE,
              "tenor_years": tenor, "side": deal["side"], "receiver": sign < 0, "notional_c": n, "coupon_bp": str(deal["coupon_bp"]), "recovery": str(RECOVERY),
              "trade_date": deal["trade_date"].isoformat(), "step_in_date": deal["step_in"].isoformat(), "accrual_start": deal["accrual_start"].isoformat(),
              "maturity_date": deal["maturity"].isoformat(), "roll_date": deal["roll"]["roll_date"].isoformat(), "fixed_basis": "ACT/360",
              "fixed_periods": [[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in periods],
              "model": _model_summary(model), "level_kind": level.get("level_kind"), "basis_bp": str(deal["basis"].quantize(BP_Q)),
              "mid_spread_bp": str(deal["mid"]), "tier": deal["tier"], "coupon_rule": deal["coupon_rule"], "quoted": quoted,
              "half_spread_bp": str(deal["terms_used"]["half_spread_bp"]), "otc_spread_mult": str(deal["mult"]), "deal_spread_bp": str(deal["deal_bp"]),
              "upfront_c": int(deal["pays_c"]), "upfront_buyer_c": int(deal["upfront_buyer_c"]),
              "upfront_pct": str((deal["pdeal"]["upfront"] * PCT).quantize(PX4)), "price": str((PCT - deal["pdeal"]["upfront"] * PCT).quantize(PX4)),
              "upfront_gl": "1335" if int(deal["pays_c"]) > 0 else "2345",
              "rebate_days": deal["rebate_days"], "rebate_c": reb, "initial_payment_c": cash_in, "settle_date": sd, "settle_id": settle_id,
              "im_pct": str(deal["im_pct"]), "mark0_c": int(deal["mark0_c"]), "cs01_0_c": int(deal["cs01_c"]), "jtd_0_c": int(deal["jtd_c"]),
              "rpv01_0": str(deal["pdeal"]["rpv01"].quantize(RPV_Q)), "margin_mode": deal["margin_mode"], "dealt_ts": ctx.ts, "model_version": MODEL_VERSION}
    name = f"Single-name CDS: {side_text} USD {fmt_money(n)} to {deal['maturity'].isoformat()} ({ent.get('name')}; {broker})"
    _insert_instrument(services, ctx, cid, name, deal["maturity"], deal["underlying_id"],
                       {"contract_id": cid, "otc_kind": KIND, "broker": broker, "cik": deal["cik"], "ticker": ticker, "entity_name": ent.get("name"),
                        "tenor_years": tenor, "side": deal["side"], "receiver": sign < 0, "maturity_date": deal["maturity"].isoformat(),
                        "coupon_bp": str(deal["coupon_bp"])})
    inputs = dict(_js(deal["curve"]), as_of=ctx.ts, quality="computed", note="the mid model value at the deal", valuation_date=deal["trade_date"].isoformat(),
                  underlying=_js(model.get("underlying") or {}), proxy=_js(model.get("proxy") or {}), model=LABEL, cds_model=_model_summary(model),
                  mid_spread_bp=str(deal["mid"]), model_spread_bp=str(deal["model_bp"]), basis_bp=str(deal["basis"].quantize(BP_Q)),
                  rpv01=str(deal["pmid"]["rpv01"].quantize(RPV_Q)), pricing=PRICING)
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c,"
                 " status, last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',NULL,NULL,?,?,0,?,NULL)",
                 (cid, ctx.command_id, aid, broker, KIND, dumps(stored), n, "USD", deal["trade_date"].isoformat(), deal["maturity"].isoformat(), deal["margin_mode"],
                  im_c, int(deal["mark0_c"]), ctx.ts, dumps(inputs)))
    # the premium leg's accrual starts at the accrual start with the rebate as its cumulative: the period sums to the full first coupon
    kind = "swappay" if sign > 0 else "swaprcv"
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,360,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate,"
                 " day_basis=excluded.day_basis, accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (kind, cid, deal["accrual_start"].isoformat(), n, str(qprice(Decimal(deal["coupon_bp"]) * BP)), deal["trade_date"].isoformat(), reb))
    # the other leg kind has nothing to accrue: marked accrued through the last night so ACCRUE_DAILY never asks for it
    last_night = (periods[-1][1] - timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,0,'0',360,?,0)"
                 " ON CONFLICT(kind, ref_id) DO NOTHING", ("swaprcv" if sign > 0 else "swappay", cid, last_night, last_night))
    ctx.touched_instruments.add((aid, cid))
    ctx.touched_accounts.add(aid)
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind=KIND, broker=broker, side=deal["side"], side_text=side_text,
              notional_c=n, cik=deal["cik"], ticker=ticker, entity_name=ent.get("name"), tenor_years=tenor, coupon_bp=str(deal["coupon_bp"]),
              deal_spread_bp=str(deal["deal_bp"]), mid_spread_bp=str(deal["mid"]), level_kind=level.get("level_kind"), tier=deal["tier"],
              upfront_c=int(deal["pays_c"]), rebate_c=reb, initial_payment_c=cash_in, settle_id=settle_id, mark0_c=int(deal["mark0_c"]), im_c=im_c,
              margin_mode=deal["margin_mode"], label=LABEL)
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    pv = _preview_dict(services, conn, deal, ctx.ts)
    return {"contract": view, "contract_id": cid, "im_transfer": {"amount_c": im_c, "funding": funding.to_dict() if hasattr(funding, "to_dict") else funding},
            "initial_payment": {"amount_c": cash_in, "direction": "received" if cash_in > 0 else ("paid" if cash_in < 0 else "none"), "settle_date": sd,
                                "settle_id": settle_id, "upfront_c": int(deal["pays_c"]), "rebate_c": reb},
            "entries": entries, "preview": pv}


# ---- valuation: live and as of a VM date --------------------------------------------------------------------------------------------------
def _stale(row: dict, now: int, inputs: dict, note: str) -> MarkView:
    stored = int(row.get("mark_c") or 0)
    return MarkView(row["contract_id"], from_c(stored), "stale", "single-name CDS model (stored mark)", "swap_model", row.get("mark_ts"), now, "model",
                    dict(inputs, qualities=["stale"], note=note), False, note)


def _state(services, row: dict, now: int, valuation_date=None, conn=None, marks=None, basis_override=None) -> dict:
    """The valuation state of an OPEN contract (plan B1 task 6): ``{ok: True, model, basis, mid, v, periods, dfs, df, curve, side, n, coupon}``
    or ``{ok: False, code, reason, vm_outcome?, ...}``.  Live (``valuation_date`` None): the model now; a VM date d: the model OF d (every
    input of R, nothing fetched) and the curve of R.  ``basis_override``: a level being tried (the basis-effect route)."""
    terms = _terms(row)
    cal = services.cal
    today = ny_of(int(now)).date()
    d = None if valuation_date is None else _date(valuation_date)
    maturity = _date(terms.get("maturity_date") or row["maturity_date"])
    cik = terms.get("cik")
    if cik in (None, ""):
        return {"ok": False, "code": "TERMS", "reason": f"{row['contract_id']} carries no reference entity"}
    model = model_now(services, int(cik), maturity, conn=conn, now=int(now), valuation_date=d, marks=marks)
    if not model.get("ok"):
        out = dict(_js({k: v for k, v in model.items() if k not in ("ok",)}), ok=False)
        out["code"] = model.get("code") or "MODEL_UNAVAILABLE"
        out["reason"] = model.get("reason") or out["code"]
        outcome = model.get("vm_outcome") or (out["code"] if out["code"] in ("CLOSE_PENDING", "CAUGHT_UP") else None)
        if d is not None and outcome in ("CLOSE_PENDING", "CAUGHT_UP"):
            r_date = _date(model.get("close_date")) if model.get("close_date") else cal.roll_back(d)
            out.update(vm_outcome=outcome, note=out["reason"], close_date=r_date.isoformat(),
                       quiet=bool(model["quiet"]) if model.get("quiet") is not None else not cal.is_business_day(d),
                       quiet_until=model.get("quiet_until") if model.get("quiet_until") is not None else epoch_of(cal.next_business_day(r_date), "12:00"),
                       waits_for="that day's inputs: its close, volatility, AAA OAS and curve (never another day's value)")
        return out
    with _marks.shared_reader(services.db, conn) as c:
        level = level_of(c, row["account_id"], cik)
    basis = _basis(level) if basis_override is None else Decimal(str(basis_override))
    model_bp = Decimal(str(model["spread_bp"]))
    mid = (model_bp + basis).quantize(BP_Q)
    if d is not None:
        v = _date(model.get("valuation_date")) if model.get("valuation_date") else cal.roll_back(d)
    else:
        v = today
    periods = [(_date(a), _date(b), _date(p)) for a, b, p in (terms.get("fixed_periods") or [])]
    side, n = terms.get("side") or "BUY_PROTECTION", int(row["notional_c"])
    coupon = Decimal(str(terms.get("coupon_bp") or "100"))
    out = {"ok": True, "model": model, "level": level, "basis": basis, "model_bp": model_bp, "mid": mid, "v": v, "periods": periods, "side": side, "n": n,
           "coupon": coupon, "terms": terms}
    if not [p for p in periods if p[1] > v + timedelta(days=1)]:
        out.update(df=None, dfs=None, curve={}, at_maturity=True)
        return out
    try:
        dfs, curve_inputs = _curve(services, periods, v, on=v if d is not None else None)
    except ConflictError as e:
        return {"ok": False, "code": e.code, "reason": e.message}
    out.update(dfs=dfs, df=_cdx.df_function(dfs), curve=curve_inputs, at_maturity=False)
    return out


def _value_at(st: dict, spread_bp) -> Tuple[Optional[int], Optional[dict]]:
    """The contract's value to its holder at a spread (bp) on the state's curve and date; 0 at maturity; None for a spread <= 0."""
    if st.get("at_maturity"):
        return 0, None
    s = Decimal(str(spread_bp)).quantize(BP_Q)
    if s <= 0:
        return None, None
    val, pr = _cdx.value_c(st["side"], st["n"], s, st["coupon"], RECOVERY, st["periods"], st["v"], st["df"])
    return int(val), pr


def _valuation(services, row: dict, now: int, valuation_date=None, shift_spread_bp=None, conn=None, marks=None):
    """``(value_c, inputs)`` of an OPEN contract, or ``(None, {code, reason, vm_outcome?, ...})`` when an input is missing (plan B1 task 6):
    the model of the contract's maturity at the valuation date plus the account basis; the inputs named ``underlying`` / ``proxy`` /
    ``curve_as_of`` for ``swaps._input_stamp`` / ``_observed_after``; ``par_rate`` / ``dealt_rate`` / ``annuity`` for the VM memo."""
    st = _state(services, row, now, valuation_date, conn=conn, marks=marks)
    if not st.get("ok"):
        return None, st
    terms = st["terms"]
    model = st["model"]
    mid = st["mid"]
    if shift_spread_bp not in (None, 0):
        mid = (mid + Decimal(str(shift_spread_bp))).quantize(BP_Q)
    if mid <= 0:
        return None, {"code": "MID_NOT_POSITIVE", "reason": f"the mid {_bp_words(mid)} (MODEL {_bp_words(st['model_bp'])} + basis {_signed_bp(st['basis'])} bp) is not positive"}
    if st.get("at_maturity"):
        return 0, {"as_of": int(now), "valuation_date": st["v"].isoformat(), "note": "no remaining coupon period (at maturity)", "quality": "computed"}
    val, pr = _value_at(st, mid)
    fl = model.get("floor") or {}
    inputs = dict(_js(st["curve"]), as_of=int(now), valuation_date=st["v"].isoformat(), contract_id=row["contract_id"], quality="computed", model=LABEL,
                  underlying=_js(model.get("underlying") or {}), proxy=_js(model.get("proxy") or {"as_of": fl.get("as_of"), "series_id": fl.get("series_id")}),
                  cds_model=_model_summary(model), model_spread_bp=str(st["model_bp"]), basis_bp=str(st["basis"].quantize(BP_Q)), level_kind=(st["level"] or {}).get("level_kind"),
                  mid_spread_bp=str(mid), floor_bp=_js(model.get("floor_bp")), raw_bp=_js(model.get("raw_bp")), bucket=model.get("bucket"),
                  coupon_bp=terms.get("coupon_bp"), recovery=str(RECOVERY), hazard=str(pr["lambda"].quantize(RPV_Q)), rpv01=str(pr["rpv01"].quantize(RPV_Q)),
                  upfront_pct=str((pr["upfront"] * PCT).quantize(PX4)), price=str(pr["price"].quantize(PX4)), side=st["side"],
                  # read by swaps._vm_core's memo (the non-IRS kinds: ``formula``) and named like the IRS / CDX inputs
                  formula=(f"single-name CDS {terms.get('ticker')} mid {_bp_words(mid)} ({LABEL} {_bp_words(st['model_bp'])} + basis {_signed_bp(st['basis'])} bp)"
                           f" vs dealt {_bp_words(terms.get('deal_spread_bp'))}, coupon {terms.get('coupon_bp')} bp, RPV01 {pr['rpv01'].quantize(PX4)}"),
                  par_rate=f"CDS mid {mid} bp", dealt_rate=f"{terms.get('deal_spread_bp')} bp dealt, coupon {terms.get('coupon_bp')} bp",
                  annuity=f"RPV01 {pr['rpv01'].quantize(PX4)}", pricing=PRICING,
                  note=f"{LABEL_WORDS}: {level_words(st['level'], st['model_bp'], mid)}")
    return int(val), inputs


def mark(services, contract, at: Optional[int] = None, valuation_date=None, snapshot=None):
    """C21 mark hook: ``(cents, inputs)``; live without a usable model / curve -> the stored mark as a stale ``MarkView``; with
    ``valuation_date`` (SWAP_VM) -> ``(None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP, note, quiet, quiet_until})`` when an input of that date is
    not out (or never will be), or ``(None, {note})`` (NO_MARK, retried) for any other missing input."""
    row = swaps._contract_dict(services, contract)
    if row is None:
        return None
    now = int(at if at is not None else services.clock.epoch())
    if row.get("status") != "OPEN":
        return 0, {"as_of": now, "note": f"{row.get('status')}: no remaining cash flows", "quality": "computed"}
    val, inputs = _valuation(services, row, now, valuation_date, marks=snapshot)
    if val is None:
        if valuation_date is not None:
            return None, dict(inputs, note=inputs.get("note") or inputs.get("reason"))
        return _stale(row, now, {k: v for k, v in inputs.items() if k in ("code", "reason", "retry_ms")},
                      f"no current single-name CDS value: {inputs.get('reason')}; the last computed mark, labelled stale")
    return int(val), inputs


def cs01_c(services, contract, now: Optional[int] = None) -> Optional[int]:
    """The contract's CS01 now: the PV change for +1 bp of its spread (signed for the holder); None without a usable model / curve."""
    row = swaps._contract_dict(services, contract)
    if row is None or row.get("status") != "OPEN":
        return None if row is None else 0
    ts = int(now if now is not None else services.clock.epoch())
    st = _state(services, row, ts)
    if not st.get("ok"):
        return None
    base, _ = _value_at(st, st["mid"])
    up, _ = _value_at(st, st["mid"] + ONE)
    return None if base is None or up is None else int(up) - int(base)


def dv01_c(services, contract) -> int:
    """C21 ``dv01_c``: the RATES DV01 - the PV change for a +1 bp parallel shift of the USD zero curve (raises ConflictError when the model or
    the curve is missing; 0 when not OPEN)."""
    row = swaps._contract_dict(services, contract)
    if row is None:
        raise RejectError("NOT_FOUND", "unknown contract")
    if row.get("status") != "OPEN":
        return 0
    now = int(services.clock.epoch())
    st = _state(services, row, now)
    if not st.get("ok"):
        raise ConflictError(str(st.get("code") or "MODEL_UNAVAILABLE"), str(st.get("reason") or "no single-name CDS value now"))
    if st.get("at_maturity"):
        return 0
    base, _ = _value_at(st, st["mid"])
    up, _ = _cdx.value_c(st["side"], st["n"], st["mid"], st["coupon"], RECOVERY, st["periods"], st["v"], _cdx.df_function(st["dfs"], 1))
    return int(up) - int(base)


# ---- the premium leg (ACCRUE_DAILY through swaps.accrue_leg) ---------------------------------------------------------------------------------
def leg_specs(services, row: dict) -> Dict[str, swaps.LegSpec]:
    """C21 ``leg_specs``: the premium leg - ``swappay`` for a buyer, ``swaprcv`` for a seller - FIXED at the coupon, ACT/360, the coupon periods."""
    terms = _terms(row)
    buyer = (terms.get("side") or "BUY_PROTECTION") == "BUY_PROTECTION"
    kind = "swappay" if buyer else "swaprcv"
    c = qprice(Decimal(str(terms.get("coupon_bp") or "100")) * BP)
    periods = tuple((_date(a), _date(b), _date(p)) for a, b, p in (terms.get("fixed_periods") or []))
    return {kind: swaps.LegSpec(kind, "premium", row["currency"], int(row["notional_c"]), "ACT/360", periods, "FIXED", c)}


def _catch_up(ctx: TxContext, services, row: dict, through: date) -> List[dict]:
    """Post the premium nights owed through ``through`` (inclusive) before a termination or a credit event settles them; 409 with the reason
    otherwise."""
    conn = ctx.conn
    cid = row["contract_id"]
    posted: List[dict] = []
    for kind, spec in leg_specs(services, row).items():
        st = conn.execute("SELECT accrued_through FROM accrual_state WHERE kind=? AND ref_id=?", (kind, cid)).fetchone()
        night = _date(row["start_date"]) if st is None or not st["accrued_through"] else _date(st["accrued_through"]) + timedelta(days=1)
        last_night = spec.periods[-1][1] - timedelta(days=1) if spec.periods else through
        while night <= min(through, last_night):
            try:
                res = swaps.accrue_night(ctx, services, kind, cid, night.isoformat())
            except RejectError as e:
                raise ConflictError(e.code, f"{cid}: the premium is settled through {through.isoformat()}, but the night of {night.isoformat()}"
                                    f" cannot accrue: {e.message}", e.details) from e
            if res.get("entry_id") is not None:
                posted.append({"kind": kind, "leg": "premium", "night": night.isoformat(), "amount_c": int(res.get("amount_c") or 0), "entry_id": res["entry_id"]})
            night += timedelta(days=1)
    return posted


# ---- termination -----------------------------------------------------------------------------------------------------------------------
def terminate(ctx: TxContext, payload: dict) -> dict:
    """C21 ``otc.terminate`` for a single-name CDS (owner): the premium accrued through today first, then the close-out at the mid -/+ the
    half-spread of the mid's tier against you (a buyer sells protection back at mid - hs, a seller buys it back at mid + hs).  No dealing
    level is needed; inputs observed now are (409 otherwise)."""
    services = ctx.services
    conn = ctx.conn
    cid = str(payload.get("contract_id") or "")
    row = swaps._open_contract(conn, cid)
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}; only an OPEN contract can be terminated", {"contract_id": cid})
    acct = account_row(conn, row["account_id"])
    if acct is None or acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {row['account_id']} is not open")
    acct = dict(acct)
    terms = _terms(row)
    today = ny_of(ctx.ts).date()
    st = _state(services, row, ctx.ts, conn=conn, marks=ctx.marks)
    if not st.get("ok"):
        raise ConflictError(str(st.get("code") or "MODEL_UNAVAILABLE"), f"{cid} cannot be terminated now: {st.get('reason')}", {"contract_id": cid})
    mid = st["mid"]
    if mid <= 0:
        raise ConflictError("MID_NOT_POSITIVE", f"the mid {_bp_words(mid)} is not positive: set a new negotiated mid first", {"contract_id": cid})
    bucket = bucket_of_mid(services, mid, st["model"])
    tier = TIER_OF_BUCKET.get(bucket) if bucket else None
    if tier is None:
        raise ConflictError("FLOOR_UNAVAILABLE", "no FRED rating ladder of the model's date: the dealer half-spread cannot be set", {"contract_id": cid})
    mine = tiers_for(conn, row["account_id"], prime.broker_of(acct))[tier]
    mult = qprice(Decimal(prime.read_schedule(conn, acct).otc_spread_mult))
    hs = (mine["half_spread_bp"] * mult).quantize(BP_Q)
    side = st["side"]
    term_bp = (mid - hs) if side == "BUY_PROTECTION" else (mid + hs)
    if term_bp <= 0:
        raise ConflictError("DEAL_SPREAD", f"the unwind spread {_bp_words(term_bp)} is not positive (mid {_bp_words(mid)} - half-spread {_bp_words(hs)})",
                            {"contract_id": cid})
    if st.get("at_maturity"):
        value, mid_val, pr = 0, 0, None
    else:
        value, pr = _value_at(st, term_bp)
        mid_val, _ = _value_at(st, mid)
    caught = _catch_up(ctx, services, row, today)
    row = swaps._open_contract(conn, cid)
    ref_c = swaps._reference_mark_c(conn, cid)
    inputs = dict(_js(st.get("curve") or {}), cds_model=_model_summary(st["model"]), model_spread_bp=str(st["model_bp"]), basis_bp=str(st["basis"].quantize(BP_Q)),
                  mid_spread_bp=str(mid), tier=tier, half_spread_eff_bp=str(hs), unwind_spread_bp=str(term_bp),
                  rpv01=None if pr is None else str(pr["rpv01"].quantize(RPV_Q)), mid_value_c=mid_val, valuation_date=today.isoformat(), model=LABEL)
    memo = (f"CDS termination {cid} ({terms.get('ticker')} {terms.get('tenor_years')}Y, {terms.get('entity_name')}): value {fmt_money(value)} at the unwind spread"
            f" {term_bp} bp (mid {mid} bp {'-' if side == 'BUY_PROTECTION' else '+'} half-spread {hs} bp, {TIER_WORDS.get(tier)} tier, against you; mid value"
            f" {fmt_money(mid_val)}); {level_words(st['level'], st['model_bp'], mid)}; VM balance {fmt_money(int(row['vm_balance_c']))} unwound; accrued"
            f" premium settled through {today.isoformat()}" + (f" ({len(caught)} night(s) accrued first)" if caught else "") + " (CDS_DEAL)")
    closed = swaps.close_contract(ctx, services, row, "TERMINATED", value, ref_c, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, True, False)
    ctx.event("OTC_TERMINATED", account_id=row["account_id"], instrument_id=cid, ref_type="otc", ref_id=cid, termination_value_c=value, inputs=inputs,
              caught_up=len(caught), kind=KIND, **{k: v for k, v in closed.items() if k not in ("funding", "value_c")})
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    entries = [c["entry_id"] for c in caught] + [e for e in (closed["entry_id"], closed["im_entry_id"]) if e is not None]
    return {"contract": view, "contract_id": cid, "termination_value_c": value, "mid_value_c": mid_val, "cost_of_unwinding_c": mid_val - value,
            "result_c": closed["result_c"], "cost_released_c": closed.get("cost_released_c", 0), "realised_c": closed.get("realised_c"),
            "cash_c": closed["cash_c"], "entries": entries, "im_released_c": closed["im_released_c"], "accrued_receive_c": closed["accrued_receive_c"],
            "accrued_pay_c": closed["accrued_pay_c"], "inputs": inputs, "accrued_through": today.isoformat(), "caught_up": caught, "funding": closed.get("funding"),
            "unwind_spread_bp": str(term_bp), "mid_spread_bp": str(mid), "tier": tier}


# ---- the quarterly coupon (job CDS_COUPON) -------------------------------------------------------------------------------------------
def h_coupon(ctx: TxContext, payload: dict) -> dict:
    """CDS_COUPON for one (contract, payment date): the coupon C x N x days / 360 - buyer Dr 2340 / Cr 1010 (funded on its date with CSA
    netting and the overdraft rule), seller Dr 1010 / Cr 1225 - recorded in ``otc_resets`` (PAY, detail kind "CDS coupon"); accruals not yet
    through the period's last night -> SKIPPED ACCRUALS_PENDING (retried).  Idempotent per date; the last one matures the contract."""
    services = ctx.services
    conn = ctx.conn
    cid, ds = swaps._key(payload, "cds.coupon")
    key = f"{cid}:{ds}"
    idem = f"cdscpn:{key}"
    row = swaps._open_contract(conn, cid)
    aid, ccy = row["account_id"], row["currency"]
    if row["kind"] != KIND:
        raise RejectError("KIND", f"{cid} is a {swaps.KIND_LABELS.get(row['kind'], row['kind'])} contract: cds.coupon is for single-name CDS contracts")
    if row["status"] != "OPEN":
        return swaps._done_noop(ctx, JOB_COUPON, key, "NOT_OPEN", f"{cid} is {row['status']}")
    existing = conn.execute("SELECT detail_json FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='PAY'", (cid, ds)).fetchone()
    if existing is not None:
        swaps._job_done(ctx, JOB_COUPON, key, "DONE", "already paid")
        return {"status": "DONE", "already": True, "posted": False, "key": key}
    d = date.fromisoformat(ds)
    terms = _terms(row)
    specs = leg_specs(services, row)
    kind, spec = next(iter(specs.items()))
    period = next((p for p in spec.periods if p[2] == d), None)
    if period is None:
        raise RejectError("KEY_INVALID", f"{ds} is not a coupon payment date of {cid}")
    if ctx.ts < epoch_of(d, COUPON_ET):
        return swaps._skip(ctx, JOB_COUPON, key, "PAY_NOT_YET", f"{cid}: the coupon of {ds} is paid from {COUPON_ET} ET", aid, cid, quiet=True)
    st = conn.execute("SELECT accrued_through FROM accrual_state WHERE kind=? AND ref_id=?", (kind, cid)).fetchone()
    through = st["accrued_through"] if st is not None else None
    last_night = (period[1] - timedelta(days=1)).isoformat()
    if through is None or through < last_night:
        return swaps._skip(ctx, JOB_COUPON, key, "ACCRUALS_PENDING", f"{cid}: the premium is accrued through {through or 'nothing'}, the period ends {last_night}"
                           " (ACCRUE_DAILY first)", aid, cid, {"accrued_through": through, "needed": last_night})
    amount, rate = swaps.leg_coupon_c(conn, row, spec, period)
    have = balance(conn, aid, spec.gl, ccy, cid)
    if amount is None or have < amount:
        return swaps._skip(ctx, JOB_COUPON, key, "ACCRUALS_PENDING", f"{cid}: premium accrued {fmt_money(have)} in {spec.gl} but the coupon is {fmt_money(amount or 0)}",
                           aid, cid)
    buyer = kind == "swappay"
    funding = None
    if buyer:
        with swaps.asof_context(ctx, d):         # the coupon of d is funded on d (C10 / C20) with CSA netting (C21)
            funding = swaps.fund_csa(ctx, services, aid, ccy, amount, "cdscpn", idem, overdraft=True)
        lines = [dr(aid, "2340", ccy, amount, instrument_id=cid, memo="CDS coupon paid (accrued premium settled)"), cr(aid, "1010", ccy, amount, memo="CDS coupon paid")]
    else:
        lines = [dr(aid, "1010", ccy, amount, memo="CDS coupon received"), cr(aid, "1225", ccy, amount, instrument_id=cid, memo="CDS coupon received (accrued premium settled)")]
    days = (period[1] - period[0]).days
    memo = (f"CDS coupon {cid} {ds} ({terms.get('ticker')} {terms.get('tenor_years')}Y): {fmt_money(int(row['notional_c']))} x {terms.get('coupon_bp')} bp x {days} days"
            f" ({period[0].isoformat()} to {(period[1] - timedelta(days=1)).isoformat()}) / 360 = {fmt_money(amount)} {'paid' if buyer else 'received'} (CDS_COUPON)")
    entry_id = ctx.post(Entry("SWAP_PAY", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds))
    net = -amount if buyer else amount
    detail = {"receive_c": 0 if buyer else amount, "pay_c": amount if buyer else 0, "net_c": net, "period": [period[0].isoformat(), period[1].isoformat()], "days": days,
              "coupon_bp": terms.get("coupon_bp"), "rate": rate, "entry_id": entry_id, "kind": "CDS coupon"}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'PAY',?,?,?)", (cid, ds, net, dumps(detail), entry_id))
    ctx.touched_instruments.add((aid, cid))
    out = {"status": "DONE", "key": key, "amount_c": amount, "net_c": net, "entry_id": entry_id, "posted": True,
           "funding": None if funding is None else (funding.to_dict() if hasattr(funding, "to_dict") else None)}
    if period == spec.periods[-1]:
        row = swaps._open_contract(conn, cid)
        closed = swaps.close_contract(ctx, services, row, "MATURED", 0, swaps._reference_mark_c(conn, cid), f"otc:{cid}:mature", "OTC_MATURE",
                                      f"CDS maturity {cid} {ds}: VM balance {fmt_money(int(row['vm_balance_c']))} unwound, IM {fmt_money(int(row['im_c']))} released"
                                      " (CDS_COUPON)", False, True)
        out["matured"] = closed
        ctx.event("SWAP_MATURED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, **{k: v for k, v in closed.items() if k != "funding"})
    swaps._job_done(ctx, JOB_COUPON, key, "DONE", f"{'paid' if buyer else 'received'} {fmt_money(amount)}" + ("; matured" if "matured" in out else ""))
    swaps._resolve_attention(ctx, ("ACCRUALS_PENDING",), key)
    ctx.event("SWAP_PAY", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, receive_c=detail["receive_c"], pay_c=detail["pay_c"], net_c=net,
              entry_id=entry_id, kind=KIND)
    return out


def coupon_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """``(contract_id, payment date)`` pairs whose CDS_COUPON is due: payment dates on or before today (00:45 ET passed) of OPEN single-name
    CDS contracts without a PAY row and not terminal in ``jobs_done``."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str]] = []
    for row in swaps._open_contracts(conn, (KIND,)):
        cid = row["contract_id"]
        paid = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
        done = swaps._jobs(conn, JOB_COUPON, cid)
        terms = _terms({k: row[k] for k in row.keys()})
        for _s, _e, p in terms.get("fixed_periods") or []:
            pd = _date(p)
            if pd > today or now < epoch_of(pd, COUPON_ET):
                continue
            ds = pd.isoformat()
            if ds not in paid and not swaps._terminal(done.get(f"{cid}:{ds}")):
                out.append((cid, ds))
    return out


# ---- the simulated credit event (plan 2.4) --------------------------------------------------------------------------------------------------
def _parse_recovery(v) -> Decimal:
    if v is None or isinstance(v, (bool, float)) or (isinstance(v, str) and not v.strip()):
        raise RejectError("TERMS", "recovery_pct: the recovery you enter, a percent from 0 to 100 (40 = 40 %)", {"field": "recovery_pct"})
    s = str(v).strip().rstrip("%").strip()
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"recovery_pct: {v!r} is not a number (40 = 40 %)", {"field": "recovery_pct"}) from e
    if not d.is_finite() or d < 0 or d > 100 or d != d.quantize(Decimal("0.0001")):
        raise RejectError("TERMS", f"recovery_pct: a percent from 0 to 100 with at most 4 decimals, got {v!r}", {"field": "recovery_pct"})
    return d


def _ensure_tables(services, conn) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cds_credit_events'").fetchone() is None:
        db = getattr(services, "db", None)
        if db is not None and hasattr(db, "ensure_schema_ext"):
            db.ensure_schema_ext(SOURCE, SCHEMA)


def _posted_after(conn, aid: str, cid: str, leg: str, gl: str, after: date) -> int:
    """The natural-sign sum of this contract's premium accrual lines of nights AFTER ``after`` (``accr:{leg}:{cid}:{night}``): what an event
    entered late reverses, summed from the postings - never re-derived by formula - so GL 2340 / 1225 of the contract reaches exactly 0."""
    total = 0
    prefix = f"accr:{leg}:{cid}:"
    for r in conn.execute("SELECT j.idem_key, l.amount_c FROM journal_lines l JOIN journal_entries j ON j.entry_id=l.entry_id WHERE j.idem_key LIKE ?"
                          " AND l.account_id=? AND l.gl=? AND l.instrument_id=?", (prefix + "%", aid, gl, cid)).fetchall():
        night = str(r["idem_key"])[len(prefix):]
        try:
            nd = date.fromisoformat(night[:10])
        except ValueError:
            continue
        if len(night) == 10 and nd > after:
            total += int(r["amount_c"] or 0)
    return natural(gl, total)


def h_credit_event(ctx: TxContext, payload: dict) -> dict:
    """``cds.credit_event {contract_id, event_type: BANKRUPTCY | FAILURE_TO_PAY, recovery_pct, event_date}`` (plan 2.4): the owner simulates the
    default of the reference entity at the recovery he enters - "simulated credit event - recovery entered by the owner; no ISDA Determinations
    Committee ruling or auction in the simulator".  It never fires by itself.  ONE command: the premium through the event date, the nights after
    it already posted reversed (summed from their postings), the protection N x (1 - recovery) net of the accrued premium settling T+1 business
    day, then the contract closes (VM unwound, cost basis released into 4450, IM released, TERMINATED)."""
    if str(ctx.actor or "") in ("lifecycle", "matcher", "worker"):
        raise swaps.Forbidden("CREDIT_EVENT_OWNER_ONLY", "a credit event is entered by the owner; it never fires by itself", {"actor": ctx.actor})
    services = ctx.services
    conn = ctx.conn
    cid = str(payload.get("contract_id") or "")
    row = swaps._open_contract(conn, cid)
    if row["kind"] != KIND:
        raise RejectError("KIND", f"{cid} is a {swaps.KIND_LABELS.get(row['kind'], row['kind'])}: this credit event applies to a single-name CDS contract",
                          {"contract_id": cid, "kind": row["kind"]})
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}; a credit event applies to an OPEN contract", {"contract_id": cid})
    et = str(payload.get("event_type") or "").strip().upper().replace(" ", "_")
    if et not in EVENT_TYPES:
        raise RejectError("TERMS", "event_type: BANKRUPTCY or FAILURE_TO_PAY (the North American XR14 events; no restructuring)", {"field": "event_type"})
    rec = _parse_recovery(payload.get("recovery_pct"))
    try:
        ed = date.fromisoformat(str(payload.get("event_date") or "").strip()[:10])
    except ValueError as e:
        raise RejectError("TERMS", f"event_date: an ISO date (2026-11-02), got {payload.get('event_date')!r}", {"field": "event_date"}) from e
    today = ny_of(ctx.ts).date()
    if ed > today:
        raise RejectError("TERMS", f"event_date {ed.isoformat()} is in the future: a credit event is entered once it happened", {"field": "event_date"})
    start = _date(row["start_date"])
    if ed < start:
        raise RejectError("TERMS", f"event_date {ed.isoformat()} is before the contract's trade date {start.isoformat()}: this contract did not cover it",
                          {"field": "event_date"})
    terms = _terms(row)
    periods = [(_date(a), _date(b), _date(p)) for a, b, p in (terms.get("fixed_periods") or [])]
    period = next((p for p in periods if p[0] <= ed < p[1]), None)
    if period is None:
        raise RejectError("TERMS", f"event_date {ed.isoformat()} is outside the contract's coupon periods", {"field": "event_date"})
    s, e, pay_date = period
    if conn.execute("SELECT 1 FROM otc_resets WHERE contract_id=? AND kind='PAY' AND event_date=?", (cid, pay_date.isoformat())).fetchone() is not None:
        raise ConflictError("EVENT_PERIOD_SETTLED", f"{cid}: the coupon of the period of {ed.isoformat()} ({s.isoformat()} .. {(e - timedelta(days=1)).isoformat()})"
                            f" was paid on {pay_date.isoformat()}; enter a credit event of the current period", {"contract_id": cid, "event_date": ed.isoformat()})
    _ensure_tables(services, conn)
    side = terms.get("side") or "BUY_PROTECTION"
    buyer = side == "BUY_PROTECTION"
    n = int(row["notional_c"])
    entity = terms.get("entity_name") or terms.get("ticker") or f"CIK {terms.get('cik')}"
    # 1. catch up: the premium through the event date (a late entry's later nights are reversed below)
    caught = _catch_up(ctx, services, row, ed)
    row = swaps._open_contract(conn, cid)
    aid, ccy = row["account_id"], row["currency"]
    leg, acc_gl = ("swappay", "2340") if buyer else ("swaprcv", "1225")
    # 2. the nights after the event date already posted, summed from their postings
    late = _posted_after(conn, aid, cid, leg, acc_gl, ed)
    # 3. the amounts
    have = balance(conn, aid, acc_gl, ccy, cid)
    accrued = have - late
    recovery = rec / PCT
    protection = to_c(from_c(n) * (ONE - recovery))
    net = protection - accrued if buyer else accrued - protection
    sd = services.cal.add_business_days(ed, CREDIT_SETTLE_BD)
    words = EVENT_TYPES[et]
    lines = []
    clear = accrued + late
    if buyer:
        if clear > 0:
            lines.append(dr(aid, "2340", ccy, clear, instrument_id=cid, memo=f"accrued premium settled through the event date {ed.isoformat()}"
                            + (" and the later night(s) reversed" if late else "")))
        elif clear < 0:
            lines.append(cr(aid, "2340", ccy, -clear, instrument_id=cid, memo="accrued premium settled (credit event)"))
        if late > 0:
            lines.append(cr(aid, "4453", ccy, late, instrument_id=cid, memo="premium accrued after the event date reversed"))
        elif late < 0:
            lines.append(dr(aid, "4453", ccy, -late, instrument_id=cid, memo="premium accrued after the event date reversed"))
        if protection:
            lines.append(cr(aid, "4450", ccy, protection, instrument_id=cid, memo=f"protection received: {fmt_money(n)} x (1 - {rec} %) (simulated credit event)"))
    else:
        if clear > 0:
            lines.append(cr(aid, "1225", ccy, clear, instrument_id=cid, memo=f"accrued premium settled through the event date {ed.isoformat()}"
                            + (" and the later night(s) reversed" if late else "")))
        elif clear < 0:
            lines.append(dr(aid, "1225", ccy, -clear, instrument_id=cid, memo="accrued premium settled (credit event)"))
        if late > 0:
            lines.append(dr(aid, "4453", ccy, late, instrument_id=cid, memo="premium accrued after the event date reversed"))
        elif late < 0:
            lines.append(cr(aid, "4453", ccy, -late, instrument_id=cid, memo="premium accrued after the event date reversed"))
        if protection:
            lines.append(dr(aid, "4450", ccy, protection, instrument_id=cid, memo=f"protection paid: {fmt_money(n)} x (1 - {rec} %) (simulated credit event)"))
    if net > 0:
        lines.append(dr(aid, "1200", ccy, net, memo=f"credit event settlement receivable {sd.isoformat()}"))
    elif net < 0:
        lines.append(cr(aid, "2100", ccy, -net, memo=f"credit event settlement payable {sd.isoformat()}"))
    memo = (f"CDS credit event {cid}: {entity} ({terms.get('ticker')}) {words} on {ed.isoformat()}, recovery {rec} % entered by the owner; protection"
            f" {fmt_money(protection)} {'received' if buyer else 'paid'}, accrued premium {fmt_money(accrued)} {'paid' if buyer else 'received'}"
            + (f" ({fmt_money(late)} accrued after the event date reversed)" if late else "")
            + f", net {fmt_money(abs(net))} {'receivable' if net > 0 else 'payable'} {sd.isoformat()}; the contract closes ({CREDIT_EVENT_LABEL})")
    entry_id = ctx.post(Entry("CDS_CREDIT_EVENT", memo, lines, f"otc:{cid}:credit", ref_type="otc", ref_id=cid)) if lines else None
    settle_id = None
    if net:
        settle_id = next_id(conn, "settlement")
        if swaps.has_columns(conn, "settlements", "ref_type", "ref_id"):
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status, ref_type, ref_id)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'PENDING','otc',?)", (settle_id, None, None, aid, "CREDIT EVENT", ccy, net, "1200" if net > 0 else "2100",
                                                                           sd.isoformat(), cid))
        else:
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'PENDING')", (settle_id, None, None, aid, "CREDIT EVENT", ccy, net, "1200" if net > 0 else "2100", sd.isoformat()))
    # 5. close: the VM unwound, the whole cost basis released into 4450, the IM released, TERMINATED, the accrual state deleted, the SWAP row expired
    row = swaps._open_contract(conn, cid)
    close_memo = (f"CDS credit-event close {cid}: {entity} {words} on {ed.isoformat()}; VM balance {fmt_money(int(row['vm_balance_c']))} unwound, the contract"
                  f" closes at 0 (the protection and the accrued premium settled in the credit event entry)")
    closed = swaps.close_contract(ctx, services, row, "TERMINATED", 0, swaps._reference_mark_c(conn, cid), f"otc:{cid}:credit-close", "CDS_CREDIT_CLOSE",
                                  close_memo, False, True)
    # 6. the record
    conn.execute("INSERT INTO cds_credit_events(contract_id, cik, entity_name, event_type, event_date, recovery_pct, notional_c, protection_c, accrued_c, late_c,"
                 " net_c, settle_date, settle_id, entry_id, close_entry_id, command_id, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, terms.get("cik"), str(entity), et, ed.isoformat(), str(rec), n, protection, accrued, late, net, sd.isoformat(), settle_id, entry_id,
                  closed.get("entry_id"), ctx.command_id, ctx.ts))
    ctx.touched_instruments.add((aid, cid))
    ctx.touched_accounts.add(aid)
    out = {"contract_id": cid, "reference_entity": str(entity), "cik": terms.get("cik"), "ticker": terms.get("ticker"), "event_type": et, "event_words": words,
           "event_date": ed.isoformat(), "recovery_pct": str(rec), "defaulted_c": n, "protection_c": protection, "accrued_c": accrued, "late_c": late,
           "reversed_c": late, "net_c": net, "value_c": net, "settle_date": sd.isoformat(), "settle_id": settle_id, "notional_before_c": n, "notional_after_c": 0,
           "cost_released_c": closed.get("cost_released_c", 0), "im_released_c": closed.get("im_released_c", 0), "entry_id": entry_id,
           "close_entry_id": closed.get("entry_id"), "im_entry_id": closed.get("im_entry_id"), "label": CREDIT_EVENT_LABEL, "contract_closed": True,
           "side": side, "date": sd.isoformat(), "kind": KIND}
    ctx.event("CDS_CREDIT_EVENT", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, **{k: v for k, v in out.items()})
    out["caught_up"] = caught
    out["contract"] = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    out["entries"] = [c["entry_id"] for c in caught] + [x for x in (entry_id, closed.get("entry_id"), closed.get("im_entry_id")) if x is not None]
    return out


def credit_events_of(services, contract_ids, conn=None) -> Dict[str, List[dict]]:
    """The simulated credit events of these contracts (the Derivatives page, the drawer, the blotter): ``{contract_id: [event]}`` with the
    keys a CDX event carries for ``blotters._credit_event_row`` plus ``event_type``, ``event_words`` and ``contract_closed``."""
    ids = [str(c) for c in (contract_ids or []) if c]
    out: Dict[str, List[dict]] = {}
    if not ids:
        return out
    with _marks.shared_reader(services.db, conn) as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cds_credit_events'").fetchone() is None:
            return out
        q = ",".join("?" for _ in ids)
        for r in c.execute(f"SELECT * FROM cds_credit_events WHERE contract_id IN ({q}) ORDER BY event_date, ts", ids).fetchall():
            d = {k: r[k] for k in r.keys()}
            d.update(reference_entity=d.get("entity_name"), defaulted_c=d.get("notional_c"), notional_before_c=d.get("notional_c"), notional_after_c=0,
                     event_words=EVENT_TYPES.get(str(d.get("event_type") or ""), str(d.get("event_type") or "").lower()), contract_closed=True,
                     label=CREDIT_EVENT_LABEL)
            out.setdefault(d["contract_id"], []).append(d)
    return out


# ---- views ----------------------------------------------------------------------------------------------------------------------------
def next_events(services, contract, conn=None, limit: int = 4) -> List[dict]:
    """C21: the next coupon payments (amount known: a fixed coupon) and the maturity."""
    row = swaps._contract_dict(services, contract, conn)
    if row is None or row.get("status") != "OPEN":
        return []
    terms = _terms(row)
    today = ny_of(services.clock.epoch()).date()
    buyer = (terms.get("side") or "BUY_PROTECTION") == "BUY_PROTECTION"
    periods = [(_date(a), _date(b), _date(p)) for a, b, p in (terms.get("fixed_periods") or [])]
    out = []
    for s, e, p in periods:
        if p < today:
            continue
        amt = _cdx.coupon_amount_c(int(row["notional_c"]), Decimal(str(terms.get("coupon_bp") or "100")), s, e)
        out.append({"date": p.isoformat(), "event": "MATURITY" if p == periods[-1][2] else "PAY",
                    "legs": [{"leg": "coupon", "side": "pay" if buyer else "receive", "amount_c": amt, "known": True, "period": [s.isoformat(), e.isoformat()]}],
                    "detail": f"CDS coupon {terms.get('coupon_bp')} bp x {(e - s).days} days / 360"})
        if len(out) >= 2:
            break
    if not any(x["event"] == "MATURITY" for x in out):
        out.append({"date": row["maturity_date"], "event": "MATURITY", "detail": "scheduled termination: the last coupon, VM unwound and IM released"})
    return out[:max(1, int(limit))]


def _jtd(side: str, n: int, value_c: Optional[int]) -> Optional[int]:
    if value_c is None:
        return None
    return _sign(side) * to_c(from_c(int(n)) * (ONE - RECOVERY)) - int(value_c)


def contract_detail(services, contract, conn=None) -> Optional[dict]:
    """C21: the single-name specifics for the contracts table and the drawer - the side as a sentence, the entity, the tenor, CS01, JTD, the
    live MODEL block, the terms in words, the credit event if any, label MODEL."""
    row = swaps._contract_dict(services, contract, conn)
    if row is None:
        return None
    terms = _terms(row)
    side = terms.get("side") or "BUY_PROTECTION"
    n = int(row["notional_c"])
    cs = None
    live = None
    jtd = None
    if row.get("status") == "OPEN":
        try:
            now = int(services.clock.epoch())
            st = _state(services, row, now, conn=conn)
            if st.get("ok") and not st.get("at_maturity") and st["mid"] > 0:
                base, pr = _value_at(st, st["mid"])
                up, _ = _value_at(st, st["mid"] + ONE)
                cs = None if up is None or base is None else int(up) - int(base)
                jtd = _jtd(side, n, base)
                m = st["model"]
                live = {"mid_spread_bp": str(st["mid"]), "model_spread_bp": str(st["model_bp"]), "basis_bp": str(st["basis"].quantize(BP_Q)),
                        "level_kind": (st["level"] or {}).get("level_kind"), "level_words": level_words(st["level"], st["model_bp"], st["mid"]),
                        "floor_bp": _js(m.get("floor_bp")), "raw_bp": _js(m.get("raw_bp")), "bucket": m.get("bucket"), "quality": m.get("quality"),
                        "upfront_pct": str((pr["upfront"] * PCT).quantize(PX4)), "price": str(pr["price"].quantize(PX4)), "rpv01": str(pr["rpv01"].quantize(RPV_Q)),
                        "value_c": base, "inputs_as_of": _js({"underlying": m.get("underlying"), "floor": (m.get("floor") or {}).get("as_of"),
                                                              "curve": (st.get("curve") or {}).get("curve_as_of"), "valuation_date": st["v"].isoformat()}),
                        "model_inputs": _js(m.get("inputs") or {}), "label": LABEL}
            elif not st.get("ok"):
                live = {"error": st.get("reason"), "code": st.get("code")}
        except (ConflictError, RejectError) as e:
            live = {"error": e.message, "code": e.code}
    ticker, tenor = terms.get("ticker"), terms.get("tenor_years")
    st_words = _side_text(ticker, tenor, side)
    cs_txt = "" if cs is None else f"; CS01 {'+' if cs >= 0 else '-'}{fmt_money(abs(cs))} per bp"
    ip = int(terms.get("initial_payment_c") or 0)
    terms_text = (f"{terms.get('entity_name')} ({ticker}, CIK {terms.get('cik')}) {tenor}Y to {terms.get('maturity_date')}, senior unsecured XR14, coupon"
                  f" {terms.get('coupon_bp')} bp, recovery 40 %; dealt {terms.get('deal_spread_bp')} bp (mid {terms.get('mid_spread_bp')} bp ="
                  f" {LABEL} + basis {terms.get('basis_bp')} bp, {TIER_WORDS.get(terms.get('tier'), terms.get('tier'))} tier"
                  f" {'+' if side == 'BUY_PROTECTION' else '-'} half-spread); upfront {terms.get('upfront_pct')} % (price {terms.get('price')}), accrued rebate"
                  f" {fmt_money(int(terms.get('rebate_c') or 0))}, initial payment {fmt_money(abs(ip))} {'received' if ip > 0 else 'paid'} {terms.get('settle_date')}")
    events = credit_events_of(services, [row["contract_id"]], conn).get(row["contract_id"], [])
    if events:
        ev = events[0]
        terms_text += (f"; simulated credit event ({ev.get('event_words')}) on {ev.get('event_date')} at {ev.get('recovery_pct')} % recovery - the contract"
                       " closed")
    return {"side": side, "side_text": f"{st_words}: USD {fmt_money(n)} notional{cs_txt}", "cik": terms.get("cik"), "ticker": ticker,
            "entity_name": terms.get("entity_name"), "entity_label": entity_label(ticker, tenor, terms.get("entity_name")), "tenor_years": tenor,
            "underlying_id": terms.get("underlying_id"), "credit_events": events, "exposure_c": -n if side == "BUY_PROTECTION" else n, "cs01_c": cs,
            "jtd_c": jtd, "jtd_rule": "jump to default = +/-(1 - 40 %) x notional less the contract's value now", "live": live, "terms_text": terms_text,
            "summary": terms_text, "deal_spread_bp": terms.get("deal_spread_bp"), "mid_spread_bp_at_deal": terms.get("mid_spread_bp"),
            "level_kind_at_deal": terms.get("level_kind"), "tier": terms.get("tier"), "upfront_c": terms.get("upfront_c"), "upfront_pct": terms.get("upfront_pct"),
            "price": terms.get("price"), "rebate_c": terms.get("rebate_c"), "initial_payment_c": terms.get("initial_payment_c"), "settle_date": terms.get("settle_date"),
            "coupon_bp": terms.get("coupon_bp"), "recovery": terms.get("recovery"), "model_at_deal": terms.get("model"), "label": LABEL,
            "label_words": LABEL_WORDS, "obligation": OBLIGATION_WORDS}


def display_fields(row: dict, terms: dict, inputs: dict) -> dict:
    """PORTFOLIO-06 (hook H1-10): what a position row and the drawer show - the dealt spread vs the MODEL mid now, the USD notional."""
    terms = terms or {}
    inputs = inputs or {}
    n = int(row.get("notional_c") or terms.get("notional_c") or 0)
    up = terms.get("upfront_c")
    dealt = _dec(terms.get("deal_spread_bp"))
    cur = _dec(inputs.get("mid_spread_bp"))
    return {"units": n // 100, "unit_label": "USD notional", "entry": None if dealt is None else f"{dealt.quantize(BP2)} bp",
            "current": None if cur is None else f"{cur.quantize(BP2)} bp", "notional_c": n,
            "extra": {"cik": terms.get("cik"), "ticker": terms.get("ticker"), "entity_name": terms.get("entity_name"), "tenor_years": terms.get("tenor_years"),
                      "side": terms.get("side"), "coupon_bp": terms.get("coupon_bp"), "upfront_c": up,
                      "upfront": None if up is None else ("received" if int(up) < 0 else "paid"), "entry_label": "dealt spread",
                      "current_label": "spread now (MODEL mid)", "floor_as_of": (inputs.get("proxy") or {}).get("as_of"),
                      "bucket": inputs.get("bucket") or (terms.get("model") or {}).get("bucket"), "label": LABEL}}


def rfq_describe(terms: dict, pv: Optional[dict] = None) -> Tuple[str, str, Optional[int], Optional[int]]:
    """``(instrument words, side words, units, notional cents)`` of a quote request (hook H1-12): ("ORCL 5Y CDS (Oracle Corp)", "BUY PROTECTION")."""
    t, pv = dict(terms or {}), dict(pv or {})
    tenor = pv.get("tenor_years") or t.get("tenor_years") or t.get("tenor") or DEFAULT_TENOR
    try:
        tenor = int(str(tenor).upper().rstrip("Y"))
    except ValueError:
        tenor = DEFAULT_TENOR
    if pv.get("entity_label"):
        label = str(pv["entity_label"])
    else:
        ticker = pv.get("ticker") or t.get("ticker")
        label = entity_label(str(ticker).upper() if ticker else (f"CIK {t.get('cik')}" if t.get("cik") else "CDS"), tenor, pv.get("entity_name"))
    side = str(t.get("side") or pv.get("side") or "BUY_PROTECTION").upper().replace(" ", "_")
    side = {"BUY": "BUY_PROTECTION", "SELL": "SELL_PROTECTION"}.get(side, side)
    notional = pv.get("notional_c")
    if notional is None:
        try:
            if t.get("notional_c") not in (None, ""):
                notional = int(Decimal(str(t["notional_c"])))
            elif t.get("notional") not in (None, ""):
                notional = to_c(str(t["notional"]).replace(",", ""))
        except (InvalidOperation, ValueError, TypeError):
            notional = None
    return label, side.replace("_", " "), None, notional


# ---- the editable tier terms (cds.terms) -----------------------------------------------------------------------------------------------
def _parse_term(field: str, value) -> Decimal:
    spec = TERM_SPECS.get(field)
    if spec is None:
        raise RejectError("TERMS_FIELD", f"{field!r} is not an editable single-name CDS term; one of {', '.join(TERM_SPECS)}", {"field": field})
    if value is None or isinstance(value, (bool, float)) or (isinstance(value, str) and not value.strip()):
        raise RejectError("TERMS_VALUE", f"{spec['label']}: a decimal string is required (never stored as 0), got {value!r}", {"field": field})
    s = str(value).strip().replace(",", "")
    pct = s.endswith("%")
    try:
        d = Decimal(s.rstrip("%").strip())
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS_VALUE", f"{spec['label']}: {value!r} is not a number", {"field": field}) from e
    if not d.is_finite():
        raise RejectError("TERMS_VALUE", f"{spec['label']}: {value!r} is not a finite number", {"field": field})
    if spec["unit"] == "pct" and (pct or d >= 1):
        d = d / PCT                                     # "4" or "4 %" -> 0.04 (a fraction of 1 or more is read as a percent)
    if d != d.quantize(Decimal("0.00000001")):
        raise RejectError("TERMS_VALUE", f"{spec['label']}: at most 8 decimal places", {"field": field})
    if d < spec["min"] or d > spec["max"]:
        lo, hi = (spec["min"] * PCT, spec["max"] * PCT) if spec["unit"] == "pct" else (spec["min"], spec["max"])
        raise RejectError("TERMS_VALUE", f"{spec['label']}: {d if spec['unit'] != 'pct' else d * PCT} is outside {lo} to {hi} {'%' if spec['unit'] == 'pct' else 'bp'}",
                          {"field": field})
    return qprice(d)


def _open_pb(conn, aid: str) -> dict:
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    acct = account_row(conn, aid)
    if acct is None:
        raise NotFound("ACCOUNT_UNKNOWN", f"account {aid} does not exist", {"account_id": aid})
    if acct["kind"] != "PB" or acct["status"] != "OPEN":
        raise RejectError("NOT_TRADEABLE_HERE", f"{aid} is not an open prime-broker account", {"account_id": aid})
    return dict(acct)


def h_terms(ctx: TxContext, payload: dict) -> dict:
    """``cds.terms {account_id, tier, im_pct?, half_spread_bp?}`` (light; plan 5.1): validates, stores and records ONE SETTINGS_CHANGED event with
    before / after; 400 NOTHING_TO_UPDATE when nothing differs.  Every quote and preview after it uses the new terms; past postings stay."""
    aid = str(payload.get("account_id") or "")
    conn = ctx.conn
    acct = _open_pb(conn, aid)
    tier = str(payload.get("tier") or "").strip().upper()
    if tier not in TIERS:
        raise RejectError("TERMS_FIELD", f"tier must be one of {', '.join(TIERS)}, got {payload.get('tier')!r}", {"field": "tier"})
    given = {k: payload[k] for k in TERM_SPECS if k in payload}
    if not given:
        raise RejectError("NOTHING_TO_UPDATE", f"no single-name CDS term given; editable: {', '.join(TERM_SPECS)}")
    parsed = {k: _parse_term(k, v) for k, v in given.items()}
    data, _ts = _stored_terms(conn, aid)
    cur = tiers_for(conn, aid, prime.broker_of(acct))[tier]
    mine = dict(data.get(tier) or {})
    before, after = {}, {}
    for k, v in parsed.items():
        if Decimal(str(cur[k])) != v:
            before[k], after[k] = str(cur[k]), str(v)
            mine[k] = str(v)
    if not after:
        raise RejectError("NOTHING_TO_UPDATE", "every given term already has that value", {"given": {k: str(v) for k, v in parsed.items()}})
    for k, seed in cur["seed"].items():
        mine.setdefault(k, str(seed))
    data[tier] = mine
    _ensure_tables(ctx.services, conn)
    conn.execute("INSERT INTO cds_terms(account_id, terms_json, updated_ts, command_id) VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET"
                 " terms_json=excluded.terms_json, updated_ts=excluded.updated_ts, command_id=excluded.command_id", (aid, json.dumps(data, sort_keys=True), ctx.ts, ctx.command_id))
    event_id = ctx.event("SETTINGS_CHANGED", account_id=aid, ref_type="cds_terms", ref_id=aid, tier=tier, before=before, after=after, fields=sorted(after),
                         note="single-name CDS tier terms (assumed, editable); past postings are never restated")
    now_t = tiers_for(conn, aid, prime.broker_of(acct))[tier]
    return {"account_id": aid, "tier": tier, "before": before, "after": after, "terms": {k: str(now_t[k]) for k in TERM_SPECS}, "event_id": event_id}


# ---- the dealing level (cds.basis) and its effect on open contracts ---------------------------------------------------------------------
def _parse_level(payload: dict) -> Tuple[str, Optional[Decimal]]:
    """``(action, negotiated mid)`` of a ``cds.basis`` payload: NEGOTIATED with a mid in bp, MODEL (``deal_at_model``) or CLEAR."""
    def truthy(v):
        return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")
    acts = []
    if payload.get("negotiated_mid_bp") not in (None, ""):
        acts.append("NEGOTIATED")
    if truthy(payload.get("deal_at_model")):
        acts.append("MODEL")
    if truthy(payload.get("clear")):
        acts.append("CLEAR")
    if len(acts) != 1:
        raise RejectError("TERMS", "give exactly one of: negotiated_mid_bp (your mid in bp), deal_at_model (your confirmation) or clear", {"field": "negotiated_mid_bp"})
    if acts[0] != "NEGOTIATED":
        return acts[0], None
    raw = payload.get("negotiated_mid_bp")
    d = _dec(raw)
    if isinstance(raw, (bool, float)) or d is None:
        raise RejectError("TERMS", f"negotiated_mid_bp: your mid spread in bp as a decimal string (e.g. 300.00), got {raw!r}", {"field": "negotiated_mid_bp"})
    if d <= 0 or d > Decimal(20000) or d != d.quantize(BP_Q):
        raise RejectError("TERMS", f"negotiated_mid_bp: a spread above 0 and at most 20,000 bp with at most 4 decimals, got {raw!r}", {"field": "negotiated_mid_bp"})
    return "NEGOTIATED", d


def _cik_of(services, conn, cik, ticker=None) -> int:
    """The CIK of a ``cds.basis`` / basis-effect request: ``cik`` as given, else the ``ticker`` resolved through C-ENT (404 ENTITY_UNKNOWN)."""
    if cik not in (None, "") and not isinstance(cik, bool):
        try:
            v = int(str(cik).strip())
        except ValueError as e:
            raise RejectError("TERMS", f"cik must be an integer (the SEC Central Index Key), got {cik!r}", {"field": "cik"}) from e
        if v <= 0:
            raise RejectError("TERMS", "cik must be a positive integer", {"field": "cik"})
        return v
    t = str(ticker or "").strip().upper()
    if not t:
        raise RejectError("TERMS", "the reference entity is required: cik (SEC Central Index Key) or ticker", {"field": "cik"})
    ce = _entities(services)
    fn = getattr(ce, "entity", None) if ce is not None else None
    ent = fn(conn, t) if callable(fn) else None
    if not ent:
        raise NotFound("ENTITY_UNKNOWN", f"no reference entity {t}: no US-listed SEC registrant has that ticker", {"field": "ticker", "key": t})
    return int(ent["cik"])


def _reference_model(services, conn, cik: int, now: int, marks=None) -> dict:
    """The model of the 5Y on-the-run maturity now: the reference a negotiated mid is measured against (basis = negotiated - this mid)."""
    today = ny_of(int(now)).date()
    maturity = maturity_for(roll_of(services.cal, today)["roll_date"], DEFAULT_TENOR)
    m = model_now(services, int(cik), maturity, conn=conn, now=int(now), marks=marks)
    if not m.get("ok"):
        code = str(m.get("code") or "INPUTS_PENDING")
        reason = str(m.get("reason") or code)
        details = _js({k: v for k, v in m.items() if k not in ("ok", "code", "reason")})
        if code == "INPUTS_PENDING":
            raise ConflictError("INPUTS_PENDING", f"the model mid of CIK {cik} cannot be computed now ({reason}): preview the name first", details)
        if code == "ENTITY_UNKNOWN":
            raise NotFound(code, reason, details)
        raise ConflictError(code, f"the model mid of CIK {cik} cannot be computed now: {reason}", details)
    m["_maturity"] = maturity
    return m


def _new_deal_terms(services, conn, acct: dict, model: dict, level_kind, basis: Decimal) -> dict:
    """For a new 5Y deal at a level: the mid, its bucket, the tier, the IM % and the half-spread (x the multiplier)."""
    mid = (Decimal(str(model["spread_bp"])) + basis).quantize(BP_Q)
    bucket = bucket_of_mid(services, mid, model) if mid > 0 else None
    tier = TIER_OF_BUCKET.get(bucket) if bucket else None
    out = {"level_kind": level_kind, "basis_bp": str(basis.quantize(BP_Q)), "mid_bp": str(mid), "bucket": bucket, "tier": tier, "im_pct": None,
           "half_spread_bp": None, "half_spread_eff_bp": None}
    if tier:
        t = tiers_for(conn, acct["account_id"], prime.broker_of(acct))[tier]
        mult = qprice(Decimal(prime.read_schedule(conn, acct).otc_spread_mult))
        out.update(im_pct=str(t["im_pct"]), half_spread_bp=str(t["half_spread_bp"]), half_spread_eff_bp=str((t["half_spread_bp"] * mult).quantize(BP_Q)))
    return out


def _open_on_name(conn, account_id: str, cik: int) -> List[dict]:
    out = []
    for r in conn.execute("SELECT * FROM otc_contracts WHERE account_id=? AND kind=? AND status='OPEN' ORDER BY contract_id", (account_id, KIND)).fetchall():
        d = {k: r[k] for k in r.keys()}
        try:
            if int(_terms(d).get("cik") or 0) == int(cik):
                out.append(d)
        except (TypeError, ValueError):
            continue
    return out


def basis_effect(services, account_id: str, cik, negotiated_mid_bp=None, deal_at_model=False, clear=False, conn=None, now: Optional[int] = None,
                 marks=None, ticker=None) -> dict:
    """``GET /api/otc/cds/basis/effect`` and the record of ``cds.basis`` (plan 3.5, test 17): computed, nothing written - the model mid of the
    name now, the level before and after (kind, basis, mid, bucket, tier, IM %, half-spread for a new 5Y deal), the default coupon (which a
    basis never changes), and every OPEN contract on the name in this account with its value now and at the new level (full revaluation, not
    CS01 x the change) and the total."""
    ts = int(now if now is not None else services.clock.epoch())
    payload = {"negotiated_mid_bp": negotiated_mid_bp, "deal_at_model": deal_at_model, "clear": clear}
    action, neg = _parse_level(payload)
    with _marks.shared_reader(services.db, conn) as c:
        cik_i = _cik_of(services, c, cik, ticker)
        acct = _open_pb(c, str(account_id or ""))
        ce = _entities(services)
        ent = ce.entity(c, cik_i) if ce is not None and callable(getattr(ce, "entity", None)) else None
        if ce is not None and callable(getattr(ce, "entity", None)) and not ent:
            raise NotFound("ENTITY_UNKNOWN", f"no reference entity with CIK {cik_i}", {"field": "cik"})
        model = _reference_model(services, c, cik_i, ts, marks)
        model_bp = Decimal(str(model["spread_bp"]))
        cur = level_of(c, acct["account_id"], cik_i)
        if action == "MODEL" and str(model.get("quality") or "") == "distressed":
            raise ConflictError("DISTRESSED_NEEDS_NEGOTIATED_MID", _distressed_words(dict(ent or {"name": f"CIK {cik_i}", "ticker": "?"}), model), {"cik": cik_i})
        new_basis = (neg - model_bp).quantize(BP_Q) if action == "NEGOTIATED" else ZERO
        new_kind = {"NEGOTIATED": "NEGOTIATED", "MODEL": "MODEL", "CLEAR": None}[action]
        before = _new_deal_terms(services, c, acct, model, (cur or {}).get("level_kind"), _basis(cur))
        after = _new_deal_terms(services, c, acct, model, new_kind, new_basis)
        rows = []
        total = 0
        missing = []
        for row in _open_on_name(c, acct["account_id"], cik_i):
            st_now = _state(services, row, ts, conn=c, marks=marks)
            st_new = _state(services, row, ts, conn=c, marks=marks, basis_override=new_basis)
            v0 = _value_at(st_now, st_now["mid"])[0] if st_now.get("ok") else None
            v1 = _value_at(st_new, st_new["mid"])[0] if st_new.get("ok") else None
            terms = _terms(row)
            item = {"contract_id": row["contract_id"], "side": terms.get("side"), "notional_c": int(row["notional_c"]), "tenor_years": terms.get("tenor_years"),
                    "mid_before_bp": str(st_now["mid"]) if st_now.get("ok") else None, "mid_after_bp": str(st_new["mid"]) if st_new.get("ok") else None,
                    "value_before_c": v0, "value_after_c": v1, "effect_c": None if v0 is None or v1 is None else v1 - v0}
            if item["effect_c"] is None:
                missing.append(row["contract_id"])
                item["note"] = (st_now.get("reason") if not st_now.get("ok") else st_new.get("reason")) or "the mid is not positive at that level"
            else:
                total += item["effect_c"]
            rows.append(item)
    name = (ent or {}).get("name") or f"CIK {cik_i}"
    return {"account_id": acct["account_id"], "cik": cik_i, "ticker": (ent or {}).get("ticker"), "entity_name": name, "action": action,
            "model_mid_bp": str(model_bp.quantize(BP_Q)), "model_maturity": model["_maturity"].isoformat(), "model_bucket": model.get("bucket"),
            "model": _model_summary(model), "before": before, "after": after, "coupon_default_bp": default_coupon_bp(model),
            "coupon_note": "the default coupon follows the MODEL bucket before any basis: a negotiated mid never changes it",
            "contracts": rows, "effect_total_c": None if missing else total, "effect_total_known_c": total, "missing": missing, "as_of": ts,
            "negotiated_mid_bp": None if neg is None else str(neg), "basis_bp_after": str(new_basis),
            "note": ("a new level re-marks every open contract on this name in this account at once (one name has one mark per account); past postings"
                     " are never restated; a VM computed later uses the level current then")}


def h_basis(ctx: TxContext, payload: dict) -> dict:
    """``cds.basis {account_id, cik, negotiated_mid_bp | deal_at_model: true | clear: true}`` (light; plan 3.5): the owner's dealing level for one
    reference entity in one PB account - his negotiated mid (stored as basis = negotiated - the model mid now, with the model and the time), his
    confirmation to deal at the MODEL (basis 0), or clear (the guard applies again).  ONE SETTINGS_CHANGED event with before, after and the
    effect on the open contracts (computed at this instant).  409 INPUTS_PENDING when the model mid cannot be computed now; 409
    DISTRESSED_NEEDS_NEGOTIATED_MID for a MODEL confirmation of a distressed name; 400 NOTHING_TO_UPDATE for a clear without a level."""
    services = ctx.services
    conn = ctx.conn
    aid = str(payload.get("account_id") or "")
    _open_pb(conn, aid)
    action, neg = _parse_level(payload)
    cik = _cik_of(services, conn, payload.get("cik"), payload.get("ticker"))
    _ensure_tables(services, conn)
    cur = level_of(conn, aid, cik)
    if action == "CLEAR" and cur is None:
        raise RejectError("NOTHING_TO_UPDATE", f"no dealing level is set for CIK {cik} in {aid}", {"cik": cik})
    eff = basis_effect(services, aid, cik, negotiated_mid_bp=None if neg is None else str(neg), deal_at_model=action == "MODEL", clear=action == "CLEAR",
                       conn=conn, now=ctx.ts, marks=ctx.marks)
    model_bp = Decimal(eff["model_mid_bp"])
    if action == "CLEAR":
        conn.execute("DELETE FROM cds_basis WHERE account_id=? AND cik=?", (aid, cik))
    else:
        if cur is not None and cur.get("level_kind") == action and (action == "MODEL" or _dec(cur.get("negotiated_mid_bp")) == neg):
            raise RejectError("NOTHING_TO_UPDATE", "that dealing level is already set", {"cik": cik, "level_kind": action})
        conn.execute("INSERT INTO cds_basis(account_id, cik, level_kind, basis_bp, negotiated_mid_bp, model_mid_bp, model_json, entered_ts, command_id)"
                     " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id, cik) DO UPDATE SET level_kind=excluded.level_kind, basis_bp=excluded.basis_bp,"
                     " negotiated_mid_bp=excluded.negotiated_mid_bp, model_mid_bp=excluded.model_mid_bp, model_json=excluded.model_json,"
                     " entered_ts=excluded.entered_ts, command_id=excluded.command_id",
                     (aid, cik, action, eff["basis_bp_after"], None if neg is None else str(neg), str(model_bp.quantize(BP_Q)), dumps(eff["model"]), ctx.ts,
                      ctx.command_id))
    effect = {"contracts": [{"contract_id": r["contract_id"], "value_before_c": r["value_before_c"], "value_after_c": r["value_after_c"], "effect_c": r["effect_c"]}
                            for r in eff["contracts"]], "effect_total_c": eff["effect_total_c"]}
    words = {"NEGOTIATED": f"your negotiated mid {_bp_words(neg)} for {eff['entity_name']} (basis {_signed_bp(eff['basis_bp_after'])} bp to the MODEL"
                           f" {_bp_words(model_bp)})",
             "MODEL": f"you confirmed dealing {eff['entity_name']} at the MODEL ({_bp_words(model_bp)}) - no market level known",
             "CLEAR": f"the dealing level of {eff['entity_name']} cleared: a new deal needs your negotiated mid or a MODEL confirmation again"}[action]
    event_id = ctx.event("SETTINGS_CHANGED", account_id=aid, ref_type="cds_basis", ref_id=f"{aid}:{cik}", cik=cik, ticker=eff.get("ticker"), action=action,
                         before=eff["before"], after=eff["after"], effect=effect, model_mid_bp=eff["model_mid_bp"], words=words,
                         note="single-name CDS dealing level; past postings are never restated; a VM computed later uses the level current then")
    return {"account_id": aid, "cik": cik, "action": action, "level": level_of(conn, aid, cik), "before": eff["before"], "after": eff["after"],
            "effect": effect, "contracts": eff["contracts"], "model_mid_bp": eff["model_mid_bp"], "coupon_default_bp": eff["coupon_default_bp"],
            "words": words, "event_id": event_id}


# ---- risk (the single-name block of risk.report; plan 5.2, 6.6) ---------------------------------------------------------------------------
def _spread_at(services, model: dict, mult: Decimal) -> Optional[Decimal]:
    cm = _model_mod(services)
    fn = getattr(cm, "spread_at", None) if cm is not None else None
    if not callable(fn):
        return None
    try:
        v = fn(model, Decimal(mult))
    except Exception:   # noqa: BLE001 - a model that cannot be re-run shows "unavailable"
        return None
    d = _dec(v)
    return None if d is None else d


def _shocked_value(st: dict, mid) -> Optional[int]:
    m = max(Decimal(str(mid)).quantize(BP_Q), MID_FLOOR_BP)
    return _value_at(st, m)[0]


def risk_block(services, rows, base_c: int) -> Optional[dict]:
    """The "Single-name CDS" block of the Risk page (contract C-RISK, plan 6.6): per contract its credit exposure, CS01, jump to default and
    value; per reference entity the gross and net, the net JTD and its share of NAV, CS01, the dealing level and the equity shocks of that name
    alone; spread scenarios (-25 / +25 / +50 / +100 bp, the mid floored at 1 bp), equity scenarios through the model (every reference equity
    -30 % / +30 % together; the name with the largest jump-to-default alone -30 / +30 / +50 %), the default of the largest JTD loss, and a
    CDS_JTD_CONCENTRATION warning above 10 % of NAV.  A name whose model cannot be re-run shows "unavailable" (None), never 0.  ``rows`` are
    risk's PosRisk rows (kind SWAP, ``extra.kind`` CDS); None when there are none."""
    mine = [r for r in rows if getattr(r, "kind", None) == "SWAP" and (getattr(r, "extra", None) or {}).get("kind") == KIND]
    if not mine:
        return None
    now = int(services.clock.epoch())
    out_rows: List[dict] = []
    missing: List[str] = []
    names: Dict[int, dict] = {}
    shocks = {b: 0 for b in SPREAD_SHOCKS_BP}
    states: List[Tuple[dict, dict, int]] = []
    cs_total = short_c = long_c = 0
    with _marks.shared_reader(services.db) as conn:
        for r in mine:
            row = swaps._contract_dict(services, r.instrument_id, conn)
            if row is None or row.get("status") != "OPEN":
                continue
            terms = _terms(row)
            n = int(row["notional_c"])
            side = terms.get("side") or "BUY_PROTECTION"
            exp = -n if side == "BUY_PROTECTION" else n
            cik = int(terms.get("cik") or 0)
            if exp < 0:
                short_c += -exp
            else:
                long_c += exp
            st = _state(services, row, now, conn=conn)
            item = {"contract_id": row["contract_id"], "account_id": row["account_id"], "label": f"{terms.get('ticker')} {terms.get('tenor_years')}Y CDS"
                    f" {'sell' if side == 'SELL_PROTECTION' else 'buy'} protection", "cik": cik, "ticker": terms.get("ticker"), "entity_name": terms.get("entity_name"),
                    "side": side, "notional_c": n, "exposure_c": exp, "tenor_years": terms.get("tenor_years"), "maturity_date": row["maturity_date"],
                    "mid_spread_bp": None, "model_spread_bp": None, "basis_bp": None, "bucket": None, "cs01_c": None, "jtd_c": None, "value_c": None,
                    "quality": "unavailable", "note": None}
            nm = names.setdefault(cik, {"cik": cik, "ticker": terms.get("ticker"), "entity_name": terms.get("entity_name"), "contracts": 0, "gross_notional_c": 0,
                                        "net_exposure_c": 0, "jtd_c": 0, "jtd_known": True, "cs01_c": 0, "levels": set(), "eq": {"0.7": 0, "1.3": 0, "1.5": 0},
                                        "eq_known": {"0.7": True, "1.3": True, "1.5": True}})
            nm["contracts"] += 1
            nm["gross_notional_c"] += n
            nm["net_exposure_c"] += exp
            if not st.get("ok") or st.get("at_maturity") or st["mid"] <= 0:
                item["note"] = st.get("reason") if not st.get("ok") else "no model value now"
                missing.append(row["contract_id"])
                nm["jtd_known"] = False
                for k in nm["eq_known"]:
                    nm["eq_known"][k] = False
                out_rows.append(item)
                continue
            base, _ = _value_at(st, st["mid"])
            up, _ = _value_at(st, st["mid"] + ONE)
            mv = getattr(r, "mv_c", None)
            mv = int(mv) if mv is not None else int(base)
            jtd = _jtd(side, n, mv)
            cs = None if up is None else int(up) - int(base)
            item.update(mid_spread_bp=str(st["mid"]), model_spread_bp=str(st["model_bp"]), basis_bp=str(st["basis"].quantize(BP_Q)), bucket=st["model"].get("bucket"),
                        cs01_c=cs, jtd_c=jtd, value_c=int(base), quality=str(st["model"].get("quality") or "computed"), level_kind=(st["level"] or {}).get("level_kind"))
            if cs is not None:
                cs_total += cs
                nm["cs01_c"] += cs
            nm["jtd_c"] += jtd
            nm["levels"].add((st["level"] or {}).get("level_kind") or "NONE")
            for b in SPREAD_SHOCKS_BP:
                v = _shocked_value(st, st["mid"] + Decimal(b))
                if v is not None:
                    shocks[b] += int(v) - int(base)
            for k in ("0.7", "1.3", "1.5"):
                s = _spread_at(services, st["model"], Decimal(k))
                if s is None:
                    nm["eq_known"][k] = False
                    continue
                v = _shocked_value(st, s + st["basis"])
                if v is None:
                    nm["eq_known"][k] = False
                else:
                    nm["eq"][k] += int(v) - int(base)
            states.append((st, item, int(base)))
            out_rows.append(item)
    nav = int(base_c or 0)
    by_name = []
    warnings = []
    for cik, nm in sorted(names.items(), key=lambda kv: str(kv[1].get("ticker") or kv[0])):
        jtd = nm["jtd_c"] if nm["jtd_known"] else None
        levels = sorted(nm["levels"])
        row = {"cik": cik, "ticker": nm["ticker"], "entity_name": nm["entity_name"], "contracts": nm["contracts"], "gross_notional_c": nm["gross_notional_c"],
               "net_exposure_c": nm["net_exposure_c"], "jtd_c": jtd, "jtd_pct_nav": None if jtd is None or not nav else (Decimal(jtd) / Decimal(nav)).quantize(PX4),
               "cs01_c": nm["cs01_c"], "level_kind": levels[0] if len(levels) == 1 else ("MIXED" if levels else None),
               "eq_dn30_pnl_c": nm["eq"]["0.7"] if nm["eq_known"]["0.7"] else None, "eq_up30_pnl_c": nm["eq"]["1.3"] if nm["eq_known"]["1.3"] else None,
               "eq_up50_pnl_c": nm["eq"]["1.5"] if nm["eq_known"]["1.5"] else None}
        by_name.append(row)
        if jtd is not None and jtd < 0 and nav > 0 and Decimal(-jtd) / Decimal(nav) > JTD_WARN:
            frac = (Decimal(-jtd) / Decimal(nav)).quantize(PX4)
            warnings.append({"code": "CDS_JTD_CONCENTRATION", "severity": "WARN",
                             "message": (f"{nm['entity_name'] or nm['ticker']} jump to default would lose USD {fmt_money(-jtd)} ({(frac * PCT).quantize(BP2)} % of NAV,"
                                         f" above {(JTD_WARN * PCT).quantize(Decimal('1'))} %)"),
                             "account_id": None, "instrument_id": None, "value": str(frac), "threshold": str(JTD_WARN)})
    known = [x for x in by_name if x["jtd_c"] is not None]
    gains = sum(x["jtd_c"] for x in known if x["jtd_c"] > 0)
    losses = sum(x["jtd_c"] for x in known if x["jtd_c"] < 0)
    scenarios = [{"name": f"single-name spreads {'+' if b > 0 else ''}{b} bp", "shock_bp": b, "pnl_c": shocks[b]} for b in SPREAD_SHOCKS_BP]
    for k, words in (("0.7", "-30 %"), ("1.3", "+30 %")):
        unknown = [x["ticker"] or str(x["cik"]) for x in by_name if (x["eq_dn30_pnl_c"] if k == "0.7" else x["eq_up30_pnl_c"]) is None]
        pnl = None if unknown else sum((x["eq_dn30_pnl_c"] if k == "0.7" else x["eq_up30_pnl_c"]) for x in by_name)
        scenarios.append({"name": f"single-name equities {words} (spreads through the model)", "equity_shock": str((Decimal(k) - ONE).quantize(BP2)), "pnl_c": pnl,
                          **({"unavailable": unknown, "note": "unavailable: the model cannot be re-run for " + ", ".join(unknown)} if unknown else {})})
    top = max(known, key=lambda x: (abs(x["jtd_c"]), str(x["ticker"]))) if known else None
    if top is not None:
        for k, col, words in (("0.7", "eq_dn30_pnl_c", "-30 %"), ("1.3", "eq_up30_pnl_c", "+30 %"), ("1.5", "eq_up50_pnl_c", "+50 %")):
            scenarios.append({"name": f"{top['ticker']} equity {words} alone (largest jump-to-default)", "equity_shock": str((Decimal(k) - ONE).quantize(BP2)), "cik": top["cik"],
                              "pnl_c": top[col], **({} if top[col] is not None else {"note": "unavailable: the model cannot be re-run for this name"})})
    worst = min(known, key=lambda x: (x["jtd_c"], str(x["ticker"]))) if known else None
    if worst is not None and worst["jtd_c"] < 0:
        scenarios.append({"name": f"default of {worst['ticker']} (largest jump-to-default loss)", "cik": worst["cik"], "pnl_c": worst["jtd_c"]})
    elif top is not None:
        scenarios.append({"name": f"default of {top['ticker']} (largest jump-to-default)", "cik": top["cik"], "pnl_c": top["jtd_c"]})
    pct = None if not nav else (Decimal(short_c + long_c) / Decimal(nav)).quantize(PX4)
    return {"bucket": "Credit derivatives (single-name CDS notional)", "rows": out_rows, "by_name": by_name, "cs01_c": cs_total, "jtd_gain_c": gains,
            "jtd_loss_c": losses, "net_jtd_c": gains + losses, "short_credit_c": short_c, "long_credit_c": long_c, "gross_pct_nav": pct, "missing": missing,
            "scenarios": scenarios, "warnings": warnings, "label": LABEL,
            "note": ("CS01 = P&L for +1 bp of each name's spread; jump to default = (1 - 40 %) x notional less the contract's value now, signed; spreads are"
                     " MODEL")}


# ---- the desk -----------------------------------------------------------------------------------------------------------------------
def _account_contracts(conn, account_id: str) -> List[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM otc_contracts WHERE account_id=? AND kind=? ORDER BY contract_id", (account_id, KIND)).fetchall()]


def desk(services, account_id: str) -> dict:
    """What the single-name CDS desk needs (plan 6.5): the PB, eligibility, the spread multiplier and margin mode, the FRED ladder and floor, the
    tier terms with their seeds, the tenors and their maturities, the coupons, the settlement rule, the MODEL text and label, the universe, the
    account's dealing levels, its CDS contracts, the conventions and the Unsupported rows."""
    with _marks.shared_reader(services.db) as conn:
        acct = account_row(conn, account_id)
        if acct is None:
            raise NotFound("ACCOUNT_UNKNOWN", f"account {account_id} does not exist", {"account_id": account_id})
        acct = dict(acct)
        if acct["kind"] != "PB":
            raise RejectError("NOT_TRADEABLE_HERE", f"{account_id} is not a prime-broker account: single-name CDS are dealt with a prime broker", {"account_id": account_id})
        sched = prime.read_schedule(conn, acct)
        try:
            rel = prime.relationship(conn, account_id)
        except Exception:   # noqa: BLE001
            rel = {}
        broker = prime.broker_of(acct)
        tiers = tiers_for(conn, account_id, broker)
        rows = _account_contracts(conn, account_id)
        try:
            bases = [{k: r[k] for k in r.keys()} for r in conn.execute("SELECT * FROM cds_basis WHERE account_id=? ORDER BY cik", (account_id,)).fetchall()]
        except sqlite3.OperationalError:
            bases = []
        ce = _entities(services)
        universe = None
        if ce is not None and callable(getattr(ce, "universe_status", None)):
            try:
                universe = _js(ce.universe_status(conn))
            except Exception as e:   # noqa: BLE001 - named, never a guessed count
                universe = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        for b in bases:
            ent = None
            if ce is not None and callable(getattr(ce, "entity", None)):
                try:
                    ent = ce.entity(conn, int(b["cik"]))
                except Exception:   # noqa: BLE001 - the row still shows its CIK
                    ent = None
            b.pop("model_json", None)
            b["ticker"] = (ent or {}).get("ticker")
            b["name"] = (ent or {}).get("name")
    try:
        elig = _eligibility(services, split_account_id(account_id)[0]).to_dict()
    except Exception as e:   # noqa: BLE001
        elig = {"error": str(e)[:200]}
    blocked = None
    if not (elig.get("pb_otc") and elig.get("ecp")):
        blocked = elig.get("pb_otc_reason") or "this book is not eligible for OTC bookings"
    now = int(services.clock.epoch())
    today = ny_of(now).date()
    roll = roll_of(services.cal, today)
    mult = qprice(Decimal(sched.otc_spread_mult))
    cm = _model_mod(services)
    ladder = None
    if cm is not None and callable(getattr(cm, "ladder", None)):
        try:
            ladder = _js(cm.ladder(services, today=today))
        except TypeError:
            ladder = _js(cm.ladder(services))
        except Exception as e:   # noqa: BLE001 - named: no dealing tier can be set without the ladder
            ladder = {"ok": False, "code": "FLOOR_UNAVAILABLE", "reason": f"{type(e).__name__}: {str(e)[:160]}"}
    tier_out = {}
    for t, v in tiers.items():
        tier_out[t] = {"buckets": TIER_BUCKETS[t], "words": TIER_WORDS[t], "im_pct": str(v["im_pct"]), "half_spread_bp": str(v["half_spread_bp"]),
                       "half_spread_eff_bp": str((v["half_spread_bp"] * mult).quantize(BP_Q)), "coupon_default_bp": 500 if t in ("BB", "B", "CCC") else 100,
                       "seed": {k: str(x) for k, x in v["seed"].items()}, "source": v["source"], "updated_ts": v["updated_ts"]}
    return {"account_id": account_id, "broker": broker, "relationship": rel.get("agreements_line") if isinstance(rel, dict) else None, "eligibility": elig,
            "blocked": blocked, "otc_spread_mult": str(mult), "margin_mode": str(sched.swap_margin_mode).upper(), "ladder": ladder,
            "floor": (ladder or {}).get("floor") if isinstance(ladder, dict) else None, "tiers": tier_out,
            "term_specs": {k: {kk: (str(vv) if isinstance(vv, Decimal) else vv) for kk, vv in v.items()} for k, v in TERM_SPECS.items()},
            "tenors": list(TENORS), "default_tenor": DEFAULT_TENOR, "coupons": list(COUPONS_BP), "recovery": str(RECOVERY),
            "roll": {"roll_date": roll["roll_date"].isoformat(), "next_roll": roll["next_roll"].isoformat(),
                     "maturities": {k: v.isoformat() for k, v in roll["maturities"].items()}},
            "settle_rule": f"T+{UPFRONT_SETTLE_BD} business days (the bilateral standard; the prime broker is the counterparty of record)",
            "model": _model_text(services), "label": LABEL, "label_words": LABEL_WORDS, "support": "LIMITED", "universe": universe,
            "bases": [_js(b) for b in bases], "contracts": [r["contract_id"] for r in rows],
            "conventions": {k: tables.CONVENTIONS.get(k, "") for k in ("CDS_MODEL", "CDS_DEAL", "CDS_COUPON", "CDS_CREDIT")},
            "unsupported": unsupported(), "obligation": OBLIGATION_WORDS, "model_version": MODEL_VERSION,
            "modules": {"credit_entities": _entities(services) is not None, "cds_model": cm is not None}}


# ---- routes, registry, binding -----------------------------------------------------------------------------------------------------
def register_routes(router) -> None:
    """C-ENGINE (plan 6.5): ``GET /api/otc/cds/desk``, ``POST /api/otc/cds/preview``, ``POST /api/otc/cds/terms``, ``POST /api/otc/cds/basis``,
    ``GET /api/otc/cds/basis/effect``, ``POST /api/otc/cds/contracts/{id}/credit-event``.  Booking and termination use the OTC routes (RFQ:
    ``POST /api/otc/rfqs`` kind CDS; ``POST /api/otc/contracts/{id}/terminate``)."""
    from ..api import ApiError, NotFound as _ApiNotFound, need, run_command

    def _refused(aid, e: RejectError) -> dict:
        return {"refused": True, "bookable": False, "kind": KIND, "account_id": aid, "code": e.code, "message": e.message, "details": e.details or {},
                "blockers": [{"code": e.code, "message": e.message, "details": e.details or {}}]}

    def cds_preview(req):
        rt = req.rt
        sw = need(rt, "swaps")
        aid = str(req.b("account_id") or "")
        with _marks.shared_reader(rt.db) as conn:
            if account_row(conn, aid) is None:
                raise _ApiNotFound(f"account {aid} does not exist")
        try:
            return sw.preview(rt, aid, KIND, dict(req.b("terms") or {}))
        except RejectError as e:
            retry = (e.details or {}).get("retry_ms")
            if retry:                     # V104R1-06: the name's inputs are being fetched: 202 + retry_ms, the ticket asks again by itself
                return 202, dict(e.details or {}, status="fetching", pending=True, code=e.code, message=e.message, retry_ms=int(retry)), {}
            if e.http == 404:
                raise ApiError(404, e.code, e.message, e.details)
            if e.http in (400, 409):      # V104R2-BW-05: a correctly refused preview answers 200 with its blocker
                return _refused(aid, e)
            raise

    def cds_desk(req):
        aid = str(req.q("account_id") or "")
        if not aid:
            raise ApiError(400, "ACCOUNT_REQUIRED", "account_id is required")
        try:
            return desk(req.rt, aid)
        except NotFound as e:
            raise _ApiNotFound(e.message, e.details)
        except RejectError as e:
            raise ApiError(400, e.code, e.message, e.details)

    def cds_terms(req):
        return run_command(req, "cds.terms", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    def cds_basis(req):
        return run_command(req, "cds.basis", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    def cds_basis_effect(req):
        try:
            return basis_effect(req.rt, str(req.q("account_id") or ""), req.q("cik"), negotiated_mid_bp=req.q("negotiated_mid_bp"),
                                deal_at_model=req.q("deal_at_model") or False, clear=req.q("clear") or False, ticker=req.q("ticker"))
        except NotFound as e:
            raise ApiError(404, e.code, e.message, e.details)
        except RejectError as e:
            retry = (e.details or {}).get("retry_ms")
            if retry:
                return 202, dict(e.details or {}, status="fetching", pending=True, code=e.code, message=e.message, retry_ms=int(retry)), {}
            if e.http in (400, 409):      # V104R2-BW-05 (B4-R2): a refused effect answers 200 with its reason in words (no 4xx reaches the browser console)
                return {"refused": True, "ok": False, "code": e.code, "message": e.message, "details": e.details or {}}
            raise ApiError(e.http, e.code, e.message, e.details)

    def credit_event(req):
        payload = {k: v for k, v in (req.body or {}).items() if k not in ("request_id", "contract_id")}
        payload["contract_id"] = req.params[0]
        return run_command(req, "cds.credit_event", payload)

    router.add("POST", r"/api/otc/cds/preview", cds_preview, mutating=False)
    router.add("GET", r"/api/otc/cds/desk", cds_desk)
    router.add("POST", r"/api/otc/cds/terms", cds_terms)
    router.add("POST", r"/api/otc/cds/basis", cds_basis)
    router.add("GET", r"/api/otc/cds/basis/effect", cds_basis_effect)
    router.add("POST", r"/api/otc/cds/contracts/([^/]+)/credit-event", credit_event)


def unsupported() -> List[dict]:
    where = "prime-broker OTC tab, Single-name CDS"
    return [{"item": "Single-name CDS dealer quotes", "state": "LIMITED",
             "wording": ("no free single-name CDS quote: MODEL spread, not validated against traded levels; set a negotiated mid per name, or confirm"
                         " dealing at the MODEL"), "where": where},
            {"item": "Single-name CDS on banks, insurers and finance companies", "state": "LIMITED",
             "wording": ("offered with borrowings-only debt: CreditGrades was calibrated for industrial firms (its 2002 document excludes banks and"
                         " finance firms); deposits, repos and policy liabilities are left out of debt - treat the spread with care"), "where": where},
            {"item": "Foreign private issuers, IFRS filers and names with under USD 100M of debt", "state": "NOT_OFFERED",
             "wording": "only US 10-K / 10-Q filers with at least USD 100,000,000 of reported debt (assumed minimum) are offered", "where": where},
            {"item": "Restructuring clauses, subordinated / LCDS, sovereign and municipal CDS, CDS options, tenors other than 1-5 / 7 / 10 years",
             "state": "NOT_OFFERED", "wording": "senior unsecured XR14 contracts of 1, 2, 3, 4, 5, 7 or 10 years only", "where": where},
            {"item": "Real credit events (ISDA Determinations Committee rulings, auctions)", "state": "SIMULATED",
             "wording": "SIMULATED on request - you enter the event and the recovery; no ruling or auction is tracked", "where": "Derivatives page, OTC contracts"},
            {"item": "Reference obligations (the ISDA standard reference obligation list)", "state": "NOT_OFFERED",
             "wording": "the contract references the senior unsecured level; the standard reference obligation list is not freely available", "where": where},
            {"item": "Traded single-name CDS prints", "state": "NOT_OFFERED",
             "wording": "no free source whose terms allow automated use: DTCC's terms prohibit it", "where": where}]


def register(bus, services=None) -> None:
    bus.register("cds.coupon", h_coupon)
    bus.register("cds.terms", h_terms, light=True)
    bus.register("cds.basis", h_basis, light=True)
    bus.register("cds.credit_event", h_credit_event)                 # owner-triggered only (no job runs it)


def _coupon_args(conn, clock, cal, sessions, s):
    return (conn, clock.epoch())


def bind(rt) -> None:
    """The module's tables (``cds_terms``, ``cds_basis``, ``cds_credit_events``; additive) and the CDS_COUPON job before SWAP_VM (a coupon date
    settles before that day's VM)."""
    db = getattr(rt, "db", None)
    if db is not None and hasattr(db, "ensure_schema_ext"):
        db.ensure_schema_ext(SOURCE, SCHEMA)
    try:
        from ..lifecycle_eod import ProductJob, add_product_job
        add_product_job(ProductJob(JOB_COUPON, "cds.coupon", SOURCE, "coupon_keys", ("contract_id", "date"), "each single-name CDS coupon date 00:45 ET",
                                   _coupon_args), before="SWAP_VM")
    except ImportError:
        pass


__all__ = ["KIND", "LABEL", "TENORS", "TIERS", "TIER_OF_BUCKET", "SCHEMA", "CONVENTIONS", "MODEL_VERSION", "CREDIT_EVENT_LABEL", "maturity_for", "roll_of",
           "entity_label", "parse_terms", "seed_terms", "tiers_for", "level_of", "level_words", "default_coupon_bp", "bucket_of_mid", "model_now", "preview",
           "book", "mark", "cs01_c", "dv01_c", "leg_specs", "terminate", "h_coupon", "coupon_keys", "h_credit_event", "credit_events_of", "next_events",
           "contract_detail", "display_fields", "rfq_describe", "h_terms", "h_basis", "basis_effect", "risk_block", "desk", "register_routes", "unsupported",
           "register", "bind"]
