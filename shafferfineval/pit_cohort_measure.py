"""THE DECISIVE COHORT-FORMATION MEASUREMENT. Read-only, stdlib only.

For EVERY peer set in `pit_peer_set` -- all 39,038 of them, across 165 month-end
as-of dates and four SIC fallback rungs -- this counts how many MEMBERS carry
the primitives each Shaffer factor actually needs, and reports the answer as a
DISTRIBUTION rather than as an average. The mean peer set is 31.8 members while
cohort sizes run 12 to 760 at sic4 and 542 to 1,310 at the office rung, so a
single mean would hide the entire result.

    N_EBITDA        operating_income AND depreciation_amortisation at a
                    MATCHED (period_end, qtrs), each non-stale. NO price
                    requirement: this is a fundamental operating benchmark and
                    nothing about price, shares, debt or cash belongs in its
                    eligibility filter.
    N_RevEBITDA     the above AND revenue -- the EBITDA-efficiency cohort.
                    Reported twice: revenue at the SAME period as the EBITDA
                    (a margin is a ratio of one period's numbers) and revenue
                    selected independently.
    N_PriceShares   a resolvable price AND a DEFENSIBLE share count -- the
                    market-cap cohort.
    N_EVEBITDA      price AND shares AND total_debt AND cash AND EBITDA -- the
                    valuation cohort, and the only one of the four that the
                    40%-weighted valuation factor can be computed from.

WHY THIS EXISTS. `build_ebitda_peer_cohort` in the frozen production core
filters peers to those with a USABLE EV/EBITDA before taking percentiles, which
makes a FUNDAMENTAL OPERATING BENCHMARK conditional on price, shares, debt and
cash, and makes `sector_ebitda_p50`/`p75` the PRICED sector's percentiles rather
than the sector's. The owner's correction is that every Shaffer factor gets its
OWN factor-specific peer universe. This measures what each of those universes
would actually contain. It is recorded as SHAFFER_V1_KNOWN_LIMITATION and
implemented only in the candidate lineage; nothing here edits the core.

SAMPLE SCOPE. N_EBITDA and N_RevEBITDA are survivorship-free: they come from
`pit_fact`, whose entities are CIK-keyed and include issuers that are long dead.
N_PriceShares and N_EVEBITDA are SURVIVOR_ONLY_DIAGNOSTIC -- they depend on
`pit_listing`, which holds 2,574 lines, every one alive in 2026, with no series
ending before 2020. Every table below that touches a price is labelled.

    python pit_cohort_measure.py --masks <dir> [--out <dir>]
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_scan
import pit_store

SAMPLE_SCOPE_PRICED = pit_store.SAMPLE_SURVIVOR_ONLY
SAMPLE_SCOPE_FUNDAMENTAL = "FULL_REPORTING_UNIVERSE"

#: The thresholds the production core actually uses. 3 is
#: `company_scoring.MIN_EBITDA_COHORT`; 12 is the 50-75 BAND's requirement,
#: because the band is a quarter of the vector by construction and three members
#: inside it needs twelve in the vector.
THRESHOLDS = (3, 5, 8, 12)
PERCENTILE_POINTS = (10, 25, 50, 75, 90)

#: SIC divisions, by two-digit major group. The SEC's own division list; the
#: peer-set `key` is the SIC prefix at the sic4/sic3/sic2 rungs, so the division
#: is read off the key rather than re-derived from the members.
SIC_DIVISIONS: tuple[tuple[int, int, str], ...] = (
    (1, 9, "A Agriculture, Forestry & Fishing"),
    (10, 14, "B Mining"),
    (15, 17, "C Construction"),
    (20, 39, "D Manufacturing"),
    (40, 49, "E Transport, Comms & Utilities"),
    (50, 51, "F Wholesale Trade"),
    (52, 59, "G Retail Trade"),
    (60, 67, "H Finance, Insurance & Real Estate"),
    (70, 89, "I Services"),
    (91, 99, "J Public Administration"),
)


def division_for(rung: str, key: str) -> str:
    """The SIC division of a peer set, or the office name at the office rung.

    An office is NOT a division and is never mapped onto one: the SEC's
    Corporation Finance offices cut across divisions by design, so forcing them
    into a division would invent a classification the store does not hold.
    """
    if rung == "office":
        return f"(office) {key}"
    try:
        major = int(str(key)[:2])
    except ValueError:
        return "unclassified"
    for low, high, label in SIC_DIVISIONS:
        if low <= major <= high:
            return label
    return "unclassified"


def percentiles(values: Sequence[int]) -> dict[str, Any]:
    """Nearest-rank percentiles, matching `pit_shares._percentiles`.

    Nearest rank and not interpolation: these are MEMBER COUNTS. A median of
    23.5 peers is a cohort no percentile was ever taken over.
    """
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    out: dict[str, Any] = {"n_sets": n, "min": ordered[0], "max": ordered[-1]}
    for point in PERCENTILE_POINTS:
        rank = max(1, min(n, math.ceil(point / 100.0 * n)))
        out[f"p{point}"] = ordered[rank - 1]
    out["median"] = out["p50"]
    out["mean"] = round(sum(ordered) / n, 2)
    for threshold in THRESHOLDS:
        hit = sum(1 for v in ordered if v >= threshold)
        out[f"pct_ge_{threshold}"] = round(100.0 * hit / n, 2)
    return out


def load_inputs(masks_dir: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with open(os.path.join(masks_dir, "fund_masks_annual.json"), encoding="utf-8") as h:
        masks = json.load(h)
    masks["masks"] = {k: {int(e): int(v, 16) for e, v in t.items()}
                      for k, t in masks["masks"].items()}
    with open(os.path.join(masks_dir, "price_shares.json"), encoding="utf-8") as h:
        price = json.load(h)
    return masks, price


def measure(conn: sqlite3.Connection, masks_payload: dict[str, Any],
            price_payload: dict[str, Any],
            model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
            peer_set_version: str = pit_store.PEER_SET_VERSION,
            progress: bool = True) -> dict[str, Any]:
    started = time.time()
    dates = masks_payload["dates"]
    masks = masks_payload["masks"]
    per_date = price_payload["per_date"]

    sets = conn.execute(
        """SELECT peer_set_id, as_of_date, rung, key, n_members, n_price_resolvable
             FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
            ORDER BY as_of_date, peer_set_id""",
        (model_version, peer_set_version)).fetchall()
    by_date: dict[str, list[sqlite3.Row]] = {}
    for row in sets:
        by_date.setdefault(row["as_of_date"], []).append(row)

    records: list[dict[str, Any]] = []
    n_member_rows = 0
    for day, rows in sorted(by_date.items()):
        bit = dates.index(day)
        priced_record = per_date.get(day) or {}
        defensible = set(priced_record.get("defensible", ()))
        priced = set(priced_record.get("priced", ()))

        ids = [int(r["peer_set_id"]) for r in rows]
        lo, hi = min(ids), max(ids)
        wanted = set(ids)
        members: dict[int, list[int]] = {i: [] for i in ids}
        for peer_set_id, entity_id in conn.execute(
                """SELECT peer_set_id, entity_id FROM pit_peer_member
                    WHERE peer_set_id BETWEEN ? AND ?""", (lo, hi)):
            if peer_set_id in wanted:
                members[peer_set_id].append(entity_id)
                n_member_rows += 1

        def has(key: str, entity_id: int) -> bool:
            return bool((masks[key].get(entity_id, 0) >> bit) & 1)

        for row in rows:
            cohort = members[int(row["peer_set_id"])]
            n_ebitda = n_rev_m = n_rev_i = n_ps = n_ev = n_priced = 0
            n_rev = n_cash = n_debt = n_pe = 0
            for entity_id in cohort:
                ebitda = has("ebitda_matched", entity_id)
                if ebitda:
                    n_ebitda += 1
                if has("ebitda_revenue_matched", entity_id):
                    n_rev_m += 1
                if has("ebitda_revenue_independent", entity_id):
                    n_rev_i += 1
                if has("revenue", entity_id):
                    n_rev += 1
                cash = has("cash", entity_id)
                debt = has("total_debt", entity_id)
                n_cash += cash
                n_debt += debt
                if entity_id in priced:
                    n_priced += 1
                    # P/E needs price + a point-in-time EPS and NO share count.
                    # EPS is not in this store (37 tags, none EPS), so the
                    # EPS-shaped leaf is stood in for by the net_income ladder,
                    # which is filed on the same periods by the same filers.
                    # Labelled PE_PROXY everywhere it is reported.
                    if has("net_income", entity_id):
                        n_pe += 1
                if entity_id in defensible:
                    n_ps += 1
                    if ebitda and cash and debt:
                        n_ev += 1
            records.append({
                "peer_set_id": int(row["peer_set_id"]), "as_of": day,
                "year": day[:4], "rung": row["rung"], "key": row["key"],
                "division": division_for(row["rung"], row["key"]),
                "n_members": len(cohort),
                "n_members_declared": int(row["n_members"]),
                "n_price_resolvable_declared": int(row["n_price_resolvable"]),
                "N_EBITDA": n_ebitda, "N_RevEBITDA": n_rev_m,
                "N_RevEBITDA_indep": n_rev_i, "N_Revenue": n_rev,
                "N_Cash": n_cash, "N_Debt": n_debt,
                "N_Priced": n_priced, "N_PriceShares": n_ps, "N_EVEBITDA": n_ev,
                "N_PE_proxy": n_pe,
            })
        if progress and (bit % 20 == 0 or bit == len(dates) - 1):
            print(f"  {day}: {len(rows)} sets, {time.time() - started:.0f}s",
                  flush=True)

    return {"records": records, "n_peer_sets": len(records),
            "n_member_rows": n_member_rows,
            "elapsed_seconds": round(time.time() - started, 1)}


METRICS = ("n_members", "N_EBITDA", "N_RevEBITDA", "N_PriceShares", "N_EVEBITDA",
           "N_PE_proxy")


def group(records: Sequence[dict[str, Any]], field: str,
          metrics: Sequence[str] = METRICS) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(str(record[field]), []).append(record)
    return {name: {metric: percentiles([r[metric] for r in rows])
                   for metric in metrics}
            for name, rows in sorted(buckets.items())}


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    masks_dir = os.getcwd()
    out_dir = None
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--masks" and i + 1 < len(argv):
            masks_dir = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        else:
            i += 1
    out_dir = out_dir or masks_dir

    masks_payload, price_payload = load_inputs(masks_dir)
    wal_before = pit_cohort_scan.wal_bytes(db_path)
    conn = pit_cohort_scan.connect_ro(db_path)
    result = measure(conn, masks_payload, price_payload)
    conn.close()
    wal_after = pit_cohort_scan.wal_bytes(db_path)

    records = result["records"]
    summary = {
        "meta": {
            "n_peer_sets": result["n_peer_sets"],
            "n_member_rows": result["n_member_rows"],
            "elapsed_seconds": result["elapsed_seconds"],
            "wal_bytes_before": wal_before, "wal_bytes_after": wal_after,
            "sample_scope_fundamental": SAMPLE_SCOPE_FUNDAMENTAL,
            "sample_scope_priced": SAMPLE_SCOPE_PRICED,
            "priced_metrics": ["N_Priced", "N_PriceShares", "N_EVEBITDA",
                               "N_PE_proxy"],
            "ladder_version": pit_cohort_scan.LADDER,
            "duration_cadence": "annual (qtrs = 4)",
        },
        "overall": {metric: percentiles([r[metric] for r in records])
                    for metric in METRICS + ("N_Revenue", "N_Priced",
                                             "N_RevEBITDA_indep", "N_Cash",
                                             "N_Debt")},
        "by_year": group(records, "year"),
        "by_rung": group(records, "rung"),
        "by_division": group(records, "division"),
    }
    path = os.path.join(out_dir, "cohort_measurement.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "records": records}, handle)
    print(f"{result['n_peer_sets']:,} peer sets, "
          f"{result['n_member_rows']:,} memberships, "
          f"{result['elapsed_seconds']}s; WAL {wal_before:,} -> {wal_after:,}")
    print(f"wrote {path} ({os.path.getsize(path):,} bytes)")
    for metric in METRICS:
        row = summary["overall"][metric]
        print(f"  {metric:14s} min={row['min']:4d} p10={row['p10']:4d} "
              f"p25={row['p25']:4d} med={row['median']:4d} p75={row['p75']:4d} "
              f"p90={row['p90']:4d} max={row['max']:4d}  "
              f">=3 {row['pct_ge_3']:5.1f}%  >=12 {row['pct_ge_12']:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
