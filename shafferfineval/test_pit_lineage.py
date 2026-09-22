"""Offline tests for the model lineage record.

Pure stdlib, no network, throwaway database:   python test_pit_lineage.py

What these checks are actually guarding. A lineage row is only worth anything if
it still says what the code says -- a record that has quietly drifted from
`company_scoring.py` is worse than no record, because it looks authoritative.
So the weight checks below compare against the IMPORTED constants rather than
against numbers typed into this file: if someone edits a production weight, this
test fails, and that failure is the point. The other invariants are the ones the
promotion story rests on: a predecessor link that survives, a specification that
cannot be rewritten by a later seed, and a status that moves only through an
audited promotion.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_models
import company_scoring
import hedging
import pit_lineage_seed as seed
import pit_store
import prediction
import sector_scoring
import storage

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _row(conn: sqlite3.Connection, version: str):
    return conn.execute(
        "SELECT * FROM pit_model_lineage WHERE model_version = ?", (version,)
    ).fetchone()


def main() -> int:
    db_path = os.path.join(tempfile.mkdtemp(), "lineage.db")
    conn = pit_store.init_db(db_path)

    print("== 1. the seed writes, and writes once ==")
    result = seed.seed_lineage(conn)
    check("database created", os.path.exists(db_path))
    check("every record reports a status",
          set(result.values()) <= {"written", "exists"}, set(result.values()))
    check("first run writes everything",
          all(status == "written" for status in result.values()),
          [v for v, s in result.items() if s != "written"])

    required = ("equity_model_v1", "sector_model_v1", "hedge_model_v1",
                "return_calibration_v1_0.20", "equity_shaffer_v1_pit")
    for version in required:
        check(f"{version} registered", _row(conn, version) is not None)

    again = seed.seed_lineage(conn)
    check("re-running is idempotent and rewrites nothing",
          all(status == "exists" for status in again.values()),
          [v for v, s in again.items() if s != "exists"])
    check("no duplicate versions",
          conn.execute("SELECT COUNT(*) FROM pit_model_lineage").fetchone()[0]
          == len(result), len(result))
    check("every record names the file its specification came from",
          all(pit_store.loads(r["factor_definitions_json"]).get("source_file")
              for r in seed.lineage_rows(conn)))

    print("== 2. equity weights survive the JSON round-trip exactly ==")
    equity = _row(conn, storage.EQUITY_MODEL_VERSION)
    weights = pit_store.loads(equity["weights_json"])
    check("weights_json is a dict", isinstance(weights, dict), type(weights))
    check("major weights match company_scoring.MAJOR_WEIGHTS",
          weights["major"] == company_scoring.MAJOR_WEIGHTS, weights.get("major"))
    check("growth weights match company_scoring.GROWTH_WEIGHTS",
          weights["growth"] == company_scoring.GROWTH_WEIGHTS, weights.get("growth"))
    check("profitability weights match company_scoring.PROFITABILITY_WEIGHTS",
          weights["profitability"] == company_scoring.PROFITABILITY_WEIGHTS,
          weights.get("profitability"))
    check("debt weights match company_scoring.DEBT_WEIGHTS",
          weights["debt"] == company_scoring.DEBT_WEIGHTS, weights.get("debt"))
    check("weights are floats after the round-trip, not strings",
          all(isinstance(v, float) for v in weights["major"].values()),
          weights["major"])
    check("the four major weights still sum to 1.00",
          abs(sum(weights["major"].values()) - 1.0) < 1e-9,
          sum(weights["major"].values()))
    check("valuation is still 0.40 and debt 0.15",
          weights["major"]["valuation"] == 0.40 and weights["major"]["debt"] == 0.15,
          weights["major"])

    transforms = pit_store.loads(equity["transformations_json"])
    check("0.75 company scaling recorded",
          transforms["company_scale"] == company_scoring.COMPANY_SCALE,
          transforms.get("company_scale"))
    check("tanh steepness recorded",
          transforms["valuation_tanh_k"] == company_scoring.VALUATION_TANH_K,
          transforms.get("valuation_tanh_k"))
    check("company score clamp recorded as [-75, +75]",
          transforms["company_score_clamp"] == [company_scoring.COMPANY_SCORE_MIN,
                                                company_scoring.COMPANY_SCORE_MAX],
          transforms.get("company_score_clamp"))
    check("drop-and-renormalise is the recorded missing-data rule",
          "renormalis" in equity["missing_data_rule"]
          and "never silently becomes zero" in equity["missing_data_rule"])
    check("normalisation records the industry percentile rank",
          "percentile" in equity["normalization"].lower()
          and "industry" in equity["normalization"].lower())
    check("equity model is human-authored", equity["authorship"] == "human")
    check("equity model is PRODUCTION", equity["status"] == pit_store.PRODUCTION)

    definitions = pit_store.loads(equity["factor_definitions_json"])
    check("peer construction records the 5-peer industry minimum",
          definitions["peer_construction"]["min_industry_peers"]
          == company_scoring.MIN_INDUSTRY_PEERS)
    check("the EBITDA cohort is recorded as the 50th-75th percentile band",
          "50th-75th" in definitions["peer_construction"]["ebitda_cohort"])
    check("the four classification bands are recorded",
          set(definitions["classification"]) == {"BULLISH", "SEMI-BULLISH",
                                                 "SEMI-BEARISH", "BEARISH"},
          definitions["classification"])
    check("BULLISH still starts at +40",
          definitions["classification"]["BULLISH"][0] == 40.0,
          definitions["classification"]["BULLISH"])

    print("== 3. the other production specifications ==")
    sector = _row(conn, storage.SECTOR_MODEL_VERSION)
    sector_weights = pit_store.loads(sector["weights_json"])
    check("sector weights match sector_scoring.SECTOR_FACTOR_WEIGHTS",
          sector_weights == sector_scoring.SECTOR_FACTOR_WEIGHTS, sector_weights)
    check("the /4 overlay divisor is recorded",
          pit_store.loads(sector["transformations_json"])["overlay_divisor"]
          == sector_scoring.OVERLAY_DIVISOR)
    check("the overlay clamp is recorded as [-25, +25]",
          pit_store.loads(sector["transformations_json"])["overlay_clamp"]
          == [sector_scoring.OVERLAY_MIN, sector_scoring.OVERLAY_MAX])

    hedge = _row(conn, storage.HEDGE_MODEL_VERSION)
    check("strategy weights match hedging.STRATEGY_WEIGHTS",
          pit_store.loads(hedge["weights_json"]) == hedging.STRATEGY_WEIGHTS)
    hedge_rules = pit_store.loads(hedge["hedge_rules_json"])
    check("hedge floor and span recorded",
          hedge_rules["hedge_floor"] == hedging.HEDGE_FLOOR
          and hedge_rules["hedge_span"] == hedging.HEDGE_SPAN, hedge_rules)
    check("the hedge ratio arithmetic is recorded",
          "(AdverseScore - 15) / 85"
          in pit_store.loads(hedge["transformations_json"])["hedge_ratio"])

    calibration = _row(conn, storage.RETURN_CALIBRATION_VERSION)
    calibration_weights = pit_store.loads(calibration["weights_json"])
    check("the 0.20 slope matches prediction.V1_SLOPE",
          calibration_weights["slope"] == prediction.V1_SLOPE, calibration_weights)
    check("the intercept is 0.0",
          calibration_weights["intercept"] == prediction.V1_INTERCEPT)
    check("the calibration is versioned separately and names itself",
          calibration["calibration_version"] == storage.RETURN_CALIBRATION_VERSION)

    print("== 4. the PIT twin descends from the production model ==")
    twin = _row(conn, pit_store.EQUITY_PIT_MODEL_VERSION)
    check("twin registered under the pit_store version id",
          twin["model_version"] == "equity_shaffer_v1_pit", twin["model_version"])
    check("twin records equity_model_v1 as its predecessor",
          twin["predecessor_version"] == storage.EQUITY_MODEL_VERSION,
          twin["predecessor_version"])
    check("twin status is RESEARCH, not production",
          twin["status"] == pit_store.RESEARCH, twin["status"])
    check("twin authorship is human -- it implements a human equation",
          twin["authorship"] == "human", twin["authorship"])
    check("twin carries the SAME weights as the production equity model",
          pit_store.loads(twin["weights_json"]) == weights)
    check("twin is not named the Shaffer Score",
          "Shaffer Score" not in twin["model_name"].replace(
              "equity Shaffer Score", ""),
          twin["model_name"])
    check("twin is labelled a Historical PIT Twin",
          "PIT Twin" in twin["model_name"], twin["model_name"])
    check("twin records the gap as evidence rather than a defect",
          "NOT A DEFECT" in twin["notes"].upper())
    check("twin records which point-in-time policies produced it",
          pit_store.LATENCY_POLICY_VERSION in twin["notes"]
          and pit_store.PEER_SET_VERSION in twin["notes"])

    print("== 5. a seeded specification cannot be rewritten ==")
    tampered = seed.equity_model_v1_record()
    tampered["weights"] = {"major": {"valuation": 0.99}}
    check("a re-seed of an existing version reports 'exists'",
          seed.register_record(conn, tampered) == "exists")
    check("and the stored weights are unchanged",
          pit_store.loads(_row(conn, storage.EQUITY_MODEL_VERSION)["weights_json"])
          == weights)

    print("== 6. promotion moves status and leaves an audit row ==")
    before = conn.execute("SELECT COUNT(*) FROM pit_promotion").fetchone()[0]
    check("no promotions yet -- the v1 models predate this store", before == 0, before)

    promotion_id = pit_store.record_promotion(
        conn, pit_store.EQUITY_PIT_MODEL_VERSION,
        from_status=pit_store.RESEARCH, to_status=pit_store.CHALLENGER,
        promoted_by="logan", note="Replayed 2010-2024; ready to be benchmarked.",
        evidence={"replay_run_id": 1, "folds_improved": 7, "folds_total": 10},
    )
    check("promotion returns an id", isinstance(promotion_id, int) and promotion_id > 0,
          promotion_id)
    check("the lineage status moved",
          _row(conn, pit_store.EQUITY_PIT_MODEL_VERSION)["status"]
          == pit_store.CHALLENGER)

    audit = conn.execute(
        "SELECT * FROM pit_promotion WHERE promotion_id = ?", (promotion_id,)
    ).fetchone()
    check("an audit row was written", audit is not None)
    check("the audit row records where it came from and where it went",
          audit["from_status"] == pit_store.RESEARCH
          and audit["to_status"] == pit_store.CHALLENGER)
    check("the audit row records WHO promoted it", audit["promoted_by"] == "logan")
    check("the audit row is timestamped", bool(audit["promoted_at"]))
    check("the evidence survives the round-trip",
          pit_store.loads(audit["evidence_json"])["folds_improved"] == 7)

    pit_store.record_promotion(
        conn, pit_store.EQUITY_PIT_MODEL_VERSION,
        from_status=pit_store.CHALLENGER, to_status=pit_store.RESEARCH,
        promoted_by="logan", note="Withdrawn pending a wider universe.")
    check("a second transition appends rather than replaces",
          conn.execute("SELECT COUNT(*) FROM pit_promotion").fetchone()[0] == 2)
    check("status follows the latest transition",
          _row(conn, pit_store.EQUITY_PIT_MODEL_VERSION)["status"]
          == pit_store.RESEARCH)
    check("the predecessor link is untouched by a promotion",
          _row(conn, pit_store.EQUITY_PIT_MODEL_VERSION)["predecessor_version"]
          == storage.EQUITY_MODEL_VERSION)
    check("promoting the twin did not touch the production model",
          _row(conn, storage.EQUITY_MODEL_VERSION)["status"] == pit_store.PRODUCTION)

    print("== 7. the wider catalogue ==")
    rows = seed.lineage_rows(conn)
    versions = {r["model_version"] for r in rows}
    check("all nineteen asset-class models registered",
          all(m.version in versions for m in asset_models.MODELS.values()),
          sorted(m.version for m in asset_models.MODELS.values()
                 if m.version not in versions))
    check("equity_shaffer_v1 is recorded as the same model under another name",
          "one model" in (_row(conn, "equity_shaffer_v1")["notes"] or ""))
    check("the equity catalogue entry keeps the 0.75 scale",
          pit_store.loads(
              _row(conn, "equity_shaffer_v1")["transformations_json"])["scale"] == 0.75)
    check("the four self-versioned derivative overlays registered",
          set(seed.DERIVATIVE_OVERLAYS) <= versions,
          sorted(set(seed.DERIVATIVE_OVERLAYS) - versions))
    check("production rows sort ahead of research rows",
          rows[0]["status"] == pit_store.PRODUCTION)
    check("store_stats counts the lineage table",
          pit_store.store_stats(conn)["pit_model_lineage"] == len(rows))

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
