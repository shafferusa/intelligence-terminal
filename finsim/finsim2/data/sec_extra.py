"""SEC EDGAR companyfacts — extra concepts for research (the Shaffer vNext Alpha program). Research only: stored in the
alt_data table (dataset ``sec_x``), never in the fundamentals table production scoring reads.

Same rules as sec.py: the LoganTerminal User-Agent, ≤ 10 requests / second (sec.py's process-wide throttle), 10-Q / 10-K
and amendments only, the JSON parsed defensively as untrusted data.

Point in time: every value keeps its FIRST filing date (restatements are ignored — a later correction was not known
at the time), and a value is usable only from that date. Cash-flow items are reported year to date in 10-Qs, so each
flow row keeps its period start and end; ``ttm`` turns them into trailing-twelve-month values as of any date
(FY_prev + YTD − YTD_prev_year), using only filings published by then.

Stored row: (asset_id, period_end, "concept|start", value, filed) — ``start`` is empty for instants.
"""
from __future__ import annotations

import bisect
import datetime as _dt
from typing import Dict, List, Optional, Tuple

from . import num
from .sec import FORMS, _date, _throttle, fetch_companyfacts

DATASET = "sec_x"
FLOWS = {
    "ocf": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")],
    "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"), ("us-gaap", "PaymentsToAcquireProductiveAssets"),
              ("us-gaap", "PaymentsForCapitalImprovements")],
    "buyback": [("us-gaap", "PaymentsForRepurchaseOfCommonStock")],
    "gross_profit": [("us-gaap", "GrossProfit")],
    "op_income": [("us-gaap", "OperatingIncomeLoss")],
    "net_income": [("us-gaap", "NetIncomeLoss")],
    "revenue": [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"), ("us-gaap", "SalesRevenueNet")],
}
INSTANTS = {
    "assets": [("us-gaap", "Assets")],
    "debt": [("us-gaap", "LongTermDebt"), ("us-gaap", "LongTermDebtNoncurrent"), ("us-gaap", "LongTermDebtAndCapitalLeaseObligations")],
    "cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue")],
}


def extract(facts: dict) -> List[Tuple[str, str, float, str]]:
    """[(period_end, field "concept|start", value, first filed)] — first-reported values, tag priority respected."""
    root = facts.get("facts") if isinstance(facts, dict) else None
    if not isinstance(root, dict):
        return []
    best: Dict[Tuple[str, str], Tuple[tuple, Tuple[str, str, float, str]]] = {}
    for concept, tags in list(FLOWS.items()) + list(INSTANTS.items()):
        flow = concept in FLOWS
        for prio, (tax, tag) in enumerate(tags):
            node = root.get(tax)
            node = node.get(tag) if isinstance(node, dict) else None
            units = node.get("units") if isinstance(node, dict) else None
            items = units.get("USD") if isinstance(units, dict) else None
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict) or it.get("form") not in FORMS:
                    continue
                end, filed, val = _date(it.get("end")), _date(it.get("filed")), num(it.get("val"))
                if end is None or filed is None or val is None or filed < end:
                    continue
                start = ""
                if flow:
                    s = _date(it.get("start"))
                    if s is None or not 75 <= (end - s).days <= 380:
                        continue
                    start = s.isoformat()
                field = f"{concept}|{start}"
                key = (field, end.isoformat())
                rank = (prio, filed.isoformat())            # preferred tag first, then the FIRST filing
                row = (end.isoformat(), field, val, filed.isoformat())
                if key not in best or rank < best[key][0]:
                    best[key] = (rank, row)
    return sorted(r for _, r in best.values())


def refresh(store, asset_ids: List[str], progress=None) -> dict:
    """Fetch companyfacts for each equity with a CIK and store the extra concepts. Returns counts."""
    say = progress or (lambda m: None)
    done, rows_n, failed = 0, 0, []
    for a in asset_ids:
        meta = store.asset(a) or {}
        cik = meta.get("cik")
        if not cik:
            continue
        try:
            _throttle()
            facts = fetch_companyfacts(int(cik))
        except Exception as e:  # noqa: BLE001
            failed.append(f"{a}: {type(e).__name__}")
            continue
        rows = extract(facts)
        store.put_alt(DATASET, [(a, end, field, val, filed) for end, field, val, filed in rows])
        done += 1
        rows_n += len(rows)
        say(f"{a}: {len(rows)} rows")
    return {"assets": done, "rows": rows_n, "failed": failed}


# ------------------------------------------------------------------ point-in-time values
class Facts:
    """One asset's extra concepts, queried as of a date (filings published on or before it only)."""

    def __init__(self, rows: List[tuple]):
        self.flows: Dict[str, List[tuple]] = {}
        self.inst: Dict[str, List[tuple]] = {}
        for _a, end, field, val, filed in rows:
            concept, start = field.split("|", 1)
            if start:
                self.flows.setdefault(concept, []).append((filed, end, start, val))
            else:
                self.inst.setdefault(concept, []).append((filed, end, val))
        for d in (self.flows, self.inst):
            for k in d:
                d[k].sort()

    @staticmethod
    def _days(a: str, b: str) -> int:
        return (_dt.date.fromisoformat(a) - _dt.date.fromisoformat(b)).days

    def instant(self, concept: str, date: str, lag_days: int = 0) -> Optional[Tuple[str, float]]:
        """(period_end, value) of the latest balance published by `date` (and ending ≥ lag_days before the latest)."""
        xs = self.inst.get(concept) or []
        k = bisect.bisect_right(xs, (date, "9999", float("inf")))
        vis = xs[:k]
        if not vis:
            return None
        latest = max(vis, key=lambda x: x[1])
        if not lag_days:
            return latest[1], latest[2]
        target = (_dt.date.fromisoformat(latest[1]) - _dt.timedelta(days=lag_days)).isoformat()
        older = [x for x in vis if abs(self._days(x[1], target)) <= 20]
        return (older[-1][1], older[-1][2]) if older else None

    def ttm(self, concept: str, date: str, shift_years: int = 0) -> Optional[Tuple[str, float]]:
        """(period_end, trailing-twelve-month value) as of `date`; shift_years = 1 gives the TTM one year earlier (for
        growth), still using only filings published by `date`."""
        xs = self.flows.get(concept) or []
        k = bisect.bisect_right(xs, (date, "9999", "9999", float("inf")))
        vis = xs[:k]
        if not vis:
            return None
        ends = sorted({x[1] for x in vis}, reverse=True)
        if not ends:
            return None
        e = ends[0]
        if shift_years:
            target = (_dt.date.fromisoformat(e) - _dt.timedelta(days=365 * shift_years)).isoformat()
            near = [x for x in ends if abs(self._days(x, target)) <= 20]
            if not near:
                return None
            e = near[0]
        rows = [x for x in vis if x[1] == e]
        cur = max(rows, key=lambda x: self._days(x[1], x[2]))           # the longest period ending there (YTD / FY)
        dur = self._days(cur[1], cur[2])
        if dur >= 340:
            return e, cur[3]
        fy = [x for x in vis if 340 <= self._days(x[1], x[2]) <= 380 and 0 <= self._days(cur[2], x[1]) <= 20]
        prev = [x for x in vis if abs(self._days(x[1], cur[1]) + 365) <= 20 and abs(self._days(x[1], x[2]) - dur) <= 20]
        if not fy or not prev:
            return None
        return e, fy[-1][3] + cur[3] - prev[-1][3]
