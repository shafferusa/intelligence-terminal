"""Offline tests for score signatures and the partial-score rule.

Pure stdlib, no network, NO REAL DATABASE:

    python test_pit_score_signature.py

Four kinds of check live here.

THE FREEZE. `score_v1` restates the frozen core's arithmetic rather than
importing it, and a restatement that drifts is worse than no restatement at
all. So this file imports `company_scoring` and pins `score_v1` against
`calculate_company_raw_score` over every one of the sixteen signatures and
several score vectors. It fails if the core changes AND if the restatement
drifts, which are the two ways this could go wrong.

THE COVERAGE ARITHMETIC. VGPD -> 1.00 and GPD -> 0.60, the owner's own
figures, plus the property that makes the whole v1/v2 distinction coherent:
with every pillar present the renormalising path and the non-renormalising
path are THE SAME OPERATION, for any weight table.

THE REFUSAL. The default evaluation path must separate signatures, and pooling
them must take a deliberate act -- not a keyword a caller adds to make an
exception go away, but a keyword AND a sentence.

THE RESTRAINT. This module describes a computation and specifies a schema; it
must open no connection and write no row. The DDL is exercised against a
throwaway in-memory store built from `pit_store.SCHEMA`, and the real
8.13 GiB store is never touched -- not read, not opened, not named.
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import company_scoring as CS
import pit_score_signature as S
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _raises_value(thunk) -> bool:
    try:
        thunk()
    except ValueError:
        return True
    except Exception:
        return False
    return False


def _close(a: float, b: float, tol: float = 1e-12) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=tol)


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


def _factor_results(scores: dict) -> dict:
    """The core's own input shape, from a plain pillar -> score mapping."""
    out = {}
    for pillar in S.PILLARS:
        value = scores.get(pillar)
        if value is None:
            out[pillar] = CS.FactorResult(name=pillar, score=None,
                                          status=CS.UNAVAILABLE)
        else:
            out[pillar] = CS.FactorResult(name=pillar, score=float(value),
                                          status=CS.OK)
    return out


#: Every signature, as a mapping with a distinct score per pillar so a weight
#: swap could not pass by symmetry.
_VECTORS = (
    {"valuation": 35.0, "growth": 12.0, "profitability": 28.0, "debt": 5.0},
    {"valuation": -30.0, "growth": 48.0, "profitability": 22.0, "debt": -15.0},
    {"valuation": -100.0, "growth": 100.0, "profitability": -55.0, "debt": 73.5},
    {"valuation": 0.0, "growth": -1.0, "profitability": 2.0, "debt": -3.0},
)

#: The v2 counterparts, in v2 BLOCK names. Separate fixture, not a rename of
#: the one above: after the 2026-09-21 correction the two lineages have
#: different block sets, and feeding v1's pillar names to `score_v2` is the
#: exact mistake that let valuation carry 0.35 for a week.
_V2_VECTORS = tuple(
    {"ebitda_strength": v["profitability"], "valuation": v["valuation"],
     "real_growth": v["growth"], "financial_quality": v["debt"]}
    for v in _VECTORS
)


def _subsets():
    for mask in range(16):
        yield tuple(p for i, p in enumerate(S.PILLARS) if mask & (1 << (3 - i)))


def main() -> int:
    print("== 1. signatures are canonical and total ==")
    check("all four pillars -> VGPD",
          S.signature_of(_VECTORS[0]) == "VGPD", S.signature_of(_VECTORS[0]))
    check("valuation absent -> GPD",
          S.signature_of({"growth": 1.0, "profitability": 2.0, "debt": 3.0}) == "GPD")
    check("input order cannot change the signature",
          S.signature_of({"debt": 3.0, "growth": 1.0, "profitability": 2.0}) == "GPD")
    check("nothing available -> NONE",
          S.signature_of({}) == S.SIGNATURE_NONE
          and S.signature_of({"growth": None}) == S.SIGNATURE_NONE)
    check("NaN and infinity are absence, not a score",
          S.signature_of({"growth": float("nan"), "debt": float("inf"),
                          "profitability": 1.0}) == "P")
    check("a non-numeric score is absence",
          S.signature_of({"growth": "12", "debt": None, "profitability": 1.0}) == "GP")
    check("every signature round-trips through pillars_for_signature",
          all(S.signature_of({p: 1.0 for p in subset})
              == ("".join(S.PILLAR_LETTER[p] for p in subset) or S.SIGNATURE_NONE)
              and S.pillars_for_signature(
                  "".join(S.PILLAR_LETTER[p] for p in subset) or S.SIGNATURE_NONE)
              == subset
              for subset in _subsets()))
    check("missing_of complements the signature",
          S.missing_of("GPD") == ("valuation",)
          and S.missing_of("VGPD") == ()
          and S.missing_of(S.SIGNATURE_NONE) == S.PILLARS)
    check("an unreadable signature raises rather than guessing",
          _raises(lambda: S.pillars_for_signature("VGX"), ValueError)
          and _raises(lambda: S.pillars_for_signature("VV"), ValueError))

    print()
    print("== 2. original_weight_coverage: VGPD 1.00, GPD 0.60 ==")
    check("VGPD -> 1.00", _close(S.weight_coverage("VGPD"), 1.00),
          S.weight_coverage("VGPD"))
    check("GPD -> 0.60", _close(S.weight_coverage("GPD"), 0.60),
          S.weight_coverage("GPD"))
    check("GPD carries 0.60 of RAW original weight",
          _close(S.weight_present("GPD"), 0.60))
    check("the missing 0.40 is exactly the valuation weight",
          _close(1.0 - S.weight_present("GPD"), S.V1_MAJOR_WEIGHTS["valuation"]))
    check("every non-empty signature has coverage in (0, 1]",
          all(0.0 < S.weight_coverage("".join(S.PILLAR_LETTER[p] for p in sub)) <= 1.0
              for sub in _subsets() if sub))
    check("coverage is a fraction of the model's own total, so v2 EVGQ is 1.00 too",
          _close(S.weight_coverage("EVGQ", S.V2_MAJOR_WEIGHTS), 1.00)
          and _close(S.weight_coverage("EGQ", S.V2_MAJOR_WEIGHTS), 0.60 / 0.85))
    check("a v1 signature is NOT readable under v2's weights -- the alphabets "
          "belong to different models",
          _raises_value(lambda: S.weight_coverage("VGPD", S.V2_MAJOR_WEIGHTS)))
    check("a scored result reports the same coverage the helper does",
          all(_close(S.score_v1({p: 1.0 for p in sub}).original_weight_coverage,
                     S.weight_coverage("".join(S.PILLAR_LETTER[p] for p in sub)))
              for sub in _subsets() if sub))

    print()
    print("== 3. FREEZE: v1's renormalised arithmetic is unchanged ==")
    check("the restated v1 weights equal company_scoring.MAJOR_WEIGHTS",
          S.V1_MAJOR_WEIGHTS == CS.MAJOR_WEIGHTS, CS.MAJOR_WEIGHTS)
    check("the restated v1 scale equals company_scoring.COMPANY_SCALE",
          _close(S.V1_COMPANY_SCALE, CS.COMPANY_SCALE))
    check("the restated clamps equal the core's",
          _close(S.V1_RAW_MIN, CS.SCORE_MIN) and _close(S.V1_RAW_MAX, CS.SCORE_MAX)
          and _close(S.V1_COMPANY_MIN, CS.COMPANY_SCORE_MIN)
          and _close(S.V1_COMPANY_MAX, CS.COMPANY_SCORE_MAX))

    raw_mismatch, weight_mismatch, company_mismatch, compared = [], [], [], 0
    for vector in _VECTORS:
        for subset in _subsets():
            scores = {p: vector[p] for p in subset}
            core_raw, core_used, _, _ = CS.calculate_company_raw_score(
                _factor_results(scores))
            core_company = CS.calculate_company_score(core_raw)
            mine = S.score_v1(scores)
            compared += 1
            if core_raw is None:
                if mine.raw_score is not None:
                    raw_mismatch.append((subset, core_raw, mine.raw_score))
                continue
            if not _close(core_raw, mine.raw_score):
                raw_mismatch.append((subset, core_raw, mine.raw_score))
            if not _close(core_company, mine.company_score):
                company_mismatch.append((subset, core_company, mine.company_score))
            if set(core_used) != set(mine.effective_weights) or any(
                    not _close(core_used[p], mine.effective_weights[p])
                    for p in core_used):
                weight_mismatch.append((subset, core_used, mine.effective_weights))
    check(f"raw score matches the frozen core on all {compared} cases",
          not raw_mismatch, raw_mismatch[:2])
    check("company score matches the frozen core on all cases",
          not company_mismatch, company_mismatch[:2])
    check("the renormalised effective weights match the frozen core exactly",
          not weight_mismatch, weight_mismatch[:2])
    check("no-pillar case: the core returns None and so does score_v1",
          CS.calculate_company_raw_score(_factor_results({}))[0] is None
          and S.score_v1({}).company_score is None
          and S.score_v1({}).partial_score_marker == S.NOT_SCORED)
    gpd = S.score_v1({"growth": 48.0, "profitability": 22.0, "debt": -15.0})
    check("v1 STILL renormalises -- the baseline is preserved, not corrected",
          _close(gpd.effective_weights["growth"], 0.25 / 0.60)
          and _close(gpd.effective_weights["profitability"], 0.20 / 0.60)
          and _close(gpd.effective_weights["debt"], 0.15 / 0.60))
    check("and that is exactly pit_shares.VALUATION_DROPPED_WEIGHTS",
          _close(gpd.effective_weights["growth"], 0.4166666666666667)
          and _close(gpd.effective_weights["profitability"], 0.3333333333333333)
          and _close(gpd.effective_weights["debt"], 0.25))

    print()
    print("== 4. a v2 partial is marked partial and NOT directly comparable ==")
    v2_partial = S.score_v2({"ebitda_strength": 48.0, "real_growth": 22.0,
                             "financial_quality": -15.0})
    check("marker is PARTIAL_SHAFFER_SCORE",
          v2_partial.partial_score_marker == S.PARTIAL_SHAFFER_SCORE)
    check("the marker's own VALUE says not directly comparable",
          "NOT_DIRECTLY_COMPARABLE" in v2_partial.partial_score_marker)
    check("comparable_with_full is False", v2_partial.comparable_with_full is False)
    check("is_partial is True", v2_partial.is_partial is True)
    check("it names WHICH pillar is missing",
          v2_partial.pillars_missing == ("valuation",))
    check("it carries the core coverage",
          _close(v2_partial.original_weight_coverage, 0.60 / 0.85))
    rendered = S.render_partial_score(v2_partial)
    check("the renderer prints the missing pillar, the coverage and the marker",
          "MISSING PILLAR" in rendered and "Valuation" in rendered
          and "CORE COVERAGE" in rendered
          and S.NOT_COMPARABLE_PHRASE.upper() in rendered
          and S.PARTIAL_SHAFFER_SCORE in rendered)
    check("v2 does NOT renormalise across blocks: 0.85 is the denominator",
          _close(v2_partial.effective_weights["ebitda_strength"], 0.35 / 0.85)
          and _close(v2_partial.effective_weights["real_growth"], 0.15 / 0.85)
          and _close(v2_partial.effective_weights["financial_quality"],
                     0.10 / 0.85))
    v2_full = S.score_v2({"ebitda_strength": 0.0, "real_growth": 48.0,
                          "financial_quality": 22.0, "valuation": -15.0})
    check("LOSING A PILLAR DOES NOT PROMOTE THE SURVIVORS under v2",
          all(_close(v2_full.effective_weights[p], v2_partial.effective_weights[p])
              for p in v2_partial.pillars_present))
    check("...whereas under v1 it promotes growth by a factor of 5/3",
          _close(gpd.effective_weights["growth"] / S.V1_MAJOR_WEIGHTS["growth"],
                 5.0 / 3.0))
    check("a v2 partial lands on a SMALLER scale: the company score is the "
          "plain 0.35E + 0.15G + 0.10Q",
          _close(v2_partial.company_score,
                 0.35 * 48.0 + 0.15 * 22.0 + 0.10 * -15.0))

    print()
    print("== 5. every pillar present: identical under both paths ==")
    for name, table, full_sig, keys in (
            ("v1", S.V1_MAJOR_WEIGHTS, S.SIGNATURE_FULL, S.PILLARS),
            ("v2", S.V2_MAJOR_WEIGHTS, S.V2_SIGNATURE_FULL, S.V2_BLOCKS)):
        renorm = S.effective_weights(full_sig, table, renormalise=True)
        direct = S.effective_weights(full_sig, table, renormalise=False)
        check(f"{name}: renormalising and not renormalising agree at {full_sig}",
              all(_close(renorm[k], direct[k]) for k in keys),
              (renorm, direct))
    for vector in _VECTORS:
        full_v1 = S.score_v1(vector)
        hand = sum(S.V1_MAJOR_WEIGHTS[p] * vector[p] for p in S.PILLARS)
        check("v1 at VGPD equals the UN-renormalised weighted sum "
              f"({vector['valuation']:+.1f}V ...)",
              _close(full_v1.raw_score, hand)
              and _close(full_v1.company_score, S.V1_COMPANY_SCALE * hand))
    for vector in _VECTORS:
        v2_vector = {b: 10.0 + 3.0 * i for i, b in enumerate(S.V2_BLOCKS)}
        full_v2 = S.score_v2(v2_vector)
        hand = sum(S.V2_MAJOR_WEIGHTS[b] * v2_vector[b] for b in S.V2_BLOCKS)
        check("v2 at EVGQ equals the same sum under its own weights "
              f"({vector['valuation']:+.1f}V ...)",
              _close(full_v2.company_score, hand))
    check("a complete row is marked FULL under both lineages",
          S.score_v1(_VECTORS[0]).partial_score_marker == S.FULL_SHAFFER_SCORE
          and S.score_v2(_V2_VECTORS[0]).partial_score_marker == S.FULL_SHAFFER_SCORE)
    check("a VGPD row carries coverage 1.00 under both lineages",
          _close(S.score_v1(_VECTORS[0]).original_weight_coverage, 1.0)
          and _close(S.score_v2(_V2_VECTORS[0]).original_weight_coverage, 1.0))

    print()
    print("== 6. the default evaluation path refuses to pool silently ==")
    rows = [S.score_v1(factors) for _, factors, _ in S.DEMO_COMPANIES]
    grouping = S.evaluation_groups(rows)
    check("the default path SEPARATES: three signatures, three groups",
          grouping.n_groups == 3 and grouping.n_rows == 3 and grouping.is_mixed)
    check("groups are keyed (model_version, signature)",
          set(grouping.keys) == {(S.V1_MODEL_VERSION, "VGPD"),
                                 (S.V1_MODEL_VERSION, "GPD"),
                                 (S.V1_MODEL_VERSION, "GD")}, grouping.keys)
    check("each group reports its own original_weight_coverage",
          _close(grouping.coverage_by_group()[(S.V1_MODEL_VERSION, "GPD")], 0.60)
          and _close(grouping.coverage_by_group()[(S.V1_MODEL_VERSION, "VGPD")], 1.0))
    check("Grouping exposes no flattened list -- there is no accidental way out",
          not any(hasattr(grouping, attr) for attr in ("all", "rows", "flat")))
    check("pool() with no acknowledgement RAISES",
          _raises(lambda: S.pool(rows), S.SignaturePoolingRefused))
    check("the boolean alone is not enough",
          _raises(lambda: S.pool(rows, acknowledge_different_models=True),
                  S.SignaturePoolingRefused))
    check("a justification alone is not enough",
          _raises(lambda: S.pool(rows, justification="because"),
                  S.SignaturePoolingRefused))
    check("whitespace is not a justification",
          _raises(lambda: S.pool(rows, acknowledge_different_models=True,
                                 justification="   "),
                  S.SignaturePoolingRefused))
    try:
        S.pool(rows)
        message = ""
    except S.SignaturePoolingRefused as exc:
        message = str(exc)
    check("the refusal names the signatures and their coverages",
          "GPD" in message and "VGPD" in message and "0.60" in message
          and "1.00" in message, message[:120])
    deliberate = S.pool(rows, acknowledge_different_models=True,
                        justification="diagnostic: how far the partials move the IC")
    check("a deliberate pool returns every row", len(deliberate) == 3)
    check("...and carries the justification forward on the result",
          "diagnostic: how far the partials move the IC" in deliberate.warning
          and "POOLED ACROSS SIGNATURES" in deliberate.warning)
    one_group = [r for r in rows if r.signature == "VGPD"]
    check("a single-signature pool needs no ceremony",
          len(S.pool(one_group)) == 1 and S.pool(one_group).warning == "")
    check("v1 VGPD and v2 VGPD are DIFFERENT groups -- the key carries the model",
          S.evaluation_groups([S.score_v1(_VECTORS[0]),
                               S.score_v2(_V2_VECTORS[0])]).n_groups == 2)
    check("a dict row with a signature groups like a PillarScore",
          S.evaluation_groups([{"model_version": "m", "score_signature": "GPD"}]
                              ).keys == (("m", "GPD"),))
    check("a row with NO signature is refused, never defaulted to VGPD",
          _raises(lambda: S.evaluation_groups(
              [{"model_version": "m", "score_signature": None}]),
              S.SignaturePoolingRefused))
    check("a row with no model_version is refused too",
          _raises(lambda: S.evaluation_groups(
              [{"model_version": "", "score_signature": "VGPD"}]),
              S.SignaturePoolingRefused))

    print()
    print("== 7. the DDL is valid, idempotent, and applies to a THROWAWAY store ==")
    check("the migration patch is in pit_store's ADD-COLUMN shape",
          S.migration_patch() == {"pit_score": [
              ("score_signature", "TEXT"),
              ("original_weight_coverage", "REAL"),
              ("partial_score_marker", "TEXT")]}, S.migration_patch())
    check("pit_store.MIGRATIONS has the same (table -> [(name, type)]) shape",
          all(isinstance(v, list) and all(isinstance(t, tuple) and len(t) == 2
                                          for t in v)
              for v in pit_store.MIGRATIONS.values()))
    check("pit_score does NOT yet carry these columns in pit_store.MIGRATIONS "
          "-- the patch is left for later, as instructed",
          "pit_score" not in pit_store.MIGRATIONS)
    check("no added column carries a DEFAULT",
          all("DEFAULT" not in t.upper() for _, t in S.PIT_SCORE_SIGNATURE_MIGRATIONS))

    scratch = sqlite3.connect(":memory:")
    scratch.executescript(pit_store.SCHEMA)
    before = S.columns_present(scratch)
    check("a fresh store has none of the three columns",
          before == {"score_signature": False, "original_weight_coverage": False,
                     "partial_score_marker": False}, before)
    added = S.ensure_columns(scratch)
    check("ensure_columns adds exactly the three",
          added == ["pit_score.score_signature",
                    "pit_score.original_weight_coverage",
                    "pit_score.partial_score_marker"], added)
    check("and they are there afterwards",
          S.columns_present(scratch) == {"score_signature": True,
                                         "original_weight_coverage": True,
                                         "partial_score_marker": True})
    check("ensure_columns is idempotent: a second call adds nothing",
          S.ensure_columns(scratch) == [])
    check("the index is created and is named in the DDL",
          scratch.execute(
              "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
              "AND name='idx_pit_score_signature'").fetchone()[0] == 1
          and "idx_pit_score_signature" in S.PIT_SCORE_COLUMN_DDL)
    check("migration_sql() returns statements and executes nothing",
          len(S.migration_sql()) == 4
          and all(s.upper().startswith(("ALTER", "CREATE"))
                  for s in S.migration_sql())
          and len(S.migration_sql(include_guard=True)) == 5)

    #: A stamped insert, to prove the columns take the values this module
    #: produces -- and that they land on the row rather than in a note.
    stamped = S.pit_score_fields(v2_partial)
    scratch.execute(
        "INSERT INTO pit_score (entity_id, as_of_date, model_version, "
        "replay_run_id, latency_policy_version, peer_set_version, "
        "ladder_version, effective_weights_json, created_at, score_signature, "
        "original_weight_coverage, partial_score_marker) "
        "VALUES (1, '2019-06-28', ?, 1, 'x', 'y', 'z', '{}', 'now', ?, ?, ?)",
        (S.CANDIDATE_MODEL_VERSION, stamped["score_signature"],
         stamped["original_weight_coverage"], stamped["partial_score_marker"]))
    row = scratch.execute(
        "SELECT score_signature, original_weight_coverage, partial_score_marker "
        "FROM pit_score").fetchone()
    check("a stamped row reads back with signature, coverage and marker",
          row[0] == "EGQ" and _close(row[1], 0.60 / 0.85)
          and row[2] == S.PARTIAL_SHAFFER_SCORE, row)
    check("pit_score_fields also carries the missing pillars and the weights",
          stamped["missing_factors"] == ["valuation"]
          and set(stamped["effective_weights"]) == {
              "ebitda_strength", "real_growth", "financial_quality"})

    print()
    print("== 8. the guard trigger refuses an unsigned score ==")
    guarded = sqlite3.connect(":memory:")
    guarded.executescript(pit_store.SCHEMA)
    S.ensure_columns(guarded, include_guard=True)

    def _insert(entity_id: int, signature, marker) -> None:
        guarded.execute(
            "INSERT INTO pit_score (entity_id, as_of_date, model_version, "
            "replay_run_id, latency_policy_version, peer_set_version, "
            "ladder_version, effective_weights_json, created_at, "
            "score_signature, partial_score_marker) "
            "VALUES (?, '2019-06-28', 'm', 1, 'x', 'y', 'z', '{}', 'now', ?, ?)",
            (entity_id, signature, marker))

    def _message(thunk) -> str:
        try:
            thunk()
        except sqlite3.Error as exc:
            return str(exc)
        return ""

    #: Each trigger is exercised with the OTHER column supplied, so the message
    #: is deterministic: two BEFORE INSERT triggers on one table fire in an
    #: order SQLite does not promise, and a test that depended on it would be
    #: a test of trigger order rather than of the guard.
    unsigned = _message(lambda: _insert(2, None, S.FULL_SHAFFER_SCORE))
    check("an insert with no score_signature is ABORTED", bool(unsigned))
    check("and the abort message says why",
          "score_signature is required" in unsigned, unsigned)
    empty = _message(lambda: _insert(3, "", S.FULL_SHAFFER_SCORE))
    check("an EMPTY signature is refused as well as a NULL one",
          "score_signature is required" in empty, empty)
    unmarked = _message(lambda: _insert(4, "GPD", None))
    check("an insert with a signature but no marker is ABORTED too",
          "partial_score_marker is required" in unmarked, unmarked)
    guarded.execute(
        "INSERT INTO pit_score (entity_id, as_of_date, model_version, "
        "replay_run_id, latency_policy_version, peer_set_version, "
        "ladder_version, effective_weights_json, created_at, score_signature, "
        "original_weight_coverage, partial_score_marker) "
        "VALUES (5, '2019-06-28', 'm', 1, 'x', 'y', 'z', '{}', 'now', 'VGPD', "
        "1.0, ?)", (S.FULL_SHAFFER_SCORE,))
    check("a fully stamped insert passes the guard",
          guarded.execute("SELECT COUNT(*) FROM pit_score").fetchone()[0] == 1)

    #: THE PRECONDITION, asserted rather than promised: pit_store.save_score as
    #: written today does NOT stamp the columns, so installing the guard before
    #: teaching it would refuse every insert the sanctioned writer makes.
    save_message = _message(
        lambda: pit_store.save_score(guarded, entity_id=6,
                                     as_of_date="2019-06-28",
                                     model_version="m", replay_run_id=1))
    #: Either trigger may be the one that fires -- save_score stamps neither
    #: column -- so the assertion is that it was REFUSED and told to stamp one
    #: of them, not which of two triggers got there first.
    check("pit_store.save_score is REFUSED by the guard today -- which is why "
          "PIT_SCORE_GUARD_DDL is specified separately and left for later",
          "is required" in save_message
          and ("score_signature" in save_message
               or "partial_score_marker" in save_message), save_message)
    guarded.close()
    scratch.close()

    print()
    print("== 9. restraint: no connection, no rows, the real store untouched ==")
    source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "pit_score_signature.py"), encoding="utf-8").read()
    check("the module never calls sqlite3.connect",
          "sqlite3.connect" not in source)
    check("the module never imports sqlite3 at all",
          "import sqlite3" not in source)
    check("the module writes no pit_score or pit_feature rows",
          "INSERT INTO pit_score" not in source
          and "INSERT INTO pit_feature" not in source)
    check("the module never names the real store file",
          "shafferfineval_pit.db" not in source
          and "DEFAULT_PIT_DB_PATH" not in source)
    check("no ML fitting: nothing is trained, nothing is fitted",
          not any(word in source for word in ("sklearn", "numpy", "import pandas",
                                              ".fit(", "gradient", "regress")))

    original = sqlite3.connect

    def _landmine(*args, **kwargs):
        raise AssertionError("pit_score_signature opened a connection")

    sqlite3.connect = _landmine
    try:
        S.render()
        S.render_demonstration()
        S.render_promotion_table()
        S.render_pooling_demonstration()
        S.render_partial_score(v2_partial)
        S.render_score(S.score_v1(_VECTORS[0]))
        S.render_comparison(S.score_v1(_VECTORS[1]), S.score_v2(_V2_VECTORS[1]))
        S.render_grouping(grouping)
        S.storage_note()
        S.migration_patch()
        S.migration_sql(include_guard=True)
        S.validate()
        for vector in _VECTORS:
            S.score(vector, "v1")
        for vector in _V2_VECTORS:
            S.score(vector, "v2")
        opened = False
    except AssertionError:
        opened = True
    finally:
        sqlite3.connect = original
    check("the entire public API runs without opening a connection", not opened)

    print()
    print("== 10. the module's own self-check and its recorded finding ==")
    check("validate() finds nothing to complain about", S.validate() == [],
          S.validate())
    check("python pit_score_signature.py would exit 0", S.main() == 0)
    check("the v1 finding is recorded as SHAFFER_V1_KNOWN_LIMITATION",
          S.KNOWN_LIMITATIONS["company_scoring.calculate_company_raw_score"]
          ["status"] == "SHAFFER_V1_KNOWN_LIMITATION")
    check("the measured availability gap is on record: 99.32/99.75/99.83% "
          "score against a 34.2% valuation cohort",
          _close(S.MEASURED["company_raw_score_availability"]["2015-06-30"], 0.9932)
          and _close(S.MEASURED["company_raw_score_availability"]["2024-06-28"],
                     0.9983)
          and S.MEASURED["ev_ebitda_cohort"]["pct_ge_3"] == 34.2)
    check("price-touching figures are labelled SURVIVOR_ONLY_DIAGNOSTIC",
          "SURVIVOR_ONLY_DIAGNOSTIC" in S.MEASURED["sample_scope"])
    check("score() refuses an unknown lineage rather than defaulting",
          _raises(lambda: S.score(_VECTORS[0], "v3"), ValueError))

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
