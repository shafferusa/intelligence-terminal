"""What recovering EPS would COST, and what it would buy. Read-only. No fetch.

The store holds 37 distinct tags and NONE of them is an EPS tag:
`EarningsPerShareDiluted` and `EarningsPerShareBasic` were never in
`pit_dera.TAG_FILTER`, which is built from the concept ladders, and a tag no
ladder names is dropped at ingest. So P/E is not thin in this store -- it is
ABSENT, and the question is whether to go and get it.

WHY IT MIGHT BE WORTH GETTING. P/E = price / TTM EPS needs a PRICE and an EPS.
It does NOT need a share count, and the share count is where the market-cap
chain dies: a point-in-time count resolves for about half the reporting
universe, the share-class guard refuses a further slice, and EV/EBITDA needs
that count plus debt plus cash plus EBITDA. EPS carries the share division
inside the filed number, so a P/E is one price and one filed figure. That is a
structurally shorter chain, and this module measures how much shorter.

WHAT IT REFUSES TO DO. It starts no fetch, opens no socket and writes nothing to
the store. The bytes-per-row figure is MEASURED by building a throwaway copy of
the `pit_fact` schema in a scratch directory and loading real rows into it --
`dbstat` is not compiled into this interpreter's SQLite, so the alternative was
an estimate, and an estimate of disk on a volume at 96% is not good enough.

The EPS-shaped leaf is stood in for by the `net_income` ladder throughout and
every such number is labelled PROXY. Net income and EPS are filed by the same
filers on the same periods in the same filings, so the proxy is a good one --
and it is a proxy, not a measurement of EPS, because EPS is not here to measure.

    python pit_cohort_eps.py [--out <dir>]
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import random
import shutil
import sqlite3
import sys
import time
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_scan
import pit_identity
import pit_policy
import pit_rawprice
import pit_store

#: What a P/E needs from EDGAR. Diluted leads: it is the denominator a
#: conservative multiple uses and the one nearly every filer reports.
EPS_CONCEPTS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")

#: The SEC ceiling this project holds itself to; `pit_identity` owns the session.
REQUESTS_PER_SECOND = pit_rawprice.SEC_REQUESTS_PER_SECOND

#: Median cached companyconcept response, MEASURED by `pit_rawprice` on the two
#: share concepts (23.1 KiB). Re-used here and labelled: it is calibrated on a
#: share concept, not measured on an EPS concept, and an EPS series carries the
#: same one-entry-per-accession shape.
CONCEPT_BYTES = pit_rawprice.MEASURED_CONCEPT_BYTES

#: The periods a replay on this grid could ever consult. An annual fact is stale
#: 15 months after its period end, so nothing ending before this can back a
#: feature at the first grid date; fetching it would be retained weight with no
#: reader.
RELEVANT_PERIOD_FLOOR = "2011-09-01"

CENSUS_DATES = ("2015-06-30", "2019-06-28", "2024-06-28")

#: What the sign check reports when it is not run. Never 0, never omitted.
UNKNOWN_SIGN = {"unmeasured": "UNKNOWN",
                "why": ("--skip-sign was passed; the share of resolvable "
                        "earnings that are POSITIVE, and so usable as a P/E "
                        "denominator, has not been measured on this run")}


def eps_tags_present(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT tag FROM pit_fact WHERE tag LIKE 'EarningsPerShare%'")]


def net_income_tags() -> list[str]:
    return [t for rung in pit_policy.ladder_for("net_income").rungs
            for t in rung.tags]


def per_entity_periods(conn: sqlite3.Connection) -> dict[int, tuple[int, int]]:
    """ONE pass: per entity, (distinct periods, vintage rows) for net income.

    Two shapes of this function were measured and discarded before this one, and
    both failures are about the same table:

      per-universe chunks   `entity_id IN (...)` AND `tag IN (...)` together
                            defeat the planner; 55 chunked queries each
                            degenerated into a full scan of an 8.1 GiB file.
      SQL GROUP BY          one scan, but the group key includes `qtrs`, which
                            lives only in the row, so SQLite sorts 2.3 million
                            rows through an external temp file. Ten minutes in,
                            it had not finished.

    So the grouping is done HERE, in a set of packed integers, and SQLite is
    asked only to stream the rows in whatever order it likes. That is the same
    trick `pit_cohort_scan` uses and it costs about a minute.
    """
    tags = net_income_tags()
    placeholders = ", ".join("?" * len(tags))
    seen: set[int] = set()
    pairs: dict[int, int] = {}
    rows: dict[int, int] = {}
    epoch = _dt.date(2000, 1, 1).toordinal()
    for entity_id, qtrs, period_end in conn.execute(
            f"""SELECT entity_id, qtrs, period_end FROM pit_fact
                 WHERE tag IN ({placeholders}) AND segments = '' AND coreg = ''
                   AND unit = 'USD' AND period_end >= ?""",
            tags + [RELEVANT_PERIOD_FLOOR]):
        try:
            offset = _dt.date.fromisoformat(period_end[:10]).toordinal() - epoch
        except (TypeError, ValueError):
            continue
        key = (entity_id << 17) | (int(qtrs) << 14) | offset
        rows[entity_id] = rows.get(entity_id, 0) + 1
        if key not in seen:
            seen.add(key)
            pairs[entity_id] = pairs.get(entity_id, 0) + 1
    return {e: (pairs.get(e, 0), n) for e, n in rows.items()}


def scope(periods: dict[int, tuple[int, int]], entity_ids: Sequence[int],
          label: str) -> dict[str, Any]:
    """(cik, period) pairs and vintage rows a targeted EPS fetch would land.

    Measured against the `net_income` ladder over the SAME entities and the same
    period window, because net income is filed on exactly the periods EPS is.
    Two counts, and they are different questions:

      pairs   distinct (entity, qtrs, period_end) -- what a de-duplicated
              "one number per period" table would hold.
      rows    one row per ACCESSION as well, which is what pit_fact stores and
              what point-in-time replay requires: a restatement is a new row,
              never an update, so the vintages ARE the point.
    """
    pairs = rows = entities_with = 0
    for entity_id in entity_ids:
        found = periods.get(entity_id)
        if not found:
            continue
        entities_with += 1
        pairs += found[0]
        rows += found[1]
    requests = len(entity_ids) * len(EPS_CONCEPTS)
    return {
        "universe": label,
        "entities": len(entity_ids),
        "entities_with_a_comparable_filing": entities_with,
        "cik_period_pairs_proxy": pairs,
        "vintage_rows_proxy": rows,
        "eps_concepts": list(EPS_CONCEPTS),
        "requests": requests,
        "requests_per_second": REQUESTS_PER_SECOND,
        "minutes_at_ceiling": round(requests / REQUESTS_PER_SECOND / 60.0, 1),
        "download_bytes_estimated": int(requests * CONCEPT_BYTES),
        "download_gib_estimated": round(requests * CONCEPT_BYTES / 1024 ** 3, 3),
        "retained_rows_estimated": rows * len(EPS_CONCEPTS),
        "retained_pairs_estimated": pairs * len(EPS_CONCEPTS),
    }


def bytes_per_fact_row(conn: sqlite3.Connection, scratch_dir: str,
                       sample: int = 150_000) -> dict[str, Any]:
    """MEASURED bytes per pit_fact row, indexes included.

    Builds the real schema in a throwaway file and loads `sample` real rows into
    it. That is the only honest way to price disk here: `dbstat` is absent, and
    pit_fact carries three indexes -- the nine-column UNIQUE autoindex is the
    expensive one and no back-of-envelope gets it right.
    """
    path = os.path.join(scratch_dir, "_rowsize_probe.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    probe = sqlite3.connect(path)
    probe.executescript("""
        CREATE TABLE pit_fact (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL, taxonomy TEXT NOT NULL, tag TEXT NOT NULL,
            unit TEXT NOT NULL, period_start TEXT, period_end TEXT NOT NULL,
            qtrs INTEGER NOT NULL, segments TEXT NOT NULL DEFAULT '',
            coreg TEXT NOT NULL DEFAULT '', val REAL NOT NULL, accn TEXT NOT NULL,
            form TEXT NOT NULL, filed TEXT NOT NULL, accepted_raw TEXT,
            accepted_eastern TEXT, available_date TEXT NOT NULL,
            latency_policy_version TEXT NOT NULL, source TEXT NOT NULL,
            UNIQUE (entity_id, taxonomy, tag, unit, period_end, qtrs, segments,
                    coreg, accn));
        CREATE INDEX idx_pit_fact_select
            ON pit_fact (entity_id, tag, period_end, available_date);
        CREATE INDEX idx_pit_fact_period ON pit_fact (entity_id, qtrs, period_end);
    """)
    columns = ", ".join(pit_store.FACT_COLUMNS)
    marks = ", ".join("?" * len(pit_store.FACT_COLUMNS))
    batch = []
    n = 0
    for row in conn.execute(
            f"SELECT {columns} FROM pit_fact WHERE tag = 'NetIncomeLoss' LIMIT ?",
            (sample,)):
        batch.append(tuple(row))
        if len(batch) >= 20_000:
            probe.executemany(
                f"INSERT OR IGNORE INTO pit_fact ({columns}) VALUES ({marks})", batch)
            n += len(batch)
            batch = []
    if batch:
        probe.executemany(
            f"INSERT OR IGNORE INTO pit_fact ({columns}) VALUES ({marks})", batch)
        n += len(batch)
    probe.commit()
    probe.close()
    size = os.path.getsize(path)
    os.remove(path)
    return {"rows_loaded": n, "file_bytes": size,
            "bytes_per_row": round(size / max(n, 1), 1),
            "note": ("measured on real NetIncomeLoss rows in a throwaway copy of "
                     "the pit_fact schema, all three indexes present; an EPS row "
                     "carries a shorter tag and a 'USD/shares' unit, so this is a "
                     "close upper bound rather than an exact EPS figure")}


def earnings_sign(conn: sqlite3.Connection, sample_per_date: int = 400,
                  seed: int = 20260921) -> dict[str, Any]:
    """How often the EPS denominator would be POSITIVE, and so usable.

    P/E is not defined for a loss-making company -- the frozen core's extreme-
    valuation penalty has nothing to grade -- so EPS coverage is an upper bound
    on P/E coverage and the gap between them is this number. Measured with the
    REAL selector, `pit_store.latest_period_as_of` + `pit_policy.is_stale`.

    A SEEDED RANDOM SAMPLE of the priced universe, not the census. The full
    census is ~2,450 entities x 3 dates x up to 3 ladder rungs of random reads
    against an 8.1 GiB store on a box with 1.4 GiB of free RAM; measured, it
    ran over forty minutes and was still going. A 400-entity sample puts the
    standard error on a proportion near 0.6 at about 2.5 points, which is far
    inside the precision this decision needs, and `n` is reported beside every
    number so nobody reads it as a census.
    """
    out: dict[str, Any] = {}
    tags = net_income_tags()
    rng = random.Random(seed)
    for day in CENSUS_DATES:
        rows = pit_identity.scored_universe_as_of(conn, day)
        entities = sorted({int(r["entity_id"]) for r in rows})
        population = len(entities)
        if sample_per_date and len(entities) > sample_per_date:
            entities = sorted(rng.sample(entities, sample_per_date))
        resolved = positive = 0
        for entity_id in entities:
            for tag in tags:
                fact = pit_store.latest_period_as_of(conn, entity_id, tag, "USD",
                                                     4, day)
                if fact is None:
                    continue
                if pit_policy.is_stale(fact["period_end"], day, 4, "net_income"):
                    continue
                resolved += 1
                if float(fact["val"]) > 0:
                    positive += 1
                break
        out[day] = {
            "priced_entities_population": population,
            "priced_entities": len(entities),
            "sampled": len(entities) < population,
            "net_income_resolved": resolved,
            "positive": positive,
            "pct_positive_of_resolved": round(100.0 * positive / max(resolved, 1), 2),
            "pct_positive_of_priced": round(100.0 * positive / max(len(entities), 1), 2),
        }
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    out_dir = os.getcwd()
    skip_sign = False
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--skip-sign":
            # The sign check is ~22,000 latest_period_as_of calls and is IO
            # bound on a box with 1.4 GiB free against an 8.1 GiB store. It is
            # the one optional number here; skipping it reports UNKNOWN rather
            # than a guess.
            skip_sign = True; i += 1
        else:
            i += 1

    started = time.time()
    free_before = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path))).free
    conn = pit_cohort_scan.connect_ro(db_path)

    dates = pit_cohort_scan.grid_dates(conn)
    print("  reading net income periods ...", flush=True)
    periods = per_entity_periods(conn)
    print(f"  {len(periods):,} entities; reading the tag census ...", flush=True)
    present = eps_tags_present(conn)
    print(f"  EPS tags found: {present or 'NONE'}", flush=True)

    priced: set[int] = set()
    print("  scanning net income periods ...", flush=True)
    for day in (dates[0], dates[len(dates) // 2], dates[-1]) + CENSUS_DATES:
        for row in pit_identity.scored_universe_as_of(conn, day):
            priced.add(int(row["entity_id"]))
    listed = {int(r[0]) for r in conn.execute(
        "SELECT DISTINCT entity_id FROM pit_listing "
        "WHERE entity_id IS NOT NULL AND instrument_kind = 'company'")}
    peers = {int(r[0]) for r in conn.execute(
        "SELECT DISTINCT entity_id FROM pit_peer_member")}

    payload: dict[str, Any] = {
        "eps_tags_in_store": present,
        "why_absent": ("pit_dera.TAG_FILTER is built from the concept ladders and "
                       "no ladder names an EPS tag, so every EPS row in 72 DERA "
                       "quarters was read and dropped at ingest."),
        "universes": {
            "priced_any_date": len(priced),
            "listed_entities": len(listed),
            "peer_set_entities": len(peers),
        },
        "plans": [
            scope(periods, sorted(priced), "price-resolvable at any measured date"),
            scope(periods, sorted(listed), "every entity with a company listing"),
            scope(periods, sorted(peers), "every entity in any peer set"),
        ],
        "row_size": bytes_per_fact_row(conn, out_dir),
        "earnings_sign_proxy": (UNKNOWN_SIGN if skip_sign else earnings_sign(conn)),
        "dera_repass_alternative": {
            "archive_bytes": int(conn.execute(
                "SELECT COALESCE(SUM(bytes_downloaded), 0) FROM pit_ingest_run "
                "WHERE source LIKE 'dera:%'").fetchone()[0]),
            "quarters": int(conn.execute(
                "SELECT COUNT(*) FROM pit_ingest_run "
                "WHERE source LIKE 'dera:%'").fetchone()[0]),
            "archive_on_disk": os.path.isdir(
                os.path.join(os.path.dirname(os.path.abspath(db_path)),
                             "pit_archive", "dera")) and bool(os.listdir(
                    os.path.join(os.path.dirname(os.path.abspath(db_path)),
                                 "pit_archive", "dera"))),
            "note": ("the DERA zips were stream-and-discarded, so this route is a "
                     "re-DOWNLOAD of the whole archive, not a re-read of a local "
                     "copy. It is peak-disk cheap -- one quarter at a time, "
                     "deleted before the next -- and bandwidth expensive."),
        },
    }
    conn.close()
    payload["free_bytes_before"] = free_before
    payload["free_bytes_after"] = shutil.disk_usage(
        os.path.dirname(os.path.abspath(db_path))).free
    payload["elapsed_seconds"] = round(time.time() - started, 1)

    path = os.path.join(out_dir, "eps_scope.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"EPS tags in store: {present or 'NONE'}")
    for plan in payload["plans"]:
        print(f"  {plan['universe']:45s} entities={plan['entities']:6,d} "
              f"pairs={plan['retained_pairs_estimated']:9,d} "
              f"rows={plan['retained_rows_estimated']:10,d} "
              f"requests={plan['requests']:7,d} "
              f"{plan['minutes_at_ceiling']:6.1f} min "
              f"{plan['download_gib_estimated']:.3f} GiB down")
    size = payload["row_size"]
    print(f"  measured {size['bytes_per_row']} bytes/pit_fact row "
          f"({size['rows_loaded']:,} rows -> {size['file_bytes']:,} bytes)")
    sign = payload["earnings_sign_proxy"]
    for day, row in (sign.items() if isinstance(sign, dict)
                     and "unmeasured" not in sign else ()):
        print(f"  {day} sampled={row['priced_entities']}/"
              f"{row['priced_entities_population']} "
              f"net income resolved={row['net_income_resolved']} "
              f"positive={row['positive']} "
              f"({row['pct_positive_of_resolved']}% of resolved)")
    print(f"wrote {path} in {payload['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
