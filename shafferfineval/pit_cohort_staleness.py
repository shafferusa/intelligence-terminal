"""WHAT THE v2 STALENESS RULE DOES TO THE EV/EBITDA COHORT. Read-only, stdlib.

`pit_fact_kind` fixes a staleness rule; this module measures what that fix buys
where it was supposed to matter. The cohort census found the EV/EBITDA
conjunction's BINDING LEAF by leave-one-out over 39,038 peer sets:

    all five leaves                        P(N>=3) = 34.2%
    without the defensible share count     P(N>=3) = 61.6%   <-- the binding leaf
    without total_debt                     P(N>=3) = 51.2%
    without EBITDA                         P(N>=3) = 46.8%
    without cash                           P(N>=3) = 35.0%

So a correct staleness rule for the share count must move P(N>=3), and the
27.4-point gap between 34.2% and 61.6% is the room it has to move in. This
module measures how much of that room v2 actually takes.

==========================================================================
THE METHOD, AND ITS LIMITS
==========================================================================

NOT an estimate and NOT a re-derivation. `pit_cohort_measure.measure()` is
re-run over the SAME 39,038 peer sets, the SAME 165 as-of dates and the SAME
fundamental masks, with exactly ONE input substituted: the per-date set of
entities holding a defensible share count. `pit_fact_kind --sets-dir` writes
that set twice, once under each policy, in `pit_cohort_price`'s own file shape,
so the substitution is a file swap and nothing else changes.

That isolates the share leaf. It also UNDERSTATES v2, because v2 re-kinds
`cash` and `total_debt` as STATE facts too, and the masks those leaves come
from were built under v1's bounds. So the census is run in four combinations:

    v1 shares + v1 fundamentals   the published baseline, reproduced
    v2 shares + v1 fundamentals   THE SHARE-LEAF ANSWER (the isolated effect)
    v1 shares + v2 fundamentals   what cash and total_debt contribute alone
    v2 shares + v2 fundamentals   the whole v2 policy

`--scan-v2` builds the v2 fundamental masks by running `pit_cohort_scan.scan`
with `_deadline_ord` replaced -- a second full pass over `pit_fact`, patched at
the call site rather than edited into the module, so `pit_cohort_scan` itself
stays exactly what produced the published numbers.

LIMITS, stated rather than buried:

  * SURVIVOR_ONLY_DIAGNOSTIC. Every number involving a price or a share count
    is counted over `pit_listing`'s 2,576 lines, all alive in 2026. The 2013
    cross-section it describes is not the cross-section that existed in 2013.
  * The masks are the ANNUAL cadence (qtrs = 4), matching the published census.
  * EBITDA's two components are FLOW facts and v2 leaves them untouched, so the
    EBITDA leaf is identical in all four runs by construction.

    python pit_cohort_staleness.py --scan-v2 --masks <dir>
    python pit_cohort_staleness.py --masks <dir> --sets <dir> --out <dir>
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_measure
import pit_cohort_scan
import pit_fact_kind
import pit_policy
import pit_store

SAMPLE_SCOPE = pit_fact_kind.SAMPLE_SCOPE

#: The two thresholds the brief names. 3 is `company_scoring.MIN_EBITDA_COHORT`;
#: 12 is what the 50-75 band needs, the band being a quarter of the vector.
HEADLINE_THRESHOLDS = (3, 12)

MONTH_NAMES = ["", "january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]


# ==========================================================================
# (1) THE V2 FUNDAMENTAL MASKS -- patched at the call site, never edited in
# ==========================================================================

def v2_deadline_ord(period_end_ord: int, qtrs: int,
                    concept: Optional[str] = None) -> int:
    """`pit_cohort_scan._deadline_ord` under `fact_kind_staleness_v2`.

    Same signature, same return (an ordinal: the last as-of date on which the
    fact is still usable), same clamped month arithmetic. The only difference
    is WHICH month count it adds -- v1's cadence bound for a flow, the state
    sanity ceiling for a state fact.

    A concept this module cannot classify falls back to v1 rather than to the
    permissive branch. Defaulting an unknown concept to `state` would hand a
    two-year persistence to something nobody has argued about.
    """
    import datetime as _dt
    end = _dt.date.fromordinal(period_end_ord)
    try:
        months = pit_fact_kind.max_age_months_v2(concept or "", qtrs)
    except ValueError:
        months = pit_policy.max_age_months(qtrs, concept)
    if months is None:                      # a MARKET fact; no month bound
        months = pit_policy.max_age_months(qtrs, concept)
    total = end.month - 1 + int(months)
    year = end.year + total // 12
    month = total % 12 + 1
    last = 31 if month == 12 else (
        _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(end.day, last)).toordinal()


def build_v2_masks(db_path: str, out_dir: str,
                   qtrs: Sequence[int] = pit_cohort_scan.QTRS_ANNUAL) -> str:
    """One patched pass over `pit_fact`, written as `fund_masks_annual_v2.json`.

    The patch is applied and REMOVED around the call. `pit_cohort_scan` is the
    module that produced the published masks and it must keep producing them.
    """
    disk = pit_fact_kind.disk_check(os.path.dirname(os.path.abspath(db_path)) or ".")
    print(f"disk: {disk['free_gib']} GiB free ({disk['pct_used']}% used)", flush=True)
    if not disk["ok"]:
        raise SystemExit(f"ABORT: under {pit_fact_kind.MIN_FREE_GIB} GiB free")

    wal_before = pit_cohort_scan.wal_bytes(db_path)
    conn = pit_cohort_scan.connect_ro(db_path)
    dates = pit_cohort_scan.grid_dates(conn)
    grid = pit_cohort_scan.Grid(dates)
    original = pit_cohort_scan._deadline_ord
    pit_cohort_scan._deadline_ord = v2_deadline_ord
    try:
        result = pit_cohort_scan.scan(conn, grid, qtrs)
    finally:
        pit_cohort_scan._deadline_ord = original
        conn.close()
    wal_after = pit_cohort_scan.wal_bytes(db_path)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "fund_masks_annual_v2.json")
    payload = {
        "dates": dates,
        "masks": {k: {str(e): hex(m) for e, m in v.items()}
                  for k, v in result["masks"].items()},
        "meta": {k: v for k, v in result.items() if k != "masks"},
        "staleness_policy_version": pit_fact_kind.STALENESS_POLICY_V2,
        "state_sanity_ceiling_months": pit_fact_kind.STATE_SANITY_CEILING_MONTHS,
        "wal_bytes_before": wal_before, "wal_bytes_after": wal_after,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    print(f"wrote {path} ({os.path.getsize(path):,} bytes); scanned "
          f"{result['n_rows_scanned']:,} rows in {result['scan_seconds']}s; "
          f"WAL {wal_before:,} -> {wal_after:,}", flush=True)
    return path


# ==========================================================================
# (2) THE FOUR RUNS
# ==========================================================================

def load_masks(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["masks"] = {k: {int(e): int(v, 16) for e, v in t.items()}
                        for k, t in payload["masks"].items()}
    return payload


def load_sets(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _headline(records: Sequence[Mapping[str, Any]], metric: str) -> dict[str, Any]:
    stats = pit_cohort_measure.percentiles([int(r[metric]) for r in records])
    return {
        "n_peer_sets": stats.get("n_sets", 0),
        "median": stats.get("median"),
        "mean": stats.get("mean"),
        "p75": stats.get("p75"),
        "p90": stats.get("p90"),
        "max": stats.get("max"),
        **{f"pct_ge_{t}": stats.get(f"pct_ge_{t}") for t in HEADLINE_THRESHOLDS},
    }


def by_month(records: Sequence[Mapping[str, Any]], metric: str) -> dict[str, Any]:
    """The headline, split by the calendar month of the as-of date.

    This is where the calendar artefact shows up in the COHORT rather than in
    the share count: the published census measured P(N>=3) at 14.5% in January,
    40.6% in April and 19.1% in October, and a staleness fix that worked must
    flatten that.
    """
    buckets: dict[int, list[Mapping[str, Any]]] = {}
    for record in records:
        buckets.setdefault(int(str(record["as_of"])[5:7]), []).append(record)
    return {MONTH_NAMES[m]: _headline(buckets[m], metric) for m in sorted(buckets)}


def run_one(conn, masks_payload: Mapping[str, Any], sets_payload: Mapping[str, Any],
            label: str) -> dict[str, Any]:
    print(f"  census: {label}", flush=True)
    started = time.time()
    result = pit_cohort_measure.measure(conn, dict(masks_payload),
                                        dict(sets_payload), progress=False)
    records = result["records"]
    out: dict[str, Any] = {
        "label": label,
        "n_peer_sets": result["n_peer_sets"],
        "n_member_rows": result["n_member_rows"],
        "elapsed_seconds": round(time.time() - started, 1),
        "sample_scope": SAMPLE_SCOPE,
    }
    for metric in ("N_EVEBITDA", "N_PriceShares", "N_EBITDA"):
        out[metric] = _headline(records, metric)
    out["N_EVEBITDA_by_month"] = by_month(records, "N_EVEBITDA")
    out["N_EVEBITDA_by_rung"] = {
        rung: _headline([r for r in records if r["rung"] == rung], "N_EVEBITDA")
        for rung in sorted({str(r["rung"]) for r in records})}
    return out


def compare(runs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Before and after, as differences rather than as two tables to eyeball."""
    base = runs.get("v1_shares_v1_fundamentals")
    if base is None:
        return {}
    out: dict[str, Any] = {"baseline": base["label"], "deltas": {}}
    for name, run in runs.items():
        if name == "v1_shares_v1_fundamentals":
            continue
        delta = {}
        for threshold in HEADLINE_THRESHOLDS:
            key = f"pct_ge_{threshold}"
            before = base["N_EVEBITDA"].get(key) or 0.0
            after = run["N_EVEBITDA"].get(key) or 0.0
            delta[key] = {
                "before": before, "after": after,
                "points": round(after - before, 2),
                "ratio": round(after / before, 3) if before else None,
            }
        delta["median"] = {"before": base["N_EVEBITDA"].get("median"),
                           "after": run["N_EVEBITDA"].get("median")}
        months_before = base["N_EVEBITDA_by_month"]
        months_after = run["N_EVEBITDA_by_month"]
        shared = sorted(set(months_before) & set(months_after))
        vals_b = [months_before[m]["pct_ge_3"] or 0.0 for m in shared]
        vals_a = [months_after[m]["pct_ge_3"] or 0.0 for m in shared]
        delta["month_spread_pct_ge_3"] = {
            "before_range_points": round(max(vals_b) - min(vals_b), 2),
            "after_range_points": round(max(vals_a) - min(vals_a), 2),
            "before_worst_month": shared[vals_b.index(min(vals_b))],
            "after_worst_month": shared[vals_a.index(min(vals_a))],
        }
        out["deltas"][name] = delta
    out["how_to_read"] = (
        "v2_shares_v1_fundamentals isolates the share leaf and is the answer to "
        "'what does the staleness fix do to the cohort'. "
        "v2_shares_v2_fundamentals is the whole policy: cash and total_debt are "
        "STATE facts too, so the isolated figure is a LOWER BOUND on v2's full "
        "effect. EBITDA is FLOW and is identical in every run by construction.")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    masks_dir = os.getcwd()
    sets_dir = os.getcwd()
    out_dir = os.getcwd()
    scan_v2 = False
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--masks" and i + 1 < len(argv):
            masks_dir = argv[i + 1]; i += 2
        elif argv[i] == "--sets" and i + 1 < len(argv):
            sets_dir = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--scan-v2":
            scan_v2 = True; i += 1
        else:
            i += 1

    if scan_v2:
        build_v2_masks(db_path, masks_dir)
        return 0

    started = time.time()
    v1_masks = load_masks(os.path.join(masks_dir, "fund_masks_annual.json"))
    v2_masks_path = os.path.join(masks_dir, "fund_masks_annual_v2.json")
    v2_masks = load_masks(v2_masks_path) if os.path.exists(v2_masks_path) else None
    v1_sets = load_sets(os.path.join(sets_dir, "price_shares_v1.json"))
    v2_sets = load_sets(os.path.join(sets_dir, "price_shares_v2.json"))

    conn = pit_cohort_scan.connect_ro(db_path)
    runs: dict[str, Any] = {}
    runs["v1_shares_v1_fundamentals"] = run_one(
        conn, v1_masks, v1_sets, "v1 shares + v1 fundamentals (published baseline)")
    runs["v2_shares_v1_fundamentals"] = run_one(
        conn, v1_masks, v2_sets, "v2 shares + v1 fundamentals (share leaf isolated)")
    if v2_masks is not None:
        runs["v1_shares_v2_fundamentals"] = run_one(
            conn, v2_masks, v1_sets, "v1 shares + v2 fundamentals (cash/debt alone)")
        runs["v2_shares_v2_fundamentals"] = run_one(
            conn, v2_masks, v2_sets, "v2 shares + v2 fundamentals (whole policy)")
    else:
        print("  fund_masks_annual_v2.json absent: the cash/total_debt half of "
              "v2 is UNMEASURED, and is reported as unmeasured, not as zero",
              flush=True)
    conn.close()

    payload = {
        "sample_scope": SAMPLE_SCOPE,
        "staleness_policy": pit_fact_kind.STALENESS_POLICY_V2,
        "state_sanity_ceiling_months": pit_fact_kind.STATE_SANITY_CEILING_MONTHS,
        "v2_fundamental_masks": ("measured" if v2_masks is not None
                                 else "UNMEASURED -- run with --scan-v2"),
        "runs": runs,
        "comparison": compare(runs),
        "elapsed_seconds": round(time.time() - started, 1),
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "cohort_staleness.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
    print(f"wrote {path} ({os.path.getsize(path):,} bytes) in "
          f"{payload['elapsed_seconds']}s", flush=True)
    for name, run in runs.items():
        ev = run["N_EVEBITDA"]
        print(f"  {name:32s} median={ev['median']:>4}  "
              f"P(N>=3)={ev['pct_ge_3']:5.1f}%  P(N>=12)={ev['pct_ge_12']:5.1f}%",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
