"""Render the cohort-formation census as tables. Reads JSON, touches no store.

Distributions, never averages. Every table that involves a price carries the
SURVIVOR_ONLY_DIAGNOSTIC label in its own header rather than in a footnote,
because a reader who copies one table out of this file must copy the label with
it.

    python pit_cohort_report.py --masks <dir>
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_measure

PRICED = {"N_PriceShares", "N_EVEBITDA", "N_Priced", "N_PE_proxy"}

LABELS = {
    "n_members": "peer set",
    "N_EBITDA": "N_EBITDA",
    "N_RevEBITDA": "N_RevEBITDA",
    "N_PriceShares": "N_PriceShares *",
    "N_EVEBITDA": "N_EVEBITDA *",
    "N_PE_proxy": "N_PE (proxy) *",
    "N_Revenue": "N_Revenue",
    "N_Priced": "N_Priced *",
    "N_Cash": "N_Cash",
    "N_Debt": "N_Debt",
    "N_RevEBITDA_indep": "N_RevEBITDA (indep)",
}

DIST_COLS = ("min", "p10", "p25", "median", "p75", "p90", "max")


def dist_table(rows: dict[str, dict[str, Any]], title: str,
               metrics: Sequence[str]) -> list[str]:
    out = [title, "-" * len(title)]
    head = (f"{'metric':22s}" + "".join(f"{c:>7s}" for c in DIST_COLS)
            + f"{'mean':>8s}" + "".join(f"{'>=' + str(t):>8s}"
                                        for t in pit_cohort_measure.THRESHOLDS))
    out.append(head)
    for metric in metrics:
        row = rows.get(metric)
        if not row:
            continue
        line = f"{LABELS.get(metric, metric):22s}"
        line += "".join(f"{row[c]:7d}" for c in DIST_COLS)
        line += f"{row['mean']:8.1f}"
        line += "".join(f"{row['pct_ge_' + str(t)]:7.1f}%"
                        for t in pit_cohort_measure.THRESHOLDS)
        out.append(line)
    return out


def breakdown(groups: dict[str, dict[str, Any]], title: str,
              metrics: Sequence[str], limit: Optional[int] = None,
              sort_by_sets: bool = False) -> list[str]:
    out = [title, "-" * len(title)]
    header = f"{'group':38s}{'sets':>7s}{'peers med':>10s}"
    for metric in metrics:
        header += f"{LABELS.get(metric, metric)[:13]:>15s}"
    out.append(header)
    out.append(f"{'':38s}{'':>7s}{'':>10s}" +
               "".join(f"{'med  >=3  >=12':>15s}" for _ in metrics))
    names = list(groups)
    if sort_by_sets:
        names.sort(key=lambda n: -groups[n]["n_members"]["n_sets"])
    if limit:
        names = names[:limit]
    for name in names:
        block = groups[name]
        line = f"{name[:37]:38s}{block['n_members']['n_sets']:7d}"
        line += f"{block['n_members']['median']:10d}"
        for metric in metrics:
            row = block[metric]
            line += f"{row['median']:6d}{row['pct_ge_3']:5.0f}{row['pct_ge_12']:5.0f}"
        out.append(line)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    masks_dir = os.getcwd()
    i = 0
    while i < len(argv):
        if argv[i] == "--masks" and i + 1 < len(argv):
            masks_dir = argv[i + 1]; i += 2
        else:
            i += 1
    with open(os.path.join(masks_dir, "cohort_measurement.json"),
              encoding="utf-8") as handle:
        payload = json.load(handle)
    summary = payload["summary"]
    meta = summary["meta"]

    lines: list[str] = []
    lines.append("COHORT FORMATION CENSUS -- every peer set in the store")
    lines.append(f"  {meta['n_peer_sets']:,} peer sets, {meta['n_member_rows']:,} "
                 f"memberships, 165 month-end as-of dates, 4 SIC rungs")
    lines.append(f"  ladder {meta['ladder_version']}; duration facts at "
                 f"{meta['duration_cadence']}")
    lines.append(f"  * = {meta['sample_scope_priced']} "
                 f"({', '.join(meta['priced_metrics'])}); everything else is "
                 f"{meta['sample_scope_fundamental']}")
    lines.append("")
    lines += dist_table(summary["overall"], "ALL 39,038 PEER SETS",
                        ("n_members", "N_Revenue", "N_EBITDA", "N_RevEBITDA",
                         "N_Cash", "N_Debt", "N_Priced", "N_PriceShares",
                         "N_EVEBITDA", "N_PE_proxy"))
    lines.append("")
    core = ("N_EBITDA", "N_RevEBITDA", "N_PriceShares", "N_EVEBITDA", "N_PE_proxy")
    lines += breakdown(summary["by_rung"], "BY SIC FALLBACK RUNG", core)
    lines.append("")
    lines += breakdown(summary["by_year"], "BY YEAR", core)
    lines.append("")
    lines += breakdown(summary["by_division"], "BY SECTOR (SIC division; office "
                       "rung sets keep their office name)", core, sort_by_sets=True)
    text = "\n".join(lines)
    print(text)
    path = os.path.join(masks_dir, "cohort_report.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
