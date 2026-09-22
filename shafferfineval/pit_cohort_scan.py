"""Pass 1 of the cohort-formation census: FUNDAMENTAL availability per entity-date.

READ-ONLY. Opens the store `mode=ro`, sets `busy_timeout`, and holds NO long
read snapshot beyond one table scan -- a reader blocks the WAL checkpointer and
this volume was filled once by a 9.3 GB WAL, so the scan is timed and the WAL
size is sampled before and after.

WHAT THIS COMPUTES, and why it is shaped this way.

The decisive question is a per-PEER-SET count, and a peer set is a set of
entities at one as-of date. So the unit of work is the (entity, as_of) pair --
1.2 million of them across 165 month-end dates -- and evaluating each one with a
separate SQL selector would be several million queries against an 8.1 GiB file.

It is not necessary. A point-in-time fact is USABLE on exactly a CLOSED INTERVAL
of as-of dates:

    usable(as_of)  <=>  available_date <= as_of <= period_end + max_age_months

`available_date` is when the filing landed and `period_end + max_age` is where
`pit_policy.is_stale` retires it. Both ends are properties of the FACT, not of
the as-of date. So one pass over `pit_fact` can turn every fact into an interval
over the 165-date grid and OR it into a per-entity bitmask; a later restatement
of the same period only ever widens the interval leftward, which is exactly what
OR does. The result is identical to calling the selector 1.2 million times, and
`pit_cohort_validate.py` proves that against the real selectors on a sample
rather than asserting it here.

THE MATCHED-PERIOD JOINT IS THE ONE THING A MASK CANNOT DO ALONE. EBITDA is
`operating_income + depreciation_amortisation` at a SHARED (period_end, qtrs) --
`pit_policy.ebitda_assembly_spec()["period_rule"]`: "a quarterly operating income
added to an annual D&A is not EBITDA". So those two concepts (and revenue, for
the matched margin variant) are accumulated per (entity, qtrs, period_end) key
and intersected afterwards. Everything else is a union over ladder rungs and is
OR-ed straight into the mask.

The availability of a derived feature is the JOINT availability of its UNIQUE
primitive leaves -- never a product of intermediate feature coverages. EBITDA
acceleration needs E_t, E_t-1, E_t-2: three observations, not four.

Stdlib only. Writes one JSON file to the caller's --out directory and nothing
to the store.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
import time
from bisect import bisect_left, bisect_right
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_policy
import pit_store

# --------------------------------------------------------------------------
# The concept ladders, read from pit_policy rather than restated.
# --------------------------------------------------------------------------

LADDER = pit_policy.LADDER_VERSION_V2          # total_debt v2; see pit_policy (2b)

#: Duration concepts are read at ANNUAL cadence (qtrs = 4) by default. The
#: Shaffer equity model's revenue, EBITDA and margin are annual quantities, and
#: `--qtrs any` re-runs the whole census at qtrs 1..4 so the choice is a
#: measured sensitivity rather than an assumption.
QTRS_ANNUAL = (4,)
QTRS_ANY = (1, 2, 3, 4)

CONCEPT_TAGS: dict[str, tuple[str, ...]] = {}


def _concept_tags(concept: str) -> tuple[str, ...]:
    return tuple(t for rung in pit_policy.ladder_for(concept, LADDER).rungs
                 for t in rung.tags)


def _gated_tags(concept: str) -> frozenset[str]:
    """Tags whose rung carries a `min_filed_date` gate (the ASC 606 pair)."""
    return frozenset(t for rung in pit_policy.ladder_for(concept, LADDER).rungs
                     if rung.min_filed_date for t in rung.tags)


#: total_debt AVAILABILITY under v2 is the union of its four STANDALONE rungs.
#: The two composite rungs (`SUM(LTDNC+LTDC+STB)` and `SUM(LTDNC+LTDC)`) cannot
#: widen it: both require LongTermDebtNoncurrent, which is a standalone rung of
#: its own, so anything they resolve the single tag resolves too. They decide
#: WHICH rung wins -- and therefore the quantity and its bound -- never whether.
DEBT_STANDALONE = ("DebtLongtermAndShorttermCombinedAmount", "LongTermDebt",
                   "LongTermDebtAndCapitalLeaseObligations",
                   "LongTermDebtNoncurrent")

_EPOCH = _dt.date(2000, 1, 1).toordinal()
_QTRS_BITS = 3
_PERIOD_BITS = 14
_KEY_SHIFT = _QTRS_BITS + _PERIOD_BITS


def _ord(day: str) -> int:
    return _dt.date.fromisoformat(day[:10]).toordinal()


def _key(entity_id: int, qtrs: int, period_ord: int) -> int:
    return (entity_id << _KEY_SHIFT) | (qtrs << _PERIOD_BITS) | (period_ord - _EPOCH)


def _deadline_ord(period_end_ord: int, qtrs: int, concept: Optional[str] = None) -> int:
    """`period_end + max_age_months` as an ordinal -- the last as-of date on
    which `pit_policy.is_stale` still says usable. Month arithmetic, not days:
    `is_stale` clamps the day into the target month and the boundary is
    INCLUSIVE, so the deadline itself is a usable date."""
    end = _dt.date.fromordinal(period_end_ord)
    months = pit_policy.max_age_months(qtrs, concept)
    total = end.month - 1 + months
    year = end.year + total // 12
    month = total % 12 + 1
    if month == 12:
        last = 31
    else:
        last = (_dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(end.day, last)).toordinal()


class Grid:
    """The 165 as-of dates, with prefix/suffix masks for O(1) interval OR."""

    def __init__(self, dates: Sequence[str]):
        self.dates = list(dates)
        self.n = len(self.dates)
        self.ords = [_ord(d) for d in self.dates]
        full = (1 << self.n) - 1
        # suffix[i] = bits i..n-1 set; prefix[i] = bits 0..i-1 set
        self.suffix = [full >> i << i for i in range(self.n + 1)]
        self.prefix = [(1 << i) - 1 for i in range(self.n + 1)]

    def interval(self, avail_ord: int, deadline_ord: int) -> int:
        """Mask of grid dates d with avail <= d <= deadline."""
        lo = bisect_left(self.ords, avail_ord)
        hi = bisect_right(self.ords, deadline_ord)
        if hi <= lo:
            return 0
        return self.suffix[lo] & self.prefix[hi]

    def index_of(self, day: str) -> int:
        return self.dates.index(day[:10])

    def mask_from(self, day: str) -> int:
        """Bits for every grid date >= `day` -- the ASC 606 rung gate."""
        return self.suffix[bisect_left(self.ords, _ord(day))]


def connect_ro(db_path: str, busy_ms: int = 60_000) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    conn.execute("PRAGMA cache_size = -80000")       # 80 MB, this box has 7.5 GiB
    return conn


def wal_bytes(db_path: str) -> int:
    try:
        return os.path.getsize(db_path + "-wal")
    except OSError:
        return 0


def grid_dates(conn: sqlite3.Connection,
               model_version: str = pit_store.EQUITY_PIT_MODEL_VERSION,
               peer_set_version: str = pit_store.PEER_SET_VERSION) -> list[str]:
    return [r[0] for r in conn.execute(
        """SELECT DISTINCT as_of_date FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
            ORDER BY as_of_date""", (model_version, peer_set_version))]


def scan(conn: sqlite3.Connection, grid: Grid, qtrs_allowed: Sequence[int],
         progress: bool = True) -> dict[str, Any]:
    """ONE pass over pit_fact. Returns per-entity bitmasks over the grid.

    Duration concepts are restricted to `qtrs_allowed`; instantaneous concepts
    (cash, total_debt, total_assets) are qtrs = 0 by definition of the ladder's
    `period_kind`, and are read as such rather than by trusting the caller.
    """
    started = time.time()
    duration = set(int(q) for q in qtrs_allowed)

    tag_concept: dict[str, str] = {}
    for concept in ("revenue", "operating_income", "depreciation_amortisation",
                    "cash", "total_assets", "net_income"):
        for tag in _concept_tags(concept):
            tag_concept[tag] = concept
    for tag in DEBT_STANDALONE:
        tag_concept[tag] = "total_debt"
    asc606 = _gated_tags("revenue")
    gate_mask = grid.mask_from(pit_policy.ASC606_FILED_GATE)

    instant = {"cash", "total_assets", "total_debt"}
    period_kind = {c: pit_policy.ladder_for(c, LADDER).period_kind
                   for c in ("revenue", "operating_income",
                             "depreciation_amortisation", "cash", "total_assets",
                             "net_income")}
    period_kind["total_debt"] = pit_policy.PERIOD_INSTANT
    for concept in instant:
        assert period_kind[concept] == pit_policy.PERIOD_INSTANT, concept

    # Masks built incrementally: a union over ladder rungs needs no key table.
    masks: dict[str, dict[int, int]] = {
        "revenue": {}, "revenue_asc606": {}, "cash": {}, "total_debt": {},
        "total_assets": {}, "operating_income": {}, "depreciation_amortisation": {},
        "net_income": {},
    }
    # Key tables, for the MATCHED-period joints only.
    keyed: dict[str, dict[int, int]] = {
        "operating_income": {}, "depreciation_amortisation": {},
        "revenue": {}, "revenue_asc606": {},
    }

    last_ord = grid.ords[-1]
    first_ord = grid.ords[0]
    placeholders = ", ".join("?" * len(tag_concept))
    # unit = 'USD' is NOT cosmetic. Entity 2544 is a 20-F filer reporting in
    # JPY: it carries DepreciationAmortizationAndAccretionNet for every year and
    # the real selector refuses every one of them, because a JPY EBITDA dropped
    # into a USD sector percentile is a 150x error with clean provenance. The
    # selector filters on unit; so does this.
    sql = (f"SELECT entity_id, tag, qtrs, period_end, available_date "
           f"FROM pit_fact WHERE segments = '' AND coreg = '' AND unit = 'USD' "
           f"AND tag IN ({placeholders})")

    n_rows = 0
    n_used = 0
    params = list(tag_concept)
    for entity_id, tag, qtrs, period_end, available in conn.execute(sql, params):
        n_rows += 1
        concept = tag_concept[tag]
        qtrs = int(qtrs)
        if concept in instant:
            if qtrs != 0:
                continue
        elif qtrs not in duration:
            continue
        try:
            avail_o = _ord(available)
            pe_o = _ord(period_end)
        except (ValueError, TypeError):
            continue
        if avail_o > last_ord:
            continue                       # never knowable inside the grid
        dead_o = _deadline_ord(pe_o, qtrs, concept)
        if dead_o < first_ord:
            continue                       # stale before the grid opens
        span = grid.interval(avail_o, dead_o)
        if not span:
            continue
        n_used += 1
        bucket = "revenue_asc606" if tag in asc606 else concept
        table = masks[bucket]
        table[entity_id] = table.get(entity_id, 0) | span
        if bucket in keyed:
            k = _key(entity_id, qtrs, pe_o)
            prev = keyed[bucket].get(k)
            if prev is None or avail_o < prev:
                keyed[bucket][k] = avail_o
        if progress and n_rows % 1_000_000 == 0:
            print(f"    ... {n_rows:,} rows scanned  {time.time() - started:.0f}s",
                  flush=True)

    scan_s = time.time() - started

    # ---- the matched-period joints ----------------------------------------
    oi, da = keyed["operating_income"], keyed["depreciation_amortisation"]
    rev_u, rev_g = keyed["revenue"], keyed["revenue_asc606"]
    ebitda: dict[int, int] = {}
    ebitda_rev: dict[int, int] = {}
    small, large = (oi, da) if len(oi) <= len(da) else (da, oi)
    for k, avail_a in small.items():
        avail_b = large.get(k)
        if avail_b is None:
            continue
        entity_id = k >> _KEY_SHIFT
        qtrs = (k >> _PERIOD_BITS) & ((1 << _QTRS_BITS) - 1)
        pe_o = (k & ((1 << _PERIOD_BITS) - 1)) + _EPOCH
        dead_o = _deadline_ord(pe_o, qtrs, "operating_income")
        span = grid.interval(max(avail_a, avail_b), dead_o)
        if not span:
            continue
        ebitda[entity_id] = ebitda.get(entity_id, 0) | span
        # Revenue at the SAME period, ASC 606 rungs gated to their own window.
        rv = rev_u.get(k)
        rg = rev_g.get(k)
        both = 0
        if rv is not None:
            both |= grid.interval(max(avail_a, avail_b, rv), dead_o)
        if rg is not None:
            both |= grid.interval(max(avail_a, avail_b, rg), dead_o) & gate_mask
        if both:
            ebitda_rev[entity_id] = ebitda_rev.get(entity_id, 0) | both

    revenue = dict(masks["revenue"])
    for entity_id, span in masks["revenue_asc606"].items():
        revenue[entity_id] = revenue.get(entity_id, 0) | (span & gate_mask)

    out = {
        "revenue": revenue,
        "operating_income": masks["operating_income"],
        "depreciation_amortisation": masks["depreciation_amortisation"],
        "cash": masks["cash"],
        "total_debt": masks["total_debt"],
        "total_assets": masks["total_assets"],
        "net_income": masks["net_income"],
        "ebitda_matched": ebitda,
        "ebitda_revenue_matched": ebitda_rev,
    }
    # The independent reading of the same joint, kept beside the matched one so
    # the cost of the period rule is a measured number rather than an opinion.
    indep: dict[int, int] = {}
    for entity_id, span in masks["operating_income"].items():
        other = masks["depreciation_amortisation"].get(entity_id)
        if other:
            indep[entity_id] = span & other
    out["ebitda_independent"] = indep
    indep_rev: dict[int, int] = {}
    for entity_id, span in indep.items():
        other = revenue.get(entity_id)
        if other:
            indep_rev[entity_id] = span & other
    out["ebitda_revenue_independent"] = indep_rev

    return {
        "masks": out,
        "n_rows_scanned": n_rows,
        "n_rows_used": n_used,
        "n_keys_oi": len(oi), "n_keys_da": len(da),
        "n_keys_rev": len(rev_u) + len(rev_g),
        "scan_seconds": round(scan_s, 1),
        "total_seconds": round(time.time() - started, 1),
        "qtrs_allowed": sorted(duration),
        "ladder_version": LADDER,
        "tags": sorted(tag_concept),
    }


def popcount_at(masks: dict[int, int], bit: int,
                universe: Optional[set[int]] = None) -> int:
    if universe is None:
        return sum(1 for m in masks.values() if (m >> bit) & 1)
    return sum(1 for e in universe if (masks.get(e, 0) >> bit) & 1)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    out_dir = os.getcwd()
    qtrs = QTRS_ANNUAL
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--qtrs" and i + 1 < len(argv):
            qtrs = QTRS_ANY if argv[i + 1] == "any" else QTRS_ANNUAL; i += 2
        else:
            i += 1

    wal_before = wal_bytes(db_path)
    conn = connect_ro(db_path)
    dates = grid_dates(conn)
    print(f"grid: {len(dates)} as-of dates {dates[0]} .. {dates[-1]}", flush=True)
    grid = Grid(dates)
    print(f"scanning pit_fact (qtrs={list(qtrs)}, ladder={LADDER}) ...", flush=True)
    result = scan(conn, grid, qtrs)
    conn.close()
    wal_after = wal_bytes(db_path)

    tag = "annual" if tuple(qtrs) == QTRS_ANNUAL else "anyqtr"
    path = os.path.join(out_dir, f"fund_masks_{tag}.json")
    payload = {
        "dates": dates,
        "masks": {k: {str(e): hex(m) for e, m in v.items()}
                  for k, v in result["masks"].items()},
        "meta": {k: v for k, v in result.items() if k != "masks"},
        "wal_bytes_before": wal_before, "wal_bytes_after": wal_after,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    meta = result
    print(f"scanned {meta['n_rows_scanned']:,} rows, used {meta['n_rows_used']:,}"
          f" in {meta['scan_seconds']}s (total {meta['total_seconds']}s)")
    print(f"keys: oi={meta['n_keys_oi']:,} da={meta['n_keys_da']:,} "
          f"rev={meta['n_keys_rev']:,}")
    print(f"WAL {wal_before:,} -> {wal_after:,} bytes")
    print(f"wrote {path} ({os.path.getsize(path):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
