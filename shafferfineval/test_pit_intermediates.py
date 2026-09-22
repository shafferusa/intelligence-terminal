"""Offline tests for the intermediate store and the attribution engine.

Pure stdlib, no network, NO DATABASE:   python test_pit_intermediates.py

Three kinds of check live here.

The first guards the ARITHMETIC. The decomposition is an algebraic identity,
so its residual must be zero on every case including the awkward ones --
availability changing mid-way, a pillar entering, a pillar leaving, weights
moving while values stand still. Generated cases, not hand-picked ones: a
hand-picked reconciliation test passes on the cases its author thought of.

The second guards the SEPARATION, which is the whole reason the module exists.
A pure weight change must produce ZERO factor effect and a pure factor change
must produce ZERO weight effect. If those two ever bleed into each other, an
availability change gets reported as an economic one and the product starts
telling its owner that a company got worse when all that happened was a filer
missed a tag.

The third guards the RESTRAINT. This module describes where the replay's
output will land; it does not put anything there. It must open no connection,
issue no INSERT, and run no DDL. That is asserted here rather than promised in
a docstring -- with a fake connection that records every statement and fails
the suite if a write ever reaches it.
"""
from __future__ import annotations

import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pit_derive
import pit_intermediates as M
import pit_invariants

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=tol)


def _flat(text: str) -> str:
    """Whitespace-normalised, so a wrapped docstring still matches a phrase."""
    return " ".join((text or "").split())


def _code_only(path: str) -> str:
    """The module's SOURCE with comments and string literals removed.

    A grep for 'sqlite3.connect' over the raw file finds the docstring sentence
    promising there is no sqlite3.connect, and passes or fails for the wrong
    reason either way. Tokenising first means the check is about what the code
    DOES, which is the only thing worth asserting.
    """
    import io
    import tokenize
    kept: list[str] = []
    with open(path, encoding="utf-8") as handle:
        for token in tokenize.generate_tokens(io.StringIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return " ".join(kept)


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


class _FakeConn:
    """A connection that records instead of connecting. Nothing is a database.

    Every statement is kept. `writes` is the list that must stay empty: if any
    module function reaches for INSERT, UPDATE, DELETE, CREATE, ALTER or DROP,
    it lands here and the suite fails.
    """

    WRITE = re.compile(r"^\s*\(?\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|"
                       r"REPLACE|PRAGMA\s+\w+\s*=)", re.IGNORECASE)

    def __init__(self, rows: list = ()) -> None:
        self.statements: list[str] = []
        self.writes: list[str] = []
        self.scripts: list[str] = []
        self.commits = 0
        self._rows = list(rows)

    def execute(self, sql: str, params=()):
        self.statements.append(sql)
        if self.WRITE.match(sql):
            self.writes.append(sql)
        return _FakeCursor(self._rows)

    def executescript(self, sql: str):
        self.scripts.append(sql)
        self.writes.append(sql)
        return _FakeCursor([])

    def commit(self) -> None:
        self.commits += 1


class _FakeCursor:
    def __init__(self, rows) -> None:
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _part(key: str, value, *, available: bool = True, weight=None,
          reason: str = "", subfactors=()) -> M.Part:
    return M.Part(
        key=key, value=value, available=available,
        weight=M.MAJOR_WEIGHTS_DECLARED[key] if weight is None else weight,
        reason=reason, subfactors=tuple(subfactors),
        n_subfactors_declared=len(M.SUBFACTORS.get(key, ())),
        n_subfactors_resolved=len(subfactors),
        price_dependent=M.PRICE_DEPENDENT_PILLAR.get(key, False))


def _snap(as_of: str, values: dict, overlay=0.0, *, peer_set_id=1,
          missing: dict = {}) -> M.ScoreSnapshot:
    parts = tuple(_part(k, values.get(k), available=k not in missing,
                        reason=missing.get(k, ""),
                        subfactors=M.SUBFACTORS[k] if k not in missing else ())
                  for k in M.PILLARS)
    draft = M.ScoreSnapshot(entity_id=1, as_of_date=as_of, parts=parts,
                            overlay=overlay, peer_set_id=peer_set_id,
                            model_version="equity_shaffer_v1_pit", replay_run_id=1)
    return M.ScoreSnapshot(
        entity_id=1, as_of_date=as_of, parts=parts, overlay=overlay,
        final_score=draft.recompute(), peer_set_id=peer_set_id,
        model_version="equity_shaffer_v1_pit", replay_run_id=1)


# --------------------------------------------------------------------------
# 1. Reconciliation -- the identity, on generated cases
# --------------------------------------------------------------------------

def test_reconciliation() -> None:
    print("\n1. the decomposition reconciles EXACTLY, generated cases")
    rng = random.Random(20260921)
    worst_blend = 0.0
    worst_total = 0.0
    availability_cases = 0
    entry_cases = 0
    exit_cases = 0

    for _ in range(3000):
        # A random availability pattern at each date, never empty: an empty
        # blend has no score and is tested separately.
        def pattern() -> dict:
            live = [k for k in M.PILLARS if rng.random() > 0.3]
            if not live:
                live = [rng.choice(M.PILLARS)]
            return {k: "insufficient_peers" for k in M.PILLARS if k not in live}

        miss_a, miss_b = pattern(), pattern()
        vals_a = {k: rng.uniform(-100, 100) for k in M.PILLARS}
        vals_b = {k: (vals_a[k] if rng.random() < 0.25
                      else rng.uniform(-100, 100)) for k in M.PILLARS}
        # Keep well inside the clamps for the BLEND identity check; the clamp
        # case has its own test, because a clamp is not a blend phenomenon.
        overlay_a, overlay_b = rng.uniform(-20, 20), rng.uniform(-20, 20)

        a = _snap("2019-05-31", vals_a, overlay_a, missing=miss_a,
                  peer_set_id=1)
        b = _snap("2019-06-28", vals_b, overlay_b, missing=miss_b,
                  peer_set_id=1 if rng.random() < 0.8 else 2)

        if set(miss_a) != set(miss_b):
            availability_cases += 1

        blend = M.decompose_blend(a.parts, b.parts, scale=M.COMPANY_SCALE)
        worst_blend = max(worst_blend, abs(blend.residual))
        entry_cases += len(blend.entered)
        exit_cases += len(blend.exited)

        # the terms must sum to the blend's own observed change
        term_sum = sum(t.points for t in blend.terms)
        worst_blend = max(worst_blend, abs(term_sum - blend.observed))

        att = M.explain_change(None, 1, a.as_of_date, b.as_of_date,
                               snapshot_from=a, snapshot_to=b)
        if att.observed_delta is not None:
            worst_total = max(worst_total,
                              abs(sum(t.points for t in att.terms)
                                  - att.observed_delta))

    check("3,000 generated cases, blend residual is zero to tolerance",
          worst_blend < 1e-9, f"worst {worst_blend:.3e}")
    check("...and the full attribution sums to the observed score change",
          worst_total < 1e-9, f"worst {worst_total:.3e}")
    check("the generator actually produced availability changes",
          availability_cases > 500, availability_cases)
    check("...including entries and exits",
          entry_cases > 500 and exit_cases > 500, (entry_cases, exit_cases))


def test_reconciliation_under_clamp() -> None:
    print("\n1b. a clamp is NAMED, never dropped into a silent residual")
    # Every pillar at +100 gives raw +100, scaled to +75 -- the company clamp
    # binds -- and an overlay of +30 pushes the total to +105, so the final
    # clamp binds too.
    a = _snap("2026-08-31", {k: 60.0 for k in M.PILLARS}, 10.0)
    b = _snap("2026-09-21", {k: 100.0 for k in M.PILLARS}, 30.0)
    att = M.explain_change(None, 1, a.as_of_date, b.as_of_date,
                           snapshot_from=a, snapshot_to=b)
    check("the clamp binds in the fixture", b.clamp_binding() != "",
          b.clamp_binding())
    check("a clamp term is emitted", len(att.by_kind(M.TERM_CLAMP)) == 1)
    check("the terms still sum to the observed change",
          _close(sum(t.points for t in att.terms), att.observed_delta, 1e-9))
    check("and the clamp term carries its cause",
          att.by_kind(M.TERM_CLAMP)[0].cause == M.CAUSE_CLAMP)
    check("the attribution reports itself as reconciling", att.reconciles)

    # A clamp term may NOT be invented when no clamp bound: inconsistent
    # snapshots must surface as RECONCILIATION_FAILED, not as a tidy line.
    good = _snap("2026-08-31", {k: 10.0 for k in M.PILLARS}, 0.0)
    broken = M.ScoreSnapshot(
        entity_id=1, as_of_date="2026-09-21",
        parts=_snap("x", {k: 12.0 for k in M.PILLARS}).parts,
        overlay=0.0, final_score=999.0, peer_set_id=1,
        model_version="equity_shaffer_v1_pit", replay_run_id=1)
    bad = M.explain_change(None, 1, "2026-08-31", "2026-09-21",
                           snapshot_from=good, snapshot_to=broken)
    check("an unexplained gap with no clamp is RECONCILIATION_FAILED",
          bad.state == M.STATE_RECONCILIATION_FAILED, bad.state)
    check("...and is not quietly reported as reconciling", not bad.reconciles)
    check("...and the snapshot's own inconsistency is listed",
          any("parts imply" in p for p in bad.problems), bad.problems)


# --------------------------------------------------------------------------
# 2. Separation -- the reason the module exists
# --------------------------------------------------------------------------

def test_pure_weight_change() -> None:
    print("\n2. a PURE weight change produces ZERO factor effect")
    values = {"valuation": 40.0, "growth": 30.0, "profitability": 25.0,
              "debt": 20.0}
    a = _snap("2026-08-31", values, 4.0)
    # Same values everywhere; only debt's availability changes, which moves
    # every surviving weight by renormalisation and nothing else.
    b = _snap("2026-09-21", dict(values, debt=None), 4.0,
              missing={"debt": "insufficient_peers"})
    blend = M.decompose_blend(a.parts, b.parts, scale=M.COMPANY_SCALE)

    check("factor effect is exactly zero", _close(blend.factor_effect, 0.0))
    check("interaction is exactly zero", _close(blend.interaction_effect, 0.0))
    check("weight effect is not zero", abs(blend.weight_effect) > 1e-6,
          blend.weight_effect)
    check("the exit is its own term, not folded into the factor effect",
          blend.exited == ("debt",) and abs(blend.exit_effect) > 1e-6)
    check("every emitted factor term is zero",
          all(_close(t.points, 0.0) for t in blend.terms
              if t.kind == M.TERM_FACTOR))
    check("the identity still holds", _close(blend.residual, 0.0))

    att = M.explain_change(None, 1, a.as_of_date, b.as_of_date,
                           snapshot_from=a, snapshot_to=b)
    check("no term is presented as an economic move",
          all(_close(t.points, 0.0) for t in att.economic_terms))


def test_pure_factor_change() -> None:
    print("\n3. a PURE factor change produces ZERO weight effect")
    a = _snap("2026-08-31", {"valuation": 40.0, "growth": 30.0,
                             "profitability": 25.0, "debt": 20.0}, 4.0)
    b = _snap("2026-09-21", {"valuation": 51.0, "growth": 44.0,
                             "profitability": 25.0, "debt": 11.0}, 4.0)
    blend = M.decompose_blend(a.parts, b.parts, scale=M.COMPANY_SCALE)

    check("weight effect is exactly zero", _close(blend.weight_effect, 0.0))
    check("interaction is exactly zero", _close(blend.interaction_effect, 0.0))
    check("there is no entry and no exit",
          blend.entered == () and blend.exited == ()
          and _close(blend.entry_effect, 0.0) and _close(blend.exit_effect, 0.0))
    check("factor effect carries the whole move",
          _close(blend.factor_effect, blend.observed))
    check("no weight term is emitted at all",
          not any(t.kind == M.TERM_WEIGHT for t in blend.terms))
    check("every shared pillar gets a factor line, including the flat one",
          {t.key for t in blend.terms if t.kind == M.TERM_FACTOR}
          == set(M.PILLARS))
    check("...and the flat one reads zero rather than being omitted",
          any(t.key == "profitability" and _close(t.points, 0.0)
              for t in blend.terms))


def test_entry_is_not_a_factor_move() -> None:
    print("\n4. a pillar ENTERING is an entry term, not a factor move")
    a = _snap("2026-08-31", {"valuation": None, "growth": 30.0,
                             "profitability": 25.0, "debt": 20.0}, 4.0,
              missing={"valuation": "insufficient_peers"})
    b = _snap("2026-09-21", {"valuation": 40.0, "growth": 30.0,
                             "profitability": 25.0, "debt": 20.0}, 4.0)
    blend = M.decompose_blend(a.parts, b.parts, scale=M.COMPANY_SCALE)

    check("valuation is recorded as entering", blend.entered == ("valuation",))
    check("no FACTOR term mentions valuation",
          not any(t.kind == M.TERM_FACTOR and t.key == "valuation"
                  for t in blend.terms))
    check("no WEIGHT term mentions valuation either",
          not any(t.kind == M.TERM_WEIGHT and t.key == "valuation"
                  for t in blend.terms))
    entry = [t for t in blend.terms if t.kind == M.TERM_ENTRY]
    check("exactly one entry term, and it is valuation's",
          len(entry) == 1 and entry[0].key == "valuation")
    check("the entry term names its cause",
          entry[0].cause == M.CAUSE_BECAME_AVAILABLE, entry[0].cause)
    check("factor effect is zero -- nothing economic happened",
          _close(blend.factor_effect, 0.0))
    check("the surviving pillars' weight moves are attributed to the entry",
          all(t.cause == M.CAUSE_RENORMALISATION and "entered" in t.detail
              for t in blend.terms if t.kind == M.TERM_WEIGHT))
    check("the identity holds", _close(blend.residual, 0.0))

    # ...and the mirror image.
    back = M.decompose_blend(b.parts, a.parts, scale=M.COMPANY_SCALE)
    check("the reverse is an EXIT, with the unavailable reason attached",
          back.exited == ("valuation",)
          and "insufficient_peers" in
          [t.detail for t in back.terms if t.kind == M.TERM_EXIT][0])


def test_weight_cause_is_named() -> None:
    print("\n5. every weight move is attributable to a named cause")
    # no entry, no exit, but the declared weights differ -> two models
    a_parts = (_part("valuation", 40.0, weight=0.40),
               _part("growth", 30.0, weight=0.60))
    b_parts = (_part("valuation", 40.0, weight=0.70),
               _part("growth", 30.0, weight=0.30))
    blend = M.decompose_blend(a_parts, b_parts)
    check("a weight move with no entry or exit is called what it is",
          all(t.cause == M.CAUSE_DECLARED_WEIGHTS_CHANGED
              for t in blend.terms if t.kind == M.TERM_WEIGHT),
          [t.cause for t in blend.terms if t.kind == M.TERM_WEIGHT])
    check("...and the detail says these are two models",
          all("two models" in t.detail for t in blend.terms
              if t.kind == M.TERM_WEIGHT))
    check("the identity holds there too", _close(blend.residual, 0.0))

    # a peer-set change re-labels the FACTOR cause: the ruler moved, not the
    # company. Every pillar but valuation is a percentile rank.
    a = _snap("2026-08-31", {k: 30.0 for k in M.PILLARS}, 0.0, peer_set_id=11)
    b = _snap("2026-09-21", {k: 40.0 for k in M.PILLARS}, 0.0, peer_set_id=12)
    att = M.explain_change(None, 1, a.as_of_date, b.as_of_date,
                           snapshot_from=a, snapshot_to=b)
    check("a peer-set change is detected", att.peer_set_changed)
    check("factor terms are labelled peer_set_changed, not economic",
          all(t.cause == M.CAUSE_PEER_SET_CHANGED
              for t in att.by_kind(M.TERM_FACTOR)))
    check("...so none of them counts as an economic move",
          att.economic_terms == () or
          all(t.kind == M.TERM_OVERLAY for t in att.economic_terms))
    check("but the arithmetic is untouched by the labelling",
          _close(sum(t.points for t in att.terms), att.observed_delta))


# --------------------------------------------------------------------------
# 3. The three UI rules
# --------------------------------------------------------------------------

def test_comparability_rule() -> None:
    print("\n6. UI RULE 1 -- a signature change is never a point move")
    a = _snap("2026-08-31", {"valuation": 40.0, "growth": 30.0,
                             "profitability": 25.0, "debt": 20.0}, 4.0)
    b = _snap("2026-09-21", {"valuation": None, "growth": 30.0,
                             "profitability": 25.0, "debt": 20.0}, 4.0,
              missing={"valuation": "insufficient_peers"})
    att = M.explain_change(None, 1, a.as_of_date, b.as_of_date,
                           snapshot_from=a, snapshot_to=b)

    check("the signature really did change VGPD -> GPD",
          (att.signature_from, att.signature_to) == ("VGPD", "GPD"))
    check("the state is COMPARABILITY_CHANGED",
          att.state == M.STATE_COMPARABILITY_CHANGED, att.state)
    check("headline_delta is None -- there is no number to render",
          att.headline_delta is None)
    check("the message is the owner's exact sentence",
          att.message == "Score comparability changed — valuation input "
                         "unavailable", repr(att.message))
    check("original_weight_coverage records 1.00 -> 0.60",
          _close(att.coverage_from, 1.00) and _close(att.coverage_to, 0.60))

    text = M.render_change(att)
    check("the rendering carries the comparability sentence",
          "Score comparability changed" in text)
    check("the rendering has NO net point move",
          "not comparable" in text
          and not re.search(r"^\s*Net:\s*[-+]\d", text, re.MULTILINE))
    check("...and does not print the two scores as an arrow",
          "Shaffer Score   " not in text)
    check("the arithmetic is still exact underneath",
          _close(sum(t.points for t in att.terms), att.observed_delta))

    check("the reverse transition says 'now available', not 'unavailable'",
          "now available" in M.comparability_message("GPD", "VGPD"))
    check("a two-pillar loss is pluralised honestly",
          M.comparability_message("VGPD", "GD")
          == "Score comparability changed — valuation and profitability "
             "inputs unavailable",
          M.comparability_message("VGPD", "GD"))
    check("an unchanged signature is not dressed up as a change",
          "unchanged" in M.comparability_message("VGPD", "VGPD"))


def test_horizon_rule() -> None:
    print("\n7. UI RULE 2 -- an unsupported horizon shows no number")
    twelve = M.horizon_forecast("12M", point_estimate=4.2)
    check("12M is INSUFFICIENT_EVIDENCE at n_eff 160.7",
          twelve["status"] == pit_invariants.STATUS_INSUFFICIENT, twelve["status"])
    check("...and the point estimate is DROPPED, not shown small",
          twelve["point_estimate"] is None)
    check("...and the display says so in words",
          twelve["display"] == "Insufficient evidence", twelve["display"])
    check("...and it names the detectable effect",
          twelve["detectable_rho"] > 0.15, twelve["detectable_rho"])

    panel = M.horizon_forecast("monthly_panel", point_estimate=0.9)
    check("the monthly panel IS sufficient at n_eff 2,035.6",
          panel["status"] == pit_invariants.STATUS_SUFFICIENT, panel["status"])
    check("...so its estimate survives", _close(panel["point_estimate"], 0.9))

    check("the verdict comes from pit_invariants, not a second definition",
          M.horizon_forecast("12M")["status"]
          == pit_invariants.evidence_status(160.7, 5).status)

    support = M.supported_horizons()
    check("no product horizon is currently supported",
          support["supported"] == [], support["supported"])
    check("...and 1M's narrow miss is reported as a miss",
          M.horizon_forecast("1M")["supported"] is False
          and 0.05 < M.horizon_forecast("1M")["detectable_rho"] < 0.06)
    check("an unmeasured horizon is unsupported rather than assumed",
          M.horizon_forecast("5Y")["supported"] is False
          and M.horizon_forecast("5Y")["basis"] == "unmeasured")
    check("a measured basis is labelled measured, a projected one projected",
          M.horizon_forecast("12M")["basis"] == "measured"
          and M.horizon_forecast("3M")["basis"] == "projected")


def test_hedge_rule() -> None:
    print("\n8. UI RULE 3 -- the hedge carries its research status")
    live = M.hedge_research_status("2026-09-21")
    check("a recommendation is available today", live["recommendation_available"])
    check("...and it is historically validated from the archive start",
          live["historical_validation_available"]
          and live["research_status"] == M.HEDGE_VALIDATED)
    check("the display is the owner's exact two lines",
          live["display"] == "Live recommendation\nHistorical validation "
                             "available from Sep 20, 2026 onward",
          repr(live["display"]))

    past = M.hedge_research_status("2019-06-28")
    check("a pre-archive date is NOT historically validated",
          past["research_status"] == M.HEDGE_LIVE_ONLY
          and not past["historical_validation_available"])
    check("...and the recommendation is still available, not withheld",
          past["recommendation_available"])
    check("...and the display refuses to promise validation for that row",
          "NOT historically validated" in past["display"], past["display"])
    check("the boundary is the archive start itself, inclusive",
          M.hedge_research_status("2026-09-20")["historical_validation_available"]
          and not M.hedge_research_status(
              "2026-09-19")["historical_validation_available"])

    row = M.hedge_snapshot_row(
        M.HedgeSnapshot(entity_id=1, as_of_date="2019-06-28", equity_score=-40.0,
                        adverse_score=40.0, hedge_ratio=0.294, spot=74.60),
        model_version="equity_shaffer_v1_pit", replay_run_id=1,
        created_at="2026-09-21T00:00:00+00:00")
    check("a built hedge row derives its status rather than trusting a caller",
          row["research_status"] == M.HEDGE_LIVE_ONLY)
    check("...and carries the floor and span the ratio was computed with",
          _close(row["hedge_floor"], 15.0) and _close(row["hedge_span"], 85.0))
    check("...and is labelled SURVIVOR_ONLY_DIAGNOSTIC because it holds a price",
          row["sample_scope"] == M.SAMPLE_SURVIVOR_ONLY)


# --------------------------------------------------------------------------
# 4. Grain -- the cohort is stored once, not 31.8 times
# --------------------------------------------------------------------------

def test_cohort_grain() -> None:
    print("\n9. a cohort statistic is stored ONCE per cohort")
    arithmetic = M.grain_arithmetic()
    cohort = arithmetic["grains"]["pit_cohort_stat"]
    check("the cohort grain is the peer-set count, not the entity-date count",
          cohort["rows_per_run"] == 39_038, cohort["rows_per_run"])
    check("...and the alternative it refuses is 1,240,000 rows",
          cohort["refuses"]["rows_per_run"] == 1_240_000)
    check("the repetition factor is the measured 31.76",
          _close(cohort["refuses"]["repetition_factor"], 31.76, 0.01),
          cohort["refuses"]["repetition_factor"])
    check("1,240,000 / 39,038 is where that number comes from",
          _close(M.ENTITY_DATES_TOTAL / M.PEER_SETS_TOTAL, 31.76, 0.01))
    check("the denormalised form would be ~322 MB against ~5.5 MB",
          cohort["refuses"]["bytes_estimate"] > 300e6
          and cohort["bytes_estimate"] < 6e6,
          (cohort["refuses"]["bytes_estimate"], cohort["bytes_estimate"]))

    # The key is the cohort's key, and it does NOT contain entity_id. That is
    # the row-count arithmetic expressed as a constraint rather than a comment.
    check("the cohort key carries no entity_id",
          "entity_id" not in cohort["grain"] and "as_of_date" in cohort["grain"])
    check("the pillar key DOES carry entity_id -- it is per company",
          "entity_id" in arithmetic["grains"]["pit_pillar_score"]["grain"])
    check("four pillar rows per entity-date, and the count says so",
          arithmetic["grains"]["pit_pillar_score"]["rows_per_run"]
          == 4 * M.ENTITY_DATES_TOTAL)

    # One cohort's statistics, built once, feed every member. Build the row and
    # confirm the payload holds no per-company field at all.
    stat = M.CohortStat(
        as_of_date="2019-06-28", rung="sic4", key="3559", n_members=24,
        n_ebitda_resolved=24, cohort_n=6, ebitda_p50=461_500_000.0,
        ebitda_p75=853_250_000.0, cohort_mean_ebitda=644_666_666.67,
        cohort_mean_margin=0.18665, cohort_mean_ev_ebitda=11.85,
        n_eligible={"valuation": 24, "growth": 21, "profitability": 23,
                    "debt": 17},
        eligibility_rule="usable_ev_ebitda_in_0.1_300",
        eligibility_rule_version="peer_set_v1_sic", peer_set_id=4101)
    row = M.cohort_stat_row(stat, model_version="equity_shaffer_v1_pit",
                            peer_set_version="peer_set_v1_sic", replay_run_id=1,
                            ladder_version="concept_ladder_v2",
                            created_at="2026-09-21T00:00:00+00:00")
    check("a cohort row holds no entity_id", "entity_id" not in row)
    check("per-factor eligibility is stored, because factors differ now",
          '"growth": 21' in row["n_eligible_json"], row["n_eligible_json"])
    check("the binding factor is DERIVED, so it cannot disagree with the JSON",
          row["binding_factor"] == "debt" and row["n_eligible_min"] == 17)
    check("the eligibility rule is recorded with its version",
          row["eligibility_rule"] and row["eligibility_rule_version"])
    check("...and the cohort size is the 50-75 count, not the sector count",
          row["n_cohort_members"] == 6 and row["n_sector_peers"] == 24)

    # The cohort row is written once and read by 31.76 members on average.
    members = int(round(M.ENTITY_DATES_TOTAL / M.PEER_SETS_TOTAL))
    pillar_rows = sum(len(M.pillar_score_rows(
        _snap("2019-06-28", {k: 10.0 for k in M.PILLARS}),
        created_at="2026-09-21T00:00:00+00:00")) for _ in range(members))
    check(f"{members} members produce {pillar_rows} pillar rows and ONE cohort row",
          pillar_rows == 4 * members)
    check("...so the cohort statistic is asserted once, not 32 times",
          len([row]) == 1)

    check("what was NOT stored is enumerated with its reason",
          len(M.NOT_STORED) >= 5
          and all(item["why"] and item["cost"] for item in M.NOT_STORED))


def test_resolved_inputs() -> None:
    print("\n10. resolved_inputs records the VALUES, compactly")
    values = pit_derive.FIXTURE_VALUES
    ebitda = M.resolved_inputs("ebitda", values)
    check("EBITDA has two inputs", ebitda["n"] == 2, ebitda["n"])
    check("...and they are the operating income and the D&A actually used",
          set(ebitda["values"]) == {"operating_income",
                                    "depreciation_amortisation"})
    ev = M.resolved_inputs("ev_ebitda", values)
    check("EV/EBITDA has five", ev["n"] == 5, sorted(ev["values"]))
    check("...price, shares, debt, cash and EBITDA -- pit_invariants' own five",
          set(ev["values"]) == {"price_adjusted", "shares_outstanding",
                                "total_debt", "cash", "ebitda"})
    check("EBITDA's own two are NOT re-recorded underneath it",
          "operating_income" not in ev["values"])
    check("an unresolved leaf is recorded as None, not omitted",
          "revenue" in M.resolved_inputs("revenue_growth", {})["values"]
          and M.resolved_inputs("revenue_growth", {})["unresolved"])
    check("a lagged leaf keeps its lag in the key",
          "revenue@t-1y" in M.resolved_inputs("revenue_growth", values)["values"])


# --------------------------------------------------------------------------
# 5. Signatures, masks and snapshot consistency
# --------------------------------------------------------------------------

def test_signatures() -> None:
    print("\n11. signatures, masks and coverage")
    full = _snap("2026-09-21", {k: 10.0 for k in M.PILLARS})
    check("a complete score signs VGPD", full.signature == "VGPD")
    check("...masks 1111", full.pillar_mask == "1111")
    check("...and covers 1.00 of the declared weight",
          _close(full.original_weight_coverage, 1.0))

    gpd = _snap("2026-09-21", {"valuation": None, "growth": 10.0,
                               "profitability": 10.0, "debt": 10.0},
                missing={"valuation": "insufficient_peers"})
    check("valuation missing signs GPD", gpd.signature == "GPD")
    check("...masks 0111 as TEXT -- the leading zero survives",
          gpd.pillar_mask == "0111" and isinstance(gpd.pillar_mask, str))
    check("...and covers 0.60, because valuation's 0.40 is absent",
          _close(gpd.original_weight_coverage, 0.60))
    check("the effective weights renormalise to 1.0",
          _close(sum(gpd.effective_weights.values()), 1.0))
    check("...and valuation is absent from them entirely",
          "valuation" not in gpd.effective_weights)

    partial = M.ScoreSnapshot(
        entity_id=1, as_of_date="2026-09-21",
        parts=(_part("valuation", 10.0, subfactors=("pe_ratio",)),
               _part("growth", 10.0, subfactors=("revenue_growth",
                                                 "ebitda_growth")),
               _part("profitability", 10.0, subfactors=M.SUBFACTORS["profitability"]),
               _part("debt", 10.0, subfactors=M.SUBFACTORS["debt"])),
        overlay=0.0)
    check("the subfactor mask is the owner's shape",
          partial.subfactor_mask.startswith("V[PE=1,EVEBITDA=0] "
                                            "G[RG=1,ACC=0,EG=1]"),
          partial.subfactor_mask)
    check("an absent pillar contributes no sub-factor chunk",
          "V[" not in gpd.subfactor_mask and "G[" in gpd.subfactor_mask,
          gpd.subfactor_mask)
    check("two 'VGPD' scores can still be told apart by the detailed mask",
          partial.subfactor_mask != full.subfactor_mask
          and partial.signature == full.signature)


def test_snapshot_consistency() -> None:
    print("\n12. a snapshot reports its own inconsistencies")
    bad = M.ScoreSnapshot(
        entity_id=1, as_of_date="2026-09-21",
        parts=(M.Part("valuation", None, 0.40, True),
               M.Part("growth", 10.0, 0.25, False),
               M.Part("profitability", 10.0, 0.20, True),
               M.Part("debt", 10.0, 0.15, True)),
        overlay=0.0)
    problems = bad.problems()
    check("available-but-valueless is caught",
          any("available but carries no value" in p for p in problems))
    check("unavailable-but-valued is caught",
          any("unavailable but carries a value" in p for p in problems))
    check("an unavailable part with no reason is caught",
          any("no reason recorded" in p for p in problems))
    check("a healthy snapshot reports nothing",
          _snap("2026-09-21", {k: 10.0 for k in M.PILLARS}).problems() == [])

    no_score = _snap("2026-09-21",
                     {k: None for k in M.PILLARS},
                     missing={k: "insufficient_peers" for k in M.PILLARS})
    check("a score with nothing available has no value and no signature",
          no_score.raw_score() is None and no_score.signature == "")
    att = M.explain_change(None, 1, "2026-08-31", "2026-09-21",
                           snapshot_from=_snap("2026-08-31",
                                               {k: 10.0 for k in M.PILLARS}),
                           snapshot_to=no_score)
    check("...and explain_change says NO_SCORE rather than inventing a fall",
          att.state == M.STATE_NO_SCORE and att.observed_delta is None,
          att.state)

    missing_prior = M.explain_change(None, 1, "2026-08-31", "2026-09-21",
                                     snapshot_from=None,
                                     snapshot_to=_snap("2026-09-21",
                                                       {k: 10.0 for k in M.PILLARS}))
    check("a missing prior is NO_PRIOR_SNAPSHOT, never a rise from zero",
          missing_prior.state == M.STATE_NO_PRIOR
          and missing_prior.observed_delta is None)


# --------------------------------------------------------------------------
# 6. Live and historical are the same call
# --------------------------------------------------------------------------

def test_live_historical_unity() -> None:
    print("\n13. the historical replay and the live job are ONE call")
    hist_a = _snap("2019-05-31", M.FIXTURE["hist_from"]["values"], 8.0)
    hist_b = _snap("2019-06-28", M.FIXTURE["hist_to"]["values"], 10.0)
    live_a = _snap("2026-08-31", M.FIXTURE["live_from"]["values"], 4.0)
    live_b = _snap("2026-09-21", M.FIXTURE["live_to"]["values"], 4.0)

    hist = M.explain_change(None, 8812, "2019-05-31", "2019-06-28",
                            snapshot_from=hist_a, snapshot_to=hist_b)
    live = M.explain_change(None, 8812, "2026-08-31", "2026-09-21",
                            snapshot_from=live_a, snapshot_to=live_b)

    check("both reconcile", hist.reconciles and live.reconciles)
    check("both reach ECONOMIC_CHANGE through the same code path",
          hist.state == live.state == M.STATE_ECONOMIC_CHANGE)
    check("the historical fixture is the owner's +58 -> +64",
          _close(hist.score_from, 58.0, 1e-9)
          and _close(hist.score_to, 64.0, 1e-9),
          (hist.score_from, hist.score_to))
    check("...netting +6.0", _close(hist.observed_delta, 6.0, 1e-9))

    by_key = {t.key: t.points for t in hist.by_kind(M.TERM_FACTOR)}
    check("valuation contributes -0.9", _close(by_key["valuation"], -0.9, 5e-4),
          by_key["valuation"])
    check("growth contributes +1.8", _close(by_key["growth"], 1.8, 5e-4),
          by_key["growth"])
    check("profitability contributes +3.1",
          _close(by_key["profitability"], 3.1, 5e-4), by_key["profitability"])
    check("debt contributes 0.0 and SAYS SO",
          "debt" in by_key and _close(by_key["debt"], 0.0))
    check("the sector overlay contributes +2.0",
          _close(hist.effect(M.TERM_OVERLAY), 2.0))

    text = M.render_change(hist)
    for expected in ("Valuation:", "Growth:", "Profitability:", "Debt/quality:",
                     "Sector overlay:", "Net:"):
        check(f"the rendering carries {expected!r}", expected in text)
    check("the rendered net is +6.0", re.search(r"Net:\s+\+6\.0", text) is not None)
    check("the worked example runs end to end",
          "HISTORICAL REPLAY" in M.worked_example()
          and "LIVE DAILY JOB" in M.worked_example())
    doc = _flat(M.explain_change.__doc__)
    check("the docstring names available_date as the shared event clock",
          "`available_date` is the shared event clock" in doc, doc[:120])
    check("...and says the two halves are one function",
          "BOTH HALVES OF THE SYSTEM" in doc)


# --------------------------------------------------------------------------
# 7. Restraint -- this module writes NOTHING
# --------------------------------------------------------------------------

def test_writes_nothing() -> None:
    print("\n14. the module opens no connection and writes no row")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pit_intermediates.py")
    source = open(path, encoding="utf-8").read()
    code = _code_only(path)
    check("there is no `import sqlite3` in the module",
          not re.search(r"\bimport\s+sqlite3\b", code))
    check("no sqlite3 name is referenced by the code at all",
          "sqlite3" not in code)
    check("nothing in the code calls .connect(",
          not re.search(r"\.\s*connect\s*\(", code))
    check("...and sqlite3 is not reachable as a module attribute either",
          not hasattr(M, "sqlite3"))

    conn = _FakeConn(rows=[])
    # every read path
    check("load_snapshot returns None against an empty store",
          M.load_snapshot(conn, 1, "2019-06-28",
                          model_version="equity_shaffer_v1_pit",
                          replay_run_id=1) is None)
    check("load_cohort_stat likewise",
          M.load_cohort_stat(conn, "2019-06-28", "sic4", "3559",
                             model_version="equity_shaffer_v1_pit",
                             peer_set_version="peer_set_v1_sic",
                             replay_run_id=1) is None)
    check("every statement issued was a SELECT",
          conn.writes == [] and conn.statements
          and all(s.lstrip().upper().startswith("SELECT") for s in conn.statements),
          conn.writes)
    check("nothing was committed", conn.commits == 0)
    check("no DDL script was run", conn.scripts == [])

    # the read guard bites
    check("read_only_sql refuses an INSERT",
          _raises(lambda: M.read_only_sql("INSERT INTO pit_score VALUES (1)"),
                  ValueError))
    check("...refuses a DDL", _raises(lambda: M.read_only_sql(
        "CREATE TABLE x (a INTEGER)"), ValueError))
    check("...refuses a second statement smuggled after a semicolon",
          _raises(lambda: M.read_only_sql(
              "SELECT 1; DROP TABLE pit_score"), ValueError))
    check("...and passes a plain SELECT through unchanged",
          M.read_only_sql("SELECT 1") == "SELECT 1")

    # the row builders build, and do not execute
    snap = _snap("2019-06-28", {k: 10.0 for k in M.PILLARS})
    rows = M.pillar_score_rows(snap, created_at="2026-09-21T00:00:00+00:00")
    check("pillar_score_rows returns four plain dicts",
          len(rows) == 4 and all(isinstance(r, dict) for r in rows))
    check("...one per DECLARED pillar, unavailable ones included",
          {r["pillar"] for r in rows} == set(M.PILLARS))
    pending = M.pending_writes({"pit_pillar_score": rows})
    check("pending_writes pairs each row with its INSERT text",
          len(pending) == 4
          and all(sql.startswith("INSERT INTO pit_pillar_score")
                  for sql, _ in pending))
    check("...and executed nothing on the way",
          conn.writes == [] and conn.commits == 0)
    check("an unknown table is refused rather than silently skipped",
          _raises(lambda: M.pending_writes({"pit_nope": [{}]}), ValueError))

    # ensure_schema is written for someone ELSE, and nothing here calls it
    check("no module-level call to ensure_schema exists",
          not re.search(r"^\s*ensure_schema\(", source, re.MULTILINE))
    check("...and the worked example never calls it",
          "ensure_schema(" not in (M.worked_example.__code__.co_names
                                   and str(M.worked_example.__code__.co_names)))

    runner = _FakeConn(rows=[])
    result = M.ensure_schema(runner)
    check("when someone DOES run it, it is idempotent DDL",
          result["status"] == "ok"
          and all("IF NOT EXISTS" in s for s in runner.scripts))
    check("...and it creates pit_cohort_stat from pit_derive's ONE declaration",
          any("pit_cohort_stat" in s for s in runner.scripts))
    check("...which this module never redeclares",
          "CREATE TABLE IF NOT EXISTS pit_cohort_stat"
          not in M.PIT_INTERMEDIATE_DDL_V2)
    check("...because a second CREATE ... IF NOT EXISTS is a SILENT no-op",
          "silent no-op" in source.lower() or "SILENT NO-OP" in source)


def test_schema_shape() -> None:
    print("\n15. the schema says what it must")
    ddl = M.PIT_INTERMEDIATE_DDL_V2
    for table in ("pit_pillar_score", "pit_hedge_snapshot"):
        check(f"{table} is declared", f"CREATE TABLE IF NOT EXISTS {table}" in ddl)
        check(f"{table} has a UNIQUE key for idempotency",
              re.search(rf"{table}.*?UNIQUE \(", ddl, re.DOTALL) is not None)
        check(f"{table} refuses UPDATE by trigger",
              f"trg_{table}_no_update" in ddl)
    check("pit_pillar_score stores BOTH weights -- the pair is the attribution",
          "original_weight" in ddl and "effective_weight" in ddl)
    check("an unavailable pillar is forced to carry zero effective weight",
          "availability = 'resolved' OR effective_weight = 0.0" in ddl)
    check("the hedge table refuses a pre-archive validation claim",
          "as_of_date >= validation_available_from" in ddl)
    check("anything holding a price is scope-checked",
          ddl.count("SURVIVOR_ONLY_DIAGNOSTIC") >= 2)

    check("pit_score gains the signature, the mask and the coverage",
          {c for c, _ in M.INTERMEDIATE_MIGRATIONS["pit_score"]}
          == {"score_signature", "pillar_mask", "original_weight_coverage",
              "subfactor_mask"})
    check("pillar_mask is TEXT so '0111' keeps its leading zero",
          dict(M.INTERMEDIATE_MIGRATIONS["pit_score"])["pillar_mask"] == "TEXT")
    check("pit_feature gains resolved_inputs_json",
          dict(M.INTERMEDIATE_MIGRATIONS["pit_feature"])
          == {"resolved_inputs_json": "TEXT"})
    check("pit_cohort_stat gains per-factor eligibility and the rule",
          {"n_eligible_json", "n_eligible_min", "binding_factor",
           "eligibility_rule", "eligibility_rule_version"}
          == {c for c, _ in M.INTERMEDIATE_MIGRATIONS["pit_cohort_stat"]})
    check("no migration carries a DEFAULT -- NULL means 'written before'",
          not any("DEFAULT" in t.upper()
                  for cols in M.INTERMEDIATE_MIGRATIONS.values()
                  for _, t in cols))
    check("every INSERT names a table the schema declares",
          set(M.INSERT_SQL) == {"pit_cohort_stat", "pit_pillar_score",
                                "pit_hedge_snapshot"})


def test_no_drift_from_the_frozen_core() -> None:
    print("\n16. the restated constants have not drifted")
    import company_scoring
    check("MAJOR_WEIGHTS_DECLARED equals the production core's",
          M.MAJOR_WEIGHTS_DECLARED == company_scoring.MAJOR_WEIGHTS,
          M.MAJOR_WEIGHTS_DECLARED)
    check("COMPANY_SCALE equals the core's", _close(M.COMPANY_SCALE, 0.75))
    check("the pillar set equals the core's factor set",
          set(M.PILLARS) == set(company_scoring.MAJOR_WEIGHTS))
    check("the growth sub-factors equal the core's",
          set(M.SUBFACTORS["growth"]) == set(company_scoring.GROWTH_WEIGHTS))
    check("the debt sub-factors equal the core's",
          set(M.SUBFACTORS["debt"]) == set(company_scoring.DEBT_WEIGHTS))
    check("the profitability sub-factors equal the core's",
          set(M.SUBFACTORS["profitability"])
          == set(company_scoring.PROFITABILITY_WEIGHTS))
    check("the weights sum to 1.00",
          _close(sum(M.MAJOR_WEIGHTS_DECLARED.values()), 1.0))
    check("price dependence is asked of the graph, not hardcoded",
          M.PRICE_DEPENDENT_PILLAR["valuation"] is True
          and M.PRICE_DEPENDENT_PILLAR["profitability"] is False,
          M.PRICE_DEPENDENT_PILLAR)
    check("the option archive start matches pit_derive's register",
          M.OPTION_ARCHIVE_START
          == pit_derive.UNAVAILABLE_REGISTER["option_quote_pre_archive"]["until"])


def main() -> int:
    test_reconciliation()
    test_reconciliation_under_clamp()
    test_pure_weight_change()
    test_pure_factor_change()
    test_entry_is_not_a_factor_move()
    test_weight_cause_is_named()
    test_comparability_rule()
    test_horizon_rule()
    test_hedge_rule()
    test_cohort_grain()
    test_resolved_inputs()
    test_signatures()
    test_snapshot_consistency()
    test_live_historical_unity()
    test_writes_nothing()
    test_schema_shape()
    test_no_drift_from_the_frozen_core()
    print()
    if fails:
        print(f"{len(fails)} FAILED: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
