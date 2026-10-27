"""The counterparty directory of the Credit Instruments marketplace (Build 2 P08; owner 2026-10-21: "why isnt there
counterparties for credit instruments?"): real institutions with ASSUMED, editable terms, the exposure per counterparty
(contract C20) and the cross-product credit routes (/api/credit/counterparties, /api/credit/exposure, /api/credit/rates).

Conventions stated here (each is a sentence the screen shows or a rule the tests pin)
- DIRECTORY: ``data/counterparties.json`` lists the banks that lend currency (a spread over each currency's policy index, a
  maximum per loan in USD and the tenors offered), the New York Fed primary dealers for repo / reverse repo (the list as read
  from newyorkfed.org on 2026-10-21: 25 dealers after NatWest's withdrawal of 2026-10-16) with a spread to SOFR each way and a
  haircut table, and the agent lenders of securities lending (the owner's share of the lending fee, the rebate formula).
  Prime brokers come from the PB schedules (the book's own copy when the account is open) and private-credit borrowers from
  the private company master.  Every term is an ASSUMED model input labelled "assumed terms" wherever it is shown; nothing is a
  quote, an offer or a credit line from the institution.
- TERMS: the JSON is the seed; the owner's edits live in ``settings.counterparty_terms`` ``{id: {section: {field: value}}}``,
  written by the light command ``counterparties.update_terms`` (validated field by field, one ``SETTINGS_CHANGED`` event with
  before / after; ``reset: true`` drops the counterparty's edits).  Rates are decimal FRACTIONS ("0.0060" = 0.60 %).
- EXPOSURE (C20): per counterparty of one lifetime, in USD at the snapshot's FX: ``exposure_c`` = what the counterparty owes the
  book or holds of it (cash lent in a reverse repo with its accrued interest, the bonds pledged in a repo at their market value,
  shares lent at their market value, the cash collateral posted on stock borrows, a private loan's mark plus accrued coupon,
  the assets held at a prime broker); ``collateral_c`` = what the book holds of the counterparty's (the dealer's bonds in a
  reverse repo, the cash collateral received on lent shares, the cash a bank, dealer or prime broker lent the book, the shares
  borrowed); ``net_c = exposure_c - collateral_c`` (positive: the book's unsecured exposure; negative: the counterparty is
  exposed to the book).  A figure that cannot be valued (no mark, no FX rate) is left out of the sums and named in ``missing``.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .commands import RejectError, TxContext
from .db import get_setting, set_setting
from .units import fmt_money, from_c, fx_c, qprice, to_c

SETTING = "counterparty_terms"
TYPES = ("BANK", "REPO_DEALER", "AGENT_LENDER", "PRIME_BROKER", "PRIVATE_BORROWER")
TYPE_LABELS = {"BANK": "bank (currency loans)", "REPO_DEALER": "primary dealer (repo / reverse repo)", "AGENT_LENDER": "agent lender (securities lending)",
               "PRIME_BROKER": "prime broker", "PRIVATE_BORROWER": "private-credit borrower"}
ASSUMED = "assumed terms"
HOUSE_LENDER_ID = "HOUSE_MM"
#: field specs of the editable sections: (kind, low, high) - "rate" fractions, "money" whole USD, "tenors" a subset
FIELD_SPECS = {
    "loan": {"spreads": ("rate_map", Decimal("0"), Decimal("0.10")), "max_usd": ("money", Decimal("0"), Decimal("100000000000")), "tenors": ("tenors", None, None)},
    "repo": {"repo_spread": ("rate", Decimal("-0.01"), Decimal("0.05")), "reverse_spread": ("rate", Decimal("-0.01"), Decimal("0.05")),
             "haircuts": ("rate_map", Decimal("0"), Decimal("0.50")), "max_usd": ("money", Decimal("0"), Decimal("100000000000"))},
    "lending": {"owner_split": ("rate", Decimal("0"), Decimal("1")), "rebate_spread": ("rate", Decimal("-0.05"), Decimal("0.05"))},
}
FIELD_LABELS = {"spreads": "spread over the policy index", "max_usd": "maximum (USD)", "tenors": "tenors", "repo_spread": "repo spread over SOFR",
                "reverse_spread": "reverse-repo spread under SOFR", "haircuts": "haircuts", "owner_split": "your share of the lending fee",
                "rebate_spread": "rebate spread to SOFR"}

_SEED_LOCK = threading.Lock()
_SEED: Optional[dict] = None


# ---- the seed ---------------------------------------------------------------------------------------------------------------------
def seed_path() -> str:
    return os.path.join(config.package_dir(), "data", "counterparties.json")


def seed() -> dict:
    """The parsed ``data/counterparties.json`` (read once per process)."""
    global _SEED
    with _SEED_LOCK:
        if _SEED is None:
            with open(seed_path(), "r", encoding="utf-8") as fh:
                _SEED = json.load(fh)
        return _SEED


def defaults() -> dict:
    return dict(seed().get("defaults") or {})


def haircut_defaults() -> Dict[str, str]:
    return dict(defaults().get("haircuts") or {})


def tenor_days(label: Optional[str], table: str = "loan_tenors") -> Optional[int]:
    """``1W`` -> 7 (``OPEN`` / ``ON`` / None -> None for loans; ``ON`` -> 1 for repo terms)."""
    t = defaults().get(table) or {}
    if label is None:
        return None
    if str(label).upper() not in t:
        raise RejectError("TENOR", f"tenor {label!r} is not one of {', '.join(t)}")
    v = t[str(label).upper()]
    return None if v is None else int(v)


def _seed_rows() -> List[dict]:
    return list(seed().get("counterparties") or [])


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def overrides(conn) -> dict:
    v = get_setting(conn, SETTING, None)
    return dict(v) if isinstance(v, dict) else {}


def _reader(services, conn=None):
    from .marks import shared_reader
    return shared_reader(services.db, conn)


# ---- the directory ------------------------------------------------------------------------------------------------------------------
def _entry(row: dict, over: dict) -> dict:
    terms = _deep_merge(row.get("terms") or {}, over.get(row["id"]) or {})
    return {"counterparty_id": row["id"], "type": row["type"], "type_label": TYPE_LABELS.get(row["type"], row["type"]), "name": row["name"],
            "label": f"{row['name']} ({ASSUMED})", "country": row.get("country"), "agreement": row.get("agreement"), "terms": terms, "assumed": True,
            "edited": bool(over.get(row["id"])), "edited_fields": sorted((over.get(row["id"]) or {}).keys()), "source": (seed().get("sources") or {}).get(row["type"])}


def _pb_entries(conn, lifetime_id: Optional[str]) -> List[dict]:
    """The prime brokers: the seed schedules, or the lifetime's own copy when its PB account is open."""
    from . import tables
    out = []
    open_rows = {}
    if lifetime_id:
        for r in conn.execute("SELECT a.account_id, a.broker, s.schedule_json FROM accounts a LEFT JOIN pb_schedules s ON s.account_id=a.account_id"
                              " WHERE a.kind='PB' AND a.lifetime_id=? AND a.status='OPEN'", (lifetime_id,)).fetchall():
            open_rows[str(r["broker"] or "")] = r
    for key, sched in tables.PB_SCHEDULES.items():
        live = open_rows.get(key)
        terms = {"financing_spread": str(sched["financing_spread"]), "gc_rate": str(sched["gc_rate"]), "htb_add": str(sched["htb_add"]),
                 "credit_interest_spread": str(sched["credit_interest_spread"])}
        if live is not None and live["schedule_json"]:
            try:
                js = json.loads(live["schedule_json"])
                for k in list(terms):
                    if js.get(k) is not None:
                        terms[k] = str(js[k])
            except ValueError:
                pass
        out.append({"counterparty_id": f"PB_{key}", "type": "PRIME_BROKER", "type_label": TYPE_LABELS["PRIME_BROKER"], "name": str(sched["name"]),
                    "label": f"{sched['name']} (prime broker, {ASSUMED})", "country": None, "agreement": "ISDA Master Agreement + CSA + Prime Brokerage Agreement",
                    "terms": {"pb": terms}, "assumed": True, "edited": False, "edited_fields": [], "account_id": live["account_id"] if live is not None else None,
                    "open": live is not None, "source": (seed().get("sources") or {}).get("PRIME_BROKER"),
                    "edit_where": "Settings > prime brokers (the schedule of an open prime-broker account)"})
    return out


def _borrower_entries(conn, services=None) -> List[dict]:
    """Private-credit borrowers: the private company master (company_id linked) - the companies that are still private names
    (``private.companies``: not LISTED / ACQUIRED; else the master's PRIVATE / FILED_IPO rows), plus any company a private loan
    of any book references."""
    out = []
    try:
        rows = conn.execute("SELECT company_id, name, status, sector, country FROM private_companies ORDER BY name").fetchall()
    except Exception:   # noqa: BLE001 - a database without the private master: no borrowers listed
        return out
    lent = set()
    try:
        lent = {r[0] for r in conn.execute("SELECT DISTINCT company_id FROM private_loans WHERE company_id IS NOT NULL").fetchall()}
    except Exception:   # noqa: BLE001
        pass
    private_names = None
    priv = getattr(services, "private", None) if services is not None else None
    if priv is not None and hasattr(priv, "companies"):
        try:
            private_names = {c["company_id"] for c in priv.companies(services, conn=conn)}
        except Exception:   # noqa: BLE001 - fall back to the master's status column
            private_names = None
    for r in rows:
        still_private = (r["company_id"] in private_names) if private_names is not None else str(r["status"] or "").upper() in ("PRIVATE", "FILED_IPO")
        if not still_private and r["company_id"] not in lent:
            continue
        out.append({"counterparty_id": f"PRV:{r['company_id']}", "company_id": r["company_id"], "type": "PRIVATE_BORROWER", "type_label": TYPE_LABELS["PRIVATE_BORROWER"],
                    "name": r["name"], "label": r["name"], "country": r["country"], "sector": r["sector"], "status": r["status"],
                    "agreement": "credit agreement (direct loan)", "terms": {}, "assumed": False, "edited": False, "edited_fields": [],
                    "source": (seed().get("sources") or {}).get("PRIVATE_BORROWER")})
    return out


def directory(services, conn=None, types: Optional[List[str]] = None, lifetime_id: Optional[str] = None) -> List[dict]:
    """Every counterparty (or the given types), seed terms merged with the owner's edits, in directory order."""
    want = set(t.upper() for t in types) if types else None
    with _reader(services, conn) as c:
        over = overrides(c)
        out = [_entry(r, over) for r in _seed_rows() if want is None or r["type"] in want]
        if want is None or "PRIME_BROKER" in want:
            out += _pb_entries(c, lifetime_id)
        if want is None or "PRIVATE_BORROWER" in want:
            out += _borrower_entries(c, services)
    return out


def get(services, counterparty_id: str, conn=None) -> Optional[dict]:
    cid = str(counterparty_id or "").strip()
    if not cid:
        return None
    with _reader(services, conn) as c:
        over = overrides(c)
        for r in _seed_rows():
            if r["id"] == cid:
                return _entry(r, over)
        if cid.startswith("PB_"):
            return next((e for e in _pb_entries(c, None) if e["counterparty_id"] == cid), None)
        if cid.startswith("PRV:"):
            return next((e for e in _borrower_entries(c, services) if e["counterparty_id"] == cid), None)
    return None


def require(services, counterparty_id: str, kind: str, conn=None) -> dict:
    """The counterparty of ``kind`` (BANK / REPO_DEALER / AGENT_LENDER) or 400 COUNTERPARTY_UNKNOWN / COUNTERPARTY_KIND."""
    e = get(services, counterparty_id, conn)
    if e is None:
        raise RejectError("COUNTERPARTY_UNKNOWN", f"counterparty {counterparty_id!r} is not in the directory", {"counterparty_id": counterparty_id})
    if e["type"] != kind:
        what = {"BANK": "a bank that lends currency", "REPO_DEALER": "a repo dealer", "AGENT_LENDER": "an agent lender"}.get(kind, kind)
        raise RejectError("COUNTERPARTY_KIND", f"{e['name']} is a {e['type_label']}, not {what}", {"counterparty_id": counterparty_id, "type": e["type"]})
    return e


def _dec(v, what: str) -> Decimal:
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError, TypeError) as e:
        raise RejectError("COUNTERPARTY_TERMS", f"{what} is not a number: {v!r}") from e
    if not d.is_finite():
        raise RejectError("COUNTERPARTY_TERMS", f"{what} is not a finite number: {v!r}")
    return d


def loan_terms(services, counterparty_id: str, ccy: str, conn=None) -> dict:
    """``{counterparty_id, name, label, spread, max_usd, tenors}`` of a bank for ``ccy`` (400 CURRENCY_NOT_LENT when the
    bank's terms do not lend that currency)."""
    e = require(services, counterparty_id, "BANK", conn)
    lt = (e["terms"] or {}).get("loan") or {}
    spreads = lt.get("spreads") or {}
    if ccy not in spreads:
        raise RejectError("CURRENCY_NOT_LENT", f"{e['label']} does not lend {ccy} in its terms (lends {', '.join(sorted(spreads))}); pick another bank or edit its terms",
                          {"counterparty_id": counterparty_id, "currency": ccy, "lends": sorted(spreads)})
    return {"counterparty_id": e["counterparty_id"], "name": e["name"], "label": e["label"], "spread": qprice(_dec(spreads[ccy], "spread")),
            "max_usd": _dec(lt.get("max_usd") or "0", "max_usd"), "tenors": list(lt.get("tenors") or []), "agreement": e.get("agreement")}


def repo_terms(services, counterparty_id: str, conn=None) -> dict:
    e = require(services, counterparty_id, "REPO_DEALER", conn)
    rt = (e["terms"] or {}).get("repo") or {}
    hc = dict(haircut_defaults())
    hc.update({k: str(v) for k, v in (rt.get("haircuts") or {}).items()})
    return {"counterparty_id": e["counterparty_id"], "name": e["name"], "label": e["label"], "agreement": e.get("agreement"),
            "repo_spread": qprice(_dec(rt.get("repo_spread", "0.0010"), "repo_spread")), "reverse_spread": qprice(_dec(rt.get("reverse_spread", "0.0005"), "reverse_spread")),
            "haircuts": {k: qprice(_dec(v, "haircut")) for k, v in hc.items()}, "max_usd": _dec(rt.get("max_usd") or "0", "max_usd")}


def lending_terms(services, counterparty_id: str, conn=None) -> dict:
    e = require(services, counterparty_id, "AGENT_LENDER", conn)
    lt = (e["terms"] or {}).get("lending") or {}
    return {"counterparty_id": e["counterparty_id"], "name": e["name"], "label": e["label"], "agreement": e.get("agreement"),
            "owner_split": qprice(_dec(lt.get("owner_split", "0.85"), "owner_split")), "rebate_spread": qprice(_dec(lt.get("rebate_spread", "-0.0050"), "rebate_spread"))}


def default_borrow_lender(services, conn=None) -> Optional[dict]:
    """The agent lender a stock borrow outside a prime broker is sourced from (``defaults.borrow_lender``)."""
    cid = defaults().get("borrow_lender")
    try:
        return lending_terms(services, cid, conn) if cid else None
    except RejectError:
        return None


# ---- terms editing (light command) --------------------------------------------------------------------------------------------------
def _check_value(section: str, field: str, value, low, high, kind) -> Any:
    label = FIELD_LABELS.get(field, field)
    if kind == "rate":
        d = _dec(value, label)
        if d < low or d > high:
            raise RejectError("COUNTERPARTY_TERMS", f"{label} {value} is outside {low} .. {high} (a decimal fraction: 0.0060 = 0.60 %)", {"field": f"{section}.{field}"})
        return str(qprice(d))
    if kind == "money":
        d = _dec(value, label)
        if d < low or d > high or d != d.to_integral_value():
            raise RejectError("COUNTERPARTY_TERMS", f"{label} must be a whole USD amount between {low} and {high:,}", {"field": f"{section}.{field}"})
        return str(int(d))
    if kind == "rate_map":
        if not isinstance(value, dict) or not value:
            raise RejectError("COUNTERPARTY_TERMS", f"{label} must be a map such as {{\"EUR\": \"0.0060\"}}", {"field": f"{section}.{field}"})
        out = {}
        for k, v in value.items():
            key = str(k).upper()
            if section == "loan":
                from . import tables
                if key not in tables.POLICY_INDEX:
                    raise RejectError("COUNTERPARTY_TERMS", f"{key}: loans are lent in {', '.join(sorted(tables.POLICY_INDEX))} only (a policy index is needed)",
                                      {"field": f"{section}.{field}.{key}"})
            elif key not in haircut_defaults():
                raise RejectError("COUNTERPARTY_TERMS", f"haircut type {key} is not one of {', '.join(haircut_defaults())}", {"field": f"{section}.{field}.{key}"})
            if v is None or v == "":
                continue
            d = _dec(v, f"{label} {key}")
            if d < low or d > high:
                raise RejectError("COUNTERPARTY_TERMS", f"{label} {key} {v} is outside {low} .. {high}", {"field": f"{section}.{field}.{key}"})
            out[key] = str(qprice(d))
        return out
    if kind == "tenors":
        allowed = list((defaults().get("loan_tenors") or {}).keys())
        if not isinstance(value, list) or not value or any(str(t).upper() not in allowed for t in value):
            raise RejectError("COUNTERPARTY_TERMS", f"tenors must be a list drawn from {', '.join(allowed)}", {"field": f"{section}.{field}"})
        return [str(t).upper() for t in value]
    raise RejectError("COUNTERPARTY_TERMS", f"{section}.{field} is not editable")


def h_update_terms(ctx: TxContext, payload: dict) -> dict:
    """``counterparties.update_terms {counterparty_id, terms: {section: {field: value}}} | {counterparty_id, reset: true}`` (light)."""
    cid = str(payload.get("counterparty_id") or "").strip()
    row = next((r for r in _seed_rows() if r["id"] == cid), None)
    if row is None:
        if cid.startswith("PB_"):
            raise RejectError("COUNTERPARTY_TERMS", "a prime broker's terms are its schedule: edit it in Settings > prime brokers", {"counterparty_id": cid})
        raise RejectError("COUNTERPARTY_UNKNOWN", f"counterparty {cid!r} is not in the directory (or has no editable terms)", {"counterparty_id": cid})
    conn = ctx.conn
    allover = overrides(conn)
    before = copy.deepcopy(allover.get(cid) or {})
    if payload.get("reset"):
        new = {}
    else:
        terms = payload.get("terms")
        if not isinstance(terms, dict) or not terms:
            raise RejectError("NOTHING_TO_UPDATE", "terms {section: {field: value}} is required (or reset: true)")
        new = copy.deepcopy(before)
        seed_terms = row.get("terms") or {}
        for section, fields in terms.items():
            if section not in FIELD_SPECS or section not in seed_terms:
                raise RejectError("COUNTERPARTY_TERMS", f"{row['name']} has no {section} terms (it is a {TYPE_LABELS.get(row['type'], row['type'])})", {"field": section})
            if not isinstance(fields, dict) or not fields:
                raise RejectError("COUNTERPARTY_TERMS", f"{section} must be a map of fields")
            for field, value in fields.items():
                spec = FIELD_SPECS[section].get(field)
                if spec is None:
                    raise RejectError("COUNTERPARTY_TERMS", f"{section}.{field} is not an editable term (editable: {', '.join(FIELD_SPECS[section])})", {"field": f"{section}.{field}"})
                kind, low, high = spec
                v = _check_value(section, field, value, low, high, kind)
                sec = new.setdefault(section, {})
                if kind == "rate_map":
                    merged = dict(sec.get(field) or {})
                    merged.update(v)
                    seed_map = {k: str(qprice(Decimal(str(x)))) for k, x in ((seed_terms.get(section) or {}).get(field) or {}).items()}
                    merged = {k: x for k, x in merged.items() if seed_map.get(k) != x}
                    if merged:
                        sec[field] = merged
                    else:
                        sec.pop(field, None)
                else:
                    seed_v = (seed_terms.get(section) or {}).get(field)
                    same = seed_v is not None and (str(seed_v) == str(v) if kind != "rate" else qprice(Decimal(str(seed_v))) == qprice(Decimal(v)))
                    if same:
                        sec.pop(field, None)
                    else:
                        sec[field] = v
                if not sec:
                    new.pop(section, None)
    if new:
        allover[cid] = new
    else:
        allover.pop(cid, None)
    set_setting(conn, SETTING, allover)
    ctx.event("SETTINGS_CHANGED", ref_type="settings", ref_id=f"{SETTING}:{cid}", before=before, after=new)
    entry = _entry(row, allover)
    return {"counterparty_id": cid, "before": before, "after": new, "counterparty": entry,
            "note": f"{row['name']}: terms are ASSUMED model inputs; the change applies to new trades (open trades keep the terms they were booked at)"}


# ---- exposure (C20) -------------------------------------------------------------------------------------------------------------------
def _usd(snap, ccy: str, cents: int) -> Optional[int]:
    if ccy == "USD":
        return int(cents)
    try:
        v = snap.fx(ccy)
    except Exception:   # noqa: BLE001
        v = None
    if v is None or getattr(v, "mark", None) is None or getattr(v, "quality", "") == "unavailable":
        return None
    return fx_c(int(cents), v.mark)


def exposure(services, lifetime_id: str, snapshot=None) -> List[dict]:
    """C20: ``[{counterparty_id, name, type, products, exposure_c, collateral_c, net_c, missing}]`` for one lifetime (module
    docstring EXPOSURE), largest |net| first."""
    snap = snapshot if snapshot is not None else services.marks.snapshot()
    rows: Dict[str, dict] = {}
    directory_by_id = {e["counterparty_id"]: e for e in directory(services, types=["BANK", "REPO_DEALER", "AGENT_LENDER"])}

    def row(cid: Optional[str], name: str, kind: str) -> dict:
        key = cid or f"name:{name}"
        if key not in rows:
            e = directory_by_id.get(cid or "")
            rows[key] = {"counterparty_id": cid, "name": e["name"] if e else name, "label": e["label"] if e else name, "type": e["type"] if e else kind,
                         "type_label": TYPE_LABELS.get(e["type"] if e else kind, kind), "products": {}, "exposure_c": 0, "collateral_c": 0, "missing": []}
        return rows[key]

    def add(r: dict, product: str, exp_c: Optional[int], coll_c: Optional[int], what: str) -> None:
        p = r["products"].setdefault(product, {"product": product, "count": 0, "items": []})
        p["count"] += 1
        p["items"].append(what)
        if exp_c is None or coll_c is None:
            r["missing"].append(f"{product} {what}: no mark or FX rate - left out of the sums")
            return
        r["exposure_c"] += int(exp_c)
        r["collateral_c"] += int(coll_c)

    with _reader(services) as conn:
        accts = {a["account_id"]: a for a in (dict(x) for x in conn.execute("SELECT * FROM accounts WHERE lifetime_id=?", (lifetime_id,)).fetchall())}
        ids = list(accts)
        if not ids:
            return []
        q = ",".join("?" for _ in ids)
        # currency loans: the bank lent the book cash (collateral = the cash it lent, principal + accrued)
        credit = getattr(services, "credit", None)
        for ln in conn.execute(f"SELECT * FROM loans WHERE status='OPEN' AND account_id IN ({q}) ORDER BY loan_id", ids).fetchall():
            ln = dict(ln)
            cp = ln.get("counterparty")
            if ln["kind"] == "PB_MARGIN":
                continue                                     # counted with the prime broker below
            acc = credit.accrued_c(conn, ln["loan_id"]) if credit is not None and hasattr(credit, "accrued_c") else 0
            owed = _usd(snap, ln["currency"], int(ln["principal_c"]) + int(acc))
            add(row(cp, ln["lender"], "BANK"), "currency loan", 0, owed, f"{ln['loan_id']} {ln['currency']} {fmt_money(int(ln['principal_c']))}")
        # repo / reverse repo
        repo = getattr(services, "repo", None)
        if repo is not None and hasattr(repo, "exposure_items"):
            for it in repo.exposure_items(services, conn, ids, snap):
                add(row(it["counterparty_id"], it["name"], "REPO_DEALER"), it["product"], it["exposure_c"], it["collateral_c"], it["what"])
        # securities lending out and stock borrows
        lending = getattr(services, "lending", None)
        if lending is not None and hasattr(lending, "exposure_items"):
            for it in lending.exposure_items(services, conn, ids, snap):
                add(row(it["counterparty_id"], it["name"], it.get("type") or "AGENT_LENDER"), it["product"], it["exposure_c"], it["collateral_c"], it["what"])
        # private credit: the borrower owes the loan's mark + accrued coupon
        pc = getattr(services, "private_credit", None)
        try:
            loans = conn.execute(f"SELECT * FROM private_loans WHERE status='OPEN' AND account_id IN ({q}) ORDER BY loan_id", ids).fetchall()
        except Exception:   # noqa: BLE001
            loans = []
        for pl in loans:
            pl = dict(pl)
            mark_c = None
            acc = 0
            if pc is not None and hasattr(pc, "pc_mark"):
                try:
                    mark_c = int(pc.pc_mark(services, pl, conn)[0])
                    acc = int(pc.accrued_c(conn, pl))
                except Exception:   # noqa: BLE001 - an unpriced loan is named, never a number
                    mark_c = None
            cid = f"PRV:{pl['company_id']}" if pl.get("company_id") else None
            r = row(cid, pl["borrower"], "PRIVATE_BORROWER")
            r["type"], r["type_label"] = "PRIVATE_BORROWER", TYPE_LABELS["PRIVATE_BORROWER"]
            add(r, "private credit", None if mark_c is None else _usd(snap, pl["currency"], mark_c + acc), 0,
                f"{pl['loan_id']} {pl['currency']} {fmt_money(int(pl['principal_c']))}")
        # prime brokers: the assets held at the PB against what the book owes the PB
        acc_mod = getattr(services, "accounting", None)
        for aid, a in accts.items():
            if a["kind"] != "PB" or a["status"] != "OPEN" or acc_mod is None:
                continue
            from . import tables
            broker = str(a.get("broker") or "")
            name = (tables.PB_SCHEDULES.get(broker) or {}).get("name") or broker
            try:
                bs = acc_mod.balance_sheet(aid, snap)
                exp_c, coll_c = int(bs.assets_c), int(bs.liabilities_c)
            except Exception:   # noqa: BLE001
                exp_c = coll_c = None
            r = row(f"PB_{broker}", name, "PRIME_BROKER")
            r["type"], r["type_label"], r["label"] = "PRIME_BROKER", TYPE_LABELS["PRIME_BROKER"], f"{name} (prime broker)"
            add(r, "prime brokerage", exp_c, coll_c, f"{aid}: assets held at the prime broker vs owed to it")
    out = []
    for r in rows.values():
        r["net_c"] = int(r["exposure_c"]) - int(r["collateral_c"])
        r["products"] = list(r["products"].values())
        out.append(r)
    out.sort(key=lambda r: (-abs(r["net_c"]), r["name"]))
    return out


def exposure_view(services, lifetime_id: str) -> dict:
    snap = services.marks.snapshot()
    rows = exposure(services, lifetime_id, snap)
    return {"lifetime_id": lifetime_id, "rows": rows, "as_of": int(services.clock.epoch()), "currency": "USD",
            "totals": {"exposure_c": sum(r["exposure_c"] for r in rows), "collateral_c": sum(r["collateral_c"] for r in rows),
                       "net_c": sum(r["net_c"] for r in rows)},
            "note": ("exposure = what the counterparty owes the book or holds of it; collateral = what the book holds of the counterparty's; net = exposure - "
                     "collateral (positive: unsecured exposure to them; negative: they are exposed to the book). USD at the snapshot's FX.")}


# ---- rates view ---------------------------------------------------------------------------------------------------------------------------
def rates_view(services) -> dict:
    """SOFR, the policy indices and the OAS prints with their as-of dates and sources (``RateStore.view``), plus the
    indicative all-in rates the directory's ASSUMED terms give: loans per bank and currency, repo / reverse repo per dealer."""
    from . import tables
    rates = getattr(services, "rates", None)
    view = rates.view() if rates is not None and hasattr(rates, "view") else {}
    sofr = rates.latest("SOFR") if rates is not None and hasattr(rates, "latest") else None
    loans = []
    for e in directory(services, types=["BANK"]):
        lt = (e["terms"] or {}).get("loan") or {}
        for ccy, spread in sorted((lt.get("spreads") or {}).items()):
            sid = tables.POLICY_INDEX.get(ccy)
            obs = rates.latest(sid) if rates is not None and sid else None
            sp = Decimal(str(spread))
            loans.append({"counterparty_id": e["counterparty_id"], "lender": e["label"], "currency": ccy, "index": sid,
                          "index_pct": None if obs is None else str(obs[1]), "index_as_of": None if obs is None else obs[0].isoformat(),
                          "spread_pct": str(qprice(sp * 100)), "all_in_pct": None if obs is None else str(qprice(Decimal(str(obs[1])) + sp * 100)),
                          "monthly_proxy": sid in tables.POLICY_INDEX_MONTHLY})
    repos = []
    for e in directory(services, types=["REPO_DEALER"]):
        t = (e["terms"] or {}).get("repo") or {}
        rs, vs = Decimal(str(t.get("repo_spread", "0"))), Decimal(str(t.get("reverse_spread", "0")))
        repos.append({"counterparty_id": e["counterparty_id"], "dealer": e["label"], "repo_spread_pct": str(qprice(rs * 100)), "reverse_spread_pct": str(qprice(vs * 100)),
                      "repo_pct": None if sofr is None else str(qprice(Decimal(str(sofr[1])) + rs * 100)),
                      "reverse_pct": None if sofr is None else str(qprice(Decimal(str(sofr[1])) - vs * 100))})
    return {"rates": view, "loans": loans, "repo": repos, "sofr": None if sofr is None else {"value_pct": str(sofr[1]), "as_of": sofr[0].isoformat(),
            "source": rates.source("SOFR") if hasattr(rates, "source") else None},
            "note": "index prints as published (percent) with their as-of dates; all-in rates are the index plus the counterparty's ASSUMED spread"}


# ---- wiring -----------------------------------------------------------------------------------------------------------------------------------
def register(bus, services=None) -> None:
    bus.register("counterparties.update_terms", h_update_terms, light=True)


def bind(rt) -> None:
    seed()                                                     # read the directory once at start (a broken file is a bind error in health)


def register_routes(r) -> None:
    from .api import ApiError, ok_or_raise, run_command

    def lifetime_of(req) -> str:
        return req.lifetime()

    def counterparties_get(req):
        rt = req.rt
        types = [t for t in str(req.q("type") or "").upper().split(",") if t]
        bad = [t for t in types if t not in TYPES]
        if bad:
            raise ApiError(400, "BAD_REQUEST", f"type must be among {', '.join(TYPES)}", {"types": list(TYPES)})
        lid = lifetime_of(req)
        rows = directory(rt, types=types or None, lifetime_id=lid)
        s = seed()
        out = {"counterparties": rows, "lifetime_id": lid, "as_of": s.get("as_of"), "note": s.get("note"), "sources": s.get("sources"),
               "types": [{"type": t, "label": TYPE_LABELS[t]} for t in TYPES], "field_labels": FIELD_LABELS, "defaults": defaults()}
        if req.q("exposure") in ("1", "true", "yes"):
            out["exposure"] = exposure_view(rt, lid)
        return out

    def counterparty_terms(req):
        cid = req.params[0]
        payload = {"counterparty_id": cid}
        if req.b("reset"):
            payload["reset"] = True
        else:
            payload["terms"] = req.b("terms")
        res = run_command(req, "counterparties.update_terms", payload)
        return ok_or_raise(res)

    def exposure_get(req):
        return exposure_view(req.rt, lifetime_of(req))

    def rates_get(req):
        return rates_view(req.rt)

    r.add("GET", r"/api/credit/counterparties", counterparties_get)
    r.add("PATCH", r"/api/credit/counterparties/([^/]+)", counterparty_terms)
    r.add("POST", r"/api/credit/counterparties/([^/]+)/terms", counterparty_terms)
    r.add("GET", r"/api/credit/exposure", exposure_get)
    r.add("GET", r"/api/credit/rates", rates_get)


__all__ = ["seed", "defaults", "directory", "get", "require", "loan_terms", "repo_terms", "lending_terms", "default_borrow_lender", "h_update_terms", "exposure",
           "exposure_view", "rates_view", "register", "bind", "register_routes", "TYPES", "TYPE_LABELS", "ASSUMED", "FIELD_SPECS", "tenor_days", "haircut_defaults"]
