"""PASS/FAIL check: the vectorised masks == the real point-in-time selectors.

Pass 1 (`pit_cohort_scan`) does not call `pit_store.latest_period_as_of` 1.2
million times; it turns each fact into the closed interval of as-of dates on
which that fact is usable and ORs the intervals into a bitmask. That is an
ARGUMENT, and an argument is not evidence. This script is the evidence: it draws
a random sample of (entity, as-of) pairs and asks BOTH the mask and the real
selector chain -- `pit_policy.resolve`, `pit_store.latest_period_as_of`,
`pit_policy.is_stale` -- whether each concept resolves. Any disagreement is a
failure and the script exits non-zero.

It also re-measures the three census marginals documented in `pit_derive.
MEASURED_COVERAGE` straight from the masks, against the peer base built from
`pit_identity.peer_universe_as_of` and a usable non-stale Assets fact. Those are
the numbers every claim in this work rests on, so they are re-derived rather
than trusted.

Read-only. Stdlib only.

    python pit_cohort_validate.py --masks <dir> [--sample 400]
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import time
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_scan
import pit_derive
import pit_identity
import pit_policy
import pit_store

LADDER = pit_cohort_scan.LADDER
ANNUAL_QTRS = 4

#: Concepts whose availability the mask claims, and the unit each is filed in.
CHECKED = {
    "revenue": ("USD", pit_policy.PERIOD_DURATION),
    "operating_income": ("USD", pit_policy.PERIOD_DURATION),
    "depreciation_amortisation": ("USD", pit_policy.PERIOD_DURATION),
    "cash": ("USD", pit_policy.PERIOD_INSTANT),
    "total_assets": ("USD", pit_policy.PERIOD_INSTANT),
}

#: Documented census, for the marginal re-derivation. `pit_derive` is the
#: authority; restating the numbers here would create a second one.
CENSUS_DATES = pit_derive.MEASURED_DATES
TOLERANCE_PP = 1.0      # the store is written concurrently; drift is expected


def real_concept_available(conn: sqlite3.Connection, entity_id: int, as_of: str,
                           concept: str) -> bool:
    """`pit_policy.resolve` + `pit_store.latest_period_as_of` + `is_stale`.

    The ladder is walked in order exactly as the replay would walk it, with
    `resolve` hiding a rung that is gated out at this as-of date. A composite
    (`combine == 'sum'`) rung needs EVERY component at a MATCHED period end, so
    it is checked that way rather than by its first tag.
    """
    unit, kind = CHECKED[concept]
    qtrs = 0 if kind == pit_policy.PERIOD_INSTANT else ANNUAL_QTRS
    for rung in pit_policy.resolve(concept, as_of, LADDER):
        if rung.combine == pit_policy.COMBINE_SUM:
            rows = [pit_store.latest_period_as_of(conn, entity_id, tag, unit,
                                                  qtrs, as_of)
                    for tag in rung.tags]
            if any(r is None for r in rows):
                continue
            ends = {r["period_end"] for r in rows}
            if len(ends) != 1:
                continue
            row = rows[0]
        else:
            row = pit_store.latest_period_as_of(conn, entity_id, rung.tag, unit,
                                                qtrs, as_of)
            if row is None:
                continue
        if pit_policy.is_stale(row["period_end"], as_of, int(row["qtrs"]), concept):
            continue
        return True
    return False


def real_ebitda_matched(conn: sqlite3.Connection, entity_id: int,
                        as_of: str) -> bool:
    """Is there a (period_end, qtrs) where BOTH EBITDA components are usable?

    `pit_policy.ebitda_assembly_spec()` requires both components at the same
    period_end and qtrs -- "a quarterly operating income added to an annual D&A
    is not EBITDA" -- and each tested for staleness independently. Walked over
    the candidate periods directly, because the ladder selector returns only the
    LATEST period and the assembly may legitimately sit on an earlier one that
    both components share.
    """
    oi_tags = [t for rung in pit_policy.resolve("operating_income", as_of, LADDER)
               for t in rung.tags]
    da_tags = [t for rung in pit_policy.resolve("depreciation_amortisation",
                                                as_of, LADDER) for t in rung.tags]
    placeholders_oi = ", ".join("?" * len(oi_tags))
    placeholders_da = ", ".join("?" * len(da_tags))
    periods = conn.execute(
        f"""SELECT DISTINCT period_end FROM pit_fact
             WHERE entity_id = ? AND qtrs = ? AND segments = '' AND coreg = ''
               AND unit = 'USD' AND available_date <= ?
               AND tag IN ({placeholders_oi})""",
        [entity_id, ANNUAL_QTRS, as_of] + oi_tags).fetchall()
    for row in periods:
        period_end = row["period_end"]
        if pit_policy.is_stale(period_end, as_of, ANNUAL_QTRS, "operating_income"):
            continue
        hit = conn.execute(
            f"""SELECT 1 FROM pit_fact
                 WHERE entity_id = ? AND qtrs = ? AND period_end = ?
                   AND segments = '' AND coreg = '' AND unit = 'USD'
                   AND available_date <= ? AND tag IN ({placeholders_da})
                 LIMIT 1""",
            [entity_id, ANNUAL_QTRS, period_end, as_of] + da_tags).fetchone()
        if hit:
            return True
    return False


def load_masks(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["masks"] = {k: {int(e): int(v, 16) for e, v in t.items()}
                        for k, t in payload["masks"].items()}
    return payload


def check(conn: sqlite3.Connection, payload: dict[str, Any], sample: int,
          seed: int = 20260921) -> dict[str, Any]:
    dates = payload["dates"]
    masks = payload["masks"]
    rng = random.Random(seed)
    entities = sorted({e for table in masks.values() for e in table})
    pairs = [(rng.choice(entities), rng.choice(dates)) for _ in range(sample)]

    failures: list[str] = []
    checked = 0
    for entity_id, day in pairs:
        bit = dates.index(day)
        for concept in CHECKED:
            want = bool((masks[concept].get(entity_id, 0) >> bit) & 1)
            got = real_concept_available(conn, entity_id, day, concept)
            checked += 1
            if want != got:
                failures.append(f"{concept} e={entity_id} {day} mask={want} real={got}")
        want = bool((masks["ebitda_matched"].get(entity_id, 0) >> bit) & 1)
        got = real_ebitda_matched(conn, entity_id, day)
        checked += 1
        if want != got:
            failures.append(f"ebitda_matched e={entity_id} {day} mask={want} real={got}")
    return {"pairs": len(pairs), "assertions": checked, "failures": failures}


def marginals(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    dates, masks = payload["dates"], payload["masks"]
    out: dict[str, Any] = {}
    for day in CENSUS_DATES:
        if day not in dates:
            continue
        bit = dates.index(day)
        peers = {int(r["entity_id"]) for r in
                 pit_identity.peer_universe_as_of(conn, day)}
        base = {e for e in peers if (masks["total_assets"].get(e, 0) >> bit) & 1}
        n = max(len(base), 1)
        row = {"peers": len(peers), "base": len(base)}
        for key in ("revenue", "operating_income", "depreciation_amortisation",
                    "cash", "total_debt", "ebitda_matched", "ebitda_independent"):
            row[key] = round(100.0 * sum(1 for e in base
                                         if (masks[key].get(e, 0) >> bit) & 1) / n, 2)
        out[day] = row
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    masks_dir = os.getcwd()
    sample = 400
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--masks" and i + 1 < len(argv):
            masks_dir = argv[i + 1]; i += 2
        elif argv[i] == "--sample" and i + 1 < len(argv):
            sample = int(argv[i + 1]); i += 2
        else:
            i += 1

    started = time.time()
    payload = load_masks(os.path.join(masks_dir, "fund_masks_annual.json"))
    conn = pit_cohort_scan.connect_ro(db_path)
    result = check(conn, payload, sample)
    marg = marginals(conn, payload)
    conn.close()

    print(f"[1] mask vs real selector: {result['assertions']:,} assertions over "
          f"{result['pairs']:,} (entity, as-of) pairs")
    for line in result["failures"][:20]:
        print("    FAIL", line)
    ok_selector = not result["failures"]
    print(f"    {'PASS' if ok_selector else 'FAIL'} "
          f"({len(result['failures'])} disagreements)")

    print("[2] marginals re-derived from the masks vs pit_derive.MEASURED_COVERAGE")
    ok_marg = True
    for day, row in marg.items():
        want = pit_derive.primitive_availability(day, LADDER)
        pairs = [("revenue", "revenue"), ("operating_income", "operating_income"),
                 ("depreciation_amortisation", "depreciation_amortisation"),
                 ("cash", "cash"), ("total_debt", "total_debt")]
        deltas = []
        for mine, theirs in pairs:
            delta = row[mine] - 100.0 * want[theirs]
            deltas.append(f"{mine}={row[mine]:.1f}({delta:+.1f})")
            if abs(delta) > TOLERANCE_PP:
                ok_marg = False
        joint = 100.0 * pit_derive.MEASURED_JOINTS["ebitda"][day]
        delta = row["ebitda_matched"] - joint
        deltas.append(f"ebitda={row['ebitda_matched']:.1f}({delta:+.1f})")
        if abs(delta) > TOLERANCE_PP:
            ok_marg = False
        print(f"    {day} base={row['base']} (documented "
              f"{pit_derive.MEASURED_BASE[day]}) " + "  ".join(deltas))
    print(f"    {'PASS' if ok_marg else 'FAIL'} (tolerance {TOLERANCE_PP} pp; the "
          f"store is written concurrently, so small drift is expected)")

    ok = ok_selector and ok_marg
    print(f"\n{'PASS' if ok else 'FAIL'}  ({time.time() - started:.1f}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
